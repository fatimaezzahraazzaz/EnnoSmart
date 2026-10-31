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
    r"(?:rédig|redig|approfond|développ|developp|révis|revis|amélior|amelior|modifi|remplac|corrig).{0,45}"
    r"(?:la\s+)?section\s+(\d+|section_[a-f0-9]+)", re.I)
# « la première section », « la deuxième section », « la dernière section » (run dcf5d05b:
# « rédiger la première section » opened a draft of all eleven sections).
ORDINAL_WORDS = {"premi": 1, "1re": 1, "1ère": 1, "1ere": 1, "deuxi": 2, "second": 2, "2e": 2, "2ème": 2, "troisi": 3, "3e": 3,
    "3ème": 3, "quatri": 4, "4e": 4, "cinqui": 5, "5e": 5, "sixi": 6, "6e": 6, "septi": 7, "7e": 7, "huiti": 8, "8e": 8,
    "neuvi": 9, "9e": 9, "dixi": 10, "10e": 10, "derni": -1}
ORDINAL_SECTION_REQUEST = re.compile(
    r"(?:rédig|redig|écri|ecri|approfond|développ|developp|révis|revis|amélior|amelior|produi|commenc).{0,60}?"
    r"\b(?:la|le)\s+(premi[eè]re|1(?:re|ère|ere)|deuxi[eè]me|seconde?|2(?:e|ème)|troisi[eè]me|3(?:e|ème)|quatri[eè]me|4e|"
    r"cinqui[eè]me|5e|sixi[eè]me|6e|septi[eè]me|7e|huiti[eè]me|8e|neuvi[eè]me|9e|dixi[eè]me|10e|derni[eè]re)\s+(?:sous-)?section\b",
    re.I)


def requested_section(state, request):
    """The single plan section a request names (« la section 3 », « la première section »,
    « la dernière section »), or None."""
    sections = state.get("outline", {}).get("sections", [])
    if not sections:
        return None
    match = SECTION_REQUEST.search(request)
    if match:
        try:
            return resolve_section(state, match.group(1))["id"]
        except ValueError:
            return None
    match = ORDINAL_SECTION_REQUEST.search(request)
    if not match:
        return None
    word = match.group(1).casefold()
    position = next((value for key, value in ORDINAL_WORDS.items() if word.startswith(key)), None)
    if position is None:
        return None
    position = len(sections) if position == -1 else position
    return sections[position-1]["id"] if 1 <= position <= len(sections) else None
SHORT_GO_AHEAD = re.compile(r"^\W*(?:(?:maintenant|ok|okay|bien|parfait|d['’]accord|c['’]est\s+bon|vas-y|allez)\W+)*"
    r"(?:tu\s+peux\s+|on\s+peut\s+|peux-tu\s+)?(?:rédig|redig)\w*(?:\s+(?:le|l['’]|tout|maintenant|le\s+document|l['’]état\s+de\s+l['’]art))*\W*$", re.I)
RESUME_REQUEST = re.compile(r"\b(?:reprends|reprendre|poursuis|poursuivre|continue|continuer)\b"
    r".{0,50}(?:rédaction|redaction|sections?|état\s+de\s+l[’']art)", re.I)
# Several sections requested at once: section numbers quoted in such a request
# are bounds ("reprends à la section 2 … jusqu'à la section 7"), not a single target.
SECTION_ID = r"(\d+|section_[a-f0-9]+)"
UNTIL = r"jusqu['’]\s*(?:à|a)"
MULTI_SECTION_REQUEST = re.compile(
    r"\btoutes\s+les\s+(?:autres\s+)?sections\b|\b(?:les\s+)?(?:autres\s+)?sections\s+(?:restantes|suivantes|manquantes|non\s+(?:rédigées|redigees|enregistrées|enregistrees))\b|"
    r"\bles\s+autres\s+sections\b|\bchaque\s+section\b|\btout\s+le\s+reste\b|\bà\s+partir\s+de\s+la\s+section\b|"
    rf"\b{UNTIL}\s+la\s+(?:dernière\s+|derniere\s+)?section\b|\bsections\s+\d+\s*(?:à|a|-|–|au|{UNTIL})\s*\d+\b|"
    r"\b(?:all|remaining)\s+(?:the\s+)?(?:remaining\s+)?sections\b", re.I)
WRITING_ACTION = re.compile(r"rédig|redig|rédac|redac|écri|ecri|reprend|poursui|continu|termin|achève|acheve|finalis|write", re.I)
SINGLE_SECTION_ONLY = re.compile(r"\b(?:uniquement|seulement|only)\b.{0,30}\bsection\s+\S+|\bseule\s+section\b|"
    r"\bsection\s+\S+\s+(?:uniquement|seulement)\b", re.I)
SECTION_RANGE = re.compile(rf"\bsections\s+{SECTION_ID}\s*(?:à|a|-|–|au|{UNTIL})\s*(?:la\s+(?:section\s+)?)?{SECTION_ID}", re.I)
SECTION_START = re.compile(rf"(?:à\s+partir\s+de|(?:reprend|recommenc)\w*\s+(?:à|a|depuis|dès|des)|depuis)\s+la\s+section\s+{SECTION_ID}", re.I)
SECTION_END = re.compile(rf"{UNTIL}\s+la\s+section\s+{SECTION_ID}", re.I)


def multi_section_requested(request):
    """Require a positive writing action in the same clause as its plural target.

    A mention of later sections, or the participle « déjà rédigée », is not an
    instruction to write them. Keep explicit ranges and remaining-section requests.
    """
    action = re.compile(r"\b(?:rédig(?:e|er|ez|eons)|redig(?:e|er|ez|eons)|écri(?:s|re|vez)|"
        r"ecri(?:s|re|vez)|reprends?|reprendre|poursuis|poursuivre|continue[rz]?|termin(?:e|er|ez)|"
        r"achève[rz]?|acheve[rz]?|finalis(?:e|er|ez)|write)\b", re.I)
    for clause in re.split(r"[.!?;\n]|,\s*(?=sans\b|ne\b)", request):
        for match in action.finditer(clause):
            prefix = clause[:match.start()]
            if re.search(r"\b(?:sans|ne|pas|éviter\s+de|eviter\s+de)\s+(?:\w+\s+){0,3}$", prefix, re.I):
                continue
            if re.search(r"\b(?:avant\s+de|afin\s+de|pour(?:\s+\w+){0,4}|puissions|puisse|pourrons|pourra|seront)\s*$", prefix, re.I):
                continue  # A future purpose/condition is not an additional writing order.
            tail = clause[match.end():]
            if re.match(r"\s+(?:pas|plus|jamais)\b", tail, re.I):
                continue
            if MULTI_SECTION_REQUEST.search(tail):
                return True
    return False


def requested_range(state, request):
    """Plan sections bounded by the request ("à partir de la section 2", "sections 3 à 5"); all by default."""
    sections = state.get("outline", {}).get("sections", [])
    if not sections:
        return []
    ids = [section["id"] for section in sections]
    span = SECTION_RANGE.search(request)
    bounds = span.groups() if span else ((SECTION_START.search(request) or [None, None])[1],
        (SECTION_END.search(request) or [None, None])[1])
    try:
        start = ids.index(resolve_section(state, bounds[0])["id"]) if bounds[0] else 0
        end = ids.index(resolve_section(state, bounds[1])["id"]) if bounds[1] else len(ids)-1
    except ValueError:
        return ids
    return ids[start:end+1] if start <= end else ids


def begin_writing(state, request):
    """Open or resume a draft request; never synthesize a human approval."""
    document = state.setdefault("document", {})
    prior = document.get("writing_progress", {})
    single = requested_section(state, request)
    section_match = bool(single)
    multi = bool(multi_section_requested(request) and
        not PLAN_BEFORE_WRITING.search(request) and not SINGLE_SECTION_ONLY.search(request))
    if multi:
        section_match = False
    full = multi or bool(FULL_REQUEST.search(request)) and not bool(PLAN_BEFORE_WRITING.search(request))
    # A short go-ahead (« Maintenant rédige », « OK, tu peux rédiger ») once a plan
    # exists starts drafting from the saved cards, without another validation.
    full = full or bool(state.get("outline", {}).get("sections") and len(request) <= 160 and
        SHORT_GO_AHEAD.search(request) and not PLAN_BEFORE_WRITING.search(request) and not section_match)
    resume = bool(RESUME_REQUEST.search(request) and prior and prior.get("status") != "draft_complete")
    if not (section_match or full or resume):
        return None
    if resume and not section_match and not full:
        # A new request retries the sections set aside by the previous run.
        prior.pop("blocked", None)
        refresh_writing(state)
        if prior["status"] not in {"plan_changed", "draft_complete"}:
            prior["status"] = "running" if prior.get("target_ids") else "awaiting_plan"
        return prior
    outline = state.get("outline", {})
    if section_match:
        section = resolve_section(state, single)
        targets = [section["id"]]
        baseline = {section["id"]: (section.get("drafts") or [{}])[-1].get("id")}
        mode = "section"
    else:
        # Full mode: sections already drafted count as saved, so drafting resumes
        # at the first unsaved section of the requested range.
        targets = requested_range(state, request) if multi else [section["id"] for section in outline.get("sections", [])]
        baseline = {}
        mode = "full"
    return open_writing(state, mode, targets, baseline)


def resume_writing(state, segment):
    """A continuation of an automatic drafting job resumes the progress the job pinned, by
    its id, unchanged: same targets, same plan revision (a changed plan is reported as
    plan_changed, never accepted), and the sections the job set aside stay blocked. None
    when that progress no longer exists (the run then drafts nothing)."""
    progress = state.get("document", {}).get("writing_progress")
    if not progress or not segment or progress.get("id") != segment.get("progress_id"):
        return None
    progress["blocked"] = {**progress.get("blocked", {}), **(segment.get("blocked") or {})}
    progress = refresh_writing(state)
    if progress["status"] not in {"plan_changed", "draft_complete", "awaiting_plan"}:
        progress["status"] = "running"
    return progress


def open_writing(state, mode="full", targets=None, baseline=None):
    """A new drafting progress (full draft by default: every plan section, or the
    plan first when none exists). Used for explicit requests, never for a plan request."""
    outline = state.get("outline", {})
    targets = [section["id"] for section in outline.get("sections", [])] if targets is None else targets
    progress = {"id": "writing_" + uuid.uuid4().hex, "mode": mode,
        "target_ids": targets, "plan_revision": outline.get("plan_revision"),
        "baseline_draft_ids": baseline or {}, "saved_draft_ids": {}, "errors": [], "section_status": {},
        "status": "running" if targets else "awaiting_plan",
        "approval_required": not bool(outline.get("approval"))}
    from .instructions import current_request
    progress["request_message_id"] = (current_request(state) or {}).get("id")
    state.setdefault("document", {})["writing_progress"] = progress
    refresh_writing(state)
    return progress


def check_write_scope(state, section_id=None, *, plan_change=False):
    """Check the active request and a job's pinned scope before any manuscript write."""
    progress = state.get("document", {}).get("writing_progress") or {}
    from .instructions import current_request
    current = current_request(state) or {}
    request = current.get("content", "")
    target = requested_section(state, request)
    if target and (not multi_section_requested(request) or SINGLE_SECTION_ONLY.search(request)):
        if plan_change:
            raise ValueError("Révision ciblée : le plan doit rester inchangé.")
        if section_id is not None and section_id != target:
            raise ValueError("Écriture hors du périmètre de la section explicitement demandée.")
    if not progress:
        return
    if progress.get("request_message_id") and current.get("id") != progress["request_message_id"]:
        return  # An old completed progress does not authorize/restrict a later request.
    segment = state.get("writing_segment")
    if segment:
        from .writing_job import scope_problem
        problem = scope_problem(segment, state)
        if problem:
            raise ValueError(problem)
    if progress.get("plan_revision") != state.get("outline", {}).get("plan_revision"):
        raise ValueError("Le plan a changé ; la portée de rédaction doit être confirmée.")
    if plan_change and progress.get("mode") == "section":
        raise ValueError("Révision ciblée : le plan et les autres sections doivent rester inchangés.")
    if section_id is not None and section_id not in progress.get("target_ids", []):
        raise ValueError("Écriture hors du périmètre des sections demandées.")


def refresh_writing(state, *, final=False):
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
        "blocked" if sid in progress.get("blocked", {}) else
        "failed" if any(error.get("section_id") == sid for error in progress["errors"][-1:]) else
        "running" if state.get("writing_focus", {}).get("section_id") == sid else "pending")
        for sid in progress["target_ids"]}
    progress["approval_required"] = not bool(outline.get("approval"))
    if progress["target_ids"] and len(saved) == len(progress["target_ids"]):
        progress["status"] = "draft_complete"
    elif progress["status"] not in {"plan_changed", "awaiting_plan"}:
        progress["status"] = "partial" if final else "running"
    return progress


def next_section(state):
    progress = refresh_writing(state)
    if not progress or progress["status"] in {"plan_changed", "draft_complete", "awaiting_plan"}:
        return None
    sections = {section["id"]: section for section in state["outline"]["sections"]}
    blocked = progress.get("blocked", {})
    return next((sections[sid] for sid in progress["target_ids"]
        if sid not in progress["saved_draft_ids"] and sid not in blocked), None)


def confirmed_save(state, section_id, draft_id, text):
    progress = state.get("document", {}).get("writing_progress")
    if not progress or section_id not in progress["target_ids"]:
        raise ValueError("Section sauvegardée hors de la demande de rédaction.")
    section = resolve_section(state, section_id)
    current = (section.get("drafts") or [{}])[-1]
    if current.get("id") != draft_id or current.get("text") != text:
        raise ValueError("La sauvegarde confirmée ne correspond pas au brouillon enregistré.")
    if progress["mode"] == "section" and draft_id == progress.get("baseline_draft_ids", {}).get(section_id):
        raise ValueError("La révision exige une nouvelle version de la section demandée.")
    refresh_writing(state)
    return progress


STOP_LABELS = {"cancelled": "interruption demandée", "timeout": "limite de durée atteinte",
    "exploration_round_budget": "limite de tours atteinte", "exploration_tool_budget": "limite d’appels d’outils atteinte",
    "tool_budget": "limite d’appels d’outils atteinte", "exploration_time_budget": "limite de durée atteinte",
    "exploration_token_budget": "limite de tokens atteinte", "token_budget": "limite de tokens atteinte",
    "exploration_cost_budget": "limite de coût atteinte", "cost_budget": "limite de coût atteinte",
    "context_budget": "limite de contexte atteinte",
    "permission_conflict": "conflit entre l'objectif de rédaction et les permissions du consultant",
    "tool_configuration_error": "aucun outil compatible avec l'étape demandée",
    "internal_error": "incident logiciel interne ; diagnostic technique enregistré",
    "upstream_error": "incident du fournisseur ou du transport API",
    "configuration_error": "requête ou configuration refusée par le fournisseur",
    "section_save_failed": "sauvegarde refusée après plusieurs corrections de la même section",
    "section_blocked": "toutes les autres sections sont enregistrées ; une section reste à débloquer"}


def writing_answer(state, stop_reason=None):
    progress = refresh_writing(state)
    if not progress:
        return None
    sections = {section["id"]: section for section in state.get("outline", {}).get("sections", [])}
    saved = [(sid, sections[sid]) for sid in progress["target_ids"]
        if sid in progress["saved_draft_ids"] and sid in sections]
    if progress["mode"] == "section" and progress["status"] == "draft_complete":
        action = "mise à jour" if progress.get("baseline_draft_ids", {}).get(saved[0][0]) else "enregistrée"
        return f"La section « {saved[0][1]['title']} » a été {action}. Sa version actuelle est disponible dans l’artefact."
    if progress["mode"] == "section":
        title = sections[progress["target_ids"][0]]["title"]
        previous = " La version précédente est conservée." if any(progress.get("baseline_draft_ids", {}).values()) else ""
        return f"La rédaction de la section « {title} » n’a pas été enregistrée." + previous
    # Drafts requested by the consultant are saved without an intermediate approval;
    # the approval status stays in the data (approval_required) for the final
    # human validation and is not repeated in every progress message.
    header = (f"Brouillon complet enregistré : {len(saved)}/{len(progress['target_ids'])} sections. "
        "L’approbation finale du document reste à faire par le consultant." if progress["status"] == "draft_complete" else
        f"Brouillon partiel enregistré : {len(saved)}/{len(progress['target_ids'])} sections.")
    if progress["status"] == "draft_complete":
        # Every section saved is a structural fact, not a scientific verdict: the coverage
        # and the review are reported separately (editorial.document_quality).
        from .editorial import document_quality
        quality = document_quality(state)
        header += (" Couverture scientifique : " + ("suffisante selon la dernière revue de couverture" if
            quality["bibliography_sufficient"] else "non établie (" + (f"{len(quality['planned_articles_without_analysis'])} "
            "publications du plan sans fiche analysée" if quality["planned_articles_without_analysis"] else
            "aucune revue de couverture suffisante enregistrée") + ")") + " ; revue scientifique du manuscrit : " +
            ("à jour." if quality["scientific_review_current"] else "à refaire sur cette version."))
    if progress["status"] == "plan_changed":
        header += " Le plan a changé ; confirmer les sections à reprendre."
    elif progress["status"] != "draft_complete":
        if stop_reason:
            header += f" Arrêt : {STOP_LABELS.get(stop_reason, stop_reason)}."
        job = state.get("writing_job") or {}
        if job.get("status") in {"running", "waiting"} and stop_reason != "cancelled":
            header += " La rédaction se poursuit automatiquement avec les sections restantes ; les sections enregistrées sont conservées."
        else:
            header += " Demandez « continue la rédaction » pour reprendre les sections restantes ; les sections enregistrées sont conservées."
    body = [header]
    if saved:
        body.append("Sections enregistrées : " + ", ".join(section["title"] for _, section in saved) + ". Le manuscrit exact est disponible dans l’artefact.")
    blocked = [(sid, sections[sid]) for sid in progress["target_ids"] if sid in progress.get("blocked", {}) and sid in sections]
    for sid, section in blocked:
        body.append(f"Section non enregistrée : « {section['title']} ». Cause : {progress['blocked'][sid]} "
            "Son contenu n’a pas été validé ; les autres sections ont été poursuivies.")
    unresolved = [e for e in progress["errors"] if e.get("section_id") not in progress["saved_draft_ids"]]
    if unresolved and not blocked and progress["status"] != "draft_complete":
        body.append("Dernière erreur de sauvegarde : " + unresolved[-1]["message"])
    return "\n\n".join(body)
