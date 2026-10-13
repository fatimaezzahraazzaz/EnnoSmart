
# -*- coding: utf-8 -*-

"""
EnnoSmart V188
Nettoyage conservateur post-extraction.

Corrige uniquement :
- Unicode / ligatures ;
- caract?res de contr?le ;
- c?sures de mise en page ;
- espaces ;
- doublons adjacents ;
- headers / footers r?p?t?s.

Ne reformule pas.
Ne filtre pas les verrous.
Ne supprime pas les r?sultats scientifiques.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata

from collections import Counter
from typing import Any, Sequence


VERSION = (
    "extraction_post_cleaner_v188_"
    "conservative_traceable"
)


LIGATURES = {
    "?": "fi",
    "?": "fl",
    "?": "ff",
    "?": "ffi",
    "?": "ffl",

    "\u00ad": "",
    "\u200b": "",
    "\ufeff": "",
}


CONTROL = re.compile(
    r"[\x00-\x08\x0b\x0c"
    r"\x0e-\x1f\x7f]"
)


TRACE = re.compile(
    r"^\s*\[[^\]]+\]"
)


PAGE_NUMBER = re.compile(
    r"^(?:page\s*)?"
    r"\d{1,4}"
    r"(?:\s*(?:/|sur|of)\s*"
    r"\d{1,4})?$",
    flags=re.I,
)


def _hash(chunks):

    text = "\n\n".join(
        str(chunk or "")
        for chunk in chunks
    )

    return hashlib.sha256(
        text.encode(
            "utf-8",
            errors="ignore",
        )
    ).hexdigest()


def _clean_one(text):

    report = {
        "dehyphenations": 0,
        "control_chars_removed": 0,
        "adjacent_duplicates_removed": 0,
    }


    text = unicodedata.normalize(
        "NFC",
        str(text or ""),
    )


    text = text.replace(
        "\r\n",
        "\n",
    ).replace(
        "\r",
        "\n",
    )


    for old, new in (
        LIGATURES.items()
    ):
        text = text.replace(
            old,
            new,
        )


    before = len(text)

    text = CONTROL.sub(
        "",
        text,
    )

    report[
        "control_chars_removed"
    ] = (
        before - len(text)
    )


    # c?sure r?elle de mise en page
    pattern = re.compile(
        r"([^\W\d_]{2,})"
        r"-\s*\n\s*"
        r"([^\W\d_]{2,})",
        flags=re.UNICODE,
    )


    text, count = pattern.subn(
        r"\1\2",
        text,
    )

    report[
        "dehyphenations"
    ] = count


    output = []
    previous = None


    for line in text.splitlines():

        line = line.replace(
            "\t",
            " ",
        )

        line = re.sub(
            r"[ ]{2,}",
            " ",
            line,
        ).strip()


        key = re.sub(
            r"\s+",
            " ",
            line.lower(),
        ).strip()


        if (
            key
            and previous
            and key == previous
            and not TRACE.match(
                line
            )
        ):

            report[
                "adjacent_duplicates_removed"
            ] += 1

            continue


        output.append(line)


        if key:
            previous = key


    cleaned = "\n".join(
        output
    )


    cleaned = re.sub(
        r"\n{4,}",
        "\n\n\n",
        cleaned,
    )


    return (
        cleaned.strip(),
        report,
    )


def _boundary_key(line):

    line = str(
        line or ""
    ).strip()


    if (
        not line
        or TRACE.match(line)
    ):
        return ""


    line = unicodedata.normalize(
        "NFKC",
        line.lower(),
    )


    line = re.sub(
        r"\d+",
        "#",
        line,
    )


    line = re.sub(
        r"\s+",
        " ",
        line,
    )


    return line.strip(
        " -_|"
    )


def _find_repeated_boundaries(
    chunks,
):

    if len(chunks) < 3:
        return set()


    counts = Counter()


    for chunk in chunks:

        lines = [
            line.strip()

            for line in str(
                chunk or ""
            ).splitlines()

            if line.strip()
        ]


        # Les modèles bureautiques peuvent produire un en-tête sur 5 à 8
        # lignes (logo, titre courant, année) et un pied sur 3 à 5 lignes. Une
        # fenêtre de trois lignes laissait donc des fragments récurrents dans
        # le corps. La suppression reste conservatrice : une ligne n'est
        # retirée que si sa signature revient sur une part importante des pages.
        candidates = (
            lines[:12]
            + lines[-8:]
        )


        seen = set()


        for line in candidates:

            key = _boundary_key(
                line
            )


            if not key:
                continue


            if len(key) > 180:
                continue


            if key in seen:
                continue


            seen.add(key)
            counts[key] += 1


    threshold = max(
        3,

        math.ceil(
            len(chunks)
            * 0.45
        ),
    )


    return {
        key
        for key, count
        in counts.items()
        if count >= threshold
    }


def _remove_boundaries(
    chunks,
    repeated,
):

    cleaned = []
    removed = 0


    for chunk in chunks:

        lines = str(
            chunk or ""
        ).splitlines()


        positions = [
            index
            for index, line
            in enumerate(lines)
            if line.strip()
        ]


        boundaries = set(
            positions[:12]
            + positions[-8:]
        )


        output = []


        for index, line in enumerate(
            lines
        ):

            stripped = line.strip()


            if TRACE.match(
                stripped
            ):
                output.append(line)
                continue


            if index in boundaries:

                key = _boundary_key(
                    stripped
                )


                if (
                    key
                    and key in repeated
                ):
                    removed += 1
                    continue


                if (
                    stripped
                    and PAGE_NUMBER.match(
                        stripped
                    )
                ):
                    removed += 1
                    continue


            output.append(line)


        cleaned.append(
            "\n".join(
                output
            ).strip()
        )


    return cleaned, removed


def clean_text_chunks(
    chunks: Sequence[str],
):

    original = [
        str(chunk or "")
        for chunk in chunks
    ]


    before_chars = sum(
        len(chunk)
        for chunk in original
    )


    totals = Counter()
    first = []


    for chunk in original:

        cleaned, report = (
            _clean_one(chunk)
        )

        first.append(cleaned)
        totals.update(report)


    repeated = (
        _find_repeated_boundaries(
            first
        )
    )


    second, removed = (
        _remove_boundaries(
            first,
            repeated,
        )
    )


    final = [
        chunk
        for chunk in second
        if chunk.strip()
    ]


    after_chars = sum(
        len(chunk)
        for chunk in final
    )


    report = {

        "version":
            VERSION,

        "chunks_before":
            len(original),

        "chunks_after":
            len(final),

        "chars_before":
            before_chars,

        "chars_after":
            after_chars,

        "chars_removed":
            max(
                0,
                before_chars
                - after_chars,
            ),

        "dehyphenations":
            totals[
                "dehyphenations"
            ],

        "control_chars_removed":
            totals[
                "control_chars_removed"
            ],

        "adjacent_duplicates_removed":
            totals[
                "adjacent_duplicates_removed"
            ],

        "repeated_boundary_lines_removed":
            removed,

        "repeated_boundary_patterns_count":
            len(repeated),

        "sha256_before":
            _hash(original),

        "sha256_after":
            _hash(final),

        "semantic_rewriting":
            False,

        "project_specific_rules":
            False,
    }


    return final, report


def post_clean_extraction_result(
    result: Any,
):

    if result is None:
        return result


    chunks = list(
        getattr(
            result,
            "text_chunks",
            [],
        )
        or []
    )


    if not chunks:
        return result


    cleaned, report = (
        clean_text_chunks(
            chunks
        )
    )


    result.text_chunks = cleaned


    try:
        result.post_cleaning_report = (
            report
        )
    except Exception:
        pass


    tags = list(
        getattr(
            result,
            "tags",
            [],
        )
        or []
    )


    tags.extend([
        "POST_EXTRACTION_CLEANED",
        "POST_CLEAN_VERSION:V188",
    ])


    result.tags = list(
        dict.fromkeys(tags)
    )


    return result
