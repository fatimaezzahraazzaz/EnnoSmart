from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

from .models import Article, public_link
from .sources import merge_articles, title_key
from .fulltext import get_fulltext
from .prompts import ANALYSIS

_reading_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="scholar-v3-reading")


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchArgs(Arguments):
    query: str = Field(min_length=1, max_length=1500)
    sources: list[Literal["semantic_scholar", "openalex", "arxiv"]] | None
    limit: int = Field(ge=1, le=20)
    family: str = Field(min_length=1, max_length=160, description="Famille scientifique visée, résumé factuel")


class MetadataArgs(Arguments):
    article_id_or_url: str = Field(min_length=1, max_length=2000)
    expected_title: str | None = Field(max_length=1000, description="Titre à vérifier lorsqu'un DOI et un titre ont été découverts ensemble")


class FulltextArgs(Arguments):
    article_id: str
    candidate_urls: list[str] | None = Field(max_length=5)


class AnalyzeArgs(Arguments):
    article_id: str
    research_context: str = Field(min_length=1, max_length=5000)


class SimilarArgs(Arguments):
    article_or_topic: str = Field(min_length=1, max_length=1500)
    mode: Literal["similar", "competing", "citations"]
    query: str | None = Field(max_length=1500, description="Requête conceptuelle pour similarité ou approches concurrentes")
    family: str = Field(min_length=1, max_length=160)


class CompareArgs(Arguments):
    article_ids: list[str] = Field(min_length=2, max_length=8)
    research_context: str = Field(min_length=1, max_length=5000)


class StateArgs(Arguments):
    topic: str = Field(max_length=2000)
    hypotheses: list[str] = Field(max_length=20)
    user_defined_locks: list[str] = Field(max_length=20)
    consultant_decisions: list[str] = Field(max_length=20)
    outline: list[str] = Field(max_length=30)


class EvaluationArgs(Arguments):
    recommended_article_ids: list[str] = Field(max_length=20)
    rejected_article_ids: list[str] = Field(max_length=50)
    covered_families: list[str] = Field(max_length=20)
    gaps: list[str] = Field(max_length=20)


TOOL_MODELS = {
    "search_scientific": (SearchArgs, "Rechercher en parallèle dans les bases académiques, en complément du web_search natif."),
    "get_article_metadata": (MetadataArgs, "Résoudre un DOI, arXiv ID, URL publique, ID de la session ou titre exact. Vérifier DOI/titre/auteurs/année."),
    "get_fulltext": (FulltextArgs, "Télécharger et extraire un texte légalement accessible. Un lien PDF seul ne prouve jamais une lecture."),
    "analyze_article": (AnalyzeArgs, "Analyser un article et sa pertinence contextuelle avec preuves issues de ses passages réellement lus."),
    "find_similar_articles": (SimilarArgs, "Rechercher des approches similaires, concurrentes ou des travaux citant une publication identifiée."),
    "compare_articles": (CompareArgs, "Comparer les analyses étayées des articles connus selon le problème scientifique."),
    "update_research_state": (StateArgs, "Conserver le sujet, hypothèses, verrous explicitement énoncés et décisions du consultant ; aucune validation CIR automatique."),
    "evaluate_research": (EvaluationArgs, "Enregistrer le classement des références vérifiées, les rejets, familles couvertes et lacunes. Utiliser les analyses pour justifier."),
}


def definitions():
    return [{"type": "function", "name": name, "description": description, "strict": True,
        "parameters": model.model_json_schema()} for name, (model, description) in TOOL_MODELS.items()]


def passages(article):
    chunks = article.fulltext.get("chunks", []) if article.fulltext.get("text") else (
        [{"section": "Abstract", "page": None, "text": article.abstract}] if article.abstract else [])
    rows, used = [], 0
    for chunk in chunks:
        for offset in range(0, len(chunk["text"]), 700):
            text = chunk["text"][offset:offset + 700]
            if not text.strip() or used + len(text) > 24000:
                continue
            eid = "ev_" + hashlib.sha256((article.id + str(chunk.get("page")) + chunk.get("section", "") + str(offset) + text).encode()).hexdigest()[:16]
            rows.append({"evidence_id": eid, "page": chunk.get("page"), "section": chunk.get("section", ""), "text": text,
                "source_url": article.fulltext.get("url") or article.url})
            used += len(text)
    return rows


def analysis_schema():
    claim = {"type": "object", "additionalProperties": False, "properties": {
        "statement": {"type": "string"}, "evidence_ids": {"type": "array", "items": {"type": "string"}}},
        "required": ["statement", "evidence_ids"]}
    properties = {name: {"type": "array", "items": claim} for name in
        ("problem", "method", "hypotheses", "data", "results", "limitations", "failure_conditions", "robustness", "generalization", "future_work", "relevance")}
    properties.update(relevance_tag={"type": "string", "enum": ["DIRECT", "CONNEXE", "FONDAMENTAL", "HORS_SUJET"]},
        method_family={"type": "string"}, generation_role={"type": "string", "enum": ["direct_generation", "field_prediction", "hybrid", "other", "unknown"]},
        analysis_limit={"type": "string"})
    return {"type": "object", "additionalProperties": False, "properties": properties, "required": list(properties)}


def orchestration_article(article):
    """Keep scientific conclusions in model context; passages remain in durable cards."""
    row = article.compact()
    row["abstract"] = row["abstract"][:1000]
    row["analysis"] = {key: value for key, value in article.analysis.items() if key in
        {"method", "results", "limitations", "relevance", "method_family", "generation_role", "analysis_limit"}}
    return row


class ScholarTools:
    def __init__(self, state, sources, model_request, config, check, activity):
        self.state, self.sources, self.model_request = state, sources, model_request
        self.config, self.check, self.activity = config, check, activity
        self.registry = {aid: Article.model_validate(row) for aid, row in state.get("articles", {}).items()}
        self.seen_queries = set()
        self.search_count = 0
        self.stagnant = 0
        self.families = set()

    def save(self):
        self.state["articles"] = {aid: article.durable() for aid, article in self.registry.items()}

    def article(self, aid):
        if aid not in self.registry:
            raise ValueError("Article inconnu. Utilisez un article_id retourné par les outils.")
        return self.registry[aid]

    def insert(self, rows):
        ids = merge_articles(self.registry, rows, self.config.max_articles)
        self.save()
        return [self.registry[aid].compact() for aid in ids]

    def search(self, query, sources, limit, family, deadline):
        key = (" ".join(query.split()).casefold(), tuple(sorted(sources or ["semantic_scholar", "openalex", "arxiv"])))
        if key in self.seen_queries:
            return {"articles": [], "warning": "Recherche redondante, utilisez une stratégie différente ou synthétisez."}
        if self.search_count >= self.config.max_research_rounds or self.stagnant >= self.config.max_stagnant_searches:
            return {"articles": [], "warning": "Limite de recherche ou absence de nouvelles références utiles. Synthétisez les lacunes."}
        self.seen_queries.add(key)
        self.search_count += 1
        self.families.add(family)
        before = len(self.registry)
        self.activity("search_scientific", "running", query=query, family=family, sources=sources or ["semantic_scholar", "openalex", "arxiv"])
        rows, warnings = self.sources.search(query, sources, limit, min(deadline, time.monotonic() + 35), self.check)
        all_articles = self.insert(rows)
        # Bound discovery material, retain every candidate durably for subsequent requests.
        # Interleave providers so completion timing never privileges a single database.
        queues = {name: [a for a in all_articles if name in a["sources"]] for name in
                  (sources or ["semantic_scholar", "openalex", "arxiv"])}
        articles, seen = [], set()
        for index in range(limit):
            for queue in queues.values():
                if index < len(queue) and queue[index]["id"] not in seen:
                    row = dict(queue[index])
                    row["abstract"] = row["abstract"][:900]
                    # Existing analyses are accessible through metadata, not every search payload.
                    row.pop("analysis", None)
                    seen.add(row["id"])
                    articles.append(row)
                if len(articles) >= limit:
                    break
            if len(articles) >= limit:
                break
        new = len(self.registry) - before
        self.stagnant = self.stagnant + 1 if new == 0 and not warnings else 0
        self.state["recent_searches"] = [*self.state.get("recent_searches", []), {"query": query, "family": family, "new_articles": new}][-30:]
        self.activity("search_scientific", "completed", query=query, family=family, found=len(articles), new_articles=new, warnings=warnings)
        return {"articles": articles, "warnings": warnings, "new_articles": new, "articles_total": len(self.registry),
            "other_candidates": [{"id": a["id"], "title": a["title"], "verified": a["verified"]} for a in all_articles if a["id"] not in seen][:30],
            "next_step_guidance": "Évaluez les références prometteuses avant d'élargir encore ; metadata permet de lire leurs abstracts complets."}

    def analyze(self, article, context):
        if not article.verified:
            return {"error": "Référence non vérifiée. Résolvez les métadonnées auprès de la publication originale avant analyse.", "metadata_errors": article.metadata_errors}
        evidence = passages(article)
        basis = "fulltext" if article.fulltext.get("text") else "abstract" if article.abstract else "metadata"
        if basis == "metadata":
            return {"article_id": article.id, "analysis_basis": basis, "analysis_limit": "Métadonnées uniquement, conclusions scientifiques indisponibles."}
        self.activity("analyze_article", "running", article_id=article.id, basis=basis)
        response = self.model_request(instructions=ANALYSIS, input=[{"role": "user", "content": json.dumps({
            "article": orchestration_article(article), "research_context": context, "analysis_basis": basis,
            "passages": evidence}, ensure_ascii=False)}], tools=[], max_output_tokens=6000,
            reasoning={"effort": "low"},
            text={"format": {"type": "json_schema", "name": "scientific_analysis", "strict": True, "schema": analysis_schema()}})
        if getattr(response, "status", "completed") != "completed":
            return {"article_id": article.id, "error": "Analyse incomplète : limite de sortie. Aucun résultat scientifique validé pour cet appel."}
        raw = json.loads(response.output_text)
        by_id = {row["evidence_id"]: row for row in evidence}
        analysis = {}
        used = set()
        for name in analysis_schema()["properties"]:
            if name in {"relevance_tag", "method_family", "generation_role", "analysis_limit"}:
                analysis[name] = raw[name]
                continue
            claims = []
            for claim in raw[name][:12]:
                ids = claim.get("evidence_ids", [])
                if claim.get("statement") and ids and all(eid in by_id for eid in ids):
                    claims.append({"statement": claim["statement"][:3000], "evidence_ids": list(dict.fromkeys(ids))})
                    used.update(ids)
            analysis[name] = claims
        analysis.update(analysis_basis=basis, evidence=[by_id[eid] for eid in sorted(used)],
            fulltext_truncated=article.fulltext.get("truncated", False))
        # A tag or method family without any supporting relevance/method passage is provisional.
        if not analysis["relevance"]:
            analysis["relevance_tag"] = "CONNEXE"
            analysis["analysis_limit"] += " Pertinence non étayée ; classement provisoire."
        if not analysis["method"]:
            analysis["generation_role"], analysis["method_family"] = "unknown", ""
        article.analysis, article.analysis_basis, article.relevance_tag = analysis, basis, analysis["relevance_tag"]
        self.save()
        self.activity("analyze_article", "completed", article_id=article.id, basis=basis, tag=article.relevance_tag,
            evidence_count=len(used), method_family=analysis["method_family"])
        return {"article_id": article.id, "analysis_basis": basis,
            "analysis": orchestration_article(article)["analysis"]}

    def execute(self, name, raw, deadline):
        self.check()
        if name not in TOOL_MODELS:
            raise ValueError("Outil inconnu.")
        args = TOOL_MODELS[name][0].model_validate(raw)
        if name == "search_scientific":
            return self.search(args.query, args.sources, args.limit, args.family, deadline)
        if name == "get_article_metadata":
            if args.article_id_or_url in self.registry:
                row = self.article(args.article_id_or_url)
                rows = self.sources.metadata(row.doi or row.arxiv_id or row.url, min(deadline, time.monotonic() + 25), self.check) if not row.verified else [row]
            else:
                rows = self.sources.metadata(args.article_id_or_url, min(deadline, time.monotonic() + 25), self.check)
            if args.expected_title:
                for row in rows:
                    if title_key(row.title) != title_key(args.expected_title):
                        return {"error": "DOI/title mismatch", "expected_title": args.expected_title,
                            "verified_title": row.title, "articles": []}
            inserted = self.insert(rows)
            return {"articles": [orchestration_article(self.registry[row["id"]]) for row in inserted],
                "warning": "Aucune référence vérifiée trouvée." if not rows else ""}
        if name == "get_fulltext":
            article = self.article(args.article_id)
            if article.fulltext.get("text"):
                result = article.fulltext
            else:
                # Bound the whole read, including PDF parsing and provider/OS delays.
                # A late worker receives a copy and cannot overwrite the conversation.
                future = _reading_pool.submit(get_fulltext, article.model_copy(deep=True), self.sources,
                    deadline, self.check, args.candidate_urls)
                try:
                    result = future.result(timeout=max(0.001, min(48, deadline - time.monotonic())))
                except FutureTimeout:
                    future.cancel()
                    self.check()
                    result = {"access_level": "abstract_only" if article.abstract else "metadata_only",
                        "text": "", "chunks": [], "truncated": False, "attempts": [],
                        "reason": "Délai global de lecture atteint ; disponibilité du texte intégral inconnue."}
            self.check()
            article.fulltext = result
            article.access_level = result["access_level"]
            if result.get("text"):
                article.fulltext_available, article.fulltext_source = True, result["url"]
            # Failure to download is not proof that a full text does not exist elsewhere.
            self.save()
            return {"article_id": article.id, **{k: v for k, v in result.items() if k not in {"text", "chunks"}},
                "readable_characters": len(result.get("text", "")), "analysis_basis": article.analysis_basis,
                "next_step_guidance": "Le texte est conservé côté serveur. analyze_article lit les passages avec leurs pages et sections."}
        if name == "analyze_article":
            return self.analyze(self.article(args.article_id), args.research_context)
        if name == "find_similar_articles":
            if args.mode == "citations":
                if self.search_count >= self.config.max_research_rounds:
                    return {"articles": [], "warning": "Limite de recherches atteinte."}
                article = self.article(args.article_or_topic)
                self.search_count += 1
                rows, warnings = self.sources.citations(article, min(deadline, time.monotonic() + 30))
                return {"articles": self.insert(rows), "warnings": warnings}
            article = self.registry.get(args.article_or_topic)
            query = args.query or (article.title if article else args.article_or_topic)
            return self.search(query, None, 12, args.family, deadline)
        if name == "compare_articles":
            rows = []
            for aid in args.article_ids:
                self.check()
                article = self.article(aid)
                if not article.analysis:
                    self.analyze(article, args.research_context)
                rows.append({"article_id": aid, "title": article.title, "access_level": article.access_level,
                    "analysis_basis": article.analysis_basis, "analysis": orchestration_article(article)["analysis"]})
            # The orchestrating model compares this evidence; no second ungrounded analysis pipeline.
            return {"research_context": args.research_context, "comparisons": rows,
                "instruction": "Comparez seulement les résultats étayés, signalez les éléments indisponibles."}
        if name == "update_research_state":
            # Record model-extracted context; label consultant claims as awaiting explicit validation.
            state = args.model_dump()
            if any(len(item) > 3000 for value in state.values() if isinstance(value, list) for item in value):
                raise ValueError("Éléments de mémoire trop longs.")
            state["origin"] = "model_extracted_from_conversation"
            self.state["research_state"] = state
            return {"saved": True}
        if name == "evaluate_research":
            ids = [aid for aid in args.recommended_article_ids if self.article(aid).verified and self.article(aid).relevance_tag != "HORS_SUJET"]
            rejected = [aid for aid in args.rejected_article_ids if aid in self.registry]
            self.state["evaluation"] = {**args.model_dump(), "recommended_article_ids": list(dict.fromkeys(ids)), "rejected_article_ids": rejected}
            self.activity("evaluate_research", "completed", recommended=ids, rejected=rejected,
                covered_families=args.covered_families, gaps=args.gaps)
            return {"saved": True, "evaluation": self.state["evaluation"]}
        raise ValueError("Outil inconnu.")
