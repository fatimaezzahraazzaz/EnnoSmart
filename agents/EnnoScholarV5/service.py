"""Authenticated V5 service. Reuses the V4 UI contracts with separate SQL tables.

No scientific work on import. A drafting mission opened by the consultant's request
(writing_job.py) continues by segments without another message and is recovered
after a restart by V4's job watchdog, started with the V5 router. An existing V4 ID
cannot resolve here.
"""
from __future__ import annotations
import copy
import os
import re
import threading
import time
from datetime import datetime, timedelta
from sqlalchemy import select, update
from .reuse import load
from .config import runtime_config, ConfigurationError
from .session import ScholarV4Session, ScholarV4Run, lock_rows, sync_records
from .persistence import ApplicationRepository, RunMeter, Budget, resource_rows, refresh_job_resources
from .resources import balance, combine
from .checkpoints import application_checkpoints
from .runtime import ResponsesSol, ScientificAdapter
from .stops import StopResearch
from experiments.ennoscholar_v5.graph import Runner
from experiments.ennoscholar_v5.progress import Scope, project

base = load("_service_base", "service.py")
SessionNotFound, SessionBusy = base.SessionNotFound, base.SessionBusy

class ScholarV5Service(base.ScholarV4Service):
    def __init__(self, session_factory, config=None, *, client=None, sources=None, checkpointer_factory=None):
        super().__init__(session_factory, runtime_config(config))
        self.client, self.sources = client, sources
        self.checkpointer_factory = checkpointer_factory or application_checkpoints

    def create(self, owner, title="Nouvelle conversation", *, options=None):
        result = super().create(owner, title, options=options)
        with self.session_factory() as db:
            row = self.owned(db, owner, result["id"], lock=True)
            row.data = {**row.data, "engine": "v5_langgraph",
                'v5_session_budget': {'limit_usd': self.config.session_budget_usd, 'revision': 0}}
            db.commit()
        return {**result, "engine": "v5_langgraph"}

    def session_read(self, row, detailed=True):
        result = super().session_read(row, detailed)
        result["engine"] = "v5_langgraph"
        if detailed:
            result['resource_budget'] = self.session_budget(row.owner_id, row.id)
        if detailed:
            with self.session_factory() as db:
                latest = db.scalar(select(ScholarV4Run).where(ScholarV4Run.session_id == row.id,
                    ScholarV4Run.owner_id == row.owner_id).order_by(ScholarV4Run.created_at.desc()).limit(1))
                result["resumable_run_id"] = latest.data.get("resumable_run_id") if latest else None
        job = row.data.get("writing_job")
        if result.get("writing_job") and job:
            # The drafting mission as the consultant follows it.
            from .writing_job import progress_view
            result["writing_job"]["mission_progress"] = progress_view(job, row.data)
        return result

    def session_budget(self, owner, sid):
        with self.session_factory() as db:
            row = self.owned(db, owner, sid)
            policy = row.data.get('v5_session_budget') or {'limit_usd': self.config.session_budget_usd, 'revision': 0}
            usage = combine(balance(data) for _, data, _ in resource_rows(db, sid))
        cap = policy.get('limit_usd')
        return {**usage, **policy, 'remaining_usd': max(0., cap-usage['committed_usd']) if cap is not None else None}

    def set_session_budget(self, owner, sid, limit_usd, *, expected_revision=None):
        from .config import positive
        if limit_usd is not None:
            try:
                limit_usd = positive(limit_usd, 'Budget de conversation')
            except ConfigurationError as exc:
                raise ValueError(str(exc)) from None
        with self.session_factory() as db:
            row = self.owned(db, owner, sid, lock=True)
            policy = row.data.get('v5_session_budget') or {'revision': 0}
            if expected_revision is not None and expected_revision != policy.get('revision', 0):
                raise ValueError('Budget modifié dans une autre fenêtre ; actualisez la conversation.')
            row.data = {**row.data, 'v5_session_budget': {'limit_usd': limit_usd, 'revision': policy.get('revision', 0)+1}}
            db.commit()
        return self.session_budget(owner, sid)

    def _write_job(self, db, row, job, *, announce=False):
        refresh_job_resources(db, row.id, job)
        return super()._write_job(db, row, job, announce=announce)

    def open_run(self, owner, sid, message, **kwargs):
        with self.session_factory() as db:
            before = self.owned(db, owner, sid).data
            saved_before = {s['id']:s['drafts'][-1]['id'] for s in before.get('outline', {}).get('sections', []) if s.get('drafts')}
        rid = super().open_run(owner, sid, message, **kwargs)
        from .writing_job import mission, science_signature
        with self.session_factory() as db:
            row = self.owned(db, owner, sid)
            run = db.get(ScholarV4Run, rid)
            latest = next((m for m in reversed(row.data.get("messages", [])) if m["role"] == "user"), {})
            job = row.data.get("writing_job") or {}
            extra = {}
            if run.data.get("writing_job_id") and job.get("id") == run.data["writing_job_id"]:
                job = copy.deepcopy(job)
                refresh_job_resources(db, sid, job)
                row.data = {**row.data, 'writing_job': job}
                # What this segment starts from: the mission caps and the saved science.
                extra = {"job_mission": copy.deepcopy(mission(copy.deepcopy(job))),
                    "science_before": science_signature(row.data)}
            run.data = {**run.data, "request_message_id": latest.get("id"), 'v5_saved_before':saved_before, **extra}
            db.commit()
        return rid

    def after_run(self, owner, sid, rid, *, dispatch=True):
        """Promote a draft intent identified by Sol, charging its first run too."""
        from .writing_job import new_job, EMPTY
        from . import workflow
        if self.config.writing_job_enabled:
            with self.session_factory() as db:
                lock_rows(db, sid, rid)
                row = self.owned(db, owner, sid)
                run = db.get(ScholarV4Run, rid)
                p = workflow.writing_progress(row.data)
                if (not row.data.get('writing_job') and p and p.get('mode')=='full'
                        and not run.data.get('writing_job_id') and run.status in {'partial','completed'}):
                    job = new_job(self.config, p['request_message_id'], rid, self.fingerprint())
                    started = run.created_at.isoformat()+'Z'
                    job.update(created_at=started, saved_before=run.data.get('v5_saved_before', {}))
                    job['mission']['started_at'] = started
                    data = copy.deepcopy(row.data); data['writing_job'] = job; row.data = data
                    budget = db.get(Budget,rid)
                    run.data = {**run.data, 'writing_job_id':job['id'], 'job_budget_usd':job['budget_usd'],
                        'job_prior':dict(EMPTY), 'job_mission':copy.deepcopy(job['mission']),
                        # The first run used the V5 ledger before its intent was known.
                        'ledger':copy.deepcopy(budget.data['entries']) if budget else []}
                    # The intent was learned during this segment: its partial
                    # response must announce automatic continuation too.
                    run.data = {**run.data, 'answer':run.data.get('answer','').replace(
                        workflow.RELAUNCH_REQUEST,workflow.MISSION_NOTE)}
                    data['messages'] = [{**m,'content':m.get('content','').replace(
                        workflow.RELAUNCH_REQUEST,workflow.MISSION_NOTE)} if m.get('run_id')==rid else m
                        for m in data.get('messages', [])]
                    row.data = data
                    db.commit()
        return super().after_run(owner,sid,rid,dispatch=dispatch)

    def writing_job(self, owner, sid):
        result = super().writing_job(owner, sid)
        with self.session_factory() as db:
            row = self.owned(db, owner, sid)
            job = row.data.get("writing_job")
            if result.get("job") and job:
                from .writing_job import progress_view
                result["job"]["mission_progress"] = progress_view(job, row.data)
        return result

    def document_action(self, owner, sid, payload):
        """Approving the plan lets a mission waiting for it continue automatically."""
        result = super().document_action(owner, sid, payload)
        if getattr(payload, "action", None) == "approve_outline":
            from .writing_job import APPROVAL_PAUSE
            with self.session_factory() as db:
                lock_rows(db, sid)
                row = self.owned(db, owner, sid)
                job = copy.deepcopy(row.data.get("writing_job"))
                if job and job.get("status") == "paused" and job.get("paused_reason") == APPROVAL_PAUSE:
                    job.update(status="waiting", paused_reason=None)
                    data = copy.deepcopy(row.data)
                    from .writing_progress import refresh_writing, reopen_after_plan_change
                    progress = refresh_writing(data)
                    if progress and progress.get("status") == "plan_changed" and job.get("progress"):
                        # The consultant edited the plan before validating it: the mission
                        # resumes the same request over the validated plan, never the old one.
                        reopened = reopen_after_plan_change(data, progress)
                        if reopened is None:
                            from .writing_job import stop
                            job = stop(job, "failed", error="Rédaction automatique arrêtée : les sections demandées ne "
                                "figurent plus dans le plan validé ; les sections enregistrées sont conservées.")
                        else:
                            reopened["request_message_id"] = job.get("request_message_id")
                            reopened["include_conclusion"] = bool(progress.get("include_conclusion"))
                            refresh_writing(data)
                            job["progress"] = {"id": reopened["id"], "target_ids": list(reopened["target_ids"]),
                                "plan_revision": reopened.get("plan_revision"), "mode": reopened["mode"],
                                "baseline_draft_ids": copy.deepcopy(reopened.get("baseline_draft_ids", {}))}
                            row.data = data
                    self._write_job(db, row, job)
                else:
                    db.rollback()
                    job = None
            if job and job.get("status") == "waiting":
                self.dispatch_job(owner, sid, job["id"], force=True)
            if job:
                result = self.read(owner, sid)
        return result

    def recover_expired(self, owner, sid):
        super().recover_expired(owner, sid)
        with self.session_factory() as db:
            row = self.owned(db, owner, sid, lock=True)
            runs = db.scalars(select(ScholarV4Run).where(ScholarV4Run.session_id == sid,
                ScholarV4Run.owner_id == owner, ScholarV4Run.status == "partial"))
            for run in runs:
                if run.data.get("reason") == "worker_interrupted" and not run.data.get("resumable_run_id"):
                    scope = Scope(owner, sid, row.data.get("project_id"))
                    from .outcomes import stopped,with_notice
                    from . import workflow
                    data = copy.deepcopy(row.data)
                    current = workflow.current_request(data).get('id') == run.data.get('request_message_id')
                    saved = workflow.writing_result(data) if current and workflow.writing_progress(data) else {}
                    result = with_notice(saved,stopped('worker_interrupted',{}))
                    run.data = {**run.data, "resumable_run_id": run.id,
                        'answer':result['answer'], 'outcome':result['outcome'],
                        "remaining_tasks": project(row.data, scope=scope)["tasks"]}
                    if current:
                        # The chat renders session.messages, not run.answer. A
                        # repeated read of an expired run must not append twice.
                        messages=data.setdefault('messages',[])
                        message=next((m for m in messages if m.get('run_id')==run.id and m.get('role')=='assistant'),None)
                        if message is None:
                            messages.append({'id':'stop_'+run.id,'role':'assistant','run_id':run.id,
                                'content':result['answer'],'partial':True,'citations':[]})
                        else:
                            message['content']=with_notice({'answer':message['content']},stopped('worker_interrupted',{}))['answer']
                            message['partial']=True
                            run.data={**run.data,'answer':message['content']}
                        row.data=data
                        sync_records(db,row)
            db.commit()

    def article_memory(self, owner, sid, query, **kwargs):
        current = self.relevant_memory(owner, query, super().article_memory(owner, sid, query, **kwargs), **kwargs)
        existing = current.get("articles", []) if isinstance(current, dict) else current
        if existing:
            return current
        from .memory import V4ReadOnlyMemory
        return self.relevant_memory(owner, query,
            V4ReadOnlyMemory(self, sid).article_memory(owner, sid, query, **kwargs), **kwargs)

    def memory_term_weights(self, owner, terms):
        """Specificity of each query term in this owner's stored works (V4 and V5)."""
        import math
        from sqlalchemy import func, or_
        from agents.EnnoScholarV4.session import ScholarV4Article as V4Article, ScholarV4Session as V4Session
        from .session import ScholarV4Article as V5Article
        counts, total = dict.fromkeys(terms, 0), 0
        with self.session_factory() as db:
            for article, session in ((V4Article, V4Session), (V5Article, ScholarV4Session)):
                owned = select(func.count()).select_from(article).join(session, session.id == article.session_id).where(
                    session.owner_id == owner)
                title = func.lower(article.data["title"].as_string())
                abstract = func.lower(article.data["abstract"].as_string())
                try:
                    rows = db.scalar(owned) or 0
                    found = {t: db.scalar(owned.where(or_(title.contains(t, autoescape=True),
                        abstract.contains(t, autoescape=True)))) or 0 for t in terms}
                except Exception:
                    db.rollback()  # A deployment without V4 tables: weights from V5 alone.
                    continue
                total += rows
                for term in terms:
                    counts[term] += found[term]
        return {t: math.log((total+1)/(counts[t]+1)) for t in terms}

    def relevant_memory(self, owner, query, found, *, exact=False, **_):
        """Keep stored works that share the query's specific vocabulary.

        Run 97df6cf6: V4's memory accepts a single shared word ("high",
        "current", "stability"), so 12 mesh/segmentation papers entered a CO2
        search. A work is kept when it contains one of the query's most specific
        terms or carries at least 30 % of its weighted vocabulary. Exact lookups
        (ID, DOI, title) and stored records are never filtered or changed.
        """
        if exact or not found:
            return found
        from agents.EnnoScholarV4.store import query_terms, tokens
        terms = sorted(query_terms(query))[:16]
        if len(terms) < 2:
            return found
        weights = self.memory_term_weights(owner, terms)
        total = sum(weights.values()) or 1.
        ranked = sorted(terms, key=lambda t: -weights[t])
        anchors = set(ranked[:max(1, -(-len(terms)//3))])
        def keep(title, abstract=""):
            words = set(tokens(f"{title} {abstract}"))
            matched = [t for t in terms if t in words]
            return bool(anchors & set(matched)) or sum(weights[t] for t in matched)/total >= .3
        packets = found.get("articles", []) if isinstance(found, dict) else found
        kept = [p for p in packets if keep(p["article"].get("title", ""), p["article"].get("abstract", ""))]
        if not isinstance(found, dict):
            return kept
        index = [r for r in found.get("candidate_index", []) if keep(r.get("title", ""))]
        return {**found, "articles": kept, "candidate_index": index,
            "filtered_out": len(packets)-len(kept)+len(found.get("candidate_index", []))-len(index)}

    def execute(self, owner, sid, rid, *, driven=False):
        scope = None
        meter = None
        config = self.config
        if config.writing_job_enabled:
            from dataclasses import replace
            from .writing_job import mission_limits
            limits = mission_limits()
            # A run whose intent is learned from Sol may become the first segment.
            # It must already respect the authorised mission cap before its first call.
            config = replace(config, max_cost_usd=min(config.max_cost_usd,config.writing_job_budget_usd),
                max_tokens=min(config.max_tokens,limits['max_tokens']),
                timeout_seconds=min(config.timeout_seconds,limits['max_seconds']))
        try:
            with self.session_factory() as db:
                row = self.owned(db, owner, sid, lock=True)
                run = db.get(ScholarV4Run, rid)
                if row.active_run_id != rid or run.cancel_requested:
                    raise SessionBusy("Exécution interrompue.")
                run.status = "running"
                scope = Scope(owner, sid, row.data.get("project_id"))
                request = run.data["request"]
                run_data = copy.deepcopy(run.data)
                db.commit()
            repo = ApplicationRepository(self, scope, run_id=rid)
            inner = self.meter(owner, sid, rid, repo.load(scope)[1])
            if run_data.get("writing_job_id"):
                # A mission segment: each call is admitted against what remains of the
                # mission's dollar budget (V4 JobMeter), and the segment's own caps never
                # exceed what remains of the mission (dollars and tokens).
                from .writing_job import segment_config
                config = segment_config(self.config, {"budget_usd": run_data["job_budget_usd"],
                    "spent": run_data["job_prior"], "mission": run_data.get("job_mission")})
                # RunMeter checks all canonical rows of the mission under the session
                # lock. The inherited job ledger is projected in that SAME commit.
            meter = RunMeter(self, scope, rid, config, inner=inner)
            def cancelled():
                with self.session_factory() as db:
                    run = db.execute(select(ScholarV4Run.cancel_requested, ScholarV4Run.status).where(
                        ScholarV4Run.id == rid, ScholarV4Run.owner_id == owner)).first()
                    holder = db.scalar(select(ScholarV4Session.active_run_id).where(ScholarV4Session.id == sid))
                return not run or run.cancel_requested or holder != rid
            def check():
                if cancelled():
                    raise StopResearch("cancelled")
                if meter.snapshot()['remaining_seconds'] <= 0:
                    raise StopResearch("job_budget")
            def activity(event):
                # Science is saved by the repository in the same commit as its
                # receipt. Activity writes only the run; never a stale session copy.
                with self.session_factory() as db:
                    lock_rows(db, run_id=rid)
                    run = db.get(ScholarV4Run, rid)
                    if run:
                        run.data = {**run.data, "activity": [*run.data.get("activity", []), event][-200:]}
                        db.commit()
            adapter = ScientificAdapter(config, meter, sources=self.sources, emit=activity, check=check,
                article_memory=lambda query, **kw: self.article_memory(owner, sid, query, **kw))
            sol = ResponsesSol(config, meter, client=self.client, emit=activity, cancelled=cancelled)
            segment = run_data.get("segment")
            if segment:
                from .writing_job import scope_problem
                problem = scope_problem(segment, repo.load(scope)[1])
                if problem:
                    # The pinned scope changed: nothing is drafted and no call is made.
                    usage = meter.snapshot()
                    self.finish(owner, sid, rid, None, {"answer": problem, "partial": True, "reason": "writing_scope_changed",
                        "engine": "langgraph_v5", "usage": {"model": "gpt-6.1-sol", "estimated_cost_usd": 0, **{k: usage[k]
                            for k in ("input_tokens", "output_tokens", "uncertain_usd", "uncertain_tokens", "tools", "seconds", "llm_calls")}}},
                        "partial")
                    return
            with self.checkpointer_factory(self.finance_engine()) as saver:
                runner = Runner(scope, repo, adapter, sol, saver)
                runner.config["configurable"]["thread_id"] += ":"+rid
                snapshot = runner.graph.get_state(runner.config)
                if snapshot.values:
                    # A stop on an uncertain call continues only after its explicit reconciliation.
                    out = runner.resume(reconciled=bool(run_data.get("v5_reconciliations")))
                else:
                    # Initialise the same documentary references/instructions as V4.
                    revision, data = repo.load(scope)
                    from .instructions import capture_instructions, current_request
                    from .references import resolve
                    from .research import original_progress
                    t = adapter.toolkit(data)
                    latest = current_request(data) or {}
                    capture_instructions(data, latest, t.registry)
                    refs = resolve(data, t.registry, request)
                    data["discovery_mode"] = "original"
                    data["conversation_references"] = refs
                    data["turn_goal"] = {"references": refs}
                    data["current_turn_article_ids"] = list(refs.get("article_ids", []))
                    data["v5_exchange"] = []
                    data["v5_tool_counters"] = {}
                    data.pop("v5_unvalidated_draft", None)
                    data.pop('v5_provider_failure',None)
                    original_progress(data, latest)
                    from .references import opens_literature_search
                    continuation = bool(re.search(r"\b(?:poursui\w*|continu\w*|reprends|repren\w*|compl[ée]t\w*|manquants?|autres)\b", request, re.I))
                    data["v5_new_search"] = bool(opens_literature_search(request) and not continuation and not refs.get("article_ids"))
                    data["v5_explicit_context_ids"] = list(refs.get("article_ids", []))
                    data["v5_memory_updated"] = False
                    # Plan and manuscript obligations of this request (V4 writing_progress).
                    from . import workflow
                    data["v5_request_start"] = {"request_id": latest.get("id"),
                        "article_ids": list(data.get('articles', {})),
                        "plan_revision": data.get("outline", {}).get("plan_revision"),
                        "plan_approved": workflow.plan_approved(data)}
                    if (data.get('v5_request_knowledge') or {}).get('request_id') != latest.get('id'):
                        data["v5_request_knowledge"] = {"request_id": latest.get("id"), "keys": [], "failed_sources": []}
                    data.pop("v5_section_attempts", None)
                    if segment and segment.get("progress_id"):
                        # A mission segment resumes the pinned progress and goal of the
                        # consultant's request; it never opens a new drafting progress.
                        data["writing_segment"] = copy.deepcopy(segment)
                        if segment.get("goal"):
                            data["turn_goal"] = copy.deepcopy(segment["goal"])
                    else:
                        workflow.begin(data, request)
                    # Initialisation itself is a durable local operation.
                    init_id = rid+":initialise"
                    repo.begin(scope, init_id, revision, "initialise", {"request_ref": latest.get("id")})
                    if repo.claim(scope, init_id):
                        repo.finish(scope, init_id, revision, data, {"initialised": True})
                    out = runner.start()
                graph_state = runner.graph.get_state(runner.config).values
            _, data = repo.load(scope)
            interrupted = out.get("__interrupt__", [])
            reason = interrupted[0].value["reason"] if interrupted else graph_state.get("reason")
            saved = copy.deepcopy(data.get("last_response", {})) if graph_state.get("response_ref") else {}
            usage = meter.snapshot()
            from .outcomes import stopped,with_notice
            with self.session_factory() as db:
                refusal = db.get(ScholarV4Run,rid).data.get('admission_refusal')
            failure = data.get('v5_provider_failure')
            from . import workflow
            notice = stopped(reason,usage,failure,refusal) if failure or graph_state.get('status')!='completed' else None
            if not saved and workflow.writing_progress(data):
                # Interrupted drafting: the saved sections are published as they are.
                saved = workflow.writing_result(data)
                if failure and not any(s.get('drafts') and s['drafts'][-1]['id'] != run_data.get('v5_saved_before',{}).get(s['id'])
                        for s in data.get('outline',{}).get('sections',[])):
                    # An error is not a new delivery of the old manuscript. The
                    # factual drafting header is enough when no section was saved.
                    saved['answer']=saved['answer'].split('\n\n---\n\n',1)[0]
            if run_data.get("writing_job_id") and saved.get("answer"):
                # The mission continues on its own: the consultant is never asked to relaunch it.
                saved = {**saved, "answer": saved["answer"].replace(workflow.RELAUNCH_REQUEST, workflow.MISSION_NOTE)}
            elif saved.get("answer") and workflow.RELAUNCH_REQUEST in saved["answer"] and not self.config.writing_job_enabled:
                saved = {**saved, "answer": saved["answer"].replace(workflow.RELAUNCH_REQUEST, workflow.RELAUNCH_REQUEST
                    + " Rédaction automatique non activée : aucun plafond financier de mission n'est autorisé "
                    "(ENNOSCHOLAR_V5_MISSION_BUDGET_USD).")}
            if workflow.writing_progress(data) and not workflow.plan_approved(data) and saved.get("answer"):
                saved = {**saved, "answer": saved["answer"]+"\n\n*Plan à valider : aucune section n'est rédigée avant "
                    "votre validation ; la préparation scientifique (fiches, preuves, comparaisons) est conservée.*"}
            elif saved.get("answer") and workflow.plan_note(data) and workflow.plan_note(data) not in saved["answer"]:
                # A plan request always says what became of the plan: modified (to validate again) or unchanged.
                saved = {**saved, "answer": saved["answer"]+"\n\n"+workflow.plan_note(data)}
            if not saved and notice:
                # Current, actually received material comes before generic
                # progress counts, for cancellations and resource stops alike.
                from .consulted import partial_response
                saved = partial_response(data)
            if not saved and not (notice and (failure or reason not in
                    {'stagnation','cancelled','consultant_review','requested_pause','model_requested_pause','finalization_failed'})):
                # Factual partial response: no extra paid synthesis and no historical
                # analyses injected into a new topic on a safety stop.
                p = project(data, scope=scope)
                if (data.get("turn_goal") or {}).get("deliverable") in {"deep_reading", "article_analysis"}:
                    saved = {"answer": Runner.partial_message({"progress": p, 'usage':graph_state.get('usage', {})}, reason), "citations": [],
                        "partial": True, "coverage_sufficient": False}
                else:
                    saved = self.selection_fallback(adapter, data, p, reason)
            if notice:
                # Messages must not alter the mission driver's retry decisions.
                # Resource notices were previously bypassed during drafting.
                if not failure and workflow.writing_progress(data):
                    notice={k:v for k,v in notice.items() if k!='diagnostic'}
                saved=with_notice(saved,notice)
                if failure or reason in {'job_budget','uncertain_model_call','uncertain_tool_call'}:
                    saved['answer']=saved['answer'].replace(workflow.RELAUNCH_REQUEST,'')
                if failure:
                    saved['answer']=saved['answer'].replace(workflow.MISSION_NOTE,'')
                    operations=graph_state.get('usage',{}).get('scientific_operations',{})
                    if operations.get('saved_analyses'):
                        saved['answer']+=f"\n\n{operations['saved_analyses']} opération(s) de sauvegarde ou d’enrichissement de fiche effectuée(s) pendant cette exécution ; ces fiches restent consultables dans la bibliothèque."
            usage = meter.snapshot()
            result = {**saved, "engine": "langgraph_v5", "reason": reason,
                "partial": graph_state.get("status") != "completed", "resumable_run_id": rid if interrupted or (
                    graph_state.get('status') == 'partial' and graph_state.get('progress', {}).get('tasks')) else None,
                "remaining_tasks": graph_state.get("progress", {}).get("tasks", {}),
                "usage": {"model": "gpt-6.1-sol", "estimated_cost_usd": usage["cost_usd"],
                    **{k: usage[k] for k in ("input_tokens", "output_tokens", "uncertain_usd", "uncertain_tokens", "tools", "seconds", "llm_calls")}},
                "budget_limits": usage["limits"]}
            result['budget_status'] = {k:usage[k] for k in ('cost_usd', 'reserved_usd', 'reserved_tokens',
                'unresolved_usd', 'unresolved_tokens', 'remaining_tokens')}
            result['call_metrics'] = [{k:e.get(k) for k in ('id','target','status','reserved_tokens','reserved_usd',
                'cost_usd','input_tokens','output_tokens','token_categories','request_metrics','admitted_at','sent_at','closed_at')}
                for e in usage['entries']]
            result['resource_scopes'] = {k:usage[k] for k in ('session_resources','session_limit_usd','mission_resources','mission_limits','remaining_seconds')}
            self.finish(owner, sid, rid, data, result, "cancelled" if reason == "cancelled" else "partial" if result["partial"] else "completed")
        except Exception as exc:
            # Previously committed science and reservations survive all failures.
            data = ApplicationRepository(self, scope).load(scope)[1] if scope else None
            usage = meter.snapshot() if meter is not None else {}
            from .outcomes import stopped,with_notice
            from . import workflow
            saved = workflow.writing_result(data) if data and workflow.writing_progress(data) else {}
            notice = stopped('v5_interrupted:'+type(exc).__name__,usage)
            self.finish(owner, sid, rid, data, {**with_notice(saved,notice),
                "partial": True, "reason": "v5_interrupted:"+type(exc).__name__, "resumable_run_id": rid,
                "remaining_tasks": project(data, scope=scope)["tasks"] if data and scope else {},
                "usage": {"model": "gpt-6.1-sol", "estimated_cost_usd": usage.get("cost_usd", 0),
                    **{k: usage.get(k, 0) for k in ("input_tokens", "output_tokens", "uncertain_usd", "uncertain_tokens", "tools", "seconds", "llm_calls")}}}, "partial")
        finally:
            self.slots.release()
        try:
            self.after_run(owner, sid, rid, dispatch=not driven)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("EnnoScholar V5 mission evaluation failed type=%s", type(exc).__name__)

    @staticmethod
    def selection_fallback(adapter, data, progress, reason):
        """V4's publication.fallback (no model call), preceded by factual counts.

        Run 97df6cf6 showed "0 publications proposées" although Sol had proposed
        six works (two verified) in a refused synthesis. Discovered notices are
        never presented as validated: each count says what was established.
        """
        from .publication import fallback
        toolkit = adapter.toolkit(data)
        result = fallback(toolkit, reason)
        toolkit.save()
        articles = data.get("articles", {})
        proposed_by_sol = [aid for aid in toolkit.final_verification_ids if aid in articles]
        verified = [aid for aid in proposed_by_sol if articles[aid].get("verified") and not articles[aid].get("metadata_errors")]
        with_cards = [aid for aid in result.get("primary_article_ids", []) if articles.get(aid, {}).get("analysis")]
        memory = sum(any(n.get("kind") == "reused_science" for n in a.get("provider_notices", [])) for a in articles.values())
        tasks = progress["tasks"].values()
        remaining = sum(t["kind"] in {"evaluate_candidate", "read_abstract", "read_local", "recover_document", "save_analysis"} for t in tasks)
        target = progress["counts"]["target"]
        lines = [f"Notices réunies dans cette conversation : {len(articles)}"+(f" (dont {memory} issues de la mémoire des conversations précédentes)" if memory else "")+" ; une notice découverte n'est pas une publication validée.",
            (f"Publications retenues par Sol dans sa dernière proposition, refusée à la validation : {len(proposed_by_sol)}, "
                f"dont {len(verified)} à bibliographie vérifiée." if proposed_by_sol else
                "Aucune proposition de publications n'a atteint la validation finale."),
            f"Publications restituées ci-dessous avec une fiche d'analyse et ses preuves : {len(with_cards)}.",
            f"Candidats ou lectures restant à examiner : {remaining}"+(f" ; objectif demandé : {target} publications." if target else ".")]
        return {**result, "answer": "\n".join("- "+line for line in lines)+"\n\n"+result["answer"],
            "selection_counts": {"collected": len(articles), "from_memory": memory, "proposed_by_sol": len(proposed_by_sol),
                "verified": len(verified), "restituted_with_analysis": len(with_cards), "remaining": remaining, "target": target}}

    def resume_run(self, owner, rid):
        self.recover_expired_for_run(owner, rid)
        if not self.slots.acquire(blocking=False):
            raise SessionBusy("Les exécutions V5 sont occupées.")
        try:
            with self.session_factory() as db:
                run = db.scalar(select(ScholarV4Run).where(ScholarV4Run.id == rid, ScholarV4Run.owner_id == owner))
                if run is None:
                    raise SessionNotFound("Exécution V5 introuvable.")
                sid = run.session_id
                row = self.owned(db, owner, sid, lock=True)
                if run.status not in {"partial", "cancelled"} or not run.data.get("resumable_run_id"):
                    raise SessionBusy("Cette exécution n'est pas en attente de reprise.")
                if row.active_run_id:
                    raise SessionBusy("Une exécution est déjà active.")
                # Old pending decisions cannot be applied to a newer consultant request.
                latest = next((m for m in reversed(row.data.get("messages", [])) if m["role"] == "user"), {})
                if latest.get("id") != run.data.get("request_message_id"):
                    raise SessionBusy("Une nouvelle demande a remplacé cette exécution.")
                budget = db.get(Budget, rid)
                if budget and any(e["status"] in {"reserved", "uncertain"} for e in budget.data["entries"]):
                    raise SessionBusy("Appel fournisseur incertain : rapprochement requis, aucune répétition automatique.")
                if run.data.get("reason") == "job_budget":
                    raise SessionBusy("Budget épuisé : une reprise ne peut pas le réinitialiser.")
                row.active_run_id, row.lease_until = rid, datetime.utcnow()+timedelta(seconds=self.config.timeout_seconds+30)
                run.status, run.cancel_requested = "queued", False
                db.commit()
            self.pool.submit(self.execute, owner, sid, rid)
            return self.run(owner, rid)
        except BaseException:
            self.slots.release()
            raise

    def reconcile_run(self, owner, rid):
        """Explicit consultant reconciliation of the uncertain paid calls of a stopped run.

        Each call whose consumption the provider never reported (sent, then the stream
        closed, or a crash after admission) is closed as settled at its full reservation:
        its maximum possible cost stays counted, so a later resume can never bill it
        twice unaccounted. Nothing is replayed or started here, and saved science, receipts
        and checkpoints are untouched; resuming remains a separate explicit action."""
        from .resources import ACTIVE
        with self.session_factory() as db:
            run = db.scalar(select(ScholarV4Run).where(ScholarV4Run.id == rid, ScholarV4Run.owner_id == owner))
            if run is None:
                raise SessionNotFound("Exécution V5 introuvable.")
            row = self.owned(db, owner, run.session_id, lock=True)
            if row.active_run_id or run.status not in {"partial", "cancelled", "failed"}:
                raise SessionBusy("Rapprochement impossible pendant une exécution active.")
            budget = db.get(Budget, rid)
            data = copy.deepcopy(budget.data) if budget else {"entries": []}
            now = datetime.utcnow().isoformat()+"+00:00"
            closed = []
            for entry in data["entries"]:
                if entry.get("status") not in ACTIVE:
                    continue
                closed.append({"call_id": entry["id"], "previous_status": entry["status"],
                    "counted_usd": float(entry.get("reserved_usd", 0)), "counted_tokens": int(entry.get("reserved_tokens", 0)),
                    "sent": bool(entry.get("sent")), "reason": entry.get("reason")})
                entry.update(status="settled", cost_usd=float(entry.get("reserved_usd", 0)),
                    input_tokens=int(entry.get("reserved_tokens", 0)), output_tokens=0, response_received=False,
                    closed_at=entry.get("closed_at") or now,
                    reconciliation={"method": "conservative_full_reservation", "by": "consultant", "at": now,
                        "previous_status": closed[-1]["previous_status"]})
            if not closed:
                return {"run_id": rid, "reconciled_calls": [], "counted_usd": 0., "resumable": bool(run.data.get("resumable_run_id"))}
            budget.data = data
            failure = (row.data or {}).get("v5_provider_failure")
            if failure:
                # The failure stays in the run's history; a resumed run starts without it.
                row.data = {k: v for k, v in row.data.items() if k != "v5_provider_failure"}
            record = {"at": now, "calls": closed, "provider_failure": failure,
                "counted_usd": round(sum(c["counted_usd"] for c in closed), 7)}
            usage = balance(data)
            run.data = {**run.data, "ledger": copy.deepcopy(data["entries"]), "resource_usage": usage,
                "budget_status": {**run.data.get("budget_status", {}), "cost_usd": usage["confirmed_usd"],
                    "reserved_usd": 0., "reserved_tokens": 0, "unresolved_usd": 0., "unresolved_tokens": 0},
                "v5_reconciliations": [*run.data.get("v5_reconciliations", []), record]}
            job_id = run.data.get("writing_job_id")
            job = (row.data or {}).get("writing_job")
            if job_id and job and job.get("id") == job_id:
                job = copy.deepcopy(job)
                run.data = {**run.data, "mission_usage": refresh_job_resources(db, row.id, job)}
                row.data = {**row.data, "writing_job": job}
            db.commit()
            return {"run_id": rid, "reconciled_calls": closed, "counted_usd": record["counted_usd"],
                "resumable": bool(run.data.get("resumable_run_id"))}

    def recover_expired_for_run(self, owner, rid):
        with self.session_factory() as db:
            run = db.scalar(select(ScholarV4Run).where(ScholarV4Run.id == rid, ScholarV4Run.owner_id == owner))
            if run is None:
                raise SessionNotFound("Exécution introuvable.")
            sid = run.session_id
        self.recover_expired(owner, sid)

_service = None
_guard = threading.Lock()

def get_service():
    if os.getenv("ENNOSCHOLAR_V5_TEST_ENABLED") != "1":
        raise ConfigurationError("V5 est réservée à l'environnement de test explicite.")
    global _service
    with _guard:
        if _service is None:
            from db.database import SessionLocal
            _service = ScholarV5Service(SessionLocal)
    return _service


def start_mission_watchdog(service=None):
    """V4's job watchdog, bound to the V5 service (its own module state)."""
    return base.start_watchdog(service or get_service())


def stop_mission_watchdog():
    return base.stop_watchdog()
