"""Structured capabilities of the single conversation model. No LLM calls here."""
from __future__ import annotations

import json
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Literal
from urllib.parse import unquote

from pydantic import BaseModel, ConfigDict, Field

from .evidence import passages, choose_passages, validate_claims, snapshot, exact_source_valid
from .fulltext import get_fulltext
from .models import Article, normalize_doi, public_link
from .store import ArticleStore, compact

_reading_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="scholar-v31-reading")


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
    objective: list[Claim] = Field(max_length=8)
    method: list[Claim] = Field(max_length=8)
    contribution: list[Claim] = Field(max_length=8)
    hypotheses: list[Claim] = Field(max_length=8)
    results: list[Claim] = Field(max_length=8)
    limitations: list[Claim] = Field(max_length=8)
    generalization: list[Claim] = Field(max_length=8)
    open_questions: list[Claim] = Field(max_length=8)
    relevance: list[Claim] = Field(max_length=8)
    relevance_tag: Literal["DIRECT", "CONNEXE", "FONDAMENTAL", "HORS_SUJET"]
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
    relevance_tag: Literal["DIRECT", "CONNEXE", "FONDAMENTAL", "HORS_SUJET"]
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


class FinalArgs(Args):
    answer: str = Field(min_length=1, max_length=30000)
    article_ids: list[str] = Field(max_length=20)
    evidence_ids: list[str] = Field(max_length=80)
    coverage_sufficient: bool
    remaining_uncertainties: list[str] = Field(max_length=30)


TOOLS = {
    "search_scientific": (SearchArgs, "Collecter dans les index scientifiques, paginables. Tout est conservé côté backend, une petite fenêtre est proposée par recherche locale."),
    "search_collected_articles": (CollectedArgs, "Chercher dans TOUT le corpus par BM25, identités exactes et embeddings locaux optionnels. Exclure les candidats déjà présentés pour des fenêtres complémentaires."),
    "get_articles": (ArticlesArgs, "Lire/résoudre des articles par ID, DOI, arXiv, URL ou titre exact. Les notices complètes restent accessibles, finalistes compris."),
    "get_fulltext": (FulltextArgs, "Récupérer légalement PDF/HTML paginés, sans confondre disponibilité, extraction et analyse."),
    "analyze_articles": (AnalyzeArgs, "read : présenter des passages exacts ciblés. save : enregistrer l'analyse scientifique que TU as produite avec ces preuves. Aucun appel LLM supplémentaire."),
    "find_similar_articles": (SimilarArgs, "Explorer références/citations ou requêtes conceptuelles après compréhension de la contribution du travail source."),
    "compare_articles": (CompareArgs, "Obtenir une matrice des analyses/preuves ; conclusions non nulles enregistrent TA comparaison scientifique avec ses evidence_ids."),
    "identify_research_gaps": (GapsArgs, "Comparer les limites et questions ouvertes. gaps non null enregistre TES lacunes avec origine et périmètre explicites, sans valider une éligibilité CIR."),
    "verify_bibliography": (VerifyArgs, "Vérifier identités titre/DOI/arXiv/auteurs/année, versions, unicité canonique, accès réel et provenance des preuves avant la synthèse."),
    "record_candidate_decisions": (DecisionsArgs, "Consigner articles effectivement examinés, motifs de rejet/rétention et classement cumulatif révisable. Aucun scoring Python scientifique."),
    "update_research_state": (MemoryArgs, "Mémoriser sujet, objectifs, hypothèses, verrous, axes, décisions et plan de la conversation."),
    "finalize_response": (FinalArgs, "Valider et publier TA réponse après contrôle bibliographique. Utiliser [paper_ID] et [ev_ID] pour des liens exacts. Les incertitudes sont conservées."),
}


def definitions():
    return [{"type": "function", "name": name, "description": description, "strict": True,
        "parameters": model.model_json_schema()} for name, (model, description) in TOOLS.items()]


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
        rows, warnings = self.sources.search(args.query, args.sources, args.limit_per_source,
            min(deadline, time.monotonic() + 35), self.check, offset=args.offset)
        self.check()
        ids, dropped = self.store.insert(rows, args.family)
        searches.append({"key": key, "query": args.query, "family": args.family, "article_ids": ids, "warnings": warnings})
        proposed, info = self.store.search(args.query, 8, True)
        return {**self.store.window(proposed, args.query), "collected": len(ids), "corpus_total": len(self.registry),
            "warnings": warnings, "capacity_dropped": dropped, "retrieval": info,
            "guidance": "Les articles non présentés restent recherchables. Réorientez les concepts, explorez les voisins ou utilisez offset pour les pages fournisseurs suivantes."}

    def verify(self, ids, deadline):
        rows = []
        for aid in ids:
            self.check()
            article = self.store.article(aid)
            if aid not in self.checked_ids:
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
            valid = bool(article.verified and article.title and article.authors and article.year and not article.metadata_errors and valid_evidence)
            if valid:
                self.checked_ids.add(aid)
            rows.append({"article_id": aid, "valid": valid, "title": article.title, "doi": article.doi,
                "arxiv_id": article.arxiv_id, "authors": article.authors, "year": article.year,
                "canonical_work_id": article.canonical_work_id or article.id, "source_version": article.source_version,
                "access_level": article.access_level, "analysis_basis": article.analysis_basis,
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
        ids = list(dict.fromkeys(args.article_ids))
        checked = self.verify(ids, deadline)
        if not checked["valid"]:
            return {"error": "Bibliographie non validée : corrigez ou signalez les références incertaines sans les présenter comme établies.", "bibliography": checked}
        if len({self.store.article(aid).canonical_work_id or aid for aid in ids}) != len(ids):
            return {"error": "Plusieurs versions d'un même travail sont citées : conservez un représentant et indiquez les versions dans sa notice."}
        for eid in args.evidence_ids:
            if eid not in self.evidence or eid not in self.shown_evidence or self.evidence[eid]["article_id"] not in ids:
                raise ValueError("Preuve inconnue, non lue ou extérieure à la bibliographie citée.")
            if not exact_source_valid(self.evidence[eid], self.snapshots):
                raise ValueError("Citation exacte ou localisation incohérente avec la version source conservée.")
        text = args.answer
        from .sources import title_key
        for label, doi in re.findall(r"\[([^\]]+)\]\(https?://(?:dx\.)?doi\.org/([^\s)]+)\)", text, re.I):
            work = next((self.store.article(aid) for aid in ids if self.store.article(aid).doi == normalize_doi(unquote(doi))), None)
            if work and label.casefold() not in {"doi", "publication", "article", "source"} and title_key(label) != title_key(work.title):
                return {"error": "Association titre–DOI incorrecte : utilisez le titre canonique ou [paper_ID]."}
        unknown_markers = [marker for marker in re.findall(r"\[(paper_[\w]+|ev_[\w]+)\]", text)
            if marker not in ids and marker not in args.evidence_ids]
        known_dois = {self.store.article(aid).doi for aid in ids if self.store.article(aid).doi}
        detected = re.findall(r"10\.\d{4,9}/[^\s<>\[\]]+", unquote(text))
        if unknown_markers or any(normalize_doi(doi.rstrip(".,;:)")) not in known_dois and normalize_doi(doi.rstrip(".,;:")) not in known_dois for doi in detected):
            return {"error": "La réponse contient une référence ou une preuve non déclarée/non vérifiée."}
        for aid in ids:
            a = self.store.article(aid)
            url = "https://doi.org/" + a.doi if a.doi else "https://arxiv.org/abs/" + a.arxiv_id if a.arxiv_id else a.url
            text = text.replace("[" + aid + "]", f"[{a.title.replace('[','').replace(']','')}]({url})")
        for eid in args.evidence_ids:
            e = self.evidence[eid]
            locator = e["section"] or "Passage source"
            if e["page_number"] is not None:
                locator += f", page PDF {e['page_number']}"
            url = e["source_url"]
            text = text.replace("[" + eid + "]", f"[{locator}]({url})" if public_link(url) else locator)
        # Bibliography comes from verified source records, never model-authored metadata.
        if ids:
            text += "\n\nRéférences et niveau de lecture :\n\n" + "\n".join(
                f"- {self.store.article(aid).title} — {self.store.article(aid).year} — " +
                (f"[DOI](https://doi.org/{self.store.article(aid).doi})" if self.store.article(aid).doi else
                 f"[Publication]({self.store.article(aid).url})") +
                f" — {self.store.article(aid).access_level}, analyse : {self.store.article(aid).analysis_basis or 'non effectuée'}."
                for aid in ids)
        if args.remaining_uncertainties:
            text += "\n\nIncertitudes restantes : " + " ; ".join(args.remaining_uncertainties)
        self.finished = {"answer": text, "citations": [], "cited_article_ids": ids,
            "cited_evidence_ids": list(dict.fromkeys(args.evidence_ids)), "coverage_sufficient": args.coverage_sufficient,
            "remaining_uncertainties": args.remaining_uncertainties, "bibliography": checked}
        self.state["last_response"] = {k: v for k, v in self.finished.items() if k != "answer"}
        self.state.setdefault("response_history", []).append(self.state["last_response"])
        return {"published": True, "cited_article_ids": ids}

    def execute(self, name, raw, deadline):
        self.check()
        if name not in TOOLS:
            raise ValueError("Outil inconnu.")
        args = TOOLS[name][0].model_validate(raw)
        if name == "search_scientific":
            return self.search(args, deadline)
        if name == "search_collected_articles":
            rows, info = self.store.search(args.query, args.limit, args.exclude_presented, args.family)
            return {**self.store.window(rows, args.query), "retrieval": info, "corpus_total": len(self.registry)}
        if name == "get_articles":
            ids = []
            for identifier in args.identifiers:
                self.check()
                if identifier in self.registry:
                    ids.append(identifier)
                else:
                    rows = self.sources.metadata(identifier, min(deadline, time.monotonic()+25), self.check)
                    inserted, _ = self.store.insert(rows)
                    ids.extend(inserted)
            result = self.store.window([self.store.article(aid) for aid in dict.fromkeys(ids)], "identities", "metadata_read")
            result["articles"] = [compact(self.store.article(aid), 3500) for aid in dict.fromkeys(ids)]
            return result
        if name == "get_fulltext":
            article = self.store.article(args.article_id)
            if not article.fulltext.get("text"):
                future = _reading_pool.submit(get_fulltext, article.model_copy(deep=True), self.sources, deadline, self.check, args.candidate_urls)
                try:
                    result = future.result(timeout=max(.001, min(48, deadline-time.monotonic())))
                except FutureTimeout:
                    future.cancel(); self.check()
                    return {"article_id": article.id, "available": None, "warning": "Lecture interrompue ; accès intégral inconnu."}
                self.check()
                article.fulltext = result
                article.access_level = result["access_level"]
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
                "analysis_basis": article.analysis_basis, "guidance": "analyze_articles(action=read) présente les passages ; tu produis ensuite l'analyse avec action=save."}
        if name == "analyze_articles":
            if args.action == "save":
                if len(args.article_ids) != 1 or args.analysis is None:
                    raise ValueError("save exige exactement un article et son analyse.")
                a = self.store.article(args.article_ids[0])
                for claim in args.analysis.model_dump().values():
                    if isinstance(claim, list):
                        for item in claim:
                            if any(eid not in self.evidence or not exact_source_valid(self.evidence[eid], self.snapshots) for eid in item["evidence_ids"]):
                                raise ValueError("Citation exacte incohérente avec le document source.")
                validated = validate_claims(args.analysis.model_dump(), a, self.evidence, self.shown_evidence)
                a.analysis = validated
                a.analysis_basis, a.relevance_tag = validated["analysis_basis"], validated["relevance_tag"]
                self.store.examined_article(a.id)
                self.save()
                return {"saved": True, "article": compact(a)}
            result = []
            for aid in args.article_ids:
                a = self.store.article(aid)
                all_rows = passages(a)
                source = snapshot(a)
                self.snapshots[source["fingerprint"]] = source
                rows = choose_passages(all_rows, args.passage_query, args.passage_offset, args.passages_per_article)
                for row in rows:
                    self.evidence[row["evidence_id"]] = row
                reading_id = "reading_" + uuid.uuid4().hex
                self.reading_windows.append({"window_id": reading_id, "article_id": aid,
                    "evidence_ids": [r["evidence_id"] for r in rows], "status": "queued"})
                article_window = self.store.window([a], "analysis", "source_read")
                result.append({"window_id": article_window["window_id"], "reading_window_id": reading_id,
                    "article": compact(a, 300), "passages": rows, "passages_total": len(all_rows),
                    "next_offset": args.passage_offset+len(rows), "extraction_truncated": a.fulltext.get("truncated", False)})
            return {"reading": result, "research_context": args.research_context}
        if name == "find_similar_articles":
            a = self.store.article(args.article_id)
            if a.id not in self.store.exposed or not (a.abstract or a.analysis):
                raise ValueError("Lisez d'abord la contribution source avec get_articles/analyze_articles.")
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
                a.relevance_tag = decision.relevance_tag
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
            memory = args.model_dump()
            if any(len(s) > 2500 for value in memory.values() if isinstance(value, list) for s in value):
                raise ValueError("Élément de mémoire trop long.")
            self.state.setdefault("memory_history", []).append(self.state.get("research_state", {}))
            self.state["research_state"] = {**memory, "origin": "conversation_model", "cir_eligibility": "not_established"}
            return {"saved": True}
        if name == "finalize_response":
            return self.finalize(args, deadline)
        raise ValueError("Outil inconnu.")
