# -*- coding: utf-8 -*-
from __future__ import annotations

r"""
scripts/experience_memory_v2_engine.py

EnnoSmart Memory V2 FINAL

Architecture finale :
CIR final
→ extraction modules.extraction.router
→ NLP CIR modules.NLP.CIR.cir_pipeline
→ chunks RAG modules.RAG.json_to_chunks
→ normalisation métier V2
→ knowledge cards
→ relations
→ collection Chroma V2 globale unique

Les documents clients ne sont pas recopiés dans le dépôt. La racine persistante
doit être placée hors du code via ``ENNOSMART_EXPERIENCE_MEMORY_V2_DIR``.
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
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Import modules.* depuis C:\EnnoSmart\modules
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT_DIR / ".env", override=False)
    load_dotenv(ROOT_DIR / "backend_api" / ".env", override=False)
except Exception:
    pass

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


# ---------------------------------------------------------------------------
# Chemins
# ---------------------------------------------------------------------------

BASE_DIR = Path(os.getenv("ENNOSMART_BASE_DIR") or os.getenv("ENNOSMART_ROOT") or ROOT_DIR).resolve()
STORAGE_DIR = BASE_DIR / "storage"

ORGANISMES_DIR = Path(os.getenv("ENNOSMART_MEMORY_V2_ROOT", str(STORAGE_DIR / "organismes")))

V2_ROOT = Path(os.getenv("ENNOSMART_EXPERIENCE_MEMORY_V2_DIR", str(STORAGE_DIR / "experience_memory_v2")))

V2_EXTRACTION_DIR = V2_ROOT / "extraction"
V2_NLP_DIR = V2_ROOT / "nlp"
V2_CHUNKS_DIR = V2_ROOT / "chunks"
V2_CARDS_DIR = V2_ROOT / "cards"
V2_RELATIONS_DIR = V2_ROOT / "relations"
V2_RUNS_DIR = V2_ROOT / "runs"
V2_CHROMA_DIR = V2_ROOT / "chroma"

V2_CATALOG = V2_ROOT / "catalog_v2.json"
V2_GLOBAL_GRAPH = V2_ROOT / "global_memory_graph.json"

SUPPORTED_EXTS = {".docx", ".pdf", ".txt", ".md"}

STOPWORDS = set("""
a afin ai ainsi alors au aucun aussi autre aux avec avoir ce ces cet cette comme dans de des du elle en entre est et etre être fait font il ils je la le les leur leurs mais ne nos nous ou par pas plus pour que qui quoi sans se ses son sont sur un une vos votre vous
the and with from that this are was were have has had into using based between under over
""".split())

SECTION_TYPE_TO_ROLE = {
    "contexte": "objectif",
    "objectifs": "objectif",
    "etat_art": "etat_art",
    "limites_etat_art": "limite",
    "verrous": "verrou",
    "methodes_travaux": "methode",
    "resultats": "resultat",
    "contribution": "contribution",
    "administratif": "administratif",
    "annexe": "annexe",
    "project_title": "objectif",
}

ROLE_TO_MEMORY_CLASS = {
    "style": "style",
    "objectif": "knowledge",
    "etat_art": "knowledge",
    "limite": "knowledge",
    "verrou": "experience",
    "methode": "experience",
    "resultat": "experience",
    "contribution": "experience",
    "parametre": "knowledge",
    "administratif": "knowledge",
    "autre": "experience",
}

DOMAIN_KEYWORDS = {
    "intelligence_artificielle": [
        "llm", "large language model", "modèle de langage", "modeles de langage",
        "rag", "graphrag", "graph-rag", "retrieval", "embedding", "prompt",
        "agent", "multi-agent", "multi agents", "mixture-of-agents", "moa",
        "qwen", "gpt", "deepseek", "transformer", "inférence", "inference",
        "ia", "intelligence artificielle",
    ],
    "genie_logiciel": [
        "java", "junit", "test unitaire", "tests unitaires", "compilation",
        "maven", "jacoco", "couverture", "evosuite", "sf110", "compileragent",
        "code source", "classe", "méthode", "method", "api", "framework",
        "génération de code", "generation de code",
    ],
    "knowledge_graph": [
        "graphe de connaissances", "knowledge graph", "neo4j", "cypher",
        "nœud", "noeud", "arête", "arete", "graphe", "graphcoder",
        "relation structurelle", "dépendance transitive", "dependance transitive",
    ],
    "mecanique": [
        "compresseur", "soufflage", "carter", "segment", "étanchéité", "pression",
        "tgm", "vibration", "acoustique", "réfrigérant", "gaz", "moteur",
    ],
    "batiment_biosource": [
        "biosourcé", "biosource", "paroi", "paille", "chanvre", "bois",
        "rei", "feu", "hygrométrique", "fongique", "déphasage", "effusivité",
        "conductivité thermique", "re2020", "construction",
    ],
    "medical_biotech": [
        "biomed", "nodule", "image médicale", "simulation", "anthropomorphique",
        "protéomique", "proteomics", "biomédical", "biomedical",
    ],
}


# ---------------------------------------------------------------------------
# Utils
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def ensure_dirs() -> None:
    for p in [
        V2_ROOT,
        V2_EXTRACTION_DIR,
        V2_NLP_DIR,
        V2_CHUNKS_DIR,
        V2_CARDS_DIR,
        V2_RELATIONS_DIR,
        V2_RUNS_DIR,
        V2_CHROMA_DIR,
    ]:
        p.mkdir(parents=True, exist_ok=True)


def strip_accents(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(c for c in text if not unicodedata.combining(c))


def norm(text: Any) -> str:
    text = strip_accents(str(text or "").lower())
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9%°µ\-\./]+", " ", text)).strip()


def clean_text(text: Any, max_chars: int = 0) -> str:
    s = str(text or "").replace("\x00", " ").replace("\r", "\n")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    if max_chars and len(s) > max_chars:
        s = s[:max_chars].rsplit(" ", 1)[0] + "..."
    return s


def slugify(value: Any, max_len: int = 90) -> str:
    s = norm(value)
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return (s or "unknown")[:max_len]


def is_year(value: Any) -> bool:
    return bool(re.fullmatch(r"(19|20)\d{2}", str(value or "").strip()))


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
        return [jsonable(x) for x in obj]
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


def read_json(path: Path, default: Any = None) -> Any:
    try:
        if not path.exists():
            return default if default is not None else {}
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else {}


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(jsonable(data), ensure_ascii=False, indent=2), encoding="utf-8")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(1024 * 1024)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def stable_id(*parts: Any) -> str:
    raw = "|".join(str(x or "") for x in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def add_log(logs: List[Dict[str, Any]], step: str, status: str, message: str, **data: Any) -> None:
    logs.append({"time": now_iso(), "step": step, "status": status, "message": message, **jsonable(data)})


def import_any(names: List[str]):
    last_error = None
    for name in names:
        try:
            return importlib.import_module(name), name, None
        except Exception as exc:
            last_error = f"{name}: {exc}"
    return None, None, last_error


def tokenize(text: str) -> List[str]:
    t = norm(text)
    out = []
    for x in t.split():
        x = x.strip("._-/'")
        if len(x) < 3:
            continue
        if x in STOPWORDS:
            continue
        if x.isdigit():
            continue
        out.append(x)
    return out


def extract_keywords(text: str, top_k: int = 18) -> List[str]:
    tokens = tokenize(text)
    counts = Counter(tokens)

    for i in range(len(tokens) - 1):
        bg = f"{tokens[i]} {tokens[i+1]}"
        if len(bg) <= 45:
            counts[bg] += 2

    keywords = []
    for k, _ in counts.most_common(top_k * 3):
        if len(k) < 3:
            continue
        if k in keywords:
            continue
        keywords.append(k)
        if len(keywords) >= top_k:
            break
    return keywords


def detect_domains(text: str, top_k: int = 3) -> List[Dict[str, Any]]:
    n = norm(text)
    scores = []
    for domain, kws in DOMAIN_KEYWORDS.items():
        score = 0
        hits = []
        for kw in kws:
            nkw = norm(kw)
            if nkw and nkw in n:
                score += 1
                hits.append(kw)
        if score:
            scores.append({"domain": domain, "score": score, "hits": hits[:8]})
    scores.sort(key=lambda x: x["score"], reverse=True)
    return scores[:top_k]


def compute_importance(role: str, text: str, meta: Dict[str, Any]) -> float:
    base = {
        "verrou": 0.95,
        "etat_art": 0.88,
        "limite": 0.90,
        "methode": 0.82,
        "resultat": 0.86,
        "contribution": 0.92,
        "objectif": 0.78,
        "style": 0.65,
    }.get(role, 0.55)
    length_bonus = min(0.08, len(text) / 12000)
    validated_bonus = 0.05 if meta.get("memory_status") == "validated" else 0
    return round(min(1.0, base + length_bonus + validated_bonus), 3)


def normalize_project_name(project: str) -> str:
    s = clean_text(project)
    low = slugify(s)
    if low in {"ai_code", "aicode", "ai_code_"}:
        return "Ai-Code"
    return s or "unknown"


# ---------------------------------------------------------------------------
# Extraction / NLP existants
# ---------------------------------------------------------------------------

class EngineArgs:
    def __init__(
        self,
        *,
        mode: str = "cir_final",
        vision_mode: str = "text_only",
        formula_mode: str = "off",
    ):
        self.mode = mode
        self.vision_mode = vision_mode
        self.formula_mode = formula_mode


def extract_file(path: Path, logs: List[Dict[str, Any]], args: EngineArgs) -> Tuple[Dict[str, Any], str]:
    mod, mod_name, err = import_any(["modules.extraction.router"])
    if mod is None:
        raise RuntimeError(f"Module extraction introuvable : {err}")
    if not hasattr(mod, "extract"):
        raise RuntimeError("modules.extraction.router.extract introuvable")

    add_log(logs, "extraction_import", "ok", "Module extraction chargé.", module=mod_name)

    result = mod.extract(
        file_path=str(path),
        vision_mode=args.vision_mode,
        formula_mode=args.formula_mode,
        source_tag="ARCHIVE",
    )

    data = jsonable(result)
    if not isinstance(data, dict):
        data = {"raw_extraction": data}

    chunks = []
    chunks.extend(list(getattr(result, "text_chunks", []) or []))
    chunks.extend(list(getattr(result, "visual_chunks", []) or []))

    text = clean_text("\n\n".join(str(x) for x in chunks if str(x).strip()))

    add_log(
        logs,
        "extraction",
        "ok" if len(text) >= 100 else "warning",
        "Extraction terminée.",
        file=str(path),
        text_chars=len(text),
        text_chunks=len(getattr(result, "text_chunks", []) or []),
        visual_chunks=len(getattr(result, "visual_chunks", []) or []),
        preview=clean_text(text[:800]),
    )

    return data, text


def run_cir_nlp(document: Dict[str, Any], logs: List[Dict[str, Any]]) -> Dict[str, Any]:
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

    add_log(
        logs,
        "nlp_cir_import",
        "ok",
        "Pipeline CIR chargé.",
        module=mod_name,
    )

    # IMPORTANT :
    # Mémoire V2 = CIR final consultant validé.
    # Donc aucun FrascatiGuard, aucun verrou reconstruit.
    try:
        result = fn(
            [document],
            memory_mode=True,
            apply_frascati=False,
        )
    except TypeError:
        # Compatibilité si ancien cir_pipeline sans paramètres.
        result = fn([document])

    result = jsonable(result)

    if not isinstance(result, dict):
        result = {"raw_nlp_result": result}

    add_log(
        logs,
        "nlp_cir",
        "ok",
        "Pipeline NLP CIR exécuté en mode mémoire fidèle, sans FrascatiGuard.",
        document=document.get("document"),
        version=result.get("version"),
        memory_mode=result.get("memory_mode"),
        apply_frascati=result.get("apply_frascati"),
        stats=result.get("stats") or {},
        sections_count=len(result.get("sections") or []),
        reports=result.get("detection_reports") or [],
    )

    return result


def add_source_metadata_to_nlp(
    nlp_result: Dict[str, Any],
    *,
    organisme: str,
    project: str,
    subproject: str,
    year: str,
    source_file: str,
    source_path: str,
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

    def inject_item(item: Dict[str, Any]) -> None:
        item.setdefault("document", source_file)
        item.setdefault("source_path", source_path)
        item["organisme"] = organisme
        item["project"] = project
        item["subproject"] = subproject
        item["year"] = year
        item["annee"] = year
        item["memory_status"] = "validated"
        item["memory_type"] = "experience"
        item["source_kind"] = "cir_final_consultant"
        item["source_file"] = source_file
        item["source_policy"] = "validated_experience"
        item["document_type"] = "cir_final_consultant"
        item["content_origin"] = "cir_final_consultant"
        item["can_use_as_fact"] = True
        item["can_use_as_style"] = True

    for pack_key in pack_keys:
        pack = out.get(pack_key)
        if not isinstance(pack, dict):
            continue
        for _, arr in pack.items():
            if not isinstance(arr, list):
                continue
            for item in arr:
                if isinstance(item, dict):
                    inject_item(item)

    fg = out.get("frascati_guard")
    if isinstance(fg, dict) and isinstance(fg.get("qualified_pack_for_ennodiagnostic"), dict):
        for _, arr in fg["qualified_pack_for_ennodiagnostic"].items():
            if not isinstance(arr, list):
                continue
            for item in arr:
                if isinstance(item, dict):
                    inject_item(item)

    out["experience_memory_v2_metadata"] = {
        "organisme": organisme,
        "project": project,
        "subproject": subproject,
        "year": year,
        "memory_status": "validated",
        "memory_type": "experience",
        "source_kind": "cir_final_consultant",
        "source_file": source_file,
        "source_path": source_path,
        "created_at": now_iso(),
    }

    return out


def nlp_to_chunks(nlp_result: Dict[str, Any], project_id: str, year: str, logs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    mod, mod_name, err = import_any(["modules.RAG.json_to_chunks"])
    if mod is None:
        raise RuntimeError(f"modules.RAG.json_to_chunks introuvable : {err}")

    fn = getattr(mod, "nlp_json_to_chunks", None)
    if not callable(fn):
        raise RuntimeError("nlp_json_to_chunks introuvable")

    chunks = jsonable(fn(project_id, nlp_result, year=year))
    if not isinstance(chunks, list):
        chunks = []

    add_log(logs, "rag_chunks_raw", "ok", "Chunks RAG bruts préparés.", module=mod_name, chunks_count=len(chunks))
    return chunks


# ---------------------------------------------------------------------------
# Normalisation rôle + enrichissement V2
# ---------------------------------------------------------------------------

def _role_from_raw_item(raw_item: Dict[str, Any], metadata: Dict[str, Any]) -> str:
    section_type = clean_text(raw_item.get("section_type") or metadata.get("section_type")).lower()

    if section_type in SECTION_TYPE_TO_ROLE:
        return SECTION_TYPE_TO_ROLE[section_type]

    source_category = clean_text(raw_item.get("_source_category") or metadata.get("pack_key")).lower()

    mapping = {
        "objectifs_locaux": "objectif",
        "verrous_rnd_locaux": "verrou",
        "methodes_locales": "methode",
        "resultats_locaux": "resultat",
        "etat_art_local": "etat_art",
        "limites_locales": "limite",
        "contributions_locales": "contribution",
        "parametres_locaux": "parametre",
    }

    if source_category in mapping:
        return mapping[source_category]

    role = clean_text(raw_item.get("role") or metadata.get("role")).lower()
    if role in {"objectif", "verrou", "methode", "resultat", "etat_art", "limite", "contribution", "parametre", "style"}:
        return role

    return "autre"


def normalize_cir_final_chunk_roles(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    for ch in chunks or []:
        if not isinstance(ch, dict):
            continue

        meta = ch.get("metadata")
        if not isinstance(meta, dict):
            meta = {}
            ch["metadata"] = meta

        raw_item = ch.get("raw_item")
        if not isinstance(raw_item, dict):
            raw_item = {}

        original_role = clean_text(meta.get("role")).lower()
        structural_role = _role_from_raw_item(raw_item, meta)

        if original_role != "style" and meta.get("chunk_level") != "style_section":
            meta["role_before_memory_normalization"] = original_role
            meta["role"] = structural_role
            meta["memory_role_normalized"] = True
            meta["frascati_role"] = raw_item.get("final_role") or meta.get("final_role") or ""

            old_id = clean_text(ch.get("id"))
            if old_id and "_verrou_" in old_id and structural_role != "verrou":
                new_id = old_id.replace("_verrou_", f"_{structural_role}_", 1)
                ch["id_before_memory_normalization"] = old_id
                ch["id"] = new_id
                meta["rag_chunk_id"] = new_id

        out.append(ch)

    return out


def make_style_chunks_from_sections(
    nlp_result: Dict[str, Any],
    *,
    organisme: str,
    project: str,
    subproject: str,
    year: str,
    source_file: str,
    source_path: str,
    source_id: str,
) -> List[Dict[str, Any]]:
    sections = nlp_result.get("sections") or []
    if not isinstance(sections, list):
        return []

    allowed_types = {"objectifs", "etat_art", "limites_etat_art", "verrous", "methodes_travaux", "resultats", "contribution", "contexte"}
    out: List[Dict[str, Any]] = []

    for i, sec in enumerate(sections):
        if not isinstance(sec, dict):
            continue

        section_type = str(sec.get("section_type") or "unknown")
        text = clean_text(sec.get("text"))

        if section_type not in allowed_types or len(text) < 250:
            continue

        title = clean_text(sec.get("title") or sec.get("section_title") or section_type, 300)

        role = {
            "objectifs": "objectif",
            "contexte": "objectif",
            "etat_art": "etat_art",
            "limites_etat_art": "limite",
            "verrous": "verrou",
            "methodes_travaux": "methode",
            "resultats": "resultat",
            "contribution": "contribution",
        }.get(section_type, "style")

        chunk_id = f"style_{slugify(source_id)}_{i:04d}_{slugify(role)}"
        style_text = f"{title}\n{text}".strip()

        out.append({
            "id": chunk_id,
            "text": style_text,
            "source_text": style_text,
            "metadata": {
                "project_id": slugify(" ".join(value for value in (project, subproject) if value)),
                "organisme": organisme,
                "project": project,
                "subproject": subproject,
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
                "page_number": sec.get("page_number") or sec.get("page_start") or sec.get("page"),
                "page_start": sec.get("page_start") or sec.get("page_number") or sec.get("page"),
                "page_end": sec.get("page_end") or sec.get("page_number") or sec.get("page"),
                "page_numbers": list(sec.get("page_numbers") or []),
                "content_kind": "section_style_example",
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


def make_document_asset_chunks(
    extraction_json: Dict[str, Any],
    nlp_result: Dict[str, Any],
    *,
    organisme: str,
    project: str,
    subproject: str,
    year: str,
    source_file: str,
    source_path: str,
    source_id: str,
) -> List[Dict[str, Any]]:
    """Indexe séparément les tableaux et légendes de figures du PDF.

    Ces chunks ne remplacent pas les sections NLP : ils rendent les résultats
    tabulaires et les références visuelles directement récupérables avec une
    provenance page/section précise.
    """
    structured = extraction_json.get("structured_data") or {}
    pages = structured.get("pages") or [] if isinstance(structured, dict) else []
    sections = nlp_result.get("sections") or []
    if not isinstance(pages, list):
        return []

    def section_for_page(page_number: int) -> Dict[str, Any]:
        candidates = []
        for section in sections if isinstance(sections, list) else []:
            if not isinstance(section, dict):
                continue
            start = section.get("page_start") or section.get("page_number") or section.get("page")
            end = section.get("page_end") or start
            try:
                start_i, end_i = int(start), int(end)
            except Exception:
                continue
            if start_i <= page_number <= end_i:
                level = int(section.get("level") or 1)
                candidates.append((level, -(end_i - start_i), section))
        return max(candidates, key=lambda item: (item[0], item[1]))[2] if candidates else {}

    def role_for(section: Dict[str, Any]) -> str:
        return SECTION_TYPE_TO_ROLE.get(str(section.get("section_type") or ""), "autre")

    output: List[Dict[str, Any]] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        try:
            page_number = int(page.get("page_number"))
        except Exception:
            continue
        section = section_for_page(page_number)
        title = clean_text(section.get("title") or section.get("section_title") or "", 300)
        section_id = clean_text(section.get("section_id") or "", 80)
        role = role_for(section)
        common_meta = {
            "project_id": slugify(" ".join(value for value in (project, subproject) if value)),
            "organisme": organisme,
            "project": project,
            "subproject": subproject,
            "year": str(year),
            "annee": str(year),
            "role": role,
            "pack_key": "document_assets",
            "document": source_file,
            "source_file": source_file,
            "source_path": source_path,
            "section_title": title,
            "section_id": section_id,
            "section_type": section.get("section_type") or "unknown",
            "page_number": page_number,
            "page_start": page_number,
            "page_end": page_number,
            "page_numbers": [page_number],
            "source_policy": "validated_experience",
            "content_origin": "cir_final_consultant",
            "document_type": "cir_final_consultant",
            "memory_status": "validated",
            "memory_type": "experience",
            "source_kind": "cir_final_consultant",
            "can_use_as_fact": True,
            "can_use_as_style": False,
            "chunk_level": "document_asset",
            "is_supporting_passage": False,
        }

        table_captions = [clean_text(value) for value in (page.get("table_captions") or []) if clean_text(value)]
        for index, markdown in enumerate(page.get("tables_markdown") or []):
            table_text = clean_text(markdown)
            if not table_text:
                continue
            caption = table_captions[index] if index < len(table_captions) else f"Tableau page {page_number}"
            text = f"{title}\n{caption}\n[TABLEAU STRUCTURÉ]\n{table_text}".strip()
            chunk_id = f"asset_{slugify(source_id)}_p{page_number:04d}_table_{index + 1:02d}"
            output.append({
                "id": chunk_id,
                "text": text,
                "source_text": text,
                "metadata": {
                    **common_meta,
                    "content_kind": "table",
                    "asset_type": "table",
                    "asset_index": index + 1,
                    "asset_caption": caption,
                    "rag_chunk_id": chunk_id,
                },
                "raw_item": {"caption": caption, "markdown": table_text, "page_number": page_number},
            })

        for index, caption_value in enumerate(page.get("figure_captions") or []):
            caption = clean_text(caption_value)
            if not caption:
                continue
            text = f"{title}\n[FIGURE PAGE {page_number}]\n{caption}".strip()
            chunk_id = f"asset_{slugify(source_id)}_p{page_number:04d}_figure_{index + 1:02d}"
            output.append({
                "id": chunk_id,
                "text": text,
                "source_text": text,
                "metadata": {
                    **common_meta,
                    "content_kind": "figure_caption",
                    "asset_type": "figure",
                    "asset_index": index + 1,
                    "asset_caption": caption,
                    "rag_chunk_id": chunk_id,
                },
                "raw_item": {"caption": caption, "page_number": page_number},
            })

    return output


def enrich_chunk_v2(
    chunk: Dict[str, Any],
    *,
    organisme: str,
    project: str,
    subproject: str,
    year: str,
    source_file: str,
    source_path: str,
) -> Dict[str, Any]:
    meta = chunk.get("metadata")
    if not isinstance(meta, dict):
        meta = {}

    text = clean_text(chunk.get("text") or chunk.get("source_text") or "")
    role = clean_text(meta.get("role") or "autre").lower() or "autre"

    organisme = clean_text(meta.get("organisme") or organisme)
    project = normalize_project_name(clean_text(meta.get("project") or project))
    raw_subproject = clean_text(meta.get("subproject") or subproject)
    subproject = normalize_project_name(raw_subproject) if raw_subproject else ""
    year = clean_text(meta.get("year") or meta.get("annee") or year)

    keywords = extract_keywords(text)
    domains = detect_domains(text)
    memory_class = ROLE_TO_MEMORY_CLASS.get(role, "experience")

    chunk_id = clean_text(chunk.get("id") or meta.get("rag_chunk_id"))
    if not chunk_id:
        chunk_id = f"v2_{slugify(organisme)}_{slugify(project)}_{slugify(subproject)}_{slugify(year)}_{role}_{stable_id(text)}"

    enriched_meta = {
        **meta,
        "v2": True,
        "v2_created_at": now_iso(),
        "chunk_id": chunk_id,
        "organisme": organisme,
        "organisme_slug": slugify(organisme),
        "project": project,
        "project_slug": slugify(project),
        "subproject": subproject,
        "subproject_slug": slugify(subproject) if subproject else "",
        "year": year,
        "document": clean_text(meta.get("document") or source_file),
        "source_file": clean_text(meta.get("source_file") or source_file),
        "source_path": clean_text(meta.get("source_path") or source_path),
        "role": role,
        "memory_class": memory_class,
        "memory_type_v2": memory_class,
        "document_type_v2": meta.get("document_type") or meta.get("source_kind") or "cir_final_consultant",
        "source_section": clean_text(meta.get("section_number") or meta.get("section_id") or meta.get("section") or ""),
        "section_title": clean_text(meta.get("section_title") or meta.get("title") or ""),
        "page_number": meta.get("page_number") if meta.get("page_number") not in (None, "", -1) else None,
        "page_start": meta.get("page_start") if meta.get("page_start") not in (None, "", -1) else None,
        "page_end": meta.get("page_end") if meta.get("page_end") not in (None, "", -1) else None,
        "content_kind": clean_text(meta.get("content_kind") or ("table" if "[TABLEAU STRUCTURÉ]" in text else "prose")),
        "keywords": ", ".join(keywords),
        "keywords_list": keywords,
        "domains": ", ".join([d["domain"] for d in domains]),
        "domains_list": domains,
        "main_domain": domains[0]["domain"] if domains else "",
        "importance": compute_importance(role, text, meta),
        "consultant_memory_use": "style_only" if memory_class == "style" else "knowledge_and_experience",
        "can_use_as_fact": bool(meta.get("can_use_as_fact", memory_class != "style")),
        "can_use_as_style": bool(meta.get("can_use_as_style", memory_class == "style")),
        "relation_key_project": f"{slugify(organisme)}::{slugify(project)}::{slugify(subproject) if subproject else ''}::{year}",
        "relation_key_domain": domains[0]["domain"] if domains else "",
        "relation_key_role": role,
    }

    return {
        "id": chunk_id,
        "text": text,
        "source_text": text,
        "metadata": enriched_meta,
        "raw_item": chunk.get("raw_item"),
    }


def make_knowledge_card(enriched_chunk: Dict[str, Any]) -> Dict[str, Any]:
    meta = enriched_chunk["metadata"]
    text = enriched_chunk["text"]
    role = meta.get("role", "autre")

    title = meta.get("section_title") or f"{role} — {meta.get('project')} {meta.get('year')}"
    card_id = f"card_{slugify(meta.get('organisme'))}_{slugify(meta.get('project'))}_{slugify(meta.get('subproject'))}_{slugify(meta.get('year'))}_{role}_{stable_id(text)}"

    return {
        "card_id": card_id,
        "card_type": role,
        "memory_class": meta.get("memory_class"),
        "title": title,
        "summary": clean_text(text, 700),
        "organisme": meta.get("organisme"),
        "project": meta.get("project"),
        "subproject": meta.get("subproject") or "",
        "year": meta.get("year"),
        "document": meta.get("document"),
        "source_chunk_id": meta.get("chunk_id"),
        "source_section": meta.get("source_section"),
        "section_title": meta.get("section_title"),
        "page_number": meta.get("page_number"),
        "content_kind": meta.get("content_kind"),
        "keywords": meta.get("keywords_list") or [],
        "domains": meta.get("domains_list") or [],
        "main_domain": meta.get("main_domain"),
        "importance": meta.get("importance"),
        "style_usable": meta.get("can_use_as_style"),
        "fact_usable": meta.get("can_use_as_fact"),
        "created_at": now_iso(),
    }


# ---------------------------------------------------------------------------
# Relations + Chroma + Catalog
# ---------------------------------------------------------------------------

def similarity_keywords(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    ak = set(a.get("keywords") or [])
    bk = set(b.get("keywords") or [])
    if not ak or not bk:
        return 0.0
    return len(ak & bk) / len(ak | bk)


def load_all_cards() -> List[Dict[str, Any]]:
    cards: List[Dict[str, Any]] = []
    for p in V2_CARDS_DIR.glob("*.cards.json"):
        data = read_json(p, [])
        if isinstance(data, list):
            cards.extend([x for x in data if isinstance(x, dict)])
    return cards


def build_relations(cards: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    relations = []
    by_project = defaultdict(list)
    by_domain = defaultdict(list)

    for c in cards:
        by_project[(c.get("organisme"), c.get("project"))].append(c)
        if c.get("main_domain"):
            by_domain[c.get("main_domain")].append(c)

    for (org, project), arr in by_project.items():
        arr = sorted(arr, key=lambda x: str(x.get("year")))
        for i in range(len(arr) - 1):
            if arr[i].get("year") != arr[i + 1].get("year"):
                relations.append({
                    "type": "same_project_over_years",
                    "from": arr[i]["card_id"],
                    "to": arr[i + 1]["card_id"],
                    "organisme": org,
                    "project": project,
                    "reason": "Même organisme et même projet sur années différentes.",
                })

    for domain, arr in by_domain.items():
        if len(arr) < 2:
            continue
        for i in range(len(arr)):
            for j in range(i + 1, min(len(arr), i + 12)):
                a, b = arr[i], arr[j]
                if a["card_id"] == b["card_id"]:
                    continue
                sim = similarity_keywords(a, b)
                same_role = a.get("card_type") == b.get("card_type")
                if sim >= 0.18 or same_role:
                    relations.append({
                        "type": "similar_experience",
                        "from": a["card_id"],
                        "to": b["card_id"],
                        "domain": domain,
                        "score": round(sim + (0.12 if same_role else 0), 3),
                        "reason": "Même domaine scientifique et mots-clés proches.",
                    })

    return relations[:5000]


def chroma_store(chunks: List[Dict[str, Any]], collection_name: str, reset: bool = False) -> Dict[str, Any]:
    mod, _, err = import_any(["modules.RAG.vector_store"])
    if mod is None:
        raise RuntimeError(f"modules.RAG.vector_store introuvable : {err}")

    RAGVectorStore = getattr(mod, "RAGVectorStore", None)
    if RAGVectorStore is None:
        raise RuntimeError("RAGVectorStore introuvable")

    vs = RAGVectorStore(V2_CHROMA_DIR)
    if reset:
        vs.reset_collection(collection_name)

    return vs.add_chunks(collection_name=collection_name, chunks=chunks, reset=False)


def prune_legacy_chroma_collections() -> List[str]:
    """Conserve uniquement la collection globale de Memory V2."""
    mod, _, err = import_any(["modules.RAG.vector_store"])
    if mod is None:
        raise RuntimeError(f"modules.RAG.vector_store introuvable : {err}")
    RAGVectorStore = getattr(mod, "RAGVectorStore", None)
    if RAGVectorStore is None:
        raise RuntimeError("RAGVectorStore introuvable")

    vector_store = RAGVectorStore(V2_CHROMA_DIR)
    removed: List[str] = []
    for raw_collection in vector_store.client.list_collections():
        name = clean_text(getattr(raw_collection, "name", raw_collection))
        if name.startswith("ennosmart_memory_v2_") and name != "ennosmart_memory_v2_global":
            vector_store.client.delete_collection(name)
            removed.append(name)
    return sorted(removed)


def load_all_v2_chunks() -> List[Dict[str, Any]]:
    chunks: List[Dict[str, Any]] = []
    for p in V2_CHUNKS_DIR.glob("*.chunks_v2.json"):
        data = read_json(p, [])
        if isinstance(data, list):
            chunks.extend([x for x in data if isinstance(x, dict)])
    return chunks


def rebuild_global_graph_and_catalog(reset_chroma: bool = False) -> Dict[str, Any]:
    ensure_dirs()

    chunks = load_all_v2_chunks()
    cards = load_all_cards()
    relations = build_relations(cards)

    write_json(V2_GLOBAL_GRAPH, {
        "created_at": now_iso(),
        "cards_count": len(cards),
        "relations_count": len(relations),
        "cards": cards,
        "relations": relations,
    })

    write_json(V2_RELATIONS_DIR / "relations_global.json", relations)

    chroma_reports = {}
    if chunks:
        chroma_reports["global"] = chroma_store(chunks, "ennosmart_memory_v2_global", reset=reset_chroma)
    elif reset_chroma:
        chroma_reports["global"] = chroma_store([], "ennosmart_memory_v2_global", reset=True)
    chroma_reports["removed_legacy_collections"] = prune_legacy_chroma_collections()

    catalog = {
        "ok": True,
        "version": "v2_final",
        "updated_at": now_iso(),
        "v2_root": str(V2_ROOT),
        "chunks_count": len(chunks),
        "cards_count": len(cards),
        "relations_count": len(relations),
        "organisms": sorted(list({c.get("organisme") for c in cards if c.get("organisme")})),
        "projects": sorted(list({
            f"{c.get('organisme')}::{c.get('project')}::{c.get('subproject') or ''}::{c.get('year')}"
            for c in cards
        })),
        "subprojects": sorted(list({c.get("subproject") for c in cards if c.get("subproject")})),
        "chroma_mode": "single_global_collection",
        "role_counts": dict(Counter(c.get("card_type") for c in cards)),
        "domain_counts": dict(Counter(c.get("main_domain") for c in cards if c.get("main_domain"))),
        "outputs": {
            "catalog": str(V2_CATALOG),
            "global_graph": str(V2_GLOBAL_GRAPH),
            "relations": str(V2_RELATIONS_DIR / "relations_global.json"),
            "chroma": str(V2_CHROMA_DIR),
        },
        "chroma_reports": chroma_reports,
    }

    write_json(V2_CATALOG, catalog)
    return catalog



# ---------------------------------------------------------------------------
# Filtre mémoire fidèle CIR final
# ---------------------------------------------------------------------------

def keep_only_explicit_cir_final_chunks(chunks: List[Dict[str, Any]], logs: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """
    Pour la mémoire d'expérience basée sur des CIR finaux validés :
    on garde uniquement les passages réellement présents dans le CIR.

    On supprime les verrous reconstruits automatiquement par la logique Frascati,
    car ils sont utiles pour EnnoDiagnostic sur documents bruts, mais pas pour
    mémoriser un CIR final consultant.

    Supprimés :
    - passage_id contenant implicit / universal
    - original_passage_id contenant implicit / universal
    - verrou_source == universal_theme_reconstruction
    - final_role == verrou_implicite_a_verifier
    - quality_status == frascati_universal_theme_to_validate
    """
    kept: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []

    for ch in chunks or []:
        if not isinstance(ch, dict):
            continue

        meta = ch.get("metadata") if isinstance(ch.get("metadata"), dict) else {}
        raw = ch.get("raw_item") if isinstance(ch.get("raw_item"), dict) else {}

        passage_id = str(raw.get("passage_id") or meta.get("original_passage_id") or "").lower()
        original_passage_id = str(meta.get("original_passage_id") or "").lower()
        final_role = str(raw.get("final_role") or meta.get("final_role") or "").lower()
        quality_status = str(raw.get("quality_status") or meta.get("quality_status") or "").lower()
        verrou_source = str(raw.get("verrou_source") or meta.get("verrou_source") or "").lower()

        is_reconstructed = (
            "implicit" in passage_id
            or "implicit" in original_passage_id
            or "universal" in passage_id
            or "universal" in original_passage_id
            or verrou_source == "universal_theme_reconstruction"
            or final_role == "verrou_implicite_a_verifier"
            or quality_status == "frascati_universal_theme_to_validate"
        )

        if is_reconstructed:
            removed.append({
                "id": ch.get("id") or meta.get("rag_chunk_id"),
                "role": meta.get("role"),
                "section_title": meta.get("section_title"),
                "final_role": final_role,
                "quality_status": quality_status,
                "verrou_source": verrou_source,
                "passage_id": passage_id,
            })
            continue

        # Pour les chunks gardés, Frascati reste seulement une métadonnée secondaire.
        meta["memory_extraction_mode"] = "cir_final_explicit_only"
        meta["auto_reconstructed_removed"] = False
        ch["metadata"] = meta
        kept.append(ch)

    if logs is not None:
        add_log(
            logs,
            "filter_explicit_cir_final",
            "ok",
            "Filtre CIR final appliqué : verrous implicites reconstruits supprimés.",
            kept=len(kept),
            removed=len(removed),
            removed_preview=removed[:10],
        )

    return kept


# ---------------------------------------------------------------------------
# Build direct V2
# ---------------------------------------------------------------------------

def original_file_target(src_path: Path, organisme: str, project: str, subproject: str, year: str) -> Path:
    target_dir = ORGANISMES_DIR / organisme / "projects" / project
    if subproject:
        target_dir = target_dir / "subprojects" / subproject
    target_dir = target_dir / "years" / str(year) / "cir_final_consultant" / "current"
    safe_name = re.sub(r"[^\w\-. àâäéèêëîïôöùûüçÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ]+", "_", src_path.name)
    return target_dir / safe_name


def store_original_file(src_path: Path, organisme: str, project: str, subproject: str, year: str) -> Path:
    target = original_file_target(src_path, organisme, project, subproject, year)
    target.parent.mkdir(parents=True, exist_ok=True)

    if src_path.resolve() != target.resolve():
        target.write_bytes(src_path.read_bytes())

    return target


def build_cir_final_v2(
    file_path: Path,
    *,
    organisme: str,
    project: str,
    year: str,
    subproject: str = "",
    copy_to_library: bool = False,
    reset_chroma: bool = False,
    vision_mode: str = "text_only",
    formula_mode: str = "off",
    rebuild_catalog: bool = True,
) -> Dict[str, Any]:
    ensure_dirs()
    t0 = time.time()
    logs: List[Dict[str, Any]] = []

    organisme = clean_text(organisme)
    project = normalize_project_name(project)
    subproject = normalize_project_name(subproject) if clean_text(subproject) else ""
    year = clean_text(year)

    if not organisme:
        raise ValueError("organisme obligatoire")
    if not project:
        raise ValueError("project obligatoire")
    if not is_year(year):
        raise ValueError("year invalide")
    if not file_path.exists():
        raise FileNotFoundError(str(file_path))
    if file_path.suffix.lower() not in SUPPORTED_EXTS:
        raise ValueError(f"Extension non supportée : {file_path.suffix}")

    # L'extraction et la création des cartes se font avant de rendre le projet
    # visible dans la bibliothèque. Un échec ne laisse donc plus une ligne à
    # zéro carte dans l'interface.
    extraction_input = file_path
    source_path = (
        original_file_target(file_path, organisme, project, subproject, year)
        if copy_to_library
        else Path(file_path.name)
    )
    source_hash = sha256_file(extraction_input)
    source_id = f"{slugify(source_path.stem)}_{source_hash[:10]}"

    add_log(logs, "start", "ok", "Build V2 direct démarré.", source_id=source_id, file=str(source_path))

    args = EngineArgs(mode="cir_final", vision_mode=vision_mode, formula_mode=formula_mode)

    extraction_json, extracted_text = extract_file(extraction_input, logs, args)

    document = {
    "document": source_path.name,
    "file_name": source_path.name,
    "source_path": str(source_path),
    "text": extracted_text,

    # Important pour forcer le mode mémoire
    "content_origin": "cir_final_consultant",
    "document_type": "cir_final_consultant",
    "source_policy": "validated_experience",
}

    nlp_result = run_cir_nlp(document, logs)
    nlp_result = add_source_metadata_to_nlp(
        nlp_result,
        organisme=organisme,
        project=project,
        subproject=subproject,
        year=year,
        source_file=source_path.name,
        source_path=str(source_path),
    )

    project_id = slugify(" ".join(value for value in (project, subproject) if value))
    raw_chunks = nlp_to_chunks(nlp_result, project_id, year, logs)

    # Mémoire CIR final = extraction fidèle uniquement.
    # On retire les verrous implicites reconstruits automatiquement.
    explicit_chunks = keep_only_explicit_cir_final_chunks(raw_chunks, logs)

    normalized_chunks = normalize_cir_final_chunk_roles(explicit_chunks)

    style_chunks = make_style_chunks_from_sections(
        nlp_result,
        organisme=organisme,
        project=project,
        subproject=subproject,
        year=year,
        source_file=source_path.name,
        source_path=str(source_path),
        source_id=source_id,
    )

    asset_chunks = make_document_asset_chunks(
        extraction_json,
        nlp_result,
        organisme=organisme,
        project=project,
        subproject=subproject,
        year=year,
        source_file=source_path.name,
        source_path=str(source_path),
        source_id=source_id,
    )

    add_log(
        logs,
        "document_assets",
        "ok",
        "Tableaux et légendes de figures préparés pour l'index vectoriel.",
        chunks_count=len(asset_chunks),
        tables=sum(1 for chunk in asset_chunks if (chunk.get("metadata") or {}).get("asset_type") == "table"),
        figures=sum(1 for chunk in asset_chunks if (chunk.get("metadata") or {}).get("asset_type") == "figure"),
    )

    all_chunks = normalized_chunks + style_chunks + asset_chunks

    chunks_v2 = [
        enrich_chunk_v2(
            ch,
            organisme=organisme,
            project=project,
            subproject=subproject,
            year=year,
            source_file=source_path.name,
            source_path=str(source_path),
        )
        for ch in all_chunks
    ]

    cards = [make_knowledge_card(ch) for ch in chunks_v2]

    if not chunks_v2 or not cards:
        raise ValueError(
            "Extraction sans passages/cartes exploitables : le CIR n'a pas été ajouté à Memory V2."
        )

    if copy_to_library:
        source_path = store_original_file(extraction_input, organisme, project, subproject, year)

    role_counts = Counter((ch.get("metadata") or {}).get("role") for ch in chunks_v2)
    memory_counts = Counter((ch.get("metadata") or {}).get("memory_class") for ch in chunks_v2)
    domain_counts = Counter((ch.get("metadata") or {}).get("main_domain") for ch in chunks_v2 if (ch.get("metadata") or {}).get("main_domain"))

    extraction_file = V2_EXTRACTION_DIR / f"{source_id}.extraction.json"
    nlp_file = V2_NLP_DIR / f"{source_id}.nlp_result.json"
    chunks_file = V2_CHUNKS_DIR / f"{source_id}.chunks_v2.json"
    cards_file = V2_CARDS_DIR / f"{source_id}.cards.json"

    write_json(extraction_file, extraction_json)
    write_json(nlp_file, nlp_result)
    write_json(chunks_file, chunks_v2)
    write_json(cards_file, cards)

    add_log(
        logs,
        "write_v2",
        "ok",
        "Fichiers V2 écrits.",
        extraction_file=str(extraction_file),
        nlp_file=str(nlp_file),
        chunks_file=str(chunks_file),
        cards_file=str(cards_file),
    )

    catalog = (
        rebuild_global_graph_and_catalog(reset_chroma=reset_chroma)
        if rebuild_catalog
        else {
            "ok": True,
            "deferred": True,
            "message": "Reconstruction globale différée jusqu'à la fin du lot.",
        }
    )

    run_report = {
        "ok": True,
        "version": "v2_final",
        "source_id": source_id,
        "source_hash": source_hash,
        "file": str(source_path),
        "file_name": source_path.name,
        "organisme": organisme,
        "project": project,
        "subproject": subproject,
        "year": year,
        "mode_detected": "cir_final",
        "chunks_count": len(chunks_v2),
        "cards_count": len(cards),
        "role_counts": dict(role_counts),
        "memory_counts": dict(memory_counts),
        "domain_counts": dict(domain_counts),
        "outputs": {
            "extraction_file": str(extraction_file),
            "nlp_file": str(nlp_file),
            "chunks_file": str(chunks_file),
            "cards_file": str(cards_file),
            "catalog_v2": str(V2_CATALOG),
            "global_graph": str(V2_GLOBAL_GRAPH),
            "chroma": str(V2_CHROMA_DIR),
        },
        "catalog_summary": catalog,
        "elapsed_seconds": round(time.time() - t0, 2),
        "logs": logs,
    }

    run_file = V2_RUNS_DIR / f"{source_id}.run_v2.json"
    write_json(run_file, run_report)
    run_report["outputs"]["run_file"] = str(run_file)

    return run_report


def scan_library() -> List[Dict[str, Any]]:
    ensure_dirs()
    rows = []

    for f in ORGANISMES_DIR.rglob("*"):
        if not f.is_file() or f.suffix.lower() not in SUPPORTED_EXTS:
            continue
        if f.name.startswith("~$"):
            continue

        parts = list(f.parts)
        low = [p.lower() for p in parts]

        # La mémoire V2 est exclusivement la mémoire des CIR finaux validés.
        # Les documents bruts, articles Scholar et brouillons placés sous le
        # même projet ne doivent jamais être proposés au batch mémoire.
        if "cir_final_consultant" not in low or f.parent.name.lower() != "current":
            continue

        try:
            i = low.index("organismes")
            organisme = parts[i + 1]
        except Exception:
            organisme = "unknown"

        project = "unknown"
        year = "unknown"

        if "projects" in low:
            j = low.index("projects")
            if j + 1 < len(parts):
                project = parts[j + 1]

        if "years" in low:
            k = low.index("years")
            if k + 1 < len(parts):
                year = parts[k + 1]

        rows.append({
            "organisme": organisme,
            "project": project,
            "year": year,
            "file_name": f.name,
            "file_path": str(f),
            "size_mb": round(f.stat().st_size / (1024 * 1024), 2),
        })

    return rows


def search_v2(
    query: str,
    collection: str = "ennosmart_memory_v2_global",
    top_k: int = 8,
    role: str = "",
    organisme: str = "",
) -> Dict[str, Any]:
    mod, _, err = import_any(["modules.RAG.vector_store"])
    if mod is None:
        raise RuntimeError(f"modules.RAG.vector_store introuvable : {err}")

    RAGVectorStore = getattr(mod, "RAGVectorStore", None)
    if RAGVectorStore is None:
        raise RuntimeError("RAGVectorStore introuvable")

    vs = RAGVectorStore(V2_CHROMA_DIR)
    res = vs.search(
        collection_name=collection,
        query=query,
        top_k=top_k,
        role_filter=role or None,
        metadata_filter={"organisme": organisme} if organisme else None,
        oversample=6,
    )

    if organisme:
        wanted = norm(organisme)
        res = [
            item for item in res
            if norm((item.get("metadata") or {}).get("organisme")) == wanted
        ][:top_k]

    return {
        "ok": True,
        "query": query,
        "collection": collection,
        "organisme_filter": organisme,
        "matches_count": len(res),
        "matches": res,
    }


def reset_all_v2(delete_organismes: bool = False) -> Dict[str, Any]:
    if V2_ROOT.exists():
        shutil.rmtree(V2_ROOT, ignore_errors=True)
    if delete_organismes and ORGANISMES_DIR.exists():
        shutil.rmtree(ORGANISMES_DIR, ignore_errors=True)
    ensure_dirs()
    return {"ok": True, "v2_root": str(V2_ROOT), "organismes_deleted": delete_organismes}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-file", default="")
    parser.add_argument("--organisme", default="")
    parser.add_argument("--project", default="")
    parser.add_argument("--year", default="")
    parser.add_argument("--reset-chroma", action="store_true")
    parser.add_argument("--no-copy", action="store_true")
    parser.add_argument("--vision-mode", default="text_only")
    parser.add_argument("--formula-mode", default="off")
    parser.add_argument("--defer-rebuild", action="store_true")

    parser.add_argument("--search", default="")
    parser.add_argument("--collection", default="ennosmart_memory_v2_global")
    parser.add_argument("--role", default="")
    parser.add_argument("--top-k", type=int, default=8)

    parser.add_argument("--rebuild-graph", action="store_true")
    parser.add_argument("--reset-all", action="store_true")
    parser.add_argument("--delete-organismes", action="store_true")
    parser.add_argument("--scan", action="store_true")

    args = parser.parse_args()

    if args.reset_all:
        print(json.dumps(reset_all_v2(delete_organismes=args.delete_organismes), ensure_ascii=False, indent=2))
        return 0

    if args.scan:
        print(json.dumps(scan_library(), ensure_ascii=False, indent=2))
        return 0

    if args.rebuild_graph:
        print(json.dumps(rebuild_global_graph_and_catalog(reset_chroma=args.reset_chroma), ensure_ascii=False, indent=2))
        return 0

    if args.build_file:
        rep = build_cir_final_v2(
            Path(args.build_file),
            organisme=args.organisme,
            project=args.project,
            year=args.year,
            copy_to_library=not args.no_copy,
            reset_chroma=args.reset_chroma,
            vision_mode=args.vision_mode,
            formula_mode=args.formula_mode,
            rebuild_catalog=not args.defer_rebuild,
        )
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0

    if args.search:
        print(json.dumps(search_v2(args.search, collection=args.collection, top_k=args.top_k, role=args.role), ensure_ascii=False, indent=2))
        return 0

    print("Utilise --build-file, --search, --scan, --rebuild-graph ou --reset-all")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
