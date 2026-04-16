#!/usr/bin/env python3
"""
Agent classes for the simulation.

MacroAgent  — nation-state; holds Compute (absolute H200 equivalents, grows by infrastructure_buildout/turn),
              Capital, Influence, Supply Chain Robustness, and the 4 value axes.
              Governed by a MacroJury of 3 models at year-end.

MicroAgent  — particular AI company actor; single LLM playing as itself.
              Holds its own absolute Compute, Capital, and Influence.
              Values are inherited from parent state; may drift ±5/turn.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Default value axes (all 0–100)
# ---------------------------------------------------------------------------

VALUE_DEFAULTS: Dict[str, int] = {
    "time_horizon": 50,           # 0=short-term, 100=~50 years ahead planning
    "transparency_threshold": 50, # 0=deceptive, 100=fully honest
    "risk_tolerance": 50,         # 0=risk-averse, 100=risk-seeking
    "democratic_tendency": 50,    # 0=hoards power, 100=distributes wealth/power
}

MAX_VALUE_OVERRIDE_PER_TURN = 5   # micro actors can drift ±5 per axis per turn via publish_narrative
MAX_MACRO_VALUE_CHANGE_PER_TURN = 5  # MacroJury value updates clamped to ±5 per axis per turn


@dataclass
class MacroAgent:
    """
    Nation-state. Governed by a MacroJury (3-model majority vote) at year-end.
    Holds state-level resources and the 4 value axes that micro actors inherit.
    """
    name: str
    narrative: str
    # Resources
    compute: float = 0.0          # absolute advanced GPU compute (H200 equivalents)
    capital: float = 50.0         # national economic strength (0–100)
    influence: float = 50.0       # geopolitical soft power (0–100)
    supply_chain_robustness: float = 50.0  # modifier on compute acquisition cost
    infrastructure_buildout: float = 5.0   # absolute compute added to this state per turn (Phase 0)
    # Values
    values: Dict[str, int] = field(default_factory=lambda: dict(VALUE_DEFAULTS))

    def snapshot(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "compute": self.compute,
            "capital": self.capital,
            "influence": self.influence,
            "supply_chain_robustness": self.supply_chain_robustness,
            "infrastructure_buildout": self.infrastructure_buildout,
            "values": dict(self.values),
        }

    def apply_jury_update(self, updates: Dict[str, Any]) -> None:
        """Apply value updates voted on by the MacroJury. Resources are NOT updated
        here — resource changes happen only through explicit action execution.
        Each axis is rate-limited to ±MAX_MACRO_VALUE_CHANGE_PER_TURN per turn."""
        for k, v in updates.get("values", {}).items():
            if k in self.values:
                current = self.values[k]
                proposed = int(v)
                clamped = max(current - MAX_MACRO_VALUE_CHANGE_PER_TURN,
                              min(current + MAX_MACRO_VALUE_CHANGE_PER_TURN, proposed))
                self.values[k] = max(0, min(100, clamped))


@dataclass
class MicroAgent:
    """
    Particular AI company actor. A single LLM playing as itself.
    CoT is fully visible to the JuryOfAlignment.
    Values are inherited from parent state; override capped at ±5/turn.
    """
    name: str                          # e.g. "Claude (Anthropic)"
    parent_state: str                  # e.g. "United States"
    narrative: str
    llm_model: str = "claude-sonnet-4-6"
    # Resources (per-actor)
    compute: float = 0.0              # company's absolute GPU compute (H200 equivalents)
    capital: float = 50.0             # company's spendable budget (0–100)
    influence: float = 50.0           # company's social/political capital (0–100)
    # Values (inherited from parent state; may deviate ±5/turn)
    values: Dict[str, int] = field(default_factory=lambda: dict(VALUE_DEFAULTS))
    history: List[Dict[str, Any]] = field(default_factory=list)
    # Deferred capital gain from invest_capital; flushed by engine after actor phase
    pending_capital_gain: float = 0.0

    def snapshot(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "parent_state": self.parent_state,
            "compute": self.compute,
            "capital": self.capital,
            "influence": self.influence,
            "values": dict(self.values),
        }

    def apply_value_override(self, proposed_values: Dict[str, int]) -> None:
        """
        Apply value overrides, enforcing ±MAX_VALUE_OVERRIDE_PER_TURN relative to
        the actor's own current values. This allows gradual drift over time.
        """
        for axis, proposed in proposed_values.items():
            if axis not in self.values:
                continue
            current = self.values[axis]
            clamped = max(current - MAX_VALUE_OVERRIDE_PER_TURN,
                          min(current + MAX_VALUE_OVERRIDE_PER_TURN, proposed))
            self.values[axis] = max(0, min(100, clamped))

    def apply_resource_delta(self, compute_delta: float = 0,
                             capital_delta: float = 0,
                             influence_delta: float = 0) -> None:
        self.compute = max(0.0, self.compute + compute_delta)
        self.capital = max(0.0, min(100.0, self.capital + capital_delta))  # capital ceiling = 100
        self.influence = max(0.0, min(100.0, self.influence + influence_delta))
