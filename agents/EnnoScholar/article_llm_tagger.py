# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

from .external_source_base import cache_root


VALID_TAGS = {
    "Direct",
    "Connexe",
    "Fondamental",
    "Technique",
    "Hors sujet",
}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)) or default)
    except Exception:
        return default


def _clean_json_response(text: str) -> Any:
    text = str(text or "").strip()

    # Retire ```json ... ```
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)

    # Cherche le premier tableau JSON.
    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        text = text[start:end + 1]

    return json.loads(text)


def _intent_text(intent: Dict[str, Any]) -> str:
    parts: List[str] = []

    for key in (
        "verrou_title",
        "original_title",
        "scientific_problem",
        "technical_object",
        "phenomenon",
    ):
        value = intent.get(key)
        if value:
            parts.append(str(value))

    plan = intent.get("scientific_query_plan")
    if isinstance(plan, dict):
        for key in (
            "scientific_object",
            "independent_variables",
            "response_variables",
            "phenomena",
            "methods",
            "validation_concepts",
        ):
            values = plan.get(key) or []
            if not isinstance(values, list):
                values = [values]

            for value in values[:5]:
                if isinstance(value, dict):
                    value = (
                        value.get("term_en")
                        or value.get("term")
                        or value.get("value")
                    )
                if value:
                    parts.append(str(value))

    # déduplication simple
    seen = set()
    out = []

    for value in parts:
        value = " ".join(value.split()).strip()
        key = value.casefold()

        if value and key not in seen:
            seen.add(key)
            out.append(value)

    return "\n".join(out)[:3500]



LLM_TAGGER_CACHE_VERSION = "llm_tagger_policy_2026_09_18_v1"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)

    if raw is None or str(raw).strip() == "":
        return default

    return str(raw).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
        "oui",
    }


def _article_cache_material(
    article: Dict[str, Any],
    *,
    lock_text: str,
    model: str,
    abstract_chars: int,
) -> Dict[str, Any]:

    abstract = str(
        article.get("abstract")
        or article.get("tldr")
        or article.get("summary")
        or ""
    )[:abstract_chars]

    return {
        "cache_version": LLM_TAGGER_CACHE_VERSION,
        "model": model,
        "lock_text": lock_text,
        "doi": str(article.get("doi") or "").strip().lower(),
        "paper_id": str(article.get("paper_id") or "").strip(),
        "title": str(article.get("title") or "")[:500],
        "abstract": abstract,
    }


def _article_cache_path(
    article: Dict[str, Any],
    *,
    lock_text: str,
    model: str,
    abstract_chars: int,
) -> Path:

    material = _article_cache_material(
        article,
        lock_text=lock_text,
        model=model,
        abstract_chars=abstract_chars,
    )

    raw = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    digest = hashlib.sha256(
        raw.encode("utf-8", errors="replace")
    ).hexdigest()

    return (
        cache_root()
        / "llm_tags"
        / digest[:2]
        / f"{digest}.json"
    )


def _read_article_tag_cache(
    path: Path,
    ttl_days: int,
) -> Dict[str, Any] | None:

    try:
        if not path.exists():
            return None

        if ttl_days > 0:
            age = time.time() - path.stat().st_mtime

            if age > ttl_days * 86400:
                return None

        data = json.loads(
            path.read_text(encoding="utf-8")
        )

        if not isinstance(data, dict):
            return None

        tag = str(data.get("tag") or "").strip()

        if tag not in VALID_TAGS:
            return None

        return {
            "tag": tag,
            "reason": str(
                data.get("reason") or ""
            ).strip()[:500],
        }

    except Exception:
        return None


def _write_article_tag_cache(
    path: Path,
    *,
    tag: str,
    reason: str,
) -> bool:

    if tag not in VALID_TAGS:
        return False

    try:
        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        payload = {
            "cache_version": LLM_TAGGER_CACHE_VERSION,
            "created_at": time.strftime(
                "%Y-%m-%dT%H:%M:%S"
            ),
            "tag": tag,
            "reason": str(reason or "")[:500],
        }

        tmp = path.with_name(
            f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp"
        )

        tmp.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        os.replace(tmp, path)
        return True

    except Exception:
        return False


def tag_articles_with_llm(
    articles: List[Dict[str, Any]],
    intent: Dict[str, Any],
    llm_call: Callable[[List[Dict[str, str]]], Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:

    articles = [
        dict(a)
        for a in (articles or [])
        if isinstance(a, dict)
    ]

    batch_size = max(
        5,
        min(
            _env_int(
                "ENNOSCHOLAR_LLM_TAGGER_BATCH_SIZE",
                15,
            ),
            25,
        ),
    )

    abstract_chars = max(
        400,
        min(
            _env_int(
                "ENNOSCHOLAR_LLM_TAGGER_ABSTRACT_CHARS",
                1200,
            ),
            2000,
        ),
    )

    cache_enabled = _env_bool(
        "ENNOSCHOLAR_LLM_TAGGER_CACHE_ENABLED",
        True,
    )

    cache_ttl_days = max(
        0,
        _env_int(
            "ENNOSCHOLAR_LLM_TAGGER_CACHE_TTL_DAYS",
            30,
        ),
    )

    model = str(
        os.getenv(
            "ENNOSCHOLAR_LLM_TAGGER_MODEL",
            "gpt-4.1-mini",
        )
        or "gpt-4.1-mini"
    ).strip()

    report = {
        "enabled": True,
        "input_count": len(articles),
        "batch_size": batch_size,
        "llm_calls": 0,
        "llm_articles": 0,
        "llm_tagged_count": 0,
        "tagged_count": 0,
        "fallback_count": 0,
        "cache_enabled": cache_enabled,
        "cache_ttl_days": cache_ttl_days,
        "cache_hits": 0,
        "cache_misses": 0,
        "cache_writes": 0,
        "errors": [],
    }

    lock_text = _intent_text(intent)

    # Articles qui nécessitent réellement un appel LLM.
    pending = []

    for article in articles:

        cache_path = _article_cache_path(
            article,
            lock_text=lock_text,
            model=model,
            abstract_chars=abstract_chars,
        )

        cached = (
            _read_article_tag_cache(
                cache_path,
                cache_ttl_days,
            )
            if cache_enabled
            else None
        )

        if cached is not None:
            old_tag = str(article.get("tag") or "")

            article["tag_before_llm"] = old_tag
            article["tag"] = cached["tag"]
            article["llm_tag_reason"] = cached["reason"]
            article["llm_tagger_used"] = True
            article["llm_tagger_cache_hit"] = True

            report["cache_hits"] += 1
            report["tagged_count"] += 1
            continue

        if cache_enabled:
            report["cache_misses"] += 1

        pending.append(
            {
                "article": article,
                "cache_path": cache_path,
            }
        )

    report["llm_articles"] = len(pending)

    for start in range(
        0,
        len(pending),
        batch_size,
    ):
        batch_items = pending[
            start:start + batch_size
        ]

        batch = [
            item["article"]
            for item in batch_items
        ]

        payload = []

        for local_idx, article in enumerate(batch):
            payload.append({
                "id": local_idx,
                "title": str(
                    article.get("title") or ""
                )[:500],
                "abstract": str(
                    article.get("abstract")
                    or article.get("tldr")
                    or article.get("summary")
                    or ""
                )[:abstract_chars],
            })

        prompt = f"""
Tu aides un consultant CIR à trier des articles scientifiques.

VERROU SCIENTIFIQUE :
{lock_text}

Classe chaque article dans UNE SEULE catégorie :

Direct:
l'article traite directement le même problème scientifique,
le même objet ou la même incertitude centrale du verrou.

Connexe:
l'article est scientifiquement lié au verrou et utile pour comparaison,
mais ne traite pas directement l'incertitude centrale.

Fondamental:
revue, théorie, principes généraux ou état des connaissances
utile pour comprendre le verrou.

Technique:
outil, framework, implémentation, dataset, benchmark,
protocole ou méthode technique utile comme support,
mais qui n'est pas la preuve scientifique centrale.

Hors sujet:
l'article n'apporte pas d'information utile pour construire
l'état de l'art de ce verrou.

IMPORTANT:
- Direct est une catégorie stricte : l'article doit traiter directement
  le coeur du verrou scientifique ou technologique.
- Pour être Direct, le titre et/ou le résumé doivent fournir des éléments
  suffisants montrant que l'article traite réellement l'incertitude centrale.
- Si une dimension essentielle du verrou manque
  (objet scientifique ou technique, phénomène, méthode centrale,
  variable étudiée, effet recherché, condition ou contrainte importante),
  l'article ne doit généralement pas être classé Direct.
- Connexe correspond à un article de la même famille de problème,
  utile pour comparer ou comprendre le verrou, mais avec une autre méthode,
  technologie, configuration, condition expérimentale ou une couverture
  seulement partielle du problème.
- Une revue, survey, systematic review, article théorique ou synthèse
  des connaissances doit généralement être classé Fondamental,
  sauf s'il réalise lui-même une étude directement centrée sur le verrou.
- Technique correspond principalement à un outil, une implémentation,
  un protocole, un jeu de données, un benchmark, une architecture,
  un dispositif expérimental ou une méthode servant de support technique,
  sans traiter directement l'incertitude centrale.
- Hors sujet est réservé aux articles qui n'apportent aucune contribution
  scientifique ou technique réellement utile pour comprendre,
  comparer ou traiter le verrou.
- Un article partageant seulement quelques mots-clés avec le verrou
  ne doit pas être considéré comme pertinent.
- Ne te base pas uniquement sur les mots-clés :
  analyse le sens du titre et du résumé.
- Si les informations disponibles sont insuffisantes pour démontrer
  un lien direct, ne suppose pas que l'article est Direct.
- L'absence de résumé ne suffit pas à classer un article Hors sujet :
  si le titre montre clairement une relation scientifique ou technique
  avec le verrou, privilégie Connexe ou Technique selon son rôle.
- Hors sujet doit être utilisé seulement lorsqu'aucun lien utile
  avec le verrou ne peut être établi à partir des informations disponibles.
- Ne calcule aucun score.
- Réponds uniquement en JSON valide.

Format exact :
[
  {{
    "id": 0,
    "tag": "Direct",
    "reason": "justification très courte"
  }}
]

ARTICLES :
{json.dumps(payload, ensure_ascii=False)}
""".strip()

        try:
            response = llm_call([
                {
                    "role": "user",
                    "content": prompt,
                }
            ])

            report["llm_calls"] += 1

            if not response.get("ok"):
                raise RuntimeError(
                    response.get("error")
                    or "appel LLM échoué"
                )

            parsed = _clean_json_response(
                response.get("content") or ""
            )

            if not isinstance(parsed, list):
                raise ValueError(
                    "La réponse LLM n'est pas une liste."
                )

            by_id = {}

            for row in parsed:
                if not isinstance(row, dict):
                    continue

                try:
                    idx = int(row.get("id"))
                except Exception:
                    continue

                tag = str(
                    row.get("tag") or ""
                ).strip()

                if tag not in VALID_TAGS:
                    continue

                by_id[idx] = {
                    "tag": tag,
                    "reason": str(
                        row.get("reason") or ""
                    ).strip()[:500],
                }

            for local_idx, item in enumerate(
                batch_items
            ):
                article = item["article"]
                result = by_id.get(local_idx)

                if not result:
                    report["fallback_count"] += 1
                    continue

                old_tag = str(
                    article.get("tag") or ""
                )

                article["tag_before_llm"] = old_tag
                article["tag"] = result["tag"]
                article["llm_tag_reason"] = (
                    result["reason"]
                )
                article["llm_tagger_used"] = True
                article["llm_tagger_cache_hit"] = False

                report["tagged_count"] += 1
                report["llm_tagged_count"] += 1

                if (
                    cache_enabled
                    and _write_article_tag_cache(
                        item["cache_path"],
                        tag=result["tag"],
                        reason=result["reason"],
                    )
                ):
                    report["cache_writes"] += 1

        except Exception as exc:
            report["errors"].append(repr(exc))
            report["fallback_count"] += len(
                batch_items
            )

    return articles, report
