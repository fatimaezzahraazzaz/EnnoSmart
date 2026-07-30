# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import traceback
from pathlib import Path
from typing import Any, Dict

import streamlit as st


st.title("Mémoire CIR intelligente")
st.caption("CIR final validé dans Chroma + comparaison contextuelle des nouveaux verrous.")


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_upload(uploaded_file, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / uploaded_file.name
    dest.write_bytes(uploaded_file.getbuffer())
    return dest


def short(text: Any, n: int = 750) -> str:
    text = str(text or "").strip()
    return text if len(text) <= n else text[:n].rstrip() + "..."


def render_comparison_item(rec: Dict[str, Any]):
    current = rec.get("current_item") or {}
    best = rec.get("best_match") or {}
    previous = best.get("previous_candidate") or {}
    decision = rec.get("decision") or {}
    llm = best.get("llm_judge") or {}
    rules = best.get("business_rules") or {}

    with st.container(border=True):
        c1, c2, c3 = st.columns([1, 1, 0.32])

        with c1:
            st.markdown("**Brut/NLP année N**")
            st.caption(f"Rôle : {current.get('role') or '-'}")
            if current.get("document"):
                st.caption(f"Document : {current.get('document')}")
            st.write(short(current.get("text"), 900))

        with c2:
            st.markdown("**CIR final précédent**")
            st.caption(f"Année : {previous.get('year') or '-'} | Rôle : {previous.get('role') or '-'}")
            if previous.get("document"):
                st.caption(f"Document : {previous.get('document')}")
            st.write(short(previous.get("text"), 900))

        with c3:
            st.metric("Nouveauté", decision.get("novelty_score", "-"))
            st.metric("Continuité", decision.get("continuity_score", "-"))
            st.caption(decision.get("label") or "")

        if llm.get("used"):
            st.info(f"LLM judge : {llm.get('decision')} — {llm.get('reason')}")
            if llm.get("risk"):
                st.warning(llm.get("risk"))

        with st.expander("Détails scoring"):
            st.json({
                "decision": decision,
                "similarity": best.get("similarity_details"),
                "business_rules": rules,
                "llm_judge": llm,
            })


with st.sidebar:
    st.header("Projet")
    organisme = st.text_input("Organisme", "Girodin")
    project = st.text_input("Projet", "TGM100")
    year = st.text_input("Année courante N", "2023")

    out_dir = Path("outputs") / "safe_rag_upload" / organisme / project / str(year)
    nlp_path = out_dir / "nlp_result.json"

    st.write("NLP courant :")
    st.code(str(nlp_path))


tab_register, tab_compare, tab_report = st.tabs(
    ["1. Enregistrer CIR final", "2. Comparer intelligent", "3. Rapport"]
)


with tab_register:
    st.subheader("Enregistrer le CIR final validé dans Chroma")

    st.info(
        "À lancer à la fin d'un dossier : le CIR validé devient la mémoire historique "
        "pour les prochaines années."
    )

    final_year = st.text_input("Année du CIR final à archiver", value=year)
    uploaded = st.file_uploader("CIR final validé", type=["docx", "pdf", "txt", "json"])
    local_path = st.text_input("Ou chemin local du CIR final", value=str(out_dir / "cir_reference_extracted.json"))

    if st.button("Indexer CIR final dans Chroma", type="primary"):
        try:
            from modules.CIR_MEMORY.cir_memory import register_final_cir_in_chroma, cir_final_dir

            if uploaded is not None:
                cir_path = save_upload(uploaded, cir_final_dir(organisme, project, final_year) / "uploads")
            else:
                cir_path = Path(local_path)

            if not cir_path.exists():
                st.error(f"Fichier introuvable : {cir_path}")
            else:
                with st.spinner("Indexation CIR final dans Chroma..."):
                    report = register_final_cir_in_chroma(
                        organisme=organisme,
                        project=project,
                        year=final_year,
                        cir_final_path=cir_path,
                    )
                st.success("CIR final indexé.")
                st.json({
                    "collection": report.get("collection_name"),
                    "items_count": report.get("items_count"),
                    "roles_count": report.get("roles_count"),
                })

        except Exception:
            st.error("Erreur indexation CIR final")
            st.code(traceback.format_exc())


with tab_compare:
    st.subheader("Comparer les nouveaux bruts avec les CIR précédents")

    use_llm = st.checkbox("Utiliser LLM judge contextuel", value=True)
    top_k = st.slider("Candidats CIR précédents par élément", 1, 5, 3)

    if st.button("Comparer avec mémoire CIR", type="primary"):
        try:
            from modules.CIR_MEMORY.cir_memory import compare_current_raw_with_cir_memory, cir_memory_dir

            if not nlp_path.exists():
                st.error(f"nlp_result.json introuvable : {nlp_path}")
            else:
                output_path = cir_memory_dir(organisme, project, year) / "cir_memory_comparison_report.json"

                with st.spinner("Comparaison intelligente en cours..."):
                    report = compare_current_raw_with_cir_memory(
                        organisme=organisme,
                        project=project,
                        current_year=year,
                        current_nlp_result_path=nlp_path,
                        output_path=output_path,
                        use_llm=use_llm,
                        top_k=top_k,
                    )

                st.session_state["cir_memory_report_v2"] = report
                st.success("Comparaison générée.")
                st.json(report.get("summary", {}))

                if not report.get("has_previous_cir"):
                    st.warning(report.get("message"))

        except Exception:
            st.error("Erreur comparaison mémoire CIR")
            st.code(traceback.format_exc())


with tab_report:
    from modules.CIR_MEMORY.cir_memory import cir_memory_dir

    report_path = cir_memory_dir(organisme, project, year) / "cir_memory_comparison_report.json"
    report = st.session_state.get("cir_memory_report_v2") or read_json(report_path, {})

    if not report:
        st.warning("Aucun rapport mémoire CIR généré.")
    elif not report.get("has_previous_cir"):
        st.warning(report.get("message") or "Aucun CIR précédent.")
        st.json(report.get("summary", {}))
    else:
        summary = report.get("summary") or {}
        st.subheader("Résumé intelligent")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Verrous N", summary.get("verrou_count", 0))
        c2.metric("Nouveaux verrous", summary.get("new_verrou_count", 0))
        c3.metric("Évolutions", summary.get("evolution_verrou_count", 0))
        c4.metric("Score nouveauté", summary.get("project_novelty_score", "-"))

        st.info(summary.get("frascati_context_explanation", ""))

        st.markdown("### Nouveaux / non retrouvés")
        for rec in report.get("new_or_not_found") or []:
            if (rec.get("current_item") or {}).get("role") == "verrou":
                render_comparison_item(rec)

        st.markdown("### Évolutions ou continuités partielles")
        for rec in report.get("evolution_or_partial_continuity") or []:
            if (rec.get("current_item") or {}).get("role") == "verrou":
                render_comparison_item(rec)

        st.markdown("### Continuités fortes")
        for rec in report.get("continuity_strong") or []:
            if (rec.get("current_item") or {}).get("role") == "verrou":
                render_comparison_item(rec)

        with st.expander("JSON complet"):
            st.json(report)
