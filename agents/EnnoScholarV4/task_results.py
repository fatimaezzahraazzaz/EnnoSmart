"""Durable task receipts shared by generation, validation, recovery and accounting.

Workers only return model responses. The owning agent thread records receipts and
applies the ordinary validators. A generated proposal is never a saved artifact.
No database, provider or model dependency lives in this module.
"""
from __future__ import annotations

import copy
import hashlib
import json
import uuid
from types import SimpleNamespace
from concurrent.futures import wait, FIRST_COMPLETED


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def applies_now(state, row):
    from .instructions import applies_now as in_force
    return in_force(state, row)


def source_signature(state, registry, ids):
    """Scientific dependencies, not display order or a draft's changing version."""
    return fingerprint({"sources": {aid: {
        "version": registry[aid].source_version, "analysis": registry[aid].analysis,
        "assessment": registry[aid].assessment, "verified": registry[aid].verified,
        "override": registry[aid].selection_override,
        "identity": [registry[aid].title, registry[aid].authors, registry[aid].year, registry[aid].doi,
            registry[aid].metadata_errors],
        "document": fingerprint({key: registry[aid].fulltext.get(key) for key in (
            "text", "chunks", "document_sha256", "document_version_id", "source_version", "source_kind", "url")}),
    } for aid in sorted(set(ids)) if aid in registry},
        "diagnostic": state.get("diagnostic_link"),
        # Repeating the same instruction with a new message UUID does not change
        # the scientific work. Provenance remains intact in the instruction log.
        "instructions": sorted({json.dumps({k: row.get(k) for k in ("text", "scope", "target_ids")}, sort_keys=True)
            for row in state.get("consultant_instructions", []) if applies_now(state, row)})})


def collect_finished(futures, consume, check):
    """Drain every completed sibling even when a deadline/cancellation interrupts.

    consume runs in the owner thread and isolates validation failures in receipts.
    Futures left running are retained by the caller for final harvesting.
    """
    pending = list(futures)
    try:
        while pending:
            ready = [future for future in pending if future.done()]
            for future in ready:
                pending.remove(future)
                consume(future)
            if pending:
                check()
                wait(pending, timeout=.2, return_when=FIRST_COMPLETED)
    finally:
        # Checking the clock must never discard a sibling which already finished.
        for future in list(pending):
            if future.done():
                pending.remove(future)
                consume(future)



# What a section repair needs from the refused proposal. Claims, numbers and the argument
# summary stay in the merge base (local_repair.merge); echoing them doubled the repair
# input (up to 5 000 characters of claims) without helping to locate the refused passage.
REPAIR_ECHO_FIELDS = ("action", "section_id", "instructions", "text", "article_ids", "evidence_ids", "project_evidence_ids")


def section_proposal(call, attempt):
    """The refused write_section call as the model must repair it: the complete proposal
    that was validated (after a paragraph patch, the merged text, not the patch alone:
    run 95f3507d repaired a title it could not see because only its patch was echoed)."""
    arguments = attempt.get("arguments")
    if call.get("name") != "write_section" or not isinstance(arguments, dict) or not arguments.get("text"):
        return call
    call["arguments"] = json.dumps({key: arguments[key] for key in REPAIR_ECHO_FIELDS if arguments.get(key) is not None},
        ensure_ascii=False)
    return call

class TaskResults:
    def __init__(self, state, config, usage, durations, checkpoint=lambda row: None, call_records=None):
        self.state, self.config, self.usage, self.durations = state, config, usage, durations
        # Per-request records of the metered client (model requested, price, reservation,
        # prefix fingerprints, ledger status), keyed by attempt id.
        self.call_records = call_records if call_records is not None else {}
        self.rows = state.setdefault("task_results", {})
        self.checkpoint = checkpoint
        self.execution = uuid.uuid4().hex
        self.calls = []
        for task in self.rows.values():
            if task["status"] == "running":
                task["status"] = "interrupted"
                if task["attempts"]:
                    task["attempts"][-1].update(status="interrupted",
                        refusal={"error": "Worker interrompu avant confirmation ; usage fournisseur inconnu."})
                    task["refusal"] = task["attempts"][-1]["refusal"]
                self.checkpoint(task)

    def task(self, kind, target, signature):
        key = fingerprint([kind, target, signature])
        return self.rows.setdefault(key, {"id": key, "kind": kind, "target": target,
            "signature": signature, "status": "pending", "content": None, "refusal": None,
            "evidence_ids": [], "saved": None, "attempts": []})

    def begin(self, task, *, evidence_ids=(), input_signature="", reserve=0):
        attempt = {"id": uuid.uuid4().hex, "execution": self.execution, "status": "running",
            "content": [], "refusal": None, "error_type": None, "usage": None,
            "duration_seconds": None, "input_signature": input_signature,
            "reserved_cost_usd": reserve,
            "evidence_ids": list(evidence_ids), "saved": None, "accounted": False}
        task["attempts"].append(attempt)
        task.update(status="running", refusal=None)
        self.calls.append(attempt)
        self.checkpoint(task)
        return attempt

    def receive(self, task, attempt, response=None, *, seconds=0, reserve=0, error=None, web_calls=0):
        """One accounting path for all calls, including invalid and interrupted ones."""
        if attempt["accounted"]:
            return
        raw = getattr(response, "usage", None)
        raw = raw.model_dump(exclude_none=True) if hasattr(raw, "model_dump") else raw
        outputs = getattr(response, "output", None) or []
        content = [item.model_dump(exclude_none=True) if hasattr(item, "model_dump") else dict(item) for item in outputs]
        # Encrypted reasoning is transport state, not scientific content to retain.
        content = [item for item in content if item.get("type") != "reasoning"]
        status = getattr(response, "status", "completed") if response is not None else "failed"
        # Scientific refusals are kept verbatim by reject(). Transport exception
        # strings can contain credentials or upstream URLs; retain safe fields.
        diagnostic = None
        if error is not None:
            from .stops import technical_diagnostic
            diagnostic = technical_diagnostic(error)
        refusal = {"error": "Incident technique interne." if diagnostic["category"] == "internal" else "Appel fournisseur non terminé.",
            "error_type": type(error).__name__, "diagnostic": diagnostic} if error is not None else None
        if response is not None and status != "completed":
            detail = getattr(response, "error", None) or getattr(response, "incomplete_details", None)
            code = detail.get("code") if isinstance(detail, dict) else getattr(detail, "code", None)
            reason = detail.get("reason") if isinstance(detail, dict) else getattr(detail, "reason", None)
            refusal = {"error": f"Responses {status}", "code": code, "reason": reason}
        attempt.update(content=content, usage=copy.deepcopy(raw), duration_seconds=max(0, seconds),
            status="generated" if response is not None and status == "completed" and error is None else "failed",
            refusal=refusal, error_type=type(error).__name__ if error else None, diagnostic=diagnostic, accounted=True,
            response_id=getattr(response, "id", None), provider_status=status,
            request_id=getattr(error, "request_id", None) or getattr(response, "_request_id", None),
            http_status=getattr(error, "status_code", None))
        task.update(status=attempt["status"], content=content, refusal=refusal,
            evidence_ids=attempt["evidence_ids"])
        from .finance import BudgetRefused
        from .pricing import resolve, cost as priced, legacy_cost, PriceUnknown
        record = self.call_records.pop(attempt["id"], None) or {}
        model = record.get("model_requested") or self.config.model
        try:
            price = resolve(self.config, model)
        except PriceUnknown:
            price = None
        breakdown = priced(raw, price, web_calls=web_calls)
        tokens = breakdown.get("tokens") or {}
        inp, out = tokens.get("input_tokens", 0), tokens.get("output_tokens", 0)
        self.usage["input_tokens"] += inp
        self.usage["output_tokens"] += out
        for key, source in (("cached_input_tokens", "cache_read_tokens"), ("cache_write_tokens", "cache_write_tokens"),
                ("ordinary_input_tokens", "ordinary_input_tokens"), ("reasoning_tokens", "reasoning_tokens")):
            self.usage[key] = self.usage.get(key, 0) + tokens.get(source, 0)
        attempt.update(model_requested=model, model_resolved=getattr(response, "model", None) or record.get("model_resolved"),
            service_tier=getattr(response, "service_tier", None) or record.get("service_tier"),
            price_version=price.version if price else None, cost=breakdown,
            **{key: record[key] for key in ("cache_probe", "ledger", "reserved_usd") if key in record})
        if isinstance(error, BudgetRefused):
            # Refused by the ledger before the request left: nothing sent, nothing reserved.
            attempt.update(status="refused_before_call", estimated_cost_usd=0., admission=error.explanation,
                refusal={"error": "Budget insuffisant : appel non émis.", "admission": error.explanation})
            task.update(status="refused_before_call", refusal=attempt["refusal"])
            return
        cost = breakdown["usd"] or 0.
        self.usage["estimated_cost_usd"] += cost
        attempt["estimated_cost_usd"] = cost
        if raw and self.config.input_price and self.config.output_price:
            # Former formula (input and output only), kept for audits of the receipts.
            attempt["legacy_estimated_cost_usd"] = legacy_cost(raw, self.config.input_price, self.config.output_price)
        if breakdown["usd"] is None:
            # No amount can be attested (usage missing, price unknown): the reservation
            # stays counted; no precise figure is shown for this call.
            self.usage["estimate_incomplete"] = True
            self.usage["unreported_reserved_cost_usd"] += reserve
            attempt["unreported_reserved_cost_usd"] = reserve
        elif breakdown["status"] != "computed":
            self.usage["estimate_unverified"] = True
        self.durations.append((max(0, seconds), out))

    def parse(self, task, attempt, tool, schema=None):
        if attempt["status"] != "generated":
            self.checkpoint(task)
            return None
        try:
            calls = [item for item in attempt["content"] if item.get("type") == "function_call" and item.get("name") == tool]
            if len(calls) != 1:
                raise ValueError(f"Un seul appel {tool} est attendu pour cette tâche.")
            args = json.loads(calls[0]["arguments"])
            if schema is not None:
                args = schema.model_validate(args).model_dump()
            attempt["arguments"] = copy.deepcopy(args)
            return args
        except (ValueError, TypeError, KeyError) as exc:
            self.reject(task, attempt, {"error": str(exc), "error_type": type(exc).__name__})
            return None

    def reject(self, task, attempt, result):
        # Exact validator response, including rejected claims and numeric locators.
        attempt.update(status="refused", refusal=copy.deepcopy(result))
        task.update(status="refused", refusal=copy.deepcopy(result), saved=None)
        self.checkpoint(task)

    def saved(self, task, attempt, receipt, content=None):
        if not receipt.get("saved"):
            raise ValueError("Une sauvegarde exige une confirmation de l'artefact.")
        attempt.update(status="saved", saved=copy.deepcopy(receipt))
        task.update(status="saved", saved=copy.deepcopy(receipt), refusal=None,
            content=copy.deepcopy(content if content is not None else attempt.get("arguments")))
        self.checkpoint(task)

    def last_refusal(self, task):
        """The latest attempt with a complete call and a validator refusal, even when a
        later attempt was interrupted or failed in transport ([refused, refused, failed]).
        A task opened because its sources' cards were updated inherits the refusal of the
        task it replaces (run dcf5d05b: the proposal and its diagnosis were lost and the
        section regenerated in full); the repair is then validated on the current proofs."""
        if not task or task.get("status") == "saved":
            return None
        for attempt in reversed(task.get("attempts", [])):
            if attempt.get("status") == "refused" and attempt.get("refusal") and any(
                    item.get("type") == "function_call" for item in attempt.get("content", [])):
                return attempt
        previous = self.rows.get(task.get("inherited_from") or "")
        if previous is not None and previous is not task:
            return self.last_refusal(previous)
        return None

    def recovery_exchange(self, task):
        """Original call plus exact rejection; same protocol after a restart."""
        attempt = self.last_refusal(task)
        if attempt is None:
            return []
        calls = [copy.deepcopy(item) for item in attempt["content"] if item.get("type") == "function_call"]
        if task.get("kind") == "section":
            calls = [section_proposal(call, attempt) for call in calls]
        return [item for call in calls for item in (call, {
            "type": "function_call_output", "call_id": call["call_id"],
            "output": json.dumps(attempt["refusal"], ensure_ascii=False)})]

    def proposal(self, task):
        if task["status"] != "generated":
            return None
        for attempt in reversed(task["attempts"]):
            if attempt["status"] != "generated":
                continue
            if task["kind"] == "section":
                try:
                    if not any(item.get("name") == "write_section" and json.loads(item["arguments"]).get("action") == "save"
                            for item in attempt["content"] if item.get("type") == "function_call"):
                        continue
                except (ValueError, KeyError, TypeError):
                    continue
            return attempt
        return None

    @staticmethod
    def response(attempt):
        """A received response recovered locally, without another model call."""
        return SimpleNamespace(output=copy.deepcopy(attempt["content"]),
            usage=copy.deepcopy(attempt["usage"]), status="completed", output_text="")

    def interrupted(self, task, attempt, reserve, seconds):
        self.receive(task, attempt, seconds=seconds, reserve=reserve,
            error=RuntimeError("Appel interrompu ; usage fournisseur inconnu."))
        attempt["status"] = task["status"] = "interrupted"
        self.checkpoint(task)

    def complete(self, tasks):
        return bool(tasks) and all(task["status"] == "saved" and task.get("saved", {}).get("saved") for task in tasks)
