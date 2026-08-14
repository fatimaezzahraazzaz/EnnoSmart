from __future__ import annotations

import importlib
import math
import re
import unicodedata
from collections import Counter
from typing import Any

from ..domain.models import ImprovementRequest
from .diagnostic_orchestration_service import ensure_diagnostic_context
from .research_context_service import build_lightweight_research_context


RESEARCH_USE_EXISTING = "use_existing_sources"
RESEARCH_LAUNCH_TARGETED = "launch_targeted_research"


def explicitly_forbids_research(value: str | None) -> bool:
    """Interdiction explicite de toute nouvelle recherche.

    Cette règle est plus forte que les choix de recherche mémorisés ou les
    heuristiques lexicales. Elle sert de coupe-circuit avant tout appel Scholar.
    """

    text = _norm(value)
    if not text:
        return False
    patterns = (
        r"\bne\s+(?:lance|lancer|demarre|demarrer|execute|executer|effectue|effectuer|fais|faire)\s+(?:aucune?|pas\s+de)\s+recherche\b",
        r"\bn['’]?utilise\s+aucune?\s+nouvelle?\s+source\b",
        r"\baucune?\s+nouvelle?\s+(?:recherche|source|publication)\b",
        r"\bsans\s+(?:nouvelle?\s+)?recherche\b",
        r"\bpas\s+de\s+(?:nouvelle?\s+)?recherche\b",
        r"\bne\s+cherche\s+(?:aucune?|pas\s+de)\s+(?:publication|source|article)s?\b",
    )
    return any(re.search(pattern, text, flags=re.I) for pattern in patterns)


def explicitly_forbids_scholar(value: str | None) -> bool:
    """Détecte une interdiction explicite d'EnnoScholar."""

    text = _norm(value)
    if not text:
        return False
    patterns = (
        r"\bne\s+(?:lance|lancer|utilise|utiliser|appelle|appeler)\s+pas\s+ennoscholar\b",
        r"\bsans\s+ennoscholar\b",
        r"\bpas\s+d['’]?ennoscholar\b",
    )
    return any(re.search(pattern, text, flags=re.I) for pattern in patterns)


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = (
        text.replace("é", "e")
        .replace("è", "e")
        .replace("ê", "e")
        .replace("à", "a")
        .replace("â", "a")
        .replace("î", "i")
        .replace("ï", "i")
        .replace("ô", "o")
        .replace("ù", "u")
        .replace("û", "u")
        .replace("ç", "c")
    )
    return re.sub(r"\s+", " ", text).strip()


def _has_positive_match(text: str, patterns: tuple[str, ...], negative_patterns: tuple[str, ...]) -> bool:
    """Retourne True uniquement pour une commande positive non niée localement.

    Les prompts consultants contiennent souvent des contraintes du type
    ``ne réutilise pas les sources déjà validées``. Un simple ``.*`` global
    pouvait autrefois relier ``sources déjà validées`` à un ``uniquement`` situé
    beaucoup plus loin et transformer à tort une demande de NOUVELLE recherche
    en ``use_existing_sources``. On borne maintenant les motifs à une proposition
    et on contrôle la négation autour du match.
    """

    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.I):
            window = text[max(0, match.start() - 45): min(len(text), match.end() + 45)]
            if any(re.search(negative, window, flags=re.I) for negative in negative_patterns):
                continue
            return True
    return False


def detect_research_choice(value: str | None) -> str | None:
    """Détecte le choix explicite du consultant sans confondre les contraintes.

    Priorité métier : une autorisation positive et explicite de lancer une
    nouvelle recherche gagne sur les mentions négatives des anciennes sources
    (``ne réutilise pas ...``). Inversement, ``ne lance pas de recherche`` ne
    doit jamais être interprété comme une autorisation de recherche.
    """

    text = _norm(value)
    if not text:
        return None

    explicit_ids = {
        RESEARCH_USE_EXISTING: RESEARCH_USE_EXISTING,
        RESEARCH_LAUNCH_TARGETED: RESEARCH_LAUNCH_TARGETED,
    }
    if text in explicit_ids:
        return explicit_ids[text]

    # Une phrase d'interdiction n'est jamais un choix "utiliser l'existant"
    # et ne doit surtout pas réactiver Scholar.
    if explicitly_forbids_research(value) or explicitly_forbids_scholar(value):
        return None

    # Les motifs restent volontairement locaux (pas de ``.*`` global) afin
    # qu'une contrainte située plusieurs phrases plus loin ne change pas le choix.
    launch_patterns = (
        r"\blanc(?:e|er|ons|ez)\b[^.!?;]{0,140}\b(?:nouvelle?\s+)?recherche\b",
        r"\bdemarr(?:e|er|ons|ez)\b[^.!?;]{0,140}\b(?:nouvelle?\s+)?recherche\b",
        r"\bexecut(?:e|er|ons|ez)\b[^.!?;]{0,140}\b(?:nouvelle?\s+)?recherche\b",
        r"\beffectu(?:e|er|ons|ez)\b[^.!?;]{0,140}\b(?:nouvelle?\s+)?recherche\b",
        r"\brecherche\s+ciblee\b[^.!?;]{0,100}\b(?:lance|lancer|demarre|demarrer)\b",
        r"\bdemande\s+explicitement\b[^.!?;]{0,120}\bennoscholar\b[^.!?;]{0,120}\brecherch\w*\b[^.!?;]{0,80}\bnouvell",
        r"\brecherch\w*\b[^.!?;]{0,90}\bnouvelles?\s+(?:publications?|articles?|sources?)\b",
        r"\b(?:cherch|recherch|trouv)\w*\b[^.!?;]{0,100}\b(?:articles?|publications?|sources?|references?)\b",
        r"\boui\b[^.!?;]{0,60}\blance\b[^.!?;]{0,80}\brecherche\b",
    )
    launch_negations = (
        r"\bne\s+(?:lance|lancer|demarre|demarrer|execute|executer|effectue|effectuer)\s+pas\b",
        r"\bpas\s+de\s+(?:nouvelle?\s+)?recherche\b",
        r"\bsans\s+(?:lancer|demarrer|executer|effectuer|faire)\b[^.!?;]{0,60}\brecherche\b",
        r"\bne\s+veux\s+pas\b[^.!?;]{0,80}\brecherche\b",
        r"\bne\s+(?:cherch|recherch|trouv)\w*\s+pas\b",
        r"\bsans\s+(?:chercher|rechercher|trouver)\b",
    )

    # Important : tester d'abord l'autorisation positive de NOUVELLE recherche.
    # Le prompt peut contenir ensuite ``ne réutilise pas les articles déjà
    # validés`` ; cette contrainte ne doit pas inverser le choix.
    if _has_positive_match(text, launch_patterns, launch_negations):
        return RESEARCH_LAUNCH_TARGETED

    existing_patterns = (
        r"\butilis(?:e|er|ons|ez)\b[^.!?;]{0,120}\bsources?\b[^.!?;]{0,100}\b(?:deja|existantes?|disponibles?|validees?)\b",
        r"\bsources?\b[^.!?;]{0,100}\b(?:deja|existantes?|disponibles?|validees?)\b[^.!?;]{0,80}\b(?:uniquement|seulement)\b",
    )
    existing_negations = (
        r"\bne\s+(?:re)?utilis(?:e|er|ons|ez)\s+pas\b",
        r"\bne\s+te\s+limite\s+pas\b",
        r"\bne\s+pas\s+(?:re)?utilis",
    )
    if _has_positive_match(text, existing_patterns, existing_negations):
        return RESEARCH_USE_EXISTING
    return None


def research_choice_actions(*, existing_sources_available: bool) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    if existing_sources_available:
        actions.append(
            {
                "id": RESEARCH_USE_EXISTING,
                "label": "Utiliser les sources déjà validées",
                "description": (
                    "Utiliser uniquement les références EnnoScholar déjà validées pour le projet, "
                    "sans lancer de nouvelle recherche."
                ),
                "submit_message": "Utiliser uniquement les sources déjà validées, sans nouvelle recherche.",
            }
        )
    actions.append(
        {
            "id": RESEARCH_LAUNCH_TARGETED,
            "label": "Lancer une recherche ciblée",
            "description": (
                "Créer une recherche EnnoScholar ciblée sur cette section. Les nouvelles sources "
                "resteront candidates jusqu'à validation du consultant."
            ),
            "submit_message": "Lancer une recherche ciblée pour cette section.",
        }
    )
    return actions



def _guided_service() -> Any:
    errors: list[str] = []
    for module_name in (
        "services.guided_research_service",
        "backend_api.services.guided_research_service",
    ):
        try:
            return importlib.import_module(module_name)
        except Exception as exc:  # pragma: no cover - dépend du backend réel
            errors.append(f"{module_name}: {exc}")
    raise ImportError(
        "Le service Guided Research d'EnnoScholar est introuvable. "
        + " | ".join(errors)
    )


def _precise_scholar_agent() -> Any:
    """Charge le moteur scientifique principal d'EnnoScholar.

    Important : ce flux n'utilise PAS WebResearchService comme moteur de
    recherche scientifique. Guided Research reste l'interface de session et de
    validation humaine, tandis que la recherche/ranking réutilise exactement le
    coeur EnnoScholar : scientific_intent_builder -> query_builder ->
    paper_ranker -> BGE reranker.
    """

    errors: list[str] = []
    for module_name in (
        "agents.EnnoScholar.scholar_agent",
        "modules.EnnoScholar.scholar_agent",
        "EnnoScholar.scholar_agent",
    ):
        try:
            module = importlib.import_module(module_name)
            cls = getattr(module, "EnnoScholarAgent", None)
            if cls is None:
                continue
            return cls(max_articles_per_verrou=40)
        except Exception as exc:  # pragma: no cover - dépend de l'installation réelle
            errors.append(f"{module_name}: {exc}")
    raise ImportError(
        "Le moteur scientifique principal EnnoScholar est introuvable. "
        "Aucun fallback générique n'est utilisé afin d'éviter de proposer des "
        "sources hors domaine. " + " | ".join(errors)
    )


def _clean(value: Any, limit: int = 12000) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\x00", " ")).strip()[:limit]


def _year_from_value(value: Any) -> int | None:
    if value is None:
        return None
    match = re.search(r"\b(19|20)\d{2}\b", str(value))
    if not match:
        return None
    year = int(match.group(0))
    return year if 1900 <= year <= 2200 else None


def _project_year(project: Any, request: ImprovementRequest) -> int | None:
    """Résout l'année CIR sans la déduire du contenu scientifique de la section."""

    for attr in (
        "year",
        "annee",
        "année",
        "cir_year",
        "exercise_year",
        "fiscal_year",
        "project_year",
    ):
        year = _year_from_value(getattr(project, attr, None))
        if year:
            return year

    for attr in ("metadata_json", "metadata", "extra", "settings"):
        value = getattr(project, attr, None)
        if isinstance(value, dict):
            for key in (
                "year",
                "annee",
                "année",
                "cir_year",
                "exercise_year",
                "fiscal_year",
                "project_year",
            ):
                year = _year_from_value(value.get(key))
                if year:
                    return year

    # Le nom du projet peut contenir explicitement l'exercice CIR (ex. "CIR 2024").
    for value in (
        request.project_name,
        getattr(project, "name", None),
        getattr(project, "title", None),
        getattr(project, "label", None),
    ):
        year = _year_from_value(value)
        if year:
            return year
    return None


def _project_name(project: Any, request: ImprovementRequest) -> str:
    return _clean(
        request.project_name
        or getattr(project, "name", None)
        or getattr(project, "title", None)
        or getattr(project, "label", None)
        or f"project-{getattr(project, 'id', '')}",
        300,
    )


def _organisation(project: Any) -> str:
    for attr in (
        "organisme",
        "organization",
        "organisation",
        "client_name",
        "company_name",
    ):
        value = _clean(getattr(project, attr, None), 300)
        if value:
            return value
    return ""


def _domain_detection(
    project: Any,
    request: ImprovementRequest,
    diagnostic_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Retourne d'abord le vrai domaine NLP/EnnoDiagnostic.

    EnnoAmel ne doit pas reconstruire le domaine à partir du titre de section.
    Le domaine détecté par le NLP est conservé tel quel pour que
    ``scientific_intent_builder`` et ``query_builder`` d'EnnoScholar puissent
    utiliser leurs niveaux, codes et libellés réels. Le domaine du projet n'est
    qu'un fallback quand aucun diagnostic exploitable n'est disponible.
    """

    diagnostic_context = diagnostic_context or {}
    detected = diagnostic_context.get("domain_detection")
    if isinstance(detected, dict) and detected:
        out = dict(detected)
        out.setdefault("source", "EnnoDiagnostic_NLP")
        return out

    domain = _clean(
        request.project_domain
        or getattr(project, "domain", None)
        or getattr(project, "project_domain", None),
        500,
    )
    if not domain:
        return {}
    return {
        "display_label": domain,
        "main_domain_label": domain,
        "source": "project_domain_fallback",
    }


_TOKEN_STOP = {
    "avec", "dans", "pour", "sans", "entre", "vers", "sous", "chez",
    "cette", "ces", "des", "les", "une", "plus", "moins", "ainsi",
    "section", "projet", "travaux", "scientifique", "technique", "cir",
    "donnees", "données", "modele", "modèle", "modeles", "modèles",
    "resultat", "résultat", "resultats", "résultats", "methode", "méthode",
    "methodes", "méthodes", "probleme", "problème", "limite", "limites",
    "ameliorer", "améliorer", "renforcer", "recherche", "publication",
    "publications", "source", "sources", "preuve", "preuves", "pertinent",
    "pertinente", "pertinentes", "nouvelle", "nouvelles",
}


def _tokens(value: Any) -> set[str]:
    original = str(value or "")
    raw = _norm(original)
    out: set[str] = set()
    for token in re.findall(r"\b[a-z0-9][a-z0-9+./_-]{2,}\b", raw):
        if token in _TOKEN_STOP:
            continue
        if len(token) >= 4 or any(ch.isdigit() for ch in token):
            out.add(token)
    # Les sigles techniques courts (SAR, ATR, CFD, FEM...) sont très
    # discriminants et doivent compter dans le rapprochement Diagnostic/section.
    for acronym in re.findall(r"\b[A-Z][A-Z0-9]{2,}(?:[-_/][A-Z0-9]+)?\b", original):
        normalized = _norm(acronym)
        if normalized and normalized not in {"cir", "r&d", "rnd", "api", "pdf"}:
            out.add(normalized)
    return out


def _research_target_text(request: ImprovementRequest) -> str:
    """Contexte local utilisé uniquement pour choisir le verrou Diagnostic.

    Le texte source reste la base principale. L'instruction consultant ajoute
    son objectif (ex. généralisation, biais, représentativité) sans remplacer
    les faits du dossier.
    """

    return _clean(
        "\n".join(
            part
            for part in (
                request.target_section_title or "",
                request.target_text or "",
                request.instruction or "",
            )
            if str(part or "").strip()
        ),
        22000,
    )


def _diagnostic_relevance(
    target_text: str,
    item: dict[str, Any],
    *,
    document_frequency: Counter[str] | None = None,
    corpus_size: int = 1,
) -> tuple[float, int, int]:
    """Score local pondéré pour relier une section à son vrai verrou.

    - le titre du verrou compte davantage que sa justification longue ;
    - les termes rares parmi les verrous sont favorisés (IDF), afin que des
      mots génériques comme ``radar`` ou ``simulation`` n'embarquent pas un
      verrou voisin ;
    - les acronymes/identifiants techniques présents dans la section gardent
      un poids fort sans aucun vocabulaire métier codé en dur.
    """

    target = _tokens(target_text)
    title_tokens = _tokens(item.get("title"))
    body_tokens = _tokens(
        " ".join(
            str(item.get(key) or "")
            for key in ("text", "justification", "evidence_text")
        )
    )
    candidate = title_tokens | body_tokens
    if not target or not candidate:
        return 0.0, 0, 0

    df = document_frequency or Counter()
    title_common = target & title_tokens
    body_common = target & body_tokens

    def weight(token: str) -> float:
        # +1 évite division par zéro. Les termes présents dans tous les verrous
        # reçoivent peu de poids ; les termes discriminants en reçoivent plus.
        idf = math.log((max(1, corpus_size) + 1.0) / (df.get(token, 0) + 1.0)) + 1.0
        # Un token court gardé par _tokens est généralement un acronyme ou un
        # identifiant technique ; il est donc naturellement discriminant.
        technical_bonus = 1.55 if len(token) <= 5 or any(ch.isdigit() for ch in token) else 1.0
        return idf * technical_bonus

    title_score = sum(weight(token) for token in title_common) * 3.0
    body_only = body_common - title_common
    body_score = sum(weight(token) for token in body_only) * 0.9
    matched_weight = title_score + body_score

    # Normalisation douce : on favorise la précision sans pénaliser un verrou
    # dont la justification est longue.
    denominator = max(
        4.0,
        math.sqrt(max(1, len(target)) * max(1, len(title_tokens) + min(len(body_tokens), 30))),
    )
    score = matched_weight / denominator
    return round(score, 6), len(title_common | body_common), len(title_common)


def _matched_diagnostic_context(
    db: Any,
    project: Any,
    request: ImprovementRequest,
    *,
    diagnostic_override: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
    """Sélectionne les verrous réellement liés à la section cible.

    V2.1 : on ne transmet plus les quatre meilleurs verrous par simple
    recouvrement lexical. Le meilleur verrou est toujours le primaire ; un
    verrou secondaire n'est gardé que s'il reste proche du meilleur score et
    partage lui aussi des éléments discriminants avec la section.
    """

    if isinstance(diagnostic_override, dict) and diagnostic_override.get("available"):
        diagnostic = diagnostic_override
    else:
        diagnostic, _ = ensure_diagnostic_context(db, project, request)

    lock_items = [
        dict(item)
        for item in (diagnostic.get("evidence_items") or [])
        if isinstance(item, dict) and item.get("type") == "diagnostic_lock"
    ]

    candidate_token_sets = [
        _tokens(
            " ".join(
                str(item.get(key) or "")
                for key in ("title", "text", "justification", "evidence_text")
            )
        )
        for item in lock_items
    ]
    document_frequency: Counter[str] = Counter()
    for token_set in candidate_token_sets:
        document_frequency.update(token_set)

    target_text = _research_target_text(request)
    rows: list[tuple[float, int, int, dict[str, Any]]] = []
    for item in lock_items:
        score, hits, title_hits = _diagnostic_relevance(
            target_text,
            item,
            document_frequency=document_frequency,
            corpus_size=max(1, len(lock_items)),
        )
        if hits:
            rows.append((score, hits, title_hits, item))

    rows.sort(key=lambda value: (value[0], value[2], value[1]), reverse=True)

    matched_items: list[dict[str, Any]] = []
    if rows:
        best_score = rows[0][0]
        # Le primaire est toujours le meilleur verrou local.
        matched_items.append(dict(rows[0][3]))

        # Au plus un secondaire : il doit rester clairement lié au même besoin,
        # et non seulement partager un mot de domaine très général.
        for score, hits, title_hits, item in rows[1:]:
            if len(matched_items) >= 2:
                break
            relative = score / best_score if best_score > 0 else 0.0
            if relative >= 0.50 and (title_hits >= 1 or hits >= 3):
                matched_items.append(dict(item))

    target_verrous: list[str] = []
    for item in matched_items:
        evidence_id = str(item.get("evidence_id") or "")
        match = re.search(r"D:verrou:([^\s]+)$", evidence_id)
        if match and match.group(1) not in target_verrous:
            target_verrous.append(match.group(1))

    context_text = _clean(
        "\n".join(
            f"{item.get('title', '')}: {item.get('text', '')}"
            for item in matched_items
        ),
        9000,
    )
    context = {
        "diagnostic_context_text": context_text,
        "matched_verrou_ids": target_verrous,
        "matched_evidence_count": len(matched_items),
        "domain_detection": (
            dict(diagnostic.get("domain_detection"))
            if isinstance(diagnostic.get("domain_detection"), dict)
            else {}
        ),
        "selection_policy": "primary_scoped_lock_plus_one_close_secondary_v2_3",
        "source": "EnnoDiagnostic_scoped_current_cir_match",
    }
    return context, target_verrous, matched_items


def _verrou_id_from_item(item: dict[str, Any], fallback: str) -> str:
    evidence_id = str(item.get("evidence_id") or "")
    match = re.search(r"D:verrou:([^\s]+)$", evidence_id)
    return str(match.group(1) if match else fallback)


def _research_verrous(
    request: ImprovementRequest,
    diagnostic_context: dict[str, Any],
    matched_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Construit un verrou EnnoScholar par verrou Diagnostic sélectionné.

    Le titre scientifique vient du verrou EnnoDiagnostic, jamais du simple
    intitulé de section. Le texte de section est conservé comme passage source
    local afin qu'EnnoScholar retrouve les noms, méthodes et contraintes
    réellement présents dans le dossier.
    """

    section_title = _clean(
        request.target_section_title
        or request.target_section_id
        or "Section scientifique à étayer",
        400,
    )
    source_text = _clean(request.target_text, 14000)

    if not matched_items:
        # V2.2 : aucun fallback pseudo-scientifique basé sur le titre de section.
        # Si EnnoDiagnostic n'a pas établi de verrou relié, EnnoAmel doit le dire
        # au lieu de lancer une recherche large susceptible de dériver hors domaine.
        raise RuntimeError(
            "Aucun verrou EnnoDiagnostic suffisamment relié à cette section n'a été identifié. "
            "La recherche EnnoScholar est arrêtée pour éviter de construire une requête à partir "
            "du seul titre de section."
        )

    verrous: list[dict[str, Any]] = []
    for index, item in enumerate(matched_items):
        verrou_id = _verrou_id_from_item(
            item,
            str(request.target_section_id or f"ennoamel_lock_{index + 1}"),
        )
        verrou_title = _clean(item.get("title"), 500) or section_title
        diagnostic_text = _clean(
            item.get("text") or item.get("evidence_text") or item.get("justification"),
            7000,
        )
        supporting = [
            {"text": diagnostic_text}
        ] if diagnostic_text else []
        verrous.append(
            {
                "verrou_id": verrou_id,
                "title": verrou_title,
                "original_title": verrou_title,
                # Le verrou Diagnostic est le problème scientifique ; la section
                # apporte les preuves/local names utiles à l'intention.
                "text": _clean(
                    "\n".join(
                        part
                        for part in (
                            verrou_title,
                            diagnostic_text,
                            source_text,
                        )
                        if part
                    ),
                    18000,
                ),
                "raw_item": {
                    "text": diagnostic_text or source_text,
                    "source_text": source_text,
                    "original_title": verrou_title,
                    "supporting_passages": supporting,
                    "source_section_title": section_title,
                    "consultant_instruction": _clean(request.instruction, 3500),
                },
                "sources": [
                    {"excerpt": row["text"]}
                    for row in supporting
                    if row.get("text")
                ],
                "context": {
                    "section_title": section_title,
                    "section_id": request.target_section_id,
                },
                "diagnostic_context": diagnostic_context,
                "source_json": {
                    "evidence_summary": diagnostic_text,
                    "matched_verrou_ids": [verrou_id],
                    "source_section_title": section_title,
                },
            }
        )
    return verrous

def _existing_decided_article_keys(db: Any, project: Any) -> set[str]:
    """Évite de présenter comme nouvelles les sources déjà gardées/rejetées."""

    keys: set[str] = set()
    try:
        from db.models import Article, ScholarRun

        rows = (
            db.query(Article)
            .join(ScholarRun, Article.scholar_run_id == ScholarRun.id)
            .filter(ScholarRun.project_id == project.id)
            .all()
        )
    except Exception:
        return keys

    decided = {
        "garde", "gardé", "gardee", "gardée", "accepted", "accept",
        "rejete", "rejeté", "rejetee", "rejetée", "rejected", "reject",
    }
    for row in rows:
        status = _norm(getattr(row, "consultant_status", None))
        if status not in decided:
            continue
        doi = _norm(getattr(row, "doi", None))
        title = _norm(getattr(row, "title", None))
        if doi:
            keys.add("doi:" + doi.removeprefix("https://doi.org/"))
        if title:
            keys.add("title:" + title)
    return keys


def _article_key(article: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    doi = _norm(article.get("doi"))
    if doi:
        keys.add("doi:" + doi.removeprefix("https://doi.org/"))
    title = _norm(article.get("title"))
    if title:
        keys.add("title:" + title)
    return keys


def _authors(value: Any) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[,;]", value) if part.strip()][:20]
    out: list[str] = []
    for item in value or []:
        if isinstance(item, dict):
            text = _clean(item.get("name") or item.get("author") or item.get("full_name"), 200)
        else:
            text = _clean(item, 200)
        if text and text not in out:
            out.append(text)
    return out[:20]


def _stable_candidate_id(article: dict[str, Any]) -> str:
    import hashlib

    basis = "|".join(
        [
            _norm(article.get("doi")),
            _norm(article.get("title")),
            str(article.get("year") or ""),
        ]
    )
    digest = hashlib.sha1(basis.encode("utf-8", errors="ignore")).hexdigest()[:18]
    return f"ENS-{digest}"


def _article_url(article: dict[str, Any]) -> str | None:
    for key in (
        "url",
        "source_url",
        "landing_url",
        "pdf_url",
        "open_access_pdf_url",
        "primary_pdf_url",
    ):
        value = _clean(article.get(key), 2000)
        if value.startswith("http"):
            return value
    value = article.get("open_access_pdf") or article.get("openAccessPdf")
    if isinstance(value, dict):
        url = _clean(value.get("url"), 2000)
        if url.startswith("http"):
            return url
    doi = _clean(article.get("doi"), 300)
    if doi:
        doi = doi.removeprefix("https://doi.org/")
        return "https://doi.org/" + doi
    return None


def _providers(article: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for value in (
        article.get("source"),
        article.get("provider"),
        article.get("source_name"),
    ):
        text = _clean(value, 120)
        if text and text not in out:
            out.append(text)
    for row in article.get("retrieval") or []:
        if isinstance(row, dict):
            text = _clean(row.get("provider") or row.get("source"), 120)
            if text and text not in out:
                out.append(text)
    return out[:8]


def _candidate_from_article(
    article: dict[str, Any],
    *,
    request: ImprovementRequest,
    target_verrous: list[str],
    research_target_ids: list[str] | None = None,
    research_target_type: str | None = None,
) -> dict[str, Any]:
    tag = str(article.get("tag") or "").strip()
    relevance_role = {
        "Direct": "direct_evidence",
        "Connexe": "connected_evidence",
        "Fondamental": "connected_evidence",
    }.get(tag, "connected_evidence")
    score = article.get("relevance_score")
    try:
        score = float(score)
    except (TypeError, ValueError):
        score = None

    # fulltext_candidate_v2
    fulltext_candidate = bool(
        _clean(
            article.get("pdf_url")
            or article.get("open_access_pdf_url")
            or article.get("primary_pdf_url")
            or article.get("fulltext_url")
            or article.get("full_text_url"),
            2000,
        )
        or article.get("free_fulltext_available") is True
        or article.get("fulltext_available") is True
        or _clean(article.get("full_text"), 20)
    )
    evidence_access_status = (
        "FULLTEXT_CANDIDATE"
        if fulltext_candidate
        else "ABSTRACT_ONLY"
        if _clean(
            article.get("abstract")
            or article.get("summary")
            or article.get("tldr"),
            50,
        )
        else "METADATA_ONLY"
    )

    return {
        "candidate_id": _stable_candidate_id(article),
        "candidate_kind": "scientific_article",
        "title": _clean(article.get("title"), 700),
        "authors": _authors(article.get("authors")),
        "year": _year_from_value(article.get("year") or article.get("publication_year")),
        "doi": _clean(article.get("doi"), 300) or None,
        "url": _article_url(article),
        "pdf_url": _clean(article.get("pdf_url") or article.get("open_access_pdf_url"), 2000) or None,
        "abstract": _clean(
            article.get("abstract") or article.get("summary") or article.get("tldr"),
            6000,
        ) or None,
        "venue": _clean(article.get("venue") or article.get("journal"), 500) or None,
        "source_providers": _providers(article),
        "scientific_evidence_eligible": fulltext_candidate,
        "context_evidence_eligible": True,
        "evidence_access_status": evidence_access_status,
        "requires_fulltext_verification": True,
        "abstract_only_is_not_complete_evidence": True,
        "evidence_scope": ["limitation", "comparison", "method", "result"],
        "section_ids": [request.target_section_id] if request.target_section_id else [],
        "section_titles": [request.target_section_title] if request.target_section_title else [],
        "target_verrous": list(target_verrous),
        "research_target_ids": list(research_target_ids or []),
        "research_target_type": research_target_type or request.research_target_type,
        "requested_dimensions": [
            "arguments scientifiques directement liés à la section",
            "méthodes, résultats, limites ou comparaisons pertinents selon la cible",
            "conditions de validité ou de transférabilité si elles sont documentées",
        ],
        "relevance_score": score,
        "tag": tag or None,
        "relevance_role": relevance_role,
        "role_reason": _clean(
            article.get("reason")
            or article.get("relevance_reason")
            or article.get("why_relevant"),
            1800,
        ) or None,
        "consultant_decision": "proposed",
        "research_engine": "ennoscholar_core",
        "raw_payloads": [dict(article)],
    }


def _extract_precise_candidates(
    report: dict[str, Any],
    *,
    request: ImprovementRequest,
    target_verrous: list[str],
    excluded_keys: set[str],
    project_year: int | None,
    research_target_ids: list[str] | None = None,
    limit: int = 15,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fusionne les résultats EnnoScholar sans perdre le verrou d'origine."""

    articles: list[dict[str, Any]] = []
    result_rows = [row for row in (report.get("results") or []) if isinstance(row, dict)]
    for result in result_rows:
        result_verrou_id = str(result.get("verrou_id") or "").strip()
        result_research_target_id = str(result.get("research_target_id") or "").strip()
        result_research_target_type = str(result.get("research_target_type") or "").strip()
        for article in result.get("articles") or []:
            if isinstance(article, dict) and article.get("title"):
                row = dict(article)
                row["_result_verrou_id"] = result_verrou_id
                row["_result_research_target_id"] = result_research_target_id
                row["_result_research_target_type"] = result_research_target_type
                articles.append(row)

    tag_order = {"Direct": 0, "Connexe": 1, "Fondamental": 2, "Hors sujet": 9}
    articles.sort(
        key=lambda row: (
            tag_order.get(str(row.get("tag") or ""), 5),
            -float(row.get("relevance_score") or 0.0),
        )
    )

    candidates: list[dict[str, Any]] = []
    candidate_by_stable: dict[str, dict[str, Any]] = {}
    removed_existing = 0
    removed_future = 0
    removed_offtopic = 0

    for article in articles:
        tag = str(article.get("tag") or "").strip()
        if tag == "Hors sujet":
            removed_offtopic += 1
            continue
        if tag not in {"Direct", "Connexe", "Fondamental"}:
            continue

        year = _year_from_value(article.get("year") or article.get("publication_year"))
        if project_year is not None and year is not None and year > project_year:
            removed_future += 1
            continue

        keys = _article_key(article)
        if keys & excluded_keys:
            removed_existing += 1
            continue

        stable = next(iter(sorted(keys)), _stable_candidate_id(article))
        result_verrou_id = str(article.get("_result_verrou_id") or "").strip()
        result_research_target_id = str(
            article.get("_result_research_target_id") or ""
        ).strip()
        result_research_target_type = str(
            article.get("_result_research_target_type") or ""
        ).strip()

        if stable in candidate_by_stable:
            existing = candidate_by_stable[stable]
            if result_verrou_id and result_verrou_id not in existing["target_verrous"]:
                existing["target_verrous"].append(result_verrou_id)
            if (
                result_research_target_id
                and result_research_target_id not in existing["research_target_ids"]
            ):
                existing["research_target_ids"].append(result_research_target_id)
            existing.setdefault("raw_payloads", []).append(
                {
                    key: value
                    for key, value in article.items()
                    if not key.startswith("_result_")
                }
            )
            continue

        article_target_verrous = [result_verrou_id] if result_verrou_id else list(target_verrous)
        article_research_targets = (
            [result_research_target_id]
            if result_research_target_id
            else list(research_target_ids or [])
        )
        candidate = _candidate_from_article(
            {
                key: value
                for key, value in article.items()
                if not key.startswith("_result_")
            },
            request=request,
            target_verrous=article_target_verrous,
            research_target_ids=article_research_targets,
            research_target_type=result_research_target_type or request.research_target_type,
        )
        candidate_by_stable[stable] = candidate
        candidates.append(candidate)
        if len(candidates) >= limit:
            break

    all_queries: list[Any] = []
    all_generated: list[Any] = []
    intents: list[dict[str, Any]] = []
    search_statuses: list[dict[str, Any]] = []
    rerankings: list[dict[str, Any]] = []
    precision_counts: Counter[str] = Counter()

    for result in result_rows:
        all_queries.extend(result.get("queries") or [])
        all_generated.extend(result.get("queries_generated") or [])
        intent = result.get("scientific_intent")
        if isinstance(intent, dict) and intent:
            intents.append(dict(intent))
        status = result.get("search_status")
        if isinstance(status, dict):
            search_statuses.append(dict(status))
            precision_counts.update(status.get("precision_tag_counts") or {})
        reranking = result.get("reranking")
        if isinstance(reranking, dict):
            rerankings.append(dict(reranking))

    def _dedupe_jsonish(rows: list[Any]) -> list[Any]:
        output: list[Any] = []
        seen: set[str] = set()
        for row in rows:
            key = repr(row)
            if key in seen:
                continue
            seen.add(key)
            output.append(row)
        return output

    metadata = {
        "queries": _dedupe_jsonish(all_queries),
        "queries_generated": _dedupe_jsonish(all_generated),
        "scientific_intent": intents[0] if intents else {},
        "scientific_intents": intents,
        "search_status": search_statuses[0] if len(search_statuses) == 1 else {
            "per_verrou": search_statuses
        },
        "reranking": rerankings[0] if len(rerankings) == 1 else {
            "per_verrou": rerankings
        },
        "precision_tag_counts": dict(precision_counts),
        "removed_existing_decided": removed_existing,
        "removed_after_project_year": removed_future,
        "removed_offtopic": removed_offtopic,
        "verrous_searched": [
            {
                "verrou_id": result.get("verrou_id"),
                "verrou_title": (
                    (result.get("scientific_intent") or {}).get("verrou_title")
                    if isinstance(result.get("scientific_intent"), dict)
                    else None
                ),
            }
            for result in result_rows
            if result.get("verrou_id")
        ],
        "research_targets_searched": [
            {
                "research_target_id": result.get("research_target_id"),
                "research_target_title": result.get("research_target_title"),
                "research_target_type": result.get("research_target_type"),
            }
            for result in result_rows
            if result.get("research_target_id")
        ],
    }
    return candidates, metadata

def _research_prompt(request: ImprovementRequest) -> str:
    title = str(request.target_section_title or request.target_section_id or "section ciblée").strip()
    target_excerpt = str(request.target_text or "").strip()
    if len(target_excerpt) > 12000:
        target_excerpt = target_excerpt[:12000]
    return (
        "Recherche scientifique ciblée demandée depuis EnnoAmel pour enrichir une section.\n\n"
        f"Section cible : {title}\n\n"
        f"Type de cible : {request.research_target_type or 'scientific_enrichment'}\n\n"
        "Instruction du consultant :\n"
        f"{request.instruction.strip()}\n\n"
        "Texte de la section à étayer :\n"
        f"{target_excerpt}\n\n"
        "Objectif strict : rechercher de nouvelles publications pertinentes qui étayent "
        "directement les arguments, méthodes, comparaisons, résultats ou limites utiles "
        "à la fonction de cette section. La cible n'est un verrou que si elle a été "
        "explicitement qualifiée comme telle. "
        "Ne pas rédiger la section et ne pas inventer de fait projet."
    )


def _persist_precise_candidates(
    service: Any,
    db: Any,
    session_id: str,
    *,
    candidates: list[dict[str, Any]],
    research_metadata: dict[str, Any],
    request: ImprovementRequest,
    target_verrous: list[str],
    project_year: int | None,
    research_target_ids: list[str] | None = None,
) -> str:
    agent = service.get_guided_research_agent()
    snapshot = agent.repository.snapshot(db, session_id)
    existing = list(snapshot.get("selected_sources") or [])
    decided_sources = [
        dict(row)
        for row in existing
        if str(row.get("consultant_decision") or "") in {"accepted", "rejected"}
    ]
    from datetime import datetime, timezone

    batch_id = "BATCH-ENS-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    current = [
        {
            **dict(row),
            "research_batch_id": batch_id,
            "current_research_batch": True,
        }
        for row in candidates
    ]
    research_plan = {
        "ok": True,
        "engine": "ennoscholar_core",
        "engine_contract": (
            "scientific_intent_builder -> query_builder -> paper_ranker -> BGE reranker"
        ),
        "source": "ennoamel_targeted_research",
        "section_id": request.target_section_id,
        "section_title": request.target_section_title,
        "target_verrous": list(target_verrous),
        "research_target_ids": list(research_target_ids or []),
        "research_target_type": request.research_target_type,
        "subject_contract": (
            "diagnostic_locks" if target_verrous else "typed_research_targets"
        ),
        "project_year_cutoff": project_year,
        "candidate_count": len(current),
        "research_batch_id": batch_id,
        **research_metadata,
    }
    state = "waiting_consultant_feedback" if current else "research_refinement"
    agent.repository.update(
        db,
        session_id,
        research_plan=research_plan,
        selected_sources=[*decided_sources, *current],
        state=state,
        ready_to_write=False,
        context_updates={
            "external_research_started": True,
            "research_engine": "ennoscholar_core",
            "generic_web_research_bypassed": True,
            "last_research_at": datetime.now(timezone.utc).isoformat(),
            "current_research_batch_id": batch_id,
            "current_candidate_ids": [
                str(row.get("candidate_id") or "")
                for row in current
                if row.get("candidate_id")
            ],
        },
    )
    return state


def launch_targeted_guided_research(
    db: Any,
    project: Any,
    request: ImprovementRequest,
    *,
    diagnostic_package: dict[str, Any] | None = None,
    diagnostic_orchestration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Lance une vraie recherche EnnoScholar précise, puis expose les candidats.

    Architecture V2.3 :
      EnnoAmel -> Guided Research (session/validation humaine)
               -> EnnoScholarAgent.run_search (moteur scientifique principal)
               -> candidats Direct/Connexe/Fondamental
               -> Guided Research Sources pour validation.

    Aucun fallback vers le moteur WebResearchService générique n'est autorisé pour
    les publications scientifiques : une erreur du coeur EnnoScholar est remontée
    explicitement plutôt que de proposer des sources hors domaine.
    """

    service = _guided_service()
    prompt = _research_prompt(request)
    session = service.create_guided_research_session(
        db,
        project,
        user_id=None,
        target_mode="section_improvement",
        entry_module="ennoamel",
    )
    session_id = str(session.get("session_id") or "").strip()
    if not session_id:
        raise RuntimeError("EnnoScholar n'a pas retourné d'identifiant de session Guided Research.")

    project_year = _project_year(project, request)
    diagnostic_ctx: dict[str, Any] = {}
    target_verrous: list[str] = []
    matched_items: list[dict[str, Any]] = []
    research_target_ids: list[str] = []
    direct_context = build_lightweight_research_context(project, request)
    target_type = str(
        direct_context.get("research_target_type") or "scientific_enrichment"
    )
    search_strategy = dict(direct_context.get("search_strategy") or {})
    diagnostic_policy = str(
        search_strategy.get("diagnostic_policy") or "not_required"
    )
    diagnostic_available = bool(
        isinstance(diagnostic_package, dict)
        and diagnostic_package.get("available")
    )
    diagnostic_required = bool(
        diagnostic_available and diagnostic_policy == "use_when_available"
    )
    diagnostic_fallback_reason = ""

    if diagnostic_required:
        diagnostic_ctx, target_verrous, matched_items = _matched_diagnostic_context(
            db,
            project,
            request,
            diagnostic_override=diagnostic_package,
        )
        if not matched_items or not target_verrous:
            raise RuntimeError(
                "EnnoDiagnostic était requis, mais aucun verrou suffisamment relié "
                "à la section n'a été confirmé."
            )
        verrous = _research_verrous(request, diagnostic_ctx, matched_items)
        domain_detection = _domain_detection(project, request, diagnostic_ctx)
        subject_payload: dict[str, Any] = {
            "diagnostic_context": diagnostic_ctx,
            "verrous": verrous,
        }
        research_mode = "diagnostic_lock_research"
    else:
        readiness = dict(direct_context.get("search_readiness") or {})
        if not readiness.get("ready"):
            raise RuntimeError(
                "La section ne contient pas assez d'ancres techniques locales pour "
                "construire une recherche scientifique ciblee. Precisez l'objet, le "
                "phenomene, la methode ou la difficulte a documenter."
            )
        domain_detection = dict(direct_context.get("domain_detection") or {})
        research_target_ids = list(direct_context.get("research_target_ids") or [])
        subject_payload = {
            "research_context": direct_context.get("research_context") or {},
            "research_targets": direct_context.get("research_targets") or [],
        }
        research_mode = "direct_typed_research_target"

    scholar = _precise_scholar_agent()
    payload = {
        "project": _project_name(project, request),
        "organisme": _organisation(project),
        "year": project_year or "",
        "domain_detection": domain_detection,
        **subject_payload,
        # Une commande explicite "nouvelle recherche" ne doit pas être satisfaite
        # par le cache global d'un ancien run.
        "force_refresh": True,
    }
    report = scholar.run_search(payload)
    excluded_keys = _existing_decided_article_keys(db, project)
    candidates, search_meta = _extract_precise_candidates(
        report,
        request=request,
        target_verrous=target_verrous,
        excluded_keys=excluded_keys,
        project_year=project_year,
        research_target_ids=research_target_ids,
        limit=15,
    )
    search_meta.update(
        {
            "matched_diagnostic_evidence": [
                {
                    "evidence_id": row.get("evidence_id"),
                    "title": row.get("title"),
                }
                for row in matched_items
            ],
            "research_context": {
                "mode": research_mode,
                "domain_detection": domain_detection,
                "primary_verrou": (
                    {
                        "id": target_verrous[0] if target_verrous else None,
                        "title": matched_items[0].get("title") if matched_items else None,
                    }
                ),
                "secondary_verrous": [
                    {
                        "id": target_verrous[index] if index < len(target_verrous) else None,
                        "title": row.get("title"),
                    }
                    for index, row in enumerate(matched_items[1:], start=1)
                ],
                "source_section_id": request.target_section_id,
                "source_section_title": request.target_section_title,
                "project_year": project_year,
                "diagnostic_run_id": (diagnostic_package or {}).get("diagnostic_run_id"),
                "diagnostic_orchestration": dict(diagnostic_orchestration or {}),
                "research_target_ids": research_target_ids,
                "research_target_type": direct_context.get("research_target_type"),
                "lightweight_context": direct_context.get("research_context") or {},
                "diagnostic_required": diagnostic_required,
                "diagnostic_available": diagnostic_available,
                "diagnostic_policy": diagnostic_policy,
                "diagnostic_fallback_reason": diagnostic_fallback_reason,
                "search_strategy": search_strategy,
                "search_readiness": direct_context.get("search_readiness") or {},
            },
            "force_refresh": True,
            "excluded_existing_decided_count": len(excluded_keys),
            "ennoscholar_version": report.get("version"),
            "search_elapsed_seconds": report.get("search_elapsed_seconds"),
        }
    )
    state = _persist_precise_candidates(
        service,
        db,
        session_id,
        candidates=candidates,
        research_metadata=search_meta,
        request=request,
        target_verrous=target_verrous,
        project_year=project_year,
        research_target_ids=research_target_ids,
    )

    return {
        "ok": True,
        "mode": RESEARCH_LAUNCH_TARGETED,
        "engine": "ennoscholar_core",
        "generic_web_research_bypassed": True,
        "session_id": session_id,
        "state": state,
        "assistant_message": (
            f"La recherche scientifique précise EnnoScholar a proposé {len(candidates)} "
            "source(s) candidate(s)."
        ),
        "next_action": "review_sources" if candidates else "refine_research",
        "candidate_count": len(candidates),
        "candidates": candidates,
        "queries": search_meta.get("queries") or [],
        "scientific_intent": search_meta.get("scientific_intent") or {},
        "scientific_intents": search_meta.get("scientific_intents") or [],
        "research_context": search_meta.get("research_context") or {},
        "project_year_cutoff": project_year,
        "target_verrous": target_verrous,
        "research_target_ids": research_target_ids,
        "research_mode": research_mode,
        "diagnostic_required": diagnostic_required,
        "research_metadata": search_meta,
    }


def format_research_candidates_message(research: dict[str, Any]) -> str:
    candidates = list(research.get("candidates") or [])
    if not candidates:
        return (
            "La recherche scientifique EnnoScholar a bien été exécutée avec son moteur de "
            "précision, mais aucun nouveau candidat suffisamment pertinent n'a été retenu. "
            "Le texte reste inchangé. Vous pouvez reformuler le besoin ou utiliser les sources "
            "déjà validées."
        )

    year_note = (
        f" Les publications postérieures à {research.get('project_year_cutoff')} sont exclues."
        if research.get("project_year_cutoff")
        else ""
    )
    lines = [
        (
            f"La recherche ciblée EnnoScholar a proposé {len(candidates)} source(s) "
            "candidate(s) avec le moteur scientifique principal."
            + year_note
        ),
    ]

    # Traçabilité lisible : le consultant peut vérifier immédiatement que la
    # recherche est bien partie du bon domaine et du bon verrou avant d'ouvrir
    # les articles.
    context = research.get("research_context") or {}
    domain = context.get("domain_detection") if isinstance(context, dict) else {}
    primary = context.get("primary_verrou") if isinstance(context, dict) else {}
    secondary = context.get("secondary_verrous") if isinstance(context, dict) else []
    if isinstance(domain, dict) and domain:
        domain_label = (
            domain.get("sub_domain_label")
            or domain.get("domain_label_niv3")
            or domain.get("main_domain_label")
            or domain.get("display_label")
        )
        if domain_label:
            lines.append(f"Domaine de recherche : {domain_label}")
    if isinstance(primary, dict) and primary.get("title"):
        lines.append(f"Verrou principal : {primary.get('title')}")
    secondary_titles = [
        str(row.get("title"))
        for row in (secondary or [])
        if isinstance(row, dict) and row.get("title")
    ]
    if secondary_titles:
        lines.append("Verrou secondaire : " + " ; ".join(secondary_titles[:2]))

    lines.extend([
        "Consultez-les dans l'onglet Sources, puis gardez uniquement celles qui doivent étayer la révision.",
        "",
    ])
    for index, row in enumerate(candidates[:10], start=1):
        year = f" ({row.get('year')})" if row.get("year") else ""
        tag = f" · {row.get('tag')}" if row.get("tag") else ""
        score = (
            f" · score={float(row.get('relevance_score')):.3f}"
            if row.get("relevance_score") is not None
            else ""
        )
        lines.append(f"{index}. {row.get('title')}{year}{tag}{score}")
    lines.extend(
        [
            "",
            "Ces sources restent candidates : aucune ne sera utilisée dans la rédaction avant validation du consultant.",
        ]
    )
    return "\n".join(lines)
