"""Automatic full-manuscript drafting: one persistent job, ordinary V4 runs as segments.

A consultant asking for the whole state of the art no longer has to send « continue la
rédaction » after each time limit (run 95f3507d stopped at 600 s with sections left).
The job lives in the session's own JSON (``data["writing_job"]``, no new table or
migration); every transition is written under the session's row lock (session.lock_rows).

Pure decisions only (no database, no provider): the service applies them.

- Spending: rebuilt from each segment's durable per-call ledger (job_ledger.py), as
  confirmed, reserved and uncertain amounts; never from a run's final usage summary.
- One budget for the whole manuscript, fixed at creation, never renewed automatically;
  every segment, the first included, is admitted call by call against what remains.
- Scope: a continuation resumes the progress the first segment opened (its id, targets,
  blocked sections and plan revision) and the request and goal the consultant gave; it is
  never rebuilt from a generic message, and no message is written in the consultant's name.
- Explicit stops: completed, cancelled, budget exhausted, no progress, segment limit,
  sections refused too often, plan or scope changed, executor unavailable, configuration
  different from the one the job was opened with.
"""
from __future__ import annotations

import copy
import hashlib
import uuid
from datetime import datetime

from .job_ledger import add, committed, totals

ACTIVE = {"running", "waiting"}
FINAL = {"completed", "cancelled", "failed", "stalled", "budget_exhausted", "not_applicable",
    "executor_unavailable", "configuration_mismatch", "exhausted"}
EMPTY = {"confirmed_usd": 0.0, "reserved_usd": 0.0, "uncertain_usd": 0.0}
# Run outcomes that say nothing about the request: the process or the provider stopped it.
INTERRUPTED = {"worker_interrupted", "upstream_error", "quota_or_rate_limit"}
NON_RETRYABLE = {"internal_error", "permission_conflict", "tool_configuration_error", "configuration_error"}
# Below this remainder no segment is started: the understanding call and one section
# reservation would not fit, and a segment that cannot save anything only spends.
MIN_SEGMENT_USD = 0.5
STOP_MESSAGES = {
    "completed": "Rédaction automatique terminée : toutes les sections demandées sont enregistrées.",
    "cancelled": "Rédaction automatique annulée à votre demande. Les sections enregistrées sont conservées.",
    "budget_exhausted": "Rédaction automatique arrêtée : budget du manuscrit atteint ({committed:.2f} $ engagés sur "
        "{budget:.2f} $ : {confirmed:.2f} $ confirmés, {uncertain:.2f} $ incertains). Les sections enregistrées sont "
        "conservées ; aucune reprise n'est lancée avec un nouveau budget sans votre demande.",
    "stalled": "Rédaction automatique arrêtée : aucune nouvelle section enregistrée sur {segments} segments consécutifs.",
    "segment_limit": "Rédaction automatique arrêtée : limite de {segments} segments atteinte.",
    "failed": "Rédaction automatique arrêtée : des sections restent refusées par les contrôles scientifiques.",
    "executor_unavailable": "Rédaction automatique arrêtée : l'exécuteur configuré (worker Celery) n'est pas disponible "
        "ou ne peut pas exécuter les tâches EnnoScholar V4. Aucun repli n'est fait ; les sections enregistrées sont conservées.",
    "configuration_mismatch": "Rédaction automatique arrêtée : le processus chargé de la suite n'a pas la configuration "
        "avec laquelle la tâche a été ouverte ({fields}). Aucun appel n'a été envoyé.",
}


def now():
    return datetime.utcnow().isoformat() + "Z"


def is_active(job):
    return bool(job) and job.get("status") in ACTIVE


def wants_full_draft(state, message):
    """The request opens or resumes a full-manuscript draft (same detection as the run,
    applied to a copy: nothing in the state changes)."""
    from .writing_progress import begin_writing
    probe = {"outline": copy.deepcopy(state.get("outline", {})),
        "document": {"writing_progress": copy.deepcopy(state.get("document", {}).get("writing_progress"))}}
    if probe["document"]["writing_progress"] is None:
        probe["document"].pop("writing_progress")
    progress = begin_writing(probe, message or "")
    return bool(progress and progress.get("mode") == "full")


def digest(value):
    return hashlib.sha256(str(value or "").encode()).hexdigest()[:16]


def fingerprint(config, database_url):
    """What a process must share with the one that opened the job to continue it: model,
    effective model of every stage (task_models routing), price catalogue content and
    price overrides, cache mode, API key and database. One-way digests only for the key,
    the database and the catalogue; never the values themselves."""
    import json
    import os
    from pathlib import Path
    from .pricing import DEFAULT_CATALOG, resolve
    from .routing import STAGES, task_models
    routing = task_models(config)
    effective = {stage: routing.get(stage, config.model) for stage in sorted(STAGES)}
    catalog = Path(getattr(config, "pricing_file", "") or os.getenv("ENNOSCHOLAR_V4_PRICING_FILE") or DEFAULT_CATALOG)
    overrides = {name: getattr(config, name) for name in ("input_price", "output_price", "cache_read_price",
        "cache_write_price", "web_call_price", "service_tier")}
    return {"model": config.model, "task_models": digest(json.dumps(effective, sort_keys=True)),
        "price_version": resolve(config).version,
        "price_catalog": digest(catalog.read_bytes().decode("utf-8", "replace") + json.dumps(overrides, sort_keys=True)),
        "prompt_cache_mode": config.prompt_cache_mode, "api_key": digest(config.api_key), "database": digest(database_url)}


def mismatch(expected, actual):
    return sorted(key for key in set(expected or {}) | set(actual or {}) if (expected or {}).get(key) != (actual or {}).get(key))


def new_job(config, message_id, first_run_id, config_fingerprint, kind="writing"):
    return {"id": "job_" + uuid.uuid4().hex, "kind": kind, "status": "running", "created_at": now(), "updated_at": now(),
        "request_message_id": message_id, "current_run_id": first_run_id,
        "budget_usd": float(config.research_job_budget_usd if kind == "research" else config.writing_job_budget_usd),
        "spent": dict(EMPTY), "segments": [],
        "max_segments": int(config.research_job_max_segments if kind == "research" else config.writing_job_max_segments),
        "stalled_segments": int(config.writing_job_stalled_segments),
        "max_section_attempts": int(config.writing_job_max_section_attempts), "no_progress": 0, "blocked": {},
        "cancel_requested": False, "error": None, "executor": None, "progress": None, "goal": None,
        "config_fingerprint": config_fingerprint}


def remaining(job):
    return max(0.0, job["budget_usd"] - committed(job.get("spent") or EMPTY))


def saved_ids(state):
    progress = (state.get("document") or {}).get("writing_progress") or {}
    return dict(progress.get("saved_draft_ids") or {})


def section_attempts(state, targets):
    """Validator refusals per target section, over every task and run of this session."""
    counts = {}
    for task in (state.get("task_results") or {}).values():
        if task.get("kind") == "section" and task.get("target") in targets:
            counts[task["target"]] = counts.get(task["target"], 0) + sum(
                a.get("status") == "refused" for a in task.get("attempts", []))
    return counts


def last_refusal(state, section_id):
    progress = (state.get("document") or {}).get("writing_progress") or {}
    errors = [e for e in progress.get("errors", []) if e.get("section_id") == section_id]
    return (errors[-1]["message"] if errors else "refus répétés des contrôles")[:400]


def segment_config(config, job):
    """The run's own cost cap: never more than what remains of the manuscript budget
    (the per-call admission of job_ledger enforces it in any case)."""
    from dataclasses import replace
    return replace(config, max_cost_usd=max(0.01, min(config.max_cost_usd, remaining(job))))


def segment_directive(job):
    """What a continuation resumes: the consultant's request and goal, and the progress
    opened by the first segment (never a new message, never a new scope)."""
    pinned = job.get("progress") or {}
    return {"job_id": job["id"], "kind": job.get("kind", "writing"),
        "request_message_id": job["request_message_id"], "progress_id": pinned.get("id"),
        "target_ids": list(pinned.get("target_ids") or []), "plan_revision": pinned.get("plan_revision"),
        "goal": copy.deepcopy(job.get("goal")), "blocked": dict(job.get("blocked") or {})}


def scope_problem(segment, state):
    """Why a continuation cannot resume its pinned scope (None when it can): checked before
    the segment's agent runs, so a changed scope never costs a call."""
    if segment.get("kind") == "research":
        progress = state.get("research_progress") or {}
        if segment.get("progress_id") and progress.get("id") != segment["progress_id"]:
            return "Recherche interrompue : la demande initiale a été remplacée."
        if segment.get("goal") and state.get("turn_goal") != segment["goal"]:
            return "Recherche interrompue : objectif modifié depuis la demande initiale."
        return None
    if not segment.get("progress_id"):
        return None  # Initialisation resumed: the run opens its progress from the original request.
    progress = (state.get("document") or {}).get("writing_progress") or {}
    if progress.get("id") != segment.get("progress_id") or progress.get("target_ids") != segment.get("target_ids"):
        return ("Rédaction automatique interrompue : la progression de rédaction a été remplacée depuis la demande ; "
            "aucune section n'a été ajoutée ni retirée du périmètre initial.")
    if (state.get("outline") or {}).get("plan_revision") != segment.get("plan_revision"):
        return "Rédaction automatique interrompue : le plan a changé depuis la demande ; confirmez les sections à reprendre."
    return None


def can_start_segment(job):
    """(True, None) or (False, final status) before a new segment is opened."""
    if job.get("cancel_requested"):
        return False, "cancelled"
    if remaining(job) < MIN_SEGMENT_USD:
        return False, "budget_exhausted"
    if len(job["segments"]) >= job["max_segments"]:
        return False, "segment_limit"
    if not (job.get("progress") or {}).get("id") and not job.get("initializing"):
        return False, "not_applicable"
    return True, None


def stop(job, status, **fields):
    reason = status
    job["status"] = "stalled" if status == "segment_limit" else status
    job["stop_reason"] = reason
    spent = job.get("spent") or EMPTY
    job["error"] = STOP_MESSAGES.get(reason, reason).format(committed=committed(spent), budget=job["budget_usd"],
        confirmed=spent["confirmed_usd"], uncertain=spent["uncertain_usd"],
        segments=job["stalled_segments"] if reason == "stalled" else job["max_segments"],
        fields=", ".join(fields.pop("mismatch", []) or []))
    job.update(fields, current_run_id=None, updated_at=now())
    if job.get("kind") == "research":
        messages = {"completed": "Recherche terminée : objectif et couverture vérifiés sur les fiches enregistrées.",
            "stalled": "Recherche arrêtée : aucune nouvelle connaissance documentaire sur plusieurs segments.",
            "segment_limit": "Recherche arrêtée : nombre maximal de segments atteint ; couverture encore partielle.",
            "budget_exhausted": "Recherche arrêtée : budget global engagé ; candidats et preuves conservés.",
            "cancelled": "Recherche annulée à votre demande ; candidats et preuves conservés."}
        if "error" not in fields and reason in messages:
            job["error"] = messages[reason]
    return job


def evaluate(job, state, run_id, run_status, run_data, saved_before):
    """Record the finished segment ``run_id`` (its ledger, its saved sections) and decide:
    waiting (continue) or a final status with its explicit cause. Once per segment."""
    if job.get("kind") == "research":
        from .research import job_evaluate
        return job_evaluate(job, state, run_id, run_status, run_data or {})
    if job.get("current_run_id") != run_id or any(s["run_id"] == run_id for s in job["segments"]):
        return job
    spent = totals((run_data or {}).get("ledger"), run_over=True)
    after = saved_ids(state)
    new = [sid for sid in after if sid not in saved_before]
    reason = (run_data or {}).get("reason")
    job["spent"] = add(job.get("spent") or EMPTY, spent)
    job["segments"].append({"run_id": run_id, "status": run_status, "reason": reason, "spent": spent,
        "saved_sections": new, "finished_at": now()})
    job["current_run_id"] = None
    job["updated_at"] = now()
    progress = (state.get("document") or {}).get("writing_progress")
    if run_status == "cancelled" or reason == "cancelled" or job.get("cancel_requested"):
        return stop(job, "cancelled")
    if reason == "budget_refused":
        return stop(job, "budget_exhausted")
    pinned = job.get("progress")
    # A late failure cannot erase an understanding or scope already checkpointed.
    # Pin before interpreting the outcome, including on interrupted first segments.
    if pinned is None and progress and progress.get("mode") == "full" and progress.get("target_ids"):
        job["progress"] = {"id": progress["id"], "target_ids": list(progress["target_ids"]),
            "plan_revision": progress.get("plan_revision")}
        job["goal"] = copy.deepcopy(state.get("turn_goal"))
        job["initializing"] = False
        pinned = job["progress"]
    diagnostic = (run_data or {}).get("diagnostic")
    if reason in NON_RETRYABLE or diagnostic and diagnostic.get("retryable") is False:
        return stop(job, "failed", error="Rédaction automatique arrêtée : incident technique ou conflit de permissions "
            f"({reason}). Objectif, progression et sections enregistrées conservés ; aucune relance payante automatique.",
            last_cause=reason, diagnostic=copy.deepcopy(diagnostic))
    technical = reason in INTERRUPTED or run_status == "failed"
    if technical:
        job["technical_failures"] = job.get("technical_failures", 0) + 1
        if job["technical_failures"] >= job["stalled_segments"]:
            return stop(job, "failed", error="Rédaction automatique arrêtée après incidents techniques répétés "
                f"({reason or run_status}). Aucune conclusion de blocage scientifique.",
                last_cause=reason or run_status, diagnostic=copy.deepcopy(diagnostic))
    else:
        job["technical_failures"] = 0
    if pinned is None and technical:
        # No durable scope exists yet. Resume the original request, with the same
        # budget and spending. Technical retries have their own bounded counter.
        job["initializing"] = True
        allowed, status = can_start_segment(job)
        if not allowed:
            return stop(job, status)
        job["status"] = "waiting"
        return job
    if pinned is None:
        job["initializing"] = False
        if not progress or progress.get("mode") != "full" or not progress.get("target_ids"):
            # The model read the request otherwise (no full draft opened): nothing to continue.
            return stop(job, "not_applicable", error=None)
        # The scope the continuations resume exactly: this progress, its targets and plan.
        job["progress"] = {"id": progress["id"], "target_ids": list(progress["target_ids"]),
            "plan_revision": progress.get("plan_revision")}
        job["goal"] = copy.deepcopy(state.get("turn_goal"))
    elif not progress or progress.get("id") != pinned["id"] or progress.get("target_ids") != pinned["target_ids"]:
        return stop(job, "failed", error="Rédaction automatique arrêtée : la progression de rédaction a changé "
            "(nouvelle demande ou nouveau périmètre) ; aucune section n'a été ajoutée au périmètre initial.")
    if progress.get("status") == "draft_complete":
        return stop(job, "completed")
    if (progress.get("status") == "plan_changed" or progress.get("plan_revision") != job["progress"]["plan_revision"]
            or (state.get("outline") or {}).get("plan_revision") != job["progress"]["plan_revision"]):
        return stop(job, "failed", error="Rédaction automatique arrêtée : le plan a changé depuis la demande ; "
            "confirmez les sections à reprendre.")
    targets = job["progress"]["target_ids"]
    attempts = section_attempts(state, targets)
    for sid in targets:
        if sid not in after and attempts.get(sid, 0) >= job["max_section_attempts"] and sid not in job["blocked"]:
            job["blocked"][sid] = last_refusal(state, sid)
    open_sections = [sid for sid in targets if sid not in after and sid not in job["blocked"]]
    if not open_sections:
        titles = {s["id"]: s.get("title", s["id"]) for s in (state.get("outline") or {}).get("sections", [])}
        return stop(job, "failed", error="Rédaction automatique arrêtée : " + "; ".join(
            f"section « {titles.get(sid, sid)} » refusée {attempts.get(sid, 0)} fois ({cause})"
            for sid, cause in job["blocked"].items()) + ". Les autres sections sont enregistrées ; corriger ou "
            "reformuler ces sections avant une nouvelle demande.")
    job["no_progress"] = 0 if new else job["no_progress"] + (0 if technical else 1)
    if job["no_progress"] >= job["stalled_segments"]:
        return stop(job, "stalled", last_cause=last_refusal(state, open_sections[0]))
    allowed, status = can_start_segment(job)
    if not allowed:
        return stop(job, status)
    job["status"] = "waiting"
    return job


def public(job):
    if not job:
        return None
    spent = job.get("spent") or EMPTY
    return {**{k: copy.deepcopy(job.get(k)) for k in ("id", "kind", "status", "created_at", "updated_at", "budget_usd", "segments",
        "max_segments", "blocked", "cancel_requested", "error", "stop_reason", "last_cause", "current_run_id", "executor",
        "paused_by_message_id", "requested_count")}, "spent": dict(spent), "spent_usd": round(committed(spent), 6),
        "remaining_usd": round(remaining(job), 6), "target_ids": (job.get("progress") or {}).get("target_ids")}
