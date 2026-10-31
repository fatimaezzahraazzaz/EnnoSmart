"""Shared V5 freshness contract for comparison receipts, including negative ones.

The future comparison block must reuse this identity, not invent another cache.
This module neither judges comparability nor creates scientific relations.
"""
from .task_results import fingerprint
from .instructions import current_request
import re


def pin_question(state, progress, request):
    if not progress:
        return
    pinned = state.get('v5_comparison_question') or {}
    generic_resume = re.fullmatch(r"\s*(?:continue|reprends|poursuis|termine|finalise)\s+(?:la\s+)?"
        r"(?:r[ée]daction|le\s+manuscrit|l[’']état\s+de\s+l[’']art)"
        r"(?:\s+(?:du\s+manuscrit|de\s+l[’']état\s+de\s+l[’']art))?[\s.!]*",request,re.I)
    if pinned.get('progress_id') != progress.get('id') or not generic_resume:
        state['v5_comparison_question'] = {'progress_id':progress['id'], 'question':request}


def identity(state, article_ids, section=None):
    ids = sorted(set(article_ids))
    question = ' '.join((current_request(state) or {}).get('content','').casefold().split())
    pinned = state.get('v5_comparison_question') or {}
    progress = (state.get('document') or {}).get('writing_progress') or {}
    # Without a pinned question (no drafting progress), both ids are None: keep the request.
    if section and pinned.get('question') is not None and pinned.get('progress_id') == progress.get('id'):
        question = ' '.join(pinned['question'].casefold().split())
    goal = state.get('turn_goal') or {}
    # A writing continuation can have only restored references before Sol sets a
    # goal again. Its pinned question/section, not that transient goal, is authoritative.
    question_context = {} if section else {k:goal.get(k) for k in ('deliverable','reading_depth','comparison_requested')}
    from .models import Article
    from .argumentation import comparison_source_signature
    registry = {aid:Article.model_validate(state['articles'][aid]) for aid in ids if aid in state.get('articles',{})}
    sources = comparison_source_signature(registry,ids)
    # Only hashes persist in the receipt, never full texts or PDFs.
    proofs = {eid:e for eid,e in state.get('evidence',{}).items() if e.get('article_id') in ids}
    snapshots = {key:value for key,value in state.get('source_snapshots',{}).items() if value.get('article_id') in ids}
    section_context = {k:section.get(k) for k in ('id','title','description')} if section else None
    return fingerprint({'version':1, 'question':question, 'goal':question_context,
        'scope':state.get('scientific_scope'), 'section':section_context, 'article_ids':ids,
        'sources':sources, 'proofs':proofs, 'snapshots':snapshots})


def current(state, decision, article_ids, section=None):
    return (sorted(set(decision.get('article_ids') or [])) == sorted(set(article_ids))
        and decision.get('identity_version') == 1
        and decision.get('identity') == identity(state,article_ids,section))


def remembered(state, article_ids, section=None):
    return any(current(state,row,article_ids,section) for row in state.get('v5_comparison_decisions',[]))
