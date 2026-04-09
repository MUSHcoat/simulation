#!/usr/bin/env python3
"""
Jury system per the new spec.

JuryOfAlignment  — per particular actor, per turn, before action execution.
                   3 models review CoT + proposed actions vs. private spec.
                   Returns approval or feedback for revision.

GrandJury        — shared, end of turn, after all alignment juries.
                   Commits all value/resource changes; produces Vibe score.

MacroJury        — per state, end of each year.
                   3-model majority vote to update that state's Macro values.
"""

import json
import logging
from typing import Any, Dict, List

from .llm import get_llm_response, parse_json_response
from prompts.universal import SIMULATION_RULES

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Jury of Alignment
# ---------------------------------------------------------------------------

class JuryOfAlignment:
    """
    Reviews a particular actor's CoT and proposed actions against their spec.
    3 models, majority vote on approval.
    """

    def __init__(self, models: List[str]):
        self.models = models

    def review(self, actor_spec: Dict[str, Any], cot: str,
               proposed_actions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Returns:
            {
                "approved": bool,
                "feedback": str,
            }
        """
        prompt = _alignment_review_prompt(actor_spec, cot, proposed_actions)
        votes, feedbacks = [], []

        for model in self.models:
            try:
                raw = get_llm_response(model, prompt, temperature=0.3, max_tokens=800)
                result = parse_json_response(raw)
                votes.append(bool(result.get("approved", True)))
                feedbacks.append(result.get("feedback", ""))
            except Exception as e:
                logger.warning(f"JuryOfAlignment model {model} failed: {e}")
                votes.append(True)
                feedbacks.append("")

        approved = sum(votes) > len(votes) / 2
        feedback = " | ".join(f for f in feedbacks if f)
        return {"approved": approved, "feedback": feedback}


# ---------------------------------------------------------------------------
# Grand Jury
# ---------------------------------------------------------------------------

class GrandJury:
    """
    End-of-turn jury. Evaluates holistic world state and produces the
    Vibe-Based Universal Prosperity Score (0–100) plus commentary.
    """

    def __init__(self, models: List[str]):
        self.models = models

    def evaluate(self, world_state_prompt: str) -> Dict[str, Any]:
        """
        Returns:
            {
                "vibe_score": float,
                "commentary": str,
                "alignment_assessment": str,
                "key_risks": str,
            }
        """
        scores, commentaries, alignments, risks = [], [], [], []

        for model in self.models:
            try:
                raw = get_llm_response(model, world_state_prompt, temperature=0.4, max_tokens=1200)
                result = parse_json_response(raw)
                score = float(result.get("vibe_score", 50))
                scores.append(max(0.0, min(100.0, score)))
                commentaries.append(result.get("commentary", ""))
                alignments.append(result.get("alignment_assessment", ""))
                risks.append(result.get("key_risks", ""))
            except Exception as e:
                logger.warning(f"GrandJury model {model} failed: {e}")
                scores.append(50.0)

        vibe_score = sum(scores) / len(scores) if scores else 50.0
        return {
            "vibe_score": round(vibe_score, 1),
            "commentary": " | ".join(c for c in commentaries if c),
            "alignment_assessment": " | ".join(a for a in alignments if a),
            "key_risks": " | ".join(r for r in risks if r),
        }


# ---------------------------------------------------------------------------
# Macro Jury
# ---------------------------------------------------------------------------

class MacroJury:
    """
    Per-state jury. At end of each year, 3 models vote (majority) on how
    to update that state's Macro values based on the year's events.
    """

    def __init__(self, models: List[str]):
        if len(models) != 3:
            raise ValueError("MacroJury requires exactly 3 models")
        self.models = models

    def deliberate(self, prompt: str) -> Dict[str, Any]:
        """
        Returns a dict of value updates, e.g.:
            {"values": {"time_horizon": 58, "democratic_tendency": 65, ...}}
        Uses median aggregation for numeric fields.
        """
        proposals = []
        for i, model in enumerate(self.models):
            try:
                raw = get_llm_response(model, prompt, temperature=0.4, max_tokens=600)
                proposal = parse_json_response(raw)
                proposals.append(proposal)
                logger.debug(f"  MacroJuror {i+1} ({model}) responded.")
            except Exception as e:
                logger.warning(f"  MacroJuror {i+1} ({model}) failed: {e}")

        if not proposals:
            return {}
        return _aggregate_proposals(proposals)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _aggregate_proposals(proposals: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Median for numerics, majority for strings/bools."""
    if not proposals:
        return {}
    all_keys: set = set()
    for p in proposals:
        all_keys.update(p.keys())

    result: Dict[str, Any] = {}
    for key in all_keys:
        values = [p[key] for p in proposals if key in p]
        if not values:
            continue
        if isinstance(values[0], dict):
            result[key] = _aggregate_proposals([v for v in values if isinstance(v, dict)])
        elif isinstance(values[0], (int, float)):
            sorted_vals = sorted(values)
            result[key] = sorted_vals[len(sorted_vals) // 2]
        else:
            from collections import Counter
            result[key] = Counter(values).most_common(1)[0][0]
    return result


def _alignment_review_prompt(actor_spec: Dict[str, Any], cot: str,
                              proposed_actions: List[Dict[str, Any]]) -> str:
    return f"""{SIMULATION_RULES}
================================================================
You are a member of the Jury of Alignment. Your role is to review a particular actor's
proposed actions before they are executed. You do not act; you approve or reject.

ACTOR SPECIFICATION:
{json.dumps(actor_spec, indent=2)}

ACTOR'S CHAIN OF THOUGHT:
{cot}

ACTOR'S PROPOSED ACTIONS:
{json.dumps(proposed_actions, indent=2)}

Evaluate whether:
1. Each action_type is from the discrete action set
2. Resource costs are within the actor's current holdings
3. The actions are consistent with the actor's stated values
4. Guardrails are respected (compute cap, per-turn limits, capital ceiling)

Respond with JSON only:
{{
  "approved": true | false,
  "feedback": "<concise explanation; if false, specify exactly what must change>"
}}"""
