"""Run the backed-up agent and current agent against the same mocked research."""
from __future__ import annotations

import ast
import importlib.util
import json
import logging
import sys
from pathlib import Path

import conftest  # Establish local test imports and isolated SQLite configuration.
import pytest

import agents.EnnoScholarV2.agent as current
from agents.EnnoScholarV2.service import ScholarChatService
from context_reproduction import QUESTION
from agents.EnnoScholarV2.context import context_metrics
from orchestration_reproduction import prepare_reproduction
from test_batching import client_for_service


class TurnLog(logging.Handler):
    def __init__(self):
        super().__init__()
        self.end = None

    def emit(self, record):
        if " end=%s rounds=%s tool_calls=%s" in record.msg:
            _, outcome, rounds, total, counts, articles = record.args
            self.end = dict(termination=outcome, rounds=rounds, tool_calls=total,
                tool_counts=json.loads(counts), backend_articles=articles)


def reproduce(agent_class, *, timeouts, legacy_module=None):
    with pytest.MonkeyPatch.context() as patch:
        conftest.forbid_real_network_and_openai.__wrapped__(patch)
        run = prepare_reproduction(patch, agent_class=agent_class, timeouts=timeouts)
        if legacy_module:
            patch.setattr(legacy_module, "get_fulltext", current.get_fulltext)
        service = ScholarChatService(run.agent.config, agent=run.agent)
        handler = TurnLog()
        logger = current.logger
        original_level = logger.level
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        try:
            result = client_for_service(patch, service).post("/scholar-v2/chat", json={"message": QUESTION})
        finally:
            logger.removeHandler(handler)
            logger.setLevel(original_level)
        payload = result.json()
        session = service.sessions.get(payload.get("session_id"))
        return {**handler.end, "http_status": result.status_code, "partial": payload.get("partial"),
            "reason": payload.get("reason"), "retained_articles": len(session.articles) if session else 0,
            "fulltexts": sum(bool(record.fulltext) for record in session.articles.values()) if session else 0,
            "analyses": sum(bool(record.analysis) for record in session.articles.values()) if session else 0,
            "synthesis_only_requests": sum(request["tool_choice"] == "none" for request in run.requests),
            "requests": [{"number": index + 1, "tool_choice": request["tool_choice"],
                **context_metrics(request["input"], request["instructions"], request["tools"])}
                for index, request in enumerate(run.requests)]}


def unchanged_sections(before_path):
    before_text = before_path.read_text(encoding="utf-8")
    after_text = Path(current.__file__).read_text(encoding="utf-8")
    def functions(source):
        cls = next(node for node in ast.parse(source).body if isinstance(node, ast.ClassDef) and node.name == "ScholarChatAgent")
        return {node.name: node for node in cls.body if isinstance(node, ast.FunctionDef)}
    before, after = functions(before_text), functions(after_text)
    verified = []
    for name in ("register", "read_article"):
        assert ast.dump(before[name]) == ast.dump(after[name]), name
        verified.append(name)
    for condition in ("name == 'search_scientific'", "name == 'find_similar_articles'", "name in {'get_fulltext', 'analyze_articles', 'analyze_article'}"):
        previous = next(node for node in before["execute_tool"].body if isinstance(node, ast.If) and ast.unparse(node.test) == condition)
        following = next(node for node in after["execute_tool"].body if isinstance(node, ast.If) and ast.unparse(node.test) == condition)
        assert ast.dump(previous) == ast.dump(following), condition
        verified.append(condition)
    start = "                    metrics = context_metrics(items, current_instructions, TOOLS)"
    end = "                    request_client = client.with_options(timeout=min(self.config.timeout, remaining))"
    assert before_text.split(start, 1)[1].split(end, 1)[0] == after_text.split(start, 1)[1].split(end, 1)[0]
    verified.append("context pressure/compaction/byte safety block identical")
    return verified


def main():
    backup = Path(sys.argv[1])
    before_path = backup / "agents/EnnoScholarV2/agent.py"
    spec = importlib.util.spec_from_file_location("agents.EnnoScholarV2._before_orchestration", before_path)
    legacy = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = legacy
    spec.loader.exec_module(legacy)
    prompt_spec = importlib.util.spec_from_file_location("_before_orchestration_prompt", backup / "agents/EnnoScholarV2/prompts.py")
    prompt_module = importlib.util.module_from_spec(prompt_spec)
    prompt_spec.loader.exec_module(prompt_module)
    legacy.SYSTEM_PROMPT = prompt_module.SYSTEM_PROMPT
    report = {"mode": "mocked SDK, mocked scientific providers/fulltext; real analysis validation and HTTP route; network forbidden",
        "before": reproduce(legacy.ScholarChatAgent, timeouts=1, legacy_module=legacy),
        "after_retry_success": reproduce(current.ScholarChatAgent, timeouts=1),
        "after_two_timeouts": reproduce(current.ScholarChatAgent, timeouts=2),
        "unchanged_agent_sections": unchanged_sections(before_path)}
    assert report["before"]["http_status"] == 502 and report["before"]["tool_calls"] == 20
    assert all(report[key]["http_status"] == 200 and report[key]["tool_calls"] == 5 for key in ("after_retry_success", "after_two_timeouts"))
    target = Path(__file__).resolve().parents[2] / ".codex_tmp/scholar-v2-orchestration-reproduction.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
