# -*- coding: utf-8 -*-
from __future__ import annotations

"""
CIR_MEMORY V162 - comparaison N/N-1 + traçabilité documentaire et surlignage

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
import os
import re
import time
from pathlib import Path
from datetime import datetime
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from modules.common.runtime_paths import (
    data_root,
    experience_memory_root,
    organism_memory_root,
    outputs_root,
)


BASE_DIR = data_root()
STORAGE_DIR = organism_memory_root()
OUTPUTS_DIR = outputs_root()


def _configured_experience_memory_v2_dir() -> Path:
    """Résout la configuration API sans dépendre d'un client ou d'un projet."""
    direct = str(os.getenv("ENNOSMART_EXPERIENCE_MEMORY_V2_DIR") or "").strip()
    if direct:
        return Path(direct).expanduser()

    # Pydantic charge les fichiers .env dans Settings sans nécessairement
    # recopier leurs valeurs dans os.environ. Les deux imports couvrent le
    # lancement depuis la racine et celui depuis backend_api.
    for module_name in ("backend_api.core.config", "core.config"):
        try:
            module = __import__(module_name, fromlist=["settings"])
            configured = str(
                getattr(module.settings, "ENNOSMART_EXPERIENCE_MEMORY_V2_DIR", "")
                or ""
            ).strip()
            if configured:
                return Path(configured).expanduser()
        except Exception:
            continue

    return experience_memory_root()


EXPERIENCE_MEMORY_V2_DIR = Path(
    _configured_experience_memory_v2_dir()
).expanduser()

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


def truncate(x: Any, max_chars: int = 700) -> str:
    """Retourne un texte compact limité à max_chars sans lever d'erreur."""
    text = re.sub(r"\s+", " ", clean_text(x)).strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def norm(x: Any) -> str:
    return _normalized_text_cached(clean_text(x).lower())


@lru_cache(maxsize=8192)
def _normalized_text_cached(x: str) -> str:
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


def year_dir(
    organisme: str,
    project: str,
    year: str,
    subproject: str = "",
) -> Path:
    scope = STORAGE_DIR / slug(organisme) / "projects" / slug(project)
    if clean_text(subproject):
        scope = scope / "subprojects" / slug(subproject)
    return scope / "years" / str(year)


def cir_final_dir(
    organisme: str,
    project: str,
    year: str,
    subproject: str = "",
) -> Path:
    return year_dir(
        organisme,
        project,
        year,
        subproject=subproject,
    ) / "cir_final"


def cir_final_report_path(
    organisme: str,
    project: str,
    year: str,
    subproject: str = "",
) -> Path:
    return cir_final_dir(
        organisme,
        project,
        year,
        subproject=subproject,
    ) / "cir_final_extracted.json"


def _path_name_variants(value: Any) -> List[str]:
    """Retourne les variantes usuelles d'un identifiant de dossier EnnoSmart."""
    raw = clean_text(value)
    if not raw:
        return ["unknown"]

    candidates = [
        raw,
        raw.lower(),
        raw.upper(),
        raw.title(),
        raw.replace("-", "_"),
        raw.replace("_", "-"),
        raw.replace(" ", "_"),
        raw.replace(" ", "-"),
        slug(raw),
        slug(raw).replace("_", "-"),
    ]

    out: List[str] = []
    for candidate in candidates:
        candidate = clean_text(candidate)
        if candidate and candidate not in out:
            out.append(candidate)
    return out


def _canonical_path_token(value: Any) -> str:
    value = clean_text(value).lower()
    value = value.translate(str.maketrans("àâäéèêëîïôöùûüç’'", "aaaeeeeiioouuuc__"))
    return re.sub(r"[^a-z0-9]+", "", value)


def current_nlp_candidate_paths(
    organisme: str,
    project: str,
    year: str,
    subproject: str = "",
) -> List[Path]:
    """Construit tous les chemins connus du ``nlp_result.json`` courant."""
    year_value = str(year).strip()
    candidates: List[Path] = []

    wanted_subproject = _canonical_path_token(subproject)
    env_path = clean_text(__import__("os").getenv("ENNOSMART_NLP_RESULT_PATH"))
    if env_path and (
        not wanted_subproject
        or wanted_subproject in _canonical_path_token(env_path)
    ):
        candidates.append(Path(env_path))

    org_variants = _path_name_variants(organisme)
    project_variants = _path_name_variants(project)
    subproject_variants = (
        _path_name_variants(subproject)
        if clean_text(subproject)
        else []
    )

    for org_value in org_variants:
        for project_value in project_variants:
            project_scope = (
                STORAGE_DIR
                / org_value
                / "projects"
                / project_value
            )
            if subproject_variants:
                for subproject_value in subproject_variants:
                    project_year_dir = (
                        project_scope
                        / "subprojects"
                        / subproject_value
                        / "years"
                        / year_value
                    )
                    candidates.extend([
                        project_year_dir / "nlp" / "nlp_result.json",
                        project_year_dir / "nlp_result.json",
                        project_year_dir / "diagnostic" / "nlp_result.json",
                        project_year_dir / "ennodiagnostic" / "nlp_result.json",
                        OUTPUTS_DIR
                        / org_value
                        / project_value
                        / subproject_value
                        / year_value
                        / "nlp_result.json",
                    ])
            else:
                # Compatibilité avec les projets sans sous-projet.
                candidates.append(
                    OUTPUTS_DIR
                    / org_value
                    / project_value
                    / year_value
                    / "nlp_result.json"
                )
                project_year_dir = project_scope / "years" / year_value
                candidates.extend([
                    project_year_dir / "nlp" / "nlp_result.json",
                    project_year_dir / "nlp_result.json",
                    project_year_dir / "diagnostic" / "nlp_result.json",
                    project_year_dir / "ennodiagnostic" / "nlp_result.json",
                ])

    unique: List[Path] = []
    seen = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def resolve_current_nlp_result_path(
    organisme: str,
    project: str,
    year: str,
    *,
    subproject: str = "",
    required: bool = False,
) -> Optional[Path]:
    """
    Résout le ``nlp_result.json`` courant sans dépendre de la casse ni du format
    ``AI_RADAR`` / ``ai_radar`` / ``ai-radar``.

    La recherche suit d'abord les chemins déterministes, puis effectue un fallback
    borné dans ``outputs/safe_rag_upload`` et ``storage/organismes``.
    """
    candidates = current_nlp_candidate_paths(
        organisme,
        project,
        year,
        subproject=subproject,
    )

    for candidate in candidates:
        try:
            if candidate.exists() and candidate.is_file():
                return candidate.resolve()
        except Exception:
            continue

    wanted_org = _canonical_path_token(organisme)
    wanted_project = _canonical_path_token(project)
    wanted_subproject = _canonical_path_token(subproject)
    wanted_year = str(year).strip()
    discovered: List[tuple[int, float, Path]] = []

    for root in [OUTPUTS_DIR, STORAGE_DIR]:
        if not root.exists():
            continue
        try:
            iterator = root.rglob("nlp_result.json")
        except Exception:
            continue

        for path in iterator:
            try:
                if not path.is_file():
                    continue
                parts = [str(part) for part in path.parts]
                joined = _canonical_path_token("/".join(parts))
                if wanted_year and wanted_year not in parts:
                    continue
                if wanted_org and wanted_org not in joined:
                    continue
                if wanted_project and wanted_project not in joined:
                    continue
                if wanted_subproject and wanted_subproject not in joined:
                    continue

                score = 0
                lowered_parts = [part.lower() for part in parts]
                if "nlp" in lowered_parts:
                    score += 20
                if "safe_rag_upload" in lowered_parts:
                    score += 15
                if "years" in lowered_parts:
                    score += 10
                if wanted_subproject and "subprojects" in lowered_parts:
                    score += 25
                if _canonical_path_token(path.parent.name) == wanted_project:
                    score += 5

                discovered.append((score, path.stat().st_mtime, path.resolve()))
            except Exception:
                continue

    if discovered:
        discovered.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return discovered[0][2]

    if required:
        searched = "\n - ".join(str(path) for path in candidates[:24])
        raise FileNotFoundError(
            "nlp_result.json courant introuvable pour "
            f"organisme={organisme!r}, projet={project!r}, "
            f"sous-projet={subproject!r}, année={year!r}.\n"
            f"Chemins testés :\n - {searched}\n"
            "Lance d'abord Préparer les sources ou définis "
            "ENNOSMART_NLP_RESULT_PATH avec le chemin exact."
        )

    return None


def current_nlp_default_path(
    organisme: str,
    project: str,
    year: str,
    subproject: str = "",
) -> Path:
    """Compatibilité : retourne le chemin résolu ou le chemin historique attendu."""
    resolved = resolve_current_nlp_result_path(
        organisme=organisme,
        project=project,
        year=year,
        subproject=subproject,
        required=False,
    )
    if resolved is not None:
        return resolved
    return (
        year_dir(
            organisme,
            project,
            year,
            subproject=subproject,
        )
        / "nlp"
        / "nlp_result.json"
    )


def comparison_report_path(
    organisme: str,
    project: str,
    year: str,
    subproject: str = "",
) -> Path:
    return (
        year_dir(organisme, project, year, subproject=subproject)
        / "cir_memory"
        / "cir_memory_comparison_report.json"
    )


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
    # Return a fresh list: callers cannot mutate the cached features.
    return list(_themes_cached(text))


@lru_cache(maxsize=4096)
def _themes_cached(text: str) -> Tuple[str, ...]:
    low = norm(text)
    found = []
    for th, kws in TECH_THEMES.items():
        if any(norm(k) in low for k in kws):
            found.append(th)
    return tuple(found)


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
# Memory V2 : CIR précédent automatique N-1 puis N-2
# ============================================================

MEMORY_V2_CATALOG_NAMES = (
    "catalog_v2.json",
    "catalog.json",
    "index_v2.json",
    "memory_v2_catalog.json",
)

MEMORY_V2_CONTAINER_KEYS = {
    "items",
    "chunks",
    "cards",
    "documents",
    "data",
    "entries",
    "records",
    "memories",
    "results",
}

MEMORY_V2_TEXT_KEYS = (
    "text",
    "source_text",
    "content",
    "excerpt",
    "section_text",
    "raw_text",
)

MEMORY_V2_CONTEXT_KEYS = (
    "organisme",
    "organisme_id",
    "organization",
    "organisation",
    "client",
    "company",
    "project",
    "project_id",
    "project_name",
    "project_slug",
    "dossier",
    "subproject",
    "subproject_name",
    "sub_project",
    "sous_projet",
    "sous_project",
    "year",
    "annee",
    "project_year",
    "role",
    "pack_key",
    "section_key",
    "section_type",
    "section_title",
    "title",
    "document",
    "source_file",
    "filename",
    "source_path",
    "memory_class",
    "memory_type",
    "content_origin",
    "chunk_id",
    "rag_chunk_id",
)


def _year_int(value: Any) -> Optional[int]:
    m = re.search(r"(?<!\d)(?:19|20)\d{2}(?!\d)", str(value or ""))
    if not m:
        return None
    try:
        return int(m.group(0))
    except Exception:
        return None


def canonical_project_key(value: Any) -> str:
    s = norm(value)
    return re.sub(r"[^a-z0-9]+", "", s)


def _normalise_memory_role(value: Any, pack_key: Any = "") -> str:
    raw = norm(value)
    pack = norm(pack_key)

    aliases = {
        "objectif": "objectif",
        "objectifs": "objectif",
        "verrou": "verrou",
        "verrous": "verrou",
        "methode": "methode",
        "methodes": "methode",
        "resultat": "resultat",
        "resultats": "resultat",
        "limite": "limite",
        "limites": "limite",
        "contribution": "contribution",
        "contributions": "contribution",
        "etat art": "etat_art",
        "etat_art": "etat_art",
        "parametre": "parametre",
        "parametres": "parametre",
        "style": "style",
    }

    for source in (raw, pack):
        if source in aliases:
            return aliases[source]
        for token, canonical in aliases.items():
            if token and token in source:
                return canonical

    for key, role in ROLE_BY_PACK.items():
        if norm(key) == pack:
            return role

    return raw or "general"


def _scalar_context_from_dict(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    out: Dict[str, Any] = {}

    nested_candidates = [
        value.get("metadata"),
        value.get("meta"),
        value.get("source_metadata"),
    ]
    payload = value.get("payload")
    if isinstance(payload, dict):
        nested_candidates.extend([
            payload.get("metadata"),
            payload.get("meta"),
        ])

    for candidate in nested_candidates:
        if not isinstance(candidate, dict):
            continue
        for key in MEMORY_V2_CONTEXT_KEYS:
            item = candidate.get(key)
            if item not in (None, "", [], {}):
                out[key] = item

    for key in MEMORY_V2_CONTEXT_KEYS:
        item = value.get(key)
        if item not in (None, "", [], {}):
            out[key] = item

    return out


def _meta_from_memory_v2_item(item: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(item, dict):
        return {}

    merged: Dict[str, Any] = {}
    inherited = item.get("__inherited_metadata")
    if isinstance(inherited, dict):
        merged.update(inherited)

    merged.update(_scalar_context_from_dict(item))
    return merged


def _text_from_memory_v2_item(item: Dict[str, Any]) -> str:
    if not isinstance(item, dict):
        return ""

    for key in MEMORY_V2_TEXT_KEYS:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return clean_text(value)

    for nested_key in ("payload", "card", "chunk", "record", "data"):
        nested = item.get(nested_key)
        if not isinstance(nested, dict):
            continue
        for key in MEMORY_V2_TEXT_KEYS:
            value = nested.get(key)
            if isinstance(value, str) and value.strip():
                return clean_text(value)

    return ""


def _iter_json_items(
    obj: Any,
    inherited: Optional[Dict[str, Any]] = None,
    depth: int = 0,
):
    """
    Parcourt aussi les catalogues Memory V2 imbriqués.

    Les métadonnées organisme/projet/année présentes sur un parent sont
    transmises aux chunks enfants. Cela corrige le cas de catalog_v2.json,
    où l'année peut être portée par la fiche projet et non par chaque passage.
    """
    if depth > 12:
        return

    inherited = dict(inherited or {})

    if isinstance(obj, list):
        for child in obj:
            yield from _iter_json_items(child, inherited=inherited, depth=depth + 1)
        return

    if not isinstance(obj, dict):
        return

    context = dict(inherited)
    context.update(_scalar_context_from_dict(obj))

    item = dict(obj)
    item["__inherited_metadata"] = context

    if _text_from_memory_v2_item(item):
        yield item

    for key, child in obj.items():
        if key in {"metadata", "meta", "source_metadata", "__inherited_metadata"}:
            continue
        if isinstance(child, (dict, list)):
            yield from _iter_json_items(child, inherited=context, depth=depth + 1)


def _read_memory_v2_file(path: Path) -> Any:
    if path.suffix.lower() == ".jsonl":
        rows: List[Any] = []
        try:
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
        except Exception:
            return None
        return rows

    return read_json(path, None)


def _iter_memory_v2_json_files() -> List[Path]:
    """
    Sources supportées :
    - catalog_v2.json à la racine ;
    - chunks/, cards/ et runs/ ;
    - JSONL éventuels.

    Le dossier style_memory_adapter est volontairement exclu de la
    comparaison factuelle N/N-1.
    """
    files: List[Path] = []

    for name in MEMORY_V2_CATALOG_NAMES:
        candidate = EXPERIENCE_MEMORY_V2_DIR / name
        if candidate.exists() and candidate.is_file():
            files.append(candidate)

    for root_name in ("chunks", "cards", "runs"):
        root = EXPERIENCE_MEMORY_V2_DIR / root_name
        if not root.exists():
            continue
        files.extend(sorted(root.rglob("*.json")))
        files.extend(sorted(root.rglob("*.jsonl")))

    # Compatibilité avec une Memory V2 stockée directement à la racine.
    if EXPERIENCE_MEMORY_V2_DIR.exists():
        for candidate in sorted(EXPERIENCE_MEMORY_V2_DIR.glob("*.json")):
            if candidate.name in MEMORY_V2_CATALOG_NAMES:
                continue
            files.append(candidate)

    unique: List[Path] = []
    seen = set()
    for path in files:
        try:
            resolved = str(path.resolve()).lower()
        except Exception:
            resolved = str(path).lower()
        if "style_memory_adapter" in resolved:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)

    return unique


def memory_v2_fingerprint(
    organisme: str = "",
    project: str = "",
    current_year: str = "",
    subproject: str = "",
) -> str:
    """
    Empreinte légère utilisée par le cache EnnoDiagnostic.

    Elle change dès qu'un catalogue/chunk Memory V2 est créé ou modifié.
    """
    parts = [
        "cir_memory_memory_v2_fingerprint_v157",
        canonical_project_key(organisme),
        canonical_project_key(project),
        canonical_project_key(subproject),
        str(current_year),
    ]

    for path in _iter_memory_v2_json_files():
        try:
            stat = path.stat()
            rel = path.relative_to(EXPERIENCE_MEMORY_V2_DIR)
            parts.append(f"{rel}|{stat.st_size}|{stat.st_mtime_ns}")
        except Exception:
            continue

    import hashlib
    return hashlib.sha256("\n".join(parts).encode("utf-8", errors="ignore")).hexdigest()


def _memory_v2_identity_values(item: Dict[str, Any]) -> Tuple[str, str, Optional[int]]:
    meta = _meta_from_memory_v2_item(item)

    org = (
        meta.get("organisme")
        or meta.get("organisme_id")
        or meta.get("organization")
        or meta.get("organisation")
        or meta.get("client")
        or meta.get("company")
        or ""
    )
    project = (
        meta.get("project")
        or meta.get("project_name")
        or meta.get("project_slug")
        or meta.get("project_id")
        or meta.get("dossier")
        or ""
    )
    year = _year_int(
        meta.get("year")
        or meta.get("annee")
        or meta.get("project_year")
        or ""
    )

    source_path = clean_text(
        meta.get("source_path")
        or item.get("__memory_v2_file_path")
        or ""
    )

    # Fallback contrôlé : certains catalogues portent l'identité uniquement
    # dans le chemin du fichier.
    if not org and source_path:
        path_key = canonical_project_key(source_path)
        if path_key:
            org = source_path
    if not project and source_path:
        project = source_path
    if year is None and source_path:
        year = _year_int(source_path)

    return clean_text(org), clean_text(project), year


def _memory_v2_subproject_value(item: Dict[str, Any]) -> str:
    meta = _meta_from_memory_v2_item(item)
    subproject = clean_text(
        meta.get("subproject")
        or meta.get("subproject_name")
        or meta.get("sub_project")
        or meta.get("sous_projet")
        or meta.get("sous_project")
        or ""
    )
    if subproject:
        return subproject

    # Certains exports plus anciens ne portent l'identité complète que dans
    # leur chemin. Le chemin canonique reste alors comparable sans règle client.
    return clean_text(
        meta.get("source_path")
        or item.get("__memory_v2_file_path")
        or ""
    )


def _memory_v2_item_matches_project(
    item: Dict[str, Any],
    organisme: str,
    project: str,
    year: Optional[int] = None,
    subproject: str = "",
) -> bool:
    org, prj, yi = _memory_v2_identity_values(item)

    wanted_org = canonical_project_key(organisme)
    wanted_project = canonical_project_key(project)
    org_key = canonical_project_key(org)
    project_key = canonical_project_key(prj)

    # Comparaison stricte même organisme + même projet.
    # Lorsque l'identité est seulement présente dans un chemin, on accepte
    # que la clé recherchée soit incluse dans ce chemin canonique.
    if not org_key or not (
        org_key == wanted_org
        or wanted_org in org_key
    ):
        return False

    if not project_key or not (
        project_key == wanted_project
        or wanted_project in project_key
    ):
        return False

    wanted_subproject = canonical_project_key(subproject)
    if wanted_subproject:
        subproject_key = canonical_project_key(
            _memory_v2_subproject_value(item)
        )
        if not subproject_key or not (
            subproject_key == wanted_subproject
            or wanted_subproject in subproject_key
        ):
            return False

    if year is not None and yi != year:
        return False

    return True


def _candidate_previous_years(
    current_year: str,
    max_previous_years: int = 3,
) -> List[int]:
    current_int = _year_int(current_year)
    if current_int is None:
        return []

    depth = max(1, int(max_previous_years or 1))
    return [current_int - offset for offset in range(1, depth + 1)]


def list_previous_years_from_memory_v2(
    organisme: str,
    project: str,
    current_year: str,
    max_previous_years: int = 10,
    subproject: str = "",
) -> List[int]:
    current_int = _year_int(current_year)
    if current_int is None:
        return []

    years = set()

    for path in _iter_memory_v2_json_files():
        data = _read_memory_v2_file(path)
        if data is None:
            continue

        for original_item in _iter_json_items(data):
            item = dict(original_item)
            item["__memory_v2_file_path"] = str(path)

            if not _memory_v2_item_matches_project(
                item,
                organisme,
                project,
                subproject=subproject,
            ):
                continue

            _, _, yi = _memory_v2_identity_values(item)
            if yi is not None and yi < current_int:
                years.add(yi)

    ordered = sorted(years, reverse=True)
    return ordered[: max(1, int(max_previous_years or 1))]


def closest_previous_year_from_memory_v2(
    organisme: str,
    project: str,
    current_year: str,
    max_previous_years: int = 3,
    subproject: str = "",
) -> Optional[int]:
    available = set(
        list_previous_years_from_memory_v2(
            organisme,
            project,
            current_year,
            max_previous_years=max(10, max_previous_years),
            subproject=subproject,
        )
    )

    # Ordre imposé : N-1, puis N-2, puis N-3...
    for candidate_year in _candidate_previous_years(
        current_year,
        max_previous_years=max_previous_years,
    ):
        if candidate_year in available:
            return candidate_year

    return max(available) if available else None


def load_previous_cir_items_from_memory_v2(
    organisme: str,
    project: str,
    previous_year: int,
    subproject: str = "",
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
        "general",
    }

    raw_items: List[Dict[str, Any]] = []
    seen = set()

    for path in _iter_memory_v2_json_files():
        data = _read_memory_v2_file(path)
        if data is None:
            continue

        for original_item in _iter_json_items(data):
            item = dict(original_item)
            item["__memory_v2_file_path"] = str(path)

            if not _memory_v2_item_matches_project(
                item,
                organisme,
                project,
                previous_year,
                subproject,
            ):
                continue

            meta = _meta_from_memory_v2_item(item)
            role = _normalise_memory_role(
                meta.get("role") or item.get("role") or "",
                meta.get("pack_key") or item.get("pack_key") or "",
            )
            memory_class = norm(
                meta.get("memory_class")
                or meta.get("memory_type")
                or item.get("memory_class")
                or item.get("memory_type")
                or ""
            )
            text = _text_from_memory_v2_item(item)
            path_blob = norm(
                f"{path} {meta.get('source_path') or ''} "
                f"{meta.get('content_origin') or ''}"
            )

            # Séparation stricte : les cartes de style ne sont jamais utilisées
            # comme contenu factuel du CIR précédent.
            if role == "style":
                continue
            if "style" in memory_class:
                continue
            if "style memory adapter" in path_blob or "style_memory_adapter" in str(path).lower():
                continue
            if role not in allowed_roles:
                continue
            if len(text) < 45:
                continue

            section_title = clean_text(
                meta.get("section_title")
                or meta.get("title")
                or item.get("section_title")
                or item.get("title")
                or ""
            )
            document = clean_text(
                meta.get("document")
                or meta.get("source_file")
                or meta.get("filename")
                or item.get("document")
                or item.get("filename")
                or ""
            )

            key = (
                role,
                canonical_project_key(document),
                norm(section_title),
                norm(text)[:300],
            )
            if key in seen:
                continue
            seen.add(key)

            chunk_id = (
                meta.get("chunk_id")
                or meta.get("rag_chunk_id")
                or item.get("id")
                or f"memory_v2_{previous_year}_{len(raw_items)}"
            )

            raw_items.append({
                "id": chunk_id,
                "item_id": chunk_id,
                "role": role,
                "pack_key": meta.get("pack_key") or "",
                "section_key": meta.get("section_key") or meta.get("pack_key") or role,
                "section_type": meta.get("section_type") or role,
                "section_title": section_title,
                "text": text,
                "document": document,
                "source_path": meta.get("source_path") or item.get("source_path") or "",
                "source_type": "experience_memory_v2_previous_cir",
                "year": str(previous_year),
                "memory_v2_path": str(path),
                "memory_class": memory_class or "factual",
                "previous_section_priority": previous_section_priority(
                    meta.get("pack_key") or "",
                    role,
                ),
            })

    segmented: List[Dict[str, Any]] = []
    section_count = len(raw_items)

    for item in raw_items:
        segments = split_previous_cir_item(item)
        if segments:
            segmented.extend(segments)
        else:
            segmented.append(item)

    for item in segmented:
        item["previous_cir_sections_count"] = section_count
        item["previous_cir_segmentation"] = (
            "memory_v2_sentence_windows_exact_previous_year_v157"
        )
        item["previous_source"] = "experience_memory_v2"
        item["previous_year"] = str(previous_year)

    return segmented


def _load_previous_local_items_for_year(
    organisme: str,
    project: str,
    previous_year: int,
    subproject: str = "",
) -> List[Dict[str, Any]]:
    report_path = cir_final_report_path(
        organisme,
        project,
        str(previous_year),
        subproject=subproject,
    )
    if not report_path.exists():
        return []

    report = read_json(report_path, {})
    items: List[Dict[str, Any]] = []
    section_count = 0

    for source_item in report.get("items") or []:
        if not isinstance(source_item, dict):
            continue
        text = clean_text(source_item.get("text"))
        if len(text) < 35:
            continue

        parent = dict(source_item)
        parent["year"] = str(previous_year)
        parent["source_type"] = "previous_cir_final_without_frascati"
        parent["previous_source"] = "local_cir_final"
        section_count += 1

        segments = split_previous_cir_item(parent)
        items.extend(segments or [parent])

    for item in items:
        item["previous_cir_sections_count"] = section_count
        item["previous_cir_segmentation"] = (
            "local_sentence_windows_v157_filtered_scoring_calibrated"
        )
        item["previous_source"] = "local_cir_final"
        item["previous_year"] = str(previous_year)

    return items


def load_previous_cir_memory_items(
    organisme: str,
    project: str,
    current_year: str,
    max_previous_years: int = 3,
    subproject: str = "",
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """
    Charge un véritable CIR précédent du même organisme et du même projet.

    Ordre déterministe :
    1. année N-1 dans Memory V2 ;
    2. année N-2 dans Memory V2 ;
    3. années suivantes jusqu'à max_previous_years ;
    4. même ordre dans l'ancien stockage local.

    Exemple : dossier 2025 -> 2024, sinon 2023.
    La mémoire de style est exclue.
    """
    candidate_years = _candidate_previous_years(
        current_year,
        max_previous_years=max_previous_years,
    )

    # Priorité à Memory V2 factuelle.
    for previous_year in candidate_years:
        items = load_previous_cir_items_from_memory_v2(
            organisme,
            project,
            previous_year,
            subproject=subproject,
        )
        if items:
            return [str(previous_year)], items

    # Fallback ancien stockage local, avec le même ordre N-1 puis N-2.
    for previous_year in candidate_years:
        items = _load_previous_local_items_for_year(
            organisme,
            project,
            previous_year,
            subproject=subproject,
        )
        if items:
            return [str(previous_year)], items

    return [], []



# ============================================================
# V162 - Source courante = verrous regroupés + preuves documentaires
# ============================================================

_GROUPED_VERROU_TITLE_KEYS = (
    "title", "titre", "verrou_title", "label", "name",
    "question_rnd", "technical_lock", "scientific_lock_title",
)

_GROUPED_VERROU_BODY_KEYS = (
    "description", "text", "justification", "difficulty", "difficulte",
    "problem", "probleme", "scientific_uncertainty", "incertitude_scientifique",
    "scientific_lock", "phenomenon", "phenomene", "technical_object",
    "objet_technique", "constraint", "contrainte", "why_not_simple_engineering",
    "evidence_summary", "consultant_context_explanation", "hypothesis",
    "hypothese", "research_question", "question_recherche",
)

_GROUPED_VERROU_EVIDENCE_KEYS = (
    "sources", "evidence", "evidences", "evidence_sources", "source_documents",
    "source_ids", "supporting_passages", "passages", "proofs", "preuves",
    "preuves_sources", "source_evidence", "source_passages", "citations_sources",
)


def _first_text_from_keys(obj: Dict[str, Any], keys: Tuple[str, ...]) -> str:
    if not isinstance(obj, dict):
        return ""
    for key in keys:
        value = obj.get(key)
        if isinstance(value, str) and clean_text(value):
            return clean_text(value)
    return ""


def _collect_text_fragments(value: Any, max_items: int = 30, max_chars: int = 12000) -> List[str]:
    """Extrait des fragments textuels sans sérialiser toute la métadonnée JSON."""
    fragments: List[str] = []
    seen = set()

    def add(txt: Any) -> None:
        s = clean_text(txt)
        if not s:
            return
        key = norm(s)[:500]
        if not key or key in seen:
            return
        seen.add(key)
        fragments.append(s)

    def walk(v: Any, depth: int = 0) -> None:
        if len(fragments) >= max_items or depth > 4:
            return
        if isinstance(v, str):
            add(v)
            return
        if isinstance(v, list):
            for item in v:
                walk(item, depth + 1)
                if len(fragments) >= max_items:
                    break
            return
        if not isinstance(v, dict):
            return

        # Priorité aux champs qui portent réellement du texte ou une source.
        priority_keys = (
            "text", "source_text", "excerpt", "content", "passage", "quote",
            "title", "section_title", "document", "filename", "source_document",
            "description", "justification", "evidence_summary", "scientific_lock",
            "why_not_simple_engineering",
        )
        for key in priority_keys:
            if key in v:
                walk(v.get(key), depth + 1)
        for key in _GROUPED_VERROU_EVIDENCE_KEYS:
            if key in v:
                walk(v.get(key), depth + 1)

    walk(value)

    out: List[str] = []
    total = 0
    for fragment in fragments:
        remaining = max_chars - total
        if remaining <= 0:
            break
        value = fragment if len(fragment) <= remaining else fragment[:remaining]
        out.append(value)
        total += len(value)
    return out


def _source_evidence_record(value: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalise une preuve EnnoDiagnostic sans perdre sa localisation documentaire."""
    if not isinstance(value, dict):
        return None

    metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}

    def pick(*keys: str) -> Any:
        for key in keys:
            direct = value.get(key)
            if direct not in (None, "", [], {}):
                return direct
            nested = metadata.get(key)
            if nested not in (None, "", [], {}):
                return nested
        return None

    text = clean_text(pick("excerpt", "text", "source_text", "content", "passage", "quote"))
    document = clean_text(pick("document", "filename", "source_document", "document_name", "source_file"))
    source_path = clean_text(pick("source_path", "path", "file_path", "document_path"))

    # Une vraie preuve doit au minimum porter un extrait ou une référence de fichier.
    if not text and not document and not source_path:
        return None

    record: Dict[str, Any] = {
        "evidence_id": pick("evidence_id"),
        "rag_chunk_id": pick("rag_chunk_id", "chunk_id"),
        "passage_id": pick("passage_id", "id"),
        "document_id": pick("document_id"),
        "document": document,
        "filename": clean_text(pick("filename")) or document,
        "source_path": source_path,
        "page_number": pick("page_number", "page"),
        "paragraph_index": pick("paragraph_index", "paragraph"),
        "char_start": pick("char_start"),
        "char_end": pick("char_end"),
        "section_title": clean_text(pick("section_title", "section")),
        "role": clean_text(pick("role", "final_role")),
        "excerpt": text,
        "text": text,
        "source_text": text,
        "metadata": metadata or None,
    }

    return {key: val for key, val in record.items() if val not in (None, "", [], {})}


def _collect_source_evidence_records(value: Any, max_items: int = 40) -> List[Dict[str, Any]]:
    """Parcourt les objets de preuve et conserve texte + document + chemin + page."""
    output: List[Dict[str, Any]] = []
    seen = set()

    def add(record: Optional[Dict[str, Any]]) -> None:
        if not record:
            return
        key = (
            norm(record.get("document") or record.get("source_path") or ""),
            norm(record.get("excerpt") or record.get("text") or "")[:500],
            str(record.get("passage_id") or record.get("rag_chunk_id") or ""),
        )
        if key in seen:
            return
        seen.add(key)
        output.append(record)

    def walk(node: Any, depth: int = 0) -> None:
        if len(output) >= max_items or depth > 6:
            return
        if isinstance(node, list):
            for item in node:
                walk(item, depth + 1)
                if len(output) >= max_items:
                    break
            return
        if not isinstance(node, dict):
            return

        record = _source_evidence_record(node)
        # Évite de transformer le verrou lui-même en fausse preuve s'il ne porte
        # ni document ni chemin ni identifiant de passage.
        if record and (
            record.get("document")
            or record.get("source_path")
            or record.get("passage_id")
            or record.get("rag_chunk_id")
        ):
            add(record)

        for key in _GROUPED_VERROU_EVIDENCE_KEYS:
            if key in node:
                walk(node.get(key), depth + 1)
        if isinstance(node.get("source_json"), dict):
            walk(node.get("source_json"), depth + 1)

    walk(value)
    return output


def _best_source_evidence(records: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not records:
        return None

    def score(record: Dict[str, Any]) -> Tuple[int, int]:
        points = 0
        if clean_text(record.get("source_path")):
            points += 40
        if clean_text(record.get("document")):
            points += 30
        if record.get("document_id") not in (None, ""):
            points += 20
        if record.get("page_number") not in (None, ""):
            points += 10
        text = clean_text(record.get("excerpt") or record.get("text"))
        if len(text) >= 80:
            points += 20
        return points, min(len(text), 2000)

    return max(records, key=score)


def grouped_verrous_to_current_items(verrous: Any) -> List[Dict[str, Any]]:
    """Convertit les verrous consolidés EnnoDiagnostic en items comparables.

    La comparaison ne repart plus des passages NLP marqués artificiellement
    role=verrou. Elle utilise le verrou regroupé et ses preuves conservées.
    """
    if not isinstance(verrous, list):
        return []

    out: List[Dict[str, Any]] = []
    seen = set()

    for index, verrou in enumerate(verrous, start=1):
        if isinstance(verrou, str):
            verrou = {"title": f"Verrou {index}", "text": verrou}
        if not isinstance(verrou, dict):
            continue

        source_json = verrou.get("source_json")
        if not isinstance(source_json, dict):
            source_json = {}

        title = (
            _first_text_from_keys(verrou, _GROUPED_VERROU_TITLE_KEYS)
            or _first_text_from_keys(source_json, _GROUPED_VERROU_TITLE_KEYS)
            or f"Verrou R&D candidat {index}"
        )

        body_fragments: List[str] = []
        for obj in (verrou, source_json):
            for key in _GROUPED_VERROU_BODY_KEYS:
                value = obj.get(key)
                if isinstance(value, str) and clean_text(value):
                    body_fragments.append(clean_text(value))

        evidence_values: List[Any] = []
        for obj in (verrou, source_json):
            for key in _GROUPED_VERROU_EVIDENCE_KEYS:
                if key in obj:
                    evidence_values.append(obj.get(key))
        evidence_fragments = _collect_text_fragments(evidence_values, max_items=35, max_chars=14000)

        combined_parts: List[str] = []
        combined_seen = set()
        for part in [title, *body_fragments, *evidence_fragments]:
            cleaned = clean_text(part)
            key = norm(cleaned)[:600]
            if not cleaned or not key or key in combined_seen:
                continue
            combined_seen.add(key)
            combined_parts.append(cleaned)

        full_text = "\n".join(combined_parts).strip()
        if len(full_text) < 35:
            continue

        dedupe_key = norm(title + " " + full_text)[:900]
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        source_evidence = _collect_source_evidence_records(
            {
                "sources": evidence_values,
                "source_json": source_json,
            },
            max_items=40,
        )
        primary_evidence = _best_source_evidence(source_evidence)

        supporting_passages: List[Dict[str, Any]] = []
        if source_evidence:
            for record in source_evidence[:25]:
                enriched = dict(record)
                enriched["source_type"] = "ennodiagnostic_grouped_evidence"
                supporting_passages.append(enriched)
        else:
            supporting_passages = [
                {
                    "text": fragment,
                    "excerpt": fragment,
                    "source_type": "ennodiagnostic_grouped_evidence",
                }
                for fragment in evidence_fragments[:25]
                if clean_text(fragment)
            ]

        display_excerpt = clean_text(
            (primary_evidence or {}).get("excerpt")
            or (primary_evidence or {}).get("text")
            or (evidence_fragments[0] if evidence_fragments else "")
            or (body_fragments[0] if body_fragments else "")
            or full_text
        )

        out.append({
            "id": verrou.get("id") or verrou.get("verrou_id") or f"diagnostic_verrou_{index}",
            "item_id": verrou.get("id") or verrou.get("verrou_id") or f"diagnostic_verrou_{index}",
            "role": "verrou",
            "pack_key": "llm_reformulated_verrous",
            "section_key": "llm_reformulated_verrous",
            "section_type": "verrou_rnd_candidat_regroupe",
            "section_title": title,
            "text": full_text,
            "display_excerpt": display_excerpt,
            "document": clean_text((primary_evidence or {}).get("document")),
            "filename": clean_text((primary_evidence or {}).get("filename")),
            "source_path": clean_text((primary_evidence or {}).get("source_path")),
            "document_id": (primary_evidence or {}).get("document_id"),
            "page_number": (primary_evidence or {}).get("page_number"),
            "paragraph_index": (primary_evidence or {}).get("paragraph_index"),
            "passage_id": (primary_evidence or {}).get("passage_id"),
            "rag_chunk_id": (primary_evidence or {}).get("rag_chunk_id"),
            "source_type": "ennodiagnostic_grouped_verrou",
            "source_evidence": source_evidence,
            "primary_evidence": primary_evidence,
            "supporting_passages": supporting_passages,
            "consultant_status": verrou.get("consultant_status") or "en_attente",
            "original_verrou": verrou,
        })

    return out


def _candidate_year_roots_for_report(
    organisme: str,
    project: str,
    year: str,
    subproject: str = "",
) -> List[Path]:
    roots: List[Path] = []
    seen = set()

    try:
        nlp_path = resolve_current_nlp_result_path(
            organisme,
            project,
            year,
            subproject=subproject,
            required=False,
        )
        if nlp_path is not None:
            # .../years/<year>/nlp/nlp_result.json -> .../years/<year>
            roots.append(nlp_path.parent.parent)
    except Exception:
        pass

    roots.append(
        year_dir(
            organisme,
            project,
            year,
            subproject=subproject,
        )
    )
    if not clean_text(subproject):
        roots.extend([
            STORAGE_DIR / str(organisme) / "projects" / str(project) / "years" / str(year),
            STORAGE_DIR / slug(organisme) / "projects" / slug(project).replace("-", "_") / "years" / str(year),
        ])

    out: List[Path] = []
    for root in roots:
        try:
            key = str(root.resolve()).lower()
        except Exception:
            key = str(root).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(root)
    return out


def load_grouped_verrous_from_ennodiagnostic_report(
    organisme: str,
    project: str,
    year: str,
    subproject: str = "",
) -> Tuple[List[Dict[str, Any]], Optional[Path]]:
    """Fallback pour la route /compare-current appelée après EnnoDiagnostic."""
    candidates: List[Path] = []
    for root in _candidate_year_roots_for_report(
        organisme,
        project,
        year,
        subproject=subproject,
    ):
        candidates.extend([
            root / "ennodiagnostic" / "ennodiagnostic_report.json",
            root / "ennodiagnostic_report.json",
        ])

    for path in candidates:
        report = read_json(path, {})
        if not isinstance(report, dict) or not report:
            continue

        arrays = [
            report.get("llm_reformulated_verrous"),
            report.get("consultant_verrous_cir"),
            (report.get("diagnostic") or {}).get("llm_reformulated_verrous")
                if isinstance(report.get("diagnostic"), dict) else None,
            (report.get("static_diagnostic") or {}).get("llm_reformulated_verrous")
                if isinstance(report.get("static_diagnostic"), dict) else None,
        ]
        for value in arrays:
            items = grouped_verrous_to_current_items(value)
            if items:
                return items, path

    return [], None


# ============================================================
# V162 - présélection légère avant score complet
# ============================================================

def build_previous_fast_index(previous_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    index: List[Dict[str, Any]] = []
    for previous in previous_items or []:
        if not isinstance(previous, dict):
            continue
        txt = clean_text(previous.get("text") or "")
        if not txt:
            continue
        index.append({
            "item": previous,
            "words": set(w for w in words(txt, 65) if not is_too_generic_keyword(w)),
            "themes": set(themes(txt)),
            "numbers": set(numbers(txt)),
            "role": clean_text(previous.get("role")).lower(),
            "priority": float(previous.get("previous_section_priority") or 0.0),
        })
    return index


def _fast_preselection_score_from_features(
    current_words: set,
    current_themes: set,
    current_numbers: set,
    current_role: str,
    previous_feature: Dict[str, Any],
) -> float:
    previous_words = previous_feature.get("words") or set()
    previous_themes = previous_feature.get("themes") or set()
    previous_numbers = previous_feature.get("numbers") or set()

    keyword_coverage = 0.0
    keyword_jaccard = 0.0
    if current_words and previous_words:
        intersection = len(current_words & previous_words)
        keyword_coverage = intersection / max(1, min(len(current_words), len(previous_words)))
        keyword_jaccard = intersection / max(1, len(current_words | previous_words))

    theme_score = 0.0
    if current_themes and previous_themes:
        theme_score = len(current_themes & previous_themes) / max(1, len(current_themes | previous_themes))

    number_score = 0.0
    if current_numbers and previous_numbers:
        number_score = len(current_numbers & previous_numbers) / max(1, len(current_numbers | previous_numbers))

    role_bonus = 0.04 if current_role and current_role == previous_feature.get("role") else 0.0
    priority_bonus = min(0.03, float(previous_feature.get("priority") or 0.0) / 4000.0)

    return (
        0.36 * keyword_coverage
        + 0.24 * keyword_jaccard
        + 0.28 * theme_score
        + 0.08 * number_score
        + role_bonus
        + priority_bonus
    )


def shortlist_previous_items(
    current: Dict[str, Any],
    previous_fast_index: List[Dict[str, Any]],
    shortlist_size: int,
) -> List[Dict[str, Any]]:
    current_text = expanded_current_text(current)
    current_words = set(w for w in words(current_text, 75) if not is_too_generic_keyword(w))
    current_themes = set(themes(current_text))
    current_numbers = set(numbers(current_text))
    current_role = clean_text(current.get("role")).lower()

    scored: List[Tuple[float, Dict[str, Any]]] = []
    for feature in previous_fast_index:
        score = _fast_preselection_score_from_features(
            current_words,
            current_themes,
            current_numbers,
            current_role,
            feature,
        )
        scored.append((score, feature.get("item") or {}))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    size = max(1, min(int(shortlist_size), len(scored))) if scored else 0
    return [item for _, item in scored[:size] if isinstance(item, dict)]

def compare_one(
    current: Dict[str, Any],
    previous_items: List[Dict[str, Any]],
    top_k: int = 3,
    shortlist_size: int = 120,
    previous_fast_index: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    fast_index = previous_fast_index or build_previous_fast_index(previous_items)
    shortlisted = shortlist_previous_items(
        current=current,
        previous_fast_index=fast_index,
        shortlist_size=max(top_k, shortlist_size),
    )

    # Garde-fou : si l'index est vide, conserver le comportement historique.
    candidates_to_score = shortlisted or list(previous_items or [])[:max(top_k, shortlist_size)]

    scored: List[Dict[str, Any]] = []
    for prev in candidates_to_score:
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
        "preselection": {
            "version": "v162_fast_keyword_theme_number_shortlist",
            "previous_items_total": len(previous_items or []),
            "shortlist_size_requested": int(shortlist_size),
            "shortlist_items_scored": len(candidates_to_score),
            "full_scores_avoided": max(0, len(previous_items or []) - len(candidates_to_score)),
        },
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
    current_verrous: Optional[List[Dict[str, Any]]] = None,
    shortlist_size: Optional[int] = None,
    subproject: str = "",
    previous_memory: Optional[Tuple[List[str], List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    if nlp_result_path:
        nlp_path = Path(nlp_result_path).expanduser().resolve()
        if not nlp_path.exists() or not nlp_path.is_file():
            raise FileNotFoundError(f"NLP courant introuvable : {nlp_path}")
    else:
        resolved_nlp_path = resolve_current_nlp_result_path(
            organisme=organisme,
            project=project,
            year=year,
            subproject=subproject,
            required=True,
        )
        if resolved_nlp_path is None:
            raise FileNotFoundError("nlp_result.json courant introuvable.")
        nlp_path = resolved_nlp_path

    print(f"[CIR_MEMORY][V162] NLP courant utilisé : {nlp_path}", flush=True)

    nlp = read_json(nlp_path, {})
    current_pack, nlp_pack_source = get_current_raw_pack_with_frascati(nlp)

    explicit_current_verrous = current_verrous is not None
    grouped_items = grouped_verrous_to_current_items(current_verrous)
    grouped_report_path: Optional[Path] = None
    current_source = "argument_current_verrous" if grouped_items else ""

    # La route dédiée n'envoie pas forcément la liste : elle peut alors relire
    # le rapport sauvegardé. Pendant un nouveau diagnostic, une liste explicite
    # vide ne doit jamais réutiliser silencieusement les verrous d'un ancien run.
    if not grouped_items and not explicit_current_verrous:
        grouped_items, grouped_report_path = load_grouped_verrous_from_ennodiagnostic_report(
            organisme=organisme,
            project=project,
            year=year,
            subproject=subproject,
        )
        if grouped_items:
            current_source = "ennodiagnostic_report.llm_reformulated_verrous"

    if grouped_items:
        raw_current_items = list(grouped_items)
        eligible_current_items = list(grouped_items)
        current_items = list(grouped_items)
        current_pack_source = current_source
        fallback_to_nlp_role_verrou = False
    elif explicit_current_verrous:
        # Le synthétiseur n'a produit aucun verrou exploitable : ne pas comparer
        # des titres de sections NLP à la place et ne pas réutiliser un ancien run.
        raw_current_items = []
        eligible_current_items = []
        current_items = []
        current_pack_source = "argument_current_verrous_empty"
        fallback_to_nlp_role_verrou = False
    else:
        raw_current_items = pack_to_items(
            current_pack,
            source_type="current_raw_with_frascati",
        )
        eligible_current_items = [
            x for x in raw_current_items
            if current_item_should_be_compared(x)
        ]
        current_items = [
            x for x in eligible_current_items
            if clean_text(x.get("role")).lower() == "verrou"
        ]
        current_pack_source = nlp_pack_source
        fallback_to_nlp_role_verrou = True

    print(
        f"[CIR_MEMORY][V162] source verrous courants={current_pack_source} | "
        f"raw={len(raw_current_items)} | éligibles={len(eligible_current_items)} | "
        f"verrous_comparés={len(current_items)}",
        flush=True,
    )
    if fallback_to_nlp_role_verrou:
        print(
            "[CIR_MEMORY][V162][WARN] Aucun verrou regroupé EnnoDiagnostic trouvé ; "
            "fallback sur les items NLP role=verrou.",
            flush=True,
        )

    previous_years, previous_items = previous_memory if previous_memory is not None else load_previous_cir_memory_items(
        organisme=organisme,
        project=project,
        current_year=year,
        max_previous_years=max_previous_years,
        subproject=subproject,
    )

    if shortlist_size is None:
        try:
            shortlist_size = int(__import__("os").getenv("ENNOSMART_CIR_MEMORY_SHORTLIST_SIZE", "120"))
        except Exception:
            shortlist_size = 120
    shortlist_size = max(top_k, min(int(shortlist_size), 500))

    base_summary = {
        "raw_current_items_count": len(raw_current_items),
        "eligible_current_items_count": len(eligible_current_items),
        "current_items_count": len(current_items),
        "current_verrous_count": len(current_items),
        "current_verrous_source": current_pack_source,
        "grouped_verrous_used": bool(grouped_items),
        "grouped_verrous_report_path": str(grouped_report_path) if grouped_report_path else None,
        "fallback_to_nlp_role_verrou": fallback_to_nlp_role_verrou,
        "non_verrou_items_skipped_count": (
            len(eligible_current_items) - len(current_items)
            if fallback_to_nlp_role_verrou else 0
        ),
        "filtered_current_noise_count": (
            len(raw_current_items) - len(eligible_current_items)
            if fallback_to_nlp_role_verrou else 0
        ),
        "filter_version": "v162_grouped_verrous_source_highlight",
        "shortlist_size": shortlist_size,
    }

    if not previous_items:
        report = {
            "ok": True,
            "version": "cir_memory_v162_grouped_verrous_source_highlight",
            "has_previous_cir": False,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "organisme": organisme,
            "project": project,
            "subproject": subproject or None,
            "current_year": str(year),
            "nlp_result_path": str(nlp_path),
            "current_pack_source": current_pack_source,
            "summary": {
                **base_summary,
                "previous_cir_items_count": 0,
                "project_novelty_score": None,
                "frascati_context_signal": "no_previous_cir",
                "previous_cir_rule": "Même organisme/projet : N-1 Memory V2, sinon N-2/N-3, puis fallback local.",
            },
            "previous_cir_available": False,
            "previous_cir_years_used": [],
            "previous_years": [],
            "registered_previous_cirs": [],
            "comparisons": [],
            "verrou_comparisons": [],
        }
    else:
        comparisons: List[Dict[str, Any]] = []
        comparison_started_at = time.perf_counter()
        previous_fast_index = build_previous_fast_index(previous_items)

        for index, current_item in enumerate(current_items, start=1):
            title = truncate(
                current_item.get("section_title")
                or current_item.get("title")
                or current_item.get("text")
                or f"Verrou {index}",
                110,
            )
            print(
                f"[CIR_MEMORY][V162] comparaison verrou {index}/{len(current_items)} : {title}",
                flush=True,
            )
            item_started_at = time.perf_counter()
            comparison = compare_one(
                current=current_item,
                previous_items=previous_items,
                top_k=top_k,
                shortlist_size=shortlist_size,
                previous_fast_index=previous_fast_index,
            )
            comparison["comparison_elapsed_seconds"] = round(
                time.perf_counter() - item_started_at,
                3,
            )
            comparisons.append(comparison)

        comparison_elapsed_seconds = round(
            time.perf_counter() - comparison_started_at,
            3,
        )
        full_pairs_theoretical = len(current_items) * len(previous_items)
        full_pairs_scored = sum(
            int((c.get("preselection") or {}).get("shortlist_items_scored") or 0)
            for c in comparisons
        )

        print(
            f"[CIR_MEMORY][V162] comparaison terminée : {len(comparisons)} verrou(s) "
            f"en {comparison_elapsed_seconds}s | paires complètes={full_pairs_scored}/"
            f"{full_pairs_theoretical}",
            flush=True,
        )

        summ = summarize(comparisons)
        previous_sections_count = int(previous_items[0].get("previous_cir_sections_count") or 0)

        report = {
            "ok": True,
            "version": "cir_memory_v162_grouped_verrous_source_highlight",
            "has_previous_cir": True,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "organisme": organisme,
            "project": project,
            "subproject": subproject or None,
            "current_year": str(year),
            "nlp_result_path": str(nlp_path),
            "previous_cir_years_used": previous_years,
            "current_pack_source": current_pack_source,
            "previous_cir_rule": "Même organisme/projet : N-1 dans Memory V2, sinon N-2/N-3. Cartes de style exclues.",
            "previous_cir_source": previous_items[0].get("previous_source") if previous_items else None,
            "previous_cir_available": True,
            "previous_years": previous_years,
            "registered_previous_cirs": [
                {
                    "year": y,
                    "source": previous_items[0].get("previous_source") if previous_items else None,
                    "items_count": len(previous_items),
                }
                for y in previous_years
            ],
            "previous_cir_year_used": previous_years[0] if previous_years else None,
            "previous_cir_segmentation": "section_to_sentence_windows_v162_fast_shortlist",
            "summary": {
                **base_summary,
                "previous_cir_sections_count": previous_sections_count,
                "previous_cir_items_count": len(previous_items),
                "previous_fast_index_count": len(previous_fast_index),
                "comparison_elapsed_seconds": comparison_elapsed_seconds,
                "full_pairs_theoretical": full_pairs_theoretical,
                "full_pairs_scored_after_preselection": full_pairs_scored,
                "full_pairs_avoided": max(0, full_pairs_theoretical - full_pairs_scored),
                **summ,
            },
            "comparisons": comparisons,
            "verrou_comparisons": comparisons,
            "new_or_not_found": [
                c for c in comparisons
                if (c.get("decision") or {}).get("status") == "new_or_not_found"
            ],
            "evolution_or_partial_continuity": [
                c for c in comparisons
                if (c.get("decision") or {}).get("status") == "evolution_or_partial_continuity"
            ],
            "continuity_strong": [
                c for c in comparisons
                if (c.get("decision") or {}).get("status") == "continuity_strong"
            ],
        }

    out_path = comparison_report_path(
        organisme,
        project,
        year,
        subproject=subproject,
    )
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
