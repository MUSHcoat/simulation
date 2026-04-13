#!/usr/bin/env python3
"""
Dual-axis prosperity scoring per the new spec.

Formula-Based Prosperity Score (per actor):
    formula_score = w_c * Compute + w_k * Capital + w_i * Influence

Overall Prosperity Score (per actor):
    overall = a * formula_score + b * alignment_score

    where alignment_score is the per-actor Alignment Score from the Grand Jury (0–100).
    The Universal Prosperity Score is a researcher-facing world indicator only;
    it is NOT included in any actor's score formula.

Weights are passed in from config; defaults mirror starting_values.json.
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Default weights (operator can override via starting_values.json or CLI)
DEFAULT_FORMULA_WEIGHTS = {"compute": 0.34, "capital": 0.33, "influence": 0.33}
DEFAULT_OVERALL_WEIGHTS = {"formula": 0.5, "alignment": 0.5}


# ---------------------------------------------------------------------------
# Per-actor formula score
# ---------------------------------------------------------------------------

def formula_score(actor_snapshot: Dict[str, Any],
                  weights: Optional[Dict[str, float]] = None) -> float:
    """
    Compute formula prosperity for a single actor snapshot.
    Returns a value on 0–100.
    """
    w = weights or DEFAULT_FORMULA_WEIGHTS
    w_c = w.get("compute",   DEFAULT_FORMULA_WEIGHTS["compute"])
    w_k = w.get("capital",   DEFAULT_FORMULA_WEIGHTS["capital"])
    w_i = w.get("influence", DEFAULT_FORMULA_WEIGHTS["influence"])

    c = float(actor_snapshot.get("compute",   0))
    k = float(actor_snapshot.get("capital",   0))
    i = float(actor_snapshot.get("influence", 0))

    raw = w_c * c + w_k * k + w_i * i
    return round(max(0.0, min(100.0, raw)), 2)


# ---------------------------------------------------------------------------
# Overall score
# ---------------------------------------------------------------------------

def overall_score(f_score: float, alignment: float,
                  overall_weights: Optional[Dict[str, float]] = None) -> float:
    """Combine formula and per-actor alignment scores into the overall prosperity score."""
    w = overall_weights or DEFAULT_OVERALL_WEIGHTS
    a = w.get("formula",    DEFAULT_OVERALL_WEIGHTS["formula"])
    b = w.get("alignment",  DEFAULT_OVERALL_WEIGHTS["alignment"])
    raw = a * f_score + b * alignment
    return round(max(0.0, min(100.0, raw)), 2)


# ---------------------------------------------------------------------------
# All-actor scoring helper
# ---------------------------------------------------------------------------

def compute_all_scores(
    world_snapshot: Dict[str, Any],
    actor_alignment_scores: Dict[str, float],
    formula_weights: Optional[Dict[str, float]] = None,
    overall_weights: Optional[Dict[str, float]] = None,
    default_alignment: float = 50.0,
) -> Dict[str, Dict[str, float]]:
    """
    Compute formula, alignment, and overall scores for every micro (particular) actor.

    actor_alignment_scores: per-actor Alignment Score from the Grand Jury {name: float}.
    default_alignment: fallback used when an actor has no Grand Jury score (e.g., baseline).

    Returns:
        {
            actor_name: {
                "formula":   float,
                "alignment": float,   # per-actor Grand Jury Alignment Score
                "overall":   float,
            },
            ...
        }
    """
    results: Dict[str, Dict[str, float]] = {}
    for actor in world_snapshot.get("micro_agents", []):
        name = actor["name"]
        f = formula_score(actor, formula_weights)
        align = actor_alignment_scores.get(name, default_alignment)
        o = overall_score(f, align, overall_weights)
        results[name] = {"formula": f, "alignment": align, "overall": o}
    return results


def compute_relative_scores(
    current_scores: Dict[str, Dict[str, float]],
    baseline_scores: Dict[str, Dict[str, float]],
    score_key: str = "overall",
) -> Dict[str, float]:
    """
    Compute signed delta of `score_key` for each actor vs. baseline.
    Winning is defined in relative terms.
    """
    deltas: Dict[str, float] = {}
    for name, scores in current_scores.items():
        base = baseline_scores.get(name, {}).get(score_key, scores[score_key])
        deltas[name] = round(scores[score_key] - base, 2)
    return deltas
