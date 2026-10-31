"""Application SQLAlchemy persistence, using the configured EnnoSmart database.

Scientific state + operation receipt share one commit. Checkpoints reference it;
PDF bytes never enter either graph state or operation receipts.
"""
from __future__ import annotations
import copy
import json
import time
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from sqlalchemy import Column, String, Integer, JSON, ForeignKey, select, update
from db.database import Base
from .session import ScholarV4Session, ScholarV4Run, lock_rows, sync_records
from .finance import BudgetRefused
from experiments.ennoscholar_v5.repository import RevisionConflict
from .resources import balance, combine, apply_mission_usage, remaining_seconds

class Revision(Base):
    __tablename__ = "scholar_v5_revisions"
    session_id = Column(String(40), ForeignKey("scholar_v5_sessions.id"), primary_key=True)
    revision = Column(Integer, nullable=False, default=0)

class Operation(Base):
    __tablename__ = "scholar_v5_operations"
    id = Column(String(64), primary_key=True)
    scope = Column(String(64), nullable=False, index=True)
    revision = Column(Integer, nullable=False)
    kind = Column(String(24), nullable=False)
    request = Column(JSON, nullable=False)
    status = Column(String(20), nullable=False)
    response = Column(JSON)

class Budget(Base):
    __tablename__ = "scholar_v5_budgets"
    run_id = Column(String(40), ForeignKey("scholar_v5_runs.id"), primary_key=True)
    data = Column(JSON, nullable=False)


def resource_rows(db, session_id):
    """Small financial rows only, never load scientific documents to sum costs."""
    return [(rid, data, job_id) for rid, data, job_id in db.execute(select(
        Budget.run_id, Budget.data, ScholarV4Run.data['writing_job_id'].as_string())
        .join(ScholarV4Run, ScholarV4Run.id == Budget.run_id)
        .where(ScholarV4Run.session_id == session_id).order_by(ScholarV4Run.created_at,ScholarV4Run.id))]


def refresh_job_resources(db, session_id, job, rows=None):
    """Canonical Budget rows also repair late settlements in completed segments."""
    rows = resource_rows(db, session_id) if rows is None else rows
    selected = [(rid, data) for rid, data, jid in rows if jid == job['id']]
    by_id = {rid: balance(data) for rid, data in selected}
    # Old segments without a canonical row retain their recorded commitments.
    for segment in job.get('segments', []):
        if segment['run_id'] not in by_id:
            by_id[segment['run_id']] = segment.get('resources') or balance({'entries': [
                {'status': 'settled', 'cost_usd': (segment.get('spent') or {}).get('confirmed_usd', 0),
                 'input_tokens': segment.get('tokens', 0)},
                {'status': 'uncertain', 'reserved_usd': (segment.get('spent') or {}).get('uncertain_usd', 0),
                 'reserved_tokens': segment.get('uncertain_tokens', 0)}]})
    usage = combine(by_id.values())
    m = job.setdefault('mission', {})
    first_limits = selected[0][1]['limits'] if selected else {}
    m.setdefault('max_tools', first_limits.get('tools', 48))
    m.setdefault('max_web', first_limits.get('web', 4))
    apply_mission_usage(job, usage)
    completed = []
    for segment in job.get('segments', []):
        r = by_id[segment['run_id']]
        segment['resources'] = dict(r)
        segment['tokens'] = r['tokens']
        segment['spent'] = {k: r[k] for k in ('confirmed_usd', 'reserved_usd', 'uncertain_usd')}
        completed.append(r)
    # V4's evaluator adds the current segment once; only completed ones belong here.
    spent = combine(completed)
    job['spent'] = {k: spent[k] for k in ('confirmed_usd', 'reserved_usd', 'uncertain_usd')}
    return usage

class ApplicationRepository:
    def __init__(self, service, scope, run_id=None):
        self.service, self.factory, self.scope = service, service.session_factory, scope
        self.run_id = run_id

    def owned(self, db, scope, lock=False):
        if scope != self.scope:
            raise PermissionError("Scope mismatch")
        return self.service.owned(db, scope.owner_id, scope.session_id, lock=lock)

    def load(self, scope):
        with self.factory() as db:
            row = self.owned(db, scope)
            rev = db.get(Revision, scope.session_id)
            return (rev.revision if rev else 0), copy.deepcopy(row.data)

    @staticmethod
    def packet(op):
        return None if op is None else {k: copy.deepcopy(getattr(op, k)) for k in
            ("revision", "kind", "request", "status", "response")}

    def operation(self, scope, opid):
        with self.factory() as db:
            self.owned(db, scope)
            op = db.get(Operation, opid)
            return self.packet(op) if op and op.scope == scope.key else None

    def begin(self, scope, opid, revision, kind, request):
        with self.factory() as db:
            self.owned(db, scope, lock=True)
            old = db.get(Operation, opid)
            if old:
                if old.scope != scope.key or old.request != request or old.kind != kind:
                    raise RevisionConflict("Operation identity changed")
                return self.packet(old)
            rev = db.get(Revision, scope.session_id)
            if not rev:
                rev = Revision(session_id=scope.session_id, revision=0)
                db.add(rev)
            if rev.revision != revision:
                raise RevisionConflict("Science changed since checkpoint")
            db.add(Operation(id=opid, scope=scope.key, revision=revision, kind=kind,
                             request=copy.deepcopy(request), status="prepared"))
            db.commit()

    def claim(self, scope, opid):
        with self.factory() as db:
            self.owned(db, scope)
            changed = db.execute(update(Operation).where(Operation.id == opid,
                Operation.scope == scope.key, Operation.status == "prepared").values(status="running"))
            db.commit()
            return changed.rowcount == 1

    def finish(self, scope, opid, revision, state, response):
        with self.factory() as db:
            row = self.owned(db, scope, lock=True)
            if self.run_id is not None and row.active_run_id != self.run_id:
                raise RevisionConflict("A stale worker cannot overwrite a newer conversation")
            rev, op = db.get(Revision, scope.session_id), db.get(Operation, opid)
            if not rev or rev.revision != revision or not op or op.scope != scope.key or op.status != "running":
                raise RevisionConflict("Receipt or science revision not owned")
            saved = copy.deepcopy(state)
            # The consultant's selections remain authoritative during a running model call.
            for aid, a in row.data.get("articles", {}).items():
                if aid in saved.get("articles", {}):
                    for key in ("selected", "selection_override"):
                        if key in a:
                            saved["articles"][aid][key] = a[key]
            saved["selection_decisions"] = row.data.get("selection_decisions", [])
            # The drafting mission is the service's state, never a run's copy.
            for key in ("writing_job", "writing_job_history", "v5_session_budget"):
                if key in row.data:
                    saved[key] = row.data[key]
                else:
                    saved.pop(key, None)
            row.data = saved
            rev.revision += 1
            op.status, op.response = "complete", copy.deepcopy(response)
            if op.kind == "action":
                sync_records(db, row)
            db.commit()
            return revision+1

    def completed_actions(self, scope):
        with self.factory() as db:
            self.owned(db, scope)
            return [self.packet(op) for op in db.scalars(select(Operation).where(
                Operation.scope == scope.key, Operation.kind == "action", Operation.status == "complete")
                .order_by(Operation.revision))]

CALIBRATION_MARGIN, CALIBRATION_FLOOR, CALIBRATION_SAMPLES = 1.15, 0.5, 8
WEB_MARGIN, WEB_FLOOR_TOKENS = 3, 32000


def _web_used(entry):
    parts = (entry.get('meta') or {}).get('parts')
    if parts is None:
        return None if entry.get('web') else False   # offered without a receipt: unknown
    return parts.get('web_calls', 0) > 0


def admission_sizing(history, point_input, web_reserve):
    """Admission size of the next call from the measured calls of this session (P2, P3).

    P2: the offline estimate is scaled by the largest actual/estimated input ratio of the
    recent calls without hosted Web, plus a 15 % margin, never below half the estimate; a
    ratio above 1 raises it. If a calibrated admission was ever exceeded, the raw estimate
    is used again. P3: the hosted-Web reserve is three times the largest Web addition
    observed (actual input minus its calibrated estimate), between 32 000 tokens and the
    configured reserve; without a measurement the configured reserve applies. Caps and
    settlements are unchanged: settlement always uses the reported usage."""
    import math
    samples = [e for e in history if e.get('status') == 'settled' and (e.get('request_metrics') or {}).get('point_input_tokens')
        and e.get('input_tokens')]
    samples.sort(key=lambda e: e.get('admitted_at') or '')
    samples = samples[-CALIBRATION_SAMPLES:]
    def over(e):
        metrics = e['request_metrics']
        sizing = metrics.get('admission_sizing') or {}
        if not sizing.get('calibrated'):
            return False
        allowance = metrics.get('admission_input_tokens', 0) + (sizing.get('web_tokens', 0) if _web_used(e) is not False else 0)
        return e['input_tokens'] > allowance
    exceeded = any(over(e) for e in samples)
    plain = [e['input_tokens']/e['request_metrics']['point_input_tokens'] for e in samples if _web_used(e) is False]
    ratio = max(plain) if plain else None
    result = {'point_input_tokens': point_input, 'samples': len(plain), 'calibrated': False,
        'input_tokens': point_input, 'web_tokens': web_reserve, 'web_samples': 0}
    if ratio is not None and not exceeded:
        factor = max(CALIBRATION_FLOOR, ratio*CALIBRATION_MARGIN)
        result.update(calibrated=True, ratio=round(ratio, 4), factor=round(factor, 4),
            input_tokens=max(1, math.ceil(point_input*factor)))
    extras = [max(0, e['input_tokens'] - e['request_metrics']['point_input_tokens']*(ratio or 1.0))
        for e in samples if _web_used(e) is True]
    if extras and ratio is not None and not exceeded:
        result.update(web_samples=len(extras), observed_web_tokens=math.ceil(max(extras)),
            web_tokens=min(web_reserve, max(min(WEB_FLOOR_TOKENS, web_reserve), math.ceil(max(extras)*WEB_MARGIN))))
    return result


class RunMeter:
    """Durable hard fence for all Responses calls of the SAME execution.

    Reserves tokens and actual priced upper bounds before transport, including Web.
    Unknown calls retain both reservations; resuming cannot reset or refill them.
    The original organisation meter remains an inner financial fence.
    """
    def __init__(self, service, scope, rid, config, inner=None):
        self.service, self.factory, self.scope, self.rid = service, service.session_factory, scope, rid
        self.config, self.inner, self.ledger = config, inner, self
        self.started = time.monotonic()
        with self.factory() as db:
            self.service.owned(db, scope.owner_id, scope.session_id, lock=True)
            row = db.get(Budget, rid)
            if row is None:
                row = Budget(run_id=rid, data={"limits": {"usd": config.max_cost_usd, "tokens": config.max_tokens,
                    "tools": config.max_tool_calls, "seconds": config.timeout_seconds, "web": config.max_web_calls},
                    "entries": [], "seconds": 0., "tools": 0, "web": 0})
                db.add(row)
            self.base_seconds = row.data["seconds"]
            self.limits = dict(row.data["limits"])
            db.commit()

    def _locked(self, db):
        lock_rows(db, self.scope.session_id, self.rid)
        self.service.owned(db, self.scope.owner_id, self.scope.session_id)
        return db.get(Budget, self.rid)

    def _scopes(self, db):
        session = db.get(ScholarV4Session, self.scope.session_id)
        run = db.get(ScholarV4Run, self.rid)
        rows = resource_rows(db, self.scope.session_id)
        all_usage = combine(balance(data) for _, data, _ in rows)
        job = copy.deepcopy(session.data.get('writing_job'))
        if not job or job['id'] != run.data.get('writing_job_id'):
            job = next((copy.deepcopy(j) for j in session.data.get('writing_job_history',[])
                if j['id']==run.data.get('writing_job_id')),None)
        mission_usage = refresh_job_resources(db, self.scope.session_id, job, rows) if job else None
        cap = (session.data.get('v5_session_budget') or {}).get('limit_usd', getattr(self.config, 'session_budget_usd', None))
        return all_usage, cap, job, mission_usage

    def _sync(self, db, budget):
        """JobMeter compatibility is a projection, in the SAME commit, not a second ledger."""
        run = db.get(ScholarV4Run, self.rid)
        run.data = {**run.data, 'ledger': copy.deepcopy(budget.data['entries']),
            'resource_usage': balance(budget.data)}
        session = db.get(ScholarV4Session, self.scope.session_id)
        job_id = run.data.get('writing_job_id')
        if job_id:
            data = copy.deepcopy(session.data)
            job = data.get('writing_job')
            if not job or job['id'] != job_id:
                job = next((j for j in data.get('writing_job_history',[]) if j['id']==job_id),None)
            if job:
                usage = refresh_job_resources(db, session.id, job)
                run.data = {**run.data, 'mission_usage': usage}
                session.data = data

    @staticmethod
    def _check_scope(name, usage, limits, amounts):
        for axis, field in (('usd', 'committed_usd'), ('tokens', 'tokens'), ('tools', 'tools'), ('web', 'web')):
            consumed, requested = usage[field], amounts.get(axis, 0)
            if axis == 'web' and requested == 0:
                continue  # A recorded Web overrun cannot spend more Web locally.
            if axis in limits and consumed+requested > limits[axis]+1e-9:
                raise BudgetRefused({'refused_budget': name, 'axis': axis, 'used': consumed,
                    'requested': requested, 'limit': limits[axis], 'classification':
                    'job_budget' if consumed >= limits[axis] else 'admission_refused'})

    def snapshot(self):
        with self.factory() as db:
            data = copy.deepcopy(db.get(Budget, self.rid).data)
            session_usage, session_cap, job, mission_usage = self._scopes(db)
        data["seconds"] = max(data["seconds"], self.base_seconds+time.monotonic()-self.started)
        entries = data["entries"]
        data.update(cost_usd=sum(e.get("cost_usd", 0) for e in entries if e["status"] == "settled"),
            reserved_tokens=sum(e['reserved_tokens'] for e in entries if e['status']=='reserved'),
            reserved_usd=sum(e['reserved_usd'] for e in entries if e['status']=='reserved'),
            unresolved_tokens=sum(e['reserved_tokens'] for e in entries if e['status']=='uncertain'),
            unresolved_usd=sum(e['reserved_usd'] for e in entries if e['status']=='uncertain'),
            uncertain_usd=sum(e["reserved_usd"] for e in entries if e["status"] in {"reserved", "uncertain"}),
            input_tokens=sum(e.get("input_tokens", 0) for e in entries if e["status"] == "settled"),
            output_tokens=sum(e.get("output_tokens", 0) for e in entries if e["status"] == "settled"),
            uncertain_tokens=sum(e["reserved_tokens"] for e in entries if e["status"] in {"reserved", "uncertain"}),
            llm_calls=sum(int(e.get('sent', e['status'] != 'released')) for e in entries),
            remaining_tokens=max(0, data['limits']['tokens']-sum(e.get('input_tokens', 0)+e.get('output_tokens', 0)
                if e['status']=='settled' else e['reserved_tokens'] if e['status'] in {'reserved','uncertain'} else 0 for e in entries)))
        remaining = max(0., data['limits']['seconds']-data['seconds'])
        if job:
            remaining = min(remaining, remaining_seconds(job['mission']))
        data.update(remaining_seconds=remaining, session_resources=session_usage, session_limit_usd=session_cap,
            mission_resources=mission_usage, mission_limits=copy.deepcopy(job['mission']) if job else None)
        return data

    def admit(self, kind, stable_target, label, model, amount_usd, detail):
        metrics = copy.deepcopy(getattr(self, 'request_metrics', {}))
        web = bool(detail.get("v5_web", getattr(self, "web_request", False)))
        with self.factory() as db:
            row = self._locked(db)
            data = copy.deepcopy(row.data)
            web_tokens = self.config.web_token_reserve if web else 0
            if metrics:
                # P2/P3: sized from this session's measured calls, with margins; the caps are unchanged.
                from .pricing import resolve, reserve_bound
                history = [e for _, d, _ in resource_rows(db, self.scope.session_id) for e in d.get('entries', [])]
                sizing = admission_sizing(history, metrics['point_input_tokens'], self.config.web_token_reserve)
                metrics.update(admission_input_tokens=sizing['input_tokens'], admission_sizing=sizing)
                web_tokens = sizing['web_tokens'] if web else 0
                detail = {**detail, 'estimated_input_tokens': sizing['input_tokens']}
                amount_usd = reserve_bound(detail['estimated_input_tokens'], detail['max_output_tokens'], resolve(self.config),
                    web_calls=int(web), web_tokens=web_tokens)
            tokens = detail["estimated_input_tokens"] + detail["max_output_tokens"] + web_tokens
            session = db.get(ScholarV4Session, self.scope.session_id)
            run = db.get(ScholarV4Run, self.rid)
            if session.active_run_id != self.rid or run.cancel_requested:
                raise BudgetRefused({"refused_budget": "v5_lease", "message": "Exécution interrompue."})
            entries = data["entries"]
            spent = sum(e.get("cost_usd", 0) if e["status"] == "settled" else e["reserved_usd"]
                if e["status"] in {"reserved", "uncertain"} else 0 for e in entries)
            used = sum(e.get("input_tokens", 0)+e.get("output_tokens", 0) if e["status"] == "settled" else e["reserved_tokens"]
                if e["status"] in {"reserved", "uncertain"} else 0 for e in entries)
            seconds = max(data["seconds"], self.base_seconds+time.monotonic()-self.started)
            uncertain_web = sum(int(e.get("web", False)) for e in entries if e["status"] in {"reserved", "uncertain"})
            axes = [('usd', spent, amount_usd), ('tokens', used, tokens), ('seconds', seconds, 0),
                ('tools', data['tools']+uncertain_web, 1), ('web', data['web']+uncertain_web, int(web))]
            for axis, consumed, requested in axes:
                if axis == 'web' and requested == 0:
                    continue
                if consumed+requested > self.limits[axis]+1e-9 or (axis in {'seconds','tools'} and consumed >= self.limits[axis]):
                    raise BudgetRefused({'refused_budget':'v5_global', 'axis':axis, 'used':consumed,
                        'requested':requested, 'limit':self.limits[axis],
                        'classification':'job_budget' if consumed >= self.limits[axis] else 'admission_refused',
                        'message':'Requête non admissible au plafond global ; résultats conservés.'})
            all_usage, session_cap, job, mission_usage = self._scopes(db)
            if session_cap is not None:
                self._check_scope('v5_session', all_usage, {'usd': session_cap}, {'usd': amount_usd})
            if job:
                m = job['mission']
                self._check_scope('v5_mission', mission_usage,
                    {'usd': job['budget_usd'], 'tokens': m['max_tokens'], 'tools': m['max_tools'], 'web': m['max_web']},
                    {'usd': amount_usd, 'tokens': tokens, 'tools': int(web), 'web': int(web)})
                if remaining_seconds(m) <= 0:
                    raise BudgetRefused({'refused_budget': 'v5_mission', 'axis': 'seconds',
                        'classification': 'job_budget', 'used': m['max_seconds'], 'limit': m['max_seconds'], 'requested': 0})
            if getattr(self, 'protect_finalization', False):
                hold_tokens = min(self.config.finalization_reserved_tokens, self.limits['tokens']//4)
                hold_usd = min(self.config.finalization_reserved_cost_usd, self.limits['usd']/4)
                classification = 'synthesis_reserve'
                if getattr(self, 'save_reserve', False):
                    # Drafting: keep one call of this size for saving the current
                    # section (its dossier and text weigh about as much as this one).
                    hold_tokens, hold_usd = max(hold_tokens, tokens), max(hold_usd, amount_usd)
                    classification = 'section_save_reserve'
                holds = [('tokens', used, tokens, hold_tokens), ('usd', spent, amount_usd, hold_usd),
                    ('seconds', seconds, 0, min(self.config.finalization_reserved_seconds, self.limits['seconds']/4)),
                    ('tools', data['tools']+uncertain_web, 1+int(web), 1)]
                for axis, consumed, requested, hold in holds:
                    if consumed+requested > self.limits[axis]-hold:
                        raise BudgetRefused({'refused_budget':'v5_synthesis', 'axis':axis, 'classification':classification,
                            'used':consumed, 'requested':requested, 'limit':self.limits[axis], 'held_for_finalization':hold})
            entry = {"id": uuid.uuid4().hex, "status": "reserved", "reserved_usd": amount_usd,
                "reserved_tokens": tokens, "model": model, "target": stable_target, "web": web,
                'sent':False, 'admitted_at':datetime.now(timezone.utc).isoformat(), 'request_metrics':metrics}
            data["entries"].append(entry)
            data["seconds"] = seconds
            row.data = data
            self._sync(db, row)
            db.commit()
        ticket = SimpleNamespace(call_id=entry["id"], done=False, inner=None)
        if self.inner:
            try:
                ticket.inner = self.inner.admit(kind, stable_target, label, model, amount_usd, detail)
            except Exception:
                self.release(ticket, "inner_admission_refused")
                raise
        return ticket

    def mark_sent(self, target):
        """Called below MeteredClient admission, immediately before SDK transport."""
        with self.factory() as db:
            row = self._locked(db)
            data = copy.deepcopy(row.data)
            entry = next(e for e in reversed(data['entries']) if e['target']==target and e['status']=='reserved')
            entry.update(sent=True, sent_at=datetime.now(timezone.utc).isoformat())
            row.data = data
            db.commit()

    def _close(self, ticket, status, **fields):
        with self.factory() as db:
            row = self._locked(db)
            data = copy.deepcopy(row.data)
            for e in data["entries"]:
                if e["id"] == ticket.call_id:
                    if e['status'] in {'reserved', 'uncertain'}:
                        e.update(status=status, closed_at=datetime.now(timezone.utc).isoformat(), **fields)
                    elif e['status'] != status or any(e.get(k) != v for k, v in fields.items() if k != 'meta'):
                        raise RevisionConflict('Conflicting settlement of a completed call')
                    metrics = e.get('request_metrics')
                    if status=='settled' and metrics:
                        from .pricing import usage_categories
                        e['token_categories'] = usage_categories(e.get('usage_details'))
                        actual = e.get('input_tokens',0)
                        metrics.update(actual_input_tokens=actual,actual_output_tokens=e.get('output_tokens',0),
                            input_estimation_error=metrics['point_input_tokens']-actual,
                            exceeded_admission_estimate=actual>metrics['admission_input_tokens'])
                    # Settlement knows hosted Web use before the graph receipt exists.
                    parts = (fields.get('meta') or {}).get('parts')
                    if status == 'settled' and parts is not None:
                        from .pricing import resolve
                        price = resolve(self.config)
                        web = int(round(parts.get('web_calls', 0)/price.web_call)) if price.web_call else 0
                        opid = e['target']+':web'
                        done = data.setdefault('tool_operations', [])
                        if opid not in done:
                            data['tools'] += web; data['web'] += web; done.append(opid)
            data["seconds"] = max(data["seconds"], self.base_seconds+time.monotonic()-self.started)
            row.data = data
            self._sync(db, row)
            db.commit()
        ticket.done = True

    def settle(self, ticket, amount_usd, *, usage=None, meta=None):
        usage = usage or {}
        self._close(ticket, "settled", cost_usd=amount_usd, input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0), response_received=True, usage_details=usage, meta=meta)
        if ticket.inner:
            self.inner.ledger.settle(ticket.inner, amount_usd, usage=usage, meta=meta)

    def record_refusal(self, explanation):
        # Structured financial diagnostics only: no request text, URLs or keys.
        allowed = {'refused_budget','axis','used','requested','limit','classification','held_for_finalization'}
        with self.factory() as db:
            run = db.get(ScholarV4Run,self.rid)
            run.data = {**run.data,'admission_refusal':{k:v for k,v in explanation.items() if k in allowed}}
            db.commit()

    def release(self, ticket, reason, meta=None):
        self._close(ticket, "released", reason=reason)
        if ticket.inner:
            self.inner.ledger.release(ticket.inner, reason, meta)

    def unknown(self, ticket, reason, meta=None):
        self._close(ticket, "uncertain", reason=reason, meta=meta)
        if ticket.inner:
            self.inner.ledger.unknown(ticket.inner, reason, meta)

    def flush_time(self):
        with self.factory() as db:
            row = self._locked(db)
            data = copy.deepcopy(row.data)
            data["seconds"] = max(data["seconds"], self.base_seconds+time.monotonic()-self.started)
            row.data = data
            db.commit()

    def record_tools(self, count, web=0, operation_id=None):
        with self.factory() as db:
            row = self._locked(db)
            data = copy.deepcopy(row.data)
            done = data.setdefault("tool_operations", [])
            if operation_id and operation_id in done:
                return
            pending = sum(int(e.get("web", False)) for e in data["entries"] if e["status"] in {"reserved", "uncertain"})
            if data["tools"]+count+web+pending > self.limits["tools"] or (web and data["web"]+web+pending > self.limits["web"]):
                raise BudgetRefused({"refused_budget": "v5_tools", "message": "Plafond global des outils atteint."})
            _, _, job, mission_usage = self._scopes(db)
            if job:
                m = job['mission']
                self._check_scope('v5_mission', mission_usage, {'tools': m['max_tools'], 'web': m['max_web']},
                    {'tools': count+web, 'web': web})
                if remaining_seconds(m) <= 0:
                    raise BudgetRefused({'refused_budget': 'v5_mission', 'axis': 'seconds', 'classification': 'job_budget'})
            if operation_id:
                done.append(operation_id)
            data["tools"] += count+web
            data["web"] += web
            data["seconds"] = max(data["seconds"], self.base_seconds+time.monotonic()-self.started)
            row.data = data
            self._sync(db, row)
            db.commit()
