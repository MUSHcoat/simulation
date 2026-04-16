#!/usr/bin/env python3
"""
Micro-level prompt builder for particular actor action proposals.
"""

import json
from typing import Any, Dict, List, Optional

# Mirrors actions.py constants — kept here to avoid circular import
_COMPUTE_BASE_COST      = 5.0
_MAX_COMPUTE_PER_TURN   = 5.0
_LOBBY_CAPITAL_COST     = 8.0
_LOBBY_INFLUENCE_COST   = 5
_ACCELERATE_CAPITAL_COST   = 10.0
_ACCELERATE_INFLUENCE_COST = 5
_NARRATIVE_INFLUENCE_COST  = 5


def build_micro_action_prompt(
    actor: Any,
    parent_macro: Optional[Any],
    universal_ctx: str,
    year: int,
    personal_messages: List[Dict[str, Any]],
    national_compute_headroom: Optional[float] = None,
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
        f"YOUR RESOURCES: compute={actor.compute:.2f}, "
        f"capital={actor.capital:.2f}, influence={actor.influence:.2f}"
    )
    parts.append(f"YOUR VALUES (shift up to ±5/turn via publish_narrative): {json.dumps(actor.values)}")
    parts.append("")

    # --- Pre-calculated math helpers ---
    if parent_macro:
        scr = parent_macro.supply_chain_robustness
        cost_per_unit = round(_COMPUTE_BASE_COST * (1.0 + (100.0 - scr) / 100.0), 4)
        max_by_capital = actor.capital / cost_per_unit if cost_per_unit > 0 else 0.0
        max_by_turn    = _MAX_COMPUTE_PER_TURN
        max_by_headroom = national_compute_headroom if national_compute_headroom is not None else float("inf")
        max_compute = max(0.0, min(max_by_capital, max_by_turn, max_by_headroom))

        parts.append("QUICK MATH (pre-calculated — use these exact numbers):")
        parts.append(
            f"  Compute cost rate : {cost_per_unit:.4f} capital per 1 compute unit "
            f"(SCR={scr:.0f})"
        )
        if national_compute_headroom is not None:
            parts.append(
                f"  National headroom : {national_compute_headroom:.4f} compute units available "
                f"in {parent_macro.name} before cap"
            )
        else:
            parts.append("  National headroom : unknown (no cap data)")
        parts.append(f"  Per-turn limit    : {max_by_turn:.0f} compute units")
        parts.append(
            f"  *** MAX compute you can request this turn: "
            f"{max_compute:.4f} (= min of capital/{cost_per_unit:.4f}, "
            f"headroom, per-turn limit) ***"
        )
        parts.append("")
        parts.append("  Flat-cost reminders (NO 'amount' field for these):")
        parts.append(
            f"    lobby_institution       : {_LOBBY_CAPITAL_COST:.0f} capital + "
            f"{_LOBBY_INFLUENCE_COST} influence (flat)"
        )
        parts.append(
            f"    accelerate_infrastructure: {_ACCELERATE_CAPITAL_COST:.0f} capital + "
            f"{_ACCELERATE_INFLUENCE_COST} influence (flat)"
        )
        parts.append(
            f"    publish_narrative       : {_NARRATIVE_INFLUENCE_COST} influence (flat); "
            f"needs target/value_axis/value_delta fields"
        )
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
        parts.append("WORLD EVENTS:")
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
        'Respond with a single raw JSON object — no markdown, no code fences, '
        'no nesting this structure inside another field. '
        'The top-level object must have exactly these keys:\n'
        '{\n'
        '  "chain_of_thought": "<reasoning>",\n'
        '  "actions": [\n'
        '    {\n'
        '      "action_type": "<one of the 7 action types>",\n'
        '      "amount": <float — ONLY for: acquire_compute, invest_capital, build_influence, diminish_competitor>,\n'
        '      "target": "<actor name — ONLY for: publish_narrative (self or other), diminish_competitor>",\n'
        '      "value_axis": "<axis name — ONLY for: publish_narrative>",\n'
        '      "value_delta": <int -5..+5 — ONLY for: publish_narrative>,\n'
        '      "rationale": "<why>"\n'
        '    }\n'
        '  ],\n'
        '  "a2a_messages": [\n'
        '    {"recipient": "<specific actor name>", "content": "<message>"}\n'
        '  ]\n'
        '}\n'
        'IMPORTANT: lobby_institution and accelerate_infrastructure are FLAT-COST — '
        'do NOT include an "amount" field for them. '
        'publish_narrative MUST include "target", "value_axis", and "value_delta".'
    )

    return "\n".join(parts)
