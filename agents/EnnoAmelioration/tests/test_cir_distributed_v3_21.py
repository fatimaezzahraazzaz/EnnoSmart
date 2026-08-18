from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_celery_worker_files_exist():
    assert (ROOT / "backend_api" / "workers" / "celery_app.py").exists()
    assert (ROOT / "backend_api" / "workers" / "cir_tasks_v321.py").exists()
    assert (ROOT / "backend_api" / "workers" / "cir_graph_v321.py").exists()


def test_windows_worker_uses_the_project_virtualenv_explicitly():
    path = ROOT / "start_cir_worker_windows.ps1"
    text = path.read_text(encoding="utf-8")
    assert 'C:\\EnnoSmart\\.venv\\Scripts\\python.exe' in text
    assert "& $pythonExe -m celery" in text


def test_router_full_document_is_background():
    path = ROOT / "backend_api" / "routers" / "improvement.py"
    text = path.read_text(encoding="utf-8")
    assert "should_background_message(" in text
    assert "enqueue_full_cir_job(" in text
    assert '"background": True' in text


def test_cir_dispatch_forces_the_dedicated_broker_connection():
    path = ROOT / "backend_api" / "services" / "cir_background_service_v321.py"
    text = path.read_text(encoding="utf-8")
    assert "connection_for_write(" in text
    assert "CIR_BROKER_URL" in text
    assert "connection=connection" in text
    assert "task_id=job_id" in text


def test_cir_status_is_written_before_task_publication():
    path = ROOT / "backend_api" / "services" / "cir_background_service_v321.py"
    text = path.read_text(encoding="utf-8")
    status_position = text.index("queued = write_background_status(")
    publish_position = text.index("celery_app.send_task(", status_position)
    assert status_position < publish_position


def test_background_direct_resume_exists():
    path = ROOT / "backend_api" / "services" / "improvement_service.py"
    text = path.read_text(encoding="utf-8")
    assert "def background_advance_full_cir(" in text
    assert "_progressive_advance(" in text


def test_no_human_continue_prompt_for_full_cir_checkpoint():
    path = ROOT / "backend_api" / "services" / "improvement_service.py"
    text = path.read_text(encoding="utf-8")
    assert "Le traitement continue automatiquement en arrière-plan." in text


def test_v320_auto_evidence_still_present():
    path = ROOT / "backend_api" / "services" / "improvement_service.py"
    text = path.read_text(encoding="utf-8")
    assert "auto_evidence_selector_v320" in text
    assert "select_sources(" in text
    assert "build_traceable_evidence(" in text
