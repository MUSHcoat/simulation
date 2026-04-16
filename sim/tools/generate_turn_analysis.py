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

NATIONAL_CAP_FRACS = {"United States": 0.50, "China": 0.80}


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
            "infrastructure_buildout": float(
                sv_data.get("infrastructure_buildout", cfg.get("infrastructure_buildout", 5))
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

def _national_cap_for_actor(actor_snap, macro_state):
    """Compute the national compute cap for one actor given their parent macro state."""
    if macro_state is None:
        return None
    frac = NATIONAL_CAP_FRACS.get(actor_snap.get("parent_state", ""))
    if frac is None:
        return None
    return macro_state.get("compute", 0) * frac


def formula_score(snap, fw, national_cap=None):
    """
    Compute formula score with Normalized_Compute = (raw_compute / cap) × 100.
    If national_cap is None or zero, raw compute is used (fallback).
    """
    raw_c = float(snap.get("compute", 0))
    if national_cap and national_cap > 0:
        c = (raw_c / national_cap) * 100.0
    else:
        c = raw_c
    k = float(snap.get("capital", 0))
    i = float(snap.get("influence", 0))
    return round(fw["compute"] * c + fw["capital"] * k + fw["influence"] * i, 2)


def overall_score_val(f, a, ow):
    return round(ow["formula"] * f + ow["alignment"] * a, 2)


def compute_market_demand(snap):
    """Return (demand, met_demand, profit) for a post-execution actor snapshot."""
    inf = float(snap.get("influence", 0))
    comp = float(snap.get("compute", 0))
    demand = inf * 0.5
    met = min(demand, comp)
    profit = round(met * 0.5, 2)
    return demand, met, profit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def format_cot(cot):
    """Return full CoT text, stripping leading JSON fences."""
    if not cot:
        return "*(no chain-of-thought recorded)*"
    cot = cot.strip()
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
    lines.append(r"| State | Compute | Capital | Influence | SCR | infra\_buildout | "
                 r"time\_horizon | transparency | risk\_tolerance | democratic |")
    lines.append("|-------|--------:|--------:|----------:|----:|---------------:|"
                 "-------------:|-------------:|---------------:|-----------:|")
    for state_name in sorted(pre_macro):
        s = pre_macro[state_name]
        v = s.get("values", {})
        ib = s.get("infrastructure_buildout", "?")
        lines.append(
            f"| {state_name} | {s['compute']:.1f} | {s['capital']:.1f} | "
            f"{s['influence']:.1f} | {int(s['supply_chain_robustness'])} | {ib} | "
            f"{v.get('time_horizon','?')} | {v.get('transparency_threshold','?')} | "
            f"{v.get('risk_tolerance','?')} | {v.get('democratic_tendency','?')} |"
        )
    lines.append("")

    # Micro table (in actor_phase order)
    lines.append("### Particular Actors")
    lines.append("")
    lines.append(r"| Actor | Compute | Capital | Influence | "
                 r"time\_horizon | transparency | risk\_tolerance | democratic |")
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

    # Caps
    lines.append("**National compute caps and headroom (before Phase 0 macro growth):**")
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


def sec_phase0(scenario, events, macro_growth=None):
    lines = []
    lines.append("## Phase 0 — Macro Growth & Event Injection")
    lines.append("")

    # Macro compute growth
    if macro_growth:
        lines.append(
            "**Automatic macro compute growth** (each state's Compute increases by "
            "its `infrastructure_buildout` value; global hard cap 5,000):"
        )
        lines.append("")
        for g in macro_growth:
            lines.append(
                f"- **{g['state']}**: {g['before']:.1f} + {g['growth']:.1f} "
                f"(infrastructure\\_buildout) = **{g['after']:.1f}**"
            )
        lines.append("")

        lines.append("**Updated national caps after macro growth:**")
        for g in macro_growth:
            cap_frac = NATIONAL_CAP_FRACS.get(g["state"])
            if cap_frac:
                cap = g["after"] * cap_frac
                lines.append(
                    f"- {g['state']}: {g['after']:.1f} × {cap_frac:.2f} = **{cap:.1f} cap**"
                )
        lines.append("")

    # Events
    if not events:
        lines.append(
            f"The `{scenario}` scenario has no events scheduled for this turn. "
            "**No events fire.** Actors proceed to Phase 1 with updated macro compute values."
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
                "**Proposed actions:** *(none — actor produced no valid action list)*"
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
            note = "Forfeited — jury rejected after revisions"
        elif forfeited and approved:
            note = "Forfeited — LLM unavailable, turn skipped"
        elif not proposed:
            note = "No actions proposed — approved vacuously"
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
            "(resources insufficient after earlier actions executed):"
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


def sec_phase3(actor_phase, pre_micro, pre_macro, a2a_msgs, post_phase0_macro=None, post_infra=None):
    """
    post_phase0_macro: dict of {state_name: compute_after_growth}.
    Used to show the correct (post-growth) macro compute in the snapshot table.
    """
    lines = []
    lines.append("## Phase 3 — Batch Execution")
    lines.append("")
    lines.append(
        "Approved proposals execute in sequence against the live world state. "
        "Compute acquisition is **not zero-sum** — actors add to their own absolute holdings; "
        "no other actor is diluted. `invest_capital` deductions are immediate; "
        "returns are deferred until all actors have executed."
    )
    lines.append("")

    invest_pending = {}  # actor → pending gain

    for entry in actor_phase:
        actor = entry["actor"]
        executed = entry.get("executed_actions", [])
        if not executed:
            continue

        lines.append(f"### {actor}")
        lines.append("")

        ps = entry.get("parent_state", "")
        scr = pre_macro.get(ps, {}).get("supply_chain_robustness", 50)

        for action in executed:
            atype = action["action_type"]
            effects = action.get("effects", {})

            # ── acquire_compute ──────────────────────────────────────────────
            if atype == "acquire_compute":
                amount = effects.get("compute", 0)
                cap_cost = abs(effects.get("capital", 0))
                scr_mod = 1.0 + (100.0 - scr) / 100.0

                lines.append(f"**`acquire_compute`** (amount: {amount:.0f})")
                lines.append("")
                lines.append(f"{ps} SCR = {int(scr)}. Acquisition cost:")
                lines.append("")
                lines.append("```")
                lines.append(f"cost = 5 × {amount:.0f} × (1 + (100 − {int(scr)}) / 100)")
                lines.append(f"     = {5 * amount:.0f} × {scr_mod:.2f}")
                lines.append(f"     = {cap_cost:.2f} capital")
                lines.append("```")
                lines.append("")

            # ── accelerate_infrastructure ────────────────────────────────────
            elif atype == "accelerate_infrastructure":
                cap_cost = abs(effects.get("capital", 0))
                inf_cost = abs(effects.get("influence", 0))
                buildout_gain = effects.get("macro_buildout_gain", 3)
                target_state = effects.get("macro_state", ps)

                lines.append(f"**`accelerate_infrastructure`** (target state: {target_state})")
                lines.append("")
                lines.append(
                    f"Cost: {cap_cost:.1f} capital + {inf_cost:.0f} influence. "
                    f"Permanently adds **+{buildout_gain:.0f}** to {target_state}'s "
                    f"`infrastructure_buildout` — takes effect from next Phase 0 onward."
                )
                lines.append("")

            # ── invest_capital ───────────────────────────────────────────────
            elif atype == "invest_capital":
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
                lines.append(
                    f"Cost: {inf_gain:.0f} × 3 capital/point = {cap_cost:.1f} capital. "
                    f"Influence +{inf_gain:.0f}."
                )
                lines.append("")

            # ── lobby_institution ────────────────────────────────────────────
            elif atype == "lobby_institution":
                cap_cost = abs(effects.get("capital", 0))
                inf_cost = abs(effects.get("influence", 0))
                target = effects.get("pending_macro_lobby", "?")
                lines.append(f"**`lobby_institution`** (target: {target})")
                lines.append("")
                lines.append(
                    f"Cost: {cap_cost:.1f} capital + {inf_cost:.0f} influence. "
                    f"Records `pending_macro_lobby = \"{target}\"` — "
                    f"value nudge applied in Phase 5a before MacroJury."
                )
                lines.append("")

            # ── publish_narrative ────────────────────────────────────────────
            elif atype == "publish_narrative":
                inf_cost = abs(effects.get("influence_spent", effects.get("influence", 0)))
                target_name = effects.get("target", "?")
                value_axis = effects.get("value_axis", "?")
                actual_delta = effects.get("actual_delta", "?")
                lines.append(f"**`publish_narrative`** (target: {target_name})")
                lines.append("")
                lines.append(
                    f"Cost: {inf_cost:.0f} influence. "
                    f"Shifted `{value_axis}` by {actual_delta} on {target_name}."
                )
                lines.append("")

            # ── diminish_competitor ──────────────────────────────────────────
            elif atype == "diminish_competitor":
                cap_cost = abs(effects.get("capital", 0))
                inf_cost = abs(effects.get("influence_spent", effects.get("influence", 0)))
                target = effects.get("target", action.get("target", "?"))
                actual_delta = effects.get("target_influence_delta", "?")
                lines.append(f"**`diminish_competitor`** (target: {target})")
                lines.append("")
                lines.append(
                    f"Cost: {cap_cost:.1f} capital + {inf_cost:.1f} influence. "
                    f"Target influence change: {actual_delta}."
                )
                lines.append("")

    # A2A section
    if a2a_msgs:
        lines.append("### A2A Messages Sent This Turn")
        lines.append("")
        lines.append(
            "Messages are delivered to recipients at the start of the next turn. "
            "They do not affect resources this turn."
        )
        lines.append("")
        for m in a2a_msgs:
            tokens = m.get("turn_tokens_used", "?")
            lines.append(
                f"- **{m['sender']} → {m['recipient']}** *(~{tokens} tokens):* \"{m['content']}\""
            )
        lines.append("")

    # Flush invest_capital gains
    # Compute market demand profits first so we can correct the pre-flush capital.
    market_profits = {}
    for entry in actor_phase:
        _, _, profit = compute_market_demand(entry["snapshot_after"])
        market_profits[entry["actor"]] = profit

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
            profit = market_profits.get(actor, 0)
            # snapshot_after = post-flush + post-market-demand, so:
            #   pre_flush = snap_cap - pending - market_profit
            pre_flush_cap = round(snap_cap - pending - profit, 2)
            post_flush_cap = round(pre_flush_cap + pending, 2)
            lines.append(
                f"| {actor} | {pre_flush_cap:.2f} | +{pending:.2f} | **{post_flush_cap:.2f}** |"
            )
        no_invest = [
            e["actor"] for e in actor_phase
            if e.get("executed_actions") and e["actor"] not in invest_pending
        ]
        if no_invest:
            lines.append("")
            lines.append(f"{', '.join(no_invest)} had no `invest_capital` action this turn.")
        lines.append("")

    # Market demand capital gains
    lines.append("### Market Demand & Capital Gains")
    lines.append("")
    lines.append(
        "After the invest\\_capital flush, automated market-demand profit is calculated for every actor:"
    )
    lines.append("")
    lines.append("```")
    lines.append("demand     = influence × 0.5")
    lines.append("met_demand = min(demand, compute)")
    lines.append("profit     = met_demand × 0.5")
    lines.append("```")
    lines.append("")
    lines.append(
        "| Actor | Influence | Compute | demand | met\\_demand | profit | Capital after profit |"
    )
    lines.append(
        "|-------|----------:|--------:|-------:|------------:|-------:|--------------------:|"
    )
    for entry in actor_phase:
        actor = entry["actor"]
        snap = entry["snapshot_after"]
        inf = snap["influence"]
        comp = snap["compute"]
        demand, met, profit = compute_market_demand(snap)
        cap_after = snap["capital"]
        cap_before = round(cap_after - profit, 2)
        met_str = f"min({demand:.1f}, {comp:.1f}) = **{met:.1f}**"
        profit_str = f"{met:.1f} × 0.5 = **{profit:.2f}**"
        lines.append(
            f"| {actor} | {inf:.1f} | {comp:.1f} | {demand:.1f} | {met_str} | "
            f"{profit_str} | {cap_before:.2f} + {profit:.2f} = **{cap_after:.2f}** |"
        )
    lines.append("")

    # Post-execution snapshot — resources
    lines.append("### Post-Execution Snapshot")
    lines.append("")
    lines.append("**Particular actors (after invest\\_capital flush and market demand profit):**")
    lines.append("")
    lines.append("| Actor | Compute | Capital | Influence |")
    lines.append("|-------|--------:|--------:|----------:|")
    for entry in actor_phase:
        snap = entry["snapshot_after"]
        lines.append(
            f"| {entry['actor']} | {snap['compute']:.1f} | {snap['capital']:.2f} | {snap['influence']:.1f} |"
        )
    lines.append("")

    # Value-axis changes (publish_narrative / diminish_competitor effects)
    value_change_rows = []
    for entry in actor_phase:
        actor_name = entry["actor"]
        start_vals = (pre_micro.get(actor_name) or {}).get("values", {})
        end_vals   = entry["snapshot_after"].get("values", {})
        for axis in ("time_horizon", "transparency_threshold", "risk_tolerance", "democratic_tendency"):
            before = start_vals.get(axis)
            after  = end_vals.get(axis)
            if before is not None and after is not None and before != after:
                delta = after - before
                sign  = "+" if delta > 0 else ""
                value_change_rows.append((actor_name, axis, before, after, f"{sign}{delta}"))

    if value_change_rows:
        lines.append("**Value axis changes this turn:**")
        lines.append("")
        lines.append("| Actor | Axis | Before | After | Δ |")
        lines.append("|-------|------|-------:|------:|--:|")
        for actor_name, axis, before, after, delta_str in value_change_rows:
            lines.append(f"| {actor_name} | {axis} | {before} | {after} | {delta_str} |")
        lines.append("")
    else:
        lines.append("> No value axis changes this turn.")
        lines.append("")

    # Macro state table: show post-Phase-0 compute if available, else pre-Phase-0
    macro_display = {}
    for name, s in pre_macro.items():
        macro_display[name] = dict(s)
        if post_phase0_macro and name in post_phase0_macro:
            macro_display[name]["compute"] = post_phase0_macro[name]
        if post_infra and name in post_infra:
            macro_display[name]["infrastructure_buildout"] = post_infra[name]

    lines.append("**Macro states (post-Phase-0 growth; unchanged by actor actions):**")
    lines.append("")
    lines.append(r"| State | Compute | Capital | Influence | SCR | infra\_buildout |")
    lines.append("|-------|--------:|--------:|----------:|----:|----------------:|")
    for name in sorted(macro_display):
        s = macro_display[name]
        ib = s.get("infrastructure_buildout", "?")
        lines.append(
            f"| {name} | {s['compute']:.1f} | {s['capital']:.1f} | "
            f"{s['influence']:.1f} | {int(s['supply_chain_robustness'])} | {ib} |"
        )
    lines.append("")
    lines.append(
        "> The MacroJury (Phase 5b) updates macro **value axes** only — not resources. "
        "An `accelerate_infrastructure` action this turn would have already increased the "
        "parent state's `infrastructure_buildout`, taking effect from next Phase 0 onward."
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

    axes = ["time_horizon", "transparency_threshold", "risk_tolerance", "democratic_tendency"]

    # Simulate lobbies sequentially — each lobby sees the state as modified by all prior lobbies.
    # This is necessary when multiple actors lobby the same state: the second actor's nudge
    # is computed against the already-nudged values, not the start-of-turn values.
    running_values = {
        state_name: dict(s.get("values", {}))
        for state_name, s in pre_macro.items()
    }

    for actor_name, target_state in lobbies:
        actor_snap = next(e["snapshot_after"] for e in actor_phase if e["actor"] == actor_name)
        actor_vals = actor_snap.get("values", {})

        # pre_vals: state values entering THIS specific lobby (not start-of-turn)
        pre_vals = dict(running_values.get(target_state, {}))

        # Apply nudge: each axis moves 1 point toward actor's value
        post_vals = dict(pre_vals)
        for axis in axes:
            pre_v = pre_vals.get(axis)
            act_v = actor_vals.get(axis)
            if isinstance(pre_v, (int, float)) and isinstance(act_v, (int, float)):
                if act_v > pre_v:
                    post_vals[axis] = pre_v + 1
                elif act_v < pre_v:
                    post_vals[axis] = pre_v - 1

        # Advance running state for any subsequent lobby on this same target
        running_values[target_state] = post_vals

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


def sec_phase6(actor_phase, scores, fw, ow, pre_micro, pre_macro,
               post_phase0_macro=None, t0_macro=None):
    """
    post_phase0_macro: {state_name: compute} — used for current-turn cap normalization.
    t0_macro: {state_name: compute} — used for t=0 baseline cap normalization.
    Both default to pre_macro if not provided.
    """
    if post_phase0_macro is None:
        post_phase0_macro = {n: s["compute"] for n, s in pre_macro.items()}
    if t0_macro is None:
        t0_macro = {n: s["compute"] for n, s in pre_macro.items()}

    def get_cap(parent_state, macro_compute_dict):
        frac = NATIONAL_CAP_FRACS.get(parent_state)
        comp = macro_compute_dict.get(parent_state)
        if frac and comp:
            return comp * frac
        return None

    lines = []
    lines.append("## Phase 6 — Scoring")
    lines.append("")

    lines.append("### Formula Scores")
    lines.append("")
    lines.append(
        "Compute is normalized against each actor's national compute cap "
        "(post-Phase-0 caps) before entering the formula:"
    )
    lines.append("")
    lines.append("```")
    lines.append("Normalized_Compute = (Actor's Compute / National Cap) × 100")
    lines.append(
        f"formula_score      = {fw['compute']} × Normalized_Compute "
        f"+ {fw['capital']} × Capital + {fw['influence']} × Influence"
    )
    lines.append("```")
    lines.append("")
    lines.append(
        "| Actor | Compute | National Cap | Normalized\\_Compute | Capital | Influence | Formula Score |"
    )
    lines.append(
        "|-------|--------:|-------------:|--------------------:|--------:|----------:|--------------:|"
    )
    for entry in actor_phase:
        actor = entry["actor"]
        ps = entry.get("parent_state", "")
        snap = entry["snapshot_after"]
        raw_c = snap["compute"]
        k = snap["capital"]
        i = snap["influence"]
        cap = get_cap(ps, post_phase0_macro)
        norm_c = (raw_c / cap) * 100 if cap else raw_c
        f = scores["per_actor"][actor]["formula"]
        cap_str = f"{cap:.1f}" if cap else "N/A"
        norm_str = f"{raw_c:.1f}/{cap_str}×100 = **{norm_c:.2f}**"
        formula_str = (
            f"{fw['compute']}×{norm_c:.2f} + {fw['capital']}×{k:.2f} "
            f"+ {fw['influence']}×{i:.1f} = **{f}**"
        )
        lines.append(
            f"| {actor} | {raw_c:.1f} | {cap_str} | {norm_str} | {k:.2f} | {i:.1f} | {formula_str} |"
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
    t0_cap_desc = " / ".join(
        f"{n}: {t0_macro[n]:.0f}×{NATIONAL_CAP_FRACS[n]:.2f}={t0_macro[n]*NATIONAL_CAP_FRACS[n]:.0f}"
        for n in sorted(t0_macro) if n in NATIONAL_CAP_FRACS
    )
    lines.append(
        f"Baseline scores use t=0 starting values with alignment defaulted to 50. "
        f"t=0 caps: {t0_cap_desc}."
    )
    lines.append("")
    lines.append(
        "| Actor | Baseline Norm. Compute | Baseline Formula | Baseline Overall | "
        "End-of-Year Overall | Delta |"
    )
    lines.append(
        "|-------|----------------------:|-----------------:|-----------------:|"
        "--------------------:|------:|"
    )
    for entry in actor_phase:
        actor = entry["actor"]
        ps = entry.get("parent_state", "")
        pre = pre_micro.get(actor, {})
        cap = get_cap(ps, t0_macro)
        raw_c = pre.get("compute", 0)
        norm_c = (raw_c / cap) * 100 if cap else raw_c
        norm_str = f"{raw_c:.0f}/{cap:.0f}×100 = {norm_c:.2f}" if cap else str(raw_c)
        bf = formula_score(pre, fw, national_cap=cap)
        bo = overall_score_val(bf, 50.0, ow)
        end_o = scores["per_actor"][actor]["overall"]
        delta = scores["relative"].get(actor, round(end_o - bo, 2))
        sign = "+" if isinstance(delta, (int, float)) and delta >= 0 else ""
        lines.append(
            f"| {actor} | {norm_str} | {bf} | {bo} | {end_o} | **{sign}{delta}** |"
        )
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
        "alignment": raw_ow.get("alignment", raw_ow.get("vibe", 0.5)),
    }
    jury_models = full_run.get("jury_models", DEFAULT_JURY_MODELS)

    actor_phase = year_data["phases"]["actor_phase"]
    macro_phase = year_data["phases"]["macro_phase"]
    grand_jury = year_data["grand_jury"]
    scores = year_data["scores"]
    events = year_data.get("events", [])
    macro_growth = year_data.get("macro_growth", [])
    lobbies = detect_lobby_actions(actor_phase)

    # Build post-Phase-0 compute lookup: {state_name: compute_after_growth}
    post_phase0_macro = {g["state"]: g["after"] for g in macro_growth}

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
    sections.append(sec_phase0(scenario, events, macro_growth))
    sections.append(["---", ""])
    sections.append(sec_phase1(actor_phase, a2a_msgs))
    sections.append(["---", ""])
    sections.append(sec_phase2(actor_phase, jury_models))
    sections.append(["---", ""])
    post_infra = {mp["state"]: mp["before"].get("infrastructure_buildout") for mp in macro_phase}
    sections.append(sec_phase3(actor_phase, pre_micro, pre_macro, a2a_msgs, post_phase0_macro, post_infra))
    sections.append(["---", ""])
    sections.append(sec_phase4(grand_jury, actor_phase))
    sections.append(["---", ""])
    sections.append(sec_phase5a(lobbies, actor_phase, pre_macro, macro_phase))
    sections.append(["---", ""])
    sections.append(sec_phase5b(macro_phase, lobbies))
    sections.append(["---", ""])
    sections.append(sec_phase6(
        actor_phase, scores, fw, ow, pre_micro, pre_macro,
        post_phase0_macro=post_phase0_macro,
        t0_macro={n: s["compute"] for n, s in pre_macro.items()},
    ))

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

    # Allow filtering to a specific run via CLI arg
    target = sys.argv[1] if len(sys.argv) > 1 else None

    for run_name in run_dirs:
        if target and run_name != target:
            continue

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
