# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
import unicodedata
from collections import defaultdict
from typing import Any, Mapping, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator


_CITATION_RE = re.compile(r"\[(A\s*\d+)\]", flags=re.I)
_NUMBER_RE = re.compile(
    r"(?<![A-Za-z])[-+]?\d+(?:[.,]\d+)?(?:\s*%|\s*dB|\s*dBsm|\s*GHz|\s*°)?",
    flags=re.I,
)
_STRONG_CLAIM_RE = re.compile(
    r"\b(d[ée]montre(?:nt)?|prouve(?:nt)?|garantit|garantissent|"
    r"significativement|syst[ée]matiquement|toujours|jamais|"
    r"aucune\s+m[ée]thode|aucune\s+solution|unanimement|"
    r"n[ée]cessairement|certainement|sans\s+ambigu[ïi]t[ée])\b",
    flags=re.I,
)
_ARGUMENTATIVE_FIGURE_RE = re.compile(
    r"\b(performance|accuracy|precision|recall|roc|confusion|matrix|"
    r"comparison|comparaison|result|résultat|validation|evaluation|"
    r"ssim|ser|rcs|benchmark|error|erreur|curve|courbe|score|"
    r"ablation|distribution|measured|synthetic|synthétique|réel|real)\b",
    flags=re.I,
)
_GENERIC_FIGURE_RE = re.compile(
    r"\b(interface|selection window|typical|example|illustration originale)\b",
    flags=re.I,
)

_TERMINOLOGY = [
    (re.compile(r"\bdomain adaptation\b", re.I), "adaptation de domaine"),
    (re.compile(r"\bfine[- ]tuning\b", re.I), "ajustement fin"),
    (re.compile(r"\boverfitting\b", re.I), "surapprentissage"),
    (re.compile(r"\bray launching\b", re.I), "lancer de rayons"),
    (re.compile(r"\bray tracing\b", re.I), "traçage de rayons"),
    (re.compile(r"\bmulti[- ]scattering\b", re.I), "diffusion multiple"),
    (re.compile(r"\bmultipath\b", re.I), "multi-trajets"),
    (re.compile(r"\bdata[- ]driven\b", re.I), "pilotée par les données"),
    (re.compile(r"\bframeworks?\b", re.I), "cadre méthodologique"),
    (re.compile(r"\baccuracy\b", re.I), "exactitude"),
]
_ALLOWED_TECHNICAL_ENGLISH = {
    "sar", "atr", "rcs", "mstar", "gan", "fgsm", "gpu", "nvidia", "optix",
    "predics", "mocem", "salsa", "sim-to-real", "go", "po", "ssim", "roc",
}


class EditorialConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exact_plan: bool = True
    remove_extra_sections: bool = True
    lexical_duplicate_threshold: float = Field(default=94.0, ge=0, le=100)
    semantic_duplicate_threshold: float = Field(default=0.94, ge=0, le=1)
    article_figure_min_score: float = Field(default=0.78, ge=0, le=3)
    project_figure_min_score: float = Field(default=0.90, ge=0, le=3)
    max_figures_per_section: int = Field(default=1, ge=0, le=5)
    nli_enabled: bool = True
    language_tool_enabled: bool = False


class EditorialContract(BaseModel):
    model_config = ConfigDict(extra="allow")

    allowed_section_ids: set[str] = Field(default_factory=set)
    allowed_section_titles: set[str] = Field(default_factory=set)
    allowed_citations: set[str] = Field(default_factory=set)

    @model_validator(mode="after")
    def validate_non_empty_contract(self) -> "EditorialContract":
        if not self.allowed_section_ids and not self.allowed_section_titles:
            raise ValueError("Plan consultant vide : validation éditoriale impossible.")
        return self


def _clean(value: Any, limit: int = 200000) -> str:
    text = str(value or "").replace("\x00", " ")
    text = text.replace("\\n\\n", "\n\n")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:limit]


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", _clean(value, 1000).casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _citation_label(value: Any) -> str:
    match = re.search(r"\bA\s*(\d+)\b", str(value or ""), flags=re.I)
    return f"A{int(match.group(1))}" if match else ""


def citations_from_text(value: Any) -> set[str]:
    return {
        _citation_label(match.group(0))
        for match in _CITATION_RE.finditer(str(value or ""))
        if _citation_label(match.group(0))
    }


def _approved_plan(contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    for key in ("approved_plan", "edited_plan", "plan", "sections"):
        value = contract.get(key)
        if isinstance(value, list) and value:
            return [dict(row) for row in value if isinstance(row, Mapping)]
    return []


def build_editorial_contract(
    plan_contract: Mapping[str, Any],
    cards: Sequence[Mapping[str, Any]],
) -> EditorialContract:
    plan = _approved_plan(plan_contract)
    ids = {
        _clean(row.get("section_id"), 200)
        for row in plan
        if _clean(row.get("section_id"), 200)
    }
    titles = {
        _norm(row.get("title"))
        for row in plan
        if _norm(row.get("title"))
    }
    citations = {
        _citation_label(row.get("citation_label") or row.get("citation_id"))
        for row in cards
    }
    citations.discard("")
    return EditorialContract(
        allowed_section_ids=ids,
        allowed_section_titles=titles,
        allowed_citations=citations,
    )


def _section_allowed(section: Mapping[str, Any], contract: EditorialContract) -> bool:
    section_id = _clean(section.get("section_id"), 200)
    title = _norm(section.get("title"))
    return (
        bool(section_id and section_id in contract.allowed_section_ids)
        or bool(title and title in contract.allowed_section_titles)
    )


def enforce_exact_consultant_plan(
    draft: Mapping[str, Any],
    contract: EditorialContract,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    output = dict(draft)
    kept = []
    removed = []
    for index, section in enumerate(draft.get("sections") or [], 1):
        if not isinstance(section, Mapping):
            continue
        if _section_allowed(section, contract):
            kept.append(dict(section))
        else:
            removed.append(
                {
                    "index": index,
                    "section_id": section.get("section_id"),
                    "title": section.get("title"),
                    "reason": "section_absente_du_plan_consultant",
                }
            )
    output["sections"] = kept
    return output, removed


def validate_citation_scope(
    draft: Mapping[str, Any],
    contract: EditorialContract,
) -> list[dict[str, Any]]:
    issues = []
    for section in draft.get("sections") or []:
        if not isinstance(section, Mapping):
            continue
        bodies = [
            ("section", _clean(section.get("content"))),
            *[
                (f"subsection:{idx}", _clean(row.get("content")))
                for idx, row in enumerate(section.get("subsections") or [])
                if isinstance(row, Mapping)
            ],
        ]
        for scope, body in bodies:
            unknown = sorted(citations_from_text(body) - contract.allowed_citations)
            if unknown:
                issues.append(
                    {
                        "section_id": section.get("section_id"),
                        "scope": scope,
                        "unknown_or_out_of_scope_citations": unknown,
                        "blocking": True,
                    }
                )
    return issues


def _paragraphs(value: Any) -> list[str]:
    text = _clean(value)
    if not text:
        return []
    items = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    return items or [text]


def _rapidfuzz_ratio(left: str, right: str) -> float:
    try:
        from rapidfuzz import fuzz
        return float(fuzz.token_set_ratio(left, right))
    except Exception:
        return 0.0


def _semantic_matrix(texts: Sequence[str]) -> Optional[list[list[float]]]:
    if len(texts) < 2:
        return None
    try:
        from modules.RAG.vector_store import encode_texts

        vectors = encode_texts(list(texts))
        matrix: list[list[float]] = []
        for left in vectors:
            row = []
            for right in vectors:
                score = sum(float(a) * float(b) for a, b in zip(left, right))
                row.append(max(-1.0, min(1.0, score)))
            matrix.append(row)
        return matrix
    except Exception:
        return None


def _paragraph_strength(text: str) -> tuple[int, int]:
    return (
        len(citations_from_text(text)),
        len(re.findall(r"\b[\wÀ-ÿ'-]+\b", text)),
    )


def remove_conservative_repetitions(
    draft: Mapping[str, Any],
    *,
    lexical_threshold: float,
    semantic_threshold: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    output = dict(draft)
    sections = [
        dict(row)
        for row in draft.get("sections") or []
        if isinstance(row, Mapping)
    ]

    refs: list[tuple[int, str, Optional[int], int, str]] = []
    texts: list[str] = []

    for section_index, section in enumerate(sections):
        for paragraph_index, paragraph in enumerate(_paragraphs(section.get("content"))):
            refs.append((section_index, "section", None, paragraph_index, paragraph))
            texts.append(paragraph)
        for subsection_index, subsection in enumerate(section.get("subsections") or []):
            if not isinstance(subsection, Mapping):
                continue
            for paragraph_index, paragraph in enumerate(_paragraphs(subsection.get("content"))):
                refs.append((section_index, "subsection", subsection_index, paragraph_index, paragraph))
                texts.append(paragraph)

    semantic = _semantic_matrix(texts)
    drop: set[int] = set()
    reports: list[dict[str, Any]] = []

    for i in range(len(texts)):
        if i in drop:
            continue
        for j in range(i + 1, len(texts)):
            if j in drop:
                continue
            left, right = texts[i], texts[j]
            if len(left.split()) < 35 or len(right.split()) < 35:
                continue
            left_cit = citations_from_text(left)
            right_cit = citations_from_text(right)
            citation_compatible = (
                left_cit == right_cit
                or left_cit.issuperset(right_cit)
                or right_cit.issuperset(left_cit)
            )
            if not citation_compatible:
                continue
            lexical = _rapidfuzz_ratio(left, right)
            semantic_score = semantic[i][j] if semantic is not None else 0.0
            if not (
                lexical >= lexical_threshold
                or semantic_score >= semantic_threshold
            ):
                continue
            keep_i = _paragraph_strength(left) >= _paragraph_strength(right)
            removed_index = j if keep_i else i
            kept_index = i if keep_i else j
            drop.add(removed_index)
            reports.append(
                {
                    "kept": refs[kept_index][:4],
                    "removed": refs[removed_index][:4],
                    "lexical_similarity": round(lexical, 2),
                    "semantic_similarity": round(semantic_score, 4),
                    "reason": "high_confidence_repetition",
                }
            )
            if removed_index == i:
                break

    kept_by_container: dict[tuple[int, str, Optional[int]], list[str]] = defaultdict(list)
    for idx, ref in enumerate(refs):
        if idx in drop:
            continue
        section_index, scope, subsection_index, _, paragraph = ref
        kept_by_container[(section_index, scope, subsection_index)].append(paragraph)

    for section_index, section in enumerate(sections):
        section["content"] = "\n\n".join(
            kept_by_container.get((section_index, "section", None), [])
        ).strip()
        subs = []
        for subsection_index, subsection in enumerate(section.get("subsections") or []):
            if not isinstance(subsection, Mapping):
                continue
            row = dict(subsection)
            row["content"] = "\n\n".join(
                kept_by_container.get(
                    (section_index, "subsection", subsection_index),
                    [],
                )
            ).strip()
            subs.append(row)
        section["subsections"] = subs

    output["sections"] = sections
    return output, reports


def normalize_french_terminology(text: str) -> str:
    output = _clean(text)
    for pattern, replacement in _TERMINOLOGY:
        output = pattern.sub(replacement, output)
    return output


def _language_detector() -> Any:
    try:
        from lingua import Language, LanguageDetectorBuilder
        return LanguageDetectorBuilder.from_languages(
            Language.FRENCH,
            Language.ENGLISH,
        ).build()
    except Exception:
        return None


def clean_and_detect_english(
    draft: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    output = dict(draft)
    detector = _language_detector()
    issues = []
    sections = []

    for section in draft.get("sections") or []:
        if not isinstance(section, Mapping):
            continue
        row = dict(section)
        row["content"] = normalize_french_terminology(row.get("content") or "")
        subs = []
        for subsection in row.get("subsections") or []:
            if not isinstance(subsection, Mapping):
                continue
            sub = dict(subsection)
            sub["content"] = normalize_french_terminology(sub.get("content") or "")
            subs.append(sub)
        row["subsections"] = subs
        sections.append(row)

        for scope, body in [
            ("section", row["content"]),
            *[
                (f"subsection:{idx}", sub.get("content") or "")
                for idx, sub in enumerate(subs)
            ],
        ]:
            for paragraph in _paragraphs(body):
                words = re.findall(r"[A-Za-z][A-Za-z'-]+", paragraph)
                if len(words) < 8 or detector is None:
                    continue
                stripped = " ".join(
                    word
                    for word in words
                    if word.casefold() not in _ALLOWED_TECHNICAL_ENGLISH
                )
                if len(stripped.split()) < 8:
                    continue
                try:
                    lang = detector.detect_language_of(stripped)
                    name = getattr(lang, "name", str(lang or ""))
                except Exception:
                    name = ""
                if str(name).upper() == "ENGLISH":
                    issues.append(
                        {
                            "section_id": row.get("section_id"),
                            "scope": scope,
                            "excerpt": paragraph[:600],
                            "blocking": False,
                            "reason": "residual_english_sentence",
                        }
                    )

    output["sections"] = sections
    return output, issues


def _evidence_by_citation(evidence_units: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for unit in evidence_units:
        citation = _citation_label(unit.get("citation_label"))
        text = _clean(
            unit.get("text")
            or unit.get("evidence")
            or unit.get("passage")
            or unit.get("content"),
            8000,
        )
        if citation and text:
            grouped[citation].append(text)
    return {
        key: "\n".join(values[:12])
        for key, values in grouped.items()
    }


def verify_numeric_claims(
    draft: Mapping[str, Any],
    evidence_units: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    evidence = _evidence_by_citation(evidence_units)
    issues = []

    for section in draft.get("sections") or []:
        if not isinstance(section, Mapping):
            continue
        bodies = [
            _clean(section.get("content")),
            *[
                _clean(row.get("content"))
                for row in section.get("subsections") or []
                if isinstance(row, Mapping)
            ],
        ]
        for body in bodies:
            for sentence in re.split(r"(?<=[.!?])\s+", body):
                numbers = [m.group(0).strip() for m in _NUMBER_RE.finditer(sentence)]
                if not numbers:
                    continue
                citations = citations_from_text(sentence)
                if not citations:
                    continue
                source_text = "\n".join(
                    evidence.get(citation, "")
                    for citation in citations
                )
                for number in numbers:
                    compact = re.sub(r"\s+", "", number.casefold())
                    compact_source = re.sub(r"\s+", "", source_text.casefold())
                    if compact and compact not in compact_source:
                        issues.append(
                            {
                                "section_id": section.get("section_id"),
                                "claim": sentence[:1000],
                                "value": number,
                                "citations": sorted(citations),
                                "blocking": True,
                                "reason": "numeric_value_not_found_in_cited_evidence",
                            }
                        )
    return issues


_NLI_RUNTIME: dict[str, Any] = {}


def _load_nli() -> tuple[Any, Any] | tuple[None, None]:
    if _NLI_RUNTIME.get("disabled"):
        return None, None
    if _NLI_RUNTIME.get("model") is not None:
        return _NLI_RUNTIME["tokenizer"], _NLI_RUNTIME["model"]

    model_id = os.getenv(
        "ENNOSCHOLAR_EDITORIAL_NLI_MODEL",
        "MoritzLaurer/multilingual-MiniLMv2-L6-mnli-xnli",
    ).strip()
    local_only = os.getenv(
        "ENNOSCHOLAR_EDITORIAL_NLI_LOCAL_ONLY",
        "1",
    ).strip().casefold() in {"1", "true", "yes", "on"}

    try:
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(
            model_id,
            local_files_only=local_only,
        )
        model = AutoModelForSequenceClassification.from_pretrained(
            model_id,
            local_files_only=local_only,
        )
        model.eval()
        _NLI_RUNTIME["tokenizer"] = tokenizer
        _NLI_RUNTIME["model"] = model
        return tokenizer, model
    except Exception as exc:
        _NLI_RUNTIME["disabled"] = True
        _NLI_RUNTIME["error"] = f"{type(exc).__name__}: {exc}"
        return None, None


def _nli_scores(premise: str, hypothesis: str) -> dict[str, float]:
    tokenizer, model = _load_nli()
    if tokenizer is None or model is None:
        return {}

    try:
        import torch

        encoded = tokenizer(
            premise[:10000],
            hypothesis[:3000],
            return_tensors="pt",
            truncation=True,
            max_length=512,
        )
        with torch.no_grad():
            logits = model(**encoded).logits[0]
            probs = torch.softmax(logits, dim=-1).tolist()
        labels = {
            int(key): str(value).casefold()
            for key, value in (model.config.id2label or {}).items()
        }
        return {
            labels.get(index, str(index)): float(score)
            for index, score in enumerate(probs)
        }
    except Exception:
        return {}


def verify_strong_claims_nli(
    draft: Mapping[str, Any],
    evidence_units: Sequence[Mapping[str, Any]],
    *,
    enabled: bool,
) -> dict[str, Any]:
    evidence = _evidence_by_citation(evidence_units)
    issues = []
    checked = 0

    if not enabled:
        return {
            "enabled": False,
            "available": False,
            "issues": [],
        }

    for section in draft.get("sections") or []:
        if not isinstance(section, Mapping):
            continue
        bodies = [
            _clean(section.get("content")),
            *[
                _clean(row.get("content"))
                for row in section.get("subsections") or []
                if isinstance(row, Mapping)
            ],
        ]
        for body in bodies:
            for sentence in re.split(r"(?<=[.!?])\s+", body):
                if not _STRONG_CLAIM_RE.search(sentence):
                    continue
                citations = citations_from_text(sentence)
                if not citations:
                    continue
                premise = "\n".join(
                    evidence.get(citation, "")
                    for citation in citations
                )
                if not premise.strip():
                    continue
                scores = _nli_scores(premise, sentence)
                if not scores:
                    continue
                checked += 1

                entailment = max(
                    [v for label, v in scores.items() if "entail" in label]
                    or [0.0]
                )
                contradiction = max(
                    [v for label, v in scores.items() if "contrad" in label]
                    or [0.0]
                )

                if contradiction >= 0.55 or entailment < 0.35:
                    issues.append(
                        {
                            "section_id": section.get("section_id"),
                            "claim": sentence[:1200],
                            "citations": sorted(citations),
                            "entailment": round(entailment, 4),
                            "contradiction": round(contradiction, 4),
                            "blocking": contradiction >= 0.55,
                            "reason": (
                                "nli_contradiction"
                                if contradiction >= 0.55
                                else "strong_claim_weakly_supported"
                            ),
                        }
                    )

    return {
        "enabled": True,
        "available": checked > 0 or not bool(_NLI_RUNTIME.get("error")),
        "model_error": _NLI_RUNTIME.get("error"),
        "claims_checked": checked,
        "issues": issues,
    }


def language_tool_report(markdown: str, *, enabled: bool) -> dict[str, Any]:
    if not enabled:
        return {"enabled": False, "available": False, "matches": []}

    try:
        import language_tool_python

        tool = language_tool_python.LanguageTool("fr")
        matches = tool.check(markdown[:120000])
        output = []
        for match in matches[:120]:
            output.append(
                {
                    "rule_id": getattr(match, "ruleId", ""),
                    "message": getattr(match, "message", ""),
                    "offset": getattr(match, "offset", None),
                    "error_length": getattr(match, "errorLength", None),
                    "replacements": list(getattr(match, "replacements", []) or [])[:5],
                }
            )
        try:
            tool.close()
        except Exception:
            pass
        return {
            "enabled": True,
            "available": True,
            "matches_count": len(matches),
            "matches": output,
        }
    except Exception as exc:
        return {
            "enabled": True,
            "available": False,
            "error": f"{type(exc).__name__}: {exc}",
            "matches": [],
        }


def _public_figure_caption_fr(placement: Mapping[str, Any]) -> str:
    original = _clean(placement.get("caption"), 1800)
    label = _clean(placement.get("figure_label"), 120)
    citation = _citation_label(placement.get("citation_label"))
    text = f"{label} {original}".casefold()

    if re.search(r"\bconfusion\s+matrix\b|matrice\s+de\s+confusion", text):
        desc = "Matrice de confusion des performances ATR"
    elif re.search(r"\broc\b", text):
        desc = "Courbe ROC des performances ATR"
    elif re.search(r"\bprecision\b|\brecall\b|average precision", text):
        desc = "Résultats de précision et de rappel"
    elif re.search(r"\bssim\b|\bser\b|synthetic|measured|réel|synthétique", text):
        desc = "Comparaison expérimentale entre données simulées et mesurées"
    elif re.search(r"\brcs\b|radar cross section", text):
        desc = "Résultat de section efficace radar (RCS)"
    elif re.search(r"\bperformance\b|\baccuracy\b|\bscore\b", text):
        desc = "Résultats de performance expérimentale"
    elif re.search(r"\bgeometry\b|géométrie|ray tracing|ray launching", text):
        desc = "Schéma de géométrie de simulation électromagnétique"
    elif re.search(r"\bcad\b|cao", text):
        desc = "Modèle CAO utilisé pour la simulation"
    else:
        desc = "Figure originale issue de la source"

    return f"{desc}{f' [{citation}]' if citation else ''}"


def select_argumentative_figures(
    placements: Sequence[Mapping[str, Any]],
    *,
    article_min_score: float,
    project_min_score: float,
    max_per_section: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    selected = []
    rejected = []
    per_section: dict[str, int] = defaultdict(int)

    for raw in placements:
        placement = dict(raw)
        section_id = _clean(placement.get("section_id"), 160)
        caption = " ".join(
            [
                _clean(placement.get("figure_label"), 120),
                _clean(placement.get("caption"), 1800),
                _clean(placement.get("source_title"), 800),
            ]
        )
        similarity = float(placement.get("semantic_similarity") or 0.0)
        quality = float(
            placement.get("quality_score")
            or placement.get("ranking_score")
            or 0.0
        )
        has_argument = bool(_ARGUMENTATIVE_FIGURE_RE.search(caption))
        generic = bool(_GENERIC_FIGURE_RE.search(caption))
        citation = _citation_label(placement.get("citation_label"))

        score = (
            similarity * 2.5
            + min(1.0, quality)
            + (0.55 if has_argument else 0.0)
            - (0.55 if generic and not has_argument else 0.0)
        )
        threshold = article_min_score if citation else project_min_score

        reason = ""
        if max_per_section == 0:
            reason = "figures_disabled"
        elif per_section[section_id] >= max_per_section:
            reason = "section_already_has_stronger_figure"
        elif score < threshold:
            reason = "insufficient_argumentative_value"

        if reason:
            rejected.append(
                {
                    "visual_id": placement.get("visual_id"),
                    "section_id": section_id,
                    "score": round(score, 4),
                    "reason": reason,
                }
            )
            continue

        placement["editorial_figure_score"] = round(score, 4)
        placement["public_caption_fr"] = _public_figure_caption_fr(placement)
        placement["original_caption_preserved_in_metadata"] = _clean(
            placement.get("caption"), 1800
        )
        placement["caption"] = placement["public_caption_fr"]
        selected.append(placement)
        per_section[section_id] += 1

    selected.sort(
        key=lambda row: (
            _clean(row.get("section_id"), 160),
            -float(row.get("editorial_figure_score") or 0),
        )
    )
    return selected, rejected


def _extract_cards(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    value = payload.get("cards")
    if not isinstance(value, list):
        value = payload.get("article_cards")
    return [
        dict(row)
        for row in (value or [])
        if isinstance(row, Mapping)
    ]


def run_cir_editorial_validation(
    *,
    phase5_payload: Mapping[str, Any],
    plan_contract: Mapping[str, Any],
    article_cards_payload: Mapping[str, Any],
    evidence_units: Sequence[Mapping[str, Any]],
    config: Optional[EditorialConfig] = None,
) -> dict[str, Any]:
    cfg = config or EditorialConfig(
        nli_enabled=os.getenv(
            "ENNOSCHOLAR_EDITORIAL_NLI_ENABLED", "1"
        ).strip().casefold() in {"1", "true", "yes", "on"},
        language_tool_enabled=os.getenv(
            "ENNOSCHOLAR_EDITORIAL_LANGUAGE_TOOL_ENABLED", "0"
        ).strip().casefold() in {"1", "true", "yes", "on"},
    )

    cards = _extract_cards(article_cards_payload)
    contract = build_editorial_contract(plan_contract, cards)

    draft = (
        dict(phase5_payload.get("draft_json"))
        if isinstance(phase5_payload.get("draft_json"), Mapping)
        else {}
    )

    cleaned, extra_sections = enforce_exact_consultant_plan(draft, contract)
    cleaned, repetition_report = remove_conservative_repetitions(
        cleaned,
        lexical_threshold=cfg.lexical_duplicate_threshold,
        semantic_threshold=cfg.semantic_duplicate_threshold,
    )
    cleaned, english_issues = clean_and_detect_english(cleaned)

    citation_scope_issues = validate_citation_scope(cleaned, contract)
    numeric_issues = verify_numeric_claims(cleaned, evidence_units)
    nli = verify_strong_claims_nli(
        cleaned,
        evidence_units,
        enabled=cfg.nli_enabled,
    )

    visual_placements = [
        dict(row)
        for row in (phase5_payload.get("visual_placements") or [])
        if isinstance(row, Mapping)
    ]
    selected_figures, rejected_figures = select_argumentative_figures(
        visual_placements,
        article_min_score=cfg.article_figure_min_score,
        project_min_score=cfg.project_figure_min_score,
        max_per_section=cfg.max_figures_per_section,
    )

    references = [
        dict(row)
        for row in (phase5_payload.get("references") or [])
        if isinstance(row, Mapping)
        and _citation_label(row.get("citation_label"))
        in contract.allowed_citations
    ]

    from agents.EnnoScholar.state_of_art.phase_5_state_of_art_writer_service import (
        draft_to_markdown,
    )

    markdown = draft_to_markdown(
        cleaned,
        phase5_payload.get("guard") or {},
        references=references,
        visual_placements=selected_figures,
    )

    language_tool = language_tool_report(
        markdown,
        enabled=cfg.language_tool_enabled,
    )

    nli_blocking = [
        row for row in (nli.get("issues") or [])
        if row.get("blocking")
    ]
    blockers = [
        *citation_scope_issues,
        *numeric_issues,
        *nli_blocking,
    ]

    updated_payload = dict(phase5_payload)
    updated_payload["draft_json"] = cleaned
    updated_payload["references"] = references
    updated_payload["visual_placements"] = selected_figures
    updated_payload.setdefault("stats", {})[
        "original_figures_inserted_count"
    ] = len(selected_figures)
    updated_payload["editorial_validation"] = {
        "ok": not blockers,
        "blocking_issues_count": len(blockers),
        "extra_sections_removed_count": len(extra_sections),
        "repetitions_removed_count": len(repetition_report),
        "residual_english_count": len(english_issues),
        "figures_before": len(visual_placements),
        "figures_after": len(selected_figures),
        "numeric_issues_count": len(numeric_issues),
        "nli_claims_checked": nli.get("claims_checked", 0),
        "nli_issues_count": len(nli.get("issues") or []),
    }

    report = {
        "ok": not blockers,
        "contract": {
            "allowed_sections_count": max(
                len(contract.allowed_section_ids),
                len(contract.allowed_section_titles),
            ),
            "allowed_citations": sorted(contract.allowed_citations),
            "exact_consultant_plan": True,
        },
        "extra_sections_removed": extra_sections,
        "repetitions_removed": repetition_report,
        "english": {
            "residual_issues": english_issues,
            "terminology_normalized": True,
            "technical_terms_whitelisted": sorted(_ALLOWED_TECHNICAL_ENGLISH),
        },
        "citation_scope_issues": citation_scope_issues,
        "numeric_issues": numeric_issues,
        "nli": nli,
        "figures": {
            "selected_count": len(selected_figures),
            "rejected_count": len(rejected_figures),
            "selected": selected_figures,
            "rejected": rejected_figures,
            "policy": "argumentative_value_no_global_quota_max_one_per_section",
        },
        "language_tool": language_tool,
        "blocking_issues": blockers,
        "tools": {
            "pydantic": True,
            "rapidfuzz": True,
            "sentence_transformers": True,
            "lingua": True,
            "multilingual_nli": cfg.nli_enabled,
            "language_tool": cfg.language_tool_enabled,
        },
    }

    return {
        "ok": not blockers,
        "payload": updated_payload,
        "markdown": markdown,
        "report": report,
    }
