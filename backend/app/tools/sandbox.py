"""Run a project's test suite on an isolated copy of AgentOps' clone, with or without a patch.

Modes (SANDBOX_MODE):
- docker:     `docker run --network none` with CPU/memory limits on a throwaway copy.
- subprocess: a throwaway temp copy, scrubbed environment (no API keys) and a timeout.
              Weaker isolation; used where Docker is unavailable (e.g. Render free tier).
- auto:       docker if the daemon and sandbox image are available, else subprocess.
The host repo is never modified.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..config import get_settings
from ..context import current

LOG_TAIL = 6000


@dataclass
class TestRun:
    runner: str
    exit_code: int
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    failure_messages: dict[str, str] = field(default_factory=dict)
    duration_s: float = 0.0
    log: str = ""
    collection_error: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _docker_available(image: str) -> bool:
    if not shutil.which("docker"):
        return False
    try:
        r = subprocess.run(["docker", "image", "inspect", image], capture_output=True, timeout=10)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


COPY_SKIP = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", "node_modules", ".venv", "venv"}


def resolve_mode(mode: str | None = None, image: str | None = None) -> str:
    s = get_settings()
    mode = mode or s.sandbox_mode
    if mode in ("docker", "subprocess"):
        return mode
    return "docker" if _docker_available(image or s.sandbox_image) else "subprocess"


def _copy_checkout(dst: Path) -> None:
    def ignore(_dir, names):
        return [n for n in names if n in COPY_SKIP or n.endswith(".pyc")]

    shutil.copytree(current().checkout, dst, ignore=ignore)


def _parse_junit(path: Path) -> tuple[list[str], list[str], dict[str, str]]:
    """Return pytest-style node ids. xunit1 output carries a `file` attribute, which handles
    test classes correctly; fall back to deriving the path from `classname`."""
    passed, failed, msgs = [], [], {}
    root = ET.parse(path).getroot()
    for tc in root.iter("testcase"):
        name = tc.get("name", "?")
        file_attr = (tc.get("file") or "").replace("\\", "/")
        cls = tc.get("classname", "")
        if file_attr:
            mod_path = file_attr
            module_dotted = file_attr[:-3].replace("/", ".") if file_attr.endswith(".py") else file_attr
            inner = cls[len(module_dotted):].lstrip(".") if cls.startswith(module_dotted) else ""
            nodeid = f"{mod_path}::{inner + '::' if inner else ''}{name}"
        elif cls:
            nodeid = f"{cls.replace('.', '/')}.py::{name}"
        else:
            nodeid = name
        if tc.find("skipped") is not None:
            continue
        bad = tc.find("failure") if tc.find("failure") is not None else tc.find("error")
        if bad is not None:
            failed.append(nodeid)
            msgs[nodeid] = (bad.get("message") or "")[:500]
        else:
            passed.append(nodeid)
    return passed, failed, msgs


def run_tests(overrides: dict[str, str] | None = None) -> TestRun:
    """overrides: app-relative path -> new file content."""
    s = get_settings()
    ctx = current()
    mode = resolve_mode(ctx.sandbox_mode, ctx.sandbox_image)
    with tempfile.TemporaryDirectory(prefix="agentops-sbx-") as tmp:
        root = Path(tmp) / "repo"
        _copy_checkout(root)
        work = root / ctx.app_path if ctx.app_path else root
        for rel, content in (overrides or {}).items():
            target = (work / rel).resolve()
            if work.resolve() not in target.parents:
                raise ValueError(f"override escapes sandbox: {rel}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        cmd = shlex.split(ctx.test_command) + ["--junitxml", "junit.xml", "-o", "junit_family=xunit1"]
        start = time.monotonic()
        if mode == "docker":
            full = [
                "docker", "run", "--rm", "--network", "none", "--memory", "512m", "--cpus", "1",
                "--pids-limit", "256", "-v", f"{root}:/work", "-w", f"/work/{ctx.app_path}".rstrip("/"),
                ctx.sandbox_image, *cmd,
            ]
            env = None
        else:
            if cmd[0] in ("python", "python3"):
                cmd[0] = ctx.python
            full = cmd
            path = os.environ.get("PATH", "/usr/bin:/bin")
            env = {  # scrubbed: no API keys or tokens reach the code under test
                "PATH": f"{ctx.env_bin}{os.pathsep}{path}" if ctx.env_bin else path,
                "HOME": tmp,
                "LANG": "C.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONHASHSEED": "0",
            }
        try:
            r = subprocess.run(full, cwd=work, env=env, capture_output=True, text=True, timeout=s.sandbox_timeout_s)
            code, out = r.returncode, (r.stdout or "") + (r.stderr or "")
        except subprocess.TimeoutExpired as e:
            code, out = -1, f"TIMEOUT after {s.sandbox_timeout_s}s\n{e.stdout or ''}"
        duration = round(time.monotonic() - start, 2)

        run = TestRun(runner=mode, exit_code=code, duration_s=duration, log=out[-LOG_TAIL:])
        junit = work / "junit.xml"
        if junit.exists():
            run.passed, run.failed, run.failure_messages = _parse_junit(junit)
        if code not in (0, 1) or not junit.exists():
            # 2=interrupted, 3=internal, 4=usage, 5=no tests: treat as a collection-level failure
            run.collection_error = True
            if "<collection>" not in run.failed:
                run.failed.append("<collection>")
                run.failure_messages["<collection>"] = out[-500:]
        return run


def _in_files(nodeid: str, files: list[str]) -> bool:
    return nodeid.split("::", 1)[0] in files


def compare(before: TestRun, after: TestRun, regression_files: list[str] | None = None,
            require_regression_test: bool = False) -> dict:
    """Decide whether a patch is verified.

    `before` = unpatched code (+ any new regression test files), `after` = patched code.
    - Always: no test that passed before may fail after, and the suite must collect.
    - With require_regression_test: the patch must add a regression test that FAILS on the
      unpatched code (it reproduces the bug) and PASSES after the fix.
    - Without it: at least one previously failing test must now pass.
    """
    regression_files = regression_files or []
    b, a = set(before.failed), set(after.failed)
    fixed = sorted(b - a)
    regressions = sorted(a - b)
    repro = sorted(t for t in before.failed if _in_files(t, regression_files))
    not_reproducing = sorted(t for t in before.passed if _in_files(t, regression_files))
    repro_still_failing = sorted(t for t in repro if t in a)
    problems = []
    if after.collection_error:
        problems.append("the test suite failed to run or collect after the patch")
    if regressions:
        problems.append(f"{len(regressions)} test(s) that passed before now fail")
    if require_regression_test:
        if not regression_files:
            problems.append("no regression test was added")
        elif not repro:
            problems.append("the new regression test does not fail on the unpatched code, so it does not reproduce the bug"
                            if not_reproducing else "the new regression test was not collected or did not run")
        if repro_still_failing:
            problems.append("the new regression test still fails after the fix")
    elif not fixed:
        problems.append("no previously failing test passes after the patch")
    return {
        "verified": not problems,
        "problems": problems,
        "fixed": fixed,
        "regressions": regressions,
        "regression_tests": repro,
        "non_reproducing_tests": not_reproducing,
        "still_failing": sorted(a & b),
        "before": {"passed": len(before.passed), "failed": len(before.failed)},
        "after": {"passed": len(after.passed), "failed": len(after.failed)},
        "runner": after.runner,
        "duration_s": round(before.duration_s + after.duration_s, 2),
    }
