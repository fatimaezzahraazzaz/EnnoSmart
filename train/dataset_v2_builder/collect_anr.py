from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

import requests
from tqdm import tqdm

from common import (
    clean_text,
    dedupe_rows,
    ensure_dirs,
    infer_project_id,
    infer_title,
    iter_dict_records,
    jsonl_write,
    pick_text_fields,
    split_passages,
)

DATASET_SLUG = "anr-01-projets-anr-dos-et-dgds-detail-des-projets-et-des-partenaires"
DATASET_API = f"https://www.data.gouv.fr/api/1/datasets/{DATASET_SLUG}/"

def choose_project_json_resource(dataset_meta: Dict[str, Any]) -> Dict[str, Any]:
    resources = [
        r for r in dataset_meta.get("resources", [])
        if str(r.get("format") or "").lower() == "json"
        and str(r.get("type") or "main").lower() in {"main", ""}
    ]
    if not resources:
        raise RuntimeError("Aucune ressource JSON principale trouvée dans le dataset ANR.")

    def score(r: Dict[str, Any]):
        title = str(r.get("title") or "").lower()
        size = int(r.get("filesize") or 0)
        modified = str(r.get("last_modified") or r.get("created_at") or "")
        s = 0
        if "projet" in title or "project" in title:
            s += 20
        if "partenaire" in title or "partner" in title:
            s -= 8
        s += min(size / 10_000_000, 30)
        return (s, modified, size)

    return max(resources, key=score)

def download_json(url: str, cache_path: Path, force_download: bool = False) -> Any:
    if cache_path.exists() and not force_download:
        print(f"[ANR] Cache local utilisé : {cache_path}")
        print(f"[ANR] Taille cache : {cache_path.stat().st_size / 1024 / 1024:.1f} Mo")
        return json.loads(cache_path.read_text(encoding="utf-8-sig"))

    print(f"[ANR] Téléchargement : {url}")
    with requests.get(url, stream=True, timeout=(20, 600)) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        with cache_path.open("wb") as f, tqdm(
            total=total, unit="B", unit_scale=True, desc="ANR JSON"
        ) as bar:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
                    bar.update(len(chunk))

    print(f"[ANR] Cache enregistré : {cache_path}")
    print(f"[ANR] Taille reçue : {cache_path.stat().st_size / 1024 / 1024:.1f} Mo")
    return json.loads(cache_path.read_text(encoding="utf-8-sig"))

def structural_summary(data: Any) -> Dict[str, Any]:
    info = {"top_type": type(data).__name__}
    if isinstance(data, dict):
        info["top_keys"] = list(data.keys())[:30]
        info["top_value_types"] = {
            str(k): type(v).__name__ for k, v in list(data.items())[:20]
        }
        if isinstance(data.get("columns"), list):
            info["columns_count"] = len(data["columns"])
            info["columns_sample"] = [str(x) for x in data["columns"][:30]]
        if isinstance(data.get("data"), list):
            info["data_count"] = len(data["data"])
            if data["data"]:
                info["first_data_type"] = type(data["data"][0]).__name__
                if isinstance(data["data"][0], list):
                    info["first_row_size"] = len(data["data"][0])
    elif isinstance(data, list):
        info["top_length"] = len(data)
        if data:
            info["first_type"] = type(data[0]).__name__
    return info

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=r"C:\EnnoSmart")
    parser.add_argument("--max-projects", type=int, default=6000)
    parser.add_argument("--resource-url", default="")
    parser.add_argument("--force-download", action="store_true")
    args = parser.parse_args()

    root = Path(args.root)
    dirs = ensure_dirs(root)
    cache_path = dirs["anr"] / "anr_source_latest.json"

    resource = None
    resource_url = args.resource_url.strip()

    if not resource_url:
        print("[ANR] Recherche automatique de la dernière ressource officielle...")
        meta = requests.get(DATASET_API, timeout=60).json()
        resource = choose_project_json_resource(meta)
        resource_url = str(resource.get("latest") or resource.get("url") or "").strip()
        if not resource_url:
            raise RuntimeError("URL de ressource ANR introuvable.")
        print("[ANR] Ressource sélectionnée :", resource.get("title"))
        print("[ANR] Mise à jour :", resource.get("last_modified"))
        print("[ANR] Taille :", resource.get("filesize"))

    data = download_json(resource_url, cache_path, args.force_download)

    structure = structural_summary(data)
    print("[ANR] Structure JSON :")
    print(json.dumps(structure, ensure_ascii=False, indent=2))

    records = list(iter_dict_records(data))
    print(f"[ANR] Enregistrements détectés : {len(records)}")

    if args.max_projects > 0:
        records = records[: args.max_projects]

    rows: List[Dict[str, Any]] = []
    projects_with_text = 0

    for idx, record in enumerate(tqdm(records, desc="Extraction ANR"), start=1):
        project_id = infer_project_id(record, idx, "ANR")
        title = infer_title(record)
        text_fields = pick_text_fields(record)
        if not text_fields:
            continue

        projects_with_text += 1
        passage_no = 0
        for field_name, field_text in text_fields:
            for passage in split_passages(field_text):
                passage_no += 1
                rows.append({
                    "source": "ANR",
                    "project_id": project_id,
                    "document_id": project_id,
                    "title": title,
                    "section_title": field_name,
                    "text": clean_text(passage),
                    "language": "fr_or_mixed",
                    "source_field": field_name,
                    "passage_no": passage_no,
                    "annotation_origin": "public_rnd_corpus",
                })

    rows = dedupe_rows(rows)
    out = dirs["anr"] / "anr_passages.jsonl"
    count = jsonl_write(out, rows)

    report = {
        "source": "ANR",
        "dataset_api": DATASET_API,
        "resource_url": resource_url,
        "resource": resource,
        "json_structure": structure,
        "records_detected_before_limit": len(list(iter_dict_records(data))),
        "records_processed": len(records),
        "projects_with_text": projects_with_text,
        "passages_written": count,
        "cache_path": str(cache_path),
        "output": str(out),
    }
    report_path = dirs["reports"] / "anr_collection_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n[OK] ANR")
    print("Projets traités :", len(records))
    print("Avec texte      :", projects_with_text)
    print("Passages        :", count)
    print("Sortie          :", out)
    print("Rapport         :", report_path)

if __name__ == "__main__":
    main()
