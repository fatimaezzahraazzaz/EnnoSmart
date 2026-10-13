# -*- coding: utf-8 -*-
from __future__ import annotations

"""V167 — LangGraph query-planning workflow for EnnoScholar.

Goals
-----
* Never confuse "0 query" with "0 scientific article".
* Keep EnnoSmart's central LLMClient as the only model/budget gateway.
* Validate structured LLM output with Pydantic v2.
* Orchestrate plan -> validate -> repair -> fallback with LangGraph when installed.
* Remain fully domain-agnostic: no project/customer/scientific-domain vocabulary.
* Preserve the V166 provider adapters, topic-drift and rescue behaviour.
* Persist an explicit query_workflow report into ScholarRun.raw_result_json.

LangGraph is optional at import time for fault tolerance. In production the installer
adds it to requirements and the workflow reports ``framework=langgraph``. If the
package is temporarily unavailable, a built-in state-machine executes the same
nodes and reports ``framework=builtin_fail_safe`` instead of breaking EnnoScholar.
"""

import contextlib
import json
import os
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, TypedDict

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from . import scientific_query_planner as _legacy

WORKFLOW_VERSION = "v167_6_adaptive_recall_50_corpus"
PLANNER_VERSION = WORKFLOW_VERSION

STATUS_PLANNING = "QUERY_PLANNING"
STATUS_REPAIR = "QUERY_REPAIR"
STATUS_READY = "QUERY_READY"
STATUS_FAILED = "QUERY_PLANNING_FAILED"


# ---------------------------------------------------------------------------
# Typed contracts
# ---------------------------------------------------------------------------

class EvidenceConcept(BaseModel):
    model_config = ConfigDict(extra="forbid")
    term_en: str = Field(min_length=2, max_length=160)
    source_phrase: str = Field(default="", max_length=220)

    @field_validator("term_en", "source_phrase")
    @classmethod
    def _clean_text(cls, value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()


class LocalIdentifier(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str = Field(min_length=1, max_length=100)
    source_phrase: str = Field(default="", max_length=220)


class AmbiguityResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_term: str = Field(min_length=1, max_length=100)
    resolved_en: str = Field(min_length=2, max_length=160)
    source_phrase: str = Field(default="", max_length=220)


class ScientificRolePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain_anchors: List[EvidenceConcept] = Field(default_factory=list)
    scientific_object: List[EvidenceConcept] = Field(default_factory=list)
    phenomena: List[EvidenceConcept] = Field(default_factory=list)
    independent_variables: List[EvidenceConcept] = Field(default_factory=list)
    response_variables: List[EvidenceConcept] = Field(default_factory=list)
    operating_conditions: List[EvidenceConcept] = Field(default_factory=list)
    methods: List[EvidenceConcept] = Field(default_factory=list)
    validation_concepts: List[EvidenceConcept] = Field(default_factory=list)
    local_identifiers: List[LocalIdentifier] = Field(default_factory=list)
    ambiguities: List[AmbiguityResolution] = Field(default_factory=list)

    @model_validator(mode="after")
    def _dedupe(self) -> "ScientificRolePlan":
        for field_name in (
            "domain_anchors", "scientific_object", "phenomena", "independent_variables",
            "response_variables", "operating_conditions", "methods",
            "validation_concepts",
        ):
            rows = getattr(self, field_name)
            seen = set()
            deduped = []
            for row in rows:
                key = _legacy._norm(row.term_en)
                if key and key not in seen:
                    seen.add(key)
                    deduped.append(row)
            setattr(self, field_name, deduped[:8])
        return self


class QueryItem(BaseModel):
    model_config = ConfigDict(extra="allow")
    query: str = Field(min_length=3, max_length=260)
    kind: str = Field(default="v167_auto", max_length=100)
    family: str = Field(default="auto", max_length=100)
    planner_version: str = WORKFLOW_VERSION


class WorkflowReport(BaseModel):
    model_config = ConfigDict(extra="allow")
    version: str = WORKFLOW_VERSION
    framework: str
    status: str
    search_allowed: bool
    planning_mode: str
    attempts: int = 0
    query_count: int = 0
    validation_errors: List[str] = Field(default_factory=list)
    llm_attempts: List[Dict[str, Any]] = Field(default_factory=list)
    consultant_message: str


class QueryWorkflowState(TypedDict, total=False):
    intent: Dict[str, Any]
    evidence: str
    evidence_units: Dict[str, str]
    evidence_fingerprint: str
    explicit_local_ids: List[str]
    raw_payload: Optional[Dict[str, Any]]
    plan: Dict[str, Any]
    queries: List[Dict[str, Any]]
    attempts: int
    validation_errors: List[str]
    validation_warnings: List[str]
    llm_attempts: List[Dict[str, Any]]
    llm_retry_reason: str
    status: str
    planning_mode: str
    consultant_message: str
    framework: str


# ---------------------------------------------------------------------------
# Observability — Langfuse is deliberately optional.
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _observation(name: str, metadata: Optional[Dict[str, Any]] = None):
    enabled = str(os.getenv("ENNOSCHOLAR_LANGFUSE_ENABLED", "0") or "0").lower() in {
        "1", "true", "yes", "on", "oui"
    }
    if not enabled:
        yield None
        return
    try:
        from langfuse import get_client  # type: ignore
        langfuse = get_client()
        with langfuse.start_as_current_observation(
            as_type="span",
            name=name,
            metadata=metadata or {},
        ) as span:
            yield span
    except Exception:
        # Observability must never break the business flow.
        yield None


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _as_plan_dict(plan: ScientificRolePlan) -> Dict[str, Any]:
    payload = plan.model_dump(mode="python")
    payload["variables"] = (
        list(payload.get("independent_variables") or [])
        + list(payload.get("response_variables") or [])
    )[:10]
    payload["constraints"] = list(payload.get("operating_conditions") or [])[:8]
    return payload


def _coerce_role_plan(payload: Mapping[str, Any]) -> ScientificRolePlan:
    # Legacy V166 exposes compatibility aliases ``variables`` and ``constraints``.
    # They are derived fields, not part of the strict V167 contract, so remove
    # them before Pydantic validation instead of rejecting an otherwise valid plan.
    allowed = {
        "domain_anchors", "scientific_object", "phenomena", "independent_variables",
        "response_variables", "operating_conditions", "methods",
        "validation_concepts", "local_identifiers", "ambiguities",
    }
    data = {k: v for k, v in dict(payload or {}).items() if k in allowed}
    return ScientificRolePlan.model_validate(data)


def _plan_has_core(plan: Mapping[str, Any]) -> bool:
    return bool(plan.get("scientific_object")) and bool(
        plan.get("independent_variables")
        or plan.get("response_variables")
        or plan.get("phenomena")
        or plan.get("methods")
    )


def _build_queries(plan: Mapping[str, Any]) -> List[Dict[str, Any]]:
    rows = _legacy._build_query_families(plan)
    out: List[Dict[str, Any]] = []
    seen = set()
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        q = _legacy._clean(row.get("query"), 240)
        key = _legacy._norm(q)
        if not q or not key or key in seen:
            continue
        if not _legacy.query_is_safe(q, plan):
            continue
        seen.add(key)
        item = dict(row)
        item["query"] = q
        item["planner_version"] = WORKFLOW_VERSION
        item["kind"] = str(item.get("kind") or "v167_auto").replace("v166_3_", "v167_")
        out.append(item)
    return out[:8]


def _quality_errors(plan: Mapping[str, Any], queries: Sequence[Mapping[str, Any]]) -> List[str]:
    errors: List[str] = []
    if not plan.get("scientific_object"):
        errors.append("missing_scientific_object")
    if not (
        plan.get("independent_variables")
        or plan.get("response_variables")
        or plan.get("phenomena")
        or plan.get("methods")
    ):
        errors.append("missing_problem_axis")
    if not queries:
        errors.append("zero_safe_queries")

    for row in queries:
        q = str(row.get("query") or "")
        if not _legacy.query_is_safe(q, plan):
            errors.append(f"unsafe_query:{q[:120]}")

    # Local identifiers must never dominate transferable scientific queries.
    local_ids = [
        str(row.get("value") or "")
        for row in plan.get("local_identifiers") or []
        if isinstance(row, Mapping)
    ]
    for row in queries:
        nq = " " + _legacy._norm(row.get("query")) + " "
        for local in local_ids:
            nl = _legacy._norm(local)
            if nl and re.search(rf"(?<![a-z0-9]){re.escape(nl)}(?![a-z0-9])", nq):
                errors.append(f"local_identifier_in_query:{local}")
    return list(dict.fromkeys(errors))


def _validated_plan_from_raw(raw_payload: Optional[Mapping[str, Any]], evidence: str) -> Tuple[Dict[str, Any], List[str]]:
    validation_errors: List[str] = []
    try:
        filtered = _legacy._validate_llm_payload(raw_payload, evidence)
        plan_model = _coerce_role_plan(filtered)
        plan = _as_plan_dict(plan_model)
    except ValidationError as exc:
        validation_errors.append(f"pydantic:{exc.errors(include_url=False)}")
        plan = _legacy._merge_legacy_role_fields({
            field: [] for field in _legacy._EXTRACT_ROLE_FIELDS
        } | {"local_identifiers": [], "ambiguities": []})
    except Exception as exc:
        validation_errors.append(f"validation:{type(exc).__name__}:{exc}")
        plan = _legacy._merge_legacy_role_fields({
            field: [] for field in _legacy._EXTRACT_ROLE_FIELDS
        } | {"local_identifiers": [], "ambiguities": []})

    queries = _build_queries(plan)
    validation_errors.extend(_quality_errors(plan, queries))
    return plan, list(dict.fromkeys(validation_errors))


# ---------------------------------------------------------------------------
# LLM planning and repair
# ---------------------------------------------------------------------------

def _call_repair_llm(
    *,
    evidence: str,
    explicit_local_ids: Sequence[str],
    previous_payload: Optional[Mapping[str, Any]],
    errors: Sequence[str],
    attempt: int,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    Client = _legacy._load_llm_client_class()
    if Client is None:
        return None, {"used": False, "ok": False, "reason": "llm_client_unavailable"}

    prompt = f"""
Tu répares un plan de recherche scientifique EnnoScholar qui a été rejeté par le contrat de validation.
Le système est multi-domaines. Ne connais aucun projet à l'avance et n'ajoute aucun vocabulaire métier absent des preuves.

OBJECTIF
Produire un JSON conforme au schéma demandé permettant au code de construire au moins une requête bibliographique scientifique fiable.

ERREURS DU CONTRAT
{json.dumps(list(errors), ensure_ascii=False)}

PLAN PRÉCÉDENT REJETÉ
{json.dumps(previous_payload or {}, ensure_ascii=False)[:5000]}

RÈGLES OBLIGATOIRES
1. Utilise uniquement PREUVES.
2. Pour chaque source_phrase, COPIE un court extrait verbatim de PREUVES (2 à 12 mots), sans paraphrase.
3. scientific_object doit être le système/composant/procédé réellement étudié, jamais "impact", "incertitude", "performance" ou une simple grandeur.
4. independent_variables = paramètres comparés ou variés.
5. response_variables = grandeurs observées/mesurées en réponse.
6. operating_conditions = conditions de fonctionnement ou contraintes imposées.
7. phenomena = comportement/mécanisme scientifique observé.
8. Les termes anglais doivent être directement recherchables et conserver le contexte technique de la grandeur.
9. Sépare les noms locaux de machine/projet des concepts scientifiques transférables.
10. Résous les mots ambigus selon leurs voisins techniques.
11. Ignore true/false/null, UUID, sessions, ids, métadonnées JSON.
12. N'invente aucune requête : retourne seulement les rôles structurés.
13. Si un rôle n'est pas prouvé, retourne [].

IDENTIFIANTS LOCAUX SUSPECTÉS
{json.dumps(list(explicit_local_ids), ensure_ascii=False)}

PREUVES
---
{evidence}
---
""".strip()

    try:
        client = Client()
        raw = client.generate(
            prompt=prompt,
            temperature=0.0,
            max_output_tokens=1400,
            retries=1,
            json_mode=True,
            response_schema=_legacy._planner_schema(),
            request_name=f"ennoscholar:scientific_query_repair:v167:attempt_{attempt}",
        )
        text = str(raw or "").strip()
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S)
        data = json.loads(text)
        meta = getattr(client, "get_last_generation_meta", lambda: {})()
        return data if isinstance(data, dict) else None, {
            "used": True,
            "ok": isinstance(data, dict),
            "request_name": f"ennoscholar:scientific_query_repair:v167:attempt_{attempt}",
            "provider": meta.get("provider"),
            "model": meta.get("model"),
            "total_tokens": meta.get("total_tokens"),
        }
    except Exception as exc:
        return None, {
            "used": True,
            "ok": False,
            "request_name": f"ennoscholar:scientific_query_repair:v167:attempt_{attempt}",
            "error": f"{type(exc).__name__}: {exc}",
        }


# ---------------------------------------------------------------------------
# Graph nodes
# ---------------------------------------------------------------------------

def _node_collect(state: QueryWorkflowState) -> Dict[str, Any]:
    intent = dict(state.get("intent") or {})
    evidence = _legacy._source_evidence(intent)
    explicit_local = _legacy._explicit_local_identifiers(intent, evidence)
    return {
        "evidence": evidence,
        "evidence_fingerprint": _legacy._evidence_fingerprint(intent),
        "explicit_local_ids": explicit_local,
        "status": STATUS_PLANNING,
        "attempts": 0,
        "validation_errors": [],
        "llm_attempts": [],
    }


def _node_plan(state: QueryWorkflowState) -> Dict[str, Any]:
    evidence = state.get("evidence") or ""
    explicit_local = state.get("explicit_local_ids") or []
    raw, meta = _legacy._call_llm_planner(evidence, explicit_local)
    plan, errors = _validated_plan_from_raw(raw, evidence)
    queries = _build_queries(plan)
    errors = list(dict.fromkeys(errors + _quality_errors(plan, queries)))
    ready = not errors and _plan_has_core(plan) and bool(queries)
    return {
        "raw_payload": raw,
        "plan": plan,
        "queries": queries,
        "attempts": 1,
        "validation_errors": errors,
        "llm_attempts": [meta],
        "status": STATUS_READY if ready else STATUS_REPAIR,
        "planning_mode": "llm_evidence_grounded" if ready else "llm_repair_required",
    }


def _node_repair(state: QueryWorkflowState) -> Dict[str, Any]:
    attempt = int(state.get("attempts") or 1) + 1
    raw, meta = _call_repair_llm(
        evidence=state.get("evidence") or "",
        explicit_local_ids=state.get("explicit_local_ids") or [],
        previous_payload=state.get("raw_payload"),
        errors=state.get("validation_errors") or [],
        attempt=attempt,
    )
    plan, errors = _validated_plan_from_raw(raw, state.get("evidence") or "")
    queries = _build_queries(plan)
    errors = list(dict.fromkeys(errors + _quality_errors(plan, queries)))
    ready = not errors and _plan_has_core(plan) and bool(queries)
    return {
        "raw_payload": raw,
        "plan": plan,
        "queries": queries,
        "attempts": attempt,
        "validation_errors": errors,
        "llm_attempts": list(state.get("llm_attempts") or []) + [meta],
        "status": STATUS_READY if ready else STATUS_REPAIR,
        "planning_mode": "llm_repaired" if ready else "llm_repair_failed",
    }


def _node_fallback(state: QueryWorkflowState) -> Dict[str, Any]:
    intent = state.get("intent") or {}
    evidence = state.get("evidence") or ""
    explicit_local = state.get("explicit_local_ids") or []
    try:
        fallback = _legacy._fallback_plan(intent, evidence, explicit_local)
        model = _coerce_role_plan(fallback)
        plan = _as_plan_dict(model)
    except Exception as exc:
        plan = dict(fallback if "fallback" in locals() and isinstance(fallback, dict) else {})
        extra = [f"fallback_validation:{type(exc).__name__}:{exc}"]
    else:
        extra = []
    queries = _build_queries(plan) if plan else []
    errors = list(dict.fromkeys(extra + _quality_errors(plan, queries)))
    ready = not errors and _plan_has_core(plan) and bool(queries)
    return {
        "plan": plan,
        "queries": queries,
        "validation_errors": errors,
        "status": STATUS_READY if ready else STATUS_FAILED,
        "planning_mode": "deterministic_fallback" if ready else "failed_after_repair_and_fallback",
        "consultant_message": (
            "Planification réparée par le fallback déterministe ; la recherche peut être lancée."
            if ready
            else "EnnoScholar n'a pas pu construire une stratégie de recherche suffisamment fiable. "
                 "Aucune base scientifique n'a été interrogée et ce résultat ne signifie pas qu'aucun article n'existe."
        ),
    }


def _node_finalize(state: QueryWorkflowState) -> Dict[str, Any]:
    queries = list(state.get("queries") or [])
    ready = bool(queries) and state.get("status") == STATUS_READY
    return {
        "status": STATUS_READY if ready else STATUS_FAILED,
        "consultant_message": (
            f"Stratégie de recherche validée : {len(queries)} requête(s) scientifique(s) prête(s)."
            if ready
            else "EnnoScholar n'a pas pu valider les requêtes scientifiques ; la recherche n'a pas été lancée."
        ),
    }


def _route_after_plan(state: QueryWorkflowState) -> str:
    return "finalize" if state.get("status") == STATUS_READY else "repair"


def _route_after_repair(state: QueryWorkflowState) -> str:
    if state.get("status") == STATUS_READY:
        return "finalize"
    max_attempts = max(2, min(int(os.getenv("ENNOSCHOLAR_QUERY_PLAN_MAX_ATTEMPTS", "2") or 2), 3))
    if int(state.get("attempts") or 0) < max_attempts:
        return "repair"
    return "fallback"


def _route_after_fallback(state: QueryWorkflowState) -> str:
    return "finalize"


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------

def _run_builtin(initial: QueryWorkflowState) -> QueryWorkflowState:
    state: QueryWorkflowState = dict(initial)
    state.update(_node_collect(state))
    state.update(_node_plan(state))
    while state.get("status") != STATUS_READY:
        max_attempts = max(2, min(int(os.getenv("ENNOSCHOLAR_QUERY_PLAN_MAX_ATTEMPTS", "2") or 2), 3))
        if int(state.get("attempts") or 0) >= max_attempts:
            state.update(_node_fallback(state))
            break
        state.update(_node_repair(state))
    state.update(_node_finalize(state))
    return state


def _run_langgraph(initial: QueryWorkflowState) -> QueryWorkflowState:
    from langgraph.graph import END, START, StateGraph  # type: ignore

    builder = StateGraph(QueryWorkflowState)
    builder.add_node("collect_evidence", _node_collect)
    builder.add_node("plan", _node_plan)
    builder.add_node("repair", _node_repair)
    builder.add_node("fallback", _node_fallback)
    builder.add_node("finalize", _node_finalize)

    builder.add_edge(START, "collect_evidence")
    builder.add_edge("collect_evidence", "plan")
    builder.add_conditional_edges(
        "plan",
        _route_after_plan,
        {"repair": "repair", "finalize": "finalize"},
    )
    builder.add_conditional_edges(
        "repair",
        _route_after_repair,
        {"repair": "repair", "fallback": "fallback", "finalize": "finalize"},
    )
    builder.add_conditional_edges(
        "fallback",
        _route_after_fallback,
        {"finalize": "finalize"},
    )
    builder.add_edge("finalize", END)
    graph = builder.compile()
    result = graph.invoke(initial)
    return dict(result)


def run_query_workflow(intent: Mapping[str, Any]) -> Dict[str, Any]:
    initial: QueryWorkflowState = {"intent": dict(intent or {})}
    framework = "builtin_fail_safe"
    with _observation(
        "ennoscholar.query_workflow",
        {"version": WORKFLOW_VERSION, "verrou_id": intent.get("verrou_id")},
    ):
        try:
            import langgraph  # noqa: F401  # type: ignore
            framework = "langgraph"
            state = _run_langgraph(initial)
        except Exception as exc:
            # A missing/broken orchestration package must never turn the entire
            # Scholar run into a 500. The exact same nodes are executed by the
            # fail-safe runner and the deviation is recorded for observability.
            framework = "builtin_fail_safe"
            state = _run_builtin(initial)
            state.setdefault("validation_errors", [])
            state["validation_errors"] = list(state.get("validation_errors") or []) + [
                f"langgraph_fallback:{type(exc).__name__}:{exc}"
            ]

    state["framework"] = framework
    status = str(state.get("status") or STATUS_FAILED)
    queries = list(state.get("queries") or [])
    report = WorkflowReport(
        framework=framework,
        status=status,
        search_allowed=bool(status == STATUS_READY and queries),
        planning_mode=str(state.get("planning_mode") or "unknown"),
        attempts=int(state.get("attempts") or 0),
        query_count=len(queries),
        validation_errors=list(state.get("validation_errors") or []),
        llm_attempts=list(state.get("llm_attempts") or []),
        consultant_message=str(state.get("consultant_message") or ""),
    ).model_dump(mode="python")

    plan = dict(state.get("plan") or {})
    plan.update({
        "planner_version": WORKFLOW_VERSION,
        "workflow_status": status,
        "planning_mode": report["planning_mode"],
        "evidence_fingerprint": state.get("evidence_fingerprint"),
        "evidence_chars": len(state.get("evidence") or ""),
        "queries": queries,
        "query_count": len(queries),
        "workflow": report,
    })
    return {"plan": plan, "queries": queries, "workflow": report}


# ---------------------------------------------------------------------------
# Public compatibility API used by query_builder.py and scholar_agent.py
# ---------------------------------------------------------------------------

def _terms(rows: Sequence[Mapping[str, Any]]) -> List[str]:
    return _legacy._terms(rows)


def _flatten_plan_terms(plan: Mapping[str, Any]) -> List[str]:
    values: List[str] = []
    for field in _legacy._ROLE_FIELDS:
        values.extend(_terms(plan.get(field) or []))
    return _legacy._unique(values, 24)


def attach_query_plan(intent: Mapping[str, Any], max_queries: int = 14) -> Dict[str, Any]:
    out = dict(intent or {})
    previous = out.get("query_workflow") if isinstance(out.get("query_workflow"), Mapping) else None
    previous_plan = out.get("scientific_query_plan") if isinstance(out.get("scientific_query_plan"), Mapping) else None
    if (
        previous
        and previous_plan
        and previous.get("version") == WORKFLOW_VERSION
        and previous_plan.get("evidence_fingerprint") == _legacy._evidence_fingerprint(out)
    ):
        result = {
            "plan": dict(previous_plan),
            "queries": list(previous_plan.get("queries") or []),
            "workflow": dict(previous),
        }
    else:
        result = run_query_workflow(out)

    plan = result["plan"]
    queries = list(result["queries"])[: max(1, int(max_queries or 14))]
    out["scientific_query_plan"] = plan
    out["query_workflow"] = result["workflow"]
    out["search_queries"] = queries
    out["query_builder_version"] = WORKFLOW_VERSION

    objects = _terms(plan.get("scientific_object") or [])
    phenomena = _terms(plan.get("phenomena") or [])
    independent = _terms(plan.get("independent_variables") or [])
    response = _terms(plan.get("response_variables") or [])
    methods = _terms(plan.get("methods") or [])
    operating = _terms(plan.get("operating_conditions") or [])
    core = _legacy._unique(objects + independent + response + phenomena, 10)

    out["core_concepts"] = core
    out["primary_core_concepts"] = _legacy._unique(
        objects[:1] + independent[:1] + response[:1], 3
    ) or core[:3]
    out["method_anchors"] = methods
    out["phenomenon_anchors"] = _legacy._unique(
        phenomena + response[:2] + operating[:2], 10
    )
    out["project_tool_terms"] = []
    out["local_names"] = [
        str(row.get("value") or "")
        for row in plan.get("local_identifiers") or []
        if isinstance(row, Mapping)
    ]
    out["query_planner_terms"] = _flatten_plan_terms(plan)
    return out


def build_queries(intent: Mapping[str, Any], max_queries: int = 14) -> List[Dict[str, Any]]:
    return attach_query_plan(intent, max_queries=max_queries).get("search_queries", [])


def query_is_safe(query: str, intent_or_plan: Mapping[str, Any], *, allow_local: bool = False) -> bool:
    if not isinstance(intent_or_plan, Mapping):
        return False
    plan = (
        intent_or_plan.get("scientific_query_plan")
        if isinstance(intent_or_plan.get("scientific_query_plan"), Mapping)
        else intent_or_plan
    )
    return _legacy.query_is_safe(query, plan, allow_local=allow_local)


def _query_similarity(a: str, b: str) -> float:
    return _legacy._query_similarity(a, b)


def select_queries(queries: Sequence[Any], intent: Mapping[str, Any], max_queries: int = 3) -> List[Dict[str, Any]]:
    enriched = attach_query_plan(intent)
    plan = enriched["scientific_query_plan"]
    candidates: List[Dict[str, Any]] = []
    seen = set()
    for raw in queries or enriched.get("search_queries") or []:
        item = dict(raw) if isinstance(raw, Mapping) else {"query": str(raw), "kind": "external"}
        q = _legacy._clean(item.get("query"), 240)
        nq = _legacy._norm(q)
        if not nq or nq in seen or not query_is_safe(q, plan):
            continue
        seen.add(nq)
        item["query"] = q
        item.setdefault("family", str(item.get("kind") or "").replace("v167_", "") or "external")
        item["selection_score"] = {
            "direct": 1.00,
            "variable_relation": 0.96,
            "operating_conditions": 0.93,
            "experimental": 0.90,
            "mechanism": 0.88,
            "secondary_axis": 0.80,
        }.get(str(item.get("family") or ""), 0.60)
        candidates.append(item)

    candidates.sort(key=lambda row: float(row.get("selection_score") or 0.0), reverse=True)
    selected: List[Dict[str, Any]] = []
    families = set()
    for item in candidates:
        if len(selected) >= max(1, int(max_queries or 3)):
            break
        family = str(item.get("family") or item.get("kind") or "")
        if family in families:
            continue
        if any(_query_similarity(item["query"], other["query"]) >= 0.72 for other in selected):
            continue
        families.add(family)
        selected.append(item)
    return selected


def adapt_query_for_provider(query: str, provider: str, intent: Optional[Mapping[str, Any]] = None) -> str:
    provider_n = _legacy._norm(provider)
    max_words = 11
    if provider_n in {"crossref", "core", "zenodo"}:
        max_words = 8
    elif provider_n in {"github", "huggingface"}:
        max_words = 9
    q = _legacy._query_words([query], max_words=max_words)
    if intent is not None:
        enriched = attach_query_plan(intent)
        if not query_is_safe(q, enriched["scientific_query_plan"]):
            return ""
    return q


def _article_text(article: Mapping[str, Any]) -> str:
    return _legacy._clean(" ".join([
        str(article.get("title") or ""),
        str(article.get("abstract") or article.get("summary") or article.get("tldr") or ""),
    ]), 4000)


def assess_topic_drift(intent: Mapping[str, Any], papers: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    enriched = attach_query_plan(intent)
    plan = enriched["scientific_query_plan"]
    objects = _terms(plan.get("scientific_object") or [])
    other = _legacy._unique(
        _terms(plan.get("phenomena") or [])
        + _terms(plan.get("variables") or [])
        + _terms(plan.get("methods") or [])
        + _terms(plan.get("constraints") or []),
        16,
    )
    rows = [p for p in papers or [] if isinstance(p, Mapping) and p.get("title")][:30]
    if not rows or not objects:
        return {"checked": False, "triggered": False, "reason": "insufficient_evidence_or_results"}

    def phrase_hit(text_tokens: set[str], phrase: str) -> bool:
        pt = {_legacy._norm(x) for x in _legacy._tokens(phrase) if len(_legacy._norm(x)) >= 3}
        return bool(pt) and len(pt & text_tokens) / max(1, len(pt)) >= 0.67

    aligned = 0
    examples = []
    for paper in rows:
        tt = {_legacy._norm(x) for x in _legacy._tokens(_article_text(paper)) if len(_legacy._norm(x)) >= 3}
        object_hit = any(phrase_hit(tt, obj) for obj in objects)
        support_hit = any(phrase_hit(tt, term) for term in other) if other else object_hit
        ok = object_hit and support_hit
        aligned += int(ok)
        if not ok and len(examples) < 5:
            examples.append(_legacy._clean(paper.get("title"), 180))

    ratio = aligned / max(1, len(rows))
    threshold = float(os.getenv("ENNOSCHOLAR_QUERY_DRIFT_MIN_ALIGNMENT", "0.35") or 0.35)
    return {
        "checked": True,
        "results_checked": len(rows),
        "aligned_count": aligned,
        "alignment_ratio": round(ratio, 4),
        "threshold": threshold,
        "triggered": bool(len(rows) >= 5 and ratio < threshold),
        "offtopic_examples": examples,
    }


def build_feedback_queries(
    intent: Mapping[str, Any],
    papers: Sequence[Mapping[str, Any]],
    existing_queries: Sequence[Any],
    max_queries: int = 2,
) -> Tuple[List[str], Dict[str, Any]]:
    drift = assess_topic_drift(intent, papers)
    if not drift.get("triggered"):
        return [], {**drift, "feedback_queries": []}

    enriched = attach_query_plan(intent)
    plan = enriched["scientific_query_plan"]
    obj = _terms(plan.get("scientific_object") or [])
    phen = _terms(plan.get("phenomena") or [])
    independent = _terms(plan.get("independent_variables") or [])
    response = _terms(plan.get("response_variables") or [])
    methods = _terms(plan.get("methods") or [])
    operating = _terms(plan.get("operating_conditions") or [])
    validation = _terms(plan.get("validation_concepts") or [])

    raw_candidates = [
        _legacy._query_words([obj[:1], independent[:1], response[:1], methods[:1], validation[:1]], max_words=12),
        _legacy._query_words([obj[:1], response[:1], operating[:1], independent[:1], phen[:1]], max_words=12),
    ]
    existing = [
        _legacy._clean(x.get("query") if isinstance(x, Mapping) else x, 240)
        for x in existing_queries or []
    ]
    out: List[str] = []
    for q in raw_candidates:
        if not q or not query_is_safe(q, plan):
            continue
        if any(_query_similarity(q, old) >= 0.78 for old in existing + out):
            continue
        out.append(q)
        if len(out) >= max(1, int(max_queries or 2)):
            break
    return out, {**drift, "feedback_queries": out, "planner_version": WORKFLOW_VERSION}


def build_rescue_queries(intent: Mapping[str, Any], existing_queries: Sequence[Any], max_queries: int = 6) -> List[str]:
    enriched = attach_query_plan(intent)
    plan = enriched["scientific_query_plan"]
    base = list(plan.get("queries") or [])
    selected = select_queries(base, enriched, max_queries=max_queries)
    existing = [
        _legacy._clean(x.get("query") if isinstance(x, Mapping) else x, 240)
        for x in existing_queries or []
    ]
    out: List[str] = []
    for item in selected:
        q = _legacy._clean(item.get("query"), 240)
        if q and not any(_query_similarity(q, old) >= 0.78 for old in existing + out):
            out.append(q)
    return out[:max(1, int(max_queries or 6))]


# =============================================================================
# V167.1 — evidence-reference contract + partial field recovery
# =============================================================================
# Root cause fixed from Run 98:
# - a valid LLM call could be entirely discarded when source_phrase was not an
#   exact enough verbatim substring;
# - repair restarted from an empty plan instead of preserving valid roles;
# - deterministic fallback could reuse polluted V161 role strings;
# - any mixed alpha+digit token in evidence could be misclassified as local id.
#
# V167.1 therefore binds concepts to stable evidence IDs, preserves every valid
# role across attempts, repairs only missing/invalid parts, and never reuses the
# legacy polluted intent as a scientific fallback.

_V1671_ROLE_FIELDS = (
    "domain_anchors",
    "scientific_object",
    "phenomena",
    "independent_variables",
    "response_variables",
    "operating_conditions",
    "methods",
    "validation_concepts",
)


def _v1671_split_evidence(evidence: str) -> Dict[str, str]:
    text = _legacy._clean(evidence, 12000)
    # Remove long metadata-like underscore/hash tokens without removing physical
    # quantities such as 300bar or model names in natural text.
    text = re.sub(r"\b(?=\S{18,}\b)(?=\S*_)[A-Za-z0-9À-ÿ_.-]+\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return {}

    # Sentence-like units are much more robust than asking the model to copy an
    # arbitrary verbatim span. Each concept cites E1/E2/... and the code restores
    # the exact source text itself.
    raw_units = re.split(r"(?<=[.!?;:])\s+|\n+", text)
    units: List[str] = []
    for raw in raw_units:
        value = _legacy._clean(raw, 420)
        if len(value) < 8:
            continue
        if value not in units:
            units.append(value)
        if len(units) >= 40:
            break

    if not units:
        units = [text[:420]]
    return {f"E{i+1}": value for i, value in enumerate(units)}


def _v1671_safe_explicit_local_identifiers(intent: Mapping[str, Any], evidence: str) -> List[str]:
    candidates: List[str] = []

    # Explicit upstream fields are authoritative when present.
    for key in ("project_tool_terms", "local_identifiers"):
        value = intent.get(key)
        if isinstance(value, list):
            candidates.extend(
                str(row.get("value") if isinstance(row, Mapping) else row)
                for row in value
            )

    # Auto-detection is deliberately title-only. This keeps TGM100-like model
    # identifiers while avoiding measurements (300bar) and file/hash ids found in
    # evidence passages.
    title = str(intent.get("verrou_title") or intent.get("original_title") or "")
    candidates.extend(_legacy._MIXED_ID_RE.findall(title))

    out: List[str] = []
    for item in candidates:
        item = _legacy._clean(item, 80)
        ni = _legacy._norm(item)
        if not ni or ni in _legacy._ADMIN_NOISE or ni in _legacy._LITERAL_NOISE:
            continue
        if not _legacy._appears_in_evidence(item, evidence):
            continue
        out.append(item)
    return _legacy._unique(out, 12)


def _v1671_schema() -> Dict[str, Any]:
    concept = {
        "type": "object",
        "properties": {
            "term_en": {"type": "string"},
            "evidence_ids": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 3,
            },
        },
        "required": ["term_en", "evidence_ids"],
        "additionalProperties": False,
    }
    local = {
        "type": "object",
        "properties": {
            "value": {"type": "string"},
            "evidence_ids": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 3,
            },
        },
        "required": ["value", "evidence_ids"],
        "additionalProperties": False,
    }
    ambiguity = {
        "type": "object",
        "properties": {
            "source_term": {"type": "string"},
            "resolved_en": {"type": "string"},
            "evidence_ids": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 3,
            },
        },
        "required": ["source_term", "resolved_en", "evidence_ids"],
        "additionalProperties": False,
    }
    return {
        "title": "ennoscholar_scientific_query_plan_v167_1",
        "type": "object",
        "properties": {
            **{field: {"type": "array", "items": concept} for field in _V1671_ROLE_FIELDS},
            "local_identifiers": {"type": "array", "items": local},
            "ambiguities": {"type": "array", "items": ambiguity},
        },
        "required": [*_V1671_ROLE_FIELDS, "local_identifiers", "ambiguities"],
        "additionalProperties": False,
    }


def _v1671_evidence_block(units: Mapping[str, str]) -> str:
    return "\n".join(f"[{key}] {value}" for key, value in units.items())


def _v1671_call_llm(
    *,
    evidence: str,
    units: Mapping[str, str],
    explicit_local_ids: Sequence[str],
    attempt: int,
    previous_payload: Optional[Mapping[str, Any]] = None,
    blocking_errors: Sequence[str] = (),
    missing_roles: Sequence[str] = (),
    domain_context: Sequence[str] = (),
    research_objective: str = "",
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    # Backward-compatible test/rolling-deployment hook: if the legacy planner
    # function was explicitly monkeypatched by a caller, honor it. The real
    # production function keeps its original name and is therefore bypassed in
    # favor of the V167.1 evidence-reference contract below.
    legacy_planner = getattr(_legacy, "_call_llm_planner", None)
    if attempt == 1 and callable(legacy_planner) and getattr(legacy_planner, "__name__", "") != "_call_llm_planner":
        return legacy_planner(evidence, explicit_local_ids)

    Client = _legacy._load_llm_client_class()
    if Client is None:
        return None, {"used": False, "ok": False, "reason": "llm_client_unavailable"}

    mode = "initial" if attempt == 1 else "repair"
    previous_text = json.dumps(previous_payload or {}, ensure_ascii=False)[:4500]
    prompt = f"""
Tu construis un plan de recherche bibliographique scientifique multi-domaines.
Le domaine détecté, l'objectif et la section parent servent à désambiguïser le
passage ciblé. Ils ne doivent jamais être ignorés au profit d'une traduction
littérale hors contexte.

MODE: {mode}

RÈGLE CENTRALE DE TRAÇABILITÉ
Les preuves sont numérotées E1, E2, etc. Pour chaque concept, retourne un ou plusieurs evidence_ids existants.
NE RECOPIE PAS les phrases sources : le code récupérera lui-même leur texte exact depuis les IDs.
N'invente jamais un evidence_id.

RÔLES
- domain_anchors: 1 à 3 expressions anglaises recherchables qui nomment le champ
  physique/technique précis commun à toute la section (pas seulement
  "engineering", "model" ou "simulation"). Elles doivent être prouvées par la
  section parent et ne doivent pas répéter scientific_object.
- scientific_object: système, composant, matériau, procédé ou objet réellement étudié.
- independent_variables: paramètres comparés, réglés ou variant dans les essais/calculs.
- response_variables: grandeurs observées, mesurées ou prédites en réponse.
- operating_conditions: conditions de fonctionnement/contraintes imposées.
- phenomena: comportement, mécanisme ou relation scientifique observée.
- methods: essai, mesure, protocole, simulation ou méthode d'analyse explicitement prouvé.
- validation_concepts: comparaison, robustesse, répétabilité ou logique de validation explicitement prouvée.
- local_identifiers: noms locaux de machine/produit/prototype/projet à séparer des concepts transférables.
- ambiguities: terme source ambigu et traduction scientifique correspondant au contexte.

QUALITÉ
1. term_en doit être une expression scientifique anglaise autonome et directement recherchable.
2. Préserve le contexte d'une grandeur: évite "temperature", "flow", "output" seuls si la preuve permet une expression plus précise.
3. Ne mets jamais "impact", "effect", "uncertainty", "severe conditions" comme objet scientifique.
4. Un rôle non prouvé doit être [].
5. Ignore true/false/null, UUID, noms de fichiers, session/run/request IDs et métadonnées JSON.
6. Les identifiants locaux suspectés ne doivent pas être réutilisés comme concepts scientifiques transférables.
7. Retourne le JSON du schéma et rien d'autre.
8. Résous les termes ambigus avec la section complète et le domaine. Par exemple,
   une traduction lexicale n'est pas acceptable si elle change de discipline.
9. Chaque scientific_object et chaque axe doit rester compatible avec au moins
   un domain_anchor. Si ce lien n'est pas prouvé, retourne [] au lieu d'élargir.

DOMAINE DÉTECTÉ (contrainte de routage, pas vocabulaire à recopier aveuglément)
{json.dumps(list(domain_context), ensure_ascii=False)}

OBJECTIF DE LA RECHERCHE (guide de sélection, pas source de faits)
{_legacy._clean(research_objective, 1800)}

IDENTIFIANTS LOCAUX SUSPECTÉS
{json.dumps(list(explicit_local_ids), ensure_ascii=False)}

RÔLES ENCORE MANQUANTS/À RÉPARER
{json.dumps(list(missing_roles), ensure_ascii=False)}

ERREURS BLOQUANTES PRÉCÉDENTES
{json.dumps(list(blocking_errors), ensure_ascii=False)}

PLAN PRÉCÉDENT (peut contenir des rôles déjà valides; ne les dégrade pas)
{previous_text}

PREUVES INDEXÉES
{_v1671_evidence_block(units)}
""".strip()

    request_name = (
        "ennoscholar:scientific_query_planner:v167_6"
        if attempt == 1
        else f"ennoscholar:scientific_query_repair:v167_6:attempt_{attempt}"
    )
    try:
        client = Client()
        raw = client.generate(
            prompt=prompt,
            temperature=0.0,
            max_output_tokens=1400,
            retries=1,
            json_mode=True,
            response_schema=_v1671_schema(),
            request_name=request_name,
        )
        text = str(raw or "").strip()
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S)
        data = json.loads(text)
        meta = getattr(client, "get_last_generation_meta", lambda: {})()
        return data if isinstance(data, dict) else None, {
            "used": True,
            "ok": isinstance(data, dict),
            "request_name": request_name,
            "provider": meta.get("provider"),
            "model": meta.get("model"),
            "total_tokens": meta.get("total_tokens"),
        }
    except Exception as exc:
        return None, {
            "used": True,
            "ok": False,
            "request_name": request_name,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _v1671_source_phrase(ids: Sequence[str], units: Mapping[str, str]) -> str:
    for evidence_id in ids:
        if evidence_id in units:
            return _legacy._clean(units[evidence_id], 220)
    return ""


def _v1671_resolve_partial(
    payload: Optional[Mapping[str, Any]],
    units: Mapping[str, str],
) -> Tuple[Dict[str, Any], List[str]]:
    plan: Dict[str, Any] = {field: [] for field in _V1671_ROLE_FIELDS}
    plan["local_identifiers"] = []
    plan["ambiguities"] = []
    warnings: List[str] = []
    if not isinstance(payload, Mapping):
        return _legacy._merge_legacy_role_fields(plan), ["llm_payload_missing"]

    valid_ids = set(units)

    def ids_for(row: Mapping[str, Any]) -> List[str]:
        raw = row.get("evidence_ids") or []
        if isinstance(raw, str):
            raw = [raw]
        ids = [str(x).strip() for x in raw if str(x).strip() in valid_ids][:3]
        if ids:
            return ids
        # Rolling-deployment/backward compatibility: old V167 payloads cited a
        # source_phrase. Convert it once to the matching evidence ID; after that
        # the exact source text is owned by the code, not by the LLM.
        phrase = _legacy._clean(row.get("source_phrase"), 220)
        if phrase:
            for evidence_id, text in units.items():
                if _legacy._appears_in_evidence(phrase, text):
                    return [evidence_id]
        return []

    for field in _V1671_ROLE_FIELDS:
        seen = set()
        for index, row in enumerate(payload.get(field) or []):
            if not isinstance(row, Mapping):
                warnings.append(f"{field}[{index}]:not_object")
                continue
            term = _legacy._clean(row.get("term_en"), 140)
            evidence_ids = ids_for(row)
            if not term:
                warnings.append(f"{field}[{index}]:missing_term")
                continue
            if not evidence_ids:
                warnings.append(f"{field}[{index}]:invalid_evidence_ref")
                continue
            if not _legacy._role_term_is_usable(term, field):
                warnings.append(f"{field}[{index}]:unusable_term:{term[:80]}")
                continue
            nt = _legacy._norm(term)
            if not nt or nt in _legacy._ADMIN_NOISE or nt in _legacy._LITERAL_NOISE or nt in seen:
                continue
            seen.add(nt)
            plan[field].append({
                "term_en": term,
                "source_phrase": _v1671_source_phrase(evidence_ids, units),
                "evidence_ids": evidence_ids,
            })
            if len(plan[field]) >= 8:
                break

    # Un ancrage de domaine identique à l'objet n'apporte aucune
    # désambiguïsation. On le retire pour forcer une réparation explicite plutôt
    # que de prétendre que deux rôles identiques constituent deux preuves.
    object_terms = {
        _legacy._norm(row.get("term_en"))
        for row in plan.get("scientific_object") or []
        if isinstance(row, Mapping)
    }
    plan["domain_anchors"] = [
        row for row in plan.get("domain_anchors") or []
        if _legacy._norm(row.get("term_en")) not in object_terms
    ]

    for index, row in enumerate(payload.get("local_identifiers") or []):
        if not isinstance(row, Mapping):
            continue
        value = _legacy._clean(row.get("value"), 100)
        evidence_ids = ids_for(row)
        phrase = _v1671_source_phrase(evidence_ids, units)
        if not value or not evidence_ids:
            warnings.append(f"local_identifiers[{index}]:invalid")
            continue
        # The identifier itself must really occur in its cited evidence unit.
        if not _legacy._appears_in_evidence(value, phrase):
            warnings.append(f"local_identifiers[{index}]:value_not_in_evidence")
            continue
        plan["local_identifiers"].append({
            "value": value,
            "source_phrase": phrase,
            "evidence_ids": evidence_ids,
        })

    for index, row in enumerate(payload.get("ambiguities") or []):
        if not isinstance(row, Mapping):
            continue
        source_term = _legacy._clean(row.get("source_term"), 100)
        resolved = _legacy._clean(row.get("resolved_en"), 140)
        evidence_ids = ids_for(row)
        phrase = _v1671_source_phrase(evidence_ids, units)
        if not source_term or not resolved or not evidence_ids:
            warnings.append(f"ambiguities[{index}]:invalid")
            continue
        if not _legacy._appears_in_evidence(source_term, phrase):
            warnings.append(f"ambiguities[{index}]:source_not_in_evidence")
            continue
        if not _legacy._role_term_is_usable(resolved, "independent_variables"):
            warnings.append(f"ambiguities[{index}]:unusable_resolution")
            continue
        plan["ambiguities"].append({
            "source_term": source_term,
            "resolved_en": resolved,
            "source_phrase": phrase,
            "evidence_ids": evidence_ids,
        })

    return _legacy._merge_legacy_role_fields(plan), warnings


def _v1671_merge_plans(base: Mapping[str, Any], new: Mapping[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    for field in _V1671_ROLE_FIELDS:
        rows: List[Dict[str, Any]] = []
        seen = set()
        for source in list(base.get(field) or []) + list(new.get(field) or []):
            if not isinstance(source, Mapping):
                continue
            term = _legacy._clean(source.get("term_en"), 140)
            key = _legacy._norm(term)
            if not key or key in seen:
                continue
            seen.add(key)
            rows.append(dict(source))
        merged[field] = rows[:8]

    # Local identifiers and ambiguity resolutions are also merged, but local IDs
    # never participate in the query concepts.
    local_rows: List[Dict[str, Any]] = []
    seen_local = set()
    for source in list(base.get("local_identifiers") or []) + list(new.get("local_identifiers") or []):
        if not isinstance(source, Mapping):
            continue
        value = _legacy._clean(source.get("value"), 100)
        key = _legacy._norm(value)
        if value and key not in seen_local:
            seen_local.add(key)
            local_rows.append(dict(source))
    merged["local_identifiers"] = local_rows[:16]

    ambiguity_rows: List[Dict[str, Any]] = []
    seen_amb = set()
    for source in list(base.get("ambiguities") or []) + list(new.get("ambiguities") or []):
        if not isinstance(source, Mapping):
            continue
        key = (_legacy._norm(source.get("source_term")), _legacy._norm(source.get("resolved_en")))
        if key[0] and key not in seen_amb:
            seen_amb.add(key)
            ambiguity_rows.append(dict(source))
    merged["ambiguities"] = ambiguity_rows[:12]
    return _legacy._merge_legacy_role_fields(merged)


def _v1671_problem_axis_present(plan: Mapping[str, Any]) -> bool:
    return bool(
        plan.get("independent_variables")
        or plan.get("response_variables")
        or plan.get("phenomena")
        or plan.get("methods")
        or plan.get("operating_conditions")
        or plan.get("validation_concepts")
    )


def _v1671_blocking_errors(plan: Mapping[str, Any], queries: Sequence[Mapping[str, Any]]) -> List[str]:
    errors: List[str] = []
    if not plan.get("scientific_object"):
        errors.append("missing_scientific_object")
    if not _v1671_problem_axis_present(plan):
        errors.append("missing_problem_axis")
    if not queries:
        errors.append("zero_safe_queries")
    return errors


def _v1671_missing_roles(plan: Mapping[str, Any]) -> List[str]:
    missing: List[str] = []
    if not plan.get("domain_anchors"):
        missing.append("domain_anchors")
    if not plan.get("scientific_object"):
        missing.append("scientific_object")
    if not _v1671_problem_axis_present(plan):
        missing.extend([
            "independent_variables", "response_variables", "phenomena",
            "operating_conditions", "methods",
        ])
    elif not plan.get("response_variables"):
        # Non-blocking, but useful for a more discriminating query.
        missing.append("response_variables")
    return list(dict.fromkeys(missing))


def _node_collect(state: QueryWorkflowState) -> Dict[str, Any]:
    intent = dict(state.get("intent") or {})
    target_evidence = _legacy._clean(_legacy._source_evidence(intent), 6500)
    parent_section = _legacy._clean(intent.get("parent_section_text"), 6500)
    evidence_parts = [f"PASSAGE CIBLE. {target_evidence}"] if target_evidence else []
    if parent_section and _legacy._norm(parent_section) != _legacy._norm(target_evidence):
        evidence_parts.append(f"SECTION PARENTE COMPLETE. {parent_section}")
    evidence = "\n".join(evidence_parts)
    units = _v1671_split_evidence(evidence)
    explicit_local = _v1671_safe_explicit_local_identifiers(intent, evidence)
    return {
        "evidence": evidence,
        "evidence_units": units,
        "evidence_fingerprint": _legacy._evidence_fingerprint(intent),
        "explicit_local_ids": explicit_local,
        "status": STATUS_PLANNING,
        "attempts": 0,
        "validation_errors": [],
        "validation_warnings": [],
        "llm_attempts": [],
    }


def _node_plan(state: QueryWorkflowState) -> Dict[str, Any]:
    evidence = state.get("evidence") or ""
    units = state.get("evidence_units") or _v1671_split_evidence(evidence)
    explicit_local = state.get("explicit_local_ids") or []
    raw, meta = _v1671_call_llm(
        evidence=evidence,
        units=units,
        explicit_local_ids=explicit_local,
        attempt=1,
    )
    plan, warnings = _v1671_resolve_partial(raw, units)

    # Keep high-confidence title-local identifiers even if the LLM omitted them.
    explicit_rows = [{"value": x, "source_phrase": "", "evidence_ids": []} for x in explicit_local]
    plan = _v1671_merge_plans(plan, {**{f: [] for f in _V1671_ROLE_FIELDS}, "local_identifiers": explicit_rows, "ambiguities": []})

    queries = _build_queries(plan)
    blockers = _v1671_blocking_errors(plan, queries)
    ready = not blockers
    return {
        "raw_payload": raw,
        "plan": plan,
        "queries": queries,
        "attempts": 1,
        "validation_errors": blockers,
        "validation_warnings": warnings,
        "llm_attempts": [meta],
        "status": STATUS_READY if ready else STATUS_REPAIR,
        "planning_mode": "llm_evidence_refs" if ready else "llm_partial_repair_required",
    }


def _node_repair(state: QueryWorkflowState) -> Dict[str, Any]:
    attempt = int(state.get("attempts") or 1) + 1
    previous_plan = dict(state.get("plan") or {})
    evidence = state.get("evidence") or ""
    units = state.get("evidence_units") or _v1671_split_evidence(evidence)
    legacy_repair = globals().get("_call_repair_llm")
    if callable(legacy_repair) and getattr(legacy_repair, "__name__", "") != "_call_repair_llm":
        raw, meta = legacy_repair(
            evidence=evidence,
            explicit_local_ids=state.get("explicit_local_ids") or [],
            previous_payload=state.get("raw_payload"),
            errors=state.get("validation_errors") or [],
            attempt=attempt,
        )
    else:
        raw, meta = _v1671_call_llm(
            evidence=evidence,
            units=units,
            explicit_local_ids=state.get("explicit_local_ids") or [],
            attempt=attempt,
            previous_payload=state.get("raw_payload"),
            blocking_errors=state.get("validation_errors") or [],
            missing_roles=_v1671_missing_roles(previous_plan),
        )
    new_plan, warnings = _v1671_resolve_partial(raw, units)
    merged = _v1671_merge_plans(previous_plan, new_plan)
    queries = _build_queries(merged)
    blockers = _v1671_blocking_errors(merged, queries)
    ready = not blockers
    return {
        "raw_payload": raw,
        "plan": merged,
        "queries": queries,
        "attempts": attempt,
        "validation_errors": blockers,
        "validation_warnings": list(state.get("validation_warnings") or []) + warnings,
        "llm_attempts": list(state.get("llm_attempts") or []) + [meta],
        "status": STATUS_READY if ready else STATUS_REPAIR,
        "planning_mode": "llm_partial_repaired" if ready else "llm_partial_repair_required",
    }


def _node_fallback(state: QueryWorkflowState) -> Dict[str, Any]:
    # V167.1 deliberately does NOT reconsume the polluted legacy V161
    # technical_object/phenomenon fields. It only uses roles that were actually
    # grounded by evidence IDs during one of the LLM attempts.
    plan = dict(state.get("plan") or {})
    queries = _build_queries(plan) if plan else []
    blockers = _v1671_blocking_errors(plan, queries)
    ready = not blockers
    return {
        "plan": plan,
        "queries": queries,
        "validation_errors": blockers,
        "status": STATUS_READY if ready else STATUS_FAILED,
        "planning_mode": "grounded_partial_fallback" if ready else "failed_after_grounded_repairs",
        "consultant_message": (
            "Les concepts valides récupérés lors des réparations suffisent à lancer la recherche."
            if ready
            else "EnnoScholar n'a pas obtenu le minimum scientifique traçable après les tentatives de réparation. "
                 "Aucune base n'a été interrogée; ce statut ne signifie pas qu'aucun article n'existe."
        ),
    }


def _route_after_repair(state: QueryWorkflowState) -> str:
    if state.get("status") == STATUS_READY:
        return "finalize"
    max_attempts = max(2, min(int(os.getenv("ENNOSCHOLAR_QUERY_PLAN_MAX_ATTEMPTS", "3") or 3), 3))
    if int(state.get("attempts") or 0) < max_attempts:
        return "repair"
    return "fallback"


def _run_builtin(initial: QueryWorkflowState) -> QueryWorkflowState:
    state: QueryWorkflowState = dict(initial)
    state.update(_node_collect(state))
    state.update(_node_plan(state))
    while state.get("status") != STATUS_READY:
        max_attempts = max(2, min(int(os.getenv("ENNOSCHOLAR_QUERY_PLAN_MAX_ATTEMPTS", "3") or 3), 3))
        if int(state.get("attempts") or 0) >= max_attempts:
            state.update(_node_fallback(state))
            break
        state.update(_node_repair(state))
    state.update(_node_finalize(state))
    return state


def run_query_workflow(intent: Mapping[str, Any]) -> Dict[str, Any]:
    initial: QueryWorkflowState = {"intent": dict(intent or {})}
    framework = "builtin_fail_safe"
    framework_error = ""
    with _observation(
        "ennoscholar.query_workflow.v167_6",
        {"version": WORKFLOW_VERSION, "verrou_id": intent.get("verrou_id")},
    ):
        try:
            import langgraph  # noqa: F401  # type: ignore
            framework = "langgraph"
            state = _run_langgraph(initial)
        except Exception as exc:
            framework = "builtin_fail_safe"
            framework_error = f"{type(exc).__name__}:{exc}"
            state = _run_builtin(initial)

    state["framework"] = framework
    status = str(state.get("status") or STATUS_FAILED)
    queries = list(state.get("queries") or [])
    report: Dict[str, Any] = {
        "version": WORKFLOW_VERSION,
        "framework": framework,
        "status": status,
        "search_allowed": bool(status == STATUS_READY and queries),
        "planning_mode": str(state.get("planning_mode") or "unknown"),
        "attempts": int(state.get("attempts") or 0),
        "max_llm_attempts": 2,
        "llm_retry_policy": "repair_only_missing_scientific_roles",
        "llm_retry_on_query_filter_failure": False,
        "llm_retry_reason": str(state.get("llm_retry_reason") or "none"),
        "query_count": len(queries),
        "validation_errors": list(state.get("validation_errors") or []),
        "validation_warnings": list(dict.fromkeys(state.get("validation_warnings") or []))[:40],
        "llm_attempts": list(state.get("llm_attempts") or []),
        "consultant_message": str(state.get("consultant_message") or ""),
        "evidence_unit_count": len(state.get("evidence_units") or {}),
        "framework_error": framework_error,
    }

    plan = dict(state.get("plan") or {})
    plan.update({
        "planner_version": WORKFLOW_VERSION,
        "workflow_status": status,
        "planning_mode": report["planning_mode"],
        "evidence_fingerprint": state.get("evidence_fingerprint"),
        "evidence_chars": len(state.get("evidence") or ""),
        "evidence_unit_count": report["evidence_unit_count"],
        "queries": queries,
        "query_count": len(queries),
        "workflow": report,
    })
    return {"plan": plan, "queries": queries, "workflow": report}


# ENNOSCHOLAR_V167_2_MULTIQUERY_PORTFOLIO_BEGIN
# Keep the V167.1 evidence-reference/partial-repair contract, but expand the
# validated role plan into a bounded 4–5 angle portfolio without another LLM call.
_V1672_BASE_BUILD_QUERIES = _build_queries

def _build_queries(plan: Mapping[str, Any]) -> List[Dict[str, Any]]:
    from .fast_retrieval import build_query_portfolio
    base = _V1672_BASE_BUILD_QUERIES(plan)
    try:
        target = int(os.getenv("ENNOSCHOLAR_QUERY_PORTFOLIO_SIZE", "5") or 5)
    except Exception:
        target = 5
    return build_query_portfolio(plan, base_queries=base, target=max(4, min(target, 5)))
# ENNOSCHOLAR_V167_2_MULTIQUERY_PORTFOLIO_END

# ENNOSCHOLAR_V167_3_USEFUL_TERMS_NO_WASTE_LLM_BEGIN
# V167.3 policy:
# - only obvious machine/generated noise is removed;
# - project/value-bearing terms are allowed in queries;
# - each query is judged independently;
# - query-building failure never triggers another LLM call;
# - at most one grounded repair call is allowed, and only for missing scientific roles.

def _v1673_core_errors(plan: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    if not plan.get("domain_anchors"):
        errors.append("missing_domain_anchor")
    if not plan.get("scientific_object"):
        errors.append("missing_scientific_object")
    if not _v1671_problem_axis_present(plan):
        errors.append("missing_problem_axis")
    return errors


def _v1671_blocking_errors(plan: Mapping[str, Any], queries: Sequence[Mapping[str, Any]]) -> List[str]:
    errors = _v1673_core_errors(plan)
    if not queries:
        errors.append("zero_safe_queries")
    return errors


def query_is_safe(query: str, intent_or_plan: Mapping[str, Any], *, allow_local: bool = True) -> bool:
    if not isinstance(intent_or_plan, Mapping):
        return False
    plan = (
        intent_or_plan.get("scientific_query_plan")
        if isinstance(intent_or_plan.get("scientific_query_plan"), Mapping)
        else intent_or_plan
    )
    from .fast_retrieval import query_is_useful
    return query_is_useful(query, plan)


def _build_queries(plan: Mapping[str, Any]) -> List[Dict[str, Any]]:
    from .fast_retrieval import build_query_portfolio
    try:
        target = int(os.getenv("ENNOSCHOLAR_QUERY_PORTFOLIO_SIZE", "5") or 5)
    except Exception:
        target = 5
    # Do not feed legacy rejected queries back into the new portfolio. The
    # validated scientific role plan is the source of truth.
    return build_query_portfolio(plan, base_queries=[], target=max(4, min(target, 5)))


def _node_plan(state: QueryWorkflowState) -> Dict[str, Any]:
    evidence = state.get("evidence") or ""
    units = state.get("evidence_units") or _v1671_split_evidence(evidence)
    explicit_local = state.get("explicit_local_ids") or []
    raw, meta = _v1671_call_llm(
        evidence=evidence,
        units=units,
        explicit_local_ids=explicit_local,
        attempt=1,
        domain_context=(state.get("intent") or {}).get("domain_context") or [],
        research_objective=str((state.get("intent") or {}).get("research_objective") or ""),
    )
    plan, warnings = _v1671_resolve_partial(raw, units)

    explicit_rows = [{"value": x, "source_phrase": "", "evidence_ids": []} for x in explicit_local]
    plan = _v1671_merge_plans(
        plan,
        {**{f: [] for f in _V1671_ROLE_FIELDS}, "local_identifiers": explicit_rows, "ambiguities": []},
    )

    queries = _build_queries(plan)
    core_errors = _v1673_core_errors(plan)
    if core_errors:
        status = STATUS_REPAIR
        planning_mode = "llm_missing_roles_repair_required"
        errors = core_errors + ([] if queries else ["zero_safe_queries"])
        message = "Le plan scientifique est incomplet ; une réparation ciblée des rôles manquants est autorisée."
    elif queries:
        status = STATUS_READY
        planning_mode = "llm_grounded_query_portfolio"
        errors = []
        message = f"Plan scientifique valide ; {len(queries)} requête(s) utiles construites sans repair LLM."
    else:
        # Crucial V167.3 rule: the LLM plan is already scientifically complete.
        # A deterministic builder/guard problem must never consume another LLM call.
        status = STATUS_FAILED
        planning_mode = "query_build_failed_no_llm_retry"
        errors = ["zero_safe_queries"]
        message = "Plan scientifique valide mais construction des requêtes impossible ; aucun repair LLM inutile n'a été lancé."

    return {
        "raw_payload": raw,
        "plan": plan,
        "queries": queries,
        "attempts": 1,
        "validation_errors": list(dict.fromkeys(errors)),
        "validation_warnings": warnings,
        "llm_attempts": [meta],
        "status": status,
        "planning_mode": planning_mode,
        "consultant_message": message,
        "llm_retry_reason": "missing_scientific_roles" if core_errors else "none",
    }


def _node_repair(state: QueryWorkflowState) -> Dict[str, Any]:
    # Exactly one repair maximum. It is reached only when scientific roles are
    # missing, never because a query was filtered/rejected.
    attempt = 2
    previous_plan = dict(state.get("plan") or {})
    evidence = state.get("evidence") or ""
    units = state.get("evidence_units") or _v1671_split_evidence(evidence)
    raw, meta = _v1671_call_llm(
        evidence=evidence,
        units=units,
        explicit_local_ids=state.get("explicit_local_ids") or [],
        attempt=attempt,
        previous_payload=state.get("raw_payload"),
        blocking_errors=_v1673_core_errors(previous_plan),
        missing_roles=_v1671_missing_roles(previous_plan),
        domain_context=(state.get("intent") or {}).get("domain_context") or [],
        research_objective=str((state.get("intent") or {}).get("research_objective") or ""),
    )
    new_plan, warnings = _v1671_resolve_partial(raw, units)
    merged = _v1671_merge_plans(previous_plan, new_plan)
    queries = _build_queries(merged)
    core_errors = _v1673_core_errors(merged)

    if core_errors:
        status = STATUS_REPAIR  # route goes to deterministic fallback, not a 3rd LLM call
        mode = "llm_single_repair_incomplete"
        errors = core_errors + ([] if queries else ["zero_safe_queries"])
        message = "La réparation LLM unique n'a pas complété les rôles scientifiques ; passage au fallback traçable."
    elif queries:
        status = STATUS_READY
        mode = "llm_single_repair_success"
        errors = []
        message = f"Plan réparé une fois ; {len(queries)} requête(s) utiles prêtes."
    else:
        status = STATUS_FAILED
        mode = "query_build_failed_after_single_repair_no_more_llm"
        errors = ["zero_safe_queries"]
        message = "Les rôles scientifiques sont présents mais le builder a échoué ; aucun troisième appel LLM n'est autorisé."

    return {
        "raw_payload": raw,
        "plan": merged,
        "queries": queries,
        "attempts": attempt,
        "validation_errors": list(dict.fromkeys(errors)),
        "validation_warnings": list(state.get("validation_warnings") or []) + warnings,
        "llm_attempts": list(state.get("llm_attempts") or []) + [meta],
        "status": status,
        "planning_mode": mode,
        "consultant_message": message,
        "llm_retry_reason": "missing_scientific_roles",
    }


def _route_after_plan(state: QueryWorkflowState) -> str:
    return "repair" if state.get("status") == STATUS_REPAIR else "finalize"


def _route_after_repair(state: QueryWorkflowState) -> str:
    if state.get("status") in {STATUS_READY, STATUS_FAILED}:
        return "finalize"
    return "fallback"


def _run_builtin(initial: QueryWorkflowState) -> QueryWorkflowState:
    state: QueryWorkflowState = dict(initial)
    state.update(_node_collect(state))
    state.update(_node_plan(state))
    if state.get("status") == STATUS_REPAIR:
        state.update(_node_repair(state))
        if state.get("status") == STATUS_REPAIR:
            state.update(_node_fallback(state))
    state.update(_node_finalize(state))
    return state


def _node_finalize(state: QueryWorkflowState) -> Dict[str, Any]:
    queries = list(state.get("queries") or [])
    ready = bool(queries) and state.get("status") == STATUS_READY
    if ready:
        message = f"Stratégie de recherche validée : {len(queries)} requête(s) scientifique(s) prête(s)."
    else:
        message = str(state.get("consultant_message") or "") or (
            "EnnoScholar n'a pas pu valider les requêtes scientifiques ; la recherche n'a pas été lancée."
        )
    return {
        "status": STATUS_READY if ready else STATUS_FAILED,
        "consultant_message": message,
    }
# ENNOSCHOLAR_V167_3_USEFUL_TERMS_NO_WASTE_LLM_END

# ENNOSCHOLAR_V167_4_FIVE_QUERIES_SELECTION_BEGIN
# Preserve the five validated query families through the final selector. The
# previous selector dropped useful siblings at similarity >= 0.72, which reduced
# a valid five-query portfolio to only three queries.
def select_queries(queries: Sequence[Any], intent: Mapping[str, Any], max_queries: int = 5) -> List[Dict[str, Any]]:
    enriched = attach_query_plan(intent)
    plan = enriched["scientific_query_plan"]
    try:
        requested = max(1, int(max_queries or 5))
    except Exception:
        requested = 5
    target = min(5, requested)

    seeds: List[Dict[str, Any]] = []
    seen = set()
    for raw in list(queries or []) + list(enriched.get("search_queries") or []):
        item = dict(raw) if isinstance(raw, Mapping) else {"query": str(raw), "kind": "external"}
        q = _legacy._clean(item.get("query"), 240)
        nq = _legacy._norm(q)
        if not nq or nq in seen or not query_is_safe(q, plan):
            continue
        seen.add(nq)
        item["query"] = q
        item.setdefault("family", str(item.get("kind") or "").replace("v167_4_", "").replace("v167_3_", "") or "external")
        seeds.append(item)

    from .fast_retrieval import build_query_portfolio
    selected = build_query_portfolio(plan, base_queries=seeds, target=target)

    score_map = {
        "direct": 1.00,
        "operating_conditions": 0.96,
        "experimental": 0.94,
        "secondary_variable": 0.91,
        "secondary_response": 0.90,
        "validation": 0.89,
        "mechanism": 0.88,
        "project_context": 0.86,
        "review": 0.82,
    }
    for row in selected:
        row["selection_score"] = score_map.get(str(row.get("family") or ""), 0.80)
        row["planner_version"] = WORKFLOW_VERSION
    return selected[:target]
# ENNOSCHOLAR_V167_4_FIVE_QUERIES_SELECTION_END

# ENNOSCHOLAR_V167_5_LEVELLED_QUERY_SELECTION_BEGIN
# Keep the five semantic levels intact through the final selector. We do not
# collapse strict/connected/fundamental/technical queries simply because they
# share core concepts; that overlap is intentional.
def select_queries(queries: Sequence[Any], intent: Mapping[str, Any], max_queries: int = 5) -> List[Dict[str, Any]]:
    enriched = attach_query_plan(intent)
    plan = enriched["scientific_query_plan"]
    try:
        requested = max(1, int(max_queries or 5))
    except Exception:
        requested = 5
    target = min(5, requested)

    from .fast_retrieval import build_query_portfolio
    selected = build_query_portfolio(plan, base_queries=[], target=target)

    score_map = {
        "strict_core": 1.00,
        "strict_conditions": 0.98,
        "connexe": 0.88,
        "fundamental": 0.76,
        "technical": 0.72,
    }
    for row in selected:
        row["selection_score"] = score_map.get(str(row.get("family") or ""), 0.80)
        row["planner_version"] = WORKFLOW_VERSION
    return selected[:target]
# ENNOSCHOLAR_V167_5_LEVELLED_QUERY_SELECTION_END

# ENNOSCHOLAR_V167_6_ADAPTIVE_RECALL_QUERY_SELECTION_BEGIN
# Six intentional search levels are preserved. Similarity between levels is not
# a reason to discard them because each level serves a different recall role.
def select_queries(queries: Sequence[Any], intent: Mapping[str, Any], max_queries: int = 6) -> List[Dict[str, Any]]:
    enriched = attach_query_plan(intent)
    plan = enriched["scientific_query_plan"]
    try:
        requested = max(1, int(max_queries or 6))
    except Exception:
        requested = 6
    target = min(6, requested)

    from .fast_retrieval import build_query_portfolio
    selected = build_query_portfolio(plan, base_queries=[], target=target)

    score_map = {
        "strict_core_a": 1.00,
        "strict_core_b": 0.99,
        "connexe_a": 0.88,
        "connexe_b": 0.84,
        "fundamental": 0.76,
        "technical": 0.72,
    }
    for row in selected:
        row["selection_score"] = score_map.get(str(row.get("family") or ""), 0.80)
        row["planner_version"] = WORKFLOW_VERSION
    return selected[:target]
# ENNOSCHOLAR_V167_6_ADAPTIVE_RECALL_QUERY_SELECTION_END
