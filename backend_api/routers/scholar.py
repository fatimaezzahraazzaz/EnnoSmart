# -*- coding: utf-8 -*-
from __future__ import annotations

# ENNOSCHOLAR_V169_1_PROJECT_PERSISTENT_CORPUS

import importlib.util
import os
import re
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session, defer
from sqlalchemy.orm.attributes import flag_modified

from core.deps import get_current_user, get_db, require_agent_enabled
from db.models import Article, DiagnosticRun, ScholarRun, User, Verrou
from schemas.scholar import ArticleDecisionRequest, ArticleRead, ScholarRead
from services.project_service import get_project_for_user
from services.consultant_verrou_service import (
    get_latest_diagnostic_verrous as get_current_and_manual_verrous,
)
from services import scholar_service as scholar_service_module
from services.scholar_service import (
    build_scholar_payload_from_selected_verrous,
    create_scholar_run_from_files,
    get_all_current_verrous,
    get_selected_verrous_for_scholar,
    read_scholar_bundle,
    run_ennoscholar,
    run_ennoscholar_from_selected_verrous,
    sync_articles_from_scholar,
)

router = APIRouter(tags=["ennoscholar"], dependencies=[Depends(require_agent_enabled("scholar"))])


# ============================================================
# Helpers backend simples — pas de logique IA ici
# ============================================================

def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _ensure_ennosmart_root_on_path() -> Path:
    """
    Permet au backend lancé depuis C:\\EnnoSmart\\backend_api
    d'importer les modules situés dans C:\\EnnoSmart\\agents.

    On cherche automatiquement le dossier racine qui contient :
    agents/EnnoScholar/abstract_translator.py
    """
    candidates: list[Path] = []

    env_root = (
        os.getenv("ENNOSMART_ROOT")
        or os.getenv("ENNOSMART_PROJECT_ROOT")
        or os.getenv("PROJECT_ROOT")
    )

    if env_root:
        candidates.append(Path(env_root))

    current_file = Path(__file__).resolve()

    for parent in current_file.parents:
        candidates.append(parent)

    cwd = Path.cwd().resolve()
    candidates.append(cwd)

    for parent in cwd.parents:
        candidates.append(parent)

    candidates.append(Path("C:/EnnoSmart"))

    seen: set[str] = set()

    for candidate in candidates:
        try:
            root = candidate.resolve()
        except Exception:
            continue

        root_key = str(root).lower()
        if root_key in seen:
            continue
        seen.add(root_key)

        translator_file = root / "agents" / "EnnoScholar" / "abstract_translator.py"

        if translator_file.exists():
            root_str = str(root)
            if root_str not in sys.path:
                sys.path.insert(0, root_str)
            return root

    raise RuntimeError(
        "Impossible de trouver le dossier EnnoSmart contenant "
        "agents/EnnoScholar/abstract_translator.py. "
        "Vérifie que le fichier existe dans C:\\EnnoSmart\\agents\\EnnoScholar\\abstract_translator.py"
    )


def _get_translate_abstract_to_french():
    """
    Charge la fonction de traduction depuis l'agent EnnoScholar.

    Priorité :
    1. import normal : agents.EnnoScholar.abstract_translator
    2. fallback : import direct par chemin de fichier
    """
    root = _ensure_ennosmart_root_on_path()

    try:
        from agents.EnnoScholar.abstract_translator import translate_abstract_to_french

        return translate_abstract_to_french
    except ModuleNotFoundError:
        translator_file = root / "agents" / "EnnoScholar" / "abstract_translator.py"

        if not translator_file.exists():
            raise RuntimeError(
                f"Fichier de traduction introuvable : {translator_file}"
            )

        spec = importlib.util.spec_from_file_location(
            "ennoscholar_abstract_translator",
            str(translator_file),
        )

        if spec is None or spec.loader is None:
            raise RuntimeError(
                f"Impossible de charger le module de traduction : {translator_file}"
            )

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        translate_fn = getattr(module, "translate_abstract_to_french", None)

        if translate_fn is None:
            raise RuntimeError(
                "La fonction translate_abstract_to_french est introuvable dans "
                f"{translator_file}"
            )

        return translate_fn


def _normalize_abstract_text(value: Any) -> str:
    """
    Nettoie un abstract sans le résumer.
    """
    if not isinstance(value, str):
        return ""

    text = value.replace("\x00", " ").strip()

    if not text:
        return ""

    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    return text


def _collect_abstract_candidates_from_json(
    value: Any,
    depth: int = 0,
    max_depth: int = 4,
) -> list[str]:
    """
    Cherche récursivement des champs susceptibles de contenir le vrai abstract.
    Cela évite de prendre un ancien résumé tronqué.
    """
    if depth > max_depth:
        return []

    candidates: list[str] = []

    abstract_keys = {
        "abstract",
        "abstract_original",
        "paper_abstract",
        "openalex_abstract",
        "semantic_scholar_abstract",
        "semanticscholar_abstract",
        "arxiv_abstract",
        "description",
    }

    fallback_keys = {
        "summary",
        "resume",
        "tldr",
    }

    if isinstance(value, dict):
        for key, item in value.items():
            key_norm = str(key or "").strip().lower()

            if key_norm in abstract_keys:
                text = _normalize_abstract_text(item)
                if text:
                    candidates.append(text)

            elif key_norm in fallback_keys:
                if isinstance(item, dict):
                    text = _normalize_abstract_text(item.get("text"))
                    if text:
                        candidates.append(text)
                else:
                    text = _normalize_abstract_text(item)
                    if text:
                        candidates.append(text)

            if isinstance(item, (dict, list)):
                candidates.extend(
                    _collect_abstract_candidates_from_json(
                        item,
                        depth=depth + 1,
                        max_depth=max_depth,
                    )
                )

    elif isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                candidates.extend(
                    _collect_abstract_candidates_from_json(
                        item,
                        depth=depth + 1,
                        max_depth=max_depth,
                    )
                )

    return candidates


def _extract_article_abstract(article: Article) -> str:
    """
    Récupère le meilleur abstract original disponible.

    Important :
    - priorité au vrai abstract brut venant des sources scientifiques ;
    - article_summary.abstract_original peut contenir une ancienne version tronquée ;
    - on prend le texte le plus long pour éviter de retraduire un ancien résumé incomplet.
    """
    sj: dict[str, Any] = article.source_json if isinstance(article.source_json, dict) else {}
    summary = sj.get("article_summary") if isinstance(sj.get("article_summary"), dict) else {}

    direct_candidates = [
        sj.get("abstract"),
        sj.get("abstract_original"),
        sj.get("paper_abstract"),
        sj.get("openalex_abstract"),
        sj.get("semantic_scholar_abstract"),
        sj.get("semanticscholar_abstract"),
        sj.get("arxiv_abstract"),
        sj.get("description"),
    ]

    recursive_candidates = _collect_abstract_candidates_from_json(sj)

    fallback_candidates = [
        sj.get("summary"),
        sj.get("resume"),
        summary.get("abstract_original"),
        summary.get("resume_court"),
    ]

    cleaned: list[str] = []

    for value in direct_candidates + recursive_candidates + fallback_candidates:
        text = _normalize_abstract_text(value)
        if text:
            cleaned.append(text)

    # Déduplication simple
    unique: list[str] = []
    seen: set[str] = set()

    for text in cleaned:
        key = text.lower()
        if key not in seen:
            unique.append(text)
            seen.add(key)

    if not unique:
        return ""

    return max(unique, key=len)


def _get_existing_abstract_fr(article: Article) -> str:
    """
    Récupère une traduction FR déjà sauvegardée, si elle existe.
    """
    sj: dict[str, Any] = article.source_json if isinstance(article.source_json, dict) else {}
    summary = sj.get("article_summary") if isinstance(sj.get("article_summary"), dict) else {}

    candidates = [
        summary.get("abstract_fr"),
        summary.get("resume_fr"),
        sj.get("abstract_fr"),
        sj.get("resume_fr"),
    ]

    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()

    return ""


def _word_count(text: str) -> int:
    return len(re.findall(r"\w+", str(text or "")))


def _translation_looks_truncated(original: str, translated: str) -> tuple[bool, str]:
    """
    Garde-fou backend : refuse de sauvegarder une traduction clairement tronquée.

    Le backend ne corrige pas la traduction, il vérifie seulement que le résultat
    n'est pas manifestement incomplet.
    """
    original_clean = _normalize_abstract_text(original)
    translated_clean = _normalize_abstract_text(translated)

    if not translated_clean:
        return True, "empty_translation"

    original_chars = len(original_clean)
    translated_chars = len(translated_clean)

    original_words = _word_count(original_clean)
    translated_words = _word_count(translated_clean)

    if original_words >= 80 and translated_words < original_words * 0.50:
        return True, (
            f"too_few_words original_words={original_words} "
            f"translated_words={translated_words}"
        )

    if original_chars >= 600 and translated_chars < original_chars * 0.45:
        return True, (
            f"too_few_chars original_chars={original_chars} "
            f"translated_chars={translated_chars}"
        )

    # Cas fréquent observé : la traduction s'arrête après les premières phrases.
    if original_words >= 120 and translated_words < 90:
        return True, (
            f"suspicious_short_translation original_words={original_words} "
            f"translated_words={translated_words}"
        )

    return False, "ok"


def _build_translation_context(project: Any, article: Article) -> dict[str, Any]:
    """
    Contexte générique envoyé à l'agent EnnoScholar.
    Aucun domaine n'est codé en dur.
    """
    sj: dict[str, Any] = article.source_json if isinstance(article.source_json, dict) else {}

    validation = (
        sj.get("verrou_scientific_validation")
        if isinstance(sj.get("verrou_scientific_validation"), dict)
        else {}
    )

    scientific_intent = (
        sj.get("scientific_intent")
        if isinstance(sj.get("scientific_intent"), dict)
        else {}
    )

    verrou_title = (
        validation.get("verrou_title")
        or scientific_intent.get("verrou_title")
        or scientific_intent.get("title")
        or sj.get("verrou_title")
        or sj.get("enriched_title")
        or sj.get("scientific_title")
    )

    source_language = (
        sj.get("language")
        or sj.get("lang")
        or sj.get("source_language")
        or sj.get("detected_language")
    )

    return {
        "organisme": project.organisme,
        "project_name": project.project_name,
        "year": project.year,
        "domain_label": project.domain_label,
        "article_title": article.title,
        "source": article.source,
        "tag_article": article.tag_article,
        "verrou_title": verrou_title,
        "scientific_intent": scientific_intent or validation,
        "source_language": source_language,
    }




# ============================================================
# V11 — Verrous EnnoScholar limités au dernier diagnostic
# ============================================================

def _latest_diagnostic_run_for_project(
    db: Session,
    project_id: int,
) -> DiagnosticRun | None:
    """Retourne le dernier DiagnosticRun officiel du projet."""
    return (
        db.query(DiagnosticRun)
        .filter(DiagnosticRun.project_id == int(project_id))
        .order_by(
            DiagnosticRun.created_at.desc(),
            DiagnosticRun.id.desc(),
        )
        .first()
    )


def _install_latest_diagnostic_verrou_policy(
    db: Session,
    project_id: int,
) -> dict[str, Any]:
    """
    Force le service EnnoScholar à lire uniquement les verrous du dernier
    DiagnosticRun, sans supprimer les anciens verrous et sans casser les
    références Article.verrou_id.

    Les fonctions de services.scholar_service utilisent les globals de leur
    module au moment de l'exécution. On remplace donc uniquement leurs deux
    sélecteurs DB par une version latest-only.
    """
    latest_run = _latest_diagnostic_run_for_project(db, project_id)

    def latest_all_verrous(
        service_db: Session,
        project: Any,
    ) -> list[Verrou]:
        return get_current_and_manual_verrous(
            service_db,
            int(project.id),
        )

    def latest_selected_verrous(
        service_db: Session,
        project: Any,
    ) -> list[Verrou]:
        return [
            verrou
            for verrou in get_current_and_manual_verrous(
                service_db,
                int(project.id),
            )
            if verrou.consultant_status == "garde"
        ]

    scholar_service_module.get_all_current_verrous = latest_all_verrous
    scholar_service_module.get_selected_verrous_for_scholar = latest_selected_verrous

    current_count = 0
    selected_count = 0
    if latest_run is not None:
        current_rows = get_current_and_manual_verrous(db, project_id)
        current_count = len(current_rows)
        selected_count = sum(
            1 for verrou in current_rows if verrou.consultant_status == "garde"
        )

    report = {
        "ok": latest_run is not None,
        "latest_diagnostic_run_id": (
            int(latest_run.id) if latest_run is not None else None
        ),
        "current_verrous": int(current_count),
        "selected_verrous": int(selected_count),
        "history_deleted": False,
        "policy": "latest_diagnostic_run_plus_manual_history",
    }
    print(
        "[EnnoScholar][V12_LATEST_ONLY_NO_DELETE] "
        f"project_id={project_id} "
        f"latest_run_id={report['latest_diagnostic_run_id']} "
        f"current={current_count} "
        f"selected={selected_count} "
        "history_deleted=false"
    )
    return report


def _latest_diagnostic_verrous(
    db: Session,
    project_id: int,
    *,
    selected_only: bool = False,
) -> list[Verrou]:
    """Lecture non cumulative utilisée par l'aperçu EnnoScholar."""
    rows = get_current_and_manual_verrous(db, project_id)
    if selected_only:
        rows = [row for row in rows if row.consultant_status == "garde"]
    return rows


# ============================================================
# V10 — Compteurs EnnoScholar non cumulatifs
# ============================================================

def _latest_scholar_run_for_project(db: Session, project_id: int) -> ScholarRun | None:
    """
    Retourne uniquement le dernier run EnnoScholar du projet.

    Pourquoi :
    - les anciens runs restent en base pour l'historique ;
    - mais l'interface consultant doit afficher le dernier état courant ;
    - sinon les articles s'additionnent à chaque relance.
    """
    return (
        db.query(ScholarRun)
        .options(defer(ScholarRun.raw_result_json))
        .filter(ScholarRun.project_id == project_id)
        # Les corpus guidés sont privés à leur conversation. Ils ne doivent
        # jamais devenir le « dernier run » de la page Articles historique.
        .filter(
            ScholarRun.status.notin_([
                "guided_conversation_corpus",
                "guided_research_standalone",
                "improvement_corpus",
            ])
        )
        .order_by(ScholarRun.created_at.desc())
        .first()
    )


def _state_of_art_preflight_run(
    db: Session,
    project: Project,
    guided_session_id: str | None,
) -> ScholarRun | None:
    """Résout le corpus que le writer va réellement lire.

    La page Articles historique reste fondée sur le dernier run canonique. Une
    rédaction lancée depuis une conversation doit toutefois contrôler le
    ScholarRun privé de cette conversation ; sinon ses articles nouvellement
    gardés sont absents du précontrôle et le backend annonce à tort un corpus
    vide. Si la conversation n'a créé aucun corpus supplémentaire, son handoff
    figé vers le workflow 1 est utilisé avant le fallback canonique.
    """

    canonical_run = _latest_scholar_run_for_project(db, int(project.id))
    session_id = str(guided_session_id or "").strip()
    if not session_id:
        return canonical_run

    from services.guided_research_service import _guided_corpus_run

    _, conversation_run, snapshot = _guided_corpus_run(
        db,
        project,
        session_id=session_id,
        create=False,
    )
    if conversation_run is not None:
        return conversation_run

    context = dict(snapshot.get("context") or {})
    handoff = (
        dict(context.get("handoff") or {})
        if isinstance(context.get("handoff"), dict)
        else {}
    )
    handoff_run_id = handoff.get("scholar_run_id")
    if handoff_run_id is not None:
        candidate = db.get(ScholarRun, int(handoff_run_id))
        if candidate is None or int(candidate.project_id) != int(project.id):
            raise RuntimeError(
                "Le ScholarRun figé par cette conversation est introuvable "
                "ou appartient à un autre projet."
            )
        return candidate

    return canonical_run


@router.get("/projects/{project_id}/scholar/latest")
def get_latest_scholar(
    project_id: int,
    compact: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)

    latest_run = _latest_scholar_run_for_project(db, project.id)

    bundle = read_scholar_bundle(project)

    return {
        "project": {
            "id": project.id,
            "organisme": project.organisme,
            "project_name": project.project_name,
            "year": project.year,
            "domain_label": project.domain_label,
            "status": project.status,
        },
        "latest_run": (
            {
                "id": latest_run.id,
                "project_id": latest_run.project_id,
                "status": latest_run.status,
                "report_path": latest_run.report_path,
                "created_at": latest_run.created_at,
                "completed_at": latest_run.completed_at,
            }
            if compact and latest_run
            else ScholarRead.model_validate(latest_run).model_dump()
            if latest_run
            else None
        ),
        "bundle": bundle,
    }


@router.get("/projects/{project_id}/scholar/selected-verrous")
def get_scholar_selected_verrous(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)

    selected = _latest_diagnostic_verrous(
        db,
        project.id,
        selected_only=True,
    )
    all_verrous = _latest_diagnostic_verrous(
        db,
        project.id,
        selected_only=False,
    )

    return {
        "ok": True,
        "selection_rule": "consultant_status == garde",
        "total_verrous": len(all_verrous),
        "selected_count": len(selected),
        "selected_verrous": [
            {
                "id": v.id,
                "title": v.title,
                "score": v.score,
                "tag_cir": v.tag_cir,
                "consultant_status": v.consultant_status,
                "justification": v.justification,
                "source_json": v.source_json,
            }
            for v in selected
        ],
    }


@router.get("/projects/{project_id}/scholar/payload-preview")
def get_scholar_payload_preview(
    project_id: int,
    max_verrous: int = Query(8, ge=1, le=20),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)
    _install_latest_diagnostic_verrou_policy(
        db=db,
        project_id=project.id,
    )
    payload = build_scholar_payload_from_selected_verrous(
        db,
        project,
        max_verrous=max_verrous,
    )

    return payload


@router.post("/projects/{project_id}/scholar/import-existing", response_model=ScholarRead)
def import_existing_scholar(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)
    run = create_scholar_run_from_files(db, project)
    return run


@router.post("/projects/{project_id}/scholar/run-from-selected-verrous", response_model=ScholarRead)
def run_scholar_from_selected_verrous(
    project_id: int,
    max_verrous: int = Query(8, ge=1, le=20),
    limit_per_query: int = Query(12, ge=1, le=50),
    offline_dry_run: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)

    try:
        _install_latest_diagnostic_verrou_policy(
            db=db,
            project_id=project.id,
        )
        run = run_ennoscholar_from_selected_verrous(
            db=db,
            project=project,
            max_verrous=max_verrous,
            limit_per_query=limit_per_query,
            offline_dry_run=offline_dry_run,
        )
        return run

    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"EnnoScholar impossible : {exc}",
        )


@router.post("/projects/{project_id}/scholar/run", response_model=ScholarRead)
def run_scholar(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)

    try:
        _install_latest_diagnostic_verrou_policy(
            db=db,
            project_id=project.id,
        )
        return run_ennoscholar(db, project)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"EnnoScholar impossible : {exc}",
        )


@router.post(
    "/projects/{project_id}/scholar/{run_id}/sync-articles",
    response_model=list[ArticleRead],
)
def sync_articles(
    project_id: int,
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Synchronise les articles du run demandé.

    Les anciens ScholarRun et leurs articles restent conservés en base pour
    la traçabilité. L'interface affiche uniquement le dernier run grâce à
    latest_only=True dans la route /projects/{project_id}/articles.
    """
    project = get_project_for_user(db, project_id, current_user)

    run = (
        db.query(ScholarRun)
        .filter(
            ScholarRun.id == run_id,
            ScholarRun.project_id == project.id,
        )
        .first()
    )

    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scholar run introuvable.",
        )

    return sync_articles_from_scholar(db, run)



# ============================================================
# ENNOSCHOLAR_ACCESS_UX_V165
# Etat d'accès/extraction stable pour le frontend.
# Aucun champ DB supplémentaire : tout est dérivé de source_json.
# ============================================================
def _compact_article_access_state(
    article: Article,
    source_json: dict[str, Any],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    evidence_status = str(
        evidence.get("evidence_status") or "NOT_CHECKED"
    ).strip().upper()
    reason_code = str(
        evidence.get("reason_code") or ""
    ).strip().upper()
    access_kind = str(
        evidence.get("access_kind") or ""
    ).strip().lower()

    manual_verified = bool(source_json.get("manual_upload_verified"))
    manual_filename = (
        str(source_json.get("uploaded_filename") or "").strip() or None
    )
    identity = (
        source_json.get("manual_upload_identity_verification")
        if isinstance(source_json.get("manual_upload_identity_verification"), dict)
        else {}
    )
    try:
        identity_score = (
            float(identity.get("score"))
            if identity.get("score") is not None
            else None
        )
    except Exception:
        identity_score = None

    access_probe = (
        source_json.get("access_probe_result")
        if isinstance(source_json.get("access_probe_result"), dict)
        else {}
    )
    browser_download_url = (
        str(
            evidence.get("browser_download_url")
            or access_probe.get("browser_download_url")
            or ""
        ).strip()
        or None
    )

    mcp_checked = isinstance(source_json.get("mcp_access_diagnostic"), dict)
    is_paywalled = bool(
        evidence_status != "FULLTEXT_READY"
        and (
            reason_code == "PAYWALL_BLOCKED"
            or access_kind == "paid"
        )
    )
    is_automation_blocked = bool(
        evidence_status != "FULLTEXT_READY"
        and (
            evidence_status == "BROWSER_DOWNLOAD_REQUIRED"
            or reason_code in {
                "PUBLIC_PDF_BROWSER_ONLY",
                "ANTIBOT_BLOCKED",
                "AUTOMATED_ACCESS_BLOCKED",
            }
            or access_kind in {"blocked", "public_browser_only"}
        )
    )

    access_status = "UNAVAILABLE"
    badge = "Accès indisponible"
    extraction_status = "MANUAL_UPLOAD_REQUIRED"
    resolution_source = str(
        evidence.get("access_resolution_source")
        or source_json.get("fulltext_resolution_source")
        or ""
    ).strip().upper() or None

    if evidence_status == "FULLTEXT_READY":
        if manual_verified:
            access_status = "READY_MANUAL"
            badge = "PDF manuel validé"
            resolution_source = "MANUAL_UPLOAD"
        else:
            access_status = "READY_AUTO"
            badge = "Texte intégral prêt"
            resolution_source = resolution_source or "AUTOMATIC"
        extraction_status = "VERIFIED_READY"

    elif is_paywalled:
        access_status = "PAYWALLED"
        badge = "Payant · aucune version légale trouvée"
        extraction_status = "MANUAL_UPLOAD_REQUIRED"

    elif is_automation_blocked:
        access_status = "AUTOMATION_BLOCKED"
        badge = "Téléchargement automatique bloqué"
        extraction_status = "MANUAL_UPLOAD_REQUIRED"

    elif evidence_status == "ACCESS_AVAILABLE":
        if reason_code == "MCP_VERIFIED_FULLTEXT_ACCESSIBLE" or access_kind == "legal_mcp_fulltext_url":
            access_status = "LEGAL_ALTERNATIVE"
            badge = "Version légale trouvée"
            resolution_source = "MCP"
        else:
            access_status = "EXTRACTIBLE"
            badge = "Accès vérifié"
            resolution_source = resolution_source or "DIRECT"
        extraction_status = "READY_TO_EXTRACT"

    elif evidence_status == "ACCESS_UNCONFIRMED":
        access_status = "UNCONFIRMED"
        badge = "Accès à confirmer"
        extraction_status = "NOT_READY"

    elif evidence_status in {
        "NOT_CHECKED",
        "ACCESS_CHECKING",
        "MCP_SEARCHING",
        "EXTRACTION_QUEUED",
        "EXTRACTION_RUNNING",
    }:
        access_status = "CHECKING"
        badge = "Vérification en cours"
        extraction_status = (
            "RUNNING"
            if evidence_status in {"EXTRACTION_QUEUED", "EXTRACTION_RUNNING"}
            else "NOT_STARTED"
        )

    elif evidence_status in {"ABSTRACT_READY", "METADATA_ONLY", "EXTRACTION_FAILED", "ACCESS_UNAVAILABLE"}:
        access_status = "UNAVAILABLE"
        badge = "PDF autorisé requis"
        extraction_status = "MANUAL_UPLOAD_REQUIRED"

    manual_upload_required = bool(
        evidence.get("needs_consultant_upload")
        or access_status in {"PAYWALLED", "AUTOMATION_BLOCKED", "UNAVAILABLE"}
    ) and evidence_status != "FULLTEXT_READY"

    selection_allowed = bool(
        evidence_status in {"ACCESS_AVAILABLE", "FULLTEXT_READY"}
    )
    access_final = bool(
        evidence_status in {"ACCESS_UNAVAILABLE", "BROWSER_DOWNLOAD_REQUIRED"}
        and mcp_checked
    )

    return {
        "access_status": access_status,
        "access_badge_label": badge,
        "extraction_status": extraction_status,
        "manual_upload_required": manual_upload_required,
        "browser_download_url": browser_download_url,
        "manual_upload_verified": manual_verified,
        "manual_upload_filename": manual_filename,
        "manual_upload_identity_score": identity_score,
        "selection_allowed": selection_allowed,
        "access_resolution_source": resolution_source,
        "mcp_checked": mcp_checked,
        "access_final": access_final,
    }


@router.get("/projects/{project_id}/articles", response_model=list[ArticleRead])
def list_articles(
    project_id: int,
    latest_only: bool = Query(True),
    compact: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Articles EnnoScholar affichés par le frontend.

    Correction importante :
    - avant : on retournait tous les articles de tous les ScholarRun du projet ;
      donc à chaque relance EnnoScholar le compteur augmentait.
    - maintenant : par défaut, on retourne seulement les articles du dernier run.

    Pour debug/historique seulement :
      /projects/{project_id}/articles?latest_only=false
    """
    project = get_project_for_user(db, project_id, current_user)

    query = (
        db.query(Article)
        .join(ScholarRun, Article.scholar_run_id == ScholarRun.id)
        .filter(ScholarRun.project_id == project.id)
    )

    if latest_only:
        latest_run = _latest_scholar_run_for_project(db, project.id)
        if not latest_run:
            return []
        query = query.filter(Article.scholar_run_id == latest_run.id)

    if compact:
        # compact_evidence_v2
        rows = query.order_by(Article.score.desc().nullslast(), Article.id.asc()).all()
        output = []
        for article in rows:
            source_json = article.source_json if isinstance(article.source_json, dict) else {}
            evidence = (
                source_json.get("evidence_preflight")
                if isinstance(source_json.get("evidence_preflight"), dict)
                else {}
            )
            access_state = _compact_article_access_state(
                article,
                source_json,
                evidence,
            )
            output.append(
                {
                    "id": article.id,
                    "scholar_run_id": article.scholar_run_id,
                    "verrou_id": article.verrou_id,
                    "title": article.title,
                    "year": article.year,
                    "source": article.source,
                    "tag_article": article.tag_article,
                    "score": article.score,
                    "url": article.url,
                    "doi": article.doi,
                    "consultant_status": article.consultant_status,
                    "source_json": {},
                    "evidence_status": evidence.get("evidence_status") or "NOT_CHECKED",
                    "evidence_label": evidence.get("evidence_label") or "Texte intégral non pré-vérifié",
                    "evidence_usable": bool(evidence.get("evidence_usable")),
                    "fulltext_ready": bool(evidence.get("fulltext_ready")),
                    "candidate_only": bool(evidence.get("candidate_only", True)),
                    "access_check_status": evidence.get("access_check_status"),
                    "evidence_reason_code": evidence.get("reason_code"),
                    "evidence_reason_detail": evidence.get("reason_detail"),
                    "evidence_recommended_action": evidence.get("recommended_action"),
                    "evidence_access_kind": evidence.get("access_kind"),
                    **access_state,
                    "created_at": article.created_at,
                }
            )
        return output

    return query.order_by(Article.score.desc().nullslast(), Article.id.asc()).all()


@router.patch("/projects/{project_id}/articles/{article_id}/decision", response_model=ArticleRead)
def update_article_decision(
    project_id: int,
    article_id: int,
    payload: ArticleDecisionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)

    article = (
        db.query(Article)
        .join(ScholarRun, Article.scholar_run_id == ScholarRun.id)
        .filter(Article.id == article_id, ScholarRun.project_id == project.id)
        .first()
    )

    if not article:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Article introuvable.",
        )

    allowed = {"garde", "rejete", "en_attente"}
    if payload.consultant_status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Statut invalide. Valeurs autorisées : {sorted(allowed)}",
        )

    evidence = (
        (article.source_json or {}).get("evidence_preflight")
        if isinstance(article.source_json, dict)
        else {}
    )
    evidence_status = str((evidence or {}).get("evidence_status") or "").upper()
    unavailable_statuses = {
        "ACCESS_UNAVAILABLE",
        "BROWSER_DOWNLOAD_REQUIRED",
        "ABSTRACT_READY",
        "METADATA_ONLY",
        "EXTRACTION_FAILED",
    }
    if payload.consultant_status in {"garde", "rejete"} and evidence_status in {
        "",
        "NOT_CHECKED",
        "ACCESS_CHECKING",
        "MCP_SEARCHING",
        "EXTRACTION_QUEUED",
        "EXTRACTION_RUNNING",
    }:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La vérification d'accès doit se terminer avant cette décision.",
        )
    if payload.consultant_status in {"garde", "rejete"} and evidence_status == "ACCESS_UNCONFIRMED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Le MCP n'a pas pu conclure. Relance la vérification avant cette décision.",
        )
    if payload.consultant_status in {"garde", "rejete"} and evidence_status in unavailable_statuses:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Le texte intégral n'est pas accessible automatiquement. "
                "Importe le PDF autorisé avant d'activer Garder ou Rejeter."
            ),
        )

    # Le clic Garder est maintenant l'unique action de preparation : si le
    # preflight a deja verifie l'acces, on extrait le texte ici puis la carte
    # est construite juste apres la conservation. Aucun second clic n'est requis.
    if payload.consultant_status == "garde" and evidence_status == "ACCESS_AVAILABLE":
        from services.scholar_evidence_preflight_service import _process_one

        extraction = _process_one(
            int(project.id),
            int(article.id),
            allow_legal_recovery=False,
        )
        db.expire_all()
        article = (
            db.query(Article)
            .join(ScholarRun, Article.scholar_run_id == ScholarRun.id)
            .filter(Article.id == article_id, ScholarRun.project_id == project.id)
            .first()
        )
        refreshed_evidence = (
            (article.source_json or {}).get("evidence_preflight")
            if article is not None and isinstance(article.source_json, dict)
            else {}
        )
        refreshed_status = str(
            (refreshed_evidence or {}).get("evidence_status") or "EXTRACTION_FAILED"
        ).upper()
        if article is None or refreshed_status != "FULLTEXT_READY":
            detail = str(
                (refreshed_evidence or {}).get("reason_detail")
                or extraction.get("reason_detail")
                or "L'extraction du texte integral a echoue."
            ).strip()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Article non garde : {detail}",
            )

    article.consultant_status = payload.consultant_status
    db.commit()
    db.refresh(article)

    # Les Article Cards n'existent que pour la sélection consultant courante.
    # Une conservation crée/réutilise la carte ; un rejet ou retour en attente
    # retire immédiatement la carte et reconstruit l'index des cartes gardées.
    try:
        from services.article_card_builder import (
            sync_article_cards_after_consultant_decision,
        )

        sync_article_cards_after_consultant_decision(db, project, article)
        db.refresh(article)
    except Exception as exc:
        source_json = dict(article.source_json or {})
        source_json["article_card_sync_error"] = f"{type(exc).__name__}: {exc}"
        article.source_json = source_json
        db.add(article)
        db.commit()
        db.refresh(article)
    return article


@router.post(
    "/projects/{project_id}/articles/{article_id}/translate-abstract",
    response_model=ArticleRead,
)
def translate_article_abstract(
    project_id: int,
    article_id: int,
    force: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Endpoint HTTP de traduction à la demande.

    Backend :
    - récupère projet/article ;
    - récupère le meilleur abstract original ;
    - appelle l'agent EnnoScholar ;
    - vérifie que la traduction n'est pas tronquée ;
    - sauvegarde le résultat ;
    - retourne ArticleRead.
    """
    if not _env_bool("ENNOSCHOLAR_TRANSLATE_ON_DEMAND", True):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La traduction à la demande est désactivée dans .env.",
        )

    project = get_project_for_user(db, project_id, current_user)

    article = (
        db.query(Article)
        .join(ScholarRun, Article.scholar_run_id == ScholarRun.id)
        .filter(Article.id == article_id, ScholarRun.project_id == project.id)
        .first()
    )

    if not article:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Article introuvable.",
        )

    abstract = _extract_article_abstract(article)
    if not abstract:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aucun abstract original disponible pour cet article.",
        )

    existing_fr = _get_existing_abstract_fr(article)
    if existing_fr and not force:
        is_truncated, reason = _translation_looks_truncated(abstract, existing_fr)
        if not is_truncated:
            return article

    translation_context = _build_translation_context(project, article)

    try:
        translate_abstract_to_french = _get_translate_abstract_to_french()

        translation = translate_abstract_to_french(
            abstract=abstract,
            context=translation_context,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Traduction impossible : {exc}",
        )

    translated_fr = str(translation.get("abstract_fr") or "").strip()

    is_truncated, truncation_reason = _translation_looks_truncated(
        original=abstract,
        translated=translated_fr,
    )

    if is_truncated:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Traduction rejetée car probablement tronquée. "
                f"Raison : {truncation_reason}. "
                f"original_chars={len(abstract)}, translated_chars={len(translated_fr)}, "
                f"original_words={_word_count(abstract)}, translated_words={_word_count(translated_fr)}"
            ),
        )

    sj: dict[str, Any] = article.source_json if isinstance(article.source_json, dict) else {}
    summary = sj.get("article_summary") if isinstance(sj.get("article_summary"), dict) else {}

    summary["abstract_original"] = abstract
    summary["resume_court"] = abstract
    summary["abstract_original_chars"] = len(abstract)
    summary["abstract_original_words"] = _word_count(abstract)

    summary["abstract_fr"] = translated_fr
    summary["resume_fr"] = translated_fr
    summary["abstract_fr_chars"] = len(translated_fr)
    summary["abstract_fr_words"] = _word_count(translated_fr)

    summary["translation_mode"] = "on_demand_agent_opus"
    summary["translation_provider"] = translation.get("provider")
    summary["translation_model"] = translation.get("model")
    summary["translation_device"] = translation.get("device")
    summary["translation_source_lang"] = translation.get("source_lang")
    summary["translation_target_lang"] = translation.get("target_lang")
    summary["translation_chunks_count"] = translation.get("chunks_count")
    summary["translation_prompt_mode"] = translation.get("prompt_mode")
    summary["translation_quality_guard"] = translation.get("quality_guard")
    summary["translation_truncation_guard"] = truncation_reason
    summary["translation_cached"] = True

    sj["article_summary"] = summary
    sj["abstract_fr"] = translated_fr

    article.source_json = sj
    flag_modified(article, "source_json")

    db.commit()
    db.refresh(article)
    return article


# ============================================================
# Synchronisation centrale de la sélection courante
# ============================================================

def _synchronize_current_article_selection(
    db: Session,
    project: Any,
) -> dict[str, Any]:
    """
    Reconstruit la sélection Phase 1 et synchronise les artefacts dérivés.

    Le service scholar_state_of_art_payload_service :
    - prend la sélection consultant courante comme source de vérité ;
    - conserve les extractions et cartes encore sélectionnées ;
    - supprime les artefacts des articles retirés ;
    - invalide les phases dépendantes lorsque la sélection change.

    Aucun téléchargement, MCP ou appel LLM n'est lancé ici.
    """
    from services.scholar_state_of_art_payload_service import (
        build_state_of_art_selection_payload,
    )

    payload = build_state_of_art_selection_payload(
        db=db,
        project=project,
    )

    if not isinstance(payload, dict) or not payload.get("ok"):
        raise RuntimeError(
            "Impossible de reconstruire la sélection EnnoScholar actuelle."
        )

    return payload


# ============================================================
# État de l'art EnnoScholar — rédaction après sélection consultant
# ============================================================

@router.get("/projects/{project_id}/scholar/state-of-art/selection-preview")
def preview_state_of_art_selection(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)

    return _synchronize_current_article_selection(
        db=db,
        project=project,
    )


@router.get("/projects/{project_id}/scholar/state-of-art/latest")
def get_latest_state_of_art(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Retourne le dernier état de l'art Phase 5 avec :
    - markdown final ;
    - verrous rédigés ;
    - method_evidence_chains V5.9 ;
    - guard / quality / chemins.
    """
    project = get_project_for_user(db, project_id, current_user)

    from services.ennoscholar_state_of_art_orchestrator import read_latest_state_of_art

    return read_latest_state_of_art(project)


@router.get("/projects/{project_id}/scholar/state-of-art/history")
def get_state_of_art_history(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Historique compatible frontend. Pour l'instant, renvoie le dernier état
    de l'art comme une entrée d'historique unique.
    """
    project = get_project_for_user(db, project_id, current_user)

    from services.ennoscholar_state_of_art_orchestrator import get_state_of_art_history

    return get_state_of_art_history(project)


@router.get(
    "/projects/{project_id}/scholar/state-of-art/visuals/{visual_id}"
)
def get_state_of_art_visual(
    project_id: int,
    visual_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Diffuse une figure extraite après contrôle d'accès au projet."""

    project = get_project_for_user(db, project_id, current_user)
    from services.scholar_visual_evidence_service import resolve_visual_asset

    path = resolve_visual_asset(project, visual_id)
    if path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Figure scientifique introuvable.",
        )
    media_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }
    return FileResponse(
        path=str(path),
        media_type=media_types.get(path.suffix.casefold(), "application/octet-stream"),
        filename=path.name,
        content_disposition_type="inline",
    )

@router.get("/projects/{project_id}/scholar/fulltext/status")
def get_scholar_fulltext_status(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Statut canonique combiné : upload, direct vérifié et récupération MCP légale."""
    project = get_project_for_user(db, project_id, current_user)

    from services.scholar_legal_recovery_service import (
        get_combined_fulltext_status_for_selected_articles,
    )

    return get_combined_fulltext_status_for_selected_articles(db, project)


@router.post("/projects/{project_id}/scholar/articles/{article_id}/fulltext/fetch")
def fetch_scholar_article_fulltext(
    project_id: int,
    article_id: int,
    force: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)

    from services.scholar_direct_fulltext_service import resolve_and_extract_fulltext_for_article

    return resolve_and_extract_fulltext_for_article(
        db=db,
        project=project,
        article_id=article_id,
        refresh_resolution=force,
        force_reextract=False,
    )


@router.post(
    "/projects/{project_id}/scholar/articles/{article_id}/fulltext/extract-on-demand",
    response_model=ArticleRead,
)
def extract_scholar_article_fulltext_on_demand(
    project_id: int,
    article_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Extrait un seul article après un clic explicite du consultant."""
    project = get_project_for_user(db, project_id, current_user)
    article = (
        db.query(Article)
        .join(ScholarRun, Article.scholar_run_id == ScholarRun.id)
        .filter(Article.id == article_id, ScholarRun.project_id == project.id)
        .first()
    )
    if article is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article introuvable.")

    evidence = (
        (article.source_json or {}).get("evidence_preflight")
        if isinstance(article.source_json, dict)
        else {}
    )
    evidence_status = str((evidence or {}).get("evidence_status") or "NOT_CHECKED").upper()
    if evidence_status in {"ACCESS_CHECKING", "MCP_SEARCHING"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La vérification d'accès est encore en cours pour cet article.",
        )
    if evidence_status in {"ACCESS_UNAVAILABLE", "BROWSER_DOWNLOAD_REQUIRED", "ACCESS_UNCONFIRMED", "ABSTRACT_READY", "METADATA_ONLY", "EXTRACTION_FAILED"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Aucune copie exploitable n'est accessible automatiquement. "
                "Importe le PDF autorisé pour lancer l'extraction."
            ),
        )

    from services.scholar_evidence_preflight_service import _process_one
    _process_one(int(project.id), int(article.id))
    db.expire_all()
    refreshed = db.get(Article, int(article.id))
    if refreshed is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article introuvable après extraction.")
    return refreshed


@router.post("/projects/{project_id}/scholar/fulltext/fetch-selected")
def fetch_scholar_selected_articles_fulltext(
    project_id: int,
    force: bool = Query(False),
    max_articles: int | None = Query(
        None,
        description="Nombre max d'articles à traiter. None ou 0 = tous les articles sélectionnés.",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)

    selection_payload = _synchronize_current_article_selection(
        db=db,
        project=project,
    )

    from services.scholar_direct_fulltext_service import (
        resolve_and_extract_fulltext_for_selected_articles,
    )

    effective_max_articles = (
        None if not max_articles or max_articles <= 0 else max_articles
    )

    result = resolve_and_extract_fulltext_for_selected_articles(
        db=db,
        project=project,
        max_articles=effective_max_articles,
        refresh_resolution=force,
        force_reextract=False,
    )

    if isinstance(result, dict):
        result["selection_sync"] = (
            selection_payload.get("artifact_sync") or {}
        )

    return result


@router.get("/projects/{project_id}/scholar/fulltext/direct-extract-status")
def get_scholar_direct_extract_status(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)

    from services.scholar_direct_fulltext_service import (
        get_unified_fulltext_status_for_selected_articles,
    )

    return get_unified_fulltext_status_for_selected_articles(db, project)


@router.post("/projects/{project_id}/scholar/articles/{article_id}/fulltext/extract-direct")
def extract_scholar_article_fulltext_direct(
    project_id: int,
    article_id: int,
    force: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)

    from services.scholar_direct_fulltext_service import (
        resolve_and_extract_fulltext_for_article,
    )

    return resolve_and_extract_fulltext_for_article(
        db=db,
        project=project,
        article_id=article_id,
        # force=true relit les URLs déjà connues pour les articles manquants,
        # sans refaire l'OCR des textes déjà extraits.
        refresh_resolution=force,
        force_reextract=False,
    )

@router.post("/projects/{project_id}/scholar/fulltext/extract-direct-selected")
def extract_scholar_selected_fulltext_direct(
    project_id: int,
    force: bool = Query(False),
    max_articles: int | None = Query(
        None,
        description="Nombre max d'articles à traiter. None ou 0 = tous les articles sélectionnés.",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)

    selection_payload = _synchronize_current_article_selection(
        db=db,
        project=project,
    )

    from services.scholar_direct_fulltext_service import (
        resolve_and_extract_fulltext_for_selected_articles,
    )

    effective_max_articles = (
        None if not max_articles or max_articles <= 0 else max_articles
    )

    result = resolve_and_extract_fulltext_for_selected_articles(
        db=db,
        project=project,
        max_articles=effective_max_articles,
        refresh_resolution=force,
        force_reextract=False,
    )

    if isinstance(result, dict):
        result["selection_sync"] = (
            selection_payload.get("artifact_sync") or {}
        )

    return result


@router.post("/projects/{project_id}/scholar/fulltext/resolve-and-extract-selected")
def resolve_and_extract_scholar_selected_fulltext(
    project_id: int,
    refresh_resolution: bool = Query(
        False,
        description="Relit les URLs déjà connues pour les articles sans texte intégral exploitable.",
    ),
    force_reextract: bool = Query(
        False,
        description="Refait volontairement l'extraction/OCR, y compris pour les textes déjà extraits.",
    ),
    max_articles: int | None = Query(
        None,
        description="Nombre max d'articles. None ou 0 = tous les articles sélectionnés.",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Endpoint canonique direct : URLs connues + PDF/HTML/XML + extraction mémoire.

    Il ne contacte aucun résolveur externe et ne relance pas l'OCR des succès
    sans `force_reextract=true`.
    """
    project = get_project_for_user(db, project_id, current_user)

    selection_payload = _synchronize_current_article_selection(
        db=db,
        project=project,
    )

    from services.scholar_direct_fulltext_service import (
        resolve_and_extract_fulltext_for_selected_articles,
    )

    effective_max_articles = None if not max_articles or max_articles <= 0 else max_articles
    result = resolve_and_extract_fulltext_for_selected_articles(
        db=db,
        project=project,
        max_articles=effective_max_articles,
        refresh_resolution=refresh_resolution,
        force_reextract=force_reextract,
    )

    if isinstance(result, dict):
        result["selection_sync"] = (
            selection_payload.get("artifact_sync") or {}
        )

    return result


@router.post("/projects/{project_id}/scholar/articles/upload-source")
async def upload_new_scholar_source(
    project_id: int,
    file: UploadFile = File(...),
    title: str | None = Form(None),
    source_url: str | None = Form(None),
    year: int | None = Form(None),
    doi: str | None = Form(None),
    guided_session_id: str | None = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Ajoute un PDF consultant au corpus puis exécute les phases 1 et 2."""
    project = get_project_for_user(db, project_id, current_user)
    filename = str(file.filename or "").strip()
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La nouvelle source doit être un fichier PDF.",
        )

    conversation_scope_id: str | None = None
    if guided_session_id:
        from services.guided_research_service import _guided_corpus_run

        _, scholar_run, guided_snapshot = _guided_corpus_run(
            db,
            project,
            session_id=str(guided_session_id),
            create=True,
        )
        if scholar_run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Corpus de conversation introuvable.",
            )
        guided_context = dict(guided_snapshot.get("context") or {})
        conversation_scope_id = str(
            guided_context.get("corpus_scope_id") or guided_session_id
        ).strip()
    else:
        scholar_run = _latest_scholar_run_for_project(db, project.id)
        if scholar_run is None:
            scholar_run = ScholarRun(
                project_id=project.id,
                status="manual_source_upload",
                raw_result_json={},
            )
            db.add(scholar_run)
            db.commit()
            db.refresh(scholar_run)

    fallback_title = re.sub(r"[_-]+", " ", Path(filename).stem).strip()
    article_title = str(title or fallback_title or "Source importée").strip()
    source_json = {
        "guided_research_source": True,
        "manual_upload_source": True,
        "candidate_kind": "scientific_article",
        "consultant_evidence_role": "connected_evidence",
        "uploaded_filename": filename,
        "guided_session_id": str(guided_session_id or "").strip() or None,
        "corpus_scope_id": conversation_scope_id,
        "conversation_owned": bool(guided_session_id),
        "project_corpus_eligible": True,
        "project_corpus_scope": "project",
        "project_corpus_global": True,
        "project_corpus_status": "fulltext_ready",
        "origin": (
            "guided_research_conversation"
            if guided_session_id
            else "manual_project_upload"
        ),
    }
    article = Article(
        scholar_run_id=scholar_run.id,
        verrou_id=None,
        title=article_title,
        year=year,
        source="consultant_upload",
        tag_article="Connexe",
        score=1.0,
        url=str(source_url or "").strip() or None,
        doi=str(doi or "").strip() or None,
        consultant_status="garde",
        source_json=source_json,
    )
    db.add(article)
    db.commit()
    db.refresh(article)

    from services.scholar_uploaded_pdf_extractor import (
        upload_and_extract_pdf_for_article,
    )

    try:
        extraction = await upload_and_extract_pdf_for_article(
            db=db,
            project=project,
            article_id=int(article.id),
            file=file,
            source_url=source_url,
        )
        if extraction.get("ok") is not True:
            raise ValueError(
                extraction.get("message")
                or "Le PDF n'a pas pu être extrait."
            )
        db.refresh(article)
        normalized_source_json = (
            dict(article.source_json)
            if isinstance(article.source_json, dict)
            else {}
        )
        normalized_source_json["guided_candidate_id"] = (
            normalized_source_json.get("guided_candidate_id")
            or f"UPLOAD-{int(article.id)}"
        )
        normalized_source_json["guided_session_id"] = (
            str(guided_session_id or "").strip() or None
        )
        article.source_json = normalized_source_json
        db.add(article)
        db.commit()
        db.refresh(article)
        if guided_session_id:
            from services.guided_research_service import (
                attach_uploaded_article_to_session,
            )

            attach_uploaded_article_to_session(
                db,
                project,
                session_id=str(guided_session_id),
                article=article,
                extraction=extraction,
            )
    except Exception as exc:
        db.delete(article)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    if guided_session_id:
        from services.guided_research_service import (
            rebuild_guided_research_corpus_cards,
        )

        selection_payload = {
            "ok": True,
            "scope_id": conversation_scope_id,
            "policy": "project_persistent_corpus_conversation_provenance",
        }
        article_cards = rebuild_guided_research_corpus_cards(
            db,
            project,
            session_id=str(guided_session_id),
            force=True,
        )
    else:
        selection_payload = _synchronize_current_article_selection(
            db=db,
            project=project,
        )

        from services.article_card_builder import (
            build_article_cards_for_selected_articles,
        )

        article_cards = build_article_cards_for_selected_articles(
            db=db,
            project=project,
            mode="auto",
            force=False,
        )
    db.refresh(article)
    return {
        "ok": True,
        "article": ArticleRead.model_validate(article).model_dump(mode="json"),
        "extraction": extraction,
        "phase_1": {
            "ok": bool(selection_payload.get("ok")),
            "selection_summary": selection_payload.get("selection_summary") or {},
            "artifact_sync": selection_payload.get("artifact_sync") or {},
        },
        "phase_2": article_cards,
    }


@router.post("/projects/{project_id}/scholar/articles/{article_id}/fulltext/upload-and-extract")
async def upload_and_extract_scholar_article_pdf(
    project_id: int,
    article_id: int,
    file: UploadFile = File(...),
    source_url: str | None = Form(None),
    guided_session_id: str | None = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)

    from services.scholar_uploaded_pdf_extractor import (
        upload_and_extract_pdf_for_article,
    )

    result = await upload_and_extract_pdf_for_article(
        db=db,
        project=project,
        article_id=article_id,
        file=file,
        source_url=source_url,
    )
    # ENNOSCHOLAR_ACCESS_UX_V165
    # Un mauvais PDF ne doit jamais être traité comme un upload réussi.
    if result.get("ok") is not True:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(
                result.get("message")
                or "Le PDF importé n'a pas pu être validé pour cet article."
            ),
        )
    if guided_session_id and result.get("ok") is True:
        from db.models import Article
        from services.guided_research_service import (
            attach_uploaded_article_to_session,
            rebuild_guided_research_corpus_cards,
        )

        article = (
            db.query(Article)
            .filter(Article.id == article_id)
            .first()
        )
        if article is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Article introuvable après import.",
            )
        attach_uploaded_article_to_session(
            db,
            project,
            session_id=str(guided_session_id),
            article=article,
            extraction=result,
        )
        result["phase_1"] = {
            "ok": True,
            "policy": "project_persistent_corpus_conversation_provenance",
        }
        result["phase_2"] = rebuild_guided_research_corpus_cards(
            db,
            project,
            session_id=str(guided_session_id),
            force=True,
        )
    return result


@router.get("/projects/{project_id}/scholar/articles/{article_id}/uploaded-pdf")
def read_uploaded_scholar_article_pdf(
    project_id: int,
    article_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)
    from services.scholar_uploaded_pdf_extractor import (
        get_article_for_project,
        uploaded_pdf_path,
    )

    article = get_article_for_project(db, project, article_id)
    pdf_path = uploaded_pdf_path(project, article)
    if not pdf_path.exists() or not pdf_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="PDF importé introuvable pour cet article.",
        )
    source_json = (
        article.source_json
        if isinstance(article.source_json, dict)
        else {}
    )
    download_name = str(
        source_json.get("uploaded_filename")
        or f"{article.title}.pdf"
    )
    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename=download_name,
        content_disposition_type="inline",
    )


@router.post("/projects/{project_id}/scholar/state-of-art/article-cards/build")
def build_scholar_article_cards(
    project_id: int,
    mode: str = Query("auto"),
    force: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)

    selection_payload = _synchronize_current_article_selection(
        db=db,
        project=project,
    )
    artifact_sync = selection_payload.get("artifact_sync") or {}

    effective_force = bool(
        force
        or artifact_sync.get("selection_changed")
        or artifact_sync.get("article_cards_payload_deleted")
    )

    from services.article_card_builder import (
        build_article_cards_for_selected_articles,
    )

    result = build_article_cards_for_selected_articles(
        db=db,
        project=project,
        mode=mode,
        force=effective_force,
    )

    if isinstance(result, dict):
        result["selection_sync"] = artifact_sync
        result["effective_force"] = effective_force

    return result


@router.get("/projects/{project_id}/scholar/state-of-art/article-cards")
def get_scholar_article_cards(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)

    from services.article_card_builder import get_article_cards_payload

    return get_article_cards_payload(project, db=db)

def _run_state_of_art_full_pipeline(
    project_id: int,
    force_phase3: bool,
    force_article_cards: bool,
    enable_polish: bool | None,
    db: Session,
    current_user: User,
    guided_session_id: str | None = None,
):
    project = get_project_for_user(db, project_id, current_user)

    # ENNOSMART_DEV_WALLET_RUN_PREFLIGHT_V1
    try:
        from services.ennosmart_dev_wallet_service import (
            assert_dev_budget_for_state_of_art,
        )

        assert_dev_budget_for_state_of_art(
            db=db,
            project=project,
        )
    except Exception as budget_exc:
        if type(budget_exc).__name__ == "BudgetLimitExceeded":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(budget_exc),
            ) from budget_exc
        raise

    latest_run = _state_of_art_preflight_run(
        db,
        project,
        guided_session_id,
    )
    if latest_run is not None:
        if guided_session_id:
            # V169.1 : le preflight contrôle le même corpus projet que celui que
            # le writer va réellement lire, pas le ScholarRun privé du chat.
            from services.guided_research_service import get_guided_research_agent
            from services.ennoscholar_project_corpus_service import (
                get_project_kept_articles,
            )

            agent = get_guided_research_agent()
            guided_snapshot = agent.repository.snapshot(db, str(guided_session_id))
            guided_context = dict(guided_snapshot.get("context") or {})
            active_verrou_ids = (
                list(guided_context.get("active_verrou_ids") or [])
                if str(guided_context.get("review_scope") or "") == "per_verrou"
                else []
            )
            run_articles = get_project_kept_articles(
                db,
                project,
                active_verrou_ids=active_verrou_ids,
            )
        else:
            run_articles = (
                db.query(Article)
                .filter(Article.scholar_run_id == latest_run.id)
                .all()
            )


        # V3 UX : la rédaction dépend uniquement du corpus explicitement
        # gardé par le consultant. Les autres candidats du dernier ScholarRun
        # peuvent continuer leur preflight sans bloquer le writer.
        pending_statuses = {
            "",
            "NOT_CHECKED",
            "ACCESS_CHECKING",
            "MCP_SEARCHING",
            "EXTRACTION_QUEUED",
            "EXTRACTION_RUNNING",
        }
        kept_articles = [
            article
            for article in run_articles
            if str(article.consultant_status or "").strip().casefold() == "garde"
        ]

        if not kept_articles:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Rédaction bloquée : aucun article n'est actuellement gardé "
                    "dans le corpus consultant."
                ),
            )

        pending_kept = []
        kept_without_fulltext = []
        for article in kept_articles:
            source_json = (
                article.source_json
                if isinstance(article.source_json, dict)
                else {}
            )
            evidence = source_json.get("evidence_preflight")
            evidence = evidence if isinstance(evidence, dict) else {}
            evidence_status = str(
                evidence.get("evidence_status") or "NOT_CHECKED"
            ).upper()

            if evidence_status in pending_statuses:
                pending_kept.append(article)
            elif evidence_status != "FULLTEXT_READY":
                kept_without_fulltext.append(article)

        if pending_kept:
            preview = " ; ".join(
                str(article.title or f"Article {article.id}")[:140]
                for article in pending_kept[:4]
            )
            suffix = f" (+{len(pending_kept) - 4})" if len(pending_kept) > 4 else ""
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Rédaction bloquée : {len(pending_kept)} article(s) gardé(s) "
                    "sont encore en cours de vérification/extraction"
                    + (f" : {preview}{suffix}." if preview else ".")
                ),
            )

        if kept_without_fulltext:
            preview = " ; ".join(
                str(article.title or f"Article {article.id}")[:140]
                for article in kept_without_fulltext[:4]
            )
            suffix = (
                f" (+{len(kept_without_fulltext) - 4})"
                if len(kept_without_fulltext) > 4
                else ""
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Rédaction bloquée : {len(kept_without_fulltext)} article(s) "
                    "gardé(s) n'ont pas de texte intégral exploitable"
                    + (f" : {preview}{suffix}. " if preview else ". ")
                    + "Importez leur PDF autorisé ou retirez-les du corpus avant "
                    "de relancer la rédaction."
                ),
            )

    try:
        # Une rédaction demandée depuis le chat possède un contrat, un corpus,
        # des checkpoints et des versions propres à sa conversation. Le graphe
        # historique reconstruit encore ses chemins depuis la racine globale du
        # projet ; l'utiliser ici ferait donc relire ou écraser les artefacts
        # d'une autre conversation. Le pipeline conversationnel applique déjà
        # l'isolation complète et reste la seule voie autorisée dans ce cas.
        use_langgraph = (
            _env_bool("ENNOSCHOLAR_LANGGRAPH_ENABLED", True)
            and not guided_session_id
        )
        if use_langgraph:
            from services.ennoscholar_langgraph_state_of_art_service import (
                run_state_of_art_langgraph,
            )

            result = run_state_of_art_langgraph(
                db=db,
                project=project,
                force_phase3=force_phase3,
                force_article_cards=force_article_cards,
                enable_polish=enable_polish,
                guided_session_id=guided_session_id,
                user_id=getattr(current_user, "id", None),
            )
        else:
            from services.ennoscholar_state_of_art_orchestrator import (
                generate_state_of_art_after_consultant_selection,
            )

            result = generate_state_of_art_after_consultant_selection(
                db=db,
                project=project,
                force_phase3=force_phase3,
                force_article_cards=force_article_cards,
                enable_polish=enable_polish,
                guided_session_id=guided_session_id,
            )
        if guided_session_id and result.get("ok"):
            from services.ennoscholar_conversation_state_service import (
                assert_conversation_result_is_isolated,
            )

            assert_conversation_result_is_isolated(
                project,
                guided_session_id,
                result,
            )
        if guided_session_id:
            from services.guided_research_service import (
                record_guided_pipeline_result,
            )

            record_guided_pipeline_result(
                db,
                project,
                session_id=guided_session_id,
                result=result,
            )
        return result
    except RuntimeError as exc:
        # La commande consultant est valide : une indisponibilité LLM ou une
        # tentative de rédaction non publiable est un état récupérable du
        # pipeline, jamais une erreur HTTP 400 imputable au client.
        internal_detail = str(exc)
        print(
            "[EnnoScholar][SOA][INTERNAL] Génération différée: "
            f"{type(exc).__name__}: {internal_detail}"
        )
        lowered = internal_detail.casefold()
        provider_unavailable = any(
            marker in lowered
            for marker in (
                "429",
                "rate limit",
                "rate_limit",
                "quota",
                "temporarily unavailable",
                "timeout",
            )
        )
        empty_conversation_scope = "verrou_scope_no_article" in lowered
        conversation_scope_violation = "conversation_scope_violation" in lowered
        failure = {
            "ok": False,
            "status": (
                "writing_service_temporarily_unavailable"
                if provider_unavailable
                else (
                    "conversation_scope_empty"
                    if empty_conversation_scope
                    else (
                        "conversation_scope_violation"
                        if conversation_scope_violation
                        else "evidence_revision_required"
                    )
                )
            ),
            "assistant_message": (
                "Le service de rédaction est momentanément saturé. Votre "
                "corpus, votre plan et vos choix sont conservés ; relancez la "
                "rédaction sans recommencer la recherche."
                if provider_unavailable
                else (
                    "Je n'ai trouvé aucune Article Card validée rattachée au "
                    "périmètre scientifique de cette conversation. Le plan est "
                    "conservé ; vérifiez le rattachement des publications au "
                    "verrou demandé avant de relancer."
                    if empty_conversation_scope
                    else (
                        "La tentative a été arrêtée pour protéger l'isolation de "
                        "cette conversation : le document produit ne lui était "
                        "pas correctement rattaché. Aucun autre état de l'art "
                        "n'a été publié dans ce chat."
                        if conversation_scope_violation
                        else
                        "Je n'ai pas publié cette tentative, car certaines parties "
                        "doivent encore être mieux reliées aux publications validées. "
                        "Votre corpus et votre plan sont conservés ; vous pouvez "
                        "poursuivre directement dans le chat."
                    )
                )
            ),
            "retryable": True,
            "previous_draft_preserved": True,
        }
        if guided_session_id:
            from services.guided_research_service import (
                record_guided_pipeline_result,
            )

            record_guided_pipeline_result(
                db,
                project,
                session_id=guided_session_id,
                result=failure,
            )
        return failure
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Génération état de l’art impossible : {exc}",
        )


@router.post("/projects/{project_id}/scholar/state-of-art/generate")
def generate_scholar_state_of_art(
    project_id: int,
    force_phase3: bool = Query(True),
    force_article_cards: bool = Query(False),
    enable_polish: bool | None = Query(None),
    guided_session_id: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Lance le pipeline complet après sélection consultant.
    Conservé pour compatibilité avec l'ancien frontend.
    """
    return _run_state_of_art_full_pipeline(
        project_id=project_id,
        force_phase3=force_phase3,
        force_article_cards=force_article_cards,
        enable_polish=enable_polish,
        db=db,
        current_user=current_user,
        guided_session_id=guided_session_id,
    )




@router.get("/projects/{project_id}/scholar/dev-budget-status")
def get_scholar_dev_budget_status(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Budget DEV local + estimation, sans appel LLM."""
    project = get_project_for_user(
        db,
        project_id,
        current_user,
    )
    from services.ennosmart_dev_wallet_service import (
        get_dev_budget_status,
    )

    return get_dev_budget_status(
        db=db,
        project=project,
    )


@router.get("/projects/{project_id}/scholar/state-of-art/cost-estimate")
def estimate_scholar_state_of_art_cost(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Estime le coût sans lancer aucun appel LLM."""
    project = get_project_for_user(
        db,
        project_id,
        current_user,
    )
    from services.ennoscholar_cost_service import (
        estimate_state_of_art_cost,
    )

    return estimate_state_of_art_cost(
        db=db,
        project=project,
    )


@router.post("/projects/{project_id}/scholar/state-of-art/run-full")
def run_full_scholar_state_of_art(
    project_id: int,
    force_phase3: bool = Query(True),
    force_article_cards: bool = Query(False),
    enable_polish: bool | None = Query(None),
    guided_session_id: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Nouveau nom explicite : Phase 1/2D/3/4/4.5/4.6/4.7/5.
    À utiliser pour tester tous les verrous et générer l'état de l'art complet.
    """
    return _run_state_of_art_full_pipeline(
        project_id=project_id,
        force_phase3=force_phase3,
        force_article_cards=force_article_cards,
        enable_polish=enable_polish,
        db=db,
        current_user=current_user,
        guided_session_id=guided_session_id,
    )


@router.get("/projects/{project_id}/scholar/state-of-art/workflow-status")
def get_scholar_state_of_art_workflow_status(
    project_id: int,
    guided_session_id: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """État LangGraph/checkpoint sans lancer de rédaction ni appel LLM."""
    project = get_project_for_user(db, project_id, current_user)
    from services.ennoscholar_langgraph_state_of_art_service import (
        get_state_of_art_workflow_status,
    )

    return get_state_of_art_workflow_status(
        db=db,
        project=project,
        user_id=getattr(current_user, "id", None),
        guided_session_id=guided_session_id,
    )


# BEGIN ENNOSCHOLAR_CONVERSATION_VERSIONING_V4
@router.get(
    "/projects/{project_id}/scholar/state-of-art/conversations/{session_id}/versions"
)
def list_scholar_conversation_state_of_art_versions(
    project_id: int,
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)
    from services.guided_research_service import get_guided_research_agent
    from services.ennoscholar_conversation_state_service import list_conversation_versions

    snapshot = get_guided_research_agent().repository.snapshot(db, session_id)
    if int(snapshot.get("project_id") or 0) != int(project.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation introuvable pour ce projet.",
        )
    versions = list_conversation_versions(project, session_id)
    return {
        "ok": True,
        "project_id": int(project.id),
        "session_id": session_id,
        "versions_count": len(versions),
        "versions": versions,
    }


@router.get(
    "/projects/{project_id}/scholar/state-of-art/conversations/{session_id}/versions/{version_id}"
)
def get_scholar_conversation_state_of_art_version(
    project_id: int,
    session_id: str,
    version_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)
    from services.guided_research_service import get_guided_research_agent
    from services.ennoscholar_conversation_state_service import get_conversation_version

    snapshot = get_guided_research_agent().repository.snapshot(db, session_id)
    if int(snapshot.get("project_id") or 0) != int(project.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation introuvable pour ce projet.",
        )
    try:
        result = get_conversation_version(project, session_id, version_id)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Version d'état de l'art introuvable.",
        )
    return {"ok": True, **result}
# END ENNOSCHOLAR_CONVERSATION_VERSIONING_V4



# BEGIN ENNOSCHOLAR_CONVERSATION_COST_ESTIMATE_V4
@router.get(
    "/projects/{project_id}/scholar/state-of-art/conversations/{session_id}/cost-estimate"
)
def get_scholar_conversation_cost_estimate(
    project_id: int,
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)
    from services.ennoscholar_conversation_state_service import (
        estimate_conversation_cost,
    )
    try:
        return estimate_conversation_cost(
            db,
            project,
            session_id,
        )
    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation introuvable pour ce projet.",
        )
# END ENNOSCHOLAR_CONVERSATION_COST_ESTIMATE_V4


# ============================================================
# EnnoScholar — récupération légale sélective via MCP
# ============================================================

@router.post("/projects/{project_id}/scholar/fulltext/recover-legal-problems")
def recover_scholar_problem_articles_legally(
    project_id: int,
    force_refresh: bool = Query(
        False,
        description="Ignore le cache positif et relance les fournisseurs légaux.",
    ),
    search_all: bool = Query(
        False,
        description="False : arrêt à la première copie légale vérifiée. True : audit de tous les fournisseurs.",
    ),
    max_articles: int | None = Query(
        None,
        description=(
            "None ou 0 = tous les articles sélectionnés ; "
            "le MCP ne cible que les échecs directs."
        ),
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Lance le MCP uniquement pour les articles sélectionnés sans texte intégral vérifié."""
    project = get_project_for_user(db, project_id, current_user)

    selection_payload = _synchronize_current_article_selection(
        db=db,
        project=project,
    )

    from services.scholar_legal_recovery_service import (
        recover_legal_fulltext_for_problem_articles,
    )

    effective_max_articles = (
        None if not max_articles or max_articles <= 0 else max_articles
    )

    result = recover_legal_fulltext_for_problem_articles(
        db=db,
        project=project,
        force_refresh=force_refresh,
        search_all=search_all,
        max_articles=effective_max_articles,
    )

    if isinstance(result, dict):
        result["selection_sync"] = (
            selection_payload.get("artifact_sync") or {}
        )

    return result


@router.post(
    "/projects/{project_id}/scholar/articles/{article_id}/fulltext/recover-legal"
)
def recover_one_scholar_article_legally(
    project_id: int,
    article_id: int,
    force_refresh: bool = Query(False),
    search_all: bool = Query(True),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Teste la récupération MCP légale pour un seul article."""
    project = get_project_for_user(db, project_id, current_user)

    from services.scholar_legal_recovery_service import (
        recover_legal_fulltext_for_article,
    )

    return recover_legal_fulltext_for_article(
        db=db,
        project=project,
        article_id=article_id,
        force_refresh=force_refresh,
        search_all=search_all,
    )


@router.get("/projects/{project_id}/scholar/fulltext/combined-status")
def get_scholar_combined_fulltext_status(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Expose explicitement le statut combiné direct + MCP + upload."""
    project = get_project_for_user(db, project_id, current_user)

    from services.scholar_legal_recovery_service import (
        get_combined_fulltext_status_for_selected_articles,
    )

    return get_combined_fulltext_status_for_selected_articles(db, project)
