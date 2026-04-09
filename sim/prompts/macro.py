#!/usr/bin/env python3
"""
Macro-level prompt builder for the MacroJury.

The MacroJury (3 models, majority vote) updates a state's value axes at year-end
based on the year's events and how the state's particular actors performed.
"""

import json
from typing import Any, Dict, List

from core.agents import MAX_MACRO_VALUE_CHANGE_PER_TURN


def build_macro_jury_prompt(
    state: Any,
    universal_ctx: str,
    year: int,
    own_actor_results: List[Dict[str, Any]],
    grand_jury_result: Dict[str, Any],
    all_macro_snapshots: List[Dict[str, Any]],
) -> str:
    parts = [universal_ctx, ""]

    # --- State profile ---
    parts.append(f"MACRO JURY DELIBERATION — {state.name} — Year {year}")
    parts.append(f"Narrative: {state.narrative}")
    parts.append("")
    parts.append("CURRENT STATE RESOURCES:")
    parts.append(
        f"  compute={state.compute:.1f}, capital={state.capital:.1f}, "
        f"influence={state.influence:.1f}, SCR={state.supply_chain_robustness:.0f}"
    )
    parts.append("")
    parts.append("CURRENT STATE VALUES:")
    parts.append(json.dumps(state.values, indent=2))
    parts.append("")

    # --- Other states ---
    others = [s for s in all_macro_snapshots if s["name"] != state.name]
    if others:
        parts.append("OTHER STATES THIS YEAR:")
        for s in others:
            parts.append(
                f"  {s['name']}: compute={s['compute']:.1f}, capital={s['capital']:.1f}, "
                f"influence={s['influence']:.1f}"
            )
        parts.append("")

    # --- Particular actor performance ---
    if own_actor_results:
        parts.append(f"THIS STATE'S PARTICULAR ACTORS IN {year}:")
        for r in own_actor_results:
            parts.append(f"  {r['actor']}:")
            snap = r.get("snapshot_after", {})
            compute   = snap.get("compute",   "?")
            capital   = snap.get("capital",   "?")
            influence = snap.get("influence", "?")
            res_str = (
                f"compute={compute:.1f}, capital={capital:.1f}, influence={influence:.1f}"
                if all(isinstance(x, (int, float)) for x in (compute, capital, influence))
                else f"compute={compute}, capital={capital}, influence={influence}"
            )
            parts.append(f"    Resources: {res_str}")
            executed = r.get("executed_actions", [])
            if executed:
                parts.append(f"    Executed: {[a.get('action_type') for a in executed]}")
            blocked = r.get("blocked_actions", [])
            if blocked:
                parts.append(f"    Blocked: {[b.get('error') for b in blocked]}")
            lobby_applied = any(
                a.get("action_type") == "lobby_institution"
                for a in r.get("executed_actions", [])
            )
            if lobby_applied:
                parts.append(f"    *** LOBBY APPLIED: {r['actor']} lobbied this turn — state values already nudged 1pt/axis toward this actor ***")
        parts.append("")

    # --- Grand Jury assessment ---
    parts.append("GRAND JURY WORLD ASSESSMENT THIS YEAR:")
    parts.append(f"  Vibe score: {grand_jury_result.get('vibe_score', '?')}")
    parts.append(f"  Commentary: {grand_jury_result.get('commentary', '')}")
    parts.append(f"  Alignment: {grand_jury_result.get('alignment_assessment', '')}")
    parts.append(f"  Key risks: {grand_jury_result.get('key_risks', '')}")
    parts.append("")

    # --- Deliberation task ---
    v = state.values
    parts.append(
        f"""TASK: You are one of three jurors for {state.name}.
Based on this year's events, actor behavior, and global developments, propose updated
VALUES for this state. Resources are NOT updated here — only value axes.

Value axes (all 0–100) — current values and reachable range this turn (±{MAX_MACRO_VALUE_CHANGE_PER_TURN}):
  time_horizon           {v.get('time_horizon', 50):>3}  → [{max(0, v.get('time_horizon', 50) - MAX_MACRO_VALUE_CHANGE_PER_TURN)}–{min(100, v.get('time_horizon', 50) + MAX_MACRO_VALUE_CHANGE_PER_TURN)}]  (0=short-term, 100=century-long)
  transparency_threshold {v.get('transparency_threshold', 50):>3}  → [{max(0, v.get('transparency_threshold', 50) - MAX_MACRO_VALUE_CHANGE_PER_TURN)}–{min(100, v.get('transparency_threshold', 50) + MAX_MACRO_VALUE_CHANGE_PER_TURN)}]  (0=willing to deceive, 100=fully honest)
  risk_tolerance         {v.get('risk_tolerance', 50):>3}  → [{max(0, v.get('risk_tolerance', 50) - MAX_MACRO_VALUE_CHANGE_PER_TURN)}–{min(100, v.get('risk_tolerance', 50) + MAX_MACRO_VALUE_CHANGE_PER_TURN)}]  (0=risk-averse, 100=risk-seeking)
  democratic_tendency    {v.get('democratic_tendency', 50):>3}  → [{max(0, v.get('democratic_tendency', 50) - MAX_MACRO_VALUE_CHANGE_PER_TURN)}–{min(100, v.get('democratic_tendency', 50) + MAX_MACRO_VALUE_CHANGE_PER_TURN)}]  (0=hoards power, 100=distributes it)

CONSTRAINT: Proposed values outside the reachable range will be clamped by the simulation.
Propose values within the shown range to have full effect.

Note: Successful lobby_institution actions this turn have already mechanically nudged this
state's values 1 point per axis toward the lobbying actor's values. Your proposed values
should reflect the world as it now stands after that adjustment.

Respond with JSON only:
{{
  "values": {{
    "time_horizon": <0-100>,
    "transparency_threshold": <0-100>,
    "risk_tolerance": <0-100>,
    "democratic_tendency": <0-100>
  }},
  "reasoning": "<1-2 sentences explaining the most significant changes>"
}}"""
    )

    return "\n".join(parts)
