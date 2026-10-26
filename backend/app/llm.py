"""Single LLM client for every agent: Nebius Token Factory's OpenAI-compatible endpoint.

`run_tool_loop` implements the agent loop: the model calls tools until it calls the
designated *finish* tool, whose arguments are the agent's structured output.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .config import get_settings


class LLMError(RuntimeError):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCall]
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw_message: dict = field(default_factory=dict)


class ChatClient(Protocol):
    def chat(self, messages: list[dict], tools: list[dict] | None, model: str) -> LLMResponse: ...


class NebiusClient:
    """Thin wrapper over the OpenAI SDK pointed at Nebius Token Factory."""

    def __init__(self) -> None:
        from openai import OpenAI

        s = get_settings()
        if not s.nebius_api_key:
            raise LLMError("NEBIUS_API_KEY is not set")
        self.s = s
        self.client = OpenAI(base_url=s.nebius_base_url, api_key=s.nebius_api_key, timeout=s.llm_timeout_s, max_retries=2)

    def chat(self, messages: list[dict], tools: list[dict] | None, model: str) -> LLMResponse:
        kwargs: dict[str, Any] = dict(
            model=model, messages=messages, temperature=self.s.llm_temperature, max_tokens=self.s.llm_max_tokens
        )
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        try:
            resp = self.client.chat.completions.create(**kwargs)
        except Exception as e:  # surface API errors as pipeline errors
            raise LLMError(f"Token Factory call failed ({model}): {e}") from e
        msg = resp.choices[0].message
        calls = []
        for tc in msg.tool_calls or []:
            calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=_parse_args(tc.function.arguments)))
        usage = resp.usage
        raw = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            raw["tool_calls"] = [
                {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ]
        return LLMResponse(
            content=msg.content or "",
            tool_calls=calls,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            raw_message=raw,
        )


_client_override: ChatClient | None = None


def set_client(client: ChatClient | None) -> None:
    """Swap the LLM client (used by tests with a scripted fake)."""
    global _client_override
    _client_override = client


def get_client() -> ChatClient:
    return _client_override or NebiusClient()


def _parse_args(raw: str | dict | None) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return {"_unparsed": raw}


def tool_spec(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


def _strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


@dataclass
class LoopResult:
    output: dict
    prompt_tokens: int
    completion_tokens: int
    rounds: int


def run_tool_loop(
    *,
    system: str,
    user: str,
    tools: list[dict],
    handlers: dict[str, Callable[[dict], str]],
    finish_tool: str,
    model: str,
    on_tool_call: Callable[[str, dict, str], None] | None = None,
    validate_finish: Callable[[dict], str | None] | None = None,
    max_rounds: int | None = None,
    budget_check: Callable[[int], None] | None = None,
) -> LoopResult:
    """Run a native tool-calling loop until `finish_tool` is called with valid arguments.

    - handlers: tool name -> function(args) returning a string result (never raises).
    - validate_finish: returns an error message to send back to the model, or None if OK.
    - budget_check: called with tokens used so far; raise to abort.
    """
    s = get_settings()
    client = get_client()
    max_rounds = max_rounds or s.max_tool_rounds
    messages: list[dict] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    pt = ct = 0
    nudges = 0
    for rnd in range(1, max_rounds + 1):
        resp = client.chat(messages, tools, model)
        pt += resp.prompt_tokens
        ct += resp.completion_tokens
        if budget_check:
            budget_check(pt + ct)

        if not resp.tool_calls:
            # Some models write the finish call as JSON text instead of a native tool call.
            # Accept {"name": finish_tool, "arguments": {...}} if it validates.
            parsed = _parse_args(_strip_think(resp.content))
            if parsed.get("name") == finish_tool and isinstance(parsed.get("arguments"), dict):
                args = parsed["arguments"]
                if not (validate_finish and validate_finish(args)):
                    if on_tool_call:
                        on_tool_call(finish_tool, args, "accepted (parsed from text)")
                    return LoopResult(args, pt, ct, rnd)
            messages.append({"role": "assistant", "content": resp.content or ""})
            nudges += 1
            if nudges > 2:
                raise LLMError(f"Model did not call `{finish_tool}` after repeated reminders")
            messages.append({
                "role": "user",
                "content": f"Use the provided tools. When you are done, call `{finish_tool}` with your final answer.",
            })
            continue

        messages.append(resp.raw_message or {"role": "assistant", "content": resp.content})
        finished: dict | None = None
        for call in resp.tool_calls:
            if call.name == finish_tool:
                err = validate_finish(call.arguments) if validate_finish else None
                if err:
                    result = f"REJECTED: {err} Fix the problem and call `{finish_tool}` again."
                else:
                    finished = call.arguments
                    result = "accepted"
            elif call.name in handlers:
                try:
                    result = handlers[call.name](call.arguments)
                except Exception as e:  # tool errors go back to the model, not up the stack
                    result = f"ERROR: {type(e).__name__}: {e}"
            else:
                result = f"ERROR: unknown tool `{call.name}`"
            if on_tool_call:
                on_tool_call(call.name, call.arguments, result)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
        if finished is not None:
            return LoopResult(finished, pt, ct, rnd)

    raise LLMError(f"Tool round limit ({max_rounds}) reached without `{finish_tool}`")


def simple_completion(system: str, user: str, model: str) -> tuple[str, int, int]:
    resp = get_client().chat([{"role": "system", "content": system}, {"role": "user", "content": user}], None, model)
    return _strip_think(resp.content), resp.prompt_tokens, resp.completion_tokens
