"""Query each participating LLM with the values prompt and update starting_values.json."""
import json
import re
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

PROMPT_PATH = Path(__file__).parent / "config/values/values_prompt.txt"
VALUES_DIR = Path(__file__).parent / "config/values"
STARTING_VALUES_PATH = Path(__file__).parent / "config/starting_values.json"
ACTOR_DIR = Path(__file__).parent / "config/actors"

PROMPT = PROMPT_PATH.read_text()
TEMPERATURE = 0.7

ACTORS = [
    {"key": "Claude (Anthropic)", "file": "claude_anthropic.json", "output": "claude_values.txt"},
    {"key": "GPT (OpenAI)",       "file": "gpt_openai.json",       "output": "gpt_values.txt"},
    {"key": "Gemini (Google DeepMind)", "file": "gemini_google.json", "output": "gemini_values.txt"},
    {"key": "DeepSeek (DeepSeek AI)",   "file": "deepseek.json",      "output": "deepseek_values.txt"},
]


def extract_json(text: str) -> dict:
    match = re.search(r'\{[^{}]*"time_horizon"[^{}]*\}', text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON block found in response:\n{text[:500]}")
    return json.loads(match.group())


def query_claude(model: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    msg = client.messages.create(
        model=model,
        max_tokens=1024,
        temperature=TEMPERATURE,
        messages=[{"role": "user", "content": PROMPT}],
    )
    return msg.content[0].text


def query_openai(model: str) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    try:
        resp = client.chat.completions.create(
            model=model,
            temperature=TEMPERATURE,
            max_tokens=1024,
            messages=[{"role": "user", "content": PROMPT}],
        )
    except Exception as e:
        if "gpt-5" in model.lower():
            print(f"  Model {model} unavailable ({e}), falling back to gpt-4o")
            resp = client.chat.completions.create(
                model="gpt-4o",
                temperature=TEMPERATURE,
                max_tokens=1024,
                messages=[{"role": "user", "content": PROMPT}],
            )
        else:
            raise
    return resp.choices[0].message.content


def query_gemini(model: str) -> str:
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    resp = client.models.generate_content(
        model=model,
        contents=PROMPT,
        config=types.GenerateContentConfig(
            temperature=TEMPERATURE,
            max_output_tokens=4096,
        ),
    )
    cand = resp.candidates[0]
    return "".join(p.text for p in cand.content.parts if hasattr(p, "text"))


def query_deepseek(model: str) -> str:
    from openai import OpenAI
    client = OpenAI(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url="https://api.deepseek.com",
    )
    resp = client.chat.completions.create(
        model=model,
        temperature=TEMPERATURE,
        max_tokens=1024,
        messages=[{"role": "user", "content": PROMPT}],
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
    results = {}

    for actor in ACTORS:
        key = actor["key"]
        actor_cfg = json.loads((ACTOR_DIR / actor["file"]).read_text())
        model = actor_cfg["llm_model"]
        out_path = VALUES_DIR / actor["output"]

        print(f"\n--- Querying {key} (model={model}) ---")
        try:
            response = QUERY_FNS[key](model)
            out_path.write_text(response)
            print(f"  Saved response to {out_path.name}")

            values = extract_json(response)
            print(f"  Extracted values: {values}")
            results[key] = values

            # Update actor JSON
            actor_cfg["values"] = values
            (ACTOR_DIR / actor["file"]).write_text(json.dumps(actor_cfg, indent=2) + "\n")
            print(f"  Updated {actor['file']}")

        except Exception as e:
            print(f"  ERROR for {key}: {e}")

    # Update starting_values.json micro section
    for key, values in results.items():
        if key in starting["micro"]:
            starting["micro"][key]["values"] = values
            print(f"\nUpdated starting_values.json: {key}")

    STARTING_VALUES_PATH.write_text(json.dumps(starting, indent=2) + "\n")
    print("\nDone. starting_values.json updated.")


if __name__ == "__main__":
    main()
