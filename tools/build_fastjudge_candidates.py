# -*- coding: utf-8 -*-
"""
build_fastjudge_candidates.py
------------------------------------------------------------
ÉTAPE 2 FastJudge : génération des candidats candidates.jsonl.

Objectif :
- parcourir C:\\EnnoSmart\\projects\\projet_x_
- lire metadata_fastjudge.json
- extraire/charger du texte depuis raw/, cir_final/, extracted/
- segmenter en passages courts
- produire annotations/candidates.jsonl par projet + fichier global.

Usage :
cd C:\\EnnoSmart
python tools\\build_fastjudge_candidates.py --projects-dir C:\\EnnoSmart\\projects

Avec extraction automatique si aucun texte extrait n'existe :
python tools\\build_fastjudge_candidates.py --projects-dir C:\\EnnoSmart\\projects --use-extraction
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

SUPPORTED_SOURCE_EXT = {
    ".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".csv",
    ".eml", ".msg", ".txt", ".png", ".jpg", ".jpeg"
}
TEXT_LIKE_EXT = {".txt", ".md", ".json", ".jsonl"}
MIN_CHARS = 35
MAX_CHARS = 1400
MAX_CANDIDATES_PER_DOC = 180


def read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            count += 1
    return count


def stable_id(*parts: Any, length: int = 16) -> str:
    s = "||".join(str(p or "") for p in parts)
    return hashlib.md5(s.encode("utf-8", errors="ignore")).hexdigest()[:length]


def norm_space(s: Any) -> str:
    s = str(s or "").replace("\u00a0", " ").replace("\ufeff", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def strip_noise_markers(s: str) -> str:
    s = str(s or "")
    s = re.sub(r"\[PAGE\s*\d+\]", " ", s, flags=re.I)
    s = re.sub(r"\[OCR\s*:[^\]]+\]", " ", s, flags=re.I)
    s = re.sub(r"\[SECTION\s*:\s*[^\]]+\]", " ", s, flags=re.I)
    s = re.sub(r"\bPAGE\s+\d+\b", " ", s, flags=re.I)
    s = re.sub(r"\bFigure\s+\d+\s*[:\-]?", " ", s, flags=re.I)
    s = re.sub(r"\bTableau\s+\d+\s*[:\-]?", " ", s, flags=re.I)
    return norm_space(s)


def safe_rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except Exception:
        return str(path)


def import_extraction_router():
    try:
        root = Path.cwd()
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from modules.extraction.router import extract
        return extract, None
    except Exception:
        return None, traceback.format_exc()


def obj_to_plain(obj: Any) -> Any:
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, list):
        return [obj_to_plain(x) for x in obj]
    if isinstance(obj, tuple):
        return [obj_to_plain(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): obj_to_plain(v) for k, v in obj.items()}
    if hasattr(obj, "to_dict"):
        try:
            return obj_to_plain(obj.to_dict())
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        try:
            return {str(k): obj_to_plain(v) for k, v in vars(obj).items()}
        except Exception:
            pass
    return str(obj)


def extract_text_chunks_with_router(file_path: Path, extract_fn) -> List[str]:
    result = extract_fn(str(file_path))
    plain = obj_to_plain(result)
    chunks = plain.get("text_chunks") if isinstance(plain, dict) else []
    texts = []
    for c in chunks or []:
        if isinstance(c, str):
            txt = c
        elif isinstance(c, dict):
            txt = c.get("text") or c.get("content") or c.get("raw_text") or ""
        else:
            txt = str(c or "")
        txt = norm_space(txt)
        if txt:
            texts.append(txt)
    return texts


def collect_files(folder: Path, exts: Optional[set[str]] = None) -> List[Path]:
    if not folder.exists():
        return []
    files = []
    for p in folder.rglob("*"):
        if not p.is_file():
            continue
        if exts and p.suffix.lower() not in exts:
            continue
        if "annotations" in [part.lower() for part in p.parts]:
            continue
        files.append(p)
    return sorted(files)


def read_text_file(path: Path) -> str:
    for enc in ["utf-8", "latin-1"]:
        try:
            return path.read_text(encoding=enc, errors="ignore")
        except Exception:
            pass
    return ""


def extract_texts_from_json(path: Path) -> List[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return []
    out: List[str] = []

    def walk(x: Any):
        if x is None:
            return
        if isinstance(x, str):
            if len(x.strip()) > 30:
                out.append(x)
            return
        if isinstance(x, list):
            for it in x:
                walk(it)
            return
        if isinstance(x, dict):
            for k in ["text", "content", "raw_text", "page_text", "markdown"]:
                if isinstance(x.get(k), str) and len(x.get(k, "").strip()) > 30:
                    out.append(x[k])
            for k in ["text_chunks", "chunks", "pages", "paragraphs", "sections", "items"]:
                if k in x:
                    walk(x[k])
    walk(data)
    return [norm_space(t) for t in out if norm_space(t)]


def load_existing_extracted_texts(project_dir: Path, source_file: Optional[Path] = None) -> List[Tuple[str, str]]:
    extracted_dir = project_dir / "extracted"
    if not extracted_dir.exists():
        return []
    candidates = collect_files(extracted_dir, TEXT_LIKE_EXT)
    if source_file:
        stem = source_file.stem.lower()
        candidates = [p for p in candidates if stem in p.stem.lower() or stem in str(p).lower()]
    texts: List[Tuple[str, str]] = []
    for p in candidates:
        if p.suffix.lower() == ".json":
            for txt in extract_texts_from_json(p):
                texts.append((p.name, txt))
        else:
            txt = read_text_file(p)
            if txt.strip():
                texts.append((p.name, txt))
    return texts


def load_texts_for_source_file(project_dir: Path, source_file: Path, use_extraction: bool, extract_fn=None) -> Tuple[List[str], str]:
    if source_file.suffix.lower() in {".txt", ".md"}:
        txt = read_text_file(source_file)
        if txt.strip():
            return [txt], "direct_text_file"
    if source_file.suffix.lower() == ".json":
        chunks = extract_texts_from_json(source_file)
        if chunks:
            return chunks, "direct_json_file"
    extracted = load_existing_extracted_texts(project_dir, source_file=source_file)
    if extracted:
        return [t for _, t in extracted], "existing_extracted_linked"
    if use_extraction and extract_fn is not None and source_file.suffix.lower() in SUPPORTED_SOURCE_EXT:
        try:
            chunks = extract_text_chunks_with_router(source_file, extract_fn)
            if chunks:
                return chunks, "extraction_router"
        except Exception:
            return [], "extraction_router_error"
    return [], "no_text_available"


def split_into_paragraphs(text: str) -> List[str]:
    text = strip_noise_markers(text)
    text = re.sub(r"\r\n", "\n", text)
    parts = re.split(r"\n\s*\n+|(?:\n\s*[-•]\s+)|(?:\n\s*\d+\.\s+)", text)
    return [norm_space(p) for p in parts if norm_space(p)]


def split_into_sentences(paragraph: str) -> List[str]:
    p = norm_space(paragraph)
    if not p:
        return []
    parts = re.split(r"(?<=[\.\!\?])\s+(?=[A-ZÀÂÇÉÈÊËÎÏÔÙÛÜ0-9])", p)
    out = []
    for part in parts:
        part = norm_space(part)
        if not part:
            continue
        if len(part) <= MAX_CHARS:
            out.append(part)
        else:
            sub = re.split(r"\s*;\s+|\s+\|\s+|(?<=:)\s+", part)
            buffer = ""
            for s in sub:
                s = norm_space(s)
                if not s:
                    continue
                if len(buffer) + len(s) + 1 <= MAX_CHARS:
                    buffer = (buffer + " " + s).strip()
                else:
                    if buffer:
                        out.append(buffer)
                    buffer = s
            if buffer:
                out.append(buffer)
    return out


def is_obvious_noise(text: str) -> bool:
    s = norm_space(text)
    n = s.lower()
    if len(s) < MIN_CHARS:
        return True
    if len(s) < 160 and any(b in n for b in ["sommaire", "table des matières", "remerciements", "bibliographie", "annexe", "copyright", "confidentiel", "page "]):
        return True
    if re.fullmatch(r"\d{1,3}/\d{1,3}.*", s):
        return True
    if re.fullmatch(r"\(?[A-Za-zÀ-ÿ ]+,\s*20\d{2}\)?", s):
        return True
    digit_ratio = sum(ch.isdigit() for ch in s) / max(1, len(s))
    if digit_ratio > 0.45 and len(s) < 300:
        return True
    return False


def weak_candidate_role(text: str) -> str:
    s = norm_space(text)
    n = s.lower()
    if any(k in n for k in ["devront être poursuivis", "devront etre poursuivis", "à confirmer", "a confirmer", "semble", "probablement", "pourrait", "incertain", "pas encore", "ne semble pas"]):
        return "limite"
    if any(k in n for k in ["résultat", "resultat", "montre", "montrent", "observé", "observe", "observée", "observations", "données montrent", "donnees montrent", "aucune différence", "aucune difference", "différence significative", "difference significative", "augmentation", "diminution", "révèle", "revele"]):
        return "resultat"
    if any(k in n for k in ["verrou", "difficulté", "difficulte", "problème", "probleme", "incertitude", "manque de", "absence de", "ne permet pas", "pas suffisant", "complexe", "complexité", "limite", "limites", "n'a pas permis", "n’a pas permis"]):
        return "verrou"
    if any(k in n for k in ["objectif", "objectifs", "vise à", "vise a", "a pour but", "afin de", "le projet vise", "l’étude vise", "l'etude vise", "tester l", "évaluer l", "evaluer l", "caractériser", "caracteriser", "développer", "developper", "améliorer", "ameliorer"]):
        if re.search(r"^\s*(un|une|le|la)?\s*(comptage|mesure|analyse)\b", s, flags=re.I):
            return "methode"
        return "objectif"
    if any(k in n for k in ["mesure", "mesurer", "comptage", "compter", "réalisé", "realise", "réaliser", "realiser", "effectué", "effectue", "tester", "comparer", "suivre", "installer", "appliquer", "calculer", "prélever", "prelever", "échantillon", "echantillon", "protocole"]):
        return "methode"
    if any(k in n for k in ["variables suivies", "paramètres mesurés", "parametres mesures", "grandeurs mesurées", "grandeurs mesurees", "taux de", "calibre", "rendement", "humidité", "humidite", "température", "temperature", "pression", "fréquence", "frequence", "diamètre", "diametre"]):
        return "variable"
    if any(k in n for k in ["contribution", "apport", "innovation", "nouvelle approche", "nouveau protocole"]):
        return "contribution"
    if any(k in n for k in ["hypothèse", "hypothese", "nous supposons", "on suppose", "il est possible", "il est probable", "on peut envisager"]):
        return "hypothese"
    return "unknown"


def candidate_quality_score(text: str, candidate_role: str) -> float:
    s = norm_space(text)
    score = 0.45
    if candidate_role != "unknown":
        score += 0.25
    if len(s) >= 80:
        score += 0.10
    if len(s) > 600:
        score -= 0.10
    if is_obvious_noise(s):
        score = 0.05
    if any(m in s.lower() for m in ["objectif", "verrou", "difficulté", "difficulte", "incertitude", "essai", "mesure", "résultat", "resultat", "méthode", "methode", "protocole", "limite", "contribution", "innovation"]):
        score += 0.10
    return round(max(0.0, min(1.0, score)), 3)


def build_context(sentences: List[str], idx: int, window: int = 1) -> Tuple[str, str]:
    before = " ".join(sentences[max(0, idx - window): idx])
    after = " ".join(sentences[idx + 1: idx + 1 + window])
    return before[:700], after[:700]


def infer_cir_section(text: str) -> str:
    n = text.lower()
    if "objectif" in n or "vise" in n or "but" in n:
        return "objectif"
    if "verrou" in n or "difficult" in n or "incertitude" in n or "problème" in n or "probleme" in n:
        return "verrou"
    if "état de l'art" in n or "etat de l'art" in n or "bibliographie" in n:
        return "etat_art"
    if "travaux" in n or "démarche" in n or "demarche" in n or "méthode" in n or "methode" in n:
        return "demarche"
    if "résultat" in n or "resultat" in n or "conclusion" in n:
        return "resultat"
    return "unknown"


def segment_text_to_candidates(text: str, project_meta: Dict[str, Any], source_file: Path, source_type: str, read_mode: str, project_dir: Path, max_candidates: int) -> List[Dict[str, Any]]:
    project_id = project_meta.get("project_id") or project_dir.name
    project_type = project_meta.get("project_type") or "unknown"
    domain = project_meta.get("domain", "to_detect_by_nlp")
    sub_domain = project_meta.get("sub_domain", "to_detect_by_nlp")
    paragraphs = split_into_paragraphs(text)
    rows: List[Dict[str, Any]] = []
    seen = set()
    for paragraph_index, para in enumerate(paragraphs, 1):
        sentences = split_into_sentences(para)
        for i, sent in enumerate(sentences):
            sent = strip_noise_markers(sent)
            if not sent or len(sent) < MIN_CHARS:
                continue
            if len(sent) > MAX_CHARS:
                sent = sent[:MAX_CHARS].strip()
            sig = hashlib.md5(sent.lower().encode("utf-8", errors="ignore")).hexdigest()
            if sig in seen:
                continue
            seen.add(sig)
            role = weak_candidate_role(sent)
            is_noise = is_obvious_noise(sent)
            if is_noise and role == "unknown":
                role = "bruit"
            context_before, context_after = build_context(sentences, i)
            row = {
                "candidate_id": stable_id(project_id, source_type, source_file.name, paragraph_index, i, sent),
                "project_id": project_id,
                "project_type": project_type,
                "source_type": source_type,
                "source_doc": source_file.name,
                "source_path": str(source_file),
                "source_relpath": safe_rel(source_file, project_dir),
                "read_mode": read_mode,
                "paragraph_index": paragraph_index,
                "sentence_index": i,
                "text": sent,
                "context_before": context_before,
                "context_after": context_after,
                "candidate_role": role,
                "role_gold": None,
                "sub_role_gold": None,
                "keep_gold": None,
                "useful_for_cir_gold": None,
                "linked_final_section": None,
                "linked_final_text": None,
                "domain": domain,
                "sub_domain": sub_domain,
                "quality_score": candidate_quality_score(sent, role),
                "is_obvious_noise": is_noise,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "dataset_version": "fastjudge_candidates_v1",
            }
            if source_type == "cir_final":
                section = infer_cir_section(sent)
                row["cir_final_section_candidate"] = section
                if row["candidate_role"] == "unknown" and section in {"objectif", "verrou", "resultat"}:
                    row["candidate_role"] = section
            rows.append(row)
            if len(rows) >= max_candidates:
                break
        if len(rows) >= max_candidates:
            break
    rows.sort(key=lambda x: (-float(x["quality_score"]), x["source_doc"], x["paragraph_index"], x["sentence_index"]))
    return rows


def list_source_files(project_dir: Path, source_type: str) -> List[Path]:
    folder = project_dir / source_type
    if source_type == "cir_final":
        folder = project_dir / "cir_final"
    return collect_files(folder, SUPPORTED_SOURCE_EXT)


def summarize_candidates(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_source, by_role, by_doc = {}, {}, {}
    for r in rows:
        by_source[r.get("source_type", "unknown")] = by_source.get(r.get("source_type", "unknown"), 0) + 1
        by_role[r.get("candidate_role", "unknown")] = by_role.get(r.get("candidate_role", "unknown"), 0) + 1
        by_doc[r.get("source_doc", "unknown")] = by_doc.get(r.get("source_doc", "unknown"), 0) + 1
    return {
        "total_candidates": len(rows),
        "by_source_type": by_source,
        "by_candidate_role": by_role,
        "by_source_doc": by_doc,
        "high_quality_candidates": sum(1 for r in rows if float(r.get("quality_score", 0)) >= 0.70),
        "noise_candidates": sum(1 for r in rows if r.get("candidate_role") == "bruit" or r.get("is_obvious_noise")),
    }


def build_candidates_for_project(project_dir: Path, use_extraction: bool, extract_fn=None, include_extracted_standalone: bool = False, max_candidates_per_doc: int = MAX_CANDIDATES_PER_DOC) -> Dict[str, Any]:
    meta = read_json(project_dir / "metadata_fastjudge.json")
    if not meta:
        return {"project_id": project_dir.name, "project_type": "missing_metadata_fastjudge", "candidates": [], "stats": {"error": "metadata_fastjudge.json introuvable"}}
    annotations_dir = project_dir / "annotations"
    annotations_dir.mkdir(parents=True, exist_ok=True)
    candidates: List[Dict[str, Any]] = []
    source_stats: List[Dict[str, Any]] = []
    for source_type in ["raw", "cir_final"]:
        for f in list_source_files(project_dir, source_type):
            chunks, read_mode = load_texts_for_source_file(project_dir, f, use_extraction, extract_fn)
            file_rows: List[Dict[str, Any]] = []
            for chunk in chunks:
                file_rows.extend(segment_text_to_candidates(chunk, meta, f, source_type, read_mode, project_dir, max_candidates_per_doc))
            candidates.extend(file_rows)
            source_stats.append({"source_type": source_type, "source_doc": f.name, "read_mode": read_mode, "chunks_count": len(chunks), "candidates_count": len(file_rows)})
    if include_extracted_standalone:
        for f in collect_files(project_dir / "extracted", TEXT_LIKE_EXT):
            if f.name in {"candidates.jsonl", "role_classification.jsonl", "raw_cir_alignment.jsonl"}:
                continue
            chunks = extract_texts_from_json(f) if f.suffix.lower() == ".json" else [read_text_file(f)]
            file_rows = []
            for chunk in chunks:
                file_rows.extend(segment_text_to_candidates(chunk, meta, f, "extracted", "standalone_extracted", project_dir, max_candidates_per_doc))
            candidates.extend(file_rows)
            source_stats.append({"source_type": "extracted", "source_doc": f.name, "read_mode": "standalone_extracted", "chunks_count": len(chunks), "candidates_count": len(file_rows)})
    deduped, seen = [], set()
    for c in sorted(candidates, key=lambda x: (-float(x.get("quality_score", 0)), x.get("source_type", ""), x.get("source_doc", ""))):
        key = (c.get("source_type"), c.get("source_doc"), hashlib.md5(c.get("text", "").lower().encode("utf-8", errors="ignore")).hexdigest())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)
    candidates = deduped
    stats = summarize_candidates(candidates)
    stats["source_stats"] = source_stats
    stats["project_id"] = meta.get("project_id")
    stats["project_type"] = meta.get("project_type")
    out_path = annotations_dir / "candidates.jsonl"
    write_jsonl(out_path, candidates)
    write_json(annotations_dir / "candidates_summary.json", stats)
    return {"project_id": meta.get("project_id"), "project_type": meta.get("project_type"), "candidates_path": str(out_path), "candidates": candidates, "stats": stats}


def parse_project_types(raw: str) -> set[str]:
    parts = [p.strip() for p in (raw or "").split(",") if p.strip()]
    return set(parts) if parts else {"raw_plus_cir", "raw_only", "cir_only"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--projects-dir", required=True, help="Dossier C:\\EnnoSmart\\projects")
    parser.add_argument("--project-types", default="raw_plus_cir,raw_only,cir_only")
    parser.add_argument("--use-extraction", action="store_true")
    parser.add_argument("--include-extracted-standalone", action="store_true")
    parser.add_argument("--max-candidates-per-doc", type=int, default=MAX_CANDIDATES_PER_DOC)
    parser.add_argument("--limit-projects", type=int, default=0)
    parser.add_argument("--only-project", default="")
    args = parser.parse_args()
    projects_dir = Path(args.projects_dir)
    if not projects_dir.exists():
        raise FileNotFoundError(f"Dossier introuvable : {projects_dir}")
    allowed_types = parse_project_types(args.project_types)
    extract_fn = None
    if args.use_extraction:
        extract_fn, err = import_extraction_router()
        if extract_fn is None:
            print("ATTENTION : import extraction impossible. Utilisation extracted/txt/json seulement.")
            print(err)
    all_rows: List[Dict[str, Any]] = []
    project_summaries: List[Dict[str, Any]] = []
    project_dirs = [p for p in sorted(projects_dir.iterdir()) if p.is_dir()]
    if args.only_project:
        project_dirs = [p for p in project_dirs if p.name == args.only_project]
    processed = 0
    for project_dir in project_dirs:
        meta = read_json(project_dir / "metadata_fastjudge.json")
        if not meta:
            print(f"SKIP {project_dir.name} : metadata_fastjudge.json absent")
            continue
        ptype = meta.get("project_type", "unknown")
        if ptype not in allowed_types:
            continue
        if args.limit_projects and processed >= args.limit_projects:
            break
        print(f"\n=== {project_dir.name} | {ptype} ===")
        result = build_candidates_for_project(project_dir, bool(args.use_extraction), extract_fn, bool(args.include_extracted_standalone), int(args.max_candidates_per_doc))
        stats = result["stats"]
        print(f"Candidates : {stats.get('total_candidates', 0)}")
        print(f"Par source : {stats.get('by_source_type', {})}")
        print(f"Par rôle candidat : {stats.get('by_candidate_role', {})}")
        all_rows.extend(result["candidates"])
        project_summaries.append({"project_id": result["project_id"], "project_type": result["project_type"], "candidates_path": result.get("candidates_path"), **stats})
        processed += 1
    training_dir = projects_dir.parent / "data" / "training"
    training_dir.mkdir(parents=True, exist_ok=True)
    global_candidates_path = training_dir / "fastjudge_candidates_all.jsonl"
    global_summary_path = training_dir / "fastjudge_candidates_summary.json"
    write_jsonl(global_candidates_path, all_rows)
    global_summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "dataset_version": "fastjudge_candidates_v1",
        "projects_dir": str(projects_dir),
        "processed_projects": processed,
        "total_candidates": len(all_rows),
        "global_stats": summarize_candidates(all_rows),
        "projects": project_summaries,
        "next_step": "Annoter / aligner candidates.jsonl pour produire role_classification_dataset.jsonl",
    }
    write_json(global_summary_path, global_summary)
    print("\n" + "=" * 80)
    print("ÉTAPE 2 TERMINÉE")
    print(f"Projets traités : {processed}")
    print(f"Candidats globaux : {len(all_rows)}")
    print(f"Fichier global : {global_candidates_path}")
    print(f"Résumé global : {global_summary_path}")
    print("=" * 80)


if __name__ == "__main__":
    main()
