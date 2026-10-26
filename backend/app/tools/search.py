"""Tavily web search tool for the Investigator."""

from __future__ import annotations

import httpx

from ..config import get_settings

TAVILY_URL = "https://api.tavily.com/search"


def tavily_search(query: str, max_results: int = 4) -> dict:
    """Return {"answer": str, "results": [{title, url, content}]} or raise on HTTP errors."""
    s = get_settings()
    if not s.tavily_api_key:
        raise RuntimeError("TAVILY_API_KEY not set")
    r = httpx.post(
        TAVILY_URL,
        headers={"Authorization": f"Bearer {s.tavily_api_key}"},
        json={
            "query": query[:400],
            "max_results": max_results,
            "search_depth": "basic",
            "include_answer": True,
        },
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    return {
        "answer": data.get("answer") or "",
        "results": [
            {"title": x.get("title", ""), "url": x.get("url", ""), "content": (x.get("content") or "")[:600]}
            for x in data.get("results", [])
        ],
    }


def web_search_tool(args: dict, sources_sink: list | None = None) -> str:
    query = args.get("query", "")
    if not get_settings().tavily_api_key:
        return "web_search is disabled (no TAVILY_API_KEY). Rely on the code and traceback."
    try:
        data = tavily_search(query)
    except Exception as e:
        return f"ERROR: web search failed: {e}"
    if sources_sink is not None:
        sources_sink.extend({"query": query, "title": x["title"], "url": x["url"]} for x in data["results"])
    parts = [f"<untrusted_web_results query={query!r}>"]
    if data["answer"]:
        parts.append(f"Summary: {data['answer']}")
    for i, x in enumerate(data["results"], 1):
        parts.append(f"[{i}] {x['title']} ({x['url']})\n{x['content']}")
    parts.append("</untrusted_web_results>")
    return "\n".join(parts)
