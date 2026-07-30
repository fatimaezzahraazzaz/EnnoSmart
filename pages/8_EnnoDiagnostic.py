# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import traceback
from pathlib import Path
from typing import Any, Dict, List

import streamlit as st


st.title("EnnoDiagnostic")
st.caption("Synthèse R&D/CIR depuis Chroma + LLM, avec contrôle IA documentaire sur les passages bruts extraits.")


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def render_ai_report(ai_report: Dict[str, Any]):
    st.subheader("Contrôle IA documentaire")

    if not ai_report or not ai_report.get("ok"):
        st.info("Aucun rapport de détection IA documentaire disponible.")
        st.caption("Lance d'abord la détection IA depuis l'onglet Générer.")
        return

    summary = ai_report.get("summary") or {}

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Score IA moyen", summary.get("average_ai_score", "-"))
    c2.metric("Niveau", summary.get("risk_level", "-"))
    c3.metric("Passages analysés", summary.get("passages_count", 0))
    c4.metric("Risque élevé", summary.get("high_count", 0))

    st.caption("Ce score concerne les passages extraits des documents bruts via le NLP, pas la synthèse LLM.")

    suspects = [
        p for p in (ai_report.get("passages") or [])
        if p.get("ai_risk") in {"high", "medium"}
    ]

    if not suspects:
        st.success("Aucun passage fortement suspect détecté.")
        return

    st.markdown("### Passages suspects IA")
    for i, p in enumerate(suspects[:30], start=1):
        with st.container(border=True):
            st.markdown(f"**{i}. {p.get('document') or 'document inconnu'}**")
            c1, c2, c3 = st.columns(3)
            c1.caption(f"Score IA : {p.get('ai_score')}")
            c2.caption(f"Niveau : {p.get('ai_risk')}")
            c3.caption(f"Rôle NLP : {p.get('role')}")
            st.write(p.get("text", ""))


def render_sources(title: str, sources: List[Dict[str, Any]], max_items: int = 30):
    st.subheader(title)
    if not sources:
        st.info("Aucune source récupérée depuis Chroma.")
        return

    for i, src in enumerate(sources[:max_items], start=1):
        meta = src.get("metadata") if isinstance(src.get("metadata"), dict) else {}
        txt = src.get("text") or src.get("source_text") or src.get("content") or ""
        with st.container(border=True):
            st.markdown(f"**{i}. {meta.get('document') or src.get('document') or 'source inconnue'}**")
            c1, c2, c3 = st.columns(3)
            c1.caption(f"Rôle : {meta.get('role') or src.get('role') or '-'}")
            c2.caption(f"Frascati : {meta.get('frascati_decision') or '-'}")
            c3.caption(f"Score : {meta.get('frascati_score') or '-'}")
            st.write(txt)


def import_agent_only_on_click():
    from agents.EnnoDiagnostic.ennodiagnostic_agent import EnnoDiagnosticAgent
    return EnnoDiagnosticAgent


def import_ai_detector_only_on_click():
    from modules.AI_DETECTOR.ai_detector import run_ai_detection_on_nlp_result
    return run_ai_detection_on_nlp_result


with st.sidebar:
    st.header("Projet")
    organisme = st.text_input("Organisme", "Girodin")
    project = st.text_input("Projet", "TGM100")
    year = st.text_input("Année", "2023")

    out_dir = Path("outputs") / "safe_rag_upload" / organisme / project / str(year)
    diagnostic_dir = out_dir / "ennodiagnostic"
    report_path = diagnostic_dir / "ennodiagnostic_report.json"
    ai_report_path = diagnostic_dir / "ai_detection_report.json"

    st.write("Rapport local :")
    st.code(str(report_path))

    use_llm = st.checkbox("Utiliser LLM pour reformulation", value=True)
    model = st.text_input("Modèle LLM", "")


tab_state, tab_generate, tab_ai, tab_report, tab_sources, tab_debug = st.tabs(
    ["1. État", "2. Générer", "3. Contrôle IA", "4. Synthèse", "5. Sources Chroma", "6. Debug"]
)


with tab_state:
    st.subheader("État EnnoDiagnostic")
    files = {
        "nlp_result.json": out_dir / "nlp_result.json",
        "rag_report.json": out_dir / "rag_report.json",
        "retrieval_report.json": out_dir / "retrieval_report.json",
        "ai_detection_report.json": ai_report_path,
        "ennodiagnostic_report.json": report_path,
    }
    for name, path in files.items():
        st.write(("✅" if path.exists() else "❌"), name)


with tab_generate:
    st.subheader("Génération")
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("### 1. Détection IA documentaire")
        if st.button("Lancer détection IA sur les passages bruts", type="secondary"):
            try:
                nlp_path = out_dir / "nlp_result.json"
                if not nlp_path.exists():
                    st.error("nlp_result.json introuvable.")
                else:
                    run_ai_detection_on_nlp_result = import_ai_detector_only_on_click()
                    with st.spinner("Détection IA en cours..."):
                        ai_report = run_ai_detection_on_nlp_result(nlp_path, ai_report_path)
                    st.session_state["ai_detection_report"] = ai_report
                    st.success("Rapport IA généré.")
                    st.json(ai_report.get("summary", {}))
            except Exception:
                err = traceback.format_exc()
                st.error("Erreur pendant la détection IA.")
                st.code(err)
                write_json(diagnostic_dir / "ai_detection_error.json", {"ok": False, "error": err})

    with col2:
        st.markdown("### 2. Synthèse EnnoDiagnostic")
        if st.button("Générer EnnoDiagnostic depuis Chroma", type="primary"):
            try:
                EnnoDiagnosticAgent = import_agent_only_on_click()
                with st.spinner("Interrogation Chroma + génération LLM..."):
                    agent = EnnoDiagnosticAgent(
                        organisme=organisme,
                        project=project,
                        year=year,
                        out_dir=str(out_dir),
                        model=model or None,
                        use_llm=use_llm,
                    )
                    report = agent.generate_diagnostic(save=True)
                st.session_state["ennodiagnostic_report"] = report
                st.success("Synthèse EnnoDiagnostic générée.")
                st.json(report.get("inputs_status", {}))
            except Exception:
                err = traceback.format_exc()
                st.error("Erreur pendant EnnoDiagnostic.")
                st.code(err)
                write_json(diagnostic_dir / "ennodiagnostic_error.json", {"ok": False, "error": err})


with tab_ai:
    ai_report = st.session_state.get("ai_detection_report") or read_json(ai_report_path, {})
    render_ai_report(ai_report)


with tab_report:
    report = st.session_state.get("ennodiagnostic_report") or read_json(report_path, {})
    if not report:
        st.warning("Aucun rapport généré.")
    else:
        ai_report = report.get("ai_detection_report") or read_json(ai_report_path, {})
        render_ai_report(ai_report)
        st.divider()

        fr = report.get("frascati_summary") or {}
        st.subheader("Lecture Frascati")
        c1, c2, c3 = st.columns(3)
        c1.metric("Score moyen", fr.get("average_frascati_score") or "-")
        c2.metric("Scores détectés", fr.get("scores_count", 0))
        c3.metric("Mode", report.get("mode", "-"))
        st.caption(fr.get("explanation", ""))

        st.divider()
        content = ((report.get("diagnostic") or {}).get("content") or "")
        st.markdown(content or "Aucun contenu.")


with tab_sources:
    report = st.session_state.get("ennodiagnostic_report") or read_json(report_path, {})
    if not report:
        st.warning("Aucun rapport généré.")
    else:
        sections = report.get("chroma_sections") or {}
        render_sources("Sources globales", sections.get("global", []))
        render_sources("Objectifs depuis Chroma", sections.get("objectifs", []))
        render_sources("Verrous depuis Chroma", sections.get("verrous", []), max_items=60)
        render_sources("Méthodes depuis Chroma", sections.get("methodes", []))
        render_sources("Résultats depuis Chroma", sections.get("resultats", []))
        render_sources("Paramètres depuis Chroma", sections.get("parametres", []))
        render_sources("Limites depuis Chroma", sections.get("limites", []))


with tab_debug:
    st.subheader("Debug")
    st.write("out_dir :")
    st.code(str(out_dir))
    st.write("report_path :")
    st.code(str(report_path))

    for name, path in {
        "ai_detection_error": diagnostic_dir / "ai_detection_error.json",
        "ennodiagnostic_error": diagnostic_dir / "ennodiagnostic_error.json",
    }.items():
        if path.exists():
            st.error(name)
            st.json(read_json(path, {}))

    if report_path.exists() and st.checkbox("Afficher JSON complet EnnoDiagnostic"):
        st.json(read_json(report_path, {}))
    if ai_report_path.exists() and st.checkbox("Afficher JSON complet IA"):
        st.json(read_json(ai_report_path, {}))
