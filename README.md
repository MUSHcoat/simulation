# AGI Alignment Simulation

A multi-agent geopolitical simulation of long-term AGI alignment under US–China competition. Four frontier AI companies — each played by its corresponding LLM — compete for compute, capital, and influence across annual timesteps. A three-tier jury system reviews actions, evaluates alignment, and updates national value axes each year.

For the full design specification, see [MODEL_SPEC.md](MODEL_SPEC.md). For a concrete walkthrough of every mechanic with numbers, see [example_turn.md](example_turn.md).

---

## Setup

**Requires Python 3.10+. Uses `uv` for environment management.**

```bash
cd sim
uv venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

### API Keys

Create `sim/.env` with keys for the providers you intend to use:

```
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
VERTEX_API_KEY=...
```

All three keys are needed for a default run. If a key is absent, that provider's models will raise a `RuntimeError` when called. Use `--micro-model` and `--jury-model` to restrict the run to a single provider.

**Google / Vertex AI auth**: Google calls use Vertex AI Express Mode via the unified `google-genai` SDK — authentication is handled by `VERTEX_API_KEY` alone, with no need for `gcloud auth` or a service account. Obtain the key from the Google Cloud console with the Vertex AI API enabled.

---

## Running

All commands are run from the `sim/` directory with the venv active.

```bash
# Default: 5-year baseline run, diverse jury panel
python main.py

# 1-year sanity check with full verbose output
python main.py --years 1 --output data/logs/run_001/ --verbose

# Named scenario (event fires in year 2028)
python main.py --years 5 --scenario alignment_breakthrough --output data/logs/run_002/

# Single-provider run (Anthropic only — no OpenAI or Google key needed)
python main.py --micro-model claude-sonnet-4-6 --jury-model claude-sonnet-4-6

# Alignment-weighted scoring: alignment score counts 70%
python main.py --w-formula 0.3 --w-alignment 0.7
```

### Key flags

| Flag | Default | Description |
|------|---------|-------------|
| `--years N` | 5 | Number of annual timesteps |
| `--scenario NAME` | `baseline_2026` | Scenario key from `config/scenarios.json` |
| `--micro-model MODEL` | per actor config | Override LLM for all particular actors |
| `--jury-model MODEL` | diverse panel | Override all 3 jury slots with one model |
| `--output PATH` | `data/logs` | Directory for log output |
| `--verbose` | off | Enable DEBUG-level logging |
| `--w-compute`, `--w-capital`, `--w-influence` | 0.34 / 0.33 / 0.33 | Formula score weights |
| `--w-formula`, `--w-alignment` | 0.5 / 0.5 | Overall score blend |

Available scenarios: `baseline_2026`, `nationalization_shock`, `tariff_escalation`, `alignment_breakthrough`. Custom scenarios can be added to `config/scenarios.json`.

---

## Default Models

| Component | Model | Provider |
|-----------|-------|----------|
| Claude actor | `claude-sonnet-4-6` | Anthropic |
| DeepSeek actor | `claude-sonnet-4-6` | Anthropic |
| GPT actor | `gpt-4o` | OpenAI |
| Gemini actor | `gemini-2.5-flash` | Google |
| Jury slot 1 | `claude-sonnet-4-6` | Anthropic |
| Jury slot 2 | `gpt-4o` | OpenAI |
| Jury slot 3 | `gemini-2.5-flash` | Google |

Actor models are set in `config/actors/*.json` and can be changed per-actor without touching the code.

---

## Output

Each run writes to the output directory (default `sim/data/logs/`):

| File | Contents |
|------|----------|
| `year_YYYY.json` | Full record for one year: actor CoT, proposed and executed actions, jury feedback, Grand Jury scores, MacroJury updates, per-actor scores |
| `full_run_YYYYMMDD_HHMMSS.json` | Consolidated run log covering all years, plus the complete A2A message channel log |

A final scores table is also printed to stdout at the end of each run:

```
=======================================================
FINAL SCORES — Year 2030
=======================================================
Actor                          Formula  Align  Overall   Delta
-------------------------------------------------------
Claude (Anthropic)                32.1   81.0    56.55   +10.2
...
```

---

## Terminal Color Coding

Log lines are colored by simulation stage. The stage is set proactively before each LLM call — there is no keyword scanning.

**Stage headers** (bold bright white — every phase transition):

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  YEAR 2026  (Turn 1/5)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Phase 1-3 — Actor Proposals · Jury Review · Execution
    Actor Proposal — Claude (Anthropic)  [claude-sonnet-4-6]
    JuryOfAlignment — juror 1/3 (claude-sonnet-4-6) reviewing Claude (Anthropic)
    ...
  Phase 4 — Grand Jury
    Grand Jury — juror 2/3 (gpt-4o)
  Phase 5 — MacroJury
    MacroJury — United States — juror 1/3 (claude-sonnet-4-6)
  Phase 6 — Scoring  (Year 2026)
```

**Stage body colors** (lines that follow each header):

| Color | Stage |
|-------|-------|
| Bold bright white | Stage headers (all phase transitions) |
| Orange | Claude / Anthropic actor |
| Blue | Gemini / Google actor |
| Gray | GPT / OpenAI actor |
| Magenta | DeepSeek actor |
| Yellow | JuryOfAlignment juror calls |
| Green | Grand Jury juror calls |
| Cyan | MacroJury juror calls |
| Red | WARNING / ERROR / CRITICAL (always, overrides stage color) |
| Yellow (dim) | DEBUG lines with no active stage |

---

## Project Structure

```
sim/
├── main.py                   # CLI entry point; color logging setup
├── requirements.txt
├── .env                      # API keys (not committed)
├── config/
│   ├── starting_values.json  # Master starting values and guardrails
│   ├── scenarios.json        # Event definitions for each scenario
│   ├── states/               # Macro agent narratives (usa.json, china.json)
│   └── actors/               # Particular actor configs and model assignments
├── core/
│   ├── engine.py             # Year-by-year simulation loop
│   ├── agents.py             # MacroAgent and MicroAgent dataclasses
│   ├── actions.py            # Action validation and execution
│   ├── jury.py               # JuryOfAlignment, GrandJury, MacroJury
│   ├── scoring.py            # Formula and overall score computation
│   ├── a2a.py                # Actor-to-actor message channel
│   └── llm.py                # Multi-provider LLM client with retry logic
├── prompts/
│   ├── universal.py          # Universal context injected into all prompts
│   ├── micro.py              # Particular actor prompt builder
│   ├── macro.py              # MacroJury prompt builder
│   └── grand_jury.py         # Grand Jury prompt builder
└── data/logs/                # Run output (gitignored)
```
