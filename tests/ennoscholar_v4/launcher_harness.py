"""Runs the real local launcher (start_backend.py) offline, for test_launcher_recovery.py.

Only two things differ from a real start: the agent is a simulated writer (no provider is
ever called) and the watchdog interval is short. The launcher file itself runs unchanged,
on the temporary database, profile and attestation directory given by its
ENNOSCHOLAR_V4_LAUNCH_* variables.
"""
import os
import runpy
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend_api")]
# The database module binds its engine at import: the same URL the launcher sets later.
os.environ["DATABASE_URL"] = "sqlite:///" + Path(os.environ["ENNOSCHOLAR_V4_LAUNCH_DB"]).as_posix()

from agents.EnnoScholarV4 import agent as agent_module, service as service_module  # noqa: E402
from agents.EnnoScholarV4.finance import BudgetRefused  # noqa: E402

PER_RUN = int(os.environ.get("HARNESS_PER_RUN", "2"))
COST = float(os.environ.get("HARNESS_COST", "1.0"))
RESERVE = float(os.environ.get("HARNESS_RESERVE", "1.2"))


class SimulatedWriter:
    """Saves PER_RUN sections per run through the real writing progress and the real
    per-call ledger (admitted for RESERVE, settled at COST). No provider call."""

    def __init__(self, config=None, **_):
        self.config, self.meter = config, None

    def run(self, data, emit, cancelled):
        from agents.EnnoScholarV4.instructions import capture_instructions, current_request
        from agents.EnnoScholarV4.writing_progress import begin_writing, refresh_writing, resume_writing
        segment = data.get("writing_segment")
        request = current_request(data)
        capture_instructions(data, request)
        resuming = bool(segment and segment.get("progress_id"))
        progress = resume_writing(data, segment) if resuming else begin_writing(data, request["content"])
        if not resuming:
            data["turn_goal"] = {"deliverable": "draft", "reading_depth": "cards", "references": {"message_id": request["id"]}}
        written = 0
        for sid in list((progress or {}).get("target_ids", [])):
            if written >= PER_RUN or cancelled():
                break
            if sid in progress["saved_draft_ids"] or sid in progress.get("blocked", {}):
                continue
            try:
                ticket = self.meter.admit("section", sid, sid, "gpt-5.6-sol", RESERVE, {})
            except BudgetRefused:
                return {"answer": "Budget.", "partial": True, "reason": "budget_refused"}
            self.meter.ledger.settle(ticket, COST)
            section = next(s for s in data["outline"]["sections"] if s["id"] == sid)
            section.setdefault("drafts", []).append({"id": uuid.uuid4().hex, "text": f"Texte {sid}"})
            written += 1
            refresh_writing(data)
            emit({"tool": "write_section", "status": "completed"})
        progress = refresh_writing(data, final=True)
        done = progress["status"] == "draft_complete"
        return {"answer": "Brouillon complet." if done else "Brouillon partiel.", "partial": not done,
            "reason": None if done else "exploration_time_budget"}


agent_module.ScholarChatAgent = SimulatedWriter
service_module.WATCHDOG_INTERVAL = float(os.environ.get("HARNESS_WATCHDOG_INTERVAL", "0.3"))
runpy.run_path(os.environ["HARNESS_LAUNCHER"], run_name="__main__")
