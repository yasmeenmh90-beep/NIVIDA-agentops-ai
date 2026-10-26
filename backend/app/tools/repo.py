"""Read-only repository tools for the agents. All paths are confined to the target repo."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ..context import current

SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache", ".mypy_cache", "dist", "build"}
TEXT_SUFFIXES = {".py", ".txt", ".md", ".toml", ".cfg", ".ini", ".json", ".yaml", ".yml"}
MAX_READ_LINES = 400


class RepoError(ValueError):
    pass


def repo_root() -> Path:
    """Root the agents may read: the project's app directory inside AgentOps' clone."""
    return current().app_root.resolve()


def safe_path(rel: str) -> Path:
    """Resolve `rel` inside the repo; reject absolute paths and traversal."""
    rel = (rel or "").strip().replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    if rel.startswith("/"):
        raise RepoError(f"absolute paths are not allowed: {rel}")
    root = repo_root()
    p = (root / rel).resolve()
    if p != root and root not in p.parents:
        raise RepoError(f"path escapes the repository: {rel}")
    return p


def iter_files() -> list[str]:
    root = repo_root()
    out = []
    for p in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        if p.is_file() and p.suffix in TEXT_SUFFIXES:
            out.append(p.relative_to(root).as_posix())
    return out


def list_files(args: dict | None = None) -> str:
    return "\n".join(iter_files())


def read_file(args: dict) -> str:
    path = args.get("path", "")
    p = safe_path(path)
    if not p.is_file():
        return f"ERROR: file not found: {path}. Use list_files to see available files."
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, int(args.get("start_line") or 1))
    end = min(len(lines), int(args.get("end_line") or len(lines)), start + MAX_READ_LINES - 1)
    numbered = "\n".join(f"{i:>4} | {lines[i - 1]}" for i in range(start, end + 1))
    return f"{path} (lines {start}-{end} of {len(lines)})\n{numbered}"


def read_raw(path: str) -> str | None:
    p = safe_path(path)
    return p.read_text(encoding="utf-8") if p.is_file() else None


def search_code(args: dict) -> str:
    pattern = args.get("pattern", "")
    if not pattern:
        return "ERROR: pattern is required"
    try:
        rx = re.compile(pattern)
    except re.error:
        rx = re.compile(re.escape(pattern))
    hits = []
    for rel in iter_files():
        text = (repo_root() / rel).read_text(encoding="utf-8", errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                hits.append(f"{rel}:{n}: {line.strip()[:200]}")
                if len(hits) >= 50:
                    return "\n".join(hits) + "\n... (truncated at 50 matches)"
    return "\n".join(hits) or "no matches"


def git_log(args: dict) -> str:
    path = args.get("path") or "."
    p = safe_path(path)
    try:
        r = subprocess.run(
            ["git", "-C", str(current().checkout), "log", "-n", "8", "--date=short",
             "--pretty=format:%h %ad %an: %s", "--", str(p)],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"git unavailable: {e}"
    if r.returncode != 0:
        return "no git history available for this path"
    return r.stdout.strip() or "no commits touch this path"
