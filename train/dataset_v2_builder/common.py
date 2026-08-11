from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Tuple

ROLE_LABELS = [
    "objectif",
    "verrou",
    "methode",
    "parametre",
    "resultat",
    "limite",
    "contribution",
    "bruit",
]

def ensure_dirs(root: Path) -> Dict[str, Path]:
    data = root / "train" / "data_v2"
    dirs = {
        "base": data,
        "anr": data / "raw" / "anr",
        "hal": data / "raw" / "hal",
        "hal_pdf": data / "raw" / "hal" / "pdf",
        "candidates": data / "candidates",
        "reports": data / "reports",
        "final": data / "final",
    }
    for p in dirs.values():
        p.mkdir(parents=True, exist_ok=True)
    return dirs

def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def norm_key(key: str) -> str:
    s = unicodedata.normalize("NFKD", str(key))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")

def text_hash(text: str) -> str:
    normalized = re.sub(r"\s+", " ", clean_text(text).lower()).strip()
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()

def jsonl_write(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    return n

def jsonl_iter(path: Path) -> Iterator[Dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    yield obj
            except Exception:
                continue

def split_passages(text: str, min_chars: int = 80, max_chars: int = 1400) -> List[str]:
    text = clean_text(text)
    if not text:
        return []

    blocks = [clean_text(x) for x in re.split(r"\n\s*\n+", text) if clean_text(x)]
    if len(blocks) <= 1:
        blocks = [
            clean_text(x)
            for x in re.split(r"(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Ý])", text)
            if clean_text(x)
        ]

    out: List[str] = []
    buffer = ""
    for block in blocks:
        if len(block) < min_chars:
            buffer = f"{buffer} {block}".strip()
            continue

        if buffer:
            block = f"{buffer} {block}".strip()
            buffer = ""

        if len(block) <= max_chars:
            out.append(block)
            continue

        sentences = re.split(r"(?<=[.!?;:])\s+", block)
        chunk = ""
        for sentence in sentences:
            candidate = (chunk + " " + sentence).strip()
            if chunk and len(candidate) > max_chars:
                if len(chunk) >= min_chars:
                    out.append(chunk)
                chunk = sentence
            else:
                chunk = candidate
        if len(chunk) >= min_chars:
            out.append(chunk)

    if buffer and len(buffer) >= min_chars:
        out.append(buffer)
    return out

def pick_text_fields(record: Dict[str, Any]) -> List[Tuple[str, str]]:
    preferred = (
        "resume", "abstract", "description", "descriptif", "objectif",
        "objectifs", "scientifique", "scientific", "summary", "contenu",
        "content", "presentation", "programme_scientifique",
    )
    excluded = (
        "adresse", "email", "telephone", "phone", "siret", "siren",
        "montant", "budget", "aide", "nom_responsable", "prenom",
    )
    selected: List[Tuple[str, str]] = []
    fallback: List[Tuple[str, str]] = []

    for raw_key, raw_value in record.items():
        key = norm_key(raw_key)
        if any(x in key for x in excluded):
            continue
        value = clean_text(raw_value)
        if len(value) < 120:
            continue
        if any(x in key for x in preferred):
            selected.append((str(raw_key), value))
        elif len(value) >= 250:
            fallback.append((str(raw_key), value))

    return selected or fallback[:3]

def find_first_value(record: Dict[str, Any], keys: Iterable[str]) -> str:
    normalized = {norm_key(k): clean_text(v) for k, v in record.items()}
    for wanted in keys:
        wanted_n = norm_key(wanted)
        for k, v in normalized.items():
            if k == wanted_n or wanted_n in k:
                if v:
                    return v
    return ""

def infer_project_id(record: Dict[str, Any], index: int, prefix: str) -> str:
    value = find_first_value(
        record,
        [
            "Projet.Code_Decision_ANR", "reference", "reference_projet",
            "code_projet", "id_projet", "project_id", "numero_projet",
            "acronyme", "acronym",
        ],
    )
    value = re.sub(r"\s+", "_", value.strip())
    return value[:100] if value else f"{prefix}_{index:07d}"

def infer_title(record: Dict[str, Any]) -> str:
    return find_first_value(
        record,
        [
            "Projet.Titre.Francais", "titre_fr", "titre", "title",
            "nom_projet", "Projet.Acronyme", "acronyme", "acronym",
        ],
    )[:500]

def _looks_like_record(d: Dict[str, Any]) -> bool:
    if not d:
        return False
    scalar = 0
    textual = 0
    for _, v in d.items():
        if v is None or isinstance(v, (str, int, float, bool)):
            scalar += 1
            if isinstance(v, str) and len(v.strip()) >= 20:
                textual += 1
    return scalar >= 4 and (textual >= 1 or scalar >= 8)

def _rows_from_columns_data(data: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    """
    Gère le format tabulaire :
      {"columns": ["a","b"], "data": [[1,2],[3,4]]}

    ainsi que :
      {"fields": [...], "data": [...]}
      {"schema":{"fields":[{"name":"a"}, ...]}, "data":[...]}
    """
    rows = data.get("data")
    if not isinstance(rows, list) or not rows:
        return

    columns = data.get("columns")
    if not isinstance(columns, list):
        columns = data.get("fields")

    if not isinstance(columns, list):
        schema = data.get("schema")
        if isinstance(schema, dict):
            fields = schema.get("fields")
            if isinstance(fields, list):
                cols = []
                for f in fields:
                    if isinstance(f, dict):
                        cols.append(f.get("name"))
                    else:
                        cols.append(f)
                columns = cols

    if isinstance(columns, list) and columns and isinstance(rows[0], list):
        cols = [str(c) for c in columns]
        for row in rows:
            if not isinstance(row, list):
                continue
            yield {
                cols[i]: row[i] if i < len(row) else None
                for i in range(len(cols))
            }
        return

    # Variante : première ligne = entêtes.
    if isinstance(rows[0], list) and all(isinstance(x, str) for x in rows[0]):
        cols = [str(x) for x in rows[0]]
        for row in rows[1:]:
            if isinstance(row, list):
                yield {
                    cols[i]: row[i] if i < len(row) else None
                    for i in range(len(cols))
                }

def _rows_from_column_arrays(data: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    """
    Gère :
      {"col1":[v1,v2], "col2":[w1,w2], ...}
    """
    arrays = {k: v for k, v in data.items() if isinstance(v, list)}
    if len(arrays) < 2:
        return
    lengths = [len(v) for v in arrays.values()]
    if not lengths or min(lengths) == 0:
        return
    # On demande une taille cohérente.
    common = max(set(lengths), key=lengths.count)
    compatible = {k: v for k, v in arrays.items() if len(v) == common}
    if len(compatible) < 2:
        return
    for i in range(common):
        yield {k: v[i] for k, v in compatible.items()}

def iter_dict_records(data: Any, _depth: int = 0) -> Iterator[Dict[str, Any]]:
    if _depth > 8:
        return

    if isinstance(data, list):
        if data and all(isinstance(x, dict) for x in data[: min(10, len(data))]):
            for item in data:
                if isinstance(item, dict):
                    yield item
            return

        # Tableau 2D avec première ligne = header.
        if data and isinstance(data[0], list) and all(isinstance(x, str) for x in data[0]):
            cols = [str(x) for x in data[0]]
            for row in data[1:]:
                if isinstance(row, list):
                    yield {
                        cols[i]: row[i] if i < len(row) else None
                        for i in range(len(cols))
                    }
            return

        for item in data:
            if isinstance(item, (dict, list)):
                yield from iter_dict_records(item, _depth + 1)
        return

    if not isinstance(data, dict):
        return

    # Pandas "split" / format tabulaire.
    if isinstance(data.get("data"), list):
        tabular = list(_rows_from_columns_data(data))
        if tabular:
            yield from tabular
            return

    # Column-oriented JSON : chaque clé est une colonne.
    col_rows = list(_rows_from_column_arrays(data))
    if col_rows:
        yield from col_rows
        return

    if _looks_like_record(data):
        yield data
        return

    for key in ("results", "records", "items", "projets", "projects", "projet", "project"):
        value = data.get(key)
        if isinstance(value, (list, dict)):
            yield from iter_dict_records(value, _depth + 1)
            return

    for value in data.values():
        if isinstance(value, (dict, list)):
            yield from iter_dict_records(value, _depth + 1)

def dedupe_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for row in rows:
        text = clean_text(row.get("text"))
        if len(text) < 60:
            continue
        h = text_hash(text)
        if h in seen:
            continue
        seen.add(h)
        row = dict(row)
        row["text_hash"] = h
        out.append(row)
    return out
