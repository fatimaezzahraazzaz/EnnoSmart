# -*- coding: utf-8 -*-
from __future__ import annotations

"""Chargement prudent des formulations de verrous CIR antérieurs.

Ce module intervient AVANT la reformulation des verrous courants.
Il charge les sections de type ``verrou`` du CIR précédent depuis Memory V2,
mais les expose uniquement comme exemples de style et de cadrage conceptuel.

Règle fondamentale :
- les faits du verrou courant viennent exclusivement des preuves NLP/RAG de
  l'année courante ;
- les textes antérieurs ne sont jamais une preuve et ne peuvent ni créer un
  verrou, ni ajouter un objet, une valeur ou une conclusion au dossier courant.
"""

import hashlib
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


VERSION = "previous_cir_verrou_context_v185_style_only"

LOCK_ROLE_VALUES = {
    "verrou",
    "verrou_rnd",
    "verrou_scientifique",
    "verrou_technologique",
    "incertitude",
}

LOCK_SECTION_MARKERS = (
    "verrou",
    "incertitude scientifique",
    "incertitude technique",
    "difficulte scientifique",
    "difficulte technique",
)

GENERIC_SECTION_TITLES = {
    "verrou",
    "verrous",
    "verrous rnd",
    "verrous scientifiques",
    "verrous technologiques",
    "incertitudes",
    "section verrou",
}


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", _clean(value).lower())
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^a-z0-9%+./_-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _truncate(value: Any, max_chars: int) -> str:
    text = _clean(value)
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    pos = max(cut.rfind("."), cut.rfind(";"), cut.rfind(":"))
    if pos < max_chars // 2:
        pos = max_chars
    return cut[:pos].rstrip() + "…"


def _dedupe(values: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for value in values:
        if not isinstance(value, Mapping):
            continue
        title = _clean(value.get("title"))
        text = _clean(value.get("text"))
        signature = _norm(f"{title}|{text}")[:700]
        if not signature or signature in seen:
            continue
        seen.add(signature)
        output.append(dict(value))
    return output


def _item_text(item: Mapping[str, Any]) -> str:
    return _clean(
        item.get("text")
        or item.get("source_text")
        or item.get("content")
        or item.get("excerpt")
    )


def _item_role(item: Mapping[str, Any]) -> str:
    return _norm(
        item.get("role")
        or item.get("section_type")
        or item.get("section_key")
        or item.get("pack_key")
    )


def _is_lock_item(item: Mapping[str, Any]) -> bool:
    role = _item_role(item)
    pack = _norm(item.get("pack_key"))
    section = _norm(item.get("section_title"))

    if role in LOCK_ROLE_VALUES or "verrou" in role:
        return True
    if "verrous_rnd" in pack or "verrou" in pack:
        return True
    if any(marker in section for marker in LOCK_SECTION_MARKERS):
        return True
    return False


def _title_from_item(item: Mapping[str, Any], text: str) -> str:
    candidates = [
        item.get("title"),
        item.get("section_title"),
        item.get("label"),
    ]
    for candidate in candidates:
        title = _clean(candidate)
        normalized = _norm(title)
        normalized = re.sub(r"^section\s+", "", normalized)
        if (
            len(title) >= 12
            and normalized not in GENERIC_SECTION_TITLES
            and not title.startswith("[")
        ):
            return _truncate(title, 220)

    first = re.split(r"(?<=[.!?;])\s+|\n+", text, maxsplit=1)[0]
    first = re.sub(r"^#{1,6}\s+", "", first)
    first = re.sub(r"^(?:[-*]|\d+[.)])\s+", "", first)
    first = re.sub(
        r"^(?:verrou|incertitude|difficulte)\s*(?:scientifique|technique|technologique|rnd)?\s*\d*\s*[:\-–—]?\s*",
        "",
        first,
        flags=re.I,
    )
    return _truncate(first, 220) if len(_clean(first)) >= 12 else ""


def _priority(item: Mapping[str, Any]) -> Tuple[float, int]:
    score = 0.0
    role = _item_role(item)
    pack = _norm(item.get("pack_key"))
    section = _norm(item.get("section_title"))
    if role == "verrou" or "verrou" in role:
        score += 100.0
    if "verrous_rnd" in pack:
        score += 60.0
    if "verrou" in section or "incertitude" in section:
        score += 30.0
    try:
        score += float(item.get("previous_section_priority") or 0.0)
    except Exception:
        pass
    text = _item_text(item)
    score += min(len(text), 1200) / 120.0
    return score, len(text)


def load_previous_verrou_context(
    organisme: str,
    project: str,
    current_year: str,
    *,
    max_previous_years: int = 1,
    max_examples: int = 16,
    max_text_chars: int = 900,
) -> Dict[str, Any]:
    """Retourne les meilleurs exemples de verrous du CIR précédent.

    La fonction réutilise le chargeur officiel de ``modules.CIR_MEMORY`` afin de
    respecter la même résolution organisme/projet/année que la comparaison N/N-1.
    """
    try:
        from modules.CIR_MEMORY.cir_memory import load_previous_cir_memory_items

        years, items = load_previous_cir_memory_items(
            organisme=organisme,
            project=project,
            current_year=current_year,
            max_previous_years=max(1, int(max_previous_years)),
        )
    except Exception as exc:
        return {
            "ok": False,
            "available": False,
            "version": VERSION,
            "error": str(exc),
            "previous_years": [],
            "examples": [],
            "prompt_block": "",
            "factual_use_allowed": False,
        }

    raw_items = [item for item in (items or []) if isinstance(item, Mapping)]
    lock_items = [item for item in raw_items if _is_lock_item(item)]
    lock_items.sort(key=_priority, reverse=True)

    examples: List[Dict[str, Any]] = []
    for item in lock_items:
        text = _item_text(item)
        if len(text) < 45:
            continue
        title = _title_from_item(item, text)
        if not title:
            continue
        year = _clean(item.get("previous_year") or item.get("year"))
        document = _clean(item.get("document"))
        example_id = "PCIR-" + hashlib.sha1(
            f"{year}|{title}|{text}".encode("utf-8", errors="ignore")
        ).hexdigest()[:10]
        examples.append({
            "example_id": example_id,
            "year": year,
            "title": title,
            "text": _truncate(text, max_text_chars),
            "role": _clean(item.get("role") or "verrou"),
            "pack_key": _clean(item.get("pack_key")),
            "section_title": _clean(item.get("section_title")),
            "document": document,
            "source": "experience_memory_v2_previous_cir",
            "usage": "style_and_conceptual_framing_only",
            "is_current_evidence": False,
        })
        if len(examples) >= max(1, int(max_examples)):
            break

    examples = _dedupe(examples)
    prompt_lines: List[str] = []
    for example in examples:
        prompt_lines.append(
            f"- [{example.get('year') or 'année antérieure'}] "
            f"{example['title']} — {_truncate(example['text'], 500)}"
        )

    return {
        "ok": True,
        "available": bool(examples),
        "version": VERSION,
        "organisme": organisme,
        "project": project,
        "current_year": str(current_year),
        "previous_years": [str(year) for year in (years or [])],
        "raw_previous_items_count": len(raw_items),
        "lock_items_count": len(lock_items),
        "examples_count": len(examples),
        "examples": examples,
        "prompt_block": "\n".join(prompt_lines),
        "usage": "style_and_conceptual_framing_only",
        "factual_use_allowed": False,
        "proof_policy": (
            "Les exemples antérieurs ne sont jamais des preuves du projet courant. "
            "Ils servent uniquement au niveau de formulation CIR et au cadrage lexical."
        ),
    }
