"""Persistent spending controller of EnnoScholar V4: ledger, budgets and admission.

Every provider call goes through ``MeteredClient``: a protective amount is reserved
atomically in every applicable budget (organisation → consultant → dossier → task,
each with optional total / daily / monthly limits) *before* the request leaves the
process, then the response's real usage is priced once with the versioned catalogue
(pricing.py) and the reservation settled. Only an amount whose non-consumption is
certain is released (no request sent, or a 4xx refusal before processing); a call
whose usage is unknown (interrupted stream, transport error, crash) stays reserved
until an administrator reconciles it.

Identities come from the authenticated service (owner id, permitted project, server
configuration), never from the browser. The tables live on their own metadata: they
are created only by the explicit migration (``migrate`` or the SQL file), never by the
application's ``create_all``. Amounts are integers in nano-dollars so that concurrent
updates never accumulate float errors. Guarantee: controlled admission and a
conservative record of known commitments, not the provider's invoice to the cent.
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import (BigInteger, Column, DateTime, Integer, JSON, MetaData, String, Table, and_, insert, or_,
    select, update)
from sqlalchemy.exc import IntegrityError

from .stops import StopResearch

NANO = 1_000_000_000
LEVELS = ("organization", "consultant", "dossier", "task")
PERIODS = ("total", "day", "month")
# HTTP statuses that prove the request was refused before any processing: nothing billed.
NOT_PROCESSED = {400, 401, 403, 404, 409, 413, 415, 422, 429}

METADATA = MetaData()
COUNTERS = Table("scholar_v4_budget_counters", METADATA,
    Column("key", String(240), primary_key=True),
    Column("level", String(20), nullable=False),
    Column("scope_id", String(200), nullable=False),
    Column("period", String(20), nullable=False),
    Column("spent_nano", BigInteger, nullable=False, default=0),
    Column("reserved_nano", BigInteger, nullable=False, default=0),
    Column("calls", Integer, nullable=False, default=0),
    Column("updated_at", DateTime, nullable=False))
LEDGER = Table("scholar_v4_spend_ledger", METADATA,
    Column("call_id", String(40), primary_key=True),
    Column("status", String(20), nullable=False, index=True),
    Column("created_at", DateTime, nullable=False, index=True),
    Column("updated_at", DateTime, nullable=False),
    Column("organization_id", String(80), nullable=False, index=True),
    Column("consultant_id", Integer, nullable=False, index=True),
    Column("dossier", String(120), nullable=False, index=True),
    Column("task_key", String(64), nullable=False, index=True),
    Column("task_kind", String(40), nullable=False),
    Column("task_label", String(240), nullable=False, default=""),
    Column("run_id", String(40), nullable=True, index=True),
    Column("session_id", String(40), nullable=True),
    Column("counter_keys", JSON, nullable=False),
    Column("reserved_nano", BigInteger, nullable=False, default=0),
    Column("settled_nano", BigInteger, nullable=True),
    Column("model_requested", String(80), nullable=False),
    Column("model_resolved", String(120), nullable=True),
    Column("service_tier", String(40), nullable=True),
    Column("price_version", String(80), nullable=True),
    Column("price_status", String(20), nullable=True),
    Column("response_id", String(120), nullable=True),
    Column("request_id", String(120), nullable=True),
    Column("usage", JSON, nullable=True),
    Column("detail", JSON, nullable=True))


def migrate(engine):
    """Additive migration (two new tables). Run explicitly by an administrator."""
    METADATA.create_all(engine, tables=[COUNTERS, LEDGER])


def installed(engine):
    from sqlalchemy import inspect
    names = set(inspect(engine).get_table_names())
    return {COUNTERS.name, LEDGER.name} <= names


def nano(usd):
    return int(round((usd or 0)*NANO))


def usd(value):
    return round((value or 0)/NANO, 9)


class _Refuse(Exception):
    def __init__(self, row):
        self.row = row


class BudgetRefused(StopResearch):
    """No request was sent: a budget cannot hold the protective amount."""
    def __init__(self, explanation):
        super().__init__("budget_refused")
        self.explanation = explanation


class BudgetPolicy:
    """Limits in USD per level and period (None = no limit at that level), with
    per-identity overrides. No production limit is invented: the default policy only
    records spending until an administrator provides one."""
    def __init__(self, raw=None):
        raw = raw or {}
        self.version = raw.get("version", "unset")
        self.levels = {level: {period: raw.get("levels", {}).get(level, {}).get(period) for period in PERIODS}
            for level in LEVELS}
        self.overrides = {level: {str(k): v for k, v in (raw.get("overrides", {}).get(level) or {}).items()}
            for level in LEVELS}
        self.alert_ratios = tuple(raw.get("alert_ratios", (0.7, 0.9)))
        for level in LEVELS:
            for limits in [self.levels[level], *self.overrides[level].values()]:
                for period, value in limits.items():
                    if period not in PERIODS or (value is not None and (not isinstance(value, (int, float)) or value < 0)):
                        raise ValueError(f"Politique budgétaire invalide : {level}.{period}={value!r}.")

    @classmethod
    def from_file(cls, path):
        if not path:
            return cls()
        with open(path, encoding="utf-8") as handle:
            return cls(json.load(handle))

    def limits(self, level, scope_id):
        return {**self.levels[level], **self.overrides[level].get(str(scope_id), {})}

    def describe(self):
        return {"version": self.version, "levels": copy.deepcopy(self.levels), "overrides": copy.deepcopy(self.overrides)}


@dataclass
class SpendScope:
    """Authenticated identities of one run (resolved by the service, never by the client)."""
    organization_id: str
    consultant_id: int
    dossier: str
    run_id: str | None = None
    session_id: str | None = None

    def scope_ids(self, task_key):
        return {"organization": self.organization_id, "consultant": str(self.consultant_id),
            "dossier": self.dossier, "task": task_key}


def task_key(scope, kind, stable_target):
    """A task keeps its budget across retries, resumptions, runs and conversations of
    the same dossier: the key depends on the dossier, the kind and a stable target
    (canonical work, section title, request text), not on run or message ids."""
    return hashlib.sha256(json.dumps([scope.organization_id, scope.dossier, kind, stable_target],
        ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:64]


def periods(now):
    return {"total": "total", "day": "day:" + now.strftime("%Y-%m-%d"), "month": "month:" + now.strftime("%Y-%m")}


@dataclass
class Ticket:
    call_id: str
    reserved_nano: int
    keys: list
    model: str
    done: bool = False
    meta: dict = field(default_factory=dict)


class Ledger:
    """Atomic reservations and single settlements on the shared database."""
    def __init__(self, engine, policy=None, clock=lambda: datetime.now(timezone.utc).replace(tzinfo=None)):
        self.engine, self.policy, self.clock = engine, policy or BudgetPolicy(), clock

    def _keys(self, scope, key, now):
        ids, names = scope.scope_ids(key), periods(now)
        rows = []
        for level in LEVELS:
            limits = self.policy.limits(level, ids[level])
            for period in PERIODS:
                rows.append({"key": f"{level}:{ids[level]}|{names[period]}", "level": level, "scope_id": ids[level],
                    "period": names[period], "limit": limits.get(period)})
        # One stable locking order for every process (no deadlock between parents).
        return sorted(rows, key=lambda row: row["key"])

    def _ensure(self, rows, now):
        for row in rows:
            try:
                with self.engine.begin() as conn:
                    if conn.execute(select(COUNTERS.c.key).where(COUNTERS.c.key == row["key"])).first():
                        continue
                    conn.execute(insert(COUNTERS).values(key=row["key"], level=row["level"], scope_id=row["scope_id"],
                        period=row["period"], spent_nano=0, reserved_nano=0, calls=0, updated_at=now))
            except IntegrityError:
                pass  # Created concurrently by another worker or process.

    @staticmethod
    def _state(conn, keys):
        return {row.key: {"spent_usd": usd(row.spent_nano), "reserved_usd": usd(row.reserved_nano), "calls": row.calls}
            for row in conn.execute(select(COUNTERS).where(COUNTERS.c.key.in_(list(keys))))}

    def state(self, keys):
        with self.engine.connect() as conn:
            return self._state(conn, keys)

    def reserve(self, scope, task, amount_usd, *, kind, label, model, detail=None):
        """Reserve amount_usd in every budget or raise BudgetRefused (nothing reserved).

        One transaction: a conditional increment per budget, in a stable key order;
        the first budget that cannot hold the amount rolls every increment back. The
        condition is evaluated by the database on the locked row, so two workers or
        processes can never both take the last available amount."""
        now = self.clock()
        rows = self._keys(scope, task, now)
        self._ensure(rows, now)
        amount, call_id = nano(amount_usd), uuid.uuid4().hex
        common = dict(created_at=now, updated_at=now, organization_id=scope.organization_id,
            consultant_id=int(scope.consultant_id), dossier=scope.dossier, task_key=task, task_kind=kind,
            task_label=label[:240], run_id=scope.run_id, session_id=scope.session_id,
            counter_keys=[row["key"] for row in rows], model_requested=model)
        try:
            with self.engine.begin() as conn:
                for row in rows:
                    condition = COUNTERS.c.key == row["key"]
                    if row["limit"] is not None:
                        condition = and_(condition, COUNTERS.c.spent_nano+COUNTERS.c.reserved_nano+amount <= nano(row["limit"]))
                    if not conn.execute(update(COUNTERS).where(condition).values(
                            reserved_nano=COUNTERS.c.reserved_nano+amount, updated_at=now)).rowcount:
                        raise _Refuse(row)
                conn.execute(insert(LEDGER).values(call_id=call_id, status="reserved", reserved_nano=amount,
                    detail={"admission": self._explain(conn, rows, None, amount), **(detail or {})}, **common))
        except _Refuse as refusal:
            with self.engine.begin() as conn:
                explanation = self._explain(conn, rows, refusal.row, amount)
                conn.execute(insert(LEDGER).values(call_id=call_id, status="refused", reserved_nano=0,
                    detail={"admission": explanation, **(detail or {})}, **common))
            raise BudgetRefused(explanation) from None
        return Ticket(call_id, amount, [row["key"] for row in rows], model)

    def _explain(self, conn, rows, refused, amount):
        current = self._state(conn, (row["key"] for row in rows))
        levels = [{"budget": row["key"], "limit_usd": row["limit"], **current.get(row["key"], {})}
            for row in rows if row["limit"] is not None or row is refused]
        result = {"requested_usd": usd(amount), "policy_version": self.policy.version, "limited_budgets": levels,
            "decision": "admitted" if refused is None else "refused"}
        if refused is None and not levels:
            result["note"] = "Aucun plafond configuré à ces niveaux : dépense enregistrée, non limitée par la politique."
        if refused is not None:
            state = current.get(refused["key"], {"spent_usd": 0, "reserved_usd": 0})
            result.update(refused_budget=refused["key"], limit_usd=refused["limit"],
                available_usd=round(refused["limit"]-state["spent_usd"]-state["reserved_usd"], 9),
                message=(f"Budget {refused['level']} {refused['scope_id']} ({refused['period']}) insuffisant : "
                    f"{state['spent_usd']:.6f} $ dépensés + {state['reserved_usd']:.6f} $ réservés + "
                    f"{usd(amount):.6f} $ demandés > plafond {refused['limit']:.6f} $."))
        return result

    def settle(self, ticket, amount_usd, *, usage=None, meta=None):
        """Record the priced usage exactly once and free the rest of the reservation."""
        return self._close(ticket.call_id, "settled", nano(amount_usd), usage=usage, meta=meta)

    def release(self, ticket, reason, meta=None):
        """Nothing was processed (certain): the whole reservation is freed."""
        return self._close(ticket.call_id, "released", 0, meta={**(meta or {}), "release_reason": reason})

    def unknown(self, ticket, reason, meta=None):
        """The request may have been processed without usage: the reservation stays held."""
        now = self.clock()
        with self.engine.begin() as conn:
            changed = conn.execute(update(LEDGER).where(LEDGER.c.call_id == ticket.call_id, LEDGER.c.status == "reserved")
                .values(status="unknown", updated_at=now, **self._meta_columns(meta),
                    detail=self._detail(conn, ticket.call_id, {"unknown_reason": reason}))).rowcount
        return bool(changed)

    def reconcile(self, call_id, amount_usd, usage=None, note=""):
        """Administrator: replace a held unknown reservation by an attested amount."""
        return self._close(call_id, "settled", nano(amount_usd), usage=usage, meta={"reconciled": note or True},
            from_status=("unknown", "reserved"))

    def recover_stale(self, older_than_seconds):
        """Reservations left by a crashed worker are labelled unknown (still held)."""
        limit = self.clock()-timedelta(seconds=older_than_seconds)
        with self.engine.begin() as conn:
            return conn.execute(update(LEDGER).where(LEDGER.c.status == "reserved", LEDGER.c.created_at < limit)
                .values(status="unknown", updated_at=self.clock())).rowcount

    @staticmethod
    def _meta_columns(meta):
        meta = meta or {}
        return {key: meta[key] for key in ("model_resolved", "service_tier", "price_version", "price_status",
            "response_id", "request_id") if meta.get(key) is not None}

    @staticmethod
    def _detail(conn, call_id, extra):
        current = conn.execute(select(LEDGER.c.detail).where(LEDGER.c.call_id == call_id)).scalar() or {}
        return {**current, **extra}

    def _close(self, call_id, status, amount, *, usage=None, meta=None, from_status=("reserved", "unknown")):
        now = self.clock()
        with self.engine.begin() as conn:
            row = conn.execute(select(LEDGER.c.reserved_nano, LEDGER.c.counter_keys, LEDGER.c.status)
                .where(LEDGER.c.call_id == call_id)).first()
            if row is None:
                return False
            extra = {k: v for k, v in (meta or {}).items() if k not in {"model_resolved", "service_tier",
                "price_version", "price_status", "response_id", "request_id"}}
            # The conditional update is the idempotence guard: a second delivery of the
            # same result (double receipt, retry of the settlement) changes nothing.
            changed = conn.execute(update(LEDGER).where(LEDGER.c.call_id == call_id, LEDGER.c.status.in_(from_status))
                .values(status=status, updated_at=now, settled_nano=amount if status == "settled" else 0,
                    usage=usage, **self._meta_columns(meta),
                    detail=self._detail(conn, call_id, {**extra, **({"overrun_usd": usd(amount-row.reserved_nano)}
                        if status == "settled" and amount > row.reserved_nano else {})}))).rowcount
            if not changed:
                return False
            for key in sorted(row.counter_keys):
                conn.execute(update(COUNTERS).where(COUNTERS.c.key == key).values(
                    reserved_nano=COUNTERS.c.reserved_nano-row.reserved_nano,
                    spent_nano=COUNTERS.c.spent_nano+amount, calls=COUNTERS.c.calls+(status == "settled"),
                    updated_at=now))
        return True

    def history(self, *, consultant_id=None, dossier=None, run_id=None, limit=200):
        query = select(LEDGER).order_by(LEDGER.c.created_at.desc()).limit(limit)
        for column, value in ((LEDGER.c.consultant_id, consultant_id), (LEDGER.c.dossier, dossier), (LEDGER.c.run_id, run_id)):
            if value is not None:
                query = query.where(column == value)
        with self.engine.connect() as conn:
            return [{**dict(row._mapping), "reserved_usd": usd(row.reserved_nano),
                "settled_usd": None if row.settled_nano is None else usd(row.settled_nano)} for row in conn.execute(query)]

    def balances(self, scope, task=None):
        rows = self._keys(scope, task or "-", self.clock())
        current = self.state(row["key"] for row in rows)
        return [{"budget": row["key"], "limit_usd": row["limit"], **current.get(row["key"], {"spent_usd": 0, "reserved_usd": 0, "calls": 0}),
            **({"available_usd": round(row["limit"]-current.get(row["key"], {}).get("spent_usd", 0)
                -current.get(row["key"], {}).get("reserved_usd", 0), 9)} if row["limit"] is not None else {})}
            for row in rows if task is not None or row["level"] != "task"]


class Meter:
    """Ledger + identities + prices of one run, used by MeteredClient."""
    def __init__(self, ledger, scope, config):
        self.ledger, self.scope, self.config = ledger, scope, config

    def admit(self, kind, stable_target, label, model, amount_usd, detail):
        return self.ledger.reserve(self.scope, task_key(self.scope, kind, stable_target), amount_usd,
            kind=kind, label=label, model=model, detail=detail)


class MeteredClient:
    """Wraps the OpenAI client: no request leaves before its reservation, every stream
    is settled once when it closes. Without a ledger (finance disabled) it only records
    the per-call metadata (requested model, prefix fingerprints, provider identifiers)."""
    PRIVATE = "_ennoscholar_task"

    def __init__(self, client, config, meter=None, records=None, _lock=None):
        self._client, self.config, self.meter = client, config, meter
        self.records = records if records is not None else {}
        self._lock = _lock or threading.Lock()
        self._prefixes = {} if records is None else None

    def with_options(self, **options):
        clone = MeteredClient(self._client.with_options(**options), self.config, self.meter, self.records, self._lock)
        clone._prefixes = self._shared_prefixes
        return clone

    @property
    def _shared_prefixes(self):
        if self._prefixes is None:
            self._prefixes = {}
        return self._prefixes

    @property
    def responses(self):
        return _Responses(self)

    def __getattr__(self, name):
        return getattr(self._client, name)


def _request_tokens(kwargs):
    from .agent import request_tokens
    return request_tokens(len(json.dumps(kwargs.get("input", []), ensure_ascii=False).encode())
        + len((kwargs.get("instructions") or "").encode()) + len(json.dumps(kwargs.get("tools", [])).encode()))


def prefix_probe(kwargs, previous):
    """Fingerprints of the request prefix (instructions, tools, each input item) and the
    part identical to the previous request with the same cache key. Hashes only: no
    client text is retained. A shared prefix makes a cache hit possible, never certain."""
    def digest(value):
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:16]
    items = kwargs.get("input", []) or []
    sizes = [len(json.dumps(item, ensure_ascii=False).encode()) for item in items]
    probe = {"cache_key": kwargs.get("prompt_cache_key"), "instructions": digest(kwargs.get("instructions", "")),
        "instructions_bytes": len((kwargs.get("instructions") or "").encode()), "tools": digest(kwargs.get("tools", [])),
        "tools_bytes": len(json.dumps(kwargs.get("tools", [])).encode()), "items": [digest(item) for item in items],
        "item_bytes": sizes}
    if previous:
        stable = previous["instructions"] == probe["instructions"] and previous["tools"] == probe["tools"]
        shared = 0
        if stable:
            for old, new in zip(previous["items"], probe["items"]):
                if old != new:
                    break
                shared += 1
        probe["compared_with_previous"] = {"instructions_same": previous["instructions"] == probe["instructions"],
            "tools_same": previous["tools"] == probe["tools"], "shared_items": shared,
            "shared_prefix_bytes": (probe["instructions_bytes"]+probe["tools_bytes"]+sum(sizes[:shared])) if stable else 0,
            "first_changed_item": shared if stable and shared < len(items) else None}
    return probe


class _Responses:
    def __init__(self, owner):
        self.owner = owner

    def create(self, **kwargs):
        from .pricing import resolve, reserve_bound
        from .model_policy import request_policy
        kwargs = request_policy(kwargs)
        owner = self.owner
        label = kwargs.pop(MeteredClient.PRIVATE, None) or {}
        config, model = owner.config, kwargs.get("model", owner.config.model)
        price = resolve(config, model)
        web = any(tool.get("type") == "web_search" for tool in kwargs.get("tools", []) or [])
        tokens = _request_tokens(kwargs)
        bound = reserve_bound(tokens, kwargs.get("max_output_tokens") or config.max_output_tokens, price,
            web_calls=1 if web else 0, web_tokens=config.web_token_reserve if web else 0)
        with owner._lock:
            prefixes = owner._shared_prefixes
            probe = prefix_probe(kwargs, prefixes.get(kwargs.get("prompt_cache_key")))
            prefixes[kwargs.get("prompt_cache_key")] = probe
        record = {"model_requested": model, "price": price.describe(), "reserved_usd": bound,
            "estimated_input_tokens": tokens, "cache_probe": probe, "ledger": None}
        ticket = None
        if owner.meter is not None:
            ticket = owner.meter.admit(label.get("kind", "unknown"), label.get("stable_target", label.get("target", "")),
                label.get("label", ""), model, bound, {"estimated_input_tokens": tokens,
                    "max_output_tokens": kwargs.get("max_output_tokens"), "price": price.describe(),
                    "cache_probe": {k: v for k, v in probe.items() if k not in {"items", "item_bytes"}}})
            record["ledger"] = {"call_id": ticket.call_id, "status": "reserved"}
        if label.get("attempt_id"):
            with owner._lock:
                owner.records[label["attempt_id"]] = record
        from .prompt_cache import layout
        sent, cache_layout = layout(kwargs, price, getattr(config, "prompt_cache_mode", "implicit"))
        record["cache_layout"] = cache_layout
        try:
            stream = owner._client.responses.create(**sent)
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if ticket is not None:
                if status in NOT_PROCESSED:
                    owner.meter.ledger.release(ticket, f"http_{status}")
                    record["ledger"]["status"] = "released"
                else:
                    owner.meter.ledger.unknown(ticket, type(exc).__name__)
                    record["ledger"]["status"] = "unknown"
            raise
        if not kwargs.get("stream"):
            _settle(owner, ticket, record, stream, price)
            return stream
        return _MeteredStream(stream, owner, ticket, record, price)


def _settle(owner, ticket, record, response, price):
    from .pricing import cost
    from .model_policy import MODEL, ModelPolicyError
    import re
    resolved = getattr(response, "model", None)
    tier = getattr(response, "service_tier", None)
    if (resolved and not re.fullmatch(re.escape(MODEL) + r"(?:-\d{4}-\d{2}(?:-\d{2})?)?", resolved)) or tier not in {None, "default"}:
        record.update(model_resolved=resolved, service_tier=tier, policy_error="provider_model_or_tier_mismatch")
        if ticket is not None:
            owner.meter.ledger.unknown(ticket, "provider_model_or_tier_mismatch",
                {"model_resolved": resolved, "service_tier": tier})
            record["ledger"]["status"] = "unknown"
        error = ModelPolicyError("Le fournisseur a retourné un modèle ou mode hors politique V4 ; usage à rapprocher, aucun repli.")
        error.code = "provider_model_or_tier_mismatch"
        raise error
    raw = getattr(response, "usage", None)
    raw = raw.model_dump(exclude_none=True) if hasattr(raw, "model_dump") else raw
    web_calls = sum((item.get("type") if isinstance(item, dict) else getattr(item, "type", None)) == "web_search_call"
        for item in getattr(response, "output", None) or [])
    priced = cost(raw, price, web_calls=web_calls)
    meta = {"model_resolved": getattr(response, "model", None), "service_tier": getattr(response, "service_tier", None),
        "response_id": getattr(response, "id", None), "request_id": getattr(response, "_request_id", None),
        "price_version": price.version, "price_status": priced.get("price_status") or price.status}
    record.update(meta)
    record["cost"] = priced
    if ticket is None:
        return
    if priced["usd"] is None:
        owner.meter.ledger.unknown(ticket, priced["status"], meta)
        record["ledger"]["status"] = "unknown"
    else:
        owner.meter.ledger.settle(ticket, priced["usd"], usage=raw, meta={**meta, "parts": priced["parts"]})
        record["ledger"]["status"] = "settled"
    ticket.done = True


class _MeteredStream:
    FINAL = {"response.completed", "response.incomplete", "response.failed"}

    def __init__(self, stream, owner, ticket, record, price):
        self._stream, self._owner, self._ticket, self._record, self._price = stream, owner, ticket, record, price
        self._final, self._closed = None, False

    def __iter__(self):
        for event in self._stream:
            if getattr(event, "type", None) in self.FINAL:
                self._final = event.response
            yield event

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            close = getattr(self._stream, "close", None)
            if close:
                close()
        finally:
            if self._final is not None:
                _settle(self._owner, self._ticket, self._record, self._final, self._price)
            elif self._ticket is not None and not self._ticket.done:
                # Interrupted before the final event: tokens may have been processed.
                self._owner.meter.ledger.unknown(self._ticket, "stream_closed_without_usage")
                self._record["ledger"]["status"] = "unknown"

    def __getattr__(self, name):
        return getattr(self._stream, name)
