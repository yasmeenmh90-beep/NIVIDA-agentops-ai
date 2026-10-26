"""Turn the Fixer's structured edits into new file contents + a unified diff, with safety limits.

The Fixer returns search/replace edits rather than a raw diff: models get exact
search/replace right far more often than hunk headers. We generate the unified diff
ourselves with difflib, so the diff shown to humans always matches what is applied.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass

from ..config import get_settings
from .repo import RepoError, read_raw, safe_path


class PatchError(ValueError):
    pass


@dataclass
class BuiltPatch:
    files: dict[str, str]  # path -> full new content
    diff: str
    changed_lines: int


def _is_protected(path: str) -> bool:
    for p in get_settings().protected_list:
        if p.endswith("/") and (path.startswith(p) or f"/{p}" in f"/{path}"):
            return True
        if path == p or path.endswith("/" + p):
            return True
    return False


def build_patch(edits: list[dict]) -> BuiltPatch:
    s = get_settings()
    if not isinstance(edits, list) or not edits:
        raise PatchError("`edits` must be a non-empty list")
    originals: dict[str, str | None] = {}
    current: dict[str, str] = {}
    for i, e in enumerate(edits, 1):
        path = str(e.get("path", "")).strip()
        old = e.get("old_string", "")
        new = e.get("new_string", "")
        if not path:
            raise PatchError(f"edit {i}: `path` is required")
        try:
            safe_path(path)
        except RepoError as err:
            raise PatchError(f"edit {i}: {err}") from err
        if not path.endswith(".py"):
            raise PatchError(f"edit {i}: only .py files may be changed ({path})")
        if _is_protected(path):
            raise PatchError(f"edit {i}: {path} is a protected path and cannot be modified")
        if path not in current:
            orig = read_raw(path)
            name = path.rsplit("/", 1)[-1]
            if orig is not None and (path.startswith("tests/") or name.startswith("test_")):
                raise PatchError(f"edit {i}: existing tests ({path}) may not be modified; add a new test file instead")
            originals[path] = orig
            current[path] = orig if orig is not None else ""
        text = current[path]
        if old == "":
            if originals[path] is not None and text:
                raise PatchError(f"edit {i}: empty old_string is only allowed when creating a new file ({path} exists)")
            current[path] = new
            continue
        count = text.count(old)
        if count == 0:
            raise PatchError(f"edit {i}: old_string not found in {path}. Copy it exactly from read_file output (without line numbers).")
        if count > 1:
            raise PatchError(f"edit {i}: old_string occurs {count} times in {path}; include more surrounding lines so it is unique.")
        current[path] = text.replace(old, new, 1)

    diff_parts, changed = [], 0
    for path, new_text in current.items():
        old_text = originals[path] or ""
        if new_text == old_text:
            continue
        d = list(difflib.unified_diff(
            old_text.splitlines(keepends=True), new_text.splitlines(keepends=True),
            fromfile=f"a/{path}" if originals[path] is not None else "/dev/null", tofile=f"b/{path}",
        ))
        changed += sum(1 for ln in d if ln[:1] in "+-" and not ln.startswith(("+++", "---")))
        diff_parts.append("".join(x if x.endswith("\n") else x + "\n" for x in d))
    if not diff_parts:
        raise PatchError("the edits do not change anything")
    if changed > s.max_patch_lines:
        raise PatchError(f"patch changes {changed} lines; the limit is {s.max_patch_lines}. Make a smaller, targeted fix.")
    files = {p: t for p, t in current.items() if t != (originals[p] or "")}
    return BuiltPatch(files=files, diff="".join(diff_parts), changed_lines=changed)
