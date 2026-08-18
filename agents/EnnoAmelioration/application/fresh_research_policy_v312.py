from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable


POLICY_VERSION = "ennoamel_fresh_research_v3_12"
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


def _has(value: str, *patterns: str) -> bool:
    return any(re.search(pattern, value, flags=re.I) for pattern in patterns)


def explicitly_requests_existing_validated_sources(value: str | None) -> bool:
    """Vrai uniquement si le consultant demande POSITIVEMENT de réutiliser ses sources.

    Une simple existence de sources dans la conversation ne suffit jamais.
    """

    text = _norm(value)
    if not text:
        return False
    positive = (
        r"\butilis\w*\b[^.!?;]{0,100}\b(?:sources?|articles?|publications?|references?|citations?|preuves?)\b"
        r"[^.!?;]{0,100}\b(?:garde(?:e|es|s)?|valide(?:e|es|s)?|selectionne(?:e|es|s)?|accepte(?:e|es|s)?|deja)\b",
        r"\b(?:avec|a partir de|sur la base de)\b[^.!?;]{0,80}"
        r"\b(?:sources?|articles?|publications?|references?|citations?|preuves?)\b"
        r"[^.!?;]{0,80}\b(?:garde(?:e|es|s)?|valide(?:e|es|s)?|selectionne(?:e|es|s)?|accepte(?:e|es|s)?|deja)\b",
        r"\b(?:sources?|articles?|publications?)\b[^.!?;]{0,80}"
        r"\b(?:que|qu')\s*(?:j[' ]?ai|nous avons)\s+(?:garde|valide|selectionne|accepte)\b",
        r"\bne\s+relance\s+pas\b[^.!?;]{0,60}\brecherche\b",
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
    """Commande explicite de recherche, indépendante de l'existence d'anciennes sources."""

    text = _norm(value)
    if not text:
        return False
    patterns = (
        r"\b(?:fais|faire|faites|lance|lancer|lancez|demarre|demarrer|effectue|effectuer|relance|relancer)\w*\b"
        r"[^.!?;]{0,100}\b(?:nouvelle?\s+)?recherche\b",
        r"\brecherche\w*\b[^.!?;]{0,100}\b(?:articles?|publications?|sources?|references?)\b",
        r"\b(?:cherche|chercher|recherche|rechercher|trouve|trouver)\w*\b"
        r"[^.!?;]{0,100}\b(?:articles?|publications?|sources?|references?)\b",
        r"\b(?:plus|davantage|d'autres?)\b[^.!?;]{0,60}\b(?:articles?|publications?|sources?|references?)\b",
    )
    forbidden = (
        r"\bne\s+.*\b(?:recherche|chercher|rechercher)\b.*\b(?:pas|plus|jamais)\b",
        r"\bsans\s+(?:nouvelle?\s+)?recherche\b",
        r"\bpas\s+de\s+(?:nouvelle?\s+)?recherche\b",
    )
    for clause in _clauses(text):
        if any(re.search(pattern, clause, re.I) for pattern in forbidden):
            continue
        if any(re.search(pattern, clause, re.I) for pattern in patterns):
            return True
    return False


def requests_scientific_strengthening(value: str | None) -> bool:
    """Détecte une demande positive de RENFORCEMENT scientifique, sans titre métier.

    Ce n'est pas un classifieur de domaine : on reconnaît uniquement l'action
    éditoriale/scientifique demandée par le consultant.
    """

    text = _norm(value)
    if not text:
        return False
    positive = (
        r"\brenforc\w*\b[^.!?;]{0,110}\bscientifi\w*\b",
        r"\bscientifi\w*\b[^.!?;]{0,110}\brenforc\w*\b",
        r"\benrich\w*\b[^.!?;]{0,110}\bscientifi\w*\b",
        r"\bscientifi\w*\b[^.!?;]{0,110}\benrich\w*\b",
        r"\b(?:argument|argumentation|justification|preuve)\w*\b[^.!?;]{0,90}\bscientifi\w*\b",
        r"\betay\w*\b[^.!?;]{0,90}\b(?:scientifi\w*|sources?|articles?|publications?)\b",
        r"\b(?:plus|davantage)\s+(?:solide|defendable|argumente)\w*\b[^.!?;]{0,90}\bscientifi\w*\b",
    )
    negated = (
        r"\bne\s+renforc\w*\s+pas\b",
        r"\bsans\s+renforc\w*\b",
        r"\baucun\w*\b[^.!?;]{0,50}\bargument\w*\s+scientifi\w*\b",
    )
    for clause in _clauses(text):
        if any(re.search(pattern, clause, re.I) for pattern in negated):
            continue
        if any(re.search(pattern, clause, re.I) for pattern in positive):
            return True
    return False


def _intent_names(intents: Iterable[Any] | None) -> set[str]:
    result: set[str] = set()
    for item in intents or []:
        value = getattr(item, "value", item)
        text = str(value or "").strip().casefold()
        if text:
            result.add(text)
    return result


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
    """Politique source de vérité pour EnnoAmel.

    Règles :
    - recherche explicitement demandée => TOUJOURS nouvelle recherche ;
    - renforcement scientifique => TOUJOURS nouvelle recherche ;
    - anciennes sources => réutilisées uniquement sur demande explicite ;
    - interdiction de recherche => aucune relance ;
    - rédaction après validation ("rédige", "continue") => ne relance pas si le
      message courant ne redemande pas un renforcement/recherche.
    """

    if hard_forbid_scholar:
        return FreshResearchDecision(
            MODE_NONE,
            "EnnoScholar est explicitement interdit par le consultant.",
        )

    explicit_fresh = explicitly_requests_fresh_research(instruction)
    explicit_reuse = explicitly_requests_existing_validated_sources(instruction)
    strengthening = requests_scientific_strengthening(instruction)
    names = _intent_names(intents)

    # Une vraie commande de nouvelle recherche gagne sur toute mémoire de session.
    if explicit_fresh:
        return FreshResearchDecision(
            MODE_FRESH,
            "Le consultant demande explicitement une nouvelle recherche ciblée.",
            explicit_reuse=explicit_reuse,
            explicit_fresh=True,
            scientific_strengthening=strengthening,
        )

    # Une demande explicite de réutilisation est la seule voie autorisée pour
    # éviter une nouvelle recherche lors d'un renforcement scientifique.
    if explicit_reuse:
        return FreshResearchDecision(
            MODE_REUSE,
            "Le consultant demande explicitement d'utiliser les sources déjà gardées/validées.",
            explicit_reuse=True,
            explicit_fresh=False,
            scientific_strengthening=strengthening,
        )

    if hard_forbid_research:
        return FreshResearchDecision(
            MODE_NONE,
            "Le consultant interdit toute nouvelle recherche.",
            explicit_reuse=False,
            explicit_fresh=False,
            scientific_strengthening=strengthening,
        )

    if editorial_only:
        return FreshResearchDecision(
            MODE_NONE,
            "La demande est purement éditoriale.",
        )

    # La demande de renforcement scientifique est toujours un nouveau cycle de
    # recherche. Les anciennes preuves ne court-circuitent jamais ce cycle.
    if strengthening:
        return FreshResearchDecision(
            MODE_FRESH,
            "Un renforcement scientifique ouvre toujours une nouvelle recherche ciblée.",
            scientific_strengthening=True,
        )

    if "research" in names:
        return FreshResearchDecision(
            MODE_FRESH,
            "L'intention RESEARCH impose une nouvelle recherche ciblée.",
            explicit_fresh=True,
        )

    if "scientific_enrichment" in names and needs_scholar:
        return FreshResearchDecision(
            MODE_FRESH,
            "L'intention SCIENTIFIC_ENRICHMENT ouvre un nouveau cycle de recherche.",
            scientific_strengthening=True,
        )

    # Un choix technique mémorisé par le frontend n'est réutilisé que lorsque
    # le message courant ne demande PAS un nouveau renforcement.
    if current_choice == "use_existing_sources":
        return FreshResearchDecision(
            MODE_REUSE,
            "Le choix courant demande d'utiliser les sources déjà validées.",
            explicit_reuse=True,
        )

    # Important : le fait qu'une section soit un état de l'art et que Scholar
    # soit utile ne suffit pas à relancer une recherche lors d'un simple
    # "rédige/continue" après validation humaine.
    return FreshResearchDecision(
        MODE_NONE,
        "Aucune nouvelle recherche n'est demandée dans le message courant.",
    )
