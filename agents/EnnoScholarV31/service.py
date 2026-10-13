from __future__ import annotations

import copy
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from sqlalchemy import or_, select, update

from .config import ScholarV31Config
from .models import Article
from .session import ScholarV31Session, ScholarV31Run

logger = logging.getLogger(__name__)
TERMINAL = {"completed", "partial", "failed", "cancelled"}


class SessionNotFound(LookupError):
    pass


class SessionBusy(RuntimeError):
    pass


def iso(value):
    return value.isoformat() + "Z" if value else None


class ScholarV31Service:
    def __init__(self, session_factory, config=None, agent_factory=None):
        self.session_factory = session_factory
        self.config = config or ScholarV31Config.from_env()
        self.agent_factory = agent_factory
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="scholar-v3-1")
        self.slots = threading.BoundedSemaphore(4)

    def owned(self, db, owner, sid):
        row = db.scalar(select(ScholarV31Session).where(ScholarV31Session.id == sid, ScholarV31Session.owner_id == owner))
        if row is None:
            raise SessionNotFound("Conversation introuvable.")
        return row

    def create(self, owner, title="Nouvelle conversation"):
        with self.session_factory() as db:
            row = ScholarV31Session(id=uuid.uuid4().hex, owner_id=owner, title=title,
                data={"messages": [], "articles": {}, "research_state": {}, "summary": ""})
            db.add(row)
            db.commit()
            return self.session_read(row)

    def session_read(self, row, detailed=True):
        result = {"id": row.id, "title": row.title, "created_at": iso(row.created_at), "updated_at": iso(row.updated_at),
            "active_run_id": row.active_run_id, "articles_total": len(row.data.get("articles", {})),
            "selected_count": sum(bool(a.get("selected")) for a in row.data.get("articles", {}).values())}
        if detailed:
            stored = row.data.get("articles", {})
            pinned = list(dict.fromkeys([*row.data.get("evaluation", {}).get("recommended_article_ids", []),
                *[aid for aid, a in stored.items() if a.get("selected")], *list(stored)[-24:]]))
            result.update(messages=row.data.get("messages", []), articles=[Article.model_validate(stored[aid]).compact()
                for aid in pinned if aid in stored], research_state=row.data.get("research_state", {}),
                recommended_article_ids=row.data.get("evaluation", {}).get("recommended_article_ids", []), summary=row.data.get("summary", ""))
            result["presented_total"] = len(row.data.get("exposed_article_ids", []))
            result["examined_total"] = len(row.data.get("examined_article_ids", []))
            result["research_gaps"] = row.data.get("research_gaps", [])
            result["comparisons"] = row.data.get("comparisons", [])
        return result

    def sessions(self, owner):
        with self.session_factory() as db:
            rows = db.scalars(select(ScholarV31Session).where(ScholarV31Session.owner_id == owner)
                .order_by(ScholarV31Session.updated_at.desc()).limit(100)).all()
            return [self.session_read(row, False) for row in rows]

    def read(self, owner, sid):
        self.recover_expired(owner, sid)
        with self.session_factory() as db:
            return self.session_read(self.owned(db, owner, sid))

    def articles(self, owner, sid, offset=0, limit=50, query=""):
        with self.session_factory() as db:
            row = self.owned(db, owner, sid)
            articles = list(row.data.get("articles", {}).values())
            if query:
                from .store import ArticleStore
                store = ArticleStore(copy.deepcopy(row.data), self.config.max_articles)
                # This read-only human search creates no GPT exposure event.
                matches, _ = store.search(query, len(articles), False)
                articles = [a.durable() for a in matches]
            return {"total": len(articles), "articles": [Article.model_validate(a).compact() for a in articles[offset:offset + limit]]}

    def article(self, owner, sid, aid):
        with self.session_factory() as db:
            row = self.owned(db, owner, sid)
            if aid not in row.data.get("articles", {}):
                raise SessionNotFound("Article introuvable.")
            return Article.model_validate(row.data["articles"][aid]).compact()

    def memory(self, owner, sid):
        with self.session_factory() as db:
            data = self.owned(db, owner, sid).data
            return {key: data.get(key, []) for key in ("candidate_windows", "exposed_article_ids",
                "examined_article_ids", "candidate_decisions", "comparisons", "research_gaps", "memory_history")}

    def import_pdf(self, owner, sid, content, filename, title="", article_id=""):
        import hashlib
        import fitz
        from agents.EnnoScholarV2.tools.article_reader import pdf_chunks, matches_article
        from agents.EnnoScholarV2.models import Article as LegacyArticle
        from .pdf_store import save_pdf
        from .store import ArticleStore
        # Authenticate and inspect ownership before extracting or writing any bytes.
        with self.session_factory() as db:
            row = self.owned(db, owner, sid)
            if row.active_run_id:
                raise SessionBusy("Attendez la fin de la recherche avant d'importer le PDF.")
            data = copy.deepcopy(row.data)
        if not content.startswith(b"%PDF") or len(content) > 15*1024*1024:
            raise ValueError("PDF requis, taille maximale 15 Mo.")
        chunks, truncated = pdf_chunks(content)
        if not chunks:
            raise ValueError("PDF sans texte exploitable ; OCR non disponible dans V3.1.")
        first_page = chunks[0].text
        store = ArticleStore(data, self.config.max_articles)
        if article_id:
            article = store.article(article_id)
            if not matches_article(LegacyArticle(title=article.title), first_page[:7000]):
                raise ValueError("Le PDF ne correspond pas à l'article sélectionné.")
        else:
            with fitz.open(stream=content, filetype="pdf") as doc:
                metadata_title = (doc.metadata or {}).get("title", "")
            proposed_title = title.strip() or metadata_title.strip() or first_page.splitlines()[0][:1000]
            digest = hashlib.sha256(content).hexdigest()
            ids, _ = store.insert([Article(title=proposed_title, external_ids={"uploaded_pdf": digest},
                sources=["uploaded_pdf"], source_version="uploaded_pdf:"+digest)])
            if not ids:
                raise ValueError("Capacité du corpus atteinte.")
            article = store.article(ids[0])
        saved = save_pdf(content)
        article.fulltext = {"text": "\n\n".join(chunk.text for chunk in chunks), "chunks": [chunk.model_dump() for chunk in chunks],
            "url": "", "pdf_file": saved, "original_filename": filename[:255], "truncated": truncated, "access_level": "fulltext",
            "source_kind": "user_uploaded_pdf", "reason": "PDF fourni par l'utilisateur ; identité bibliographique à vérifier séparément."}
        article.access_level, article.fulltext_available = "fulltext", True
        article.source_version = "pdf_sha256:"+saved.removesuffix(".pdf")
        # Replacing a source invalidates the current analysis; old snapshots retain old evidence.
        article.analysis, article.analysis_basis = {}, None
        store.save()
        with self.session_factory() as db:
            row = db.scalar(select(ScholarV31Session).where(ScholarV31Session.id == sid, ScholarV31Session.owner_id == owner).with_for_update())
            if row.active_run_id:
                raise SessionBusy("Une recherche a démarré ; réessayez l'import après sa fin.")
            current = copy.deepcopy(row.data)
            current.setdefault("articles", {})[article.id] = article.durable()
            row.data, row.updated_at = current, datetime.utcnow()
            db.commit()
        return article.compact()

    def select_article(self, owner, sid, aid, selected):
        with self.session_factory() as db:
            row = db.scalar(select(ScholarV31Session).where(ScholarV31Session.id == sid, ScholarV31Session.owner_id == owner).with_for_update())
            if row is None:
                raise SessionNotFound("Conversation introuvable.")
            data = copy.deepcopy(row.data)
            if aid not in data.get("articles", {}):
                raise SessionNotFound("Article introuvable.")
            if not data["articles"][aid].get("verified"):
                raise ValueError("Référence non vérifiée ; conservation impossible.")
            data["articles"][aid]["selected"] = selected
            data.setdefault("selection_decisions", []).append({"article_id": aid, "selected": selected, "at": iso(datetime.utcnow())})
            row.data, row.updated_at = data, datetime.utcnow()
            db.commit()
            return Article.model_validate(data["articles"][aid]).compact()

    def recover_expired(self, owner, sid):
        with self.session_factory() as db:
            row = self.owned(db, owner, sid)
            if row.active_run_id and row.lease_until and row.lease_until < datetime.utcnow():
                rid = row.active_run_id
                changed = db.execute(update(ScholarV31Session).where(ScholarV31Session.id == sid,
                    ScholarV31Session.active_run_id == rid, ScholarV31Session.lease_until < datetime.utcnow())
                    .values(active_run_id=None, lease_until=None))
                if changed.rowcount:
                    run = db.get(ScholarV31Run, rid)
                    if run and run.status not in TERMINAL:
                        run.status = "partial"
                        run.data = {**run.data, "reason": "worker_interrupted", "answer": "Recherche interrompue ou délai dépassé. Les échanges précédents sont conservés ; relancez votre demande."}
                db.commit()

    def start(self, owner, request):
        self.config.validate()
        if not self.slots.acquire(blocking=False):
            raise SessionBusy("Les recherches V3 sont occupées. Réessayez dans un instant.")
        try:
            sid = request.session_id or self.create(owner)["id"]
            self.recover_expired(owner, sid)
            rid = uuid.uuid4().hex
            with self.session_factory() as db:
                self.owned(db, owner, sid)
                changed = db.execute(update(ScholarV31Session).where(ScholarV31Session.id == sid,
                    ScholarV31Session.owner_id == owner, ScholarV31Session.active_run_id.is_(None)).values(
                    active_run_id=rid, lease_until=datetime.utcnow() + timedelta(seconds=self.config.timeout_seconds + 30)))
                if not changed.rowcount:
                    raise SessionBusy("Une recherche est déjà en cours dans cette conversation.")
                db.add(ScholarV31Run(id=rid, session_id=sid, owner_id=owner, status="queued",
                    data={"activity": [], "usage": {}, "answer": "", "citations": [], "request": request.message}))
                row = db.get(ScholarV31Session, sid)
                data = copy.deepcopy(row.data)
                data.setdefault("messages", []).append({"id": uuid.uuid4().hex, "role": "user", "content": request.message})
                row.data = data
                if row.title == "Nouvelle conversation":
                    row.title = request.message[:100]
                row.updated_at = datetime.utcnow()
                db.commit()
            self.pool.submit(self.execute, owner, sid, rid)
            return self.run(owner, rid)
        except BaseException:
            self.slots.release()
            raise

    def run(self, owner, rid):
        with self.session_factory() as db:
            row = db.scalar(select(ScholarV31Run).where(ScholarV31Run.id == rid, ScholarV31Run.owner_id == owner))
            if row is None:
                raise SessionNotFound("Recherche introuvable.")
            sid = row.session_id
        self.recover_expired(owner, sid)
        with self.session_factory() as db:
            row = db.get(ScholarV31Run, rid)
            return {"id": row.id, "session_id": row.session_id, "status": row.status, "cancel_requested": row.cancel_requested,
                "created_at": iso(row.created_at), "updated_at": iso(row.updated_at),
                **{k: v for k, v in row.data.items() if k != "request"}}

    def cancel(self, owner, rid):
        with self.session_factory() as db:
            row = db.scalar(select(ScholarV31Run).where(ScholarV31Run.id == rid, ScholarV31Run.owner_id == owner))
            if row is None:
                raise SessionNotFound("Recherche introuvable.")
            if row.status not in TERMINAL:
                row.cancel_requested = True
                db.commit()
        return self.run(owner, rid)

    def execute(self, owner, sid, rid):
        from .agent import ScholarV31ChatAgent, StopResearch
        data = None
        try:
            with self.session_factory() as db:
                row = self.owned(db, owner, sid)
                data = copy.deepcopy(row.data)
                run = db.get(ScholarV31Run, rid)
                run.status = "running"
                db.commit()
            def cancelled():
                with self.session_factory() as db:
                    run = db.get(ScholarV31Run, rid)
                    return run is None or run.cancel_requested or run.status in TERMINAL
            def activity(event):
                with self.session_factory() as db:
                    run = db.get(ScholarV31Run, rid)
                    if run.status in TERMINAL:
                        return
                    run.data = {**run.data, "activity": [*run.data.get("activity", []), event][-200:]}
                    run.updated_at = datetime.utcnow()
                    if event.get("status") == "completed" and event.get("tool") in {
                            "responses", "search_scientific", "search_collected_articles", "get_articles", "get_fulltext",
                            "analyze_articles", "record_candidate_decisions", "verify_bibliography", "update_research_state",
                            "find_similar_articles", "compare_articles", "identify_research_gaps", "finalize_response"}:
                        session = db.scalar(select(ScholarV31Session).where(ScholarV31Session.id == sid,
                            ScholarV31Session.owner_id == owner).with_for_update())
                        if session.active_run_id == rid:
                            checkpoint = copy.deepcopy(data)
                            for aid, article in session.data.get("articles", {}).items():
                                if aid in checkpoint.get("articles", {}):
                                    checkpoint["articles"][aid]["selected"] = article.get("selected", False)
                            checkpoint["selection_decisions"] = session.data.get("selection_decisions", [])
                            session.data = checkpoint
                    db.commit()
            agent = self.agent_factory() if self.agent_factory else ScholarV31ChatAgent(self.config)
            result = agent.run(data, emit=activity, cancelled=cancelled)
            self.finish(owner, sid, rid, data, result, "cancelled" if result.get("reason") == "cancelled" else "partial" if result.get("partial") else "completed")
        except Exception as exc:
            # Only class names, never request bodies, upstream URLs or secrets.
            logger.warning("EnnoScholar V3.1 run failed type=%s", type(exc).__name__)
            self.finish(owner, sid, rid, data, {"answer": "La recherche n'a pas pu terminer. Vérifiez les accès OpenAI et réessayez. Les échanges et articles déjà récupérés sont conservés.",
                "reason": "upstream_error", "error_type": type(exc).__name__, "partial": True}, "failed")
        finally:
            self.slots.release()

    def finish(self, owner, sid, rid, data, result, status):
        with self.session_factory() as db:
            row = db.scalar(select(ScholarV31Session).where(ScholarV31Session.id == sid, ScholarV31Session.owner_id == owner).with_for_update())
            run = db.get(ScholarV31Run, rid)
            if row.active_run_id != rid or run.status in TERMINAL:
                return  # A stale worker must never overwrite a newer turn.
            if data is None:
                data = copy.deepcopy(row.data)
            else:
                # Selection changes made during a running turn are authoritative.
                for aid, article in row.data.get("articles", {}).items():
                    if aid in data.get("articles", {}):
                        data["articles"][aid]["selected"] = article.get("selected", False)
                data["selection_decisions"] = row.data.get("selection_decisions", [])
            data.setdefault("messages", []).append({"id": uuid.uuid4().hex, "role": "assistant", "content": result["answer"],
                "citations": result.get("citations", []), "run_id": rid, "partial": status != "completed"})
            row.data, row.active_run_id, row.lease_until, row.updated_at = data, None, None, datetime.utcnow()
            run.data = {**run.data, **result}
            run.status, run.updated_at = status, datetime.utcnow()
            db.commit()


_service = None
_guard = threading.Lock()


def get_service():
    global _service
    with _guard:
        if _service is None:
            from db.database import SessionLocal
            _service = ScholarV31Service(SessionLocal)
        return _service
