# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from pathlib import Path
from datetime import datetime
import json
import re
import zipfile
import html
import shutil
import unicodedata
from typing import Optional

router = APIRouter(prefix="/projects", tags=["CIR final consultant"])


# =============================================================================
# Chemins / JSON
# =============================================================================

def root() -> Path:
    return Path(__file__).resolve().parents[2]


def safe_name(x: str, default: str = "unknown") -> str:
    x = str(x or default).strip()
    x = unicodedata.normalize("NFKD", x)
    x = "".join(c for c in x if not unicodedata.combining(c))
    x = re.sub(r"[^a-zA-Z0-9_.-]+", "_", x).strip("._-")
    return x or default


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


# =============================================================================
# Nettoyage texte / table des matières Word
# =============================================================================

def clean_text(text: str) -> str:
    text = str(text or "").replace("\x00", " ")
    text = text.replace("\xa0", " ").replace("\u202f", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _looks_like_word_coordinate(line: str) -> bool:
    return bool(re.match(r"^-?\d{8,}$", str(line or "").strip()))


def _is_toc_start(line: str) -> bool:
    low = str(line or "").strip().lower()
    return (
        "table des matières" in low
        or "table des matieres" in low
        or low == "sommaire"
    )


def _is_word_toc_line(line: str) -> bool:
    raw = str(line or "").strip()
    low = raw.lower()

    if not raw:
        return False

    if _is_toc_start(raw):
        return True

    if "pageref" in low or "_toc" in low or low.startswith("toc \\o"):
        return True

    # Cas d'une ligne de sommaire après extraction :
    # 1.3. Etat de l'art 5
    if re.match(r"^\d+(?:\.\d+)*\.?\s+.{3,180}\s+\d{1,3}$", raw):
        words = re.findall(r"[A-Za-zÀ-ÿ]{3,}", raw)
        if len(words) >= 2:
            return True

    return False


def remove_word_toc(text: str) -> str:
    """
    Supprime la table des matières Word avant détection des sections.
    Corrige les faux extraits contenant : TOC \\o, PAGEREF, _Toc.
    """
    text = str(text or "").replace("\r", "\n")
    lines = text.splitlines()

    cleaned: list[str] = []
    in_toc = False

    for line in lines:
        raw = str(line or "").strip()

        if _looks_like_word_coordinate(raw):
            continue

        if _is_toc_start(raw):
            in_toc = True
            continue

        is_toc_line = _is_word_toc_line(raw)

        if in_toc and is_toc_line:
            continue

        # Si on était dans la TOC et qu'on trouve une vraie ligne non TOC,
        # on sort du bloc TOC.
        if in_toc and raw and not is_toc_line:
            in_toc = False

        if is_toc_line:
            continue

        cleaned.append(line)

    return clean_text("\n".join(cleaned))


def _contains_toc_noise(text: str) -> bool:
    low = str(text or "").lower()
    return "pageref" in low or "_toc" in low or "toc \\o" in low


# =============================================================================
# Extraction DOCX / PDF / TXT
# =============================================================================

def _extract_docx_with_python_docx(path: Path) -> Optional[str]:
    """Extraction DOCX propre avec styles Word, en ignorant les styles toc."""
    try:
        from docx import Document
        from docx.oxml.text.paragraph import CT_P
        from docx.oxml.table import CT_Tbl
        from docx.text.paragraph import Paragraph
        from docx.table import Table
    except Exception:
        return None

    try:
        doc = Document(str(path))
    except Exception:
        return None

    parts: list[str] = []

    for child in doc.element.body.iterchildren():
        if isinstance(child, CT_P):
            para = Paragraph(child, doc)
            raw = para.text.strip()
            if not raw:
                continue

            style_name = ""
            try:
                style_name = (para.style.name or "").lower().strip()
            except Exception:
                style_name = ""

            if style_name.startswith("toc"):
                continue
            if _is_word_toc_line(raw):
                continue
            if _looks_like_word_coordinate(raw):
                continue

            parts.append(raw)

        elif isinstance(child, CT_Tbl):
            table = Table(child, doc)
            rows: list[str] = []
            for row in table.rows:
                cells = [cell.text.strip().replace("\n", " | ") for cell in row.cells if cell.text.strip()]
                if cells:
                    rows.append(" | ".join(cells))
            if rows:
                parts.append("\n".join(rows))

    return remove_word_toc("\n".join(parts))


def _extract_docx_from_xml(path: Path) -> str:
    """Fallback DOCX par XML brut."""
    parts: list[str] = []
    with zipfile.ZipFile(path) as z:
        names = ["word/document.xml"]
        names += [n for n in z.namelist() if n.startswith("word/header") and n.endswith(".xml")]
        names += [n for n in z.namelist() if n.startswith("word/footer") and n.endswith(".xml")]

        for name in names:
            if name not in z.namelist():
                continue

            xml = z.read(name).decode("utf-8", errors="ignore")
            xml = re.sub(r"</w:p>", "\n", xml)
            xml = re.sub(r"</w:tr>", "\n", xml)
            xml = re.sub(r"<[^>]+>", "", xml)
            parts.append(html.unescape(xml))

    return remove_word_toc("\n".join(parts))


def extract_docx(path: Path) -> str:
    text = _extract_docx_with_python_docx(path)
    if text and len(text.strip()) > 200:
        return text
    return _extract_docx_from_xml(path)


def extract_pdf(path: Path) -> str:
    errors = []
    for mod_name in ["pypdf", "PyPDF2"]:
        try:
            mod = __import__(mod_name)
            reader = mod.PdfReader(str(path))
            text = "\n".join([(p.extract_text() or "") for p in reader.pages])
            return remove_word_toc(text)
        except Exception as e:
            errors.append(f"{mod_name}: {e}")
    raise RuntimeError("Impossible d'extraire le PDF : " + " | ".join(errors))


def extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".docx":
        return extract_docx(path)
    if ext == ".pdf":
        return extract_pdf(path)
    if ext in [".txt", ".md"]:
        return remove_word_toc(path.read_text(encoding="utf-8", errors="ignore"))
    raise HTTPException(status_code=400, detail="Format accepté : .docx, .pdf, .txt, .md")


# =============================================================================
# Détection sections CIR final
# =============================================================================

def _search_first(text: str, patterns: list[str], start_pos: int = 0) -> Optional[re.Match]:
    best = None
    for p in patterns:
        m = re.search(p, text[start_pos:], flags=re.I | re.M)
        if not m:
            continue
        absolute_start = start_pos + m.start()
        if best is None or absolute_start < start_pos + best.start():
            # recrée une recherche absolue plus simple à gérer
            best = re.search(p, text[absolute_start:], flags=re.I | re.M)
            if best:
                best._absolute_start = absolute_start  # type: ignore[attr-defined]
                best._absolute_end = absolute_start + best.end()  # type: ignore[attr-defined]
    return best


def find_section(text: str, start_patterns: list[str], end_patterns: list[str]) -> str:
    text = remove_word_toc(text)

    start_match = None
    start = -1
    for p in start_patterns:
        m = re.search(p, text, flags=re.I | re.M)
        if m:
            if start < 0 or m.start() < start:
                start_match = m
                start = m.start()

    if start_match is None or start < 0:
        return ""

    search_from = max(start + 40, start_match.end())
    end = len(text)

    for p in end_patterns:
        m = re.search(p, text[search_from:], flags=re.I | re.M)
        if m:
            candidate_end = search_from + m.start()
            if candidate_end > start and candidate_end < end:
                end = candidate_end

    section = clean_text(text[start:end])

    # Sécurité : ne jamais sauvegarder un extrait de sommaire comme style.
    if _contains_toc_noise(section):
        section = remove_word_toc(section)
    if _contains_toc_noise(section):
        return ""

    return section


def sections_from_text(text: str) -> dict:
    text = remove_word_toc(text)

    etat_art = find_section(
        text,
        [
            r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?[ÉE]tat\s+de\s+l[’']art\b",
            r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Etat\s+de\s+l[’']art\b",
            r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Analyse\s+des\s+connaissances",
        ],
        [
            r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Insuffisances?\s+des\s+solutions",
            r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Verrous?\s+et\s+incertitudes",
            r"(?:^|\n)\s*1\.4\.?\s+",
        ],
    )

    insuff = find_section(
        text,
        [
            r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Insuffisances?\s+des\s+solutions",
            r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Limites\s+des\s+solutions",
        ],
        [
            r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Verrous?\s+et\s+incertitudes",
            r"(?:^|\n)\s*1\.4\.?\s+",
        ],
    )

    verrous = find_section(
        text,
        [
            r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Verrous?\s+et\s+incertitudes",
            r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Verrou\s+central",
        ],
        [
            r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?D[ée]marche\s+exp[ée]rimentale",
            r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Travaux\s+R[& ]?D",
            r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Travaux\s+r[ée]alis[ée]s",
            r"(?:^|\n)\s*1\.5\.?\s+",
        ],
    )

    return {
        "etat_art": etat_art,
        "insuffisances": insuff,
        "verrous": verrous,
    }


# =============================================================================
# Mémoire de style
# =============================================================================

def append_style_memory(organisme: str, project: str, year: str, filename: str, sections: dict) -> dict:
    path = root() / "storage" / "organismes" / safe_name(organisme, "organisme_unknown") / "cir_style_memory" / "style_memory.json"
    memory = read_json(path, {"version": "v58_style_memory", "examples": []})

    if not isinstance(memory, dict):
        memory = {"version": "v58_style_memory", "examples": []}
    if not isinstance(memory.get("examples"), list):
        memory["examples"] = []

    # Nettoyage automatique des anciens mauvais exemples contenant PAGEREF/_Toc.
    memory["examples"] = [
        e for e in memory["examples"]
        if isinstance(e, dict) and not _contains_toc_noise(str(e.get("text") or ""))
    ]

    added = []
    role_map = {
        "etat_art": "etat_art",
        "insuffisances": "limite",
        "verrous": "verrou",
    }

    for key, role in role_map.items():
        txt = clean_text(sections.get(key) or "")
        if len(txt) < 150:
            continue
        if _contains_toc_noise(txt):
            continue

        memory["examples"].append({
            "example_id": f"{safe_name(project, 'project')}_{year}_{role}_{len(memory['examples']) + 1}",
            "organisme": organisme,
            "project": project,
            "year": year,
            "role": role,
            "section_key": key,
            "section_title": key,
            "text": txt[:14000],
            "source_file": filename,
            "domain_key": "unknown",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "warning": "Style uniquement. Ne pas utiliser comme preuve factuelle.",
        })
        added.append({"role": role, "section": key, "chars": len(txt)})

    memory["version"] = "v58_style_memory"
    memory["updated_at"] = datetime.now().isoformat(timespec="seconds")
    write_json(path, memory)

    return {
        "used": bool(added),
        "path": str(path),
        "examples_added": added,
        "examples_total": len(memory["examples"]),
    }


# =============================================================================
# Endpoints
# =============================================================================

@router.post("/{project_id}/cir-final-consultant/upload")
async def upload_cir_final_consultant(
    project_id: int,
    file: UploadFile = File(...),
    organisme: str = Form("organisme_unknown"),
    project: str = Form("project_unknown"),
    year: str = Form("unknown"),
):
    filename = file.filename or "cir_final_consultant.docx"
    ext = Path(filename).suffix.lower()

    if ext not in [".docx", ".pdf", ".txt", ".md"]:
        raise HTTPException(status_code=400, detail="Format accepté : .docx, .pdf, .txt, .md")

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = root() / "storage" / "projects" / str(project_id) / "cir_final_consultant" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    saved = run_dir / filename
    with saved.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        text = extract_text(saved)
        text = remove_word_toc(text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Extraction impossible : {e}")

    if len(text) < 200:
        raise HTTPException(status_code=400, detail="Texte extrait trop court.")

    sections = sections_from_text(text)
    style = append_style_memory(organisme, project, year, filename, sections)

    warnings = []
    if _contains_toc_noise(text[:2500]):
        warnings.append("Le début du texte contient encore des traces de sommaire Word.")
    for key, value in sections.items():
        if _contains_toc_noise(value):
            warnings.append(f"La section {key} contient encore PAGEREF/_Toc et n'a pas été ajoutée au style.")

    cir_memory_path = run_dir / "cir_final_memory.json"
    write_json(cir_memory_path, {
        "project_id": project_id,
        "organisme": organisme,
        "project": project,
        "year": year,
        "source_file": filename,
        "text_chars": len(text),
        "sections": sections,
        "usage": "mémoire N-1 et style uniquement, pas document brut de diagnostic",
        "warnings": warnings,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    })

    report = {
        "status": "success",
        "version": "v58_cir_final_consultant_toc_fix",
        "project_id": project_id,
        "run_id": run_id,
        "file": {
            "name": filename,
            "path": str(saved),
            "size_bytes": saved.stat().st_size,
        },
        "extraction": {
            "text_chars": len(text),
            "text_preview": text[:1200],
            "toc_noise_detected_in_preview": _contains_toc_noise(text[:1200]),
        },
        "detected_sections": {
            k: {
                "found": bool(v),
                "chars": len(v or ""),
                "preview": (v or "")[:700],
                "toc_noise": _contains_toc_noise(v or ""),
            }
            for k, v in sections.items()
        },
        "style_memory": style,
        "cir_memory": {"used": True, "path": str(cir_memory_path)},
        "warnings": warnings,
        "usage_warning": "Ne pas mélanger ce CIR final avec les documents bruts du diagnostic.",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }

    write_json(run_dir / "cir_final_consultant_report.json", report)
    return report


@router.get("/{project_id}/cir-final-consultant/latest")
def latest_cir_final_consultant(project_id: int):
    base = root() / "storage" / "projects" / str(project_id) / "cir_final_consultant"
    reports = list(base.glob("*/cir_final_consultant_report.json"))
    if not reports:
        return {"status": "empty", "project_id": project_id}
    latest = sorted(reports, key=lambda p: p.stat().st_mtime, reverse=True)[0]
    return read_json(latest, {"status": "error"})


@router.post("/{project_id}/cir-previous/upload-final")
async def upload_alias(
    project_id: int,
    file: UploadFile = File(...),
    organisme: str = Form("organisme_unknown"),
    project: str = Form("project_unknown"),
    year: str = Form("unknown"),
):
    return await upload_cir_final_consultant(project_id, file, organisme, project, year)


@router.get("/{project_id}/cir-previous")
def latest_alias(project_id: int):
    return latest_cir_final_consultant(project_id)
