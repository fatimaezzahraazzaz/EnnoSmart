# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import hashlib
import py_compile
import shutil
import sys
from pathlib import Path
from typing import Callable, Dict, Tuple

PATCH_VERSION = "v1_provenance_without_lock_logic_change"

EXPECTED_BLOB_SHA: Dict[str, str] = {
    "agents/EnnoDiagnostic/ennodiagnostic_agent.py": "8b9e98bb5ec704288da384439b5d6479c2c8405f",
    "agents/EnnoDiagnostic/diagnostic_static_presenter.py": "b0a125a2366f03588daabe44258ad0528db398fa",
    "agents/EnnoDiagnostic/structured_eligibility_writer.py": "e9bec08616ce62eb0fcdf381bd8635e1f171e335",
}

NEW_MODULE = "agents/EnnoDiagnostic/evidence_provenance.py"


def git_blob_sha(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"[{label}] ancre attendue 1 fois, trouvée {count} fois")
    return text.replace(old, new, 1)


def function_slice(text: str, signature: str) -> Tuple[int, int, str]:
    start = text.find(signature)
    if start < 0:
        raise RuntimeError(f"Fonction introuvable: {signature.strip()}")
    search_from = start + len(signature)
    candidates = []
    for marker in ("\ndef ", "\nasync def ", "\nclass ", "\n@"):
        pos = text.find(marker, search_from)
        if pos >= 0:
            candidates.append(pos)
    end = min(candidates) if candidates else len(text)
    return start, end, text[start:end]


def replace_function(text: str, signature: str, transform: Callable[[str], str]) -> str:
    start, end, body = function_slice(text, signature)
    new_body = transform(body)
    return text[:start] + new_body + text[end:]


def add_provenance_import(text: str, typing_anchor: str) -> str:
    marker = "from .evidence_provenance import"
    if marker in text:
        return text
    block = typing_anchor + "\n\ntry:\n    from .evidence_provenance import (\n        PROV_EXTERNAL_LITERATURE,\n        PROV_HISTORICAL,\n        classify_evidence_provenance,\n        is_project_anchor,\n        provenance_allows_section,\n    )\nexcept Exception:\n    from evidence_provenance import (  # type: ignore\n        PROV_EXTERNAL_LITERATURE,\n        PROV_HISTORICAL,\n        classify_evidence_provenance,\n        is_project_anchor,\n        provenance_allows_section,\n    )\n"
    return replace_once(text, typing_anchor, block, "import evidence_provenance")


def patch_agent(text: str) -> str:
    if "ENNOSMART_PROVENANCE_PATCH_V1" in text:
        return text

    typing_anchor = "from typing import Any, Dict, List, Optional, Tuple"
    text = add_provenance_import(text, typing_anchor)

    def patch_proof(fn: str) -> str:
        fn = replace_once(
            fn,
            "    return {\n        \"evidence_id\": passage_id,",
            "    provenance = classify_evidence_provenance(item)\n    return {\n        \"evidence_id\": passage_id,",
            "agent:_nlp_passage_proof provenance",
        )
        fn = replace_once(
            fn,
            "        \"source_path\": raw_path,\n        \"page_number\":",
            "        \"source_path\": raw_path,\n        \"evidence_origin\": provenance.get(\"evidence_origin\"),\n        \"actor_scope\": provenance.get(\"actor_scope\"),\n        \"provenance_reason\": provenance.get(\"provenance_reason\"),\n        \"provenance_confidence\": provenance.get(\"provenance_confidence\"),\n        \"page_number\":",
            "agent:_nlp_passage_proof fields",
        )
        return fn

    text = replace_function(text, "def _nlp_passage_proof(", patch_proof)

    def patch_purpose(fn: str) -> str:
        anchor = "    reference_like = _is_reference_like_passage(passage)\n"
        insertion = anchor + "    # ENNOSMART_PROVENANCE_PATCH_V1: ce garde est en aval du regroupement des verrous.\n    # L'état de l'art reste autorisé pour uncertainty/novelty, mais ne peut plus\n    # devenir une hypothèse, expérience, résultat ou preuve de démarche du projet.\n    provenance = classify_evidence_provenance(passage)\n    project_fact_purposes = {\n        \"hypothesis\", \"experiment\", \"result\", \"learning\",\n        \"creativity\", \"systematicity\", \"transferability\",\n    }\n    if (\n        purpose in project_fact_purposes\n        and provenance.get(\"evidence_origin\") in {PROV_EXTERNAL_LITERATURE, PROV_HISTORICAL}\n    ):\n        return -1000.0\n"
        return replace_once(fn, anchor, insertion, "agent:_purpose_score gate")

    text = replace_function(text, "def _purpose_score(", patch_purpose)
    return text


def patch_presenter(text: str) -> str:
    if "ENNODIAG_PROVENANCE_GUARD_V1" in text:
        return text

    typing_anchor = "from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple"
    text = add_provenance_import(text, typing_anchor)

    def patch_select(fn: str) -> str:
        anchor = "    for source in candidates:\n        if _technical_evidence_rejection_reason(source, config.key):"
        replacement = "    for source in candidates:\n        # ENNODIAG_PROVENANCE_GUARD_V1 — filtre d'usage uniquement.\n        # Aucun candidat/verrou n'est supprimé en amont.\n        if not provenance_allows_section(source, config.key):\n            continue\n        if _technical_evidence_rejection_reason(source, config.key):"
        return replace_once(fn, anchor, replacement, "presenter:select_sources provenance")

    text = replace_function(text, "def select_sources_for_section(", patch_select)

    def patch_evidence_source(fn: str) -> str:
        fn = replace_once(
            fn,
            "    meta = meta_of(source)\n    rag_chunk_id = clean_text(",
            "    meta = meta_of(source)\n    provenance = classify_evidence_provenance(source)\n    rag_chunk_id = clean_text(",
            "presenter:evidence_from_source classify",
        )
        fn = replace_once(
            fn,
            "        \"source_path\": clean_text(meta.get(\"source_path\") or source.get(\"source_path\") or \"\", 900),\n        \"page_number\":",
            "        \"source_path\": clean_text(meta.get(\"source_path\") or source.get(\"source_path\") or \"\", 900),\n        \"evidence_origin\": provenance.get(\"evidence_origin\"),\n        \"actor_scope\": provenance.get(\"actor_scope\"),\n        \"provenance_reason\": provenance.get(\"provenance_reason\"),\n        \"provenance_confidence\": provenance.get(\"provenance_confidence\"),\n        \"page_number\":",
            "presenter:evidence_from_source fields",
        )
        return fn

    text = replace_function(text, "def evidence_from_source(", patch_evidence_source)

    def patch_official(fn: str) -> str:
        # F0 est un calcul backend, pas une preuve de fait projet.
        fn = replace_once(
            fn,
            "            \"document\": \"Évaluation NLP/Frascati\",\n            \"source_path\": \"\",",
            "            \"document\": \"Évaluation NLP/Frascati\",\n            \"source_path\": \"\",\n            \"evidence_origin\": \"calculated_assessment\",\n            \"actor_scope\": \"backend_calculation\",\n            \"provenance_reason\": \"deterministic_backend_assessment\",",
            "presenter:F0 provenance",
        )
        loop_anchor = "    for proof in source_proofs:\n        original_id = clean_text(proof.get(\"evidence_id\"), 240)"
        loop_replacement = "    for proof in source_proofs:\n        provenance = classify_evidence_provenance(proof)\n        origin = provenance.get(\"evidence_origin\")\n        stage = clean_text(proof.get(\"operation_function\") or proof.get(\"proof_kind\"), 80).lower()\n        # Défense en profondeur : même un cache ancien ne doit pas transformer\n        # l'état de l'art en travaux réalisés par le projet.\n        if purpose in {\"demarche_detectee\", \"resultats_metriques\"} and origin == PROV_EXTERNAL_LITERATURE:\n            continue\n        if (\n            purpose == \"justification_frascati\"\n            and origin == PROV_EXTERNAL_LITERATURE\n            and stage in {\"hypothesis\", \"hypothesis_component\", \"experiment\", \"result\", \"learning\", \"systematicity\", \"transferability\", \"creativity\"}\n        ):\n            continue\n        original_id = clean_text(proof.get(\"evidence_id\"), 240)"
        fn = replace_once(fn, loop_anchor, loop_replacement, "presenter:official evidence filter")
        fn = replace_once(
            fn,
            "            \"source_path\": clean_text(proof.get(\"source_path\"), 900),\n            \"page_number\":",
            "            \"source_path\": clean_text(proof.get(\"source_path\"), 900),\n            \"evidence_origin\": provenance.get(\"evidence_origin\"),\n            \"actor_scope\": provenance.get(\"actor_scope\"),\n            \"provenance_reason\": provenance.get(\"provenance_reason\"),\n            \"provenance_confidence\": provenance.get(\"provenance_confidence\"),\n            \"page_number\":",
            "presenter:official evidence fields",
        )
        return fn

    text = replace_function(text, "def _official_frascati_evidence(", patch_official)

    def patch_history(fn: str) -> str:
        return replace_once(
            fn,
            "                \"source_path\": source_path,\n                \"page_number\":",
            "                \"source_path\": source_path,\n                \"evidence_origin\": \"historical_project\",\n                \"actor_scope\": \"project_team_previous_year\",\n                \"provenance_reason\": \"previous_cir_temporal_scope\",\n                \"page_number\":",
            "presenter:historical provenance",
        )

    text = replace_function(text, "def _historical_axis_evidence(", patch_history)

    def patch_context(fn: str) -> str:
        fn = replace_once(
            fn,
            "                \"reference_like\": ev.get(\"reference_like\"),\n                \"quantitative_values\":",
            "                \"reference_like\": ev.get(\"reference_like\"),\n                \"evidence_origin\": ev.get(\"evidence_origin\"),\n                \"actor_scope\": ev.get(\"actor_scope\"),\n                \"provenance_reason\": ev.get(\"provenance_reason\"),\n                \"quantitative_values\":",
            "presenter:prompt provenance fields",
        )
        return fn

    text = replace_function(text, "def build_section_context(", patch_context)

    # Nouveau garde top-level inséré juste avant le garde des opérations.
    guard_marker = "\ndef _demarche_operation_errors("
    if guard_marker not in text:
        raise RuntimeError("presenter: ancre _demarche_operation_errors introuvable")
    provenance_guard = r'''

def _provenance_grounding_errors(
    paragraphs: Sequence[Dict[str, Any]],
    items: Sequence[Dict[str, Any]],
    evidence: Sequence[Dict[str, Any]],
    section_key: str,
) -> List[str]:
    """Bloque une mauvaise attribution d'acteur sans modifier les verrous."""
    guarded_sections = {
        "synthese_strategique",
        "objectif_global",
        "demarche_detectee",
        "resultats_metriques",
        "parametres_contraintes",
        "justification_frascati",
    }
    if section_key not in guarded_sections:
        return []

    evidence_by_id = {
        str(item.get("evidence_id")): item
        for item in evidence if isinstance(item, dict)
    }
    units = [*paragraphs, *items]
    errors: List[str] = []
    execution_claims = {
        "contexte", "hypothese", "methodes_outils", "etapes_experimentales",
        "resultats", "apprentissage",
    }

    for index, unit in enumerate(units, start=1):
        cited = _unit_evidence(unit, evidence_by_id)
        if not cited:
            continue
        reports = [classify_evidence_provenance(item) for item in cited]
        external = [
            report for report in reports
            if report.get("evidence_origin") == PROV_EXTERNAL_LITERATURE
        ]
        claim_kind = _grounding_norm(unit.get("claim_kind") or "")

        if section_key == "justification_frascati":
            if claim_kind in execution_claims and external:
                errors.append(
                    f"affirmation {index}: provenance externe utilisée comme fait du projet courant"
                )
            if claim_kind == "verrou":
                documentary = [
                    item for item in cited
                    if str(item.get("evidence_id") or "") != "F0"
                ]
                if documentary and not any(is_project_anchor(item) for item in documentary):
                    errors.append(
                        f"affirmation {index}: verrou sans ancrage documentaire du projet courant"
                    )
            continue

        if external:
            errors.append(
                f"élément {index}: provenance externe utilisée comme fait du projet courant"
            )
    return errors
'''
    text = text.replace(guard_marker, provenance_guard + guard_marker, 1)

    # Le garde de provenance participe à la validation normale.
    parse_anchor = "    grounding_errors.extend(\n        _strict_claim_grounding_errors(grounding_units, items, evidence, config.key)\n    )\n    if config.key == \"demarche_detectee\":"
    parse_replacement = "    grounding_errors.extend(\n        _strict_claim_grounding_errors(grounding_units, items, evidence, config.key)\n    )\n    grounding_errors.extend(\n        _provenance_grounding_errors(grounding_units, items, evidence, config.key)\n    )\n    if config.key == \"demarche_detectee\":"
    text = replace_once(text, parse_anchor, parse_replacement, "presenter:parse provenance guard")

    # Expose la provenance dans les preuves cliquables frontend.
    proof_anchor = "                \"role\": item.get(\"role\"),\n                \"summary_fr\":"
    proof_replacement = "                \"role\": item.get(\"role\"),\n                \"evidence_origin\": item.get(\"evidence_origin\"),\n                \"actor_scope\": item.get(\"actor_scope\"),\n                \"provenance_reason\": item.get(\"provenance_reason\"),\n                \"provenance_confidence\": item.get(\"provenance_confidence\"),\n                \"summary_fr\":"
    text = replace_once(text, proof_anchor, proof_replacement, "presenter:proof quotes provenance")

    # Une erreur d'attribution ne peut jamais être rétrogradée en warning_only.
    hard_anchor = "                for marker in (\n                    \"comparateur numérique inversé\","
    hard_replacement = "                for marker in (\n                    \"provenance externe utilisée comme fait du projet courant\",\n                    \"verrou sans ancrage documentaire du projet courant\",\n                    \"comparateur numérique inversé\","
    text = replace_once(text, hard_anchor, hard_replacement, "presenter:hard provenance errors")

    # Renforce explicitement le contrat prompt sans réduire les sources de verrou.
    contract_anchor = "- Utilise exclusivement les PREUVES numérotées du projet courant.\n- Les preuves F/E décrivent l'année courante."
    contract_replacement = "- Utilise exclusivement les PREUVES numérotées du projet courant.\n- `evidence_origin=external_literature` décrit un travail scientifique tiers : il peut contextualiser un verrou, mais jamais devenir un objectif, une démarche, un paramètre ou un résultat réalisé par le projet courant.\n- `actor_scope=external_authors` ne doit jamais être reformulé comme « l'équipe », « nous » ou « le projet ».\n- Les preuves F/E décrivent l'année courante."
    text = replace_once(text, contract_anchor, contract_replacement, "presenter:prompt provenance contract")

    return text


def patch_writer(text: str) -> str:
    if "ENNODIAG_PYDANTIC_PROVENANCE_V1" in text:
        return text

    import_anchor = "from pydantic_ai import Agent, ModelRetry, ModelSettings, RunContext, ToolOutput"
    import_block = import_anchor + "\n\ntry:\n    from .evidence_provenance import (\n        PROV_EXTERNAL_LITERATURE,\n        classify_evidence_provenance,\n        is_project_anchor,\n    )\nexcept Exception:\n    from evidence_provenance import (  # type: ignore\n        PROV_EXTERNAL_LITERATURE,\n        classify_evidence_provenance,\n        is_project_anchor,\n    )"
    text = replace_once(text, import_anchor, import_block, "writer:provenance import")

    regex_anchor = "_FULL_ELIGIBILITY_RE = re.compile(\n    r\"\\b(?:pleine? [eé]ligibilit[eé]|assurer (?:une|la) [eé]ligibilit[eé]|garantir (?:une|la) [eé]ligibilit[eé])\\b\",\n    re.I,\n)\n"
    regex_replacement = regex_anchor + "_SCORE_MISINTERPRETATION_RE = re.compile(\n    r\"\\b(?:part acquise|taux d eligibilite|chance d acceptation|probabilite d acceptation|\"\n    r\"pourcentage du projet|elements techniques (?:sont )?defendables|travaux (?:sont )?eligibles?)\\b\",\n    re.I,\n)\n"
    text = replace_once(text, regex_anchor, regex_replacement, "writer:score semantics regex")

    instructions_anchor = "18. `result_facts` est facultatif. Si tu le renseignes, chaque fait quantitatif doit être observé et directement sourcé. Le claim `resultats` ne doit jamais introduire un chiffre absent de ses preuves citées.\n"
    instructions_replacement = instructions_anchor + "19. ENNODIAG_PYDANTIC_PROVENANCE_V1 : une preuve `evidence_origin=external_literature` ou `actor_scope=external_authors` reste utilisable pour contextualiser l'état de l'art/verrou, mais ne peut jamais être présentée comme une action, une hypothèse, une expérience, un résultat ou un apprentissage réalisé par le projet.\n20. Un pourcentage Frascati est uniquement un indice de couverture/défendabilité documentaire de l'opération de référence. N'écris jamais « X % du projet », « X % des éléments techniques sont défendables », « part acquise X % », taux/chance/probabilité d'acceptation.\n"
    text = replace_once(text, instructions_anchor, instructions_replacement, "writer:instructions provenance")

    def patch_validator(fn: str) -> str:
        cited_anchor = "        cited = [ctx.deps.evidence_by_id[eid] for eid in claim.evidence_ids]\n        documentary_ids = [eid for eid in claim.evidence_ids if eid != ctx.deps.score_evidence_id]\n"
        cited_replacement = cited_anchor + "        provenance_reports = [classify_evidence_provenance(item) for item in cited]\n        external_ids = [\n            str(item.get(\"evidence_id\"))\n            for item, report in zip(cited, provenance_reports)\n            if report.get(\"evidence_origin\") == PROV_EXTERNAL_LITERATURE\n            and str(item.get(\"evidence_id\") or \"\") != ctx.deps.score_evidence_id\n        ]\n\n        # La littérature reste disponible pour comprendre le verrou, mais elle\n        # ne peut jamais devenir le travail réalisé par le projet courant.\n        project_execution_kinds = TECHNICAL_CLAIM_KINDS - {\"verrou\"}\n        if claim.claim_kind in project_execution_kinds and external_ids:\n            errors.append(\n                f\"{claim.claim_kind}: provenance externe utilisée comme fait du projet courant : \"\n                + \", \".join(external_ids)\n            )\n        if claim.claim_kind == \"verrou\":\n            documentary = [\n                item for item in cited\n                if str(item.get(\"evidence_id\") or \"\") != ctx.deps.score_evidence_id\n            ]\n            if documentary and not any(is_project_anchor(item) for item in documentary):\n                errors.append(\"verrou: aucune preuve du dossier courant ne rattache ce verrou au projet.\")\n"
        fn = replace_once(fn, cited_anchor, cited_replacement, "writer:validator provenance")

        score_anchor = "        if _FULL_ELIGIBILITY_RE.search(claim.text):\n            errors.append(f\"{claim.claim_kind}: ne jamais garantir une pleine éligibilité CIR.\")"
        score_replacement = score_anchor + "\n        if (\n            \"%\" in claim.text\n            and _SCORE_MISINTERPRETATION_RE.search(_norm_text(claim.text))\n        ):\n            errors.append(\n                f\"{claim.claim_kind}: le pourcentage Frascati est un indice documentaire, pas une part du projet ni une probabilité d'acceptation.\"\n            )"
        fn = replace_once(fn, score_anchor, score_replacement, "writer:score semantics validator")

        fact_anchor = "        evidence = ctx.deps.evidence_by_id[fact.evidence_id]\n        if bool(evidence.get(\"reference_like\")):"
        fact_replacement = "        evidence = ctx.deps.evidence_by_id[fact.evidence_id]\n        fact_provenance = classify_evidence_provenance(evidence)\n        if fact_provenance.get(\"evidence_origin\") == PROV_EXTERNAL_LITERATURE:\n            errors.append(f\"result_facts: provenance externe utilisée pour {fact.subject}.\")\n            continue\n        if bool(evidence.get(\"reference_like\")):"
        fn = replace_once(fn, fact_anchor, fact_replacement, "writer:result_fact provenance")
        return fn

    text = replace_function(text, "async def validate_eligibility_output(", patch_validator)

    def patch_compact(fn: str) -> str:
        loop_anchor = "    for item in evidence:\n        if not isinstance(item, dict):"
        loop_replacement = "    for item in evidence:\n        if not isinstance(item, dict):"
        fn = replace_once(fn, loop_anchor, loop_replacement, "writer:compact loop anchor")
        # Injecte juste après le contrôle evidence_id : aucune dépendance à la structure amont.
        anchor = "        if not evidence_id:\n            continue\n        compact.append("
        replacement = "        if not evidence_id:\n            continue\n        provenance = classify_evidence_provenance(item)\n        compact.append("
        fn = replace_once(fn, anchor, replacement, "writer:compact classify")
        field_anchor = "                \"reference_like\": bool(item.get(\"reference_like\")),\n                \"hypothesis_explicit\":"
        field_replacement = "                \"reference_like\": bool(item.get(\"reference_like\")),\n                \"evidence_origin\": provenance.get(\"evidence_origin\"),\n                \"actor_scope\": provenance.get(\"actor_scope\"),\n                \"provenance_reason\": provenance.get(\"provenance_reason\"),\n                \"provenance_confidence\": provenance.get(\"provenance_confidence\"),\n                \"hypothesis_explicit\":"
        fn = replace_once(fn, field_anchor, field_replacement, "writer:compact provenance fields")
        return fn

    text = replace_function(text, "def _compact_evidence_for_prompt(", patch_compact)
    return text


def compile_files(paths):
    for path in paths:
        py_compile.compile(str(path), doraise=True)


def run_provenance_self_test(module_path: Path) -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location("_ennodiag_provenance_selftest", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Impossible de charger evidence_provenance.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ext = {"section_title": "État de l'art", "text": "We evaluated 19 popular LLMs."}
    assert module.classify_evidence_provenance(ext)["evidence_origin"] == module.PROV_EXTERNAL_LITERATURE
    assert not module.provenance_allows_section(ext, "resultats_metriques")
    direct = {"section_title": "Travaux réalisés", "text": "Dans ce projet, l'équipe a mesuré les résultats."}
    assert module.classify_evidence_provenance(direct)["evidence_origin"] == module.PROV_PROJECT_DIRECT


def latest_backup(root: Path) -> Path:
    backup_root = root / ".ennosmart_patch_backups"
    candidates = sorted([p for p in backup_root.glob("provenance_fix_*") if p.is_dir()], reverse=True)
    if not candidates:
        raise RuntimeError("Aucune sauvegarde provenance_fix trouvée.")
    return candidates[0]


def rollback(root: Path, backup: Path | None) -> None:
    backup = backup or latest_backup(root)
    print(f"[ROLLBACK] source={backup}")
    for relative in list(EXPECTED_BLOB_SHA) + [NEW_MODULE]:
        src = backup / relative
        dst = root / relative
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            print(f"  restauré: {relative}")
        elif relative == NEW_MODULE and dst.exists():
            dst.unlink()
            print(f"  supprimé: {relative}")
    print("[OK] rollback terminé")


def apply(root: Path, package_dir: Path) -> None:
    files = {rel: root / rel for rel in EXPECTED_BLOB_SHA}
    missing = [rel for rel, path in files.items() if not path.exists()]
    if missing:
        raise RuntimeError("Fichiers introuvables: " + ", ".join(missing))

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = root / ".ennosmart_patch_backups" / f"provenance_fix_{stamp}"
    backup.mkdir(parents=True, exist_ok=True)

    originals: Dict[str, str] = {}
    for rel, path in files.items():
        data = path.read_bytes()
        sha = git_blob_sha(data)
        expected = EXPECTED_BLOB_SHA[rel]
        status = "OK" if sha == expected else "LOCAL_MODIFIED"
        print(f"[CHECK] {rel}: {status} blob={sha[:12]} expected={expected[:12]}")
        originals[rel] = data.decode("utf-8")
        target = backup / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)

    existing_module = root / NEW_MODULE
    if existing_module.exists():
        target = backup / NEW_MODULE
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(existing_module, target)

    new_module_source = package_dir / "files" / "evidence_provenance.py"
    if not new_module_source.exists():
        raise RuntimeError(f"Payload absent: {new_module_source}")

    transforms = {
        "agents/EnnoDiagnostic/ennodiagnostic_agent.py": patch_agent,
        "agents/EnnoDiagnostic/diagnostic_static_presenter.py": patch_presenter,
        "agents/EnnoDiagnostic/structured_eligibility_writer.py": patch_writer,
    }

    patched: Dict[str, str] = {}
    try:
        for rel, transform in transforms.items():
            patched[rel] = transform(originals[rel])

        # Écriture seulement après que TOUTES les transformations ont réussi.
        for rel, content in patched.items():
            files[rel].write_text(content, encoding="utf-8", newline="\n")
        existing_module.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(new_module_source, existing_module)

        compile_files([*files.values(), existing_module])
        run_provenance_self_test(existing_module)
    except Exception:
        print("[ERROR] échec pendant l'application ; restauration automatique...")
        rollback(root, backup)
        raise

    # Diff lisible pour audit local.
    diff_parts = []
    for rel in transforms:
        before = originals[rel].splitlines(keepends=True)
        after = patched[rel].splitlines(keepends=True)
        diff_parts.extend(difflib.unified_diff(before, after, fromfile=f"a/{rel}", tofile=f"b/{rel}"))
    diff_parts.extend(difflib.unified_diff([], existing_module.read_text(encoding="utf-8").splitlines(keepends=True), fromfile=f"a/{NEW_MODULE}", tofile=f"b/{NEW_MODULE}"))
    diff_path = root / "EnnoDiagnostic_provenance_fix_v1.diff"
    diff_path.write_text("".join(diff_parts), encoding="utf-8")

    print("\n[OK] Correctif appliqué et compilé.")
    print(f"[OK] Backup : {backup}")
    print(f"[OK] Diff   : {diff_path}")
    print("[SCOPE] Verrous: aucune fonction de détection/regroupement/reformulation n'est modifiée.")
    print("[SCOPE] Agent modifié uniquement dans _nlp_passage_proof et _purpose_score (sélection de preuves aval).")
    print("\nCommandes conseillées:")
    print("  git diff -- agents/EnnoDiagnostic/ennodiagnostic_agent.py agents/EnnoDiagnostic/diagnostic_static_presenter.py agents/EnnoDiagnostic/structured_eligibility_writer.py agents/EnnoDiagnostic/evidence_provenance.py")
    print("  python -m py_compile agents/EnnoDiagnostic/evidence_provenance.py agents/EnnoDiagnostic/ennodiagnostic_agent.py agents/EnnoDiagnostic/diagnostic_static_presenter.py agents/EnnoDiagnostic/structured_eligibility_writer.py")


def main() -> int:
    parser = argparse.ArgumentParser(description="Patch EnnoDiagnostic provenance — sans changer la logique des verrous")
    parser.add_argument("--repo", required=True, help="Racine locale du repo EnnoSmart, ex. C:\\EnnoSmart")
    parser.add_argument("--rollback", action="store_true", help="Restaure la dernière sauvegarde du patch")
    parser.add_argument("--backup", default="", help="Chemin d'une sauvegarde précise pour rollback")
    args = parser.parse_args()

    root = Path(args.repo).expanduser().resolve()
    if not root.exists():
        raise RuntimeError(f"Repo introuvable: {root}")

    if args.rollback:
        rollback(root, Path(args.backup).resolve() if args.backup else None)
        return 0

    apply(root, Path(__file__).resolve().parent)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"\n[ECHEC] {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
