from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, asdict
from typing import Any


_VERSION = "research_text_normalizer_v3_10"


@dataclass
class ResearchNormalizationReport:
    version: str
    raw_chars: int
    normalized_chars: int
    removed_reference_blocks: int
    removed_reference_markers: list[int]
    inferred_reference_markers: list[int]
    inline_marker_repairs: list[dict[str, Any]]
    protected_enumerated_codes: list[str]
    raw_sha256: str
    normalized_sha256: str
    semantic_rewriting: bool = False
    active_version_modified: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha(value: str) -> str:
    return hashlib.sha256(
        str(value or "").encode("utf-8", errors="ignore")
    ).hexdigest()


def _normalize_unicode(value: Any) -> str:
    text = unicodedata.normalize("NFC", str(value or ""))
    text = (
        text.replace("\u00ad", "")
        .replace("\u200b", "")
        .replace("\ufeff", "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )
    return text


def _reference_score(body: str) -> int:
    """Score structurel d'un bloc bibliographique / note.

    Aucun auteur, domaine ou projet n'est codé en dur.
    """

    value = " ".join(str(body or "").split())
    if not value:
        return 0

    score = 0

    if re.search(r"https?://|www\.|doi\.org|arxiv\.org|hal\.", value, re.I):
        score += 3

    if re.search(r"\b(?:19|20)\d{2}\b", value):
        score += 1

    if re.search(
        r"\b(?:doi|vol\.?|volume|pages?|pp\.?|proceedings|conference|"
        r"journal|transactions|symposium|workshop|coRR)\b",
        value,
        re.I,
    ):
        score += 1

    # Forme auteur fréquente : "Nom, X." ou plusieurs séparateurs de noms.
    if len(re.findall(r",", value)) >= 2:
        score += 1

    if re.search(r"[“\"].{8,180}[”\"]", value):
        score += 1

    if len(value) >= 80:
        score += 1

    return score


def _split_paragraphs(text: str) -> list[str]:
    return [
        block.strip()
        for block in re.split(r"\n[ \t]*\n+", str(text or ""))
        if block.strip()
    ]


def _remove_reference_blocks(
    text: str,
) -> tuple[str, list[int], list[str]]:
    """Retire uniquement les paragraphes numérotés qui ressemblent à des références."""

    kept: list[str] = []
    markers: list[int] = []
    removed: list[str] = []

    for block in _split_paragraphs(text):
        match = re.match(
            r"^\s*(?P<num>\d{1,2})(?:[.)])?[ \t]+(?P<body>[\s\S]+)$",
            block,
        )
        if not match:
            kept.append(block)
            continue

        number = int(match.group("num"))
        body = match.group("body").strip()
        score = _reference_score(body)

        # Une URL numérotée est une référence même si le bloc est court.
        url_reference = bool(
            re.match(r"^(?:https?://|www\.)", body, re.I)
        )

        if url_reference or score >= 2:
            markers.append(number)
            removed.append(block)
            continue

        kept.append(block)

    return "\n\n".join(kept), sorted(set(markers)), removed


def _inferred_marker_numbers(explicit: list[int]) -> set[int]:
    """Infère au maximum les deux notes suivantes si une séquence est visible.

    Exemple générique : si les définitions 1, 2, 3 sont dans l'extrait mais que
    les notes 4 et 5 tombent sur la page suivante, les marqueurs 4/5 restent
    plausibles. Aucun numéro n'est inventé au-delà de deux positions.
    """

    values = sorted(set(int(x) for x in explicit if 1 <= int(x) <= 30))
    if len(values) < 2:
        return set(values)

    # On n'infère que si la fin de la séquence est réellement consécutive.
    tail = values[-1]
    if (tail - 1) not in values:
        return set(values)

    return set(values) | {tail + 1, tail + 2}


def _enumerated_code_tokens(text: str) -> set[str]:
    """Détecte les longues listes parenthétiques de codes/instances.

    Une parenthèse contenant au moins quatre éléments courts séparés par des
    virgules est traitée comme une liste d'instances, pas comme une liste de
    concepts scientifiques principaux.
    """

    protected: set[str] = set()

    for match in re.finditer(r"\(([^()\n]{5,500})\)", str(text or "")):
        content = match.group(1)
        parts = [part.strip() for part in content.split(",")]
        if len(parts) < 4:
            continue

        simple = [
            part
            for part in parts
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/+:-]{0,15}", part)
        ]
        if len(simple) / max(1, len(parts)) < 0.75:
            continue

        protected.update(simple)

    return protected


def _stem_occurs_standalone(text: str, stem: str, full_token: str) -> bool:
    shadow = re.sub(
        rf"(?<![A-Za-z0-9]){re.escape(full_token)}(?![A-Za-z0-9])",
        " ",
        text,
        flags=re.I,
    )
    return bool(
        re.search(
            rf"(?<![A-Za-z0-9]){re.escape(stem)}(?![A-Za-z0-9])",
            shadow,
            flags=re.I,
        )
    )


def _looks_like_expansion_after(text: str, end: int) -> bool:
    tail = text[end:end + 240]
    match = re.match(r"\s*\(([^)]{8,220})\)", tail)
    if not match:
        return False

    inside = match.group(1)
    words = re.findall(r"[A-Za-zÀ-ÿ]{2,}", inside)
    return len(words) >= 3


def _repair_inline_reference_markers(
    text: str,
    *,
    explicit_markers: list[int],
    protected_codes: set[str],
) -> tuple[str, list[dict[str, Any]], list[int]]:
    """Répare MSTAR3 -> MSTAR seulement si le chiffre ressemble à une note.

    Protections importantes :
    - les codes d'une longue énumération restent intacts (BMP2, BTR60, etc.) ;
    - les identifiants contenant déjà d'autres chiffres ne sont pas touchés ;
    - un token n'est corrigé que si plusieurs indices convergent.
    """

    likely_numbers = _inferred_marker_numbers(explicit_markers)
    repairs: list[dict[str, Any]] = []

    # Stem uniquement alphabétique : BMP2 est éligible syntaxiquement mais sera
    # protégé par la détection de liste ; T72/ZSU23 ne correspondent pas.
    pattern = re.compile(
        r"(?<![A-Za-z0-9])(?P<stem>[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ_-]{2,})"
        r"(?P<num>[1-9])(?![A-Za-z0-9])"
    )

    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        stem = match.group("stem")
        number = int(match.group("num"))

        if token in protected_codes:
            return token

        explicit_or_inferred = number in likely_numbers
        standalone_elsewhere = _stem_occurs_standalone(text, stem, token)
        followed_by_expansion = _looks_like_expansion_after(text, match.end())
        natural_word_marker = bool(
            stem[:1].islower()
            and explicit_or_inferred
            and re.match(r"[\s.,;:)\]]", text[match.end():match.end() + 1] or " ")
        )

        # Deux voies sûres :
        # 1) un sigle/nombre est confirmé par sa forme sans chiffre ailleurs
        #    ou par une expansion immédiatement après ;
        # 2) un mot courant en minuscules porte un numéro appartenant à la
        #    séquence de notes observée/inférée.
        should_repair = bool(
            standalone_elsewhere
            or followed_by_expansion
            or natural_word_marker
        )

        if not should_repair:
            return token

        reasons: list[str] = []
        if number in explicit_markers:
            reasons.append("explicit_reference_number")
        elif explicit_or_inferred:
            reasons.append("inferred_reference_number")
        if standalone_elsewhere:
            reasons.append("same_stem_exists_without_number")
        if followed_by_expansion:
            reasons.append("followed_by_parenthetical_expansion")
        if natural_word_marker:
            reasons.append("natural_word_plus_reference_number")

        repairs.append({
            "before": token,
            "after": stem,
            "marker": number,
            "reasons": reasons,
        })
        return stem

    repaired = pattern.sub(replace, text)
    return repaired, repairs, sorted(likely_numbers)


def normalize_research_section_text(
    raw_text: Any,
) -> tuple[str, dict[str, Any]]:
    """Produit une vue nettoyée POUR LA RECHERCHE, sans modifier la version active."""

    raw = _normalize_unicode(raw_text)
    protected_codes = _enumerated_code_tokens(raw)

    without_refs, explicit_markers, removed_blocks = _remove_reference_blocks(raw)

    repaired, repairs, inferred_markers = _repair_inline_reference_markers(
        without_refs,
        explicit_markers=explicit_markers,
        protected_codes=protected_codes,
    )

    # Nettoyage purement typographique après suppression des références.
    repaired = re.sub(r"[ \t]+", " ", repaired)
    repaired = re.sub(r" *\n *", "\n", repaired)
    repaired = re.sub(r"\n{3,}", "\n\n", repaired)
    repaired = repaired.strip()

    report = ResearchNormalizationReport(
        version=_VERSION,
        raw_chars=len(raw),
        normalized_chars=len(repaired),
        removed_reference_blocks=len(removed_blocks),
        removed_reference_markers=explicit_markers,
        inferred_reference_markers=inferred_markers,
        inline_marker_repairs=repairs,
        protected_enumerated_codes=sorted(protected_codes),
        raw_sha256=_sha(raw),
        normalized_sha256=_sha(repaired),
    )
    return repaired, report.to_dict()
