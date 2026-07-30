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


def cut_before_conclusion(text: str) -> str:
    """Empêche resultats_obtenus de contenir la conclusion/contribution."""
    text = clean_text(text or "")
    if not text:
        return ""

    stop_patterns = [
        r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Conclusion\s+et\s+contribution\s+scientifique",
        r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Conclusion\s+et\s+contribution",
        r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Contribution\s+scientifique",
        r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Conclusion\b",
    ]

    cut = len(text)
    for p in stop_patterns:
        m = re.search(p, text, flags=re.I | re.M)
        if m and 0 <= m.start() < cut:
            cut = m.start()

    return clean_text(text[:cut])


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


def _section_patterns() -> dict:
    """Patterns robustes pour les sections d'un CIR final / CIR précédent."""
    return {
        "objectifs_projet": {
            "starts": [
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Objectifs?\s+du\s+projet\b",
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Objectifs?\s+vis[ée]s?",
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Contexte\s+du\s+projet\b",
            ],
            "ends": [
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?[ÉE]tat\s+de\s+l[’']art\b",
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Etat\s+de\s+l[’']art\b",
                r"(?:^|\n)\s*1\.3\.?\s+",
            ],
        },
        "etat_art": {
            "starts": [
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?[ÉE]tat\s+de\s+l[’']art\b",
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Etat\s+de\s+l[’']art\b",
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Analyse\s+des\s+connaissances",
            ],
            "ends": [
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Insuffisances?\s+des\s+solutions",
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Verrous?\s+et\s+incertitudes",
                r"(?:^|\n)\s*1\.4\.?\s+",
            ],
        },
        "insuffisances": {
            "starts": [
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Insuffisances?\s+des\s+solutions",
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Limites\s+des\s+solutions",
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Limites\s+de\s+l[’'][ée]tat\s+de\s+l[’']art",
            ],
            "ends": [
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Verrous?\s+et\s+incertitudes",
                r"(?:^|\n)\s*1\.4\.?\s+",
            ],
        },
        "verrous": {
            "starts": [
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Verrous?\s+et\s+incertitudes",
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Verrou\s+central",
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Incertitudes?\s+scientifiques?",
            ],
            "ends": [
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?D[ée]marche\s+exp[ée]rimentale",
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Travaux\s+R[& ]?D",
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Travaux\s+r[ée]alis[ée]s",
                r"(?:^|\n)\s*1\.5\.?\s+",
            ],
        },
        "demarche_experimentale": {
            "starts": [
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?D[ée]marche\s+exp[ée]rimentale",
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Description\s+des\s+travaux\s+ant[ée]rieurs",
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?M[ée]thodologie\s+exp[ée]rimentale",
            ],
            "ends": [
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Description\s+des\s+travaux\s+r[ée]alis[ée]s\s+en\s+\d{4}",
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Travaux\s+r[ée]alis[ée]s\s+en\s+\d{4}",
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?R[ée]sultats?\s+obtenus",
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Conclusion\s+et\s+contribution",
                r"(?:^|\n)\s*1\.5\.2\.?\s+",
                r"(?:^|\n)\s*1\.6\.?\s+",
            ],
        },
        "travaux_realises": {
            "starts": [
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Description\s+des\s+travaux\s+r[ée]alis[ée]s\s+en\s+\d{4}",
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Travaux\s+r[ée]alis[ée]s\s+en\s+\d{4}",
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Description\s+des\s+op[ée]rations\s+R[& ]?D",
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Travaux\s+R[& ]?D\s+r[ée]alis[ée]s",
            ],
            "ends": [
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Conclusion\s+et\s+contribution",
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Conclusion\b",
                r"(?:^|\n)\s*1\.6\.?\s+",
            ],
        },
        "conclusion_contribution": {
            "starts": [
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Conclusion\s+et\s+contribution\s+scientifique",
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Conclusion\s+et\s+contribution",
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Contribution\s+scientifique",
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Conclusion\b",
            ],
            "ends": [
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Annexes?\b",
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Bibliographie\b",
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Documents?\s+associ[ée]s",
            ],
        },
    }


def extract_result_paragraphs(text: str, travaux_text: str = "", conclusion_text: str = "") -> str:
    """
    Construit une section 'résultats obtenus' sans hallucination.
    Quand le CIR n'a pas de titre global 'Résultats', on extrait uniquement les paragraphes
    qui existent dans le document et qui contiennent des marqueurs de résultats/validation.
    """
    base = clean_text(travaux_text or "")
    if not base:
        base = clean_text(text or "")

    explicit = find_section(
        text,
        [
            r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?R[ée]sultats?\s+obtenus\b",
            r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?R[ée]sultats?\s+des\s+travaux\b",
            r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?R[ée]sultats?\s+exp[ée]rimentaux\b",
        ],
        [
            r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Conclusion\b",
            r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?Contribution\b",
            r"(?:^|\n)\s*1\.6\.?\s+",
        ],
    )
    if explicit and len(explicit) > 250:
        return cut_before_conclusion(explicit)

    markers = [
        r"\br[ée]sultats?\b",
        r"\bessais?\b",
        r"\btests?\b",
        r"\bmesures?\b",
        r"\bvalidation\b",
        r"\bvalid[ée]e?s?\b",
        r"\bmontr[ée]e?s?\b",
        r"\bpermis\b",
        r"\ba\s+permis\b",
        r"\bnous\s+avons\s+(?:obtenu|valid[ée]|constat[ée]|montr[ée]|pu)\b",
        r"\bconclusion(?:s)?\s+suivantes?\b",
        r"\ba\s+[ée]t[ée]\s+(?:valid[ée]|observ[ée]|mesur[ée]|confirm[ée])\b",
    ]

    # L'extraction DOCX produit souvent une ligne par paragraphe, pas forcément des doubles sauts.
    # On coupe donc par ligne pour éviter de prendre tout le bloc travaux comme un seul paragraphe.
    chunks = re.split(r"\n+", base)
    selected: list[str] = []
    seen: set[str] = set()

    for para in chunks:
        p = clean_text(para)
        if len(p) < 70:
            continue
        low = p.lower()
        if any(re.search(m, low, flags=re.I) for m in markers):
            signature = re.sub(r"\s+", " ", low[:220])
            if signature not in seen:
                seen.add(signature)
                selected.append(p)

    # IMPORTANT V60 : la conclusion reste uniquement dans conclusion_contribution.
    # On ne l'ajoute plus dans resultats_obtenus pour éviter les doublons.
    return cut_before_conclusion(clean_text("\n\n".join(selected)))[:16000]


def sections_from_text(text: str) -> dict:
    text = remove_word_toc(text)
    patterns = _section_patterns()

    sections: dict[str, str] = {}
    for key in [
        "objectifs_projet",
        "etat_art",
        "insuffisances",
        "verrous",
        "demarche_experimentale",
        "travaux_realises",
        "conclusion_contribution",
    ]:
        cfg = patterns[key]
        sections[key] = find_section(text, cfg["starts"], cfg["ends"])

    sections["resultats_obtenus"] = extract_result_paragraphs(
        text,
        travaux_text=sections.get("travaux_realises", ""),
        conclusion_text=sections.get("conclusion_contribution", ""),
    )

    # Ordre logique pour JSON, panel et mémoire.
    return {
        "objectifs_projet": sections.get("objectifs_projet", ""),
        "etat_art": sections.get("etat_art", ""),
        "insuffisances": sections.get("insuffisances", ""),
        "verrous": sections.get("verrous", ""),
        "demarche_experimentale": sections.get("demarche_experimentale", ""),
        "travaux_realises": sections.get("travaux_realises", ""),
        "resultats_obtenus": sections.get("resultats_obtenus", ""),
        "conclusion_contribution": sections.get("conclusion_contribution", ""),
    }



# =============================================================================
# Chemin canonique CIR par organisme / projet / année
# =============================================================================

def canonical_year_dir(organisme: str, project: str, year: str) -> Path:
    """
    Chemin unique métier.
    C'est CE chemin que doivent utiliser EnnoDiagnostic, comparaison N-1 et mémoire CIR.
    """
    return (
        root()
        / "storage"
        / "organismes"
        / safe_name(organisme, "organisme_unknown").lower()
        / "projects"
        / safe_name(project, "project_unknown").lower()
        / "years"
        / str(year)
    )


def canonical_cir_current_dir(organisme: str, project: str, year: str) -> Path:
    return canonical_year_dir(organisme, project, year) / "cir_final_consultant" / "current"


def canonical_cir_memory_path(organisme: str, project: str, year: str) -> Path:
    return canonical_year_dir(organisme, project, year) / "cir_final_memory.json"


def canonical_cir_extracted_path(organisme: str, project: str, year: str) -> Path:
    """
    Format lu par modules.CIR_MEMORY.cir_memory pour la comparaison N-1.
    """
    return canonical_year_dir(organisme, project, year) / "cir_final" / "cir_final_extracted.json"


def legacy_project_current_dir(project_id: int) -> Path:
    """
    Ancien chemin. On le garde seulement comme copie de compatibilité frontend.
    Le chemin source de vérité reste canonical_year_dir().
    """
    return root() / "storage" / "projects" / str(project_id) / "cir_final_consultant" / "current"


def sections_to_items(sections: dict, organisme: str, project: str, year: str, source_file: str) -> list[dict]:
    """
    Transforme les sections larges du CIR final en items comparables.
    Nécessaire parce que le module comparaison travaille sur une liste d'items.
    """
    role_map = {
        "objectifs_projet": "objectif",
        "etat_art": "etat_art",
        "insuffisances": "limite",
        "verrous": "verrou",
        "demarche_experimentale": "methode",
        "travaux_realises": "methode",
        "resultats_obtenus": "resultat",
        "conclusion_contribution": "contribution",
    }

    title_map = {
        "objectifs_projet": "Objectifs du projet",
        "etat_art": "État de l’art",
        "insuffisances": "Insuffisances des solutions existantes",
        "verrous": "Verrous et incertitudes",
        "demarche_experimentale": "Démarche expérimentale",
        "travaux_realises": "Travaux réalisés",
        "resultats_obtenus": "Résultats obtenus",
        "conclusion_contribution": "Conclusion et contribution",
    }

    items: list[dict] = []
    for key, text in (sections or {}).items():
        txt = clean_text(text or "")
        if len(txt) < 50:
            continue
        items.append({
            "item_id": f"{safe_name(project, 'project')}_{year}_{key}",
            "organisme": organisme,
            "project": project,
            "year": str(year),
            "role": role_map.get(key, key),
            "section_key": key,
            "section_type": key,
            "section_title": title_map.get(key, key),
            "text": txt,
            "source_file": source_file,
            "source_type": "previous_cir_final_without_frascati",
        })
    return items


def build_cir_memory_payload(project_id: int, organisme: str, project: str, year: str, filename: str, text: str, sections: dict, warnings: list[str]) -> dict:
    items = sections_to_items(sections, organisme, project, year, filename)
    return {
        "version": "v61_canonical_cir_memory",
        "project_id": project_id,
        "organisme": organisme,
        "project": project,
        "year": str(year),
        "source_file": filename,
        "text_chars": len(text or ""),
        "sections": sections,
        "items": items,
        "items_count": len(items),
        "section_lengths": {k: len(v or "") for k, v in (sections or {}).items()},
        "section_modes": {
            "resultats_obtenus": "extraction_selective_resultats_sans_conclusion"
        },
        "usage": "mémoire N-1 complète et style uniquement, pas document brut de diagnostic",
        "storage_mode": "canonical_organisme_project_year",
        "warnings": warnings,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }

# =============================================================================
# Mémoire de style
# =============================================================================

def append_style_memory(organisme: str, project: str, year: str, filename: str, sections: dict) -> dict:
    path = root() / "storage" / "organismes" / safe_name(organisme, "organisme_unknown") / "cir_style_memory" / "style_memory.json"
    memory = read_json(path, {"version": "v60_style_memory", "examples": []})

    if not isinstance(memory, dict):
        memory = {"version": "v60_style_memory", "examples": []}
    if not isinstance(memory.get("examples"), list):
        memory["examples"] = []

    # Nettoyage automatique des anciens mauvais exemples contenant PAGEREF/_Toc.
    memory["examples"] = [
        e for e in memory["examples"]
        if isinstance(e, dict) and not _contains_toc_noise(str(e.get("text") or ""))
    ]

    added = []
    role_map = {
        "objectifs_projet": "objectif",
        "etat_art": "etat_art",
        "insuffisances": "limite",
        "verrous": "verrou",
        "demarche_experimentale": "demarche",
        "travaux_realises": "travaux",
        "resultats_obtenus": "resultat",
        "conclusion_contribution": "conclusion",
    }

    for key, role in role_map.items():
        txt = clean_text(sections.get(key) or "")
        if len(txt) < 150:
            continue
        if _contains_toc_noise(txt):
            continue

        # Éviter les doublons quand on ré-uploade le même CIR précédent.
        memory["examples"] = [
            e for e in memory["examples"]
            if not (
                isinstance(e, dict)
                and str(e.get("organisme") or "") == str(organisme)
                and str(e.get("project") or "") == str(project)
                and str(e.get("year") or "") == str(year)
                and str(e.get("source_file") or "") == str(filename)
                and str(e.get("section_key") or "") == str(key)
            )
        ]

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

    memory["version"] = "v60_style_memory"
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

    # V61 : chemin source de vérité = organisme / projet / année.
    # L'ancien storage/projects/{id} reste uniquement une copie de compatibilité.
    run_id = "current"

    canonical_dir = canonical_cir_current_dir(organisme, project, year)
    if canonical_dir.exists():
        shutil.rmtree(canonical_dir)
    canonical_dir.mkdir(parents=True, exist_ok=True)

    saved = canonical_dir / filename
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

    cir_memory = build_cir_memory_payload(
        project_id=project_id,
        organisme=organisme,
        project=project,
        year=year,
        filename=filename,
        text=text,
        sections=sections,
        warnings=warnings,
    )

    # 1) Mémoire canonique lisible par l'interface et les outils projet/année
    canonical_memory_path = canonical_cir_memory_path(organisme, project, year)
    write_json(canonical_memory_path, cir_memory)

    # 2) Format directement lu par modules.CIR_MEMORY pour la comparaison N-1
    canonical_extracted_path = canonical_cir_extracted_path(organisme, project, year)
    write_json(canonical_extracted_path, cir_memory)

    report = {
        "status": "success",
        "version": "v62_canonical_cir_previous_memory_no_legacy",
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
        "sections_full": sections,
        "cir_memory": {
            "used": True,
            "canonical_memory_path": str(canonical_memory_path),
            "canonical_extracted_path": str(canonical_extracted_path),
            "items_count": len(cir_memory.get("items") or []),
        },
        "storage": {
            "mode": "canonical_organisme_project_year",
            "canonical_directory": str(canonical_dir),
            "legacy_directory": None,
            "note": "Le chemin canonique est storage/organismes/{organisme}/projects/{project}/years/{year}. aucun dossier storage/projects/{id} n’est recréé."
        },
        "warnings": warnings,
        "usage_warning": "Ne pas mélanger ce CIR final avec les documents bruts du diagnostic.",
        "complete_memory_note": "Sections ajoutées pour comparaison N-1 : objectifs, démarche, travaux, résultats, conclusion/contribution.",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }

    canonical_report_path = canonical_dir / "cir_final_consultant_report.json"
    write_json(canonical_report_path, report)
    # V62 : aucune copie legacy dans storage/projects/{id}.
    # La source officielle est le chemin canonique organisme/projet/année.

    return report


@router.get("/{project_id}/cir-final-consultant/latest")
def latest_cir_final_consultant(project_id: int):
    base = root() / "storage" / "projects" / str(project_id) / "cir_final_consultant"
    current_report = base / "current" / "cir_final_consultant_report.json"
    if current_report.exists():
        return read_json(current_report, {"status": "error"})

    # Compatibilité avec les anciens runs V58/V59 déjà créés avant la V60.
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
