"""Targeted repair of a refused section proposal (no second drafting engine).

A refused proposal is diagnosed locally, paragraph by paragraph, with the same
validators (attribution, numeric trace). The repair replaces only the paragraphs it
names (``paragraph_patches``); every other paragraph is kept byte for byte, with its
claims, numbers and proofs. The merged text then passes every ordinary check of
``write_section`` on the whole section before it can be saved. A global problem can
still be repaired with a complete text.
"""
from __future__ import annotations

import copy
import re

PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")


def split(text):
    return [p for p in PARAGRAPH_SPLIT.split(text or "") if p.strip()]


def diagnose(toolkit, arguments, refusal):
    """Paragraphs responsible for a refusal, with their problems (deterministic)."""
    from .attribution import check_attribution
    from .provenance_check import numeric_trace
    text = arguments.get("text") or ""
    paragraphs = split(text)
    rows = {}
    try:
        for issue in check_attribution(text, arguments.get("article_ids") or [], toolkit):
            index = next((i for i, p in enumerate(paragraphs) if issue["sentence"][:120] in p), None)
            if index is not None:
                rows.setdefault(index, []).append({"code": issue["code"], "names": issue["names"],
                    "sentence": issue["sentence"][:300]})
        _, untraced = numeric_trace(toolkit, text, arguments.get("evidence_ids") or [],
            arguments.get("project_evidence_ids") or [])
        for row in untraced:
            index = next((i for i, p in enumerate(paragraphs) if row["context"][:80] in p), None)
            if index is not None:
                rows.setdefault(index, []).append({"code": "numeric_value_without_proof", "value": row["value"]})
    except Exception:
        # A diagnosis never blocks the ordinary refusal; the model can send a whole text.
        return None
    for claim in (refusal or {}).get("claims_not_in_text", []) or []:
        rows.setdefault("claims", []).append(claim)
    if not rows:
        return None
    located = sorted(k for k in rows if isinstance(k, int))
    return {"paragraphs_total": len(paragraphs), "kept_paragraphs": [i for i in range(len(paragraphs)) if i not in located],
        "paragraphs": [{"index": i, "excerpt": paragraphs[i][:240], "problems": rows[i]} for i in located],
        **({"claims_to_correct": rows["claims"]} if "claims" in rows else {}),
        "repair_protocol": "Réparation ciblée : write_section(save) avec paragraph_patches=[{index, text}] pour ces seuls "
            "paragraphes (index de la proposition refusée), et les claims / evidence_ids / project_evidence_ids de ces "
            "paragraphes. Les autres paragraphes sont conservés à l'identique ; toute la section est revalidée. "
            "Un texte complet reste possible si le problème est global."}


def apply_patches(text, patches):
    paragraphs = split(text)
    changed = []
    for patch in patches:
        index = patch["index"]
        if not 0 <= index < len(paragraphs):
            raise ValueError(f"Paragraphe {index} inexistant dans la proposition refusée ({len(paragraphs)} paragraphes).")
        replacement = (patch.get("text") or "").strip()
        if PARAGRAPH_SPLIT.search(replacement):
            raise ValueError("Un correctif remplace un seul paragraphe (pas de ligne vide interne).")
        paragraphs[index] = replacement
        changed.append(index)
    return "\n\n".join(p for p in paragraphs if p.strip()), changed


def merge(base, patch_arguments):
    """The refused proposal with its named paragraphs replaced. Claims, numbers and
    calculations of untouched paragraphs are kept; the patch adds its own."""
    patches = patch_arguments.get("paragraph_patches") or []
    old_paragraphs = split(base.get("text"))
    text, changed = apply_patches(base["text"], patches)
    removed = " \n".join(old_paragraphs[i] for i in changed)
    def kept(rows, key):
        return [r for r in rows or [] if str(r.get(key, "")) not in removed or not str(r.get(key, ""))]
    merged = copy.deepcopy(base)
    merged.update({key: patch_arguments[key] for key in ("instructions",) if patch_arguments.get(key)})
    merged["text"] = text
    for key in ("article_ids", "evidence_ids", "project_evidence_ids"):
        merged[key] = list(dict.fromkeys([*(base.get(key) or []), *(patch_arguments.get(key) or [])]))
    merged["claims"] = [*kept(base.get("claims"), "statement"), *(patch_arguments.get("claims") or [])]
    merged["numeric_claims"] = [*kept(base.get("numeric_claims"), "value"), *(patch_arguments.get("numeric_claims") or [])]
    merged["calculations"] = [*kept(base.get("calculations"), "result"), *(patch_arguments.get("calculations") or [])]
    merged["paragraph_patches"] = None
    merged["paragraph_id"] = None
    return merged, {"changed_paragraphs": changed, "kept_paragraphs": len(old_paragraphs)-len(changed),
        "paragraphs_total": len(old_paragraphs)}
