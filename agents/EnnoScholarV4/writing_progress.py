"""Section-by-section progress over V4's existing outline and immutable drafts."""
from __future__ import annotations

import re
import uuid

from .tools.outline import resolve_section


FULL_REQUEST = re.compile(
    r"(?:rédig|redig|écri|ecri|produi|produi).{0,90}(?:tout|complet|intégral|integral|entier).{0,90}"
    r"(?:état\s+de\s+l[’']art|state\s+of\s+(?:the\s+)?art|document)|"
    r"(?:rédig|redig).{0,50}état\s+de\s+l[’']art.{0,50}(?:complet|entier|intégral|integral)", re.I)
PLAN_BEFORE_WRITING = re.compile(r"\bplan\b.{0,100}\bavant\s+de\s+(?:rédig|redig|écri|ecri)|\bne\s+(?:rédige|redige|rédiger|rediger)\s+pas\b", re.I)
SECTION_REQUEST = re.compile(
    r"(?:rédig|redig|approfond|développ|developp|révis|revis|amélior|amelior).{0,45}"
    r"(?:la\s+)?section\s+(\d+|section_[a-f0-9]+)", re.I)
RESUME_REQUEST = re.compile(r"\b(?:reprends|reprendre|poursuis|poursuivre|continue|continuer)\b"
    r".{0,50}(?:rédaction|redaction|sections?|état\s+de\s+l[’']art)", re.I)


def begin_writing(state, request):
    """Open or resume a draft request; never synthesize a human approval."""
    document = state.setdefault("document", {})
    prior = document.get("writing_progress", {})
    section_match = SECTION_REQUEST.search(request)
    full = bool(FULL_REQUEST.search(request)) and not bool(PLAN_BEFORE_WRITING.search(request))
    resume = bool(RESUME_REQUEST.search(request) and prior and prior.get("status") != "draft_complete")
    if not (section_match or full or resume):
        return None
    if resume and not section_match and not full:
        refresh_writing(state)
        if prior["status"] not in {"plan_changed", "draft_complete"}:
            prior["status"] = "running" if prior.get("target_ids") else "awaiting_plan"
        return prior
    outline = state.get("outline", {})
    if section_match:
        section = resolve_section(state, section_match.group(1))
        targets = [section["id"]]
        baseline = {section["id"]: (section.get("drafts") or [{}])[-1].get("id")}
        mode = "section"
    else:
        targets = [section["id"] for section in outline.get("sections", [])]
        baseline = {}
        mode = "full"
    progress = {"id": "writing_" + uuid.uuid4().hex, "mode": mode,
        "target_ids": targets, "plan_revision": outline.get("plan_revision"),
        "baseline_draft_ids": baseline, "saved_draft_ids": {}, "errors": [], "section_status": {},
        "status": "running" if targets else "awaiting_plan",
        "approval_required": not bool(outline.get("approval"))}
    document["writing_progress"] = progress
    refresh_writing(state)
    return progress


def refresh_writing(state):
    progress = state.get("document", {}).get("writing_progress")
    if not progress:
        return None
    outline = state.get("outline", {})
    sections = outline.get("sections", [])
    if progress["mode"] == "full" and not progress["target_ids"] and sections:
        progress["target_ids"] = [section["id"] for section in sections]
        progress["plan_revision"] = outline.get("plan_revision")
        progress["status"] = "running"
    current = {section["id"]: section for section in sections}
    if (progress["target_ids"] and (not set(progress["target_ids"]) <= set(current) or
            progress.get("plan_revision") != outline.get("plan_revision"))):
        progress["status"] = "plan_changed"
        return progress
    saved = {}
    for sid in progress["target_ids"]:
        drafts = current[sid].get("drafts", [])
        if drafts and (progress["mode"] == "full" or drafts[-1]["id"] != progress["baseline_draft_ids"].get(sid)):
            saved[sid] = drafts[-1]["id"]
    progress["saved_draft_ids"] = saved
    progress["section_status"] = {sid: ("saved" if sid in saved else
        "failed" if any(error.get("section_id") == sid for error in progress["errors"][-1:]) else
        "running" if state.get("writing_focus", {}).get("section_id") == sid else "pending")
        for sid in progress["target_ids"]}
    progress["approval_required"] = not bool(outline.get("approval"))
    if progress["target_ids"] and len(saved) == len(progress["target_ids"]):
        progress["status"] = "draft_complete"
    elif progress["status"] not in {"plan_changed", "awaiting_plan"}:
        progress["status"] = "running"
    return progress


def next_section(state):
    progress = refresh_writing(state)
    if not progress or progress["status"] in {"plan_changed", "draft_complete", "awaiting_plan"}:
        return None
    sections = {section["id"]: section for section in state["outline"]["sections"]}
    return next((sections[sid] for sid in progress["target_ids"] if sid not in progress["saved_draft_ids"]), None)


def confirmed_save(state, section_id, draft_id, text):
    progress = state.get("document", {}).get("writing_progress")
    if not progress or section_id not in progress["target_ids"]:
        raise ValueError("Section sauvegardée hors de la demande de rédaction.")
    section = resolve_section(state, section_id)
    current = (section.get("drafts") or [{}])[-1]
    if current.get("id") != draft_id or current.get("text") != text:
        raise ValueError("La sauvegarde confirmée ne correspond pas au brouillon enregistré.")
    refresh_writing(state)
    return progress


def writing_answer(state):
    progress = refresh_writing(state)
    if not progress:
        return None
    sections = {section["id"]: section for section in state.get("outline", {}).get("sections", [])}
    saved = [(sid, sections[sid]) for sid in progress["target_ids"]
        if sid in progress["saved_draft_ids"] and sid in sections]
    if progress["mode"] == "section" and progress["status"] == "draft_complete":
        return f"La section « {saved[0][1]['title']} » a été enregistrée. Sa version actuelle est disponible dans l’artefact."
    header = ("Brouillon complet enregistré" if progress["status"] == "draft_complete" else
        f"Brouillon partiel enregistré : {len(saved)}/{len(progress['target_ids'])} sections.")
    if progress["approval_required"]:
        header += " Plan en attente de validation humaine."
    if progress["status"] == "plan_changed":
        header += " Le plan a changé ; confirmer les sections à reprendre."
    elif progress["status"] != "draft_complete":
        header += " Demandez « continue la rédaction » pour reprendre les sections restantes."
    body = [header]
    if saved:
        body.append("Sections enregistrées : " + ", ".join(section["title"] for _, section in saved) + ". Le manuscrit exact est disponible dans l’artefact.")
    if progress["errors"]:
        body.append("Dernière erreur de sauvegarde : " + progress["errors"][-1]["message"])
    return "\n\n".join(body)
