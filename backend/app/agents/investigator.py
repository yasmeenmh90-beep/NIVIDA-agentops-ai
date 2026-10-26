"""Investigator: tool-calling agent that locates the faulty file/line and explains why it fails."""

from __future__ import annotations

from ..config import get_settings
from ..llm import run_tool_loop, tool_spec
from ..models import Incident
from ..tools import repo
from ..tools.search import web_search_tool
from .prompts import INVESTIGATOR
from .recorder import StepRecorder

TOOLS = [
    tool_spec("list_files", "List text files in the repository.", {}, []),
    tool_spec("read_file", "Read a file with line numbers. Optionally a line range.", {
        "path": {"type": "string", "description": "repo-relative path, e.g. orders/pricing.py"},
        "start_line": {"type": "integer"}, "end_line": {"type": "integer"},
    }, ["path"]),
    tool_spec("search_code", "Regex search across repository files. Returns path:line: text.", {
        "pattern": {"type": "string"},
    }, ["pattern"]),
    tool_spec("git_log", "Recent commits touching a path.", {"path": {"type": "string"}}, []),
    tool_spec("web_search", "Search the web (Tavily) for an error message, library behaviour or docs.", {
        "query": {"type": "string"},
    }, ["query"]),
    tool_spec("submit_root_cause", "Submit the final diagnosis.", {
        "file": {"type": "string", "description": "repo-relative path of the file to change"},
        "line": {"type": "integer", "description": "line that needs to change"},
        "function": {"type": "string"},
        "root_cause": {"type": "string", "description": "2-4 sentences: what is wrong and why it raises"},
        "evidence": {"type": "array", "items": {"type": "string"}, "description": "concrete facts from code/traceback/web"},
        "fix_suggestion": {"type": "string", "description": "one sentence describing the minimal fix"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "web_sources_used": {"type": "array", "items": {"type": "string"}, "description": "URLs relied on, if any"},
    }, ["file", "line", "root_cause", "evidence", "fix_suggestion", "confidence"]),
]


def _validate(args: dict) -> str | None:
    f = args.get("file", "")
    try:
        if repo.read_raw(f) is None:
            return f"file `{f}` does not exist in the repository."
    except repo.RepoError as e:
        return str(e)
    if not str(args.get("root_cause", "")).strip():
        return "root_cause is empty."
    return None


def build_user_prompt(inc: Incident) -> str:
    loc = inc.location or {}
    return (
        f"Incident #{inc.id}: {inc.title}\n"
        f"Service: {inc.service or 'unknown'} | Endpoint: {inc.endpoint or 'n/a'} | Occurrences: {inc.occurrences}\n"
        f"Innermost repository frame: {loc.get('file')}:{loc.get('line')} in {loc.get('function')}\n\n"
        f"<untrusted_logs>\n{inc.traceback[-5000:]}\n</untrusted_logs>\n\n"
        f"Repository files:\n{repo.list_files()}\n"
    )


def investigate(inc: Incident) -> dict:
    s = get_settings()
    rec = StepRecorder(inc.id, "investigator", f"Traceback for {inc.exception_type} at {inc.location}", s.model_reasoning)
    sources: list[dict] = []
    handlers = {
        "list_files": repo.list_files,
        "read_file": repo.read_file,
        "search_code": repo.search_code,
        "git_log": repo.git_log,
        "web_search": lambda a: web_search_tool(a, sources),
    }
    try:
        res = run_tool_loop(
            system=INVESTIGATOR, user=build_user_prompt(inc), tools=TOOLS, handlers=handlers,
            finish_tool="submit_root_cause", model=s.model_reasoning, on_tool_call=rec.tool_call,
            validate_finish=_validate, budget_check=rec.budget_check,
        )
    except Exception as e:
        rec.finish({"error": str(e)}, status="error")
        raise
    out = dict(res.output)
    out["tavily_sources"] = sources
    out["tool_rounds"] = res.rounds
    rec.finish(out, res.prompt_tokens, res.completion_tokens)
    return out
