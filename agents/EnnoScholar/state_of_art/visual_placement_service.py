# -*- coding: utf-8 -*-
from __future__ import annotations

"""Shared deterministic visual selection for EnnoScholar writers."""

import math
import os
import re
import unicodedata
from collections import Counter
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple


def _clean(value: Any, limit: int = 30000) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        value = " ".join(_clean(item, limit) for item in value)
    elif isinstance(value, Mapping):
        for key in (
            "text", "value", "title", "label", "name", "description",
            "summary", "caption", "content",
        ):
            candidate = _clean(value.get(key), limit)
            if candidate:
                value = candidate
                break
        else:
            value = ""
    return re.sub(r"\s+", " ", str(value).replace("\x00", " ")).strip()[:limit]


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    return [value]


def normalize_citation(value: Any) -> str:
    text = _clean(value, 80).strip("[] ")
    match = re.search(r"\b([AS])\s*(\d+)\b", text, flags=re.I)
    return f"{match.group(1).upper()}{match.group(2)}" if match else ""


def citations_from_text(value: Any) -> Set[str]:
    text = _clean(value, 200000)
    output: Set[str] = set()
    for bracket in re.finditer(r"\[([^\[\]]+)\]", text):
        for match in re.finditer(r"\b([AS])\s*(\d+)\b", bracket.group(1), flags=re.I):
            output.add(f"{match.group(1).upper()}{match.group(2)}")
    return output


def _tokens(value: Any) -> Set[str]:
    text = unicodedata.normalize("NFKD", _clean(value, 30000).casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    stop = {
        "the", "and", "for", "with", "from", "this", "that", "these", "those",
        "une", "des", "dans", "pour", "avec", "sur", "les", "est", "sont",
        "figure", "fig", "table", "source", "article", "section", "page",
        "results", "result", "method", "methods", "study", "paper",
    }
    return {
        token for token in re.findall(r"[a-z0-9]{3,}", text)
        if token not in stop
    }


def token_similarity(left: Any, right: Any) -> float:
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def _number(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bbox_dimensions(candidate: Mapping[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    raw = (
        candidate.get("bbox")
        or candidate.get("crop_bbox")
        or candidate.get("bounding_box")
        or candidate.get("box")
    )
    if isinstance(raw, Mapping):
        x0 = _number(raw.get("x0") if "x0" in raw else raw.get("left"))
        y0 = _number(raw.get("y0") if "y0" in raw else raw.get("top"))
        x1 = _number(raw.get("x1") if "x1" in raw else raw.get("right"))
        y1 = _number(raw.get("y1") if "y1" in raw else raw.get("bottom"))
        if None not in (x0, y0, x1, y1):
            return abs(x1 - x0), abs(y1 - y0)
        return _number(raw.get("width")), _number(raw.get("height"))
    if isinstance(raw, (list, tuple)) and len(raw) >= 4:
        values = [_number(item) for item in raw[:4]]
        if all(item is not None for item in values):
            x0, y0, x1, y1 = values
            return abs(x1 - x0), abs(y1 - y0)
    return (
        _number(candidate.get("width") or candidate.get("crop_width") or candidate.get("pixel_width")),
        _number(candidate.get("height") or candidate.get("crop_height") or candidate.get("pixel_height")),
    )


def _crop_page_fraction(candidate: Mapping[str, Any]) -> Optional[float]:
    width, height = _bbox_dimensions(candidate)
    page_width = _number(candidate.get("page_width") or candidate.get("source_page_width"))
    page_height = _number(candidate.get("page_height") or candidate.get("source_page_height"))
    if (
        width is None or height is None
        or page_width is None or page_height is None
        or page_width <= 0 or page_height <= 0
    ):
        return None
    return max(0.0, min(2.0, (width * height) / (page_width * page_height)))


def _quality(candidate: Mapping[str, Any]) -> float:
    raw = _number(
        candidate.get("ranking_score")
        or candidate.get("quality_score")
        or candidate.get("visual_quality_score")
        or candidate.get("score")
    )
    if raw is None:
        return 0.0
    if 1.0 < raw <= 100.0:
        raw /= 100.0
    return max(0.0, min(1.0, raw))


_BAD_KIND_TOKENS = {
    "logo", "icon", "favicon", "avatar", "watermark", "header", "footer",
    "banner", "brand", "branding", "publisher_mark", "page_number",
    "decoration", "decorative", "cover", "thumbnail", "qr", "barcode",
    "paragraph", "text_block", "text-only", "text_only", "plain_text",
}

_GOOD_KIND_TOKENS = {
    "figure", "table", "diagram", "schema", "schematic", "graph", "chart",
    "plot", "architecture", "workflow", "pipeline", "heatmap", "matrix",
    "confusion_matrix", "experimental_result", "result_figure", "map",
    "microscopy", "illustration_scientific", "scientific_figure",
}


def screen_visual_candidate(candidate: Mapping[str, Any]) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    visual_id = _clean(candidate.get("visual_id"), 160)
    if not visual_id:
        reasons.append("missing_visual_id")

    def _normalize_kind(value: Any) -> str:
        text = unicodedata.normalize("NFKD", _clean(value, 300).casefold())
        text = "".join(
            char for char in text
            if not unicodedata.combining(char)
        )
        return re.sub(r"[^a-z0-9]+", "_", text).strip("_")

    normalized_kinds = [
        _normalize_kind(candidate.get(key))
        for key in (
            "visual_type",
            "figure_type",
            "kind",
            "type",
            "category",
            "content_type",
            "role",
            "asset_type",
        )
        if _clean(candidate.get(key), 300)
    ]

    def _has_kind(*markers: str) -> bool:
        for value in normalized_kinds:
            padded = f"_{value}_"
            for marker in markers:
                normalized_marker = _normalize_kind(marker)
                if (
                    value == normalized_marker
                    or f"_{normalized_marker}_" in padded
                    or value.startswith(normalized_marker + "_")
                    or value.endswith("_" + normalized_marker)
                ):
                    return True
        return False

    explicit_bad_flags = (
        "is_logo",
        "is_header",
        "is_footer",
        "is_watermark",
        "is_decorative",
        "text_only",
        "is_text_only",
        "is_cover",
        "is_thumbnail",
    )
    if any(candidate.get(flag) is True for flag in explicit_bad_flags):
        reasons.append("explicit_non_scientific_visual")

    if _has_kind(
        "logo",
        "icon",
        "favicon",
        "avatar",
        "watermark",
        "header",
        "footer",
        "banner",
        "brand",
        "branding",
        "publisher_mark",
        "page_number",
        "decoration",
        "decorative",
        "cover",
        "thumbnail",
        "qr",
        "barcode",
        "paragraph",
        "text_block",
        "text_only",
        "plain_text",
    ):
        reasons.append("non_scientific_visual_type")

    if candidate.get("usable") is False:
        reasons.append("marked_unusable")
    if candidate.get("crop_valid") is False:
        reasons.append("invalid_crop")
    if candidate.get("renderable") is False:
        reasons.append("not_renderable")

    width, height = _bbox_dimensions(candidate)
    if width is not None and height is not None:
        if width <= 0 or height <= 0:
            reasons.append("invalid_dimensions")
        else:
            if width > 2 and height > 2 and (width < 90 or height < 70):
                reasons.append("tiny_crop")
            ratio = width / max(height, 1e-9)
            if ratio > 9.0 or ratio < 0.11:
                reasons.append("extreme_aspect_ratio")

    page_fraction = _crop_page_fraction(candidate)
    if page_fraction is not None and page_fraction >= 0.92:
        reasons.append("near_full_page_capture")

    text_density = _number(
        candidate.get("text_density")
        or candidate.get("ocr_text_ratio")
        or candidate.get("text_ratio")
    )

    table_like = _has_kind(
        "table",
        "scientific_table",
        "result_table",
        "comparison_table",
        "matrix",
        "confusion_matrix",
    )

    explicitly_scientific = _has_kind(
        "figure",
        "scientific_figure",
        "diagram",
        "schema",
        "schematic",
        "graph",
        "chart",
        "plot",
        "architecture",
        "workflow",
        "pipeline",
        "heatmap",
        "experimental_result",
        "result_figure",
        "map",
        "microscopy",
        "illustration_scientific",
    )

    if (
        text_density is not None
        and text_density >= 0.90
        and not table_like
        and not explicitly_scientific
    ):
        reasons.append("text_dominant_crop")

    descriptor = " ".join(
        _clean(candidate.get(key), 1800)
        for key in (
            "figure_label",
            "caption",
            "context",
            "alt_text",
            "source_title",
            "visual_type",
            "figure_type",
        )
    )
    if len(descriptor.strip()) < 8:
        reasons.append("missing_visual_semantics")

    blocking = {
        "missing_visual_id",
        "explicit_non_scientific_visual",
        "non_scientific_visual_type",
        "marked_unusable",
        "invalid_crop",
        "not_renderable",
        "invalid_dimensions",
        "tiny_crop",
        "extreme_aspect_ratio",
        "near_full_page_capture",
        "text_dominant_crop",
        "missing_visual_semantics",
    }
    return not any(reason in blocking for reason in reasons), reasons

def candidate_semantic_text(candidate: Mapping[str, Any]) -> str:
    return " ".join(
        _clean(candidate.get(key), limit)
        for key, limit in (
            ("figure_label", 200), ("caption", 2500), ("context", 3500),
            ("alt_text", 1200), ("source_title", 1200),
            ("visual_type", 300), ("figure_type", 300),
        )
    )


def _split_paragraphs(value: Any) -> List[str]:
    text = str(value or "").replace("\\n\\n", "\n\n")
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    return paragraphs or ([text.strip()] if text.strip() else [])


def _paragraph_anchors(draft: Mapping[str, Any]) -> List[Dict[str, Any]]:
    anchors: List[Dict[str, Any]] = []
    for section_index, section in enumerate(draft.get("sections") or []):
        if not isinstance(section, Mapping):
            continue
        section_id = _clean(section.get("section_id"), 160) or f"section_{section_index + 1}"

        paragraph_rows = section.get("paragraphs")
        if isinstance(paragraph_rows, list):
            for paragraph_index, paragraph in enumerate(paragraph_rows):
                if not isinstance(paragraph, Mapping):
                    continue
                text = _clean(paragraph.get("text"), 20000)
                if not text:
                    continue
                citations = {
                    normalize_citation(item)
                    for item in _as_list(paragraph.get("citations"))
                    if normalize_citation(item)
                }
                citations.update(citations_from_text(text))
                anchors.append({
                    "anchor_key": f"{section_id}|section|0|{paragraph_index}",
                    "section_id": section_id,
                    "section_index": section_index,
                    "content_scope": "section",
                    "subsection_index": None,
                    "paragraph_index": paragraph_index,
                    "paragraph_text": text,
                    "citations": citations,
                })
        else:
            for paragraph_index, text in enumerate(_split_paragraphs(section.get("content"))):
                anchors.append({
                    "anchor_key": f"{section_id}|section|0|{paragraph_index}",
                    "section_id": section_id,
                    "section_index": section_index,
                    "content_scope": "section",
                    "subsection_index": None,
                    "paragraph_index": paragraph_index,
                    "paragraph_text": text,
                    "citations": citations_from_text(text),
                })

        for subsection_index, subsection in enumerate(section.get("subsections") or []):
            if not isinstance(subsection, Mapping):
                continue
            for paragraph_index, text in enumerate(_split_paragraphs(subsection.get("content"))):
                anchors.append({
                    "anchor_key": f"{section_id}|subsection|{subsection_index}|{paragraph_index}",
                    "section_id": section_id,
                    "section_index": section_index,
                    "content_scope": "subsection",
                    "subsection_index": subsection_index,
                    "paragraph_index": paragraph_index,
                    "paragraph_text": text,
                    "citations": citations_from_text(text),
                })
    return anchors


def _normalized_vectors(texts: Sequence[str]) -> Optional[List[List[float]]]:
    if not texts:
        return []
    if os.getenv("ENNOSCHOLAR_VISUAL_MULTILINGUAL_MATCHING", "1").strip().lower() not in {
        "1", "true", "yes", "on", "oui"
    }:
        return None
    try:
        from modules.RAG.vector_store import encode_texts
        raw_vectors = encode_texts(list(texts))
        if len(raw_vectors) != len(texts):
            return None
        output: List[List[float]] = []
        for raw in raw_vectors:
            vector = [float(item) for item in raw]
            norm = math.sqrt(sum(item * item for item in vector))
            output.append(
                [item / norm for item in vector]
                if norm > 0
                else [0.0 for _ in vector]
            )
        return output
    except Exception:
        return None


def _semantic_matrix(anchors, candidates):
    anchor_texts = [_clean(anchor.get("paragraph_text"), 8000) for anchor in anchors]
    candidate_texts = [candidate_semantic_text(candidate)[:5000] for candidate in candidates]
    vectors = _normalized_vectors([*anchor_texts, *candidate_texts])
    if vectors is None:
        return {}
    split = len(anchor_texts)
    output = {}
    for a_index, anchor in enumerate(anchors):
        left = vectors[a_index]
        for c_index, candidate in enumerate(candidates):
            right = vectors[split + c_index]
            cosine = (
                sum(a * b for a, b in zip(left, right))
                if left and right and len(left) == len(right)
                else 0.0
            )
            adjusted = max(0.0, (cosine - 0.18) / 0.82)
            output[(
                str(anchor.get("anchor_key") or ""),
                _clean(candidate.get("visual_id"), 160),
            )] = min(1.0, adjusted)
    return output


def _contract_sections(contract: Mapping[str, Any]):
    output = {}
    for index, section in enumerate(contract.get("sections") or []):
        if not isinstance(section, Mapping):
            continue
        section_id = _clean(section.get("section_id"), 160) or f"section_{index + 1}"
        output[section_id] = section
    return output


def _collect_candidates(cards_payload, cards, *, citation_field, include_project_visuals):
    accepted = []
    rejected = []
    reason_counts: Counter = Counter()
    seen: Set[str] = set()

    for card in cards:
        if not isinstance(card, Mapping):
            continue
        citation = normalize_citation(card.get(citation_field))
        source_title = _clean(card.get("title") or card.get("article_title"), 1000)
        for raw in card.get("visual_evidence") or []:
            if not isinstance(raw, Mapping):
                continue
            item = dict(raw)
            item["citation_label"] = citation
            item.setdefault("source_title", source_title)
            item.setdefault("source_kind", "scientific_article")
            visual_id = _clean(item.get("visual_id"), 160)
            if visual_id and visual_id in seen:
                reason_counts["duplicate_visual_id"] += 1
                rejected.append({
                    "visual_id": visual_id,
                    "citation_label": citation,
                    "reasons": ["duplicate_visual_id"],
                })
                continue
            ok, reasons = screen_visual_candidate(item)
            if not ok:
                reason_counts.update(reasons)
                rejected.append({
                    "visual_id": visual_id,
                    "citation_label": citation,
                    "source_title": source_title,
                    "reasons": reasons,
                })
                if visual_id:
                    seen.add(visual_id)
                continue
            if visual_id:
                seen.add(visual_id)
            accepted.append(item)

    if include_project_visuals:
        for raw in cards_payload.get("project_visual_evidence") or []:
            if not isinstance(raw, Mapping):
                continue
            item = dict(raw)
            item["citation_label"] = ""
            item.setdefault("source_kind", "project_document")
            visual_id = _clean(item.get("visual_id"), 160)
            if visual_id and visual_id in seen:
                reason_counts["duplicate_visual_id"] += 1
                rejected.append({
                    "visual_id": visual_id,
                    "citation_label": "",
                    "reasons": ["duplicate_visual_id"],
                })
                continue
            ok, reasons = screen_visual_candidate(item)
            if not ok:
                reason_counts.update(reasons)
                rejected.append({
                    "visual_id": visual_id,
                    "citation_label": "",
                    "source_title": _clean(item.get("source_title"), 1000),
                    "reasons": reasons,
                })
                if visual_id:
                    seen.add(visual_id)
                continue
            if visual_id:
                seen.add(visual_id)
            accepted.append(item)

    return accepted, rejected, reason_counts


def build_visual_placements_shared(
    *,
    draft,
    contract,
    cards_payload,
    cards,
    citation_field,
    article_min_similarity=0.09,
    project_min_similarity=0.10,
    max_per_section=1,
    max_visuals=0,
    include_project_visuals=True,
    enabled=True,
):
    if not enabled:
        return [], {
            "enabled": False, "candidate_count": 0,
            "screened_candidate_count": 0, "rejected_candidate_count": 0,
            "placed_count": 0, "no_visual_reason": "visuals_disabled",
            "rejection_reasons": {}, "rejected": [], "unmatched": [],
        }

    candidates, rejected, reason_counts = _collect_candidates(
        cards_payload, cards,
        citation_field=citation_field,
        include_project_visuals=include_project_visuals,
    )
    total_candidate_count = len(candidates) + len(rejected)
    anchors = _paragraph_anchors(draft)

    if not candidates:
        return [], {
            "enabled": True,
            "candidate_count": total_candidate_count,
            "screened_candidate_count": 0,
            "rejected_candidate_count": len(rejected),
            "placed_count": 0,
            "no_visual_reason": (
                "no_visual_evidence"
                if total_candidate_count == 0
                else "all_visual_candidates_rejected"
            ),
            "rejection_reasons": dict(reason_counts),
            "rejected": rejected, "unmatched": [],
        }

    if not anchors:
        return [], {
            "enabled": True,
            "candidate_count": total_candidate_count,
            "screened_candidate_count": len(candidates),
            "rejected_candidate_count": len(rejected),
            "placed_count": 0,
            "no_visual_reason": "no_draft_paragraph_anchor",
            "rejection_reasons": dict(reason_counts),
            "rejected": rejected, "unmatched": [],
        }

    semantic = _semantic_matrix(anchors, candidates)
    contract_by_id = _contract_sections(contract)
    scored = []
    candidate_status = {
        _clean(candidate.get("visual_id"), 160): {
            "cited_anchor_seen": False,
            "similar_anchor_seen": False,
        }
        for candidate in candidates
    }

    for anchor in anchors:
        section_id = str(anchor.get("section_id") or "")
        section_contract = contract_by_id.get(section_id) or {}
        section_verrous = {
            _clean(value, 160)
            for value in (
                section_contract.get("verrou_ids")
                or [
                    verrou.get("verrou_id")
                    for verrou in section_contract.get("verrous") or []
                    if isinstance(verrou, Mapping)
                ]
            )
            if _clean(value, 160)
        }
        paragraph_text = _clean(anchor.get("paragraph_text"), 12000)
        paragraph_context = " ".join([
            paragraph_text,
            _clean(section_contract.get("title"), 1200),
            _clean(section_contract.get("objective"), 2500),
        ])

        for candidate in candidates:
            visual_id = _clean(candidate.get("visual_id"), 160)
            citation = normalize_citation(candidate.get("citation_label"))
            if not visual_id:
                continue
            status = candidate_status[visual_id]

            if citation:
                if citation not in set(anchor.get("citations") or set()):
                    continue
                status["cited_anchor_seen"] = True
            else:
                status["cited_anchor_seen"] = True

            lexical = token_similarity(candidate_semantic_text(candidate), paragraph_context)
            embedding = semantic.get((str(anchor.get("anchor_key") or ""), visual_id), 0.0)
            similarity = max(lexical, embedding)
            threshold = article_min_similarity if citation else project_min_similarity
            if similarity < threshold:
                continue
            status["similar_anchor_seen"] = True

            quality = _quality(candidate)
            score = (2.0 if citation else 0.0) + quality + similarity * 3.0
            target_verrous = {
                _clean(value, 160)
                for value in candidate.get("target_verrous") or []
                if _clean(value, 160)
            }
            if section_verrous and target_verrous & section_verrous:
                score += 0.35

            scored.append((
                score,
                int(anchor.get("section_index") or 0),
                {
                    "section_id": section_id,
                    "visual_id": visual_id,
                    "citation_label": citation,
                    "source_kind": _clean(candidate.get("source_kind"), 100),
                    "source_title": _clean(candidate.get("source_title"), 900),
                    "page": candidate.get("page"),
                    "figure_label": _clean(candidate.get("figure_label"), 180),
                    "caption": _clean(candidate.get("caption"), 1800),
                    "context": _clean(candidate.get("context"), 2600),
                    "quality_score": quality,
                    "semantic_similarity": round(similarity, 4),
                    "lexical_similarity": round(lexical, 4),
                    "selection_score": round(score, 4),
                    "content_scope": anchor.get("content_scope"),
                    "subsection_index": anchor.get("subsection_index"),
                    "paragraph_index": anchor.get("paragraph_index"),
                    "anchor_key": anchor.get("anchor_key"),
                    "anchor_excerpt": _clean(paragraph_text, 420),
                    "same_article_cited_in_paragraph": bool(citation),
                    "original_figure_preserved": True,
                    "placement_policy": (
                        "screened_visual_plus_paragraph_citation_plus_semantic_match"
                    ),
                },
            ))

    placements = []
    occupied_counts: Counter = Counter()
    occupied_visuals: Set[str] = set()
    for _, _, placement in sorted(scored, key=lambda item: (-item[0], item[1])):
        if max_visuals > 0 and len(placements) >= max_visuals:
            break
        section_id = str(placement.get("section_id") or "")
        if max_per_section > 0 and occupied_counts[section_id] >= max_per_section:
            continue
        if placement["visual_id"] in occupied_visuals:
            continue
        occupied_counts[section_id] += 1
        occupied_visuals.add(placement["visual_id"])
        placements.append(placement)

    order = {
        _clean(section.get("section_id"), 160) or f"section_{index + 1}": index
        for index, section in enumerate(draft.get("sections") or [])
        if isinstance(section, Mapping)
    }
    placements.sort(key=lambda row: (
        order.get(str(row.get("section_id") or ""), 10000),
        0 if row.get("content_scope") == "section" else 1,
        int(row.get("subsection_index") or 0),
        int(row.get("paragraph_index") or 0),
    ))

    unmatched = []
    for candidate in candidates:
        visual_id = _clean(candidate.get("visual_id"), 160)
        if visual_id in occupied_visuals:
            continue
        status = candidate_status.get(visual_id) or {}
        citation = normalize_citation(candidate.get("citation_label"))
        if citation and not status.get("cited_anchor_seen"):
            reason = "source_not_cited_in_any_paragraph"
        elif not status.get("similar_anchor_seen"):
            reason = "low_semantic_relevance"
        else:
            reason = "lower_ranked_or_section_visual_limit"
        reason_counts[reason] += 1
        unmatched.append({
            "visual_id": visual_id,
            "citation_label": citation,
            "source_title": _clean(candidate.get("source_title"), 1000),
            "reason": reason,
        })

    if placements:
        no_visual_reason = ""
    elif candidates and unmatched and all(
        row.get("reason") == "source_not_cited_in_any_paragraph"
        for row in unmatched
    ):
        no_visual_reason = "no_visual_from_cited_sources"
    elif candidates:
        no_visual_reason = "no_visual_relevant_enough"
    else:
        no_visual_reason = "all_visual_candidates_rejected"

    return placements, {
        "enabled": True,
        "candidate_count": total_candidate_count,
        "screened_candidate_count": len(candidates),
        "rejected_candidate_count": len(rejected),
        "placed_count": len(placements),
        "no_visual_reason": no_visual_reason,
        "rejection_reasons": dict(reason_counts),
        "rejected": rejected[:250],
        "unmatched": unmatched[:250],
        "policy": {
            "same_article_must_be_cited_in_paragraph": True,
            "semantic_match_required": True,
            "llm_selects_visuals": False,
            "max_per_section": max_per_section,
            "max_visuals": max_visuals,
            "article_min_similarity": article_min_similarity,
            "project_min_similarity": project_min_similarity,
            "logos_headers_text_blocks_rejected": True,
            "suspicious_crops_rejected": True,
        },
    }


def visual_markdown_lines(placement: Mapping[str, Any]) -> List[str]:
    visual_id = _clean(placement.get("visual_id"), 160)
    if not visual_id:
        return []
    figure_label = _clean(placement.get("figure_label"), 180)
    caption = _clean(placement.get("caption"), 1800)
    alt = " — ".join(item for item in (figure_label, caption) if item)
    alt = (alt or "Figure scientifique sourcée").replace("[", "").replace("]", "")
    lines = [f"![{alt}](ennoscholar-visual://{visual_id})", ""]

    provenance = []
    citation = normalize_citation(placement.get("citation_label"))
    if citation:
        provenance.append(f"source [{citation}]")
    else:
        source_title = _clean(placement.get("source_title"), 700)
        if source_title:
            provenance.append(f"document projet « {source_title} »")
    if placement.get("page"):
        provenance.append(f"page {placement['page']}")

    legend = " — ".join(item for item in (figure_label, caption) if item).rstrip(" .")
    if provenance:
        legend = f"{legend}. {' ; '.join(provenance)}" if legend else " ; ".join(provenance)
    if legend:
        lines.extend([f"*{legend.strip().rstrip(' .')}.*", ""])
    return lines


__all__ = [
    "build_visual_placements_shared",
    "candidate_semantic_text",
    "citations_from_text",
    "normalize_citation",
    "screen_visual_candidate",
    "token_similarity",
    "visual_markdown_lines",
]
