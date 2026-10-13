# -*- coding: utf-8 -*-
from __future__ import annotations

"""V168 universal scientific-role paper ranker.

Classification is based on the *current* verrou only. No project/domain ontology,
client name or radar/compressor-specific vocabulary is hard-coded.

Tags:
- Direct: same scientific object + the central relation/problem is supported.
- Connexe: same object/domain with a useful but incomplete scientific relation.
- Fondamental: theory/review/principle/method background useful to understand the verrou.
- Technique: implementation/tool/simulator/software/protocol-oriented source aligned with the object.
- Hors sujet: internal rejection tag; never used as a search target.

BGE reranking is intentionally separate: it may reorder papers, but must not invent
or promote scientific categories.
"""

import math
import re
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Mapping, Set, Tuple

from .utils import clean_text, norm, tokenize

VERSION = "v168_role_coverage_classifier"

DIRECT_TAG = "Direct"
CONNEXE_TAG = "Connexe"
FONDAMENTAL_TAG = "Fondamental"
TECHNIQUE_TAG = "Technique"
HORS_SUJET_TAG = "Hors sujet"

# Only linguistic/scaffolding noise. Scientific quantities such as accuracy,
# temperature, density, performance, speed, flow, etc. are deliberately kept.
ROLE_STOP = {
    "the", "a", "an", "of", "for", "to", "in", "on", "with", "and", "or", "from",
    "by", "between", "toward", "towards", "under", "over", "into", "through", "using",
    "used", "use", "type", "types", "different", "various", "according", "versus", "vs",
    "study", "studies", "paper", "article", "approach", "approaches", "method", "methods",
    "model", "models", "system", "systems", "based", "proposed", "new", "analysis",
    "le", "la", "les", "un", "une", "des", "de", "du", "pour", "dans", "sur", "avec",
    "sans", "entre", "vers", "par", "selon", "et", "ou", "utilise", "utilisee", "utilisé",
    "utilisée", "etude", "étude", "article", "methode", "méthode", "systeme", "système",
}

LEXICAL_STOP = ROLE_STOP | {
    "research", "result", "results", "framework", "evaluation", "validation", "comparison",
    "comparaison", "projet", "verrou", "incertitude", "travaux", "dossier", "consultant",
    "ennodiagnostic", "ennoscholar", "cir", "frascati", "json", "http", "api", "pdf", "docx",
}

# Generic publication-type cues only; no domain-specific terms.
FUNDAMENTAL_CUES = (
    "review", "survey", "overview", "state of the art", "state-of-the-art", "fundamental",
    "fundamentals", "theory", "theoretical framework", "principles", "tutorial", "perspective",
)

TECHNICAL_CUES = (
    "software", "source code", "simulation code", "codebase", "toolkit", "tool chain", "toolchain",
    "simulator", "renderer", "implementation", "implemented", "hardware accelerated",
    "hardware-accelerated", "gpu accelerated", "gpu-accelerated", "fpga", "repository",
    "benchmark suite", "data generator", "dataset generator", "pipeline implementation",
    "prototype system", "technical report", "user guide", "documentation",
)


def _safe_text(x: Any, max_chars: int = 4000) -> str:
    return clean_text(x, max_chars)


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value if str(v or "").strip()]
    return [str(value)] if str(value or "").strip() else []


def _row_terms(value: Any) -> List[str]:
    out: List[str] = []
    rows = value if isinstance(value, list) else ([] if value is None else [value])
    for row in rows:
        if isinstance(row, Mapping):
            for key in ("term_en", "term", "value", "source_phrase"):
                val = str(row.get(key) or "").strip()
                if val:
                    out.append(val)
                    break
        else:
            val = str(row or "").strip()
            if val:
                out.append(val)
    return _unique_terms(out)


def _unique_terms(values: Iterable[str], limit: int = 24) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for value in values:
        value = clean_text(value, 180)
        nv = norm(value)
        if not nv or nv in seen:
            continue
        seen.add(nv)
        out.append(value)
        if len(out) >= limit:
            break
    return out


def _paper_key(article: Dict[str, Any]) -> str:
    doi = _safe_text(article.get("doi"), 220).lower()
    if doi:
        return "doi:" + re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", doi)
    paper_id = _safe_text(
        article.get("paper_id") or article.get("paperId") or article.get("id") or article.get("external_id"),
        260,
    ).lower()
    if paper_id:
        return "id:" + paper_id
    title = norm(article.get("title"))[:260]
    year = str(article.get("year") or "").strip()
    return f"title:{title}:{year}" if title else ""


def dedupe_papers(papers: List[Dict[str, Any]] | None) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for article in papers or []:
        if not isinstance(article, dict):
            continue
        key = _paper_key(article) or ("title:" + norm(article.get("title")))
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(article)
    return out


def _article_text(article: Dict[str, Any]) -> str:
    fields = article.get("fields_of_study") or article.get("fieldsOfStudy") or []
    fields_text = " ".join(map(str, fields)) if isinstance(fields, list) else str(fields or "")
    return " ".join([
        str(article.get("title") or ""),
        str(article.get("abstract") or article.get("tldr") or article.get("summary") or ""),
        str(article.get("venue") or ""),
        fields_text,
    ])


def _role_tokens(text: Any) -> List[str]:
    out: List[str] = []
    for token in tokenize(text):
        nt = norm(token)
        if not nt or nt in ROLE_STOP or len(nt) < 2:
            continue
        out.append(nt)
    return out


def _lexical_tokens(text: Any) -> Set[str]:
    return {
        norm(t) for t in tokenize(text)
        if norm(t) and len(norm(t)) >= 3 and norm(t) not in LEXICAL_STOP
    }


def _token_match(a: str, b: str) -> bool:
    a, b = norm(a), norm(b)
    if not a or not b:
        return False
    if a == b:
        return True
    # Acronyms/short scientific tokens must match exactly; prevents SAR-like
    # accidental matches to unrelated words.
    if len(a) <= 4 or len(b) <= 4:
        return False
    if len(a) >= 6 and len(b) >= 6 and (a.startswith(b[:5]) or b.startswith(a[:5])):
        return True
    return SequenceMatcher(None, a, b).ratio() >= 0.90


def _term_hit(text_norm: str, term: str) -> bool:
    term_norm = norm(term)
    if not term_norm:
        return False
    if re.search(rf"(?<![a-z0-9]){re.escape(term_norm)}(?![a-z0-9])", " " + text_norm + " "):
        return True
    wanted = _role_tokens(term)
    available = _role_tokens(text_norm)
    if not wanted or not available:
        return False
    matched = 0
    used: Set[int] = set()
    for token in wanted:
        best_idx = None
        for idx, candidate in enumerate(available):
            if idx in used:
                continue
            if _token_match(token, candidate):
                best_idx = idx
                break
        if best_idx is not None:
            used.add(best_idx)
            matched += 1
    n = len(wanted)
    if n == 1:
        needed = 1
    elif n == 2:
        needed = 2
    elif n == 3:
        needed = 2
    else:
        needed = max(2, math.ceil(n * 0.60))
    return matched >= needed


def _group_hits(text_norm: str, title_norm: str, terms: List[str]) -> Dict[str, Any]:
    hits = [term for term in terms if _term_hit(text_norm, term)]
    title_hits = [term for term in hits if _term_hit(title_norm, term)]
    return {"hit": bool(hits), "hits": hits[:8], "title_hits": title_hits[:8]}


def _scientific_roles(intent: Dict[str, Any]) -> Tuple[Dict[str, List[str]], bool]:
    plan = intent.get("scientific_query_plan") if isinstance(intent.get("scientific_query_plan"), Mapping) else {}
    structured = bool(plan)

    objects = _row_terms(plan.get("scientific_object")) if plan else []
    independent = _row_terms(plan.get("independent_variables")) if plan else []
    response = _row_terms(plan.get("response_variables")) if plan else []
    operating = _row_terms(plan.get("operating_conditions")) if plan else []
    phenomena = _row_terms(plan.get("phenomena")) if plan else []
    methods = _row_terms(plan.get("methods")) if plan else []
    validation = _row_terms(plan.get("validation_concepts")) if plan else []
    local = _row_terms(plan.get("local_identifiers")) if plan else []

    # Primary concepts/legacy fields enrich object recognition but do not alter
    # the structured scientific relation extracted from the current verrou.
    objects = _unique_terms(objects + _as_list(intent.get("primary_core_concepts")) + _as_list(intent.get("technical_object")))
    phenomena = _unique_terms(phenomena + _as_list(intent.get("phenomenon_anchors")) + _as_list(intent.get("phenomenon")))
    methods = _unique_terms(methods + _as_list(intent.get("method_anchors")) + _as_list(intent.get("methods")))

    if not structured:
        core = _as_list(intent.get("core_concepts"))
        primary = _as_list(intent.get("primary_core_concepts"))
        objects = _unique_terms(primary or _as_list(intent.get("technical_object")) or core[:2])
        secondary = [x for x in core if norm(x) not in {norm(y) for y in objects}]
        # Legacy intents do not expose variable roles; treat their extra core
        # concepts as relation evidence rather than pretending a variable type.
        phenomena = _unique_terms(phenomena + secondary)

    return {
        "object": objects,
        "independent": independent,
        "response": response,
        "operating": operating,
        "phenomena": phenomena,
        "methods": methods,
        "validation": validation,
        "local": local,
    }, structured


def _jaccard(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def _year_bonus(year: Any) -> float:
    try:
        y = int(year)
    except Exception:
        return 0.0
    if y >= 2022:
        return 0.025
    if y >= 2018:
        return 0.018
    if y >= 2012:
        return 0.010
    return 0.0


def _citation_bonus(article: Dict[str, Any]) -> float:
    try:
        c = int(article.get("citation_count") or article.get("citationCount") or 0)
    except Exception:
        c = 0
    return min(math.log10(c + 1) / 100.0, 0.03) if c > 0 else 0.0


def _cue_hits(text_norm: str, cues: Iterable[str]) -> List[str]:
    return [cue for cue in cues if norm(cue) in text_norm]


def _legacy_alias_core_hits(intent: Dict[str, Any], text_norm: str) -> Dict[str, List[str]]:
    core = _as_list(intent.get("core_concepts"))
    primary = set(_as_list(intent.get("primary_core_concepts")))
    aliases = intent.get("concept_aliases") if isinstance(intent.get("concept_aliases"), dict) else {}
    core_hits: List[str] = []
    primary_hits: List[str] = []
    secondary_hits: List[str] = []
    for concept in core:
        candidates = [concept] + _as_list(aliases.get(concept))
        if not any(_term_hit(text_norm, alias) for alias in candidates):
            continue
        core_hits.append(concept)
        if concept in primary:
            primary_hits.append(concept)
        else:
            secondary_hits.append(concept)
    return {"core": core_hits, "primary": primary_hits, "secondary": secondary_hits}


def score_paper(article: Dict[str, Any], intent: Dict[str, Any]) -> Dict[str, Any]:
    article = article or {}
    intent = intent or {}
    title = _safe_text(article.get("title"), 420)
    paper_text = _article_text(article)
    paper_norm = norm(paper_text)
    title_norm = norm(title)

    roles, structured = _scientific_roles(intent)
    role_hits = {name: _group_hits(paper_norm, title_norm, terms) for name, terms in roles.items()}
    legacy = _legacy_alias_core_hits(intent, paper_norm)

    # Primary/object support can come from the structured object role or the
    # legacy primary aliases, useful when the LLM object phrase is verbose.
    object_hit = bool(role_hits["object"]["hit"] or legacy["primary"])
    independent_hit = bool(role_hits["independent"]["hit"])
    response_hit = bool(role_hits["response"]["hit"])
    operating_hit = bool(role_hits["operating"]["hit"])
    phenomena_hit = bool(role_hits["phenomena"]["hit"])
    methods_hit = bool(role_hits["methods"]["hit"])
    validation_hit = bool(role_hits["validation"]["hit"])

    support_flags = {
        "independent": independent_hit,
        "response": response_hit,
        "operating": operating_hit,
        "phenomena": phenomena_hit,
        "methods": methods_hit,
        "validation": validation_hit,
    }
    support_role_count = sum(1 for x in support_flags.values() if x)

    # Central relation: exact variable->response coverage is strongest. A
    # phenomenon phrase can independently express the relation (e.g. sim-to-real
    # generalization). Response+condition is accepted when no independent variable
    # was extracted. This remains completely domain-independent.
    relation_evidence = bool(
        (independent_hit and response_hit)
        or phenomena_hit
        or (response_hit and operating_hit)
        or (independent_hit and validation_hit)
        or (not structured and len(legacy["secondary"]) >= 1 and phenomena_hit)
    )

    title_technical_cues = _cue_hits(title_norm, TECHNICAL_CUES)
    text_technical_cues = _cue_hits(paper_norm, TECHNICAL_CUES)
    fundamental_cues = _cue_hits(title_norm, FUNDAMENTAL_CUES)

    explicit_technical_source = bool(
        article.get("source") in {"technical_catalog", "github", "huggingface"}
        or article.get("source_type") in {"technical_reference", "repository", "software", "dataset"}
        or str(article.get("retrieval_target_category") or "").lower() == "technique"
    )
    technical_eligible = bool(
        explicit_technical_source
        or (object_hit and bool(title_technical_cues))
        or (object_hit and len(text_technical_cues) >= 2)
    )

    # A fundamental source must still be scientifically anchored to the current
    # object/problem; generic "review" papers in another field are rejected.
    # With a structured plan, a paper that clearly covers the same scientific
    # object but none of the central relation roles can still be useful background.
    fundamental_eligible = bool(
        (fundamental_cues and (object_hit or methods_hit or phenomena_hit))
        or (structured and object_hit and support_role_count == 0)
    )

    if structured:
        direct_eligible = bool(object_hit and relation_evidence and support_role_count >= 2)
    else:
        # Legacy intents have no typed variable roles. Two independently matched
        # primary concepts + an explicit phenomenon/validation/method relation are
        # required for Direct; object-only papers remain Connexe.
        legacy_method_hits = role_hits["methods"]["hits"]
        direct_eligible = bool(
            object_hit
            and len(legacy["primary"]) >= 2
            and (phenomena_hit or len(legacy_method_hits) >= 2 or len(legacy["secondary"]) >= 1)
        )

    connexe_eligible = bool(
        (object_hit and support_role_count >= 1)
        or (not structured and object_hit and len(legacy["primary"]) >= 1)
    )

    # Generic lexical relevance is a tie-breaker only, never enough for Direct.
    intent_text = " ".join(sum((terms for terms in roles.values()), []))
    overlap = _jaccard(_lexical_tokens(intent_text), _lexical_tokens(paper_text))
    title_overlap = _jaccard(_lexical_tokens(intent_text), _lexical_tokens(title))

    weights = {
        "object": 0.25,
        "independent": 0.14,
        "response": 0.14,
        "operating": 0.06,
        "phenomena": 0.16,
        "methods": 0.07,
        "validation": 0.06,
    }
    score = 0.0
    for role, weight in weights.items():
        if role == "object":
            score += weight if object_hit else 0.0
        elif role_hits[role]["hit"]:
            score += weight
    score += 0.05 * min(overlap * 5.0, 1.0)
    score += 0.035 * min(title_overlap * 4.0, 1.0)
    score += 0.025 if relation_evidence else 0.0
    score += _year_bonus(article.get("year")) + _citation_bonus(article)
    if fundamental_eligible:
        score += 0.035
    if technical_eligible:
        score += 0.035
    score = max(0.0, min(score, 1.0))

    # Classification precedence: explicit technical implementations are useful
    # technical evidence, not inflated Direct papers. Then strict Direct,
    # Connexe, Fundamental and finally internal Hors sujet.
    if technical_eligible:
        tag = TECHNIQUE_TAG
        reason = "Source Technique : implémentation, outil, simulateur ou infrastructure aligné avec l'objet scientifique."
    elif direct_eligible:
        tag = DIRECT_TAG
        reason = "Article Direct : même objet scientifique et relation centrale du verrou soutenue par plusieurs rôles indépendants."
    elif connexe_eligible:
        tag = CONNEXE_TAG
        reason = "Article Connexe : même objet scientifique avec au moins un axe utile, mais la relation centrale du verrou n'est pas entièrement établie."
    elif fundamental_eligible:
        tag = FONDAMENTAL_TAG
        reason = "Article Fondamental : théorie, revue ou principes généraux scientifiquement ancrés au verrou."
    else:
        tag = HORS_SUJET_TAG
        reason = "Article Hors sujet : chevauchement insuffisant avec l'objet et les rôles scientifiques du verrou."

    core_hits = _unique_terms(legacy["core"] + role_hits["object"]["hits"])
    primary_hits = _unique_terms(legacy["primary"] + role_hits["object"]["hits"])
    secondary_hits = _unique_terms(
        legacy["secondary"]
        + role_hits["independent"]["hits"]
        + role_hits["response"]["hits"]
        + role_hits["operating"]["hits"]
    )

    return {
        "relevance_score": round(score, 4),
        "tag": tag,
        "reason": reason,
        "score_details": {
            "ranker_version": VERSION,
            "structured_role_plan_used": structured,
            "role_hits": {name: data["hits"] for name, data in role_hits.items()},
            "role_title_hits": {name: data["title_hits"] for name, data in role_hits.items()},
            "object_role_hit": object_hit,
            "independent_role_hit": independent_hit,
            "response_role_hit": response_hit,
            "operating_role_hit": operating_hit,
            "phenomenon_role_hit": phenomena_hit,
            "method_role_hit": methods_hit,
            "validation_role_hit": validation_hit,
            "support_role_count": support_role_count,
            "relation_evidence": relation_evidence,
            "problem_evidence": relation_evidence,
            "direct_eligible": direct_eligible,
            "connexe_eligible": connexe_eligible,
            "fundamental_eligible": fundamental_eligible,
            "technical_eligible": technical_eligible,
            "technical_cues": _unique_terms(text_technical_cues, 8),
            "fundamental_cues": _unique_terms(fundamental_cues, 8),
            "core_hits": core_hits[:12],
            "primary_core_hits": primary_hits[:12],
            "secondary_core_hits": secondary_hits[:12],
            "core_concept_hit_count": len(core_hits),
            "primary_core_hit_count": len(primary_hits),
            "secondary_core_hit_count": len(secondary_hits),
            "method_anchor_hit_count": len(role_hits["methods"]["hits"]),
            "independent_method_hits": role_hits["methods"]["hits"][:12],
            "phenomenon_anchor_hit_count": len(role_hits["phenomena"]["hits"]),
            "specific_anchor_count": support_role_count + (1 if object_hit else 0),
            "object_overlap_count": 1 if object_hit else 0,
            "overlap": round(overlap, 4),
            "title_overlap": round(title_overlap, 4),
            "domain_contradiction": bool(not object_hit and support_role_count == 0),
            "domain_specific_ontology_used": False,
            "year_bonus": round(_year_bonus(article.get("year")), 4),
            "citation_bonus": round(_citation_bonus(article), 4),
        },
    }


def rank_papers_for_intent(
    papers: List[Dict[str, Any]] | None,
    intent: Dict[str, Any],
    top_n: int = 12,
) -> List[Dict[str, Any]]:
    clean = dedupe_papers(papers)
    ranked: List[Dict[str, Any]] = []
    for paper in clean:
        if not isinstance(paper, dict):
            continue
        item = dict(paper)
        try:
            item.update(score_paper(item, intent))
        except Exception as exc:
            item["tag"] = HORS_SUJET_TAG
            item["relevance_score"] = 0.0
            item["reason"] = "Article non classé à cause d'une erreur du garde scientifique."
            item["score_details"] = {"ranker_version": VERSION, "error": repr(exc)}
        ranked.append(item)

    order = {DIRECT_TAG: 4, CONNEXE_TAG: 3, FONDAMENTAL_TAG: 2, TECHNIQUE_TAG: 1, HORS_SUJET_TAG: 0}
    ranked.sort(
        key=lambda x: (
            order.get(str(x.get("tag") or ""), 0),
            float(x.get("relevance_score") or 0.0),
            int(x.get("citation_count") or 0),
            int(x.get("year") or 0) if str(x.get("year") or "").isdigit() else 0,
        ),
        reverse=True,
    )
    return ranked[:max(1, int(top_n or 12))]
