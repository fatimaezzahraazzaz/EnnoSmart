from __future__ import annotations

import os
import socket
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend_api")]
os.environ["DATABASE_URL"] = "sqlite:///:memory:"


class OutputItem(dict):
    def model_dump(self, **kwargs):
        return dict(self)


def response(*items):
    return SimpleNamespace(output=[OutputItem(item) for item in items])


def message(text, annotations=None):
    return {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text, "annotations": annotations or []}]}


def call(name, arguments, call_id="call_1"):
    import json
    return {"type": "function_call", "name": name, "arguments": json.dumps(arguments), "call_id": call_id, "id": "fc_" + call_id}


@pytest.fixture(autouse=True)
def forbid_real_network_and_openai(monkeypatch):
    original_connect = socket.socket.connect
    def forbidden(*args, **kwargs):
        raise AssertionError("Real network / paid OpenAI calls are forbidden in automatic V2 tests")
    def local_event_loop_only(sock, address):
        # Windows asyncio implements socketpair using a loopback connection.
        if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
            return original_connect(sock, address)
        return forbidden()
    monkeypatch.setattr(socket.socket, "connect", local_event_loop_only)
    from openai.resources.responses.responses import Responses
    monkeypatch.setattr(Responses, "create", forbidden)
    from contextlib import nullcontext
    monkeypatch.setattr("modules.LLM.llm_concurrency.llm_capacity_slot", lambda *args: nullcontext())


@pytest.fixture
def fake_client():
    client = Mock()
    client.with_options.return_value = client
    return client


def evidence_reference(payload, excerpt, supports):
    """Mock model selects a server-provided evidence ID; never supplies a quote."""
    row = next((row for row in payload["passages"] if row["text"] == excerpt), None)
    return {"evidence_id": row["evidence_id"] if row else "unknown_evidence_id", "supports": supports}
