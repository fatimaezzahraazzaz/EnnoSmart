"""Model per task, prepared but neutral by default.

With ``task_models`` empty (the default), every task uses ``config.model`` exactly as
before. An administrator may later assign a model to a stage after a comparative
validation (quality of methods, numbers, protocols and uncertainties, then cost); the
model actually requested is recorded on every receipt and ledger row.
"""
from __future__ import annotations

import json

# Stages an administrator can configure, and the agent's request kinds they cover.
STAGES = {
    "understanding": {"understanding", "conversation", "recovery"},
    "selection": {"selection"},
    "reading": {"reading"},
    "comparison": {"argumentation"},
    "plan": {"planning", "plan_part"},
    "writing": {"section"},
    "local_repair": {"section_repair"},
    "review": {"final", "review"},
}
STAGE_OF = {kind: stage for stage, kinds in STAGES.items() for kind in kinds}


def task_models(config):
    raw = (getattr(config, "task_models", "") or "").strip()
    if not raw:
        return {}
    try:
        mapping = json.loads(raw)
    except ValueError:
        raise ValueError("ENNOSCHOLAR_V4_TASK_MODELS doit être un objet JSON {étape: modèle}.") from None
    if not isinstance(mapping, dict) or any(not isinstance(v, str) or not v.strip() for v in mapping.values()):
        raise ValueError("ENNOSCHOLAR_V4_TASK_MODELS doit associer chaque étape à un nom de modèle.")
    unknown = sorted(set(mapping)-set(STAGES))
    if unknown:
        raise ValueError(f"Étapes inconnues dans ENNOSCHOLAR_V4_TASK_MODELS : {unknown} (attendues : {sorted(STAGES)}).")
    return {stage: model.strip() for stage, model in mapping.items()}


def model_for(config, kind):
    """The model requested for one request kind; config.model unless configured."""
    return task_models(config).get(STAGE_OF.get(kind, ""), config.model)
