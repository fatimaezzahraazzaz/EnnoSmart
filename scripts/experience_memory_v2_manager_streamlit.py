# -*- coding: utf-8 -*-
from __future__ import annotations

"""
scripts/experience_memory_v2_manager_streamlit.py

Interface finale unique :
- Ajouter organisme / projet / année / CIR
- Traitement direct V2 : extraction + NLP + chunks enrichis + cards + relations + Chroma
- Recherche V2
- Statistiques
- Reset V2
"""

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import streamlit as st

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from experience_memory_v2_engine import (  # noqa
    V2_ROOT,
    V2_CATALOG,
    V2_GLOBAL_GRAPH,
    V2_CHROMA_DIR,
    ORGANISMES_DIR,
    build_cir_final_v2,
    read_json,
    scan_library,
    search_v2,
    reset_all_v2,
    rebuild_global_graph_and_catalog,
    is_year,
    clean_text,
)


def save_uploaded_temp(uploaded_file) -> Path:
    tmp_dir = V2_ROOT / "_tmp_uploads"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    safe_name = re.sub(r"[^\w\-. àâäéèêëîïôöùûüçÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ]+", "_", uploaded_file.name)
    p = tmp_dir / safe_name
    p.write_bytes(uploaded_file.getbuffer())
    return p


def show_logs(logs: List[Dict[str, Any]]) -> None:
    if not logs:
        st.info("Aucun log.")
        return
    for log in logs:
        status = str(log.get("status") or "")
        step = log.get("step")
        msg = log.get("message")
        if status == "ok":
            st.success(f"{step} — {msg}")
        elif status == "warning":
            st.warning(f"{step} — {msg}")
        elif status == "error":
            st.error(f"{step} — {msg}")
        else:
            st.write(f"{step} — {msg}")
        with st.expander("Détails", expanded=False):
            st.json(log)


def graph_cards_df() -> pd.DataFrame:
    graph = read_json(V2_GLOBAL_GRAPH, {"cards": []})
    cards = graph.get("cards") if isinstance(graph, dict) else []
    if not isinstance(cards, list) or not cards:
        return pd.DataFrame()
    return pd.DataFrame(cards)


st.set_page_config(page_title="EnnoSmart Memory V2 Final", layout="wide")

st.title("EnnoSmart — Mémoire d'expérience V2 finale")
st.caption("Un seul pipeline : CIR final → extraction → NLP → chunks enrichis → cards → relations → Chroma V2")

with st.sidebar:
    st.header("Chemins")
    st.code(str(V2_ROOT))
    st.code(str(V2_CHROMA_DIR))

    st.divider()
    st.header("Options build")
    reset_chroma = st.checkbox("Reset Chroma avant ce build", value=False)
    vision_mode = st.selectbox("Vision mode", ["text_only", "auto", "fast", "full"], index=0)
    formula_mode = st.selectbox("Formula mode", ["off", "fast", "explain"], index=0)

tabs = st.tabs([
    "➕ Ajouter + traiter",
    "📁 Bibliothèque",
    "🧠 Knowledge cards",
    "🔗 Relations",
    "🔎 Recherche",
    "📊 Statistiques",
    "🗑️ Reset",
])

if "last_report_v2" not in st.session_state:
    st.session_state["last_report_v2"] = None

with tabs[0]:
    st.subheader("Ajouter un CIR final et alimenter directement la V2")

    with st.form("add_cir_v2"):
        c1, c2, c3 = st.columns(3)
        with c1:
            organisme = st.text_input("Organisme", value="")
        with c2:
            project = st.text_input("Projet", value="")
        with c3:
            year = st.text_input("Année", value="2025")

        uploaded = st.file_uploader("CIR final", type=["docx", "pdf", "txt", "md"])
        submit = st.form_submit_button("Ajouter + traiter V2", type="primary")

    if submit:
        if not clean_text(organisme):
            st.error("Organisme obligatoire.")
        elif not clean_text(project):
            st.error("Projet obligatoire.")
        elif not is_year(year):
            st.error("Année invalide.")
        elif not uploaded:
            st.error("Fichier obligatoire.")
        else:
            try:
                tmp = save_uploaded_temp(uploaded)
                with st.spinner("Traitement V2 : extraction + NLP + Chroma..."):
                    rep = build_cir_final_v2(
                        tmp,
                        organisme=clean_text(organisme),
                        project=clean_text(project),
                        year=clean_text(year),
                        copy_to_library=True,
                        reset_chroma=reset_chroma,
                        vision_mode=vision_mode,
                        formula_mode=formula_mode,
                    )
                    st.session_state["last_report_v2"] = rep
                st.success("CIR ajouté et mémoire V2 alimentée.")
            except Exception as exc:
                st.error(str(exc))

    rep = st.session_state.get("last_report_v2")
    if rep:
        st.markdown("### Dernier traitement")
        st.success(
            f"{rep.get('file_name')} — chunks={rep.get('chunks_count')} — cards={rep.get('cards_count')}"
        )
        c1, c2, c3 = st.columns(3)
        c1.write("Rôles")
        c1.json(rep.get("role_counts") or {})
        c2.write("Mémoires")
        c2.json(rep.get("memory_counts") or {})
        c3.write("Domaines")
        c3.json(rep.get("domain_counts") or {})
        show_logs(rep.get("logs") or [])
        with st.expander("Rapport complet", expanded=False):
            st.json(rep)

with tabs[1]:
    st.subheader("Bibliothèque des CIR originaux")
    rows = scan_library()
    if not rows:
        st.warning("Aucun CIR dans storage/organismes.")
    else:
        st.dataframe(pd.DataFrame(rows), use_container_width=True)

with tabs[2]:
    st.subheader("Knowledge cards")
    df = graph_cards_df()
    if df.empty:
        st.warning("Aucune card. Ajoute d'abord un CIR.")
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Cards", len(df))
        c2.metric("Organismes", df["organisme"].nunique() if "organisme" in df else 0)
        c3.metric("Projets", df["project"].nunique() if "project" in df else 0)
        c4.metric("Rôles", df["card_type"].nunique() if "card_type" in df else 0)

        roles = ["all"] + sorted(df["card_type"].dropna().unique().tolist())
        role = st.selectbox("Filtrer rôle", roles)
        show = df if role == "all" else df[df["card_type"] == role]

        cols = [
            "card_type", "memory_class", "title", "organisme", "project",
            "year", "main_domain", "importance", "keywords"
        ]
        st.dataframe(show[[c for c in cols if c in show.columns]], use_container_width=True)

with tabs[3]:
    st.subheader("Relations")
    graph = read_json(V2_GLOBAL_GRAPH, {"relations": []})
    rel = graph.get("relations") if isinstance(graph, dict) else []
    if not rel:
        st.warning("Aucune relation.")
    else:
        st.metric("Relations", len(rel))
        st.dataframe(pd.DataFrame(rel), use_container_width=True)

with tabs[4]:
    st.subheader("Recherche dans la mémoire V2")

    c1, c2, c3 = st.columns(3)
    with c1:
        collection = st.text_input("Collection", value="ennosmart_memory_v2_global")
    with c2:
        top_k = st.number_input("Top K", min_value=1, max_value=30, value=8)
    with c3:
        role = st.selectbox("Rôle", ["", "objectif", "etat_art", "limite", "verrou", "methode", "resultat", "contribution", "style"])

    query = st.text_area("Question", value="Quels projets ont déjà rencontré des verrous similaires ?")

    if st.button("Rechercher", type="primary", disabled=not query.strip()):
        try:
            with st.spinner("Recherche Chroma V2..."):
                res = search_v2(query, collection=collection, top_k=int(top_k), role=role)
                st.session_state["search_v2"] = res
        except Exception as exc:
            st.session_state["search_v2"] = {"ok": False, "error": str(exc)}

    res = st.session_state.get("search_v2")
    if res:
        if not res.get("ok"):
            st.error(res.get("error"))
        else:
            st.success(f"{res.get('matches_count')} résultat(s)")
            for m in res.get("matches") or []:
                meta = m.get("metadata") or {}
                st.markdown(f"### {meta.get('section_title') or meta.get('document') or m.get('id')}")
                st.caption(
                    f"{meta.get('organisme')} | {meta.get('project')} | {meta.get('year')} | "
                    f"role={meta.get('role')} | mémoire={meta.get('memory_class')} | "
                    f"domaine={meta.get('main_domain')} | importance={meta.get('importance')}"
                )
                st.write(m.get("text"))
                with st.expander("Métadonnées", expanded=False):
                    st.json(meta)

with tabs[5]:
    st.subheader("Statistiques")
    catalog = read_json(V2_CATALOG, {})
    if not catalog:
        st.warning("catalog_v2.json absent.")
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Chunks", catalog.get("chunks_count", 0))
        c2.metric("Cards", catalog.get("cards_count", 0))
        c3.metric("Relations", catalog.get("relations_count", 0))

        st.markdown("### Rôles")
        st.json(catalog.get("role_counts") or {})
        st.markdown("### Domaines")
        st.json(catalog.get("domain_counts") or {})
        with st.expander("Catalog complet"):
            st.json(catalog)

    if st.button("Reconstruire graph/catalog/Chroma depuis fichiers V2"):
        with st.spinner("Reconstruction..."):
            rep = rebuild_global_graph_and_catalog(reset_chroma=True)
        st.success("Reconstruit.")
        st.json(rep)

with tabs[6]:
    st.subheader("Reset")
    st.warning("Cette action peut supprimer toute la mémoire V2.")

    delete_orgs = st.checkbox("Supprimer aussi storage/organismes", value=False)

    if st.button("RESET TOTAL V2", type="primary"):
        rep = reset_all_v2(delete_organismes=delete_orgs)
        st.success("Reset effectué.")
        st.json(rep)

    st.markdown("### Fichiers")
    st.code(f"V2_ROOT = {V2_ROOT}")
    st.code(f"CATALOG = {V2_CATALOG}")
    st.code(f"GRAPH = {V2_GLOBAL_GRAPH}")
    st.code(f"CHROMA = {V2_CHROMA_DIR}")
