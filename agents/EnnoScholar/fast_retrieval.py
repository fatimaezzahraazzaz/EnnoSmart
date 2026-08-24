# -*- coding: utf-8 -*-
from __future__ import annotations

"""EnnoScholar V167.6 — adaptive recall, levelled queries, and diversified 50-source corpus retrieval.

This module is intentionally domain-agnostic.  It does not contain project,
client, technology or scientific-field vocabularies.  It only orchestrates:
- 4–5 evidence-grounded query angles from validated scientific roles;
- a small first wave over broad bibliographic providers;
- conditional second-wave expansion when coverage is still insufficient;
- deterministic routing and latency/accounting metadata.

The actual provider clients, rate-limit behaviour, ranking and article access
pipelines remain owned by the existing EnnoScholar modules.
"""

import os
import re
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

VERSION = "v167_6_adaptive_recall_50_corpus"


# ---------------------------------------------------------------------------
# Generic text helpers — no domain vocabulary.
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[A-Za-z0-9À-ÿ][A-Za-z0-9À-ÿ_./+%-]*")


def _clean(value: Any, max_chars: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:max_chars].strip()


def _norm(value: Any) -> str:
    return " ".join(_clean(value, 600).lower().split())


def _tokens(value: Any) -> List[str]:
    return [m.group(0).lower() for m in _WORD_RE.finditer(_clean(value, 600))]


def _terms(rows: Any) -> List[str]:
    if not isinstance(rows, list):
        return []
    out: List[str] = []
    seen = set()
    for row in rows:
        if isinstance(row, Mapping):
            term = _clean(row.get("term_en") or row.get("value"), 120)
        else:
            term = _clean(row, 120)
        key = _norm(term)
        if term and key and key not in seen:
            seen.add(key)
            out.append(term)
    return out


_JOIN_STOPWORDS = {"the", "a", "an", "of", "for", "under", "with", "on", "in"}

def _join(parts: Iterable[Any], max_words: int = 11) -> str:
    words: List[str] = []
    seen = set()
    for part in parts:
        values = part if isinstance(part, (list, tuple)) else [part]
        for value in values:
            for word in _tokens(value):
                if word in _JOIN_STOPWORDS or word in seen:
                    continue
                seen.add(word)
                words.append(word)
                if len(words) >= max_words:
                    return " ".join(words)
    return " ".join(words)


def _similarity(a: str, b: str) -> float:
    sa = set(_tokens(a))
    sb = set(_tokens(b))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / max(1, len(sa | sb))


_UUID_RE_V1673 = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b")
_LONG_HEX_RE_V1673 = re.compile(r"^[0-9a-f]{16,}$", re.I)
_GENERATED_SUFFIX_RE_V1673 = re.compile(r"(?:^|[_-])[0-9a-f]{8,}$", re.I)
_NOISE_LITERALS_V1673 = {"true", "false", "null", "none", "undefined", "nan"}
_NOISE_PREFIXES_V1673 = ("session_", "run_", "trace_", "request_", "job_", "task_", "chunk_")


def is_obvious_noise_token(token: str) -> bool:
    """Return True only for structural/generated noise.

    V167.3 deliberately does *not* classify project vocabulary as scientific vs
    local/public/private. If a token can carry project/search meaning, it stays.
    We remove only obvious machine-generated noise.
    """
    raw = _clean(token, 180).strip(" ,;:()[]{}<>\"'")
    low = raw.lower()
    if not raw:
        return True
    if low in _NOISE_LITERALS_V1673:
        return True
    if _UUID_RE_V1673.fullmatch(raw):
        return True
    if any(low.startswith(prefix) for prefix in _NOISE_PREFIXES_V1673):
        return True
    compact = re.sub(r"[^0-9a-f]", "", low)
    if _LONG_HEX_RE_V1673.fullmatch(compact) and len(compact) >= 20:
        return True
    # Typical extracted-file/chunk identifiers ending in a long generated hash.
    if len(raw) >= 24 and "_" in raw and _GENERATED_SUFFIX_RE_V1673.search(raw):
        return True
    # Paths and opaque URLs are not query concepts.
    if "\\" in raw or raw.startswith("http://") or raw.startswith("https://"):
        return True
    return False


def sanitize_query_text(query: Any, max_words: int = 14) -> str:
    """Remove only obvious noise and preserve value-bearing project terms.

    Examples intentionally preserved: TGM100, 300bar, R290, ViT, product/tool
    names. UUIDs, run/session IDs, hashes and generated extraction identifiers
    are removed.
    """
    text = _clean(query, 360)
    text = _UUID_RE_V1673.sub(" ", text)
    words: List[str] = []
    seen = set()
    for token in _tokens(text):
        if is_obvious_noise_token(token):
            continue
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        words.append(token)
        if len(words) >= max_words:
            break
    return " ".join(words)


def _local_values(plan: Mapping[str, Any]) -> List[str]:
    values: List[str] = []
    for row in plan.get("local_identifiers") or []:
        if isinstance(row, Mapping):
            value = _clean(row.get("value"), 100)
        else:
            value = _clean(row, 100)
        if value and not is_obvious_noise_token(value):
            values.append(value)
    return values


def _remove_phrase(text: str, phrase: str) -> str:
    out = " " + _norm(text) + " "
    target = _norm(phrase)
    if target:
        out = re.sub(rf"(?<![a-z0-9]){re.escape(target)}(?![a-z0-9])", " ", out)
    return sanitize_query_text(out, max_words=12)


def _object_variants(plan: Mapping[str, Any]) -> List[str]:
    """Return broad + contextual object variants without deleting information.

    A project-specific term is *kept* in the plan and may appear in a contextual
    query. We merely avoid forcing the same optional term into every query.
    """
    objects = _terms(plan.get("scientific_object") or [])
    if not objects:
        return []
    variants: List[str] = []
    for raw_object in objects[:4]:
        full = sanitize_query_text(raw_object, max_words=7)
        broad = full
        for value in _local_values(plan):
            candidate = _remove_phrase(broad, value)
            if candidate:
                broad = candidate
        for value in (broad, full):
            key = _norm(value)
            if value and key not in {_norm(x) for x in variants}:
                variants.append(value)
    return variants


def _role_terms_for_safety(plan: Mapping[str, Any]) -> Tuple[List[str], List[str], List[str]]:
    domains = [
        sanitize_query_text(x, max_words=6)
        for x in _terms(plan.get("domain_anchors") or [])
        if sanitize_query_text(x, max_words=6)
    ]
    objects = _object_variants(plan)
    axes = (
        _terms(plan.get("independent_variables") or [])
        + _terms(plan.get("response_variables") or [])
        + _terms(plan.get("phenomena") or [])
        + _terms(plan.get("operating_conditions") or [])
        + _terms(plan.get("methods") or [])
        + _terms(plan.get("validation_concepts") or [])
    )
    return domains, objects, [sanitize_query_text(x, max_words=8) for x in axes if sanitize_query_text(x, max_words=8)]


def query_is_useful(query: str, plan: Mapping[str, Any]) -> bool:
    """Simple V167.3 policy: useful vs obvious noise.

    Local/project terms are allowed. Every query is judged independently. A bad
    query is skipped; it never invalidates its siblings or triggers an LLM retry.
    """
    q = sanitize_query_text(query, max_words=14)
    tokens = _tokens(q)
    if len(tokens) < 3 or len(tokens) > 14:
        return False
    domains, objects, axes = _role_terms_for_safety(plan)
    if not domains or not objects or not axes:
        return False
    qset = set(_tokens(q))

    def hit(term: str, threshold: float = 0.5) -> bool:
        tt = set(_tokens(term))
        return bool(tt) and (len(tt & qset) / max(1, len(tt))) >= threshold

    domain_hit = any(hit(domain, 0.67) for domain in domains)
    object_hit = any(hit(obj, 0.6) for obj in objects)
    axis_hits = sum(1 for axis in axes if hit(axis, 0.5))
    return bool(domain_hit and object_hit and axis_hits >= 1)


def _safe_query(query: str, plan: Mapping[str, Any]) -> bool:
    return query_is_useful(query, plan)


# ---------------------------------------------------------------------------
# Query portfolio
# ---------------------------------------------------------------------------

_GENERIC_MODIFIERS = {
    # These are retrieval intent descriptors, not project/domain concepts.
    "experimental": ["experimental study"],
    "validation": ["experimental validation"],
    "review": ["review"],
    "operating": ["operating conditions"],
}


def build_query_portfolio(
    plan: Mapping[str, Any],
    base_queries: Sequence[Mapping[str, Any]] | None = None,
    target: int = 6,
) -> List[Dict[str, Any]]:
    """Build a six-query scientific breadth ladder from one validated plan.

    The LLM extracts evidence-grounded concepts once. Python then creates:
      1) strict_core_a  -> closest causal/relational formulation;
      2) strict_core_b  -> alternative close formulation using phenomenon,
                           operating condition or validation vocabulary;
      3) connexe_a      -> secondary variable/response around the same object;
      4) connexe_b      -> method/secondary response transfer literature;
      5) fundamental    -> broader mechanism/review literature;
      6) technical      -> method/project/tool/protocol-oriented context.

    There is deliberately no hors-sujet query family.
    """
    target = max(1, min(int(target or 6), 6))
    objects = _object_variants(plan)
    domain = [
        sanitize_query_text(x, 6)
        for x in _terms(plan.get("domain_anchors") or [])
        if sanitize_query_text(x, 6)
    ][:1]
    if not domain or not objects:
        return []
    domain_tokens = set(_tokens(" ".join(domain)))
    objects.sort(
        key=lambda value: (
            len(domain_tokens & set(_tokens(value))),
            len(set(_tokens(value))),
        ),
        reverse=True,
    )
    broad_obj = objects[0]
    contextual_obj = objects[-1]

    def anchored(parts: List[Any], max_words: int) -> str:
        return _join([domain, *parts], max_words=max_words)

    indep = [sanitize_query_text(x, 8) for x in _terms(plan.get("independent_variables") or [])]
    resp = [sanitize_query_text(x, 8) for x in _terms(plan.get("response_variables") or [])]
    phen = [sanitize_query_text(x, 10) for x in _terms(plan.get("phenomena") or [])]
    operating = [sanitize_query_text(x, 10) for x in _terms(plan.get("operating_conditions") or [])]
    methods = [sanitize_query_text(x, 10) for x in _terms(plan.get("methods") or [])]
    validation = [sanitize_query_text(x, 10) for x in _terms(plan.get("validation_concepts") or [])]
    indep = [x for x in indep if x]
    resp = [x for x in resp if x]
    phen = [x for x in phen if x]
    operating = [x for x in operating if x]
    methods = [x for x in methods if x]
    validation = [x for x in validation if x]

    primary_i = indep[:1]
    primary_r = resp[:1]
    secondary_i = indep[1:2]
    secondary_r = resp[1:2]

    out: List[Dict[str, Any]] = []
    seen = set()
    level_meta = {
        "strict_core_a": ("Direct", "proche_stricte_a", 1.00),
        "strict_core_b": ("Direct", "proche_stricte_b", 0.99),
        "connexe_a": ("Connexe", "connexe_a", 0.88),
        "connexe_b": ("Connexe", "connexe_b", 0.84),
        "fundamental": ("Fondamental", "fondamental", 0.76),
        "technical": ("Technique", "technique", 0.72),
    }

    def add(query: str, family: str, source: str = "adaptive_portfolio") -> None:
        query = sanitize_query_text(query, max_words=14)
        key = _norm(query)
        if not key or key in seen or not query_is_useful(query, plan):
            return
        # Keep semantically distinct levels. Collapse only near-identical strings.
        if any(_similarity(query, row["query"]) >= 0.97 for row in out):
            return
        target_category, search_level, strictness = level_meta.get(
            family, ("Connexe", family, 0.80)
        )
        kind_map = {
            "strict_core_a": "v167_6_proche_stricte_a",
            "strict_core_b": "v167_6_proche_stricte_b",
            "connexe_a": "v167_6_connexe_a",
            "connexe_b": "v167_6_connexe_b",
            "fundamental": "v167_6_fondamental",
            "technical": "v167_6_technique",
        }
        seen.add(key)
        out.append({
            "query": query,
            "kind": kind_map.get(family, f"v167_6_{family}"),
            "family": family,
            "search_level": search_level,
            "target_category": target_category,
            "strictness": strictness,
            "planner_version": VERSION,
            "portfolio_source": source,
        })

    # Strict A: direct relation between object, main input/cause and response.
    add(anchored([[broad_obj], primary_i, primary_r], max_words=14), "strict_core_a")

    # Strict B: alternate scientific formulation. Prefer a validated phenomenon,
    # then operating conditions, then method/validation vocabulary. This is an
    # alternate expression of the same problem, not a broader domain query.
    if phen:
        strict_b = anchored([[objects[1] if len(objects) > 1 else broad_obj], phen[:1], primary_i, operating[:1]], max_words=14)
    elif operating:
        strict_b = anchored([[broad_obj], primary_i, primary_r, operating[:1]], max_words=14)
    elif methods:
        strict_b = anchored([[broad_obj], primary_i, primary_r, methods[:1]], max_words=14)
    else:
        strict_b = anchored([[broad_obj], primary_i, primary_r, validation[:1]], max_words=14)
    add(strict_b, "strict_core_b")

    # Connexe A: same object, secondary causal/input axis while retaining the
    # main response. Useful for transferable operating/physics literature.
    if secondary_i:
        connexe_a = anchored([[broad_obj], secondary_i, primary_r], max_words=14)
    elif secondary_r:
        connexe_a = anchored([[broad_obj], primary_i, secondary_r], max_words=14)
    elif methods:
        connexe_a = anchored([[broad_obj], primary_i, methods[:1]], max_words=14)
    else:
        connexe_a = anchored([[broad_obj], phen[:1], primary_r], max_words=14)
    add(connexe_a, "connexe_a")

    # Connexe B: deliberately use a different validated axis than Connexe A.
    if secondary_r:
        connexe_b = anchored([[contextual_obj], primary_i, secondary_r], max_words=14)
    elif methods:
        connexe_b = anchored([[contextual_obj], methods[:1], primary_r], max_words=14)
    elif validation:
        connexe_b = anchored([[contextual_obj], primary_i, validation[:1]], max_words=14)
    else:
        connexe_b = anchored([[contextual_obj], operating[:1], primary_r], max_words=14)
    add(connexe_b, "connexe_b")

    # Fundamental: mechanisms/principles/review, not a target for Direct papers.
    if phen:
        fundamental = anchored([[broad_obj], ["fundamentals", "review"], phen[:1]], max_words=14)
    elif methods:
        fundamental = anchored([[broad_obj], ["fundamentals", "review"], methods[:1]], max_words=14)
    else:
        fundamental = anchored([[broad_obj], primary_i, primary_r, ["fundamentals", "review"]], max_words=14)
    add(fundamental, "fundamental")

    # Technical: keep value-bearing local/project/tool terms and validated
    # method/protocol terms, especially for repository-like providers.
    technical_parts: List[Any] = [[contextual_obj]]
    if methods:
        technical_parts.append(methods[:1])
    elif validation:
        technical_parts.append(validation[:1])
    if not methods:
        technical_parts.append(primary_i)
    technical_parts.append(primary_r)
    add(anchored(technical_parts, max_words=14), "technical")

    # Conservative fill: never invent domain terms and never create hors-sujet.
    fillers = [
        ("strict_core_b", anchored([[broad_obj], primary_i, primary_r, operating[:1] or methods[:1]], max_words=14)),
        ("connexe_a", anchored([[broad_obj], secondary_i or primary_i, secondary_r or primary_r], max_words=14)),
        ("connexe_b", anchored([[contextual_obj], methods[:1] or validation[:1], primary_r], max_words=14)),
        ("fundamental", anchored([[broad_obj], primary_r, ["fundamentals", "review"]], max_words=14)),
        ("technical", anchored([[contextual_obj], validation[:1] or methods[:1], primary_i], max_words=14)),
    ]
    existing_families = {row["family"] for row in out}
    for family, query in fillers:
        if len(out) >= target:
            break
        if family in existing_families:
            continue
        before = len(out)
        add(query, family, "adaptive_fill")
        if len(out) > before:
            existing_families.add(family)

    order = {
        "strict_core_a": 0,
        "strict_core_b": 1,
        "connexe_a": 2,
        "connexe_b": 3,
        "fundamental": 4,
        "technical": 5,
    }
    out.sort(key=lambda row: order.get(str(row.get("family") or ""), 99))

    # Backward-compatible 5-query view for V167.3-V167.5 callers/tests.
    # V167.6 itself requests target=6, so production gets the full new ladder.
    if target <= 5:
        wanted = ["strict_core_a", "strict_core_b", "connexe_a", "fundamental", "technical"]
        compat_names = {
            "strict_core_a": ("strict_core", "proche_stricte", "v167_5_proche_stricte"),
            "strict_core_b": ("strict_conditions", "proche_conditions", "v167_5_proche_conditions"),
            "connexe_a": ("connexe", "connexe", "v167_5_connexe"),
            "fundamental": ("fundamental", "fondamental", "v167_5_fondamental"),
            "technical": ("technical", "technique", "v167_5_technique"),
        }
        compat: List[Dict[str, Any]] = []
        by_new_family = {str(row.get("family") or ""): row for row in out}
        for fam in wanted:
            row = by_new_family.get(fam)
            if not row:
                continue
            item = dict(row)
            old_family, old_level, old_kind = compat_names[fam]
            item["family"] = old_family
            item["search_level"] = old_level
            item["kind"] = old_kind
            compat.append(item)
        return compat[:target]

    return out[:target]


# ---------------------------------------------------------------------------
# Provider coverage + adaptive depth
# ---------------------------------------------------------------------------

# Capability routing only. No project/domain vocabulary is encoded here.
# All enabled providers get one first-pass opportunity so the corpus is not
# dominated by a single source. Deeper calls are conditional.
_PRIMARY_BIBLIO = ("openalex", "crossref", "semantic_scholar")


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except Exception:
        value = default
    return max(lo, min(value, hi))


def _query_text(row: Mapping[str, Any]) -> str:
    return _clean(row.get("query"), 240)


def _family(row: Mapping[str, Any]) -> str:
    value = _clean(row.get("family") or row.get("kind") or "auto", 80)
    return re.sub(r"^v\d+(?:_\d+)*_", "", value) or "auto"


def _round_robin_query(qrows: Sequence[Mapping[str, Any]], idx: int) -> Mapping[str, Any]:
    if not qrows:
        return {}
    return qrows[idx % len(qrows)]


def build_fast_retrieval_plan(
    queries: Sequence[Mapping[str, Any]],
    scientific_sources: Sequence[str],
    artifact_sources: Sequence[str],
    requested_limit: int,
) -> Dict[str, Any]:
    """Route levelled queries over all enabled providers without a cartesian explosion.

    Wave 1 gives every enabled provider one concurrent opportunity, but routes a
    search level suited to corpus diversity. Wave 2 gives at most one additional
    query to each scientific provider only when the unique candidate pool is
    still too small/diversity is weak.

    This keeps the 11-provider breadth while bounding calls to roughly 11 + <=9
    instead of 5 x 11.
    """
    active_scientific = list(dict.fromkeys(str(x) for x in scientific_sources if x))
    active_artifacts = list(dict.fromkeys(str(x) for x in artifact_sources if x))
    qrows = [dict(q) for q in queries if isinstance(q, Mapping) and _query_text(q)]

    wave1_limit = min(
        max(1, int(requested_limit or 20)),
        _env_int("ENNOSCHOLAR_COVERAGE_LIMIT", 25, 10, 35),
    )
    wave2_limit = min(
        max(1, int(requested_limit or 20)),
        _env_int("ENNOSCHOLAR_DEPTH_LIMIT", 20, 8, 30),
    )

    target_final_useful = _env_int("ENNOSCHOLAR_TARGET_USEFUL_PER_VERROU", 50, 20, 80)
    raw_candidate_target = _env_int(
        "ENNOSCHOLAR_RAW_CANDIDATE_TARGET",
        max(150, int(round(target_final_useful * 3.0))),
        target_final_useful,
        220,
    )
    min_success_sources = _env_int("ENNOSCHOLAR_MIN_PROVIDER_DIVERSITY", 5, 2, 9)

    by_family = {_family(q): q for q in qrows}
    # Tolerate older names when an existing cached/legacy row slips through.
    aliases = {
        "strict_core_a": ["strict_core_a", "strict_core", "direct"],
        "strict_core_b": ["strict_core_b", "strict_conditions", "operating_conditions"],
        "connexe_a": ["connexe_a", "connexe", "secondary_variable"],
        "connexe_b": ["connexe_b", "secondary_response", "experimental"],
        "fundamental": ["fundamental", "mechanism", "review"],
        "technical": ["technical", "project_context", "validation"],
    }

    def pick(level: str, fallback_index: int = 0) -> Mapping[str, Any]:
        for name in aliases.get(level, [level]):
            if name in by_family:
                return by_family[name]
        if qrows:
            return qrows[fallback_index % len(qrows)]
        return {}

    strict_core_a = pick("strict_core_a", 0)
    strict_core_b = pick("strict_core_b", 1)
    connexe_a = pick("connexe_a", 2)
    connexe_b = pick("connexe_b", 3)
    fundamental = pick("fundamental", 4)
    technical = pick("technical", 5)

    # Provider capability routing is generic (bibliographic vs repository), not
    # project/domain-specific. Every enabled provider still participates.
    preferred_level = {
        "openalex": "strict_core_a",
        "semantic_scholar": "strict_core_b",
        "crossref": "strict_core_a",
        "doaj": "connexe_a",
        "arxiv": "fundamental",
        "hal": "fundamental",
        "core": "connexe_b",
        "europe_pmc": "strict_core_b",
        "zenodo": "technical",
    }
    level_rows = {
        "strict_core_a": strict_core_a,
        "strict_core_b": strict_core_b,
        "connexe_a": connexe_a,
        "connexe_b": connexe_b,
        "fundamental": fundamental,
        "technical": technical,
    }

    wave1: List[Dict[str, Any]] = []
    wave2: List[Dict[str, Any]] = []

    for idx, provider in enumerate(active_scientific):
        level = preferred_level.get(provider)
        if level is None:
            level = ("strict_core_a", "strict_core_b", "connexe_a", "connexe_b", "fundamental")[idx % 5]
        q = level_rows.get(level) or _round_robin_query(qrows, idx)
        if not q:
            continue
        wave1.append({
            "source": provider,
            "query": _query_text(q),
            "family": _family(q),
            "search_level": q.get("search_level") or level,
            "target_category": q.get("target_category"),
            "limit": wave1_limit,
            "wave": 1,
            "artifact": False,
        })

    # Repository/artifact providers receive the technical query by default.
    for provider in active_artifacts:
        q = technical or _round_robin_query(qrows, 5)
        if not q:
            continue
        wave1.append({
            "source": provider,
            "query": _query_text(q),
            "family": _family(q),
            "search_level": q.get("search_level") or "technique",
            "target_category": "Technique",
            "limit": _env_int("ENNOSCHOLAR_TECHNICAL_LIMIT", 12, 4, 18),
            "wave": 1,
            "artifact": True,
        })

    # Depth wave: one additional, different level/provider at most. We favour
    # strict/connected literature first, then fundamental breadth. Technical
    # sources were already queried in wave 1 and do not add a third stage.
    max_wave2_calls = _env_int(
        "ENNOSCHOLAR_DEPTH_MAX_CALLS",
        min(9, len(active_scientific)),
        0,
        12,
    )
    second_level_cycle = ("strict_core_b", "connexe_a", "connexe_b", "fundamental", "strict_core_a")
    first_family_by_provider = {str(r["source"]): str(r.get("family") or "") for r in wave1 if not r.get("artifact")}
    for pidx, provider in enumerate(active_scientific):
        if len(wave2) >= max_wave2_calls:
            break
        first_family = first_family_by_provider.get(provider, "")
        chosen = None
        for offset in range(len(second_level_cycle)):
            level = second_level_cycle[(pidx + offset) % len(second_level_cycle)]
            candidate = level_rows.get(level)
            if candidate and _family(candidate) != first_family:
                chosen = candidate
                break
        if chosen is None:
            chosen = _round_robin_query(qrows, pidx + 1)
        if not chosen:
            continue
        wave2.append({
            "source": provider,
            "query": _query_text(chosen),
            "family": _family(chosen),
            "search_level": chosen.get("search_level"),
            "target_category": chosen.get("target_category"),
            "limit": wave2_limit,
            "wave": 2,
            "artifact": False,
        })

    return {
        "version": VERSION,
        "strategy": "adaptive_recall_levelled_coverage_then_conditional_depth",
        "query_count": len(qrows),
        "query_families": list(dict.fromkeys(_family(q) for q in qrows)),
        "query_levels": [
            {"family": _family(q), "level": q.get("search_level"), "target_category": q.get("target_category")}
            for q in qrows
        ],
        "target_final_useful": target_final_useful,
        "raw_candidate_target": raw_candidate_target,
        "min_success_sources": min_success_sources,
        "scientific_provider_count": len(active_scientific),
        "artifact_provider_count": len(active_artifacts),
        "providers_available_count": len(active_scientific) + len(active_artifacts),
        "wave1_sources": active_scientific,
        "wave1_artifact_sources": active_artifacts,
        "wave2_sources": active_scientific,
        "wave1_jobs": wave1,
        "wave2_jobs": wave2,
        "artifact_jobs": [],
        "wave1_limit": wave1_limit,
        "wave2_limit": wave2_limit,
        "wave1_calls": len(wave1),
        "wave2_calls_max": len(wave2),
        "artifact_calls_max": len(active_artifacts),
        "old_cartesian_calls_avoided_estimate": max(
            0,
            len(qrows) * (len(active_scientific) + len(active_artifacts)) - len(wave1),
        ),
    }


def should_expand_wave2(
    *,
    unique_candidates: int,
    successful_wave1_sources: int,
    wave1_plan: Mapping[str, Any],
) -> Tuple[bool, str, int]:
    """Expand only if volume OR provider diversity is insufficient."""
    target = int(wave1_plan.get("raw_candidate_target") or 100)
    min_sources = int(wave1_plan.get("min_success_sources") or 4)
    has_wave2 = bool(wave1_plan.get("wave2_jobs"))
    if not has_wave2:
        return False, "no_depth_jobs", target
    if unique_candidates < target:
        return True, f"unique_candidates_below_target:{unique_candidates}<{target}", target
    if successful_wave1_sources < min_sources:
        return True, f"provider_diversity_below_target:{successful_wave1_sources}<{min_sources}", target
    return False, "coverage_sufficient", target



def should_run_adaptive_refinement(
    *,
    unique_candidates: int,
    retrieval_plan: Mapping[str, Any],
) -> Tuple[bool, str, int]:
    """Trigger one vocabulary-refinement step only for a weak corpus.

    It is intentionally separate from query planning repairs. A valid scientific
    plan is never sent back for repair simply because retrieval recall is low.
    """
    target = int(retrieval_plan.get("raw_candidate_target") or 150)
    trigger_ratio = float(os.getenv("ENNOSCHOLAR_REFINEMENT_TRIGGER_RATIO", "0.80") or 0.80)
    trigger = max(40, int(round(target * max(0.40, min(trigger_ratio, 0.95)))))
    enabled = str(os.getenv("ENNOSCHOLAR_ADAPTIVE_REFINEMENT", "1")).lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return False, "adaptive_refinement_disabled", trigger
    if unique_candidates >= trigger:
        return False, f"candidate_pool_sufficient:{unique_candidates}>={trigger}", trigger
    return True, f"candidate_pool_below_refinement_trigger:{unique_candidates}<{trigger}", trigger


def build_refinement_jobs(
    refinement_queries: Sequence[Mapping[str, Any]],
    scientific_sources: Sequence[str],
    *,
    limit: int = 25,
) -> List[Dict[str, Any]]:
    """Route at most four adaptive queries to high-yield enabled providers.

    No provider/domain terms are invented here. Provider names only express API
    capabilities. This stage is conditional and therefore does not recreate a
    6 x 11 cartesian search.
    """
    rows = [dict(q) for q in refinement_queries if isinstance(q, Mapping) and _query_text(q)]
    if not rows:
        return []
    enabled = list(dict.fromkeys(str(x) for x in scientific_sources if x))
    preferred = ["openalex", "core", "crossref", "semantic_scholar", "doaj", "hal", "arxiv"]
    providers = [p for p in preferred if p in enabled]
    max_calls = _env_int("ENNOSCHOLAR_REFINEMENT_MAX_CALLS", 4, 1, 6)
    jobs: List[Dict[str, Any]] = []
    for idx, provider in enumerate(providers[:max_calls]):
        row = rows[idx % len(rows)]
        jobs.append({
            "source": provider,
            "query": _query_text(row),
            "family": row.get("family") or "adaptive_refinement",
            "search_level": row.get("search_level") or "proche_refinement",
            "target_category": row.get("target_category") or "Direct",
            "limit": max(8, min(int(limit or 25), 35)),
            "wave": 3,
            "artifact": False,
            "adaptive_refinement": True,
        })
    return jobs


def article_has_core_alignment(article: Mapping[str, Any], plan: Mapping[str, Any]) -> bool:
    """Strict lexical guard for citation-expansion seeds, independent of ranker tags."""
    title = _clean(article.get("title"), 500)
    abstract = _clean(article.get("abstract"), 3000)
    text_tokens = set(_tokens(f"{title} {abstract}"))
    if not text_tokens:
        return False
    objects, axes = _role_terms_for_safety(plan)

    def phrase_hit(term: str, threshold: float = 0.5) -> bool:
        tt = set(_tokens(term))
        return bool(tt) and (len(tt & text_tokens) / max(1, len(tt))) >= threshold

    object_hit = any(phrase_hit(obj, 0.5) for obj in objects)
    axis_hits = sum(1 for axis in axes if phrase_hit(axis, 0.5))
    # Require the object + at least two grounded axes before using an article as
    # a citation-graph seed. This avoids propagating current ranker false positives.
    return bool(object_hit and axis_hits >= 2)

def build_job_tuples(
    plan_jobs: Sequence[Mapping[str, Any]],
    source_functions: Mapping[str, Any],
    artifact_functions: Mapping[str, Any],
) -> List[Tuple[str, str, Any, bool, int]]:
    out: List[Tuple[str, str, Any, bool, int]] = []
    for row in plan_jobs:
        source = str(row.get("source") or "")
        is_artifact = bool(row.get("artifact"))
        funcs = artifact_functions if is_artifact else source_functions
        func = funcs.get(source)
        query = _clean(row.get("query"), 240)
        if not source or not query or func is None:
            continue
        out.append((source, query, func, is_artifact, int(row.get("limit") or 10)))
    return out
