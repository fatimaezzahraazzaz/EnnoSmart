# -*- coding: utf-8 -*-
from __future__ import annotations

"""
EnnoDiagnostic Agent - V189 groupes NLP transmis sans regroupement aval

Architecture respectée :
- Les groupes de preuves NLP qualifiés sont lus directement depuis nlp_result.json en complément de Chroma.
- Le score Frascati est calculé en amont par le NLP / Frascati. Le JSON NLP est
  la source officielle ; les métadonnées RAG/Chroma servent de contrôle.
- Le regroupement des verrous est effectué une seule fois dans le NLP, avant
  Frascati ; le RAG et cet agent conservent chaque ``lock_group_id`` tel quel.
- Cet agent ne recalcule jamais le score Frascati.
- Le LLM sert uniquement à reformuler le diagnostic et à justifier le score à partir des preuves du projet.
- La mémoire de style et les projets similaires ne servent jamais de preuve factuelle.
- La comparaison CIR précédent est injectée uniquement dans une section dédiée,
  sous forme structurée. Elle ne peut jamais créer un fait ou un verrou courant.
"""

import hashlib
import json
import os
import re
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# =========================================================
# Constantes vocabulaire métier
# =========================================================

# Dans EnnoDiagnostic, on ne valide pas encore des verrous CIR.
# On détecte des signaux / candidats, qui seront ensuite filtrés par le consultant
# puis confrontés à l’état de l’art dans EnnoScholar.
SIGNAL_SECTION_TITLE = "Signaux de verrous R&D candidats"
LEGACY_SIGNAL_SECTION_TITLE = "Verrous R&D / signaux de verrous"

MEMORY_V2_SECTION_TITLE = "Mémoire V2"
CIR_PREVIOUS_SECTION_TITLE = "Continuité avec le CIR N-1"


def _clean_part_for_path(value: Any) -> str:
    return str(value or "").strip().replace("-", "_").replace(" ", "_")


def _candidate_unique(values: List[str]) -> List[str]:
    out: List[str] = []
    for v in values:
        v = str(v or "").strip()
        if v and v not in out:
            out.append(v)
    return out


def _path_match_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _resolve_ennosmart_year_root(organisme: str, project: str, year: str) -> Path:
    """Résout génériquement le dossier annuel d'un projet EnnoSmart.

    La résolution recherche d'abord les répertoires existants sans dépendre de
    la casse, des espaces, des tirets ou des underscores. Si aucun chemin
    n'existe encore, elle construit un chemin canonique à partir des valeurs
    reçues, sans règle propre à un organisme ou à un projet.
    """
    root = Path(os.getenv("ENNOSMART_ROOT", r"C:\EnnoSmart"))
    storage = root / "storage" / "organismes"
    year_value = str(year)

    exact = storage / str(organisme).strip() / "projects" / _clean_part_for_path(project) / "years" / year_value
    if exact.exists():
        return exact

    org_key = _path_match_key(organisme)
    project_key = _path_match_key(project)
    if storage.exists():
        for org_dir in storage.iterdir():
            if not org_dir.is_dir() or _path_match_key(org_dir.name) != org_key:
                continue
            projects_dir = org_dir / "projects"
            if not projects_dir.exists():
                continue
            for project_dir in projects_dir.iterdir():
                if not project_dir.is_dir() or _path_match_key(project_dir.name) != project_key:
                    continue
                candidate = project_dir / "years" / year_value
                if candidate.exists():
                    return candidate

    org_default = str(organisme or "unknown_organisme").strip() or "unknown_organisme"
    project_default = _clean_part_for_path(project) or "unknown_project"
    return storage / org_default / "projects" / project_default / "years" / year_value


# =========================================================
# Utils
# =========================================================

def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def sha256_file(path: Path) -> str:
    """Calcule une empreinte SHA-256 stable pour un fichier."""
    try:
        if not path.exists() or not path.is_file():
            return ""
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def sha256_text(value: str) -> str:
    """Calcule une empreinte SHA-256 stable pour une chaîne."""
    return hashlib.sha256(str(value or "").encode("utf-8", errors="ignore")).hexdigest()


def safe_read_json(path: Path) -> Dict[str, Any]:
    """Lit un JSON sans bloquer le diagnostic si le fichier est absent ou invalide."""
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def clean_text(text: Any) -> str:
    return str(text or "").strip()


def repair_mojibake(text: Any) -> str:
    s = clean_text(text)
    if not s:
        return ""

    markers = ("Ã", "Â", "â€™", "â€“", "â€œ", "â€")
    if not any(m in s for m in markers):
        return s

    replacements = {
        "Ã©": "é",
        "Ã¨": "è",
        "Ãª": "ê",
        "Ã«": "ë",
        "Ã ": "à",
        "Ã¢": "â",
        "Ã§": "ç",
        "Ã´": "ô",
        "Ã¹": "ù",
        "Ã»": "û",
        "Ã®": "î",
        "Ã¯": "ï",
        "Ã‰": "É",
        "â€™": "’",
        "â€œ": "“",
        "â€": "”",
        "â€“": "–",
        "â€”": "—",
    }
    fixed = s
    for bad, good in replacements.items():
        fixed = fixed.replace(bad, good)

    try:
        latin_fixed = s.encode("latin1", errors="ignore").decode("utf-8", errors="ignore")
        if sum(latin_fixed.count(m) for m in markers) < sum(fixed.count(m) for m in markers):
            fixed = latin_fixed
    except Exception:
        pass

    return fixed


def truncate(text: Any, max_chars: int = 700) -> str:
    text = clean_text(text)
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def compact_demarche_audit(value: Any) -> Dict[str, Any]:
    """Conserve uniquement le verdict utile au diagnostic et au prompt LLM."""
    audit = value if isinstance(value, dict) else {}
    keys = (
        "version",
        "label",
        "readability_score",
        "readability_score_semantics",
        "method_steps_count",
        "research_justified_steps_count",
        "routine_engineering_steps_count",
        "unexplained_steps_count",
        "redundant_steps_count",
        "groups_with_possible_direct_final_solution_shortcut",
        "direct_final_solution_risk",
        "eligibility_impact",
        "llm_review_recommended",
        "llm_review_reasons",
        "llm_policy",
        "questions_to_ask",
        "human_validation_required",
    )
    return {key: audit.get(key) for key in keys if key in audit}


def extract_markdown_section(content: str, title: str) -> str:
    content = repair_mojibake(content)
    if not content:
        return ""

    pattern = re.compile(
        r"^##\s+" + re.escape(title).replace(r"\ ", r"\s+") + r"\s*$([\s\S]*?)(?=^##\s+|\Z)",
        re.IGNORECASE | re.MULTILINE,
    )
    match = pattern.search(content)
    return match.group(1).strip() if match else ""


def insert_markdown_section_after(content: str, after_title: str, new_section: str) -> str:
    content = repair_mojibake(content)
    new_section = repair_mojibake(new_section).strip()
    if not new_section:
        return content

    pattern = re.compile(
        r"(^##\s+" + re.escape(after_title).replace(r"\ ", r"\s+") + r"\s*$[\s\S]*?)(?=^##\s+|\Z)",
        re.IGNORECASE | re.MULTILINE,
    )
    match = pattern.search(content)

    if not match:
        return f"{new_section}\n\n{content}".strip()

    insert_at = match.end(1)
    return (content[:insert_at].rstrip() + "\n\n" + new_section + "\n\n" + content[insert_at:].lstrip()).strip()


def replace_or_insert_markdown_section(
    content: str,
    title: str,
    new_section: str,
    after_title: str = "Lecture Frascati du dossier",
) -> str:
    content = repair_mojibake(content)
    new_section = repair_mojibake(new_section).strip()

    if not new_section:
        return content

    if not re.match(r"^##\s+", new_section):
        new_section = f"## {title}\n{new_section}"

    pattern = re.compile(
        r"(^##\s+" + re.escape(title).replace(r"\ ", r"\s+") + r"\s*$[\s\S]*?)(?=^##\s+|\Z)",
        re.IGNORECASE | re.MULTILINE,
    )

    if pattern.search(content):
        return pattern.sub(new_section.rstrip() + "\n\n", content, count=1).strip()

    return insert_markdown_section_after(content, after_title=after_title, new_section=new_section)


def build_diagnostic_sections(content: str) -> Dict[str, str]:
    titles = [
        "Analyse Frascati",
        "Lecture Frascati du dossier",
        "Justification Frascati du score",
        MEMORY_V2_SECTION_TITLE,
        CIR_PREVIOUS_SECTION_TITLE,
        "Synthèse stratégique du projet",
        "Objectif global reformulé",
        SIGNAL_SECTION_TITLE,
        LEGACY_SIGNAL_SECTION_TITLE,
        "Démarche expérimentale détectée",
        "Résultats et métriques disponibles",
        "Paramètres et contraintes techniques",
        "Points à valider par le consultant",
    ]

    sections = {title: extract_markdown_section(content, title) for title in titles}

    # Compatibilité frontend : l’ancien frontend peut encore chercher
    # "Verrous R&D / signaux de verrous". On lui donne le même contenu,
    # mais le titre métier canonique reste "Signaux de verrous R&D candidats".
    signal_text = sections.get(SIGNAL_SECTION_TITLE) or sections.get(LEGACY_SIGNAL_SECTION_TITLE) or ""
    if signal_text:
        sections[SIGNAL_SECTION_TITLE] = signal_text
        sections[LEGACY_SIGNAL_SECTION_TITLE] = signal_text

    # Compatibilité : le frontend moderne affiche une seule section
    # « Analyse Frascati », composée de la lecture du score et de la
    # justification projet-spécifique générée par le LLM.
    analysis_text = sections.get("Analyse Frascati") or ""
    if not analysis_text:
        reading = sections.get("Lecture Frascati du dossier") or ""
        justification = sections.get("Justification Frascati du score") or ""
        analysis_text = "\n\n".join(
            part for part in [
                f"Lecture du score\n\n{reading}" if reading else "",
                f"Justification projet-spécifique\n\n{justification}" if justification else "",
            ]
            if part
        )
    if analysis_text:
        sections["Analyse Frascati"] = analysis_text

    return sections


def normalize_report_vocabulary(content: str) -> str:
    """
    Corrige le vocabulaire du rapport pour éviter de présenter comme validés
    des éléments qui ne sont encore que des signaux candidats avant EnnoScholar.
    """
    content = repair_mojibake(content)
    if not content:
        return ""

    replacements = {
        "## Verrous CIR consolidés": f"## {SIGNAL_SECTION_TITLE}",
        "Verrous CIR consolidés": "Signaux de verrous R&D candidats",
        "Verrou identifié": "Signal candidat détecté",
        "Verrous identifiés": "Signaux candidats détectés",
        "Nature du verrou": "Hypothèse de verrou",
        "verrou CIR validé": "signal candidat à confirmer",
        "verrous CIR validés": "signaux candidats à confirmer",
        "verrou scientifiquement défendable": "signal à confirmer par EnnoScholar",
    }

    for old, new in replacements.items():
        content = content.replace(old, new)

    return content


def meta_of(src: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(src, dict):
        return {}
    meta = src.get("metadata")
    return meta if isinstance(meta, dict) else {}


def source_text(src: Dict[str, Any]) -> str:
    if not isinstance(src, dict):
        return ""
    return clean_text(
        src.get("text")
        or src.get("source_text")
        or src.get("content")
        or src.get("excerpt")
        or ""
    )


def source_doc(src: Dict[str, Any]) -> str:
    if not isinstance(src, dict):
        return ""
    meta = meta_of(src)
    return clean_text(
        meta.get("document")
        or meta.get("filename")
        or meta.get("source_name")
        or src.get("document")
        or src.get("filename")
        or src.get("source_name")
        or ""
    )


def source_path(src: Dict[str, Any]) -> str:
    if not isinstance(src, dict):
        return ""
    meta = meta_of(src)
    return clean_text(
        meta.get("source_path")
        or meta.get("path")
        or src.get("source_path")
        or src.get("path")
        or ""
    )


def dedupe_sources(sources: List[Dict[str, Any]], max_items: int = 30) -> List[Dict[str, Any]]:
    seen = set()
    out: List[Dict[str, Any]] = []
    positions: Dict[Any, int] = {}

    def group_source_priority(source: Dict[str, Any]) -> int:
        meta = meta_of(source)
        if clean_text(meta.get("source_type")) == "nlp_result_direct_group":
            return 3
        if clean_text(meta.get("chunk_level")) == "nlp_main_item":
            return 2
        if bool(meta.get("is_supporting_passage")):
            return 0
        return 1

    for src in sources or []:
        if not isinstance(src, dict):
            continue

        txt = source_text(src)
        if not txt:
            continue

        meta = meta_of(src)
        lock_group_id = clean_text(meta.get("lock_group_id"))
        key = (
            ("nlp_lock_group", lock_group_id)
            if lock_group_id
            else (source_doc(src), clean_text(meta.get("role")), txt[:250])
        )
        if key in seen:
            if lock_group_id:
                position = positions.get(key)
                if (
                    position is not None
                    and group_source_priority(src) > group_source_priority(out[position])
                ):
                    out[position] = src
            continue

        seen.add(key)
        positions[key] = len(out)
        out.append(src)

    return out[:max_items]




def is_universal_reconstruction_source(src: Dict[str, Any]) -> bool:
    """Détecte les catégories transverses reconstruites, sans logique métier/projet."""
    if not isinstance(src, dict):
        return False
    meta = meta_of(src)
    blob = " ".join([
        clean_text(meta.get("verrou_source")),
        clean_text(meta.get("final_role")),
        clean_text(meta.get("theme_id")),
        clean_text(meta.get("rag_chunk_id")),
        source_text(src)[:220],
    ]).lower()
    return (
        "universal_theme_reconstruction" in blob
        or "verrou_implicit_universal" in blob
        or blob.strip().startswith("verrou implicite possible")
    )


def rank_sources_for_agent(sources: List[Dict[str, Any]], max_items: int = 30) -> List[Dict[str, Any]]:
    """
    Classe les sources pour donner priorité aux preuves NLP spécifiques.
    Les catégories universelles restent disponibles, mais seulement en secours.
    """
    deduped = dedupe_sources(sources or [], max_items=max_items * 4)

    def score(src: Dict[str, Any]) -> float:
        meta = meta_of(src)
        value = 0.0
        if not is_universal_reconstruction_source(src):
            value += 100.0
        if clean_text(meta.get("pack_key")) == "verrous_rnd_locaux":
            value += 25.0
        if clean_text(meta.get("role")) == "verrou":
            value += 10.0
        if clean_text(meta.get("source_type")) == "nlp_result_direct_group":
            value += 50.0
        if clean_text(meta.get("chunk_level")) == "nlp_main_item":
            value += 8.0
        elif bool(meta.get("is_supporting_passage")):
            value -= 8.0
        for key, weight in [("rank_score", 2.0), ("confidence", 1.0), ("verrou_score", 1.5), ("frascati_score", 1.0)]:
            try:
                value += float(meta.get(key) or 0) * weight
            except Exception:
                pass
        value += min(len(source_text(src)), 900) / 9000.0
        return value

    return sorted(deduped, key=score, reverse=True)[:max_items]


def merge_ranked_sources(*groups: List[Dict[str, Any]], max_items: int = 30) -> List[Dict[str, Any]]:
    combined: List[Dict[str, Any]] = []
    for group in groups:
        if isinstance(group, list):
            combined.extend(group)
    return rank_sources_for_agent(combined, max_items=max_items)


# =====================================================
# V130 - Sélection réelle des sources par l'agent
# =====================================================
# Objectif : corriger la cause, pas seulement l'affichage.
# L'agent enrichit les sources transmises au LLM avec des passages objectifs/verrous
# récupérés dans tous les rôles Chroma. Aucune règle projet/SAR/ATR n'est codée :
# les termes sont génériques (objectif, évaluer, limite, incertitude, etc.) et les
# termes métier restent ceux des sources.

_DIAG_OBJECTIVE_MARKERS = [
    "objectif", "but", "vise", "visent", "finalité", "finalite", "évaluer", "evaluer",
    "qualifier", "valider", "démontrer", "demontrer", "comparer", "mesurer",
    "étudier", "etudier", "capacité", "capacite", "afin de", "permettre de",
    "chercher à", "chercher a", "enjeu", "besoin",
]

_DIAG_LOCK_MARKERS = [
    "incertitude", "difficulté", "difficulte", "limite", "limites", "non démontré",
    "non demontre", "à vérifier", "a verifier", "reste à", "reste a", "écart", "ecart",
    "non maîtrisé", "non maitrise", "représentativité", "representativite",
    "transposabilité", "transposabilite", "généralisation", "generalisation",
    "robustesse", "sensibilité", "sensibilite", "influence des paramètres",
    "influence des parametres", "compromis", "conditions réelles", "conditions reelles",
]

_DIAG_DOC_NOISE_MARKERS = [
    "table des matières", "table des matieres", "table des illustrations", "glossaire",
    "identification", "documents applicables", "version", "page", "figure ", "référence |",
]

# V131 : ces marqueurs restent génériques. Ils servent à distinguer un objectif/problème
# de niveau projet d'une simple validation locale, d'une table ou d'un résultat isolé.
_DIAG_PROJECT_LEVEL_MARKERS = [
    "but de cette étude", "but de cette etude", "objectif global", "finalité", "finalite",
    "évaluer les capacités", "evaluer les capacites", "procédure d'évaluation", "procedure d evaluation",
    "comparer", "comparables", "scénario cible", "scenario cible", "jeux d'entrainement",
    "jeu de donnée", "jeu de donne", "modèles", "modeles", "performances",
    "généralisation", "generalisation", "données synthétiques", "donnees synthetiques",
]

_DIAG_LOCAL_PROOF_MARKERS = [
    "validation unitaire", "bon fonctionnement", "méthodologie", "methodologie",
    "les figures", "figure", "tableau", "table des", "bonne correspondance",
    "valeurs théoriques", "valeurs theoriques", "comparaison aux résultats",
    "comparaison aux resultats", "résultats montrent", "resultats montrent", "on observe",
]

_DIAG_STRONG_LOCK_MARKERS = [
    "incertitude", "difficulté", "difficulte", "limite", "limites", "écart", "ecart",
    "non démontré", "non demontre", "à vérifier", "a verifier", "reste à", "reste a",
    "ne présume pas", "ne presume pas", "différences visibles", "differences visibles",
    "marges d'amélioration", "marges d amelioration", "lourd", "lourde", "impératif", "imperatif",
    "représentativité", "representativite", "transposabilité", "transposabilite",
    "généralisation", "generalisation", "conditions réelles", "conditions reelles",
]


def _diag_norm(value: Any) -> str:
    try:
        txt = repair_mojibake(value).lower()
    except Exception:
        txt = str(value or "").lower()
    table = str.maketrans("àâäéèêëîïôöùûüç’'", "aaaeeeeiioouuuc__")
    txt = txt.translate(table)
    txt = re.sub(r"[^a-z0-9]+", " ", txt)
    return re.sub(r"\s+", " ", txt).strip()


def _diag_marker_count(text: Any, markers: List[str]) -> int:
    key = _diag_norm(text)
    return sum(1 for m in markers if _diag_norm(m) in key)


def _diag_blob(src: Dict[str, Any]) -> str:
    meta = meta_of(src)
    return "\n".join([
        source_text(src),
        clean_text(meta.get("section_title") or meta.get("section")),
        clean_text(meta.get("theme_label")),
        clean_text(meta.get("technical_signature")),
        clean_text(meta.get("role") or meta.get("final_role")),
        clean_text(meta.get("pack_key")),
        source_doc(src),
    ])


def _diag_has_noise(src: Dict[str, Any]) -> bool:
    return _diag_marker_count(_diag_blob(src)[:800], _DIAG_DOC_NOISE_MARKERS) > 0


def _diag_objective_score(src: Dict[str, Any]) -> float:
    meta = meta_of(src)
    blob = _diag_blob(src)
    role = _diag_norm(meta.get("role") or meta.get("final_role") or src.get("role"))
    pack = _diag_norm(meta.get("pack_key") or src.get("pack_key"))
    score = 0.0
    score += 3.0 * _diag_marker_count(blob, _DIAG_OBJECTIVE_MARKERS)
    score += 7.0 * _diag_marker_count(blob, _DIAG_PROJECT_LEVEL_MARKERS)
    if "objectif" in role or "objectif" in pack:
        score += 10.0
    if any(x in role or x in pack for x in ["methode", "resultat", "limite", "contribution"]):
        score += 3.0
    try:
        score += float(meta.get("rank_score") or 0) * 1.5
        score += float(meta.get("confidence") or 0) * 1.0
    except Exception:
        pass
    text = source_text(src)
    if len(text) > 120:
        score += min(len(text), 1200) / 260.0
    # Ne pas laisser une validation locale/unitaire écraser le vrai objectif projet.
    if _diag_marker_count(blob, _DIAG_LOCAL_PROOF_MARKERS) and not _diag_marker_count(blob, _DIAG_PROJECT_LEVEL_MARKERS):
        score -= 10.0
    if _diag_has_noise(src):
        score -= 10.0
    if is_universal_reconstruction_source(src):
        score -= 16.0
    return score


def _diag_lock_score(src: Dict[str, Any]) -> float:
    meta = meta_of(src)
    blob = _diag_blob(src)
    role = _diag_norm(meta.get("role") or meta.get("final_role") or src.get("role"))
    pack = _diag_norm(meta.get("pack_key") or src.get("pack_key"))
    score = 0.0
    lock_count = _diag_marker_count(blob, _DIAG_LOCK_MARKERS)
    strong_lock_count = _diag_marker_count(blob, _DIAG_STRONG_LOCK_MARKERS)
    local_proof_count = _diag_marker_count(blob, _DIAG_LOCAL_PROOF_MARKERS)
    score += 4.0 * lock_count
    score += 5.0 * strong_lock_count
    if "verrou" in role or "verrou" in pack:
        score += 14.0
    if "limite" in role or "limite" in pack:
        score += 12.0
    if any(x in role or x in pack for x in ["methode", "resultat", "parametre"]):
        score += 1.0
    try:
        score += float(meta.get("frascati_score") or 0) * 4.0
        score += float(meta.get("verrou_score") or 0) * 4.0
        score += float(meta.get("rank_score") or 0) * 1.5
    except Exception:
        pass
    # Une preuve locale ou un résultat positif seul ne doit pas devenir verrou.
    if local_proof_count and strong_lock_count == 0:
        score -= 14.0
    if _diag_has_noise(src):
        score -= 12.0
    if is_universal_reconstruction_source(src):
        score -= 22.0
    return score


def _diag_dedupe_sources(sources: List[Dict[str, Any]], max_items: int = 30) -> List[Dict[str, Any]]:
    seen = set()
    out: List[Dict[str, Any]] = []
    for src in sources or []:
        if not isinstance(src, dict):
            continue
        txt = source_text(src)
        if not txt:
            continue
        key = (source_doc(src).lower(), re.sub(r"\W+", "", txt[:360].lower()))
        if key in seen:
            continue
        seen.add(key)
        out.append(src)
        if len(out) >= max_items:
            break
    return out


def _diag_rank_sources(sources: List[Dict[str, Any]], scorer, max_items: int = 20) -> List[Dict[str, Any]]:
    filtered = [s for s in (sources or []) if isinstance(s, dict) and source_text(s)]
    scored: List[Tuple[float, Dict[str, Any]]] = []
    for src in filtered:
        try:
            value = float(scorer(src))
        except Exception:
            value = 0.0
        item = dict(src)
        meta = dict(meta_of(item))
        meta["agent_selection_score"] = round(value, 4)
        meta["agent_selection_version"] = "v131_ranked_context"
        # Les modules existants pondèrent déjà rank_score/confidence : on y injecte le score agent
        # pour que la sélection réelle prime dans les prompts sans changer leur API.
        meta["rank_score"] = max(float(meta.get("rank_score") or 0), min(max(value / 10.0, 0.0), 10.0))
        meta["confidence"] = max(float(meta.get("confidence") or 0), 0.85 if value > 0 else 0.2)
        item["metadata"] = meta
        scored.append((value, item))
    ranked = [item for _, item in sorted(scored, key=lambda kv: kv[0], reverse=True)]
    return _diag_dedupe_sources(ranked, max_items=max_items)


def _diag_enrich_sections_for_real_agent(agent: "EnnoDiagnosticAgent", sections: Dict[str, List[Dict[str, Any]]]) -> Dict[str, List[Dict[str, Any]]]:
    """Ajoute des contextes objectifs/verrous sélectionnés par l'agent avant les appels LLM.

    Correction de fond : beaucoup de dossiers stockent l'objectif réel dans méthode/résultat/conclusion,
    pas seulement dans le rôle objectif. Cette fonction récupère ces passages de manière générique.
    """
    if not isinstance(sections, dict):
        return sections

    objective_broad = agent.search_chroma(
        role=None,
        query="but objectif évaluer qualifier valider démontrer comparer capacité besoin finalité technique résultats attendus",
        top_k=22,
    )
    lock_broad = agent.search_chroma(
        role=None,
        query="incertitude technique limite difficulté non démontré à vérifier représentativité transposabilité généralisation robustesse influence paramètres conditions réelles",
        top_k=22,
    )

    objective_pool: List[Dict[str, Any]] = []
    for key in ["objectifs", "global", "methodes", "resultats", "limites", "contributions", "axe_preuves_resultats"]:
        value = sections.get(key)
        if isinstance(value, list):
            objective_pool.extend(value)
    objective_pool.extend(objective_broad)

    lock_pool: List[Dict[str, Any]] = []
    for key in ["verrous", "limites", "methodes", "resultats", "parametres", "axe_problemes_transverses", "axe_contraintes_transverses"]:
        value = sections.get(key)
        if isinstance(value, list):
            lock_pool.extend(value)
    lock_pool.extend(lock_broad)

    objective_context = _diag_rank_sources(objective_pool, _diag_objective_score, max_items=10)
    lock_context = _diag_rank_sources(lock_pool, _diag_lock_score, max_items=14)

    sections["objectif_agent_context"] = objective_context
    sections["verrou_agent_context"] = lock_context

    # L'objectif peut être enrichi car il ne décide pas de la liste des verrous.
    sections["objectifs"] = _diag_dedupe_sources(objective_context, max_items=10)

    # Ne jamais remplacer les candidats NLP stricts par cette recherche large.
    # Les méthodes, résultats et paramètres restent un contexte d'explication ;
    # ils ne deviennent pas de nouveaux candidats de verrou.
    sections["verrou_agent_context"] = _diag_dedupe_sources(lock_context, max_items=14)

    sections["_agent_selection_report"] = {
        "version": "v131_real_agent_source_selection_ranked_context",
        "principle": (
            "Les candidats de verrou viennent du rôle NLP strict. La recherche large "
            "sert uniquement de contexte d'explication."
        ),
        "objective_broad_count": len(objective_broad),
        "lock_broad_count": len(lock_broad),
        "objective_context_count": len(objective_context),
        "lock_context_count": len(lock_context),
        "top_objective_documents": [source_doc(s) for s in objective_context[:5]],
        "top_lock_documents": [source_doc(s) for s in lock_context[:5]],
    }
    return sections

def technical_title_from_source(src: Dict[str, Any], max_chars: int = 180) -> str:
    """
    Extrait un titre technique provisoire depuis une preuve, sans règle métier.
    Sert uniquement de secours si le LLM n'a pas produit assez de titres.
    """
    txt = repair_mojibake(source_text(src))
    meta = meta_of(src)

    candidates = [
        clean_text(meta.get("llm_title")),
        clean_text(meta.get("verrou_title")),
        clean_text(meta.get("theme_label")) if not is_universal_reconstruction_source(src) else "",
        clean_text(meta.get("section_title")),
    ]

    patterns = [
        r"V\s*\d+\s*\|\s*([^|:\n]{8,140})(?:\s*:\s*([^|\n.]{8,180}))?",
        r"ID\s+Verrou\s*\|[^\n]*?V\s*\d+\s*\|\s*([^|:\n]{8,140})(?:\s*:\s*([^|\n.]{8,180}))?",
        r"Verrou\s*\d+\s*[:\-–—]\s*([^\n.]{12,180})",
        r"OBJ\s*\d+\s*[-–—:]\s*([^\n.]{12,180})",
        r"P\s*\d+(?:\.\d+)*\s+([^:\n]{8,140})(?:\s*:\s*([^\n.]{8,180}))?",
    ]

    for pattern in patterns:
        match = re.search(pattern, txt, flags=re.I)
        if match:
            part1 = clean_text(match.group(1) or "")
            part2 = clean_text(match.group(2) or "") if match.lastindex and match.lastindex >= 2 else ""
            title = f"{part1} : {part2}" if part2 else part1
            candidates.append(title)

    first_sentence = re.split(r"[.!?\n]", txt, maxsplit=1)[0]
    candidates.append(first_sentence)

    for candidate in candidates:
        candidate = re.sub(r"^[-*•\d.)\s]+", "", clean_text(candidate))
        candidate = re.sub(r"^Verrou implicite possible\s*[—–:-]\s*", "", candidate, flags=re.I)
        candidate = re.sub(r"^Question de qualification\s*:\s*", "", candidate, flags=re.I)
        candidate = candidate.strip(" |:-–—")
        if len(candidate) >= 12:
            return truncate(candidate, max_chars)

    return "Signal technique à reformuler"

def build_sources_block(title: str, sources: List[Dict[str, Any]], max_items: int = 10, max_text_chars: int = 520) -> str:
    lines = [f"## {title}"]

    if not sources:
        lines.append("- Aucun élément récupéré depuis Chroma.")
        return "\n".join(lines)

    for i, src in enumerate(sources[:max_items], start=1):
        meta = meta_of(src)
        role = clean_text(meta.get("role"))
        doc = source_doc(src)
        decision = clean_text(meta.get("frascati_decision") or meta.get("decision"))
        fr_score = meta.get("frascati_score", "")
        page = meta.get("page") or meta.get("page_number") or src.get("page")
        txt = truncate(source_text(src), max_text_chars)

        lines.append(
            f"- Source {i} | rôle={role or '-'} | document={doc or '-'} | "
            f"page={page if page not in (None, '') else '-'} | "
            f"frascati={decision or '-'} | score={fr_score if fr_score != '' else '-'}\n"
            f"  Texte : {txt}"
        )

    return "\n".join(lines)


def build_sources_block_compact(
    title: str,
    sources: List[Dict[str, Any]],
    max_items: int = 5,
    max_text_chars: int = 260,
) -> str:
    lines = [f"## {title}"]

    if not sources:
        lines.append("- Aucun élément récupéré depuis Chroma.")
        return "\n".join(lines)

    for i, src in enumerate(sources[:max_items], start=1):
        meta = meta_of(src)
        role = clean_text(meta.get("role"))
        doc = source_doc(src)
        decision = clean_text(meta.get("frascati_decision") or meta.get("decision"))
        fr_score = meta.get("frascati_score", "")
        txt = truncate(source_text(src), max_text_chars)

        lines.append(
            f"- Source {i} | rôle={role or '-'} | document={doc or '-'} | "
            f"frascati={decision or '-'} | score={fr_score if fr_score != '' else '-'} | "
            f"texte={txt}"
        )

    return "\n".join(lines)


def build_llm(model=None):
    try:
        from modules.LLM.llm_client import LLMClient
        return LLMClient(model=model)
    except TypeError:
        from modules.LLM.llm_client import LLMClient
        return LLMClient()
    except Exception as e:
        raise RuntimeError(f"Impossible de charger modules.LLM.llm_client.LLMClient : {e}")


# =========================================================
# Agent EnnoDiagnostic
# =========================================================

class EnnoDiagnosticAgent:
    def __init__(
        self,
        organisme: Optional[str] = None,
        project: Optional[str] = None,
        year: Optional[str | int] = None,
        organisme_id: Optional[str] = None,
        project_id: Optional[str] = None,
        year_id: Optional[str | int] = None,
        out_dir: Optional[str] = None,
        model: Optional[str] = None,
        gemini_model: Optional[str] = None,
        use_llm: bool = True,
        use_style_memory: bool = True,
        **kwargs,
    ):
        self.organisme = organisme_id or organisme or "unknown_organisme"
        self.project = project_id or project or "unknown_project"
        self.year = str(year_id or year or "2023")
        self.model = model or gemini_model
        self.use_llm = use_llm
        self.use_style_memory = use_style_memory

        # V132 : sortie standard dans storage/organismes/.../years/<year>/ennodiagnostic.
        # Si out_dir est fourni par une route API, on le respecte, sauf si ENNOSMART_FORCE_STORAGE_OUTPUT=1.
        force_storage = str(os.getenv("ENNOSMART_FORCE_STORAGE_OUTPUT", "1")).strip() != "0"
        if out_dir and not force_storage:
            self.out_dir = Path(out_dir)
        else:
            self.out_dir = _resolve_ennosmart_year_root(self.organisme, self.project, self.year)

        self.diagnostic_dir = self.out_dir / "ennodiagnostic"
        self.report_path = self.diagnostic_dir / "ennodiagnostic_report.json"

        from modules.RAG.retriever import EnnoRetriever

        self.retriever = EnnoRetriever(
            organisme=self.organisme,
            project=self.project,
            year=self.year,
        )

        self.llm = build_llm(self.model) if self.use_llm else None

    # =====================================================
    # Chroma retrieval
    # =====================================================

    def search_chroma(self, role: Optional[str], query: str, top_k: int = 12) -> List[Dict[str, Any]]:
        if role:
            sources = self.retriever.search(question=query, role_filter=role, top_k=top_k)
        else:
            sources = self.retriever.search(question=query, role_filter=None, top_k=top_k)
        return rank_sources_for_agent(sources, max_items=top_k)

    def _nlp_lock_group_to_source(self, group: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Convertit un groupe NLP en source compatible avec le synthétiseur.

        Le groupe reste une hypothèse à valider : cette conversion ne crée pas
        de verrou et ne modifie aucun score Frascati.
        """
        if not isinstance(group, dict):
            return None
        # V178 : si le NLP a explicitement qualifié le groupe comme sous-problème
        # local ou non affichable, il ne doit pas redevenir un verrou principal
        # dans EnnoDiagnostic.
        scope = clean_text(group.get("technical_scope") or group.get("lock_scope"))
        explicit_display = group.get("display_as_main_lock", group.get("display_as_lock"))
        if explicit_display is False:
            return None
        if scope in {"local_technical_subproblem", "secondary", "supporting_measurement"}:
            return None
        # Frascati V2 fournit une recommandation 0/1 mais ne filtre plus les
        # verrous. La presence dans la vue principale NLP fait foi ; une
        # recommandation negative reste un verrou potentiel a examiner.
        assessment = group.get("frascati_assessment") or {}
        if not isinstance(assessment, dict):
            assessment = {}
        recommendation = group.get("frascati_recommendation")
        if recommendation is None:
            recommendation = group.get("frascati_decision")
        frascati_score = group.get("frascati_score")
        if frascati_score in (None, ""):
            frascati_score = assessment.get("eligibility_score")

        supports = [item for item in (group.get("supporting_passages") or []) if isinstance(item, dict)]
        representative = clean_text(
            group.get("text")
            or group.get("source_text")
            or group.get("excerpt")
        )
        label = clean_text(
            group.get("candidate_group_label")
            or group.get("title")
            or group.get("section_title")
            or "Signal technique à qualifier"
        )
        excerpts: List[str] = []
        for support in supports[:8]:
            value = clean_text(support.get("text") or support.get("excerpt"))
            if value and value not in excerpts:
                excerpts.append(value)
        text = "\n".join(
            part
            for part in [label, representative, *excerpts[:5]]
            if part
        )
        if not text:
            return None

        primary = supports[0] if supports else group
        metadata = {
            "role": "verrou",
            "pack_key": "verrous_rnd_locaux",
            "source_type": "nlp_result_direct_group",
            "derived_view": group.get("derived_view") or "qualified_lock_evidence_group",
            "lock_group_id": group.get("lock_group_id") or group.get("passage_id"),
            "passage_id": group.get("passage_id") or group.get("lock_group_id"),
            "candidate_group_label": label,
            "document": primary.get("document") or group.get("document"),
            "source_path": primary.get("source_path") or group.get("source_path"),
            "section_title": primary.get("section_title") or group.get("section_title"),
            "frascati_score": frascati_score,
            "frascati_decision": recommendation,
            "frascati_recommendation": recommendation,
            "frascati_recommendation_label": group.get("frascati_recommendation_label"),
            "frascati_assessment": assessment,
            "final_role": group.get("final_role") or "verrou_potentiel",
            "evidence_count": group.get("evidence_count") or len(supports),
            "supporting_passages": supports,
            "supporting_documents": group.get("supporting_documents") or [],
            "needs_human_validation": group.get("needs_human_validation", True),
            "verrou_source": group.get("verrou_source") or "nlp_group_direct",
            "technical_scope": scope or "project_structuring_lock",
            "display_as_main_lock": True if explicit_display is None else bool(explicit_display),
        }
        return {
            "text": text,
            "source_text": representative or text,
            "document": metadata.get("document"),
            "source_path": metadata.get("source_path"),
            "metadata": metadata,
        }

    def _load_nlp_lock_group_sources(self) -> List[Dict[str, Any]]:
        """Charge les groupes qualifiés du NLP sans dépendre de l'indexation RAG.

        Chroma reste utilisé pour les preuves détaillées. Cette lecture directe
        évite qu'un groupe récent disparaisse simplement parce que l'index RAG
        n'a pas encore été reconstruit.
        """
        try:
            path = self._find_current_nlp_result_path()
            if path is None or not path.exists():
                return []
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[EnnoDiagnostic][V164_NLP_GROUPS][WARN] {exc}")
            return []

        collected: List[Dict[str, Any]] = []
        frascati = payload.get("frascati_guard") or {}
        if isinstance(frascati, dict):
            for key in ("verrous_probables", "verrous_a_verifier"):
                values = frascati.get(key) or []
                if isinstance(values, list):
                    collected.extend(item for item in values if isinstance(item, dict))

        pack = payload.get("multi_document_evidence_pack_for_ennodiagnostic") or {}
        if isinstance(pack, dict):
            values = pack.get("verrous_rnd_locaux") or []
            if isinstance(values, list):
                collected.extend(item for item in values if isinstance(item, dict))
            prompt_view = pack.get("prompt_balanced_view") or {}
            if isinstance(prompt_view, dict):
                values = prompt_view.get("verrous_rnd_locaux") or []
                if isinstance(values, list):
                    collected.extend(item for item in values if isinstance(item, dict))

        seen = set()
        sources: List[Dict[str, Any]] = []
        for group in collected:
            key = clean_text(group.get("lock_group_id") or group.get("passage_id"))
            if not key:
                key = hashlib.sha1(
                    clean_text(group.get("candidate_group_label") or group.get("text")).encode("utf-8")
                ).hexdigest()
            if key in seen:
                continue
            seen.add(key)
            source = self._nlp_lock_group_to_source(group)
            if source is not None:
                sources.append(source)

        if sources:
            print(
                f"[EnnoDiagnostic][V164_NLP_GROUPS] "
                f"groupes_directs={len(sources)} source={path}"
            )
        return sources

    def retrieve_all_sections(self) -> Dict[str, List[Dict[str, Any]]]:
        sections: Dict[str, List[Dict[str, Any]]] = {}

        sections["global"] = self.search_chroma(
            role=None,
            query="résumé global contexte technique objectif difficultés travaux résultats limites innovation",
            top_k=14,
        )

        sections["objectifs"] = self.search_chroma(
            role="objectif",
            query="objectifs locaux objectif global finalité technique besoin performances attendues",
            top_k=12,
        )

        verrou_retrieval_window = int(os.getenv("ENNOSMART_DIAG_VERROU_RETRIEVAL_WINDOW", "250"))
        strict_verrou_candidates = self.search_chroma(
            role="verrou",
            query="verrous R&D incertitudes techniques difficultés scientifiques blocages limites hypothèses à valider phénomènes non maîtrisés",
            top_k=verrou_retrieval_window,
        )
        strict_verrou_candidates = [
            source
            for source in strict_verrou_candidates
            if not is_universal_reconstruction_source(source)
            and not bool(meta_of(source).get("rejected_as_verrou"))
        ]
        direct_nlp_groups = self._load_nlp_lock_group_sources()
        # Aucun quota métier : Chroma et le JSON NLP sont deux chemins d'accès
        # aux mêmes groupes qualifiés. Leur fusion est dédupliquée par source.
        combined_verrou_candidates = merge_ranked_sources(
            strict_verrou_candidates,
            direct_nlp_groups,
            max_items=max(
                verrou_retrieval_window,
                len(strict_verrou_candidates) + len(direct_nlp_groups),
            ),
        )
        sections["verrous"] = dedupe_sources(
            combined_verrou_candidates,
            max_items=max(verrou_retrieval_window, len(combined_verrou_candidates)),
        )
        sections["_nlp_verrou_candidates"] = list(sections["verrous"])

        sections["methodes"] = self.search_chroma(
            role="methode",
            query="démarche expérimentale méthodes essais prototypes simulations protocole travaux réalisés validation technique",
            top_k=14,
        )

        sections["resultats"] = self.search_chroma(
            role="resultat",
            query="résultats métriques mesures performances essais valeurs chiffrées observations conclusions résultats qualitatifs",
            top_k=14,
        )

        sections["parametres"] = self.search_chroma(
            role="parametre",
            query="paramètres techniques contraintes valeurs seuils dimensions pression débit température configuration conditions",
            top_k=10,
        )

        sections["limites"] = self.search_chroma(
            role="limite",
            query="limites contraintes problèmes points bloquants données manquantes risques à vérifier incertitudes",
            top_k=10,
        )

        # On conserve les sources qui portent le score Frascati séparément.
        # Elles peuvent être très synthétiques ; elles ne doivent pas écraser
        # les preuves détaillées utilisées par le LLM pour reformuler.
        sections["_frascati_verrous"] = list(sections.get("_nlp_verrou_candidates", []))

        # Axes complémentaires génériques : ils servent à récupérer des preuves
        # sans coder un projet précis ni un domaine précis.
        sections["axe_problemes_transverses"] = self.search_chroma(
            role=None,
            query="problème difficulté limite incertitude non maîtrisé instabilité anomalie défaut non conforme robustesse fiabilité performance qualité",
            top_k=12,
        )

        sections["axe_contraintes_transverses"] = self.search_chroma(
            role=None,
            query="contraintes exigences conditions paramètres seuils configuration contexte environnement ressources compatibilité objectif attendu",
            top_k=12,
        )

        sections["axe_preuves_resultats"] = self.search_chroma(
            role=None,
            query="preuves mesures tests essais résultats observations métriques comparaison validation limites conclusions valeurs courbes tableaux",
            top_k=12,
        )

        # Les limites et problèmes transverses enrichissent l'explication, mais
        # ne sont jamais ajoutés à la liste primaire des candidats NLP.
        sections["verrou_support_context"] = merge_ranked_sources(
            sections.get("limites", []),
            sections.get("axe_problemes_transverses", []),
            sections.get("axe_contraintes_transverses", []),
            max_items=30,
        )

        # V130 — vraie sélection agent avant LLM : on enrichit objectifs/verrous
        # depuis toutes les preuves Chroma pertinentes, pas seulement depuis le rôle strict.
        sections = _diag_enrich_sections_for_real_agent(self, sections)

        return sections

    # =====================================================
    # Frascati
    # =====================================================

    def _load_official_frascati_summary(self) -> Dict[str, Any]:
        """Lit l'évaluation Frascati officielle produite par le NLP.

        Le score n'est jamais recalculé ici. Cette méthode restaure seulement
        le contrat de transmission NLP -> RAG -> EnnoDiagnostic, y compris les
        scores par groupe nécessaires aux verrous consolidés.
        """
        path = self._find_current_nlp_result_path()
        if path is None or not path.exists():
            return {"ok": False, "missing": True, "group_assessments": []}
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            return {
                "ok": False,
                "missing": False,
                "error": str(exc),
                "source_path": str(path),
                "group_assessments": [],
            }

        guard = payload.get("frascati_guard") or {}
        if not isinstance(guard, dict):
            guard = {}
        assessment = guard.get("frascati_assessment") or {}
        if not isinstance(assessment, dict):
            assessment = {}

        raw_groups = assessment.get("group_assessments") or []
        group_assessments: List[Dict[str, Any]] = []
        if isinstance(raw_groups, list):
            for raw in raw_groups:
                if not isinstance(raw, dict):
                    continue
                group_id = clean_text(raw.get("group_id"))
                try:
                    score = float(raw.get("eligibility_score"))
                except Exception:
                    score = 0.0
                if not group_id or score <= 0:
                    continue
                group_assessments.append({
                    "group_id": group_id,
                    "eligibility_score": round(score, 4),
                    "eligibility_assessment_score": raw.get("eligibility_assessment_score"),
                    "risk_level": clean_text(raw.get("risk_level")) or None,
                    "interpretation": clean_text(raw.get("interpretation")) or None,
                    "questions_to_ask": raw.get("questions_to_ask")
                    if isinstance(raw.get("questions_to_ask"), list) else [],
                    "dimensions": raw.get("dimensions")
                    if isinstance(raw.get("dimensions"), dict) else {},
                })

        scope_by_group: Dict[str, Dict[str, Any]] = {}
        technical_groups = guard.get("technical_lock_groups") or []
        if isinstance(technical_groups, list):
            for group in technical_groups:
                if not isinstance(group, dict):
                    continue
                group_id = clean_text(group.get("lock_group_id") or group.get("passage_id"))
                if not group_id:
                    continue
                scope_by_group[group_id] = {
                    "technical_scope": clean_text(group.get("technical_scope") or group.get("lock_scope")),
                    "display_as_main_lock": bool(group.get("display_as_main_lock", True)),
                }

        main_group_assessments: List[Dict[str, Any]] = []
        for item in group_assessments:
            enriched = dict(item)
            enriched.update(scope_by_group.get(item["group_id"], {}))
            item.update(scope_by_group.get(item["group_id"], {}))
            scope = clean_text(enriched.get("technical_scope"))
            display = enriched.get("display_as_main_lock")
            if display is True or scope in {"project_structuring_lock", "principal", "main_lock"}:
                main_group_assessments.append(enriched)

        global_score = None
        score_source = None
        for candidate, source in (
            ((payload.get("stats") or {}).get("global_frascati_score"), "nlp.stats.global_frascati_score"),
            (assessment.get("eligibility_score"), "nlp.frascati_guard.frascati_assessment.eligibility_score"),
        ):
            try:
                value = float(candidate)
            except Exception:
                continue
            if value > 0:
                global_score = round(value, 4)
                score_source = source
                break

        try:
            eligibility_assessment_score = round(
                float(assessment.get("eligibility_assessment_score")),
                4,
            )
        except Exception:
            eligibility_assessment_score = None
        demarche_legibility = compact_demarche_audit(
            assessment.get("demarche_legibility")
        )

        return {
            "ok": global_score is not None or bool(group_assessments),
            "source_path": str(path),
            "score_source": score_source,
            "average_frascati_score": global_score,
            "eligibility_assessment_score": eligibility_assessment_score,
            "eligibility_assessment_score_semantics": assessment.get("eligibility_assessment_score_semantics"),
            "eligibility_recommendation": assessment.get("eligibility_recommendation"),
            "recommendation_label": assessment.get("recommendation_label"),
            "risk_level": clean_text(assessment.get("risk_level")) or None,
            "scores_count": len(group_assessments),
            "main_groups_scores_count": len(main_group_assessments),
            "main_groups_average_frascati_score": (
                round(statistics.mean(
                    float(item["eligibility_score"]) for item in main_group_assessments
                ), 4)
                if main_group_assessments else None
            ),
            "group_assessments": group_assessments,
            "main_group_assessments": main_group_assessments,
            "questions_to_ask": assessment.get("questions_to_ask")
            if isinstance(assessment.get("questions_to_ask"), list) else [],
            "demarche_legibility": demarche_legibility,
        }

    def _load_rag_cluster_frascati_audit(self) -> Dict[str, Any]:
        """Compatibilité : l'audit de clusters RAG est désormais désactivé."""
        return {
            "ok": False,
            "disabled": True,
            "reason": "single_lock_grouping_owned_by_nlp_before_frascati",
            "scores": [],
            "score_rows": [],
            "source_path": None,
        }

    def frascati_summary_from_chroma(self, verrou_sources: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Retourne le score NLP officiel avec un audit RAG/Chroma séparé."""
        official = self._load_official_frascati_summary()
        source_chunk_scores: List[float] = []
        rag_decisions: Dict[str, int] = {}
        rag_levels: Dict[str, int] = {}

        for src in verrou_sources or []:
            meta = meta_of(src)

            try:
                score_float = float(meta.get("frascati_score"))
                if score_float > 0:
                    source_chunk_scores.append(score_float)
            except Exception:
                pass

            decision = clean_text(meta.get("frascati_decision") or meta.get("decision"))
            if decision and decision != "unknown":
                rag_decisions[decision] = rag_decisions.get(decision, 0) + 1

            level = clean_text(meta.get("verrou_candidate_level"))
            if level and level != "unknown":
                rag_levels[level] = rag_levels.get(level, 0) + 1

        cluster_audit = self._load_rag_cluster_frascati_audit()
        rag_scores = source_chunk_scores
        rag_average = round(statistics.mean(rag_scores), 4) if rag_scores else None
        official_score = official.get("average_frascati_score") if official.get("ok") else None
        official_main_score = official.get("main_groups_average_frascati_score") if official.get("ok") else None
        average_score = official_score if official_score is not None else rag_average
        assessments = official.get("group_assessments") or []
        interpretation_counts: Dict[str, int] = {}
        risk_counts: Dict[str, int] = {}
        for item in assessments:
            interpretation = clean_text(item.get("interpretation"))
            risk = clean_text(item.get("risk_level"))
            if interpretation:
                interpretation_counts[interpretation] = interpretation_counts.get(interpretation, 0) + 1
            if risk:
                risk_counts[risk] = risk_counts.get(risk, 0) + 1

        return {
            "average_frascati_score": average_score,
            "eligibility_assessment_score": official.get("eligibility_assessment_score"),
            "eligibility_assessment_score_semantics": official.get("eligibility_assessment_score_semantics"),
            "eligibility_recommendation": official.get("eligibility_recommendation"),
            "recommendation_label": official.get("recommendation_label"),
            "scores_count": official.get("scores_count", len(rag_scores)) if official.get("ok") else len(rag_scores),
            "risk_level": official.get("risk_level"),
            "score_source": official.get("score_source") if official.get("ok") else "rag_chroma_metadata_fallback",
            "source_path": official.get("source_path"),
            "group_assessments": assessments,
            "main_group_assessments": official.get("main_group_assessments") or [],
            "main_groups_scores_count": official.get("main_groups_scores_count", 0),
            "main_groups_average_frascati_score": official.get("main_groups_average_frascati_score"),
            "questions_to_ask": official.get("questions_to_ask") or [],
            "demarche_legibility": official.get("demarche_legibility") or {},
            "decisions_count": interpretation_counts or rag_decisions,
            "candidate_levels_count": risk_counts or rag_levels,
            "rag_audit": {
                "scores_count": len(rag_scores),
                "average_frascati_score": rag_average,
                "score_available": bool(rag_scores),
                "score_source": (
                    "rag_chroma_source_metadata" if source_chunk_scores
                    else None
                ),
                "source_path": cluster_audit.get("source_path"),
                "score_rows": cluster_audit.get("score_rows") or [],
                "consistent_with_nlp": (
                    None if (official_main_score or official_score) is None or rag_average is None
                    else abs(float(official_main_score or official_score) - float(rag_average)) <= 0.0001
                ),
                "source_chunks_audit": {
                    "scores_count": len(source_chunk_scores),
                    "decisions_count": rag_decisions,
                    "candidate_levels_count": rag_levels,
                },
            },
            "explanation": (
                "Le score officiel est lu dans le résultat NLP/Frascati déjà calculé. "
                "Les métadonnées RAG/Chroma sont conservées comme contrôle de transmission ; "
                "EnnoDiagnostic ne recalcule jamais ce score."
            ),
        }

    def _compact_frascati_block(self, frascati_summary: Dict[str, Any]) -> str:
        return json.dumps(
            {
                "average_frascati_score": frascati_summary.get("average_frascati_score"),
                "eligibility_assessment_score": frascati_summary.get("eligibility_assessment_score"),
                "eligibility_recommendation": frascati_summary.get("eligibility_recommendation"),
                "recommendation_label": frascati_summary.get("recommendation_label"),
                "scores_count": frascati_summary.get("scores_count"),
                "risk_level": frascati_summary.get("risk_level"),
                "score_source": frascati_summary.get("score_source"),
                "main_groups_scores_count": frascati_summary.get("main_groups_scores_count"),
                "main_groups_average_frascati_score": frascati_summary.get("main_groups_average_frascati_score"),
                "decisions_count": frascati_summary.get("decisions_count"),
                "candidate_levels_count": frascati_summary.get("candidate_levels_count"),
                "demarche_legibility": compact_demarche_audit(
                    frascati_summary.get("demarche_legibility")
                ),
            },
            ensure_ascii=False,
            indent=2,
        )


    # =====================================================
    # Memory V2 - projets similaires / continuité / style
    # =====================================================

    def load_previous_verrou_context(self) -> Dict[str, Any]:
        """Charge les verrous CIR antérieurs AVANT la reformulation courante.

        Ce contexte est strictement rédactionnel : il aide le LLM à formuler une
        incertitude au niveau consultant CIR, mais il n'est jamais ajouté aux
        preuves du cluster courant et ne peut valider aucun fait.
        """
        try:
            try:
                from agents.EnnoDiagnostic.previous_cir_verrou_context import (
                    load_previous_verrou_context,
                )
            except Exception:
                from previous_cir_verrou_context import load_previous_verrou_context

            report = load_previous_verrou_context(
                organisme=self.organisme,
                project=self.project,
                current_year=self.year,
                max_previous_years=max(
                    1,
                    int(os.getenv("ENNOSMART_DIAG_VERROU_PREVIOUS_MAX_YEARS", "1")),
                ),
                max_examples=max(
                    1,
                    int(os.getenv("ENNOSMART_DIAG_VERROU_PREVIOUS_MAX_EXAMPLES", "16")),
                ),
                max_text_chars=max(
                    250,
                    int(os.getenv("ENNOSMART_DIAG_VERROU_PREVIOUS_EXAMPLE_MAX_CHARS", "900")),
                ),
            )
            if not isinstance(report, dict):
                return {
                    "ok": False,
                    "available": False,
                    "examples": [],
                    "previous_years": [],
                    "message": "Contexte des verrous CIR antérieurs invalide.",
                    "factual_use_allowed": False,
                }
            print(
                "[EnnoDiagnostic][V186_PREVIOUS_LOCK_CONTEXT] "
                f"years={report.get('previous_years') or []} "
                f"examples={report.get('examples_count') or 0} "
                "usage=style_only",
                flush=True,
            )
            return report
        except Exception as exc:
            print(f"[EnnoDiagnostic][V186_PREVIOUS_LOCK_CONTEXT][WARN] {exc}", flush=True)
            return {
                "ok": False,
                "available": False,
                "error": str(exc),
                "examples": [],
                "previous_years": [],
                "message": "Contexte des verrous CIR antérieurs indisponible.",
                "factual_use_allowed": False,
            }


    def load_memory_v2_context(self, sections: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
        try:
            from modules.EXPERIENCE_MEMORY.memory_v2_retriever import retrieve_memory_v2_for_diagnostic
            report = retrieve_memory_v2_for_diagnostic(
                organisme=self.organisme,
                project=self.project,
                year=self.year,
                sections=sections,
            )
            return report if isinstance(report, dict) else {"ok": False, "prompt_block": "Memory V2 rapport invalide."}
        except Exception as exc:
            return {
                "ok": False,
                "error": str(exc),
                "message": "Memory V2 indisponible.",
                "prompt_block": "Memory V2 indisponible.",
                "similar_projects": [],
                "by_role": {},
                "style_examples": [],
            }



    def build_memory_v2_usage_report(
        self,
        memory_v2_report: Optional[Dict[str, Any]],
        style_memory_report: Optional[Dict[str, Any]],
        sections: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    ) -> Dict[str, Any]:
        memory_v2_report = memory_v2_report if isinstance(memory_v2_report, dict) else {}
        style_memory_report = style_memory_report if isinstance(style_memory_report, dict) else {}
        sections = sections if isinstance(sections, dict) else {}

        similar_projects = memory_v2_report.get("similar_projects") or []
        if not isinstance(similar_projects, list):
            similar_projects = []

        by_role = memory_v2_report.get("by_role") or {}
        if not isinstance(by_role, dict):
            by_role = {}

        style_examples_count = int(style_memory_report.get("examples_count") or 0)
        style_by_role = style_memory_report.get("examples_by_role_count") or {}
        if not isinstance(style_by_role, dict):
            style_by_role = {}

        cards_consulted = (
            memory_v2_report.get("cards_consulted")
            or memory_v2_report.get("cards_count")
            or memory_v2_report.get("results_count")
            or memory_v2_report.get("retrieved_count")
            or 0
        )

        cards_used = (
            memory_v2_report.get("cards_used")
            or memory_v2_report.get("used_count")
            or memory_v2_report.get("selected_count")
            or 0
        )

        if not cards_used and by_role:
            try:
                cards_used = sum(len(v or []) for v in by_role.values() if isinstance(v, list))
            except Exception:
                cards_used = 0

        if not cards_consulted:
            cards_consulted = cards_used

        try:
            similar_verrous = len(by_role.get("verrou") or by_role.get("verrous") or [])
        except Exception:
            similar_verrous = 0

        verrous_reused = (
            memory_v2_report.get("verrous_reused")
            or memory_v2_report.get("similar_verrous_used")
            or similar_verrous
            or 0
        )

        best_project = similar_projects[0] if similar_projects else {}
        if not isinstance(best_project, dict):
            best_project = {}

        best_score = (
            best_project.get("score")
            or best_project.get("similarity")
            or best_project.get("match_score")
            or best_project.get("confidence")
            or 0
        )

        try:
            best_score_float = float(best_score or 0)
            if best_score_float > 1:
                best_score_float = best_score_float / 100.0
        except Exception:
            best_score_float = 0.0

        confidence_parts: List[float] = []
        if memory_v2_report.get("ok") or memory_v2_report.get("available"):
            confidence_parts.append(0.35)
        if similar_projects:
            confidence_parts.append(min(0.25, 0.25 * max(best_score_float, 0.5)))
        if cards_used:
            confidence_parts.append(0.20)
        if style_examples_count:
            confidence_parts.append(0.20)

        confidence = round(min(1.0, sum(confidence_parts)), 2) if confidence_parts else 0.0

        experience_used = bool(memory_v2_report.get("ok") and (similar_projects or cards_used or by_role))
        style_used = bool(style_memory_report.get("ok") and style_examples_count > 0)

        similar_projects_clean: List[Dict[str, Any]] = []
        for p in similar_projects[:8]:
            if not isinstance(p, dict):
                continue
            similar_projects_clean.append({
                "organisme": p.get("organisme") or p.get("organization") or self.organisme,
                "project": p.get("project") or p.get("project_name") or p.get("project_slug") or "",
                "year": str(p.get("year") or p.get("annee") or ""),
                "score": p.get("score") or p.get("similarity") or p.get("match_score") or p.get("confidence") or "",
                "reason": p.get("reason") or p.get("why") or p.get("match_reason") or "",
            })

        return {
            "ok": True,
            "enabled": True,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "organisme": self.organisme,
            "project": self.project,
            "year": self.year,
            "experience": {
                "enabled": True,
                "available": bool(memory_v2_report.get("available") or memory_v2_report.get("ok")),
                "used": experience_used,
                "cards_consulted": int(cards_consulted or 0),
                "cards_used": int(cards_used or 0),
                "similar_project_found": bool(similar_projects),
                "similar_projects_count": len(similar_projects),
                "similar_projects": similar_projects_clean,
                "similar_verrous": int(similar_verrous or 0),
                "verrous_reused": int(verrous_reused or 0),
                "principle": memory_v2_report.get("principle"),
                "error": memory_v2_report.get("error"),
            },
            "style": {
                "enabled": bool(self.use_style_memory),
                "available": bool(style_memory_report.get("ok")),
                "used": style_used,
                "examples_used": style_examples_count,
                "examples_by_role_count": style_by_role,
                "memory_path": style_memory_report.get("memory_path"),
                "principle": style_memory_report.get("principle"),
                "error": style_memory_report.get("error"),
            },
            "confidence": confidence,
            "interpretation": {
                "used_for": [
                    item for item, enabled in [
                        ("recherche de projets similaires", bool(similar_projects)),
                        ("continuité R&D / expérience", experience_used),
                        ("inspiration de reformulation des verrous", bool(cards_used or similar_verrous)),
                        ("style CIR", style_used),
                    ]
                    if enabled
                ],
                "not_used_for": [
                    "preuve factuelle du dossier courant",
                    "réutilisation de chiffres historiques",
                    "validation automatique de l'éligibilité CIR",
                ],
            },
            "debug_counts": {
                "chroma_sources_current_project": {
                    k: len(v or [])
                    for k, v in sections.items()
                    if isinstance(v, list) and not str(k).startswith("_")
                },
                "memory_v2_keys": sorted(list(memory_v2_report.keys())),
                "style_memory_keys": sorted(list(style_memory_report.keys())),
            },
        }

    def render_memory_v2_usage_section(self, usage_report: Dict[str, Any]) -> str:
        usage_report = usage_report if isinstance(usage_report, dict) else {}
        exp = usage_report.get("experience") or {}
        sty = usage_report.get("style") or {}
        interp = usage_report.get("interpretation") or {}

        lines: List[str] = []
        lines.append(f"## {MEMORY_V2_SECTION_TITLE}")
        lines.append("")
        lines.append("Cette section indique si la base Memory V2 a été utilisée pendant ce diagnostic.")
        lines.append("")
        lines.append("### Synthèse d'utilisation")
        lines.append(f"- Statut mémoire V2 : {'active' if usage_report.get('enabled') else 'inactive'}")
        lines.append(f"- Expérience / connaissance utilisée : {'oui' if exp.get('used') else 'non'}")
        lines.append(f"- Style CIR utilisé : {'oui' if sty.get('used') else 'non'}")
        lines.append(f"- Niveau de confiance mémoire : {usage_report.get('confidence', 0)}")
        lines.append("")
        lines.append("### Expérience et continuité")
        lines.append(f"- Projet similaire trouvé : {'oui' if exp.get('similar_project_found') else 'non'}")
        lines.append(f"- Projets similaires retrouvés : {exp.get('similar_projects_count', 0)}")
        lines.append(f"- Cartes consultées : {exp.get('cards_consulted', 0)}")
        lines.append(f"- Cartes retenues/utilisées : {exp.get('cards_used', 0)}")
        lines.append(f"- Verrous similaires retrouvés : {exp.get('similar_verrous', 0)}")
        lines.append(f"- Verrous utilisés comme inspiration : {exp.get('verrous_reused', 0)}")

        similar_projects = exp.get("similar_projects") or []
        if similar_projects:
            lines.append("")
            lines.append("### Projets similaires")
            for i, p in enumerate(similar_projects[:5], start=1):
                project_name = clean_text(p.get("project")) or "-"
                year = clean_text(p.get("year")) or "-"
                score = p.get("score")
                score_txt = "-"
                try:
                    score_float = float(score)
                    score_txt = f"{round(score_float * 100, 1)}%" if score_float <= 1 else f"{round(score_float, 1)}%"
                except Exception:
                    score_txt = clean_text(score) or "-"
                lines.append(f"{i}. {project_name} — {year} — similarité : {score_txt}")

        lines.append("")
        lines.append("### Style CIR")
        lines.append(f"- Exemples de style utilisés : {sty.get('examples_used', 0)}")
        by_role = sty.get("examples_by_role_count") or {}
        if by_role:
            role_txt = ", ".join(f"{k}={v}" for k, v in by_role.items())
            lines.append(f"- Répartition par rôle : {role_txt}")

        used_for = interp.get("used_for") or []
        not_used_for = interp.get("not_used_for") or []

        if used_for:
            lines.append("")
            lines.append("### Influence autorisée")
            for item in used_for:
                lines.append(f"- {item}")

        if not_used_for:
            lines.append("")
            lines.append("### Limites de sécurité")
            for item in not_used_for:
                lines.append(f"- Non utilisé pour : {item}")

        if exp.get("error") or sty.get("error"):
            lines.append("")
            lines.append("### Alertes techniques")
            if exp.get("error"):
                lines.append(f"- Memory V2 expérience : {exp.get('error')}")
            if sty.get("error"):
                lines.append(f"- Memory V2 style : {sty.get('error')}")

        return "\n".join(lines).strip()


    # =====================================================
    # Style memory
    # =====================================================

    def _sources_query_text(self, sources: List[Dict[str, Any]], max_sources: int = 8) -> str:
        parts: List[str] = []
        for src in sources[:max_sources]:
            doc = source_doc(src)
            txt = source_text(src)
            if txt:
                parts.append(f"document={doc}\n{txt}")
        return "\n\n".join(parts)

    def load_style_memory_context(self, sections: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
        if not self.use_style_memory:
            return {
                "ok": False,
                "disabled": True,
                "message": "Mémoire rédactionnelle CIR désactivée.",
                "style_block": "Mémoire rédactionnelle CIR désactivée.",
                "stats": {},
                "examples_by_role": {},
            }

        try:
            from modules.CIR_STYLE_MEMORY.style_memory import (
                load_style_memory,
                retrieve_style_examples,
                build_style_block,
                style_memory_path,
            )

            memory = load_style_memory(self.organisme)
            stats = memory.get("stats") or {}
            memory_path = str(style_memory_path(self.organisme))

            role_sources = {
                "objectif": sections.get("objectifs", []),
                "verrou": sections.get("verrous", []) + sections.get("limites", []),
                "methode": sections.get("methodes", []),
                "resultat": sections.get("resultats", []),
                "parametre": sections.get("parametres", []),
            }

            examples_by_role: Dict[str, List[Dict[str, Any]]] = {}
            all_examples: List[Dict[str, Any]] = []

            for role, sources in role_sources.items():
                query = self._sources_query_text(sources, max_sources=8)
                examples = retrieve_style_examples(
                    organisme=self.organisme,
                    target_role=role,
                    query_text=query,
                    project=self.project,
                    top_k=3,
                    strict_domain=True,
                )
                examples_by_role[role] = examples
                all_examples.extend(examples)

            seen = set()
            unique_examples = []
            for ex in all_examples:
                ex_id = ex.get("example_id") or f"{ex.get('role')}|{ex.get('text','')[:80]}"
                if ex_id in seen:
                    continue
                seen.add(ex_id)
                unique_examples.append(ex)

            style_block = build_style_block(unique_examples[:8], max_chars_per_example=450)

            return {
                "ok": True,
                "memory_path": memory_path,
                "stats": stats,
                "examples_count": len(unique_examples),
                "examples_by_role_count": {k: len(v) for k, v in examples_by_role.items()},
                "examples_by_role": examples_by_role,
                "style_block": style_block,
                "principle": (
                    "Les exemples CIR sont utilisés uniquement pour la rédaction. "
                    "Les faits doivent provenir uniquement des sources Chroma du dossier courant."
                ),
            }

        except Exception as e:
            return {
                "ok": False,
                "error": str(e),
                "message": "Mémoire rédactionnelle CIR indisponible.",
                "stats": {},
                "examples_by_role": {},
                "style_block": "Aucun exemple de style CIR disponible.",
            }

    def _style_memory_for_role(
        self,
        style_memory_report: Optional[Dict[str, Any]],
        role: str,
        max_chars: int = 1600,
    ) -> str:
        if not isinstance(style_memory_report, dict) or not style_memory_report.get("ok"):
            return "Aucune mémoire de style exploitable. Appliquer un style CIR clair, technique, prudent et vérifiable."

        examples_by_role = style_memory_report.get("examples_by_role") or {}
        role_examples = examples_by_role.get(role) or []

        if role_examples:
            lines = [
                "Exemples de style CIR pour ce rôle uniquement.",
                "Règle : ne jamais copier un fait ni une phrase entière ; utiliser seulement le style.",
                "",
            ]
            for i, ex in enumerate(role_examples[:3], start=1):
                ex_text = clean_text(ex.get("text") or ex.get("content") or "")
                ex_role = clean_text(ex.get("role") or role)
                if ex_text:
                    lines.append(f"- Exemple {i} | rôle={ex_role} : {truncate(ex_text, 420)}")
            return truncate("\n".join(lines), max_chars)

        return truncate(clean_text(style_memory_report.get("style_block")), max_chars)

    def _style_role_for_section(self, role: str, title: str = "") -> str:
        """Retourne le rôle de style, jamais un rôle de preuve."""
        key = _diag_norm(role or title)
        if any(token in key for token in ("verrou", "signal", "limite")):
            return "verrou"
        if any(token in key for token in ("objectif", "synthese", "strategie", "global")):
            return "objectif"
        if any(token in key for token in ("methode", "demarche", "essai", "protocole")):
            return "methode"
        if any(token in key for token in ("resultat", "metrique", "mesure")):
            return "resultat"
        if any(token in key for token in ("parametre", "contrainte", "configuration")):
            return "parametre"
        return "verrou"

    def _style_blocks_for_prompt(
        self,
        style_memory_report: Optional[Dict[str, Any]],
        compact: bool = False,
    ) -> str:
        """Construit un contexte rédactionnel court, séparé des preuves."""
        max_per_role = 250 if compact else 480
        roles = ("objectif", "verrou", "methode", "resultat", "parametre")
        lines = [
            "EXEMPLES DE STYLE CIR — AUCUNE VALEUR DE PREUVE :",
            "Ils indiquent seulement la forme de rédaction. Ne pas reprendre les noms, chiffres, documents ou faits des exemples.",
        ]
        for role in roles:
            block = self._style_memory_for_role(style_memory_report, role, max_chars=max_per_role)
            if block and not block.startswith("Aucune mémoire"):
                lines.append(f"[{role}]\n{block}")
        if len(lines) == 2:
            lines.append("Appliquer un style CIR clair, technique, prudent et vérifiable.")
        return "\n\n".join(lines)

    def _memory_v2_context_block(
        self,
        memory_v2_report: Optional[Dict[str, Any]],
        max_chars: int = 1800,
    ) -> str:
        """Expose Memory V2 comme aide de contexte, jamais comme source courante."""
        if not isinstance(memory_v2_report, dict) or not memory_v2_report.get("ok"):
            return "Mémoire de projets similaires indisponible."
        raw = clean_text(memory_v2_report.get("prompt_block"))
        if not raw:
            return "Mémoire de projets similaires disponible, sans extrait exploitable."
        return "\n".join([
            "PROJETS SIMILAIRES / MEMORY V2 — CONTEXTE NON FACTUEL :",
            "Utiliser uniquement pour repérer une continuité possible et améliorer la formulation.",
            "Ne jamais transformer ces informations en fait, mesure, résultat ou preuve du projet courant.",
            truncate(raw, max_chars),
        ])

    def _cir_previous_comparison_block(
        self,
        cir_memory_report: Optional[Dict[str, Any]],
        max_items: int = 8,
        max_chars: int = 1800,
    ) -> str:
        """Réduit N-1 à une comparaison lisible sans transmettre un ancien CIR brut."""
        if not isinstance(cir_memory_report, dict) or not cir_memory_report.get("ok"):
            return "Comparaison CIR N-1 indisponible ou aucun CIR antérieur exploitable."

        years = cir_memory_report.get("previous_cir_years_used") or cir_memory_report.get("previous_years") or []
        comparisons = cir_memory_report.get("verrou_comparisons") or cir_memory_report.get("comparisons") or []
        if not isinstance(comparisons, list):
            comparisons = []
        lines = [
            "COMPARAISON AVEC LE CIR N-1 — CONTEXTE HISTORIQUE STRUCTURÉ :",
            f"Année(s) antérieure(s) retenue(s) : {', '.join(str(y) for y in years) if years else 'non précisée(s)'}.",
            "Cette comparaison sert seulement à qualifier continuité, nouveauté ou évolution.",
            "Elle ne prouve aucun fait du projet courant et ne doit pas créer de verrou supplémentaire.",
        ]
        safe_keys = (
            "status", "relation", "comparison", "comparison_status", "current_title", "previous_title",
            "current_label", "previous_label", "match_type", "similarity", "reason", "explanation",
        )
        for index, item in enumerate(comparisons[:max_items], start=1):
            if not isinstance(item, dict):
                continue
            values = []
            for key in safe_keys:
                value = item.get(key)
                if isinstance(value, (str, int, float)) and clean_text(value):
                    values.append(f"{key}={truncate(value, 260)}")
            if values:
                lines.append(f"- Comparaison {index} : " + " | ".join(values))
        if len(lines) == 4:
            lines.append("- Aucun rapprochement structuré exploitable.")
        return truncate("\n".join(lines), max_chars)

    def render_cir_previous_comparison_section(
        self,
        cir_memory_report: Optional[Dict[str, Any]],
    ) -> str:
        """Section déterministe utilisée aussi lorsque le LLM est indisponible."""
        return "\n\n".join([
            f"## {CIR_PREVIOUS_SECTION_TITLE}",
            self._cir_previous_comparison_block(cir_memory_report, max_items=10, max_chars=3200),
            "Conclusion : cette lecture historique est à confirmer par le consultant ; les sources RAG du projet courant restent les seules preuves de la période analysée.",
        ])


    # =====================================================
    # Verrous reformulés par le LLM pour validation consultant
    # =====================================================

    def _token_set_for_matching(self, text: Any) -> set[str]:
        text = repair_mojibake(text)
        text = text.lower()
        text = re.sub(r"[^a-z0-9àâäéèêëîïôöùûüç]+", " ", text)
        stop = {
            "les", "des", "une", "dans", "pour", "avec", "sans", "sur", "par", "aux", "est", "sont", "que", "qui",
            "verrou", "verrous", "signal", "signaux", "technique", "techniques", "candidat", "candidats", "source", "sources",
            "document", "documents", "frascati", "validation", "consultant", "projet", "dossier", "preuve", "preuves",
        }
        return {w for w in re.findall(r"[a-z0-9àâäéèêëîïôöùûüç]{3,}", text) if w not in stop}

    def _similarity_for_llm_candidate(self, candidate_text: str, src: Dict[str, Any]) -> float:
        a = self._token_set_for_matching(candidate_text)
        meta = meta_of(src)
        source_blob = "\n".join([
            source_text(src),
            str(meta.get("theme_label") or ""),
            str(meta.get("theme_id") or ""),
            str(meta.get("final_role") or ""),
            str(meta.get("technical_signature") or ""),
        ])
        b = self._token_set_for_matching(source_blob)
        if not a or not b:
            return 0.0
        inter = len(a & b)
        return inter / max(1, min(len(a), len(b)))

    def _extract_signal_blocks_from_markdown(self, content: str) -> List[Dict[str, str]]:
        section = extract_markdown_section(content, SIGNAL_SECTION_TITLE) or extract_markdown_section(content, LEGACY_SIGNAL_SECTION_TITLE)
        section = repair_mojibake(section)
        if not section:
            return []

        lines = [ln.rstrip() for ln in section.splitlines()]
        blocks: List[List[str]] = []
        current: List[str] = []

        def starts_candidate(line: str) -> bool:
            s = line.strip()
            if not s:
                return False
            if re.match(r"^#{3,}\s+", s):
                return True
            if re.match(r"^(?:[-*]|\d+[\.)])\s+\*\*[^*]{15,}\*\*", s):
                return True
            if re.match(r"^(?:[-*]|\d+[\.)])\s+(?:Signal|Hypothèse|Verrou|Axe)\b", s, flags=re.I):
                return True
            return False

        for line in lines:
            if starts_candidate(line):
                if current:
                    blocks.append(current)
                current = [line]
            else:
                if current:
                    current.append(line)
        if current:
            blocks.append(current)

        out: List[Dict[str, str]] = []
        for block in blocks[:12]:
            raw = "\n".join(block).strip()
            if len(raw) < 30:
                continue
            first = block[0].strip()
            first = re.sub(r"^#{3,}\s+", "", first)
            first = re.sub(r"^(?:[-*]|\d+[\.)])\s+", "", first)
            m = re.search(r"\*\*(.*?)\*\*", first)
            title = m.group(1).strip() if m else first
            title = re.split(r"\s+[—–-]\s+|\s*:\s*", title, maxsplit=1)[0]
            title = re.sub(r"^(Signal|Hypothèse|Verrou|Axe)\s*(candidat|technique)?\s*\d*\s*[:\-–—]?\s*", "", title, flags=re.I).strip()
            title = truncate(title, 220)
            if len(title) < 12:
                continue
            out.append({"title": title, "block": raw})
        return out

    def build_llm_reformulated_verrous(
        self,
        content: str,
        sections: Dict[str, List[Dict[str, Any]]],
        frascati_summary: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        V189 : reformulation dédiée des groupes sémantiques produits par le NLP.

        Le NLP fixe la composition des groupes avant Frascati. Le RAG les
        transmet sans consolidation et EnnoDiagnostic ne fait que les reformuler.
        Ce n'est plus le frontend qui fabrique les verrous depuis les chunks.
        L'agent crée une vraie sortie JSON : report["llm_reformulated_verrous"].

        Règles :
        - pas de recalcul Frascati ;
        - pas d'invention ;
        - les preuves sources restent attachées ;
        - les catégories universelles Frascati sont exclues des candidats ;
        - plusieurs chunks d'un même ``lock_group_id`` restituent le même groupe,
          mais deux identifiants NLP distincts ne sont jamais fusionnés.
        - aucun minimum ni maximum de verrous n'est imposé.
        """
        try:
            try:
                from agents.EnnoDiagnostic.consultant_verrou_synthesizer import synthesize_consultant_verrous
            except Exception:
                from consultant_verrou_synthesizer import synthesize_consultant_verrous

            style_block = ""
            try:
                # On donne uniquement un style court, jamais une preuve factuelle.
                style_block = self._style_memory_for_role(
                    getattr(self, "_last_style_memory_report", None),
                    "verrou",
                    max_chars=1200,
                )
            except Exception:
                style_block = ""

            synthesis = synthesize_consultant_verrous(
                sections=sections,
                frascati_summary=frascati_summary,
                llm=self.llm,
                style_block=style_block,
                memory_v2_report=getattr(self, "_last_memory_v2_report", None),
                previous_cir_context=getattr(self, "_last_previous_verrou_context", None),
            )
            self._last_verrou_synthesis_report = synthesis
            items = synthesis.get("llm_reformulated_verrous") if isinstance(synthesis, dict) else []
            if isinstance(items, list) and items:
                return items
        except Exception as exc:
            self._last_verrou_synthesis_report = {
                "ok": False,
                "mode": "synthesizer_import_or_runtime_error",
                "error": str(exc),
            }
            print(f"[EnnoDiagnostic][V122_VERROU_SYNTHESIS][ERROR] {exc}")

        # Fallback conservateur : ne jamais republier les chunks bruts comme
        # verrous finaux. Ils restent disponibles dans la piste d'audit Chroma
        # et pourront être retraités au prochain lancement.
        self._last_verrou_synthesis_report["fallback_policy"] = "no_raw_chunk_promotion"
        return []

    def _enrich_verrous_with_frascati(
        self,
        items: List[Dict[str, Any]],
        frascati_summary: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Rattache à chaque verrou le score officiel de ses groupes NLP."""
        assessments = frascati_summary.get("group_assessments") or []
        by_group = {
            clean_text(item.get("group_id")): item
            for item in assessments
            if isinstance(item, dict) and clean_text(item.get("group_id"))
        }
        output: List[Dict[str, Any]] = []
        for raw in items or []:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            group_ids = item.get("member_group_ids") or item.get("original_nlp_group_ids") or []
            if isinstance(group_ids, str):
                group_ids = [group_ids]
            matched = [by_group[group_id] for group_id in group_ids if group_id in by_group]
            scores = [float(entry["eligibility_score"]) for entry in matched if entry.get("eligibility_score")]
            if scores:
                score = round(statistics.mean(scores), 4)
                item["score"] = score
                item["frascati_score"] = score
                item["upstream_frascati_score"] = score
                item["frascati_score_source"] = "nlp_group_assessments"
                item["frascati_group_assessments"] = matched
                risks = [clean_text(entry.get("risk_level")) for entry in matched if clean_text(entry.get("risk_level"))]
                if risks:
                    item["frascati_risk_level"] = risks[0] if len(set(risks)) == 1 else "mixte"
            output.append(item)
        return output

    # =====================================================
    # External reports
    # =====================================================

    def load_ai_detection_report(self) -> Dict[str, Any]:
        path = self.diagnostic_dir / "ai_detection_report.json"
        if not path.exists():
            return {"ok": False, "missing": True, "message": "Aucun rapport IA documentaire disponible."}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def ai_detection_prompt_block(self, ai_report: Dict[str, Any]) -> str:
        if not isinstance(ai_report, dict) or not ai_report.get("ok"):
            return "Aucun score IA documentaire disponible."

        summary = ai_report.get("summary") or {}
        ai_detection = ai_report.get("ai_detection") or {}
        score = summary.get("average_ai_percentage") or ai_detection.get("global_ai_percentage")
        if score is None:
            score = summary.get("average_ai_score")

        return (
            "Contrôle IA documentaire :\n"
            f"- Score IA moyen : {score}%\n"
            f"- Niveau : {summary.get('risk_level') or ai_detection.get('risk_level')}\n"
            f"- Passages analysés : {summary.get('passages_count') or ai_detection.get('total_passages_analyzed')}\n"
            f"- Passages risque élevé : {summary.get('high_count') or ai_detection.get('high_risk_passages_count')}\n"
            f"- Passages risque moyen : {summary.get('medium_count') or ai_detection.get('medium_risk_passages_count')}\n"
            "Note : ce score concerne les passages extraits des documents bruts, pas la synthèse LLM."
        )

    def _find_current_nlp_result_path(self) -> Optional[Path]:
        """Utilise le résolveur unique de CIR_MEMORY pour retrouver le NLP courant."""
        try:
            from modules.CIR_MEMORY.cir_memory import resolve_current_nlp_result_path

            resolved = resolve_current_nlp_result_path(
                organisme=self.organisme,
                project=self.project,
                year=self.year,
                required=False,
            )
            if resolved is not None:
                print(f"[EnnoDiagnostic][V158] NLP courant résolu : {resolved}")
                return resolved
        except Exception as exc:
            print(f"[EnnoDiagnostic][V158][WARN] Résolveur NLP indisponible : {exc}")

        # Dernier fallback local, utile si le module CIR_MEMORY est temporairement indisponible.
        for candidate in [
            self.out_dir / "nlp" / "nlp_result.json",
            self.out_dir / "nlp_result.json",
        ]:
            if candidate.exists() and candidate.is_file():
                return candidate.resolve()

        return None

    def load_cir_memory_report(
        self,
        current_verrous: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Compare les verrous regroupés EnnoDiagnostic au CIR précédent.

        Priorité :
        1. liste ``current_verrous`` créée par ``build_llm_reformulated_verrous`` ;
        2. rapport EnnoDiagnostic déjà sauvegardé, pour la route dédiée ;
        3. fallback prudent sur les items NLP role=verrou.

        Memory V2 sert ici à retrouver le CIR du même organisme/projet en N-1,
        puis N-2/N-3. La mémoire de style est exclue des preuves.
        """
        try:
            from modules.CIR_MEMORY.cir_memory import (
                load_or_create_cir_memory_comparison,
                memory_v2_fingerprint,
            )

            nlp_path = self._find_current_nlp_result_path()
            if not nlp_path:
                return {
                    "ok": False,
                    "missing": True,
                    "has_previous_cir": False,
                    "previous_cir_available": False,
                    "previous_cir_years_used": [],
                    "comparisons": [],
                    "verrou_comparisons": [],
                    "message": "nlp_result.json courant introuvable pour la comparaison CIR précédent.",
                }

            normalized_current_verrous = [
                item for item in (current_verrous or []) if isinstance(item, dict)
            ]
            current_verrous_json = json.dumps(
                normalized_current_verrous,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            current_verrous_hash = sha256_text(current_verrous_json)

            cache_dir = self.diagnostic_dir / "cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path = cache_dir / "cir_previous_comparison_cache_v161.json"
            cache_version = "ennodiagnostic_cir_previous_cache_v161_grouped_verrous"

            nlp_hash = sha256_file(nlp_path)
            memory_v2_hash = memory_v2_fingerprint(
                organisme=self.organisme,
                project=self.project,
                current_year=self.year,
            )

            root = Path(os.getenv("ENNOSMART_ROOT", r"C:\EnnoSmart"))
            org_slug = str(self.organisme).strip().lower()
            project_slug = str(self.project).strip().lower()
            possible_local_memory_paths = [
                self.out_dir / "cir_previous" / "cir_final_memory.json",
                self.out_dir / "cir_final_consultant" / "current" / "cir_final_memory.json",
                self.out_dir / "cir_memory" / "cir_final_memory.json",
                root / "storage" / "organismes" / org_slug / "projects" / project_slug / "years" / self.year / "cir_previous" / "cir_final_memory.json",
                root / "storage" / "organismes" / org_slug / "projects" / project_slug / "years" / self.year / "cir_final_consultant" / "current" / "cir_final_memory.json",
                root / "storage" / "organismes" / org_slug / "projects" / project_slug / "years" / self.year / "cir_memory" / "cir_final_memory.json",
            ]

            local_hash_parts: List[str] = []
            existing_local_paths: List[str] = []
            for memory_path in possible_local_memory_paths:
                try:
                    if memory_path.exists() and memory_path.is_file():
                        local_hash_parts.append(sha256_file(memory_path))
                        existing_local_paths.append(str(memory_path))
                except Exception:
                    continue

            local_memory_hash = sha256_text("|".join(local_hash_parts))
            shortlist_size = max(
                10,
                min(
                    500,
                    int(os.getenv("ENNOSMART_CIR_MEMORY_SHORTLIST_SIZE", "120")),
                ),
            )
            cache_key = sha256_text(
                "|".join([
                    cache_version,
                    self.organisme,
                    self.project,
                    self.year,
                    nlp_hash,
                    current_verrous_hash,
                    memory_v2_hash,
                    local_memory_hash,
                    str(shortlist_size),
                ])
            )

            force_recompute = str(
                os.getenv("ENNOSMART_CIR_MEMORY_FORCE_RECOMPUTE", "0")
            ).strip().lower() in {"1", "true", "yes", "oui"}

            cached = safe_read_json(cache_path)
            if (
                not force_recompute
                and cached.get("cache_version") == cache_version
                and cached.get("cache_key") == cache_key
                and isinstance(cached.get("report"), dict)
            ):
                report = dict(cached["report"])
                report.update({
                    "cached": True,
                    "cache_key": cache_key,
                    "cache_path": str(cache_path),
                    "managed_by_ennodiagnostic": True,
                    "in_prompt": False,
                    "current_verrous_hash": current_verrous_hash,
                })
                print(
                    "♻️ Comparaison CIR précédent V161 réutilisée "
                    f"| années={report.get('previous_cir_years_used') or []} "
                    f"| comparaisons={len(report.get('verrou_comparisons') or [])}",
                    flush=True,
                )
                return report

            t0 = time.time()
            max_previous_years = max(
                1,
                int(os.getenv("ENNOSMART_CIR_MEMORY_MAX_PREVIOUS_YEARS", "3")),
            )

            report = load_or_create_cir_memory_comparison(
                organisme=self.organisme,
                project=self.project,
                year=self.year,
                nlp_result_path=nlp_path,
                max_previous_years=max_previous_years,
                current_verrous=normalized_current_verrous,
                shortlist_size=shortlist_size,
            )
            if not isinstance(report, dict):
                report = {
                    "ok": False,
                    "has_previous_cir": False,
                    "previous_cir_available": False,
                    "previous_cir_years_used": [],
                    "comparisons": [],
                    "verrou_comparisons": [],
                    "message": "Rapport CIR mémoire invalide.",
                }

            elapsed = round(time.time() - t0, 2)
            previous_years = report.get("previous_cir_years_used")
            if not isinstance(previous_years, list):
                previous_years = report.get("previous_years")
            if not isinstance(previous_years, list):
                previous_years = []

            comparisons = report.get("comparisons")
            if not isinstance(comparisons, list):
                comparisons = []
            verrou_comparisons = report.get("verrou_comparisons")
            if not isinstance(verrou_comparisons, list):
                verrou_comparisons = []

            has_previous = bool(
                report.get("has_previous_cir")
                or report.get("previous_cir_available")
                or previous_years
            )
            report.update({
                "cached": False,
                "cache_version": cache_version,
                "cache_key": cache_key,
                "cache_path": str(cache_path),
                "comparison_total_elapsed_seconds": elapsed,
                "nlp_result_path": str(nlp_path),
                "current_verrous_count": len(normalized_current_verrous),
                "current_verrous_hash": current_verrous_hash,
                "memory_v2_fingerprint": memory_v2_hash,
                "previous_memory_paths_detected": existing_local_paths,
                "managed_by_ennodiagnostic": True,
                "in_prompt": False,
                "has_previous_cir": has_previous,
                "previous_cir_available": has_previous,
                "previous_cir_years_used": previous_years,
                "previous_years": previous_years,
                "comparisons": comparisons,
                "verrou_comparisons": verrou_comparisons,
            })

            save_json(
                cache_path,
                {
                    "cache_version": cache_version,
                    "cache_key": cache_key,
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "organisme": self.organisme,
                    "project": self.project,
                    "year": self.year,
                    "nlp_hash": nlp_hash,
                    "current_verrous_hash": current_verrous_hash,
                    "current_verrous_count": len(normalized_current_verrous),
                    "memory_v2_fingerprint": memory_v2_hash,
                    "local_memory_hash": local_memory_hash,
                    "previous_memory_paths_detected": existing_local_paths,
                    "report": report,
                },
            )

            print(
                "✅ Comparaison CIR précédent V161 calculée "
                f"en {elapsed}s | années={previous_years} "
                f"| source={report.get('previous_cir_source')} "
                f"| source_verrous={(report.get('summary') or {}).get('current_verrous_source')} "
                f"| comparaisons_verrous={len(verrou_comparisons)}",
                flush=True,
            )
            return report

        except Exception as exc:
            return {
                "ok": False,
                "has_previous_cir": False,
                "previous_cir_available": False,
                "previous_cir_years_used": [],
                "previous_years": [],
                "comparisons": [],
                "verrou_comparisons": [],
                "error": str(exc),
                "message": "Impossible de comparer avec le CIR précédent.",
                "managed_by_ennodiagnostic": True,
                "in_prompt": False,
            }

    # =====================================================
    # LLM prompt
    # =====================================================

    def _build_fast_single_prompt(
        self,
        sections: Dict[str, List[Dict[str, Any]]],
        frascati_summary: Dict[str, Any],
        style_memory_report: Optional[Dict[str, Any]],
        ai_detection_report: Optional[Dict[str, Any]],
        cir_memory_report: Optional[Dict[str, Any]],
        memory_v2_report: Optional[Dict[str, Any]] = None,
    ) -> str:
        budgets = {
            "global": int(os.getenv("ENNOSMART_DIAG_FAST_GLOBAL", "5")),
            "objectifs": int(os.getenv("ENNOSMART_DIAG_FAST_OBJECTIFS", "5")),
            "verrous": int(os.getenv("ENNOSMART_DIAG_FAST_VERROUS", "14")),
            "methodes": int(os.getenv("ENNOSMART_DIAG_FAST_METHODES", "7")),
            "resultats": int(os.getenv("ENNOSMART_DIAG_FAST_RESULTATS", "7")),
            "parametres": int(os.getenv("ENNOSMART_DIAG_FAST_PARAMETRES", "5")),
            "limites": int(os.getenv("ENNOSMART_DIAG_FAST_LIMITES", "5")),
        }

        parts: List[str] = []
        parts.append("Tu es EnnoDiagnostic, agent de synthèse CIR.")
        parts.append("")
        parts.append("Règles strictes :")
        parts.append("- Les faits doivent venir uniquement des sources RAG/Chroma du dossier courant.")
        parts.append("- La mémoire de style et Memory V2 ne sont jamais des sources factuelles.")
        parts.append("- Ne refais pas Frascati : reprends seulement le résumé déjà calculé.")
        parts.append("- Ne recalcule jamais le score Frascati.")
        parts.append("- Le score Frascati vient du NLP / module Frascati ; tu dois seulement le justifier.")
        parts.append("- Ne dis jamais que le CIR ou qu’un verrou est validé ; écris à valider par le consultant et à confirmer par EnnoScholar si nécessaire.")
        parts.append("- Cite les noms de documents quand ils sont disponibles.")
        parts.append("- Ne cite jamais 'JSON NLP'. Dis sources indexées ou sources Chroma.")
        parts.append("")
        if isinstance(memory_v2_report, dict) and memory_v2_report.get("ok"):
            parts.append("Mémoire V2 disponible :")
            parts.append(memory_v2_report.get("prompt_block") or "Mémoire V2 vide.")
            parts.append("")
            parts.append("Consignes Memory V2 :")
            parts.append("- Utilise Memory V2 pour repérer projets similaires, continuité et formulations de style.")
            parts.append("- N'utilise jamais un ancien CIR comme preuve du dossier courant.")
            parts.append("- Si un signal courant ressemble à un ancien projet mais manque de preuve courante, indique un risque de faux positif.")
            parts.append("- Reformule les verrous avec objet technique + phénomène + contrainte, sans ajouter de faits historiques.")
            parts.append("")
        else:
            parts.append("Mémoire V2 : aucune mémoire exploitable ou non disponible.")
            parts.append("")
        parts.append(self._cir_previous_comparison_block(cir_memory_report, max_items=6, max_chars=1400))
        parts.append("")
        parts.append("Règles de consolidation CIR :")
        parts.append("- Le NLP/Frascati fournit des signaux bruts, des scores et des preuves. Ton rôle est de les reformuler en hypothèses de verrous R&D candidates, lisibles pour un consultant CIR.")
        parts.append("- Memory V2 sert aux analogies et au style ; elle ne doit jamais ajouter un fait absent des sources du dossier courant.")
        parts.append("- Interdiction de garder des titres génériques comme : Non-transférabilité, Cause racine, Performance insuffisante, Comportement instable, Compromis entre contraintes.")
        parts.append("- Construis des titres techniques spécifiques à partir des preuves : objet technique + phénomène non maîtrisé + contrainte ou condition d'usage.")
        parts.append("- Ne force aucun axe métier prédéfini : mécanique, chimie, architecture, IA ou LLM doivent suivre la même logique objet/phénomène/contrainte/incertitude/preuves.")
        parts.append("")
        parts.append("Résumé Frascati déjà calculé par le NLP / Frascati :")
        parts.append(json.dumps(frascati_summary, ensure_ascii=False, indent=2))
        parts.append("")
        parts.append("Consigne spéciale pour la section 'Justification Frascati du score' :")
        parts.append("- La justification doit être spécifique au projet courant, jamais générique.")
        parts.append("- Explique pourquoi le score est cohérent avec les preuves du projet.")
        parts.append("- Explique les éléments qui augmentent le score et ceux qui le limitent.")
        parts.append("- Explique ce qui a été vérifié par NLP/Frascati : rôles, scores, décisions, signaux candidats, méthodes, résultats, paramètres, limites.")
        parts.append("")

        if isinstance(ai_detection_report, dict) and ai_detection_report.get("ok"):
            parts.append("Contrôle IA documentaire déjà disponible :")
            parts.append(self.ai_detection_prompt_block(ai_detection_report))
            parts.append("")
        else:
            parts.append("Contrôle IA documentaire déjà disponible : aucun rapport exploitable ou rapport absent.")
            parts.append("")

        parts.append("Mémoire de style CIR par section :")
        parts.append(self._style_blocks_for_prompt(style_memory_report, compact=False))
        parts.append("")
        parts.append("Sources RAG compactes par rôle :")
        parts.append(build_sources_block("Contexte global", sections.get("global", []), budgets["global"]))
        parts.append(build_sources_block("Objectifs", sections.get("objectifs", []), budgets["objectifs"]))
        parts.append(build_sources_block("Verrous", sections.get("verrous", []), budgets["verrous"]))
        parts.append(build_sources_block("Démarches / méthodes", sections.get("methodes", []), budgets["methodes"]))
        parts.append(build_sources_block("Résultats", sections.get("resultats", []), budgets["resultats"]))
        parts.append(build_sources_block("Paramètres", sections.get("parametres", []), budgets["parametres"]))
        parts.append(build_sources_block("Limites / points à vérifier", sections.get("limites", []), budgets["limites"]))
        parts.append(build_sources_block("Axe problèmes transverses", sections.get("axe_problemes_transverses", []), 5))
        parts.append(build_sources_block("Axe contraintes transverses", sections.get("axe_contraintes_transverses", []), 5))
        parts.append(build_sources_block("Axe preuves et résultats", sections.get("axe_preuves_resultats", []), 5))
        parts.append("")
        parts.append("Réponse attendue exactement avec ces titres Markdown :")
        parts.append("## Lecture Frascati du dossier")
        parts.append("## Justification Frascati du score")
        parts.append(f"## {MEMORY_V2_SECTION_TITLE}")
        parts.append(f"## {CIR_PREVIOUS_SECTION_TITLE}")
        parts.append("## Synthèse stratégique du projet")
        parts.append("## Objectif global reformulé")
        parts.append(f"## {SIGNAL_SECTION_TITLE}")
        parts.append("## Démarche expérimentale détectée")
        parts.append("## Résultats et métriques disponibles")
        parts.append("## Paramètres et contraintes techniques")
        parts.append("## Points à valider par le consultant")
        parts.append("")
        parts.append("Contraintes de rédaction :")
        parts.append("- Ne vise aucun nombre de signaux : fusionne seulement les passages portant sur la même incertitude et conserve séparément les incertitudes réellement distinctes.")
        parts.append("- Pour chaque signal candidat : titre technique provisoire reformulé par le LLM, difficulté observée, phénomène possiblement non maîtrisé, preuves/documents, statut de validation.")
        parts.append("- Le titre doit être exploitable pour EnnoScholar après validation consultant : pas un simple mot-clé, pas un thème générique.")
        parts.append("- Dans la démarche : organiser par axe technique, sans inventer de protocole.")
        parts.append("- Le nombre d'étapes ne prouve jamais la R&D. Distingue les étapes reliées à une incertitude, une hypothèse, une évaluation et un apprentissage des procédures d'ingénierie courante.")
        parts.append("- Si l'audit de lisibilité signale un raccourci possible, explique uniquement à partir des preuves pourquoi la solution finale ne pouvait pas être choisie dès le départ ; sinon marque ce point à valider.")
        parts.append("- Dans les résultats : séparer résultats chiffrés, observations qualitatives, résultats insuffisants/à valider.")
        parts.append("- Ne fabrique jamais de valeur, de résultat ou de document source.")

        prompt = "\n".join(parts)

        try:
            llm_max_chars = int(os.getenv("ENNOSMART_LLM_MAX_PROMPT_CHARS", "18000"))
        except Exception:
            llm_max_chars = 18000
        try:
            requested_hard_max = int(os.getenv("ENNOSMART_DIAG_FAST_PROMPT_HARD_MAX", str(llm_max_chars)))
        except Exception:
            requested_hard_max = llm_max_chars

        hard_max = max(12000, min(requested_hard_max, llm_max_chars))

        if len(prompt) > hard_max:
            print(f"[EnnoDiagnostic][FAST_PROMPT][V96] prompt_chars={len(prompt)} > {hard_max}, compression contractuelle")

            parts2: List[str] = []
            parts2.append("Tu es EnnoDiagnostic, agent de synthèse CIR.")
            parts2.append("")
            parts2.append("Règles strictes :")
            parts2.append("- Les faits doivent venir uniquement des sources RAG/Chroma du dossier courant.")
            parts2.append("- Ne refais pas Frascati et ne recalcule jamais le score.")
            parts2.append("- Le score Frascati vient du NLP / module Frascati ; tu dois seulement le justifier.")
            parts2.append("- La justification Frascati doit être spécifique au projet courant, jamais générique.")
            parts2.append("- Ne dis jamais que le CIR ou qu’un verrou est validé ; écris à valider par le consultant et à confirmer par EnnoScholar si nécessaire.")
            parts2.append("")
            parts2.append("Résumé Frascati déjà calculé par le NLP / Frascati :")
            parts2.append(json.dumps(frascati_summary, ensure_ascii=False, indent=2))
            parts2.append("")

            if isinstance(ai_detection_report, dict) and ai_detection_report.get("ok"):
                parts2.append("Contrôle IA documentaire déjà disponible :")
                parts2.append(self.ai_detection_prompt_block(ai_detection_report))
                parts2.append("")
            else:
                parts2.append("Contrôle IA documentaire déjà disponible : aucun rapport exploitable ou rapport absent.")
                parts2.append("")

            parts2.append(self._memory_v2_context_block(memory_v2_report, max_chars=700))
            parts2.append("")
            parts2.append(self._cir_previous_comparison_block(cir_memory_report, max_items=3, max_chars=700))
            parts2.append("")

            parts2.append("Mémoire de style CIR par section :")
            parts2.append(self._style_blocks_for_prompt(style_memory_report, compact=True))
            parts2.append("")
            parts2.append("Sources RAG compactes par rôle :")
            parts2.append(build_sources_block_compact("Contexte global", sections.get("global", []), 2, 220))
            parts2.append(build_sources_block_compact("Objectifs", sections.get("objectifs", []), 2, 220))
            parts2.append(build_sources_block_compact("Verrous", sections.get("verrous", []), 5, 250))
            parts2.append(build_sources_block_compact("Démarches / méthodes", sections.get("methodes", []), 4, 240))
            parts2.append(build_sources_block_compact("Résultats", sections.get("resultats", []), 4, 240))
            parts2.append(build_sources_block_compact("Paramètres", sections.get("parametres", []), 3, 220))
            parts2.append(build_sources_block_compact("Limites / points à vérifier", sections.get("limites", []), 3, 220))
            parts2.append(build_sources_block_compact("Axe problèmes transverses", sections.get("axe_problemes_transverses", []), 2, 220))
            parts2.append(build_sources_block_compact("Axe contraintes transverses", sections.get("axe_contraintes_transverses", []), 2, 220))
            parts2.append(build_sources_block_compact("Axe preuves et résultats", sections.get("axe_preuves_resultats", []), 2, 220))
            parts2.append("")
            parts2.append("Réponse attendue exactement avec ces titres Markdown :")
            parts2.append("## Lecture Frascati du dossier")
            parts2.append("## Justification Frascati du score")
            parts2.append(f"## {MEMORY_V2_SECTION_TITLE}")
            parts2.append(f"## {CIR_PREVIOUS_SECTION_TITLE}")
            parts2.append("## Synthèse stratégique du projet")
            parts2.append("## Objectif global reformulé")
            parts2.append(f"## {SIGNAL_SECTION_TITLE}")
            parts2.append("## Démarche expérimentale détectée")
            parts2.append("## Résultats et métriques disponibles")
            parts2.append("## Paramètres et contraintes techniques")
            parts2.append("## Points à valider par le consultant")
            parts2.append("")
            parts2.append("Contraintes : verrous techniques sourcés, justification Frascati spécifique, pas de valeurs inventées.")

            prompt = "\n".join(parts2)

            if len(prompt) > hard_max:
                overflow_note = "\n\n[Contexte réduit : Frascati, score IA, mémoire style et titres obligatoires conservés. Sources complètes disponibles dans chroma_sections.]"
                prompt = prompt[: max(1000, hard_max - len(overflow_note))].rstrip() + overflow_note

        return prompt

    # =====================================================
    # Fallbacks and dedicated Frascati justification
    # =====================================================

    def build_frascati_section(self, frascati_summary: Dict[str, Any], ai_detection_report: Optional[Dict[str, Any]] = None) -> str:
        lines = []
        lines.append("## Lecture Frascati du dossier")
        lines.append(f"- Score Frascati officiel récupéré depuis le NLP : {frascati_summary.get('average_frascati_score')}")
        lines.append(f"- Nombre de scores utilisés : {frascati_summary.get('scores_count')}")
        lines.append(f"- Source du score : {frascati_summary.get('score_source')}")
        lines.append(f"- Décisions détectées : {frascati_summary.get('decisions_count')}")
        lines.append(f"- Niveaux de signaux candidats : {frascati_summary.get('candidate_levels_count')}")
        lines.append(
            "- Score de l'étude d'éligibilité (critères Frascati + démarche) : "
            f"{frascati_summary.get('eligibility_assessment_score')}"
        )
        demarche = compact_demarche_audit(frascati_summary.get("demarche_legibility"))
        if demarche:
            lines.append(
                "- Pertinence de la démarche : "
                f"{demarche.get('label')} ; "
                f"étapes R&D justifiées={demarche.get('research_justified_steps_count', 0)}, "
                f"ingénierie classique={demarche.get('routine_engineering_steps_count', 0)}, "
                f"à expliquer={demarche.get('unexplained_steps_count', 0)}."
            )
        lines.append("")
        lines.append(
            "Ce score reprend les métadonnées produites pendant le NLP et le contrôle Frascati. "
            "Il ne constitue pas une validation finale du CIR ; les verrous doivent rester à valider par le consultant."
        )

        if isinstance(ai_detection_report, dict) and ai_detection_report.get("ok"):
            lines.append("")
            lines.append("### Contrôle IA documentaire")
            lines.append(self.ai_detection_prompt_block(ai_detection_report))

        return "\n".join(lines)

    def fallback_section_without_llm(self, title: str, sources: List[Dict[str, Any]], max_items: int = 8) -> str:
        lines = [f"## {title}"]

        if not sources:
            lines.append("- Aucun élément récupéré depuis Chroma.")
            return "\n".join(lines)

        for src in sources[:max_items]:
            meta = meta_of(src)
            doc = source_doc(src)
            role = clean_text(meta.get("role"))
            decision = clean_text(meta.get("frascati_decision") or meta.get("decision"))
            score = meta.get("frascati_score", "")
            page = meta.get("page") or meta.get("page_number") or src.get("page")
            txt = truncate(source_text(src), 330)
            lines.append(
                f"- Document : {doc or '-'} | page={page if page not in (None, '') else '-'} | "
                f"rôle={role or '-'} | Frascati={decision or '-'} | score={score if score != '' else '-'}\n"
                f"  {txt}"
            )

        return "\n".join(lines)


    def _frascati_contextual_questions(
        self,
        evidence_sources: List[Dict[str, Any]],
        frascati_summary: Dict[str, Any],
        max_questions: int = 6,
    ) -> List[Dict[str, Any]]:
        """
        Construit des questions de qualification contextualisées pour expliquer
        le score Frascati sans le recalculer.

        Ces questions ne sont pas des règles codées par projet.
        Elles traduisent ce que le score NLP/Frascati a cherché à qualifier :
        incertitude, méthode, résultats, paramètres, limites et preuves sources.
        """
        questions: List[Dict[str, Any]] = []
        seen = set()

        def role_of(src: Dict[str, Any]) -> str:
            meta = meta_of(src)
            return clean_text(meta.get("role") or meta.get("final_role") or src.get("role") or "")

        def decision_of(src: Dict[str, Any]) -> str:
            meta = meta_of(src)
            return clean_text(meta.get("frascati_decision") or meta.get("decision") or meta.get("verrou_candidate_level") or "à vérifier")

        def add(question: str, src: Dict[str, Any], status: str, why: str) -> None:
            key = re.sub(r"\s+", " ", question.lower()).strip()
            if not question or key in seen:
                return
            seen.add(key)
            questions.append({
                "question": question,
                "provisional_answer": status,
                "why": why,
                "document": source_doc(src) or "Source Chroma",
                "role": role_of(src) or "-",
                "decision": decision_of(src),
                "evidence": truncate(source_text(src), 360),
            })

        for src in evidence_sources or []:
            if len(questions) >= max_questions:
                break

            txt = repair_mojibake(source_text(src))
            low = txt.lower()
            role = role_of(src).lower()
            decision = decision_of(src)

            if not txt:
                continue

            if "verrou" in role or "limite" in role or any(w in low for w in ["incertitude", "nécessaire", "necessaire", "écart", "ecart", "différences", "differences", "ne présume pas", "reste"]):
                add(
                    "Le passage signale-t-il une incertitude technique spécifique au dossier courant, plutôt qu’une simple tâche d’ingénierie ?",
                    src,
                    f"Réponse provisoire : {decision}.",
                    "Cette question pèse sur le score car le NLP/Frascati cherche d’abord des passages exprimant une limite, une difficulté, un écart ou une hypothèse à vérifier.",
                )
                continue

            if "methode" in role or "méthode" in role or any(w in low for w in ["essai", "test", "simulation", "valider", "validation", "comparaison", "modèle", "modele"]):
                add(
                    "Les travaux décrits correspondent-ils à une démarche de vérification ou d’expérimentation visant à lever l’incertitude ?",
                    src,
                    f"Réponse provisoire : {decision}.",
                    "Cette question augmente le score lorsque les sources décrivent des essais, comparaisons, simulations ou validations reliés à un problème technique.",
                )
                continue

            if "resultat" in role or "résultat" in role or re.search(r"\b\d+([,.]\d+)?\s?%|\b\d+([,.]\d+)?\s?(ms|s|m²|m2|ghz|db)\b", low, flags=re.I):
                add(
                    "Les résultats disponibles permettent-ils de caractériser le comportement observé, tout en laissant des points techniques à expliquer ou confirmer ?",
                    src,
                    f"Réponse provisoire : {decision}.",
                    "Cette question contribue au score lorsque des mesures ou résultats existent, mais elle ne valide pas automatiquement le verrou sans analyse consultant et EnnoScholar.",
                )
                continue

            if "parametre" in role or "paramètre" in role or any(w in low for w in ["paramètre", "parametre", "seuil", "configuration", "condition", "surface", "densité", "densite"]):
                add(
                    "Les paramètres ou conditions d’usage identifiés influencent-ils la robustesse, la représentativité ou la performance technique ?",
                    src,
                    f"Réponse provisoire : {decision}.",
                    "Cette question aide à expliquer le score car les paramètres peuvent montrer que le problème dépend de conditions techniques non triviales.",
                )
                continue

        if not questions and evidence_sources:
            src = evidence_sources[0]
            add(
                "Les sources indexées contiennent-elles assez d’indices techniques pour prioriser des signaux candidats avant validation humaine ?",
                src,
                "Réponse provisoire : à vérifier.",
                "Cette question correspond au rôle du score Frascati dans EnnoDiagnostic : prioriser, pas valider.",
            )

        return questions[:max_questions]

    _REASONING_LEAK_MARKERS = [
        "we need to",
        "we must",
        "let's craft",
        "need to output",
        "must output",
        "the user wants",
        "we have the score",
        "we should",
        "i will",
        "je dois",
        "je vais",
    ]

    def _has_llm_reasoning_leak(self, text: Any) -> bool:
        value = repair_mojibake(text).lower()
        if not value:
            return False
        hits = sum(1 for marker in self._REASONING_LEAK_MARKERS if marker in value)
        # Un seul "we need to" au début suffit ; plusieurs marqueurs n'importe où aussi.
        if value.strip().startswith(("we need", "we must", "let's craft", "need to output")):
            return True
        return hits >= 2

    def _sanitize_llm_section_or_empty(self, content: Any, title: str) -> str:
        """
        Nettoie une sortie LLM dédiée.
        Si le modèle renvoie son raisonnement/prompt au lieu de la section finale,
        on retourne vide pour forcer le fallback déterministe.
        """
        text = repair_mojibake(content).strip()
        if not text:
            return ""

        # Retirer les fences éventuelles.
        text = re.sub(r"^```(?:markdown|md)?\s*", "", text, flags=re.I).strip()
        text = re.sub(r"\s*```$", "", text).strip()

        # Si le texte contient clairement du raisonnement interne, on refuse.
        if self._has_llm_reasoning_leak(text):
            return ""

        if not text.startswith("##"):
            text = f"## {title}\n{text}"

        section = extract_markdown_section(text, title)
        if not section:
            return ""

        if self._has_llm_reasoning_leak(section):
            return ""

        return text

    def build_frascati_justification_section(
        self,
        frascati_summary: Dict[str, Any],
        sections: Dict[str, List[Dict[str, Any]]],
        ai_detection_report: Optional[Dict[str, Any]] = None,
    ) -> str:
        score = frascati_summary.get("average_frascati_score")
        scores_count = frascati_summary.get("scores_count")
        decisions = frascati_summary.get("decisions_count") or {}
        levels = frascati_summary.get("candidate_levels_count") or {}

        evidence_sources = dedupe_sources(
            (sections.get("verrous") or [])[:8]
            + (sections.get("methodes") or [])[:5]
            + (sections.get("resultats") or [])[:5]
            + (sections.get("parametres") or [])[:4]
            + (sections.get("limites") or [])[:4]
            + (sections.get("axe_problemes_transverses") or [])[:4]
            + (sections.get("axe_contraintes_transverses") or [])[:4]
            + (sections.get("axe_preuves_resultats") or [])[:4],
            max_items=18,
        )

        docs: List[str] = []
        evidence_lines: List[str] = []
        joined_text = " ".join(source_text(src) for src in evidence_sources).lower()

        # Extraction générique de thèmes dominants depuis les sources, sans lexique projet.
        theme_terms: List[str] = []
        stop_terms = {
            "verrou", "verrous", "signal", "signaux", "technique", "techniques", "candidat", "candidats",
            "source", "sources", "document", "documents", "frascati", "validation", "consultant", "projet",
            "preuve", "preuves", "dossier", "methode", "méthode", "resultat", "résultat", "analyse",
            "avec", "dans", "pour", "plus", "sont", "cette", "cela", "ainsi", "comme", "être", "etre",
        }
        for tok in re.findall(r"[a-zA-ZÀ-ÿ0-9][a-zA-ZÀ-ÿ0-9%°µ/\-.']{3,}", joined_text):
            t = tok.strip("-_. '").lower()
            if t and t not in stop_terms:
                theme_terms.append(t)

        counts: Dict[str, int] = {}
        for t in theme_terms:
            counts[t] = counts.get(t, 0) + 1
        themes = [t for t, _ in sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:8]]

        for src in evidence_sources:
            doc = source_doc(src)
            if doc and doc not in docs:
                docs.append(doc)

            txt = truncate(source_text(src), 240)
            meta = meta_of(src)
            role = clean_text(meta.get("role"))
            decision = clean_text(meta.get("frascati_decision") or meta.get("decision"))
            if txt:
                evidence_lines.append(
                    f"- {doc or 'Source Chroma'}"
                    f"{f' ({role})' if role else ''}"
                    f"{f' — décision {decision}' if decision else ''} : {txt}"
                )

        if not evidence_lines:
            evidence_lines.append("- Aucune preuve Chroma suffisamment explicite n’a été retrouvée : à valider par le consultant.")

        contextual_questions = self._frascati_contextual_questions(
            evidence_sources=evidence_sources,
            frascati_summary=frascati_summary,
            max_questions=6,
        )

        ai_summary = ""
        if isinstance(ai_detection_report, dict) and ai_detection_report.get("ok"):
            ai_summary = self.ai_detection_prompt_block(ai_detection_report)

        lines: List[str] = []
        lines.append("## Justification Frascati du score")
        lines.append("")
        lines.append("### Pourquoi ce score ?")
        lines.append(
            f"Le score Frascati de {score if score is not None else 'non disponible'} provient du module NLP/Frascati exécuté pendant la préparation des sources. "
            f"Il s’appuie sur {scores_count} évaluation(s) de groupe enregistrée(s) dans le résultat NLP/Frascati. "
            "Ces éléments sont des signaux candidats, pas des verrous validés. Le LLM et Memory V2 ne recalculent pas ce score."
        )
        if themes:
            lines.append("Dans ce dossier, le score est porté par les signaux techniques candidats suivants : " + "; ".join(themes) + ".")
        if docs:
            lines.append("Les documents principalement mobilisés sont : " + "; ".join(docs[:8]) + ".")
        lines.append("")
        lines.append("### Questions de qualification utilisées pour interpréter le score")
        if contextual_questions:
            for i, q in enumerate(contextual_questions, start=1):
                lines.append(f"{i}. {q.get('question')}")
                lines.append(f"   - Réponse provisoire : {q.get('provisional_answer')}")
                lines.append(f"   - Pourquoi cela compte dans ce projet : {q.get('why')}")
                lines.append(f"   - Source : {q.get('document')} | rôle={q.get('role')} | décision={q.get('decision')}")
                lines.append(f"   - Indice : {q.get('evidence')}")
        else:
            lines.append("- Aucune question contextualisée n’a pu être construite automatiquement : validation consultant nécessaire.")
        lines.append("")
        lines.append("### Éléments qui augmentent le score")
        lines.extend(evidence_lines[:8])
        lines.append("")
        lines.append("### Éléments qui limitent le score")
        lines.append(f"- Les décisions Frascati restent au stade de qualification avant EnnoScholar : {json.dumps(decisions, ensure_ascii=False)}.")
        lines.append(f"- Les niveaux candidats détectés montrent que les signaux ne sont pas confirmés comme verrous CIR : {json.dumps(levels, ensure_ascii=False)}.")
        lines.append("- La justification CIR finale nécessite EnnoScholar et validation consultant pour confirmer le caractère non directement résoluble des difficultés techniques.")
        lines.append("- Les résultats chiffrés et la cause technique exacte doivent être reliés clairement aux signaux retenus pour EnnoScholar.")
        lines.append("- Les contraintes industrielles doivent être séparées des incertitudes technologiques réellement investiguées.")
        lines.append("- Memory V2 peut aider à reformuler ou comparer, mais elle n’est pas utilisée comme preuve factuelle du score courant.")
        lines.append("")
        lines.append("### Ce qui a été vérifié")
        lines.append(f"- Nombre de scores Frascati exploités pour prioriser les signaux candidats : {scores_count}.")
        lines.append(f"- Interprétations Frascati récupérées depuis les évaluations NLP : {json.dumps(decisions, ensure_ascii=False)}.")
        lines.append(f"- Niveaux de signaux candidats : {json.dumps(levels, ensure_ascii=False)}.")
        lines.append("- Présence de passages de rôle verrou, méthode, résultat, paramètre et limite dans les sources indexées ; le rôle verrou signifie candidat à vérifier, pas validation finale.")
        if ai_summary:
            lines.append("- Cohérence avec le contrôle IA documentaire disponible, distinct de la décision CIR.")
        lines.append("")
        lines.append("### Points à valider par le consultant")
        lines.append("- Confirmer quelles questions de qualification correspondent réellement à une incertitude technologique au sens CIR.")
        lines.append("- Vérifier que les preuves documentaires rattachent bien les essais, mesures et observations aux signaux retenus.")
        lines.append("- Confirmer les résultats manquants ou partiels avant EnnoScholar et avant toute décision d’éligibilité.")
        if ai_summary:
            lines.append("")
            lines.append("### Rappel contrôle IA documentaire")
            lines.append(ai_summary)

        return "\n".join(lines)


    def generate_frascati_justification_section(
        self,
        sections: Dict[str, List[Dict[str, Any]]],
        frascati_summary: Dict[str, Any],
        ai_detection_report: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        title = "Justification Frascati du score"

        selected_sources = dedupe_sources(
            (sections.get("verrous") or [])[:10]
            + (sections.get("methodes") or [])[:5]
            + (sections.get("resultats") or [])[:5]
            + (sections.get("parametres") or [])[:4]
            + (sections.get("limites") or [])[:4]
            + (sections.get("axe_problemes_transverses") or [])[:4]
            + (sections.get("axe_contraintes_transverses") or [])[:4]
            + (sections.get("axe_preuves_resultats") or [])[:4],
            max_items=22,
        )

        fallback_content = self.build_frascati_justification_section(
            frascati_summary=frascati_summary,
            sections=sections,
            ai_detection_report=ai_detection_report,
        )

        if self.llm is None:
            return {
                "ok": True,
                "title": title,
                "content": fallback_content,
                "mode": "fallback_without_llm",
                "prompt_chars": 0,
                "sources_count": len(selected_sources),
            }

        prompt = f"""
Tu es EnnoDiagnostic, agent d'aide à la qualification CIR.

RÈGLE ABSOLUE :
Le score Frascati est déjà calculé par le NLP / module Frascati.
Tu ne dois jamais recalculer le score.
Tu ne dois jamais inventer un autre score.
Tu dois uniquement JUSTIFIER le score fourni à partir des preuves du projet courant.
Ce score priorise des signaux candidats avant EnnoScholar ; il ne valide aucun verrou CIR.
Memory V2 peut aider à reformuler ou contextualiser, mais ne doit jamais être utilisée comme preuve du score.

Score et décisions Frascati calculés par le backend :
{json.dumps(frascati_summary, ensure_ascii=False, indent=2)}

Contrôle IA documentaire :
{self.ai_detection_prompt_block(ai_detection_report or {})}

Sources du projet courant :
{build_sources_block("Sources utilisées pour justifier le score Frascati", selected_sources, max_items=18)}

Ta réponse doit être spécifique au projet courant.
Interdiction d'écrire une justification générique.
Ne parle pas de manière abstraite : cite les phénomènes, essais, paramètres, documents et limites présents dans les sources.
Si une preuve manque, écris clairement : à valider par le consultant.

Tu dois produire exactement cette section Markdown, sans explication avant, sans raisonnement, sans code fence :

## Justification Frascati du score

### Pourquoi ce score ?
Explique pourquoi le score obtenu est cohérent avec les preuves du projet courant.
Explique aussi pourquoi ce score ne correspond pas à une validation maximale ni à un verrou scientifiquement défendable.

### Questions de qualification utilisées pour interpréter le score
Liste 4 à 6 questions concrètes que le diagnostic cherche à qualifier dans CE projet.
Pour chaque question :
- donne la réponse provisoire : oui partiel / non démontré / à vérifier ;
- indique la preuve ou le document qui justifie cette réponse provisoire.
Exemple de forme :
1. La représentativité de X est-elle démontrée dans les conditions Y ?
   - Réponse provisoire : à vérifier.
   - Indice source : document ..., passage ...

### Éléments qui augmentent le score
- Liste les éléments techniques concrets détectés dans les sources.
- Chaque point doit être spécifique au projet et relié à une preuve ou un document.

### Éléments qui limitent le score
- Explique pourquoi le score n'est pas maximal.
- Mentionne les preuves manquantes, incomplètes ou à confirmer.
- Rappelle que Memory V2 n'est pas une preuve factuelle du dossier courant.

### Ce qui a été vérifié
- Explique ce que le NLP/Frascati a vérifié : rôles des passages, signaux candidats, méthodes, résultats, paramètres, limites, scores et décisions.

### Points à valider par le consultant
- Liste les validations humaines et scientifiques nécessaires avant décision CIR, notamment le passage par EnnoScholar.

Contraintes :
- Ne pas inventer de preuve.
- Ne pas utiliser de justification générique.
- Citer les documents quand ils sont disponibles.
- Ne jamais écrire que le CIR ou qu’un verrou est validé avant EnnoScholar et validation consultant.
- Ne jamais recalculer ni modifier le score.
- Ne jamais écrire ton raisonnement interne comme "We need", "We must", "Let's craft", "Je dois".
""".strip()

        try:
            raw_content = self.llm.generate(
                prompt,
                temperature=float(os.getenv("ENNOSMART_FRASCATI_JUSTIFICATION_TEMPERATURE", "0.03")),
                max_output_tokens=int(os.getenv("ENNOSMART_FRASCATI_JUSTIFICATION_MAX_TOKENS", "1300")),
                retries=int(os.getenv("ENNOSMART_FRASCATI_JUSTIFICATION_RETRIES", "1")),
            )
            content = self._sanitize_llm_section_or_empty(raw_content, title=title)

            if not content:
                return {
                    "ok": False,
                    "title": title,
                    "content": fallback_content,
                    "mode": "fallback_after_reasoning_leak_or_invalid_llm_justification",
                    "prompt_chars": len(prompt),
                    "sources_count": len(selected_sources),
                    "llm_raw_preview": truncate(raw_content, 500),
                }

            section_text = extract_markdown_section(content, title)
            if len(section_text) < 450:
                return {
                    "ok": False,
                    "title": title,
                    "content": fallback_content,
                    "mode": "fallback_after_too_short_llm_justification",
                    "prompt_chars": len(prompt),
                    "sources_count": len(selected_sources),
                    "llm_section_chars": len(section_text),
                }

            return {
                "ok": True,
                "title": title,
                "content": content,
                "mode": "llm_dedicated_frascati_justification",
                "prompt_chars": len(prompt),
                "sources_count": len(selected_sources),
            }

        except Exception as e:
            print(f"[EnnoDiagnostic][FRASCATI_JUSTIFICATION][ERROR] {e}")
            return {
                "ok": False,
                "title": title,
                "content": fallback_content,
                "mode": "fallback_after_llm_error",
                "error": str(e),
                "prompt_chars": len(prompt),
                "sources_count": len(selected_sources),
            }


    def fallback_without_llm(
        self,
        sections: Dict[str, List[Dict[str, Any]]],
        frascati_summary: Dict[str, Any],
        cir_memory_report: Optional[Dict[str, Any]] = None,
    ) -> str:
        return "\n\n".join(
            [
                self.build_frascati_section(frascati_summary),
                self.build_frascati_justification_section(frascati_summary, sections),
                self.render_cir_previous_comparison_section(cir_memory_report),
                self.fallback_section_without_llm("Synthèse stratégique du projet", sections.get("global", []), 6),
                self.fallback_section_without_llm("Objectif global reformulé", sections.get("objectifs", []), 6),
                self.fallback_section_without_llm(SIGNAL_SECTION_TITLE, sections.get("verrous", []), 12),
                self.fallback_section_without_llm("Démarche expérimentale détectée", sections.get("methodes", []), 8),
                self.fallback_section_without_llm("Résultats et métriques disponibles", sections.get("resultats", []), 8),
                self.fallback_section_without_llm("Paramètres et contraintes techniques", sections.get("parametres", []), 8),
                self.fallback_section_without_llm("Points à valider par le consultant", sections.get("limites", []) + sections.get("verrous", []), 10),
            ]
        ).strip()

    # =====================================================
    # Compatibility
    # =====================================================

    def build_section_prompt(
        self,
        title: str,
        instruction: str,
        role: str,
        sources: List[Dict[str, Any]],
        frascati_summary: Dict[str, Any],
        style_memory_report: Optional[Dict[str, Any]] = None,
        cir_memory_report: Optional[Dict[str, Any]] = None,
        include_cir_previous_comparison: bool = False,
        extra_sources: Optional[List[Dict[str, Any]]] = None,
        max_sources: int = 10,
        max_extra_sources: int = 4,
    ) -> str:
        extra_sources = extra_sources or []
        style_role = self._style_role_for_section(role, title)
        parts = [
            "Tu es EnnoDiagnostic, agent de synthèse CIR.",
            "Les faits doivent venir uniquement des sources Chroma du dossier courant.",
            "Les exemples de style et la comparaison historique ne sont jamais des preuves du projet courant.",
            "Tu ne dois pas refaire Frascati ni recalculer le score.",
            f"Objectif : rédiger uniquement la section suivante : {title}",
            instruction,
            "STYLE UNIQUEMENT :",
            self._style_memory_for_role(style_memory_report, style_role, max_chars=900),
            build_sources_block(f"Sources RAG utiles pour {title}", sources, max_sources),
        ]
        if include_cir_previous_comparison or title == CIR_PREVIOUS_SECTION_TITLE:
            parts.append(self._cir_previous_comparison_block(cir_memory_report, max_items=8, max_chars=1600))
            parts.append(
                "Pour cette seule section, explique les rapprochements historiques sans affirmer un fait courant absent des sources RAG. "
                "Utilise les statuts nouveau, persistant, modifié ou à confirmer quand ils sont fournis."
            )
        if extra_sources:
            parts.append(build_sources_block("Contexte complémentaire limité", extra_sources, max_extra_sources))
        parts.append(f"## {title}")
        return "\n\n".join(parts)

    def generate_llm_section(
        self,
        title: str,
        instruction: str,
        role: str,
        sources: List[Dict[str, Any]],
        frascati_summary: Dict[str, Any],
        style_memory_report: Optional[Dict[str, Any]],
        cir_memory_report: Optional[Dict[str, Any]] = None,
        include_cir_previous_comparison: bool = False,
        extra_sources: Optional[List[Dict[str, Any]]] = None,
        max_sources: int = 10,
        max_extra_sources: int = 4,
        max_output_tokens: int = 900,
    ) -> Dict[str, Any]:
        if self.llm is None:
            return {
                "ok": True,
                "title": title,
                "content": self.fallback_section_without_llm(title, sources, max_items=max_sources),
                "mode": "fallback_without_llm",
                "prompt_chars": 0,
                "sources_count": len(sources or []),
            }

        prompt = self.build_section_prompt(
            title=title,
            instruction=instruction,
            role=role,
            sources=sources,
            frascati_summary=frascati_summary,
            style_memory_report=style_memory_report,
            cir_memory_report=cir_memory_report,
            include_cir_previous_comparison=include_cir_previous_comparison,
            extra_sources=extra_sources,
            max_sources=max_sources,
            max_extra_sources=max_extra_sources,
        )
        try:
            content = self.llm.generate(prompt, temperature=0.08, max_output_tokens=max_output_tokens, retries=2)
            content = repair_mojibake(content)
            if not content.startswith("##"):
                content = f"## {title}\n{content}"
            return {"ok": True, "title": title, "content": content, "mode": "llm_section_by_section", "prompt_chars": len(prompt), "sources_count": len(sources or [])}
        except Exception as e:
            return {
                "ok": False,
                "title": title,
                "content": self.fallback_section_without_llm(title, sources, max_items=max_sources),
                "mode": "fallback_after_llm_error",
                "error": str(e),
                "prompt_chars": len(prompt),
                "sources_count": len(sources or []),
            }

    def _render_reformulated_verrous_section(self, items: List[Dict[str, Any]]) -> str:
        lines = [f"## {SIGNAL_SECTION_TITLE}"]
        visible = [
            item for item in (items or [])
            if isinstance(item, dict) and item.get("display_as_lock", True)
        ]
        if not visible:
            lines.append("Aucun verrou principal exploitable n'a été consolidé automatiquement. À valider par le consultant.")
            return "\n\n".join(lines)

        for index, item in enumerate(visible, start=1):
            title = clean_text(item.get("title")) or f"Signal R&D candidat {index}"
            explanation = clean_text(
                item.get("consultant_explanation")
                or item.get("why_agent_found_verrou")
                or item.get("justification")
                or item.get("text")
            )
            uncertainty = clean_text(item.get("scientific_lock"))
            documents = clean_text(item.get("document"))
            status = clean_text(item.get("consultant_status") or "en_attente")
            lines.append(f"### {index}. {title}")
            if explanation:
                lines.append(explanation)
            if uncertainty:
                lines.append(f"Incertitude à qualifier : {uncertainty}")
            if documents:
                lines.append(f"Documents courants associés : {documents}")
            lines.append(f"Statut : {status} — validation consultant nécessaire.")
        return "\n\n".join(lines)

    def _generate_fast_markdown_fallback(
        self,
        sections: Dict[str, List[Dict[str, Any]]],
        frascati_summary: Dict[str, Any],
        style_memory_report: Dict[str, Any],
        ai_detection_report: Dict[str, Any],
        cir_memory_report: Dict[str, Any],
        memory_v2_report: Dict[str, Any],
    ) -> Tuple[str, str]:
        """Secours compatible si le présentateur sectionnel est indisponible."""
        prompt = self._build_fast_single_prompt(
            sections=sections,
            frascati_summary=frascati_summary,
            style_memory_report=style_memory_report,
            ai_detection_report=ai_detection_report,
            cir_memory_report=cir_memory_report,
            memory_v2_report=memory_v2_report,
        )
        if self.llm is None:
            return self.fallback_without_llm(sections, frascati_summary, cir_memory_report), prompt
        try:
            content = self.llm.generate(
                prompt,
                temperature=float(os.getenv("ENNOSMART_DIAG_FAST_TEMPERATURE", "0.05")),
                max_output_tokens=int(os.getenv("ENNOSMART_DIAG_FAST_MAX_OUTPUT_TOKENS", "4200")),
                retries=1,
                request_name="ennodiagnostic:complete_report_fallback",
            )
            content = normalize_report_vocabulary(repair_mojibake(content))
            if content.strip():
                return content.strip(), prompt
        except Exception as exc:
            print(f"[EnnoDiagnostic][V182_FAST_FALLBACK][WARN] {exc}")
        return self.fallback_without_llm(sections, frascati_summary, cir_memory_report), prompt

    def generate_diagnostic(self, save: bool = True) -> Dict[str, Any]:
        """Exécute RAG -> sections LLM -> verrous RAG -> comparaison N-1.

        Les preuves du projet courant restent séparées des exemples de style,
        de Memory V2 et du CIR précédent.
        """
        started_at = time.time()
        self.diagnostic_dir.mkdir(parents=True, exist_ok=True)

        sections = self.retrieve_all_sections()
        frascati_sources = sections.get("_frascati_verrous") or sections.get("verrous") or []
        frascati_summary = self.frascati_summary_from_chroma(frascati_sources)
        ai_detection_report = self.load_ai_detection_report()
        style_memory_report = self.load_style_memory_context(sections)
        memory_v2_report = self.load_memory_v2_context(sections)
        previous_verrou_context = self.load_previous_verrou_context()
        self._last_style_memory_report = style_memory_report
        self._last_memory_v2_report = memory_v2_report
        self._last_previous_verrou_context = previous_verrou_context

        core_result: Dict[str, Any] = {}
        static_diagnostic: Dict[str, Any] = {}
        presenter_error: Optional[str] = None
        try:
            try:
                from agents.EnnoDiagnostic.diagnostic_static_presenter import (
                    build_final_static_diagnostic,
                    generate_structured_diagnostic_core,
                )
            except Exception:
                from diagnostic_static_presenter import (
                    build_final_static_diagnostic,
                    generate_structured_diagnostic_core,
                )

            core_result = generate_structured_diagnostic_core(
                llm=self.llm,
                sections=sections,
                frascati_summary=frascati_summary,
                style_memory_report=style_memory_report,
                ai_detection_report=ai_detection_report,
                memory_v2_report=memory_v2_report,
            )
        except Exception as exc:
            presenter_error = str(exc)
            print(f"[EnnoDiagnostic][V182_PRESENTER][WARN] {exc}")

        # La composition des verrous vient du NLP. Le LLM ne fait que reformuler.
        llm_reformulated_verrous = self.build_llm_reformulated_verrous(
            content="",
            sections=sections,
            frascati_summary=frascati_summary,
        )
        llm_reformulated_verrous = self._enrich_verrous_with_frascati(
            llm_reformulated_verrous,
            frascati_summary,
        )
        cir_memory_report = self.load_cir_memory_report(
            current_verrous=llm_reformulated_verrous,
        )
        self._last_cir_memory_report = cir_memory_report
        memory_v2_usage_report = self.build_memory_v2_usage_report(
            memory_v2_report=memory_v2_report,
            style_memory_report=style_memory_report,
            sections=sections,
        )

        content = ""
        prompt = clean_text(core_result.get("prompt"))
        if core_result:
            try:
                static_diagnostic = build_final_static_diagnostic(
                    core_result=core_result,
                    sections=sections,
                    frascati_summary=frascati_summary,
                    frascati_justification_result=(core_result.get("section_payloads_by_key") or {}).get("justification_frascati"),
                    memory_v2_usage_report=memory_v2_usage_report,
                    llm_reformulated_verrous=llm_reformulated_verrous,
                )
                values = static_diagnostic.get("sections_by_key") or {}
                n1_body = self._cir_previous_comparison_block(
                    cir_memory_report,
                    max_items=10,
                    max_chars=3200,
                )
                values["continuite_n1"] = n1_body
                static_diagnostic["sections_by_key"] = values
                titles = static_diagnostic.get("sections_by_title") or {}
                titles[CIR_PREVIOUS_SECTION_TITLE] = n1_body
                static_diagnostic["sections_by_title"] = titles

                ordered = [
                    ("Analyse Frascati", values.get("lecture_frascati")),
                    ("Justification Frascati du score", values.get("justification_frascati")),
                    (MEMORY_V2_SECTION_TITLE, values.get("memoire_v2")),
                    (CIR_PREVIOUS_SECTION_TITLE, values.get("continuite_n1")),
                    ("Synthèse stratégique du projet", values.get("synthese_strategique")),
                    ("Objectif global reformulé", values.get("objectif_global")),
                    (SIGNAL_SECTION_TITLE, values.get("verrous_rnd")),
                    ("Démarche expérimentale détectée", values.get("demarche_detectee")),
                    ("Résultats et métriques disponibles", values.get("resultats_metriques")),
                    ("Paramètres et contraintes techniques", values.get("parametres_contraintes")),
                ]
                content = "\n\n".join(
                    f"## {title}\n{clean_text(body)}"
                    for title, body in ordered
                    if clean_text(body)
                )
            except Exception as exc:
                presenter_error = str(exc)
                print(f"[EnnoDiagnostic][V182_STATIC][WARN] {exc}")

        if not content:
            content, prompt = self._generate_fast_markdown_fallback(
                sections=sections,
                frascati_summary=frascati_summary,
                style_memory_report=style_memory_report,
                ai_detection_report=ai_detection_report,
                cir_memory_report=cir_memory_report,
                memory_v2_report=memory_v2_report,
            )
            content = replace_or_insert_markdown_section(
                content,
                MEMORY_V2_SECTION_TITLE,
                self.render_memory_v2_usage_section(memory_v2_usage_report),
                after_title="Justification Frascati du score",
            )
            content = replace_or_insert_markdown_section(
                content,
                CIR_PREVIOUS_SECTION_TITLE,
                self.render_cir_previous_comparison_section(cir_memory_report),
                after_title=MEMORY_V2_SECTION_TITLE,
            )
            content = replace_or_insert_markdown_section(
                content,
                SIGNAL_SECTION_TITLE,
                self._render_reformulated_verrous_section(llm_reformulated_verrous),
                after_title="Objectif global reformulé",
            )

        content = normalize_report_vocabulary(content)
        diagnostic_sections = build_diagnostic_sections(content)
        static_titles = static_diagnostic.get("sections_by_title") if isinstance(static_diagnostic, dict) else {}
        if isinstance(static_titles, dict):
            for title, body in static_titles.items():
                if clean_text(body):
                    diagnostic_sections.setdefault(str(title), clean_text(body))

        key_map = {
            "analyse_frascati": diagnostic_sections.get("Analyse Frascati") or diagnostic_sections.get("Lecture Frascati du dossier"),
            "lecture_frascati": diagnostic_sections.get("Lecture Frascati du dossier") or diagnostic_sections.get("Analyse Frascati"),
            "justification_frascati": diagnostic_sections.get("Justification Frascati du score"),
            "memoire_v2": diagnostic_sections.get(MEMORY_V2_SECTION_TITLE),
            "continuite_n1": diagnostic_sections.get(CIR_PREVIOUS_SECTION_TITLE),
            "synthese_strategique": diagnostic_sections.get("Synthèse stratégique du projet"),
            "objectif_global": diagnostic_sections.get("Objectif global reformulé"),
            "verrous_rnd": diagnostic_sections.get(SIGNAL_SECTION_TITLE),
            "demarche_detectee": diagnostic_sections.get("Démarche expérimentale détectée"),
            "resultats_metriques": diagnostic_sections.get("Résultats et métriques disponibles"),
            "parametres_contraintes": diagnostic_sections.get("Paramètres et contraintes techniques"),
            "points_validation": diagnostic_sections.get("Points à valider par le consultant"),
        }
        diagnostic_sections_by_key = {
            key: clean_text(value) for key, value in key_map.items() if clean_text(value)
        }

        elapsed = round(time.time() - started_at, 2)
        report: Dict[str, Any] = {
            "ok": True,
            "status": "completed",
            "version": "ennodiagnostic_v189_nlp_group_passthrough_cir_reformulation",
            "mode": core_result.get("status") or "fast_prompt_fallback",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "organisme": self.organisme,
            "project": self.project,
            "year": self.year,
            "diagnostic": {"status": "completed", "content": content},
            "content": content,
            "report_markdown": content,
            "diagnostic_sections": diagnostic_sections,
            "diagnostic_sections_by_key": diagnostic_sections_by_key,
            "report_sections": diagnostic_sections_by_key,
            "diagnostic_cards": static_diagnostic.get("cards") or [],
            "static_diagnostic": static_diagnostic,
            "llm_reformulated_verrous": llm_reformulated_verrous,
            "consultant_verrous_cir": llm_reformulated_verrous,
            "verrou_synthesis_report": getattr(self, "_last_verrou_synthesis_report", {}),
            "frascati_summary": frascati_summary,
            "demarche_legibility": frascati_summary.get("demarche_legibility") or {},
            "ai_detection_report": ai_detection_report,
            "style_memory_report": style_memory_report,
            "memory_v2_report": memory_v2_report,
            "memory_v2_usage_report": memory_v2_usage_report,
            "previous_verrou_context_report": previous_verrou_context,
            "cir_memory_report": cir_memory_report,
            "chroma_sections": sections,
            "context_engineering": core_result,
            "inputs_status": {
                "rag_sections_loaded": True,
                "style_memory_available": bool(style_memory_report.get("ok")),
                "memory_v2_available": bool(memory_v2_report.get("ok")),
                "previous_verrou_context_available": bool(previous_verrou_context.get("available")),
                "previous_cir_available": bool(cir_memory_report.get("has_previous_cir")),
            },
            "telemetry": {
                "elapsed_seconds": elapsed,
                "presenter_error": presenter_error,
                "prompt_chars": len(prompt),
                "main_verrous_count": len(llm_reformulated_verrous),
                "previous_verrou_examples_count": int(previous_verrou_context.get("examples_count") or 0),
                "previous_verrou_context_in_prompt": bool(previous_verrou_context.get("available")),
                "previous_verrou_context_factual_use_allowed": False,
                "demarche_llm_review_recommended": bool(
                    (frascati_summary.get("demarche_legibility") or {}).get("llm_review_recommended")
                ),
                "demarche_llm_review_policy": "reuse_existing_demarche_section_call_no_extra_call",
            },
            "output_path": str(self.report_path),
        }

        # Vérification de sérialisabilité avant écriture et retour API.
        report = json.loads(json.dumps(report, ensure_ascii=False, default=str))
        if save:
            save_json(self.report_path, report)
            save_json(self.diagnostic_dir / "diagnostic_ennodiagnostic.json", report)
            save_json(self.diagnostic_dir / "llm_reformulated_verrous.json", {
                "version": report["version"],
                "items": llm_reformulated_verrous,
            })
        print(
            f"✅ EnnoDiagnostic V186 terminé | sections={len(diagnostic_sections_by_key)} "
            f"| verrous={len(llm_reformulated_verrous)} | N-1={bool(cir_memory_report.get('has_previous_cir'))} "
            f"| temps={elapsed}s",
            flush=True,
        )
        return report
