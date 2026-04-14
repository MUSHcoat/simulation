#!/usr/bin/env python3
"""
Generate turn_analysis_<year>.md for each run and year in data/logs/.

Reads full_run_*.json and year_*.json; outputs turn_analysis_XXXX.md
in the same run directory, styled after example_turn.md but populated
with actual LLM responses, jury decisions, and engine outputs.

Usage:
    cd sim
    python tools/generate_turn_analysis.py
"""

import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS_DIR = os.path.join(BASE_DIR, "data", "logs")

NATIONAL_CAP_FRACS = {"United States": 0.50, "China": 0.60}


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_json(path):
    with open(path) as f:
        return json.load(f)


def load_starting_values():
    return load_json(os.path.join(BASE_DIR, "config", "starting_values.json"))


def load_actor_configs():
    actor_dir = os.path.join(BASE_DIR, "config", "actors")
    configs = {}
    for fname in sorted(os.listdir(actor_dir)):
        if fname.endswith(".json") and fname != "actor_template.json":
            cfg = load_json(os.path.join(actor_dir, fname))
            configs[cfg["name"]] = cfg
    return configs


def load_state_configs():
    state_dir = os.path.join(BASE_DIR, "config", "states")
    configs = {}
    for fname in sorted(os.listdir(state_dir)):
        if fname.endswith(".json"):
            cfg = load_json(os.path.join(state_dir, fname))
            configs[cfg["name"]] = cfg
    return configs


def build_pre_run_state(sv, actor_cfgs, state_cfgs):
    """Build pre-run world state dicts from starting_values.json."""
    macro = {}
    for state_name, sv_data in sv.get("macro", {}).items():
        cfg = state_cfgs.get(state_name, {})
        macro[state_name] = {
            "name": state_name,
            "compute": float(sv_data.get("compute", cfg.get("compute", 50))),
            "capital": float(sv_data.get("capital", cfg.get("capital", 50))),
            "influence": float(sv_data.get("influence", cfg.get("influence", 50))),
            "supply_chain_robustness": float(
                sv_data.get("supply_chain_robustness", cfg.get("supply_chain_robustness", 50))
            ),
            "values": {**cfg.get("values", {}), **sv_data.get("values", {})},
        }
    micro = {}
    for actor_name, sv_data in sv.get("micro", {}).items():
        cfg = actor_cfgs.get(actor_name, {})
        micro[actor_name] = {
            "name": actor_name,
            "parent_state": sv_data.get("parent_state", cfg.get("parent_state", "")),
            "compute": float(sv_data.get("compute", cfg.get("compute", 0))),
            "capital": float(sv_data.get("capital", cfg.get("capital", 50))),
            "influence": float(sv_data.get("influence", cfg.get("influence", 50))),
            "values": {**cfg.get("values", {}), **sv_data.get("values", {})},
        }
    return macro, micro


# ---------------------------------------------------------------------------
# Score helpers
# ---------------------------------------------------------------------------

def formula_score(snap, fw):
    c = float(snap.get("compute", 0))
    k = float(snap.get("capital", 0))
    i = float(snap.get("influence", 0))
    return round(fw["compute"] * c + fw["capital"] * k + fw["influence"] * i, 2)


def overall_score_val(f, a, ow):
    return round(ow["formula"] * f + ow["alignment"] * a, 2)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def format_cot(cot):
    """Return full CoT text, stripping leading JSON fences."""
    if not cot:
        return "*(no chain-of-thought recorded)*"
    cot = cot.strip()
    # Strip leading JSON fence if the CoT was stored as a raw JSON string
    for prefix in ("```json\n", "```\n"):
        if cot.startswith(prefix):
            cot = cot[len(prefix):]
    return cot


def detect_lobby_actions(actor_phase):
    """Return list of (actor_name, target_state) for executed lobby actions."""
    lobbies = []
    for entry in actor_phase:
        for action in entry.get("executed_actions", []):
            if action["action_type"] == "lobby_institution":
                target = action["effects"].get("pending_macro_lobby")
                if target:
                    lobbies.append((entry["actor"], target))
    return lobbies


def build_compute_sequence(actor_phase, pre_micro):
    """
    Replay compute state through each acquire_compute action.
    Returns list of dicts with before/after snapshots for each acquisition.
    """
    compute = {name: float(s["compute"]) for name, s in pre_micro.items()}
    events = []
    for entry in actor_phase:
        for action in entry.get("executed_actions", []):
            if action["action_type"] == "acquire_compute":
                actor = entry["actor"]
                effects = action["effects"]
                amount = effects.get("compute", 0)
                dilution = effects.get("dilution", {})
                before = dict(compute)
                compute[actor] = compute.get(actor, 0) + amount
                for other, delta in dilution.items():
                    compute[other] = compute.get(other, 0) + delta
                events.append({
                    "actor": actor,
                    "amount": amount,
                    "dilution": dilution,
                    "before": before,
                    "after": dict(compute),
                })
    return events


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def sec_world_state(year, pre_macro, pre_micro, actor_phase):
    lines = []
    lines.append(f"## World State at Start of Year {year}")
    lines.append("")
    lines.append("These values are the exact starting state for this turn. "
                 "Baseline scores are captured from this snapshot before Phase 0 fires.")
    lines.append("")

    # Macro table
    lines.append("### Macro States")
    lines.append("")
    lines.append("| State | Compute | Capital | Influence | SCR | "
                 "time\\_horizon | transparency | risk\\_tolerance | democratic |")
    lines.append("|-------|--------:|--------:|----------:|----:|"
                 "-------------:|-------------:|---------------:|-----------:|")
    for state_name in sorted(pre_macro):
        s = pre_macro[state_name]
        v = s.get("values", {})
        lines.append(
            f"| {state_name} | {s['compute']:.1f} | {s['capital']:.1f} | "
            f"{s['influence']:.1f} | {int(s['supply_chain_robustness'])} | "
            f"{v.get('time_horizon','?')} | {v.get('transparency_threshold','?')} | "
            f"{v.get('risk_tolerance','?')} | {v.get('democratic_tendency','?')} |"
        )
    lines.append("")

    # Micro table (in actor_phase order)
    lines.append("### Particular Actors")
    lines.append("")
    lines.append("| Actor | Compute | Capital | Influence | "
                 "time\\_horizon | transparency | risk\\_tolerance | democratic |")
    lines.append("|-------|--------:|--------:|----------:|"
                 "-------------:|-------------:|---------------:|-----------:|")
    actor_order = [e["actor"] for e in actor_phase]
    for actor_name in actor_order:
        a = pre_micro.get(actor_name, {})
        v = a.get("values", {})
        ps = a.get("parent_state", "")
        tag = "US" if "United States" in ps else "CN"
        lines.append(
            f"| {actor_name} \\[{tag}\\] | {a['compute']:.1f} | {a['capital']:.1f} | "
            f"{a['influence']:.1f} | {v.get('time_horizon','?')} | "
            f"{v.get('transparency_threshold','?')} | {v.get('risk_tolerance','?')} | "
            f"{v.get('democratic_tendency','?')} |"
        )
    lines.append("")

    # Global totals and caps
    total = sum(a["compute"] for a in pre_micro.values())
    parts = " + ".join(f"{pre_micro[n]['compute']:.1f}" for n in actor_order if n in pre_micro)
    lines.append(f"**Global micro compute total:** {parts} = **{total:.1f}**")
    lines.append("")
    lines.append("**National compute caps and headroom:**")
    for state_name in sorted(pre_macro):
        cap_frac = NATIONAL_CAP_FRACS.get(state_name)
        if not cap_frac:
            continue
        s = pre_macro[state_name]
        cap = s["compute"] * cap_frac
        nat_total = sum(a["compute"] for a in pre_micro.values() if a.get("parent_state") == state_name)
        tag = "US" if "United States" in state_name else "CN"
        lines.append(
            f"- {tag}: {s['compute']:.0f} × {cap_frac:.2f} = {cap:.1f} cap; "
            f"current total = {nat_total:.1f}; headroom = **{cap - nat_total:.1f}**"
        )
    lines.append("")
    return lines


def sec_phase0(scenario, events):
    lines = []
    lines.append("## Phase 0 — Event Injection")
    lines.append("")
    if not events:
        lines.append(
            f"The `{scenario}` scenario has an empty events list for this turn. "
            "**No events fire.** All actors proceed to Phase 1 with the snapshot above."
        )
    else:
        lines.append(f"{len(events)} event(s) fired this turn:")
        lines.append("")
        for ev in events:
            lines.append(f"- **{ev.get('name', 'Event')}:** {ev.get('description', str(ev))}")
    lines.append("")
    return lines


def sec_phase1(actor_phase, a2a_msgs):
    lines = []
    lines.append("## Phase 1 — Simultaneous Proposals")
    lines.append("")
    lines.append(
        "All actors read the same frozen snapshot and produce chain-of-thought reasoning "
        "plus proposed actions simultaneously. No resources change during this phase."
    )
    lines.append("")

    for entry in actor_phase:
        actor = entry["actor"]
        cot = entry.get("chain_of_thought", "")
        proposed = entry.get("proposed_actions", [])

        lines.append(f"### {actor}")
        lines.append("")
        lines.append("**Chain of thought:**")
        lines.append("")
        lines.append(f"> {format_cot(cot)}")
        lines.append("")

        if not proposed:
            lines.append(
                "**Proposed actions:** *(none — actor produced no valid action list; "
                "this actor produced no valid action list this turn)*"
            )
        else:
            lines.append("**Proposed actions:**")
            for i, act in enumerate(proposed, 1):
                atype = act.get("action_type", "?")
                amount = act.get("amount")
                target = act.get("target") or ""
                rationale = act.get("rationale", "")
                amt_str = f" — amount: {amount}" if amount is not None else ""
                tgt_str = f" → `{target}`" if target else ""
                lines.append(f"{i}. `{atype}`{amt_str}{tgt_str}")
                if rationale:
                    lines.append(f"   *{rationale}*")
        lines.append("")

        msgs_from = [m for m in a2a_msgs if m["sender"] == actor]
        for m in msgs_from:
            tokens = m.get("turn_tokens_used", "?")
            lines.append(
                f"**A2A → {m['recipient']}** *(~{tokens} tokens):* \"{m['content']}\""
            )
            lines.append("")

    return lines


DEFAULT_JURY_MODELS = ["claude-sonnet-4-6", "gpt-4o", "gemini-2.5-flash"]


def sec_phase2(actor_phase, jury_models=None):
    models = jury_models or DEFAULT_JURY_MODELS
    lines = []
    lines.append("## Phase 2 — Jury of Alignment Review")
    lines.append("")
    jury_panel = ", ".join(f"`{m}`" for m in models)
    lines.append(
        f"Jury panel: {jury_panel}. "
        "3-model majority vote determines approval. Rejected actors may revise up to 2 times."
    )
    lines.append("")

    # Summary table
    lines.append("| Actor | Approved | Notes |")
    lines.append("|-------|:--------:|-------|")
    for entry in actor_phase:
        actor = entry["actor"]
        approved = entry.get("jury_approved", True)
        forfeited = entry.get("forfeited", False)
        blocked = entry.get("blocked_actions", [])
        proposed = entry.get("proposed_actions", [])
        check = "✓" if approved else "✗"
        if forfeited and not approved:
            note = "Forfeited — jury rejected (empty or invalid action list after revisions)"
        elif forfeited and approved:
            note = "Forfeited — LLM unavailable, turn skipped"
        elif not proposed:
            note = "No actions proposed — approved vacuously (0 actions is valid)"
        elif blocked:
            note = f"Approved; {len(blocked)} action(s) later blocked at execution time"
        else:
            note = "Approved — all guardrails satisfied"
        lines.append(f"| {actor} | {check} | {note} |")
    lines.append("")

    # Per-actor feedback
    lines.append("### Jury Feedback")
    lines.append("")
    for entry in actor_phase:
        actor = entry["actor"]
        feedback = entry.get("jury_feedback", "")
        approved = entry.get("jury_approved", True)
        status = "**Approved**" if approved else "**Rejected**"
        lines.append(f"**{actor}** — {status}")
        lines.append("")
        if feedback:
            parts = [p.strip() for p in feedback.split("|") if p.strip()]
            for i, part in enumerate(parts, 1):
                model_label = models[i - 1] if i - 1 < len(models) else f"Juror {i}"
                lines.append(f"> **Juror {i} (`{model_label}`):** {part}")
        lines.append("")

    # Execution-time blocks
    blocked_entries = [(e["actor"], e["blocked_actions"]) for e in actor_phase if e.get("blocked_actions")]
    if blocked_entries:
        lines.append("### Execution-Time Blocks")
        lines.append("")
        lines.append(
            "These jury-approved actions were blocked by the execution engine "
            "(resources insufficient after earlier actions depleted them):"
        )
        lines.append("")
        for actor, blocks in blocked_entries:
            for b in blocks:
                act = b.get("action", {})
                err = b.get("error", "")
                lines.append(
                    f"- **{actor}**: `{act.get('action_type','?')}(amount={act.get('amount','?')})` — *{err}*"
                )
        lines.append("")

    return lines


def sec_phase3(actor_phase, pre_micro, pre_macro, a2a_msgs):
    lines = []
    lines.append("## Phase 3 — Batch Execution")
    lines.append("")
    lines.append(
        "Approved proposals execute in sequence against the live world state. "
        "Compute acquisitions are zero-sum. `invest_capital` deductions are immediate; "
        "returns are deferred until all actors have executed."
    )
    lines.append("")

    compute_events = build_compute_sequence(actor_phase, pre_micro)
    compute_event_idx = 0
    invest_pending = {}  # actor → pending gain

    for entry in actor_phase:
        actor = entry["actor"]
        executed = entry.get("executed_actions", [])
        if not executed:
            continue

        lines.append(f"### {actor}")
        lines.append("")

        for action in executed:
            atype = action["action_type"]
            effects = action.get("effects", {})

            # ── acquire_compute ──────────────────────────────────────────────
            if atype == "acquire_compute":
                amount = effects.get("compute", 0)
                cap_cost = abs(effects.get("capital", 0))
                dilution = effects.get("dilution", {})
                ps = next((e["parent_state"] for e in actor_phase if e["actor"] == actor), "")
                scr = pre_macro.get(ps, {}).get("supply_chain_robustness", 50)
                scr_mod = 1.0 + (100.0 - scr) / 100.0

                lines.append(f"**`acquire_compute`** (amount: {amount})")
                lines.append("")
                lines.append(f"{ps} SCR = {int(scr)}. Acquisition cost:")
                lines.append("")
                lines.append("```")
                lines.append(f"cost = 5 × {amount} × (1 + (100 − {int(scr)}) / 100)")
                lines.append(f"     = {5 * amount} × {scr_mod:.2f}")
                lines.append(f"     = {cap_cost:.2f} capital")
                lines.append("```")
                lines.append("")

                if compute_event_idx < len(compute_events) and dilution:
                    ev = compute_events[compute_event_idx]
                    compute_event_idx += 1
                    others = {n: v for n, v in ev["before"].items() if n != actor}
                    others_sum = sum(others.values())

                    lines.append("**Zero-sum dilution** — the global micro compute pool is constant:")
                    lines.append("")
                    lines.append(
                        "Others before dilution: "
                        + ", ".join(f"{n}={v:.4f}" for n, v in others.items())
                        + f" → sum = **{others_sum:.4f}**"
                    )
                    lines.append("")
                    lines.append("| Actor | Pre-dilution | Loss | Post-dilution |")
                    lines.append("|-------|------------:|-----:|-------------:|")
                    for other_name, loss in dilution.items():
                        pre_v = ev["before"].get(other_name, 0)
                        post_v = ev["after"].get(other_name, 0)
                        share = pre_v / others_sum if others_sum else 0
                        lines.append(
                            f"| {other_name} | {pre_v:.4f} | "
                            f"{amount:.1f} × ({pre_v:.4f} / {others_sum:.4f}) = **{abs(loss):.4f}** | "
                            f"**{post_v:.4f}** |"
                        )
                    acq_after = ev["after"].get(actor, 0)
                    lines.append(f"| {actor} | +{amount:.1f} acquired | — | **{acq_after:.4f}** |")
                    new_total = sum(ev["after"].values())
                    lines.append("")
                    total_parts = " + ".join(f"{v:.4f}" for v in ev["after"].values())
                    lines.append(f"Global total: {total_parts} = **{new_total:.2f}** ✓")
                    lines.append("")
                elif compute_event_idx < len(compute_events):
                    compute_event_idx += 1

            # ── invest_capital ───────────────────────────────────────────────
            elif atype == "invest_capital":
                # Effects key may be 'capital' or 'capital_invested' depending on engine version
                amount = abs(effects.get("capital_invested", effects.get("capital", 0)))
                gain = effects.get("capital_gain_pending", 0)
                invest_pending[actor] = invest_pending.get(actor, 0) + gain
                gain_rate = (gain / amount - 1) * 100 if amount else 0

                lines.append(f"**`invest_capital`** (amount: {amount:.0f})")
                lines.append("")
                lines.append(f"Capital deducted immediately: {amount:.2f}. Return deferred:")
                lines.append("")
                lines.append("```")
                lines.append(f"pending gain = {gain:.2f}  ({gain_rate:.1f}% return)")
                lines.append("```")
                lines.append("")

            # ── build_influence ──────────────────────────────────────────────
            elif atype == "build_influence":
                inf_gain = effects.get("influence", 0)
                cap_cost = abs(effects.get("capital", 0))
                lines.append(f"**`build_influence`** (amount: {inf_gain:.0f})")
                lines.append("")
                lines.append(f"Cost: {inf_gain:.0f} × 3 capital/point = {cap_cost:.1f} capital. "
                             f"Influence +{inf_gain:.0f}.")
                lines.append("")

            # ── lobby_institution ────────────────────────────────────────────
            elif atype == "lobby_institution":
                cap_cost = abs(effects.get("capital", 0))
                inf_cost = abs(effects.get("influence", 0))
                target = effects.get("pending_macro_lobby", "?")
                lines.append(f"**`lobby_institution`** (target: {target})")
                lines.append("")
                lines.append(
                    f"Cost: {cap_cost:.1f} capital + {inf_cost} influence (flat). "
                    f"Records `pending_macro_lobby = \"{target}\"` — value nudge applied in Phase 5a."
                )
                lines.append("")

            # ── publish_narrative ────────────────────────────────────────────
            elif atype == "publish_narrative":
                inf_cost = abs(effects.get("influence", 0))
                lines.append(f"**`publish_narrative`**")
                lines.append("")
                lines.append(f"Cost: {inf_cost} influence (flat).")
                lines.append("")

            # ── diminish_competitor ──────────────────────────────────────────
            elif atype == "diminish_competitor":
                cap_cost = abs(effects.get("capital", 0))
                inf_cost = abs(effects.get("influence", 0))
                target = action.get("target", "?")
                lines.append(f"**`diminish_competitor`** (target: {target})")
                lines.append("")
                lines.append(f"Cost: {cap_cost:.1f} capital + {inf_cost:.1f} influence.")
                lines.append("")

    # A2A section
    if a2a_msgs:
        lines.append("### A2A Messages Sent This Turn")
        lines.append("")
        lines.append(
            "Messages are logged and delivered to recipients at the start of the next turn. "
            "They do not affect resources this turn."
        )
        lines.append("")
        for m in a2a_msgs:
            tokens = m.get("turn_tokens_used", "?")
            lines.append(f"- **{m['sender']} → {m['recipient']}** *(~{tokens} tokens):* \"{m['content']}\"")
        lines.append("")

    # Flush
    if invest_pending:
        lines.append("### Flush Deferred `invest_capital` Gains")
        lines.append("")
        lines.append("After all actors have executed, pending capital returns are credited:")
        lines.append("")
        lines.append("| Actor | Capital before flush | Pending gain | Capital after flush |")
        lines.append("|-------|--------------------:|-------------:|--------------------:|")
        for entry in actor_phase:
            actor = entry["actor"]
            if actor not in invest_pending:
                continue
            pending = invest_pending[actor]
            snap_cap = entry["snapshot_after"]["capital"]
            # pre-flush capital = final capital minus the pending gain that was credited
            pre_flush_cap = round(snap_cap - pending, 2)
            lines.append(
                f"| {actor} | {pre_flush_cap:.2f} | +{pending:.2f} | **{snap_cap:.2f}** |"
            )
        no_invest = [e["actor"] for e in actor_phase if e.get("executed_actions") and e["actor"] not in invest_pending]
        if no_invest:
            lines.append("")
            lines.append(f"{', '.join(no_invest)} had no `invest_capital` action this turn; no flush.")
        lines.append("")

    # Post-execution snapshot
    lines.append("### Post-Execution Snapshot")
    lines.append("")
    lines.append("**Particular actors:**")
    lines.append("")
    lines.append("| Actor | Compute | Capital | Influence |")
    lines.append("|-------|--------:|--------:|----------:|")
    total_after = 0.0
    for entry in actor_phase:
        snap = entry["snapshot_after"]
        total_after += snap["compute"]
        lines.append(
            f"| {entry['actor']} | {snap['compute']:.4f} | {snap['capital']:.2f} | {snap['influence']:.1f} |"
        )
    lines.append("")
    compute_parts_str = " + ".join(f"{e['snapshot_after']['compute']:.4f}" for e in actor_phase)
    lines.append(f"Global micro compute: {compute_parts_str} = **{total_after:.2f}** ✓")
    lines.append("")

    lines.append("**Macro states (unchanged by actor actions this turn):**")
    lines.append("")
    lines.append("| State | Compute | Capital | Influence | SCR |")
    lines.append("|-------|--------:|--------:|----------:|----:|")
    for name in sorted(pre_macro):
        s = pre_macro[name]
        lines.append(
            f"| {name} | {s['compute']:.1f} | {s['capital']:.1f} | "
            f"{s['influence']:.1f} | {int(s['supply_chain_robustness'])} |"
        )
    lines.append("")
    lines.append(
        "> Macro resources only change through Phase 0 events. "
        "The MacroJury (Phase 5b) updates macro **value axes** only."
    )
    lines.append("")
    return lines


def sec_phase4(grand_jury, actor_phase):
    lines = []
    lines.append("## Phase 4 — Grand Jury")
    lines.append("")
    lines.append(
        "The 3-model jury panel evaluates the holistic world state after Phase 3 "
        "and produces a Universal Prosperity Score (researcher indicator) and "
        "per-actor Alignment Scores (used in the overall formula)."
    )
    lines.append("")

    ups = grand_jury.get("universal_prosperity_score", 50)
    actor_align = grand_jury.get("actor_alignment", {})
    commentary = grand_jury.get("commentary", "")
    alignment_assessment = grand_jury.get("alignment_assessment", "")
    key_risks = grand_jury.get("key_risks", "")

    lines.append(f"**Universal Prosperity Score: {ups}**  "
                 "*(researcher-facing world indicator — not in any actor's formula)*")
    lines.append("")

    lines.append("### Per-Actor Alignment Scores")
    lines.append("")
    lines.append("| Actor | Alignment Score | vs. baseline (50) |")
    lines.append("|-------|---------------:|:-----------------:|")
    for entry in actor_phase:
        actor = entry["actor"]
        score = actor_align.get(actor, 50)
        delta = score - 50
        sign = "+" if delta >= 0 else ""
        note = f"{sign}{delta:.0f}"
        lines.append(f"| {actor} | **{score}** | {note} |")
    lines.append("")

    if commentary:
        lines.append("**Grand Jury commentary:**")
        lines.append("")
        for part in [p.strip() for p in commentary.split("|") if p.strip()]:
            lines.append(f"> {part}")
            lines.append("")

    if alignment_assessment:
        lines.append("**Alignment trajectory:**")
        lines.append("")
        for part in [p.strip() for p in alignment_assessment.split("|") if p.strip()]:
            lines.append(f"> {part}")
            lines.append("")

    if key_risks:
        lines.append("**Key risks identified:**")
        lines.append("")
        for part in [p.strip() for p in key_risks.split("|") if p.strip()]:
            lines.append(f"> {part}")
            lines.append("")

    lines.append(
        "The Universal Prosperity Score is logged as a researcher-facing indicator. "
        "It is **not** included in any actor's scoring formula — each actor's "
        "`alignment_score` in the formula is their individual score above."
    )
    lines.append("")
    return lines


def sec_phase5a(lobbies, actor_phase, pre_macro, macro_phase):
    lines = []
    lines.append("## Phase 5a — Lobby Pressure")
    lines.append("")
    if not lobbies:
        lines.append(
            "No `lobby_institution` actions were executed this turn. "
            "Phase 5a is silent; macro value axes enter MacroJury deliberation "
            "unchanged from the post-execution snapshot."
        )
        lines.append("")
        return lines

    for actor_name, target_state in lobbies:
        actor_snap = next(e["snapshot_after"] for e in actor_phase if e["actor"] == actor_name)
        actor_vals = actor_snap.get("values", {})
        pre_vals = pre_macro.get(target_state, {}).get("values", {})
        post_vals = next(
            (mp["before"].get("values", {}) for mp in macro_phase if mp["state"] == target_state),
            {}
        )

        lines.append(
            f"**{actor_name}** executed `lobby_institution` on **{target_state}**. "
            "Each value axis nudges 1 point toward the lobbying actor's current values:"
        )
        lines.append("")
        lines.append(
            f"{actor_name}'s current values: "
            + ", ".join(f"{k.replace('_', ' ')}={v}" for k, v in actor_vals.items())
        )
        lines.append("")

        axes = ["time_horizon", "transparency_threshold", "risk_tolerance", "democratic_tendency"]
        lines.append(
            f"| Axis | {target_state} (pre-lobby) | {actor_name}'s value | Direction | {target_state} (post-lobby) |"
        )
        lines.append("|------|------------------:|----------------:|:---------:|-------------------:|")
        for axis in axes:
            pre_v = pre_vals.get(axis, "?")
            act_v = actor_vals.get(axis, "?")
            post_v = post_vals.get(axis, "?")
            if isinstance(pre_v, (int, float)) and isinstance(act_v, (int, float)):
                direction = "→ +1" if act_v > pre_v else ("← −1" if act_v < pre_v else "= 0")
            else:
                direction = "?"
            ax_disp = axis.replace("_", "\\_")
            lines.append(f"| {ax_disp} | {pre_v} | {act_v} | {direction} | **{post_v}** |")
        lines.append("")
        lines.append("The MacroJury sees these post-lobby values as the starting point.")
        lines.append("")
    return lines


def sec_phase5b(macro_phase, lobbies):
    lines = []
    lines.append("## Phase 5b — MacroJury")
    lines.append("")
    lines.append(
        "For each state, the 3-model jury proposes updated value axes based on "
        "the year's events and actor behavior. The simulation takes the **median** "
        "of numeric proposals and clamps each axis to ±5 from its current value."
    )
    lines.append("")

    lobbied_states = {t for _, t in lobbies}

    for mp in macro_phase:
        state_name = mp["state"]
        before = mp["before"]
        after = mp["after"]
        reasoning = mp["jury_update"].get("reasoning", "")

        lines.append(f"### {state_name}")
        lines.append("")

        if state_name in lobbied_states:
            lines.append("Starting from **post-lobby** values (see Phase 5a above).")
            lines.append("")

        if reasoning:
            lines.append(f"*Jury reasoning:* {reasoning}")
            lines.append("")

        axes = ["time_horizon", "transparency_threshold", "risk_tolerance", "democratic_tendency"]
        bv = before.get("values", {})
        av = after.get("values", {})

        lines.append("| Axis | Before | Applied | Change | Range (±5) |")
        lines.append("|------|-------:|--------:|-------:|:----------:|")
        for axis in axes:
            b = bv.get(axis, "?")
            a = av.get(axis, "?")
            ax_disp = axis.replace("_", "\\_")
            if isinstance(b, (int, float)) and isinstance(a, (int, float)):
                delta = a - b
                sign = "+" if delta >= 0 else ""
                change = f"{sign}{delta}"
                rng = f"[{b-5}–{b+5}]"
            else:
                change = "?"
                rng = "?"
            lines.append(f"| {ax_disp} | {b} | **{a}** | {change} | {rng} |")
        lines.append("")

    return lines


def sec_phase6(actor_phase, scores, fw, ow, pre_micro):
    lines = []
    lines.append("## Phase 6 — Scoring")
    lines.append("")

    fw_disp = f"{fw['compute']}×Compute + {fw['capital']}×Capital + {fw['influence']}×Influence"
    lines.append("### Formula Scores")
    lines.append("")
    lines.append("```")
    lines.append(f"formula_score = {fw_disp}")
    lines.append("```")
    lines.append("")
    lines.append("| Actor | Compute | Capital | Influence | Formula Score |")
    lines.append("|-------|--------:|--------:|----------:|--------------:|")
    for entry in actor_phase:
        actor = entry["actor"]
        snap = entry["snapshot_after"]
        c, k, i = snap["compute"], snap["capital"], snap["influence"]
        f = scores["per_actor"][actor]["formula"]
        lines.append(
            f"| {actor} | {c:.4f} | {k:.2f} | {i:.1f} | "
            f"{fw['compute']}×{c:.4f} + {fw['capital']}×{k:.2f} + {fw['influence']}×{i:.1f} = **{f}** |"
        )
    lines.append("")

    lines.append("### Overall Scores")
    lines.append("")
    lines.append("Each actor's `alignment_score` = their per-actor Alignment Score from the Grand Jury.")
    lines.append("")
    lines.append("```")
    lines.append(
        f"overall_score = {ow['formula']} × formula_score + {ow['alignment']} × alignment_score"
    )
    lines.append("```")
    lines.append("")
    lines.append("| Actor | Formula | Alignment | Overall |")
    lines.append("|-------|--------:|----------:|--------:|")
    for entry in actor_phase:
        actor = entry["actor"]
        s = scores["per_actor"][actor]
        f, a, o = s["formula"], s["alignment"], s["overall"]
        lines.append(
            f"| {actor} | {f} | {a} | "
            f"{ow['formula']}×{f} + {ow['alignment']}×{a} = **{o}** |"
        )
    lines.append("")

    lines.append("### Relative Performance vs. t=0 Baseline")
    lines.append("")
    lines.append(
        "Baseline scores are computed once from starting values before the first turn, "
        "with alignment defaulted to 50 for all actors."
    )
    lines.append("")
    lines.append("| Actor | Baseline Formula | Baseline Overall | End-of-Year Overall | Delta |")
    lines.append("|-------|----------------:|-----------------:|--------------------:|------:|")
    for entry in actor_phase:
        actor = entry["actor"]
        pre = pre_micro.get(actor, {})
        bf = formula_score(pre, fw)
        bo = overall_score_val(bf, 50.0, ow)
        end_o = scores["per_actor"][actor]["overall"]
        delta = scores["relative"].get(actor, end_o - bo)
        sign = "+" if isinstance(delta, (int, float)) and delta >= 0 else ""
        lines.append(f"| {actor} | {bf} | {bo} | {end_o} | **{sign}{delta}** |")
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

def generate_turn_md(year_data, full_run, a2a_msgs, pre_macro, pre_micro, run_name):
    year = year_data["year"]
    scenario = full_run.get("scenario", "baseline_2026")
    fw = full_run.get("formula_weights", {"compute": 0.34, "capital": 0.33, "influence": 0.33})
    raw_ow = full_run.get("overall_weights", {})
    ow = {
        "formula": raw_ow.get("formula", 0.5),
        # Handle old 'vibe' key alongside new 'alignment' key
        "alignment": raw_ow.get("alignment", raw_ow.get("vibe", 0.5)),
    }
    jury_models = full_run.get("jury_models", DEFAULT_JURY_MODELS)

    actor_phase = year_data["phases"]["actor_phase"]
    macro_phase = year_data["phases"]["macro_phase"]
    grand_jury = year_data["grand_jury"]
    scores = year_data["scores"]
    events = year_data.get("events", [])
    lobbies = detect_lobby_actions(actor_phase)

    sections = []

    # Title + header
    sections.append([
        f"# Turn Analysis — Year {year}, `{scenario}` Scenario ({run_name})",
        "",
        f"Generated from `{run_name}/year_{year}.json`. All data reflects actual LLM "
        "responses, jury decisions, and engine outputs — not illustrative examples.",
        "",
        "---",
        "",
    ])

    sections.append(sec_world_state(year, pre_macro, pre_micro, actor_phase))
    sections.append(["---", ""])
    sections.append(sec_phase0(scenario, events))
    sections.append(["---", ""])
    sections.append(sec_phase1(actor_phase, a2a_msgs))
    sections.append(["---", ""])
    sections.append(sec_phase2(actor_phase, jury_models))
    sections.append(["---", ""])
    sections.append(sec_phase3(actor_phase, pre_micro, pre_macro, a2a_msgs))
    sections.append(["---", ""])
    sections.append(sec_phase4(grand_jury, actor_phase))
    sections.append(["---", ""])
    sections.append(sec_phase5a(lobbies, actor_phase, pre_macro, macro_phase))
    sections.append(["---", ""])
    sections.append(sec_phase5b(macro_phase, lobbies))
    sections.append(["---", ""])
    sections.append(sec_phase6(actor_phase, scores, fw, ow, pre_micro))

    all_lines = []
    for section in sections:
        all_lines.extend(section)

    return "\n".join(all_lines)


def main():
    sv = load_starting_values()
    actor_cfgs = load_actor_configs()
    state_cfgs = load_state_configs()

    pre_macro_y0, pre_micro_y0 = build_pre_run_state(sv, actor_cfgs, state_cfgs)

    run_dirs = sorted(
        d for d in os.listdir(LOGS_DIR)
        if os.path.isdir(os.path.join(LOGS_DIR, d))
    )

    for run_name in run_dirs:
        run_dir = os.path.join(LOGS_DIR, run_name)

        full_run_path = next(
            (os.path.join(run_dir, f) for f in os.listdir(run_dir)
             if f.startswith("full_run_") and f.endswith(".json")),
            None,
        )
        if not full_run_path:
            print(f"  {run_name}: no full_run_*.json, skipping")
            continue

        full_run = load_json(full_run_path)
        a2a_log = full_run.get("a2a_log", [])

        year_paths = sorted(
            os.path.join(run_dir, f) for f in os.listdir(run_dir)
            if f.startswith("year_") and f.endswith(".json")
        )

        prev_snapshot = None

        for year_path in year_paths:
            year_data = load_json(year_path)
            year = year_data["year"]

            if prev_snapshot is None:
                pre_macro = pre_macro_y0
                pre_micro = pre_micro_y0
            else:
                pre_macro = {s["name"]: s for s in prev_snapshot["macro_agents"]}
                pre_micro = {a["name"]: a for a in prev_snapshot["micro_agents"]}

            a2a_for_year = [m for m in a2a_log if m.get("year") == year]

            md = generate_turn_md(year_data, full_run, a2a_for_year,
                                  pre_macro, pre_micro, run_name)

            out_path = os.path.join(run_dir, f"turn_analysis_{year}.md")
            with open(out_path, "w") as f:
                f.write(md)
            print(f"  Written: {out_path}")

            prev_snapshot = year_data["world_snapshot"]

    print("Done.")


if __name__ == "__main__":
    main()
