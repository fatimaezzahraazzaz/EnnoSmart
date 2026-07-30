# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
from pathlib import Path
import pandas as pd
import streamlit as st

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experience_memory_v2 import V2_CATALOG, V2_GLOBAL_GRAPH, V2_CHROMA_DIR, build_v2, read_json, search_v2

st.set_page_config(page_title="EnnoSmart Memory V2", layout="wide")
st.title("EnnoSmart — Memory V2 intelligente")
st.caption("Knowledge cards + domaines + mots-clés + relations + Chroma V2")

tabs = st.tabs(["Construire V2", "Knowledge cards", "Relations", "Recherche V2", "Fichiers"])

with tabs[0]:
    st.subheader("Construire la V2 depuis les chunks V1")
    col1, col2 = st.columns(2)
    with col1:
        org_filter = st.text_input("Filtrer organisme optionnel", value="")
    with col2:
        reset = st.checkbox("Reset Chroma V2", value=True)
    if st.button("Construire / reconstruire Memory V2", type="primary"):
        with st.spinner("Construction V2..."):
            rep = build_v2(reset_chroma=reset, organism_filter=org_filter)
            st.session_state["v2_build_report"] = rep
        st.success("Memory V2 construite.")
    if st.session_state.get("v2_build_report"):
        st.json(st.session_state["v2_build_report"])

with tabs[1]:
    st.subheader("Knowledge cards")
    graph = read_json(V2_GLOBAL_GRAPH, {"cards": [], "relations": []})
    cards = graph.get("cards") or []
    if not cards:
        st.warning("Aucune card. Construis d'abord la V2.")
    else:
        df = pd.DataFrame(cards)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Cards", len(df))
        c2.metric("Organismes", df["organisme"].nunique() if "organisme" in df else 0)
        c3.metric("Projets", df["project"].nunique() if "project" in df else 0)
        c4.metric("Rôles", df["card_type"].nunique() if "card_type" in df else 0)
        role = st.selectbox("Filtrer rôle", ["all"] + sorted(df["card_type"].dropna().unique().tolist()))
        if role != "all":
            df = df[df["card_type"] == role]
        cols = [c for c in ["card_type", "memory_class", "title", "organisme", "project", "year", "main_domain", "importance", "keywords"] if c in df.columns]
        st.dataframe(df[cols], use_container_width=True)

with tabs[2]:
    st.subheader("Relations entre projets / années / domaines")
    graph = read_json(V2_GLOBAL_GRAPH, {"cards": [], "relations": []})
    rel = graph.get("relations") or []
    if not rel:
        st.warning("Aucune relation.")
    else:
        df = pd.DataFrame(rel)
        st.metric("Relations", len(df))
        st.dataframe(df, use_container_width=True)

with tabs[3]:
    st.subheader("Recherche dans Chroma V2")
    col1, col2, col3 = st.columns(3)
    with col1:
        collection = st.text_input("Collection", value="ennosmart_memory_v2_global")
    with col2:
        top_k = st.number_input("Top K", min_value=1, max_value=30, value=8)
    with col3:
        role = st.selectbox("Rôle", ["", "objectif", "verrou", "methode", "resultat", "etat_art", "limite", "contribution", "style"])
    query = st.text_area("Question", value="Quels projets ont déjà traité GraphRAG ou génération de tests Java ?")
    if st.button("Rechercher", type="primary"):
        with st.spinner("Recherche V2..."):
            try:
                st.session_state["v2_search"] = search_v2(query, collection=collection, top_k=int(top_k), role=role)
            except Exception as exc:
                st.session_state["v2_search"] = {"ok": False, "error": str(exc)}
    res = st.session_state.get("v2_search")
    if res:
        if not res.get("ok"):
            st.error(res.get("error"))
        else:
            st.success(f"{res.get('matches_count')} résultat(s)")
            for m in res.get("matches") or []:
                meta = m.get("metadata") or {}
                st.markdown(f"### {meta.get('section_title') or meta.get('document') or m.get('id')}")
                st.caption(f"{meta.get('organisme')} | {meta.get('project')} | {meta.get('year')} | role={meta.get('role')} | class={meta.get('memory_class')} | domain={meta.get('main_domain')} | importance={meta.get('importance')}")
                st.write(m.get("text"))
                with st.expander("Métadonnées"):
                    st.json(meta)

with tabs[4]:
    st.subheader("Fichiers V2")
    st.code(f"Catalog : {V2_CATALOG}")
    st.code(f"Graph : {V2_GLOBAL_GRAPH}")
    st.code(f"Chroma : {V2_CHROMA_DIR}")
    cat = read_json(V2_CATALOG, {})
    if cat:
        st.json(cat)
