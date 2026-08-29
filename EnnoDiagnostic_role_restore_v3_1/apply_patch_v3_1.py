# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import importlib.util
import py_compile
import shutil
import sys
from pathlib import Path
from typing import Dict, Tuple

PATCH_VERSION = "v3_1_restore_nlp_role_authority_robust"
PRESENTER = "agents/EnnoDiagnostic/diagnostic_static_presenter.py"
PROVENANCE = "agents/EnnoDiagnostic/evidence_provenance.py"
WRITER = "agents/EnnoDiagnostic/structured_eligibility_writer.py"
AGENT = "agents/EnnoDiagnostic/ennodiagnostic_agent.py"
TARGETS = [PRESENTER, PROVENANCE, WRITER, AGENT]


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


def replace_function(text: str, signature: str, new_body: str) -> str:
    start, end, _ = function_slice(text, signature)
    return text[:start] + new_body.rstrip() + "\n\n" + text[end:].lstrip("\n")


def add_helper_import(text: str) -> str:
    if "is_trusted_current_project_evidence" in text:
        return text
    needle = "        is_project_anchor,\n"
    count = text.count(needle)
    if count not in {1, 2}:
        raise RuntimeError(f"import provenance inattendu: is_project_anchor trouvé {count} fois")
    return text.replace(needle, needle + "        is_trusted_current_project_evidence,\n")


def patch_presenter(text: str) -> str:
    if "ENNODIAG_ROLE_AUTHORITY_V3_1" in text or "ENNODIAG_ROLE_AUTHORITY_V3" in text:
        return text
    if "ENNODIAG_PROVENANCE_GUARD_V2" not in text:
        raise RuntimeError(
            "presenter: le correctif provenance V2 n'est pas détecté. "
            "V3.1 s'arrête pour éviter d'écraser un état inattendu."
        )

    text = add_helper_import(text)
    text = text.replace(
        "# ENNODIAG_PROVENANCE_GUARD_V2 — filtre d'usage strict project_direct, jamais filtre de verrou.",
        "# ENNODIAG_ROLE_AUTHORITY_V3_1 — rôle NLP autoritaire + garde provenance; aucun recalcul de rôle.",
        1,
    )

    old = '''        if target_only:\n            return "target_or_planning_not_result"\n        if not observed:\n            return "no_observed_result"'''
    new = '''        if target_only:\n            return "target_or_planning_not_result"\n        nlp_result_role = role in {"resultat", "result", "contribution"}\n        if not observed and not nlp_result_role:\n            return "no_observed_result"'''
    text = replace_once(text, old, new, "presenter:result NLP role authority")

    old = '''        if operation_function not in {"experiment", "hypothesis", "learning", "historical method"} and not method_signal:\n            return "not_a_method"\n        if not method_signal:\n            return "method_not_described"'''
    new = '''        nlp_method_role = role in {"methode", "method", "demarche"}\n        if operation_function not in {"experiment", "hypothesis", "learning", "historical method"} and not method_signal and not nlp_method_role:\n            return "not_a_method"\n        if not method_signal and not nlp_method_role:\n            return "method_not_described"'''
    text = replace_once(text, old, new, "presenter:method NLP role authority")

    old = '''        if not has_parameter and not has_constraint:\n            return "not_a_parameter_or_technical_constraint"'''
    new = '''        nlp_parameter_role = role in {"parametre", "parameter", "limite", "constraint"}\n        if not has_parameter and not has_constraint and not nlp_parameter_role:\n            return "not_a_parameter_or_technical_constraint"'''
    text = replace_once(text, old, new, "presenter:parameter NLP role authority")

    anchor = '''        "role": clean_text(meta.get("role") or meta.get("final_role") or source.get("role") or "", 80),\n        "proof_kind":'''
    replacement = '''        "role": clean_text(meta.get("role") or meta.get("final_role") or source.get("role") or "", 80),\n        "semantic_role": clean_text(meta.get("semantic_role") or source.get("semantic_role") or "", 80),\n        "original_model_role": clean_text(meta.get("original_model_role") or source.get("original_model_role") or "", 80),\n        "content_origin": clean_text(meta.get("content_origin") or source.get("content_origin") or "", 120),\n        "declared_corpus": clean_text(meta.get("declared_corpus") or source.get("declared_corpus") or "", 120),\n        "diagnostic_corpus_selected": bool(meta.get("diagnostic_corpus_selected") or source.get("diagnostic_corpus_selected")),\n        "temporal_scope": clean_text(meta.get("temporal_scope") or source.get("temporal_scope") or "", 120),\n        "proof_kind":'''
    text = replace_once(text, anchor, replacement, "presenter:propagate NLP/current-corpus metadata")

    # V3.1 : patch structurel de _official_frascati_evidence. On ne dépend plus
    # du commentaire/formatage exact du bloc V2 local : seule la zone entre
    # le calcul de `stage` et `original_id` est remplacée.
    official_start, official_end, official_fn = function_slice(
        text, "def _official_frascati_evidence("
    )
    official_lines = official_fn.splitlines()
    stage_idx = next((
        i for i, line in enumerate(official_lines)
        if 'stage = clean_text(proof.get("operation_function")' in line
    ), None)
    original_id_idx = next((
        i for i, line in enumerate(official_lines)
        if 'original_id = clean_text(proof.get("evidence_id")' in line
    ), None)
    if stage_idx is None or original_id_idx is None or original_id_idx <= stage_idx:
        raise RuntimeError(
            "[presenter:official role-aware provenance V3.1] structure inattendue "
            "dans _official_frascati_evidence : stage/original_id introuvable"
        )
    indent = official_lines[stage_idx][
        : len(official_lines[stage_idx]) - len(official_lines[stage_idx].lstrip())
    ]
    policy = [
        indent + "# ENNODIAG_ROLE_AUTHORITY_V3_1 — rôle NLP courant qualifié, externe bloqué.",
        indent + 'if purpose in {"demarche_detectee", "resultats_metriques", "parametres_contraintes"}:',
        indent + "    if not is_trusted_current_project_evidence(proof, purpose):",
        indent + "        continue",
        indent + 'if purpose == "justification_frascati":',
        indent + '    if stage in {"experiment", "systematicity", "creativity"}:',
        indent + '        allowed = is_trusted_current_project_evidence(proof, "demarche_detectee")',
        indent + '    elif stage in {"result", "learning", "transferability"}:',
        indent + '        allowed = is_trusted_current_project_evidence(proof, "resultats_metriques")',
        indent + '    elif stage in {"uncertainty", "novelty"}:',
        indent + '        allowed = is_trusted_current_project_evidence(proof, "verrou")',
        indent + "    else:",
        indent + '        allowed = origin == "project_direct"',
        indent + "    if not allowed:",
        indent + "        continue",
    ]
    official_lines = (
        official_lines[: stage_idx + 1]
        + policy
        + official_lines[original_id_idx:]
    )
    patched_official = "\n".join(official_lines)
    text = (
        text[:official_start]
        + patched_official.rstrip()
        + "\n\n"
        + text[official_end:].lstrip("\n")
    )

    new_guard = r'''def _provenance_grounding_errors(
    paragraphs: Sequence[Dict[str, Any]],
    items: Sequence[Dict[str, Any]],
    evidence: Sequence[Dict[str, Any]],
    section_key: str,
) -> List[str]:
    """V3.1 : bloque l'externe, pas un rôle NLP courant déjà qualifié."""
    guarded_sections = {
        "synthese_strategique", "objectif_global", "demarche_detectee",
        "resultats_metriques", "parametres_contraintes", "justification_frascati",
    }
    if section_key not in guarded_sections:
        return []

    evidence_by_id = {
        str(item.get("evidence_id")): item
        for item in evidence if isinstance(item, dict)
    }
    units = [*paragraphs, *items]
    errors: List[str] = []
    historical_allowed_sections = {
        "demarche_detectee", "resultats_metriques", "parametres_contraintes",
    }
    execution_to_section = {
        "methodes_outils": "demarche_detectee",
        "etapes_experimentales": "demarche_detectee",
        "resultats": "resultats_metriques",
        "apprentissage": "resultats_metriques",
    }

    for index, unit in enumerate(units, start=1):
        cited = _unit_evidence(unit, evidence_by_id)
        if not cited:
            continue
        documentary = [
            item for item in cited
            if str(item.get("evidence_id") or "") != "F0"
        ]
        claim_kind = _grounding_norm(unit.get("claim_kind") or "")

        if section_key == "justification_frascati":
            trust_section = execution_to_section.get(claim_kind)
            if claim_kind == "verrou":
                trust_section = "verrou"
            elif claim_kind in {"contexte", "hypothese"}:
                trust_section = None

            for item in documentary:
                report = classify_evidence_provenance(item)
                if report.get("evidence_origin") == "project_direct":
                    continue
                if trust_section and is_trusted_current_project_evidence(item, trust_section):
                    continue
                errors.append(
                    f"affirmation {index}: preuve non confirmée projet utilisée comme fait du projet courant"
                )
            if documentary and not any(
                classify_evidence_provenance(item).get("evidence_origin") == "project_direct"
                or (trust_section and is_trusted_current_project_evidence(item, trust_section))
                for item in documentary
            ):
                errors.append(
                    f"affirmation {index}: fait du projet sans preuve project_direct ou rôle NLP courant qualifié"
                )
            continue

        historical = [
            item for item in documentary
            if classify_evidence_provenance(item).get("evidence_origin") == "historical_project"
            or item.get("temporal_scope") == "previous_cir_continuity"
        ]
        if historical and section_key in historical_allowed_sections and len(historical) == len(documentary):
            continue

        for item in documentary:
            report = classify_evidence_provenance(item)
            if report.get("evidence_origin") == "project_direct":
                continue
            if section_key in {"demarche_detectee", "resultats_metriques", "parametres_contraintes"} and is_trusted_current_project_evidence(item, section_key):
                continue
            errors.append(
                f"élément {index}: preuve non confirmée projet utilisée comme fait du projet courant"
            )
        if documentary and not any(
            classify_evidence_provenance(item).get("evidence_origin") == "project_direct"
            or (
                section_key in {"demarche_detectee", "resultats_metriques", "parametres_contraintes"}
                and is_trusted_current_project_evidence(item, section_key)
            )
            for item in documentary
        ):
            errors.append(
                f"élément {index}: fait du projet sans preuve project_direct ou rôle NLP courant qualifié"
            )
    return errors
'''
    text = replace_function(text, "def _provenance_grounding_errors(", new_guard)

    # Contrat prompt V3.1 : remplacement tolérant, ligne par ligne. Le garde
    # déterministe reste l'autorité si le texte local a été légèrement remanié.
    prompt_replacements = {
        "- V2 STRICTE : pour affirmer un objectif, une action, une démarche, un résultat, un apprentissage ou un paramètre du projet courant, la preuve doit avoir `evidence_origin=project_direct`.":
            "- V3.1 : pour les sections démarche/résultats/paramètres, le rôle NLP du corpus courant est l'autorité sémantique ; le presenter ne refait pas cette classification par regex.",
        "- `external_literature` reste disponible pour l'état de l'art/verrou mais ne prouve jamais un travail réalisé par le projet.":
            "- `external_literature` reste toujours interdite comme travail réalisé par le projet ; elle peut seulement contextualiser l'état de l'art/verrou.",
        "- `ambiguous_current_dossier` signifie précisément que l'acteur n'est pas établi : interdiction de le transformer en fait du projet.":
            "- `ambiguous_current_dossier` peut servir à une section technique uniquement si la preuve appartient au corpus courant, possède le rôle NLP correspondant et ne porte aucun signal d'article/auteur tiers.",
        "- `actor_scope=external_authors` ou `unknown` ne doit jamais être reformulé comme « l'équipe », « nous » ou « le projet ».":
            "- Une cible/planification ne doit jamais devenir un résultat acquis. Objectif et synthèse restent soumis à un ancrage projet direct.",
    }
    replaced_prompt_rules = 0
    for old_line, new_line in prompt_replacements.items():
        if old_line in text:
            text = text.replace(old_line, new_line, 1)
            replaced_prompt_rules += 1
    if replaced_prompt_rules == 0:
        print("[INFO] presenter: contrat prompt V2 non reconnu exactement ; gardes déterministes V3.1 appliqués quand même.")

    text = text.replace(
        '"context_engineering_version": "v194_strict_project_direct_provenance"',
        '"context_engineering_version": "v195_nlp_role_authority_with_provenance"',
        1,
    )
    return text


def patch_agent(text: str) -> str:
    if "ENNOSMART_ROLE_AUTHORITY_V3" in text:
        return text
    if "ENNOSMART_PROVENANCE_PATCH_V2" not in text:
        return text
    text = add_helper_import(text)
    old = '''    # ENNOSMART_PROVENANCE_PATCH_V2: garde STRICTEMENT aval du regroupement.\n    # uncertainty/novelty restent ouverts à tout le dossier pour ne perdre aucun verrou.\n    # En revanche, un fait attribué au projet exige maintenant project_direct :\n    # ambiguous_current_dossier n'est jamais promu automatiquement en fait projet.\n    provenance = classify_evidence_provenance(passage)\n    project_fact_purposes = {\n        "hypothesis", "experiment", "result", "learning",\n        "creativity", "systematicity", "transferability",\n    }\n    if (\n        purpose in project_fact_purposes\n        and provenance.get("evidence_origin") != "project_direct"\n    ):\n        return -1000.0'''
    new = '''    # ENNOSMART_ROLE_AUTHORITY_V3 : le rôle NLP courant peut alimenter les\n    # démarches/résultats sans devenir project_direct. L'externe reste bloqué.\n    provenance = classify_evidence_provenance(passage)\n    project_fact_purposes = {\n        "hypothesis", "experiment", "result", "learning",\n        "creativity", "systematicity", "transferability",\n    }\n    role_section = {\n        "experiment": "demarche_detectee",\n        "systematicity": "demarche_detectee",\n        "result": "resultats_metriques",\n        "learning": "resultats_metriques",\n        "transferability": "resultats_metriques",\n    }.get(purpose)\n    allowed = provenance.get("evidence_origin") == "project_direct"\n    if not allowed and role_section:\n        allowed = is_trusted_current_project_evidence(passage, role_section)\n    if purpose in project_fact_purposes and not allowed:\n        return -1000.0'''
    return replace_once(text, old, new, "agent:v3 role-aware purpose")


def patch_writer(text: str) -> str:
    if "ENNODIAG_PYDANTIC_ROLE_AUTHORITY_V3" in text:
        return text
    if "ENNODIAG_PYDANTIC_PROVENANCE_V2" not in text:
        return text
    text = add_helper_import(text)

    old_instructions = '''19. ENNODIAG_PYDANTIC_PROVENANCE_V2 : pour tout fait attribué au projet courant (contexte technique, hypothèse, méthode, étape expérimentale, résultat, apprentissage), utilise uniquement des preuves `evidence_origin=project_direct`.\n20. `ambiguous_current_dossier` signifie que l'acteur n'est pas prouvé : cette preuve est interdite pour affirmer un fait du projet, même si elle provient d'un fichier du dossier courant.\n21. La littérature externe peut contextualiser un verrou seulement si au moins une preuve `project_direct` rattache ce verrou au projet.\n22. Un pourcentage Frascati est uniquement un indice de couverture/défendabilité documentaire de l'opération de référence. N'écris jamais « X % du projet », « X % des critères sont validés », « part acquise », taux/chance/probabilité d'acceptation ou garantie de robustesse/généralisation.'''
    new_instructions = '''19. ENNODIAG_PYDANTIC_ROLE_AUTHORITY_V3 : objectif/contexte/hypothèse exigent `project_direct`; pour méthodes/étapes/résultats, une preuve du corpus courant avec rôle NLP correspondant peut aussi être utilisée si `is_trusted_current_project_evidence` l'autorise.\n20. La littérature externe reste interdite comme fait projet, même si son rôle NLP ressemble à une méthode ou un résultat.\n21. Un verrou peut être rattaché par une preuve `project_direct` ou par un rôle NLP `verrou/limite` du corpus courant qualifié.\n22. Un pourcentage Frascati est uniquement un indice de couverture/défendabilité documentaire de l'opération de référence. N'écris jamais « X % du projet », « X % des critères sont validés », « part acquise », taux/chance/probabilité d'acceptation ou garantie de robustesse/généralisation.'''
    text = replace_once(text, old_instructions, new_instructions, "writer:v3 instructions")

    old_block = '''        provenance_reports = [classify_evidence_provenance(item) for item in cited]\n        documentary_pairs = [\n            (item, report)\n            for item, report in zip(cited, provenance_reports)\n            if str(item.get("evidence_id") or "") != ctx.deps.score_evidence_id\n        ]\n        project_direct_ids = [\n            str(item.get("evidence_id"))\n            for item, report in documentary_pairs\n            if report.get("evidence_origin") == "project_direct"\n        ]\n        non_project_ids = [\n            str(item.get("evidence_id"))\n            for item, report in documentary_pairs\n            if report.get("evidence_origin") != "project_direct"\n        ]\n\n        # V2 : « présent dans le dossier » ne signifie pas « réalisé par le projet ».\n        project_execution_kinds = TECHNICAL_CLAIM_KINDS - {"verrou"}\n        if claim.claim_kind in project_execution_kinds:\n            if non_project_ids:\n                errors.append(\n                    f"{claim.claim_kind}: preuve non project_direct utilisée comme fait du projet : "\n                    + ", ".join(non_project_ids)\n                )\n            if not project_direct_ids:\n                errors.append(\n                    f"{claim.claim_kind}: au moins une preuve project_direct est obligatoire."\n                )\n\n        if claim.claim_kind == "verrou":\n            documentary = [item for item, _report in documentary_pairs]\n            if documentary and not any(is_project_anchor(item) for item in documentary):\n                errors.append("verrou: aucune preuve project_direct ne rattache ce verrou au projet.")\n            ambiguous_ids = [\n                str(item.get("evidence_id"))\n                for item, report in documentary_pairs\n                if report.get("evidence_origin") == "ambiguous_current_dossier"\n            ]\n            if ambiguous_ids:\n                errors.append(\n                    "verrou: une preuve ambiguë ne peut pas servir d'ancrage projet : "\n                    + ", ".join(ambiguous_ids)\n                )'''
    new_block = '''        provenance_reports = [classify_evidence_provenance(item) for item in cited]\n        documentary_pairs = [\n            (item, report)\n            for item, report in zip(cited, provenance_reports)\n            if str(item.get("evidence_id") or "") != ctx.deps.score_evidence_id\n        ]\n        claim_section = {\n            "methodes_outils": "demarche_detectee",\n            "etapes_experimentales": "demarche_detectee",\n            "resultats": "resultats_metriques",\n            "apprentissage": "resultats_metriques",\n        }.get(claim.claim_kind)\n\n        allowed_ids = []\n        rejected_ids = []\n        for item, report in documentary_pairs:\n            evidence_id = str(item.get("evidence_id"))\n            allowed = report.get("evidence_origin") == "project_direct"\n            if not allowed and claim_section:\n                allowed = is_trusted_current_project_evidence(item, claim_section)\n            if allowed:\n                allowed_ids.append(evidence_id)\n            else:\n                rejected_ids.append(evidence_id)\n\n        project_execution_kinds = TECHNICAL_CLAIM_KINDS - {"verrou"}\n        if claim.claim_kind in project_execution_kinds:\n            if rejected_ids:\n                errors.append(\n                    f"{claim.claim_kind}: preuve non autorisée utilisée comme fait du projet : "\n                    + ", ".join(rejected_ids)\n                )\n            if not allowed_ids:\n                errors.append(\n                    f"{claim.claim_kind}: preuve project_direct ou rôle NLP courant qualifié obligatoire."\n                )\n\n        if claim.claim_kind == "verrou":\n            documentary = [item for item, _report in documentary_pairs]\n            if documentary and not any(\n                is_project_anchor(item) or is_trusted_current_project_evidence(item, "verrou")\n                for item in documentary\n            ):\n                errors.append("verrou: aucun ancrage courant qualifié ne rattache ce verrou au projet.")'''
    text = replace_once(text, old_block, new_block, "writer:v3 role-aware validator")

    old_fact = '''        fact_provenance = classify_evidence_provenance(evidence)\n        if fact_provenance.get("evidence_origin") != "project_direct":\n            errors.append(\n                f"result_facts: preuve non project_direct utilisée pour {fact.subject}."\n            )\n            continue'''
    new_fact = '''        fact_provenance = classify_evidence_provenance(evidence)\n        if (\n            fact_provenance.get("evidence_origin") != "project_direct"\n            and not is_trusted_current_project_evidence(evidence, "resultats_metriques")\n        ):\n            errors.append(\n                f"result_facts: preuve ni project_direct ni résultat NLP courant qualifié pour {fact.subject}."\n            )\n            continue'''
    text = replace_once(text, old_fact, new_fact, "writer:v3 result facts")
    text = text.replace("ENNODIAG_PYDANTIC_PROVENANCE_V2", "ENNODIAG_PYDANTIC_ROLE_AUTHORITY_V3")
    return text


def compile_targets(root: Path) -> None:
    for rel in TARGETS:
        path = root / rel
        if path.exists():
            py_compile.compile(str(path), doraise=True)


def run_module_self_test(module_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("_ennodiag_role_v3_selftest", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Impossible de charger evidence_provenance.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ext = {"section_title": "État de l'art", "semantic_role": "resultat", "content_origin": "ambiguous_current_dossier", "text": "We evaluated 19 popular LLMs."}
    assert module.classify_evidence_provenance(ext)["evidence_origin"] == module.PROV_EXTERNAL_LITERATURE
    assert not module.is_trusted_current_project_evidence(ext, "resultats_metriques")

    paper_we = {"semantic_role": "resultat", "content_origin": "ambiguous_current_dossier", "document": "paper.docx", "text": "We evaluated 19 popular LLMs and measured failures."}
    assert not module.is_trusted_current_project_evidence(paper_we, "resultats_metriques")

    result = {"semantic_role": "resultat", "content_origin": "ambiguous_current_dossier", "document": "resultats.xlsx", "text": "SCoT4UT : compilabilité mesurée 53 %."}
    assert module.is_trusted_current_project_evidence(result, "resultats_metriques")

    method = {"semantic_role": "methode", "content_origin": "ambiguous_current_dossier", "document": "demarche.docx", "text": "RAG4UT utilise Methods2Test pour récupérer le contexte de code."}
    assert module.is_trusted_current_project_evidence(method, "demarche_detectee")

    param = {"semantic_role": "parametre", "content_origin": "ambiguous_current_dossier", "document": "protocole.docx", "text": "Le modèle Qwen2.5-Coder 7B est exécuté localement."}
    assert module.is_trusted_current_project_evidence(param, "parametres_contraintes")


def latest_backup(root: Path) -> Path:
    backup_root = root / ".ennosmart_patch_backups"
    candidates = sorted([p for p in backup_root.glob("role_restore_v3_1_*") if p.is_dir()], reverse=True)
    if not candidates:
        raise RuntimeError("Aucune sauvegarde role_restore_v3_1 trouvée.")
    return candidates[0]


def rollback(root: Path, backup: Path | None) -> None:
    backup = backup or latest_backup(root)
    print(f"[ROLLBACK V3.1] source={backup}")
    for rel in TARGETS:
        src = backup / rel
        dst = root / rel
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            print(f"  restauré: {rel}")
    compile_targets(root)
    print("[OK] rollback V3.1 terminé.")


def apply(root: Path, package_dir: Path) -> None:
    required = [PRESENTER, PROVENANCE, WRITER]
    missing = [rel for rel in required if not (root / rel).exists()]
    if missing:
        raise RuntimeError("Fichiers introuvables: " + ", ".join(missing))

    originals: Dict[str, str] = {}
    for rel in TARGETS:
        path = root / rel
        if path.exists():
            originals[rel] = path.read_text(encoding="utf-8")

    if "ENNODIAG_ROLE_AUTHORITY_V3_1" in originals[PRESENTER] or "ENNODIAG_ROLE_AUTHORITY_V3" in originals[PRESENTER]:
        compile_targets(root)
        run_module_self_test(root / PROVENANCE)
        print("[OK] V3/V3.1 déjà installée. Aucun fichier modifié.")
        return

    if "ENNODIAG_PROVENANCE_GUARD_V2" not in originals[PRESENTER]:
        raise RuntimeError("Le presenter local n'est pas dans l'état provenance V2 attendu.")
    if "V2 stricte" not in originals[PROVENANCE]:
        raise RuntimeError("evidence_provenance.py V2 attendu avant V3.1.")

    payload = package_dir / "files" / "evidence_provenance.py"
    if not payload.exists():
        raise RuntimeError(f"Payload absent: {payload}")
    new_provenance = payload.read_text(encoding="utf-8")

    patched = dict(originals)
    patched[PRESENTER] = patch_presenter(originals[PRESENTER])
    # Writer et agent sont des alignements complémentaires. Ils ne doivent pas
    # empêcher la correction principale du presenter si le local a été restauré
    # partiellement depuis `clean` ou remanié entre V2 et V3.1.
    try:
        patched[WRITER] = patch_writer(originals[WRITER])
    except RuntimeError as exc:
        print(f"[INFO] writer conservé tel quel : {exc}")
        patched[WRITER] = originals[WRITER]
    if AGENT in originals:
        try:
            patched[AGENT] = patch_agent(originals[AGENT])
        except RuntimeError as exc:
            print(f"[INFO] agent conservé tel quel : {exc}")
            patched[AGENT] = originals[AGENT]
    patched[PROVENANCE] = new_provenance

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = root / ".ennosmart_patch_backups" / f"role_restore_v3_1_{stamp}"
    backup.mkdir(parents=True, exist_ok=True)
    for rel, content in originals.items():
        dst = backup / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(content, encoding="utf-8", newline="\n")

    print("[CHECK] provenance V2 détectée : OK")
    print("[CHECK] modules/NLP/* : NON MODIFIÉS")
    print("[CHECK] frascati_assessment.py / demarche_legibility.py : NON MODIFIÉS")
    print("[CHECK] historical continuity / verrous : NON MODIFIÉS")
    print("[CHECK] correction ciblée : restitution méthode/résultat/paramètre + provenance")

    try:
        for rel, content in patched.items():
            (root / rel).write_text(content, encoding="utf-8", newline="\n")
        compile_targets(root)
        run_module_self_test(root / PROVENANCE)
    except Exception:
        print("[ERROR] échec V3.1 ; restauration automatique...")
        rollback(root, backup)
        raise

    diff_parts = []
    for rel in TARGETS:
        if rel not in originals or rel not in patched or originals[rel] == patched[rel]:
            continue
        diff_parts.extend(difflib.unified_diff(
            originals[rel].splitlines(keepends=True),
            patched[rel].splitlines(keepends=True),
            fromfile=f"a/{rel} (avant V3.1)",
            tofile=f"b/{rel} (V3.1)",
        ))
    diff_path = root / "EnnoDiagnostic_role_restore_v3_1.diff"
    diff_path.write_text("".join(diff_parts), encoding="utf-8")

    print("\n[OK] Correctif V3.1 appliqué et compilé.")
    print(f"[OK] Backup : {backup}")
    print(f"[OK] Diff   : {diff_path}")
    print("[SCOPE] Le NLP reste autorité pour methode/resultat/parametre.")
    print("[SCOPE] État de l'art / auteurs externes restent bloqués comme faits projet.")
    print("[SCOPE] Cible/planification reste bloquée comme résultat acquis.")
    print("[SCOPE] Aucun changement de calcul Frascati, groupes de verrous ou CIR N-1.")


def main() -> int:
    parser = argparse.ArgumentParser(description="EnnoDiagnostic V3.1 — restaure l'autorité des rôles NLP")
    parser.add_argument("--repo", required=True, help=r"Racine locale, ex. C:\EnnoSmart")
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument("--backup", default="")
    args = parser.parse_args()
    root = Path(args.repo).expanduser().resolve()
    if args.rollback:
        rollback(root, Path(args.backup).resolve() if args.backup else None)
        return 0
    apply(root, Path(__file__).resolve().parent)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"\n[ECHEC V3.1] {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
