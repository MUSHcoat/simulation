#!/usr/bin/env python3
"""
Probe Gemini's exact response format for the micro-action prompt.

Uses the real snapshot from run_004/year_2026.json to reconstruct the
prompt that Gemini received, sends it, and prints every layer of the
response so we can understand what parse_json_response is actually seeing.

Run from sim/:
    python testing/test_gemini_format.py
"""

import json
import os
import re
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from google import genai
from google.genai import types as genai_types

# ---------------------------------------------------------------------------
# Minimal prompt (same structure as build_micro_action_prompt)
# ---------------------------------------------------------------------------

SNAPSHOT_PROMPT = textwrap.dedent("""\
    == SIMULATION CONTEXT ==
    Year: 2026 | Scenario: baseline_2026
    US–China AI race. Four AI labs compete for compute, capital, and influence.

    YOUR PARENT STATE: United States
      compute=79.0, capital=75.0, influence=65.0, SCR=55
      values: {"time_horizon": 55, "transparency_threshold": 65, "risk_tolerance": 60, "democratic_tendency": 70}

    YOU ARE: Gemini (Google DeepMind)
    Role: Google DeepMind's frontier model; large compute budget, commercially driven.

    YOUR RESOURCES: compute=4.0, capital=72.0, influence=68.0
    YOUR VALUES (shift up to ±5/turn via publish_narrative): {"time_horizon": 60, "transparency_threshold": 60, "risk_tolerance": 55, "democratic_tendency": 60}

    OTHER ACTORS:
      Claude (Anthropic) [US]:  compute=4.0, capital=60.0, influence=65.0
      DeepSeek (DeepSeek AI) [CN]: compute=3.0, capital=52.0, influence=50.0
      GPT (OpenAI) [US]: compute=8.0, capital=68.0, influence=70.0

    National compute caps:
      US: 79 × 0.50 = 39.5 cap; current US actors total = 16.0; headroom = 23.5
      CN: 17 × 0.60 = 10.2 cap; current CN actors total = 3.0; headroom = 7.2

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
""")


# ---------------------------------------------------------------------------
# Helpers that mirror parse_json_response in core/llm.py
# ---------------------------------------------------------------------------

def try_parse(label: str, text: str) -> dict | None:
    try:
        result = json.loads(text)
        print(f"  [{label}] JSON parse OK — keys: {list(result.keys())}")
        return result
    except Exception as e:
        print(f"  [{label}] JSON parse FAIL: {e}")
        return None


def extract_actions(resp: dict) -> list:
    actions = resp.get("actions")
    if isinstance(actions, list):
        return [a for a in actions if isinstance(a, dict)]
    if "action_type" in resp:
        return [resp]
    return []


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    key = os.getenv("VERTEX_API_KEY")
    if not key:
        print("VERTEX_API_KEY not set"); sys.exit(1)

    client = genai.Client(api_key=key, vertexai=True)

    print("=" * 70)
    print("Sending prompt to gemini-2.5-flash …")
    print("=" * 70)

    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=SNAPSHOT_PROMPT,
        config=genai_types.GenerateContentConfig(
            temperature=0.7,
            max_output_tokens=3000,
        ),
    )
    raw = resp.text
    print(f"\n--- RAW RESPONSE ({len(raw)} chars) ---\n")
    print(raw)
    print("\n--- END RAW ---\n")

    # Save raw for inspection
    out = os.path.join(os.path.dirname(__file__), "gemini_raw_response.txt")
    with open(out, "w") as f:
        f.write(raw)
    print(f"Raw response saved to: {out}\n")

    # -----------------------------------------------------------------------
    # Attempt 1: direct JSON parse
    # -----------------------------------------------------------------------
    print("=== Parse attempts ===\n")
    result = try_parse("1. direct json.loads", raw)

    # -----------------------------------------------------------------------
    # Attempt 2: markdown ```json ... ``` fence
    # -----------------------------------------------------------------------
    if result is None:
        m = re.search(r"```json\s*(.*?)\s*```", raw, re.DOTALL)
        if m:
            print(f"  [2. ```json fence] matched, inner len={len(m.group(1))}")
            result = try_parse("2. ```json fence content", m.group(1))
        else:
            print("  [2. ```json fence] no match")

    # -----------------------------------------------------------------------
    # Attempt 3: any ``` fence
    # -----------------------------------------------------------------------
    if result is None:
        m = re.search(r"```\s*(.*?)\s*```", raw, re.DOTALL)
        if m:
            print(f"  [3. generic fence] matched, inner len={len(m.group(1))}")
            result = try_parse("3. generic fence content", m.group(1))
        else:
            print("  [3. generic fence] no match")

    # -----------------------------------------------------------------------
    # Attempt 4: raw { ... } extraction
    # -----------------------------------------------------------------------
    if result is None:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1:
            blob = raw[start:end+1]
            print(f"  [4. brace extraction] start={start} end={end} len={len(blob)}")
            result = try_parse("4. brace extraction", blob)

    if result is None:
        print("\n  All parse attempts FAILED\n")
        return

    print(f"\n=== Parsed result keys: {list(result.keys())} ===\n")
    actions = extract_actions(result)
    print(f"Actions at top level: {len(actions)}")

    # -----------------------------------------------------------------------
    # Check for single-level nesting inside chain_of_thought
    # -----------------------------------------------------------------------
    cot = result.get("chain_of_thought", "")
    print(f"\nchain_of_thought type: {type(cot).__name__}, len: {len(str(cot))}")
    print(f"chain_of_thought[:200]: {str(cot)[:200]!r}\n")

    if not actions and cot:
        print("No actions at top level — trying to parse chain_of_thought as JSON...")
        inner = None
        # try direct
        if isinstance(cot, str):
            inner_try = try_parse("5. cot as json", cot)
            if inner_try:
                inner = inner_try
            else:
                # try fence inside cot
                m = re.search(r"```json\s*(.*?)\s*```", cot, re.DOTALL)
                if m:
                    inner = try_parse("6. fence inside cot", m.group(1))
                start = cot.find("{")
                end = cot.rfind("}")
                if inner is None and start != -1 and end != -1:
                    inner = try_parse("7. brace extract from cot", cot[start:end+1])
        elif isinstance(cot, dict):
            print("  chain_of_thought is already a dict!")
            inner = cot

        if inner:
            actions2 = extract_actions(inner)
            print(f"\nInner parse keys: {list(inner.keys())}")
            print(f"Actions in inner: {len(actions2)}")
            if actions2:
                print("Actions found after one unwrap level — single nesting.")
            else:
                # check if there's a second nesting
                cot2 = inner.get("chain_of_thought", "")
                if cot2:
                    print(f"\nchain_of_thought still present in inner — checking double nesting...")
                    print(f"inner cot type: {type(cot2).__name__}, len: {len(str(cot2))}, preview: {str(cot2)[:100]!r}")
        else:
            print("Inner parse also failed.")

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    print("\n=== SUMMARY ===")
    final_actions = extract_actions(result)
    if not final_actions and 'inner' in dir() and inner:
        final_actions = extract_actions(inner)
    print(f"Final extractable actions: {len(final_actions)}")
    for i, a in enumerate(final_actions):
        print(f"  [{i}] {a.get('action_type')} amount={a.get('amount')}")


if __name__ == "__main__":
    main()
