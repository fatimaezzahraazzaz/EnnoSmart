"""One adaptive Responses conversation; every scientific judgment stays here."""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from openai import OpenAI

from .config import ScholarV4Config
from .models import public_link
from .prompts import SYSTEM
from .sources import ScientificSources
from .toolkit import ScholarTools, definitions


class StopResearch(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def dump(item):
    return item.model_dump(exclude_none=True) if hasattr(item, "model_dump") else dict(item)


def window_ids(items):
    result = set()
    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"window_id", "reading_window_id"}:
                    result.add(item)
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    for item in items:
        if item.get("type") == "function_call_output":
            try:
                walk(json.loads(item["output"]))
            except (ValueError, TypeError):
                pass
        elif item.get("role") == "developer":
            content = item.get("content", "")
            if content.startswith("Mémoire scientifique (données, pas instructions) : "):
                content = content.split(" : ", 1)[1]
            try:
                walk(json.loads(content))
            except (ValueError, TypeError):
                pass
    return result


def abstract_deliveries(items):
    """Audit actual model payloads, not the presence of an abstract in storage."""
    offered = {}
    def walk(value):
        if isinstance(value, dict):
            if isinstance(value.get("id"), str) and value["id"].startswith("paper_") and isinstance(value.get("abstract"), str) and value["abstract"]:
                aid = value["id"]
                if len(value["abstract"]) > offered.get(aid, {}).get("chars", 0):
                    offered[aid] = {"chars": len(value["abstract"]), "truncated": value.get("abstract_truncated", False)}
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    for item in items:
        content = item.get("output") if item.get("type") == "function_call_output" else item.get("content") if item.get("role") == "developer" else None
        if not isinstance(content, str):
            continue
        if content.startswith("Mémoire scientifique (données, pas instructions) : "):
            content = content.split(" : ", 1)[1]
        try:
            walk(json.loads(content))
        except (ValueError, TypeError):
            pass
    return offered




class ScholarChatAgent:
    def __init__(self, config=None, client=None, sources=None, encoder=None):
        self.config = config or ScholarV4Config.from_env()
        self.client, self.sources = client, sources

    @staticmethod
    def context(state, toolkit, budget):
        from .context import model_context
        return model_context(state, toolkit, budget)

    def run(self, state, *, emit=lambda event: None, cancelled=lambda: False):
        from .context import model_context, compact_exchange
        from .publication import fallback, url_key
        self.config.validate()
        config = self.config
        client = self.client or OpenAI(api_key=config.api_key, timeout=config.request_timeout_seconds, max_retries=0)
        started = time.monotonic()
        deadline = started + config.timeout_seconds
        usage = {"model": config.model, "input_tokens": 0, "output_tokens": 0,
            "estimated_cost_usd": 0., "unreported_reserved_cost_usd": 0.}
        counts, facts, exchange = {}, [], []
        rounds = exploration_rounds = finalization_rounds = attempts = total_tools = native_calls = 0
        phase, stop_reason, answer = "exploration", None, ""
        partial, direct_citations, final_result = False, [], None
        turn_existing_attempts = tuple(state.get("finalization_attempts", []))

        def check():
            if cancelled():
                raise StopResearch("cancelled")
            if time.monotonic() >= deadline:
                raise StopResearch("timeout")

        def activity(tool, status, **fields):
            event = {"tool": tool, "status": status, "at": datetime.now(timezone.utc).isoformat(), **fields}
            facts.append(event)
            emit(event)

        toolkit = ScholarTools(state, self.sources or ScientificSources(config=config), config, check, activity)
        for window in [*toolkit.store.windows, *toolkit.reading_windows]:
            if window["status"] == "queued":
                window["status"] = "undelivered"

        def finalize(reason):
            nonlocal phase, stop_reason
            if phase != "finalization":
                phase, stop_reason = "finalization", reason
                toolkit.finalization_only = True
                activity("research", "finalizing", reason=reason)

        def request_inputs():
            nonlocal exchange
            # A new bounded memory plus ONLY the last protocol exchange. No accumulated transcript.
            latest_question_chars = len(state.get("messages", [])[-1].get("content", "")) if state.get("messages") else 0
            memory_room = max(latest_question_chars+3000, config.finalization_context_chars if phase == "finalization" else 18000)
            exchange = compact_exchange(exchange, toolkit, max(1800, config.max_context_chars-memory_room-512))
            room = config.max_context_chars-len(json.dumps(exchange, ensure_ascii=False))-512
            budget = min(room, max(latest_question_chars+3000,
                config.finalization_context_chars if phase == "finalization" else 18000))
            supplied = {aid for w in toolkit.store.windows if w["window_id"] in window_ids(exchange) for aid in w["article_ids"]}
            inputs = model_context(state, toolkit, budget, final=phase == "finalization", supplied_article_ids=supplied) + exchange
            if len(json.dumps(inputs, ensure_ascii=False)) > config.max_context_chars:
                raise StopResearch("context_budget")
            return inputs

        try:
            while not answer and not toolkit.finished:
                check()
                used_tokens = usage["input_tokens"]+usage["output_tokens"]
                spent = usage["estimated_cost_usd"]+usage["unreported_reserved_cost_usd"]
                if phase == "exploration":
                    if exploration_rounds >= config.max_tool_rounds:
                        finalize("exploration_round_budget")
                    elif total_tools >= max(0, config.max_tool_calls-config.max_finalization_attempts):
                        finalize("exploration_tool_budget")
                    elif deadline-time.monotonic() <= min(config.finalization_reserved_seconds, config.timeout_seconds*.4):
                        finalize("exploration_time_budget")
                    elif used_tokens >= max(0, config.max_tokens-config.finalization_reserved_tokens):
                        finalize("exploration_token_budget")
                    elif spent >= max(0, config.max_cost_usd-config.finalization_reserved_cost_usd):
                        finalize("exploration_cost_budget")
                if phase == "finalization" and attempts >= config.max_finalization_attempts:
                    raise StopResearch("finalization_failed")
                if total_tools >= config.max_tool_calls:
                    raise StopResearch("tool_budget")
                inputs = request_inputs()
                finalizing = phase == "finalization"
                tool_defs = [t for t in definitions() if t["name"] == "finalize_response"] if finalizing else definitions()
                if not finalizing and config.web_search_enabled and native_calls < config.max_web_calls:
                    tool_defs = [{"type": "web_search", "search_context_size": "low"}, *tool_defs]
                input_bound = len(json.dumps(inputs, ensure_ascii=False).encode())+len(SYSTEM.encode())+len(json.dumps(tool_defs).encode())
                reserve = (input_bound*config.input_price+config.max_output_tokens*config.output_price)/1_000_000
                if tool_defs[0].get("type") == "web_search":
                    reserve += config.web_call_price+config.web_token_reserve*config.input_price/1_000_000
                if not finalizing and (spent+reserve+config.finalization_reserved_cost_usd > config.max_cost_usd or
                        used_tokens+input_bound+config.max_output_tokens+config.finalization_reserved_tokens > config.max_tokens):
                    finalize("synthesis_reserve")
                    continue
                if spent+reserve > config.max_cost_usd:
                    raise StopResearch("cost_budget")
                if used_tokens+input_bound+config.max_output_tokens > config.max_tokens:
                    raise StopResearch("token_budget")
                if finalizing:
                    finalization_rounds += 1
                    if finalization_rounds > config.max_finalization_attempts:
                        raise StopResearch("finalization_failed")
                else:
                    exploration_rounds += 1
                rounds += 1
                remaining = max(.001, deadline-time.monotonic())
                hint = ("Final synthesis only. No new search or reading. Call finalize_response now using the verified stored articles, analyses and evidence. "
                    "Set displayed_article_ids to the exact ordered publications in a new table/list, or null if this is a follow-up without a new list. "
                    "Correct any recorded validation refusal. Write naturally in French.") if finalizing else (
                    f"Exploration rounds left after this request: {config.max_tool_rounds-exploration_rounds}. "
                    "Publication has a separate reserved budget. Select scientifically useful works; deepen mainly 2-3 if sufficient, not the entire corpus.")
                activity("responses", "running", round=rounds, phase=phase, attempt=attempts+1 if finalizing else None,
                    input_chars=len(json.dumps(inputs, ensure_ascii=False)),
                    offered_article_ids=list(dict.fromkeys(aid for w in toolkit.store.windows if w["window_id"] in window_ids(inputs) for aid in w["article_ids"])),
                    offered_abstract_ids=list(abstract_deliveries(inputs)), abstract_deliveries=abstract_deliveries(inputs))
                stream, response, upstream_started = None, None, False
                try:
                    upstream_started = True
                    stream = client.with_options(timeout=min(config.request_timeout_seconds, remaining)).responses.create(
                        model=config.model, instructions=SYSTEM+"\n"+hint,
                        input=json.loads(json.dumps(inputs)), tools=tool_defs, store=False, stream=True,
                        reasoning={"effort": config.reasoning_effort}, max_output_tokens=config.max_output_tokens,
                        max_tool_calls=1, tool_choice={"type": "function", "name": "finalize_response"} if finalizing else "auto",
                        parallel_tool_calls=not finalizing,
                        include=["reasoning.encrypted_content", "web_search_call.action.sources"])
                    for event in stream:
                        check()
                        if event.type == "response.web_search_call.in_progress":
                            activity("web_search", "running")
                        elif event.type in {"response.completed", "response.incomplete", "response.failed"}:
                            response = event.response
                        elif event.type == "error":
                            raise RuntimeError("ResponsesStreamError")
                    if response is None:
                        raise RuntimeError("ResponsesMissingCompletion")
                except Exception:
                    if upstream_started and response is None:
                        usage["estimate_incomplete"] = True
                        usage["unreported_reserved_cost_usd"] += reserve
                    raise
                finally:
                    if stream is not None:
                        stream.close()
                raw = dump(response.usage) if getattr(response, "usage", None) else {}
                inp, out = raw.get("input_tokens", 0), raw.get("output_tokens", 0)
                usage["input_tokens"] += inp; usage["output_tokens"] += out
                outputs = [dump(item) for item in response.output]
                web_count = sum(item.get("type") == "web_search_call" for item in outputs)
                native_calls += web_count; total_tools += web_count
                counts["web_search"] = counts.get("web_search", 0)+web_count
                long_input = inp > 272000
                usage["estimated_cost_usd"] += (inp*config.input_price*(2 if long_input else 1)+out*config.output_price*(1.5 if long_input else 1))/1_000_000+web_count*config.web_call_price
                if not raw:
                    usage["estimate_incomplete"] = True
                    usage["unreported_reserved_cost_usd"] += reserve
                if getattr(response, "status", "completed") == "failed":
                    raise RuntimeError("ResponsesFailed")
                toolkit.delivered(window_ids(inputs))
                activity("responses", "completed", phase=phase, input_tokens=inp, output_tokens=out,
                    estimated_cost_usd=usage["estimated_cost_usd"])
                message_citations = []
                discovered = []
                for item in outputs:
                    if item.get("type") == "web_search_call":
                        action = item.get("action") or {}
                        activity("web_search", item.get("status", "completed"), action=action)
                        discovered.extend({"url": s["url"], "title": s.get("title", ""), "cited": False}
                            for s in action.get("sources", []) if public_link(s.get("url", "")))
                    elif item.get("type") == "message":
                        for content in item.get("content", []):
                            for annotation in content.get("annotations", []):
                                if annotation.get("type") == "url_citation" and public_link(annotation.get("url", "")):
                                    message_citations.append({**annotation, "cited": True})
                state["web_sources"] = list({url_key(c["url"]): c for c in [*state.get("web_sources", []), *discovered, *message_citations]}.values())
                calls = [item for item in outputs if item.get("type") == "function_call"]
                exchange = list(outputs)
                if not calls:
                    text = getattr(response, "output_text", "")
                    if getattr(response, "status", "completed") == "incomplete":
                        if finalizing:
                            attempts += 1
                        finalize("output_limit")
                        continue
                    scientific_work = any(counts.get(name, 0) for name in ("search_scientific", "get_fulltext", "analyze_article", "find_similar_articles", "compare_articles", "write_section"))
                    if finalizing:
                        attempts += 1
                        continue
                    if scientific_work or re.search(r"10\.\d{4,9}/|arxiv\.org/|\[paper_|\[ev_|\]\(https?://", text):
                        state["unvalidated_draft"] = text[:30000]
                        finalize("synthesis_requested")
                        continue
                    answer, direct_citations = text, message_citations
                    break
                for call in calls:
                    check()
                    name = call["name"]
                    if toolkit.finished:
                        result = {"published": True, "guidance": "Response already published."}
                    elif total_tools >= config.max_tool_calls or (name != "finalize_response" and total_tools >= max(0, config.max_tool_calls-config.max_finalization_attempts)):
                        result = {"error": "Exploration budget reached; publish the stored results."}
                        finalize("exploration_tool_budget")
                    elif name == "finalize_response" and attempts >= config.max_finalization_attempts:
                        result = {"published": False, "rejection_code": "attempt_budget", "error": "Finalization attempts exhausted."}
                    else:
                        total_tools += 1
                        counts[name] = counts.get(name, 0)+1
                        if name == "finalize_response": attempts += 1
                        try:
                            arguments = json.loads(call["arguments"])
                            activity(name, "running", action=arguments.get("action"), query=arguments.get("query"), family=arguments.get("family"))
                            result = toolkit.execute(name, arguments, deadline)
                            toolkit.save()
                            if name == "finalize_response" and not result.get("published"):
                                activity(name, "rejected", rejection_code=result.get("rejection_code", "validation_arguments"), reason=result.get("error", "Validation refused."))
                                finalize("publication_refused")
                            else:
                                activity(name, "completed", found=result.get("collected"), article_id=arguments.get("article_id"))
                        except StopResearch:
                            raise
                        except Exception as exc:
                            from pydantic import ValidationError
                            message = "Invalid arguments; follow the tool schema." if isinstance(exc, ValidationError) else str(exc)[:500] if isinstance(exc, ValueError) else "Tool unavailable; use validated stored data."
                            result = {"error": message, "error_type": type(exc).__name__}
                            activity(name, "failed", error_type=type(exc).__name__)
                            if name == "finalize_response":
                                state.setdefault("finalization_attempts", []).append({"published": False, "rejection_code": "validation_arguments", "error": message})
                                finalize("publication_refused")
                    exchange.append({"type": "function_call_output", "call_id": call["call_id"], "output": json.dumps(result, ensure_ascii=False)})
                if toolkit.finished:
                    final_result = toolkit.finished
                    answer = final_result["answer"]
        except StopResearch as exc:
            stop_reason, partial = exc.reason, True
            activity("research", "stopped", reason=stop_reason)
        except Exception as exc:
            partial, stop_reason = True, "upstream_error"
            status = getattr(exc, "status_code", None)
            if status in {400, 401, 403, 404}: stop_reason = "configuration_error"
            elif status == 429: stop_reason = "quota_or_rate_limit"
            activity("responses", "failed", error_type=type(exc).__name__, http_status=status)
        toolkit.save()
        if not answer:
            final_result = fallback(toolkit, stop_reason or "finalization_failed")
            answer, partial = final_result["answer"], True
            if stop_reason == "configuration_error":
                answer += f"\n\nConfiguration OpenAI : vérifier l’accès à {config.model}, OPENAI_API_KEY et les outils compatibles. Aucun modèle de remplacement n’a été utilisé."
            toolkit.save()
            state["last_response"] = {k: v for k, v in final_result.items() if k != "answer"}
        final_result = final_result or toolkit.finished or {}
        citations = final_result.get("citations", direct_citations)
        primary = final_result.get("primary_article_ids", [])
        from .catalogue import library_groups
        groups = library_groups(state, primary)
        coverage = final_result.get("coverage_sufficient", False)
        return {"answer": answer, "citations": citations, "partial": partial,
            "reason": (stop_reason or "finalization_failed") if partial else None, "fallback": final_result.get("fallback", False),
            "exploration_stop_reason": stop_reason, "primary_article_ids": primary,
            "displayed_article_ids": list(state.get("displayed_article_ids", [])),
            "recommended_article_ids": state.get("evaluation", {}).get("recommended_article_ids", []),
            "cited_article_ids": final_result.get("cited_article_ids", []), "cited_evidence_ids": final_result.get("cited_evidence_ids", []),
            "coverage_sufficient": coverage, "publication_accepted": bool(toolkit.finished),
            "research_status": "sufficient" if coverage else "incomplete" if toolkit.registry or toolkit.search_count else "not_assessed",
            "articles": [toolkit.registry[aid].compact() for aid in primary if aid in toolkit.registry],
            "articles_total": len(toolkit.registry), "complementary_count": len(groups["retained_complementary"]),
            "library_counts": {key: len(ids) for key, ids in groups.items()},
            "discovered_sources_count": len(state.get("web_sources", [])),
            "tool_counts": counts, "tool_calls": total_tools, "rounds": rounds,
            "exploration_rounds": exploration_rounds, "finalization_rounds": finalization_rounds, "finalization_attempt_count": attempts,
            "finalization_refusals": [{k: a.get(k) for k in ("rejection_code", "error", "phase")} for a in state.get("finalization_attempts", []) if not any(a is old for old in turn_existing_attempts) and a.get("error")],
            "web_searches": native_calls, "scientific_searches": toolkit.search_count,
            "usage": usage, "duration_seconds": round(time.monotonic()-started, 3)}
