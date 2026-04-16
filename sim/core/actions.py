#!/usr/bin/env python3
"""
Discrete action set for particular (micro) actors.

Actions:
  1. acquire_compute           — spend Capital; gain absolute Compute.
                                 No dilution of other actors.
  2. accelerate_infrastructure — spend Capital + Influence; permanently adds +3 to the
                                 parent macro state's infrastructure_buildout value, which
                                 increases that state's per-turn compute growth from Phase 0
                                 of the following turn onward.
  3. invest_capital            — spend Capital; gain Capital next turn (compounding)
  4. build_influence           — spend Capital; gain Influence
  5. publish_narrative         — spend Influence; shift any actor's (including self)
                                 value on one axis by up to ±MAX_VALUE_OVERRIDE_PER_TURN
                                 from their current value
  6. diminish_competitor       — spend Capital + Influence; reduce any other actor's Influence
  7. lobby_institution         — spend Capital + Influence; mechanically nudges parent
                                 state's values 1 point per axis toward the actor's values
                                 (applied before MacroJury)

All resource mutations go through execute_action(), which enforces guardrails and
returns a structured result dict.
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Action names (canonical strings)
# ---------------------------------------------------------------------------

ACTION_ACQUIRE_COMPUTE          = "acquire_compute"
ACTION_ACCELERATE_INFRASTRUCTURE = "accelerate_infrastructure"
ACTION_INVEST_CAPITAL           = "invest_capital"
ACTION_BUILD_INFLUENCE          = "build_influence"
ACTION_PUBLISH_NARRATIVE        = "publish_narrative"
ACTION_DIMINISH_COMPETITOR      = "diminish_competitor"
ACTION_LOBBY_INSTITUTION        = "lobby_institution"

VALID_ACTIONS = {
    ACTION_ACQUIRE_COMPUTE,
    ACTION_ACCELERATE_INFRASTRUCTURE,
    ACTION_INVEST_CAPITAL,
    ACTION_BUILD_INFLUENCE,
    ACTION_PUBLISH_NARRATIVE,
    ACTION_DIMINISH_COMPETITOR,
    ACTION_LOBBY_INSTITUTION,
}

# ---------------------------------------------------------------------------
# Guardrail constants (mirror of starting_values.json guardrails)
# ---------------------------------------------------------------------------

CAPITAL_CEILING          = 100.0
MIN_ACTION_COST          = 1.0
MAX_COMPUTE_PER_TURN     = 5.0
MAX_ACTIONS_PER_TURN     = 2

# National aggregate compute caps  {state_name: fraction_of_macro_compute}
NATIONAL_COMPUTE_CAPS: Dict[str, float] = {
    "United States": 0.50,
    "China":         0.80,
}

# Capital compounding rates by current capital level
# return_rate = base_return * (capital_after_deduction / 100 + 1)
CAPITAL_INVESTMENT_BASE_RETURN = 0.10   # 10% base return per turn on amount invested

# Base capital cost per compute point (modified by Supply Chain Robustness)
COMPUTE_BASE_COST = 5.0

# Cost of build_influence (capital per influence point gained)
INFLUENCE_BUILD_COST = 3.0

# Cost of publish_narrative (influence spent)
NARRATIVE_INFLUENCE_COST = 5

# Cost of diminish_competitor (per influence point removed from target)
DIMINISH_CAPITAL_COST_PER_POINT   = 2.0
DIMINISH_INFLUENCE_COST_PER_POINT = 1.0

# Cost of lobby_institution
LOBBY_CAPITAL_COST   = 8.0
LOBBY_INFLUENCE_COST = 5

# Cost/effect of accelerate_infrastructure
ACCELERATE_CAPITAL_COST    = 10.0
ACCELERATE_INFLUENCE_COST  = 5
ACCELERATE_BUILDOUT_GAIN   = 3.0    # added permanently to parent macro state's infrastructure_buildout


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_action(action: Dict[str, Any], actor, macro_agents: List,
                    all_micro_agents: List) -> Optional[str]:
    """
    Check whether `action` is valid for `actor`.
    Returns an error string if invalid, or None if valid.

    action dict structure:
      {
        "action_type": str,
        "amount":      float | None,   # for acquire_compute, invest_capital, build_influence
        "target":      str | None,     # for publish_narrative / diminish_competitor (actor name)
        "value_axis":  str | None,     # for publish_narrative
        "value_delta": int | None,     # for publish_narrative (signed, -5..+5)
      }
    """
    action_type = action.get("action_type", "")
    if action_type not in VALID_ACTIONS:
        return f"Unknown action type: {action_type!r}"

    amount = float(action.get("amount") or 0)
    parent_macro = _get_macro(actor.parent_state, macro_agents)

    if action_type == ACTION_ACQUIRE_COMPUTE:
        if amount <= 0:
            return "acquire_compute requires amount > 0"
        if amount > MAX_COMPUTE_PER_TURN:
            return f"acquire_compute amount {amount} exceeds per-turn cap of {MAX_COMPUTE_PER_TURN}"

        scr = parent_macro.supply_chain_robustness if parent_macro else 50.0
        cost = _compute_acquisition_cost(amount, scr)
        if actor.capital < max(MIN_ACTION_COST, cost):
            return f"Insufficient capital ({actor.capital:.1f}) for compute acquisition (cost {cost:.1f})"

        # Check national aggregate cap (post-Phase-0 macro compute is the reference)
        cap_frac = NATIONAL_COMPUTE_CAPS.get(actor.parent_state)
        if cap_frac is not None and parent_macro is not None:
            current_national = sum(
                a.compute for a in all_micro_agents if a.parent_state == actor.parent_state
            )
            cap = parent_macro.compute * cap_frac
            if current_national + amount > cap:
                return (
                    f"National aggregate compute cap reached "
                    f"({current_national:.1f} + {amount:.1f} > "
                    f"{parent_macro.compute:.1f} × {cap_frac} = {cap:.1f})"
                )

    elif action_type == ACTION_ACCELERATE_INFRASTRUCTURE:
        if actor.capital < max(MIN_ACTION_COST, ACCELERATE_CAPITAL_COST):
            return (f"Insufficient capital ({actor.capital:.1f}) for accelerate_infrastructure "
                    f"(cost {ACCELERATE_CAPITAL_COST:.0f})")
        if actor.influence < max(MIN_ACTION_COST, ACCELERATE_INFLUENCE_COST):
            return (f"Insufficient influence ({actor.influence:.1f}) for accelerate_infrastructure "
                    f"(cost {ACCELERATE_INFLUENCE_COST})")

    elif action_type == ACTION_INVEST_CAPITAL:
        if amount <= 0:
            return "invest_capital requires amount > 0"
        if actor.capital < amount:
            return f"Insufficient capital ({actor.capital:.1f}) to invest {amount:.1f}"

    elif action_type == ACTION_BUILD_INFLUENCE:
        if amount <= 0:
            return "build_influence requires amount > 0"
        cost = amount * INFLUENCE_BUILD_COST
        if actor.capital < max(MIN_ACTION_COST, cost):
            return f"Insufficient capital ({actor.capital:.1f}) to build {amount:.1f} influence (cost {cost:.1f})"

    elif action_type == ACTION_PUBLISH_NARRATIVE:
        target_name = action.get("target")
        if not target_name:
            return "publish_narrative requires a target actor name"
        if actor.influence < max(MIN_ACTION_COST, NARRATIVE_INFLUENCE_COST):
            return f"Insufficient influence ({actor.influence:.1f}) to publish narrative (cost {NARRATIVE_INFLUENCE_COST})"
        target = _resolve_actor(target_name, all_micro_agents)
        if target is None:
            return f"publish_narrative target {target_name!r} not found"
        value_axis = action.get("value_axis", "")
        if value_axis not in target.values:
            return f"publish_narrative value_axis {value_axis!r} not valid"
        value_delta = int(action.get("value_delta", 0))
        from .agents import MAX_VALUE_OVERRIDE_PER_TURN
        if abs(value_delta) > MAX_VALUE_OVERRIDE_PER_TURN:
            return (f"publish_narrative value_delta {value_delta} exceeds "
                    f"±{MAX_VALUE_OVERRIDE_PER_TURN} limit")

    elif action_type == ACTION_DIMINISH_COMPETITOR:
        if amount <= 0:
            return "diminish_competitor requires amount > 0"
        target_name = action.get("target")
        if not target_name:
            return "diminish_competitor requires a target actor name"
        target = _resolve_actor(target_name, all_micro_agents)
        if target is None:
            return f"diminish_competitor target {target_name!r} not found"
        if target.name == actor.name:
            return "diminish_competitor cannot target self"
        capital_cost = amount * DIMINISH_CAPITAL_COST_PER_POINT
        influence_cost = amount * DIMINISH_INFLUENCE_COST_PER_POINT
        if actor.capital < max(MIN_ACTION_COST, capital_cost):
            return (f"Insufficient capital ({actor.capital:.1f}) to diminish "
                    f"{amount:.1f} influence (cost {capital_cost:.1f})")
        if actor.influence < max(MIN_ACTION_COST, influence_cost):
            return (f"Insufficient influence ({actor.influence:.1f}) to diminish "
                    f"{amount:.1f} influence (cost {influence_cost:.1f})")

    elif action_type == ACTION_LOBBY_INSTITUTION:
        total_cost_k = max(MIN_ACTION_COST, LOBBY_CAPITAL_COST)
        total_cost_i = max(MIN_ACTION_COST, LOBBY_INFLUENCE_COST)
        if actor.capital < total_cost_k:
            return f"Insufficient capital ({actor.capital:.1f}) for lobby (cost {total_cost_k})"
        if actor.influence < total_cost_i:
            return f"Insufficient influence ({actor.influence:.1f}) for lobby (cost {total_cost_i})"

    return None  # valid


def execute_action(action: Dict[str, Any], actor, macro_agents: List,
                   all_micro_agents: List) -> Dict[str, Any]:
    """
    Execute a validated action, mutating resources in-place.
    Returns a result dict with what changed.
    """
    action_type = action["action_type"]
    amount = float(action.get("amount") or 0)
    result: Dict[str, Any] = {"action_type": action_type, "actor": actor.name, "effects": {}}

    parent_macro = _get_macro(actor.parent_state, macro_agents)

    if action_type == ACTION_ACQUIRE_COMPUTE:
        scr = parent_macro.supply_chain_robustness if parent_macro else 50.0
        cost = _compute_acquisition_cost(amount, scr)
        actor.capital -= cost
        actor.compute += amount
        result["effects"] = {"compute": +amount, "capital": -cost}
        logger.info(f"    {actor.name}: acquire_compute +{amount:.1f} (cost {cost:.1f} capital)")

    elif action_type == ACTION_ACCELERATE_INFRASTRUCTURE:
        actor.capital   -= ACCELERATE_CAPITAL_COST
        actor.influence -= ACCELERATE_INFLUENCE_COST
        if parent_macro is not None:
            parent_macro.infrastructure_buildout += ACCELERATE_BUILDOUT_GAIN
            logger.info(
                f"    {actor.name}: accelerate_infrastructure → "
                f"{parent_macro.name} infrastructure_buildout +{ACCELERATE_BUILDOUT_GAIN:.0f} "
                f"(now {parent_macro.infrastructure_buildout:.1f})"
            )
        result["effects"] = {
            "capital":   -ACCELERATE_CAPITAL_COST,
            "influence": -ACCELERATE_INFLUENCE_COST,
            "macro_buildout_gain": ACCELERATE_BUILDOUT_GAIN,
            "macro_state": actor.parent_state,
        }

    elif action_type == ACTION_INVEST_CAPITAL:
        # Deduct capital now; gain is deferred — engine flushes it after all actors execute
        actor.capital -= amount
        gain = round(amount * (1 + CAPITAL_INVESTMENT_BASE_RETURN * (actor.capital / 100 + 1)), 2)
        actor.pending_capital_gain += gain
        result["effects"] = {"capital_invested": -amount, "capital_gain_pending": gain}
        logger.info(f"    {actor.name}: invest_capital -{amount:.1f} (gain {gain:.2f} pending)")

    elif action_type == ACTION_BUILD_INFLUENCE:
        cost = amount * INFLUENCE_BUILD_COST
        actor.capital -= cost
        actor.influence = min(100.0, actor.influence + amount)
        result["effects"] = {"influence": +amount, "capital": -cost}
        logger.info(f"    {actor.name}: build_influence +{amount:.1f} (cost {cost:.1f} capital)")

    elif action_type == ACTION_PUBLISH_NARRATIVE:
        target_name = action.get("target", "")
        value_axis  = action.get("value_axis", "")
        value_delta = int(action.get("value_delta", 0))
        target = _resolve_actor(target_name, all_micro_agents)

        actor.influence -= NARRATIVE_INFLUENCE_COST
        if target and value_axis in target.values:
            old = target.values[value_axis]
            # Route through apply_value_override so the ±MAX_VALUE_OVERRIDE_PER_TURN
            # clamp is enforced relative to the target's current value.
            target.apply_value_override({value_axis: old + value_delta})
            actual_delta = target.values[value_axis] - old
            result["effects"] = {
                "influence_spent": -NARRATIVE_INFLUENCE_COST,
                "target": target_name,
                "value_axis": value_axis,
                "requested_delta": value_delta,
                "actual_delta": actual_delta,
            }
            logger.info(
                f"    {actor.name}: publish_narrative → {target_name}.{value_axis} "
                f"{old}→{target.values[value_axis]}"
            )

    elif action_type == ACTION_DIMINISH_COMPETITOR:
        target_name = action.get("target", "")
        target = _resolve_actor(target_name, all_micro_agents)
        capital_cost = amount * DIMINISH_CAPITAL_COST_PER_POINT
        influence_cost = amount * DIMINISH_INFLUENCE_COST_PER_POINT
        actor.capital   -= capital_cost
        actor.influence -= influence_cost
        if target:
            old_influence = target.influence
            target.influence = max(0.0, target.influence - amount)
            actual_delta = round(target.influence - old_influence, 2)
            result["effects"] = {
                "capital":               -capital_cost,
                "influence_spent":       -influence_cost,
                "target":                target_name,
                "target_influence_delta": actual_delta,
            }
            logger.info(
                f"    {actor.name}: diminish_competitor → {target_name} "
                f"influence {old_influence:.1f}→{target.influence:.1f}"
            )

    elif action_type == ACTION_LOBBY_INSTITUTION:
        actor.capital   -= LOBBY_CAPITAL_COST
        actor.influence -= LOBBY_INFLUENCE_COST
        result["effects"] = {
            "capital":   -LOBBY_CAPITAL_COST,
            "influence": -LOBBY_INFLUENCE_COST,
            "pending_macro_lobby": actor.parent_state,
        }
        logger.info(f"    {actor.name}: lobby_institution targeting {actor.parent_state}")

    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compute_acquisition_cost(amount: float, supply_chain_robustness: float) -> float:
    """cost = base × amount × (1 + (100 − SCR) / 100)"""
    scr_modifier = 1.0 + (100.0 - supply_chain_robustness) / 100.0
    return round(COMPUTE_BASE_COST * amount * scr_modifier, 2)


def _get_macro(state_name: str, macro_agents: List):
    return next((s for s in macro_agents if s.name == state_name), None)


def _resolve_actor(name: str, micro_agents: List):
    """
    Resolve an actor name to an agent, tolerating partial/short names.
    Tries exact match first, then case-insensitive prefix match, then
    case-insensitive substring match.  Returns None if no match.
    """
    if not name:
        return None
    # Exact match
    exact = next((a for a in micro_agents if a.name == name), None)
    if exact:
        return exact
    # Case-insensitive prefix or substring match
    lower = name.lower()
    for a in micro_agents:
        if a.name.lower().startswith(lower) or lower in a.name.lower():
            return a
    return None
