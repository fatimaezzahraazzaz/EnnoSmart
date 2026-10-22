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
# Publication markers checked by publication.publish ("unknown_marker").
CITATION = re.compile(r"\[((?:paper|ev)_[\w-]+)\]")
# A shortened identifier is completed only when it keeps at least this many characters
# after its prefix (ids carry 20 to 24) and designates exactly one admissible record
# (run 95f3507d: [ev_c9e88fa7c429da9f7] for ev_c9e88fa7c429da9f7f90cf10, refused three times).
MIN_PREFIX_CHARS = 12


def split(text):
    return [p for p in PARAGRAPH_SPLIT.split(text or "") if p.strip()]


def evidence_problem(toolkit, eid, article_ids):
    """Why publication.publish would refuse one declared publication proof (None if valid)."""
    from .evidence import exact_source_valid
    row = toolkit.evidence.get(eid)
    if not row:
        return "unknown_evidence"
    if eid not in toolkit.shown_evidence:
        return "unread_evidence"
    if row["article_id"] not in article_ids:
        return "evidence_outside_cited_articles"
    if not exact_source_valid(row, toolkit.snapshots):
        return "evidence_integrity"
    return None


def _admissible(toolkit, kind, article_ids, evidence_ids, section_article_ids):
    """Records a marker may designate: the declared ones, then the proofs shown for the
    declared works. A publication is never added to the bibliography automatically: only
    a shortened identifier of a declared one is completed (citing a new work needs its
    proofs, which the model must choose)."""
    if kind == "evidence":
        declared = [e for e in evidence_ids if e in toolkit.evidence]
        shown = [e for e in toolkit.shown_evidence if e in toolkit.evidence and toolkit.evidence[e]["article_id"] in article_ids]
        return declared, shown
    return [a for a in article_ids if a in toolkit.registry], []


def marker_issues(toolkit, arguments, section_article_ids=()):
    """Every publication marker or declared proof the publication check would refuse,
    located in its paragraph, with the record it most likely designates. Deterministic."""
    text = arguments.get("text") or ""
    article_ids = list(arguments.get("article_ids") or [])
    evidence_ids = list(arguments.get("evidence_ids") or [])
    issues, cited = [], set()
    for index, paragraph in enumerate(split(text)):
        for marker in dict.fromkeys(CITATION.findall(paragraph)):
            cited.add(marker)
            kind = "article" if marker.startswith("paper_") else "evidence"
            at = paragraph.find("[" + marker + "]")
            row = {"paragraph": index, "marker": marker, "kind": kind,
                "excerpt": paragraph[max(0, at-160):at+len(marker)+2]}
            if marker in (article_ids if kind == "article" else evidence_ids):
                problem = evidence_problem(toolkit, marker, article_ids) if kind == "evidence" else None
                if problem:
                    issues.append({**row, "problem": problem, "fixable": False, **({"article_id":
                        toolkit.evidence[marker]["article_id"]} if marker in toolkit.evidence else {})})
                continue
            declared, available = _admissible(toolkit, kind, article_ids, evidence_ids, section_article_ids)
            if marker in (toolkit.registry if kind == "article" else toolkit.evidence):
                # A valid record cited without being declared: declaring it is the whole fix.
                issues.append({**row, "problem": "undeclared_" + kind, "candidates": [marker],
                    "fixable": marker in available and (kind == "article" or not evidence_problem(toolkit, marker, article_ids))})
                continue
            candidates = [c for c in dict.fromkeys([*declared, *available]) if c.startswith(marker)]
            pool = toolkit.registry if kind == "article" else toolkit.evidence
            if candidates:
                # Unique among every known identifier, not only the admissible ones: a second
                # record sharing the prefix (another work's proof) makes the completion a guess.
                known = [c for c in pool if c.startswith(marker)]
                issues.append({**row, "problem": "truncated_identifier", "candidates": candidates[:5],
                    "known_matches": len(known), "fixable": len(candidates) == 1 and known == candidates
                    and len(marker.split("_", 1)[1]) >= MIN_PREFIX_CHARS})
                continue
            elsewhere = [c for c in pool if c.startswith(marker)][:3] if len(marker.split("_", 1)[1]) >= 6 else []
            issues.append({**row, "problem": "unknown_identifier", "candidates": elsewhere, "fixable": False})
    for eid in evidence_ids:
        problem = None if eid in cited else evidence_problem(toolkit, eid, article_ids)
        if problem:
            issues.append({"paragraph": None, "marker": eid, "kind": "evidence", "problem": problem, "excerpt": "",
                "fixable": False, **({"article_id": toolkit.evidence[eid]["article_id"]} if eid in toolkit.evidence else {})})
    return issues


def correct_marker_arguments(toolkit, arguments, section_article_ids=()):
    """Complete the identifiers a deterministic check resolves without doubt (a unique,
    long enough prefix of an admissible record; a valid proof or analysed source cited
    without being declared) in the text, its declarations and its claims. Returns a
    corrected copy and the corrections; every check of the save still runs afterwards."""
    issues = marker_issues(toolkit, arguments, section_article_ids)
    fixes = {i["marker"]: i for i in issues if i.get("fixable")}
    if not fixes:
        return arguments, []
    mapping = {marker: issue["candidates"][0] for marker, issue in fixes.items()}
    fixed = copy.deepcopy(arguments)
    fixed["text"] = CITATION.sub(lambda m: "[" + mapping.get(m.group(1), m.group(1)) + "]", fixed["text"])
    for issue in fixes.values():
        key = "article_ids" if issue["kind"] == "article" else "evidence_ids"
        fixed[key] = list(dict.fromkeys([*(mapping.get(e, e) for e in fixed.get(key) or []), issue["candidates"][0]]))
    for claim in fixed.get("claims") or []:
        claim["evidence_ids"] = list(dict.fromkeys(mapping.get(e, e) for e in claim.get("evidence_ids") or []))
    return fixed, [{"paragraph": i["paragraph"], "written": marker, "corrected_to": mapping[marker], "problem": i["problem"]}
        for marker, i in fixes.items()]


def correct_markers(toolkit, args, section_article_ids=()):
    """correct_marker_arguments applied in place to a validated write_section call."""
    claims = [{"evidence_ids": list(c.evidence_ids)} for c in args.claims]
    fixed, corrections = correct_marker_arguments(toolkit, {"text": args.text, "article_ids": list(args.article_ids),
        "evidence_ids": list(args.evidence_ids), "claims": claims}, section_article_ids)
    if corrections:
        args.text, args.article_ids, args.evidence_ids = fixed["text"], fixed["article_ids"], fixed["evidence_ids"]
        for claim, row in zip(args.claims, fixed["claims"]):
            claim.evidence_ids = row["evidence_ids"]
    return corrections


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
        pass
    try:
        markers = marker_issues(toolkit, arguments)
    except Exception:
        markers = []
    for issue in markers:
        key = issue["paragraph"] if issue["paragraph"] is not None else "declarations"
        rows.setdefault(key, []).append({"code": issue["problem"], "marker": issue["marker"],
            **{k: issue[k] for k in ("candidates", "article_id", "excerpt") if issue.get(k)}})
    for claim in (refusal or {}).get("claims_not_in_text", []) or []:
        rows.setdefault("claims", []).append(claim)
    if not rows:
        return None
    located = sorted(k for k in rows if isinstance(k, int))
    return {"paragraphs_total": len(paragraphs), "kept_paragraphs": [i for i in range(len(paragraphs)) if i not in located],
        "paragraphs": [{"index": i, "excerpt": paragraphs[i][:240], "problems": rows[i]} for i in located],
        **({"claims_to_correct": rows["claims"]} if "claims" in rows else {}),
        **({"declarations_to_correct": rows["declarations"]} if "declarations" in rows else {}),
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
