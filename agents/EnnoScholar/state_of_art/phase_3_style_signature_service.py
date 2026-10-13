# -*- coding: utf-8 -*-
from __future__ import annotations

"""Signature stylistique générique utilisée par le rédacteur d'état de l'art.

Cette étape n'extrait aucune preuve scientifique. Elle transforme uniquement
les exemples CIR de la Phase 3 en préférences de rédaction traçables et
indépendantes du domaine du projet.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def _read_json(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    if not source.is_file():
        return {}
    try:
        value = json.loads(source.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: str | Path, value: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _iter_text(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        text = " ".join(value.replace("\x00", " ").split()).strip()
        if len(text) >= 40:
            yield text
    elif isinstance(value, dict):
        for child in value.values():
            yield from _iter_text(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _iter_text(child)


def _sentence_lengths(texts: list[str]) -> list[int]:
    values: list[int] = []
    for text in texts:
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            words = re.findall(r"\b[\wÀ-ÿ'-]+\b", sentence)
            if words:
                values.append(len(words))
    return values


def run_phase_3_style_signature(
    *,
    fewshot_payload_path: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    fewshot = _read_json(fewshot_payload_path)
    texts = list(_iter_text(fewshot))[:400]
    lengths = _sentence_lengths(texts)
    average_sentence_words = (
        round(sum(lengths) / len(lengths), 2) if lengths else 22.0
    )
    transition_markers = (
        "cependant",
        "néanmoins",
        "ainsi",
        "en revanche",
        "par conséquent",
        "dans ce contexte",
        "en particulier",
    )
    joined = " ".join(texts).casefold()
    observed_transitions = [
        marker for marker in transition_markers if marker in joined
    ]
    payload = {
        "ok": True,
        "phase": "phase_3_style_signature",
        "payload_type": "generic_cir_style_signature_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_path": str(Path(fewshot_payload_path)),
        "style_only_not_evidence": True,
        "signature": {
            "language": "fr",
            "average_sentence_words": average_sentence_words,
            "preferred_transitions": observed_transitions or list(transition_markers),
            "paragraph_policy": {
                "minimum_sentences": 3,
                "preferred_sentences": 5,
                "claim_then_evidence_then_limit": True,
            },
            "rhetorical_moves": [
                "définir précisément l'objet étudié lorsque cela est nécessaire",
                "expliquer le mécanisme ou la procédure avant de présenter les résultats",
                "comparer les approches sur des critères explicités",
                "relier les limites observées à l'incertitude du projet",
            ],
        },
        "quality": {
            "source_texts_count": len(texts),
            "sentences_count": len(lengths),
            "domain_terms_copied": False,
        },
        "rules": {
            "no_project_name_hardcoding": True,
            "no_domain_hardcoding": True,
            "never_citable": True,
        },
        "output_path": str(Path(output_path)),
    }
    _write_json(output_path, payload)
    return payload


__all__ = ["run_phase_3_style_signature"]
