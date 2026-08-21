# -*- coding: utf-8 -*-
from __future__ import annotations

"""
scholar_memory_v2.py — EnnoScholar V146 conditional scientific memory

Mémoire V2 pour EnnoScholar.

Objectif :
- exploiter la base V2 organisme -> projet -> année ;
- retrouver les articles déjà utilisés dans des dossiers/sujets similaires ;
- injecter ces articles comme candidats prioritaires avant la recherche externe ;
- ne jamais halluciner : seules les sources présentes dans les rapports JSON locaux
  sont réutilisées.

Ce module ne dépend pas de la base SQL. Il scanne les rapports EnnoScholar déjà
exportés dans les dossiers V2, puis maintient un index JSON local.
"""

import json
import os
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


def _safe(x: Any, max_chars: int = 2000) -> str:
    s = re.sub(r"\s+", " ", str(x or "")).strip()
    return s[:max_chars].strip()


def _norm(text: Any) -> str:
    s = str(text or "").lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("’", "'").replace("`", "'")
    s = re.sub(r"[^a-z0-9+\-/.% ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


_STOP = {
    "the", "and", "or", "for", "with", "without", "from", "into", "study", "review",
    "analysis", "method", "methods", "model", "models", "system", "systems", "using",
    "based", "evaluation", "technical", "uncertainty", "performance",
    "les", "des", "pour", "avec", "dans", "sur", "une", "est", "sont", "projet",
    "travaux", "verrou", "incertitude", "validation", "repr", "representativite",
}


def _tokens(text: Any) -> set[str]:
    return {
        t
        for t in re.findall(r"[a-z0-9][a-z0-9+\-/.%]{2,}", _norm(text))
        if t not in _STOP and len(t) >= 3
    }


def _paper_key(p: Dict[str, Any]) -> str:
    doi = _safe(p.get("doi"), 200).lower()
    if doi:
        return "doi:" + doi

    pid = _safe(p.get("paper_id") or p.get("id"), 240).lower()
    if pid and not pid.startswith("tech:"):
        return "id:" + pid

    title = _norm(p.get("title"))[:220]
    year = str(p.get("year") or "")
    return f"title:{title}:{year}"


def _cache_root() -> Path:
    root = os.getenv("ENNOSCHOLAR_CACHE_DIR")
    if root:
        return Path(root)
    return Path.cwd() / "storage" / "ennoscholar_cache"


def _memory_index_path() -> Path:
    custom = os.getenv("ENNOSCHOLAR_MEMORY_V2_INDEX")
    if custom:
        return Path(custom)
    return _cache_root() / "memory_v2_index.json"



def _root_candidates() -> List[Path]:
    """Racines de rapports métier uniquement, jamais le cache EnnoScholar lui-même."""
    raw_roots: List[str] = []
    for name in [
        "ENNOSCHOLAR_MEMORY_V2_ROOT", "ENNOSMART_MEMORY_V2_ROOT",
        "ENNOSMART_V2_ROOT", "ENNOSCHOLAR_REPORTS_ROOT",
    ]:
        value = os.getenv(name)
        if value:
            raw_roots.extend([x.strip() for x in value.split(";") if x.strip()])

    raw_roots.extend([
        r"C:\EnnoSmart\storage\organismes",
        r"C:\EnnoSmart\outputs",
    ])

    roots: List[Path] = []
    seen = set()
    for raw in raw_roots:
        p = Path(raw)
        try:
            key = str(p.resolve()) if p.exists() else str(p)
        except Exception:
            key = str(p)
        if key in seen or not p.exists() or not p.is_dir():
            continue
        seen.add(key)
        roots.append(p)
    return roots

def _is_report_candidate(path: Path) -> bool:
    name = path.name.lower()
    parent = path.parent.name.lower()

    if path.suffix.lower() != ".json":
        return False

    if "ennoscholar" in parent:
        return True

    return (
        "ennoscholar" in name
        or "scholar_report" in name
        or "state_of_art" in name
        or "etat_art" in name
    )



def _iter_report_files(max_files: int = 300) -> Iterable[Path]:
    seen = set()
    blocked_parts = {"ennoscholar_cache", "backups", ".venv", "venv", "node_modules", "state_of_art_payload"}
    for root in _root_candidates():
        try:
            for path in root.rglob("*.json"):
                if len(seen) >= max_files:
                    return
                try:
                    low_parts = {part.lower() for part in path.parts}
                    if low_parts & blocked_parts:
                        continue
                    if path.name.lower() in {"memory_v2_index.json", "selection_payload.json", "article_cards_payload.json"}:
                        continue
                    if not _is_report_candidate(path):
                        continue
                    key = str(path.resolve())
                    if key in seen:
                        continue
                    seen.add(key)
                    yield path
                except Exception:
                    continue
        except Exception:
            continue

def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            return None


def _infer_v2_context(path: Path, report: Dict[str, Any]) -> Dict[str, Any]:
    # Priorité au JSON.
    organisme = _safe(report.get("organisme") or report.get("organization") or report.get("client"), 120)
    project = _safe(report.get("project") or report.get("projet") or report.get("project_name"), 160)
    year = _safe(report.get("year") or report.get("annee") or report.get("année"), 20)

    parts = list(path.parts)

    # Fallback chemin V2 : organisme / projet / année / ennoscholar / report.json
    year_idx = None
    for i, part in enumerate(parts):
        if re.fullmatch(r"20\d{2}", str(part)):
            year_idx = i
    if year_idx is not None:
        if not year:
            year = str(parts[year_idx])
        if not project and year_idx - 1 >= 0:
            project = str(parts[year_idx - 1])
        if not organisme and year_idx - 2 >= 0:
            organisme = str(parts[year_idx - 2])

    return {
        "organisme": organisme,
        "project": project,
        "year": year,
        "report_path": str(path),
    }


def _walk_articles(obj: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(obj, dict):
        # Formats principaux EnnoScholar.
        for key in [
            "articles",
            "selected_articles",
            "citation_articles",
            "references",
            "papers",
        ]:
            val = obj.get(key)
            if isinstance(val, list):
                for item in val:
                    if isinstance(item, dict) and _safe(item.get("title")):
                        yield item

        # Parcours récursif, mais on évite de parcourir certains champs énormes.
        for k, v in obj.items():
            if k in {"raw_item", "diagnostic_context", "state_of_art", "prompt", "content"}:
                continue
            if isinstance(v, (dict, list)):
                yield from _walk_articles(v)

    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                yield from _walk_articles(item)



def _normalize_article(article: Dict[str, Any], ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    title = _safe(article.get("title"), 320)
    if not title:
        return None

    source = _safe(article.get("source"), 80) or "unknown"
    # Empêche memory_v2+memory_v2+... et l'auto-indexation récursive.
    if "memory_v2" in source.lower() or article.get("memory_v2_prior") is True:
        return None
    if not _safe(ctx.get("project"), 160) or not _safe(ctx.get("year"), 20):
        return None

    abstract = _safe(article.get("abstract") or article.get("tldr") or article.get("summary"), 3000)
    if len(abstract) < 80:
        return None

    paper_id = _safe(article.get("paper_id") or article.get("id") or article.get("external_id") or article.get("doi"), 240)
    try:
        year = int(article.get("year")) if article.get("year") else None
    except Exception:
        year = None
    try:
        citation_count = int(article.get("citation_count") or article.get("citations") or 0)
    except Exception:
        citation_count = 0

    return {
        "source": source,
        "paper_id": paper_id or _paper_key(article),
        "title": title,
        "abstract": abstract,
        "year": year,
        "venue": _safe(article.get("venue") or article.get("journal") or article.get("publication_venue"), 180),
        "url": _safe(article.get("url") or article.get("landing_page_url"), 500),
        "pdf_url": _safe(article.get("pdf_url") or article.get("primary_pdf_url"), 500),
        "doi": _safe(article.get("doi"), 240),
        "authors": article.get("authors") if isinstance(article.get("authors"), list) else [],
        "citation_count": citation_count,
        "fields_of_study": article.get("fields_of_study") if isinstance(article.get("fields_of_study"), list) else [],
        "query": "memory_v2_prior_article",
        "memory_v2_prior": True,
        "memory_v2_origin": ctx,
        "memory_v2_previous_tag": _safe(article.get("tag") or article.get("previous_tag"), 80),
        "memory_v2_previous_relevance_score": article.get("relevance_score"),
    }


def build_memory_v2_index(force: bool = False, max_files: int | None = None) -> Dict[str, Any]:
    index_path = _memory_index_path()
    ttl_hours = int(os.getenv("ENNOSCHOLAR_MEMORY_V2_INDEX_TTL_HOURS", "24"))
    max_files = int(max_files or os.getenv("ENNOSCHOLAR_MEMORY_V2_MAX_FILES", "300"))

    if not force and index_path.exists():
        try:
            age_hours = (time.time() - index_path.stat().st_mtime) / 3600
            cached = json.loads(index_path.read_text(encoding="utf-8"))
            if (
                age_hours <= ttl_hours
                and isinstance(cached, dict)
                and cached.get("version") == "memory_v2_index_v145_non_recursive"
                and isinstance(cached.get("articles"), list)
            ):
                return cached
        except Exception:
            pass

    articles: List[Dict[str, Any]] = []
    seen = set()
    scanned_files = 0
    for path in _iter_report_files(max_files=max_files):
        scanned_files += 1
        report = _read_json(path)
        if not isinstance(report, dict):
            continue
        ctx = _infer_v2_context(path, report)
        if not _safe(ctx.get("project"), 160) or not _safe(ctx.get("year"), 20):
            continue
        for raw_article in _walk_articles(report):
            normalized = _normalize_article(raw_article, ctx)
            if not normalized:
                continue
            key = _paper_key(normalized)
            if key in seen:
                continue
            seen.add(key)
            articles.append(normalized)

    payload = {
        "version": "memory_v2_index_v145_non_recursive",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "roots": [str(x) for x in _root_candidates()],
        "scanned_files": scanned_files,
        "articles_count": len(articles),
        "articles": articles,
    }
    try:
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return payload


def _intent_text(intent: Dict[str, Any]) -> str:
    """Mémoire comparée au verrou local, sans contexte global ni domaine générique."""
    source_basis = intent.get("source_basis") or {}
    if not isinstance(source_basis, dict):
        source_basis = {}
    return " ".join([
        _safe(intent.get("verrou_title"), 300),
        _safe(intent.get("original_title"), 300),
        _safe(intent.get("scientific_problem"), 700),
        _safe(intent.get("technical_object"), 350),
        _safe(intent.get("phenomenon"), 350),
        " ".join(map(str, intent.get("methods") or [])),
        " ".join(map(str, intent.get("key_terms_en") or [])),
        " ".join(map(str, intent.get("key_terms_fr") or [])),
        " ".join(map(str, intent.get("strong_anchors") or [])),
        _safe(source_basis.get("source_text_excerpt"), 1200),
    ])

def _article_text(article: Dict[str, Any]) -> str:
    return " ".join([
        _safe(article.get("title"), 300),
        _safe(article.get("abstract") or article.get("tldr"), 1800),
        _safe(article.get("venue"), 120),
        " ".join(map(str, article.get("fields_of_study") or [])),
    ])



def _similarity(intent: Dict[str, Any], article: Dict[str, Any]) -> float:
    intent_text = _intent_text(intent)
    article_text = _article_text(article)
    itoks = _tokens(intent_text)
    atoks = _tokens(article_text)
    if not itoks or not atoks:
        return 0.0

    common = itoks & atoks
    title_tokens = _tokens(article.get("title"))
    title_common = itoks & title_tokens

    article_norm = " " + _norm(article_text) + " "
    strong_anchors = [str(x) for x in intent.get("strong_anchors") or []]
    exact_anchor_hits = 0
    for anchor in strong_anchors[:16]:
        normalized = _norm(anchor)
        if not normalized:
            continue
        pattern = re.escape(normalized).replace(r"\ ", r"\s+")
        if re.search(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", article_norm):
            exact_anchor_hits += 1

    # Sans ancre exacte, exiger au moins trois termes informatifs communs.
    if exact_anchor_hits == 0 and len(common) < 3:
        return 0.0

    overlap = len(common) / max(6, min(len(itoks), 30))
    title_overlap = len(title_common) / max(4, min(len(itoks), 16))
    anchor_score = min(exact_anchor_hits / 3.0, 1.0)
    score = 0.50 * min(overlap, 1.0) + 0.25 * min(title_overlap, 1.0) + 0.25 * anchor_score
    return round(max(0.0, min(score, 1.0)), 4)

def match_memory_v2_articles(
    intent: Dict[str, Any],
    *,
    organisme: str = "",
    project: str = "",
    year: str = "",
    top_k: int | None = None,
    min_score: float | None = None,
    force_reindex: bool | None = None,
) -> Dict[str, Any]:
    enabled = str(os.getenv("ENNOSCHOLAR_MEMORY_V2_ENABLED", "0")).lower().strip() in {"1", "true", "yes", "on"}
    if not enabled:
        return {"enabled": False, "articles": [], "candidates_count": 0, "reason": "disabled_by_env"}

    top_k = int(top_k or os.getenv("ENNOSCHOLAR_MEMORY_V2_TOP_K", "0"))
    min_score = float(min_score if min_score is not None else os.getenv("ENNOSCHOLAR_MEMORY_V2_MIN_SCORE", "0.24"))
    force_reindex = bool(force_reindex) or str(os.getenv("ENNOSCHOLAR_MEMORY_V2_FORCE_REINDEX", "0")).lower().strip() in {"1", "true", "yes", "on"}

    index = build_memory_v2_index(force=force_reindex)
    articles = index.get("articles") or []

    current_org = _norm(organisme)
    current_project = _norm(project)
    current_year = _norm(year)

    scored: List[Dict[str, Any]] = []
    for article in articles:
        if not isinstance(article, dict):
            continue

        origin = article.get("memory_v2_origin") or {}
        # On évite de réinjecter exactement le même rapport de la même année/projet.
        if (
            current_project
            and current_year
            and _norm(origin.get("project")) == current_project
            and _norm(origin.get("year")) == current_year
            and (not current_org or _norm(origin.get("organisme")) == current_org)
        ):
            continue

        score = _similarity(intent, article)
        if score < min_score:
            continue

        x = dict(article)
        x["memory_v2_similarity"] = score
        x["memory_v2_match_reason"] = "Article antérieur réinjecté après compatibilité locale stricte avec le verrou courant."
        scored.append(x)

    # Déduplication : un article peut être dans plusieurs rapports.
    dedup: Dict[str, Dict[str, Any]] = {}
    for article in scored:
        key = _paper_key(article)
        previous = dedup.get(key)
        if previous is None or float(article.get("memory_v2_similarity") or 0) > float(previous.get("memory_v2_similarity") or 0):
            dedup[key] = article

    out = list(dedup.values())
    out.sort(
        key=lambda a: (
            float(a.get("memory_v2_similarity") or 0),
            1 if str(a.get("memory_v2_previous_tag") or "") == "Direct" else 0,
            int(a.get("citation_count") or 0),
        ),
        reverse=True,
    )
    out = out[:max(1, top_k)]

    return {
        "enabled": True,
        "version": "memory_v2_match_v145_strict",
        "index_articles_count": int(index.get("articles_count") or len(articles)),
        "scanned_files": int(index.get("scanned_files") or 0),
        "top_k": top_k,
        "min_score": min_score,
        "candidates_count": len(out),
        "articles": out,
    }


# =============================================================================
# V146 — Memory V2 est une source conditionnelle, jamais un bonus automatique
# =============================================================================
def _v146_exact(text_norm: str, phrase: str) -> bool:
    p = _norm(phrase)
    if not p:
        return False
    pattern = re.escape(p).replace(r"\ ", r"\s+")
    return bool(re.search(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", text_norm))


def _v146_memory_role_score(intent: Dict[str, Any], article: Dict[str, Any]) -> Dict[str, Any]:
    text = " " + _norm(_article_text(article)) + " "
    title = " " + _norm(article.get("title")) + " "
    aliases = intent.get("concept_aliases") if isinstance(intent.get("concept_aliases"), dict) else {}
    core_hits: List[str] = []
    primary_set = {str(x) for x in intent.get("primary_core_concepts") or []}
    for concept in intent.get("core_concepts") or []:
        candidates = aliases.get(concept) or [concept]
        hit = False
        for alias in candidates:
            # Generic exact matching: no acronym/domain deny-list.
            if _v146_exact(text, str(alias)):
                hit = True
                break
        if hit:
            core_hits.append(str(concept))
    primary_hits = [c for c in core_hits if c in primary_set] if primary_set else core_hits[:1]
    method_hits = [str(x) for x in intent.get("method_anchors") or [] if _v146_exact(text, str(x))]
    phenomenon_hits = [str(x) for x in intent.get("phenomenon_anchors") or [] if _v146_exact(text, str(x))]
    title_core = [c for c in core_hits if any(_v146_exact(title, a) for a in (aliases.get(c) or [c]))]

    lexical = _similarity(intent, article)
    support = len(method_hits) + len(phenomenon_hits)
    if not primary_hits:
        return {"score": 0.0, "core_hits": core_hits, "primary_hits": [], "method_hits": method_hits, "phenomenon_hits": phenomenon_hits,
                "title_core_hits": [], "reason": "no_primary_core_concept"}
    if support == 0 and len(core_hits) < 2:
        return {"score": 0.0, "core_hits": core_hits, "method_hits": [], "phenomenon_hits": [],
                "title_core_hits": title_core, "reason": "core_without_method_or_phenomenon"}

    score = 0.42 * min(len(primary_hits) / 2.0, 1.0)
    score += 0.18 * min(support / 2.0, 1.0)
    score += 0.14 * min(len(title_core), 1.0)
    score += 0.26 * lexical
    return {"score": round(min(score, 1.0), 4), "core_hits": core_hits, "primary_hits": primary_hits,
            "method_hits": method_hits, "phenomenon_hits": phenomenon_hits,
            "title_core_hits": title_core, "reason": "prequalified"}


def match_memory_v2_articles(
    intent: Dict[str, Any], *, organisme: str = "", project: str = "", year: str = "",
    top_k: int | None = None, min_score: float | None = None,
    force_reindex: bool | None = None,
) -> Dict[str, Any]:
    enabled = str(os.getenv("ENNOSCHOLAR_MEMORY_V2_ENABLED", "0")).lower().strip() in {"1", "true", "yes", "on"}
    if not enabled:
        return {"enabled": False, "articles": [], "candidates_count": 0, "reason": "disabled_by_env"}
    top_k = int(top_k or os.getenv("ENNOSCHOLAR_MEMORY_V2_TOP_K", "0"))
    min_score = float(min_score if min_score is not None else os.getenv("ENNOSCHOLAR_MEMORY_V2_MIN_SCORE", "0.30"))
    force_reindex = bool(force_reindex) or str(os.getenv("ENNOSCHOLAR_MEMORY_V2_FORCE_REINDEX", "0")).lower().strip() in {"1", "true", "yes", "on"}
    index = build_memory_v2_index(force=force_reindex)
    articles = index.get("articles") or []
    current_org, current_project, current_year = _norm(organisme), _norm(project), _norm(year)
    scored: List[Dict[str, Any]] = []
    rejected_no_core = 0
    rejected_below = 0

    for article in articles:
        if not isinstance(article, dict):
            continue
        origin = article.get("memory_v2_origin") or {}
        if (current_project and current_year and _norm(origin.get("project")) == current_project
            and _norm(origin.get("year")) == current_year
            and (not current_org or _norm(origin.get("organisme")) == current_org)):
            continue
        gate = _v146_memory_role_score(intent, article)
        if gate["score"] <= 0:
            rejected_no_core += 1
            continue
        if gate["score"] < min_score:
            rejected_below += 1
            continue
        x = dict(article)
        x.update({
            "memory_v2_similarity": gate["score"],
            "memory_v2_prequalified": True,
            "memory_v2_core_hits": gate["core_hits"],
            "memory_v2_primary_hits": gate.get("primary_hits") or [],
            "memory_v2_method_hits": gate["method_hits"],
            "memory_v2_phenomenon_hits": gate["phenomenon_hits"],
            "memory_v2_match_reason": "Article antérieur proposé comme candidat, sous réserve du ranker et du BGE V146.",
        })
        scored.append(x)

    dedup: Dict[str, Dict[str, Any]] = {}
    for article in scored:
        key = _paper_key(article)
        prev = dedup.get(key)
        if prev is None or float(article.get("memory_v2_similarity") or 0) > float(prev.get("memory_v2_similarity") or 0):
            dedup[key] = article
    out = sorted(dedup.values(), key=lambda a: (
        float(a.get("memory_v2_similarity") or 0), int(a.get("citation_count") or 0)
    ), reverse=True)[:max(1, top_k)]
    return {
        "enabled": True, "version": "memory_v2_match_v146_conditional_gate",
        "index_articles_count": int(index.get("articles_count") or len(articles)),
        "scanned_files": int(index.get("scanned_files") or 0), "top_k": top_k,
        "min_score": min_score, "prequalified_count": len(out),
        "candidates_count": len(out), "rejected_no_core": rejected_no_core,
        "rejected_below_score": rejected_below, "articles": out,
    }
