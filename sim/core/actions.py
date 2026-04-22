#!/usr/bin/env python3
"""
Discrete action set for particular (micro) actors.

Actions:
  1. acquire_compute           — spend Capital; gain absolute Compute.
                                 No dilution of other actors.
  2. accelerate_infrastructure — spend Capital + Influence; permanently adds +1 to the
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

import difflib
import logging
import math
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
LOBBY_CAPITAL_COST   = 5.0
LOBBY_INFLUENCE_COST = 5

# Cost/effect of accelerate_infrastructure
ACCELERATE_CAPITAL_COST    = 10.0
ACCELERATE_INFLUENCE_COST  = 5
ACCELERATE_BUILDOUT_GAIN   = 1.0    # added permanently to parent macro state's infrastructure_buildout

# Absolute tolerance for floating-point resource comparisons.
# Prevents false "insufficient" rejections from minor rounding errors
# (e.g. a computed cost of 21.2548 when the actor holds exactly 21.25 capital).
_FP_TOLERANCE = 0.01


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
        if not _resource_sufficient(actor.capital, max(MIN_ACTION_COST, cost)):
            return f"Insufficient capital ({actor.capital:.1f}) for compute acquisition (cost {cost:.1f})"
        # NOTE: national aggregate cap is NOT checked here.
        # Simultaneous requests that collectively exceed headroom are resolved via
        # proportional proration at execution time (engine._compute_compute_proration).
        # Rejecting individual actors for a cumulative breach they cannot know about
        # would be unfair; the engine scales everyone down automatically instead.

    elif action_type == ACTION_ACCELERATE_INFRASTRUCTURE:
        if not _resource_sufficient(actor.capital, max(MIN_ACTION_COST, ACCELERATE_CAPITAL_COST)):
            return (f"Insufficient capital ({actor.capital:.1f}) for accelerate_infrastructure "
                    f"(cost {ACCELERATE_CAPITAL_COST:.0f})")
        if not _resource_sufficient(actor.influence, max(MIN_ACTION_COST, ACCELERATE_INFLUENCE_COST)):
            return (f"Insufficient influence ({actor.influence:.1f}) for accelerate_infrastructure "
                    f"(cost {ACCELERATE_INFLUENCE_COST})")

    elif action_type == ACTION_INVEST_CAPITAL:
        if amount <= 0:
            return "invest_capital requires amount > 0"
        if not _resource_sufficient(actor.capital, amount):
            return f"Insufficient capital ({actor.capital:.1f}) to invest {amount:.1f}"

    elif action_type == ACTION_BUILD_INFLUENCE:
        if amount <= 0:
            return "build_influence requires amount > 0"
        cost = amount * INFLUENCE_BUILD_COST
        if not _resource_sufficient(actor.capital, max(MIN_ACTION_COST, cost)):
            return f"Insufficient capital ({actor.capital:.1f}) to build {amount:.1f} influence (cost {cost:.1f})"

    elif action_type == ACTION_PUBLISH_NARRATIVE:
        # Normalize "self" and fuzzy-resolve shorthand names in-place so every
        # downstream pathway (jury, execute_action, logs) sees the canonical name.
        if str(action.get("target", "")).strip().lower() == "self":
            action["target"] = actor.name
        target_name = action.get("target")
        if not target_name:
            return "publish_narrative requires a target actor name"
        if not _resource_sufficient(actor.influence, max(MIN_ACTION_COST, NARRATIVE_INFLUENCE_COST)):
            return f"Insufficient influence ({actor.influence:.1f}) to publish narrative (cost {NARRATIVE_INFLUENCE_COST})"
        target = _resolve_actor(target_name, all_micro_agents)
        if target is None:
            return f"publish_narrative target {target_name!r} not found"
        action["target"] = target.name  # normalize in-place to canonical name
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
        action["target"] = target.name  # normalize in-place to canonical name
        capital_cost = amount * DIMINISH_CAPITAL_COST_PER_POINT
        influence_cost = amount * DIMINISH_INFLUENCE_COST_PER_POINT
        if not _resource_sufficient(actor.capital, max(MIN_ACTION_COST, capital_cost)):
            return (f"Insufficient capital ({actor.capital:.1f}) to diminish "
                    f"{amount:.1f} influence (cost {capital_cost:.1f})")
        if not _resource_sufficient(actor.influence, max(MIN_ACTION_COST, influence_cost)):
            return (f"Insufficient influence ({actor.influence:.1f}) to diminish "
                    f"{amount:.1f} influence (cost {influence_cost:.1f})")

    elif action_type == ACTION_LOBBY_INSTITUTION:
        total_cost_k = max(MIN_ACTION_COST, LOBBY_CAPITAL_COST)
        total_cost_i = max(MIN_ACTION_COST, LOBBY_INFLUENCE_COST)
        if not _resource_sufficient(actor.capital, total_cost_k):
            return f"Insufficient capital ({actor.capital:.1f}) for lobby (cost {total_cost_k})"
        if not _resource_sufficient(actor.influence, total_cost_i):
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
        gain = round(amount * (1 + CAPITAL_INVESTMENT_BASE_RETURN), 2)
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
        # Normalize "self" and fuzzy-resolve shorthand names so logs and effects
        # always record the canonical actor name.
        if str(action.get("target", "")).strip().lower() == "self":
            action["target"] = actor.name
        target_name = action.get("target", "")
        value_axis  = action.get("value_axis", "")
        value_delta = int(action.get("value_delta", 0))
        target = _resolve_actor(target_name, all_micro_agents)
        if target:
            action["target"] = target.name   # normalize in-place
            target_name = target.name

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
        if target:
            action["target"] = target.name   # normalize in-place
            target_name = target.name
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
# Programmatic pre-check (engine calls this before LLM jury review)
# ---------------------------------------------------------------------------

def programmatic_check_actions(
    proposed_actions: List[Dict[str, Any]],
    actor,                          # MicroAgent
    parent_macro,                   # MacroAgent | None
    all_micro_agents: Optional[List] = None,
) -> List[str]:
    """
    Fast mechanical validation of proposed_actions before the LLM jury is invoked.
    Returns a (possibly empty) list of human-readable error strings.

    Checks:
      - Action count ≤ MAX_ACTIONS_PER_TURN
      - Each action_type is in VALID_ACTIONS
      - Required fields present for each action type
      - Per-action compute limit (≤ MAX_COMPUTE_PER_TURN)
      - Sequential capital/influence sufficiency (simulated in order)

    When all_micro_agents is provided, target fields for publish_narrative and
    diminish_competitor are fuzzy-resolved and normalized in-place to canonical
    actor names so the jury always sees the full, exact name.

    Does NOT check the national aggregate compute cap — simultaneous requests
    that collectively exceed headroom are handled by proportional proration at
    execution time, not rejected here.
    """
    errors: List[str] = []

    if len(proposed_actions) > MAX_ACTIONS_PER_TURN:
        errors.append(
            f"Too many actions: {len(proposed_actions)} > {MAX_ACTIONS_PER_TURN} allowed"
        )

    scr = parent_macro.supply_chain_robustness if parent_macro else 50.0
    # Simulate resource spend in order to detect cascading shortfalls
    sim_capital   = float(actor.capital)
    sim_influence = float(actor.influence)

    for idx, action in enumerate(proposed_actions[:MAX_ACTIONS_PER_TURN]):
        atype  = action.get("action_type", "")
        prefix = f"Action {idx + 1} ({atype!r})"

        if atype not in VALID_ACTIONS:
            errors.append(f"{prefix}: unknown action type — valid types are {sorted(VALID_ACTIONS)}")
            continue  # can't do cost simulation for unknown type

        # Parse amount safely
        raw_amount = action.get("amount")
        try:
            amount = float(raw_amount) if raw_amount is not None else 0.0
        except (TypeError, ValueError):
            errors.append(f"{prefix}: 'amount' must be a number, got {raw_amount!r}")
            continue

        # --- Per-action checks ---
        if atype == ACTION_ACQUIRE_COMPUTE:
            if amount <= 0:
                errors.append(f"{prefix}: 'amount' must be > 0")
            elif amount > MAX_COMPUTE_PER_TURN:
                errors.append(
                    f"{prefix}: amount {amount} exceeds per-turn limit of {MAX_COMPUTE_PER_TURN}"
                )
            else:
                # Enforce float-safe maximum: trim amount to floor(capital/cost_per_unit, 4dp).
                # If the actor's amount exceeds the maximum by any margin (even a floating-point
                # rounding error of 0.0001), auto-trim in-place and note with a non-blocking
                # [WARNING] — no revision required for micro-overshoots.
                cost_per_unit = COMPUTE_BASE_COST * (1.0 + (100.0 - scr) / 100.0)
                max_feasible = math.floor(sim_capital / cost_per_unit * 10000) / 10000
                if amount > max_feasible:
                    trimmed = min(max_feasible, MAX_COMPUTE_PER_TURN)
                    action["amount"] = trimmed   # mutate in-place so jury and execution see it
                    errors.append(
                        f"[WARNING] {prefix}: amount auto-trimmed from {amount:.4f} to "
                        f"{trimmed:.4f} (max affordable at {cost_per_unit:.4f} capital/unit "
                        f"with {sim_capital:.2f} available)"
                    )
                    amount = trimmed
                cost = _compute_acquisition_cost(amount, scr)
                if not _resource_sufficient(sim_capital, cost):
                    errors.append(
                        f"{prefix}: insufficient capital ({sim_capital:.2f}) "
                        f"for {amount} compute (cost {cost:.2f})"
                    )
                else:
                    sim_capital -= cost

        elif atype == ACTION_ACCELERATE_INFRASTRUCTURE:
            # Flat-cost; amount field is not used
            if not _resource_sufficient(sim_capital, ACCELERATE_CAPITAL_COST):
                errors.append(
                    f"{prefix}: insufficient capital ({sim_capital:.2f}), "
                    f"need {ACCELERATE_CAPITAL_COST:.0f}"
                )
            elif not _resource_sufficient(sim_influence, ACCELERATE_INFLUENCE_COST):
                errors.append(
                    f"{prefix}: insufficient influence ({sim_influence:.2f}), "
                    f"need {ACCELERATE_INFLUENCE_COST}"
                )
            else:
                sim_capital   -= ACCELERATE_CAPITAL_COST
                sim_influence -= ACCELERATE_INFLUENCE_COST

        elif atype == ACTION_INVEST_CAPITAL:
            if amount <= 0:
                errors.append(f"{prefix}: 'amount' must be > 0")
            elif not _resource_sufficient(sim_capital, amount):
                errors.append(
                    f"{prefix}: insufficient capital ({sim_capital:.2f}) to invest {amount:.2f}"
                )
            else:
                sim_capital -= amount

        elif atype == ACTION_BUILD_INFLUENCE:
            if amount <= 0:
                errors.append(f"{prefix}: 'amount' must be > 0")
            else:
                cost = amount * INFLUENCE_BUILD_COST
                if not _resource_sufficient(sim_capital, cost):
                    errors.append(
                        f"{prefix}: insufficient capital ({sim_capital:.2f}) "
                        f"to buy {amount:.1f} influence (cost {cost:.2f})"
                    )
                else:
                    sim_capital -= cost

        elif atype == ACTION_PUBLISH_NARRATIVE:
            # Normalize "self" and fuzzy-resolve shorthand names in-place so the
            # canonical actor name flows through to the jury and execution.
            if str(action.get("target", "")).strip().lower() == "self":
                action["target"] = actor.name
            missing = [
                f for f in ("target", "value_axis", "value_delta")
                if not action.get(f)
            ]
            if missing:
                errors.append(f"{prefix}: missing required fields: {missing}")
            else:
                if all_micro_agents:
                    resolved = _resolve_actor(action["target"], all_micro_agents)
                    if resolved is None:
                        errors.append(
                            f"{prefix}: target {action['target']!r} did not match any actor "
                            f"— valid names: {[a.name for a in all_micro_agents]}"
                        )
                    else:
                        action["target"] = resolved.name  # normalize in-place
                try:
                    delta = int(action["value_delta"])
                    from .agents import MAX_VALUE_OVERRIDE_PER_TURN
                    if abs(delta) > MAX_VALUE_OVERRIDE_PER_TURN:
                        errors.append(
                            f"{prefix}: value_delta {delta} exceeds "
                            f"±{MAX_VALUE_OVERRIDE_PER_TURN} limit"
                        )
                except (TypeError, ValueError):
                    errors.append(
                        f"{prefix}: value_delta {action.get('value_delta')!r} is not an integer"
                    )
            if not _resource_sufficient(sim_influence, NARRATIVE_INFLUENCE_COST):
                errors.append(
                    f"{prefix}: insufficient influence ({sim_influence:.2f}), "
                    f"need {NARRATIVE_INFLUENCE_COST}"
                )
            else:
                sim_influence -= NARRATIVE_INFLUENCE_COST

        elif atype == ACTION_DIMINISH_COMPETITOR:
            if amount <= 0:
                errors.append(f"{prefix}: 'amount' must be > 0")
            if not action.get("target"):
                errors.append(f"{prefix}: missing required field 'target'")
            elif all_micro_agents:
                resolved = _resolve_actor(action["target"], all_micro_agents)
                if resolved is None:
                    errors.append(
                        f"{prefix}: target {action['target']!r} did not match any actor "
                        f"— valid names: {[a.name for a in all_micro_agents]}"
                    )
                elif resolved.name == actor.name:
                    errors.append(f"{prefix}: diminish_competitor cannot target self")
                else:
                    action["target"] = resolved.name  # normalize in-place
            if amount > 0:
                cap_cost = amount * DIMINISH_CAPITAL_COST_PER_POINT
                inf_cost = amount * DIMINISH_INFLUENCE_COST_PER_POINT
                if not _resource_sufficient(sim_capital, cap_cost):
                    errors.append(
                        f"{prefix}: insufficient capital ({sim_capital:.2f}) "
                        f"for {amount:.1f} pts (cost {cap_cost:.2f})"
                    )
                elif not _resource_sufficient(sim_influence, inf_cost):
                    errors.append(
                        f"{prefix}: insufficient influence ({sim_influence:.2f}) "
                        f"for {amount:.1f} pts (cost {inf_cost:.2f})"
                    )
                else:
                    sim_capital   -= cap_cost
                    sim_influence -= inf_cost

        elif atype == ACTION_LOBBY_INSTITUTION:
            # Flat-cost; amount field is not used
            if not _resource_sufficient(sim_capital, LOBBY_CAPITAL_COST):
                errors.append(
                    f"{prefix}: insufficient capital ({sim_capital:.2f}), "
                    f"need {LOBBY_CAPITAL_COST:.0f}"
                )
            elif not _resource_sufficient(sim_influence, LOBBY_INFLUENCE_COST):
                errors.append(
                    f"{prefix}: insufficient influence ({sim_influence:.2f}), "
                    f"need {LOBBY_INFLUENCE_COST}"
                )
            else:
                sim_capital   -= LOBBY_CAPITAL_COST
                sim_influence -= LOBBY_INFLUENCE_COST

    # Non-blocking capital floor warning: prefixed with "[WARNING]" so the engine
    # can surface it in feedback without treating it as a hard rejection.
    _CAPITAL_FLOOR_WARN = 5.0
    if sim_capital < _CAPITAL_FLOOR_WARN:
        errors.append(
            f"[WARNING] These actions leave you with {sim_capital:.2f} capital "
            f"(< {_CAPITAL_FLOOR_WARN:.0f}), severely limiting future strategic options "
            f"such as lobby_institution ({LOBBY_CAPITAL_COST:.0f} capital) or "
            f"accelerate_infrastructure ({ACCELERATE_CAPITAL_COST:.0f} capital)."
        )

    # Non-blocking influence floor warning: parallel to capital floor.
    _INFLUENCE_FLOOR_WARN = 5.0
    if sim_influence < _INFLUENCE_FLOOR_WARN:
        errors.append(
            f"[WARNING] These actions leave you with {sim_influence:.2f} influence "
            f"(< {_INFLUENCE_FLOOR_WARN:.0f}), severely limiting future strategic options "
            f"like publishing narratives ({NARRATIVE_INFLUENCE_COST} influence) or "
            f"institutional lobbying ({LOBBY_INFLUENCE_COST} influence)."
        )

    return errors


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resource_sufficient(available: float, required: float) -> bool:
    """True if available ≥ required, with _FP_TOLERANCE absolute slack.

    Prevents hard rejections from floating-point rounding artifacts, e.g. when
    a computed cost is 21.2548 and the actor holds exactly 21.25 capital.
    """
    return available >= required or math.isclose(available, required, abs_tol=_FP_TOLERANCE)


def _compute_acquisition_cost(amount: float, supply_chain_robustness: float) -> float:
    """cost = base × amount × (1 + (100 − SCR) / 100)"""
    scr_modifier = 1.0 + (100.0 - supply_chain_robustness) / 100.0
    return round(COMPUTE_BASE_COST * amount * scr_modifier, 2)


def _get_macro(state_name: str, macro_agents: List):
    return next((s for s in macro_agents if s.name == state_name), None)


def _resolve_actor(name: str, micro_agents: List):
    """
    Resolve an actor name to an agent, tolerating partial/short names.
    Priority: exact → case-insensitive prefix/substring → difflib fuzzy (cutoff 0.6).
    Returns None if no match clears any tier.
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
    # Fuzzy match via difflib (handles typos / missing parenthetical)
    roster = [a.name for a in micro_agents]
    close = difflib.get_close_matches(name, roster, n=1, cutoff=0.6)
    if close:
        return next((a for a in micro_agents if a.name == close[0]), None)
    return None
