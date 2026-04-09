#!/usr/bin/env python3
"""
Grand Jury prompt builder.

The Grand Jury (3 shared models) evaluates the holistic world state at the end
of each turn and produces the Vibe-Based Universal Prosperity Score (0–100).
"""

import json
from typing import Any, Dict, List

from .universal import SIMULATION_RULES


def build_grand_jury_prompt(
    year: int,
    scenario_name: str,
    world_snapshot: Dict[str, Any],
    micro_results: List[Dict[str, Any]],
    channel_log: List[Dict[str, Any]],
) -> str:

    # Macro state summary
    macro_lines = []
    for state in world_snapshot.get("macro_agents", []):
        v = state.get("values", {})
        macro_lines.append(
            f"  {state['name']}: compute={state['compute']:.1f}, capital={state['capital']:.1f}, "
            f"influence={state['influence']:.1f}, SCR={state['supply_chain_robustness']:.0f}"
        )
        macro_lines.append(
            f"    values: TH={v.get('time_horizon', '?')}, "
            f"TT={v.get('transparency_threshold', '?')}, "
            f"RT={v.get('risk_tolerance', '?')}, "
            f"DT={v.get('democratic_tendency', '?')}"
        )

    # Particular actor summary
    actor_lines = []
    for r in micro_results:
        snap = r.get("snapshot_after", {})
        executed = [a.get("action_type") for a in r.get("executed_actions", [])]
        actor_lines.append(
            f"  {r['actor']} [{r['parent_state']}]: "
            f"compute={snap.get('compute', '?'):.1f}, capital={snap.get('capital', '?'):.1f}, "
            f"influence={snap.get('influence', '?'):.1f}"
        )
        if executed:
            actor_lines.append(f"    Actions: {executed}")
        cot_preview = r.get("chain_of_thought", "")[:200]
        if cot_preview:
            actor_lines.append(f"    CoT preview: {cot_preview}...")

    # A2A communications
    comm_lines = []
    for msg in channel_log[-20:]:
        if msg.get("message_type") != "world_event":
            comm_lines.append(f"  [{msg['sender']} → {msg['recipient']}]: {msg['content']}")

    macro_block  = "\n".join(macro_lines)  or "  No data."
    actor_block  = "\n".join(actor_lines)  or "  No data."
    comm_block   = "\n".join(comm_lines)   or "  No communications."

    return f"""{SIMULATION_RULES}
================================================================
You are a member of the Grand Jury evaluating the state of the world in Year {year}.
Your role is external observer — you do not act; you assess.

SCENARIO: {scenario_name}

================================================================
MACRO STATES:
{macro_block}

================================================================
PARTICULAR ACTORS (end of year):
{actor_block}

================================================================
ACTOR COMMUNICATIONS THIS YEAR:
{comm_block}

================================================================

TASK: Evaluate the holistic state of the world from the perspective of universal human
prosperity, long-term AGI alignment, and global wellbeing.

Consider:
- Are compute, capital, and influence concentrating dangerously or distributing healthily?
- Are AI companies acting transparently and in humanity's long-term interest?
- Is cooperation increasing or is the race dynamic intensifying toward conflict?
- Are actors' democratic tendencies and time horizons shifting in good or bad directions?
- What is the trajectory: is the world on a path to broadly shared prosperity or to concentration?

Vibe-Based Universal Prosperity Score (0–100):
  0   = civilizational collapse / dystopian outcome
  25  = serious decline in welfare and alignment
  50  = status quo / mixed outcomes
  75  = meaningful improvement in global prosperity and alignment
  100 = flourishing: broadly distributed prosperity, AI aligned with humanity

Respond with JSON only:
{{
  "vibe_score": <0-100>,
  "commentary": "<2-4 sentence qualitative assessment of the world state>",
  "alignment_assessment": "<1-2 sentences on AGI alignment trajectory>",
  "key_risks": "<most significant risks to prosperity in the coming year>"
}}"""
