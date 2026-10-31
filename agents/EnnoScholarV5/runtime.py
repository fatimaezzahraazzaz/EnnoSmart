"""Responses -> LangGraph decision -> existing scientific tool -> durable receipt.

No call to original.run or ScholarChatAgent.run; no LLM supervisor. Sol has the
complete V4 scientific toolkit, including free decisions about reading/writing.
"""
from __future__ import annotations
import copy
import json
import time
from datetime import datetime, timezone
from openai import OpenAI
from agents.EnnoScholarV4.finance import MeteredClient, BudgetRefused
from agents.EnnoScholarV4.pricing import resolve, cost
from experiments.ennoscholar_v5.adapter import Decision, V4Adapter
from .toolkit import ScholarTools, definitions, TOOLS
from .original import model_context, compact_exchange, window_ids, SYSTEM, DISCOVERY_GUIDANCE
from .prompts import SYSTEM as COMPLETE_SCIENTIFIC_GUIDE
from .sources import ScientificSources
from .stops import StopResearch
from experiments.ennoscholar_v5.progress import synchronize_scope


class _TrackedClient:
    """Instrument the existing metered Responses path without changing V4."""
    def __init__(self, client, meter):
        self.client, self.meter = client, meter

    def with_options(self, **options):
        return _TrackedClient(self.client.with_options(**options), self.meter)

    @property
    def responses(self):
        return self

    def create(self, **kwargs):
        self.meter.mark_sent(self.meter.current_target)
        return self.client.responses.create(**kwargs)

class ResponsesSol:
    langgraph_live = True

    def __init__(self, config, meter, *, client=None, emit=lambda event: None, cancelled=lambda: False):
        self.config, self.meter, self.emit, self.cancelled = config, meter, emit, cancelled
        sdk = client or OpenAI(api_key=config.api_key, timeout=config.request_timeout_seconds, max_retries=0)
        self.client = MeteredClient(_TrackedClient(sdk, meter), config, meter=meter)

    def reservation(self):
        # Real admission is performed by MeteredClient against the durable meter
        # with the exact assembled request, immediately before sending it.
        return {"cost_usd": 0., "tokens": 0}

    def bind(self, session, state, opid):
        self.session, self.state, self.opid = session, state, opid
        self.meter.current_target = opid

    def activity(self, status, **fields):
        self.emit({"tool": "responses", "status": status, "at": datetime.now(timezone.utc).isoformat(), **fields})

    def choose(self, inputs, tools, progress, *, instructions=""):
        before = self.meter.snapshot()
        if self.cancelled():
            return Decision("pause", {"reason": "cancelled"}, sent_calls=0, response_received=False)
        # A human-authorised resume is a new attempt of the same metered run.
        # Its current outcome must not be masked by a previous provider error.
        # The old run activity/messages remain the durable history.
        self.session.pop('v5_provider_failure', None)
        seconds = min(before.get('remaining_seconds', self.config.timeout_seconds-before["seconds"]), self.config.request_timeout_seconds)
        if seconds <= 0:
            return Decision("pause", {"reason": "job_budget"}, sent_calls=0, response_received=False)
        goal = self.session.get('turn_goal', {})
        from .call_context import profile
        context = profile(self.session)
        constraints = set((goal.get("references") or {}).get("constraints") or [])
        constraints.update(self.session.get("conversation_references", {}).get("constraints") or [])
        web = not ({"no_search", "no_download"} & constraints) and self.config.web_search_enabled and before["web"] < before["limits"]["web"] and before["tools"] < before["limits"]["tools"]
        if goal.get('search_needed') is False:
            web = False
        if context['kind'] != 'science':
            web = False
        self.meter.web_request = web
        selected_tools = ([{"type": "web_search", "search_context_size": "low"}] if web else [])+tools
        from . import workflow
        drafting = workflow.writing_active(self.session)
        if drafting:
            # Sections are published one by one with write_section; the manuscript
            # is assembled locally, so no free-text synthesis is forced and the
            # synthesis reserve is not held back from the remaining sections.
            self.session.pop("v5_unvalidated_draft", None)
        force_final = bool(not drafting and (self.session.get("v5_unvalidated_draft") or self.state.get('finalizing')))
        # While drafting, one call is kept for saving the current section (run
        # b748605d spent its budget on readings, then could not save). Reaching it
        # turns the next call into that save: write_section only.
        saving = bool(drafting and self.state.get('finalizing'))
        self.meter.protect_finalization = context['kind'] == 'science' and not force_final and not saving
        self.meter.save_reserve = drafting and not saving
        final_tools = {"finalize_response"}
        if force_final and workflow.plan_required(self.session):
            final_tools.add("manage_outline")  # A plan is saved before it is presented.
        if force_final and ((self.session.get("turn_goal") or {}).get("deliverable") == "comparison"
                or workflow.comparison_required(self.session)):
            # Relations (or an explicit no-relation assessment) are saved before a comparison is
            # presented; the compared works may only be known from the final citations (D1).
            final_tools.add("compare_articles")
        if force_final:
            selected_tools = [t for t in tools if t.get("name") in final_tools]
            self.meter.web_request = False
        if saving:
            final_tools = {"write_section"}
            selected_tools = [t for t in tools if t.get("name") == "write_section"]
            self.meter.web_request = False
        stream, response, error, local_refusal = None, None, None, False
        self.activity("running", engine="langgraph_v5", sequence=self.state["sequence"])
        try:
            request = dict(
                model="gpt-6.1-sol", instructions=instructions, input=copy.deepcopy(inputs),
                tools=selected_tools, store=False, stream=True, reasoning={"effort": self.config.reasoning_effort},
                max_output_tokens=min(self.config.max_output_tokens, context.get('max_output_tokens', self.config.max_output_tokens)), max_tool_calls=1,
                # As V4: several function calls per response, except a forced synthesis.
                # max_tool_calls bounds hosted Web calls only.
                parallel_tool_calls=not (force_final or saving) or len(final_tools) > 1,
                tool_choice=({"type": "function", "name": "write_section"} if saving else "auto" if not force_final else
                    "required" if len(final_tools) > 1 else {"type": "function", "name": "finalize_response"}),
                include=["reasoning.encrypted_content", "web_search_call.action.sources"],
                _ennoscholar_task={"kind": "v5_science", "stable_target": self.opid, "attempt_id": self.opid})
            try:
                from .token_estimation import estimate
                self.meter.request_metrics = {**estimate(request), 'task':context['kind'],
                    'phase':'section_save' if saving else 'finalization' if force_final else 'decision',
                    'tools':[t.get('name') or t.get('type') for t in request['tools']]}
                stream = self.client.with_options(timeout=max(.001, seconds)).responses.create(**request)
            except BudgetRefused as exc:
                # A local refusal has sent nothing. Try the same scientific
                # decision without hosted Web's upper bound, never retry transport.
                if not self.meter.web_request or exc.explanation.get('refused_budget') not in {'v5_global','v5_synthesis'}:
                    raise
                self.activity('admission_refused', detail=exc.explanation, retry='without_native_web')
                self.meter.web_request = False
                request['tools'] = [t for t in selected_tools if t.get('type') != 'web_search']
                self.meter.request_metrics = {**self.meter.request_metrics, **estimate(request)}
                stream = self.client.with_options(timeout=max(.001, seconds)).responses.create(**request)
            for event in stream:
                if self.cancelled():
                    raise StopResearch("cancelled")
                if self.meter.snapshot().get('remaining_seconds', 1) <= 0:
                    raise StopResearch("job_budget")
                if event.type in {"response.completed", "response.incomplete", "response.failed"}:
                    response = event.response
                elif event.type == "error":
                    raise RuntimeError("ResponsesStreamError")
        except BudgetRefused as exc:
            error, local_refusal = exc.explanation.get('classification', 'admission_refused'), True
            self.meter.record_refusal(exc.explanation)
            self.activity('admission_refused', detail=exc.explanation)
        except StopResearch as exc:
            error = exc.reason
        except Exception as exc:
            error = "provider_error:"+type(exc).__name__
            from .outcomes import provider_failure
            self.session['v5_provider_failure'] = provider_failure(exc)
        finally:
            if stream is not None:
                try:
                    stream.close()  # Actual MeteredClient settlement, even on cancellation.
                except Exception as exc:
                    error = error or "provider_error:"+type(exc).__name__
                    from .outcomes import provider_failure
                    self.session['v5_provider_failure'] = provider_failure(exc)
        after = self.meter.snapshot()
        usage = {"cost_usd": max(0., after["cost_usd"]-before["cost_usd"]),
            "input_tokens": max(0, after["input_tokens"]-before["input_tokens"]),
            "output_tokens": max(0, after["output_tokens"]-before["output_tokens"]),
            "uncertain_usd": max(0., after["uncertain_usd"]-before["uncertain_usd"]),
            "uncertain_tokens": max(0, after["uncertain_tokens"]-before["uncertain_tokens"])}
        usage.update(sent_calls=max(0, after['llm_calls']-before['llm_calls']), response_received=response is not None)
        self.activity("failed" if error else "completed", **usage)
        if error or response is None:
            reason = error or "uncertain_model_call"
            if usage["uncertain_usd"] or usage["uncertain_tokens"]:
                reason = "uncertain_model_call"
            return Decision("pause", {"reason": reason}, **usage)
        outputs = [item.model_dump(exclude_none=True) if hasattr(item, "model_dump") else dict(item) for item in response.output]
        native = [item for item in outputs if item.get("type") == "web_search_call"]
        if native:
            self.activity('native_web_received', count=len(native),
                calls=[{'id': item.get('id'), 'action': (item.get('action') or {}).get('type')} for item in native],
                limit=after['limits']['web'], consumed=after['web'], exceeded=after['web'] > after['limits']['web'])
        # Persist original hosted source metadata. URL discovery is not document reading.
        from .models import public_link
        from .publication import url_key
        sources = {url_key(s["url"]): s for s in self.session.get("web_sources", [])}
        request_id = next((m["id"] for m in reversed(self.session.get("messages", [])) if m["role"] == "user"), None)
        for item in native:
            action = item.get("action") or {}
            leads = list(action.get("sources", []))
            if action.get("url"):
                leads.append({"url": action["url"]})
            for lead in leads:
                if not public_link(lead.get("url", "")):
                    continue
                key = url_key(lead["url"])
                sources[key] = {**sources.get(key, {}), **lead, "discovery_message_id": request_id,
                    "native_action": action.get("type", "search"), "native_content_status": "url_only"}
        self.session["web_sources"] = list(sources.values())
        self.session["v5_exchange"] = outputs
        self.meter.record_tools(0, web=len(native), operation_id=self.opid+":web")
        usage["native_calls"] = len(native)
        if getattr(response, "status", "completed") == "failed":
            self.session['v5_provider_failure'] = {'code':'ProviderResponseFailed','http_status':None,'retryable':False}
            return Decision("pause", {"reason": "provider_failed"}, **usage)
        if after["uncertain_tokens"]:
            return Decision("pause", {"reason": "uncertain_model_call"}, **usage)
        calls = [item for item in outputs if item.get("type") == "function_call"]
        if calls:
            if (force_final or saving) and (any(c['name'] not in final_tools for c in calls) or len(calls) > len(final_tools)):
                return Decision('pause', {'reason':self.state.get('stop_cause') or 'finalization_failed'}, **usage)
            parsed = []
            for call in calls:
                try:
                    args = json.loads(call["arguments"])
                except (ValueError, TypeError):
                    if len(calls) == 1:
                        return Decision("pause", {"reason": "provider_protocol_arguments"}, **usage)
                    # Its own validation error is returned to Sol; other calls still run.
                    args = {"v5_invalid_arguments": True}
                parsed.append({"tool": call["name"], "arguments": args, "call_id": call["call_id"],
                    "purpose": args.get("research_context") or args.get("query") or call["name"]})
            first = parsed[0]
            return Decision(first["tool"], first["arguments"], call_id=first["call_id"], purpose=first["purpose"],
                calls=parsed[1:], **usage)
        if native:
            return Decision("v5_continue", purpose="native_web_discovery", **usage)
        text = getattr(response, "output_text", "")
        if not text.strip():
            self.session['v5_provider_failure'] = {'code': 'ProviderEmptyResponse', 'http_status': None, 'retryable': False}
            return Decision('pause', {'reason': 'provider_failed'}, **usage)
        if drafting:
            # A text answer is not a saved section; the dossier stays offered.
            return Decision("v5_continue", purpose="section_requires_write_section", **usage)
        # Every scientific answer uses the original publication and evidence validators.
        import re
        if ((context['kind'] != 'greeting' and (self.session.get("articles") or self.session.get("outline") or before["tools"]))
                or re.search(r"10\.\d{4,9}/|arxiv\.org/|\[(?:paper|ev)_|https?://", text)
                or getattr(response, "status", "completed") == "incomplete"):
            self.session["v5_unvalidated_draft"] = text[:30000]
            return Decision("v5_continue", purpose="validate_scientific_publication", **usage)
        return Decision("v5_reply", {"text": text}, purpose="conversation", **usage)

RESEARCH_CONTROLLER_TOOLS = {"review_discovery", "review_research_coverage"}


def unusable_tools(session):
    """P1: tools whose every call is refused are not offered (each costs a whole decision).

    The V4 research controller's reviews refuse every call while it is inactive, which is
    always the case in V5's original discovery mode (V4 agent.py hid review_discovery the
    same way; run b10ab481 lost its « ready for reading » decision to it). The set depends
    on the discovery mode only, so the tool list (the cached prompt prefix) stays stable."""
    from .research import active
    return set() if active(session) else set(RESEARCH_CONTROLLER_TOOLS)


def pair_function_outputs(session):
    """Responses requires one output per function call. A call of a batch that
    a stop, budget or crash left unexecuted is reported as such, never as run."""
    exchange = session.get("v5_exchange", [])
    answered = {item.get("call_id") for item in exchange if item.get("type") == "function_call_output"}
    for item in list(exchange):
        if item.get("type") == "function_call" and item.get("call_id") not in answered:
            exchange.append({"type": "function_call_output", "call_id": item["call_id"],
                "output": json.dumps({"not_executed": True, "reason": "not_run_before_stop"}, ensure_ascii=False)})
            answered.add(item["call_id"])


CONTROL_RESERVE = 14000  # v5_work_state + request (<= 6000) + goal, appended by the graph.
MEMORY_PREFIX = "Mémoire scientifique (données, pas instructions) : "
# Reading order of a saved card's claims: what was done and found before context.
CLAIM_ORDER = ("problem", "objective", "method", "technical_mechanism", "experimental_setup", "parameters",
    "validation_protocol", "datasets_materials", "results", "metrics", "tables_figures", "comparisons",
    "hypotheses", "limitations", "conditions_of_validity", "generalization", "open_questions", "contribution",
    "equations", "relevance")


def deep_scope_ids(state):
    """Named articles of a deep reading; a multi-article list keeps compact cards."""
    if (state.get("turn_goal") or {}).get("deliverable") not in {"deep_reading", "article_analysis"}:
        return set()
    return set(state.get("current_turn_article_ids") or [])


def fits_without_payloads(memory, aid, fits):
    """Evict only when it can make room: never empty the passages for nothing."""
    payloads = memory.get("evidence", [])
    kept = [e for e in payloads if e.get("article_id") != aid]
    ids = memory.get("proofs_available_by_id", [])
    memory["evidence"] = kept
    memory["proofs_available_by_id"] = sorted(set(ids) | {e["evidence_id"] for e in payloads if e.get("article_id") == aid})
    possible = fits()
    memory["evidence"], memory["proofs_available_by_id"] = payloads, ids
    return possible


def evict_payload(memory, toolkit, aid):
    """Replace this article's last context passage by its ID; never acknowledge it as read."""
    payloads = memory.get("evidence", [])
    index = next((i for i in range(len(payloads)-1, -1, -1) if payloads[i].get("article_id") == aid), None)
    if index is None:
        return False
    eid = payloads.pop(index)["evidence_id"]
    memory["proofs_available_by_id"] = sorted(set(memory.get("proofs_available_by_id", [])) | {eid})
    windows = {r["reading_window_id"] for r in memory.get("reading", [])}
    for window in list(toolkit.reading_windows):
        if window["window_id"] in windows and window["status"] == "queued" and eid in window["evidence_ids"]:
            window["evidence_ids"] = [e for e in window["evidence_ids"] if e != eid]
            if not window["evidence_ids"]:
                toolkit.reading_windows.remove(window)
                memory["reading"] = [r for r in memory["reading"] if r["reading_window_id"] != window["window_id"]]
    return True


def complete_scope_cards(inputs, state, toolkit, budget, *, final=False):
    """Give Sol the saved claims V4's list card omits, within the same memory budget.

    V4's scientific_card keeps one claim for five fields. For a single named
    deep reading this hid saved mechanism, protocol and animal results (run
    e5cdb635: 0 of 8 such proofs presented, then reported as "not examined").
    Nothing is generated or re-validated here: whole saved claims and their exact
    passages only, with a delivery receipt like V4's context windows.
    """
    import uuid
    from .evidence import exact_source_valid
    from .original import evidence_projection
    item = next((i for i in inputs if i.get("role") == "developer" and isinstance(i.get("content"), str)
        and i["content"].startswith(MEMORY_PREFIX)), None)
    if item is None:
        return
    memory = json.loads(item["content"][len(MEMORY_PREFIX):])
    latest = sum(len(json.dumps(i, ensure_ascii=False)) for i in inputs[inputs.index(item)+1:] if i.get("role") == "user")
    fits = lambda: len(json.dumps({"role": "developer", "content": MEMORY_PREFIX+json.dumps(memory, ensure_ascii=False)},
        ensure_ascii=False))+latest <= budget
    deep = deep_scope_ids(state)
    presented = set(state.get("v5_presented_context_evidence", [])) if not final else set()
    def valid(claim, aid):
        return claim.get("evidence_ids") and all(eid in toolkit.evidence and toolkit.evidence[eid]["article_id"] == aid
            and exact_source_valid(toolkit.evidence[eid], toolkit.snapshots) for eid in claim["evidence_ids"])
    added_ids = {}
    for card in [*memory.get("selected_articles", []), *memory.get("consulted_articles", [])]:
        article = toolkit.registry.get(card.get("id"))
        if article is None or not article.analysis or not isinstance(card.get("analysis"), dict):
            continue
        aid = article.id
        stored = {f: [c for c in article.analysis.get(f, []) if isinstance(c, dict) and valid(c, aid)] for f in CLAIM_ORDER}
        given = {c.get("statement") for claims in card["analysis"].values() for c in claims}
        if aid in deep:
            full = False
            for field in CLAIM_ORDER:
                for claim in stored[field]:
                    if claim["statement"] in given:
                        continue
                    card["analysis"].setdefault(field, []).append({"statement": claim["statement"], "evidence_ids": claim["evidence_ids"]})
                    # Its proofs stay addressable by ID, as for V4's own claims.
                    sent = {e["evidence_id"] for e in memory.get("evidence", [])}
                    new_ids = set(claim["evidence_ids"])-sent-set(memory.get("proofs_available_by_id", []))
                    memory["proofs_available_by_id"] = sorted(set(memory.get("proofs_available_by_id", [])) | new_ids)
                    # Before synthesis, Sol can re-read a passage by ID: whole
                    # saved claims come before extra copies of passages. In
                    # finalisation, citable passages keep priority.
                    if not final and not fits() and fits_without_payloads(memory, aid, fits):
                        while not fits() and evict_payload(memory, toolkit, aid):
                            pass
                    if not fits():
                        card["analysis"][field].pop()
                        if not card["analysis"][field]:
                            card["analysis"].pop(field)
                        memory["proofs_available_by_id"] = sorted(set(memory["proofs_available_by_id"])-new_ids)
                        full = True
                        break
                    given.add(claim["statement"])
                    added_ids.setdefault(aid, []).extend(claim["evidence_ids"])
                if full:
                    break
        # Count every stored field, not only V4's five card fields.
        provided = {f: sum(c.get("statement") in {s["statement"] for s in stored[f]} for c in card["analysis"].get(f, []))
            for f in CLAIM_ORDER}
        omitted = {f: len(stored[f])-provided[f] for f in CLAIM_ORDER if len(stored[f]) > provided[f]}
        card.pop("analysis_omitted_claims", None)
        if omitted:
            card["analysis_omitted_claims"] = omitted
            if not fits():
                card.pop("analysis_omitted_claims")
    # Exact passages of the added claims, first proof of each claim first.
    for aid, eids in added_ids.items():
        have = {e["evidence_id"] for e in memory.get("evidence", [])}
        ordered = list(dict.fromkeys(eids))
        window = {"window_id": "reading_context_"+uuid.uuid4().hex, "article_id": aid, "evidence_ids": [], "status": "queued"}
        for eid in ordered:
            if eid in have or eid in presented:
                continue
            memory.setdefault("evidence", []).append(evidence_projection(toolkit.evidence[eid], toolkit.snapshots))
            if not fits():
                memory["evidence"].pop()
                break
            window["evidence_ids"].append(eid)
        if window["evidence_ids"]:
            toolkit.reading_windows.append(window)
            memory.setdefault("reading", []).append({"reading_window_id": window["window_id"]})
            if not fits():
                memory["reading"].pop()
                toolkit.reading_windows.pop()
                memory["evidence"] = [e for e in memory["evidence"] if e["evidence_id"] not in window["evidence_ids"]]
                continue
            sent = set(window["evidence_ids"])
            memory["proofs_available_by_id"] = [e for e in memory.get("proofs_available_by_id", []) if e not in sent]
    item["content"] = MEMORY_PREFIX+json.dumps(memory, ensure_ascii=False)


def orchestration_instructions(text):
    return text.replace(
        "Le budget de six tours d'outils par défaut favorise les appels groupés : recherches puis lectures,\n"
        "jugements sauvegardés et synthèse. Plusieurs outils par tour sont possibles. Si le budget limite\n",
        "Le budget global borne les recherches, lectures, jugements sauvegardés et synthèse.\n"
        "Aucun nombre fixe de tours. Plusieurs outils par réponse sont possibles ; ils s'exécutent dans l'ordre donné, "
        "et un appel qui dépend de leurs résultats attend leur examen. Si le budget limite\n")

class ScientificAdapter(V4Adapter):
    # The current complete guide includes the original scientific guide: send it
    # once, then retain its original discovery addendum. No duplicate prompt cost.
    instructions = orchestration_instructions(COMPLETE_SCIENTIFIC_GUIDE)+"\n"+DISCOVERY_GUIDANCE+"\n"+(
        "Orchestration V5 : choisis librement les outils selon le besoin scientifique. "
        "Aucun nombre de tours prédéfini. Réutilise les résultats valides ; poursuis le travail utile dans le budget global. "
        "Distingue extraction, lecture et analyse. Signale les lacunes ; conserve les tâches incomplètes. "
        "Comprends le problème, la contribution et la structure avant de choisir les approfondissements utiles. "
        "analyze_article(index) expose sections, passages et lacunes ; choisis librement les lectures ciblées, "
        "en priorité les passages reportés pertinents. save enrichit la fiche et les mesures avec preuves ; "
        "review enregistre ta couverture pour la question et ses incertitudes, sans exiger une lecture exhaustive. "
        "Les fiches déjà présentées restent récupérables par get_analysis_claims. Si une comparaison ou rédaction est "
        "demandée, confronte les travaux utiles par compare_articles et conserve les conditions et incertitudes. "
        "Avant chaque write_section, scientific_preparation du dossier liste les lectures, fiches et comparaisons "
        "encore utiles à la section ; les fiches prêtes sont réutilisées. "
        "Une nouvelle question scientifique autorise un approfondissement. "
        "Pour rédiger : search_needed=False signifie pas de découverte de nouveaux articles ; get_fulltext reste "
        "nécessaire pour les articles sélectionnés jamais récupérés, sauf interdiction explicite de téléchargement. "
        "Suivre scientific_preparation : récupérer, choisir les passages utiles, enrichir les fiches et comparer. "
        "Exploiter les résultats chiffrés, unités, conditions et mécanismes déjà présents, avec [ev_…] pour chaque "
        "affirmation scientifique ; distinguer les auteurs, tes inférences et les informations absentes. "
        "Termine par finalize_response avec les validations et preuves existantes.")

    def __init__(self, config, meter, *, sources=None, emit=lambda event: None, check=lambda: None, article_memory=None):
        super().__init__(config, sources or ScientificSources(config=config), fixture_sources=True, article_memory=article_memory)
        self.meter, self.emit, self.check = meter, emit, check

    def tools(self):
        # Includes writing, plans, comparisons, gaps, scientific scope, and all original tools.
        allowed = getattr(self, 'task_profile', {}).get('tools')
        unusable = unusable_tools(getattr(self, 'tool_session', None) or {})
        return [t for t in definitions(True) if (allowed is None or t.get('name') in allowed)
            and t.get('name') not in unusable]

    def toolkit(self, session):
        t = ScholarTools(session, self.sources, self.config, self.check,
            lambda tool, status, **fields: self.emit({"tool": tool, "status": status,
                "at": datetime.now(timezone.utc).isoformat(), **fields}), article_memory=self.article_memory)
        counters = session.get("v5_tool_counters", {})
        for name in ("search_count", "new_candidates"):
            setattr(t, name, counters.get(name, 0))
        t.deep_analysis_ids = set(counters.get("deep_analysis_ids", []))
        t.checked_ids = {aid for aid, a in t.registry.items() if a.verified and not a.metadata_errors}
        # Publication-time verification receipts survive toolkit reconstruction:
        # a failed registry lookup is not repeated by the next finalisation attempt.
        final = session.get("v5_final_verification", {})
        t.final_verification_attempted = set(final.get("attempted", []))
        t.final_verification_ids = list(final.get("ids", []))
        return t

    def prepare(self, session, last_response=None):
        from .evidence import proof_scope
        token = proof_scope.set(session)
        try:
            return self._prepare(session, last_response)
        finally:
            proof_scope.reset(token)

    def _prepare(self, session, last_response=None):
        synchronize_scope(session)
        t = self.toolkit(session)
        pair_function_outputs(session)
        from .call_context import profile, inputs as administrative_inputs
        self.task_profile = profile(session)
        self.tool_session = session
        self.instructions = self.task_profile.get('instructions', type(self).instructions)
        if self.task_profile['kind'] != 'science':
            return t, administrative_inputs(session, self.task_profile)
        # Same split as V4's request_inputs: bounded memory, then the remaining
        # room for the tool result Sol just asked for. A fixed 22 000-char cap
        # cut every 8-passage reading (~25 000 chars) to 4 passages (run e5cdb635).
        # While drafting, the section dossier takes its share from the same budget.
        from . import workflow
        exchange_budget, memory_room, dossier_room = workflow.context_split(self.config, session)
        exchange = compact_exchange(copy.deepcopy(session.get("v5_exchange", [])), t, exchange_budget)
        room = self.config.max_context_chars-len(json.dumps(exchange, ensure_ascii=False))-CONTROL_RESERVE-dossier_room
        view = session
        if session.get("v5_new_search"):
            # A new, unreferenced search starts from its request and metadata index.
            # Historical science stays stored and is accessible by explicit tools.
            allowed = set(session.get("v5_explicit_context_ids", [])) | set(session.get("current_turn_article_ids", []))
            view = {**session, "summary": "", "focus": {}, "outline": {}, "response_history": [], "last_response": {},
                "messages": [next(m for m in reversed(session["messages"]) if m["role"] == "user")],
                "displayed_article_ids": [a for a in session.get("displayed_article_ids", []) if a in allowed],
                "evaluation": {"recommended_article_ids": [a for a in session.get("evaluation", {}).get("recommended_article_ids", []) if a in allowed]},
                "articles": {aid: a if aid in allowed else {**a, "analysis": {}, "fulltext": {}, "documents": {}} for aid, a in session.get("articles", {}).items()}}
            if not session.get("v5_memory_updated"):
                view["research_state"] = {}
            # Use a context-only toolkit: clearing history here cannot mutate the repository.
            context_toolkit = self.toolkit(copy.deepcopy(view))
        elif session.get("writing_segment"):
            # A mission segment has no new consultant message: the previous segments'
            # answers (a partial manuscript of 23,000 characters in the replay) would be
            # read as the current exchange. Their sections are saved and in the dossier.
            messages = session.get("messages", [])
            last_user = max((i for i, m in enumerate(messages) if m["role"] == "user"), default=len(messages)-1)
            view = {**session, "messages": messages[:last_user+1]}
            context_toolkit = t
        else:
            context_toolkit = t
        final = bool(session.get('v5_finalization_mode') or session.get('v5_unvalidated_draft'))
        memory_budget = min(memory_room, max(3000, room))
        # Working memory of this request: what Sol already consulted stays visible
        # between decisions. Its room is taken from the same memory budget.
        working = None
        if not final and workflow.next_writing_section(session) is None:
            working = workflow.working_memory(session, min(workflow.WORKING_MEMORY_CHARS, memory_budget//2))
        working_item = ({"role": "developer", "content": workflow.WORKING_PREFIX+json.dumps(working, ensure_ascii=False)}
            if working else None)
        if working_item:
            memory_budget = max(3000, memory_budget-len(json.dumps(working_item, ensure_ascii=False)))
        from .consulted import packet as consulted_packet
        from .toolkit import transmitted_evidence_ids
        excluded = transmitted_evidence_ids(exchange, t.evidence)
        allowance = max(0, min(12000, memory_budget//2, memory_budget-3200))
        consulted = consulted_packet(session, allowance, exclude=excluded)
        def material_item(value):
            return {'role': 'developer', 'content': json.dumps(value, ensure_ascii=False)} if value else None
        consulted_item = material_item(consulted)
        reserved = len(json.dumps(consulted_item, ensure_ascii=False)) if consulted_item else 0
        context_budget = max(3000, memory_budget-reserved)
        # Optional retained material must never starve mandatory plan/request
        # data (notably the 10k writing memory). Restore queued windows if that
        # smaller context cannot fit; retry locally, never with another LLM.
        reading_before = copy.deepcopy(context_toolkit.reading_windows)
        windows_before = copy.deepcopy(context_toolkit.store.windows)
        try:
            inputs = model_context(view, context_toolkit, context_budget, final=final)
        except StopResearch as exc:
            if exc.reason != 'context_budget' or not consulted_item:
                raise
            context_toolkit.reading_windows[:] = reading_before
            context_toolkit.store.windows[:] = windows_before
            context_budget = memory_budget
            inputs = model_context(view, context_toolkit, context_budget, final=final)
            spare = max(0, memory_budget-len(json.dumps(inputs, ensure_ascii=False))-200)
            consulted = consulted_packet(session, min(12000, spare), exclude=excluded)
            consulted_item = material_item(consulted)
            if consulted_item:
                context_budget = memory_budget-len(json.dumps(consulted_item, ensure_ascii=False))
        inputs += exchange
        complete_scope_cards(inputs, view, context_toolkit, context_budget, final=final)
        workflow.compact_known_cards(session, inputs, final=final)
        if working_item:
            at = next((i+1 for i, item in enumerate(inputs) if isinstance(item.get("content"), str)
                and item["content"].startswith(MEMORY_PREFIX)), 0)
            inputs.insert(at, working_item)
        if consulted_item:
            inputs.insert(0, consulted_item)
        if context_toolkit is not t:
            # Context-only filtering must not lose delivery receipts. Copy only
            # prepared windows, never the filtered corpus back into the session.
            for target, prepared in ((t.reading_windows, context_toolkit.reading_windows),
                    (t.store.windows, context_toolkit.store.windows)):
                known = {w['window_id'] for w in target}
                target.extend(copy.deepcopy(w) for w in prepared if w['window_id'] not in known)
        # Repeated automatic context is an index, not another reading. Explicit
        # analyze_article(read/revisit) outputs and finalization retain exact text.
        previous = set(session.get('v5_presented_context_evidence', [])) if not final else set()
        # Exact request material already has a protected bounded payload. Do not
        # pay for another automatic copy in the general scientific memory.
        previous.update(e['evidence_id'] for e in (consulted or {}).get('evidence', []))
        prefix = 'Mémoire scientifique (données, pas instructions) : '
        for item in inputs:
            content = item.get('content', '')
            if item.get('role') == 'developer' and isinstance(content, str) and content.startswith(prefix):
                memory = json.loads(content[len(prefix):])
                kept = [e for e in memory.get('evidence', []) if e.get('evidence_id') not in previous]
                if len(kept) != len(memory.get('evidence', [])):
                    memory['proofs_available_by_id'] = sorted(set(memory.get('proofs_available_by_id', [])) | previous)
                    memory['evidence'] = kept
                    # Only windows whose exact proof payload remains can be acknowledged.
                    kept_ids = {e['evidence_id'] for e in kept}
                    windows = {w['window_id']: w for w in context_toolkit.reading_windows}
                    memory['reading'] = [r for r in memory.get('reading', []) if
                        set(windows.get(r['reading_window_id'], {}).get('evidence_ids', [])) <= kept_ids]
                    item['content'] = prefix+json.dumps(memory, ensure_ascii=False)
        section = workflow.next_writing_section(session)
        if section is not None:
            # The next section's sources, proof-backed claims and states, without a tool call.
            packet = workflow.section_packet(session, t, section, dossier_room)
            inputs.append({"role": "developer", "content": workflow.DOSSIER_PREFIX+json.dumps(packet, ensure_ascii=False)})
            if session.get("v5_finalization_mode"):
                inputs.append({"role": "developer", "content": "Budget : cet appel est réservé à l'enregistrement de la "
                    f"section {section['id']} par write_section(action='save') à partir du dossier, sans nouvelle lecture. "
                    "Seules les sources analysées sont citées [paper_ID] ; les autres sont signalées automatiquement "
                    "comme limites documentaires. Les sections suivantes reprendront à la demande suivante."})
        if session.get("v5_unvalidated_draft"):
            inputs.append({"role": "developer", "content": "Valide ta réponse avec finalize_response ; respecte les refus de validation et indique les tâches restantes."})
        return t, inputs

    def writing_active(self, session):
        from . import workflow
        return workflow.writing_active(session)

    def gathers(self, decision):
        from . import workflow
        return workflow.gathers(decision)

    def novel_delivery(self, session, decision, output):
        from . import workflow
        return workflow.novel_delivery(session, decision, output)

    def workflow_view(self, session):
        from . import workflow
        return workflow.workflow_view(session)

    def save_ready(self, session):
        from . import workflow
        return workflow.save_ready(session)

    def delivered(self, toolkit, inputs):
        from .workflow import DOSSIER_PREFIX
        receipts = [{**item, 'content':item['content'][len(DOSSIER_PREFIX):]} if
            isinstance(item.get('content'),str) and item['content'].startswith(DOSSIER_PREFIX) else item for item in inputs]
        toolkit.delivered(window_ids(inputs), inputs=receipts)
        from .workflow import acknowledge_inputs
        acknowledge_inputs(toolkit.state, inputs)
        from .toolkit import transmitted_evidence_ids
        toolkit.state['v5_presented_context_evidence'] = sorted(set(toolkit.state.get('v5_presented_context_evidence', [])) |
            transmitted_evidence_ids(receipts, toolkit.evidence))
        toolkit.save()

    def undelivered(self, toolkit, inputs):
        for window in [*toolkit.reading_windows, *toolkit.store.windows]:
            if window['status'] == 'queued':
                window['status'] = 'undelivered'
        toolkit.save()

    def local(self, session, decision):
        return decision.tool in {"v5_continue", "v5_reply"} or super().local(session, decision)

    def before_reuse(self, session, decision):
        # archived_document runs before execute. It must not bypass V5's
        # version check, including for a process/session restored from storage.
        if decision.tool == 'get_fulltext' and decision.arguments.get('article_id'):
            self.toolkit(session).refresh_html_extractions([decision.arguments['article_id']])

    def execute(self, session, decision, *, remaining_seconds):
        snapshot = self.meter.snapshot() if callable(getattr(self.meter, 'snapshot', None)) else None
        if isinstance(snapshot, dict):
            remaining_seconds = min(remaining_seconds, snapshot.get('remaining_seconds', remaining_seconds))
        if decision.tool == "v5_continue":
            return {"native_results_saved": True}
        if decision.tool == "v5_reply":
            session["last_response"] = {"answer": decision.arguments["text"], "citations": [], "coverage_sufficient": True}
            return {"published": True}
        if decision.tool not in TOOLS:
            return {"error": "Unknown scientific tool"}
        from . import workflow
        if decision.tool == "finalize_response":
            attempts = session.get("v5_tool_counters", {}).get("finalization_attempts", 0)
            if attempts >= self.config.max_finalization_attempts:
                return {"not_executed": True, "v5_stop_reason": "finalization_failed"}
            if workflow.writing_active(session) and workflow.plan_approved(session):
                # Before the plan's approval nothing is drafted: the run may end with a
                # preparation report (its sections stay pending).
                section = workflow.next_writing_section(session)
                return {"not_executed": True, "reason": "sections_pending", "next_section_id": section["id"],
                    "guidance": "Rédaction en cours : enregistrer chaque section avec write_section(save) ; "
                        "le manuscrit est assemblé et publié à la dernière section."}
        exchange_budget = workflow.context_split(self.config, session)[0]
        room = exchange_budget-workflow.exchange_size(session.get("v5_exchange", []))
        if workflow.gathers(decision) and room < workflow.MIN_GATHERING_ROOM:
            # Nothing is read or counted: the call is renewed with the next response.
            result = workflow.deferred()
            workflow.remember_deferred(session, decision, result)
            return result
        opid = session.get("v5_current_operation", decision.call_id)
        try:
            self.meter.record_tools(1, operation_id=opid)
        except BudgetRefused as exc:
            self.meter.record_refusal(exc.explanation)
            return {"not_executed": True, "v5_stop_reason": "job_budget"}
        t = self.toolkit(session)
        if decision.tool == 'finalize_response':
            t.finalization_only = True
            # V4 original.run's publication phase: the cited, presented works are
            # verified against the registries within the remaining deadline.
            # Unconfirmed references stay distinguished in the refusal.
            constraints = set((session.get("turn_goal", {}).get("references") or {}).get("constraints") or [])
            t.final_verification_allowed = "no_search" not in constraints
        finalization_attempts = session.get("v5_tool_counters", {}).get("finalization_attempts", 0)+int(decision.tool == "finalize_response")
        self.emit({"tool": decision.tool, "status": "running", "at": datetime.now(timezone.utc).isoformat()})
        from .turn_control import edit_snapshot, finish_edit
        edit_before = edit_snapshot(session, decision)
        try:
            result = self.workflow_checks(session, t, decision)
            if result is None:
                result = t.execute(decision.tool, dict(decision.arguments), time.monotonic()+remaining_seconds)
        except StopResearch as exc:
            result = {"not_executed": True, "v5_stop_reason": exc.reason}
        except Exception as exc:
            result = {"error": str(exc)[:1500], "error_type": type(exc).__name__}
        finally:
            t.save()
            session["v5_tool_counters"] = {"search_count": t.search_count, "new_candidates": t.new_candidates,
                "deep_analysis_ids": sorted(t.deep_analysis_ids), "finalization_attempts": finalization_attempts}
            if decision.tool == "finalize_response":
                session["v5_final_verification"] = {"attempted": sorted(t.final_verification_attempted),
                    "ids": list(t.final_verification_ids)}
        if decision.tool == "finalize_response" and not result.get("published") and finalization_attempts >= self.config.max_finalization_attempts:
            result["v5_stop_reason"] = "finalization_failed"
        if decision.tool in {"get_article", "get_analysis_claims", "analyze_article", "compare_articles"}:
            explicit = [a.get("id") for a in result.get("articles", []) if isinstance(a, dict)]
            explicit += decision.arguments.get("article_ids", [])
            if decision.arguments.get("article_id"):
                explicit.append(decision.arguments["article_id"])
            session["v5_explicit_context_ids"] = list(dict.fromkeys([*session.get("v5_explicit_context_ids", []), *filter(None, explicit)]))
        if decision.tool == "update_research_state":
            session["v5_memory_updated"] = True
        if decision.tool == 'set_turn_goal' and not result.get('error'):
            ids = list((session.get('turn_goal', {}).get('references') or {}).get('article_ids') or [])
            session['v5_explicit_context_ids'] = ids
            session['current_turn_article_ids'] = ids
            session['conversation_references'] = copy.deepcopy(session['turn_goal']['references'])
        synchronize_scope(session)
        if t.finished:
            session["last_response"] = copy.deepcopy(t.finished)
            session.pop("v5_unvalidated_draft", None)
        result = self.after_workflow(session, t, decision, result)
        result = finish_edit(session, decision, result, edit_before)
        # A reading result carries its passages, not another copy of the whole card.
        for row in result.get('reading', []):
            article = row.get('article') or {}
            if article.get('analysis'):
                article.pop('analysis')
                article['analysis_ref'] = {'article_id':article['id'], 'retrieve':'get_analysis_claims'}
        result = workflow.fit_output(session, t, decision, result, room)
        workflow.remember_deferred(session, decision, result)
        workflow.record_delivery(session, decision, result, confirmed=False)
        self.emit({"tool": decision.tool, "status": "rejected" if result.get("error") else "completed",
            "at": datetime.now(timezone.utc).isoformat()})
        return result

    def workflow_checks(self, session, toolkit, decision):
        """Refusals decided before V4's tool runs (None lets the tool run)."""
        from . import workflow
        args = decision.arguments
        if decision.tool == 'write_section' and args.get('action') == 'save':
            from .writing_progress import plan_edit_request
            from .instructions import current_request
            from .turn_control import human_refusal
            if plan_edit_request(session, (current_request(session) or {}).get('content', '')):
                return human_refusal(session, args, 'plan_edit_request')
            if not workflow.plan_approved(session):
                return human_refusal(session, args, 'plan_not_approved')
        if decision.tool == "finalize_response" and workflow.plan_required(session) and not workflow.plan_unchanged_allowed(session):
            return {"published": False, "rejection_code": "plan_not_saved",
                "error": "Le plan proposé n'est pas enregistré. L'enregistrer avec manage_outline (create pour un nouveau plan ; "
                    "pour un plan existant : rename, update, add, delete, move ou merge, replace seulement sans brouillon) : "
                    "sections, article_ids et article_roles, puis publier sa présentation ; l'approbation reste au consultant. "
                    "Si la modification demandée est imprécise ou impossible, republier une réponse qui demande la précision "
                    "utile ou explique la limite : le plan restera inchangé."}
        if decision.tool == "finalize_response" and not session.get("v5_finalization_mode"):
            # D2/D3 before D1: reading and cards first, so that the comparison is saved once,
            # on the final evidence (a saved comparison is stale after new passages are read).
            # Asked once per request and set of works; a second publication is never blocked,
            # and a forced synthesis (stagnation, reserve) is never refused for it.
            missing = workflow.cards_to_save(session, toolkit, args)
            gaps = workflow.reading_gaps(session, toolkit, args)
            from .instructions import current_request
            gate = session.setdefault("v5_card_gate", {})
            request = (current_request(session) or {}).get("id")
            if gate.get("request_id") != request:
                gate.clear()
                gate["request_id"] = request
            keys = {*missing, *(aid+":sections" for aid in gaps)}
            if keys and not keys <= set(gate.get("refused", [])):
                gate["refused"] = sorted(set(gate.get("refused", [])) | keys)
                cited = [a for a in args.get("article_ids") or [] if a in toolkit.registry]
                return {"published": False,
                    "rejection_code": "analysis_persistence_required" if missing else "requested_sections_unread",
                    "error": " ".join(m for m, on in ((workflow.SAVE_CARDS, missing), (workflow.READING_GAPS, gaps)) if on),
                    **({"pending_analysis_article_ids": list(missing), "evidence_ids_by_article": missing} if missing else {}),
                    **({"unread_requested_sections": gaps} if gaps else {}),
                    "requested_dimensions": workflow.requested_dimensions(session),
                    "documentary_status": {aid: workflow.documentary_status(session, toolkit, aid) for aid in cited}}
        if decision.tool == "finalize_response" and workflow.comparison_required(session, args.get("article_ids")):
            return {"published": False, "rejection_code": "comparison_not_saved",
                "error": "La comparaison demandée n'a aucune relation enregistrée : la matrice lue n'est pas une comparaison "
                    "sauvegardée. "+workflow.SAVE_RELATIONS,
                "article_ids": workflow.comparison_article_ids(session, args.get("article_ids"))}
        if decision.tool == "compare_articles":
            refusal = workflow.comparison_check(session, args)
            if refusal:
                return refusal
        if decision.tool == "manage_outline" and args.get("action") != "read":
            refusal = plan_edit_check(session, args)
            if refusal:
                return refusal
        if decision.tool == "write_section":
            notice = workflow.dossier_in_context(session, args)
            if notice:
                return notice
        if decision.tool == "write_section" and args.get("action") == "save":
            from .tools.outline import resolve_section
            try:
                section = resolve_section(session, args.get("section_id"))
            except ValueError:
                return None  # V4 returns its own refusal.
            # Confirming a text refused for preparation reuses it (decision arguments
            # are completed in place, so V4 validates the very text proposed).
            workflow.proposed_section(session, section["id"], args)
            progress = workflow.writing_progress(session) or {}
            if progress.get('include_conclusion') and progress.get('target_ids', [None])[-1] == section['id']:
                import re
                text = args.get('text') or '\n'.join(p.get('text','') for p in args.get('paragraphs', []))
                if not re.search(r'^#{1,3}\s+Conclusion générale\s*\n\s*\S', text, re.M|re.I):
                    return {'saved':False,'rejection_code':'conclusion_required',
                        'error':'Ajouter la Conclusion générale aux résultats et limites de la dernière section. '
                            'Elle doit être sauvegardée avec ses citations et preuves, pas publiée seulement dans le chat.'}
            cited, declared = workflow.uncarded_sources(session, toolkit, section, args)
            if cited:
                workflow.keep_proposed_section(session, section["id"], args, "section_source_not_analysed")
                return {"saved": False, "rejection_code": "section_source_not_analysed", "sources": cited,
                    "error": "Le texte cite comme source analysée une publication sans fiche scientifique : "
                        + " ; ".join(f"[{s['article_id']}] « {s['title'][:90]} » — {s['action']}" for s in cited)
                        + ". Aucune citation n'est retirée automatiquement ; write_section(save, text=null) ne reprend "
                        "que le texte refusé."}
            if declared:
                # Declared only: not a scientific source of this text, still a planned one.
                args["article_ids"] = [a for a in args["article_ids"] if a not in {s["article_id"] for s in declared}]
                toolkit.v5_undeclared = declared
            refusal = workflow.preparation_refusal(session, toolkit, section)
            if refusal:
                workflow.keep_proposed_section(session, section["id"], args)
                return refusal
            missing = workflow.missing_section_sources(session, toolkit, section, args)
            if missing:
                return {"saved": False, "rejection_code": "section_sources_missing", "missing_sources": missing,
                    "error": "Chaque source analysée de la section doit être intégrée avec [paper_ID], ses preuves, ses "
                        "méthodes, résultats et limites. Les sources sans fiche exploitable sont enregistrées automatiquement "
                        "comme limites documentaires de la section."}
        return None

    def after_workflow(self, session, toolkit, decision, result):
        """Plan readiness after a saved plan; drafting progress after a section save."""
        from . import workflow
        if not isinstance(result, dict):
            return result
        if result.get('outcome') == 'human_validation_required':
            return result
        if decision.tool == 'set_turn_goal' and not result.get('error'):
            workflow.reconcile_draft_goal(session)
        if decision.tool == "compare_articles" and not result.get("error"):
            result = workflow.after_comparison(session, decision.arguments, result)
        if decision.tool == "manage_outline" and result.get("saved"):
            result["source_readiness"] = workflow.plan_readiness(session, toolkit)
            # A drafting request waiting for its plan starts on the saved sections.
            from .writing_progress import refresh_writing
            refresh_writing(session)
        if (decision.tool == "write_section" and decision.arguments.get("action") == "save" and result.get("saved")
                and not workflow.writing_progress(session)):
            # A section saved outside a drafting request (a revision) keeps its limits and quality history too.
            result["documentary_limits"] = workflow.record_section_limits(session, toolkit, result["section_id"])
            workflow.drop_provisional_section(session, result["section_id"])
        if decision.tool == "write_section" and decision.arguments.get("action") == "save" and workflow.writing_progress(session):
            from .writing_progress import refresh_writing
            sid = decision.arguments.get("section_id")
            if not result.get("saved") and result.get("rejection_code") not in {"section_preparation_required", "plan_not_approved"}:
                from .tools.outline import resolve_section
                try:
                    sid = resolve_section(session, sid)["id"]
                except ValueError:
                    sid = None
                if sid and workflow.record_section_refusal(session, sid, result.get("error") or result.get("rejection_code"),
                        self.config.max_finalization_attempts):
                    result["section_set_aside"] = sid
            if not result.get("saved") and sid and result.get("rejection_code") not in {"section_preparation_required",
                    "section_source_not_analysed", "plan_not_approved"}:
                workflow.keep_proposed_section(session, sid, decision.arguments, result.get("error") or result.get("rejection_code"))
            if result.get("saved"):
                progress = workflow.writing_progress(session) or {}
                if progress.get('include_conclusion') and progress.get('target_ids', [None])[-1] == result['section_id']:
                    sec = next(s for s in session['outline']['sections'] if s['id']==result['section_id'])
                    d = sec['drafts'][-1]
                    session['document']['conclusion'] = {'section_id':sec['id'],'draft_id':d['id'],
                        'plan_revision':session['outline']['plan_revision'], 'evidence_ids':d.get('evidence_ids', [])}
                result["documentary_limits"] = workflow.record_section_limits(session, toolkit, result["section_id"])
                workflow.drop_provisional_section(session, result["section_id"])
                if getattr(toolkit, "v5_undeclared", None):
                    result["not_declared_as_analysed"] = toolkit.v5_undeclared
            refresh_writing(session)
            following = workflow.next_writing_section(session)
            if following is None:
                session["last_response"] = workflow.writing_result(session)
                progress = workflow.writing_progress(session) or {}
                result = {**result, "published": True, "writing_complete": progress.get('status')=='draft_complete'
                    and not progress.get('blocked')}
            else:
                result["next_section_id"] = following["id"]
        return result

    def finish_exchange(self, session, decision, output):
        self.meter.flush_time()
        if decision.tool in {'search_scientific', 'search_collected_articles', 'find_similar_articles'}:
            # Includes receipt replays: a candidate considered for this request
            # need not be newly inserted or have its historical analysis injected.
            from .workflow import knowledge
            record = knowledge(session)
            ids = [a.get('id') for a in output.get('articles', []) if isinstance(a, dict) and a.get('id')]
            record['candidate_ids'] = sorted(set(record.get('candidate_ids', [])) | set(ids))
        if decision.call_id:
            session.setdefault("v5_exchange", []).append({"type": "function_call_output", "call_id": decision.call_id,
                "output": json.dumps(output, ensure_ascii=False)})


def plan_edit_check(session, args):
    """Refusal of one of Sol's plan edits for the current request, or None (V4 then runs it).

    A targeted plan edit changes the named section only: its title, description, place,
    sources, and its removal, addition or merge when the consultant asked for one. A
    drafted section never leaves the plan unless the consultant named it for removal, and
    is never merged into another section (V4 would make its text the other section's
    current version). An explicitly requested removal is not refused for « preserving the
    corpus » (V4 plan_operation keeps the corpus of the whole plan when no search is needed):
    the removed publications leave the corpus of this request."""
    from .writing_progress import plan_edit_text, plan_edit_target, plan_edit_kind, plan_edit_actions
    from .instructions import current_request
    from .tools.outline import resolve_section
    current = current_request(session) or {}
    request = current.get("content", "")
    action = args.get("action")

    def section(identifier):
        try:
            return resolve_section(session, identifier) if identifier else None
        except ValueError:
            return None
    named, other = section(args.get("section_id")), section(args.get("target_section_id"))
    text = plan_edit_text(session, request)
    edit = text is not None
    kind = plan_edit_kind(text or request)
    # The section the consultant named, resolved once on the plan as it was: after
    # « Supprime la section 4 », « section 4 » no longer designates another section.
    pin = session.get("v5_plan_edit_target") or {}
    if pin.get("request_id") != current.get("id"):
        pin = {"request_id": current.get("id"), "section_id": plan_edit_target(session, request) if edit else None}
        session["v5_plan_edit_target"] = pin
    target = pin["section_id"]
    if target:
        allowed = plan_edit_actions(text)
        if action not in allowed or (action != "add" and target not in {s["id"] for s in (named, other) if s}):
            return {"saved": False, "rejection_code": "plan_edit_out_of_scope", "error":
                f"Modification ciblée du plan : seule la section {target} change (manage_outline "
                + ", ".join(sorted(allowed)) + " avec son section_id) ; les autres sections et le plan restent inchangés."}
    if action == "delete" and named and named.get("drafts") and not (edit and kind["removal"] and target == named["id"]):
        return {"saved": False, "rejection_code": "drafted_section_protected", "error":
            f"La section « {named['title'][:120]} » est déjà rédigée : elle ne quitte le plan que si le consultant demande "
            "explicitement sa suppression en la nommant. La conserver, la renommer, ou y fusionner une autre section."}
    if action == "merge" and named and named.get("drafts"):
        return {"saved": False, "rejection_code": "drafted_section_protected", "error":
            f"Fusion refusée : la section « {named['title'][:120]} » est déjà rédigée et son texte remplacerait celui de la "
            "section cible. Fusionner dans l'autre sens (la section rédigée comme target_section_id) ; ses brouillons restent intacts."}
    operation = (session.get("turn_goal") or {}).get("plan_operation") or {}
    sections = (session.get("outline") or {}).get("sections", [])
    if (action == "delete" and named and edit and kind["removal"] and target == named["id"]
            and operation.get("section_id") == named["id"]):
        # « Modifie le plan : supprime la section 4 » : V4 plan_operation reads « modifie »
        # as a revision of section 4 and only lets it be updated. Its removal, explicitly
        # asked and confined to it above, is the requested operation.
        operation["section_id"] = None
        operation["kind"] = "revision"
    reduce = bool(operation.get("max_sections") and len(sections) > operation["max_sections"])
    if (action in {"delete", "dissociate"} and named and (edit and kind["removal"] or reduce)
            and operation.get("memory_only") and operation.get("preserve_corpus") and operation.get("article_ids")):
        removed = set(named.get("article_ids", [])) if action == "delete" else set(args.get("article_ids") or [])
        removed -= {aid for s in sections if s["id"] != named["id"] for aid in s.get("article_ids", [])}
        operation["article_ids"] = [aid for aid in operation["article_ids"] if aid not in removed]
    return None
