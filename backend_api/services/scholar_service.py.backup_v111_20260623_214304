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
        "non transférabilité",
        "non transferabilite",
        "cause racine",
        "performance insuffisante",
        "compromis entre contraintes",
        "comportement instable",
        "qualité de sortie",
        "qualite sortie",
        "fiabilité usure",
        "fiabilite usure",
        "maîtrise thermique",
        "maitrise thermique",
    ]
    return any(g in nt for g in generic)


def _build_enriched_scientific_profile(
    title: str,
    source_text: str,
    project_name: str = "",
    domain_label: str = "",
) -> Dict[str, Any]:
    """
    Reconstruit le vrai objet scientifique envoyé à EnnoScholar.
    Le titre Frascati générique reste visible, mais EnnoScholar reçoit :
    - un titre technique précis
    - un texte scientifique enrichi
    - des requêtes ciblées
    """
    combined = " ".join([title or "", source_text or "", project_name or "", domain_label or ""])
    nt = _norm_scholar_text(combined)

    project_hint = project_name or "TGM100"
    base_constraints = []
    if "300" in nt and "bar" in nt:
        base_constraints.append("300 bar")
    if "100" in nt and ("m3/h" in nt or "m³/h" in nt):
        base_constraints.append("100 m³/h")
    if "haute pression" in nt or "high pressure" in nt or "300 bar" in " ".join(base_constraints):
        base_constraints.append("high pressure")
    constraints_text = ", ".join(dict.fromkeys(base_constraints))

    # Soufflage carter / segments
    if _has_any(nt, ["soufflage", "carter", "reniflard", "segments", "segment", "piston", "huile", "blow-by", "étanchéité", "etancheite"]):
        enriched_title = (
            "Maîtrise du soufflage carter lié à l’usure des segments et à l’étanchéité "
            f"du compresseur haute pression {project_hint}"
        )
        scientific_text = (
            f"{enriched_title}. Le verrou porte sur le phénomène de blow-by dans un compresseur "
            "alternatif haute pression : fuite d'air et d'huile vers le carter, pression carter, "
            "remontée par le reniflard, usure des segments, étanchéité piston/cylindre, dégradation "
            "en fonctionnement et stabilité de la segmentation sous conditions réelles. "
            f"Contraintes projet : {constraints_text}. "
            f"Indices sources : {source_text[:1800]}"
        )
        queries = [
            "reciprocating compressor piston rings blow-by leakage crankcase pressure",
            "high pressure compressor piston ring wear sealing oil carryover",
            "piston rings blow-by leakage crankcase ventilation reciprocating compressor",
            "compressor cylinder piston ring wear leakage high pressure",
            "oil carryover crankcase blow-by piston rings compressor",
        ]
        return {
            "enriched_title": enriched_title,
            "scientific_text": scientific_text,
            "suggested_queries": queries,
            "profile": "blowby_segments_crankcase",
        }

    # Thermique / refroidissement / réfrigérant
    if _has_any(nt, ["thermique", "refroidissement", "température", "temperature", "réfrigérant", "refrigerant", "débit d'eau", "debit d eau", "eau", "cooling", "intercooler", "heat exchanger"]):
        enriched_title = (
            "Maîtrise du refroidissement du premier étage d’un compresseur haute pression "
            f"{project_hint} sous variation du débit d’eau"
        )
        scientific_text = (
            f"{enriched_title}. Le verrou porte sur le dimensionnement et le comportement thermique "
            "du refroidissement/intercooler du premier étage d’un compresseur haute pression : "
            "température de sortie, débit d’eau, échange thermique, stabilité thermique, dissipation "
            "de chaleur, influence du sens de circulation et conditions réelles de compression. "
            f"Contraintes projet : {constraints_text}. "
            f"Indices sources : {source_text[:1800]}"
        )
        queries = [
            "high pressure reciprocating compressor intercooler water cooling temperature",
            "reciprocating compressor first stage cooling water flow rate outlet temperature",
            "compressor intercooler thermal management high pressure air compression",
            "heat transfer intercooler reciprocating compressor water flow",
            "compressed air temperature control high pressure compressor cooling",
        ]
        return {
            "enriched_title": enriched_title,
            "scientific_text": scientific_text,
            "suggested_queries": queries,
            "profile": "thermal_cooling_intercooler",
        }

    # Vibro-acoustique
    if _has_any(nt, ["vibration", "vibro", "acoustique", "bruit", "noise", "aspiration", "silencieux", "déportée", "deportee"]):
        enriched_title = (
            "Maîtrise du comportement vibro-acoustique d’un compresseur haute pression "
            f"{project_hint} en conditions de fonctionnement"
        )
        scientific_text = (
            f"{enriched_title}. Le verrou porte sur les vibrations, le bruit d’aspiration, "
            "l’acoustique du compresseur, l’influence de l’aspiration déportée, la propagation "
            "du bruit et la stabilité dynamique sous vitesse/pression de fonctionnement. "
            f"Contraintes projet : {constraints_text}. "
            f"Indices sources : {source_text[:1800]}"
        )
        queries = [
            "reciprocating compressor vibration acoustic noise suction pulsation",
            "compressor intake noise vibration high pressure reciprocating compressor",
            "pulsation noise suction line reciprocating compressor acoustic",
            "vibro acoustic behavior reciprocating compressor suction",
        ]
        return {
            "enriched_title": enriched_title,
            "scientific_text": scientific_text,
            "suggested_queries": queries,
            "profile": "vibro_acoustic_compressor",
        }

    # Air sec / condensats
    if _has_any(nt, ["air sec", "séchage", "sechage", "condensat", "condensats", "point de rosée", "rosee", "dew point", "séparateur", "separateur"]):
        enriched_title = (
            "Maîtrise de la production d’un air sec conforme en sortie de compresseur haute pression "
            f"{project_hint}"
        )
        scientific_text = (
            f"{enriched_title}. Le verrou porte sur la séparation des condensats, le séchage de l’air "
            "comprimé, la maîtrise du point de rosée, la qualité de sortie et la conformité de l’air "
            "produit après compression haute pression. "
            f"Contraintes projet : {constraints_text}. "
            f"Indices sources : {source_text[:1800]}"
        )
        queries = [
            "compressed air drying dew point high pressure compressor condensate separation",
            "high pressure compressed air moisture removal condensate separator",
            "compressed air quality dew point condensate high pressure compressor",
        ]
        return {
            "enriched_title": enriched_title,
            "scientific_text": scientific_text,
            "suggested_queries": queries,
            "profile": "compressed_air_drying",
        }

    # Contrepoids / équilibrage
    if _has_any(nt, ["contrepoids", "équilibrage", "equilibrage", "balourd", "plomb", "masse", "dynamique"]):
        enriched_title = (
            "Conception d’un contrepoids sans plomb compatible avec les contraintes "
            f"d’équilibrage dynamique du compresseur {project_hint}"
        )
        scientific_text = (
            f"{enriched_title}. Le verrou porte sur l’équilibrage statique et dynamique, "
            "la substitution du plomb, la masse du contrepoids, le balourd, les contraintes "
            "de vibration et la compatibilité mécanique en fonctionnement. "
            f"Contraintes projet : {constraints_text}. "
            f"Indices sources : {source_text[:1800]}"
        )
        queries = [
            "dynamic balancing counterweight lead free rotating machinery vibration",
            "counterweight mass balancing compressor vibration lead replacement",
            "rotating machinery dynamic balancing counterweight design",
        ]
        return {
            "enriched_title": enriched_title,
            "scientific_text": scientific_text,
            "suggested_queries": queries,
            "profile": "counterweight_dynamic_balancing",
        }

    # Fallback générique enrichi par source
    clean_src = source_text[:2200]
    enriched_title = title
    if _generic_title(title) and clean_src:
        # prendre une phrase technique source plutôt que le thème Frascati
        first_sentence = re.split(r"(?<=[.!?])\s+", clean_src)[0].strip()
        if len(first_sentence) > 30:
            enriched_title = first_sentence[:180]

    scientific_text = (
        f"{enriched_title}. Verrou scientifique reconstruit à partir des preuves techniques sources. "
        f"Domaine : {domain_label}. Projet : {project_hint}. Indices sources : {clean_src}"
    )
    return {
        "enriched_title": enriched_title,
        "scientific_text": scientific_text,
        "suggested_queries": [],
        "profile": "generic_source_enriched",
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
            },
        },
    })

def build_scholar_payload_from_selected_verrous(db: Session, project: Project, max_verrous: int = 8) -> Dict[str, Any]:
    selected = get_selected_verrous_for_scholar(db, project)
    selected = selected[:max_verrous]

    nlp_path = current_nlp_result_path(project)
    nlp = read_json(nlp_path, {}) if nlp_path else {}
    domain_detection = _extract_domain_detection(nlp)

    diagnostic_context = extract_diagnostic_context_from_report(project)

    return sanitize_json_value({
        "organisme": project.organisme,
        "project": project.project_name,
        "year": str(project.year),
        "source": "consultant_selected_verrous_from_ennodiagnostic",
        "selection_rule": "Only verrous with consultant_status='garde' are sent to EnnoScholar.",
        "selected_verrous_count": len(selected),
        "input_nlp_result": str(nlp_path) if nlp_path else "",
        "diagnostic_context": diagnostic_context,
        "domain_detection": domain_detection,
        "verrous": [
            verrou_to_scholar_payload(
                v,
                project_name=project.project_name,
                domain_label=project.domain_label or "",
            )
            for v in selected
        ],
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

    return sanitize_json_value({
        "ok": True,
        "verrous_analyzed": len(results),
        "decision_counts": decisions,
        "verrous_scientifiquement_defendables": defensible,
        "verrous_a_confirmer": confirm,
        "support_scientifique_faible": weak,
        "aucun_article_trouve": none,
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
    limit_per_query: int = 3,
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
    report["input_payload"] = str(paths["payload"])
    report["outputs"] = {
        "payload": str(paths["payload"]),
        "report": str(paths["report"]),
        "summary": str(paths["summary"]),
    }
    report["selection"] = {
        "selected_verrou_ids": [v.id for v in selected_verrous[:max_verrous]],
        "selected_verrous_count": len(selected_verrous[:max_verrous]),
        "rule": "consultant_status == garde",
    }

    summary = build_scholar_summary(report)

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
        },
        completed_at=datetime.utcnow(),
    )

    db.add(run)
    project.status = "EnnoScholar terminé"
    db.commit()
    db.refresh(run)

    # Synchroniser directement les articles pour que le frontend les voie.
    sync_articles_from_scholar(db, run)

    return run


def run_ennoscholar(db: Session, project: Project) -> ScholarRun:
    offline = os.getenv("ENNOSCHOLAR_OFFLINE_DRY_RUN", "0").strip() == "1"
    max_verrous = int(os.getenv("ENNOSCHOLAR_MAX_VERROUS", "8"))
    limit = int(os.getenv("ENNOSCHOLAR_LIMIT_PER_QUERY", "3"))

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


def sync_articles_from_scholar(db: Session, run: ScholarRun) -> List[Article]:
    """
    Synchronise les articles avec lien article -> verrou_id.
    """
    report = _report_from_run(run)
    results = report.get("results") or []

    changed_or_created: List[Article] = []
    existing_by_key = {
        (a.title.lower().strip(), a.verrou_id): a
        for a in db.query(Article).filter(Article.scholar_run_id == run.id).all()
    }

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

            key = (title.lower().strip(), verrou_id_int)
            article = existing_by_key.get(key)

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
                    source_json={**item, "verrou_scientific_validation": verrou_decision},
                )
                db.add(article)
                changed_or_created.append(article)
                existing_by_key[key] = article
            else:
                article.source_json = article.source_json or {}
                article.source_json["verrou_scientific_validation"] = verrou_decision
                changed_or_created.append(article)

    db.commit()

    for article in changed_or_created:
        db.refresh(article)

    return changed_or_created
