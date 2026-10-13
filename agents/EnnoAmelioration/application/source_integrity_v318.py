from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

POLICY_VERSION = "ennoamel_source_integrity_v3_18"

_IMMUTABLE_RE = re.compile(
    r"\[BLOC DOCUMENT IMMUTABLE\b[\s\S]*?\[/BLOC DOCUMENT IMMUTABLE\]",
    flags=re.I,
)
_REFERENCE_START_RE = re.compile(
    r"(?m)^[ \t]*(?P<num>\d{1,3})[ \t]+(?=[A-ZÀ-ÖØ-Ý])"
)
_REFERENCE_SIGNAL_RE = re.compile(
    r"(?i)\b(?:19|20)\d{2}\b|"
    r"\b(?:doi|arxiv|ieee|acm|springer|elsevier|proceedings|conference|journal|"
    r"vol\.?|pp\.?|transactions?|society|remote sensing|cvpr|iccv|eusAR)\b"
)
_TOKEN_RE = re.compile(r"\[\[\s*ENNO_PROTECTED_V318_(\d{4})\s*\]\]", re.I)


@dataclass(frozen=True)
class ProtectedFragment:
    token: str
    kind: str
    identifier: str
    text: str


def _reference_fragments(text: str) -> list[tuple[int, int, str, str]]:
    """Repère les entrées bibliographiques numérotées sans toucher aux titres 1.2.3."""
    source = str(text or "")
    starts = list(_REFERENCE_START_RE.finditer(source))
    output: list[tuple[int, int, str, str]] = []

    for index, match in enumerate(starts):
        start = match.start()
        next_start = starts[index + 1].start() if index + 1 < len(starts) else len(source)

        # Une référence extraite d'un PDF est généralement séparée du corps par
        # un saut de paragraphe. On prend la borne la plus proche.
        double_break = source.find("\n\n", match.end())
        end = next_start
        if double_break >= 0 and double_break < next_start:
            end = double_break

        fragment = source[start:end].strip()
        if not fragment:
            continue
        if len(fragment) < 18 or len(fragment) > 2600:
            continue
        if not _REFERENCE_SIGNAL_RE.search(fragment):
            continue

        output.append(
            (
                start,
                end,
                str(match.group("num") or "").strip(),
                source[start:end],
            )
        )
    return output


def extract_protected_fragments(text: str) -> list[tuple[int, int, str, str]]:
    source = str(text or "")
    spans: list[tuple[int, int, str, str]] = []

    for match in _IMMUTABLE_RE.finditer(source):
        block = match.group(0)
        block_id_match = re.search(r'\bid="([^"]+)"', block, flags=re.I)
        block_id = block_id_match.group(1) if block_id_match else "unknown"
        spans.append((match.start(), match.end(), f"document_block:{block_id}", block))

    for start, end, number, fragment in _reference_fragments(source):
        # Ne jamais masquer deux fois une zone déjà incluse dans un bloc immutable.
        if any(start < old_end and end > old_start for old_start, old_end, _, _ in spans):
            continue
        spans.append((start, end, f"reference:{number}", fragment))

    spans.sort(key=lambda item: item[0])
    return spans


def mask_protected_text(text: str) -> tuple[str, list[ProtectedFragment]]:
    source = str(text or "")
    spans = extract_protected_fragments(source)
    if not spans:
        return source, []

    fragments: list[ProtectedFragment] = []
    output = source

    # Remplacement de droite à gauche pour conserver les offsets.
    for sequence, (start, end, identifier, fragment) in reversed(
        list(enumerate(spans, start=1))
    ):
        token = f"[[ENNO_PROTECTED_V318_{sequence:04d}]]"
        kind = "document_block" if identifier.startswith("document_block:") else "reference"
        fragments.append(
            ProtectedFragment(
                token=token,
                kind=kind,
                identifier=identifier,
                text=fragment,
            )
        )
        output = output[:start] + token + output[end:]

    fragments.reverse()
    return output, fragments


def prepare_writer_request(request: Any) -> tuple[Any, list[ProtectedFragment]]:
    """Masque uniquement les éléments qui ne doivent jamais être réécrits."""
    target = str(getattr(request, "target_text", "") or "")
    full = str(getattr(request, "full_text", "") or "")
    masked_target, fragments = mask_protected_text(target)

    if not fragments:
        return request, []

    masked_full = full
    if target and target in full:
        masked_full = full.replace(target, masked_target, 1)

    protected_contract = (
        "\n\nCONTRAT D'INTÉGRITÉ DOCUMENTAIRE V3.18\n"
        "Les jetons [[ENNO_PROTECTED_V318_XXXX]] représentent des figures, tableaux "
        "ou références bibliographiques du document source. Ils sont STRICTEMENT "
        "IMMUTABLES : conserve chaque jeton exactement une fois, au même endroit "
        "logique. Ne le reformule pas, ne le développe pas et ne le supprime pas."
    )

    updated = request.model_copy(
        update={
            "target_text": masked_target,
            "full_text": masked_full,
            "instruction": str(getattr(request, "instruction", "") or "") + protected_contract,
        }
    )
    return updated, fragments


def restore_protected_candidate(
    candidate: str,
    fragments: list[ProtectedFragment],
) -> tuple[str, dict[str, Any]]:
    text = str(candidate or "")
    if not fragments:
        return text, {
            "policy_version": POLICY_VERSION,
            "required_count": 0,
            "missing_tokens": [],
            "duplicated_tokens": [],
            "restored_count": 0,
            "complete": True,
        }

    missing: list[str] = []
    duplicated: list[str] = []
    restored = text

    for fragment in fragments:
        token_number = re.search(r"(\d{4})", fragment.token)
        number = token_number.group(1) if token_number else ""
        token_re = re.compile(
            rf"\[\[\s*ENNO_PROTECTED_V318_{re.escape(number)}\s*\]\]",
            flags=re.I,
        )
        matches = list(token_re.finditer(restored))
        if not matches:
            missing.append(fragment.identifier)
            continue
        if len(matches) > 1:
            duplicated.append(fragment.identifier)

        # Remplace toutes les occurrences mais signale les duplications.
        restored = token_re.sub(lambda _m, value=fragment.text: value, restored)

    report = {
        "policy_version": POLICY_VERSION,
        "required_count": len(fragments),
        "missing_tokens": missing,
        "duplicated_tokens": duplicated,
        "restored_count": len(fragments) - len(missing),
        "complete": not missing and not duplicated,
    }
    return restored, report


def integrity_issues_from_protection(report: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for value in report.get("missing_tokens") or []:
        issues.append(f"protected_fragment_missing:{value}")
    for value in report.get("duplicated_tokens") or []:
        issues.append(f"protected_fragment_duplicated:{value}")
    return issues


def hard_conservation_issues(issues: list[str]) -> list[str]:
    prefixes = (
        "document_block_missing:",
        "document_block_changed:",
        "references_perdues:",
        "renvois_visuels_perdus:",
        "mesures_perdues:",
        "titres_perdus:",
        "liens_perdus:",
        "protected_fragment_missing:",
        "protected_fragment_duplicated:",
        "source_fact_missing:",
        "source_fact_altered:",
    )
    return [
        str(issue)
        for issue in (issues or [])
        if str(issue).startswith(prefixes)
    ]
