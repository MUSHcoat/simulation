#!/usr/bin/env python3
"""
Micro-level prompt builder for particular actor action proposals.

Actors are AI models playing as themselves. Each actor sees:
- Universal context (rules, world state)
- Their parent state's current resources and values
- Their own resources and values
- Recent A2A messages
- Their action history
"""

import json
from typing import Any, Dict, List, Optional


def build_micro_action_prompt(
    actor: Any,
    parent_macro: Optional[Any],
    universal_ctx: str,
    year: int,
    public_messages: List[Dict[str, Any]],
    personal_messages: List[Dict[str, Any]],
) -> str:
    parts = [universal_ctx, ""]

    # --- Parent state context ---
    if parent_macro:
        parts.append(f"YOUR PARENT STATE: {parent_macro.name}")
        parts.append(
            f"  Resources: compute={parent_macro.compute:.1f}, "
            f"capital={parent_macro.capital:.1f}, influence={parent_macro.influence:.1f}"
        )
        parts.append(f"  Supply Chain Robustness: {parent_macro.supply_chain_robustness:.0f}")
        parts.append(f"  State values: {json.dumps(parent_macro.values)}")
        parts.append("")

    # --- Actor identity and state ---
    parts.append(f"YOU ARE: {actor.name}")
    parts.append(f"Role: {actor.narrative}")
    parts.append("")
    parts.append("YOUR CURRENT RESOURCES:")
    parts.append(f"  compute={actor.compute:.1f}, capital={actor.capital:.1f}, influence={actor.influence:.1f}")
    parts.append("")
    parts.append("YOUR CURRENT VALUES (inherited from parent state; you may deviate ±5/turn):")
    parts.append(json.dumps(actor.values, indent=2))
    parts.append("")

    # --- Action history ---
    if actor.history:
        parts.append("YOUR RECENT HISTORY (last 3 turns):")
        for entry in actor.history[-3:]:
            parts.append(f"  Year {entry.get('year')}: {[a.get('action_type') for a in entry.get('executed', [])]}")
            if entry.get("jury_feedback"):
                parts.append(f"  Jury feedback: {entry['jury_feedback']}")
        parts.append("")

    # --- A2A messages from last turn ---
    if public_messages:
        parts.append("PUBLIC MESSAGES FROM LAST TURN:")
        for msg in public_messages[-10:]:
            parts.append(f"  [{msg['sender']} → all]: {msg['content']}")
        parts.append("")

    if personal_messages:
        parts.append("MESSAGES ADDRESSED TO YOU:")
        for msg in personal_messages[-5:]:
            if msg.get("sender") != actor.name:
                parts.append(f"  [From {msg['sender']}]: {msg['content']}")
        parts.append("")

    # --- Task ---
    parts.append(f"YOUR TASK FOR YEAR {year}:")
    parts.append("")
    parts.append(
        f"You are {actor.name}, an AI model acting as a proxy for the company that built you "
        f"in this simulation. Act consistently with your company's real-world values, goals, "
        f"and strategic interests as you understand them."
    )
    parts.append("")
    parts.append(
        "Step 1: Write a chain of thought situational analysis. Consider:\n"
        "  - Current resource positions (yours and competitors)\n"
        "  - Which actions best advance your interests this turn\n"
        "  - Whether to cooperate, compete, or do both with other actors\n"
        "  - How your values should shape your choices\n"
        "  - Any messages you want to send to other actors"
    )
    parts.append("")
    parts.append(
        "Step 2: Propose up to 2 actions from the discrete action set.\n"
        "Step 3: Optionally include A2A messages to other actors (within 500-token budget)."
    )
    parts.append("")
    parts.append(
        "Respond with JSON only:\n"
        "{\n"
        '  "chain_of_thought": "<full situational analysis and reasoning>",\n'
        '  "actions": [\n'
        "    {\n"
        '      "action_type": "<acquire_compute|invest_capital|build_influence|publish_narrative|lobby_institution>",\n'
        '      "amount": <float, for acquire_compute/invest_capital/build_influence>,\n'
        '      "target": "<actor name, for publish_narrative>",\n'
        '      "value_axis": "<time_horizon|transparency_threshold|risk_tolerance|democratic_tendency, for publish_narrative>",\n'
        '      "value_delta": <signed int -10..+10, for publish_narrative>,\n'
        '      "rationale": "<why this action>"\n'
        "    }\n"
        "  ],\n"
        '  "a2a_messages": [\n'
        "    {\n"
        '      "recipient": "<actor name or *>",\n'
        '      "content": "<message text (counts against your 500-token budget)>"\n'
        "    }\n"
        "  ]\n"
        "}"
    )

    return "\n".join(parts)
