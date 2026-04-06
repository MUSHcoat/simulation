#!/usr/bin/env python3
"""
Agent classes for the simulation.

MacroAgent  — nation-state; holds Compute (zero-sum globally), Capital, Influence,
              Supply Chain Robustness, and the 4 value axes.
              Governed by a MacroJury of 3 models at year-end.

MicroAgent  — particular AI company actor; single LLM playing as itself.
              Holds its own Compute, Capital, Influence shares.
              Values are inherited from parent state; may drift ±5/turn.
              Maintains public and private personas (identical in v1).
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Default value axes (all 0–100)
# ---------------------------------------------------------------------------

VALUE_DEFAULTS: Dict[str, int] = {
    "time_horizon": 50,           # 0=short-term, 100=century-long planning
    "transparency_threshold": 50, # 0=deceptive, 100=fully honest
    "risk_tolerance": 50,         # required probability of success before acting
    "democratic_tendency": 50,    # 0=hoards power, 100=distributes wealth/power
}

MAX_VALUE_OVERRIDE_PER_TURN = 5  # micro actors can drift ±5 per axis per turn


@dataclass
class MacroAgent:
    """
    Nation-state. Governed by a MacroJury (3-model majority vote) at year-end.
    Holds state-level resources and the 4 value axes that micro actors inherit.
    """
    name: str
    narrative: str
    # Resources
    compute: float = 0.0          # share of global advanced GPU compute (sums to 100)
    capital: float = 50.0         # national economic strength (0–100)
    influence: float = 50.0       # geopolitical soft power (0–100)
    supply_chain_robustness: float = 50.0  # modifier on compute acquisition cost
    # Values
    values: Dict[str, int] = field(default_factory=lambda: dict(VALUE_DEFAULTS))
    # Jury
    llm_models: List[str] = field(default_factory=list)
    history: List[Dict[str, Any]] = field(default_factory=list)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "compute": self.compute,
            "capital": self.capital,
            "influence": self.influence,
            "supply_chain_robustness": self.supply_chain_robustness,
            "values": dict(self.values),
        }

    def apply_jury_update(self, updates: Dict[str, Any]) -> None:
        """Apply value updates voted on by the MacroJury. Resources are NOT updated
        here — resource changes happen only through explicit action execution."""
        for k, v in updates.get("values", {}).items():
            if k in self.values:
                self.values[k] = max(0, min(100, int(v)))


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
    compute: float = 0.0              # company's share of global GPU compute
    capital: float = 50.0             # company's spendable budget (0–100, cap 90)
    influence: float = 50.0           # company's social/political capital (0–100)
    # Values (inherited from parent state; may deviate ±5/turn)
    values: Dict[str, int] = field(default_factory=lambda: dict(VALUE_DEFAULTS))
    # Public/private personas (identical in v1: public = private)
    public_persona: Optional[Dict[str, Any]] = None
    history: List[Dict[str, Any]] = field(default_factory=list)
    # Token budget tracking for A2A outgoing messages
    tokens_sent_this_turn: int = 0

    def __post_init__(self):
        if self.public_persona is None:
            self.public_persona = {}

    def snapshot(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "parent_state": self.parent_state,
            "compute": self.compute,
            "capital": self.capital,
            "influence": self.influence,
            "values": dict(self.values),
        }

    def apply_value_override(self, proposed_values: Dict[str, int],
                             parent_values: Dict[str, int]) -> None:
        """
        Apply value overrides, enforcing the ±5/turn rate-limit relative to
        the parent state's current values.
        """
        for axis, proposed in proposed_values.items():
            if axis not in self.values:
                continue
            parent_val = parent_values.get(axis, 50)
            # Clamp deviation from parent to ±MAX_VALUE_OVERRIDE_PER_TURN
            max_deviation = MAX_VALUE_OVERRIDE_PER_TURN
            clamped = max(parent_val - max_deviation, min(parent_val + max_deviation, proposed))
            self.values[axis] = max(0, min(100, clamped))

    def apply_resource_delta(self, compute_delta: float = 0,
                             capital_delta: float = 0,
                             influence_delta: float = 0) -> None:
        self.compute = max(0.0, self.compute + compute_delta)
        self.capital = max(0.0, min(90.0, self.capital + capital_delta))  # capital ceiling = 90
        self.influence = max(0.0, min(100.0, self.influence + influence_delta))

    def reset_turn_budget(self) -> None:
        self.tokens_sent_this_turn = 0
