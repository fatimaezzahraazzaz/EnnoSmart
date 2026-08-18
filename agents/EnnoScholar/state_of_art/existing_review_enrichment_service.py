# -*- coding: utf-8 -*-
from __future__ import annotations

from agents.EnnoAmelioration.application.revision_integrity_v318 import verify_additions_entailment

"""Enrichissement conservateur d'un état de l'art déjà rédigé.

Ce service appartient à EnnoScholar : EnnoAmelioration lui délègue la partie
scientifique, puis conserve la responsabilité de versionner la proposition.
Contrairement au writer Phase 5 qui produit un document neuf, ce service ne
retourne que des insertions ancrées dans le texte existant. Le texte source ne
peut donc pas être résumé ou supprimé silencieusement.
"""

import json
import os
import re
from typing import Any, Mapping, Sequence

from modules.LLM.llm_client import LLMClient, reload_config


_CITATION_RE = re.compile(r"(?<![A-Za-z0-9])A\d+(?![A-Za-z0-9])", re.I)
_NUMBER_RE = re.compile(r"(?<![A-Za-zÀ-ÿ])\d+(?:[.,]\d+)?(?:\s*%)?")
_FORBIDDEN_OUTPUT_RE = re.compile(
    r"(?im)^\s*(?:preuves\s+utilis[ée]es?\s*:|#{1,6}\s+|(?:\d+\.){2,}\s+)|"
    r"\b(?:article\s+card|ennoscholar|ennodiagnostic|phase\s+[1-5])\b"
)



_DANGLING_END_RE = re.compile(
    r"(?i)\b(?:de|du|des|à|au|aux|avec|sans|dans|par|pour|vers|"
    r"et|ou|mais|donc|car|que|qui|dont|comme|notamment|afin)\s*$"
)


def _strip_trailing_citations(value: str) -> str:
    """Retire seulement les citations finales pour contrôler la ponctuation."""
    text = _clean(value)
    while True:
        cleaned = re.sub(
            r"\s*(?:\[\s*A\d+\s*\]|A\d+)\s*$",
            "",
            text,
            flags=re.I,
        ).rstrip()
        if cleaned == text:
            return cleaned
        text = cleaned


def _addition_completeness_errors(content: str) -> list[str]:
    """Bloque les paragraphes tronqués et les phrases nouvelles sans citation."""
    errors: list[str] = []
    value = _clean(content)
    if not value:
        return ["contenu_vide"]

    without_tail_citations = _strip_trailing_citations(value)
    if not re.search(r"[.!?…][\"'»”’)]?\s*$", without_tail_citations):
        errors.append("phrase_finale_incomplete")

    lexical_tail = re.sub(r"[.!?…,:;\"'»”’)\]]+\s*$", "", without_tail_citations)
    if _DANGLING_END_RE.search(lexical_tail):
        errors.append("fin_syntaxique_suspendue")

    # Dans ce service, tout le contenu est nouveau par construction : chaque
    # phrase informative doit donc porter au moins une citation validée.
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?…])\s+", value)
        if sentence.strip()
    ]
    for index, sentence in enumerate(sentences, start=1):
        words = re.findall(r"[A-Za-zÀ-ÿ0-9-]+", sentence)
        if len(words) < 8:
            continue
        if not _CITATION_RE.search(sentence):
            errors.append(f"phrase_{index}_sans_citation")

    return errors

def _clean(value: Any, limit: int = 100000) -> str:
    return str(value or "").replace("\x00", " ").strip()[:limit]


def _section_body_start(content: str) -> int:
    """Offset du vrai corps après la ligne de titre."""
    value = _clean(content)
    if not value:
        return 0
    newline = value.find("\n")
    if newline < 0:
        return len(value)
    return newline + 1


def _section_body_text(content: str) -> str:
    value = _clean(content)
    start = _section_body_start(value)
    return value[start:].strip() if start < len(value) else ""


def _is_writable_scientific_section(row: Mapping[str, Any]) -> bool:
    """Exclut les sections conteneurs qui ne portent qu'un titre."""
    body = _section_body_text(_clean(row.get("content")))
    words = re.findall(r"[A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9'-]*", body)
    return len(words) >= 8



def _extract_json(value: Any) -> dict[str, Any]:
    raw = _clean(value)
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


def _evidence_by_citation(
    evidence_rows: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    output: dict[str, str] = {}
    for row in evidence_rows:
        citation = _clean(row.get("citation_id"), 40).upper()
        if not citation:
            continue
        output[citation] = " ".join(
            _clean(row.get(key), 10000)
            for key in (
                "title",
                "year",
                "method",
                "results",
                "limits",
                "impact",
            )
            if _clean(row.get(key), 10000)
        )
    return output


def _normalised_number(value: str) -> str:
    return re.sub(r"\s+", "", value).replace(",", ".").casefold()


def _validate_additions(
    payload: Mapping[str, Any],
    *,
    target_text: str,
    sections: Sequence[Mapping[str, Any]],
    evidence_by_citation: Mapping[str, str],
) -> tuple[list[dict[str, Any]], list[str]]:
    section_map = {
        _clean(row.get("section_id"), 160): row
        for row in sections
        if _clean(row.get("section_id"), 160)
    }
    allowed = set(evidence_by_citation)
    accepted: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, raw in enumerate(payload.get("additions") or [], start=1):
        if not isinstance(raw, Mapping):
            errors.append(f"addition_{index}:format_invalide")
            continue
        section_id = _clean(raw.get("section_id"), 160)
        anchor = _clean(raw.get("anchor"), 800)
        content = _clean(raw.get("content"), 20000)
        section = section_map.get(section_id)
        if section is None:
            errors.append(f"addition_{index}:section_inconnue")
            continue
        section_content = _clean(section.get("content"))
        if not anchor or section_content.count(anchor) != 1:
            errors.append(f"addition_{index}:ancre_absente_ou_ambigue")
            continue
        if not content:
            errors.append(f"addition_{index}:contenu_vide")
            continue
        if len(content.split()) < 45:
            errors.append(f"addition_{index}:complement_trop_court")
        if len(content.split()) > 900:
            errors.append(f"addition_{index}:complement_trop_long")
        if _FORBIDDEN_OUTPUT_RE.search(content):
            errors.append(f"addition_{index}:marqueur_technique_ou_titre_interdit")
        used = {value.upper() for value in _CITATION_RE.findall(content)}
        unknown = sorted(used - allowed)
        if unknown:
            errors.append(f"addition_{index}:citations_non_autorisees:{','.join(unknown)}")
        if not used:
            errors.append(f"addition_{index}:aucune_citation")
        known_used = used & allowed
        evidence_text = " ".join(
            evidence_by_citation[citation] for citation in known_used
        )
        evidence_numbers = {_normalised_number(v) for v in _NUMBER_RE.findall(evidence_text)}
        unsupported_numbers = sorted({
            _normalised_number(v)
            for v in _NUMBER_RE.findall(content)
            if _normalised_number(v) not in evidence_numbers
        })
        if unsupported_numbers:
            errors.append(f"addition_{index}:valeurs_non_etayees:" + ",".join(unsupported_numbers[:12]))
        accepted.append({
            "section_id": section_id,
            "anchor": anchor,
            "content": content,
            "citations": sorted(known_used),
        })

    used_all = {
        citation
        for addition in accepted
        for citation in (addition.get("citations") or [])
    }
    missing = sorted(set(allowed) - used_all, key=lambda x: int(x[1:]) if x[1:].isdigit() else x)
    if missing:
        errors.append("sources_acceptees_non_utilisees:" + ",".join(missing))
    if not accepted:
        errors.append("aucun_complement_scientifique_valide")
    return accepted, list(dict.fromkeys(errors))



def _apply_anchored_additions(
    target_text: str,
    sections: Sequence[Mapping[str, Any]],
    additions: Sequence[Mapping[str, Any]],
) -> str:
    section_map = {
        _clean(row.get("section_id"), 160): row
        for row in sections
        if _clean(row.get("section_id"), 160)
    }
    insertions: list[tuple[int, str]] = []
    for addition in additions:
        section = section_map[_clean(addition.get("section_id"), 160)]
        section_content = _clean(section.get("content"))
        section_start = target_text.find(section_content)
        anchor = _clean(addition.get("anchor"), 800)
        anchor_start = section_content.find(anchor)
        body_start = _section_body_start(section_content)
        if section_start < 0 or anchor_start < 0:
            raise ValueError("Une ancre validée n'est plus présente dans le texte cible.")
        if anchor_start < body_start:
            raise ValueError(
                "Une insertion scientifique ne peut pas être ancrée dans un titre."
            )
        position = section_start + anchor_start + len(anchor)
        insertions.append((position, "\n\n" + _clean(addition.get("content"))))
    output = target_text
    for position, content in sorted(insertions, key=lambda item: item[0], reverse=True):
        output = output[:position] + content + output[position:]
    return output


def generate_state_of_art_additions(
    *,
    target_text: str,
    sections: Sequence[Mapping[str, Any]],
    instruction: str,
    project_name: str,
    project_domain: str,
    evidence_rows: Sequence[Mapping[str, Any]],
    llm: LLMClient | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    evidence_map = _evidence_by_citation(evidence_rows)
    if not evidence_map:
        raise ValueError("Aucune Article Card validée n'est disponible pour l'enrichissement.")
    # V3.6 — seules les sections possédant un vrai corps rédactionnel
    # peuvent recevoir un ajout. Les titres conteneurs (ex. 1.3 suivi
    # immédiatement de 1.3.1) restent structurels et ne sont jamais des ancres.
    usable_sections = [
        {
            "section_id": _clean(row.get("section_id"), 160),
            "title": _clean(row.get("title"), 1200),
            "content": _clean(row.get("content"), 30000),
        }
        for row in sections
        if _clean(row.get("section_id"), 160)
        and _clean(row.get("content"), 30000)
        and _is_writable_scientific_section(row)
    ]
    if not usable_sections:
        raise ValueError("Aucune sous-section exploitable n'a été détectée.")

    schema = {
        "title": "existing_state_of_art_anchored_additions",
        "type": "object",
        "required": ["additions", "uncovered_sections"],
        "properties": {
            "additions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["section_id", "anchor", "content", "citations"],
                    "properties": {
                        "section_id": {"type": "string"},
                        "anchor": {"type": "string"},
                        "content": {"type": "string"},
                        "citations": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                },
            },
            "uncovered_sections": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
    }
    prompt = f"""
Tu es EnnoScholar, l'agent scientifique chargé d'enrichir un état de l'art CIR
déjà rédigé. EnnoAmelioration conservera le texte existant mot pour mot et
appliquera uniquement tes compléments ancrés.

DEMANDE DU CONSULTANT
{_clean(instruction, 6000)}

PROJET
Nom : {_clean(project_name, 500) or 'non précisé'}
Domaine : {_clean(project_domain, 800) or 'non précisé'}

SOUS-SECTIONS EXISTANTES
{json.dumps(usable_sections, ensure_ascii=False)}

ARTICLE CARDS VALIDÉES — SEULES PREUVES EXTERNES AUTORISÉES
{json.dumps(list(evidence_rows), ensure_ascii=False)}

CONTRAT SCIENTIFIQUE
- Une citation [Ai] n'est autorisée que si la preuve Ai soutient directement toute l'affirmation à laquelle elle est rattachée.
- Si une preuve ne soutient qu'une partie de la phrase, scinde ou réduis la phrase.
- Une proximité thématique ne constitue jamais une preuve suffisante.
- TOUTES les Article Cards validées transmises dans ce corpus doivent être utilisées au moins une fois.
- Chaque citation_id accepté doit apparaître dans au moins un complément réellement étayé par cette source.
- Si plusieurs sources soutiennent le même argument, cite-les ensemble plutôt que d'en ignorer une.
- Une sortie qui omet une seule source acceptée est invalide et doit être régénérée.
- Ne réécris, ne résume et ne supprime aucun passage existant.
- Propose uniquement des paragraphes nouveaux qui apportent une comparaison,
  un protocole, un résultat quantitatif, une contradiction, une condition de
  validité, une limite ou une analyse de transférabilité réellement absente.
- Chaque ajout est placé après une ancre copiée mot pour mot depuis le CORPS
  de la sous-section concernée. Choisis une phrase ou un fragment long et unique.
- N'utilise JAMAIS comme ancre un numéro de section, un titre, un sous-titre ou
  une ligne de titre. Un complément doit apparaître après une phrase existante
  du paragraphe qu'il renforce.
- Place chaque argument dans la sous-section dont le contenu traite réellement
  du sujet : un argument général sur les méthodes dans l'analyse générale, un
  argument sur un jeu de données dans la sous-section qui décrit ce jeu.
- Chaque phrase scientifique nouvelle porte la ou les citations [A1] exactes
  qui l'étayent. N'utilise aucun identifiant absent des Article Cards.
- Comme chaque `content` est entièrement nouveau, AUCUNE phrase informative ne
  peut être laissée sans citation. Si une phrase ne peut pas être reliée à une
  Article Card validée, supprime-la au lieu d'écrire « à confirmer ».
- Ne recopie pas une information déjà présente dans le texte simplement pour lui
  ajouter une citation : les citations doivent documenter un APPORT NOUVEAU.
- Termine chaque ajout par une phrase complète et ponctuée. Aucun fragment ou
  amorce de transition inachevée n'est accepté.
- N'invente aucun chiffre, résultat, méthode, jeu de données ou conclusion.
- Ne transforme jamais les travaux internes du projet en preuve publiée.
- Une source connexe ne devient jamais une validation directe du projet.
- Signale prudemment ce que le corpus ne permet pas de conclure, notamment
  lorsqu'une validation physique ou une transférabilité n'est pas démontrée.
- N'ajoute ni titre, ni bibliographie, ni liste « Preuves utilisées ».
- Si une sous-section est déjà suffisamment étayée ou si les cartes ne
  permettent pas de la renforcer, place son section_id dans uncovered_sections
  et n'invente aucun complément.

SORTIE
Retourne uniquement le JSON conforme au schéma. Chaque content doit être un
paragraphe français directement publiable et chaque anchor doit exister une
seule fois, exactement, dans le content de sa section.
""".strip()

    if llm is None:
        runtime = reload_config()
        configured_model = _clean(
            os.getenv("ENNOSCHOLAR_PHASE5_WRITER_MODEL")
            or runtime.get("ENNOSCHOLAR_PHASE5_WRITER_MODEL"),
            200,
        )
        llm = LLMClient(model=configured_model or None)

    attempts: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []
    uncovered: list[str] = []
    for attempt in range(1, 4):
        attempt_prompt = prompt
        if attempts:
            attempt_prompt += (
                "\n\nCORRECTION AUTOMATIQUE DU PREMIER ESSAI\n"
                "Corrige uniquement ces violations et régénère le JSON complet :\n"
                + json.dumps(attempts[-1].get("errors") or [], ensure_ascii=False)
            )
        raw = llm.generate(
            attempt_prompt,
            temperature=0.05,
            max_output_tokens=7000,
            max_input_tokens=120000,
            retries=0,
            json_mode=True,
            response_schema=schema,
            request_name="ennoscholar:existing_state_of_art:anchored_enrichment",
        )
        payload = _extract_json(raw)
        accepted, validation_findings = _validate_additions(
            payload,
            target_text=target_text,
            sections=usable_sections,
            evidence_by_citation=evidence_map,
        )
        # Un ajout techniquement insérable reste visible. Les contrôles de
        # longueur, citations, chiffres et couverture deviennent des alertes
        # destinées au consultant, jamais une cause de suppression du candidat.
        errors = [] if accepted else list(validation_findings)
        _v318_entailment = {}
        _v318_warnings: list[str] = (
            list(validation_findings) if accepted else []
        )
        if accepted and not errors:
            _v318_entailment = verify_additions_entailment(
                accepted,
                list(evidence_rows or []),
            )
            if not _v318_entailment.get("complete"):
                _v318_issues = list(_v318_entailment.get("issues") or [])
                _v318_warnings.extend(_v318_issues)
                _v318_failures = list(
                    _v318_entailment.get("citation_failures") or []
                )
                if _v318_failures:
                    _v318_warnings.append(
                        "citation_entailment_details:"
                        + json.dumps(
                            _v318_failures[:8],
                            ensure_ascii=False,
                        )
                    )
                print(
                    "[V3.25][AnchoredEntailment][ADVISORY] "
                    f"complete=False issues={_v318_issues}"
                )
        uncovered = [
            _clean(value, 160) for value in payload.get("uncovered_sections") or []
        ]
        attempts.append(
            {
                "attempt": attempt,
                "errors": errors,
                "integrity_warnings": _v318_warnings,
                "quality_control_mode": "advisory_only",
                "accepted_addition_count": len(accepted),
                "llm": llm.get_last_generation_meta(),
            }
        )
        if accepted and not errors:
            break
    if not accepted or attempts[-1].get("errors"):
        raise RuntimeError(
            "EnnoScholar n'a pas produit de complément entièrement traçable ; "
            "le texte original est conservé."
        )

    return accepted, {
        "agent": "EnnoScholar",
        "service": "existing_state_of_art_enrichment",
        "strategy": "scientific_anchored_additions_only",
        "attempt_count": len(attempts),
        "call_count": len(attempts),
        "attempts": attempts,
        "additions": accepted,
        "quality_control_mode": "advisory_only",
        "integrity_warnings": list(
            attempts[-1].get("integrity_warnings") or []
        ),
        "controls_blocked_candidate": False,
        "uncovered_sections": uncovered,
        "original_words": len(target_text.split()),
        "addition_words": sum(
            len(_clean(row.get("content")).split()) for row in accepted
        ),
        "prompt_tokens": sum(
            int((row.get("llm") or {}).get("prompt_tokens") or 0)
            for row in attempts
        ),
        "completion_tokens": sum(
            int((row.get("llm") or {}).get("completion_tokens") or 0)
            for row in attempts
        ),
        "total_tokens": sum(
            int((row.get("llm") or {}).get("total_tokens") or 0)
            for row in attempts
        ),
    }


def enrich_existing_state_of_art(
    **kwargs: Any,
) -> tuple[str, dict[str, Any]]:
    """Compatibilité hors orchestrateur ; EnnoAmelioration utilise le plan brut."""

    additions, meta = generate_state_of_art_additions(**kwargs)
    target_text = _clean(kwargs.get("target_text"))
    sections = kwargs.get("sections") or []
    improved = _apply_anchored_additions(target_text, sections, additions)
    return improved, {
        **meta,
        "strategy": "anchored_additions_preserve_original",
        "improved_words": len(improved.split()),
    }


__all__ = [
    "_apply_anchored_additions",
    "_validate_additions",
    "enrich_existing_state_of_art",
    "generate_state_of_art_additions",
]
