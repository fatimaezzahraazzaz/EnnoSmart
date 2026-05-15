"""
modules/NLP/router.py — EnnoSmart NLP v6.0 evidence-first
──────────────────────────────────────────────────────────────────────────────

Pipeline cible :

domains.json
   │
ExtractionResult
   → cleaner
   → normalizer
   → segmenter
   → evidence_mapper
   → aggregator
   ├→ domain_classifier
   └→ synthesizer
       → final_taxonomy_mapper
   → NLPResult

Changement majeur :
- Le NER, llm_extractor_smart et keyword_postprocessor ne sont plus le cœur du pipeline.
- On ne type plus des mots isolés.
- On classe des passages par fonction CIR/R&D.
- Les mots-clés sont dérivés des preuves validées.
- final_taxonomy_mapper remplit les champs spécialisés sans LLM.

Compatibilité :
- process_extraction()
- process_document()
- to_json()
- NLPConfig
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# IMPORTS NOUVEAU PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

try:
    from modules.NLP.cleaner import clean_chunks, _strip_structural_blocks
except Exception as exc:
    raise ImportError("Erreur import modules.NLP.cleaner") from exc

try:
    from modules.NLP.normalizer import normalize_chunks
except Exception as exc:
    raise ImportError("Erreur import modules.NLP.normalizer") from exc

try:
    from modules.NLP.segmenter import segment_chunks, summarize_passages
except Exception as exc:
    raise ImportError("Erreur import modules.NLP.segmenter") from exc

try:
    from modules.NLP.evidence_mapper import map_evidence
    EVIDENCE_MAPPER_AVAILABLE = True
except Exception as exc:
    map_evidence = None
    EVIDENCE_MAPPER_AVAILABLE = False
    logger.warning("evidence_mapper non disponible : %s", exc)

try:
    from modules.NLP.aggregator import aggregate
    AGGREGATOR_AVAILABLE = True
except Exception as exc:
    aggregate = None
    AGGREGATOR_AVAILABLE = False
    logger.warning("aggregator non disponible : %s", exc)

try:
    from modules.NLP.domain_classifier import classify_domain
    DOMAIN_CLASSIFIER_AVAILABLE = True
except Exception as exc:
    classify_domain = None
    DOMAIN_CLASSIFIER_AVAILABLE = False
    logger.warning("domain_classifier non disponible : %s", exc)

try:
    from modules.NLP.synthesizer import synthesize
    SYNTHESIZER_AVAILABLE = True
except Exception as exc:
    synthesize = None
    SYNTHESIZER_AVAILABLE = False
    logger.warning("synthesizer non disponible : %s", exc)

try:
    from modules.NLP.final_taxonomy_mapper import map_final_taxonomy
    FINAL_TAXONOMY_MAPPER_AVAILABLE = True
except Exception as exc:
    map_final_taxonomy = None
    FINAL_TAXONOMY_MAPPER_AVAILABLE = False
    logger.warning("final_taxonomy_mapper non disponible : %s", exc)


# NER optionnel uniquement. Pas utilisé par défaut.
try:
    from modules.NLP.ner import extract_entities_batch
    NER_AVAILABLE = True
except Exception as exc:
    extract_entities_batch = None
    NER_AVAILABLE = False
    logger.warning("NER optionnel non disponible : %s", exc)


# ══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class NLPConfig:
    # Compatibilité anciens flags.
    use_gliner: bool = False
    use_spacy: bool = False
    use_regex: bool = False
    gliner_model: str = "urchade/gliner_multi-v2.1"

    use_llm_refiner: bool = False
    llm_refiner_model: str = ""

    # Gardé pour compatibilité CLI : utilisé comme modèle principal du pipeline v6.
    use_llm_extractor: bool = True
    llm_extractor_model: str = "ollama:qwen2.5:7b-instruct"

    # Pipeline evidence-first.
    use_evidence_mapper: bool = True
    use_domain_classifier: bool = True
    use_synthesizer: bool = True
    use_final_taxonomy_mapper: bool = True

    # NER secondaire/debug.
    use_ner_enrichment: bool = False
    ner_on_visual_chunks: bool = False

    # Segmenter.
    max_passage_chars: int = 2800
    passage_overlap_chars: int = 250
    keep_small_passages: bool = True

    # Debug / identité.
    include_debug: bool = False
    terminology_text_only: bool = True

    organisme_name: Optional[str] = None
    organisme_id: Optional[str] = None
    file_hash: Optional[str] = None
    document_id: Optional[str] = None

    # domains.json.
    domains_path: Optional[str] = None

    # Compatibilité anciens champs.
    top_rag_keywords: int = 20
    min_rag_score: float = 3.5


# ══════════════════════════════════════════════════════════════════════════════
# SORTIE
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class EnrichedChunk:
    chunk_id: str
    index: int
    source: str
    content: str
    metadata: dict = field(default_factory=dict)


@dataclass
class DocumentMetadata:
    file_name: str = ""
    file_category: str = "unknown"
    source_tag: str = "DE_DOC"
    title: Optional[str] = None
    author: Optional[str] = None
    page_count: int = 0

    organisme_name: str = "Organisme inconnu"
    organisme_id: str = "organisme_inconnu"
    file_hash: str = ""
    document_id: str = ""

    domaine_principal: str = "non_classifié"
    domaines_scores: dict[str, int] = field(default_factory=dict)
    domaine_detail: dict = field(default_factory=dict)

    # Nouvelle sortie evidence-first.
    fiche_cir: dict = field(default_factory=dict)
    evidence_map: dict = field(default_factory=dict)
    aggregated_evidence: dict = field(default_factory=dict)
    lacunes: list[str] = field(default_factory=list)

    # Compatibilité ancien JSON.
    technologies: list[str] = field(default_factory=list)
    verrous_techniques: list[str] = field(default_factory=list)
    mots_cles_projet: dict = field(default_factory=lambda: {"high_confidence": [], "candidates": []})
    axes_projet: list[str] = field(default_factory=list)

    objet_recherche: list[str] = field(default_factory=list)
    sous_domaines: list[str] = field(default_factory=list)
    hypotheses_rd: list[str] = field(default_factory=list)
    protocoles_experimentaux: list[str] = field(default_factory=list)
    outils_technologies: list[str] = field(default_factory=list)
    modeles_algorithmes: list[str] = field(default_factory=list)
    architectures_systeme: list[str] = field(default_factory=list)
    jeux_donnees_benchmarks: list[str] = field(default_factory=list)
    metriques_evaluation: list[str] = field(default_factory=list)
    parametres_variables: list[str] = field(default_factory=list)
    normes_techniques: list[str] = field(default_factory=list)
    materiaux_composants: list[str] = field(default_factory=list)
    limitations_perspectives: list[str] = field(default_factory=list)

    objectifs_rd: list[str] = field(default_factory=list)
    resultats_rd: list[str] = field(default_factory=list)
    methodes_rd: list[str] = field(default_factory=list)
    composants_techniques: list[str] = field(default_factory=list)

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

    indicateurs_cir: dict = field(default_factory=lambda: {"etp": [], "montants": [], "jalons": []})
    montants: list[str] = field(default_factory=list)


@dataclass
class NLPResult:
    document_metadata: DocumentMetadata
    chunks: list[EnrichedChunk]
    debug: Optional[dict] = None


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS GÉNÉRIQUES
# ══════════════════════════════════════════════════════════════════════════════

def _obj_to_dict(obj: Any) -> dict:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "to_dict"):
        try:
            return obj.to_dict()
        except Exception:
            pass
    if hasattr(obj, "__dataclass_fields__"):
        try:
            return asdict(obj)
        except Exception:
            pass
    try:
        return dict(obj)
    except Exception:
        return {}


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _dedupe(values: list[str]) -> list[str]:
    out, seen = [], set()
    for v in values or []:
        text = re.sub(r"\s+", " ", str(v or "")).strip()
        if not text:
            continue
        key = text.lower()
        if key not in seen:
            seen.add(key)
            out.append(text)
    return out


def _get_doc_type(file_category: str) -> str:
    cat = str(file_category or "unknown").lower()
    mapping = {
        "pptx": "pptx", "presentation": "pptx",
        "docx": "docx", "word": "docx",
        "pdf": "pdf", "pdf_native": "pdf", "pdf_ocr": "pdf",
        "email": "email", "eml": "email", "msg": "email",
        "excel": "excel", "xlsx": "excel", "xls": "excel", "csv": "excel",
        "image": "image", "png": "image", "jpg": "image", "jpeg": "image",
    }
    return mapping.get(cat, "unknown")


def _source_tag_from_extraction(extraction_result: Any) -> str:
    if extraction_result is None:
        return "DE_DOC"
    tag = getattr(extraction_result, "source_tag", "DE_DOC")
    return str(tag.value) if hasattr(tag, "value") else str(tag)


def _slugify(value: str) -> str:
    import unicodedata
    text = str(value or "").lower().strip()
    if not text:
        return "organisme_inconnu"
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "organisme_inconnu"


def _infer_organisme(extraction_result: Any, doc_id: str) -> str:
    if extraction_result is not None:
        org = getattr(extraction_result, "organisme", None)
        if org:
            return str(org).strip()

    source_path = str(getattr(extraction_result, "source_path", "") or "") if extraction_result else ""
    if source_path:
        p = Path(source_path)
        ignored = {"projects", "projets", "project", "projet", "cir_final", "raw", "raw_documents", "documents", "data", "ennosmart"}
        for parent in p.parents:
            name = parent.name.strip()
            low = name.lower()
            if not name or low in ignored or re.match(r"projet[_\-\s]*\d+", low):
                continue
            if len(name) >= 3:
                return name

    parts = re.split(r"[-_]", str(doc_id or ""))
    ignored_parts = {"cir", "cir2024", "vf", "v1", "v2", "v3", "doc", "document", "rapport", "final"}
    for part in parts:
        p = part.strip()
        if len(p) >= 3 and p.lower() not in ignored_parts and not re.match(r"20\d{2}", p):
            return p

    return "Organisme inconnu"


def _resolve_identity(extraction_result: Any, doc_id: str, config: NLPConfig) -> tuple[str, str, str, str]:
    organisme_name = (config.organisme_name or _infer_organisme(extraction_result, doc_id) or "Organisme inconnu").strip()
    organisme_id = _slugify(config.organisme_id or organisme_name)
    file_hash = str(config.file_hash or "").strip()
    document_id = str(config.document_id or "").strip()
    if not document_id:
        document_id = f"{organisme_id}_{file_hash[:16]}" if file_hash else f"{organisme_id}_{_slugify(doc_id)}"
    return organisme_name, organisme_id, file_hash, document_id


def _chunk_source_type(content: str, doc_type: str) -> str:
    if doc_type == "pptx":
        return "presentation"
    if doc_type == "excel" or re.search(r"^\|.+\|", content or "", re.MULTILINE):
        return "table"
    if doc_type == "email":
        return "email_body"
    if doc_type == "image":
        return "visual"
    return "text"


def _extract_summary_from_fiche(fiche: dict, key: str) -> list[str]:
    items = fiche.get(key, [])
    out = []
    if isinstance(items, dict):
        val = items.get("resume") or items.get("text") or items.get("value")
        if val:
            out.append(str(val))
    elif isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                val = item.get("resume") or item.get("text") or item.get("value")
                if val:
                    out.append(str(val))
            elif item:
                out.append(str(item))
    elif items:
        out.append(str(items))
    return _dedupe(out)


def _concepts_from_aggregation(aggregated: Any, max_items: int = 24) -> list[str]:
    d = _obj_to_dict(aggregated)
    concepts = d.get("concepts") or d.get("technical_concepts") or []
    out = []

    if isinstance(concepts, dict):
        concepts = concepts.items()

    for c in concepts:
        if isinstance(c, tuple):
            out.append(str(c[0]))
        elif isinstance(c, dict):
            val = c.get("text") or c.get("concept") or c.get("name")
            if val:
                out.append(str(val))
        else:
            out.append(str(c))
    return _dedupe(out)[:max_items]


def _get_evidence_items_from_aggregation(aggregated: Any) -> list[dict]:
    d = _obj_to_dict(aggregated)

    for key in ["evidence", "items", "all_evidence", "preuves"]:
        v = d.get(key)
        if isinstance(v, list):
            return [x if isinstance(x, dict) else _obj_to_dict(x) for x in v]

    by_role = d.get("by_role")
    if isinstance(by_role, dict):
        out = []
        for role, values in by_role.items():
            for item in values or []:
                it = item if isinstance(item, dict) else _obj_to_dict(item)
                it.setdefault("role", role)
                out.append(it)
        return out

    return []


def _domain_to_metadata(domain_result: Any) -> tuple[str, dict[str, int], dict]:
    d = _obj_to_dict(domain_result)
    if not d:
        return "non_classifié", {}, {}

    label = (
        d.get("domaine_principal")
        or d.get("domain_label")
        or d.get("label")
        or d.get("niv3_label")
        or d.get("niv2_label")
        or d.get("niv1_label")
        or "non_classifié"
    )
    conf = float(d.get("confidence") or d.get("score") or 0.0)
    scores = {str(label): int(conf * 100)} if label and label != "non_classifié" else {}
    return str(label), scores, d


def _synthesis_to_fiche_and_keywords(synthesis: Any, aggregated: Any) -> tuple[dict, list[str], list[str]]:
    d = _obj_to_dict(synthesis)
    fiche = d.get("fiche_cir") if isinstance(d.get("fiche_cir"), dict) else d.get("fiche") if isinstance(d.get("fiche"), dict) else {}

    keywords = d.get("mots_cles") or d.get("keywords") or d.get("concepts_techniques") or []
    if isinstance(keywords, dict):
        kw = []
        for values in keywords.values():
            if isinstance(values, list):
                kw.extend([str(x) for x in values])
            elif values:
                kw.append(str(values))
        keywords = kw
    elif not isinstance(keywords, list):
        keywords = []

    lacunes = d.get("lacunes") or d.get("gaps") or []
    if not isinstance(lacunes, list):
        lacunes = []

    keywords = _dedupe([str(x) for x in keywords])
    if not keywords:
        keywords = _concepts_from_aggregation(aggregated)

    return fiche, keywords, _dedupe([str(x) for x in lacunes])


def _build_empty_metadata(
    doc_id: str,
    file_category_str: str,
    extraction_result: Any,
    config: NLPConfig,
) -> DocumentMetadata:
    org_name, org_id, file_hash, document_id = _resolve_identity(extraction_result, doc_id, config)
    return DocumentMetadata(
        file_name=doc_id,
        file_category=file_category_str,
        source_tag=_source_tag_from_extraction(extraction_result),
        title=getattr(extraction_result, "title", None) if extraction_result else None,
        author=getattr(extraction_result, "author", None) if extraction_result else None,
        page_count=getattr(extraction_result, "page_count", 0) if extraction_result else 0,
        organisme_name=org_name,
        organisme_id=org_id,
        file_hash=file_hash,
        document_id=document_id,
        organismes=[org_name],
    )



def _apply_final_taxonomy(meta: DocumentMetadata, final_taxonomy: Any) -> None:
    """
    Injecte les champs spécialisés produits par final_taxonomy_mapper.
    Le mapper ne remplace pas la fiche CIR/evidence_map, il complète la projection finale.
    """
    d = _obj_to_dict(final_taxonomy)
    if not d:
        return

    list_fields = [
        "technologies",
        "verrous_techniques",
        "axes_projet",
        "objet_recherche",
        "sous_domaines",
        "hypotheses_rd",
        "protocoles_experimentaux",
        "outils_technologies",
        "modeles_algorithmes",
        "architectures_systeme",
        "jeux_donnees_benchmarks",
        "metriques_evaluation",
        "parametres_variables",
        "normes_techniques",
        "materiaux_composants",
        "limitations_perspectives",
        "objectifs_rd",
        "resultats_rd",
        "methodes_rd",
        "composants_techniques",
        "livrables",
        "depenses_eligibles",
        "brevets",
        "partenaires_rd",
        "personnes",
        "organismes",
        "materiaux",
        "equipements",
        "lieux",
        "dates_periodes",
        "montants",
    ]

    for field_name in list_fields:
        value = d.get(field_name)
        if isinstance(value, list):
            cleaned = _dedupe([str(v) for v in value if str(v or "").strip()])
            if cleaned:
                setattr(meta, field_name, cleaned)

    mots_cles = d.get("mots_cles_projet")
    if isinstance(mots_cles, dict):
        high = _dedupe([str(v) for v in mots_cles.get("high_confidence", []) if str(v or "").strip()])
        cand = _dedupe([str(v) for v in mots_cles.get("candidates", []) if str(v or "").strip()])
        if high or cand:
            meta.mots_cles_projet = {"high_confidence": high, "candidates": cand}
            meta.technologies = high[:12] or meta.technologies

    indicateurs = d.get("indicateurs_cir")
    if isinstance(indicateurs, dict):
        meta.indicateurs_cir = {
            "etp": _dedupe([str(v) for v in indicateurs.get("etp", []) if str(v or "").strip()]),
            "montants": _dedupe([str(v) for v in indicateurs.get("montants", []) if str(v or "").strip()]),
            "jalons": _dedupe([str(v) for v in indicateurs.get("jalons", []) if str(v or "").strip()]),
        }

def _build_metadata(
    doc_id: str,
    file_category_str: str,
    extraction_result: Any,
    config: NLPConfig,
    evidence_result: Any,
    aggregated: Any,
    domain_result: Any,
    synthesis: Any,
    final_taxonomy: Any = None,
) -> DocumentMetadata:
    meta = _build_empty_metadata(doc_id, file_category_str, extraction_result, config)

    domaine, scores, detail = _domain_to_metadata(domain_result)
    meta.domaine_principal = domaine
    meta.domaines_scores = scores
    meta.domaine_detail = detail

    meta.evidence_map = _obj_to_dict(evidence_result)
    meta.aggregated_evidence = _obj_to_dict(aggregated)

    fiche, keywords, lacunes = _synthesis_to_fiche_and_keywords(synthesis, aggregated)
    meta.fiche_cir = fiche
    meta.lacunes = lacunes

    meta.mots_cles_projet = {
        "high_confidence": keywords[:12],
        "candidates": keywords[12:30],
    }
    meta.technologies = list(meta.mots_cles_projet["high_confidence"])
    meta.sous_domaines = _concepts_from_aggregation(aggregated, 10)

    # Compatibilité champs anciens, remplis depuis la fiche.
    meta.objet_recherche = _extract_summary_from_fiche(fiche, "objet_du_projet") or _extract_summary_from_fiche(fiche, "objet")
    meta.objectifs_rd = _extract_summary_from_fiche(fiche, "objectifs")
    meta.verrous_techniques = _extract_summary_from_fiche(fiche, "verrous")
    meta.methodes_rd = _dedupe(
        _extract_summary_from_fiche(fiche, "demarche")
        + _extract_summary_from_fiche(fiche, "méthodes")
        + _extract_summary_from_fiche(fiche, "methodes")
        + _extract_summary_from_fiche(fiche, "essais")
    )
    meta.resultats_rd = _extract_summary_from_fiche(fiche, "resultats") or _extract_summary_from_fiche(fiche, "résultats")
    # Ne pas confondre état de l'art avec limitations/perspectives.
    # Ces champs seront remplis proprement par final_taxonomy_mapper.
    meta.limitations_perspectives = []

    _apply_final_taxonomy(meta, final_taxonomy)

    return meta


# ══════════════════════════════════════════════════════════════════════════════
# POINTS D'ENTRÉE
# ══════════════════════════════════════════════════════════════════════════════

def process_extraction(extraction_result: Any, config: NLPConfig | None = None) -> NLPResult:
    cfg = config or NLPConfig()
    file_category = getattr(extraction_result, "file_category", None)
    file_category_str = file_category.value if hasattr(file_category, "value") else str(file_category)
    doc_type = _get_doc_type(file_category_str)

    return _run_pipeline(
        text_chunks=list(getattr(extraction_result, "text_chunks", []) or []),
        visual_chunks=list(getattr(extraction_result, "visual_chunks", []) or []),
        doc_id=Path(getattr(extraction_result, "file_name", "doc")).stem,
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
    timings: dict[str, float] = {}
    t_global = time.time()

    if not text_chunks and not visual_chunks:
        meta = _build_empty_metadata(doc_id, file_category_str, extraction_result, config)
        return NLPResult(meta, [], debug={"warning": "Aucun chunk en entrée."} if config.include_debug else None)

    # 1. Cleaner.
    t0 = time.time()
    pre_text = [_strip_structural_blocks(c or "", doc_type=doc_type)[0] for c in text_chunks]
    pre_visual = [_strip_structural_blocks(c or "", doc_type=doc_type)[0] for c in visual_chunks]
    clean_text_result = clean_chunks(pre_text, doc_type=doc_type)
    clean_visual_result = clean_chunks(pre_visual, doc_type=doc_type)
    cleaned_text = clean_text_result.clean_chunks
    cleaned_visual = clean_visual_result.clean_chunks
    timings["cleaner"] = round(time.time() - t0, 3)

    if not cleaned_text and not cleaned_visual:
        meta = _build_empty_metadata(doc_id, file_category_str, extraction_result, config)
        return NLPResult(meta, [], debug={"timings": timings, "warning": "Aucun chunk après cleaner."} if config.include_debug else None)

    # 2. Normalizer.
    t0 = time.time()
    norm_text_result = normalize_chunks(cleaned_text)
    norm_visual_result = normalize_chunks(cleaned_visual)
    normalized_text = norm_text_result.normalized_chunks
    normalized_visual = norm_visual_result.normalized_chunks
    normalized = normalized_text + normalized_visual
    n_text = len(normalized_text)
    timings["normalizer"] = round(time.time() - t0, 3)

    # 3. Segmenter.
    t0 = time.time()
    base_for_evidence = normalized_text if config.terminology_text_only else normalized
    passages = segment_chunks(
        base_for_evidence,
        doc_id=doc_id,
        max_chars=config.max_passage_chars,
        overlap_chars=config.passage_overlap_chars,
        keep_small=config.keep_small_passages,
    )
    segmenter_summary = summarize_passages(passages)
    timings["segmenter"] = round(time.time() - t0, 3)

    # 4. Evidence mapper.
    t0 = time.time()
    evidence_result = None
    if config.use_evidence_mapper and config.use_llm_extractor and EVIDENCE_MAPPER_AVAILABLE and map_evidence is not None:
        evidence_result = map_evidence(
            passages=passages,
            model=config.llm_extractor_model,
            enabled=True,
        )
    timings["evidence_mapper"] = round(time.time() - t0, 3)

    # 5. Aggregator.
    t0 = time.time()
    aggregated = None
    if evidence_result is not None and AGGREGATOR_AVAILABLE and aggregate is not None:
        aggregated = aggregate(evidence_result)
    timings["aggregator"] = round(time.time() - t0, 3)

    # 6a. Domain classifier.
    t0 = time.time()
    domain_result = None
    if aggregated is not None and config.use_domain_classifier and DOMAIN_CLASSIFIER_AVAILABLE and classify_domain is not None:
        domain_result = classify_domain(
            aggregated=aggregated,
            model=config.llm_extractor_model,
            enabled=True,
            domains_path=config.domains_path,
        )
    timings["domain_classifier"] = round(time.time() - t0, 3)

    # 6b. Synthesizer.
    t0 = time.time()
    synthesis = None
    if aggregated is not None and config.use_synthesizer and config.use_llm_extractor and SYNTHESIZER_AVAILABLE and synthesize is not None:
        synthesis = synthesize(
            aggregated=aggregated,
            domain_classification=domain_result,
            model=config.llm_extractor_model,
            enabled=True,
        )
    timings["synthesizer"] = round(time.time() - t0, 3)

    # 7. Final taxonomy mapper (sans LLM).
    t0 = time.time()
    final_taxonomy = None
    if (
        aggregated is not None
        and config.use_final_taxonomy_mapper
        and FINAL_TAXONOMY_MAPPER_AVAILABLE
        and map_final_taxonomy is not None
    ):
        final_taxonomy = map_final_taxonomy(
            aggregated=aggregated,
            synthesis=synthesis,
            domain_classification=domain_result,
        )
    timings["final_taxonomy_mapper"] = round(time.time() - t0, 3)

    # NER secondaire optionnel.
    ner_entities_by_chunk: dict[int, list[dict]] = {i: [] for i in range(len(normalized))}
    ner_debug = None
    if config.use_ner_enrichment and NER_AVAILABLE and extract_entities_batch is not None:
        t0 = time.time()
        try:
            scope = normalized if config.ner_on_visual_chunks else normalized[:n_text]
            sources = [_chunk_source_type(c, doc_type) for c in scope]
            ner_result = extract_entities_batch(
                scope,
                use_gliner=config.use_gliner,
                use_spacy=config.use_spacy,
                use_regex=config.use_regex,
                chunk_sources=sources,
            )
            for i, cr in enumerate(getattr(ner_result, "results", []) or []):
                ents = []
                for e in getattr(cr, "entities", []) or []:
                    txt = str(getattr(e, "text", "") or "").strip()
                    typ = str(getattr(e, "type", "") or "").strip()
                    if txt:
                        ents.append({
                            "text": txt,
                            "type": typ,
                            "confidence": round(float(getattr(e, "confidence", 0.0) or 0.0), 3),
                            "source": getattr(e, "source", "ner"),
                        })
                ner_entities_by_chunk[i] = ents
            ner_debug = {
                "enabled": True,
                "chunks": len(scope),
                "backend_stats": getattr(ner_result, "backend_stats", {}),
            }
        except Exception as exc:
            ner_debug = {"enabled": True, "error": str(exc)}
        timings["ner_optional"] = round(time.time() - t0, 3)

    # Metadata.
    doc_meta = _build_metadata(
        doc_id=doc_id,
        file_category_str=file_category_str,
        extraction_result=extraction_result,
        config=config,
        evidence_result=evidence_result,
        aggregated=aggregated,
        domain_result=domain_result,
        synthesis=synthesis,
        final_taxonomy=final_taxonomy,
    )

    # Chunks enrichis.
    passage_by_source_chunk: dict[int, list[dict]] = {}
    for p in passages:
        idx = getattr(p, "source_chunk_index", None)
        if idx is None:
            continue
        passage_by_source_chunk.setdefault(int(idx), []).append({
            "passage_id": getattr(p, "passage_id", ""),
            "section_title": getattr(p, "section_title", ""),
            "section_role": getattr(p, "section_role", "unknown"),
            "part_index": getattr(p, "part_index", 0),
        })

    enriched_chunks: list[EnrichedChunk] = []
    for i, content in enumerate(normalized):
        source = "text" if i < n_text else "visual"
        enriched_chunks.append(
            EnrichedChunk(
                chunk_id=f"{doc_id}_chunk_{i:04d}",
                index=i,
                source=source,
                content=content,
                metadata={
                    "file_name": doc_id,
                    "organisme_name": doc_meta.organisme_name,
                    "organisme_id": doc_meta.organisme_id,
                    "file_hash": doc_meta.file_hash,
                    "document_id": doc_meta.document_id,
                    "file_category": file_category_str,
                    "doc_type": doc_type,
                    "source_type": source,
                    "chunk_source_type": _chunk_source_type(content, doc_type),
                    "domaine_principal": doc_meta.domaine_principal,
                    "mots_cles_high_confidence": doc_meta.mots_cles_projet.get("high_confidence", []),
                    "mots_cles_candidates": doc_meta.mots_cles_projet.get("candidates", []),
                    "objet_recherche": doc_meta.objet_recherche,
                    "objectifs_rd": doc_meta.objectifs_rd,
                    "verrous_techniques": doc_meta.verrous_techniques,
                    "methodes_rd": doc_meta.methodes_rd,
                    "resultats_rd": doc_meta.resultats_rd,
                    "protocoles_experimentaux": doc_meta.protocoles_experimentaux,
                    "normes_techniques": doc_meta.normes_techniques,
                    "materiaux_composants": doc_meta.materiaux_composants,
                    "limitations_perspectives": doc_meta.limitations_perspectives,
                    "brevets": doc_meta.brevets,
                    "lacunes": doc_meta.lacunes,
                    "passages": passage_by_source_chunk.get(i, []),
                    "entities": ner_entities_by_chunk.get(i, []),
                },
            )
        )

    timings["total"] = round(time.time() - t_global, 3)

    debug = None
    if config.include_debug:
        debug = {
            "pipeline": "evidence_first_v6",
            "timings": timings,
            "doc_type": doc_type,
            "file_category": file_category_str,
            "modules_available": {
                "evidence_mapper": EVIDENCE_MAPPER_AVAILABLE,
                "aggregator": AGGREGATOR_AVAILABLE,
                "domain_classifier": DOMAIN_CLASSIFIER_AVAILABLE,
                "synthesizer": SYNTHESIZER_AVAILABLE,
                "final_taxonomy_mapper": FINAL_TAXONOMY_MAPPER_AVAILABLE,
                "ner_optional": NER_AVAILABLE,
            },
            "cleaning": {
                "text_chunks_in": len(text_chunks),
                "text_chunks_out": len(cleaned_text),
                "visual_chunks_in": len(visual_chunks),
                "visual_chunks_out": len(cleaned_visual),
                "text_chars_removed": clean_text_result.total_chars_removed,
                "visual_chars_removed": clean_visual_result.total_chars_removed,
            },
            "normalizer": {
                "text_substitutions": getattr(norm_text_result, "total_substitutions", 0),
                "visual_substitutions": getattr(norm_visual_result, "total_substitutions", 0),
            },
            "segmenter": segmenter_summary,
            "evidence_mapper": _obj_to_dict(evidence_result).get("stats", _obj_to_dict(evidence_result).get("_stats")),
            "aggregator": _obj_to_dict(aggregated).get("stats", _obj_to_dict(aggregated).get("_stats")),
            "domain_classifier": _obj_to_dict(domain_result),
            "synthesizer": _obj_to_dict(synthesis).get("stats", _obj_to_dict(synthesis).get("_stats")),
            "final_taxonomy_mapper": _obj_to_dict(final_taxonomy).get("stats", _obj_to_dict(final_taxonomy).get("_stats")),
            "ner_optional": ner_debug,
            "metadata_fusion": {
                "domaine_principal": doc_meta.domaine_principal,
                "mots_cles_projet": doc_meta.mots_cles_projet,
                "objet_recherche": doc_meta.objet_recherche,
                "objectifs_rd": doc_meta.objectifs_rd,
                "verrous_techniques": doc_meta.verrous_techniques,
                "methodes_rd": doc_meta.methodes_rd,
                "resultats_rd": doc_meta.resultats_rd,
                "protocoles_experimentaux": doc_meta.protocoles_experimentaux,
                "normes_techniques": doc_meta.normes_techniques,
                "materiaux_composants": doc_meta.materiaux_composants,
                "limitations_perspectives": doc_meta.limitations_perspectives,
                "brevets": doc_meta.brevets,
                "lacunes": doc_meta.lacunes,
            },
        }

    return NLPResult(document_metadata=doc_meta, chunks=enriched_chunks, debug=debug)


# ══════════════════════════════════════════════════════════════════════════════
# JSON EXPORT
# ══════════════════════════════════════════════════════════════════════════════

def to_json(result: NLPResult) -> dict:
    meta = result.document_metadata
    return {
        "pipeline": {
            "name": "extraction → cleaner → normalizer → segmenter → evidence_mapper → aggregator → domain_classifier → synthesizer → final_taxonomy_mapper",
            "version": "6.1-evidence-first-taxonomy",
            "router": "modules.NLP.router",
        },
        "document_metadata": {
            "file_name": meta.file_name,
            "file_category": meta.file_category,
            "source_tag": meta.source_tag,
            "organisme_name": meta.organisme_name,
            "organisme_id": meta.organisme_id,
            "file_hash": meta.file_hash,
            "document_id": meta.document_id,
            "title": meta.title,
            "author": meta.author,
            "page_count": meta.page_count,

            "domaine_principal": meta.domaine_principal,
            "domaines_scores": meta.domaines_scores,
            "domaine_detail": meta.domaine_detail,

            "fiche_cir": meta.fiche_cir,
            "evidence_map": meta.evidence_map,
            "aggregated_evidence": meta.aggregated_evidence,
            "lacunes": meta.lacunes,

            # Compatibilité ancien affichage.
            "technologies": meta.technologies,
            "verrous_techniques": meta.verrous_techniques,
            "mots_cles_projet": meta.mots_cles_projet,
            "axes_projet": meta.axes_projet,
            "objet_recherche": meta.objet_recherche,
            "sous_domaines": meta.sous_domaines,
            "hypotheses_rd": meta.hypotheses_rd,
            "protocoles_experimentaux": meta.protocoles_experimentaux,
            "outils_technologies": meta.outils_technologies,
            "modeles_algorithmes": meta.modeles_algorithmes,
            "architectures_systeme": meta.architectures_systeme,
            "jeux_donnees_benchmarks": meta.jeux_donnees_benchmarks,
            "metriques_evaluation": meta.metriques_evaluation,
            "parametres_variables": meta.parametres_variables,
            "normes_techniques": meta.normes_techniques,
            "materiaux_composants": meta.materiaux_composants,
            "limitations_perspectives": meta.limitations_perspectives,
            "objectifs_rd": meta.objectifs_rd,
            "resultats_rd": meta.resultats_rd,
            "methodes_rd": meta.methodes_rd,
            "composants_techniques": meta.composants_techniques,
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


process = process_document
