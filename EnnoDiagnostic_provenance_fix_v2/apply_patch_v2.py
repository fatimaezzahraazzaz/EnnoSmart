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
from typing import Callable, Dict, Tuple

PATCH_VERSION = "v2_strict_project_direct_provenance"

TARGETS = {
    "agents/EnnoDiagnostic/ennodiagnostic_agent.py": "agent",
    "agents/EnnoDiagnostic/diagnostic_static_presenter.py": "presenter",
    "agents/EnnoDiagnostic/structured_eligibility_writer.py": "writer",
}
PROVENANCE_MODULE = "agents/EnnoDiagnostic/evidence_provenance.py"


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


def require_v1(root: Path, originals: Dict[str, str]) -> None:
    module = root / PROVENANCE_MODULE
    if not module.exists():
        raise RuntimeError(
            "V2 attend le correctif V1 déjà installé, mais evidence_provenance.py est absent. "
            "N'applique rien : utilise d'abord V1 ou restaure un état cohérent."
        )
    checks = {
        "agent": "ENNOSMART_PROVENANCE_PATCH_V1" in originals["agents/EnnoDiagnostic/ennodiagnostic_agent.py"],
        "presenter": "ENNODIAG_PROVENANCE_GUARD_V1" in originals["agents/EnnoDiagnostic/diagnostic_static_presenter.py"],
        "writer": "ENNODIAG_PYDANTIC_PROVENANCE_V1" in originals["agents/EnnoDiagnostic/structured_eligibility_writer.py"],
    }
    if not all(checks.values()):
        missing = ", ".join(k for k, ok in checks.items() if not ok)
        raise RuntimeError(
            "État V1 incomplet ou fichiers modifiés après V1 (marqueurs absents: " + missing + "). "
            "V2 s'arrête avant toute écriture pour éviter de casser le projet."
        )


def already_v2(originals: Dict[str, str], module_text: str) -> bool:
    return (
        "ENNOSMART_PROVENANCE_PATCH_V2" in originals["agents/EnnoDiagnostic/ennodiagnostic_agent.py"]
        and "ENNODIAG_PROVENANCE_GUARD_V2" in originals["agents/EnnoDiagnostic/diagnostic_static_presenter.py"]
        and "ENNODIAG_PYDANTIC_PROVENANCE_V2" in originals["agents/EnnoDiagnostic/structured_eligibility_writer.py"]
        and "V2 stricte" in module_text
    )


def patch_agent(text: str) -> str:
    if "ENNOSMART_PROVENANCE_PATCH_V2" in text:
        return text
    if "ENNOSMART_PROVENANCE_PATCH_V1" not in text:
        raise RuntimeError("agent: V1 introuvable")

    # Le pack NLP ne doit plus appeler implicitement "project_core" un passage
    # dont l'acteur n'a pas été établi. Ceci ne change aucun groupe de verrou.
    text = replace_once(
        text,
        '            "content_origin": clean_text(item.get("content_origin")) or "project_core",',
        '            "content_origin": clean_text(item.get("content_origin")) or "ambiguous_current_dossier",',
        "agent:nlp_pack default provenance",
    )

    old = '''    # ENNOSMART_PROVENANCE_PATCH_V1: ce garde est en aval du regroupement des verrous.\n    # L'état de l'art reste autorisé pour uncertainty/novelty, mais ne peut plus\n    # devenir une hypothèse, expérience, résultat ou preuve de démarche du projet.\n    provenance = classify_evidence_provenance(passage)\n    project_fact_purposes = {\n        "hypothesis", "experiment", "result", "learning",\n        "creativity", "systematicity", "transferability",\n    }\n    if (\n        purpose in project_fact_purposes\n        and provenance.get("evidence_origin") in {PROV_EXTERNAL_LITERATURE, PROV_HISTORICAL}\n    ):\n        return -1000.0\n'''
    new = '''    # ENNOSMART_PROVENANCE_PATCH_V2: garde STRICTEMENT aval du regroupement.\n    # uncertainty/novelty restent ouverts à tout le dossier pour ne perdre aucun verrou.\n    # En revanche, un fait attribué au projet exige maintenant project_direct :\n    # ambiguous_current_dossier n'est jamais promu automatiquement en fait projet.\n    provenance = classify_evidence_provenance(passage)\n    project_fact_purposes = {\n        "hypothesis", "experiment", "result", "learning",\n        "creativity", "systematicity", "transferability",\n    }\n    if (\n        purpose in project_fact_purposes\n        and provenance.get("evidence_origin") != "project_direct"\n    ):\n        return -1000.0\n'''
    return replace_once(text, old, new, "agent:strict purpose provenance")


def patch_presenter(text: str) -> str:
    if "ENNODIAG_PROVENANCE_GUARD_V2" in text:
        return text
    if "ENNODIAG_PROVENANCE_GUARD_V1" not in text:
        raise RuntimeError("presenter: V1 introuvable")

    text = replace_once(
        text,
        "# ENNODIAG_PROVENANCE_GUARD_V1 — filtre d'usage uniquement.",
        "# ENNODIAG_PROVENANCE_GUARD_V2 — filtre d'usage strict project_direct, jamais filtre de verrou.",
        "presenter:v2 marker",
    )

    old_filter = '''        # Défense en profondeur : même un cache ancien ne doit pas transformer\n        # l'état de l'art en travaux réalisés par le projet.\n        if purpose in {"demarche_detectee", "resultats_metriques"} and origin == PROV_EXTERNAL_LITERATURE:\n            continue\n        if (\n            purpose == "justification_frascati"\n            and origin == PROV_EXTERNAL_LITERATURE\n            and stage in {"hypothesis", "hypothesis_component", "experiment", "result", "learning", "systematicity", "transferability", "creativity"}\n        ):\n            continue\n'''
    new_filter = '''        # V2 — défense en profondeur : pour les faits du projet courant,\n        # `ambiguous_current_dossier` est bloqué au même titre que la littérature.\n        # Cela n'affecte pas les cartes/verrous, seulement les preuves narratives aval.\n        if purpose in {"demarche_detectee", "resultats_metriques"} and origin != "project_direct":\n            continue\n        if (\n            purpose == "justification_frascati"\n            and stage in {\n                "uncertainty", "hypothesis", "hypothesis_component", "experiment",\n                "result", "learning", "systematicity", "transferability",\n                "creativity", "novelty",\n            }\n            and origin != "project_direct"\n        ):\n            continue\n'''
    text = replace_once(text, old_filter, new_filter, "presenter:official strict project direct")

    new_guard = r'''def _provenance_grounding_errors(
    paragraphs: Sequence[Dict[str, Any]],
    items: Sequence[Dict[str, Any]],
    evidence: Sequence[Dict[str, Any]],
    section_key: str,
) -> List[str]:
    """V2 : une affirmation projet doit être portée par une preuve project_direct.

    Les preuves externes/ambiguës ne sont pas supprimées du corpus de verrous.
    Elles sont seulement interdites lorsqu'une section affirme que l'équipe projet
    a visé, réalisé, mesuré, paramétré ou appris quelque chose.
    """
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
    historical_allowed_sections = {
        "demarche_detectee", "resultats_metriques", "parametres_contraintes",
    }

    for index, unit in enumerate(units, start=1):
        cited = _unit_evidence(unit, evidence_by_id)
        if not cited:
            # Le garde générique signale déjà « sans evidence_id » ; cette erreur
            # devient hard en V2 dans generate_one_section.
            continue

        documentary = [
            item for item in cited
            if str(item.get("evidence_id") or "") != "F0"
        ]
        reports = [classify_evidence_provenance(item) for item in documentary]
        direct = [
            item for item, report in zip(documentary, reports)
            if report.get("evidence_origin") == "project_direct"
        ]
        historical = [
            item for item, report in zip(documentary, reports)
            if report.get("evidence_origin") == "historical_project"
            or item.get("temporal_scope") == "previous_cir_continuity"
        ]
        non_confirmed_current = [
            item for item, report in zip(documentary, reports)
            if report.get("evidence_origin") not in {"project_direct", "historical_project"}
        ]
        claim_kind = _grounding_norm(unit.get("claim_kind") or "")

        if section_key == "justification_frascati":
            if claim_kind in execution_claims:
                if non_confirmed_current:
                    errors.append(
                        f"affirmation {index}: preuve non confirmée projet utilisée comme fait du projet courant"
                    )
                if not direct:
                    errors.append(
                        f"affirmation {index}: fait du projet sans preuve project_direct"
                    )
            elif claim_kind == "verrou":
                # La littérature peut contextualiser un verrou, mais le rattachement
                # au projet exige au moins un ancrage direct du dossier courant.
                if not direct:
                    errors.append(
                        f"affirmation {index}: verrou sans ancrage documentaire du projet courant"
                    )
                ambiguous = [
                    item for item, report in zip(documentary, reports)
                    if report.get("evidence_origin") == "ambiguous_current_dossier"
                ]
                if ambiguous:
                    errors.append(
                        f"affirmation {index}: preuve ambiguë interdite pour rattacher le verrou au projet"
                    )
            continue

        # Historique N-1 : chemin dédié, contrôlé ensuite par _historical_temporal_errors.
        if (
            historical
            and section_key in historical_allowed_sections
            and len(historical) == len(documentary)
        ):
            continue

        if non_confirmed_current:
            errors.append(
                f"élément {index}: preuve non confirmée projet utilisée comme fait du projet courant"
            )
        if documentary and not direct:
            errors.append(
                f"élément {index}: fait du projet sans preuve project_direct"
            )
    return errors
'''
    text = replace_function(text, "def _provenance_grounding_errors(", new_guard)

    old_markers = '''                    "provenance externe utilisée comme fait du projet courant",\n                    "verrou sans ancrage documentaire du projet courant",\n                    "comparateur numérique inversé",'''
    new_markers = '''                    "provenance externe utilisée comme fait du projet courant",\n                    "preuve non confirmée projet utilisée comme fait du projet courant",\n                    "fait du projet sans preuve project_direct",\n                    "preuve ambiguë interdite pour rattacher le verrou au projet",\n                    "verrou sans ancrage documentaire du projet courant",\n                    "sans evidence_id",\n                    "comparateur numérique inversé",'''
    text = replace_once(text, old_markers, new_markers, "presenter:hard v2 provenance + missing evidence")

    old_contract = '''- `evidence_origin=external_literature` décrit un travail scientifique tiers : il peut contextualiser un verrou, mais jamais devenir un objectif, une démarche, un paramètre ou un résultat réalisé par le projet courant.\n- `actor_scope=external_authors` ne doit jamais être reformulé comme « l'équipe », « nous » ou « le projet ».'''
    new_contract = '''- V2 STRICTE : pour affirmer un objectif, une action, une démarche, un résultat, un apprentissage ou un paramètre du projet courant, la preuve doit avoir `evidence_origin=project_direct`.\n- `external_literature` reste disponible pour l'état de l'art/verrou mais ne prouve jamais un travail réalisé par le projet.\n- `ambiguous_current_dossier` signifie précisément que l'acteur n'est pas établi : interdiction de le transformer en fait du projet.\n- `actor_scope=external_authors` ou `unknown` ne doit jamais être reformulé comme « l'équipe », « nous » ou « le projet ».'''
    text = replace_once(text, old_contract, new_contract, "presenter:v2 prompt contract")

    if '"context_engineering_version": "v193_consultant_rewrite_without_raw_evidence_fallback"' in text:
        text = text.replace(
            '"context_engineering_version": "v193_consultant_rewrite_without_raw_evidence_fallback"',
            '"context_engineering_version": "v194_strict_project_direct_provenance"',
            1,
        )
    return text


def patch_writer(text: str) -> str:
    if "ENNODIAG_PYDANTIC_PROVENANCE_V2" in text:
        return text
    if "ENNODIAG_PYDANTIC_PROVENANCE_V1" not in text:
        raise RuntimeError("writer: V1 introuvable")

    old_regex = '''_SCORE_MISINTERPRETATION_RE = re.compile(\n    r"\\b(?:part acquise|taux d eligibilite|chance d acceptation|probabilite d acceptation|"\n    r"pourcentage du projet|elements techniques (?:sont )?defendables|travaux (?:sont )?eligibles?)\\b",\n    re.I,\n)\n'''
    new_regex = '''_SCORE_MISINTERPRETATION_RE = re.compile(\n    r"\\b(?:part acquise|acquis(?:e|es)? solide|taux d eligibilite|chance d acceptation|"\n    r"probabilite d acceptation|pourcentage du projet|elements techniques (?:sont )?defendables|"\n    r"travaux (?:sont )?eligibles?|documentes? et valides?|criteres?.{0,90}valides?|"\n    r"garantir (?:la )?(?:robustesse|generalisation|eligibilite))\\b",\n    re.I,\n)\n'''
    text = replace_once(text, old_regex, new_regex, "writer:strict score regex")

    old_instructions = '''19. ENNODIAG_PYDANTIC_PROVENANCE_V1 : une preuve `evidence_origin=external_literature` ou `actor_scope=external_authors` reste utilisable pour contextualiser l'état de l'art/verrou, mais ne peut jamais être présentée comme une action, une hypothèse, une expérience, un résultat ou un apprentissage réalisé par le projet.\n20. Un pourcentage Frascati est uniquement un indice de couverture/défendabilité documentaire de l'opération de référence. N'écris jamais « X % du projet », « X % des éléments techniques sont défendables », « part acquise X % », taux/chance/probabilité d'acceptation.\n'''
    new_instructions = '''19. ENNODIAG_PYDANTIC_PROVENANCE_V2 : pour tout fait attribué au projet courant (contexte technique, hypothèse, méthode, étape expérimentale, résultat, apprentissage), utilise uniquement des preuves `evidence_origin=project_direct`.\n20. `ambiguous_current_dossier` signifie que l'acteur n'est pas prouvé : cette preuve est interdite pour affirmer un fait du projet, même si elle provient d'un fichier du dossier courant.\n21. La littérature externe peut contextualiser un verrou seulement si au moins une preuve `project_direct` rattache ce verrou au projet.\n22. Un pourcentage Frascati est uniquement un indice de couverture/défendabilité documentaire de l'opération de référence. N'écris jamais « X % du projet », « X % des critères sont validés », « part acquise », taux/chance/probabilité d'acceptation ou garantie de robustesse/généralisation.\n'''
    text = replace_once(text, old_instructions, new_instructions, "writer:v2 instructions")

    old_validator_block = '''        provenance_reports = [classify_evidence_provenance(item) for item in cited]\n        external_ids = [\n            str(item.get("evidence_id"))\n            for item, report in zip(cited, provenance_reports)\n            if report.get("evidence_origin") == PROV_EXTERNAL_LITERATURE\n            and str(item.get("evidence_id") or "") != ctx.deps.score_evidence_id\n        ]\n\n        # La littérature reste disponible pour comprendre le verrou, mais elle\n        # ne peut jamais devenir le travail réalisé par le projet courant.\n        project_execution_kinds = TECHNICAL_CLAIM_KINDS - {"verrou"}\n        if claim.claim_kind in project_execution_kinds and external_ids:\n            errors.append(\n                f"{claim.claim_kind}: provenance externe utilisée comme fait du projet courant : "\n                + ", ".join(external_ids)\n            )\n        if claim.claim_kind == "verrou":\n            documentary = [\n                item for item in cited\n                if str(item.get("evidence_id") or "") != ctx.deps.score_evidence_id\n            ]\n            if documentary and not any(is_project_anchor(item) for item in documentary):\n                errors.append("verrou: aucune preuve du dossier courant ne rattache ce verrou au projet.")\n'''
    new_validator_block = '''        provenance_reports = [classify_evidence_provenance(item) for item in cited]\n        documentary_pairs = [\n            (item, report)\n            for item, report in zip(cited, provenance_reports)\n            if str(item.get("evidence_id") or "") != ctx.deps.score_evidence_id\n        ]\n        project_direct_ids = [\n            str(item.get("evidence_id"))\n            for item, report in documentary_pairs\n            if report.get("evidence_origin") == "project_direct"\n        ]\n        non_project_ids = [\n            str(item.get("evidence_id"))\n            for item, report in documentary_pairs\n            if report.get("evidence_origin") != "project_direct"\n        ]\n\n        # V2 : « présent dans le dossier » ne signifie pas « réalisé par le projet ».\n        project_execution_kinds = TECHNICAL_CLAIM_KINDS - {"verrou"}\n        if claim.claim_kind in project_execution_kinds:\n            if non_project_ids:\n                errors.append(\n                    f"{claim.claim_kind}: preuve non project_direct utilisée comme fait du projet : "\n                    + ", ".join(non_project_ids)\n                )\n            if not project_direct_ids:\n                errors.append(\n                    f"{claim.claim_kind}: au moins une preuve project_direct est obligatoire."
                )\n\n        if claim.claim_kind == "verrou":\n            documentary = [item for item, _report in documentary_pairs]\n            if documentary and not any(is_project_anchor(item) for item in documentary):\n                errors.append("verrou: aucune preuve project_direct ne rattache ce verrou au projet.")\n            ambiguous_ids = [\n                str(item.get("evidence_id"))\n                for item, report in documentary_pairs\n                if report.get("evidence_origin") == "ambiguous_current_dossier"\n            ]\n            if ambiguous_ids:\n                errors.append(\n                    "verrou: une preuve ambiguë ne peut pas servir d'ancrage projet : "\n                    + ", ".join(ambiguous_ids)\n                )\n'''
    text = replace_once(text, old_validator_block, new_validator_block, "writer:strict project-direct validator")

    old_score = '''        if (\n            "%" in claim.text\n            and _SCORE_MISINTERPRETATION_RE.search(_norm_text(claim.text))\n        ):\n            errors.append(\n                f"{claim.claim_kind}: le pourcentage Frascati est un indice documentaire, pas une part du projet ni une probabilité d'acceptation."\n            )'''
    new_score = '''        if (\n            _SCORE_MISINTERPRETATION_RE.search(_norm_text(claim.text))\n            and (claim.claim_kind in FRASCATI_CLAIM_KINDS or "%" in claim.text)\n        ):\n            errors.append(\n                f"{claim.claim_kind}: sémantique Frascati invalide ; parler uniquement d'indice/couverture documentaire, jamais de part acquise, critères validés ou garantie."\n            )'''
    text = replace_once(text, old_score, new_score, "writer:strict score semantics")

    old_fact = '''        fact_provenance = classify_evidence_provenance(evidence)\n        if fact_provenance.get("evidence_origin") == PROV_EXTERNAL_LITERATURE:\n            errors.append(f"result_facts: provenance externe utilisée pour {fact.subject}.")\n            continue\n'''
    new_fact = '''        fact_provenance = classify_evidence_provenance(evidence)\n        if fact_provenance.get("evidence_origin") != "project_direct":\n            errors.append(\n                f"result_facts: preuve non project_direct utilisée pour {fact.subject}."\n            )\n            continue\n'''
    text = replace_once(text, old_fact, new_fact, "writer:result facts strict")
    return text


def compile_targets(root: Path) -> None:
    for rel in [*TARGETS.keys(), PROVENANCE_MODULE]:
        py_compile.compile(str(root / rel), doraise=True)


def run_module_self_test(module_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("_ennodiag_provenance_v2_selftest", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Impossible de charger evidence_provenance.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ext = {"section_title": "État de l'art", "text": "We evaluated 19 popular LLMs."}
    assert module.classify_evidence_provenance(ext)["evidence_origin"] == module.PROV_EXTERNAL_LITERATURE
    assert not module.provenance_allows_section(ext, "resultats_metriques")

    ambiguous = {"text": "MoA combines several heterogeneous LLMs."}
    assert module.classify_evidence_provenance(ambiguous)["evidence_origin"] == module.PROV_AMBIGUOUS
    assert not module.is_project_anchor(ambiguous)
    assert not module.provenance_allows_section(ambiguous, "objectif_global")

    fake_core = {"content_origin": "project_core", "text": "12 encoder layers and 12 decoder layers."}
    assert module.classify_evidence_provenance(fake_core)["evidence_origin"] == module.PROV_AMBIGUOUS
    assert not module.provenance_allows_section(fake_core, "parametres_contraintes")

    direct = {"section_title": "Travaux réalisés", "text": "Dans ce projet, l'équipe a mesuré la compilabilité."}
    assert module.classify_evidence_provenance(direct)["evidence_origin"] == module.PROV_PROJECT_DIRECT
    assert module.is_project_anchor(direct)
    assert module.provenance_allows_section(direct, "resultats_metriques")


def latest_backup(root: Path) -> Path:
    backup_root = root / ".ennosmart_patch_backups"
    candidates = sorted([p for p in backup_root.glob("provenance_fix_v2_*") if p.is_dir()], reverse=True)
    if not candidates:
        raise RuntimeError("Aucune sauvegarde provenance_fix_v2 trouvée.")
    return candidates[0]


def rollback(root: Path, backup: Path | None) -> None:
    backup = backup or latest_backup(root)
    print(f"[ROLLBACK V2] source={backup}")
    for rel in [*TARGETS.keys(), PROVENANCE_MODULE]:
        src = backup / rel
        dst = root / rel
        if not src.exists():
            raise RuntimeError(f"Sauvegarde incomplète: {src}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        print(f"  restauré: {rel}")
    compile_targets(root)
    print("[OK] rollback V2 terminé : état V1 restauré et compilé.")


def apply(root: Path, package_dir: Path) -> None:
    paths = {rel: root / rel for rel in TARGETS}
    missing = [rel for rel, path in paths.items() if not path.exists()]
    if missing:
        raise RuntimeError("Fichiers introuvables: " + ", ".join(missing))

    module_path = root / PROVENANCE_MODULE
    originals: Dict[str, str] = {rel: path.read_text(encoding="utf-8") for rel, path in paths.items()}
    old_module_text = module_path.read_text(encoding="utf-8") if module_path.exists() else ""

    if already_v2(originals, old_module_text):
        compile_targets(root)
        run_module_self_test(module_path)
        print("[OK] V2 est déjà installée. Aucun fichier modifié.")
        return

    require_v1(root, originals)

    payload_module = package_dir / "files" / "evidence_provenance.py"
    if not payload_module.exists():
        raise RuntimeError(f"Payload absent: {payload_module}")
    new_module_text = payload_module.read_text(encoding="utf-8")

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = root / ".ennosmart_patch_backups" / f"provenance_fix_v2_{stamp}"
    backup.mkdir(parents=True, exist_ok=True)
    for rel, path in paths.items():
        dst = backup / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dst)
    dst = backup / PROVENANCE_MODULE
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(module_path, dst)

    print("[CHECK] V1 détectée : OK")
    print("[CHECK] aucune préparation de sources / aucun nlp_result.json ne sera modifié")
    print("[CHECK] aucune fonction de détection/regroupement/reformulation des verrous n'est ciblée")

    try:
        patched = {
            "agents/EnnoDiagnostic/ennodiagnostic_agent.py": patch_agent(originals["agents/EnnoDiagnostic/ennodiagnostic_agent.py"]),
            "agents/EnnoDiagnostic/diagnostic_static_presenter.py": patch_presenter(originals["agents/EnnoDiagnostic/diagnostic_static_presenter.py"]),
            "agents/EnnoDiagnostic/structured_eligibility_writer.py": patch_writer(originals["agents/EnnoDiagnostic/structured_eligibility_writer.py"]),
        }

        # Écriture atomique logique : tous les transforms réussissent avant la première écriture.
        for rel, content in patched.items():
            paths[rel].write_text(content, encoding="utf-8", newline="\n")
        module_path.write_text(new_module_text, encoding="utf-8", newline="\n")

        compile_targets(root)
        run_module_self_test(module_path)
    except Exception:
        print("[ERROR] V2 non appliquée correctement ; restauration automatique de l'état V1...")
        rollback(root, backup)
        raise

    diff_parts = []
    for rel in TARGETS:
        diff_parts.extend(difflib.unified_diff(
            originals[rel].splitlines(keepends=True),
            (root / rel).read_text(encoding="utf-8").splitlines(keepends=True),
            fromfile=f"a/{rel} (V1)",
            tofile=f"b/{rel} (V2)",
        ))
    diff_parts.extend(difflib.unified_diff(
        old_module_text.splitlines(keepends=True),
        new_module_text.splitlines(keepends=True),
        fromfile=f"a/{PROVENANCE_MODULE} (V1)",
        tofile=f"b/{PROVENANCE_MODULE} (V2)",
    ))
    diff_path = root / "EnnoDiagnostic_provenance_fix_v2.diff"
    diff_path.write_text("".join(diff_parts), encoding="utf-8")

    print("\n[OK] Correctif V2 appliqué et compilé.")
    print(f"[OK] Backup V1 : {backup}")
    print(f"[OK] Diff V1→V2 : {diff_path}")
    print("[SCOPE] Verrous : aucune logique de détection/regroupement/reformulation modifiée.")
    print("[SCOPE] uncertainty/novelty restent ouvertes ; project facts = project_direct uniquement.")
    print("[SCOPE] ambiguous_current_dossier est maintenant BLOQUÉ pour objectif/démarche/résultat/paramètre/conclusion technique.")
    print("[SCOPE] les paragraphes sans evidence_id deviennent des erreurs bloquantes après retry.")


def main() -> int:
    parser = argparse.ArgumentParser(description="EnnoDiagnostic provenance V2 — strict project_direct, au-dessus de V1")
    parser.add_argument("--repo", required=True, help=r'Racine locale du repo, ex. C:\EnnoSmart')
    parser.add_argument("--rollback", action="store_true", help="Restaure la dernière sauvegarde V2 (retour à V1)")
    parser.add_argument("--backup", default="", help="Sauvegarde V2 précise à restaurer")
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
        print(f"\n[ECHEC V2] {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
