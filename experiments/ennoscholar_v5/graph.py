"""Small durable executor around the original V4 tools, with no science controller.

    reconcile -> Sol decision -> validated V4 tool -> reconcile
         |                |                              |
         +----------------+-> pause/checkpoint <----------+

No fixed LLM-turn limit. Scientific operations continue within a cumulative
budget; explicit pause, stagnation and exhaustion leave a durable checkpoint.
"""
from __future__ import annotations

from dataclasses import asdict
import time
import json
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command, interrupt

from agents.EnnoScholarV4.task_results import fingerprint
from .adapter import Decision, available_tools
from .progress import project,model_work_view,request_packet
from .repository import RevisionConflict
from .policy import reusable, semantic_key, informative_signature, stagnant, useful_need


class State(TypedDict, total=False):
    scope_key: str
    revision: int
    job_id: str
    slice: int
    slice_calls: int
    sequence: int
    usage: dict
    progress: dict
    pending: str | None
    pending_decision: str | None
    last_operation: str | None
    status: str
    reason: str | None
    response_ref: str | None
    no_gain_streak: int
    last_purpose: str
    step_start_signature: str
    finalizing: bool
    finalization_attempted: bool
    stop_cause: str
    recent_no_gain: list
    batch: list
    batch_productive: bool


DISCOVERY_TOOLS = {'search_scientific', 'search_collected_articles', 'find_similar_articles'}


# Sol's calls of one response run in its order, one durable receipt each. A call
# written without the results of earlier calls of the same response waits for
# them: Sol reviews those results first (not executed, never counted).
def depends_on_batch_results(decision):
    return decision.tool == 'finalize_response' or (
        decision.tool == 'analyze_article' and decision.arguments.get('action') in {'save', 'repair', 'review'})


class Runner:
    def __init__(self, scope, repository, adapter, sol, checkpointer, *, clock=time.monotonic, after_commit=None):
        if not (getattr(sol, "offline", False) or getattr(sol, "langgraph_live", False)):
            raise PermissionError("Prototype offline only; live MeteredClient integration requires review")
        self.scope, self.repo, self.adapter, self.sol = scope, repository, adapter, sol
        self.clock, self.after_commit = clock, after_commit or (lambda opid: None)
        self.active_started=None
        self.base_seconds=0.
        self.config = {"configurable": {"thread_id": "scholar-v5-"+scope.key}, "recursion_limit": max(1000, adapter.config.max_tokens*6)}
        builder = StateGraph(State)
        builder.add_node("reconcile", self.reconcile)
        builder.add_node("decide", self.decide)
        builder.add_node("prepare_decision", self.prepare_decision)
        builder.add_node("perform", self.perform)
        builder.add_node("pause", self.pause)
        builder.add_edge(START, "reconcile")
        builder.add_conditional_edges("reconcile", self.route,
            {"decide":"decide", "prepare_decision":"prepare_decision", "perform":"perform", "pause":"pause", "end":END})
        builder.add_edge("prepare_decision", "decide")
        builder.add_edge("decide", "reconcile")
        builder.add_edge("perform", "reconcile")
        builder.add_edge("pause", "reconcile")
        self.graph = builder.compile(checkpointer=checkpointer)

    def check_scope(self, state):
        if state.get("scope_key") != self.scope.key:
            raise PermissionError("Checkpoint belongs to another user/project/session")

    def exhausted(self, state):
        c, u = self.adapter.config, state["usage"]
        return (u["cost_usd"]+u.get("uncertain_usd",0) >= c.max_cost_usd or
            u["input_tokens"]+u["output_tokens"]+u.get("uncertain_tokens",0) >= c.max_tokens or
            u["tools"] >= c.max_tool_calls or u["seconds"] >= c.timeout_seconds)

    def reconcile(self, state):
        self.check_scope(state)
        revision, session = self.repo.load(self.scope)
        if revision != state["revision"]:
            # Only a known receipt may bridge the crash-after-commit window.
            # Historical checkpoints cannot silently use a future document/card.
            pending = state.get("pending") or state.get("pending_decision")
            op = self.repo.operation(self.scope, pending) if pending else None
            if not op or op["status"] != "complete" or op["revision"]+1 != revision:
                raise RevisionConflict("Checkpoint/science revision mismatch")
            return {}  # perform recovers that operation's receipt, not its effects.
        usage=dict(state['usage'])
        if self.active_started is not None:
            usage['seconds']=max(usage['seconds'],self.base_seconds+self.clock()-self.active_started)
        result = {"progress": project(session, scope=self.scope),"usage":usage}
        if (getattr(self.sol, 'langgraph_live', False) and not state.get('pending') and
                not state.get('pending_decision') and not state.get('finalization_attempted') and
                state.get('reason') in {'stagnation', 'admission_refused', 'synthesis_reserve', 'section_save_reserve'} and
                not self.exhausted({**state, 'usage': usage}) and
                # A drafting request publishes its saved sections locally: no free-text synthesis,
                # except the save of the current section that its reserve was kept for.
                (not getattr(self.adapter, 'writing_active', lambda s: False)(session) or
                    # The save of a writable section only: its reserve (run b748605d) or a
                    # stall behind repeated readings (run 152fc86a). A section still being
                    # prepared is left to the next segment, its cards and proofs saved.
                    (state.get('reason') in {'section_save_reserve', 'stagnation'} and
                        getattr(self.adapter, 'save_ready', lambda s: False)(session)))):
            result.update(finalizing=True, stop_cause=state['reason'], reason=None)
        return result

    def route(self, state):
        if state.get("status") in {"completed", "partial"}:
            return "end"
        if state.get("pending"):
            return "perform"
        if state.get("pending_decision"):
            return "decide"
        if state.get("reason") or self.exhausted(state):
            return "pause"
        reserve = self.sol.reservation()
        if (state['usage']['cost_usd']+reserve['cost_usd'] > self.adapter.config.max_cost_usd or
                state['usage']['input_tokens']+state['usage']['output_tokens']+reserve['tokens'] > self.adapter.config.max_tokens):
            return "pause"
        return "prepare_decision"

    def prepare_decision(self, state):
        revision = state['revision']
        opid = fingerprint([state["job_id"],"decision",state["slice"],state["sequence"],revision])
        self.repo.begin(self.scope,opid,revision,"decision",{"model":"gpt-6.1-sol"})
        return {"pending_decision":opid}

    def assemble(self, state, session, inputs):
        """Inputs of one decision: prepared context plus the control message, within
        max_context_chars as actually sent. Local only: no provider call."""
        if getattr(self.adapter, 'task_profile', {}).get('kind', 'science') != 'science':
            # The plan profile already contains the actual request and saved plan.
            # No scientific coverage dump or historical manuscripts are needed.
            if len(json.dumps(inputs, ensure_ascii=False)) > self.adapter.config.max_context_chars:
                raise ValueError('Administrative context budget; saved plan retained')
            return inputs
        workflow = self.adapter.workflow_view(session) if hasattr(self.adapter, 'workflow_view') else {}
        control = {
            **({'v5_workflow': workflow} if workflow else {}),
            'request':request_packet(session), 'current_goal':session.get('turn_goal', {}),
            'no_gain_streak':state.get('no_gain_streak',0),
            'recent_no_gain':state.get('recent_no_gain', [])[-4:],
            'control': 'Finaliser honnêtement avec les preuves disponibles ; signaler les tâches restantes.' if state.get('finalizing') else
                ('Rédaction en cours : enregistrer la section du dossier par write_section(save) dès que sa préparation est '
                 'suffisante (v5_workflow.current_section) ; une lecture seulement pour une lacune nommée de cette section.'
                 if getattr(self.adapter, 'writing_active', lambda s: False)(session) else
                'Ne pas reformuler le cadrage ni relire la notice sans nouveau besoin. Choisir librement une tâche utile : pagination restante, lecture, analyse, recherche complémentaire motivée ou finalisation.')}
        # The control travels as a JSON string inside the inputs: measure it as
        # sent, escaping included (run 7de2fab6: +1,418 chars, 102 over the limit).
        limit = self.adapter.config.max_context_chars
        embedded = lambda value: len(json.dumps(json.dumps(value,ensure_ascii=False),ensure_ascii=False))
        room = limit-len(json.dumps(inputs,ensure_ascii=False))-embedded(control)-120
        view_budget = min(10000,max(500,room))
        while True:
            try:
                control['v5_work_state'] = model_work_view(state['progress'], budget=view_budget)
            except ValueError as exc:
                if view_budget == min(10000,max(500,room)):
                    raise  # As before the fix: the targeted works do not fit at all.
                # Shrinking would omit a targeted work: refuse, nothing is dropped.
                raise ValueError('Local context budget; scientific sources retained in the repository') from exc
            sent = [*inputs, {'role':'developer','content':json.dumps(control,ensure_ascii=False)}]
            over = len(json.dumps(sent,ensure_ascii=False))-limit
            if over <= 0 or view_budget <= 500:
                break
            view_budget = max(500, view_budget-over-200)  # The work view yields first.
        inputs = sent
        if over > 0:
            raise ValueError('Local context budget; scientific sources retained in the repository')
        return inputs

    def decide(self, state):
        actual_revision, session = self.repo.load(self.scope)
        opid = state['pending_decision']
        prior = self.repo.operation(self.scope,opid)
        revision = prior['revision']
        if prior['status']=='complete':
            response = prior['response']
        else:
            if actual_revision != revision:
                raise RevisionConflict('Planner science version changed')
            if prior['status']=='running':
                return {"reason":"uncertain_model_call", "status":"running", "pending_decision":None}
            last = self.repo.operation(self.scope, state["last_operation"]) if state.get("last_operation") else None
            # Context assembly is LOCAL. A failure here must never be recorded
            # as an uncertain paid provider call.
            session['v5_finalization_mode'] = bool(state.get('finalizing'))
            toolkit, inputs = self.adapter.prepare(session, last["response"].get("output") if last else None)
            inputs = self.assemble(state, session, inputs)
            if not self.repo.claim(self.scope,opid):
                # The provider may have processed a crashed request. Never call
                # it again just because a graph checkpoint was not committed.
                return {"reason":"uncertain_model_call", "status":"running", "pending_decision":None}
            started = self.clock()
            if hasattr(self.sol, "bind"):
                self.sol.bind(session, state, opid)
            decision = self.sol.choose(inputs, self.adapter.tools() if hasattr(self.adapter, "tools") else available_tools(),
                state["progress"], instructions=self.adapter.instructions)
            if decision.model != "gpt-6.1-sol":
                raise ValueError("Unexpected model")
            if min(decision.cost_usd,decision.input_tokens,decision.output_tokens,decision.seconds)<0:
                raise ValueError('Usage cannot be negative')
            if decision.sent_calls and decision.response_received:
                self.adapter.delivered(toolkit, inputs)
            elif hasattr(self.adapter, 'undelivered'):
                self.adapter.undelivered(toolkit, inputs)
            # Progress of this decision starts after the receipts it confirms for the
            # previous one: a late receipt cannot credit a repeated re-read (run f147eded).
            # Items are credited once, when prepared (adapter.novel_delivery).
            start = (project(session, scope=self.scope) if hasattr(self.adapter, 'novel_delivery') else state['progress'])
            response = {"decision": asdict(decision), "seconds":max(decision.seconds,self.clock()-started),
                'request_sent':bool(decision.sent_calls), 'response_received':decision.response_received,
                'evidence_prepared':True, 'evidence_presented':bool(decision.sent_calls and decision.response_received),
                "before_signature":informative_signature(start)}
            self.repo.finish(self.scope, opid, revision, session, response)
            self.after_commit(opid)
        decision = Decision(**response["decision"])
        usage = dict(state["usage"])
        usage["cost_usd"] += decision.cost_usd
        usage["input_tokens"] += decision.input_tokens
        usage["output_tokens"] += decision.output_tokens
        usage["seconds"] += response["seconds"]
        usage["llm_calls"] += decision.sent_calls
        usage["tools"] += decision.native_calls
        usage["uncertain_usd"] = usage.get("uncertain_usd", 0)+decision.uncertain_usd
        usage["uncertain_tokens"] = usage.get("uncertain_tokens", 0)+decision.uncertain_tokens
        # Scientific proposals live with V4 science, not in every checkpoint.
        action_id = fingerprint([opid,"tool"])
        self.repo.begin(self.scope, action_id, revision+1, "action", asdict(decision))
        # Further calls of the same response: begun one at a time, at their revision.
        batch = [{"id": fingerprint([opid, "tool", i+1]), "request": asdict(Decision(**call))}
            for i, call in enumerate(decision.calls)]
        return {"revision":revision+1,"usage":usage,"slice_calls":state["slice_calls"]+1,
            "batch":batch,"batch_productive":False,
            "step_start_signature":response['before_signature'],
            "sequence":state["sequence"]+1,"pending":action_id,"pending_decision":None,
            'finalization_attempted': bool(state.get('finalization_attempted') or state.get('finalizing')),
            "reason":decision.arguments.get("reason", "model_requested_pause") if decision.tool=="pause" else None}

    def perform(self, state):
        opid = state["pending"]
        op = self.repo.operation(self.scope, opid)
        if not op:
            raise RevisionConflict("Missing operation receipt")
        decision = Decision(**op["request"])
        revision, session = self.repo.load(self.scope)
        if op["status"] == "complete":
            response, next_revision = op["response"], op["revision"]+1
        else:
            if revision != op["revision"]:
                raise RevisionConflict("Operation's document revision changed")
            started = self.clock()
            if hasattr(self.adapter, "meter"):
                session["v5_current_operation"] = opid
            claimed = self.repo.claim(self.scope,opid)
            if not claimed and not self.adapter.local(session, decision):
                return {"pending":None,"reason":"uncertain_tool_call"}
            if decision.tool=="pause":
                output = {"paused":True}
                key,reuse_reason=None,None
            elif self.exhausted(state):
                output = {"not_executed":True,"reason":"job_budget"}
                key,reuse_reason=None,None
            elif state.get('batch_productive') and depends_on_batch_results(decision):
                output = {"not_executed":True,"reason":"deferred_until_results_reviewed",
                    "guidance":"Les autres appels de cette réponse ont produit de nouveaux résultats : les examiner, puis renouveler cet appel."}
                key,reuse_reason=None,None
            else:
                # Same flag as the decision node: checks of a forced synthesis must not cost a call.
                session['v5_finalization_mode'] = bool(state.get('finalizing'))
                if hasattr(self.adapter, 'before_reuse'):
                    self.adapter.before_reuse(session, decision)
                key,output,reuse_reason=reusable(session,decision,self.repo.completed_actions(self.scope))
                if output is None:
                    if not useful_need(state['progress'],decision):
                        output={'not_executed':True,'reason':'scientific_purpose_required'}
                    else:
                        output = self.adapter.execute(session, decision,
                            remaining_seconds=max(.001,self.adapter.config.timeout_seconds-state["usage"]["seconds"]))
            if hasattr(self.adapter, "finish_exchange"):
                self.adapter.finish_exchange(session, decision, output)
            after=project(session,scope=self.scope)
            # A receipt for the previous result may have arrived during decide.
            # It cannot turn an identical repeated operation into new work.
            no_gain=reuse_reason=='same_operation' or informative_signature(after)==state['step_start_signature']
            # Preparing a new local result is useful orchestration work, although
            # its passages are not yet presented or examined. Identical replays
            # never earn this credit, and an empty/refused result does not either.
            # A search that only returns already known works is not progress: for
            # discovery tools only a changed scientific state (new candidates) counts.
            novel = getattr(self.adapter, 'novel_delivery', None)
            if novel is not None:
                # Credit only items never delivered in this request (run f147eded:
                # five decisions re-sent 229 known claims and were counted as progress).
                context_prepared = (not reuse_reason and decision.tool not in DISCOVERY_TOOLS
                    and novel(session, decision, output))
            else:
                context_prepared = (not reuse_reason and not output.get('error') and not output.get('not_executed')
                    and decision.tool not in DISCOVERY_TOOLS
                    and (any(output.get(k) for k in ('articles','claims','structured_measurements'))
                        or any(r.get('passages') for k in ('reading','indexes') for r in output.get(k, []))))
            if context_prepared:
                no_gain=False
            response = {"output":output,"seconds":self.clock()-started,'semantic_key':semantic_key(session, decision) if key else None,
                'reuse_reason':reuse_reason,'no_gain':no_gain}
            next_revision = self.repo.finish(self.scope,opid,revision,session,response)
            self.after_commit(opid)
        usage = dict(state["usage"])
        usage["tools"] += int(decision.tool not in {"pause", "v5_continue", "v5_reply"} and not response["output"].get("not_executed") and not response.get('reuse_reason'))
        usage["seconds"] += response["seconds"]
        published = (decision.tool in {"finalize_response", "v5_reply", "write_section"}
            or response['output'].get('outcome') in {'operation_saved', 'human_validation_required'}) and response["output"].get("published")
        scientific = dict(usage.get('scientific_operations', {}))
        output = response['output']
        if not response.get('reuse_reason') and not output.get('error') and not output.get('not_executed'):
            kind = ('reads' if decision.tool=='analyze_article' and output.get('reading') else
                'saved_analyses' if decision.tool=='analyze_article' and output.get('saved') else
                'extractions' if decision.tool=='get_fulltext' and output.get('access_level')=='fulltext' else None)
            if kind:
                scientific[kind] = scientific.get(kind, 0)+1
        usage['scientific_operations'] = scientific
        gathers = getattr(self.adapter, 'gathers', lambda d: not depends_on_batch_results(d) and d.tool != 'pause')
        productive = bool(state.get('batch_productive') or (not response.get('reuse_reason') and not output.get('error')
            and not output.get('not_executed') and gathers(decision)))
        # During a forced synthesis, saving the plan or the comparison it presents
        # is its preparation, not a failed publication.
        prepared = bool(state.get('finalizing') and decision.tool in {'manage_outline', 'compare_articles'}
            and output.get('saved') and not response.get('reuse_reason'))
        interrupted = (state.get('reason') or output.get('v5_stop_reason') or output.get('reason') == 'job_budget'
            or published or (state.get('finalizing') and not published and not prepared))
        if state.get('batch') and not interrupted:
            # Next call of the same response. Progress and stagnation are judged
            # once per Sol decision, after its last call.
            following = state['batch'][0]
            self.repo.begin(self.scope, following['id'], next_revision, 'action', following['request'])
            return {"revision":next_revision,"usage":usage,"pending":following['id'],"last_operation":opid,
                "batch":state['batch'][1:],"batch_productive":productive,"status":"running"}
        current = self.repo.load(self.scope)[1]
        projection=project(current,scope=self.scope)
        sufficient=bool(current.get('last_response',{}).get('coverage_sufficient'))
        # Pending candidate/depth cues inform Sol, never veto honest publication.
        # No graph assertion of sufficient coverage without V4's publication flag.
        streak,stalled=stagnant(state,no_gain=response.get('no_gain',False),
            reuse_reason=response.get('reuse_reason'),purpose=decision.purpose)
        if getattr(self.sol, 'langgraph_live', False):
            stalled = streak >= 3  # Feedback after the first miss; one recovery opportunity before synthesis.
        recent = [*state.get('recent_no_gain', []), {'tool':decision.tool, 'reused':response.get('reuse_reason'),
            'purpose':decision.purpose[:300]}][-4:] if response.get('no_gain') else []
        stop = state.get('reason') or response['output'].get('v5_stop_reason') or (
            'stagnation' if stalled and decision.tool not in {'pause','finalize_response'} else None)
        if (state.get('finalizing') and state.get('stop_cause') == 'stagnation' and decision.tool == 'write_section'
                and output.get('saved') and not published):
            # The stalled section is saved: drafting resumes with the next one.
            return {"revision":next_revision,"usage":usage,"pending":None,"last_operation":opid,"batch":[],"batch_productive":False,
                "response_ref":state.get("response_ref"),"no_gain_streak":0,"last_purpose":decision.purpose,'recent_no_gain':[],
                "reason":state.get('reason'),"finalizing":False,"finalization_attempted":False,"stop_cause":"","status":"running"}
        if state.get('finalizing') and not published:
            # A repairable refusal returns its reasons to Sol for one more forced
            # synthesis, while attempts and admission allow it. The adapter stops
            # at max_finalization_attempts; a refused admission pauses the graph.
            repairable = ((decision.tool == 'finalize_response' and output.get('rejection_code') or prepared)
                and not output.get('v5_stop_reason') and not self.exhausted({**state, 'usage': usage}))
            stop = stop or (None if repairable else state.get('stop_cause', 'finalization_failed'))
        return {"revision":next_revision,"usage":usage,"pending":None,"last_operation":opid,"batch":[],"batch_productive":False,
            "response_ref":opid if published else state.get("response_ref"),
            "no_gain_streak":streak,"last_purpose":decision.purpose,
            'recent_no_gain':recent, "reason":stop or (state.get('stop_cause') if published else None),
            "status":("completed" if output.get('administrative_complete') or sufficient and not projection['counts']['missing'] else "partial") if published else "running"}

    def pause(self, state):
        reserve = self.sol.reservation()
        cannot_reserve = (state['usage']['cost_usd']+reserve['cost_usd'] > self.adapter.config.max_cost_usd or
            state['usage']['input_tokens']+state['usage']['output_tokens']+reserve['tokens'] > self.adapter.config.max_tokens)
        reason = state.get("reason") or ("job_budget" if self.exhausted(state) or cannot_reserve else "requested_pause")
        payload = {"reason":reason,"counts":state["progress"]["counts"],
            "pending_task_ids":list(state["progress"]["tasks"]),
            "message":self.partial_message(state,reason)}
        resume = interrupt(payload)
        # An uncertain model call continues only once the consultant reconciled it and the
        # canonical ledger holds no reserved or uncertain call any more (its maximum is counted).
        reconciled = (reason == "uncertain_model_call" and isinstance(resume, dict)
            and resume.get("reconciled") is True and self.calls_settled())
        if reason in {"job_budget","uncertain_model_call","uncertain_tool_call"} and not reconciled:
            raise ValueError("A bounded budget or an uncertain paid call cannot be reset by resume")
        if not isinstance(resume, dict) or resume.get("continue") is not True:
            raise ValueError("Explicit continuation is required")
        return {"slice":state["slice"]+1,"slice_calls":0,"reason":None,"status":"running","no_gain_streak":0,
            'finalizing':False, 'finalization_attempted':False, 'stop_cause':''}

    @staticmethod
    def partial_message(state,reason):
        c=state['progress']['counts']
        scoped = [a for a in state['progress']['articles'].values() if a['in_request_scope']]
        causes = {'job_budget': 'Un plafond global est atteint', 'admission_refused': 'La requête prévue dépasse les ressources restantes',
            'synthesis_reserve': 'Les ressources restantes sont réservées à la synthèse',
            'section_save_reserve': "Les ressources restantes étaient réservées à la sauvegarde de la section en cours",
            'stagnation': 'Les dernières opérations ne produisaient plus de progrès',
            'uncertain_model_call': 'Un appel fournisseur reste à rapprocher'}
        if scoped and state['progress']['goal'].get('deliverable') in {'deep_reading', 'article_analysis'}:
            operations=state.get('usage', {}).get('scientific_operations', {})
            activity=(f" Cette exécution : {operations.get('reads',0)} opérations de lecture ciblée, "
                f"{operations.get('extractions',0)} extractions, {operations.get('saved_analyses',0)} sauvegardes ou enrichissements de fiche.")
            rows = [f"{a.get('title', a['article_id'])} : document {a['document']}, "
                f"{len(a['extracted_pages'])}/{a['page_count'] or '?'} pages extraites, "
                f"analyse {'partielle disponible' if a['analysis_saved'] else 'non sauvegardée'}, "
                f"{len(a['saved_evidence_ids'])} preuves sauvegardées" +
                (f", extraction à reprendre page {a['next_page']}" if a['next_page'] else '') for a in scoped]
            return '. '.join(rows)+'. '+causes.get(reason, 'La poursuite est suspendue')+(
                '. Aucune nouvelle analyse scientifique validée publiée dans cette exécution.'+activity+' '
                'Les fiches existantes restent consultables ; approfondissement et tâches restantes conservés.')
        count=f"{c['proposed']} publications proposées"
        if c['target'] is not None:count+=f" sur {c['target']} demandées ; {c['missing']} restent à proposer"
        return f"{count}. Les documents et preuves acquis sont conservés. " + (
            "Le budget de cette exécution est atteint ; le travail reste incomplet." if reason=='job_budget' else
            "La poursuite est suspendue ; des lectures ou évaluations restent à effectuer.")

    def start(self, *, historical_usage=None):
        self.repo.load(self.scope)
        if self.graph.get_state(self.config).values:
            raise ValueError("Existing thread: resume it instead of resetting its budget")
        usage = {"cost_usd":0.,"input_tokens":0,"output_tokens":0,"seconds":0.,"tools":0,"llm_calls":0,
            **(historical_usage or {})}
        revision = self.repo.load(self.scope)[0]
        self.active_started,self.base_seconds=self.clock(),usage['seconds']
        return self.graph.invoke({"scope_key":self.scope.key,"revision":revision,
            "job_id":fingerprint([self.scope.key,revision]),"slice":0,"slice_calls":0,"sequence":0,
            "usage":usage,"pending":None,"pending_decision":None,"last_operation":None,"status":"running","reason":None},
            self.config,durability="sync")

    def calls_settled(self):
        """No reserved or uncertain paid call left in the canonical ledger of this run."""
        meter = getattr(self.adapter, "meter", None) or getattr(self.sol, "meter", None)
        if meter is None or not hasattr(meter, "snapshot"):
            return False
        usage = meter.snapshot()
        return not any(usage.get(k) for k in ("unresolved_usd", "unresolved_tokens", "reserved_usd", "reserved_tokens"))

    def resume(self, reconciled=False):
        self.repo.load(self.scope)  # Authorise before reading checkpoint content.
        snapshot = self.graph.get_state(self.config)
        self.check_scope(snapshot.values)
        self.active_started,self.base_seconds=self.clock(),snapshot.values['usage']['seconds']
        if snapshot.values.get('status')=='partial' and not snapshot.interrupts:
            return self.graph.invoke({'status':'running','reason':None,'no_gain_streak':0,
                'finalizing':False, 'finalization_attempted':False, 'stop_cause':'',
                'slice':snapshot.values['slice']+1,'slice_calls':0},self.config,durability='sync')
        if snapshot.interrupts:
            command = {"continue": True, **({"reconciled": True} if reconciled else {})}
            return self.graph.invoke(Command(resume=command),self.config,durability="sync")
        # Resume a crashed node, without replaying a completed operation.
        return self.graph.invoke(None,self.config,durability="sync")
