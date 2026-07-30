# -*- coding: utf-8 -*-
from __future__ import annotations

"""
scripts/ai_experience_memory_rag.py — V3 corrigé

But :
- Construire une base RAG géante d'expérience / connaissance / style CIR
- Sans backend
- Sans frontend React
- En utilisant les modules IA existants :
  modules.extraction.router
  modules.NLP.pipeline
  modules.NLP.CIR.cir_pipeline
  modules.RAG.vector_store
  modules.RAG.json_to_chunks

Correction V3 :
- Normalisation des rôles pour CIR final :
  le rôle principal du chunk reste le rôle structurel CIR.
  Frascati reste une métadonnée secondaire.
"""

import argparse
import dataclasses
import hashlib
import importlib
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from scripts.ai_experience_memory_rag_patch import normalize_cir_final_chunk_roles
except Exception:
    try:
        from ai_experience_memory_rag_patch import normalize_cir_final_chunk_roles
    except Exception:
        def normalize_cir_final_chunk_roles(chunks):
            return chunks


BASE_DIR = Path(os.getenv("ENNOSMART_BASE_DIR", r"C:\EnnoSmart"))
MEMORY_ROOT = Path(os.getenv("ENNOSMART_EXPERIENCE_MEMORY_DIR", str(BASE_DIR / "storage" / "experience_memory")))

CATALOG_PATH = MEMORY_ROOT / "catalog.json"
RUNS_DIR = MEMORY_ROOT / "runs"
CHUNKS_DIR = MEMORY_ROOT / "chunks"
CHROMA_DIR = MEMORY_ROOT / "chroma"

SUPPORTED_FILES = {
    ".pdf", ".docx", ".doc", ".pptx", ".ppt",
    ".xlsx", ".xls", ".xlsm", ".csv",
    ".txt", ".md",
    ".msg", ".eml",
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp",
}
JSON_EXTS = {".json"}

CIR_STRONG_WORDS = [
    "crédit impôt recherche", "credit impot recherche",
    "verrous et incertitudes", "verrous scientifiques", "verrous techniques",
    "état de l’art", "etat de l art",
    "démarche expérimentale", "demarche experimentale",
    "travaux r&d", "travaux rd",
    "conclusion et contribution",
]


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def ensure_dirs() -> None:
    for p in [MEMORY_ROOT, RUNS_DIR, CHUNKS_DIR, CHROMA_DIR]:
        p.mkdir(parents=True, exist_ok=True)


def strip_accents(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def slugify(value: Any, default: str = "unknown", max_len: int = 90) -> str:
    text = strip_accents(str(value or "").lower())
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return (text[:max_len] or default)


def clean_text(value: Any, max_chars: int = 0) -> str:
    text = str(value or "")
    text = text.replace("\x00", " ").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    if max_chars and len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + "..."
    return text


def norm_text(text: Any) -> str:
    text = strip_accents(str(text or "").lower())
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(1024 * 1024)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def jsonable(obj: Any) -> Any:
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, Path):
        return str(obj)
    if dataclasses.is_dataclass(obj):
        return jsonable(dataclasses.asdict(obj))
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [jsonable(v) for v in obj]
    if hasattr(obj, "model_dump"):
        try:
            return jsonable(obj.model_dump())
        except Exception:
            pass
    if hasattr(obj, "dict"):
        try:
            return jsonable(obj.dict())
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        try:
            return jsonable(vars(obj))
        except Exception:
            pass
    return str(obj)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(jsonable(data), ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        if not path.exists():
            return default if default is not None else {}
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else {}


def add_log(logs: List[Dict[str, Any]], step: str, status: str, message: str, **data: Any) -> None:
    logs.append({"time": now_iso(), "step": step, "status": status, "message": message, **jsonable(data)})


def iter_input_files(inputs: List[str]) -> List[Path]:
    files: List[Path] = []
    for raw in inputs or []:
        p = Path(raw).expanduser()
        if not p.exists():
            continue
        if p.is_file():
            if p.suffix.lower() in SUPPORTED_FILES | JSON_EXTS:
                files.append(p)
            continue
        for f in p.rglob("*"):
            if f.is_file() and f.suffix.lower() in SUPPORTED_FILES | JSON_EXTS:
                files.append(f)

    seen = set()
    out: List[Path] = []
    for f in sorted(files, key=lambda x: str(x).lower()):
        key = str(f.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


def import_any(names: List[str]):
    last_error = None
    for name in names:
        try:
            return importlib.import_module(name), name, None
        except Exception as exc:
            last_error = f"{name}: {exc}"
    return None, None, last_error


def extract_file_with_existing_module(path: Path, args: argparse.Namespace, logs: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], str]:
    mod, mod_name, err = import_any(["modules.extraction.router"])
    if mod is None:
        raise RuntimeError(f"Module extraction introuvable : {err}")
    if not hasattr(mod, "extract"):
        raise RuntimeError("modules.extraction.router.extract introuvable")

    add_log(logs, "extraction_import", "ok", "Module extraction chargé.", module=mod_name)

    source_tag = "ARCHIVE" if args.mode in {"cir_final", "auto"} else "DE_DOC"
    result = mod.extract(
        file_path=str(path),
        vision_mode=args.vision_mode,
        formula_mode=args.formula_mode,
        source_tag=source_tag,
    )

    data = jsonable(result)
    if not isinstance(data, dict):
        data = {"raw_extraction": data}

    chunks = []
    chunks.extend(list(getattr(result, "text_chunks", []) or []))
    chunks.extend(list(getattr(result, "visual_chunks", []) or []))
    text = clean_text("\n\n".join(str(x) for x in chunks if str(x).strip()))

    add_log(
        logs, "extraction", "ok" if len(text) >= 100 else "warning",
        "Extraction terminée.",
        file=str(path), text_chars=len(text),
        text_chunks=len(getattr(result, "text_chunks", []) or []),
        visual_chunks=len(getattr(result, "visual_chunks", []) or []),
        preview=clean_text(text[:800]),
    )
    return data, text


def run_cir_nlp_existing(document: Dict[str, Any], logs: List[Dict[str, Any]]) -> Dict[str, Any]:
    mod, mod_name, err = import_any([
        "modules.NLP.CIR.cir_pipeline",
        "modules.NLP.cir_pipeline",
        "NLP.CIR.cir_pipeline",
    ])
    if mod is None:
        raise RuntimeError(f"Pipeline CIR introuvable : {err}")
    fn = getattr(mod, "run_cir_pipeline", None) or getattr(mod, "run_pipeline", None)
    if not callable(fn):
        raise RuntimeError("run_cir_pipeline introuvable")

    add_log(logs, "nlp_cir_import", "ok", "Pipeline CIR chargé.", module=mod_name)
    result = jsonable(fn([document]))
    if not isinstance(result, dict):
        result = {"raw_nlp_result": result}

    add_log(
        logs, "nlp_cir", "ok", "Pipeline NLP CIR exécuté.",
        document=document.get("document"),
        stats=result.get("stats") or {},
        sections_count=len(result.get("sections") or []),
        reports=result.get("detection_reports") or [],
    )
    return result


def run_generic_nlp_existing(document: Dict[str, Any], logs: List[Dict[str, Any]]) -> Dict[str, Any]:
    mod, mod_name, err = import_any(["modules.NLP.pipeline", "NLP.pipeline"])
    if mod is None:
        raise RuntimeError(f"Pipeline NLP générique introuvable : {err}")
    fn = getattr(mod, "run_nlp_pipeline_fast", None)
    if not callable(fn):
        raise RuntimeError("run_nlp_pipeline_fast introuvable")

    add_log(logs, "nlp_generic_import", "ok", "Pipeline NLP générique chargé.", module=mod_name)
    result = jsonable(fn([document], include_cir_final=True, include_state_of_art_in_candidates=True))
    if not isinstance(result, dict):
        result = {"raw_nlp_result": result}

    add_log(logs, "nlp_generic", "ok", "Pipeline NLP générique exécuté.", document=document.get("document"), stats=result.get("stats") or {})
    return result


def looks_like_cir_final(file_name: str, text: str) -> bool:
    low = norm_text(f"{file_name}\n{text[:120000]}")
    hits = sum(1 for k in CIR_STRONG_WORDS if norm_text(k) in low)
    return hits >= 2 or ("cir" in low and hits >= 1)


def enrich_pack_items(
    nlp_result: Dict[str, Any],
    *,
    source_file: str,
    source_path: str,
    organisme: str,
    project: str,
    year: str,
    memory_status: str,
    memory_type: str,
    source_kind: str,
) -> Dict[str, Any]:
    out = dict(nlp_result or {})

    pack_keys = [
        "multi_document_evidence_pack_for_ennodiagnostic",
        "merged_evidence_pack_for_ennodiagnostic",
        "evidence_pack_for_ennodiagnostic",
        "merged_evidence_pack_before_frascati",
        "raw_evidence_pack_before_frascati",
        "evidence_pack_before_frascati",
    ]

    for pack_key in pack_keys:
        pack = out.get(pack_key)
        if not isinstance(pack, dict):
            continue
        for _, arr in pack.items():
            if not isinstance(arr, list):
                continue
            for item in arr:
                if not isinstance(item, dict):
                    continue
                item.setdefault("document", source_file)
                item.setdefault("source_path", source_path)
                item["organisme"] = organisme
                item["project"] = project
                item["year"] = year
                item["annee"] = year
                item["memory_status"] = memory_status
                item["memory_type"] = memory_type
                item["source_kind"] = source_kind
                item["source_file"] = source_file
                item["source_policy"] = "validated_experience" if memory_status == "validated" else "working_experience"
                item["document_type"] = source_kind
                item["content_origin"] = source_kind
                item["can_use_as_fact"] = memory_status == "validated"
                item["can_use_as_style"] = memory_status == "validated" and memory_type in {"style", "experience"}

    fg = out.get("frascati_guard")
    if isinstance(fg, dict) and isinstance(fg.get("qualified_pack_for_ennodiagnostic"), dict):
        qpack = fg["qualified_pack_for_ennodiagnostic"]
        for _, arr in qpack.items():
            if not isinstance(arr, list):
                continue
            for item in arr:
                if isinstance(item, dict):
                    item.setdefault("document", source_file)
                    item.setdefault("source_path", source_path)
                    item["organisme"] = organisme
                    item["project"] = project
                    item["year"] = year
                    item["annee"] = year
                    item["memory_status"] = memory_status
                    item["memory_type"] = memory_type
                    item["source_kind"] = source_kind
                    item["source_file"] = source_file
                    item["source_policy"] = "validated_experience" if memory_status == "validated" else "working_experience"
                    item["document_type"] = source_kind
                    item["content_origin"] = source_kind
                    item["can_use_as_fact"] = memory_status == "validated"
                    item["can_use_as_style"] = memory_status == "validated" and memory_type in {"style", "experience"}

    out["experience_memory_metadata"] = {
        "organisme": organisme,
        "project": project,
        "year": year,
        "memory_status": memory_status,
        "memory_type": memory_type,
        "source_kind": source_kind,
        "source_file": source_file,
        "source_path": source_path,
        "created_at": now_iso(),
    }
    return out


def make_style_chunks_from_cir_sections(
    nlp_result: Dict[str, Any],
    *,
    organisme: str,
    project: str,
    year: str,
    source_file: str,
    source_path: str,
    source_id: str,
) -> List[Dict[str, Any]]:
    sections = nlp_result.get("sections") or []
    if not isinstance(sections, list):
        return []

    out: List[Dict[str, Any]] = []
    allowed_types = {"objectifs", "etat_art", "limites_etat_art", "verrous", "methodes_travaux", "resultats", "contribution", "contexte"}

    for i, sec in enumerate(sections):
        if not isinstance(sec, dict):
            continue
        section_type = str(sec.get("section_type") or "unknown")
        text = clean_text(sec.get("text"))
        if section_type not in allowed_types or len(text) < 250:
            continue

        title = clean_text(sec.get("title") or sec.get("section_title") or section_type, 300)
        role = {
            "objectifs": "objectif", "contexte": "objectif", "etat_art": "etat_art",
            "limites_etat_art": "limite", "verrous": "verrou",
            "methodes_travaux": "methode", "resultats": "resultat", "contribution": "contribution",
        }.get(section_type, "style")

        chunk_id = f"style_{slugify(source_id)}_{i:04d}_{slugify(role)}"
        style_text = f"{title}\n{text}".strip()

        out.append({
            "id": chunk_id,
            "text": style_text,
            "source_text": style_text,
            "metadata": {
                "project_id": slugify(project),
                "organisme": organisme,
                "project": project,
                "year": str(year),
                "annee": str(year),
                "role": "style",
                "style_role": role,
                "pack_key": "style_examples",
                "document": source_file,
                "source_file": source_file,
                "source_path": source_path,
                "section_title": title,
                "section_type": section_type,
                "source_policy": "validated_experience",
                "content_origin": "cir_final_style",
                "document_type": "cir_final_consultant",
                "memory_status": "validated",
                "memory_type": "style",
                "source_kind": "cir_final_consultant",
                "can_use_as_fact": False,
                "can_use_as_style": True,
                "chunk_level": "style_section",
                "is_supporting_passage": False,
                "rag_chunk_id": chunk_id,
            },
            "raw_item": sec,
        })
    return out


def nlp_result_to_rag_chunks(nlp_result: Dict[str, Any], *, project_id: str, year: str, logs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    mod, mod_name, err = import_any(["modules.RAG.json_to_chunks"])
    if mod is None:
        raise RuntimeError(f"modules.RAG.json_to_chunks introuvable : {err}")
    fn = getattr(mod, "nlp_json_to_chunks", None)
    if not callable(fn):
        raise RuntimeError("nlp_json_to_chunks introuvable")

    chunks = jsonable(fn(project_id, nlp_result, year=year))
    if not isinstance(chunks, list):
        chunks = []

    before_roles = {}
    for c in chunks:
        if isinstance(c, dict):
            r = ((c.get("metadata") or {}).get("role") if isinstance(c.get("metadata"), dict) else "")
            before_roles[r] = before_roles.get(r, 0) + 1

    chunks = normalize_cir_final_chunk_roles(chunks)

    after_roles = {}
    normalized = 0
    for c in chunks:
        if isinstance(c, dict):
            meta = c.get("metadata") or {}
            r = meta.get("role")
            after_roles[r] = after_roles.get(r, 0) + 1
            if meta.get("memory_role_normalized"):
                normalized += 1

    add_log(
        logs, "rag_chunks", "ok", "Chunks RAG préparés depuis le JSON NLP.",
        chunks_count=len(chunks), module=mod_name,
        roles_before=before_roles, roles_after=after_roles,
        roles_normalized=normalized,
    )
    return chunks


def store_chunks_in_chroma(chunks: List[Dict[str, Any]], *, collection_name: str, reset: bool, logs: List[Dict[str, Any]]) -> Dict[str, Any]:
    mod, mod_name, err = import_any(["modules.RAG.vector_store"])
    if mod is None:
        raise RuntimeError(f"modules.RAG.vector_store introuvable : {err}")
    RAGVectorStore = getattr(mod, "RAGVectorStore", None)
    if RAGVectorStore is None:
        raise RuntimeError("RAGVectorStore introuvable")

    vs = RAGVectorStore(CHROMA_DIR)
    if reset:
        vs.reset_collection(collection_name)
        add_log(logs, "chroma_reset", "ok", "Collection Chroma réinitialisée.", collection=collection_name)

    rep = vs.add_chunks(collection_name=collection_name, chunks=chunks, reset=False)
    add_log(
        logs, "chroma_index", "ok", "Chunks ajoutés dans Chroma.",
        collection=collection_name, persist_dir=str(CHROMA_DIR),
        added=rep.get("added", 0), deduplicated=rep.get("deduplicated", 0),
    )
    return rep


def save_to_catalog(entry: Dict[str, Any]) -> None:
    catalog = read_json(CATALOG_PATH, default={"items": []})
    if not isinstance(catalog, dict):
        catalog = {"items": []}
    items = catalog.get("items")
    if not isinstance(items, list):
        items = []

    sid = entry.get("source_id")
    items = [x for x in items if not (isinstance(x, dict) and x.get("source_id") == sid)]
    items.append(entry)
    catalog["items"] = sorted(items, key=lambda x: str(x.get("created_at") or ""), reverse=True)
    catalog["updated_at"] = now_iso()
    write_json(CATALOG_PATH, catalog)


def process_one_file(path: Path, args: argparse.Namespace, reset_collection: bool = False) -> Dict[str, Any]:
    logs: List[Dict[str, Any]] = []
    t0 = time.time()
    ensure_dirs()

    source_hash = sha256_file(path)
    source_id = f"{slugify(path.stem)}_{source_hash[:10]}"
    organisme = args.organisme or "experience_global"
    project = args.project or path.stem
    year = str(args.year or datetime.now().year)
    memory_status = "validated" if args.validated else "working"
    memory_type = args.memory_type

    add_log(logs, "start", "ok", "Traitement source démarré.", file=str(path), source_id=source_id)

    extraction_json: Dict[str, Any] = {}
    text = ""

    if path.suffix.lower() == ".json":
        nlp_result = read_json(path, {})
        if not isinstance(nlp_result, dict):
            raise RuntimeError(f"JSON invalide : {path}")
        detected_mode = "nlp_json"
        add_log(logs, "json_load", "ok", "JSON NLP chargé directement.", file=str(path))
    else:
        extraction_json, text = extract_file_with_existing_module(path, args=args, logs=logs)
        doc = {
            "document": path.name,
            "file_name": path.name,
            "source_path": str(path),
            "text": text,
            "content_origin": "cir_final" if args.mode == "cir_final" else "experience_source",
            "source_policy": "validated_experience" if memory_status == "validated" else "working_experience",
            "document_type": "cir_final_consultant" if args.mode == "cir_final" else "experience_document",
        }

        detected_mode = "cir_final" if (args.mode == "auto" and looks_like_cir_final(path.name, text)) else args.mode
        if detected_mode == "cir_final":
            nlp_result = run_cir_nlp_existing(doc, logs=logs)
        else:
            nlp_result = run_generic_nlp_existing(doc, logs=logs)

    source_kind = {
        "cir_final": "cir_final_consultant",
        "raw_docs": "experience_document",
        "nlp_json": "nlp_json",
        "auto": "experience_document",
    }.get(detected_mode, detected_mode)

    nlp_result = enrich_pack_items(
        nlp_result,
        source_file=path.name,
        source_path=str(path),
        organisme=organisme,
        project=project,
        year=year,
        memory_status=memory_status,
        memory_type=memory_type,
        source_kind=source_kind,
    )

    chunks = nlp_result_to_rag_chunks(nlp_result, project_id=slugify(project), year=year, logs=logs)

    if detected_mode == "cir_final" and args.include_style:
        style_chunks = make_style_chunks_from_cir_sections(
            nlp_result,
            organisme=organisme,
            project=project,
            year=year,
            source_file=path.name,
            source_path=str(path),
            source_id=source_id,
        )
        chunks.extend(style_chunks)
        add_log(logs, "style_chunks", "ok", "Chunks de style CIR ajoutés.", style_chunks_count=len(style_chunks))

    chunks_file = CHUNKS_DIR / f"{source_id}.chunks.json"
    nlp_file = CHUNKS_DIR / f"{source_id}.nlp_result.json"
    extraction_file = CHUNKS_DIR / f"{source_id}.extraction.json"

    write_json(chunks_file, chunks)
    write_json(nlp_file, nlp_result)
    if extraction_json:
        write_json(extraction_file, extraction_json)

    add_log(logs, "write_files", "ok", "Fichiers mémoire écrits.", chunks_file=str(chunks_file), nlp_file=str(nlp_file))

    collection_global = "ennosmart_experience_global"
    collection_org = f"ennosmart_experience_{slugify(organisme)}"
    chroma_reports = {}

    if args.collection in {"global", "both"}:
        chroma_reports["global"] = store_chunks_in_chroma(chunks, collection_name=collection_global, reset=reset_collection, logs=logs)
    if args.collection in {"organism", "both"}:
        chroma_reports["organism"] = store_chunks_in_chroma(chunks, collection_name=collection_org, reset=reset_collection, logs=logs)

    elapsed = round(time.time() - t0, 2)
    report = {
        "ok": True,
        "source_id": source_id,
        "source_hash": source_hash,
        "file": str(path),
        "file_name": path.name,
        "organisme": organisme,
        "project": project,
        "year": year,
        "mode_requested": args.mode,
        "mode_detected": detected_mode,
        "memory_status": memory_status,
        "memory_type": memory_type,
        "chunks_count": len(chunks),
        "chroma_reports": chroma_reports,
        "outputs": {
            "chunks_file": str(chunks_file),
            "nlp_file": str(nlp_file),
            "extraction_file": str(extraction_file) if extraction_json else None,
        },
        "elapsed_seconds": elapsed,
        "logs": logs,
    }

    run_file = RUNS_DIR / f"{source_id}.run.json"
    write_json(run_file, report)
    save_to_catalog({
        "source_id": source_id,
        "file": str(path),
        "file_name": path.name,
        "organisme": organisme,
        "project": project,
        "year": year,
        "mode_detected": detected_mode,
        "memory_status": memory_status,
        "memory_type": memory_type,
        "chunks_count": len(chunks),
        "created_at": now_iso(),
        "run_file": str(run_file),
        "chunks_file": str(chunks_file),
    })
    return report


def search_experience_memory(
    query: str,
    *,
    collection_name: str = "ennosmart_experience_global",
    top_k: int = 8,
    role_filter: Optional[str] = None,
    memory_type_filter: Optional[str] = None,
    memory_status_filter: Optional[str] = None,
) -> Dict[str, Any]:
    ensure_dirs()
    logs: List[Dict[str, Any]] = []
    mod, mod_name, err = import_any(["modules.RAG.vector_store"])
    if mod is None:
        raise RuntimeError(f"modules.RAG.vector_store introuvable : {err}")
    RAGVectorStore = getattr(mod, "RAGVectorStore", None)
    if RAGVectorStore is None:
        raise RuntimeError("RAGVectorStore introuvable")

    vs = RAGVectorStore(CHROMA_DIR)
    res = vs.search(
        collection_name=collection_name,
        query=query,
        top_k=max(top_k * 4, top_k),
        role_filter=role_filter if role_filter and role_filter not in {"all", "auto"} else None,
        oversample=6,
    )

    matches = []
    for item in res:
        meta = item.get("metadata") or {}
        if memory_type_filter and memory_type_filter != "all" and str(meta.get("memory_type") or "") != memory_type_filter:
            continue
        if memory_status_filter and memory_status_filter != "all" and str(meta.get("memory_status") or "") != memory_status_filter:
            continue
        matches.append(item)
        if len(matches) >= top_k:
            break

    add_log(logs, "search", "ok", "Recherche expérience mémoire terminée.", collection=collection_name, matches_count=len(matches))
    return {"ok": True, "query": query, "collection_name": collection_name, "top_k": top_k, "matches_count": len(matches), "matches": matches, "logs": logs}


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Construire une base RAG géante d'expérience / connaissance / style CIR.")
    p.add_argument("--input", nargs="+", required=True, help="Fichier(s) ou dossier(s) à indexer.")
    p.add_argument("--organisme", default="experience_global")
    p.add_argument("--project", default="")
    p.add_argument("--year", default="")
    p.add_argument("--mode", choices=["auto", "cir_final", "raw_docs", "nlp_json"], default="auto")
    p.add_argument("--memory-type", choices=["experience", "knowledge", "style"], default="experience")
    p.add_argument("--validated", action="store_true", help="Marquer comme mémoire validée.")
    p.add_argument("--include-style", action="store_true", help="Pour les CIR finaux : ajouter aussi des chunks de style.")
    p.add_argument("--collection", choices=["global", "organism", "both"], default="both")
    p.add_argument("--reset", action="store_true", help="Réinitialise la/les collections avant indexation du premier fichier.")
    p.add_argument("--vision-mode", default="text_only", choices=["text_only", "auto", "fast", "full"])
    p.add_argument("--formula-mode", default="off", choices=["off", "fast", "explain"])
    p.add_argument("--out", default="", help="Chemin optionnel du rapport JSON global.")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    ensure_dirs()
    args = build_arg_parser().parse_args(argv)
    files = iter_input_files(args.input)
    if not files:
        print("Aucun fichier trouvé.")
        return 1

    reports = []
    reset_next = bool(args.reset)
    for idx, f in enumerate(files, start=1):
        print(f"\n[{idx}/{len(files)}] {f}")
        try:
            rep = process_one_file(f, args=args, reset_collection=reset_next)
            reports.append(rep)
            reset_next = False
            print(f"OK — chunks={rep.get('chunks_count')} mode={rep.get('mode_detected')}")
        except Exception as exc:
            rep = {"ok": False, "file": str(f), "error": str(exc), "time": now_iso()}
            reports.append(rep)
            print(f"ERREUR — {exc}")

    summary = {
        "ok": any(r.get("ok") for r in reports),
        "created_at": now_iso(),
        "memory_root": str(MEMORY_ROOT),
        "chroma_dir": str(CHROMA_DIR),
        "files_count": len(files),
        "success_count": sum(1 for r in reports if r.get("ok")),
        "error_count": sum(1 for r in reports if not r.get("ok")),
        "reports": reports,
    }
    out = Path(args.out) if args.out else RUNS_DIR / f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    write_json(out, summary)
    print("\nRapport :", out)
    print("Chroma :", CHROMA_DIR)
    print("Catalog :", CATALOG_PATH)
    return 0 if summary["success_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
