"""Day-1 check: confirm your Nebius key works, list Nemotron models, and test native tool calling.

    cd backend && python scripts/check_nebius.py            # uses MODEL_REASONING from .env
    python scripts/check_nebius.py --model nvidia/<model-id>

Pick a model that returns a proper `tool_calls` response and put it in MODEL_REASONING.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openai import OpenAI  # noqa: E402

from app.config import get_settings  # noqa: E402


def main() -> int:
    s = get_settings()
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=s.model_reasoning)
    a = ap.parse_args()
    if not s.nebius_api_key:
        print("NEBIUS_API_KEY is not set (backend/.env)")
        return 1
    client = OpenAI(base_url=s.nebius_base_url, api_key=s.nebius_api_key)

    print("== Models containing 'nemotron' ==")
    try:
        ids = sorted(m.id for m in client.models.list().data)
        nem = [i for i in ids if "nemotron" in i.lower()]
        print("\n".join(nem) or "(none found; all models below)\n" + "\n".join(ids))
    except Exception as e:
        print(f"could not list models: {e}")

    print(f"\n== Tool-calling test on {a.model} ==")
    tools = [{"type": "function", "function": {
        "name": "read_file", "description": "Read a file",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    }}]
    try:
        r = client.chat.completions.create(
            model=a.model, temperature=0, max_tokens=300, tools=tools, tool_choice="auto",
            messages=[{"role": "user", "content": "Read the file orders/pricing.py using the tool."}],
        )
    except Exception as e:
        print(f"FAILED: {e}")
        return 1
    msg = r.choices[0].message
    if msg.tool_calls:
        tc = msg.tool_calls[0]
        print(f"OK  tool call: {tc.function.name}({tc.function.arguments})")
        print(f"usage: {r.usage}")
        return 0
    print("NO native tool call returned. Content was:\n" + json.dumps(msg.content)[:500])
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
