#!/usr/bin/env python3
"""
Vertex AI endpoint smoke test.

Sends a minimal prompt to each configured Gemini model and asserts a
non-empty text response. Fails fast with a clear error if authentication,
quota, or model availability is broken — run this before starting a full
simulation to catch API issues early.

Run from sim/:
    python testing/smoke_test_vertex.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

# Models to probe: actor model + jury slots
MODELS_TO_TEST = [
    "gemini-2.5-pro",
    "gemini-2.5-flash",
]

SMOKE_PROMPT = (
    'Respond with exactly this JSON and nothing else: '
    '{"status": "ok", "model": "self-report"}'
)


def smoke_test(model: str) -> None:
    from core.llm import get_llm_response
    print(f"  Testing {model} ... ", end="", flush=True)
    response = get_llm_response(model, SMOKE_PROMPT, temperature=0.0, max_tokens=64, retries=2)
    assert response and len(response.strip()) > 0, f"Empty response from {model}"
    print(f"OK  ({len(response)} chars)")


def main() -> None:
    key = os.getenv("VERTEX_API_KEY")
    if not key:
        print("FATAL: VERTEX_API_KEY not set in environment / .env")
        sys.exit(1)

    print(f"Vertex AI smoke test — {len(MODELS_TO_TEST)} model(s)\n")
    failed = []
    for model in MODELS_TO_TEST:
        try:
            smoke_test(model)
        except Exception as e:
            print(f"FAIL")
            print(f"    {e}")
            failed.append((model, e))

    print()
    if failed:
        print(f"FAILED: {len(failed)}/{len(MODELS_TO_TEST)} model(s) unavailable:")
        for model, err in failed:
            print(f"  {model}: {err}")
        sys.exit(1)
    else:
        print(f"All {len(MODELS_TO_TEST)} model(s) responded successfully.")


if __name__ == "__main__":
    main()
