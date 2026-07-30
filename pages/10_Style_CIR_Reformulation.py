# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st


st.set_page_config(page_title="EnnoSmart - Style CIR", layout="wide")

st.title("Mémoire rédactionnelle CIR")
st.caption(
    "Le LLM s'inspire du style des CIR validés pour reformuler EnnoDiagnostic, "
    "sans copier et sans inventer."
)


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


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


def slug(x: str) -> str:
    import re
    x = str(x or "").strip().lower()
    x = re.sub(r"[^\w\-]+", "_", x)
    x = re.sub(r"_+", "_", x).strip("_")
    return x or "unknown"


def storage_year_dir(organisme: str, project: str, year: str) -> Path:
    return Path("storage") / "organismes" / slug(organisme) / "projects" / slug(project) / "years" / str(year)


def style_memory_path(organisme: str) -> Path:
    return Path("storage") / "organismes" / slug(organisme) / "cir_style_memory" / "style_memory.json"


def reformulation_path(organisme: str, project: str, year: str) -> Path:
    return storage_year_dir(organisme, project, year) / "diagnostics" / "reformulation_rnd_style_cir.json"


def render_cmd(result: Dict[str, Any], title: str):
    if not result:
        return
    with st.expander(title, expanded=not result.get("ok")):
        st.code(result.get("cmd", ""))
        st.write("Code retour :", result.get("returncode"))
        if result.get("stdout"):
            st.markdown("**Sortie**")
            st.code(result.get("stdout"))
        if result.get("stderr"):
            st.markdown("**Erreurs**")
            st.code(result.get("stderr"))


with st.sidebar:
    st.header("Projet")
    organisme = st.text_input("Organisme", "Girodin")
    project = st.text_input("Projet courant", "TGM100")
    current_year = st.text_input("Année courante", "2023")
    style_project = st.text_input("Projet CIR style", "TGM100")
    style_year = st.text_input("Année CIR style", "2022")

    root = Path(".").resolve()
    current_nlp = Path("outputs") / "safe_rag_upload" / organisme / project / current_year / "nlp_result.json"
    style_nlp = Path("outputs") / "safe_rag_upload" / organisme / style_project / style_year / "nlp_result.json"

    st.markdown("### Chemins")
    st.write("NLP courant")
    st.code(str(current_nlp))
    st.write("NLP CIR style")
    st.code(str(style_nlp))
    st.write("Mémoire style")
    st.code(str(style_memory_path(organisme)))


tab1, tab2, tab3, tab4 = st.tabs([
    "1. Enregistrer style CIR",
    "2. Générer reformulation",
    "3. Résultat",
    "4. Mémoire",
])


with tab1:
    st.subheader("1. Enregistrer le style d'un CIR validé")
    st.info(
        "Cette étape lit le nlp_result du CIR final validé et ajoute ses sections "
        "comme exemples de style. Frascati n'est pas utilisé."
    )

    c1, c2 = st.columns(2)
    with c1:
        st.write("NLP CIR style :", "✅" if style_nlp.exists() else "❌")
        st.code(str(style_nlp))
    with c2:
        max_ex = st.number_input("Max exemples par rôle", min_value=1, max_value=20, value=8)

    if st.button("Enregistrer ce CIR comme style", type="primary"):
        if not style_nlp.exists():
            st.error("NLP du CIR style introuvable. Lance d'abord le pipeline du CIR final.")
        else:
            script = root / "run_register_cir_style_memory.py"
            cmd = [
                sys.executable,
                str(script),
                "--organisme", organisme,
                "--project", style_project,
                "--year", style_year,
                "--nlp-result", str(style_nlp),
                "--max-examples-per-role", str(int(max_ex)),
            ]
            with st.spinner("Enregistrement du style CIR..."):
                result = run_command(cmd, cwd=root)
            if result.get("ok"):
                st.success("Style CIR enregistré.")
            else:
                st.error("Erreur enregistrement style CIR.")
            render_cmd(result, "Détail commande")


with tab2:
    st.subheader("2. Générer une reformulation R&D inspirée du style CIR")
    st.info(
        "Le LLM utilise les sources courantes comme faits, et la mémoire CIR uniquement comme style rédactionnel."
    )

    st.write("NLP courant :", "✅" if current_nlp.exists() else "❌")
    st.code(str(current_nlp))
    st.write("Mémoire style :", "✅" if style_memory_path(organisme).exists() else "❌")
    st.code(str(style_memory_path(organisme)))

    no_llm = st.checkbox("Tester sans LLM", value=False)

    if st.button("Générer reformulation R&D style CIR", type="primary"):
        if not current_nlp.exists():
            st.error("NLP courant introuvable.")
        elif not style_memory_path(organisme).exists():
            st.error("Mémoire de style introuvable. Enregistre d'abord un CIR style.")
        else:
            script = root / "run_reformulate_with_cir_style.py"
            cmd = [
                sys.executable,
                str(script),
                "--organisme", organisme,
                "--project", project,
                "--year", current_year,
                "--nlp-result", str(current_nlp),
            ]
            if no_llm:
                cmd.append("--no-llm")
            with st.spinner("Reformulation R&D style CIR en cours..."):
                result = run_command(cmd, cwd=root)
            if result.get("ok"):
                st.success("Reformulation générée.")
            else:
                st.error("Erreur génération reformulation.")
            render_cmd(result, "Détail commande")


with tab3:
    st.subheader("3. Résultat reformulé")
    report = read_json(reformulation_path(organisme, project, current_year), {})
    if not report:
        st.warning("Aucune reformulation générée.")
    else:
        stats = report.get("style_memory_stats") or {}
        c1, c2, c3 = st.columns(3)
        c1.metric("Exemples style", stats.get("examples_count", 0))
        c2.metric("Pack courant", report.get("current_pack_source", "—"))
        c3.metric("Version", report.get("version", "—"))

        st.markdown("### Reformulation complète")
        st.markdown(report.get("full_markdown") or "")

        with st.expander("Détail JSON", expanded=False):
            st.json(report)

        st.download_button(
            "Télécharger reformulation_rnd_style_cir.json",
            data=json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"),
            file_name="reformulation_rnd_style_cir.json",
            mime="application/json",
        )


with tab4:
    st.subheader("4. Mémoire de style CIR")
    mem = read_json(style_memory_path(organisme), {})
    if not mem:
        st.warning("Aucune mémoire style.")
    else:
        stats = mem.get("stats") or {}
        c1, c2, c3 = st.columns(3)
        c1.metric("Exemples", stats.get("examples_count", 0))
        c2.metric("Rôles", len(stats.get("roles") or {}))
        c3.metric("Années", len(stats.get("years") or {}))

        st.write("Rôles :", stats.get("roles"))
        st.write("Projets :", stats.get("projects"))
        st.write("Années :", stats.get("years"))

        examples = mem.get("examples") or []
        table = []
        for ex in examples:
            table.append({
                "role": ex.get("role"),
                "project": ex.get("project"),
                "year": ex.get("year"),
                "section": ex.get("section_title"),
                "aperçu": (ex.get("text") or "")[:180],
            })
        st.dataframe(table, use_container_width=True, hide_index=True)

        with st.expander("JSON mémoire", expanded=False):
            st.json(mem)
