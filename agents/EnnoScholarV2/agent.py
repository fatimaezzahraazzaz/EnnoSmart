from __future__ import annotations

import json
import logging
import re
import time
import uuid
import threading
from contextvars import copy_context
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import urlparse

from openai import APITimeoutError
from pydantic import ValidationError

from .citations import (BIBLIOGRAPHY_INSTRUCTION, BibliographyError, bibliography_format,
    parse_output, render_bibliography)
from .config import ScholarConfig
from .conversation import CONVERSATION_INSTRUCTION
from .article_reference import (DISCOVERY_TOOLS, REFERENCE_INSTRUCTION, clarification_message,
    explicitly_no_new_search, resolve_article_reference, resolve_message_reference, resolve_pending_reference)
from .context import (ActiveSources, compact_analysis, compact_article, context_metrics,
    estimate_tokens, fulltext_status, prune_native_compaction, serialized_bytes)
from .models import Article, ArticleAnalysis, Citation, FulltextResult, SearchIntent, ToolActivity, ScientificMemory
from .canonical import ArticleStore, article_id, identity_keys, merge_metadata, same_record
from .evidence_provenance import analysis_material
from .tools.collected_search import search_collected_articles
from .prompts import SYSTEM_PROMPT, COLLECTED_CORPUS_INSTRUCTION
from .performance import analysis_signature, cached_work_fulltext
from .tools.article_analysis import AnalysisIncompleteError, analyze_article
from .tools.article_reader import fetch_url, html_document, pdf_chunks
from .tools.fulltext import get_fulltext, unavailable_fulltext
from .tools.scientific_search import ScientificSearch, deduplicate, normalize_doi
from .tools.similarity import find_similar_articles
from .tools.compact_results import compact_results
from .tools.scientific_selection import work_groups


logger = logging.getLogger("uvicorn.error")


SOFT_INSTRUCTION = """Le budget normal d'outils approche de sa limite.
Utilise maintenant les résultats déjà collectés. N'appelle un nouvel outil que si une
information indispensable manque. Ne répète pas les recherches ni la récupération de
notices déjà obtenues. Termine les vérifications fulltext et analyses nécessaires en batch,
puis produis immédiatement la meilleure réponse possible."""

REQUEST_KINDS = ["bibliographic_search", "article_analysis", "followup", "other"]
REQUEST_INSTRUCTION = """Déclare request_kind dans chaque fonction selon la demande ORIGINALE
de ce tour, pas selon l'outil : bibliographic_search pour trouver/comparer les meilleures références,
article_analysis pour lire/analyser un article ciblé, followup pour une question sur les résultats
précédents, other sinon. Une recherche nécessaire à une analyse ou un suivi ne change pas ce type.
Sans appel de fonction, hors synthèse JSON structurée, préfixe ta réponse par
<!--scholar_request:TYPE--> ; ce marqueur sera retiré. Le JSON final n'a aucun préfixe.
Pour bibliographic_search, une sélection explicite et justifiée est obligatoire avant la synthèse.
Compare ensemble les candidats réellement visibles/examinés à la demande originale : objet,
opération, dimension, méthode et application; distingue production directe/hybride, adaptation,
réparation/lissage, reconstruction pour une autre application et utilisation en simulation.
Les notices doivent être comparées avant citations, année, accès ou existence d'une analyse.
Un candidat DIRECT peut dépasser un CONNEXE déjà analysé. Aucun benchmark ni domaine prédéfini.
Enregistre tes finalistes avec set_article_selection et une raison scientifique courte par ID.
Une inspection ou un pin automatique ne constitue pas cette sélection. N'invente pas de notice."""


@dataclass
class TurnResult:
    answer: str
    citations: list[Citation]
    activity: list[ToolActivity]
    partial: bool = False
    reason: str | None = None
    tool_counts: dict[str, int] = field(default_factory=dict)
    rounds: int = 0
    active_article_ids: list[str] = field(default_factory=list)
    scientific_memory: ScientificMemory | None = None

    def __iter__(self):
        # Preserve the existing three-value service/testing contract.
        return iter((self.answer, self.citations, self.activity))


@dataclass
class StoredArticle:
    article: Article
    fulltext: FulltextResult | None = None
    analysis: ArticleAnalysis | None = None
    analysis_signature: str = ""
    fulltext_article_id: str = ""


def function_tool(name: str, description: str, properties: dict) -> dict:
    return {"type": "function", "name": name, "description": description, "strict": True,
        "parameters": {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}}


STRING = {"type": "string"}
OPTIONAL_STRING = {"type": ["string", "null"]}
LIMIT = {"type": "integer", "minimum": 1, "maximum": 20}
ARTICLE_IDS = {"type": "array", "items": STRING, "minItems": 1, "maxItems": 8}
INTENT = {"type": "object", "additionalProperties": False,
    "properties": {name: {"type": "array", "items": STRING, "maxItems": 12,
        **({"minItems": 1} if name in {"target_terms", "primary_operations"} else {})}
        for name in SearchIntent.model_fields}, "required": list(SearchIntent.model_fields)}
TOOLS = [
    {"type": "web_search"},
    function_tool("resolve_article_reference", "Retrouver LOCALement une notice dans le registre COMPLET de cette session depuis un titre, alias, DOI, URL ou identifiant arXiv. Aucun réseau. À utiliser avant de dire qu'un article est absent ou de demander des détails à l'utilisateur. Plusieurs correspondances nécessitent une clarification.",
        {"reference": STRING}),
    function_tool("search_scientific", "Chercher plusieurs axes en batch dans Semantic Scholar, OpenAlex et arXiv. intent décrit l'objet, l'objectif principal, les objectifs secondaires, les opérations seulement connexes, les méthodes et le contexte DE CET UTILISATEUR, avec variantes lexicales conceptuelles. La profondeur fournisseurs est indépendante du top_k compact (1 à 12). Conserver les articles payants.",
        {"queries": {"type": "array", "items": STRING, "minItems": 1, "maxItems": 8},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 12}, "intent": INTENT}),
    function_tool("search_collected_articles", "Chercher LOCALement dans tout le corpus déjà collecté, sans Internet. Query scientifique ciblée, BM25 de navigation uniquement, un travail canonique par candidat. Utile après une recherche large et avant de conclure qu'une famille manque. exclude_article_ids exclut aussi les versions associées; cursor parcourt les autres correspondances de la même query.",
        {"query": STRING, "limit": {"type": "integer", "minimum": 1, "maximum": 12},
         "exclude_article_ids": {"type": ["array", "null"], "items": STRING, "maxItems": 10000}, "cursor": OPTIONAL_STRING}),
    function_tool("set_article_selection", "Mémoriser TES finalistes scientifiques et les notices à garder épinglées. Remplace ces deux listes, chacune de 0 à 12 IDs connus. Le jugement appartient au chat, aucun score Python. Les autres travaux et analyses restent backend.",
        {"finalist_article_ids": {"type": "array", "items": STRING, "maxItems": 12},
         "pinned_article_ids": {"type": "array", "items": STRING, "maxItems": 12},
         "justifications": {"type": ["array", "null"], "maxItems": 12, "items": {
             "type": "object", "additionalProperties": False, "properties": {
                 "article_id": STRING, "reason": {"type": "string", "minLength": 1, "maxLength": 500}},
             "required": ["article_id", "reason"]}}}),
    function_tool("read_article", "Résoudre un article depuis une URL publique, un DOI ou un identifiant arXiv. Ne garantit pas le full text.",
        {"url_or_doi": STRING}),
    function_tool("get_articles", "Retrouver en UN appel les notices de quelques candidats utiles (1 à 12), avec abstracts compacts et analyses vérifiées; jamais le texte intégral. Ne pas récupérer tous les candidats ni répéter des notices déjà disponibles.",
        {"article_ids": {"type": "array", "items": STRING, "minItems": 1, "maxItems": 12}}),
    function_tool("get_fulltext", "Opération coûteuse : stabiliser les quelques finalistes avec les notices avant de vérifier légalement leur accès en batch. Jusqu'à huit si l'utilisateur demande leur analyse approfondie. Textes stockés backend; retour disponibilité uniquement. candidate_urls peut être vide.",
        {"article_ids": ARTICLE_IDS, "candidate_urls": {"type": "array", "items": STRING, "maxItems": 5}}),
    function_tool("analyze_articles", "Analyser en batch exclusivement les textes réellement stockés des articles demandés. Retour compact et preuves exactes; aucune récupération automatique.",
        {"article_ids": ARTICLE_IDS, "user_context": OPTIONAL_STRING}),
    function_tool("find_similar_articles", "Rechercher des candidats similaires à un article connu ou un sujet. Choisis des requêtes scientifiques ciblées; queries peut être vide.",
        {"article_or_topic": STRING, "article_id": OPTIONAL_STRING,
            "queries": {"type": "array", "items": STRING, "maxItems": 4}, "limit": LIMIT}),
]

for _tool in TOOLS:
    if _tool["type"] == "function":
        _tool["parameters"]["properties"]["request_kind"] = {"type": "string", "enum": REQUEST_KINDS}
        _tool["parameters"]["required"].append("request_kind")


class ScholarChatAgent:
    def __init__(self, config: ScholarConfig | None = None, *, client=None, search: ScientificSearch | None = None):
        self.config = config or ScholarConfig.from_env()
        self._client = client
        self.search = search or ScientificSearch(self.config)

    @property
    def client(self):
        if self._client is None:
            self.config.require_openai()
            from openai import OpenAI
            self._client = OpenAI(api_key=self.config.api_key, base_url=self.config.base_url,
                timeout=self.config.timeout, max_retries=0)
        return self._client

    def register(self, rows: list[dict], registry: dict[str, StoredArticle]) -> list[dict]:
        output, index = [], {}
        candidates = [Article.model_validate(row) for row in rows]
        # Provider IDs live in external_ids. Session version IDs remain generated,
        # while an already stored version keeps its ID and attached payloads.
        aliases = {article.id: article_id(article) for article in candidates if article.id and article.id not in registry}
        for article in candidates:
            article.id = aliases.get(article.id, article.id) or article_id(article)
            article.related_article_ids = [aliases.get(key, key) for key in article.related_article_ids]
            article.canonical_article_id = aliases.get(article.canonical_article_id, article.canonical_article_id)
        positions = {key: position for position, key in enumerate(registry)}
        for key, record in registry.items():
            for identity in identity_keys(record.article):
                index.setdefault(identity, []).append(key)
        for candidate in deduplicate(candidates, self.config.max_articles):
            matches = list(dict.fromkeys([*([candidate.id] if candidate.id in registry else []),
                *(key for identity in identity_keys(candidate) for key in index.get(identity, [])
                if same_record(registry[key].article, candidate))]))
            if matches:
                identifier = min(matches, key=positions.get)
                record = registry[identifier]
                merged = merge_metadata(record.article.model_copy(deep=True), candidate)
                merged.id = identifier
                merged.access_level = record.article.access_level if record.fulltext else merged.access_level
                merged.analysis_basis = record.article.analysis_basis
                merged.related_article_ids = list(dict.fromkeys([*merged.related_article_ids, *candidate.related_article_ids, *matches]))
                record.article = candidate = merged
            elif len(registry) < self.config.max_articles:
                identifier = candidate.id
                registry[identifier] = StoredArticle(candidate)
                positions[identifier] = len(registry) - 1
            else:
                continue
            if candidate.relevance_origin != "llm":
                candidate.relevance_score = candidate.relevance_tag = candidate.relevance_label = None
                candidate.ranking_reason = "Qualification scientifique par le chat, non par le retrieval."
                candidate.relevance_origin = "retrieval"
            for identity in identity_keys(candidate):
                if identifier not in index.setdefault(identity, []):
                    index[identity].append(identifier)
            output.append(identifier)
        ArticleStore(registry).annotate()
        return [registry[identifier].article.model_dump(exclude={"provider_notices"}) for identifier in dict.fromkeys(output)]

    def read_article(self, url_or_doi: str, registry: dict[str, StoredArticle]) -> dict:
        value = url_or_doi.strip()
        if re.match(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)?10\.\d{4,9}/\S+$", value, re.I):
            doi = normalize_doi(value)
            found = self.search.lookup_doi(doi)
            if found:
                return {"articles": self.register([a.model_dump() for a in found], registry)}
            return {"articles": [], "reason": "DOI non résolu par les API disponibles."}
        match = re.search(r"arxiv\.org/(?:abs|pdf|html)/([^?#]+)", value, re.I)
        if match:
            identifier = match.group(1).removesuffix(".pdf")
            found = self.search.arxiv("", 1, identifier=identifier)
            if found:
                return {"articles": self.register([a.model_dump() for a in found], registry)}
        data, content_type, url = fetch_url(value)
        if data.startswith(b"%PDF"):
            chunks, truncated = pdf_chunks(data)
            if not chunks or sum(len(c.text) for c in chunks) < 500:
                return {"articles": [], "reason": "PDF sans texte scientifique extractible."}
            title = next((line.strip() for line in chunks[0].text.splitlines() if len(line.strip()) > 12), url)[:1000]
            registered = self.register([Article(title=title, url=url, pdf_url=url, source="article_url", sources=["article_url"]).model_dump()], registry)
            record = registry[registered[0]["id"]]
            record.fulltext = FulltextResult(access_level="fulltext", text="\n\n".join(c.text for c in chunks),
                chunks=chunks, truncated=truncated, fulltext_source=urlparse(url).hostname or "", fulltext_url=url,
                reason="PDF fourni par l'utilisateur, texte extrait; passages sélectionnés.")
            record.article.access_level = "fulltext"
        elif "html" in content_type:
            article, chunks, truncated = html_document(data, url)
            if article.doi.startswith("10."):
                found = self.search.lookup_doi(article.doi)
                if found:
                    article = deduplicate([*found, article], 1)[0]
            registered = self.register([article.model_dump()], registry)
            record = registry[registered[0]["id"]]
            if chunks:
                record.fulltext = FulltextResult(access_level="fulltext", text="\n\n".join(c.text for c in chunks),
                    chunks=chunks, truncated=truncated, fulltext_source=urlparse(url).hostname or "", fulltext_url=url,
                    reason="Article HTML accessible; passages sélectionnés.")
                record.article.access_level = "fulltext"
        else:
            return {"articles": [], "reason": "Format non pris en charge."}
        return {"articles": [record.article.model_dump()], "reading_status": "Texte récupéré; aucune analyse effectuée." if record.fulltext else "Métadonnées/abstract uniquement."}

    def execute_tool(self, name: str, arguments: dict, registry: dict[str, StoredArticle], client, *, deadline: float | None = None) -> dict:
        if name == "resolve_article_reference":
            return resolve_article_reference(arguments.get("reference"), registry)
        if name == "search_collected_articles":
            return search_collected_articles(registry, arguments.get("query"), arguments.get("limit", 10),
                arguments.get("exclude_article_ids"), arguments.get("cursor"))
        if name == "search_scientific":
            if "queries" in arguments:
                options = {"deadline": deadline} if deadline is not None else {}
                if arguments.get("intent") is not None:
                    options["intent"] = arguments["intent"]
                result = self.search.search_batch(arguments["queries"], arguments.get("top_k", 8), **options)
                rows = self.register(result.pop("all_articles"), registry)
                candidates = result.get("articles")
                if not candidates and rows:
                    candidates = compact_results([Article.model_validate(row) for row in rows], arguments.get("top_k", 8))
                resolved = []
                for candidate in candidates[:min(arguments.get("top_k", 8), 12)]:
                    identifier = candidate.get("article_id") or candidate.get("id")
                    if identifier not in registry:
                        notice = Article.model_validate(candidate)
                        identifier = next((key for key, record in registry.items() if same_record(notice, record.article)), None)
                    if identifier:
                        # Stable session IDs and verified access remain authoritative.
                        resolved.append({**candidate, "article_id": identifier, "access_level": registry[identifier].article.access_level})
                result["articles"] = resolved
                result["total_stored"] = len(rows)
                result["registry_total"] = len(registry)
                if len(rows) < result.get("total_found", len(rows)):
                    result.setdefault("warnings", []).append({"source": "registry", "reason": "Capacité du registre atteinte ; collecte partiellement conservée."})
                return result
            # Compatibility for older in-flight calls. New tool schema exposes batching.
            result = self.search.search_scientific(arguments["query"], arguments.get("limit", 10))
            result["articles"] = compact_results([Article.model_validate(row) for row in self.register(result["articles"], registry)], arguments.get("limit", 8))
            return result
        if name == "read_article":
            result = self.read_article(arguments["url_or_doi"], registry)
            result["articles"] = [compact_article(Article.model_validate(row)) for row in result.get("articles", [])]
            return result
        if name == "get_articles":
            identifiers = arguments.get("article_ids")
            if not isinstance(identifiers, list) or not 1 <= len(identifiers) <= 12 or not all(isinstance(identifier, str) for identifier in identifiers):
                raise ValueError("Le batch de notices accepte de un à douze identifiants.")
            results = []
            for identifier in dict.fromkeys(identifiers):
                if identifier not in registry:
                    results.append({"article_id": identifier, "error": "Article inconnu dans cette conversation."})
                else:
                    # Internal reader retained; only the batch is exposed to the model.
                    results.append(self.execute_tool("get_article", {"article_id": identifier}, registry, client))
            return {"articles": results}
        if name == "find_similar_articles":
            identifier = arguments.get("article_id")
            if identifier and identifier not in registry:
                raise ValueError("Article inconnu dans cette conversation.")
            result = find_similar_articles(arguments["article_or_topic"], self.search,
                article=registry[identifier].article if identifier else None,
                queries=arguments.get("queries"), limit=arguments.get("limit", 10))
            versions = result.pop("all_articles", None)
            result["articles"] = compact_results([Article.model_validate(row) for row in self.register(
                versions if versions is not None else result["articles"], registry)], min(arguments.get("limit", 8), 12))
            return result
        if name in {"get_fulltext", "analyze_articles", "analyze_article"}:
            identifiers = arguments.get("article_ids", [arguments.get("article_id")])
            if not isinstance(identifiers, list) or not 1 <= len(identifiers) <= 8:
                raise ValueError("Le batch accepte de un à huit articles.")
            identifiers = list(dict.fromkeys(identifiers))
            batch_started = time.monotonic()
            if name == "get_fulltext":
                batch_deadline = batch_started + self.config.fulltext_batch_timeout
                deadline = min(deadline, batch_deadline) if deadline is not None else batch_deadline
            work_locks = {registry[identifier].article.canonical_article_id or identifier: threading.Lock()
                for identifier in identifiers if identifier in registry}

            def process(identifier):
                if identifier not in registry:
                    return {"article_id": identifier, "error": "Article inconnu dans cette conversation."}
                if name != "get_fulltext" and deadline is not None and time.monotonic() >= deadline:
                    return {"article_id": identifier, "error": "Durée disponible épuisée; notice conservée."}
                record = registry[identifier]
                try:
                    if name == "get_fulltext":
                        article_started = time.monotonic()
                        group = record.article.canonical_article_id or identifier
                        with work_locks[group]:
                            result, source_id = cached_work_fulltext(identifier, registry)
                            cache_hit = result is not None
                            if cache_hit:
                                logger.info("EnnoScholar V2 fulltext article_id=%s total_elapsed=%.3f attempts=0 cache_hit=true source_article_id=%s",
                                    identifier, time.monotonic() - article_started, source_id)
                            elif deadline is not None and time.monotonic() >= deadline:
                                result = unavailable_fulltext(record.article, incomplete=True)
                                logger.info("EnnoScholar V2 fulltext article_id=%s total_elapsed=%.3f attempts=0 cache_hit=false status=deadline",
                                    identifier, time.monotonic() - article_started)
                            else:
                                article_deadline = time.monotonic() + self.config.fulltext_article_timeout
                                if deadline is not None:
                                    article_deadline = min(deadline, article_deadline)
                                result = get_fulltext(record.article, self.search, arguments.get("candidate_urls"), deadline=article_deadline)
                            if result.access_level == "fulltext" and result.text:
                                record.fulltext = result
                                record.fulltext_article_id = source_id or identifier
                            record.article.access_level = result.access_level
                            status = fulltext_status(identifier, result)
                            if record.fulltext_article_id:
                                status["source_article_id"] = record.fulltext_article_id
                            return status
                    analysis_started = time.monotonic()
                    signature = analysis_signature(record, self.config, arguments.get("user_context"))
                    if record.analysis is not None and record.analysis_signature == signature:
                        logger.info("EnnoScholar V2 analysis article_id=%s elapsed=%.3f cache_hit=true", identifier, time.monotonic() - analysis_started)
                        return {"article_id": identifier, **compact_analysis(record.analysis)}
                    remaining = deadline - time.monotonic() if deadline is not None else self.config.timeout
                    analysis_client = client.with_options(timeout=max(0.1, min(self.config.timeout, remaining)))
                    record.analysis = analyze_article(record.article, analysis_material(identifier, registry), analysis_client, self.config, arguments.get("user_context"))
                    record.analysis_signature = signature
                    record.article.analysis_basis = record.analysis.analysis_basis
                    logger.info("EnnoScholar V2 analysis article_id=%s elapsed=%.3f cache_hit=false", identifier, time.monotonic() - analysis_started)
                    return {"article_id": identifier, **compact_analysis(record.analysis)}
                except Exception as exc:
                    basis = "fulltext" if record.fulltext and record.fulltext.access_level == "fulltext" and record.fulltext.text else "abstract" if record.article.abstract else "metadata"
                    reason = "analysis_incomplete" if isinstance(exc, AnalysisIncompleteError) else "analysis_validation_error" if name != "get_fulltext" and isinstance(exc, ValidationError) else "article_tool_error"
                    logger.info("EnnoScholar V2 batch article failure tool=%s article_id=%s title=%s basis=%s error_type=%s reason=%s",
                        name, identifier, json.dumps(record.article.title, ensure_ascii=False), basis, type(exc).__name__, reason)
                    return {"article_id": identifier, "access_level": record.article.access_level,
                        "error": "analysis_incomplete: analyse non terminée; source conservée." if isinstance(exc, AnalysisIncompleteError) else "Source ou analyse indisponible; aucune conclusion inventée."}

            concurrency = self.config.fulltext_concurrency if name == "get_fulltext" else self.config.analysis_concurrency
            with ThreadPoolExecutor(max_workers=min(max(1, concurrency), 3, len(identifiers)), thread_name_prefix="scholar-v2-articles") as pool:
                futures = [pool.submit(copy_context().run, process, identifier) for identifier in identifiers]
                results = [future.result() for future in futures]
            if "article_ids" not in arguments:
                return results[0]
            return {"articles": results}
        identifier = arguments.get("article_id")
        if identifier not in registry:
            raise ValueError("Article inconnu dans cette conversation.")
        record = registry[identifier]
        if name == "get_article":
            result = compact_article(record.article, abstract_limit=4000)
            if record.fulltext:
                result["fulltext"] = fulltext_status(identifier, record.fulltext)
            if record.analysis:
                result["analysis"] = compact_analysis(record.analysis)
            return result
        raise ValueError("Outil non disponible.")

    def run(self, message: str, history: list[dict], registry: dict[str, StoredArticle], *, memory: ScientificMemory | None = None) -> TurnResult:
        client = self.client
        memory = memory or ScientificMemory()
        ArticleStore(registry).annotate()
        active = ActiveSources(registry, self.config.active_article_limit, memory)
        bibliographic = False
        selection_ready = False
        comparison_phase = False
        comparison_requested_ids = []
        comparison_presented = False
        bibliography_repair = False
        last_visible_rows = []
        last_index_ids = []

        def declare_request(kind):
            nonlocal bibliographic, selection_ready
            if kind not in REQUEST_KINDS:
                raise ValueError("Type de demande invalide.")
            if kind == "bibliographic_search" and not bibliographic:
                bibliographic, selection_ready = True, False

        def declare_text(answer):
            marker = re.match(r"\s*<!--scholar_request:(\w+)-->\s*", answer)
            if marker:
                declare_request(marker.group(1))
                return answer[marker.end():]
            return answer

        def require_grounding(answer):
            if bibliographic and selection_ready:
                return render_bibliography(answer, registry, active.finalists)
            return answer, [], []
        no_new_search = explicitly_no_new_search(message)
        local_reference = resolve_pending_reference(message, registry, memory.pending_clarification) or resolve_message_reference(message, registry)
        if local_reference and local_reference["status"] == "resolved":
            # Reference resolution is inspection, separate from scientific finalists.
            active.inspect([{"article_id": local_reference["article_id"]}])
            memory.pending_clarification = None
        reference_policy = REFERENCE_INSTRUCTION + "\nRestriction du tour : no_new_search=" + str(no_new_search).lower()
        if local_reference:
            reference_policy += "\nRésolution locale préalable : " + json.dumps(
                {key: value for key, value in local_reference.items() if key != "article"}, ensure_ascii=False)
            logger.info("EnnoScholar V2 local_reference status=%s article_id=%s no_new_search=%s",
                local_reference["status"], local_reference.get("article_id", ""), str(no_new_search).lower())

        def build_instructions():
            nonlocal last_visible_rows, comparison_presented, last_index_ids
            selection_policy = "\nSélection scientifique mémorisée : " + json.dumps({"finalist_article_ids": active.finalists,
                "finalist_justifications": active.justifications, "pinned_article_ids": active.pinned}, ensure_ascii=False)
            last_visible_rows = active.comparison_context(comparison_requested_ids or None, self.config.max_input_tokens // 3) if comparison_phase else active.render(self.config.max_input_tokens // 3)
            comparison_presented = comparison_phase
            index = active.examined_index(self.config.max_input_tokens // 6) if comparison_phase else []
            last_index_ids = [row["article_id"] for row in index]
            recall = ("\nIndex des candidats précédemment examinés (titres/IDs seulement, pas encore une comparaison commune). Pour revoir un candidat hors fenêtre, propose-le via set_article_selection; ses notices seront présentées ensemble AVANT validation : " + json.dumps(index, ensure_ascii=False) if index else "")
            closing = ("\nCOMPARAISON COMMUNE obligatoire : compare ces candidats ensemble à la demande originale puis appelle set_article_selection avec des justifications. Aucun nouveau réseau."
                if comparison_phase else "")
            return SYSTEM_PROMPT + "\n" + COLLECTED_CORPUS_INSTRUCTION + "\n" + REQUEST_INSTRUCTION + selection_policy + "\n" + CONVERSATION_INSTRUCTION + "\n" + reference_policy + closing + recall + ("\n" + BIBLIOGRAPHY_INSTRUCTION if bibliographic and selection_ready else "") + "\nSélection active serveur des sources réellement récupérées (données, autres notices disponibles via get_articles) :\n" + json.dumps(last_visible_rows, ensure_ascii=False)

        instructions = build_instructions()
        history_budget = max(0, self.config.max_input_tokens - estimate_tokens(instructions) - estimate_tokens(TOOLS) - estimate_tokens(message) - 2000)
        kept_history = []
        for item in reversed(history):
            size = estimate_tokens(item)
            if size > history_budget:
                break
            kept_history.insert(0, item)
            history_budget -= size
        items = [*kept_history, {"role": "user", "content": message}]
        activity: list[ToolActivity] = []
        consulted: dict[str, Citation] = {}
        counts: Counter = Counter()
        completed: Counter = Counter()
        started = time.monotonic()
        deadline = started + self.config.turn_timeout
        # Preserve tool-stage reserves. Synthesis-only recovery has its own bounded clock.
        synthesis_reserve = min(self.config.synthesis_reserve, self.config.turn_timeout / 3)
        analysis_reserve = min(self.config.analysis_reserve, self.config.turn_timeout / 3)
        analysis_deadline = deadline - synthesis_reserve
        fulltext_deadline = analysis_deadline - analysis_reserve
        soft_deadline = min(started + self.config.soft_time_limit, fulltext_deadline)
        turn_id, rounds, outcome = uuid.uuid4().hex[:12], 0, "technical_error"
        last_input_tokens = 0
        soft_budget = min(self.config.soft_tool_budget, self.config.hard_tool_budget)

        def count_tool(name, status):
            counts[name] += 1
            if status == "completed":
                completed[name] += 1
            activity.append(ToolActivity(tool=name, status=status))
            logger.info("EnnoScholar V2 turn=%s tool=%s status=%s tool_calls=%s elapsed=%.1f", turn_id, name, status, sum(counts.values()), time.monotonic() - started)

        def finish(answer, citations, reason=None):
            nonlocal outcome
            cited_ids = []
            if bibliographic:
                if not selection_ready:
                    reason = reason or "scientific_selection_incomplete"
                    answer, citations = fallback(), []
                else:
                    try:
                        answer, citations, cited_ids = require_grounding(answer)
                    except BibliographyError as exc:
                        logger.info("EnnoScholar V2 turn=%s bibliography_rejected error=%s", turn_id, str(exc))
                        reason = reason or "bibliography_validation_failed"
                        answer, citations = fallback(), []
            if bibliographic:
                logger.info("EnnoScholar V2 turn=%s closure reason=%s inspected=%s finalists=%s cited=%s selection_ready=%s",
                    turn_id, reason or "completed", json.dumps(active.inspected), json.dumps(active.finalists), json.dumps(cited_ids), selection_ready)
            outcome = reason or "completed"
            if reason:
                prefix = ("Réponse partielle : la comparaison et la sélection scientifique finales n'ont pas pu être achevées.\n\n" if bibliographic and not selection_ready
                    else "Réponse partielle : les références finales n'ont pas pu être validées.\n\n" if reason == "bibliography_validation_failed"
                    else "Réponse partielle.\n\n"
                    if reason in {"synthesis_timeout", "synthesis_unavailable"} else "Réponse partielle : la limite de recherche a été atteinte. Voici les résultats disponibles.\n\n")
                citations = [citation.model_copy(update={
                    "start_index": citation.start_index + len(prefix) if citation.start_index is not None else None,
                    "end_index": citation.end_index + len(prefix) if citation.end_index is not None else None}) for citation in citations]
                answer = prefix + answer
            known_urls = {citation.url for citation in citations}
            if not bibliographic:
                citations.extend(citation for url, citation in consulted.items() if url not in known_urls)
            known_urls.update(citation.url for citation in citations)
            for identifier in active.identifiers:
                record = registry[identifier]
                url = "https://doi.org/" + record.article.doi if record.article.doi else record.article.url
                if url.startswith(("https://", "http://")) and url not in known_urls:
                    citations.append(Citation(url=url, title=record.article.title, cited=False))
                    known_urls.add(url)
            return TurnResult(answer, citations, activity, bool(reason), reason, dict(counts), rounds, list(active.identifiers), active.snapshot())

        def fallback():
            lines = ["La synthèse finale n'a pas pu être générée.",
                "Voici les analyses ou notices conservées des articles concernés, sans nouvelle évaluation scientifique :", ""]
            # Existing conversational selection only; never invent a scientific ranking.
            ordered_ids = active.identifiers or list(registry)
            records = [registry[identifier] for identifier in ordered_ids[:5]]
            for record in records:
                article = record.article
                title = article.title.replace("[", "\\[").replace("]", "\\]")
                url = "https://doi.org/" + article.doi if article.doi else article.url
                label = f"[{title}]({url})" if url.startswith(("https://", "http://")) else title
                basis = {"metadata_only": "métadonnées uniquement", "abstract_only": "abstract disponible", "fulltext": "texte intégral récupéré (passages sélectionnés)"}[article.access_level]
                lines.append(f"- {label} — {basis}.")
                if record.analysis:
                    analysis = record.analysis
                    lines.append(f"  Analyse fondée sur : {analysis.analysis_basis}.")
                    for label, value in (("Méthode", analysis.method), ("Résultats", analysis.results)):
                        if value:
                            lines.append(f"  {label} conservés : {value[:180]}")
                    if analysis.analysis_limit:
                        lines.append(f"  Portée : {analysis.analysis_limit[:120]}")
            if not records:
                lines.append("Aucun article scientifique n'a encore été récupéré.")
            return "\n".join(lines)

        def synthesis_request(final_instructions, final_items, timeout, phase_deadline, attempt):
            nonlocal rounds
            remaining = phase_deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Budget propre de synthèse épuisé.")
            timeout = min(timeout, remaining)
            logger.info("EnnoScholar V2 turn=%s synthesis attempt=%s timeout=%.1f tools=none", turn_id, attempt, timeout)
            rounds += 1
            active.remember_visible(last_visible_rows)
            logger.info("EnnoScholar V2 turn=%s visible round=%s phase=synthesis ids=%s", turn_id, rounds,
                json.dumps([row["id"] for row in last_visible_rows]))
            options = {"text": bibliography_format()} if bibliographic and selection_ready else {}
            response = client.with_options(timeout=timeout, max_retries=0).responses.create(
                model=self.config.model, instructions=final_instructions, input=final_items,
                tools=[], tool_choice="none", store=False, max_output_tokens=self.config.max_output_tokens, **options)
            if getattr(response, "status", "completed") != "completed":
                raise RuntimeError("Synthèse non terminée.")
            answer, citations = parse_output([item.model_dump(exclude_none=True) for item in response.output])
            answer = declare_text(answer)
            require_grounding(answer)
            return answer, citations

        def retry_synthesis(final_instructions, final_items, reason=None, phase_deadline=None):
            # Exactly one recovery request. Its clock never uses the tool-stage deadline.
            logger.info("EnnoScholar V2 turn=%s synthesis_timeout retry=1 tools=none", turn_id)
            if phase_deadline is None:
                phase_deadline = time.monotonic() + self.config.synthesis_retry_timeout
            try:
                answer, citations = synthesis_request(final_instructions, final_items,
                    self.config.synthesis_retry_timeout, phase_deadline, 2)
                if answer.strip():
                    return finish(answer, citations, reason)
            except Exception as exc:
                logger.info("EnnoScholar V2 turn=%s synthesis_timeout retry=exhausted error=%s fallback=deterministic", turn_id, type(exc).__name__)
                if isinstance(exc, BibliographyError):
                    return finish(fallback(), [], "bibliography_validation_failed")
            return finish(fallback(), [], "synthesis_timeout")

        def finalize(reason):
            # A separate bounded synthesis phase can outlive the tool-stage deadline.
            # No scientific/network tool is reachable from this phase.
            if bibliographic and not selection_ready:
                logger.info("EnnoScholar V2 turn=%s selection_limit trigger=%s", turn_id, reason or "analysis_deadline")
                return finish(fallback(), [], reason or "scientific_selection_incomplete")
            phase_deadline = time.monotonic() + self.config.synthesis_timeout + self.config.synthesis_retry_timeout
            final_instructions = build_instructions() + "\nLa recherche est terminée. Aucun nouvel outil. Synthétise uniquement les résultats réellement collectés; indique les données manquantes sans demander de réduire le sujet."
            final_items = items
            if context_metrics(final_items, final_instructions, [])['serialized_bytes'] > self.config.max_context_bytes:
                final_items = [*kept_history[-2:], {"role": "user", "content": message}]
            if context_metrics(final_items, final_instructions, [])['serialized_bytes'] <= self.config.max_context_bytes:
                try:
                    answer, citations = synthesis_request(final_instructions, final_items,
                        self.config.synthesis_timeout, phase_deadline, 1)
                    if answer.strip():
                        return finish(answer, citations, reason)
                except APITimeoutError:
                    return retry_synthesis(final_instructions, final_items, reason, phase_deadline)
                except BibliographyError as exc:
                    logger.info("EnnoScholar V2 turn=%s bibliography_regeneration error=%s tools=none", turn_id, str(exc))
                    return retry_synthesis(final_instructions + "\nRépare uniquement les associations bibliographiques depuis les notices serveur : " + str(exc), final_items, reason, phase_deadline)
                except Exception as exc:
                    logger.info("EnnoScholar V2 turn=%s synthesis_fallback=%s", turn_id, type(exc).__name__)
            return finish(fallback(), [], reason or "synthesis_unavailable")

        from modules.LLM.llm_concurrency import llm_capacity_slot
        logger.info("EnnoScholar V2 turn=%s start soft=%s hard=%s max_rounds=%s", turn_id, soft_budget, self.config.hard_tool_budget, self.config.max_rounds)
        try:
            if local_reference and local_reference["status"] == "ambiguous":
                memory.pending_clarification = local_reference
                return finish(clarification_message(local_reference), [])
            if local_reference and local_reference["status"] == "not_found" and no_new_search:
                return finish(local_reference["message"], [])
            with self.search.turn_cache(), llm_capacity_slot("ennoscholar_v2:chat"):
                for _ in range(self.config.max_rounds):
                    if sum(counts.values()) >= self.config.hard_tool_budget:
                        return finalize("tool_budget_reached")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return finalize("time_budget_reached")
                    if time.monotonic() >= analysis_deadline and registry:
                        # Enter the reserved synthesis tail normally; a soft timeout
                        # is never an immediate partial answer or tools=[] transition.
                        return finalize("analysis_time_budget_reached" if bibliographic else None)
                    if bibliographic and registry and not selection_ready and _ >= self.config.max_rounds - 2:
                        comparison_phase = True
                    finishing = sum(counts.values()) >= soft_budget or time.monotonic() >= soft_deadline
                    current_instructions = build_instructions() + ("\n" + SOFT_INSTRUCTION if finishing else "")
                    metrics = context_metrics(items, current_instructions, TOOLS)
                    if metrics['estimated_tokens'] > self.config.max_input_tokens or last_input_tokens > self.config.max_input_tokens or metrics['serialized_bytes'] > self.config.max_context_bytes:
                        # Token pressure requests compaction, never synthesis-only finalization.
                        try:
                            compacted = client.with_options(timeout=min(self.config.timeout, remaining)).responses.compact(
                                model=self.config.model, input=items, instructions=current_instructions)
                            canonical = [item.model_dump(exclude_none=True) for item in compacted.output]
                            if not canonical or not any(item.get('type') == 'compaction' for item in canonical):
                                raise ValueError("État compacté absent.")
                            # Standalone output is already canonical: do not prune it again.
                            items = canonical
                            last_input_tokens = 0
                            logger.info("EnnoScholar V2 turn=%s standalone_compaction=completed", turn_id)
                        except Exception as exc:
                            logger.info("EnnoScholar V2 turn=%s standalone_compaction=%s continuation=native", turn_id, type(exc).__name__)
                        metrics = context_metrics(items, current_instructions, TOOLS)
                    if metrics['serialized_bytes'] > self.config.max_context_bytes:
                        return finalize("context_safety_bytes_reached")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return finalize("time_budget_reached")
                    logger.info("EnnoScholar V2 turn=%s context estimated_tokens=%s serialized_bytes=%s active_articles=%s last_input_tokens=%s",
                        turn_id, metrics['estimated_tokens'], metrics['serialized_bytes'], len(active.identifiers), last_input_tokens)
                    # With stored results this response can already be the final answer.
                    # Do not clip this possible synthesis to an old network deadline.
                    # Any tools it proposes still pass the existing deadline checks.
                    request_timeout = self.config.synthesis_timeout if registry else min(self.config.timeout, remaining)
                    request_client = client.with_options(timeout=request_timeout)
                    available_tools = TOOLS
                    if no_new_search:
                        available_tools = [tool for tool in available_tools
                            if (tool.get("name") or tool.get("type")) not in DISCOVERY_TOOLS]
                    if finishing and registry:
                        # The soft limit becomes a finishing phase, not a hard stop:
                        # fulltext/analysis remain available, but discovery and already
                        # completed notice retrieval cannot keep consuming the budget.
                        excluded = {"search_scientific", "find_similar_articles"}
                        if sum(counts.values()) >= soft_budget and completed["get_articles"]:
                            excluded.add("get_articles")
                        available_tools = [tool for tool in available_tools if tool.get("name") not in excluded]
                        logger.info("EnnoScholar V2 turn=%s phase=finishing elapsed=%.1f remaining=%.1f tool_calls=%s excluded=%s", turn_id,
                            time.monotonic() - started, remaining, sum(counts.values()), sorted(excluded))
                    if time.monotonic() >= fulltext_deadline:
                        available_tools = [tool for tool in available_tools if tool.get("name") != "get_fulltext"]
                    tool_choice = "auto"
                    if comparison_phase:
                        available_tools = [tool for tool in TOOLS if tool.get("name") == "set_article_selection"]
                        tool_choice = {"type": "function", "name": "set_article_selection"}
                    elif bibliography_repair:
                        available_tools, tool_choice = [], "none"
                    rounds += 1
                    active.remember_visible(last_visible_rows)
                    logger.info("EnnoScholar V2 turn=%s visible round=%s phase=%s ids=%s examined_index_ids=%s", turn_id, rounds,
                        "comparison" if comparison_phase else "bibliography_repair" if bibliography_repair else "chat",
                        json.dumps([row["id"] for row in last_visible_rows]), json.dumps(last_index_ids))
                    try:
                        options = {"text": bibliography_format()} if bibliographic and selection_ready else {}
                        response = request_client.responses.create(model=self.config.model, instructions=current_instructions,
                            input=items, tools=available_tools, tool_choice=tool_choice, store=False,
                            max_tool_calls=self.config.hard_tool_budget - sum(counts.values()),
                            context_management=[{"type": "compaction", "compact_threshold": self.config.compact_threshold}],
                            include=["web_search_call.action.sources", "reasoning.encrypted_content"],
                            max_output_tokens=self.config.max_output_tokens, **options)
                    except APITimeoutError:
                        if not registry:
                            raise
                        return retry_synthesis(current_instructions + "\nLa recherche est terminée. Aucun nouvel outil. Synthétise uniquement les résultats collectés et leur niveau d'accès réel.", items)
                    output = [item.model_dump(exclude_none=True) for item in response.output]
                    usage = getattr(response, 'usage', None)
                    observed = usage.get('input_tokens') if isinstance(usage, dict) else getattr(usage, 'input_tokens', None)
                    if isinstance(observed, int):
                        last_input_tokens = observed
                        logger.info("EnnoScholar V2 turn=%s response_input_tokens=%s", turn_id, observed)
                    for item in output:
                        if item.get("type") == "web_search_call":
                            if no_new_search:
                                activity.append(ToolActivity(tool="web_search", status="skipped"))
                            else:
                                count_tool("web_search", "completed" if item.get("status") == "completed" else "error")
                    answer, citations = parse_output(output)
                    answer = declare_text(answer)
                    for citation in citations:
                        consulted[citation.url] = citation.model_copy(update={"cited": False, "start_index": None, "end_index": None})
                    calls = [item for item in output if item.get("type") == "function_call"]
                    # Scope must be known even when the proposed tools are refused
                    # by a deadline; a skipped search is not a completed comparison.
                    for proposed in calls:
                        try:
                            metadata = json.loads(proposed.get("arguments", "{}"))
                            kind = metadata.get("request_kind")
                            if kind is not None:
                                declare_request(kind)
                            elif proposed.get("name") in {"search_scientific", "search_collected_articles", "find_similar_articles"}:
                                declare_request("bibliographic_search")
                        except (ValueError, TypeError, AttributeError):
                            pass  # The existing tool argument handler reports the error.
                    if not calls:
                        if not answer.strip():
                            raise RuntimeError("Réponse OpenAI sans texte ni appel d'outil.")
                        if bibliographic and not selection_ready:
                            if not registry:
                                return finish(fallback(), [], "scientific_selection_incomplete")
                            comparison_phase = True
                            items.append({"role": "user", "content": "Ne synthétise pas encore. Compare la fenêtre commune à ma demande originale et enregistre tes finalistes justifiés via set_article_selection."})
                            logger.info("EnnoScholar V2 turn=%s selection_required round=%s", turn_id, rounds)
                            continue
                        try:
                            require_grounding(answer)
                        except BibliographyError as exc:
                            logger.info("EnnoScholar V2 turn=%s bibliography_rejected error=%s", turn_id, str(exc))
                            if bibliography_repair:
                                return finish(fallback(), [], "bibliography_validation_failed")
                            bibliography_repair = True
                            items.append({"role": "user", "content": "Régénère uniquement la réponse bibliographique structurée avec les métadonnées exactes du registre. Aucun outil. Erreur : " + str(exc)})
                            continue
                        reason = "tool_budget_reached" if sum(counts.values()) >= self.config.hard_tool_budget else None
                        return finish(answer, citations, reason)
                    # Keep opaque reasoning and native compaction items within this turn.
                    items.extend(output)
                    for call in calls:
                        name = call.get("name", "")
                        soft_time = time.monotonic() >= soft_deadline
                        if (bibliography_repair or (comparison_phase and name != "set_article_selection")):
                            activity.append(ToolActivity(tool=name, status="skipped"))
                            result = {"error": "Clôture uniquement : aucun outil de recherche, lecture ou analyse ne doit être relancé."}
                        elif no_new_search and name in DISCOVERY_TOOLS:
                            activity.append(ToolActivity(tool=name, status="skipped"))
                            result = {"error": "Recherche interdite par l'utilisateur pour ce tour. Résoudre uniquement dans le registre de session; notices, fulltext et analyses d'articles connus restent autorisés."}
                            logger.info("EnnoScholar V2 turn=%s tool=%s status=skipped reason=no_new_search", turn_id, name)
                        elif name == "get_article" or (name in {"search_scientific", "find_similar_articles", "get_articles"}
                                and (sum(counts.values()) >= soft_budget or soft_time) and registry
                                and (name != "get_articles" or (sum(counts.values()) >= soft_budget and completed["get_articles"]))):
                            activity.append(ToolActivity(tool=name, status="skipped"))
                            result = {"error": ("get_article unitaire non disponible : utilise get_articles(article_ids=[...]) pour les quelques notices utiles."
                                if name == "get_article" else "Soft limit atteinte : ne répète pas les recherches ni les notices déjà obtenues; termine fulltext/analyse et synthétise les résultats disponibles.")}
                            logger.info("EnnoScholar V2 turn=%s tool=%s status=skipped reason=%s", turn_id, name,
                                "unitary_notice_disabled" if name == "get_article" else "soft_budget_finishing")
                        elif sum(counts.values()) >= self.config.hard_tool_budget or time.monotonic() >= analysis_deadline or (name == "get_fulltext" and time.monotonic() >= fulltext_deadline):
                            activity.append(ToolActivity(tool=name, status="skipped"))
                            result = {"error": "Budget atteint : appel non exécuté. Synthétiser les résultats existants."}
                        else:
                            status = "completed"
                            try:
                                arguments = json.loads(call.get("arguments", "{}"))
                                # Model-owned intent classification, never a keyword/ranking rule.
                                declared = arguments.pop("request_kind", None)
                                if declared is not None:
                                    declare_request(declared)
                                elif name in {"search_scientific", "search_collected_articles", "find_similar_articles"}:
                                    declare_request("bibliographic_search")
                                tool_deadline = fulltext_deadline if name in {"get_fulltext", "search_scientific", "find_similar_articles"} else analysis_deadline
                                if name == "search_scientific":
                                    tool_deadline = min(tool_deadline, time.monotonic() + self.config.search_timeout, soft_deadline)
                                if name == "set_article_selection":
                                    chosen = arguments.get("finalist_article_ids") or []
                                    if bibliographic:
                                        store = ArticleStore(registry)
                                        visible = {store.work_id(row["id"]) for row in last_visible_rows}
                                        examined = {store.work_id(key) for key in [*active.examined, *active.inspected]}
                                        selected = {store.work_id(key) for key in chosen}
                                        if not chosen or None in selected or not selected <= examined or arguments.get("justifications") is None:
                                            raise ValueError("Choisis des finalistes réellement examinés et justifie chacun.")
                                        if not comparison_presented or not selected <= visible:
                                            comparison_requested_ids = list(chosen)
                                            comparison_phase = True
                                            raise ValueError("Ces candidats seront présentés ensemble au prochain tour avant validation de la sélection.")
                                    result = active.select(chosen, arguments.get("pinned_article_ids"), arguments.get("justifications"))
                                    if bibliographic:
                                        selection_ready, comparison_phase = True, False
                                    logger.info("EnnoScholar V2 turn=%s selection finalists=%s justifications=%s", turn_id,
                                        json.dumps(active.finalists), json.dumps(active.justifications, ensure_ascii=False))
                                elif no_new_search and name == "read_article":
                                    # Do not bypass the ban through this external DOI/URL reader.
                                    result = self.execute_tool("resolve_article_reference", {"reference": arguments.get("url_or_doi")}, registry, request_client)
                                else:
                                    result = self.execute_tool(name, arguments, registry, request_client, deadline=tool_deadline)
                                if name in {"search_scientific", "search_collected_articles", "find_similar_articles", "read_article"} and bibliographic:
                                    selection_ready = False
                                if name == "search_collected_articles":
                                    logger.info("EnnoScholar V2 turn=%s local_search round=%s query=%s returned_ids=%s matches=%s exclusions=%s cursor=%s next_cursor=%s",
                                        turn_id, rounds, json.dumps(arguments.get("query"), ensure_ascii=False),
                                        json.dumps([row.get("article_id") for row in result.get("articles", [])]), result.get("matched_work_count"),
                                        json.dumps(arguments.get("exclude_article_ids")), arguments.get("cursor"), result.get("next_cursor"))
                                if name == "search_scientific":
                                    logger.info("EnnoScholar V2 turn=%s search query_count=%s corpus=%s collection=%s", turn_id,
                                        len(arguments.get("queries", [])), len(registry), json.dumps(result.get("collection", []), ensure_ascii=False))
                                if result.get("error") or (result.get("articles") and all(row.get("error") for row in result["articles"])):
                                    status = "error"
                            except Exception as exc:
                                status = "error"
                                logger.info("EnnoScholar V2 turn=%s tool_error=%s error=%s", turn_id, name, type(exc).__name__)
                                result = {"error": "Outil indisponible, arguments invalides ou source inaccessible. Aucun résultat ne doit être inventé."}
                            count_tool(name, status)
                        if name in {"resolve_article_reference", "read_article"} and result.get("status") in {"ambiguous", "not_found"}:
                            if result["status"] == "ambiguous":
                                memory.pending_clarification = result
                                return finish(clarification_message(result), [])
                            if no_new_search:
                                return finish(result["message"], [])
                        if name in {"resolve_article_reference", "read_article"} and result.get("status") == "resolved":
                            active.inspect([{"article_id": result["article_id"]}])
                            memory.pending_clarification = None
                            wire_result = {key: value for key, value in result.items() if key != "article"}
                            wire_result["selection_updated"] = True
                        elif name == "get_articles" and "articles" in result:
                            # Reuse the existing context manager unchanged: detail
                            # notices live in its active selection, not in repeated outputs.
                            rows = [row for row in result["articles"] if row.get("id")]
                            active.inspect(rows, detailed=True)
                            wire_result = {"article_ids": [row["id"] for row in rows], "selection_updated": True,
                                "errors": [row for row in result["articles"] if row.get("error")]}
                        else:
                            wire_result = active.tool_result(name, result)
                        logger.info("EnnoScholar V2 turn=%s tool=%s result_tokens=%s wire_tokens=%s result_bytes=%s wire_bytes=%s active_articles=%s",
                            turn_id, name, estimate_tokens(result), estimate_tokens(wire_result),
                            serialized_bytes(result), serialized_bytes(wire_result), len(active.identifiers))
                        logger.info("EnnoScholar V2 turn=%s inspection round=%s inspected_ids=%s", turn_id, rounds, json.dumps(active.inspected))
                        items.append({"type": "function_call_output", "call_id": call["call_id"], "output": json.dumps(wire_result, ensure_ascii=False)})
                    before_pruning = len(items)
                    items = prune_native_compaction(items)
                    if any(item.get('type') == 'compaction' for item in output):
                        # Reported input usage described the old window, not the new state.
                        last_input_tokens = 0
                    if len(items) != before_pruning:
                        logger.info("EnnoScholar V2 turn=%s native_compaction_prefix_removed=%s", turn_id, before_pruning - len(items))
                return finalize("round_budget_reached")
        finally:
            logger.info("EnnoScholar V2 turn=%s end=%s rounds=%s tool_calls=%s tool_counts=%s articles=%s", turn_id, outcome,
                rounds, sum(counts.values()), json.dumps(dict(counts), sort_keys=True), len(registry))
