"""Local outcomes of verified administrative writes. Never validates scientific prose."""
import copy
import re

from .instructions import current_request
from .writing_progress import plan_edit_text, plan_edit_target, plan_edit_kind


def edit_snapshot(state, decision):
    if decision.tool != 'manage_outline' or decision.arguments.get('action') == 'read':
        return None
    request = current_request(state) or {}
    text = plan_edit_text(state, request.get('content', ''))
    if text is None:
        return None
    return {'request_id': request.get('id'), 'text': text, 'target': plan_edit_target(state, request.get('content', '')),
        'outline': copy.deepcopy(state.get('outline') or {})}


def publish(state, answer, *, outcome, complete=False):
    state['last_response'] = {'answer': answer, 'citations': [], 'coverage_sufficient': False,
        'outcome': outcome, 'partial': not complete}
    return {'published': True, 'administrative_complete': complete, 'outcome': outcome}


def finish_edit(state, decision, result, before):
    """Only acknowledge a single requested edit whose saved result can be checked locally.

    Complex edits retain Sol's existing presentation path. Scientific claims cannot
    enter this answer: titles are read from the committed plan, not model prose.
    """
    if not before or not result.get('saved'):
        return result
    old, new = before['outline'], state.get('outline') or {}
    if new.get('plan_revision') == old.get('plan_revision'):
        return result
    old_sections, new_sections = old.get('sections', []), new.get('sections', [])
    old_ids, new_ids = [s['id'] for s in old_sections], [s['id'] for s in new_sections]
    target, action = before['target'], decision.arguments.get('action')
    kind = plan_edit_kind(before['text'])
    pending = state.get('v5_pending_draft') or {}
    combined = pending.get('request_id') == before['request_id']
    # Several explicit operations stay on the existing, validated path.
    from .writing_progress import _PLAN_ACTION
    command = re.sub(r'^\s*modifie\s+le\s+plan\s*:\s*', '', before['text'], flags=re.I)
    if len(_PLAN_ACTION.findall(command)) > 1:
        return result
    answer = None
    if action == 'rename' and target and not any(kind.values()) and old_ids == new_ids:
        section = next((s for s in new_sections if s['id'] == target), None)
        if section and section['title'] == decision.arguments.get('title'):
            answer = f"Titre de la section {old_ids.index(target)+1} enregistré : **{section['title']}**."
    elif action == 'delete' and target and kind['removal'] and not kind['sources']:
        if new_ids == [sid for sid in old_ids if sid != target]:
            removed = next(s for s in old_sections if s['id'] == target)
            answer = f"Section « {removed['title']} » supprimée du plan ; version antérieure conservée."
    elif action == 'add' and combined and kind['addition']:
        added = [s for s in new_sections if s['id'] not in old_ids]
        if len(added) == 1 and [sid for sid in new_ids if sid in old_ids] == old_ids:
            answer = f"Section « {added[0]['title']} » ajoutée et enregistrée dans le plan."
    if answer is None:
        return result
    if combined:
        answer += ' La rédaction demandée est conservée et attend votre validation du plan.'
    return {**result, **publish(state, answer, outcome='human_validation_required' if combined else 'operation_saved',
        complete=not combined)}


def human_refusal(state, args, code):
    """Keep a refused proposal without treating it as a saved manuscript section."""
    from . import workflow
    from .tools.outline import resolve_section
    try:
        section = resolve_section(state, args.get('section_id'))
    except ValueError:
        section = None
    if section:
        workflow.keep_proposed_section(state, section['id'], args, code)
    if code == 'plan_edit_request' and workflow.plan_approved(state):
        # Wrong tool before the requested edit: Sol can still repair it locally.
        return {'saved': False, 'rejection_code': code, 'error':
            'Demande de modification du plan : modifier le plan avec manage_outline ; aucune section n’est rédigée.'}
    answer = (("Demande de modification du plan. " if code == 'plan_edit_request' else '')
        + "Rédaction suspendue : validation du plan attendue dans l'espace État de l'art. "
        "Les sections déjà sauvegardées et les propositions en attente sont conservées.")
    return {'saved': False, 'rejection_code': code, 'error': answer,
        **publish(state, answer, outcome='human_validation_required')}
