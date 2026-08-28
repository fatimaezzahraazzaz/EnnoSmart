from __future__ import annotations

import argparse
import py_compile
import shutil
from pathlib import Path

MARKER = "historical_continuity_reconciler_v200"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Patch {label}: expected exactly 1 match, found {count}")
    return text.replace(old, new, 1)


def apply(repo: Path) -> Path:
    target = repo / "agents" / "EnnoDiagnostic" / "ennodiagnostic_agent.py"
    module = repo / "agents" / "EnnoDiagnostic" / "historical_continuity_reconciler.py"
    if not target.exists():
        raise FileNotFoundError(f"Missing target: {target}")
    if not module.exists():
        raise FileNotFoundError(f"Missing V200 module: {module}")

    text = target.read_text(encoding="utf-8-sig")
    if MARKER in text and '"historical_continuity_report": historical_continuity_report' in text:
        print("V200 integration already present; no text patch needed.")
        py_compile.compile(str(target), doraise=True)
        py_compile.compile(str(module), doraise=True)
        return target

    backup = target.with_name(target.name + ".before-v200")
    if not backup.exists():
        shutil.copy2(target, backup)
        print(f"Backup created: {backup}")

    old_flow = '''        llm_reformulated_verrous = self._enrich_verrous_with_frascati(
            llm_reformulated_verrous,
            frascati_summary,
        )
        cir_memory_report = self.load_cir_memory_report(
            current_verrous=llm_reformulated_verrous,
        )
'''
    new_flow = '''        llm_reformulated_verrous = self._enrich_verrous_with_frascati(
            llm_reformulated_verrous,
            frascati_summary,
        )

        # V200 - PASS 2 LONGITUDINAL.
        # IMPORTANT: la première détection ci-dessus reste 100% année N.
        # Le CIR N-1 intervient seulement maintenant pour réconcilier la continuité,
        # regrouper les sous-problèmes d'une même famille historique et lancer un
        # gap probe ciblé dans les PREUVES COURANTES si un verrou N-1 semble absent.
        pre_reconciliation_verrous_count = len(llm_reformulated_verrous)
        try:
            try:
                from agents.EnnoDiagnostic.historical_continuity_reconciler import (
                    VERSION as historical_continuity_reconciler_v200,
                    reconcile_historical_continuity,
                )
            except Exception:
                from historical_continuity_reconciler import (
                    VERSION as historical_continuity_reconciler_v200,
                    reconcile_historical_continuity,
                )

            historical_continuity_report = reconcile_historical_continuity(
                organisme=self.organisme,
                project=self.project,
                subproject=self.subproject,
                year=self.year,
                current_verrous=llm_reformulated_verrous,
                current_sections=sections,
                search_current=self.search_chroma,
                llm=self.llm,
                output_dir=self.diagnostic_dir,
            )
            reconciled_verrous = historical_continuity_report.get("reconciled_verrous")
            if isinstance(reconciled_verrous, list):
                llm_reformulated_verrous = [
                    item for item in reconciled_verrous if isinstance(item, dict)
                ]
            # Re-attache les scores Frascati officiels aux groupes conservés / fusionnés.
            # Aucun score n'est recalculé par la réconciliation historique.
            llm_reformulated_verrous = self._enrich_verrous_with_frascati(
                llm_reformulated_verrous,
                frascati_summary,
            )
        except Exception as exc:
            historical_continuity_report = {
                "ok": False,
                "version": "historical_continuity_reconciler_v200",
                "error": str(exc),
                "has_previous_cir": False,
                "current_before_count": pre_reconciliation_verrous_count,
                "reconciled_count": len(llm_reformulated_verrous),
                "reconciled_verrous": llm_reformulated_verrous,
                "policy": "fail-open: keep independent current-year diagnostic unchanged",
            }
            print(f"[EnnoDiagnostic][V200_HISTORICAL_RECONCILIATION][WARN] {exc}", flush=True)

        cir_memory_report = self.load_cir_memory_report(
            current_verrous=llm_reformulated_verrous,
        )
'''
    text = replace_once(text, old_flow, new_flow, "main reconciliation flow")

    old_static = '''                static_diagnostic = build_final_static_diagnostic(
                    core_result=core_result,
                    sections=sections,
                    frascati_summary=frascati_summary,
                    frascati_justification_result=(core_result.get("section_payloads_by_key") or {}).get("justification_frascati"),
                    memory_v2_usage_report=memory_v2_usage_report,
                    llm_reformulated_verrous=llm_reformulated_verrous,
                )
                values = static_diagnostic.get("sections_by_key") or {}
'''
    new_static = '''                static_diagnostic = build_final_static_diagnostic(
                    core_result=core_result,
                    sections=sections,
                    frascati_summary=frascati_summary,
                    frascati_justification_result=(core_result.get("section_payloads_by_key") or {}).get("justification_frascati"),
                    memory_v2_usage_report=memory_v2_usage_report,
                    llm_reformulated_verrous=llm_reformulated_verrous,
                )
                static_diagnostic["historical_continuity_report"] = historical_continuity_report
                values = static_diagnostic.get("sections_by_key") or {}
'''
    text = replace_once(text, old_static, new_static, "static diagnostic continuity payload")

    old_fields = '''            "memory_v2_usage_report": memory_v2_usage_report,
            "previous_verrou_context_report": previous_verrou_context,
            "cir_memory_report": cir_memory_report,
'''
    new_fields = '''            "memory_v2_usage_report": memory_v2_usage_report,
            "previous_verrou_context_report": previous_verrou_context,
            "historical_continuity_report": historical_continuity_report,
            "cir_memory_report": cir_memory_report,
'''
    text = replace_once(text, old_fields, new_fields, "report field")

    old_inputs = '''                "previous_verrou_context_available": bool(previous_verrou_context.get("available")),
                "previous_cir_available": bool(cir_memory_report.get("has_previous_cir")),
'''
    new_inputs = '''                "previous_verrou_context_available": bool(previous_verrou_context.get("available")),
                "historical_continuity_available": bool(historical_continuity_report.get("has_previous_cir")),
                "historical_gap_recovery_count": int(historical_continuity_report.get("recovered_gap_candidates_count") or 0),
                "previous_cir_available": bool(cir_memory_report.get("has_previous_cir")),
'''
    text = replace_once(text, old_inputs, new_inputs, "inputs status")

    old_telemetry = '''                "main_verrous_count": len(llm_reformulated_verrous),
                "previous_verrou_examples_count": int(previous_verrou_context.get("examples_count") or 0),
'''
    new_telemetry = '''                "main_verrous_count": len(llm_reformulated_verrous),
                "main_verrous_before_historical_reconciliation": pre_reconciliation_verrous_count,
                "historical_reconciliation_merged_groups": int(historical_continuity_report.get("merged_groups_count") or 0),
                "historical_reconciliation_gap_recovered": int(historical_continuity_report.get("recovered_gap_candidates_count") or 0),
                "historical_reconciliation_history_is_current_proof": False,
                "previous_verrou_examples_count": int(previous_verrou_context.get("examples_count") or 0),
'''
    text = replace_once(text, old_telemetry, new_telemetry, "telemetry")

    old_version = '            "version": "ennodiagnostic_v190_traceable_unified_eligibility_analysis",\n'
    new_version = '            "version": "ennodiagnostic_v200_historical_continuity_reconciliation",\n'
    text = replace_once(text, old_version, new_version, "report version")

    old_render = '''            if documents:
                lines.append(f"Documents courants associés : {documents}")
            lines.append(f"Statut : {status} — validation consultant nécessaire.")
'''
    new_render = '''            if documents:
                lines.append(f"Documents courants associés : {documents}")
            history = item.get("historical_continuity") if isinstance(item.get("historical_continuity"), dict) else {}
            if history:
                history_status = clean_text(history.get("status"))
                previous_year = clean_text(history.get("previous_year"))
                family_title = clean_text(history.get("historical_family_title"))
                if history_status:
                    lines.append(
                        "Continuité historique : "
                        + history_status
                        + (f" | {previous_year}" if previous_year else "")
                        + (f" | famille N-1 : {family_title}" if family_title else "")
                        + "."
                    )
            subproblems = item.get("subproblems_current") if isinstance(item.get("subproblems_current"), list) else []
            if len(subproblems) > 1:
                lines.append("Sous-problèmes courants regroupés : " + "; ".join(clean_text(value) for value in subproblems if clean_text(value)) + ".")
            if item.get("historical_gap_recovered"):
                lines.append("Origine : candidat récupéré par contrôle N-1 puis confirmé uniquement par des preuves de l'année courante ; à valider.")
            lines.append(f"Statut : {status} — validation consultant nécessaire.")
'''
    text = replace_once(text, old_render, new_render, "fallback render history")

    old_print = '            f"✅ EnnoDiagnostic V191 terminé | sections={len(diagnostic_sections_by_key)} "\n'
    new_print = '            f"✅ EnnoDiagnostic V200 terminé | sections={len(diagnostic_sections_by_key)} "\n'
    if old_print in text:
        text = text.replace(old_print, new_print, 1)

    target.write_text(text, encoding="utf-8")
    py_compile.compile(str(target), doraise=True)
    py_compile.compile(str(module), doraise=True)
    print(f"Patched and compiled: {target}")
    return target


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=r"C:\EnnoSmart")
    args = parser.parse_args()
    apply(Path(args.repo).resolve())


if __name__ == "__main__":
    main()
