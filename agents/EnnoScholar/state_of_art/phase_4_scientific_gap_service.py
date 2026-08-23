# -*- coding: utf-8 -*-
from __future__ import annotations

"""
phase_4_scientific_gap_service.py

EnnoScholar — Phase 4 : Scientific Gap Builder

Version V4.2 — Consultant Gap Builder + Phase 2 Evidence Bank

Objectif :
- construire le gap scientifique à partir des articles déjà sélectionnés ;
- respecter la sélection humaine du consultant ;
- ne rejeter aucun article choisi ;
- donner plus de poids aux articles taggés Direct et Connexe ;
- éviter le hardcoding métier par domaine ;
- éviter une confiance scientifique trop optimiste ;
- préparer un plan de citations propre pour la Phase 5.

Important :
- Phase 4 ne cherche pas d’articles ;
- Phase 4 ne remplace pas le consultant ;
- Phase 4 ne rejette pas les articles sélectionnés ;
- Article Cards = seules preuves scientifiques ;
- Fewshot / Memory V2 = style uniquement, jamais preuve.
"""

import json
import os
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..contracts import ContractError, build_confirmed_contract

ROOT_DIR = Path(os.getenv("ENNOSMART_ROOT_DIR") or os.getenv("ENNOSMART_ROOT") or Path(__file__).resolve().parents[3])


# ============================================================
# Configuration générique
# ============================================================

GENERIC_STOPWORDS_FR_EN = {
    "avec", "dans", "pour", "plus", "moins", "entre", "comme", "cette",
    "cela", "ainsi", "afin", "etre", "être", "sont", "nous", "notre",
    "leur", "leurs", "des", "les", "une", "aux", "sur", "par", "que",
    "qui", "quoi", "dont", "de", "du", "la", "le", "un", "en", "et",
    "ou", "au", "ce", "ces", "son", "ses", "il", "elle", "ils", "elles",
    "est", "ont", "été", "ete", "avoir", "très", "tres", "aussi",
    "notamment", "projet", "travaux", "année", "annee", "cir", "page",
    "document", "dossier", "client", "consultant", "button", "pipeline",
    "step", "true", "false", "none", "null", "ok", "id", "raw", "loaded",
    "used", "count", "paths", "storage", "organismes", "projects", "years",
    "documents", "the", "and", "for", "with", "from", "that", "this",
    "these", "those", "are", "was", "were", "been", "have", "has", "had",
    "into", "using", "based", "between", "within", "their", "there",
    "where", "which", "when", "then", "than", "such", "also", "can",
    "may", "our", "your",
}

ARTICLE_USAGE_LABELS = {
    "direct_evidence": "Preuve directe",
    "related_evidence": "Preuve connexe",
    "methodological_context": "Contexte méthodologique",
    "background_context": "Contexte général",
    "weak_context": "Contexte faible",
}

# Direct et Connexe ont volontairement plus de poids.
ARTICLE_USAGE_WEIGHTS = {
    "direct_evidence": 1.00,
    "related_evidence": 0.82,
    "methodological_context": 0.58,
    "background_context": 0.42,
    "weak_context": 0.25,
}

SELECTION_RELATION_WEIGHTS = {
    "direct": 1.00,
    "related": 0.82,
    "background": 0.55,
    "unknown": 0.35,
}

RELATION_DIRECT_WORDS = {
    "direct",
    "directe",
    "directs",
    "article_direct",
    "articles_directs",
    "direct_article",
    "direct_articles",
    "direct_evidence",
}

RELATION_RELATED_WORDS = {
    "connexe",
    "connexes",
    "related",
    "related_article",
    "related_articles",
    "article_connexe",
    "articles_connexes",
    "related_evidence",
}

RELATION_BACKGROUND_WORDS = {
    "fondamental",
    "fondamentaux",
    "fundamental",
    "fundamentals",
    "background",
    "background_article",
    "background_articles",
    "article_fondamental",
    "articles_fondamentaux",
}


# ============================================================
# Helpers généraux
# ============================================================

def clean_text(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, (int, float)):
        return str(value)

    if isinstance(value, (list, tuple)):
        return " ".join(clean_text(x) for x in value if clean_text(x))

    if isinstance(value, dict):
        for key in ["text", "value", "label", "title", "name", "summary", "resume"]:
            txt = clean_text(value.get(key))
            if txt:
                return txt
        return ""

    s = str(value)
    s = s.replace("\u00a0", " ")
    s = s.replace("\r", "\n")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def normalize_for_match(value: Any) -> str:
    s = clean_text(value).lower()
    tr = str.maketrans("àâäéèêëîïôöùûüç’", "aaaeeeeiioouuuc'")
    s = s.translate(tr)
    s = re.sub(r"[^a-z0-9_'\- ]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def fs_slug(value: Any) -> str:
    s = str(value or "").strip().lower()
    tr = str.maketrans("àâäéèêëîïôöùûüç’'", "aaaeeeeiioouuuc__")
    s = s.translate(tr)
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "unknown"


def _read_json(path: str | Path, default=None):
    path = Path(path)
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _write_json(path: str | Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def truncate(text: Any, max_chars: int = 600) -> str:
    s = clean_text(text)
    if len(s) <= max_chars:
        return s

    cut = s[:max_chars].rstrip()
    last_dot = max(cut.rfind("."), cut.rfind("!"), cut.rfind("?"))

    if last_dot >= 120:
        return cut[:last_dot + 1].strip()

    last_space = cut.rfind(" ")
    if last_space >= 120:
        return cut[:last_space].strip()

    return cut.strip()


def as_list(value: Any) -> List[Any]:
    if value is None:
        return []

    if isinstance(value, list):
        return value

    if isinstance(value, dict):
        return list(value.values())

    return [value]


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except Exception:
        return default


def normalize_score(value: Any) -> float:
    """
    Normalise les scores possibles :
    - 0 à 1
    - 0 à 10
    - 0 à 100
    """
    score = safe_float(value, 0.0)

    if score <= 0:
        return 0.0

    if score <= 1:
        return round(score, 4)

    if score <= 10:
        return round(score / 10.0, 4)

    return round(min(1.0, score / 100.0), 4)


def _field_text(obj: Dict[str, Any], *keys: str) -> str:
    if not isinstance(obj, dict):
        return ""

    for key in keys:
        value = obj.get(key)
        txt = clean_text(value)
        if txt:
            return txt

    return ""


# ============================================================
# Phase 2 V3.2 — Evidence Bank helpers
# ============================================================

EVIDENCE_BUCKET_LABELS = {
    "problem": "problème scientifique",
    "solution": "solution / contribution",
    "method": "méthode / principe technique",
    "workflow": "enchaînement technique",
    "dataset": "données / datasets",
    "validation": "validation / protocole expérimental",
    "results": "résultats",
    "limitations": "limites",
    "future_work": "travaux futurs",
    "definition": "définitions / concepts",
}

EVIDENCE_BUCKET_PRIORITY = [
    "problem",
    "solution",
    "method",
    "workflow",
    "dataset",
    "validation",
    "results",
    "limitations",
    "definition",
    "future_work",
]


def get_article_evidence_bank(card: Dict[str, Any]) -> Dict[str, Any]:
    bank = card.get("article_evidence_bank")
    if isinstance(bank, dict):
        return bank
    return {}


def get_paragraph_buckets(card: Dict[str, Any]) -> Dict[str, Any]:
    bank = get_article_evidence_bank(card)
    buckets = bank.get("paragraph_buckets")
    if isinstance(buckets, dict):
        return buckets
    return {}


def extract_bucket_items(
    card: Dict[str, Any],
    bucket: str,
    max_items: int = 4,
    max_chars: int = 900,
) -> List[Dict[str, Any]]:
    buckets = get_paragraph_buckets(card)
    raw_items = as_list(buckets.get(bucket))

    out = []
    seen = set()

    for item in raw_items:
        if isinstance(item, dict):
            text = clean_text(
                item.get("text")
                or item.get("paragraph")
                or item.get("value")
            )
            section = clean_text(item.get("section"))
            score = safe_float(item.get("score"), 0.0)
            source = clean_text(item.get("source"))
            paragraph_index = item.get("paragraph_index")
            matched_terms = item.get("matched_terms") or []
        else:
            text = clean_text(item)
            section = ""
            score = 0.0
            source = ""
            paragraph_index = None
            matched_terms = []

        if not text:
            continue

        if len(text) < 45 and len(text.split()) < 8:
            continue

        key = normalize_for_match(text)[:220]
        if key in seen:
            continue
        seen.add(key)

        out.append({
            "bucket": bucket,
            "bucket_label": EVIDENCE_BUCKET_LABELS.get(bucket, bucket),
            "section": section,
            "paragraph_index": paragraph_index,
            "text": truncate(text, max_chars),
            "score": score,
            "source": source,
            "matched_terms": matched_terms,
        })

        if len(out) >= max_items:
            break

    return out


def extract_bucket_texts(
    card: Dict[str, Any],
    buckets: List[str],
    max_items_per_bucket: int = 2,
    max_chars: int = 750,
) -> List[str]:
    texts = []
    seen = set()

    for bucket in buckets:
        for item in extract_bucket_items(
            card,
            bucket,
            max_items=max_items_per_bucket,
            max_chars=max_chars,
        ):
            txt = clean_text(item.get("text"))
            if not txt:
                continue
            key = normalize_for_match(txt)[:180]
            if key in seen:
                continue
            seen.add(key)
            texts.append(txt)

    return texts


def first_bucket_text(
    card: Dict[str, Any],
    buckets: List[str],
    fallback: Any = "",
    max_chars: int = 900,
) -> str:
    texts = extract_bucket_texts(
        card,
        buckets,
        max_items_per_bucket=2,
        max_chars=max_chars,
    )
    if texts:
        return truncate(" ".join(texts), max_chars)

    return truncate(fallback, max_chars)


def evidence_bucket_counts(card: Dict[str, Any]) -> Dict[str, int]:
    buckets = get_paragraph_buckets(card)
    counts = {}
    for bucket in EVIDENCE_BUCKET_PRIORITY:
        counts[bucket] = len(as_list(buckets.get(bucket)))
    return counts


def has_extractable_evidence_bank(card: Dict[str, Any]) -> bool:
    return any(v > 0 for v in evidence_bucket_counts(card).values())


def build_phase2_evidence_for_reasoning(
    card: Dict[str, Any],
    max_total_chars: int = 5200,
) -> str:
    """
    Construit un bloc compact de preuves originales Phase 2.
    Ce texte est destiné au raisonnement Phase 4/4.5/5.
    Il ne doit pas être utilisé tel quel comme rédaction finale.
    """
    lines = []

    for bucket in EVIDENCE_BUCKET_PRIORITY:
        items = extract_bucket_items(card, bucket, max_items=2, max_chars=620)
        if not items:
            continue

        label = EVIDENCE_BUCKET_LABELS.get(bucket, bucket)
        lines.append(f"\n[{bucket.upper()} — {label}]")

        for item in items:
            section = clean_text(item.get("section"))
            section_txt = f" ({section})" if section else ""
            lines.append(f"-{section_txt} {item.get('text')}")

    text = "\n".join(lines).strip()

    if not text:
        text = clean_text(card.get("phase2_evidence_for_reasoning"))

    return truncate(text, max_total_chars)


def build_evidence_extracts_for_next_phases(card: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "evidence_bank_available": has_extractable_evidence_bank(card),
        "bucket_counts": evidence_bucket_counts(card),
        "problem": extract_bucket_items(card, "problem", max_items=3, max_chars=700),
        "solution": extract_bucket_items(card, "solution", max_items=3, max_chars=700),
        "method": extract_bucket_items(card, "method", max_items=3, max_chars=700),
        "workflow": extract_bucket_items(card, "workflow", max_items=3, max_chars=700),
        "dataset": extract_bucket_items(card, "dataset", max_items=3, max_chars=700),
        "validation": extract_bucket_items(card, "validation", max_items=3, max_chars=700),
        "results": extract_bucket_items(card, "results", max_items=3, max_chars=700),
        "limitations": extract_bucket_items(card, "limitations", max_items=3, max_chars=700),
        "future_work": extract_bucket_items(card, "future_work", max_items=2, max_chars=700),
        "definition": extract_bucket_items(card, "definition", max_items=3, max_chars=700),
        "phase2_evidence_for_reasoning": build_phase2_evidence_for_reasoning(card),
        "usage_instruction": (
            "Utiliser ces paragraphes originaux comme matière scientifique. "
            "Ne pas reprendre les anciens champs technical_narrative_capsule s'ils contiennent du texte sale."
        ),
    }


# ============================================================
# Paths
# ============================================================

def state_of_art_payload_dir(organisme: str, project: str, year: str) -> Path:
    return (
        ROOT_DIR
        / "storage"
        / "organismes"
        / fs_slug(organisme)
        / "projects"
        / fs_slug(project)
        / "years"
        / str(year)
        / "ennoscholar"
        / "state_of_art_payload"
    )


def default_selection_payload_path(organisme: str, project: str, year: str) -> Path:
    return state_of_art_payload_dir(organisme, project, year) / "selection_payload.json"


def default_article_cards_payload_path(organisme: str, project: str, year: str) -> Path:
    return state_of_art_payload_dir(organisme, project, year) / "article_cards" / "article_cards_payload.json"


def default_fewshot_payload_path(organisme: str, project: str, year: str) -> Path:
    return (
        state_of_art_payload_dir(organisme, project, year)
        / "phase_3_style_memory"
        / "fewshot_payload.json"
    )


def phase_4_output_dir(organisme: str, project: str, year: str) -> Path:
    return state_of_art_payload_dir(organisme, project, year) / "phase_4_scientific_gap"


def scientific_gap_output_path(organisme: str, project: str, year: str) -> Path:
    return phase_4_output_dir(organisme, project, year) / "gap_scientific_payload.json"


# ============================================================
# Relations consultant
# ============================================================

def relation_from_text(value: Any) -> str:
    s = normalize_for_match(value)

    if any(word in s for word in RELATION_DIRECT_WORDS):
        return "direct"

    if any(word in s for word in RELATION_RELATED_WORDS):
        return "related"

    if any(word in s for word in RELATION_BACKGROUND_WORDS):
        return "background"

    return "unknown"


def first_known_relation(*values: str) -> str:
    for value in values:
        relation = relation_from_text(value)
        if relation != "unknown":
            return relation
    return "unknown"


def usage_from_relation(relation: str, card: Dict[str, Any]) -> str:
    relation = relation or "unknown"

    has_method = bool(clean_text(card.get("method")))
    has_results = bool(clean_text(card.get("results")))
    has_limitations = bool(clean_text(card.get("limitations")))

    if relation == "direct":
        return "direct_evidence"

    if relation == "related":
        return "related_evidence"

    if relation == "background":
        if has_method or has_results or has_limitations:
            return "methodological_context"
        return "background_context"

    if has_method or has_results or has_limitations:
        return "methodological_context"

    return "background_context"


# ============================================================
# Project context
# ============================================================

def extract_project_context(selection_payload: Dict[str, Any]) -> Dict[str, Any]:
    diagnostic_context = selection_payload.get("diagnostic_context") or {}

    return {
        "project_name": _field_text(
            selection_payload,
            "project_name",
            "project",
            "project_label",
            "nom_projet",
        ),
        "organisme": _field_text(
            selection_payload,
            "organisme",
            "organism",
            "client",
        ),
        "domain_label": _field_text(
            selection_payload,
            "domain_label",
            "domain",
            "main_domain",
            "domaine",
        ),
        "objectif_global": _field_text(
            diagnostic_context,
            "objectif_global",
            "objectif",
            "objectif_rd",
            "objectif_r&d",
        ),
        "resume_strategique": _field_text(
            diagnostic_context,
            "resume_strategique",
            "summary",
            "resume",
            "contexte_projet",
        ),
    }


# ============================================================
# Verrous depuis selection_payload
# ============================================================

def extract_articles_from_verrou(verrou: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Récupère les articles associés au verrou dans selection_payload.
    Le tag consultant est conservé via _selection_relation.
    """
    candidates = []

    article_keys = [
        "articles_directs",
        "articles_connexes",
        "articles_fondamentaux",
        "direct_articles",
        "related_articles",
        "fundamental_articles",
        "background_articles",
        "selected_articles",
        "articles",
    ]

    for key in article_keys:
        relation_from_key = relation_from_text(key)

        for item in as_list(verrou.get(key)):
            if isinstance(item, dict):
                normalized = dict(item)

                relation = first_known_relation(
                    item.get("tag"),
                    item.get("semantic_tag"),
                    item.get("article_tag"),
                    item.get("relation_type"),
                    item.get("category"),
                    key,
                )

                if relation == "unknown":
                    relation = relation_from_key

                normalized["_selection_key"] = key
                normalized["_selection_relation"] = relation
                normalized["consultant_selected"] = True
                candidates.append(normalized)

    return candidates


def normalize_verrou(verrou: Dict[str, Any], index: int) -> Dict[str, Any]:
    title = _field_text(
        verrou,
        "verrou_title",
        "title",
        "name",
        "label",
        "verrou",
    )

    objectif = _field_text(
        verrou,
        "objectif_rd",
        "objectif_r&d",
        "objectif_r_d",
        "objectif",
    )

    contexte = _field_text(
        verrou,
        "contexte_projet",
        "contexte",
        "context",
        "description",
        "summary",
    )

    verrou_id = _field_text(verrou, "verrou_id", "id", "lock_id")
    if not verrou_id:
        raise ContractError(
            "missing_verrou_id",
            f"Le verrou confirmé à l'index {index} ne possède pas d'identifiant.",
            {"index": index},
        )
    if not title:
        raise ContractError(
            "missing_verrou_title",
            f"Le verrou confirmé {verrou_id!r} ne possède pas de titre.",
            {"index": index, "verrou_id": verrou_id},
        )

    selected_articles = extract_articles_from_verrou(verrou)

    return {
        "verrou_id": verrou_id,
        "verrou_index": index,
        "verrou_title": title,
        "objectif_rd": objectif,
        "contexte_projet": contexte,
        "raw": verrou,
        "selection_articles": selected_articles,
    }


def extract_verrous(selection_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    verrous = (
        selection_payload.get("verrous")
        or selection_payload.get("locks")
        or selection_payload.get("scientific_locks")
        or []
    )

    out = []

    for i, verrou in enumerate(as_list(verrous), 1):
        if isinstance(verrou, dict):
            out.append(normalize_verrou(verrou, i))

    return out


# ============================================================
# Article Cards
# ============================================================

def find_article_cards_container(payload: Dict[str, Any]) -> List[Any]:
    if not isinstance(payload, dict):
        return []

    for key in [
        "article_cards",
        "cards",
        "articles",
        "selected_article_cards",
        "article_cards_payload",
    ]:
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            return list(value.values())

    for key in [
        "verrou_cards",
        "cards_by_verrou",
        "article_cards_by_verrou",
        "verrous",
    ]:
        value = payload.get(key)
        if isinstance(value, dict):
            merged = []
            for verrou_id, cards in value.items():
                for card in as_list(cards):
                    if isinstance(card, dict):
                        card = dict(card)
                        card.setdefault("verrou_id", verrou_id)
                        merged.append(card)
            if merged:
                return merged

    return []


def normalize_article_card(card: Dict[str, Any], index: int) -> Dict[str, Any]:
    """
    V4.2 :
    Normalise une Article Card en donnant la priorité au nouveau bloc Phase 2 V3.2 :
        article_evidence_bank.paragraph_buckets

    Les anciens champs restent conservés en fallback et pour compatibilité UI,
    mais les phases suivantes doivent consommer evidence_extracts_for_phase_4_5.
    """
    article_id = _field_text(
        card,
        "article_id",
        "paper_id",
        "semantic_scholar_id",
        "openalex_id",
        "arxiv_id",
        "id",
        "doi",
    )

    citation_label = _field_text(
        card,
        "citation_label",
        "citation_id",
        "reference_id",
        "article_ref",
        "ref",
    ) or f"A{index}"

    title = _field_text(card, "title", "article_title", "paper_title", "name")

    abstract_raw = _field_text(
        card,
        "abstract_for_writer",
        "abstract_fr",
        "abstract_original",
        "abstract",
        "summary",
        "resume",
    )

    method_raw = _field_text(
        card,
        "method",
        "methodology",
        "methods",
        "approach",
        "methode",
        "méthode",
    )

    results_raw = _field_text(
        card,
        "results",
        "main_results",
        "resultats",
        "résultats",
        "findings",
    )

    limitations_raw = _field_text(
        card,
        "limitations",
        "limites",
        "limits",
        "article_limitations",
        "weaknesses",
    )

    relevance_raw = _field_text(
        card,
        "relevance",
        "pertinence",
        "why_relevant",
        "justification",
        "relevance_reason",
        "limite_pour_notre_projet",
    )

    evidence_extracts = build_evidence_extracts_for_next_phases(card)
    evidence_context = evidence_extracts.get("phase2_evidence_for_reasoning") or ""

    abstract = first_bucket_text(
        card,
        ["problem", "solution"],
        fallback=abstract_raw,
        max_chars=950,
    )

    method = first_bucket_text(
        card,
        ["method", "workflow"],
        fallback=method_raw,
        max_chars=1200,
    )

    results = first_bucket_text(
        card,
        ["results", "validation"],
        fallback=results_raw,
        max_chars=1000,
    )

    limitations = first_bucket_text(
        card,
        ["limitations", "future_work"],
        fallback=limitations_raw,
        max_chars=1000,
    )

    relevance = first_bucket_text(
        card,
        ["problem", "limitations", "validation"],
        fallback=relevance_raw,
        max_chars=1000,
    )

    authors = card.get("authors") or card.get("author_names") or []
    if isinstance(authors, list):
        authors_text = ", ".join(clean_text(x) for x in authors[:5] if clean_text(x))
    else:
        authors_text = clean_text(authors)

    year = _field_text(card, "year", "publication_year", "published_year", "date")

    tag = _field_text(
        card,
        "tag",
        "semantic_tag",
        "article_tag",
        "relation_type",
        "category",
        "role",
    )

    raw_score = (
        card.get("score")
        or card.get("relevance_score")
        or card.get("final_score")
        or card.get("rerank_score")
        or 0.0
    )

    verrou_id = _field_text(card, "verrou_id", "lock_id", "related_verrou_id")

    full_text = " ".join([
        title,
        abstract,
        method,
        results,
        limitations,
        relevance,
        tag,
        evidence_context,
    ])

    consultant_selected = card.get("consultant_selected")
    if consultant_selected is None:
        consultant_selected = True

    evidence = card.get("evidence") or {}
    if not isinstance(evidence, dict):
        evidence = {}

    return {
        "card_index": index,
        "article_id": article_id or f"article_{index:03d}",
        "citation_label": citation_label,
        "title": title,
        "authors": authors_text,
        "year": year,
        "abstract": abstract,
        "method": method,
        "results": results,
        "limitations": limitations,
        "relevance": relevance,
        "tag": tag,
        "tag_relation": relation_from_text(tag),
        "score": safe_float(raw_score),
        "score_normalized": normalize_score(raw_score),
        "verrou_id": verrou_id,
        "consultant_selected": bool(consultant_selected),
        "full_text": clean_text(full_text),
        "article_evidence_bank_available": bool(evidence_extracts.get("evidence_bank_available")),
        "article_evidence_bucket_counts": evidence_extracts.get("bucket_counts") or {},
        "evidence_extracts_for_phase_4_5": evidence_extracts,
        "phase2_evidence_for_reasoning": evidence_context,
        "full_text_available": bool(evidence.get("full_text_available")),
        "generation_mode": clean_text(evidence.get("generation_mode")),
        "text_chars": safe_int(evidence.get("text_chars"), 0),
        "raw": card,
    }


def extract_article_cards(article_cards_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    cards_raw = find_article_cards_container(article_cards_payload)

    cards = []
    for i, card in enumerate(cards_raw, 1):
        if not isinstance(card, dict):
            continue

        normalized = normalize_article_card(card, i)

        if not normalized["title"] and not normalized["full_text"]:
            continue

        cards.append(normalized)

    return cards


# ============================================================
# Matching sélection consultant ↔ Article Cards
# ============================================================

def article_identity_values(article: Dict[str, Any]) -> List[str]:
    values = []

    for key in [
        "article_id",
        "paper_id",
        "semantic_scholar_id",
        "openalex_id",
        "arxiv_id",
        "id",
        "doi",
        "citation_label",
        "ref",
    ]:
        txt = clean_text(article.get(key))
        if txt:
            values.append(normalize_for_match(txt))

    title = clean_text(article.get("title") or article.get("article_title"))
    if title:
        values.append(normalize_for_match(title)[:180])

    return values


def selected_article_identity_map(verrou: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    mapping = {}

    for article in verrou.get("selection_articles") or []:
        if not isinstance(article, dict):
            continue

        relation = article.get("_selection_relation") or relation_from_text(article.get("_selection_key"))

        for identity in article_identity_values(article):
            if identity:
                mapping[identity] = {
                    "selected_in_phase_1": True,
                    "selection_relation": relation or "unknown",
                    "selection_key": article.get("_selection_key"),
                    "consultant_selected": True,
                }

    return mapping


def card_identity_keys(card: Dict[str, Any]) -> List[str]:
    return article_identity_values(card)


def selection_link_info(verrou: Dict[str, Any], card: Dict[str, Any]) -> Dict[str, Any]:
    mapping = selected_article_identity_map(verrou)

    for identity in card_identity_keys(card):
        if identity in mapping:
            return mapping[identity]

    return {
        "selected_in_phase_1": False,
        "selection_relation": "unknown",
        "selection_key": "",
        "consultant_selected": True,
    }


def associate_cards_to_verrou(
    verrou: Dict[str, Any],
    article_cards: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Si selection_payload contient des articles associés au verrou,
    on garde ceux-là. Sinon fallback : on garde toutes les Article Cards.
    Aucun article sélectionné n’est rejeté globalement.
    """
    selected_map = selected_article_identity_map(verrou)

    if not selected_map:
        return article_cards

    associated = []

    for card in article_cards:
        info = selection_link_info(verrou, card)

        linked_by_verrou_id = (
            bool(card.get("verrou_id"))
            and clean_text(card.get("verrou_id")) == clean_text(verrou.get("verrou_id"))
        )

        if info.get("selected_in_phase_1") or linked_by_verrou_id:
            associated.append(card)

    if associated:
        return associated

    return article_cards


# ============================================================
# Keywords génériques
# ============================================================

def tokens(text: Any, extra_stopwords: Optional[set] = None) -> List[str]:
    s = normalize_for_match(text)
    raw = re.findall(r"\b[a-zA-Z0-9_'-]{3,}\b", s)

    stopwords = set(GENERIC_STOPWORDS_FR_EN)
    if extra_stopwords:
        stopwords.update(extra_stopwords)

    out = []

    for tok in raw:
        tok = tok.strip("_-'")
        if len(tok) < 3:
            continue
        if tok in stopwords:
            continue
        if tok.isdigit():
            continue
        out.append(tok)

    return out


def dynamic_context_stopwords(
    organisme: str,
    project: str,
    year: str,
    project_context: Optional[Dict[str, Any]] = None,
) -> set:
    values = [organisme, project, year]

    if project_context:
        values.extend([
            project_context.get("project_name", ""),
            project_context.get("organisme", ""),
            project_context.get("domain_label", ""),
        ])

    out = set()
    for value in values:
        for tok in tokens(value):
            out.add(tok)

    return out


def extract_top_keywords_for_verrou(
    verrou: Dict[str, Any],
    ranked_articles: List[Dict[str, Any]],
    extra_stopwords: Optional[set] = None,
    max_items: int = 12,
) -> List[str]:
    """
    V4.1 :
    - priorité au verrou et aux articles Direct ;
    - les articles Connexes contribuent peu pour éviter de polluer le gap ;
    - évite que des sujets secondaires deviennent des dimensions principales.
    """
    direct_articles = [
        item for item in ranked_articles
        if item.get("article_usage", {}).get("article_usage_type") == "direct_evidence"
    ]

    related_articles = [
        item for item in ranked_articles
        if item.get("article_usage", {}).get("article_usage_type") == "related_evidence"
    ]

    text_parts = [
        verrou.get("verrou_title") or "",
        verrou.get("objectif_rd") or "",
    ]

    # Poids fort aux articles Direct.
    for item in direct_articles:
        card = item.get("card") or {}
        text_parts.extend([
            card.get("title") or "",
            card.get("relevance") or "",
            card.get("method") or "",
        ])

    # Poids faible aux Connexes : titre seulement, et seulement les 2 premiers.
    for item in related_articles[:2]:
        card = item.get("card") or {}
        text_parts.append(card.get("title") or "")

    text = " ".join(text_parts)
    counter = Counter(tokens(text, extra_stopwords=extra_stopwords))

    cleaned = Counter()
    for term, count in counter.items():
        if len(term) < 4:
            continue
        if term in GENERIC_STOPWORDS_FR_EN:
            continue
        cleaned[term] += count

    return [term for term, _ in cleaned.most_common(max_items)]


# ============================================================
# Qualification des articles
# ============================================================

def build_article_usage_info(
    card: Dict[str, Any],
    selection_info: Dict[str, Any],
    linked_by_verrou_id: bool,
) -> Dict[str, Any]:
    selection_relation = selection_info.get("selection_relation") or "unknown"
    tag_relation = card.get("tag_relation") or "unknown"

    relation = "unknown"

    if linked_by_verrou_id:
        relation = "direct"
    elif selection_relation != "unknown":
        relation = selection_relation
    elif tag_relation != "unknown":
        relation = tag_relation

    usage_type = usage_from_relation(relation, card)
    usage_label = ARTICLE_USAGE_LABELS.get(usage_type, usage_type)

    base_weight = ARTICLE_USAGE_WEIGHTS.get(usage_type, 0.25)
    relation_weight = SELECTION_RELATION_WEIGHTS.get(relation, 0.35)
    score_normalized = safe_float(card.get("score_normalized"))

    # Direct/Connexe pèse plus que le score automatique.
    argument_strength_score = round(
        min(
            1.0,
            (base_weight * 0.60)
            + (relation_weight * 0.30)
            + (score_normalized * 0.10),
        ),
        3,
    )

    if relation == "direct":
        writer_instruction = (
            "À utiliser comme appui principal dans la rédaction de l’état de l’art et du gap scientifique."
        )
        usage_explanation = (
            "Article prioritaire car il est relié au verrou ou taggé comme Direct dans la sélection consultant."
        )

    elif relation == "related":
        writer_instruction = (
            "À utiliser comme appui connexe important, en expliquant les limites de transposition au projet courant."
        )
        usage_explanation = (
            "Article important car il est taggé comme Connexe ou identifié comme source proche du verrou."
        )

    elif relation == "background":
        writer_instruction = (
            "À utiliser pour le cadrage scientifique, méthodologique ou contextuel, sans le présenter comme preuve centrale."
        )
        usage_explanation = (
            "Article utile pour situer le contexte ou la méthode, mais moins central qu’un article Direct ou Connexe."
        )

    else:
        writer_instruction = (
            "À utiliser avec prudence comme élément de contexte, car aucun tag Direct/Connexe/Fondamental clair n’a été trouvé."
        )
        usage_explanation = (
            "Article conservé car présent dans la sélection validée, mais son rôle argumentatif est moins explicite."
        )

    return {
        "article_usage_type": usage_type,
        "article_usage_label": usage_label,
        "argument_strength_score": argument_strength_score,
        "consultant_selected": True,
        "selected_in_phase_1": bool(selection_info.get("selected_in_phase_1")),
        "selection_relation": selection_relation,
        "tag_relation": tag_relation,
        "final_relation_used": relation,
        "usage_explanation": usage_explanation,
        "writer_instruction": writer_instruction,
        "signals": {
            "linked_by_verrou_id": linked_by_verrou_id,
            "score_normalized": score_normalized,
            "has_relevance": bool(clean_text(card.get("relevance"))),
            "has_method": bool(clean_text(card.get("method"))),
            "has_results": bool(clean_text(card.get("results"))),
            "has_limitations": bool(clean_text(card.get("limitations"))),
        },
    }


def rank_articles_for_verrou(
    verrou: Dict[str, Any],
    article_cards: List[Dict[str, Any]],
    max_articles: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Tri argumentatif.
    Aucun article consultant n’est rejeté.
    Direct > Connexe > Méthodologique > Contexte.
    """
    cards = associate_cards_to_verrou(verrou, article_cards)
    ranked = []

    for card in cards:
        linked_by_verrou_id = (
            bool(card.get("verrou_id"))
            and clean_text(card.get("verrou_id")) == clean_text(verrou.get("verrou_id"))
        )

        selection_info = selection_link_info(verrou, card)

        usage_info = build_article_usage_info(
            card=card,
            selection_info=selection_info,
            linked_by_verrou_id=linked_by_verrou_id,
        )

        usage_type = usage_info.get("article_usage_type")
        usage_weight = ARTICLE_USAGE_WEIGHTS.get(usage_type, 0.25)

        relation = usage_info.get("final_relation_used") or "unknown"
        relation_weight = SELECTION_RELATION_WEIGHTS.get(relation, 0.35)

        match_score = round(
            min(
                1.0,
                (usage_weight * 0.55)
                + (relation_weight * 0.35)
                + (safe_float(card.get("score_normalized")) * 0.10),
            ),
            4,
        )

        match_reasons = ["consultant_selected"]

        if selection_info.get("selected_in_phase_1"):
            match_reasons.append("selected_in_phase_1")

        if linked_by_verrou_id:
            match_reasons.append("linked_by_verrou_id")

        if relation == "direct":
            match_reasons.append("direct_tag_high_weight")

        if relation == "related":
            match_reasons.append("related_tag_high_weight")

        ranked.append({
            "card": card,
            "match_score": match_score,
            "match_reasons": match_reasons,
            "article_usage": usage_info,
            "consultant_selected": True,
        })

    ranked.sort(
        key=lambda x: (
            ARTICLE_USAGE_WEIGHTS.get(x["article_usage"]["article_usage_type"], 0.0),
            x.get("match_score", 0.0),
            safe_float(x["card"].get("score_normalized")),
        ),
        reverse=True,
    )

    if max_articles is not None and max_articles > 0:
        return ranked[:max_articles]

    return ranked


# ============================================================
# Construction argumentaire
# ============================================================

def summarize_article_contribution(card: Dict[str, Any], usage: Dict[str, Any]) -> str:
    label = clean_text(card.get("citation_label"))
    usage_label = clean_text(usage.get("article_usage_label")).lower()
    usage_type = usage.get("article_usage_type")

    problem = extract_bucket_texts(card.get("raw") or {}, ["problem"], max_items_per_bucket=1, max_chars=260)
    method_ev = extract_bucket_texts(card.get("raw") or {}, ["method", "workflow"], max_items_per_bucket=1, max_chars=300)
    validation_ev = extract_bucket_texts(card.get("raw") or {}, ["validation", "dataset"], max_items_per_bucket=1, max_chars=260)
    results_ev = extract_bucket_texts(card.get("raw") or {}, ["results"], max_items_per_bucket=1, max_chars=260)

    relevance = clean_text(card.get("relevance"))
    method = clean_text(card.get("method"))
    results = clean_text(card.get("results"))
    abstract = clean_text(card.get("abstract"))

    prefix = f"{label} est mobilisé comme {usage_label}."

    if usage_type == "direct_evidence":
        if method_ev:
            return f"{prefix} Il apporte une preuve technique principale sur le mécanisme étudié : {truncate(method_ev[0], 320)}"
        if relevance:
            return f"{prefix} Il constitue un appui principal pour le verrou : {truncate(relevance, 320)}"
        if method:
            return f"{prefix} Il apporte une approche centrale pour positionner le verrou : {truncate(method, 300)}"
        return f"{prefix} Il constitue un appui central de la sélection bibliographique."

    if usage_type == "related_evidence":
        if validation_ev:
            return f"{prefix} Il apporte un éclairage connexe sur les données, le protocole ou la validation : {truncate(validation_ev[0], 320)}"
        if relevance:
            return f"{prefix} Il apporte un éclairage connexe important : {truncate(relevance, 320)}"
        if method:
            return f"{prefix} Il décrit une approche proche ou complémentaire : {truncate(method, 300)}"
        return f"{prefix} Il contribue à situer des travaux proches, sans suffire seul à lever le verrou."

    if usage_type == "methodological_context":
        if method_ev:
            return f"{prefix} Il sert principalement à structurer la comparaison méthodologique : {truncate(method_ev[0], 320)}"
        if results_ev:
            return f"{prefix} Il apporte des résultats utiles pour l’analyse comparative : {truncate(results_ev[0], 320)}"
        if method:
            return f"{prefix} Il sert principalement à structurer la comparaison méthodologique : {truncate(method, 300)}"
        if results:
            return f"{prefix} Il apporte des résultats utiles pour l’analyse comparative : {truncate(results, 300)}"
        return f"{prefix} Il fournit un contexte méthodologique utile pour la rédaction."

    if problem:
        return f"{prefix} Il sert au cadrage scientifique général : {truncate(problem[0], 300)}"

    if abstract:
        return f"{prefix} Il sert au cadrage scientifique général : {truncate(abstract, 300)}"

    return f"{prefix} Il est conservé dans la sélection consultant comme élément de contexte."


def summarize_article_limitation(card: Dict[str, Any], usage: Dict[str, Any]) -> str:
    label = clean_text(card.get("citation_label"))
    limitations_ev = extract_bucket_texts(
        card.get("raw") or {},
        ["limitations", "future_work"],
        max_items_per_bucket=1,
        max_chars=350,
    )
    validation_ev = extract_bucket_texts(
        card.get("raw") or {},
        ["validation", "dataset"],
        max_items_per_bucket=1,
        max_chars=300,
    )

    limitations = clean_text(card.get("limitations"))
    usage_type = usage.get("article_usage_type")

    if limitations_ev:
        return f"{label} présente une limite ou une réserve à prendre en compte : {truncate(limitations_ev[0], 350)}"

    if limitations and "les auteurs ne formulent pas de limitation explicite" not in normalize_for_match(limitations):
        return f"{label} présente une limite à prendre en compte : {truncate(limitations, 350)}"

    if validation_ev:
        return (
            f"{label} doit être transposé avec prudence, car la preuve disponible dépend d'un protocole, "
            f"de données ou de conditions expérimentales spécifiques : {truncate(validation_ev[0], 300)}"
        )

    if usage_type == "direct_evidence":
        return (
            f"{label} est central pour le verrou, mais ses résultats doivent être validés "
            "dans les conditions propres du projet courant."
        )

    if usage_type == "related_evidence":
        return (
            f"{label} est connexe au verrou, mais sa transposition au contexte projet "
            "doit être discutée explicitement."
        )

    if usage_type == "methodological_context":
        return (
            f"{label} apporte une base méthodologique, mais ne démontre pas directement "
            "la levée du verrou scientifique."
        )

    return (
        f"{label} contribue au cadrage général, mais ne doit pas être utilisé comme preuve centrale."
    )


def group_citations_by_usage(ranked_articles: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    grouped = {
        "direct_evidence": [],
        "related_evidence": [],
        "methodological_context": [],
        "background_context": [],
        "weak_context": [],
    }

    for item in ranked_articles:
        card = item["card"]
        usage_type = item["article_usage"]["article_usage_type"]
        citation = clean_text(card.get("citation_label"))

        if citation:
            grouped.setdefault(usage_type, []).append(citation)

    return grouped


def build_citation_plan(ranked_articles: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    grouped = group_citations_by_usage(ranked_articles)

    main = grouped.get("direct_evidence") or []
    supporting = grouped.get("related_evidence") or []
    methodology = grouped.get("methodological_context") or []
    background = (grouped.get("background_context") or []) + (grouped.get("weak_context") or [])

    return {
        "main_citations": main,
        "supporting_citations": supporting,
        "methodological_citations": methodology,
        "background_citations": background,
        "all_allowed_citations": main + supporting + methodology + background,
    }


def build_literature_coverage(ranked_articles: List[Dict[str, Any]]) -> List[str]:
    return [
        summarize_article_contribution(item["card"], item["article_usage"])
        for item in ranked_articles
    ]


def build_article_limitations(ranked_articles: List[Dict[str, Any]]) -> List[str]:
    limitations = [
        summarize_article_limitation(item["card"], item["article_usage"])
        for item in ranked_articles
    ]

    if len(ranked_articles) >= 2:
        limitations.append(
            "Pris ensemble, ces articles forment un socle bibliographique validé par le consultant. "
            "Cependant, les articles Direct doivent porter l’argumentation principale, les articles Connexes doivent appuyer la discussion, "
            "et les articles de contexte doivent rester secondaires."
        )

    return limitations


def build_non_transposability(
    verrou: Dict[str, Any],
    ranked_articles: List[Dict[str, Any]],
    keywords: List[str],
) -> List[str]:
    title = clean_text(verrou.get("verrou_title"))
    objectif = clean_text(verrou.get("objectif_rd"))

    grouped = group_citations_by_usage(ranked_articles)

    direct_count = len(grouped.get("direct_evidence") or [])
    related_count = len(grouped.get("related_evidence") or [])
    methodological_count = len(grouped.get("methodological_context") or [])
    background_count = len(grouped.get("background_context") or []) + len(grouped.get("weak_context") or [])

    items = []

    if title:
        items.append(
            f"La littérature sélectionnée permet de situer le verrou « {title} », "
            "mais elle ne démontre pas automatiquement que les approches étudiées sont directement applicables au cas du projet courant."
        )

    if objectif:
        items.append(
            f"L’objectif R&D du dossier impose une validation propre au projet : {truncate(objectif, 280)}"
        )

    items.append(
        f"La sélection contient {direct_count} article(s) Direct, {related_count} article(s) Connexe(s), "
        f"{methodological_count} article(s) méthodologique(s) et {background_count} article(s) de contexte. "
        "Cette répartition impose de distinguer les preuves centrales des sources de cadrage."
    )

    if keywords:
        items.append(
            "Les dimensions de non-transposabilité à discuter sont notamment : "
            + ", ".join(keywords[:8])
            + "."
        )

    return items


def build_scientific_gap_text(
    verrou: Dict[str, Any],
    ranked_articles: List[Dict[str, Any]],
    keywords: List[str],
) -> str:
    title = clean_text(verrou.get("verrou_title"))
    plan = build_citation_plan(ranked_articles)

    main = plan["main_citations"]
    supporting = plan["supporting_citations"]
    methodology = plan["methodological_citations"]
    background = plan["background_citations"]

    if title:
        text = (
            f"Le gap scientifique associé au verrou « {title} » réside dans l’écart entre les résultats disponibles "
            "dans la littérature sélectionnée et la validation nécessaire dans le contexte spécifique du projet."
        )
    else:
        text = (
            "Le gap scientifique réside dans l’écart entre les résultats disponibles dans la littérature sélectionnée "
            "et la validation nécessaire dans le contexte spécifique du projet."
        )

    if main:
        text += (
            " Les articles "
            + ", ".join(main)
            + " constituent les appuis principaux de l’état de l’art."
        )

    if supporting:
        text += (
            " Les articles "
            + ", ".join(supporting)
            + " apportent des éléments connexes importants, mais nécessitent une discussion de transposabilité."
        )

    context = methodology + background
    if context:
        text += (
            " Les articles "
            + ", ".join(context)
            + " doivent être utilisés principalement pour le cadrage ou la méthodologie."
        )

    if keywords:
        text += (
            " Les dimensions à discuter sont : "
            + ", ".join(keywords[:6])
            + "."
        )

    text += (
        " Ainsi, même si la sélection bibliographique fournit un socle utile, elle ne suffit pas à lever seule "
        "les incertitudes liées à la faisabilité, à la robustesse, à la généralisation ou à la transposition au contexte propre du projet."
    )

    return text


def build_rd_justification_text(verrou: Dict[str, Any], ranked_articles: List[Dict[str, Any]]) -> str:
    title = clean_text(verrou.get("verrou_title"))
    plan = build_citation_plan(ranked_articles)

    direct_count = len(plan["main_citations"])
    related_count = len(plan["supporting_citations"])

    if title:
        return (
            f"Des travaux R&D restent nécessaires pour traiter le verrou « {title} ». "
            f"La sélection bibliographique contient {direct_count} appui(s) Direct et {related_count} appui(s) Connexe(s), "
            "ce qui permet de cadrer l’état de l’art, mais ne démontre pas automatiquement la faisabilité ou la performance "
            "dans les conditions propres du projet. La démarche R&D doit donc permettre de tester, adapter et valider les approches identifiées "
            "à partir de protocoles spécifiques au dossier."
        )

    return (
        "Des travaux R&D restent nécessaires, car l’état de l’art permet de cadrer le problème, "
        "mais ne permet pas de conclure directement sur la faisabilité ou la performance dans les conditions propres du projet."
    )


def build_supporting_articles(ranked_articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []

    for item in ranked_articles:
        card = item["card"]
        usage = item["article_usage"]

        out.append({
            "citation_label": card.get("citation_label"),
            "article_id": card.get("article_id"),
            "title": card.get("title"),
            "authors": card.get("authors"),
            "year": card.get("year"),
            "tag": card.get("tag"),
            "score": card.get("score"),
            "score_normalized": card.get("score_normalized"),
            "consultant_selected": True,
            "selected_in_phase_1": usage.get("selected_in_phase_1"),
            "selection_relation": usage.get("selection_relation"),
            "tag_relation": usage.get("tag_relation"),
            "final_relation_used": usage.get("final_relation_used"),
            "article_usage_type": usage.get("article_usage_type"),
            "article_usage_label": usage.get("article_usage_label"),
            "argument_strength_score": usage.get("argument_strength_score"),
            "usage_explanation": usage.get("usage_explanation"),
            "writer_instruction": usage.get("writer_instruction"),
            "signals": usage.get("signals"),
            "match_score": item.get("match_score"),
            "match_reasons": item.get("match_reasons"),
            "usable_as_scientific_source": True,
            "can_be_cited": True,
            "do_not_overuse_as_direct_proof": usage.get("article_usage_type") in {
                "related_evidence",
                "methodological_evidence",
                "background_evidence",
                "background_context",
                "methodological_context",
                "weak_context",
            },
            "article_evidence_bank_available": card.get("article_evidence_bank_available"),
            "article_evidence_bucket_counts": card.get("article_evidence_bucket_counts") or {},
            "phase2_evidence_for_reasoning": card.get("phase2_evidence_for_reasoning"),
            "evidence_extracts_for_phase_4_5": card.get("evidence_extracts_for_phase_4_5") or {},
            "generation_mode": card.get("generation_mode"),
            "full_text_available": card.get("full_text_available"),
            "text_chars": card.get("text_chars"),
        })

    return out


def build_article_argumentation_map(ranked_articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []

    for item in ranked_articles:
        card = item["card"]
        usage = item["article_usage"]

        out.append({
            "citation_label": card.get("citation_label"),
            "title": card.get("title"),
            "consultant_selected": True,
            "selection_relation": usage.get("selection_relation"),
            "final_relation_used": usage.get("final_relation_used"),
            "article_usage_type": usage.get("article_usage_type"),
            "article_usage_label": usage.get("article_usage_label"),
            "argument_strength_score": usage.get("argument_strength_score"),
            "recommended_use_in_phase_5": usage.get("writer_instruction"),
        })

    return out


def infer_risk_level(ranked_articles: List[Dict[str, Any]]) -> str:
    plan = build_citation_plan(ranked_articles)

    direct_count = len(plan["main_citations"])
    related_count = len(plan["supporting_citations"])
    background_count = len(plan["background_citations"])

    if direct_count >= 2:
        return "low"

    if direct_count >= 1 and related_count >= 1:
        return "medium"

    if related_count >= 2:
        return "medium"

    if background_count >= len(ranked_articles):
        return "high"

    return "medium"


def confidence_scores(ranked_articles: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    V4.1 :
    - la sélection consultant reste à 1.0 ;
    - la confiance scientifique reste prudente, plafonnée à 0.90 ;
    - évite un 1.0 qui donne une impression de certitude excessive.
    """
    if not ranked_articles:
        return {
            "consultant_selection_confidence": 1.0,
            "scientific_gap_confidence": 0.25,
            "argumentation_balance_score": 0.0,
        }

    usage_scores = [
        ARTICLE_USAGE_WEIGHTS.get(item["article_usage"]["article_usage_type"], 0.25)
        for item in ranked_articles
    ]

    avg_usage = sum(usage_scores) / max(1, len(usage_scores))
    plan = build_citation_plan(ranked_articles)

    direct_count = len(plan["main_citations"])
    related_count = len(plan["supporting_citations"])
    methodological_count = len(plan["methodological_citations"])
    background_count = len(plan["background_citations"])

    balance = min(
        1.0,
        (direct_count * 0.28)
        + (related_count * 0.16)
        + (methodological_count * 0.08)
        + (avg_usage * 0.28),
    )

    scientific_confidence = min(0.90, 0.30 + (0.60 * balance))

    if background_count > direct_count + related_count:
        scientific_confidence = min(scientific_confidence, 0.72)

    return {
        "consultant_selection_confidence": 1.0,
        "scientific_gap_confidence": round(scientific_confidence, 3),
        "argumentation_balance_score": round(balance, 3),
    }


def analyze_gap_for_verrou(
    verrou: Dict[str, Any],
    article_cards: List[Dict[str, Any]],
    extra_stopwords: Optional[set] = None,
    max_articles_per_verrou: Optional[int] = None,
) -> Dict[str, Any]:
    ranked_articles = rank_articles_for_verrou(
        verrou=verrou,
        article_cards=article_cards,
        max_articles=max_articles_per_verrou,
    )

    keywords = extract_top_keywords_for_verrou(
        verrou=verrou,
        ranked_articles=ranked_articles,
        extra_stopwords=extra_stopwords,
    )

    citation_plan = build_citation_plan(ranked_articles)

    literature_coverage = build_literature_coverage(ranked_articles)
    article_limitations = build_article_limitations(ranked_articles)

    non_transposability = build_non_transposability(
        verrou=verrou,
        ranked_articles=ranked_articles,
        keywords=keywords,
    )

    scientific_gap = build_scientific_gap_text(
        verrou=verrou,
        ranked_articles=ranked_articles,
        keywords=keywords,
    )

    rd_justification = build_rd_justification_text(
        verrou=verrou,
        ranked_articles=ranked_articles,
    )

    warnings = []

    if not citation_plan["main_citations"]:
        warnings.append(
            "Aucun article Direct n’est disponible pour ce verrou ; la Phase 5 devra rédiger le gap avec prudence."
        )

    if citation_plan["background_citations"] and not citation_plan["main_citations"]:
        warnings.append(
            "Les articles disponibles sont surtout des articles de contexte ; ils ne doivent pas être utilisés comme preuves centrales."
        )

    return {
        "verrou_id": verrou.get("verrou_id"),
        "verrou_index": verrou.get("verrou_index"),
        "verrou_title": verrou.get("verrou_title"),
        "objectif_rd": verrou.get("objectif_rd"),
        "contexte_projet": verrou.get("contexte_projet"),
        "keywords": keywords,
        "articles_used_count": len(ranked_articles),
        "consultant_selected_articles_count": len(ranked_articles),
        "supporting_articles": build_supporting_articles(ranked_articles),
        "article_argumentation_map": build_article_argumentation_map(ranked_articles),
        "citation_targets": citation_plan["all_allowed_citations"],
        "citation_plan_for_phase_5": citation_plan,
        "citation_targets_by_usage": group_citations_by_usage(ranked_articles),
        "literature_coverage": literature_coverage,
        "article_limitations": article_limitations,
        "non_transposability": non_transposability,
        "scientific_gap": scientific_gap,
        "rd_justification": rd_justification,
        "risk_level": infer_risk_level(ranked_articles),
        "confidence": confidence_scores(ranked_articles),
        "warnings": warnings,
        "rules": {
            "evidence_source": "article_cards_only",
            "consultant_selection_respected": True,
            "no_consultant_article_rejected": True,
            "articles_are_qualified_not_filtered": True,
            "direct_and_related_tags_have_more_weight": True,
            "scientific_confidence_is_prudent": True,
            "keywords_prioritize_direct_articles": True,
            "domain_specific_hardcoding": False,
            "memory_v2_used_as_proof": False,
            "fewshot_used_as_proof": False,
            "can_be_used_by_phase_5_writer": True,
            "phase_4_5_should_use_evidence_bank": True,
        },
    }


# ============================================================
# Résumé global
# ============================================================

def build_global_gap_summary(verrous_gap_analysis: List[Dict[str, Any]]) -> Dict[str, Any]:
    risk_counter = Counter(item.get("risk_level") or "unknown" for item in verrous_gap_analysis)

    total_articles = sum(safe_int(item.get("articles_used_count")) for item in verrous_gap_analysis)

    grouped_global = {
        "direct_evidence": [],
        "related_evidence": [],
        "methodological_context": [],
        "background_context": [],
        "weak_context": [],
    }

    all_citations = []

    for item in verrous_gap_analysis:
        all_citations.extend(item.get("citation_targets") or [])

        grouped = item.get("citation_targets_by_usage") or {}
        for usage_type, citations in grouped.items():
            grouped_global.setdefault(usage_type, [])
            for citation in citations or []:
                if citation not in grouped_global[usage_type]:
                    grouped_global[usage_type].append(citation)

    unique_citations = []
    seen = set()

    for citation in all_citations:
        if not citation:
            continue
        if citation in seen:
            continue
        seen.add(citation)
        unique_citations.append(citation)

    scientific_confidences = []

    for item in verrous_gap_analysis:
        confidence = item.get("confidence") or {}
        scientific_confidences.append(safe_float(confidence.get("scientific_gap_confidence")))

    avg_scientific_confidence = (
        round(sum(scientific_confidences) / len(scientific_confidences), 3)
        if scientific_confidences
        else 0.0
    )

    return {
        "verrous_count": len(verrous_gap_analysis),
        "total_article_links": total_articles,
        "unique_citation_targets": unique_citations,
        "unique_citation_count": len(unique_citations),
        "citation_targets_by_usage": grouped_global,
        "risk_distribution": dict(risk_counter),
        "avg_consultant_selection_confidence": 1.0,
        "avg_scientific_gap_confidence": avg_scientific_confidence,
        "main_message": (
            "L’analyse du gap scientifique respecte la sélection humaine du consultant : aucun article choisi n’est rejeté. "
            "Les articles taggés Direct et Connexe reçoivent un poids argumentatif supérieur afin de guider la rédaction finale. "
            "La confiance scientifique reste volontairement prudente."
        ),
    }


def inspect_fewshot_payload(fewshot_payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(fewshot_payload, dict) or not fewshot_payload:
        return {
            "available": False,
            "used_as_proof": False,
        }

    quality = fewshot_payload.get("quality") or {}
    rules = fewshot_payload.get("rules") or {}

    return {
        "available": bool(fewshot_payload.get("ok")),
        "payload_type": fewshot_payload.get("payload_type"),
        "fewshot_count": fewshot_payload.get("fewshot_count"),
        "quality": quality,
        "used_as_proof": False,
        "raw_memory_examples_used_in_fewshot": rules.get("raw_memory_examples_used_in_fewshot"),
        "fewshots_generated_from_style_profile_templates": rules.get("fewshots_generated_from_style_profile_templates"),
    }


# ============================================================
# API principale Phase 4
# ============================================================

def build_scientific_gap_payload(
    organisme: str,
    project: str,
    year: str,
    selection_payload_path: Optional[str | Path] = None,
    article_cards_payload_path: Optional[str | Path] = None,
    fewshot_payload_path: Optional[str | Path] = None,
    output_path: Optional[str | Path] = None,
    max_articles_per_verrou: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Construit gap_scientific_payload.json.

    max_articles_per_verrou :
    - None = garder tous les articles associés au verrou ;
    - entier = limiter si besoin.
    """

    selection_path = (
        Path(selection_payload_path)
        if selection_payload_path
        else default_selection_payload_path(organisme, project, year)
    )

    cards_path = (
        Path(article_cards_payload_path)
        if article_cards_payload_path
        else default_article_cards_payload_path(organisme, project, year)
    )

    fewshot_path = (
        Path(fewshot_payload_path)
        if fewshot_payload_path
        else default_fewshot_payload_path(organisme, project, year)
    )

    out_path = (
        Path(output_path)
        if output_path
        else scientific_gap_output_path(organisme, project, year)
    )

    selection_payload = _read_json(selection_path, {}) or {}
    article_cards_payload = _read_json(cards_path, {}) or {}
    fewshot_payload = _read_json(fewshot_path, {}) or {}

    if not selection_payload:
        result = {
            "ok": False,
            "phase": "phase_4_scientific_gap",
            "step": "phase_4_scientific_gap_service",
            "status": "missing_selection_payload",
            "message": "selection_payload.json introuvable ou vide.",
            "input_paths": {
                "selection_payload": str(selection_path),
                "article_cards_payload": str(cards_path),
                "fewshot_payload": str(fewshot_path),
            },
            "output_path": str(out_path),
        }
        _write_json(out_path, result)
        return result

    if not article_cards_payload:
        result = {
            "ok": False,
            "phase": "phase_4_scientific_gap",
            "step": "phase_4_scientific_gap_service",
            "status": "missing_article_cards_payload",
            "message": "article_cards_payload.json introuvable ou vide.",
            "input_paths": {
                "selection_payload": str(selection_path),
                "article_cards_payload": str(cards_path),
                "fewshot_payload": str(fewshot_path),
            },
            "output_path": str(out_path),
        }
        _write_json(out_path, result)
        return result

    try:
        verrou_contract = build_confirmed_contract(
            selection_payload,
            source_path=str(selection_path),
        )
    except ContractError as exc:
        result = {
            **exc.as_dict(),
            "phase": "phase_4_scientific_gap",
            "step": "phase_4_scientific_gap_service",
            "input_paths": {
                "selection_payload": str(selection_path),
                "article_cards_payload": str(cards_path),
                "fewshot_payload": str(fewshot_path),
            },
            "output_path": str(out_path),
        }
        _write_json(out_path, result)
        return result

    selection_payload = {
        **selection_payload,
        "verrous": verrou_contract["verrous"],
        "verrou_fingerprint": verrou_contract["verrou_fingerprint"],
    }

    project_context = extract_project_context(selection_payload)

    extra_stopwords = dynamic_context_stopwords(
        organisme=organisme,
        project=project,
        year=str(year),
        project_context=project_context,
    )

    verrous = extract_verrous(selection_payload)
    article_cards = extract_article_cards(article_cards_payload)

    if not verrous:
        result = {
            "ok": False,
            "phase": "phase_4_scientific_gap",
            "step": "phase_4_scientific_gap_service",
            "status": "no_verrous_found",
            "message": "Aucun verrou détecté dans selection_payload.json.",
            "input_paths": {
                "selection_payload": str(selection_path),
                "article_cards_payload": str(cards_path),
                "fewshot_payload": str(fewshot_path),
            },
            "project_context": project_context,
            "article_cards_count": len(article_cards),
            "output_path": str(out_path),
        }
        _write_json(out_path, result)
        return result

    if not article_cards:
        result = {
            "ok": False,
            "phase": "phase_4_scientific_gap",
            "step": "phase_4_scientific_gap_service",
            "status": "no_article_cards_found",
            "message": "Aucune Article Card exploitable trouvée dans article_cards_payload.json.",
            "input_paths": {
                "selection_payload": str(selection_path),
                "article_cards_payload": str(cards_path),
                "fewshot_payload": str(fewshot_path),
            },
            "project_context": project_context,
            "verrous_count": len(verrous),
            "output_path": str(out_path),
        }
        _write_json(out_path, result)
        return result

    verrous_gap_analysis = []

    for verrou in verrous:
        analysis = analyze_gap_for_verrou(
            verrou=verrou,
            article_cards=article_cards,
            extra_stopwords=extra_stopwords,
            max_articles_per_verrou=max_articles_per_verrou,
        )
        verrous_gap_analysis.append(analysis)

    global_summary = build_global_gap_summary(verrous_gap_analysis)

    result = {
        "ok": True,
        "phase": "phase_4_scientific_gap",
        "step": "phase_4_scientific_gap_service",
        "payload_type": "gap_scientific_payload_v4_2_consultant_gap_builder_evidence_bank",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "organisme": organisme,
        "project": project,
        "year": str(year),
        "input_paths": {
            "selection_payload": str(selection_path),
            "article_cards_payload": str(cards_path),
            "fewshot_payload": str(fewshot_path),
        },
        "project_context": project_context,
        "selection_summary": {
            "verrous_count": len(verrous),
            "article_cards_count": len(article_cards),
            "consultant_selected_articles_count": len(article_cards),
            "selection_human_validated": True,
            "verrou_fingerprint": verrou_contract["verrou_fingerprint"],
        },
        "fewshot_status": inspect_fewshot_payload(fewshot_payload),
        "global_gap_summary": global_summary,
        "verrous_gap_analysis": verrous_gap_analysis,
        "rules": {
            "scientific_sources_allowed": "article_cards_only",
            "article_cards_as_proof": True,
            "phase2_evidence_bank_consumed": True,
            "selection_payload_as_project_context": True,
            "fewshot_as_style_only": True,
            "fewshot_as_proof": False,
            "memory_v2_as_proof": False,
            "must_not_invent_citations": True,
            "phase_5_writer_must_use_citation_targets": True,
            "consultant_selection_respected": True,
            "no_consultant_article_rejected": True,
            "articles_are_qualified_not_filtered": True,
            "direct_and_related_tags_have_more_weight": True,
            "scientific_confidence_is_prudent": True,
            "keywords_prioritize_direct_articles": True,
            "domain_specific_hardcoding": False,
        },
        "output_path": str(out_path),
    }

    _write_json(out_path, result)
    return result
