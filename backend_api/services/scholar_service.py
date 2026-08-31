# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import os
import json
import re

from sqlalchemy.orm import Session

from db.models import Article, DiagnosticRun, Project, ScholarRun, Verrou
from services.diagnostic_service import get_project_store, sanitize_json_value, ensure_ennosmart_imports


def _load_canonical_env() -> None:
    """Charge le .env racine avant tout import ou réglage EnnoScholar."""
    root_dir = Path(__file__).resolve().parents[2]
    env_paths = [root_dir / ".env", root_dir / "backend_api" / ".env"]
    try:
        from dotenv import load_dotenv

        for env_path in env_paths:
            load_dotenv(env_path, override=False)
        return
    except Exception:
        # Le backend garde un chargeur minimal si python-dotenv n'est pas installé.
        for env_path in env_paths:
            try:
                parsed: Dict[str, str] = {}
                for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    key = key.strip()
                    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                        continue
                    value = value.strip()
                    if (
                        len(value) >= 2
                        and value[0] == value[-1]
                        and value[0] in {"'", '"'}
                    ):
                        value = value[1:-1]
                    parsed[key] = value
                for key, value in parsed.items():
                    os.environ.setdefault(key, value)
            except Exception:
                continue


_load_canonical_env()


def read_json(path: str | Path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: str | Path, data: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def scholar_paths(project: Project) -> Dict[str, Path]:
    ps = get_project_store(project)
    scholar_dir = ps.project_dir / "ennoscholar"
    scholar_dir.mkdir(parents=True, exist_ok=True)

    return {
        "project_dir": ps.project_dir,
        "output_dir": ps.project_dir,
        "scholar_dir": scholar_dir,
        "report": scholar_dir / "ennoscholar_report.json",
        "payload": scholar_dir / "validated_verrous_for_scholar.json",
        "summary": scholar_dir / "ennoscholar_summary.json",
    }


def read_scholar_bundle(project: Project) -> Dict[str, Any]:
    paths = scholar_paths(project)

    report = read_json(paths["report"], {})
    payload = read_json(paths["payload"], {})
    summary = read_json(paths["summary"], {})

    return sanitize_json_value({
        "output_dir": str(paths["output_dir"]),
        "scholar_dir": str(paths["scholar_dir"]),
        "report": report,
        "payload": payload,
        "summary": summary,
        "files_found": {
            "report": paths["report"].exists(),
            "payload": paths["payload"].exists(),
            "summary": paths["summary"].exists(),
        },
    })


def latest_diagnostic_report_path(project: Project) -> Optional[Path]:
    ps = get_project_store(project)
    candidates = [
        ps.diagnostics_dir / "ennodiagnostic_report.json",
        ps.diagnostics_dir / "diagnostic_ennodiagnostic.json",
        ps.project_dir / "ennodiagnostic" / "ennodiagnostic_report.json",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def current_nlp_result_path(project: Project) -> Optional[Path]:
    ps = get_project_store(project)
    candidates = [
        ps.nlp_dir / "nlp_result.json",
        ps.project_dir / "nlp" / "nlp_result.json",
        ps.project_dir / "nlp_result.json",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def _extract_domain_detection(nlp: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(nlp, dict):
        return {}
    d = nlp.get("domain_detection")
    if isinstance(d, dict):
        return d
    for key in ["raw_result", "pre_cir_structured_result", "cir_structured_result"]:
        obj = nlp.get(key)
        if isinstance(obj, dict) and isinstance(obj.get("domain_detection"), dict):
            return obj["domain_detection"]
    return {}


def _flatten_text(value: Any, max_chars: int = 2500) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:max_chars]
    if isinstance(value, dict):
        parts = []
        for k, v in value.items():
            txt = _flatten_text(v, max_chars=max_chars)
            if txt:
                parts.append(f"{k}: {txt}")
        return "\n".join(parts)[:max_chars]
    if isinstance(value, list):
        return "\n".join(_flatten_text(x, max_chars=max_chars) for x in value)[:max_chars]
    return str(value)[:max_chars]


def extract_diagnostic_context_from_report(project: Project) -> Dict[str, Any]:
    path = latest_diagnostic_report_path(project)
    if not path:
        return {}

    data = read_json(path, {})
    content = ""

    if isinstance(data, dict):
        diag = data.get("diagnostic")
        if isinstance(diag, dict):
            content = diag.get("content") or ""
        if not content:
            content = data.get("content") or data.get("report_markdown") or ""

    return {
        "diagnostic_report_path": str(path),
        "diagnostic_context_text": str(content or "")[:4500],
    }


def get_selected_verrous_for_scholar(db: Session, project: Project) -> List[Verrou]:
    """
    Seuls les verrous validés par le consultant partent vers EnnoScholar.
    Convention frontend actuelle : consultant_status == garde.
    """
    return (
        db.query(Verrou)
        .join(DiagnosticRun, Verrou.diagnostic_run_id == DiagnosticRun.id)
        .filter(DiagnosticRun.project_id == project.id)
        .filter(Verrou.consultant_status == "garde")
        .order_by(Verrou.score.desc().nullslast(), Verrou.created_at.asc())
        .all()
    )


def get_all_current_verrous(db: Session, project: Project) -> List[Verrou]:
    return (
        db.query(Verrou)
        .join(DiagnosticRun, Verrou.diagnostic_run_id == DiagnosticRun.id)
        .filter(DiagnosticRun.project_id == project.id)
        .order_by(Verrou.created_at.desc())
        .all()
    )


def _source_json_text(source_json: Any) -> str:
    if not isinstance(source_json, dict):
        return ""
    for key in ["manual_scholar_text", "text", "source_text", "description", "excerpt", "content"]:
        v = source_json.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return _flatten_text(source_json, max_chars=1200)



def _norm_scholar_text(text: Any) -> str:
    s = str(text or "").lower()
    s = s.replace("œ", "oe")
    s = re.sub(r"[^a-z0-9àâäéèêëîïôöùûüç°³/%.-]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _extract_source_passages_from_source_json(src: Dict[str, Any]) -> List[str]:
    passages: List[str] = []

    def add(value: Any):
        if isinstance(value, str) and value.strip():
            passages.append(value.strip())
        elif isinstance(value, dict):
            for key in ["text", "source_text", "excerpt", "content", "passage"]:
                v = value.get(key)
                if isinstance(v, str) and v.strip():
                    passages.append(v.strip())
        elif isinstance(value, list):
            for item in value:
                add(item)

    for key in [
        "manual_scholar_text",
        "scientific_query_text",
        "source_text",
        "text",
        "excerpt",
        "content",
        "supporting_passages",
        "sources",
        "evidence",
        "source_passages",
    ]:
        if key in src:
            add(src.get(key))

    return [p for p in passages if len(p) >= 25][:10]


def _has_any(text: str, terms: List[str]) -> bool:
    nt = _norm_scholar_text(text)
    return any(_norm_scholar_text(t) in nt for t in terms)


def _generic_title(title: str) -> bool:
    nt = _norm_scholar_text(title)
    generic = [
        "non transférabilité", "non transferabilite", "cause racine", "performance insuffisante",
        "compromis entre contraintes", "comportement instable", "qualité de sortie", "qualite sortie",
        "fiabilité", "fiabilite", "dégradation", "degradation",
    ]
    return any(g in nt for g in generic)


def _top_scientific_terms(text: str, max_terms: int = 12) -> List[str]:
    stop = {
        "verrou", "verrous", "technique", "scientifique", "projet", "dossier", "solution", "solutions",
        "contrainte", "contraintes", "performance", "performances", "fonctionnement", "conditions",
        "recherche", "developpement", "développement", "etude", "étude", "analyse", "travaux",
        "resultat", "résultat", "resultats", "résultats", "possible", "implicite", "validation",
        "consultant", "document", "documents", "source", "sources", "indices", "maitrise", "maîtrise",
        "systeme", "système", "processus", "methode", "méthode", "dans", "pour", "avec", "sans",
        "par", "sur", "des", "les", "une", "the", "and", "with", "from", "that", "this",
    }
    toks = re.findall(r"[a-zA-ZÀ-ÿ0-9][a-zA-ZÀ-ÿ0-9%°µµ/\-.']{2,}", _norm_scholar_text(text))
    clean = []
    for t in toks:
        t = t.strip("-_. '")
        if len(t) < 3 or t in stop:
            continue
        clean.append(t)
    counts = {}
    for t in clean:
        counts[t] = counts.get(t, 0) + 1
    return [t for t, _ in sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:max_terms]]


def _first_informative_sentence(text: str, fallback: str = "") -> str:
    for sentence in re.split(r"(?<=[.!?;])\s+|\n+", str(text or "")):
        sentence = sentence.strip(" -•\t")
        if 40 <= len(sentence) <= 220:
            return sentence
    return fallback or str(text or "")[:180]


def _build_enriched_scientific_profile(
    title: str,
    source_text: str,
    project_name: str = "",
    domain_label: str = "",
) -> Dict[str, Any]:
    """Construit un brief scientifique générique, sans fabriquer de requêtes brutes.

    Le backend conserve les preuves et sépare les noms locaux. La normalisation
    scientifique et les requêtes anglaises sont produites dans l'agent EnnoScholar.
    Aucune règle client, projet, logiciel ou domaine n'est codée en dur ici.
    """
    title = str(title or "").strip()
    source_text = str(source_text or "").strip()
    domain_label = str(domain_label or "").strip()
    project_name = str(project_name or "").strip()

    clean_src = source_text[:3600]
    enriched_title = title or "Signal scientifique à analyser"
    if _generic_title(enriched_title) and clean_src:
        enriched_title = _first_informative_sentence(clean_src, fallback=enriched_title)

    combined = "\n".join([enriched_title, clean_src])
    terms = _top_scientific_terms(combined, max_terms=16)

    # Les noms propres/sigles sont transmis comme contexte local, jamais comme
    # concepts scientifiques centraux. L'agent les exclura par défaut des requêtes.
    local_names = []
    for token in re.findall(r"\b[A-Z][A-Z0-9_-]{1,}\b|\b[A-Z][a-zA-Z0-9_-]{3,}\b", combined):
        if token not in local_names:
            local_names.append(token)
        if len(local_names) >= 12:
            break

    context_bits = []
    if domain_label:
        context_bits.append(f"Domaine indicatif du projet : {domain_label}.")
    if project_name:
        context_bits.append(f"Projet : {project_name}.")

    scientific_text = (
        f"{enriched_title}. "
        "Analyser l'incertitude scientifique à partir des preuves techniques locales, "
        "en distinguant les phénomènes, les méthodes, les contraintes et les critères de validation. "
        + " ".join(context_bits)
        + f" Indices sources : {clean_src}"
    ).strip()

    return {
        "enriched_title": enriched_title,
        "scientific_text": scientific_text,
        # Important : plus de requêtes par fréquence brute côté backend.
        "suggested_queries": [],
        "profile": "generic_evidence_brief_v2",
        "extracted_terms": terms,
        "local_names": local_names,
        "query_generation_owner": "agents.EnnoScholar.scientific_query_normalizer",
        "project_specific_rules": False,
    }


def verrou_to_scholar_payload(
    verrou: Verrou,
    project_name: str = "",
    domain_label: str = "",
) -> Dict[str, Any]:
    src = verrou.source_json if isinstance(verrou.source_json, dict) else {}

    passages = _extract_source_passages_from_source_json(src)
    source_text = " ".join(passages[:6])

    fallback_text = (
        _source_json_text(src)
        or verrou.justification
        or verrou.title
        or ""
    )
    if len(source_text) < 80:
        source_text = " ".join([source_text, fallback_text]).strip()

    original_title = verrou.title or ""
    profile = _build_enriched_scientific_profile(
        title=original_title,
        source_text=source_text,
        project_name=project_name,
        domain_label=domain_label,
    )

    enriched_title = profile["enriched_title"]
    scientific_text = profile["scientific_text"]
    suggested_queries = profile.get("suggested_queries") or []

    raw_item = {
        "text": scientific_text,
        "source_text": source_text,
        "supporting_passages": [{"text": p} for p in passages[:8]],
        "original_title": original_title,
        "enriched_title": enriched_title,
        "enrichment_profile": profile.get("profile"),
        "local_names": profile.get("local_names") or [],
    }

    return sanitize_json_value({
        "verrou_id": str(verrou.id),
        "db_verrou_id": verrou.id,

        # Le titre envoyé à EnnoScholar est maintenant le titre scientifique enrichi.
        "title": enriched_title,
        "verrou_title": enriched_title,
        "original_title": original_title,

        # Le texte principal est maintenant riche et basé sur les sources techniques.
        "text": scientific_text,
        "scientific_query_text": scientific_text,
        "suggested_queries": suggested_queries,

        "raw_item": raw_item,
        "context": {
            "project": project_name,
            "domain": domain_label,
            "original_verrou_title": original_title,
            "source_documents": src.get("sources") if isinstance(src, dict) else [],
        },

        "frascati": {
            "decision": verrou.tag_cir,
            "frascati_score": verrou.score,
        },
        "score": verrou.score,
        "consultant_status": verrou.consultant_status,
        "sources": src.get("sources") if isinstance(src, dict) else [],
        "source_passages": passages[:8],
        "source_json": {
            **src,
            "scholar_enrichment": {
                "profile": profile.get("profile"),
                "original_title": original_title,
                "enriched_title": enriched_title,
                "suggested_queries": suggested_queries,
                "local_names": profile.get("local_names") or [],
                "query_generation_owner": profile.get("query_generation_owner"),
            },
        },
    })



# ============================================================
# V111 — Déduplication + regroupement avant EnnoScholar
# ============================================================

def _v111_norm_group_text(value: Any) -> str:
    s = str(value or "").lower()
    s = s.replace("œ", "oe")
    s = re.sub(r"[^a-z0-9àâäéèêëîïôöùûüç]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _v111_unique_strings(values: List[Any], max_items: int = 30) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values or []:
        if isinstance(value, dict):
            value = value.get("text") or value.get("title") or value.get("name") or ""
        s = str(value or "").strip()
        if not s:
            continue
        k = _v111_norm_group_text(s)[:260]
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
        if len(out) >= max_items:
            break
    return out


def _v111_unique_sources(values: List[Any], max_items: int = 40) -> List[Any]:
    out: List[Any] = []
    seen = set()
    for value in values or []:
        if isinstance(value, dict):
            k = _v111_norm_group_text(value.get("document") or value.get("file") or value.get("source") or value.get("name") or json.dumps(value, ensure_ascii=False, sort_keys=True))
        else:
            k = _v111_norm_group_text(value)
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(value)
        if len(out) >= max_items:
            break
    return out


def _v111_group_key(item: Dict[str, Any]) -> str:
    raw_item = item.get("raw_item") if isinstance(item.get("raw_item"), dict) else {}
    src = item.get("source_json") if isinstance(item.get("source_json"), dict) else {}
    enrichment = src.get("scholar_enrichment") if isinstance(src.get("scholar_enrichment"), dict) else {}

    profile = (
        raw_item.get("enrichment_profile")
        or enrichment.get("profile")
        or item.get("enrichment_profile")
        or ""
    )
    profile = str(profile or "").strip()

    # Version générique : aucun profil métier ne force le regroupement.
    title = item.get("verrou_title") or item.get("title") or ""
    norm_title = _v111_norm_group_text(title)

    # Fallback : titre enrichi normalisé.
    if norm_title:
        return "title::" + norm_title[:180]

    return "misc::" + str(item.get("verrou_id") or item.get("db_verrou_id") or "unknown")


def _v111_group_reason(group_key: str, items: List[Dict[str, Any]]) -> str:
    profile = group_key.replace("profile::", "") if group_key.startswith("profile::") else ""
    titles = [str(x.get("original_title") or x.get("title") or "").strip() for x in items]

    if len(items) > 1:
        return "titres enrichis proches après normalisation et sources techniques compatibles"

    return "aucun regroupement nécessaire : verrou scientifique unique"


def _v111_merge_group_items(items: List[Dict[str, Any]], group_key: str) -> Dict[str, Any]:
    """
    Fusionne plusieurs signaux retenus en un seul verrou scientifique EnnoScholar.
    Le premier item reste le verrou porteur pour conserver un verrou_id DB compatible avec Article.verrou_id.
    Les autres sont conservés dans grouped_source_verrou_ids / grouped_original_titles.
    """
    primary = dict(items[0])
    raw_primary = dict(primary.get("raw_item") or {})
    source_json_primary = dict(primary.get("source_json") or {})

    grouped_ids = []
    grouped_db_ids = []
    original_titles = []
    enriched_titles = []
    all_sources: List[Any] = []
    all_passages: List[str] = []
    all_queries: List[str] = []
    all_texts: List[str] = []

    for item in items:
        vid = item.get("verrou_id") or item.get("db_verrou_id")
        dbid = item.get("db_verrou_id") or item.get("verrou_id")
        if vid is not None:
            grouped_ids.append(str(vid))
        if dbid is not None:
            grouped_db_ids.append(str(dbid))

        ot = str(item.get("original_title") or item.get("context", {}).get("original_verrou_title") or "").strip()
        if ot:
            original_titles.append(ot)

        et = str(item.get("verrou_title") or item.get("title") or "").strip()
        if et:
            enriched_titles.append(et)

        all_sources.extend(item.get("sources") or [])
        all_sources.extend((item.get("context") or {}).get("source_documents") or [])
        all_passages.extend(item.get("source_passages") or [])
        all_queries.extend(item.get("suggested_queries") or [])
        txt = str(item.get("scientific_query_text") or item.get("text") or "").strip()
        if txt:
            all_texts.append(txt)

    grouped_ids = _v111_unique_strings(grouped_ids, 50)
    grouped_db_ids = _v111_unique_strings(grouped_db_ids, 50)
    original_titles = _v111_unique_strings(original_titles, 30)
    enriched_titles = _v111_unique_strings(enriched_titles, 10)
    all_sources = _v111_unique_sources(all_sources, 60)
    all_passages = _v111_unique_strings(all_passages, 18)
    all_queries = _v111_unique_strings(all_queries, 12)
    all_texts = _v111_unique_strings(all_texts, 8)

    reason = _v111_group_reason(group_key, items)
    consolidated_title = enriched_titles[0] if enriched_titles else str(primary.get("title") or "Verrou scientifique consolidé")

    grouped_note = ""
    if len(items) > 1:
        grouped_note = (
            "\n\nSignaux EnnoDiagnostic regroupés automatiquement avant EnnoScholar : "
            + "; ".join(original_titles)
            + f". Raison du regroupement : {reason}."
        )

    merged_text = str(primary.get("scientific_query_text") or primary.get("text") or consolidated_title)
    complement_texts = [t for t in all_texts if _v111_norm_group_text(t)[:220] != _v111_norm_group_text(merged_text)[:220]]
    if complement_texts:
        merged_text += "\n\nIndices complémentaires issus des signaux regroupés :\n- " + "\n- ".join(t[:900] for t in complement_texts[:4])
    merged_text += grouped_note

    primary["title"] = consolidated_title
    primary["verrou_title"] = consolidated_title
    primary["text"] = merged_text
    primary["scientific_query_text"] = merged_text
    primary["suggested_queries"] = all_queries
    primary["sources"] = all_sources
    primary["source_passages"] = all_passages

    primary["grouping"] = {
        "active": len(items) > 1,
        "group_key": group_key,
        "grouped_count": len(items),
        "grouped_source_verrou_ids": grouped_ids,
        "grouped_db_verrou_ids": grouped_db_ids,
        "grouped_original_titles": original_titles,
        "consolidated_title": consolidated_title,
        "reason": reason,
    }

    raw_primary.update({
        "grouped_count": len(items),
        "grouped_source_verrou_ids": grouped_ids,
        "grouped_original_titles": original_titles,
        "grouping_reason": reason,
        "source_text": (raw_primary.get("source_text") or "")[:2500],
        "supporting_passages": [{"text": p} for p in all_passages[:12]],
    })
    primary["raw_item"] = raw_primary

    source_json_primary["scholar_grouping"] = primary["grouping"]
    primary["source_json"] = source_json_primary

    context = dict(primary.get("context") or {})
    context.update({
        "grouped_original_titles": original_titles,
        "grouped_source_verrou_ids": grouped_ids,
        "grouping_reason": reason,
        "source_documents": all_sources,
    })
    primary["context"] = context

    return sanitize_json_value(primary)

# >>> V117_GENERIC_SCHOLAR_GROUPING_BEGIN
# Regroupement générique EnnoScholar — sans profils techniques codés en dur.
# Principe : regrouper les signaux gardés par le consultant uniquement à partir
# de leur contenu textuel, des titres, des passages sources et des requêtes proposées.
# Ce bloc ne contient aucun profil projet codé en dur : il peut fonctionner sur plusieurs domaines,
# logiciel, biologie, électronique, matériaux, IA, etc.

import copy as _v117_copy
import hashlib as _v117_hashlib
import math as _v117_math
import re as _v117_re
import unicodedata as _v117_unicodedata
from collections import Counter as _V117Counter
from typing import Any as _V117Any, Dict as _V117Dict, List as _V117List, Tuple as _V117Tuple


_V117_STOPWORDS = {
    # français général
    "de", "des", "du", "la", "le", "les", "un", "une", "et", "ou", "a", "au", "aux", "en", "dans", "sur", "par", "pour",
    "avec", "sans", "sous", "chez", "entre", "vers", "afin", "ainsi", "plus", "moins", "tres", "très", "etre", "être",
    "est", "sont", "ete", "été", "ce", "cet", "cette", "ces", "son", "sa", "ses", "leur", "leurs", "nous", "vous", "ils", "elles",
    "il", "elle", "on", "que", "qui", "dont", "comme", "mais", "donc", "or", "ni", "car", "se", "du", "d", "l",
    # anglais général
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "without", "by", "from", "as", "at", "is", "are",
    "was", "were", "be", "been", "being", "this", "that", "these", "those", "it", "its", "into", "between", "within",
    # mots CIR / pipeline trop génériques
    "verrou", "verrous", "technologique", "technique", "scientifique", "scientifiques", "technologiques", "projet", "dossier",
    "question", "qualification", "solution", "solutions", "existantes", "existants", "contrainte", "contraintes", "performance", "performances",
    "fonctionnement", "conditions", "recherche", "developpement", "développement", "etude", "étude", "analyse", "travaux", "resultats", "résultats",
    "possible", "implicite", "confirmer", "valider", "validation", "consultant", "document", "documents", "sources", "indices", "source",
    "maitrise", "maîtrise", "amelioration", "amélioration", "optimisation", "systeme", "système", "processus", "methode", "méthode",
}


def _v117_strip_accents(text: str) -> str:
    text = str(text or "")
    text = _v117_unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not _v117_unicodedata.combining(ch))


def _v117_norm(text: str) -> str:
    text = _v117_strip_accents(text).lower()
    text = text.replace("’", "'")
    text = _v117_re.sub(r"[^a-z0-9%°µµ/\-\.\s']+", " ", text)
    text = _v117_re.sub(r"\s+", " ", text).strip()
    return text


def _v117_tokens(text: str) -> _V117List[str]:
    text = _v117_norm(text)
    raw = _v117_re.findall(r"[a-z0-9][a-z0-9%°µ/\-\.']{1,}", text)
    out = []
    for tok in raw:
        tok = tok.strip("-_. '")
        if len(tok) < 3:
            continue
        if tok in _V117_STOPWORDS:
            continue
        # Retirer les tokens trop purement administratifs
        if tok.startswith("fig") or tok.startswith("tableau"):
            continue
        out.append(tok)
    return out


def _v117_ngrams(tokens: _V117List[str]) -> _V117List[str]:
    grams = list(tokens)
    for n in (2, 3):
        for i in range(0, max(0, len(tokens) - n + 1)):
            gram_tokens = tokens[i:i+n]
            # Éviter les n-grams trop faibles
            if any(t in _V117_STOPWORDS for t in gram_tokens):
                continue
            grams.append(" ".join(gram_tokens))
    return grams


def _v117_counter(text: str) -> _V117Counter:
    toks = _v117_tokens(text)
    grams = _v117_ngrams(toks)
    c = _V117Counter(grams)
    # les n-grams techniques pèsent un peu plus que les mots isolés
    for k in list(c.keys()):
        if " " in k:
            c[k] *= 2.0
    return c


def _v117_cosine(a: _V117Counter, b: _V117Counter) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    dot = sum(float(a[k]) * float(b[k]) for k in common)
    na = _v117_math.sqrt(sum(float(v) * float(v) for v in a.values()))
    nb = _v117_math.sqrt(sum(float(v) * float(v) for v in b.values()))
    if not na or not nb:
        return 0.0
    return dot / (na * nb)


def _v117_jaccard(a_terms: _V117List[str], b_terms: _V117List[str]) -> float:
    a = set(a_terms or [])
    b = set(b_terms or [])
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def _v117_top_terms(counter: _V117Counter, n: int = 12) -> _V117List[str]:
    return [k for k, _ in counter.most_common(n)]


def _v117_get(obj: _V117Any, *keys: str, default: _V117Any = "") -> _V117Any:
    cur = obj
    for key in keys:
        if isinstance(cur, dict):
            cur = cur.get(key, default)
        else:
            return default
    return cur if cur is not None else default


def _v117_as_list(value: _V117Any) -> _V117List[_V117Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _v117_text_from_signal(item: _V117Dict[str, _V117Any]) -> str:
    """Construit une représentation textuelle générique du signal."""
    parts: _V117List[str] = []
    for key in (
        "verrou_title", "title", "original_title", "enriched_title",
        "text", "verrou_text", "scientific_query_text", "source_text",
    ):
        v = item.get(key)
        if isinstance(v, str) and v.strip():
            parts.append(v)

    raw = item.get("raw_item") if isinstance(item.get("raw_item"), dict) else {}
    for key in ("title", "verrou_title", "original_title", "enriched_title", "text", "source_text"):
        v = raw.get(key)
        if isinstance(v, str) and v.strip():
            parts.append(v)

    source_json = item.get("source_json") if isinstance(item.get("source_json"), dict) else {}
    for key in ("text", "source_text"):
        v = source_json.get(key)
        if isinstance(v, str) and v.strip():
            parts.append(v)

    # Passages sources
    for p in _v117_as_list(item.get("source_passages")):
        if isinstance(p, str):
            parts.append(p)
        elif isinstance(p, dict) and p.get("text"):
            parts.append(str(p.get("text")))
    for p in _v117_as_list(raw.get("supporting_passages")):
        if isinstance(p, str):
            parts.append(p)
        elif isinstance(p, dict) and p.get("text"):
            parts.append(str(p.get("text")))

    # Requêtes proposées par EnnoDiagnostic/EnnoScholar : utiles et génériques
    for q in _v117_as_list(item.get("suggested_queries")) + _v117_as_list(raw.get("suggested_queries")):
        if isinstance(q, str):
            parts.append(q)
        elif isinstance(q, dict) and q.get("query"):
            parts.append(str(q.get("query")))

    return "\n".join(str(p) for p in parts if str(p).strip())


def _v117_title(item: _V117Dict[str, _V117Any]) -> str:
    for path in (
        ("verrou_title",), ("title",), ("enriched_title",), ("original_title",),
        ("raw_item", "enriched_title"), ("raw_item", "verrou_title"), ("raw_item", "title"), ("raw_item", "original_title"),
    ):
        v = _v117_get(item, *path, default="")
        if isinstance(v, str) and v.strip():
            return v.strip()
    return "Signal technique à analyser"


def _v117_original_title(item: _V117Dict[str, _V117Any]) -> str:
    for path in (("original_title",), ("raw_item", "original_title"), ("context", "original_verrou_title"), ("title",), ("verrou_title",)):
        v = _v117_get(item, *path, default="")
        if isinstance(v, str) and v.strip():
            return v.strip()
    return _v117_title(item)


def _v117_signal_id(item: _V117Dict[str, _V117Any]) -> str:
    for key in ("verrou_id", "db_verrou_id", "id"):
        v = item.get(key)
        if v is not None and str(v).strip():
            return str(v).strip()
    raw = _v117_text_from_signal(item)[:500]
    return _v117_hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:10]


def _v117_similarity(sig_a: _V117Dict[str, _V117Any], sig_b: _V117Dict[str, _V117Any]) -> _V117Tuple[float, _V117List[str]]:
    ca = sig_a.get("_v117_counter") or _V117Counter()
    cb = sig_b.get("_v117_counter") or _V117Counter()
    ta = sig_a.get("_v117_top_terms") or []
    tb = sig_b.get("_v117_top_terms") or []

    cosine = _v117_cosine(ca, cb)
    jacc = _v117_jaccard(ta, tb)

    title_a = _v117_norm(sig_a.get("_v117_title", ""))
    title_b = _v117_norm(sig_b.get("_v117_title", ""))
    title_terms_a = _v117_tokens(title_a)
    title_terms_b = _v117_tokens(title_b)
    title_jacc = _v117_jaccard(title_terms_a, title_terms_b)

    shared = [t for t in ta if t in set(tb)]
    score = (0.62 * cosine) + (0.25 * jacc) + (0.13 * title_jacc)

    # Bonus si plusieurs expressions techniques non génériques se recoupent.
    technical_shared = [t for t in shared if len(t) >= 5 and t not in _V117_STOPWORDS]
    if len(technical_shared) >= 4:
        score += 0.08
    elif len(technical_shared) >= 2:
        score += 0.04

    return min(1.0, float(score)), technical_shared[:10]


def _v117_prepare_signal(item: _V117Dict[str, _V117Any]) -> _V117Dict[str, _V117Any]:
    out = _v117_copy.deepcopy(item)
    text = _v117_text_from_signal(out)
    c = _v117_counter(text)
    out["_v117_text"] = text
    out["_v117_counter"] = c
    out["_v117_top_terms"] = _v117_top_terms(c, 18)
    out["_v117_title"] = _v117_title(out)
    out["_v117_original_title"] = _v117_original_title(out)
    out["_v117_id"] = _v117_signal_id(out)
    return out


def _v117_choose_representative(group: _V117List[_V117Dict[str, _V117Any]]) -> _V117Dict[str, _V117Any]:
    # Choisit le signal le plus riche, sans règle métier codée en dur.
    def score(x: _V117Dict[str, _V117Any]) -> float:
        title = x.get("_v117_title", "")
        text_len = len(x.get("_v117_text", ""))
        source_count = len(_v117_as_list(x.get("source_passages")))
        query_count = len(_v117_as_list(x.get("suggested_queries")))
        return len(title) * 0.3 + min(text_len, 6000) * 0.002 + source_count * 2 + query_count
    return max(group, key=score)


def _v117_build_group(group: _V117List[_V117Dict[str, _V117Any]], index: int) -> _V117Tuple[_V117Dict[str, _V117Any], _V117Dict[str, _V117Any]]:
    rep = _v117_choose_representative(group)
    merged = _v117_copy.deepcopy(rep)

    ids = [_v117_signal_id(x) for x in group]
    db_ids = [str(x.get("db_verrou_id") or x.get("verrou_id") or x.get("id") or _v117_signal_id(x)) for x in group]
    original_titles = []
    for x in group:
        t = x.get("_v117_original_title") or _v117_original_title(x)
        if t and t not in original_titles:
            original_titles.append(t)

    # Termes partagés : intersection souple des top termes du groupe.
    term_counts = _V117Counter()
    for x in group:
        for t in set(x.get("_v117_top_terms") or []):
            term_counts[t] += 1
    min_count = 2 if len(group) > 1 else 1
    shared_terms = [t for t, n in term_counts.most_common(14) if n >= min_count and t not in _V117_STOPWORDS]

    title = rep.get("_v117_title") or _v117_title(rep)
    group_hash = _v117_hashlib.sha1(("|".join(ids) + title).encode("utf-8", errors="ignore")).hexdigest()[:10]
    group_key = f"semantic::{group_hash}"

    reason = (
        "Regroupement générique par similarité sémantique entre les titres, passages sources, "
        "preuves techniques et requêtes de recherche."
    )
    if shared_terms:
        reason += " Termes communs détectés : " + ", ".join(shared_terms[:8]) + "."

    if len(group) > 1:
        grouped_sentence = (
            "\n\nSignaux EnnoDiagnostic regroupés automatiquement avant EnnoScholar : "
            + "; ".join(original_titles)
            + ". Raison du regroupement : "
            + reason
        )
        base_text = str(merged.get("text") or merged.get("verrou_text") or "")
        merged["text"] = (base_text + grouped_sentence).strip()
        merged["verrou_text"] = merged["text"]

    merged["verrou_title"] = title
    merged["title"] = title
    merged["group_key"] = group_key
    merged["grouping_method"] = "generic_semantic_similarity"
    merged["grouped_count"] = len(group)
    merged["grouped_source_verrou_ids"] = ids
    merged["grouped_db_verrou_ids"] = db_ids
    merged["grouped_original_titles"] = original_titles
    merged["semantic_group_terms"] = shared_terms
    merged["source_signals"] = original_titles

    # Nettoyer les champs internes avant retour
    for k in list(merged.keys()):
        if str(k).startswith("_v117_"):
            merged.pop(k, None)

    report = {
        "group_key": group_key,
        "profile": "generic_semantic_similarity",
        "consolidated_title": title,
        "grouped_count": len(group),
        "grouped_source_verrou_ids": ids,
        "grouped_db_verrou_ids": db_ids,
        "grouped_original_titles": original_titles,
        "shared_terms": shared_terms,
        "reason": reason,
    }
    return merged, report


def consolidate_selected_verrous_for_scholar(selected_verrous: _V117Any, *args: _V117Any, **kwargs: _V117Any) -> _V117Dict[str, _V117Any]:
    """
    Regroupe les signaux gardés par le consultant avant EnnoScholar.

    Version V119 : générique, sans profils métier codés en dur.
    - Entrée : liste de signaux/verrous déjà gardés par le consultant.
    - Sortie : payload compatible avec les versions précédentes :
      verrous, grouping_summary, grouping_report, verrous_before_grouping.
    """
    if isinstance(selected_verrous, dict):
        # Compatibilité si l'appelant passe déjà un payload.
        raw_verrous = selected_verrous.get("verrous") or selected_verrous.get("selected_verrous") or []
        base_payload = _v117_copy.deepcopy(selected_verrous)
    else:
        raw_verrous = selected_verrous or []
        base_payload = {}

    raw_list = [x for x in _v117_as_list(raw_verrous) if isinstance(x, dict)]

    # Garder uniquement les signaux explicitement conservés si l'information existe.
    # Si le statut n'est pas présent, on ne filtre pas pour éviter de casser les anciens flux.
    has_status = any("consultant_status" in x or "status" in x for x in raw_list)
    if has_status:
        kept = []
        for x in raw_list:
            st = str(x.get("consultant_status") or x.get("status") or "").strip().lower()
            if st in {"garde", "gardé", "garder", "keep", "kept", "selected", "valide", "validé", "retenu"}:
                kept.append(x)
        signals = kept
    else:
        signals = raw_list

    prepared = [_v117_prepare_signal(x) for x in signals]

    # Seuil générique : assez strict pour éviter de fusionner des sujets différents.
    # Peut être ajusté sans changer le code via kwargs ou variable d'environnement si besoin.
    try:
        threshold = float(kwargs.get("threshold", 0.46))
    except Exception:
        threshold = 0.46

    groups: _V117List[_V117List[_V117Dict[str, _V117Any]]] = []
    group_terms: _V117List[_V117Counter] = []

    for sig in prepared:
        best_idx = -1
        best_score = 0.0
        best_shared: _V117List[str] = []

        for i, group in enumerate(groups):
            # Comparer au représentant et au centroïde textuel du groupe.
            rep = group[0]
            score_rep, shared_rep = _v117_similarity(sig, rep)

            centroid_sig = {
                "_v117_counter": group_terms[i],
                "_v117_top_terms": _v117_top_terms(group_terms[i], 18),
                "_v117_title": group[0].get("_v117_title", ""),
            }
            score_centroid, shared_centroid = _v117_similarity(sig, centroid_sig)
            score = max(score_rep, score_centroid)
            shared = shared_rep if score_rep >= score_centroid else shared_centroid

            if score > best_score:
                best_idx = i
                best_score = score
                best_shared = shared

        # Sécurité anti-fusion : il faut au moins des termes techniques communs,
        # sauf si les titres sont très proches.
        title_close = False
        if best_idx >= 0:
            title_close = _v117_jaccard(
                _v117_tokens(sig.get("_v117_title", "")),
                _v117_tokens(groups[best_idx][0].get("_v117_title", "")),
            ) >= 0.55

        if best_idx >= 0 and best_score >= threshold and (len(best_shared) >= 2 or title_close):
            groups[best_idx].append(sig)
            group_terms[best_idx].update(sig.get("_v117_counter") or _V117Counter())
        else:
            groups.append([sig])
            group_terms.append(_V117Counter(sig.get("_v117_counter") or {}))

    consolidated: _V117List[_V117Dict[str, _V117Any]] = []
    reports: _V117List[_V117Dict[str, _V117Any]] = []
    for idx, group in enumerate(groups, start=1):
        merged, report = _v117_build_group(group, idx)
        consolidated.append(merged)
        reports.append(report)

    duplicates_removed = max(0, len(signals) - len(consolidated))
    summary = {
        "active": True,
        "method": "generic_semantic_similarity",
        "input_signals_count": len(signals),
        "grouped_verrous_count": len(consolidated),
        "duplicates_removed": duplicates_removed,
        "hardcoded_profiles_used": False,
        "message": "Les signaux gardés par le consultant ont été regroupés par similarité sémantique générique, sans profils métier codés en dur.",
    }

    result = _v117_copy.deepcopy(base_payload)
    result.update({
        "ok": True,
        "source": "consultant_selected_verrous_from_ennodiagnostic_grouped_v117_generic",
        "selection_rule": "Only consultant-retained signals are selected, then similar signals are grouped with a generic semantic similarity method.",
        "selected_verrous_count": len(signals),
        "selected_signals_count": len(signals),
        "grouped_verrous_count": len(consolidated),
        "grouping_applied": True,
        "grouping_summary": summary,
        "grouping_report": {"ok": True, "groups": reports},
        "verrous_before_grouping": raw_list,
        "verrous": consolidated,
    })
    return result

# <<< V117_GENERIC_SCHOLAR_GROUPING_END

def build_scholar_payload_from_selected_verrous(db: Session, project: Project, max_verrous: int = 8) -> Dict[str, Any]:
    # max_verrous is kept for API compatibility, not as a silent selection cap.
    # Each consultant-retained lock must receive its own search and result.
    selected = get_selected_verrous_for_scholar(db, project)

    nlp_path = current_nlp_result_path(project)
    nlp = read_json(nlp_path, {}) if nlp_path else {}
    domain_detection = _extract_domain_detection(nlp)

    diagnostic_context = extract_diagnostic_context_from_report(project)

    # V111 : étape 1 — enrichir chaque signal gardé comme avant.
    enriched_verrous = [
        verrou_to_scholar_payload(
            v,
            project_name=project.project_name,
            domain_label=project.domain_label or "",
        )
        for v in selected
    ]

    return sanitize_json_value({
        "organisme": project.organisme,
        "project": project.project_name,
        "year": str(project.year),
        "source": "consultant_selected_verrous_from_ennodiagnostic",
        "selection_rule": "Every verrou with consultant_status='garde' is searched independently, without truncation or merging.",
        "selected_verrous_count": len(selected),
        "selected_signals_count": len(selected),
        "grouped_verrous_count": len(enriched_verrous),
        "grouping_applied": False,
        "input_nlp_result": str(nlp_path) if nlp_path else "",
        "diagnostic_context": diagnostic_context,
        "domain_detection": domain_detection,
        "grouping_summary": {
            "active": False,
            "input_signals_count": len(selected),
            "grouped_verrous_count": len(enriched_verrous),
            "duplicates_removed": 0,
            "message": "Chaque verrou gardé est recherché séparément, sans fusion automatique.",
        },
        "grouping_report": {"ok": True, "groups": []},
        "verrous_before_grouping": enriched_verrous,
        "verrous": enriched_verrous,
    })


def create_scholar_run_from_files(db: Session, project: Project) -> ScholarRun:
    paths = scholar_paths(project)
    bundle = read_scholar_bundle(project)

    run = ScholarRun(
        project_id=project.id,
        status="completed_from_existing_files" if bundle.get("files_found", {}).get("report") else "no_result_found",
        report_path=str(paths["report"]) if paths["report"].exists() else None,
        raw_result_json=bundle,
        completed_at=datetime.utcnow(),
    )

    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _decision_counts(report: Dict[str, Any]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for r in report.get("results") or []:
        d = str(r.get("decision") or "unknown")
        counts[d] = counts.get(d, 0) + 1
    return counts


def build_scholar_summary(report: Dict[str, Any]) -> Dict[str, Any]:
    results = report.get("results") or []
    decisions = _decision_counts(report)

    defensible = decisions.get("verrou_scientifiquement_defendable", 0)
    confirm = decisions.get("verrou_a_confirmer_par_etat_art", 0)
    weak = decisions.get("support_scientifique_faible", 0)
    none = decisions.get("aucun_article_trouve", 0)
    failed = [r for r in results if r.get("subject_search_failed")]

    return sanitize_json_value({
        "ok": True,
        "verrous_analyzed": len(results),
        "decision_counts": decisions,
        "verrous_scientifiquement_defendables": defensible,
        "verrous_a_confirmer": confirm,
        "support_scientifique_faible": weak,
        "aucun_article_trouve": none,
        "searches_failed": len(failed),
        "failed_verrou_ids": [r.get("verrou_id") for r in failed],
        "search_complete": not any(r.get("search_incomplete") or r.get("subject_search_failed") for r in results),
        "articles_total": sum(len(r.get("articles") or []) for r in results),
        "interpretation": (
            "EnnoScholar valide scientifiquement les verrous sélectionnés par le consultant. "
            "Il ne décide pas seul de l'éligibilité CIR finale."
        ),
    })


def run_ennoscholar_from_selected_verrous(
    db: Session,
    project: Project,
    max_verrous: int = 8,
    limit_per_query: int = 50,
    offline_dry_run: bool = False,
) -> ScholarRun:
    """
    Lien EnnoDiagnostic -> EnnoScholar :
    1. lit les verrous consultant_status='garde'
    2. construit un payload scientifique
    3. lance EnnoScholar sur ces verrous uniquement
    4. sauvegarde report + summary
    5. crée un ScholarRun
    """
    ensure_ennosmart_imports()

    # EnnoScholar du projet est dans agents/EnnoScholar.
    try:
        from agents.EnnoScholar.scholar_agent import EnnoScholarAgent
    except Exception:
        from agents.EnnoScholar.scholar_agent import EnnoScholarAgent

    paths = scholar_paths(project)
    selected_verrous = get_selected_verrous_for_scholar(db, project)

    if not selected_verrous:
        raise RuntimeError(
            "Aucun verrou validé pour EnnoScholar. "
            "Dans EnnoDiagnostic, sélectionne au moins un verrou avec le statut 'garde'."
        )

    payload = build_scholar_payload_from_selected_verrous(db, project, max_verrous=max_verrous)
    write_json(paths["payload"], payload)

    use_semantic = os.getenv("ENNOSCHOLAR_USE_SEMANTIC_SCHOLAR", "1").strip() != "0"
    use_openalex = os.getenv("ENNOSCHOLAR_USE_OPENALEX", "1").strip() != "0"
    use_arxiv = os.getenv("ENNOSCHOLAR_USE_ARXIV", "1").strip() != "0"

    agent = EnnoScholarAgent(
        use_semantic_scholar=use_semantic,
        use_openalex=use_openalex,
        use_arxiv=use_arxiv,
        limit_per_query=limit_per_query,
        offline_dry_run=offline_dry_run,
    )

    report = agent.run(payload)
    # V111 : garder la trace du regroupement dans le rapport EnnoScholar.
    report["grouping_summary"] = payload.get("grouping_summary") or {}
    report["grouping_report"] = payload.get("grouping_report") or {}
    report["verrous_before_grouping"] = payload.get("verrous_before_grouping") or []
    report["input_payload"] = str(paths["payload"])
    report["outputs"] = {
        "payload": str(paths["payload"]),
        "report": str(paths["report"]),
        "summary": str(paths["summary"]),
    }
    report["selection"] = {
        "selected_verrou_ids": [v.get("db_verrou_id") for v in payload["verrous"]],
        "selected_verrous_count": len(payload["verrous"]),
        "rule": "consultant_status == garde",
        "grouped_verrous_count": payload.get("grouped_verrous_count"),
        "grouping_applied": payload.get("grouping_applied"),
    }

    summary = build_scholar_summary(report)
    # V111 : exposer le regroupement au frontend via bundle.summary.
    summary["grouping_summary"] = payload.get("grouping_summary") or {}
    summary["grouping_report"] = payload.get("grouping_report") or {}

    write_json(paths["report"], report)
    write_json(paths["summary"], summary)

    run = ScholarRun(
        project_id=project.id,
        status="completed",
        report_path=str(paths["report"]),
        raw_result_json={
            "report": report,
            "summary": summary,
            "payload": payload,
            "results": report.get("results") or [],
        },
        completed_at=datetime.utcnow(),
    )

    db.add(run)
    project.status = "EnnoScholar terminé"
    db.commit()
    db.refresh(run)

    # Synchroniser directement les articles pour que le frontend les voie.
    sync_articles_from_scholar(db, run)

    # >>> ENNOSMART_RESEARCH_UPGRADE_V2_PREFLIGHT
    try:
        from services.scholar_evidence_preflight_service import run_or_queue_preflight
        preflight_report = run_or_queue_preflight(db, project, run)
        db.refresh(run)
    except Exception as exc:
        raw = dict(run.raw_result_json or {})
        raw["evidence_preflight"] = {
            "enabled": True,
            "mode": "v2_error",
            "error": f"{type(exc).__name__}: {exc}",
        }
        run.raw_result_json = raw
        db.add(run)
        db.commit()
        db.refresh(run)
    # <<< ENNOSMART_RESEARCH_UPGRADE_V2_PREFLIGHT

    return run


def run_ennoscholar(db: Session, project: Project) -> ScholarRun:
    offline = os.getenv("ENNOSCHOLAR_OFFLINE_DRY_RUN", "0").strip() == "1"
    max_verrous = int(os.getenv("ENNOSCHOLAR_MAX_VERROUS", "8"))
    limit = int(os.getenv("ENNOSCHOLAR_LIMIT_PER_QUERY", "50"))

    return run_ennoscholar_from_selected_verrous(
        db=db,
        project=project,
        max_verrous=max_verrous,
        limit_per_query=limit,
        offline_dry_run=offline,
    )


def _get_title(item: Dict[str, Any]) -> Optional[str]:
    for key in ("title", "titre", "paper_title", "article_title", "name"):
        v = item.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _to_int(value: Any) -> Optional[int]:
    try:
        if isinstance(value, str) and len(value) >= 4 and value[:4].isdigit():
            return int(value[:4])
        return int(value)
    except Exception:
        return None


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return None


def _article_year(item: Dict[str, Any]) -> Optional[int]:
    for key in ("year", "publication_year", "published_year", "date"):
        y = _to_int(item.get(key))
        if y:
            return y
    return None


def _article_score(item: Dict[str, Any]) -> Optional[float]:
    for key in ("relevance_score", "score", "similarity", "rank_score", "final_score"):
        s = _to_float(item.get(key))
        if s is not None:
            return s
    return None


def _article_source(item: Dict[str, Any]) -> Optional[str]:
    for key in ("source", "database", "provider", "origin"):
        v = item.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    if item.get("arxiv_id"):
        return "ArXiv"
    if item.get("openalex_id"):
        return "OpenAlex"
    if item.get("semantic_scholar_id"):
        return "Semantic Scholar"
    return None


def _article_tag(item: Dict[str, Any]) -> Optional[str]:
    for key in ("tag", "tag_article", "article_tag", "classification", "label"):
        v = item.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _article_url(item: Dict[str, Any]) -> Optional[str]:
    for key in ("url", "pdf_url", "link", "landing_page_url"):
        v = item.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _article_doi(item: Dict[str, Any]) -> Optional[str]:
    v = item.get("doi")
    return v.strip() if isinstance(v, str) and v.strip() else None


def _report_from_run(run: ScholarRun) -> Dict[str, Any]:
    data = run.raw_result_json or {}
    if isinstance(data.get("report"), dict):
        return data["report"]
    if isinstance(data.get("bundle"), dict) and isinstance(data["bundle"].get("report"), dict):
        return data["bundle"]["report"]
    return data if isinstance(data, dict) else {}


# CONSULTANT_DECISION_MEMORY_V1
def _consultant_decision_norm_v1(value):
    import unicodedata

    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _consultant_status_v1(value):
    normalized = _consultant_decision_norm_v1(value).replace(" ", "_")
    if normalized in {"garde", "garder", "gardee", "keep", "kept", "selected"}:
        return "garde"
    if normalized in {"rejete", "rejetee", "reject", "rejected"}:
        return "rejete"
    return "en_attente"


def _consultant_status_priority_v1(value):
    status = _consultant_status_v1(value)
    if status == "garde":
        return 3
    if status == "rejete":
        return 2
    return 1


def _consultant_doi_v1(value):
    text = str(value or "").strip().lower()
    text = re.sub(
        r"^(?:doi\s*:\s*|https?://(?:dx\.)?doi\.org/)",
        "",
        text,
    )
    return text.strip().rstrip("./")


def _consultant_url_v1(value):
    text = str(value or "").strip().lower()
    text = text.split("#", 1)[0].split("?", 1)[0]
    return text.rstrip("/")


def _consultant_year_v1(value):
    match = re.search(r"\b(?:19|20)\d{2}\b", str(value or ""))
    return match.group(0) if match else ""


def _consultant_source_json_v1(value):
    return value if isinstance(value, dict) else {}


def _consultant_identity_keys_v1(
    *,
    title=None,
    year=None,
    doi=None,
    url=None,
    paper_id=None,
):
    keys = set()

    normalized_doi = _consultant_doi_v1(doi)
    if normalized_doi:
        keys.add(f"doi:{normalized_doi}")

    normalized_url = _consultant_url_v1(url)
    if normalized_url:
        keys.add(f"url:{normalized_url}")

    normalized_paper_id = re.sub(
        r"\s+",
        "",
        str(paper_id or "").strip().lower(),
    )
    if normalized_paper_id:
        keys.add(f"paper:{normalized_paper_id}")

    normalized_title = _consultant_decision_norm_v1(title)
    normalized_year = _consultant_year_v1(year)
    if normalized_title:
        keys.add(f"title:{normalized_title}")
        if normalized_year:
            keys.add(f"title_year:{normalized_title}:{normalized_year}")

    return keys


def _consultant_model_identity_keys_v1(article):
    source = _consultant_source_json_v1(article.source_json)
    return _consultant_identity_keys_v1(
        title=article.title or source.get("title") or source.get("article_title"),
        year=article.year or source.get("year") or source.get("publication_year"),
        doi=article.doi or source.get("doi"),
        url=article.url or source.get("url") or source.get("link"),
        paper_id=(
            source.get("paper_id")
            or source.get("paperId")
            or source.get("openalex_id")
        ),
    )


def _consultant_item_identity_keys_v1(item, title):
    source = _consultant_source_json_v1(item.get("source_json"))
    return _consultant_identity_keys_v1(
        title=title,
        year=(
            item.get("year")
            or item.get("publication_year")
            or item.get("published_year")
            or source.get("year")
        ),
        doi=item.get("doi") or source.get("doi"),
        url=(
            item.get("url")
            or item.get("link")
            or item.get("landing_page_url")
            or source.get("url")
        ),
        paper_id=(
            item.get("paper_id")
            or item.get("paperId")
            or item.get("openalex_id")
            or source.get("paper_id")
            or source.get("paperId")
        ),
    )


def _consultant_item_status_v1(item):
    source = _consultant_source_json_v1(item.get("source_json"))
    return _consultant_status_v1(
        item.get("consultant_status")
        or item.get("consultant_decision")
        or source.get("consultant_status")
        or source.get("consultant_decision")
    )


def _consultant_project_decision_memory_v1(db, run):
    rows = (
        db.query(Article)
        .join(ScholarRun, Article.scholar_run_id == ScholarRun.id)
        .filter(
            ScholarRun.project_id == run.project_id,
            Article.scholar_run_id != run.id,
        )
        .all()
    )

    memory = {}
    for article in rows:
        status = _consultant_status_v1(article.consultant_status)
        if status == "en_attente":
            continue

        for key in _consultant_model_identity_keys_v1(article):
            current = memory.get(key, "en_attente")
            if _consultant_status_priority_v1(status) > _consultant_status_priority_v1(current):
                memory[key] = status

    return memory


def _consultant_preserved_status_v1(memory, item, title):
    statuses = [
        memory[key]
        for key in _consultant_item_identity_keys_v1(item, title)
        if key in memory
    ]
    item_status = _consultant_item_status_v1(item)
    if item_status != "en_attente":
        statuses.append(item_status)

    if not statuses:
        return "en_attente"
    return max(statuses, key=_consultant_status_priority_v1)


def sync_articles_from_scholar(db: Session, run: ScholarRun) -> List[Article]:
    """
    Synchronise les articles du run courant sans perdre les décisions consultant.

    La décision est mémorisée par identité scientifique (DOI, URL, paper_id ou
    titre/année), indépendamment du run et du verrou. Ainsi, une nouvelle
    recherche peut recréer les lignes Article sans remettre un article déjà
    gardé ou rejeté à ``en_attente``.
    """
    report = _report_from_run(run)
    results = report.get("results") or []
    decision_memory = _consultant_project_decision_memory_v1(db, run)

    changed_or_created: List[Article] = []
    changed_objects = set()
    existing_rows = (
        db.query(Article)
        .filter(Article.scholar_run_id == run.id)
        .order_by(Article.id.asc())
        .all()
    )
    existing_by_identity = {}
    for existing in existing_rows:
        for identity_key in _consultant_model_identity_keys_v1(existing):
            existing_by_identity.setdefault(identity_key, existing)

    def mark_changed(article):
        marker = id(article)
        if marker not in changed_objects:
            changed_objects.add(marker)
            changed_or_created.append(article)

    def merge_coverage(existing_source, incoming_item):
        def coverage_score(value):
            try:
                return float(value or 0)
            except (TypeError, ValueError):
                return 0.0

        merged = {}
        candidates = list(existing_source.get("covered_verrous") or [])
        candidates.extend(incoming_item.get("covered_verrous") or [])
        incoming_source = _consultant_source_json_v1(incoming_item.get("source_json"))
        candidates.extend(incoming_source.get("covered_verrous") or [])
        for coverage in candidates:
            if not isinstance(coverage, dict):
                continue
            verrou_key = str(
                coverage.get("verrou_id")
                or coverage.get("verrou_number")
                or coverage.get("verrou_title")
                or ""
            ).strip().lower()
            if not verrou_key:
                continue
            old = merged.get(verrou_key)
            if old is None or coverage_score(coverage.get("relevance_score")) > coverage_score(old.get("relevance_score")):
                merged[verrou_key] = dict(coverage)
        return list(merged.values())

    for result in results:
        verrou_id = result.get("verrou_id")
        try:
            verrou_id_int = int(verrou_id) if verrou_id is not None else None
        except Exception:
            verrou_id_int = None

        verrou_decision = {
            "verrou_id": verrou_id_int,
            "verrou_title": result.get("verrou_title"),
            "scientific_decision": result.get("decision"),
            "scientific_support_score": result.get("scientific_support_score"),
            "rnd_uncertainty_score": result.get("rnd_uncertainty_score"),
            "engineering_only_risk": result.get("engineering_only_risk"),
            "gap_analysis": result.get("gap_analysis"),
            "consultant_action": result.get("consultant_action"),
        }

        for item in result.get("articles") or []:
            if not isinstance(item, dict):
                continue

            title = _get_title(item)
            if not title:
                continue

            identity_keys = _consultant_item_identity_keys_v1(item, title)
            article = next(
                (
                    existing_by_identity[identity_key]
                    for identity_key in sorted(identity_keys)
                    if identity_key in existing_by_identity
                ),
                None,
            )
            preserved_status = _consultant_preserved_status_v1(
                decision_memory,
                item,
                title,
            )

            if article is None:
                article = Article(
                    scholar_run_id=run.id,
                    verrou_id=verrou_id_int,
                    title=title,
                    year=_article_year(item),
                    source=_article_source(item),
                    tag_article=_article_tag(item),
                    score=_article_score(item),
                    url=_article_url(item),
                    doi=_article_doi(item),
                    consultant_status=preserved_status,
                    source_json={
                        **item,
                        "verrou_scientific_validation": verrou_decision,
                    },
                )
                db.add(article)
                mark_changed(article)
                for identity_key in identity_keys:
                    existing_by_identity[identity_key] = article
            else:
                source_json = dict(article.source_json or {})
                source_json["verrou_scientific_validation"] = verrou_decision
                coverage = merge_coverage(source_json, item)
                if coverage:
                    source_json["covered_verrous"] = coverage
                    source_json["multi_verrou_article"] = len(coverage) > 1
                    source_json["multi_verrou_count"] = len(coverage)
                    source_json["multi_verrou_policy"] = "globally_deduped_backend_guard"
                article.source_json = source_json
                current_status = _consultant_status_v1(article.consultant_status)
                if (
                    current_status == "en_attente"
                    and preserved_status != "en_attente"
                ):
                    article.consultant_status = preserved_status
                mark_changed(article)
                for identity_key in identity_keys:
                    existing_by_identity[identity_key] = article

    db.commit()

    for article in changed_or_created:
        db.refresh(article)

    return changed_or_created
