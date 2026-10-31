"""A factual projection of V4 receipts, not scientific ranking or a controller."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import re
from agents.EnnoScholarV4.task_results import fingerprint
from agents.EnnoScholarV4.evidence import exact_source_valid, passages
from agents.EnnoScholarV4.models import Article


@dataclass(frozen=True)
class Scope:
    owner_id: int
    session_id: str
    project_id: str | None = None

    @property
    def key(self):
        return fingerprint(asdict(self))


def goal_core(goal):
    """Structural scientific intent, excluding its free-text paraphrase.

    A new question/hypothesis belongs in research_state or research_need; changes
    there remain meaningful. Cosmetic understanding does not refill a loop.
    """
    refs = goal.get('references') or goal
    core = {k: goal.get(k) for k in ('deliverable', 'reading_depth', 'depth',
        'search_needed', 'article_scope', 'research_need', 'comparison_requested', 'focus_lock_ids', 'exact_quote') if goal.get(k) not in (None, '', [])}
    core['references'] = {k: sorted(refs.get(k) or goal.get(k) or []) for k in
        ('article_ids', 'lock_ids', 'constraints')}
    if goal.get('deliverable') not in {'deep_reading', 'article_analysis'}:
        core['requested_article_count'] = goal.get('requested_article_count')
    return core


def request_packet(session):
    request = next((m for m in reversed(session.get('messages', [])) if m.get('role') == 'user'), {})
    return {'id': request.get('id'), 'text': str(request.get('content', request.get('text', '')))[:6000]}


def scope_ids(session):
    goal = session.get('turn_goal') or {}
    return set((goal.get('references') or {}).get('article_ids') or []) | set(
        session.get('conversation_references', {}).get('article_ids') or []) | set(
        session.get('current_turn_article_ids') or []) | set(session.get('v5_explicit_context_ids') or [])


def synchronize_scope(session):
    """Persist the same explicit scope used by tools, context and projection."""
    ids = sorted(scope_ids(session) & session.get('articles', {}).keys())
    session['current_turn_article_ids'] = ids
    refs = session.setdefault('conversation_references', {})
    refs['article_ids'] = ids


NON_SCIENTIFIC = re.compile(r"references|bibliograph|acknowledg|author contributions|declaration of interests", re.I)
# Saved-card fields that a deep reading may still lack. A factual cue only: Sol
# decides which ones the request needs; none is a finalisation condition.
CARD_FIELDS = ("technical_mechanism", "experimental_setup", "parameters", "validation_protocol", "results",
    "metrics", "tables_figures", "comparisons", "hypotheses", "limitations", "conditions_of_validity", "open_questions")


def examined_passages(session, aid, rows):
    """Only exact saved analysis/review receipts, never a presentation flag."""
    from agents.EnnoScholarV5.science import passage_states
    return passage_states(session, Article.model_validate(session['articles'][aid]), rows)['examined']


def reading_map(rows, examined, *, presented=(), saved=()):
    """Where the extracted text lies and what was examined: sections, pages, counts."""
    groups = []
    for r in rows:
        label = " ".join(str(r.get("section") or "untitled").split())
        if groups and groups[-1]["section"] == label:
            g = groups[-1]
        else:
            g = {"section": label, "first_page": r.get("page"), "last_page": r.get("page"), "passages": 0,
                "examined": 0, 'presented':0, 'saved':0}
            groups.append(g)
        g["last_page"] = r.get("page") if r.get("page") is not None else g["last_page"]
        g["passages"] += 1
        g["examined"] += r["evidence_id"] in examined
        g['presented'] += r['evidence_id'] in presented
        g['saved'] += r['evidence_id'] in saved
    pages = {}
    for r in rows:
        if r.get("page") is not None and not NON_SCIENTIFIC.search(str(r.get("section", ""))):
            pages.setdefault(r["page"], False)
            pages[r["page"]] |= r["evidence_id"] in examined
    return ([{"section": g["section"], "pages": f"{g['first_page']}-{g['last_page']}", "passages": g["passages"],
        "examined": g["examined"], 'presented':g['presented'], 'saved':g['saved']} for g in groups], sorted(p for p, seen in pages.items() if not seen))


def unexamined_passages(session, aid, *, page_start=None, page_limit=None, limit=40):
    """Locators of extracted passages not yet examined, for analyze_article(evidence_ids)."""
    article = Article.model_validate(session["articles"][aid])
    rows = passages(article)
    examined = examined_passages(session, aid, rows)
    last = page_start+(page_limit or 20)-1 if page_start else None
    pending = [r for r in rows if r["evidence_id"] not in examined and not NON_SCIENTIFIC.search(str(r.get("section", "")))
        and (page_start is None or r.get("page") is not None and page_start <= r["page"] <= last)]
    return {"total_unexamined": len(pending), "passages": [{"evidence_id": r["evidence_id"], "page": r.get("page"),
        "section": " ".join(str(r.get("section") or "").split())[:40], "preview": " ".join(r["text"].split())[:100]}
        for r in pending[:limit]],
        "read_with": "analyze_article(action='read', article_ids=[id], evidence_ids=[jusqu'à 8], passage_query='', passage_offset=0)"}


def project(session, *, scope, requested_fields=("method", "results", "limitations")):
    """Never infer that a saved claim or successful download completes a reading.

    Unstarted reads and partial PDFs are visible even for a single named paper.
    No task below triggers an action: Sol chooses which ones matter to the need.
    """
    articles = session.get("articles", {})
    proposed = session.get("research_progress", {}).get("proposed_article_ids",
        session.get("displayed_article_ids", []))
    proposed = list(dict.fromkeys(aid for aid in proposed if aid in articles and
        articles[aid].get("selection_override") is not False))
    priority = set(proposed) | scope_ids(session)
    priority.update(session.get("conversation_references", {}).get("article_ids", []))
    priority.update(aid for aid, a in articles.items() if
        a.get("selection_override") is True or a.get("assessment", {}).get("decision") == "retain")
    proofs = session.get("evidence", {})
    snapshots = session.get("source_snapshots", {})
    shown = set(session.get("shown_evidence_ids", []))
    rows, tasks = {}, {}
    goal = session.get('turn_goal') or {}
    constraints = set(goal.get('constraints') or []) | set((goal.get('references') or {}).get('constraints') or [])
    document_only = 'no_reanalysis' in constraints or (
        goal.get('deliverable') == 'answer' and goal.get('reading_depth') == 'none')
    current_ids = set(session.get('v5_explicit_context_ids', [])) | set(session.get('current_turn_article_ids', []))
    request_id = next((m.get('id') for m in reversed(session.get('messages', [])) if m.get('role') == 'user'), None)
    start = session.get('v5_request_start') or {}
    scoped_start = start.get('request_id') == request_id and isinstance(start.get('article_ids'), list)
    if scoped_start:
        current_ids.update(set(articles)-set(start['article_ids']))
    received = session.get('v5_request_knowledge') or {}
    if received.get('request_id') == request_id:
        current_ids.update(received.get('candidate_ids', []))

    def task(kind, aid=None, *, version="", details=None):
        key = fingerprint([scope.key, kind, aid, version])
        tasks[key] = {"id": key, "kind": kind, "article_id": aid, "source_version": version,
            **(details or {})}

    for aid, raw in articles.items():
        a = Article.model_validate(raw)
        document, analysis = a.fulltext, a.analysis
        version = document.get("document_version", {})
        version_id = version.get("version_id") or a.source_version or a.doi or aid
        extracted = passages(a)
        available = len(extracted)
        from agents.EnnoScholarV5.science import passage_states, reading_status, analysis_depth
        stages = passage_states(session, a, extracted)
        # Provenance, body coverage and sufficiency, apart from V4's access_status (point 4, run 48d2c500).
        depth = analysis_depth(session, a) if analysis and aid in priority else None
        coverage, unexamined_pages = reading_map(extracted, stages['examined'],
            presented=stages['presented'], saved=stages['saved']) if extracted else ([], [])
        scientific = reading_status(session, a, rows=extracted, states=stages) if aid in priority else {}
        seen = sorted(eid for eid in shown if eid in proofs and proofs[eid]["article_id"] == aid
            and exact_source_valid(proofs[eid], snapshots))
        saved = sorted(e["evidence_id"] for e in analysis.get("evidence", [])
            if exact_source_valid(e, snapshots))
        fields = {f: "targeted_claims_saved" if analysis.get(f) or
            f == "method" and analysis.get("technical_mechanism") else "not_established"
            for f in requested_fields}
        missing = [f for f, status in fields.items() if status == "not_established"]
        partial = bool(document.get("text") and (document.get("truncated") or
            document.get("extraction_complete") is False))
        row = {"article_id": aid, "title": a.title, "work_id": a.canonical_work_id or aid,
            "candidate_decision": a.assessment.get("decision", "not_evaluated"),
            "consultant_decision": a.selection_override, "proposed": aid in proposed,"in_request_scope":aid in priority,
            "bibliography": "conflict" if a.metadata_errors else "verified" if a.verified else "unverified",
            "document": "partial" if partial else "extracted" if document.get("text") else "not_recovered",
            "document_version": version_id, "document_sha256": version.get("document_sha256"),
            "page_count": document.get("page_count"), "next_page": document.get("next_page"),
            "extracted_pages": sorted(int(p) for p, r in document.get("page_extraction", {}).items()
                if r.get("status") == "complete"),
            "access_attempts":[{"attempt_ref":i,**{k:r[k] for k in ('stage','http_status','error','failure_code','retry_at') if k in r}}
                for i,r in enumerate(document.get('attempts',[]))],
            "available_passages": available, "read_evidence_ids": seen, "saved_evidence_ids": saved,
            "offered_evidence_ids":sorted(eid for eid,e in proofs.items() if e.get('article_id')==aid and exact_source_valid(e,snapshots)),
            "analysis_saved": bool(analysis), "analysis_basis": a.analysis_basis,
            "analysis_depth": {k: depth[k] for k in ('provenance', 'coverage', 'depth', 'question_sufficient', 'label')} if depth else None,
            "reading_coverage": analysis.get("reading_coverage", "not_established"),
            "analysis_signature":fingerprint(analysis),"assessment_signature":fingerprint(a.assessment),
            "fields": fields, "missing_fields": missing,
            "reading_map": coverage, "unexamined_pages": unexamined_pages,
            'scientific_coverage': scientific,
            'unexamined_sections':[g['section'] for g in coverage if g['examined'] < g['passages']],
            "empty_card_fields": [f for f in CARD_FIELDS if not analysis.get(f)] if analysis else []}
        rows[aid] = row
        if a.selection_override is False or a.assessment.get("decision") == "reject":
            continue
        if scoped_start and session.get('v5_new_search') and aid not in current_ids and aid not in scope_ids(session):
            continue  # Old corpus remains stored, without unrelated new-request tasks.
        if not a.assessment and aid not in priority:
            task("evaluate_candidate", aid, version=version_id)
        if aid not in priority:
            continue
        if a.metadata_errors:
            task("resolve_bibliography", aid, version=version_id)
        if not document.get("text"):
            task("recover_document", aid, version=version_id)
        if partial:
            task("continue_extraction", aid, version=version_id,
                details={"next_page": document.get("next_page"), "page_count": document.get("page_count")})
        if not seen:
            task("read_local" if document.get("text") else "read_abstract", aid, version=version_id,
                details={"fields": list(requested_fields)})
        if seen and not analysis and not document_only:
            task("save_analysis", aid, version=version_id, details={"evidence_ids": seen})
        # Targeted analyses are not exhaustive and do not silently close future
        # method/protocol/limit questions. This is a cue, never a finalisation gate.
        if analysis and not scientific.get('question_sufficient'):
            task("review_depth", aid, version=version_id, details={"missing_fields": missing,
                "unexamined_pages": unexamined_pages, "empty_card_fields": rows[aid]["empty_card_fields"]})
    manuscript_tasks(session, task)
    from agents.EnnoScholarV5.science import comparison_work
    for group in comparison_work(session):
        if group['status']=='consider_comparison' and len(group['candidate_article_ids'])>=2:
            task('consider_comparison', version=fingerprint(group['candidate_article_ids']),
                details={'group':group['group'], 'article_ids':group['candidate_article_ids'], 'optional':True})
    target = session.get("research_progress", {}).get("requested_count")
    if session.get('turn_goal', {}).get('deliverable') in {'deep_reading', 'article_analysis'}:
        target = None  # A named-paper reading is not a proposal quota.
    if target and len(proposed) < target:
        task("complete_selection", details={"missing": target-len(proposed)})
    return {"goal":{"request_ref":request_packet(session)['id'],
            **goal_core(session.get('turn_goal', {})), "fields":list(requested_fields)},
        "search_refs":[{"search_ref":i,"key":fingerprint(s.get('key')),"article_ids":s.get('article_ids',[]),
            "approach_ids":s.get('approach_ids',[]),"family":s.get('family')} for i,s in enumerate(session.get('recent_searches',[]))],
        "scientific_outputs": {**{key: fingerprint(session.get(key)) for key in (
            "outline", "document", "comparisons", "scientific_relations", "research_gaps", "research_state", "research_need", "scientific_scope", "web_sources")},
            # Stored knowledge actually delivered for this request (cards, plan,
            # dossier, conversation): reading it before writing is progress.
            "delivered_knowledge": fingerprint([(session.get("v5_request_knowledge") or {}).get(k) for k in ("request_id", "keys")]),
            # Workflow, not science: a bounded preparation refusal records an obligation
            # of the section (at most LOCAL_REFUSALS per section and request), so it is not
            # a no-progress repeat; it adds no card, proof or relation.
            "workflow_obligations": fingerprint(session.get("v5_section_preparation"))},
        "articles": rows, "tasks": tasks, "counts": {"target": target,
        "candidates": len(rows), "proposed": len(proposed), "missing": max(0, target-len(proposed)) if target else None,
        "read": sum(bool(r["read_evidence_ids"]) for r in rows.values()),
        "analyses_saved": sum(r["analysis_saved"] for aid, r in rows.items() if aid in priority),
        "documents_partial": sum(r["document"] == "partial" for r in rows.values())}}


def manuscript_tasks(session, task):
    """Plan and manuscript obligations of the current request, kept for resumption."""
    request = next((m.get("id") for m in reversed(session.get("messages", [])) if m.get("role") == "user"), None)
    goal = session.get("turn_goal") or {}
    outline = session.get("outline", {})
    start = session.get("v5_request_start") or {}
    if (goal.get("deliverable") in {"plan", "plan_revision"} and start.get("request_id") == request and
            (outline.get("plan_revision") == start.get("plan_revision") or not outline.get("sections"))):
        task("persist_plan", version=str(outline.get("plan_revision")), details={"request_ref": request})
    progress = (session.get("document") or {}).get("writing_progress") or {}
    if progress.get("request_message_id") != request:
        return
    sections = {s["id"]: s for s in outline.get("sections", [])}
    saved, blocked = progress.get("saved_draft_ids", {}), progress.get("blocked", {})
    from types import SimpleNamespace
    from agents.EnnoScholarV4.writing_context import source_readiness
    view = SimpleNamespace(registry={aid: Article.model_validate(a) for aid, a in session.get("articles", {}).items()},
        shown_evidence=list(session.get("shown_evidence_ids", [])), evidence=session.get("evidence", {}),
        snapshots=session.get("source_snapshots", {}))
    for sid in progress.get("target_ids", []):
        if sid in saved or sid not in sections:
            continue
        section = sections[sid]
        task("draft_section", sid, version=str(outline.get("plan_revision")), details={"section_id": sid,
            "title": section["title"][:160], "blocked": blocked.get(sid)})
        for aid, row in source_readiness(session, view, section.get("article_ids", [])).items():
            if row["status"] != "analysed":
                task("analyse_section_source", aid, version=str(outline.get("plan_revision")),
                    details={"section_id": sid, "status": row["status"], "basis": row["basis"]})


def model_work_view(progress, *, budget=10000):
    """All targeted works first; detail may be compressed, never silently dropped.

    Section tuples: label (or local index), available, presented, examined, saved.
    The index tool returns full labels and exact passage IDs without page assumptions.
    """
    tasks = progress['tasks']
    view = {'counts':progress['counts'], 'goal':progress['goal'],
        'pending_by_kind':{kind:sum(t['kind']==kind for t in tasks.values()) for kind in {t['kind'] for t in tasks.values()}},
        'section_columns':['section', 'available', 'presented', 'examined', 'saved'],
        'articles':[], 'total_articles':len(progress['articles']), 'omitted_articles':0,
        'retrieve':'analyze_article(action=index, article_ids, section_filter, index_offset) : sections et passages exacts ; aucune lecture implicite'}
    targeted = [(aid,a) for aid,a in progress['articles'].items() if a['in_request_scope']]
    others = [(aid,a) for aid,a in progress['articles'].items() if not a['in_request_scope']]
    view['targeted_articles']=len(targeted)
    view['omitted_articles']=len(others)
    for aid,a in targeted:
        science = a.get('scientific_coverage') or {}
        view['articles'].append({'article_id':aid, 'in_request_scope':True, 'document':a['document'],
            'next_page':a['next_page'], 'page_count':a['page_count'], 'analysis_saved':a['analysis_saved'],
            'unexamined_pages':a['unexamined_pages'], 'empty_card_fields':a['empty_card_fields'],
            'states':science.get('states', {}), 'question_sufficient':science.get('question_sufficient',False),
            'missing_fields':a['missing_fields'], 'gaps':[k for k,v in science.get('dimensions',{}).items() if v not in {'documented','verified','not_applicable'}
                or ((a.get('analysis_depth') or {}).get('coverage') or {}).get(k) == 'introductory'],
            'analysis_depth':(a.get('analysis_depth') or {}).get('depth'),
            'sections':[[g['section'],g['passages'],g['presented'],g['examined'],g['saved']] for g in a.get('reading_map',[])],
            'section_count':len(a.get('reading_map',[]))})
    size = lambda: len(json.dumps(view,ensure_ascii=False))
    if size()>budget:
        # Every section remains represented by its local ordinal. Full literal
        # labels are paginated by index; this compression is explicit to Sol.
        for row in view['articles']:
            row['section_labels_in_index']=True
            row['sections']=[[i,*section[1:]] for i,section in enumerate(row['sections'])]
    if size()>budget:
        for row in view['articles']:
            row.pop('gaps',None)
            row.pop('analysis_depth',None)
    if size()>budget:
        raise ValueError('La carte de tous les articles ciblés ne tient pas dans le contexte disponible ; aucun article omis.')
    for aid,a in others:
        row={'article_id':aid,'in_request_scope':False,'analysis_saved':a['analysis_saved']}
        view['articles'].append(row)
        view['omitted_articles']=view['total_articles']-len(view['articles'])
        if size()>budget:
            view['articles'].pop()
            view['omitted_articles']+=1
            break
    view['omitted_articles']=view['total_articles']-len(view['articles'])
    return view
