# -*- coding: utf-8 -*-
from __future__ import annotations

"""
scripts/experience_memory_v2.py

Memory V2 EnnoSmart : enrichit les chunks V1 existants puis crée une base Chroma V2.
Entrée  : C:\\EnnoSmart\\storage\\experience_memory\\chunks\\*.chunks.json
Sortie  : C:\\EnnoSmart\\storage\\experience_memory_v2
"""
import argparse
import dataclasses
import hashlib
import importlib
import json
import math
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parents[1]

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
    

BASE_DIR = Path(os.getenv("ENNOSMART_BASE_DIR", r"C:\EnnoSmart"))
V1_ROOT = Path(os.getenv("ENNOSMART_EXPERIENCE_MEMORY_DIR", str(BASE_DIR / "storage" / "experience_memory")))
V2_ROOT = Path(os.getenv("ENNOSMART_EXPERIENCE_MEMORY_V2_DIR", str(BASE_DIR / "storage" / "experience_memory_v2")))

V1_CHUNKS_DIR = V1_ROOT / "chunks"
V2_CHUNKS_DIR = V2_ROOT / "chunks"
V2_CARDS_DIR = V2_ROOT / "cards"
V2_RELATIONS_DIR = V2_ROOT / "relations"
V2_CHROMA_DIR = V2_ROOT / "chroma"
V2_CATALOG = V2_ROOT / "catalog_v2.json"
V2_GLOBAL_GRAPH = V2_ROOT / "global_memory_graph.json"

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
    ],
    "genie_logiciel": [
        "java", "junit", "test unitaire", "tests unitaires", "compilation",
        "maven", "jacoco", "couverture", "evosuite", "sf110", "compileragent",
        "code source", "classe", "méthode", "method", "api", "framework",
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

STOPWORDS = set("""
a afin ai ainsi alors au aucun aussi autre aux avec avoir ce ces cet cette comme dans de des du elle en entre est et etre être fait font il ils je la le les leur leurs mais ne nos nous ou par pas plus pour que qui quoi sans se ses son sont sur un une vos votre vous
 the and with from that this are was were have has had into using based between under over
""".split())


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def ensure_dirs() -> None:
    for p in [V2_ROOT, V2_CHUNKS_DIR, V2_CARDS_DIR, V2_RELATIONS_DIR, V2_CHROMA_DIR]:
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


def import_any(names: List[str]):
    last_error = None
    for name in names:
        try:
            return importlib.import_module(name), name, None
        except Exception as exc:
            last_error = f"{name}: {exc}"
    return None, None, last_error


def tokenize(text: str) -> List[str]:
    out = []
    for x in norm(text).split():
        x = x.strip("._-/'")
        if len(x) < 3 or x in STOPWORDS or x.isdigit():
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
    return [k for k, _ in counts.most_common(top_k)]


def detect_domains(text: str, top_k: int = 3) -> List[Dict[str, Any]]:
    n = norm(text)
    scores = []
    for domain, kws in DOMAIN_KEYWORDS.items():
        score, hits = 0, []
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
        "verrou": 0.95, "etat_art": 0.88, "limite": 0.90,
        "methode": 0.82, "resultat": 0.86, "contribution": 0.92,
        "objectif": 0.78, "style": 0.65,
    }.get(role, 0.55)
    length_bonus = min(0.08, len(text) / 12000)
    validated_bonus = 0.05 if meta.get("memory_status") == "validated" else 0
    return round(min(1.0, base + length_bonus + validated_bonus), 3)


def infer_memory_class(role: str, meta: Dict[str, Any]) -> str:
    if meta.get("memory_type") == "style" or role == "style":
        return "style"
    return ROLE_TO_MEMORY_CLASS.get(role, "experience")


def get_chunk_text(chunk: Dict[str, Any]) -> str:
    return clean_text(chunk.get("text") or chunk.get("source_text") or "")


def get_meta(chunk: Dict[str, Any]) -> Dict[str, Any]:
    meta = chunk.get("metadata")
    return meta if isinstance(meta, dict) else {}


def stable_id(*parts: Any) -> str:
    raw = "|".join(str(x or "") for x in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def load_v1_chunks() -> List[tuple[Path, List[Dict[str, Any]]]]:
    out = []
    for p in sorted(V1_CHUNKS_DIR.glob("*.chunks.json")):
        data = read_json(p, [])
        if isinstance(data, list):
            out.append((p, [x for x in data if isinstance(x, dict)]))
    return out


def enrich_chunk(chunk: Dict[str, Any], source_file: Path) -> Dict[str, Any]:
    meta = dict(get_meta(chunk))
    text = get_chunk_text(chunk)
    role = clean_text(meta.get("role") or "autre").lower() or "autre"
    organisme = clean_text(
    meta.get("organisme")
    or meta.get("organization")
    or meta.get("client")
    or "Scalian"
    )

    project = clean_text(
        meta.get("project")
        or meta.get("project_name")
        or meta.get("project_id")
        or "Ai-Code"
    )

    year = clean_text(
        meta.get("year")
        or meta.get("annee")
        or "2025"
    )
    document = clean_text(meta.get("document") or meta.get("source_file") or source_file.name)

    keywords = extract_keywords(text)
    domains = detect_domains(text)
    memory_class = infer_memory_class(role, meta)
    source_section = clean_text(meta.get("section_number") or meta.get("section_id") or meta.get("section") or "")
    section_title = clean_text(meta.get("section_title") or meta.get("title") or "")

    chunk_id = clean_text(chunk.get("id") or meta.get("rag_chunk_id"))
    if not chunk_id:
        chunk_id = f"v2_{slugify(organisme)}_{slugify(project)}_{slugify(year)}_{role}_{stable_id(text)}"

    enriched_meta = {
        **meta,
        "v2": True,
        "v2_created_at": now_iso(),
        "chunk_id": chunk_id,
        "organisme": organisme,
        "organisme_slug": slugify(organisme),
        "project": project,
        "project_slug": slugify(project),
        "year": year,
        "document": document,
        "source_file": document,
        "role": role,
        "memory_class": memory_class,
        "memory_type_v2": memory_class,
        "document_type_v2": meta.get("document_type") or meta.get("source_kind") or "cir_final_consultant",
        "source_section": source_section,
        "section_title": section_title,
        "keywords": ", ".join(keywords),
        "keywords_list": keywords,
        "domains": ", ".join([d["domain"] for d in domains]),
        "domains_list": domains,
        "main_domain": domains[0]["domain"] if domains else "",
        "importance": compute_importance(role, text, meta),
        "consultant_memory_use": "style_only" if memory_class == "style" else "knowledge_and_experience",
        "can_use_as_fact": bool(meta.get("can_use_as_fact", memory_class != "style")),
        "can_use_as_style": bool(meta.get("can_use_as_style", memory_class == "style")),
        "relation_key_project": f"{slugify(organisme)}::{slugify(project)}::{year}",
        "relation_key_domain": domains[0]["domain"] if domains else "",
        "relation_key_role": role,
    }
    return {"id": chunk_id, "text": text, "source_text": text, "metadata": enriched_meta, "raw_item": chunk.get("raw_item")}


def make_knowledge_card(enriched_chunk: Dict[str, Any]) -> Dict[str, Any]:
    meta = enriched_chunk["metadata"]
    text = enriched_chunk["text"]
    role = meta.get("role", "autre")
    title = meta.get("section_title") or f"{role} — {meta.get('project')} {meta.get('year')}"
    card_id = f"card_{slugify(meta.get('organisme'))}_{slugify(meta.get('project'))}_{slugify(meta.get('year'))}_{role}_{stable_id(text)}"
    return {
        "card_id": card_id,
        "card_type": role,
        "memory_class": meta.get("memory_class"),
        "title": title,
        "summary": clean_text(text, 700),
        "organisme": meta.get("organisme"),
        "project": meta.get("project"),
        "year": meta.get("year"),
        "document": meta.get("document"),
        "source_chunk_id": meta.get("chunk_id"),
        "source_section": meta.get("source_section"),
        "section_title": meta.get("section_title"),
        "keywords": meta.get("keywords_list") or [],
        "domains": meta.get("domains_list") or [],
        "main_domain": meta.get("main_domain"),
        "importance": meta.get("importance"),
        "style_usable": meta.get("can_use_as_style"),
        "fact_usable": meta.get("can_use_as_fact"),
        "created_at": now_iso(),
    }


def similarity_keywords(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    ak = set(a.get("keywords") or [])
    bk = set(b.get("keywords") or [])
    if not ak or not bk:
        return 0.0
    return len(ak & bk) / max(1, len(ak | bk))


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
                relations.append({"type": "same_project_over_years", "from": arr[i]["card_id"], "to": arr[i + 1]["card_id"], "organisme": org, "project": project, "reason": "Même organisme et même projet sur années différentes."})

    for domain, arr in by_domain.items():
        for i in range(len(arr)):
            for j in range(i + 1, min(len(arr), i + 12)):
                a, b = arr[i], arr[j]
                sim = similarity_keywords(a, b)
                same_role = a.get("card_type") == b.get("card_type")
                if sim >= 0.18 or same_role:
                    relations.append({"type": "similar_experience", "from": a["card_id"], "to": b["card_id"], "domain": domain, "score": round(sim + (0.12 if same_role else 0), 3), "reason": "Même domaine scientifique et mots-clés proches."})
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


def build_v2(reset_chroma: bool = False, organism_filter: str = "") -> Dict[str, Any]:
    ensure_dirs()
    loaded = load_v1_chunks()
    all_chunks_v2, all_cards, reports = [], [], []

    for source_path, chunks in loaded:
        enriched, cards = [], []
        for ch in chunks:
            e = enrich_chunk(ch, source_path)
            meta = e["metadata"]
            if organism_filter and slugify(meta.get("organisme")) != slugify(organism_filter):
                continue
            enriched.append(e)
            cards.append(make_knowledge_card(e))
        if not enriched:
            continue
        source_id = source_path.name.replace(".chunks.json", "")
        out_chunks = V2_CHUNKS_DIR / f"{source_id}.chunks_v2.json"
        out_cards = V2_CARDS_DIR / f"{source_id}.cards.json"
        write_json(out_chunks, enriched)
        write_json(out_cards, cards)
        all_chunks_v2.extend(enriched)
        all_cards.extend(cards)
        reports.append({"source": str(source_path), "chunks_v1": len(chunks), "chunks_v2": len(enriched), "cards": len(cards), "chunks_file": str(out_chunks), "cards_file": str(out_cards)})

    relations = build_relations(all_cards)
    relations_file = V2_RELATIONS_DIR / "relations_global.json"
    write_json(relations_file, relations)
    write_json(V2_GLOBAL_GRAPH, {"created_at": now_iso(), "cards_count": len(all_cards), "relations_count": len(relations), "cards": all_cards, "relations": relations})

    chroma_reports = {}
    if all_chunks_v2:
        chroma_reports["global"] = chroma_store(all_chunks_v2, "ennosmart_memory_v2_global", reset=reset_chroma)
        by_org = defaultdict(list)
        for ch in all_chunks_v2:
            by_org[ch["metadata"].get("organisme_slug") or "unknown"].append(ch)
        for org_slug, arr in by_org.items():
            chroma_reports[f"organism_{org_slug}"] = chroma_store(arr, f"ennosmart_memory_v2_{org_slug}", reset=reset_chroma)

    catalog = {
        "ok": True, "version": "v2", "created_at": now_iso(),
        "v1_root": str(V1_ROOT), "v2_root": str(V2_ROOT),
        "chunks_count": len(all_chunks_v2), "cards_count": len(all_cards), "relations_count": len(relations),
        "organisms": sorted(list({c.get("organisme") for c in all_cards if c.get("organisme")})),
        "projects": sorted(list({f"{c.get('organisme')}::{c.get('project')}::{c.get('year')}" for c in all_cards})),
        "role_counts": dict(Counter(c.get("card_type") for c in all_cards)),
        "domain_counts": dict(Counter(c.get("main_domain") for c in all_cards if c.get("main_domain"))),
        "reports": reports,
        "outputs": {"catalog": str(V2_CATALOG), "global_graph": str(V2_GLOBAL_GRAPH), "relations": str(relations_file), "chroma": str(V2_CHROMA_DIR)},
        "chroma_reports": chroma_reports,
    }
    write_json(V2_CATALOG, catalog)
    return catalog


def search_v2(query: str, collection: str = "ennosmart_memory_v2_global", top_k: int = 8, role: str = "") -> Dict[str, Any]:
    mod, _, err = import_any(["modules.RAG.vector_store"])
    if mod is None:
        raise RuntimeError(f"modules.RAG.vector_store introuvable : {err}")
    RAGVectorStore = getattr(mod, "RAGVectorStore", None)
    if RAGVectorStore is None:
        raise RuntimeError("RAGVectorStore introuvable")
    vs = RAGVectorStore(V2_CHROMA_DIR)
    res = vs.search(collection_name=collection, query=query, top_k=top_k, role_filter=role or None, oversample=6)
    return {"ok": True, "query": query, "collection": collection, "matches_count": len(res), "matches": res}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--reset-chroma", action="store_true")
    parser.add_argument("--organisme", default="")
    parser.add_argument("--search", default="")
    parser.add_argument("--collection", default="ennosmart_memory_v2_global")
    parser.add_argument("--role", default="")
    parser.add_argument("--top-k", type=int, default=8)
    args = parser.parse_args()
    if args.build:
        print(json.dumps(build_v2(reset_chroma=args.reset_chroma, organism_filter=args.organisme), ensure_ascii=False, indent=2))
        return 0
    if args.search:
        print(json.dumps(search_v2(args.search, collection=args.collection, top_k=args.top_k, role=args.role), ensure_ascii=False, indent=2))
        return 0
    print("Utilise --build ou --search")
    return 1

if __name__ == "__main__":
    raise SystemExit(main())
