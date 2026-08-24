from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any

from ..domain.models import ImprovementRequest, SectionFunction, TargetScope


LIGHTWEIGHT_RESEARCH_CONTEXT_VERSION = "v1_1_typed_scientific_research"
MIN_LOCAL_DOMAIN_CONFIDENCE = 0.35
MAX_FULL_DOCUMENT_RESEARCH_TARGETS = 16
MAX_LOCK_FACETS_PER_SECTION = 6


_RESEARCH_STRATEGIES: dict[str, dict[str, Any]] = {
    "scientific_landscape": {
        "diagnostic_policy": "not_required",
        "objective": "Positionner les approches existantes sans fabriquer de verrou projet.",
        "evidence_axes": [
            "familles d'approches directement comparables",
            "methodes et protocoles documentes",
            "limites et conditions de validite publiees",
            "ecarts de connaissance explicitement etablis par les sources",
        ],
    },
    "lock_search": {
        "diagnostic_policy": "use_when_available",
        "objective": "Etayer le verrou reel de la section et sa non-trivialite.",
        "evidence_axes": [
            "mecanismes connus a l'origine de la difficulte",
            "limites ou cas d'echec documentes",
            "conditions dans lesquelles les approches existantes deviennent insuffisantes",
            "methodes de validation employees dans la litterature",
        ],
    },
    "limitation_search": {
        "diagnostic_policy": "not_required",
        "objective": "Documenter une limite observee sans lui attribuer une cause absente du projet.",
        "evidence_axes": [
            "limites publiees sur des objets comparables",
            "facteurs d'influence documentes",
            "domaines de validite et cas d'echec",
        ],
    },
    "method_search": {
        "diagnostic_policy": "not_required",
        "objective": "Comparer et justifier les choix methodologiques de la section.",
        "evidence_axes": [
            "methodes comparables",
            "protocoles de validation",
            "avantages, limites et conditions d'emploi",
        ],
    },
    "parameter_search": {
        "diagnostic_policy": "not_required",
        "objective": "Documenter l'influence et le choix des parametres sans inventer de valeur projet.",
        "evidence_axes": [
            "sensibilite aux parametres",
            "plages et conditions documentees",
            "methodes de calibration ou d'optimisation",
        ],
    },
    "result_interpretation": {
        "diagnostic_policy": "not_required",
        "objective": "Mettre les resultats en perspective avec des observations publiees comparables.",
        "evidence_axes": [
            "resultats comparables",
            "explications documentees",
            "limites de comparaison",
        ],
    },
    "contribution_positioning": {
        "diagnostic_policy": "not_required",
        "objective": "Positionner la contribution par rapport aux solutions publiees.",
        "evidence_axes": [
            "solutions anterieures comparables",
            "ecarts de methode ou de domaine de validite",
            "limites connues des approches existantes",
        ],
    },
    "context_enrichment": {
        "diagnostic_policy": "not_required",
        "objective": "Ajouter uniquement le contexte scientifique utile a la comprehension.",
        "evidence_axes": ["definitions etablies", "phenomenes et enjeux documentes"],
    },
    "scientific_synthesis": {
        "diagnostic_policy": "not_required",
        "objective": "Relier les constats de la section aux connaissances publiees pertinentes.",
        "evidence_axes": ["convergences", "divergences", "limites de transposition"],
    },
    "scientific_enrichment": {
        "diagnostic_policy": "not_required",
        "objective": "Renforcer les affirmations scientifiques qui disposent d'un ancrage local precis.",
        "evidence_axes": ["preuves directes", "methodes comparables", "limites publiees"],
    },
}

_ANCHOR_STOPWORDS = {
    "avec", "cette", "dans", "des", "est", "ete", "etre", "fait", "faire", "les",
    "leur", "mais", "pour", "plus", "projet", "section", "sont", "sur", "une",
    "utilise", "utiliser", "travaux", "texte", "passage", "resultat", "resultats",
    "scientifique", "scientifiques", "technique", "techniques", "ameliorer", "renforcer",
}

_LOCK_FACET_SIGNALS = re.compile(
    r"\b(?:verrou|incertitud|difficult|complex|limit|depend|variab|insuffis|"
    r"non[ -]?trivial|challenge|uncertain|representativ|validat|erreur|error|"
    r"failure|echec|robust|generalis)\w*\b",
    flags=re.I,
)


def _clean(value: Any, limit: int = 0) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit] if limit and len(text) > limit else text


def local_cir_context(request: ImprovementRequest, flank_chars: int = 14000) -> str:
    """Retourne le contexte local du document courant, sans lire les RAW projet."""

    target = str(request.target_text or "")
    full = str(request.full_text or "")
    if not full or full == target or request.target_scope == TargetScope.FULL_DOCUMENT:
        return ""

    index = full.find(target) if target else -1
    if index < 0:
        return ""

    start = max(0, index - flank_chars)
    end = min(len(full), index + len(target) + flank_chars)
    context = full[start:end]
    local_index = context.find(target)
    if local_index >= 0:
        context = context[:local_index] + "\n" + context[local_index + len(target):]
    return context.strip()


def _fallback_domain(project: Any, request: ImprovementRequest) -> dict[str, Any]:
    value = _clean(
        request.project_domain
        or getattr(project, "domain_label", None)
        or getattr(project, "domain", None)
        or getattr(project, "project_domain", None),
        500,
    )
    if not value:
        return {}
    return {
        "display_label": value,
        "main_domain_label": value,
        "confidence": 0.0,
        "source": "project_domain_fallback",
    }


def detect_domain_lightweight(
    project: Any,
    request: ImprovementRequest,
    *,
    local_context: str = "",
) -> dict[str, Any]:
    """Utilise uniquement le classifieur de domaine, sans NLP/Frascati/RAG."""

    analysis_text = "\n".join(
        part
        for part in (
            request.target_section_title or "",
            request.target_text or "",
            local_context,
        )
        if str(part or "").strip()
    )
    try:
        from modules.NLP.domain_classifier import classify_domain

        detected = classify_domain(analysis_text[:250000]) if analysis_text.strip() else {}
    except Exception as exc:
        detected = {
            "confidence": 0.0,
            "top_domains": [],
            "warning": f"lightweight domain detection failed: {type(exc).__name__}: {exc}",
        }

    detected_has_domain = bool(isinstance(detected, dict) and (
        detected.get("domain_code_niv1")
        or detected.get("domain_code_niv2")
        or detected.get("domain_code_niv3")
        or detected.get("display_label")
    ))
    try:
        confidence = float((detected or {}).get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    fallback = _fallback_domain(project, request)
    if detected_has_domain and (
        confidence >= MIN_LOCAL_DOMAIN_CONFIDENCE or not fallback
    ):
        output = dict(detected)
        output["source"] = "EnnoAmel_lightweight_domain_classifier"
        return output
    if fallback:
        return {
            **fallback,
            "source": "project_domain_after_low_confidence_local_detection",
            "local_detection_confidence": confidence,
            "local_detection_candidate": (
                (detected or {}).get("display_label")
                or ((detected or {}).get("display") or {}).get("display_label")
            ),
        }
    return dict(detected or {})


def _target_type(value: Any) -> str:
    raw = str(value.value if isinstance(value, SectionFunction) else value or "").strip()
    aliases = {
        SectionFunction.CONTEXT.value: "context_enrichment",
        SectionFunction.SCIENTIFIC_LANDSCAPE.value: "scientific_landscape",
        SectionFunction.UNCERTAINTY.value: "lock_search",
        SectionFunction.METHOD.value: "method_search",
        SectionFunction.PARAMETER.value: "parameter_search",
        SectionFunction.RESULT.value: "result_interpretation",
        SectionFunction.LIMITATION.value: "limitation_search",
        SectionFunction.CONTRIBUTION.value: "contribution_positioning",
        SectionFunction.SYNTHESIS.value: "scientific_synthesis",
        SectionFunction.OTHER.value: "scientific_enrichment",
    }
    if raw in aliases.values():
        return raw
    return aliases.get(raw, "scientific_enrichment")


def _infer_target_type(request: ImprovementRequest, declared: str) -> str:
    """Precise un type generique depuis la demande et le titre, sans LLM lourd."""

    if declared != "scientific_enrichment":
        return declared
    text = _clean(
        " ".join(
            part
            for part in (
                request.target_section_title or "",
                request.instruction or "",
            )
            if part
        ),
        4500,
    )
    folded = unicodedata.normalize("NFKD", text.casefold())
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    if re.search(r"\b(?:etat de l.art|litterature|bibliograph|travaux anterieurs)\b", folded):
        return "scientific_landscape"
    if re.search(r"\b(?:verrou|incertitude|obstacle|non.?trivial)\b", folded):
        return "lock_search"
    if re.search(r"\b(?:limite|limitation|cas d.echec)\b", folded):
        return "limitation_search"
    return declared


def research_strategy(value: Any) -> dict[str, Any]:
    """Retourne le besoin documentaire utile au role de la section."""

    target_type = _target_type(value)
    return {
        "research_target_type": target_type,
        **dict(_RESEARCH_STRATEGIES.get(target_type) or _RESEARCH_STRATEGIES["scientific_enrichment"]),
        "article_use_policy": "candidate_until_consultant_validation",
        "project_fact_policy": "never_infer_missing_project_facts_from_publications",
    }


def _local_search_readiness(title: str, target_text: str) -> dict[str, Any]:
    """Verifie que la cible contient assez d'ancres pour eviter une recherche large.

    Ce controle ne remplace pas l'extraction scientifique d'EnnoScholar. Il ne
    conserve qu'un apercu de termes locaux pour expliquer pourquoi une recherche
    peut etre lancee; les mots-cles finaux restent produits par
    ``scientific_intent_builder``.
    """

    raw = " ".join(part for part in (title, target_text) if part).strip()
    folded = unicodedata.normalize("NFKD", raw.casefold())
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    terms: list[str] = []
    for token in re.findall(r"[a-z][a-z0-9_-]{2,}", folded):
        if token in _ANCHOR_STOPWORDS or token in terms:
            continue
        terms.append(token)
    acronyms = list(dict.fromkeys(re.findall(r"\b[A-Z][A-Z0-9-]{1,}\b", raw)))[:8]
    useful = [*acronyms, *terms]
    return {
        "ready": bool(len(useful) >= 4 and len(str(target_text or "").strip()) >= 35),
        "local_anchor_preview": useful[:18],
        "anchor_count": len(useful),
        "source": "current_section_only",
        "final_keyword_builder": "EnnoScholar.scientific_intent_builder",
    }


def _lock_research_facets(
    *,
    target_id: str,
    section_title: str,
    target_text: str,
    target_type: str,
    research_context: dict[str, Any],
    strategy: dict[str, Any],
    research_objective: str,
) -> list[dict[str, Any]]:
    """Decoupe une section de verrous agregee en passages scientifiques autonomes."""

    if target_type != "lock_search":
        return []
    blocks = [
        _clean(block, 7000)
        for block in re.split(r"\n\s*\n+", str(target_text or ""))
        if len(_clean(block)) >= 120
    ]
    scored: list[tuple[int, int, str, dict[str, Any]]] = []
    for index, block in enumerate(blocks):
        readiness = _local_search_readiness(section_title, block)
        signals = len(_LOCK_FACET_SIGNALS.findall(block))
        if signals < 1 or int(readiness.get("anchor_count") or 0) < 6:
            continue
        score = signals * 20 + min(int(readiness.get("anchor_count") or 0), 25)
        scored.append((score, index, block, readiness))

    # Une section monothématique reste une seule cible. Le decoupage est reserve
    # aux sections agregees qui expriment plusieurs difficultes independantes.
    if len(scored) < 3:
        return []
    selected = sorted(
        sorted(scored, key=lambda row: (row[0], len(row[2])), reverse=True)[
            :MAX_LOCK_FACETS_PER_SECTION
        ],
        key=lambda row: row[1],
    )

    facets: list[dict[str, Any]] = []
    for ordinal, (_, source_index, block, readiness) in enumerate(selected, start=1):
        first_sentence = re.split(r"(?<=[.!?])\s+", block, maxsplit=1)[0]
        facet_title = _clean(first_sentence, 240) or f"Axe scientifique {ordinal}"
        facet_id = f"{target_id}:facet:{ordinal}"
        facet_context = {
            **research_context,
            "context_kind": "section_lock_facet",
            "source_section_id": target_id,
            "source_section_title": section_title,
            # Le passage reste la cible locale, mais la section complete est le
            # garde-fou semantique commun. Sans ce parent, des mots ambigus tels
            # que frequence, recalage ou modele changent facilement de domaine.
            "parent_section_text": _clean(target_text, 20000),
            "parent_section_text_chars": len(str(target_text or "").strip()),
            "research_objective": _clean(research_objective, 3500),
            "research_target_id": facet_id,
            "research_target_title": facet_title,
            "source_passage_index": source_index,
            "search_readiness": readiness,
        }
        facets.append({
            "research_target_id": facet_id,
            "research_target_type": target_type,
            "title": facet_title,
            "text": block,
            "parent_section_id": target_id,
            "parent_section_title": section_title,
            "source_passage_index": source_index,
            "raw_item": {
                "text": block,
                "source_text": block,
                "parent_section_text": _clean(target_text, 20000),
                "research_objective": _clean(research_objective, 3500),
                "original_title": facet_title,
                "supporting_passages": [],
                "source_section_title": section_title,
                "search_strategy": strategy,
            },
            "context": facet_context,
            "research_context": facet_context,
            "source_json": {
                "source_section_title": section_title,
                "parent_section_id": target_id,
                "parent_section_text": _clean(target_text, 20000),
                "research_target_type": target_type,
                "source_origin": "ennoamel_current_section_lock_facet",
            },
        })
    return facets


def _full_document_research_context(
    project: Any,
    request: ImprovementRequest,
) -> dict[str, Any]:
    """Construit des cibles sectionnelles au lieu d'une requete document vague."""

    plan_by_id = {
        plan.section_id: plan
        for plan in (request.research_section_plan or [])
    }
    primary_roles = {
        SectionFunction.SCIENTIFIC_LANDSCAPE,
        SectionFunction.UNCERTAINTY,
        SectionFunction.LIMITATION,
        SectionFunction.CONTRIBUTION,
    }
    secondary_roles = {
        SectionFunction.METHOD,
        SectionFunction.PARAMETER,
        SectionFunction.RESULT,
        SectionFunction.SYNTHESIS,
    }
    candidates: list[tuple[tuple[int, int, int, int], dict[str, Any]]] = []
    for section in request.sections:
        plan = plan_by_id.get(section.section_id)
        if plan is not None and not plan.needs_scholar:
            continue
        function = plan.function if plan is not None else SectionFunction.OTHER
        subrequest = request.model_copy(
            update={
                "full_text": request.full_text,
                "target_text": section.content,
                "target_scope": TargetScope.SECTION,
                "target_section_id": section.section_id,
                "target_section_title": section.title,
                "research_target_type": function.value,
                "research_section_plan": [],
                "sections": [],
            }
        )
        context = build_lightweight_research_context(
            project,
            subrequest,
            section_function=function,
        )
        readiness = dict(context.get("search_readiness") or {})
        if not readiness.get("ready"):
            continue
        role_priority = 3 if function in primary_roles else 2 if function in secondary_roles else 1
        score = (
            role_priority,
            int(readiness.get("anchor_count") or 0),
            min(len(section.content or ""), 8000),
            int(section.level or 1),
        )
        candidates.append((score, context))

    candidates.sort(key=lambda row: row[0], reverse=True)
    selected_contexts = [
        row[1] for row in candidates[:MAX_FULL_DOCUMENT_RESEARCH_TARGETS]
    ]
    targets: list[dict[str, Any]] = []
    for section_context in selected_contexts:
        for target in section_context.get("research_targets") or []:
            if not isinstance(target, dict):
                continue
            targets.append(target)
            if len(targets) >= MAX_FULL_DOCUMENT_RESEARCH_TARGETS:
                break
        if len(targets) >= MAX_FULL_DOCUMENT_RESEARCH_TARGETS:
            break
    target_ids = [
        str(target.get("research_target_id") or "")
        for target in targets
        if str(target.get("research_target_id") or "").strip()
    ]
    domain = dict(
        (selected_contexts[0].get("domain_detection") or {})
        if selected_contexts
        else _fallback_domain(project, request)
    )
    readiness = {
        "ready": bool(targets),
        "target_count": len(targets),
        "eligible_section_count": len(candidates),
        "omitted_section_count": max(0, len(candidates) - len(targets)),
        "max_targets": MAX_FULL_DOCUMENT_RESEARCH_TARGETS,
        "source": "semantic_section_plan_and_current_document_only",
        "final_keyword_builder": "EnnoScholar.scientific_intent_builder",
    }
    strategy = {
        "research_target_type": "multi_section_scientific_enrichment",
        "diagnostic_policy": "not_required",
        "objective": "Rechercher separement les preuves utiles aux sections scientifiques du CIR.",
        "evidence_axes": [],
        "article_use_policy": "candidate_until_consultant_validation",
        "project_fact_policy": "never_infer_missing_project_facts_from_publications",
    }
    research_context = {
        "context_kind": "full_document_section_targets",
        "research_objective": _clean(request.instruction, 3500),
        "source_section_ids": target_ids,
        "no_project_fact_inference": True,
        "keywords_generated_here": False,
        "search_strategy": strategy,
        "search_readiness": readiness,
    }
    return {
        "version": LIGHTWEIGHT_RESEARCH_CONTEXT_VERSION,
        "mode": "direct_multi_section_scholar_without_mandatory_diagnostic",
        "domain_detection": domain,
        "research_context": research_context,
        "research_targets": targets,
        "research_target_ids": target_ids,
        "research_target_type": "multi_section_scientific_enrichment",
        "diagnostic_required": False,
        "diagnostic_policy": "not_required",
        "search_readiness": readiness,
        "search_strategy": strategy,
        "keywords_generated_here": False,
    }


def build_lightweight_research_context(
    project: Any,
    request: ImprovementRequest,
    *,
    section_function: SectionFunction | str | None = None,
) -> dict[str, Any]:
    """Construit le sens transmis à EnnoScholar, sans générer de mots-clés.

    Les mots-clés, objets, phénomènes, méthodes et requêtes restent la
    responsabilité de ``scientific_intent_builder`` et ``query_builder``.
    """

    if (
        request.target_scope in {TargetScope.MULTI_SECTION, TargetScope.FULL_DOCUMENT}
        and len(request.sections) > 1
    ):
        return _full_document_research_context(project, request)

    context = local_cir_context(request)
    domain = detect_domain_lightweight(project, request, local_context=context)
    target_type = _infer_target_type(
        request,
        _target_type(
            section_function
            or getattr(request, "research_target_type", None)
            or SectionFunction.OTHER
        ),
    )
    source_basis = "\n".join(
        part
        for part in (
            request.target_section_id or "",
            request.target_section_title or "",
            request.target_text or "",
        )
        if str(part or "").strip()
    )
    digest = hashlib.sha1(source_basis.encode("utf-8", errors="ignore")).hexdigest()[:12]
    target_id = _clean(request.target_section_id, 120) or f"research-{digest}"
    title = _clean(
        request.target_section_title
        or request.target_section_id
        or "Cible scientifique à enrichir",
        500,
    )
    target_text = str(request.target_text or "").strip()

    strategy = research_strategy(target_type)
    readiness = _local_search_readiness(title, target_text)
    research_context = {
        "context_kind": "lightweight_research_context",
        "context_text": context,
        "local_context": context,
        "research_objective": _clean(request.instruction, 3500),
        "parent_section_text": _clean(target_text, 20000),
        "parent_section_text_chars": len(target_text),
        "source_section_id": request.target_section_id,
        "source_section_title": request.target_section_title,
        "no_project_fact_inference": True,
        "keywords_generated_here": False,
        "search_strategy": strategy,
        "search_readiness": readiness,
        "project_knowledge_policy": {
            "reuse_project_domain": True,
            "reuse_existing_validated_articles_first": True,
            "memory_v2_role": "retrieval_context_only",
            "raw_memory_facts_authorized": False,
        },
    }
    research_target = {
        "research_target_id": target_id,
        "research_target_type": target_type,
        "title": title,
        "text": target_text,
        "raw_item": {
            "text": target_text,
            "source_text": target_text,
            "parent_section_text": _clean(target_text, 20000),
            "research_objective": _clean(request.instruction, 3500),
            "original_title": title,
            "supporting_passages": ([{"text": context}] if context else []),
            "source_section_title": title,
            "consultant_instruction": _clean(request.instruction, 3500),
            "search_strategy": strategy,
        },
        "context": research_context,
        "research_context": research_context,
        "source_json": {
            "source_section_title": title,
            "research_target_type": target_type,
            "source_origin": "ennoamel_current_section",
        },
    }
    facets = _lock_research_facets(
        target_id=target_id,
        section_title=title,
        target_text=target_text,
        target_type=target_type,
        research_context=research_context,
        strategy=strategy,
        research_objective=request.instruction,
    )
    research_targets = facets or [research_target]
    research_target_ids = [
        str(row.get("research_target_id") or "")
        for row in research_targets
        if str(row.get("research_target_id") or "").strip()
    ]
    return {
        "version": LIGHTWEIGHT_RESEARCH_CONTEXT_VERSION,
        "mode": "direct_scholar_without_mandatory_diagnostic",
        "domain_detection": domain,
        "research_context": research_context,
        "research_targets": research_targets,
        "research_target_ids": research_target_ids,
        "research_target_type": target_type,
        "diagnostic_required": False,
        "diagnostic_policy": strategy["diagnostic_policy"],
        "search_readiness": readiness,
        "search_strategy": strategy,
        "keywords_generated_here": False,
        "target_decomposition": {
            "enabled": bool(facets),
            "policy": "lock_section_passage_facets_v1",
            "parent_section_id": target_id,
            "target_count": len(research_targets),
        },
    }
