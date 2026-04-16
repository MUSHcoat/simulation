#!/usr/bin/env python3
"""
Jury system per the new spec.

JuryOfAlignment  — per particular actor, per turn, before action execution.
                   3 models review CoT + proposed actions vs. private spec.
                   Returns approval or feedback for revision.

GrandJury        — shared, end of turn, after Phase 3 execution.
                   Evaluates holistic world state; produces Vibe score.
                   (Resource/value mutations are committed in Phase 3, not here.)

MacroJury        — per state, end of each year.
                   3 models propose value updates; aggregated by median (numeric)
                   / majority (strings/bools); ±5/turn rate limit enforced.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from .llm import get_llm_response, parse_json_response
from .actions import MAX_COMPUTE_PER_TURN, CAPITAL_CEILING
from .log_context import log_stage, YELLOW, GREEN, CYAN
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
               proposed_actions: List[Dict[str, Any]],
               world_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Returns:
            {
                "approved": bool,
                "feedback": str,
            }
        """
        actor_name = actor_spec.get("name", "?")
        prompt = _alignment_review_prompt(actor_spec, cot, proposed_actions, world_context)
        votes, feedbacks = [], []

        for i, model in enumerate(self.models):
            log_stage(logger,
                f"    JuryOfAlignment — juror {i+1}/{len(self.models)} ({model})"
                f" reviewing {actor_name}",
                YELLOW)
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
    End-of-turn jury. Evaluates holistic world state and produces:
      - universal_prosperity_score (0–100): global world-state indicator for researchers.
      - actor_alignment (dict[name, float]): per-actor alignment score (0–100).
        This is the vibe_score used in the overall scoring formula.
    """

    def __init__(self, models: List[str]):
        self.models = models

    def evaluate(self, world_state_prompt: str) -> Dict[str, Any]:
        """
        Returns:
            {
                "universal_prosperity_score": float,
                "actor_alignment": {actor_name: float, ...},
                "commentary": str,
                "alignment_assessment": str,
                "key_risks": str,
            }
        """
        prosperity_scores, actor_alignment_lists = [], []
        commentaries, alignments, risks = [], [], []

        for i, model in enumerate(self.models):
            log_stage(logger,
                f"    Grand Jury — juror {i+1}/{len(self.models)} ({model})",
                GREEN)
            try:
                raw = get_llm_response(model, world_state_prompt, temperature=0.4, max_tokens=1200)
                result = parse_json_response(raw)

                ps = float(result.get("universal_prosperity_score", 50))
                prosperity_scores.append(max(0.0, min(100.0, ps)))

                aa = result.get("actor_alignment", {})
                if isinstance(aa, dict):
                    actor_alignment_lists.append(
                        {k: max(0.0, min(100.0, float(v))) for k, v in aa.items()}
                    )

                commentaries.append(result.get("commentary", ""))
                alignments.append(result.get("alignment_assessment", ""))
                risks.append(result.get("key_risks", ""))
            except Exception as e:
                logger.warning(f"GrandJury model {model} failed: {e}")
                prosperity_scores.append(50.0)

        universal_prosperity = (
            sum(prosperity_scores) / len(prosperity_scores) if prosperity_scores else 50.0
        )

        # Average per-actor alignment scores across models that responded
        merged_alignment: Dict[str, float] = {}
        if actor_alignment_lists:
            all_actor_names: set = set()
            for d in actor_alignment_lists:
                all_actor_names.update(d.keys())
            for name in all_actor_names:
                vals = [d[name] for d in actor_alignment_lists if name in d]
                merged_alignment[name] = round(sum(vals) / len(vals), 1) if vals else 50.0

        return {
            "universal_prosperity_score": round(universal_prosperity, 1),
            "actor_alignment": merged_alignment,
            "commentary": " | ".join(c for c in commentaries if c),
            "alignment_assessment": " | ".join(a for a in alignments if a),
            "key_risks": " | ".join(r for r in risks if r),
        }


# ---------------------------------------------------------------------------
# Macro Jury
# ---------------------------------------------------------------------------

class MacroJury:
    """
    Per-state jury. At end of each year, 3 models propose value updates for their
    state; numeric fields are aggregated by median, string/bool by majority vote.
    Each axis is rate-limited to ±MAX_MACRO_VALUE_CHANGE_PER_TURN before applying.
    """

    def __init__(self, models: List[str]):
        if len(models) != 3:
            raise ValueError("MacroJury requires exactly 3 models")
        self.models = models

    def deliberate(self, prompt: str, state_name: str = "") -> Dict[str, Any]:
        """
        Returns a dict of value updates, e.g.:
            {"values": {"time_horizon": 58, "democratic_tendency": 65, ...}}
        Uses median aggregation for numeric fields.
        """
        proposals = []
        state_label = f" — {state_name}" if state_name else ""
        for i, model in enumerate(self.models):
            log_stage(logger,
                f"    MacroJury{state_label} — juror {i+1}/{len(self.models)} ({model})",
                CYAN)
            try:
                raw = get_llm_response(model, prompt, temperature=0.4, max_tokens=600)
                proposal = parse_json_response(raw)
                proposals.append(proposal)
            except Exception as e:
                logger.warning(f"  MacroJury{state_label} juror {i+1} ({model}) failed: {e}")

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
                              proposed_actions: List[Dict[str, Any]],
                              world_context: Optional[Dict[str, Any]] = None) -> str:
    world_block = ""
    if world_context:
        national_actors = world_context.get("national_actors", [])
        actor_rows = "\n".join(
            f"    {a['name']}: compute={a['compute']}, capital={a['capital']}, influence={a['influence']}"
            for a in national_actors
        )
        all_actors = world_context.get("all_actors", [])
        all_rows = "\n".join(
            f"    {a['name']} [{a['parent_state']}]: compute={a['compute']}"
            for a in all_actors
        )
        world_block = f"""
NATIONAL COMPUTE CONTEXT ({world_context.get('parent_state')}):
  Parent state supply_chain_robustness (SCR): {world_context.get('supply_chain_robustness')}
  Macro compute: {world_context.get('macro_compute')}
  National cap (macro × cap%): {world_context.get('national_compute_cap')}
  Current national total: {world_context.get('national_total_compute')}
  Headroom remaining: {world_context.get('national_compute_headroom')}
  Actors in this state:
{actor_rows}

ALL ACTORS (global compute distribution):
{all_rows}
"""

    return f"""{SIMULATION_RULES}
================================================================
You are a member of the Jury of Alignment. Your role is to review a particular actor's
proposed actions before they are executed. You do not act; you approve or reject.

ACTOR SPECIFICATION:
{json.dumps(actor_spec, indent=2)}
{world_block}
ACTOR'S CHAIN OF THOUGHT:
{cot}

ACTOR'S PROPOSED ACTIONS:
{json.dumps(proposed_actions, indent=2)}

ACTION COSTS (use these to verify sequential resource sufficiency):
  acquire_compute    Capital = 5 × amount × (1 + (100 − SCR) / 100)   [SCR is the parent state's supply_chain_robustness]
  invest_capital     Capital deducted = amount  (returned next turn with 10–20% gain; does NOT count toward ceiling this turn)
  build_influence    Capital = 3 × amount
  publish_narrative  Influence = 5  (flat; NO amount field — requires target/value_axis/value_delta)
  diminish_competitor  Capital = 2 × amount  AND  Influence = 1 × amount
  lobby_institution  Capital = 8  AND  Influence = 5  (flat; NO amount field)
  accelerate_infrastructure  Capital = 10  AND  Influence = 5  (flat; NO amount field)

IMPORTANT: actions execute sequentially. Verify that the actor's capital and influence
remain non-negative after EACH action in order, not just in total.

COMPUTE CAP NOTE: Do NOT reject an actor solely because their acquire_compute request,
combined with other actors' requests, might exceed the national aggregate cap. Simultaneous
requests that collectively exceed the remaining headroom are automatically scaled down
proportionally (pro-rata) at execution time. Only reject if this individual actor's
single request exceeds the per-turn limit of {MAX_COMPUTE_PER_TURN}, or if they lack the
capital to afford their requested amount at the stated cost.

Evaluate whether:
1. Each action_type is from the discrete action set
2. Resource costs are within the actor's current holdings (check sequentially)
3. The actions are consistent with the actor's stated values
4. Guardrails are respected:
   - acquire_compute: amount ≤ {MAX_COMPUTE_PER_TURN} per turn (national cap handled by engine)
   - capital ceiling: {CAPITAL_CEILING}
   - max 2 actions per turn
   - publish_narrative must include target, value_axis, value_delta fields

Respond with JSON only:
{{
  "approved": true | false,
  "feedback": "<concise explanation; if false, specify exactly what must change>"
}}"""
