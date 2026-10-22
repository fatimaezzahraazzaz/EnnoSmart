from __future__ import annotations

import copy
import logging
import threading
import uuid
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from sqlalchemy import or_, select, update

from .config import ScholarV4Config
from .models import Article
from .job_ledger import JobMeter, close_open_entries
from .session import ScholarV4Session, ScholarV4Run, lock_rows, sync_records

logger = logging.getLogger(__name__)
TERMINAL = {"completed", "partial", "failed", "cancelled"}
# Cancellation reads two run/session columns at most this often (deadlines are checked
# on every event by the agent); ownership and project access are re-verified less often.
CANCEL_CHECK_SECONDS = 1.0
ACCESS_CHECK_SECONDS = 30.0


DRIVE_TASK = "ennosmart.scholar_v4.drive_writing_job"
# A process's right to hand a job to a driver; renewed by that process, expired otherwise.
DISPATCH_CLAIM_SECONDS = 120
WORKER_CHECK = "ennosmart.scholar_v4.worker_check"


class ExecutorUnavailable(RuntimeError):
    pass


def celery_client():
    """A producer for the existing worker (backend_api/worker/celery_app.py), with the same
    broker and result variables. It fails fast instead of retrying. The worker module itself
    is not imported: it rewrites ENNOSCHOLAR_* variables from its .env file."""
    import os
    from celery import Celery
    app = Celery("ennosmart", broker=os.getenv("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0"),
        backend=os.getenv("CELERY_RESULT_BACKEND", "redis://127.0.0.1:6379/0"))
    app.conf.update(broker_connection_retry=False, broker_connection_retry_on_startup=False,
        broker_connection_timeout=3, broker_connection_max_retries=0, task_serializer="json",
        result_serializer="json", accept_content=["json"])
    return app


def database_identity(engine):
    """The database a process works on: its URL, with a SQLite file made absolute (a
    relative path depends on the working directory of each process)."""
    import os
    url = engine.url
    if url.get_backend_name() == "sqlite" and url.database and url.database != ":memory:":
        url = url.set(database=os.path.abspath(url.database))
    return url.render_as_string(hide_password=False)


def worker_fingerprint():
    """Configuration of this process (a Celery worker), compared with the backend's."""
    from db.database import engine
    from .writing_job import fingerprint
    config = ScholarV4Config.from_env()
    return fingerprint(config, database_identity(engine))


class SessionNotFound(LookupError):
    pass


class SessionBusy(RuntimeError):
    pass


def iso(value):
    return value.isoformat() + "Z" if value else None


class ScholarV4Service:
    def __init__(self, session_factory, config=None, agent_factory=None):
        self.session_factory = session_factory
        self.config = config or ScholarV4Config.from_env()
        self.agent_factory = agent_factory
        self.pool = ThreadPoolExecutor(max_workers=self.config.workers, thread_name_prefix="scholar-v4")
        self.slots = threading.BoundedSemaphore(self.config.workers)
        self._dispatched = {}
        self._job_sessions = {}
        self.process_id = uuid.uuid4().hex

    def owned(self, db, owner, sid, *, lock=False):
        if lock:
            # A writer of the session's JSON reads it under the row lock (session.lock_rows).
            lock_rows(db, sid)
        row = db.scalar(select(ScholarV4Session).where(ScholarV4Session.id == sid, ScholarV4Session.owner_id == owner))
        if row is None:
            raise SessionNotFound("Conversation introuvable.")
        if row.data.get("mode") == "diagnostic_linked":
            from .diagnostic_link import permitted_project
            permitted_project(db, owner, row.data["diagnostic_link"]["snapshot"]["project"]["id"])
        return row

    def create(self, owner, title="Nouvelle conversation", *, options=None):
        with self.session_factory() as db:
            data = {"messages": [], "articles": {}, "research_state": {}, "summary": "", "project_id": None, "selected_verrous": [], "focus": {}, "outline_versions": [], "mode": "express"}
            if options and options.mode == "diagnostic_linked":
                from .diagnostic_link import source_snapshot, make_link
                snapshot = source_snapshot(db, owner, options.project_id, options.diagnostic_id)
                if snapshot["source_version"] != options.source_version:
                    raise SessionBusy("Le diagnostic a changé. Rechargez les verrous avant confirmation.")
                data.update(mode="diagnostic_linked", project_id=options.project_id,
                            diagnostic_link=make_link(snapshot, options.selected_lock_ids, options.allow_unkept))
            row = ScholarV4Session(id=uuid.uuid4().hex, owner_id=owner, title=title,
                data=data)
            db.add(row)
            db.commit()
            return self.session_read(row)

    def session_read(self, row, detailed=True):
        result = {"id": row.id, "title": row.title, "created_at": iso(row.created_at), "updated_at": iso(row.updated_at),
            "active_run_id": row.active_run_id, "articles_total": len(row.data.get("articles", {})),
            "selected_count": sum(bool(a.get("selected")) for a in row.data.get("articles", {}).values())}
        result["mode"] = row.data.get("mode", "express")
        from .writing_job import public
        result["writing_job"] = public(row.data.get("writing_job"))
        if result["mode"] == "diagnostic_linked":
            from .diagnostic_link import summary
            result["diagnostic_context"] = summary(row.data["diagnostic_link"])
        if detailed:
            stored = row.data.get("articles", {})
            pinned = list(dict.fromkeys([*row.data.get("evaluation", {}).get("recommended_article_ids", []),
                *[aid for aid, a in stored.items() if a.get("selected")], *row.data.get("held_article_ids", []),
                *list(stored)[-24:]]))
            result.update(messages=row.data.get("messages", []), articles=[Article.model_validate(stored[aid]).compact()
                for aid in pinned if aid in stored], research_state=row.data.get("research_state", {}),
                recommended_article_ids=row.data.get("evaluation", {}).get("recommended_article_ids", []), summary=row.data.get("summary", ""))
            result["presented_total"] = len(row.data.get("exposed_article_ids", []))
            result["examined_total"] = len(row.data.get("examined_article_ids", []))
            result["research_gaps"] = row.data.get("research_gaps", [])
            result["comparisons"] = row.data.get("comparisons", [])
            result["outline"] = row.data.get("outline", {})
            result["scientific_reading"] = row.data.get("scientific_reading", {})
            result["project_facts"] = row.data.get("project_facts", [])
            result["argument_blueprint"] = row.data.get("argument_blueprint", {})
            result["writing_progress"] = row.data.get("document", {}).get("writing_progress", {})
            from .argumentation import blueprint_status
            status_ids = set(row.data.get("evaluation", {}).get("recommended_article_ids", []))
            status_ids.update(aid for aid, article in stored.items() if article.get("selected") or
                article.get("assessment", {}).get("decision") == "retain")
            status_ids.update(fact["source_id"] for fact in row.data.get("project_facts", [])
                if fact.get("source_type") == "uploaded_project_document")
            result["argument_blueprint_status"] = blueprint_status(row.data,
                {aid: Article.model_validate(article) for aid, article in stored.items() if aid in status_ids})
            result["displayed_article_ids"] = row.data.get("displayed_article_ids", [])
            result["held_article_ids"] = row.data.get("held_article_ids", [])
            result["consultant_decisions"] = row.data.get("consultant_decisions", [])[-30:]
        return result

    def sessions(self, owner):
        with self.session_factory() as db:
            rows = db.scalars(select(ScholarV4Session).where(ScholarV4Session.owner_id == owner)
                .order_by(ScholarV4Session.updated_at.desc()).limit(100)).all()
            from fastapi import HTTPException
            result = []
            for row in rows:
                try:
                    self.owned(db, owner, row.id)
                except HTTPException:
                    continue
                result.append(self.session_read(row, False))
            return result

    def diagnostic_projects(self, owner):
        from .diagnostic_link import list_projects
        with self.session_factory() as db:
            return list_projects(db, owner)

    def diagnostic_preview(self, owner, project_id):
        from .diagnostic_link import source_snapshot, make_link, summary
        with self.session_factory() as db:
            source = source_snapshot(db, owner, project_id)
            return summary({"snapshot": source, "revision": 1, "selected_lock_ids": [], "active_lock_ids": []})

    def project_context(self, owner, sid, kind="facts", offset=0, limit=5, scope="active"):
        from .diagnostic_link import retrieve, source_snapshot
        with self.session_factory() as db:
            row = self.owned(db, owner, sid)
            result = retrieve(row.data, kind, offset, limit, scope=scope)
            result["history"] = row.data["diagnostic_link"].get("history", [])
            old = row.data["diagnostic_link"]["snapshot"]
            try:
                latest = source_snapshot(db, owner, old["project"]["id"])
                result["source_changed"] = latest["source_version"] != old["source_version"]
            except ValueError:
                result["source_changed"] = True
            return result

    def update_project_context(self, owner, sid, payload):
        from .diagnostic_link import source_snapshot, set_scope, validate_selection
        with self.session_factory() as db:
            row = self.owned(db, owner, sid, lock=True)
            if row.active_run_id:
                raise SessionBusy("Attendre la fin du tour avant de changer le contexte.")
            data = copy.deepcopy(row.data)
            link = data.get("diagnostic_link")
            if data.get("mode") != "diagnostic_linked" or not link:
                raise ValueError("Conversation Express : créer une conversation liée distincte.")
            if payload.expected_revision != link["revision"]:
                raise SessionBusy("Le périmètre a changé. Rechargez la conversation.")
            if payload.action != "scope":
                if not payload.confirmed:
                    raise ValueError("Confirmer explicitement la nouvelle sélection ou l'actualisation.")
                source = link["snapshot"]
                if payload.action == "refresh":
                    source = source_snapshot(db, owner, source["project"]["id"])
                    if source["source_version"] != payload.source_version:
                        raise SessionBusy("Rechargez le diagnostic avant de confirmer l'actualisation.")
                ids = validate_selection(source, payload.lock_ids, payload.allow_unkept)
                link["history"].append({"action": "selection", "previous_selected_lock_ids": link["selected_lock_ids"][:],
                                        "selected_lock_ids": ids[:], "origin": "authenticated_human", "at": iso(datetime.utcnow())})
                if payload.action == "refresh":
                    link["source_history"].append(copy.deepcopy(link["snapshot"]))
                    link["history"].append({"action": "refresh", "previous_source_version": link["snapshot"]["source_version"],
                                            "source_version": source["source_version"], "at": iso(datetime.utcnow()),
                                            "previous_selected_lock_ids": link["selected_lock_ids"][:]})
                    link["snapshot"] = source
                link["selected_lock_ids"] = ids
            set_scope(data, payload.lock_ids)
            changed = db.execute(update(ScholarV4Session).where(ScholarV4Session.id == sid,
                ScholarV4Session.owner_id == owner, ScholarV4Session.active_run_id.is_(None),
                ScholarV4Session.updated_at == row.updated_at).values(data=data, updated_at=datetime.utcnow()))
            if changed.rowcount != 1:
                raise SessionBusy("Modification concurrente : rechargez la conversation.")
            db.commit()
        return self.read(owner, sid)

    def document_action(self, owner, sid, payload):
        import copy
        from .manuscript import approve_outline, save_document_revision
        from .toolkit import ScholarTools
        with self.session_factory() as db:
            row = self.owned(db, owner, sid, lock=True)
            if row.active_run_id:
                raise SessionBusy("Attendre la fin du tour avant de modifier le document.")
            data = copy.deepcopy(row.data)
            if payload.expected_version != data.get("outline", {}).get("version", 0):
                raise SessionBusy("Le document a changé. Rechargez sa dernière version.")
            from .sources import ScientificSources
            t = ScholarTools(data, ScientificSources(config=self.config), self.config)
            if payload.action == "approve_outline":
                approve_outline(data, owner)
                from .writing_progress import refresh_writing
                refresh_writing(data)
            elif payload.action == "outline_edit":
                if payload.outline is None:
                    raise ValueError("Modification du plan manquante.")
                t.consultant_edit = True   # the authenticated consultant's own edit, not a model proposal
                t.execute("manage_outline", payload.outline.model_dump(), time.monotonic()+30)
            elif payload.action == "set_scope":
                if payload.scope is None:
                    raise ValueError("Cadrage manquant.")
                data["scientific_scope"] = payload.scope.model_dump(mode="json")
                data.pop("literature_coverage_review", None)
                if data.get("outline"):
                    data["outline"].pop("approval", None)
                    data["outline"]["status"] = "modified"
                    data["outline"]["plan_revision"] = data["outline"].get("plan_revision", 0)+1
            elif payload.action == "edit_draft":
                from .tools.outline import resolve_section
                section = resolve_section(data, payload.section_id)
                if not section.get("drafts") or not payload.text:
                    raise ValueError("Brouillon ou texte de modification manquant.")
                draft = section["drafts"][-1]
                t.human_edit = True
                saved = t.execute("write_section", {"section_id": section["id"],"action":"save","instructions":"Révision humaine",
                    "text":payload.text,"paragraph_id":payload.paragraph_id,"article_ids":draft["article_ids"],"evidence_ids":draft["evidence_ids"],
                    "claims":[c for c in draft.get("claims",[]) if c["statement"] in payload.text],
                    "numeric_claims":[{k:f[k] for k in ("evidence_id","column","value","unit","metric")}
                        for f in draft.get("numeric_claims",[]) if f["value"] in payload.text]},time.monotonic()+30)
                if not saved.get("saved"):
                    raise ValueError(saved.get("error", "La révision n'a pas passé les contrôles de traçabilité."))
            elif payload.action == "restore_draft":
                from .tools.outline import resolve_section
                section = resolve_section(data,payload.section_id)
                draft = next((d for d in section.get("drafts",[]) if d["id"]==payload.draft_id),None)
                if not draft:
                    raise ValueError("Version de section introuvable.")
                t.claim_union([{"evidence_ids":draft["evidence_ids"]}],draft["article_ids"])
                section["drafts"].append({**copy.deepcopy(draft),"id":uuid.uuid4().hex,"restored_from":draft["id"]})
            elif payload.action == "approve_document":
                from .scientific_review import deterministic_review
                if data.get("outline",{}).get("status") != "approved" or not all(s.get("drafts") for s in data["outline"]["sections"]):
                    raise ValueError("Valider le plan et rédiger toutes les sections avant l'approbation du document.")
                if not deterministic_review(data,t)["integrity_passed"]:
                    raise ValueError("Corriger les problèmes de traçabilité avant l'approbation humaine du document.")
            elif payload.action == "update_figure":
                if not payload.figure:
                    raise ValueError("Recette scientifique manquante.")
                t.execute("create_scientific_visual",payload.figure.model_dump(),time.monotonic()+30)
            elif payload.action == "remove_figure":
                figures=data.get("scientific_figures",[])
                old=next((f for f in figures if f["id"]==payload.figure_id),None)
                if old is None:
                    raise ValueError("Figure introuvable.")
                data.setdefault("figure_versions",[]).append(copy.deepcopy(old))
                figures.remove(old)
            else:
                raise ValueError("Action documentaire non disponible.")
            if payload.action not in {"outline_edit","edit_draft","update_figure"}:
                data.setdefault("outline", {"title": "État de l'art", "sections": []})["version"] = payload.expected_version+1
            if payload.action not in {"edit_draft","update_figure","outline_edit"}:
                revision = save_document_revision(data)
            if payload.action == "approve_document":
                data["document"]["approval"] = {"version_id":revision["id"],"owner_id":owner,"origin":"authenticated_human","at":iso(datetime.utcnow())}
                revision["approval"] = copy.deepcopy(data["document"]["approval"])
            t.save()
            changed = db.execute(update(ScholarV4Session).where(ScholarV4Session.id == sid,
                ScholarV4Session.owner_id == owner, ScholarV4Session.active_run_id.is_(None),
                ScholarV4Session.updated_at == row.updated_at).values(data=data, updated_at=datetime.utcnow()))
            if changed.rowcount != 1:
                raise SessionBusy("Une modification concurrente exige de recharger le document.")
            row.data = data
            sync_records(db, row)
            db.commit()
            return self.read(owner, sid)

    def document_read(self, owner, sid):
        from .editorial import document_quality, plan_review
        from .scientific_scope import literature_coverage
        from .scientific_review import deterministic_review
        from .toolkit import ScholarTools
        from .citation_render import render_manuscript
        with self.session_factory() as db:
            data = self.owned(db, owner, sid).data
            registry = {aid: Article.model_validate(a) for aid, a in data.get("articles", {}).items()}
            used = set(eid for s in data.get("outline", {}).get("sections", [])
                for eid in [*s.get("evidence_ids", []), *[e for d in s.get("drafts", []) for e in d.get("evidence_ids", [])]])
            used.update(e for r in data.get("scientific_relations", []) for e in r["evidence_ids"])
            used.update(e for f in data.get("scientific_figures", []) for e in f["evidence_ids"])
            return {"outline": data.get("outline", {}), "document": data.get("document", {}),
                "quality": document_quality(data), "plan_review": plan_review(data.get("outline", {})),
                "scientific_scope": data.get("scientific_scope", {}), "coverage": literature_coverage(data, registry),
                "relations": data.get("scientific_relations", []), "figures": data.get("scientific_figures", []),
                "review": deterministic_review(copy.deepcopy(data), ScholarTools(copy.deepcopy(data), None, self.config)),
                "versions": [{k:v for k,v in r.items() if k not in {"outline", "figures", "article_metadata"}} for r in data.get("document_versions", [])],
                "articles": [a.compact() for a in registry.values()],
                "evidence": {eid:data["evidence"][eid] for eid in used if eid in data.get("evidence", {})},
                # Same citation rendering as the exports, from the drafts' raw identifiers.
                "rendering": render_manuscript(data, data.get("outline", {}).get("sections", []))}

    def export_document(self,owner,sid,version_id,fmt):
        from .document_export import export_document
        with self.session_factory() as db:
            state=copy.deepcopy(self.owned(db,owner,sid).data)
        return export_document(state,version_id,fmt)

    def document_figure(self,owner,sid,figure_id):
        from .scientific_visuals import figure_png
        with self.session_factory() as db:
            state=self.owned(db,owner,sid).data
            figure=next((f for f in state.get("scientific_figures",[]) if f["id"]==figure_id),None)
            if figure is None:
                raise SessionNotFound("Figure introuvable.")
            return figure_png(figure)

    def read(self, owner, sid):
        self.recover_expired(owner, sid)
        with self.session_factory() as db:
            return self.session_read(self.owned(db, owner, sid))

    def articles(self, owner, sid, offset=0, limit=50, query="", exclude_ids=None, category="all"):
        with self.session_factory() as db:
            row = self.owned(db, owner, sid)
            articles = list(row.data.get("articles", {}).values())
            excluded = set(exclude_ids or [])
            articles = [a for a in articles if a["id"] not in excluded]
            if category != "all":
                from .catalogue import library_groups
                allowed = set(library_groups(row.data, excluded).get(category, []))
                articles = [a for a in articles if a["id"] in allowed]
            if query:
                from .store import ArticleStore
                store = ArticleStore(copy.deepcopy(row.data), self.config.max_articles)
                # This read-only human search creates no GPT exposure event.
                matches, _ = store.search(query, len(articles), False, ids=[a["id"] for a in articles])
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
                "examined_article_ids", "candidate_decisions", "comparisons", "research_gaps", "memory_history",
                "displayed_article_ids", "web_sources", "finalization_attempts", "scientific_reading",
                "project_facts", "argument_blueprint", "argument_blueprint_versions", "scientific_relations")}

    def import_pdf(self, owner, sid, content, filename, title="", article_id=""):
        import hashlib
        import fitz
        from agents.EnnoScholarV2.tools.article_reader import matches_article
        from .tools.extraction import pdf_chunks
        from agents.EnnoScholarV2.models import Article as LegacyArticle
        from .pdf_store import save_pdf
        from .store import ArticleStore
        # Authenticate and inspect ownership before extracting or writing any bytes.
        with self.session_factory() as db:
            row = self.owned(db, owner, sid)
            if row.active_run_id:
                raise SessionBusy("Attendez la fin de la recherche avant d'importer le PDF.")
            data = copy.deepcopy(row.data)
        if not content.startswith(b"%PDF") or len(content) > self.config.max_pdf_bytes:
            raise ValueError("PDF requis, taille maximale 15 Mo.")
        chunks, truncated = pdf_chunks(content, self.config)
        if not chunks or not any(chunk.text.strip() for chunk in chunks):
            raise ValueError("PDF sans texte exploitable ; OCR non disponible dans V4.")
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
        article.access_status = "fulltext_extracted"
        article.source_version = "pdf_sha256:"+saved.removesuffix(".pdf")
        # Replacing a source invalidates the current analysis; old snapshots retain old evidence.
        article.analysis, article.analysis_basis = {}, None
        store.save()
        with self.session_factory() as db:
            lock_rows(db, sid)
            row = db.scalar(select(ScholarV4Session).where(ScholarV4Session.id == sid, ScholarV4Session.owner_id == owner))
            if row.active_run_id:
                raise SessionBusy("Une recherche a démarré ; réessayez l'import après sa fin.")
            current = copy.deepcopy(row.data)
            current.setdefault("articles", {})[article.id] = article.durable()
            current.setdefault("focus", {})["article_id"] = article.id
            row.data, row.updated_at = current, datetime.utcnow()
            sync_records(db, row)
            db.commit()
        return article.compact()

    def order_articles(self, owner, sid, article_ids):
        from .decisions import apply_order
        with self.session_factory() as db:
            lock_rows(db, sid)
            row = db.scalar(select(ScholarV4Session).where(ScholarV4Session.id == sid, ScholarV4Session.owner_id == owner))
            if row is None:
                raise SessionNotFound("Conversation introuvable.")
            if row.active_run_id:
                raise SessionBusy("Attendre la fin du tour avant de modifier la sélection.")
            data = copy.deepcopy(row.data)
            articles = data.get("articles", {})
            if any(aid not in articles for aid in article_ids):
                raise SessionNotFound("Article introuvable.")
            if any(articles[aid].get("selection_override") is False for aid in article_ids):
                raise ValueError("Un article écarté ne peut pas être ordonné ; le rétablir d'abord.")
            order = apply_order(data, article_ids, origin="consultant_interface")
            row.data, row.updated_at = data, datetime.utcnow()
            sync_records(db, row)
            db.commit()
            return {"recommended_article_ids": order}

    def select_article(self, owner, sid, aid, selected, decision=None):
        """selected=True/False keeps the original API; decision adds hold/restore."""
        from .decisions import apply_decision
        if decision is not None:
            selected = decision == "accept"
        with self.session_factory() as db:
            lock_rows(db, sid)
            row = db.scalar(select(ScholarV4Session).where(ScholarV4Session.id == sid, ScholarV4Session.owner_id == owner))
            if row is None:
                raise SessionNotFound("Conversation introuvable.")
            if row.active_run_id:
                raise SessionBusy("Attendre la fin du tour avant de modifier la sélection.")
            data = copy.deepcopy(row.data)
            if aid not in data.get("articles", {}):
                raise SessionNotFound("Article introuvable.")
            if selected and not data["articles"][aid].get("verified"):
                raise ValueError("Référence non vérifiée ; conservation impossible.")
            data["articles"][aid]["selected"] = selected
            data["articles"][aid]["selection_override"] = selected
            data.setdefault("selection_decisions", []).append({"article_id": aid, "selected": selected, "at": iso(datetime.utcnow())})
            evaluation = data.get("evaluation", {})
            if not selected and aid in evaluation.get("recommended_article_ids", []):
                data.setdefault("evaluation_history", []).append(copy.deepcopy(evaluation))
                evaluation["recommended_article_ids"] = [item for item in evaluation["recommended_article_ids"] if item != aid]
            if decision is not None:
                apply_decision(data, aid, decision,
                    lambda a, sel, override: data["articles"][a].update(selected=sel, selection_override=override),
                    verified=bool(data["articles"][aid].get("verified")), origin="consultant_interface")
            outline = data.get("outline", {})
            # A pause (hold) or a return to the model's judgment keeps plan memberships.
            if outline.get("sections") and decision not in {"hold", "restore"}:
                from .manuscript import save_document_revision
                changed = False
                if not selected:
                    memberships = []
                    for section in outline["sections"]:
                        if aid not in section.get("article_ids", []):
                            continue
                        memberships.append({"section_id": section["id"], "roles": [copy.deepcopy(role)
                            for role in section.get("article_roles", []) if role["article_id"] == aid],
                            "evidence_ids": [eid for eid in section.get("evidence_ids", [])
                                if data.get("evidence", {}).get(eid, {}).get("article_id") == aid],
                            "relation_ids": [rid for rid in section.get("relation_ids", []) if
                                any(rel.get("id") == rid and aid in rel.get("article_ids", [])
                                    for rel in data.get("scientific_relations", []))]})
                        section["article_ids"] = [item for item in section["article_ids"] if item != aid]
                        section["article_roles"] = [role for role in section.get("article_roles", []) if role["article_id"] != aid]
                        section["evidence_ids"] = [eid for eid in section.get("evidence_ids", [])
                            if data.get("evidence", {}).get(eid, {}).get("article_id") != aid]
                        section["relation_ids"] = [rid for rid in section.get("relation_ids", []) if
                            not any(rel.get("id") == rid and aid in rel.get("article_ids", [])
                                for rel in data.get("scientific_relations", []))]
                        changed = True
                    if memberships:
                        data.setdefault("selection_memberships", {})[aid] = {
                            "sections": memberships, "removed_at_plan_revision": outline.get("plan_revision", outline["version"])}
                else:
                    membership = data.get("selection_memberships", {}).get(aid, {})
                    if membership.get("removed_at_plan_revision") == outline.get("plan_revision", outline["version"]):
                        for saved in membership.get("sections", []):
                            section = next((s for s in outline["sections"] if s["id"] == saved["section_id"]), None)
                            if section is not None and aid not in section["article_ids"]:
                                section["article_ids"].append(aid)
                                section["article_roles"] = [*section.get("article_roles", []), *saved["roles"]]
                                section["evidence_ids"] = list(dict.fromkeys([
                                    *section.get("evidence_ids", []), *saved.get("evidence_ids", [])]))
                                section["relation_ids"] = list(dict.fromkeys([
                                    *section.get("relation_ids", []), *saved.get("relation_ids", [])]))
                                changed = True
                if changed:
                    data.setdefault("outline_versions", []).append(copy.deepcopy(row.data["outline"]))
                    next_revision = outline.get("plan_revision", outline["version"]) + 1
                    outline["version"] += 1
                    if not selected:
                        data["selection_memberships"][aid]["removed_at_plan_revision"] = next_revision
                    outline["plan_revision"] = next_revision
                    outline["status"] = "modified"
                    outline.pop("approval", None)
                    save_document_revision(data)
            row.data, row.updated_at = data, datetime.utcnow()
            sync_records(db, row)
            db.commit()
            return Article.model_validate(data["articles"][aid]).compact()

    def import_document(self, owner, sid, content, filename):
        from pathlib import Path
        import hashlib
        import io
        import zipfile
        from xml.etree.ElementTree import fromstring
        from .store import ArticleStore
        extension = Path(filename).suffix.lower()
        with self.session_factory() as db:
            row = self.owned(db, owner, sid, lock=True)
            if row.active_run_id:
                raise SessionBusy("Attendez la fin de la recherche avant l'import.")
        if not content or len(content) > self.config.max_pdf_bytes:
            raise ValueError("Document vide ou trop volumineux (15 Mo maximum).")
        if extension == ".pdf":
            return self.import_pdf(owner, sid, content, filename)
        if extension in {".txt", ".md"}:
            try:
                text = content.decode("utf-8-sig")
            except UnicodeDecodeError:
                raise ValueError("Le texte doit être encodé en UTF-8.") from None
            if "\x00" in text:
                raise ValueError("Fichier binaire invalide.")
        elif extension == ".docx":
            try:
                with zipfile.ZipFile(io.BytesIO(content)) as archive:
                    if sum(info.file_size for info in archive.infolist()) > self.config.max_pdf_bytes * 2:
                        raise ValueError("Document Word décompressé trop volumineux.")
                    xml = archive.read("word/document.xml")
                    if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
                        raise ValueError("Déclarations XML non autorisées dans ce document Word.")
                    document = fromstring(xml)
                    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
                    text = "\n".join("".join(node.text or "" for node in p.iter(ns+"t")) for p in document.iter(ns+"p"))
            except (zipfile.BadZipFile, KeyError):
                raise ValueError("Document Word invalide.") from None
        else:
            raise ValueError("Formats acceptés : PDF, DOCX, TXT et Markdown.")
        if not text.strip() or len(text) > 1000000:
            raise ValueError("Texte vide ou dépassant la limite d'un million de caractères.")
        digest = hashlib.sha256(content).hexdigest()
        with self.session_factory() as db:
            lock_rows(db, sid)
            row = db.scalar(select(ScholarV4Session).where(ScholarV4Session.id == sid, ScholarV4Session.owner_id == owner))
            if row.active_run_id:
                raise SessionBusy("Une recherche a démarré ; réessayez après sa fin.")
            data = copy.deepcopy(row.data)
            store = ArticleStore(data, self.config.max_articles)
            ids, _ = store.insert([Article(title=Path(filename).stem[:1000], external_ids={"uploaded_document": digest}, sources=["uploaded_document"])])
            if not ids:
                raise ValueError("Capacité du corpus atteinte.")
            article = store.article(ids[0])
            article.fulltext = {"text": text, "chunks": [{"text": text[i:i+6000], "section": "", "page": None} for i in range(0, len(text), 6000)],
                "url": "", "source_kind": "user_uploaded_document", "original_filename": Path(filename).name[:255], "truncated": False}
            article.access_level, article.access_status, article.fulltext_available = "fulltext", "fulltext_extracted", True
            article.source_version = "sha256:" + digest
            article.analysis, article.analysis_basis = {}, None
            store.save()
            data.setdefault("focus", {})["article_id"] = article.id
            row.data, row.updated_at = data, datetime.utcnow()
            sync_records(db, row)
            db.commit()
            return article.compact()

    def recover_expired(self, owner, sid):
        with self.session_factory() as db:
            lease = db.execute(select(ScholarV4Session.active_run_id, ScholarV4Session.lease_until)
                .where(ScholarV4Session.id == sid, ScholarV4Session.owner_id == owner)).first()
            if lease is None:
                raise SessionNotFound("Conversation introuvable.")
            if not (lease.active_run_id and lease.lease_until and lease.lease_until < datetime.utcnow()):
                return
            rid = lease.active_run_id
            lock_rows(db, sid, rid)
            changed = db.execute(update(ScholarV4Session).where(ScholarV4Session.id == sid,
                ScholarV4Session.active_run_id == rid, ScholarV4Session.lease_until < datetime.utcnow())
                .values(active_run_id=None, lease_until=None))
            if changed.rowcount:
                run = db.get(ScholarV4Run, rid)
                if run and run.status not in TERMINAL:
                    run.status = "partial"
                    run.data = {**run.data, "reason": "worker_interrupted", "answer": "Recherche interrompue ou délai dépassé. Les échanges précédents sont conservés ; relancez votre demande."}
                if run is not None:
                    # Calls still reserved may have been processed: they stay counted, as uncertain.
                    close_open_entries(run, "worker_interrupted")
            db.commit()

    def finance_engine(self):
        bind = getattr(self.session_factory, "kw", {}).get("bind")
        if bind is None:
            with self.session_factory() as db:
                bind = db.get_bind()
        return bind

    def meter(self, owner, sid, rid, data):
        """Spending controller of one run (None when disabled). Identities come from the
        authenticated owner, the permitted project of the conversation and the server's
        organisation setting, never from the request body."""
        if not self.config.finance_enabled:
            return None
        from .config import ConfigurationError
        from .finance import BudgetPolicy, Ledger, Meter, SpendScope, installed
        engine = self.finance_engine()
        if not installed(engine):
            raise ConfigurationError("Contrôleur financier activé sans ses tables : appliquer la migration "
                "20261019_ennoscholar_v4_finance.sql (ou finance.migrate) avant d'activer ENNOSCHOLAR_V4_FINANCE_ENABLED.")
        dossier = (f"project:{data['project_id']}" if data.get("mode") == "diagnostic_linked" and data.get("project_id")
            else f"conversation:{sid}")
        return Meter(Ledger(engine, BudgetPolicy.from_file(self.config.budget_policy_file)),
            SpendScope(self.config.organization_id, int(owner), dossier, rid, sid), self.config)

    def spending(self, owner, limit=100):
        """The consultant's own ledger rows and balances (no other consultant's data)."""
        if not self.config.finance_enabled:
            return {"enabled": False, "calls": [], "balances": []}
        from .finance import BudgetPolicy, Ledger, SpendScope
        ledger = Ledger(self.finance_engine(), BudgetPolicy.from_file(self.config.budget_policy_file))
        rows = ledger.history(consultant_id=int(owner), limit=min(max(1, limit), 500))
        scope = SpendScope(self.config.organization_id, int(owner), "-")
        return {"enabled": True, "policy": ledger.policy.describe(), "calls": [{k: (v.isoformat()+"Z" if hasattr(v, "isoformat") else v)
            for k, v in row.items() if k not in {"usage", "counter_keys", "reserved_nano", "settled_nano"}} for row in rows],
            "balances": [b for b in ledger.balances(scope) if b["budget"].startswith(("consultant:", "organization:"))]}

    def start(self, owner, request):
        self.config.validate()
        if self.config.finance_enabled:
            from .finance import installed
            from .config import ConfigurationError
            if not installed(self.finance_engine()):
                raise ConfigurationError("Contrôleur financier activé sans ses tables : appliquer la migration avant usage.")
        if not self.slots.acquire(blocking=False):
            raise SessionBusy("Les recherches V4 sont occupées. Réessayez dans un instant.")
        try:
            sid = request.session_id or self.create(owner)["id"]
            self.recover_expired(owner, sid)
            # A request that would open an automatic drafting job checks its executor first:
            # in explicit Celery mode nothing starts without a worker able to run V4 tasks.
            executor = self.choose_executor() if self.opens_job(owner, sid, request.message) else None
            rid = self.open_run(owner, sid, request.message, executor=executor)
            self.pool.submit(self.execute, owner, sid, rid)
            return self.run(owner, rid)
        except BaseException:
            self.slots.release()
            raise

    def opens_job(self, owner, sid, message):
        from .writing_job import wants_full_draft
        from .research import wants_research
        if not (self.config.writing_job_enabled or self.config.research_job_enabled):
            return False
        with self.session_factory() as db:
            data = self.owned(db, owner, sid).data
            job = data.get("writing_job")
            # Same rule as open_run: a running segment keeps the conversation; a waiting or
            # paused job is replaced by an explicit new drafting or research request.
            return (not job or job.get("status") != "running") and bool(
                self.config.writing_job_enabled and wants_full_draft(data, message) or
                self.config.research_job_enabled and wants_research(message))

    def open_run(self, owner, sid, message, *, job_id=None, executor=None):
        """Claim the session's run lease (one active run per conversation) and record the
        request, under the session's lock. A consultant request for the whole manuscript
        opens its drafting job in the same transaction. A job segment (job_id) adds no
        message: it resumes the consultant's request, goal and progress (segment_directive).
        A consultant message while the job waits pauses it (resumed only on request)."""
        from .writing_job import (EMPTY, FINAL, is_active, new_job, now, public, saved_ids, segment_directive,
            wants_full_draft)
        from .research import wants_research
        rid = uuid.uuid4().hex
        with self.session_factory() as db:
            self.owned(db, owner, sid)
            changed = db.execute(update(ScholarV4Session).where(ScholarV4Session.id == sid,
                ScholarV4Session.owner_id == owner, ScholarV4Session.active_run_id.is_(None)).values(
                active_run_id=rid, lease_until=datetime.utcnow() + timedelta(seconds=self.config.timeout_seconds + 30)))
            if not changed.rowcount:
                raise SessionBusy("Une recherche est déjà en cours dans cette conversation.")
            db.expire_all()  # the update above holds the lock: read the current row
            row = db.get(ScholarV4Session, sid)
            data = dict(row.data)
            job = copy.deepcopy(data.get("writing_job"))
            run_data = {"activity": [], "usage": {}, "answer": "", "citations": [], "request": message}
            if job_id is not None:
                if not job or job["id"] != job_id or job["status"] != "waiting" or job.get("current_run_id"):
                    db.rollback()
                    raise SessionBusy("La rédaction automatique n'attend pas de nouveau segment.")
                request = next((m for m in data.get("messages", []) if m.get("id") == job["request_message_id"]), {})
                job.update(status="running", current_run_id=rid, saved_before=saved_ids(data), updated_at=now(), executor=executor)
                run_data.update(request=request.get("content", ""), automatic=True, writing_job_id=job["id"],
                    segment=segment_directive(job), job_budget_usd=job["budget_usd"], job_prior=dict(job["spent"]))
                data["writing_job"] = job
            else:
                message_row = {"id": uuid.uuid4().hex, "role": "user", "content": message}
                data["messages"] = [*data.get("messages", []), message_row]
                kind = ("writing" if self.config.writing_job_enabled and wants_full_draft(data, message) else
                    "research" if self.config.research_job_enabled and wants_research(message) else None)
                if job and job["status"] == "waiting" and kind is None:
                    # The consultant writes during the job: it pauses and resumes only on request.
                    job.update(status="paused", paused_by_message_id=message_row["id"], updated_at=now())
                    data["writing_job"] = job
                elif kind is not None and (not job or job["status"] in FINAL | {"waiting", "paused"}):
                    # An explicit new drafting or research request replaces a waiting or paused job
                    # (one durable job per conversation), recorded in the history, never silently.
                    if job:
                        closed = job if job["status"] in FINAL else {**job, "status": "superseded",
                            "superseded_by_message_id": message_row["id"]}
                        data["writing_job_history"] = [*data.get("writing_job_history", []), public(closed)][-10:]
                    job = {**new_job(self.config, message_row["id"], rid, self.fingerprint(), kind=kind),
                        "saved_before": saved_ids(data), "executor": executor}
                    data["writing_job"] = job
                    run_data.update(writing_job_id=job["id"], job_budget_usd=job["budget_usd"], job_prior=dict(EMPTY))
                if row.title == "Nouvelle conversation":
                    row.title = message[:100]
            db.add(ScholarV4Run(id=rid, session_id=sid, owner_id=owner, status="queued", data=run_data))
            row.data = data
            row.updated_at = datetime.utcnow()
            sync_records(db, row)
            db.commit()
        if job is not None and is_active(job):
            self._job_sessions[sid] = owner
        return rid

    def run(self, owner, rid):
        with self.session_factory() as db:
            row = db.scalar(select(ScholarV4Run).where(ScholarV4Run.id == rid, ScholarV4Run.owner_id == owner))
            if row is None:
                raise SessionNotFound("Recherche introuvable.")
            sid = row.session_id
            self.owned(db, owner, sid)
        self.recover_expired(owner, sid)
        self.recover_job(owner, sid)
        with self.session_factory() as db:
            row = db.get(ScholarV4Run, rid)
            return {"id": row.id, "session_id": row.session_id, "status": row.status, "cancel_requested": row.cancel_requested,
                "created_at": iso(row.created_at), "updated_at": iso(row.updated_at),
                **{k: v for k, v in row.data.items() if k not in {"request", "segment"}}}

    def cancel(self, owner, rid):
        with self.session_factory() as db:
            row = db.scalar(select(ScholarV4Run).where(ScholarV4Run.id == rid, ScholarV4Run.owner_id == owner))
            if row is None:
                raise SessionNotFound("Recherche introuvable.")
            if row.status not in TERMINAL:
                self.owned(db, owner, row.session_id)
                row.cancel_requested = True
                db.commit()
        return self.run(owner, rid)

    def execute(self, owner, sid, rid, *, driven=False):
        """One run. A run of a drafting job (its first segment included) is metered call by
        call against the manuscript budget (job_ledger.JobMeter) and its cost cap is what
        remains of it; a continuation resumes the consultant's request, goal and progress.
        A segment run by the job driver (``driven``) is evaluated without another dispatch."""
        from .agent import ScholarChatAgent, StopResearch
        from .writing_job import segment_config
        data = None
        try:
            with self.session_factory() as db:
                row = self.owned(db, owner, sid)
                data = copy.deepcopy(row.data)
                run = db.get(ScholarV4Run, rid)
                run.status = "running"
                run_data = dict(run.data)
                db.commit()
            data.pop("writing_segment", None)
            config, meter = self.config, self.meter(owner, sid, rid, data)
            if run_data.get("writing_job_id"):
                config = segment_config(self.config, {"budget_usd": run_data["job_budget_usd"], "spent": run_data["job_prior"]})
                meter = JobMeter(self.session_factory, sid, rid, run_data["job_budget_usd"], run_data["job_prior"], inner=meter)
                if run_data.get("segment"):
                    data["writing_segment"] = copy.deepcopy(run_data["segment"])
            checked = {"at": None, "access_at": time.monotonic(), "stop": False}
            def cancelled():
                # Called on every streamed event. Loading the session row parses its
                # whole JSON (about 8 MB in run b52f582a): a frequent check reads two
                # small columns at most once per CANCEL_CHECK_SECONDS; ownership and
                # project access (which need the row) are re-verified less often.
                now = time.monotonic()
                if checked["stop"]:
                    return True
                if checked["at"] is not None and now-checked["at"] < CANCEL_CHECK_SECONDS:
                    return False
                checked["at"] = now
                from fastapi import HTTPException
                with self.session_factory() as db:
                    run = db.execute(select(ScholarV4Run.cancel_requested, ScholarV4Run.status)
                        .where(ScholarV4Run.id == rid, ScholarV4Run.owner_id == owner)).first()
                    session = db.execute(select(ScholarV4Session.owner_id, ScholarV4Session.active_run_id)
                        .where(ScholarV4Session.id == sid)).first()
                    # A stale worker (lease taken over by a newer run) stops as well.
                    stop = (run is None or run.cancel_requested or run.status in TERMINAL or session is None
                        or session.owner_id != owner or session.active_run_id != rid)
                    if not stop and now-checked["access_at"] >= ACCESS_CHECK_SECONDS:
                        checked["access_at"] = now
                        try:
                            self.owned(db, owner, sid)
                        except (HTTPException, SessionNotFound):
                            stop = True
                checked["stop"] = stop
                return stop
            def activity(event):
                checkpoint_event = event.get("status") in {"completed", "rejected"} and event.get("tool") in {
                    "responses", "search_scientific", "search_collected_articles", "get_article", "get_fulltext",
                    "analyze_article", "record_candidate_decisions", "verify_bibliography", "update_research_state",
                    "find_similar_articles", "compare_articles", "identify_research_gaps", "finalize_response",
                    "write_section", "manage_outline", "create_scientific_visual", "review_document",
                    "review_literature_coverage", "review_research_coverage", "scientific_reading", "record_project_facts",
                    "build_argument_blueprint", "argumentation", "writing_progress", "task_result"}
                with self.session_factory() as db:
                    # Under the rows' lock: a checkpoint never overwrites a job transition, a
                    # cancellation or a ledger entry written meanwhile by another thread or process.
                    lock_rows(db, sid if checkpoint_event else None, rid)
                    run = db.get(ScholarV4Run, rid)
                    if run.status in TERMINAL:
                        return
                    run.data = {**run.data, "activity": [*run.data.get("activity", []), event][-200:]}
                    run.updated_at = datetime.utcnow()
                    if checkpoint_event:
                        session = db.scalar(select(ScholarV4Session).where(ScholarV4Session.id == sid,
                            ScholarV4Session.owner_id == owner))
                        if session.active_run_id == rid:
                            checkpoint = copy.deepcopy(data)
                            checkpoint.pop("writing_segment", None)
                            for aid, article in session.data.get("articles", {}).items():
                                if aid in checkpoint.get("articles", {}):
                                    checkpoint["articles"][aid]["selected"] = article.get("selected", False)
                            checkpoint["selection_decisions"] = session.data.get("selection_decisions", [])
                            # The drafting job is the service's state, never the run's copy.
                            for key in ("writing_job", "writing_job_history"):
                                if key in session.data:
                                    checkpoint[key] = session.data[key]
                            session.data = checkpoint
                            # Model proposals/receipts need a durable JSON checkpoint,
                            # not a reindex of every article and draft. Validated saves
                            # still update dedicated tables in this same transaction.
                            if event.get("tool") != "responses" and not (
                                    event.get("tool") == "task_result" and event.get("task_status") != "saved"):
                                sync_records(db, session)
                    db.commit()
            from .writing_job import scope_problem
            problem = scope_problem(data["writing_segment"], data) if data.get("writing_segment") else None
            if problem:
                # The pinned scope changed: nothing is drafted and no call is made.
                result = {"answer": problem, "partial": True, "reason": "writing_scope_changed"}
            else:
                agent = self.agent_factory() if self.agent_factory else ScholarChatAgent(config)
                agent.config = config
                agent.meter = meter
                result = agent.run(data, emit=activity, cancelled=cancelled)
            self.finish(owner, sid, rid, data, result, "cancelled" if result.get("reason") == "cancelled" else "partial" if result.get("partial") else "completed")
        except Exception as exc:
            # Only class names, never request bodies, upstream URLs or secrets.
            logger.warning("EnnoScholar V4 run failed type=%s", type(exc).__name__)
            self.finish(owner, sid, rid, data, {"answer": "La recherche n'a pas pu terminer. Vérifiez les accès OpenAI et réessayez. Les échanges et articles déjà récupérés sont conservés.",
                "reason": "upstream_error", "error_type": type(exc).__name__, "partial": True}, "failed")
        finally:
            self.slots.release()
        try:
            self.after_run(owner, sid, rid, dispatch=not driven)
        except Exception as exc:
            logger.warning("EnnoScholar V4 writing job evaluation failed type=%s", type(exc).__name__)

    def finish(self, owner, sid, rid, data, result, status):
        with self.session_factory() as db:
            lock_rows(db, sid, rid)
            row = db.scalar(select(ScholarV4Session).where(ScholarV4Session.id == sid, ScholarV4Session.owner_id == owner))
            run = db.get(ScholarV4Run, rid)
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
                for key in ("writing_job", "writing_job_history"):
                    if key in row.data:
                        data[key] = row.data[key]
            data.pop("writing_segment", None)
            messages = data.setdefault("messages", [])
            previous = messages[-1] if messages else {}
            if (run.data.get("segment") and previous.get("role") == "assistant" and previous.get("automatic")
                    and previous.get("writing_job_id") == run.data["writing_job_id"]
                    and previous.get("content") == result["answer"]):
                # The same answer again (a segment without new knowledge, run 111cf83c saved three
                # identical fallbacks): the existing message records this run instead of a copy.
                previous.setdefault("repeated_run_ids", []).append(rid)
            else:
                messages.append({"id": uuid.uuid4().hex, "role": "assistant", "content": result["answer"],
                    **({"automatic": True, "writing_job_id": run.data["writing_job_id"]} if run.data.get("segment") else {}),
                    "citations": result.get("citations", []), "run_id": rid, "partial": status != "completed",
                    **{key: result.get(key) for key in ("primary_article_ids", "discussed_article_ids", "references", "displayed_article_ids", "complementary_count", "discovered_sources_count", "fallback", "library_counts", "coverage_sufficient", "publication_accepted", "research_status")}})
            row.data, row.active_run_id, row.lease_until, row.updated_at = data, None, None, datetime.utcnow()
            run.data = {**run.data, **{k: v for k, v in result.items() if k != "ledger"}}
            close_open_entries(run, "run_finished_without_settlement")
            run.status, run.updated_at = status, datetime.utcnow()
            sync_records(db, row)
            db.commit()

    # --- Automatic full-manuscript drafting (writing_job.py, job_ledger.py) ---------------

    def fingerprint(self):
        from .writing_job import fingerprint
        return fingerprint(self.config, database_identity(self.finance_engine()))

    def _write_job(self, db, row, job, *, announce=False):
        """Store the job (the caller holds the session's lock) and, for an explicit stop,
        tell the consultant why."""
        data = {**row.data, "writing_job": job}
        from .writing_job import FINAL
        if job.get("status") in FINAL:
            # A segment's answer announced a continuation before the job was evaluated; the job
            # stopped, so the announcement is withdrawn from its messages (state after evaluation).
            from .agent import CONTINUATION_NOTE
            data["messages"] = [{**m, "content": m["content"].replace(CONTINUATION_NOTE, ""), "continuation_withdrawn": True}
                if m.get("writing_job_id") == job.get("id") and CONTINUATION_NOTE in (m.get("content") or "") else m
                for m in data.get("messages", [])]
        if announce and job.get("error") and job["status"] not in {"completed", "not_applicable"}:
            data["messages"] = [*data.get("messages", []), {"id": uuid.uuid4().hex, "role": "assistant",
                "content": job["error"], "automatic": True, "writing_job_id": job["id"], "partial": True,
                "citations": [], "writing_job_status": job["status"]}]
        row.data, row.updated_at = data, datetime.utcnow()
        sync_records(db, row)
        db.commit()

    def _stop_job(self, owner, sid, job_id, status, **fields):
        from .writing_job import FINAL, stop
        with self.session_factory() as db:
            lock_rows(db, sid)
            row = db.scalar(select(ScholarV4Session).where(ScholarV4Session.id == sid, ScholarV4Session.owner_id == owner))
            job = (row.data.get("writing_job") if row else None) or {}
            if job.get("id") != job_id or job.get("status") in FINAL or job.get("current_run_id"):
                db.rollback()
                return job
            job = stop(copy.deepcopy(job), status, **fields)
            self._write_job(db, row, job, announce=True)
            return job

    def after_run(self, owner, sid, rid, *, dispatch=True):
        """Evaluate a finished job segment once, under the session's lock (spending from its
        ledger, saved sections, stop or continue), then let the job continue if it waits."""
        from .writing_job import FINAL, evaluate
        with self.session_factory() as db:
            lock_rows(db, sid)
            row = db.scalar(select(ScholarV4Session).where(ScholarV4Session.id == sid, ScholarV4Session.owner_id == owner))
            job = (row.data.get("writing_job") if row else None)
            if not job:
                db.rollback()
                return None
            run = db.get(ScholarV4Run, rid)
            if job.get("current_run_id") == rid and run is not None and run.status in TERMINAL:
                job = evaluate(copy.deepcopy(job), row.data, rid, run.status, run.data, job.get("saved_before") or {})
                self._write_job(db, row, job, announce=job["status"] in FINAL)
            else:
                db.rollback()
        if dispatch and job.get("status") == "waiting" and not job.get("current_run_id"):
            self.dispatch_job(owner, sid, job["id"])
        return job

    def verify_worker(self):
        """The Celery worker answers through the configured broker, runs the V4 task and has
        this backend's configuration (model, prices, cache mode, API key and database, by
        one-way digest). Raises ExecutorUnavailable with the cause otherwise."""
        from .writing_job import mismatch
        seen = getattr(self, "_worker_verified", None)
        if seen and time.monotonic()-seen < 60:
            return
        try:
            result = celery_client().send_task(WORKER_CHECK, kwargs={}, retry=False)
            remote = result.get(timeout=10)
        except Exception as exc:
            raise ExecutorUnavailable(f"worker Celery injoignable ou tâche V4 non enregistrée ({type(exc).__name__}).") from None
        diff = mismatch(self.fingerprint(), remote)
        if diff:
            raise ExecutorUnavailable("le worker Celery n'a pas la configuration du backend : " + ", ".join(diff) + ".")
        self._worker_verified = time.monotonic()

    def choose_executor(self):
        """thread, or celery after verify_worker. Explicit celery never falls back: the
        request is refused (ConfigurationError) when the worker cannot run V4 tasks."""
        from .config import ConfigurationError
        mode = self.config.writing_job_executor
        if mode == "thread":
            return "thread"
        try:
            self.verify_worker()
            return "celery"
        except ExecutorUnavailable as exc:
            if mode == "celery":
                raise ConfigurationError("Rédaction automatique impossible : " + str(exc)) from None
            logger.info("EnnoScholar V4 writing job executor: thread (auto mode, %s)", exc)
            return "thread"

    def dispatch_job(self, owner, sid, job_id, *, force=False):
        """Hand the job to its driver. Duplicates are harmless: a segment needs the
        session's run lease, which only one driver can hold."""
        recent = self._dispatched.get(job_id)
        if recent and time.monotonic()-recent < 30 and not force:
            return
        self._job_sessions[sid] = owner
        if not self.claim_dispatch(owner, sid, job_id):
            return  # Another process is handing this job to its driver.
        self._dispatched[job_id] = time.monotonic()
        mode = self.config.writing_job_executor
        if mode != "thread":
            try:
                self.verify_worker()
                celery_client().send_task(DRIVE_TASK, kwargs={"owner": int(owner), "session_id": sid, "job_id": job_id},
                    retry=False)
                return
            except ExecutorUnavailable as exc:
                if mode == "celery":
                    self._stop_job(owner, sid, job_id, "executor_unavailable", last_cause=str(exc))
                    return
            except Exception as exc:
                if mode == "celery":
                    self._stop_job(owner, sid, job_id, "executor_unavailable", last_cause=type(exc).__name__)
                    return
        self.pool.submit(self._drive_in_thread, owner, sid, job_id)

    def _drive_in_thread(self, owner, sid, job_id):
        outcome = self.drive_job(owner, sid, job_id, "thread") or {}
        if outcome.get("retry_after"):
            timer = threading.Timer(outcome["retry_after"], self.dispatch_job, args=(owner, sid, job_id), kwargs={"force": True})
            timer.daemon = True
            timer.start()

    def drive_job(self, owner, sid, job_id, executor="thread"):
        """Run the job's segments until it stops or must wait. Returns {"status", and
        "retry_after" seconds when a segment's lease is still live (a lost worker's lease
        expires first: its calls are then counted as uncertain, never replayed blindly)}."""
        from .writing_job import FINAL, can_start_segment, mismatch
        for _ in range(max(int(self.config.writing_job_max_segments), int(self.config.research_job_max_segments)) + 3):
            self.recover_expired(owner, sid)
            with self.session_factory() as db:
                row = db.scalar(select(ScholarV4Session).where(ScholarV4Session.id == sid, ScholarV4Session.owner_id == owner))
                job = row.data.get("writing_job") if row else None
                lease_until = row.lease_until if row else None
                current = (job or {}).get("current_run_id")
                run_status = db.execute(select(ScholarV4Run.status).where(ScholarV4Run.id == current)).scalar() if current else None
            if not job or job["id"] != job_id or job["status"] in FINAL or job["status"] == "paused":
                return {"status": (job or {}).get("status")}
            if current:
                if run_status in TERMINAL:
                    # Finished but not evaluated (process stopped between the two): evaluate now.
                    self.after_run(owner, sid, current, dispatch=False)
                    continue
                wait = (lease_until - datetime.utcnow()).total_seconds() if lease_until else 0
                return {"status": "running", "retry_after": max(5.0, wait + 5.0)}
            diff = mismatch(job.get("config_fingerprint"), self.fingerprint())
            if diff:
                self._stop_job(owner, sid, job_id, "configuration_mismatch", mismatch=diff)
                return {"status": "configuration_mismatch"}
            allowed, status = can_start_segment(job)
            if not allowed:
                self._stop_job(owner, sid, job_id, status)
                return {"status": status}
            if not self.slots.acquire(timeout=600):
                return {"status": "waiting", "retry_after": 60.0}
            try:
                rid = self.open_run(owner, sid, None, job_id=job_id, executor=executor)
            except SessionBusy:
                # The consultant's own run (which pauses the job) or another driver holds it.
                self.slots.release()
                return {"status": "waiting"}
            except BaseException:
                self.slots.release()
                raise
            self.execute(owner, sid, rid, driven=True)
        return {"status": "waiting"}

    def writing_job(self, owner, sid):
        from .writing_job import public
        self.recover_job(owner, sid)
        with self.session_factory() as db:
            row = self.owned(db, owner, sid)
            return {"job": public(row.data.get("writing_job")), "history": row.data.get("writing_job_history", [])}

    def cancel_job(self, owner, sid):
        """Stop the automatic drafting: the running segment is cancelled like any run (its
        validated saves are kept) and no further segment starts."""
        from .writing_job import is_active, stop
        with self.session_factory() as db:
            self.owned(db, owner, sid)
            lock_rows(db, sid)
            row = db.scalar(select(ScholarV4Session).where(ScholarV4Session.id == sid, ScholarV4Session.owner_id == owner))
            job = row.data.get("writing_job")
            if is_active(job) or (job or {}).get("status") == "paused":
                job = copy.deepcopy(job)
                job["cancel_requested"] = True
                current = job.get("current_run_id")
                run = db.get(ScholarV4Run, current) if current else None
                if run is not None and run.status not in TERMINAL:
                    lock_rows(db, run_id=current)
                    run = db.get(ScholarV4Run, current)
                    run.cancel_requested = True
                    self._write_job(db, row, job)
                else:
                    self._write_job(db, row, stop(job, "cancelled"), announce=True)
            else:
                db.rollback()
        return self.writing_job(owner, sid)

    def resume_job(self, owner, sid):
        """A job paused by a consultant message continues only on the consultant's request,
        with the same scope, goal and budget."""
        with self.session_factory() as db:
            self.owned(db, owner, sid)
            lock_rows(db, sid)
            row = db.scalar(select(ScholarV4Session).where(ScholarV4Session.id == sid, ScholarV4Session.owner_id == owner))
            job = copy.deepcopy(row.data.get("writing_job"))
            if not job or job.get("status") != "paused":
                db.rollback()
                raise ValueError("Aucune rédaction automatique en pause dans cette conversation.")
            job.update(status="waiting", paused_by_message_id=None)
            self._write_job(db, row, job)
        self.dispatch_job(owner, sid, job["id"], force=True)
        return self.writing_job(owner, sid)

    def recover_job(self, owner, sid):
        """After a restart, a lost worker or an expired lease: a job left waiting, or whose
        segment ended without evaluation, is handed to its driver again (same job, budget
        and scope). Cheap while a run holds a live lease (two columns)."""
        from .writing_job import is_active
        if background_held():
            return  # The launcher's read-only start-up checks are still running.
        try:
            with self.session_factory() as db:
                lease = db.execute(select(ScholarV4Session.active_run_id, ScholarV4Session.lease_until)
                    .where(ScholarV4Session.id == sid, ScholarV4Session.owner_id == owner)).first()
                if lease is None or (lease.active_run_id and lease.lease_until and lease.lease_until >= datetime.utcnow()):
                    return
                row = db.scalar(select(ScholarV4Session).where(ScholarV4Session.id == sid, ScholarV4Session.owner_id == owner))
                job = row.data.get("writing_job")
            if not is_active(job):
                self._job_sessions.pop(sid, None)
                return
            self._job_sessions[sid] = owner
            self.dispatch_job(owner, sid, job["id"])
        except Exception as exc:
            logger.warning("EnnoScholar V4 writing job recovery failed type=%s", type(exc).__name__)

    def discover_jobs(self, *, every_job=False):
        """Register the conversations whose job may need a driver, read from the database
        (jobs opened by any process). Running and waiting jobs are matched in the stored
        JSON without loading it; every_job (start-up) matches any job."""
        from sqlalchemy import String, cast, or_
        text = cast(ScholarV4Session.data, String)
        # The job's id comes first in its object; its status follows somewhere after it (other
        # keys, such as kind, may sit in between). A false match is only checked, never acted on.
        pattern = '%"writing_job": {"id": "job_' + "_" * 32 + '"%"status": "STATUS"%'
        condition = (text.like('%"writing_job"%') if every_job else
            or_(text.like(pattern.replace("STATUS", "running")), text.like(pattern.replace("STATUS", "waiting"))))
        try:
            with self.session_factory() as db:
                rows = db.execute(select(ScholarV4Session.id, ScholarV4Session.owner_id).where(condition)).all()
        except Exception as exc:
            logger.warning("EnnoScholar V4 writing job discovery failed type=%s", type(exc).__name__)
            return
        for sid, owner in rows:
            self._job_sessions.setdefault(sid, owner)

    def recover_all_jobs(self):
        """At start-up: every job of this database is registered and resumed when its lease
        allows it (recover_job drops the finished ones)."""
        self.discover_jobs(every_job=True)
        for sid, owner in list(self._job_sessions.items()):
            self.recover_job(owner, sid)

    def watchdog_tick(self, *, rescan=False):
        if rescan:
            self.discover_jobs()
        for sid, owner in list(self._job_sessions.items()):
            self.recover_job(owner, sid)

    def watchdog(self, interval=60.0, stop_event=None, first_delay=0.0):
        """Periodic recovery without any browser open: registered jobs every interval (two
        columns while a lease is live), the database rescanned for jobs of other processes."""
        stop_event = stop_event or threading.Event()
        if stop_event.wait(first_delay):
            return
        while not _background_ready.wait(0.2):
            if stop_event.is_set():
                return
        self.recover_all_jobs()
        scanned = time.monotonic()
        while not stop_event.wait(interval):
            rescan = time.monotonic()-scanned >= WATCHDOG_RESCAN_SECONDS
            self.watchdog_tick(rescan=rescan)
            if rescan:
                scanned = time.monotonic()

    def claim_dispatch(self, owner, sid, job_id):
        """Cross-process coordination: one process at a time hands a job to its driver. The
        claim is written in the job under the session's lock and expires on its own."""
        from .writing_job import is_active
        with self.session_factory() as db:
            lock_rows(db, sid)
            row = db.scalar(select(ScholarV4Session).where(ScholarV4Session.id == sid, ScholarV4Session.owner_id == owner))
            job = (row.data.get("writing_job") if row else None) or {}
            claim = job.get("dispatch") or {}
            live = bool(claim.get("until")) and claim["until"] > datetime.utcnow().isoformat()
            if job.get("id") != job_id or not is_active(job) or (live and claim.get("process") != self.process_id):
                db.rollback()
                return False
            job = {**job, "dispatch": {"process": self.process_id,
                "until": (datetime.utcnow() + timedelta(seconds=DISPATCH_CLAIM_SECONDS)).isoformat()}}
            row.data = {**row.data, "writing_job": job}
            db.commit()
            return True

_service = None
_guard = threading.Lock()


def get_service():
    """The process's service, without background activity: the job watchdog is started by
    FastAPI's start-up (start_watchdog), never by a Celery worker or the launcher's checks."""
    global _service
    with _guard:
        if _service is None:
            from db.database import SessionLocal
            _service = ScholarV4Service(SessionLocal)
        return _service


# Watchdog of automatic drafting jobs: first scan once background work is allowed, then
# every minute; the database is rescanned for jobs opened by other processes every few
# minutes.
WATCHDOG_FIRST_DELAY = 0.0
WATCHDOG_INTERVAL = 60.0
WATCHDOG_RESCAN_SECONDS = 300.0
_watchdog = {"thread": None, "stop": None}
# Background work (job recovery) waits for this gate. Open by default; a launcher that
# verifies the start-up in read-only mode closes it first (hold_background) and opens it
# when its checks have ended (release_background), so nothing is written before.
_background_ready = threading.Event()
_background_ready.set()


def hold_background():
    _background_ready.clear()


def release_background():
    _background_ready.set()


def background_held():
    return not _background_ready.is_set()


def start_watchdog(service=None):
    service = service or get_service()
    if not service.config.writing_job_enabled:
        return None
    with _guard:
        if _watchdog["thread"] is not None and _watchdog["thread"].is_alive():
            return _watchdog["thread"]
        stop = threading.Event()
        thread = threading.Thread(target=service.watchdog, name="scholar-v4-jobs", daemon=True,
            kwargs={"interval": WATCHDOG_INTERVAL, "stop_event": stop, "first_delay": WATCHDOG_FIRST_DELAY})
        _watchdog.update(thread=thread, stop=stop)
        thread.start()
        return thread


def stop_watchdog():
    with _guard:
        thread, stop = _watchdog["thread"], _watchdog["stop"]
        _watchdog.update(thread=None, stop=None)
    if stop is not None:
        stop.set()
    if thread is not None:
        thread.join(5)
