"""V5 drafting mission: V4's automatic manuscript job (writing_job.py), with V5 limits.

The consultant asks once for the whole state of the art; each LangGraph run is a
segment bounded by its per-run ceiling AND the mission's remaining commitments.
The job continues until the sections are saved or a mission limit is reached.
The canonical V5 Budget ledger projects the job's spending in the same transaction.
V4's job provides: a segment limit; stalled segments; pinned scope; a
driver (thread or Celery) and recovery after a restart. V5 adds:

- a mission cap in tokens and a maximal duration, checked before each segment and
  bounding each segment's own token cap;
- scientific progress, not only saved sections: a segment that saved cards,
  proofs or relations is progress (a large corpus is prepared over segments);
- automatic continuation only once the plan is approved by the consultant
  (« en attente de validation » otherwise);
- a consultant-facing state and progress line, and the scientific quality of the
  manuscript (complete, or complete with documentary limits).

Pure decisions, as in V4: the service applies them under the session's lock.
"""
from __future__ import annotations

import copy
import os
from datetime import datetime

from .reuse import load
from .resources import balance, combine, apply_mission_usage, remaining_seconds
from .config import positive

base = load("_writing_job_base", "writing_job.py")
for _name in dir(base):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(base, _name))

STOP_MESSAGES = {**base.STOP_MESSAGES,
    "mission_tokens": "Rédaction automatique suspendue : plafond de la mission atteint ({tokens} tokens sur {max_tokens}). "
        "Les sections, fiches, preuves et comparaisons enregistrées sont conservées ; une autorisation supplémentaire est "
        "nécessaire pour poursuivre.",
    "mission_duration": "Rédaction automatique suspendue : durée maximale de la mission atteinte ({minutes} min). Le travail "
        "enregistré est conservé ; une autorisation supplémentaire est nécessaire pour poursuivre.",
    "budget_exhausted": base.STOP_MESSAGES["budget_exhausted"] + " Une autorisation supplémentaire est nécessaire pour poursuivre.",
    "mission_tools": "Mission suspendue : plafond global des outils atteint. Résultats conservés ; une autorisation supplémentaire est nécessaire pour poursuivre.",
    "mission_web": "Mission suspendue : plafond global du Web atteint. Résultats conservés ; une autorisation supplémentaire est nécessaire pour poursuivre.",
    # Every budget stop of the mission says so (V4's own evaluation included).
}
AUTHORISATION = "Une autorisation supplémentaire est nécessaire pour poursuivre."
APPROVAL_PAUSE = "plan_approval"


def mission_limits():
    """Mission caps beyond the dollar budget (writing_job_budget_usd), configurable."""
    return {"max_tokens": positive(os.getenv("ENNOSCHOLAR_V5_MISSION_MAX_TOKENS", "2000000"), 'Tokens de mission', integer=True),
        "max_seconds": positive(os.getenv("ENNOSCHOLAR_V5_MISSION_MAX_SECONDS", "5400"), 'Durée de mission')}


def new_job(config, message_id, first_run_id, config_fingerprint, kind="writing"):
    job = base.new_job(config, message_id, first_run_id, config_fingerprint, kind=kind)
    return {**job, "mission": {**mission_limits(), "tokens": 0, "tools": 0, "web": 0,
        "max_tools": getattr(config, 'mission_max_tool_calls', None) or config.max_tool_calls,
        "max_web": getattr(config, 'mission_max_web_calls', None) or config.max_web_calls,
        "time_basis": 'wall_clock_including_validation_wait', "started_at": job["created_at"]}}


def mission(job):
    return job.setdefault("mission", {**mission_limits(), "tokens": 0, "started_at": job.get("created_at") or now()})


def elapsed(job):
    started = mission(job).get("started_at") or job.get("created_at")
    try:
        return (datetime.utcnow() - datetime.fromisoformat(started.rstrip("Z"))).total_seconds()
    except (TypeError, ValueError):
        return 0.0


def remaining_tokens(job):
    m = mission(job)
    return max(0, int(m["max_tokens"]) - int(m.get("tokens", 0)))


def segment_config(config, job):
    """The segment's own caps: never more than what remains of the mission."""
    from dataclasses import replace
    m = mission(job)
    return replace(config, max_cost_usd=min(config.max_cost_usd, remaining(job)),
        max_tokens=min(config.max_tokens, remaining_tokens(job)),
        max_tool_calls=max(0, min(config.max_tool_calls, m.get('max_tools', config.max_tool_calls)-m.get('tools', 0))),
        max_web_calls=max(0, min(config.max_web_calls, m.get('max_web', config.max_web_calls)-m.get('web', 0))),
        timeout_seconds=min(config.timeout_seconds, remaining_seconds(m)))


def mission_stop(job):
    """The V5 mission cap reached, or None."""
    m = mission(job)
    if remaining_tokens(job) <= 0:
        return "mission_tokens"
    if elapsed(job) >= float(m["max_seconds"]):
        return "mission_duration"
    if m.get('tools', 0) >= m.get('max_tools', float('inf')):
        return 'mission_tools'
    return None


def can_start_segment(job):
    if job.get('cancel_requested'):
        return False, 'cancelled'
    if remaining(job) <= 0:
        return False, 'budget_exhausted'
    if len(job.get('segments', [])) >= job['max_segments']:
        return False, 'segment_limit'
    if not (job.get('progress') or {}).get('id') and not job.get('initializing'):
        return False, 'not_applicable'
    reason = mission_stop(job)
    return (False, reason) if reason else (True, None)


def stop(job, status, **fields):
    m = mission(job)
    if status in {"mission_tokens", "mission_duration", "mission_tools", "mission_web"}:
        fields.setdefault("error", STOP_MESSAGES[status].format(tokens=m.get("tokens", 0), max_tokens=m["max_tokens"],
            minutes=int(float(m["max_seconds"]) // 60)))
        job = base.stop(job, "budget_exhausted", **fields)
        job["stop_reason"] = status
        return job
    if status == "budget_exhausted" and "error" not in fields:
        spent = job.get("spent") or EMPTY
        fields["error"] = STOP_MESSAGES["budget_exhausted"].format(committed=committed(spent), budget=job["budget_usd"],
            confirmed=spent["confirmed_usd"], uncertain=spent["uncertain_usd"])
    return base.stop(job, status, **fields)


def science_signature(state):
    """Saved scientific work: card claims with proofs, evidence and current relations."""
    from .context import ANALYSIS_FIELDS
    from .evidence import exact_source_valid
    from .task_results import fingerprint
    from .workflow import readiness_toolkit
    from .argumentation import relation_current
    from .comparison_memory import current
    fields = ANALYSIS_FIELDS[:ANALYSIS_FIELDS.index('method_family')]
    evidence = {eid:e for eid,e in (state.get('evidence') or {}).items()
        if exact_source_valid(e,state.get('source_snapshots') or {})}
    claims = {fingerprint([aid,field,c.get('statement'),sorted(c.get('evidence_ids') or [])])
        for aid,a in (state.get('articles') or {}).items() for field in fields
        for c in (a.get('analysis') or {}).get(field,[]) or [] if isinstance(c,dict)
        and c.get('statement') and c.get('evidence_ids') and set(c['evidence_ids']) <= set(evidence)}
    toolkit = readiness_toolkit(state)
    relations = {fingerprint(r) for r in state.get('scientific_relations') or [] if relation_current(toolkit,r)}
    sections = {s['id']:s for s in (state.get('outline') or {}).get('sections',[])}
    decisions = {d['identity'] for d in state.get('v5_comparison_decisions') or []
        if current(state,d,d.get('article_ids') or [],sections.get(d.get('section_id')))}
    readings = set(state.get('shown_evidence_ids') or []) & set(evidence)
    drafts = {d['id'] for s in sections.values() for d in s.get('drafts',[])}
    return {'claims':len(claims), 'evidence':len(evidence), 'relations':len(relations),
        'comparison_decisions':len(decisions), 'readings':len(readings), 'drafts':len(drafts),
        'work_ids':sorted({*('claim:'+x for x in claims), *('proof:'+x for x in evidence),
            *('relation:'+x for x in relations), *('decision:'+x for x in decisions),
            *('read:'+x for x in readings), *('draft:'+x for x in drafts)})}


def evaluate(job, state, run_id, run_status, run_data, saved_before):
    """V4's evaluation of the segment, then the V5 mission rules."""
    run_data = run_data or {}
    if job.get("current_run_id") != run_id or any(s["run_id"] == run_id for s in job.get("segments", [])):
        return job
    job = base.evaluate(job, state, run_id, run_status, run_data, saved_before)
    if job.get("status") == "budget_exhausted" and AUTHORISATION not in (job.get("error") or ""):
        job["error"] = (job.get("error") or "") + " " + AUTHORISATION
    if not job.get("segments") or job["segments"][-1]["run_id"] != run_id:
        return job
    segment = job["segments"][-1]
    usage = run_data.get("usage") or {}
    ledger_usage = balance({'entries': run_data.get('ledger', []), 'tools': usage.get('tools', 0), 'web': usage.get('web', 0)})
    if not any(any(k in e for k in ('input_tokens', 'output_tokens', 'reserved_tokens')) for e in run_data.get('ledger', [])):
        ledger_usage.update(confirmed_tokens=int(usage.get('input_tokens', 0))+int(usage.get('output_tokens', 0)),
            uncertain_tokens=int(usage.get('uncertain_tokens', 0)), reserved_tokens=int(usage.get('reserved_tokens', 0)))
        ledger_usage['tokens'] = sum(ledger_usage[k] for k in ('confirmed_tokens', 'uncertain_tokens', 'reserved_tokens'))
    segment['resources'] = run_data.get('resource_usage') or ledger_usage
    segment["tokens"] = segment['resources']['tokens']
    m = mission(job)
    total = run_data.get('mission_usage') or combine(s.get('resources') or {'tokens': s.get('tokens', 0)} for s in job['segments'])
    apply_mission_usage(job, total)
    before, after = run_data.get("science_before"), science_signature(state)
    segment["science"] = after
    segment["science_progress"] = bool(before) and bool(set(after['work_ids'])-set(before.get('work_ids') or []))
    if segment["science_progress"] and not segment.get("saved_sections"):
        # Cards, proofs or comparisons saved: preparation is progress for the next sections.
        job["no_progress"] = 0
        if job.get("status") == "stalled" and job.get("stop_reason") == "stalled":
            job.update(status="waiting", stop_reason=None, error=None, last_cause=None)
    if (not segment.get('saved_sections') and not segment['science_progress'] and run_data.get('reason') in
            {'admission_refused', 'synthesis_reserve', 'section_save_reserve', 'job_budget'}):
        job = stop(job, 'budget_exhausted', error="Mission suspendue : ressources restantes insuffisantes pour réserver "
            "le prochain appel et sa sauvegarde. Résultats conservés ; une autorisation supplémentaire est nécessaire pour poursuivre.")
        job['stop_reason'] = 'reservation_insufficient'
        return job
    if job.get("status") == "waiting":
        if not (state.get("outline") or {}).get("approval"):
            # Continuation needs the consultant's approval of the plan (its first segment did not).
            job.update(status="paused", paused_reason=APPROVAL_PAUSE, updated_at=now())
            return job
        reason = mission_stop(job)
        if reason:
            return stop(job, reason)
    return job


STATES = {"en_cours": "En cours", "en_attente_de_validation": "En attente de validation du plan",
    "en_pause": "En pause", "termine": "Terminé", "termine_avec_limites": "Terminé avec limites documentaires",
    "suspendu_par_budget": "Suspendu par budget", "erreur": "Erreur", "annule": "Annulé"}


def consultant_state(job, provisional):
    status = (job or {}).get("status")
    if status in {"running", "waiting"}:
        return "en_cours"
    if status == "paused":
        return "en_attente_de_validation" if job.get("paused_reason") == APPROVAL_PAUSE else "en_pause"
    if status == "completed":
        return "termine_avec_limites" if provisional else "termine"
    if status == "budget_exhausted":
        return "suspendu_par_budget"
    if status == "cancelled":
        return "annule"
    return "erreur"


def progress_view(job, state):
    """What the consultant sees: sections done, current step, quality, consumption."""
    from . import workflow
    outline = state.get("outline") or {}
    progress = (state.get("document") or {}).get("writing_progress") or {}
    targets = list(((job or {}).get("progress") or {}).get("target_ids") or progress.get("target_ids") or [])
    sections = {s["id"]: s for s in outline.get("sections", [])}
    saved = [sid for sid in targets if sid in (progress.get("saved_draft_ids") or {})]
    toolkit = workflow.readiness_toolkit(state)
    quality = {sid: workflow.section_quality(state, sections[sid], toolkit) for sid in saved if sid in sections}
    provisional = [sid for sid, q in quality.items() if q["scientific_state"] != "prepared"]
    nxt = next((sections[sid] for sid in targets if sid not in saved and sid in sections
        and sid not in (job or {}).get("blocked", {})), None)
    state_key = consultant_state(job, provisional)
    line = f"Rédaction de l'état de l'art — {len(saved)}/{len(targets)} sections terminées."
    remaining_steps = []
    if nxt is not None:
        prep = workflow.section_preparation(state, toolkit, nxt)
        number = targets.index(nxt["id"]) + 1
        remaining_steps = [a.get("reason") or a.get("action") for a in prep["actions"]][:8]
        if state_key in {"en_cours", "en_pause", "en_attente_de_validation", "suspendu_par_budget"}:
            line += (f" Préparation scientifique de la section {number}." if prep["actions"] else
                f" Rédaction de la section {number}.")
    if provisional:
        line += f" {len(provisional)} section(s) provisoire(s) : couverture scientifique insuffisante."
    m = mission(job) if job else {}
    return {"state": state_key, "label": STATES[state_key], "progress_text": line,
        "sections_done": len(saved), "sections_total": len(targets), "provisional_section_ids": provisional,
        "section_quality": {sid: {k: q[k] for k in ("basis", "scientific_state")} for sid, q in quality.items()},
        "next_section_id": nxt["id"] if nxt else None, "remaining_steps": remaining_steps,
        "segments": len((job or {}).get("segments", [])), "spent_usd": round(committed((job or {}).get("spent") or EMPTY), 6),
        "budget_usd": (job or {}).get("budget_usd"), "tokens": m.get("tokens", 0), "max_tokens": m.get("max_tokens"),
        "max_seconds": m.get("max_seconds"), 'remaining_seconds':remaining_seconds(m) if m else None,
        'time_basis':m.get('time_basis','wall_clock_including_validation_wait'),
        'tools':m.get('tools',0), 'max_tools':m.get('max_tools'), 'web':m.get('web',0), 'max_web':m.get('max_web'),
        'resources':copy.deepcopy((job or {}).get('resources') or {}),
        "error": (job or {}).get("error"), "stop_reason": (job or {}).get("stop_reason")}


def public(job):
    view = base.public(job)
    if view is not None:
        view["mission"] = copy.deepcopy((job or {}).get("mission"))
        view["paused_reason"] = (job or {}).get("paused_reason")
    return view


# Only the V5-loaded driver sees adaptive admission; original V4 is untouched.
base.can_start_segment = can_start_segment


def wants_full_draft(state, message):
    """V4's detection on a copy, with the drafting a combined request kept for after the
    plan's validation (« Continue la rédaction » then resumes exactly that drafting)."""
    from .writing_progress import begin_writing
    probe = {"outline": copy.deepcopy(state.get("outline", {})),
        "document": {"writing_progress": copy.deepcopy(state.get("document", {}).get("writing_progress"))},
        "outline_versions": copy.deepcopy(state.get("outline_versions", [])),
        "v5_pending_draft": copy.deepcopy(state.get("v5_pending_draft"))}
    if probe["document"]["writing_progress"] is None:
        probe["document"].pop("writing_progress")
    if probe["v5_pending_draft"] is None:
        probe.pop("v5_pending_draft")
    progress = begin_writing(probe, message or "")
    return bool(progress and progress.get("mode") == "full")
