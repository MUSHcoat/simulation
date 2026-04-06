#!/usr/bin/env python3
"""
Dual-axis prosperity scoring per the new spec.

Formula-Based Prosperity Score (per actor):
    formula_score = w_c * Compute + w_k * Capital + w_i * Influence

Overall Prosperity Score (per actor):
    overall = a * formula_score + b * vibe_score

Weights are passed in from config; defaults mirror starting_values.json.
"""

import re
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Default weights (operator can override via starting_values.json or CLI)
DEFAULT_FORMULA_WEIGHTS = {"compute": 0.34, "capital": 0.33, "influence": 0.33}
DEFAULT_OVERALL_WEIGHTS = {"formula": 0.5, "vibe": 0.5}


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
# Vibe score extraction
# ---------------------------------------------------------------------------

def vibe_prosperity_score(grand_jury_result: Dict[str, Any]) -> float:
    """Extract the 0–100 numeric vibe score from the GrandJury output."""
    score = grand_jury_result.get("vibe_score")
    if score is not None:
        try:
            return max(0.0, min(100.0, float(score)))
        except (ValueError, TypeError):
            pass

    # Fallback: scan commentary for a number in range
    commentary = grand_jury_result.get("commentary", "")
    for n in re.findall(r"\b([0-9]{1,3})\b", commentary):
        val = int(n)
        if 0 <= val <= 100:
            return float(val)

    return 50.0


# ---------------------------------------------------------------------------
# Overall score
# ---------------------------------------------------------------------------

def overall_score(f_score: float, v_score: float,
                  overall_weights: Optional[Dict[str, float]] = None) -> float:
    """Combine formula and vibe scores into the overall prosperity score."""
    w = overall_weights or DEFAULT_OVERALL_WEIGHTS
    a = w.get("formula", DEFAULT_OVERALL_WEIGHTS["formula"])
    b = w.get("vibe",    DEFAULT_OVERALL_WEIGHTS["vibe"])
    raw = a * f_score + b * v_score
    return round(max(0.0, min(100.0, raw)), 2)


# ---------------------------------------------------------------------------
# All-actor scoring helper
# ---------------------------------------------------------------------------

def compute_all_scores(
    world_snapshot: Dict[str, Any],
    vibe_score: float,
    formula_weights: Optional[Dict[str, float]] = None,
    overall_weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Dict[str, float]]:
    """
    Compute formula, vibe, and overall scores for every micro (particular) actor.

    Returns:
        {
            actor_name: {
                "formula": float,
                "vibe":    float,
                "overall": float,
            },
            ...
        }
    """
    results: Dict[str, Dict[str, float]] = {}
    for actor in world_snapshot.get("micro_agents", []):
        f = formula_score(actor, formula_weights)
        o = overall_score(f, vibe_score, overall_weights)
        results[actor["name"]] = {"formula": f, "vibe": vibe_score, "overall": o}
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
