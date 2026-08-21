# -*- coding: utf-8 -*-
from __future__ import annotations

"""Universal EnnoScholar scientific intent builder.

The scientific vocabulary is extracted exclusively from the current lock title,
its linked EnnoDiagnostic evidence/passages, and explicit keywords/concepts
already present in that payload.  No project/domain/technology ontology is
encoded here.
"""

import re
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Tuple

from .utils import clean_text, clean_title, dedupe_keep_order, flatten_text, norm, tokenize


META_TERMS = {
    "cir", "frascati", "ennodiagnostic", "ennoscholar", "consultant", "dossier",
    "projet", "project", "document", "documents", "source", "sources", "preuve",
    "preuves", "evidence", "verrou", "lock", "scientifique", "scientific",
    "technique", "technical", "qualification", "nlp", "rag", "llm", "api",
    "json", "pdf", "docx",
}

GENERIC_NOISE = META_TERMS | {
    "question", "analyse", "analysis", "study", "paper", "article", "research",
    "result", "results", "resultat", "resultats", "possible", "probable",
    "notamment", "ainsi", "however", "therefore", "although", "also", "very",
    "not", "non", "pas", "plus", "moins", "even", "especially",
    # universal discourse / document artefacts: never scientific anchors by themselves
    "intrinsically", "thus", "therefore", "hence", "herein", "whereas",
    "grounded", "version", "cite", "cited", "citation", "citations",
    "figure", "fig", "table", "section", "appendix", "respectively",
    "namely", "according", "indeed", "overview", "introduction",
    "conclusion", "conclusions", "ie", "eg",
    "impossibilite", "impossibility",
}

FR_FUNCTION_WORDS = {
    "sur", "avec", "sans", "dans", "pour", "des", "les", "une", "un", "aux",
    "du", "de", "la", "le", "et", "ou", "entre", "vers", "par", "en", "au",
    "ce", "cette", "ces", "leur", "leurs", "qui", "que", "dont",
}
EN_FUNCTION_WORDS = {
    "the", "with", "without", "from", "into", "for", "and", "or", "between",
    "this", "that", "these", "those", "their", "by", "in", "on", "of", "to",
    "which", "where", "when",
}

# Generic scientific relation words.  They help label the problem but do not
# introduce domain concepts into the query vocabulary.
GENERIC_PHENOMENON_WORDS = {
    "uncertainty", "incertitude", "limitation", "limitations", "instability",
    "instabilite", "instabilité", "variability", "variabilite", "variabilité",
    "error", "erreur", "bias", "biais", "gap", "ecart", "écart", "tradeoff",
    "trade-off", "compromise", "compromis", "robustness", "robustesse",
    "generalization", "generalisation", "généralisation", "representativeness",
    "representativite", "représentativité", "accuracy", "precision", "précision",
}
GENERIC_METHOD_WORDS = {
    "method", "methods", "methode", "méthode", "algorithm", "algorithme",
    "protocol", "protocole", "simulation", "simulations", "experiment",
    "experimental", "essai", "test", "tests", "measurement", "measurements",
    "mesure", "mesures", "benchmark", "validation", "model", "models",
    "modele", "modèle", "modeles", "modèles", "prototype",
}

EXPLICIT_KEY_NAMES = {
    "keywords", "keyword", "key_terms", "key_terms_fr", "key_terms_en",
    "scientific_keywords", "technical_keywords", "terms", "concepts",
    "entities", "methods", "methodes", "méthodes", "technical_terms",
    "domain_terms", "search_terms", "anchors", "strong_anchors",
    "scientific_terms",
}


def _clean_source_text(value: Any, max_chars: int = 5000) -> str:
    text = clean_text(value, max_chars)
    text = re.sub(r"(?i)question\s+de\s+qualification\s*: ?", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _add_text(out: List[str], value: Any, *, min_chars: int = 20, max_chars: int = 5000) -> None:
    if isinstance(value, str):
        text = _clean_source_text(value, max_chars)
        if len(text) >= min_chars:
            out.append(text)
    elif isinstance(value, dict):
        section = _clean_source_text(value.get("section_title"), 500)
        body = ""
        for key in ("text", "source_text", "excerpt", "content", "passage", "description"):
            if isinstance(value.get(key), str) and value.get(key).strip():
                body = _clean_source_text(value.get(key), max_chars)
                break
        if section and body:
            _add_text(out, f"{section}. {body}", min_chars=min_chars, max_chars=max_chars)
        elif section or body:
            _add_text(out, section or body, min_chars=min_chars, max_chars=max_chars)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _add_text(out, item, min_chars=min_chars, max_chars=max_chars)


def _collect_source_passages(verrou: Dict[str, Any]) -> List[str]:
    passages: List[str] = []
    # Evidence-specific collections are read first so that a generic context blob
    # cannot displace the passages actually linked to the lock.
    for key in ("supporting_passages", "source_passages", "evidence", "sources"):
        _add_text(passages, verrou.get(key))
    for key in ("source_text", "scientific_query_text", "text", "verrou_text"):
        _add_text(passages, verrou.get(key))

    for nested_key in ("raw_item", "source_json", "research_context"):
        nested = verrou.get(nested_key)
        if not isinstance(nested, dict):
            continue
        for key in (
            "supporting_passages", "source_passages", "evidence", "sources",
            "source_text", "manual_scholar_text", "scientific_query_text", "text",
        ):
            _add_text(passages, nested.get(key))

    unique: List[str] = []
    seen = set()
    # Preserve insertion order; the backend already orders the strongest linked
    # evidence first.  This is more faithful than sorting by passage length.
    for passage in passages:
        key = norm(passage)
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(passage)
        if len(unique) >= 10:
            break
    return unique


def _collect_explicit_keywords(verrou: Dict[str, Any]) -> List[str]:
    values: List[str] = []

    def walk(obj: Any, depth: int = 0) -> None:
        if depth > 3:
            return
        if isinstance(obj, dict):
            for key, value in obj.items():
                key_norm = str(key or "").strip().lower()
                if key_norm in EXPLICIT_KEY_NAMES:
                    if isinstance(value, str):
                        parts = re.split(r"[,;|\n]+", value)
                        values.extend(clean_text(part, 120) for part in parts if clean_text(part, 120))
                    elif isinstance(value, (list, tuple, set)):
                        for item in value:
                            if isinstance(item, str):
                                text = clean_text(item, 120)
                                if text:
                                    values.append(text)
                            elif isinstance(item, dict):
                                for label_key in ("term", "text", "label", "name", "value"):
                                    text = clean_text(item.get(label_key), 120)
                                    if text:
                                        values.append(text)
                                        break
                if key_norm in {
                    "raw_item", "source_json", "research_context", "scientific_intent",
                    "scholar_enrichment", "enrichment",
                }:
                    walk(value, depth + 1)

    walk(verrou)
    cleaned: List[str] = []
    for value in values:
        toks = [token for token in tokenize(value) if norm(token) not in GENERIC_NOISE]
        if toks:
            cleaned.append(" ".join(toks[:5]))
    return dedupe_keep_order(cleaned, 18)


def _choose_title(verrou: Dict[str, Any], passages: List[str]) -> str:
    for key in ("verrou_title", "title", "research_target_title", "original_title"):
        title = clean_title(verrou.get(key))
        if title and len(title) >= 12:
            return clean_text(title, 220)
    for passage in passages:
        first = re.split(r"(?<=[.!?])\s+", passage)[0]
        if len(first) >= 25:
            return clean_text(first, 220)
    return "Scientific uncertainty to investigate"


def _detect_language(title: str, passages: List[str]) -> str:
    sample = " ".join([title] + passages[:2]).lower()
    words = {norm(x) for x in re.findall(r"[A-Za-zÀ-ÿ]+", sample)}
    fr = len(words & FR_FUNCTION_WORDS)
    en = len(words & EN_FUNCTION_WORDS)
    return "fr" if fr >= en else "en"


def _content_tokens(text: Any) -> List[str]:
    out: List[str] = []
    for token in tokenize(text):
        token = str(token).strip(".,;:()[]{}\"\'")
        nt = norm(token)
        if (
            not nt
            or nt in GENERIC_NOISE
            or nt in FR_FUNCTION_WORDS
            or nt in EN_FUNCTION_WORDS
            or nt.isdigit()
            or len(nt) < 3
        ):
            continue
        out.append(token)
    return out


def _extract_acronyms(text: str, max_items: int = 10) -> List[str]:
    blocked = {"CIR", "RND", "RD", "NLP", "RAG", "LLM", "AI", "IA", "API", "JSON", "PDF"}
    out: List[str] = []
    seen = set()
    for token in re.findall(r"\b[A-Z][A-Z0-9]{1,}(?:[-_/][A-Z0-9]+)?\b", text):
        if token.upper() in blocked:
            continue
        key = norm(token)
        if key and key not in seen:
            seen.add(key)
            out.append(token)
    return out[:max_items]


def _title_key_terms(title: str, explicit_keywords: List[str], max_terms: int = 12) -> List[str]:
    tokens = _content_tokens(title)
    if not tokens:
        return explicit_keywords[:max_terms]

    # Contiguous 2-word expressions from the title are valuable because they
    # preserve the consultant's wording without fabricating an ontology.
    phrases: List[str] = []
    for i in range(len(tokens) - 1):
        a, b = tokens[i], tokens[i + 1]
        if norm(a) != norm(b):
            phrases.append(f"{a} {b}")

    acronyms = _extract_acronyms(title)
    ordered = acronyms + phrases + tokens + explicit_keywords
    return dedupe_keep_order(ordered, max_terms)


def _evidence_key_terms(passages: List[str], title: str, explicit_keywords: List[str], max_terms: int = 16) -> List[str]:
    title_tokens = {norm(token) for token in _content_tokens(title)}
    explicit_tokens = {norm(token) for token in _content_tokens(" ".join(explicit_keywords))}

    counts: Counter[str] = Counter()
    passage_presence: Counter[str] = Counter()
    raw_by_norm: Dict[str, str] = {}
    first_position: Dict[str, int] = {}
    position = 0

    for passage in passages[:8]:
        seen_in_passage = set()
        for token in _content_tokens(passage):
            nt = norm(token)
            if not nt:
                continue
            counts[nt] += 1
            seen_in_passage.add(nt)
            raw_by_norm.setdefault(nt, token)
            first_position.setdefault(nt, position)
            position += 1
        for nt in seen_in_passage:
            passage_presence[nt] += 1

    scored: List[Tuple[float, int, str]] = []
    for nt, count in counts.items():
        score = float(count)
        score += passage_presence[nt] * 1.35
        if nt in title_tokens:
            score += 3.0
        if nt in explicit_tokens:
            score += 3.5
        if len(nt) >= 6:
            score += min(len(nt) / 20.0, 0.8)
        scored.append((score, -first_position.get(nt, 0), raw_by_norm[nt]))

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    terms = [raw for _, _, raw in scored]
    return dedupe_keep_order(explicit_keywords + terms, max_terms)


def _collect_context(verrou: Dict[str, Any], diagnostic_context: Dict[str, Any]) -> str:
    parts: List[str] = []
    for value in (verrou.get("context"), verrou.get("research_context"), diagnostic_context):
        if isinstance(value, dict):
            parts.append(flatten_text(value, 2500))
        elif isinstance(value, str):
            parts.append(value)
    return _clean_source_text(" ".join(parts), 4000)


def _overlap_context(context: str, local_terms: Iterable[str]) -> str:
    anchors = {norm(token) for token in _content_tokens(" ".join(local_terms))}
    if not context or not anchors:
        return ""
    candidates: List[Tuple[int, str]] = []
    for sentence in re.split(r"(?<=[.!?;])\s+|\n+", context):
        sentence = _clean_source_text(sentence, 700)
        if len(sentence) < 30:
            continue
        overlap = anchors & {norm(token) for token in _content_tokens(sentence)}
        if len(overlap) >= 2:
            candidates.append((len(overlap), sentence))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return _clean_source_text(" ".join(sentence for _, sentence in candidates[:2]), 1200)


def _role_terms(terms: List[str]) -> Tuple[List[str], List[str], List[str]]:
    objects: List[str] = []
    phenomena: List[str] = []
    methods: List[str] = []
    for term in terms:
        words = set(norm(term).split())
        if words & GENERIC_PHENOMENON_WORDS:
            phenomena.append(term)
        elif words & GENERIC_METHOD_WORDS:
            methods.append(term)
        else:
            objects.append(term)
    return (
        dedupe_keep_order(objects, 8),
        dedupe_keep_order(phenomena, 6),
        dedupe_keep_order(methods, 6),
    )


def build_scientific_intent(
    verrou: Dict[str, Any],
    domain_detection: Dict[str, Any] | None = None,
    diagnostic_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    del domain_detection  # kept only for API compatibility; no domain routing here.
    verrou = dict(verrou or {})
    diagnostic_context = diagnostic_context or {}

    passages = _collect_source_passages(verrou)
    title = _choose_title(verrou, passages)
    explicit_keywords = _collect_explicit_keywords(verrou)
    title_tokens = dedupe_keep_order(_content_tokens(title), 18)
    title_terms = _title_key_terms(title, explicit_keywords)
    passage_terms = _evidence_key_terms(passages, title, [], max_terms=16)
    evidence_terms = dedupe_keep_order(explicit_keywords + passage_terms, 18)
    language = _detect_language(title, passages)

    local_terms = dedupe_keep_order(title_terms + explicit_keywords + evidence_terms, 28)
    context_text = _collect_context(verrou, diagnostic_context)
    relevant_context = _overlap_context(context_text, local_terms)
    context_terms = _evidence_key_terms([relevant_context] if relevant_context else [], title, [], max_terms=6)
    local_terms = dedupe_keep_order(local_terms + context_terms, 30)

    object_terms, phenomenon_terms, method_terms = _role_terms(local_terms)
    acronyms = _extract_acronyms(" ".join([title] + passages[:5]))

    # Core concepts remain traceable to the input; no alias introduces a word
    # that did not occur in title/evidence/explicit keywords.
    core_concepts = dedupe_keep_order(
        title_terms[:6] + explicit_keywords[:5] + evidence_terms[:8],
        14,
    )
    primary_core = dedupe_keep_order(acronyms + title_terms[:5], 5) or core_concepts[:3]
    strong_anchors = dedupe_keep_order(acronyms + title_terms[:7] + explicit_keywords[:5], 16)

    technical_object = clean_text(" ".join(object_terms[:4]), 260)
    phenomenon = clean_text(" ".join(phenomenon_terms[:3]), 220)
    scientific_problem = clean_text(
        " ".join([title] + explicit_keywords[:3] + evidence_terms[:4]),
        520,
    )

    confidence = 0.35
    confidence += 0.20 if title_terms else 0.0
    confidence += 0.15 if passages else 0.0
    confidence += 0.10 if explicit_keywords else 0.0
    confidence += 0.10 if len(evidence_terms) >= 5 else 0.0
    confidence += 0.05 if relevant_context else 0.0

    research_target_id = str(
        verrou.get("research_target_id")
        or verrou.get("target_id")
        or verrou.get("verrou_id")
        or verrou.get("db_verrou_id")
        or ""
    )

    return {
        "verrou_id": research_target_id,
        "verrou_title": title,
        "original_title": clean_text(verrou.get("original_title") or title, 220),
        "scientific_problem": scientific_problem,
        "technical_object": technical_object,
        "phenomenon": phenomenon,
        "constraints": [],
        "methods": method_terms,
        "title_tokens": title_tokens,
        "title_key_terms": title_terms,
        "explicit_keywords": explicit_keywords,
        "passage_key_terms": passage_terms,
        "evidence_key_terms": evidence_terms,
        "key_terms_fr": local_terms if language == "fr" else [],
        "key_terms_en": local_terms if language == "en" else [],
        "strong_anchors": strong_anchors,
        "core_concepts": core_concepts,
        "primary_core_concepts": primary_core,
        "concept_aliases": {concept: [concept] for concept in core_concepts},
        "method_anchors": method_terms,
        "phenomenon_anchors": phenomenon_terms,
        "local_names": acronyms,
        "literal_source_acronyms": acronyms,
        "literal_source_phrases": dedupe_keep_order(title_terms[:6] + explicit_keywords[:4], 8),
        "literal_source_terms": dedupe_keep_order(title_terms + evidence_terms, 14),
        "source_passages": passages,
        "source_basis": {
            "verrou_title": title,
            "linked_passages_count": len(passages),
            "linked_passages_excerpt": passages[:3],
            "explicit_keywords": explicit_keywords,
            "relevant_diagnostic_context": relevant_context,
        },
        "query_language": language,
        "intent_confidence": round(min(confidence, 0.95), 3),
        "search_queries": [],
        "intent_builder_version": "v161_title_passages_verified_keywords_universal",
        "hardcoded_domain_rules": False,
        "project_specific_rules": False,
    }
