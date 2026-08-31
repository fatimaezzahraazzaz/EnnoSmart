"""A new diagnostic starts with no previous agent runs for that project."""
from pathlib import Path
from functools import wraps
from threading import Lock
import shutil

from sqlalchemy import MetaData, Table, delete, inspect, or_, select
from db.models import Article, DiagnosticRun, ScholarRun, Verrou

_project_locks = {}
_locks_guard = Lock()


def exclusive_project_diagnostic(function):
    """Do not let a second launch erase a diagnostic still being generated."""
    @wraps(function)
    def wrapped(db, project, *args, **kwargs):
        with _locks_guard:
            lock = _project_locks.setdefault(int(project.id), Lock())
        if not lock.acquire(blocking=False):
            raise RuntimeError('Une préparation ou un diagnostic est déjà en cours pour ce projet.')
        try:
            return function(db, project, *args, **kwargs)
        finally:
            lock.release()
    return wrapped


def delete_previous_agent_runs(db, project_id: int) -> dict:
    """Delete dependent tests first; never delete documents or other projects."""
    project_id = int(project_id)
    runs = select(DiagnosticRun.id).where(DiagnosticRun.project_id == project_id)
    locks = select(Verrou.id).where(Verrou.diagnostic_run_id.in_(runs))
    scholar = select(ScholarRun.id).where(ScholarRun.project_id == project_id)
    counts = {}
    connection = db.connection()
    available = set(inspect(connection).get_table_names())
    metadata = MetaData()
    if {'guided_research_sessions', 'guided_research_messages'} <= available:
        sessions = Table('guided_research_sessions', metadata, autoload_with=connection)
        messages = Table('guided_research_messages', metadata, autoload_with=connection)
        session_ids = select(sessions.c.id).where(sessions.c.project_id == project_id)
        counts['guided_research_messages'] = db.execute(delete(messages).where(messages.c.session_id.in_(session_ids))).rowcount
        counts['guided_research_sessions'] = db.execute(delete(sessions).where(sessions.c.project_id == project_id)).rowcount
    for name in ('checkpoint_writes', 'checkpoint_blobs', 'checkpoints'):
        if name in available:
            table = Table(name, metadata, autoload_with=connection)
            counts[name] = db.execute(delete(table).where(table.c.thread_id.like(f'ennoscholar-soa-p{project_id}-%'))).rowcount
    for model, condition in (
        (Article, or_(Article.scholar_run_id.in_(scholar), Article.verrou_id.in_(locks))),
        (ScholarRun, ScholarRun.project_id == project_id),
        (Verrou, Verrou.diagnostic_run_id.in_(runs)),
        (DiagnosticRun, DiagnosticRun.project_id == project_id),
    ):
        counts[model.__tablename__] = db.execute(delete(model).where(condition).execution_options(synchronize_session=False)).rowcount
    db.flush()
    return {'project_id': project_id, 'deleted': counts, 'manual_locks_preserved': False,
            'documents_deleted': False, 'chroma_deleted': False}


def delete_previous_agent_files(project_store) -> list[str]:
    """Preserve prepared NLP/RAG and the preparation report on agent-only reruns."""
    root = Path(project_store.project_dir).resolve()
    targets = [root / name for name in (
        'ennodiagnostic', 'ennoscholar', 'document_compare', 'cir_memory',
        'selected_verrous_for_scholar.json', 'comparison_cir_vs_raw.json',
        'diagnostic_ennodiagnostic.json', 'ennodiagnostic_report.json', 'ai_detection_report.json',
    )]
    diagnostics = Path(project_store.diagnostics_dir).resolve()
    # Validate every path before the first deletion, including symlink targets.
    targets.extend(p for p in diagnostics.glob('*') if p.name != 'prepare_sources_report.json')
    for path in targets:
        resolved = path.resolve()
        if resolved == root or not resolved.is_relative_to(root):
            raise RuntimeError(f'Refus de supprimer un artefact hors projet : {path}')
    removed = []
    for path in targets:
        if path.is_symlink():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
        else:
            continue
        removed.append(str(path))
    return removed


def reset_previous_agent_runs(db, project, project_store, *, status='Diagnostic en cours') -> dict:
    try:
        report = delete_previous_agent_runs(db, project.id)
        report['files_removed'] = delete_previous_agent_files(project_store)
        # The application also supports a separate output directory. Clear its
        # generated report copies, otherwise the file fallback can revive a run.
        from core.config import settings
        from services.file_service import project_output_dir
        output = project_output_dir(project).resolve()
        output_base = settings.ai_output_root_path.resolve()
        if output != Path(project_store.project_dir).resolve() and output.exists():
            if output == output_base or not output.is_relative_to(output_base):
                raise RuntimeError(f'Refus de nettoyer un dossier de sortie hors périmètre : {output}')
            from types import SimpleNamespace
            report['files_removed'].extend(delete_previous_agent_files(
                SimpleNamespace(project_dir=output, diagnostics_dir=output/'diagnostics')
            ))
        project.status = status
        # Commit now: a failed new run must not resurrect the old results.
        db.commit()
        print(f'[EnnoDiagnostic][RESET] {report}', flush=True)
        return report
    except Exception:
        db.rollback()
        raise
