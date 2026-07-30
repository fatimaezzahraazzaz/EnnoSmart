# -*- coding: utf-8 -*-
from __future__ import annotations

"""
scripts/experience_memory_streamlit.py

Interface locale Streamlit pour :
- lister les sources mémoire
- ajouter un fichier/dossier
- lancer extraction + NLP + RAG Chroma
- rechercher dans la base RAG géante

Lancement :
streamlit run scripts/experience_memory_streamlit.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import streamlit as st

# Permet d'importer le script voisin si lancé depuis la racine projet.
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from ai_experience_memory_rag import (  # noqa: E402
    CATALOG_PATH,
    CHROMA_DIR,
    MEMORY_ROOT,
    ensure_dirs,
    process_one_file,
    read_json,
    search_experience_memory,
)


class Args:
    def __init__(
        self,
        *,
        organisme: str,
        project: str,
        year: str,
        mode: str,
        memory_type: str,
        validated: bool,
        include_style: bool,
        collection: str,
        reset: bool,
        vision_mode: str,
        formula_mode: str,
    ):
        self.organisme = organisme
        self.project = project
        self.year = year
        self.mode = mode
        self.memory_type = memory_type
        self.validated = validated
        self.include_style = include_style
        self.collection = collection
        self.reset = reset
        self.vision_mode = vision_mode
        self.formula_mode = formula_mode


def catalog_df() -> pd.DataFrame:
    data = read_json(CATALOG_PATH, {"items": []})
    items = data.get("items") if isinstance(data, dict) else []
    if not isinstance(items, list):
        items = []
    if not items:
        return pd.DataFrame(columns=[
            "source_id", "file_name", "organisme", "project", "year",
            "mode_detected", "memory_status", "memory_type", "chunks_count",
            "created_at",
        ])
    return pd.DataFrame(items)


def show_logs(logs: List[Dict[str, Any]]) -> None:
    if not logs:
        st.info("Aucun log.")
        return
    for log in logs:
        status = log.get("status")
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


st.set_page_config(page_title="EnnoSmart Experience Memory RAG", layout="wide")
ensure_dirs()

st.title("EnnoSmart — Base RAG géante d'expérience / connaissance / style")
st.caption("Interface locale IA seulement : extraction → NLP existant → modules.RAG existant → Chroma")

with st.sidebar:
    st.header("Configuration")
    st.write("Memory root")
    st.code(str(MEMORY_ROOT))
    st.write("Chroma")
    st.code(str(CHROMA_DIR))

    st.divider()
    st.subheader("Métadonnées")
    organisme = st.text_input("Organisme", value="experience_global")
    project = st.text_input("Projet / base", value="base_experience")
    year = st.text_input("Année", value="global")

    mode = st.selectbox("Mode", ["auto", "cir_final", "raw_docs", "nlp_json"], index=0)
    memory_type = st.selectbox("Type mémoire", ["experience", "knowledge", "style"], index=0)
    validated = st.checkbox("Mémoire validée", value=True)
    include_style = st.checkbox("Ajouter chunks de style CIR", value=True)

    st.divider()
    st.subheader("RAG")
    collection = st.selectbox("Indexer dans", ["both", "global", "organism"], index=0)
    reset = st.checkbox("Reset collection avant build", value=False)
    vision_mode = st.selectbox("Vision mode", ["text_only", "auto", "fast", "full"], index=0)
    formula_mode = st.selectbox("Formula mode", ["off", "fast", "explain"], index=0)

tabs = st.tabs(["Ajouter / traiter", "Catalogue", "Recherche RAG", "Aide"])

with tabs[0]:
    st.subheader("Ajouter une source")

    source_mode = st.radio("Type d'entrée", ["Uploader fichiers", "Chemin dossier/fichier local"], horizontal=True)

    args = Args(
        organisme=organisme,
        project=project,
        year=year,
        mode=mode,
        memory_type=memory_type,
        validated=validated,
        include_style=include_style,
        collection=collection,
        reset=reset,
        vision_mode=vision_mode,
        formula_mode=formula_mode,
    )

    if source_mode == "Uploader fichiers":
        uploads = st.file_uploader(
            "Dépose des CIR finaux / docs / JSON NLP",
            accept_multiple_files=True,
            type=["pdf", "docx", "doc", "pptx", "ppt", "xlsx", "xls", "csv", "txt", "md", "msg", "eml", "json", "png", "jpg", "jpeg"],
        )

        if st.button("Traiter et indexer dans Chroma", type="primary", disabled=not uploads):
            reports = []
            with st.spinner("Traitement en cours..."):
                for i, up in enumerate(uploads):
                    suffix = Path(up.name).suffix
                    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                        tmp.write(up.getbuffer())
                        tmp_path = Path(tmp.name)

                    try:
                        rep = process_one_file(tmp_path, args=args, reset_collection=(reset and i == 0))
                        rep["uploaded_name"] = up.name
                        reports.append(rep)
                    except Exception as exc:
                        reports.append({"ok": False, "uploaded_name": up.name, "error": str(exc)})

            st.session_state["last_reports"] = reports
            st.success("Traitement terminé.")

    else:
        path_text = st.text_area("Chemins locaux, un par ligne", value="")
        if st.button("Traiter chemin(s) local(aux)", type="primary", disabled=not path_text.strip()):
            reports = []
            paths = [Path(x.strip()) for x in path_text.splitlines() if x.strip()]
            with st.spinner("Traitement en cours..."):
                first = True
                for p in paths:
                    candidates = []
                    if p.is_file():
                        candidates = [p]
                    elif p.is_dir():
                        candidates = [x for x in p.rglob("*") if x.is_file()]
                    else:
                        reports.append({"ok": False, "file": str(p), "error": "Chemin introuvable"})
                        continue

                    for f in candidates:
                        try:
                            rep = process_one_file(f, args=args, reset_collection=(reset and first))
                            first = False
                            reports.append(rep)
                        except Exception as exc:
                            reports.append({"ok": False, "file": str(f), "error": str(exc)})

            st.session_state["last_reports"] = reports
            st.success("Traitement terminé.")

    reports = st.session_state.get("last_reports") or []
    if reports:
        st.subheader("Derniers traitements")
        for rep in reports:
            if rep.get("ok"):
                st.success(f"{rep.get('file_name') or rep.get('uploaded_name')} — chunks={rep.get('chunks_count')}")
                show_logs(rep.get("logs") or [])
            else:
                st.error(f"{rep.get('file') or rep.get('uploaded_name')} — {rep.get('error')}")
            with st.expander("Réponse brute", expanded=False):
                st.json(rep)

with tabs[1]:
    st.subheader("Catalogue mémoire")
    df = catalog_df()
    st.dataframe(df, use_container_width=True)

    if not df.empty:
        st.download_button(
            "Télécharger catalogue JSON",
            data=json.dumps(read_json(CATALOG_PATH, {"items": []}), ensure_ascii=False, indent=2),
            file_name="experience_memory_catalog.json",
            mime="application/json",
        )

with tabs[2]:
    st.subheader("Recherche dans la base RAG géante")

    col1, col2, col3 = st.columns(3)
    with col1:
        collection_name = st.text_input("Collection Chroma", value="ennosmart_experience_global")
    with col2:
        top_k = st.number_input("Top K", min_value=1, max_value=30, value=8)
    with col3:
        role_filter = st.selectbox(
            "Rôle",
            ["all", "objectif", "verrou", "methode", "resultat", "etat_art", "parametre", "contribution", "limite", "style"],
            index=0,
        )

    col4, col5 = st.columns(2)
    with col4:
        memory_type_filter = st.selectbox("Type mémoire", ["all", "experience", "knowledge", "style"], index=0)
    with col5:
        memory_status_filter = st.selectbox("Statut", ["all", "validated", "working"], index=0)

    query = st.text_area("Question / recherche", value="Quels exemples de verrous et de style CIR sont disponibles ?")

    if st.button("Rechercher", type="primary", disabled=not query.strip()):
        with st.spinner("Recherche Chroma..."):
            try:
                res = search_experience_memory(
                    query,
                    collection_name=collection_name,
                    top_k=int(top_k),
                    role_filter=None if role_filter == "all" else role_filter,
                    memory_type_filter=memory_type_filter,
                    memory_status_filter=memory_status_filter,
                )
                st.session_state["last_search"] = res
            except Exception as exc:
                st.session_state["last_search"] = {"ok": False, "error": str(exc)}

    res = st.session_state.get("last_search")
    if res:
        if not res.get("ok"):
            st.error(res.get("error") or "Erreur recherche")
        else:
            st.success(f"{res.get('matches_count')} résultat(s)")
            for item in res.get("matches") or []:
                meta = item.get("metadata") or {}
                st.markdown(f"### {meta.get('section_title') or meta.get('document') or item.get('id')}")
                st.caption(
                    f"role={meta.get('role')} | style_role={meta.get('style_role')} | "
                    f"type={meta.get('memory_type')} | statut={meta.get('memory_status')} | "
                    f"doc={meta.get('document')}"
                )
                st.write(item.get("text"))
                with st.expander("Metadata", expanded=False):
                    st.json(meta)

with tabs[3]:
    st.markdown(
        """
### Principe

Cette interface ne passe pas par le backend ni le frontend React.

Elle utilise directement les modules IA :

1. `modules.extraction.router.extract`
2. `modules.NLP.CIR.cir_pipeline` pour les CIR finaux
3. `modules.NLP.pipeline` pour les documents bruts
4. `modules.RAG.json_to_chunks`
5. `modules.RAG.vector_store` avec Chroma

### Commande CLI équivalente

```powershell
python scripts/ai_experience_memory_rag.py --input "C:\\mes_cir" --organisme experience_global --project base_experience --year global --mode auto --validated --include-style --collection both --reset
```

### Collections

- `ennosmart_experience_global`
- `ennosmart_experience_<organisme>`

La base globale sert pour chercher dans toute l'expérience.
La base organisme sert pour garder une mémoire spécialisée par client.
"""
    )
