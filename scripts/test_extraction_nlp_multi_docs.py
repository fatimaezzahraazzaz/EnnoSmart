"""
scripts/test_extraction_nlp_multi_docs.py

Test UNIQUEMENT Extraction + NLP sur plusieurs documents bruts d'un même projet.
Ce script ne touche pas au RAG et ne touche pas à l'orchestrateur.

But :
  plusieurs documents bruts -> extraction séparée -> NLP séparé -> JSON projet consolidé

Sortie :
  debug/multi_docs_extraction_nlp/<organisme>/<projet>/per_document/*.extraction.json
  debug/multi_docs_extraction_nlp/<organisme>/<projet>/per_document/*.nlp.json
  debug/multi_docs_extraction_nlp/<organisme>/<projet>/project_extraction_nlp_summary.json

Exemple PowerShell :
python scripts/test_extraction_nlp_multi_docs.py `
  --files "C:\\docs\\presentation.pptx" "C:\\docs\\rapport.pdf" "C:\\docs\\essais.xlsx" `
  --organisme "client_test" `
  --project-name "Projet brut client" `
  --llm-model "ollama:qwen3:4b-instruct" `
  --vision-mode text_only `
  --no-gliner `
  --debug
"""

from __future__ import annotations

import sys
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import argparse
import dataclasses
import hashlib
import inspect
import json
import re
import time
import unicodedata
from pathlib import Path

# Correction import Windows :
# quand ce script est lancé depuis C:\EnnoSmart\scripts,
# Python peut ne pas voir C:\EnnoSmart dans sys.path.
# On ajoute donc explicitement la racine du projet pour importer modules.*.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from typing import Any


# ---------------------------------------------------------------------
# Utils
# ---------------------------------------------------------------------

def slugify(value: str) -> str:
    value = str(value or "").strip().lower()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "unknown"


def file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk_size)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def ensure_jsonable(obj: Any) -> Any:
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): ensure_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [ensure_jsonable(v) for v in obj]
    if dataclasses.is_dataclass(obj):
        return ensure_jsonable(dataclasses.asdict(obj))
    if hasattr(obj, "model_dump"):
        try:
            return ensure_jsonable(obj.model_dump())
        except Exception:
            pass
    if hasattr(obj, "dict"):
        try:
            return ensure_jsonable(obj.dict())
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        try:
            return ensure_jsonable(vars(obj))
        except Exception:
            pass
    return str(obj)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ensure_jsonable(data), ensure_ascii=False, indent=2), encoding="utf-8")


def clean_text(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_key(text: str) -> str:
    text = clean_text(text).lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def dedup_strings(values: list[str]) -> list[str]:
    seen, out = set(), []
    for value in values:
        text = clean_text(value)
        if not text:
            continue
        key = normalize_key(text)
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def unique_items(items: list[dict[str, Any]], text_key: str = "text") -> list[dict[str, Any]]:
    seen, out = set(), []
    for item in items:
        if not isinstance(item, dict):
            item = {text_key: str(item)}
        text = clean_text(item.get(text_key) or item.get("phrase_source") or item.get("value"))
        if not text:
            continue
        key = normalize_key(text)
        if key in seen:
            continue
        seen.add(key)
        item[text_key] = text
        out.append(item)
    return out


def flatten_strings(value: Any) -> list[str]:
    out: list[str] = []
    if value is None:
        return out
    if isinstance(value, str):
        text = clean_text(value)
        return [text] if text else []
    if isinstance(value, dict):
        for k in ["high_confidence", "mots_cles_high_confidence", "keywords", "candidates", "values", "items", "data"]:
            if k in value:
                out.extend(flatten_strings(value.get(k)))
        for k in ["text", "value", "phrase_source", "name", "label", "title"]:
            if k in value:
                out.extend(flatten_strings(value.get(k)))
        return out
    if isinstance(value, (list, tuple, set)):
        for v in value:
            out.extend(flatten_strings(v))
        return out
    text = clean_text(value)
    return [text] if text else []


def extract_final_field(nlp_json: dict[str, Any], keys: list[str]) -> list[str]:
    out: list[str] = []
    for key in keys:
        if key in nlp_json:
            out.extend(flatten_strings(nlp_json.get(key)))
    for parent in ["final_output", "synthesis", "synthese", "document_metadata"]:
        block = nlp_json.get(parent)
        if isinstance(block, dict):
            for key in keys:
                if key in block:
                    out.extend(flatten_strings(block.get(key)))
    return dedup_strings(out)


def add_source_item(bucket: list[dict[str, Any]], *, text: str, source_file: str, source_type: str, role: str,
                    confidence: float | None = None, section_role: str | None = None, passage_id: str | None = None) -> None:
    text = clean_text(text)
    if not text:
        return
    bucket.append({
        "text": text,
        "source_file": source_file,
        "source_type": source_type,
        "role": role,
        "section_role": section_role,
        "passage_id": passage_id,
        "confidence": confidence,
    })


def as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


# ---------------------------------------------------------------------
# Dynamic Extraction / NLP calls
# ---------------------------------------------------------------------

def call_with_supported_kwargs(func: Any, **kwargs: Any) -> Any:
    try:
        sig = inspect.signature(func)
        accepted = {name: kwargs[name] for name in sig.parameters if name in kwargs}
        return func(**accepted)
    except TypeError:
        if "file_path" in kwargs:
            return func(kwargs["file_path"])
        if "path" in kwargs:
            return func(kwargs["path"])
        raise


def run_extraction_raw(path: Path, args: argparse.Namespace) -> Any:
    """
    Lance modules.extraction.router.extract() et conserve l'objet ExtractionResult.

    Important : on garde l'objet brut pour le NLP, car modules.NLP.router.process_extraction()
    lit les attributs .text_chunks, .visual_chunks, .file_category, etc.
    """
    import importlib

    router = importlib.import_module("modules.extraction.router")

    if hasattr(router, "extract"):
        func = getattr(router, "extract")
    else:
        func = None
        for name in ["extract_document", "extract_file", "route_extraction", "run_extraction"]:
            if hasattr(router, name):
                func = getattr(router, name)
                break

    if func is None:
        raise ImportError("Aucune fonction d'extraction trouvée dans modules.extraction.router")

    formula_mode = "fast" if bool(args.formulas) else "off"

    return call_with_supported_kwargs(
        func,
        file_path=str(path),
        path=str(path),
        source_path=str(path),
        vision_mode=args.vision_mode,
        formula_mode=formula_mode,
        enable_formulas=bool(args.formulas),
        source_tag=args.source_tag,
        organisme=args.organisme,
        organisme_name=args.organisme,
    )


def extraction_to_json(extraction_result: Any) -> dict[str, Any]:
    data = ensure_jsonable(extraction_result)
    return data if isinstance(data, dict) else {"raw_result": data}


def build_nlp_config(args: argparse.Namespace) -> Any:
    """
    Construit NLPConfig en respectant la signature réelle de modules.NLP.router.NLPConfig.
    """
    import importlib

    router = importlib.import_module("modules.NLP.router")

    if not hasattr(router, "NLPConfig"):
        return None

    NLPConfig = getattr(router, "NLPConfig")
    kwargs = {
        "use_gliner": bool(args.gliner),
        "gliner_model": args.gliner_model,
        "use_regex": True,
        "use_llm_extractor": bool(args.use_llm),
        "llm_model": args.llm_model,
        "llm_extractor_model": args.llm_model,
        "model": args.llm_model,
        "ner_on_visual_chunks": bool(args.ner_on_visual_chunks),
        "include_debug": bool(args.debug),
        "debug": bool(args.debug),
        "organisme": args.organisme,
        "organisme_name": args.organisme,
        "source_tag": args.source_tag,
    }

    try:
        sig = inspect.signature(NLPConfig)
        accepted = {k: v for k, v in kwargs.items() if k in sig.parameters}
        return NLPConfig(**accepted)
    except Exception:
        return NLPConfig()


def run_nlp_from_extraction(extraction_result: Any, args: argparse.Namespace) -> dict[str, Any]:
    """
    Lance le NLP avec le vrai point d'entrée de ton router NLP.

    Priorité : process_extraction(extraction_result, config)
    car ton router NLP V7 utilise ce point d'entrée pour récupérer :
      - text_chunks
      - visual_chunks
      - file_category
      - document_metadata
    """
    import importlib

    router = importlib.import_module("modules.NLP.router")
    config = build_nlp_config(args)

    if hasattr(router, "process_extraction"):
        result = router.process_extraction(extraction_result, config=config)
    elif hasattr(router, "process_document"):
        text_chunks = list(getattr(extraction_result, "text_chunks", []) or [])
        visual_chunks = list(getattr(extraction_result, "visual_chunks", []) or [])
        file_name = str(getattr(extraction_result, "file_name", "doc") or "doc")
        file_category = getattr(extraction_result, "file_category", "unknown")
        file_category_str = file_category.value if hasattr(file_category, "value") else str(file_category)
        result = router.process_document(
            text_chunks=text_chunks,
            visual_chunks=visual_chunks,
            doc_id=Path(file_name).stem,
            config=config,
            file_category=file_category_str,
        )
    else:
        raise ImportError("Aucune fonction NLP compatible trouvée : process_extraction/process_document")

    if hasattr(router, "to_json"):
        try:
            result = router.to_json(result)
        except Exception:
            result = ensure_jsonable(result)
    else:
        result = ensure_jsonable(result)

    return result if isinstance(result, dict) else {"raw_nlp_result": result}


# ---------------------------------------------------------------------
# Metadata + consolidation
# ---------------------------------------------------------------------

def enrich_metadata(*, extraction_json: dict[str, Any], nlp_json: dict[str, Any], path: Path, args: argparse.Namespace, file_hash: str) -> dict[str, Any]:
    org_id = args.organisme_id or slugify(args.organisme)
    project_id = args.project_id or slugify(args.project_name)
    document_id = f"{org_id}_{project_id}_{file_hash[:12]}"
    doc_meta = dict(nlp_json.get("document_metadata", {}) or {})
    doc_meta.update({
        "organisme_name": args.organisme,
        "organisme_id": org_id,
        "project_name": args.project_name,
        "project_id": project_id,
        "file_name": path.name,
        "source_path": str(path),
        "file_hash": file_hash,
        "document_id": document_id,
        "file_suffix": path.suffix.lower(),
    })
    for k in ["title", "author", "page_count", "file_category", "source_tag", "creation_date"]:
        if k in extraction_json and k not in doc_meta:
            doc_meta[k] = extraction_json.get(k)
    nlp_json["document_metadata"] = doc_meta
    for chunk in nlp_json.get("chunks", []) or []:
        if isinstance(chunk, dict):
            meta = dict(chunk.get("metadata", {}) or {})
            meta.update({
                "organisme_name": args.organisme,
                "organisme_id": org_id,
                "project_name": args.project_name,
                "project_id": project_id,
                "file_name": path.name,
                "file_hash": file_hash,
                "document_id": document_id,
            })
            chunk["metadata"] = meta
    return nlp_json


def iter_evidences(nlp_json: dict[str, Any]) -> list[dict[str, Any]]:
    evidences: list[dict[str, Any]] = []
    for key in ["aggregated_evidence", "evidences", "preuves", "evidence_map"]:
        value = nlp_json.get(key)
        if isinstance(value, list):
            evidences.extend([v for v in value if isinstance(v, dict)])
        elif isinstance(value, dict):
            for role, items in value.items():
                for item in as_list(items):
                    if isinstance(item, dict):
                        item = dict(item)
                        item.setdefault("role", role)
                        evidences.append(item)
                    elif isinstance(item, str):
                        evidences.append({"role": role, "phrase_source": item})

    sections = nlp_json.get("document_metadata", {}).get("document_structure", {}).get("sections", [])
    if isinstance(sections, list):
        for sec in sections:
            if not isinstance(sec, dict):
                continue
            role = sec.get("role") or sec.get("section_role")
            content = sec.get("content") or sec.get("text")
            if content:
                evidences.append({
                    "role": role,
                    "section_role": role,
                    "phrase_source": content,
                    "title": sec.get("title"),
                    "passage_id": sec.get("section_id"),
                    "source": "document_structure.sections",
                    "confidence": sec.get("confidence"),
                })
    return evidences


def classify_verrou_evidence(ev: dict[str, Any]) -> str:
    role = normalize_key(str(ev.get("role") or ""))
    section_role = normalize_key(str(ev.get("section_role") or ""))
    text = normalize_key(str(ev.get("phrase_source") or ev.get("text") or ""))
    if "verrou" in role or "verrou" in section_role:
        return "confirmed"
    if any(s in text for s in ["verrou", "incertitude scientifique", "incertitude technique", "difficulte technique", "difficulté technique"]):
        return "confirmed"
    if any(s in text for s in ["limite", "frein", "probleme", "problème", "difficile", "ne permet pas", "insuffisant", "contrainte", "risque", "manque", "non resolu", "non résolu", "pas encore", "reste a", "reste à", "necessite", "nécessite"]):
        return "probable"
    return "other"


def consolidate_project(per_doc_results: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    project: dict[str, Any] = {
        "project_metadata": {
            "organisme_name": args.organisme,
            "organisme_id": args.organisme_id or slugify(args.organisme),
            "project_name": args.project_name,
            "project_id": args.project_id or slugify(args.project_name),
            "documents_count": len(per_doc_results),
        },
        "documents": [],
        "domaines": [],
        "objectifs": [],
        "verrous_confirmes": [],
        "verrous_probables_a_valider": [],
        "methodes_demarches": [],
        "resultats": [],
        "etat_art": [],
        "mots_cles": [],
        "technologies": [],
        "materiaux_composants": [],
        "equipements": [],
        "personnes": [],
        "organismes_partenaires": [],
        "preuves_par_document": [],
        "notes_qualite": [
            "Les verrous confirmés proviennent des champs verrous ou de sections clairement identifiées comme verrous.",
            "Les verrous probables sont déduits de limites, difficultés ou contraintes présentes dans les documents bruts.",
            "Aucun verrou probable ne doit être considéré comme définitif sans validation humaine.",
        ],
    }

    field_map = {
        "domaines": ["domaine_principal", "domaine_applicatif", "domaine_scientifique_detaille", "domaines", "domaines_scientifiques"],
        "objectifs": ["objectifs_rd", "objectifs", "objectifs_recherche", "objectifs_scientifiques"],
        "verrous_confirmes": ["verrous_techniques", "verrous", "verrous_scientifiques", "incertitudes"],
        "methodes_demarches": ["methodes_rd", "methodes", "demarches", "demarche_experimentale", "travaux_rd"],
        "resultats": ["resultats_rd", "resultats", "resultats_obtenus"],
        "etat_art": ["etat_art", "etat_de_lart", "etat_de_l_art", "travaux_anterieurs"],
        "mots_cles": ["mots_cles_high_confidence", "mots_cles", "mots_cles_projet", "keywords"],
        "technologies": ["technologies", "technologies_rd", "technologies_cles"],
        "materiaux_composants": ["materiaux_composants", "materiaux", "composants", "materials"],
        "equipements": ["equipements", "outils", "tools", "instruments"],
        "personnes": ["personnes", "people", "equipe", "rh"],
        "organismes_partenaires": ["organismes", "partenaires", "partenaires_rd", "laboratoires", "clients"],
    }
    role_name = {
        "domaines": "domaine", "objectifs": "objectif", "verrous_confirmes": "verrou",
        "methodes_demarches": "methode", "resultats": "resultat", "etat_art": "etat_art",
        "mots_cles": "mot_cle", "technologies": "technologie", "materiaux_composants": "materiau",
        "equipements": "equipement", "personnes": "personne", "organismes_partenaires": "organisme",
    }

    for doc in per_doc_results:
        nlp = doc["nlp_json"]
        meta = nlp.get("document_metadata", {}) or {}
        source_file = meta.get("file_name") or doc["file_name"]
        project["documents"].append({
            "file_name": source_file,
            "file_hash": meta.get("file_hash"),
            "document_id": meta.get("document_id"),
            "suffix": Path(source_file).suffix.lower(),
            "nlp_json_path": doc.get("nlp_json_path"),
            "extraction_json_path": doc.get("extraction_json_path"),
        })
        for bucket, keys in field_map.items():
            for text in extract_final_field(nlp, keys):
                add_source_item(project[bucket], text=text, source_file=source_file, source_type="final_field", role=role_name[bucket])

        doc_evidences = []
        for ev in iter_evidences(nlp):
            phrase = clean_text(ev.get("phrase_source") or ev.get("text") or ev.get("content"))
            if not phrase:
                continue
            role = str(ev.get("role") or "").strip()
            section_role = str(ev.get("section_role") or "").strip()
            confidence = ev.get("confidence")
            passage_id = ev.get("passage_id") or ev.get("section_id")
            doc_evidences.append({
                "role": role,
                "section_role": section_role,
                "phrase_source": phrase,
                "confidence": confidence,
                "passage_id": passage_id,
                "source_file": source_file,
            })
            verrou_type = classify_verrou_evidence(ev)
            if verrou_type == "confirmed":
                add_source_item(project["verrous_confirmes"], text=phrase, source_file=source_file, source_type="evidence", role="verrou", confidence=confidence, section_role=section_role, passage_id=passage_id)
            elif verrou_type == "probable":
                add_source_item(project["verrous_probables_a_valider"], text=phrase, source_file=source_file, source_type="evidence", role="verrou_probable", confidence=confidence, section_role=section_role, passage_id=passage_id)

        project["preuves_par_document"].append({
            "file_name": source_file,
            "evidences_count": len(doc_evidences),
            "evidences": unique_items(doc_evidences, text_key="phrase_source"),
        })

    for key in ["domaines", "objectifs", "verrous_confirmes", "verrous_probables_a_valider", "methodes_demarches", "resultats", "etat_art", "mots_cles", "technologies", "materiaux_composants", "equipements", "personnes", "organismes_partenaires"]:
        project[key] = unique_items(project[key], text_key="text")

    project["summary_counts"] = {k: len(project[k]) for k in ["domaines", "objectifs", "verrous_confirmes", "verrous_probables_a_valider", "methodes_demarches", "technologies", "mots_cles"]}
    project["summary_counts"]["documents"] = len(project["documents"])
    return project


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--files", nargs="+", required=True, help="Liste des documents bruts à analyser.")
    p.add_argument("--organisme", required=True)
    p.add_argument("--organisme-id", default=None)
    p.add_argument("--project-name", required=True)
    p.add_argument("--project-id", default=None)
    p.add_argument("--out-dir", default="debug/multi_docs_extraction_nlp")
    p.add_argument("--source-tag", default="DE_DOC")
    p.add_argument("--vision-mode", default="text_only", choices=["text_only", "fast", "full"])
    p.add_argument("--formulas", action="store_true")
    p.add_argument("--gliner", dest="gliner", action="store_true")
    p.add_argument("--no-gliner", dest="gliner", action="store_false")
    p.set_defaults(gliner=False)
    p.add_argument("--gliner-model", default="urchade/gliner_multi-v2.1")
    p.add_argument("--use-llm", dest="use_llm", action="store_true")
    p.add_argument("--no-llm", dest="use_llm", action="store_false")
    p.set_defaults(use_llm=True)
    p.add_argument("--llm-model", default="ollama:qwen3:4b-instruct")
    p.add_argument("--ner-on-visual-chunks", action="store_true")
    p.add_argument("--debug", action="store_true")
    p.add_argument("--max-docs", type=int, default=0, help="Limiter le nombre de documents pour un test rapide. 0 = tous.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    t0 = time.time()
    project_id = args.project_id or slugify(args.project_name)
    out_root = Path(args.out_dir) / slugify(args.organisme) / project_id
    per_doc_dir = out_root / "per_document"
    per_doc_dir.mkdir(parents=True, exist_ok=True)
    paths = [Path(p) for p in args.files]
    if args.max_docs and args.max_docs > 0:
        paths = paths[:args.max_docs]

    print("\n======== Extraction + NLP multi-documents ========")
    print(f"Organisme : {args.organisme}")
    print(f"Projet    : {args.project_name} ({project_id})")
    print(f"Documents : {len(paths)}")
    print(f"Sorties   : {out_root}")
    print(f"Projet root ajouté au sys.path : {PROJECT_ROOT}")

    per_doc_results: list[dict[str, Any]] = []
    for i, path in enumerate(paths, start=1):
        if not path.exists():
            print(f"\n[ERREUR] Fichier introuvable : {path}")
            continue

        print(f"\n-- Document {i}/{len(paths)} : {path.name}")
        doc_t0 = time.time()
        fh = file_sha256(path)

        try:
            print("  1) Extraction...")
            extraction_result = run_extraction_raw(path, args)
            extraction_json = extraction_to_json(extraction_result)
            extraction_path = per_doc_dir / f"{i:02d}_{slugify(path.stem)}.extraction.json"
            write_json(extraction_path, extraction_json)
            print(f"     OK Extraction sauvegardée : {extraction_path}")
            print(f"     Chunks texte : {len(getattr(extraction_result, 'text_chunks', []) or [])}")
            print(f"     Chunks visuels : {len(getattr(extraction_result, 'visual_chunks', []) or [])}")

            print("  2) NLP...")
            nlp_json = run_nlp_from_extraction(extraction_result, args)
            nlp_json = enrich_metadata(extraction_json=extraction_json, nlp_json=nlp_json, path=path, args=args, file_hash=fh)
            nlp_path = per_doc_dir / f"{i:02d}_{slugify(path.stem)}.nlp.json"
            write_json(nlp_path, nlp_json)
            print(f"     OK NLP sauvegardé : {nlp_path}")

            per_doc_results.append({
                "file_name": path.name,
                "source_path": str(path),
                "file_hash": fh,
                "extraction_json_path": str(extraction_path),
                "nlp_json_path": str(nlp_path),
                "nlp_json": nlp_json,
                "processing_time": round(time.time() - doc_t0, 2),
            })

            counts = {
                "objectifs": len(extract_final_field(nlp_json, ["objectifs_rd", "objectifs"])),
                "verrous": len(extract_final_field(nlp_json, ["verrous_techniques", "verrous"])),
                "methodes": len(extract_final_field(nlp_json, ["methodes_rd", "methodes", "demarches"])),
                "technologies": len(extract_final_field(nlp_json, ["technologies", "technologies_rd"])),
                "mots_cles": len(extract_final_field(nlp_json, ["mots_cles", "mots_cles_high_confidence", "mots_cles_projet"])),
            }
            print(f"     OK Résumé doc : {counts}")
            print(f"     Temps doc : {round(time.time() - doc_t0, 1)}s")

        except Exception as exc:
            print(f"     ERREUR Erreur sur {path.name} : {exc}")
            if args.debug:
                import traceback
                traceback.print_exc()

    print("\n-- Consolidation projet...")
    project_json = consolidate_project(per_doc_results, args)
    project_path = out_root / "project_extraction_nlp_summary.json"
    write_json(project_path, project_json)
    print(f"OK JSON projet sauvegardé : {project_path}")
    print("\nRésumé consolidation :")
    print(json.dumps(project_json["summary_counts"], ensure_ascii=False, indent=2))

    print("\nVerrous confirmés :")
    for idx, item in enumerate(project_json["verrous_confirmes"][:10], start=1):
        print(f" {idx}. {item['text'][:180]} — source: {item.get('source_file')}")
    print("\nVerrous probables à valider :")
    for idx, item in enumerate(project_json["verrous_probables_a_valider"][:10], start=1):
        print(f" {idx}. {item['text'][:180]} — source: {item.get('source_file')}")
    print(f"\nTemps total : {round(time.time() - t0, 1)}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
