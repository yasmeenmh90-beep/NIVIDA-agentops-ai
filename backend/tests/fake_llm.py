"""Scripted stand-in for Nemotron, used only in tests.

It plays back fixed tool calls so the pipeline plumbing (tool loop, patch validation,
sandboxed pytest, retries, approval, GitHub PR, revisions) can be tested without network
access. It does NOT demonstrate model quality; that requires a real Nebius key.
"""

import json
import uuid

from app.llm import LLMResponse, ToolCall


def call(name, **args):
    return ToolCall(id=f"call_{uuid.uuid4().hex[:8]}", name=name, arguments=args)


class ScriptedLLM:
    def __init__(self, scripts: dict[str, list[list[ToolCall]]], summary="Orders API fails."):
        """scripts: agent key ('investigator'|'fixer') -> list of turns; a turn is a list of tool calls."""
        self.scripts = {k: list(v) for k, v in scripts.items()}
        self.summary = summary
        self.calls: list[dict] = []
        self.first_user_message: dict[str, str] = {}

    def chat(self, messages, tools, model, tool_choice=None):
        system = messages[0]["content"]
        agent = "investigator" if "Investigator agent" in system else "fixer" if "Fixer agent" in system else "triage"
        self.calls.append({"agent": agent, "n_messages": len(messages), "model": model,
                           "revision": "REVISION of an open pull request" in system})
        if len(messages) == 2:
            self.first_user_message.setdefault(agent, messages[1]["content"])
            if agent == "fixer" and "REVISION" in system:
                self.first_user_message["reviser"] = messages[1]["content"]
        if agent == "triage":
            return LLMResponse(content=self.summary, tool_calls=[], prompt_tokens=50, completion_tokens=10)
        turn = self.scripts[agent].pop(0)
        raw = {"role": "assistant", "content": "", "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
            for c in turn
        ]}
        return LLMResponse(content="", tool_calls=turn, prompt_tokens=400, completion_tokens=80, raw_message=raw)


INVESTIGATE_BUG1 = [
    [call("read_file", path="orders/pricing.py")],
    [call("web_search", query="python dict.get returns None TypeError unsupported operand")],
    [call("submit_root_cause", file="orders/pricing.py", line=12, function="discount_rate",
          root_cause="discount_rate() returns DISCOUNT_CODES.get(code), which is None when no discount code is "
                     "sent. order_total() then computes 1 - None, raising TypeError.",
          evidence=["orders/pricing.py:12 `return DISCOUNT_CODES.get(code)` has no default",
                    "traceback ends in order_total: `subtotal * (1 - rate)`"],
          fix_suggestion="Return 0.0 when the code is missing or unknown.", confidence="high")],
]

REGRESSION_TEST = '''from fastapi.testclient import TestClient

from orders.main import app


def test_order_without_discount_code():
    r = TestClient(app).post("/orders", json={"customer_id": "c", "items": [{"sku": "A", "qty": 2, "unit_price": 5.0}]})
    assert r.status_code == 201
    assert r.json()["total"] == 10.0
'''
REGRESSION_TEST_PATH = "tests/test_regression_missing_discount.py"
REGRESSION_TEST_ID = f"{REGRESSION_TEST_PATH}::test_order_without_discount_code"

# Passes on the buggy code too, so it does not reproduce the bug.
NON_REPRODUCING_TEST = '''from orders.pricing import order_total


def test_discount_applied():
    assert order_total([{"qty": 1, "unit_price": 10.0}], "WELCOME10") == 9.0
'''

FIX_EDIT = {"path": "orders/pricing.py", "old_string": "    return DISCOUNT_CODES.get(code)",
            "new_string": "    return DISCOUNT_CODES.get(code, 0.0) if code else 0.0"}
TEST_EDIT = {"path": REGRESSION_TEST_PATH, "old_string": "", "new_string": REGRESSION_TEST}

GOOD_FIX_BUG1 = [call("submit_patch", edits=[FIX_EDIT, TEST_EDIT],
                      explanation="Missing or unknown discount codes now mean no discount instead of None.")]

NO_TEST_FIX = [call("submit_patch", edits=[FIX_EDIT], explanation="Fix without a test.")]

NON_REPRO_FIX = [call("submit_patch", edits=[FIX_EDIT, {"path": "tests/test_regression_discount_applied.py",
                                                        "old_string": "", "new_string": NON_REPRODUCING_TEST}],
                      explanation="Fix with a test that does not reproduce the bug.")]

USELESS_FIX_BUG1 = [call("submit_patch", edits=[{
    "path": "orders/pricing.py",
    "old_string": '    """Return the discount rate for a code (0.10 means 10% off)."""',
    "new_string": '    """Return the discount rate for a code (e.g. 0.10 = 10% off)."""',
}, TEST_EDIT], explanation="Docstring tweak (does not fix anything).")]

INVALID_FIX = [call("submit_patch", edits=[{
    "path": "orders/pricing.py", "old_string": "this text does not exist", "new_string": "x",
}], explanation="bad")]

# Revision requested by a reviewer: "use `code or ''` instead of the conditional"
REVISION_FIX = [call("submit_patch", edits=[{
    "path": "orders/pricing.py",
    "old_string": "    return DISCOUNT_CODES.get(code, 0.0) if code else 0.0",
    "new_string": "    return DISCOUNT_CODES.get(code or \"\", 0.0)",
}], explanation="Simplified the lookup as requested by the reviewer.")]
