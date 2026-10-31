"""V5 natural manuscript requests over the unchanged V4 progress machinery."""
import re
from .reuse import load
base = load('_writing_progress_base', 'writing_progress.py')
_begin = base.begin_writing
_resume = base.RESUME_REQUEST
_refresh = base.refresh_writing
_COMPLETION_ACTION = r"\b(?:termine[rz]?|achève[rz]?|acheve[rz]?|finalis(?:e|er|ez))\b"
# « Réécris le contenu de la section 3 » is a targeted drafting request too.
_REWRITE_ACTION = r"\b(?:r[ée][ée]cri\w*|reecri\w*)"
base.RESUME_REQUEST = re.compile(_resume.pattern +
    "|" + _COMPLETION_ACTION + r".{0,90}"
    r"(?:rédaction|redaction|manuscrit|état\s+de\s+l[’']art|sections?)", re.I)
# These verbs must resolve a numbered/ordinal section before the resume branch.
# Keep V4's locators and capture groups; only its separately loaded V5 copy changes.
for _name in ('SECTION_REQUEST', 'ORDINAL_SECTION_REQUEST'):
    _pattern = getattr(base, _name)
    # Non-greedy gap: « Révise la section 2, pas la section 5 » names section 2, not 5.
    setattr(base, _name, re.compile(_pattern.pattern.replace('(?:', '(?:' + _COMPLETION_ACTION + '|' + _REWRITE_ACTION + '|', 1)
        .replace('.{0,45}(', '.{0,45}?('), _pattern.flags))

# An explicit list of sections (« la section 3 puis la section 4 », « les sections 2 et 5 »):
# each named section is revised; none other, and never the whole manuscript.
_REF = r"(\d+|section_[a-f0-9]+)"
_JOIN = (r"(?:\s*,\s*(?:puis\s+|et\s+)?|\s+(?:et|puis|ainsi\s+que)\s+)(?:(?:ensuite|aussi)\s+)?"
    r"(?:(?:la|le)\s+)?(?:section\s+)?")
_SECTION_LIST = re.compile(rf"\bsections?\s+{_REF}(?:{_JOIN}{_REF})+", re.I)
_LIST_ACTION = re.compile(r"r[ée]dig|redig|r[ée][ée]cri|reecri|r[ée]vis|revis|am[ée]lior|corrig|approfond|d[ée]velopp|"
    r"termin|ach[eè]v|finalis|compl[eè]t|reprend|modifi", re.I)


def section_list_requested(request):
    return bool(_SECTION_LIST.search(request or '') and _LIST_ACTION.search(request or ''))


def listed_sections(state, request):
    """Plan ids of an explicit list of sections, [] when absent or when one is unknown."""
    if not section_list_requested(request):
        return []
    ids = []
    for ref in re.findall(_REF, _SECTION_LIST.search(request).group(0)):
        try:
            sid = base.resolve_section(state, ref)['id']
        except ValueError:
            return []  # Never guess an unknown section.
        if sid not in ids:
            ids.append(sid)
    return ids if len(ids) > 1 else []


_requested_section = base.requested_section
_multi_section = base.multi_section_requested


def requested_section(state, request):
    """The single section a request names (V4), None for an explicit list of sections."""
    return None if section_list_requested(request) else _requested_section(state, request)


def multi_section_requested(request):
    return bool(_multi_section(request) or section_list_requested(request))


base.requested_section = requested_section
base.multi_section_requested = multi_section_requested

# A request about the plan itself (a section's title, place, sources, the plan's
# structure) is a plan edit, not a content revision: « Modifie le titre de la section 3
# dans le plan » must not open the drafting of section 3, whose scope forbids any plan change.
_SECTION_WORD = r"(?:une|la|les|un|cette|ces|deux|trois)?\s*(?:nouvelle\s+|derni[eè]re\s+|premi[eè]re\s+)?(?:sections?|parties?)"
_PLAN_MARKER = re.compile(r"\b(?:titres?|intitul\w*|renomm\w*|dans\s+le\s+plan|du\s+plan|au\s+plan|structure\s+du\s+plan|"
    r"ordre\s+des\s+sections|d[ée]plac\w*|r[ée]ordonn\w*|fusionn\w*|scind\w*|invers\w*\s+(?:les\s+)?sections|permut\w*|"
    rf"(?:ajout\w*|ins[ée]r\w*|supprim\w*|retir\w*|enl[eè]v\w*)\s+{_SECTION_WORD}|"
    r"(?:supprim|retir|enl[eè]v|dissoci|associ|ajout|rattach)\w*\s+(?:l['’]\s*(?:article|étude|etude)|la\s+publication|les\s+(?:articles|publications))|"
    r"remplac\w*\s+(?:la\s+)?section\s+\S+\s+par\s+(?:une|un)\b|"
    r"(?:modifi|chang|r[ée]vis|revoi|refai|restructur|r[ée]organis)\w*\s+(?:le|ce|mon|notre)\s+plan)\b", re.I)
_PLAN_ACTION = re.compile(r"\b(?:modifi|chang|renomm|corrig|remplac|supprim|retir|ajout|ins[eè]r|ins[ée]r|d[ée]plac|invers|"
    r"permut|fusionn|scind|r[ée]ordonn|r[ée]organis|restructur|revoi|refai|r[ée]vis|mets|mettre|met|place|associ|dissoci|"
    r"enl[eè]v|r[ée]duis|reformul|ajust)\w*", re.I)
_CONTENT_MARKER = re.compile(r"\b(?:contenus?|textes?|r[ée]dig\w*|redig\w*|r[ée][ée]cri\w*|reecri\w*|paragraphes?|"
    r"brouillons?|r[ée]daction|redaction|prose|d[ée]velopp\w*|approfond\w*|argumentation|am[ée]lior\w*|enrichi\w*|"
    r"termin\w*|finalis\w*|ach[eè]v\w*)\b", re.I)
# « Ne modifie pas le plan », « sans modifier le plan » protect the plan; they never request an edit.
_NEGATED_PLAN = re.compile(r"(?:\bsans|\bne|\bn['’])\s*(?:pas\s+)?(?:modifi|chang|touch)\w*\s+(?:pas\s+|rien\s+)?"
    r"(?:à\s+|au\s+|a\s+)?(?:le\s+|du\s+|mon\s+|ce\s+|notre\s+|l['’]\s*)?plan\b", re.I)
_REMOVAL = re.compile(r"\b(?:supprim|retir|enl[eè]v|dissoci|r[ée]duis)\w*|\b(?:au\s+)?maximum\b|\bau\s+plus\b|\bmax\b", re.I)
_ADDITION = re.compile(r"\b(?:ajout|ins[eè]r|ins[ée]r)\w*", re.I)
_MERGE = re.compile(r"\b(?:fusionn|regroup|r[ée]uni)\w*", re.I)
_MOVE = re.compile(r"\b(?:d[ée]plac|invers|permut|r[ée]ordonn|remont|descend)\w*|\bordre\b|\bposition\b|"
    r"\b(?:place|mets|mettre|met)\b.{0,40}\b(?:avant|apr[eè]s|premi[eè]re?|derni[eè]re?|d[ée]but|fin)\b", re.I)
_SOURCES = re.compile(r"\b(?:associ|dissoci)\w*|\b(?:articles?|publications?|[ée]tudes?|sources?|paper_[a-f0-9]+)\b", re.I)
_ANY_SECTION = re.compile(r"\bsection\s+(\d+|section_[a-f0-9]+)\b", re.I)
_ANY_ORDINAL = re.compile(r"\b(?:la|le)\s+(premi[eè]re|1(?:re|ère|ere)|deuxi[eè]me|seconde?|2(?:e|ème)|troisi[eè]me|3(?:e|ème)|"
    r"quatri[eè]me|4e|cinqui[eè]me|5e|sixi[eè]me|6e|septi[eè]me|7e|huiti[eè]me|8e|neuvi[eè]me|9e|dixi[eè]me|10e|"
    r"derni[eè]re)\s+section\b", re.I)


def _plan_edit_only(state, request):
    request = request or ''
    return bool(state.get('outline', {}).get('sections') and _PLAN_MARKER.search(request) and _PLAN_ACTION.search(request)
        and not _CONTENT_MARKER.search(request) and not _NEGATED_PLAN.search(request))


# « Ajoute une section sur les limites puis rédige l'état de l'art complet » : the plan
# edit first, then (after the consultant validates the changed plan) the drafting asked.
_THEN = re.compile(r"\s*,?\s*\b(?:puis|ensuite|et\s+ensuite|et\s+apr[eè]s|apr[eè]s\s+quoi|avant\s+de)\b\s*|\s*[.;]\s+", re.I)
_DRAFT_CLAUSE = re.compile(r"\b(?:r[ée]dig|redig|[ée]cri[rsv]|ecri[rsv])\w*", re.I)
_PRONOUN_DRAFT = re.compile(r"\b(?:r[ée]dig|redig|[ée]cri)\w*[-\s](?:la|les|le)\b(?!\s*(?:section|partie|état|etat|manuscrit|document|plan))|"
    r"\b(?:la|les|le)\s+(?:r[ée]diger|rediger|[ée]crire|ecrire)\b", re.I)


def combined_request(state, request):
    """(plan clause, drafting clause) of a plan edit followed by a drafting order, or None."""
    if not state.get('outline', {}).get('sections'):
        return None
    for match in _THEN.finditer(request or ''):
        plan, draft = request[:match.start()], request[match.end():]
        if _plan_edit_only(state, plan) and _DRAFT_CLAUSE.search(draft):
            return plan, draft
    return None


def plan_edit_text(state, request):
    """The words of the request that edit the plan (all of it, or its plan clause), or None."""
    if _plan_edit_only(state, request):
        return request
    combined = combined_request(state, request)
    return combined[0] if combined else None


def plan_edit_request(state, request):
    """The request changes the plan (title, order, sections, sources); it drafts nothing now."""
    return plan_edit_text(state, request) is not None


def _approved(state):
    outline = state.get('outline') or {}
    approval = outline.get('approval') or {}
    return bool(approval) and approval.get('plan_revision', outline.get('plan_revision')) == outline.get(
        'plan_revision', approval.get('plan_revision'))


def pending_draft(state):
    """The drafting a combined request asked for, kept until the changed plan is validated."""
    return state.get('v5_pending_draft') or None


def plan_edit_kind(request):
    """What a plan edit request explicitly allows besides editing the named section."""
    request = request or ''
    return {'removal': bool(_REMOVAL.search(request)), 'addition': bool(_ADDITION.search(request)),
        'merge': bool(_MERGE.search(request)), 'move': bool(_MOVE.search(request)), 'sources': bool(_SOURCES.search(request))}


def plan_edit_actions(request):
    """manage_outline actions a targeted plan edit allows on its section (always rename/update)."""
    kind = plan_edit_kind(request)
    return ({'rename', 'update'} | ({'move'} if kind['move'] else set()) | ({'associate', 'dissociate'} if kind['sources'] else set())
        | ({'delete'} if kind['removal'] and not kind['sources'] else set()) | ({'add'} if kind['addition'] and not kind['sources'] else set())
        | ({'merge'} if kind['merge'] else set()))


def plan_edit_target(state, request):
    """The single plan section a plan edit names, or None (whole-plan edit)."""
    request = plan_edit_text(state, request)
    if request is None:
        return None
    sections = state.get('outline', {}).get('sections', [])
    if len({m.casefold() for m in _ANY_SECTION.findall(request)}) > 1:
        return None  # Several sections named: a whole-plan edit, each change checked by V4.
    match = _ANY_SECTION.search(request)
    if match:
        try:
            return base.resolve_section(state, match.group(1))['id']
        except ValueError:
            return None
    match = _ANY_ORDINAL.search(request)
    if match:
        word = match.group(1).casefold()
        position = next((v for k, v in base.ORDINAL_WORDS.items() if word.startswith(k)), None)
        position = len(sections) if position == -1 else position
        if position and 1 <= position <= len(sections):
            return sections[position-1]['id']
    return None


_check_scope = base.check_write_scope


def check_write_scope(state, section_id=None, *, plan_change=False):
    """A plan edit request changes the plan, never a section's text."""
    from .instructions import current_request
    request = (current_request(state) or {}).get('content', '')
    if plan_edit_request(state, request):
        if not plan_change:
            raise ValueError("Demande de modification du plan : aucune section n'est rédigée pour cette demande.")
        # The section-revision rule (« le plan doit rester inchangé ») does not apply;
        # the other scope checks do. The edit is confined to the named section by the
        # runtime and by V4's plan_operation for a plan revision.
        return _check_scope(state, section_id, plan_change=False)
    return _check_scope(state, section_id, plan_change=plan_change)


base.check_write_scope = check_write_scope


def writing_request_blocked(state, request):
    """A negation, an unresolved section or a plan edit never opens drafting."""
    if plan_edit_request(state, request):
        return True
    if re.search(r"\b(?:ne\s+(?:termine|finalise|continue|rédige|redige|achève|acheve)\s+pas|sans\s+(?:terminer|finaliser|rédiger|rediger))\b", request, re.I):
        return True
    single = base.requested_section(state, request)
    multi = base.multi_section_requested(request)
    # A later mention of saved sections does not narrow an explicit manuscript request.
    scope = re.search(r"\b(?:sections?|manuscrit|document|état\s+de\s+l[’']art|state\s+of\s+(?:the\s+)?art)\b", request, re.I)
    if scope and scope.group().casefold().startswith('section') and not single and not multi:
        return True
    return bool(re.search(_COMPLETION_ACTION, request, re.I) and not single and not multi
        and not base.RESUME_REQUEST.search(request))


def reopen_after_plan_change(state, prior):
    """The same drafting scope over the plan as it is now (the plan changed since it opened).

    V4 reports such a progress as plan_changed and never drafts it again: « Continue la
    rédaction » after a plan edit, even revalidated, drafted nothing. The sections the prior
    progress covered are kept when they still exist; a full draft of the whole plan covers
    the whole current plan (saved sections stay saved). None when nothing remains."""
    outline = state.get('outline', {})
    current = [s['id'] for s in outline.get('sections', [])]
    targets = [sid for sid in current if sid in prior.get('target_ids', [])]
    if prior.get('mode') == 'full':
        old = next((v for v in reversed(state.get('outline_versions', []))
            if v.get('plan_revision', v.get('version')) == prior.get('plan_revision')), None)
        if old is None or set(prior.get('target_ids', [])) >= {s['id'] for s in old.get('sections', [])}:
            targets = current
        return base.open_writing(state, 'full', targets, {}) if targets else None
    if not targets:
        return None
    return base.open_writing(state, prior.get('mode', 'section'), targets,
        {sid: prior.get('baseline_draft_ids', {}).get(sid) for sid in targets})


def _resume_like(request):
    return bool(base.RESUME_REQUEST.search(request or '') or base.SHORT_GO_AHEAD.search(request or ''))


def open_pending(state, pending):
    """The drafting a combined request asked for, over the plan as validated now."""
    clause = pending.get('clause', '')
    current = [s['id'] for s in state.get('outline', {}).get('sections', [])]
    if _PRONOUN_DRAFT.search(clause):
        # « … puis rédige-la » : the section(s) the plan edit added, else the section it named.
        targets = [sid for sid in current if sid not in pending.get('plan_ids', [])] or [
            sid for sid in [pending.get('target')] if sid in current]
        if not targets:
            return None
        sections = {s['id']: s for s in state['outline']['sections']}
        return base.open_writing(state, 'section', targets, {sid: (sections[sid].get('drafts') or [{}])[-1].get('id')
            for sid in targets})
    return begin_writing(state, clause)


def begin_writing(state, request):
    from .instructions import current_request
    request_id = (current_request(state) or {}).get('id')
    combined = combined_request(state, request)
    if combined:
        # Plan first: nothing is drafted (no mission) before the changed plan is validated;
        # the drafting asked is kept and resumed by « Continue la rédaction » after validation.
        state['v5_pending_draft'] = {'request_id': request_id, 'clause': combined[1].strip()[:600],
            'plan_ids': [s['id'] for s in state['outline']['sections']], 'target': plan_edit_target(state, request),
            'plan_revision': state['outline'].get('plan_revision')}
        return None
    pending = pending_draft(state)
    if pending and pending.get('request_id') != request_id and _resume_like(request):
        if not _approved(state):
            return None  # The changed plan awaits the consultant's validation: still nothing drafted.
        state.pop('v5_pending_draft', None)
        progress = open_pending(state, pending)
        if progress is not None:
            progress['request_message_id'] = request_id
            if progress.get('mode') == 'full':
                progress['include_conclusion'] = True
            refresh_writing(state)
        return progress
    progress = _begin_request(state, request)
    if progress is not None:
        state.pop('v5_pending_draft', None)  # A new drafting request replaces the earlier intention.
    return progress


def _begin_request(state, request):
    if writing_request_blocked(state, request):
        return None
    if section_list_requested(request):
        # « Rédige la section 3 puis la section 4 » : exactly these sections, each a
        # targeted revision of its current version; an unknown one opens nothing.
        ids = listed_sections(state, request)
        if not ids:
            return None
        sections = {s['id']: s for s in state.get('outline', {}).get('sections', [])}
        progress = base.open_writing(state, 'section', ids, {sid: (sections[sid].get('drafts') or [{}])[-1].get('id')
            for sid in ids})
        refresh_writing(state)
        return progress
    progress = _begin(state, request)
    if progress is not None and progress.get('status') == 'plan_changed':
        progress = reopen_after_plan_change(state, progress)
        if progress is None:
            return None  # Its sections left the plan: nothing is widened to the whole manuscript.
    # A completion request can also start from a plan with drafts but no ledger.
    if progress is None and base.RESUME_REQUEST.search(request) and state.get('outline', {}).get('sections'):
        if not base.PLAN_BEFORE_WRITING.search(request):
            progress = base.open_writing(state)
    if progress is not None:
        from .instructions import current_request
        progress['request_message_id'] = (current_request(state) or {}).get('id')
        if progress.get('mode') == 'full':
            progress['include_conclusion'] = True
        refresh_writing(state)
    return progress


def conclusion_pending(state):
    p = state.get('document', {}).get('writing_progress') or {}
    if not p.get('include_conclusion') or not p.get('target_ids'):
        return False
    sec = next((s for s in state.get('outline', {}).get('sections', []) if s['id']==p['target_ids'][-1]), {})
    draft = (sec.get('drafts') or [{}])[-1]
    return not re.search(r'^#{1,3}\s+Conclusion générale\s*\n\s*\S', draft.get('raw_text', ''), re.M|re.I)


def refresh_writing(state, *, final=False):
    p = _refresh(state, final=final)
    if p and p.get('status') != 'plan_changed' and conclusion_pending(state):
        last = p['target_ids'][-1]
        if last in p.get('saved_draft_ids', {}):
            p['saved_draft_ids'].pop(last)
            p['section_status'][last] = 'pending_conclusion'
            p['status'] = 'partial' if final else 'running'
    return p


_answer = base.writing_answer


def writing_answer(state, stop_reason=None):
    """V4's answer; a revision of several listed sections names each of them."""
    progress = refresh_writing(state)
    if not progress or progress.get('mode') != 'section' or len(progress.get('target_ids', [])) < 2:
        return _answer(state, stop_reason)
    titles = {s['id']: s['title'] for s in state.get('outline', {}).get('sections', [])}
    saved = [titles[sid] for sid in progress['target_ids'] if sid in progress.get('saved_draft_ids', {}) and sid in titles]
    pending = [titles[sid] for sid in progress['target_ids'] if sid not in progress.get('saved_draft_ids', {}) and sid in titles]
    text = (f"Sections mises à jour : {', '.join(f'« {t} »' for t in saved)}." if saved else "Aucune des sections demandées n’a été enregistrée.")
    if pending:
        text += f" Sections non enregistrées : {', '.join(f'« {t} »' for t in pending)}. Les versions précédentes sont conservées."
    if stop_reason and pending:
        text += f" Arrêt : {base.STOP_LABELS.get(stop_reason, stop_reason)}."
    return text


base.begin_writing = begin_writing
base.refresh_writing = refresh_writing
base.writing_answer = writing_answer
for name in dir(base):
    if not name.startswith('_'):
        globals()[name] = getattr(base, name)
