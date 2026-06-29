# -*- coding: utf-8 -*-
from __future__ import annotations

"""
CIR_MEMORY V68.1 - comparaison CIR précédent segmentée + scoring calibré + filtre anti-bruit

Corrections par rapport V2 :
1) CIR final mémoire = SANS FrascatiGuard, sections structurées uniquement.
2) Correction ProjectStore : compatible avec signatures organisme/project OU organisme_id/project_id.
3) Comparaison :
   - les supporting_passages ne deviennent plus des verrous séparés.
   - current_items_count baisse.
   - verrou_count correspond aux verrous principaux seulement.
4) Pour le CIR final, les sections larges sont gardées comme sections, mais la comparaison
   utilise aussi section_title + section_type + texte pour matcher.
"""

import json
import re
from pathlib import Path
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple


BASE_DIR = Path(r"C:\EnnoSmart")
STORAGE_DIR = BASE_DIR / "storage" / "organismes"
OUTPUTS_DIR = BASE_DIR / "outputs" / "safe_rag_upload"
EXPERIENCE_MEMORY_V2_DIR = BASE_DIR / "storage" / "experience_memory_v2"

PACK_KEYS = [
    "objectifs_locaux",
    "verrous_rnd_locaux",
    "methodes_locales",
    "resultats_locaux",
    "limites_locales",
    "contributions_locales",
    "etat_art_local",
    "parametres_locaux",
]

ROLE_BY_PACK = {
    "objectifs_locaux": "objectif",
    "verrous_rnd_locaux": "verrou",
    "methodes_locales": "methode",
    "resultats_locaux": "resultat",
    "limites_locales": "limite",
    "contributions_locales": "contribution",
    "etat_art_local": "etat_art",
    "parametres_locaux": "parametre",
}

STOP = {
    "avec", "dans", "pour", "plus", "moins", "entre", "comme", "cette",
    "cela", "ainsi", "afin", "etre", "être", "sont", "nous", "notre",
    "leur", "leurs", "des", "les", "une", "aux", "sur", "par", "que",
    "qui", "quoi", "dont", "de", "du", "la", "le", "un", "en", "et",
    "ou", "au", "ce", "ces", "son", "ses", "projet", "systeme", "système",
    "verrou", "possible", "question", "qualification", "documents", "concernés",
    "concernes", "partir", "indices", "dispersés", "disperses", "consultant",
}

TECH_THEMES = {
    "performance_pression_debit": [
        "performance", "débit", "debit", "pression", "300", "400", "bar", "m3/h",
        "haute pression", "refoulement", "rendement", "atteindre", "compression",
    ],
    "vibration_acoustique": [
        "vibration", "vibratoire", "acoustique", "bruit", "sonore", "silencieux",
        "aspiration", "équilibrage", "equilibrage", "contrepoids", "moteur",
        "signature vibratoire", "fréquence", "frequence", "hz", "rpm", "poulie",
    ],
    "thermique_refroidissement": [
        "thermique", "température", "temperature", "refroidissement", "réfrigérant",
        "refrigerant", "échauffement", "echauffement", "chaleur", "eau",
        "circuit d'eau", "inter-étage", "inter etage", "étage", "etage", "tubes",
    ],
    "qualite_air_sechage": [
        "air sec", "humidité", "humidite", "rosée", "rosee", "point de rosée",
        "point de rosee", "sécheur", "secheur", "membrane", "condensat",
        "condensats", "huile", "eau", "filtre", "purge", "qualité de l'air",
    ],
    "usure_fiabilite_etancheite": [
        "usure", "fiabilité", "fiabilite", "résistance", "resistance",
        "étanchéité", "etancheite", "segment", "segmentation", "piston",
        "chemise", "rotule", "bielle", "reniflard", "fuite", "huile",
        "flambage", "transformateur", "soufflage carter", "carter",
    ],
    "cause_racine_essais": [
        "cause", "racine", "identifier", "identification", "analyse",
        "essai", "essais", "test", "mesure", "relevé", "releve",
        "prototype", "validation", "comparaison", "simulation", "calcul",
        "modélisation", "modelisation", "microscopie", "dureté", "durete",
    ],
    "compromis_contraintes": [
        "compromis", "contrainte", "contraintes", "exigence", "exigences",
        "sous-marin", "sous marin", "compact", "encombrement", "débit",
        "pression", "bruit", "température", "simultanément", "simultanement",
    ],
    "etat_art_non_transferable": [
        "état de l'art", "etat de l'art", "connaissances existantes",
        "solutions existantes", "insuffisances", "insuffisance", "bibliographie",
        "littérature", "litterature", "non transposable", "non transférable",
        "non transferable", "architecture", "barillet",
    ],
}


def slug(x: Any) -> str:
    x = str(x or "").strip().lower()
    x = re.sub(r"[^\w\-]+", "_", x, flags=re.UNICODE)
    x = re.sub(r"_+", "_", x).strip("_")
    return x or "unknown"


def clean_text(x: Any) -> str:
    x = str(x or "")
    x = x.replace("\r\n", "\n").replace("\r", "\n")
    x = re.sub(r"[ \t]+", " ", x)
    x = re.sub(r"\n{3,}", "\n\n", x)
    return x.strip()


def norm(x: Any) -> str:
    x = clean_text(x).lower()
    tr = str.maketrans("àâäéèêëîïôöùûüç’", "aaaeeeeiioouuuc'")
    x = x.translate(tr)
    x = re.sub(r"[^\w%/.,\-]+", " ", x)
    x = re.sub(r"\s+", " ", x)
    return x.strip()


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


def year_dir(organisme: str, project: str, year: str) -> Path:
    return STORAGE_DIR / slug(organisme) / "projects" / slug(project) / "years" / str(year)


def cir_final_dir(organisme: str, project: str, year: str) -> Path:
    return year_dir(organisme, project, year) / "cir_final"


def cir_final_report_path(organisme: str, project: str, year: str) -> Path:
    return cir_final_dir(organisme, project, year) / "cir_final_extracted.json"


def current_nlp_default_path(organisme: str, project: str, year: str) -> Path:
    return OUTPUTS_DIR / organisme / project / str(year) / "nlp_result.json"


def comparison_report_path(organisme: str, project: str, year: str) -> Path:
    return year_dir(organisme, project, year) / "cir_memory" / "cir_memory_comparison_report.json"


def _safe_pack(pack: Any) -> Dict[str, List[Dict[str, Any]]]:
    out = {k: [] for k in PACK_KEYS}
    if isinstance(pack, dict):
        for k in PACK_KEYS:
            arr = pack.get(k)
            if isinstance(arr, list):
                out[k] = [x for x in arr if isinstance(x, dict)]
    return out


def get_cir_structured_pack_without_frascati(nlp: Dict[str, Any]) -> Tuple[Dict[str, List[Dict[str, Any]]], str]:
    """
    CIR final mémoire :
    PAS de FrascatiGuard.
    On garde les sections CIR structurées comme elles sont.
    """
    if not isinstance(nlp, dict):
        return _safe_pack({}), "empty"

    cir = nlp.get("cir_structured_result")
    if isinstance(cir, dict):
        pack = cir.get("evidence_pack_before_frascati")
        if isinstance(pack, dict):
            return _safe_pack(pack), "cir_structured_result.evidence_pack_before_frascati"

    if nlp.get("pipeline_type") == "cir_structured":
        pack = nlp.get("evidence_pack_before_frascati")
        if isinstance(pack, dict):
            return _safe_pack(pack), "top_level_cir_structured.evidence_pack_before_frascati"

    pack = nlp.get("merged_evidence_pack_before_frascati")
    if isinstance(pack, dict):
        return _safe_pack(pack), "merged_evidence_pack_before_frascati"

    pack = nlp.get("evidence_pack_before_frascati")
    if isinstance(pack, dict):
        return _safe_pack(pack), "top_level.evidence_pack_before_frascati"

    return _safe_pack({}), "not_found"


def get_current_raw_pack_with_frascati(nlp: Dict[str, Any]) -> Tuple[Dict[str, List[Dict[str, Any]]], str]:
    """
    Bruts de l'année N :
    FrascatiGuard autorisé.
    Mais on récupère seulement les items principaux.
    Les supporting_passages restent dans l'item, ils ne deviennent pas des verrous séparés.
    """
    if not isinstance(nlp, dict):
        return _safe_pack({}), "empty"

    fg = nlp.get("frascati_guard")
    if isinstance(fg, dict):
        pack = fg.get("qualified_pack_for_ennodiagnostic")
        if isinstance(pack, dict):
            return _safe_pack(pack), "frascati_guard.qualified_pack_for_ennodiagnostic"

    for key in [
        "multi_document_evidence_pack_for_ennodiagnostic",
        "merged_evidence_pack_for_ennodiagnostic",
        "evidence_pack_for_ennodiagnostic",
        "merged_evidence_pack_before_frascati",
        "evidence_pack_before_frascati",
    ]:
        pack = nlp.get(key)
        if isinstance(pack, dict):
            return _safe_pack(pack), key

    return _safe_pack({}), "not_found"


def item_text(item: Dict[str, Any]) -> str:
    title = clean_text(item.get("section_title") or item.get("title") or "")
    label = clean_text(item.get("section_label") or "")
    text = clean_text(item.get("text") or item.get("source_text") or "")
    parts = []
    if label:
        parts.append(label)
    if title and norm(title) not in norm(text[:250]):
        parts.append(title)
    parts.append(text)
    return "\n".join([p for p in parts if p]).strip()


def pack_to_items(pack: Dict[str, Any], source_type: str) -> List[Dict[str, Any]]:
    """
    IMPORTANT V3 :
    Ne crée PAS un item pour chaque supporting_passage.
    Sinon un seul verrou générique avec 6 preuves devient 7 verrous.
    """
    out: List[Dict[str, Any]] = []
    seen = set()

    for pack_key in PACK_KEYS:
        role = ROLE_BY_PACK.get(pack_key, "general")
        for idx, item in enumerate(pack.get(pack_key) or []):
            if not isinstance(item, dict):
                continue

            txt = item_text(item)
            if len(txt) < 35:
                continue

            doc = str(item.get("document") or item.get("file_name") or "")
            sec_title = str(item.get("section_title") or item.get("title") or "")
            sec_type = str(item.get("section_type") or "")
            passage_id = str(item.get("passage_id") or item.get("id") or f"{pack_key}_{idx}")

            key = (role, doc, sec_title, norm(txt)[:220])
            if key in seen:
                continue
            seen.add(key)

            out.append({
                "id": passage_id,
                "role": role,
                "pack_key": pack_key,
                "text": txt,
                "document": doc,
                "section_title": sec_title,
                "section_type": sec_type,
                "section_label": item.get("section_label"),
                "source_path": item.get("source_path"),
                "source_type": source_type,
                "content_origin": item.get("content_origin"),
                "quality_status": item.get("quality_status"),
                "frascati_decision": (item.get("frascati") or {}).get("decision") or item.get("frascati_decision"),
                "frascati_score": (item.get("frascati") or {}).get("frascati_score") or item.get("frascati_score"),
                "theme_id": item.get("theme_id"),
                "theme_label": item.get("theme_label"),
                "supporting_passages": filter_supporting_passages(item.get("supporting_passages") or []),
            })

    return out


def roles_count(items: List[Dict[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for x in items:
        r = str(x.get("role") or "unknown")
        out[r] = out.get(r, 0) + 1
    return dict(sorted(out.items()))


def _make_project_store(organisme: str, project: str, year: str):
    from modules.RAG.project_store import ProjectStore
    try:
        return ProjectStore(organisme=organisme, project=project, year=year)
    except TypeError:
        try:
            return ProjectStore(organisme_id=organisme, project_id=project, year=year)
        except TypeError:
            return ProjectStore(organisme, project, year)


def register_final_cir_nlp_result_in_chroma(
    organisme: str,
    project: str,
    year: str,
    cir_final: str | Path,
    nlp_result_path: str | Path,
) -> Dict[str, Any]:
    nlp_path = Path(nlp_result_path)
    if not nlp_path.exists():
        raise FileNotFoundError(f"NLP result du CIR final introuvable : {nlp_path}")

    nlp = read_json(nlp_path, {})
    pack, pack_source = get_cir_structured_pack_without_frascati(nlp)
    items = pack_to_items(pack, source_type="cir_final_structured_without_frascati")

    if not items:
        raise RuntimeError(
            "Aucun item CIR structuré trouvé sans Frascati. "
            "Vérifie que le nlp_result contient cir_structured_result.evidence_pack_before_frascati."
        )

    report = {
        "ok": True,
        "version": "cir_memory_v68_1_cir_without_frascati",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "organisme": organisme,
        "project": project,
        "year": str(year),
        "cir_final_file": str(cir_final),
        "nlp_result_path": str(nlp_path),
        "rule": "CIR final mémoire = sections CIR structurées AVANT FrascatiGuard",
        "pack_source": pack_source,
        "items_count": len(items),
        "roles": roles_count(items),
        "items": items,
    }

    out_path = cir_final_report_path(organisme, project, year)
    write_json(out_path, report)

    chroma_info = {"attempted": False}
    try:
        from modules.RAG.vector_store import RAGVectorStore
        from modules.RAG.json_to_chunks import nlp_json_to_chunks

        ps = _make_project_store(organisme, project, year)
        if hasattr(ps, "ensure"):
            ps.ensure()

        pseudo_nlp = {
            "version": "cir_memory_v68_1_cir_without_frascati",
            "pipeline_type": "cir_final_memory_without_frascati",
            "evidence_pack_before_frascati": pack,
            "evidence_pack_for_ennodiagnostic": pack,
        }
        try:
            chunks = nlp_json_to_chunks(project_id=project, nlp_result=pseudo_nlp)
        except TypeError:
            # Compatibilité avec anciennes signatures éventuelles.
            try:
                chunks = nlp_json_to_chunks(project, pseudo_nlp)
            except TypeError:
                chunks = nlp_json_to_chunks(pseudo_nlp)
        collection_name = f"ennosmart_{slug(organisme)}_{slug(project)}_{year}_cir_final"

        chroma_dir = getattr(ps, "chroma_dir", None)
        if chroma_dir is None:
            chroma_dir = year_dir(organisme, project, year) / "rag" / "chroma"

        vs = RAGVectorStore(chroma_dir)
        try:
            vs.add_chunks(collection_name=collection_name, chunks=chunks)
        except TypeError:
            vs.add_chunks(chunks)

        chroma_info = {
            "attempted": True,
            "ok": True,
            "collection": collection_name,
            "chroma_dir": str(chroma_dir),
            "chunks_indexed": len(chunks),
        }
    except Exception as e:
        chroma_info = {
            "attempted": True,
            "ok": False,
            "error": str(e),
            "note": "La mémoire JSON est créée même si l'index Chroma échoue.",
        }

    report["chroma"] = chroma_info
    write_json(out_path, report)
    return report


def words(text: str, limit: int = 80) -> List[str]:
    ws = re.findall(r"\b[\wÀ-ÿ'-]{4,}\b", norm(text))
    ws = [w for w in ws if w not in STOP]
    freq = {}
    for w in ws:
        freq[w] = freq.get(w, 0) + 1
    return [w for w, _ in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]


def jaccard(a: List[str], b: List[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def numbers(text: str) -> List[str]:
    return re.findall(r"\b\d+(?:[,.]\d+)?\s*(?:bar|bars|kg|mm|°c|db|hz|rpm|m3/h|%)?\b", norm(text))


def themes(text: str) -> List[str]:
    low = norm(text)
    found = []
    for th, kws in TECH_THEMES.items():
        if any(norm(k) in low for k in kws):
            found.append(th)
    return found


def expanded_current_text(item: Dict[str, Any]) -> str:
    """
    Pour un verrou brut Frascati, on ajoute les preuves support dans le texte de scoring,
    mais pas comme verrous séparés.
    """
    parts = [item.get("text") or ""]
    for sp in item.get("supporting_passages") or []:
        if isinstance(sp, dict):
            parts.append(sp.get("text") or "")
    return "\n".join([clean_text(p) for p in parts if clean_text(p)])



def is_universal_implicit_current(current: Dict[str, Any]) -> bool:
    cid = norm(current.get("id") or "")
    text = norm(current.get("text") or "")
    quality = norm(current.get("quality_status") or "")
    return (
        cid.startswith("implicit_universal")
        or "frascati_universal" in quality
        or text.startswith("verrou implicite possible")
    )


def is_previous_broad_segment(previous: Dict[str, Any], previous_themes: List[str]) -> bool:
    text = previous.get("text") or ""
    low = norm(text)
    if len(previous_themes) >= 5:
        return True
    if len(text) > 650 and len(previous_themes) >= 4:
        return True
    if re.search(r"processus de compression.*plusieurs problematiques|ensemble de ces performances|parametres du compresseur", low):
        return True
    return False


def current_counterweight_mismatch(current_text: str, previous_text: str) -> bool:
    c = norm(current_text)
    p = norm(previous_text)
    if not re.search(r"contrepoids|masselotte|equilibr|plomb|fonte", c):
        return False
    # Pour un passage contrepoids, le CIR précédent doit parler au moins de vibration/équilibrage/résistance/compromis mécanique.
    return not re.search(r"contrepoids|masselotte|equilibr|plomb|forces d inertie|vibration|vibratoire|resistance mecanique|compromis", p)


def current_refrigerant_mismatch(current_text: str, previous_text: str) -> bool:
    c = norm(current_text)
    p = norm(previous_text)
    if not re.search(r"refrigerant|refroidissement|temperature|debit d eau|tube|100bar", c):
        return False
    return not re.search(r"refrigerant|refroidissement|temperature|echauffement|eau liquide|secheur|condensat|pression", p)


def current_acoustic_mismatch(current_text: str, previous_text: str) -> bool:
    c = norm(current_text)
    p = norm(previous_text)
    if not re.search(r"aspiration|acoustique|bruit|gaine|insonorise|silencieux", c):
        return False
    return not re.search(r"aspiration|acoustique|bruit|nuisance sonore|silencieux|vibration|vibratoire", p)


def calibration_cap_and_penalty(current: Dict[str, Any], previous: Dict[str, Any], details: Dict[str, Any]) -> Tuple[float, Optional[float], List[str]]:
    ct = expanded_current_text(current)
    pt = previous.get("text", "")
    kw = float(details.get("keyword_jaccard") or 0.0)
    seq = float(details.get("sequence") or 0.0)
    theme_score = float(details.get("theme_score") or 0.0)
    number_score = float(details.get("number_score") or 0.0)
    current_themes = details.get("current_themes") or []
    previous_themes = details.get("previous_themes") or []
    shared = details.get("shared_themes") or []

    penalty = 0.0
    cap: Optional[float] = None
    reasons: List[str] = []

    universal = is_universal_implicit_current(current)
    broad_prev = is_previous_broad_segment(previous, previous_themes)

    if universal:
        penalty += 0.10
        cap = 0.58 if cap is None else min(cap, 0.58)
        reasons.append("current_universal_implicit_weight_reduced")

    if broad_prev:
        penalty += 0.08
        cap = 0.62 if cap is None else min(cap, 0.62)
        reasons.append("previous_segment_too_broad")

    if kw < 0.03 and number_score == 0 and seq < 0.12:
        cap = 0.64 if cap is None else min(cap, 0.64)
        reasons.append("low_keyword_overlap_no_number_match")

    # V68.1 : les objectifs/propositions faibles ne doivent pas devenir continuité forte
    # si le match repose surtout sur des thèmes larges.
    current_role = str(current.get("role") or "")
    if current_role != "verrou" and kw < 0.04 and seq < 0.08 and number_score == 0:
        penalty += 0.04
        cap = 0.60 if cap is None else min(cap, 0.60)
        reasons.append("non_verrou_low_direct_evidence")

    if len(current_themes) >= 6:
        penalty += 0.07
        cap = 0.60 if cap is None else min(cap, 0.60)
        reasons.append("current_item_too_broad_many_themes")

    if len(previous_themes) >= 6:
        penalty += 0.06
        cap = 0.64 if cap is None else min(cap, 0.64)
        reasons.append("previous_item_too_broad_many_themes")

    if current_counterweight_mismatch(ct, pt):
        penalty += 0.28
        cap = 0.42 if cap is None else min(cap, 0.42)
        reasons.append("counterweight_specific_mismatch")

    if current_refrigerant_mismatch(ct, pt):
        penalty += 0.15
        cap = 0.55 if cap is None else min(cap, 0.55)
        reasons.append("refrigerant_specific_mismatch")

    if current_acoustic_mismatch(ct, pt):
        penalty += 0.15
        cap = 0.55 if cap is None else min(cap, 0.55)
        reasons.append("acoustic_specific_mismatch")

    # Si le score vient presque uniquement du thème, on évite le faux "fort".
    if theme_score >= 0.75 and kw < 0.025 and seq < 0.05 and len(shared) <= 3:
        penalty += 0.08
        cap = 0.56 if cap is None else min(cap, 0.56)
        reasons.append("theme_only_similarity")

    return penalty, cap, reasons


def score_pair(current: Dict[str, Any], previous: Dict[str, Any]) -> Dict[str, Any]:
    ct = expanded_current_text(current)
    pt = previous.get("text", "")

    seq = SequenceMatcher(None, norm(ct)[:2500], norm(pt)[:2500]).ratio()

    cw = [w for w in words(ct, 80) if not is_too_generic_keyword(w)]
    pw = [w for w in words(pt, 80) if not is_too_generic_keyword(w)]
    kw = jaccard(cw, pw)

    th_c, th_p = themes(ct), themes(pt)
    shared = sorted(set(th_c) & set(th_p))
    theme_score = len(shared) / max(1, len(set(th_c) | set(th_p))) if (th_c or th_p) else 0.0

    num_c, num_p = set(numbers(ct)), set(numbers(pt))
    number_score = len(num_c & num_p) / max(1, len(num_c | num_p)) if (num_c or num_p) else 0.0

    same_role = current.get("role") == previous.get("role")
    role_bonus = 0.08 if same_role else 0.0
    if current.get("role") == "verrou" and previous.get("role") in {"verrou", "limite", "etat_art", "objectif"}:
        role_bonus += 0.04

    prev_priority_bonus = min(0.08, float(previous.get("previous_section_priority") or 0) / 1000.0)
    specific_bonus = specific_pair_bonus(ct, pt, th_c, th_p)
    generic_penalty = previous_candidate_penalty(pt)

    raw_score = (
        0.12 * seq
        + 0.22 * kw
        + 0.38 * theme_score
        + 0.06 * number_score
        + role_bonus
        + prev_priority_bonus
        + specific_bonus
        - generic_penalty
    )

    details = {
        "score_raw_before_calibration": round(max(0.0, min(1.0, raw_score)), 4),
        "sequence": round(seq, 4),
        "keyword_jaccard": round(kw, 4),
        "theme_score": round(theme_score, 4),
        "number_score": round(number_score, 4),
        "role_bonus": round(role_bonus, 4),
        "prev_relevant_bonus": round(prev_priority_bonus, 4),
        "specific_bonus": round(specific_bonus, 4),
        "generic_penalty": round(generic_penalty, 4),
        "current_themes": th_c,
        "previous_themes": th_p,
        "shared_themes": shared,
        "current_keywords": cw[:20],
        "previous_keywords": pw[:20],
        "current_numbers": sorted(num_c),
        "previous_numbers": sorted(num_p),
        "current_is_universal_implicit": is_universal_implicit_current(current),
        "previous_is_broad_segment": is_previous_broad_segment(previous, th_p),
    }

    calibration_penalty, score_cap, reasons = calibration_cap_and_penalty(current, previous, details)
    score = raw_score - calibration_penalty
    if score_cap is not None:
        score = min(score, score_cap)
    score = max(0.0, min(1.0, score))

    details.update({
        "score": round(score, 4),
        "calibration_penalty": round(calibration_penalty, 4),
        "score_cap": round(score_cap, 4) if score_cap is not None else None,
        "score_cap_reasons": reasons,
    })
    return details


def decision_from_score(score: float, details: Dict[str, Any]) -> Dict[str, Any]:
    shared_themes = details.get("shared_themes") or []
    keyword_jaccard = float(details.get("keyword_jaccard") or 0.0)
    sequence = float(details.get("sequence") or 0.0)
    number_score = float(details.get("number_score") or 0.0)
    cap_reasons = details.get("score_cap_reasons") or []
    universal = bool(details.get("current_is_universal_implicit"))
    broad_previous = bool(details.get("previous_is_broad_segment"))

    strong_blockers = {
        "current_universal_implicit_weight_reduced",
        "previous_segment_too_broad",
        "current_item_too_broad_many_themes",
        "previous_item_too_broad_many_themes",
        "counterweight_specific_mismatch",
        "refrigerant_specific_mismatch",
        "acoustic_specific_mismatch",
        "theme_only_similarity",
        "non_verrou_low_direct_evidence",
    }
    has_strong_blocker = any(r in strong_blockers for r in cap_reasons) or universal or broad_previous

    enough_direct_evidence = (
        keyword_jaccard >= 0.035
        or sequence >= 0.18
        or number_score >= 0.20
    )

    if score >= 0.72 and len(shared_themes) >= 2 and enough_direct_evidence and not has_strong_blocker:
        status = "continuity_strong"
        label = "Continuité forte avec le CIR précédent"
    elif score >= 0.42 or (score >= 0.32 and shared_themes):
        status = "evolution_or_partial_continuity"
        label = "Évolution ou continuité partielle à vérifier"
    else:
        status = "new_or_not_found"
        label = "Nouveauté potentielle ou non retrouvée dans le CIR précédent"

    # Le score de continuité reste le score calibré : on ne le remonte pas artificiellement à 0.70.
    continuity = round(max(0.0, min(1.0, score)), 4)
    novelty = round(1.0 - continuity, 4)

    return {
        "status": status,
        "label": label,
        "continuity_score": continuity,
        "novelty_score": novelty,
        "calibration": {
            "keyword_jaccard": round(keyword_jaccard, 4),
            "sequence": round(sequence, 4),
            "shared_themes_count": len(shared_themes),
            "current_is_universal_implicit": universal,
            "previous_is_broad_segment": broad_previous,
            "score_cap_reasons": cap_reasons,
        },
    }



GENERIC_PREVIOUS_PATTERNS = [
    "nous devons donc developper des solutions techniques nouvelles",
    "ainsi le dispositif du module de compression etant un systeme complexe",
    "necessaire a chaque nouvelle implementation",
    "realiser une analyse mecanique fine",
    "consequences de cette implementation",
    "obtention des parametres du compresseur",
]

CURRENT_NOISE_PATTERNS = [
    r"telephone|téléphone",
    r"urban[- ]valley",
    r"chemin du bas des indes",
    r"cormeilles[- ]en[- ]parisis",
    r"written by|redige|rédigé|date modification",
    r"mann hummel.*telephone",
    r"\bape\s*\d{3,4}\s*[a-z]\b",
    r"\brcs\b|\bsiret\b|\bsiren\b|\btva\b",
    r"\bfr\s*\d{8,}\b",
    r"page\s+\d+\s+sur\s+\d+",
    r"révision\s+[a-z]|revision\s+[a-z]|maj\s+mati[eè]re|m[aà]j",
    r"^\s*[a-z]\s*\|.*\d{2}/\d{2}/\d{4}",
]

GENERIC_KEYWORDS = {
    "compresseur", "compresseurs", "solution", "solutions", "technique", "techniques",
    "developper", "developpement", "travaux", "projet", "objectif", "objectifs",
    "parametre", "parametres", "dispositif", "mecanique", "ensemble", "ainsi", "donc",
    "permettant", "atteindre", "performance", "performances",
    "mann", "hummel", "europiclon", "urban", "valley", "france", "telephone",
    "date", "modification", "redige", "written", "chemin", "indes", "cormeilles",
}


def is_generic_previous_text(text: str) -> bool:
    low = norm(text)
    return any(p in low for p in GENERIC_PREVIOUS_PATTERNS)


def is_current_noise_text(text: str) -> bool:
    """
    V68.1 : filtre bruit renforcé.
    Objectif : empêcher les fragments de type entête, adresse, téléphone, RCS/SIRET,
    fiche fournisseur ou tableau de révision de devenir des comparaisons CIR.
    """
    low = norm(text)
    if not low:
        return True
    if len(low) < 45:
        return True

    technical_signals = [
        "essai", "essais", "mesure", "mesures", "vibration", "vibratoire", "acoustique",
        "temperature", "refrigerant", "refroidissement", "contrepoids", "masselotte",
        "segment", "segmentation", "soufflage", "condensat", "condensats", "secheur",
        "hygrometrie", "air sec", "pression", "debit", "reniflard", "etancheite",
        "poulie", "gaine", "aspiration", "eprouve", "hydraulique", "tube", "tubes",
        "compresseur", "tgm100", "100bar", "300bar", "kg", "bar",
    ]
    has_technical = any(k in low for k in technical_signals)
    detected_themes = themes(text)
    has_noise = any(re.search(p, low) for p in CURRENT_NOISE_PATTERNS)

    hard_admin_patterns = [
        r"\brcs\b|\bsiret\b|\bsiren\b|\bape\b|\btva\b",
        r"telephone|téléphone|adresse|urban[- ]valley|cormeilles|chemin du bas des indes",
        r"page\s+\d+\s+sur\s+\d+",
        r"written by|date modification|redige|rédigé",
        r"\bfr\s*\d{8,}\b",
    ]
    admin_hits = sum(1 for p in hard_admin_patterns if re.search(p, low))

    # Cas typique observé : Mann Hummel / Urban-Valley / téléphone / date modification.
    if re.search(r"mann\s+hummel|europiclon", low) and admin_hits >= 1:
        return True
    if re.search(r"mann\s+hummel|europiclon", low) and re.search(r"rev|revision|révision|redige|rédigé|written|date", low):
        return True

    # Adresse / téléphone / registre légal : on filtre même s'il reste un mot technique isolé.
    if admin_hits >= 2:
        return True
    if admin_hits >= 1 and len(detected_themes) <= 1:
        return True

    # Les entêtes de documents fournisseur contiennent souvent des mots comme gaine/cinématique,
    # mais pas une vraie phrase d'essai ou de résultat.
    if re.search(r"date modification|written by|redige|rédigé", low) and not re.search(r"essai|mesure|resultat|releve|temperature|vibration|acoustique", low):
        return True

    if has_noise and not has_technical:
        return True

    toks = re.findall(r"\b\w+\b", low)
    if toks:
        numeric_ratio = sum(1 for t in toks if re.search(r"\d", t)) / max(1, len(toks))
        alpha_words = [t for t in toks if t.isalpha()]
        if numeric_ratio > 0.34 and len(detected_themes) <= 1:
            return True
        if len(alpha_words) <= 5 and numeric_ratio > 0.25:
            return True

    # Fragments de révision/tableau sans phrase technique exploitable.
    if re.search(r"\b(rev|revision|révision)\b", low) and re.search(r"\d{2}/\d{2}/\d{4}", low) and len(detected_themes) <= 1:
        return True

    # Fragment produit / nomenclature : beaucoup de références, peu de verbe technique.
    product_like = re.search(r"mann\s+hummel|europiclon|weg\s+w22|gaine papier|corps v\d|cinematique v\d", low)
    has_project_action = re.search(r"essai|mesure|tester|teste|testes|evaluer|releve|gain|comparaison|ameliorer|optimiser|monte|montes", low)
    if product_like and not has_project_action:
        return True

    return False

def filter_supporting_passages(passages: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not isinstance(passages, list):
        return out
    seen = set()
    for sp in passages:
        if not isinstance(sp, dict):
            continue
        txt = clean_text(sp.get("text") or "")
        if not txt or is_current_noise_text(txt):
            continue
        key = norm(txt)[:220]
        if key in seen:
            continue
        seen.add(key)
        out.append(sp)
    return out

def split_sentences(text: str) -> List[str]:
    clean = clean_text(text).replace("\n", " ")
    if not clean:
        return []
    parts = re.split(r"(?<=[.!?])\s+(?=[A-ZÉÈÀÂÎÔÛÇ])", clean)
    out = []
    for p in parts:
        p = clean_text(p)
        if len(p) >= 40:
            out.append(p)
    return out


def previous_section_priority(section_key: str, role: str) -> int:
    s = norm(f"{section_key} {role}")
    if "verrou" in s:
        return 100
    if "insuffisance" in s or "limite" in s:
        return 88
    if "etat_art" in s or "etat" in s:
        return 82
    if "objectif" in s:
        return 78
    if "travaux" in s or "demarche" in s or "methode" in s:
        return 70
    return 50


def make_previous_segment_item(parent: Dict[str, Any], segment: str, index: int) -> Dict[str, Any]:
    parent_id = str(parent.get("id") or parent.get("item_id") or parent.get("section_key") or "previous")
    section_key = str(parent.get("section_key") or parent.get("pack_key") or parent.get("section_type") or "")
    role = str(parent.get("role") or "general")
    title = str(parent.get("section_title") or parent.get("title") or section_key or role)

    x = dict(parent)
    x.update({
        "id": f"{parent_id}_seg_{index:03d}",
        "item_id": f"{parent_id}_seg_{index:03d}",
        "parent_item_id": parent_id,
        "parent_role": role,
        "role": role,
        "section_key": section_key,
        "section_type": str(parent.get("section_type") or section_key),
        "section_title": title,
        "text": clean_text(segment),
        "parent_text_preview": clean_text(parent.get("text") or "")[:1200],
        "segment_index": index,
        "segment_source": "previous_cir_section_sentence_window",
        "previous_section_priority": previous_section_priority(section_key, role),
        "is_generic_previous_segment": is_generic_previous_text(segment),
    })
    return x


def split_previous_cir_item(parent: Dict[str, Any], max_segments: int = 80) -> List[Dict[str, Any]]:
    """
    Transforme une grande section CIR en petits passages comparables.
    C'est la correction centrale : on ne compare plus 61 items courants avec seulement 8 grandes sections.
    """
    text = clean_text(parent.get("text") or "")
    if len(text) < 60:
        return []

    sentences = split_sentences(text)
    windows: List[str] = []

    for i in range(len(sentences)):
        for size in (1, 2, 3):
            part = " ".join(sentences[i:i + size]).strip()
            if 80 <= len(part) <= 1300:
                windows.append(part)

    for p in re.split(r"\n{2,}", text):
        p = clean_text(p)
        if 80 <= len(p) <= 1500:
            windows.append(p)

    unique: List[str] = []
    seen = set()
    for w in windows:
        key = norm(w)[:260]
        if key in seen:
            continue
        seen.add(key)
        if is_generic_previous_text(w):
            continue
        if not themes(w) and len(words(w, 20)) < 5:
            continue
        unique.append(w)
        if len(unique) >= max_segments:
            break

    if not unique:
        unique = [text[:1400]]

    return [make_previous_segment_item(parent, seg, idx) for idx, seg in enumerate(unique)]


def current_item_should_be_compared(item: Dict[str, Any]) -> bool:
    txt_main = clean_text(item.get("text") or "")
    txt = expanded_current_text(item)

    # Le texte principal détermine si l'item a du sens comme comparaison.
    # Les supporting_passages ne doivent pas sauver un entête administratif.
    if is_current_noise_text(txt_main):
        return False
    if is_current_noise_text(txt) and len(themes(txt_main)) <= 1:
        return False
    if len(clean_text(txt_main)) < 60:
        return False

    # Objectif faible = simple référence/nomenclature sans action technique exploitable.
    role = str(item.get("role") or "")
    low = norm(txt_main)
    if role == "objectif":
        has_action = re.search(r"essai|essais|mesure|mesures|evaluer|évaluer|tester|testes|releve|relevés|ameliorer|améliorer|optimiser|monte|montés|realisation|réalisation|developper|développer", low)
        if not has_action and len(themes(txt_main)) <= 1:
            return False

    return True


def is_too_generic_keyword(w: str) -> bool:
    return norm(w) in GENERIC_KEYWORDS


def specific_pair_bonus(current_text: str, previous_text: str, current_themes: List[str], previous_themes: List[str]) -> float:
    c = norm(current_text)
    p = norm(previous_text)
    bonus = 0.0

    if "vibration_acoustique" in current_themes:
        if re.search(r"vibrations?|vibratoire|acoustique|bruit|nuisances sonores|aspiration|resonateur", p):
            bonus += 0.12
        if "aspiration" in c and re.search(r"aspiration|bruit|acoustique|resonateur|trajet d aspiration", p):
            bonus += 0.14

    if "thermique_refroidissement" in current_themes:
        if re.search(r"temperature|echauffement|refroidissement|refrigerant|eau liquide|debit d eau", p):
            bonus += 0.14
        if re.search(r"refrigerant|100bar|temperature|debit d eau|refroidissement", c) and re.search(r"temperature|eau liquide|refroidissement|pression|echauffement", p):
            bonus += 0.12

    if "qualite_air_sechage" in current_themes:
        if re.search(r"air sec|point de rosee|hygrometrie|condensats?|eau liquide|secheur", p):
            bonus += 0.14

    if "usure_fiabilite_etancheite" in current_themes:
        if re.search(r"usure|fuite|huile|reniflard|etancheite|segment|resistance mecanique", p):
            bonus += 0.15

    if "contrepoids" in c or "masselotte" in c or "equilibr" in c or "plomb" in c:
        if re.search(r"masselotte|equilibrage|forces d inertie|vibrations|resistance mecanique|comportement vibratoire|compromis", p):
            bonus += 0.18

    return bonus


def previous_candidate_penalty(previous_text: str) -> float:
    p = norm(previous_text)
    penalty = 0.0
    if is_generic_previous_text(previous_text):
        penalty += 0.35
    generic_hits = sum(1 for w in ["solution", "solutions", "techniques", "developper", "parametres", "dispositif", "implementation", "compresseur"] if w in p)
    specific_hits = sum(1 for w in ["vibration", "acoustique", "bruit", "aspiration", "temperature", "refroidissement", "refrigerant", "eau liquide", "hygrometrie", "air sec", "condensat", "usure", "reniflard", "fuite", "segment", "masselotte", "contrepoids", "equilibrage"] if w in p)
    if generic_hits >= 4 and specific_hits <= 2:
        penalty += 0.12
    return penalty



# ============================================================
# Memory V2 : CIR précédent automatique le plus proche
# ============================================================

def _year_int(value: Any) -> Optional[int]:
    m = re.search(r"\d{4}", str(value or ""))
    if not m:
        return None
    try:
        return int(m.group(0))
    except Exception:
        return None


def canonical_project_key(value: Any) -> str:
    s = norm(value)
    return re.sub(r"[^a-z0-9]+", "", s)


def _meta_from_memory_v2_item(item: Dict[str, Any]) -> Dict[str, Any]:
    meta = item.get("metadata") if isinstance(item, dict) else {}
    return meta if isinstance(meta, dict) else {}


def _text_from_memory_v2_item(item: Dict[str, Any]) -> str:
    return clean_text(
        item.get("text")
        or item.get("source_text")
        or item.get("content")
        or item.get("excerpt")
        or ""
    )


def _iter_json_items(obj: Any):
    if isinstance(obj, list):
        for x in obj:
            if isinstance(x, dict):
                yield x
    elif isinstance(obj, dict):
        yielded = False
        for key in ["items", "chunks", "cards", "documents", "data"]:
            value = obj.get(key)
            if isinstance(value, list):
                yielded = True
                for x in value:
                    if isinstance(x, dict):
                        yield x
        if not yielded:
            yield obj


def _iter_memory_v2_json_files() -> List[Path]:
    roots = [
        EXPERIENCE_MEMORY_V2_DIR / "chunks",
        EXPERIENCE_MEMORY_V2_DIR / "cards",
        EXPERIENCE_MEMORY_V2_DIR / "runs",
    ]
    files: List[Path] = []
    for root in roots:
        if root.exists():
            files.extend(sorted(root.rglob("*.json")))
    return files


def _memory_v2_item_matches_project(
    item: Dict[str, Any],
    organisme: str,
    project: str,
    year: Optional[int] = None,
) -> bool:
    meta = _meta_from_memory_v2_item(item)
    org = meta.get("organisme") or item.get("organisme") or ""
    prj = meta.get("project") or meta.get("project_id") or item.get("project") or item.get("project_id") or ""
    y = meta.get("year") or meta.get("annee") or item.get("year") or item.get("annee") or ""

    if canonical_project_key(org) != canonical_project_key(organisme):
        return False
    if canonical_project_key(prj) != canonical_project_key(project):
        return False

    yi = _year_int(y)
    if year is not None and yi != year:
        return False

    return True


def list_previous_years_from_memory_v2(
    organisme: str,
    project: str,
    current_year: str,
) -> List[int]:
    current_int = _year_int(current_year)
    if current_int is None:
        return []

    years = set()

    for path in _iter_memory_v2_json_files():
        data = read_json(path, None)
        if data is None:
            continue

        for item in _iter_json_items(data):
            if not _memory_v2_item_matches_project(item, organisme, project):
                continue
            meta = _meta_from_memory_v2_item(item)
            yi = _year_int(meta.get("year") or meta.get("annee") or item.get("year") or item.get("annee"))
            if yi is not None and yi < current_int:
                years.add(yi)

    return sorted(years, reverse=True)


def closest_previous_year_from_memory_v2(
    organisme: str,
    project: str,
    current_year: str,
) -> Optional[int]:
    years = list_previous_years_from_memory_v2(organisme, project, current_year)
    return years[0] if years else None


def load_previous_cir_items_from_memory_v2(
    organisme: str,
    project: str,
    previous_year: int,
) -> List[Dict[str, Any]]:
    allowed_roles = {
        "objectif",
        "verrou",
        "methode",
        "resultat",
        "limite",
        "contribution",
        "etat_art",
        "parametre",
    }

    raw_items: List[Dict[str, Any]] = []
    seen = set()

    for path in _iter_memory_v2_json_files():
        data = read_json(path, None)
        if data is None:
            continue

        for item in _iter_json_items(data):
            if not _memory_v2_item_matches_project(item, organisme, project, previous_year):
                continue

            meta = _meta_from_memory_v2_item(item)
            role = clean_text(meta.get("role") or item.get("role") or "")
            memory_class = clean_text(meta.get("memory_class") or item.get("memory_class") or "")
            text = _text_from_memory_v2_item(item)

            if role == "style" or memory_class == "style":
                continue
            if role and role not in allowed_roles:
                continue
            if len(text) < 45:
                continue

            key = (role, norm(text)[:260])
            if key in seen:
                continue
            seen.add(key)

            raw_items.append({
                "id": meta.get("chunk_id") or meta.get("rag_chunk_id") or item.get("id") or f"memory_v2_{previous_year}_{len(raw_items)}",
                "item_id": meta.get("chunk_id") or meta.get("rag_chunk_id") or item.get("id") or f"memory_v2_{previous_year}_{len(raw_items)}",
                "role": role or "general",
                "pack_key": meta.get("pack_key") or "",
                "section_key": meta.get("section_key") or meta.get("pack_key") or role or "",
                "section_type": meta.get("section_type") or role or "",
                "section_title": meta.get("section_title") or meta.get("title") or "",
                "text": text,
                "document": meta.get("document") or meta.get("source_file") or item.get("document") or "",
                "source_path": meta.get("source_path") or item.get("source_path") or "",
                "source_type": "experience_memory_v2_previous_cir",
                "year": str(previous_year),
                "memory_v2_path": str(path),
                "previous_section_priority": previous_section_priority(meta.get("pack_key") or "", role or "general"),
            })

    segmented: List[Dict[str, Any]] = []
    section_count = len(raw_items)

    for item in raw_items:
        segments = split_previous_cir_item(item)
        if segments:
            segmented.extend(segments)
        else:
            segmented.append(item)

    for x in segmented:
        x["previous_cir_sections_count"] = section_count
        x["previous_cir_segmentation"] = "memory_v2_sentence_windows_closest_previous_year"
        x["previous_source"] = "experience_memory_v2"
        x["previous_year"] = str(previous_year)

    return segmented


def load_previous_cir_memory_items(organisme: str, project: str, current_year: str, max_previous_years: int = 1) -> Tuple[List[str], List[Dict[str, Any]]]:
    """
    Charge automatiquement le CIR précédent le plus proche.

    Règle :
    - dossier courant 2025 -> prendre 2024 si disponible ;
    - sinon prendre 2023 ;
    - priorité à Memory V2, car elle contient les CIR finaux consultant validés ;
    - fallback vers l'ancien stockage local cir_final/cir_final_extracted.json.
    """
    current_int = _year_int(current_year)

    v2_year = closest_previous_year_from_memory_v2(organisme, project, current_year)
    if v2_year is not None:
        items = load_previous_cir_items_from_memory_v2(organisme, project, v2_year)
        if items:
            return [str(v2_year)], items

    root = STORAGE_DIR / slug(organisme) / "projects" / slug(project) / "years"
    if not root.exists():
        return [], []

    candidates: List[int] = []
    for yd in root.iterdir():
        if not yd.is_dir():
            continue
        yi = _year_int(yd.name)
        if yi is None:
            continue
        if current_int is not None and yi >= current_int:
            continue
        p = cir_final_report_path(organisme, project, str(yi))
        if p.exists():
            candidates.append(yi)

    if not candidates:
        return [], []

    previous_year = max(candidates)
    years = [str(previous_year)]
    items: List[Dict[str, Any]] = []
    section_count = 0

    report = read_json(cir_final_report_path(organisme, project, str(previous_year)), {})
    for item in report.get("items") or []:
        if not isinstance(item, dict):
            continue
        txt = clean_text(item.get("text"))
        if len(txt) < 35:
            continue
        parent = dict(item)
        parent["year"] = str(previous_year)
        parent["source_type"] = "previous_cir_final_without_frascati"
        parent["previous_source"] = "local_cir_final"
        section_count += 1
        items.extend(split_previous_cir_item(parent))

    for x in items:
        x["previous_cir_sections_count"] = section_count
        x["previous_cir_segmentation"] = "sentence_windows_v68_1_filtered_scoring_calibrated"
        x["previous_source"] = x.get("previous_source") or "local_cir_final"
        x["previous_year"] = str(previous_year)

    return years, items


def compare_one(current: Dict[str, Any], previous_items: List[Dict[str, Any]], top_k: int = 3) -> Dict[str, Any]:
    scored = []
    for prev in previous_items:
        details = score_pair(current, prev)
        scored.append({
            "previous_candidate": prev,
            "similarity_score": details["score"],
            "similarity_details": details,
        })
    scored.sort(key=lambda x: x["similarity_score"], reverse=True)
    candidates = scored[:top_k]
    best = candidates[0] if candidates else None

    if best:
        dec = decision_from_score(best["similarity_score"], best["similarity_details"])
        best["final_scores"] = dec
    else:
        dec = {
            "status": "new_or_not_found",
            "label": "Aucun CIR précédent trouvé",
            "continuity_score": 0.0,
            "novelty_score": 1.0,
        }

    return {
        "current_item": current,
        "best_match": best,
        "candidates": candidates,
        "decision": dec,
    }


def summarize(comparisons: List[Dict[str, Any]]) -> Dict[str, Any]:
    verrous = [c for c in comparisons if (c.get("current_item") or {}).get("role") == "verrou"]

    def status(c):
        return (c.get("decision") or {}).get("status")

    new_v = [c for c in verrous if status(c) == "new_or_not_found"]
    evo_v = [c for c in verrous if status(c) == "evolution_or_partial_continuity"]
    cont_v = [c for c in verrous if status(c) == "continuity_strong"]

    novelty = None
    weighted_novelty_sum = 0.0
    weight_sum = 0.0
    universal_count = 0
    broad_previous_count = 0

    for c in verrous:
        cur = c.get("current_item") or {}
        dec = c.get("decision") or {}
        best = c.get("best_match") or {}
        details = best.get("similarity_details") or {}

        weight = 0.45 if is_universal_implicit_current(cur) else 1.0
        if is_universal_implicit_current(cur):
            universal_count += 1
        if details.get("previous_is_broad_segment"):
            broad_previous_count += 1

        weighted_novelty_sum += weight * float(dec.get("novelty_score") or 0.0)
        weight_sum += weight

    if weight_sum > 0:
        novelty = weighted_novelty_sum / weight_sum

    if novelty is None:
        signal = "no_verrou"
        explanation = "Aucun verrou courant à comparer."
    elif novelty >= 0.55:
        signal = "new_rnd_attention"
        explanation = "Plusieurs verrous semblent nouveaux ou en évolution par rapport au CIR précédent."
    elif novelty <= 0.25:
        signal = "continuity_reuse_risk"
        explanation = "Beaucoup d'éléments sont en continuité avec le CIR précédent : attention à justifier la nouveauté de l'année N."
    else:
        signal = "mixed"
        explanation = "Profil mixte : certains éléments prolongent le CIR précédent, d'autres semblent nouveaux ou évolutifs."

    return {
        "comparisons_count": len(comparisons),
        "verrou_count": len(verrous),
        "new_verrou_count": len(new_v),
        "evolution_verrou_count": len(evo_v),
        "continuity_verrou_count": len(cont_v),
        "universal_implicit_verrou_count": universal_count,
        "broad_previous_match_count": broad_previous_count,
        "project_novelty_score": round(novelty, 4) if novelty is not None else None,
        "project_novelty_rule": "weighted average of calibrated novelty scores; universal reconstructed verrous weight=0.45",
        "frascati_context_signal": signal,
        "frascati_context_explanation": explanation,
    }


def compare_current_raw_with_cir_memory(
    organisme: str,
    project: str,
    year: str,
    nlp_result_path: Optional[str | Path] = None,
    top_k: int = 3,
    max_previous_years: int = 3,
) -> Dict[str, Any]:
    nlp_path = Path(nlp_result_path) if nlp_result_path else current_nlp_default_path(organisme, project, year)
    if not nlp_path.exists():
        raise FileNotFoundError(f"NLP courant introuvable : {nlp_path}")

    nlp = read_json(nlp_path, {})
    current_pack, current_pack_source = get_current_raw_pack_with_frascati(nlp)
    raw_current_items = pack_to_items(current_pack, source_type="current_raw_with_frascati")
    current_items = [x for x in raw_current_items if current_item_should_be_compared(x)]

    previous_years, previous_items = load_previous_cir_memory_items(
        organisme=organisme,
        project=project,
        current_year=year,
        max_previous_years=max_previous_years,
    )

    if not previous_items:
        report = {
            "ok": True,
            "version": "cir_memory_v68_1_segmented_calibrated_compare",
            "has_previous_cir": False,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "organisme": organisme,
            "project": project,
            "current_year": str(year),
            "current_pack_source": current_pack_source,
            "summary": {
                "raw_current_items_count": len(raw_current_items),
                "current_items_count": len(current_items),
                "filtered_current_noise_count": len(raw_current_items) - len(current_items),
                "filter_version": "v68_1_admin_noise_strict",
                "previous_cir_items_count": 0,
                "project_novelty_score": None,
                "frascati_context_signal": "no_previous_cir",
                "previous_cir_rule": "Memory V2 cherchée automatiquement : année la plus proche avant l’année courante.",
            },
            "comparisons": [],
            "verrou_comparisons": [],
        }
    else:
        comparisons = [compare_one(cur, previous_items, top_k=top_k) for cur in current_items]
        summ = summarize(comparisons)
        previous_sections_count = 0
        if previous_items:
            previous_sections_count = int(previous_items[0].get("previous_cir_sections_count") or 0)

        report = {
            "ok": True,
            "version": "cir_memory_v68_1_segmented_calibrated_compare",
            "has_previous_cir": True,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "organisme": organisme,
            "project": project,
            "current_year": str(year),
            "previous_cir_years_used": previous_years,
            "current_pack_source": current_pack_source,
            "previous_cir_rule": "CIR final précédent automatiquement pris depuis Memory V2 : année la plus proche avant l’année courante, ex. 2025 -> 2024 sinon 2023. Fallback ancien stockage local si Memory V2 indisponible.",
            "previous_cir_source": previous_items[0].get("previous_source") if previous_items else None,
            "previous_cir_year_used": previous_years[0] if previous_years else None,
            "previous_cir_segmentation": "section_to_sentence_windows_v68_1_filtered_scoring_calibrated",
            "summary": {
                "raw_current_items_count": len(raw_current_items),
                "current_items_count": len(current_items),
                "filtered_current_noise_count": len(raw_current_items) - len(current_items),
                "filter_version": "v68_1_admin_noise_strict",
                "previous_cir_sections_count": previous_sections_count,
                "previous_cir_items_count": len(previous_items),
                **summ,
            },
            "comparisons": comparisons,
            "verrou_comparisons": [
                c for c in comparisons if (c.get("current_item") or {}).get("role") == "verrou"
            ],
            "new_or_not_found": [
                c for c in comparisons if (c.get("decision") or {}).get("status") == "new_or_not_found"
            ],
            "evolution_or_partial_continuity": [
                c for c in comparisons if (c.get("decision") or {}).get("status") == "evolution_or_partial_continuity"
            ],
            "continuity_strong": [
                c for c in comparisons if (c.get("decision") or {}).get("status") == "continuity_strong"
            ],
        }

    out_path = comparison_report_path(organisme, project, year)
    write_json(out_path, report)
    return report


load_or_create_cir_memory_comparison = compare_current_raw_with_cir_memory


def cir_memory_prompt_block(report: Dict[str, Any], max_items: int = 6) -> str:
    if not report or not report.get("has_previous_cir"):
        return "Aucune mémoire CIR précédente disponible."

    lines = [
        "Mémoire CIR précédente disponible.",
        f"Résumé : {report.get('summary')}",
        "Comparaisons verrous principales :",
    ]
    for x in (report.get("verrou_comparisons") or [])[:max_items]:
        cur = x.get("current_item") or {}
        best = x.get("best_match") or {}
        prev = best.get("previous_candidate") or {}
        dec = x.get("decision") or {}
        lines.append(
            f"- Verrou courant : {clean_text(cur.get('text'))[:220]} | "
            f"Décision : {dec.get('status')} | "
            f"Ancien CIR : {clean_text(prev.get('text'))[:220]}"
        )
    return "\n".join(lines)
