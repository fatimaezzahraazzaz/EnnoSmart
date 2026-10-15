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
from .session import ScholarV4Session, ScholarV4Run, sync_records

logger = logging.getLogger(__name__)
TERMINAL = {"completed", "partial", "failed", "cancelled"}


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

    def owned(self, db, owner, sid):
        row = db.scalar(select(ScholarV4Session).where(ScholarV4Session.id == sid, ScholarV4Session.owner_id == owner))
        if row is None:
            raise SessionNotFound("Conversation introuvable.")
        return row

    def create(self, owner, title="Nouvelle conversation"):
        with self.session_factory() as db:
            row = ScholarV4Session(id=uuid.uuid4().hex, owner_id=owner, title=title,
                data={"messages": [], "articles": {}, "research_state": {}, "summary": "", "project_id": None, "selected_verrous": [], "focus": {}, "outline_versions": []})
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
        return result

    def sessions(self, owner):
        with self.session_factory() as db:
            rows = db.scalars(select(ScholarV4Session).where(ScholarV4Session.owner_id == owner)
                .order_by(ScholarV4Session.updated_at.desc()).limit(100)).all()
            return [self.session_read(row, False) for row in rows]

    def document_action(self, owner, sid, payload):
        import copy
        from .manuscript import approve_outline, save_document_revision
        from .toolkit import ScholarTools
        with self.session_factory() as db:
            row = self.owned(db, owner, sid)
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
        from .scientific_scope import literature_coverage
        from .scientific_review import deterministic_review
        from .toolkit import ScholarTools
        with self.session_factory() as db:
            data = self.owned(db, owner, sid).data
            registry = {aid: Article.model_validate(a) for aid, a in data.get("articles", {}).items()}
            used = set(eid for s in data.get("outline", {}).get("sections", [])
                for eid in [*s.get("evidence_ids", []), *[e for d in s.get("drafts", []) for e in d.get("evidence_ids", [])]])
            used.update(e for r in data.get("scientific_relations", []) for e in r["evidence_ids"])
            used.update(e for f in data.get("scientific_figures", []) for e in f["evidence_ids"])
            return {"outline": data.get("outline", {}), "document": data.get("document", {}),
                "scientific_scope": data.get("scientific_scope", {}), "coverage": literature_coverage(data, registry),
                "relations": data.get("scientific_relations", []), "figures": data.get("scientific_figures", []),
                "review": deterministic_review(copy.deepcopy(data), ScholarTools(copy.deepcopy(data), None, self.config)),
                "versions": [{k:v for k,v in r.items() if k not in {"outline", "figures", "article_metadata"}} for r in data.get("document_versions", [])],
                "articles": [a.compact() for a in registry.values()],
                "evidence": {eid:data["evidence"][eid] for eid in used if eid in data.get("evidence", {})}}

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
            row = db.scalar(select(ScholarV4Session).where(ScholarV4Session.id == sid, ScholarV4Session.owner_id == owner).with_for_update())
            if row.active_run_id:
                raise SessionBusy("Une recherche a démarré ; réessayez l'import après sa fin.")
            current = copy.deepcopy(row.data)
            current.setdefault("articles", {})[article.id] = article.durable()
            current.setdefault("focus", {})["article_id"] = article.id
            row.data, row.updated_at = current, datetime.utcnow()
            sync_records(db, row)
            db.commit()
        return article.compact()

    def select_article(self, owner, sid, aid, selected):
        with self.session_factory() as db:
            row = db.scalar(select(ScholarV4Session).where(ScholarV4Session.id == sid, ScholarV4Session.owner_id == owner).with_for_update())
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
            outline = data.get("outline", {})
            if outline.get("sections"):
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
            row = self.owned(db, owner, sid)
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
            row = db.scalar(select(ScholarV4Session).where(ScholarV4Session.id == sid, ScholarV4Session.owner_id == owner).with_for_update())
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
            row = self.owned(db, owner, sid)
            if row.active_run_id and row.lease_until and row.lease_until < datetime.utcnow():
                rid = row.active_run_id
                changed = db.execute(update(ScholarV4Session).where(ScholarV4Session.id == sid,
                    ScholarV4Session.active_run_id == rid, ScholarV4Session.lease_until < datetime.utcnow())
                    .values(active_run_id=None, lease_until=None))
                if changed.rowcount:
                    run = db.get(ScholarV4Run, rid)
                    if run and run.status not in TERMINAL:
                        run.status = "partial"
                        run.data = {**run.data, "reason": "worker_interrupted", "answer": "Recherche interrompue ou délai dépassé. Les échanges précédents sont conservés ; relancez votre demande."}
                db.commit()

    def start(self, owner, request):
        self.config.validate()
        if not self.slots.acquire(blocking=False):
            raise SessionBusy("Les recherches V4 sont occupées. Réessayez dans un instant.")
        try:
            sid = request.session_id or self.create(owner)["id"]
            self.recover_expired(owner, sid)
            rid = uuid.uuid4().hex
            with self.session_factory() as db:
                self.owned(db, owner, sid)
                changed = db.execute(update(ScholarV4Session).where(ScholarV4Session.id == sid,
                    ScholarV4Session.owner_id == owner, ScholarV4Session.active_run_id.is_(None)).values(
                    active_run_id=rid, lease_until=datetime.utcnow() + timedelta(seconds=self.config.timeout_seconds + 30)))
                if not changed.rowcount:
                    raise SessionBusy("Une recherche est déjà en cours dans cette conversation.")
                db.add(ScholarV4Run(id=rid, session_id=sid, owner_id=owner, status="queued",
                    data={"activity": [], "usage": {}, "answer": "", "citations": [], "request": request.message}))
                row = db.get(ScholarV4Session, sid)
                data = copy.deepcopy(row.data)
                data.setdefault("messages", []).append({"id": uuid.uuid4().hex, "role": "user", "content": request.message})
                row.data = data
                if row.title == "Nouvelle conversation":
                    row.title = request.message[:100]
                row.updated_at = datetime.utcnow()
                sync_records(db, row)
                db.commit()
            self.pool.submit(self.execute, owner, sid, rid)
            return self.run(owner, rid)
        except BaseException:
            self.slots.release()
            raise

    def run(self, owner, rid):
        with self.session_factory() as db:
            row = db.scalar(select(ScholarV4Run).where(ScholarV4Run.id == rid, ScholarV4Run.owner_id == owner))
            if row is None:
                raise SessionNotFound("Recherche introuvable.")
            sid = row.session_id
        self.recover_expired(owner, sid)
        with self.session_factory() as db:
            row = db.get(ScholarV4Run, rid)
            return {"id": row.id, "session_id": row.session_id, "status": row.status, "cancel_requested": row.cancel_requested,
                "created_at": iso(row.created_at), "updated_at": iso(row.updated_at),
                **{k: v for k, v in row.data.items() if k != "request"}}

    def cancel(self, owner, rid):
        with self.session_factory() as db:
            row = db.scalar(select(ScholarV4Run).where(ScholarV4Run.id == rid, ScholarV4Run.owner_id == owner))
            if row is None:
                raise SessionNotFound("Recherche introuvable.")
            if row.status not in TERMINAL:
                row.cancel_requested = True
                db.commit()
        return self.run(owner, rid)

    def execute(self, owner, sid, rid):
        from .agent import ScholarChatAgent, StopResearch
        data = None
        try:
            with self.session_factory() as db:
                row = self.owned(db, owner, sid)
                data = copy.deepcopy(row.data)
                run = db.get(ScholarV4Run, rid)
                run.status = "running"
                db.commit()
            def cancelled():
                with self.session_factory() as db:
                    run = db.get(ScholarV4Run, rid)
                    return run is None or run.cancel_requested or run.status in TERMINAL
            def activity(event):
                with self.session_factory() as db:
                    run = db.get(ScholarV4Run, rid)
                    if run.status in TERMINAL:
                        return
                    run.data = {**run.data, "activity": [*run.data.get("activity", []), event][-200:]}
                    run.updated_at = datetime.utcnow()
                    if event.get("status") in {"completed", "rejected"} and event.get("tool") in {
                            "responses", "search_scientific", "search_collected_articles", "get_article", "get_fulltext",
                            "analyze_article", "record_candidate_decisions", "verify_bibliography", "update_research_state",
                            "find_similar_articles", "compare_articles", "identify_research_gaps", "finalize_response",
                            "write_section", "manage_outline", "create_scientific_visual", "review_document",
                            "review_literature_coverage", "scientific_reading", "record_project_facts",
                            "build_argument_blueprint", "argumentation", "writing_progress"}:
                        session = db.scalar(select(ScholarV4Session).where(ScholarV4Session.id == sid,
                            ScholarV4Session.owner_id == owner).with_for_update())
                        if session.active_run_id == rid:
                            checkpoint = copy.deepcopy(data)
                            for aid, article in session.data.get("articles", {}).items():
                                if aid in checkpoint.get("articles", {}):
                                    checkpoint["articles"][aid]["selected"] = article.get("selected", False)
                            checkpoint["selection_decisions"] = session.data.get("selection_decisions", [])
                            session.data = checkpoint
                            sync_records(db, session)
                    db.commit()
            agent = self.agent_factory() if self.agent_factory else ScholarChatAgent(self.config)
            result = agent.run(data, emit=activity, cancelled=cancelled)
            self.finish(owner, sid, rid, data, result, "cancelled" if result.get("reason") == "cancelled" else "partial" if result.get("partial") else "completed")
        except Exception as exc:
            # Only class names, never request bodies, upstream URLs or secrets.
            logger.warning("EnnoScholar V4 run failed type=%s", type(exc).__name__)
            self.finish(owner, sid, rid, data, {"answer": "La recherche n'a pas pu terminer. Vérifiez les accès OpenAI et réessayez. Les échanges et articles déjà récupérés sont conservés.",
                "reason": "upstream_error", "error_type": type(exc).__name__, "partial": True}, "failed")
        finally:
            self.slots.release()

    def finish(self, owner, sid, rid, data, result, status):
        with self.session_factory() as db:
            row = db.scalar(select(ScholarV4Session).where(ScholarV4Session.id == sid, ScholarV4Session.owner_id == owner).with_for_update())
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
            data.setdefault("messages", []).append({"id": uuid.uuid4().hex, "role": "assistant", "content": result["answer"],
                "citations": result.get("citations", []), "run_id": rid, "partial": status != "completed",
                **{key: result.get(key) for key in ("primary_article_ids", "displayed_article_ids", "complementary_count", "discovered_sources_count", "fallback", "library_counts", "coverage_sufficient", "publication_accepted", "research_status")}})
            row.data, row.active_run_id, row.lease_until, row.updated_at = data, None, None, datetime.utcnow()
            run.data = {**run.data, **result}
            run.status, run.updated_at = status, datetime.utcnow()
            sync_records(db, row)
            db.commit()


_service = None
_guard = threading.Lock()


def get_service():
    global _service
    with _guard:
        if _service is None:
            from db.database import SessionLocal
            _service = ScholarV4Service(SessionLocal)
        return _service
