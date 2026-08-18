from __future__ import annotations

import json
import re
from typing import Any, Mapping

from modules.LLM.llm_client import LLMClient

POLICY_VERSION = "ennoamel_revision_integrity_v3_18"

_CITATION_RE = re.compile(r"(?<![A-Za-z0-9])A\d+(?![A-Za-z0-9])", re.I)
_SENTENCE_SPLIT_RE = re.compile(
    r"\n{2,}|(?<=[.!?])\s+(?=(?:[A-ZÀ-Ý0-9\[]|#{1,6}\s))"
)
_IMMUTABLE_RE = re.compile(
    r"\[BLOC DOCUMENT IMMUTABLE\b[\s\S]*?\[/BLOC DOCUMENT IMMUTABLE\]",
    flags=re.I,
)
_REFERENCE_LINE_RE = re.compile(
    r"(?m)^[ \t]*\d{1,3}[ \t]+(?=[A-ZÀ-ÖØ-Ý]).*$"
)


def _clean(value: Any, limit: int = 12000) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _extract_json(value: Any) -> dict[str, Any]:
    raw = _clean(value, 100000)
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        parsed = json.loads(raw)
    except Exception:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            parsed = json.loads(raw[start : end + 1])
        except Exception:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _strip_nonsemantic_source_blocks(text: str) -> str:
    value = _IMMUTABLE_RE.sub("", str(text or ""))
    value = _REFERENCE_LINE_RE.sub("", value)
    return value


def source_units(original: str, max_units: int = 120) -> list[dict[str, str]]:
    source = _strip_nonsemantic_source_blocks(original)
    raw_units = [
        re.sub(r"\s+", " ", row).strip()
        for row in _SENTENCE_SPLIT_RE.split(source)
        if re.sub(r"\s+", " ", row).strip()
    ]
    output: list[dict[str, str]] = []
    for raw in raw_units:
        # Titres seuls / fragments trop courts ne sont pas des faits à vérifier.
        if len(raw.split()) < 5:
            continue
        output.append(
            {
                "source_id": f"S{len(output) + 1}",
                "text": _clean(raw, 1200),
            }
        )
        if len(output) >= max_units:
            break
    return output


def cited_claims(candidate: str, max_claims: int = 80) -> list[dict[str, Any]]:
    rows = [
        re.sub(r"\s+", " ", row).strip()
        for row in _SENTENCE_SPLIT_RE.split(str(candidate or ""))
        if re.sub(r"\s+", " ", row).strip()
    ]
    output: list[dict[str, Any]] = []
    for row in rows:
        citations = sorted(
            {value.upper() for value in _CITATION_RE.findall(row)},
            key=lambda x: int(x[1:]) if x[1:].isdigit() else x,
        )
        if not citations:
            continue
        claim = _CITATION_RE.sub("", row)
        claim = re.sub(r"\[\s*\]", "", claim)
        claim = re.sub(r"\s+", " ", claim).strip()
        if not claim:
            continue
        output.append(
            {
                "claim_id": f"C{len(output) + 1}",
                "claim": _clean(claim, 1600),
                "citation_ids": citations,
            }
        )
        if len(output) >= max_claims:
            break
    return output


def _accepted_evidence_rows(evidence: dict[str, Any] | None) -> list[dict[str, Any]]:
    scholar = evidence.get("scholar") if isinstance(evidence, dict) else None
    rows = scholar.get("evidence") if isinstance(scholar, dict) else None
    output: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        citation_id = str(row.get("citation_id") or "").strip().upper()
        if not re.fullmatch(r"A\d+", citation_id):
            continue
        output.append(row)
    return output


def evidence_payload(evidence: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    payload: dict[str, dict[str, Any]] = {}
    keys = (
        "title",
        "year",
        "abstract",
        "evidence_text",
        "claim",
        "claims",
        "snippet",
        "quote",
        "support",
        "method",
        "results",
        "limits",
        "impact",
        "rationale",
        "relevance",
    )
    for row in _accepted_evidence_rows(evidence):
        citation_id = str(row.get("citation_id") or "").strip().upper()
        item: dict[str, Any] = {"citation_id": citation_id}
        for key in keys:
            value = row.get(key)
            if value in (None, "", [], {}):
                continue
            if isinstance(value, str):
                item[key] = _clean(value, 7000)
            elif isinstance(value, (list, tuple)):
                item[key] = [
                    _clean(child, 1800)
                    for child in list(value)[:8]
                ]
            else:
                item[key] = value
        payload[citation_id] = item
    return payload


def _schema() -> dict[str, Any]:
    return {
        "title": "ennoamel_revision_integrity_v318",
        "type": "object",
        "required": ["source_checks", "citation_checks"],
        "properties": {
            "source_checks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["source_id", "status", "reason"],
                    "properties": {
                        "source_id": {"type": "string"},
                        "status": {
                            "type": "string",
                            "enum": ["preserved", "altered", "missing"],
                        },
                        "reason": {"type": "string"},
                    },
                },
            },
            "citation_checks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": [
                        "claim_id",
                        "citation_id",
                        "status",
                        "reason",
                    ],
                    "properties": {
                        "claim_id": {"type": "string"},
                        "citation_id": {"type": "string"},
                        "status": {
                            "type": "string",
                            "enum": ["supported", "partial", "unsupported"],
                        },
                        "reason": {"type": "string"},
                        "supported_rewrite": {"type": "string"},
                    },
                },
            },
        },
    }


def verify_revision_integrity(
    original: str,
    candidate: str,
    evidence: dict[str, Any] | None,
    *,
    llm: LLMClient | None = None,
) -> dict[str, Any]:
    units = source_units(original)
    claims = cited_claims(candidate)
    evidence_map = evidence_payload(evidence)

    # Pas de nouvelle citation scientifique : contrôle de conservation seulement.
    relevant_evidence = {
        citation_id: evidence_map.get(citation_id, {"citation_id": citation_id})
        for claim in claims
        for citation_id in claim.get("citation_ids") or []
    }

    prompt = f"""
Tu es le contrôleur de fidélité scientifique d'EnnoAmelioration.

Tu dois faire DEUX contrôles indépendants.

1. CONSERVATION DU TEXTE SOURCE
Pour CHAQUE source_id, vérifie si l'information portée par le segment source est
toujours présente dans la candidate.
- preserved : même information, éventuellement reformulée ou fusionnée.
- altered : l'information subsiste mais sa portée, son nombre, sa causalité, son
  niveau de certitude, son acteur, sa chronologie ou sa relation technique a changé.
- missing : l'information a disparu.
Ne considère pas qu'une information est préservée simplement parce que le thème
général est encore présent.

2. ENTAILMENT DES CITATIONS
Pour CHAQUE paire claim_id / citation_id :
- supported : la preuve fournie soutient directement l'affirmation entière.
- partial : elle soutient seulement une partie ; la phrase doit être scindée ou
  réduite avant de conserver cette citation.
- unsupported : la preuve ne soutient pas cette affirmation.
N'utilise AUCUNE connaissance générale. La seule preuve scientifique autorisée
est le contenu de la source correspondant au citation_id.
Une citation décorative, seulement thématiquement proche, est unsupported.
Si le papier parle d'ISAR, ne transforme pas cela en preuve directe d'un résultat
SAR différent. Si le papier ne mentionne pas MSTAR, ne valide pas une phrase qui
lui attribue un résultat MSTAR.

SEGMENTS SOURCE À VÉRIFIER
{json.dumps(units, ensure_ascii=False)}

CANDIDATE COMPLÈTE
{_clean(candidate, 30000)}

CLAIMS CITÉS
{json.dumps(claims, ensure_ascii=False)}

PREUVES AUTORISÉES POUR CES CITATIONS
{json.dumps(relevant_evidence, ensure_ascii=False)}

Retourne uniquement le JSON demandé. Pour source_checks, retourne exactement un
élément par source_id. Pour citation_checks, retourne exactement un élément par
paire claim_id/citation_id.
""".strip()

    verifier = llm or LLMClient()
    raw = verifier.generate(
        prompt,
        temperature=0.0,
        max_output_tokens=7000,
        max_input_tokens=100000,
        retries=0,
        json_mode=True,
        response_schema=_schema(),
        request_name="ennoamelioration:revision_integrity_v318",
    )
    payload = _extract_json(raw)

    source_rows = [
        row
        for row in (payload.get("source_checks") or [])
        if isinstance(row, dict)
    ]
    citation_rows = [
        row
        for row in (payload.get("citation_checks") or [])
        if isinstance(row, dict)
    ]

    expected_source_ids = [row["source_id"] for row in units]
    returned_source = {
        str(row.get("source_id") or ""): row
        for row in source_rows
        if str(row.get("source_id") or "")
    }

    source_failures: list[dict[str, Any]] = []
    for source_id in expected_source_ids:
        row = returned_source.get(source_id)
        if row is None:
            source_failures.append(
                {
                    "source_id": source_id,
                    "status": "missing",
                    "reason": "Le contrôleur n'a pas retourné de verdict pour ce segment.",
                }
            )
            continue
        status = str(row.get("status") or "").strip().casefold()
        if status != "preserved":
            source_failures.append(
                {
                    "source_id": source_id,
                    "status": status or "missing",
                    "reason": _clean(row.get("reason"), 700),
                }
            )

    expected_pairs = [
        (claim["claim_id"], citation_id)
        for claim in claims
        for citation_id in claim.get("citation_ids") or []
    ]
    returned_pairs = {
        (
            str(row.get("claim_id") or ""),
            str(row.get("citation_id") or "").upper(),
        ): row
        for row in citation_rows
    }

    citation_failures: list[dict[str, Any]] = []
    for claim_id, citation_id in expected_pairs:
        row = returned_pairs.get((claim_id, citation_id))
        if row is None:
            citation_failures.append(
                {
                    "claim_id": claim_id,
                    "citation_id": citation_id,
                    "status": "unsupported",
                    "reason": "Aucun verdict d'entailment n'a été retourné.",
                    "supported_rewrite": "",
                }
            )
            continue
        status = str(row.get("status") or "").strip().casefold()
        if status != "supported":
            citation_failures.append(
                {
                    "claim_id": claim_id,
                    "citation_id": citation_id,
                    "status": status or "unsupported",
                    "reason": _clean(row.get("reason"), 700),
                    "supported_rewrite": _clean(
                        row.get("supported_rewrite"), 1500
                    ),
                }
            )

    issues: list[str] = []
    for row in source_failures[:20]:
        prefix = (
            "source_fact_altered:"
            if row.get("status") == "altered"
            else "source_fact_missing:"
        )
        issues.append(prefix + str(row.get("source_id") or ""))
    for row in citation_failures[:30]:
        issues.append(
            "citation_non_etayee:"
            + str(row.get("claim_id") or "")
            + ":"
            + str(row.get("citation_id") or "")
            + ":"
            + str(row.get("status") or "")
        )

    meta = {}
    try:
        meta = verifier.get_last_generation_meta()
    except Exception:
        meta = {}

    return {
        "policy_version": POLICY_VERSION,
        "control_mode": "advisory_only",
        "blocking": False,
        "source_unit_count": len(units),
        "claim_count": len(claims),
        "citation_pair_count": len(expected_pairs),
        "source_failures": source_failures,
        "citation_failures": citation_failures,
        "source_conservation_complete": not source_failures,
        "citation_entailment_complete": not citation_failures,
        "complete": not source_failures and not citation_failures,
        "issues": issues,
        "llm": meta,
    }


def verify_additions_entailment(
    additions: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
    *,
    llm: LLMClient | None = None,
) -> dict[str, Any]:
    evidence = {
        "scholar": {
            "available": True,
            "evidence": list(evidence_rows or []),
        }
    }
    candidate = "\n\n".join(
        str(row.get("content") or "")
        for row in (additions or [])
        if isinstance(row, dict)
    )

    # Ici il n'y a pas de source à préserver : uniquement citation -> preuve.
    claims = cited_claims(candidate)
    evidence_map = evidence_payload(evidence)
    relevant_evidence = {
        citation_id: evidence_map.get(citation_id, {"citation_id": citation_id})
        for claim in claims
        for citation_id in claim.get("citation_ids") or []
    }

    if not claims:
        return {
            "policy_version": POLICY_VERSION,
            "control_mode": "advisory_only",
            "blocking": False,
            "complete": False,
            "citation_entailment_complete": False,
            "citation_failures": [
                {
                    "claim_id": "",
                    "citation_id": "",
                    "status": "unsupported",
                    "reason": "Aucune affirmation citée n'a été détectée.",
                }
            ],
            "issues": ["citation_entailment_absent"],
        }

    schema = {
        "title": "ennoamel_addition_entailment_v318",
        "type": "object",
        "required": ["citation_checks"],
        "properties": {
            "citation_checks": _schema()["properties"]["citation_checks"]
        },
    }

    prompt = f"""
Contrôle strictement la relation entre chaque affirmation et chaque citation.

CLAIMS
{json.dumps(claims, ensure_ascii=False)}

PREUVES
{json.dumps(relevant_evidence, ensure_ascii=False)}

Pour chaque paire claim_id/citation_id, retourne supported, partial ou unsupported.
supported signifie que la preuve soutient directement toute l'affirmation.
partial ou simple proximité thématique n'est pas suffisant.
N'utilise aucune connaissance extérieure aux preuves.
""".strip()

    verifier = llm or LLMClient()
    raw = verifier.generate(
        prompt,
        temperature=0.0,
        max_output_tokens=3500,
        max_input_tokens=60000,
        retries=0,
        json_mode=True,
        response_schema=schema,
        request_name="ennoscholar:anchored_entailment_v318",
    )
    payload = _extract_json(raw)
    returned = {
        (
            str(row.get("claim_id") or ""),
            str(row.get("citation_id") or "").upper(),
        ): row
        for row in (payload.get("citation_checks") or [])
        if isinstance(row, dict)
    }

    failures: list[dict[str, Any]] = []
    for claim in claims:
        for citation_id in claim.get("citation_ids") or []:
            pair = (claim["claim_id"], citation_id)
            row = returned.get(pair)
            if row is None or str(row.get("status") or "").casefold() != "supported":
                failures.append(
                    {
                        "claim_id": claim["claim_id"],
                        "citation_id": citation_id,
                        "status": (
                            str(row.get("status") or "unsupported").casefold()
                            if row
                            else "unsupported"
                        ),
                        "reason": _clean(
                            row.get("reason") if row else "Verdict absent.",
                            700,
                        ),
                        "supported_rewrite": _clean(
                            row.get("supported_rewrite") if row else "",
                            1200,
                        ),
                    }
                )

    return {
        "policy_version": POLICY_VERSION,
        "control_mode": "advisory_only",
        "blocking": False,
        "citation_failures": failures,
        "citation_entailment_complete": not failures,
        "complete": not failures,
        "issues": [
            f"citation_non_etayee:{row['claim_id']}:{row['citation_id']}:{row['status']}"
            for row in failures
        ],
    }


def render_integrity_retry_instruction(report: dict[str, Any]) -> str:
    source_failures = report.get("source_failures") or []
    citation_failures = report.get("citation_failures") or []

    payload = {
        "source_failures": source_failures[:12],
        "citation_failures": citation_failures[:12],
    }
    return (
        "\n\nCORRECTION AUTOMATIQUE V3.18 — FIDÉLITÉ OBLIGATOIRE\n"
        "Corrige uniquement les problèmes suivants.\n"
        "- Tout fait source signalé missing/altered doit être réintégré sans changer sa portée.\n"
        "- Toute citation partial/unsupported doit être déplacée, scindée ou reformulée pour "
        "ne soutenir qu'une affirmation directement démontrée par sa preuve.\n"
        "- N'ajoute aucune citation décorative.\n"
        "- Toutes les sources acceptées restent obligatoires : si une source soutient seulement "
        "un fait minimal, utilise uniquement ce fait minimal.\n"
        "- Ne supprime aucune figure, tableau, référence, mesure ou fait existant.\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def revision_block_message(issues: list[str]) -> str:
    values = [str(value or "") for value in (issues or [])]
    if any(
        value.startswith("citation_non_etayee:")
        or value == "scientific_entailment_blocking"
        for value in values
    ):
        return (
            "La proposition n'a pas été finalisée car au moins une citation ne "
            "soutenait pas directement l'affirmation à laquelle elle était reliée. "
            "Aucune citation décorative n'est conservée et la version active reste "
            "inchangée. Si une source acceptée ne peut pas être intégrée fidèlement, "
            "il faut la désélectionner ou choisir une source plus adaptée."
        )
    if any(
        value.startswith(
            (
                "source_fact_missing:",
                "source_fact_altered:",
                "document_block_missing:",
                "document_block_changed:",
                "references_perdues:",
                "protected_fragment_missing:",
            )
        )
        for value in values
    ):
        return (
            "La proposition n'a pas été finalisée car elle perdait ou modifiait "
            "encore un élément du texte source (fait, référence, figure ou bloc "
            "protégé). La version active reste inchangée."
        )
    return (
        "La proposition n'a pas satisfait tous les contrôles de fidélité. "
        "La version active reste inchangée."
    )
