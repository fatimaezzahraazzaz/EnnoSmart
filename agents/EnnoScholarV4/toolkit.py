"""Structured capabilities of the single conversation model. No LLM calls here."""
from __future__ import annotations

import json
import copy
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Literal
from urllib.parse import unquote

from pydantic import BaseModel, ConfigDict, Field

from .evidence import passages, choose_passages, validate_claims, snapshot, exact_source_valid
from .tools.fulltext import get_fulltext
from .models import Article, normalize_doi, public_link
from .store import ArticleStore, compact
from .tools.outline import OutlineArgs, manage_outline, resolve_section
from .context import evidence_projection

_reading_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="scholar-v4-reading")


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchArgs(Args):
    query: str = Field(min_length=1, max_length=1500)
    sources: list[Literal["semantic_scholar", "openalex", "arxiv"]] | None
    limit_per_source: int = Field(ge=1, le=100)
    offset: int = Field(ge=0, le=10000)
    family: str = Field(min_length=1, max_length=160)


class CollectedArgs(Args):
    query: str = Field(max_length=1500)
    limit: int = Field(ge=1, le=12)
    exclude_presented: bool
    family: str | None


class ArticlesArgs(Args):
    identifiers: list[str] = Field(min_length=1, max_length=8)


class FulltextArgs(Args):
    article_id: str
    candidate_urls: list[str] | None = Field(max_length=5)
    page_start: int | None = Field(ge=1)
    page_limit: int | None = Field(ge=1, le=20)


class Claim(Args):
    statement: str = Field(min_length=1, max_length=2500)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)
    origin: Literal["source_statement", "inference"]


class Analysis(Args):
    problem: list[Claim] = Field(max_length=8)
    technical_mechanism: list[Claim] = Field(max_length=8)
    experimental_setup: list[Claim] = Field(max_length=8)
    objective: list[Claim] = Field(max_length=8)
    method: list[Claim] = Field(max_length=8)
    contribution: list[Claim] = Field(max_length=8)
    hypotheses: list[Claim] = Field(max_length=8)
    results: list[Claim] = Field(max_length=8)
    limitations: list[Claim] = Field(max_length=8)
    generalization: list[Claim] = Field(max_length=8)
    open_questions: list[Claim] = Field(max_length=8)
    relevance: list[Claim] = Field(max_length=8)
    method_family: str = Field(max_length=160)
    analysis_limit: str = Field(max_length=2500)


class AnalyzeArgs(Args):
    article_ids: list[str] = Field(min_length=1, max_length=4)
    action: Literal["read", "save"]
    research_context: str = Field(max_length=5000)
    passage_query: str = Field(max_length=1500)
    passage_offset: int = Field(ge=0)
    passages_per_article: int = Field(ge=1, le=8)
    analysis: Analysis | None
    revisit: bool = False


class SimilarArgs(Args):
    article_id: str
    mode: Literal["similar", "alternatives", "references", "citations"]
    conceptual_query: str | None = Field(max_length=1500)
    family: str = Field(max_length=160)


class CompareArgs(Args):
    article_ids: list[str] = Field(min_length=2, max_length=8)
    research_context: str = Field(max_length=5000)
    conclusions: list[Claim] | None = Field(max_length=20)


class Gap(Args):
    statement: str = Field(min_length=1, max_length=2500)
    origin: Literal["author_limit", "comparative_inference", "corpus_absence"]
    article_ids: list[str] = Field(max_length=20)
    evidence_ids: list[str] = Field(max_length=20)
    scope: str = Field(min_length=1, max_length=2500)


class GapsArgs(Args):
    article_ids: list[str] = Field(min_length=1, max_length=12)
    research_context: str = Field(max_length=5000)
    gaps: list[Gap] | None = Field(max_length=20)


class VerifyArgs(Args):
    article_ids: list[str] = Field(min_length=1, max_length=20)


class Decision(Args):
    article_id: str
    decision: Literal["retain", "reject", "pending"]
    justification: str = Field(min_length=1, max_length=2500)


class DecisionsArgs(Args):
    decisions: list[Decision] = Field(min_length=1, max_length=20)
    finalist_ids: list[str] | None = Field(max_length=20)
    covered_families: list[str] = Field(max_length=30)
    remaining_axes: list[str] = Field(max_length=30)


class MemoryArgs(Args):
    topic: str = Field(max_length=2000)
    objectives: list[str] = Field(max_length=20)
    hypotheses: list[str] = Field(max_length=20)
    user_defined_locks: list[str] = Field(max_length=20)
    consultant_decisions: list[str] = Field(max_length=20)
    search_axes: list[str] = Field(max_length=30)
    outline: list[str] = Field(max_length=30)
    scientific_question: str | None = Field(default=None, max_length=3000)
    technical_object: str | None = Field(default=None, max_length=2000)
    approach: str | None = Field(default=None, max_length=2000)
    application: str | None = Field(default=None, max_length=2000)
    constraints: list[str] | None = Field(default=None, max_length=30)
    exclusions: list[str] | None = Field(default=None, max_length=30)
    clarifications: list[str] | None = Field(default=None, max_length=30)


class AnalysisSave(Args):
    article_id: str
    analysis: Analysis


class FinalArgs(Args):
    answer: str = Field(min_length=1, max_length=30000)
    article_ids: list[str] = Field(max_length=20)
    evidence_ids: list[str] = Field(max_length=80)
    coverage_sufficient: bool
    remaining_uncertainties: list[str] = Field(max_length=30)
    analyses: list[AnalysisSave] | None = Field(default=None, max_length=10)
    displayed_article_ids: list[str] | None = Field(default=None, max_length=20)


class SectionSearchArgs(Args):
    section_id: str | None
    query: str = Field(min_length=1, max_length=1500)
    limit_per_source: int = Field(ge=1, le=80)


class WriteArgs(Args):
    section_id: str | None
    action: Literal["read", "save"]
    instructions: str = Field(max_length=5000)
    text: str | None = Field(max_length=30000)
    article_ids: list[str] = Field(max_length=80)
    evidence_ids: list[str] = Field(max_length=80)


class HistoryArgs(Args):
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=20)


TOOLS = {
    "manage_outline": (OutlineArgs, "Créer/lire/modifier le plan avec identifiants stables et versions. rename modifie seulement un titre, sans recherche. section_id accepte aussi sa position 1, 2..."),
    "search_for_section": (SectionSearchArgs, "Rechercher pour une section du plan. Formule query selon sujet, section, autres sections et références existantes. section_id null utilise la dernière section ciblée."),
    "write_section": (WriteArgs, "read fournit les analyses et preuves d'une section ; save enregistre TA rédaction avec les preuves réellement consultées. Aucune génération simulée."),
    "read_conversation": (HistoryArgs, "Relire les messages anciens exclus du contexte compact, sans effacer les décisions exactes."),
    "search_scientific": (SearchArgs, "Collecter dans les index scientifiques, paginables. Tout est conservé côté backend, une petite fenêtre est proposée par recherche locale."),
    "search_collected_articles": (CollectedArgs, "Parcourir les candidats conservés sans classement scientifique numérique. Exclure les candidats déjà présentés pour des fenêtres complémentaires."),
    "get_article": (ArticlesArgs, "Lire/résoudre des articles par ID, DOI, arXiv, URL ou titre exact. Les notices complètes restent accessibles, finalistes compris."),
    "get_fulltext": (FulltextArgs, "Récupérer légalement PDF/HTML paginés, sans confondre disponibilité, extraction et analyse."),
    "analyze_article": (AnalyzeArgs, "read : présenter des passages exacts ciblés. save : enregistrer l'analyse scientifique que TU as produite avec ces preuves. Aucun appel LLM supplémentaire."),
    "find_similar_articles": (SimilarArgs, "Explorer références/citations ou requêtes conceptuelles après compréhension de la contribution du travail source."),
    "compare_articles": (CompareArgs, "Obtenir une matrice des analyses/preuves ; conclusions non nulles enregistrent TA comparaison scientifique avec ses evidence_ids."),
    "identify_research_gaps": (GapsArgs, "Comparer les limites et questions ouvertes. gaps non null enregistre TES lacunes avec origine et périmètre explicites, sans valider une éligibilité CIR."),
    "verify_bibliography": (VerifyArgs, "Vérifier identités titre/DOI/arXiv/auteurs/année, versions, unicité canonique, accès réel et provenance des preuves avant la synthèse."),
    "record_candidate_decisions": (DecisionsArgs, "Consigner articles effectivement examinés, motifs de rejet/rétention et classement cumulatif révisable. Aucun scoring Python scientifique."),
    "update_research_state": (MemoryArgs, "Mémoriser sujet, objectifs, hypothèses, verrous, axes, décisions et plan de la conversation."),
    "finalize_response": (FinalArgs, "Valider et publier TA réponse après contrôle bibliographique. Utiliser [paper_ID] et [ev_ID] pour des liens exacts. Les incertitudes sont conservées."),
}


def definitions():
    def strict_schema(value):
        if isinstance(value, dict):
            value.pop("default", None)
            if "properties" in value:
                value["required"] = list(value["properties"])
                value["additionalProperties"] = False
            for item in value.values():
                strict_schema(item)
        elif isinstance(value, list):
            for item in value:
                strict_schema(item)
        return value
    return [{"type": "function", "name": name, "description": description, "strict": True,
        "parameters": strict_schema(model.model_json_schema())} for name, (model, description) in TOOLS.items()]


class ScholarTools:
    def __init__(self, state, sources, config, check=lambda: None, activity=lambda *a, **k: None, encoder=None):
        self.state, self.sources, self.config, self.check, self.activity = state, sources, config, check, activity
        self.store = ArticleStore(state, config.max_articles, encoder)
        self.registry = self.store.registry
        self.evidence = state.setdefault("evidence", {})
        self.snapshots = state.setdefault("source_snapshots", {})
        self.reading_windows = state.setdefault("reading_windows", [])
        self.shown_evidence = state.setdefault("shown_evidence_ids", [])
        self.search_count = 0
        self.finished = None
        self.checked_ids = set()
        self.deep_analysis_ids = set()
        self.new_candidates = 0
        self.finalization_only = False
        self.metadata_detail_ids = set()

    def save(self):
        self.store.save()

    def delivered(self, window_ids):
        self.store.delivered(window_ids)
        for window in self.reading_windows:
            if window["status"] == "queued" and window["window_id"] in window_ids:
                window["status"] = "presented"
                for eid in window["evidence_ids"]:
                    if eid not in self.shown_evidence:
                        self.shown_evidence.append(eid)

    def search(self, args, deadline):
        if self.search_count >= self.config.max_research_rounds:
            return {"warning": "Budget de recherches atteint ; corpus conservé et explorations locales disponibles."}
        key = [" ".join(args.query.split()), sorted(args.sources or ["semantic_scholar", "openalex", "arxiv"]), args.offset, args.limit_per_source]
        searches = self.state.setdefault("recent_searches", [])
        if any(s.get("key") == key and not s.get("warnings") for s in searches):
            return {"warning": "Cette recherche a déjà été collectée. Explorez localement ou changez l'axe/la page."}
        self.search_count += 1
        if self.new_candidates >= self.config.max_search_results:
            return {"warning": "Limite de nouveaux candidats atteinte ; utilisez les articles conservés."}
        rows, warnings = self.sources.search(args.query, args.sources, min(args.limit_per_source, self.config.max_search_results),
            min(deadline, time.monotonic() + 35), self.check, offset=args.offset)
        self.check()
        rows = rows[:self.config.max_search_results-self.new_candidates]
        self.new_candidates += len(rows)
        ids, dropped = self.store.insert(rows, args.family)
        searches.append({"key": key, "query": args.query, "family": args.family, "article_ids": ids, "warnings": warnings})
        proposed = [self.registry[aid] for aid in ids]
        info = {"method": "all_new_candidates", "scores_are_scientific_ranking": False}
        return {**self.store.window(proposed, args.query), "collected": len(ids), "corpus_total": len(self.registry),
            "warnings": warnings, "capacity_dropped": dropped, "retrieval": info,
            "guidance": "Les articles non présentés restent recherchables. Réorientez les concepts, explorez les voisins ou utilisez offset pour les pages fournisseurs suivantes."}

    def verify(self, ids, deadline, local_only=False):
        rows = []
        for aid in ids:
            if not local_only:
                self.check()
            article = self.store.article(aid)
            if not local_only and aid not in self.checked_ids:
                if article.doi:
                    probe = article.model_copy(deep=True)
                    # An arXiv notice alone cannot validate an arbitrary journal DOI/title association.
                    probe.verified = False
                    checked = self.sources.verify(probe, min(deadline, time.monotonic()+12))
                    article.verified = checked.verified
                    article.metadata_errors = checked.metadata_errors
                    article.verification_sources = checked.verification_sources
                    if checked.verified:
                        article.title, article.authors, article.year = checked.title, checked.authors, checked.year
                else:
                    resolved = self.sources.metadata(article.arxiv_id or article.url, min(deadline, time.monotonic()+20), self.check)
                    self.store.insert(resolved)
            valid_evidence = all(e["source_article_id"] == article.id and e["evidence_id"] in self.evidence and
                self.evidence[e["evidence_id"]] == e and exact_source_valid(e, self.snapshots)
                for e in article.analysis.get("evidence", []))
            valid = bool(article.verified and article.title and article.authors and article.year and not article.metadata_errors and valid_evidence
                and (not article.doi or normalize_doi(article.doi) == article.doi))
            if article.fulltext.get("source_kind") in {"user_uploaded_pdf", "user_uploaded_document"} and not article.doi:
                valid = bool(article.title and article.fulltext.get("text") and valid_evidence)
            if valid:
                self.checked_ids.add(aid)
            rows.append({"article_id": aid, "valid": valid, "title": article.title, "doi": article.doi,
                "arxiv_id": article.arxiv_id, "authors": article.authors, "year": article.year,
                "canonical_work_id": article.canonical_work_id or article.id, "source_version": article.source_version,
                "access_status": article.access_status, "access_level": article.access_level, "analysis_basis": article.analysis_basis,
                "fulltext_available": article.fulltext_available, "publisher_access": article.publisher_access,
                "verification_sources": article.verification_sources, "errors": article.metadata_errors,
                "evidence_integrity": valid_evidence})
        self.save()
        return {"articles": rows, "valid": all(r["valid"] for r in rows)}

    def claim_union(self, claims, ids):
        for claim in claims:
            if any(eid not in self.evidence or eid not in self.shown_evidence or self.evidence[eid]["article_id"] not in ids
                for eid in claim["evidence_ids"]):
                raise ValueError("Comparaison sans preuve réellement lue dans les articles concernés.")
            if any(not exact_source_valid(self.evidence[eid], self.snapshots) for eid in claim["evidence_ids"]):
                raise ValueError("Preuve comparative incohérente avec le document source.")

    def finalize(self, args, deadline):
        from .publication import publish
        return publish(self, args, deadline)

    def execute(self, name, raw, deadline):
        self.check()
        if self.finalization_only and name != "finalize_response":
            raise ValueError("La phase de publication utilise uniquement les résultats conservés.")
        if name not in TOOLS:
            raise ValueError("Outil inconnu.")
        args = TOOLS[name][0].model_validate(raw)
        if name == "read_conversation":
            messages = self.state.get("messages", [])
            return {"messages": messages[args.offset:args.offset+args.limit], "total": len(messages)}
        if name == "manage_outline":
            return manage_outline(self.state, args, self.registry)
        if name == "search_for_section":
            section = resolve_section(self.state, args.section_id)
            self.state.setdefault("focus", {})["section_id"] = section["id"]
            result = self.search(SearchArgs(query=args.query, sources=None, limit_per_source=args.limit_per_source,
                offset=0, family=section["title"]), deadline)
            return {**result, "section": section, "guidance": "Évalue la correspondance scientifique avant manage_outline(associate). N'associe pas automatiquement tous les résultats."}
        if name == "write_section":
            section = resolve_section(self.state, args.section_id)
            self.state.setdefault("focus", {})["section_id"] = section["id"]
            if args.action == "read":
                ids = list(dict.fromkeys(section["article_ids"] + args.article_ids))
                return {"section": section, "articles": [compact(self.store.article(aid), 1200) for aid in ids],
                    "guidance": "Lis les analyses et leurs passages avec analyze_article. Signale les sources insuffisantes et les limites de comparabilité."}
            if not args.text or not args.article_ids or not args.evidence_ids:
                raise ValueError("La rédaction sauvegardée exige du texte, des articles et des preuves consultées.")
            self.claim_union([{"evidence_ids": args.evidence_ids}], args.article_ids)
            if any(not self.store.article(aid).analysis for aid in args.article_ids):
                raise ValueError("Analyser les sources avant de sauvegarder une section scientifique.")
            # Validate every bibliography link before committing the draft.
            previous = self.finished
            previous_response = self.state.get("last_response")
            previous_history_length = len(self.state.get("response_history", []))
            checked = self.finalize(FinalArgs(answer=args.text, article_ids=args.article_ids, evidence_ids=args.evidence_ids,
                coverage_sufficient=False, remaining_uncertainties=[]), deadline)
            if not checked.get("published"):
                return checked
            rendered = self.finished["answer"]
            self.finished = previous
            if previous_response is None:
                self.state.pop("last_response", None)
            else:
                self.state["last_response"] = previous_response
            del self.state["response_history"][previous_history_length:]
            old_outline = copy.deepcopy(self.state["outline"])
            section.setdefault("drafts", []).append({"id": uuid.uuid4().hex, "text": rendered,
                "instructions": args.instructions, "article_ids": args.article_ids, "evidence_ids": args.evidence_ids})
            self.state.setdefault("outline_versions", []).append(old_outline)
            self.state["outline"]["version"] += 1
            return {"saved": True, "section_id": section["id"], "text": rendered}
        if name == "search_scientific":
            return self.search(args, deadline)
        if name == "search_collected_articles":
            rows, info = self.store.search(args.query, args.limit, args.exclude_presented, args.family)
            return {**self.store.window(rows, args.query), "retrieval": info, "corpus_total": len(self.registry)}
        if name == "get_article":
            ids = []
            for identifier in args.identifiers:
                self.check()
                if identifier in {"cet article", "current", "selected"}:
                    identifier = self.state.get("focus", {}).get("article_id", "")
                elif identifier.isdigit():
                    ordered = self.state.get("displayed_article_ids") or self.state.get("last_response", {}).get("cited_article_ids", []) or self.state.get("evaluation", {}).get("recommended_article_ids", [])
                    if 1 <= int(identifier) <= len(ordered):
                        identifier = ordered[int(identifier)-1]
                if identifier in self.registry:
                    ids.append(identifier)
                else:
                    rows = self.sources.metadata(identifier, min(deadline, time.monotonic()+25), self.check)
                    inserted, _ = self.store.insert(rows)
                    ids.extend(inserted)
            if ids:
                self.state.setdefault("focus", {})["article_id"] = ids[-1]
            result = self.store.window([self.store.article(aid) for aid in dict.fromkeys(ids)], "identities", "metadata_read")
            result["articles"] = []
            for aid in dict.fromkeys(ids):
                a = self.store.article(aid)
                result["articles"].append(compact(a, 3500) if aid not in self.metadata_detail_ids else
                    {"id": aid, "title": a.title, "access_status": a.access_status, "analysis_basis": a.analysis_basis,
                     "already_provided": True, "guidance": "Notice et analyses conservées ; lire les preuves avec analyze_article si nécessaire."})
                self.metadata_detail_ids.add(aid)
            return result
        if name == "get_fulltext":
            article = self.store.article(args.article_id)
            self.state.setdefault("focus", {})["article_id"] = article.id
            if article.id not in self.deep_analysis_ids and len(self.deep_analysis_ids) >= self.config.max_deep_analysis:
                return {"warning": "Limite des lectures approfondies atteinte."}
            self.deep_analysis_ids.add(article.id)
            if not article.fulltext.get("text"):
                future = _reading_pool.submit(get_fulltext, article.model_copy(deep=True), self.sources, deadline, self.check, args.candidate_urls, self.config)
                try:
                    result = future.result(timeout=max(.001, min(48, deadline-time.monotonic())))
                except FutureTimeout:
                    future.cancel(); self.check()
                    return {"article_id": article.id, "available": None, "warning": "Lecture interrompue ; accès intégral inconnu."}
                self.check()
                article.fulltext = result
                article.access_level = result["access_level"]
                article.access_status = result["access_status"]
                if result.get("text"):
                    article.fulltext_available, article.fulltext_source = True, result["url"]
            if args.page_start is not None:
                from .pdf_store import extract_pages
                if not article.fulltext.get("pdf_file"):
                    return {"warning": "Cette source n'est pas un PDF conservé ; pas de pagination disponible."}
                chunks, page_count = extract_pages(article.fulltext["pdf_file"], args.page_start, args.page_limit or 5)
                by_page = {chunk["page"]: chunk for chunk in article.fulltext.get("chunks", []) if chunk.get("page")}
                by_page.update({chunk["page"]: chunk for chunk in chunks})
                article.fulltext["chunks"] = [by_page[page] for page in sorted(by_page)]
                article.fulltext["text"] = "\n\n".join(chunk["text"] for chunk in article.fulltext["chunks"])
                article.fulltext["page_count"] = page_count
                # Page presence alone does not mean every page was read by GPT.
                article.fulltext["truncated"] = len(by_page) < page_count
            self.save()
            return {"article_id": article.id, **{k: v for k, v in article.fulltext.items() if k not in {"text", "chunks"}},
                "analysis_basis": article.analysis_basis, "guidance": "analyze_article(action=read) présente les passages ; tu produis ensuite l'analyse avec action=save."}
        if name == "analyze_article":
            if args.action == "save":
                if len(args.article_ids) != 1 or args.analysis is None:
                    raise ValueError("save exige exactement un article et son analyse.")
                a = self.store.article(args.article_ids[0])
                from .tools.article_analysis import ArticleAnalyzer
                validated = ArticleAnalyzer.save(a, args.analysis, self.evidence, self.shown_evidence, self.snapshots)
                self.store.examined_article(a.id)
                self.save()
                return {"saved": True, "article_id": a.id, "access_status": a.access_status,
                    "analysis_basis": a.analysis_basis, "evidence_ids": [e["evidence_id"] for e in a.analysis["evidence"]]}
            result = []
            for aid in args.article_ids:
                a = self.store.article(aid)
                all_rows = passages(a)
                source = snapshot(a)
                self.snapshots[source["fingerprint"]] = source
                chosen = choose_passages(all_rows, args.passage_query, args.passage_offset, args.passages_per_article)
                blocked = set(self.shown_evidence)
                blocked.update(eid for w in self.reading_windows if w["status"] == "queued" for eid in w["evidence_ids"])
                rows = chosen if args.revisit else [r for r in chosen if r["evidence_id"] not in blocked]
                for row in rows:
                    self.evidence[row["evidence_id"]] = row
                reading_id = "reading_" + uuid.uuid4().hex
                self.reading_windows.append({"window_id": reading_id, "article_id": aid,
                    "evidence_ids": [r["evidence_id"] for r in rows], "status": "queued"})
                article_window = self.store.window([a], "analysis", "source_read")
                result.append({"window_id": article_window["window_id"], "reading_window_id": reading_id,
                    "article": compact(a, 300), "passages": [evidence_projection(r) for r in rows], "passages_total": len(all_rows),
                    "already_provided_evidence_ids": [r["evidence_id"] for r in chosen if r not in rows],
                    "next_offset": args.passage_offset+len(chosen), "extraction_truncated": a.fulltext.get("truncated", False),
                    "guidance": "Les preuves exactes sont stockées ; revisit=true permet une relecture explicite."})
            return {"reading": result, "research_context": args.research_context}
        if name == "find_similar_articles":
            a = self.store.article(args.article_id)
            if a.id not in self.store.exposed or not (a.abstract or a.analysis):
                raise ValueError("Lisez d'abord la contribution source avec get_article/analyze_article.")
            if args.mode in {"references", "citations"}:
                if self.search_count >= self.config.max_research_rounds:
                    return {"warning": "Budget de recherches atteint."}
                self.search_count += 1
                rows, warnings = self.sources.citations(a, min(deadline, time.monotonic()+30), args.mode)
                ids, dropped = self.store.insert(rows, args.family)
                proposed, info = self.store.search(args.conceptual_query or "", 8, True, ids=ids)
                return {**self.store.window(proposed, args.mode), "collected": len(ids), "warnings": warnings, "capacity_dropped": dropped, "retrieval": info}
            if not args.conceptual_query:
                raise ValueError("Fournissez une requête conceptuelle fondée sur le problème/contribution, pas le titre seul.")
            return self.search(SearchArgs(query=args.conceptual_query, sources=None, limit_per_source=30, offset=0, family=args.family), deadline)
        if name == "compare_articles":
            ids = list(dict.fromkeys(args.article_ids))
            if args.conclusions is not None:
                claims = [c.model_dump() for c in args.conclusions]
                self.claim_union(claims, ids)
                self.state.setdefault("comparisons", []).append({"article_ids": ids, "context": args.research_context, "conclusions": claims})
            return {"matrix": [compact(self.store.article(aid), 900) for aid in ids], "research_context": args.research_context,
                "guidance": "Compare toi-même problème, objectif, méthode, contribution, résultats, limites, généralisation et niveau de preuve. Aucun résultat absent ne doit être inventé."}
        if name == "identify_research_gaps":
            ids = list(dict.fromkeys(args.article_ids))
            if args.gaps is not None:
                gaps = [g.model_dump() for g in args.gaps]
                for gap in gaps:
                    if not set(gap["article_ids"]) <= set(ids):
                        raise ValueError("Gap associé à un article extérieur au périmètre consulté.")
                    if gap["origin"] != "corpus_absence":
                        if not gap["evidence_ids"]:
                            raise ValueError("Une limite/inférence comparative exige des preuves.")
                        self.claim_union([gap], gap["article_ids"])
                        if gap["origin"] == "comparative_inference" and len({self.evidence[eid]["article_id"] for eid in gap["evidence_ids"]}) < 2:
                            raise ValueError("Une inférence comparative exige au moins deux sources.")
                self.state.setdefault("research_gaps", []).append({"article_ids": ids, "context": args.research_context, "gaps": gaps, "cir_eligibility": "not_established"})
            return {"articles": [compact(self.store.article(aid), 600) for aid in ids],
                "search_scope": self.state.get("recent_searches", [])[-12:], "cir_eligibility": "not_established"}
        if name == "verify_bibliography":
            return self.verify(args.article_ids, deadline)
        if name == "record_candidate_decisions":
            for decision in args.decisions:
                a = self.store.article(decision.article_id)
                self.store.examined_article(a.id)
                a.assessment = {**decision.model_dump(), "basis": a.analysis_basis or ("abstract" if a.abstract else "metadata")}
                self.state.setdefault("candidate_decisions", []).append(a.assessment)
            if args.finalist_ids is not None:
                for aid in args.finalist_ids:
                    a = self.store.article(aid)
                    if aid not in self.store.examined or a.assessment.get("decision") == "reject":
                        raise ValueError("Un finaliste doit avoir été effectivement examiné et non rejeté.")
                self.state["evaluation"] = {"recommended_article_ids": list(dict.fromkeys(args.finalist_ids)),
                    "covered_families": args.covered_families, "remaining_axes": args.remaining_axes,
                    "ranking_origin": "conversation_model_scientific_comparison"}
            self.save()
            return {"saved": True, "evaluation": self.state.get("evaluation", {}), "examined_total": len(self.store.examined)}
        if name == "update_research_state":
            memory = args.model_dump(exclude_none=True)
            if any(len(s) > 2500 for value in memory.values() if isinstance(value, list) for s in value):
                raise ValueError("Élément de mémoire trop long.")
            self.state.setdefault("memory_history", []).append(self.state.get("research_state", {}))
            self.state["research_state"] = {**self.state.get("research_state", {}), **memory, "origin": "conversation_model", "cir_eligibility": "not_established"}
            self.state["summary"] = ("Sujet : " + memory["topic"] + "\nDécisions : " + "; ".join(memory["consultant_decisions"]))[:6000]
            return {"saved": True}
        if name == "finalize_response":
            return self.finalize(args, deadline)
        raise ValueError("Outil inconnu.")
