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
    passed, failed, msgs = [], [], {}
    root = ET.parse(path).getroot()
    for tc in root.iter("testcase"):
        cls = tc.get("classname", "")
        mod = cls.replace(".", "/") + ".py" if cls else ""
        nodeid = f"{mod}::{tc.get('name')}" if mod else tc.get("name", "?")
        bad = tc.find("failure") if tc.find("failure") is not None else tc.find("error")
        if tc.find("skipped") is not None:
            continue
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

        cmd = shlex.split(ctx.test_command) + ["--junitxml", "junit.xml"]
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


def compare(before: TestRun, after: TestRun) -> dict:
    """A patch is verified if it fixes >=1 failing test and breaks none (after ⊂ before, strictly)."""
    b, a = set(before.failed), set(after.failed)
    fixed = sorted(b - a)
    regressions = sorted(a - b)
    verified = bool(fixed) and not regressions and not after.collection_error
    return {
        "verified": verified,
        "fixed": fixed,
        "regressions": regressions,
        "still_failing": sorted(a & b),
        "before": {"passed": len(before.passed), "failed": len(before.failed)},
        "after": {"passed": len(after.passed), "failed": len(after.failed)},
        "runner": after.runner,
        "duration_s": round(before.duration_s + after.duration_s, 2),
    }
