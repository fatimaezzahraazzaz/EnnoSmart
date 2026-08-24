# -*- coding: utf-8 -*-
from __future__ import annotations

"""Corpus consultant persistant au niveau projet pour EnnoScholar Guided Research.

V169.2 applique une mémoire opérationnelle par génération active :
- l'historique PostgreSQL est conservé uniquement pour audit/traçabilité ;
- le dernier DiagnosticRun définit les verrous actifs ;
- le dernier ScholarRun canonique définit la sélection scientifique de base ;
- les articles gardés dans des conversations guidées créées après ce ScholarRun
  enrichissent la même génération et restent disponibles dans les conversations
  suivantes ;
- dès qu'une nouvelle génération canonique EnnoScholar existe, les anciens runs ne
  sont plus relus par les agents ;
- les articles EnnoAmelioration restent exclus ;
- le corpus peut être filtré par verrou et les doublons sont fusionnés par DOI,
  sinon par titre normalisé.

Aucune migration SQL n'est nécessaire : PostgreSQL contient déjà Article,
ScholarRun.project_id, Article.verrou_id et Article.consultant_status.
"""

import re
import unicodedata
from collections import defaultdict
from typing import Any, Iterable, Mapping

from sqlalchemy.orm import Session

from db.models import Article, DiagnosticRun, Project, ScholarRun, Verrou

V169_MARKER = "v169_2_active_generation_consultant_corpus"
_EXCLUDED_RUN_STATUSES = {"improvement_corpus"}


def _clean(value: Any, limit: int = 4000) -> str:
    text = re.sub(r"\s+", " ", str(value or "").replace("\x00", " ")).strip()
    return text[:limit] if limit else text


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", _clean(value).casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_doi(value: Any) -> str:
    doi = _clean(value, 800).casefold()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi)
    return doi.rstrip(".,; ")


def _as_str_set(values: Iterable[Any] | None) -> set[str]:
    return {_clean(value, 160) for value in (values or []) if _clean(value, 160)}


_PROJECT_CARD_CITATION_KEYS = {
    "citation_id",
    "citation_label",
    "citation_token",
    "selection_citation_id",
}


def _relabel_project_corpus_card(
    raw_card: Mapping[str, Any],
    citation_label: str,
) -> dict[str, Any]:
    """Attribue une citation unique à une carte issue d'un corpus multi-runs.

    Chaque ScholarRun construit historiquement ses cartes à partir de ``A1``.
    Lorsqu'un corpus projet agrège plusieurs runs, conserver ces labels locaux
    fait disparaître les cartes homonymes dans la Phase 5. La réécriture couvre
    également les preuves visuelles imbriquées afin que leur citation continue
    de pointer vers la bonne publication.
    """

    original_label = _clean(
        raw_card.get("citation_label")
        or raw_card.get("citation_id")
        or raw_card.get("citation_token"),
        80,
    )

    def rewrite(value: Any) -> Any:
        if isinstance(value, Mapping):
            rewritten: dict[str, Any] = {}
            for key, child in value.items():
                if key in _PROJECT_CARD_CITATION_KEYS:
                    rewritten[key] = citation_label
                else:
                    rewritten[key] = rewrite(child)
            return rewritten
        if isinstance(value, list):
            return [rewrite(child) for child in value]
        if isinstance(value, tuple):
            return [rewrite(child) for child in value]
        return value

    card = rewrite(dict(raw_card))
    card["citation_id"] = citation_label
    card["citation_label"] = citation_label
    if original_label:
        card["project_corpus_original_citation_label"] = original_label
    card["project_corpus_citation_relabelled"] = True
    return card


def article_scope_ids(article: Article) -> set[str]:
    """Retourne tous les verrous connus pour un article, quelle que soit sa provenance."""
    ids: set[str] = set()
    if getattr(article, "verrou_id", None) is not None:
        ids.add(str(article.verrou_id))

    src = article.source_json if isinstance(article.source_json, Mapping) else {}
    for key in (
        "covered_verrou_ids",
        "target_verrous",
        "verrou_ids",
        "active_verrou_ids",
    ):
        value = src.get(key)
        if isinstance(value, (list, tuple, set)):
            ids.update(_as_str_set(value))
        elif value is not None and _clean(value, 160):
            ids.add(_clean(value, 160))

    # Certaines versions rangent la provenance dans un sous-objet.
    for nested_key in ("guided_research", "provenance", "selection"):
        nested = src.get(nested_key)
        if not isinstance(nested, Mapping):
            continue
        for key in ("covered_verrou_ids", "target_verrous", "verrou_ids"):
            value = nested.get(key)
            if isinstance(value, (list, tuple, set)):
                ids.update(_as_str_set(value))
    return ids


def article_is_project_global(article: Article) -> bool:
    """Indique qu'un article guidé s'applique au projet, sans verrou imposé.

    Les premières versions du chat ne stockaient pas le marqueur explicite.  On
    reconnaît donc aussi leurs articles guidés sans aucun identifiant de verrou,
    afin que les sources déjà acceptées restent visibles après la correction.
    """

    src = article.source_json if isinstance(article.source_json, Mapping) else {}
    explicit_scope = _clean(src.get("project_corpus_scope"), 40).casefold()
    if explicit_scope in {"project", "global", "project_global"}:
        return True
    if bool(src.get("project_corpus_global")):
        return True
    if article_scope_ids(article):
        return False
    return bool(
        src.get("guided_research_source")
        or _clean(src.get("origin"), 80).casefold()
        in {"guided_research_conversation", "manual_project_upload"}
    )


def article_identity(article: Article) -> str:
    doi = _normalize_doi(getattr(article, "doi", None))
    if doi:
        return f"doi:{doi}"
    title = _norm(getattr(article, "title", None))
    if title:
        return f"title:{title}"
    return f"article:{int(article.id)}"


def _source_scope_ids(source: Mapping[str, Any]) -> set[str]:
    ids: set[str] = set()
    for key in (
        "target_verrous",
        "covered_verrou_ids",
        "verrou_ids",
        "active_verrou_ids",
    ):
        value = source.get(key)
        if isinstance(value, (list, tuple, set)):
            ids.update(_as_str_set(value))
        elif value is not None and _clean(value, 160):
            ids.add(_clean(value, 160))
    for key in ("verrou_id", "target_verrou_id"):
        if source.get(key) not in (None, ""):
            ids.add(_clean(source.get(key), 160))
    return ids


def _source_identity(source: Mapping[str, Any]) -> str:
    prep = source.get("fulltext_preparation")
    if isinstance(prep, Mapping) and prep.get("article_id"):
        return f"article:{prep.get('article_id')}"
    doi = _normalize_doi(source.get("doi"))
    if doi:
        return f"doi:{doi}"
    title = _norm(source.get("title"))
    if title:
        return f"title:{title}"
    candidate = _clean(source.get("candidate_id"), 300)
    return f"candidate:{candidate}" if candidate else ""


def _fulltext_quality(article: Article) -> int:
    src = article.source_json if isinstance(article.source_json, Mapping) else {}
    evidence = src.get("evidence_preflight")
    evidence = evidence if isinstance(evidence, Mapping) else {}
    prep = src.get("fulltext_preparation")
    prep = prep if isinstance(prep, Mapping) else {}
    score = 0
    if evidence.get("fulltext_ready"):
        score += 4
    if evidence.get("evidence_usable"):
        score += 3
    if prep.get("usable_as_scientific_evidence"):
        score += 3
    if src.get("fulltext_verified"):
        score += 2
    return score


def _article_preference(article: Article) -> tuple[Any, ...]:
    try:
        relevance = float(article.score or 0.0)
    except (TypeError, ValueError):
        relevance = 0.0
    created = getattr(article, "created_at", None)
    created_ts = created.timestamp() if created is not None else 0.0
    return (_fulltext_quality(article), relevance, created_ts, int(article.id))


def get_active_generation_scope(
    db: Session,
    project: Project,
) -> dict[str, Any]:
    """Retourne la génération opérationnelle courante du projet.

    Historique != mémoire agent : les anciens DiagnosticRun/ScholarRun restent en
    base, mais ne sont pas inclus dans ce scope.
    """
    latest_diagnostic = (
        db.query(DiagnosticRun)
        .filter(DiagnosticRun.project_id == int(project.id))
        .order_by(DiagnosticRun.created_at.desc(), DiagnosticRun.id.desc())
        .first()
    )

    active_verrou_ids: set[str] = set()
    all_current_verrou_ids: set[str] = set()
    if latest_diagnostic is not None:
        verrous = (
            db.query(Verrou)
            .filter(Verrou.diagnostic_run_id == int(latest_diagnostic.id))
            .order_by(Verrou.created_at.asc(), Verrou.id.asc())
            .all()
        )
        all_current_verrou_ids = {str(row.id) for row in verrous}
        active_verrou_ids = {
            str(row.id)
            for row in verrous
            if _clean(getattr(row, "consultant_status", ""), 40).casefold()
            == "garde"
        }

    # Même définition que scholar_selection_scope : le run canonique historique
    # de la page Articles exclut les corpus guidés et EnnoAmel.
    canonical_run = (
        db.query(ScholarRun)
        .filter(ScholarRun.project_id == int(project.id))
        .filter(
            ScholarRun.status.notin_([
                "improvement_corpus",
                "guided_conversation_corpus",
                "guided_research_standalone",
            ])
        )
        .order_by(ScholarRun.created_at.desc(), ScholarRun.id.desc())
        .first()
    )

    return {
        "project_id": int(project.id),
        "latest_diagnostic_run_id": (
            int(latest_diagnostic.id) if latest_diagnostic is not None else None
        ),
        "all_current_verrou_ids": sorted(all_current_verrou_ids),
        "active_verrou_ids": sorted(active_verrou_ids),
        "canonical_scholar_run_id": (
            int(canonical_run.id) if canonical_run is not None else None
        ),
        "canonical_scholar_created_at": (
            canonical_run.created_at if canonical_run is not None else None
        ),
        "policy": "latest_diagnostic_plus_current_scholar_generation_only",
    }


def _effective_requested_verrous(
    generation: Mapping[str, Any],
    requested: Iterable[Any] | None,
) -> set[str]:
    requested_ids = _as_str_set(requested)
    current_ids = _as_str_set(generation.get("active_verrou_ids") or [])

    # S'il existe un DiagnosticRun, aucun verrou historique ne peut être réactivé
    # par une vieille conversation ou un ancien source_json.
    if generation.get("latest_diagnostic_run_id") is not None:
        if requested_ids:
            return current_ids & requested_ids
        return current_ids

    # Workflow EnnoScholar autonome sans diagnostic : le verrou explicite de la
    # conversation reste l'autorité.
    return requested_ids


def get_project_kept_articles(
    db: Session,
    project: Project,
    *,
    active_verrou_ids: Iterable[Any] | None = None,
) -> list[Article]:
    """Corpus actif partagé entre conversations, jamais entre générations.

    Base = articles gardés du ScholarRun canonique courant.
    Enrichissement = articles gardés dans des runs guidés créés après cette base.
    Les anciens ScholarRun canoniques sont volontairement oubliés par les agents.
    """
    generation = get_active_generation_scope(db, project)
    effective_verrous = _effective_requested_verrous(
        generation, active_verrou_ids
    )

    # Un diagnostic existe mais aucun verrou n'est actuellement gardé : corpus vide.
    if (
        generation.get("latest_diagnostic_run_id") is not None
        and not effective_verrous
    ):
        return []

    canonical_id = generation.get("canonical_scholar_run_id")
    canonical_created_at = generation.get("canonical_scholar_created_at")

    rows: list[Article] = []
    if canonical_id is not None:
        rows.extend(
            db.query(Article)
            .filter(Article.scholar_run_id == int(canonical_id))
            .filter(Article.consultant_status == "garde")
            .all()
        )

        # Les recherches/ajouts guidés enrichissent le corpus courant uniquement
        # s'ils ont été créés après la génération canonique active.
        guided_query = (
            db.query(Article)
            .join(ScholarRun, Article.scholar_run_id == ScholarRun.id)
            .filter(ScholarRun.project_id == int(project.id))
            .filter(
                ScholarRun.status.in_([
                    "guided_conversation_corpus",
                    "guided_research_standalone",
                ])
            )
            .filter(Article.consultant_status == "garde")
        )
        if canonical_created_at is not None:
            guided_query = guided_query.filter(
                ScholarRun.created_at >= canonical_created_at
            )
        rows.extend(guided_query.all())
    else:
        # En mode autonome, le projet n'a pas de ScholarRun canonique servant de
        # frontière de génération. Son corpus est donc la mémoire dédupliquée de
        # toutes les conversations EnnoScholar, pas seulement de la plus récente.
        rows.extend(
            db.query(Article)
            .join(ScholarRun, Article.scholar_run_id == ScholarRun.id)
            .filter(ScholarRun.project_id == int(project.id))
            .filter(
                ScholarRun.status.in_([
                    "guided_conversation_corpus",
                    "guided_research_standalone",
                ])
            )
            .filter(Article.consultant_status == "garde")
            .all()
        )

    # « Gardé » mémorise la décision du consultant, mais ne suffit pas pour
    # entrer dans le corpus de rédaction. Un article sans texte intégral reste
    # dans les sources acceptées de la conversation, où le PDF peut être importé,
    # et n'est ajouté ici qu'après extraction vérifiée.
    rows = [row for row in rows if _article_is_writing_usable(row)]

    if effective_verrous:
        rows = [
            row for row in rows
            if (
                article_scope_ids(row) & effective_verrous
                or article_is_project_global(row)
            )
        ]

    chosen: dict[str, Article] = {}
    for article in rows:
        key = article_identity(article)
        previous = chosen.get(key)
        if previous is None or _article_preference(article) > _article_preference(previous):
            chosen[key] = article

    return sorted(
        chosen.values(),
        key=lambda row: (
            -(float(row.score or 0.0) if str(row.score or "").replace(".", "", 1).isdigit() else 0.0),
            -(int(row.year or 0)),
            int(row.id),
        ),
    )


def _article_is_writing_usable(article: Article) -> bool:
    src = article.source_json if isinstance(article.source_json, Mapping) else {}
    evidence = src.get("evidence_preflight")
    evidence = evidence if isinstance(evidence, Mapping) else {}
    prep = src.get("fulltext_preparation")
    prep = prep if isinstance(prep, Mapping) else {}
    return bool(
        evidence.get("evidence_usable")
        or evidence.get("fulltext_ready")
        or prep.get("usable_as_scientific_evidence")
        or src.get("fulltext_verified")
    )


def get_conversation_corpus_run(
    db: Session,
    project: Project,
    *,
    session_id: str,
    corpus_scope_id: str | None = None,
) -> ScholarRun | None:
    """Résout le ScholarRun privé d'une conversation autonome.

    La résolution repose sur les marqueurs de provenance persistés dans le run,
    jamais sur le « dernier run du projet ». Une conversation ne peut donc pas
    récupérer par accident le corpus d'une conversation voisine.
    """
    wanted_session_id = _clean(session_id, 160)
    wanted_scope_id = _clean(corpus_scope_id or session_id, 160)
    rows = (
        db.query(ScholarRun)
        .filter(ScholarRun.project_id == int(project.id))
        .filter(
            ScholarRun.status.in_([
                "guided_conversation_corpus",
                "guided_research_standalone",
            ])
        )
        .order_by(ScholarRun.created_at.desc(), ScholarRun.id.desc())
        .all()
    )
    for run in rows:
        raw = run.raw_result_json if isinstance(run.raw_result_json, Mapping) else {}
        known_session_ids = {
            _clean(value, 160)
            for value in (raw.get("guided_session_ids") or [])
            if _clean(value, 160)
        }
        if (
            _clean(raw.get("corpus_scope_id"), 160) == wanted_scope_id
            or _clean(raw.get("guided_session_id"), 160) == wanted_session_id
            or wanted_session_id in known_session_ids
        ):
            return run
    return None


def get_conversation_kept_articles(
    db: Session,
    project: Project,
    *,
    session_id: str,
    corpus_scope_id: str | None = None,
    active_verrou_ids: Iterable[Any] | None = None,
) -> tuple[ScholarRun | None, list[Article]]:
    """Retourne uniquement les preuves prêtes de la conversation autonome."""
    run = get_conversation_corpus_run(
        db,
        project,
        session_id=session_id,
        corpus_scope_id=corpus_scope_id,
    )
    if run is None:
        return None, []

    rows = (
        db.query(Article)
        .filter(Article.scholar_run_id == int(run.id))
        .filter(Article.consultant_status == "garde")
        .all()
    )
    # La décision « garder » et l'admission dans le corpus restent deux étapes
    # distinctes. Sans texte intégral vérifié, la source demeure dans sa carte
    # de conversation mais n'entre ni dans les cards ni dans la rédaction.
    rows = [row for row in rows if _article_is_writing_usable(row)]

    requested_verrous = _as_str_set(active_verrou_ids)
    if requested_verrous:
        rows = [
            row
            for row in rows
            if (
                article_scope_ids(row) & requested_verrous
                or article_is_project_global(row)
            )
        ]

    chosen: dict[str, Article] = {}
    for article in rows:
        key = article_identity(article)
        previous = chosen.get(key)
        if previous is None or _article_preference(article) > _article_preference(previous):
            chosen[key] = article
    articles = sorted(
        chosen.values(),
        key=lambda row: (
            -(float(row.score or 0.0) if str(row.score or "").replace(".", "", 1).isdigit() else 0.0),
            -(int(row.year or 0)),
            int(row.id),
        ),
    )
    return run, articles


def get_conversation_corpus_cards_payload(
    db: Session,
    project: Project,
    *,
    session_id: str,
    corpus_scope_id: str | None = None,
    active_verrou_ids: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Lit les Article Cards du seul corpus autonome de la conversation."""
    from services.article_card_builder import get_article_cards_payload

    scope_id = _clean(corpus_scope_id or session_id, 160)
    run, articles = get_conversation_kept_articles(
        db,
        project,
        session_id=session_id,
        corpus_scope_id=scope_id,
        active_verrou_ids=active_verrou_ids,
    )
    wanted_ids = {int(article.id) for article in articles}
    raw_cards: list[Mapping[str, Any]] = []
    if run is not None and wanted_ids:
        try:
            payload = get_article_cards_payload(
                project,
                scope_id=scope_id,
                db=db,
                scholar_run_id=int(run.id),
            )
        except Exception:
            payload = {}
        for key in ("cards", "article_cards", "items", "articles"):
            value = payload.get(key) if isinstance(payload, Mapping) else None
            if isinstance(value, list):
                raw_cards = [row for row in value if isinstance(row, Mapping)]
                break

    cards_by_article: dict[int, dict[str, Any]] = {}
    for raw in raw_cards:
        try:
            article_id = int(raw.get("article_id"))
        except (TypeError, ValueError):
            continue
        if article_id not in wanted_ids:
            continue
        cards_by_article[article_id] = dict(raw)
    cards = [
        _relabel_project_corpus_card(cards_by_article[key], f"A{index}")
        for index, key in enumerate(sorted(cards_by_article), start=1)
    ]
    card_ids = set(cards_by_article)
    return {
        "ok": True,
        "project_id": int(project.id),
        "session_id": session_id,
        "corpus_scope_id": scope_id,
        "effective_corpus_scope_id": scope_id,
        "project_corpus": False,
        "conversation_corpus": True,
        "corpus_isolated": True,
        "scholar_run_id": int(run.id) if run is not None else None,
        "scholar_run_ids": [int(run.id)] if run is not None else [],
        "cards": cards,
        "cards_count": len(cards),
        "selected_articles_count": len(articles),
        "writing_ready_cards_count": len(cards),
        "writing_ready_article_ids": sorted(card_ids),
        "excluded_from_writing_count": max(0, len(articles) - len(cards)),
        "excluded_article_ids": sorted(wanted_ids - card_ids),
        "payload_path": (
            f"db://projects/{int(project.id)}/guided-conversations/"
            f"{session_id}/cards"
        ),
        "version": V169_MARKER,
    }


def serialize_project_corpus_source(article: Article) -> dict[str, Any]:
    src = article.source_json if isinstance(article.source_json, Mapping) else {}
    candidate_id = _clean(src.get("guided_candidate_id"), 300) or f"PROJECT-A{int(article.id)}"
    scope_ids = sorted(article_scope_ids(article))
    usable = _article_is_writing_usable(article)
    corpus_status = _clean(src.get("project_corpus_status"), 80) or (
        "fulltext_ready" if usable else "needs_manual_upload"
    )
    return {
        "candidate_id": candidate_id,
        "candidate_kind": "scientific_article",
        "title": _clean(article.title, 4000),
        "authors": list(src.get("authors") or [])[:20],
        "year": article.year,
        "doi": article.doi,
        "url": article.url,
        "provider": article.source or src.get("provider") or "project_corpus",
        "source": article.source or src.get("source") or "project_corpus",
        "full_scholar_tag": article.tag_article,
        "relevance_score": article.score,
        "consultant_decision": "accepted",
        "consultant_reason": "Article gardé dans le corpus persistant du projet.",
        "target_verrous": scope_ids,
        "project_corpus": True,
        "project_corpus_global": article_is_project_global(article),
        "project_corpus_status": corpus_status,
        "project_id": int(article.scholar_run.project_id) if getattr(article, "scholar_run", None) else None,
        "origin_scholar_run_id": int(article.scholar_run_id),
        "fulltext_preparation": {
            "article_id": int(article.id),
            "usable_as_scientific_evidence": usable,
            "status": "project_corpus_ready" if usable else "project_corpus_metadata_only",
        },
    }


def merge_project_and_session_sources(
    project_sources: Iterable[Mapping[str, Any]],
    session_sources: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Fusionne le corpus durable et les candidats du tour courant.

    Une copie session ne peut pas annuler silencieusement un article déjà gardé au
    niveau projet. Le retrait projet passe par l'action explicite de suppression,
    qui modifie ``Article.consultant_status`` en base.
    """
    output: list[dict[str, Any]] = []
    index_by_identity: dict[str, int] = {}

    for raw in project_sources:
        if not isinstance(raw, Mapping):
            continue
        row = dict(raw)
        key = _source_identity(row)
        index_by_identity[key or f"project:{len(output)}"] = len(output)
        output.append(row)

    for raw in session_sources:
        if not isinstance(raw, Mapping):
            continue
        row = dict(raw)
        key = _source_identity(row)
        if key and key in index_by_identity:
            idx = index_by_identity[key]
            merged = dict(output[idx])
            # On conserve les métadonnées fraîches de la conversation, mais la
            # décision projet garde la priorité tant que l'Article est encore garde.
            for field, value in row.items():
                if value not in (None, "", [], {}):
                    merged[field] = value
            merged["consultant_decision"] = "accepted"
            merged["project_corpus"] = True
            output[idx] = merged
            continue
        if key:
            index_by_identity[key] = len(output)
        output.append(row)
    return output


def get_effective_guided_sources(
    db: Session,
    project: Project,
    *,
    session_sources: Iterable[Mapping[str, Any]] | None = None,
    active_verrou_ids: Iterable[Any] | None = None,
) -> list[dict[str, Any]]:
    generation = get_active_generation_scope(db, project)
    effective_verrous = _effective_requested_verrous(
        generation, active_verrou_ids
    )
    project_rows = [
        serialize_project_corpus_source(article)
        for article in get_project_kept_articles(
            db, project, active_verrou_ids=active_verrou_ids
        )
    ]

    raw_session = [
        dict(row) for row in (session_sources or []) if isinstance(row, Mapping)
    ]
    if generation.get("latest_diagnostic_run_id") is not None:
        # Une session d'une ancienne génération ne peut pas réinjecter ses sources
        # simplement parce qu'elle existe encore dans l'historique conversationnel.
        if active_verrou_ids is not None and not effective_verrous:
            raw_session = []
        elif effective_verrous:
            raw_session = [
                row for row in raw_session
                if _source_scope_ids(row) & effective_verrous
            ]
        else:
            raw_session = []

    return merge_project_and_session_sources(project_rows, raw_session)


def get_project_corpus_cards_payload(
    db: Session,
    project: Project,
    *,
    active_verrou_ids: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Agrège les Article Cards déjà construites dans tous les runs du corpus projet."""
    from services.article_card_builder import get_article_cards_payload

    generation = get_active_generation_scope(db, project)
    articles = get_project_kept_articles(
        db, project, active_verrou_ids=active_verrou_ids
    )
    wanted_ids = {int(article.id) for article in articles}
    if not wanted_ids:
        return {
            "ok": True,
            "project_id": int(project.id),
            "project_corpus": True,
            "cards": [],
            "cards_count": 0,
            "selected_articles_count": 0,
            "writing_ready_cards_count": 0,
            "writing_ready_article_ids": [],
            "excluded_from_writing_count": 0,
            "excluded_article_ids": [],
            "payload_path": f"db://projects/{int(project.id)}/project_persistent_corpus/cards",
            "version": V169_MARKER,
            "active_generation": generation,
        }

    groups: dict[tuple[int, str | None], set[int]] = defaultdict(set)
    for article in articles:
        src = article.source_json if isinstance(article.source_json, Mapping) else {}
        scope = _clean(src.get("corpus_scope_id"), 160) or None
        groups[(int(article.scholar_run_id), scope)].add(int(article.id))

    cards_by_article: dict[int, dict[str, Any]] = {}
    for (run_id, scope_id), article_ids in groups.items():
        try:
            payload = get_article_cards_payload(
                project,
                scope_id=scope_id,
                db=db,
                scholar_run_id=run_id,
            )
        except Exception:
            payload = {}
        cards = []
        for key in ("cards", "article_cards", "items", "articles"):
            value = payload.get(key) if isinstance(payload, Mapping) else None
            if isinstance(value, list):
                cards = [row for row in value if isinstance(row, Mapping)]
                break
        for raw in cards:
            try:
                article_id = int(raw.get("article_id"))
            except (TypeError, ValueError):
                continue
            if article_id not in article_ids or article_id not in wanted_ids:
                continue
            previous = cards_by_article.get(article_id)
            candidate = dict(raw)
            if previous is None:
                cards_by_article[article_id] = candidate
            else:
                # Préfère la carte ayant le plus de preuves textuelles.
                previous_size = len(str(previous.get("evidence") or ""))
                candidate_size = len(str(candidate.get("evidence") or ""))
                if candidate_size > previous_size:
                    cards_by_article[article_id] = candidate

    # Les labels A1..An sont locaux à chaque ScholarRun. L'agrégat projet doit
    # posséder son propre espace de citations, sinon Phase 5 élimine silencieusement
    # les cartes A1/A2/... provenant des recherches supplémentaires.
    cards = [
        _relabel_project_corpus_card(cards_by_article[key], f"A{index}")
        for index, key in enumerate(sorted(cards_by_article), start=1)
    ]
    return {
        "ok": True,
        "project_id": int(project.id),
        "project_corpus": True,
        "cards": cards,
        "cards_count": len(cards),
        "selected_articles_count": len(articles),
        "writing_ready_cards_count": len(cards),
        "writing_ready_article_ids": [
            int(row.get("article_id")) for row in cards if row.get("article_id") is not None
        ],
        "excluded_from_writing_count": max(0, len(articles) - len(cards)),
        "excluded_article_ids": sorted(wanted_ids - set(cards_by_article)),
        "payload_path": f"db://projects/{int(project.id)}/project_persistent_corpus/cards",
        "scholar_run_ids": sorted({int(article.scholar_run_id) for article in articles}),
        "version": V169_MARKER,
        "active_generation": generation,
    }


def reject_project_corpus_article(
    db: Session,
    project: Project,
    *,
    article_id: int,
) -> list[int]:
    """Retire explicitement une identité scientifique du corpus projet.

    Tous les doublons de la même publication sont retirés pour éviter qu'une copie
    historique réapparaisse dans la conversation suivante.
    """
    target = (
        db.query(Article)
        .join(ScholarRun, Article.scholar_run_id == ScholarRun.id)
        .filter(Article.id == int(article_id))
        .filter(ScholarRun.project_id == int(project.id))
        .filter(ScholarRun.status.notin_(sorted(_EXCLUDED_RUN_STATUSES)))
        .first()
    )
    if target is None:
        raise LookupError("Article absent du corpus EnnoScholar de ce projet.")

    identity = article_identity(target)
    rows = (
        db.query(Article)
        .join(ScholarRun, Article.scholar_run_id == ScholarRun.id)
        .filter(ScholarRun.project_id == int(project.id))
        .filter(ScholarRun.status.notin_(sorted(_EXCLUDED_RUN_STATUSES)))
        .filter(Article.consultant_status == "garde")
        .all()
    )
    changed: list[int] = []
    for row in rows:
        if article_identity(row) != identity:
            continue
        row.consultant_status = "rejete"
        db.add(row)
        changed.append(int(row.id))
    db.commit()
    return changed
