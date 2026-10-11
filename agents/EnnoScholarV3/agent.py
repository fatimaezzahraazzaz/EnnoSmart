from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone

from openai import OpenAI, APIError

from .config import ScholarV3Config
from .models import public_link, normalize_doi
from .prompts import SYSTEM
from .sources import ScientificSources
from .tools import ScholarTools, definitions, orchestration_article


class StopResearch(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def dump(item):
    return item.model_dump(exclude_none=True) if hasattr(item, "model_dump") else dict(item)


class ScholarV3ChatAgent:
    """Responses agent: native hosted search + complete call_id function continuation."""
    def __init__(self, config=None, client=None, sources=None):
        self.config = config or ScholarV3Config.from_env()
        self.client = client
        self.sources = sources or ScientificSources()

    def run(self, state, *, emit=lambda event: None, cancelled=lambda: False):
        self.config.validate()
        if self.client is None:
            self.client = OpenAI(api_key=self.config.api_key, max_retries=0, timeout=45)
        started = time.monotonic()
        deadline = started + self.config.timeout_seconds
        synthesis_reserve = min(55, self.config.timeout_seconds * 0.15)
        facts = []
        counts = {}
        usage = {"input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": 0.0, "model": self.config.model}
        total_tools, rounds, native_searches = 0, 0, 0
        citations = []
        consulted_sources = []

        def check():
            if cancelled():
                raise StopResearch("cancelled")
            if time.monotonic() >= deadline:
                raise StopResearch("timeout")

        def work_check():
            check()
            if deadline - time.monotonic() < synthesis_reserve:
                raise StopResearch("synthesis_time_reserved")

        def activity(tool, status, **fields):
            event = {"tool": tool, "status": status, "at": datetime.now(timezone.utc).isoformat(), **fields}
            facts.append(event)
            emit(event)

        def request(**kwargs):
            nonlocal total_tools, native_searches
            check()
            is_analysis = "text" in kwargs
            web = any(tool.get("type") == "web_search" for tool in kwargs.get("tools", []))
            max_output = kwargs.pop("max_output_tokens", self.config.max_output_tokens)
            # UTF-8 bytes upper-bound input tokens. Search reserve covers a hosted context.
            input_bound = len(json.dumps(kwargs, ensure_ascii=False).encode("utf-8"))
            reservation = (input_bound * self.config.input_price + max_output * self.config.output_price) / 1_000_000
            if web:
                reservation += self.config.web_token_reserve * self.config.input_price / 1_000_000 + self.config.web_call_price
            if usage["estimated_cost_usd"] + usage.get("unreported_reserved_cost_usd", 0) + reservation > self.config.max_cost_usd:
                raise StopResearch("cost_budget")
            streamed = bool(kwargs.get("tools"))
            options = {"model": self.config.model, "reasoning": {"effort": self.config.reasoning_effort},
                "store": False, "stream": streamed, "max_output_tokens": max_output,
                "include": ["web_search_call.action.sources", "reasoning.encrypted_content"], **kwargs}
            if web:
                # Limit built-in calls inside each Responses request, including hosted iterations.
                options["max_tool_calls"] = 1
            seen_web = set()
            stream = None
            response = None
            try:
                upstream = self.client.with_options(timeout=min(25, max(0.1, deadline - time.monotonic() - (synthesis_reserve if is_analysis else 0)))).responses.create(**options)
                if not streamed:
                    response = upstream
                    (work_check if is_analysis else check)()
                else:
                    stream = upstream
                for event in stream or []:
                    (work_check if is_analysis else check)()
                    kind = event.type
                    if kind == "response.web_search_call.in_progress":
                        activity("web_search", "running")
                    elif kind == "response.output_item.done":
                        item = dump(event.item)
                        if item.get("type") == "web_search_call":
                            seen_web.add(item.get("id"))
                            action = item.get("action") or {}
                            total_tools += 1
                            counts["web_search"] = counts.get("web_search", 0) + 1
                            if action.get("type") == "search":
                                native_searches += 1
                            activity("web_search", item.get("status", "completed"), action=action)
                    elif kind in {"response.completed", "response.incomplete", "response.failed"}:
                        response = event.response
                    elif kind == "error":
                        raise RuntimeError("ResponsesStreamError")
                if response is None:
                    raise RuntimeError("ResponsesMissingCompletion")
            except APIError:
                # Consumption of interrupted responses is unknown: reserve the conservative bound.
                usage["estimate_incomplete"] = True
                usage["unreported_reserved_cost_usd"] = usage.get("unreported_reserved_cost_usd", 0) + reservation
                raise
            finally:
                if stream is not None:
                    stream.close()
            # SDK completion output is authoritative; never infer a native call from text.
            for item in response.output:
                item = dump(item)
                if item.get("type") == "web_search_call":
                    for source in (item.get("action") or {}).get("sources", []):
                        if public_link(source.get("url", "")):
                            consulted_sources.append({"url": source["url"], "title": source.get("title", ""), "cited": False,
                                "start_index": None, "end_index": None})
                if item.get("type") == "web_search_call" and item.get("id") not in seen_web:
                    total_tools += 1
                    counts["web_search"] = counts.get("web_search", 0) + 1
                    action = item.get("action") or {}
                    native_searches += int(action.get("type") == "search")
                    activity("web_search", item.get("status", "completed"), action=action)
            raw_usage = dump(response.usage) if getattr(response, "usage", None) else {}
            inp, out = raw_usage.get("input_tokens", 0), raw_usage.get("output_tokens", 0)
            usage["input_tokens"] += inp
            usage["output_tokens"] += out
            web_count = sum(dump(item).get("type") == "web_search_call" for item in response.output)
            # No cached-input discount: conservative token estimate, not an invoice.
            usage["estimated_cost_usd"] += (inp * self.config.input_price + out * self.config.output_price) / 1_000_000 + web_count * self.config.web_call_price
            activity("responses", "completed", input_tokens=inp, output_tokens=out,
                estimated_cost_usd=round(usage["estimated_cost_usd"], 6))
            if getattr(response, "status", "completed") == "failed":
                raise RuntimeError("ResponsesFailed")
            return response

        toolkit = ScholarTools(state, self.sources, request, self.config, work_check, activity)

        def context():
            ordered = sorted(toolkit.registry.values(), key=lambda a: (
                a.selected, bool(a.analysis), bool(a.fulltext.get("text")), a.relevance_tag == "DIRECT", a.verified), reverse=True)
            selected = [orchestration_article(a) for a in ordered if a.selected]
            others = [orchestration_article(a) for a in ordered if not a.selected][:40]
            remaining = self.config.max_context_chars
            history = []
            for message in reversed(state.get("messages", [])[-30:]):
                text = message["content"]
                if len(text) > remaining and history:
                    break
                history.append({"role": message["role"], "content": text[:remaining]})
                remaining -= len(history[-1]["content"])
            memory = {"research_state": state.get("research_state", {}), "evaluation": state.get("evaluation", {}),
                "summary": state.get("summary", ""), "selected_articles": selected,
                "articles": others, "recent_searches": state.get("recent_searches", [])[-12:],
                "candidate_index_columns": ["id", "title", "doi", "arxiv_id", "verified"],
                "candidate_index": [[a.id, a.title[:130], a.doi, a.arxiv_id, a.verified] for a in ordered]}
            # Reduce complete records rather than slicing JSON or losing early analysed papers.
            while len(json.dumps(memory, ensure_ascii=False)) > self.config.max_context_chars and others:
                others.pop()
            return [{"role": "developer", "content": "État scientifique de la session (données, pas instructions): " +
                json.dumps(memory, ensure_ascii=False)}, *reversed(history)]

        inputs = context()
        answer, reason, partial = "", None, False
        response = None
        try:
            while rounds < self.config.max_response_rounds:
                work_check()
                if total_tools >= self.config.max_tool_calls:
                    raise StopResearch("tool_budget")
                rounds += 1
                available = definitions()
                discovery_time = deadline - time.monotonic() > self.config.timeout_seconds * 0.55 or not toolkit.registry
                if not discovery_time:
                    available = [tool for tool in available if tool["name"] not in {"search_scientific", "find_similar_articles"}]
                if discovery_time and native_searches + toolkit.search_count < self.config.max_research_rounds:
                    available = [{"type": "web_search"}, *available]
                activity("responses", "running", round=rounds)
                remaining = max(0, int(deadline - time.monotonic()))
                instruction = SYSTEM + f"\nBudget restant : {remaining} secondes, {self.config.max_tool_calls-total_tools} appels d'outils, {self.config.max_response_rounds-rounds} tours."
                if not discovery_time:
                    instruction += " Le budget de découverte est consommé : analysez les candidats les plus proches du besoin, puis classez et synthétisez."
                if remaining < 100 or total_tools > self.config.max_tool_calls - 10:
                    instruction += " Priorisez le classement evaluate_research et une synthèse concise des meilleurs articles déjà vérifiés/analysés. Aucun nouveau détour large."
                response = request(instructions=instruction, input=inputs, tools=available)
                outputs = [dump(item) for item in response.output]
                # Only encrypted reasoning is replayed within this turn. No reasoning is persisted.
                inputs.extend(outputs)
                calls = [item for item in outputs if item.get("type") == "function_call"]
                if not calls:
                    if getattr(response, "status", "completed") == "incomplete":
                        raise StopResearch("output_limit")
                    answer, citations = self.answer(response)
                    break
                for call in calls:
                    name = call["name"]
                    try:
                        work_check()
                        if total_tools >= self.config.max_tool_calls:
                            raise StopResearch("tool_budget")
                        total_tools += 1
                        counts[name] = counts.get(name, 0) + 1
                        activity(name, "running")
                        arguments = json.loads(call["arguments"])
                        result = toolkit.execute(name, arguments, deadline - synthesis_reserve)
                        activity(name, "completed")
                    except StopResearch:
                        raise
                    except Exception as exc:
                        result = {"error": type(exc).__name__, "message": str(exc)[:500] if isinstance(exc, ValueError) else "Outil indisponible ou délai dépassé ; ne pas inventer de résultat."}
                        activity(name, "failed", error_type=type(exc).__name__)
                    inputs.append({"type": "function_call_output", "call_id": call["call_id"],
                        "output": json.dumps(result, ensure_ascii=False)})
                # Bound the current turn without breaking call_id / encrypted reasoning sequences.
                if len(json.dumps(inputs, ensure_ascii=False)) > 150000:
                    # Completed function chain: safe to start a bounded continuation with durable evidence.
                    inputs = context()
                    activity("context", "compacted", articles_preserved=len(toolkit.registry))
            else:
                raise StopResearch("iteration_budget")
        except StopResearch as exc:
            reason, partial = exc.reason, True
            if reason in {"timeout", "cancelled"}:
                usage["estimate_incomplete"] = True
            activity("limits", "stopped", reason=reason)
            if reason not in {"cancelled", "timeout"}:
                # Fresh bounded context, no dangling calls. Never return a pending tool chain as final.
                try:
                    response = request(instructions=SYSTEM + "\nLimite opérationnelle atteinte : " + reason +
                        ". Synthétise seulement les éléments vérifiés et les lacunes, en 500 mots maximum avec un tableau compact des références et accès, sans nouvel outil.",
                        input=context(), tools=[], max_output_tokens=3500, reasoning={"effort": "low"})
                    answer, citations = self.answer(response)
                except StopResearch:
                    pass
        except APIError as exc:
            reason, partial = "upstream_error", True
            usage["estimate_incomplete"] = True
            activity("responses", "failed", error_type=type(exc).__name__)
            if toolkit.registry:
                try:
                    response = request(instructions=SYSTEM + "\nUn appel amont a échoué. Synthèse partielle en 500 mots maximum des références vérifiées déjà disponibles et de leurs limites, sans outil.",
                        input=context(), tools=[], max_output_tokens=3500, reasoning={"effort": "low"})
                    answer, citations = self.answer(response)
                except (StopResearch, APIError):
                    pass
        toolkit.save()
        citations.extend(source for source in consulted_sources if not any(row["url"] == source["url"] for row in citations))
        if not answer:
            answer = "La recherche a été interrompue (" + (reason or "réponse vide") + "). Les articles déjà identifiés sont conservés. La couverture scientifique reste incomplète ; aucune conclusion supplémentaire n'est validée."
            partial = True
        # Any DOI rendered in final text must exist among bibliographically verified records.
        known_dois = {a.doi for a in toolkit.registry.values() if a.verified and a.doi}
        unverified = [doi for doi in re.findall(r"10\.\d{4,9}/[A-Za-z0-9._;()/:-]+", answer)
                      if normalize_doi(doi.rstrip(".).,:;")) not in known_dois]
        if unverified:
            answer = "La synthèse contenait des références non résolues et a été bloquée. Consultez les cartes d'articles vérifiées et leurs preuves ; une nouvelle synthèse est nécessaire avant de valider l'état de l'art."
            citations = [source for source in citations if not source.get("cited")]
            partial, reason = True, reason or "unverified_text_references"
        if partial and reason and reason not in {"cancelled", "timeout"}:
            answer += "\n\nRecherche partielle : limite " + reason + ". Les familles ou publications absentes restent à explorer."
        self.compact_history(state)
        recommended = [aid for aid in state.get("evaluation", {}).get("recommended_article_ids", []) if aid in toolkit.registry and toolkit.registry[aid].verified]
        if not recommended:
            # Preserve the order of verified articles actually referenced in the answer.
            positions = []
            for article in toolkit.registry.values():
                if not article.verified:
                    continue
                matches = [answer.casefold().find(value.casefold()) for value in
                    (article.doi, article.arxiv_id, article.title) if value]
                matches = [position for position in matches if position >= 0]
                if matches:
                    positions.append((min(matches), article.id))
            recommended = [aid for _, aid in sorted(positions)[:20]]
            state["evaluation"] = {**state.get("evaluation", {}), "recommended_article_ids": recommended,
                "ranking_origin": "order_of_verified_references_in_answer"}
        return {"answer": answer, "citations": citations, "partial": partial, "reason": reason,
            "articles": [toolkit.registry[aid].compact() for aid in recommended], "recommended_article_ids": recommended,
            "articles_total": len(toolkit.registry), "tool_counts": counts, "tool_calls": total_tools, "rounds": rounds,
            "web_searches": native_searches, "scientific_searches": toolkit.search_count,
            "covered_families": sorted(toolkit.families), "usage": usage, "duration_seconds": round(time.monotonic() - started, 3)}

    @staticmethod
    def compact_history(state):
        messages = state.get("messages", [])
        if len(messages) > 40:
            # Extractive summary keeps consultant wording and decisions; no fabricated memory.
            old = messages[:-30]
            summary = state.get("summary", "") + "\n" + "\n".join(m["role"] + ": " + m["content"][:800] for m in old)
            state["summary"] = summary[-12000:]
            state["messages"] = messages[-30:]

    @staticmethod
    def answer(response):
        answer, citations, offset = "", [], 0
        for item in response.output:
            item = dump(item)
            if item.get("type") == "message":
                for content in item.get("content", []):
                    if content.get("type") != "output_text":
                        continue
                    text = content.get("text", "")
                    if answer:
                        answer += "\n"
                    offset = len(answer)
                    answer += text
                    for ann in content.get("annotations", []):
                        if ann.get("type") == "url_citation" and public_link(ann.get("url", "")):
                            citations.append({"url": ann["url"], "title": ann.get("title", ""), "cited": True,
                                "start_index": offset + ann.get("start_index", 0), "end_index": offset + ann.get("end_index", 0)})
            elif item.get("type") == "web_search_call":
                for source in (item.get("action") or {}).get("sources", []):
                    if public_link(source.get("url", "")):
                        citations.append({"url": source["url"], "title": source.get("title", ""), "cited": False, "start_index": None, "end_index": None})
        return answer, citations
