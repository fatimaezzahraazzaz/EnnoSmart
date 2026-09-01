# -*- coding: utf-8 -*-
from __future__ import annotations

"""
scholar_state_of_art_payload_service.py

PHASE 1 — Construction du payload propre pour la rédaction de l'état de l'art CIR.

Version V1.3 : contexte projet structuré + conservation de tous les articles gardés par défaut.

Objectif :
- ne pas appeler de LLM ;
- ne pas rédiger encore l'état de l'art ;
- récupérer uniquement les articles gardés par le consultant ;
- grouper les articles par verrou scientifique ;
- séparer Direct / Connexe / Fondamental ;
- exclure Hors sujet et sources techniques de la rédaction ;
- préparer un payload propre pour la Phase 2 : fiches articles + rédaction CIR.

Règle CIR importante :
- Articles sélectionnés = preuves scientifiques ;
- Dossier courant = contexte projet ;
- Mémoire V2 = style uniquement, pas preuve scientifique.
"""

import json
import os
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path

from modules.common.runtime_paths import storage_root
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from db.models import Article, DiagnosticRun, Project, ScholarRun, Verrou
from services.scholar_selection_scope import (
    get_current_scholar_run,
    get_current_selected_articles,
)


# ============================================================
# Helpers génériques
# ============================================================

def _safe_text(value: Any, max_chars: int = 2000) -> str:
    if value is None:
        return ""

    text = str(value).replace("\x00", " ").strip()
    text = re.sub(r"\s+", " ", text)

    if max_chars and len(text) > max_chars:
        return text[:max_chars].strip()

    return text.strip()


def _strip_accents(value: Any) -> str:
    text = str(value or "")
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _norm(value: Any) -> str:
    text = _strip_accents(value).lower()
    text = text.replace("’", "'").replace("`", "'")
    text = re.sub(r"[^a-z0-9+\-/.% ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _json_safe(value: Any) -> Any:
    """
    Convertit les objets non sérialisables en JSON propre.
    """
    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}

    if isinstance(value, list):
        return [_json_safe(v) for v in value]

    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]

    return value


def _dedupe_keep_order(values: List[Any], max_items: int = 50) -> List[Any]:
    out: List[Any] = []
    seen = set()

    for value in values or []:
        if value is None:
            continue

        if isinstance(value, dict):
            key = _norm(
                value.get("id")
                or value.get("title")
                or value.get("text")
                or json.dumps(value, ensure_ascii=False, sort_keys=True)
            )
        else:
            key = _norm(value)

        if not key or key in seen:
            continue

        seen.add(key)
        out.append(value)

        if len(out) >= max_items:
            break

    return out


def _flatten_text(value: Any, max_chars: int = 3500) -> str:
    """
    Aplatit un JSON technique en texte court.
    """
    if value is None:
        return ""

    if isinstance(value, str):
        return _safe_text(value, max_chars)

    if isinstance(value, dict):
        parts: List[str] = []
        for key, item in value.items():
            txt = _flatten_text(item, max_chars=max_chars)
            if txt:
                parts.append(f"{key}: {txt}")
            if len(" ".join(parts)) >= max_chars:
                break
        return _safe_text("\n".join(parts), max_chars)

    if isinstance(value, list):
        parts = [_flatten_text(item, max_chars=max_chars) for item in value[:30]]
        return _safe_text("\n".join([p for p in parts if p]), max_chars)

    return _safe_text(value, max_chars)


def _score_float(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except Exception:
        return 0.0


def _year_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        text = str(value)
        if len(text) >= 4 and text[:4].isdigit():
            return int(text[:4])
        return int(value)
    except Exception:
        return None


# ============================================================
# Sauvegarde payload Phase 1
# ============================================================

def _slugify_path_segment(value: Any, default: str = "unknown") -> str:
    """
    Même logique de chemin que les autres services EnnoScholar :
    <organisme> / <projet> / <année>
    -> storage/organismes/<organisme>/projects/<projet>/years/<année>
    """
    text = _strip_accents(str(value or "").lower())
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or default


def _project_ennoscholar_dir(project: Project) -> Path:
    """
    Dossier racine EnnoScholar du projet.

    Par défaut, le stockage est résolu depuis la racine du dépôt.
    """
    root = storage_root()

    organisme = _slugify_path_segment(getattr(project, "organisme", None), "organisme")
    project_name = _slugify_path_segment(getattr(project, "project_name", None), "project")
    year = _slugify_path_segment(getattr(project, "year", None), "year")

    return (
        root
        / "organismes"
        / organisme
        / "projects"
        / project_name
        / "years"
        / year
        / "ennoscholar"
    )


def state_of_art_payload_dir(project: Project) -> Path:
    return _project_ennoscholar_dir(project) / "state_of_art_payload"


def state_of_art_selection_payload_path(project: Project) -> Path:
    return state_of_art_payload_dir(project) / "selection_payload.json"


def save_state_of_art_selection_payload(project: Project, payload: Dict[str, Any]) -> Path:
    """
    Sauvegarde officielle du payload Phase 1.
    Cette fonction est appelée automatiquement par build_state_of_art_selection_payload().
    """
    path = state_of_art_selection_payload_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_json_safe(payload), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return path



# ============================================================
# Tags articles
# ============================================================

def normalize_article_tag(tag: Any) -> str:
    value = _safe_text(tag, 120)

    if not value:
        return "Non classé"

    n = _norm(value)

    if "direct" in n:
        return "Direct"

    if "connexe" in n or "related" in n:
        return "Connexe"

    if "fondamental" in n or "fundamental" in n or "background" in n:
        return "Fondamental"

    if "hors" in n or "irrelevant" in n or "off topic" in n:
        return "Hors sujet"

    if "technique" in n or "technical" in n or "catalog" in n:
        return "Technique"

    return value


def is_technical_catalog_article(article: Article | Dict[str, Any]) -> bool:
    if isinstance(article, Article):
        sj = _as_dict(article.source_json)
        source = article.source or sj.get("source")
        tag = article.tag_article or sj.get("tag") or sj.get("tag_article")
        paper_id = sj.get("paper_id") or sj.get("id")
        source_type = sj.get("source_type")
        source_kind = sj.get("source_kind")
    else:
        sj = _as_dict(article.get("source_json"))
        source = article.get("source") or sj.get("source")
        tag = article.get("tag") or article.get("tag_article") or sj.get("tag") or sj.get("tag_article")
        paper_id = article.get("paper_id") or sj.get("paper_id") or sj.get("id")
        source_type = article.get("source_type") or sj.get("source_type")
        source_kind = article.get("source_kind") or sj.get("source_kind")

    source_n = _norm(source)
    tag_n = normalize_article_tag(tag)
    paper_n = _norm(paper_id)
    source_type_n = _norm(source_type)
    source_kind_n = _norm(source_kind)

    return (
        source_n in {"technical catalog", "technical_catalog"}
        or source_type_n in {"technical reference", "technical_reference"}
        or "source technique" in source_kind_n
        or paper_n.startswith("tech ")
        or paper_n.startswith("tech:")
        or tag_n == "Technique"
    )


def is_article_usable_for_state_of_art(article: Article) -> bool:
    tag = normalize_article_tag(article.tag_article)

    if article.consultant_status != "garde":
        return False

    if is_technical_catalog_article(article):
        return False

    return tag in {"Direct", "Connexe", "Fondamental"}


# ============================================================
# Extraction article
# ============================================================

def _article_summary(article: Article) -> Dict[str, Any]:
    sj = _as_dict(article.source_json)
    summary = sj.get("article_summary")

    return summary if isinstance(summary, dict) else {}


def extract_article_abstract_original(article: Article) -> str:
    sj = _as_dict(article.source_json)
    summary = _article_summary(article)

    candidates = [
        summary.get("abstract_original"),
        sj.get("abstract_original"),
        sj.get("abstract"),
        sj.get("summary"),
        summary.get("resume_court"),
    ]

    tldr = sj.get("tldr")
    if isinstance(tldr, dict):
        candidates.append(tldr.get("text"))
    elif isinstance(tldr, str):
        candidates.append(tldr)

    for value in candidates:
        text = _safe_text(value, 12000)
        if text:
            return text

    return ""


def extract_article_abstract_fr(article: Article) -> str:
    sj = _as_dict(article.source_json)
    summary = _article_summary(article)

    candidates = [
        summary.get("abstract_fr"),
        summary.get("resume_fr"),
        sj.get("abstract_fr"),
        sj.get("abstract_translated_fr"),
        sj.get("resume_fr"),
    ]

    for value in candidates:
        text = _safe_text(value, 12000)
        if text:
            return text

    return ""


def extract_article_authors(article: Article) -> List[str]:
    sj = _as_dict(article.source_json)

    authors = sj.get("authors")
    if not isinstance(authors, list):
        authors = []

    out: List[str] = []

    for author in authors:
        if isinstance(author, str):
            name = _safe_text(author, 160)
        elif isinstance(author, dict):
            name = _safe_text(author.get("name") or author.get("author_name"), 160)
        else:
            name = ""

        if name:
            out.append(name)

    return _dedupe_keep_order(out, max_items=12)


def extract_article_reason(article: Article) -> str:
    sj = _as_dict(article.source_json)
    validation = _as_dict(sj.get("verrou_scientific_validation"))

    candidates = [
        sj.get("reason"),
        sj.get("alignment_reason"),
        sj.get("justification"),
        sj.get("tag_consultant"),
        validation.get("gap_analysis"),
        validation.get("consultant_action"),
    ]

    for value in candidates:
        text = _safe_text(value, 1600)
        if text:
            return text

    return "Lien proposé par EnnoScholar ; à vérifier par le consultant."


def _article_stable_key(article: Article) -> str:
    sj = _as_dict(article.source_json)

    doi = _norm(article.doi or sj.get("doi"))
    if doi:
        return f"doi:{doi}"

    pid = _norm(sj.get("paper_id") or sj.get("paperId") or sj.get("id"))
    if pid:
        return f"paper:{pid}"

    return f"title:{_norm(article.title)[:180]}:{article.year or ''}"


def article_to_payload_item(article: Article, citation_id: str) -> Dict[str, Any]:
    """
    Phase 1 : transforme l'article DB en objet propre.

    Les champs apport/methode/resultat/limite sont préparés,
    mais la vraie extraction intelligente sera faite en Phase 2.
    """
    sj = _as_dict(article.source_json)
    summary = _article_summary(article)

    tag = normalize_article_tag(article.tag_article or sj.get("tag") or sj.get("tag_article"))
    abstract_original = extract_article_abstract_original(article)
    abstract_fr = extract_article_abstract_fr(article)

    reason = extract_article_reason(article)

    apport = _safe_text(summary.get("apport_scientifique"), 1200)
    limite = _safe_text(summary.get("limite_pour_le_projet"), 1200)

    if not apport:
        apport = (
            "À construire en Phase 2 à partir du titre, de l'abstract, "
            "du tag article et du contexte du verrou."
        )

    if not limite:
        limite = (
            "À construire en Phase 2 : préciser ce que l'article ne résout pas "
            "directement pour le contexte du projet CIR."
        )

    return {
        "citation_id": citation_id,
        "citation_token": f"[{citation_id}]",

        "article_id": article.id,
        "scholar_run_id": article.scholar_run_id,
        "verrou_id": article.verrou_id,

        "title": _safe_text(article.title, 500),
        "authors": extract_article_authors(article),
        "year": article.year,
        "source": _safe_text(article.source, 120),
        "doi": _safe_text(article.doi, 220),
        "url": _safe_text(article.url, 800),
        "score": article.score,
        "tag": tag,
        "consultant_status": article.consultant_status,

        "reason": reason,

        "abstract_original": abstract_original,
        "abstract_fr": abstract_fr,
        "abstract_for_writer": abstract_fr or abstract_original,

        # Pré-fiche article. Phase 2 remplacera ces champs par une vraie analyse.
        "fiche_article": {
            "label": f"[{citation_id}] {_safe_text(article.title, 180)}",
            "type": tag,
            "apport_scientifique": apport,
            "methode": _safe_text(summary.get("methode"), 1200)
            or "À extraire en Phase 2 depuis l'abstract fourni.",
            "resultat": _safe_text(summary.get("resultat"), 1200)
            or "À extraire en Phase 2 depuis l'abstract fourni. Si non disponible, indiquer : non précisé dans l'abstract fourni.",
            "limite_pour_notre_projet": limite,
            "citation_exploitable": (
                f"{_safe_text(article.title, 220)} ({article.year or 'année non précisée'})"
            ),
        },

        "source_basis": {
            "source_json_keys": sorted(list(sj.keys()))[:60],
            "has_abstract_original": bool(abstract_original),
            "has_abstract_fr": bool(abstract_fr),
            "has_article_summary": bool(summary),
            "stable_key": _article_stable_key(article),
        },
    }


# ============================================================
# Extraction verrou / contexte projet
# ============================================================

def _get_latest_scholar_run(db: Session, project: Project) -> Optional[ScholarRun]:
    return get_current_scholar_run(db, project)


def _get_project_verrous(db: Session, project: Project) -> Dict[int, Verrou]:
    rows = (
        db.query(Verrou)
        .join(DiagnosticRun, Verrou.diagnostic_run_id == DiagnosticRun.id)
        .filter(DiagnosticRun.project_id == project.id)
        .all()
    )

    return {v.id: v for v in rows}


def _get_latest_diagnostic_run(db: Session, project: Project) -> Optional[DiagnosticRun]:
    return (
        db.query(DiagnosticRun)
        .filter(DiagnosticRun.project_id == project.id)
        .order_by(DiagnosticRun.created_at.desc())
        .first()
    )



def _path_variants(value: Any) -> List[str]:
    """
    Variantes robustes pour retrouver les dossiers créés par les anciens modules :
    Casse et séparateurs différents selon les versions historiques.
    """
    raw = _safe_text(value, 180)
    if not raw:
        return []

    slug = _slugify_path(raw)
    no_space = re.sub(r"\s+", "_", raw.strip())
    variants = [
        raw,
        raw.replace("-", "_"),
        raw.replace("_", "-"),
        no_space,
        no_space.replace("-", "_"),
        no_space.replace("_", "-"),
        slug,
        slug.upper(),
        slug.lower(),
        raw.upper(),
        raw.lower(),
    ]

    return [x for x in _dedupe_keep_order([_safe_text(v, 180) for v in variants], 20) if x]


def _read_json_file(path: Path) -> Dict[str, Any]:
    try:
        if path.exists() and path.is_file():
            return _as_dict(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return {}
    return {}


def _candidate_ennodiagnostic_report_paths(project: Project) -> List[Path]:
    """
    Recherche robuste du rapport final EnnoDiagnostic.

    Le chemin reste générique : organisme/projet/année.
    """
    paths: List[Path] = []

    explicit = _safe_text(os.getenv("ENNOSMART_ENNODIAGNOSTIC_REPORT_PATH"), 1200)
    if explicit:
        paths.append(Path(explicit))

    root = storage_root()

    organismes = _path_variants(getattr(project, "organisme", None))
    projects = _path_variants(getattr(project, "project_name", None))
    years = _path_variants(getattr(project, "year", None))

    for org in organismes:
        for prj in projects:
            for year in years:
                base = root / "organismes" / org / "projects" / prj / "years" / year
                paths.extend([
                    base / "ennodiagnostic" / "ennodiagnostic_report.json",
                    base / "diagnostics" / "ennodiagnostic_report.json",
                    base / "diagnostics" / "diagnostic_ennodiagnostic.json",
                    base / "ennodiagnostic" / "diagnostic_ennodiagnostic.json",
                ])

    return list(dict.fromkeys(paths))


def _load_ennodiagnostic_report(project: Project) -> Dict[str, Any]:
    """
    Charge le rapport final EnnoDiagnostic depuis le stockage projet.
    Ce rapport devient la source prioritaire du contexte projet pour Phase 1.
    """
    checked: List[str] = []

    for path in _candidate_ennodiagnostic_report_paths(project):
        checked.append(str(path))
        data = _read_json_file(path)
        if data:
            return {
                "available": True,
                "path": str(path),
                "checked_paths": checked[:80],
                "report": data,
            }

    return {
        "available": False,
        "path": "",
        "checked_paths": checked[:80],
        "report": {},
    }


def _extract_frascati_text_from_report(report: Dict[str, Any]) -> str:
    fr = _as_dict(report.get("frascati_justification"))
    generation = _as_dict(fr.get("generation"))

    candidates = [
        fr.get("text"),
        generation.get("content"),
        _flatten_text(fr, max_chars=5000),
    ]

    for value in candidates:
        text = _safe_text(value, 7000)
        if text:
            return text

    return ""


def _iter_report_passages(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Récupère les passages sources EnnoDiagnostic lorsque disponibles.
    Dans ton JSON, ils sont dans ai_detection_report.ai_detection.passages.
    """
    out: List[Dict[str, Any]] = []

    # Cas principal de ton rapport actuel.
    ai_report = _as_dict(report.get("ai_detection_report"))
    ai_detection = _as_dict(ai_report.get("ai_detection"))
    passages = ai_detection.get("passages")
    if isinstance(passages, list):
        out.extend([p for p in passages if isinstance(p, dict)])

    # Fallbacks si un futur EnnoDiagnostic expose les sources autrement.
    for key in [
        "source_passages",
        "sources",
        "evidence",
        "evidences",
        "rag_passages",
        "passages",
    ]:
        value = report.get(key)
        if isinstance(value, list):
            out.extend([p for p in value if isinstance(p, dict)])

    deduped: List[Dict[str, Any]] = []
    seen = set()
    for p in out:
        text = _safe_text(p.get("text") or p.get("content") or p.get("passage"), 3500)
        if len(text) < 35:
            continue
        key = _norm(p.get("passage_id") or text[:240])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(p)

    return deduped


def _passage_role(passage: Dict[str, Any]) -> str:
    return _norm(passage.get("role") or passage.get("pack") or passage.get("section"))


def _score_project_passage(passage: Dict[str, Any], verrou_title: str = "") -> float:
    """
    Score heuristique générique pour sélectionner les passages utiles au contexte CIR.
    Pas de rédaction ici : on choisit seulement les preuves projet utiles.
    """
    text = _safe_text(passage.get("text") or passage.get("content") or passage.get("passage"), 4000)
    n = _norm(text)
    role = _passage_role(passage)

    if not n:
        return 0.0

    score = 0.0

    role_weights = {
        "objectif": 3.0,
        "objectifs": 3.0,
        "verrou": 3.5,
        "verrous": 3.5,
        "limite": 3.2,
        "limites": 3.2,
        "methode": 2.4,
        "methodes": 2.4,
        "resultat": 2.2,
        "resultats": 2.2,
        "contribution": 2.0,
        "contributions": 2.0,
        "parametre": 1.8,
        "parametres": 1.8,
    }
    for token, weight in role_weights.items():
        if token in role:
            score += weight
            break

    # Mots-clés CIR/projet génériques.
    keyword_weights = {
        "representativ": 2.8,
        "validation": 2.4,
        "valider": 2.4,
        "verifier": 2.2,
        "a verifier": 2.2,
        "non demontre": 2.4,
        "necessaire": 1.8,
        "objectif": 1.6,
        "but": 1.5,
        "limite": 1.9,
        "cependant": 1.5,
        "ecart": 1.5,
        "difference": 1.4,
        "comparaison": 1.7,
        "mesure": 1.6,
        "reference": 1.3,
        "standard": 1.3,
        "performance": 1.6,
        "precision": 1.5,
        "robustesse": 1.7,
        "generalisation": 1.8,
        "synthetique": 1.8,
        "augmentation": 1.8,
        "entrainement": 1.3,
        "donnees": 1.2,
        "parametre": 1.4,
        "metrique": 1.4,
        "incertitude": 1.6,
        "transpos": 1.7,
    }
    for kw, weight in keyword_weights.items():
        if kw in n:
            score += weight

    # Termes du verrou pour favoriser les passages alignés sans hardcoding domaine.
    title_terms = [t for t in _norm(verrou_title).split() if len(t) >= 4]
    if title_terms:
        matches = sum(1 for t in title_terms if t in n)
        score += min(matches * 0.8, 4.0)

    # pénalités pour tables très bruitées ou fragments trop courts
    if len(text) < 80:
        score -= 1.0
    if text.count("|") >= 5 or text.count("Figure") >= 8:
        score -= 1.5

    return score


def _select_project_source_passages(
    report: Dict[str, Any],
    verrou_title: str = "",
    max_items: int = 18,
) -> List[Dict[str, Any]]:
    passages = _iter_report_passages(report)
    scored: List[Tuple[float, Dict[str, Any]]] = []

    for p in passages:
        score = _score_project_passage(p, verrou_title=verrou_title)
        if score <= 1.0:
            continue
        scored.append((score, p))

    scored.sort(key=lambda x: x[0], reverse=True)

    out: List[Dict[str, Any]] = []
    seen_text = set()
    for score, p in scored:
        text = _safe_text(p.get("text") or p.get("content") or p.get("passage"), 900)
        key = _norm(text[:260])
        if not text or key in seen_text:
            continue
        seen_text.add(key)
        out.append({
            "passage_id": _safe_text(p.get("passage_id") or p.get("id"), 260),
            "document": _safe_text(p.get("document") or p.get("source_document") or p.get("filename"), 500),
            "section": _safe_text(p.get("section"), 300),
            "role": _safe_text(p.get("role") or p.get("pack"), 120),
            "source": _safe_text(p.get("source"), 160),
            "text_origin": _safe_text(p.get("text_origin"), 160),
            "score": round(score, 3),
            "text": text,
        })
        if len(out) >= max_items:
            break

    return out



def _sentences(text: str, max_items: int = 120) -> List[str]:
    """
    Découpe prudente en phrases courtes.
    Objectif V1.2 : éviter de transformer de longs passages OCR/RAG en champs métier.
    """
    text = _safe_text(text, 16000)
    if not text:
        return []

    # Nettoyage de titres et artefacts de diagnostic qui polluent les champs métier.
    text = re.sub(r"#+\s*", " ", text)
    text = re.sub(r"\bJustification Frascati du score\b", " ", text, flags=re.I)
    text = re.sub(r"\bPourquoi ce score\s*\??", " ", text, flags=re.I)
    text = re.sub(r"\bQuestions de qualification utilisées pour interpréter le score\b", " ", text, flags=re.I)
    text = re.sub(r"\bÉléments qui augmentent le score\b", " ", text, flags=re.I)
    text = re.sub(r"\bÉléments qui limitent le score\b", " ", text, flags=re.I)
    text = re.sub(r"\bCe qui a été vérifié\b", " ", text, flags=re.I)
    text = re.sub(r"\bPoints à valider par le consultant\b", " ", text, flags=re.I)
    text = re.sub(r"\bIndice source\s*:\s*[^.?!]+[.?!]", " ", text, flags=re.I)
    text = re.sub(r"\bSource\s+\d+\b", " ", text, flags=re.I)

    parts = re.split(r"(?<=[.!?])\s+|\n+|\s+-\s+", text)
    out: List[str] = []
    for p in parts:
        p = _clean_context_sentence(p, max_chars=520)
        if len(p) >= 30:
            out.append(p)
        if len(out) >= max_items:
            break
    return out


def _is_noise_context_sentence(text: Any) -> bool:
    """
    Filtre les phrases utiles pour la trace mais inutiles comme besoin/contrainte projet.
    """
    s = _safe_text(text, 800)
    n = _norm(s)
    if not n:
        return True

    noise_patterns = [
        "justification frascati du score",
        "pourquoi ce score",
        "score obtenu",
        "score frascati",
        "nlp/frascati",
        "le nlp frascati",
        "presence de signaux candidats",
        "indice source",
        "document r",
        "passage source",
        "reponse provisoire",
        "réponse provisoire",
        "source 7",
        "source 8",
        "source 9",
        "source 17",
        "figure ",
        "classification du document",
        "tous droits reserves",
        "tous droits réservés",
    ]
    if any(p in n for p in noise_patterns):
        # Exceptions : certaines phrases contiennent "score" mais un vrai fait métier.
        if "doit etre" not in n and "necessaire" not in n and "representativ" not in n:
            return True

    # Table des matières / références / morceaux OCR.
    if s.count("|") >= 3:
        return True
    if len(re.findall(r"\bFigure\s*\d+", s, flags=re.I)) >= 2:
        return True
    if len(s) < 35:
        return True

    return False


def _clean_context_sentence(text: Any, max_chars: int = 500) -> str:
    """
    Nettoie une phrase issue du rapport EnnoDiagnostic sans en inventer le contenu.
    """
    s = _safe_text(text, max_chars=max_chars)
    if not s:
        return ""
    s = re.sub(r"^\s*[-–•\d.)]+\s*", "", s).strip()
    s = re.sub(r"\bJustification Frascati du score\b", "", s, flags=re.I).strip()
    s = re.sub(r"\bRéponse provisoire\s*:\s*", "", s, flags=re.I).strip()
    s = re.sub(r"\bIndice source\s*:\s*.*$", "", s, flags=re.I).strip()
    s = re.sub(r"\s+", " ", s).strip(" -–.;:")
    return _safe_text(s, max_chars=max_chars)


def _first_good_sentences(text: str, keywords: List[str], max_items: int = 2, max_chars_each: int = 340) -> List[str]:
    kws = [_norm(k) for k in keywords if _norm(k)]
    out: List[str] = []
    for s in _sentences(text, max_items=220):
        if _is_noise_context_sentence(s):
            continue
        ns = _norm(s)
        if not kws or any(k in ns for k in kws):
            out.append(_safe_text(s, max_chars_each))
        if len(out) >= max_items:
            break
    return _dedupe_keep_order(out, max_items=max_items)


def _extract_relevant_sentences(text: str, keywords: List[str], max_items: int = 5) -> List[str]:
    kws = [_norm(k) for k in keywords if _norm(k)]
    scored: List[Tuple[int, str]] = []
    for s in _sentences(text, max_items=220):
        if _is_noise_context_sentence(s):
            continue
        ns = _norm(s)
        score = sum(1 for k in kws if k in ns)
        if score:
            scored.append((score, _safe_text(s, 420)))
    scored.sort(key=lambda x: x[0], reverse=True)
    return _dedupe_keep_order([s for _, s in scored], max_items=max_items)


def _compress_project_passage(passage: Dict[str, Any], max_chars: int = 420) -> str:
    """
    Transforme un long passage source en extrait court exploitable.
    On ne synthétise pas avec LLM, on sélectionne les meilleures phrases.
    """
    raw = _safe_text(passage.get("text") or passage.get("content") or passage.get("passage"), 1400)
    if not raw:
        return ""
    role = _norm(passage.get("role") or passage.get("pack") or passage.get("section"))
    if "objectif" in role:
        kws = ["but", "objectif", "afin", "valider", "evaluer", "évaluer", "representativ", "entrainer", "entraîner"]
    elif "verrou" in role or "limite" in role:
        kws = ["cependant", "limite", "necessaire", "nécessaire", "verifier", "vérifier", "representativ", "non", "ecart", "différence", "conditions"]
    elif "methode" in role:
        kws = ["validation", "valider", "methode", "méthode", "metrique", "métrique", "comparaison", "mesure", "reference", "référence"]
    elif "resultat" in role or "contribution" in role:
        kws = ["montre", "montré", "gain", "resultat", "résultat", "precision", "précision", "representativ", "cependant", "reste"]
    else:
        kws = ["validation", "representativ", "necessaire", "objectif", "limite", "comparaison"]

    selected = _first_good_sentences(raw, kws, max_items=2, max_chars_each=max_chars)
    if selected:
        return _safe_text(" ".join(selected), max_chars)

    for s in _sentences(raw, max_items=10):
        if not _is_noise_context_sentence(s):
            return _safe_text(s, max_chars)
    return ""


def _summarize_from_passages(passages: List[Dict[str, Any]], keywords: List[str], max_items: int = 5) -> List[str]:
    out: List[str] = []
    kws = [_norm(k) for k in keywords if _norm(k)]
    for p in passages:
        raw = _safe_text(p.get("text"), 1400)
        n = _norm(raw)
        if any(k in n for k in kws):
            short = _compress_project_passage(p)
            if short and not _is_noise_context_sentence(short):
                out.append(short)
        if len(out) >= max_items:
            break
    return _dedupe_keep_order(out, max_items=max_items)


def _derive_validation_criteria(frascati_text: str, passages: List[Dict[str, Any]]) -> List[str]:
    combined = _norm(frascati_text + "\n" + "\n".join(_safe_text(p.get("text"), 500) for p in passages))

    candidate_map = [
        ("représentativité des données générées ou synthétiques", ["representativ", "synthetique"]),
        ("validation sur données de mesure ou données de référence", ["mesure", "reference", "standard"]),
        ("comparaison avec une méthode, un simulateur ou un référentiel de référence", ["comparaison", "reference", "standard"]),
        ("définition et stabilité des paramètres de validation", ["parametre", "metrique", "validation"]),
        ("performance de classification ou de détection", ["performance", "precision", "classification", "detection"]),
        ("robustesse et généralisation en conditions non vues", ["robustesse", "generalisation", "conditions"]),
        ("qualité ou fidélité des sorties produites", ["qualite", "fidelite", "image", "sortie"]),
        ("temps de calcul, coût de production ou passage à l’échelle", ["temps de calcul", "cout", "ressource", "stockage", "echelle", "large scale"]),
    ]

    out: List[str] = []
    for label, kws in candidate_map:
        if any(k in combined for k in kws):
            out.append(label)

    return _dedupe_keep_order(out, max_items=8)


def _canonical_constraint_from_sentence(sentence: str) -> str:
    """
    Convertit les questions/statuts Frascati en contraintes propres.
    Exemple : "Les paramètres ... ? Réponse provisoire : non démontré" ->
    "Définir et appliquer rigoureusement les paramètres de validation."
    """
    s = _clean_context_sentence(sentence, max_chars=520)
    n = _norm(s)
    if not s or _is_noise_context_sentence(s):
        # Certaines questions utiles sont classées bruit à cause de "réponse provisoire" ; on les traite avant abandon.
        pass

    if "representativ" in n and ("donnees" in n or "données" in s.lower()):
        return "Démontrer la représentativité des données dans les conditions d’utilisation visées par le projet."
    if "modeles" in n and ("mesure" in n or "valid" in n):
        return "Valider les modèles sur des observations ou des données de référence indépendantes des données de construction."
    if "parametre" in n and "validation" in n:
        return "Définir, justifier et appliquer rigoureusement les paramètres de validation utilisés pour qualifier les données et les modèles."
    if "resultats" in n and ("standard" in n or "reference" in n or "compar" in n):
        return "Comparer les résultats obtenus à des standards, simulateurs, mesures ou données de référence."
    if "comparaison" in n and ("reference" in n or "mesure" in n or "standard" in n):
        return "Prévoir une comparaison explicite avec une référence représentative pour confirmer la validité des résultats."
    if "robust" in n or "generalisation" in n:
        return "Vérifier la robustesse et la généralisation des modèles dans des conditions non vues ou différentes des données d’entraînement."
    if "temps de calcul" in n or "stockage" in n or "memoire" in n or "passage a l echelle" in n:
        return "Maîtriser le coût de calcul, le volume de stockage et le passage à l’échelle des données générées."
    if "phenomene" in n or "physique" in n:
        return "Caractériser les phénomènes physiques et les conditions expérimentales qui influencent la représentativité des résultats."

    if _is_noise_context_sentence(s):
        return ""
    return s


def _derive_constraints(frascati_text: str, passages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    constraints: List[Dict[str, Any]] = []

    # 1) Contraintes canoniques depuis Frascati : propres, courtes, non bruitées.
    fr_sentences = _sentences(frascati_text, max_items=120)
    for s in fr_sentences:
        ns = _norm(s)
        if not any(k in ns for k in [
            "representativ", "a verifier", "non demontre", "doit", "necessaire",
            "parametre", "comparaison", "donnees de reference", "mesure", "generalisation", "robustesse"
        ]):
            continue
        c = _canonical_constraint_from_sentence(s)
        if not c:
            continue
        constraints.append({
            "constraint": c,
            "source": "frascati_justification",
            "why_it_matters_for_verrou": "Ce point conditionne la démonstration que le verrou reste ouvert dans le contexte du projet.",
        })

    # 2) Contraintes depuis passages sources, mais sous forme courte.
    for p in passages:
        raw = _safe_text(p.get("text"), 1400)
        n = _norm(raw)
        if not any(k in n for k in ["cependant", "limite", "necessaire", "verifier", "validation", "ecart", "difference", "representativ", "rugosite", "diffraction", "mesure"]):
            continue
        c = _canonical_constraint_from_sentence(_compress_project_passage(p))
        if not c:
            continue
        constraints.append({
            "constraint": c,
            "source": "passage_source_ennodiagnostic",
            "why_it_matters_for_verrou": "Ce passage apporte une contrainte ou une limite projet à prendre en compte dans l’état de l’art.",
        })

    return _dedupe_keep_order(constraints, max_items=10)


def _derive_project_need(project: Project, verrou_title: str, frascati_text: str, passages: List[Dict[str, Any]]) -> str:
    """
    Produit un besoin projet propre sans LLM.
    Le texte est générique et se nourrit des signaux détectés, sans codage domaine fermé.
    """
    combined = _norm(frascati_text + "\n" + "\n".join(_safe_text(p.get("text"), 900) for p in passages))
    target = "les données, méthodes ou systèmes étudiés"
    if "donnee" in combined:
        target = "les données utilisées ou produites"
    elif "modele" in combined:
        target = "les modèles étudiés"
    elif "simulation" in combined:
        target = "les simulations et leurs conditions de validité"

    action = "vérifier"
    if "validation" in combined or "valider" in combined:
        action = "valider"

    criteria_parts: List[str] = []
    if "representativ" in combined:
        criteria_parts.append("leur représentativité")
    if "performance" in combined or "precision" in combined:
        criteria_parts.append("leur impact sur la performance")
    if "generalisation" in combined or "robustesse" in combined:
        criteria_parts.append("leur capacité de généralisation")
    if "mesure" in combined or "reference" in combined or "standard" in combined:
        criteria_parts.append("leur cohérence avec des données de mesure ou de référence")
    if not criteria_parts:
        criteria_parts.append("leur pertinence dans les conditions d’utilisation du projet")

    return _safe_text(
        f"Le besoin du projet {project.project_name} est de {action} que {target} sont suffisantes pour soutenir l’objectif technique visé, "
        f"notamment {', '.join(criteria_parts)}. Cette vérification doit être menée dans le contexte propre du dossier, et non par simple transposition d’une méthode générique.",
        900,
    )


def _derive_objective(project: Project, verrou_title: str, frascati_text: str, passages: List[Dict[str, Any]]) -> str:
    candidates = _summarize_from_passages(
        passages,
        ["but", "objectif", "évaluer", "evaluer", "valider", "entrainer", "entraîner", "produire", "comparer"],
        3,
    )
    if candidates:
        return _safe_text(" ".join(candidates[:2]), 950)

    # Fallback propre si les passages sont trop bruités.
    return _safe_text(
        f"L’objectif technique est d’analyser le verrou « {verrou_title or 'non précisé'} » à partir des preuves projet et des références scientifiques disponibles, afin d’identifier ce qui reste à vérifier expérimentalement.",
        900,
    )


def _derive_uncertainty(frascati_text: str, passages: List[Dict[str, Any]]) -> str:
    constraints = _derive_constraints(frascati_text, passages)
    labels = [c.get("constraint") for c in constraints if c.get("source") == "frascati_justification"][:4]
    if labels:
        return _safe_text(" ".join(labels), 1200)

    candidates = _extract_relevant_sentences(
        frascati_text,
        ["à vérifier", "non démontré", "n'est pas", "limite", "représentativité", "incertitude", "doit être confirmée"],
        max_items=3,
    )
    return _safe_text(" ".join(candidates), 1200)


def _build_project_context_structured(
    project: Project,
    diagnostic_context: Dict[str, Any],
    verrou_title: str = "",
) -> Dict[str, Any]:
    """
    Bloc propre pour Phase 4.6 / Phase 5.
    V1.2 : source EnnoDiagnostic prioritaire + nettoyage sémantique.
    Les longs passages restent en preuves, pas dans besoin_projet/objectif_technique.
    """
    report_info = _as_dict(diagnostic_context.get("ennodiagnostic_report"))
    report = _as_dict(report_info.get("report"))
    frascati_text = _safe_text(diagnostic_context.get("frascati_justification_text"), 7000)
    passages = _select_project_source_passages(report, verrou_title=verrou_title, max_items=18) if report else []

    if not frascati_text:
        frascati_text = _safe_text(diagnostic_context.get("diagnostic_context_text"), 5000)

    data_env_candidates = _summarize_from_passages(
        passages,
        ["données", "donnees", "dataset", "mesure", "simulation", "observation", "modèle", "modele", "environnement", "protocole", "simulateur"],
        5,
    )

    constraints = _derive_constraints(frascati_text, passages)
    criteria = _derive_validation_criteria(frascati_text, passages)

    return {
        "available": bool(report or frascati_text or passages),
        "source_priority": [
            "ennodiagnostic_report.json",
            "DiagnosticRun.raw_result_json fallback",
            "Verrou DB",
        ],
        "report_path": report_info.get("path") or "",
        "besoin_projet": _derive_project_need(project, verrou_title, frascati_text, passages),
        "objectif_technique": _derive_objective(project, verrou_title, frascati_text, passages),
        "contexte_technique": _safe_text(
            f"Projet {project.project_name} porté par {project.organisme}, année {project.year}. "
            f"Domaine détecté : {getattr(project, 'domain_label', '') or 'non précisé'}. "
            f"Verrou ciblé : {verrou_title or 'non précisé'}.",
            900,
        ),
        "donnees_et_environnement": _dedupe_keep_order(data_env_candidates, max_items=6),
        "contraintes_projet": constraints,
        "criteres_validation": criteria,
        "incertitude_rd": _derive_uncertainty(frascati_text, passages),
        "points_de_preuve_projet": passages[:12],
        "trace": {
            "from_ennodiagnostic_report": bool(report_info.get("available")),
            "from_frascati_justification": bool(frascati_text),
            "source_passages_count": len(passages),
            "semantic_cleaning_v1_2": True,
        },
    }


def _title_keywords(text: str, max_items: int = 12) -> List[str]:
    stop = {
        "pour", "avec", "dans", "des", "les", "une", "the", "and", "sur", "par", "aux", "du", "de", "la", "le",
        "validation", "efficacite", "methodes", "methode", "donnees", "classification",
    }
    terms = []
    for t in _norm(text).split():
        if len(t) >= 4 and t not in stop:
            terms.append(t)
    return _dedupe_keep_order(terms, max_items=max_items)


def _filter_passages_for_verrou(passages: List[Dict[str, Any]], verrou_title: str, max_items: int = 8) -> List[Dict[str, Any]]:
    kws = _title_keywords(verrou_title)
    if not kws:
        return passages[:max_items]

    scored: List[Tuple[int, Dict[str, Any]]] = []
    for p in passages:
        n = _norm(p.get("text"))
        score = sum(1 for k in kws if k in n)
        role = _norm(p.get("role"))
        if "verrou" in role:
            score += 3
        if "objectif" in role or "limite" in role:
            score += 2
        if score > 0:
            scored.append((score, p))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in scored[:max_items]] or passages[:max_items]



def _build_verrou_context_structured(
    project: Project,
    db_verrou: Optional[Verrou],
    verrou_title: str,
    objectif_rnd: str,
    diagnostic_context: Dict[str, Any],
    source_signals: List[str],
    project_context_structured: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Contexte structuré spécialisé par verrou.
    V1.2 : champs courts, propres, utilisables directement par Phase 4.6.
    """
    project_passages = _as_list(project_context_structured.get("points_de_preuve_projet"))
    verrou_passages = _filter_passages_for_verrou(project_passages, verrou_title, max_items=10)

    justification = _safe_text(getattr(db_verrou, "justification", "") if db_verrou else "", 1200)
    src = _as_dict(getattr(db_verrou, "source_json", None) if db_verrou else None)

    explicit_problem = ""
    for key in ["probleme_a_resoudre", "problem", "scientific_problem", "technical_problem", "question_qualification", "question"]:
        explicit_problem = _clean_context_sentence(src.get(key), 900)
        if explicit_problem:
            break

    if not explicit_problem:
        candidates = _extract_relevant_sentences(
            "\n".join(source_signals) + "\n" + project_context_structured.get("incertitude_rd", ""),
            ["comment", "vérifier", "verifier", "démontré", "demontre", "représentativité", "representativ", "limite", "incertitude", "conditions"],
            max_items=2,
        )
        explicit_problem = _safe_text(" ".join(candidates), 1000)

    if not explicit_problem and verrou_title:
        explicit_problem = f"Vérifier dans quelle mesure le verrou « {verrou_title} » reste non résolu dans le contexte technique propre du projet."

    criteria = _as_list(project_context_structured.get("criteres_validation"))

    if justification:
        limite = justification
    else:
        limite = _safe_text(project_context_structured.get("incertitude_rd"), 1200)

    return {
        "available": True,
        "besoin_associe": _safe_text(
            project_context_structured.get("besoin_projet")
            or objectif_rnd
            or verrou_title,
            1000,
        ),
        "objectif_rd": _safe_text(objectif_rnd or verrou_title, 700),
        "probleme_a_resoudre": explicit_problem,
        "limite_identifiee": _safe_text(limite, 1000),
        "pourquoi_ce_n_est_pas_un_simple_developpement": _safe_text(
            "Le point à démontrer ne se limite pas à appliquer une méthode existante : il faut vérifier son comportement, "
            "sa représentativité, ses paramètres, ses limites de transposition et sa généralisation dans les conditions propres du projet.",
            900,
        ),
        "ce_que_l_etat_de_l_art_devra_verifier": _safe_text(
            "L’état de l’art doit établir ce que les travaux existants savent déjà faire, puis montrer ce qu’ils ne démontrent pas "
            "pour ce contexte projet : représentativité, validation expérimentale, transposition, robustesse, généralisation et critères de comparaison.",
            900,
        ),
        "criteres_de_succes": criteria,
        "contraintes_associees": _as_list(project_context_structured.get("contraintes_projet"))[:8],
        "preuves_sources": verrou_passages,
        "trace": {
            "from_verrou_db_title": bool(verrou_title),
            "from_verrou_db_justification": bool(justification),
            "from_ennodiagnostic_project_context": bool(project_context_structured.get("available")),
            "source_signals_count": len(source_signals or []),
            "preuves_sources_count": len(verrou_passages),
            "semantic_cleaning_v1_2": True,
        },
    }


def _extract_diagnostic_context(db: Session, project: Project) -> Dict[str, Any]:
    """
    Contexte projet pour le payload Phase 1.

    Priorité corrigée :
    1) ennodiagnostic/ennodiagnostic_report.json dans le stockage projet ;
    2) DiagnosticRun.raw_result_json en fallback DB ;
    3) aplatissage brut uniquement en dernier recours.
    """
    report_info = _load_ennodiagnostic_report(project)
    report = _as_dict(report_info.get("report"))
    frascati_text = _extract_frascati_text_from_report(report) if report else ""
    selected_passages = _select_project_source_passages(report, max_items=18) if report else []

    run = _get_latest_diagnostic_run(db, project)

    db_context_text = ""
    db_source_label = ""
    if run:
        raw = _as_dict(getattr(run, "raw_result_json", None))
        source = raw or _as_dict(getattr(run, "source_json", None))
        db_source_label = "diagnostic_run.raw_result_json" if raw else "diagnostic_run.source_json"

        text_candidates: List[str] = []
        for key in [
            "resume_strategique",
            "résumé_stratégique",
            "synthese",
            "synthèse",
            "objectif_global",
            "objectif",
            "content",
            "report_markdown",
            "diagnostic",
        ]:
            value = source.get(key) if isinstance(source, dict) else None

            if isinstance(value, str):
                text_candidates.append(value)
            elif isinstance(value, dict):
                text_candidates.append(_flatten_text(value, max_chars=2500))

        db_context_text = _safe_text("\n".join([x for x in text_candidates if x]), 4500)

        if not db_context_text and source:
            db_context_text = _flatten_text(source, max_chars=4500)

    # Texte exploitable prioritaire : justification Frascati + meilleurs passages sources.
    report_passages_text = "\n".join(
        f"[{p.get('role') or 'source'}] {p.get('text')}"
        for p in selected_passages[:10]
        if p.get("text")
    )

    context_text = _safe_text(
        "\n".join([x for x in [frascati_text, report_passages_text, db_context_text] if x]),
        8000,
    )

    return {
        "available": bool(context_text),
        "diagnostic_run_id": run.id if run else None,
        "diagnostic_context_text": context_text,
        "diagnostic_context_source": (
            "ennodiagnostic_report.json+source_passages"
            if report_info.get("available")
            else db_source_label or "none"
        ),
        "ennodiagnostic_report": {
            "available": bool(report_info.get("available")),
            "path": report_info.get("path") or "",
            "checked_paths": report_info.get("checked_paths") or [],
            # On garde le rapport complet en mémoire pendant la construction ; il sera retiré du payload final.
            "report": report,
        },
        "frascati_justification_text": frascati_text,
        "selected_source_passages": selected_passages,
    }


def _article_validation(article: Article) -> Dict[str, Any]:
    sj = _as_dict(article.source_json)
    validation = sj.get("verrou_scientific_validation")

    return validation if isinstance(validation, dict) else {}


def _article_scientific_intent(article: Article) -> Dict[str, Any]:
    sj = _as_dict(article.source_json)

    for key in [
        "scientific_intent",
        "intent",
        "query_context",
    ]:
        value = sj.get(key)
        if isinstance(value, dict):
            return value

    validation = _article_validation(article)
    return validation if validation else {}


def _verrou_title_from_article(article: Article, verrous_by_id: Dict[int, Verrou]) -> str:
    validation = _article_validation(article)
    scientific_intent = _article_scientific_intent(article)
    sj = _as_dict(article.source_json)

    db_verrou = verrous_by_id.get(article.verrou_id) if article.verrou_id else None

    candidates = [
        db_verrou.title if db_verrou else "",
        validation.get("verrou_title"),
        scientific_intent.get("verrou_title"),
        scientific_intent.get("title"),
        sj.get("verrou_title"),
        sj.get("enriched_title"),
        sj.get("scientific_title"),
    ]

    for value in candidates:
        text = _safe_text(value, 500)
        if text:
            return text

    if article.verrou_id:
        return f"Verrou lié {article.verrou_id}"

    return "Verrou scientifique non identifié"


def _verrou_key_from_article(article: Article, verrous_by_id: Dict[int, Verrou]) -> str:
    if article.verrou_id:
        return f"verrou_db:{article.verrou_id}"

    validation = _article_validation(article)
    scientific_intent = _article_scientific_intent(article)
    sj = _as_dict(article.source_json)

    explicit = (
        validation.get("verrou_id")
        or scientific_intent.get("verrou_id")
        or sj.get("verrou_id")
        or sj.get("verrou_key")
        or sj.get("group_id")
    )

    if explicit:
        return f"verrou_src:{_safe_text(explicit, 120)}"

    title = _verrou_title_from_article(article, verrous_by_id)
    return f"verrou_title:{_norm(title)[:180]}"


def _extract_source_signals(article: Article, db_verrou: Optional[Verrou]) -> List[str]:
    signals: List[str] = []

    if db_verrou:
        if db_verrou.title:
            signals.append(db_verrou.title)
        if db_verrou.justification:
            signals.append(db_verrou.justification)

        src = _as_dict(db_verrou.source_json)
        for key in [
            "original_title",
            "enriched_title",
            "scientific_query_text",
            "text",
            "justification",
        ]:
            if src.get(key):
                signals.append(str(src.get(key)))

    sj = _as_dict(article.source_json)
    validation = _article_validation(article)
    scientific_intent = _article_scientific_intent(article)

    for obj in [validation, scientific_intent, sj]:
        for key in [
            "verrou_title",
            "original_title",
            "enriched_title",
            "scientific_problem",
            "technical_object",
            "phenomenon",
            "diagnostic_context_text",
            "reason",
        ]:
            if isinstance(obj, dict) and obj.get(key):
                signals.append(str(obj.get(key)))

    return [_safe_text(x, 600) for x in _dedupe_keep_order(signals, 12)]


def _extract_objectif_rnd(
    project: Project,
    db_verrou: Optional[Verrou],
    article_group: List[Article],
    diagnostic_context: Dict[str, Any],
) -> str:
    """
    Phase 1 : objectif R&D court et prudent.
    La formulation sera améliorée en Phase 4/5 par le writer.
    """
    candidates: List[str] = []

    if db_verrou:
        src = _as_dict(db_verrou.source_json)

        for key in [
            "objectif_r&d",
            "objectif_rd",
            "objectif",
            "objective",
            "scientific_objective",
            "verrou_title",
            "enriched_title",
            "original_title",
        ]:
            if src.get(key):
                candidates.append(str(src.get(key)))

        if db_verrou.title:
            candidates.append(db_verrou.title)

        if db_verrou.justification:
            candidates.append(db_verrou.justification)

    for article in article_group[:3]:
        intent = _article_scientific_intent(article)
        validation = _article_validation(article)

        for obj in [intent, validation]:
            for key in ["scientific_problem", "verrou_title", "technical_object", "phenomenon"]:
                if isinstance(obj, dict) and obj.get(key):
                    candidates.append(str(obj.get(key)))

    for value in candidates:
        text = _safe_text(value, 420)
        if text:
            return text

    return (
        f"Qualifier l'état de l'art scientifique associé au verrou du projet "
        f"{project.project_name}, afin d'identifier les limites des approches existantes "
        f"et de préparer la justification des travaux R&D."
    )


def _extract_contexte_projet(
    project: Project,
    db_verrou: Optional[Verrou],
    article_group: List[Article],
    diagnostic_context: Dict[str, Any],
) -> str:
    parts: List[str] = []

    parts.append(
        f"Projet {project.project_name} porté par {project.organisme}, année {project.year}."
    )

    if project.domain_label:
        parts.append(f"Domaine détecté : {project.domain_label}.")

    if db_verrou and db_verrou.justification:
        parts.append(f"Justification du verrou : {_safe_text(db_verrou.justification, 700)}")

    # V1.2 : contexte court pour compatibilité legacy. Le contexte détaillé est dans project_context_structured.
    fr_text = _safe_text(diagnostic_context.get("frascati_justification_text"), 1200)
    useful = _extract_relevant_sentences(
        fr_text,
        ["représentativité", "representativ", "à vérifier", "non démontré", "paramètres", "comparaison", "mesure", "référence"],
        max_items=3,
    )
    if useful:
        parts.append("Contexte EnnoDiagnostic : " + _safe_text(" ".join(useful), 1200))

    # Complément depuis le premier article si EnnoScholar a déjà stocké un contexte.
    for article in article_group[:2]:
        sj = _as_dict(article.source_json)
        ctx = sj.get("context")
        if isinstance(ctx, dict):
            ctx_text = _flatten_text(ctx, max_chars=700)
            if ctx_text:
                parts.append(f"Contexte article/verrou : {ctx_text}")
                break

    return _safe_text("\n".join(parts), 2600)


def _build_contraintes_cir() -> List[str]:
    return [
        "Rédiger l'état de l'art par verrou scientifique, et non comme une liste de résumés article par article.",
        "Utiliser uniquement les articles gardés par le consultant comme preuves scientifiques.",
        "Citer uniquement les références autorisées sous la forme [A1], [A2], etc.",
        "Exclure les articles Hors sujet, rejetés, en attente et les sources techniques non académiques de la rédaction scientifique.",
        "Utiliser majoritairement les articles Directs et Connexes ; les articles Fondamentaux servent seulement à contextualiser.",
        "Mettre en évidence ce qui existe déjà, ce qui est maîtrisé, ce qui reste insuffisant et ce qui n'est pas directement transposable au projet.",
        "Ne jamais inventer de résultat, de performance, d'auteur, d'année, de DOI ou de référence.",
        "La mémoire de style, lorsqu'elle sera activée, servira uniquement au style rédactionnel et jamais comme preuve scientifique.",
    ]


# ============================================================
# Query articles
# ============================================================

def _get_kept_articles(db: Session, project: Project) -> List[Article]:
    return get_current_selected_articles(db, project)


def _sort_articles_for_writer(articles: List[Article]) -> List[Article]:
    def key(article: Article) -> Tuple[int, float, int]:
        tag = normalize_article_tag(article.tag_article)

        tag_rank = {
            "Direct": 3,
            "Connexe": 2,
            "Fondamental": 1,
        }.get(tag, 0)

        score = _score_float(article.score)
        year = _year_int(article.year) or 0

        return (tag_rank, score, year)

    return sorted(articles, key=key, reverse=True)


def _limit_articles_by_type(
    articles: List[Article],
    tag: str,
    max_count: int,
) -> Tuple[List[Article], List[Article]]:
    selected = [a for a in articles if normalize_article_tag(a.tag_article) == tag]
    selected = _sort_articles_for_writer(selected)

    if max_count <= 0:
        return selected, []

    return selected[:max_count], selected[max_count:]

def _slugify_path(value: Any) -> str:
    text = _strip_accents(str(value or "unknown")).lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "unknown"



def _public_diagnostic_context(diagnostic_context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Version légère du contexte diagnostic à écrire dans selection_payload.json.
    On ne sauvegarde pas le rapport complet pour éviter un payload énorme.
    """
    report_info = _as_dict(diagnostic_context.get("ennodiagnostic_report"))
    return {
        "available": bool(diagnostic_context.get("available")),
        "diagnostic_run_id": diagnostic_context.get("diagnostic_run_id"),
        "diagnostic_context_source": diagnostic_context.get("diagnostic_context_source"),
        "diagnostic_context_text": _safe_text(diagnostic_context.get("frascati_justification_text") or diagnostic_context.get("diagnostic_context_text"), 2200),
        "ennodiagnostic_report": {
            "available": bool(report_info.get("available")),
            "path": report_info.get("path") or "",
            "checked_paths": report_info.get("checked_paths") or [],
        },
        "frascati_justification_text": _safe_text(diagnostic_context.get("frascati_justification_text"), 6000),
        "selected_source_passages": _as_list(diagnostic_context.get("selected_source_passages"))[:18],
    }

# ============================================================
# Synchronisation stricte sélection -> artefacts dérivés
# ============================================================

_ARTICLE_FILE_RE = re.compile(r"^article_(\d+)_", flags=re.IGNORECASE)


def _selection_article_ids(payload: Dict[str, Any]) -> set[int]:
    """Retourne les IDs DB présents dans la sélection courante."""
    out: set[int] = set()
    for verrou in _as_list((payload or {}).get("verrous")):
        if not isinstance(verrou, dict):
            continue
        for key in ["articles_directs", "articles_connexes", "articles_fondamentaux", "selected_articles"]:
            for article in _as_list(verrou.get(key)):
                if not isinstance(article, dict):
                    continue
                raw_id = article.get("article_id") or article.get("db_article_id") or article.get("id")
                try:
                    article_id = int(raw_id)
                except (TypeError, ValueError):
                    continue
                if article_id > 0:
                    out.add(article_id)
    for article in _as_list((payload or {}).get("supplemental_guided_articles")):
        if not isinstance(article, dict):
            continue
        raw_id = article.get("article_id") or article.get("db_article_id") or article.get("id")
        try:
            article_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if article_id > 0:
            out.add(article_id)
    return out


def _read_existing_json(path: Path) -> Dict[str, Any]:
    try:
        if path.exists() and path.is_file():
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
    except Exception:
        return {}
    return {}


def _article_id_from_artifact(path: Path) -> Optional[int]:
    match = _ARTICLE_FILE_RE.match(path.name)
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def _delete_stale_article_artifacts(directory: Path, selected_ids: set[int]) -> List[str]:
    """Supprime seulement les fichiers article_<id>_* des articles retirés."""
    deleted: List[str] = []
    if not directory.exists():
        return deleted
    for path in directory.rglob("*"):
        if not path.is_file():
            continue
        article_id = _article_id_from_artifact(path)
        if article_id is None or article_id in selected_ids:
            continue
        try:
            path.unlink()
            deleted.append(str(path))
        except OSError as exc:
            print(f"[EnnoScholar][SelectionSync] DELETE_FAILED path={path} error={exc}", flush=True)
    return deleted


def _delete_tree(directory: Path) -> List[str]:
    """Supprime un dossier de sortie dérivé sans toucher aux sources projet."""
    deleted: List[str] = []
    if not directory.exists():
        return deleted
    for path in sorted(directory.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        try:
            if path.is_file():
                path.unlink()
                deleted.append(str(path))
            elif path.is_dir():
                path.rmdir()
        except OSError:
            pass
    try:
        directory.rmdir()
    except OSError:
        pass
    return deleted


def synchronize_state_of_art_artifacts_with_selection(
    project: Project,
    selection_payload: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Rend le stockage strictement cohérent avec la sélection consultant courante.

    - conserve les extractions et cartes individuelles des articles encore sélectionnés ;
    - supprime les artefacts article_<id>_* des articles retirés ;
    - invalide le payload agrégé des Article Cards si la sélection change ;
    - invalide les Phases 4 à 5, car les citations A1/A2/... doivent être recalculées ;
    - ne supprime jamais les lignes Article en base ni les documents sources du projet.
    """
    selected_ids = _selection_article_ids(selection_payload)
    selection_path = state_of_art_selection_payload_path(project)
    previous_payload = _read_existing_json(selection_path)
    previous_ids = _selection_article_ids(previous_payload)
    selection_changed = previous_ids != selected_ids

    scholar_dir = _project_ennoscholar_dir(project)
    fulltext_dir = scholar_dir / "fulltext"
    state_dir = scholar_dir / "state_of_art_payload"
    cards_dir = state_dir / "article_cards"

    # Tous ces répertoires ne contiennent que des artefacts dérivés.
    # La suppression est limitée aux noms article_<id>_*.
    artifact_dirs = [
        fulltext_dir / "extracted_direct",
        fulltext_dir / "extracted_uploaded",
        fulltext_dir / "extracted_legal",
        fulltext_dir / "extracted",
        fulltext_dir / "status",
        fulltext_dir / "html",
        fulltext_dir / "debug",
        cards_dir,
    ]

    deleted_stale_files: List[str] = []
    for directory in artifact_dirs:
        deleted_stale_files.extend(_delete_stale_article_artifacts(directory, selected_ids))

    # Une trace obsolète trouvée signifie aussi que les agrégats doivent être reconstruits.
    artifacts_changed = bool(deleted_stale_files)
    must_invalidate = selection_changed or artifacts_changed

    aggregate_cards_path = cards_dir / "article_cards_payload.json"
    aggregate_cards_deleted = False
    if must_invalidate and aggregate_cards_path.exists():
        try:
            aggregate_cards_path.unlink()
            aggregate_cards_deleted = True
        except OSError as exc:
            print(
                f"[EnnoScholar][SelectionSync] CARD_PAYLOAD_DELETE_FAILED "
                f"path={aggregate_cards_path} error={exc}",
                flush=True,
            )

    deleted_downstream_files: List[str] = []
    if must_invalidate:
        for directory_name in [
            "phase_4_scientific_gap",
            "phase_4_5_scientific_reasoning",
            "phase_4_6_project_rd_argumentation",
            "phase_4_7_scientific_narrative",
            "phase_5_state_of_art_writer",
        ]:
            deleted_downstream_files.extend(_delete_tree(state_dir / directory_name))

    result = {
        "ok": True,
        "selection_changed": selection_changed,
        "previous_selected_articles_count": len(previous_ids),
        "selected_articles_count": len(selected_ids),
        "previous_article_ids": sorted(previous_ids),
        "selected_article_ids": sorted(selected_ids),
        "removed_article_ids": sorted(previous_ids - selected_ids),
        "added_article_ids": sorted(selected_ids - previous_ids),
        "deleted_stale_files_count": len(deleted_stale_files),
        "deleted_stale_files": deleted_stale_files,
        "article_cards_payload_invalidated": aggregate_cards_deleted,
        "downstream_invalidated": must_invalidate,
        "deleted_downstream_files_count": len(deleted_downstream_files),
    }

    print(
        "[EnnoScholar][SelectionSync] "
        f"previous={len(previous_ids)} current={len(selected_ids)} "
        f"removed={len(previous_ids - selected_ids)} added={len(selected_ids - previous_ids)} "
        f"deleted_stale={len(deleted_stale_files)} invalidate={must_invalidate}",
        flush=True,
    )
    return result


# ============================================================
# Construction payload par verrou
# ============================================================

def build_state_of_art_selection_payload(
    db: Session,
    project: Project,
) -> Dict[str, Any]:
    """
    PHASE 1.

    Retourne le payload propre de rédaction, sans appeler le LLM.
    """
    # V1.3 : par défaut, on garde TOUS les articles validés par le consultant.
    # Ancien comportement : Direct=4, Connexe=4, Fondamental=2 par verrou.
    # Ici, 0 signifie "aucune limite". Tu peux remettre une limite par variable d'environnement
    # si tu veux réduire le payload envoyé aux phases suivantes.
    max_direct = int(os.getenv("ENNOSCHOLAR_WRITER_MAX_DIRECT", "0"))
    max_connexe = int(os.getenv("ENNOSCHOLAR_WRITER_MAX_CONNEXE", "0"))
    max_fondamental = int(os.getenv("ENNOSCHOLAR_WRITER_MAX_FONDAMENTAL", "0"))

    kept_articles = _get_kept_articles(db, project)
    verrous_by_id = _get_project_verrous(db, project)
    diagnostic_context = _extract_diagnostic_context(db, project)
    latest_run = _get_latest_scholar_run(db, project)
    latest_run_context = (
        _as_dict(latest_run.raw_result_json) if latest_run is not None else {}
    )
    standalone_mode = (
        _safe_text(latest_run_context.get("mode"), 80).casefold()
        == "standalone_chat"
    )
    standalone_brief = (
        _as_dict(latest_run_context.get("project_brief"))
        if standalone_mode
        else {}
    )
    standalone_verrous = [
        dict(row)
        for row in _as_list(latest_run_context.get("consultant_verrous"))
        if isinstance(row, dict)
        and _safe_text(row.get("id"), 120)
        and _safe_text(row.get("title"), 1600)
    ]
    standalone_verrous_by_id = {
        _safe_text(row.get("id"), 120): row for row in standalone_verrous
    }
    standalone_active_ids = [
        _safe_text(value, 120)
        for value in _as_list(latest_run_context.get("active_verrou_ids"))
        if _safe_text(value, 120) in standalone_verrous_by_id
    ]

    def standalone_project_context(
        verrou: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        verrou = verrou or {}
        objective = _safe_text(
            standalone_brief.get("objective")
            or standalone_brief.get("project_objective"),
            2400,
        )
        description = _safe_text(
            standalone_brief.get("description")
            or standalone_brief.get("project_description"),
            3000,
        )
        domain = _safe_text(
            standalone_brief.get("domain") or project.domain_label,
            1000,
        )
        verrou_title = _safe_text(verrou.get("title"), 2400)
        verrou_justification = _safe_text(
            verrou.get("justification") or verrou.get("supporting_context"),
            3000,
        )
        return {
            "available": True,
            "source_priority": ["guided_consultant_conversation"],
            "report_path": "",
            "besoin_projet": description or objective,
            "objectif_technique": objective,
            "contexte_technique": " ".join(
                value for value in (domain, description) if value
            ),
            "donnees_et_environnement": list(
                standalone_brief.get("data_and_environment") or []
            ),
            "contraintes_projet": list(
                standalone_brief.get("constraints") or []
            ),
            "criteres_validation": list(
                standalone_brief.get("validation_criteria") or []
            ),
            "incertitude_rd": verrou_title or verrou_justification,
            "points_de_preuve_projet": [
                {"role": role, "text": value, "source": "guided_conversation"}
                for role, value in (
                    ("objectif", objective),
                    ("verrou", verrou_title),
                    ("justification", verrou_justification),
                )
                if value
            ],
            "trace": {
                "from_guided_consultant_conversation": True,
                "guided_session_id": latest_run_context.get("guided_session_id"),
                "standalone_without_diagnostic": True,
            },
        }

    # Contexte projet global construit depuis EnnoDiagnostic final.
    # Il sera aussi spécialisé par verrou plus bas.
    project_context_global = _build_project_context_structured(
        project=project,
        diagnostic_context=diagnostic_context,
        verrou_title="",
    )
    if standalone_mode and standalone_verrous:
        project_context_global = standalone_project_context()

    groups: Dict[str, Dict[str, Any]] = {}

    excluded_articles: List[Dict[str, Any]] = []
    supplemental_guided_articles: List[Dict[str, Any]] = []

    for article in kept_articles:
        tag = normalize_article_tag(article.tag_article)

        if not is_article_usable_for_state_of_art(article):
            excluded_articles.append({
                "article_id": article.id,
                "title": article.title,
                "tag": tag,
                "consultant_status": article.consultant_status,
                "reason": (
                    "Article exclu de la rédaction : statut non gardé, tag non exploitable, "
                    "Hors sujet ou source technique."
                ),
            })
            continue

        source_json = dict(_as_dict(article.source_json))
        if (
            source_json.get("guided_research_source")
            and article.verrou_id is None
        ):
            if standalone_mode and standalone_verrous:
                target_ids = [
                    _safe_text(value, 120)
                    for value in _as_list(
                        source_json.get("target_verrous")
                        or source_json.get("covered_verrou_ids")
                    )
                    if _safe_text(value, 120) in standalone_verrous_by_id
                ]
                target_ids = target_ids or standalone_active_ids or list(
                    standalone_verrous_by_id
                )
                if not (
                    source_json.get("target_verrous")
                    or source_json.get("covered_verrou_ids")
                ):
                    source_json["target_verrous"] = target_ids
                    source_json["covered_verrou_ids"] = target_ids
                    source_json["guided_candidate_id"] = (
                        source_json.get("guided_candidate_id")
                        or (
                            f"UPLOAD-{int(article.id)}"
                            if source_json.get("manual_upload_source")
                            else f"GUIDED-{int(article.id)}"
                        )
                    )
                    article.source_json = source_json
                    db.add(article)
                    db.commit()
                    db.refresh(article)
                for target_id in target_ids:
                    standalone_verrou = standalone_verrous_by_id[target_id]
                    group_key = f"standalone::{target_id}"
                    if group_key not in groups:
                        groups[group_key] = {
                            "_db_verrou": None,
                            "_standalone_verrou": standalone_verrou,
                            "_articles": [],
                            "verrou_key": target_id,
                            "verrou_id": target_id,
                            "verrou_title": _safe_text(
                                standalone_verrou.get("title"), 2400
                            ),
                        }
                    groups[group_key]["_articles"].append(article)
                continue
            # Une publication guidée sans rattachement explicite reste
            # disponible dans les Article Cards comme contexte scientifique
            # transversal. Elle ne doit pas créer un faux verrou canonique.
            supplemental_guided_articles.append({
                "article_id": article.id,
                "title": article.title,
                "year": article.year,
                "source": article.source,
                "doi": article.doi,
                "url": article.url,
                "tag": tag,
                "guided_candidate_id": source_json.get("guided_candidate_id"),
                "consultant_evidence_role": source_json.get(
                    "consultant_evidence_role"
                ),
                "section_ids": list(source_json.get("section_ids") or []),
                "target_verrous": list(
                    source_json.get("target_verrous")
                    or source_json.get("covered_verrou_ids")
                    or []
                ),
                "usage": "transversal_article_card_without_new_verrou",
            })
            excluded_articles.append({
                "article_id": article.id,
                "title": article.title,
                "tag": tag,
                "consultant_status": article.consultant_status,
                "reason": (
                    "Source guidée transversale conservée dans les fiches "
                    "scientifiques, sans création de verrou non confirmé."
                ),
            })
            continue

        group_key = _verrou_key_from_article(article, verrous_by_id)

        if group_key not in groups:
            db_verrou = verrous_by_id.get(article.verrou_id) if article.verrou_id else None
            title = _verrou_title_from_article(article, verrous_by_id)

            groups[group_key] = {
                "_db_verrou": db_verrou,
                "_articles": [],
                "verrou_key": group_key,
                "verrou_id": article.verrou_id or group_key,
                "verrou_title": title,
            }

        groups[group_key]["_articles"].append(article)

    verrous_payload: List[Dict[str, Any]] = []
    total_direct = 0
    total_connexe = 0
    total_fondamental = 0
    total_selected_for_writer = 0
    warnings: List[str] = []

    if max_direct > 0 or max_connexe > 0 or max_fondamental > 0:
        warnings.append(
            "Attention : une limite d'articles est active pour le writer "
            f"(Direct={max_direct}, Connexe={max_connexe}, Fondamental={max_fondamental}). "
            "Mets ENNOSCHOLAR_WRITER_MAX_DIRECT/CONNEXE/FONDAMENTAL à 0 pour inclure tous les articles gardés."
        )

    for index, group in enumerate(groups.values(), start=1):
        db_verrou: Optional[Verrou] = group.get("_db_verrou")
        standalone_verrou = _as_dict(group.get("_standalone_verrou"))
        group_articles: List[Article] = _sort_articles_for_writer(group.get("_articles") or [])

        direct_articles, direct_overflow = _limit_articles_by_type(group_articles, "Direct", max_direct)
        connexe_articles, connexe_overflow = _limit_articles_by_type(group_articles, "Connexe", max_connexe)
        fondamental_articles, fondamental_overflow = _limit_articles_by_type(group_articles, "Fondamental", max_fondamental)

        total_direct += len(direct_articles)
        total_connexe += len(connexe_articles)
        total_fondamental += len(fondamental_articles)

        selected_for_writer = direct_articles + connexe_articles + fondamental_articles
        total_selected_for_writer += len(selected_for_writer)

        citation_counter = 1

        def convert_list(items: List[Article]) -> List[Dict[str, Any]]:
            nonlocal citation_counter

            out: List[Dict[str, Any]] = []

            for item in items:
                citation_id = f"A{citation_counter}"
                citation_counter += 1
                out.append(article_to_payload_item(item, citation_id))

            return out

        articles_directs = convert_list(direct_articles)
        articles_connexes = convert_list(connexe_articles)
        articles_fondamentaux = convert_list(fondamental_articles)

        overflow = direct_overflow + connexe_overflow + fondamental_overflow

        source_signals: List[str] = []
        for article in group_articles[:6]:
            source_signals.extend(_extract_source_signals(article, db_verrou))

        source_signals = _dedupe_keep_order(source_signals, 12)

        objectif_rnd = _extract_objectif_rnd(
            project=project,
            db_verrou=db_verrou,
            article_group=group_articles,
            diagnostic_context=diagnostic_context,
        )
        if standalone_verrou:
            objectif_rnd = _safe_text(
                standalone_brief.get("objective")
                or standalone_brief.get("project_objective")
                or standalone_verrou.get("justification"),
                3000,
            )

        contexte_projet = _extract_contexte_projet(
            project=project,
            db_verrou=db_verrou,
            article_group=group_articles,
            diagnostic_context=diagnostic_context,
        )
        if standalone_verrou:
            contexte_projet = _safe_text(
                standalone_project_context(standalone_verrou).get(
                    "contexte_technique"
                ),
                4000,
            )

        # Version structurée, exploitable par Phase 4.6 et Phase 5.
        # Elle utilise le rapport final EnnoDiagnostic, pas seulement le dump DB.
        project_context_structured = _build_project_context_structured(
            project=project,
            diagnostic_context=diagnostic_context,
            verrou_title=group.get("verrou_title") or "",
        )
        if standalone_verrou:
            project_context_structured = standalone_project_context(
                standalone_verrou
            )

        scientific_intent = {}
        for article in group_articles:
            intent = _article_scientific_intent(article)
            if intent:
                scientific_intent = intent
                break

        can_write_without_force = len(articles_directs) + len(articles_connexes) > 0

        if not can_write_without_force:
            warnings.append(
                f"{group.get('verrou_title')} : aucun article Direct ou Connexe gardé. "
                "La rédaction CIR serait faible sans force."
            )

        verrou_payload = {
            "verrou_index": index,
            "verrou_id": group.get("verrou_id"),
            "verrou_key": group.get("verrou_key"),
            "verrou_title": group.get("verrou_title"),

            "objectif_r&d": objectif_rnd,
            "objectif_rd": objectif_rnd,

            "contexte_projet": contexte_projet,
            "project_context_structured": project_context_structured,
            "verrou_context_structured": _build_verrou_context_structured(
                project=project,
                db_verrou=db_verrou,
                verrou_title=group.get("verrou_title") or "",
                objectif_rnd=objectif_rnd,
                diagnostic_context=diagnostic_context,
                source_signals=source_signals,
                project_context_structured=project_context_structured,
            ),
            "source_signals": source_signals,

            "scientific_intent": scientific_intent,

            "articles_directs": articles_directs,
            "articles_connexes": articles_connexes,
            "articles_fondamentaux": articles_fondamentaux,

            # Phase 3 : sera rempli par cir_style_memory.py.
            "memoire_style": [],
            "style_memory": [],
            "memoire_style_status": "non_active_phase_1",

            "contraintes_cir": _build_contraintes_cir(),

            "selection_policy": {
                "rule": "consultant_status == garde AND tag in Direct/Connexe/Fondamental AND not technical_catalog",
                "priority": "Direct + Connexe, Fondamental seulement pour contextualiser",
                "limit_mode": "0 = aucune limite ; >0 = top N par type et par verrou",
                "max_direct": max_direct,
                "max_connexe": max_connexe,
                "max_fondamental": max_fondamental,
                "can_write_without_force": can_write_without_force,
            },

            "counts": {
                "articles_total_kept_for_verrou": len(group_articles),
                "direct": len(articles_directs),
                "connexe": len(articles_connexes),
                "fondamental": len(articles_fondamentaux),
                "selected_for_writer": len(selected_for_writer),
                "excluded_by_limit": len(overflow),
            },

            "articles_exclus_par_limite": [
                {
                    "article_id": item.id,
                    "title": item.title,
                    "tag": normalize_article_tag(item.tag_article),
                    "score": item.score,
                    "year": item.year,
                }
                for item in overflow
            ],
        }

        verrous_payload.append(verrou_payload)

    payload = {
        "ok": True,
        "agent": "EnnoScholar",
        "phase": "phase_1_selection_payload",
        "payload_type": "state_of_art_selection_payload_v1",
        "payload_version": "v1_4_current_scholar_run_selection_only",
        "generated_at": datetime.utcnow().isoformat(),

        "project": {
            "id": project.id,
            "organisme": project.organisme,
            "project_name": project.project_name,
            "year": project.year,
            "domain_label": project.domain_label,
            "status": project.status,
        },

        # Compatibilité avec scholar_agent.run_writer_from_selection plus tard.
        "organisme": project.organisme,
        "project_name": project.project_name,
        "project": project.project_name,
        "year": project.year,
        "domain_label": project.domain_label,

        "latest_scholar_run": {
            "id": latest_run.id if latest_run else None,
            "status": latest_run.status if latest_run else None,
            "created_at": latest_run.created_at.isoformat() if latest_run and latest_run.created_at else None,
            "report_path": latest_run.report_path if latest_run else None,
        },

        "diagnostic_context": _public_diagnostic_context(diagnostic_context),
        "project_context_structured": project_context_global,

        "selection_summary": {
            "kept_articles_total": len(kept_articles),
            "usable_articles_total": total_selected_for_writer,
            "verrous_count": len(verrous_payload),
            "direct_articles": total_direct,
            "connexe_articles": total_connexe,
            "fondamental_articles": total_fondamental,
            "excluded_articles_count": len(excluded_articles),
            "excluded_by_limit_total": sum(
                int(v.get("counts", {}).get("excluded_by_limit") or 0)
                for v in verrous_payload
            ),
            "can_write_without_force": any(
                v.get("selection_policy", {}).get("can_write_without_force")
                for v in verrous_payload
            ),
        },

        "contraintes_cir_globales": _build_contraintes_cir(),

        "verrous": verrous_payload,

        "supplemental_guided_articles": supplemental_guided_articles,

        "excluded_articles": excluded_articles,

        "warnings": warnings,

        "next_phase": {
            "phase_2": "Générer les fiches articles enrichies : apport, méthode, résultat, limite projet, citation exploitable.",
            "phase_3": "Récupérer Memory V2 comme mémoire de style uniquement.",
            "phase_4": "Construire la matrice de gap scientifique.",
            "phase_4_6": "Construire l'argumentaire CIR du verrou à partir du contexte projet structuré.",
            "phase_5": "Rédiger l'état de l'art CIR par verrou avec citation_guard.",
        },
    }


        # Sauvegarde Phase 1
    output_dir = (
        storage_root()
        / "organismes"
        / _slugify_path(project.organisme)
        / "projects"
        / _slugify_path(project.project_name)
        / "years"
        / _slugify_path(project.year)
        / "ennoscholar"
        / "state_of_art_payload"
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "selection_payload.json"

    # Synchronisation AVANT l'écriture du nouveau payload afin de comparer
    # la sélection précédente avec la sélection courante.
    artifact_sync = synchronize_state_of_art_artifacts_with_selection(
        project=project,
        selection_payload=payload,
    )
    payload["artifact_sync"] = artifact_sync
    payload["payload_path"] = str(output_path)
    payload["saved"] = True

    output_path.write_text(
        json.dumps(_json_safe(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return _json_safe(payload)
