# -*- coding: utf-8 -*-
from __future__ import annotations

"""
scripts/experience_memory_manager_streamlit.py — V3 corrigé

Interface locale IA :
- Organisme -> Projet -> Année -> CIR
- Ajouter manuellement organisme / projet / année + CIR
- Supprimer une source mémoire du catalogue
- Nettoyer les faux organismes détectés
- Construire RAG avec rôle structurel corrigé
"""

import json
import os
import re
import sys
import shutil
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd
import streamlit as st

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai_experience_memory_rag import (  # noqa: E402
    CATALOG_PATH,
    CHROMA_DIR,
    MEMORY_ROOT,
    ensure_dirs,
    process_one_file,
    read_json,
    write_json,
    search_experience_memory,
)

DEFAULT_SCAN_ROOTS = [
    Path(os.getenv("ENNOSMART_STORAGE_ROOT", r"C:\EnnoSmart\storage\organismes")),
    Path(os.getenv("ENNOSMART_OUTPUT_ROOT", r"C:\EnnoSmart\outputs\safe_rag_upload")),
]

CIR_EXTS = {".docx", ".pdf", ".txt", ".md"}

CIR_NAME_PATTERNS = [
    r"\bcir\b",
    r"credit.?impot.?recherche",
    r"crédit.?impôt.?recherche",
    r"dossier.?technique",
    r"\bdt\b",
    r"vf\b",
    r"final",
]

FINAL_PATTERNS = [
    r"vf\b",
    r"final",
    r"finale",
    r"consultant",
    r"version.?finale",
]

BAD_ORGANISMES = {
    "", "2", "uploads", "upload", "current", "memory", "validated", "working",
    "documents", "source_documents", "safe_rag_upload", "outputs", "storage",
}


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


def clean_text(x: Any) -> str:
    return re.sub(r"\s+", " ", str(x or "")).strip()


def slugify(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"[^a-z0-9àâäéèêëîïôöùûüç]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "unknown"


def is_year(value: Any) -> bool:
    return bool(re.fullmatch(r"(19|20)\d{2}", str(value or "").strip()))


def is_valid_organisme(value: Any) -> bool:
    v = clean_text(value)
    if not v:
        return False
    if v.lower() in BAD_ORGANISMES:
        return False
    if v.isdigit():
        return False
    if len(v) < 3:
        return False
    return True


def file_score_for_cir(path: Path) -> int:
    name = path.name.lower()
    score = 0
    for pat in CIR_NAME_PATTERNS:
        if re.search(pat, name, flags=re.I):
            score += 2
    for pat in FINAL_PATTERNS:
        if re.search(pat, name, flags=re.I):
            score += 3
    if path.suffix.lower() == ".docx":
        score += 2
    elif path.suffix.lower() == ".pdf":
        score += 1
    if path.name.startswith("~$"):
        score -= 100
    return score


def detect_org_project_year(path: Path, scan_root: Path) -> Tuple[str, str, str]:
    try:
        rel_parts = list(path.relative_to(scan_root).parts)
    except Exception:
        rel_parts = list(path.parts)

    parts = [p for p in rel_parts if p and p not in {"memory", "validated", "working"}]
    low = [p.lower() for p in parts]

    # Cas storage/organismes/<org>/projects/<project>/years/<year>
    if "organismes" in low:
        i = low.index("organismes")
        org = parts[i + 1] if i + 1 < len(parts) else "unknown"
        project = "unknown"
        year = "unknown"

        if "projects" in low:
            j = low.index("projects")
            project = parts[j + 1] if j + 1 < len(parts) else "unknown"
        elif i + 2 < len(parts):
            project = parts[i + 2]

        if "years" in low:
            k = low.index("years")
            year = parts[k + 1] if k + 1 < len(parts) else "unknown"
        else:
            for p in parts:
                if is_year(p):
                    year = p
                    break
        return clean_text(org), clean_text(project), clean_text(year)

    # Cas root = C:\EnnoSmart\storage\organismes, donc rel = Girodin/projects/TGM100/years/2022/...
    if len(parts) >= 1 and is_valid_organisme(parts[0]):
        org = parts[0]
        project = "unknown"
        year = "unknown"

        if "projects" in low:
            j = low.index("projects")
            project = parts[j + 1] if j + 1 < len(parts) else "unknown"
        elif len(parts) >= 2:
            project = parts[1]

        if "years" in low:
            k = low.index("years")
            year = parts[k + 1] if k + 1 < len(parts) else "unknown"
        else:
            for p in parts:
                if is_year(p):
                    year = p
                    break

        return clean_text(org), clean_text(project), clean_text(year)

    # Cas safe_rag_upload/<org>/<project>/<year>
    if "safe_rag_upload" in low:
        i = low.index("safe_rag_upload")
        org = parts[i + 1] if i + 1 < len(parts) else "unknown"
        project = parts[i + 2] if i + 2 < len(parts) else "unknown"
        year = parts[i + 3] if i + 3 < len(parts) and is_year(parts[i + 3]) else "unknown"
        return clean_text(org), clean_text(project), clean_text(year)

    # Fallback : année puis deux dossiers avant
    year_idx = None
    for idx, p in enumerate(parts):
        if is_year(p):
            year_idx = idx
            break

    if year_idx is not None:
        year = parts[year_idx]
        project = parts[year_idx - 1] if year_idx - 1 >= 0 else path.parent.name
        org = parts[year_idx - 2] if year_idx - 2 >= 0 else "unknown"
        return clean_text(org), clean_text(project), clean_text(year)

    return "unknown", path.parent.name, "unknown"


def scan_cir_library(scan_roots: List[Path], only_final: bool = True, hide_invalid_orgs: bool = True) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []

    for root in scan_roots:
        if not root.exists():
            continue
        for f in root.rglob("*"):
            if not f.is_file():
                continue
            if f.suffix.lower() not in CIR_EXTS:
                continue
            if f.name.startswith("~$"):
                continue

            score = file_score_for_cir(f)
            if score <= 0:
                continue

            org, project, year = detect_org_project_year(f, root)
            if hide_invalid_orgs and not is_valid_organisme(org):
                continue

            is_final = score >= 5
            if only_final and not is_final:
                continue

            rows.append({
                "organisme": org,
                "project": project,
                "year": year,
                "file_name": f.name,
                "file_path": str(f),
                "suffix": f.suffix.lower(),
                "cir_score": score,
                "is_probable_final": is_final,
                "size_mb": round(f.stat().st_size / (1024 * 1024), 2),
                "modified_at": f.stat().st_mtime,
            })

    if not rows:
        return pd.DataFrame(columns=[
            "organisme", "project", "year", "file_name", "file_path",
            "suffix", "cir_score", "is_probable_final", "size_mb", "modified_at",
        ])

    return pd.DataFrame(rows).sort_values(
        ["organisme", "project", "year", "cir_score", "file_name"],
        ascending=[True, True, True, False, True],
    ).reset_index(drop=True)


def load_catalog_df() -> pd.DataFrame:
    data = read_json(CATALOG_PATH, {"items": []})
    items = data.get("items") if isinstance(data, dict) else []
    if not isinstance(items, list):
        items = []
    if not items:
        return pd.DataFrame(columns=[
            "source_id", "file_name", "organisme", "project", "year",
            "mode_detected", "memory_status", "memory_type", "chunks_count",
            "created_at", "file", "run_file", "chunks_file",
        ])
    return pd.DataFrame(items)


def save_catalog_df(df: pd.DataFrame) -> None:
    items = df.to_dict("records") if not df.empty else []
    write_json(CATALOG_PATH, {"items": items, "updated_at": pd.Timestamp.now().isoformat(timespec="seconds")})


def build_library_status_df(library_df: pd.DataFrame, catalog_df: pd.DataFrame) -> pd.DataFrame:
    if library_df.empty:
        return library_df

    cat_paths = set()
    cat_keys = set()

    if not catalog_df.empty:
        for _, r in catalog_df.iterrows():
            cat_paths.add(clean_text(r.get("file")).lower())
            cat_keys.add((
                clean_text(r.get("organisme")).lower(),
                clean_text(r.get("project")).lower(),
                clean_text(r.get("year")).lower(),
                clean_text(r.get("file_name")).lower(),
            ))

    rows = []
    for _, r in library_df.iterrows():
        key = (
            clean_text(r.get("organisme")).lower(),
            clean_text(r.get("project")).lower(),
            clean_text(r.get("year")).lower(),
            clean_text(r.get("file_name")).lower(),
        )
        path_key = clean_text(r.get("file_path")).lower()
        item = dict(r)
        item["memory_built"] = key in cat_keys or path_key in cat_paths
        rows.append(item)

    return pd.DataFrame(rows)


def group_summary(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["organisme", "projects_count", "years_count", "cir_files_count", "final_candidates", "memory_built_count"])
    return df.groupby("organisme").agg(
        projects_count=("project", "nunique"),
        years_count=("year", "nunique"),
        cir_files_count=("file_path", "count"),
        final_candidates=("is_probable_final", "sum"),
        memory_built_count=("memory_built", "sum"),
    ).reset_index()


def project_tree_df(df: pd.DataFrame, organisme: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    d = df[df["organisme"] == organisme].copy()
    if d.empty:
        return pd.DataFrame()

    rows = []
    for (project, year), g in d.groupby(["project", "year"]):
        best = g.sort_values(["is_probable_final", "cir_score"], ascending=[False, False]).iloc[0]
        rows.append({
            "project": project,
            "year": year,
            "cir_count": len(g),
            "best_cir": best["file_name"],
            "best_path": best["file_path"],
            "best_score": best["cir_score"],
            "final_candidate": bool(best["is_probable_final"]),
            "memory_built": bool(g["memory_built"].any()),
            "status": "✅ mémoire construite" if bool(g["memory_built"].any()) else "🟡 à construire",
        })
    return pd.DataFrame(rows).sort_values(["project", "year"])


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


def run_build_for_file(
    file_path: str,
    *,
    organisme: str,
    project: str,
    year: str,
    mode: str = "cir_final",
    memory_type: str = "experience",
    validated: bool = True,
    include_style: bool = True,
    collection: str = "both",
    reset: bool = False,
    vision_mode: str = "text_only",
    formula_mode: str = "off",
) -> Dict[str, Any]:
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
    return process_one_file(Path(file_path), args=args, reset_collection=reset)


def delete_catalog_source(source_id: str, delete_files: bool = False) -> bool:
    cat = load_catalog_df()
    if cat.empty or "source_id" not in cat.columns:
        return False

    row = cat[cat["source_id"] == source_id]
    if row.empty:
        return False

    if delete_files:
        for col in ["run_file", "chunks_file"]:
            p = row.iloc[0].get(col)
            if p:
                try:
                    Path(str(p)).unlink(missing_ok=True)
                except Exception:
                    pass

    cat = cat[cat["source_id"] != source_id].copy()
    save_catalog_df(cat)
    return True


def clean_bad_catalog_entries() -> int:
    cat = load_catalog_df()
    if cat.empty:
        return 0

    before = len(cat)
    mask = cat["organisme"].apply(is_valid_organisme)
    cat = cat[mask].copy()
    save_catalog_df(cat)
    return before - len(cat)


def copy_uploaded_to_library(uploaded_file, organisme: str, project: str, year: str) -> Path:
    target_dir = Path(r"C:\EnnoSmart\storage\organismes") / organisme / "projects" / project / "years" / str(year) / "cir_final_consultant" / "current"
    target_dir.mkdir(parents=True, exist_ok=True)

    name = uploaded_file.name
    safe_name = re.sub(r"[^\w\-. àâäéèêëîïôöùûüçÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ]+", "_", name)
    target = target_dir / safe_name

    target.write_bytes(uploaded_file.getbuffer())
    return target


st.set_page_config(page_title="EnnoSmart — Gestionnaire mémoire CIR", layout="wide")
ensure_dirs()

st.title("EnnoSmart — Gestionnaire mémoire CIR")
st.caption("Organisme → Projet → Année → CIR final → extraction/NLP/RAG Chroma")

with st.sidebar:
    st.header("Dossiers à scanner")
    roots_text = st.text_area("Chemins racines", value="\n".join(str(p) for p in DEFAULT_SCAN_ROOTS), height=100)
    scan_roots = [Path(x.strip()) for x in roots_text.splitlines() if x.strip()]

    only_final = st.checkbox("Afficher seulement les CIR finaux probables", value=True)
    hide_invalid_orgs = st.checkbox("Masquer faux organismes", value=True)

    st.divider()
    st.header("Build")
    default_mode = st.selectbox("Mode traitement", ["cir_final", "auto", "raw_docs", "nlp_json"], index=0)
    memory_type = st.selectbox("Type mémoire", ["experience", "knowledge", "style"], index=0)
    include_style = st.checkbox("Extraire aussi le style CIR", value=True)
    validated = st.checkbox("Marquer validé", value=True)
    collection = st.selectbox("Collection", ["both", "global", "organism"], index=0)
    reset_collection = st.checkbox("Reset collection avant build", value=False)
    vision_mode = st.selectbox("Vision mode", ["text_only", "auto", "fast", "full"], index=0)
    formula_mode = st.selectbox("Formula mode", ["off", "fast", "explain"], index=0)

    st.divider()
    st.write("Chroma")
    st.code(str(CHROMA_DIR))

tabs = st.tabs(["📁 Bibliothèque CIR", "➕ Ajouter", "🧠 Construction mémoire", "🔎 Recherche RAG", "📊 Statistiques", "🗑️ Nettoyage"])

if "library_df" not in st.session_state:
    st.session_state["library_df"] = pd.DataFrame()
if "last_reports" not in st.session_state:
    st.session_state["last_reports"] = []

with tabs[0]:
    st.subheader("Scanner les CIR existants")

    if st.button("Scanner maintenant", type="primary"):
        with st.spinner("Scan des dossiers..."):
            library_df = scan_cir_library(scan_roots, only_final=only_final, hide_invalid_orgs=hide_invalid_orgs)
            catalog_df = load_catalog_df()
            st.session_state["library_df"] = build_library_status_df(library_df, catalog_df)

    df = st.session_state["library_df"]

    if df.empty:
        st.warning("Aucun CIR détecté. Clique sur Scanner maintenant ou ajoute un CIR dans l'onglet ➕ Ajouter.")
    else:
        st.success(f"{len(df)} fichier(s) CIR détecté(s).")
        st.dataframe(group_summary(df), use_container_width=True)

        organismes = sorted(df["organisme"].dropna().unique().tolist())
        selected_org = st.selectbox("Organisme", organismes)
        tree = project_tree_df(df, selected_org)

        st.markdown("### Projets / années")
        st.dataframe(tree[["project", "year", "cir_count", "best_cir", "final_candidate", "memory_built", "status"]], use_container_width=True)

        st.markdown("### Fichiers CIR détectés")
        filtered = df[df["organisme"] == selected_org].copy()
        st.dataframe(
            filtered[["organisme", "project", "year", "file_name", "is_probable_final", "memory_built", "cir_score", "file_path"]],
            use_container_width=True,
        )

with tabs[1]:
    st.subheader("Ajouter un organisme / projet / année / CIR final")

    with st.form("manual_add"):
        c1, c2, c3 = st.columns(3)
        with c1:
            new_org = st.text_input("Organisme", value="")
        with c2:
            new_project = st.text_input("Projet", value="")
        with c3:
            new_year = st.text_input("Année", value="2024")

        uploaded = st.file_uploader("CIR final", type=["docx", "pdf", "txt", "md"])
        process_now = st.checkbox("Traiter immédiatement après ajout", value=True)

        submitted = st.form_submit_button("Ajouter", type="primary")

    if submitted:
        if not is_valid_organisme(new_org):
            st.error("Organisme invalide.")
        elif not clean_text(new_project):
            st.error("Projet obligatoire.")
        elif not is_year(new_year):
            st.error("Année invalide.")
        elif not uploaded:
            st.error("Ajoute un fichier CIR final.")
        else:
            try:
                target = copy_uploaded_to_library(uploaded, clean_text(new_org), clean_text(new_project), clean_text(new_year))
                st.success(f"CIR ajouté : {target}")

                rep = None
                if process_now:
                    with st.spinner("Traitement extraction + NLP + Chroma..."):
                        rep = run_build_for_file(
                            str(target),
                            organisme=clean_text(new_org),
                            project=clean_text(new_project),
                            year=clean_text(new_year),
                            mode="cir_final",
                            memory_type=memory_type,
                            validated=True,
                            include_style=include_style,
                            collection=collection,
                            reset=reset_collection,
                            vision_mode=vision_mode,
                            formula_mode=formula_mode,
                        )
                        st.session_state["last_reports"] = [rep]
                    st.success("Traitement terminé.")

                library_df = scan_cir_library(scan_roots, only_final=only_final, hide_invalid_orgs=hide_invalid_orgs)
                st.session_state["library_df"] = build_library_status_df(library_df, load_catalog_df())

                if rep:
                    show_logs(rep.get("logs") or [])
            except Exception as exc:
                st.error(str(exc))

with tabs[2]:
    st.subheader("Construire / reconstruire la mémoire")

    df = st.session_state["library_df"]
    if df.empty:
        st.warning("Scanne d'abord la bibliothèque.")
    else:
        orgs = sorted(df["organisme"].dropna().unique().tolist())
        org = st.selectbox("Organisme à traiter", orgs, key="build_org")
        dorg = df[df["organisme"] == org].copy()

        projects = sorted(dorg["project"].dropna().unique().tolist())
        project = st.selectbox("Projet", projects, key="build_project")
        dproj = dorg[dorg["project"] == project].copy()

        years = sorted(dproj["year"].dropna().unique().tolist())
        year = st.selectbox("Année", years, key="build_year")
        dyear = dproj[dproj["year"] == year].sort_values(["is_probable_final", "cir_score"], ascending=[False, False])

        file_labels = [f"{r.file_name} | score={r.cir_score} | {'final probable' if r.is_probable_final else 'candidat'}" for r in dyear.itertuples()]
        selected_label = st.selectbox("CIR à utiliser", file_labels)
        selected_row = dyear.iloc[file_labels.index(selected_label)]
        st.code(selected_row["file_path"])

        c1, c2, c3 = st.columns(3)
        with c1:
            if st.button("Construire ce CIR", type="primary"):
                with st.spinner("Extraction + NLP + Chroma..."):
                    rep = run_build_for_file(
                        selected_row["file_path"], organisme=org, project=project, year=year,
                        mode=default_mode, memory_type=memory_type, validated=validated,
                        include_style=include_style, collection=collection, reset=reset_collection,
                        vision_mode=vision_mode, formula_mode=formula_mode,
                    )
                    st.session_state["last_reports"] = [rep]
                    st.session_state["library_df"] = build_library_status_df(df, load_catalog_df())
                st.success("Build terminé.")

        with c2:
            if st.button("Construire tous les CIR de cette année"):
                reports = []
                with st.spinner("Traitement de l'année..."):
                    for _, row in dyear.iterrows():
                        rep = run_build_for_file(
                            row["file_path"], organisme=org, project=project, year=year,
                            mode=default_mode, memory_type=memory_type, validated=validated,
                            include_style=include_style, collection=collection, reset=reset_collection and len(reports) == 0,
                            vision_mode=vision_mode, formula_mode=formula_mode,
                        )
                        reports.append(rep)
                    st.session_state["last_reports"] = reports
                    st.session_state["library_df"] = build_library_status_df(df, load_catalog_df())
                st.success("Build année terminé.")

        with c3:
            if st.button("Construire tout l'organisme"):
                reports = []
                with st.spinner("Traitement de l'organisme..."):
                    for _, row in dorg.sort_values(["project", "year", "cir_score"], ascending=[True, True, False]).iterrows():
                        rep = run_build_for_file(
                            row["file_path"], organisme=org, project=str(row["project"]), year=str(row["year"]),
                            mode=default_mode, memory_type=memory_type, validated=validated,
                            include_style=include_style, collection=collection, reset=reset_collection and len(reports) == 0,
                            vision_mode=vision_mode, formula_mode=formula_mode,
                        )
                        reports.append(rep)
                    st.session_state["last_reports"] = reports
                    st.session_state["library_df"] = build_library_status_df(df, load_catalog_df())
                st.success("Build organisme terminé.")

    reports = st.session_state.get("last_reports") or []
    if reports:
        st.markdown("### Derniers logs")
        for rep in reports:
            if rep.get("ok"):
                st.success(f"{rep.get('file_name')} — chunks={rep.get('chunks_count')} — mode={rep.get('mode_detected')}")
                show_logs(rep.get("logs") or [])
            else:
                st.error(rep.get("error") or "Erreur")
            with st.expander("Rapport brut", expanded=False):
                st.json(rep)

with tabs[3]:
    st.subheader("Recherche dans la base RAG")

    c1, c2, c3 = st.columns(3)
    with c1:
        collection_name = st.text_input("Collection Chroma", value="ennosmart_experience_global")
    with c2:
        top_k = st.number_input("Top K", min_value=1, max_value=30, value=8)
    with c3:
        role_filter = st.selectbox("Rôle", ["all", "objectif", "verrou", "methode", "resultat", "etat_art", "parametre", "contribution", "limite", "style"], index=0)

    c4, c5 = st.columns(2)
    with c4:
        memory_type_filter = st.selectbox("Type mémoire", ["all", "experience", "knowledge", "style"], index=0)
    with c5:
        memory_status_filter = st.selectbox("Statut", ["all", "validated", "working"], index=0)

    query = st.text_area("Question", value="Trouve des exemples d'état de l'art et de verrous CIR.")

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
                title = meta.get("section_title") or meta.get("document") or item.get("id")
                st.markdown(f"### {title}")
                st.caption(
                    f"org={meta.get('organisme')} | projet={meta.get('project')} | année={meta.get('year')} | "
                    f"role={meta.get('role')} | avant={meta.get('role_before_memory_normalization')} | "
                    f"type={meta.get('memory_type')} | statut={meta.get('memory_status')}"
                )
                st.write(item.get("text"))
                with st.expander("Metadata"):
                    st.json(meta)

with tabs[4]:
    st.subheader("Statistiques")
    df = st.session_state["library_df"]
    catalog = load_catalog_df()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("CIR détectés", 0 if df.empty else len(df))
    c2.metric("Organismes", 0 if df.empty else df["organisme"].nunique())
    c3.metric("Projets", 0 if df.empty else df["project"].nunique())
    c4.metric("Sources indexées", 0 if catalog.empty else len(catalog))

    if not catalog.empty:
        stats = catalog.groupby("organisme").agg(
            sources=("source_id", "count"),
            chunks=("chunks_count", "sum"),
            projects=("project", "nunique"),
            years=("year", "nunique"),
        ).reset_index()
        st.dataframe(stats, use_container_width=True)

with tabs[5]:
    st.subheader("Nettoyage / suppression")

    if st.button("Supprimer faux organismes du catalog.json"):
        n = clean_bad_catalog_entries()
        st.success(f"{n} entrée(s) supprimée(s) du catalogue.")

    catalog = load_catalog_df()
    if catalog.empty:
        st.info("Catalogue vide.")
    else:
        st.dataframe(catalog[["source_id", "organisme", "project", "year", "file_name", "chunks_count", "created_at"]], use_container_width=True)

        source_id = st.selectbox("Source à supprimer du catalogue", catalog["source_id"].tolist())
        delete_files = st.checkbox("Supprimer aussi run_file/chunks_file", value=False)

        if st.button("Supprimer cette source", type="primary"):
            ok = delete_catalog_source(source_id, delete_files=delete_files)
            if ok:
                st.success("Source supprimée du catalogue.")
            else:
                st.error("Source introuvable.")

    st.warning("Attention : la suppression du catalogue ne supprime pas encore les embeddings déjà écrits dans Chroma. Pour repartir propre, utilise Reset collection avant un nouveau build.")
