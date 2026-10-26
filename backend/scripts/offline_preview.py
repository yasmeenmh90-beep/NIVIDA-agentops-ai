"""OFFLINE UI PREVIEW ONLY. Runs the backend with a scripted stand-in for Nemotron so you can work
on the dashboard without a Nebius key or credits. Only bug 1 has a script; this is NOT the real
agent and must not be used for the demo video or submission.

    cd backend && python scripts/offline_preview.py     # then open the frontend
"""

import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))
os.environ.setdefault("SANDBOX_MODE", "subprocess")

import uvicorn  # noqa: E402

from app import llm  # noqa: E402
from app.main import app  # noqa: E402
from fake_llm import GOOD_FIX_BUG1, INVESTIGATE_BUG1, ScriptedLLM  # noqa: E402


class Replaying(ScriptedLLM):
    """Restarts the bug-1 script for every new incident."""

    def chat(self, messages, tools, model):
        system = messages[0]["content"]
        if "Detector agent" not in system and "TypeError" not in messages[1]["content"]:
            raise llm.LLMError("offline preview only has a script for bug 1; set NEBIUS_API_KEY for real runs")
        if len(messages) == 2 and "Investigator agent" in system:
            self.scripts["investigator"] = list(INVESTIGATE_BUG1)
        if len(messages) == 2 and "Fixer agent" in system:
            self.scripts["fixer"] = [GOOD_FIX_BUG1]
        import time
        time.sleep(1.2)  # make the timeline visibly progress
        return super().chat(messages, tools, model)


llm.set_client(Replaying({}, summary="[offline preview] Scripted summary. Real runs use Nemotron on Token Factory."))

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
