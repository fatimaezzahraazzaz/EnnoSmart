# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import streamlit as st

from modules.DOC_COMPARE.document_compare import (
    compare_documents,
    SUPPORTED_EXTENSIONS,
    discover_comparable_file_pairs,
    compare_pair_to_report,
)


st.set_page_config(
    page_title="EnnoSmart - Comparaison documents",
    layout="wide",
)

st.title("Comparaison de documents")
st.caption(
    "Comparer deux documents A et B pour identifier les passages communs, "
    "les passages présents seulement dans A, seulement dans B, et les passages différents."
)

ALLOWED_EXT = sorted([x.replace(".", "") for x in SUPPORTED_EXTENSIONS])


# =========================================================
# Utils
# =========================================================

def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path, default=None):
    if not path.exists():
        return default

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_uploaded_file(file, target_dir: Path) -> str:
    target_dir.mkdir(parents=True, exist_ok=True)

    path = target_dir / Path(file.name).name

    if path.exists():
        stem = path.stem
        suffix = path.suffix
        i = 1

        while True:
            candidate = target_dir / f"{stem} ({i}){suffix}"
            if not candidate.exists():
                path = candidate
                break
            i += 1

    path.write_bytes(file.getbuffer())
    return str(path).replace("\\", "/")


def list_project_files(folder: Path) -> List[str]:
    allowed = {f".{x}" for x in ALLOWED_EXT}

    if not folder.exists():
        return []

    return [
        str(p).replace("\\", "/")
        for p in sorted(folder.iterdir(), key=lambda x: x.name.lower())
        if p.is_file() and p.suffix.lower() in allowed
    ]


def safe_html(text: str) -> str:
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br>")
    )


# =========================================================
# CSS
# =========================================================

def css() -> None:
    st.markdown(
        """
        <style>
        .docbox {
            border: 1px solid #e5e7eb;
            border-radius: 12px;
            padding: 14px;
            background: #ffffff;
            margin-bottom: 12px;
            max-height: 420px;
            overflow-y: auto;
            line-height: 1.55;
            font-size: 0.93rem;
        }
        .only-a-block {
            border-left: 5px solid #ef4444;
            background: #fef2f2;
            padding: 10px;
            border-radius: 8px;
            margin-bottom: 10px;
        }
        .only-b-block {
            border-left: 5px solid #22c55e;
            background: #f0fdf4;
            padding: 10px;
            border-radius: 8px;
            margin-bottom: 10px;
        }
        .different-block {
            border-left: 5px solid #f59e0b;
            background: #fffbeb;
            padding: 10px;
            border-radius: 8px;
            margin-bottom: 10px;
        }
        .common-block {
            border-left: 5px solid #64748b;
            background: #f8fafc;
            padding: 10px;
            border-radius: 8px;
            margin-bottom: 10px;
        }
        mark.add {
            background: #bbf7d0;
            color: #14532d;
            padding: 1px 3px;
            border-radius: 4px;
        }
        mark.del {
            background: #fecaca;
            color: #7f1d1d;
            padding: 1px 3px;
            border-radius: 4px;
            text-decoration: line-through;
        }
        mark.chg {
            background: #fde68a;
            color: #78350f;
            padding: 1px 3px;
            border-radius: 4px;
        }
        .small-note {
            color: #64748b;
            font-size: 0.88rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_html_block(content: str, css_class: str = "docbox") -> None:
    st.markdown(f"<div class='{css_class}'>{content}</div>", unsafe_allow_html=True)


def render_plain_block(text: str, css_class: str) -> None:
    render_html_block(safe_html(text), css_class)


# =========================================================
# Render report
# =========================================================

def render_summary(summary: Dict[str, Any]) -> None:
    identical_count = summary.get("identical_count", 0)
    different_count = summary.get("different_count", summary.get("modified_count", 0))
    only_in_a_count = summary.get("only_in_a_count", summary.get("removed_count", 0))
    only_in_b_count = summary.get("only_in_b_count", summary.get("added_count", 0))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Blocs document A", summary.get("blocks_a", 0))
    c2.metric("Blocs document B", summary.get("blocks_b", 0))
    c3.metric("Différents A/B", different_count)
    c4.metric("Taux de différence", summary.get("change_rate", 0))

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Communs", identical_count)
    c6.metric("Seulement dans A", only_in_a_count)
    c7.metric("Seulement dans B", only_in_b_count)
    c8.metric("Caractères A/B", f"{summary.get('chars_a', 0)} / {summary.get('chars_b', 0)}")


def get_different_items(comparison: Dict[str, Any]) -> List[Dict[str, Any]]:
    return comparison.get("different_between_a_b") or comparison.get("modified") or []


def get_only_a_items(comparison: Dict[str, Any]) -> List[Dict[str, Any]]:
    return comparison.get("only_in_a") or comparison.get("removed_from_a") or []


def get_only_b_items(comparison: Dict[str, Any]) -> List[Dict[str, Any]]:
    return comparison.get("only_in_b") or comparison.get("added_in_b") or []


def get_common_items(comparison: Dict[str, Any]) -> List[Dict[str, Any]]:
    return comparison.get("identical") or []


def render_different(comparison: Dict[str, Any], max_items: int) -> None:
    items = get_different_items(comparison)[:max_items]

    if not items:
        st.success("Aucun passage différent détecté entre A et B.")
        return

    for i, it in enumerate(items, start=1):
        ctx = it.get("context_key", "")
        st.markdown(f"### Différence {i} — score {it.get('score')} — contexte `{ctx}`")

        col1, col2 = st.columns(2)

        with col1:
            st.write("**Document A**")
            render_html_block(it.get("left_html") or safe_html(it.get("a_text", "")), "different-block")

        with col2:
            st.write("**Document B**")
            render_html_block(it.get("right_html") or safe_html(it.get("b_text", "")), "different-block")


def render_only_a(comparison: Dict[str, Any], max_items: int) -> None:
    items = get_only_a_items(comparison)[:max_items]

    if not items:
        st.success("Aucun passage présent seulement dans A.")
        return

    for i, it in enumerate(items, start=1):
        st.markdown(f"### Seulement dans A — élément {i} — contexte `{it.get('context_key', '')}`")
        render_plain_block(it.get("text", ""), "only-a-block")


def render_only_b(comparison: Dict[str, Any], max_items: int) -> None:
    items = get_only_b_items(comparison)[:max_items]

    if not items:
        st.success("Aucun passage présent seulement dans B.")
        return

    for i, it in enumerate(items, start=1):
        st.markdown(f"### Seulement dans B — élément {i} — contexte `{it.get('context_key', '')}`")
        render_plain_block(it.get("text", ""), "only-b-block")


def render_common(comparison: Dict[str, Any], max_items: int) -> None:
    items = get_common_items(comparison)[:max_items]

    if not items:
        st.warning("Aucun passage commun détecté.")
        return

    for i, it in enumerate(items, start=1):
        st.markdown(f"### Commun aux deux — élément {i} — contexte `{it.get('context_key', '')}`")
        render_plain_block(it.get("text", ""), "common-block")


def render_report(report: Dict[str, Any], key_prefix: str = "report") -> None:
    if not report:
        st.warning("Aucun rapport à afficher.")
        return

    if not report.get("ok"):
        st.error(report.get("error", "Erreur."))
        st.json(report)
        return

    summary = report.get("summary", {})
    comparison = report.get("comparison", {})

    st.write("**Document A :**", summary.get("doc_a"))
    st.write("**Document B :**", summary.get("doc_b"))

    render_summary(summary)

    st.markdown(
        """
        <div class='small-note'>
        Rouge = présent seulement dans A · Vert = présent seulement dans B ·
        Jaune = différent entre A et B · Gris = commun aux deux.
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.divider()

    max_items = st.slider(
        "Nombre maximum d’éléments à afficher",
        min_value=5,
        max_value=200,
        value=40,
        step=5,
        key=f"{key_prefix}_max_items",
    )

    result_tab1, result_tab2, result_tab3, result_tab4, result_tab5 = st.tabs(
        [
            "Différents entre A et B",
            "Seulement dans A",
            "Seulement dans B",
            "Communs aux deux",
            "JSON brut",
        ]
    )

    with result_tab1:
        render_different(comparison, max_items)

    with result_tab2:
        render_only_a(comparison, max_items)

    with result_tab3:
        render_only_b(comparison, max_items)

    with result_tab4:
        render_common(comparison, max_items)

    with result_tab5:
        st.json(report)


# =========================================================
# Main
# =========================================================

css()

with st.sidebar:
    st.header("Projet")
    organisme = st.text_input("Organisme", "Girodin")
    project = st.text_input("Projet", "TGM100")
    year = st.text_input("Année", "2023")

    base_out_dir = Path("outputs") / "safe_rag_upload" / organisme / project / str(year)
    uploaded_dir = base_out_dir / "uploaded"
    compare_dir = base_out_dir / "doc_compare"
    auto_dir = compare_dir / "auto"

    st.write("Dossier documents projet :")
    st.code(str(uploaded_dir))

    st.write("Dossier comparaison :")
    st.code(str(compare_dir))


tab_auto, tab_select, tab_result, tab_exports = st.tabs(
    [
        "0. Paires détectées du projet",
        "1. Comparer deux documents",
        "2. Résultat comparaison",
        "3. Exports",
    ]
)


# =========================================================
# Tab auto
# =========================================================

with tab_auto:
    st.subheader("Paires détectées automatiquement")

    st.info(
        "Le module détecte les fichiers qui peuvent être des versions, doublons ou variantes proches. "
        "Il ne compare pas toutes les paires automatiquement : tu choisis une paire, puis il affiche uniquement sa comparaison."
    )

    col_settings1, col_settings2, col_settings3 = st.columns(3)

    with col_settings1:
        min_similarity = st.slider(
            "Similarité minimale",
            min_value=0.50,
            max_value=0.95,
            value=0.70,
            step=0.05,
        )

    with col_settings2:
        include_medium = st.checkbox(
            "Inclure paires moyennes à confirmer",
            value=True,
        )

    with col_settings3:
        max_pairs = st.slider(
            "Nombre max de paires affichées",
            min_value=5,
            max_value=50,
            value=20,
            step=5,
        )

    pairs = discover_comparable_file_pairs(
        str(uploaded_dir),
        min_similarity=min_similarity,
        include_medium=include_medium,
        max_pairs=max_pairs,
    )

    if not pairs:
        st.warning("Aucune paire détectée avec ces critères.")
    else:
        strong_count = sum(1 for p in pairs if p.get("decision") == "strong")
        medium_count = sum(1 for p in pairs if p.get("decision") == "medium")

        st.success(
            f"{len(pairs)} paire(s) affichée(s) : "
            f"{strong_count} forte(s), {medium_count} moyenne(s)."
        )

        labels = []
        for i, p in enumerate(pairs, start=1):
            label = (
                f"{i}. [{p.get('decision')}] {p.get('name_a')} ↔ {p.get('name_b')} "
                f"(sim={p.get('similarity')}, raison={p.get('reason')})"
            )
            labels.append(label)

        selected_label = st.selectbox(
            "Choisir une paire à comparer",
            labels,
        )

        selected_index = labels.index(selected_label)
        selected_pair = pairs[selected_index]

        st.write("**Document A :**")
        st.code(selected_pair.get("file_a"))

        st.write("**Document B :**")
        st.code(selected_pair.get("file_b"))

        c1, c2, c3 = st.columns(3)
        c1.metric("Décision", selected_pair.get("decision"))
        c2.metric("Similarité", selected_pair.get("similarity"))
        c3.metric("Raison", selected_pair.get("reason"))

        st.write("Tokens communs :")
        st.write(", ".join(selected_pair.get("common_tokens") or []))

        if st.button("Comparer uniquement cette paire", type="primary"):
            with st.spinner("Comparaison de la paire sélectionnée..."):
                report = compare_pair_to_report(
                    selected_pair["file_a"],
                    selected_pair["file_b"],
                    str(auto_dir),
                    force=True,
                )

            st.session_state["doc_compare_report"] = report
            st.session_state["selected_auto_pair"] = selected_pair

            save_json(compare_dir / "last_doc_compare_report.json", report)

            st.success("Comparaison terminée pour cette paire uniquement.")

        if st.session_state.get("doc_compare_report"):
            st.divider()
            st.subheader("Résultat de la paire sélectionnée")
            render_report(st.session_state["doc_compare_report"], key_prefix="auto_pair")


# =========================================================
# Tab manual
# =========================================================

with tab_select:
    st.subheader("Comparer deux documents A/B")

    mode = st.radio(
        "Source des documents",
        [
            "Utiliser documents déjà uploadés du projet",
            "Uploader deux documents à comparer",
        ],
    )

    doc_a_path = None
    doc_b_path = None

    if mode == "Utiliser documents déjà uploadés du projet":
        files = list_project_files(uploaded_dir)

        if not files:
            st.warning("Aucun document trouvé dans le dossier uploaded du projet.")
        else:
            doc_a_path = st.selectbox("Document A", files, index=0)
            default_b = 1 if len(files) > 1 else 0
            doc_b_path = st.selectbox("Document B", files, index=default_b)

    else:
        col1, col2 = st.columns(2)

        with col1:
            file_a = st.file_uploader("Document A", type=ALLOWED_EXT, key="doc_compare_a")

        with col2:
            file_b = st.file_uploader("Document B", type=ALLOWED_EXT, key="doc_compare_b")

        if file_a:
            doc_a_path = save_uploaded_file(file_a, compare_dir / "uploaded")
            st.success("Document A sauvegardé.")
            st.code(doc_a_path)

        if file_b:
            doc_b_path = save_uploaded_file(file_b, compare_dir / "uploaded")
            st.success("Document B sauvegardé.")
            st.code(doc_b_path)

    st.divider()

    if doc_a_path and doc_b_path:
        st.write("**Document A :**")
        st.code(doc_a_path)

        st.write("**Document B :**")
        st.code(doc_b_path)

        if st.button("Comparer Document A et Document B", type="primary"):
            with st.spinner("Comparaison en cours..."):
                report = compare_documents(doc_a_path, doc_b_path)

            save_json(compare_dir / "last_doc_compare_report.json", report)

            if report.get("ok"):
                st.session_state["doc_compare_report"] = report
                st.success("Comparaison terminée.")
                render_summary(report.get("summary", {}))
            else:
                st.error(report.get("error", "Erreur inconnue."))
                st.json(report)


# =========================================================
# Tab result
# =========================================================

with tab_result:
    st.subheader("Résultat de comparaison")

    report = st.session_state.get("doc_compare_report") or read_json(
        compare_dir / "last_doc_compare_report.json"
    )

    if not report:
        st.warning("Aucune comparaison disponible.")
    else:
        render_report(report, key_prefix="main_result")


# =========================================================
# Tab exports
# =========================================================

with tab_exports:
    st.subheader("Exports")

    st.write("Dossier export manuel :")
    st.code(str(compare_dir))

    st.write("Dossier export auto :")
    st.code(str(auto_dir))

    report_path = compare_dir / "last_doc_compare_report.json"

    if report_path.exists():
        st.success("Rapport disponible : last_doc_compare_report.json")

        with open(report_path, "rb") as f:
            st.download_button(
                label="Télécharger le rapport JSON",
                data=f,
                file_name="last_doc_compare_report.json",
                mime="application/json",
            )

        st.json(read_json(report_path))
    else:
        st.warning("Aucun rapport exporté.")
