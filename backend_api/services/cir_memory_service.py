# -*- coding: utf-8 -*-
from __future__ import annotations

"""
cir_memory_service.py — EnnoSmart CIR Memory Builder V1

Objectif V1 :
- Construire une mémoire CIR multi-projets / multi-années par organisme.
- Séparer clairement :
  1) memory/validated : CIR finaux consultant validés
  2) memory/working   : sorties EnnoDiagnostic / EnnoScholar / textes générés
  3) memory/bibliography : articles scientifiques sélectionnés ou utilisés
- Créer un index global par organisme pour que les agents puissent rechercher
  dans tous les projets et toutes les années d'un même organisme.

Règle importante :
- Un CIR final validé peut servir de connaissance projet + style rédactionnel.
- Les sorties agents restent en working tant qu'elles ne sont pas validées.
- Les articles sont une mémoire bibliographique séparée, pas une mémoire de style.
"""

import json
import re
import unicodedata
import zipfile
import importlib
import os
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Any, Dict, List, Optional, Iterable, Tuple

from db.models import Project
from services.diagnostic_service import get_project_store, sanitize_json_value


# ---------------------------------------------------------------------------
# JSON / texte
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def read_json(path: str | Path, default: Any = None) -> Any:
    p = Path(path)
    if default is None:
        default = {}
    try:
        if not p.exists():
            return default
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: str | Path, data: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(sanitize_json_value(data), ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: str | Path, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(text or ""), encoding="utf-8")


def clean_text(value: Any, max_chars: int = 0) -> str:
    text = str(value or "")
    text = text.replace("\x00", " ")
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    if max_chars and len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + "..."
    return text


def flatten_text(value: Any, max_chars: int = 20000) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return clean_text(value, max_chars)
    if isinstance(value, dict):
        parts: List[str] = []
        for k, v in value.items():
            txt = flatten_text(v, max_chars=max_chars)
            if txt:
                parts.append(f"{k}: {txt}")
        return clean_text("\n".join(parts), max_chars)
    if isinstance(value, list):
        return clean_text("\n".join(flatten_text(x, max_chars=max_chars) for x in value), max_chars)
    return clean_text(str(value), max_chars)


def strip_accents(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def slugify(value: Any, default: str = "unknown") -> str:
    text = strip_accents(str(value or "").lower())
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or default


# ---------------------------------------------------------------------------
# Chemins mémoire
# ---------------------------------------------------------------------------

def _fallback_organism_dir(project: Project) -> Path:
    return Path(r"C:\EnnoSmart") / "storage" / "organismes" / slugify(project.organisme)


def find_organism_dir(project_dir: Path, project: Project) -> Path:
    """
    Retrouve le dossier organisme à partir du project_dir existant.

    Compatible avec :
    storage/organismes/<org>/projects/<project>/years/<year>
    ou d'autres variantes proches.
    """
    project_dir = Path(project_dir)

    for p in [project_dir] + list(project_dir.parents):
        if p.parent.name.lower() == "organismes":
            return p

    # Fallback robuste si le project_store ne suit pas encore cette structure.
    return _fallback_organism_dir(project)


def cir_memory_paths(project: Project) -> Dict[str, Path]:
    ps = get_project_store(project)
    project_dir = Path(ps.project_dir)
    organism_dir = find_organism_dir(project_dir, project)

    memory_dir = project_dir / "memory"
    validated_dir = memory_dir / "validated"
    working_dir = memory_dir / "working"
    bibliography_dir = memory_dir / "bibliography"

    cir_final_dir = project_dir / "cir_final"
    cir_final_consultant_dir = project_dir / "cir_final_consultant"

    organism_memory_dir = organism_dir / "memory"
    organism_validated_dir = organism_memory_dir / "validated"
    organism_working_dir = organism_memory_dir / "working"
    organism_bibliography_dir = organism_memory_dir / "bibliography"

    return {
        "project_dir": project_dir,
        "organism_dir": organism_dir,
        "memory_dir": memory_dir,

        "validated_dir": validated_dir,
        "working_dir": working_dir,
        "bibliography_dir": bibliography_dir,

        "cir_final_dir": cir_final_dir,
        "cir_final_consultant_dir": cir_final_consultant_dir,

        "validated_knowledge": validated_dir / "knowledge.json",
        "validated_style": validated_dir / "style.json",
        "validated_chunks": validated_dir / "chunks.json",

        "working_diagnostic": working_dir / "diagnostic_memory.json",
        "working_scholar": working_dir / "scholar_memory.json",
        "working_generated_state_art": working_dir / "generated_state_of_art.json",
        "working_chunks": working_dir / "working_chunks.json",

        "bibliography_memory": bibliography_dir / "bibliography_memory.json",

        "organism_validated_dir": organism_validated_dir,
        "organism_working_dir": organism_working_dir,
        "organism_bibliography_dir": organism_bibliography_dir,

        "organism_knowledge_index": organism_validated_dir / "organism_knowledge_index.json",
        "organism_style_index": organism_validated_dir / "organism_style_index.json",
        "organism_chunks_index": organism_validated_dir / "organism_chunks_index.json",

        "organism_working_index": organism_working_dir / "organism_working_index.json",
        "organism_articles_index": organism_bibliography_dir / "organism_articles_index.json",
        "organism_global_search_index": organism_memory_dir / "organism_global_search_index.json",
    }


def ensure_memory_dirs(project: Project) -> Dict[str, Path]:
    paths = cir_memory_paths(project)
    for key in [
        "validated_dir", "working_dir", "bibliography_dir",
        "cir_final_dir",
        "organism_validated_dir", "organism_working_dir", "organism_bibliography_dir",
    ]:
        paths[key].mkdir(parents=True, exist_ok=True)
    return paths


# ---------------------------------------------------------------------------
# Extraction texte CIR final
# ---------------------------------------------------------------------------

def extract_text_from_docx(path: str | Path) -> str:
    """
    Extraction DOCX sans dépendance python-docx.
    Lit word/document.xml et récupère les paragraphes.
    """
    p = Path(path)
    paragraphs: List[str] = []

    with zipfile.ZipFile(p) as z:
        names = set(z.namelist())
        main_xml = "word/document.xml"
        if main_xml not in names:
            return ""

        xml = z.read(main_xml).decode("utf-8", errors="ignore")

        # Ajouter des retours entre paragraphes.
        xml = xml.replace("</w:p>", "</w:p>\n")

        for para_xml in re.findall(r"<w:p[\s\S]*?</w:p>", xml):
            texts = re.findall(r"<w:t[^>]*>([\s\S]*?)</w:t>", para_xml)
            if not texts:
                continue
            para = "".join(unescape(t) for t in texts)
            para = clean_text(para)
            if para:
                paragraphs.append(para)

    return clean_text("\n".join(paragraphs))


def extract_text_from_pdf(path: str | Path) -> str:
    """
    Extraction PDF simple si pypdf/PyPDF2 est installé.
    Pas d'OCR en V1.
    """
    p = Path(path)

    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        try:
            from PyPDF2 import PdfReader  # type: ignore
        except Exception as exc:
            raise RuntimeError(
                "Extraction PDF impossible : installe pypdf ou PyPDF2, ou utilise DOCX pour la V1."
            ) from exc

    reader = PdfReader(str(p))
    pages: List[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            continue

    return clean_text("\n\n".join(pages))


def extract_text_from_file(path: str | Path) -> str:
    p = Path(path)
    suffix = p.suffix.lower()

    if suffix == ".docx":
        return extract_text_from_docx(p)

    if suffix == ".pdf":
        return extract_text_from_pdf(p)

    if suffix in {".txt", ".md"}:
        return clean_text(p.read_text(encoding="utf-8", errors="ignore"))

    raise RuntimeError(
        f"Format non supporté en V1 pour la mémoire CIR : {suffix}. "
        "Utilise .docx, .pdf ou .txt."
    )


def find_latest_cir_final_file(project: Project) -> Optional[Path]:
    """
    Cherche un CIR final déjà présent dans plusieurs emplacements possibles.
    Compatible avec tes anciens dossiers cir_final_consultant/current.
    """
    paths = ensure_memory_dirs(project)

    candidates_dirs = [
        paths["cir_final_dir"],
        paths["cir_final_consultant_dir"],
        paths["cir_final_consultant_dir"] / "current",
        paths["project_dir"] / "cir_final_consultant" / "current",
        paths["project_dir"] / "cir_final_consultant",
    ]

    allowed = {".docx", ".pdf", ".txt", ".md"}
    files: List[Path] = []
    for d in candidates_dirs:
        if not d.exists():
            continue
        for f in d.rglob("*"):
            if f.is_file() and f.suffix.lower() in allowed:
                files.append(f)

    if not files:
        return None

    files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    return files[0]


# ---------------------------------------------------------------------------
# Découpage sections / rôles
# ---------------------------------------------------------------------------

ROLE_PATTERNS: Dict[str, List[str]] = {
    "etat_art": [
        r"état\s+de\s+l[’']art", r"etat\s+de\s+l[’']art",
        r"bibliograph", r"art\s+antérieur", r"art\s+anterieur",
        r"travaux\s+existants", r"connaissances\s+existantes",
    ],
    "objectif": [
        r"objectif", r"contexte\s+du\s+projet", r"présentation\s+du\s+projet",
        r"presentation\s+du\s+projet", r"enjeux",
    ],
    "verrou": [
        r"verrou", r"incertitude", r"difficulté\s+technique", r"difficulte\s+technique",
        r"problématique\s+scientifique", r"problematique\s+scientifique",
        r"risque\s+technique",
    ],
    "demarche": [
        r"démarche", r"demarche", r"méthodologie", r"methodologie",
        r"travaux\s+réalisés", r"travaux\s+realises", r"essais",
        r"protocole", r"expériment", r"experiment", r"simulation",
    ],
    "resultat": [
        r"résultat", r"resultat", r"résultats", r"resultats",
        r"bilan", r"performances\s+obtenues", r"validation",
    ],
    "conclusion": [
        r"conclusion", r"perspectives", r"synthèse", r"synthese",
        r"apport\s+r&d", r"apport\s+scientifique",
    ],
    "moyens": [
        r"moyens", r"ressources", r"équipe", r"equipe", r"matériel", r"materiel",
        r"partenaires", r"sous-traitance", r"sous\s+traitance",
    ],
}


def classify_role(title: str = "", text: str = "") -> str:
    hay = strip_accents(f"{title}\n{text}".lower())
    for role, patterns in ROLE_PATTERNS.items():
        for pat in patterns:
            if re.search(strip_accents(pat.lower()), hay, flags=re.I):
                return role
    return "autre"


def looks_like_heading(line: str) -> bool:
    line = clean_text(line)
    if not line:
        return False

    if len(line) > 160:
        return False

    norm = strip_accents(line.lower())

    # Titres numérotés : 1. / 1.2 / I. / A.
    if re.match(r"^(\d+(\.\d+)*|[ivxlcdm]+|[a-z])[\.\)]\s+.{3,}$", norm, flags=re.I):
        return True

    # Titres connus par rôle
    if classify_role(line, "") != "autre":
        return True

    # Ligne courte majoritairement en majuscules
    letters = re.sub(r"[^A-Za-zÀ-ÿ]", "", line)
    if len(letters) >= 6:
        upper_ratio = sum(1 for c in letters if c.isupper()) / max(1, len(letters))
        if upper_ratio > 0.65:
            return True

    return False


def split_cir_sections(text: str) -> List[Dict[str, Any]]:
    text = clean_text(text)
    if not text:
        return []

    lines = [clean_text(x) for x in text.split("\n")]
    lines = [x for x in lines if x]

    sections: List[Dict[str, Any]] = []
    current_title = "Introduction / contenu non titré"
    current_lines: List[str] = []

    def flush() -> None:
        nonlocal current_title, current_lines
        content = clean_text("\n".join(current_lines))
        if content:
            role = classify_role(current_title, content)
            sections.append({
                "section_id": f"S{len(sections) + 1:03d}",
                "section_title": current_title,
                "role": role,
                "text": content,
                "char_count": len(content),
            })
        current_lines = []

    for line in lines:
        if looks_like_heading(line):
            if current_lines:
                flush()
            current_title = line
        else:
            current_lines.append(line)

    if current_lines:
        flush()

    if not sections:
        sections.append({
            "section_id": "S001",
            "section_title": "Document complet",
            "role": classify_role("", text),
            "text": text,
            "char_count": len(text),
        })

    return sections


def chunk_text(text: str, max_chars: int = 1400, overlap: int = 120) -> List[str]:
    text = clean_text(text)
    if not text:
        return []

    if len(text) <= max_chars:
        return [text]

    sentences = re.split(r"(?<=[.!?;:])\s+", text)
    chunks: List[str] = []
    current = ""

    for sent in sentences:
        sent = clean_text(sent)
        if not sent:
            continue
        if len(current) + len(sent) + 1 <= max_chars:
            current = (current + " " + sent).strip()
        else:
            if current:
                chunks.append(current)
            tail = current[-overlap:] if overlap and current else ""
            current = clean_text(tail + " " + sent)

    if current:
        chunks.append(current)

    return chunks


def build_chunks_from_sections(
    sections: List[Dict[str, Any]],
    project: Project,
    source_file: str,
    memory_status: str,
    source_type: str,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    org_slug = slugify(project.organisme)
    project_slug = slugify(project.project_name)
    year = str(project.year or "")

    for section in sections:
        role = section.get("role") or "autre"
        pieces = chunk_text(section.get("text") or "")
        for i, piece in enumerate(pieces, start=1):
            chunk_id = f"{org_slug}_{project_slug}_{year}_{role}_{section.get('section_id')}_{i:03d}"
            out.append({
                "chunk_id": chunk_id,
                "organisme": project.organisme,
                "organisme_slug": org_slug,
                "project": project.project_name,
                "project_slug": project_slug,
                "project_id": project.id,
                "year": year,
                "role": role,
                "section_id": section.get("section_id"),
                "section_title": section.get("section_title"),
                "text": piece,
                "source_file": source_file,
                "source_type": source_type,
                "memory_status": memory_status,
                "confidence": "high" if memory_status == "validated" else "medium",
                "requires_validation": memory_status != "validated",
                "created_at": now_iso(),
            })
    return out



# ---------------------------------------------------------------------------
# Intégration du pipeline NLP CIR existant
# ---------------------------------------------------------------------------


def _norm(text: Any) -> str:
    text = strip_accents(str(text or "").lower())
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def _load_existing_cir_pipeline():
    """
    Charge ton pipeline CIR existant sans créer un deuxième module métier.

    Le chemin peut être forcé dans .env :
        ENNOSMART_CIR_NLP_MODULE=modules.NLP.cir_pipeline

    Le module doit exposer :
        run_cir_pipeline(documents)
    ou :
        run_pipeline(documents)
    """
    candidates: List[str] = []

    env_module = os.getenv("ENNOSMART_CIR_NLP_MODULE", "").strip()
    if env_module:
        candidates.append(env_module)

    candidates.extend([
        "modules.NLP.cir_pipeline",
        "modules.nlp.cir_pipeline",
        "NLP.cir_pipeline",
        "nlp.cir_pipeline",
        "agents.EnnoDiagnostic.NLP.cir_pipeline",
        "agents.EnnoDiagnostic.nlp.cir_pipeline",
        "agents.NLP.cir_pipeline",
        "ennodiagnostic.NLP.cir_pipeline",
        "ennodiagnostic.nlp.cir_pipeline",
        "cir_pipeline",
    ])

    errors: List[str] = []

    for module_name in candidates:
        try:
            module = importlib.import_module(module_name)
            runner = getattr(module, "run_cir_pipeline", None) or getattr(module, "run_pipeline", None)
            if callable(runner):
                return runner, module_name, errors
        except Exception as exc:
            errors.append(f"{module_name}: {exc}")

    return None, None, errors


def _pipeline_section_role(section: Dict[str, Any]) -> str:
    """
    Convertit les rôles/types du pipeline CIR vers les rôles mémoire.
    """
    role = str(section.get("role") or "").strip().lower()
    section_type = str(section.get("section_type") or "").strip().lower()

    if role in {"objectif", "verrou", "etat_art", "resultat", "conclusion", "limite", "administratif", "annexe"}:
        return role

    if role in {"methode", "méthode"}:
        return "demarche"

    if role == "contribution":
        return "conclusion"

    mapping = {
        "project_title": "project_title",
        "contexte": "objectif",
        "objectifs": "objectif",
        "etat_art": "etat_art",
        "limites_etat_art": "limite",
        "verrous": "verrou",
        "methodes_travaux": "demarche",
        "resultats": "resultat",
        "contribution": "conclusion",
        "annexe": "annexe",
        "administratif": "administratif",
    }

    return mapping.get(section_type, "autre")


def _pipeline_section_label(section_type: str) -> str:
    return {
        "project_title": "Intitulé / fiche projet",
        "contexte": "Contexte du projet",
        "objectifs": "Objectifs du projet",
        "etat_art": "État de l'art",
        "limites_etat_art": "Insuffisances de l'état de l'art",
        "verrous": "Verrous et incertitudes R&D",
        "methodes_travaux": "Démarche expérimentale / travaux R&D",
        "resultats": "Résultats / essais / simulations",
        "contribution": "Contribution scientifique, technique ou technologique",
        "annexe": "Annexe",
        "administratif": "Administratif / RH / indicateurs",
    }.get(str(section_type or ""), "Section CIR")


def _find_char_span(full_text: str, section_text: str, cursor: int = 0) -> Tuple[Optional[int], Optional[int]]:
    """
    Traçabilité simple : retrouve la position caractère d'une section dans le texte extrait.
    Pour DOCX/PDF, c'est une traçabilité texte, pas encore une coordonnée visuelle page.
    """
    full = str(full_text or "")
    sec = clean_text(section_text)

    if not full or not sec:
        return None, None

    probe = sec[:250].strip()
    if len(probe) < 30:
        return None, None

    pos = full.find(probe, max(0, cursor))
    if pos < 0:
        pos = full.find(probe)

    if pos < 0:
        return None, None

    return pos, min(len(full), pos + len(sec))


def _normalize_cir_pipeline_sections(
    pipeline_report: Dict[str, Any],
    full_text: str,
    document_name: str,
    source_path: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Transforme les sections de ton cir_pipeline en sections compatibles mémoire.
    """
    raw_sections = pipeline_report.get("sections") or []
    sections: List[Dict[str, Any]] = []
    source_sections: List[Dict[str, Any]] = []
    cursor = 0

    for idx, raw in enumerate(raw_sections, start=1):
        if not isinstance(raw, dict):
            continue

        title = clean_text(raw.get("title"), 500)
        text = clean_text(raw.get("text"))
        if len(text) < 40:
            continue

        section_type = clean_text(raw.get("section_type") or "unknown", 120)
        role = _pipeline_section_role(raw)

        # Les annexes courtes ne servent pas à la mémoire utile.
        if role == "annexe" and len(text) < 300:
            continue

        section_id = clean_text(raw.get("section_id"), 120) or f"S{idx:03d}"
        label = clean_text(raw.get("section_label"), 160) or _pipeline_section_label(section_type)

        final_text = text
        if title and not _norm(text).startswith(_norm(title)[:25]):
            final_text = f"{title}\n{text}".strip()

        start_char, end_char = _find_char_span(full_text, text, cursor=cursor)
        if start_char is not None:
            cursor = start_char + 1

        section = {
            "section_id": section_id,
            "section_title": title or label,
            "title": title or label,
            "role": role,
            "text": final_text,
            "section_type": section_type,
            "section_label": label,
            "level": raw.get("level") or 1,
            "page": raw.get("page"),
            "confidence": float(pipeline_report.get("confidence") or 1.0),
            "document": document_name,
            "source_path": source_path,
            "source_type": "cir_final_consultant",
            "memory_status": "validated",
            "content_origin": "cir_pipeline",
        }

        sections.append(section)
        source_sections.append({
            "section_id": section_id,
            "title": title or label,
            "role": role,
            "section_type": section_type,
            "section_label": label,
            "level": raw.get("level") or 1,
            "page": raw.get("page"),
            "start_char": start_char,
            "end_char": end_char,
            "text_chars": len(final_text),
        })

    source_map = {
        "source_file": document_name,
        "source_path": source_path,
        "source_type": "cir_final_consultant",
        "content_origin": "cir_pipeline",
        "sections": source_sections,
    }

    return sections, source_map


def _pack_items_to_memory(
    pack: Dict[str, Any],
    pack_key: str,
    source_file: str,
    max_items: int = 80,
) -> List[Dict[str, Any]]:
    arr = pack.get(pack_key) or []
    if not isinstance(arr, list):
        return []

    out: List[Dict[str, Any]] = []

    for item in arr[:max_items]:
        if not isinstance(item, dict):
            continue

        out.append({
            "passage_id": item.get("passage_id"),
            "section_id": item.get("section_id"),
            "section_title": item.get("section_title"),
            "section_type": item.get("section_type"),
            "section_label": item.get("section_label"),
            "role": item.get("role"),
            "text": clean_text(item.get("text"), 3000),
            "source_file": source_file,
            "source_path": item.get("source_path"),
            "confidence": item.get("confidence"),
            "model_confidence": item.get("model_confidence"),
            "quality_status": item.get("quality_status"),
            "accepted_for_synthesis": item.get("accepted_for_synthesis"),
            "needs_human_validation": item.get("needs_human_validation"),
            "content_origin": "cir_pipeline",
            "memory_status": "validated",
        })

    return out


def _build_knowledge_from_cir_pipeline(
    project: Project,
    pipeline_report: Dict[str, Any],
    source_file: str,
) -> Dict[str, Any]:
    pack = (
        pipeline_report.get("evidence_pack_for_ennodiagnostic")
        or pipeline_report.get("evidence_pack_before_frascati")
        or {}
    )

    return {
        "organisme": project.organisme,
        "project": project.project_name,
        "project_id": project.id,
        "year": str(project.year or ""),
        "memory_type": "validated_knowledge",
        "memory_status": "validated",
        "source_type": "cir_final_consultant",
        "content_origin": "cir_pipeline",
        "source_file": source_file,
        "created_at": now_iso(),
        "objectifs": _pack_items_to_memory(pack, "objectifs_locaux", source_file),
        "verrous": _pack_items_to_memory(pack, "verrous_rnd_locaux", source_file),
        "demarches": _pack_items_to_memory(pack, "methodes_locales", source_file),
        "resultats": _pack_items_to_memory(pack, "resultats_locaux", source_file),
        "etat_art_points": _pack_items_to_memory(pack, "etat_art_local", source_file),
        "limites": _pack_items_to_memory(pack, "limites_locales", source_file),
        "conclusions": _pack_items_to_memory(pack, "contributions_locales", source_file),
        "parametres": _pack_items_to_memory(pack, "parametres_locaux", source_file),
        "moyens": [],
        "autres": [],
        "cir_pipeline_stats": pipeline_report.get("stats") or {},
        "detection_reports": pipeline_report.get("detection_reports") or [],
        "frascati_guard": pipeline_report.get("frascati_guard"),
    }


def _build_style_from_cir_sections(
    project: Project,
    sections: List[Dict[str, Any]],
    source_file: str,
) -> Dict[str, Any]:
    allowed_roles = {"etat_art", "verrou", "demarche", "resultat", "conclusion", "limite"}
    examples: List[Dict[str, Any]] = []

    for sec in sections:
        role = sec.get("role") or "autre"
        text = clean_text(sec.get("text"), 2200)

        if role not in allowed_roles:
            continue

        if len(text) < 180:
            continue

        tags = ["CIR", role, "validated_style"]
        nt = _norm(text)

        if any(x in nt for x in ["toutefois", "cependant", "ne permet pas", "limite", "insuffisance"]):
            tags.append("prudent")

        if any(x in nt for x in ["verrou", "incertitude", "difficulte"]):
            tags.append("incertitude_rnd")

        if any(x in nt for x in ["essai", "simulation", "prototype", "mesure"]):
            tags.append("demonstration_technique")

        examples.append({
            "example_id": f"style_{slugify(source_file)}_{slugify(sec.get('section_id'))}",
            "role": role,
            "section_id": sec.get("section_id"),
            "section_title": sec.get("section_title"),
            "section_type": sec.get("section_type"),
            "section_label": sec.get("section_label"),
            "text": text,
            "source_file": source_file,
            "organisme": project.organisme,
            "project": project.project_name,
            "project_id": project.id,
            "year": str(project.year or ""),
            "memory_status": "validated",
            "source_type": "cir_final_consultant",
            "content_origin": "cir_pipeline",
            "style_tags": sorted(set(tags)),
            "warning": "STYLE UNIQUEMENT : ne pas utiliser comme preuve factuelle du dossier courant.",
        })

    return {
        "organisme": project.organisme,
        "project": project.project_name,
        "project_id": project.id,
        "year": str(project.year or ""),
        "memory_type": "validated_style",
        "memory_status": "validated",
        "source_type": "cir_final_consultant",
        "content_origin": "cir_pipeline",
        "source_file": source_file,
        "created_at": now_iso(),
        "examples": examples[:100],
    }


def _run_existing_cir_pipeline_for_memory(
    project: Project,
    text: str,
    cir_path: Path,
) -> Dict[str, Any]:
    """
    Lance directement ton cir_pipeline existant après extraction texte.
    Aucun adaptateur séparé nécessaire.
    """
    runner, module_name, errors = _load_existing_cir_pipeline()

    if not callable(runner):
        return {
            "ok": False,
            "reason": "Pipeline NLP CIR introuvable.",
            "import_errors": errors,
            "hint": "Ajoute ENNOSMART_CIR_NLP_MODULE=chemin.du.module.cir_pipeline dans .env",
        }

    doc = {
        "document": cir_path.name,
        "file_name": cir_path.name,
        "source_path": str(cir_path),
        "text": text,
    }

    try:
        report = runner([doc])
    except Exception as exc:
        return {
            "ok": False,
            "reason": f"Erreur pendant run_cir_pipeline : {exc}",
            "module": module_name,
        }

    if not isinstance(report, dict):
        return {
            "ok": False,
            "reason": "run_cir_pipeline n'a pas retourné un dictionnaire.",
            "module": module_name,
        }

    detection_reports = report.get("detection_reports") or []
    is_structured = any(
        bool(r.get("is_cir_structured"))
        for r in detection_reports
        if isinstance(r, dict)
    )

    raw_sections = report.get("sections") or []

    if not is_structured or not raw_sections:
        return {
            "ok": False,
            "reason": "Le pipeline n'a pas reconnu ce fichier comme CIR structuré.",
            "module": module_name,
            "pipeline_report": report,
            "detection_reports": detection_reports,
            "stats": report.get("stats") or {},
        }

    sections, source_map = _normalize_cir_pipeline_sections(
        pipeline_report=report,
        full_text=text,
        document_name=cir_path.name,
        source_path=str(cir_path),
    )

    if not sections:
        return {
            "ok": False,
            "reason": "CIR reconnu, mais aucune section exploitable après normalisation.",
            "module": module_name,
            "pipeline_report": report,
            "detection_reports": detection_reports,
            "stats": report.get("stats") or {},
        }

    knowledge = _build_knowledge_from_cir_pipeline(
        project=project,
        pipeline_report=report,
        source_file=cir_path.name,
    )

    style = _build_style_from_cir_sections(
        project=project,
        sections=sections,
        source_file=cir_path.name,
    )

    metadata = {
        "ok": True,
        "module": module_name,
        "pipeline_version": report.get("version"),
        "pipeline_type": report.get("pipeline_type"),
        "content_origin": "cir_pipeline",
        "source_file": cir_path.name,
        "source_path": str(cir_path),
        "stats": report.get("stats") or {},
        "detection_reports": detection_reports,
        "outline_by_document": report.get("outline_by_document") or [],
        "sections_count": len(sections),
        "style_examples_count": len(style.get("examples") or []),
        "memory_status": "validated",
        "source_type": "cir_final_consultant",
        "created_at": now_iso(),
    }

    return {
        "ok": True,
        "module": module_name,
        "pipeline_report": report,
        "sections": sections,
        "source_map": source_map,
        "knowledge": knowledge,
        "style": style,
        "metadata": metadata,
    }

# ---------------------------------------------------------------------------
# Mémoire validée depuis CIR final
# ---------------------------------------------------------------------------

def build_knowledge_from_chunks(project: Project, chunks: List[Dict[str, Any]], source_file: str) -> Dict[str, Any]:
    by_role: Dict[str, List[Dict[str, Any]]] = {}
    for c in chunks:
        by_role.setdefault(c.get("role") or "autre", []).append(c)

    def items(role: str, max_items: int = 30) -> List[Dict[str, Any]]:
        arr = []
        for c in by_role.get(role, [])[:max_items]:
            arr.append({
                "chunk_id": c.get("chunk_id"),
                "section_title": c.get("section_title"),
                "text": clean_text(c.get("text"), 1600),
                "source_file": source_file,
                "year": str(project.year or ""),
                "project": project.project_name,
            })
        return arr

    return {
        "organisme": project.organisme,
        "project": project.project_name,
        "project_id": project.id,
        "year": str(project.year or ""),
        "memory_type": "validated_knowledge",
        "memory_status": "validated",
        "source_type": "cir_final_consultant",
        "source_file": source_file,
        "created_at": now_iso(),
        "objectifs": items("objectif"),
        "verrous": items("verrou"),
        "demarches": items("demarche"),
        "resultats": items("resultat"),
        "etat_art_points": items("etat_art"),
        "conclusions": items("conclusion"),
        "moyens": items("moyens"),
        "autres": items("autre", max_items=15),
    }


def build_style_from_chunks(project: Project, chunks: List[Dict[str, Any]], source_file: str) -> Dict[str, Any]:
    style_roles = {"etat_art", "verrou", "demarche", "resultat", "conclusion"}
    examples: List[Dict[str, Any]] = []

    for c in chunks:
        role = c.get("role") or "autre"
        txt = clean_text(c.get("text"), 1800)
        if role not in style_roles:
            continue
        if len(txt) < 180:
            continue

        examples.append({
            "example_id": c.get("chunk_id"),
            "role": role,
            "section_title": c.get("section_title"),
            "text": txt,
            "source_file": source_file,
            "organisme": project.organisme,
            "project": project.project_name,
            "project_id": project.id,
            "year": str(project.year or ""),
            "memory_status": "validated",
            "source_type": "cir_final_consultant",
            "style_tags": _style_tags_for_role(role, txt),
            "warning": "STYLE UNIQUEMENT : ne pas utiliser comme preuve factuelle du dossier courant.",
        })

    return {
        "organisme": project.organisme,
        "project": project.project_name,
        "project_id": project.id,
        "year": str(project.year or ""),
        "memory_type": "validated_style",
        "memory_status": "validated",
        "source_type": "cir_final_consultant",
        "source_file": source_file,
        "created_at": now_iso(),
        "examples": examples[:80],
    }


def _style_tags_for_role(role: str, text: str) -> List[str]:
    tags = ["CIR"]
    nt = strip_accents(text.lower())
    if role == "etat_art":
        tags += ["etat_art", "gap_etat_art", "prudent"]
    if role == "verrou":
        tags += ["incertitude", "verrou", "validation"]
    if role == "demarche":
        tags += ["demarche", "essais", "methodologie"]
    if role == "resultat":
        tags += ["resultats", "preuves", "mesures"]
    if role == "conclusion":
        tags += ["conclusion", "apport_rnd"]

    if any(w in nt for w in ["toutefois", "cependant", "ne permet pas", "limite"]):
        tags.append("prudence")
    if any(w in nt for w in ["justifie", "necessite", "necessaire"]):
        tags.append("justification_rnd")

    # Déduplication
    out = []
    for t in tags:
        if t not in out:
            out.append(t)
    return out


def build_validated_memory_from_cir_final(
    project: Project,
    cir_file_path: str | Path | None = None,
    rebuild_index: bool = True,
) -> Dict[str, Any]:
    """
    Construit memory/validated à partir d'un CIR final consultant.

    Traitement réel :
    1. upload/stockage du fichier
    2. extraction texte
    3. appel direct de ton pipeline NLP CIR existant : run_cir_pipeline()
    4. génération :
       - knowledge.json
       - style.json
       - chunks.json
       - sections.json
       - source_map.json
       - memory_metadata.json
    5. rebuild index organisme

    Si le pipeline CIR n'est pas trouvé ou ne reconnaît pas le document,
    le service garde un fallback simple pour ne pas bloquer l'import.
    """
    paths = ensure_memory_dirs(project)

    cir_path = Path(cir_file_path) if cir_file_path else find_latest_cir_final_file(project)
    if not cir_path or not cir_path.exists():
        return {
            "ok": False,
            "reason": "Aucun CIR final consultant trouvé pour ce projet.",
            "searched_dirs": [
                str(paths["cir_final_dir"]),
                str(paths["cir_final_consultant_dir"]),
                str(paths["cir_final_consultant_dir"] / "current"),
            ],
        }

    text = extract_text_from_file(cir_path)
    text = clean_text(text)

    if len(text) < 200:
        return {
            "ok": False,
            "reason": "Texte extrait trop court. Vérifie le fichier CIR final.",
            "file": str(cir_path),
            "file_name": cir_path.name,
            "text_chars": len(text),
        }

    extracted_text_path = paths["cir_final_dir"] / "extracted_text.txt"
    write_text(extracted_text_path, text)

    # -----------------------------------------------------------------------
    # 1) Traitement prioritaire par ton pipeline NLP CIR existant
    # -----------------------------------------------------------------------
    nlp_result = _run_existing_cir_pipeline_for_memory(
        project=project,
        text=text,
        cir_path=cir_path,
    )

    if nlp_result.get("ok"):
        sections = nlp_result.get("sections") or []
        knowledge = nlp_result.get("knowledge") or {}
        style = nlp_result.get("style") or {}
        source_map = nlp_result.get("source_map") or {}
        memory_metadata = nlp_result.get("metadata") or {}

        chunks = build_chunks_from_sections(
            sections=sections,
            project=project,
            source_file=cir_path.name,
            memory_status="validated",
            source_type="cir_final_consultant",
        )

        for chunk in chunks:
            chunk["content_origin"] = "cir_pipeline"
            chunk["nlp_pipeline_used"] = True
            chunk["can_use_as_fact"] = True
            chunk["can_use_as_style"] = True

        knowledge["created_at"] = now_iso()
        knowledge["chunks_count"] = len(chunks)

        style["created_at"] = now_iso()
        style["examples_count"] = len(style.get("examples") or [])

        sections_json = {
            "organisme": project.organisme,
            "project": project.project_name,
            "project_id": project.id,
            "year": str(project.year or ""),
            "memory_status": "validated",
            "source_type": "cir_final_consultant",
            "content_origin": "cir_pipeline",
            "source_file": cir_path.name,
            "source_path": str(cir_path),
            "sections_count": len(sections),
            "sections": sections,
            "created_at": now_iso(),
        }

        memory_metadata.update({
            "ok": True,
            "text_chars": len(text),
            "chunks_count": len(chunks),
            "extracted_text": str(extracted_text_path),
            "created_at": now_iso(),
        })

        write_json(paths["validated_knowledge"], knowledge)
        write_json(paths["validated_style"], style)
        write_json(paths["validated_chunks"], chunks)
        write_json(paths["validated_dir"] / "sections.json", sections_json)
        write_json(paths["validated_dir"] / "source_map.json", source_map)
        write_json(paths["validated_dir"] / "memory_metadata.json", memory_metadata)

        index_result = None
        if rebuild_index:
            index_result = rebuild_organism_memory_index(project)

        return {
            "ok": True,
            "memory_status": "validated",
            "source_type": "cir_final_consultant",
            "content_origin": "cir_pipeline",
            "nlp_pipeline_used": True,
            "nlp_module": nlp_result.get("module"),
            "file": str(cir_path),
            "file_name": cir_path.name,
            "extracted_text": str(extracted_text_path),
            "text_chars": len(text),
            "sections_count": len(sections),
            "chunks_count": len(chunks),
            "style_examples_count": len(style.get("examples") or []),
            "outputs": {
                "knowledge": str(paths["validated_knowledge"]),
                "style": str(paths["validated_style"]),
                "chunks": str(paths["validated_chunks"]),
                "sections": str(paths["validated_dir"] / "sections.json"),
                "source_map": str(paths["validated_dir"] / "source_map.json"),
                "memory_metadata": str(paths["validated_dir"] / "memory_metadata.json"),
            },
            "nlp": memory_metadata,
            "index_result": index_result,
        }

    # -----------------------------------------------------------------------
    # 2) Fallback simple si le pipeline CIR n'est pas disponible.
    # -----------------------------------------------------------------------
    sections = split_cir_sections(text)
    chunks = build_chunks_from_sections(
        sections=sections,
        project=project,
        source_file=cir_path.name,
        memory_status="validated",
        source_type="cir_final_consultant",
    )

    for chunk in chunks:
        chunk["content_origin"] = "simple_memory_fallback"
        chunk["nlp_pipeline_used"] = False
        chunk["can_use_as_fact"] = True
        chunk["can_use_as_style"] = True

    knowledge = build_knowledge_from_chunks(project, chunks, source_file=cir_path.name)
    style = build_style_from_chunks(project, chunks, source_file=cir_path.name)

    knowledge["content_origin"] = "simple_memory_fallback"
    knowledge["nlp_pipeline_used"] = False
    knowledge["nlp_pipeline_error"] = nlp_result

    style["content_origin"] = "simple_memory_fallback"
    style["nlp_pipeline_used"] = False

    sections_json = {
        "organisme": project.organisme,
        "project": project.project_name,
        "project_id": project.id,
        "year": str(project.year or ""),
        "memory_status": "validated",
        "source_type": "cir_final_consultant",
        "content_origin": "simple_memory_fallback",
        "source_file": cir_path.name,
        "source_path": str(cir_path),
        "sections_count": len(sections),
        "sections": sections,
        "created_at": now_iso(),
    }

    source_map = {
        "source_file": cir_path.name,
        "source_path": str(cir_path),
        "source_type": "cir_final_consultant",
        "content_origin": "simple_memory_fallback",
        "sections": [
            {
                "section_id": section.get("section_id"),
                "title": section.get("section_title") or section.get("title"),
                "role": section.get("role"),
                "text_chars": len(str(section.get("text") or "")),
            }
            for section in sections
        ],
    }

    memory_metadata = {
        "ok": True,
        "content_origin": "simple_memory_fallback",
        "nlp_pipeline_used": False,
        "nlp_pipeline_error": nlp_result,
        "source_file": cir_path.name,
        "source_path": str(cir_path),
        "text_chars": len(text),
        "sections_count": len(sections),
        "chunks_count": len(chunks),
        "extracted_text": str(extracted_text_path),
        "created_at": now_iso(),
    }

    write_json(paths["validated_knowledge"], knowledge)
    write_json(paths["validated_style"], style)
    write_json(paths["validated_chunks"], chunks)
    write_json(paths["validated_dir"] / "sections.json", sections_json)
    write_json(paths["validated_dir"] / "source_map.json", source_map)
    write_json(paths["validated_dir"] / "memory_metadata.json", memory_metadata)

    index_result = None
    if rebuild_index:
        index_result = rebuild_organism_memory_index(project)

    return {
        "ok": True,
        "memory_status": "validated",
        "source_type": "cir_final_consultant",
        "content_origin": "simple_memory_fallback",
        "nlp_pipeline_used": False,
        "nlp_pipeline_error": nlp_result,
        "file": str(cir_path),
        "file_name": cir_path.name,
        "extracted_text": str(extracted_text_path),
        "text_chars": len(text),
        "sections_count": len(sections),
        "chunks_count": len(chunks),
        "style_examples_count": len(style.get("examples") or []),
        "outputs": {
            "knowledge": str(paths["validated_knowledge"]),
            "style": str(paths["validated_style"]),
            "chunks": str(paths["validated_chunks"]),
            "sections": str(paths["validated_dir"] / "sections.json"),
            "source_map": str(paths["validated_dir"] / "source_map.json"),
            "memory_metadata": str(paths["validated_dir"] / "memory_metadata.json"),
        },
        "index_result": index_result,
    }


# ---------------------------------------------------------------------------
# Mémoire de travail depuis EnnoDiagnostic / EnnoScholar
# ---------------------------------------------------------------------------

def _latest_diagnostic_candidates(project: Project) -> List[Path]:
    ps = get_project_store(project)
    d = Path(ps.project_dir)
    return [
        d / "diagnostics" / "ennodiagnostic_report.json",
        d / "diagnostics" / "diagnostic_ennodiagnostic.json",
        d / "ennodiagnostic" / "ennodiagnostic_report.json",
        d / "ennodiagnostic_report.json",
    ]


def find_latest_diagnostic_report(project: Project) -> Optional[Path]:
    for p in _latest_diagnostic_candidates(project):
        if p.exists():
            return p
    return None


def build_working_memory_from_diagnostic(project: Project, rebuild_index: bool = True) -> Dict[str, Any]:
    paths = ensure_memory_dirs(project)
    diag_path = find_latest_diagnostic_report(project)

    if not diag_path:
        return {
            "ok": False,
            "reason": "Aucun rapport EnnoDiagnostic trouvé.",
        }

    data = read_json(diag_path, {})
    text = ""

    if isinstance(data, dict):
        if isinstance(data.get("diagnostic"), dict):
            text = data["diagnostic"].get("content") or ""
        text = text or data.get("content") or data.get("report_markdown") or flatten_text(data, max_chars=50000)

    text = clean_text(text, 80000)
    sections = split_cir_sections(text)
    chunks = build_chunks_from_sections(
        sections=sections,
        project=project,
        source_file=diag_path.name,
        memory_status="working",
        source_type="ennodiagnostic",
    )

    memory = {
        "organisme": project.organisme,
        "project": project.project_name,
        "project_id": project.id,
        "year": str(project.year or ""),
        "memory_type": "working_diagnostic",
        "memory_status": "working",
        "source_type": "ennodiagnostic",
        "confidence": "medium",
        "requires_validation": True,
        "created_at": now_iso(),
        "source_file": str(diag_path),
        "diagnostic_summary": flatten_text(data, max_chars=12000),
        "chunks_count": len(chunks),
        "warning": "Mémoire de travail issue d'EnnoDiagnostic : ne pas considérer comme CIR final validé.",
    }

    write_json(paths["working_diagnostic"], memory)

    # Fusionner dans working_chunks.json avec source_type=ennodiagnostic
    existing = read_json(paths["working_chunks"], [])
    existing = [x for x in existing if not (isinstance(x, dict) and x.get("source_type") == "ennodiagnostic")]
    write_json(paths["working_chunks"], existing + chunks)

    index_result = None
    if rebuild_index:
        index_result = rebuild_organism_memory_index(project)

    return {
        "ok": True,
        "source_file": str(diag_path),
        "chunks_count": len(chunks),
        "outputs": {
            "diagnostic_memory": str(paths["working_diagnostic"]),
            "working_chunks": str(paths["working_chunks"]),
        },
        "index_result": index_result,
    }


def _scholar_report_candidates(project: Project) -> List[Path]:
    ps = get_project_store(project)
    d = Path(ps.project_dir)
    return [
        d / "ennoscholar" / "ennoscholar_report.json",
        d / "ennoscholar_report.json",
    ]


def _state_art_candidates(project: Project) -> List[Path]:
    ps = get_project_store(project)
    d = Path(ps.project_dir)
    return [
        d / "ennoscholar" / "ennoscholar_state_of_art_report.json",
        d / "ennoscholar_state_of_art_report.json",
    ]


def _article_is_kept(article: Dict[str, Any]) -> bool:
    st = str(
        article.get("consultant_status")
        or article.get("status")
        or article.get("decision")
        or ""
    ).strip().lower()
    return st in {"garde", "gardé", "keep", "kept", "selected", "retenu", "validé", "valide"}


def _article_tag(article: Dict[str, Any]) -> str:
    return clean_text(
        article.get("tag") or article.get("tag_article") or article.get("classification") or article.get("label"),
        80,
    )


def _article_title(article: Dict[str, Any]) -> str:
    return clean_text(article.get("title") or article.get("paper_title") or article.get("article_title"), 500)


def build_working_memory_from_scholar(
    project: Project,
    articles_from_db: Optional[List[Any]] = None,
    rebuild_index: bool = True,
) -> Dict[str, Any]:
    paths = ensure_memory_dirs(project)

    scholar_report_path = next((p for p in _scholar_report_candidates(project) if p.exists()), None)
    state_art_path = next((p for p in _state_art_candidates(project) if p.exists()), None)

    report = read_json(scholar_report_path, {}) if scholar_report_path else {}
    state_art = read_json(state_art_path, {}) if state_art_path else {}

    articles: List[Dict[str, Any]] = []

    # 1) Articles depuis DB si fournis par le router
    for a in articles_from_db or []:
        try:
            source_json = a.source_json if isinstance(a.source_json, dict) else {}
            item = {
                **source_json,
                "db_article_id": a.id,
                "title": a.title,
                "year": a.year,
                "source": a.source,
                "tag": a.tag_article,
                "score": a.score,
                "url": a.url,
                "doi": a.doi,
                "consultant_status": a.consultant_status,
                "verrou_id": a.verrou_id,
            }
            articles.append(item)
        except Exception:
            continue

    # 2) Articles depuis report fichier
    if isinstance(report, dict):
        for r in report.get("results") or []:
            if not isinstance(r, dict):
                continue
            for item in r.get("articles") or []:
                if isinstance(item, dict):
                    x = dict(item)
                    x.setdefault("verrou_id", r.get("verrou_id"))
                    x.setdefault("verrou_title", r.get("verrou_title"))
                    articles.append(x)

    # Déduplication
    seen = set()
    dedup_articles: List[Dict[str, Any]] = []
    for a in articles:
        title = _article_title(a)
        if not title:
            continue
        key = (title.lower(), str(a.get("verrou_id") or ""))
        if key in seen:
            continue
        seen.add(key)
        dedup_articles.append(a)

    bibliography_items = []
    for a in dedup_articles:
        bibliography_items.append({
            "organisme": project.organisme,
            "project": project.project_name,
            "project_id": project.id,
            "year": str(project.year or ""),
            "verrou_id": a.get("verrou_id"),
            "verrou_title": a.get("verrou_title"),
            "title": _article_title(a),
            "authors": a.get("authors") or [],
            "year_publication": a.get("year"),
            "source": a.get("source"),
            "tag": _article_tag(a),
            "score": a.get("score") or a.get("relevance_score"),
            "url": a.get("url"),
            "doi": a.get("doi"),
            "consultant_status": a.get("consultant_status"),
            "is_kept": _article_is_kept(a),
            "memory_status": "working",
            "source_type": "ennoscholar_article",
            "confidence": "medium",
            "requires_validation": True,
            "created_at": now_iso(),
        })

    bibliography_memory = {
        "organisme": project.organisme,
        "project": project.project_name,
        "project_id": project.id,
        "year": str(project.year or ""),
        "memory_type": "bibliography_memory",
        "memory_status": "working",
        "source_type": "ennoscholar",
        "created_at": now_iso(),
        "scholar_report": str(scholar_report_path) if scholar_report_path else None,
        "state_of_art_report": str(state_art_path) if state_art_path else None,
        "articles_count": len(bibliography_items),
        "kept_articles_count": sum(1 for x in bibliography_items if x.get("is_kept")),
        "items": bibliography_items,
        "warning": "Mémoire bibliographique : ne pas mélanger avec le style CIR.",
    }

    write_json(paths["bibliography_memory"], bibliography_memory)

    # Generated state of art comme working memory séparée
    state_art_memory = {
        "organisme": project.organisme,
        "project": project.project_name,
        "project_id": project.id,
        "year": str(project.year or ""),
        "memory_type": "working_generated_state_of_art",
        "memory_status": "working",
        "source_type": "ennoscholar_generated",
        "confidence": "medium",
        "requires_validation": True,
        "created_at": now_iso(),
        "source_file": str(state_art_path) if state_art_path else None,
        "report": state_art if isinstance(state_art, dict) else {},
        "warning": "Texte généré par LLM : ne devient validé qu'après intégration/validation consultant.",
    }
    write_json(paths["working_generated_state_art"], state_art_memory)

    # Chunks depuis les drafts générés
    generated_chunks: List[Dict[str, Any]] = []
    if isinstance(state_art, dict):
        sections: List[Dict[str, Any]] = []
        for i, r in enumerate(state_art.get("results") or [], start=1):
            if not isinstance(r, dict):
                continue
            soa = r.get("state_of_art") if isinstance(r.get("state_of_art"), dict) else {}
            draft = clean_text(soa.get("draft") or r.get("draft"))
            if draft:
                sections.append({
                    "section_id": f"SA{i:03d}",
                    "section_title": r.get("verrou_title") or f"État de l'art généré {i}",
                    "role": "etat_art",
                    "text": draft,
                    "char_count": len(draft),
                })
        generated_chunks = build_chunks_from_sections(
            sections=sections,
            project=project,
            source_file=Path(str(state_art_path)).name if state_art_path else "ennoscholar_state_of_art_report.json",
            memory_status="working",
            source_type="ennoscholar_generated",
        )

    existing = read_json(paths["working_chunks"], [])
    existing = [
        x for x in existing
        if not (isinstance(x, dict) and x.get("source_type") in {"ennoscholar_generated"})
    ]
    write_json(paths["working_chunks"], existing + generated_chunks)

    index_result = None
    if rebuild_index:
        index_result = rebuild_organism_memory_index(project)

    return {
        "ok": True,
        "scholar_report_found": bool(scholar_report_path),
        "state_art_report_found": bool(state_art_path),
        "articles_count": len(bibliography_items),
        "kept_articles_count": sum(1 for x in bibliography_items if x.get("is_kept")),
        "generated_chunks_count": len(generated_chunks),
        "outputs": {
            "bibliography_memory": str(paths["bibliography_memory"]),
            "generated_state_of_art": str(paths["working_generated_state_art"]),
            "working_chunks": str(paths["working_chunks"]),
        },
        "index_result": index_result,
    }


def build_all_memory_for_project(
    project: Project,
    articles_from_db: Optional[List[Any]] = None,
    include_validated: bool = True,
    include_working: bool = True,
) -> Dict[str, Any]:
    results: Dict[str, Any] = {
        "ok": True,
        "project_id": project.id,
        "organisme": project.organisme,
        "project": project.project_name,
        "year": str(project.year or ""),
        "steps": {},
    }

    if include_validated:
        results["steps"]["validated_from_cir_final"] = build_validated_memory_from_cir_final(
            project=project,
            cir_file_path=None,
            rebuild_index=False,
        )

    if include_working:
        results["steps"]["working_from_diagnostic"] = build_working_memory_from_diagnostic(
            project=project,
            rebuild_index=False,
        )
        results["steps"]["working_from_scholar"] = build_working_memory_from_scholar(
            project=project,
            articles_from_db=articles_from_db,
            rebuild_index=False,
        )

    results["index_result"] = rebuild_organism_memory_index(project)
    return sanitize_json_value(results)


# ---------------------------------------------------------------------------
# Index organisme global
# ---------------------------------------------------------------------------

def _iter_project_memory_files(organism_dir: Path, filename: str) -> Iterable[Path]:
    # Structure recommandée
    for p in organism_dir.glob(f"projects/*/years/*/memory/**/{filename}"):
        if p.is_file():
            yield p

    # Fallback : projets stockés autrement sous organisme
    for p in organism_dir.glob(f"**/memory/**/{filename}"):
        if p.is_file():
            yield p


def _load_list_or_items(path: Path, key: str = "items") -> List[Dict[str, Any]]:
    data = read_json(path, {})
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        arr = data.get(key)
        if isinstance(arr, list):
            return [x for x in arr if isinstance(x, dict)]
    return []


def rebuild_organism_memory_index(project: Project) -> Dict[str, Any]:
    paths = ensure_memory_dirs(project)
    organism_dir = paths["organism_dir"]

    knowledge_entries: List[Dict[str, Any]] = []
    style_examples: List[Dict[str, Any]] = []
    validated_chunks: List[Dict[str, Any]] = []
    working_chunks: List[Dict[str, Any]] = []
    articles: List[Dict[str, Any]] = []

    # validated knowledge
    for p in _iter_project_memory_files(organism_dir, "knowledge.json"):
        data = read_json(p, {})
        if isinstance(data, dict) and data.get("memory_type") == "validated_knowledge":
            knowledge_entries.append(data)

    # validated style
    for p in _iter_project_memory_files(organism_dir, "style.json"):
        data = read_json(p, {})
        if isinstance(data, dict):
            for ex in data.get("examples") or []:
                if isinstance(ex, dict):
                    style_examples.append(ex)

    # validated chunks
    for p in _iter_project_memory_files(organism_dir, "chunks.json"):
        for c in _load_list_or_items(p, key="chunks"):
            if c.get("memory_status") == "validated":
                validated_chunks.append(c)

    # working chunks
    for p in _iter_project_memory_files(organism_dir, "working_chunks.json"):
        for c in _load_list_or_items(p, key="chunks"):
            if c.get("memory_status") == "working":
                working_chunks.append(c)

    # bibliography
    for p in _iter_project_memory_files(organism_dir, "bibliography_memory.json"):
        for a in _load_list_or_items(p, key="items"):
            articles.append(a)

    knowledge_index = {
        "organisme": project.organisme,
        "organisme_slug": slugify(project.organisme),
        "index_type": "organism_validated_knowledge_index",
        "created_at": now_iso(),
        "projects_count": len({(x.get("project"), x.get("year")) for x in knowledge_entries}),
        "entries_count": len(knowledge_entries),
        "entries": knowledge_entries,
    }

    style_index = {
        "organisme": project.organisme,
        "organisme_slug": slugify(project.organisme),
        "index_type": "organism_validated_style_index",
        "created_at": now_iso(),
        "examples_count": len(style_examples),
        "examples": style_examples,
    }

    chunks_index = {
        "organisme": project.organisme,
        "organisme_slug": slugify(project.organisme),
        "index_type": "organism_validated_chunks_index",
        "created_at": now_iso(),
        "chunks_count": len(validated_chunks),
        "chunks": validated_chunks,
    }

    working_index = {
        "organisme": project.organisme,
        "organisme_slug": slugify(project.organisme),
        "index_type": "organism_working_index",
        "created_at": now_iso(),
        "chunks_count": len(working_chunks),
        "chunks": working_chunks,
        "warning": "Mémoire de travail : ne pas utiliser comme mémoire validée.",
    }

    articles_index = {
        "organisme": project.organisme,
        "organisme_slug": slugify(project.organisme),
        "index_type": "organism_articles_index",
        "created_at": now_iso(),
        "articles_count": len(articles),
        "kept_articles_count": sum(1 for x in articles if x.get("is_kept")),
        "items": articles,
    }

    # Index global pour recherche simple : validated + working + articles résumés
    global_items: List[Dict[str, Any]] = []
    global_items.extend(validated_chunks)
    global_items.extend(working_chunks)

    for a in articles:
        title = clean_text(a.get("title"))
        if not title:
            continue
        global_items.append({
            "chunk_id": f"article_{slugify(title)[:80]}_{a.get('year_publication') or ''}",
            "organisme": a.get("organisme"),
            "project": a.get("project"),
            "project_id": a.get("project_id"),
            "year": a.get("year"),
            "role": "bibliography",
            "section_title": a.get("verrou_title") or "Article scientifique",
            "text": " — ".join([
                title,
                clean_text(a.get("tag")),
                clean_text(a.get("source")),
                clean_text(a.get("doi") or a.get("url")),
            ]),
            "source_type": "ennoscholar_article",
            "memory_status": "working",
            "confidence": "medium",
            "requires_validation": True,
        })

    global_search_index = {
        "organisme": project.organisme,
        "organisme_slug": slugify(project.organisme),
        "index_type": "organism_global_search_index",
        "created_at": now_iso(),
        "items_count": len(global_items),
        "items": global_items,
        "rules": {
            "validated": "CIR finaux consultant : utilisables comme connaissance validée + style.",
            "working": "Sorties agents : contexte de travail à valider.",
            "bibliography": "Articles scientifiques : sources séparées, pas style CIR.",
        },
    }

    write_json(paths["organism_knowledge_index"], knowledge_index)
    write_json(paths["organism_style_index"], style_index)
    write_json(paths["organism_chunks_index"], chunks_index)
    write_json(paths["organism_working_index"], working_index)
    write_json(paths["organism_articles_index"], articles_index)
    write_json(paths["organism_global_search_index"], global_search_index)

    return {
        "ok": True,
        "organism_dir": str(organism_dir),
        "outputs": {
            "knowledge_index": str(paths["organism_knowledge_index"]),
            "style_index": str(paths["organism_style_index"]),
            "chunks_index": str(paths["organism_chunks_index"]),
            "working_index": str(paths["organism_working_index"]),
            "articles_index": str(paths["organism_articles_index"]),
            "global_search_index": str(paths["organism_global_search_index"]),
        },
        "counts": {
            "knowledge_entries": len(knowledge_entries),
            "style_examples": len(style_examples),
            "validated_chunks": len(validated_chunks),
            "working_chunks": len(working_chunks),
            "articles": len(articles),
            "global_items": len(global_items),
        },
    }


# ---------------------------------------------------------------------------
# Recherche simple V1
# ---------------------------------------------------------------------------

STOPWORDS = {
    "avec", "dans", "pour", "plus", "moins", "entre", "comme", "cette", "cela",
    "ainsi", "afin", "sont", "nous", "notre", "leur", "leurs", "des", "les",
    "une", "aux", "sur", "par", "que", "qui", "quoi", "dont", "projet",
    "travaux", "article", "articles", "verrou", "cir", "the", "and", "with",
    "from", "that", "this", "are", "was", "were"
}


def tokenize(text: str) -> List[str]:
    text = strip_accents(str(text or "").lower())
    text = re.sub(r"[^a-z0-9%°µ/\-\.']+", " ", text)
    toks = []
    for t in text.split():
        t = t.strip("-_. '")
        if len(t) < 3:
            continue
        if t in STOPWORDS:
            continue
        toks.append(t)
    return toks


def lexical_score(query: str, text: str) -> float:
    q = tokenize(query)
    t = tokenize(text)
    if not q or not t:
        return 0.0
    qs = set(q)
    ts = set(t)
    inter = len(qs & ts)
    if inter == 0:
        return 0.0
    coverage = inter / max(1, len(qs))
    precision = inter / max(1, len(ts))
    return round((0.78 * coverage) + (0.22 * min(1.0, precision * 8)), 4)


def search_organism_memory(
    project: Project,
    query: str,
    roles: Optional[List[str]] = None,
    memory_statuses: Optional[List[str]] = None,
    source_types: Optional[List[str]] = None,
    top_k: int = 8,
) -> Dict[str, Any]:
    paths = ensure_memory_dirs(project)

    index = read_json(paths["organism_global_search_index"], {})
    if not index.get("items"):
        rebuild_organism_memory_index(project)
        index = read_json(paths["organism_global_search_index"], {})

    items = index.get("items") or []
    roles_set = {str(x).strip() for x in roles or [] if str(x).strip()}
    status_set = {str(x).strip() for x in memory_statuses or [] if str(x).strip()}
    source_set = {str(x).strip() for x in source_types or [] if str(x).strip()}

    scored = []
    for item in items:
        if not isinstance(item, dict):
            continue

        if roles_set and str(item.get("role") or "") not in roles_set:
            continue
        if status_set and str(item.get("memory_status") or "") not in status_set:
            continue
        if source_set and str(item.get("source_type") or "") not in source_set:
            continue

        text = " ".join([
            clean_text(item.get("section_title"), 300),
            clean_text(item.get("text"), 4000),
            clean_text(item.get("project"), 200),
            clean_text(item.get("year"), 20),
        ])

        score = lexical_score(query, text)
        if score <= 0:
            continue

        out = dict(item)
        out["score"] = score
        out["text"] = clean_text(out.get("text"), 1800)
        scored.append(out)

    scored.sort(key=lambda x: (float(x.get("score") or 0), 1 if x.get("memory_status") == "validated" else 0), reverse=True)

    return {
        "ok": True,
        "query": query,
        "organisme": project.organisme,
        "top_k": top_k,
        "roles": list(roles_set),
        "memory_statuses": list(status_set),
        "source_types": list(source_set),
        "matches_count": len(scored[:top_k]),
        "matches": scored[:top_k],
        "index_path": str(paths["organism_global_search_index"]),
    }


def retrieve_style_examples_for_project(
    project: Project,
    role: str,
    query_text: str,
    top_k: int = 3,
) -> List[Dict[str, Any]]:
    """
    Fonction prête pour EnnoScholar / EnnoAmel.
    Récupère uniquement la mémoire style validée.
    """
    paths = ensure_memory_dirs(project)
    index = read_json(paths["organism_style_index"], {})
    if not index.get("examples"):
        rebuild_organism_memory_index(project)
        index = read_json(paths["organism_style_index"], {})

    examples = []
    for ex in index.get("examples") or []:
        if not isinstance(ex, dict):
            continue
        if role and str(ex.get("role") or "") != role:
            continue
        score = lexical_score(query_text, f"{ex.get('section_title')} {ex.get('text')}")
        if score <= 0:
            score = 0.01
        y = dict(ex)
        y["style_match_score"] = score
        examples.append(y)

    examples.sort(key=lambda x: float(x.get("style_match_score") or 0), reverse=True)
    return examples[:top_k]


def build_style_block(examples: List[Dict[str, Any]], max_chars_per_example: int = 900) -> str:
    if not examples:
        return "Aucun exemple de style CIR validé disponible."

    lines = [
        "EXEMPLES DE STYLE CIR VALIDÉS",
        "Ces exemples servent uniquement à imiter le style, la structure argumentative et le niveau de prudence.",
        "Ils ne doivent jamais être utilisés comme preuves factuelles du dossier courant.",
    ]

    for i, ex in enumerate(examples, start=1):
        lines.append("")
        lines.append(
            f"[STYLE {i}] rôle={ex.get('role')} | projet={ex.get('project')} | année={ex.get('year')} | score={ex.get('style_match_score')}"
        )
        if ex.get("section_title"):
            lines.append(f"Titre section : {ex.get('section_title')}")
        lines.append("Extrait de style :")
        lines.append(clean_text(ex.get("text"), max_chars_per_example))

    return "\n".join(lines).strip()


def get_project_memory_status(project: Project) -> Dict[str, Any]:
    paths = ensure_memory_dirs(project)

    return {
        "ok": True,
        "project_id": project.id,
        "organisme": project.organisme,
        "project": project.project_name,
        "year": str(project.year or ""),
        "paths": {k: str(v) for k, v in paths.items() if isinstance(v, Path)},
        "files_found": {
            "validated_knowledge": paths["validated_knowledge"].exists(),
            "validated_style": paths["validated_style"].exists(),
            "validated_chunks": paths["validated_chunks"].exists(),
            "working_diagnostic": paths["working_diagnostic"].exists(),
            "working_scholar": paths["working_scholar"].exists(),
            "working_generated_state_art": paths["working_generated_state_art"].exists(),
            "working_chunks": paths["working_chunks"].exists(),
            "bibliography_memory": paths["bibliography_memory"].exists(),
            "organism_global_search_index": paths["organism_global_search_index"].exists(),
        },
        "latest_cir_final": str(find_latest_cir_final_file(project)) if find_latest_cir_final_file(project) else None,
    }


# =============================================================================
# V2 RAG CHROMA + LOGS — surcharge non destructive
# =============================================================================
# Cette partie garde tout le traitement NLP existant au-dessus, puis ajoute :
# - logs extraction/NLP/JSON/index/Chroma dans les réponses API
# - stockage Chroma après build validated / working / all
# - recherche RAG Chroma

import hashlib as _memory_hashlib
import math as _memory_math

_MEMORY_EMBEDDER = None
_MEMORY_EMBEDDING_DIM = 384


def _memory_log(step: str, status: str, message: str, **data: Any) -> Dict[str, Any]:
    return {
        "time": now_iso(),
        "step": step,
        "status": status,
        "message": message,
        **sanitize_json_value(data),
    }


def _memory_safe_meta(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return value
    return clean_text(json.dumps(sanitize_json_value(value), ensure_ascii=False), 1000)


def _memory_hash_embedding(text: str, dim: int = _MEMORY_EMBEDDING_DIM) -> List[float]:
    toks = tokenize(text)
    vec = [0.0] * dim
    if not toks:
        return vec
    for tok in toks:
        digest = _memory_hashlib.sha256(tok.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "little") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vec[idx] += sign
    norm = _memory_math.sqrt(sum(x * x for x in vec)) or 1.0
    return [round(x / norm, 6) for x in vec]


def memory_embed_texts(texts: List[str]) -> Tuple[List[List[float]], str]:
    """
    Embeddings pour Chroma.
    Priorité : sentence-transformers si installé.
    Fallback : hash_embedding local 384 dimensions.
    """
    global _MEMORY_EMBEDDER
    model_name = os.getenv(
        "ENNOSMART_MEMORY_EMBEDDING_MODEL",
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    )
    try:
        if _MEMORY_EMBEDDER is None:
            from sentence_transformers import SentenceTransformer  # type: ignore
            _MEMORY_EMBEDDER = SentenceTransformer(model_name)
        vectors = _MEMORY_EMBEDDER.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
        ).tolist()
        return [[float(v) for v in row] for row in vectors], model_name
    except Exception:
        return [_memory_hash_embedding(t) for t in texts], "hash_embedding_fallback_384"


def _memory_chroma_paths(project: Project) -> Dict[str, Any]:
    paths = ensure_memory_dirs(project)
    chroma_dir = paths["organism_dir"] / "memory" / "chroma"
    chroma_dir.mkdir(parents=True, exist_ok=True)
    collection_name = f"ennosmart_memory_{slugify(project.organisme)[:45]}"
    return {"chroma_dir": chroma_dir, "collection_name": collection_name}


def _get_memory_chroma_collection(project: Project):
    try:
        import chromadb  # type: ignore
    except Exception as exc:
        raise RuntimeError("ChromaDB non installé. Lance : pip install chromadb") from exc
    cp = _memory_chroma_paths(project)
    client = chromadb.PersistentClient(path=str(cp["chroma_dir"]))
    collection = client.get_or_create_collection(
        name=cp["collection_name"],
        metadata={"organisme": str(project.organisme or ""), "type": "ennosmart_cir_memory"},
    )
    return client, collection, cp["chroma_dir"], cp["collection_name"]


def _metadata_for_chroma(item: Dict[str, Any]) -> Dict[str, Any]:
    allowed = [
        "chunk_id", "organisme", "organisme_slug", "project", "project_slug",
        "project_id", "year", "role", "section_id", "section_title",
        "section_type", "section_label", "source_file", "source_type",
        "memory_status", "content_origin", "confidence", "requires_validation",
        "can_use_as_fact", "can_use_as_style", "created_at",
    ]
    out: Dict[str, Any] = {}
    for k in allowed:
        if k in item:
            out[k] = _memory_safe_meta(item.get(k))
    # Chroma filtre mieux avec str pour project_id
    if "project_id" in out:
        out["project_id"] = str(out["project_id"])
    return out


def _memory_items_for_project(project: Project) -> List[Dict[str, Any]]:
    paths = ensure_memory_dirs(project)
    items: List[Dict[str, Any]] = []

    validated = read_json(paths["validated_chunks"], [])
    if isinstance(validated, list):
        items.extend([x for x in validated if isinstance(x, dict)])

    working = read_json(paths["working_chunks"], [])
    if isinstance(working, list):
        items.extend([x for x in working if isinstance(x, dict)])

    bibliography = read_json(paths["bibliography_memory"], {})
    for a in bibliography.get("items") or []:
        if not isinstance(a, dict):
            continue
        title = clean_text(a.get("title"))
        if not title:
            continue
        items.append({
            "chunk_id": f"article_{slugify(title)[:80]}_{a.get('year_publication') or ''}_{slugify(a.get('verrou_id'))}",
            "organisme": a.get("organisme") or project.organisme,
            "organisme_slug": slugify(a.get("organisme") or project.organisme),
            "project": a.get("project") or project.project_name,
            "project_slug": slugify(a.get("project") or project.project_name),
            "project_id": a.get("project_id") or project.id,
            "year": a.get("year") or str(project.year or ""),
            "role": "bibliography",
            "section_title": a.get("verrou_title") or "Article scientifique",
            "text": " — ".join([
                title,
                clean_text(a.get("tag")),
                clean_text(a.get("source")),
                clean_text(a.get("doi") or a.get("url")),
            ]),
            "source_type": "ennoscholar_article",
            "memory_status": "working",
            "content_origin": "ennoscholar",
            "confidence": "medium",
            "requires_validation": True,
            "can_use_as_fact": False,
            "can_use_as_style": False,
            "created_at": now_iso(),
        })

    dedup: Dict[str, Dict[str, Any]] = {}
    for it in items:
        cid = clean_text(it.get("chunk_id"))
        txt = clean_text(it.get("text"))
        if cid and txt:
            dedup[cid] = it
    return list(dedup.values())


def _memory_global_items(project: Project) -> List[Dict[str, Any]]:
    paths = ensure_memory_dirs(project)
    index = read_json(paths["organism_global_search_index"], {})
    items = index.get("items") or []
    if not isinstance(items, list):
        return []
    return [x for x in items if isinstance(x, dict) and clean_text(x.get("text"))]


def store_items_in_chroma(
    project: Project,
    items: List[Dict[str, Any]],
    reset_project: bool = False,
    reset_organism: bool = False,
) -> Dict[str, Any]:
    logs: List[Dict[str, Any]] = []
    try:
        client, collection, chroma_dir, collection_name = _get_memory_chroma_collection(project)
    except Exception as exc:
        return {
            "ok": False,
            "stored": False,
            "reason": str(exc),
            "logs": [_memory_log("chroma", "error", "Chroma indisponible.", error=str(exc))],
        }

    try:
        if reset_organism:
            try:
                client.delete_collection(collection_name)
            except Exception:
                pass
            collection = client.get_or_create_collection(
                name=collection_name,
                metadata={"organisme": str(project.organisme or ""), "type": "ennosmart_cir_memory"},
            )
            logs.append(_memory_log("chroma_reset", "ok", "Collection organisme réinitialisée.", collection=collection_name))
        elif reset_project:
            try:
                collection.delete(where={"project_id": str(project.id)})
                logs.append(_memory_log("chroma_delete_project", "ok", "Anciens chunks projet supprimés de Chroma.", project_id=project.id))
            except Exception as exc:
                logs.append(_memory_log("chroma_delete_project", "warning", "Suppression anciens chunks projet impossible.", error=str(exc)))

        ids: List[str] = []
        docs: List[str] = []
        metas: List[Dict[str, Any]] = []
        for it in items:
            cid = clean_text(it.get("chunk_id"))
            txt = clean_text(it.get("text"))
            if not cid or not txt:
                continue
            ids.append(cid)
            docs.append(txt)
            metas.append(_metadata_for_chroma(it))

        if not ids:
            logs.append(_memory_log("chroma_upsert", "warning", "Aucun chunk à stocker dans Chroma."))
            return {"ok": True, "stored": True, "items_count": 0, "collection": collection_name, "chroma_dir": str(chroma_dir), "logs": logs}

        total = 0
        embedding_model = ""
        batch_size = 64
        for start in range(0, len(ids), batch_size):
            b_ids = ids[start:start+batch_size]
            b_docs = docs[start:start+batch_size]
            b_metas = metas[start:start+batch_size]
            embeddings, embedding_model = memory_embed_texts(b_docs)
            collection.upsert(ids=b_ids, documents=b_docs, metadatas=b_metas, embeddings=embeddings)
            total += len(b_ids)

        logs.append(_memory_log(
            "chroma_upsert", "ok", "Chunks stockés dans Chroma.",
            items_count=total, collection=collection_name, chroma_dir=str(chroma_dir), embedding_model=embedding_model,
        ))
        return {"ok": True, "stored": True, "items_count": total, "collection": collection_name, "chroma_dir": str(chroma_dir), "embedding_model": embedding_model, "logs": logs}
    except Exception as exc:
        logs.append(_memory_log("chroma_upsert", "error", "Erreur stockage Chroma.", error=str(exc)))
        return {"ok": False, "stored": False, "reason": str(exc), "collection": collection_name, "chroma_dir": str(chroma_dir), "logs": logs}


def store_project_memory_in_chroma(project: Project, reset_project: bool = True) -> Dict[str, Any]:
    return store_items_in_chroma(project, _memory_items_for_project(project), reset_project=reset_project)


def store_organism_memory_in_chroma(project: Project, reset_organism: bool = False) -> Dict[str, Any]:
    return store_items_in_chroma(project, _memory_global_items(project), reset_organism=reset_organism)


def search_organism_memory_chroma(
    project: Project,
    query: str,
    roles: Optional[List[str]] = None,
    memory_statuses: Optional[List[str]] = None,
    source_types: Optional[List[str]] = None,
    top_k: int = 8,
) -> Dict[str, Any]:
    logs: List[Dict[str, Any]] = []
    query = clean_text(query, 1000)
    if not query:
        return {"ok": False, "reason": "query obligatoire.", "matches": [], "logs": logs}
    try:
        _, collection, chroma_dir, collection_name = _get_memory_chroma_collection(project)
    except Exception as exc:
        return {"ok": False, "reason": str(exc), "matches": [], "logs": [_memory_log("chroma_search", "error", "Chroma indisponible.", error=str(exc))]}
    try:
        embeddings, embedding_model = memory_embed_texts([query])
        res = collection.query(query_embeddings=embeddings, n_results=max(10, top_k * 5), include=["documents", "metadatas", "distances"])
        ids = (res.get("ids") or [[]])[0]
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        distances = (res.get("distances") or [[]])[0]
        roles_set = {str(x).strip() for x in roles or [] if str(x).strip()}
        status_set = {str(x).strip() for x in memory_statuses or [] if str(x).strip()}
        source_set = {str(x).strip() for x in source_types or [] if str(x).strip()}
        matches: List[Dict[str, Any]] = []
        for cid, doc, meta, dist in zip(ids, docs, metas, distances):
            meta = meta or {}
            if roles_set and str(meta.get("role") or "") not in roles_set:
                continue
            if status_set and str(meta.get("memory_status") or "") not in status_set:
                continue
            if source_set and str(meta.get("source_type") or "") not in source_set:
                continue
            try:
                score = round(1.0 / (1.0 + float(dist)), 4)
            except Exception:
                score = 0.0
            matches.append({"chunk_id": cid, "score": score, "distance": dist, "text": clean_text(doc, 1800), **meta})
            if len(matches) >= top_k:
                break
        logs.append(_memory_log("chroma_search", "ok", "Recherche RAG mémoire terminée.", matches_count=len(matches), collection=collection_name, chroma_dir=str(chroma_dir), embedding_model=embedding_model))
        return {"ok": True, "engine": "chroma", "query": query, "organisme": project.organisme, "top_k": top_k, "roles": list(roles_set), "memory_statuses": list(status_set), "source_types": list(source_set), "matches_count": len(matches), "matches": matches, "collection": collection_name, "chroma_dir": str(chroma_dir), "embedding_model": embedding_model, "logs": logs}
    except Exception as exc:
        logs.append(_memory_log("chroma_search", "error", "Erreur recherche Chroma.", error=str(exc)))
        return {"ok": False, "reason": str(exc), "matches": [], "collection": collection_name, "chroma_dir": str(chroma_dir), "logs": logs}


# Wrappers pour ajouter logs + Chroma sans casser le pipeline NLP existant.
_build_validated_memory_from_cir_final_base = build_validated_memory_from_cir_final
_build_working_memory_from_diagnostic_base = build_working_memory_from_diagnostic
_build_working_memory_from_scholar_base = build_working_memory_from_scholar
_build_all_memory_for_project_base = build_all_memory_for_project
_get_project_memory_status_base = get_project_memory_status


def _logs_from_build_result(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    logs: List[Dict[str, Any]] = []
    if result.get("file"):
        logs.append(_memory_log("file_detect", "ok", "CIR final détecté.", file=result.get("file"), file_name=result.get("file_name")))
    if result.get("extracted_text"):
        logs.append(_memory_log("extraction", "ok", "Extraction texte terminée.", text_chars=result.get("text_chars"), extracted_text=result.get("extracted_text")))
    if "nlp_pipeline_used" in result:
        logs.append(_memory_log("nlp", "ok" if result.get("nlp_pipeline_used") else "warning", "Traitement NLP CIR terminé." if result.get("nlp_pipeline_used") else "Fallback simple utilisé.", content_origin=result.get("content_origin"), nlp_module=result.get("nlp_module"), sections_count=result.get("sections_count")))
    if result.get("outputs"):
        logs.append(_memory_log("json_write", "ok", "JSON mémoire écrits.", outputs=result.get("outputs"), chunks_count=result.get("chunks_count")))
    if result.get("index_result"):
        logs.append(_memory_log("index", "ok", "Index organisme mis à jour.", counts=(result.get("index_result") or {}).get("counts")))
    return logs


def build_validated_memory_from_cir_final(
    project: Project,
    cir_file_path: str | Path | None = None,
    rebuild_index: bool = True,
    store_chroma: bool = True,
) -> Dict[str, Any]:
    result = _build_validated_memory_from_cir_final_base(project, cir_file_path=cir_file_path, rebuild_index=rebuild_index)
    logs = _logs_from_build_result(result)
    if result.get("ok") and store_chroma:
        chroma = store_project_memory_in_chroma(project, reset_project=True)
        result["chroma_result"] = chroma
        logs.extend(chroma.get("logs") or [])
    result["logs"] = logs
    return sanitize_json_value(result)


def build_working_memory_from_diagnostic(project: Project, rebuild_index: bool = True, store_chroma: bool = True) -> Dict[str, Any]:
    result = _build_working_memory_from_diagnostic_base(project, rebuild_index=rebuild_index)
    logs = [_memory_log("working_diagnostic", "ok" if result.get("ok") else "warning", "Build working EnnoDiagnostic terminé.", chunks_count=result.get("chunks_count"), reason=result.get("reason"))]
    if result.get("ok") and store_chroma:
        chroma = store_project_memory_in_chroma(project, reset_project=True)
        result["chroma_result"] = chroma
        logs.extend(chroma.get("logs") or [])
    result["logs"] = logs
    return sanitize_json_value(result)


def build_working_memory_from_scholar(project: Project, articles_from_db: Optional[List[Any]] = None, rebuild_index: bool = True, store_chroma: bool = True) -> Dict[str, Any]:
    result = _build_working_memory_from_scholar_base(project, articles_from_db=articles_from_db, rebuild_index=rebuild_index)
    logs = [_memory_log("working_scholar", "ok" if result.get("ok") else "warning", "Build working EnnoScholar terminé.", articles_count=result.get("articles_count"), generated_chunks_count=result.get("generated_chunks_count"), reason=result.get("reason"))]
    if result.get("ok") and store_chroma:
        chroma = store_project_memory_in_chroma(project, reset_project=True)
        result["chroma_result"] = chroma
        logs.extend(chroma.get("logs") or [])
    result["logs"] = logs
    return sanitize_json_value(result)


def build_all_memory_for_project(project: Project, articles_from_db: Optional[List[Any]] = None, include_validated: bool = True, include_working: bool = True) -> Dict[str, Any]:
    result = _build_all_memory_for_project_base(project, articles_from_db=articles_from_db, include_validated=include_validated, include_working=include_working)
    chroma = store_project_memory_in_chroma(project, reset_project=True)
    result["chroma_result"] = chroma
    logs: List[Dict[str, Any]] = []
    for step in (result.get("steps") or {}).values():
        if isinstance(step, dict):
            logs.extend(step.get("logs") or _logs_from_build_result(step))
    logs.extend(chroma.get("logs") or [])
    result["logs"] = logs
    return sanitize_json_value(result)


def get_project_memory_status(project: Project) -> Dict[str, Any]:
    status = _get_project_memory_status_base(project)
    paths = ensure_memory_dirs(project)
    metadata = read_json(paths["validated_dir"] / "memory_metadata.json", {})
    chroma_info = {"available": False, "items_count": None, "collection": None, "chroma_dir": str(paths["organism_dir"] / "memory" / "chroma")}
    try:
        _, collection, chroma_dir, collection_name = _get_memory_chroma_collection(project)
        chroma_info = {"available": True, "items_count": collection.count(), "collection": collection_name, "chroma_dir": str(chroma_dir)}
    except Exception as exc:
        chroma_info["error"] = str(exc)
    latest = find_latest_cir_final_file(project)
    status["latest_cir_final_name"] = latest.name if latest else None
    status["validated_metadata"] = metadata if isinstance(metadata, dict) else {}
    status["chroma"] = chroma_info
    return sanitize_json_value(status)
