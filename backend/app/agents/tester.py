"""Tester: runs the suite in the sandbox before and after the patch and decides if it is verified.

"before" = the unpatched code at the analysed commit, plus any NEW test files from the patch
(so a regression test must fail first). "after" = the patched code. See sandbox.compare for
the exact rule; by default a fix only counts if it ships a regression test that reproduces
the bug.
"""

from __future__ import annotations

from ..config import get_settings
from ..tools.repo import is_test_path, on_disk
from ..tools.patching import BuiltPatch
from ..tools.sandbox import TestRun, compare, run_tests
from .recorder import StepRecorder




def failure_report(run: TestRun, cmp: dict) -> str:
    lines = ["Verification FAILED:"] + [f"- {p}" for p in cmp.get("problems", [])]
    lines.append(f"Fixed: {cmp['fixed']}. New failures: {cmp['regressions']}. "
                 f"Regression tests that reproduced the bug: {cmp.get('regression_tests')}. "
                 f"Regression tests that did NOT reproduce it (passed on unpatched code): {cmp.get('non_reproducing_tests')}.")
    for t in (cmp["regressions"] or []) + [t for t in run.failed if t not in cmp["regressions"]]:
        if t in run.failure_messages:
            lines.append(f"- {t}: {run.failure_messages[t]}")
    lines.append("--- pytest output after the patch (tail) ---")
    lines.append(run.log[-2500:])
    return "\n".join(lines)


def verify_patch(incident_id: int, patch: BuiltPatch, attempt: int, baseline: TestRun,
                 label: str = "") -> tuple[bool, dict, str]:
    rec = StepRecorder(incident_id, "tester",
                       f"{label or f'Attempt {attempt}'}: {len(patch.files)} file(s), {patch.changed_lines} changed lines")
    try:
        new_tests = {p: c for p, c in patch.files.items() if is_test_path(p) and not on_disk(p)}
        if new_tests:
            before = run_tests(new_tests)
            rec.tool_call("sandbox.run_tests", {"variant": "unpatched code + new regression tests"},
                          f"passed={len(before.passed)} failed={len(before.failed)} runner={before.runner}")
        else:
            before = baseline
        after = run_tests(patch.files)
        rec.tool_call("sandbox.run_tests", {"variant": "patched"},
                      f"passed={len(after.passed)} failed={len(after.failed)} runner={after.runner}")
        cmp = compare(before, after, sorted(new_tests), get_settings().require_regression_test)
        cmp["before_failed"] = before.failed
        cmp["after_failed"] = after.failed
        log = (
            f"=== BEFORE ({'unpatched + new regression tests' if new_tests else 'unpatched'}) exit={before.exit_code} ===\n"
            f"{before.log[-3000:]}\n=== AFTER (patched) exit={after.exit_code} ===\n{after.log[-3000:]}"
        )
    except Exception as e:
        rec.finish({"error": str(e), "attempt": attempt}, status="error")
        raise
    rec.finish(cmp | {"attempt": attempt})
    if cmp["verified"]:
        return True, cmp, log
    return False, cmp, log + "\n\n" + failure_report(after, cmp)
