from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable

POLICY_VERSION = "ennoamel_natural_intent_v3_14"
MODE_FRESH = "fresh_research"
MODE_REUSE = "reuse_validated_sources"
MODE_NONE = "no_research_action"


@dataclass(frozen=True)
class FreshResearchDecision:
    mode: str
    reason: str
    explicit_reuse: bool = False
    explicit_fresh: bool = False
    natural_strengthening: bool = False
    semantic_strengthening: bool = False


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("’", "'")
    return re.sub(r"\s+", " ", text).strip()


def _clauses(value: str | None) -> list[str]:
    return [
        part.strip()
        for part in re.split(
            r"[.!?;\n]+|\bmais\b|\bcependant\b|\ben revanche\b",
            _norm(value),
            flags=re.I,
        )
        if part.strip()
    ]


def _intent_names(intents: Iterable[Any] | None) -> set[str]:
    names: set[str] = set()
    for item in intents or []:
        value = getattr(item, "value", item)
        text = str(value or "").strip().casefold()
        if text:
            names.add(text)
    return names


def explicitly_requests_existing_validated_sources(value: str | None) -> bool:
    positives = (
        r"\butilis\w*\b[^.!?;]{0,140}\b(?:sources?|articles?|publications?|references?|citations?|preuves?)\b"
        r"[^.!?;]{0,140}\b(?:garde\w*|valid\w*|selectionn\w*|accept\w*|deja)\b",
        r"\b(?:avec|a partir de|sur la base de)\b[^.!?;]{0,100}"
        r"\b(?:sources?|articles?|publications?|references?|citations?|preuves?)\b"
        r"[^.!?;]{0,100}\b(?:garde\w*|valid\w*|selectionn\w*|accept\w*|deja)\b",
        r"\b(?:sources?|articles?|publications?|preuves?)\b[^.!?;]{0,100}"
        r"\b(?:que|qu')\s*(?:j[' ]?ai|nous avons)\s+(?:garde|valide|selectionne|accepte)\w*\b",
        r"\bne\s+relance\w*\s+pas\b[^.!?;]{0,80}\b(?:recherche|ennoscholar)\b",
        r"\bsans\s+(?:nouvelle?\s+)?recherche\b",
    )
    negatives = (
        r"\bne\s+(?:re)?utilis\w*\s+pas\b",
        r"\bn[' ]?utilis\w*\s+pas\b",
        r"\bsans\s+utilis\w*\b",
    )
    for clause in _clauses(value):
        if any(re.search(p, clause, re.I) for p in negatives):
            continue
        if any(re.search(p, clause, re.I) for p in positives):
            return True
    return False


def explicitly_requests_fresh_research(value: str | None) -> bool:
    positives = (
        r"\b(?:fais|faire|faites|lance|lancer|lancez|demarre|demarrer|effectue|effectuer|relance|relancer)\w*\b"
        r"[^.!?;]{0,140}\b(?:nouvelle?\s+)?recherche\b",
        r"\b(?:cherche|chercher|recherche|rechercher|trouve|trouver)\w*\b"
        r"[^.!?;]{0,140}\b(?:articles?|publications?|sources?|references?|preuves?)\b",
        r"\brecherche\w*\b[^.!?;]{0,140}\b(?:articles?|publications?|sources?|references?|preuves?)\b",
    )
    negatives = (
        r"\bsans\s+(?:nouvelle?\s+)?recherche\b",
        r"\bpas\s+de\s+(?:nouvelle?\s+)?recherche\b",
        r"\bne\s+relance\w*\s+pas\b[^.!?;]{0,80}\brecherche\b",
        r"\bne\s+(?:cherche|recherche|trouve)\w*\s+pas\b",
    )
    for clause in _clauses(value):
        if any(re.search(p, clause, re.I) for p in negatives):
            continue
        if any(re.search(p, clause, re.I) for p in positives):
            return True
    return False


def requests_natural_strengthening(value: str | None) -> bool:
    """Reconnaît une demande de renforcer le fond, sans vocabulaire métier codé en dur."""

    negatives = (
        r"\bne\s+(?:renforc|developp|approfond|etoff|complet|enrich|ajout|rajout)\w*\s+pas\b",
        r"\bsans\s+(?:renforc|developp|approfond|etoff|complet|enrich|ajout|rajout)\w*\b",
        r"\baucun\w*\b[^.!?;]{0,80}\b(?:argument|justification|preuve|explication|raison)\w*\b",
    )
    positives = (
        r"\b(?:renforc|approfond|etoff|developp|consolid|enrich)\w*\b",
        r"\b(?:plus|davantage|encore\s+plus)\b[^.!?;]{0,60}"
        r"\b(?:argument|justification|preuve|explication|raison)\w*\b",
        r"\b(?:besoin|necessit|faible|insuffisan|manque|pas\s+assez)\w*\b[^.!?;]{0,120}"
        r"\b(?:argument|justification|preuve|explication|raison)\w*\b",
        r"\b(?:ajout|rajout|apport|integre|introdui|complete)\w*\b[^.!?;]{0,140}"
        r"\b(?:argument|justification|preuve|explication|raison)\w*\b",
        r"\b(?:argument|justifi|etay)\w*\b[^.!?;]{0,70}"
        r"\b(?:davantage|mieux|plus|fortement|solidement)\b",
        r"\b(?:rend|rendre)\w*\b[^.!?;]{0,120}"
        r"\b(?:plus\s+)?(?:solide|argumente|convaincant|defendable|etaye)\w*\b",
        r"\b(?:improve|improuve|amelior)\w*\b[^.!?;]{0,140}"
        r"\b(?:plus|davantage)\b[^.!?;]{0,60}"
        r"\b(?:argument|justification|preuve|explication|raison)\w*\b",
    )
    for clause in _clauses(value):
        if any(re.search(p, clause, re.I) for p in negatives):
            continue
        if any(re.search(p, clause, re.I) for p in positives):
            return True
    return False


def _is_write_after_validation(value: str | None) -> bool:
    text = _norm(value)
    if not text:
        return False
    write = bool(
        re.search(
            r"\b(?:redig|reecri|ecri|termine|finalise|continue|genere|produi)\w*\b",
            text,
            re.I,
        )
    )
    return bool(
        write
        and not requests_natural_strengthening(text)
        and not explicitly_requests_fresh_research(text)
    )


def resolve_fresh_research_policy(
    *,
    instruction: str,
    current_choice: str | None,
    intents: Iterable[Any] | None,
    needs_scholar: bool,
    editorial_only: bool,
    hard_forbid_research: bool,
    hard_forbid_scholar: bool,
) -> FreshResearchDecision:
    if hard_forbid_scholar:
        return FreshResearchDecision(MODE_NONE, "Le consultant interdit explicitement EnnoScholar.")

    explicit_reuse = explicitly_requests_existing_validated_sources(instruction)
    explicit_fresh = explicitly_requests_fresh_research(instruction)
    natural = requests_natural_strengthening(instruction)
    names = _intent_names(intents)
    semantic = bool({"argumentation", "scientific_enrichment", "research"} & names)

    if explicit_reuse and not explicit_fresh:
        return FreshResearchDecision(
            MODE_REUSE,
            "Le consultant demande explicitement les preuves déjà validées.",
            explicit_reuse=True,
            natural_strengthening=natural,
            semantic_strengthening=semantic,
        )

    if hard_forbid_research:
        return FreshResearchDecision(
            MODE_NONE,
            "Le consultant interdit une nouvelle recherche.",
            natural_strengthening=natural,
            semantic_strengthening=semantic,
        )

    if explicit_fresh:
        return FreshResearchDecision(
            MODE_FRESH,
            "Le consultant demande explicitement une recherche ciblée.",
            explicit_fresh=True,
            natural_strengthening=natural,
            semantic_strengthening=semantic,
        )

    if editorial_only:
        return FreshResearchDecision(MODE_NONE, "La demande est purement éditoriale.")

    if _is_write_after_validation(instruction):
        return FreshResearchDecision(
            MODE_NONE,
            "Le consultant demande la rédaction après validation, sans nouveau renforcement.",
        )

    # V3.14 : souveraineté de l'intention.
    # Pas de dépendance à needs_scholar : si le consultant demande de renforcer
    # le fond, EnnoScholar est activé par cette décision.
    if natural or semantic:
        return FreshResearchDecision(
            MODE_FRESH,
            "Renforcement du fond / de l'argumentation demandé : nouvelle recherche EnnoScholar ciblée obligatoire avant rédaction.",
            natural_strengthening=natural,
            semantic_strengthening=semantic,
        )

    return FreshResearchDecision(MODE_NONE, "Aucune nouvelle action de recherche n'est demandée.")
