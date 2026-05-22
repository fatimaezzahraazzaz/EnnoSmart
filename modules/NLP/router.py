"""
modules/NLP/router.py — EnnoSmart NLP V7.2.0 (V7 + apports V8.1)
──────────────────────────────────────────────────────────────────────────────

Changements V7.2.0 vs V7.1.2 (apports V8.1) :
- NOUVEAU DocumentMetadata : champs domaine_applicatif, domaine_scientifique_detaille,
  etat_art (liste plate) ajoutés. Ils étaient calculés dans les modules mais jamais
  exposés dans le JSON final — c'est le bug principal identifié dans la comparaison V7/V8.
- NOUVEAU _apply_final_taxonomy : mappe les nouveaux champs V7.2.0 depuis final_taxonomy.
- NOUVEAU _build_metadata : passe domain_result à _apply_final_taxonomy pour extraire
  domaine_applicatif et domaine_scientifique_detaille.
- NOUVEAU to_json : expose domaine_applicatif, domaine_scientifique_detaille, etat_art.
- Pipeline et logique interne identiques à V7.1.2.
"""

from __future__ import annotations

import inspect
import logging
import os
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

# V7.2.1 : évite les vérifications réseau HuggingFace si le modèle est déjà en cache
# Gain : ~10s au démarrage. Désactiver si vous changez de modèle GLiNER.
if not os.environ.get("HF_HUB_OFFLINE"):
    os.environ["HF_HUB_OFFLINE"] = "1"

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# IMPORTS CORE
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
    SEGMENTER_AVAILABLE = True
except Exception as exc:
    segment_chunks = None
    summarize_passages = None
    SEGMENTER_AVAILABLE = False
    logger.warning("segmenter non disponible : %s", exc)


# ══════════════════════════════════════════════════════════════════════════════
# IMPORTS V7 SECTION-FIRST
# ══════════════════════════════════════════════════════════════════════════════

try:
    from modules.NLP.document_structure_mapper import map_document_structure
    DOCUMENT_STRUCTURE_MAPPER_AVAILABLE = True
except Exception as exc:
    map_document_structure = None
    DOCUMENT_STRUCTURE_MAPPER_AVAILABLE = False
    logger.warning("document_structure_mapper non disponible : %s", exc)

try:
    import modules.NLP.section_extractor as section_extractor_module
    SECTION_EXTRACTOR_AVAILABLE = True
except Exception as exc:
    section_extractor_module = None
    SECTION_EXTRACTOR_AVAILABLE = False
    logger.warning("section_extractor non disponible : %s", exc)

try:
    from modules.NLP.role_postprocessor import postprocess_evidence_roles
    ROLE_POSTPROCESSOR_AVAILABLE = True
except Exception as exc:
    postprocess_evidence_roles = None
    ROLE_POSTPROCESSOR_AVAILABLE = False
    logger.warning("role_postprocessor non disponible : %s", exc)

try:
    from modules.NLP.evidence_validator import validate_evidence
    EVIDENCE_VALIDATOR_AVAILABLE = True
except Exception as exc:
    validate_evidence = None
    EVIDENCE_VALIDATOR_AVAILABLE = False
    logger.warning("evidence_validator non disponible : %s", exc)

try:
    from modules.NLP.technical_terms_extractor import extract_technical_terms
    TECHNICAL_TERMS_EXTRACTOR_AVAILABLE = True
except Exception as exc:
    extract_technical_terms = None
    TECHNICAL_TERMS_EXTRACTOR_AVAILABLE = False
    logger.warning("technical_terms_extractor non disponible : %s", exc)

try:
    from modules.NLP.quality_reporter import build_quality_report
    QUALITY_REPORTER_AVAILABLE = True
except Exception as exc:
    build_quality_report = None
    QUALITY_REPORTER_AVAILABLE = False
    logger.warning("quality_reporter non disponible : %s", exc)


# ══════════════════════════════════════════════════════════════════════════════
# IMPORTS FALLBACK
# ══════════════════════════════════════════════════════════════════════════════

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

try:
    from modules.NLP.final_output_guard import apply_final_output_guard
    FINAL_OUTPUT_GUARD_AVAILABLE = True
except Exception as exc:
    apply_final_output_guard = None
    FINAL_OUTPUT_GUARD_AVAILABLE = False
    logger.warning("final_output_guard non disponible : %s", exc)


# ══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class NLPConfig:
    use_gliner: bool = False
    use_spacy: bool = False
    use_regex: bool = False
    use_llm_refiner: bool = False
    llm_refiner_model: str = ""
    use_llm_extractor: bool = True
    llm_extractor_model: str = "ollama:qwen3:4b-instruct"
    use_document_structure_mapper: bool = True
    use_section_extractor: bool = True
    use_role_postprocessor: bool = True
    use_evidence_validator: bool = True
    use_technical_terms_extractor: bool = True
    use_quality_reporter: bool = True
    use_evidence_mapper: bool = True
    max_llm_passages: int = 24
    use_domain_classifier: bool = True
    use_synthesizer: bool = True
    use_final_taxonomy_mapper: bool = True
    use_gliner_ner: bool = False
    # V7.2.1 : modèle déjà en cache, évite le téléchargement du medium-v2.5 (1.67GB)
    gliner_model: str = "urchade/gliner_multi-v2.1"
    # V7.2.1 : réduit de 2800 à 2000 pour diminuer la charge LLM par passage
    max_passage_chars: int = 2000
    passage_overlap_chars: int = 200
    keep_small_passages: bool = True
    include_debug: bool = False
    terminology_text_only: bool = True
    organisme_name: Optional[str] = None
    organisme_id: Optional[str] = None
    file_hash: Optional[str] = None
    document_id: Optional[str] = None
    domains_path: Optional[str] = None
    top_rag_keywords: int = 20
    min_rag_score: float = 3.5


# ══════════════════════════════════════════════════════════════════════════════
# DATACLASSES SORTIE
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

    # ── NOUVEAU V8.1 : champs domaine enrichis ────────────────────────────────
    domaine_applicatif: Optional[str] = None
    domaine_scientifique_detaille: Optional[str] = None
    # ─────────────────────────────────────────────────────────────────────────

    # V7
    document_structure: dict = field(default_factory=dict)
    fiche_cir: dict = field(default_factory=dict)
    evidence_map: dict = field(default_factory=dict)
    aggregated_evidence: dict = field(default_factory=dict)
    technical_terms: dict = field(default_factory=dict)
    quality_report: dict = field(default_factory=dict)
    validation_report: dict = field(default_factory=dict)
    role_postprocess_stats: dict = field(default_factory=dict)
    lacunes: list[str] = field(default_factory=list)

    # Compatibilité ancien JSON
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

    # ── NOUVEAU V8.1 : champs enrichis ───────────────────────────────────────
    etat_art: list[str] = field(default_factory=list)
    # ─────────────────────────────────────────────────────────────────────────


@dataclass
class NLPResult:
    document_metadata: DocumentMetadata
    chunks: list[EnrichedChunk]
    debug: Optional[dict] = None


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS GÉNÉRIQUES (identiques à V7.1.2)
# ══════════════════════════════════════════════════════════════════════════════

def _obj_to_dict(obj: Any) -> dict:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "to_dict"):
        try:
            data = obj.to_dict()
            return data if isinstance(data, dict) else {}
        except Exception:
            pass
    if hasattr(obj, "__dataclass_fields__"):
        try:
            return asdict(obj)
        except Exception:
            pass
    if isinstance(obj, SimpleNamespace):
        return vars(obj)
    try:
        data = dict(obj)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _dedupe(values: list[str], max_items: int | None = None) -> list[str]:
    out, seen = [], set()
    for v in values or []:
        text = re.sub(r"\s+", " ", str(v or "")).strip(" \t\n\r;:,.")
        if not text:
            continue
        key = text.lower()
        if key not in seen:
            seen.add(key)
            out.append(text)
        if max_items and len(out) >= max_items:
            break
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


def _safe_call(fn: Any, *args: Any, **kwargs: Any) -> Any:
    if fn is None:
        return None
    try:
        sig = inspect.signature(fn)
        params = sig.parameters
        accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
        accepts_args = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in params.values())

        if accepts_kwargs:
            return fn(*args, **kwargs)

        filtered_kwargs = {k: v for k, v in kwargs.items() if k in params}
        if not accepts_args:
            positional_params = [
                p for p in params.values()
                if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
            ]
            max_args = max(len(positional_params) - len(filtered_kwargs), 0)
            args = args[:max_args]
        return fn(*args, **filtered_kwargs)
    except Exception:
        raise


def _find_function(module: Any, names: list[str]) -> Any:
    if module is None:
        return None
    for name in names:
        fn = getattr(module, name, None)
        if callable(fn):
            return fn
    return None


def _sections_to_passages_like(structure: Any, doc_id: str) -> list[SimpleNamespace]:
    d = _obj_to_dict(structure)
    sections = d.get("sections", [])
    out = []
    for i, s in enumerate(sections or []):
        if not isinstance(s, dict):
            sd = _obj_to_dict(s)
        else:
            sd = s
        text = sd.get("content") or sd.get("text") or ""
        title = sd.get("title") or sd.get("section_title") or ""
        role = sd.get("role") or sd.get("section_role") or "unknown"
        section_id = sd.get("section_id") or f"{doc_id}_S{i:04d}"
        if text:
            out.append(SimpleNamespace(
                text=text, passage_id=section_id, section_title=title,
                section_role=role, source_chunk_index=sd.get("source_chunk_index"),
                part_index=0, char_start=0, char_end=len(text),
                source_type=sd.get("source_type", "text"), metadata=sd,
            ))
    return out


def _get_evidence_mappings(evidence_map: Any) -> list:
    if isinstance(evidence_map, dict):
        return evidence_map.get("mappings", []) or []
    if isinstance(evidence_map, list):
        return evidence_map
    return getattr(evidence_map, "mappings", []) or []


def _evidence_dynamic_report(evidence_map: Any, name: str) -> dict:
    if evidence_map is None:
        return {}
    if isinstance(evidence_map, dict):
        return evidence_map.get(name, {}) or {}
    val = getattr(evidence_map, name, None)
    if isinstance(val, dict):
        return val
    return {}


def _evidence_map_to_object(evidence_map: Any) -> Any:
    if evidence_map is None:
        return SimpleNamespace(mappings=[], errors=[], llm_calls=0, backend="unknown", model="", processing_time=0.0)
    if hasattr(evidence_map, "mappings") and not isinstance(evidence_map, dict):
        return evidence_map
    if not isinstance(evidence_map, dict):
        return evidence_map

    mappings_obj = []
    for m in evidence_map.get("mappings", []) or []:
        if not isinstance(m, dict):
            mappings_obj.append(m)
            continue
        evs_obj = []
        for ev in m.get("evidences", []) or []:
            if isinstance(ev, dict):
                evs_obj.append(SimpleNamespace(
                    role=ev.get("role", ""),
                    phrase_source=ev.get("phrase_source") or ev.get("phrase") or ev.get("text") or "",
                    passage_id=ev.get("passage_id") or m.get("passage_id", ""),
                    section_role=ev.get("section_role") or m.get("section_role", "unknown"),
                    confidence=ev.get("confidence", 0.7),
                    validated=ev.get("validated", True),
                    role_original=ev.get("role_original", None),
                    role_postprocess_reason=ev.get("role_postprocess_reason", None),
                ))
            else:
                evs_obj.append(ev)
        mappings_obj.append(SimpleNamespace(
            passage_id=m.get("passage_id", ""),
            roles_cir=m.get("roles_cir", []),
            evidences=evs_obj,
            concepts=m.get("concepts", []) or [],
            structured_entities=m.get("structured_entities", {}) or {},
            error=m.get("error"),
        ))

    stats = evidence_map.get("stats", {}) or {}
    return SimpleNamespace(
        mappings=mappings_obj,
        errors=evidence_map.get("errors", []) or [],
        llm_calls=stats.get("llm_calls", 0),
        backend=stats.get("backend", "unknown"),
        model=stats.get("model", ""),
        processing_time=stats.get("processing_time", 0.0),
        role_postprocess_stats=evidence_map.get("role_postprocess_stats", {}),
        validation_report=evidence_map.get("validation_report", {}),
    )


def _obj_to_dict_with_dynamic(obj: Any) -> dict:
    d = _obj_to_dict(obj)
    if not isinstance(d, dict):
        d = {}
    for name in ("role_postprocess_stats", "validation_report"):
        val = _evidence_dynamic_report(obj, name)
        if val and name not in d:
            d[name] = val
    return d


def _get_sections(structure: Any) -> list:
    if isinstance(structure, dict):
        return structure.get("sections", []) or []
    if isinstance(structure, list):
        return structure
    return getattr(structure, "sections", []) or []


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
    return _dedupe(out, max_items)


def _domain_to_metadata(domain_result: Any) -> tuple[str, dict[str, int], dict]:
    d = _obj_to_dict(domain_result)
    if not d:
        return "non_classifié", {}, {}
    label = (
        d.get("domaine_principal") or d.get("domain_label") or d.get("label")
        or d.get("niv3_label") or d.get("label_niv3") or d.get("niv2_label")
        or d.get("label_niv2") or d.get("niv1_label") or d.get("label_niv1")
        or "non_classifié"
    )
    conf = float(d.get("confidence") or d.get("score") or 0.0)
    scores = {str(label): int(conf * 100)} if label and label != "non_classifié" else {}
    return str(label), scores, d



def _is_clean_keyword_for_router(text: str) -> bool:
    t = str(text or "").strip()
    if not t or len(t) > 140: return False
    if re.search(r"\b(de|du|des|d['’]?|l['’]?|pour|avec|dans|par|sur)$", t, re.I): return False
    if re.search(r"ces résultats|a permis le développement d|problématiques d$|haute technologie l$", t, re.I): return False
    return True


def _synthesis_to_fiche_and_keywords(synthesis: Any, aggregated: Any, technical_terms: Any = None) -> tuple[dict, list[str], list[str]]:
    d = _obj_to_dict(synthesis)
    fiche = d.get("fiche_cir") if isinstance(d.get("fiche_cir"), dict) else d.get("fiche") if isinstance(d.get("fiche"), dict) else {}
    tt = _obj_to_dict(technical_terms)
    mc = tt.get("mots_cles_projet", {}) if isinstance(tt.get("mots_cles_projet"), dict) else {}
    tech_high = mc.get("high_confidence", []) or []
    tech_cand = mc.get("candidates", []) or []
    keywords = _dedupe([str(x) for x in (tech_high + tech_cand) if _is_clean_keyword_for_router(str(x))])
    if not keywords:
        synth_kw = d.get("mots_cles") or d.get("keywords") or d.get("concepts_techniques") or []
        if isinstance(synth_kw, dict):
            tmp=[]
            for values in synth_kw.values():
                if isinstance(values, list): tmp.extend([str(x) for x in values])
                elif values: tmp.append(str(values))
            synth_kw=tmp
        if isinstance(synth_kw, list):
            keywords = _dedupe([str(x) for x in synth_kw if _is_clean_keyword_for_router(str(x))])
    if not keywords:
        keywords = [x for x in _concepts_from_aggregation(aggregated) if _is_clean_keyword_for_router(x)]
    lacunes = d.get("lacunes") or d.get("gaps") or []
    if not isinstance(lacunes, list): lacunes = []
    return fiche, keywords, _dedupe([str(x) for x in lacunes])


def _build_empty_metadata(doc_id, file_category_str, extraction_result, config):
    org_name, org_id, file_hash, document_id = _resolve_identity(extraction_result, doc_id, config)
    return DocumentMetadata(
        file_name=doc_id, file_category=file_category_str,
        source_tag=_source_tag_from_extraction(extraction_result),
        title=getattr(extraction_result, "title", None) if extraction_result else None,
        author=getattr(extraction_result, "author", None) if extraction_result else None,
        page_count=getattr(extraction_result, "page_count", 0) if extraction_result else 0,
        organisme_name=org_name, organisme_id=org_id,
        file_hash=file_hash, document_id=document_id,
        organismes=[org_name],
    )


def _apply_final_taxonomy(meta: DocumentMetadata, final_taxonomy: Any, domain_result: Any = None) -> None:
    d = _obj_to_dict(final_taxonomy)
    if not d:
        return

    list_fields = [
        "technologies", "verrous_techniques", "axes_projet", "objet_recherche",
        "sous_domaines", "hypotheses_rd", "protocoles_experimentaux", "outils_technologies",
        "modeles_algorithmes", "architectures_systeme", "jeux_donnees_benchmarks",
        "metriques_evaluation", "parametres_variables", "normes_techniques",
        "materiaux_composants", "limitations_perspectives", "objectifs_rd",
        "resultats_rd", "methodes_rd", "composants_techniques", "livrables",
        "depenses_eligibles", "brevets", "partenaires_rd", "personnes",
        "organismes", "materiaux", "equipements", "lieux", "dates_periodes", "montants",
        # NOUVEAU V8.1
        "etat_art",
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

    # ── NOUVEAU V8.1 : mapper domaine_applicatif + domaine_scientifique_detaille ──
    # Depuis final_taxonomy (calculé par final_taxonomy_mapper V7.2.0)
    applicatif = d.get("domaine_applicatif")
    if applicatif and not meta.domaine_applicatif:
        meta.domaine_applicatif = str(applicatif)

    detaille = d.get("domaine_scientifique_detaille")
    if detaille and not meta.domaine_scientifique_detaille:
        meta.domaine_scientifique_detaille = str(detaille)

    # Depuis domain_result (calculé par domain_classifier V7.2.0) — priorité
    if domain_result is not None:
        dr = _obj_to_dict(domain_result)
        if dr.get("domaine_applicatif") and not meta.domaine_applicatif:
            meta.domaine_applicatif = str(dr["domaine_applicatif"])
        if dr.get("domaine_scientifique_detaille") and not meta.domaine_scientifique_detaille:
            meta.domaine_scientifique_detaille = str(dr["domaine_scientifique_detaille"])
    # ─────────────────────────────────────────────────────────────────────────


def _apply_technical_terms(meta: DocumentMetadata, technical_terms: Any) -> None:
    d = _obj_to_dict(technical_terms)
    if not d:
        return
    meta.technical_terms = d
    mc = d.get("mots_cles_projet")
    if isinstance(mc, dict):
        high = _dedupe([str(v) for v in mc.get("high_confidence", []) if str(v or "").strip()])
        cand = _dedupe([str(v) for v in mc.get("candidates", []) if str(v or "").strip()])
        if high or cand:
            meta.mots_cles_projet = {
                "high_confidence": high or meta.mots_cles_projet.get("high_confidence", []),
                "candidates": cand or meta.mots_cles_projet.get("candidates", []),
            }
    for src_key, target_field in [
        ("technologies", "technologies"),
        ("materiaux_composants", "materiaux_composants"),
        ("equipements", "equipements"),
        ("methodes", "outils_technologies"),
        ("metriques", "metriques_evaluation"),
        ("normes", "normes_techniques"),
    ]:
        vals = d.get(src_key)
        if isinstance(vals, list) and vals:
            current = getattr(meta, target_field, [])
            setattr(meta, target_field, _dedupe(current + [str(v) for v in vals if str(v or "").strip()]))

    # ── NOUVEAU V8.1 : organismes propres depuis technical_terms ─────────────
    org_detected = d.get("organismes_detectes", [])
    if isinstance(org_detected, list) and org_detected:
        current = list(meta.organismes or [])
        meta.organismes = _dedupe(org_detected + current)
        if not meta.partenaires_rd:
            meta.partenaires_rd = _dedupe(org_detected)
    # ─────────────────────────────────────────────────────────────────────────


def _build_metadata(
    doc_id, file_category_str, extraction_result, config,
    document_structure, evidence_result, aggregated, domain_result,
    synthesis, final_taxonomy=None, technical_terms=None, quality_report=None,
) -> DocumentMetadata:
    meta = _build_empty_metadata(doc_id, file_category_str, extraction_result, config)

    domaine, scores, detail = _domain_to_metadata(domain_result)
    meta.domaine_principal = domaine
    meta.domaines_scores = scores
    meta.domaine_detail = detail

    meta.document_structure = _obj_to_dict(document_structure)
    meta.evidence_map = _obj_to_dict_with_dynamic(evidence_result)
    meta.aggregated_evidence = _obj_to_dict(aggregated)
    meta.validation_report = meta.evidence_map.get("validation_report", {}) or _evidence_dynamic_report(evidence_result, "validation_report")
    meta.role_postprocess_stats = meta.evidence_map.get("role_postprocess_stats", {}) or _evidence_dynamic_report(evidence_result, "role_postprocess_stats")

    # V7.4.1 : la fiche_cir/synthèse LLM n'est plus source de vérité.
    # On garde seulement les mots-clés/lacunes éventuels, puis final_taxonomy + final_output_guard
    # construisent les champs plats evidence-first.
    _fiche_ignored, keywords, lacunes = _synthesis_to_fiche_and_keywords(synthesis, aggregated, technical_terms)
    meta.fiche_cir = {}
    meta.lacunes = lacunes

    meta.mots_cles_projet = {"high_confidence": keywords[:12], "candidates": keywords[12:30]}
    meta.technologies = list(meta.mots_cles_projet["high_confidence"])
    meta.sous_domaines = _concepts_from_aggregation(aggregated, 10)

    # Ne plus remplir les champs finaux depuis fiche_cir.
    meta.objet_recherche = []
    meta.objectifs_rd = []
    meta.verrous_techniques = []
    meta.methodes_rd = []
    meta.resultats_rd = []
    meta.limitations_perspectives = []

    # MODIFIÉ V8.1 : passer domain_result pour mapper domaine_applicatif
    _apply_final_taxonomy(meta, final_taxonomy, domain_result=domain_result)
    _apply_technical_terms(meta, technical_terms)

    qd = _obj_to_dict(quality_report)
    if qd:
        meta.quality_report = qd

    return meta


# ══════════════════════════════════════════════════════════════════════════════
# ADAPTERS V7 (identiques à V7.1.2)
# ══════════════════════════════════════════════════════════════════════════════

def _build_document_structure(normalized_text, doc_id, doc_type, config):
    document_text = "\n\n".join([str(x or "") for x in normalized_text if str(x or "").strip()])

    if config.use_document_structure_mapper and DOCUMENT_STRUCTURE_MAPPER_AVAILABLE and map_document_structure is not None:
        candidates = [
            lambda: _safe_call(map_document_structure, document_text=document_text, text=document_text, chunks=normalized_text, doc_id=doc_id, doc_type=doc_type),
            lambda: map_document_structure(document_text, doc_id=doc_id, doc_type=doc_type),
            lambda: map_document_structure(document_text, doc_id),
            lambda: map_document_structure(document_text),
            lambda: map_document_structure(normalized_text),
        ]
        for call in candidates:
            try:
                result = call()
                if result is not None:
                    return result
            except Exception as exc:
                logger.debug("document_structure_mapper tentative échouée : %s", exc)
        logger.warning("document_structure_mapper a échoué, fallback segmenter.")

    sections = []
    if SEGMENTER_AVAILABLE and segment_chunks is not None:
        try:
            passages = segment_chunks(normalized_text, doc_id=doc_id, max_chars=config.max_passage_chars, overlap_chars=config.passage_overlap_chars, keep_small=config.keep_small_passages)
            for i, p in enumerate(passages):
                sections.append({
                    "section_id": getattr(p, "passage_id", f"{doc_id}_S{i:04d}"),
                    "title": getattr(p, "section_title", ""), "role": getattr(p, "section_role", "unknown"),
                    "section_role": getattr(p, "section_role", "unknown"), "content": getattr(p, "text", ""),
                    "source_chunk_index": getattr(p, "source_chunk_index", None), "source_type": getattr(p, "source_type", "text"),
                })
        except Exception as exc:
            logger.warning("Fallback segmenter vers sections échoué : %s", exc)

    if not sections and document_text:
        sections = [{"section_id": f"{doc_id}_S0000", "title": "", "role": "unknown", "section_role": "unknown", "content": document_text, "source_chunk_index": None, "source_type": "text"}]

    return {"document_id": doc_id, "sections": sections, "stats": {"sections": len(sections), "fallback": True}}


def _extract_evidence_from_sections(document_structure, doc_id, config):
    if config.use_section_extractor and SECTION_EXTRACTOR_AVAILABLE and section_extractor_module is not None:
        fn = _find_function(section_extractor_module, ["extract_from_sections", "extract_cir_from_sections", "extract_sections", "section_extract", "extract_evidence_from_sections", "extract_evidence"])
        if fn is not None:
            candidates = [
                lambda: _safe_call(fn, document_structure=document_structure, structure=document_structure, sections=_get_sections(document_structure), doc_id=doc_id, model=config.llm_extractor_model, llm_model=config.llm_extractor_model, enabled=config.use_llm_extractor),
                lambda: fn(document_structure, model=config.llm_extractor_model, enabled=config.use_llm_extractor),
                lambda: fn(_get_sections(document_structure), model=config.llm_extractor_model, enabled=config.use_llm_extractor),
                lambda: fn(_get_sections(document_structure)),
            ]
            for call in candidates:
                try:
                    result = call()
                    if result is not None:
                        logger.info("section_extractor utilisé avec succès.")
                        return result
                except Exception as exc:
                    logger.debug("section_extractor tentative échouée : %s", exc)
            logger.warning("section_extractor importé mais toutes les signatures ont échoué.")

    if config.use_evidence_mapper and EVIDENCE_MAPPER_AVAILABLE and map_evidence is not None:
        passages = _sections_to_passages_like(document_structure, doc_id)
        if passages:
            logger.warning("Fallback evidence_mapper sur sections (%d passages).", len(passages))
            return map_evidence(passages=passages, model=config.llm_extractor_model, enabled=config.use_llm_extractor, max_llm_passages=config.max_llm_passages)

    logger.warning("Aucune extraction de preuves disponible.")
    return {"mappings": [], "stats": {"errors": 1, "message": "no_extractor_available"}}


def _postprocess_and_validate_evidence(evidence_map, document_structure, config):
    result = evidence_map
    if config.use_role_postprocessor and ROLE_POSTPROCESSOR_AVAILABLE and postprocess_evidence_roles is not None:
        try:
            result = postprocess_evidence_roles(result, sections=document_structure, strict=True, add_debug=config.include_debug)
        except Exception as exc:
            logger.warning("role_postprocessor erreur : %s", exc)
    if config.use_evidence_validator and EVIDENCE_VALIDATOR_AVAILABLE and validate_evidence is not None:
        try:
            result = validate_evidence(result, sections=document_structure, strict=True)
        except Exception as exc:
            logger.warning("evidence_validator erreur : %s", exc)
    return result


def _extract_technical_terms_wrapper(document_structure, evidence_map, config):
    if not (config.use_technical_terms_extractor and TECHNICAL_TERMS_EXTRACTOR_AVAILABLE and extract_technical_terms is not None):
        return {}
    try:
        return _safe_call(extract_technical_terms, sections=document_structure, evidence_map=evidence_map, use_gliner=config.use_gliner_ner, gliner_model=config.gliner_model)
    except Exception as exc:
        logger.warning("technical_terms_extractor erreur : %s", exc)
        return {}


def _build_quality_report_wrapper(document_structure, evidence_map, final_taxonomy, meta_preview, config):
    if not (config.use_quality_reporter and QUALITY_REPORTER_AVAILABLE and build_quality_report is not None):
        return {}
    sections = _get_sections(document_structure)
    attempts = [
        lambda: _safe_call(build_quality_report, sections=sections, evidence_map=evidence_map, evidence=evidence_map, final_taxonomy=final_taxonomy, metadata=meta_preview, meta=meta_preview),
        lambda: build_quality_report(sections, final_taxonomy, evidence_map),
        lambda: build_quality_report(sections, final_taxonomy),
        lambda: build_quality_report(meta_preview),
    ]
    last_error = None
    for attempt in attempts:
        try:
            result = attempt()
            if result is not None:
                return result
        except Exception as exc:
            last_error = exc
    logger.warning("quality_reporter erreur : %s", last_error)
    return {}


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
        doc_type=doc_type, file_category_str=file_category_str,
        extraction_result=extraction_result, config=cfg,
    )


def process_document(text_chunks, visual_chunks=None, doc_id="doc", config=None, file_category="unknown") -> NLPResult:
    return _run_pipeline(
        text_chunks=text_chunks, visual_chunks=visual_chunks or [],
        doc_id=doc_id, doc_type=_get_doc_type(file_category),
        file_category_str=file_category, extraction_result=None, config=config or NLPConfig(),
    )


def _run_pipeline(text_chunks, visual_chunks, doc_id, doc_type, file_category_str, extraction_result, config) -> NLPResult:
    timings: dict[str, float] = {}
    t_global = time.time()

    if not text_chunks and not visual_chunks:
        meta = _build_empty_metadata(doc_id, file_category_str, extraction_result, config)
        return NLPResult(meta, [], debug={"warning": "Aucun chunk en entrée."} if config.include_debug else None)

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

    t0 = time.time()
    norm_text_result = normalize_chunks(cleaned_text)
    norm_visual_result = normalize_chunks(cleaned_visual)
    normalized_text = norm_text_result.normalized_chunks
    normalized_visual = norm_visual_result.normalized_chunks
    normalized = normalized_text + normalized_visual
    n_text = len(normalized_text)
    timings["normalizer"] = round(time.time() - t0, 3)

    t0 = time.time()
    base_for_structure = normalized_text if config.terminology_text_only else normalized
    document_structure = _build_document_structure(base_for_structure, doc_id, doc_type, config)
    timings["document_structure_mapper"] = round(time.time() - t0, 3)

    t0 = time.time()
    passages = []
    segmenter_summary = {}
    if SEGMENTER_AVAILABLE and segment_chunks is not None:
        try:
            passages = segment_chunks(base_for_structure, doc_id=doc_id, max_chars=config.max_passage_chars, overlap_chars=config.passage_overlap_chars, keep_small=config.keep_small_passages)
            segmenter_summary = summarize_passages(passages) if summarize_passages else {}
        except Exception as exc:
            logger.warning("segmenter debug échoué : %s", exc)
    timings["segmenter_debug"] = round(time.time() - t0, 3)

    t0 = time.time()
    evidence_result = _extract_evidence_from_sections(document_structure, doc_id, config)
    timings["section_extractor"] = round(time.time() - t0, 3)

    t0 = time.time()
    evidence_result = _postprocess_and_validate_evidence(evidence_result, document_structure, config)
    timings["role_postprocessor_validator"] = round(time.time() - t0, 3)

    t0 = time.time()
    technical_terms = _extract_technical_terms_wrapper(document_structure, evidence_result, config)
    timings["technical_terms_extractor"] = round(time.time() - t0, 3)

    t0 = time.time()
    aggregated = None
    evidence_for_aggregation = _evidence_map_to_object(evidence_result)
    if evidence_for_aggregation is not None and AGGREGATOR_AVAILABLE and aggregate is not None:
        aggregated = aggregate(evidence_for_aggregation)
    timings["aggregator"] = round(time.time() - t0, 3)

    t0 = time.time()
    domain_result = None
    if aggregated is not None and config.use_domain_classifier and DOMAIN_CLASSIFIER_AVAILABLE and classify_domain is not None:
        domain_result = _safe_call(classify_domain, aggregated=aggregated, model=config.llm_extractor_model, enabled=True, domains_path=config.domains_path)
    timings["domain_classifier"] = round(time.time() - t0, 3)

    t0 = time.time()
    synthesis = None
    if aggregated is not None and config.use_synthesizer and config.use_llm_extractor and SYNTHESIZER_AVAILABLE and synthesize is not None:
        synthesis = _safe_call(synthesize, aggregated=aggregated, domain_classification=domain_result, model=config.llm_extractor_model, enabled=True)
    timings["synthesizer"] = round(time.time() - t0, 3)

    t0 = time.time()
    final_taxonomy = None
    if aggregated is not None and config.use_final_taxonomy_mapper and FINAL_TAXONOMY_MAPPER_AVAILABLE and map_final_taxonomy is not None:
        final_taxonomy = _safe_call(
            map_final_taxonomy,
            aggregated=aggregated, synthesis=synthesis,
            domain_classification=domain_result,
            evidence_map=evidence_for_aggregation,
            raw_chunks=list(normalized_text),
            technical_terms=technical_terms,
            document_structure=document_structure,
        )
    timings["final_taxonomy_mapper"] = round(time.time() - t0, 3)

    doc_meta = _build_metadata(
        doc_id=doc_id, file_category_str=file_category_str,
        extraction_result=extraction_result, config=config,
        document_structure=document_structure, evidence_result=evidence_result,
        aggregated=aggregated, domain_result=domain_result,
        synthesis=synthesis, final_taxonomy=final_taxonomy,
        technical_terms=technical_terms, quality_report=None,
    )

    # V7.4.1 : garde-fou final, supprime fiche_cir et impose les champs plats propres.
    if FINAL_OUTPUT_GUARD_AVAILABLE and apply_final_output_guard is not None:
        try:
            doc_meta = apply_final_output_guard(doc_meta, remove_fiche_cir=True)
        except Exception as exc:
            logger.warning("final_output_guard erreur : %s", exc)

    t0 = time.time()
    quality_report = _build_quality_report_wrapper(document_structure, evidence_result, final_taxonomy, doc_meta, config)
    timings["quality_reporter"] = round(time.time() - t0, 3)
    doc_meta.quality_report = _obj_to_dict(quality_report)

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
        enriched_chunks.append(EnrichedChunk(
            chunk_id=f"{doc_id}_chunk_{i:04d}", index=i, source=source, content=content,
            metadata={
                "file_name": doc_id, "organisme_name": doc_meta.organisme_name,
                "organisme_id": doc_meta.organisme_id, "file_hash": doc_meta.file_hash,
                "document_id": doc_meta.document_id, "file_category": file_category_str,
                "doc_type": doc_type, "source_type": source,
                "chunk_source_type": _chunk_source_type(content, doc_type),
                "domaine_principal": doc_meta.domaine_principal,
                # NOUVEAU V8.1
                "domaine_applicatif": doc_meta.domaine_applicatif,
                "domaine_scientifique_detaille": doc_meta.domaine_scientifique_detaille,
                "mots_cles_high_confidence": doc_meta.mots_cles_projet.get("high_confidence", []),
                "mots_cles_candidates": doc_meta.mots_cles_projet.get("candidates", []),
                "objet_recherche": doc_meta.objet_recherche,
                "objectifs_rd": doc_meta.objectifs_rd,
                "verrous_techniques": doc_meta.verrous_techniques,
                "methodes_rd": doc_meta.methodes_rd,
                "resultats_rd": doc_meta.resultats_rd,
                "etat_art": doc_meta.etat_art,
                "protocoles_experimentaux": doc_meta.protocoles_experimentaux,
                "normes_techniques": doc_meta.normes_techniques,
                "materiaux_composants": doc_meta.materiaux_composants,
                "limitations_perspectives": doc_meta.limitations_perspectives,
                "brevets": doc_meta.brevets, "lacunes": doc_meta.lacunes,
                "passages": passage_by_source_chunk.get(i, []), "entities": [],
            },
        ))

    timings["total"] = round(time.time() - t_global, 3)

    debug = None
    if config.include_debug:
        debug = {
            "pipeline": "nlp_v7_2_section_first_role_postprocessor_v8_enrichments",
            "timings": timings,
            "doc_type": doc_type, "file_category": file_category_str,
            "modules_available": {
                "document_structure_mapper": DOCUMENT_STRUCTURE_MAPPER_AVAILABLE,
                "section_extractor": SECTION_EXTRACTOR_AVAILABLE,
                "role_postprocessor": ROLE_POSTPROCESSOR_AVAILABLE,
                "evidence_validator": EVIDENCE_VALIDATOR_AVAILABLE,
                "technical_terms_extractor": TECHNICAL_TERMS_EXTRACTOR_AVAILABLE,
                "aggregator": AGGREGATOR_AVAILABLE,
                "domain_classifier": DOMAIN_CLASSIFIER_AVAILABLE,
                "synthesizer": SYNTHESIZER_AVAILABLE,
                "final_taxonomy_mapper": FINAL_TAXONOMY_MAPPER_AVAILABLE,
                "final_output_guard": FINAL_OUTPUT_GUARD_AVAILABLE,
                "quality_reporter": QUALITY_REPORTER_AVAILABLE,
                "evidence_mapper_fallback": EVIDENCE_MAPPER_AVAILABLE,
            },
            "document_structure": _obj_to_dict(document_structure).get("stats", {}),
            "segmenter_debug": segmenter_summary,
            "evidence_map_stats": _obj_to_dict_with_dynamic(evidence_result).get("stats", {}),
            "role_postprocess_stats": _obj_to_dict_with_dynamic(evidence_result).get("role_postprocess_stats", {}),
            "validation_report": _obj_to_dict_with_dynamic(evidence_result).get("validation_report", {}),
            "technical_terms": _obj_to_dict(technical_terms).get("stats", {}),
            "aggregator": _obj_to_dict(aggregated).get("stats", {}),
            "domain_classifier": _obj_to_dict(domain_result),
            "synthesizer": _obj_to_dict(synthesis).get("stats", {}),
            "final_taxonomy_mapper": _obj_to_dict(final_taxonomy).get("stats", {}),
            "quality_report": doc_meta.quality_report,
            "metadata_fusion": {
                "domaine_principal": doc_meta.domaine_principal,
                "domaine_applicatif": doc_meta.domaine_applicatif,
                "domaine_scientifique_detaille": doc_meta.domaine_scientifique_detaille,
                "mots_cles_projet": doc_meta.mots_cles_projet,
                "objet_recherche": doc_meta.objet_recherche,
                "objectifs_rd": doc_meta.objectifs_rd,
                "verrous_techniques": doc_meta.verrous_techniques,
                "methodes_rd": doc_meta.methodes_rd,
                "resultats_rd": doc_meta.resultats_rd,
                "etat_art": doc_meta.etat_art,
                "partenaires_rd": doc_meta.partenaires_rd,
                "organismes": doc_meta.organismes,
            },
        }

    logger.info(
        "NLP V7.4.1 terminé : sections=%d preuves=%d domaine=%s applicatif=%s total=%.2fs",
        len(_get_sections(document_structure)), len(_get_evidence_mappings(evidence_result)),
        doc_meta.domaine_principal, doc_meta.domaine_applicatif, timings["total"],
    )
    return NLPResult(document_metadata=doc_meta, chunks=enriched_chunks, debug=debug)


# ══════════════════════════════════════════════════════════════════════════════
# JSON EXPORT — NOUVEAU V8.1 : champs enrichis exposés
# ══════════════════════════════════════════════════════════════════════════════

def to_json(result: NLPResult) -> dict:
    meta = result.document_metadata
    return {
        "pipeline": {
            "name": "extraction → cleaner → normalizer → document_structure_mapper → section_extractor → role_postprocessor → evidence_validator → technical_terms_extractor → aggregator → domain_classifier → synthesizer → final_taxonomy_mapper → quality_reporter",
            "version": "7.4.1-no-fiche-cir-final-guard",
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
            # NOUVEAU V8.1
            "domaine_applicatif": meta.domaine_applicatif,
            "domaine_scientifique_detaille": meta.domaine_scientifique_detaille,

            "document_structure": meta.document_structure,
            "evidence_map": meta.evidence_map,
            "aggregated_evidence": meta.aggregated_evidence,
            "technical_terms": meta.technical_terms,
            "quality_report": meta.quality_report,
            "validation_report": meta.validation_report,
            "role_postprocess_stats": meta.role_postprocess_stats,
            "lacunes": meta.lacunes,

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
            # NOUVEAU V8.1
            "etat_art": meta.etat_art,
        },
        "chunks": [
            {"chunk_id": c.chunk_id, "index": c.index, "source": c.source, "content": c.content, "metadata": c.metadata}
            for c in result.chunks
        ],
        "debug": result.debug,
    }