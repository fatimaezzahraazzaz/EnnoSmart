# -*- coding: utf-8 -*-
from __future__ import annotations

"""EnnoScholar CIR Quality Guard V3.

Garde déterministe branché sur la Phase 5 existante.
Aucun appel LLM n'est ajouté ici.

Objectifs:
- confirmer la force des preuves article × verrou;
- empêcher qu'une source connexe devienne une preuve directe;
- rendre bloquantes les sur-affirmations CIR sur un verrou peu couvert;
- retirer les répétitions évidentes sans supprimer une sous-section obligatoire;
- filtrer les figures contextuelles / projet / non probantes.
"""

import copy
import os
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Set, Tuple


SCHEMA_VERSION = "ennoscholar_cir_quality_guard_v3"

# Pour confirmer DIRECT, il faut autre chose qu'une simple description de méthode.
_DIRECT_KINDS = {"result", "limitation", "protocol", "data"}

_STRONG_CLAIM_RE = re.compile(
    r"\b(?:"
    r"d[ée]montre|prouve|confirme|[ée]tablit|garantit|atteste|"
    r"cause|entra[iî]ne|provoque|d[ée]termine|"
    r"permet de conclure|met clairement en [ée]vidence|"
    r"constitue (?:donc )?un verrou scientifique majeur|"
    r"valide|r[ée]sout|l[èe]ve (?:le|ce) verrou"
    r")\b",
    flags=re.I,
)

# Dans le workflow conversationnel, les sources connexes doivent pouvoir
# décrire exactement le sous-problème qu'elles documentent. On ne bloque donc
# que les formulations qui prétendent conclure sur le verrou complet. Le
# workflow historique continue d'utiliser _STRONG_CLAIM_RE ci-dessus.
_GUIDED_STRONG_VERROU_CLAIM_RE = re.compile(
    r"\b(?:"
    r"(?:r[ée]sout|l[èe]ve) (?:le|ce) verrou|"
    r"permet de conclure (?:sur|quant [àa]) (?:le|ce) verrou|"
    r"(?:d[ée]montre|prouve|confirme|[ée]tablit|valide|garantit)"
    r".{0,100}\b(?:le verrou complet|ce verrou|la solution globale)\b|"
    r"(?:robustesse|d[ée]tection).{0,100}(?:peu|faible quantit[ée]) de "
    r"donn[ée]es annot[ée]es.{0,100}"
    r"(?:est d[ée]montr[ée]e|est prouv[ée]e|est valid[ée]e|est garantie)"
    r")",
    flags=re.I,
)

_DISCLOSURE_RE = re.compile(
    r"(?:"
    r"\babsence de preuve\b|"
    r"\bsans preuve directe\b|"
    r"\baucune preuve (?:scientifique )?directe\b|"
    r"\bsources? connexes?\b|"
    r"\bne permet(?:tent)? pas de conclure\b|"
    r"\bne permet(?:tent)? pas d['’][ée]tablir\b|"
    r"\bcorpus\b.{0,100}\b(?:insuffisant|ne permet pas|ne suffit pas)\b|"
    r"\bn['’](?:est|apporte|fournit|[ée]tablit|d[ée]montre|permet)\b"
    r".{0,45}\bpas\b|"
    r"\breste (?:incertain|incertaine|non [ée]tabli|non d[ée]montr[ée])\b"
    r")",
    flags=re.I,
)

_ADVOCACY_REPLACEMENTS = (
    (
        re.compile(r"\bjustifient pleinement\b", flags=re.I),
        "mettent en évidence des incertitudes scientifiques ou techniques qui motivent",
    ),
    (
        re.compile(r"\bjustifie pleinement\b", flags=re.I),
        "met en évidence une incertitude scientifique ou technique qui motive",
    ),
    (
        re.compile(r"\bprouve définitivement\b", flags=re.I),
        "apporte un élément de preuve dans les conditions étudiées",
    ),
    (
        re.compile(r"\bdémontre définitivement\b", flags=re.I),
        "montre dans les conditions étudiées",
    ),
)


def _clean(value: Any, limit: int = 200000) -> str:
    text = str(value or "").replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:limit]


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", _clean(value).casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _tokens(value: Any) -> Set[str]:
    stop = {
        "avec", "dans", "pour", "sans", "entre", "cette", "des", "les", "une",
        "sur", "par", "que", "qui", "est", "sont", "the", "and", "for", "with",
        "from", "that", "this", "scientifique", "technique", "verrou", "section",
        "etat", "art", "projet", "methode", "method", "donnees", "data",
    }
    return {
        token
        for token in _norm(value).split()
        if len(token) >= 3 and token not in stop
    }


def _similarity(left: Any, right: Any) -> float:
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def _citation(value: Any) -> str:
    match = re.search(r"\bA\s*(\d+)\b", _clean(value, 80), flags=re.I)
    return f"A{match.group(1)}" if match else ""


def _citations_from_text(value: Any) -> List[str]:
    labels = {
        f"A{match.group(1)}"
        for match in re.finditer(r"\bA\s*(\d+)\b", _clean(value))
    }
    return sorted(labels, key=lambda item: int(item[1:]))


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    return [value]


def _all_verrou_rows(blueprint: Mapping[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    def append(raw: Any) -> None:
        if not isinstance(raw, Mapping):
            return
        row = dict(raw)
        vid = _clean(row.get("verrou_id") or row.get("id"), 120)
        if not vid or vid in seen:
            return
        seen.add(vid)
        rows.append(row)

    for raw in blueprint.get("verrous") or []:
        append(raw)
    for section in blueprint.get("sections") or []:
        if not isinstance(section, Mapping):
            continue
        for raw in section.get("verrous") or []:
            append(raw)
    return rows


def build_cir_evidence_matrix(
    blueprint: Mapping[str, Any],
    evidence_units: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Construit une matrice conservatrice article × verrou.

    Une citation déclarée directe en Phase 4.7 n'est confirmée DIRECT que si:
    - une unité de preuve atomique porte explicitement le même verrou_id;
    - cette unité est de nature résultat, limitation, protocole ou données.

    Une simple description de méthode ne suffit donc pas à "prouver" le verrou.
    """
    by_citation: Dict[str, List[Dict[str, Any]]] = {}
    for raw in evidence_units or []:
        if not isinstance(raw, Mapping):
            continue
        citation = _citation(raw.get("citation_label"))
        if citation:
            by_citation.setdefault(citation, []).append(dict(raw))

    role_contract = (
        blueprint.get("evidence_roles_by_verrou")
        if isinstance(blueprint.get("evidence_roles_by_verrou"), Mapping)
        else {}
    )

    verrou_reports: List[Dict[str, Any]] = []
    source_relations: List[Dict[str, Any]] = []

    for verrou in _all_verrou_rows(blueprint):
        vid = _clean(verrou.get("verrou_id") or verrou.get("id"), 120)
        title = _clean(
            verrou.get("verrou_title") or verrou.get("title"),
            1200,
        )
        declared = (
            role_contract.get(vid)
            if isinstance(role_contract.get(vid), Mapping)
            else verrou
        )

        def citation_set(key: str) -> Set[str]:
            result = {_citation(x) for x in _as_list(declared.get(key))}
            result.discard("")
            return result

        direct_declared = citation_set("direct_citations")
        related = citation_set("related_citations")
        methodological = citation_set("methodological_citations")
        background = citation_set("background_citations")

        direct_confirmed: List[str] = []
        direct_downgraded: List[str] = []

        for citation in sorted(direct_declared, key=lambda x: int(x[1:])):
            units = by_citation.get(citation) or []
            explicit_units = [
                unit
                for unit in units
                if vid
                in {
                    _clean(value, 120)
                    for value in _as_list(unit.get("verrou_ids") or [])
                }
            ]
            strong_explicit = [
                unit
                for unit in explicit_units
                if _clean(unit.get("kind"), 80).casefold() in _DIRECT_KINDS
            ]
            if strong_explicit:
                direct_confirmed.append(citation)
                relation = "DIRECT"
                basis = [
                    {
                        "evidence_id": unit.get("evidence_id"),
                        "kind": unit.get("kind"),
                        "text": _clean(unit.get("text"), 650),
                    }
                    for unit in strong_explicit[:4]
                ]
            else:
                direct_downgraded.append(citation)
                relation = "CONNECTED_PENDING_DIRECT_CONFIRMATION"
                basis = [
                    {
                        "evidence_id": unit.get("evidence_id"),
                        "kind": unit.get("kind"),
                        "text": _clean(unit.get("text"), 650),
                    }
                    for unit in explicit_units[:4]
                ]

            source_relations.append(
                {
                    "verrou_id": vid,
                    "citation": citation,
                    "relation": relation,
                    "declared_phase47_role": "direct",
                    "evidence_basis": basis,
                }
            )

        connected = sorted(
            (related | methodological | background | set(direct_downgraded))
            - set(direct_confirmed),
            key=lambda x: int(x[1:]),
        )

        for citation in connected:
            if citation in direct_declared:
                continue
            declared_role = (
                "related"
                if citation in related
                else "methodological"
                if citation in methodological
                else "background"
            )
            source_relations.append(
                {
                    "verrou_id": vid,
                    "citation": citation,
                    "relation": (
                        "CONNECTED"
                        if declared_role in {"related", "methodological"}
                        else "BACKGROUND"
                    ),
                    "declared_phase47_role": declared_role,
                    "evidence_basis": [],
                }
            )

        direct_count = len(direct_confirmed)
        connected_count = len(connected)
        if direct_count >= 2:
            strength = "FORTE"
        elif direct_count == 1:
            strength = "MOYENNE"
        elif connected_count:
            strength = "FAIBLE"
        else:
            strength = "INSUFFISANTE"

        verrou_reports.append(
            {
                "verrou_id": vid,
                "verrou_title": title,
                "strength": strength,
                "direct_confirmed_citations": direct_confirmed,
                "direct_declared_but_unconfirmed": direct_downgraded,
                "connected_citations": connected,
                "direct_count": direct_count,
                "connected_count": connected_count,
                "allow_strong_verrou_conclusion": direct_count > 0,
                "requires_insufficiency_disclosure": direct_count == 0,
                "research_recommended": direct_count == 0,
                "writer_rule": (
                    "Les preuves directes confirmées permettent uniquement des "
                    "conclusions limitées aux conditions réellement documentées."
                    if direct_count
                    else (
                        "Aucune preuve directe confirmée: présenter les sources "
                        "comme connexes, dire ce qu'elles établissent réellement et "
                        "ce qu'elles ne permettent pas de conclure sur le verrou."
                    )
                ),
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy": {
            "direct_requires_atomic_verrou_link": True,
            "direct_requires_result_limitation_protocol_or_data": True,
            "related_never_promoted_to_direct": True,
            "absence_of_direct_evidence_requires_disclosure": True,
            "additional_llm_calls": 0,
        },
        "verrous": verrou_reports,
        "source_relations": source_relations,
    }


def evidence_matrix_for_prompt(
    matrix: Mapping[str, Any],
    verrou_ids: Iterable[Any],
) -> Dict[str, Any]:
    ids = {_clean(value, 120) for value in verrou_ids if _clean(value, 120)}
    return {
        "policy": dict(matrix.get("policy") or {}),
        "verrous": [
            dict(row)
            for row in matrix.get("verrous") or []
            if isinstance(row, Mapping)
            and (not ids or _clean(row.get("verrou_id"), 120) in ids)
        ],
    }


def _sentence_split(value: Any) -> List[str]:
    return [
        _clean(part, 5000)
        for part in re.split(r"(?<=[.!?])\s+|\n+", _clean(value))
        if len(_clean(part)) >= 18
    ]


def audit_cir_section(
    generated: Mapping[str, Any],
    section: Mapping[str, Any],
    matrix: Mapping[str, Any],
) -> Dict[str, Any]:
    """Bloque une conclusion forte sur un verrou sans preuve directe confirmée."""
    matrix_by_id = {
        _clean(row.get("verrou_id"), 120): row
        for row in matrix.get("verrous") or []
        if isinstance(row, Mapping)
    }
    subsection_by_id = {
        _clean(row.get("verrou_id"), 120): row
        for row in generated.get("subsections") or []
        if isinstance(row, Mapping)
    }

    issues: List[Dict[str, Any]] = []
    matrix_policy = (
        matrix.get("policy")
        if isinstance(matrix.get("policy"), Mapping)
        else {}
    )
    strong_claim_re = (
        _GUIDED_STRONG_VERROU_CLAIM_RE
        if matrix_policy.get("guided_conversation")
        else _STRONG_CLAIM_RE
    )

    for verrou in section.get("verrous") or []:
        if not isinstance(verrou, Mapping):
            continue
        vid = _clean(verrou.get("verrou_id"), 120)
        policy = matrix_by_id.get(vid) or {}
        subsection = subsection_by_id.get(vid) or {}
        content = _clean(subsection.get("content"))

        if not policy.get("allow_strong_verrou_conclusion"):
            for sentence in _sentence_split(content):
                if not strong_claim_re.search(sentence):
                    continue
                if _DISCLOSURE_RE.search(sentence):
                    continue
                issues.append(
                    {
                        "type": "cir_related_evidence_overclaim",
                        "verrou_id": vid,
                        "verrou_title": _clean(
                            verrou.get("verrou_title") or verrou.get("title"),
                            800,
                        ),
                        "strength": policy.get("strength") or "FAIBLE",
                        "claim": sentence,
                        "citations": _citations_from_text(sentence),
                        "reason": (
                            "Le verrou ne dispose d'aucune preuve directe "
                            "confirmée. Une source connexe ou méthodologique ne "
                            "peut pas soutenir une conclusion causale ou "
                            "affirmative sur ce verrou."
                        ),
                        "blocking": True,
                    }
                )

        if (
            policy.get("requires_insufficiency_disclosure")
            and content
            and not _DISCLOSURE_RE.search(content)
        ):
            issues.append(
                {
                    "type": "cir_missing_insufficiency_disclosure",
                    "verrou_id": vid,
                    "verrou_title": _clean(
                        verrou.get("verrou_title") or verrou.get("title"),
                        800,
                    ),
                    "strength": policy.get("strength") or "FAIBLE",
                    "claim": "",
                    "citations": [],
                    "reason": (
                        "Le texte doit indiquer explicitement que le corpus "
                        "sélectionné ne fournit pas de preuve directe pour ce verrou."
                    ),
                    "blocking": True,
                }
            )

    return {
        "ok": not issues,
        "issues": issues,
        "blocking_issues": [
            issue for issue in issues if issue.get("blocking")
        ],
    }


def audit_cir_draft(
    draft: Mapping[str, Any],
    blueprint: Mapping[str, Any],
    matrix: Mapping[str, Any],
) -> Dict[str, Any]:
    generated_by_id = {
        _clean(row.get("section_id"), 120): row
        for row in draft.get("sections") or []
        if isinstance(row, Mapping)
    }
    reports: List[Dict[str, Any]] = []
    issues: List[Dict[str, Any]] = []

    for section in blueprint.get("sections") or []:
        if not isinstance(section, Mapping):
            continue
        section_id = _clean(section.get("section_id"), 120)
        report = audit_cir_section(
            generated_by_id.get(section_id) or {},
            section,
            matrix,
        )
        reports.append({"section_id": section_id, **report})
        issues.extend(report.get("issues") or [])

    return {
        "ok": not issues,
        "issues": issues,
        "blocking_issues": [
            issue for issue in issues if issue.get("blocking")
        ],
        "sections": reports,
    }


def _soften_text(value: Any) -> Tuple[str, List[Dict[str, str]]]:
    text = _clean(value)
    changes: List[Dict[str, str]] = []
    for pattern, replacement in _ADVOCACY_REPLACEMENTS:
        if not pattern.search(text):
            continue
        before = text
        text = pattern.sub(replacement, text)
        if text != before:
            changes.append(
                {
                    "kind": "advocacy_softening",
                    "pattern": pattern.pattern,
                    "replacement": replacement,
                }
            )
    return text, changes


def _paragraphs(value: Any) -> List[str]:
    return [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", _clean(value))
        if paragraph.strip()
    ]


def _paragraph_citations(value: Any) -> Set[str]:
    return set(_citations_from_text(value))


def apply_cir_postprocessing(
    draft: Mapping[str, Any],
    blueprint: Mapping[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Nuance le langage et retire seulement les répétitions très sûres."""
    result = copy.deepcopy(dict(draft))
    changes: List[Dict[str, Any]] = []
    seen_paragraphs: List[Tuple[str, Set[str], str]] = []

    for section in result.get("sections") or []:
        if not isinstance(section, dict):
            continue
        section_id = _clean(section.get("section_id"), 120)

        softened, local_changes = _soften_text(section.get("content"))
        section["content"] = softened
        changes.extend(
            {"section_id": section_id, "scope": "section", **change}
            for change in local_changes
        )

        subsections = [
            row
            for row in section.get("subsections") or []
            if isinstance(row, dict)
        ]
        for subsection in subsections:
            softened_sub, sub_changes = _soften_text(
                subsection.get("content")
            )
            subsection["content"] = softened_sub
            changes.extend(
                {
                    "section_id": section_id,
                    "scope": "subsection",
                    "verrou_id": _clean(
                        subsection.get("verrou_id"), 120
                    ),
                    **change,
                }
                for change in sub_changes
            )

        # Cas typique observé: la section et son unique sous-section portent
        # pratiquement le même titre et répètent le même raisonnement.
        if len(subsections) == 1:
            subsection = subsections[0]
            title_similarity = _similarity(
                section.get("title"),
                subsection.get("title"),
            )
            parent_paragraphs = _paragraphs(section.get("content"))
            child_paragraphs = _paragraphs(subsection.get("content"))

            if (
                title_similarity >= 0.62
                and parent_paragraphs
                and child_paragraphs
            ):
                kept: List[str] = []
                for paragraph in parent_paragraphs:
                    best = max(
                        (
                            _similarity(paragraph, child)
                            for child in child_paragraphs
                        ),
                        default=0.0,
                    )
                    if best >= 0.55:
                        changes.append(
                            {
                                "section_id": section_id,
                                "scope": "section",
                                "kind": "redundant_parent_paragraph_removed",
                                "similarity": round(best, 3),
                                "excerpt": paragraph[:350],
                            }
                        )
                        continue
                    kept.append(paragraph)
                section["content"] = "\n\n".join(kept)

        # Quasi-doublon global seulement si très proche ET citations compatibles.
        for scope, owner in [
            ("section", section),
            *[
                ("subsection", subsection)
                for subsection in subsections
            ],
        ]:
            current: List[str] = []
            for paragraph in _paragraphs(owner.get("content")):
                citations = _paragraph_citations(paragraph)
                duplicate_of = None

                for (
                    previous,
                    previous_citations,
                    previous_location,
                ) in seen_paragraphs:
                    if _similarity(paragraph, previous) < 0.90:
                        continue
                    if (
                        citations
                        and previous_citations
                        and not (
                            citations <= previous_citations
                            or previous_citations <= citations
                        )
                    ):
                        continue
                    duplicate_of = previous_location
                    break

                if duplicate_of:
                    changes.append(
                        {
                            "section_id": section_id,
                            "scope": scope,
                            "kind": "near_duplicate_paragraph_removed",
                            "duplicate_of": duplicate_of,
                            "excerpt": paragraph[:350],
                        }
                    )
                    continue

                current.append(paragraph)
                location = (
                    f"{section_id}:{scope}:"
                    f"{_clean(owner.get('verrou_id'), 120) if scope == 'subsection' else 'main'}"
                )
                seen_paragraphs.append(
                    (paragraph, citations, location)
                )

            owner["content"] = "\n\n".join(current)

    return result, {
        "schema_version": SCHEMA_VERSION,
        "changes_count": len(changes),
        "changes": changes,
    }


def classify_visual_role(placement: Mapping[str, Any]) -> str:
    if not _citation(placement.get("citation_label")):
        return "PROJECT_RESULT"

    text = _norm(
        " ".join(
            [
                _clean(placement.get("figure_label"), 200),
                _clean(placement.get("caption"), 1800),
                _clean(placement.get("anchor_excerpt"), 1000),
            ]
        )
    )

    if re.search(
        r"\b(?:comparison|comparaison|versus|benchmark|"
        r"measured simulated|mesure simulation|simulation mesure)\b",
        text,
    ):
        return "COMPARISON"

    if re.search(
        r"\b(?:result|performance|accuracy|precision|error|erreur|"
        r"curve|courbe|graph|plot|metric|rcs|measurement|mesure|validation)\b",
        text,
    ):
        return "EVIDENCE"

    if re.search(
        r"\b(?:architecture|cad|cao|model|modele|geometry|geometrie|"
        r"schema|diagram|workflow|setup|method|methode|algorithm)\b",
        text,
    ):
        return "METHOD"

    return "CONTEXT"


def filter_visual_placements(
    placements: Sequence[Mapping[str, Any]],
    blueprint: Mapping[str, Any],
    matrix: Mapping[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    section_by_id = {
        _clean(section.get("section_id"), 120): section
        for section in blueprint.get("sections") or []
        if isinstance(section, Mapping)
    }
    matrix_by_id = {
        _clean(row.get("verrou_id"), 120): row
        for row in matrix.get("verrous") or []
        if isinstance(row, Mapping)
    }

    allow_project = os.getenv(
        "ENNOSCHOLAR_PHASE5_ALLOW_PROJECT_CONTEXT_VISUALS",
        "0",
    ).strip().lower() in {"1", "true", "yes", "on"}

    allow_unrequested_method = os.getenv(
        "ENNOSCHOLAR_PHASE5_ALLOW_UNREQUESTED_METHOD_VISUALS",
        "0",
    ).strip().lower() in {"1", "true", "yes", "on"}

    kept: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []

    for raw in placements or []:
        if not isinstance(raw, Mapping):
            continue

        placement = dict(raw)
        role = classify_visual_role(placement)
        placement["cir_visual_role"] = role

        section_id = _clean(placement.get("section_id"), 120)
        section = section_by_id.get(section_id) or {}
        verrous = [
            row
            for row in section.get("verrous") or []
            if isinstance(row, Mapping)
        ]
        citation = _citation(placement.get("citation_label"))
        reason = ""

        if role == "PROJECT_RESULT" and not allow_project:
            reason = (
                "Figure issue d'un document projet: exclue par défaut de "
                "l'état de l'art scientifique."
            )
        elif role == "CONTEXT":
            reason = "Figure contextuelle non probante: exclue."
        elif role == "METHOD" and not (
            allow_unrequested_method
            or bool(section.get("visual_requirements"))
        ):
            reason = (
                "Figure de méthode sans besoin visuel explicitement demandé: exclue."
            )
        elif verrous and role in {"EVIDENCE", "COMPARISON"}:
            direct_allowed: Set[str] = set()
            for verrou in verrous:
                vid = _clean(verrou.get("verrou_id"), 120)
                direct_allowed.update(
                    matrix_by_id.get(vid, {}).get(
                        "direct_confirmed_citations"
                    )
                    or []
                )

            if citation and citation not in direct_allowed:
                reason = (
                    "Figure présentée comme preuve/comparaison mais sa source "
                    "n'est pas une preuve directe confirmée du verrou."
                )

        if reason:
            rejected.append(
                {
                    "visual_id": placement.get("visual_id"),
                    "section_id": section_id,
                    "role": role,
                    "citation": citation,
                    "reason": reason,
                }
            )
            continue

        try:
            similarity = float(
                placement.get("semantic_similarity") or 0.0
            )
        except (TypeError, ValueError):
            similarity = 0.0

        minimum = (
            0.10
            if role in {"EVIDENCE", "COMPARISON"}
            else 0.14
        )

        if similarity < minimum:
            rejected.append(
                {
                    "visual_id": placement.get("visual_id"),
                    "section_id": section_id,
                    "role": role,
                    "citation": citation,
                    "reason": (
                        f"Similarité sémantique insuffisante "
                        f"({similarity:.3f} < {minimum:.2f})."
                    ),
                }
            )
            continue

        kept.append(placement)

    return kept, {
        "schema_version": SCHEMA_VERSION,
        "kept_count": len(kept),
        "rejected_count": len(rejected),
        "kept": [
            {
                "visual_id": row.get("visual_id"),
                "section_id": row.get("section_id"),
                "role": row.get("cir_visual_role"),
                "citation": _citation(row.get("citation_label")),
            }
            for row in kept
        ],
        "rejected": rejected,
    }


def build_cir_quality_report(
    *,
    matrix: Mapping[str, Any],
    audit: Mapping[str, Any],
    postprocess: Mapping[str, Any],
    visual_report: Mapping[str, Any],
    final_guard: Mapping[str, Any],
) -> Dict[str, Any]:
    weak_verrous = [
        {
            "verrou_id": row.get("verrou_id"),
            "verrou_title": row.get("verrou_title"),
            "strength": row.get("strength"),
            "direct_count": row.get("direct_count"),
            "connected_count": row.get("connected_count"),
            "research_recommended": row.get("research_recommended"),
        }
        for row in matrix.get("verrous") or []
        if isinstance(row, Mapping)
        and row.get("strength") in {"FAIBLE", "INSUFFISANTE"}
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "publication_ready": bool(final_guard.get("ok"))
        and not list(audit.get("blocking_issues") or []),
        "evidence_matrix": dict(matrix),
        "weak_verrous": weak_verrous,
        "claim_guard": dict(audit),
        "postprocessing": dict(postprocess),
        "visual_policy": dict(visual_report),
        "final_guard_errors": list(
            final_guard.get("errors") or []
        ),
        "recommendations": [
            (
                f"Recherche complémentaire recommandée pour « "
                f"{row.get('verrou_title')} »: aucune preuve directe confirmée."
            )
            for row in weak_verrous
        ],
        "cost_policy": {
            "additional_llm_calls_added_by_v3": 0,
            "note": (
                "Le garde V3 est déterministe. Les coûts éventuels restent "
                "uniquement ceux des retries déjà prévus par Phase 5."
            ),
        },
    }
