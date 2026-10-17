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
from .prompts import SYSTEM, instructions_for
from .sources import ScientificSources
from .toolkit import ScholarTools, definitions

# Comparison and blueprint windows per article batch (claims prioritized: methods,
# mechanisms, results, limits). Not a budget: rounds, tools and costs are unchanged.
ARGUMENT_WINDOWS = 3
# Repeated identical successful get_analysis_claims reads per section. Distinct
# articles, fields and continuation pages remain available within the run budgets.
CLAIM_READS_PER_SECTION = 4

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
        from .context import model_context, compact_exchange, ActiveReadingContextError, transmitted_evidence_ids
        from .publication import fallback, url_key
        from .scientific_reading import (reading_tasks, next_task, prepare_reading,
            completed as reading_completed, update_coverage, reading_allowance, recovery_context,
            extend_reading_focus, fit_reading_focus, focused_reading_exchange)
        from .argumentation import active_articles, comparison_batches, blueprint_status, project_observation_source, comparison_source_signature
        from .writing_progress import begin_writing, refresh_writing, next_section, confirmed_save, writing_answer
        from .writing_context import section_article_ids
        from .tools.outline import resolve_section
        self.config.validate()
        config = self.config
        state["current_turn_article_ids"] = []
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
        request_text = state.get("messages", [{}])[-1].get("content", "")
        from .instructions import capture_instructions
        capture_instructions(state, (state.get("messages") or [{}])[-1], toolkit.registry)
        # Task foci are recomputed from durable receipts (comparison offsets,
        # saved drafts, writing progress); a focus left by an interrupted run must
        # not be resent with the next request's context.
        for stale in ("argumentation_focus", "writing_focus", "writing_planning"):
            state.pop(stale, None)
        writing = begin_writing(state, request_text)
        named_articles = [aid for aid, article in toolkit.registry.items()
            if len(article.title.split(":", 1)[0].strip()) >= 5 and
            article.title.split(":", 1)[0].strip().casefold() in request_text.casefold()]
        focus_id = state.get("focus", {}).get("article_id")
        # A previous download/reading focus is a priority only when this message
        # refers to it ("cet article", "cette méthode", "celui-ci"); a multi-work
        # request, even misspelled, must not deepen one stale focus alone.
        if not re.search(r"\b(?:cet|cette|ce)\s+(?:article|publication|papier|travail|étude|etude|méthode|methode|approche|modèle|modele)\b|"
                r"\bcel(?:ui|le)[- ]ci\b|\bthis\s+(?:paper|article|work|study|method)\b|\bl['’](?:article|étude|etude)\s+(?:actuel|en\s+cours)\b",
                request_text, re.I):
            focus_id = None
        priority_ids = tuple(dict.fromkeys([*named_articles, *([focus_id] if focus_id else [])]))
        state["current_turn_article_ids"] = list(priority_ids)
        requested_fields = tuple(field for pattern, field in (
            (r"méthod|method|architecture|mécanism|mechanism", "method"),
            (r"expérien|experimen|protocole|testé|tested", "experimental_setup"),
            (r"résultat|result|chiffr|metric|métrique|performance|temps de calcul", "results"),
            (r"limit|insuffisan|faibless|constraint", "limitations"))
            if re.search(pattern, request_text, re.I))
        detail_request = bool(re.search(
            r"analys|compare|comparai|expliqu|approfond|méthod|method|mécanism|mechanism|résultat|result|limit|insuffisan|expérien|protocole",
            request_text, re.I))
        writing_round_allowance = (len(writing.get("target_ids", []))*3+4) if writing else 0
        writing_attempts = 0
        planning_attempts = 0
        automatic_reading = bool(state.get("automatic_reading_focus") or
            state.get("scientific_scope", {}).get("work_type") == "state_of_art" or
            re.search(r"état\s+de\s+l[’']art|state\s+of\s+(?:the\s+)?art|rédig|redig|rédac", request_text, re.I) or
            (detail_request and bool(toolkit.registry)))
        if writing and writing["mode"] == "section":
            automatic_reading = False
            # A targeted revision owns this run; stale recovery/reading tasks from
            # an earlier request must not preempt the existing draft and sources.
            state.pop("automatic_reading_focus", None)
            state.pop("reading_recovery_focus", None)
        # Drafting from a saved plan reuses the stored cards: only works whose core
        # dimensions were never read are read now; depth stays available on demand
        # (get_analysis_claims, analyze_article) inside each section's own context.
        writing_continuation = bool(writing and state.get("outline", {}).get("sections"))
        argumentation_requested = automatic_reading and bool(writing or re.search(
            r"état\s+de\s+l[’']art|compar|plan|rédig|redig|rédac", request_text, re.I))
        analysis_round_allowance = 0
        argument_round_allowance = 0
        compared_batches = set()
        blueprint_windows = [0]
        project_capture_attempted = False
        argumentation_failed = False
        argumentation_attempts = 0
        prepared_articles = set()
        verification_attempted = set()
        recovery_rounds = 0
        reading_budget_reason = None
        # A discovery request waits for this message's model selection; older
        # tasks must not consume the reading budget before the new decisions.
        selection_ready = bool(priority_ids or not re.search(
            r"cherch|trouv|discover|search|recens|identifi|plus\s+pertinent|plus\s+récent|d['’]autres\s+(?:articles|publications|travaux)|"
            r"quel(?:le)?s\s+(?:sont\s+)?(?:les\s+)?(?:travaux|articles|publications)", request_text, re.I))
        selection_reviewed = False
        review_rounds = 0
        # Selection checkpoint: candidates presented in this message must be
        # judged before the exploration rounds run out.
        exposed_before = set(state.get("exposed_article_ids", []))
        turn_decided = False
        selection_checkpoints = 0
        state.pop("selection_focus", None)
        if automatic_reading:
            ledger = reading_tasks(state, toolkit.registry, requested_fields, priority_ids)
            ledger.pop("blocked_reason", None)
            analysis_round_allowance = reading_allowance(state, config.max_deep_analysis)
            if argumentation_requested:
                from .context import analysis_projection
                from .argumentation import claim_entries
                # The existing deep-analysis allowance is shared with stored
                # science continuations, never added a second time per window.
                continuation = sum((len(list(claim_entries(analysis_projection(a.analysis))))+5)//6 for a in toolkit.registry.values() if a.analysis and a.selection_override is not False)
                analysis_round_allowance = max(analysis_round_allowance, min(config.max_deep_analysis, continuation))

        def tool_limit():
            return config.max_tool_calls + (len(writing.get("target_ids", []))*3+2 if writing else 0)

        reading_stopped = False

        def selection_candidates(extra=()):
            """This message's candidates that can be judged on their abstract:
            presented ones first, then stored but never presented ones (batch
            index), in provider order. Consultant-rejected or held works and
            notices without abstract are not submitted; nothing here is rejected."""
            held = set(state.get("held_article_ids", []))
            presented = [aid for aid in toolkit.store.exposed if aid not in exposed_before]
            ids = list(dict.fromkeys([*extra, *presented, *toolkit.turn_article_ids]))
            return [aid for aid in ids if aid in toolkit.registry and toolkit.registry[aid].abstract
                and (aid in extra or aid not in toolkit.store.examined) and aid not in held
                and toolkit.registry[aid].selection_override is not False]

        def argument_tool_reserve():
            """Tools kept for comparison and blueprint when the request needs them,
            so reading cannot spend the whole unchanged tool limit."""
            return 2 if argumentation_requested and not priority_ids and not writing else 0

        # A question about works whose cards already cover methods, protocol,
        # results and limits is answered from those cards (and targeted claim
        # retrieval); deeper reading of their sources needs an explicit request.
        reuse_cards = not re.search(r"\b(?:relis|relire|relise|approfondi\w*|lis\s+(?:le|la|les|tout)|lire\s+(?:le|la|les|tout)|"
            r"texte\s+int[ée]gral|nouvelle\s+lecture|re-?read|read\s+(?:the\s+)?full|deeper)\b", request_text, re.I)
        # Only cards complete before this request are reused this way; works selected
        # in this request are still read and deepened as before.
        reused_cards = frozenset(task["article_id"] for task in state.get("scientific_reading", {}).get("tasks", [])
            if task.get("core_covered")) if reuse_cards else frozenset()

        def core_only_now():
            """Depth beyond core dimensions only while the reading allowance and
            the time/token budgets leave room for the requested comparisons."""
            if not argumentation_requested or priority_ids:
                return False
            if reading_stopped or writing_continuation:
                return True
            reading_left = config.max_tool_rounds+analysis_round_allowance-exploration_rounds
            used = usage["input_tokens"]+usage["output_tokens"]
            return (reading_left <= 2 or deadline-time.monotonic() <= config.timeout_seconds*.5 or
                used >= config.max_tokens*.5)

        def internal_tool(name, arguments):
            nonlocal total_tools
            check()
            if total_tools >= tool_limit()-(0 if writing else config.max_finalization_attempts):
                raise StopResearch("exploration_tool_budget")
            total_tools += 1
            counts[name] = counts.get(name, 0)+1
            activity(name, "running", action=arguments.get("action"), article_id=arguments.get("article_id"))
            if name == "verify_bibliography":
                verification_attempted.update(arguments.get("article_ids", []))
            result = toolkit.execute(name, arguments, deadline)
            toolkit.save()
            activity(name, "completed", article_id=arguments.get("article_id"))
            return result
        for window in [*toolkit.store.windows, *toolkit.reading_windows]:
            if window["status"] == "queued":
                window["status"] = "undelivered"

        def finalize(reason):
            nonlocal phase, stop_reason
            if writing and refresh_writing(state)["status"] != "draft_complete":
                raise StopResearch(reason)
            if phase != "finalization":
                phase, stop_reason = "finalization", reason
                toolkit.finalization_only = True
                toolkit.final_verification_allowed = True
                activity("research", "finalizing", reason=reason)

        section_failures = {}
        claim_reads = {}
        claim_receipts = {}
        blocked_since_save = [0]

        def writing_failure(section_id, message):
            # Counted per section: the refusal (with what to fix) reaches the model,
            # which corrects and retries. A section still refused after three
            # attempts is set aside with its cause; the following sections are
            # drafted in the same run and the document is never reported complete.
            nonlocal writing_attempts
            writing_attempts += 1
            section_failures[section_id] = section_failures.get(section_id, 0)+1
            state["document"]["writing_progress"].setdefault("errors", []).append(
                {"section_id": section_id, "message": message[:500]})
            refresh_writing(state)
            activity("writing_progress", "failed", section_id=section_id, error=message[:500],
                attempt=section_failures[section_id])
            if section_failures[section_id] >= 3:
                state["document"]["writing_progress"].setdefault("blocked", {})[section_id] = message[:500]
                state.pop("writing_focus", None)
                refresh_writing(state)
                activity("writing_progress", "blocked", section_id=section_id, error=message[:500])
                blocked_since_save[0] += 1
                if blocked_since_save[0] >= 2:
                    # Two sections in a row refused without any save: a systemic
                    # cause, not a section-specific one; stop instead of spending
                    # the budget on every remaining section.
                    raise StopResearch("section_save_failed")

        def request_inputs():
            nonlocal exchange
            # A new bounded memory plus ONLY the last protocol exchange. No accumulated transcript.
            latest_question_chars = len(state.get("messages", [])[-1].get("content", "")) if state.get("messages") else 0
            memory_room = max(latest_question_chars+3000, config.finalization_context_chars if phase == "finalization" else 18000)
            exchange = compact_exchange(exchange, toolkit, max(1800, config.max_context_chars-memory_room-512))
            room = config.max_context_chars-len(json.dumps(exchange, ensure_ascii=False))-512
            # The task's memory keeps its own share; the previous protocol exchange
            # (already stored results) is compacted into what remains, so the same
            # request limit holds without stopping the turn.
            budget = min(memory_room, config.max_context_chars-2300)
            supplied = {aid for w in toolkit.store.windows if w["window_id"] in window_ids(exchange) for aid in w["article_ids"]}
            if phase != "finalization":
                fit_reading_focus(state, toolkit, min(budget, max(room, 1800)) if state.get("automatic_reading_focus") else budget)
                exchange = focused_reading_exchange(exchange, state)
            context = model_context(state, toolkit, budget, final=phase == "finalization", supplied_article_ids=supplied)
            room = config.max_context_chars-len(json.dumps(context, ensure_ascii=False))-512
            if len(json.dumps(exchange, ensure_ascii=False)) > room:
                exchange = compact_exchange(exchange, toolkit, max(1800, room))
            inputs = context + exchange
            if len(json.dumps(inputs, ensure_ascii=False)) > config.max_context_chars:
                raise StopResearch("context_budget")
            return inputs

        try:
            while not answer and not toolkit.finished:
                check()
                # Up to two model rounds to review this message's unseen candidates
                # before long readings; reading then resumes automatically.
                holding = review_rounds > 0
                review_rounds = max(0, review_rounds-1)
                if phase == "exploration" and automatic_reading and selection_ready and not holding and not reading_stopped and not state.get("selection_focus") and not state.get("automatic_reading_focus"):
                    task = next_task(state, toolkit.registry, priority_ids, requested_fields, core_only=core_only_now(), skip_depth_ids=reused_cards)
                    if task:
                        aid = task["article_id"]
                        article = toolkit.registry[aid]
                        required_tools = 2 + (aid not in toolkit.checked_ids and aid not in verification_attempted) + (not article.fulltext.get("text") and not task.get("access_attempted"))
                        used_tokens = usage["input_tokens"]+usage["output_tokens"]
                        spent = usage["estimated_cost_usd"]+usage["unreported_reserved_cost_usd"]
                        reason = ("exploration_round_budget" if exploration_rounds >= config.max_tool_rounds+analysis_round_allowance else
                            "deep_analysis_budget" if aid not in prepared_articles and len(prepared_articles) >= config.max_deep_analysis else
                            "exploration_tool_budget" if total_tools+required_tools > config.max_tool_calls-config.max_finalization_attempts-argument_tool_reserve() else
                            "exploration_time_budget" if deadline-time.monotonic() <= min(config.finalization_reserved_seconds, config.timeout_seconds*.4) else
                            "exploration_token_budget" if used_tokens >= max(0,config.max_tokens-config.finalization_reserved_tokens) else
                            "exploration_cost_budget" if spent >= max(0,config.max_cost_usd-config.finalization_reserved_cost_usd) else None)
                        if reason:
                            reading_budget_reason = reason
                            state["scientific_reading"]["blocked_reason"] = reason
                            if argumentation_requested and (reason in {"exploration_round_budget", "deep_analysis_budget"} or
                                    (reason == "exploration_tool_budget" and comparison_batches(state, toolkit.registry))):
                                # Reading allowance spent: compare what is already
                                # documented (partial) instead of skipping comparison.
                                reading_stopped = True
                                task = None
                                activity("scientific_reading", "stopped", reason=reason)
                            else:
                                finalize(reason)
                        else:
                            if aid not in toolkit.checked_ids and aid not in verification_attempted:
                                # One registry check for every finalist still unchecked (same tool, fewer calls).
                                batch = [x for x in dict.fromkeys([aid, *state.get("scientific_reading", {}).get("active_article_ids", [])])
                                    if x in toolkit.registry and x not in toolkit.checked_ids and x not in verification_attempted][:8]
                                internal_tool("verify_bibliography", {"article_ids": batch})
                            prepared_articles.add(aid)
                            prepare_reading(state, toolkit, task, internal_tool)
                            activity("scientific_reading", "completed", article_id=aid,
                                reading_status=task["status"], basis=task["basis"])
                    state.pop("reading_recovery_focus", None)
                    if not task and not priority_ids and recovery_rounds < min(2, config.max_tool_rounds):
                        recovery = recovery_context(state, toolkit.registry)
                        if recovery and (recovery["candidate_ids"] or recovery["unverified_discovered_sources"]):
                            unchecked = [r["article_id"] for r in recovery["blocked_articles"]
                                if r["article_id"] not in toolkit.checked_ids and r["article_id"] not in verification_attempted and not toolkit.registry[r["article_id"]].verified]
                            if unchecked:
                                internal_tool("verify_bibliography", {"article_ids": unchecked[:8]})
                            state["reading_recovery_focus"] = True
                if (phase == "exploration" and argumentation_requested and not state.get("automatic_reading_focus") and not state.get("selection_focus")
                        and not state.get("argumentation_focus") and not state.get("reading_recovery_focus") and not argumentation_failed
                        and active_articles(state, toolkit.registry)
                        and (not blueprint_status(state, toolkit.registry)["exists"] or
                            blueprint_status(state, toolkit.registry)["stale"] or
                            not blueprint_status(state, toolkit.registry)["complete"])
                        and (reading_stopped or next_task(state, toolkit.registry, priority_ids, requested_fields, core_only=core_only_now(), skip_depth_ids=reused_cards) is None)):
                    batches = comparison_batches(state, toolkit.registry)
                    source_id = project_observation_source(state) if not project_capture_attempted else None
                    argument_round_allowance = max(argument_round_allowance, len(batches)*2+bool(source_id))
                    # A batch already compared on its prioritized windows (same sources)
                    # is not compared again; its offsets are kept for a deeper request.
                    pending = next((batch for batch in batches if tuple(batch) not in compared_batches and not (
                        (state.get("comparison_reading", {}).get("|".join(batch), {}).get("complete") or
                            state.get("comparison_reading", {}).get("|".join(batch), {}).get("windows", 0) >= ARGUMENT_WINDOWS) and
                        state["comparison_reading"]["|".join(batch)].get("source_signature") == comparison_source_signature(toolkit.registry, batch))), None)
                    if source_id:
                        state["argumentation_focus"] = {"stage": "project_facts", "article_ids": [], "source_id": source_id}
                        project_capture_attempted = True
                    elif pending:
                        key = "|".join(pending)
                        signature = comparison_source_signature(toolkit.registry, pending)
                        receipt = state.setdefault("comparison_reading", {}).get(key, {})
                        if receipt.get("source_signature") != signature:
                            receipt = {"source_signature": signature, "claim_offsets": {}, "complete": False}
                            state["comparison_reading"][key] = receipt
                        state["argumentation_focus"] = {"stage": "comparison", "article_ids": pending,
                            "claim_offsets": receipt["claim_offsets"]}
                    else:
                        status = blueprint_status(state, toolkit.registry)
                        uncovered = set(active_articles(state, toolkit.registry)) if status["stale"] else set(status["uncovered_article_ids"])
                        blueprint_batches = batches or [active_articles(state, toolkit.registry)[:4]]
                        focus_batch = next((batch for batch in blueprint_batches if set(batch) & uncovered), None)
                        if focus_batch:
                            state["argumentation_focus"] = {"stage": "blueprint", "article_ids": focus_batch,
                                "merge": status["exists"] and not status["stale"]}
                    if state.get("argumentation_focus"):
                        activity("argumentation", "running", stage=state["argumentation_focus"]["stage"])
                if writing and not holding and not state.get("selection_focus") and not state.get("automatic_reading_focus") and not state.get("argumentation_focus") and not state.get("reading_recovery_focus"):
                    progress = refresh_writing(state)
                    if progress["status"] == "draft_complete":
                        answer = writing_answer(state)
                        break
                    if progress["status"] == "plan_changed":
                        raise StopResearch("plan_changed")
                    if (not state.get("outline", {}).get("sections") and
                            active_articles(state, toolkit.registry)):
                        state["writing_planning"] = True
                    elif state.get("outline", {}).get("sections"):
                        section = next_section(state)
                        if not section and progress.get("blocked"):
                            # Every other section is saved; only set-aside ones remain.
                            raise StopResearch("section_blocked")
                        if section:
                            state["writing_focus"] = {"section_id": section["id"],
                                "article_ids": section_article_ids(state, toolkit.registry, section),
                                "retrieval_progress": list(claim_receipts.get(section["id"], {}).values())[-8:]}
                            refresh_writing(state)
                            writing_round_allowance = max(writing_round_allowance,
                                len(writing["target_ids"])*3+4)
                if (phase == "exploration" and not turn_decided and selection_checkpoints < 2 and toolkit.search_count
                        and not state.get("selection_focus") and not state.get("automatic_reading_focus")
                        and not state.get("argumentation_focus") and not state.get("writing_focus") and not state.get("writing_planning")
                        and config.max_tool_rounds+analysis_round_allowance+argument_round_allowance+writing_round_allowance-exploration_rounds <= 1):
                    candidates = selection_candidates()
                    if candidates:
                        # The model still judges relevance; the orchestrator only
                        # makes sure the judgment happens while reading is possible.
                        state["selection_focus"] = {"candidate_ids": candidates[:24], "presented_total": len(candidates)}
                        selection_checkpoints += 1
                        activity("selection_checkpoint", "running", candidates=len(candidates))
                used_tokens = usage["input_tokens"]+usage["output_tokens"]
                spent = usage["estimated_cost_usd"]+usage["unreported_reserved_cost_usd"]
                if phase == "exploration":
                    if exploration_rounds >= config.max_tool_rounds+analysis_round_allowance+argument_round_allowance+writing_round_allowance:
                        finalize("exploration_round_budget")
                    elif total_tools >= max(0, tool_limit()-(0 if writing else config.max_finalization_attempts)):
                        finalize("exploration_tool_budget")
                    elif not writing and deadline-time.monotonic() <= min(config.finalization_reserved_seconds, config.timeout_seconds*.4):
                        finalize("exploration_time_budget")
                    elif not writing and used_tokens >= max(0, config.max_tokens-config.finalization_reserved_tokens):
                        finalize("exploration_token_budget")
                    elif not writing and spent >= max(0, config.max_cost_usd-config.finalization_reserved_cost_usd):
                        finalize("exploration_cost_budget")
                if phase == "finalization" and attempts >= config.max_finalization_attempts:
                    raise StopResearch("finalization_failed")
                if total_tools >= tool_limit():
                    raise StopResearch("tool_budget")
                inputs = request_inputs()
                finalizing = phase == "finalization"
                document_tools = bool(state.get("outline") or state.get("scientific_scope") or re.search(
                    r"état\s+de\s+l[’']art|state\s+of\s+(?:the\s+)?art|\bCIR\b|\bplan\b|rédig|redig|rédac|section|brouillon|figure|graphique|revue",
                    state.get("messages", [{}])[-1].get("content", ""), re.I))
                focused_reading = state.get("automatic_reading_focus") if not finalizing else None
                if focused_reading and (not focused_reading["evidence_ids"] or
                        not set(focused_reading["evidence_ids"]) <= transmitted_evidence_ids(inputs, toolkit.evidence)):
                    raise ActiveReadingContextError("Le contexte transmis ne contient pas toutes les preuves du focus actif.")
                focused_argument = state.get("argumentation_focus") if not finalizing and not focused_reading else None
                focused_section = state.get("writing_focus") if not finalizing and not focused_reading and not focused_argument else None
                revision_save_due = bool(focused_section and writing["mode"] == "section" and
                    exploration_rounds+1 >= config.max_tool_rounds+analysis_round_allowance+argument_round_allowance+writing_round_allowance)
                focused_plan = bool(state.get("writing_planning") and not focused_section and not finalizing)
                focused_selection = (state.get("selection_focus") if not finalizing and not focused_reading and not focused_argument
                    and not focused_section and not focused_plan else None)
                argument_tool = ({"project_facts": "record_project_facts", "comparison": "compare_articles",
                    "blueprint": "build_argument_blueprint"}.get(focused_argument["stage"]) if focused_argument else None)
                tool_defs = [t for t in definitions() if t["name"] == "finalize_response"] if finalizing else (
                    [t for t in definitions() if t["name"] == "analyze_article"] if focused_reading else
                    [t for t in definitions() if t["name"] == argument_tool]
                    if focused_argument else [t for t in definitions() if t["name"] == "manage_outline"]
                    if focused_plan else [t for t in definitions() if t["name"] in {"write_section", "get_fulltext", "analyze_article", "get_analysis_claims"}]
                    if focused_section else [t for t in definitions() if t["name"] == "record_candidate_decisions"]
                    if focused_selection else definitions(document_tools))
                if revision_save_due:
                    # Use the final existing round for the requested action. The
                    # saved draft still passes every ordinary scientific check.
                    tool_defs = [t for t in tool_defs if t["name"] == "write_section"]
                    tool_defs[0]["parameters"]["properties"]["action"] = {"type": "string", "enum": ["save"]}
                if not finalizing and not state.get("reading_recovery_focus") and not focused_reading and not focused_argument and not focused_plan and not focused_section and not focused_selection and config.web_search_enabled and native_calls < config.max_web_calls:
                    tool_defs = [{"type": "web_search", "search_context_size": "low"}, *tool_defs]
                # Task-scoped rules: a focused request carries only its task's guide.
                task_instructions = instructions_for("final" if finalizing else "reading" if focused_reading else
                    "argumentation" if focused_argument else "section" if focused_section else "planning" if focused_plan else
                    "selection" if focused_selection else "recovery" if state.get("reading_recovery_focus") else "conversation")
                input_bound = len(json.dumps(inputs, ensure_ascii=False).encode())+len(task_instructions.encode())+len(json.dumps(tool_defs).encode())
                reserve = (input_bound*config.input_price+config.max_output_tokens*config.output_price)/1_000_000
                if tool_defs[0].get("type") == "web_search":
                    reserve += config.web_call_price+config.web_token_reserve*config.input_price/1_000_000
                if not finalizing and (spent+reserve+(0 if writing else config.finalization_reserved_cost_usd) > config.max_cost_usd or
                        used_tokens+input_bound+config.max_output_tokens+(0 if writing else config.finalization_reserved_tokens) > config.max_tokens):
                    finalize("exploration_cost_budget" if spent+reserve+(0 if writing else config.finalization_reserved_cost_usd) > config.max_cost_usd else "exploration_token_budget")
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
                if not finalizing and state.get("reading_recovery_focus"):
                    recovery_rounds += 1
                rounds += 1
                remaining = max(.001, deadline-time.monotonic())
                hint = ("Final synthesis only. No new search or reading. Call finalize_response now using the verified stored articles, analyses and evidence. "
                    "Use conversation_science and the argument blueprint to explain methods, mechanisms, experiments, verified numbers and limitations in depth when the question asks for them. "
                    "Compare protocols cautiously; a proposed plan belongs in this chat, while only saved manuscript sections belong in the document artifact. "
                    "Set displayed_article_ids to the exact ordered publications in a new table/list, or null if this is a follow-up without a new list. "
                    "Correct any recorded validation refusal. Write naturally in French.") if finalizing else (
                    f"Analyze priority article {focused_reading['article_id']} now. You may read targeted passages with analyze_article(action=read), or save the supported findings with action=save, "
                    "using only the exact passages in automatic_scientific_reading. Fill method, mechanisms, equations, parameters, "
                    "experiments, numeric results, tables, figures and limitations only where supported; leave unknown fields empty. "
                    "The new analysis enriches the existing card. Do not claim exhaustive reading."
                    " If a window yields no supported finding, provide reviewed_evidence_ids, reviewed_fields and analysis_limit with empty claims; never invent a fact to advance."
                    if focused_reading else
                    ("Créer et enregistrer maintenant un plan scientifique argumenté pour l'état de l'art avec manage_outline(create). "
                        "Ce plan reste un brouillon non approuvé jusqu'à une action authentifiée du consultant."
                        if focused_plan else
                    (f"Rédiger maintenant uniquement la section {focused_section['section_id']} et enregistrer le texte avec write_section(save). "
                        "Exploiter section_drafting.packet : analyses approfondies, blueprint, relations, faits du projet distincts, preuves et transitions. "
                        "Citer seulement les résultats soutenus par les preuves exactes ; si un détail de fiche ou une preuve essentielle manque, "
                        "le récupérer avec get_analysis_claims ou lire le passage ciblé. Appliquer consultant_instructions (consignes, pas des faits). "
                        "Ne pas annoncer la réussite avant le résultat saved=true."
                        if focused_section else
                    (f"Record the consultant's exact project observations from user message {focused_argument['source_id']} with record_project_facts. Use exact_quote verbatim, keep project measurements distinct from publication results, and infer no CIR lock."
                        if focused_argument["stage"] == "project_facts" else
                        f"Compare articles {focused_argument['article_ids']} using compare_articles. Save only relations supported by exact evidence from both works; pass an empty relations list if no reliable relation can be established. "
                        "Compare concretely: methods and mechanisms, protocols and datasets, and results. When both works report values on a comparable metric, put them in experimental_results (value, metric, dataset, conditions); otherwise set comparable_protocols=false and say what differs. A demonstrated result is never merged with a comparative inference."
                        if focused_argument["stage"] == "comparison" else
                        f"Build and save the evidence-linked argument blueprint for articles {focused_argument['article_ids']} now. Set merge={str(focused_argument['merge']).lower()}. Cover each of these analyzed articles through its supported role, and connect registered relations across the overlap. Keep project facts separate and do not invent a CIR lock.")
                    if focused_argument else
                    ("Selection now, before the exploration rounds run out: judge the candidates in selection_candidates from their "
                        "abstracts and record_candidate_decisions. Retain only works directly relevant to the scientific question, reject "
                        "off-topic ones with a short reason, keep uncertain ones pending, and set finalist_ids to the best works in order "
                        "(typically 3 to 8). Their texts will then be read and analyzed automatically."
                        if focused_selection else
                    (("Selection review: before long readings, inspect the unreviewed candidates of this message or the finalists "
                        "without known full text listed by record_candidate_decisions if they may matter, then confirm or revise "
                        "the finalists. Do not publish yet. " if holding else "") +
                    f"Exploration rounds left after this request: {config.max_tool_rounds+analysis_round_allowance+argument_round_allowance+writing_round_allowance-exploration_rounds}. "
                    "Publication has a separate reserved budget. Select scientifically useful works across the research axes "
                    "with record_candidate_decisions as soon as abstracts allow it; priority full texts will then be read and analyzed "
                    "automatically before writing.")))))
                activity("responses", "running", round=rounds, phase=phase, attempt=attempts+1 if finalizing else None,
                    input_chars=len(json.dumps(inputs, ensure_ascii=False)),
                    offered_article_ids=list(dict.fromkeys(aid for w in toolkit.store.windows if w["window_id"] in window_ids(inputs) for aid in w["article_ids"])),
                    offered_abstract_ids=list(abstract_deliveries(inputs)), abstract_deliveries=abstract_deliveries(inputs))
                if revision_save_due:
                    hint += " Dernier tour disponible : sauvegarder maintenant la section révisée avec write_section(save). " \
                        "N'inventer aucun détail manquant ; signaler les points encore non vérifiés dans le texte."
                if focused_section and writing["mode"] == "section":
                    hint += " Réviser le texte exact de section_drafting.packet.revision.raw_text lorsqu'il existe, " \
                        "en conservant ses informations étayées. retrieval_progress indique les pages déjà reçues : " \
                        "ne pas recommencer une lecture complète des fiches. Les alertes des autres sections sont hors de cette révision."
                stream, response, upstream_started = None, None, False
                try:
                    upstream_started = True
                    stream = client.with_options(timeout=min(config.request_timeout_seconds, remaining)).responses.create(
                        model=config.model, instructions=task_instructions+"\n"+hint,
                        input=json.loads(json.dumps(inputs)), tools=tool_defs, store=False, stream=True,
                        reasoning={"effort": config.reasoning_effort}, max_output_tokens=config.max_output_tokens,
                        max_tool_calls=1, tool_choice={"type": "function", "name": "finalize_response"} if finalizing else
                            {"type": "function", "name": "analyze_article"} if focused_reading else
                            {"type": "function", "name": argument_tool}
                                if focused_argument else {"type": "function", "name": "manage_outline"}
                                if focused_plan else {"type": "function", "name": "record_candidate_decisions"}
                                if focused_selection else {"type": "function", "name": "write_section"}
                                if revision_save_due else "auto",
                        parallel_tool_calls=not (finalizing or focused_reading or focused_argument or focused_selection),
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
                # Measured only (no price change): provider-cached input and reasoning tokens.
                cached = (raw.get("input_tokens_details") or {}).get("cached_tokens", 0) or 0
                reasoning = (raw.get("output_tokens_details") or {}).get("reasoning_tokens", 0) or 0
                usage["cached_input_tokens"] = usage.get("cached_input_tokens", 0)+cached
                usage["reasoning_tokens"] = usage.get("reasoning_tokens", 0)+reasoning
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
                    error = getattr(response,"error",None)
                    upstream_code = getattr(error,"code",None) or (error.get("code") if isinstance(error,dict) else None)
                    activity("responses", "failed", phase=phase, upstream_error_code=upstream_code or "unknown")
                    raise RuntimeError("ResponsesFailed")
                toolkit.delivered(window_ids(inputs), inputs=inputs)
                activity("responses", "completed", phase=phase, input_tokens=inp, output_tokens=out,
                    cached_input_tokens=cached, reasoning_tokens=reasoning, instructions_chars=len(task_instructions),
                    tools_chars=len(json.dumps(tool_defs)), estimated_cost_usd=usage["estimated_cost_usd"])
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
                    if focused_reading:
                        task = next((item for item in state["scientific_reading"]["tasks"]
                            if item["article_id"] == focused_reading["article_id"]), None)
                        if task:
                            task["attempts"] += 1
                            if task["attempts"] >= 2:
                                task["status"] = "needs_review"
                                state.pop("automatic_reading_focus", None)
                            update_coverage(state)
                            activity("scientific_reading", "rejected", article_id=task["article_id"])
                        continue
                    if focused_argument:
                        argumentation_attempts += 1
                        if argumentation_attempts >= 2:
                            argumentation_failed = True
                            state.pop("argumentation_focus", None)
                            activity("argumentation", "needs_review", stage=focused_argument["stage"])
                        continue
                    if focused_section or focused_plan:
                        if focused_section:
                            writing_failure(focused_section["section_id"], "Aucune sauvegarde de section demandée au modèle.")
                        else:
                            planning_attempts += 1
                            if planning_attempts >= 2:
                                raise StopResearch("plan_not_saved")
                        continue
                    if focused_selection:
                        state.pop("selection_focus", None)
                        activity("selection_checkpoint", "rejected")
                        continue
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
                    elif total_tools >= tool_limit() or (name != "finalize_response" and total_tools >= max(0, tool_limit()-(0 if writing else config.max_finalization_attempts))):
                        result = {"error": "Exploration budget reached; publish the stored results."}
                        finalize("exploration_tool_budget")
                    elif name == "finalize_response" and attempts >= config.max_finalization_attempts:
                        result = {"published": False, "rejection_code": "attempt_budget", "error": "Finalization attempts exhausted."}
                    elif name in {"finalize_response", "compare_articles", "build_argument_blueprint"} and not finalizing and not focused_argument and automatic_reading and selection_ready and not reading_stopped and next_task(state, toolkit.registry, priority_ids, requested_fields, core_only=core_only_now(), skip_depth_ids=reused_cards) is not None:
                        # Covers parallel selection+publication calls in the same
                        # response, before the next scheduling loop can run.
                        total_tools += 1
                        counts[name] = counts.get(name, 0)+1
                        result = {"published": False, "deferred": True, "error": "Lectures prioritaires encore en attente ; poursuivre la lecture avant la synthèse."}
                        activity(name, "deferred", reason="priority_reading_pending")
                    else:
                        total_tools += 1
                        counts[name] = counts.get(name, 0)+1
                        if name == "finalize_response": attempts += 1
                        arguments = {}
                        try:
                            arguments = json.loads(call["arguments"])
                            if focused_reading and (name != "analyze_article" or arguments.get("action") not in {"read", "save"} or
                                    arguments.get("article_ids") != [focused_reading["article_id"]]):
                                raise ValueError("Lire ou enregistrer uniquement l'article scientifique en cours.")
                            if focused_argument and (name != argument_tool or
                                    (focused_argument["stage"] == "project_facts" and
                                        any(fact.get("source_id") != focused_argument["source_id"] for fact in arguments.get("facts", []))) or
                                    (focused_argument["stage"] == "comparison" and
                                        (arguments.get("article_ids") != focused_argument["article_ids"] or arguments.get("relations") is None)) or
                                    (focused_argument["stage"] == "blueprint" and
                                        (arguments.get("action") != "save" or
                                            bool(arguments.get("merge")) != focused_argument["merge"]))):
                                raise ValueError("Enregistrer maintenant la comparaison ou le blueprint ciblé, avec des preuves exactes.")
                            if focused_plan and (name != "manage_outline" or arguments.get("action") != "create"):
                                raise ValueError("Créer et sauvegarder le plan avant la rédaction.")
                            if focused_selection and state.get("selection_focus") and name != "record_candidate_decisions":
                                raise ValueError("Enregistrer maintenant la sélection des candidats présentés.")
                            if focused_section and name == "write_section" and arguments.get("section_id"):
                                # "2" or the title designate the same section as its stable id.
                                try:
                                    arguments["section_id"] = resolve_section(state, arguments["section_id"])["id"]
                                except ValueError:
                                    pass
                            if focused_section and name == "get_analysis_claims":
                                from .toolkit import ClaimsArgs
                                claim_args = ClaimsArgs.model_validate(arguments)
                                claim_key = (focused_section["section_id"], toolkit.store.article(claim_args.article_id).id,
                                    tuple(sorted(set(claim_args.fields or []))), claim_args.offset)
                                if claim_reads.get(claim_key, 0) >= CLAIM_READS_PER_SECTION:
                                    raise ValueError("Cette page de fiche a déjà été relue pour cette section : utiliser next_offset, "
                                        "d'autres champs ou un autre article pour récupérer les preuves manquantes, "
                                        "ou rédiger avec les affirmations reçues.")
                            if focused_section and (name not in {"write_section", "get_fulltext", "analyze_article", "get_analysis_claims"} or
                                    (name == "write_section" and (arguments.get("action") not in {"read", "save"} or
                                    arguments.get("section_id") != focused_section["section_id"]))):
                                raise ValueError("Sauvegarder uniquement la section en cours ou lire sa source ciblée.")
                            activity(name, "running", action=arguments.get("action"), query=arguments.get("query"), family=arguments.get("family"),
                                **({"article_id": arguments.get("article_id"), "fields": arguments.get("fields"),
                                    "offset": arguments.get("offset"), "section_id": focused_section["section_id"] if focused_section else None}
                                    if name == "get_analysis_claims" else {}))
                            result = toolkit.execute(name, arguments, deadline)
                            if focused_section and name == "get_analysis_claims":
                                claim_reads[claim_key] = claim_reads.get(claim_key, 0)+1
                                claim_receipts.setdefault(focused_section["section_id"], {})[claim_key] = {
                                    "article_id": result["article_id"], "fields": claim_args.fields,
                                    "offset": result["offset"], "next_offset": result["next_offset"],
                                    "complete": result["complete"], "claims_received": len(result["claims"])}
                            if focused_reading and arguments.get("action") == "read":
                                fresh = extend_reading_focus(state, toolkit, result.get("reading", []))
                                task = next(t for t in state["scientific_reading"]["tasks"] if t["article_id"] == focused_reading["article_id"])
                                if not fresh:
                                    task["attempts"] += 1
                                    if task["attempts"] >= 2:
                                        task["status"] = "needs_review"
                                        state.pop("automatic_reading_focus", None)
                            toolkit.save()
                            if focused_plan and result.get("outline", {}).get("sections"):
                                state.pop("writing_planning", None)
                                refresh_writing(state)
                                planning_attempts = 0
                                activity("writing_progress", "completed", stage="plan", approval_required=True)
                            if focused_section and name == "write_section" and arguments.get("action") == "save":
                                if not result.get("saved"):
                                    writing_failure(focused_section["section_id"], result.get("error", "Sauvegarde refusée."))
                                else:
                                    confirmed_save(state, focused_section["section_id"], result["draft_id"], result["text"])
                                    state.pop("writing_focus", None)
                                    writing_attempts = 0
                                    blocked_since_save[0] = 0
                                    activity("writing_progress", "completed", section_id=focused_section["section_id"],
                                        draft_id=result["draft_id"], document_version_id=result["document_version_id"])
                            if name == "get_fulltext" and automatic_reading:
                                reading_tasks(state, toolkit.registry, requested_fields, priority_ids)
                                analysis_round_allowance = max(analysis_round_allowance, reading_allowance(state, config.max_deep_analysis))
                            if name == "record_candidate_decisions" and result.get("saved"):
                                turn_decided = True
                                if focused_selection:
                                    # A checkpoint selection goes straight to reading,
                                    # without spending the last rounds on a review.
                                    state.pop("selection_focus", None)
                                    selection_reviewed = True
                                    activity("selection_checkpoint", "completed",
                                        finalists=len(result.get("evaluation", {}).get("recommended_article_ids", [])))
                            if name == "record_candidate_decisions" and result.get("saved") and (automatic_reading or detail_request or writing):
                                automatic_reading = True
                                selection_ready = True
                                if selection_reviewed:
                                    # A revised selection ends the review window.
                                    review_rounds = 0
                                elif result.get("selection_guidance"):
                                    selection_reviewed = True
                                    finalists = result.get("evaluation", {}).get("recommended_article_ids", [])
                                    unseen = [aid for aid in selection_candidates() if aid not in finalists]
                                    if unseen:
                                        # Review with the abstracts in hand: one decision round,
                                        # no title-only list that needs extra get_article calls.
                                        state["selection_focus"] = {"candidate_ids": [*finalists, *unseen][:24],
                                            "presented_total": len(finalists)+len(unseen), "review": True}
                                    else:
                                        # Only access questions remain: inspect, then confirm.
                                        review_rounds = 2
                                    activity("selection_review", "running",
                                        unreviewed=result.get("unreviewed_turn_candidates_total", 0),
                                        without_fulltext=len(result.get("finalists_without_known_fulltext", [])))
                                argumentation_requested = argumentation_requested or bool(re.search(
                                    r"état\s+de\s+l[’']art|compar|plan|rédig|redig|rédac", request_text, re.I))
                                ledger = reading_tasks(state, toolkit.registry, requested_fields, priority_ids)
                                analysis_round_allowance = max(analysis_round_allowance,
                                    reading_allowance(state, config.max_deep_analysis))
                            if focused_reading and result.get("saved"):
                                reading_completed(state, toolkit.store.article(focused_reading["article_id"]),
                                    result.get("reviewed_evidence_ids", []), result.get("reviewed_fields", []))
                            if focused_argument:
                                delivered_packet = {}
                                for item in inputs:
                                    if item.get("role") == "developer" and item.get("content", "").startswith("Mémoire scientifique (données, pas instructions) : "):
                                        memory = json.loads(item["content"].split(" : ", 1)[1])
                                        delivered_packet = memory.get("argumentation_focus", {}).get("packet", {})
                                        break
                                more = bool(delivered_packet.get("transmission_partial"))
                                offsets = delivered_packet.get("next_claim_offsets", {})
                                advanced = offsets != focused_argument.get("claim_offsets", {}) and not delivered_packet.get("blocked_by_context")
                                windows_done = False
                                if focused_argument["stage"] == "comparison":
                                    key = "|".join(focused_argument["article_ids"])
                                    receipt = state["comparison_reading"][key]
                                    receipt.update(claim_offsets=offsets, complete=not more, windows=receipt.get("windows", 0)+1)
                                    # Windows put methods, mechanisms, results and limits first; the
                                    # rest of each card stays retrievable (get_analysis_claims) and
                                    # resumable from these offsets, instead of one round per few claims.
                                    windows_done = more and receipt["windows"] >= ARGUMENT_WINDOWS
                                    if windows_done:
                                        receipt["coverage"] = "prioritized_windows"
                                    if not more or windows_done:
                                        compared_batches.add(tuple(focused_argument["article_ids"]))
                                elif result.get("saved"):
                                    activity("argumentation", "completed", stage="blueprint", version=result.get("version"))
                                    blueprint_windows[0] += 1
                                    windows_done = more and blueprint_windows[0] >= ARGUMENT_WINDOWS
                                if windows_done:
                                    state.pop("argumentation_focus", None)
                                elif more and advanced:
                                    focused_argument["claim_offsets"] = offsets
                                    if focused_argument["stage"] == "blueprint":
                                        focused_argument["merge"] = True
                                else:
                                    state.pop("argumentation_focus", None)
                                    if more:
                                        argumentation_failed = True
                                        state["scientific_reading"]["blocked_reason"] = "argument_context_budget"
                                argumentation_attempts = 0
                            if name == "finalize_response" and not result.get("published"):
                                activity(name, "rejected", rejection_code=result.get("rejection_code", "validation_arguments"), reason=result.get("error", "Validation refused."))
                                finalize("publication_refused")
                            else:
                                activity(name, "completed", found=result.get("collected", len(result["articles"]) if isinstance(result.get("articles"), list) else None),
                                    article_id=arguments.get("article_id"), refused=result.get("refused"), empty_providers=result.get("empty_providers"),
                                    **({"offset": result["offset"], "next_offset": result["next_offset"], "complete": result["complete"],
                                        "claims_returned": len(result["claims"])} if name == "get_analysis_claims" else {}))
                                if focused_reading and result.get("saved"):
                                    activity("scientific_reading", "completed", article_id=focused_reading["article_id"],
                                        reading_status=next((t["status"] for t in state["scientific_reading"]["tasks"] if t["article_id"] == focused_reading["article_id"]), "partial"))
                        except StopResearch:
                            raise
                        except Exception as exc:
                            from pydantic import ValidationError
                            message = "Invalid arguments; follow the tool schema." if isinstance(exc, ValidationError) else str(exc)[:500] if isinstance(exc, ValueError) else "Tool unavailable; use validated stored data."
                            result = {"error": message, "error_type": type(exc).__name__}
                            activity(name, "failed", error_type=type(exc).__name__, error=message)
                            if focused_reading:
                                task = next((item for item in state["scientific_reading"]["tasks"]
                                    if item["article_id"] == focused_reading["article_id"]), None)
                                if task:
                                    task["attempts"] += 1
                                    if task["attempts"] >= 2:
                                        task["status"] = "needs_review"
                                        state.pop("automatic_reading_focus", None)
                                    update_coverage(state)
                                    activity("scientific_reading", "rejected", article_id=task["article_id"])
                            if focused_argument:
                                argumentation_attempts += 1
                                if argumentation_attempts >= 2:
                                    argumentation_failed = True
                                    state.pop("argumentation_focus", None)
                                    activity("argumentation", "needs_review", stage=focused_argument["stage"])
                            if name == "finalize_response":
                                state.setdefault("finalization_attempts", []).append({"published": False, "rejection_code": "validation_arguments", "error": message})
                                finalize("publication_refused")
                            if focused_section and name == "write_section" and arguments.get("action") != "read":
                                writing_failure(focused_section["section_id"], message)
                            if focused_plan:
                                planning_attempts += 1
                                if planning_attempts >= 2:
                                    raise StopResearch("plan_not_saved")
                            if focused_selection:
                                # The refusal reaches the model; one more checkpoint may follow.
                                state.pop("selection_focus", None)
                    exchange.append({"type": "function_call_output", "call_id": call["call_id"], "output": json.dumps(
                        {**result, "text": result["text"][:400]+("…" if len(result["text"])>400 else "")}
                        if name == "write_section" and result.get("saved") else result, ensure_ascii=False)})
                if toolkit.finished:
                    final_result = toolkit.finished
                    answer = final_result["answer"]
        except ActiveReadingContextError:
            stop_reason, partial = "evidence_transmission_blocked", True
            activity("scientific_reading", "blocked", reason=stop_reason,
                article_id=state.get("automatic_reading_focus", {}).get("article_id"))
        except StopResearch as exc:
            stop_reason, partial = exc.reason, True
            activity("research", "stopped", reason=stop_reason)
        except Exception as exc:
            partial, stop_reason = True, "upstream_error"
            status = getattr(exc, "status_code", None)
            if status in {400, 401, 403, 404}: stop_reason = "configuration_error"
            elif status == 429: stop_reason = "quota_or_rate_limit"
            activity("responses", "failed", error_type=type(exc).__name__, http_status=status)
        state.pop("selection_focus", None)
        if reading_budget_reason:
            partial = True
        toolkit.save()
        if writing:
            answer = writing_answer(state, stop_reason)
            # Transversal attribution audit of every saved section (deterministic, no
            # model call): earlier drafts are reported, never rewritten automatically.
            from .attribution import audit_document
            audit = audit_document(state, toolkit)
            state["document"]["attribution_audit"] = audit
            if audit:
                titles = list(dict.fromkeys(issue["section_title"] for issue in audit))
                answer += (f"\n\nContrôle d’attribution : {len(audit)} phrase(s) à vérifier dans "
                    + ", ".join(f"« {title} »" for title in titles[:4]) + " — "
                    + "; ".join(f"{', '.join(issue['names'])}" for issue in audit[:4])
                    + ". Ces noms n’identifient aucune publication de la section et ne figurent dans aucune preuve citée "
                    "dans la phrase. Demandez « révise la section … » pour les corriger.")
            partial = refresh_writing(state)["status"] != "draft_complete"
            final_result = {"answer": answer, "coverage_sufficient": False, "fallback": partial}
        if not answer:
            final_result = fallback(toolkit, stop_reason or "finalization_failed")
            answer, partial = final_result["answer"], True
            if stop_reason == "configuration_error":
                answer += f"\n\nConfiguration OpenAI : vérifier l’accès à {config.model}, OPENAI_API_KEY et les outils compatibles. Aucun modèle de remplacement n’a été utilisé."
            toolkit.save()
            state["last_response"] = {k: v for k, v in final_result.items() if k != "answer"}
        if stop_reason == "evidence_transmission_blocked":
            answer += "\n\nLa transmission des preuves de l’article prioritaire est bloquée par la limite de contexte ou un paquet incomplet. La sauvegarde de l’analyse active n’a pas été demandée ; la tâche est conservée pour reprise."
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
            "coverage_sufficient": coverage, "publication_accepted": bool(toolkit.finished) or bool(writing and not partial),
            "writing_progress": state.get("document", {}).get("writing_progress", {}),
            "scientific_reading": state.get("scientific_reading", {}),
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
