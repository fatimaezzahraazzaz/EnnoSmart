"""Exclusive V4 migration: fake Responses transport only; no real data or API."""
import ast
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from agents.EnnoScholarV4.config import ScholarV4Config, ConfigurationError
from agents.EnnoScholarV4.finance import MeteredClient
from agents.EnnoScholarV4.model_policy import MODEL, ModelPolicyError, request_policy
from agents.EnnoScholarV4.pricing import resolve, cost, reserve_bound
from agents.EnnoScholarV4.routing import STAGES, model_for
from agents.EnnoScholarV4.writing_job import fingerprint


class Provider:
    def __init__(self, error=None):
        self.sent, self.error = [], error

    @property
    def responses(self):
        return self

    def with_options(self, **_):
        return self

    def create(self, **kwargs):
        self.sent.append(kwargs)
        if self.error:
            raise self.error
        return NS(model=MODEL, id="resp_offline", service_tier="default", output=[],
            usage={"input_tokens": 1000, "output_tokens": 500,
                "input_tokens_details": {"cached_tokens": 200, "cache_write_tokens": 300},
                "output_tokens_details": {"reasoning_tokens": 400}})


@pytest.mark.parametrize("old", ["gpt-5.6-sol", "gpt-6.1-sol", "gpt-4.1", "unknown"])
def test_old_env_and_direct_overrides_cannot_select_another_model(monkeypatch, old):
    monkeypatch.setenv("ENNOSCHOLAR_V4_MODEL", old)
    monkeypatch.setenv("ENNOSCHOLAR_V4_TASK_MODELS", json.dumps({"reading": old, "writing": old}))
    config = ScholarV4Config.from_env()
    assert config.model == MODEL and config.task_models == ""
    assert replace(config, model=old, task_models='{"writing":"gpt-4.1"}').model == MODEL


@pytest.mark.parametrize("kind", sorted({k for kinds in STAGES.values() for k in kinds} | {"future_kind"}))
def test_every_stage_and_parallel_client_clone_passes_the_same_edge_policy(kind):
    config = ScholarV4Config(api_key="mock", prompt_cache_mode="explicit")
    provider, records = Provider(), {}
    client = MeteredClient(provider, config, records=records).with_options(timeout=1)
    assert model_for(config, kind) == MODEL
    client.responses.create(model="gpt-4.1", input=[], instructions="Stable " * 2000,
        max_output_tokens=7000, _ennoscholar_task={"kind": kind, "attempt_id": kind})
    assert provider.sent[0]["model"] == MODEL and provider.sent[0]["service_tier"] == "default"
    assert records[kind]["model_requested"] == records[kind]["model_resolved"] == MODEL
    assert records[kind]["cache_layout"]["breakpoint"] == "instructions"
    assert provider.sent[0]["input"][0]["content"][0]["text"] == "Stable " * 2000


@pytest.mark.parametrize("error", [TimeoutError("offline"), RuntimeError("model unavailable")])
def test_provider_errors_never_fall_back(error):
    provider = Provider(error)
    client = MeteredClient(provider, ScholarV4Config(api_key="mock"))
    with pytest.raises(type(error)):
        client.responses.create(model="gpt-5.6-sol", input=[], max_output_tokens=100)
    assert len(provider.sent) == 1 and provider.sent[0]["model"] == MODEL


@pytest.mark.parametrize("kwargs", [
    {"reasoning": {"effort": "unbounded"}}, {"service_tier": "priority"},
    {"max_output_tokens": 128001}, {"extra_body": {"model": "gpt-4.1"}},
    {"extra_body": {"prompt_cache_options": {"ttl": "12h"}}},
])
def test_incompatible_or_unpriced_parameters_fail_before_any_transport(kwargs):
    provider = Provider()
    with pytest.raises(ValueError):
        MeteredClient(provider, ScholarV4Config(api_key="mock")).responses.create(input=[], **kwargs)
    assert provider.sent == []


def test_standard_luna_cost_is_disjoint_and_long_context_and_reservations_are_protective():
    config = ScholarV4Config(api_key="mock")
    price = resolve(config)
    assert (price.input, price.cache_read, price.cache_write, price.output) == (.1, .01, .125, .5)
    raw = Provider().create().usage
    expected = (500*.1 + 200*.01 + 300*.125 + 500*.5)/1e6
    assert cost(raw, price)["usd"] == pytest.approx(expected)
    assert reserve_bound(1000, 7000, price) >= expected
    long = cost({"input_tokens": 300000, "output_tokens": 1000}, price)
    assert long["long_context"] and long["usd"] == pytest.approx((300000*.2 + 1000*.75)/1e6)
    # Old prices remain available explicitly for historical audit, never for routing.
    assert resolve(config, "gpt-5.6-sol").input == 4


def test_a_waiting_job_cannot_silently_continue_with_its_old_model_fingerprint():
    # Exercise the same fingerprint comparison as local and Celery continuations.
    config = ScholarV4Config(api_key="mock")
    current = fingerprint(config, "sqlite:///offline.db")
    assert current["model"] == MODEL
    old = {**current, "model": "gpt-5.6-sol"}
    from agents.EnnoScholarV4.writing_job import mismatch
    assert mismatch(old, current) == ["model"]


def test_unexpected_returned_model_is_reported_without_a_second_call():
    class WrongModel(Provider):
        def create(self, **kwargs):
            response = super().create(**kwargs)
            response.model = "gpt-6.1-sol"
            return response
    provider, records = WrongModel(), {}
    client = MeteredClient(provider, ScholarV4Config(api_key="mock"), records=records)
    with pytest.raises(ModelPolicyError, match="hors politique") as failure:
        client.responses.create(input=[], _ennoscholar_task={"attempt_id": "wrong"})
    from agents.EnnoScholarV4.stops import technical_diagnostic
    diagnostic = technical_diagnostic(failure.value)
    assert diagnostic["reason"] == "configuration_error" and not diagnostic["retryable"]
    assert diagnostic["code"] == "provider_model_or_tier_mismatch"
    assert len(provider.sent) == 1
    assert records["wrong"]["model_requested"] == MODEL
    assert records["wrong"]["model_resolved"] == "gpt-6.1-sol"
    assert "cost" not in records["wrong"]


def test_live_launchers_and_sources_have_no_direct_unmetered_v4_generation():
    root = Path(__file__).resolve().parents[2]
    for path in (root / "agents/EnnoScholarV4").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute) and node.func.attr == "create"
            and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "responses"]
        if calls:
            assert path.name in {"agent.py", "finance.py"}, path
    launcher = Path(r"C:\Users\hp\.codex\runtime\ennoscholar-v4\start_backend.py")
    if launcher.exists():
        source = launcher.read_text(encoding="utf-8")
        assert "model_policy import MODEL as REQUIRED_MODEL" in source
        assert 'REQUIRED_MODEL = "gpt-5.6-sol"' not in source
