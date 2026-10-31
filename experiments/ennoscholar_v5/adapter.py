"""Reuse V4 science and validators; no replacement scientific implementation."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from unittest.mock import Mock

from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.toolkit import ScholarTools
from agents.EnnoScholarV4.original import model_context, tool_definitions, window_ids, SYSTEM, DISCOVERY_GUIDANCE


@dataclass
class Decision:
    tool: str
    arguments: dict = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.
    seconds: float = 0.
    model: str = "gpt-6.1-sol"
    purpose: str = ""
    retry_reason: str = ""
    native_calls: int = 0
    uncertain_usd: float = 0.
    uncertain_tokens: int = 0
    call_id: str = ""
    sent_calls: int = 1
    response_received: bool = True
    # Further function calls of the same response, in Sol's order:
    # [{"tool", "arguments", "call_id", "purpose"}]. Executed one by one.
    calls: list = field(default_factory=list)


class ScriptedSol:
    """Explicit offline substitute. Does not measure Sol's scientific quality."""
    offline = True
    def __init__(self, choices):
        self.choices, self.calls = list(choices), []

    def choose(self, inputs, tools, progress, *, instructions=""):
        self.calls.append({"inputs": inputs, "tools": tools, "progress": progress,"instructions":instructions})
        if not self.choices:
            return Decision("pause")
        choice = self.choices.pop(0)
        return choice(inputs, progress) if callable(choice) else choice

    def reservation(self):
        # Synthetic clients expose fixture usage before the call. A future live
        # port MUST use V4's persistent MeteredClient reservation instead.
        d = self.choices[0] if self.choices else Decision("pause")
        return {"cost_usd":d.cost_usd,"tokens":d.input_tokens+d.output_tokens} if isinstance(d,Decision) else {
            "cost_usd":0.,"tokens":0}


class V4Adapter:
    instructions=SYSTEM+'\n'+DISCOVERY_GUIDANCE
    def __init__(self, config=None, sources=None, *, fixture_sources=False, article_memory=None, clock=time.monotonic):
        self.config = config or ScholarV4Config(api_key="offline-placeholder", web_search_enabled=False)
        self.config.validate()
        if self.config.model != "gpt-6.1-sol":
            raise ValueError("The prototype preserves Sol")
        self.clock, self.fixture_sources = clock, fixture_sources
        self.article_memory=article_memory
        self.sources = sources or Mock()
        if sources is None:
            self.sources.metadata.side_effect = PermissionError("No network in the prototype")
            self.sources.search.side_effect = PermissionError("No network in the prototype")
            self.sources.verify.side_effect = PermissionError("No network in the prototype")

    def toolkit(self, session):
        t = ScholarTools(session, self.sources, self.config,article_memory=self.article_memory)
        counters=session.get('v5_tool_counters',{})
        for name in ('search_count','new_candidates'):
            setattr(t,name,counters.get(name,0))
        t.deep_analysis_ids=set(counters.get('deep_analysis_ids',[]))
        # Reuse verified receipts rather than doing another registry call on
        # each node reconstruction. An explicit invalid record stays invalid.
        t.checked_ids = {aid for aid, a in t.registry.items() if a.verified and not a.metadata_errors}
        return t

    def prepare(self, session, last_response=None):
        t = self.toolkit(session)
        inputs = model_context(session, t, self.config.finalization_context_chars)
        if last_response:
            inputs.append({"role": "developer", "content":
                json.dumps({"v5_saved_tool_result":last_response}, ensure_ascii=False)})
        if len(json.dumps(inputs, ensure_ascii=False)) > self.config.max_context_chars:
            raise ValueError("Existing context limit reached; do not drop source data")
        return t, inputs

    def delivered(self, toolkit, inputs):
        toolkit.delivered(window_ids(inputs), inputs=inputs)
        toolkit.save()

    def local(self, session, decision):
        if decision.tool in {"search_scientific", "find_similar_articles"}:
            return False
        if decision.tool == "get_article":
            return all(i in session.get("articles", {}) for i in decision.arguments.get("identifiers", []))
        if decision.tool == "get_fulltext":
            ft = session["articles"][decision.arguments["article_id"]].get("fulltext", {})
            return bool(ft.get("pdf_file") and decision.arguments.get("page_start") or
                ft.get("text") and not decision.arguments.get("complement_reason") and not decision.arguments.get("candidate_urls"))
        if decision.tool == "verify_bibliography":
            return all(session["articles"][aid].get("verified") and not session["articles"][aid].get("metadata_errors")
                for aid in decision.arguments.get("article_ids", []))
        return True

    def execute(self, session, decision, *, remaining_seconds):
        if not self.local(session, decision) and not self.fixture_sources:
            raise PermissionError("Network tools require explicit future authorisation; fixtures only here")
        if decision.tool not in {d["name"] for d in tool_definitions()}:
            raise ValueError("Only existing original V4 tools are available")
        t = self.toolkit(session)
        args = dict(decision.arguments)
        # Ordinary V4 publication performs its provenance/citation/save checks.
        # Final verification is local here: no hidden metadata request is allowed.
        if decision.tool == "finalize_response":
            t.finalization_only = True
        try:
            result = t.execute(decision.tool, args, self.clock()+remaining_seconds)
        except (ValueError, PermissionError) as exc:
            result = {"error": str(exc), "error_type":type(exc).__name__}
        finally:
            t.save()
            session['v5_tool_counters']={name:getattr(t,name) for name in ('search_count','new_candidates')}
            session['v5_tool_counters']['deep_analysis_ids']=sorted(t.deep_analysis_ids)
        return result

    def writing_packet(self, session, section):
        from agents.EnnoScholarV4.writing_context import writing_context
        return writing_context(session, self.toolkit(session), section, budget=14000)


def available_tools():
    return tool_definitions()
