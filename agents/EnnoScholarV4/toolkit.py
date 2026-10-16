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

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .evidence import passages, choose_passages, validate_claims, snapshot, exact_source_valid
from .tools.fulltext import get_fulltext
from .models import Article, normalize_doi, public_link
from .store import ArticleStore, compact, interleave_by_provider
from .tools.outline import OutlineArgs, manage_outline, resolve_section
from .context import evidence_projection, transmitted_evidence_ids
from .scientific_scope import ScientificScope, temporal_scope, literature_coverage
from .scientific_visuals import VisualArgs, create_visual

_reading_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="scholar-v4-reading")


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchArgs(Args):
    query: str = Field(min_length=1, max_length=1500, description="4 à 10 mots-clés scientifiques précis d'un seul axe, souvent en anglais. Sans OR ni parenthèses de préférence : Semantic Scholar et arXiv reçoivent une forme en mots-clés.")
    sources: list[Literal["semantic_scholar", "openalex", "arxiv"]] | None
    limit_per_source: int = Field(ge=1, le=100, description="8 à 15 en général : la pertinence prime sur la quantité et le plafond de nouveaux candidats est partagé par toutes les recherches du message.")
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
    equations: list[Claim] = Field(default_factory=list, max_length=8)
    parameters: list[Claim] = Field(default_factory=list, max_length=8)
    experimental_setup: list[Claim] = Field(max_length=8)
    validation_protocol: list[Claim] = Field(default_factory=list, max_length=8)
    datasets_materials: list[Claim] = Field(default_factory=list, max_length=8)
    objective: list[Claim] = Field(max_length=8)
    method: list[Claim] = Field(max_length=8)
    contribution: list[Claim] = Field(max_length=8)
    hypotheses: list[Claim] = Field(max_length=8)
    results: list[Claim] = Field(max_length=8)
    metrics: list[Claim] = Field(default_factory=list, max_length=8)
    tables_figures: list[Claim] = Field(default_factory=list, max_length=8)
    comparisons: list[Claim] = Field(default_factory=list, max_length=8)
    limitations: list[Claim] = Field(max_length=8)
    conditions_of_validity: list[Claim] = Field(default_factory=list, max_length=8)
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
    reviewed_evidence_ids: list[str] = Field(default_factory=list, max_length=40)
    reviewed_fields: list[Literal["problem", "objective", "contribution", "method", "technical_mechanism", "equations", "parameters", "experimental_setup", "validation_protocol", "datasets_materials", "results", "metrics", "tables_figures", "comparisons", "limitations", "conditions_of_validity"]] = Field(default_factory=list, max_length=20)
    table_number: str | None = Field(default=None, max_length=40,
        description="Optional exact table number to read structured scientific rows without unrelated prose.")
    evidence_ids: list[str] | None = Field(default=None, max_length=8,
        description="Optional exact evidence_ids of this article to (re)read, instead of passage_query/passage_offset.")


class SimilarArgs(Args):
    article_id: str
    mode: Literal["similar", "alternatives", "references", "citations"]
    conceptual_query: str | None = Field(max_length=1500)
    family: str = Field(max_length=160)


class ScientificRelation(Args):
    article_ids: list[str] = Field(min_length=2, max_length=8)
    kind: Literal["improvement", "partial_resolution", "alternative", "contradiction", "complementarity", "conditional_superiority", "historical_resolution"]
    statement: str = Field(min_length=1, max_length=2500)
    origin: Literal["source_statement", "comparative_inference"]
    conditions: str = Field(min_length=1, max_length=3000)
    experimental_results: list[Claim] = Field(default_factory=list, max_length=12)
    evidence_ids: list[str] = Field(min_length=2, max_length=30)
    comparable_protocols: bool | None = None
    uncertainties: list[str] = Field(default_factory=list, max_length=20)


class ClaimOffset(Args):
    article_id: str = Field(min_length=1)
    offset: int = Field(ge=0)


class ClaimCursorArgs(Args):
    # Responses strict schemas require fixed object keys. Transport a dynamic
    # article->offset mapping as entries, preserving the existing stored map.
    claim_offsets: list[ClaimOffset] = Field(default_factory=list,
        description="Reprendre next_claim_offsets avec une entrée {article_id, offset} par article ; [] pour commencer.")

    @field_validator("claim_offsets", mode="before")
    @classmethod
    def accept_existing_cursor_map(cls, value):
        if isinstance(value, dict):
            return [{"article_id": aid, "offset": offset} for aid, offset in value.items()]
        return value

    @field_validator("claim_offsets")
    @classmethod
    def unique_article_cursors(cls, value):
        if len({entry.article_id for entry in value}) != len(value):
            raise ValueError("Un seul curseur de reprise est autorisé par article.")
        return value

    @property
    def claim_offset_map(self):
        return {entry.article_id: entry.offset for entry in self.claim_offsets}


class CompareArgs(ClaimCursorArgs):
    article_ids: list[str] = Field(min_length=2, max_length=8)
    research_context: str = Field(max_length=5000)
    conclusions: list[Claim] | None = Field(max_length=20)
    relations: list[ScientificRelation] | None = Field(default=None, max_length=20)


class ProjectFactInput(Args):
    category: Literal["objective", "method", "parameter", "experimental_setup", "dataset", "result", "difficulty", "condition", "constraint", "uncertainty"]
    statement: str = Field(min_length=1, max_length=2500)
    exact_quote: str = Field(min_length=1, max_length=2500)
    source_type: Literal["consultant_message", "uploaded_project_document"]
    source_id: str
    value: str | None = None
    unit: str | None = None


class ProjectFactsArgs(Args):
    facts: list[ProjectFactInput] = Field(min_length=1, max_length=30)


class ArgumentStep(Args):
    kind: Literal["problem", "method", "mechanism", "experimental_result", "comparison", "limitation", "partial_resolution", "project_confrontation", "open_question"]
    statement: str = Field(min_length=1, max_length=2500)
    article_ids: list[str] = Field(default_factory=list, max_length=8)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    relation_ids: list[str] = Field(default_factory=list, max_length=8)
    project_fact_ids: list[str] = Field(default_factory=list, max_length=8)
    conditions: str = Field(default="", max_length=2500)
    uncertainty: str = Field(default="", max_length=2500)


class ArgumentAxis(Args):
    axis: str = Field(min_length=1, max_length=300)
    scientific_problem: str = Field(min_length=1, max_length=2500)
    section_id: str | None = None
    steps: list[ArgumentStep] = Field(min_length=1, max_length=20)
    transition: str = Field(default="", max_length=2500)


class BlueprintArgs(ClaimCursorArgs):
    action: Literal["read", "save"]
    merge: bool = False
    global_question: str = Field(default="", max_length=3000)
    narrative_logic: str = Field(default="", max_length=5000)
    axes: list[ArgumentAxis] = Field(default_factory=list, max_length=20)
    remaining_questions: list[str] = Field(default_factory=list, max_length=30)


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
    scientific_scope: ScientificScope | None = None


class Availability(Args):
    article_id: str
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    kind: Literal["publisher", "preprint"]
    source_url: str
    evidence_id: str


class CoverageArgs(Args):
    action: Literal["read", "save"]
    available_dates: list[Availability] = Field(default_factory=list, max_length=30)
    recent_axes_checked: list[str] = Field(default_factory=list, max_length=30)
    foundational_article_ids: list[str] = Field(default_factory=list, max_length=30)
    possible_resolutions_checked: list[str] = Field(default_factory=list, max_length=30)
    missing_axes: list[str] = Field(default_factory=list, max_length=30)
    uncertainties: list[str] = Field(default_factory=list, max_length=30)
    sufficient: bool = False


class AnalysisSave(Args):
    article_id: str
    analysis: Analysis


class FinalArgs(Args):
    answer: str = Field(min_length=1, max_length=30000)
    article_ids: list[str] = Field(max_length=80)
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
    paragraph_id: str | None = None
    claims: list[Claim] = Field(default_factory=list, max_length=80)
    numeric_claims: list["NumericClaim"] = Field(default_factory=list, max_length=80)
    calculations: list["Calculation"] = Field(default_factory=list, max_length=40)


class NumericClaim(Args):
    evidence_id: str
    column: str
    value: str
    unit: str
    metric: str


class Calculation(Args):
    left: NumericClaim
    right: NumericClaim
    operation: Literal["difference", "relative_percent"]
    result: str
    rounding_digits: int = Field(default=2,ge=0,le=6)
    unit: str


WriteArgs.model_rebuild()


class ReviewIssue(Args):
    section_id: str | None
    statement: str = Field(max_length=3000)
    severity: Literal["blocking", "warning", "information"]
    evidence_ids: list[str] = Field(max_length=30)


class ReviewArgs(Args):
    action: Literal["read", "save"]
    section_id: str | None = None
    text_offset: int = Field(default=0, ge=0)
    issues: list[ReviewIssue] = Field(default_factory=list, max_length=60)
    conclusion: Literal["unassessed", "insufficient_evidence", "known_solution", "uncertainty_supported"] = "unassessed"
    reasoning: str = Field(default="", max_length=5000)


class HistoryArgs(Args):
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=20)


class ConsultantDecision(Args):
    article_id: str
    decision: Literal["accept", "reject", "hold", "restore"]


class ConsultantDecisionsArgs(Args):
    decisions: list[ConsultantDecision] = Field(max_length=30)
    order: list[str] | None = Field(max_length=30, description="Ordre demandé par le consultant, sinon null.")
    source_message_id: str = Field(min_length=1, max_length=80, description="ID du message du consultant, ou latest.")
    exact_quote: str = Field(min_length=2, max_length=500, description="Mots exacts du consultant exprimant la décision.")


TOOLS = {
    "record_consultant_decisions": (ConsultantDecisionsArgs, "Enregistrer les décisions du CONSULTANT exprimées dans le chat (« garde ces articles », « retire le 3 », « mets en attente », « place X en premier », revenir sur un choix) avec ses mots exacts. accept/reject/hold/restore et ordre. Ne valide jamais une affirmation, un plan, un document ni une éligibilité CIR."),
    "record_project_facts": (ProjectFactsArgs, "Enregistrer les faits exacts du projet depuis un message du consultant ou un document projet importé ; garder cette provenance distincte des publications."),
    "build_argument_blueprint": (BlueprintArgs, "read expose analyses, comparaisons et faits projet ; save enregistre une histoire scientifique par axe, reliée aux preuves et relations réellement lues. Ne conclut jamais à un verrou CIR."),
    "create_scientific_visual": (VisualArgs, "Créer ou régénérer une figure liée à une section et à son argument : schéma de synthèse étayé, graphique de cellules vérifiées d'un même protocole, ou figure PDF originale avec permission exacte. Recette éditable et provenance conservées."),
    "review_document": (ReviewArgs, "read fournit contrôles déterministes et une fenêtre du texte enregistré (section_id, text_offset). save consigne ta revue sémantique, expérimentale et globale, distincte de l'intégrité technique et sans approbation humaine."),
    "review_literature_coverage": (CoverageArgs, "Lire la disponibilité temporelle ; save consigne ta revue scientifique de couverture et les dates prouvées. Ne change aucun classement ni moteur. sufficient reste une appréciation du modèle, jamais une preuve d'éligibilité CIR."),
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


def definitions(include_document_tools=True):
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
        "parameters": strict_schema(model.model_json_schema())} for name, (model, description) in TOOLS.items()
        if include_document_tools or name not in {"create_scientific_visual","review_document","review_literature_coverage",
            "record_project_facts", "build_argument_blueprint"}]


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
        # Notices collected during this message, so the title index favors them.
        self.turn_article_ids = []
        self.finalization_only = False
        # Set by the agent's publication phase only (never by write_section).
        self.final_verification_allowed = False
        self.final_verification_attempted = set()
        self.metadata_detail_ids = set()

    def save(self):
        self.store.save()

    def delivered(self, window_ids, *, inputs=()):
        """Acknowledge a completed model request using its actual source payload."""
        self.store.delivered(window_ids)
        transmitted = transmitted_evidence_ids(inputs, self.evidence)
        for window in self.reading_windows:
            if window["status"] in {"queued", "undelivered"} and window["window_id"] in window_ids:
                if transmitted and set(window["evidence_ids"]) <= transmitted:
                    window["status"] = "presented"
                for eid in window["evidence_ids"]:
                    if eid not in transmitted:
                        continue
                    if eid not in self.shown_evidence:
                        self.shown_evidence.append(eid)
                    article_id = self.evidence.get(eid, {}).get("article_id")
                    if article_id and article_id not in self.store.exposed:
                        self.store.exposed.append(article_id)
        for window in self.state.get("document_reading_windows", []):
            if window["window_id"] in window_ids:
                window["status"] = "presented"

    def search(self, args, deadline):
        if self.search_count >= self.config.max_research_rounds:
            return {"warning": "Budget de recherches atteint ; corpus conservé et explorations locales disponibles."}
        key = [" ".join(args.query.split()), sorted(args.sources or ["semantic_scholar", "openalex", "arxiv"]), args.offset, args.limit_per_source]
        searches = self.state.setdefault("recent_searches", [])
        if any(s.get("key") == key and not s.get("warnings") for s in searches):
            return {"warning": "Cette recherche a déjà été collectée. Explorez localement ou changez l'axe/la page."}
        if self.new_candidates >= self.config.max_search_results:
            # Refused before any provider call: it does not consume a search round,
            # and the refusal stays visible instead of looking like an empty result.
            self.state.setdefault("refused_searches", []).append({"query": args.query, "family": args.family,
                "reason": "new_candidate_limit"})
            return {"warning": "Limite de nouveaux candidats atteinte ; utilisez les articles conservés.",
                "refused": "new_candidate_limit", "collected": 0, "new_candidates": 0,
                "new_candidate_budget_remaining": 0,
                "guidance": "search_collected_articles(query=...) retrouve les notices déjà collectées selon les termes de la requête."}
        self.search_count += 1
        rows, warnings = self.sources.search(args.query, args.sources, min(args.limit_per_source, self.config.max_search_results),
            min(deadline, time.monotonic() + 35), self.check, offset=args.offset)
        self.check()
        stats = getattr(self.sources, "last_provider_stats", None)
        provider_stats = stats if isinstance(stats, dict) else {}
        # A provider answering nothing is reported, never silently ignored.
        empty = [name for name, row in provider_stats.items() if row.get("returned") == 0]
        # Only works absent from the corpus consume the new-candidate limit;
        # rediscovered or cross-provider duplicates merge into existing records.
        # Share the unchanged limit: one broad query keeps at most a third of the
        # remaining room (at least 10), so later targeted axes are not refused.
        # Interleaving keeps each provider's best-ranked notices within the share.
        remaining = self.config.max_search_results-self.new_candidates
        share = min(remaining, max(10, -(-remaining//3)))
        ids, dropped = self.store.insert(interleave_by_provider(rows), args.family, max_new=share)
        new = self.store.last_new_count
        self.new_candidates += new
        self.turn_article_ids.extend(aid for aid in ids if aid not in self.turn_article_ids)
        searches.append({"key": key, "query": args.query, "family": args.family, "article_ids": ids, "warnings": warnings,
            "new_candidates": new, "limit_dropped": dropped, "providers": provider_stats})
        proposed = [self.registry[aid] for aid in ids]
        info = {"method": "all_new_candidates", "scores_are_scientific_ranking": False}
        return {**self.store.window(proposed, args.query), "collected": len(ids), "new_candidates": new,
            "already_known": len(ids)-new, "new_candidate_budget_remaining": self.config.max_search_results-self.new_candidates,
            "corpus_total": len(self.registry),
            "warnings": warnings, "capacity_dropped": dropped, "retrieval": info,
            **({"providers": {name: row.get("returned") for name, row in provider_stats.items()}} if provider_stats else {}),
            **({"empty_providers": empty, "empty_guidance": "Aucun résultat chez ces fournisseurs : reformuler en 4 à 8 mots-clés précis du problème, sans OR ni parenthèses."} if empty else {}),
            "guidance": "Les articles non présentés restent recherchables. Réorientez les concepts, explorez les voisins ou utilisez offset pour les pages fournisseurs suivantes. Le plafond de nouveaux candidats est partagé par toutes les recherches du message : un limit_per_source modéré préserve les axes suivants."}

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
        if name == "create_scientific_visual":
            return create_visual(self,args)
        if name in {"analyze_article", "compare_articles", "verify_bibliography"}:
            self.state["current_turn_article_ids"] = list(dict.fromkeys([
                *self.state.get("current_turn_article_ids", []), *args.article_ids]))
        if name == "read_conversation":
            messages = self.state.get("messages", [])
            return {"messages": messages[args.offset:args.offset+args.limit], "total": len(messages)}
        if name == "record_project_facts":
            from .argumentation import save_project_facts
            return save_project_facts(self.state, args.facts)
        if name == "build_argument_blueprint":
            from .argumentation import argument_packet, bounded_argument_packet, blueprint_status, save_blueprint
            if args.action == "read":
                return {"packet": bounded_argument_packet(argument_packet(self.state, self, claim_offsets=args.claim_offset_map), min(14000, self.config.max_context_chars//3)),
                    "blueprint": self.state.get("argument_blueprint", {}),
                    "status": blueprint_status(self.state, self.registry)}
            return save_blueprint(self.state, self, args)
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
            self.state.setdefault("document", {})["active_section_id"] = section["id"]
            if args.action == "read":
                from .writing_context import writing_context, section_projection
                ids = list(dict.fromkeys(section["article_ids"] + args.article_ids))
                return {"section": section_projection(section), "writing_context": writing_context(self.state, self, {**section, "article_ids": ids}, budget=14000),
                    "guidance": "Lis les analyses et leurs passages avec analyze_article. Signale les sources insuffisantes et les limites de comparabilité."}
            if not args.text or not args.article_ids or not args.evidence_ids:
                raise ValueError("La rédaction sauvegardée exige du texte, des articles et des preuves consultées.")
            # A scientific draft may be prepared before human approval. Only the
            # authenticated document action can approve the plan or manuscript.
            approval_required = not bool(self.state.get("outline", {}).get("approval"))
            from .scientific_review import validate_number, validate_calculation
            claims = [c.model_dump() for c in args.claims]
            self.claim_union(claims, args.article_ids)
            if any(c["statement"] not in args.text for c in claims):
                raise ValueError("Chaque affirmation déclarée doit figurer exactement dans le texte rédigé.")
            facts = [validate_number(f.model_dump(), self) for f in args.numeric_claims]
            calculations = [validate_calculation(c.model_dump(),self) for c in args.calculations]
            if any(f["evidence_id"] not in args.evidence_ids or f["article_id"] not in args.article_ids for f in facts):
                raise ValueError("Résultat numérique hors des références du brouillon.")
            if any(eid not in args.evidence_ids for c in calculations for eid in c["evidence_ids"]):
                raise ValueError("Calcul hors des preuves du brouillon.")
            paragraphs = [{"id":"paragraph_"+uuid.uuid4().hex,"text":p} for p in re.split(r"\n\s*\n",args.text) if p.strip()]
            raw_text = args.text
            if args.paragraph_id:
                if not section.get("drafts"):
                    raise ValueError("Aucun brouillon à réviser localement.")
                old = section["drafts"][-1]
                paragraphs = copy.deepcopy(old.get("paragraphs", []))
                target = next((p for p in paragraphs if p["id"]==args.paragraph_id),None)
                if target is None:
                    raise ValueError("Paragraphe inconnu ; relire le brouillon.")
                old_text = target["text"]
                target["text"] = args.text
                raw_text = "\n\n".join(p["text"] for p in paragraphs)
                claims = [c for c in old.get("claims",[]) if c["statement"] not in old_text]+claims
                facts = [f for f in old.get("numeric_claims",[]) if f["value"] not in old_text]+facts
                calculations = [c for c in old.get("calculations",[]) if c["result"] not in old_text]+calculations
                args.article_ids = list(dict.fromkeys([*old["article_ids"],*args.article_ids]))
                args.evidence_ids = list(dict.fromkeys([*old["evidence_ids"],*args.evidence_ids]))
            self.claim_union([{"evidence_ids": args.evidence_ids}], args.article_ids)
            if any(not self.store.article(aid).analysis for aid in args.article_ids):
                raise ValueError("Analyser les sources avant de sauvegarder une section scientifique.")
            # Validate every bibliography link before committing the draft.
            previous = self.finished
            previous_response = self.state.get("last_response")
            previous_history_length = len(self.state.get("response_history", []))
            # Editing known evidence requires no provider requests.
            was_final_only = self.finalization_only
            self.finalization_only = True
            try:
                checked = self.finalize(FinalArgs(answer=raw_text, article_ids=args.article_ids, evidence_ids=args.evidence_ids,
                    coverage_sufficient=False, remaining_uncertainties=[]), deadline)
            finally:
                self.finalization_only = was_final_only
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
            old_document = copy.deepcopy(self.state.get("document", {}))
            outline_versions = len(self.state.get("outline_versions", []))
            document_versions = len(self.state.get("document_versions", []))
            draft_id = uuid.uuid4().hex
            try:
                section.setdefault("drafts", []).append({"id": draft_id, "text": rendered,
                    "raw_text":raw_text,"paragraphs":paragraphs,"claims":claims,"numeric_claims":facts,"calculations":calculations,
                    "instructions": args.instructions, "article_ids": args.article_ids, "evidence_ids": args.evidence_ids})
                self.state.setdefault("outline_versions", []).append(old_outline)
                self.state["outline"]["version"] += 1
                from .manuscript import save_document_revision
                revision = save_document_revision(self.state)
            except Exception:
                self.state["outline"] = old_outline
                self.state["document"] = old_document
                del self.state.setdefault("outline_versions", [])[outline_versions:]
                del self.state.setdefault("document_versions", [])[document_versions:]
                raise
            return {"saved": True, "section_id": section["id"], "draft_id": draft_id,
                "document_version_id": revision["id"], "text": rendered,
                "approval_required": approval_required}
        if name == "review_document":
            from .scientific_review import deterministic_review
            review = deterministic_review(self.state, self)
            if args.action == "read":
                sections = self.state.get("outline",{}).get("sections",[])
                section = resolve_section(self.state,args.section_id) if args.section_id else next((s for s in sections if s.get("drafts")), None)
                if section:
                    if not section.get("drafts"):
                        raise ValueError("Cette section n'est pas encore rédigée ; choisir un brouillon existant pour la revue.")
                    draft = section["drafts"][-1]
                    text = draft.get("raw_text",draft["text"])
                    wid = "document_read_"+uuid.uuid4().hex
                    end = min(len(text),args.text_offset+7000)
                    self.state.setdefault("document_reading_windows",[]).append({"window_id":wid,"status":"queued",
                        "draft_id":draft["id"],"section_id":section["id"],"start":args.text_offset,"end":end})
                    review.update(window_id=wid,section_id=section["id"],text=text[args.text_offset:end],next_offset=end,text_total=len(text))
                review["sections"] = [{"id":s["id"],"title":s["title"],"draft_id":(s.get("drafts") or [{}])[-1].get("id")} for s in sections]
                return review
            for issue in args.issues:
                if issue.evidence_ids:
                    self.claim_union([{"evidence_ids":issue.evidence_ids}],list(self.registry))
            complete = True
            for s in self.state.get("outline",{}).get("sections",[]):
                if not s.get("drafts"):
                    complete = False
                    continue
                d=s["drafts"][-1]; cursor=0
                ranges=sorted((w["start"],w["end"]) for w in self.state.get("document_reading_windows",[])
                    if w["status"]=="presented" and w["draft_id"]==d["id"])
                for start,end in ranges:
                    if start<=cursor:
                        cursor=max(cursor,end)
                complete = complete and cursor>=len(d.get("raw_text",d["text"]))
            review["scientific"] = {"issues":[i.model_dump() for i in args.issues],"conclusion":args.conclusion,
                "reasoning":args.reasoning,"origin":"conversation_model_assessment","all_current_text_examined":complete}
            self.state["scientific_review"]=review
            self.state.setdefault("document",{})["review_stale"]=False
            return {"saved":True,"review":review}
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
            # First recover an accessible scientific document, even when a page
            # was requested. A subsequent page request can complement saved HTML
            # with a PDF; it must not exclude HTML on the initial recovery.
            pdf_needed = args.page_start is not None and bool(article.fulltext.get("text")) and not article.fulltext.get("pdf_file")
            if not article.fulltext.get("text") or pdf_needed:
                from agents.EnnoScholarV2.tools.article_reader import matches_article
                from agents.EnnoScholarV2.models import Article as LegacyArticle
                candidates = list(args.candidate_urls or [])
                candidates.extend(s["url"] for s in self.state.get("web_sources", []) if s.get("url") and
                    matches_article(LegacyArticle(title=article.title), s.get("title", "")))
                future = _reading_pool.submit(get_fulltext, article.model_copy(deep=True), self.sources, deadline, self.check, candidates, self.config, pdf_only=pdf_needed)
                try:
                    wait = self.config.max_pdf_download_seconds+3 if pdf_needed else self.config.max_download_seconds+3
                    result = future.result(timeout=max(.001, min(wait, deadline-time.monotonic())))
                except FutureTimeout:
                    future.cancel(); self.check()
                    return {"article_id": article.id, "available": None, "warning": "Lecture interrompue ; accès intégral inconnu."}
                self.check()
                if pdf_needed and not result.get("text") and article.fulltext.get("text"):
                    article.fulltext.setdefault("attempts", []).extend(result.get("attempts", []))
                    self.save()
                    return {"article_id": article.id, "warning": "PDF complémentaire non récupéré ; source HTML conservée, pagination non vérifiable.", "attempts": result.get("attempts", [])}
                if pdf_needed:
                    old_version = article.fulltext.get("document_version", {}).get("version_id")
                    if old_version:
                        article.documents[old_version] = copy.deepcopy(article.fulltext)
                result["attempts"] = [*article.fulltext.get("attempts", []), *result.get("attempts", [])][-100:]
                article.fulltext = result
                article.access_level = result["access_level"]
                article.access_status = result["access_status"]
                if result.get("text"):
                    article.fulltext_available, article.fulltext_source = True, result["url"]
                    version = result.get("document_version")
                    if version:
                        article.source_version = version["label"]
                        article.documents[version["version_id"]] = copy.deepcopy(result)
            if args.page_start is not None:
                from .pdf_store import extract_pages
                if not article.fulltext.get("pdf_file"):
                    self.save()
                    return {"article_id": article.id,
                        **{k: v for k, v in article.fulltext.items() if k not in {"text", "chunks"}},
                        "warning": "Texte scientifique conservé ; pagination non vérifiable sans PDF complémentaire.",
                        "guidance": "analyze_article(action=read) peut lire ce texte ; un nouvel appel get_fulltext avec page_start peut rechercher un PDF complémentaire."}
                chunks, page_count = extract_pages(article.fulltext["pdf_file"], args.page_start, args.page_limit or 5)
                replaced_pages = {c["page"] for c in chunks}
                retained = [c for c in article.fulltext.get("chunks", []) if c.get("page") not in replaced_pages]
                article.fulltext["chunks"] = sorted([*retained, *chunks], key=lambda c: c.get("page") or 0)
                article.fulltext["text"] = "\n\n".join(chunk["text"] for chunk in article.fulltext["chunks"])
                article.fulltext["page_count"] = page_count
                # Page presence alone does not mean every page was read by GPT.
                article.fulltext["truncated"] = len({c.get("page") for c in article.fulltext["chunks"]}) < page_count
                version = article.fulltext.get("document_version")
                if version:
                    article.documents[version["version_id"]] = copy.deepcopy(article.fulltext)
            self.save()
            return {"article_id": article.id, **{k: v for k, v in article.fulltext.items() if k not in {"text", "chunks"}},
                "analysis_basis": article.analysis_basis, "guidance": "analyze_article(action=read) présente les passages ; tu produis ensuite l'analyse avec action=save."}
        if name == "analyze_article":
            if args.action == "save":
                if len(args.article_ids) != 1 or args.analysis is None:
                    raise ValueError("save exige exactement un article et son analyse.")
                a = self.store.article(args.article_ids[0])
                if args.reviewed_evidence_ids:
                    if not args.reviewed_fields or not args.analysis.analysis_limit.strip():
                        raise ValueError("Une revue sans résultat précise les champs examinés et sa limite.")
                    if any(eid not in self.evidence or eid not in self.shown_evidence or
                            self.evidence[eid]["article_id"] != a.id or not exact_source_valid(self.evidence[eid], self.snapshots)
                            for eid in args.reviewed_evidence_ids):
                        raise ValueError("La revue exige des preuves exactes de cet article réellement présentées au modèle.")
                from .tools.article_analysis import ArticleAnalyzer
                has_claims = any(isinstance(v, list) and v for v in args.analysis.model_dump().values())
                if has_claims or not args.reviewed_evidence_ids:
                    ArticleAnalyzer.save(a, args.analysis, self.evidence, self.shown_evidence, self.snapshots)
                if args.reviewed_evidence_ids:
                    self.state.setdefault("scientific_reading_reviews", []).append({"article_id": a.id,
                        "evidence_ids": args.reviewed_evidence_ids, "fields": args.reviewed_fields,
                        "outcome": "examined_without_result", "note": args.analysis.analysis_limit})
                from .argumentation import refresh_relations
                refresh_relations(self)
                self.store.examined_article(a.id)
                self.save()
                return {"saved": True, "article_id": a.id, "access_status": a.access_status,
                    "analysis_basis": a.analysis_basis, "evidence_ids": [e["evidence_id"] for e in a.analysis.get("evidence", [])],
                    "reviewed_evidence_ids": args.reviewed_evidence_ids, "reviewed_fields": args.reviewed_fields}
            result = []
            for aid in args.article_ids:
                a = self.store.article(aid)
                all_rows = passages(a)
                if args.table_number is not None:
                    all_rows = [r for r in all_rows if str(r.get("table", {}).get("number")) == args.table_number]
                source = snapshot(a)
                self.snapshots[source["fingerprint"]] = source
                blocked = set(self.shown_evidence)
                blocked.update(eid for w in self.reading_windows if w["status"] == "queued" for eid in w["evidence_ids"])
                ordered = choose_passages(all_rows, args.passage_query, 0, len(all_rows))
                if args.evidence_ids:
                    # Exact passages requested by ID; the passage cursor is not moved.
                    by_id = {r["evidence_id"]: r for r in all_rows}
                    ordered = [by_id[eid] for eid in dict.fromkeys(args.evidence_ids) if eid in by_id]
                cursor, chosen, rows = (0 if args.evidence_ids else args.passage_offset), [], []
                while cursor < len(ordered) and len(rows) < args.passages_per_article:
                    row = ordered[cursor]
                    chosen.append(row)
                    cursor += 1
                    if args.revisit or row["evidence_id"] not in blocked:
                        rows.append(row)
                for row in rows:
                    self.evidence[row["evidence_id"]] = row
                reading_id = "reading_" + uuid.uuid4().hex
                self.reading_windows.append({"window_id": reading_id, "article_id": aid,
                    "evidence_ids": [r["evidence_id"] for r in rows], "status": "queued"})
                article_window = self.store.window([a], "analysis", "source_read")
                result.append({"window_id": article_window["window_id"], "reading_window_id": reading_id,
                    "article": compact(a, 300), "passages": [evidence_projection(r) for r in rows], "passages_total": len(all_rows),
                    "already_provided_evidence_ids": [r["evidence_id"] for r in chosen if r not in rows],
                    "next_offset": args.passage_offset if args.evidence_ids else cursor, "extraction_truncated": a.fulltext.get("truncated", False),
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
                self.turn_article_ids.extend(aid for aid in ids if aid not in self.turn_article_ids)
                proposed, info = self.store.search(args.conceptual_query or "", 8, True, ids=ids)
                return {**self.store.window(proposed, args.mode), "collected": len(ids), "warnings": warnings, "capacity_dropped": dropped, "retrieval": info}
            if not args.conceptual_query:
                raise ValueError("Fournissez une requête conceptuelle fondée sur le problème/contribution, pas le titre seul.")
            return self.search(SearchArgs(query=args.conceptual_query, sources=None, limit_per_source=30, offset=0, family=args.family), deadline)
        if name == "compare_articles":
            from .argumentation import article_reading_status, comparison_source_signature, relation_current, refresh_relations, argument_packet, bounded_argument_packet
            ids = list(dict.fromkeys(args.article_ids))
            from .scientific_reading import comparison_ready
            ledger = self.state.get("scientific_reading", {})
            if not ledger.get("blocked_reason") and any(t["article_id"] in ids and not comparison_ready(t)
                    for t in ledger.get("tasks", [])):
                raise ValueError("Lectures scientifiques prioritaires encore réalisables : terminer les fenêtres avant la comparaison.")
            refresh_relations(self)
            pending = []
            for relation in args.relations or []:
                row = relation.model_dump()
                if not set(row["article_ids"]) <= set(ids):
                    raise ValueError("Relation hors du périmètre comparé.")
                self.claim_union([{"evidence_ids": row["evidence_ids"]}, *row["experimental_results"]], row["article_ids"])
                if any(self.evidence[eid]["source_fingerprint"] != snapshot(self.registry[self.evidence[eid]["article_id"]])["fingerprint"] for eid in row["evidence_ids"]):
                    raise ValueError("Preuves comparatives anciennes : relire la version actuelle avant de sauvegarder une nouvelle relation.")
                if {self.evidence[eid]["article_id"] for eid in row["evidence_ids"]} != set(row["article_ids"]):
                    raise ValueError("Une relation exige des preuves de chaque publication.")
                if row["kind"] == "conditional_superiority" and row["comparable_protocols"] is not True:
                    raise ValueError("Une supériorité expérimentale exige des protocoles comparables explicitement examinés.")
                if row["kind"] == "conditional_superiority" and (not row["experimental_results"] or
                        any(self.evidence[eid]["basis"] != "fulltext" for eid in row["evidence_ids"]) or
                        any(not (self.registry[aid].analysis.get("experimental_setup") or self.registry[aid].analysis.get("validation_protocol")) for aid in row["article_ids"])):
                    raise ValueError("Une supériorité exige des résultats et protocoles documentés dans les textes intégraux.")
                for claim in [{"statement": row["statement"], "evidence_ids": row["evidence_ids"]}, *row["experimental_results"]]:
                    numbers = re.findall(r"(?<![\w.])\d+(?:[.,]\d+)?", claim["statement"])
                    quotes = " ".join(self.evidence[eid]["exact_quote"] for eid in claim["evidence_ids"])
                    supported = {value.replace(",", ".") for value in re.findall(r"(?<![\w.])\d+(?:[.,]\d+)?", quotes)}
                    if any(value.replace(",", ".") not in supported for value in numbers):
                        raise ValueError("Résultat numérique comparatif absent des preuves citées.")
                signature = comparison_source_signature(self.registry, row["article_ids"])
                key = json.dumps(row, sort_keys=True, ensure_ascii=False)
                old = next((r for r in [*self.state.get("scientific_relations", []), *pending]
                    if relation_current(self, r) and r.get("source_signature") == signature and
                    json.dumps({k: r[k] for k in row}, sort_keys=True, ensure_ascii=False) == key), None)
                if not old:
                    pending.append({"id": "relation_"+uuid.uuid4().hex, **row, "source_signature": signature,
                        "stale": False, "comparison_partial": any(article_reading_status(self.state, self.registry[aid])["partial"] for aid in row["article_ids"])})
            if args.conclusions is not None:
                claims = [c.model_dump() for c in args.conclusions]
                self.claim_union(claims, ids)
                self.state.setdefault("comparisons", []).append({"article_ids": ids, "context": args.research_context, "conclusions": claims})
            self.state.setdefault("scientific_relations", []).extend(pending)
            return {"matrix": [{**compact(self.store.article(aid), 900), "reading_status": article_reading_status(self.state, self.registry[aid])} for aid in ids], "research_context": args.research_context,
                "comparison_partial": any(article_reading_status(self.state, self.registry[aid])["partial"] for aid in ids),
                "packet": bounded_argument_packet(argument_packet(self.state, self, ids, claim_offsets=args.claim_offset_map), min(14000, self.config.max_context_chars//3)),
                "scientific_relations": [r for r in self.state.get("scientific_relations", []) if set(r["article_ids"]) <= set(ids) and relation_current(self, r)],
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
        if name == "record_consultant_decisions":
            from .decisions import apply_decision, apply_order, consultant_source
            source = consultant_source(self.state, args.source_message_id, args.exact_quote)
            ids = [d.article_id for d in args.decisions] + list(args.order or [])
            for aid in ids:
                self.store.article(aid)
            def set_flags(aid, selected, override):
                self.registry[aid].selected, self.registry[aid].selection_override = selected, override
            rows = [apply_decision(self.state, d.article_id, d.decision, set_flags,
                verified=self.registry[d.article_id].verified, origin="consultant_chat", source=source)
                for d in args.decisions]
            if args.order:
                apply_order(self.state, args.order, origin="consultant_chat", source=source)
            self.save()
            return {"saved": True, "decisions": rows, "recommended_article_ids":
                self.state.get("evaluation", {}).get("recommended_article_ids", []),
                "held_article_ids": self.state.get("held_article_ids", []),
                "guidance": "Analyses et preuves conservées. Une acceptation ne valide aucune affirmation non vérifiée ni une éligibilité CIR."}
        if name == "record_candidate_decisions":
            for decision in args.decisions:
                a = self.store.article(decision.article_id)
                self.store.examined_article(a.id)
                a.assessment = {**decision.model_dump(), "basis": a.analysis_basis or ("abstract" if a.abstract else "metadata")}
                self.state.setdefault("candidate_decisions", []).append(a.assessment)
            if args.finalist_ids is not None:
                for aid in args.finalist_ids:
                    a = self.store.article(aid)
                    if a.selection_override is False:
                        raise ValueError("Un article retiré par le consultant ne peut pas être finaliste actif.")
                    if aid not in self.store.examined or a.assessment.get("decision") == "reject":
                        raise ValueError("Un finaliste doit avoir été effectivement examiné et non rejeté.")
                # The consultant's decisions prevail over a revised model ranking:
                # accepted works stay, held works wait, the requested order leads.
                held = set(self.state.get("held_article_ids", []))
                accepted = [aid for aid, a in self.registry.items() if a.selection_override is True]
                order = [aid for aid in self.state.get("consultant_order", []) if aid in self.registry
                    and self.registry[aid].selection_override is not False and aid not in held]
                finalists = [aid for aid in dict.fromkeys([*args.finalist_ids, *accepted]) if aid not in held]
                finalists = [*[aid for aid in order if aid in finalists], *[aid for aid in finalists if aid not in order]]
                self.state["evaluation"] = {"recommended_article_ids": finalists,
                    "covered_families": args.covered_families, "remaining_axes": args.remaining_axes,
                    "ranking_origin": "conversation_model_scientific_comparison"}
            self.save()
            result = {"saved": True, "evaluation": self.state.get("evaluation", {}), "examined_total": len(self.store.examined)}
            unseen = [aid for aid in self.turn_article_ids if aid in self.registry and aid not in self.store.exposed
                and aid not in self.store.examined and self.registry[aid].selection_override is not False]
            if unseen:
                # Visibility only: the model alone judges whether any of them matters.
                result["unreviewed_turn_candidates"] = [{"id": aid, "title": self.registry[aid].title[:140],
                    "year": self.registry[aid].year} for aid in unseen[:30]]
                result["unreviewed_turn_candidates_total"] = len(unseen)
            finalists = self.state.get("evaluation", {}).get("recommended_article_ids", []) if args.finalist_ids is not None else []
            limited = [{"id": aid, "access_status": self.registry[aid].access_status} for aid in finalists
                if aid in self.registry and not self.registry[aid].fulltext.get("text") and not self.registry[aid].pdf_url
                and self.registry[aid].publisher_access != "open_access" and not self.registry[aid].fulltext.get("attempts")]
            if limited:
                result["finalists_without_known_fulltext"] = limited
            if unseen or limited:
                result["selection_guidance"] = ("Avant les lectures longues : les candidats non présentés de ce message "
                    "peuvent être consultés par get_article/search_collected_articles ; un finaliste sans texte intégral connu "
                    "ne sera analysé que sur son abstract si aucune version accessible n'est trouvée. "
                    "Confirmer ou réviser les finalistes avec record_candidate_decisions.")
            return result
        if name == "update_research_state":
            memory = args.model_dump(exclude_none=True)
            scope = memory.pop("scientific_scope", None)
            if scope is not None:
                scope = ScientificScope.model_validate(scope).model_dump(mode="json")
                if scope != self.state.get("scientific_scope"):
                    previous_scope=self.state.get("scientific_scope",{})
                    material_change={k:v for k,v in scope.items() if k!="work_type"}!={k:v for k,v in previous_scope.items() if k!="work_type"}
                    self.state.setdefault("scope_history", []).append(copy.deepcopy(self.state.get("scientific_scope", {})))
                    self.state["scientific_scope"] = scope
                    if material_change:
                        self.state.pop("literature_coverage_review", None)
                        self.state.setdefault("document", {})["review_stale"] = True
                    if material_change and self.state.get("outline",{}).get("sections"):
                        plan=self.state["outline"]
                        plan.pop("approval",None); plan["status"]="modified"
                        plan["plan_revision"]=plan.get("plan_revision",plan["version"])+1
                        plan["version"]+=1
                        from .manuscript import save_document_revision
                        save_document_revision(self.state)
            if any(len(s) > 2500 for value in memory.values() if isinstance(value, list) for s in value):
                raise ValueError("Élément de mémoire trop long.")
            self.state.setdefault("memory_history", []).append(self.state.get("research_state", {}))
            self.state["research_state"] = {**self.state.get("research_state", {}), **memory, "origin": "conversation_model", "cir_eligibility": "not_established"}
            self.state["summary"] = ("Sujet : " + memory["topic"] + "\nDécisions : " + "; ".join(memory["consultant_decisions"]))[:6000]
            return {"saved": True}
        if name == "review_literature_coverage":
            if args.action == "save":
                from datetime import date
                records = copy.deepcopy(self.state.get("availability_records", {}))
                for record in args.available_dates:
                    date.fromisoformat(record.date)
                    self.claim_union([{"evidence_ids": [record.evidence_id]}], [record.article_id])
                    proof = self.evidence[record.evidence_id]
                    if record.source_url != proof["source_url"] or record.date not in proof["exact_quote"]:
                        raise ValueError("La date exacte et sa provenance doivent figurer dans la preuve consultée.")
                    row = record.model_dump()
                    if row not in records.setdefault(record.article_id, []):
                        records[record.article_id].append(row)
                for aid in args.foundational_article_ids:
                    self.store.article(aid)
                self.state["availability_records"] = records
                self.state["literature_coverage_review"] = {**args.model_dump(exclude={"available_dates", "action"}),
                    "scope": temporal_scope(self.state), "origin": "conversation_model_scientific_assessment"}
            return literature_coverage(self.state, self.registry)
        if name == "finalize_response":
            return self.finalize(args, deadline)
        raise ValueError("Outil inconnu.")
