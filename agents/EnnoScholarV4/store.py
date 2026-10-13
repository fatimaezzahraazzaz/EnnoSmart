"""Canonical records, whole-corpus retrieval and append-only exposure ledger.

Retrieval scores propose reading windows. No score selects scientific finalists.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from collections import defaultdict

from agents.EnnoScholarV2.canonical import same_record, strong_keys
from agents.EnnoScholarV2.models import Article as LegacyArticle
from .models import Article, normalize_doi, public_link


def tokens(text):
    text = unicodedata.normalize("NFKD", text.casefold())
    return re.findall(r"[a-z0-9]+", "".join(c for c in text if not unicodedata.combining(c)))


def legacy(article):
    return LegacyArticle.model_validate(article.model_dump())


def compact(article, abstract_limit=1400):
    from .context import article_projection
    return article_projection(article, abstract_limit)


class ArticleStore:
    def __init__(self, state, maximum=20000, encoder=None):
        self.state, self.maximum, self.encoder = state, maximum, encoder
        self.registry = {aid: Article.model_validate(row) for aid, row in state.get("articles", {}).items()}
        self.windows = state.setdefault("candidate_windows", [])
        self.exposed = state.setdefault("exposed_article_ids", [])
        self.examined = state.setdefault("examined_article_ids", [])
        self.vector_cache = {}
        self.semantic_warning = ""
        self._keys, self._titles = defaultdict(list), defaultdict(list)
        for article in self.registry.values():
            self._index(article)

    def _index(self, article):
        for key in strong_keys(legacy(article)):
            if article.id not in self._keys[key]:
                self._keys[key].append(article.id)
        title = " ".join(tokens(article.title))
        if article.id not in self._titles[title]:
            self._titles[title].append(article.id)

    def save(self):
        self.state["articles"] = {aid: row.durable() for aid, row in self.registry.items()}

    def article(self, aid):
        if aid not in self.registry:
            raise ValueError("Article inconnu : utilisez un identifiant retourné par les outils.")
        return self.registry[aid]

    def insert(self, incoming, family=""):
        ids, dropped = [], 0
        for raw in incoming:
            row = Article.model_validate(raw.durable() if hasattr(raw, "durable") else raw.model_dump() if hasattr(raw, "model_dump") else raw)
            row.doi = normalize_doi(row.doi)
            row.arxiv_id = re.sub(r"v\d+$", "", row.arxiv_id.removesuffix(".pdf"))
            if not row.title.strip() or not (row.doi or row.arxiv_id or row.external_ids or public_link(row.url)):
                continue
            notice = row.model_dump(include={"title", "authors", "year", "doi", "arxiv_id", "url", "pdf_url", "sources", "external_ids"})
            row.provider_notices = row.provider_notices or [notice]
            candidate_ids = list(self._titles.get(" ".join(tokens(row.title)), []))
            for key in strong_keys(legacy(row)):
                candidate_ids.extend(self._keys.get(key, []))
            match = next((self.registry[aid] for aid in dict.fromkeys(candidate_ids)
                if same_record(legacy(self.registry[aid]), legacy(row))), None)
            if match:
                # A contradictory notice is retained for inspection; it cannot validate an association.
                if row.verified and (not match.verified or match.metadata_errors):
                    match.title, match.authors, match.year = row.title, row.authors, row.year
                    match.verified, match.metadata_errors = True, list(row.metadata_errors)
                for name in ("abstract", "doi", "arxiv_id", "url", "pdf_url", "authors", "year", "journal", "publisher", "publication_date"):
                    if not getattr(match, name) and getattr(row, name):
                        setattr(match, name, getattr(row, name))
                for name in ("sources", "matched_queries", "work_urls", "verification_sources", "provider_notices"):
                    values = getattr(match, name)
                    for value in getattr(row, name):
                        if value not in values:
                            values.append(value)
                match.external_ids = {**row.external_ids, **match.external_ids}
                if row.publisher_access != "unknown":
                    match.publisher_access = row.publisher_access
                match.citation_count = max(match.citation_count, row.citation_count)
                row = match
            else:
                if len(self.registry) >= self.maximum:
                    dropped += 1
                    continue
                stable = row.doi or row.arxiv_id or "|".join([row.title, str(row.year), *row.authors, row.url])
                row.id = "paper_" + hashlib.sha256(stable.encode()).hexdigest()[:20]
                row.canonical_work_id = row.id
                self.registry[row.id] = row
            if family and family not in row.scientific_families:
                row.scientific_families.append(family)
            row.source_version = row.source_version or ("arxiv:" + row.arxiv_id if row.arxiv_id and not row.doi else row.doi or row.url)
            row.access_level = "fulltext" if row.fulltext.get("text") else "abstract_only" if row.abstract else "metadata_only"
            if row.access_status == "metadata_only":
                row.access_status = "fulltext_available" if row.pdf_url else row.access_level
            self._index(row)
            ids.append(row.id)
        # Only explicit shared identifiers link distinct versions. Titles alone do not prove equivalence.
        linked = {}
        for row in self.registry.values():
            for key in strong_keys(legacy(row)):
                if key in linked:
                    other = linked[key]
                    old, new = row.canonical_work_id or row.id, other.canonical_work_id or other.id
                    for member in self.registry.values():
                        if (member.canonical_work_id or member.id) == old:
                            member.canonical_work_id = new
                    row.related_article_ids = list(dict.fromkeys([*row.related_article_ids, other.id]))
                    other.related_article_ids = list(dict.fromkeys([*other.related_article_ids, row.id]))
                else:
                    linked[key] = row
        self.save()
        return list(dict.fromkeys(ids)), dropped

    def search(self, query, limit=8, exclude_presented=True, family=None, ids=None):
        candidates = list(self.registry.values())
        blocked = set(self.exposed)
        blocked.update(aid for window in self.windows if window["status"] == "queued" for aid in window["article_ids"])
        if exclude_presented:
            candidates = [a for a in candidates if a.id not in blocked and a.id not in self.examined]
        if family:
            candidates = [a for a in candidates if any(family.casefold() in f.casefold() for f in a.scientific_families)]
        if ids is not None:
            candidates = [a for a in candidates if a.id in ids]
        if not candidates:
            return [], {}
        # Present candidates for model judgment without numeric scientific ranking.
        normalized = query.casefold().strip()
        exact = [a for a in candidates if normalized and normalized in
                 [a.id.casefold(), a.doi.casefold(), a.arxiv_id.casefold(), a.title.casefold()]]
        remaining = [a for a in candidates if a not in exact]
        return (exact + remaining)[:limit], {"method": "stored_candidates", "matched": len(candidates),
            "scores_are_scientific_ranking": False}

    def window(self, rows, query, purpose="retrieval"):
        # Bound transmission, not collection or scientific ranking. Omitted records
        # stay unexposed and can be retrieved through search_collected_articles.
        offered = []
        for article in rows[:8]:
            row = compact(article, 1600)
            if offered and len(json.dumps([*offered, row], ensure_ascii=False)) > 20000:
                break
            offered.append(row)
        window = {"window_id": "window_" + uuid.uuid4().hex, "query": query, "purpose": purpose,
            "article_ids": [a["id"] for a in offered], "status": "queued"}
        self.windows.append(window)
        return {"window_id": window["window_id"], "articles": offered,
            "delivery": {"offered": len(offered), "batch_total": len(rows), "remaining_in_batch": len(rows)-len(offered),
                "guidance": "Toutes les notices et abstracts sont conservés. Pour les fenêtres suivantes : search_collected_articles(exclude_presented=true), ou get_article par ID/titre. Les fenêtres ne constituent pas un classement scientifique."}}

    def delivered(self, window_ids):
        for window in self.windows:
            if window["window_id"] in window_ids and window["status"] == "queued":
                window["status"] = "presented"
                for aid in window["article_ids"]:
                    if aid not in self.exposed:
                        self.exposed.append(aid)

    def examined_article(self, aid):
        if aid not in self.exposed:
            raise ValueError("Cet article n'a pas encore été présenté au GPT.")
        if aid not in self.examined:
            self.examined.append(aid)

    def summary(self):
        finalists = self.state.get("evaluation", {}).get("recommended_article_ids", [])
        pinned = list(dict.fromkeys([*finalists, *[a.id for a in self.registry.values() if a.selected]]))
        return {"articles_total": len(self.registry), "presented_total": len(self.exposed), "examined_total": len(self.examined),
            "finalists": [compact(self.registry[aid], 300) for aid in pinned[:30] if aid in self.registry],
            "pinned_total": len(pinned),
            "recent_decisions": self.state.get("candidate_decisions", [])[-12:],
            "recent_windows": [{k: v for k, v in w.items() if k != "query"} for w in self.windows[-8:]],
            "retrieval": "Search the whole backend corpus with search_collected_articles; no provider-order window."}
