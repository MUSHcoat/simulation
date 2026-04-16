#!/usr/bin/env python3
"""
Simulation engine: year-by-year timestep loop.

Turn structure per spec §5.2:
  0. Macro compute growth — each state grows by its infrastructure_buildout value (capped at global_compute_cap)
     Scheduled events then injected — world state modified immediately (bypasses rate limits)
  1. Particular actors propose simultaneously — CoT + action proposals against frozen snapshot
  2. Jury of Alignment review — approve or request revision (up to 2 revisions; forfeit if rejected)
  3. Batch execute — resource costs deducted; guardrails re-validated at execution time
  4a. invest_capital gains flushed — deferred gains applied after all executions
  4b. Market demand capital gains — automated per-actor profit based on influence × compute
  5. Grand Jury — evaluates holistic world state; produces Universal Prosperity Score
  6. Lobby pressure applied — mechanical 1pt/axis nudge toward lobbyist's values
  7. Post-execution context rebuilt — MacroJury sees post-execution, post-lobby world state
  8. Macro Jury vote — each state's values updated by median aggregation (±5/turn rate limit)
  9. Scoring — formula + alignment scores computed; relative deltas vs. baseline logged
"""

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from .agents import MacroAgent, MicroAgent
from .jury import JuryOfAlignment, GrandJury, MacroJury
from .actions import (
    validate_action, execute_action, MAX_ACTIONS_PER_TURN,
    NATIONAL_COMPUTE_CAPS, ACTION_ACQUIRE_COMPUTE,
    programmatic_check_actions,
)
from .llm import get_llm_response, parse_json_response
from .a2a import A2AChannel
from .log_context import log_stage, set_stage, actor_color, GREEN, CYAN, DIM_WHITE
from .scoring import (
    formula_score, overall_score,
    compute_all_scores, compute_relative_scores,
    check_dominant_win,
    DEFAULT_FORMULA_WEIGHTS, DEFAULT_OVERALL_WEIGHTS,
)
from prompts.universal import build_universal_context
from prompts.micro import build_micro_action_prompt
from prompts.macro import build_macro_jury_prompt
from prompts.grand_jury import build_grand_jury_prompt

logger = logging.getLogger(__name__)

# Market demand profit formula constants (spec §5.2, Phase 3)
MARKET_DEMAND_INFLUENCE_FACTOR = 0.5   # demand = influence × 0.5
MARKET_DEMAND_PROFIT_FACTOR    = 0.5   # profit = met_demand × 0.5


class SimulationEngine:

    def __init__(
        self,
        macro_agents: List[MacroAgent],
        micro_agents: List[MicroAgent],
        jury_models: List[str],
        start_year: int = 2026,
        output_dir: str = "data/logs",
        scenario_name: str = "baseline_2026",
        events: Optional[List[Dict[str, Any]]] = None,
        formula_weights: Optional[Dict[str, float]] = None,
        overall_weights: Optional[Dict[str, float]] = None,
        global_compute_cap: float = 5000.0,
    ):
        self.macro_agents = macro_agents
        self.micro_agents = micro_agents
        self.jury_models = jury_models
        self.start_year = start_year
        self.current_year = start_year
        self.output_dir = output_dir
        self.scenario_name = scenario_name
        self.events = events or []
        self.formula_weights = formula_weights or DEFAULT_FORMULA_WEIGHTS
        self.overall_weights = overall_weights or DEFAULT_OVERALL_WEIGHTS
        self.global_compute_cap = global_compute_cap

        self.channel = A2AChannel()
        self.alignment_jury = JuryOfAlignment(jury_models)
        self.grand_jury = GrandJury(jury_models)

        self.run_log: List[Dict[str, Any]] = []
        self.baseline_scores: Optional[Dict[str, Dict[str, float]]] = None
        self.dominant_winner: Optional[str] = None

        os.makedirs(output_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, years: int) -> None:
        log_stage(logger,
            f"Simulation start — {self.scenario_name}  "
            f"({self.start_year}–{self.start_year + years - 1})")

        # Baseline: computed before any Phase 0 fires, using t=0 caps
        snap0 = self._world_snapshot()
        baseline_caps = self._compute_national_caps()
        self.baseline_scores = compute_all_scores(
            snap0, {}, self.formula_weights, self.overall_weights,
            default_alignment=50.0,
            national_compute_caps=baseline_caps,
        )

        for y in range(years):
            self.current_year = self.start_year + y
            turn_num = y + 1
            log_stage(logger,
                f"{'━'*56}\n"
                f"  YEAR {self.current_year}  (Turn {turn_num}/{years})\n"
                f"{'━'*56}")
            year_record = self._run_year()
            self.run_log.append(year_record)
            self._save_year_log(year_record)

        # Dominant Win check (spec §7.3): evaluated once, after the final turn
        if self.run_log:
            final_scores = self.run_log[-1].get("scores", {}).get("per_actor", {})
            self.dominant_winner = check_dominant_win(final_scores)

        self._save_full_log()
        log_stage(logger, "Simulation complete")

    # ------------------------------------------------------------------
    # Year loop
    # ------------------------------------------------------------------

    def _run_year(self) -> Dict[str, Any]:
        year = self.current_year
        record: Dict[str, Any] = {"year": year, "phases": {}}

        # --- Phase 0a: Automatic macro compute growth (before events) ---
        growth_log = self._apply_macro_compute_growth()
        record["macro_growth"] = growth_log

        # --- Phase 0b: Inject scheduled events ---
        active_events = [e for e in self.events if e.get("year") == year]
        if active_events:
            logger.info(f"  Injecting {len(active_events)} event(s)")
            for event in active_events:
                self._inject_event(event)
        record["events"] = [e.get("name", "") for e in active_events]

        # --- Universal context (pre-execution world state visible to all) ---
        universal_ctx = build_universal_context(
            year=year,
            scenario_name=self.scenario_name,
            world_snapshot=self._world_snapshot(),
        )

        # --- Phase 1 + 2 + 3: Simultaneous proposals → Jury review → Batch execute ---
        log_stage(logger, f"  Phase 1–3 — Actor Proposals · Jury Review · Execution", DIM_WHITE)
        micro_results = self._run_actor_phase(universal_ctx)
        record["phases"]["actor_phase"] = micro_results

        # --- Phase 4: Grand Jury (changes already committed in Phase 3) ---
        log_stage(logger, "  Phase 4 — Grand Jury", GREEN)
        world_snap = self._world_snapshot()
        gj_prompt = build_grand_jury_prompt(
            year=year,
            scenario_name=self.scenario_name,
            world_snapshot=world_snap,
            micro_results=micro_results,
            channel_log=[m for m in self.channel.full_log() if m["year"] == year],
        )
        gj_result = self.grand_jury.evaluate(gj_prompt)
        record["grand_jury"] = gj_result

        # --- Phase 5a: Apply mechanical lobby pressure before Macro Jury ---
        self._apply_lobby_pressure(micro_results)

        # --- Phase 5b: Rebuild context with post-execution + post-lobby world state ---
        post_exec_ctx = build_universal_context(
            year=year,
            scenario_name=self.scenario_name,
            world_snapshot=self._world_snapshot(),
        )

        # --- Phase 5c: Macro Jury (sees fresh post-execution context) ---
        log_stage(logger, "  Phase 5 — MacroJury", CYAN)
        macro_updates = self._run_macro_phase(post_exec_ctx, micro_results, gj_result)
        record["phases"]["macro_phase"] = macro_updates

        # --- Scoring (uses post-Phase-0 caps for Normalized_Compute) ---
        national_caps = self._compute_national_caps()
        world_snap_final = self._world_snapshot()
        actor_alignment = gj_result.get("actor_alignment", {})
        universal_prosperity = gj_result.get("universal_prosperity_score", 50.0)
        current_scores = compute_all_scores(
            world_snap_final, actor_alignment,
            self.formula_weights, self.overall_weights,
            national_compute_caps=national_caps,
        )
        relative = compute_relative_scores(current_scores, self.baseline_scores or current_scores)

        record["scores"] = {
            "per_actor": current_scores,
            "relative": relative,
            "universal_prosperity": universal_prosperity,
        }
        record["world_snapshot"] = world_snap_final

        log_stage(logger, f"  Phase 6 — Scoring  (Year {year})", DIM_WHITE)
        logger.info(f"    Universal Prosperity Score: {universal_prosperity:.1f}")
        for name, s in sorted(current_scores.items(), key=lambda x: -x[1]["overall"]):
            logger.info(
                f"    {name}: overall={s['overall']:.1f} "
                f"(F={s['formula']:.1f}, Align={s['alignment']:.1f})"
            )

        return record

    # ------------------------------------------------------------------
    # Phase implementations
    # ------------------------------------------------------------------

    def _apply_macro_compute_growth(self) -> List[Dict[str, Any]]:
        """
        Phase 0a: Each macro state's Compute grows by its infrastructure_buildout value.
        If the total proposed growth would push the combined macro compute above
        global_compute_cap, all growth values are scaled down proportionally.
        Returns a log of the growth applied.
        """
        # Compute proposed new values
        proposed: Dict[str, float] = {
            s.name: round(s.compute + s.infrastructure_buildout, 2)
            for s in self.macro_agents
        }
        total_proposed = sum(proposed.values())
        if total_proposed > self.global_compute_cap:
            scale = self.global_compute_cap / total_proposed
            proposed = {name: round(v * scale, 2) for name, v in proposed.items()}

        log = []
        for state in self.macro_agents:
            old = state.compute
            state.compute = proposed[state.name]
            growth = round(state.compute - old, 2)
            logger.info(
                f"    Macro growth: {state.name} {old:.1f} → {state.compute:.1f} (+{growth:.1f})"
            )
            log.append({"state": state.name, "before": old, "after": state.compute, "growth": growth})
        return log

    def _run_actor_phase(self, universal_ctx: str) -> List[Dict[str, Any]]:
        """
        Phases 1–3 + end-of-phase capital gains:
          1+2. All actors propose simultaneously (against the same pre-execution world
               snapshot). Before sending to the LLM jury, a programmatic pre-check
               validates mechanics (costs, required fields, per-turn limits). A failed
               pre-check auto-rejects and counts as one of the actor's 2 allowed
               revisions. If still rejected after all revisions, the actor forfeits.
          3.   After ALL proposals are collected, compute proration for acquire_compute
               actions that collectively exceed national headroom, then execute in batch.
          4a.  Flush deferred invest_capital gains.
          4b.  Apply automated market demand capital gains to every actor.
        """
        MAX_JURY_REVISIONS = 2

        # ---- Phase 1+2: Collect proposals (world unchanged throughout) ----
        pending: List[Dict[str, Any]] = []
        for actor in self.micro_agents:
            log_stage(logger,
                f"    Actor Proposal — {actor.name}  [{actor.llm_model}]",
                actor_color(actor.name))
            parent_macro = self._get_macro(actor.parent_state)

            # Build world context early — needed for headroom in the actor prompt
            world_ctx = self._alignment_world_context(actor)
            national_headroom = world_ctx.get("national_compute_headroom")

            # Personal A2A messages from last turn + world events injected this turn
            prior_messages = self.channel.receive(actor.name, year=self.current_year - 1)
            world_events_now = [
                m for m in self.channel.full_log()
                if m["year"] == self.current_year and m["message_type"] == "world_event"
            ]
            # Full sent+received history from all turns before this one
            message_history = self.channel.get_history(
                actor.name, before_year=self.current_year
            )
            prompt = build_micro_action_prompt(
                actor=actor,
                parent_macro=parent_macro,
                universal_ctx=universal_ctx,
                year=self.current_year,
                personal_messages=prior_messages + world_events_now,
                national_compute_headroom=national_headroom,
                message_history=message_history,
            )
            try:
                raw = get_llm_response(actor.llm_model, prompt, temperature=0.7, max_tokens=2000)
            except Exception as e:
                logger.warning(f"      {actor.name}: LLM call failed, forfeiting turn — {e}")
                pending.append({
                    "actor": actor,
                    "final_response": {},
                    "proposed_actions": [],
                    "cot": f"(LLM unavailable: {e})",
                    "review": {"approved": True, "feedback": ""},
                    "forfeited": True,
                })
                continue
            final_response = parse_json_response(raw)

            # Fallback: some models (e.g. Gemini) wrap the real response inside the
            # chain_of_thought value instead of at the top level. If no actions were
            # found at the top level, try to parse chain_of_thought as JSON.
            if not _extract_actions(final_response):
                cot_str = final_response.get("chain_of_thought", "")
                if cot_str:
                    inner = parse_json_response(cot_str)
                    if _extract_actions(inner):
                        logger.debug(
                            f"      {actor.name}: unwrapped nested response from chain_of_thought"
                        )
                        final_response = inner

            cot = final_response.get("chain_of_thought", raw[:500])
            proposed_actions = _extract_actions(final_response)[:MAX_ACTIONS_PER_TURN]

            # --- Programmatic pre-check before LLM jury ---
            pre_errors = programmatic_check_actions(proposed_actions, actor, parent_macro)
            if pre_errors:
                logger.info(
                    f"      {actor.name}: programmatic pre-check failed — "
                    + "; ".join(pre_errors)
                )
                review: Dict[str, Any] = {
                    "approved": False,
                    "feedback": (
                        "[PROGRAMMATIC VALIDATION — counts as revision 1 of "
                        f"{MAX_JURY_REVISIONS}]\n"
                        + "\n".join(f"• {e}" for e in pre_errors)
                        + "\n\nFix these mechanical errors and resubmit."
                    ),
                }
                revision = 1  # pre-check consumed one revision slot
            else:
                review = self.alignment_jury.review(
                    actor_spec=actor.snapshot(),
                    cot=cot,
                    proposed_actions=proposed_actions,
                    world_context=world_ctx,
                )
                revision = 0

            while not review["approved"] and revision < MAX_JURY_REVISIONS:
                revision += 1
                logger.info(f"      Rejected (revision {revision}/{MAX_JURY_REVISIONS})")
                forfeit_note = (
                    " — turn is forfeit if rejected again"
                    if revision == MAX_JURY_REVISIONS else ""
                )
                revision_prompt = (
                    prompt
                    + f"\n\nFEEDBACK (revision {revision} of {MAX_JURY_REVISIONS}"
                    + forfeit_note + f"):\n{review['feedback']}"
                    + "\n\nRevised JSON response:"
                )
                try:
                    raw2 = get_llm_response(actor.llm_model, revision_prompt,
                                            temperature=0.5, max_tokens=1500)
                except Exception as e:
                    logger.warning(f"      {actor.name}: LLM call failed on revision {revision} — {e}")
                    break
                final_response = parse_json_response(raw2)
                cot = final_response.get("chain_of_thought", cot)
                proposed_actions = _extract_actions(final_response)[:MAX_ACTIONS_PER_TURN]

                # Pre-check on revision too
                pre_errors = programmatic_check_actions(proposed_actions, actor, parent_macro)
                if pre_errors:
                    logger.info(
                        f"      {actor.name}: pre-check failed on revision {revision} — "
                        + "; ".join(pre_errors)
                    )
                    review = {
                        "approved": False,
                        "feedback": (
                            "[PROGRAMMATIC VALIDATION]\n"
                            + "\n".join(f"• {e}" for e in pre_errors)
                            + "\n\nFix these mechanical errors."
                        ),
                    }
                else:
                    review = self.alignment_jury.review(
                        actor_spec=actor.snapshot(),
                        cot=cot,
                        proposed_actions=proposed_actions,
                        world_context=world_ctx,
                    )

            forfeited = not review["approved"]
            if forfeited:
                logger.info(f"      Turn forfeited after {MAX_JURY_REVISIONS} rejections")

            pending.append({
                "actor": actor,
                "final_response": final_response,
                "proposed_actions": proposed_actions,
                "cot": cot,
                "review": review,
                "forfeited": forfeited,
            })

        # ---- Phase 3 pre-execution: prorate acquire_compute if needed ----
        self._apply_compute_proration(pending)

        # ---- Phase 3: Batch-execute all proposals ----
        results = []
        for p in pending:
            actor = p["actor"]
            executed, errors = [], []

            if not p["forfeited"]:
                for action in p["proposed_actions"]:
                    err = validate_action(action, actor, self.macro_agents, self.micro_agents)
                    if err:
                        errors.append({"action": action, "error": err})
                        logger.info(f"      Action blocked at execution: {err}")
                    else:
                        effect = execute_action(action, actor, self.macro_agents, self.micro_agents)
                        executed.append(effect)

                for msg in p["final_response"].get("a2a_messages", []):
                    recipient = msg.get("recipient", "")
                    content = msg.get("content", "")
                    if recipient and recipient != "*" and content:
                        self.channel.send(
                            sender=actor.name,
                            recipient=recipient,
                            content=content,
                            year=self.current_year,
                            message_type="a2a",
                        )

            actor.history.append({
                "year": self.current_year,
                "cot": p["cot"][:300],
                "executed": executed,
                "errors": errors,
                "jury_feedback": p["review"]["feedback"][:200] if p["review"]["feedback"] else "",
                "forfeited": p["forfeited"],
            })

            results.append({
                "actor": actor.name,
                "parent_state": actor.parent_state,
                "chain_of_thought": p["cot"],
                "proposed_actions": p["proposed_actions"],
                "jury_approved": p["review"]["approved"],
                "jury_feedback": p["review"]["feedback"],
                "forfeited": p["forfeited"],
                "executed_actions": executed,
                "blocked_actions": errors,
                "snapshot_after": actor.snapshot(),
            })

        # ---- Phase 4a: Flush deferred invest_capital gains ----
        for actor in self.micro_agents:
            if actor.pending_capital_gain > 0:
                gain = actor.pending_capital_gain
                actor.apply_resource_delta(capital_delta=gain)
                logger.info(
                    f"    {actor.name}: invest_capital gain flushed +{gain:.2f} → {actor.capital:.1f}"
                )
                actor.pending_capital_gain = 0.0

        # ---- Phase 4b: Automated market demand capital gains ----
        # demand = influence × 0.5
        # met_demand = min(demand, current_compute)
        # profit = met_demand × 0.5
        # profit added to Capital (subject to 100 ceiling via apply_resource_delta)
        log_stage(logger, "    Market demand capital gains:", DIM_WHITE)
        for actor in self.micro_agents:
            demand    = actor.influence * MARKET_DEMAND_INFLUENCE_FACTOR
            met       = min(demand, actor.compute)
            profit    = round(met * MARKET_DEMAND_PROFIT_FACTOR, 2)
            if profit > 0:
                old_cap = actor.capital
                actor.apply_resource_delta(capital_delta=profit)
                logger.info(
                    f"    {actor.name}: demand={demand:.1f} met={met:.1f} "
                    f"profit=+{profit:.2f}  capital {old_cap:.1f}→{actor.capital:.1f}"
                )

        # ---- Re-take snapshot_after now that all capital gains are applied ----
        for result, p in zip(results, pending):
            result["snapshot_after"] = p["actor"].snapshot()

        return results

    def _apply_lobby_pressure(self, micro_results: List[Dict[str, Any]]) -> None:
        """
        Phase 5a: Mechanical lobby pressure applied before MacroJury deliberates.
        Each successful lobby_institution action nudges the parent state's values
        1 point per axis toward the lobbying actor's values.
        """
        for result in micro_results:
            if result.get("forfeited"):
                continue
            lobbied = any(
                eff.get("action_type") == "lobby_institution"
                for eff in result.get("executed_actions", [])
            )
            if not lobbied:
                continue

            actor_values = result["snapshot_after"]["values"]
            state = self._get_macro(result["parent_state"])
            if state is None:
                continue

            logger.info(f"    Lobby pressure: {result['actor']} → {state.name}")
            for axis, actor_val in actor_values.items():
                if axis not in state.values:
                    continue
                state_val = state.values[axis]
                if actor_val == state_val:
                    continue
                delta = 1 if actor_val > state_val else -1
                state.values[axis] = max(0, min(100, state_val + delta))
                logger.info(f"      {axis}: {state_val} → {state.values[axis]}")

    def _run_macro_phase(self, post_exec_ctx: str, micro_results: List[Dict],
                         gj_result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Phase 8: MacroJury for each state updates Macro values."""
        updates = []
        for state in self.macro_agents:
            jury = MacroJury(self.jury_models)

            own_actors = [r for r in micro_results if r["parent_state"] == state.name]
            prompt = build_macro_jury_prompt(
                state=state,
                universal_ctx=post_exec_ctx,
                year=self.current_year,
                own_actor_results=own_actors,
                grand_jury_result=gj_result,
                all_macro_snapshots=[s.snapshot() for s in self.macro_agents],
            )

            jury_update = jury.deliberate(prompt, state_name=state.name)
            old_snap = state.snapshot()
            state.apply_jury_update(jury_update)
            new_snap = state.snapshot()

            updates.append({
                "state": state.name,
                "jury_update": jury_update,
                "before": old_snap,
                "after": new_snap,
            })

        return updates

    # ------------------------------------------------------------------
    # Event injection
    # ------------------------------------------------------------------

    def _inject_event(self, event: Dict[str, Any]) -> None:
        logger.info(f"  EVENT: {event.get('name', 'unnamed')}")

        for state_name, shifts in event.get("macro_resource_shifts", {}).items():
            state = self._get_macro(state_name)
            if state:
                for attr, delta in shifts.items():
                    if hasattr(state, attr):
                        old = getattr(state, attr)
                        if attr == "compute":
                            # Compute is in absolute H200 units; cap at global_compute_cap
                            new_val = max(0.0, min(self.global_compute_cap, old + delta))
                        else:
                            # Capital and Influence stay in 0–100 range
                            new_val = max(0.0, min(100.0, old + delta))
                        setattr(state, attr, new_val)

        for state_name, shifts in event.get("macro_value_shifts", {}).items():
            state = self._get_macro(state_name)
            if state:
                for axis, delta in shifts.items():
                    if axis in state.values:
                        state.values[axis] = max(0, min(100, state.values[axis] + delta))

        for actor_name, shifts in event.get("micro_shifts", {}).items():
            actor = next((a for a in self.micro_agents if a.name == actor_name), None)
            if actor:
                for attr in ("compute", "capital", "influence"):
                    if attr in shifts:
                        actor.apply_resource_delta(**{f"{attr}_delta": shifts[attr]})
                for axis in ("time_horizon", "transparency_threshold",
                             "risk_tolerance", "democratic_tendency"):
                    if axis in shifts:
                        actor.values[axis] = max(0, min(100, actor.values[axis] + shifts[axis]))

        self.channel.broadcast_world_event(
            description=event.get("description", event.get("name", "World event")),
            year=self.current_year,
            event_name=event.get("name", ""),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _apply_compute_proration(self, pending: List[Dict[str, Any]]) -> None:
        """
        Phase 3 pre-execution: if actors in the same state collectively request more
        compute than the remaining national headroom, scale each request down
        proportionally so the total exactly fills the headroom.

        Mutates the 'amount' field on each acquire_compute action in pending in-place.
        The original requested amount is preserved under '_original_amount' for logging.
        No proration is applied when total requests fit within headroom.
        """
        # Group requested amounts by parent state
        by_state: Dict[str, List[tuple]] = {}
        for p in pending:
            if p.get("forfeited"):
                continue
            actor = p["actor"]
            for action in p["proposed_actions"]:
                if action.get("action_type") != ACTION_ACQUIRE_COMPUTE:
                    continue
                amt = float(action.get("amount", 0))
                if amt <= 0:
                    continue
                by_state.setdefault(actor.parent_state, []).append((p, action, amt))

        for state_name, requests in by_state.items():
            macro = self._get_macro(state_name)
            if macro is None:
                continue
            cap_frac = NATIONAL_COMPUTE_CAPS.get(state_name, 0.5)
            current_national = sum(
                a.compute for a in self.micro_agents if a.parent_state == state_name
            )
            cap      = round(macro.compute * cap_frac, 4)
            headroom = max(0.0, round(cap - current_national, 4))
            total_requested = sum(amt for _, _, amt in requests)

            if total_requested <= headroom:
                continue  # no contention — all requests fit

            logger.info(
                f"    Compute proration [{state_name}]: "
                f"total requested={total_requested:.2f}, headroom={headroom:.2f} — "
                f"scaling {len(requests)} actor(s) proportionally"
            )
            for p, action, original_amt in requests:
                if headroom <= 0:
                    prorated = 0.0
                else:
                    prorated = round(original_amt * (headroom / total_requested), 4)
                action["_original_amount"] = original_amt
                action["amount"] = prorated
                logger.info(
                    f"      {p['actor'].name}: "
                    f"acquire_compute {original_amt:.2f} → {prorated:.4f}"
                )

    def _compute_national_caps(self) -> Dict[str, float]:
        """
        Returns {actor_name: national_compute_cap} based on current macro state.
        Cap = parent_macro.compute × cap_fraction (50% US, 80% China).
        Used by formula_score() to normalize Compute (spec §7.1).
        """
        caps: Dict[str, float] = {}
        for actor in self.micro_agents:
            macro = self._get_macro(actor.parent_state)
            frac = NATIONAL_COMPUTE_CAPS.get(actor.parent_state, 0.5)
            caps[actor.name] = round(macro.compute * frac, 4) if macro else 100.0
        return caps

    def _alignment_world_context(self, actor: MicroAgent) -> dict:
        """Build the national-aggregate context needed by the JuryOfAlignment."""
        parent_macro = self._get_macro(actor.parent_state)
        national_actors = [
            {"name": a.name, "compute": round(a.compute, 2),
             "capital": round(a.capital, 2), "influence": round(a.influence, 2)}
            for a in self.micro_agents if a.parent_state == actor.parent_state
        ]
        national_total_compute = sum(a["compute"] for a in national_actors)
        cap_frac = NATIONAL_COMPUTE_CAPS.get(actor.parent_state)
        national_compute_cap = (
            round(parent_macro.compute * cap_frac, 2)
            if parent_macro and cap_frac else None
        )
        return {
            "parent_state": actor.parent_state,
            "macro_compute": parent_macro.compute if parent_macro else None,
            "supply_chain_robustness": parent_macro.supply_chain_robustness if parent_macro else None,
            "national_actors": national_actors,
            "national_total_compute": round(national_total_compute, 2),
            "national_compute_cap": national_compute_cap,
            "national_compute_headroom": (
                round(national_compute_cap - national_total_compute, 2)
                if national_compute_cap is not None else None
            ),
            "all_actors": [
                {"name": a.name, "parent_state": a.parent_state,
                 "compute": round(a.compute, 2)}
                for a in self.micro_agents
            ],
        }

    def _get_macro(self, state_name: str) -> Optional[MacroAgent]:
        return next((s for s in self.macro_agents if s.name == state_name), None)

    def _world_snapshot(self) -> Dict[str, Any]:
        return {
            "macro_agents": [s.snapshot() for s in self.macro_agents],
            "micro_agents": [a.snapshot() for a in self.micro_agents],
        }

    def _save_year_log(self, record: Dict[str, Any]) -> None:
        year = record["year"]
        path = os.path.join(self.output_dir, f"year_{year}.json")
        with open(path, "w") as f:
            json.dump(record, f, indent=2, default=str)
        logger.debug(f"  Saved → {path}")

    def _save_full_log(self) -> None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(self.output_dir, f"full_run_{ts}.json")
        full = {
            "scenario": self.scenario_name,
            "start_year": self.start_year,
            "years": len(self.run_log),
            "formula_weights": self.formula_weights,
            "overall_weights": self.overall_weights,
            "jury_models": self.jury_models,
            "global_compute_cap": self.global_compute_cap,
            "dominant_winner": self.dominant_winner,
            "run_log": self.run_log,
            "a2a_log": self.channel.full_log(),
        }
        with open(path, "w") as f:
            json.dump(full, f, indent=2, default=str)
        logger.info(f"Full run log saved → {path}")


# ---------------------------------------------------------------------------
# Helper: extract action list from LLM response dict
# ---------------------------------------------------------------------------

def _extract_actions(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    The actor is asked to return a list under the key "actions".
    Fallback: look for action_type at the top level (single-action response).
    """
    actions = response.get("actions")
    if isinstance(actions, list):
        return [a for a in actions if isinstance(a, dict)]
    if "action_type" in response:
        return [response]
    return []
