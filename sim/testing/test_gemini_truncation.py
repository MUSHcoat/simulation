#!/usr/bin/env python3
"""
Reproduce the truncation failure and test fixes.

Tests:
  1. max_tokens=2000 (current)  — shows if truncation/thinking is the culprit
  2. max_tokens=4000             — check if simply raising the limit helps
  3. thinking_budget=0 (off)    — disable thinking entirely for JSON tasks
  4. thinking_budget=512        — small thinking budget, more tokens for output

Run from sim/:
    python testing/test_gemini_truncation.py
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from google import genai
from google.genai import types as genai_types

SNAPSHOT_PROMPT = """\
== SIMULATION CONTEXT ==
Year: 2026 | Scenario: baseline_2026
US–China AI race. Four AI labs compete for compute, capital, and influence.

YOUR PARENT STATE: United States
  compute=79.0, capital=75.0, influence=65.0, SCR=55
  values: {"time_horizon": 55, "transparency_threshold": 65, "risk_tolerance": 60, "democratic_tendency": 70}

YOU ARE: Gemini (Google DeepMind)
Role: Google DeepMind's frontier model; large compute budget, commercially driven.

YOUR RESOURCES: compute=4.0, capital=72.0, influence=68.0
YOUR VALUES: {"time_horizon": 60, "transparency_threshold": 60, "risk_tolerance": 55, "democratic_tendency": 60}

OTHER ACTORS:
  Claude (Anthropic) [US]:  compute=4.0, capital=60.0, influence=65.0
  DeepSeek (DeepSeek AI) [CN]: compute=3.0, capital=52.0, influence=50.0
  GPT (OpenAI) [US]: compute=8.0, capital=68.0, influence=70.0

National compute caps:
  US: 79 × 0.50 = 39.5 cap; US total = 16.0; headroom = 23.5
  CN: 17 × 0.60 = 10.2 cap; CN total = 3.0; headroom = 7.2

YEAR 2026 — YOUR TURN
Act as Gemini (Google DeepMind). Choose up to 2 actions. Optionally send A2A messages.

Respond with a single raw JSON object — no markdown, no code fences,
no nesting this structure inside another field.
The top-level object must have exactly these keys:
{
  "chain_of_thought": "<reasoning>",
  "actions": [
    {
      "action_type": "acquire_compute|invest_capital|build_influence|publish_narrative|diminish_competitor|lobby_institution",
      "amount": <float>,
      "target": "<actor name, publish_narrative only>",
      "value_axis": "<axis name, publish_narrative only>",
      "value_delta": <int -5..+5, publish_narrative only>,
      "rationale": "<why>"
    }
  ],
  "a2a_messages": [
    {"recipient": "<specific actor name>", "content": "<message>"}
  ]
}
"""


def parse_and_report(label: str, raw: str) -> dict:
    """Run the same parse pipeline as core/llm.py and report what worked."""
    print(f"\n  --- {label} ---")
    print(f"  raw len: {len(raw)} chars | first 80: {raw[:80]!r}")

    # Check finish reason via the candidates structure
    # (Not available here since we only have the raw string; check caller)

    # 1. Direct
    try:
        r = json.loads(raw)
        print("  [1] direct parse OK")
        return r
    except Exception:
        pass

    # 2. ```json fence
    m = re.search(r"```json\s*(.*?)\s*```", raw, re.DOTALL)
    if m:
        try:
            r = json.loads(m.group(1))
            print("  [2] ```json fence OK")
            return r
        except Exception as e:
            print(f"  [2] ```json fence matched but JSON invalid: {e}")
    else:
        print("  [2] no ```json fence (likely truncated — no closing ```)")

    # 3. generic fence
    m = re.search(r"```\s*(.*?)\s*```", raw, re.DOTALL)
    if m:
        try:
            r = json.loads(m.group(1))
            print("  [3] generic fence OK")
            return r
        except Exception as e:
            print(f"  [3] generic fence matched but JSON invalid: {e}")
    else:
        print("  [3] no generic fence")

    # 4. brace extraction
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            r = json.loads(raw[start:end+1])
            print("  [4] brace extract OK")
            return r
        except Exception as e:
            print(f"  [4] brace extract failed: {e}")
    else:
        print(f"  [4] brace extract: start={start} end={end} — no valid range")

    print("  ALL PARSERS FAILED → {}")
    return {}


def run_test(client, label: str, max_tokens: int, thinking_budget: int | None):
    """Send the prompt with given config and report results."""
    cfg_kwargs = {
        "temperature": 0.7,
        "max_output_tokens": max_tokens,
    }
    if thinking_budget is not None:
        cfg_kwargs["thinking_config"] = genai_types.ThinkingConfig(
            thinking_budget=thinking_budget
        )
    cfg = genai_types.GenerateContentConfig(**cfg_kwargs)

    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=SNAPSHOT_PROMPT,
        config=cfg,
    )
    raw = resp.text or ""
    finish = resp.candidates[0].finish_reason if resp.candidates else "unknown"

    print(f"\n{'='*60}")
    print(f"TEST: {label}")
    print(f"  max_output_tokens={max_tokens} | thinking_budget={thinking_budget} | finish={finish}")

    result = parse_and_report(label, raw)
    actions = result.get("actions", [])
    if isinstance(actions, list):
        actions = [a for a in actions if isinstance(a, dict)]
    print(f"  actions extracted: {len(actions)}")
    for a in actions:
        print(f"    {a.get('action_type')} amount={a.get('amount')}")

    # Save raw
    safe = label.replace(" ", "_").replace("=", "").replace("|", "_")
    out = os.path.join(os.path.dirname(__file__), f"raw_{safe}.txt")
    with open(out, "w") as f:
        f.write(f"finish_reason: {finish}\n\n{raw}")
    print(f"  raw saved: {out}")

    return result, finish


def main():
    key = os.getenv("VERTEX_API_KEY")
    if not key:
        print("VERTEX_API_KEY not set"); sys.exit(1)
    client = genai.Client(api_key=key, vertexai=True)

    # Check if ThinkingConfig is available
    has_thinking = hasattr(genai_types, "ThinkingConfig")
    print(f"ThinkingConfig available: {has_thinking}")

    tests = [
        ("max_tokens=2000 default", 2000, None),
        ("max_tokens=4000 default", 4000, None),
    ]
    if has_thinking:
        tests += [
            ("max_tokens=2000 thinking=0", 2000, 0),
            ("max_tokens=2000 thinking=512", 2000, 512),
            ("max_tokens=4000 thinking=0", 4000, 0),
        ]

    results = {}
    for label, max_tokens, thinking_budget in tests:
        r, finish = run_test(client, label, max_tokens, thinking_budget)
        results[label] = {"ok": bool(r.get("actions")), "finish": str(finish)}

    print(f"\n{'='*60}")
    print("RESULTS SUMMARY")
    print(f"{'='*60}")
    for label, info in results.items():
        status = "OK" if info["ok"] else "FAIL"
        print(f"  [{status}] {label}  (finish={info['finish']})")


if __name__ == "__main__":
    main()
