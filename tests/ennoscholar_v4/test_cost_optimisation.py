"""Costs without a model change: corrected catalogue, explicit cache layout, lighter repairs.

Offline: no provider call; the real session (run 95f3507d receipts) is read in read-only mode.
"""
import ast
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.pricing import cost, resolve
from agents.EnnoScholarV4.prompt_cache import layout

SOL = ScholarV4Config(api_key="mock")


# --- Catalogue (official pages read on 2026-10-20) ---------------------------------------------

def test_the_catalogue_matches_the_official_prices_and_keeps_historical_prices_with_luna_by_default():
    assert SOL.model == "gpt-6-luna"
    sol = resolve(SOL, "gpt-5.6-sol")
    assert (sol.input, sol.cache_read, sol.cache_write, sol.output) == (4.0, 0.40, 5.0, 20.0)
    six = resolve(SOL, "gpt-6.1-sol")
    # Corrected: the previous entry (12 / 0.60 / 15 / 60) did not match the provider's page.
    assert (six.input, six.cache_read, six.cache_write, six.output) == (2.0, 0.10, 2.50, 10.0)
    assert six.version == sol.version == "openai-standard-2026-10-21" and six.verified
    assert sol.prompt_cache == six.prompt_cache == "explicit"


def test_legacy_stage_overrides_are_neutralized_by_the_exclusive_luna_policy(monkeypatch):
    for name in ("ENNOSCHOLAR_V4_MODEL", "ENNOSCHOLAR_V4_TASK_MODELS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "mock")
    assert ScholarV4Config.from_env().model == "gpt-6-luna"
    from agents.EnnoScholarV4.routing import model_for
    trial = replace(SOL, task_models=json.dumps({"writing": "gpt-6.1-sol"}))
    trial.validate()                                                    # priced: accepted when chosen explicitly
    assert model_for(trial, "section") == "gpt-6-luna" and model_for(trial, "reading") == "gpt-6-luna"
    replace(SOL, model="gpt-6.1-sol").validate()


# --- Explicit cache layout --------------------------------------------------------------------

def section_request(instructions="Règles stables de rédaction. " * 300):
    return {"model": "gpt-5.6-sol", "instructions": instructions, "tools": [{"type": "function", "name": "write_section",
        "parameters": {"type": "object"}}], "input": [{"role": "developer", "content": "Mémoire de la section 3"},
        {"role": "developer", "content": "Consigne de ce tour : rédiger la section 3"}], "prompt_cache_key": "ennoscholar-v4-section"}


def test_only_the_stable_prefix_is_written_to_the_cache_and_nothing_is_removed():
    request = section_request()
    sent, receipt = layout(request, resolve(SOL), "explicit")
    assert receipt["breakpoint"] == "instructions" and receipt["stable_prefix_tokens"] >= 1024
    assert "instructions" not in sent and sent["extra_body"]["prompt_cache_options"] == {"mode": "explicit"}
    first = sent["input"][0]
    assert first["role"] == "developer" and first["content"][0]["text"] == request["instructions"]
    assert first["content"][0]["prompt_cache_breakpoint"] == {"mode": "explicit"}
    assert sent["input"][1:] == request["input"] and sent["tools"] == request["tools"]   # same content, same order
    assert request["instructions"] and "extra_body" not in request                          # the agent's request unchanged
    # Two sections share their prefix exactly: the second one reads it from the cache.
    other = section_request()
    other["input"] = [{"role": "developer", "content": "Mémoire de la section 4"}, request["input"][1]]
    assert layout(other, resolve(SOL), "explicit")[0]["input"][0] == first


def test_implicit_mode_a_short_prefix_or_a_model_without_explicit_cache_leave_the_request_as_is():
    request = section_request()
    assert layout(request, resolve(SOL), "implicit") == (request, None)
    unknown = replace(resolve(SOL), prompt_cache="")
    assert layout(request, unknown, "explicit") == (request, None)
    short = section_request("Règle courte.")
    sent, receipt = layout(short, resolve(SOL), "explicit")
    # Below the provider's minimum, a write would never be reused: explicit mode, no breakpoint.
    assert receipt["breakpoint"] is None and sent["instructions"] == "Règle courte." and sent["input"] == short["input"]
    sent, receipt = layout(request, resolve(SOL), "off")
    assert receipt["breakpoint"] is None and sent["extra_body"]["prompt_cache_options"] == {"mode": "explicit"}


def test_deployments_use_explicit_caching_by_default(monkeypatch):
    monkeypatch.delenv("ENNOSCHOLAR_V4_PROMPT_CACHE_MODE", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "mock")
    assert ScholarV4Config.from_env().prompt_cache_mode == "explicit"
    monkeypatch.setenv("ENNOSCHOLAR_V4_PROMPT_CACHE_MODE", "implicit")
    assert ScholarV4Config.from_env().prompt_cache_mode == "implicit"
    with pytest.raises(Exception):
        replace(SOL, prompt_cache_mode="partout").validate()


def test_the_metered_client_reserves_on_the_agents_request_and_sends_the_cached_layout():
    from agents.EnnoScholarV4.finance import MeteredClient

    class Provider:
        def __init__(self):
            self.sent = []

        @property
        def responses(self):
            return self

        def create(self, **kwargs):
            self.sent.append(kwargs)
            return NS(usage={"input_tokens": 10, "output_tokens": 2}, output=[], id="r")

    records = {}
    for mode in ("implicit", "explicit"):
        provider = Provider()
        client = MeteredClient(provider, replace(SOL, prompt_cache_mode=mode))
        client.responses.create(**section_request(), _ennoscholar_task={"attempt_id": mode})
        records[mode] = (client.records[mode], provider.sent[0])
    assert records["implicit"][0]["reserved_usd"] == records["explicit"][0]["reserved_usd"]   # same protective bound
    assert "instructions" in records["implicit"][1] and "instructions" not in records["explicit"][1]
    assert records["explicit"][0]["cache_layout"]["breakpoint"] == "instructions"


def test_on_the_real_section_requests_the_layout_removes_the_cache_write_surcharge():
    """Run 95f3507d receipts: every section request wrote its whole prefix (≈ 16 000 tokens)
    and read nothing. With the stable prefix alone written once, then read."""
    sol = resolve(SOL, "gpt-5.6-sol")
    real = {"input_tokens": 16683, "input_tokens_details": {"cache_write_tokens": 16093, "cached_tokens": 0}, "output_tokens": 0}
    before = cost(real, sol)["usd"]
    prefix = 3000                                                    # instructions + write_section tool (≈ 12 kB)
    first = cost({"input_tokens": 16683, "input_tokens_details": {"cache_write_tokens": prefix, "cached_tokens": 0},
        "output_tokens": 0}, sol)["usd"]
    later = cost({"input_tokens": 16683, "input_tokens_details": {"cache_write_tokens": 0, "cached_tokens": prefix},
        "output_tokens": 0}, sol)["usd"]
    assert before == pytest.approx(0.082825, abs=1e-6)
    assert first < before and later < 0.70 * before                  # about -32 % of input cost per later section


def test_the_repair_echo_keeps_the_whole_proposal_and_drops_only_duplicated_fields():
    from tests.ennoscholar_v4.test_run_dcf5d05b import session
    from agents.EnnoScholarV4.config import ScholarV4Config
    from agents.EnnoScholarV4.task_results import TaskResults
    state = session()
    task = state["task_results"].get("869f9aaafdbef8385cd2230f8f7ecb777613f3b6280b44003ef1e04d29e59d62")
    if not task:
        pytest.skip("Tâche du run 95f3507d absente")
    refused = next(a for a in task["attempts"] if a["status"] == "refused")
    original = json.loads(next(c for c in refused["content"] if c.get("type") == "function_call")["arguments"])
    results = TaskResults(state, ScholarV4Config(api_key="mock"), dict(input_tokens=0, output_tokens=0,
        estimated_cost_usd=0., unreported_reserved_cost_usd=0.), [])
    task = {**task, "attempts": [refused], "status": "refused"}
    echoed = json.loads(results.recovery_exchange(task)[0]["arguments"])
    assert echoed["text"] == refused["arguments"]["text"] and echoed["evidence_ids"] == refused["arguments"]["evidence_ids"]
    saved = len(json.dumps(original, ensure_ascii=False)) - len(json.dumps(echoed, ensure_ascii=False))
    assert saved >= len(json.dumps(original["claims"], ensure_ascii=False))     # ≈ 6 500 characters less per repair
    # The claims are not lost: they stay in the attempt, the merge base of a paragraph repair.
    assert len(refused["arguments"]["claims"]) == len(original["claims"])


# --- Celery: the existing worker drives the job ----------------------------------------------

def test_the_job_is_handed_to_the_existing_celery_worker_only_after_checking_it(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from agents.EnnoScholarV4 import service as module
    sent = []

    class Producer:
        def send_task(self, name, kwargs, retry):
            sent.append((name, kwargs, retry))
            return NS(get=lambda timeout: svc.fingerprint())
    monkeypatch.setattr(module, "celery_client", lambda: Producer())
    svc = module.ScholarV4Service(sessionmaker(bind=create_engine("sqlite://")), replace(SOL, writing_job_executor="celery"))
    svc.claim_dispatch = lambda owner, sid, job_id: True        # cross-process claim: test_writing_job_stability
    try:
        assert svc.choose_executor() == "celery"
        svc.dispatch_job(1, "session_x", "job_y")
        svc.dispatch_job(1, "session_x", "job_y")                       # a duplicate within 30 s is not sent again
        assert [name for name, _, _ in sent] == [module.WORKER_CHECK, module.DRIVE_TASK]
        assert sent[1][1] == {"owner": 1, "session_id": "session_x", "job_id": "job_y"} and sent[1][2] is False
    finally:
        svc.pool.shutdown(wait=True)


def test_auto_mode_records_a_thread_executor_when_no_worker_can_run_v4(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from agents.EnnoScholarV4 import service as module

    class Down:
        def send_task(self, *a, **k):
            raise ConnectionRefusedError()
    monkeypatch.setattr(module, "celery_client", lambda: Down())
    svc = module.ScholarV4Service(sessionmaker(bind=create_engine("sqlite://")), replace(SOL, writing_job_executor="auto"))
    try:
        assert svc.choose_executor() == "thread"
        assert replace(SOL).writing_job_executor == "thread"            # Celery is never used unless configured
    finally:
        svc.pool.shutdown(wait=True)


def test_the_worker_side_tasks_check_v4_and_retry_after_a_live_lease_instead_of_acknowledging():
    source = (Path(__file__).resolve().parents[2] / "backend_api" / "worker" / "tasks.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    tasks = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    drive = tasks["drive_writing_job"]
    options = {kw.arg: ast.literal_eval(kw.value) for kw in drive.decorator_list[0].keywords}
    assert options["name"] == "ennosmart.scholar_v4.drive_writing_job" and options["acks_late"] is True
    assert options["reject_on_worker_lost"] is True
    body = ast.unparse(drive)
    assert "self.retry(countdown" in body and "get_service().drive_job(" in body
    check = tasks["scholar_v4_worker_check"]
    assert "worker_fingerprint()" in ast.unparse(check)
    assert {kw.arg: ast.literal_eval(kw.value) for kw in check.decorator_list[0].keywords} == {
        "name": "ennosmart.scholar_v4.worker_check"}
