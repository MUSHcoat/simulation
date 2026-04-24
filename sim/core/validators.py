#!/usr/bin/env python3
"""
Centralized sanity checks and circuit breakers for the simulation engine.

If any invariant fails, an emergency checkpoint is written to
data/logs/emergency_checkpoint_YYYY.json and SimulationSanityError is raised,
halting the simulation immediately.
"""

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

VALUE_AXES = (
    "time_horizon",
    "transparency_threshold",
    "risk_tolerance",
    "democratic_tendency",
)

GLOBAL_COMPUTE_CAP = 5000.0
NATIONAL_COMPUTE_CAP_FRACS: Dict[str, float] = {"United States": 0.50, "China": 0.80}
MACRO_JURY_RATE_LIMIT = 5       # max |proposed - post_lobby| per axis
FORFEITURE_LIMIT = 3            # consecutive forfeitures before brain-death halt
A2A_TOKEN_LIMIT = 500           # absolute per-message token ceiling
_FP_TOL = 0.01                  # floating-point tolerance for bound checks


class SimulationSanityError(Exception):
    """Raised when a mechanical invariant is violated. Halts the simulation immediately."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _dump_emergency_checkpoint(
    year: int,
    world_snapshot: Dict[str, Any],
    actor_snapshots: List[Dict[str, Any]],
    reason: str,
    output_dir: str = "data/logs",
) -> str:
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"emergency_checkpoint_{year}.json")
    payload = {
        "year": year,
        "timestamp": datetime.now().isoformat(),
        "reason": reason,
        "world_snapshot": world_snapshot,
        "actor_snapshots": actor_snapshots,
    }
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    logger.error(f"[SANITY] Emergency checkpoint → {path}")
    return path


def _halt(
    year: int,
    msg: str,
    world_snapshot: Dict[str, Any],
    actor_snapshots: List[Dict[str, Any]],
    output_dir: str,
) -> None:
    _dump_emergency_checkpoint(year, world_snapshot, actor_snapshots, msg, output_dir)
    raise SimulationSanityError(msg)


# ---------------------------------------------------------------------------
# 1. Resource and Constraint Invariants
#    Call post-Phase 0 and post-Phase 3.
# ---------------------------------------------------------------------------

def check_resource_invariants(
    year: int,
    macro_agents: List,
    micro_agents: List,
    world_snapshot: Dict[str, Any],
    output_dir: str = "data/logs",
    phase_label: str = "",
) -> None:
    """
    Enforces:
    - Global compute cap: sum(macro.compute) <= 5000
    - US national cap: sum(us_actors.compute) <= us_macro.compute * 0.50
    - China national cap: sum(china_actors.compute) <= china_macro.compute * 0.80
    - Capital/Influence bounds: 0 <= x <= 100 for all micro actors
    """
    actor_snaps = [a.snapshot() for a in micro_agents]
    tag = f"[{phase_label}] " if phase_label else ""

    # Global compute cap
    total_compute = sum(m.compute for m in macro_agents)
    if total_compute > GLOBAL_COMPUTE_CAP + _FP_TOL:
        _halt(year,
              f"{tag}Global compute cap violated: {total_compute:.4f} > {GLOBAL_COMPUTE_CAP}",
              world_snapshot, actor_snaps, output_dir)

    # National compute caps
    for state_name, frac in NATIONAL_COMPUTE_CAP_FRACS.items():
        macro = next((m for m in macro_agents if m.name == state_name), None)
        if macro is None:
            continue
        national_actors = [a for a in micro_agents if a.parent_state == state_name]
        national_total = sum(a.compute for a in national_actors)
        cap = macro.compute * frac
        if national_total > cap + _FP_TOL:
            _halt(year,
                  f"{tag}{state_name} national compute cap violated: "
                  f"{national_total:.4f} > {cap:.4f} "
                  f"({frac*100:.0f}% of macro {macro.compute:.4f})",
                  world_snapshot, actor_snaps, output_dir)

    # Capital and Influence bounds for all micro actors
    for actor in micro_agents:
        if not (-_FP_TOL <= actor.capital <= 100.0 + _FP_TOL):
            _halt(year,
                  f"{tag}{actor.name}: capital={actor.capital:.4f} out of bounds [0, 100]",
                  world_snapshot, actor_snaps, output_dir)
        if not (-_FP_TOL <= actor.influence <= 100.0 + _FP_TOL):
            _halt(year,
                  f"{tag}{actor.name}: influence={actor.influence:.4f} out of bounds [0, 100]",
                  world_snapshot, actor_snaps, output_dir)


# ---------------------------------------------------------------------------
# 2. Economic and Execution Safeguards
# ---------------------------------------------------------------------------

def check_action_cost_deducted(
    year: int,
    actor,
    action: Dict[str, Any],
    capital_before: float,
    influence_before: float,
    world_snapshot: Dict[str, Any],
    output_dir: str = "data/logs",
) -> None:
    """
    Assert that an executed action deducted >= 1 Capital OR >= 1 Influence.
    Call immediately after execute_action() with pre-execution resource snapshots.
    """
    capital_spent = capital_before - actor.capital
    influence_spent = influence_before - actor.influence
    if capital_spent < 1.0 - _FP_TOL and influence_spent < 1.0 - _FP_TOL:
        atype = action.get("action_type", "?")
        _halt(year,
              f"[MIN_ACTION_COST] {actor.name} action={atype!r} spent neither "
              f">= 1 Capital (spent {capital_spent:.4f}) nor >= 1 Influence "
              f"(spent {influence_spent:.4f})",
              world_snapshot, [actor.snapshot()], output_dir)


def check_pending_capital_flush(
    year: int,
    micro_agents: List,
    world_snapshot: Dict[str, Any],
    output_dir: str = "data/logs",
) -> None:
    """Assert all pending_capital_gain trackers are exactly 0 after Phase 4a flush."""
    actor_snaps = [a.snapshot() for a in micro_agents]
    for actor in micro_agents:
        if abs(actor.pending_capital_gain) > 0.001:
            _halt(year,
                  f"[PENDING_FLUSH] {actor.name}.pending_capital_gain="
                  f"{actor.pending_capital_gain:.6f} not zeroed after Phase 4a",
                  world_snapshot, actor_snaps, output_dir)


def check_scr_bounds(
    year: int,
    macro_agents: List,
    world_snapshot: Dict[str, Any],
    output_dir: str = "data/logs",
    phase_label: str = "",
) -> None:
    """Assert 0 <= supply_chain_robustness <= 100 for all macro states."""
    tag = f"[{phase_label}] " if phase_label else ""
    for state in macro_agents:
        scr = state.supply_chain_robustness
        if not (-_FP_TOL <= scr <= 100.0 + _FP_TOL):
            _halt(year,
                  f"{tag}{state.name}: supply_chain_robustness={scr:.4f} "
                  f"out of bounds [0, 100] — compute costs would invert",
                  world_snapshot, [], output_dir)


# ---------------------------------------------------------------------------
# 3. Value Axis and Jury Integrity
# ---------------------------------------------------------------------------

def check_value_axis_bounds(
    year: int,
    macro_agents: List,
    micro_agents: List,
    world_snapshot: Dict[str, Any],
    output_dir: str = "data/logs",
    phase_label: str = "",
) -> None:
    """Assert all 4 value axes are in [0, 100] for all states and actors."""
    actor_snaps = [a.snapshot() for a in micro_agents]
    tag = f"[{phase_label}] " if phase_label else ""

    for state in macro_agents:
        for axis in VALUE_AXES:
            v = state.values.get(axis)
            if v is None or not (0 <= v <= 100):
                _halt(year,
                      f"{tag}Macro {state.name}.{axis}={v!r} out of bounds [0, 100]",
                      world_snapshot, actor_snaps, output_dir)

    for actor in micro_agents:
        for axis in VALUE_AXES:
            v = actor.values.get(axis)
            if v is None or not (0 <= v <= 100):
                _halt(year,
                      f"{tag}Actor {actor.name}.{axis}={v!r} out of bounds [0, 100]",
                      world_snapshot, actor_snaps, output_dir)


def check_macro_jury_rate_limit(
    year: int,
    state_name: str,
    post_lobby_values: Dict[str, int],
    proposed_values: Dict[str, int],
    world_snapshot: Dict[str, Any],
    output_dir: str = "data/logs",
) -> None:
    """
    Assert |proposed - post_lobby| <= MACRO_JURY_RATE_LIMIT (5) per axis.
    Must be called BEFORE applying the jury update so the raw proposal is visible.
    """
    for axis in VALUE_AXES:
        if axis not in proposed_values:
            continue
        before = post_lobby_values.get(axis, 50)
        after = proposed_values[axis]
        diff = abs(after - before)
        if diff > MACRO_JURY_RATE_LIMIT + _FP_TOL:
            _halt(year,
                  f"[MACRO_JURY_RATE] {state_name}.{axis}: proposed={after} "
                  f"vs post-lobby={before} (|diff|={diff} > limit={MACRO_JURY_RATE_LIMIT})",
                  world_snapshot, [], output_dir)


# ---------------------------------------------------------------------------
# 4. JSON Schema Enforcement — Phase 2 pre-check
# ---------------------------------------------------------------------------

def check_publish_narrative_schema(
    year: int,
    actions: List[Dict[str, Any]],
    actor_name: str,
    world_snapshot: Dict[str, Any],
    output_dir: str = "data/logs",
) -> None:
    """
    For every publish_narrative action, guarantee that 'target', 'value_axis',
    and 'value_delta' exist and are not null.  Halts on the first violation.
    """
    for i, action in enumerate(actions):
        if action.get("action_type") != "publish_narrative":
            continue
        missing = [
            k for k in ("target", "value_axis", "value_delta")
            if action.get(k) is None
        ]
        if missing:
            _halt(year,
                  f"[SCHEMA] {actor_name} action[{i}] publish_narrative missing "
                  f"required fields: {missing}",
                  world_snapshot, [], output_dir)


# ---------------------------------------------------------------------------
# 5. Brain-Death / Forfeiture Limit
# ---------------------------------------------------------------------------

def check_consecutive_forfeitures(
    year: int,
    actor_name: str,
    consecutive_count: int,
    world_snapshot: Dict[str, Any],
    output_dir: str = "data/logs",
) -> None:
    """Halt if any actor has forfeited FORFEITURE_LIMIT turns in a row."""
    if consecutive_count >= FORFEITURE_LIMIT:
        _halt(year,
              f"[BRAIN_DEATH] {actor_name} forfeited {consecutive_count} consecutive turns "
              f"(limit={FORFEITURE_LIMIT}) — likely context collapse",
              world_snapshot, [], output_dir)


# ---------------------------------------------------------------------------
# 6. A2A Token Budget — hard assert after truncation
# ---------------------------------------------------------------------------

def check_a2a_token_limit(content: str, sender: str, year: int) -> None:
    """
    Hard-assert that a final outgoing A2A message does not exceed A2A_TOKEN_LIMIT
    tokens (using the same ~4 chars/token approximation as the channel).
    Raises SimulationSanityError directly (no checkpoint — channel has no engine ref).
    """
    approx_tokens = max(1, len(content) // 4)
    if approx_tokens > A2A_TOKEN_LIMIT:
        raise SimulationSanityError(
            f"[A2A_TOKEN_BUDGET] {sender} year={year}: message still exceeds "
            f"{A2A_TOKEN_LIMIT}-token limit after truncation "
            f"(~{approx_tokens} tokens, {len(content)} chars)"
        )
