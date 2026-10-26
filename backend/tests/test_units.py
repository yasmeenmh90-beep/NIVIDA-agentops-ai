import json

import pytest

from app.agents import detector
from app.tools import repo
from app.tools.patching import PatchError, build_patch

DEMO_TB = (
    'Traceback (most recent call last):\n'
    '  File "/srv/app/demo-app/orders/main.py", line 61, in list_orders\n'
    '    return paginate(store.list_orders(), page, size)\n'
    '  File "/srv/app/demo-app/orders/pagination.py", line 17, in paginate\n'
    '    "first_id": items[start]["id"],\n'
    'IndexError: list index out of range\n'
)


def test_parse_json_line_maps_frames_by_suffix():
    line = json.dumps({"method": "GET", "path": "/orders", "service": "orders-api", "traceback": DEMO_TB})
    [ev] = detector.parse_lines([line])
    assert ev.exc_type == "IndexError"
    assert ev.culprit.repo_file == "orders/pagination.py"
    assert ev.culprit.func == "paginate"
    assert ev.signature == "IndexError|orders/pagination.py:paginate"
    assert detector.base_severity(ev) == "medium"


def test_parse_plain_text_traceback_among_other_lines():
    lines = ["INFO started", *DEMO_TB.splitlines(), "INFO next request"]
    [ev] = detector.parse_lines(lines)
    assert ev.message == "list index out of range"


def test_keyerror_message():
    tb = DEMO_TB.replace("IndexError: list index out of range", "KeyError: 'customer_id'")
    [ev] = detector.parse_lines(tb.splitlines())
    assert ev.exc_type == "KeyError" and ev.message == "'customer_id'"


def test_severity_escalates_with_occurrences():
    assert detector.escalate("medium", 1) == "medium"
    assert detector.escalate("medium", 5) == "high"
    assert detector.escalate("high", 30) == "critical"


def test_repo_tools_block_path_traversal():
    with pytest.raises(repo.RepoError):
        repo.read_file({"path": "../backend/app/config.py"})
    with pytest.raises(repo.RepoError):
        repo.read_file({"path": "/etc/passwd"})


def test_patch_generates_diff():
    p = build_patch([{"path": "orders/pricing.py", "old_string": "    return DISCOUNT_CODES.get(code)",
                      "new_string": "    return DISCOUNT_CODES.get(code, 0.0)"}])
    assert "-    return DISCOUNT_CODES.get(code)" in p.diff
    assert "+    return DISCOUNT_CODES.get(code, 0.0)" in p.diff
    assert p.changed_lines == 2


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


def test_patch_line_limit():
    big = "\n".join(f"# line {i}" for i in range(100))
    with pytest.raises(PatchError, match="limit"):
        build_patch([{"path": "tests/test_new_regression.py", "old_string": "", "new_string": big}])
