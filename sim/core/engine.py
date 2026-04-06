#!/usr/bin/env python3
"""
Simulation engine: year-by-year timestep loop.

Turn structure per spec (Section 5.2):
  1. Particular actors move simultaneously — CoT + action proposals
  2. Jury of Alignment review — approve or request revision
  3. Action execution — resource costs deducted; guardrails enforced
  4. Grand Jury — commits changes; produces Vibe Prosperity Score
  5. Macro Jury vote — each state updates its Macro values
"""

import json
import logging
import os
from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List, Optional

from .agents import MacroAgent, MicroAgent
from .jury import JuryOfAlignment, GrandJury, MacroJury
from .actions import validate_action, execute_action, MAX_ACTIONS_PER_TURN
from .llm import get_llm_response, parse_json_response
from .a2a import A2AChannel
from .scoring import (
    formula_score, vibe_prosperity_score, overall_score,
    compute_all_scores, compute_relative_scores,
    DEFAULT_FORMULA_WEIGHTS, DEFAULT_OVERALL_WEIGHTS,
)
from prompts.universal import build_universal_context
from prompts.micro import build_micro_action_prompt
from prompts.macro import build_macro_jury_prompt
from prompts.grand_jury import build_grand_jury_prompt

logger = logging.getLogger(__name__)


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

        self.channel = A2AChannel()
        self.alignment_jury = JuryOfAlignment(jury_models)
        self.grand_jury = GrandJury(jury_models)

        self.run_log: List[Dict[str, Any]] = []
        self.baseline_scores: Optional[Dict[str, Dict[str, float]]] = None

        os.makedirs(output_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, years: int) -> None:
        logger.info(
            f"=== Simulation start: {self.scenario_name} "
            f"({self.start_year}–{self.start_year + years - 1}) ==="
        )
        # Capture baseline formula scores at t=0
        snap0 = self._world_snapshot()
        self.baseline_scores = compute_all_scores(
            snap0, 50.0, self.formula_weights, self.overall_weights
        )

        for y in range(years):
            self.current_year = self.start_year + y
            logger.info(f"\n{'='*60}\nYEAR {self.current_year}\n{'='*60}")
            year_record = self._run_year()
            self.run_log.append(year_record)
            self._save_year_log(year_record)

        self._save_full_log()
        logger.info("=== Simulation complete ===")

    # ------------------------------------------------------------------
    # Year loop
    # ------------------------------------------------------------------

    def _run_year(self) -> Dict[str, Any]:
        year = self.current_year
        record: Dict[str, Any] = {"year": year, "phases": {}}

        # Reset per-turn A2A budgets
        for actor in self.micro_agents:
            actor.reset_turn_budget()

        # --- Inject scheduled events ---
        active_events = [e for e in self.events if e.get("year") == year]
        if active_events:
            logger.info(f"  Injecting {len(active_events)} event(s)")
            for event in active_events:
                self._inject_event(event)
        record["events"] = [e.get("name", "") for e in active_events]

        # --- Universal context (world state visible to all) ---
        universal_ctx = build_universal_context(
            year=year,
            scenario_name=self.scenario_name,
            world_snapshot=self._world_snapshot(),
            active_events=active_events,
        )

        # --- Phase 1 + 2 + 3: Actors propose → Jury reviews → Execute ---
        logger.info("  Phase 1–3: Actor proposals, jury review, action execution")
        micro_results = self._run_actor_phase(universal_ctx)
        record["phases"]["actor_phase"] = micro_results

        # --- Phase 4: Grand Jury ---
        logger.info("  Phase 4: Grand Jury")
        world_snap = self._world_snapshot()
        gj_prompt = build_grand_jury_prompt(
            year=year,
            scenario_name=self.scenario_name,
            world_snapshot=world_snap,
            micro_results=micro_results,
            channel_log=self.channel.get_public_log(year=year),
        )
        gj_result = self.grand_jury.evaluate(gj_prompt)
        record["grand_jury"] = gj_result

        # --- Phase 5: Macro Jury ---
        logger.info("  Phase 5: Macro Jury")
        macro_updates = self._run_macro_phase(universal_ctx, micro_results, gj_result)
        record["phases"]["macro_phase"] = macro_updates

        # --- Scoring ---
        world_snap_final = self._world_snapshot()
        v_score = vibe_prosperity_score(gj_result)
        current_scores = compute_all_scores(
            world_snap_final, v_score, self.formula_weights, self.overall_weights
        )
        relative = compute_relative_scores(current_scores, self.baseline_scores or current_scores)

        record["scores"] = {
            "per_actor": current_scores,
            "relative": relative,
            "vibe_prosperity": v_score,
        }
        record["world_snapshot"] = world_snap_final

        logger.info(f"  Vibe score: {v_score:.1f}")
        for name, s in sorted(current_scores.items(), key=lambda x: -x[1]["overall"]):
            logger.info(f"    {name}: overall={s['overall']:.1f} (F={s['formula']:.1f})")

        return record

    # ------------------------------------------------------------------
    # Phase implementations
    # ------------------------------------------------------------------

    def _run_actor_phase(self, universal_ctx: str) -> List[Dict[str, Any]]:
        """
        Phases 1–3 combined:
          1. All actors produce CoT + action proposals simultaneously.
          2. JuryOfAlignment reviews each; actor may revise once if rejected.
          3. Validated actions are executed; resources updated.
        """
        results = []
        for actor in self.micro_agents:
            logger.info(f"    Actor: {actor.name}")
            parent_macro = self._get_macro(actor.parent_state)

            # --- Phase 1: Actor proposes ---
            prompt = build_micro_action_prompt(
                actor=actor,
                parent_macro=parent_macro,
                universal_ctx=universal_ctx,
                year=self.current_year,
                public_messages=self.channel.get_public_log(year=self.current_year - 1),
                personal_messages=self.channel.receive(actor.name, year=self.current_year - 1),
            )
            raw = get_llm_response(actor.llm_model, prompt, temperature=0.7, max_tokens=2000)
            response = parse_json_response(raw)
            cot = response.get("chain_of_thought", raw[:500])
            proposed_actions = _extract_actions(response)

            # Clamp to max 2 actions
            proposed_actions = proposed_actions[:MAX_ACTIONS_PER_TURN]

            # --- Phase 2: Jury of Alignment ---
            review = self.alignment_jury.review(
                actor_spec=actor.snapshot(),
                cot=cot,
                proposed_actions=proposed_actions,
            )

            if not review["approved"]:
                logger.info(f"      JuryOfAlignment rejected → requesting revision")
                revision_prompt = (
                    prompt
                    + f"\n\nJURY FEEDBACK (revise your actions):\n{review['feedback']}"
                    + "\n\nRevised JSON response:"
                )
                raw2 = get_llm_response(actor.llm_model, revision_prompt,
                                        temperature=0.5, max_tokens=1500)
                response2 = parse_json_response(raw2)
                cot = response2.get("chain_of_thought", cot)
                proposed_actions = _extract_actions(response2)[:MAX_ACTIONS_PER_TURN]

            # --- Phase 3: Execute validated actions ---
            executed, errors = [], []
            for action in proposed_actions:
                err = validate_action(action, actor, self.macro_agents, self.micro_agents)
                if err:
                    errors.append({"action": action, "error": err})
                    logger.info(f"      Action blocked: {err}")
                else:
                    effect = execute_action(action, actor, self.macro_agents, self.micro_agents)
                    executed.append(effect)

            # Publish actions to A2A channel
            summary = "; ".join(
                a.get("action_type", "unknown") for a in proposed_actions
            ) or "no actions"
            self.channel.send(
                sender=actor.name,
                recipient="*",
                content=f"{actor.name} this turn: {summary}",
                year=self.current_year,
                message_type="a2a",
            )

            actor.history.append({
                "year": self.current_year,
                "cot": cot[:300],
                "executed": executed,
                "errors": errors,
                "jury_feedback": review["feedback"],
            })

            results.append({
                "actor": actor.name,
                "parent_state": actor.parent_state,
                "chain_of_thought": cot,
                "proposed_actions": proposed_actions,
                "jury_approved": review["approved"],
                "jury_feedback": review["feedback"],
                "executed_actions": executed,
                "blocked_actions": errors,
                "snapshot_after": actor.snapshot(),
            })

        return results

    def _run_macro_phase(self, universal_ctx: str, micro_results: List[Dict],
                         gj_result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Phase 5: MacroJury for each state updates Macro values."""
        updates = []
        for state in self.macro_agents:
            logger.info(f"    MacroJury: {state.name}")
            jury = MacroJury(state.llm_models or self.jury_models)

            own_actors = [r for r in micro_results if r["parent_state"] == state.name]
            prompt = build_macro_jury_prompt(
                state=state,
                universal_ctx=universal_ctx,
                year=self.current_year,
                own_actor_results=own_actors,
                grand_jury_result=gj_result,
                all_macro_snapshots=[s.snapshot() for s in self.macro_agents],
            )

            jury_update = jury.deliberate(prompt)
            old_snap = state.snapshot()
            state.apply_jury_update(jury_update)
            new_snap = state.snapshot()

            # Propagate updated state values to actors that haven't overridden them
            # (inheritance: actors start each year re-anchored to parent state)
            for actor in self.micro_agents:
                if actor.parent_state == state.name:
                    actor.apply_value_override(
                        proposed_values=dict(actor.values),  # keep existing overrides
                        parent_values=state.values,
                    )

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

        # Macro resource shifts
        for state_name, shifts in event.get("macro_resource_shifts", {}).items():
            state = self._get_macro(state_name)
            if state:
                for attr, delta in shifts.items():
                    if hasattr(state, attr):
                        old = getattr(state, attr)
                        setattr(state, attr, max(0.0, min(100.0, old + delta)))

        # Macro value shifts
        for state_name, shifts in event.get("macro_value_shifts", {}).items():
            state = self._get_macro(state_name)
            if state:
                for axis, delta in shifts.items():
                    if axis in state.values:
                        state.values[axis] = max(0, min(100, state.values[axis] + delta))

        # Micro resource/value shifts
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

        # Broadcast event
        self.channel.broadcast_world_event(
            description=event.get("description", event.get("name", "World event")),
            year=self.current_year,
            event_name=event.get("name", ""),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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
    # Single-action fallback
    if "action_type" in response:
        return [response]
    return []
