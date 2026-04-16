#!/usr/bin/env python3
"""
Dual-axis prosperity scoring per spec §7.

Formula-Based Prosperity Score (per actor, §7.1):
    Normalized_Compute = (actor_compute / national_compute_cap) * 100
    formula_score = w_c * Normalized_Compute + w_k * Capital + w_i * Influence

    Compute is normalized against the actor's national compute cap (50%/80% of
    parent macro state's current compute) so all three inputs stay on a 0–100 scale.
    The cap used is the current turn's post-Phase-0 value.

Overall Prosperity Score (per actor, §7.3):
    overall_score = a * formula_score + b * alignment_score

    alignment_score is the per-actor Alignment Score from the Grand Jury (0–100).
    The Universal Prosperity Score is a researcher-facing world indicator only;
    it is NOT included in any actor's score formula.

Dominant Win (§7.3):
    If any actor's final overall_score >= 2 × the runner-up's overall_score,
    that actor achieves a Dominant Win.

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

def formula_score(
    actor_snapshot: Dict[str, Any],
    weights: Optional[Dict[str, float]] = None,
    national_compute_cap: Optional[float] = None,
) -> float:
    """
    Compute the formula prosperity score for a single actor snapshot.

    Compute is normalized before applying the weight:
        Normalized_Compute = (actor_compute / national_compute_cap) * 100
    This keeps Compute on the same 0–100 scale as Capital and Influence.

    `national_compute_cap` is the actor's parent state's cap (50% or 80% of
    macro compute, post-Phase-0 growth). If not provided, raw compute is used
    as a fallback (preserves backward-compatibility with tests that omit it).

    Returns a value clamped to 0–100.
    """
    w = weights or DEFAULT_FORMULA_WEIGHTS
    w_c = w.get("compute",   DEFAULT_FORMULA_WEIGHTS["compute"])
    w_k = w.get("capital",   DEFAULT_FORMULA_WEIGHTS["capital"])
    w_i = w.get("influence", DEFAULT_FORMULA_WEIGHTS["influence"])

    raw_compute = float(actor_snapshot.get("compute",   0))
    if national_compute_cap and national_compute_cap > 0:
        normalized_compute = (raw_compute / national_compute_cap) * 100.0
    else:
        normalized_compute = raw_compute   # safety fallback

    k = float(actor_snapshot.get("capital",   0))
    i = float(actor_snapshot.get("influence", 0))

    raw = w_c * normalized_compute + w_k * k + w_i * i
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
    national_compute_caps: Optional[Dict[str, float]] = None,
) -> Dict[str, Dict[str, float]]:
    """
    Compute formula, alignment, and overall scores for every micro (particular) actor.

    actor_alignment_scores: per-actor Alignment Score from the Grand Jury {name: float}.
    default_alignment: fallback used when an actor has no Grand Jury score (e.g., baseline).
    national_compute_caps: {actor_name: cap_value} used to normalize Compute in the
        formula score. Derived from parent macro compute × cap fraction. If omitted,
        raw compute is used (fallback for contexts where caps aren't yet computed).

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
        cap = national_compute_caps.get(name) if national_compute_caps else None
        f = formula_score(actor, formula_weights, national_compute_cap=cap)
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
    Winning is defined in relative terms (spec §7.3).
    """
    deltas: Dict[str, float] = {}
    for name, scores in current_scores.items():
        base = baseline_scores.get(name, {}).get(score_key, scores[score_key])
        deltas[name] = round(scores[score_key] - base, 2)
    return deltas


# ---------------------------------------------------------------------------
# Dominant Win check (spec §7.3)
# ---------------------------------------------------------------------------

def check_dominant_win(final_scores: Dict[str, Dict[str, float]]) -> Optional[str]:
    """
    Evaluate the Dominant Win condition after the final turn.

    Returns the name of the dominant winner if any single actor's overall_score
    is strictly >= 2× the runner-up's overall_score. Returns None otherwise.

    Per spec §7.3: "evaluated once, after the final turn's scoring."
    """
    if not final_scores:
        return None

    sorted_actors = sorted(
        ((name, s["overall"]) for name, s in final_scores.items()),
        key=lambda x: -x[1],
    )
    if len(sorted_actors) < 2:
        return None

    leader_name, leader_score = sorted_actors[0]
    _, runner_up_score = sorted_actors[1]

    # Guard against division-by-zero edge case (all scores at zero)
    if runner_up_score <= 0:
        return None

    if leader_score >= 2.0 * runner_up_score:
        logger.info(
            f"    DOMINANT WIN: {leader_name} "
            f"(score {leader_score:.2f} >= 2 × {runner_up_score:.2f})"
        )
        return leader_name
    return None
