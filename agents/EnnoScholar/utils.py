# -*- coding: utf-8 -*-
from __future__ import annotations

"""
utils.py — EnnoScholar V2.1

Fonctions communes.
V2.1 ajoute un nettoyage des phrases Frascati / qualification pour éviter
que les queries deviennent :
"question qualification permet-elle..."
"""

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Set


STOPWORDS = {
    # français
    "le", "la", "les", "un", "une", "des", "de", "du", "d", "dans", "sur", "avec",
    "et", "ou", "pour", "par", "en", "au", "aux", "a", "à", "est", "sont", "etre",
    "être", "ce", "cet", "cette", "ces", "afin", "plus", "moins", "entre", "chez",
    "qui", "que", "quoi", "dont", "leur", "leurs", "son", "ses", "sous", "mise",
    "place", "projet", "travaux", "travail", "objectif", "objectifs", "documents",
    "indiquent", "montre", "montrent", "permet", "permettent", "permet-elle",
    "vise", "visent", "enjeu", "enjeux", "contexte", "besoin", "solution",
    "solutions", "possible", "implicite", "probable", "contrainte", "contraintes",
    "maitrise", "maîtrise", "amelioration", "amélioration", "developpement",
    "développement", "question", "qualification", "systeme", "système", "atteint",
    "atteindre", "reste", "stable", "maitrise", "maîtrisé", "maitrise", "non",
    "suffisante", "insuffisante", "courante", "standard",
    # anglais
    "the", "a", "an", "of", "for", "to", "in", "on", "and", "or", "with", "by",
    "is", "are", "be", "from", "as", "at", "this", "that", "these", "those",
    "project", "objective", "work", "study", "analysis", "question", "qualification",
}


GENERIC_BAD_TITLE_PREFIXES = [
    r"^verrou\s+implicite\s+possible\s*[—\-:]\s*",
    r"^verrou\s+probable\s*[—\-:]\s*",
    r"^verrou\s+(a|à)\s+(verifier|vérifier)\s*[—\-:]\s*",
]

FRASCATI_QUESTION_PATTERNS = [
    r"question\s+de\s+qualification\s*:?.*?(?=\.|;|$)",
    r"le\s+système\s+atteint-il.*?(?=\.|;|$)",
    r"le\s+systeme\s+atteint-il.*?(?=\.|;|$)",
    r"la\s+solution\s+permet-elle.*?(?=\.|;|$)",
    r"le\s+comportement\s+du\s+système.*?(?=\.|;|$)",
    r"le\s+comportement\s+du\s+systeme.*?(?=\.|;|$)",
    r"la\s+solution\s+reste-t-elle.*?(?=\.|;|$)",
]


def norm(text: Any) -> str:
    s = str(text or "").lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("’", "'").replace("`", "'")
    s = re.sub(r"[^a-z0-9+\-/.% ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def clean_text(text: Any, max_chars: int = 2000) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    return s[:max_chars].strip()


def remove_frascati_question_text(text: Any) -> str:
    s = clean_text(text, 5000)
    for pat in FRASCATI_QUESTION_PATTERNS:
        s = re.sub(pat, " ", s, flags=re.I)
    s = re.sub(r"\b(question\s+de\s+qualification|verrou\s+implicite\s+possible)\b", " ", s, flags=re.I)
    return clean_text(s, 5000)


def clean_title(title: Any) -> str:
    t = clean_text(title, 220)
    for pat in GENERIC_BAD_TITLE_PREFIXES:
        t = re.sub(pat, "", t, flags=re.I)
    t = remove_frascati_question_text(t)
    return t.strip(" .;:-—")


def tokenize(text: Any) -> List[str]:
    text = remove_frascati_question_text(text)
    return [
        t for t in re.findall(r"[a-z0-9][a-z0-9+\-/.%]{2,}", norm(text))
        if t not in STOPWORDS and len(t) >= 3
    ]


def token_set(text: Any) -> Set[str]:
    return set(tokenize(text))


def flatten_text(obj: Any, max_chars: int = 4000) -> str:
    parts: List[str] = []

    def walk(x: Any):
        if len(" ".join(parts)) >= max_chars:
            return
        if isinstance(x, str):
            if len(x.strip()) > 15:
                parts.append(x.strip())
        elif isinstance(x, dict):
            for k, v in x.items():
                if k in {"raw_item", "style_examples", "articles", "sources"}:
                    continue
                walk(v)
        elif isinstance(x, list):
            for y in x[:30]:
                walk(y)

    walk(obj)
    return remove_frascati_question_text(clean_text(" ".join(parts), max_chars))


def read_json(path: str | Path, default: Any = None) -> Any:
    p = Path(path)
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def write_json(path: str | Path, data: Any) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def short_hash(*parts: Any, n: int = 12) -> str:
    raw = "|".join(clean_text(p, 500) for p in parts)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:n]


def dedupe_keep_order(items: List[str], max_items: int | None = None) -> List[str]:
    out = []
    seen = set()
    for x in items:
        x = clean_text(x, 120)
        if not x:
            continue
        k = norm(x)
        if k in seen:
            continue
        seen.add(k)
        out.append(x)
        if max_items and len(out) >= max_items:
            break
    return out


def jaccard(a: Any, b: Any) -> float:
    ta, tb = token_set(a), token_set(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)
