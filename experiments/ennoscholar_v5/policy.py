"""Deterministic resource policy, without a scientific relevance score.

Identical successes are reusable; new questions, representations or source
versions remain open. A technical no-progress streak is a safety brake, not a
claim that the scientific literature is exhausted.
"""
import copy
from agents.EnnoScholarV4.task_results import fingerprint
from .progress import goal_core, unexamined_passages


def resolve_id(session, value):
    def norm(text):
        return ' '.join(str(text).casefold().replace('https://doi.org/', '').split())
    for aid, article in session.get('articles', {}).items():
        if norm(value) in {norm(aid), *(norm(article.get(k, '')) for k in ('title', 'doi', 'url'))}:
            return aid
    return value


# Results that depend on the current consultant request or on the plan and
# manuscript state are reused only within that request and state. Run
# 572ad911 received the previous request's goal ("five sections") for
# "section 1 only" because a goal receipt was matched across requests.
REQUEST_SCOPED = {'set_turn_goal', 'read_conversation', 'manage_outline', 'write_section', 'build_argument_blueprint',
    'review_document', 'review_literature_coverage', 'finalize_response', 'update_research_state'}


def request_context(session, tool):
    if tool not in REQUEST_SCOPED:
        return None
    request = next((m.get('id') for m in reversed(session.get('messages', [])) if m.get('role') == 'user'), None)
    return [request, len(session.get('messages', [])), fingerprint(session.get('outline', {})),
        fingerprint((session.get('document') or {}).get('writing_progress'))]


def article_identity(session, aid, tool):
    """What a tool result about one work depends on (document, card, bibliography, decision)."""
    a=session.get('articles',{}).get(aid,{})
    ft=a.get('fulltext',{})
    return [a.get('canonical_work_id') or a.get('doi') or aid,
        ft.get('document_version',{}),ft.get('document_sha256'),
        [a.get(k) for k in ('url','pdf_url','oa_url','open_access_locations')],ft.get('remaining_document_urls'),
        fingerprint(ft.get('text','')),fingerprint(a.get('analysis',{})) if tool in {
            'get_article','get_analysis_claims','verify_bibliography','finalize_response','compare_articles'} else None,
        fingerprint({k:a.get(k) for k in ('title','authors','year','verified','metadata_errors','assessment','selection_override')})]


def notice_state(session, aid):
    """Identity of the notice of one work as get_article would return it now."""
    return fingerprint(article_identity(session, aid, 'get_article'))


def known_notices(session, identifiers):
    """Requested works whose current notice Sol already received in this request: {resolved_id: title}.

    Uses the notice receipts confirmed at delivery (v5_request_knowledge.notices), so a batch
    that only overlaps a previous one is recognised work by work; a changed card, document,
    bibliography or decision gives a new state and the notice is sent again."""
    from agents.EnnoScholarV5.workflow import knowledge
    received = knowledge(session).get('notices') or {}
    known = {}
    for identifier in identifiers or []:
        aid = resolve_id(session, identifier)
        if aid in session.get('articles', {}) and received.get(aid) == notice_state(session, aid):
            known[aid] = (session['articles'][aid].get('title') or '')[:160]
    return known


ASKED_ONCE = {'analysis_persistence_required', 'requested_sections_unread'}


def notice_receipt(known):
    return {'articles': [], 'already_received': [{'id': aid, 'title': title} for aid, title in known.items()],
        'guidance': 'Notices inchangées déjà transmises dans cette demande (mémoire de travail). Pour approfondir : '
            'analyze_article(index, section_filter) puis read des méthodes, résultats, conditions ou limites.'}


def semantic_key(session,decision):
    args=copy.deepcopy(decision.arguments)
    reading_state = None
    if decision.tool=='write_section' and args.get('action')=='read':
        args.pop('instructions',None)  # Wording does not change a saved dossier.
        reading_state = {aid:a.get('analysis') for aid,a in session.get('articles',{}).items()}
    if decision.tool == 'analyze_article' and args.get('action') in {'index', 'review'}:
        # A coverage review belongs to this request/card; an index reflects
        # current receipts and deferred work, never a cached earlier window.
        reading_state = [next((m.get('id') for m in reversed(session.get('messages', [])) if m.get('role')=='user'), None),
            session.get('scientific_reading'), session.get('shown_evidence_ids'),
            {aid: session.get('articles', {}).get(aid, {}).get('analysis') for aid in args.get('article_ids', [])}]
    elif decision.tool == 'analyze_article' and args.get('action') == 'read':
        # Same mission message across segments: reuse. A new consultant question
        # may need these passages again even when its tool wording is identical.
        reading_state = next((m.get('id') for m in reversed(session.get('messages', [])) if m.get('role')=='user'), None)
    elif decision.tool == 'get_analysis_claims':
        # Wording does not change a saved window. A new question, card version,
        # field, offset or explicit revisit remains a distinct scientific need.
        args.pop('research_context', None)
        reading_state = [next((m.get('id') for m in reversed(session.get('messages', [])) if m.get('role')=='user'), None),
            decision.retry_reason]
    elif decision.tool == 'compare_articles':
        from agents.EnnoScholarV5.comparison_memory import identity
        reading_state = [session.get('scientific_relations'), identity(session, args.get('article_ids') or [])]
        if args.get('relations') is None and args.get('conclusions') is None:
            # Read-only matrix: its wording does not change what is read (run fd41608c).
            # The runtime answers a repeated window with a short notice instead.
            args.pop('research_context', None)
            args.pop('assessment', None)
            reading_state = [reading_state, request_context(session, 'read_conversation')]
    elif decision.tool == 'identify_research_gaps':
        from agents.EnnoScholarV5.comparison_memory import identity
        reading_state = identity(session, args.get('article_ids') or [])
    elif decision.tool == 'finalize_response':
        # A publication refused for lack of saved relations is valid once they are
        # saved (or explicitly ruled out): it is not the same failed operation.
        reading_state = [session.get('scientific_relations'), session.get('v5_comparison_decisions')]
    if decision.tool == 'set_turn_goal':
        return fingerprint([decision.tool, goal_core(args), request_context(session, decision.tool)])
    if decision.tool == 'get_article':
        args['identifiers'] = sorted(resolve_id(session, i) for i in args.get('identifiers', []))
    for key in ('query','research_context','passage_query'):
        if isinstance(args.get(key),str):args[key]=' '.join(args[key].casefold().split())
    ids=args.get('article_ids',[]) or ([args['article_id']] if args.get('article_id') else args.get('identifiers',[]))
    identities=[article_identity(session, aid, decision.tool) for aid in ids]
    provenance = ({k:v for k,v in session.get('source_snapshots',{}).items() if v.get('article_id') in ids}
        if decision.tool in {'analyze_article','get_analysis_claims','verify_bibliography','finalize_response'} else {})
    purpose = '' if decision.tool in {'get_article','get_analysis_claims'} else ' '.join(decision.purpose.casefold().split())
    return fingerprint([decision.tool,args,identities,fingerprint(provenance),purpose,request_context(session, decision.tool),
        fingerprint(reading_state)])


def informative_signature(progress):
    """Ignore receipt UUIDs and display windows; retain scientific state changes."""
    rows = {}
    for aid, row in progress['articles'].items():
        rows[aid] = {k:v for k,v in row.items() if k not in {'available_passages','access_attempts'}}
        rows[aid]['access_failures'] = sorted({fingerprint({k:v for k,v in attempt.items()
            if k not in {'attempt_ref','retry_at'}}) for attempt in row.get('access_attempts', [])})
    return fingerprint([rows, progress.get("scientific_outputs", {}), progress.get('goal')])


def reusable(session,decision,completed):
    key=semantic_key(session,decision)
    if decision.tool == 'set_turn_goal' and goal_core(decision.arguments) == goal_core(session.get('turn_goal', {})):
        return key, {'reused_goal': True, 'turn_goal': copy.deepcopy(session['turn_goal']),
            'guidance': 'Cadrage déjà sauvegardé. Choisir une tâche scientifique restante ou finaliser.'}, 'same_goal'
    # Archived pages are answered from the current local state, before any
    # receipt replay, so the unexamined-passage index is never stale.
    a=session.get('articles',{}).get(decision.arguments.get('article_id'),{})
    ft=a.get('fulltext',{})
    if decision.tool=='get_fulltext' and ft.get('text') and not decision.arguments.get('candidate_urls'):
        start=decision.arguments.get('page_start')
        limit=decision.arguments.get('page_limit') or 20
        complete=bool(start and 1<=start<=(ft.get('page_count') or 0) and all(ft.get('page_extraction',{}).get(str(p),{}).get('status')=='complete'
            for p in range(start,min(start+limit,(ft.get('page_count') or start)+1))))
        # A complement reason without new URLs on already extracted pages
        # would re-extract the same archived PDF (run e5cdb635, operation 16).
        if (start is None and not decision.arguments.get('complement_reason')) or complete:
            # Run e5cdb635: Sol asked four times for pages 15-29 of a complete
            # document and received no content. The archived text answers with
            # exact locators of the passages it has not examined on those pages.
            return key,{'reused_local':True,'article_id':a['id'],'access_level':a.get('access_level'),
                'page_count':ft.get('page_count'),'next_page':ft.get('next_page'),
                'truncated':ft.get('truncated'),
                'unexamined_passages':unexamined_passages(session,a['id'],page_start=start,page_limit=decision.arguments.get('page_limit')),
                'guidance':'Document archivé, pas de nouvelle extraction. Lire les passages utiles par evidence_ids ou reprendre next_page.'},'archived_document'
    if decision.tool == 'get_article' and not decision.retry_reason:
        # Run 48d2c500: the same notices were requested again in identical or overlapping
        # batches and each replay re-sent the whole result (about 11 500 characters). Every
        # requested notice already received unchanged in this request gets a short receipt;
        # an overlapping batch is reduced to its new or changed works by the toolkit.
        identifiers = decision.arguments.get('identifiers') or []
        known = known_notices(session, identifiers)
        if identifiers and len(known) == len({resolve_id(session, i) for i in identifiers}):
            return key, notice_receipt(known), 'same_operation'
    # A document found inaccessible during this request is not requested again
    # without a new source or an explicit reason (run 2d882893: three attempts).
    failed = set((session.get('v5_request_knowledge') or {}).get('failed_sources') or [])
    if (decision.tool == 'get_fulltext' and decision.arguments.get('article_id') in failed and not ft.get('text')
            and not decision.arguments.get('candidate_urls') and not decision.retry_reason):
        return key,{'not_executed':True,'reason':'source_inaccessible','article_id':decision.arguments.get('article_id'),
            'previous_attempts':[{k:a.get(k) for k in ('stage','http_status','error') if a.get(k) is not None}
                for a in ft.get('attempts',[])][-4:],
            'guidance':'Source inaccessible pendant cette demande : la signaler comme limite documentaire. '
                'Une nouvelle tentative exige candidate_urls ou une raison explicite.'},'inaccessible_source'
    # Replays of successful actions return their exact result. Failed attempts
    # are never silently treated as successes or retried in a loop. A result
    # deferred for lack of context room is not a result: it is never replayed.
    previous=next((op for op in reversed(completed) if op['response'].get('semantic_key')==key
        and not op['response'].get('output', {}).get('transmission_deferred')),None)
    if previous:
        output=previous['response']['output']
        if output.get('reason') == 'dossier_already_in_context':
            # Not a failure: the same notice again (the dossier is in the context).
            return key,copy.deepcopy(output),'same_operation'
        if not output.get('error') and not output.get('not_executed') and not output.get('failure_code'):
            if decision.tool == 'get_analysis_claims':
                from agents.EnnoScholarV5.workflow import claim_receipt
                known = (session.get('v5_request_knowledge') or {}).get('in_context_claims',{}).get(output.get('article_id'),[])
                if (output.get('claims') and not output.get('structured_measurements') and
                        all(claim_receipt(c.get('field'),c) in known for c in output['claims'])):
                    return key, {'reused_card':True, 'article_id':output.get('article_id'),
                        'next_offset':output.get('next_offset'), 'total_claims':output.get('total_claims'),
                        'guidance':'Fenêtre déjà reçue ; exploiter les affirmations de la mémoire de travail. '
                            'Autres champs/offsets ou une relecture motivée restent disponibles.'}, 'same_operation'
            if decision.tool=='write_section' and decision.arguments.get('action')=='read' and output.get('writing_context'):
                sid=(output.get('section') or {}).get('id')
                receipt=f"dossier:{sid}:{fingerprint(output['writing_context'])}"
                if receipt in (session.get('v5_request_knowledge') or {}).get('keys', []):
                    return key,{'reused_dossier':True,'section_id':sid,
                        'guidance':'Dossier identique déjà transmis. Les fiches et preuves restent récupérables par '
                            'get_analysis_claims/analyze_article ; un changement scientifique produit un nouveau dossier.'},'same_operation'
            if decision.tool=='analyze_article' and decision.arguments.get('action')=='read' and not decision.retry_reason:
                ids = [p['evidence_id'] for r in output.get('reading', []) for p in r.get('passages', [])]
                if ids and set(ids) <= set(session.get('shown_evidence_ids', [])):
                    return key,{'reused_reading':True, 'presented_evidence_ids':ids,
                        'guidance':'Passages déjà fournis, sans nouvelle lecture ni copie du texte. '
                            'Analyser/sauvegarder les preuves utiles ; index pour les lacunes ; revisit=true pour une relecture motivée.'},'same_operation'
            return key,copy.deepcopy(output),'same_operation'
        if decision.tool == 'finalize_response' and output.get('rejection_code') in ASKED_ONCE:
            # These checks ask once per request and let the next publication through: the
            # same publication sent again must reach them, not a cached failure.
            return key,None,None
        if not decision.retry_reason:
            return key,{'not_executed':True,'reason':'blocked_previous_failure',
                'previous_error':output.get('error'),'retry_requires':'changed source/version or an explicit retry reason'},'blocked_failure'
    return key,None,None


def useful_need(progress,decision):
    """Bind resource use to persisted work or Sol's explicit scientific purpose.

    A purpose is not a correctness guarantee. Relevance still belongs to Sol;
    deterministic guards cannot decide that two semantic questions are equal.
    """
    if decision.tool in {'finalize_response','pause'} or decision.purpose.strip():return True
    args=decision.arguments
    ids=set(args.get('article_ids') or args.get('identifiers') or [args.get('article_id')])
    if decision.tool in {'search_scientific','find_similar_articles','search_collected_articles'}:
        return any(t['kind'] in {'complete_selection','evaluate_candidate'} for t in progress['tasks'].values())
    return any(aid in progress['articles'] for aid in ids)


def stagnant(state,*,no_gain,reuse_reason,purpose):
    # Goal/scientific-state changes reset this streak. Alternating tool names or
    # cosmetic purpose text cannot reset it. Production gives recovery feedback
    # before its third miss, then uses the existing finalization path.
    streak=state.get('no_gain_streak',0)+1 if no_gain else 0
    return streak,streak>=2
