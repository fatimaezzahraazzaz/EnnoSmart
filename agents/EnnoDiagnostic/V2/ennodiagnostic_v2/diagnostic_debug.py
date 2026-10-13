"""Server-side debug archive; never embeds raw responses in consultant reports."""
from __future__ import annotations
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class DiagnosticDebug:
    def __init__(self, directory=None):
        root = Path(directory) if directory else Path(__file__).resolve().parents[4] / "ennodiagnostic_debug"
        self.directory = root / (datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid4().hex[:12])
        self.directory.mkdir(parents=True, exist_ok=False)
        self.path = self.directory / "trace.jsonl"
        self.sequence = 0

    def record(self, stage, value):
        self.sequence += 1
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"sequence": self.sequence, "stage": stage,
                "time": datetime.now(timezone.utc).isoformat(), "data": value},
                ensure_ascii=False, default=str) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


def record(owner, stage, value):
    sink = getattr(owner, "debug_sink", None)
    if sink is not None:
        sink.record(stage, value)
