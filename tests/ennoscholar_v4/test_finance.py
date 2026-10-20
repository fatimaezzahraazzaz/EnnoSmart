"""Corrections 1 and 2 of audit 128698bb: priced receipts and the persistent spending controller.

Offline only: the receipts come from the audit's token counters (fixture without any
request or response text); every database is a temporary SQLite file.
"""
import json
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine

from agents.EnnoScholarV4.config import ConfigurationError, ScholarV4Config
from agents.EnnoScholarV4.finance import (BudgetPolicy, BudgetRefused, Ledger, Meter, MeteredClient, SpendScope,
    installed, migrate, task_key)
from agents.EnnoScholarV4.pricing import PriceUnknown, cost, legacy_cost, reserve_bound, resolve, usage_categories
from agents.EnnoScholarV4.task_results import TaskResults

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "audit_128698bb_receipts.json").read_text(encoding="utf-8"))
SOL = ScholarV4Config(api_key="mock", model="gpt-5.6-sol")


def usage(inp, out, read=0, write=0, reasoning=0):
    return {"input_tokens": inp, "output_tokens": out, "output_tokens_details": {"reasoning_tokens": reasoning},
        "input_tokens_details": {"cached_tokens": read, "cache_write_tokens": write}}


# --- Correction 1: catalogue and categories ---------------------------------------------

def test_each_token_category_has_its_own_price_and_reasoning_is_not_counted_twice():
    price = resolve(SOL)
    assert (price.input, price.cache_read, price.cache_write, price.output) == (4.0, .4, 5.0, 20.0)
    assert cost(usage(1000, 0), price)["usd"] == pytest.approx(.004)                 # ordinary input
    assert cost(usage(1000, 0, read=1000), price)["usd"] == pytest.approx(.0004)     # cache read
    assert cost(usage(1000, 0, write=1000), price)["usd"] == pytest.approx(.005)     # cache write
    priced = cost(usage(1000, 500, read=200, write=300, reasoning=400), price)
    # 500 ordinary + 200 read + 300 written; reasoning (400) is already inside the 500 output tokens.
    assert priced["usd"] == pytest.approx((500*4+200*.4+300*5+500*20)/1e6)
    assert priced["tokens"]["ordinary_input_tokens"] == 500 and priced["status"] == "computed"
    # Without any cache detail, every input token is ordinary.
    assert cost({"input_tokens": 1000, "output_tokens": 10}, price)["usd"] == pytest.approx((1000*4+10*20)/1e6)


def test_unknown_usage_price_or_model_never_yields_a_precise_amount():
    price = resolve(SOL)
    assert cost(None, price) == {"status": "usage_unavailable", "usd": None, "tokens": None, "price": price.describe()}
    assert cost({"output_tokens": 5}, price)["usd"] is None                           # interrupted, no input count
    assert cost(usage(10, 1), None)["status"] == "price_unknown"
    with pytest.raises(PriceUnknown):
        resolve(replace(SOL, model="modele-inconnu"))
    with pytest.raises(PriceUnknown):
        resolve(replace(SOL, service_tier="priority"))                                # tier absent from the catalogue
    # Incoherent counters (more cached than input): every input token at the highest input price, marked unverified.
    odd = cost(usage(100, 0, read=80, write=80), price)
    assert odd["status"] == "unverified" and odd["usd"] == pytest.approx(100*5/1e6)


def test_configuration_inconsistency_is_resolved_by_the_catalogue_not_by_the_old_2_10_defaults():
    default = ScholarV4Config(api_key="mock")
    assert default.model == "gpt-6.1-sol"                                             # model unchanged
    price = resolve(default)
    assert (price.input, price.output) == (12.0, 60.0) and price.status == "catalog"  # not 2 / 10
    # The launch profile's explicit 4 / 20 for gpt-5.6-sol equals the catalogue: verified.
    assert resolve(replace(SOL, input_price=4.0, output_price=20.0)).status == "catalog"
    # Explicit prices that differ from the catalogue: the higher one, marked as a conflict.
    conflict = resolve(replace(default, input_price=4.0, output_price=20.0))
    assert conflict.status == "conflict" and (conflict.input, conflict.output) == (12.0, 60.0)
    # A model outside the catalogue needs its four prices.
    with pytest.raises(ConfigurationError):
        replace(SOL, model="autre-modele", input_price=1.0, output_price=2.0).validate()
    replace(SOL, model="autre-modele", input_price=1.0, output_price=2.0, cache_read_price=.1, cache_write_price=1.2).validate()


@pytest.mark.parametrize("run,app,standard", [
    ("128698bb", 1.030556, 1.161951), ("91590987", 1.959280, 2.262225), ("d46f6f20", .022744, .027567)])
def test_the_audit_receipts_are_reproduced_call_by_call(run, app, standard):
    rows = [r for r in FIXTURE["receipts"] if r["run"].startswith(run)]
    price = resolve(SOL)
    for row in rows:
        assert round(cost(row["usage"], price)["usd"], 6) == pytest.approx(row["audit_standard_cost"], abs=1e-6)
        assert round(legacy_cost(row["usage"], 4.0, 20.0), 6) == pytest.approx(row["audit_app_cost"], abs=1e-6)
    assert round(sum(cost(r["usage"], price)["usd"] for r in rows), 6) == pytest.approx(standard, abs=2e-6)
    assert round(sum(legacy_cost(r["usage"], 4.0, 20.0) for r in rows), 6) == pytest.approx(app, abs=2e-6)


def test_the_reservation_bound_covers_every_real_receipt_without_assuming_a_cache_hit():
    price = resolve(SOL)
    for row in FIXTURE["receipts"]:
        tokens = usage_categories(row["usage"])
        bound = reserve_bound(tokens["input_tokens"], 7000, price)
        assert bound >= cost(row["usage"], price)["usd"]
    # The former bound (input price only) was below the real cost of a call written to the cache.
    counter = usage(10000, 1000, write=10000)
    assert (10000*4+1000*20)/1e6 < cost(counter, price)["usd"] <= reserve_bound(10000, 1000, price)


def test_receipts_record_categories_versions_and_keep_unknown_usage_reserved():
    run = dict(input_tokens=0, output_tokens=0, estimated_cost_usd=0., unreported_reserved_cost_usd=0.)
    tasks = TaskResults({}, SOL, run, [])
    task = tasks.task("reading", "paper_x", "v1")
    attempt = tasks.begin(task)
    row = FIXTURE["receipts"][0]
    response = NS(output=[], status="completed", usage=row["usage"], id="resp_1", model="gpt-5.6-sol-2026-10", service_tier="default")
    tasks.receive(task, attempt, response, seconds=1)
    tasks.receive(task, attempt, response, seconds=1)                                   # double receipt: counted once
    assert run["estimated_cost_usd"] == pytest.approx(row["audit_standard_cost"], abs=1e-6)
    assert attempt["cost"]["tokens"]["cache_write_tokens"] == row["usage"]["input_tokens_details"]["cache_write_tokens"]
    assert attempt["price_version"] and attempt["model_resolved"] == "gpt-5.6-sol-2026-10" and attempt["service_tier"] == "default"
    interrupted = tasks.begin(tasks.task("section", "s", "v1"))
    tasks.interrupted(tasks.rows[next(k for k in tasks.rows if tasks.rows[k]["target"] == "s")], interrupted, .3, 2)
    assert run["unreported_reserved_cost_usd"] == pytest.approx(.3) and run["estimate_incomplete"]


# --- Correction 2: ledger, budgets and admission ----------------------------------------

def engine_at(tmp_path, name="finance.db"):
    engine = create_engine(f"sqlite:///{tmp_path / name}", connect_args={"timeout": 60})
    migrate(engine)
    return engine


def scope(consultant=1, dossier="project:7", run="r1", session="s1"):
    return SpendScope("org", consultant, dossier, run, session)


def reserve(ledger, sc, amount, target=("w",), kind="reading"):
    return ledger.reserve(sc, task_key(sc, kind, list(target)), amount, kind=kind, label="t", model="gpt-5.6-sol")


def spent_and_reserved(ledger, key):
    row = ledger.state([key]).get(key, {"spent_usd": 0, "reserved_usd": 0})
    return row["spent_usd"]+row["reserved_usd"]


def test_migration_is_explicit_and_absent_tables_refuse_activation(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    assert not installed(engine)
    from agents.EnnoScholarV4.service import ScholarV4Service
    from sqlalchemy.orm import sessionmaker
    service = ScholarV4Service(sessionmaker(bind=engine), replace(SOL, finance_enabled=True))
    with pytest.raises(ConfigurationError):
        service.meter(1, "s", "r", {})
    migrate(engine)
    assert installed(engine)
    assert service.meter(1, "s", "r", {"mode": "diagnostic_linked", "project_id": 7}).scope.dossier == "project:7"
    # Disabled by default: no ledger, no table needed.
    assert ScholarV4Service(sessionmaker(bind=create_engine("sqlite://")), SOL).meter(1, "s", "r", {}) is None


def test_eight_consultants_in_parallel_respect_their_own_and_the_organisation_limits(tmp_path):
    ledger = Ledger(engine_at(tmp_path), BudgetPolicy({"levels": {"consultant": {"day": .5}, "organization": {"month": 3.0}}}))
    results = {c: 0 for c in range(1, 9)}
    lock = threading.Lock()
    def consultant(c):
        for _ in range(10):
            try:
                ticket = reserve(ledger, scope(c, f"project:{c}"), .1)
                ledger.settle(ticket, .1)
                with lock:
                    results[c] += 1
            except BudgetRefused:
                pass
    threads = [threading.Thread(target=consultant, args=(c,)) for c in results]
    [t.start() for t in threads]
    [t.join() for t in threads]
    # 8 × 0.5 $ of individual room, but 3.0 $ for the organisation: exactly 30 calls of 0.1 $.
    assert all(n <= 5 for n in results.values()) and sum(results.values()) == 30
    org = next(b for b in ledger.balances(scope(1)) if b["budget"].startswith("organization:org|month"))
    assert org["spent_usd"] == pytest.approx(3.0) and org["reserved_usd"] == pytest.approx(0)
    for c in results:
        mine = next(b for b in ledger.balances(scope(c, f"project:{c}")) if b["budget"].startswith(f"consultant:{c}|day"))
        assert mine["spent_usd"] == pytest.approx(results[c]*.1) and mine["spent_usd"] <= .5 + 1e-9


def test_parallel_calls_of_one_consultant_never_share_the_same_remaining_amount(tmp_path):
    ledger = Ledger(engine_at(tmp_path), BudgetPolicy({"levels": {"dossier": {"total": 1.0}}}))
    admitted = []
    barrier = threading.Barrier(12)
    def call():
        barrier.wait()
        try:
            admitted.append(reserve(ledger, scope(), .3))
        except BudgetRefused:
            pass
    threads = [threading.Thread(target=call) for _ in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(admitted) == 3                                                             # 3 × 0.3 ≤ 1.0 < 4 × 0.3
    key = next(k for k in admitted[0].keys if k.startswith("dossier:") and k.endswith("|total"))
    assert spent_and_reserved(ledger, key) == pytest.approx(.9)


def test_two_simultaneous_calls_on_the_last_balance_admit_exactly_one(tmp_path):
    ledger = Ledger(engine_at(tmp_path), BudgetPolicy({"levels": {"consultant": {"month": .5}}}))
    reserve(ledger, scope(), .3)
    barrier, outcome = threading.Barrier(2), []
    def call():
        barrier.wait()
        try:
            reserve(ledger, scope(), .15)
            outcome.append("admitted")
        except BudgetRefused as refusal:
            outcome.append(refusal.explanation["refused_budget"])
    threads = [threading.Thread(target=call) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(outcome)[0] == "admitted" and outcome.count("admitted") == 1
    assert "consultant:1|month" in [o for o in outcome if o != "admitted"][0]


def test_several_processes_cannot_reserve_the_same_budget_twice(tmp_path):
    engine_at(tmp_path, "shared.db")
    worker = Path(__file__).with_name("finance_worker.py")
    policy = json.dumps({"levels": {"organization": {"total": 2.0}}})
    start = time.time()+3
    processes = [subprocess.Popen([sys.executable, str(worker), str(tmp_path / "shared.db"), str(c), "6", "0.1", policy,
        str(start)], stdout=subprocess.PIPE, text=True) for c in range(1, 5)]
    outputs = [json.loads(p.communicate(timeout=180)[0].strip().splitlines()[-1]) for p in processes]
    assert sum(o["admitted"] for o in outputs) == 20 and sum(o["refused"] for o in outputs) == 4
    ledger = Ledger(create_engine(f"sqlite:///{tmp_path / 'shared.db'}"))
    org = ledger.state(["organization:org|total"])["organization:org|total"]
    assert org["spent_usd"] == pytest.approx(2.0) and org["reserved_usd"] == pytest.approx(0)


def test_a_task_keeps_its_budget_across_runs_and_conversations_of_the_same_dossier(tmp_path):
    ledger = Ledger(engine_at(tmp_path), BudgetPolicy({"levels": {"task": {"total": .5}}}))
    first = scope(run="r1", session="conversation-A")
    ledger.settle(reserve(ledger, first, .4, target=("section", "état de l'art"), kind="section"), .4)
    # A retry in a new run of a new conversation of the same dossier: same task, same budget.
    again = scope(run="r2", session="conversation-B")
    assert task_key(first, "section", ["section", "état de l'art"]) == task_key(again, "section", ["section", "état de l'art"])
    with pytest.raises(BudgetRefused) as refused:
        reserve(ledger, again, .2, target=("section", "état de l'art"), kind="section")
    assert refused.value.explanation["refused_budget"].startswith("task:")
    # Another dossier is another task.
    reserve(ledger, scope(dossier="project:8"), .2, target=("section", "état de l'art"), kind="section")


def test_retry_after_an_error_releases_only_what_was_certainly_not_processed(tmp_path):
    engine = engine_at(tmp_path)
    ledger = Ledger(engine)
    class Failing:
        def __init__(self, status): self.status = status
        def with_options(self, **kw): return self
        @property
        def responses(self): return self
        def create(self, **kw):
            error = RuntimeError("upstream")
            error.status_code = self.status
            raise error
    for status, expected in ((429, "released"), (500, "unknown"), (None, "unknown")):
        client = MeteredClient(Failing(status), SOL, meter=Meter(ledger, scope(), SOL))
        with pytest.raises(RuntimeError):
            client.responses.create(model="gpt-5.6-sol", input=[{"role": "user", "content": "x"}], instructions="",
                tools=[], max_output_tokens=100, stream=True, _ennoscholar_task={"kind": "reading", "stable_target": ["w"]})
        assert ledger.history(limit=1)[0]["status"] == expected
    held = ledger.state([next(k for k in ledger.history(limit=1)[0]["counter_keys"] if k.startswith("consultant:") and k.endswith("total"))])
    assert list(held.values())[0]["reserved_usd"] > 0                                    # two unknown calls stay held


def test_a_crash_between_reservation_and_settlement_keeps_the_amount_committed(tmp_path):
    clock = [__import__("datetime").datetime(2026, 10, 19, 12, 0)]
    ledger = Ledger(engine_at(tmp_path), BudgetPolicy({"levels": {"consultant": {"total": 1.0}}}), clock=lambda: clock[0])
    ticket = reserve(ledger, scope(), .6)                                                 # worker crashes here
    with pytest.raises(BudgetRefused):
        reserve(ledger, scope(), .6)                                                      # still counted
    clock[0] = clock[0].replace(hour=13)
    row = lambda: next(r for r in ledger.history() if r["call_id"] == ticket.call_id)
    assert ledger.recover_stale(1800) == 1 and row()["status"] == "unknown"
    assert ledger.reconcile(ticket.call_id, .25, note="facture") and row()["settled_usd"] == pytest.approx(.25)
    reserve(ledger, scope(), .6)                                                          # 0.25 + 0.6 ≤ 1.0


class Streaming:
    """A fake provider stream; stop_after cuts it before the final event."""
    def __init__(self, response, stop_after=None):
        self.response, self.stop_after, self.calls = response, stop_after, 0
    def with_options(self, **kw): return self
    @property
    def responses(self): return self
    def create(self, **kw):
        self.calls += 1
        events = [NS(type="response.output_text.delta"), NS(type="response.completed", response=self.response)]
        return iter(events[:self.stop_after] if self.stop_after is not None else events)


def metered(ledger, provider, sc=None):
    return MeteredClient(provider, SOL, meter=Meter(ledger, sc or scope(), SOL))


def call(client):
    stream = client.responses.create(model="gpt-5.6-sol", instructions="i", tools=[], max_output_tokens=7000,
        input=[{"role": "user", "content": "x"*300}], stream=True, prompt_cache_key="ennoscholar-v4-section",
        _ennoscholar_task={"kind": "section", "stable_target": ["section", "a"], "attempt_id": "a1"})
    try:
        for _ in stream:
            pass
    finally:
        stream.close()
    return stream


def test_settlement_is_single_and_uses_the_real_usage_and_identifiers(tmp_path):
    ledger = Ledger(engine_at(tmp_path))
    row = FIXTURE["receipts"][1]
    provider = Streaming(NS(usage=row["usage"], output=[], id="resp_9", model="gpt-5.6-sol-2026-10", service_tier="default"))
    client = metered(ledger, provider)
    stream = call(client)
    stream.close()                                                                         # second close: no double settlement
    entry = ledger.history(limit=1)[0]
    assert entry["status"] == "settled" and entry["settled_usd"] == pytest.approx(row["audit_standard_cost"], abs=1e-6)
    assert entry["response_id"] == "resp_9" and entry["model_resolved"] == "gpt-5.6-sol-2026-10" and entry["price_version"]
    assert not ledger.settle(NS(call_id=entry["call_id"]), 9.0)                            # a late duplicate changes nothing
    assert client.records["a1"]["cost"]["usd"] == pytest.approx(row["audit_standard_cost"], abs=1e-6)


def test_a_voluntary_interruption_or_missing_usage_keeps_the_reservation(tmp_path):
    ledger = Ledger(engine_at(tmp_path))
    call(metered(ledger, Streaming(None, stop_after=1)))
    assert ledger.history(limit=1)[0]["status"] == "unknown"
    call(metered(ledger, Streaming(NS(usage=None, output=[], id="r"))))
    assert ledger.history(limit=1)[0]["status"] == "unknown"


def test_no_request_leaves_before_its_reservation_and_a_parent_budget_refuses_it(tmp_path):
    ledger = Ledger(engine_at(tmp_path), BudgetPolicy({"levels": {"organization": {"day": .05},
        "consultant": {"day": 10.0}}}))
    provider = Streaming(NS(usage=usage(10, 10), output=[], id="r"))
    with pytest.raises(BudgetRefused) as refused:
        call(metered(ledger, provider))
    assert provider.calls == 0                                                             # nothing sent
    explanation = refused.value.explanation
    assert explanation["refused_budget"].startswith("organization:org|day") and "insuffisant" in explanation["message"]
    assert ledger.history(limit=1)[0]["status"] == "refused"


def test_the_agent_stops_cleanly_on_a_budget_refusal_without_losing_saved_drafts(tmp_path):
    from tests.ennoscholar_v4.test_context_memory import (Client, Model, REQUEST_A, agent, rich_corpus, save_section,
        seed_argumentation, seven_section_plan)
    t, ids = rich_corpus(2)
    seven_section_plan(t, ids)
    seed_argumentation(t, ids)
    for number in range(1, 4):
        save_section(t, number, ids)
    saved = {s["id"]: [d["id"] for d in s.get("drafts", [])] for s in t.state["outline"]["sections"]}
    t.state["messages"] = [{"id": "m", "role": "user", "content": REQUEST_A}]
    # Each reservation is the protective bound (~0.49 $ for the understanding call, ~0.65 $ for
    # a section at the default model's catalogue price); settlement keeps only the real cost.
    ledger = Ledger(engine_at(tmp_path), BudgetPolicy({"levels": {"dossier": {"total": .6}}}))
    provider = Client(Model(ids, []))
    runner = agent(provider)
    runner.meter = Meter(ledger, scope(), runner.config)
    result = runner.run(t.state)
    assert result["reason"] == "budget_refused" and "Budget" in result["answer"]
    for section in t.state["outline"]["sections"]:
        assert [d["id"] for d in section.get("drafts", [])][:len(saved[section["id"]])] == saved[section["id"]]
    rows = ledger.history()
    sent = [r for r in rows if r["status"] != "refused"]
    assert rows[0]["status"] == "refused" and all(r["status"] == "settled" for r in sent)
    assert all(r["reserved_usd"] <= .6 for r in sent) and rows[0]["task_kind"] == "section"
    # The run's own counter and the ledger use the same prices.
    assert result["usage"]["estimated_cost_usd"] == pytest.approx(sum(r["settled_usd"] for r in sent), abs=1e-9)
