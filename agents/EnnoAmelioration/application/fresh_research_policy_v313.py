from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable

POLICY_VERSION = "ennoamel_natural_strengthening_v3_13"
MODE_FRESH = "fresh_research"
MODE_REUSE = "reuse_validated_sources"
MODE_NONE = "no_research_action"


@dataclass(frozen=True)
class FreshResearchDecision:
    mode: str
    reason: str
    explicit_reuse: bool = False
    explicit_fresh: bool = False
    scientific_strengthening: bool = False
    natural_strengthening: bool = False


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("’", "'")
    return re.sub(r"\s+", " ", text).strip()


def _clauses(value: str) -> list[str]:
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
    result: set[str] = set()
    for item in intents or []:
        value = getattr(item, "value", item)
        text = str(value or "").strip().casefold()
        if text:
            result.add(text)
    return result


def explicitly_requests_existing_validated_sources(value: str | None) -> bool:
    text = _norm(value)
    if not text:
        return False
    positive = (
        r"\butilis\w*\b[^.!?;]{0,130}\b(?:sources?|articles?|publications?|references?|citations?|preuves?)\b"
        r"[^.!?;]{0,130}\b(?:garde\w*|valid\w*|selectionn\w*|accept\w*|deja)\b",
        r"\b(?:avec|a partir de|sur la base de)\b[^.!?;]{0,100}"
        r"\b(?:sources?|articles?|publications?|references?|citations?|preuves?)\b"
        r"[^.!?;]{0,100}\b(?:garde\w*|valid\w*|selectionn\w*|accept\w*|deja)\b",
        r"\b(?:sources?|articles?|publications?|preuves?)\b[^.!?;]{0,100}"
        r"\b(?:que|qu')\s*(?:j[' ]?ai|nous avons)\s+(?:garde|valide|selectionne|accepte)\w*\b",
        r"\bne\s+relance\s+pas\b[^.!?;]{0,80}\brecherche\b",
        r"\bsans\s+(?:nouvelle?\s+)?recherche\b",
    )
    negative = (
        r"\bne\s+(?:re)?utilis\w*\s+pas\b",
        r"\bn[' ]?utilis\w*\s+pas\b",
        r"\bsans\s+utilis\w*\b",
    )
    for clause in _clauses(text):
        if any(re.search(pattern, clause, re.I) for pattern in negative):
            continue
        if any(re.search(pattern, clause, re.I) for pattern in positive):
            return True
    return False


def explicitly_requests_fresh_research(value: str | None) -> bool:
    text = _norm(value)
    if not text:
        return False
    patterns = (
        r"\b(?:fais|faire|faites|lance|lancer|lancez|demarre|demarrer|effectue|effectuer|relance|relancer)\w*\b"
        r"[^.!?;]{0,130}\b(?:nouvelle?\s+)?recherche\b",
        r"\brecherche\w*\b[^.!?;]{0,130}\b(?:articles?|publications?|sources?|references?)\b",
        r"\b(?:cherche|chercher|recherche|rechercher|trouve|trouver)\w*\b"
        r"[^.!?;]{0,130}\b(?:articles?|publications?|sources?|references?)\b",
        r"\b(?:plus|davantage|d'autres?)\b[^.!?;]{0,80}\b(?:articles?|publications?|sources?|references?)\b",
    )
    forbidden = (
        r"\bsans\s+(?:nouvelle?\s+)?recherche\b",
        r"\bpas\s+de\s+(?:nouvelle?\s+)?recherche\b",
        r"\bne\s+relance\w*\s+pas\b[^.!?;]{0,60}\brecherche\b",
    )
    for clause in _clauses(text):
        if any(re.search(pattern, clause, re.I) for pattern in forbidden):
            continue
        if any(re.search(pattern, clause, re.I) for pattern in patterns):
            return True
    return False


def requests_natural_strengthening(value: str | None) -> bool:
    """Comprend le renforcement en langage naturel, sans dépendre d'un titre métier."""
    text = _norm(value)
    if not text:
        return False

    negative = (
        r"\bne\s+(?:renforc|developp|approfond|etoff|complet|ajout)\w*\s+pas\b",
        r"\bsans\s+(?:renforc|developp|approfond|etoff|complet|ajout)\w*\b",
        r"\baucun\w*\b[^.!?;]{0,70}\b(?:argument|justification|preuve|raison)\w*\b",
        r"\bne\s+rajout\w*\s+(?:aucun\w*|pas\s+de)\b[^.!?;]{0,70}\bargument\w*\b",
    )

    action_patterns = (
        r"\b(?:renforc|approfond|etoff|developp|complet|consolid|enrich)\w*\b",
        r"\b(?:ajout|rajout|apport|donne|integre|introdui)\w*\b[^.!?;]{0,150}"
        r"\b(?:argument|justification|preuve|explication|raison)\w*\b",
        r"\b(?:plus|davantage|encore\s+plus)\b[^.!?;]{0,45}"
        r"\b(?:argument|justification|preuve|explication|raison)\w*\b",
        r"\b(?:argument|justifi|etay)\w*\b[^.!?;]{0,70}"
        r"\b(?:davantage|mieux|plus|fortement|solidement)\b",
        r"\b(?:rend|rendre)\w*\b[^.!?;]{0,120}"
        r"\b(?:plus\s+)?(?:solide|argumente|convaincant|defendable|etaye)\w*\b",
        r"\b(?:manque|insuffisan|pas\s+assez)\w*\b[^.!?;]{0,90}"
        r"\b(?:argument|justification|preuve|explication|raison)\w*\b",
    )

    for clause in _clauses(text):
        if any(re.search(pattern, clause, re.I) for pattern in negative):
            continue
        if any(re.search(pattern, clause, re.I) for pattern in action_patterns):
            return True
    return False


def requests_explicit_scientific_strengthening(value: str | None) -> bool:
    text = _norm(value)
    if not text:
        return False
    patterns = (
        r"\b(?:renforc|enrich|approfond|developp|etoff)\w*\b[^.!?;]{0,160}\bscientifi\w*\b",
        r"\bscientifi\w*\b[^.!?;]{0,160}\b(?:renforc|enrich|approfond|developp|etoff)\w*\b",
        r"\b(?:argument|argumentation|justification|preuve)\w*\b[^.!?;]{0,120}\bscientifi\w*\b",
        r"\betay\w*\b[^.!?;]{0,120}\b(?:scientifi\w*|sources?|articles?|publications?)\b",
    )
    return any(
        re.search(pattern, clause, re.I)
        for clause in _clauses(text)
        for pattern in patterns
    )


def _looks_like_write_after_validation(value: str | None) -> bool:
    text = _norm(value)
    if not text:
        return False
    write_action = bool(
        re.search(
            r"\b(?:redig|reecri|ecri|termine|finalise|continue|genere|produi)\w*\b",
            text,
            flags=re.I,
        )
    )
    strengthening = requests_natural_strengthening(text) or requests_explicit_scientific_strengthening(text)
    fresh = explicitly_requests_fresh_research(text)
    return bool(write_action and not strengthening and not fresh)


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
        return FreshResearchDecision(
            MODE_NONE,
            "EnnoScholar est explicitement interdit par le consultant.",
        )

    explicit_reuse = explicitly_requests_existing_validated_sources(instruction)
    explicit_fresh = explicitly_requests_fresh_research(instruction)
    natural_strengthening = requests_natural_strengthening(instruction)
    scientific_strengthening = requests_explicit_scientific_strengthening(instruction)
    names = _intent_names(intents)

    if explicit_reuse and not explicit_fresh:
        return FreshResearchDecision(
            MODE_REUSE,
            "Le consultant demande explicitement d'utiliser les sources déjà gardées/validées.",
            explicit_reuse=True,
            scientific_strengthening=scientific_strengthening,
            natural_strengthening=natural_strengthening,
        )

    if hard_forbid_research:
        return FreshResearchDecision(
            MODE_NONE,
            "Le consultant interdit toute nouvelle recherche.",
            scientific_strengthening=scientific_strengthening,
            natural_strengthening=natural_strengthening,
        )

    if explicit_fresh:
        return FreshResearchDecision(
            MODE_FRESH,
            "Le consultant demande explicitement une nouvelle recherche ciblée.",
            explicit_fresh=True,
            scientific_strengthening=scientific_strengthening,
            natural_strengthening=natural_strengthening,
        )

    if editorial_only:
        return FreshResearchDecision(
            MODE_NONE,
            "La demande est purement éditoriale.",
        )

    # V3.13 : pas besoin du mot "scientifique".
    # Si le routeur sémantique a décidé que la cible relève de Scholar et que
    # le consultant demande de renforcer/développer/argumenter, nouvelle recherche.
    if needs_scholar and (natural_strengthening or scientific_strengthening):
        return FreshResearchDecision(
            MODE_FRESH,
            "Le consultant demande un renforcement du fond sur une cible relevant d'EnnoScholar ; une nouvelle recherche ciblée est obligatoire.",
            scientific_strengthening=scientific_strengthening,
            natural_strengthening=natural_strengthening,
        )

    # Le sens déjà produit par le routeur sémantique est souverain.
    if needs_scholar and (
        "research" in names
        or "scientific_enrichment" in names
        or "argumentation" in names
    ):
        if not _looks_like_write_after_validation(instruction):
            return FreshResearchDecision(
                MODE_FRESH,
                "Le routage sémantique a identifié une demande d'argumentation/enrichissement sur une cible Scholar.",
                scientific_strengthening=("scientific_enrichment" in names),
                natural_strengthening=("argumentation" in names),
            )

    # Ne jamais réutiliser silencieusement un vieux current_choice du frontend.
    return FreshResearchDecision(
        MODE_NONE,
        "Aucune nouvelle action de recherche n'est demandée dans ce tour.",
    )
