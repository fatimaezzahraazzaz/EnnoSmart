from __future__ import annotations

import difflib
import json
import re
import unicodedata
from typing import Any

from modules.LLM.llm_client import LLMClient

from ..domain.models import (
    AuditFinding,
    ImprovementIntent,
    ImprovementRequest,
    RoutingDecision,
    SectionFunction,
)
from .evidence_coverage_v315 import (
    build_coverage_report,
    build_mandatory_scholar_payload,
    render_mandatory_evidence_contract,
)
from .section_improvement_policy import render_section_improvement_contract
from .document_structure_service import immutable_document_blocks
from .markdown_service import normalize_llm_markdown_output


_NUMBERED_HEADING_RE = re.compile(
    r"(?m)^\s*(?:#{1,6}\s*)?((?:\d+\.)+)\s*[^\n]+?\s*$"
)
_REFERENCE_ENTRY_RE = re.compile(
    r"(?m)^\s*\[?(\d{1,3})\]?[.)]?\s+(?=[A-ZÀ-ÖØ-Ý])"
)
_FIGURE_TABLE_RE = re.compile(
    r"(?i)\b(figure|fig\.?|tableau|table)\s*([0-9]+[a-z]?)"
)
_MEASURE_RE = re.compile(
    r"(?i)(?<!\w)(\d+(?:[.,]\d+)?)\s*(%|°|db|ghz|mhz|khz|hz|mm|cm|km)"
    r"(?=$|[\s.,;:)\]])"
)
_CITATION_ID_RE = re.compile(r"(?<![A-Za-z0-9])A\d+(?![A-Za-z0-9])", re.I)


_EDITORIAL_SCOPE_RISK_TERMS = {
    "actuel", "actuelle", "actuels", "actuelles", "actuellement",
    "futur", "future", "futurs", "futures", "desormais",
    "recent", "recente", "recents", "recentes",
    "ancien", "ancienne", "anciens", "anciennes",
    "principal", "principale", "principaux", "principales",
    "majeur", "majeure", "majeurs", "majeures",
    "essentiel", "essentielle", "essentiels", "essentielles",
    "critique", "critiques", "significatif", "significative",
    "significatifs", "significatives", "indispensable", "indispensables",
    "robuste", "robustes", "systematique", "systematiques",
    "total", "totale", "totaux", "totales", "totalement",
    "garantit", "garantie", "garanties", "garantissent",
    "certain", "certaine", "certains", "certaines", "toujours", "jamais",
}

_MARKDOWN_HEADING_LINE_RE = re.compile(r"^[ \t]*#{1,6}[ \t]+\S")
_NUMBERED_HEADING_LINE_RE = re.compile(r"^[ \t]*\d+(?:\.\d+){1,6}\.?[ \t]+\S")

# V3.0 — garde lexicale générique du mode éditorial strict.
# Cette liste ne contient aucun terme métier/projet : uniquement des mots-outils
# et connecteurs français autorisés pour améliorer la forme sans enrichir le fond.
_EDITORIAL_GLUE_WORDS = {
    "a", "afin", "ainsi", "alors", "au", "aucun", "aucune", "aux", "avec",
    "ce", "ces", "cet", "cette", "cependant", "chez", "comme", "dans", "de",
    "des", "donc", "du", "elle", "elles", "en", "entre", "est", "et", "etre",
    "eux", "il", "ils", "la", "le", "les", "leur", "leurs", "lors", "mais",
    "meme", "ne", "ni", "notamment", "nous", "ou", "par", "pas", "plus",
    "pour", "que", "qui", "sa", "sans", "se", "ses", "si", "son", "sont",
    "sous", "sur", "tandis", "toutefois", "tout", "tous", "toute", "toutes",
    "un", "une", "vers", "via", "y", "d", "l", "n", "s", "qu",
}

_EDITORIAL_SUFFIXES = (
    "ements", "ement", "ations", "ation", "itions", "ition", "iques", "ique",
    "euses", "euse", "eurs", "eur", "ives", "ive", "ifs", "if", "ées", "ée",
    "és", "é", "es", "s", "x", "er", "ir", "re", "ent", "ant", "ait", "aient",
)


def _lexical_normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", normalized.casefold())


def _lexical_stem(value: str) -> str:
    token = _lexical_normalize(value)
    if len(token) <= 4:
        return token
    for suffix in _EDITORIAL_SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            return token[: -len(suffix)]
    return token


def _content_tokens(value: str) -> list[str]:
    return re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'-]{2,}", str(value or ""))


def _editorial_lexical_risks(original: str, candidate: str) -> list[str]:
    """Repère les ajouts lexicaux à risque dans une révision *style only*.

    L'objectif n'est pas de juger la qualité stylistique mais de rendre visible
    toute précision qui n'a pas d'ancrage lexical/morphologique dans la source.
    Les connecteurs et mots-outils restent autorisés.
    """

    source_tokens = {_lexical_normalize(t) for t in _content_tokens(original)}
    source_stems = {_lexical_stem(t) for t in _content_tokens(original)}
    risks: list[str] = []

    # Toute expansion parenthétique nouvellement introduite après un acronyme est
    # interdite en mode strict (définition/traduction implicite).
    source_pairs = {
        (_lexical_normalize(a), _lexical_normalize(b))
        for a, b in re.findall(r"\b([A-Z][A-Z0-9-]{1,9})\s*\(([^)\n]{2,100})\)", original)
    }
    for acronym, expansion in re.findall(
        r"\b([A-Z][A-Z0-9-]{1,9})\s*\(([^)\n]{2,100})\)", candidate
    ):
        key = (_lexical_normalize(acronym), _lexical_normalize(expansion))
        if key not in source_pairs:
            risks.append(f"expansion_acronyme:{acronym}({expansion.strip()})")

    seen: set[str] = set()
    for token in _content_tokens(candidate):
        norm = _lexical_normalize(token)
        if not norm or norm in _EDITORIAL_GLUE_WORDS or norm in seen:
            continue
        seen.add(norm)
        if norm in source_tokens:
            continue
        stem = _lexical_stem(token)
        if stem and stem in source_stems:
            continue
        # Une proximité morphologique forte est acceptée (accord/conjugaison).
        if len(stem) >= 5 and any(
            min(len(stem), len(src)) >= 5
            and (stem.startswith(src) or src.startswith(stem))
            for src in source_stems
        ):
            continue
        risks.append(f"terme_nouveau:{token}")

    return risks[:40]


def _strict_editorial_repair_prompt(original: str, candidate: str, risks: list[str]) -> str:
    return f"""Tu corriges une réécriture éditoriale CIR en mode FAITS STRICTEMENT CONSTANTS.

Le TEXTE SOURCE ci-dessous est l'unique source de vérité. La candidate contient encore des termes ou précisions qui ne sont pas directement ancrés dans cette source.

RÈGLE DE RÉPARATION
- Repars du TEXTE SOURCE, pas de tes connaissances et pas des faits ajoutés par la candidate.
- La candidate peut seulement t'indiquer une meilleure organisation ou fluidité.
- Supprime toute définition, traduction, développement d'acronyme, relation de provenance, temporalité, qualificatif, causalité ou précision absente du source.
- Pour les notions techniques, privilégie le vocabulaire déjà présent dans la source. Les nouveaux mots de liaison purement grammaticaux sont autorisés.
- Ne supprime aucun fait du source.
- Retourne uniquement le texte réparé.

ÉLÉMENTS À RISQUE DÉTECTÉS AUTOMATIQUEMENT
{json.dumps(risks, ensure_ascii=False)}

TEXTE SOURCE IMMUTABLE
---
{original}
---

CANDIDATE À RÉPARER
---
{candidate}
---
"""


def _normalise_marker(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


def _allowed_citation_ids(evidence: dict[str, Any]) -> set[str]:
    scholar = evidence.get("scholar") if isinstance(evidence, dict) else None
    rows = scholar.get("evidence") if isinstance(scholar, dict) else None
    return {
        str(row.get("citation_id") or "").strip().upper()
        for row in (rows or [])
        if isinstance(row, dict) and str(row.get("citation_id") or "").strip()
    }


def validate_conservative_revision(
    original: str,
    rewritten: str,
    *,
    allowed_citation_ids: set[str] | None = None,
    allow_reduction: bool = False,
    enrichment_requested: bool = False,
) -> list[str]:
    """Détecte une réécriture qui résume ou mutile silencieusement la source.

    EnnoAmelioration travaille par proposition et validation humaine. Une sortie
    qui perd des éléments protégés doit être signalée au consultant, mais elle reste
    visible comme candidate tant qu'elle n'est pas validée. Les contrôles sont
    déterministes et ne jugent pas le style ; ils servent d'alertes sur la structure,
    les preuves, les mesures et les renvois déjà présents.
    """
    issues: list[str] = []
    original_document_blocks = immutable_document_blocks(original)
    rewritten_document_blocks = immutable_document_blocks(rewritten)
    for block_id, original_block in original_document_blocks.items():
        rewritten_block = rewritten_document_blocks.get(block_id)
        if rewritten_block is None:
            issues.append(f"document_block_missing:{block_id}")
        elif rewritten_block.strip() != original_block.strip():
            issues.append(f"document_block_changed:{block_id}")

    source = str(original or "")
    proposal = str(rewritten or "")
    source_words = len(source.split())
    proposal_words = len(proposal.split())

    if source_words >= 120 and not allow_reduction:
        minimum_ratio = 1.00 if enrichment_requested else 0.82
        ratio = proposal_words / max(1, source_words)
        if ratio < minimum_ratio:
            issues.append(
                f"contraction_excessive:{ratio:.2f}<minimum_{minimum_ratio:.2f}"
            )

    source_headings = [
        _normalise_marker(match.group(1))
        for match in _NUMBERED_HEADING_RE.finditer(source)
    ]
    proposal_normalised = _normalise_marker(proposal)
    missing_headings = [
        heading for heading in source_headings if heading not in proposal_normalised
    ]
    if missing_headings:
        issues.append("titres_perdus:" + " | ".join(missing_headings[:8]))

    if not allow_reduction:
        source_references = set(_REFERENCE_ENTRY_RE.findall(source))
        proposal_references = set(_REFERENCE_ENTRY_RE.findall(proposal))
        missing_references = sorted(
            source_references - proposal_references,
            key=lambda value: int(value),
        )
        if missing_references:
            issues.append("references_perdues:" + ",".join(missing_references[:30]))

        source_visuals = {
            (_normalise_marker(kind), number.casefold())
            for kind, number in _FIGURE_TABLE_RE.findall(source)
        }
        proposal_visuals = {
            (_normalise_marker(kind), number.casefold())
            for kind, number in _FIGURE_TABLE_RE.findall(proposal)
        }
        missing_visuals = sorted(source_visuals - proposal_visuals)
        if missing_visuals:
            issues.append(
                "renvois_visuels_perdus:"
                + ",".join(f"{kind} {number}" for kind, number in missing_visuals[:20])
            )

        source_measures = {
            (number.replace(",", "."), unit.casefold())
            for number, unit in _MEASURE_RE.findall(source)
        }
        proposal_measures = {
            (number.replace(",", "."), unit.casefold())
            for number, unit in _MEASURE_RE.findall(proposal)
        }
        missing_measures = sorted(source_measures - proposal_measures)
        if missing_measures:
            issues.append(
                "mesures_perdues:"
                + ",".join(f"{number}{unit}" for number, unit in missing_measures[:25])
            )

        source_urls = set(re.findall(r"https?://[^\s)>]+", source, flags=re.I))
        proposal_urls = set(re.findall(r"https?://[^\s)>]+", proposal, flags=re.I))
        if source_urls - proposal_urls:
            issues.append(f"liens_perdus:{len(source_urls - proposal_urls)}")

    allowed = {value.upper() for value in (allowed_citation_ids or set())}
    source_citations = {value.upper() for value in _CITATION_ID_RE.findall(source)}
    proposal_citations = {value.upper() for value in _CITATION_ID_RE.findall(proposal)}
    unauthorized = sorted(proposal_citations - source_citations - allowed)
    if unauthorized:
        issues.append("citations_non_autorisees:" + ",".join(unauthorized))

    if re.search(r"(?im)^\s*preuves\s+utilis[ée]es?\s*:", proposal) and not re.search(
        r"(?im)^\s*preuves\s+utilis[ée]es?\s*:", source
    ):
        issues.append("inventaire_technique_ajoute_au_document")

    return issues


def _strip_fence(text: str) -> str:
    value = str(text or "").strip()
    fenced = re.fullmatch(r"```(?:markdown|md|text)?\s*(.*?)\s*```", value, flags=re.I | re.S)
    return fenced.group(1).strip() if fenced else value


def _match_editorial_format(original: str, rewritten: str) -> str:
    return normalize_llm_markdown_output(rewritten)


def _heading_key(value: str) -> str:
    line = re.sub(r"^[ \t]*#{1,6}[ \t]+", "", str(value or "").strip())
    return _normalise_marker(line)


def _leading_source_heading(original: str) -> str:
    for line in str(original or "").splitlines():
        if not line.strip():
            continue
        if _MARKDOWN_HEADING_LINE_RE.match(line) or _NUMBERED_HEADING_LINE_RE.match(line):
            return line.rstrip()
        return ""
    return ""


def _preserve_leading_heading(original: str, candidate: str) -> str:
    heading = _leading_source_heading(original)
    value = normalize_llm_markdown_output(candidate)
    if not heading or not value:
        return value
    wanted = _heading_key(heading)
    first_lines = [line for line in value.splitlines()[:6] if line.strip()]
    if any(_heading_key(line) == wanted for line in first_lines):
        return value
    return f"{heading}\n\n{value}".strip()


def _editorial_semantic_scope_risks(original: str, candidate: str) -> list[str]:
    source_terms = {_lexical_normalize(token) for token in _content_tokens(original)}
    candidate_terms = {_lexical_normalize(token) for token in _content_tokens(candidate)}
    risky_added = sorted(
        term for term in candidate_terms - source_terms
        if term in _EDITORIAL_SCOPE_RISK_TERMS
    )
    return [f"portee_semantique_nouvelle:{term}" for term in risky_added]



def _editorial_text_similarity(
    left: str,
    right: str,
) -> float:
    """
    Compare deux versions en ignorant seulement
    les différences d'espacement et de retours
    à la ligne.

    Cette fonction permet notamment de détecter
    qu'un contrôle sémantique est revenu presque
    mot pour mot au texte source.
    """

    left_value = re.sub(
        r"\s+",
        " ",
        normalize_llm_markdown_output(left),
    ).strip().casefold()

    right_value = re.sub(
        r"\s+",
        " ",
        normalize_llm_markdown_output(right),
    ).strip().casefold()

    return difflib.SequenceMatcher(
        a=left_value,
        b=right_value,
    ).ratio()


def _bounded_json_value(
    value: Any,
    *,
    string_limit: int,
    list_limit: int,
    depth: int = 0,
) -> Any:
    """Réduit un payload tout en conservant un JSON syntaxiquement valide."""

    if depth >= 7:
        return "[profondeur tronquée]"
    if isinstance(value, str):
        return value if len(value) <= string_limit else value[:string_limit] + "…"
    if isinstance(value, dict):
        return {
            str(key): _bounded_json_value(
                child,
                string_limit=string_limit,
                list_limit=list_limit,
                depth=depth + 1,
            )
            for key, child in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        rows = list(value)
        output = [
            _bounded_json_value(
                child,
                string_limit=string_limit,
                list_limit=list_limit,
                depth=depth + 1,
            )
            for child in rows[:list_limit]
        ]
        if len(rows) > list_limit:
            output.append({"_truncated_items": len(rows) - list_limit})
        return output
    return value


def _compact_json(value: Any, limit: int) -> str:
    """Sérialise sans jamais couper une chaîne JSON au milieu."""

    raw = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    if len(raw) <= limit:
        return raw
    for string_limit, list_limit in (
        (2400, 24),
        (1400, 16),
        (800, 10),
        (400, 7),
        (220, 5),
        (100, 3),
    ):
        compacted = _bounded_json_value(
            value, string_limit=string_limit, list_limit=list_limit
        )
        raw = json.dumps(
            compacted, ensure_ascii=False, default=str, separators=(",", ":")
        )
        if len(raw) <= limit:
            return raw
    return json.dumps(
        {
            "_truncated": True,
            "summary": "Payload trop volumineux pour le budget du prompt.",
            "top_level_keys": list(value)[:30] if isinstance(value, dict) else [],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


_SECTION_BLUEPRINT_KEYS = {
    SectionFunction.CONTEXT: "positionnement_scientifique_du_verrou",
    SectionFunction.SCIENTIFIC_LANDSCAPE: "travaux_existants_directement_lies",
    SectionFunction.UNCERTAINTY: "gap_scientifique_technique",
    SectionFunction.LIMITATION: "limites_de_l_etat_de_l_art",
    SectionFunction.CONTRIBUTION: "synthese_cir_exploitable",
}


def _select_cir_style_guidance(
    evidence: dict[str, Any], routing: RoutingDecision
) -> dict[str, Any]:
    """Sélectionne un petit guide CIR sûr, distinct des preuves factuelles."""

    memory = evidence.get("cir_style") if isinstance(evidence, dict) else None
    if not isinstance(memory, dict) or not memory.get("available"):
        return {
            "available": False,
            "usage": "neutral_consultant_style_fallback",
            "selected_pattern_ids": [],
        }

    profile = memory.get("style_profile") or {}
    argumentation = memory.get("argumentation_profile") or {}
    argumentative = bool(
        set(routing.intents)
        & {ImprovementIntent.ARGUMENTATION, ImprovementIntent.CIR_ELIGIBILITY}
    )

    writing_rules = [str(row) for row in profile.get("writing_rules") or []]
    if not argumentative:
        # En reformulation pure, les canevas « verrou/gap/travaux » ne doivent
        # jamais imposer du fond absent. On conserve le ton et les règles de
        # prudence/forme réellement réutilisables.
        reusable = [
            row
            for row in writing_rules
            if re.search(
                r"prud|ton|structur|transition|niveau|ne jamais|uniquement|"
                r"mémoire|memory|copi|cit",
                row,
                flags=re.I,
            )
        ]
        writing_rules = reusable or writing_rules

    selected_patterns: list[dict[str, Any]] = []
    if argumentative:
        for row in argumentation.get("reasoning_patterns") or profile.get(
            "reasoning_patterns"
        ) or []:
            if not isinstance(row, dict):
                continue
            selected_patterns.append(
                {
                    "pattern_id": str(row.get("pattern_id") or ""),
                    "label": str(row.get("label") or ""),
                    "steps": list(row.get("steps") or [])[:6],
                    "usage": "reasoning_structure_only",
                }
            )
            if len(selected_patterns) >= 3:
                break

    blueprint_key = _SECTION_BLUEPRINT_KEYS.get(routing.section_function)
    blueprints = argumentation.get("section_blueprints") or {}
    selected_blueprint = (
        dict(blueprints.get(blueprint_key) or {})
        if argumentative and blueprint_key
        else {}
    )
    fewshots = [
        {
            "role": str(row.get("role") or ""),
            "input_hint": str(row.get("input_hint") or "")[:350],
            "output_style_example": str(row.get("output_style_example") or "")[:900],
        }
        for row in (memory.get("fewshot_templates") or [])[:2]
        if isinstance(row, dict)
    ]
    pattern_ids = [
        str(row.get("pattern_id"))
        for row in selected_patterns
        if row.get("pattern_id")
    ]
    pattern_ids.extend(
        f"writing_rule_{index}"
        for index, _ in enumerate(writing_rules[:8], start=1)
    )
    if blueprint_key and selected_blueprint:
        pattern_ids.append(f"section_blueprint:{blueprint_key}")

    return {
        "available": True,
        "usage": "style_and_reasoning_structure_only",
        "fact_eligible": False,
        "can_be_cited": False,
        "tone": profile.get("tone") or "consultant CIR/R&D prudent",
        "tone_details": list(profile.get("tone_details") or [])[:6],
        "style_constraints": dict(profile.get("style_constraints") or {}),
        "writing_rules": writing_rules[:8],
        "reasoning_patterns": selected_patterns,
        "section_blueprint": selected_blueprint,
        "fewshot_templates": fewshots,
        "selected_pattern_ids": pattern_ids,
        "application_rule": (
            "Appliquer seulement les règles compatibles avec la fonction de la section "
            "et les faits présents. Ne jamais compléter un placeholder par supposition."
        ),
    }


def _strict_editorial_review_prompt(original: str, candidate: str, risks: list[str] | None = None) -> str:
    return f"""Tu es un contrôleur de fidélité éditoriale pour un dossier CIR.

Ta mission n'est PAS d'améliorer davantage le fond. Tu dois contrôler une proposition de réécriture par rapport au texte source immuable et corriger uniquement les dérives de sens.

RÈGLE ABSOLUE
Chaque information factuelle, technique ou relationnelle de la VERSION À CONTRÔLER doit être directement soutenue par le TEXTE SOURCE. Si une formulation est seulement plausible, plus précise, plus forte ou voisine du sens source, elle doit être ramenée au niveau exact du texte source.

INTERDICTIONS
- N'ajoute aucune information issue de tes connaissances générales.
- N'ajoute ni définition, ni traduction, ni développement d'acronyme qui n'apparaît pas déjà dans le texte source.
- Ne remplace pas une expression technique par un quasi-synonyme susceptible de changer sa portée, son mode de fonctionnement, sa temporalité, sa précision ou son niveau de certitude.
- N'ajoute aucune relation de provenance, d'appartenance, de causalité, d'objectif, de garantie, d'antériorité ou de conséquence qui n'est pas explicitement portée par le texte source.
- N'ajoute aucun qualificatif qui augmente ou précise la portée d'un fait : caractère originel, temps réel, robustesse, caractère majeur, fourniture par un organisme, etc., sauf si cette précision figure déjà dans le texte source.
- Ne supprime aucun fait technique du texte source. Une fusion de phrases est autorisée uniquement si tous les faits restent présents.
- Ne change aucun nombre, unité, nom propre, jeu de données, acronyme, technologie ou relation logique.

CE QUI EST AUTORISÉ
- correction grammaticale et orthographique ;
- amélioration de la fluidité ;
- transitions purement rédactionnelles ;
- fusion ou scission de phrases sans ajout ni perte de sens ;
- réorganisation locale uniquement lorsque les relations logiques restent identiques.

PROCÉDURE
1. Compare silencieusement chaque proposition avec le texte source.
2. Supprime ou reformule toute micro-information non directement traçable au texte source.
3. Conserve les améliorations de style qui n'altèrent pas le sens.
4. Retourne UNIQUEMENT la version corrigée en Markdown propre, sans commentaire et sans fence de code.
5. Conserve exactement les titres/sous-titres et la structure Markdown déjà présents dans la source.
6. N'écris jamais d'entité HTML technique telle que &nbsp; ou &#x20; : utilise des espaces et retours Markdown normaux.
7. Corrige seulement les dérives signalées ; conserve les améliorations de fluidité qui restent fidèles à la source.
8. Ne retourne JAMAIS simplement le texte source mot pour mot pour supprimer un risque.
9. Corrige uniquement les phrases contenant les dérives signalées.
10. Toutes les autres améliorations rédactionnelles de la candidate doivent être conservées.
11. Une correction de temporalité, d'intensité ou de portée doit rester locale : elle ne doit pas annuler le travail rédactionnel réalisé sur le reste de la section.

TEXTE SOURCE IMMUTABLE
---
{original}
---

TERMES/EXPRESSIONS NOUVELLES À CONTRÔLER EN PRIORITÉ
{json.dumps(risks or [], ensure_ascii=False)}

VERSION À CONTRÔLER
---
{candidate}
---
"""


def _scientific_additive_mode(routing: RoutingDecision) -> bool:
    """Renforcement scientifique = enrichissement additif, hors concision/correction ciblée."""
    intent_values = {str(getattr(intent, "value", intent)) for intent in routing.intents}
    return bool(
        routing.needs_scholar
        and "scientific_enrichment" in intent_values
        and "concision" not in intent_values
        and not routing.candidate_revision
    )


def _scientific_text_similarity(left: str, right: str) -> float:
    left_value = re.sub(
        r"\s+",
        " ",
        normalize_llm_markdown_output(left),
    ).strip().casefold()
    right_value = re.sub(
        r"\s+",
        " ",
        normalize_llm_markdown_output(right),
    ).strip().casefold()
    return difflib.SequenceMatcher(a=left_value, b=right_value).ratio()


def _scientific_additive_review_prompt(
    original: str,
    candidate: str,
    allowed_citations: list[str],
) -> str:
    return f"""Tu es le contrôleur final d'un RENFORCEMENT SCIENTIFIQUE ADDITIF pour un dossier CIR.

OBJECTIF
La version candidate doit conserver TOUT le contenu informatif du texte source et
l'enrichir avec les arguments scientifiques déjà introduits depuis les sources validées.

RÈGLE ABSOLUE : SOURCE + AJOUTS, JAMAIS SOURCE REMPLACÉE PAR AJOUTS
- Aucun fait, argument, exemple, réserve, condition, nom propre, chiffre, jeu de données,
  relation logique ou précision technique du TEXTE SOURCE ne doit disparaître.
- Une information source peut être reformulée localement pour la grammaire ou la fluidité,
  mais elle doit rester explicitement reconnaissable et de portée équivalente.
- Ne résume pas le texte source.
- Ne compresse pas deux phrases sources si cette fusion risque de faire apparaître une
  suppression dans le comparatif ou de rendre une information moins explicite.
- Conserve l'ordre logique des informations source.
- Les nouveaux arguments scientifiques doivent être AJOUTÉS après ou autour du passage
  qu'ils renforcent ; ils ne doivent pas remplacer le passage source.
- Une citation scientifique nouvelle doit porter sur un APPORT NOUVEAU issu des preuves
  validées. Ne colle pas une nouvelle citation sur une phrase qui ne fait que reformuler
  une information déjà présente dans la source.
- Si un article validé n'apporte rien d'utile à ce passage, ne l'utilise pas artificiellement.
- Conserve tous les identifiants de citation scientifique déjà présents dans la candidate,
  sauf s'ils sont manifestement dupliqués à l'identique.
- N'ajoute aucune autre source et aucun identifiant de citation non autorisé.
- N'ajoute pas de liste finale "Preuves utilisées".
- Ne supprime pas un argument source sous prétexte qu'un article fournit un argument
  scientifiquement plus fort : garde le source puis ajoute l'argument étayé séparément.

MÉTHODE DE CONTRÔLE
1. Compare silencieusement chaque unité d'information du TEXTE SOURCE à la candidate.
2. Repère toute information source absente, condensée au point de perdre sa précision,
   ou remplacée par un nouvel argument.
3. Réinsère cette information au bon endroit avec le minimum de modification.
4. Conserve les nouveaux arguments et citations valides de la candidate.
5. Retourne uniquement la version réparée, sans commentaire.

CITATIONS AUTORISÉES
{", ".join(allowed_citations) if allowed_citations else "Aucune"}

TEXTE SOURCE — CONTENU À PRÉSERVER
---
{original}
---

CANDIDATE ENRICHIE — À RÉPARER SI NÉCESSAIRE
---
{candidate}
---
"""


def _scientific_additive_review_is_safe(
    original: str,
    candidate: str,
    reviewed: str,
) -> tuple[bool, list[str]]:
    """Empêche le contrôleur de supprimer l'enrichissement ou de revenir au source."""
    reasons: list[str] = []
    reviewed_value = normalize_llm_markdown_output(reviewed)
    if not reviewed_value:
        return False, ["review_empty"]

    source_words = len(normalize_llm_markdown_output(original).split())
    candidate_words = len(normalize_llm_markdown_output(candidate).split())
    reviewed_words = len(reviewed_value.split())

    if reviewed_words < max(int(source_words * 0.98), int(candidate_words * 0.88)):
        reasons.append(
            f"review_too_short:{reviewed_words}<source={source_words},candidate={candidate_words}"
        )

    source_similarity_candidate = _scientific_text_similarity(original, candidate)
    source_similarity_reviewed = _scientific_text_similarity(original, reviewed_value)
    if source_similarity_reviewed >= 0.995 and source_similarity_candidate < 0.995:
        reasons.append("review_collapsed_to_source")

    source_citations = {
        value.upper() for value in _CITATION_ID_RE.findall(original)
    }
    candidate_new_citations = {
        value.upper() for value in _CITATION_ID_RE.findall(candidate)
    } - source_citations
    reviewed_new_citations = {
        value.upper() for value in _CITATION_ID_RE.findall(reviewed_value)
    } - source_citations

    missing_candidate_citations = sorted(
        candidate_new_citations - reviewed_new_citations
    )
    if missing_candidate_citations:
        reasons.append(
            "review_lost_validated_citations:"
            + ",".join(missing_candidate_citations)
        )

    return not reasons, reasons



def _merge_generation_meta(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    merged = dict(second or first or {})
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        merged[key] = int((first or {}).get(key) or 0) + int((second or {}).get(key) or 0)
    merged["writer_internal_call_count"] = 2
    merged["strict_editorial_review_applied"] = True
    merged["strict_editorial_first_pass"] = dict(first or {})
    merged["strict_editorial_review_pass"] = dict(second or {})
    return merged


class ControlledWriter:
    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or LLMClient()

    def rewrite(
        self,
        request: ImprovementRequest,
        routing: RoutingDecision,
        audit: list[AuditFinding],
        evidence: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        target = request.target_text.strip()
        word_count = max(1, len(target.split()))
        max_output_tokens = min(8000, max(900, int(word_count * 2.2)))
        allowed_citations = sorted(_allowed_citation_ids(evidence))
        style_guidance = _select_cir_style_guidance(evidence, routing)
        style_memory = evidence.get("cir_style") if isinstance(evidence, dict) else None
        if isinstance(style_memory, dict):
            style_memory["guidance_injected"] = bool(style_guidance.get("available"))
            style_memory["selected_pattern_ids"] = list(
                style_guidance.get("selected_pattern_ids") or []
            )
        project_evidence = {
            "project_context": evidence.get("project_context") or {},
            "constraints": evidence.get("constraints") or {},
        }
        diagnostic_evidence = {
            "diagnostic": evidence.get("diagnostic") or {},
            "orchestration": evidence.get("diagnostic_orchestration") or {},
        }
        scholar_evidence = evidence.get("scholar") or {}
        mandatory_evidence_contract = render_mandatory_evidence_contract(evidence)
        mandatory_scholar_payload = build_mandatory_scholar_payload(evidence, max_chars=30000)
        conservative_mode = not any(
            intent.value == "concision" for intent in routing.intents
        )
        scientific_additive_mode = _scientific_additive_mode(routing)
        context_structural_mode = bool(
            routing.section_function == SectionFunction.CONTEXT
            and not routing.candidate_revision
            and conservative_mode
        )
        uncertainty_evidence_mode = bool(
            routing.section_function == SectionFunction.UNCERTAINTY
            and not routing.candidate_revision
            and conservative_mode
        )
        if context_structural_mode:
            preservation_contract = """MODE RESTRUCTURATION CONTEXTE À FAITS CONSTANTS
- Le matériau à préserver est constitué des FAITS, relations logiques, limites, réserves et précisions techniques de la source ; les phrases de la source ne sont pas des unités à conserver ou paraphraser une par une.
- Réécris la section au niveau des paragraphes et des blocs d'idées. Sur un texte développé, vise en général 3 à 6 blocs cohérents selon le contenu réellement disponible, sans créer artificiellement un bloc manquant.
- Chaque bloc doit avoir une fonction argumentative claire parmi celles réellement présentes : poser le contexte, introduire le besoin, expliciter une contrainte, présenter une piste, exposer sa limite, conduire à la motivation des travaux.
- Fusionne les phrases adjacentes qui expriment la même idée et supprime les répétitions de formulation. Une phrase peut disparaître comme unité rédactionnelle si TOUS ses faits sont conservés ailleurs dans le même bloc.
- Rends explicite la chaîne cause → conséquence déjà contenue dans le texte à l'aide de transitions naturelles, mais n'invente jamais une causalité, une nécessité, un enjeu ou une portée qui n'est pas documenté.
- Ne fais pas une simple substitution de synonymes phrase par phrase. L'amélioration attendue doit être perceptible dans l'organisation du raisonnement, pas seulement dans le vocabulaire.
- Tu peux légèrement raccourcir les détours descriptifs ou les répétitions ; ne raccourcis jamais en supprimant une information technique utile, une réserve, une condition, un exemple factuel, un nom propre, un chiffre, une unité ou une relation logique.
- N'intensifie pas le texte : n'ajoute pas spontanément « crucial », « majeur », « significatif », « robuste », « essentiel », « garantit », « démontre » ou un équivalent si ce niveau d'affirmation n'est pas déjà présent ou prouvé.
- Conserve le niveau de précision exact : ne transforme pas « résolution du système » en un type de résolution particulier, « un logiciel » en « plusieurs logiciels », ni une possibilité en certitude.
- Conserve intégralement titres/sous-titres, références bibliographiques, citations, chiffres, unités, renvois aux figures/tableaux et liens présents.
- N'ajoute aucune preuve, citation ou information scientifique simplement pour rendre le contexte plus convaincant. Le renforcement vient d'abord de la structure et de l'enchaînement des faits existants.
- Si la source termine déjà par la motivation des travaux, conserve cette fonction de conclusion et rends la transition plus nette sans ajouter un nouveau verrou ou une nouvelle contribution.
"""
        elif uncertainty_evidence_mode:
            preservation_contract = """MODE VERROU / INCERTITUDE — ARGUMENTATION ÉTAYÉE
- Le but est de rendre l'incertitude plus lisible et défendable, pas de rendre le vocabulaire plus spectaculaire.
- Réécris au niveau des blocs d'idées. Vise une progression du type : observation documentée → origine de la difficulté → conséquence/risque documenté → ce qui reste non maîtrisé → pourquoi une investigation est nécessaire. N'utilise que les maillons réellement soutenus.
- Distingue trois niveaux et ne les mélange jamais : (1) fait observé dans le projet ; (2) interprétation prudente déduite directement de ces faits ; (3) limite scientifique générale soutenue par une source Scholar validée.
- Une preuve EnnoDiagnostic peut étayer ce qui a été observé, testé, rencontré ou mesuré dans le projet. Une preuve EnnoScholar peut étayer ce que la littérature établit sur une limite, un écart de domaine, une difficulté de généralisation ou l'insuffisance d'une approche publiée ; elle ne prouve jamais qu'un fait s'est produit dans le projet.
- Pour montrer le caractère non immédiat/non trivial, n'invente jamais l'échec de méthodes standards. Tu peux l'expliquer uniquement si les preuves montrent explicitement au moins un de ces éléments : paramètre impossible à déterminer a priori, variabilité non maîtrisée, méthode testée insuffisante, résultat dépendant de plusieurs facteurs, itérations/essais nécessaires.
- N'ajoute pas spontanément des exemples de solutions prétendument insuffisantes (« plus de bruit », « augmentation standard », « simple réglage », etc.) si elles ne figurent pas dans le texte ou une preuve.
- N'ajoute pas de protocole, métrique, plan d'expérience ou méthode future détaillée si ce contenu appartient à une autre section. Ici, il suffit d'établir pourquoi l'investigation est nécessaire.
- Évite les intensificateurs ou promesses non prouvés : « majeur », « crucial », « indispensable », « garantit », « robustesse garantie », « efficacité en conditions réelles », etc.
- Quand une preuve est partielle, écris une formulation bornée (« peut », « constitue un risque », « ne permet pas d'établir a priori ») plutôt qu'une certitude.
- Préserve tous les faits, chiffres, noms, exemples et réserves de la source. Tu peux fusionner les phrases redondantes si aucun fait n'est perdu.
- Si des citations Scholar validées sont autorisées, place-les uniquement après l'affirmation scientifique qu'elles étayent et n'en ajoute pas à une phrase décrivant un fait interne au projet.
"""
        else:
            preservation_contract = (
                """MODE DE RÉVISION CONSERVATEUR
- « Améliorer » ne signifie ni résumer ni remplacer le fond existant.
- Préserve toutes les informations utiles, mais ne considère pas chaque phrase comme une unité immuable : fusionne ou scinde localement lorsque cela améliore réellement la cohérence.
- La sortie doit rester d'un niveau de détail comparable à l'entrée lorsque la demande vise à renforcer l'argumentation.
- Conserve intégralement tous les titres et sous-titres, références bibliographiques, citations, chiffres, unités, renvois aux figures/tableaux et liens présents.
- N'ajoute pas de liste finale « Preuves utilisées » : intègre chaque citation autorisée au passage exact qu'elle étaye.
- Identifie silencieusement les blocs d'idées, puis réécris chaque bloc en conservant leur ordre logique et leurs dépendances.
- Tu peux fusionner ou scinder des phrases à l'intérieur d'un même bloc thématique, mais ne déplace pas une idée dans un autre bloc et ne permute pas cause, conséquence, solution et limite.
- Préserve le niveau de précision factuel : un terme générique ne doit pas devenir plus spécifique sans preuve ; un qualificatif, une dimension ou une catégorie ne doit pas être remplacé par un concept voisin.
- Si une précision technique de l'original n'est pas nécessaire à la fluidité, reformule-la mais ne la remplace jamais par une autre précision.
"""
                if conservative_mode
                else """MODE DE RÉVISION CONCISE DEMANDÉ PAR LE CONSULTANT
- La réduction est autorisée, mais les faits, chiffres, réserves, citations et limites scientifiques doivent rester exacts.
"""
            )

        scientific_additive_contract = (
            """MODE RENFORCEMENT SCIENTIFIQUE ADDITIF — SOURCE + ARGUMENTS VALIDÉS
- Le texte source constitue la COLONNE VERTÉBRALE de la version améliorée.
- Ne supprime aucun fait, argument, exemple, réserve, condition, chiffre, nom propre,
  jeu de données, relation logique ou précision technique présent dans la source.
- Le renforcement ne remplace jamais le contenu source : il AJOUTE des arguments
  scientifiques issus des seules preuves Scholar validées.
- Garde les informations source dans le même ordre logique. Corrige la grammaire ou
  la fluidité localement, mais n'efface pas une phrase en absorbant son contenu dans
  une autre si cela rend l'information moins explicite.
- Pour faciliter la traçabilité, évite de fusionner plusieurs phrases source en une
  seule. Chaque information source doit rester reconnaissable dans le comparatif.
- Insère les nouveaux arguments scientifiques immédiatement après le passage qu'ils
  renforcent, dans des phrases distinctes lorsque c'est possible.
- Une nouvelle citation [A#] doit être attachée à l'ARGUMENT NOUVEAU qu'elle étaye.
  Ne transforme pas une simple reformulation d'un fait source en phrase "scientifique"
  uniquement en lui ajoutant [A#].
- Ne cite pas toutes les sources sélectionnées par principe : utilise uniquement une
  source lorsqu'elle apporte un élément réellement utile au raisonnement.
- Respecte target_bindings et allowed_claim_scope pour chaque source : une preuve liée
  à un verrou, un passage ou une section ne peut étayer aucun autre passage.
- Dans une section contenant plusieurs verrous, insère chaque apport seulement à
  proximité du passage correspondant à son research_target_id ou parent_section_id.
- Si aucune liaison cible n'autorise l'usage dans le passage courant, n'utilise pas la
  citation, même si l'article appartient au même domaine général.
- Si plusieurs sources étayent exactement le même apport, évite l'empilement décoratif
  de citations ; garde celles qui soutiennent effectivement l'affirmation.
- Ne remplace jamais un argument source par un argument plus moderne, plus fort ou plus
  général. Conserve l'argument source puis ajoute séparément l'apport scientifique.
- La version enrichie ne doit pas être plus courte que la source, sauf suppression
  explicitement demandée par le consultant dans son instruction.
"""
            if scientific_additive_mode
            else ""
        )
        strict_fact_contract = (
            """MODE ÉDITORIAL STRICT — FAITS CONSTANTS
- Le consultant demande une amélioration de forme, pas un enrichissement de fond.
- Utilise EXCLUSIVEMENT les faits et relations déjà présents dans le TEXTE CIBLE. Les autres sections du dossier, la mémoire CIR, les métadonnées projet et les connaissances générales ne servent pas à compléter le fond.
- Tu peux reformuler, fusionner, scinder et réordonner localement les phrases si cela améliore la lisibilité, mais chaque information de sortie doit être directement traçable à une information du texte cible.
- N'ajoute aucune définition, expansion d'acronyme, traduction, exemple, justification technique, cause, conséquence, avantage, limite, méthode, résultat ou interprétation absente du texte cible.
- En particulier, ne développe pas un acronyme entre parenthèses si son développement n'est pas déjà écrit dans le texte cible.
- N'introduis aucun terme scientifique nouveau uniquement parce qu'il est plausible dans le domaine.
- Ne supprime aucun fait technique. Si deux phrases sont fusionnées, tous leurs faits doivent rester présents dans la formulation fusionnée.
- Les améliorations attendues portent uniquement sur la syntaxe, la grammaire, la fluidité, les transitions, la clarté et la structure rédactionnelle.
"""
            if routing.strict_fact_preservation or routing.editorial_only
            else ""
        )
        section_contract = render_section_improvement_contract(routing.section_function)
        candidate_revision_contract = (
            """MODE CORRECTION DE LA PROPOSITION COURANTE
- Le TEXTE CIBLE est la proposition courante à corriger. Ne repars pas d'une autre version et ne reconstruis pas toute la section.
- Corrige uniquement les points visés par la dernière instruction du consultant. Tout passage non visé doit rester inchangé autant que possible.
- Par défaut, n'ajoute aucun nouveau paragraphe, fait, argument, exemple, résultat, chiffre, technologie, source, citation ou référence.
- N'utilise pas spontanément EnnoDiagnostic, EnnoScholar ou des preuves disponibles pour enrichir la candidate. Une preuve peut être ajoutée uniquement si le consultant le demande explicitement dans cette révision.
- Si le consultant demande de retirer une intensification, une interprétation ou une notion ajoutée, supprime-la ou reviens au niveau de précision déjà présent dans la proposition/source.
- Conserve l'organisation que le consultant demande explicitement de garder.
- Une correction grammaticale ou stylistique ne doit pas devenir une nouvelle argumentation scientifique.
- Le but de ce tour est une correction différentielle de la candidate, pas une nouvelle amélioration générale.
"""
            if routing.candidate_revision
            else ""
        )
        rd_contract = (
            """CONTRAT DE RENFORCEMENT R&D/CIR
- Ne rends jamais le dossier plus « éligible » par une simple montée en intensité du vocabulaire.
- Le rôle de la section prime : une introduction ne devient pas un verrou, une méthode ne devient pas un état de l'art, et une section de résultats ne devient pas une contribution.
- Une incertitude, une limite, un caractère non trivial, une méthode, un résultat ou une contribution ne peuvent être renforcés factuellement que s'ils sont déjà présents dans le texte ou étayés par une preuve autorisée précise.
- Si la preuve établit seulement une observation, conserve ce niveau : ne la transforme pas en verrou démontré.
- Quand un élément factuel manque, améliore la rédaction à faits constants au lieu de compléter par supposition.
- Les marqueurs [Preuve documentaire insuffisante] et [À confirmer par le consultant] ne doivent être ajoutés que si une affirmation indispensable à la logique de la section est explicitement demandée mais impossible à étayer.
"""
            if routing.needs_diagnostic or routing.needs_scholar
            else ""
        )
        prompt = f"""Tu es EnnoAmelioration, responsable de la révision finale d'un dossier CIR en français.

MISSION DU CONSULTANT
{request.instruction}

PROJET
Nom : {request.project_name or 'non précisé'}
Domaine : {request.project_domain or 'non précisé'}
Cible : {request.target_scope.value} — {request.target_section_title or 'texte sélectionné'}

CONTRAT ABSOLU
- Les PREUVES FACTUELLES sont cloisonnées par provenance : diagnostic et scholar peuvent étayer un fait ; le GUIDE RÉDACTIONNEL CIR/R&D ne guide que la forme et l'organisation du raisonnement.
- Ne transforme jamais un pattern de mémoire CIR en information factuelle, même s'il paraît pertinent pour le projet.
- Distingue toujours fait démontré, information partielle et manque documentaire. N'affirme jamais une éligibilité CIR officielle.
- Quand une information nécessaire manque, conserve une formulation prudente ; utilise si nécessaire [À confirmer par le consultant] ou [Preuve documentaire insuffisante].
- Réécris uniquement le texte cible fourni.
- Conserve tous les faits, résultats, chiffres, réserves et citations exactes.
- Conserve aussi la portée exacte des affirmations : ne spécialise pas une notion générale, ne change pas la nature d'une contrainte et ne transforme pas une possibilité en certitude.
- Les relations logiques déjà présentes (cause, conséquence, opposition, condition, limite, motivation) sont des éléments factuels à préserver autant que les chiffres.
- N'invente ni activité réalisée, ni résultat, ni source, ni éligibilité CIR.
- Une information scientifique nouvelle n'est autorisée que si elle figure dans PREUVES AUTORISÉES.
- Les termes présents uniquement dans la consigne du consultant ne constituent pas des preuves factuelles. N'ajoute pas dans le texte cible un acronyme, un chiffre, une norme, une technologie ou une référence simplement parce qu'ils apparaissent dans la consigne.
- Le fait que le consultant demande une rédaction adaptée à un dossier CIR n'autorise pas à ajouter le mot « CIR », une qualification d'éligibilité ou un vocabulaire administratif absent du texte source.
- Cite une preuve avec son citation_id existant ; ne crée jamais d'identifiant.
- Si une preuve manque, garde une formulation prudente au lieu de compléter par supposition.
- Produis une rédaction naturelle, homogène, de niveau consultant, exclusivement en français.
- Respecte le format éditorial du texte d'entrée. Si le texte est rédigé en paragraphes simples, n'ajoute ni Markdown, ni titres avec #, ni balises techniques.
- Exception impérative : tout bloc délimité par [BLOC DOCUMENT IMMUTABLE ...] et [/BLOC DOCUMENT IMMUTABLE] représente une figure ou un tableau du document source. Recopie ce bloc strictement à l'identique, au même emplacement. Ne reformule ni son marqueur, ni son contenu, ni sa légende.
- Conserve les séparations avant et après le passage afin de ne jamais fusionner deux sections ou deux paragraphes.
- Ne parle pas de l'audit, des agents, du prompt ou de tes limites dans le texte.
- Retourne seulement le texte amélioré, sans commentaire et sans bloc de code.

{preservation_contract}

{scientific_additive_contract}

{strict_fact_contract}

{candidate_revision_contract}

CONTRAT SÉMANTIQUE DE LA SECTION
{section_contract}

{rd_contract}

GUIDE RÉDACTIONNEL CIR/R&D — NON FACTUEL
- Applique réellement le ton, les règles de forme et les patterns sélectionnés ci-dessous lorsqu'ils sont compatibles avec la fonction de la section.
- En mode reformulation éditoriale, utilise surtout le ton, la syntaxe, les transitions et le niveau de prudence ; n'impose jamais un canevas de verrou, de gap ou de travaux R&D absent du texte.
- Un exemple ou un placeholder du guide ne doit jamais être recopié comme fait du projet.
{_compact_json(style_guidance, 9000)}

CONTRAT D'UTILISATION DES SOURCES SCIENTIFIQUES VALIDÉES
{mandatory_evidence_contract}

IDENTIFIANTS DE CITATION AUTORISÉS
{', '.join(allowed_citations) if allowed_citations else 'Aucun nouvel identifiant de citation autorisé.'}

INTENTIONS
{', '.join(intent.value for intent in routing.intents)}

AUDIT
{_compact_json([item.model_dump(mode='json') for item in audit], 7000)}

CONTEXTE PROJET AUTORISÉ
{_compact_json(project_evidence, 4000)}

PREUVES FACTUELLES ENNODIAGNOSTIC
{_compact_json(diagnostic_evidence, 16000)}

PREUVES FACTUELLES ENNOSCHOLAR — SOURCES CIBLÉES ET FACULTATIVES
{mandatory_scholar_payload}

MANQUES DOCUMENTAIRES SIGNALÉS
{_compact_json(evidence.get('gaps') or [], 3000)}

FORMAT DE SORTIE — MARKDOWN
- Retourne uniquement le contenu amélioré, en Markdown UTF-8.
- Ne retourne aucun commentaire avant ou après le texte.
- N'entoure jamais le résultat d'une fence de code Markdown.
- Du texte simple est déjà du Markdown valide : n'ajoute pas de décoration artificielle.
- Conserve exactement les titres et sous-titres présents.
- Conserve les listes, emphases et liens Markdown présents dans la source.
- Utilise de vrais espaces et de vrais retours de paragraphe.
- N'écris jamais d'entités HTML telles que &nbsp; ou &#x20;.
- Un paragraphe est séparé par une seule ligne vide.
- Ne fabrique pas de retour à la ligne au milieu d'une phrase.
- Conserve strictement la temporalité attachée à chaque élément.
- Ne transforme pas « futur » en « actuel » et inversement.
- N'introduis pas de qualificatif d'intensité ou de certitude absent de la source : principal, majeur, essentiel, critique, garanti, robuste, etc.

TEXTE CIBLE
---
{target}
---
"""
        output = self.llm.generate(
            prompt,
            temperature=0.12,
            max_output_tokens=max_output_tokens,
            max_input_tokens=60000,
            retries=1,
            request_name="ennoamelioration:writer:controlled_revision",
        )
        improved = _preserve_leading_heading(
            target,
            _match_editorial_format(target, _strip_fence(output)),
        )
        if not improved:
            raise RuntimeError("Le modèle de rédaction a renvoyé une proposition vide.")

        first_meta = dict(self.llm.get_last_generation_meta() or {})
        strict_editorial_mode = bool(
            (routing.editorial_only or routing.strict_fact_preservation)
            and not routing.needs_diagnostic
            and not routing.needs_scholar
            and not routing.candidate_revision
        )
        if strict_editorial_mode:
            first_risks = _editorial_lexical_risks(target, improved)
            semantic_scope_risks = _editorial_semantic_scope_risks(target, improved)
            remaining_scope_risks = list(semantic_scope_risks)
            review_attempted = False
            review_applied = False
            meta = first_meta

            if semantic_scope_risks:
                review_attempted = True
                reviewed_output = self.llm.generate(
                    _strict_editorial_review_prompt(target, improved, semantic_scope_risks),
                    temperature=0.0,
                    max_output_tokens=max_output_tokens,
                    max_input_tokens=60000,
                    retries=1,
                    request_name="ennoamelioration:writer:semantic_scope_review",
                )
                reviewed = _preserve_leading_heading(
                    target,
                    _match_editorial_format(target, _strip_fence(reviewed_output)),
                )
                reviewed_scope_risks = _editorial_semantic_scope_risks(target, reviewed)
                reviewed_vs_source = _editorial_text_similarity(
                    target,
                    reviewed,
                )

                first_candidate_vs_source = _editorial_text_similarity(
                    target,
                    improved,
                )

                review_reduces_risk = (
                    len(reviewed_scope_risks)
                    < len(semantic_scope_risks)
                )

                review_collapsed_to_source = (
                    reviewed_vs_source >= 0.995
                    and first_candidate_vs_source < 0.995
                )

                # Le contrôleur sémantique ne doit jamais
                # annuler toute l'amélioration simplement
                # pour revenir au texte source.
                #
                # La seconde version est acceptée uniquement
                # si elle réduit réellement les risques ET
                # reste une vraie version améliorée.
                if (
                    reviewed
                    and review_reduces_risk
                    and not review_collapsed_to_source
                ):
                    improved = reviewed

                    remaining_scope_risks = (
                        reviewed_scope_risks
                    )

                    second_meta = dict(
                        self.llm.get_last_generation_meta()
                        or {}
                    )

                    meta = _merge_generation_meta(
                        first_meta,
                        second_meta,
                    )

                    review_applied = True

                else:
                    # Le contrôleur est revenu trop près de
                    # l'original ou n'a pas réellement réduit
                    # les risques.
                    #
                    # On garde donc la PREMIÈRE vraie
                    # amélioration visible au consultant.
                    remaining_scope_risks = (
                        semantic_scope_risks
                    )

                    meta = first_meta

            remaining_lexical_risks = _editorial_lexical_risks(target, improved)
            meta.update(
                {
                    "writer_internal_call_count": int(
                        meta.get("writer_internal_call_count") or (2 if review_attempted else 1)
                    ),
                    "strict_editorial_review_attempted": review_attempted,
                    "strict_editorial_review_applied": review_applied,
                    "strict_editorial_lexical_repair_applied": False,
                    "strict_editorial_first_risks": first_risks,
                    "strict_editorial_semantic_scope_risks": semantic_scope_risks,
                    "strict_editorial_remaining_scope_risks": remaining_scope_risks,
                    "strict_editorial_remaining_risks": remaining_lexical_risks,
                    "editorial_validation_policy": "consultant_reviews_visible_candidate",
                    "strict_editorial_safe_fallback_to_source": False,
                }
            )
        else:
            meta = first_meta
            meta.setdefault("writer_internal_call_count", 1)
            meta.setdefault("strict_editorial_review_applied", False)


        if scientific_additive_mode:
            additive_first_candidate = improved
            additive_first_meta = dict(meta or {})
            try:
                reviewed_output = self.llm.generate(
                    _scientific_additive_review_prompt(
                        target,
                        additive_first_candidate,
                        allowed_citations,
                    ),
                    temperature=0.0,
                    max_output_tokens=max_output_tokens,
                    max_input_tokens=60000,
                    retries=0,
                    request_name="ennoamelioration:writer:additive_preservation_review",
                )
                additive_reviewed = _match_editorial_format(
                    target,
                    _strip_fence(reviewed_output),
                )
                review_safe, review_reasons = _scientific_additive_review_is_safe(
                    target,
                    additive_first_candidate,
                    additive_reviewed,
                )
                additive_second_meta = dict(self.llm.get_last_generation_meta() or {})

                meta["scientific_additive_review_attempted"] = True
                meta["scientific_additive_review_reasons"] = review_reasons
                meta["scientific_additive_first_candidate_words"] = len(
                    additive_first_candidate.split()
                )
                meta["scientific_additive_reviewed_words"] = len(
                    additive_reviewed.split()
                )
                meta["scientific_additive_review_pass"] = additive_second_meta
                meta["writer_internal_call_count"] = int(
                    additive_first_meta.get("writer_internal_call_count") or 1
                ) + 1

                for token_key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    meta[token_key] = int(
                        additive_first_meta.get(token_key) or 0
                    ) + int(additive_second_meta.get(token_key) or 0)

                if review_safe:
                    improved = additive_reviewed
                    meta["scientific_additive_review_applied"] = True
                    meta["scientific_additive_preservation"] = "passed"
                else:
                    improved = additive_first_candidate
                    meta["scientific_additive_review_applied"] = False
                    meta["scientific_additive_preservation"] = (
                        "review_rejected_keep_first_candidate"
                    )
            except Exception as exc:
                improved = additive_first_candidate
                meta["scientific_additive_review_attempted"] = True
                meta["scientific_additive_review_applied"] = False
                meta["scientific_additive_preservation"] = (
                    "review_error_keep_first_candidate"
                )
                meta["scientific_additive_review_error"] = str(exc)[:500]

        meta.update(
            {
                "original_words": word_count,
                "improved_words": len(improved.split()),
                "cir_style_guidance_injected": bool(style_guidance.get("available")),
                "cir_style_pattern_ids": list(
                    style_guidance.get("selected_pattern_ids") or []
                ),
                "rewrite_mode": (
                    "strict_editorial_fact_locked"
                    if strict_editorial_mode
                    else "context_structural"
                    if context_structural_mode
                    else "uncertainty_evidence"
                    if uncertainty_evidence_mode
                    else "candidate_revision"
                    if routing.candidate_revision
                    else "conservative"
                    if conservative_mode
                    else "concise"
                ),
            }
        )
        coverage = build_coverage_report(evidence, improved)
        meta.update({"accepted_source_coverage": coverage, "accepted_source_coverage_complete": bool(coverage.get("complete"))})
        return improved, meta
