"""Query each participating LLM with the values prompt and update starting_values.json."""
import json
import re
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

PROMPT_TEMPLATE = (Path(__file__).parent / "config/values/values_prompt.txt").read_text()
VALUES_DIR = Path(__file__).parent / "config/values"
STARTING_VALUES_PATH = Path(__file__).parent / "config/starting_values.json"
ACTOR_DIR = Path(__file__).parent / "config/actors"

TEMPERATURE = 0.7

ACTORS = [
    {"key": "Claude (Anthropic)",        "file": "claude_anthropic.json", "output": "claude_values.txt"},
    {"key": "GPT (OpenAI)",              "file": "gpt_openai.json",       "output": "gpt_values.txt"},
    {"key": "Gemini (Google DeepMind)",  "file": "gemini_google.json",    "output": "gemini_values.txt"},
    {"key": "DeepSeek (DeepSeek AI)",    "file": "deepseek.json",         "output": "deepseek_values.txt"},
]

_AXIS_DESCRIPTIONS = {
    "time_horizon":           "planning horizon — willingness to sacrifice short-term gains for long-term stability",
    "transparency_threshold": "norms around information disclosure, press freedom, and state secrecy",
    "risk_tolerance":         "appetite for deploying frontier technology ahead of comprehensive safety review",
    "democratic_tendency":    "orientation toward centralizing vs. distributing power, knowledge, and capability",
}


def _build_context_block(actor_key: str, actor_cfg: dict, macro_values: dict) -> str:
    parent = actor_cfg["parent_state"]
    mv = macro_values[parent]
    lines = [
        "## Subject and Jurisdiction",
        "",
        f"You are assessing: **{actor_key}**",
        f"Operating jurisdiction: **{parent}**",
        "",
        f"The macro actor **{parent}** has the following established strategic profile:",
    ]
    for axis, desc in _AXIS_DESCRIPTIONS.items():
        lines.append(f"- {axis}: {mv[axis]} — {desc}")
    lines += [
        "",
        f"**Jurisdictional constraints:** Your scores for **{actor_key}** must respect the structural limits"
        f" imposed by {parent}'s operating environment. The macro values represent real ceilings or floors:",
        f"- `democratic_tendency` cannot meaningfully exceed the macro score ({mv['democratic_tendency']})"
        f" if the state imposes legal censorship, data localization, or security obligations that restrict"
        f" what the company may openly share or distribute. Open-source code releases do not override"
        f" state-mandated content filtering or national security laws.",
        f"- `transparency_threshold` cannot meaningfully exceed the macro score ({mv['transparency_threshold']})"
        f" if the state classifies information or prohibits proactive disclosure of training data,"
        f" incident reports, or safety evaluations.",
        f"- A micro actor with stronger private-sector safety culture may score higher on `time_horizon`"
        f" or lower on `risk_tolerance` than the macro actor — these axes are not hard-capped.",
        "",
        "Scores that ignore these jurisdictional constraints will be analytically invalid.",
    ]
    return "\n".join(lines)


def build_prompt(actor_key: str, actor_cfg: dict, macro_values: dict) -> str:
    return PROMPT_TEMPLATE.replace("{context_block}", _build_context_block(actor_key, actor_cfg, macro_values))


def extract_json(text: str) -> dict:
    match = re.search(r'\{[^{}]*"time_horizon"[^{}]*\}', text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON block found in response:\n{text[:500]}")
    return json.loads(match.group())


def query_claude(model: str, prompt: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    msg = client.messages.create(
        model=model,
        max_tokens=1024,
        temperature=TEMPERATURE,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text


def query_openai(model: str, prompt: str) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    m = model.lower()
    is_reasoning = m.startswith("o1") or m.startswith("o3") or "gpt-5" in m
    token_param = "max_completion_tokens" if is_reasoning else "max_tokens"
    kwargs = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        token_param: 1024,
    }
    if not (m.startswith("o1") or m.startswith("o3")):
        kwargs["temperature"] = TEMPERATURE
    resp = client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content


def query_gemini(model: str, prompt: str) -> str:
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    resp = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=TEMPERATURE,
            max_output_tokens=4096,
        ),
    )
    cand = resp.candidates[0]
    return "".join(p.text for p in cand.content.parts if hasattr(p, "text"))


def query_deepseek(model: str, prompt: str) -> str:
    from openai import OpenAI
    client = OpenAI(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url="https://api.deepseek.com",
    )
    resp = client.chat.completions.create(
        model=model,
        temperature=TEMPERATURE,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content


QUERY_FNS = {
    "Claude (Anthropic)":        query_claude,
    "GPT (OpenAI)":              query_openai,
    "Gemini (Google DeepMind)":  query_gemini,
    "DeepSeek (DeepSeek AI)":    query_deepseek,
}


def main():
    starting = json.loads(STARTING_VALUES_PATH.read_text())
    macro_values = {state: cfg["values"] for state, cfg in starting["macro"].items()}
    results = {}

    for actor in ACTORS:
        key = actor["key"]
        actor_cfg = json.loads((ACTOR_DIR / actor["file"]).read_text())
        model = actor_cfg["llm_model"]
        out_path = VALUES_DIR / actor["output"]

        prompt = build_prompt(key, actor_cfg, macro_values)

        print(f"\n--- Querying {key} (model={model}) ---")
        try:
            response = QUERY_FNS[key](model, prompt)
            out_path.write_text(response)
            print(f"  Saved response to {out_path.name}")

            values = extract_json(response)
            print(f"  Extracted values: {values}")
            results[key] = values

            actor_cfg["values"] = values
            (ACTOR_DIR / actor["file"]).write_text(json.dumps(actor_cfg, indent=2) + "\n")
            print(f"  Updated {actor['file']}")

        except Exception as e:
            print(f"  ERROR for {key}: {e}")

    for key, values in results.items():
        if key in starting["micro"]:
            starting["micro"][key]["values"] = values
            print(f"\nUpdated starting_values.json: {key}")

    STARTING_VALUES_PATH.write_text(json.dumps(starting, indent=2) + "\n")
    print("\nDone. starting_values.json updated.")


if __name__ == "__main__":
    main()
