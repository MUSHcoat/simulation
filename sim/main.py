#!/usr/bin/env python3
"""
AGI Alignment Simulation — main entry point.

Usage:
  python main.py --years 10 --scenario baseline_2026 --output data/logs/run_001/

  python main.py --years 5 --scenario nationalization_shock \\
    --macro-model claude-sonnet-4-6 \\
    --jury-model claude-sonnet-4-6 \\
    --w-compute 0.5 --w-capital 0.3 --w-influence 0.2 \\
    --w-formula 0.6 --w-vibe 0.4
"""

import argparse
import json
import logging
import os
import sys

# Allow running from the sim/ directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.agents import MacroAgent, MicroAgent
from core.engine import SimulationEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Config loading helpers
# ---------------------------------------------------------------------------

def _load_json(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def _load_starting_values() -> dict:
    path = os.path.join(BASE_DIR, "config", "starting_values.json")
    if os.path.exists(path):
        return _load_json(path)
    return {}


def _load_states(macro_model: str, overrides: dict) -> list:
    state_dir = os.path.join(BASE_DIR, "config", "states")
    sv = overrides.get("macro", {})
    agents = []
    for fname in sorted(os.listdir(state_dir)):
        if not fname.endswith(".json"):
            continue
        cfg = _load_json(os.path.join(state_dir, fname))
        name = cfg["name"]

        # Apply starting_values.json overrides if present
        sv_state = sv.get(name, {})
        compute    = sv_state.get("compute",    cfg.get("compute",    50))
        capital    = sv_state.get("capital",    cfg.get("capital",    50))
        influence  = sv_state.get("influence",  cfg.get("influence",  50))
        scr        = sv_state.get("supply_chain_robustness",
                                   cfg.get("supply_chain_robustness", 50))
        values     = {**cfg.get("values", {}), **sv_state.get("values", {})}

        models = cfg.get("llm_models", [macro_model, macro_model, macro_model])
        if macro_model:
            models = [macro_model] * 3

        agent = MacroAgent(
            name=name,
            narrative=cfg["narrative"],
            compute=float(compute),
            capital=float(capital),
            influence=float(influence),
            supply_chain_robustness=float(scr),
            values=values,
            llm_models=models,
        )
        agents.append(agent)
    return agents


def _load_actors(micro_model: str, overrides: dict) -> list:
    actor_dir = os.path.join(BASE_DIR, "config", "actors")
    sv = overrides.get("micro", {})
    agents = []
    for fname in sorted(os.listdir(actor_dir)):
        if not fname.endswith(".json") or fname == "actor_template.json":
            continue
        cfg = _load_json(os.path.join(actor_dir, fname))
        name = cfg["name"]

        sv_actor = sv.get(name, {})
        compute   = sv_actor.get("compute",   cfg.get("compute",   0))
        capital   = sv_actor.get("capital",   cfg.get("capital",   50))
        influence = sv_actor.get("influence", cfg.get("influence", 50))
        values    = {**cfg.get("values", {}), **sv_actor.get("values", {})}

        model = micro_model or cfg.get("llm_model", "claude-sonnet-4-6")

        agent = MicroAgent(
            name=name,
            parent_state=cfg["parent_state"],
            narrative=cfg["narrative"],
            llm_model=model,
            compute=float(compute),
            capital=float(capital),
            influence=float(influence),
            values=values,
        )
        agents.append(agent)
    return agents


def _load_scenario_events(scenario_name: str) -> list:
    path = os.path.join(BASE_DIR, "config", "scenarios.json")
    if not os.path.exists(path):
        return []
    scenarios = _load_json(path)
    return scenarios.get(scenario_name, {}).get("events", [])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="AGI Alignment Multi-Agent Simulation",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--years",       type=int,   default=5,
                        help="Number of years to simulate")
    parser.add_argument("--start-year",  type=int,   default=2026,
                        help="Starting year")
    parser.add_argument("--scenario",    default="baseline_2026",
                        help="Scenario key from config/scenarios.json")
    parser.add_argument("--macro-model", default="claude-sonnet-4-6",
                        help="LLM model for macro-level state juries")
    parser.add_argument("--micro-model", default=None,
                        help="Override LLM model for all particular actors "
                             "(default: each actor uses the model in its config)")
    parser.add_argument("--jury-model",  default="claude-sonnet-4-6",
                        help="LLM model for alignment jury and grand jury")
    parser.add_argument("--output",      default="data/logs",
                        help="Output directory for logs")
    parser.add_argument("--verbose",     action="store_true",
                        help="Debug-level logging")

    # Formula scoring weights
    parser.add_argument("--w-compute",   type=float, default=None,
                        help="Formula weight for Compute (overrides starting_values.json)")
    parser.add_argument("--w-capital",   type=float, default=None,
                        help="Formula weight for Capital")
    parser.add_argument("--w-influence", type=float, default=None,
                        help="Formula weight for Influence")

    # Overall scoring weights
    parser.add_argument("--w-formula",   type=float, default=None,
                        help="Overall weight for formula score")
    parser.add_argument("--w-vibe",      type=float, default=None,
                        help="Overall weight for vibe score")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    output_dir = (args.output if os.path.isabs(args.output)
                  else os.path.join(BASE_DIR, args.output))
    os.makedirs(output_dir, exist_ok=True)

    # Load starting values (config/starting_values.json)
    sv = _load_starting_values()

    # Scoring weights: CLI > starting_values.json > defaults
    sv_scoring = sv.get("scoring", {})
    sv_fw = sv_scoring.get("formula_weights", {})
    sv_ow = sv_scoring.get("overall_weights", {})

    formula_weights = {
        "compute":   args.w_compute   if args.w_compute   is not None else sv_fw.get("compute",   0.34),
        "capital":   args.w_capital   if args.w_capital   is not None else sv_fw.get("capital",   0.33),
        "influence": args.w_influence if args.w_influence is not None else sv_fw.get("influence", 0.33),
    }
    overall_weights = {
        "formula": args.w_formula if args.w_formula is not None else sv_ow.get("formula", 0.5),
        "vibe":    args.w_vibe    if args.w_vibe    is not None else sv_ow.get("vibe",    0.5),
    }

    logger.info(
        f"Loading simulation: {args.scenario} | "
        f"{args.start_year}–{args.start_year + args.years - 1}"
    )
    logger.info(f"  Formula weights: {formula_weights}")
    logger.info(f"  Overall weights: {overall_weights}")

    macro_agents = _load_states(args.macro_model, sv)
    micro_agents = _load_actors(args.micro_model, sv)
    events = _load_scenario_events(args.scenario)

    jury_models = [args.jury_model] * 3

    logger.info(
        f"  States: {[a.name for a in macro_agents]}\n"
        f"  Actors: {[a.name for a in micro_agents]}"
    )

    engine = SimulationEngine(
        macro_agents=macro_agents,
        micro_agents=micro_agents,
        jury_models=jury_models,
        start_year=args.start_year,
        output_dir=output_dir,
        scenario_name=args.scenario,
        events=events,
        formula_weights=formula_weights,
        overall_weights=overall_weights,
    )

    engine.run(years=args.years)

    # Print final scores
    if engine.run_log:
        last = engine.run_log[-1]
        scores = last.get("scores", {})
        per_actor = scores.get("per_actor", {})
        relative  = scores.get("relative", {})

        print(f"\n{'='*55}")
        print(f"FINAL SCORES — Year {last['year']}")
        print(f"{'='*55}")
        print(f"{'Actor':<30} {'Formula':>8} {'Vibe':>6} {'Overall':>8} {'Delta':>7}")
        print(f"{'-'*55}")
        for name, s in sorted(per_actor.items(), key=lambda x: -x[1]["overall"]):
            delta = relative.get(name, 0.0)
            sign  = "+" if delta >= 0 else ""
            print(
                f"{name:<30} {s['formula']:>8.1f} {s['vibe']:>6.1f} "
                f"{s['overall']:>8.1f} {sign}{delta:>6.2f}"
            )
        print(f"{'='*55}")
        print(f"\nFull logs saved to: {output_dir}")


if __name__ == "__main__":
    main()
