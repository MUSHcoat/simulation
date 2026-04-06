#!/usr/bin/env python3
"""
Multi-provider LLM client.
Supports Anthropic (claude-*), OpenAI (gpt-*, o*), and Google (gemini-*).
"""

import os
import re
import json
import time
import logging
from typing import Any, Dict

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Optional SDK imports
try:
    import anthropic as _anthropic
    _anthropic_client = _anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY")) if os.getenv("ANTHROPIC_API_KEY") else None
except Exception:
    _anthropic_client = None

try:
    import openai as _openai
    import httpx
    _openai_client = _openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"), http_client=httpx.Client()) if os.getenv("OPENAI_API_KEY") else None
except Exception:
    _openai_client = None

try:
    import google.generativeai as _genai
    _google_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if _google_key:
        _genai.configure(api_key=_google_key)
    else:
        _genai = None
except Exception:
    _genai = None


def get_llm_response(model: str, prompt: str, temperature: float = 0.7,
                     max_tokens: int = 3000, retries: int = 3) -> str:
    """Call the appropriate LLM provider and return the text response."""
    last_err = None
    for attempt in range(retries):
        try:
            m = model.lower()
            if m.startswith("claude"):
                if not _anthropic_client:
                    raise RuntimeError("Anthropic client not configured — set ANTHROPIC_API_KEY")
                resp = _anthropic_client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    messages=[{"role": "user", "content": prompt}],
                )
                return "".join(block.text for block in resp.content)

            elif m.startswith("gpt") or m.startswith("o1") or m.startswith("o3"):
                if not _openai_client:
                    raise RuntimeError("OpenAI client not configured — set OPENAI_API_KEY")
                is_reasoning = m.startswith("o1") or m.startswith("o3") or "gpt-5" in m
                token_param = "max_completion_tokens" if is_reasoning else "max_tokens"
                kwargs: Dict[str, Any] = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    token_param: max_tokens,
                }
                if not (m.startswith("o1") or m.startswith("o3")):
                    kwargs["temperature"] = temperature
                resp = _openai_client.chat.completions.create(**kwargs)
                return resp.choices[0].message.content

            elif m.startswith("gemini"):
                if not _genai:
                    raise RuntimeError("Google GenAI not configured — set GOOGLE_API_KEY or GEMINI_API_KEY")
                gmodel = _genai.GenerativeModel(model)
                resp = gmodel.generate_content(
                    [{"text": prompt}],
                    generation_config={"temperature": temperature, "max_output_tokens": max_tokens},
                )
                return resp.text

            else:
                raise RuntimeError(f"Unknown model provider for: {model}")

        except Exception as e:
            last_err = e
            logger.warning(f"LLM call attempt {attempt + 1} failed for {model}: {e}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)

    raise RuntimeError(f"All {retries} LLM call attempts failed: {last_err}")


def parse_json_response(text: str) -> Dict[str, Any]:
    """Robustly parse a JSON response, handling markdown code blocks."""
    # Direct parse
    try:
        return json.loads(text)
    except Exception:
        pass

    # Markdown JSON block
    match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            pass

    # Any markdown block
    match = re.search(r"```\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            pass

    # Raw JSON blob extraction
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            pass

    logger.warning(f"Could not parse JSON from response: {text[:200]!r}")
    return {}
