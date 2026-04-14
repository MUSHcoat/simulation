#!/usr/bin/env python3
"""
Multi-provider LLM client.
Supports Anthropic (claude-*), OpenAI (gpt-*, o*), and Google (gemini-*).
"""

import os
import re
import json
import time
import threading
import logging
from typing import Any, Dict

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-provider rate throttle
# ---------------------------------------------------------------------------
# Vertex AI (paid tier) has generous RPM quotas; no Gemini throttle needed.
# Add entries here if using a provider with a strict rate limit.
_PROVIDER_MIN_SPACING: Dict[str, float] = {
    # "gemini": 7.0,  # re-enable if using AI Studio free tier (10 RPM cap)
}
_provider_last_call: Dict[str, float] = {}
_provider_lock = threading.Lock()


def _throttle(model: str) -> None:
    """Sleep if needed to stay under per-provider rate limits."""
    provider = next((p for p in _PROVIDER_MIN_SPACING if model.lower().startswith(p)), None)
    if not provider:
        return
    min_spacing = _PROVIDER_MIN_SPACING[provider]
    with _provider_lock:
        elapsed = time.monotonic() - _provider_last_call.get(provider, 0.0)
        if elapsed < min_spacing:
            wait = min_spacing - elapsed
            logger.debug(f"  Throttling {provider}: waiting {wait:.1f}s to stay under rate limit")
            time.sleep(wait)
        _provider_last_call[provider] = time.monotonic()


# ---------------------------------------------------------------------------
# SDK clients
# ---------------------------------------------------------------------------
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
    from google import genai as _genai
    from google.genai import types as _genai_types
    _vertex_key = os.getenv("VERTEX_API_KEY")
    # Vertex AI Express Mode: api_key + vertexai=True (no project/location).
    # project/location and api_key are mutually exclusive in the SDK.
    _genai_client = (
        _genai.Client(api_key=_vertex_key, vertexai=True)
        if _vertex_key
        else None
    )
except Exception as _e:
    logger.debug(f"Google GenAI client init failed: {_e}")
    _genai_client = None
    _genai_types = None


def get_llm_response(model: str, prompt: str, temperature: float = 0.7,
                     max_tokens: int = 3000, retries: int = 5) -> str:
    """Call the appropriate LLM provider and return the text response."""
    last_err = None
    for attempt in range(retries):
        try:
            _throttle(model)
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
                if not _genai_client:
                    raise RuntimeError("Google GenAI not configured — set VERTEX_API_KEY in .env")
                # Disable thinking: gemini-2.5-flash uses thinking tokens by default,
                # which eat into max_output_tokens and truncate the visible response.
                # The chain_of_thought field in the JSON already captures reasoning.
                thinking_cfg = (
                    _genai_types.ThinkingConfig(thinking_budget=0)
                    if hasattr(_genai_types, "ThinkingConfig")
                    else None
                )
                cfg_kwargs: Dict[str, Any] = {
                    "temperature": temperature,
                    "max_output_tokens": max_tokens,
                }
                if thinking_cfg is not None:
                    cfg_kwargs["thinking_config"] = thinking_cfg
                resp = _genai_client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=_genai_types.GenerateContentConfig(**cfg_kwargs),
                )
                return resp.text

            else:
                raise RuntimeError(f"Unknown model provider for: {model}")

        except Exception as e:
            last_err = e
            logger.warning(f"LLM call attempt {attempt + 1} failed for {model}: {e}")
            if attempt < retries - 1:
                # Longer backoff for transient overload (503/429); shorter for other errors.
                err_str = str(e).lower()
                is_overload = any(s in err_str for s in ("503", "429", "unavailable", "rate limit", "too many"))
                delay = min(60, (20 if is_overload else 5) * 2 ** attempt)
                logger.info(f"  Retrying {model} in {delay}s (attempt {attempt + 2}/{retries})...")
                time.sleep(delay)

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
