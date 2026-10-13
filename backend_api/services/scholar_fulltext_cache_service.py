# -*- coding: utf-8 -*-
from __future__ import annotations

"""Cache global PostgreSQL des textes intégraux EnnoScholar."""

import hashlib
import re
import threading
import unicodedata
from datetime import datetime
from typing import Any, Dict, Iterable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.database import engine
from db.models import Article, ScholarFulltextCache


_TABLE_LOCK = threading.Lock()
_TABLE_READY = False


def _ensure_table() -> None:
    global _TABLE_READY
    if _TABLE_READY:
        return
    with _TABLE_LOCK:
        if not _TABLE_READY:
            ScholarFulltextCache.__table__.create(bind=engine, checkfirst=True)
            _TABLE_READY = True


def ensure_fulltext_cache_ready() -> None:
    """Initialise la table avant de lancer un pool de threads.

    Cette initialisation ne doit pas avoir lieu depuis un thread qui conserve
    deja une connexion SQL. Avec 16 threads et le pool SQLAlchemy par defaut
    (15 connexions au maximum), le ``checkfirst`` demanderait une connexion
    supplementaire et pourrait attendre ``pool_timeout`` en cascade.
    """
    _ensure_table()


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text)).strip()


def normalize_doi(value: Any) -> str:
    doi = str(value or "").strip().casefold()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi)
    doi = re.sub(r"^doi\s*:\s*", "", doi)
    return doi.strip()


def article_cache_key(article: Article) -> str:
    doi = normalize_doi(article.doi)
    if doi:
        return f"doi:{doi}"
    identity = f"{_norm(article.title)}|{int(article.year or 0)}"
    return "title_year:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def get_cached_fulltext(db: Session, article: Article) -> Dict[str, Any] | None:
    _ensure_table()
    row = (
        db.query(ScholarFulltextCache)
        .filter(ScholarFulltextCache.cache_key == article_cache_key(article))
        .first()
    )
    if row is None or int(row.text_chars or 0) <= 0:
        return None
    payload = dict(row.payload_json or {})
    if payload.get("full_text_status") != "text_extracted" or not payload.get("ok"):
        return None
    payload.update({
        "fulltext_cache_hit": True,
        "fulltext_cache_id": int(row.id),
        "fulltext_cache_key": row.cache_key,
        "storage_mode": "global_database_cache",
    })
    return payload


def get_cached_fulltexts(
    db: Session,
    articles: Iterable[Article],
) -> Dict[int, Dict[str, Any]]:
    """Charge les cache hits d'un catalogue en une seule requete SQL."""
    article_rows = list(articles)
    if not article_rows:
        return {}

    _ensure_table()
    article_keys = {
        int(article.id): article_cache_key(article)
        for article in article_rows
    }
    cache_rows = (
        db.query(ScholarFulltextCache)
        .filter(ScholarFulltextCache.cache_key.in_(set(article_keys.values())))
        .all()
    )
    valid_by_key: Dict[str, Dict[str, Any]] = {}
    for row in cache_rows:
        if int(row.text_chars or 0) <= 0:
            continue
        payload = dict(row.payload_json or {})
        if payload.get("full_text_status") != "text_extracted" or not payload.get("ok"):
            continue
        payload.update({
            "fulltext_cache_hit": True,
            "fulltext_cache_id": int(row.id),
            "fulltext_cache_key": row.cache_key,
            "storage_mode": "global_database_cache",
        })
        valid_by_key[row.cache_key] = payload

    return {
        article_id: dict(valid_by_key[key])
        for article_id, key in article_keys.items()
        if key in valid_by_key
    }


def store_cached_fulltext(
    db: Session,
    article: Article,
    result: Dict[str, Any],
) -> ScholarFulltextCache | None:
    if not isinstance(result, dict):
        return None
    text = str(result.get("full_text") or "")
    text_chars = int(result.get("text_chars") or len(text))
    if not result.get("ok") or result.get("full_text_status") != "text_extracted" or text_chars <= 0:
        return None

    _ensure_table()
    key = article_cache_key(article)
    row = (
        db.query(ScholarFulltextCache)
        .filter(ScholarFulltextCache.cache_key == key)
        .first()
    )
    if row is None:
        row = ScholarFulltextCache(cache_key=key, payload_json={})

    content_sha = str(result.get("remote_sha256") or "").strip()
    if not content_sha:
        content_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    row.doi = normalize_doi(article.doi) or None
    row.normalized_title = _norm(article.title)
    row.year = article.year
    row.content_sha256 = content_sha
    row.text_chars = text_chars
    row.extraction_method = str(result.get("extraction_method") or "")[:100] or None
    row.source_kind = str(result.get("content_source_kind") or result.get("retrieval_stage") or "")[:50] or None
    row.source_url = result.get("fulltext_final_url") or result.get("fulltext_source_url") or article.url
    payload = dict(result)
    payload["storage_mode"] = "global_database_cache"
    row.payload_json = payload
    row.updated_at = datetime.utcnow()
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        row = (
            db.query(ScholarFulltextCache)
            .filter(ScholarFulltextCache.cache_key == key)
            .one()
        )
    db.refresh(row)
    return row
