from __future__ import annotations

import json
import math
import re
import unicodedata
from typing import Any, Iterable

from modules.LLM.llm_client import LLMClient

POLICY_VERSION = "ennoamel_cir_auto_evidence_v3_20"

_WORD_RE = re.compile(r"\b[a-zA-ZÀ-ÿ0-9][\wÀ-ÿ'-]{2,}\b")
_ID_KEYS = (
    "article_id",
    "id",
    "articleId",
    "scholar_article_id",
)
_TITLE_KEYS = ("title", "paper_title", "name")
_ABSTRACT_KEYS = (
    "abstract",
    "evidence_text",
    "snippet",
    "summary",
    "description",
    "tldr",
)
_SCORE_KEYS = (
    "score",
    "relevance_score",
    "rerank_score",
    "semantic_score",
    "similarity",
)
_TAG_KEYS = (
    "tag",
    "relevance_tag",
    "relation",
    "category",
    "classification",
)
_YEAR_KEYS = ("year", "publication_year", "published_year")
_PROVIDER_KEYS = ("provider", "source", "origin")


def _clean(value: Any, limit: int = 12000) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip()


def _first(mapping: dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _article_id(source: dict[str, Any]) -> int | None:
    raw = _first(source, _ID_KEYS)
    if isinstance(raw, bool):
        return None
    try:
        value = int(raw)
    except Exception:
        return None
    return value if value > 0 else None


def _float(value: Any) -> float:
    try:
        score = float(value)
    except Exception:
        return 0.0
    if math.isnan(score) or math.isinf(score):
        return 0.0
    return score


def _year(source: dict[str, Any]) -> int | None:
    raw = _first(source, _YEAR_KEYS)
    if raw in (None, ""):
        return None
    match = re.search(r"\b(19|20)\d{2}\b", str(raw))
    return int(match.group(0)) if match else None


def normalize_source(source: dict[str, Any], ordinal: int) -> dict[str, Any]:
    source = dict(source or {})
    article_id = _article_id(source)
    title = _clean(_first(source, _TITLE_KEYS), 1000)
    abstract = _clean(_first(source, _ABSTRACT_KEYS), 9000)
    score = _float(_first(source, _SCORE_KEYS))
    tag = _clean(_first(source, _TAG_KEYS), 100)
    provider = _clean(_first(source, _PROVIDER_KEYS), 100)
    year = _year(source)
    source_candidate_id = _clean(source.get("candidate_id"), 200)

    return {
        # L'identifiant Guided Research existe avant l'Article DB. Il est donc
        # la clé correcte pour accepter automatiquement une publication et
        # déclencher ensuite son extraction plein texte.
        "candidate_id": source_candidate_id or f"C{ordinal}",
        "article_id": article_id,
        "title": title,
        "year": year,
        "tag": tag,
        "score": score,
        "provider": provider,
        "abstract_or_snippet": abstract,
    }


def _tokens(text: str) -> set[str]:
    ignored = {
        "avec", "dans", "pour", "sans", "plus", "cette", "comme", "ainsi",
        "nous", "leur", "leurs", "entre", "des", "les", "une", "sur", "par",
        "the", "and", "for", "with", "from", "this", "that", "using", "based",
    }
    return {
        token
        for token in (_norm(value) for value in _WORD_RE.findall(str(text or "")))
        if len(token) >= 3 and token not in ignored
    }


def _lexical_support(section_text: str, source: dict[str, Any]) -> float:
    left = _tokens(section_text)
    right = _tokens(
        f"{source.get('title', '')} {source.get('abstract_or_snippet', '')}"
    )
    if not left or not right:
        return 0.0
    overlap = len(left & right)
    return overlap / max(1.0, math.sqrt(len(left) * len(right)))


def _tag_bonus(tag: str) -> float:
    value = _norm(tag)
    if "direct" in value:
        return 0.18
    if "connexe" in value or "related" in value:
        return 0.06
    if "fondamental" in value or "fundamental" in value:
        return 0.03
    return 0.0


def _pre_score(section_text: str, source: dict[str, Any]) -> float:
    score = _float(source.get("score"))
    # Les moteurs n'utilisent pas forcément la même échelle. On borne le score
    # uniquement pour le tri préliminaire ; le LLM fait la décision sémantique.
    if score > 1:
        score = min(score / 100.0, 1.0)
    return round(
        0.60 * max(0.0, min(score, 1.0))
        + 0.30 * _lexical_support(section_text, source)
        + _tag_bonus(str(source.get("tag") or "")),
        6,
    )


def _extract_json(raw: Any) -> dict[str, Any]:
    text = str(raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except Exception:
        left, right = text.find("{"), text.rfind("}")
        if left < 0 or right <= left:
            return {}
        try:
            payload = json.loads(text[left : right + 1])
        except Exception:
            return {}
    return payload if isinstance(payload, dict) else {}


def _schema() -> dict[str, Any]:
    return {
        "title": "ennoamel_cir_auto_evidence_v320",
        "type": "object",
        "required": ["decisions"],
        "properties": {
            "decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": [
                        "candidate_id",
                        "decision",
                        "relevance",
                        "reason",
                        "supported_need",
                    ],
                    "properties": {
                        "candidate_id": {"type": "string"},
                        "decision": {
                            "type": "string",
                            "enum": ["select", "reject"],
                        },
                        "relevance": {
                            "type": "string",
                            "enum": ["direct", "partial", "irrelevant"],
                        },
                        "reason": {"type": "string"},
                        "supported_need": {"type": "string"},
                        "evidence_hint": {"type": "string"},
                    },
                },
            }
        },
    }


def select_sources(
    *,
    section_text: str,
    section_title: str,
    weakness_reasons: list[str] | None,
    candidate_sources: list[dict[str, Any]],
    max_selected: int = 3,
    llm: LLMClient | None = None,
) -> dict[str, Any]:
    """Sélection préliminaire automatique des articles.

    IMPORTANT : cette fonction ne déclare PAS un article définitivement
    exploitable. La décision finale appartient à la couche Article Cards /
    evidence extraction : un article sans preuve préparée ne sera pas compté
    parmi les sources réellement retenues.
    """
    normalized = [
        normalize_source(row, index)
        for index, row in enumerate(candidate_sources or [], start=1)
        if isinstance(row, dict)
    ]

    eligible: list[dict[str, Any]] = []
    rejected_preflight: list[dict[str, Any]] = []

    for row in normalized:
        reasons: list[str] = []
        if not row.get("title"):
            reasons.append("title_missing")
        if not row.get("abstract_or_snippet"):
            reasons.append("abstract_or_snippet_missing")

        if reasons:
            rejected_preflight.append(
                {
                    **row,
                    "decision": "reject",
                    "reason": ",".join(reasons),
                    "stage": "preflight",
                }
            )
            continue

        row["pre_score"] = _pre_score(section_text, row)
        eligible.append(row)

    eligible.sort(
        key=lambda row: (
            float(row.get("pre_score") or 0.0),
            float(row.get("score") or 0.0),
        ),
        reverse=True,
    )

    # Un pool court limite le coût LLM tout en laissant assez de diversité.
    pool = eligible[: min(8, len(eligible))]

    if not pool:
        return {
            "policy_version": POLICY_VERSION,
            "selected_article_ids": [],
            "selected": [],
            "rejected": rejected_preflight,
            "decision_mode": "no_eligible_candidate",
        }

    prompt = f"""
Tu es le sélecteur automatique de preuves scientifiques du mode CIR COMPLET.

Tu dois décider quels articles sont réellement utiles pour renforcer LA SECTION
COURANTE. Ne juge pas l'article par son thème général uniquement.

SECTION COURANTE
Titre : {_clean(section_title, 500)}
Texte :
{_clean(section_text, 14000)}

FAIBLESSES DÉTECTÉES
{json.dumps(list(weakness_reasons or []), ensure_ascii=False)}

ARTICLES CANDIDATS
{json.dumps(pool, ensure_ascii=False)}

RÈGLES STRICTES
1. select uniquement si l'abstract/snippet contient un élément qui peut soutenir
   directement une faiblesse, une justification ou une limite présente dans
   cette section.
2. Une simple proximité de domaine est insuffisante.
3. Ne sélectionne jamais un article pour ajouter un nouveau thème étranger à la
   section.
4. supported_need doit décrire précisément CE QUE l'article peut soutenir dans
   la section, sans inventer de résultat absent de l'abstract/snippet.
5. evidence_hint indique le passage ou l'idée de l'abstract/snippet qui motive
   le choix ; ce n'est qu'un indice. L'extrait réellement traçable sera construit
   ensuite par Article Cards/full text.
6. Maximum {max(1, int(max_selected))} articles select.
7. S'il n'existe aucun article assez direct, rejette-les tous. Il vaut mieux
   conserver une section sans renforcement que fabriquer une justification.
8. Ne considère pas le score comme une preuve scientifique.

Retourne un verdict pour CHAQUE candidate_id du pool.
""".strip()

    verifier = llm or LLMClient()
    payload: dict[str, Any] = {}
    llm_meta: dict[str, Any] = {}

    try:
        raw = verifier.generate(
            prompt,
            temperature=0.0,
            max_output_tokens=3500,
            max_input_tokens=50000,
            retries=0,
            json_mode=True,
            response_schema=_schema(),
            request_name="ennoamelioration:cir_auto_evidence_selection_v320",
        )
        payload = _extract_json(raw)
        try:
            llm_meta = verifier.get_last_generation_meta()
        except Exception:
            llm_meta = {}
    except Exception as exc:
        payload = {}
        llm_meta = {
            "error": f"{exc.__class__.__name__}: {exc}",
        }

    source_by_id = {
        str(row["candidate_id"]): row
        for row in pool
    }
    decisions = [
        row
        for row in (payload.get("decisions") or [])
        if isinstance(row, dict)
        and str(row.get("candidate_id") or "") in source_by_id
    ]

    selected: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = list(rejected_preflight)

    if decisions:
        seen: set[str] = set()
        for decision in decisions:
            candidate_id = str(decision.get("candidate_id") or "")
            if candidate_id in seen:
                continue
            seen.add(candidate_id)
            source = source_by_id[candidate_id]
            choice = str(decision.get("decision") or "").casefold()
            relevance = str(decision.get("relevance") or "").casefold()

            enriched = {
                **source,
                "decision": (
                    "select"
                    if choice == "select" and relevance == "direct"
                    else "reject"
                ),
                "semantic_relevance": relevance,
                "reason": _clean(decision.get("reason"), 900),
                "supported_need": _clean(
                    decision.get("supported_need"), 1400
                ),
                "evidence_hint": _clean(
                    decision.get("evidence_hint"), 1800
                ),
                "stage": "semantic_selection",
            }

            if enriched["decision"] == "select":
                selected.append(enriched)
            else:
                rejected.append(enriched)

        # Tout candidat sans verdict est rejeté : pas de sélection implicite.
        for candidate_id, source in source_by_id.items():
            if candidate_id not in seen:
                rejected.append(
                    {
                        **source,
                        "decision": "reject",
                        "reason": "semantic_verdict_missing",
                        "stage": "semantic_selection",
                    }
                )

        selected.sort(
            key=lambda row: float(row.get("pre_score") or 0.0),
            reverse=True,
        )
        selected = selected[: max(1, int(max_selected))]
        decision_mode = "llm_semantic"
    else:
        # Fallback très conservateur si le contrôleur LLM est indisponible :
        # un seul article, uniquement s'il est déjà tagué Direct ET possède un
        # recouvrement lexical raisonnable. V3.18 vérifiera ensuite l'entailment.
        direct = [
            row
            for row in pool
            if "direct" in _norm(row.get("tag"))
            and _lexical_support(section_text, row) >= 0.05
        ]
        selected = direct[:1]
        selected_ids = {row["candidate_id"] for row in selected}
        for row in pool:
            if row["candidate_id"] not in selected_ids:
                rejected.append(
                    {
                        **row,
                        "decision": "reject",
                        "reason": "selector_unavailable_conservative_fallback",
                        "stage": "fallback",
                    }
                )
        for row in selected:
            row.update(
                {
                    "decision": "select",
                    "semantic_relevance": "direct",
                    "reason": "conservative_direct_fallback",
                    "supported_need": "",
                    "evidence_hint": "",
                    "stage": "fallback",
                }
            )
        decision_mode = "conservative_fallback"

    article_ids = [
        int(row["article_id"])
        for row in selected
        if row.get("article_id") is not None
    ]
    candidate_ids = [
        str(row["candidate_id"])
        for row in selected
        if str(row.get("candidate_id") or "").strip()
    ]

    return {
        "policy_version": POLICY_VERSION,
        "selected_article_ids": article_ids,
        "selected_candidate_ids": candidate_ids,
        "selected": selected,
        "rejected": rejected,
        "decision_mode": decision_mode,
        "pool_size": len(pool),
        "candidate_count": len(candidate_sources or []),
        "llm": llm_meta,
    }



def _post_extract_schema() -> dict[str, Any]:
    return {
        "title": "ennoamel_post_extract_relevance_v1",
        "type": "object",
        "required": ["decisions"],
        "properties": {
            "decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": [
                        "article_id",
                        "decision",
                        "relevance",
                        "reason",
                        "supported_claim",
                        "supporting_refs",
                    ],
                    "properties": {
                        "article_id": {"type": "string"},
                        "decision": {
                            "type": "string",
                            "enum": ["use", "reject"],
                        },
                        "relevance": {
                            "type": "string",
                            "enum": ["direct", "partial", "irrelevant"],
                        },
                        "reason": {"type": "string"},
                        "supported_claim": {"type": "string"},
                        "supporting_refs": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                },
            }
        },
    }


def _original_passages_from_card(
    card: dict[str, Any],
    *,
    max_passages: int = 14,
) -> list[dict[str, Any]]:
    """Extrait uniquement les vrais paragraphes issus du full text."""

    bank = card.get("article_evidence_bank")
    if not isinstance(bank, dict):
        return []

    rules = (
        bank.get("rules")
        if isinstance(bank.get("rules"), dict)
        else {}
    )

    if rules.get("paragraphs_are_original_extracts") is not True:
        return []

    buckets = bank.get("paragraph_buckets")
    if not isinstance(buckets, dict):
        return []

    ordered = (
        "problem",
        "solution",
        "method",
        "workflow",
        "validation",
        "results",
        "limitations",
        "definition",
        "future_work",
    )

    passages: list[dict[str, Any]] = []
    seen: set[str] = set()

    for bucket in ordered:
        rows = buckets.get(bucket) or []
        if not isinstance(rows, list):
            continue

        for row in rows[:4]:
            if not isinstance(row, dict):
                continue

            value = _clean(row.get("text"), 1800)
            key = _norm(value)

            if not value or not key or key in seen:
                continue

            seen.add(key)

            passages.append(
                {
                    "ref": f"P{len(passages) + 1}",
                    "bucket": bucket,
                    "section": _clean(row.get("section"), 200),
                    "text": value,
                }
            )

            if len(passages) >= max_passages:
                return passages

    return passages


def validate_prepared_sources_from_fulltext(
    *,
    section_text: str,
    section_title: str,
    weakness_reasons: list[str] | None,
    preliminary_selection: dict[str, Any],
    prepared_sources: list[dict[str, Any]],
    evidence_cards: list[dict[str, Any]],
    llm: LLMClient | None = None,
) -> dict[str, Any]:
    """Validation APRES extraction.

    Important :
    - tout article extrait reste conservé ;
    - seuls les articles dont les paragraphes ORIGINAUX soutiennent
      directement la section sont autorisés pour la rédaction.
    """

    cards_by_article: dict[int, dict[str, Any]] = {}

    for card in evidence_cards or []:
        if not isinstance(card, dict):
            continue

        article_id = _article_id(card)

        if article_id is not None:
            cards_by_article[article_id] = dict(card)

    selected_by_article: dict[int, dict[str, Any]] = {}

    for row in preliminary_selection.get("selected") or []:
        if not isinstance(row, dict):
            continue

        article_id = _article_id(row)

        if article_id is not None:
            selected_by_article[article_id] = dict(row)

    preserved: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    rejected_preflight: list[dict[str, Any]] = []

    passage_maps: dict[int, dict[str, dict[str, Any]]] = {}

    for raw in prepared_sources or []:
        if not isinstance(raw, dict):
            continue

        source = dict(raw)
        article_id = _article_id(source)

        # IMPORTANT : même s'il sera rejeté pour l'écriture,
        # l'article extrait reste conservé ici.
        preserved.append(source)

        if article_id is None:
            rejected_preflight.append(
                {
                    **source,
                    "decision": "reject",
                    "reason": "prepared_article_id_missing",
                    "stage": "post_extract_preflight",
                }
            )
            continue

        card = cards_by_article.get(article_id)

        passages = _original_passages_from_card(card or {})

        if not passages:
            rejected_preflight.append(
                {
                    **source,
                    "article_id": article_id,
                    "decision": "reject",
                    "reason": "no_original_fulltext_passage_available",
                    "stage": "post_extract_preflight",
                }
            )
            continue

        passage_maps[article_id] = {
            row["ref"]: row
            for row in passages
        }

        preliminary = selected_by_article.get(article_id) or {}

        candidates.append(
            {
                "article_id": str(article_id),
                "candidate_id": (
                    _row_candidate_id(source)
                    or _row_candidate_id(preliminary)
                ),
                "title": _clean(
                    source.get("title")
                    or preliminary.get("title"),
                    900,
                ),
                "initial_supported_need": _clean(
                    preliminary.get("supported_need"),
                    1400,
                ),
                "initial_reason": _clean(
                    preliminary.get("reason"),
                    1200,
                ),
                "passages": passages,
            }
        )

    if not candidates:
        return {
            "policy_version": "ennoamel_post_extract_relevance_v1",
            "preserved_extracted_sources": preserved,
            "eligible_sources": [],
            "eligible_article_ids": [],
            "rejected_for_writing": rejected_preflight,
            "decision_mode": "no_original_passages",
        }

    prompt = f"""
Tu vérifies la pertinence scientifique APRES extraction du texte intégral.

SECTION CIR COURANTE
Titre : {_clean(section_title, 500)}

Texte :
{_clean(section_text, 14000)}

FAIBLESSES A RENFORCER
{json.dumps(list(weakness_reasons or []), ensure_ascii=False)}

ARTICLES EXTRAITS
{json.dumps(candidates, ensure_ascii=False)}

REGLES STRICTES

1. Base ta décision UNIQUEMENT sur les passages originaux P1, P2... fournis.

2. "use" uniquement si au moins un passage soutient DIRECTEMENT une
   affirmation scientifique utile à cette section.

3. Une simple proximité de mots, de domaine ou de thème est insuffisante.

4. Vérifie la compatibilité réelle :
   - phénomène étudié ;
   - système ou matériau ;
   - mécanisme ;
   - conditions/régime expérimental ;
   - type de résultat démontré.

5. Un résultat obtenu sur un système voisin ne peut pas automatiquement
   justifier le système du CIR.

6. Si l'article n'est pertinent qu'à un niveau général ou indirect :
   relevance="partial" et decision="reject".

7. supporting_refs doit contenir uniquement les P# qui soutiennent
   réellement supported_claim.

8. supported_claim doit être une affirmation précise et bornée que le
   Writer peut défendre sans extrapolation.

9. Si aucun passage n'est assez direct : reject.

10. Un article rejeté ici reste CONSERVE dans le corpus extrait.
    Ce verdict décide uniquement s'il peut être utilisé automatiquement
    dans la rédaction.
""".strip()

    verifier = llm or LLMClient()

    payload: dict[str, Any] = {}
    llm_meta: dict[str, Any] = {}

    try:
        raw = verifier.generate(
            prompt,
            temperature=0.0,
            max_output_tokens=2600,
            max_input_tokens=50000,
            retries=0,
            json_mode=True,
            response_schema=_post_extract_schema(),
            request_name="ennoamelioration:post_extract_relevance_v1",
        )

        payload = _extract_json(raw)

        try:
            llm_meta = verifier.get_last_generation_meta()
        except Exception:
            llm_meta = {}

    except Exception as exc:
        llm_meta = {
            "error": f"{exc.__class__.__name__}: {exc}"
        }

    prepared_by_article = {
        _article_id(row): dict(row)
        for row in preserved
        if _article_id(row) is not None
    }

    decisions = {
        str(row.get("article_id") or ""): row
        for row in (payload.get("decisions") or [])
        if isinstance(row, dict)
    }

    eligible_sources: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = list(rejected_preflight)

    for candidate in candidates:
        article_id = int(candidate["article_id"])

        decision = decisions.get(str(article_id))

        if not isinstance(decision, dict):
            rejected.append(
                {
                    **prepared_by_article[article_id],
                    "article_id": article_id,
                    "decision": "reject",
                    "reason": "post_extract_semantic_verdict_missing",
                    "stage": "post_extract_semantic",
                }
            )
            continue

        choice = str(
            decision.get("decision") or ""
        ).casefold()

        relevance = str(
            decision.get("relevance") or ""
        ).casefold()

        valid_refs = [
            str(ref)
            for ref in (
                decision.get("supporting_refs") or []
            )
            if str(ref)
            in passage_maps.get(article_id, {})
        ]

        supported_claim = _clean(
            decision.get("supported_claim"),
            1800,
        )

        if (
            choice == "use"
            and relevance == "direct"
            and valid_refs
            and supported_claim
        ):
            supporting_passages = [
                passage_maps[article_id][ref]
                for ref in valid_refs
            ]

            eligible_sources.append(
                {
                    **prepared_by_article[article_id],
                    "article_id": article_id,
                    "post_extract_relevance": "direct",
                    "post_extract_supported_claim": supported_claim,
                    "post_extract_supporting_refs": valid_refs,
                    "post_extract_supporting_passages": supporting_passages,
                    "post_extract_reason": _clean(
                        decision.get("reason"),
                        1200,
                    ),
                    "writing_eligible": True,
                }
            )

        else:
            rejected.append(
                {
                    **prepared_by_article[article_id],
                    "article_id": article_id,
                    "decision": "reject",
                    "relevance": relevance or "irrelevant",
                    "reason": (
                        _clean(
                            decision.get("reason"),
                            1200,
                        )
                        or "not_direct_after_fulltext"
                    ),
                    "supported_claim": supported_claim,
                    "supporting_refs": valid_refs,
                    "stage": "post_extract_semantic",
                    "writing_eligible": False,
                }
            )

    return {
        "policy_version": "ennoamel_post_extract_relevance_v1",

        # TOUS les articles extraits restent ici.
        "preserved_extracted_sources": preserved,

        # Seulement ceux-ci sont autorisés pour le Writer.
        "eligible_sources": eligible_sources,
        "eligible_article_ids": [
            int(row["article_id"])
            for row in eligible_sources
        ],

        "rejected_for_writing": rejected,

        "decision_mode": (
            "llm_fulltext_original_passages"
            if decisions
            else "conservative_reject_without_verifier"
        ),

        "llm": llm_meta,
    }



def bind_prepared_sources(
    *,
    selection: dict[str, Any],
    prepared_sources: list[dict[str, Any]],
) -> dict[str, Any]:
    """Rattache les ``article_id`` créés après la présélection Guided Research.

    La présélection intervient volontairement avant la création des lignes
    Article : elle ne connaît donc que ``candidate_id``. La préparation
    full-text / Article Card crée ensuite ``article_id``. Sans ce rattachement,
    le contrôle final peut prendre une rédaction scientifique réussie pour un
    échec et déclencher à tort le fallback éditorial.
    """

    prepared_by_candidate = {
        _row_candidate_id(row): dict(row)
        for row in (prepared_sources or [])
        if isinstance(row, dict)
        and _row_candidate_id(row)
        and _row_article_id(row) is not None
    }

    selected: list[dict[str, Any]] = []
    bound_article_ids: list[int] = []
    bound_candidate_ids: list[str] = []
    for raw in selection.get("selected") or []:
        if not isinstance(raw, dict):
            continue
        source = dict(raw)
        candidate_id = _row_candidate_id(source)
        prepared = prepared_by_candidate.get(candidate_id)
        article_id = _row_article_id(source)
        if prepared is not None:
            article_id = _row_article_id(prepared) or article_id
            source.update(
                {
                    "article_id": article_id,
                    "article_card_ready": bool(
                        prepared.get("article_card_ready")
                    ),
                    "fulltext_status": prepared.get("fulltext_status"),
                    "prepared_for_writing": bool(
                        prepared.get("article_card_ready")
                    ),
                }
            )
            for key in (
                "title",
                "authors",
                "year",
                "provider",
                "doi",
                "url",
                "site_url",
                "pdf_url",
                "abstract",
                "abstract_or_snippet",
            ):
                if not source.get(key) and prepared.get(key):
                    source[key] = prepared.get(key)

        if article_id is not None:
            bound_article_ids.append(article_id)
            if candidate_id:
                bound_candidate_ids.append(candidate_id)
        selected.append(source)

    return {
        **dict(selection),
        "selected": selected,
        "selected_article_ids": list(dict.fromkeys(bound_article_ids)),
        "selected_candidate_ids": list(
            dict.fromkeys(
                str(value or "").strip()
                for value in (selection.get("selected_candidate_ids") or [])
                if str(value or "").strip()
            )
        ),
        "prepared_binding": {
            "bound_count": len(set(bound_article_ids)),
            "bound_article_ids": list(dict.fromkeys(bound_article_ids)),
            "bound_candidate_ids": list(dict.fromkeys(bound_candidate_ids)),
            "policy": "candidate_id_to_article_id_after_fulltext_preparation",
        },
    }


def _evidence_rows(result: Any) -> list[dict[str, Any]]:
    evidence = getattr(result, "evidence", None)
    scholar = evidence.get("scholar") if isinstance(evidence, dict) else None
    rows = scholar.get("evidence") if isinstance(scholar, dict) else None
    return [
        dict(row)
        for row in (rows or [])
        if isinstance(row, dict)
    ]


def _source_rows(result: Any) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in (getattr(result, "sources_used", None) or [])
        if isinstance(row, dict)
    ]


def _row_article_id(row: dict[str, Any]) -> int | None:
    return _article_id(row)


def _best_excerpt(row: dict[str, Any]) -> str:
    for key in (
        "evidence_text",
        "quote",
        "snippet",
        "support",
        "claim",
        "abstract",
        "rationale",
    ):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return _clean(value, 5000)
        if isinstance(value, list) and value:
            joined = " ".join(
                _clean(child, 1200)
                for child in value[:4]
            ).strip()
            if joined:
                return joined
    return ""


def _row_candidate_id(row: dict[str, Any]) -> str:
    return _clean(
        row.get("candidate_id")
        or row.get("guided_candidate_id")
        or row.get("research_candidate_id"),
        220,
    )


def build_traceable_evidence(
    *,
    result: Any,
    selection: dict[str, Any],
) -> dict[str, Any]:
    """Finalise les sources après préparation Article Card/full text.

    La présélection travaille avec ``candidate_id`` car ``article_id`` peut ne
    pas exister avant la préparation. Après extraction, le rattachement se fait
    par article_id, candidate_id, puis titre normalisé. Une source n'est
    writing-ready que si un véritable article_id et un extrait traçable existent.
    """
    selected = [
        dict(row)
        for row in (selection.get("selected") or [])
        if isinstance(row, dict)
    ]
    evidence_rows = _evidence_rows(result)
    sources_used = _source_rows(result)

    selected_by_article: dict[int, dict[str, Any]] = {}
    selected_by_candidate: dict[str, dict[str, Any]] = {}
    selected_by_title: dict[str, dict[str, Any]] = {}
    for source in selected:
        article_id = _row_article_id(source)
        candidate_id = _row_candidate_id(source)
        title_key = _norm(source.get("title"))
        if article_id is not None:
            selected_by_article[article_id] = source
        if candidate_id:
            selected_by_candidate[candidate_id] = source
        if title_key:
            selected_by_title[title_key] = source

    def _match_selected(row: dict[str, Any]) -> dict[str, Any] | None:
        article_id = _row_article_id(row)
        if article_id is not None and article_id in selected_by_article:
            return selected_by_article[article_id]
        candidate_id = _row_candidate_id(row)
        if candidate_id and candidate_id in selected_by_candidate:
            return selected_by_candidate[candidate_id]
        title_key = _norm(
            row.get("title") or row.get("paper_title") or row.get("name")
        )
        if title_key and title_key in selected_by_title:
            return selected_by_title[title_key]
        return None

    evidence_for_source: dict[int, dict[str, Any]] = {}
    prepared_article_for_source: dict[int, int] = {}
    for row in evidence_rows:
        source = _match_selected(row)
        if source is None:
            continue
        article_id = _row_article_id(row) or _row_article_id(source)
        if article_id is not None:
            prepared_article_for_source[id(source)] = article_id
        evidence_for_source[id(source)] = row

    used_source_keys: set[int] = set()
    for row in sources_used:
        source = _match_selected(row)
        if source is None:
            continue
        used_source_keys.add(id(source))
        article_id = _row_article_id(row) or _row_article_id(source)
        if article_id is not None:
            prepared_article_for_source[id(source)] = article_id

    # Source réellement utilisée = citation effectivement présente
    # dans la candidate.
    candidate_text = str(
        getattr(result, "improved_target", None) or ""
    )

    cited_ids = {
        value.upper()
        for value in re.findall(
            r"(?<![A-Za-z0-9])(A\d+)(?![A-Za-z0-9])",
            candidate_text,
            flags=re.I,
        )
    }

    for source_key, evidence_row in evidence_for_source.items():
        citation_id = str(
            evidence_row.get("citation_id") or ""
        ).strip().upper()

        if citation_id and citation_id in cited_ids:
            used_source_keys.add(source_key)


    final: list[dict[str, Any]] = []
    advisory_sources: list[dict[str, Any]] = []
    rejected_after_evidence: list[dict[str, Any]] = []

    for source in selected:
        source_key = id(source)
        evidence_row = evidence_for_source.get(source_key)
        article_id = (
            _row_article_id(source)
            or prepared_article_for_source.get(source_key)
            or (_row_article_id(evidence_row) if evidence_row else None)
        )

        if article_id is not None:
            advisory_excerpt = (
                _best_excerpt(evidence_row)
                if evidence_row
                else _clean(
                    source.get("abstract")
                    or source.get("abstract_or_snippet"),
                    5000,
                )
            )
            advisory_sources.append(
                {
                    **source,
                    "candidate_id": _row_candidate_id(source),
                    "article_id": article_id,
                    "citation_id": str(
                        (evidence_row or {}).get("citation_id") or ""
                    ).strip().upper(),
                    "evidence_excerpt": advisory_excerpt,
                    "evidence_status": (
                        "writing_ready"
                        if evidence_row and advisory_excerpt
                        else "consultant_review"
                    ),
                    "final_decision": "advisory_accept",
                }
            )

        if article_id is None:
            rejected_after_evidence.append({
                **source,
                "final_decision": "reject",
                "final_reason": "prepared_article_id_missing",
            })
            continue

        if source_key not in used_source_keys or evidence_row is None:
            rejected_after_evidence.append({
                **source,
                "article_id": article_id,
                "final_decision": "reject",
                "final_reason": "article_card_or_traceable_evidence_unavailable",
            })
            continue

        excerpt = _best_excerpt(evidence_row)
        if not excerpt:
            rejected_after_evidence.append({
                **source,
                "article_id": article_id,
                "final_decision": "reject",
                "final_reason": "traceable_excerpt_missing",
            })
            continue

        citation_id = str(evidence_row.get("citation_id") or "").strip().upper()
        final.append({
            "candidate_id": _row_candidate_id(source),
            "article_id": article_id,
            "citation_id": citation_id,
            "title": source.get("title") or evidence_row.get("title"),
            "year": source.get("year") or evidence_row.get("year"),
            "provider": source.get("provider") or evidence_row.get("provider"),
            "selection_reason": source.get("reason"),
            "supported_need": source.get("supported_need"),
            "evidence_hint": source.get("evidence_hint"),
            "evidence_excerpt": excerpt,
            "evidence_status": "writing_ready",
            "final_decision": "auto_accept",
        })

    return {
        "policy_version": POLICY_VERSION,
        "traceability_policy_version": "candidate_to_article_after_preparation_v3_23",
        "auto_accepted": final,
        "auto_accepted_article_ids": [int(row["article_id"]) for row in final],
        "auto_accepted_candidate_ids": [
            str(row.get("candidate_id") or "")
            for row in final
            if str(row.get("candidate_id") or "").strip()
        ],
        "advisory_sources": advisory_sources,
        "prepared_source_count": len(advisory_sources),
        "rejected_after_evidence": rejected_after_evidence,
        "writing_ready_count": len(final),
        "selected_preliminary_count": len(selected),
    }
