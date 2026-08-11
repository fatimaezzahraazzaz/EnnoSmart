
# -*- coding: utf-8 -*-

"""
EnnoSmart V189
PDF reading order - WORD GUTTER FIRST.

Correction de V188 :
V188 pouvait reconstruire une ligne AVANT d'avoir s?par?
les deux colonnes.

V189 fait l'inverse :

    mots + coordonn?es
        ?
    d?tection g?om?trique du gutter
        ?
    s?paration gauche / droite
        ?
    reconstruction des lignes par colonne
        ?
    gauche puis droite

Le module :
- reste g?n?rique ;
- n'utilise aucun vocabulaire m?tier ;
- ne contient aucun profil projet ;
- n'utilise aucun LLM ;
- conserve un fallback strict vers page.extract_text().
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Mapping, Sequence


VERSION = (
    "pdf_reading_order_v192_"
    "body_band_recurring_whitespace_gutter"
)


@dataclass
class ReadingOrderResult:

    text: str
    source_text_raw: str

    layout_mode: str
    column_count: int
    confidence: float

    word_count: int
    rendered_word_count: int

    table_words_removed: int

    split_x: float | None = None


# ============================================================
# BASIC HELPERS
# ============================================================

def _f(
    value: Any,
    default: float = 0.0,
) -> float:

    try:
        return float(value)
    except Exception:
        return float(default)


def _normalise_words(
    words: Sequence[
        Mapping[str, Any]
    ],
) -> list[dict[str, Any]]:

    output = []

    for raw in words or []:

        text = str(
            raw.get("text")
            or ""
        ).strip()

        if not text:
            continue

        x0 = _f(
            raw.get("x0")
        )

        x1 = _f(
            raw.get("x1")
        )

        top = _f(
            raw.get("top")
        )

        bottom = _f(
            raw.get("bottom")
        )

        if x1 <= x0:
            continue

        if bottom <= top:
            bottom = top + 1.0

        output.append(
            {
                "text": text,

                "x0": x0,
                "x1": x1,

                "top": top,
                "bottom": bottom,
            }
        )

    return output


def _inside_bbox(
    word: Mapping[str, Any],
    bbox: Sequence[float],
) -> bool:

    if (
        not bbox
        or len(bbox) != 4
    ):
        return False

    cx = (
        _f(word.get("x0"))
        + _f(word.get("x1"))
    ) / 2.0

    cy = (
        _f(word.get("top"))
        + _f(word.get("bottom"))
    ) / 2.0

    x0, top, x1, bottom = [
        float(v)
        for v in bbox
    ]

    return (
        x0 <= cx <= x1
        and top <= cy <= bottom
    )


# ============================================================
# ROWS
# ============================================================

def _group_rows(
    words: Sequence[
        Mapping[str, Any]
    ],
) -> list[dict[str, Any]]:
    """
    Regroupe uniquement selon Y.

    IMPORTANT :
    cette fonction NE concat?ne PAS encore les colonnes.
    """

    if not words:
        return []

    ordered = sorted(
        words,
        key=lambda word: (
            _f(
                word.get("top")
            ),
            _f(
                word.get("x0")
            ),
        ),
    )

    rows = []

    for word in ordered:

        top = _f(
            word.get("top")
        )

        bottom = _f(
            word.get("bottom")
        )

        cy = (
            top + bottom
        ) / 2.0

        height = max(
            1.0,
            bottom - top,
        )

        best_row = None
        best_distance = None

        # Les derni?res lignes sont les seules plausibles.
        for row in reversed(
            rows[-4:]
        ):

            tolerance = max(
                2.1,
                0.42
                * max(
                    height,
                    row["mean_height"],
                ),
            )

            distance = abs(
                cy - row["cy"]
            )

            if distance <= tolerance:

                if (
                    best_distance is None
                    or distance
                    < best_distance
                ):
                    best_row = row
                    best_distance = distance

        if best_row is None:

            rows.append(
                {
                    "cy": cy,

                    "mean_height":
                        height,

                    "words": [
                        dict(word)
                    ],
                }
            )

            continue

        best_row["words"].append(
            dict(word)
        )

        n = len(
            best_row["words"]
        )

        best_row["cy"] = (
            best_row["cy"]
            * (n - 1)
            + cy
        ) / n

        best_row[
            "mean_height"
        ] = (
            best_row[
                "mean_height"
            ]
            * (n - 1)
            + height
        ) / n


    for row in rows:

        row["words"] = sorted(
            row["words"],
            key=lambda word:
                _f(
                    word.get("x0")
                ),
        )

    return sorted(
        rows,
        key=lambda row:
            row["cy"],
    )


def _make_line(
    words: Sequence[
        Mapping[str, Any]
    ],
) -> dict[str, Any] | None:

    if not words:
        return None

    ordered = sorted(
        words,
        key=lambda word:
            _f(
                word.get("x0")
            ),
    )

    text = " ".join(
        str(
            word.get("text")
            or ""
        ).strip()

        for word in ordered

        if str(
            word.get("text")
            or ""
        ).strip()
    ).strip()

    if not text:
        return None

    return {

        "text": text,

        "x0":
            min(
                _f(
                    word.get("x0")
                )
                for word in ordered
            ),

        "x1":
            max(
                _f(
                    word.get("x1")
                )
                for word in ordered
            ),

        "top":
            min(
                _f(
                    word.get("top")
                )
                for word in ordered
            ),

        "bottom":
            max(
                _f(
                    word.get("bottom")
                )
                for word in ordered
            ),

        "word_count":
            len(ordered),
    }


# ============================================================
# GUTTER DETECTION
# ============================================================

def _row_partition(
    row: Mapping[str, Any],
    split_x: float,
    gutter_half_width: float,
):

    left = []
    right = []
    bridge = []

    lower = (
        split_x
        - gutter_half_width
    )

    upper = (
        split_x
        + gutter_half_width
    )

    for word in (
        row.get("words")
        or []
    ):

        x0 = _f(
            word.get("x0")
        )

        x1 = _f(
            word.get("x1")
        )

        if x1 <= lower:

            left.append(
                word
            )

        elif x0 >= upper:

            right.append(
                word
            )

        else:

            bridge.append(
                word
            )

    return (
        left,
        right,
        bridge,
    )



def _evaluate_split(
    rows,
    split_x,
    page_width,
):
    """
    V190 - analyse hybride du gutter.

    Deux familles de preuves sont utilis?es :

    A. preuve locale :
       plusieurs baselines contiennent un bloc ? gauche
       ET un bloc ? droite avec un vide au centre ;

    B. preuve globale :
       ? l'?chelle de toute la page, le gutter reste
       faiblement occup?, avec des masses textuelles
       ?quilibr?es des deux c?t?s.

    B permet de d?tecter des pages contenant figures,
    ?quations, l?gendes ou paragraphes d?salign?s pour
    lesquelles la preuve locale V189 ?tait trop stricte.
    """

    width = float(
        page_width
    )

    gutter_half = max(
        8.0,
        width * 0.018,
    )

    lower = (
        split_x
        - gutter_half
    )

    upper = (
        split_x
        + gutter_half
    )


    # --------------------------------------------------------
    # GLOBAL : r?cup?rer les mots uniques de la page
    # --------------------------------------------------------

    all_words = []

    seen = set()

    for row in rows:

        for word in (
            row.get("words")
            or []
        ):

            key = (
                round(
                    _f(
                        word.get("x0")
                    ),
                    2,
                ),
                round(
                    _f(
                        word.get("x1")
                    ),
                    2,
                ),
                round(
                    _f(
                        word.get("top")
                    ),
                    2,
                ),
                str(
                    word.get("text")
                    or ""
                ),
            )

            if key in seen:
                continue

            seen.add(key)

            all_words.append(
                word
            )


    total_words = len(
        all_words
    )


    if total_words < 20:
        return None


    left_global = 0
    right_global = 0

    gutter_overlap_words = 0
    gutter_center_words = 0


    for word in all_words:

        x0 = _f(
            word.get("x0")
        )

        x1 = _f(
            word.get("x1")
        )

        center = (
            x0 + x1
        ) / 2.0


        if center < split_x:
            left_global += 1
        else:
            right_global += 1


        # BBox traversant la bande centrale.
        if (
            x1 >= lower
            and x0 <= upper
        ):
            gutter_overlap_words += 1


        # Centre r?ellement situ? dans le gutter.
        if (
            lower
            <= center
            <= upper
        ):
            gutter_center_words += 1


    if (
        left_global == 0
        or right_global == 0
    ):
        return None


    global_balance = (
        min(
            left_global,
            right_global,
        )
        / max(
            left_global,
            right_global,
        )
    )


    gutter_overlap_ratio = (
        gutter_overlap_words
        / total_words
    )


    gutter_center_ratio = (
        gutter_center_words
        / total_words
    )


    # --------------------------------------------------------
    # LOCAL : analyse des baselines
    # --------------------------------------------------------

    eligible_rows = 0

    both_side_rows = 0

    clean_paired_rows = 0

    gutter_rows = 0


    for row in rows:

        words = (
            row.get("words")
            or []
        )


        if len(words) < 3:
            continue


        eligible_rows += 1


        has_left = False
        has_right = False
        has_gutter = False


        for word in words:

            x0 = _f(
                word.get("x0")
            )

            x1 = _f(
                word.get("x1")
            )

            center = (
                x0 + x1
            ) / 2.0


            if center < lower:

                has_left = True

            elif center > upper:

                has_right = True


            if (
                x1 >= lower
                and x0 <= upper
            ):

                has_gutter = True


        if (
            has_left
            and has_right
        ):

            both_side_rows += 1


            if not has_gutter:

                clean_paired_rows += 1


        if has_gutter:

            gutter_rows += 1


    both_side_ratio = (
        both_side_rows
        / max(
            1,
            eligible_rows,
        )
    )


    clean_paired_ratio = (
        clean_paired_rows
        / max(
            1,
            eligible_rows,
        )
    )


    gutter_row_ratio = (
        gutter_rows
        / max(
            1,
            eligible_rows,
        )
    )


    # --------------------------------------------------------
    # CONFIANCE GLOBALE
    # --------------------------------------------------------

    # Une page ? deux colonnes doit avoir peu de mots
    # traversant le gutter.
    gutter_cleanliness = max(
        0.0,

        min(
            1.0,

            1.0
            - (
                gutter_overlap_ratio
                / 0.18
            ),
        ),
    )


    global_confidence = (

        0.38
        * gutter_cleanliness

        + 0.27
        * global_balance

        + 0.25
        * min(
            1.0,
            both_side_ratio / 0.65,
        )

        + 0.10
        * min(
            1.0,
            total_words / 500.0,
        )
    )


    # --------------------------------------------------------
    # CONFIANCE LOCALE
    # --------------------------------------------------------

    strict_confidence = (

        0.45
        * min(
            1.0,
            clean_paired_ratio / 0.30,
        )

        + 0.20
        * global_balance

        + 0.20
        * (
            1.0
            - min(
                1.0,
                gutter_row_ratio,
            )
        )

        + 0.15
        * min(
            1.0,
            clean_paired_rows / 10.0,
        )
    )


    # Score de s?lection du meilleur split.
    #
    # Ce score sert uniquement ? choisir le gutter parmi
    # les candidats. Ce n'est PAS une probabilit?.
    score = (

        0.40
        * global_confidence

        + 0.30
        * strict_confidence

        + 0.20
        * (
            1.0
            - gutter_overlap_ratio
        )

        + 0.10
        * global_balance
    )


    return {

        "split_x":
            float(split_x),

        "gutter":
            float(gutter_half),

        "total_words":
            total_words,

        "global_balance":
            global_balance,

        "gutter_overlap_ratio":
            gutter_overlap_ratio,

        "gutter_center_ratio":
            gutter_center_ratio,

        "eligible_rows":
            eligible_rows,

        "both_side_rows":
            both_side_rows,

        "both_side_ratio":
            both_side_ratio,

        "clean_paired_rows":
            clean_paired_rows,

        "clean_paired_ratio":
            clean_paired_ratio,

        "gutter_rows":
            gutter_rows,

        "gutter_row_ratio":
            gutter_row_ratio,

        "global_confidence":
            global_confidence,

        "strict_confidence":
            strict_confidence,

        "score":
            score,
    }



def _find_recurring_gutter(
    rows,
    page_width,
):
    """
    V191.

    Trouve le gutter ? partir des VRAIS grands espaces
    horizontaux observ?s entre deux mots adjacents.

    Un vrai gutter de document multi-colonnes appara?t
    approximativement au m?me X sur de nombreuses lignes.

    Contrairement ? V190, on ne choisit donc plus le split
    uniquement en fonction d'un score gauche/droite.

    Retour :
        {
            split_x,
            confidence,
            support_rows,
            support_ratio,
            median_gap,
            balance
        }

    ou None si aucun gutter g?om?triquement stable.
    """

    width = float(
        page_width
    )

    if width <= 0:
        return None


    central_min = (
        width * 0.35
    )

    central_max = (
        width * 0.65
    )


    minimum_gap = max(
        10.0,
        width * 0.018,
    )


    # --------------------------------------------------------
    # Collecter les grands gaps centraux
    # --------------------------------------------------------

    gap_samples = []

    meaningful_rows = 0

    all_words = []


    for row_index, row in enumerate(
        rows
    ):

        words = sorted(
            row.get("words")
            or [],
            key=lambda word:
                _f(
                    word.get("x0")
                ),
        )


        if len(words) < 2:
            continue


        meaningful_rows += 1

        all_words.extend(
            words
        )


        for left_word, right_word in zip(
            words,
            words[1:],
        ):

            left_end = _f(
                left_word.get("x1")
            )

            right_start = _f(
                right_word.get("x0")
            )


            gap = (
                right_start
                - left_end
            )


            if gap < minimum_gap:
                continue


            midpoint = (
                left_end
                + right_start
            ) / 2.0


            if not (
                central_min
                <= midpoint
                <= central_max
            ):
                continue


            gap_samples.append(
                {
                    "row":
                        row_index,

                    "left_end":
                        left_end,

                    "right_start":
                        right_start,

                    "midpoint":
                        midpoint,

                    "gap":
                        gap,
                }
            )


    if (
        meaningful_rows < 8
        or len(gap_samples) < 4
    ):
        return None


    # --------------------------------------------------------
    # Clustering des midpoints
    # --------------------------------------------------------

    tolerance = max(
        7.0,
        width * 0.016,
    )


    best = None


    for seed in gap_samples:

        seed_x = (
            seed["midpoint"]
        )


        members = [
            sample

            for sample in gap_samples

            if abs(
                sample["midpoint"]
                - seed_x
            ) <= tolerance
        ]


        if len(members) < 4:
            continue


        # Une ligne ne compte qu'une seule fois.
        by_row = {}

        for member in members:

            row_id = member[
                "row"
            ]


            existing = by_row.get(
                row_id
            )


            if (
                existing is None
                or member["gap"]
                > existing["gap"]
            ):

                by_row[
                    row_id
                ] = member


        selected = list(
            by_row.values()
        )


        support_rows = len(
            selected
        )


        if support_rows < 4:
            continue


        # Pond?ration : un grand vide est une preuve
        # plus forte qu'un simple espace inter-mots.
        weighted_sum = 0.0
        weight_total = 0.0


        for sample in selected:

            weight = min(
                60.0,
                max(
                    1.0,
                    sample["gap"],
                ),
            )


            weighted_sum += (
                sample["midpoint"]
                * weight
            )

            weight_total += weight


        if weight_total <= 0:
            continue


        split_x = (
            weighted_sum
            / weight_total
        )


        # M?diane des tailles de gap.
        gap_values = sorted(
            sample["gap"]
            for sample in selected
        )


        middle = len(
            gap_values
        ) // 2


        if len(gap_values) % 2:

            median_gap = (
                gap_values[
                    middle
                ]
            )

        else:

            median_gap = (
                gap_values[
                    middle - 1
                ]
                + gap_values[
                    middle
                ]
            ) / 2.0


        # ----------------------------------------------------
        # Balance globale autour du split propos?
        # ----------------------------------------------------

        left_count = 0
        right_count = 0

        crossing = 0


        small_guard = max(
            3.0,
            width * 0.006,
        )


        seen_words = set()


        for word in all_words:

            key = (
                round(
                    _f(
                        word.get("x0")
                    ),
                    2,
                ),
                round(
                    _f(
                        word.get("x1")
                    ),
                    2,
                ),
                round(
                    _f(
                        word.get("top")
                    ),
                    2,
                ),
                str(
                    word.get("text")
                    or ""
                ),
            )


            if key in seen_words:
                continue


            seen_words.add(
                key
            )


            x0 = _f(
                word.get("x0")
            )

            x1 = _f(
                word.get("x1")
            )

            center = (
                x0 + x1
            ) / 2.0


            if center < split_x:

                left_count += 1

            else:

                right_count += 1


            if (
                x0
                <= split_x + small_guard
                and x1
                >= split_x - small_guard
            ):

                crossing += 1


        if (
            left_count == 0
            or right_count == 0
        ):
            continue


        balance = (
            min(
                left_count,
                right_count,
            )
            / max(
                left_count,
                right_count,
            )
        )


        crossing_ratio = (
            crossing
            / max(
                1,
                len(
                    seen_words
                ),
            )
        )


        support_ratio = (
            support_rows
            / max(
                1,
                meaningful_rows,
            )
        )


        gap_strength = min(
            1.0,
            median_gap
            / max(
                18.0,
                width * 0.055,
            ),
        )


        confidence = (

            0.44
            * min(
                1.0,
                support_ratio / 0.32,
            )

            + 0.24
            * gap_strength

            + 0.22
            * balance

            + 0.10
            * (
                1.0
                - min(
                    1.0,
                    crossing_ratio / 0.08,
                )
            )
        )


        # La position pr?s du centre est l?g?rement
        # favoris?e, sans imposer 50/50.
        centrality = (
            1.0
            - min(
                1.0,
                abs(
                    split_x
                    - width * 0.5
                )
                / (
                    width * 0.18
                ),
            )
        )


        score = (
            0.80
            * confidence

            + 0.20
            * centrality
        )


        candidate = {
            "split_x":
                float(
                    split_x
                ),

            "confidence":
                float(
                    confidence
                ),

            "support_rows":
                support_rows,

            "support_ratio":
                float(
                    support_ratio
                ),

            "median_gap":
                float(
                    median_gap
                ),

            "balance":
                float(
                    balance
                ),

            "crossing_ratio":
                float(
                    crossing_ratio
                ),

            "score":
                float(
                    score
                ),
        }


        if (
            best is None
            or candidate["score"]
            > best["score"]
        ):

            best = candidate


    if best is None:
        return None


    # S?curit? :
    # le vide doit ?tre observ? sur plusieurs lignes
    # et produire deux masses textuelles cr?dibles.
    accepted = bool(

        best[
            "support_rows"
        ] >= 5

        and best[
            "support_ratio"
        ] >= 0.10

        and best[
            "median_gap"
        ] >= minimum_gap

        and best[
            "balance"
        ] >= 0.25

        and best[
            "confidence"
        ] >= 0.48
    )


    if not accepted:
        return None


    return best


def _detect_two_columns(
    rows,
    page_width,
):
    """
    V191.

    1. Cherche d'abord le gutter r?el r?current.
    2. V?rifie ensuite la g?om?trie globale avec
       _evaluate_split V190.
    3. Si aucun gutter r?current fiable n'existe,
       fallback vers la logique hybride V190.

    La d?tection du TYPE de page et la localisation du
    gutter deviennent donc deux probl?mes distincts.
    """

    width = float(
        page_width
    )


    if (
        width <= 0
        or len(rows) < 8
    ):

        return (
            False,
            None,
            0.0,
            None,
        )


    # ========================================================
    # A. GUTTER RECURRENT
    # ========================================================

    recurring = (
        _find_recurring_gutter(
            rows,
            width,
        )
    )


    if recurring is not None:

        split_x = float(
            recurring[
                "split_x"
            ]
        )


        metrics = (
            _evaluate_split(
                rows,
                split_x,
                width,
            )
        )


        if metrics is not None:

            # On conserve dans l'audit la preuve V191.
            metrics[
                "recurring_gutter"
            ] = recurring


            # Le gutter r?current est lui-m?me une preuve
            # forte de structure colonne.
            accepted = bool(

                recurring[
                    "support_rows"
                ] >= 5

                and recurring[
                    "balance"
                ] >= 0.25

                and (
                    metrics[
                        "global_confidence"
                    ] >= 0.50

                    or metrics[
                        "strict_confidence"
                    ] >= 0.42

                    or recurring[
                        "confidence"
                    ] >= 0.62
                )
            )


            if accepted:

                confidence = max(

                    recurring[
                        "confidence"
                    ],

                    metrics[
                        "global_confidence"
                    ],

                    metrics[
                        "strict_confidence"
                    ],
                )


                return (
                    True,

                    split_x,

                    round(
                        float(
                            confidence
                        ),
                        4,
                    ),

                    metrics,
                )


    # ========================================================
    # B. FALLBACK V190
    # ========================================================

    candidates = []


    for ratio in (
        0.40,
        0.42,
        0.44,
        0.46,
        0.48,
        0.50,
        0.52,
        0.54,
        0.56,
        0.58,
        0.60,
    ):

        metrics = (
            _evaluate_split(
                rows,
                width * ratio,
                width,
            )
        )


        if metrics is not None:

            candidates.append(
                metrics
            )


    if not candidates:

        return (
            False,
            None,
            0.0,
            None,
        )


    best = max(
        candidates,
        key=lambda item:
            item["score"],
    )


    strict_accept = bool(

        best[
            "clean_paired_rows"
        ] >= 5

        and best[
            "clean_paired_ratio"
        ] >= 0.14

        and best[
            "global_balance"
        ] >= 0.25

        and best[
            "strict_confidence"
        ] >= 0.48
    )


    # Sans gutter récurrent, une simple répartition équilibrée des mots à
    # gauche et à droite du centre ne prouve pas deux colonnes : c'est aussi le
    # comportement normal d'un paragraphe justifié. Le fallback global V190
    # réordonnait ainsi des pages monocolonnes. En fallback, seules plusieurs
    # lignes réellement séparées des deux côtés constituent une preuve.
    accepted = bool(strict_accept)


    confidence = max(
        best[
            "strict_confidence"
        ],
        best[
            "global_confidence"
        ],
    )


    return (
        accepted,

        float(
            best["split_x"]
        ),

        round(
            float(
                confidence
            ),
            4,
        ),

        best,
    )

# ============================================================
# TWO COLUMN RECONSTRUCTION
# ============================================================


def _central_gap(
    words,
    split_x,
):
    """
    Mesure le vide horizontal qui traverse le split.

    Une vraie ligne pleine largeur a g?n?ralement une
    continuit? typographique autour du centre.

    Une baseline contenant deux colonnes distinctes poss?de
    au contraire un gap beaucoup plus important.
    """

    ordered = sorted(
        words,
        key=lambda word:
            _f(
                word.get("x0")
            ),
    )


    if len(ordered) < 2:
        return None, None


    gaps = []

    central_gap = None


    for left, right in zip(
        ordered,
        ordered[1:],
    ):

        gap = max(
            0.0,

            _f(
                right.get("x0")
            )
            - _f(
                left.get("x1")
            ),
        )


        gaps.append(
            gap
        )


        left_center = (
            _f(
                left.get("x0")
            )
            + _f(
                left.get("x1")
            )
        ) / 2.0


        right_center = (
            _f(
                right.get("x0")
            )
            + _f(
                right.get("x1")
            )
        ) / 2.0


        if (
            left_center
            <= split_x
            <= right_center
        ):

            central_gap = gap


    positive = [
        gap
        for gap in gaps
        if gap > 0
    ]


    typical_gap = (
        median(
            positive
        )
        if positive
        else 3.0
    )


    return (
        central_gap,
        typical_gap,
    )


def _is_full_width_row(
    row,
    split_x,
    gutter,
    page_width,
):
    """
    Une ligne pleine largeur doit ?tre r?ellement CONTINUE
    autour du centre.

    Cela ?vite que :
        texte colonne gauche       texte colonne droite

    soit pris pour un titre pleine largeur.
    """

    words = (
        row.get("words")
        or []
    )


    if len(words) < 4:
        return False


    x0 = min(
        _f(
            word.get("x0")
        )
        for word in words
    )


    x1 = max(
        _f(
            word.get("x1")
        )
        for word in words
    )


    span_ratio = (
        (x1 - x0)
        / max(
            1.0,
            page_width,
        )
    )


    if span_ratio < 0.68:
        return False


    centers = [
        (
            _f(
                word.get("x0")
            )
            + _f(
                word.get("x1")
            )
        ) / 2.0

        for word in words
    ]


    has_left = any(
        center
        < split_x - gutter
        for center in centers
    )


    has_right = any(
        center
        > split_x + gutter
        for center in centers
    )


    if not (
        has_left
        and has_right
    ):

        return False


    (
        central_gap,
        typical_gap,
    ) = _central_gap(
        words,
        split_x,
    )


    if central_gap is None:
        return False


    # Titre continu :
    # le gap au centre doit ressembler ? l'espacement
    # typographique normal de la ligne.
    continuous_limit = max(
        8.0,
        typical_gap * 2.2,
    )


    return (
        central_gap
        <= continuous_limit
    )


def _resolve_ambiguous_words(
    words,
    split_x,
    gutter,
):
    """
    Affecte les mots du gutter ? la bonne colonne.

    Exemple page de bibliographie :
        "8 Literature"

    Si "8" tombe l?g?rement ? gauche du split mais
    "Literature" est clairement ? droite, les deux restent
    ensemble.

    Aucun contenu lexical n'est utilis? :
    uniquement les distances horizontales.
    """

    ordered = sorted(
        words,
        key=lambda word:
            _f(
                word.get("x0")
            ),
    )


    lower = (
        split_x - gutter
    )

    upper = (
        split_x + gutter
    )


    left = []
    right = []
    ambiguous = []


    for word in ordered:

        center = (
            _f(
                word.get("x0")
            )
            + _f(
                word.get("x1")
            )
        ) / 2.0


        if center < lower:

            left.append(
                word
            )

        elif center > upper:

            right.append(
                word
            )

        else:

            ambiguous.append(
                word
            )


    # Aucun doute.
    if not ambiguous:

        return (
            left,
            right,
        )


    # Si toute la ligne non ambigu? appartient d?j?
    # ? une seule colonne, les mots du gutter vont
    # avec elle.
    if (
        left
        and not right
    ):

        left.extend(
            ambiguous
        )

        return (
            sorted(
                left,
                key=lambda word:
                    _f(
                        word.get("x0")
                    ),
            ),
            [],
        )


    if (
        right
        and not left
    ):

        right.extend(
            ambiguous
        )

        return (
            [],
            sorted(
                right,
                key=lambda word:
                    _f(
                        word.get("x0")
                    ),
            ),
        )


    # Sinon on affecte chaque mot ambigu au voisin
    # horizontal le plus proche.
    for word in ambiguous:

        x0 = _f(
            word.get("x0")
        )

        x1 = _f(
            word.get("x1")
        )


        left_distance = float(
            "inf"
        )

        right_distance = float(
            "inf"
        )


        if left:

            left_distance = min(
                abs(
                    x0
                    - _f(
                        candidate.get("x1")
                    )
                )
                for candidate in left
            )


        if right:

            right_distance = min(
                abs(
                    _f(
                        candidate.get("x0")
                    )
                    - x1
                )
                for candidate in right
            )


        if (
            left_distance
            <= right_distance
        ):

            left.append(
                word
            )

        else:

            right.append(
                word
            )


    return (
        sorted(
            left,
            key=lambda word:
                _f(
                    word.get("x0")
                ),
        ),

        sorted(
            right,
            key=lambda word:
                _f(
                    word.get("x0")
                ),
        ),
    )


def _split_rows_into_columns(
    rows,
    split_x,
    gutter,
    page_width,
):

    left_lines = []

    right_lines = []

    full_lines = []


    for row in rows:

        words = (
            row.get("words")
            or []
        )


        if not words:
            continue


        if _is_full_width_row(
            row,
            split_x,
            gutter,
            page_width,
        ):

            line = (
                _make_line(
                    words
                )
            )


            if line:

                full_lines.append(
                    line
                )


            continue


        (
            left_words,
            right_words,
        ) = _resolve_ambiguous_words(
            words,
            split_x,
            gutter,
        )


        left_line = (
            _make_line(
                left_words
            )
        )


        right_line = (
            _make_line(
                right_words
            )
        )


        if left_line:

            left_lines.append(
                left_line
            )


        if right_line:

            right_lines.append(
                right_line
            )


    return (
        left_lines,
        right_lines,
        full_lines,
    )

# ============================================================
# RENDER
# ============================================================

def _render_lines(
    lines,
):

    if not lines:
        return ""


    ordered = sorted(
        lines,
        key=lambda line: (
            _f(
                line.get("top")
            ),
            _f(
                line.get("x0")
            ),
        ),
    )


    heights = [
        max(
            1.0,

            _f(
                line.get("bottom")
            )
            - _f(
                line.get("top")
            ),
        )
        for line in ordered
    ]


    typical_height = (
        median(heights)
        if heights
        else 10.0
    )


    paragraph_gap = max(
        5.0,
        typical_height * 0.70,
    )


    output = []

    previous_bottom = None


    for line in ordered:

        text = str(
            line.get("text")
            or ""
        ).strip()

        if not text:
            continue


        top = _f(
            line.get("top")
        )


        if (
            previous_bottom is not None
            and (
                top
                - previous_bottom
            )
            > paragraph_gap
        ):

            output.append("")


        output.append(
            text
        )


        previous_bottom = _f(
            line.get("bottom")
        )


    return "\n".join(
        output
    ).strip()


def _render_columns(
    left_lines,
    right_lines,
    full_lines,
):
    """
    Une ligne pleine largeur cr?e une fronti?re verticale.

    Dans chaque bande :

        colonne gauche compl?te
        puis colonne droite compl?te.
    """

    full_lines = sorted(
        full_lines,
        key=lambda line:
            _f(
                line.get("top")
            ),
    )


    output = []

    cursor = float(
        "-inf"
    )


    def emit_band(
        start_y,
        end_y,
    ):

        left = [
            line

            for line in left_lines

            if (
                _f(
                    line.get("top")
                )
                >= start_y

                and _f(
                    line.get("top")
                )
                < end_y
            )
        ]


        right = [
            line

            for line in right_lines

            if (
                _f(
                    line.get("top")
                )
                >= start_y

                and _f(
                    line.get("top")
                )
                < end_y
            )
        ]


        left_text = (
            _render_lines(
                left
            )
        )

        right_text = (
            _render_lines(
                right
            )
        )


        if left_text:

            output.append(
                left_text
            )


        if right_text:

            output.append(
                right_text
            )


    for full in full_lines:

        top = _f(
            full.get("top")
        )


        emit_band(
            cursor,
            top,
        )


        text = str(
            full.get("text")
            or ""
        ).strip()


        if text:

            output.append(
                text
            )


        cursor = max(
            cursor,
            _f(
                full.get("bottom")
            ),
        )


    emit_band(
        cursor,
        float("inf"),
    )


    return "\n\n".join(
        text.strip()

        for text in output

        if text
        and text.strip()
    ).strip()


# ============================================================
# PUBLIC API
# ============================================================

def extract_page_reading_order(
    page: Any,
    page_number: int,
    *,
    table_bboxes: Sequence[
        Sequence[float]
    ] | None = None,
) -> ReadingOrderResult:
    """
    Retourne :
    - texte natif si une structure 2 colonnes n'est
      pas suffisamment d?montr?e ;
    - texte reconstruit colonne gauche -> colonne droite
      sinon.
    """

    native_text = (
        page.extract_text(
            x_tolerance=2,
            y_tolerance=2,
        )
        or ""
    )


    try:

        raw_words = (
            page.extract_words(
                x_tolerance=2,
                y_tolerance=2,
                keep_blank_chars=False,
                use_text_flow=False,
            )
            or []
        )


    except TypeError:

        raw_words = (
            page.extract_words(
                x_tolerance=2,
                y_tolerance=2,
                keep_blank_chars=False,
            )
            or []
        )


    except Exception:

        raw_words = []


    words = _normalise_words(
        raw_words
    )

    table_bboxes = list(table_bboxes or [])
    filtered_words = [
        word
        for word in words
        if not any(_inside_bbox(word, bbox) for bbox in table_bboxes)
    ]
    removed = len(words) - len(filtered_words)
    if removed and not filtered_words:
        filtered_words = words
        removed = 0
    working_words = filtered_words if removed else words

    def render_single_column(source_words):
        lines = [
            line
            for line in (_make_line(row.get("words") or []) for row in _group_rows(source_words))
            if line
        ]
        return _render_lines(lines)


    width = float(
        getattr(
            page,
            "width",
            0.0,
        )
        or 0.0
    )


    if (
        len(working_words) < 8
        or width <= 0
    ):

        return ReadingOrderResult(

            text=(render_single_column(working_words) or native_text) if removed else native_text,

            source_text_raw=
                native_text,

            layout_mode=
                "native_safe_fallback",

            column_count=1,

            confidence=0.0,

            word_count=
                len(working_words),

            rendered_word_count=
                len(words),

            table_words_removed=removed,

            split_x=None,
        )


    # ========================================================
    # 1. DETECTER LE GUTTER SUR LA ZONE UTILE DE LA PAGE
    # ========================================================

    # Logos, titres courants et numéros de page créent souvent deux masses
    # latérales qui ressemblent artificiellement à des colonnes. La décision
    # de mise en page se prend donc sur la bande centrale du document. Cette
    # règle est purement géométrique et ne dépend d'aucun vocabulaire métier.
    height = float(
        getattr(
            page,
            "height",
            0.0,
        )
        or 0.0
    )

    detection_words = [
        word
        for word in working_words
        if (
            height <= 0
            or (
                _f(word.get("bottom")) >= height * 0.13
                and _f(word.get("top")) <= height * 0.86
            )
        )
    ]

    if len(detection_words) < 20:
        detection_words = working_words

    detection_rows = (
        _group_rows(
            detection_words
        )
    )


    (
        two_columns,
        split_x,
        confidence,
        metrics,
    ) = _detect_two_columns(
        detection_rows,
        width,
    )


    if (
        not two_columns
        or split_x is None
        or metrics is None
    ):

        return ReadingOrderResult(

            text=(render_single_column(working_words) or native_text) if removed else native_text,

            source_text_raw=
                native_text,

            layout_mode=
                "native_single_column",

            column_count=1,

            confidence=
                confidence,

            word_count=
                len(working_words),

            rendered_word_count=
                len(words),

            table_words_removed=removed,

            split_x=None,
        )


    # ========================================================
    # 3. REFAIRE LES ROWS MAIS SANS JAMAIS MELANGER
    #    GAUCHE ET DROITE
    # ========================================================

    rows = (
        _group_rows(
            filtered_words
        )
    )


    gutter = float(
        metrics[
            "gutter"
        ]
    )


    (
        left_lines,
        right_lines,
        full_lines,
    ) = _split_rows_into_columns(

        rows,

        split_x,

        gutter,

        width,
    )


    reordered = (
        _render_columns(
            left_lines,
            right_lines,
            full_lines,
        )
    )


    # ========================================================
    # 4. FILET DE SECURITE
    # ========================================================

    if len(
        reordered.strip()
    ) < 30:

        return ReadingOrderResult(

            text=(render_single_column(working_words) or native_text) if removed else native_text,

            source_text_raw=
                native_text,

            layout_mode=
                "native_safe_fallback",

            column_count=1,

            confidence=
                confidence,

            word_count=
                len(words),

            rendered_word_count=
                len(words),

            table_words_removed=removed,

            split_x=None,
        )


    return ReadingOrderResult(

        text=reordered,

        source_text_raw=
            native_text,

        layout_mode=
            "bbox_two_column_recurring_gutter",

        column_count=2,

        confidence=
            confidence,

        word_count=
            len(words),

        rendered_word_count=
            len(filtered_words),

        table_words_removed=
            removed,

        split_x=
            round(
                float(split_x),
                2,
            ),
    )
