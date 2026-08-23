from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import threading
import time


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend_api"
for path in (str(ROOT), str(BACKEND)):
    if path not in sys.path:
        sys.path.insert(0, path)


def test_llm_capacity_allows_parallel_calls_but_respects_limit(monkeypatch):
    from modules.LLM import llm_concurrency

    monkeypatch.setenv("ENNOSMART_LLM_MAX_CONCURRENCY", "3")
    monkeypatch.setenv("ENNOSMART_LLM_QUEUE_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("ENNOSMART_LLM_DISTRIBUTED_LIMITER", "0")
    monkeypatch.setattr(llm_concurrency, "_CONFIG", None)
    monkeypatch.setattr(llm_concurrency, "_GATE", None)

    active = 0
    peak = 0
    guard = threading.Lock()

    def operation(index: int) -> int:
        nonlocal active, peak
        with llm_concurrency.llm_capacity_slot(f"test:{index}"):
            with guard:
                active += 1
                peak = max(peak, active)
            time.sleep(0.04)
            with guard:
                active -= 1
        return index

    with ThreadPoolExecutor(max_workers=12) as pool:
        assert sorted(pool.map(operation, range(12))) == list(range(12))

    assert peak == 3
    status = llm_concurrency.llm_concurrency_status()
    assert status["active"] == 0
    assert status["started"] == 12


def test_llm_generation_metadata_is_isolated_per_thread(monkeypatch):
    from modules.LLM import llm_client

    monkeypatch.setattr(llm_client, "_CONFIG", {"ENNOSMART_LLM_PROVIDER": "ollama"})
    client = llm_client.LLMClient()
    barrier = threading.Barrier(2)

    def write_and_read(name: str) -> str:
        client._last_generation_meta = {"request_name": name}
        barrier.wait(timeout=2)
        return str(client.get_last_generation_meta().get("request_name"))

    with ThreadPoolExecutor(max_workers=2) as pool:
        values = set(pool.map(write_and_read, ("session-a", "session-b")))

    assert values == {"session-a", "session-b"}


def test_same_session_is_serialized_but_different_sessions_are_not(monkeypatch):
    from core import execution_lock

    monkeypatch.setattr(execution_lock.settings, "SESSION_LOCK_DISTRIBUTED", False)
    entered = threading.Event()
    release = threading.Event()

    def hold_first_session() -> None:
        with execution_lock.session_execution_lock("guided", "session-a"):
            entered.set()
            assert release.wait(timeout=2)

    worker = threading.Thread(target=hold_first_session)
    worker.start()
    assert entered.wait(timeout=2)

    try:
        try:
            with execution_lock.session_execution_lock("guided", "session-a"):
                raise AssertionError("Le même session_id ne doit pas entrer deux fois.")
        except execution_lock.SessionBusyError:
            pass

        # Une autre conversation ne doit pas attendre la première.
        with execution_lock.session_execution_lock("guided", "session-b"):
            assert True
    finally:
        release.set()
        worker.join(timeout=2)

    assert not worker.is_alive()


def test_cir_worker_has_thread_safe_job_identity_and_parallel_pool():
    task_source = (BACKEND / "workers" / "cir_tasks_v321.py").read_text(
        encoding="utf-8"
    )
    graph_source = (BACKEND / "workers" / "cir_graph_v321.py").read_text(
        encoding="utf-8"
    )
    launcher = (ROOT / "start_cir_worker_windows.ps1").read_text(
        encoding="utf-8"
    )

    assert "ENNOSMART_CURRENT_CELERY_TASK_ID" not in task_source
    assert "ENNOSMART_CURRENT_CELERY_TASK_ID" not in graph_source
    assert '"job_id": task_id' in task_source
    assert "--pool=threads" in launcher
    assert "ENNOSMART_CIR_WORKER_CONCURRENCY" in launcher

    scholar_launcher = (BACKEND / "worker" / "run_local_worker.py").read_text(
        encoding="utf-8"
    )
    assert 'ENNOSCHOLAR_CELERY_CONCURRENCY", "4"' in scholar_launcher
