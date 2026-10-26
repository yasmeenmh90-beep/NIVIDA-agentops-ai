"""Fixer: writes a minimal patch (search/replace edits) for the diagnosed root cause."""

from __future__ import annotations

from ..config import get_settings
from ..llm import run_tool_loop, tool_spec
from ..models import Incident
from ..tools import repo
from ..tools.patching import BuiltPatch, PatchError, build_patch
from .prompts import FIXER
from .recorder import StepRecorder

TOOLS = [
    tool_spec("read_file", "Read a file with line numbers.", {
        "path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"},
    }, ["path"]),
    tool_spec("search_code", "Regex search across repository files.", {"pattern": {"type": "string"}}, ["pattern"]),
    tool_spec("submit_patch", "Submit the patch as exact search/replace edits.", {
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string", "description": "exact existing text; empty only to create a new file"},
                    "new_string": {"type": "string"},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
        "explanation": {"type": "string", "description": "2-4 sentences in plain English"},
    }, ["edits", "explanation"]),
]


def build_user_prompt(inc: Incident, root_cause: dict, previous_failure: str | None) -> str:
    f = root_cause.get("file", "")
    parts = [
        f"Incident #{inc.id}: {inc.title}",
        f"Root cause ({root_cause.get('confidence', '?')} confidence) in {f}:{root_cause.get('line')}:",
        str(root_cause.get("root_cause", "")),
        f"Suggested fix: {root_cause.get('fix_suggestion', '')}",
        "",
        "Current file:",
        repo.read_file({"path": f}),
    ]
    if previous_failure:
        parts += ["", "Your previous patch did NOT pass verification. Output from that attempt:",
                  f"<untrusted_logs>\n{previous_failure[-4000:]}\n</untrusted_logs>"]
    return "\n".join(parts)


def fix(inc: Incident, root_cause: dict, attempt: int, previous_failure: str | None) -> tuple[BuiltPatch, str]:
    s = get_settings()
    rec = StepRecorder(inc.id, "fixer", f"Attempt {attempt} for {root_cause.get('file')}:{root_cause.get('line')}",
                       s.model_reasoning)
    built: dict[str, BuiltPatch] = {}

    def validate(args: dict) -> str | None:
        try:
            built["patch"] = build_patch(args.get("edits") or [])
        except PatchError as e:
            return str(e)
        if not str(args.get("explanation", "")).strip():
            return "explanation is empty."
        return None

    try:
        res = run_tool_loop(
            system=FIXER, user=build_user_prompt(inc, root_cause, previous_failure), tools=TOOLS,
            handlers={"read_file": repo.read_file, "search_code": repo.search_code},
            finish_tool="submit_patch", model=s.model_reasoning, on_tool_call=rec.tool_call,
            validate_finish=validate, budget_check=rec.budget_check,
        )
    except Exception as e:
        rec.finish({"error": str(e), "attempt": attempt}, status="error")
        raise
    patch = built["patch"]
    explanation = str(res.output.get("explanation", "")).strip()
    rec.finish({"attempt": attempt, "explanation": explanation, "diff": patch.diff,
                "changed_lines": patch.changed_lines, "files": list(patch.files)},
               res.prompt_tokens, res.completion_tokens)
    return patch, explanation
