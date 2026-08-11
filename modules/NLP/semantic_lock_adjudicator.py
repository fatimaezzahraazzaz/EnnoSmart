
# -*- coding: utf-8 -*-
"""
EnnoSmart V189
Semantic Lock Adjudicator

Objectif
--------
Comparer deux groupes de verrous d?j? issus du pipeline NLP
et d?terminer s'ils expriment potentiellement la m?me
incertitude scientifique/technique.

Ce module NE :
- cr?e aucun verrou ;
- ne supprime aucune preuve ;
- ne modifie pas FastJudge ;
- ne remplace pas Frascati ;
- ne fixe aucun nombre cible de verrous.

Architecture :
    project_lock_seed
        -> groupes NLP
        -> cosine similarity
        -> NLI bidirectionnel
        -> relation s?mantique candidate

Mod?le :
MoritzLaurer/multilingual-MiniLMv2-L6-mnli-xnli
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import math
import os

import torch
from transformers import (
    AutoModelForSequenceClassification,
    AutoTokenizer,
)


VERSION = (
    "semantic_lock_adjudicator_v189_"
    "local_multilingual_minilm_nli_bidirectional"
)

DEFAULT_MODEL_NAME = (
    "MoritzLaurer/"
    "multilingual-MiniLMv2-L6-mnli-xnli"
)


def _resolve_local_snapshot(
    model_name: str,
    cache_dir: str,
) -> Optional[Path]:
    """Résout un snapshot Hugging Face local sans aucun appel réseau."""

    direct = Path(model_name)
    if direct.is_dir():
        return direct

    model_cache = Path(cache_dir) / (
        "models--" + str(model_name).replace("/", "--")
    )
    revision = ""
    ref = model_cache / "refs" / "main"
    if ref.is_file():
        try:
            revision = ref.read_text(encoding="utf-8").strip()
        except Exception:
            revision = ""

    candidates: List[Path] = []
    if revision:
        candidates.append(model_cache / "snapshots" / revision)
    snapshots = model_cache / "snapshots"
    if snapshots.is_dir():
        candidates.extend(
            path
            for path in snapshots.iterdir()
            if path.is_dir()
        )

    required = ("config.json", "tokenizer_config.json")
    return next(
        (
            path
            for path in candidates
            if all((path / name).is_file() for name in required)
            and (
                (path / "model.safetensors").is_file()
                or (path / "pytorch_model.bin").is_file()
            )
        ),
        None,
    )


# ============================================================
# PROFIL D'UN GROUPE
# ============================================================

def _clean(value: Any) -> str:
    return " ".join(
        str(value or "").split()
    ).strip()


def _quality(item: Mapping[str, Any]) -> float:
    """
    Priorit? aux vrais project_lock_seed et aux passages
    ayant le meilleur score FastJudge.

    Ce score sert uniquement ? choisir les passages
    repr?sentatifs. Ce n'est pas une probabilit? CIR.
    """

    score = 0.0

    if item.get("project_lock_seed"):
        score += 10.0

    if item.get("lock_candidate_explicit"):
        score += 2.0

    try:
        score += float(
            item.get("lock_candidate_score")
            or item.get("verrou_score")
            or 0.0
        )
    except Exception:
        pass

    return score


def build_group_profile(
    group: Mapping[str, Any],
    *,
    max_passages: int = 3,
    max_chars_per_passage: int = 650,
) -> str:
    """
    Construit un profil court du verrou.

    On pr?f?re les passages project_lock_seed plut?t
    que le champ `group["text"]`, qui peut ?tre une
    repr?sentation partiellement bruit?e.
    """

    supporting = [
        item
        for item in (
            group.get("supporting_passages")
            or []
        )
        if isinstance(item, Mapping)
    ]

    project_seeds = [
        item
        for item in supporting
        if item.get("project_lock_seed")
    ]

    pool = (
        project_seeds
        if project_seeds
        else supporting
    )

    pool = sorted(
        pool,
        key=_quality,
        reverse=True,
    )

    parts: List[str] = []

    for item in pool[:max_passages]:

        section = _clean(
            item.get("section_title")
        )

        before = _clean(
            item.get("context_before")
        )

        passage = _clean(
            item.get("text")
        )

        after = _clean(
            item.get("context_after")
        )

        # Le passage lui-m?me reste prioritaire.
        local = " ".join(
            x
            for x in (
                before,
                passage,
                after,
            )
            if x
        )

        local = local[
            :max_chars_per_passage
        ]

        if section:
            local = (
                f"Section: {section}. "
                + local
            )

        if local:
            parts.append(local)

    if not parts:

        fallback = _clean(
            group.get("text")
            or group.get("analysis_text")
        )

        if fallback:
            parts.append(
                fallback[:1500]
            )

    return "\n".join(parts)


# ============================================================
# COSINE
# ============================================================

def cosine_similarity(
    left: Sequence[float],
    right: Sequence[float],
) -> float:

    if not left or not right:
        return 0.0

    if len(left) != len(right):
        return 0.0

    dot = sum(
        float(a) * float(b)
        for a, b in zip(
            left,
            right,
        )
    )

    norm_left = math.sqrt(
        sum(
            float(x) * float(x)
            for x in left
        )
    )

    norm_right = math.sqrt(
        sum(
            float(x) * float(x)
            for x in right
        )
    )

    if (
        norm_left <= 0.0
        or norm_right <= 0.0
    ):
        return 0.0

    return float(
        dot
        / (
            norm_left
            * norm_right
        )
    )


# ============================================================
# RESULTAT
# ============================================================

@dataclass
class NLIResult:
    entailment: float
    neutral: float
    contradiction: float


@dataclass
class LockPairResult:
    cosine: float

    entailment_ab: float
    entailment_ba: float

    neutral_ab: float
    neutral_ba: float

    contradiction_ab: float
    contradiction_ba: float

    symmetric_entailment: float
    maximum_entailment: float
    maximum_contradiction: float

    relation: str
    decision_score: float

    def as_dict(self) -> Dict[str, Any]:
        return {
            "cosine":
                round(self.cosine, 4),

            "entailment_ab":
                round(self.entailment_ab, 4),

            "entailment_ba":
                round(self.entailment_ba, 4),

            "neutral_ab":
                round(self.neutral_ab, 4),

            "neutral_ba":
                round(self.neutral_ba, 4),

            "contradiction_ab":
                round(
                    self.contradiction_ab,
                    4,
                ),

            "contradiction_ba":
                round(
                    self.contradiction_ba,
                    4,
                ),

            "symmetric_entailment":
                round(
                    self.symmetric_entailment,
                    4,
                ),

            "maximum_entailment":
                round(
                    self.maximum_entailment,
                    4,
                ),

            "maximum_contradiction":
                round(
                    self.maximum_contradiction,
                    4,
                ),

            "relation":
                self.relation,

            "decision_score":
                round(
                    self.decision_score,
                    4,
                ),
        }


# ============================================================
# MODELE
# ============================================================

class SemanticLockAdjudicator:

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL_NAME,
        cache_dir: Optional[str] = None,
        max_length: int = 384,
    ):

        self.model_name = model_name
        self.max_length = int(
            max_length
        )

        if cache_dir is None:

            repo_root = (
                Path(__file__)
                .resolve()
                .parents[2]
            )

            cache_dir = str(
                repo_root
                / "models"
                / "huggingface"
            )

        self.cache_dir = cache_dir

        local_snapshot = _resolve_local_snapshot(
            self.model_name,
            self.cache_dir,
        )
        allow_download = str(
            os.getenv("ENNOSMART_NLI_ALLOW_DOWNLOAD", "0")
        ).strip().lower() in {"1", "true", "yes", "on"}
        if local_snapshot is None and not allow_download:
            raise FileNotFoundError(
                "Snapshot NLI local introuvable. Placez le modèle dans "
                f"{self.cache_dir!s} ou activez explicitement "
                "ENNOSMART_NLI_ALLOW_DOWNLOAD=1."
            )
        self.model_source = str(local_snapshot or self.model_name)
        self.local_files_only = bool(local_snapshot is not None or not allow_download)

        self.device = torch.device(
            "cuda"
            if torch.cuda.is_available()
            else "cpu"
        )

        self.tokenizer = (
            AutoTokenizer.from_pretrained(
                self.model_source,
                cache_dir=self.cache_dir,
                local_files_only=self.local_files_only,
            )
        )

        self.model = (
            AutoModelForSequenceClassification
            .from_pretrained(
                self.model_source,
                cache_dir=self.cache_dir,
                local_files_only=self.local_files_only,
            )
        )

        self.model.to(
            self.device
        )

        self.model.eval()

        # Ne jamais supposer l'ordre des classes.
        label2id = {
            str(key).lower():
                int(value)
            for key, value
            in self.model.config.label2id.items()
        }

        self.entailment_id = label2id[
            "entailment"
        ]

        self.neutral_id = label2id[
            "neutral"
        ]

        self.contradiction_id = label2id[
            "contradiction"
        ]


    @torch.inference_mode()
    def _predict_direction(
        self,
        premises: Sequence[str],
        hypotheses: Sequence[str],
        *,
        batch_size: int = 16,
    ) -> List[NLIResult]:

        if len(premises) != len(hypotheses):
            raise ValueError(
                "premises/hypotheses mismatch"
            )

        results: List[NLIResult] = []

        for start in range(
            0,
            len(premises),
            batch_size,
        ):

            batch_premises = list(
                premises[
                    start:
                    start + batch_size
                ]
            )

            batch_hypotheses = list(
                hypotheses[
                    start:
                    start + batch_size
                ]
            )

            encoded = self.tokenizer(
                batch_premises,
                batch_hypotheses,
                padding=True,
                truncation=True,
                max_length=self.max_length,
                return_tensors="pt",
            )

            encoded = {
                key:
                    value.to(
                        self.device
                    )
                for key, value
                in encoded.items()
            }

            output = self.model(
                **encoded
            )

            probabilities = (
                torch.softmax(
                    output.logits,
                    dim=-1,
                )
                .detach()
                .cpu()
            )

            for probs in probabilities:

                results.append(
                    NLIResult(
                        entailment=float(
                            probs[
                                self.entailment_id
                            ]
                        ),

                        neutral=float(
                            probs[
                                self.neutral_id
                            ]
                        ),

                        contradiction=float(
                            probs[
                                self.contradiction_id
                            ]
                        ),
                    )
                )

        return results


    def compare_many(
        self,
        profiles: Sequence[str],
        vectors: Optional[
            Sequence[
                Sequence[float]
            ]
        ] = None,
        *,
        batch_size: int = 16,
    ) -> List[Dict[str, Any]]:
        """
        Compare toutes les paires de groupes.

        IMPORTANT :
        `relation` est encore une proposition de V186.
        Elle ne doit pas fusionner automatiquement les
        verrous avant validation sur plusieurs dossiers.
        """

        pair_indexes: List[
            Tuple[int, int]
        ] = []

        premises_ab: List[str] = []
        hypotheses_ab: List[str] = []

        premises_ba: List[str] = []
        hypotheses_ba: List[str] = []

        for left in range(
            len(profiles)
        ):

            for right in range(
                left + 1,
                len(profiles),
            ):

                pair_indexes.append(
                    (
                        left,
                        right,
                    )
                )

                premises_ab.append(
                    profiles[left]
                )

                hypotheses_ab.append(
                    profiles[right]
                )

                premises_ba.append(
                    profiles[right]
                )

                hypotheses_ba.append(
                    profiles[left]
                )

        forward = self._predict_direction(
            premises_ab,
            hypotheses_ab,
            batch_size=batch_size,
        )

        backward = self._predict_direction(
            premises_ba,
            hypotheses_ba,
            batch_size=batch_size,
        )

        results: List[
            Dict[str, Any]
        ] = []

        for pair_position, (
            left,
            right,
        ) in enumerate(
            pair_indexes
        ):

            ab = forward[
                pair_position
            ]

            ba = backward[
                pair_position
            ]

            if vectors is not None:

                cosine = cosine_similarity(
                    vectors[left],
                    vectors[right],
                )

            else:

                cosine = 0.0

            symmetric_entailment = min(
                ab.entailment,
                ba.entailment,
            )

            maximum_entailment = max(
                ab.entailment,
                ba.entailment,
            )

            maximum_contradiction = max(
                ab.contradiction,
                ba.contradiction,
            )


            # =================================================
            # PROPOSITION GENERIQUE
            #
            # Ces seuils ne sont PAS calibr?s sur AI-RADAR.
            # Ils servent uniquement au diagnostic V186.
            #
            # Ils devront ?tre valid?s sur plusieurs dossiers
            # avant utilisation automatique.
            # =================================================

            if (
                cosine >= 0.50
                and symmetric_entailment >= 0.45
                and maximum_contradiction <= 0.35
            ):

                relation = (
                    "SAME_CANDIDATE"
                )

            elif (
                cosine >= 0.42
                and maximum_entailment >= 0.40
                and maximum_contradiction <= 0.55
            ):

                relation = (
                    "RELATED_CANDIDATE"
                )

            else:

                relation = (
                    "DISTINCT_CANDIDATE"
                )


            # Score de classement uniquement.
            # Ce n'est pas une probabilit?.
            decision_score = (
                0.45
                * cosine
                +
                0.55
                * symmetric_entailment
            )


            result = LockPairResult(
                cosine=cosine,

                entailment_ab=
                    ab.entailment,

                entailment_ba=
                    ba.entailment,

                neutral_ab=
                    ab.neutral,

                neutral_ba=
                    ba.neutral,

                contradiction_ab=
                    ab.contradiction,

                contradiction_ba=
                    ba.contradiction,

                symmetric_entailment=
                    symmetric_entailment,

                maximum_entailment=
                    maximum_entailment,

                maximum_contradiction=
                    maximum_contradiction,

                relation=
                    relation,

                decision_score=
                    decision_score,
            )

            row = result.as_dict()

            row[
                "left_index"
            ] = left

            row[
                "right_index"
            ] = right

            results.append(
                row
            )

        results.sort(
            key=lambda item:
                item[
                    "decision_score"
                ],
            reverse=True,
        )

        return results
