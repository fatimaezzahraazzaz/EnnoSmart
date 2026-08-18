from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_langgraph_topology_present():
    path = ROOT / "backend_api" / "workers" / "cir_graph_v321.py"
    text = path.read_text(encoding="utf-8")
    assert "StateGraph" in text
    assert 'builder.add_node(' in text
    assert '"start"' in text
    assert '"inspect"' in text
    assert '"advance"' in text
    assert "add_conditional_edges" in text


def test_redis_saver_is_used():
    path = ROOT / "backend_api" / "workers" / "cir_tasks_v321.py"
    text = path.read_text(encoding="utf-8")
    assert "RedisSaver.from_conn_string" in text
    assert "checkpointer.setup()" in text
    assert '"thread_id"' in text


def test_celery_redis_visibility_timeout_configured():
    path = ROOT / "backend_api" / "workers" / "celery_app.py"
    text = path.read_text(encoding="utf-8")
    assert "visibility_timeout" in text
    assert "task_acks_late=True" in text
    assert "worker_prefetch_multiplier=1" in text


def test_background_status_endpoint_present():
    path = ROOT / "backend_api" / "routers" / "improvement.py"
    text = path.read_text(encoding="utf-8")
    assert '"/sessions/{session_id}/background"' in text
    assert '"/background/health"' in text


def test_manual_section_path_remains_sync():
    path = ROOT / "backend_api" / "routers" / "improvement.py"
    text = path.read_text(encoding="utf-8")
    assert "session, candidate = send_message(" in text
    assert '"background": False' in text
