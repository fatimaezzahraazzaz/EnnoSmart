from __future__ import annotations

from typing import Any, Mapping

POLICY_VERSION = "ennoamel_scientific_success_commit_v3_24_1"


def _unique_ints(values: Any) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for value in values or []:
        try:
            item = int(value)
        except (TypeError, ValueError):
            continue
        if item <= 0 or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _article_ids_from_locals(local_vars: Mapping[str, Any]) -> list[int]:
    # Les noms historiques du full-CIR sont volontairement supportés : le
    # contrat reste indépendant de la version exacte du backend local.
    values: list[Any] = []
    for key in (
        "auto_article_ids",
        "ready_article_ids",
        "article_ids",
        "accepted_article_ids",
        "writing_ready_article_ids",
    ):
        values.extend(local_vars.get(key) or [])

    request = local_vars.get("request")
    if request is not None:
        values.extend(getattr(request, "evidence_article_ids", None) or [])
    return _unique_ints(values)


def _find_result(local_vars: Mapping[str, Any]) -> Any | None:
    result = local_vars.get("result")
    if result is not None and hasattr(result, "improved_target"):
        return result
    for value in local_vars.values():
        if hasattr(value, "improved_target") and hasattr(value, "ok"):
            return value
    return None


def _find_request(local_vars: Mapping[str, Any]) -> Any | None:
    request = local_vars.get("request")
    if request is not None and hasattr(request, "target_text"):
        return request
    for value in local_vars.values():
        if hasattr(value, "target_text") and hasattr(value, "evidence_article_ids"):
            return value
    return None


def _find_workflow(local_vars: Mapping[str, Any]) -> dict[str, Any] | None:
    workflow = local_vars.get("workflow")
    if isinstance(workflow, dict) and ("units" in workflow or "patches" in workflow):
        return workflow
    for value in local_vars.values():
        if isinstance(value, dict) and "units" in value and "stats" in value:
            return value
    return None


def _find_unit(local_vars: Mapping[str, Any]) -> dict[str, Any] | None:
    unit = local_vars.get("unit")
    if isinstance(unit, dict) and "unit_id" in unit and "source_sha256" in unit:
        return unit
    for value in local_vars.values():
        if (
            isinstance(value, dict)
            and "unit_id" in value
            and "start" in value
            and "end" in value
            and "source_sha256" in value
        ):
            return value
    return None


def scientific_success_from_runtime(local_vars: Mapping[str, Any]) -> dict[str, Any]:
    """Décide si la première rédaction scientifique est déjà un vrai succès.

    Important : cette fonction n'accepte PAS une simple publication trouvée.
    Elle exige :
      - au moins un article_id déjà préparé / transmis au writer ;
      - result.ok ;
      - une candidate non vide ;
      - une candidate différente du texte cible.

    Les contrôles du writer (conservation, intégrité, scope) ont déjà été
    exécutés avant le retour ``result``. Le contrat empêche uniquement le
    backend de lancer ensuite un second passage éditorial sans sources.
    """
    result = _find_result(local_vars)
    request = _find_request(local_vars)
    article_ids = _article_ids_from_locals(local_vars)

    improved = str(getattr(result, "improved_target", "") or "") if result is not None else ""
    original = str(getattr(request, "target_text", "") or "") if request is not None else ""
    ok = bool(result is not None and getattr(result, "ok", False))
    changed = bool(improved.strip() and (not original.strip() or improved.strip() != original.strip()))
    success = bool(ok and article_ids and changed)

    return {
        "policy_version": POLICY_VERSION,
        "success": success,
        "article_ids": article_ids,
        "article_count": len(article_ids),
        "result_ok": ok,
        "candidate_ready": bool(improved.strip()),
        "candidate_changed": changed,
        "status": "scientific_candidate_ready" if success else "not_ready",
    }


def commit_scientific_success_from_runtime(local_vars: Mapping[str, Any]) -> bool:
    """Fige la candidate scientifique dans le workflow et interdit le fallback.

    ``add_patch`` est importé ici afin de ne dépendre ni du nom d'import ni de
    la forme du backend. Si une section a déjà un patch, la fonction est
    idempotente et ne double pas les statistiques.
    """
    marker = scientific_success_from_runtime(local_vars)
    if not marker["success"]:
        return False

    result = _find_result(local_vars)
    workflow = _find_workflow(local_vars)
    unit = _find_unit(local_vars)
    if result is None or workflow is None or unit is None:
        return False

    unit_id = str(unit.get("unit_id") or "")
    if any(str(row.get("unit_id") or "") == unit_id for row in (workflow.get("patches") or [])):
        unit["scientific_success_v3241"] = marker
        return True

    improved = str(getattr(result, "improved_target", "") or "")
    generation = dict(getattr(result, "generation", None) or {})
    generation["scientific_success_v3241"] = marker

    from agents.EnnoAmelioration.application.cir_section_progressive_v320 import add_patch

    add_patch(
        workflow,
        unit,
        improved,
        mode="scientific",
        generation=generation,
    )
    unit["scientific_success_v3241"] = marker
    workflow["scientific_success_policy_version"] = POLICY_VERSION
    return True
