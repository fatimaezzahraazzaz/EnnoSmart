# -*- coding: utf-8 -*-
from __future__ import annotations

"""
pages/02_EnnoScholar.py

Interface Streamlit EnnoScholar :
1. Charger les résultats EnnoDiagnostic / NLP
2. Sélectionner les verrous confirmés par le consultant
3. Lancer EnnoScholar : recherche articles
4. Sélectionner les articles pertinents
5. Générer l'état de l'art depuis la sélection consultant

À lancer depuis C:\EnnoSmart :
    streamlit run app_streamlit.py

ou directement :
    streamlit run pages\02_EnnoScholar.py
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st


# ──────────────────────────────────────────────────────────────────────────────
# Chemin projet
# ──────────────────────────────────────────────────────────────────────────────

def _find_project_root() -> Path:
    candidates = [Path.cwd()]

    try:
        candidates.extend(Path(__file__).resolve().parents)
    except Exception:
        pass

    for c in candidates:
        if (c / "agents" / "EnnoScholar").exists():
            return c

    return Path.cwd()


PROJECT_ROOT = _find_project_root()

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ──────────────────────────────────────────────────────────────────────────────
# Imports EnnoScholar
# ──────────────────────────────────────────────────────────────────────────────

try:
    from agents.EnnoScholar.scholar_agent import (
        EnnoScholarAgent,
        run_state_of_art_writer_from_selection,
    )
except Exception:
    # Si ta version actuelle n'a pas encore run_state_of_art_writer_from_selection
    from agents.EnnoScholar.scholar_agent import EnnoScholarAgent

    run_state_of_art_writer_from_selection = None


try:
    from agents.EnnoScholar.utils import clean_text, write_json, read_json
except Exception:
    def clean_text(text: Any, max_chars: int = 2000) -> str:
        s = " ".join(str(text or "").split()).strip()
        return s[:max_chars]

    def read_json(path: str | Path, default: Any = None) -> Any:
        p = Path(path)
        if not p.exists():
            return default
        return json.loads(p.read_text(encoding="utf-8"))

    def write_json(path: str | Path, data: Any) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return p


# ──────────────────────────────────────────────────────────────────────────────
# Configuration page
# ──────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="EnnoScholar",
    page_icon="📚",
    layout="wide",
)

st.title("📚 EnnoScholar — Agent 2")
st.caption(
    "Sélection des verrous → recherche scientifique → sélection des articles → rédaction de l’état de l’art."
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers JSON / extraction verrous
# ──────────────────────────────────────────────────────────────────────────────

VERROU_KEYS = [
    "verrous_rnd_locaux",
    "verrous_locaux",
    "verrous_confirmes",
    "validated_verrous",
    "technical_locks",
    "verrous",
    "locks",
]

CONTEXT_KEYS = {
    "objectifs": ["objectifs_locaux", "objectifs", "objectifs_confirmes"],
    "methodes": ["methodes_locales", "methodes", "demarches"],
    "resultats": ["resultats_locaux", "resultats"],
    "parametres": ["parametres_locaux", "parametres"],
    "limites": ["limites_locales", "limites"],
}


def safe_json_load(path: str | Path, default: Any = None) -> Any:
    try:
        return read_json(path, default)
    except Exception as exc:
        st.error(f"Erreur lecture JSON : {exc}")
        return default


def safe_json_write(path: str | Path, data: Any) -> Path:
    return write_json(path, data)


def short(text: Any, n: int = 280) -> str:
    return clean_text(text, n)


def _first(obj: Dict[str, Any], keys: List[str], default: Any = "") -> Any:
    if not isinstance(obj, dict):
        return default

    for k in keys:
        v = obj.get(k)
        if v not in [None, "", [], {}]:
            return v

    return default


def _as_list(x: Any) -> List[Any]:
    if isinstance(x, list):
        return x
    if x in [None, "", {}]:
        return []
    return [x]


def _get_pack(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Supporte plusieurs structures possibles :
    - frascati_guard.qualified_pack_for_ennodiagnostic
    - multi_document_evidence_pack_for_ennodiagnostic
    - merged_evidence_pack_for_ennodiagnostic
    - evidence_pack_for_ennodiagnostic
    - JSON déjà aplati : verrous_locaux, objectifs_locaux...
    """
    if not isinstance(data, dict):
        return {}

    fg = data.get("frascati_guard") or {}

    if isinstance(fg, dict):
        q = fg.get("qualified_pack_for_ennodiagnostic")
        if isinstance(q, dict):
            return q

    for key in [
        "multi_document_evidence_pack_for_ennodiagnostic",
        "merged_evidence_pack_for_ennodiagnostic",
        "evidence_pack_for_ennodiagnostic",
        "qualified_pack_for_ennodiagnostic",
        "nlp_extracted_view",
        "extracted_view",
    ]:
        val = data.get(key)
        if isinstance(val, dict):
            return val

    # fallback : si les clés sont déjà à la racine
    if any(k in data for k in VERROU_KEYS + ["objectifs_locaux", "methodes_locales"]):
        return data

    return {}


def _domain_detection(data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return {}

    if isinstance(data.get("domain_detection"), dict):
        return data["domain_detection"]

    for key in ["raw_result", "pre_cir_structured_result", "cir_structured_result"]:
        obj = data.get(key)
        if isinstance(obj, dict) and isinstance(obj.get("domain_detection"), dict):
            return obj["domain_detection"]

    return {}


def _item_text(item: Dict[str, Any]) -> str:
    return clean_text(
        _first(
            item,
            [
                "text",
                "source_text",
                "excerpt",
                "description",
                "content",
                "verrou_text",
                "label",
                "title",
            ],
        ),
        1400,
    )


def _item_title(item: Dict[str, Any], idx: int) -> str:
    title = clean_text(
        _first(
            item,
            [
                "title",
                "theme_label",
                "verrou_title",
                "section_title",
                "label",
                "name",
            ],
        ),
        180,
    )

    if title and len(title) >= 8:
        return title

    text = _item_text(item)
    if text:
        return clean_text(text, 130)

    return f"Verrou {idx}"


def _item_score(item: Dict[str, Any]) -> float:
    for k in [
        "frascati_score",
        "score",
        "confidence",
        "verrou_score",
        "rank_score",
    ]:
        try:
            if item.get(k) is not None:
                return float(item.get(k) or 0)
        except Exception:
            pass

    fr = item.get("frascati") or {}
    if isinstance(fr, dict):
        for k in ["frascati_score", "score"]:
            try:
                if fr.get(k) is not None:
                    return float(fr.get(k) or 0)
            except Exception:
                pass

    return 0.0


def _is_rejected_verrou(item: Dict[str, Any]) -> bool:
    if not isinstance(item, dict):
        return True

    if item.get("rejected_as_verrou"):
        return True

    status = clean_text(item.get("quality_status")).lower()
    final_role = clean_text(item.get("final_role")).lower()
    decision = clean_text(item.get("frascati_decision")).lower()

    if "rejected" in status or "faux_verrou" in decision:
        return True

    if final_role in {"methode", "resultat", "parametre", "indice_non_verrou"}:
        return True

    return False


def _collect_context_for_item(item: Dict[str, Any], pack: Dict[str, Any]) -> Dict[str, List[str]]:
    """
    Contexte simple : objectifs/méthodes/résultats/paramètres.
    On garde les premiers éléments propres.
    """
    ctx: Dict[str, List[str]] = {}

    item_doc = clean_text(item.get("document") or item.get("source_document") or item.get("file_name"))

    for out_key, possible_keys in CONTEXT_KEYS.items():
        values = []

        for pk in possible_keys:
            arr = pack.get(pk)
            if not isinstance(arr, list):
                continue

            # Priorité même document
            same_doc = []
            others = []

            for it in arr:
                if not isinstance(it, dict):
                    continue

                txt = _item_text(it)
                if not txt:
                    continue

                doc = clean_text(it.get("document") or it.get("source_document") or it.get("file_name"))

                if item_doc and doc and item_doc == doc:
                    same_doc.append(txt)
                else:
                    others.append(txt)

            values.extend(same_doc[:3])
            values.extend(others[:2])

        # dédoublonnage
        seen = set()
        clean_values = []

        for v in values:
            key = v.lower()[:160]
            if key not in seen:
                seen.add(key)
                clean_values.append(v)

        ctx[out_key] = clean_values[:3]

    return ctx


def _sources_for_item(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    sources = []

    main_excerpt = _item_text(item)

    if main_excerpt:
        sources.append({
            "document": clean_text(item.get("document") or item.get("source_document") or item.get("file_name")),
            "section": clean_text(item.get("section_title") or item.get("section")),
            "source_path": clean_text(item.get("source_path")),
            "excerpt": main_excerpt,
        })

    supporting = item.get("supporting_passages") or item.get("sources") or []

    if isinstance(supporting, list):
        for sp in supporting[:5]:
            if not isinstance(sp, dict):
                continue

            txt = clean_text(sp.get("text") or sp.get("source_text") or sp.get("excerpt"), 600)

            if not txt:
                continue

            sources.append({
                "document": clean_text(sp.get("document") or item.get("document")),
                "section": clean_text(sp.get("section_title") or item.get("section_title")),
                "source_path": clean_text(sp.get("source_path") or item.get("source_path")),
                "excerpt": txt,
            })

    return sources[:6]


def item_to_scholar_verrou(item: Dict[str, Any], idx: int, pack: Dict[str, Any]) -> Dict[str, Any]:
    title = _item_title(item, idx)
    text = _item_text(item)

    return {
        "verrou_id": str(
            item.get("verrou_id")
            or item.get("theme_id")
            or item.get("id")
            or f"manual_verrou_{idx}"
        ),
        "title": title,
        "text": text or title,
        "frascati": item.get("frascati") or {
            "score": item.get("frascati_score") or item.get("score"),
            "decision": item.get("frascati_decision") or item.get("quality_status"),
        },
        "nlp_scores": {
            "confidence": item.get("confidence"),
            "verrou_score": item.get("verrou_score"),
            "rank_score": item.get("rank_score"),
            "quality_status": item.get("quality_status"),
            "final_role": item.get("final_role"),
            "selected_by_consultant": True,
        },
        "context": _collect_context_for_item(item, pack),
        "sources": _sources_for_item(item),
        "raw_item": item,
        "suggested_queries": item.get("suggested_queries") or [],
    }


def collect_verrou_candidates(data: Dict[str, Any]) -> Dict[str, Any]:
    pack = _get_pack(data)
    domain = _domain_detection(data)

    candidates = []

    for key in VERROU_KEYS:
        arr = pack.get(key)

        if not isinstance(arr, list):
            continue

        for item in arr:
            if not isinstance(item, dict):
                continue

            if _is_rejected_verrou(item):
                continue

            txt = _item_text(item)
            title = _item_title(item, len(candidates) + 1)

            if not txt and not title:
                continue

            candidates.append({
                "_source_key": key,
                "_title": title,
                "_text": txt,
                "_score": _item_score(item),
                "_document": clean_text(
                    item.get("document") or item.get("source_document") or item.get("file_name"),
                    180,
                ),
                "_raw": item,
            })

    # Déduplication simple par titre + début texte
    out = []
    seen = set()

    for c in candidates:
        k = (c["_title"].lower()[:100], c["_text"].lower()[:160])

        if k in seen:
            continue

        seen.add(k)
        out.append(c)

    return {
        "domain_detection": domain,
        "pack": pack,
        "candidates": out,
    }


def build_payload_from_selected_verrous(
    selected_candidates: List[Dict[str, Any]],
    pack: Dict[str, Any],
    domain_detection: Dict[str, Any],
    organisme: str,
    project: str,
    year: str,
    nlp_result_path: str,
    diagnostic_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    verrous = []

    for i, c in enumerate(selected_candidates, 1):
        raw = c.get("_raw") or {}
        verrous.append(item_to_scholar_verrou(raw, i, pack))

    return {
        "organisme": organisme,
        "project": project,
        "year": year,
        "input_nlp_result": nlp_result_path,
        "domain_detection": domain_detection or {},
        "diagnostic_context": diagnostic_context or {},
        "selector": {
            "version": "streamlit_consultant_selection",
            "selection_rule": "consultant_checked_verrous",
            "verrous_selected": len(verrous),
        },
        "verrous": verrous,
    }


def build_selection_payload_from_ui(
    report: Dict[str, Any],
    selected_map: Dict[str, bool],
    notes_map: Dict[str, str],
) -> Dict[str, Any]:
    verrous = []

    for vi, r in enumerate(report.get("results") or []):
        if not isinstance(r, dict):
            continue

        selected_articles = []

        for ai, art in enumerate(r.get("articles") or []):
            article_key = f"article_{vi}_{ai}"

            if selected_map.get(article_key):
                a = dict(art)
                a["consultant_selected"] = True
                a["consultant_note"] = notes_map.get(article_key, "")
                selected_articles.append(a)

        verrous.append({
            "verrou_id": r.get("verrou_id"),
            "verrou_title": r.get("verrou_title"),
            "verrou_text": r.get("verrou_text"),
            "scientific_intent": r.get("scientific_intent") or {},
            "decision": r.get("decision"),
            "scientific_support_score": r.get("scientific_support_score"),
            "gap_analysis": r.get("gap_analysis"),
            "selected_articles": selected_articles,
        })

    return {
        "agent": "EnnoScholar",
        "payload_type": "selected_articles_for_state_of_art",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "organisme": report.get("organisme"),
        "project": report.get("project"),
        "year": report.get("year"),
        "domain_detection": report.get("domain_detection") or {},
        "diagnostic_context": report.get("diagnostic_context") or {},
        "verrous": verrous,
    }


def run_writer_compatible(
    selection_path: Path,
    out_dir: Path,
    writer_mode: str,
    llm_model: str,
    llm_temperature: float,
) -> Dict[str, Any]:
    """
    Compatible avec ton scholar_agent V3.
    Si la fonction publique existe, on l'utilise.
    Sinon on passe par EnnoScholarAgent.run_writer_from_selection.
    """
    if run_state_of_art_writer_from_selection is not None:
        return run_state_of_art_writer_from_selection(
            selection_payload_path=selection_path,
            out_dir=out_dir,
            writer_mode=writer_mode,
            llm_model=llm_model,
            llm_temperature=llm_temperature,
        )

    selection_payload = safe_json_load(selection_path, {})

    agent = EnnoScholarAgent(
        use_semantic_scholar=False,
        use_openalex=False,
        use_arxiv=False,
        offline_dry_run=True,
    )

    if not hasattr(agent, "run_writer_from_selection"):
        raise RuntimeError(
            "Ton scholar_agent.py ne contient pas run_writer_from_selection. "
            "Remplace scholar_agent.py par la version V3 que je t’ai donnée."
        )

    report = agent.run_writer_from_selection(
        selection_payload=selection_payload,
        writer_mode=writer_mode,
        llm_model=llm_model,
        llm_temperature=llm_temperature,
    )

    report["input_selection_payload"] = str(selection_path)
    report["outputs"] = {
        "state_of_art_report": str(out_dir / "ennoscholar_state_of_art_report.json")
    }

    safe_json_write(out_dir / "ennoscholar_state_of_art_report.json", report)

    return report


# ──────────────────────────────────────────────────────────────────────────────
# Sidebar
# ──────────────────────────────────────────────────────────────────────────────

st.sidebar.header("⚙️ Configuration")

default_base = "outputs\\safe_rag_upload\\Girodin\\TGM100\\2023"

organisme = st.sidebar.text_input("Organisme", value="Girodin")
project = st.sidebar.text_input("Projet", value="TGM100")
year = st.sidebar.text_input("Année", value="2023")

nlp_result_path = st.sidebar.text_input(
    "Chemin résultat NLP / Diagnostic",
    value=f"{default_base}\\nlp_extracted_view.json",
)

diagnostic_report_path = st.sidebar.text_input(
    "Diagnostic report optionnel",
    value="",
)

out_dir = st.sidebar.text_input(
    "Dossier sortie EnnoScholar",
    value=f"{default_base}\\ennoscholar",
)

limit_per_query = st.sidebar.slider("Articles par requête", 1, 10, 4)
max_verrous_ui = st.sidebar.slider("Max verrous à envoyer", 1, 10, 5)

offline_dry_run = st.sidebar.checkbox("Mode test sans recherche web", value=False)

use_semantic = st.sidebar.checkbox("Semantic Scholar", value=True)
use_openalex = st.sidebar.checkbox("OpenAlex", value=True)
use_arxiv = st.sidebar.checkbox("ArXiv", value=True)

st.sidebar.divider()

writer_mode = st.sidebar.selectbox(
    "Mode rédaction",
    ["template", "auto", "llm"],
    index=0,
    help="template = sans LLM ; auto/llm = utilise OpenRouter si clé disponible.",
)

llm_model = st.sidebar.text_input(
    "Modèle LLM",
    value=os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
)

llm_temperature = st.sidebar.slider("Température LLM", 0.0, 1.0, 0.15, 0.05)

openrouter_key = st.sidebar.text_input(
    "OPENROUTER_API_KEY",
    value=os.getenv("OPENROUTER_API_KEY", ""),
    type="password",
)

if openrouter_key:
    os.environ["OPENROUTER_API_KEY"] = openrouter_key


# ──────────────────────────────────────────────────────────────────────────────
# Session state
# ──────────────────────────────────────────────────────────────────────────────

if "verrou_candidates" not in st.session_state:
    st.session_state.verrou_candidates = []

if "pack" not in st.session_state:
    st.session_state.pack = {}

if "domain_detection" not in st.session_state:
    st.session_state.domain_detection = {}

if "scholar_report" not in st.session_state:
    st.session_state.scholar_report = None

if "selection_payload" not in st.session_state:
    st.session_state.selection_payload = None

if "state_of_art_report" not in st.session_state:
    st.session_state.state_of_art_report = None


# ──────────────────────────────────────────────────────────────────────────────
# Onglets
# ──────────────────────────────────────────────────────────────────────────────

tab1, tab2, tab3, tab4 = st.tabs([
    "1️⃣ Verrous consultant",
    "2️⃣ Recherche articles",
    "3️⃣ Sélection articles",
    "4️⃣ Rédaction état de l’art",
])


# ──────────────────────────────────────────────────────────────────────────────
# TAB 1 — Charger / sélectionner verrous
# ──────────────────────────────────────────────────────────────────────────────

with tab1:
    st.subheader("1️⃣ Charger le diagnostic et sélectionner les verrous")

    nlp_path = Path(nlp_result_path)

    col_a, col_b, col_c = st.columns([1, 1, 1])

    with col_a:
        st.write("Fichier NLP :")
        st.code(str(nlp_path), language="text")

    with col_b:
        st.write("Existe :")
        st.write("✅ Oui" if nlp_path.exists() else "❌ Non")

    with col_c:
        if st.button("🔄 Charger / rafraîchir les verrous", use_container_width=True):
            data = safe_json_load(nlp_path, {})

            extracted = collect_verrou_candidates(data)

            st.session_state.verrou_candidates = extracted["candidates"]
            st.session_state.pack = extracted["pack"]
            st.session_state.domain_detection = extracted["domain_detection"]

            st.success(f"{len(st.session_state.verrou_candidates)} verrou(s) candidat(s) chargé(s).")

    candidates = st.session_state.verrou_candidates

    if not candidates:
        st.warning(
            "Aucun verrou affiché pour le moment. Clique sur “Charger / rafraîchir les verrous”. "
            "Si ça reste vide, le JSON donné ne contient probablement pas les clés `verrous_locaux` "
            "ou `verrous_rnd_locaux`."
        )
    else:
        st.info(
            "Coche uniquement les verrous que le consultant veut envoyer à EnnoScholar. "
            "C’est cette sélection qui remplace le passage automatique qui t’a donné 0 verrou."
        )

        selected_candidates = []

        for i, c in enumerate(candidates[:50], 1):
            raw = c.get("_raw") or {}
            title = c.get("_title") or f"Verrou {i}"
            text = c.get("_text") or ""

            st.markdown("---")

            left, right = st.columns([0.08, 0.92])

            with left:
                checked = st.checkbox(
                    "OK",
                    value=i <= max_verrous_ui,
                    key=f"verrou_select_{i}",
                    label_visibility="collapsed",
                )

            with right:
                st.markdown(f"#### V{i} — {title}")
                st.caption(
                    f"Source: {c.get('_source_key')} | "
                    f"Score: {round(float(c.get('_score') or 0), 3)} | "
                    f"Document: {c.get('_document') or '—'}"
                )

                st.write(short(text, 700))

                with st.container():
                    cols = st.columns(4)
                    cols[0].caption(f"final_role: {raw.get('final_role') or '—'}")
                    cols[1].caption(f"quality: {raw.get('quality_status') or '—'}")
                    cols[2].caption(f"confidence: {raw.get('confidence') or '—'}")
                    cols[3].caption(f"verrou_score: {raw.get('verrou_score') or '—'}")

            if checked:
                selected_candidates.append(c)

        st.session_state.current_selected_candidates = selected_candidates

        st.success(f"{len(selected_candidates)} verrou(s) sélectionné(s).")


# ──────────────────────────────────────────────────────────────────────────────
# TAB 2 — Lancer EnnoScholar recherche
# ──────────────────────────────────────────────────────────────────────────────

with tab2:
    st.subheader("2️⃣ Lancer EnnoScholar — recherche scientifique")

    selected_candidates = st.session_state.get("current_selected_candidates", [])

    st.write(f"Verrous prêts à envoyer : **{len(selected_candidates)}**")

    if not selected_candidates:
        st.warning("Sélectionne d’abord au moins un verrou dans l’onglet 1.")
    else:
        if st.button("🚀 Lancer la recherche EnnoScholar", type="primary", use_container_width=True):
            out = Path(out_dir)
            out.mkdir(parents=True, exist_ok=True)

            diagnostic_context = {}

            if diagnostic_report_path:
                diag_path = Path(diagnostic_report_path)
                if diag_path.exists():
                    try:
                        from agents.EnnoScholar.scholar_agent import extract_diagnostic_context

                        diagnostic_context = extract_diagnostic_context(
                            safe_json_load(diag_path, {})
                        )
                    except Exception:
                        diagnostic_context = safe_json_load(diag_path, {})

            payload = build_payload_from_selected_verrous(
                selected_candidates=selected_candidates,
                pack=st.session_state.pack,
                domain_detection=st.session_state.domain_detection,
                organisme=organisme,
                project=project,
                year=year,
                nlp_result_path=str(nlp_path),
                diagnostic_context=diagnostic_context,
            )

            payload_path = out / "validated_verrous_for_scholar.json"
            report_path = out / "ennoscholar_report.json"

            safe_json_write(payload_path, payload)

            with st.spinner("Recherche articles en cours..."):
                agent = EnnoScholarAgent(
                    use_semantic_scholar=use_semantic,
                    use_openalex=use_openalex,
                    use_arxiv=use_arxiv,
                    limit_per_query=limit_per_query,
                    offline_dry_run=offline_dry_run,
                )

                if hasattr(agent, "run_search"):
                    report = agent.run_search(payload)
                else:
                    report = agent.run(payload)

                report["outputs"] = {
                    "payload": str(payload_path),
                    "report": str(report_path),
                }

                safe_json_write(report_path, report)

                st.session_state.scholar_report = report

            st.success("Recherche EnnoScholar terminée.")
            st.code(str(report_path), language="text")

    report = st.session_state.scholar_report

    if report:
        st.markdown("### Résultat recherche")

        c1, c2, c3 = st.columns(3)
        c1.metric("Verrous analysés", report.get("verrous_analyzed", len(report.get("results") or [])))
        c2.metric("Décisions", len(report.get("decision_counts") or {}))
        c3.metric("Résultats", len(report.get("results") or []))

        for i, r in enumerate(report.get("results") or [], 1):
            st.markdown("---")
            st.markdown(f"### V{i} — {r.get('verrou_title')}")
            st.caption(
                f"Décision : {r.get('decision')} | "
                f"Score support : {r.get('scientific_support_score')} | "
                f"Articles : {r.get('articles_found')}"
            )

            intent = r.get("scientific_intent") or {}

            with st.container():
                st.write("**Problème scientifique :**", intent.get("scientific_problem") or "—")
                st.write("**Objet technique :**", intent.get("technical_object") or "—")
                st.write("**Phénomène :**", intent.get("phenomenon") or "—")

            if r.get("gap_analysis"):
                st.info(r["gap_analysis"])

            articles = r.get("articles") or []
            if articles:
                preview = []
                for a in articles[:8]:
                    preview.append({
                        "tag": a.get("tag"),
                        "score": a.get("relevance_score"),
                        "year": a.get("year"),
                        "title": a.get("title"),
                        "source": a.get("source"),
                    })

                st.dataframe(preview, use_container_width=True, hide_index=True)
            else:
                st.warning("Aucun article trouvé pour ce verrou.")


# ──────────────────────────────────────────────────────────────────────────────
# TAB 3 — Sélection articles
# ──────────────────────────────────────────────────────────────────────────────

with tab3:
    st.subheader("3️⃣ Sélectionner les articles pour l’état de l’art")

    report = st.session_state.scholar_report

    # Permet aussi de recharger un report existant
    existing_report_path = Path(out_dir) / "ennoscholar_report.json"

    if report is None and existing_report_path.exists():
        if st.button("📂 Charger le dernier ennoscholar_report.json"):
            st.session_state.scholar_report = safe_json_load(existing_report_path, {})
            report = st.session_state.scholar_report

    if not report:
        st.warning("Lance d’abord la recherche dans l’onglet 2.")
    else:
        selected_map: Dict[str, bool] = {}
        notes_map: Dict[str, str] = {}

        st.info(
            "Coche les articles vraiment utiles. Par défaut, les articles Direct et Connexe sont pré-cochés."
        )

        for vi, r in enumerate(report.get("results") or []):
            st.markdown("---")
            st.markdown(f"## V{vi + 1} — {r.get('verrou_title')}")

            articles = r.get("articles") or []

            if not articles:
                st.warning("Aucun article pour ce verrou.")
                continue

            for ai, art in enumerate(articles):
                article_key = f"article_{vi}_{ai}"

                tag = art.get("tag")
                score = art.get("relevance_score")
                title = art.get("title") or "Sans titre"
                year_a = art.get("year") or "s.d."
                source = art.get("source") or ""
                doi = art.get("doi") or ""
                url = art.get("url") or ""

                default_checked = tag in {"Direct", "Connexe"}

                col_check, col_body = st.columns([0.06, 0.94])

                with col_check:
                    checked = st.checkbox(
                        "select",
                        value=default_checked,
                        key=f"select_{article_key}",
                        label_visibility="collapsed",
                    )

                with col_body:
                    st.markdown(f"### {tag or '—'} | score {score} — {title}")
                    st.caption(f"{year_a} | {source} | DOI: {doi or '—'}")
                    if url:
                        st.caption(url)

                    reason = art.get("reason") or ""
                    if reason:
                        st.write("**Pourquoi cet article est proposé :**", reason)

                    abstract = art.get("abstract") or art.get("tldr") or ""
                    if abstract:
                        st.write(short(abstract, 900))

                    note = st.text_input(
                        "Note consultant optionnelle",
                        value="",
                        key=f"note_{article_key}",
                    )

                selected_map[article_key] = checked
                notes_map[article_key] = note

        if st.button("💾 Sauvegarder la sélection articles", type="primary", use_container_width=True):
            selection_payload = build_selection_payload_from_ui(
                report=report,
                selected_map=selected_map,
                notes_map=notes_map,
            )

            out = Path(out_dir)
            out.mkdir(parents=True, exist_ok=True)

            selection_path = out / "selected_articles_for_state_of_art.json"

            safe_json_write(selection_path, selection_payload)

            st.session_state.selection_payload = selection_payload

            st.success("Sélection articles sauvegardée.")
            st.code(str(selection_path), language="text")

            total_selected = sum(
                len(v.get("selected_articles") or [])
                for v in selection_payload.get("verrous") or []
            )

            st.metric("Articles sélectionnés", total_selected)


# ──────────────────────────────────────────────────────────────────────────────
# TAB 4 — Rédaction état de l’art
# ──────────────────────────────────────────────────────────────────────────────

with tab4:
    st.subheader("4️⃣ Générer l’état de l’art")

    out = Path(out_dir)
    selection_path = out / "selected_articles_for_state_of_art.json"

    if not selection_path.exists():
        st.warning(
            "Aucune sélection articles trouvée. Va dans l’onglet 3 et sauvegarde la sélection."
        )
    else:
        st.write("Payload sélection :")
        st.code(str(selection_path), language="text")

        if writer_mode in {"auto", "llm"} and not os.getenv("OPENROUTER_API_KEY"):
            st.warning(
                "Tu as choisi auto/llm mais OPENROUTER_API_KEY est vide. "
                "Soit ajoute la clé dans la sidebar, soit utilise writer_mode=template."
            )

        if st.button("📝 Générer l’état de l’art", type="primary", use_container_width=True):
            with st.spinner("Rédaction de l’état de l’art en cours..."):
                try:
                    final_report = run_writer_compatible(
                        selection_path=selection_path,
                        out_dir=out,
                        writer_mode=writer_mode,
                        llm_model=llm_model,
                        llm_temperature=llm_temperature,
                    )

                    st.session_state.state_of_art_report = final_report
                    st.success("État de l’art généré.")

                    output_path = out / "ennoscholar_state_of_art_report.json"
                    st.code(str(output_path), language="text")

                except Exception as exc:
                    st.error(f"Erreur rédaction : {exc}")

    final_report = st.session_state.state_of_art_report

    # Recharge si déjà existant
    final_path = Path(out_dir) / "ennoscholar_state_of_art_report.json"

    if final_report is None and final_path.exists():
        if st.button("📂 Charger le dernier état de l’art généré"):
            st.session_state.state_of_art_report = safe_json_load(final_path, {})
            final_report = st.session_state.state_of_art_report

    if final_report:
        st.markdown("## Résultat final")

        c1, c2, c3 = st.columns(3)
        c1.metric("Verrous rédigés", final_report.get("verrous_written", len(final_report.get("results") or [])))
        c2.metric("Warnings", final_report.get("total_warnings", 0))
        c3.metric("Citation errors", final_report.get("citation_errors", 0))

        for i, r in enumerate(final_report.get("results") or [], 1):
            st.markdown("---")
            st.markdown(f"## État de l’art V{i} — {r.get('verrou_title')}")

            soa = r.get("state_of_art") or {}

            st.caption(
                f"Mode : {soa.get('mode')} | "
                f"Articles sélectionnés : {r.get('selected_articles_count')}"
            )

            warnings = soa.get("warnings") or []
            if warnings:
                st.warning("\n".join(str(w) for w in warnings))

            guard = soa.get("citation_guard") or {}
            if guard:
                if guard.get("ok"):
                    st.success("Citation guard : OK")
                else:
                    st.error(f"Citations inconnues : {guard.get('unknown_citations')}")

            draft = soa.get("draft") or ""

            if draft:
                st.markdown(draft)
            else:
                st.warning("Aucun texte généré.")

            refs = soa.get("references") or []

            if refs:
                st.markdown("### Références")
                st.dataframe(refs, use_container_width=True, hide_index=True)