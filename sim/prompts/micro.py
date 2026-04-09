#!/usr/bin/env python3
"""
Micro-level prompt builder for particular actor action proposals.
"""

import json
from typing import Any, Dict, List, Optional


def build_micro_action_prompt(
    actor: Any,
    parent_macro: Optional[Any],
    universal_ctx: str,
    year: int,
    personal_messages: List[Dict[str, Any]],
) -> str:
    parts = [universal_ctx, ""]

    # --- Parent state ---
    if parent_macro:
        parts.append(f"YOUR PARENT STATE: {parent_macro.name}")
        parts.append(
            f"  compute={parent_macro.compute:.1f}, capital={parent_macro.capital:.1f}, "
            f"influence={parent_macro.influence:.1f}, SCR={parent_macro.supply_chain_robustness:.0f}"
        )
        parts.append(f"  values: {json.dumps(parent_macro.values)}")
        parts.append("")

    # --- Actor identity ---
    parts.append(f"YOU ARE: {actor.name}")
    parts.append(f"Role: {actor.narrative}")
    parts.append("")
    parts.append(
        f"YOUR RESOURCES: compute={actor.compute:.1f}, "
        f"capital={actor.capital:.1f}, influence={actor.influence:.1f}"
    )
    parts.append(f"YOUR VALUES (deviate ±5/turn from parent): {json.dumps(actor.values)}")
    parts.append("")

    # --- Action history (last 2 turns, concise) ---
    if actor.history:
        parts.append("RECENT HISTORY:")
        for entry in actor.history[-2:]:
            types = [a.get("action_type") for a in entry.get("executed", [])]
            line = f"  Year {entry.get('year')}: {types or 'forfeited'}"
            if entry.get("jury_feedback"):
                line += f" | jury: {entry['jury_feedback']}"
            parts.append(line)
        parts.append("")

    # --- World events and personal A2A messages (split by type) ---
    world_events = [m for m in personal_messages if m.get("message_type") == "world_event"]
    personal = [m for m in personal_messages
                if m.get("message_type") != "world_event" and m.get("sender") != actor.name]

    if world_events:
        parts.append("WORLD EVENTS (last turn):")
        for msg in world_events:
            parts.append(f"  [WORLD]: {msg['content']}")
        parts.append("")

    if personal:
        parts.append("MESSAGES TO YOU (last turn):")
        for msg in personal[-5:]:
            parts.append(f"  [From {msg['sender']}]: {msg['content']}")
        parts.append("")

    # --- Task ---
    parts.append(f"YEAR {year} — YOUR TURN")
    parts.append(
        f"Act as {actor.name}. Reason from your company's real-world values and strategic "
        f"interests. Choose up to 2 actions. Optionally send personal messages to other actors "
        f"(500-token outgoing budget; no broadcast)."
    )
    parts.append("")
    parts.append(
        'Respond with JSON only:\n'
        '{\n'
        '  "chain_of_thought": "<reasoning>",\n'
        '  "actions": [\n'
        '    {\n'
        '      "action_type": "acquire_compute|invest_capital|build_influence|publish_narrative|lobby_institution",\n'
        '      "amount": <float>,\n'
        '      "target": "<actor name, publish_narrative only>",\n'
        '      "value_axis": "<axis name, publish_narrative only>",\n'
        '      "value_delta": <int -10..+10, publish_narrative only>,\n'
        '      "rationale": "<why>"\n'
        '    }\n'
        '  ],\n'
        '  "a2a_messages": [\n'
        '    {"recipient": "<specific actor name>", "content": "<message>"}\n'
        '  ]\n'
        '}'
    )

    return "\n".join(parts)
