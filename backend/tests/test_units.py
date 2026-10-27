import json
from pathlib import Path

import pytest

from app.agents import detector
from app.context import ProjectContext, use_project
from app.tools import repo
from app.tools.patching import PatchError, build_patch

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample_service"

DEMO_TB = (
    'Traceback (most recent call last):\n'
    '  File "/srv/app/services/orders/orders/main.py", line 61, in list_orders\n'
    '    return paginate(store.list_orders(), page, size)\n'
    '  File "/srv/app/services/orders/orders/pagination.py", line 17, in paginate\n'
    '    "first_id": items[start]["id"],\n'
    'IndexError: list index out of range\n'
)


@pytest.fixture(autouse=True)
def project_ctx():
    ctx = ProjectContext(
        id=0, name="unit", checkout=FIXTURE, app_root=FIXTURE, app_path="", python="python", env_bin=None,
        test_command="python -m pytest -q", sandbox_mode="subprocess", sandbox_image="",
        protected=[".github/", "requirements.txt", "conftest.py", "pytest.ini"],
    )
    with use_project(ctx):
        yield ctx


@pytest.mark.parametrize("key", ["traceback", "exc_info", "stack_trace", "exception"])
def test_parse_json_line_variants(key):
    line = json.dumps({"method": "GET", "path": "/orders", "service": "orders-api", key: DEMO_TB})
    [ev] = detector.parse_lines([line])
    assert ev.exc_type == "IndexError"
    assert ev.culprit.repo_file == "orders/pagination.py"
    assert ev.signature == "IndexError|orders/pagination.py:paginate"
    assert detector.base_severity(ev) == "medium"


def test_parse_plain_text_traceback_among_other_lines():
    lines = ["INFO started", *DEMO_TB.splitlines(), "INFO next request"]
    [ev] = detector.parse_lines(lines)
    assert ev.message == "list index out of range"


def test_frames_outside_repo_are_ignored_for_culprit():
    tb = DEMO_TB.replace("/srv/app/services/orders/orders/pagination.py", "/usr/lib/python3/site-packages/x.py")
    [ev] = detector.parse_lines(tb.splitlines())
    assert ev.culprit.repo_file == "orders/main.py"


def test_severity_escalates_with_occurrences():
    assert detector.escalate("medium", 1) == "medium"
    assert detector.escalate("medium", 5) == "high"
    assert detector.escalate("high", 30) == "critical"


def test_repo_tools_block_path_traversal():
    with pytest.raises(repo.RepoError):
        repo.read_file({"path": "../../app/config.py"})
    with pytest.raises(repo.RepoError):
        repo.read_file({"path": "/etc/passwd"})


def test_patch_generates_diff():
    p = build_patch([{"path": "orders/pricing.py", "old_string": "    return DISCOUNT_CODES.get(code)",
                      "new_string": "    return DISCOUNT_CODES.get(code, 0.0)"}])
    assert "+    return DISCOUNT_CODES.get(code, 0.0)" in p.diff and p.changed_lines == 2


@pytest.mark.parametrize("edit,msg", [
    ({"path": "orders/pricing.py", "old_string": "nope", "new_string": "x"}, "not found"),
    ({"path": "orders/store.py", "old_string": "    ", "new_string": "  "}, "occurs"),
    ({"path": "requirements.txt", "old_string": "fastapi", "new_string": "x"}, "only .py"),
    ({"path": "tests/conftest.py", "old_string": "import os", "new_string": "x"}, "protected"),
    ({"path": "tests/test_orders.py", "old_string": "def test_health", "new_string": "def _skip"}, "existing tests"),
    ({"path": "../x.py", "old_string": "", "new_string": "x"}, "escapes"),
])
def test_patch_policy_rejections(edit, msg):
    with pytest.raises(PatchError, match=msg):
        build_patch([edit])


def test_patch_line_limits_code_and_tests_separately():
    big_test = "\n".join(f"# line {i}" for i in range(150))
    with pytest.raises(PatchError, match="regression test is 150 lines"):
        build_patch([{"path": "tests/test_new_regression.py", "old_string": "", "new_string": big_test}])
    ok = build_patch([{"path": "tests/test_new_regression.py", "old_string": "", "new_string": "\n".join(big_test.splitlines()[:50])}])
    assert ok.new_test_files == ["tests/test_new_regression.py"] and ok.code_lines == 0
    big_code = "\n".join(f"x{i} = {i}" for i in range(60))
    with pytest.raises(PatchError, match="application code"):
        build_patch([{"path": "orders/new_module.py", "old_string": "", "new_string": big_code}])


def test_overlay_is_seen_by_tools_and_patches(project_ctx):
    import dataclasses
    from app.context import use_project as use

    pr_version = (FIXTURE / "orders/pricing.py").read_text().replace(
        "DISCOUNT_CODES.get(code)", "DISCOUNT_CODES.get(code, 0.0)")
    reg = "def test_x():\n    assert True\n"
    with use(dataclasses.replace(project_ctx, overlay={"orders/pricing.py": pr_version,
                                                       "tests/test_regression_x.py": reg})):
        assert "get(code, 0.0)" in repo.read_file({"path": "orders/pricing.py"})
        assert "tests/test_regression_x.py" in repo.list_files()
        # the agent's own regression test (not in the clone) may be revised
        p = build_patch([{"path": "tests/test_regression_x.py", "old_string": "assert True", "new_string": "assert 1"}])
        assert p.new_test_files == ["tests/test_regression_x.py"]


# ---------------- verification rule ----------------

from app.tools.sandbox import TestRun, compare  # noqa: E402
TestRun.__test__ = False

REG = "tests/test_regression_bug.py"


def run(passed=(), failed=(), err=False):
    return TestRun(runner="subprocess", exit_code=1 if failed else 0, passed=list(passed), failed=list(failed),
                   collection_error=err)


def test_compare_requires_reproducing_regression_test():
    before = run(["tests/test_a.py::ok"], [f"{REG}::test_bug"])
    after = run(["tests/test_a.py::ok", f"{REG}::test_bug"])
    c = compare(before, after, [REG], True)
    assert c["verified"] and c["regression_tests"] == [f"{REG}::test_bug"]


def test_compare_rejects_test_that_passes_on_buggy_code():
    before = run(["tests/test_a.py::ok", f"{REG}::test_bug"])
    after = run(["tests/test_a.py::ok", f"{REG}::test_bug"])
    c = compare(before, after, [REG], True)
    assert not c["verified"] and "does not reproduce" in c["problems"][0]


def test_compare_rejects_regressions_and_missing_test():
    before = run(["tests/test_a.py::ok"], [f"{REG}::test_bug"])
    after = run([f"{REG}::test_bug"], ["tests/test_a.py::ok"])
    assert "now fail" in " ".join(compare(before, after, [REG], True)["problems"])
    assert "no regression test" in compare(run(), run(), [], True)["problems"][0]


def test_compare_legacy_rule_without_requirement():
    c = compare(run([], ["tests/test_a.py::bug"]), run(["tests/test_a.py::bug"]), [], False)
    assert c["verified"] and c["fixed"] == ["tests/test_a.py::bug"]


def test_junit_xunit1_nodeids_with_classes(tmp_path):
    from app.tools.sandbox import _parse_junit

    (tmp_path / "j.xml").write_text(
        '<testsuites><testsuite>'
        '<testcase classname="tests.test_x.TestCart" name="test_a" file="tests/test_x.py"/>'
        '<testcase classname="tests.test_x" name="test_b" file="tests/test_x.py"><failure message="boom"/></testcase>'
        '</testsuite></testsuites>')
    passed, failed, msgs = _parse_junit(tmp_path / "j.xml")
    assert passed == ["tests/test_x.py::TestCart::test_a"]
    assert failed == ["tests/test_x.py::test_b"] and msgs[failed[0]] == "boom"


# ---------------- sentry conversion ----------------

def test_sentry_event_becomes_detectable_traceback():
    from app.routers.webhooks import sentry_event_to_lines

    payload = {"action": "triggered", "data": {"event": {
        "project_name": "orders-api",
        "request": {"method": "GET", "url": "https://shop.example.com/orders?page=last"},
        "exception": {"values": [{"type": "IndexError", "value": "list index out of range", "stacktrace": {"frames": [
            {"abs_path": "/srv/app/orders/main.py", "lineno": 61, "function": "list_orders",
             "context_line": "    return paginate(store.list_orders(), page, size)"},
            {"abs_path": "/srv/app/orders/pagination.py", "lineno": 17, "function": "paginate",
             "context_line": '        "first_id": items[start]["id"],'},
        ]}}]}}}}
    [line] = sentry_event_to_lines(payload)
    [ev] = detector.parse_lines([line])
    assert ev.exc_type == "IndexError" and ev.method == "GET" and ev.path == "/orders"
    assert ev.culprit.repo_file == "orders/pagination.py" and ev.culprit.func == "paginate"


# ---------------- robustness ----------------

def test_resolve_traceback_style_paths():
    assert repo.resolve("/app/orders/pricing.py") == "orders/pricing.py"
    assert repo.resolve("./orders/pricing.py") == "orders/pricing.py"
    assert "def discount_rate" in repo.read_file({"path": "/srv/x/orders/pricing.py"})
    p = build_patch([{"path": "/app/orders/pricing.py", "old_string": "    return DISCOUNT_CODES.get(code)",
                      "new_string": "    return DISCOUNT_CODES.get(code, 0.0)"}])
    assert list(p.files) == ["orders/pricing.py"]


def test_tool_loop_forces_finish_on_last_round():
    from app import llm
    from app.llm import LLMResponse, ToolCall, run_tool_loop, tool_spec

    class Explorer:
        def __init__(self):
            self.seen = []

        def chat(self, messages, tools, model, tool_choice=None):
            names = [t["function"]["name"] for t in tools]
            self.seen.append((names, tool_choice))
            if tool_choice:  # forced: answer
                return LLMResponse("", [ToolCall("f", "done", {"answer": 42})])
            return LLMResponse("", [ToolCall(f"c{len(self.seen)}", "look", {})])

    fake = Explorer()
    llm.set_client(fake)
    try:
        res = run_tool_loop(system="s", user="u", tools=[tool_spec("look", "", {}, []), tool_spec("done", "", {}, [])],
                            handlers={"look": lambda a: "nothing"}, finish_tool="done", model="m", max_rounds=5)
    finally:
        llm.set_client(None)
    assert res.output == {"answer": 42} and res.rounds == 5
    assert fake.seen[-1] == (["done"], {"type": "function", "function": {"name": "done"}})
