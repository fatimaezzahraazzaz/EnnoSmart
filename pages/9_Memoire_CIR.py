# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional

import streamlit as st


st.set_page_config(page_title="EnnoSmart - Mémoire CIR", layout="wide")

st.title("Mémoire CIR / Comparaison N-1")
st.caption(
    "Enregistrer un CIR final précédent sans Frascati, puis comparer l'année courante avec cette mémoire CIR."
)


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


def run_command(cmd: List[str], cwd: Path) -> Dict[str, Any]:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )
    return {
        "cmd": " ".join(str(x) for x in cmd),
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "ok": proc.returncode == 0,
    }


def save_uploaded_file(uploaded_file, target_dir: Path) -> Optional[Path]:
    if uploaded_file is None:
        return None
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / Path(uploaded_file.name).name
    if path.exists():
        stem, suffix = path.stem, path.suffix
        i = 1
        while True:
            candidate = target_dir / f"{stem} ({i}){suffix}"
            if not candidate.exists():
                path = candidate
                break
            i += 1
    path.write_bytes(uploaded_file.getbuffer())
    return path


def latest_file(folder: Path, suffixes=(".docx", ".pdf", ".doc")) -> Optional[Path]:
    if not folder.exists():
        return None
    files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in suffixes]
    if not files:
        return None
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def status_icon(path: Path) -> str:
    return "✅" if path.exists() else "❌"


def short_text(text: str, limit: int = 350) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


def slug(x: str) -> str:
    import re
    x = str(x or "").strip().lower()
    x = re.sub(r"[^\w\-]+", "_", x)
    x = re.sub(r"_+", "_", x).strip("_")
    return x or "unknown"


def storage_year_dir(organisme: str, project: str, year: str) -> Path:
    return Path("storage") / "organismes" / slug(organisme) / "projects" / slug(project) / "years" / str(year)


def render_command_result(result: Dict[str, Any], title: str):
    if not result:
        return
    with st.expander(title, expanded=not result.get("ok")):
        st.write("Commande :")
        st.code(result.get("cmd", ""))
        st.write("Code retour :", result.get("returncode"))
        if result.get("stdout"):
            st.markdown("**Sortie**")
            st.code(result.get("stdout"))
        if result.get("stderr"):
            st.markdown("**Erreurs**")
            st.code(result.get("stderr"))


def render_summary(summary: Dict[str, Any]):
    if not summary:
        st.info("Aucun résumé disponible.")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Items courants", summary.get("current_items_count", 0))
    c2.metric("Items CIR précédent", summary.get("previous_cir_items_count", 0))
    c3.metric("Verrous comparés", summary.get("verrou_count", 0))
    c4.metric("Score nouveauté", summary.get("project_novelty_score", "—"))

    c5, c6, c7 = st.columns(3)
    c5.metric("Nouveaux", summary.get("new_verrou_count", 0))
    c6.metric("Évolutions", summary.get("evolution_verrou_count", 0))
    c7.metric("Continuités fortes", summary.get("continuity_verrou_count", 0))

    signal = summary.get("frascati_context_signal")
    explanation = summary.get("frascati_context_explanation")
    if signal:
        st.info(f"Signal : **{signal}** — {explanation or ''}")


def render_verrou_comparisons(report: Dict[str, Any]):
    rows = report.get("verrou_comparisons") or []
    if not rows:
        st.warning("Aucune comparaison de verrou disponible.")
        return

    st.subheader("Verrous N comparés au CIR précédent")

    table = []
    for i, x in enumerate(rows, 1):
        cur = x.get("current_item") or {}
        best = x.get("best_match") or {}
        prev = best.get("previous_candidate") or {}
        dec = x.get("decision") or {}
        details = (best.get("similarity_details") or {}) if isinstance(best, dict) else {}

        table.append({
            "#": i,
            "statut": dec.get("status"),
            "nouveauté": dec.get("novelty_score"),
            "continuité": dec.get("continuity_score"),
            "thèmes partagés": ", ".join(details.get("shared_themes") or []),
            "verrou courant": short_text(cur.get("text"), 140),
            "CIR précédent": short_text(prev.get("text"), 140),
        })

    st.dataframe(table, use_container_width=True, hide_index=True)

    st.markdown("### Détail par verrou")
    for i, x in enumerate(rows, 1):
        cur = x.get("current_item") or {}
        best = x.get("best_match") or {}
        prev = best.get("previous_candidate") or {}
        dec = x.get("decision") or {}
        details = (best.get("similarity_details") or {}) if isinstance(best, dict) else {}

        title = f"{i}. {dec.get('status')} — nouveauté {dec.get('novelty_score')} / continuité {dec.get('continuity_score')}"
        with st.expander(title, expanded=False):
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Verrou / signal courant N**")
                st.write(cur.get("text") or "")
                st.caption(f"Document : {cur.get('document') or '—'}")
            with c2:
                st.markdown("**Meilleur passage CIR précédent**")
                st.write(prev.get("text") or "")
                st.caption(f"Année : {prev.get('year') or '—'} | Rôle : {prev.get('role') or '—'}")

            st.markdown("**Détails scoring**")
            d1, d2, d3, d4 = st.columns(4)
            d1.metric("similarité texte", details.get("sequence", 0))
            d2.metric("mots clés", details.get("keyword_jaccard", 0))
            d3.metric("thème", details.get("theme_score", 0))
            d4.metric("nombres", details.get("number_score", 0))

            if details.get("shared_themes"):
                st.success("Thèmes partagés : " + ", ".join(details.get("shared_themes")))
            if details.get("current_keywords"):
                st.write("Mots clés N :", ", ".join(details.get("current_keywords") or []))
            if details.get("previous_keywords"):
                st.write("Mots clés CIR précédent :", ", ".join(details.get("previous_keywords") or []))


with st.sidebar:
    st.header("Projet")

    organisme = st.text_input("Organisme", "Girodin")
    project = st.text_input("Projet", "TGM100")
    current_year = st.text_input("Année courante N", "2023")
    previous_year = st.text_input("Année CIR précédent", "2022")

    root = Path(".").resolve()

    current_out_dir = Path("outputs") / "safe_rag_upload" / organisme / project / str(current_year)
    previous_out_dir = Path("outputs") / "safe_rag_upload" / organisme / project / str(previous_year)
    previous_upload_dir = previous_out_dir / "uploaded"

    previous_nlp_path = previous_out_dir / "nlp_result.json"
    current_nlp_path = current_out_dir / "nlp_result.json"

    previous_storage_dir = storage_year_dir(organisme, project, previous_year)
    current_storage_dir = storage_year_dir(organisme, project, current_year)

    cir_memory_path = previous_storage_dir / "cir_final" / "cir_final_extracted.json"
    comparison_path = current_storage_dir / "cir_memory" / "cir_memory_comparison_report.json"

    st.write("NLP courant :")
    st.code(str(current_nlp_path))
    st.write("Mémoire CIR précédent :")
    st.code(str(cir_memory_path))


tab_register, tab_compare, tab_report, tab_status = st.tabs(
    [
        "1. Enregistrer CIR précédent",
        "2. Comparer année courante",
        "3. Rapport mémoire CIR",
        "4. État fichiers",
    ]
)


with tab_register:
    st.subheader("1. Enregistrer un CIR final précédent sans Frascati")
    st.info(
        "Le CIR final précédent est traité comme un document structuré validé. "
        "On garde ses sections telles qu'elles sont, sans FrascatiGuard."
    )

    uploaded_cir = st.file_uploader(
        "CIR final précédent",
        type=["docx", "doc", "pdf"],
        accept_multiple_files=False,
    )

    col_save, col_pipeline, col_register = st.columns(3)

    with col_save:
        if st.button("Sauvegarder CIR N-1"):
            saved = save_uploaded_file(uploaded_cir, previous_upload_dir)
            if saved:
                meta = {
                    "organisme": organisme,
                    "project": project,
                    "year": previous_year,
                    "saved_at": datetime.now().isoformat(timespec="seconds"),
                    "file": str(saved).replace("\\", "/"),
                    "note": "CIR final précédent destiné à la mémoire CIR sans Frascati.",
                }
                write_json(previous_out_dir / "cir_final_upload.json", meta)
                st.success("CIR sauvegardé.")
                st.json(meta)
            else:
                st.error("Aucun fichier CIR sélectionné.")

    previous_cir_file = latest_file(previous_upload_dir)

    with col_pipeline:
        if st.button("Lancer Extraction/NLP du CIR N-1"):
            if not previous_cir_file:
                st.error("Aucun CIR précédent sauvegardé.")
            else:
                worker = root / "safe_pipeline_worker.py"
                if not worker.exists():
                    st.error("safe_pipeline_worker.py introuvable.")
                else:
                    cmd = [
                        sys.executable,
                        str(worker),
                        "--mode", "pipeline",
                        "--organisme", organisme,
                        "--project", project,
                        "--year", str(previous_year),
                        "--out-dir", str(previous_out_dir),
                        "--folder", str(previous_upload_dir),
                        "--include-cir-final",
                    ]
                    with st.spinner("Pipeline CIR N-1 en cours..."):
                        result = run_command(cmd, cwd=root)
                    write_json(previous_out_dir / "cir_memory_pipeline_run.json", result)
                    if result.get("ok"):
                        st.success("Pipeline CIR N-1 terminé.")
                    else:
                        st.error("Erreur pipeline CIR N-1.")
                    render_command_result(result, "Détail commande pipeline")

    with col_register:
        if st.button("Indexer CIR N-1 sans Frascati", type="primary"):
            previous_cir_file = latest_file(previous_upload_dir)
            if not previous_cir_file:
                st.error("Aucun CIR précédent sauvegardé.")
            elif not previous_nlp_path.exists():
                st.error("nlp_result.json du CIR N-1 introuvable. Lance d'abord le pipeline.")
            else:
                script = root / "run_register_final_cir_from_nlp.py"
                if not script.exists():
                    st.error("run_register_final_cir_from_nlp.py introuvable.")
                else:
                    cmd = [
                        sys.executable,
                        str(script),
                        "--organisme", organisme,
                        "--project", project,
                        "--year", str(previous_year),
                        "--cir-final", str(previous_cir_file),
                        "--nlp-result", str(previous_nlp_path),
                    ]
                    with st.spinner("Indexation mémoire CIR en cours..."):
                        result = run_command(cmd, cwd=root)
                    write_json(previous_out_dir / "cir_memory_register_run.json", result)
                    if result.get("ok"):
                        st.success("CIR final indexé comme mémoire sans Frascati.")
                    else:
                        st.error("Erreur indexation CIR mémoire.")
                    render_command_result(result, "Détail commande indexation")

    st.divider()
    st.markdown("### CIR N-1 détecté")
    if previous_cir_file:
        st.write("📄", previous_cir_file.name)
    else:
        st.info("Aucun CIR sauvegardé.")

    st.markdown("### Mémoire CIR actuelle")
    memory = read_json(cir_memory_path, {})
    if memory:
        c1, c2, c3 = st.columns(3)
        c1.metric("Items", memory.get("items_count", 0))
        c2.metric("Pack source", memory.get("pack_source", "—"))
        c3.metric("Version", memory.get("version", "—"))
        st.write("Rôles :", memory.get("roles"))
        chroma = memory.get("chroma") or {}
        if chroma.get("ok"):
            st.success(f"Chroma OK : {chroma.get('collection')} | chunks={chroma.get('chunks_indexed')}")
        elif chroma:
            st.warning(f"Chroma non OK : {chroma.get('error')}")
    else:
        st.warning("Aucune mémoire CIR indexée pour cette année précédente.")


with tab_compare:
    st.subheader("2. Comparer les documents bruts de l'année N avec le CIR précédent")
    st.info(
        "Cette action compare les verrous/signaux issus des documents bruts N avec la mémoire CIR N-1. "
        "Les bruts utilisent Frascati, le CIR précédent non."
    )

    col_check, col_run = st.columns([1, 1])

    with col_check:
        st.markdown("### Pré-requis")
        st.write(status_icon(current_nlp_path), "NLP année courante", str(current_nlp_path))
        st.write(status_icon(cir_memory_path), "Mémoire CIR précédent", str(cir_memory_path))

    with col_run:
        top_k = st.number_input("Top matches par item", min_value=1, max_value=8, value=3, step=1)

        if st.button("Comparer avec la mémoire CIR", type="primary"):
            if not current_nlp_path.exists():
                st.error("nlp_result.json de l'année courante introuvable. Lance d'abord le pipeline des bruts.")
            elif not cir_memory_path.exists():
                st.error("Mémoire CIR précédente introuvable. Indexe d'abord le CIR N-1.")
            else:
                script = root / "run_compare_cir_memory_from_nlp.py"
                if not script.exists():
                    st.error("run_compare_cir_memory_from_nlp.py introuvable.")
                else:
                    cmd = [
                        sys.executable,
                        str(script),
                        "--organisme", organisme,
                        "--project", project,
                        "--year", str(current_year),
                        "--top-k", str(int(top_k)),
                    ]
                    with st.spinner("Comparaison mémoire CIR en cours..."):
                        result = run_command(cmd, cwd=root)
                    write_json(current_out_dir / "cir_memory_compare_run.json", result)
                    if result.get("ok"):
                        st.success("Comparaison mémoire CIR générée.")
                    else:
                        st.error("Erreur comparaison mémoire CIR.")
                    render_command_result(result, "Détail commande comparaison")

    st.divider()
    report = read_json(comparison_path, {})
    if report:
        render_summary(report.get("summary") or {})
        render_verrou_comparisons(report)
    else:
        st.warning("Aucun rapport de comparaison mémoire CIR disponible.")


with tab_report:
    st.subheader("3. Rapport mémoire CIR")

    report = read_json(comparison_path, {})
    if not report:
        st.warning("Aucun rapport disponible.")
    else:
        render_summary(report.get("summary") or {})
        st.divider()
        render_verrou_comparisons(report)

        st.divider()
        with open(comparison_path, "rb") as f:
            st.download_button(
                "Télécharger cir_memory_comparison_report.json",
                data=f,
                file_name="cir_memory_comparison_report.json",
                mime="application/json",
            )

        with st.expander("JSON complet", expanded=False):
            st.json(report)


with tab_status:
    st.subheader("4. État des fichiers")

    files = {
        "CIR N-1 uploadé": previous_upload_dir,
        "NLP CIR N-1": previous_nlp_path,
        "Mémoire CIR N-1 JSON": cir_memory_path,
        "NLP année courante N": current_nlp_path,
        "Rapport comparaison mémoire": comparison_path,
        "Run pipeline CIR": previous_out_dir / "cir_memory_pipeline_run.json",
        "Run register CIR": previous_out_dir / "cir_memory_register_run.json",
        "Run compare": current_out_dir / "cir_memory_compare_run.json",
    }

    for name, path in files.items():
        if path.is_dir():
            exists = path.exists() and any(path.iterdir())
        else:
            exists = path.exists()
        st.write("✅" if exists else "❌", name)
        st.code(str(path))

    st.divider()
    st.markdown("### Commandes terminal équivalentes")

    commands = (
        f'python safe_pipeline_worker.py --mode pipeline --organisme {organisme} --project {project} '
        f'--year {previous_year} --out-dir "{previous_out_dir}" --folder "{previous_upload_dir}" --include-cir-final\n\n'
        f'python run_register_final_cir_from_nlp.py --organisme {organisme} --project {project} '
        f'--year {previous_year} --cir-final "CHEMIN_DU_CIR_N-1.docx" --nlp-result "{previous_nlp_path}"\n\n'
        f'python run_compare_cir_memory_from_nlp.py --organisme {organisme} --project {project} --year {current_year}'
    )
    st.code(commands)
