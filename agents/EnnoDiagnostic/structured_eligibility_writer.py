# -*- coding: utf-8 -*-
from __future__ import annotations

"""Rédaction structurée de la conclusion d'éligibilité EnnoDiagnostic avec PydanticAI.

Objectif
--------
Remplacer le couple fragile « prompt JSON libre -> json.loads -> gardes regex -> retry manuel »
par un contrat de sortie typé et validé automatiquement par PydanticAI.

Ce module :
- force une sortie structurée via ToolOutput ;
- valide la structure avec Pydantic ;
- demande automatiquement une nouvelle génération via ModelRetry si la sortie est invalide ;
- conserve le rattachement claim -> evidence_ids pour les sources cliquables ;
- reconstruit le paragraphe final côté Python ;
- ne contient aucun hardcoding de projet, de verrou, de technologie ou de valeur métier.
"""

import os
import re
import unicodedata
from dataclasses import dataclass, asdict, is_dataclass
from typing import Any, Dict, List, Literal, Optional, Set

from pydantic import BaseModel, Field, field_validator
from pydantic_ai import Agent, ModelRetry, ModelSettings, RunContext, ToolOutput

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass


# ---------------------------------------------------------------------------
# Modèles de sortie : le LLM ne renvoie PLUS de JSON libre ni de champ `text`
# dupliqué. Il renvoie seulement des claims structurés.
# ---------------------------------------------------------------------------

ClaimKind = Literal[
    "contexte",
    "verrou",
    "hypothese",
    "methodes_outils",
    "etapes_experimentales",
    "resultats",
    "apprentissage",
    "frascati_acquis",
    "frascati_a_consolider",
    "conclusion",
]

REQUIRED_CLAIM_KINDS: tuple[str, ...] = (
    "contexte",
    "verrou",
    "hypothese",
    "methodes_outils",
    "etapes_experimentales",
    "resultats",
    "apprentissage",
    "frascati_acquis",
    "frascati_a_consolider",
    "conclusion",
)

TECHNICAL_CLAIM_KINDS: Set[str] = {
    "contexte",
    "verrou",
    "hypothese",
    "methodes_outils",
    "etapes_experimentales",
    "resultats",
    "apprentissage",
}

FRASCATI_CLAIM_KINDS: Set[str] = {
    "frascati_acquis",
    "frascati_a_consolider",
    "conclusion",
}


class EligibilityClaim(BaseModel):
    """Une affirmation sourçable de la conclusion CIR."""

    claim_kind: ClaimKind
    text: str = Field(min_length=20, max_length=1100)
    evidence_ids: List[str] = Field(min_length=1, max_length=5)

    @field_validator("text")
    @classmethod
    def clean_claim_text(cls, value: str) -> str:
        value = re.sub(r"\s+", " ", str(value or "")).strip()
        if not value:
            raise ValueError("Le texte du claim est vide.")
        return value

    @field_validator("evidence_ids")
    @classmethod
    def unique_evidence_ids(cls, values: List[str]) -> List[str]:
        out: List[str] = []
        for value in values:
            item = str(value or "").strip()
            if item and item not in out:
                out.append(item)
        if not out:
            raise ValueError("Au moins une preuve est requise.")
        return out


class ResultFact(BaseModel):
    """Fait quantitatif caché utilisé pour sécuriser le claim de résultats."""

    fact_type: Literal[
        "observed_metric",
        "explicit_comparison",
        "observed_gain",
        "per_item_metric",
        "limitation_context",
    ]
    subject: str = Field(min_length=2, max_length=180)
    metric: str = Field(min_length=2, max_length=120)
    value: str = Field(min_length=1, max_length=40)
    unit: str = Field(default="", max_length=30)
    comparison_subject: Optional[str] = Field(default=None, max_length=180)
    comparison_value: Optional[str] = Field(default=None, max_length=40)
    difference: Optional[str] = Field(default=None, max_length=40)
    evidence_id: str


class EligibilityNarrative(BaseModel):
    """Sortie structurée complète de la conclusion d'éligibilité."""

    claims: List[EligibilityClaim] = Field(min_length=6, max_length=10)
    result_facts: List[ResultFact] = Field(default_factory=list, max_length=5)


@dataclass
class EligibilityDeps:
    evidence_by_id: Dict[str, Dict[str, Any]]
    allowed_evidence_ids: Set[str]
    score_evidence_id: str = "F0"


# ---------------------------------------------------------------------------
# Normalisation factuelle générique
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9])[-+]?\d+(?:[.,]\d+)?(?:\s*%)?")
_INTERNAL_TOKEN_RE = re.compile(
    r"\b(?:rnd_core_defendable|rnd_core_partial|insufficient_evidence|"
    r"classical_engineering|evidence_score|semantic_role)\b",
    flags=re.I,
)


_RANGE_RE = re.compile(
    r"\b(?:de|from)\s+([-+]?\d+(?:[.,]\d+)?)\s*%?\s+(?:[aà]|to)\s+([-+]?\d+(?:[.,]\d+)?)\s*%?\b|"
    r"\b(?:entre|between)\s+([-+]?\d+(?:[.,]\d+)?)\s*%?\s+(?:et|and)\s+([-+]?\d+(?:[.,]\d+)?)\s*%?\b",
    re.I,
)
_GLOBAL_WORD_RE = re.compile(r"\b(?:global|globale|overall|moyenne|moyen|average|mean)\b", re.I)
_STRONG_SIGNIFICANCE_RE = re.compile(r"\b(?:significatif|significative|significantly|significant)\b", re.I)
_FULL_ELIGIBILITY_RE = re.compile(
    r"\b(?:pleine? [eé]ligibilit[eé]|assurer (?:une|la) [eé]ligibilit[eé]|garantir (?:une|la) [eé]ligibilit[eé])\b",
    re.I,
)

_CLAIM_ALLOWED_PROOF_KINDS: Dict[str, Set[str]] = {
    "verrou": {"uncertainty"},
    "hypothese": {"hypothesis", "hypothesis_component"},
    "methodes_outils": {"experiment", "hypothesis", "hypothesis_component", "systematicity"},
    "etapes_experimentales": {"experiment", "systematicity"},
    "resultats": {"result", "quantitative_result", "qualitative_result"},
    "apprentissage": {"learning", "result", "quantitative_result", "qualitative_result"},
}


def _norm_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _number_tokens(value: Any) -> Set[str]:
    output: Set[str] = set()
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    for match in _NUMBER_RE.finditer(text):
        token = match.group(0).replace(" ", "").replace(",", ".").replace("%", "")
        try:
            normalized = f"{float(token):.10f}".rstrip("0").rstrip(".")
        except Exception:
            normalized = token.lstrip("+")
        if normalized:
            output.add(normalized)
    return output


def _evidence_text(item: Dict[str, Any]) -> str:
    return " ".join(
        str(item.get(key) or "")
        for key in (
            "excerpt",
            "source_text_original",
            "summary_fr",
            "justification_bridge_fr",
        )
    )


def _usage_to_dict(result: Any) -> Dict[str, Any]:
    usage = getattr(result, "usage", None)
    if callable(usage):
        try:
            usage = usage()
        except Exception:
            usage = None
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        try:
            return usage.model_dump()
        except Exception:
            pass
    if is_dataclass(usage):
        try:
            return asdict(usage)
        except Exception:
            pass
    if isinstance(usage, dict):
        return dict(usage)
    return {"value": str(usage)}


# ---------------------------------------------------------------------------
# Agent PydanticAI
# ---------------------------------------------------------------------------

_MODEL = os.getenv("ENNOSMART_PYDANTIC_MODEL", "openai-chat:gpt-4.1-mini")
_MAX_OUTPUT_TOKENS = int(os.getenv("ENNOSMART_PYDANTIC_ELIGIBILITY_MAX_TOKENS", "2400"))

_SYSTEM_INSTRUCTIONS = """
Tu es EnnoDiagnostic, agent d'aide à l'analyse CIR.

Ta tâche est de rédiger une conclusion d'éligibilité projet-spécifique à partir UNIQUEMENT
des preuves fournies dans le message utilisateur.

Règles :
1. Tout le texte visible est en français.
2. Ne jamais inventer un nom, une méthode, un outil, un résultat, un paramètre, un lien causal ou un chiffre.
3. Pour la partie technique, cite uniquement des preuves documentaires différentes de F0 ; F0 est réservé au calcul Frascati.
4. N'affiche jamais de liste brute de nombres. Un chiffre expérimental doit être associé à son objet, sa métrique, son unité et sa comparaison lorsque la preuve les contient.
5. Raconte la chaîne réelle : contexte -> verrou -> hypothèse -> méthodes/outils -> étapes expérimentales -> résultats -> apprentissage -> Frascati -> conclusion.
6. N'utilise pas de formulation générique si les preuves permettent de nommer concrètement l'objet technique, les méthodes et les résultats.
7. Si une étape n'est pas prouvée, dis clairement qu'elle reste insuffisamment documentée au lieu de l'inventer.
8. Les critères Frascati et les pourcentages proviennent uniquement de F0.
9. La conclusion doit expliquer pourquoi la part acquise est acquise et pourquoi la part restante doit être consolidée.
10. N'utilise jamais les codes internes de classification dans le texte visible.
11. Ne renvoie pas de champ paragraphe global : renvoie uniquement les claims structurés demandés. Le backend concatène les claims.
12. Respecte la fonction documentaire des preuves : un verrou doit citer une preuve `uncertainty`, une hypothèse une preuve `hypothesis_component`, une expérimentation une preuve `experiment`, et un résultat une preuve de résultat.
13. N'utilise jamais une preuve marquée `reference_like=true` comme expérience ou résultat du projet. Elle peut seulement aider à l'état de l'art.
14. Pour les résultats, privilégie `primary_result_evidence=true` et les scopes `global_comparison`, `global_metric` ou `observed_metric`. Une métrique par classe/cible ne doit jamais être généralisée à toute la méthode.
15. Ne crée jamais une plage « de X à Y », une moyenne, un gain, un écart ou une amélioration significative si cette relation n'est pas formulée explicitement dans UNE preuve citée.
16. Ne transforme pas une marge théorique avant 100 % en résultat expérimental. Les preuves `headroom_context` servent seulement de contexte secondaire.
17. La valeur Frascati est un indice de défendabilité/couverture documentaire, jamais un pourcentage de chance d'acceptation ni une garantie d'éligibilité administrative.
18. `result_facts` est facultatif. Si tu le renseignes, chaque fait quantitatif doit être observé et directement sourcé. Le claim `resultats` ne doit jamais introduire un chiffre absent de ses preuves citées.
""".strip()

eligibility_agent: Agent[EligibilityDeps, EligibilityNarrative] = Agent(
    _MODEL,
    deps_type=EligibilityDeps,
    output_type=ToolOutput(
        EligibilityNarrative,
        name="return_eligibility_narrative",
        description="Retourne la conclusion CIR structurée et ses preuves, sans texte libre hors schéma.",
        max_retries=2,
    ),
    retries={"output": 2},
    model_settings=ModelSettings(
        temperature=0.0,
        max_tokens=_MAX_OUTPUT_TOKENS,
        timeout=120,
    ),
    instructions=_SYSTEM_INSTRUCTIONS,
)


@eligibility_agent.output_validator
async def validate_eligibility_output(
    ctx: RunContext[EligibilityDeps],
    output: EligibilityNarrative,
) -> EligibilityNarrative:
    """Validation factuelle courte.

    Pydantic garantit déjà la structure. Ici on ne déclenche ModelRetry que pour
    des erreurs réellement dangereuses : preuve inexistante, chiffre non sourcé,
    preuve bibliographique utilisée comme expérience/résultat ou promesse
    d'éligibilité. Les préférences de forme/ranking restent des instructions et ne
    doivent jamais épuiser le budget de retries.
    """

    errors: List[str] = []
    claims = output.claims
    kinds = [claim.claim_kind for claim in claims]

    # Chaîne minimale nécessaire à une conclusion CIR exploitable. On ne force
    # plus dix claims exacts : le modèle peut fusionner contexte/méthodes ou
    # apprentissage/conclusion sans être rejeté trois fois.
    core_kinds = {
        "verrou", "hypothese", "etapes_experimentales", "resultats",
        "frascati_acquis", "frascati_a_consolider", "conclusion",
    }
    missing_core = sorted(core_kinds - set(kinds))
    if missing_core:
        errors.append("Claims essentiels manquants : " + ", ".join(missing_core))

    for claim in claims:
        unknown_ids = [eid for eid in claim.evidence_ids if eid not in ctx.deps.allowed_evidence_ids]
        if unknown_ids:
            errors.append(f"{claim.claim_kind}: preuves inconnues : {', '.join(unknown_ids)}")
            continue

        cited = [ctx.deps.evidence_by_id[eid] for eid in claim.evidence_ids]
        documentary_ids = [eid for eid in claim.evidence_ids if eid != ctx.deps.score_evidence_id]

        if claim.claim_kind in TECHNICAL_CLAIM_KINDS and not documentary_ids:
            errors.append(f"{claim.claim_kind}: une preuve documentaire du projet courant est obligatoire.")

        if claim.claim_kind in {"frascati_acquis", "frascati_a_consolider"}:
            if ctx.deps.score_evidence_id not in claim.evidence_ids:
                errors.append(f"{claim.claim_kind}: F0 est obligatoire pour les valeurs Frascati.")

        # Aucun nombre visible ne peut être fabriqué ou recalculé.
        source_numbers = _number_tokens(" ".join(_evidence_text(item) for item in cited))
        unsupported_numbers = sorted(_number_tokens(claim.text) - source_numbers)
        if unsupported_numbers:
            errors.append(
                f"{claim.claim_kind}: nombres absents des preuves citées : "
                + ", ".join(unsupported_numbers)
            )

        if claim.claim_kind in {"etapes_experimentales", "resultats", "apprentissage"}:
            if any(
                bool(item.get("reference_like"))
                and str(item.get("evidence_id") or "") != ctx.deps.score_evidence_id
                for item in cited
            ):
                errors.append(f"{claim.claim_kind}: référence bibliographique utilisée comme preuve du projet.")

        if _FULL_ELIGIBILITY_RE.search(claim.text):
            errors.append(f"{claim.claim_kind}: ne jamais garantir une pleine éligibilité CIR.")
        if _INTERNAL_TOKEN_RE.search(claim.text):
            errors.append(f"{claim.claim_kind}: code interne présent dans le texte visible.")

    # result_facts reste un bonus de traçabilité. S'il est fourni, ses valeurs
    # doivent être présentes dans la preuve indiquée ; son absence n'est plus une
    # erreur bloquante.
    for fact in output.result_facts:
        if fact.evidence_id not in ctx.deps.allowed_evidence_ids or fact.evidence_id == ctx.deps.score_evidence_id:
            errors.append(f"result_facts: preuve invalide pour {fact.subject}.")
            continue
        evidence = ctx.deps.evidence_by_id[fact.evidence_id]
        if bool(evidence.get("reference_like")):
            errors.append(f"result_facts: référence bibliographique utilisée pour {fact.subject}.")
            continue
        source_numbers = _number_tokens(_evidence_text(evidence))
        for value in (fact.value, fact.comparison_value, fact.difference):
            if value and not _number_tokens(value).issubset(source_numbers):
                errors.append(f"result_facts: valeur {value!r} absente de {fact.evidence_id}.")

    if errors:
        raise ModelRetry(
            "Corrige seulement ces erreurs factuelles, sans ajouter d'information :\n- "
            + "\n- ".join(errors[:8])
        )
    return output


# ---------------------------------------------------------------------------
# Prompt et adaptation au payload historique EnnoDiagnostic
# ---------------------------------------------------------------------------


def _compact_evidence_for_prompt(evidence: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    compact: List[Dict[str, Any]] = []
    for item in evidence:
        if not isinstance(item, dict):
            continue
        evidence_id = str(item.get("evidence_id") or "").strip()
        if not evidence_id:
            continue
        compact.append(
            {
                "evidence_id": evidence_id,
                "role": item.get("role"),
                "section_title": item.get("section_title"),
                "document": item.get("document_name") or item.get("document"),
                "rattachement_operation": item.get("justification_bridge_fr") or None,
                "summary_fr": item.get("summary_fr") or None,
                "proof_kind": item.get("proof_kind") or None,
                "result_scope": item.get("result_scope") or None,
                "primary_result_evidence": bool(item.get("primary_result_evidence")),
                "reference_like": bool(item.get("reference_like")),
                "hypothesis_explicit": item.get("hypothesis_explicit"),
                "hypothesis_anchor": item.get("hypothesis_anchor"),
                "quantitative_values": item.get("quantitative_values") or [],
                "text": item.get("excerpt") or item.get("source_text_original") or "",
            }
        )
    return compact


def _prompt_from_evidence(
    frascati_summary: Dict[str, Any],
    evidence: List[Dict[str, Any]],
) -> str:
    import json

    report = (
        frascati_summary.get("eligibility_evidence_report")
        if isinstance(frascati_summary, dict)
        else {}
    )
    report = report if isinstance(report, dict) else {}
    reference = report.get("reference_operation") if isinstance(report.get("reference_operation"), dict) else {}

    payload = {
        "operation_de_reference": {
            "group_id": reference.get("group_id"),
            "title": reference.get("title"),
            "operation_status": reference.get("operation_status"),
        },
        "preuve_calcul_et_preuves_documentaires": _compact_evidence_for_prompt(evidence),
    }

    return (
        "Rédige la conclusion d'éligibilité à partir du paquet de preuves ci-dessous. "
        "Choisis les preuves les plus pertinentes pour chaque claim ; ne te sens pas obligé d'utiliser toutes les preuves. "
        "Une référence bibliographique, une table des matières, une affiliation ou une citation d'un travail tiers ne doit "
        "pas servir de preuve d'une expérimentation menée par le projet. Si une preuve est ambiguë, préfère une autre preuve "
        "plus directe ou indique que le maillon reste à consolider. Pour `result_facts`, extrais seulement les faits quantitatifs "
        "observés depuis les preuves de résultat principales ; ne transforme pas une métrique par classe en performance globale et "
        "ne reconstruis aucune plage, moyenne ou différence absente du passage source.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    )


def _attach_proofs_to_claim(
    claim: Dict[str, Any],
    evidence_by_id: Dict[str, Dict[str, Any]],
) -> None:
    claim["proofs"] = [
        evidence_by_id[eid]
        for eid in claim.get("evidence_ids", [])
        if eid in evidence_by_id
    ]


def generate_eligibility_section_with_pydantic_ai(
    frascati_summary: Dict[str, Any],
    evidence: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Génère le payload compatible avec `diagnostic_static_presenter.py`."""

    evidence_by_id = {
        str(item.get("evidence_id")): item
        for item in evidence
        if isinstance(item, dict) and item.get("evidence_id")
    }
    if "F0" not in evidence_by_id:
        raise RuntimeError(
            "La preuve F0 du calcul Frascati est absente ; impossible de rédiger une conclusion structurée fiable."
        )

    deps = EligibilityDeps(
        evidence_by_id=evidence_by_id,
        allowed_evidence_ids=set(evidence_by_id),
        score_evidence_id="F0",
    )

    prompt = _prompt_from_evidence(frascati_summary, evidence)
    from modules.LLM.llm_concurrency import llm_capacity_slot

    with llm_capacity_slot("ennodiagnostic:eligibility_structured"):
        result = eligibility_agent.run_sync(prompt, deps=deps)
    narrative = result.output
    result_facts = [fact.model_dump() for fact in narrative.result_facts]

    claims: List[Dict[str, Any]] = []
    used_ids: List[str] = []
    for claim in narrative.claims:
        item = claim.model_dump()
        for evidence_id in item["evidence_ids"]:
            if evidence_id not in used_ids:
                used_ids.append(evidence_id)
        _attach_proofs_to_claim(item, evidence_by_id)
        claims.append(item)

    body = " ".join(claim["text"].strip() for claim in claims if claim.get("text")).strip()
    used_evidence = [evidence_by_id[eid] for eid in used_ids if eid in evidence_by_id]

    paragraph = {
        "text": body,
        "evidence_ids": used_ids,
        "claims": claims,
        "proofs": used_evidence,
    }

    return {
        "body": body,
        "paragraphs": [paragraph],
        "items": [],
        "evidence_ids": used_ids,
        "evidence": used_evidence,
        "validation_errors": [],
        "validation_warnings_only": False,
        "valid": True,
        "status": "pydantic_ai_structured_output",
        "result_facts": result_facts,
        "telemetry": {
            "framework": "pydantic_ai",
            "model": _MODEL,
            "output_mode": "tool_output",
            "schema": "EligibilityNarrative",
            "automatic_output_retries": 3,
            "usage": _usage_to_dict(result),
        },
        "framework_prompt": prompt,
    }
