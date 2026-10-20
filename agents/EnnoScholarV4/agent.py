"""One adaptive Responses conversation; every scientific judgment stays here."""
from __future__ import annotations

import copy
import json
import math
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout, Future
from datetime import datetime, timezone
from pathlib import Path

from openai import OpenAI

from .config import ScholarV4Config
from .models import public_link
from .prompts import SYSTEM, instructions_for
from .sources import ScientificSources
from .toolkit import ScholarTools, definitions
from .task_results import TaskResults, fingerprint, source_signature, collect_finished
from .finance import MeteredClient
from .pricing import resolve as resolve_price, reserve_bound
from .routing import model_for

# Comparison and blueprint windows per article batch (claims prioritized: methods,
# mechanisms, results, limits). Not a budget: rounds, tools and costs are unchanged.
ARGUMENT_WINDOWS = 3
# Repeated identical successful get_analysis_claims reads per section. Distinct
# articles, fields and continuation pages remain available within the run budgets.
CLAIM_READS_PER_SECTION = 4

# Request size in tokens before the call, from its UTF-8 size. Measured on the real
# runs: 3.85 to 4.1 characters per token (prompt, schemas and French/English data).
# Three bytes per token over-estimates by about 30 %, so the cost and token reserves
# stay protective without counting every byte as a token (four times too much).
BYTES_PER_TOKEN = 3.0


# Independent analyses of selected works run concurrently (writes stay sequential).
# A scheduling limit, not a budget: rounds, tools, tokens, cost and time are unchanged.
PARALLEL_ANALYSES = 3
# Process-wide limit on concurrent background model calls (several runs share the
# account's rate limits).
LLM_SLOTS = threading.BoundedSemaphore(6)
# Expected duration of one part of a plan generated in parallel (one lock or axis,
# ~1 200 output tokens) and of a whole plan in one call (4 000-5 000 tokens measured
# at 25-30 tokens/s in runs e7bae458 and d8606428). Scheduling estimates, not budgets.
PLAN_PART_SECONDS = 90
PLAN_SECONDS = 180
PLAN_MARGIN = 20
# Goals that discover new works: their readings and comparisons wait for this
# message's selection instead of starting on finalists left by earlier turns.
DISCOVERY_GOALS = {"literature_search", "plan"}
# Rounds a section may spend without new card claims or saved analyses (passages
# read, refused or repeated calls) before its save is required; the save itself still
# passes every scientific check.
SECTION_READ_ROUNDS = 3
# Sections whose sources all have a card are written concurrently (saves stay sequential
# and fully validated). Scheduling limits, not budgets.
PARALLEL_SECTIONS = 3
# Expected duration of one section save (~1 500 output tokens at 25-30 tokens/s) before
# one is measured in the run: no save call is started if it cannot end before the deadline.
SECTION_SECONDS = 75
# Background analyses kept running while sections are written (the remaining slots of the
# process-wide LLM_SLOTS serve the sections). A scheduling share, not a budget.
READING_SLOTS_WHILE_WRITING = 2
# Comparisons built while drafting (per section, from available proofs), per request.
MAX_DRAFT_COMPARISONS = 3
# One section's share of the run's token cap in its sequential rounds, and its rounds.
SECTION_TOKEN_SHARE = 0.15
SECTION_MAX_ROUNDS = 8
# Section memory tried once when the full one is refused by the token admission.
REDUCED_SECTION_CONTEXT_CHARS = 18000
# A closing synthesis, in any of the manuscript's languages.
SYNTHESIS_TITLE = re.compile(r"\b(?:synth[eè]se|conclusions?|perspectives|bilan|discussion\s+g[eé]n[eé]rale|summary|"
    r"outlook|concluding|general\s+discussion)\b", re.I)

# Cache-friendly request layout: stable instructions and tools first, the round's own
# instruction (rounds left, focused article, stage) as the last input item.
TURN_INSTRUCTION_PREFIX = "Consigne de ce tour : "
TURN_INSTRUCTION_BYTES = 4000
# Groups requests of the same task so the provider can route them to its prompt cache.
# ENNOSCHOLAR_V4_PROMPT_CACHE_KEY=0 omits the parameter (stable prefixes are kept).
CACHE_KEY_PREFIX = "" if os.getenv("ENNOSCHOLAR_V4_PROMPT_CACHE_KEY", "1") == "0" else "ennoscholar-v4-"


def cache_key(task):
    return {"prompt_cache_key": CACHE_KEY_PREFIX+task} if CACHE_KEY_PREFIX else {}


def turn_instruction(hint):
    return {"role": "developer", "content": TURN_INSTRUCTION_PREFIX + hint}


def request_tokens(size_bytes):
    return math.ceil(size_bytes / BYTES_PER_TOKEN)


from .stops import StopResearch  # noqa: E402  (re-exported: tests and service import it from here)


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
        from .context import model_context, compact_exchange, ActiveReadingContextError, transmitted_evidence_ids, SECTION_CONTEXT_CHARS, PLAN_PART_CONTEXT_CHARS
        from .publication import fallback, url_key
        from .scientific_reading import (reading_tasks, next_task, prepare_reading,
            completed as reading_completed, update_coverage, reading_allowance, recovery_context,
            extend_reading_focus, fit_reading_focus, focused_reading_exchange)
        from .argumentation import active_articles, comparison_batches, blueprint_status, project_observation_source, comparison_source_signature
        from .writing_progress import (begin_writing, refresh_writing, next_section, confirmed_save, writing_answer,
            open_writing, PLAN_BEFORE_WRITING)
        from .turn import call_estimate, plan_answer, provisional_plan, validated, strip_numbering
        from .writing_context import section_article_ids
        from .tools.outline import resolve_section
        self.config.validate()
        config = self.config
        state["current_turn_article_ids"] = []
        # Every provider request passes through the metered client: reserved in the
        # persistent ledger first when the spending controller is on (self.meter), and
        # always recorded (model requested, prefix fingerprints, provider identifiers).
        client = MeteredClient(self.client or OpenAI(api_key=config.api_key, timeout=config.request_timeout_seconds,
            max_retries=0), config, meter=getattr(self, "meter", None))
        price = resolve_price(config)
        started = time.monotonic()
        deadline = started + config.timeout_seconds
        usage = {"model": config.model, "input_tokens": 0, "output_tokens": 0,
            "estimated_cost_usd": 0., "unreported_reserved_cost_usd": 0., "price": price.describe()}
        counts, facts, exchange = {}, [], []
        rounds = exploration_rounds = finalization_rounds = attempts = total_tools = native_calls = 0
        phase, stop_reason, answer = "exploration", None, ""
        budget_refusal = None
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
        from .diagnostic_link import scope_from_message, LINKED_GUIDANCE
        scope_from_message(state, request_text)
        linked = state.get("mode") == "diagnostic_linked"
        available_definitions = lambda document=True: definitions(document, linked=linked)
        from .instructions import capture_instructions
        capture_instructions(state, (state.get("messages") or [{}])[-1], toolkit.registry)
        # Task foci are recomputed from durable receipts (comparison offsets,
        # saved drafts, writing progress); a focus left by an interrupted run must
        # not be resent with the next request's context.
        for stale in ("argumentation_focus", "writing_focus", "writing_planning", "turn_goal"):
            state.pop(stale, None)
        # A draft detected from the wording is only a proposal until the model has
        # read the message; its previous progress is restored if it is not wanted.
        prior_progress = copy.deepcopy(state.get("document", {}).get("writing_progress"))
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
        # Conversational references resolved locally from the stored exchanges (no model
        # call): « les 2 articles », « le deuxième », « leurs limites » are the publications the
        # last answer discussed, not an older displayed list nor the last downloaded work.
        from .references import resolve as resolve_references
        references = resolve_references(state, toolkit.registry, request_text)
        state["conversation_references"] = references
        if references["article_ids"] and not references["ambiguous"]:
            focus_id = None
            named_articles = [aid for aid in named_articles if aid in references["article_ids"]]
        priority_ids = tuple(dict.fromkeys([*references["article_ids"], *named_articles, *([focus_id] if focus_id else [])]))
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
        # Reading or comparing triggered only by the stored work type or a reading
        # left by an earlier turn waits for the model's first round: it reads the
        # message first (set_turn_goal), so « ce plan ne me convainc pas » is not
        # preceded by long readings it did not ask for.
        explicit_reading = bool(priority_ids or detail_request or writing or re.search(
            r"état\s+de\s+l[’']art|state\s+of\s+(?:the\s+)?art|rédig|redig|rédac", request_text, re.I))
        goal_pending = automatic_reading and not explicit_reading
        # Understanding before execution: when the wording alone would start a
        # reading, a comparison or a draft, the model first reads the message and
        # declares its goal; the stages then follow that goal, not the words.
        understanding_needed = bool(writing or automatic_reading or argumentation_requested)
        deferred_focus = state.pop("automatic_reading_focus", None) if goal_pending or understanding_needed else None
        plan_goal = plan_done = planning_forced = plan_checkpoint = False
        # Works this request must read when the goal names them (sources of the sections to draft).
        reading_scope = [None]
        deepen_attempted = set()
        draft_comparisons = [0]
        plan_parts_tried = gap_used = last_round_searched = False
        provisional_reason = None
        plan_note = ""
        durations = []
        def task_checkpoint(task):
            toolkit.save()
            activity("task_result", "completed", task_id=task["id"], task_kind=task["kind"],
                target=task["target"], task_status=task["status"])
        tasks = TaskResults(state, config, usage, durations, task_checkpoint, call_records=client.records)

        def stable_target(task):
            """What a task's budget follows across retries, runs and conversations."""
            kind, target = task["kind"], task["target"]
            if kind == "reading" and target in toolkit.registry:
                from .scientific_reading import reading_identity
                return ["reading", reading_identity(toolkit.registry[target])]
            if kind == "section":
                section = resolve_section(state, target) if target else None
                return ["section", re.sub(r"\W+", " ", (section or {}).get("title", target)).strip().casefold()]
            if kind == "plan_part":
                return ["plan_part", target]
            return [kind, re.sub(r"\s+", " ", request_text).strip().casefold()]

        def label(kwargs, task, attempt, kind=None):
            """Ledger identity of one request, and the model of its stage (routing.py:
            config.model for every stage unless an administrator configured one)."""
            kind = kind or task["kind"]
            kwargs["model"] = model_for(config, kind)
            kwargs[MeteredClient.PRIVATE] = {"kind": task["kind"], "target": task["target"], "attempt_id": attempt["id"],
                "stable_target": stable_target(task), "label": f"{task['kind']}:{task['target']}"}
            return kwargs
        plan_tasks = []
        plan_incomplete = False
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

        def earlier_candidates(with_missing_abstract=False):
            """Works collected by earlier messages for the locks (or the conversation) this
            message targets and never judged, or left pending only for lack of an abstract
            now found: still evaluable without a new search. Search order kept."""
            goal = state.get("turn_goal") or {}
            targets = set(goal.get("lock_ids") or (state.get("diagnostic_link") or {}).get("active_lock_ids", [])) if linked else None
            held = set(state.get("held_article_ids", []))
            ids = []
            for search in state.get("recent_searches", [])[:toolkit.searches_at_start]:
                if linked and not set(search.get("lock_ids") or []) & targets:
                    continue
                for aid in search.get("article_ids", []):
                    article = toolkit.registry.get(aid)
                    if (not article or aid in toolkit.turn_article_ids or aid in held or article.selection_override is not None
                            or article.analysis):
                        continue
                    assessment = article.assessment or {}
                    if assessment.get("decision") and not assessment.get("reopened_for_abstract"):
                        continue
                    if article.abstract or with_missing_abstract:
                        ids.append(aid)
            return list(dict.fromkeys(ids))

        def selection_pool():
            """The window judged at a checkpoint: this message's candidates first, then the
            earlier undecided ones (run d71d17b0: 60 relevant notices never re-proposed).
            Notices without abstract get one looked up from their DOI when time allows."""
            missing = [aid for aid in [*toolkit.turn_article_ids, *earlier_candidates(True)]
                if aid in toolkit.registry and not toolkit.registry[aid].abstract]
            if missing and time_left_for(20):
                found = toolkit.enrich_abstracts(missing, min(deadline, time.monotonic()+12))
                if found:
                    activity("abstract_lookup", "completed", found=len(found), requested=min(8, len(missing)))
            candidates = selection_candidates()
            earlier = [aid for aid in earlier_candidates() if aid not in candidates]
            pool = [*balanced(candidates)[:24], *earlier][:24]
            return pool, len(candidates)+len(earlier), [aid for aid in pool if aid in earlier]

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
            if (plan_goal or (state.get("turn_goal") or {}).get("reading_depth") == "core") and not priority_ids:
                # A plan (or a declared core reading) needs every key work's core
                # dimensions, not one work's depth.
                return True
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

        def time_left_for(seconds):
            """Whether a call of this expected duration still ends before the time the
            final answer needs: the unchanged publication reserve, or the longest
            generation measured in this run when it is longer (critical path)."""
            final = max(min(config.finalization_reserved_seconds, config.timeout_seconds*.4),
                call_estimate(durations, config.timeout_seconds*.2) if durations else 0)
            return deadline-time.monotonic()-seconds > final

        def finalize(reason):
            nonlocal phase, stop_reason, planning_forced, plan_checkpoint, provisional_reason
            if (plan_goal and not plan_done and not planning_forced and not plan_checkpoint and phase == "exploration"
                    and not turn_decided and toolkit.search_count and total_tools+2 <= tool_limit() and time_left_for(30)):
                # New references were searched but none judged yet: the plan is built
                # on a real selection (the candidates of this message, every lock
                # represented), not on the earlier finalists alone.
                candidates, total, earlier = selection_pool()
                if candidates:
                    plan_checkpoint = True
                    for key in ("automatic_reading_focus", "argumentation_focus", "reading_recovery_focus"):
                        state.pop(key, None)
                    state["selection_focus"] = {"candidate_ids": candidates, "presented_total": total, "before_plan": True,
                        "earlier_candidate_ids": earlier}
                    activity("selection_checkpoint", "running", candidates=len(candidates), stage="before_plan", reason=reason)
                    return
            if plan_goal and not plan_done and phase == "exploration" and deadline-time.monotonic() < plan_need():
                # The plan cannot be generated before the deadline: answer with the
                # provisional plan rather than start a call that would be cut.
                provisional_reason = reason
                activity("writing_progress", "provisional", stage="plan", reason=reason)
                return
            if (plan_goal and not plan_done and phase == "exploration" and planning_attempts < 2
                    and total_tools < tool_limit()):
                # A plan request ends with the saved plan (rendered without another
                # model call), not with a synthesis of what was found so far.
                planning_forced = True
                state["writing_planning"] = True
                for key in ("automatic_reading_focus", "argumentation_focus", "selection_focus", "reading_recovery_focus"):
                    state.pop(key, None)
                activity("writing_progress", "running", stage="plan", reason=reason)
                return
            if writing and refresh_writing(state)["status"] != "draft_complete":
                raise StopResearch(reason)
            if phase != "finalization":
                phase, stop_reason = "finalization", reason
                toolkit.finalization_only = True
                toolkit.final_verification_allowed = True
                activity("research", "finalizing", reason=reason)

        speculative, pool, stop_calls = {}, [None], threading.Event()
        understanding_calls = [0]
        plan_inflight = {}
        # Wall time per stage (understanding, conversation/search, selection, reading,
        # argumentation, plan, writing, final), tools included, for latency analysis.
        stage_times, stage_now = {}, ["preparation", time.monotonic()]

        def enter_stage(name):
            now = time.monotonic()
            stage_times[stage_now[0]] = round(stage_times.get(stage_now[0], 0)+now-stage_now[1], 1)
            stage_now[:] = [name, now]
        parallel_stats = {"launched": 0, "max_concurrent": 0}

        def reading_hint(focus):
            return (f"Analyze priority article {focus['article_id']} now. You may read targeted passages with analyze_article(action=read), or save the supported findings with action=save, "
                "using only the exact passages in automatic_scientific_reading, cited by their ref (E1, E2…). Fill method, mechanisms, equations, parameters, "
                "experiments, numeric results, tables, figures and limitations only where supported; leave unknown fields empty. "
                "The new analysis enriches the existing card. Do not claim exhaustive reading."
                " If a window yields no supported finding, provide reviewed_evidence_ids, reviewed_fields and analysis_limit with empty claims; never invent a fact to advance.")

        def reading_kwargs(inputs, focus):
            """The request of one focused analysis, identical in the background and main thread."""
            return dict(model=config.model, instructions=instructions_for("reading"), **cache_key("reading"),
                input=[*json.loads(json.dumps(inputs)), turn_instruction(reading_hint(focus))],
                tools=[t for t in available_definitions() if t["name"] == "analyze_article"], store=False, stream=True,
                reasoning={"effort": config.reasoning_effort}, max_output_tokens=config.max_output_tokens, max_tool_calls=1,
                tool_choice={"type": "function", "name": "analyze_article"}, parallel_tool_calls=False,
                include=["reasoning.encrypted_content", "web_search_call.action.sources"])

        def in_flight():
            """Cost and token reservations of analyses running in the background."""
            return (sum(e["reserve"] for e in speculative.values()),
                sum(e["tokens"]+config.max_output_tokens for e in speculative.values()))

        def background(kwargs):
            # Network only: no state is read or written here.
            with LLM_SLOTS:
                if stop_calls.is_set():
                    raise StopResearch("cancelled")
                begun = time.monotonic()
                stream = client.with_options(timeout=min(config.request_timeout_seconds, max(.001, deadline-time.monotonic()))).responses.create(**kwargs)
                response = None
                try:
                    for event in stream:
                        if event.type in {"response.completed", "response.incomplete", "response.failed"}:
                            response = event.response
                        elif stop_calls.is_set():
                            raise StopResearch("cancelled")
                        elif event.type == "error":
                            raise RuntimeError("ResponsesStreamError")
                finally:
                    stream.close()
                if response is None:
                    raise RuntimeError("ResponsesMissingCompletion")
                return response, time.monotonic()-begun

        def launch(focus, inputs):
            kwargs = reading_kwargs(inputs, focus)
            tokens = request_tokens(len(json.dumps(kwargs["input"], ensure_ascii=False).encode())+len(kwargs["instructions"].encode())
                +len(json.dumps(kwargs["tools"]).encode()))
            if pool[0] is None:
                pool[0] = ThreadPoolExecutor(max_workers=PARALLEL_ANALYSES, thread_name_prefix="scholar-v4-analysis")
            task = tasks.task("reading", focus["article_id"], fingerprint([
                focus["article_id"], focus.get("source_version"), focus["evidence_ids"]]))
            proposal = tasks.proposal(task)
            attempt = proposal or tasks.begin(task, evidence_ids=transmitted_evidence_ids(inputs, toolkit.evidence),
                input_signature=fingerprint(inputs), reserve=reserve_of(tokens))
            label(kwargs, task, attempt)
            if proposal:
                future = Future()
                future.set_result((tasks.response(proposal), 0))
            else:
                future = pool[0].submit(background, kwargs)
            entry = {"focus": copy.deepcopy(focus), "inputs": inputs, "tokens": tokens,
                "task": task, "attempt": attempt, "begun": time.monotonic(),
                "reserve": reserve_of(tokens),
                "future": future}
            parallel_stats["launched"] += 1
            speculative[focus["article_id"]] = entry
            parallel_stats["max_concurrent"] = max(parallel_stats["max_concurrent"], len(speculative))
            activity("responses", "parallel_started", article_id=focus["article_id"], in_flight=len(speculative))
            return entry

        def wait_entry(entry):
            while True:
                check()
                try:
                    return entry["future"].result(timeout=.2)
                except FutureTimeout:
                    continue
                except StopResearch:
                    raise
                except Exception as exc:
                    # Rate limit or transport error: this analysis is retried once in the main thread.
                    activity("responses", "parallel_failed", article_id=entry["focus"]["article_id"], error_type=type(exc).__name__)
                    tasks.receive(entry["task"], entry["attempt"], error=exc,
                        seconds=time.monotonic()-entry["begun"], reserve=entry["reserve"])
                    task_checkpoint(entry["task"])
                    return None, None

        def launch_more():
            """Prepare the next independent readings and start their analyses while the
            current one runs. Tools, rounds, deep-analysis count, cost, tokens and time
            are checked as for a sequential reading, with in-flight reservations counted."""
            nonlocal exchange
            current = state.get("automatic_reading_focus")
            allowance = config.max_tool_rounds+analysis_round_allowance+argument_round_allowance+writing_round_allowance
            # speculative includes the analysis running now (its round is already counted).
            # While sections are written, part of the work stays reserved for the analyses they
            # still need (audit 91590987: no analysis started while any section was ready).
            slots = READING_SLOTS_WHILE_WRITING if writing and writing_continuation else PARALLEL_ANALYSES
            while (len(speculative) < slots and automatic_reading and selection_ready and not reading_stopped
                    and not goal_pending and exploration_rounds+len(speculative) <= allowance
                    and time_left_for(call_estimate(durations, config.timeout_seconds*.2))):
                # Do not delay a ready result behind more synchronous downloads.
                if any(entry["future"].done() for entry in speculative.values()):
                    break
                task = next_task(state, toolkit.registry, priority_ids, requested_fields, core_only=core_only_now(), skip_depth_ids=reused_cards)
                if not task:
                    break
                aid = task["article_id"]
                article = toolkit.registry[aid]
                required = 2 + (aid not in toolkit.checked_ids and aid not in verification_attempted) + (not article.fulltext.get("text") and not task.get("access_attempted"))
                if ((aid not in prepared_articles and len(prepared_articles) >= config.max_deep_analysis) or
                        # Each registered analysis (the running one included) still has its save to come.
                        total_tools+required+len(speculative) > (tool_limit() if writing else config.max_tool_calls)
                            -config.max_finalization_attempts-argument_tool_reserve()):
                    break
                if to_deepen(article):
                    deepen_attempted.add(aid)
                saved_exchange = exchange
                try:
                    enter_stage("reading")
                    if aid not in toolkit.checked_ids and aid not in verification_attempted:
                        batch = [x for x in dict.fromkeys([aid, *state.get("scientific_reading", {}).get("active_article_ids", [])])
                            if x in toolkit.registry and x not in toolkit.checked_ids and x not in verification_attempted][:8]
                        internal_tool("verify_bibliography", {"article_ids": batch})
                    prepared_articles.add(aid)
                    focus = prepare_reading(state, toolkit, task, internal_tool)
                    if not focus:
                        continue
                    exchange = []
                    try:
                        inputs = request_inputs()
                    except ActiveReadingContextError:
                        # Too large to send intact: kept resumable for a sequential round.
                        break
                    focus = state["automatic_reading_focus"]
                    if not set(focus["evidence_ids"]) <= transmitted_evidence_ids(inputs, toolkit.evidence):
                        break
                    cost, tokens = in_flight()
                    estimate = request_tokens(len(json.dumps(inputs, ensure_ascii=False).encode())+TURN_INSTRUCTION_BYTES+8000)
                    if (usage["estimated_cost_usd"]+usage["unreported_reserved_cost_usd"]+cost+reserve_of(estimate)
                            +config.finalization_reserved_cost_usd > config.max_cost_usd or
                            usage["input_tokens"]+usage["output_tokens"]+tokens+estimate+config.max_output_tokens
                            +config.finalization_reserved_tokens > config.max_tokens):
                        break
                    launch(focus, inputs)
                    activity("scientific_reading", "completed", article_id=aid, reading_status=task["status"], basis=task["basis"], parallel=True)
                finally:
                    exchange = saved_exchange
                    state["automatic_reading_focus"] = current

        def reserve_of(tokens):
            # No cache hit assumed: every input token at the highest input price (pricing.py).
            return reserve_bound(tokens, config.max_output_tokens, price)

        def task_background(kwargs):
            begun = time.monotonic()
            try:
                response, seconds = background(kwargs)
                return response, seconds, None
            except Exception as exc:
                return None, time.monotonic()-begun, exc

        def collect_job(future):
            job = plan_inflight.pop(future)
            task, attempt = job["task"], job["attempt"]
            if future.cancelled():
                tasks.interrupted(task, attempt, 0, 0)
                return
            response, seconds, error = future.result()
            tasks.receive(task, attempt, response, seconds=seconds, error=error, reserve=reserve_of(job["tokens"]))
            if isinstance(error, StopResearch):
                # A budget refusal (nothing sent) stops the run with its explanation instead
                # of counting as a failed save of this section.
                task_checkpoint(task)
                raise error
            if task["kind"] == "section":
                section_seconds.append(seconds)
            if attempt["status"] == "generated":
                toolkit.delivered(window_ids(job["inputs"]), inputs=job["inputs"])
            task_checkpoint(task)  # Proposal survives a crash before validation/save.
            raw = attempt["usage"] or {}
            activity("responses", "completed" if attempt["status"] == "generated" else "parallel_failed",
                task=task["kind"], task_id=task["id"], target=task["target"],
                input_tokens=raw.get("input_tokens", 0), output_tokens=raw.get("output_tokens", 0),
                cached_input_tokens=(raw.get("input_tokens_details") or {}).get("cached_tokens", 0),
                reasoning_tokens=(raw.get("output_tokens_details") or {}).get("reasoning_tokens", 0),
                call_seconds=round(seconds, 1), error_type=attempt["error_type"],
                estimated_cost_usd=usage["estimated_cost_usd"], parallel=True)
            job["apply"](task, attempt)

        def run_task_jobs(jobs):
            """Shared collector for plan parts and sections; mutations stay serial."""
            executor = ThreadPoolExecutor(max_workers=len(jobs), thread_name_prefix="scholar-v4-task")
            futures = []
            try:
                for job in jobs:
                    task = job["task"]
                    proposal = tasks.proposal(task)
                    if proposal:
                        job["apply"](task, proposal)
                        continue
                    job["attempt"] = tasks.begin(task,
                        evidence_ids=transmitted_evidence_ids(job["inputs"], toolkit.evidence),
                        input_signature=fingerprint(job["kwargs"]["input"]), reserve=reserve_of(job["tokens"]))
                    label(job["kwargs"], task, job["attempt"], job.get("stage"))
                    job["begun"] = time.monotonic()
                    future = executor.submit(task_background, job["kwargs"])
                    plan_inflight[future] = job
                    futures.append(future)
                collect_finished(futures, collect_job, check)
            finally:
                executor.shutdown(wait=False, cancel_futures=True)

        def harvest_parallel():
            """Background analyses never processed: real usage when they finished,
            their reservation otherwise. Their reading stays resumable (task pending)."""
            nonlocal total_tools
            stop_calls.set()
            for entry in list(speculative.values()):
                future = entry["future"]
                done = future.done() and not future.cancelled() and future.exception() is None
                if done:
                    response = future.result()[0]
                    tasks.receive(entry["task"], entry["attempt"], response,
                        seconds=future.result()[1], reserve=entry["reserve"])
                    toolkit.delivered(window_ids(entry["inputs"]), inputs=entry["inputs"])
                    task_checkpoint(entry["task"])
                    attempt = entry["attempt"]
                    arguments = tasks.parse(entry["task"], attempt, "analyze_article")
                    if arguments and arguments.get("action") == "save":
                        prior_focus = state.get("automatic_reading_focus")
                        state["automatic_reading_focus"] = copy.deepcopy(entry["focus"])
                        try:
                            total_tools += 1
                            counts["analyze_article"] = counts.get("analyze_article", 0)+1
                            result = toolkit.execute("analyze_article", arguments, deadline, settle=True)
                            if result.get("saved"):
                                aid = entry["focus"]["article_id"]
                                reading_completed(state, toolkit.store.article(aid),
                                    result.get("reviewed_evidence_ids", []), result.get("reviewed_fields", []))
                                tasks.saved(entry["task"], attempt, {"saved": True,
                                    "artifact_kind": "analysis", "artifact_id": aid}, arguments)
                            else:
                                tasks.reject(entry["task"], attempt, result)
                        except ValueError as exc:
                            tasks.reject(entry["task"], attempt, {"error": str(exc), "error_type": type(exc).__name__})
                        finally:
                            if prior_focus and prior_focus["article_id"] != entry["focus"]["article_id"]:
                                state["automatic_reading_focus"] = prior_focus
                            else:
                                state.pop("automatic_reading_focus", None)
                elif future.done() and not future.cancelled():
                    tasks.receive(entry["task"], entry["attempt"], error=future.exception(),
                        seconds=time.monotonic()-entry["begun"], reserve=entry["reserve"])
                    task_checkpoint(entry["task"])
                elif future.cancel():
                    tasks.interrupted(entry["task"], entry["attempt"], 0, 0)
                else:
                    tasks.interrupted(entry["task"], entry["attempt"], entry["reserve"], time.monotonic()-entry["begun"])
            speculative.clear()
            for future, job in list(plan_inflight.items()):
                if future.done():
                    collect_job(future)
                else:
                    cancelled_before_start = future.cancel()
                    tasks.interrupted(job["task"], job["attempt"],
                        0 if cancelled_before_start else reserve_of(job["tokens"]),
                        time.monotonic()-job["begun"])
            plan_inflight.clear()
            if pool[0] is not None:
                pool[0].shutdown(wait=False, cancel_futures=True)

        def balanced(ids):
            """Candidates of a linked message alternate between the locks whose
            searches found them, so one lock's long result list cannot fill the
            selection window. Not a quota: the model still retains what is relevant."""
            if not linked:
                return list(ids)
            origin = {}
            for search in state.get("recent_searches", [])[toolkit.searches_at_start:]:
                for aid in search.get("article_ids", []):
                    origin.setdefault(aid, (search.get("lock_ids") or ["-"])[0])
            groups = {}
            for aid in ids:
                groups.setdefault(origin.get(aid, "-"), []).append(aid)
            return [group[i] for i in range(max((len(g) for g in groups.values()), default=0))
                for group in groups.values() if i < len(group)]

        def argumentation_stage_allowed(goal=None):
            """Preconditions of an automatic comparison or blueprint once the goal is
            declared: the goal asks for it, this message's selection is done when it
            searched, and the requested deliverable keeps its own time. Without a
            declared goal (degraded mode) the earlier behaviour applies."""
            goal = goal if goal is not None else state.get("turn_goal")
            if not goal:
                return True
            if goal["deliverable"] == "comparison":
                pass
            elif goal["deliverable"] == "draft":
                # Drafting compares per section (draft_comparison_batch), never through the
                # corpus-wide comparison and blueprint stages.
                return False
            elif goal["deliverable"] in {"plan", "plan_revision"} and goal["reading_depth"] == "deep":
                if not time_left_for(plan_need()+call_estimate(durations, config.timeout_seconds*.2)):
                    return False
            else:
                return False
            # This message's new works are judged before any comparison.
            return not awaiting_selection()

        def awaiting_selection():
            """New works of this message are still to be judged: searched and not yet
            selected, or announced (search_needed) before the model's first round."""
            if turn_decided or priority_ids:
                return False
            goal = state.get("turn_goal") or {}
            announced = goal.get("search_needed") or (goal.get("search_needed") is None and goal.get("deliverable") in DISCOVERY_GOALS)
            return bool(toolkit.search_count or (announced and not rounds))

        def writing_sources():
            """Works cited by the sections still to draft, in section order, without a
            saved card yet: a section can only cite analysed works."""
            progress = state.get("document", {}).get("writing_progress") or {}
            saved = set((progress.get("saved_draft_ids") or {}))
            ids = []
            for section in state.get("outline", {}).get("sections", []):
                if section["id"] in progress.get("target_ids", []) and section["id"] not in saved:
                    ids.extend(section.get("article_ids", []))
            return [aid for aid in dict.fromkeys(ids) if aid in toolkit.registry and
                toolkit.registry[aid].selection_override is not False and
                (not toolkit.registry[aid].analysis or to_deepen(toolkit.registry[aid]))]

        def deepening_requested():
            goal = state.get("turn_goal") or {}
            return goal.get("reading_depth") == "deep"

        def to_deepen(article):
            """A card made from the abstract while the work's text is now stored locally: when
            the request asks for depth, that text is read before the card is used (an abstract
            card is never presented as a full reading)."""
            return bool(deepening_requested() and article.analysis and
                (article.analysis_basis or article.analysis.get("analysis_basis")) == "abstract" and article.fulltext.get("text")
                and article.id not in deepen_attempted)

        def draft_comparison_batch():
            """The works of the next writable section to compare before it is drafted, when the
            request asks for a comparison: two to four of its sources with exact proofs, not
            compared yet on these sources. A section with one documented work is drafted without
            a forced comparison."""
            from .writing_context import source_readiness
            from .argumentation import comparison_source_signature
            # Only an understood request to compare (comparison_requested), never the wording alone.
            if not (writing and (state.get("turn_goal") or {}).get("comparison_requested")) or draft_comparisons[0] >= MAX_DRAFT_COMPARISONS:
                return None
            progress = refresh_writing(state) or {}
            for sid in progress.get("target_ids", []):
                if sid in progress.get("saved_draft_ids", {}) or sid in progress.get("blocked", {}):
                    continue
                section = resolve_section(state, sid)
                if is_synthesis(section) or section_state(section) != "ready":
                    continue
                rows = source_readiness(state, toolkit, section_article_ids(state, toolkit.registry, section))
                ids = [aid for aid, row in rows.items() if row["status"] == "analysed"][:4]
                if len(ids) < 2 or tuple(ids) in compared_batches:
                    continue
                # Comparability known locally: two works must document the same dimension
                # (methods, protocol, results, limits) with exact proofs. Otherwise no relation
                # can be demonstrated and none is asked from the model (audit 128698bb: a call
                # to save an empty comparison); the absence is recorded with its reason.
                shared = comparable_dimensions(ids)
                if not shared:
                    key = "|".join(ids)
                    state.setdefault("comparison_outcomes", {})[key] = {"article_ids": ids, "status": "not_comparable",
                        "source_signature": comparison_source_signature(toolkit.registry, ids),
                        "reason": "Aucune dimension (méthode, protocole, résultats, limites) documentée avec preuves "
                            "exactes par au moins deux de ces publications."}
                    compared_batches.add(tuple(ids))
                    activity("argumentation", "not_comparable", stage="comparison", article_ids=ids)
                    continue
                ids = [aid for aid in ids if any(aid in members for members in shared.values())]
                receipt = state.get("comparison_reading", {}).get("|".join(ids), {})
                if receipt.get("source_signature") == comparison_source_signature(toolkit.registry, ids) and (
                        receipt.get("complete") or receipt.get("windows", 0) >= ARGUMENT_WINDOWS):
                    continue
                return ids
            return None

        def request_pages():
            """Card pages delivered in this request (durable, so a resumption knows them too)."""
            message = (state.get("messages") or [{}])[-1].get("id", "")
            store = state.setdefault("request_card_pages", {})
            if store.get("message_id") != message:
                store.clear()
                store.update(message_id=message, keys=[], claims=[], repeated=False)
            return store

        def card_claim_keys(aid):
            from .writing_context import claim_key
            from .context import analysis_projection
            if aid not in toolkit.registry:
                return []
            view = analysis_projection(toolkit.registry[aid].analysis, evidence=toolkit.evidence)
            return [claim_key(c) for value in view.values() if isinstance(value, list) and value and isinstance(value[0], dict)
                for c in value]

        def forbidden_tools():
            """Tools the consultant's explicit constraints for this message exclude."""
            from .references import FORBIDDEN_TOOLS
            refs = (state.get("turn_goal") or {}).get("references") or state.get("conversation_references") or {}
            return {tool for constraint in refs.get("constraints") or [] for tool in FORBIDDEN_TOOLS.get(constraint, ())}

        def phase_tools(name):
            """The tool of an argumentation phase, restricted to that phase's action: a
            comparison phase saves (relations required, possibly empty when nothing can be
            demonstrated); it never offers the read form refused afterwards (audit 128698bb)."""
            tools = copy.deepcopy([t for t in available_definitions() if t["name"] == name])
            if name == "compare_articles" and tools:
                relations = tools[0]["parameters"]["properties"]["relations"]
                if "anyOf" in relations:
                    tools[0]["parameters"]["properties"]["relations"] = {**next(
                        option for option in relations["anyOf"] if option.get("type") == "array"),
                        "title": relations.get("title", "Relations")}
            return tools

        def comparable_dimensions(ids):
            from .argumentation import comparable_dimensions as shared_dimensions
            return shared_dimensions(toolkit, ids)

        def is_synthesis(section):
            """A closing synthesis (conclusion, perspectives, overall discussion): written once the
            scientific sections it summarises are saved or set aside."""
            return bool(SYNTHESIS_TITLE.search(section.get("title", "")))

        def section_state(section):
            """Documentary state of a section, from its sources' own state (never from the
            presence of an abstract): ready (at least one source analysed with exact proofs;
            unavailable sources stay named limits), needs_analysis (a source can still be
            read in this request and none is citable yet, or a reading of it is running),
            framing (no citable publication, but project facts: a framing section without
            bibliographic results), blocked (nothing citable at all)."""
            from .writing_context import source_readiness
            progress = state.get("document", {}).get("writing_progress") or {}
            if is_synthesis(section):
                others = [sid for sid in progress.get("target_ids", []) if sid != section["id"]
                    and not is_synthesis(resolve_section(state, sid))]
                if any(sid not in progress.get("saved_draft_ids", {}) and sid not in progress.get("blocked", {}) for sid in others):
                    # The synthesis waits for the sections it synthesises (audit 91590987: written
                    # before section 7, it used 44 % of the run's tokens).
                    return "awaiting_sections"
            rows = source_readiness(state, toolkit, section_article_ids(state, toolkit.registry, section))
            statuses = {aid: row["status"] for aid, row in rows.items()}
            if not reading_stopped and automatic_reading:
                for aid in statuses:
                    if statuses[aid] == "analysed" and to_deepen(toolkit.registry[aid]):
                        statuses[aid] = "to_analyse"
            running = any(aid in speculative for aid in statuses)
            pending = [aid for aid, status in statuses.items() if status == "to_analyse"] if not reading_stopped and automatic_reading else []
            if running or (pending and "analysed" not in statuses.values()):
                return "needs_analysis"
            if pending and len(pending) > sum(status == "analysed" for status in statuses.values()):
                # Most of its works are still being read: waiting gives a better section.
                return "needs_analysis"
            if "analysed" in statuses.values():
                return "ready"
            if linked and section_project_facts(section):
                return "framing"
            return "blocked"

        def section_project_facts(section):
            from .diagnostic_link import lock_dossier, dossier_evidence_ids
            return dossier_evidence_ids(lock_dossier(state, section.get("lock_ids") or None, 4000))

        def section_ready(section=None):
            """A section (any remaining one by default) can be written now."""
            if section is None:
                progress = refresh_writing(state)
                if not progress:
                    return False
                return any(section_ready(resolve_section(state, sid)) for sid in progress["target_ids"]
                    if sid not in progress.get("saved_draft_ids", {}) and sid not in progress.get("blocked", {}))
            return bool(section) and section_state(section) in {"ready", "framing"}

        def section_estimate():
            """Expected duration of one section save: measured in this run, else the default."""
            return max([SECTION_SECONDS, *section_seconds])

        def section_hint(section_id, repair=None, framing=False):
            extra = ""
            if framing:
                extra += (" Section de cadrage : aucune publication de cette section n'a de fiche citable. Ne présenter aucun "
                    "résultat bibliographique ; s'appuyer sur les travaux du projet de diagnostic_context, cités [projet:evidence_id], "
                    "et nommer les publications à analyser comme limite documentaire (article_ids et evidence_ids : []).")
            if repair:
                extra += (" Réparation : la proposition précédente (appel ci-dessus) a été refusée pour le motif exact du "
                    "résultat d'outil. Corriger seulement ce qui est refusé, garder les phrases étayées, et réenregistrer. "
                    "Si le refus contient paragraph_diagnosis : envoyer write_section(save) avec paragraph_patches pour les seuls "
                    "paragraphes listés (text peut être null) ; les autres paragraphes sont conservés à l'identique et toute la "
                    "section est revalidée.")
            return (f"Rédiger maintenant uniquement la section {section_id} et enregistrer le texte avec write_section(save). "
                "Raisonner à partir de section_drafting.packet (question, sources et leur état, proof_backed_claims avec leurs "
                "preuves exactes) et de diagnostic_context (unités de travail du projet : démarches, méthodes, paramètres, "
                "résultats, limites, avec leurs preuves). Confronter précisément ce que les publications ont établi, dans quelles "
                "conditions, à ce que le projet a tenté et obtenu ; dire ce qu'elles expliquent, encadrent ou ne résolvent pas. "
                "Résultat de publication : [paper_ID] et ses [ev_…] dans la même phrase ; fait du projet : [projet:evidence_id] ; "
                "comparaison : les deux ; une interprétation est présentée comme telle. claims : [] ou phrases recopiées mot pour "
                "mot du texte avec leur type (scientific, project, comparison, interpretation). Chaque valeur chiffrée figure "
                "dans une preuve citée du même paragraphe. manuscript_map dit ce que les autres sections établissent : y "
                "renvoyer brièvement plutôt que répéter ; argument_summary résume le rôle, les acquis, les limites et les liens "
                "de cette section. " + extra.strip() + " "
                "Exploiter section_drafting.packet : analyses approfondies, blueprint, relations, faits du projet distincts, preuves et transitions. "
                "Citer seulement les résultats soutenus par les preuves exactes ; si un détail de fiche ou une preuve essentielle manque, "
                "le récupérer avec get_analysis_claims ou lire le passage ciblé. Appliquer consultant_instructions (consignes, pas des faits). "
                "Ne pas annoncer la réussite avant le résultat saved=true.")

        def section_task(section):
            signature = fingerprint([state.get("outline", {}).get("plan_revision"),
                {k: v for k, v in section.items() if k != "drafts"},
                source_signature(state, toolkit.registry, section_article_ids(state, toolkit.registry, section)),
                writing.get("mode"), writing.get("baseline_draft_ids", {}).get(section["id"])])
            task = tasks.task("section", section["id"], signature)
            if not task["attempts"] and not task.get("inherited_from"):
                # Same section, new sources' cards: the refused proposal of the replaced task stays
                # the base of the repair (local, paragraph by paragraph) instead of a regeneration.
                previous = [row for row in tasks.rows.values() if row is not task and row["kind"] == "section"
                    and row["target"] == section["id"] and row.get("status") != "saved" and tasks.last_refusal(row)]
                if previous:
                    task["inherited_from"] = previous[-1]["id"]
            signatures = state.setdefault("section_read_signatures", {})
            if signatures.get(section["id"]) != signature:
                state.setdefault("section_read_rounds", {}).pop(section["id"], None)
                state.setdefault("section_received_claims", {}).pop(section["id"], None)
                state.setdefault("section_read_receipts", {}).pop(section["id"], None)
                signatures[section["id"]] = signature
            return task

        def repair_merge(task, arguments):
            """paragraph_patches: the refused proposal of this task with only the named
            paragraphs replaced (local_repair.merge); everything else is kept exactly and
            the whole section is validated again by write_section."""
            if not arguments.get("paragraph_patches"):
                return arguments
            from .local_repair import merge
            refused = tasks.last_refusal(task)
            base = (refused or {}).get("arguments") or {}
            if not base.get("text"):
                raise ValueError("Aucune proposition refusée à réparer pour cette section : envoyer le texte complet.")
            merged, receipt = merge(base, arguments)
            merged["section_id"], merged["action"] = arguments.get("section_id") or base.get("section_id"), "save"
            if arguments.get("argument_summary"):
                merged["argument_summary"] = arguments["argument_summary"]
            activity("writing_progress", "local_repair", section_id=merged["section_id"], **receipt)
            return merged

        def section_refusal(arguments, result):
            """The exact refusal plus the paragraphs responsible for it (local diagnosis)."""
            from .local_repair import diagnose
            if not isinstance(result, dict) or not arguments.get("text"):
                return result
            diagnosis = diagnose(toolkit, arguments, result)
            return {**result, "paragraph_diagnosis": diagnosis} if diagnosis else result

        def apply_section_task(task, attempt):
            nonlocal total_tools
            section = resolve_section(state, task["target"])
            if task.get("saved") and any(d["id"] == task["saved"].get("draft_id") for d in section.get("drafts", [])):
                return
            arguments = tasks.parse(task, attempt, "write_section")
            if arguments is None:
                writing_failure(section["id"], task["refusal"], fatal=False)
                return
            if arguments.get("action") != "save" or str(arguments.get("section_id")) not in {section["id"], str(state["outline"]["sections"].index(section)+1)}:
                refusal = {"error": "Sauvegarder uniquement la section en cours avec write_section(save)."}
                tasks.reject(task, attempt, refusal)
                writing_failure(section["id"], refusal, fatal=False)
                return
            arguments["section_id"] = section["id"]
            state["writing_focus"] = {"section_id": section["id"],
                "article_ids": section_article_ids(state, toolkit.registry, section), "retrieval_progress": []}
            total_tools += 1
            counts["write_section"] = counts.get("write_section", 0)+1
            try:
                arguments = repair_merge(task, arguments)
                attempt["arguments"] = copy.deepcopy(arguments)
                result = toolkit.execute("write_section", arguments, deadline, settle=True, operation_id=attempt["id"])
                if not result.get("saved"):
                    result = section_refusal(arguments, result)
                    tasks.reject(task, attempt, result)
                    writing_failure(section["id"], result, fatal=False)
                    return
                confirmed_save(state, section["id"], result["draft_id"], result["text"])
                tasks.saved(task, attempt, {k: v for k, v in result.items() if k != "text"}, arguments)
                blocked_since_save[0] = 0
                activity("writing_progress", "completed", section_id=section["id"], draft_id=result["draft_id"],
                    document_version_id=result["document_version_id"], parallel=True)
            except ValueError as exc:
                refusal = section_refusal(arguments, {"error": str(exc), "error_type": type(exc).__name__})
                tasks.reject(task, attempt, refusal)
                writing_failure(section["id"], refusal, fatal=False)
            finally:
                state.pop("writing_focus", None)

        def parallel_sections():
            nonlocal exchange, rounds, exploration_rounds
            progress = refresh_writing(state)
            if writing["mode"] != "full" or deadline-time.monotonic() <= section_estimate()+10:
                return False
            # One queue for first drafts and repairs: refused sections keep their last proposal
            # and exact refusal and come back in the next batch, fewest attempts first, so one
            # refused section cannot monopolise the run (b52f582a: 430 s on one section).
            candidates = []
            for index, sid in enumerate(progress["target_ids"]):
                if sid in progress.get("saved_draft_ids", {}) or sid in progress.get("blocked", {}):
                    continue
                section = resolve_section(state, sid)
                status = section_state(section)
                if status in {"ready", "framing"}:
                    candidates.append((section_failures.get(sid, 0), index, section, status))
            candidates.sort(key=lambda row: row[:2])
            batch = [(section, status) for _, _, section, status in candidates[:PARALLEL_SECTIONS]]
            if len(batch) < 2:
                return False
            saved_exchange, jobs = exchange, []
            try:
                for section, status in batch:
                    state["writing_focus"] = {"section_id": section["id"],
                        "article_ids": section_article_ids(state, toolkit.registry, section), "retrieval_progress": []}
                    task = section_task(section)
                    exchange = tasks.recovery_exchange(task)
                    repair = bool(exchange)
                    inputs = request_inputs()
                    sent = transmitted_evidence_ids(inputs, toolkit.evidence)
                    if status == "ready" and not sent:
                        # Verified on the final request: a section with analysed sources is never
                        # generated without one exact passage to support its claims.
                        activity("writing_progress", "context_insufficient", section_id=section["id"])
                        continue
                    tools = [t for t in available_definitions() if t["name"] == "write_section"]
                    tools[0]["parameters"]["properties"]["action"] = {"type": "string", "enum": ["save"]}
                    kwargs = dict(model=config.model, instructions=instructions_for("section")+("\n"+LINKED_GUIDANCE if linked else ""),
                        **cache_key("section"), input=[*json.loads(json.dumps(inputs)), turn_instruction(section_hint(section["id"],
                            repair=repair, framing=status == "framing"))],
                        tools=tools, store=False, stream=True, reasoning={"effort": config.reasoning_effort},
                        max_output_tokens=config.max_output_tokens, max_tool_calls=1,
                        tool_choice={"type": "function", "name": "write_section"}, parallel_tool_calls=False,
                        include=["reasoning.encrypted_content", "web_search_call.action.sources"])
                    tokens = request_tokens(len(json.dumps(kwargs, ensure_ascii=False).encode()))
                    jobs.append(dict(task=task, inputs=inputs, kwargs=kwargs, tokens=tokens, apply=apply_section_task,
                        stage="section_repair" if repair else None))
                    activity("responses", "running", task="section", section_id=section["id"], parallel=True, repair=repair,
                        section_state=status, proofs_sent=len(sent), input_chars=len(json.dumps(inputs, ensure_ascii=False)),
                        estimated_input_tokens=tokens)
            finally:
                state.pop("writing_focus", None)
                exchange = saved_exchange
            cost, tokens_in_flight = in_flight()
            while jobs and (usage["estimated_cost_usd"]+usage["unreported_reserved_cost_usd"]+cost+sum(reserve_of(j["tokens"]) for j in jobs) > config.max_cost_usd
                    or usage["input_tokens"]+usage["output_tokens"]+tokens_in_flight+sum(j["tokens"]+config.max_output_tokens for j in jobs) > config.max_tokens
                    or total_tools+len(jobs) > tool_limit()):
                jobs.pop()
            if len(jobs) < 2:
                return False
            enter_stage("writing")
            rounds += 1
            exploration_rounds += 1
            activity("responses", "parallel_started", task="section", sections=len(jobs))
            try:
                run_task_jobs(jobs)
            finally:
                refresh_writing(state)
            if blocked_since_save[0] >= 2:
                # Two sections set aside without any save in between: a systemic cause.
                raise StopResearch("section_save_failed")
            return True

        def apply_goal(goal):
            """Adapt this turn's existing stages to the model's reading of the message.
            An explicit drafting request keeps its own pipeline."""
            nonlocal automatic_reading, argumentation_requested, plan_goal, writing, writing_round_allowance
            nonlocal writing_continuation, analysis_round_allowance, selection_ready, priority_ids
            deliverable, depth = goal["deliverable"], goal["reading_depth"]
            # The publications this message targets bound the work before any tool runs
            # (run ce08ad31: the right pair was understood, then fifteen readings of four
            # other works were scheduled from the general recommendations).
            refs = goal.get("references") or {}
            targets = [aid for aid in refs.get("article_ids") or [] if aid in toolkit.registry]
            # A draft works from its plan sections' own sources (run dcf5d05b: the ten works of the
            # turn replaced the five sources of the requested introduction).
            if targets and refs.get("scope") in {"exclusive", "primary"} and not refs.get("ambiguous") and deliverable != "draft":
                priority_ids = tuple(dict.fromkeys(targets))
                state["current_turn_article_ids"] = list(priority_ids)
                if refs["scope"] == "exclusive" and not (writing and deliverable == "draft"):
                    reading_scope[0] = list(priority_ids)
            if refs.get("ambiguous") and not (writing and deliverable == "draft"):
                # A real ambiguity is asked to the consultant: nothing is read or compared.
                automatic_reading = argumentation_requested = False
                state.pop("argumentation_focus", None)
                activity("references", "clarification_needed", candidates=refs.get("candidates", []))
                return
            if {"no_reanalysis", "no_download"} & set(refs.get("constraints") or []) and not (writing and deliverable == "draft"):
                # Stored cards only: no automatic reading at all; comparisons use the saved cards.
                depth = "none"
            # Dependencies follow the declared goal, not the wording: a discovery
            # (literature search, new plan) reads and compares only after this
            # message's selection; every other goal works on the current corpus.
            if priority_ids or turn_decided or goal.get("search_needed") is False or deliverable not in DISCOVERY_GOALS:
                selection_ready = True
            else:
                # New works are (or may be) searched: nothing is read or compared before
                # the model's first round, then before its selection if it searched.
                selection_ready = False
            # A focus prepared under an earlier reading of the message is dropped
            # before it starts if the declared goal does not need it.
            if not argumentation_stage_allowed(goal):
                state.pop("argumentation_focus", None)
            if writing and deliverable == "draft":
                # « Rédige et compare » keeps both obligations (audit 91590987: the comparison
                # was dropped as soon as the deliverable was a draft).
                argumentation_requested = bool(goal.get("comparison_requested"))
                if not writing_continuation:
                    return
                if writing["mode"] == "section":
                    # The requested section's sources bound every reading of this request.
                    own = [aid for sid in writing["target_ids"] for aid in resolve_section(state, sid).get("article_ids", [])
                        if aid in toolkit.registry]
                    priority_ids = tuple(dict.fromkeys(own))
                    state["current_turn_article_ids"] = list(priority_ids)
                # Drafting from the saved plan: the sources its sections cite are read
                # first (a section can only cite analysed works), in section order.
                scope = writing_sources()
                if scope:
                    reading_scope[0] = scope
                    reading_tasks(state, toolkit.registry, requested_fields, priority_ids, scope_ids=scope)
                    analysis_round_allowance = max(analysis_round_allowance, reading_allowance(state, config.max_deep_analysis))
                automatic_reading = bool(scope)
                return
            if writing:
                # The wording suggested a draft; the model understood another request.
                document = state.setdefault("document", {})
                if prior_progress is None:
                    document.pop("writing_progress", None)
                else:
                    document["writing_progress"] = prior_progress
                writing, writing_round_allowance, writing_continuation = None, 0, False
                activity("writing_progress", "cancelled", reason="turn_goal", deliverable=deliverable)
            plan_goal = deliverable in {"plan", "plan_revision"}
            if deliverable == "draft" and not PLAN_BEFORE_WRITING.search(request_text):
                # One named section (« la première section », « la section 3 ») stays the only target
                # of the request; the whole plan is drafted only when the request asks for it.
                from .writing_progress import requested_section
                single = requested_section(state, request_text) or requested_section(state, goal.get("understanding") or "")
                writing = open_writing(state, "section", [single], {single: ((resolve_section(state, single).get("drafts")
                    or [{}])[-1]).get("id")}) if single else open_writing(state)
                writing_round_allowance = len(writing["target_ids"])*3+4
                writing_continuation = bool(state.get("outline", {}).get("sections"))
                # Sections compare their works from the saved cards; no automatic
                # comparison or blueprint stage precedes them.
                reading, argumentation_requested = True, bool(goal.get("comparison_requested"))
                activity("writing_progress", "running", stage="draft_requested")
                scope = writing_sources() if writing_continuation else []
                if scope:
                    reading_scope[0] = scope
                    reading_tasks(state, toolkit.registry, requested_fields, priority_ids, scope_ids=scope)
                    analysis_round_allowance = max(analysis_round_allowance, reading_allowance(state, config.max_deep_analysis))
                    automatic_reading = True
                    return
            else:
                plan_goal = plan_goal or deliverable == "draft"
                # A plan is built from abstracts, cards already saved and the project
                # context; full texts are read for it only when the model asks for depth.
                # Analysis, comparison and deep reading requests read the full texts.
                reading = (depth == "deep") if plan_goal else (depth in {"core", "deep"} or deliverable in {"comparison", "deep_reading"})
                if depth == "none":
                    # Memory only (« uniquement les analyses déjà enregistrées »): no new reading.
                    reading = False
                argumentation_requested = deliverable == "comparison" or (plan_goal and depth == "deep")
            if reading and not automatic_reading:
                reading_tasks(state, toolkit.registry, requested_fields, priority_ids, scope_ids=reading_scope[0])
                analysis_round_allowance = max(analysis_round_allowance, reading_allowance(state, config.max_deep_analysis))
            automatic_reading = reading

        def plan_parts():
            """Independent parts of a plan: a framing part (introduction, synthesis) and
            one part per active lock, or per declared search axis in Express."""
            link = state.get("diagnostic_link") or {}
            if linked:
                parts = [{"kind": "lock", "lock_ids": [v["id"]], "label": v["title"]} for v in link.get("snapshot", {}).get("locks", [])
                    if v["id"] in link.get("active_lock_ids", [])]
            else:
                parts = [{"kind": "axis", "lock_ids": [], "label": axis} for axis in state.get("research_state", {}).get("search_axes", [])[:4]]
            return [{"kind": "framing", "lock_ids": [], "label": "introduction et synthèse"}, *parts] if len(parts) >= 2 else []

        def plan_need():
            """Time the plan still needs: one part (parallel) or a whole plan in one call."""
            measured = [d for d, out in durations if out >= 1000]
            if plan_parts() and not any(sec.get("drafts") for sec in state.get("outline", {}).get("sections", [])):
                return PLAN_PART_SECONDS
            return max([PLAN_SECONDS, *measured])

        def part_hint(part):
            if part["kind"] == "framing":
                return ("Partie cadre du plan d'état de l'art. sections : l'introduction (1 à 2 sections : chaîne technique, enjeux, "
                    "interdépendance des verrous ou axes, critères d'évaluation). closing_sections : la synthèse et les perspectives "
                    "(1 section). Ne détaille pas chaque verrou ou axe : d'autres parties s'en chargent en parallèle. consultant_note : "
                    "deux ou trois phrases naturelles (choix, limites de la recherche, question utile pour poursuivre).")
            target = (f"au verrou {part['lock_ids'][0]} — « {part['label']} »" if part["kind"] == "lock" else f"à l'axe « {part['label']} »")
            return (f"Partie du plan consacrée {target}. Propose 1 à 3 sections (paragraphes argumentés) : scientific_objective "
                "(question traitée), " + (f"lock_ids (au moins [\"{part['lock_ids'][0]}\"]), " if part["lock_ids"] else "lock_ids [], ") +
                "article_ids et article_roles choisis dans plan_sources (publications de cette partie) ou, si elle apporte "
                "ici quelque chose de précis, dans other_plan_sources (références des autres parties : ne pas les répéter sans "
                "rôle propre). experimental_results : méthodes et résultats établis par les fiches (card) ou les résumés, avec "
                "leur portée ; aucun chiffre absent des sources. project_connection : confronter aux essais, paramètres et "
                "résultats du projet donnés dans diagnostic_context (link=declared ou lock_evidence_passage, à citer comme "
                "faits du projet ; general_context n'est pas rattaché au verrou). limitations, missing_information (lacunes, "
                "lectures à faire, verrou sans publication pertinente). Aucun candidat non retenu, aucun résultat inventé. "
                "closing_sections : []. consultant_note : une phrase sur cette partie.")

        def provisional_section(part):
            ids = [aid for aid, a in toolkit.registry.items() if validated(a) and a.selection_override is not False and
                (not part["lock_ids"] or set(part["lock_ids"]) & set((a.assessment or {}).get("lock_ids") or []))][:4]
            return {"title": part["label"][:300], "description": "Section provisoire : la génération de cette partie n'a pas abouti.",
                "article_ids": ids, "scientific_objective": "", "questions": [], "article_roles": [], "relation_ids": [],
                "experimental_results": [], "limitations": [], "evidence_ids": [],
                "missing_information": ["Partie à reprendre : génération ou validation non aboutie ; les autres parties sont conservées."],
                "project_connection": "", "figure_suggestions": [], "lock_ids": part["lock_ids"]}

        def clean_section(section, part):
            # Normalize presentation only. Unknown sources/relations/proofs are
            # refused by the shared validator, never silently removed.
            section = dict(section)
            section["title"] = strip_numbering(section.get("title", ""))
            section["lock_ids"] = list(dict.fromkeys([*part["lock_ids"], *section.get("lock_ids", [])]))
            return section

        def apply_plan_task(task, attempt):
            nonlocal total_tools
            from .toolkit import PlanPartArgs
            from .tools.outline import validate_sections
            arguments = tasks.parse(task, attempt, "propose_plan_sections", PlanPartArgs)
            if arguments is None:
                return
            part = task["part"]
            try:
                sections = [clean_section(sec, part) for sec in arguments["sections"]]
                closing = [clean_section(sec, part) for sec in arguments["closing_sections"]]
                if not sections and not closing:
                    raise ValueError("La partie ne contient aucune section ; elle reste à générer.")
                validate_sections(state, [*sections, *closing], toolkit.registry)
                counts["propose_plan_sections"] = counts.get("propose_plan_sections", 0)+1
                total_tools += 1
                tasks.saved(task, attempt, {"saved": True, "artifact_id": task["id"], "artifact_kind": "plan_part"},
                    {"sections": sections, "closing_sections": closing, "consultant_note": arguments["consultant_note"]})
            except ValueError as exc:
                tasks.reject(task, attempt, {"error": str(exc), "error_type": type(exc).__name__})

        def assemble_plan():
            nonlocal total_tools, plan_done, plan_note, plan_incomplete
            saved = [task for task in plan_tasks if task["status"] == "saved"]
            if not saved:
                return False
            merged, closing, notes = [], [], []
            for task in plan_tasks:
                part = task["part"]
                if task["status"] != "saved":
                    if part["kind"] != "framing":
                        merged.append(provisional_section(part))
                    continue
                content = task["content"]
                merged.extend(content["sections"])
                closing.extend(content["closing_sections"])
                notes.append(content["consultant_note"])
            plan_incomplete = not tasks.complete(plan_tasks)
            failed = sum(task["status"] != "saved" for task in plan_tasks)
            message = next((n.strip() for n in notes if n.strip()), "")
            if failed:
                message += (" " if message else "") + f"{failed} partie(s) du plan restent provisoires (génération non aboutie)."
            generation = state["plan_generation"]
            assembled = fingerprint([merged, closing, [(t["id"], t["status"] == "saved") for t in plan_tasks]])
            if (plan_done and generation.get("assembled_signature") == assembled
                    and generation.get("outline_version") == state.get("outline", {}).get("version")):
                return True
            if (generation.get("assembled_signature") != assembled or not state.get("outline", {}).get("sections")
                    or generation.get("outline_version") != state["outline"].get("version")):
                total_tools += 1
                counts["manage_outline"] = counts.get("manage_outline", 0)+1
                try:
                    toolkit.execute("manage_outline", {"action": "replace" if state.get("outline", {}).get("sections") else "create",
                        "title": state.get("outline", {}).get("title") or "État de l’art", "section_id": None,
                        "target_section_id": None, "position": None, "sections": [*merged, *closing],
                        "article_ids": None, "offset": 0, "limit": 8, "consultant_message": message}, deadline, settle=True)
                except ValueError as exc:
                    generation.update(complete=False, refusal={"error": str(exc)})
                    activity("manage_outline", "failed", error_type=type(exc).__name__, error=str(exc))
                    return False
                generation.update(assembled_signature=assembled, outline_version=state["outline"]["version"])
            generation["complete"] = not plan_incomplete
            state["outline"]["generation"] = {"required_task_ids": [t["id"] for t in plan_tasks],
                "complete": not plan_incomplete, "missing_task_ids": [t["id"] for t in plan_tasks if t["status"] != "saved"]}
            plan_done, plan_note = True, message
            state.pop("writing_planning", None)
            activity("writing_progress", "completed", stage="plan", parallel_parts=len(plan_tasks), failed_parts=failed)
            return True

        def parallel_plan():
            nonlocal exchange, rounds, exploration_rounds, plan_tasks
            from .turn import plan_source_ids
            parts = plan_parts()
            if not parts or any(sec.get("drafts") for sec in state.get("outline", {}).get("sections", [])):
                return False
            previous = state.get("plan_generation", {})
            request = previous.get("request", request_text) if re.search(r"continu|reprend|poursui", request_text, re.I) else request_text
            signature = fingerprint([parts, request, source_signature(state, toolkit.registry, plan_source_ids(state, toolkit.registry))])
            if previous.get("signature") != signature:
                state["plan_generation"] = {"signature": signature, "request": request, "complete": False}
            plan_tasks = [tasks.task("plan_part", fingerprint(part), signature) for part in parts]
            for part, task in zip(parts, plan_tasks):
                task["part"] = part
            state["plan_generation"]["required_task_ids"] = [t["id"] for t in plan_tasks]
            saved_exchange, jobs = exchange, []
            try:
                for part, task in zip(parts, plan_tasks):
                    if task["status"] == "saved":
                        continue
                    state["plan_part"] = part
                    exchange = tasks.recovery_exchange(task)
                    inputs = request_inputs()
                    kwargs = dict(model=config.model, instructions=instructions_for("planning")+("\n"+LINKED_GUIDANCE if linked else ""),
                        **cache_key("plan_part"), input=[*json.loads(json.dumps(inputs)), turn_instruction(part_hint(part))],
                        tools=definitions(only={"propose_plan_sections"}), store=False, stream=True,
                        reasoning={"effort": config.reasoning_effort}, max_output_tokens=config.max_output_tokens, max_tool_calls=1,
                        tool_choice={"type": "function", "name": "propose_plan_sections"}, parallel_tool_calls=False,
                        include=["reasoning.encrypted_content", "web_search_call.action.sources"])
                    jobs.append(dict(task=task, inputs=inputs, kwargs=kwargs,
                        tokens=request_tokens(len(json.dumps(kwargs, ensure_ascii=False).encode())), apply=apply_plan_task))
            finally:
                state.pop("plan_part", None)
                exchange = saved_exchange
            # Each proposal and the assembly consume the existing tool budget.
            while jobs and total_tools+len(jobs)+1 > tool_limit():
                jobs.pop()
            reserve = sum(reserve_of(j["tokens"]) for j in jobs)
            tokens_needed = sum(j["tokens"]+config.max_output_tokens for j in jobs)
            running_cost, running_tokens = in_flight()
            if (usage["estimated_cost_usd"]+usage["unreported_reserved_cost_usd"]+reserve+running_cost > config.max_cost_usd or
                    usage["input_tokens"]+usage["output_tokens"]+tokens_needed+running_tokens > config.max_tokens):
                return assemble_plan()
            enter_stage("plan")
            if jobs:
                rounds += 1
                exploration_rounds += 1
                activity("responses", "parallel_started", task="plan", parts=len(jobs))
                try:
                    run_task_jobs(jobs)
                finally:
                    assembled = assemble_plan()
                return assembled
            return assemble_plan()

        def real_wait(futures):
            from concurrent.futures import wait as wait_futures
            wait_futures(futures, timeout=.2)

        def unsearched_targets():
            """Targeted active locks without any attributed search yet (linked mode)."""
            if not linked:
                return []
            from .turn import lock_coverage
            coverage = lock_coverage(state, toolkit.registry) or {}
            targets = (state.get("turn_goal") or {}).get("lock_ids") or [r["lock_id"] for r in coverage.get("locks", [])]
            return [lid for lid in coverage.get("not_searched", []) if lid in targets]

        def gap_round_due():
            """After the first selection, one more search round for locks still without
            a retained publication, only when the plan's own time stays available."""
            if not (plan_goal and linked and not gap_used and toolkit.search_count):
                return False
            from .turn import lock_coverage
            coverage = lock_coverage(state, toolkit.registry) or {}
            goal_locks = (state.get("turn_goal") or {}).get("lock_ids") or [r["lock_id"] for r in coverage.get("locks", [])]
            missing = [r["lock_id"] for r in coverage.get("locks", []) if r["lock_id"] in goal_locks and not r["retained_total"]]
            allowance = config.max_tool_rounds+analysis_round_allowance+argument_round_allowance+writing_round_allowance
            return bool(missing) and exploration_rounds+2 <= allowance and deadline-time.monotonic() > plan_need()+120

        def understand():
            """One short forced call: the model reads the message in its context and
            declares its goal before any automatic stage. Returns True when applied."""
            nonlocal total_tools
            enter_stage("understanding")
            try:
                context = model_context(state, toolkit, 9000)
            except StopResearch:
                raise
            except Exception as exc:
                activity("turn_goal", "context_failed", error_type=type(exc).__name__)
                return False
            kwargs = dict(model=config.model, instructions=instructions_for("understanding")+("\n"+LINKED_GUIDANCE if linked else ""),
                **cache_key("understanding"), input=[*json.loads(json.dumps(context)), turn_instruction(
                    "Lis la demande du consultant dans son contexte et déclare ta compréhension avec set_turn_goal "
                    "(livrable, verrous, profondeur de lecture). Aucune autre action dans cet appel : la suite suit ta déclaration.")],
                tools=[t for t in available_definitions() if t["name"] == "set_turn_goal"], store=False, stream=True,
                reasoning={"effort": config.reasoning_effort}, max_output_tokens=config.max_output_tokens, max_tool_calls=1,
                tool_choice={"type": "function", "name": "set_turn_goal"}, parallel_tool_calls=False,
                include=["reasoning.encrypted_content", "web_search_call.action.sources"])
            tokens = request_tokens(len(json.dumps(kwargs["input"], ensure_ascii=False).encode())+len(kwargs["instructions"].encode())
                +len(json.dumps(kwargs["tools"]).encode()))
            reserve = reserve_of(tokens)
            if (usage["estimated_cost_usd"]+reserve+config.finalization_reserved_cost_usd > config.max_cost_usd or
                    usage["input_tokens"]+usage["output_tokens"]+tokens+config.max_output_tokens+config.finalization_reserved_tokens > config.max_tokens):
                return False
            understanding_calls[0] += 1
            activity("responses", "running", phase="understanding", input_chars=len(json.dumps(kwargs["input"], ensure_ascii=False)))
            task = tasks.task("understanding", state.get("messages", [{}])[-1].get("id", "message"), fingerprint(kwargs["input"]))
            attempt = tasks.begin(task, input_signature=fingerprint(kwargs["input"]), reserve=reserve)
            label(kwargs, task, attempt)
            begun, response, stream = time.monotonic(), None, None
            try:
                stream = client.with_options(timeout=min(config.request_timeout_seconds, max(.001, deadline-time.monotonic()))).responses.create(**kwargs)
                for event in stream:
                    check()
                    if event.type in {"response.completed", "response.incomplete", "response.failed"}:
                        response = event.response
            except StopResearch as exc:
                tasks.receive(task, attempt, response, seconds=time.monotonic()-begun, reserve=reserve, error=exc)
                task_checkpoint(task)
                raise
            except Exception as exc:
                activity("responses", "failed", phase="understanding", error_type=type(exc).__name__)
                tasks.receive(task, attempt, response, seconds=time.monotonic()-begun, reserve=reserve, error=exc)
                task_checkpoint(task)
                return False
            finally:
                if stream is not None:
                    stream.close()
            if response is None:
                return False
            raw = dump(response.usage) if getattr(response, "usage", None) else {}
            inp, out = raw.get("input_tokens", 0), raw.get("output_tokens", 0)
            cached = (raw.get("input_tokens_details") or {}).get("cached_tokens", 0) or 0
            tasks.receive(task, attempt, response, seconds=time.monotonic()-begun, reserve=reserve)
            task_checkpoint(task)
            activity("responses", "completed", phase="understanding", input_tokens=inp, output_tokens=out, cached_input_tokens=cached,
                estimated_cost_usd=usage["estimated_cost_usd"], call_seconds=round(durations[-1][0], 1), task="understanding")
            for item in (dump(i) for i in getattr(response, "output", None) or []):
                if item.get("type") == "function_call" and item.get("name") == "set_turn_goal":
                    total_tools += 1
                    counts["set_turn_goal"] = counts.get("set_turn_goal", 0)+1
                    try:
                        result = toolkit.execute("set_turn_goal", json.loads(item["arguments"]), deadline)
                    except Exception as exc:
                        activity("set_turn_goal", "failed", error_type=type(exc).__name__, error=str(exc)[:300])
                        return False
                    activity("set_turn_goal", "completed", deliverable=result["goal"]["deliverable"],
                        reading_depth=result["goal"]["reading_depth"])
                    apply_goal(result["goal"])
                    return True
            return False

        def uncovered_hint():
            """Active locks this turn targets that no attributed search has covered yet."""
            goal = state.get("turn_goal") or {}
            if not linked or not toolkit.search_count or goal.get("deliverable") in {"answer", "deep_reading", "draft"}:
                return ""
            from .turn import lock_coverage
            coverage = lock_coverage(state, toolkit.registry)
            targets = goal.get("lock_ids") or [row["lock_id"] for row in coverage["locks"]]
            missing = [lid for lid in coverage["not_searched"] if lid in targets]
            if gap_used and not state.get("selection_focus"):
                # The single gap round of a plan: locks still without a retained work.
                missing = [row["lock_id"] for row in coverage["locks"] if row["lock_id"] in targets and not row["retained_total"]]
                return (f" Gap round before the plan: active locks without any retained publication: {missing}. Run one targeted "
                    "search per lock with different scientific vocabulary (lock_ids), or explain why none applies; the selection and "
                    "the plan follow." if missing else "")
            return (f" Active locks without any attributed search yet: {missing}. Search them with lock_ids, "
                "or explain why, before selecting finalists." if missing else "")

        section_failures = {}
        section_tokens, section_calls, project_requests, context_room = {}, {}, set(), [None]
        section_rounds = state.setdefault("section_read_rounds", {})
        section_seconds = []
        claim_reads = {}
        claim_receipts = state.setdefault("section_read_receipts", {})
        seen_claims = state.setdefault("section_received_claims", {})
        blocked_since_save = [0]

        def writing_failure(section_id, message, *, fatal=True):
            # Counted per section: the refusal (with what to fix) reaches the model,
            # which corrects and retries. A section still refused after three
            # attempts is set aside with its cause; the following sections are
            # drafted in the same run and the document is never reported complete.
            nonlocal writing_attempts
            refusal = message if isinstance(message, dict) else {"error": message}
            message = refusal.get("error") or "Sauvegarde refusée."
            writing_attempts += 1
            section_failures[section_id] = section_failures.get(section_id, 0)+1
            state["document"]["writing_progress"].setdefault("errors", []).append(
                {"section_id": section_id, "message": message, "refusal": copy.deepcopy(refusal)})
            refresh_writing(state)
            activity("writing_progress", "failed", section_id=section_id, error=message[:500],
                attempt=section_failures[section_id])
            if section_failures[section_id] >= 3:
                state["document"]["writing_progress"].setdefault("blocked", {})[section_id] = message[:500]
                state.pop("writing_focus", None)
                refresh_writing(state)
                activity("writing_progress", "blocked", section_id=section_id, error=message[:500])
                blocked_since_save[0] += 1
                if fatal and blocked_since_save[0] >= 2:
                    # Two sections in a row refused without any save: a systemic
                    # cause, not a section-specific one; stop instead of spending
                    # the budget on every remaining section.
                    raise StopResearch("section_save_failed")

        def request_inputs():
            nonlocal exchange
            if writing and state.get("writing_focus") and not state.get("automatic_reading_focus"):
                task = section_task(resolve_section(state, state["writing_focus"]["section_id"]))
                recovery = tasks.recovery_exchange(task)
                if recovery and not any(item.get("call_id") == recovery[-1]["call_id"] for item in exchange):
                    exchange = recovery
            # A new bounded memory plus ONLY the last protocol exchange. No accumulated transcript.
            latest_question_chars = len(state.get("messages", [])[-1].get("content", "")) if state.get("messages") else 0
            memory_room = max(latest_question_chars+3000, config.finalization_context_chars if phase == "finalization" else
                (context_room[0] or SECTION_CONTEXT_CHARS) if state.get("writing_focus") and not state.get("automatic_reading_focus")
                and not state.get("argumentation_focus") else PLAN_PART_CONTEXT_CHARS if state.get("plan_part") else 18000)
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
            if understanding_needed:
                understood = understand()
                if understood:
                    goal_pending = False
                    if deferred_focus and automatic_reading:
                        state["automatic_reading_focus"] = deferred_focus
                    deferred_focus = None
                else:
                    # Degraded mode only (no goal returned): the earlier behaviour applies
                    # unchanged, including a reading left by an earlier turn.
                    if deferred_focus and not goal_pending:
                        state["automatic_reading_focus"] = deferred_focus
                        deferred_focus = None
                    activity("turn_goal", "missing")
            while not answer and not toolkit.finished:
                check()
                if provisional_reason:
                    # Not enough time left for the plan itself: a useful provisional plan now.
                    break
                if (plan_goal and not plan_done and phase == "exploration" and not state.get("writing_planning")
                        and deadline-time.monotonic() <= plan_need()+PLAN_MARGIN):
                    finalize("plan_time")
                    if provisional_reason:
                        break
                if (not selection_ready and state.get("turn_goal") and (state["turn_goal"].get("search_needed") or
                        state["turn_goal"]["deliverable"] in DISCOVERY_GOALS) and rounds and not awaiting_selection()):
                    # A search was announced but the model's round did not search: the
                    # current finalists are the corpus of this request.
                    selection_ready = True
                if goal_pending and rounds:
                    # The model has read the message: a reading left by an earlier
                    # turn resumes only if this turn still asks for reading.
                    goal_pending = False
                    if deferred_focus and automatic_reading:
                        state["automatic_reading_focus"] = deferred_focus
                    deferred_focus = None
                # Up to two model rounds to review this message's unseen candidates
                # before long readings; reading then resumes automatically.
                holding = review_rounds > 0
                review_rounds = max(0, review_rounds-1)
                if (phase == "exploration" and speculative and not state.get("automatic_reading_focus") and not state.get("selection_focus")
                        and not state.get("writing_planning")):
                    # An analysis already running in parallel is processed next, in launch order.
                    # While drafting, a section whose sources are ready is written first; only
                    # finished analyses (or one the next section needs) are processed meanwhile.
                    ready = [e for e in speculative.values() if e["future"].done()]
                    if not (writing and writing_continuation and section_ready()) or ready:
                        state["automatic_reading_focus"] = copy.deepcopy((ready or list(speculative.values()))[0]["focus"])
                if (phase == "exploration" and state.get("automatic_reading_focus")
                        and state["automatic_reading_focus"]["article_id"] not in speculative
                        and not time_left_for(call_estimate(durations, config.timeout_seconds*.2))):
                    # The analysis call would end inside the publication reserve (its
                    # duration is measured on this run): keep the task for a later
                    # turn and use the remaining time for the requested answer.
                    stopped = state.pop("automatic_reading_focus")
                    reading_stopped, reading_budget_reason = True, "exploration_time_budget"
                    state["scientific_reading"]["blocked_reason"] = "exploration_time_budget"
                    activity("scientific_reading", "stopped", reason="exploration_time_budget", article_id=stopped.get("article_id"))
                    # A draft keeps its remaining time for the sections (they cite the
                    # works already analysed); only the other requests finalize here.
                    if not writing and (not argumentation_requested or plan_goal):
                        finalize("exploration_time_budget")
                if (phase == "exploration" and automatic_reading and selection_ready and not goal_pending and not holding and not reading_stopped
                        and not state.get("selection_focus") and not state.get("automatic_reading_focus")
                        and not (writing and writing_continuation and section_ready())):
                    task = next_task(state, toolkit.registry, priority_ids, requested_fields, core_only=core_only_now(), skip_depth_ids=reused_cards)
                    if task:
                        aid = task["article_id"]
                        article = toolkit.registry[aid]
                        required_tools = 2 + (aid not in toolkit.checked_ids and aid not in verification_attempted) + (not article.fulltext.get("text") and not task.get("access_attempted"))
                        used_tokens = usage["input_tokens"]+usage["output_tokens"]+in_flight()[1]
                        spent = usage["estimated_cost_usd"]+usage["unreported_reserved_cost_usd"]+in_flight()[0]
                        reason = ("exploration_round_budget" if exploration_rounds >= config.max_tool_rounds+analysis_round_allowance else
                            "deep_analysis_budget" if aid not in prepared_articles and len(prepared_articles) >= config.max_deep_analysis else
                            "exploration_tool_budget" if total_tools+required_tools > config.max_tool_calls-config.max_finalization_attempts-argument_tool_reserve() else
                            "exploration_time_budget" if deadline-time.monotonic() <= min(config.finalization_reserved_seconds, config.timeout_seconds*.4) else
                            # The reading ends with an analysis call: it must also fit before the reserve.
                            "exploration_time_budget" if not time_left_for(call_estimate(durations, config.timeout_seconds*.2)) else
                            "exploration_token_budget" if used_tokens >= max(0,config.max_tokens-config.finalization_reserved_tokens) else
                            "exploration_cost_budget" if spent >= max(0,config.max_cost_usd-config.finalization_reserved_cost_usd) else None)
                        if reason:
                            reading_budget_reason = reason
                            state["scientific_reading"]["blocked_reason"] = reason
                            if writing or (argumentation_requested or plan_goal) and (reason in {"exploration_round_budget", "deep_analysis_budget"} or
                                    (reason == "exploration_tool_budget" and comparison_batches(state, toolkit.registry))):
                                # Reading allowance spent: compare what is already
                                # documented (partial) instead of skipping comparison. A draft
                                # writes its sections from the works already analysed.
                                reading_stopped = True
                                task = None
                                activity("scientific_reading", "stopped", reason=reason)
                            else:
                                finalize(reason)
                        else:
                            enter_stage("reading")
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
                    if not task and not priority_ids and not writing and recovery_rounds < min(2, config.max_tool_rounds):
                        recovery = recovery_context(state, toolkit.registry)
                        if recovery and (recovery["candidate_ids"] or recovery["unverified_discovered_sources"]):
                            unchecked = [r["article_id"] for r in recovery["blocked_articles"]
                                if r["article_id"] not in toolkit.checked_ids and r["article_id"] not in verification_attempted and not toolkit.registry[r["article_id"]].verified]
                            if unchecked:
                                internal_tool("verify_bibliography", {"article_ids": unchecked[:8]})
                            state["reading_recovery_focus"] = True
                if (phase == "exploration" and argumentation_requested and not goal_pending and not (plan_goal and state.get("writing_planning")) and not state.get("automatic_reading_focus") and not state.get("selection_focus")
                        and not state.get("argumentation_focus") and not state.get("reading_recovery_focus") and not argumentation_failed
                        and argumentation_stage_allowed()
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
                    elif not state.get("turn_goal"):
                        # Degraded mode only: a declared goal never starts a blueprint by
                        # itself (the plan and the sections carry the storyline; the model
                        # can still build one explicitly with build_argument_blueprint).
                        status = blueprint_status(state, toolkit.registry)
                        uncovered = set(active_articles(state, toolkit.registry)) if status["stale"] else set(status["uncovered_article_ids"])
                        blueprint_batches = batches or [active_articles(state, toolkit.registry)[:4]]
                        focus_batch = next((batch for batch in blueprint_batches if set(batch) & uncovered), None)
                        if focus_batch:
                            state["argumentation_focus"] = {"stage": "blueprint", "article_ids": focus_batch,
                                "merge": status["exists"] and not status["stale"]}
                    if state.get("argumentation_focus"):
                        activity("argumentation", "running", stage=state["argumentation_focus"]["stage"])
                if plan_goal and not plan_done and turn_decided and not state.get("writing_planning") and gap_round_due():
                    # One targeted search round for the locks without a retained work (hint below).
                    gap_used = True
                    activity("writing_progress", "gap_round", stage="plan")
                elif (plan_goal and not plan_done and phase == "exploration" and turn_decided and not holding and not (gap_used and last_round_searched and selection_candidates())
                        and not state.get("selection_focus") and not state.get("automatic_reading_focus")
                        and not state.get("argumentation_focus") and not state.get("reading_recovery_focus")
                        and (not automatic_reading or reading_stopped or next_task(state, toolkit.registry, priority_ids,
                            requested_fields, core_only=core_only_now(), skip_depth_ids=reused_cards) is None)):
                    # Search, selection and the affordable readings are done: build the plan.
                    state["writing_planning"] = True
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
                        if not state.get("writing_focus") and PARALLEL_ANALYSES > 1:
                            launch_more()
                        batch = None if state.get("writing_focus") or not time_left_for(2*section_estimate()) else draft_comparison_batch()
                        if batch:
                            # Compared from the cards' proofs before the section is drafted; the saved
                            # relations travel with the section's dossier.
                            key = "|".join(batch)
                            signature = comparison_source_signature(toolkit.registry, batch)
                            receipt = state.setdefault("comparison_reading", {}).get(key, {})
                            if receipt.get("source_signature") != signature:
                                receipt = {"source_signature": signature, "claim_offsets": {}, "complete": False}
                                state["comparison_reading"][key] = receipt
                            state["argumentation_focus"] = {"stage": "comparison", "article_ids": batch,
                                "claim_offsets": receipt["claim_offsets"]}
                            draft_comparisons[0] += 1
                            argument_round_allowance += 2
                            activity("argumentation", "running", stage="comparison", purpose="draft", article_ids=batch)
                            continue
                        if not state.get("writing_focus") and PARALLEL_SECTIONS > 1 and parallel_sections():
                            continue
                        remaining = [resolve_section(state, sid) for sid in progress["target_ids"]
                            if sid not in progress.get("saved_draft_ids", {}) and sid not in progress.get("blocked", {})]
                        states = {s["id"]: section_state(s) for s in remaining}
                        # Fewest refusals first among the writable sections.
                        writable = sorted((s for s in remaining if states[s["id"]] in {"ready", "framing"}),
                            key=lambda s: section_failures.get(s["id"], 0))
                        section = writable[0] if writable else None
                        if not section and remaining and all(v == "blocked" for v in states.values()):
                            # Nothing citable for the remaining sections: each is set aside with its cause.
                            for row in remaining:
                                state["document"]["writing_progress"].setdefault("blocked", {})[row["id"]] = (
                                    "Aucune publication analysée avec preuves exactes ni fait projet citable pour cette section.")
                            refresh_writing(state)
                            raise StopResearch("section_blocked")
                        if (not section and remaining and not speculative and not state.get("automatic_reading_focus")
                                and not reading_stopped and next_task(state, toolkit.registry, priority_ids, requested_fields,
                                    core_only=core_only_now(), skip_depth_ids=reused_cards) is None):
                            # No reading can still start: sources waiting for one are documented limits.
                            reading_stopped = True
                            continue
                        if not section and remaining and speculative and not state.get("automatic_reading_focus"):
                            # The sections still to write wait for analyses already running: their
                            # results are taken as they arrive, without a general round in between
                            # (audit 128698bb: a 0.13 $ general round re-declared the known goal).
                            entry = next((e for e in speculative.values() if e["future"].done()),
                                next(iter(speculative.values())))
                            state["automatic_reading_focus"] = copy.deepcopy(entry["focus"])
                            activity("writing_progress", "awaiting_readings", article_id=entry["focus"]["article_id"],
                                in_flight=len(speculative))
                            continue
                        if not section and progress.get("blocked") and not remaining:
                            # Every other section is saved; only set-aside ones remain.
                            raise StopResearch("section_blocked")
                        if section:
                            task = section_task(section)
                            proposal = tasks.proposal(task)
                            if proposal:
                                apply_section_task(task, proposal)
                                continue
                            state["writing_focus"] = {"section_id": section["id"],
                                "article_ids": section_article_ids(state, toolkit.registry, section),
                                "retrieval_progress": list(claim_receipts.get(section["id"], {}).values())[-8:]}
                            refresh_writing(state)
                            writing_round_allowance = max(writing_round_allowance,
                                len(writing["target_ids"])*3+4)
                selection_due = (config.max_tool_rounds+analysis_round_allowance+argument_round_allowance+writing_round_allowance-exploration_rounds <= 1
                    # A plan or a literature search judges the candidates as soon as a search round
                    # returns them (and once more after the gap round), not at the end of the rounds.
                    or (last_round_searched and (state.get("turn_goal") or {}).get("deliverable") in {"plan", "plan_revision", "literature_search"}
                        and not unsearched_targets()))
                if (phase == "exploration" and (not turn_decided or (gap_used and last_round_searched)) and selection_checkpoints < 2 and toolkit.search_count
                        and not state.get("selection_focus") and not state.get("automatic_reading_focus")
                        and not state.get("argumentation_focus") and not state.get("writing_focus") and not state.get("writing_planning")
                        and selection_due):
                    candidates, total, earlier = selection_pool()
                    if candidates:
                        # The model still judges relevance; the orchestrator only
                        # makes sure the judgment happens while reading is possible.
                        state["selection_focus"] = {"candidate_ids": candidates, "presented_total": total,
                            "earlier_candidate_ids": earlier}
                        selection_checkpoints += 1
                        activity("selection_checkpoint", "running", candidates=len(candidates))
                # In-flight parallel analyses keep their reservation until processed.
                used_tokens = usage["input_tokens"]+usage["output_tokens"]+in_flight()[1]
                spent = usage["estimated_cost_usd"]+usage["unreported_reserved_cost_usd"]+in_flight()[0]
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
                if (phase == "exploration" and plan_goal and not plan_done and state.get("writing_planning")
                        and not state.get("selection_focus") and not plan_parts_tried):
                    plan_parts_tried = True
                    if parallel_plan():
                        answer = plan_answer(state, toolkit.registry, plan_note)
                        break
                if (phase == "exploration" and writing and state.get("writing_focus") and not state.get("automatic_reading_focus")
                        and PARALLEL_ANALYSES > 1):
                    # The sources of the following sections are analysed in the background
                    # while this section is written (same budgets and reservations).
                    launch_more()
                prelaunched = speculative.get((state.get("automatic_reading_focus") or {}).get("article_id")) if phase == "exploration" else None
                # A prepared analysis keeps the exact payload it was launched with.
                inputs = prelaunched["inputs"] if prelaunched else request_inputs()
                if prelaunched:
                    # Its reservation is checked below as this round's own reserve, not twice.
                    spent -= prelaunched["reserve"]
                    used_tokens -= prelaunched["tokens"]+config.max_output_tokens
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
                revision_save_due = bool(focused_section and (writing["mode"] == "section" and
                    exploration_rounds+1 >= config.max_tool_rounds+analysis_round_allowance+argument_round_allowance+writing_round_allowance
                    # After a few rounds without new card claims or saved analyses, the section is
                    # written from what was received (run 2fd068ff: 11 reading rounds, no save).
                    or section_rounds.get(focused_section["section_id"], 0) >= SECTION_READ_ROUNDS
                    # A section's own share of the run (audit 91590987: the synthesis read for six rounds
                    # and used 44 % of the tokens): beyond it, the section is written from what it has.
                    or section_tokens.get(focused_section["section_id"], 0) >= config.max_tokens*SECTION_TOKEN_SHARE
                    or section_calls.get(focused_section["section_id"], 0) >= SECTION_MAX_ROUNDS))
                if focused_section:
                    section_calls[focused_section["section_id"]] = section_calls.get(focused_section["section_id"], 0)+1
                section_progress = False
                if focused_section and phase == "exploration" and deadline-time.monotonic() <= section_estimate():
                    # A section that cannot be saved before the deadline is not started: the
                    # saved sections are kept and the next request resumes from this one.
                    raise StopResearch("exploration_time_budget")
                focused_plan = bool(state.get("writing_planning") and not focused_section and not finalizing)
                focused_selection = (state.get("selection_focus") if not finalizing and not focused_reading and not focused_argument
                    and not focused_section and not focused_plan else None)
                argument_tool = ({"project_facts": "record_project_facts", "comparison": "compare_articles",
                    "blueprint": "build_argument_blueprint"}.get(focused_argument["stage"]) if focused_argument else None)
                tool_defs = [t for t in available_definitions() if t["name"] == "finalize_response"] if finalizing else (
                    [t for t in available_definitions() if t["name"] == "analyze_article"] if focused_reading else
                    phase_tools(argument_tool)
                    if focused_argument else [t for t in available_definitions() if t["name"] == "manage_outline"]
                    if focused_plan else [t for t in available_definitions() if t["name"] in {"write_section", "get_fulltext", "analyze_article", "get_analysis_claims"}]
                    if focused_section else [t for t in available_definitions() if t["name"] == "record_candidate_decisions"]
                    if focused_selection else available_definitions(document_tools))
                if focused_section and not revision_save_due:
                    # Only the tools this section really needs (audit 128698bb: three rounds of
                    # card reads returned no new claim). The packet's retrieval_gaps are computed
                    # locally; claims it carries count as delivered to this section.
                    from .writing_context import section_packet, claim_key
                    packet = section_packet(inputs) or {}
                    gaps = packet.get("retrieval_gaps")
                    if gaps is not None:
                        seen = seen_claims.setdefault(focused_section["section_id"], [])
                        seen[:] = list(dict.fromkeys([*seen, *(claim_key(c) for c in packet.get("proof_backed_claims", []))]))
                        unneeded = set()
                        if not gaps["claims_not_transmitted"]:
                            unneeded.add("get_analysis_claims")
                        if not (gaps["sources_without_card"] or gaps["dimensions_not_examined"] or gaps["abstract_only"]):
                            unneeded |= {"get_fulltext", "analyze_article"}
                        if unneeded:
                            tool_defs = [t for t in tool_defs if t.get("name") not in unneeded]
                            activity("writing_progress", "tools_narrowed", section_id=focused_section["section_id"],
                                omitted=sorted(unneeded), gaps={k: bool(v) for k, v in gaps.items()})
                if not (finalizing or focused_reading or focused_argument or focused_plan or focused_section or focused_selection):
                    # A tool whose input does not exist yet (no plan, no draft, no saved
                    # card) cannot be used this round; it returns with its input.
                    analysed = sum(bool(a.analysis) for a in toolkit.registry.values())
                    has_sections = bool(state.get("outline", {}).get("sections"))
                    has_drafts = any(s.get("drafts") for s in state.get("outline", {}).get("sections", []))
                    unusable = ({"set_turn_goal"} if state.get("turn_goal") else set()) | (
                        # The goal of this message is already declared and applied; a new message
                        # is understood again by its own understanding call.
                        {"write_section", "search_for_section"} if not has_sections else set()) | (
                        {"review_document", "create_scientific_visual"} if not has_drafts else set()) | (
                        {"get_analysis_claims", "identify_research_gaps", "build_argument_blueprint"} if not analysed else set()) | (
                        {"compare_articles"} if analysed < 2 else set())
                    tool_defs = [t for t in tool_defs if t.get("name") not in unusable]
                packet_claims = set()
                if not (finalizing or focused_reading or focused_argument or focused_section or focused_plan or focused_selection):
                    # The targeted publications travel in conversation_science in every round (context.py).
                    # Claims already in this request are not fetched again; when every claim of every
                    # targeted work is in it, get_analysis_claims is not offered (run 8cb4d68a: the same
                    # LDPoly page and HoliTracer pages were fetched three times).
                    from .writing_context import claim_key
                    try:
                        sent = json.loads(inputs[0]["content"].split(" : ", 1)[1])
                    except (ValueError, IndexError, KeyError, TypeError):
                        sent = {}
                    science = sent.get("conversation_science") or {}
                    packet_claims = {claim_key(c) for paper in science.get("papers", []) for value in paper.get("analysis", {}).values()
                        if isinstance(value, list) for c in value if isinstance(c, dict)}
                    coverage = (sent.get("conversation_focus") or {}).get("coverage") or {}
                    pages = request_pages()
                    delivered = set(pages["claims"]) | packet_claims
                    complete = coverage and all(row.get("claims_sent") and not row.get("claims_not_sent") for row in coverage.values())
                    every_claim = coverage and all(key in delivered for aid in coverage for key in card_claim_keys(aid))
                    if complete or every_claim or pages["repeated"]:
                        # Everything the targeted cards hold is in this request or was delivered in it, or
                        # the same page was asked again: answering is due, no further card read.
                        tool_defs = [t for t in tool_defs if t.get("name") != "get_analysis_claims"]
                        activity("references", "cards_in_context", coverage=coverage, repeated_page=pages["repeated"])
                forbidden = forbidden_tools()
                if forbidden and not (finalizing or focused_reading or focused_argument or focused_plan or focused_selection):
                    # The consultant's explicit limits (no search, download, re-analysis or manuscript
                    # change) remove those tools before the request, not after a refused call.
                    tool_defs = [t for t in tool_defs if t.get("name") not in forbidden]
                if (linked and not finalizing and not focused_reading and not focused_selection and not (focused_plan and plan_goal)
                        and not any(t.get("name") == "get_project_context" for t in tool_defs)):
                    tool_defs.extend(t for t in available_definitions() if t["name"] == "get_project_context")
                if revision_save_due:
                    # Use the final existing round for the requested action. The
                    # saved draft still passes every ordinary scientific check.
                    tool_defs = [t for t in tool_defs if t["name"] == "write_section"]
                    tool_defs[0]["parameters"]["properties"]["action"] = {"type": "string", "enum": ["save"]}
                if not finalizing and not state.get("reading_recovery_focus") and not focused_reading and not focused_argument and not focused_plan and not focused_section and not focused_selection and config.web_search_enabled and native_calls < config.max_web_calls and "search_scientific" not in forbidden:
                    tool_defs = [{"type": "web_search", "search_context_size": "low"}, *tool_defs]
                # Task-scoped rules: a focused request carries only its task's guide.
                task_kind = ("final" if finalizing else "reading" if focused_reading else
                    "argumentation" if focused_argument else "section" if focused_section else "planning" if focused_plan else
                    "selection" if focused_selection else "recovery" if state.get("reading_recovery_focus") else "conversation")
                task_instructions = instructions_for(task_kind)
                enter_stage({"final": "final", "reading": "reading", "argumentation": "argumentation", "section": "writing",
                    "planning": "plan", "selection": "selection"}.get(task_kind, "conversation"))
                if linked and not focused_reading:
                    task_instructions += "\n" + LINKED_GUIDANCE
                # The round's own instruction travels at the end of the input (below), so
                # instructions and tools form a prefix identical across rounds of a task.
                input_bound = request_tokens(len(json.dumps(inputs, ensure_ascii=False).encode())+len(task_instructions.encode())
                    +len(json.dumps(tool_defs).encode())+TURN_INSTRUCTION_BYTES)
                web_round = tool_defs[0].get("type") == "web_search"
                reserve = reserve_bound(input_bound, config.max_output_tokens, price, web_calls=int(web_round),
                    web_tokens=config.web_token_reserve if web_round else 0)
                if not finalizing and (spent+reserve+(0 if writing else config.finalization_reserved_cost_usd) > config.max_cost_usd or
                        used_tokens+input_bound+config.max_output_tokens+(0 if writing else config.finalization_reserved_tokens) > config.max_tokens):
                    reason = ("exploration_cost_budget" if spent+reserve+(0 if writing else config.finalization_reserved_cost_usd)
                        > config.max_cost_usd else "exploration_token_budget")
                    # The exact admission, not just « limite atteinte » (audit 91590987: 372 856 used
                    # + 21 108 estimated input + 7 000 output reserve > 400 000).
                    activity("budget", "admission_refused", reason=reason, task=task_kind,
                        section_id=(focused_section or {}).get("section_id"), used_tokens=used_tokens,
                        estimated_input_tokens=input_bound, output_reserve_tokens=config.max_output_tokens,
                        token_cap=config.max_tokens, spent_usd=round(spent, 6), reserve_usd=round(reserve, 6),
                        cost_cap_usd=config.max_cost_usd, reduced_context=context_room[0] is not None)
                    if focused_section and context_room[0] is None:
                        # A smaller section context (fewer claims, same proofs per claim) may fit.
                        context_room[0] = REDUCED_SECTION_CONTEXT_CHARS
                        continue
                    finalize(reason)
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
                    reading_hint(focused_reading)
                    if focused_reading else
                    (("Construire maintenant le plan argumentatif demandé et l'enregistrer avec manage_outline : create s'il n'existe pas, "
                        "replace pour le refondre s'il n'a aucun brouillon, sinon update/add/delete/move. Partir de l'analyse : lock_coverage, "
                        "plan_sources, fiches enregistrées et contexte projet. Pour chaque section : scientific_objective (question traitée), "
                        "lock_ids, article_ids et article_roles (une publication peut servir plusieurs sections), experimental_results "
                        "(méthodes et résultats à exploiter), project_connection (comparaison aux essais du projet ; distinguer faits liés "
                        "au verrou et contexte général), limitations, missing_information (lacunes, verrou sans publication pertinente, "
                        "lectures à approfondir). Couvrir chaque verrou actif ou dire pourquoi. consultant_message : quelques phrases "
                        "naturelles sur les choix, les limites de la recherche et une question utile pour poursuivre. Brouillon non approuvé."
                        if plan_goal else
                        "Créer et enregistrer maintenant un plan scientifique argumenté pour l'état de l'art avec manage_outline(create). "
                        "Ce plan reste un brouillon non approuvé jusqu'à une action authentifiée du consultant.")
                        if focused_plan else
                    (section_hint(focused_section["section_id"], repair=bool(tasks.recovery_exchange(section_task(
                        resolve_section(state, focused_section["section_id"])))),
                        framing=section_state(resolve_section(state, focused_section["section_id"])) == "framing")
                        if focused_section else
                    (f"Record the consultant's exact project observations from user message {focused_argument['source_id']} with record_project_facts. Use exact_quote verbatim, keep project measurements distinct from publication results, and infer no CIR lock."
                        if focused_argument["stage"] == "project_facts" else
                        f"Compare articles {focused_argument['article_ids']} using compare_articles. Save only relations supported by exact evidence from both works; pass an empty relations list if no reliable relation can be established. "
                        "Compare concretely: methods and mechanisms, protocols and datasets, and results. When both works report values on a comparable metric, put them in experimental_results (value, metric, dataset, conditions); otherwise set comparable_protocols=false and say what differs. A demonstrated result is never merged with a comparative inference."
                        if focused_argument["stage"] == "comparison" else
                        f"Build and save the evidence-linked argument blueprint for articles {focused_argument['article_ids']} now. Set merge={str(focused_argument['merge']).lower()}. Cover each of these analyzed articles through its supported role, and connect registered relations across the overlap. Keep project facts separate and do not invent a CIR lock.")
                    if focused_argument else
                    ("Selection now, before the exploration rounds run out: judge the candidates in selection_candidates from their "
                        "abstracts and record_candidate_decisions. Retain works that bring something to the scientific question, reject "
                        "off-topic ones with a short reason, keep uncertain ones pending, and set finalist_ids to the best retained works in "
                        "order (pending works are not finalists). Judge every candidate whose abstract you can assess, including those "
                        "collected_in_earlier_message. Rank by explicit criteria, not shared keywords: fit to the problem, relevance of "
                        "the method, results actually reported and their comparability with the project, quality and limits of the "
                        "evidence; set contribution (direct, related, foundational). A foundational method from another application "
                        "domain can be retained; discovery (queries, providers, citations) is context, never a score."
                        + (" Linked project: give each decision the lock_ids of the active locks it addresses; choose finalists so that "
                            "every active lock with a relevant work is represented, and leave a lock without relevant candidate visible "
                            "(lock_coverage) rather than filling it." if linked else "")
                        if focused_selection else
                    (("Selection review: before long readings, inspect the unreviewed candidates of this message or the finalists "
                        "without known full text listed by record_candidate_decisions if they may matter, then confirm or revise "
                        "the finalists. Do not publish yet. " if holding else "") +
                    f"Exploration rounds left after this request: {config.max_tool_rounds+analysis_round_allowance+argument_round_allowance+writing_round_allowance-exploration_rounds}. "
                    "Publication has a separate reserved budget. Select scientifically useful works across the research axes "
                    "with record_candidate_decisions as soon as abstracts allow it; priority full texts will then be read and analyzed "
                    "automatically before writing." + ("" if state.get("turn_goal") else
                    " First understand this message in context and record it with set_turn_goal, in parallel with your first tools "
                    "(not needed for a direct conversational answer).") + uncovered_hint())))))
                activity("responses", "running", round=rounds, phase=phase, attempt=attempts+1 if finalizing else None,
                    input_chars=len(json.dumps(inputs, ensure_ascii=False)),
                    **({"proofs_sent": len(transmitted_evidence_ids(inputs, toolkit.evidence)), "section_id": focused_section["section_id"]}
                        if focused_section else {}),
                    offered_article_ids=list(dict.fromkeys(aid for w in toolkit.store.windows if w["window_id"] in window_ids(inputs) for aid in w["article_ids"])),
                    offered_abstract_ids=list(abstract_deliveries(inputs)), abstract_deliveries=abstract_deliveries(inputs))
                refs = (state.get("turn_goal") or {}).get("references") or {}
                if not (focused_reading or focused_argument or focused_section or focused_plan or focused_selection):
                    if refs.get("ambiguous"):
                        hint += (" Références ambiguës (conversation_focus.clarification_needed) : demander au consultant de "
                            "préciser les publications visées avec finalize_response, sans lecture ni analyse.")
                    elif refs.get("article_ids"):
                        hint += (f" Publications visées par cette demande, dans l'ordre du dialogue : {refs['article_ids']} "
                            f"(périmètre {refs.get('scope')}). Répondre sur elles à partir de leurs fiches et preuves "
                            "(get_analysis_claims) ; ne pas les remplacer par une liste affichée plus ancienne"
                            + (" ni lire d'autres travaux." if refs.get("scope") == "exclusive" else "."))
                if revision_save_due:
                    hint += (" Dernier tour disponible : sauvegarder maintenant la section révisée avec write_section(save). "
                        if writing["mode"] == "section" else
                        " Sauvegarder maintenant cette section avec write_section(save), à partir des fiches, preuves et faits "
                        "projet déjà reçus ; ne citer que des publications dont la fiche est enregistrée. ") + \
                        "N'inventer aucun détail manquant ; signaler les points encore non vérifiés dans le texte."
                if focused_section and writing["mode"] == "section":
                    hint += " Réviser le texte exact de section_drafting.packet.revision.raw_text lorsqu'il existe, " \
                        "en conservant ses informations étayées. retrieval_progress indique les pages déjà reçues : " \
                        "ne pas recommencer une lecture complète des fiches. Les alertes des autres sections sont hors de cette révision."
                stream, response, upstream_started = None, None, False
                call_started = time.monotonic()
                entry, call_seconds = None, None
                if focused_reading and phase == "exploration" and PARALLEL_ANALYSES > 1:
                    # This analysis runs in the background (or already does); the next
                    # independent readings are prepared and started while it runs. It
                    # stays registered (and reserved) until processed here.
                    entry = speculative.get(focused_reading["article_id"]) or launch(focused_reading, inputs)
                    launch_more()
                active_task = entry["task"] if entry else (section_task(resolve_section(state, focused_section["section_id"]))
                    if focused_section else tasks.task(task_kind, str(rounds), fingerprint(inputs)))
                active_attempt = entry["attempt"] if entry else tasks.begin(active_task,
                    evidence_ids=transmitted_evidence_ids(inputs, toolkit.evidence), input_signature=fingerprint(inputs), reserve=reserve)
                try:
                    if entry is not None:
                        response, call_seconds = wait_entry(entry)
                        speculative.pop(focused_reading["article_id"], None)
                    if response is not None:
                        pass
                    elif focused_reading:
                        if active_attempt["accounted"]:
                            active_attempt = tasks.begin(active_task,
                                evidence_ids=transmitted_evidence_ids(inputs, toolkit.evidence), input_signature=fingerprint(inputs), reserve=reserve)
                        # Synchronous path (parallel disabled, or one retry after a failed background call).
                        upstream_started = True
                        stream = client.with_options(timeout=min(config.request_timeout_seconds, remaining)).responses.create(
                            **label(reading_kwargs(inputs, focused_reading), active_task, active_attempt))
                    else:
                      upstream_started = True
                      stream = client.with_options(timeout=min(config.request_timeout_seconds, remaining)).responses.create(
                        **label({}, active_task, active_attempt, "section_repair" if focused_section and tasks.recovery_exchange(
                            active_task) else task_kind), instructions=task_instructions, **cache_key(task_kind),
                        input=[*json.loads(json.dumps(inputs)), turn_instruction(hint)], tools=tool_defs, store=False, stream=True,
                        reasoning={"effort": config.reasoning_effort}, max_output_tokens=config.max_output_tokens,
                        max_tool_calls=1, tool_choice={"type": "function", "name": "finalize_response"} if finalizing else
                            "required" if linked and (focused_argument or (focused_plan and not plan_goal)) else
                            {"type": "function", "name": "analyze_article"} if focused_reading else
                            {"type": "function", "name": argument_tool}
                                if focused_argument else {"type": "function", "name": "manage_outline"}
                                if focused_plan else {"type": "function", "name": "record_candidate_decisions"}
                                if focused_selection else {"type": "function", "name": "write_section"}
                                if revision_save_due else "auto",
                        parallel_tool_calls=not (finalizing or focused_reading or focused_argument or focused_selection),
                        include=["reasoning.encrypted_content", "web_search_call.action.sources"])
                    for event in stream or ():
                        check()
                        if event.type == "response.web_search_call.in_progress":
                            activity("web_search", "running")
                        elif event.type in {"response.completed", "response.incomplete", "response.failed"}:
                            response = event.response
                        elif event.type == "error":
                            raise RuntimeError("ResponsesStreamError")
                    if response is None:
                        raise RuntimeError("ResponsesMissingCompletion")
                except Exception as exc:
                    if upstream_started:
                        tasks.receive(active_task, active_attempt, response, seconds=time.monotonic()-call_started,
                            reserve=reserve, error=exc)
                        task_checkpoint(active_task)
                    raise
                finally:
                    if stream is not None:
                        stream.close()
                raw = dump(response.usage) if getattr(response, "usage", None) else {}
                inp, out = raw.get("input_tokens", 0), raw.get("output_tokens", 0)
                if focused_section:
                    section_tokens[focused_section["section_id"]] = section_tokens.get(focused_section["section_id"], 0)+inp+out
                cached = (raw.get("input_tokens_details") or {}).get("cached_tokens", 0) or 0
                reasoning = (raw.get("output_tokens_details") or {}).get("reasoning_tokens", 0) or 0
                outputs = [dump(item) for item in response.output]
                web_count = sum(item.get("type") == "web_search_call" for item in outputs)
                native_calls += web_count; total_tools += web_count
                counts["web_search"] = counts.get("web_search", 0)+web_count
                elapsed = call_seconds if call_seconds is not None else time.monotonic()-call_started
                tasks.receive(active_task, active_attempt, response, seconds=elapsed, reserve=reserve, web_calls=web_count)
                if getattr(response, "status", "completed") != "completed":
                    task_checkpoint(active_task)
                    error = getattr(response,"error",None)
                    upstream_code = getattr(error,"code",None) or (error.get("code") if isinstance(error,dict) else None)
                    activity("responses", "failed", phase=phase, upstream_error_code=upstream_code or "unknown")
                    raise StopResearch("output_limit" if getattr(response, "status", "completed") == "incomplete" else "upstream_error")
                toolkit.delivered(window_ids(inputs), inputs=inputs)
                task_checkpoint(active_task)
                activity("responses", "completed", phase=phase, input_tokens=inp, output_tokens=out,
                    cached_input_tokens=cached, reasoning_tokens=reasoning, instructions_chars=len(task_instructions),
                    tools_chars=len(json.dumps(tool_defs)), estimated_cost_usd=usage["estimated_cost_usd"],
                    call_seconds=round(elapsed, 1), task=task_kind)
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
                            if focused_argument["stage"] == "comparison":
                                # Not scheduled again in this request (draft comparisons included).
                                compared_batches.add(tuple(focused_argument["article_ids"]))
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
                last_round_searched = any(c["name"] == "search_scientific" for c in calls)
                search_args = []
                for c in calls:
                    if c["name"] == "search_scientific":
                        try:
                            search_args.append(json.loads(c["arguments"]))
                        except ValueError:
                            pass
                if len(search_args) >= 2 and not finalizing:
                    # Provider requests of this round's searches run concurrently.
                    toolkit.prefetch_searches(search_args, deadline)
                for call in calls:
                    try:
                        local_call = call["name"] in {"analyze_article", "write_section"} and                             json.loads(call["arguments"]).get("action") == "save"
                    except (ValueError, TypeError, AttributeError):
                        local_call = False
                    if not local_call:
                        # A received save is validated locally even past the deadline (above).
                        check()
                    name = call["name"]
                    if toolkit.finished:
                        result = {"published": True, "guidance": "Response already published."}
                    elif total_tools >= tool_limit() or (name != "finalize_response" and total_tools >= max(0, tool_limit()-(0 if writing else config.max_finalization_attempts))
                            # The plan replaces the publication: its selection and save use the publication's reserve.
                            and not (plan_goal and name in {"manage_outline", "record_candidate_decisions"} and (planning_forced or plan_checkpoint))):
                        result = {"error": "Exploration budget reached; publish the stored results."}
                        finalize("exploration_tool_budget")
                    elif name == "finalize_response" and attempts >= config.max_finalization_attempts:
                        result = {"published": False, "rejection_code": "attempt_budget", "error": "Finalization attempts exhausted."}
                    elif name == "finalize_response" and plan_goal and not plan_done and not planning_forced and not finalizing:
                        # The consultant asked for a plan: it is saved, then shown with
                        # the lock coverage, instead of a synthesis of the search.
                        total_tools += 1
                        counts[name] = counts.get(name, 0)+1
                        result = {"published": False, "deferred": True, "error": "Plan demandé : l'enregistrer d'abord avec "
                            "manage_outline ; il sera présenté au consultant avec la couverture des verrous."}
                        activity(name, "deferred", reason="plan_pending")
                        finalize("plan_requested")
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
                            project_retrieval = linked and name == "get_project_context"
                            if focused_reading and not project_retrieval and (name != "analyze_article" or arguments.get("action") not in {"read", "save"} or
                                    arguments.get("article_ids") != [focused_reading["article_id"]]):
                                raise ValueError("Lire ou enregistrer uniquement l'article scientifique en cours.")
                            if focused_argument and not project_retrieval and (name != argument_tool or
                                    (focused_argument["stage"] == "project_facts" and
                                        any(fact.get("source_id") != focused_argument["source_id"] for fact in arguments.get("facts", []))) or
                                    (focused_argument["stage"] == "comparison" and
                                        (arguments.get("article_ids") != focused_argument["article_ids"] or arguments.get("relations") is None)) or
                                    (focused_argument["stage"] == "blueprint" and
                                        (arguments.get("action") != "save" or
                                            bool(arguments.get("merge")) != focused_argument["merge"]))):
                                raise ValueError("Enregistrer maintenant la comparaison ou le blueprint ciblé, avec des preuves exactes.")
                            if focused_plan and not project_retrieval and (name != "manage_outline" or (arguments.get("action") == "read"
                                    if plan_goal else arguments.get("action") != "create")):
                                raise ValueError("Créer et sauvegarder le plan avant la rédaction.")
                            if focused_selection and not project_retrieval and state.get("selection_focus") and name != "record_candidate_decisions":
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
                            # Project facts stay retrievable while drafting (run 2fd068ff: 11 refused calls).
                            if focused_section and not project_retrieval and (name not in {"write_section", "get_fulltext", "analyze_article", "get_analysis_claims"} or
                                    (name == "write_section" and (arguments.get("action") not in {"read", "save"} or
                                    arguments.get("section_id") != focused_section["section_id"]))):
                                raise ValueError("Sauvegarder uniquement la section en cours ou lire sa source ciblée.")
                            activity(name, "running", action=arguments.get("action"), query=arguments.get("query"), family=arguments.get("family"),
                                **({"article_id": arguments.get("article_id"), "fields": arguments.get("fields"),
                                    "offset": arguments.get("offset"), "section_id": focused_section["section_id"] if focused_section else None}
                                    if name == "get_analysis_claims" else {}))
                            # Saving a card or a section from a response already received is local
                            # validation (every check applies, no provider request): it is not lost
                            # when the deadline passes between reception and save, as in the
                            # background path. Reads and downloads keep the deadline check.
                            local_save = name in {"analyze_article", "write_section"} and arguments.get("action") == "save"
                            if focused_section and name == "write_section" and arguments.get("action") == "save":
                                arguments = repair_merge(active_task, arguments)
                                active_attempt["arguments"] = copy.deepcopy(arguments)
                                result = toolkit.execute(name, arguments, deadline, settle=True, operation_id=active_attempt["id"])
                            else:
                                result = toolkit.execute(name, arguments, deadline, settle=local_save)
                            if name == "search_scientific" and result.get("refused") == "new_candidate_limit":
                                # Refused before any provider call: it does not spend the tool budget.
                                total_tools -= 1
                            if name == "set_turn_goal" and result.get("saved"):
                                apply_goal(result["goal"])
                            if plan_goal and name == "manage_outline" and arguments.get("action") != "read" and result.get("saved"):
                                plan_done = True
                                plan_note = arguments.get("consultant_message") or plan_note
                                state.pop("writing_planning", None)
                            if focused_section and name == "analyze_article" and result.get("saved"):
                                section_progress = True
                            if focused_section and name == "get_analysis_claims":
                                seen = seen_claims.setdefault(focused_section["section_id"], [])
                                fresh = [fingerprint([c.get("statement"), sorted(c.get("evidence_ids", []))])
                                    for c in result.get("claims", [])]
                                if set(fresh)-set(seen):
                                    section_progress = True
                                # Only what this section has not received yet travels again (delta read).
                                known = set(seen)
                                kept = [c for c, key in zip(result.get("claims", []), fresh) if key not in known]
                                if not kept and result.get("claims"):
                                    # Known locally: nothing new on this page (deterministic answer).
                                    result = {"article_id": result["article_id"], "no_new_evidence": True,
                                        "already_delivered_claims": len(result["claims"]), "next_offset": result["next_offset"],
                                        "complete": result["complete"], "guidance": "Ces affirmations ont déjà été transmises à "
                                        "cette section (paquet ou lecture précédente). Consulter retrieval_gaps : s'il est vide, "
                                        "rédiger et sauvegarder maintenant."}
                                elif len(kept) < len(result.get("claims", [])):
                                    needed = {e for c in kept for e in c.get("evidence_ids", [])}
                                    result = {**result, "claims": kept, "already_received_claims": len(result["claims"])-len(kept),
                                        "evidence": [e for e in result.get("evidence", []) if e.get("evidence_id") in needed]}
                                seen[:] = list(dict.fromkeys([*seen, *fresh]))
                            if not focused_section and name == "get_analysis_claims":
                                # Pages of this request: the same page twice returns nothing new (run 8cb4d68a
                                # fetched LDPoly page 0 and HoliTracer pages 0 and 22 three times).
                                from .writing_context import claim_key
                                pages = request_pages()
                                page = fingerprint([toolkit.store.article(arguments.get("article_id")).id, arguments.get("offset", 0),
                                    sorted(arguments.get("fields") or [])])
                                if page in pages["keys"]:
                                    pages["repeated"] = True
                                    result = {"article_id": result["article_id"], "no_new_evidence": True,
                                        "page_already_delivered": True, "next_offset": result["next_offset"],
                                        "complete": result["complete"], "guidance": "Cette page a déjà été fournie dans cette "
                                        "requête et ses affirmations sont dans conversation_science ou dans la réponse précédente : "
                                        "répondre maintenant à partir des preuves disponibles et signaler ce qui manque."}
                                else:
                                    pages["keys"].append(page)
                                    pages["claims"].extend(claim_key(c) for c in result.get("claims", []))
                            if not focused_section and name == "get_analysis_claims" and packet_claims and result.get("claims"):
                                kept = [c for c in result.get("claims", []) if claim_key(c) not in packet_claims]
                                if result.get("claims") and not kept:
                                    result = {"article_id": result["article_id"], "no_new_evidence": True,
                                        "already_in_request": len(result["claims"]), "next_offset": result["next_offset"],
                                        "complete": result["complete"], "guidance": "Ces affirmations et leurs preuves sont déjà "
                                        "dans conversation_science de cette requête : répondre à partir de ce paquet."}
                                elif len(kept) < len(result.get("claims", [])):
                                    needed = {e for c in kept for e in c.get("evidence_ids", [])}
                                    result = {**result, "claims": kept, "already_in_request": len(result["claims"])-len(kept),
                                        "evidence": [e for e in result.get("evidence", []) if e.get("evidence_id") in needed]}
                            if focused_section and name == "get_project_context":
                                # Durable across runs of the same diagnostic snapshot (resumptions).
                                request_key = fingerprint([focused_section["section_id"], arguments,
                                    (state.get("diagnostic_link") or {}).get("snapshot", {}).get("source_version")])
                                project_requests = state.setdefault("section_project_requests", [])
                                if request_key in project_requests:
                                    result = {"already_received": True, "guidance": "Même requête projet déjà reçue pour cette "
                                        "section : utiliser la réponse précédente ou diagnostic_context."}
                                if request_key not in project_requests:
                                    project_requests.append(request_key)
                            if focused_section and name == "get_analysis_claims":
                                claim_reads[claim_key] = claim_reads.get(claim_key, 0)+1
                                claim_receipts.setdefault(focused_section["section_id"], {})[fingerprint(claim_key)] = {
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
                                    result = section_refusal(arguments, result)
                                    tasks.reject(active_task, active_attempt, result)
                                    writing_failure(focused_section["section_id"], result)
                                else:
                                    confirmed_save(state, focused_section["section_id"], result["draft_id"], result["text"])
                                    tasks.saved(active_task, active_attempt,
                                        {k: v for k, v in result.items() if k != "text"}, arguments)
                                    state.pop("writing_focus", None)
                                    writing_attempts = 0
                                    blocked_since_save[0] = 0
                                    activity("writing_progress", "completed", section_id=focused_section["section_id"],
                                        draft_id=result["draft_id"], document_version_id=result["document_version_id"])
                            if name == "get_fulltext" and automatic_reading:
                                reading_tasks(state, toolkit.registry, requested_fields, priority_ids, scope_ids=reading_scope[0])
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
                            if name == "record_candidate_decisions" and result.get("saved") and (automatic_reading or writing or
                                    (detail_request and not state.get("turn_goal"))):
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
                                # Without a declared goal, the request's wording still decides.
                                argumentation_requested = argumentation_requested or (not state.get("turn_goal") and bool(re.search(
                                    r"état\s+de\s+l[’']art|compar|plan|rédig|redig|rédac", request_text, re.I)))
                                ledger = reading_tasks(state, toolkit.registry, requested_fields, priority_ids, scope_ids=reading_scope[0])
                                analysis_round_allowance = max(analysis_round_allowance,
                                    reading_allowance(state, config.max_deep_analysis))
                            if focused_reading and result.get("saved"):
                                reading_completed(state, toolkit.store.article(focused_reading["article_id"]),
                                    result.get("reviewed_evidence_ids", []), result.get("reviewed_fields", []))
                                if writing and reading_scope[0] is not None:
                                    # A draft reads the works its sections cannot cite yet: a work with a
                                    # card leaves the reading scope (one window each, sections first).
                                    reading_scope[0] = writing_sources()
                                    reading_tasks(state, toolkit.registry, requested_fields, priority_ids, scope_ids=reading_scope[0])
                            if focused_argument and not project_retrieval:
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
                                    # While drafting, one prioritized window per batch: the section goes on.
                                    windows_done = more and receipt["windows"] >= (1 if writing else ARGUMENT_WINDOWS)
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
                                    output_chars=len(json.dumps(result, ensure_ascii=False, default=str)),
                                    article_id=arguments.get("article_id"), refused=result.get("refused"), empty_providers=result.get("empty_providers"),
                                    **({"offset": result["offset"], "next_offset": result["next_offset"], "complete": result["complete"],
                                        "claims_returned": len(result["claims"])} if name == "get_analysis_claims" else {}))
                                if focused_reading and result.get("saved"):
                                    tasks.saved(active_task, active_attempt, {"saved": True,
                                        "artifact_id": focused_reading["article_id"], "artifact_kind": "analysis",
                                        "source_version": toolkit.registry[focused_reading["article_id"]].source_version}, arguments)
                                    activity("scientific_reading", "completed", article_id=focused_reading["article_id"],
                                        reading_status=next((t["status"] for t in state["scientific_reading"]["tasks"] if t["article_id"] == focused_reading["article_id"]), "partial"))
                        except StopResearch:
                            raise
                        except Exception as exc:
                            from pydantic import ValidationError
                            message = str(exc) if isinstance(exc, (ValidationError, ValueError)) else "Tool unavailable; use validated stored data."
                            result = {"error": message, "error_type": type(exc).__name__}
                            activity(name, "failed", error_type=type(exc).__name__, error=message)
                            if focused_reading and name != "get_project_context":
                                task = next((item for item in state["scientific_reading"]["tasks"]
                                    if item["article_id"] == focused_reading["article_id"]), None)
                                if task:
                                    task["attempts"] += 1
                                    if task["attempts"] >= 2:
                                        task["status"] = "needs_review"
                                        state.pop("automatic_reading_focus", None)
                                    update_coverage(state)
                                    activity("scientific_reading", "rejected", article_id=task["article_id"])
                            if focused_argument and name != "get_project_context":
                                argumentation_attempts += 1
                                if argumentation_attempts >= 2:
                                    argumentation_failed = True
                                    state.pop("argumentation_focus", None)
                                    activity("argumentation", "needs_review", stage=focused_argument["stage"])
                                    if focused_argument["stage"] == "comparison":
                                        # Not scheduled again in this request (draft comparisons included).
                                        compared_batches.add(tuple(focused_argument["article_ids"]))
                            if name == "finalize_response":
                                state.setdefault("finalization_attempts", []).append({"published": False, "rejection_code": "validation_arguments", "error": message})
                                finalize("publication_refused")
                            if focused_section and name == "write_section" and arguments.get("action") != "read":
                                result = section_refusal(arguments, result)
                                tasks.reject(active_task, active_attempt, result)
                                writing_failure(focused_section["section_id"], result)
                            if focused_plan and name != "get_project_context":
                                planning_attempts += 1
                                if planning_attempts >= 2:
                                    raise StopResearch("plan_not_saved")
                            if focused_selection and name != "get_project_context":
                                # The refusal reaches the model; one more checkpoint may follow.
                                state.pop("selection_focus", None)
                    exchange.append({"type": "function_call_output", "call_id": call["call_id"], "output": json.dumps(
                        {**result, "text": result["text"][:400]+("…" if len(result["text"])>400 else "")}
                        if name == "write_section" and result.get("saved") else result, ensure_ascii=False)})
                if focused_section and not section_progress and (state.get("writing_focus") or {}).get("section_id") == focused_section["section_id"]:
                    section_rounds[focused_section["section_id"]] = section_rounds.get(focused_section["section_id"], 0)+1
                if toolkit.finished:
                    final_result = toolkit.finished
                    answer = final_result["answer"]
                elif plan_done and not answer:
                    answer = plan_answer(state, toolkit.registry, plan_note)
        except ActiveReadingContextError:
            stop_reason, partial = "evidence_transmission_blocked", True
            activity("scientific_reading", "blocked", reason=stop_reason,
                article_id=state.get("automatic_reading_focus", {}).get("article_id"))
        except StopResearch as exc:
            stop_reason, partial = exc.reason, True
            budget_refusal = getattr(exc, "explanation", None)
            activity("research", "stopped", reason=stop_reason, **({"admission": budget_refusal} if budget_refusal else {}))
        except Exception as exc:
            partial, stop_reason = True, "upstream_error"
            status = getattr(exc, "status_code", None)
            if status in {400, 401, 403, 404}: stop_reason = "configuration_error"
            elif status == 429: stop_reason = "quota_or_rate_limit"
            activity("responses", "failed", error_type=type(exc).__name__, http_status=status)
        harvest_parallel()
        if plan_tasks:
            # The stop-time harvest may have received another completed sibling.
            # Render/completeness must follow the last saved artifacts, not the
            # assembly snapshot taken before that harvest.
            assemble_plan()
        enter_stage("end")
        state.pop("selection_focus", None)
        if plan_goal:
            state.pop("writing_planning", None)
        if plan_done and not writing:
            # The saved plan answers the request even if a later step stopped; its
            # gaps and the lock coverage are stated in the answer itself.
            answer = answer or plan_answer(state, toolkit.registry, plan_note)
            partial = plan_incomplete
            if partial:
                stop_reason = stop_reason or "plan_parts_incomplete"
            final_result = {"answer": answer, "coverage_sufficient": False, "fallback": False}
        elif reading_budget_reason:
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
            partial = refresh_writing(state, final=True)["status"] != "draft_complete"
            final_result = {"answer": answer, "coverage_sufficient": False, "fallback": partial}
        if plan_goal and not plan_done and not writing:
            # A plan request always ends with a usable plan: recorded publications,
            # candidates labelled as not evaluated, and gaps. Nothing is invented.
            answer = provisional_plan(state, toolkit.registry, provisional_reason or stop_reason or "time")
            partial = True
            stop_reason = stop_reason or "plan_time"
            final_result = {"answer": answer, "coverage_sufficient": False, "fallback": False, "provisional_plan": True}
        if not answer:
            final_result = fallback(toolkit, stop_reason or "finalization_failed")
            answer, partial = final_result["answer"], True
            if stop_reason == "configuration_error":
                answer += f"\n\nConfiguration OpenAI : vérifier l’accès à {config.model}, OPENAI_API_KEY et les outils compatibles. Aucun modèle de remplacement n’a été utilisé."
            toolkit.save()
            state["last_response"] = {k: v for k, v in final_result.items() if k != "answer"}
        if stop_reason == "budget_refused":
            # No request was sent for the refused call; everything saved before is kept.
            refusal = budget_refusal or {}
            answer += ("\n\nBudget : " + refusal.get("message", "un budget configuré ne peut pas couvrir l'appel suivant.")
                + " Les sections, fiches et preuves déjà sauvegardées sont conservées ; la tâche reprendra sur demande "
                "une fois le budget ajusté par un administrateur.")
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
            "discussed_article_ids": final_result.get("discussed_article_ids", primary),
            "references": (state.get("turn_goal") or {}).get("references") or state.get("conversation_references"),
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
            "tool_counts": counts, "tool_calls": total_tools, "rounds": rounds, "understanding_calls": understanding_calls[0],
            "exploration_rounds": exploration_rounds, "finalization_rounds": finalization_rounds, "finalization_attempt_count": attempts,
            "finalization_refusals": [{k: a.get(k) for k in ("rejection_code", "error", "phase")} for a in state.get("finalization_attempts", []) if not any(a is old for old in turn_existing_attempts) and a.get("error")],
            "web_searches": native_calls, "scientific_searches": toolkit.search_count,
            # Measured per run: model time (sum of calls) and wall time, to verify parallel gains.
            "timings": {"llm_calls": len(durations), "llm_seconds": round(sum(d for d, _ in durations), 1),
                "wall_seconds": round(time.monotonic()-started, 1), "parallel_analyses": parallel_stats["launched"],
                "max_concurrent_analyses": parallel_stats["max_concurrent"], "stages": dict(stage_times)},
            "usage": usage, "duration_seconds": round(time.monotonic()-started, 3)}
