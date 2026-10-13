import os
import socket
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend_api")]
os.environ["DATABASE_URL"] = "sqlite:///:memory:"


@pytest.fixture(autouse=True)
def no_paid_or_external_calls(monkeypatch):
    original = socket.socket.connect
    def block(sock, address):
        if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
            return original(sock, address)
        raise AssertionError("External network is forbidden in mocked V4 tests; use research/evaluate.py --live")
    monkeypatch.setattr(socket.socket, "connect", block)
    from openai.resources.responses.responses import Responses
    monkeypatch.setattr(Responses, "create", lambda *a, **k: (_ for _ in ()).throw(AssertionError("Paid API forbidden")))
