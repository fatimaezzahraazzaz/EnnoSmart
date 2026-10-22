"""Durable spending of an automatic drafting job, per provider call.

Every provider call of a job segment is reserved in the segment's own run row before the
request leaves (protective amount), then settled at its priced cost, released (certainly
not processed) or marked uncertain (may have been processed: the reservation stays
counted). A run interrupted before ``finish()`` keeps its entries; an entry still reserved
when its run is over counts as uncertain. The manuscript budget is rebuilt from these
entries, never from the run's final usage summary (audit: 1.25 $ of receipts were lost).

Admission refuses a call when confirmed + reserved + uncertain + the new reservation would
exceed the manuscript budget, and when the segment no longer holds the conversation's run
lease (a stale worker): nothing is sent then.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select

from .finance import BudgetRefused
from .session import ScholarV4Run, ScholarV4Session, lock_rows

CLOSED = {"settled", "released", "uncertain"}


def now():
    return datetime.utcnow().isoformat() + "Z"


def totals(entries, run_over=True):
    """confirmed (settled cost), reserved (calls in flight), uncertain (calls that may have
    been processed without a known cost, counted at their reservation)."""
    confirmed = reserved = uncertain = 0.0
    for entry in entries or []:
        status = entry.get("status")
        if status == "settled":
            confirmed += float(entry.get("cost_usd") or 0)
        elif status == "uncertain" or (status == "reserved" and run_over):
            uncertain += float(entry.get("reserved_usd") or 0)
        elif status == "reserved":
            reserved += float(entry.get("reserved_usd") or 0)
    return {"confirmed_usd": round(confirmed, 9), "reserved_usd": round(reserved, 9), "uncertain_usd": round(uncertain, 9)}


def committed(summary):
    return summary["confirmed_usd"] + summary["reserved_usd"] + summary["uncertain_usd"]


def add(a, b):
    return {key: round(a.get(key, 0) + b.get(key, 0), 9) for key in ("confirmed_usd", "reserved_usd", "uncertain_usd")}


class JobTicket:
    def __init__(self, entry_id, inner=None):
        self.entry_id, self.inner, self.done = entry_id, inner, False

    @property
    def call_id(self):
        return self.entry_id


class JobMeter:
    """Meter of one job segment for MeteredClient (admit, then ledger.settle / release /
    unknown). ``prior`` is what the job's earlier segments committed when this one opened
    (an upper bound: a stale segment's late settlement can only lower it). An inner meter
    (the organisation's finance ledger, when enabled) is applied too."""

    def __init__(self, session_factory, session_id, run_id, budget_usd, prior, inner=None):
        self.session_factory, self.session_id, self.run_id = session_factory, session_id, run_id
        self.budget_usd, self.prior, self.inner = float(budget_usd), dict(prior), inner
        self.ledger = self

    def _entries(self, db):
        # Copies: an entry changed in place would compare equal to the stored JSON and
        # SQLAlchemy would write nothing.
        run = db.get(ScholarV4Run, self.run_id)
        return run, [dict(entry) for entry in (run.data or {}).get("ledger", [])]

    def admit(self, kind, stable_target, label, model, amount_usd, detail):
        with self.session_factory() as db:
            lock_rows(db, self.session_id, self.run_id)
            holder = db.execute(select(ScholarV4Session.active_run_id)
                .where(ScholarV4Session.id == self.session_id)).scalar()
            if holder != self.run_id:
                db.rollback()
                raise BudgetRefused({"refused_budget": "writing_job:lease", "message": "Segment remplacé ou arrêté : "
                    "aucun appel n'est envoyé par un segment qui ne détient plus la conversation."})
            run, entries = self._entries(db)
            spent = add(self.prior, totals(entries, run_over=False))
            if committed(spent) + amount_usd > self.budget_usd + 1e-9:
                db.rollback()
                raise BudgetRefused({"refused_budget": "writing_job:manuscript", "message":
                    f"Budget du manuscrit insuffisant : {committed(spent):.2f} $ engagés (confirmés, réservés ou incertains) "
                    f"sur {self.budget_usd:.2f} $, réservation de {amount_usd:.2f} $ refusée.", **spent})
            entry = {"id": uuid.uuid4().hex, "status": "reserved", "reserved_usd": round(float(amount_usd), 9),
                "kind": kind, "label": str(label)[:120], "model": model, "at": now()}
            run.data = {**run.data, "ledger": [*entries, entry]}
            db.commit()
        inner = None
        if self.inner is not None:
            try:
                inner = self.inner.admit(kind, stable_target, label, model, amount_usd, detail)
            except BudgetRefused:
                self._close(entry["id"], "released", release_reason="organisation_budget_refused")
                raise
        return JobTicket(entry["id"], inner)

    def _close(self, entry_id, status, **fields):
        with self.session_factory() as db:
            lock_rows(db, run_id=self.run_id)
            run, entries = self._entries(db)
            for entry in entries:
                if entry["id"] == entry_id and entry["status"] == "reserved":
                    entry.update(status=status, closed_at=now(), **fields)
            run.data = {**run.data, "ledger": entries}
            db.commit()

    # Ledger interface used by MeteredClient / _MeteredStream.
    def settle(self, ticket, amount_usd, *, usage=None, meta=None):
        self._close(ticket.entry_id, "settled", cost_usd=round(float(amount_usd), 9))
        if ticket.inner is not None:
            self.inner.ledger.settle(ticket.inner, amount_usd, usage=usage, meta=meta)

    def release(self, ticket, reason, meta=None):
        self._close(ticket.entry_id, "released", release_reason=str(reason)[:80])
        if ticket.inner is not None:
            self.inner.ledger.release(ticket.inner, reason, meta)

    def unknown(self, ticket, reason, meta=None):
        self._close(ticket.entry_id, "uncertain", uncertain_reason=str(reason)[:80])
        if ticket.inner is not None:
            self.inner.ledger.unknown(ticket.inner, reason, meta)


def close_open_entries(run, reason):
    """Entries still reserved when a run ends or is recovered become explicitly uncertain
    (the call may have been processed): their reservation stays counted."""
    entries = [dict(entry) for entry in (run.data or {}).get("ledger", [])]
    changed = False
    for entry in entries:
        if entry.get("status") == "reserved":
            entry.update(status="uncertain", uncertain_reason=reason, closed_at=now())
            changed = True
    if changed:
        run.data = {**run.data, "ledger": entries}
    return changed
