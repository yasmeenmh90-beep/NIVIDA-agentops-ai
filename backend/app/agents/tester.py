"""Tester: runs the suite in the sandbox before and after the patch and decides if it is verified.

Verified = the patch makes at least one previously failing test pass and introduces no new
failures. If the Fixer added a regression test, the "before" run includes that test on the
unpatched code, so the new test must fail first and pass after the fix.
"""

from __future__ import annotations

from ..tools.patching import BuiltPatch
from ..tools.sandbox import TestRun, compare, run_tests
from .recorder import StepRecorder


def _is_test(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return path.startswith("tests/") or name.startswith("test_") or name.endswith("_test.py")


def failure_report(run: TestRun, cmp: dict) -> str:
    lines = [f"Verified: {cmp['verified']}. Fixed: {cmp['fixed']}. New failures: {cmp['regressions']}."]
    for t in cmp["regressions"] or run.failed:
        if t in run.failure_messages:
            lines.append(f"- {t}: {run.failure_messages[t]}")
    lines.append("--- pytest output (tail) ---")
    lines.append(run.log[-2500:])
    return "\n".join(lines)


def verify_patch(incident_id: int, patch: BuiltPatch, attempt: int, baseline: TestRun) -> tuple[bool, dict, str]:
    rec = StepRecorder(incident_id, "tester", f"Attempt {attempt}: {len(patch.files)} file(s), {patch.changed_lines} changed lines")
    try:
        test_only = {p: c for p, c in patch.files.items() if _is_test(p)}
        if test_only:
            before = run_tests(test_only)
            rec.tool_call("sandbox.run_tests", {"variant": "unpatched code + new tests"},
                          f"passed={len(before.passed)} failed={len(before.failed)} runner={before.runner}")
        else:
            before = baseline
        after = run_tests(patch.files)
        rec.tool_call("sandbox.run_tests", {"variant": "patched"},
                      f"passed={len(after.passed)} failed={len(after.failed)} runner={after.runner}")
        cmp = compare(before, after)
        cmp["before_failed"] = before.failed
        cmp["after_failed"] = after.failed
        cmp["failure_messages_before"] = {k: before.failure_messages.get(k, "") for k in cmp["fixed"]}
        log = (
            f"=== BEFORE ({'unpatched + new tests' if test_only else 'unpatched'}) exit={before.exit_code} ===\n"
            f"{before.log[-3000:]}\n=== AFTER (patched) exit={after.exit_code} ===\n{after.log[-3000:]}"
        )
    except Exception as e:
        rec.finish({"error": str(e), "attempt": attempt}, status="error")
        raise
    rec.finish({k: v for k, v in cmp.items() if k != "failure_messages_before"} | {"attempt": attempt})
    report = "" if cmp["verified"] else failure_report(after, cmp)
    return cmp["verified"], cmp, log if cmp["verified"] else log + "\n\n" + report
