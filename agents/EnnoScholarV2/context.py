"""Compact model-facing views. Complete metadata and verified texts stay in RAM."""
from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path

from .models import Article, ArticleAnalysis, FulltextResult, ScientificMemory
from .canonical import ArticleStore, same_record
from .tools.scientific_search import normalize_doi, normalized_title


def serialized_bytes(value) -> int:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return len(text.encode("utf-8"))


@lru_cache(maxsize=1)
def _tokenizer():
    """Optional, V2-owned vocabulary; never download or change a shared cache."""
    path = Path(__file__).resolve().parents[2] / ".codex_tmp/ennoscholar-v2-context/o200k_base.tiktoken"
    if not path.is_file():
        return None
    try:
        import base64
        import hashlib
        import tiktoken
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d":
            return None
        ranks = {base64.b64decode(token): int(rank) for token, rank in
            (line.split() for line in data.splitlines() if line)}
        pattern = "|".join([
            r"[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]*[\p{Ll}\p{Lm}\p{Lo}\p{M}]+(?i:'s|'t|'re|'ve|'m|'ll|'d)?",
            r"[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]+[\p{Ll}\p{Lm}\p{Lo}\p{M}]*(?i:'s|'t|'re|'ve|'m|'ll|'d)?",
            r"\p{N}{1,3}", r" ?[^\s\p{L}\p{N}]+[\r\n/]*", r"\s*[\r\n]+", r"\s+(?!\S)", r"\s+",
        ])
        return tiktoken.Encoding(name="ennoscholar_v2_o200k", pat_str=pattern, mergeable_ranks=ranks, special_tokens={})
    except (ImportError, OSError, ValueError):
        return None


def _visible(value):
    if isinstance(value, dict):
        return {key: _visible(item) for key, item in value.items() if key != "encrypted_content"}
    if isinstance(value, list):
        return [_visible(item) for item in value]
    return value


def estimate_tokens(value) -> int:
    """Plaintext estimate; opaque ciphertext bytes are not rendered model tokens."""
    value = _visible(value)
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    encoding = _tokenizer()
    if encoding is not None:
        return len(encoding.encode(text, disallowed_special=()))
    # Offline fallback without extra dependencies. An estimate, never a byte limit.
    cjk = len(re.findall(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]", text))
    return math.ceil((len(text) - cjk) / 4) + cjk


def context_metrics(items, instructions, tools) -> dict:
    return {"estimated_tokens": estimate_tokens(items) + estimate_tokens(instructions) + estimate_tokens(tools),
        "serialized_bytes": serialized_bytes(items) + serialized_bytes(instructions) + serialized_bytes(tools)}


def prune_native_compaction(items: list[dict]) -> list[dict]:
    positions = [index for index, item in enumerate(items) if item.get("type") == "compaction"]
    return items[positions[-1]:] if positions else items


def compact_article(article: Article, *, abstract_limit: int = 800) -> dict:
    data = article.model_dump(exclude={"provider_notices"})
    for key in ("relevance_score", "relevance_tag", "relevance_label", "retrieval_score", "ranking_reason", "relevance_origin", "relevance_for"):
        data.pop(key, None)
    data["title"] = article.title[:500]
    data["authors"] = article.authors[:5]
    data["abstract"] = article.abstract[:abstract_limit]
    data["abstract_truncated"] = len(article.abstract) > abstract_limit
    data["matched_queries"] = [query[:250] for query in article.matched_queries[:8]]
    data["work_urls"] = article.work_urls[:4]
    data["canonical_id"] = article.canonical_article_id or article.id
    return data


def compact_analysis(analysis: ArticleAnalysis) -> dict:
    data = analysis.model_dump()
    for key in ("problem", "method", "data", "results", "relevance", "analysis_limit"):
        data[key] = data[key][:600]
    for key in ("limitations", "future_work"):
        data[key] = [value[:300] for value in data[key][:3]]
    # Preserve exact verified excerpts and provenance; never generate new evidence.
    data["evidence"] = data["evidence"][:3]
    data["analysis_truncated"] = data != analysis.model_dump()
    return data


def fulltext_status(identifier: str, result: FulltextResult) -> dict:
    return {"article_id": identifier, "access_level": result.access_level,
        "available": result.access_level == "fulltext", "fulltext_source": result.fulltext_source,
        "fulltext_url": result.fulltext_url, "reason": result.reason[:500], "truncated": result.truncated}


def source_context(registry, budget: int, *, identifiers: list[str] | None = None, limit: int = 12, detailed=None) -> list[dict]:
    rows = []
    store = ArticleStore(registry)
    identifiers = identifiers if identifiers is not None else list(reversed(registry))
    for identifier in identifiers:
        if identifier not in registry or len(rows) >= limit:
            continue
        record = registry[identifier]
        article = record.article
        work = store.by_id.get(identifier)
        if work and not article.abstract and work.article.abstract:
            article = article.model_copy(update={"abstract": work.article.abstract})
        data = compact_article(article, abstract_limit=4000 if identifier in (detailed or set()) else 800)
        if record.fulltext:
            data["fulltext"] = fulltext_status(identifier, record.fulltext)
        if record.analysis:
            data["analysis"] = compact_analysis(record.analysis)
        # Shorten detail before dropping an inspected notice under token pressure.
        remaining_notices = min(limit - len(rows), len(identifiers) - identifiers.index(identifier))
        share = budget // max(1, remaining_notices)
        if estimate_tokens(data) > share:
            data["abstract"] = data["abstract"][:200]
            data["abstract_truncated"] = len(record.article.abstract) > len(data["abstract"])
            if "analysis" in data:
                analysis = data["analysis"]
                analysis["evidence"] = analysis["evidence"][:1]  # Exact quote unchanged.
                for key in ("problem", "method", "data", "results", "relevance", "analysis_limit"):
                    analysis[key] = analysis[key][:200]
                analysis["limitations"] = analysis["limitations"][:1]
                analysis["future_work"] = analysis["future_work"][:1]
                analysis["analysis_truncated"] = True
        size = estimate_tokens(data)
        if size <= budget:
            rows.append(data)
            budget -= size
    return rows


class ActiveSources:
    """Bounded visible window; inspection, pins and GPT finalists persist separately."""

    def __init__(self, registry, limit=12, memory: ScientificMemory | None = None):
        self.registry, self.limit = registry, max(1, min(limit, 12))
        self.memory = memory or ScientificMemory()
        self.store = ArticleStore(registry)
        self.identifiers = self._unique(self.memory.active_article_ids)
        self.detailed = set(self._unique(self.memory.detailed_article_ids))
        self.pinned = self._unique(self.memory.pinned_article_ids)[:self.limit]
        self.inspected = self._unique(self.memory.inspected_article_ids)
        self.finalists = self._unique(self.memory.finalist_article_ids)[:self.limit]
        self.candidate_window = self._unique(self.memory.candidate_window)[:self.limit]
        self.candidate_excerpts = dict(self.memory.candidate_excerpts)
        # Restore scientific memory before any fallback for a legacy/new session.
        self.identifiers = self._unique([*self.finalists, *self.pinned, *self.identifiers,
            *self.candidate_window, *self.inspected, *reversed(registry)])[:self.limit]

    def _unique(self, identifiers):
        selected, seen = [], set()
        for identifier in identifiers:
            work = self.store.work_id(identifier)
            if identifier in self.registry and work and work not in seen:
                selected.append(identifier)
                seen.add(work)
        return selected

    @staticmethod
    def identities(article):
        # Compatibility helper: title similarity is not an unconditional identity.
        from .canonical import strong_keys
        keys = strong_keys(article) | {("id", article.id)}
        if article.canonical_article_id:
            keys.add(("work", article.canonical_article_id))
        return keys | {("id", identifier) for identifier in article.related_article_ids}

    def resolve(self, row):
        identifier = row.get("article_id") or row.get("id") or row.get("canonical_id")
        if identifier in self.registry:
            return identifier
        if row.get("title"):
            candidate = Article.model_validate(row)
            return next((key for key, record in self.registry.items() if same_record(candidate, record.article)), None)
        return None

    def update(self, rows):
        self.store = ArticleStore(self.registry)
        recent = self._unique([identifier for row in rows if (identifier := self.resolve(row))])[:self.limit]
        self.candidate_window = recent
        self.candidate_excerpts = {identifier: row["abstract_excerpt"][:800] for row in rows
            if (identifier := self.resolve(row)) in recent and isinstance(row.get("abstract_excerpt"), str)}
        priority = self._unique([*self.finalists, *self.pinned])
        new = [identifier for identifier in recent if self.store.work_id(identifier) not in {self.store.work_id(key) for key in priority}]
        # All returned candidates get a visible slot. Stored pins/finalists are never
        # deleted when a new window temporarily occupies their display positions.
        reserved = min(len(new), self.limit)
        self.identifiers = self._unique([*priority[:self.limit - reserved], *recent,
            *priority, *self.identifiers])[:self.limit]
        return recent

    def inspect(self, rows, *, detailed=False):
        self.store = ArticleStore(self.registry)
        identifiers = self._unique([identifier for row in rows if (identifier := self.resolve(row))])
        self.inspected = self._unique([*identifiers, *self.inspected])
        self.pinned = self._unique([*identifiers, *self.pinned])[:self.limit]
        self.identifiers = self._unique([*identifiers, *self.finalists, *self.pinned, *self.identifiers])[:self.limit]
        if detailed:
            self.detailed.update(identifiers)
        return identifiers

    def select(self, finalists, pinned):
        if not all(isinstance(ids, list) and len(ids) <= self.limit and all(isinstance(key, str) and key in self.registry for key in ids) for ids in (finalists, pinned)):
            raise ValueError("Les finalistes et pins doivent être des identifiants connus, au plus douze par sélection.")
        self.store = ArticleStore(self.registry)
        self.finalists, self.pinned = self._unique(finalists), self._unique(pinned)
        self.identifiers = self._unique([*self.finalists, *self.pinned, *self.candidate_window, *self.identifiers])[:self.limit]
        return {"finalist_article_ids": self.finalists, "pinned_article_ids": self.pinned, "selection_updated": True}

    def snapshot(self):
        return ScientificMemory(active_article_ids=self.identifiers, candidate_window=self.candidate_window, candidate_excerpts=self.candidate_excerpts,
            inspected_article_ids=self.inspected, pinned_article_ids=self.pinned, finalist_article_ids=self.finalists,
            detailed_article_ids=sorted(self.detailed), pending_clarification=self.memory.pending_clarification)

    def render(self, token_budget):
        rows = source_context(self.registry, token_budget, identifiers=self.identifiers, limit=self.limit, detailed=self.detailed)
        for row in rows:
            if row["id"] in self.candidate_excerpts and row["id"] not in self.detailed:
                # This region was selected from a stored abstract, not generated text.
                row["abstract"] = self.candidate_excerpts[row["id"]][:len(row["abstract"]) or 800]
                row["abstract_truncated"] = True
        return rows

    def tool_result(self, name, result):
        if name in {"search_scientific", "search_collected_articles", "find_similar_articles", "read_article"}:
            result = dict(result)
            identifiers = self.update(result.pop("articles", []))
            result["article_ids"] = identifiers[:self.limit]
            result["selection_updated"] = True
            result["queries"] = [query[:250] for query in result.get("queries", [])[:8]]
            result["warnings"] = [dict(warning, reason=warning.get("reason", "")[:200]) for warning in result.get("warnings", [])[:3]]
            for warning in result["warnings"]:
                if "query" in warning:
                    warning["query"] = warning["query"][:250]
            return result
        if name == "get_article" and result.get("id"):
            self.inspect([result], detailed=True)
            return {"article_id": result["id"], "selection_updated": True}
        if name in {"get_fulltext", "analyze_articles", "analyze_article"}:
            rows = result.get("articles", [result])
            self.inspect(rows)
            if name != "get_fulltext":
                references = [{key: row[key] for key in ("article_id", "analysis_basis", "access_level", "error") if key in row} for row in rows]
                return {"articles": references, "selection_updated": True}
        return result
