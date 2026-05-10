"""
modules/NLP/router.py — EnnoSmart NLP v2.1
──────────────────────────────────────────────────────────────────────────────
Point d'entrée unique du module NLP.

LIAISON extraction → NLP :
  L'agent appelle process_extraction(result) avec un ExtractionResult.

Pipeline :
  ExtractionResult
      ↓
  cleaner.py
      ↓
  normalizer.py
      ↓
  ner_smart.py
      ↓
  terminology_smart.py
      ↓
  rag_ranker.py via terminology_smart
      ↓
  NLPResult

Objectifs :
  - Propager doc_type partout : pptx, docx, pdf, email, excel, image.
  - Éviter que les artefacts extraction soient détectés comme technologies.
  - Ne pas laisser les chunks visuels polluer les métadonnées document.
  - Produire des chunks enrichis prêts pour le futur RAG.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# IMPORTS FLEXIBLES
# ══════════════════════════════════════════════════════════════════════════════

try:
    from modules.NLP.cleaner import clean_chunks, _strip_structural_blocks
except Exception:
    from modules.NLP.cleaner import clean_chunks, _strip_structural_blocks

try:
    from modules.NLP.normalizer import normalize_chunks
except Exception:
    from modules.NLP.normalizer import normalize_chunks

try:
    from modules.NLP.ner import extract_entities_batch
except Exception:
    from modules.NLP.ner import extract_entities_batch

try:
    from modules.NLP.Terminology_smart import analyze_terminology_smart
except Exception:
    from modules.NLP.Terminology_smart import analyze_terminology_smart


# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class NLPConfig:
    """Paramètres du pipeline NLP."""
    use_gliner: bool = True
    use_spacy: bool = False
    use_regex: bool = True
    gliner_model: str = "urchade/gliner_multi-v2.1"

    top_rag_keywords: int = 20
    min_rag_score: float = 3.5

    # Important :
    # Par défaut, on ne fait PAS le NER sur les chunks visuels.
    # Les visuels restent dans les chunks RAG, mais ne pilotent pas les métadonnées globales.
    ner_on_visual_chunks: bool = False

    # Important :
    # La terminologie globale document doit rester basée sur les chunks texte.
    terminology_text_only: bool = True

    include_debug: bool = False


# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION PAR TYPE DE DOCUMENT
# ══════════════════════════════════════════════════════════════════════════════

ACRONYM_BLACKLIST_COMMON: set[str] = {
    "FORMULES", "FORMULE", "PHYSIQUE", "INCONNU", "MECANIQUE", "MÉCANIQUE",
    "CHIMIE", "DÉTECTÉES", "DETECTEES", "CONFIANCE", "EXPLICATION",
    "DOMAINE", "LATEX", "QUALITÉ", "QUALITE",
    "OMML", "LLM", "HEURISTIC", "FORMULADOMAIN",
    "IMAGES", "IMAGE", "PAGE", "SECTION",
    "QWEN", "GPU", "CPU", "CACHE", "FULL",
}

ACRONYM_BLACKLIST_BY_TYPE: dict[str, set[str]] = {
    "docx": {
        "FORMULES", "FORMULE", "PHYSIQUE", "INCONNU", "MECANIQUE",
        "MÉCANIQUE", "OMML", "LLM", "HEURISTIC", "CONFIANCE",
        "DOMAINE", "LATEX", "EXPLICATION",
    },
    "pptx": {
        "SLIDE", "SLIDES", "NOTE", "NOTES",
        "PRÉSENTATEUR", "PRESENTATEUR",
        "ESSAI", "ESSAIS", "RESULTATS", "RÉSULTATS", "RESULTAT", "RÉSULTAT",
        "PRESENTATION", "PRÉSENTATION", "SOUTENANCE",
        "TABULAIRES", "ECCART", "ECART", "TAUX",
        "MASTER", "MERCI", "POUR", "VOTRE", "ATTENTION",
        "CONCLUSION", "SOMMAIRE", "OBJECTIF", "OBJECTIFS",
        "METHODOLOGIE", "MÉTHODOLOGIE",
        "PERPECTIVES", "PERSPECTIVES",
        "CARACTERISATION", "CARACTÉRISATION",
        "SIMULATION", "TABLEAU", "TABLEAUX",
        "FORMULES", "FORMULE", "PHYSIQUE", "INCONNU", "MECANIQUE",
        "MÉCANIQUE", "LLM", "LATEX", "DOMAINE", "CONFIANCE",
    },
    "pdf": {
        "FORMULES", "FORMULE", "PHYSIQUE", "INCONNU", "LLM",
        "HEURISTIC", "LATEX", "DOMAINE", "CONFIANCE", "EXPLICATION",
    },
    "email": {
        "FROM", "TO", "CC", "BCC", "RE", "FW", "FWD",
        "INBOX", "SENT", "DRAFT", "REPLY", "FORWARD",
        "SUBJECT", "DATE", "MIME", "SMTP",
    },
    "excel": {
        "TOTAL", "SOUS-TOTAL", "SOMME", "SUM", "AVG", "MAX", "MIN",
        "SHEET", "TAB", "CELL", "ROW", "COL", "REF",
        "TRUE", "FALSE", "NULL", "N/A", "NA",
    },
}

PROJET_AXE_EXCLUSION_PATTERNS: list[str] = [
    r"axe\s+des\s+[xy]\b",
    r"axe\s+[xy]\s+\(",
    r"axe\s+[xy]\s+semblent",
    r"CIR\s+car\s+",
    r"CIR\s+pour\s+illustrer",
    r"projet\s+sur\s+lequel\s+je",
    r"axes?\s*$",
]

PHYSICAL_UNITS: set[str] = {
    "GPa", "MPa", "kPa", "Pa", "kN", "MN", "GN",
    "Hz", "kHz", "MHz", "GHz", "THz",
    "nm", "µm", "mm", "cm", "km",
    "kg", "mg", "µg", "kJ", "MJ",
    "°C", "°K", "°F",
}


# ══════════════════════════════════════════════════════════════════════════════
# RÉSULTATS
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class EnrichedChunk:
    """Un chunk prêt à indexer dans le RAG."""
    chunk_id: str
    index: int
    source: str
    content: str
    metadata: dict = field(default_factory=dict)


@dataclass
class DocumentMetadata:
    """Métadonnées document-level pour le RAG."""
    domaine_principal: str = "non_classifié"
    domaines_scores: dict[str, int] = field(default_factory=dict)

    technologies: list[str] = field(default_factory=list)
    verrous_techniques: list[str] = field(default_factory=list)
    mots_cles_projet: dict = field(
        default_factory=lambda: {"high_confidence": [], "candidates": []}
    )
    axes_projet: list[str] = field(default_factory=list)

    objectifs_rd: list[str] = field(default_factory=list)
    resultats_rd: list[str] = field(default_factory=list)
    livrables: list[str] = field(default_factory=list)
    depenses_eligibles: list[str] = field(default_factory=list)
    brevets: list[str] = field(default_factory=list)
    partenaires_rd: list[str] = field(default_factory=list)

    personnes: list[str] = field(default_factory=list)
    organismes: list[str] = field(default_factory=list)
    materiaux: list[str] = field(default_factory=list)
    equipements: list[str] = field(default_factory=list)
    lieux: list[str] = field(default_factory=list)
    dates_periodes: list[str] = field(default_factory=list)

    indicateurs_cir: dict = field(
        default_factory=lambda: {"etp": [], "montants": [], "jalons": []}
    )
    montants: list[str] = field(default_factory=list)

    file_name: str = ""
    file_category: str = "unknown"
    source_tag: str = "DE_DOC"
    title: Optional[str] = None
    author: Optional[str] = None
    page_count: int = 0


@dataclass
class NLPResult:
    """Résultat complet du pipeline NLP."""
    document_metadata: DocumentMetadata
    chunks: list[EnrichedChunk]
    debug: Optional[dict] = None


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS INTERNES
# ══════════════════════════════════════════════════════════════════════════════

def _group_entities_by_type(entities: list) -> dict[str, list]:
    result: dict[str, list] = {}

    for ent in entities:
        t = getattr(ent, "type", "UNKNOWN")
        result.setdefault(t, []).append(ent)

    return result


def _get_doc_type(file_category: str) -> str:
    """Normalise le file_category en doc_type simple."""
    category = (file_category or "unknown").lower()

    mapping = {
        "pptx": "pptx",
        "presentation": "pptx",

        "docx": "docx",
        "word": "docx",

        "pdf": "pdf",
        "pdf_native": "pdf",
        "pdf_ocr": "pdf",
        "pdf_scanned": "pdf",

        "email": "email",
        "mail": "email",
        "eml": "email",
        "msg": "email",

        "excel": "excel",
        "xlsx": "excel",
        "xls": "excel",
        "csv": "excel",

        "image": "image",
        "png": "image",
        "jpg": "image",
        "jpeg": "image",
    }

    return mapping.get(category, category if category in mapping.values() else "unknown")


def _get_chunk_source_type(chunk: str, doc_type: str) -> str:
    """
    Détermine le type de chunk pour ajuster la confiance NER.
    """
    text = (chunk or "").strip()
    upper = text[:300].upper()

    if upper.startswith("[IMAGE") or "[QUALITÉ:" in upper or "[QUALITE:" in upper:
        return "visual"

    if doc_type == "pptx":
        return "presentation"

    if doc_type == "excel" or "[TABLEAU" in upper:
        return "table"

    if doc_type == "email":
        return "email_body"

    return "text"


def _filter_projet_axe_entities(entities: list) -> list:
    """Filtre les faux positifs PROJET_AXE."""
    import re

    filtered = []

    for ent in entities:
        if getattr(ent, "type", "") != "PROJET_AXE":
            filtered.append(ent)
            continue

        text = getattr(ent, "text", "").strip()

        is_noise = any(
            re.search(pat, text, re.IGNORECASE)
            for pat in PROJET_AXE_EXCLUSION_PATTERNS
        )

        if len(text) < 4 or text.lower() in {"axes", "axe"}:
            is_noise = True

        if not is_noise:
            filtered.append(ent)

    return filtered


def _reclassify_physical_units(entities: list) -> list:
    """Reclasse GPa, MHz, mm… de MONTANT_CIR vers AUTRE."""
    for ent in entities:
        if getattr(ent, "type", "") == "MONTANT_CIR":
            if getattr(ent, "text", "").strip() in PHYSICAL_UNITS:
                ent.type = "AUTRE"

    return entities


def _filter_blacklisted_entities(entities: list, doc_type: str) -> list:
    """Supprime les artefacts structurels restants après NER."""
    blacklist = ACRONYM_BLACKLIST_COMMON | ACRONYM_BLACKLIST_BY_TYPE.get(doc_type, set())

    filtered = []

    for ent in entities:
        text = getattr(ent, "text", "").strip()
        upper = text.upper()

        if upper in blacklist:
            continue

        if upper.startswith(("SLIDE", "NOTES", "FORMULE", "LATEX", "DOMAINE", "CONFIANCE")):
            continue

        if "FORMULADOMAIN" in upper:
            continue

        filtered.append(ent)

    return filtered


def _texts(lst) -> list[str]:
    return [e.text for e in (lst or []) if getattr(e, "text", None)]


def _source_tag_from_extraction(extraction_result: Any) -> str:
    if extraction_result is None:
        return "DE_DOC"

    source_tag = getattr(extraction_result, "source_tag", "DE_DOC")

    if hasattr(source_tag, "value"):
        return str(source_tag.value)

    return str(source_tag)


def _get_rag_keyword_structure(rag_ready: dict) -> dict:
    value = rag_ready.get("mots_cles_projet", {"high_confidence": [], "candidates": []})

    if isinstance(value, dict):
        return {
            "high_confidence": list(value.get("high_confidence", [])),
            "candidates": list(value.get("candidates", [])),
        }

    # Compatibilité avec ancienne version : liste simple.
    if isinstance(value, list):
        return {
            "high_confidence": value[:12],
            "candidates": [],
        }

    return {"high_confidence": [], "candidates": []}


# ══════════════════════════════════════════════════════════════════════════════
# PIPELINE PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

def process_extraction(
    extraction_result,
    config: NLPConfig | None = None,
) -> NLPResult:
    """
    Pipeline NLP complet à partir d'un ExtractionResult.
    """
    cfg = config or NLPConfig()

    file_category = getattr(extraction_result, "file_category", None)
    file_category_str = file_category.value if hasattr(file_category, "value") else str(file_category)
    doc_type = _get_doc_type(file_category_str)

    text_chunks = list(getattr(extraction_result, "text_chunks", []))
    visual_chunks = list(getattr(extraction_result, "visual_chunks", []))
    doc_id = Path(getattr(extraction_result, "file_name", "doc")).stem

    return _run_pipeline(
        text_chunks=text_chunks,
        visual_chunks=visual_chunks,
        doc_id=doc_id,
        doc_type=doc_type,
        file_category_str=file_category_str,
        extraction_result=extraction_result,
        config=cfg,
    )


def process_document(
    text_chunks: list[str],
    visual_chunks: list[str] | None = None,
    doc_id: str = "doc",
    config: NLPConfig | None = None,
    file_category: str = "unknown",
) -> NLPResult:
    """
    Point d'entrée alternatif pour tests.
    """
    return _run_pipeline(
        text_chunks=text_chunks,
        visual_chunks=visual_chunks or [],
        doc_id=doc_id,
        doc_type=_get_doc_type(file_category),
        file_category_str=file_category,
        extraction_result=None,
        config=config or NLPConfig(),
    )


def _run_pipeline(
    text_chunks: list[str],
    visual_chunks: list[str],
    doc_id: str,
    doc_type: str,
    file_category_str: str,
    extraction_result: Any,
    config: NLPConfig,
) -> NLPResult:
    """
    Orchestration interne du pipeline NLP.
    """
    timings = {}

    if not text_chunks and not visual_chunks:
        return NLPResult(
            document_metadata=DocumentMetadata(
                file_category=file_category_str,
                file_name=doc_id,
            ),
            chunks=[],
        )

    # ──────────────────────────────────────────────────────────────────────
    # ÉTAPE 0 — STRIP STRUCTUREL AVANT CLEANER
    # Objectif : supprimer très tôt les blocs techniques injectés par
    # l'extraction ([FORMULES], LaTeX:, Domaine:, Confiance:, etc.).
    # Ainsi, GLiNER / NER ne voient plus ces marqueurs.
    # Important : on garde le traitement séparé texte / visuel pour conserver
    # un n_text exact après normalisation.
    # ──────────────────────────────────────────────────────────────────────
    t0 = time.time()

    pre_cleaned_text: list[str] = []
    pre_cleaned_visual: list[str] = []
    strip_text_removed = 0
    strip_visual_removed = 0

    for chunk in text_chunks:
        original = chunk or ""
        stripped, _ = _strip_structural_blocks(original, doc_type=doc_type)
        pre_cleaned_text.append(stripped)
        strip_text_removed += max(0, len(original) - len(stripped))

    for chunk in visual_chunks:
        original = chunk or ""
        stripped, _ = _strip_structural_blocks(original, doc_type=doc_type)
        pre_cleaned_visual.append(stripped)
        strip_visual_removed += max(0, len(original) - len(stripped))

    timings["pre_strip_structural"] = round(time.time() - t0, 3)

    logger.info(
        "NLP [pre-strip] %s [%s] text=%d visual=%d | %d chars structurels supprimés",
        doc_id,
        doc_type,
        len(pre_cleaned_text),
        len(pre_cleaned_visual),
        strip_text_removed + strip_visual_removed,
    )

    # ──────────────────────────────────────────────────────────────────────
    # ÉTAPE 1 — CLEANER
    # Important : nettoyage séparé texte / visuel pour garder n_text exact.
    # Le cleaner reçoit déjà des chunks sans marqueurs structurels lourds.
    # ──────────────────────────────────────────────────────────────────────
    t0 = time.time()

    clean_text_result = clean_chunks(pre_cleaned_text, doc_type=doc_type)
    clean_visual_result = clean_chunks(pre_cleaned_visual, doc_type=doc_type)

    cleaned_text = clean_text_result.clean_chunks
    cleaned_visual = clean_visual_result.clean_chunks

    cleaned = cleaned_text + cleaned_visual
    n_text = len(cleaned_text)

    timings["cleaner"] = round(time.time() - t0, 3)

    total_chars_removed = (
        strip_text_removed
        + strip_visual_removed
        + clean_text_result.total_chars_removed
        + clean_visual_result.total_chars_removed
    )

    logger.info(
        "NLP [cleaner] %s [%s] text=%d→%d visual=%d→%d | %d chars supprimés au total",
        doc_id,
        doc_type,
        len(text_chunks),
        len(cleaned_text),
        len(visual_chunks),
        len(cleaned_visual),
        total_chars_removed,
    )

    if not cleaned:
        return NLPResult(
            document_metadata=DocumentMetadata(
                file_category=file_category_str,
                file_name=doc_id,
            ),
            chunks=[],
            debug={
                "timings": timings,
                "doc_type": doc_type,
                "warning": "Tous les chunks ont été supprimés par le cleaner.",
            } if config.include_debug else None,
        )

    # ──────────────────────────────────────────────────────────────────────
    # ÉTAPE 2 — NORMALIZER
    # ──────────────────────────────────────────────────────────────────────
    t0 = time.time()

    norm_text_result = normalize_chunks(cleaned_text)
    norm_visual_result = normalize_chunks(cleaned_visual)

    normalized_text = norm_text_result.normalized_chunks
    normalized_visual = norm_visual_result.normalized_chunks

    normalized = normalized_text + normalized_visual
    n_text = len(normalized_text)

    timings["normalizer"] = round(time.time() - t0, 3)

    total_substitutions = (
        getattr(norm_text_result, "total_substitutions", 0)
        + getattr(norm_visual_result, "total_substitutions", 0)
    )

    substitution_types = {}

    for result in (norm_text_result, norm_visual_result):
        for key, value in dict(getattr(result, "substitution_types", {})).items():
            substitution_types[key] = substitution_types.get(key, 0) + value

    # ──────────────────────────────────────────────────────────────────────
    # ÉTAPE 3 — NER SMART
    # ──────────────────────────────────────────────────────────────────────
    t0 = time.time()

    ner_scope = normalized if config.ner_on_visual_chunks else normalized[:n_text]

    chunk_sources = [
        _get_chunk_source_type(chunk, doc_type)
        for chunk in ner_scope
    ]

    ner_result = extract_entities_batch(
        ner_scope,
        use_gliner=config.use_gliner,
        use_spacy=config.use_spacy,
        use_regex=config.use_regex,
        chunk_sources=chunk_sources,
    )

    all_entities = []
    text_entities = []

    for i, chunk_result in enumerate(ner_result.results):
        chunk_result.entities = _filter_projet_axe_entities(chunk_result.entities)
        chunk_result.entities = _reclassify_physical_units(chunk_result.entities)
        chunk_result.entities = _filter_blacklisted_entities(chunk_result.entities, doc_type)

        all_entities.extend(chunk_result.entities)

        if i < n_text:
            text_entities.extend(chunk_result.entities)

    timings["ner"] = round(time.time() - t0, 3)

    logger.info(
        "NLP [ner] %s : %d entités | backends=%s",
        doc_id,
        len(all_entities),
        getattr(ner_result, "backend_stats", {}),
    )

    # ──────────────────────────────────────────────────────────────────────
    # ÉTAPE 4 — TERMINOLOGY + RAG RANKER
    # ──────────────────────────────────────────────────────────────────────
    t0 = time.time()

    term_chunks = normalized[:n_text] if config.terminology_text_only else normalized
    term_entities = text_entities if config.terminology_text_only else all_entities

    term_result = analyze_terminology_smart(
        chunks=term_chunks,
        ner_entities=term_entities,
        top_rag_keywords=config.top_rag_keywords,
        doc_type=doc_type,
    )

    timings["terminology"] = round(time.time() - t0, 3)

    logger.info(
        "NLP [terminology] %s : doc_type=%s | domaine=%s | entités_métier=%d",
        doc_id,
        doc_type,
        term_result.domaine_principal,
        term_result.total_entities,
    )

    # ──────────────────────────────────────────────────────────────────────
    # DOCUMENT METADATA
    # ──────────────────────────────────────────────────────────────────────
    rag_ready = (
        term_result.to_rag_ready_dict()
        if hasattr(term_result, "to_rag_ready_dict")
        else {}
    )

    indicateurs = rag_ready.get("indicateurs_cir", {"etp": [], "montants": [], "jalons": []})
    mots_cles_projet = _get_rag_keyword_structure(rag_ready)

    doc_meta = DocumentMetadata(
        domaine_principal=term_result.domaine_principal,
        domaines_scores=term_result.domaines_scores,

        technologies=rag_ready.get("technologies", []),
        verrous_techniques=rag_ready.get("verrous_techniques", []),
        mots_cles_projet=mots_cles_projet,
        axes_projet=rag_ready.get("axes_projet", []),

        objectifs_rd=_texts(getattr(term_result, "objectifs_rd", [])),
        resultats_rd=_texts(getattr(term_result, "resultats_rd", [])),
        livrables=_texts(getattr(term_result, "livrables", [])),
        depenses_eligibles=_texts(getattr(term_result, "depenses_eligibles", [])),
        brevets=_texts(getattr(term_result, "brevets", [])),
        partenaires_rd=_texts(getattr(term_result, "partenaires_rd", [])),

        personnes=_texts(getattr(term_result, "personnes", [])),
        organismes=_texts(getattr(term_result, "organismes", [])),
        materiaux=_texts(getattr(term_result, "materiaux", [])),
        equipements=_texts(getattr(term_result, "equipements", [])),
        lieux=_texts(getattr(term_result, "lieux", [])),
        dates_periodes=_texts(getattr(term_result, "dates_periodes", [])),

        indicateurs_cir=indicateurs,
        montants=indicateurs.get("montants", []),

        file_name=doc_id,
        file_category=file_category_str,
        source_tag=_source_tag_from_extraction(extraction_result),
        title=getattr(extraction_result, "title", None) if extraction_result else None,
        author=getattr(extraction_result, "author", None) if extraction_result else None,
        page_count=getattr(extraction_result, "page_count", 0) if extraction_result else 0,
    )

    # ──────────────────────────────────────────────────────────────────────
    # CHUNKS ENRICHIS
    # ──────────────────────────────────────────────────────────────────────
    entities_by_chunk = {i: [] for i in range(len(normalized))}

    for i, chunk_result in enumerate(ner_result.results):
        entities_by_chunk[i] = [
            {
                "text": e.text,
                "type": e.type,
                "confidence": round(float(e.confidence), 3),
            }
            for e in chunk_result.entities
        ]

    enriched_chunks: list[EnrichedChunk] = []

    for i, content in enumerate(normalized):
        source = "text" if i < n_text else "visual"
        chunk_source_type = _get_chunk_source_type(content, doc_type)

        enriched_chunks.append(
            EnrichedChunk(
                chunk_id=f"{doc_id}_chunk_{i:04d}",
                index=i,
                source=source,
                content=content,
                metadata={
                    "file_name": doc_id,
                    "file_category": file_category_str,
                    "doc_type": doc_type,
                    "source_type": source,
                    "chunk_source_type": chunk_source_type,

                    "domaine_principal": term_result.domaine_principal,
                    "technologies": doc_meta.technologies,
                    "verrous_techniques": doc_meta.verrous_techniques,
                    "mots_cles_high_confidence": doc_meta.mots_cles_projet.get("high_confidence", []),
                    "mots_cles_candidates": doc_meta.mots_cles_projet.get("candidates", []),

                    "has_formulas": "[FORMULES" in content.upper() or "LATEX" in content.upper(),
                    "has_images": "[IMAGE" in content.upper() or "[IMAGES" in content.upper(),
                    "entities": entities_by_chunk.get(i, []),
                },
            )
        )

    # ──────────────────────────────────────────────────────────────────────
    # DEBUG
    # ──────────────────────────────────────────────────────────────────────
    debug = None

    if config.include_debug:
        entities_by_type = _group_entities_by_type(all_entities)

        debug = {
            "timings": timings,
            "doc_type": doc_type,
            "file_category": file_category_str,

            "cleaning": {
                "text_chunks_in": len(text_chunks),
                "text_chunks_after_pre_strip": len(pre_cleaned_text),
                "text_chunks_out": len(cleaned_text),
                "visual_chunks_in": len(visual_chunks),
                "visual_chunks_after_pre_strip": len(pre_cleaned_visual),
                "visual_chunks_out": len(cleaned_visual),
                "pre_strip_chars_removed": strip_text_removed + strip_visual_removed,
                "chars_removed": total_chars_removed,
                "text_transformations": clean_text_result.transformations_summary,
                "visual_transformations": clean_visual_result.transformations_summary,
            },

            "normalization": {
                "substitutions": total_substitutions,
                "substitution_types": substitution_types,
            },

            "ner": {
                "ner_on_visual_chunks": config.ner_on_visual_chunks,
                "ner_scope_chunks": len(ner_scope),
                "total_entities": len(all_entities),
                "text_entities": len(text_entities),
                "backend_stats": getattr(ner_result, "backend_stats", {}),
                "entities_by_type": {k: len(v) for k, v in entities_by_type.items()},
            },

            "terminology": (
                term_result.to_dict()
                if hasattr(term_result, "to_dict")
                else {}
            ),
        }

    return NLPResult(
        document_metadata=doc_meta,
        chunks=enriched_chunks,
        debug=debug,
    )


# ══════════════════════════════════════════════════════════════════════════════
# EXPORT JSON
# ══════════════════════════════════════════════════════════════════════════════

def to_json(result: NLPResult) -> dict:
    """Convertit un NLPResult en dict JSON sérialisable pour le RAG."""
    meta = result.document_metadata

    return {
        "pipeline": {
            "name": "extraction → cleaner → normalizer → ner_smart → terminology_smart → rag_ranker",
            "version": "4.1",
            "router": "modules.NLP.router",
        },

        "document_metadata": {
            "file_name": meta.file_name,
            "file_category": meta.file_category,
            "source_tag": meta.source_tag,
            "title": meta.title,
            "author": meta.author,
            "page_count": meta.page_count,

            "domaine_principal": meta.domaine_principal,
            "domaines_scores": meta.domaines_scores,

            "technologies": meta.technologies,
            "verrous_techniques": meta.verrous_techniques,
            "mots_cles_projet": meta.mots_cles_projet,
            "axes_projet": meta.axes_projet,

            "objectifs_rd": meta.objectifs_rd,
            "resultats_rd": meta.resultats_rd,
            "livrables": meta.livrables,
            "depenses_eligibles": meta.depenses_eligibles,
            "brevets": meta.brevets,
            "partenaires_rd": meta.partenaires_rd,

            "personnes": meta.personnes,
            "organismes": meta.organismes,
            "materiaux": meta.materiaux,
            "equipements": meta.equipements,
            "lieux": meta.lieux,
            "dates_periodes": meta.dates_periodes,

            "indicateurs_cir": meta.indicateurs_cir,
            "montants": meta.montants,
        },

        "chunks": [
            {
                "chunk_id": c.chunk_id,
                "index": c.index,
                "source": c.source,
                "content": c.content,
                "metadata": c.metadata,
            }
            for c in result.chunks
        ],

        "debug": result.debug,
    }