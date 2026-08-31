# -*- coding: utf-8 -*-
# ENNODIAG_FULL_FIX_V5_2_20260829 — strict source authority, provenance, missing-lock recovery
from __future__ import annotations

# ENNODIAG_FINAL_FIX_V4_20260829 — ennodiagnostic_agent

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


def _resolve_ennosmart_year_root(
    organisme: str,
    project: str,
    year: str,
    subproject: str = "",
) -> Path:
    """Résout génériquement le dossier annuel d'un projet EnnoSmart.

    La résolution recherche d'abord les répertoires existants sans dépendre de
    la casse, des espaces, des tirets ou des underscores. Si aucun chemin
    n'existe encore, elle construit un chemin canonique à partir des valeurs
    reçues, sans règle propre à un organisme ou à un projet.
    """
    root = Path(
        os.getenv("ENNOSMART_ROOT")
        or Path(__file__).resolve().parents[2]
    )
    storage = root / "storage" / "organismes"
    year_value = str(year)

    exact_project = (
        storage
        / str(organisme).strip()
        / "projects"
        / _clean_part_for_path(project)
    )
    exact = (
        exact_project
        / "subprojects"
        / _clean_part_for_path(subproject)
        / "years"
        / year_value
        if str(subproject or "").strip()
        else exact_project / "years" / year_value
    )
    if exact.exists():
        return exact

    org_key = _path_match_key(organisme)
    project_key = _path_match_key(project)
    subproject_key = _path_match_key(subproject)
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
                scope_dir = project_dir
                if subproject_key:
                    subprojects_dir = project_dir / "subprojects"
                    if not subprojects_dir.exists():
                        continue
                    scope_dir = next(
                        (
                            value
                            for value in subprojects_dir.iterdir()
                            if value.is_dir()
                            and _path_match_key(value.name) == subproject_key
                        ),
                        None,
                    )
                    if scope_dir is None:
                        continue
                candidate = scope_dir / "years" / year_value
                if candidate.exists():
                    return candidate

    org_default = str(organisme or "unknown_organisme").strip() or "unknown_organisme"
    project_default = _clean_part_for_path(project) or "unknown_project"
    project_scope = storage / org_default / "projects" / project_default
    if str(subproject or "").strip():
        project_scope = (
            project_scope
            / "subprojects"
            / (_clean_part_for_path(subproject) or "unknown_subproject")
        )
    return project_scope / "years" / year_value


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


def clean_text(text: Any, max_chars: int | None = None) -> str:
    """Normalise un texte et le tronque optionnellement.

    La signature accepte ``max_chars`` car plusieurs chemins EnnoDiagnostic
    utilisent cette fonction comme helper de compaction. L'argument reste
    facultatif afin de préserver tous les appels historiques à un seul argument.
    """
    value = str(text or "").strip()
    if max_chars is not None:
        try:
            limit = int(max_chars)
        except Exception:
            limit = 0
        if limit > 0 and len(value) > limit:
            value = value[:limit].rstrip() + "…"
    return value


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
        "analysis_unit",
        "direct_rnd_rule",
        "project_status",
        "operation_status",
        "operations_count",
        "operation_count",
        "rnd_core_defendable_operations_count",
        "rnd_core_partial_operations_count",
        "classical_engineering_operations_count",
        "insufficient_evidence_operations_count",
        "all_operations_classical_engineering",
        "label",
        "readability_score",
        "readability_score_semantics",
        "documentary_confidence",
        "activities_count",
        "direct_rnd_activities_count",
        "necessary_rnd_support_activities_count",
        "classical_engineering_activities_count",
        "insufficient_evidence_activities_count",
        "method_steps_count",
        "research_justified_steps_count",
        "routine_engineering_steps_count",
        "unexplained_steps_count",
        "redundant_steps_count",
        "groups_with_possible_direct_final_solution_shortcut",
        "direct_final_solution_risk",
        "eligibility_impact",
        "risk_adjustment",
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


_FRASCATI_LABELS = {
    "novelty": "Nouveauté",
    "creativity": "Créativité",
    "uncertainty": "Incertitude scientifique ou technique",
    "systematicity": "Démarche systématique",
    "transferability": "Transférabilité ou reproductibilité",
}

_FRASCATI_STATUS_VALUE = {
    "documented": 1.0,
    "partial": 0.5,
    "missing": 0.0,
    "contradictory": 0.0,
}

_FRASCATI_PROOF_PATTERNS = {
    "novelty": re.compile(
        r"etat de l.?art|state of the art|existing (?:solution|method)|solution existante|"
        r"insuffisan|limite de l.?existant|knowledge gap|not covered",
        re.I,
    ),
    "creativity": re.compile(
        r"hypoth[eè]se|hypothesis|approche originale|novel approach|prototype|"
        r"exploration|variante|conception|architecture nouvelle",
        re.I,
    ),
    "uncertainty": re.compile(
        r"incert|uncertain|impossible [aà] pr[eé]dire|unpredict|verrou|non ma[iî]tris|"
        r"reste [aà] comprendre|unknown|limitation",
        re.I,
    ),
    "systematicity": re.compile(
        r"protocole|protocol|campagne d.?essais|experiment|test|compar|benchmark|"
        r"it[eé]ration|mesur|simulation|param[eè]tr",
        re.I,
    ),
    "transferability": re.compile(
        r"reproduct|reus|r[eé]utilis|g[eé]n[eé]ralis|generaliz|capitalis|"
        r"transf[eé]r|transposable|replicab|connaissances acquises",
        re.I,
    ),
}

_HYPOTHESIS_EVIDENCE_PATTERN = re.compile(
    r"\b(?:hypoth[eè]se|hypothesis|we propose|nous proposons|propos(?:er|ons)|"
    r"new approach|nouvelle approche|combine|combinaison|union|compl[eé]mentair|"
    r"complementary|paradigm|because|parce que|rationale|suppos(?:er|ons)|"
    r"could|might|pourrait|expected|attendu|overlap|recouvr|influence|impact)\b",
    re.I,
)

_HYPOTHESIS_LINK_PATTERN = re.compile(
    r"\b(?:because|parce que|therefore|thus|donc|afin de|pour|complementary|"
    r"compl[eé]mentair|different|diff[eé]rent|union|overlap|recouvr|combine|"
    r"combinaison|improv|am[eé]lior|impact|influence)\b",
    re.I,
)

_RESULT_SECTION_PATTERN = re.compile(
    r"(?:^|[/\\>:\-])\s*(?:results?|r[eé]sultats?|findings?|conclusions?)\s*\]?$",
    re.I,
)

_RESULT_SIGNAL_PATTERN = re.compile(
    r"\b(?:result|r[eé]sultat|measur|mesur|metric|m[eé]trique|precision|pr[eé]cision|"
    r"accuracy|performance|gain|gap|[eé]cart|difference|diff[eé]rence|increase|"
    r"augmentation|decrease|diminution|improv|am[eé]lior|score|rate|taux)\b",
    re.I,
)

_RESULT_OBSERVATION_PATTERN = re.compile(
    r"\b(?:obtained|achieved|reached|outperform|performed better|performed worse|observed|"
    r"shows?|showed|indicates?|demonstrates?|concludes?|found|yielded|"
    r"obtenu|atteint|surpass|plus performant|moins performant|observ[eé]|constat[eé]|"
    r"montr(?:e|ent)|indiqu(?:e|ent)|d[eé]montr(?:e|ent)|conclu|s’av[eè]re|"
    r"pr[eé]cision (?:est|de|atteint)|accuracy (?:is|of|reached))\b",
    re.I,
)

_QUANTITATIVE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])[-+]?\d+(?:[.,]\d+)?\s*(?:%|ms|s|m|cm|mm|km|"
    r"db|hz|khz|mhz|ghz|bar|pa|kpa|mpa|w|kw|mw|v|a|kg|g|°c)?(?![A-Za-z0-9])",
    re.I,
)

_METADATA_SECTION_PATTERN = re.compile(
    r"^(?:title|titre|authors?|auteurs?|affiliations?|corresponding author|"
    r"adresse|address|citation|to cite this version|bibliograph(?:y|ie)|references?|"
    r"doi|copyright|acknowledg(?:e)?ments?|remerciements?|table of contents|"
    r"table des mati[eè]res|list of figures|table des illustrations|list of tables|"
    r"liste des tableaux)\s*:?$",
    re.I,
)

_METADATA_TEXT_PATTERN = re.compile(
    r"\b(?:corresponding author|author affiliations?|to cite this version|"
    r"all rights reserved|copyright|https?://doi\.org|doi\s*:|"
    r"university|universit[eé]|laboratory|laboratoire|postal|cedex|"
    r"street|avenue|adresse|address|hal id)\b|"
    r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}",
    re.I,
)

_STATE_OF_ART_PATTERN = re.compile(
    r"\b(?:state of the art|related work|literature review|background|bibliograph|"
    r"[eé]tat de l.?art|travaux (?:ant[eé]rieurs|connexes)|litt[eé]rature|existing (?:work|method|solution)|"
    r"solution existante|known (?:method|approach)|m[eé]thode connue)\b",
    re.I,
)

_PROJECT_ACTION_PATTERN = re.compile(
    r"\b(?:we (?:use|used|develop|developed|implement|implemented|train|trained|evaluate|evaluated|"
    r"measure|measured|compare|compared|conduct|conducted|perform|performed|propose|proposed)|"
    r"nous (?:utilisons|avons|d[eé]veloppons|d[eé]velopp[eé]|mettons|avons mis|entra[iî]nons|"
    r"avons entra[iî]n[eé]|[eé]valuons|avons [eé]valu[eé]|mesurons|avons mesur[eé]|comparons|"
    r"avons compar[eé]|proposons)|l.?[eé]quipe (?:a|met|utilise|d[eé]veloppe|teste|mesure|compare)|"
    r"dans (?:ce|le) projet|au cours (?:de|du) projet|our (?:method|approach|experiment|study|work))\b",
    re.I,
)

_EXPERIMENT_PATTERN = re.compile(
    r"\b(?:experiment|experimental|exp[eé]rien|protocole|protocol|test(?:ed|ing)?|essai|"
    r"training|train(?:ed|ing)?|entra[iî]n|dataset|data set|jeu de donn[eé]es|sample|[eé]chantillon|"
    r"parameter|param[eè]tr|configuration|condition|benchmark|comparison|comparaison|compare|"
    r"mesur|measure|evaluation|[eé]valuation|validation|simulation|simulator|simulateur|"
    r"cross-validation|validation crois[eé]e|ablation|baseline|t[eé]moin)\b",
    re.I,
)

_LEARNING_PATTERN = re.compile(
    r"\b(?:we (?:observe|observed|conclude|concluded|show|showed|found|learned)|"
    r"nous (?:observons|avons observ[eé]|concluons|avons conclu|constatons|avons constat[eé])|"
    r"the results? (?:show|indicate|demonstrate|suggest)|les r[eé]sultats? (?:montrent|indiquent|"
    r"d[eé]montrent|sugg[eè]rent)|observation|conclusion|finding|enseignement|connaissance acquise|"
    r"permet de conclure|supports? the hypothesis|soutient l.?hypoth[eè]se)\b",
    re.I,
)

_LIMITATION_PATTERN = re.compile(
    r"\b(?:limit(?:ation|ed|s)?|shortcoming|drawback|fails? to|cannot|unable|insufficient|"
    r"knowledge gap|open problem|limite|lacune|insuffisan|ne permet pas|impossible|reste [aà]|"
    r"non r[eé]solu|probl[eè]me ouvert)\b",
    re.I,
)

_CONTRIBUTION_PATTERN = re.compile(
    r"\b(?:contribution|we propose|nous proposons|novel|new (?:method|approach|design|combination)|"
    r"nouve(?:au|lle) (?:m[eé]thode|approche|conception|combinaison)|original|architecture|"
    r"combine|combinaison|hybrid|hybride|prototype|conception)\b",
    re.I,
)

_REPRODUCIBILITY_PATTERN = re.compile(
    r"\b(?:reproduc|replicab|reusab|re-utilis|r[eé]utilis|transferab|transf[eé]r|"
    r"generaliz|g[eé]n[eé]ralis|different conditions|diff[eé]rentes conditions|"
    r"parameter settings|param[eè]tres document[eé]s|procedure|proc[eé]dure|workflow|"
    r"knowledge gained|connaissances? acquises?|capitalis)\b",
    re.I,
)


# Signaux génériques de qualité documentaire. Ils ne contiennent aucun nom de
# projet, de technologie ou de dataset. Leur rôle est d'éviter qu'une citation
# bibliographique ou une phrase de contexte soit promue comme expérience du projet.
_REFERENCE_CITATION_PATTERN = re.compile(
    r"(?:\bet\s+al\.\b|\bdoi\s*:\s*|https?://doi\.org|"
    r"\b(?:journal|proceedings|conference|transactions|letters)\b|"
    r"\bvol\.?\s*\d+\b|\bpp?\.?\s*\d+(?:\s*[-–]\s*\d+)?\b|"
    r"[“\"]{1}[^”\"]{12,220}[”\"]{1}|"
    r"\b[A-ZÀ-ÖØ-Ý][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+\s*,\s*(?:[A-Z]\.?\s*){1,3}(?:,|\band\b|\bet\b))",
    re.I,
)

_REFERENCE_SECTION_FRAGMENT_PATTERN = re.compile(
    r"(?:\bet\s+al\.\b|\bdoi\b|\bvol\.?\b|\bpp?\.?\b|"
    r"[“\"][^”\"]{8,180}$|"
    r"^[A-ZÀ-ÖØ-Ý][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+(?:\s+[A-ZÀ-ÖØ-Ý][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+){0,3}\s*,\s*"
    r"(?:and|et|&|[A-Z]\.)\s*)",
    re.I,
)

_EXPLICIT_HYPOTHESIS_PATTERN = re.compile(
    r"\b(?:our (?:main |working )?(?:assumption|hypothesis)|main assumption|"
    r"we hypothesi[sz]e|we assume|we propose|we investigate whether|we study whether|"
    r"nous (?:faisons l['’]hypoth[eè]se|supposons|proposons|cherchons [aà] d[eé]terminer si|"
    r"[eé]tudions si)|l['’]hypoth[eè]se (?:est|selon laquelle)|hypoth[eè]se centrale|"
    r"proposition centrale|new approach|nouvelle approche)\b",
    re.I,
)

_DESIGN_PROPOSAL_PATTERN = re.compile(
    r"\b(?:new approach|novel approach|nouvelle approche|approche originale|"
    r"we propose (?:a|an) (?:new|novel)|nous proposons (?:une|un) (?:nouvelle?|originale?)|"
    r"combine|combinaison|complementary|compl[eé]mentair|hybrid|hybride|new design|nouvelle conception)\b",
    re.I,
)

_REJECTED_OR_COUNTERFACTUAL_PATTERN = re.compile(
    r"\b(?:rather than|instead of|we do not|we don['’]?t|not intended to|not aim(?:ed)? to|"
    r"avoid(?:ing)?|without trying to|au lieu de|plut[oô]t que|nous ne\s+\w+(?:\s+\w+){0,5}\s+pas|"
    r"sans chercher [aà]|sans tenter de)\b",
    re.I,
)

_COMPARATIVE_RESULT_PATTERN = re.compile(
    r"\b(?:compared (?:with|to)|comparison with|versus|vs\.?|whereas|while|against|"
    r"contre|par rapport [aà]|tandis que|compar[eé] [aà]|gap of|[eé]cart de|difference of|"
    r"diff[eé]rence de|increas(?:e|ed|es|ing)(?: this result)? by|augmentation de|gain de|improvement of|"
    r"am[eé]lioration de|higher than|lower than|sup[eé]rieur [aà]|inf[eé]rieur [aà])\b",
    re.I,
)

_PAIRWISE_COMPARISON_PATTERN = re.compile(
    r"\b(?:compared (?:with|to)|versus|vs\.?|against|contre|par rapport [aà]|tandis que|whereas|"
    r"gap (?:of )?.*? between|[eé]cart (?:de )?.*? entre|difference .*? between|diff[eé]rence .*? entre|"
    r"higher than|lower than|sup[eé]rieur [aà]|inf[eé]rieur [aà])\b",
    re.I,
)

_OBSERVED_GAIN_PATTERN = re.compile(
    r"\b(?:increas(?:e|ed|es|ing)(?: this result)? by|improv(?:e|ed|es|ement) (?:by|of)|"
    r"augmentation de|gain de|am[eé]lioration de)\b",
    re.I,
)

_CONTROLLED_PROTOCOL_PATTERN = re.compile(
    r"\b(?:using either|two groups?|deux groupes?|same (?:algorithm|model|models|dataset|conditions|parameters)|"
    r"m[eê]me(?:s)? (?:algorithme|mod[eè]le|mod[eè]les|jeu de donn[eé]es|conditions|param[eè]tres)|"
    r"performances? .*? measured on|performances? .*? mesur[eé]es? sur|"
    r"compared under the same|compar[eé]s? dans les m[eê]mes conditions|control(?:led)? comparison|comparaison contr[oô]l[eé]e)\b",
    re.I,
)

_GLOBAL_RESULT_PATTERN = re.compile(
    r"\b(?:overall|global|average|mean|moyenn?e|total|across (?:all|the)|"
    r"sur l['’]ensemble|toutes les (?:classes|cibles|configurations))\b",
    re.I,
)

_PER_ITEM_RESULT_PATTERN = re.compile(
    r"\b(?:confusion matrix|matrice de confusion|for (?:the|class)|pour (?:la|le|les) (?:classe|cible)|"
    r"class[- ]wise|per[- ]class|par classe|par cible|confused with|confondu(?:e)? avec)\b",
    re.I,
)

_HEADROOM_OR_BOUND_PATTERN = re.compile(
    r"\b(?:remains? possible|remaining improvement|reste .*?(?:possible|avant d['’]atteindre)|"
    r"upper bound|maximum possible|theoretical maximum|marge d['’]am[eé]lioration|"
    r"pour atteindre\s+100\s*%|to reach\s+100\s*%)\b",
    re.I,
)

_STRONG_PROTOCOL_PATTERN = re.compile(
    r"\b(?:we (?:generated|produced|trained|evaluated|compared|measured|used|considered)|"
    r"nous (?:avons g[eé]n[eé]r[eé]|avons produit|avons entra[iî]n[eé]|avons [eé]valu[eé]|"
    r"avons compar[eé]|avons mesur[eé]|avons utilis[eé]|consid[eé]rons)|"
    r"same (?:dataset|model|models|conditions|parameters)|m[eê]mes? (?:jeu de donn[eé]es|mod[eè]les|conditions|param[eè]tres)|"
    r"training set|test set|jeu d['’]entra[iî]nement|jeu de test|"
    r"depression angles?|angles? de d[eé]pression|configurations? d['’]acquisition|"
    r"ablation study|[eé]tude d['’]ablation)\b",
    re.I,
)

_THIRD_PARTY_WORK_PATTERN = re.compile(
    r"\b(?:the authors?|their (?:classifier|method|model|approach|results?)|previous work|prior work|"
    r"a previous study|another study|reported by|according to|les auteurs?|leur (?:classifieur|m[eé]thode|mod[eè]le|approche)|"
    r"travaux ant[eé]rieurs|une [eé]tude ant[eé]rieure|rapport[eé] par)\b|\[[0-9]{1,3}\]",
    re.I,
)

_RESEARCH_OBJECTIVE_ONLY_PATTERN = re.compile(
    r"\b(?:we propose to study|we aim to study|we investigate (?:the|whether)|we study the impact|"
    r"nous proposons d['’][eé]tudier|nous visons [aà] [eé]tudier|nous [eé]tudions l['’]impact|"
    r"pour r[eé]pondre [aà] ces questions|to answer these questions)\b",
    re.I,
)

_FUNCTION_PATTERNS = {
    "uncertainty": _FRASCATI_PROOF_PATTERNS["uncertainty"],
    "hypothesis": _HYPOTHESIS_EVIDENCE_PATTERN,
    "experiment": _EXPERIMENT_PATTERN,
    "result": _RESULT_SIGNAL_PATTERN,
    "learning": _LEARNING_PATTERN,
}

_ROLE_HINTS = {
    "uncertainty": {"verrou", "objectif", "parametre"},
    "hypothesis": {"objectif", "contribution", "parametre", "verrou", "methode", "resultat"},
    "experiment": {"methode", "parametre", "contribution"},
    "result": {"resultat"},
    "learning": {"resultat", "contribution"},
    "novelty": {"etat_art", "verrou", "objectif"},
    "creativity": {"contribution", "objectif", "methode"},
    "systematicity": {"methode", "parametre", "resultat"},
    "transferability": {"methode", "parametre", "contribution", "resultat"},
}


def _passage_evidence_id(passage: Dict[str, Any]) -> str:
    metadata = passage.get("metadata") if isinstance(passage.get("metadata"), dict) else {}
    return clean_text(
        passage.get("passage_id") or passage.get("id")
        or metadata.get("passage_id") or metadata.get("original_passage_id")
        or metadata.get("rag_chunk_id")
    )


def _passage_position(passage: Dict[str, Any], default: int = 0) -> int:
    metadata = passage.get("metadata") if isinstance(passage.get("metadata"), dict) else {}
    for key in ("sentence_start", "paragraph_index", "char_start"):
        try:
            value = passage.get(key) if passage.get(key) not in (None, "") else metadata.get(key)
            if value not in (None, ""):
                return int(value)
        except Exception:
            continue
    return default


def _passage_has_position(passage: Dict[str, Any]) -> bool:
    metadata = passage.get("metadata") if isinstance(passage.get("metadata"), dict) else {}
    return any(
        passage.get(key) not in (None, "") or metadata.get(key) not in (None, "")
        for key in ("sentence_start", "paragraph_index", "char_start")
    )


def _passage_document_key(passage: Dict[str, Any]) -> str:
    metadata = passage.get("metadata") if isinstance(passage.get("metadata"), dict) else {}
    document_id = clean_text(
        passage.get("document_id")
        or passage.get("source_document_id")
        or passage.get("doc_id")
        or metadata.get("document_id")
        or metadata.get("source_document_id")
    )
    if document_id:
        return f"id:{document_id}"
    return _path_match_key(
        passage.get("document")
        or passage.get("document_name")
        or passage.get("source_path")
        or metadata.get("document")
        or metadata.get("document_name")
        or metadata.get("source_path")
    )


def _passage_section_text(passage: Dict[str, Any]) -> str:
    metadata = passage.get("metadata") if isinstance(passage.get("metadata"), dict) else {}
    return " / ".join(
        value for value in (
            clean_text(passage.get("section_path") or metadata.get("section_path")),
            clean_text(passage.get("section_title") or metadata.get("section_title")),
        )
        if value
    )


def _passage_full_text(passage: Dict[str, Any]) -> str:
    fragments: List[Tuple[str, str]] = []
    for key in ("analysis_text", "context_before", "text", "context_after"):
        value = re.sub(r"\s+", " ", clean_text(passage.get(key))).strip()
        signature = _diag_norm(value)
        if not value or not signature:
            continue
        if any(signature in existing_signature for _, existing_signature in fragments):
            continue
        fragments = [
            (existing_value, existing_signature)
            for existing_value, existing_signature in fragments
            if existing_signature not in signature
        ]
        fragments.append((value, signature))
    return " ".join(value for value, _ in fragments)


def _is_result_metadata(passage: Dict[str, Any]) -> bool:
    """Rejette les vraies métadonnées sans jeter un contenu scientifique mal sectionné.

    Certains parseurs PDF conservent un ancien titre de section comme « To cite this
    version » alors que le chunk contient ensuite l'abstract ou les contributions.
    La décision regarde donc d'abord le contenu réel du passage.
    """
    metadata = passage.get("metadata") if isinstance(passage.get("metadata"), dict) else {}
    origin = clean_text(passage.get("content_origin") or metadata.get("content_origin")).lower()
    text = clean_text(passage.get("text"))
    section = _passage_section_text(passage).strip()
    primary = " ".join((section, text))

    substantive = bool(
        len(text) >= 70
        and (
            _EXPLICIT_HYPOTHESIS_PATTERN.search(text)
            or _CONTRIBUTION_PATTERN.search(text)
            or _LIMITATION_PATTERN.search(text)
            or _EXPERIMENT_PATTERN.search(text)
            or _RESULT_OBSERVATION_PATTERN.search(text)
            or _PROJECT_ACTION_PATTERN.search(text)
        )
    )
    if origin == "metadata" and not substantive:
        return True

    section_parts = [part.strip() for part in re.split(r"[/\\>]", section) if part.strip()]
    if any(_METADATA_SECTION_PATTERN.fullmatch(part) for part in section_parts) and not substantive:
        return True

    metadata_like = bool(_METADATA_TEXT_PATTERN.search(primary))
    result_like = bool(_RESULT_SIGNAL_PATTERN.search(text) and _QUANTITATIVE_PATTERN.search(text))
    return metadata_like and not substantive and not result_like



def _is_reference_like_passage(passage: Dict[str, Any]) -> bool:
    """Détecte les fragments bibliographiques/citations sans les confondre avec l'état de l'art.

    Une référence peut servir à la nouveauté/état de l'art, mais ne doit pas devenir
    une preuve d'expérience ou de résultat du projet sauf si le même passage décrit
    explicitement une action menée par l'équipe.
    """
    if _is_result_metadata(passage):
        return True
    section = _passage_section_text(passage)
    text = clean_text(passage.get("text"))
    full = _passage_full_text(passage)
    if _REFERENCE_SECTION_FRAGMENT_PATTERN.search(section):
        return True
    citation_hits = len(_REFERENCE_CITATION_PATTERN.findall(f"{section} {text}"))
    if citation_hits >= 2 and not _PROJECT_ACTION_PATTERN.search(full):
        return True
    return False


def _hypothesis_quality(passage: Dict[str, Any]) -> Dict[str, Any]:
    joined = f"{_passage_section_text(passage)} {_passage_full_text(passage)}"
    explicit = bool(_EXPLICIT_HYPOTHESIS_PATTERN.search(joined))
    proposal = bool(_CONTRIBUTION_PATTERN.search(joined))
    design_proposal = bool(_DESIGN_PROPOSAL_PATTERN.search(joined))
    research_objective_only = bool(_RESEARCH_OBJECTIVE_ONLY_PATTERN.search(joined)) and not design_proposal
    linked_reason = bool(_HYPOTHESIS_LINK_PATTERN.search(joined))
    rejected = bool(_REJECTED_OR_COUNTERFACTUAL_PATTERN.search(joined))
    return {
        "explicit": explicit,
        "proposal": proposal,
        "design_proposal": design_proposal,
        "research_objective_only": research_objective_only,
        "linked_reason": linked_reason,
        "rejected_or_counterfactual": rejected,
    }


def _result_scope(passage: Dict[str, Any], fragment: Optional[str] = None) -> str:
    """Classe un résultat sans dépendre du domaine métier."""
    text = clean_text(fragment) if fragment is not None else _passage_full_text(passage)
    section = _passage_section_text(passage)
    joined = f"{section} {text}"
    has_values = bool(_quantitative_values(text))
    comparative = bool(_COMPARATIVE_RESULT_PATTERN.search(joined))
    pairwise = bool(_PAIRWISE_COMPARISON_PATTERN.search(joined))
    observed_gain = bool(_OBSERVED_GAIN_PATTERN.search(joined))
    global_signal = bool(_GLOBAL_RESULT_PATTERN.search(joined))
    per_item = bool(_PER_ITEM_RESULT_PATTERN.search(joined))
    headroom = bool(_HEADROOM_OR_BOUND_PATTERN.search(joined))
    observed = bool(_RESULT_OBSERVATION_PATTERN.search(joined))
    if pairwise and has_values and not per_item:
        return "global_comparison"
    if observed_gain and has_values and not per_item:
        return "observed_gain"
    if global_signal and has_values and observed:
        return "global_metric"
    if per_item and has_values:
        return "per_item_metric"
    if headroom and has_values:
        return "headroom_context"
    if has_values and observed:
        return "observed_metric"
    if observed:
        return "qualitative_observation"
    return "result_context"


def _fragment_windows(value: str) -> List[str]:
    """Crée de petits fragments pour éviter qu'un chunk mélange plusieurs métriques."""
    text = re.sub(r"\s+", " ", clean_text(value)).strip()
    if not text:
        return []
    sentences = [
        item.strip()
        for item in re.split(r"(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Ý0-9])", text)
        if item.strip()
    ]
    if len(sentences) <= 1:
        # Les extractions PDF perdent parfois la ponctuation ; garder aussi des
        # fenêtres raisonnables autour des séparateurs forts.
        clauses = [item.strip() for item in re.split(r"\s*[;•]\s*", text) if item.strip()]
        sentences = clauses if len(clauses) > 1 else [text]
    windows: List[str] = []
    for index, sentence in enumerate(sentences):
        windows.append(sentence)
        if index + 1 < len(sentences):
            windows.append(f"{sentence} {sentences[index + 1]}")
    # Le texte complet reste candidat seulement si aucune vraie segmentation
    # n'a été possible ; sinon il mélangerait plusieurs métriques ou conclusions.
    if len(sentences) == 1 and len(text) <= 650:
        windows.append(text)
    seen = set()
    out = []
    for item in windows:
        sig = _diag_norm(item)
        if not sig or sig in seen:
            continue
        seen.add(sig)
        out.append(item)
    return out


def _distinctive_tokens(value: Any) -> set[str]:
    common = {
        "with", "that", "this", "from", "have", "their", "they", "were", "been",
        "pour", "avec", "dans", "cette", "nous", "sont", "plus", "entre", "ainsi",
        "result", "results", "resultat", "resultats", "approach", "approche",
    }
    return {
        token for token in _diag_norm(value).split()
        if len(token) >= 4 and token not in common
    }


def _nlp_passage_proof(passage: Any) -> Dict[str, Any]:
    item = passage if isinstance(passage, dict) else {}
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    raw_path = clean_text(item.get("source_path") or metadata.get("source_path"))
    document = clean_text(
        item.get("document") or item.get("document_name") or item.get("filename")
        or metadata.get("document") or metadata.get("document_name") or metadata.get("filename")
    )
    if not document and raw_path:
        document = Path(raw_path).name
    excerpt = _passage_full_text(item)
    passage_id = _passage_evidence_id(item)
    original_text = re.sub(r"\s+", " ", clean_text(item.get("text"))).strip()
    coordinates = item.get("highlight_coordinates")
    if coordinates is None:
        coordinates = (
            item.get("coordinates") or item.get("bbox") or item.get("bounding_box")
            or metadata.get("highlight_coordinates") or metadata.get("coordinates")
            or metadata.get("bbox") or metadata.get("bounding_box")
        )
    return {
        "evidence_id": passage_id,
        "passage_id": passage_id,
        "document_id": (
            item.get("document_id") or item.get("source_document_id") or item.get("doc_id")
            or metadata.get("document_id") or metadata.get("source_document_id")
        ),
        "document": document,
        "document_name": document,
        "source_path": raw_path,
        "page_number": item.get("page_number") or item.get("page") or metadata.get("page_number") or metadata.get("page"),
        "section_title": clean_text(item.get("section_title") or metadata.get("section_title")),
        "section_path": clean_text(item.get("section_path") or metadata.get("section_path")),
        "heading_path": clean_text(item.get("heading_path") or metadata.get("heading_path")),
        "source_zone": clean_text(item.get("source_zone") or metadata.get("source_zone")),
        "document_section_type": clean_text(item.get("document_section_type") or metadata.get("document_section_type")),
        "sentence_start": item.get("sentence_start") if item.get("sentence_start") is not None else metadata.get("sentence_start"),
        "paragraph_index": item.get("paragraph_index") if item.get("paragraph_index") is not None else metadata.get("paragraph_index"),
        "char_start": (
            item.get("char_start") if item.get("char_start") is not None
            else metadata.get("char_start") if metadata.get("char_start") is not None
            else item.get("sentence_start") if item.get("sentence_start") is not None
            else metadata.get("sentence_start")
        ),
        "char_end": item.get("char_end") if item.get("char_end") is not None else metadata.get("char_end"),
        "highlight_coordinates": coordinates,
        "role": clean_text(
            item.get("role") or item.get("semantic_role") or item.get("original_model_role")
            or metadata.get("role") or metadata.get("semantic_role")
        ),
        "semantic_role": clean_text(item.get("semantic_role") or metadata.get("semantic_role")),
        "original_role": clean_text(item.get("original_model_role")),
        "original_model_role": clean_text(item.get("original_model_role") or metadata.get("original_model_role")),
        "content_origin": clean_text(item.get("content_origin") or metadata.get("content_origin")),
        "source_type": clean_text(item.get("source_type") or metadata.get("source_type")),
        "source_kind": clean_text(item.get("source_kind") or metadata.get("source_kind")),
        "document_type": clean_text(item.get("document_type") or metadata.get("document_type")),
        "document_category": clean_text(item.get("document_category") or metadata.get("document_category")),
        "declared_corpus": clean_text(item.get("declared_corpus") or metadata.get("declared_corpus")),
        "diagnostic_corpus_selected": bool(item.get("diagnostic_corpus_selected") or metadata.get("diagnostic_corpus_selected")),
        "current_project_evidence": bool(item.get("current_project_evidence") or metadata.get("current_project_evidence")),
        "declared_raw_document": bool(item.get("declared_raw_document") or metadata.get("declared_raw_document")),
        "temporal_scope": clean_text(item.get("temporal_scope") or metadata.get("temporal_scope")),
        "evidence_origin": clean_text(item.get("evidence_origin") or metadata.get("evidence_origin")),
        "actor_scope": clean_text(item.get("actor_scope") or metadata.get("actor_scope")),
        "execution_status": clean_text(item.get("execution_status") or metadata.get("execution_status")),
        "reference_like": bool(item.get("reference_like") or metadata.get("reference_like")),
        "is_state_of_art": bool(item.get("is_state_of_art") or metadata.get("is_state_of_art")),
        "is_external_literature": bool(item.get("is_external_literature") or metadata.get("is_external_literature")),
        "literature_only": bool(item.get("literature_only") or metadata.get("literature_only")),
        "analysis_text_used": bool(item.get("analysis_text")),
        "context_before_used": bool(item.get("context_before")),
        "context_after_used": bool(item.get("context_after")),
        "source_text_original": truncate(original_text or excerpt, 1200),
        "excerpt": truncate(excerpt, 700),
    }


def _dedupe_nlp_passages(passages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    output: List[Dict[str, Any]] = []
    for index, passage in enumerate(passages):
        if not isinstance(passage, dict):
            continue
        evidence_id = _passage_evidence_id(passage)
        signature = evidence_id or (
            _passage_document_key(passage),
            _passage_position(passage, index),
            _diag_norm(_passage_full_text(passage))[:300],
        )
        if not _passage_full_text(passage) or signature in seen:
            continue
        seen.add(signature)
        output.append(passage)
    return output


def _dedupe_proofs(proofs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    output: List[Dict[str, Any]] = []
    for proof in proofs:
        if not isinstance(proof, dict):
            continue
        signature = clean_text(proof.get("evidence_id") or proof.get("passage_id")) or (
            clean_text(proof.get("document")),
            clean_text(proof.get("source_text_original") or proof.get("excerpt"))[:300],
        )
        if signature in seen:
            continue
        seen.add(signature)
        output.append(proof)
    return output


def _semantic_bigrams(value: Any) -> set[str]:
    tokens = [token for token in _diag_norm(value).split() if len(token) >= 4]
    return {f"{left} {right}" for left, right in zip(tokens, tokens[1:])}


def _operation_semantic_link_report(
    passage: Dict[str, Any],
    group: Optional[Dict[str, Any]],
    purpose: str = "",
    passage_index: int = 0,
) -> Dict[str, Any]:
    if not isinstance(group, dict) or not group:
        return {
            "score": 0.0,
            "operation_id": None,
            "purpose": purpose or None,
            "shared_terms": [],
            "reasons_fr": ["Le rattachement à une opération précise n’a pas pu être vérifié."],
            "direct_support": False,
        }

    supporting = [
        item for item in (group.get("supporting_passages") or [])
        if isinstance(item, dict)
    ]
    evidence_id = _passage_evidence_id(passage)
    supporting_ids = {_passage_evidence_id(item) for item in supporting if _passage_evidence_id(item)}
    direct_support = bool(evidence_id and evidence_id in supporting_ids)
    document_key = _passage_document_key(passage)
    same_document_support = [item for item in supporting if _passage_document_key(item) == document_key]
    same_document = bool(document_key and same_document_support)

    operation_text = " ".join((
        clean_text(group.get("text")),
        clean_text(group.get("analysis_text")),
        clean_text(group.get("technical_scope")),
        clean_text(group.get("section_title")),
        " ".join(_passage_full_text(item) for item in supporting[:8]),
    ))
    passage_text = _passage_full_text(passage)
    operation_tokens = _distinctive_tokens(operation_text)
    passage_tokens = _distinctive_tokens(passage_text)
    shared_terms = sorted(operation_tokens & passage_tokens, key=lambda value: (-len(value), value))[:10]
    shared_bigrams = _semantic_bigrams(operation_text) & _semantic_bigrams(passage_text)

    distance: Optional[int] = None
    if same_document_support and _passage_has_position(passage):
        positioned = [item for item in same_document_support if _passage_has_position(item)]
        if positioned:
            distance = min(
                abs(_passage_position(passage, passage_index) - _passage_position(item))
                for item in positioned
            )

    purpose_pattern = _FUNCTION_PATTERNS.get(purpose) or _FRASCATI_PROOF_PATTERNS.get(purpose)
    purpose_aligned = bool(purpose_pattern and purpose_pattern.search(passage_text))
    score = 0.0
    reasons: List[str] = []
    if direct_support:
        score += 0.45
        reasons.append("le passage appartient aux preuves déjà rattachées à l’opération")
    if same_document:
        score += 0.12
        reasons.append("il provient du même document que l’opération")
    if distance is not None:
        if distance <= 6:
            score += 0.16
            reasons.append("il est immédiatement voisin du passage décrivant l’opération")
        elif distance <= 14:
            score += 0.11
            reasons.append("il est proche du passage décrivant l’opération")
        elif distance <= 30:
            score += 0.05
    if shared_terms:
        score += min(0.16, len(shared_terms) * 0.025)
        reasons.append("il partage le vocabulaire technique propre à l’opération")
    if shared_bigrams:
        score += min(0.06, len(shared_bigrams) * 0.02)
    if purpose_aligned:
        score += 0.08
        reasons.append(f"son contenu remplit la fonction documentaire « {purpose} »")
    score = min(1.0, score)
    rendered_terms = ", ".join(shared_terms[:5])
    purpose_labels = {
        "uncertainty": "incertitude ou verrou",
        "hypothesis": "hypothèse",
        "experiment": "expérimentation",
        "result": "résultat",
        "learning": "apprentissage",
        "novelty": "nouveauté",
        "creativity": "créativité",
        "systematicity": "démarche systématique",
        "transferability": "transférabilité",
    }
    if purpose_aligned:
        reasons[-1] = (
            "son contenu remplit la fonction documentaire « "
            + purpose_labels.get(purpose, purpose)
            + " »"
        )
    bridge = "Cette preuve est reliée à l’opération"
    if reasons:
        bridge += " car " + ", ".join(reasons[:3])
    if rendered_terms:
        bridge += f" ; les termes techniques communs les plus discriminants sont : {rendered_terms}"
    bridge += "."
    return {
        "score": round(score, 4),
        "operation_id": clean_text(group.get("lock_group_id") or group.get("passage_id")) or None,
        "purpose": purpose or None,
        "shared_terms": shared_terms,
        "shared_bigrams_count": len(shared_bigrams),
        "same_document": same_document,
        "position_distance": distance,
        "purpose_aligned": purpose_aligned,
        "direct_support": direct_support,
        "reasons_fr": reasons,
        "justification_bridge_fr": bridge,
    }


def _operation_affinity_score(
    passage: Dict[str, Any],
    group: Optional[Dict[str, Any]],
    passage_index: int,
) -> float:
    return 100.0 * float(
        _operation_semantic_link_report(passage, group, passage_index=passage_index).get("score") or 0.0
    )


def _proof_summary_fr(purpose: str, passage: Dict[str, Any], values: Optional[List[str]] = None) -> str:
    # Le résumé visible ne doit jamais devenir une liste brute de nombres. Les
    # valeurs restent dans ``quantitative_values`` pour le LLM et la traçabilité,
    # qui doivent les remettre dans leur contexte (métrique, objet, comparaison).
    summaries = {
        "uncertainty": "Ce passage documente l’incertitude technique, le verrou ou la limite qui motive l’investigation.",
        "hypothesis": "Ce passage formule ou étaye l’hypothèse technique et la raison de l’investigation.",
        "experiment": "Ce passage décrit le protocole, les paramètres, les données, la comparaison ou les conditions expérimentales effectivement mobilisés.",
        "result": "Ce passage rapporte un résultat observé ou une conclusion quantitative issue des travaux ; les valeurs doivent être interprétées avec leur métrique et leur objet.",
        "learning": "Ce passage présente l’enseignement ou la conclusion tirée des résultats obtenus.",
        "novelty": "Ce passage situe les limites des connaissances ou solutions existantes qui soutiennent la nouveauté recherchée.",
        "creativity": "Ce passage documente une hypothèse, une contribution, une nouvelle conception ou une combinaison originale.",
        "systematicity": "Ce passage décrit une démarche effectivement menée dans le projet, avec un protocole, des paramètres ou une validation traçable.",
        "transferability": "Ce passage documente des paramètres, une procédure réutilisable, une validation dans plusieurs conditions ou des connaissances transférables.",
    }
    return summaries.get(purpose, "Ce passage apporte une preuve directement rattachée à l’opération évaluée.")


def _best_source_fragment(passage: Dict[str, Any], purpose: str) -> Tuple[str, str, bool]:
    candidates: List[Tuple[float, str, str]] = []
    pattern = _FUNCTION_PATTERNS.get(purpose) or _FRASCATI_PROOF_PATTERNS.get(purpose)
    for field in ("text", "context_before", "context_after"):
        raw_value = re.sub(r"\s+", " ", clean_text(passage.get(field))).strip()
        if not raw_value:
            continue
        for value in _fragment_windows(raw_value):
            score = min(len(value), 500) / 500 * 3.0
            if pattern is not None:
                score += min(len(pattern.findall(value)), 8) * 12.0
            if purpose == "result":
                score += min(len(_quantitative_values(value)), 8) * 14.0
                score += min(len(_RESULT_OBSERVATION_PATTERN.findall(value)), 5) * 14.0
                scope = _result_scope(passage, value)
                score += {
                    "global_comparison": 95.0,
                    "global_metric": 72.0,
                    "observed_gain": 60.0,
                    "observed_metric": 48.0,
                    "qualitative_observation": 22.0,
                    "per_item_metric": -18.0,
                    "headroom_context": -65.0,
                    "result_context": -40.0,
                }.get(scope, 0.0)
            elif purpose == "hypothesis":
                quality = _hypothesis_quality({**passage, "text": value, "context_before": "", "context_after": "", "analysis_text": ""})
                score += 90.0 if quality["explicit"] else 0.0
                score += 42.0 if quality["proposal"] else 0.0
                score += 95.0 if quality["design_proposal"] else 0.0
                score += 22.0 if quality["linked_reason"] else 0.0
                score -= 110.0 if quality["research_objective_only"] else 0.0
                score -= 120.0 if quality["rejected_or_counterfactual"] else 0.0
                if quality["explicit"] and len(value) < 90 and not _HYPOTHESIS_LINK_PATTERN.search(value):
                    score -= 85.0
            elif purpose in {"experiment", "systematicity", "transferability"}:
                score += min(len(_EXPERIMENT_PATTERN.findall(value)), 8) * 9.0
                if _STRONG_PROTOCOL_PATTERN.search(value):
                    score += 65.0
            # Les très gros fragments favorisent les mélanges de faits ; pénalité douce.
            if len(value) > 800:
                score -= (len(value) - 800) / 40.0
            candidates.append((score, field, value))
    if candidates:
        _, field, value = max(candidates, key=lambda item: item[0])
        return value, field, True
    analysis = re.sub(r"\s+", " ", clean_text(passage.get("analysis_text"))).strip()
    return analysis, "analysis_text", False


def _purpose_score(
    passage: Dict[str, Any],
    purpose: str,
    group: Optional[Dict[str, Any]],
    passage_index: int,
    preferred_evidence_ids: Optional[set[str]] = None,
) -> float:
    if _is_result_metadata(passage):
        return -1000.0
    full_text = _passage_full_text(passage)
    section = _passage_section_text(passage)
    joined = f"{section} {full_text}".strip()
    if not joined:
        return -1000.0
    reference_like = _is_reference_like_passage(passage)

    role = _diag_norm(
        passage.get("role")
        or passage.get("semantic_role")
        or passage.get("original_model_role")
        or (
            passage.get("metadata", {}).get("role")
            if isinstance(passage.get("metadata"), dict)
            else ""
        )
    )
    evidence_id = _passage_evidence_id(passage)
    values = _quantitative_values(full_text)
    score = _operation_affinity_score(passage, group, passage_index)
    if evidence_id and evidence_id in (preferred_evidence_ids or set()):
        score += 42.0
    if role in _ROLE_HINTS.get(purpose, set()):
        score += 24.0

    pattern = _FUNCTION_PATTERNS.get(purpose) or _FRASCATI_PROOF_PATTERNS.get(purpose)
    if pattern is not None:
        score += min(len(pattern.findall(joined)), 8) * 15.0

    if purpose == "uncertainty":
        score += min(len(_LIMITATION_PATTERN.findall(joined)), 6) * 13.0
    elif purpose == "hypothesis":
        quality = _hypothesis_quality(passage)
        score += min(len(_HYPOTHESIS_LINK_PATTERN.findall(joined)), 6) * 10.0
        score += min(len(_CONTRIBUTION_PATTERN.findall(joined)), 5) * 8.0
        if quality["explicit"]:
            score += 135.0
        elif quality["proposal"]:
            score += 58.0
        if quality["design_proposal"]:
            score += 130.0
        if quality["linked_reason"]:
            score += 24.0
        if quality["rejected_or_counterfactual"]:
            score -= 190.0
        # Une question « étudier l'impact de X » n'est pas encore l'hypothèse.
        if quality["research_objective_only"]:
            score -= 155.0
        if reference_like:
            score -= 120.0
    elif purpose == "experiment":
        score += min(len(_EXPERIMENT_PATTERN.findall(joined)), 10) * 12.0
        if values:
            score += min(len(values), 6) * 5.0
        if _STRONG_PROTOCOL_PATTERN.search(joined):
            score += 115.0
        if _CONTROLLED_PROTOCOL_PATTERN.search(joined):
            score += 135.0
        if _PROJECT_ACTION_PATTERN.search(joined):
            score += 70.0
        if reference_like:
            score -= 320.0
        if role == "resultat" or _RESULT_SECTION_PATTERN.search(section):
            score -= 95.0
        if _THIRD_PARTY_WORK_PATTERN.search(joined) and not _PROJECT_ACTION_PATTERN.search(full_text):
            score -= 240.0
        if _STATE_OF_ART_PATTERN.search(joined) and not _PROJECT_ACTION_PATTERN.search(full_text):
            score -= 180.0
        # Une simple mention de simulation/dataset n'est pas suffisante pour être
        # une expérience du projet.
        if not (_STRONG_PROTOCOL_PATTERN.search(joined) or _PROJECT_ACTION_PATTERN.search(joined)):
            score -= 65.0
    elif purpose == "result":
        if reference_like:
            score -= 340.0
        if _THIRD_PARTY_WORK_PATTERN.search(joined) and not _PROJECT_ACTION_PATTERN.search(full_text):
            score -= 260.0
        if _STATE_OF_ART_PATTERN.search(joined) and not _PROJECT_ACTION_PATTERN.search(full_text):
            score -= 180.0
        if role == "resultat":
            score += 120.0
        if _RESULT_SECTION_PATTERN.search(section):
            score += 90.0
        if _LEARNING_PATTERN.search(joined):
            score += 34.0
        if _RESULT_OBSERVATION_PATTERN.search(joined):
            score += 42.0
        score += min(len(values), 10) * 14.0
        scope = _result_scope(passage)
        score += {
            "global_comparison": 145.0,
            "global_metric": 105.0,
            "observed_gain": 82.0,
            "observed_metric": 65.0,
            "qualitative_observation": 25.0,
            "per_item_metric": -35.0,
            "headroom_context": -115.0,
            "result_context": -80.0,
        }.get(scope, 0.0)
        outcome_signal = bool(
            role == "resultat"
            or _RESULT_SECTION_PATTERN.search(section)
            or _LEARNING_PATTERN.search(joined)
            or _RESULT_OBSERVATION_PATTERN.search(joined)
        )
        if not outcome_signal:
            score -= 170.0 if _EXPERIMENT_PATTERN.search(joined) else 95.0
    elif purpose == "learning":
        if reference_like:
            score -= 250.0
        if _RESULT_SECTION_PATTERN.search(section):
            score += 50.0
        if role == "resultat":
            score += 45.0
        if values:
            score += min(len(values), 6) * 5.0
    elif purpose == "novelty":
        if _STATE_OF_ART_PATTERN.search(joined):
            score += 75.0
        score += min(len(_LIMITATION_PATTERN.findall(joined)), 6) * 16.0
    elif purpose == "creativity":
        score += min(len(_CONTRIBUTION_PATTERN.findall(joined)), 8) * 16.0
        score += min(len(_HYPOTHESIS_EVIDENCE_PATTERN.findall(joined)), 6) * 9.0
    elif purpose == "systematicity":
        score += min(len(_EXPERIMENT_PATTERN.findall(joined)), 10) * 14.0
        if _STRONG_PROTOCOL_PATTERN.search(joined):
            score += 95.0
        if _PROJECT_ACTION_PATTERN.search(joined):
            score += 70.0
        if reference_like:
            score -= 320.0
        if _STATE_OF_ART_PATTERN.search(joined) and not _PROJECT_ACTION_PATTERN.search(full_text):
            score -= 220.0
    elif purpose == "transferability":
        score += min(len(_REPRODUCIBILITY_PATTERN.findall(joined)), 8) * 18.0
        score += min(len(_EXPERIMENT_PATTERN.findall(joined)), 6) * 7.0
        if _PROJECT_ACTION_PATTERN.search(joined):
            score += 28.0
        if reference_like and not _REPRODUCIBILITY_PATTERN.search(joined):
            score -= 180.0

    if passage.get("analysis_text"):
        score += 5.0
    if passage.get("context_before") or passage.get("context_after"):
        score += 4.0
    return score


def _rank_passages_for_purpose(
    passages: List[Dict[str, Any]],
    purpose: str,
    group: Optional[Dict[str, Any]] = None,
    preferred_evidence_ids: Optional[List[str]] = None,
    max_items: int = 3,
) -> List[Dict[str, Any]]:
    preferred = {clean_text(value) for value in (preferred_evidence_ids or []) if clean_text(value)}
    candidates: List[Tuple[float, int, Dict[str, Any], List[str]]] = []
    for index, passage in enumerate(_dedupe_nlp_passages(passages)):
        score = _purpose_score(passage, purpose, group, index, preferred)
        semantic_link = _operation_semantic_link_report(
            passage,
            group,
            purpose=purpose,
            passage_index=index,
        )
        if isinstance(group, dict) and group:
            link_score = float(semantic_link.get("score") or 0.0)
            direct_support = bool(semantic_link.get("direct_support"))
            shared_terms = semantic_link.get("shared_terms") or []
            distance = semantic_link.get("position_distance")
            purpose_aligned = bool(semantic_link.get("purpose_aligned"))
            close_and_aligned = bool(
                distance is not None
                and int(distance) <= 6
                and purpose_aligned
                and len(shared_terms) >= 1
            )
            # Même document + motif lexical ne suffit plus. On exige soit une
            # preuve directement possédée par le groupe, soit un lien sémantique
            # réellement discriminant avec l'opération.
            if not direct_support and not close_and_aligned and (link_score < 0.32 or len(shared_terms) < 2):
                continue
        if score < 35.0:
            continue
        values = _quantitative_values(_passage_full_text(passage))
        passage_with_link = {**passage, "_semantic_link": semantic_link}
        candidates.append((score, index, passage_with_link, values))
    candidates.sort(key=lambda item: (item[0], -item[1]), reverse=True)

    output: List[Dict[str, Any]] = []
    for score, _, passage, values in candidates[:max_items]:
        proof = _nlp_passage_proof(passage)
        source_fragment, source_field, source_is_original = _best_source_fragment(passage, purpose)
        source_values = _quantitative_values(source_fragment)
        grounded_values = source_values if source_is_original else values
        semantic_link = passage.get("_semantic_link") if isinstance(passage.get("_semantic_link"), dict) else {}
        hypothesis_quality = _hypothesis_quality(passage) if purpose == "hypothesis" else {}
        result_scope = _result_scope(passage, source_fragment) if purpose == "result" else None
        proof.update({
            "proof_kind": purpose,
            "selection_score": round(score, 2),
            "summary_fr": _proof_summary_fr(purpose, passage, grounded_values),
            "quantitative_values": grounded_values,
            "source_field": source_field,
            "source_is_original": source_is_original,
            "reference_like": _is_reference_like_passage(passage),
            "result_scope": result_scope,
            "hypothesis_explicit": hypothesis_quality.get("explicit"),
            "hypothesis_design_proposal": hypothesis_quality.get("design_proposal"),
            "hypothesis_research_objective_only": hypothesis_quality.get("research_objective_only"),
            "hypothesis_rejected_or_counterfactual": hypothesis_quality.get("rejected_or_counterfactual"),
            "selection_context_excerpt": truncate(_passage_full_text(passage), 900),
            "source_text_original": truncate(source_fragment, 1200),
            "excerpt": truncate(source_fragment, 700),
            "semantic_link": semantic_link,
            "justification_bridge_fr": clean_text(semantic_link.get("justification_bridge_fr")),
        })
        output.append(proof)
    return output


def _proofs_are_linked(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    if _passage_document_key(left) != _passage_document_key(right):
        return False
    if (
        _passage_has_position(left)
        and _passage_has_position(right)
        and abs(_passage_position(left) - _passage_position(right)) <= 30
    ):
        return True
    left_text = _passage_full_text(left) or clean_text(left.get("source_text_original") or left.get("excerpt"))
    right_text = _passage_full_text(right) or clean_text(right.get("source_text_original") or right.get("excerpt"))
    return len(_distinctive_tokens(left_text) & _distinctive_tokens(right_text)) >= 2


def _causal_coherence_report(function_evidence: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    stages = ["uncertainty", "hypothesis", "experiment", "result", "learning"]
    present = {stage: bool(function_evidence.get(stage)) for stage in stages}
    base_score = sum(1 for value in present.values() if value) / len(stages)
    linked_pairs: List[str] = []
    for left_stage, right_stage in zip(stages, stages[1:]):
        left_proofs = function_evidence.get(left_stage) or []
        right_proofs = function_evidence.get(right_stage) or []
        if any(
            _proofs_are_linked(left, right)
            for left in left_proofs
            for right in right_proofs
        ):
            linked_pairs.append(f"{left_stage}->{right_stage}")
    causal_link_bonus = min(0.2, len(linked_pairs) * 0.05)
    score = min(1.0, base_score + causal_link_bonus)
    required_proofs = bool(
        present["uncertainty"]
        and present["experiment"]
        and (present["result"] or present["learning"])
    )
    explicit_hypothesis_or_strong_reasoning = bool(
        present["hypothesis"] or (required_proofs and len(linked_pairs) >= 3)
    )
    return {
        "score": round(score, 4),
        "base_stage_coverage": round(base_score, 4),
        "causal_link_bonus": round(causal_link_bonus, 4),
        "stages_present": present,
        "linked_pairs": linked_pairs,
        "chain_complete": all(present.values()),
        "required_proofs_present": required_proofs,
        "explicit_hypothesis_or_strong_reasoning": explicit_hypothesis_or_strong_reasoning,
        "defendable_evidence_gate": bool(required_proofs and explicit_hypothesis_or_strong_reasoning),
    }


def _review_operation_status(raw_status: str, causal_report: Dict[str, Any]) -> Tuple[str, str]:
    status = raw_status or "insufficient_evidence"
    gate = bool(causal_report.get("defendable_evidence_gate"))
    required = bool(causal_report.get("required_proofs_present"))
    if status == "classical_engineering":
        return status, "La qualification d’ingénierie classique issue du garde métier est conservée."
    if status == "insufficient_evidence":
        return status, (
            "Les preuves restent insuffisantes et nécessitent une validation du consultant ; "
            "elles ne sont pas requalifiées automatiquement en ingénierie classique."
        )
    if status == "rnd_core_defendable" and not gate:
        if required:
            return "rnd_core_partial", (
                "L’incertitude, l’expérimentation et le résultat sont rattachés, mais l’hypothèse explicite "
                "ou la continuité causale doit encore être consolidée."
            )
        return "insufficient_evidence", (
            "La qualification R&D défendable n’est pas affichée faute de preuves suffisantes sur "
            "l’incertitude, l’expérimentation et le résultat ou l’apprentissage."
        )
    if status == "rnd_core_partial" and not required:
        return "insufficient_evidence", (
            "La chaîne documentaire reste trop incomplète pour soutenir un noyau R&D partiel sans validation."
        )
    return status, "Le statut est cohérent avec la chaîne de preuves rattachée à l’opération."


def _criterion_reason_fr(criterion: Dict[str, Any], evidence: List[Dict[str, Any]]) -> str:
    label = clean_text(criterion.get("label") or criterion.get("criterion"))
    status = clean_text(criterion.get("status"))
    if status == "documented" and evidence:
        return f"Le critère « {label} » est documenté par des passages classés selon leur pertinence pour cette opération."
    if status == "partial" and evidence:
        return f"Le critère « {label} » est partiellement documenté ; les preuves disponibles doivent encore être consolidées."
    if status == "contradictory":
        return f"Les éléments rattachés au critère « {label} » sont contradictoires et nécessitent une vérification du consultant."
    return f"Le critère « {label} » ne dispose pas encore d’une preuve projet suffisamment explicite."


def _selection_number(value: Any) -> float:
    try:
        return float(str(value if value is not None else 0.0).strip().replace("%", "").replace(",", "."))
    except Exception:
        return 0.0


def _operation_justification_fr(operation: Dict[str, Any]) -> str:
    """Résumé d'audit déterministe, jamais utilisé comme récit final du projet.

    Le récit consultant doit être produit à partir des preuves fonctionnelles
    (verrou → hypothèse → expérience → résultat → apprentissage). Cette fonction
    évite donc volontairement toute liste de nombres et toute pseudo-explication
    technique générique qui pourrait être concaténée dans la conclusion globale.
    """
    functional = operation.get("functional_evidence") if isinstance(operation.get("functional_evidence"), dict) else {}
    causal = operation.get("causal_coherence") if isinstance(operation.get("causal_coherence"), dict) else {}
    stages = causal.get("stages_present") if isinstance(causal.get("stages_present"), dict) else {}

    present_labels = [
        label
        for key, label in (
            ("uncertainty", "incertitude/verrou"),
            ("hypothesis", "hypothèse"),
            ("experiment", "expérimentation"),
            ("result", "résultat"),
            ("learning", "apprentissage"),
        )
        if stages.get(key) and functional.get(key)
    ]
    missing_labels = [
        label
        for key, label in (
            ("uncertainty", "incertitude/verrou"),
            ("hypothesis", "hypothèse"),
            ("experiment", "expérimentation"),
            ("result", "résultat"),
            ("learning", "apprentissage"),
        )
        if not (stages.get(key) and functional.get(key))
    ]

    status = clean_text(operation.get("operation_status"))
    status_text = {
        "rnd_core_defendable": "Le garde métier classe cette opération comme noyau R&D défendable.",
        "rnd_core_partial": "Le garde métier identifie un noyau R&D partiel à consolider.",
        "classical_engineering": "Le garde métier classe cette opération comme ingénierie classique au vu des preuves rattachées.",
        "insufficient_evidence": "Les preuves rattachées sont insuffisantes pour qualifier le noyau R&D.",
    }.get(status, "La qualification de l’opération doit être validée.")

    parts = [status_text]
    if present_labels:
        parts.append("Maillons documentés : " + ", ".join(present_labels) + ".")
    if missing_labels:
        parts.append("Maillons à consolider : " + ", ".join(missing_labels) + ".")
    if operation.get("consultant_validation_required"):
        parts.append("Validation du consultant CIR requise.")
    return " ".join(parts)


def _operation_passage_pool(
    group: Dict[str, Any],
    additional_passages: List[Dict[str, Any]],
    max_neighbor_distance: int = 14,
    foreign_owned_evidence_ids: Optional[set[str]] = None,
) -> List[Dict[str, Any]]:
    """Élargit prudemment les preuves d'une opération sans fuite inter-opérations.

    Un passage déjà utilisé comme preuve de base d'une autre opération n'est pas
    réattaché automatiquement. Un simple voisinage documentaire ne suffit plus :
    il faut également un signal fonctionnel et un lien lexical/sectionnel avec
    l'opération courante.
    """
    base = [item for item in (group.get("supporting_passages") or []) if isinstance(item, dict)]
    base_by_document: Dict[str, List[int]] = {}
    base_tokens_by_document: Dict[str, List[set[str]]] = {}
    base_sections_by_document: Dict[str, set[str]] = {}
    for index, passage in enumerate(base):
        document_key = _passage_document_key(passage)
        if document_key:
            base_by_document.setdefault(document_key, []).append(_passage_position(passage, index))
            base_tokens_by_document.setdefault(document_key, []).append(
                _distinctive_tokens(_passage_full_text(passage))
            )
            section = _diag_norm(_passage_section_text(passage))
            if section:
                base_sections_by_document.setdefault(document_key, set()).add(section)

    attached = list(base)
    base_ids = {_passage_evidence_id(item) for item in base if _passage_evidence_id(item)}
    foreign_owned = foreign_owned_evidence_ids or set()

    for index, passage in enumerate(additional_passages):
        evidence_id = _passage_evidence_id(passage)
        if evidence_id and (evidence_id in base_ids or evidence_id in foreign_owned):
            continue
        document_key = _passage_document_key(passage)
        positions = base_by_document.get(document_key) or []
        if not positions:
            continue

        full_text = _passage_full_text(passage)
        if not full_text or _is_result_metadata(passage):
            continue
        candidate_tokens = _distinctive_tokens(full_text)
        shared_token_count = max(
            (len(candidate_tokens & base_tokens) for base_tokens in (base_tokens_by_document.get(document_key) or [])),
            default=0,
        )
        position = _passage_position(passage, index)
        distance = min(abs(position - base_position) for base_position in positions)
        section = _diag_norm(_passage_section_text(passage))
        same_section = bool(section and section in (base_sections_by_document.get(document_key) or set()))

        has_function_or_criterion_signal = any(
            pattern.search(full_text)
            for pattern in (
                _HYPOTHESIS_EVIDENCE_PATTERN,
                _EXPERIMENT_PATTERN,
                _RESULT_SIGNAL_PATTERN,
                _LEARNING_PATTERN,
                *_FRASCATI_PROOF_PATTERNS.values(),
            )
        )
        if not has_function_or_criterion_signal:
            continue

        strong_neighbor = distance <= min(max_neighbor_distance, 6) and shared_token_count >= 1
        strong_semantic = shared_token_count >= 2
        section_link = same_section and shared_token_count >= 1 and distance <= max_neighbor_distance
        if strong_neighbor or strong_semantic or section_link:
            attached.append(passage)

    return _dedupe_nlp_passages(attached)


def _build_hypothesis_evidence(
    passages: List[Dict[str, Any]],
    group: Optional[Dict[str, Any]] = None,
    max_items: int = 5,
) -> List[Dict[str, Any]]:
    """Reconstruit l'hypothèse en privilégiant une proposition explicite non rejetée."""
    ranked = _rank_passages_for_purpose(
        passages,
        "hypothesis",
        group=group,
        max_items=max(max_items * 4, 12),
    )
    passages_by_id = {
        clean_text(item.get("passage_id") or item.get("id")): item
        for item in _dedupe_nlp_passages(passages)
    }
    candidates: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for proof in ranked:
        if proof.get("reference_like") or proof.get("hypothesis_rejected_or_counterfactual"):
            continue
        raw = passages_by_id.get(clean_text(proof.get("evidence_id")))
        if isinstance(raw, dict):
            candidates.append((proof, raw))
    if not candidates:
        return []

    explicit_candidates = [item for item in candidates if item[0].get("hypothesis_explicit")]
    seed_pool = explicit_candidates or candidates
    # L'ancre doit contenir la matière technique de l'hypothèse, pas seulement
    # une amorce du type « nous proposons une nouvelle approche ».
    seed = max(
        seed_pool,
        key=lambda item: (
            1 if item[0].get("hypothesis_design_proposal") else 0,
            1 if _HYPOTHESIS_LINK_PATTERN.search(clean_text(item[0].get("excerpt"))) else 0,
            min(len(clean_text(item[0].get("excerpt"))), 500),
            float(item[0].get("selection_score") or 0.0),
        ),
    )
    selected = [seed]
    for candidate in candidates:
        if candidate is seed:
            continue
        linked = any(_proofs_are_linked(candidate[1], current[1]) for current in selected)
        if not linked:
            continue
        selected.append(candidate)
        if len(selected) >= min(max_items, 4):
            break
    seed_id = clean_text(seed[0].get("evidence_id"))
    selected.sort(key=lambda item: _passage_position(item[1]))
    proofs: List[Dict[str, Any]] = []
    component_ids = [clean_text(item[0].get("evidence_id")) for item in selected]
    for index, (proof, _) in enumerate(selected):
        proof = dict(proof)
        is_anchor = clean_text(proof.get("evidence_id")) == seed_id
        proof.update({
            "proof_kind": "hypothesis_component",
            "reconstruction_basis": "explicit_proposal_then_same_document_semantic_neighbors",
            "hypothesis_component_ids": component_ids,
            "hypothesis_anchor": is_anchor,
            "summary_fr": (
                "Ce passage constitue le point d’ancrage de l’hypothèse technique."
                if is_anchor
                else "Ce passage complète l’hypothèse par une raison, une condition ou une conséquence explicitement reliée."
            ),
        })
        proofs.append(proof)
    return proofs


def _quantitative_values(value: Any) -> List[str]:
    output: List[str] = []
    for match in _QUANTITATIVE_PATTERN.finditer(str(value or "")):
        token = re.sub(r"\s+", " ", match.group(0)).strip()
        bare = token.replace("%", "").replace(",", ".").strip()
        try:
            number = float(re.match(r"[-+]?\d+(?:\.\d+)?", bare).group(0))  # type: ignore[union-attr]
        except Exception:
            number = None
        if number is not None and 1900 <= number <= 2100 and not re.search(r"[%A-Za-z°]", token):
            continue
        normalized = token.lower().replace(" ", "").replace(",", ".")
        if normalized and normalized not in output:
            output.append(normalized)
    return output


def _build_result_evidence(
    passages: List[Dict[str, Any]],
    group: Optional[Dict[str, Any]] = None,
    max_items: int = 6,
) -> List[Dict[str, Any]]:
    candidates = _rank_passages_for_purpose(
        passages,
        "result",
        group=group,
        max_items=max(max_items * 5, 20),
    )
    scope_priority = {
        "global_comparison": 6,
        "global_metric": 5,
        "observed_gain": 4,
        "observed_metric": 4,
        "qualitative_observation": 3,
        "per_item_metric": 2,
        "headroom_context": 1,
        "result_context": 0,
    }
    candidates = [item for item in candidates if not item.get("reference_like")]
    candidates.sort(
        key=lambda item: (
            scope_priority.get(clean_text(item.get("result_scope")), 0),
            float(item.get("selection_score") or 0.0),
        ),
        reverse=True,
    )

    selected: List[Dict[str, Any]] = []
    covered_values: set[str] = set()
    primary_selected = 0
    for raw_proof in candidates:
        if len(selected) >= max_items:
            break
        proof = dict(raw_proof)
        scope = clean_text(proof.get("result_scope")) or "result_context"
        values = [str(value) for value in (proof.get("quantitative_values") or [])]
        new_values = set(values) - covered_values

        # Les preuves globales/comparatives sont prioritaires. Les métriques par
        # classe et les bornes théoriques ne doivent jamais devenir le résultat
        # principal lorsqu'une preuve globale existe.
        if scope in {"global_comparison", "global_metric", "observed_gain", "observed_metric"}:
            primary_selected += 1
        elif scope == "per_item_metric" and primary_selected >= 2:
            continue
        elif scope == "headroom_context" and primary_selected >= 1:
            continue

        if selected and not new_values and scope != "qualitative_observation":
            continue
        proof.update({
            "proof_kind": "quantitative_result" if values else "qualitative_result",
            "result_priority_score": proof.get("selection_score"),
            "quantitative_values": values,
            "metadata_excluded": False,
            "primary_result_evidence": scope in {"global_comparison", "global_metric", "observed_gain", "observed_metric"},
        })
        selected.append(proof)
        covered_values.update(values)

    # Si aucun résultat principal n'a été trouvé, garder au maximum deux preuves
    # secondaires au lieu de remplir la sortie avec des métriques par classe.
    if not any(item.get("primary_result_evidence") for item in selected):
        selected = selected[:2]
    return selected


def _criterion_breakdown_from_assessment(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    existing = raw.get("criteria_score_breakdown")
    if isinstance(existing, list) and existing:
        return [dict(item) for item in existing if isinstance(item, dict)]
    criteria = raw.get("criteria") or raw.get("dimensions") or {}
    if not isinstance(criteria, dict):
        criteria = {}
    output: List[Dict[str, Any]] = []
    for criterion in _FRASCATI_LABELS:
        item = criteria.get(criterion) if isinstance(criteria.get(criterion), dict) else {}
        status = clean_text(item.get("status")) or "missing"
        contribution = round(0.2 * _FRASCATI_STATUS_VALUE.get(status, 0.0), 4)
        output.append({
            "criterion": criterion,
            "label": _FRASCATI_LABELS[criterion],
            "status": status,
            "criterion_weight": 0.2,
            "contribution_to_index": contribution,
            "remaining_gap_to_full_coverage": round(0.2 - contribution, 4),
            "reason": clean_text(item.get("reason")),
            "evidence_ids": [clean_text(value) for value in (item.get("evidence_ids") or []) if clean_text(value)],
            "question": clean_text(item.get("question")) or None,
        })
    return output



def _build_eligibility_evidence_report_fast(
    assessment: Dict[str, Any],
    technical_groups: List[Dict[str, Any]],
    additional_passages: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Construit le rapport d'éligibilité sans reranking sémantique coûteux.

    ``Préparer les sources`` a déjà produit :
    - les groupes techniques ;
    - les statuts de démarche ;
    - les identifiants des preuves de la chaîne causale ;
    - les cinq critères Frascati.

    Le diagnostic n'a donc pas besoin de rescanner/reranker tout le catalogue
    NLP (souvent > 1 000 passages) plusieurs dizaines de fois. Cette version
    résout simplement les ``evidence_ids`` déjà décidés en amont et conserve
    strictement les statuts officiels. Elle ne crée, ne fusionne et ne supprime
    aucun verrou.
    """
    raw_assessments = [
        item for item in (assessment.get("group_assessments") or [])
        if isinstance(item, dict)
    ]
    groups_by_id: Dict[str, Dict[str, Any]] = {}
    for group in technical_groups or []:
        if not isinstance(group, dict):
            continue
        gid = clean_text(group.get("lock_group_id") or group.get("passage_id"))
        if gid:
            groups_by_id[gid] = group

    # Résolution O(n) des preuves déjà sélectionnées par le NLP. On indexe le
    # catalogue une seule fois au lieu de reranker tous les passages pour chaque
    # critère, maillon et opération.
    proof_by_id: Dict[str, Dict[str, Any]] = {}
    group_proof_ids: Dict[str, List[str]] = {}

    def register(passage: Any, group_id: str = "") -> None:
        if not isinstance(passage, dict):
            return
        pid = _passage_evidence_id(passage)
        if not pid:
            return
        if pid not in proof_by_id:
            proof = _nlp_passage_proof(passage)
            if proof.get("excerpt"):
                proof_by_id[pid] = proof
        if group_id:
            ids = group_proof_ids.setdefault(group_id, [])
            if pid not in ids:
                ids.append(pid)

    for gid, group in groups_by_id.items():
        for passage in group.get("supporting_passages") or []:
            register(passage, gid)
        register(group, gid)

    for passage in additional_passages or []:
        register(passage)

    def resolve(ids: Any, *, max_items: int = 4, fallback_group_id: str = "") -> List[Dict[str, Any]]:
        output: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for raw_id in ids or []:
            pid = clean_text(raw_id)
            proof = proof_by_id.get(pid)
            if not pid or not isinstance(proof, dict) or pid in seen:
                continue
            seen.add(pid)
            output.append(dict(proof))
            if len(output) >= max_items:
                return output
        # Fallback documentaire minimal : seulement une preuve directement
        # attachée au même groupe, jamais un voisin sémantique reconstruit.
        if not output and fallback_group_id:
            for pid in group_proof_ids.get(fallback_group_id) or []:
                proof = proof_by_id.get(pid)
                if isinstance(proof, dict) and pid not in seen:
                    output.append(dict(proof))
                    seen.add(pid)
                    if len(output) >= max_items:
                        break
        return output

    basis_group_id = clean_text(assessment.get("score_basis_group_id"))
    if not basis_group_id and raw_assessments:
        basis_group_id = clean_text(raw_assessments[0].get("group_id"))

    operation_reports: List[Dict[str, Any]] = []
    for raw in raw_assessments:
        gid = clean_text(raw.get("group_id"))
        if not gid:
            continue
        group = groups_by_id.get(gid, {})
        demarche = raw.get("demarche_legibility")
        demarche = demarche if isinstance(demarche, dict) else {}
        causal = demarche.get("causal_chain")
        causal = causal if isinstance(causal, dict) else {}

        chain = {
            "uncertainty_evidence_ids": resolve(
                causal.get("uncertainty_evidence_ids"), max_items=3, fallback_group_id=gid,
            ),
            "hypothesis_or_rationale_evidence_ids": resolve(
                causal.get("hypothesis_or_rationale_evidence_ids"), max_items=3,
            ),
            "experiment_evidence_ids": resolve(
                causal.get("experiment_evidence_ids"), max_items=3,
            ),
            "result_or_learning_evidence_ids": resolve(
                causal.get("result_or_learning_evidence_ids"), max_items=4,
            ),
        }
        functional = {
            "uncertainty": list(chain["uncertainty_evidence_ids"]),
            "hypothesis": list(chain["hypothesis_or_rationale_evidence_ids"]),
            "experiment": list(chain["experiment_evidence_ids"]),
            "result": list(chain["result_or_learning_evidence_ids"]),
            "learning": [],
        }
        stages_present = {
            "uncertainty": bool(functional["uncertainty"]),
            "hypothesis": bool(functional["hypothesis"]),
            "experiment": bool(functional["experiment"]),
            "result_or_learning": bool(functional["result"]),
        }
        stage_count = sum(1 for value in stages_present.values() if value)
        causal_coherence = {
            "score": round(stage_count / 4.0, 4),
            "stages_present": stages_present,
            "linked_pairs": [],
            "chain_complete": stage_count == 4,
            "mode": "upstream_evidence_ids_no_reranking",
        }

        criteria: List[Dict[str, Any]] = []
        for criterion in _criterion_breakdown_from_assessment(raw):
            explicit_ids = criterion.get("evidence_ids") or []
            proofs = resolve(explicit_ids, max_items=3)
            criteria.append({
                **criterion,
                "reason_fr": clean_text(criterion.get("reason")) or None,
                "evidence": proofs,
            })

        activities_by_status: Dict[str, List[Dict[str, Any]]] = {
            "direct_rnd": [],
            "necessary_rnd_support": [],
            "classical_engineering": [],
            "insufficient_evidence": [],
        }
        for activity in demarche.get("activities") or []:
            if not isinstance(activity, dict):
                continue
            status = clean_text(activity.get("activity_status"))
            if status not in activities_by_status or len(activities_by_status[status]) >= 4:
                continue
            pid = clean_text(activity.get("evidence_id"))
            proof = proof_by_id.get(pid)
            if not isinstance(proof, dict):
                proof = {
                    "evidence_id": pid,
                    "document": clean_text(activity.get("document")),
                    "section_title": clean_text(activity.get("section_title")),
                    "excerpt": truncate(activity.get("text_excerpt"), 700),
                    "role": "methode",
                }
            activities_by_status[status].append(dict(proof))

        operation_status = clean_text(demarche.get("operation_status")) or "insufficient_evidence"
        title = clean_text(
            group.get("text") or group.get("analysis_text") or group.get("section_title")
        )
        anchor = resolve([], max_items=1, fallback_group_id=gid)
        operation_report = {
            "group_id": gid,
            "title": truncate(title, 320) or f"Opération {len(operation_reports) + 1}",
            "technical_scope": clean_text(group.get("technical_scope")),
            "anchor_evidence": anchor[0] if anchor else {},
            "operation_status": operation_status,
            "nlp_operation_status": operation_status,
            "status_review_reason_fr": "Statut repris de l'évaluation NLP/Frascati amont sans reranking dans EnnoDiagnostic.",
            "documentary_coverage": raw.get("documentary_coverage") or raw.get("eligibility_score") or 0.0,
            "rnd_defensibility_index": raw.get("rnd_defensibility_index") or raw.get("eligibility_assessment_score") or 0.0,
            "eligibility_recommendation": raw.get("eligibility_recommendation"),
            "risk_level": clean_text(raw.get("risk_level")),
            "evidence_risk_level": clean_text(raw.get("risk_level")) or "medium",
            "consultant_validation_required": bool(
                operation_status in {"rnd_core_partial", "insufficient_evidence"}
                or not causal_coherence["chain_complete"]
            ),
            "evidence_quality_score": causal_coherence["score"],
            "semantic_link_quality_score": None,
            "criteria": criteria,
            "causal_chain_complete": causal_coherence["chain_complete"],
            "causal_coherence": causal_coherence,
            "causal_chain_evidence": chain,
            "functional_evidence": functional,
            "hypothesis_reconstruction_evidence": functional["hypothesis"],
            "prioritized_result_evidence": functional["result"],
            "activities_by_status": activities_by_status,
        }
        operation_report["justification_fr"] = _operation_justification_fr(operation_report)
        operation_reports.append(operation_report)

    basis = next(
        (item for item in operation_reports if clean_text(item.get("group_id")) == basis_group_id),
        operation_reports[0] if operation_reports else {},
    )
    status_priority = {
        "rnd_core_defendable": 4,
        "rnd_core_partial": 3,
        "insufficient_evidence": 2,
        "classical_engineering": 1,
    }
    reference_operation = max(
        operation_reports,
        key=lambda item: (
            status_priority.get(clean_text(item.get("operation_status")), 0),
            _selection_number(item.get("documentary_coverage")),
        ),
        default=basis,
    )
    reference_group_id = clean_text(reference_operation.get("group_id"))

    try:
        score = round(float(
            assessment.get("rnd_defensibility_index")
            or assessment.get("eligibility_assessment_score")
            or 0.0
        ), 4)
    except Exception:
        score = 0.0
    try:
        documentary_coverage = round(float(assessment.get("documentary_coverage") or 0.0), 4)
    except Exception:
        documentary_coverage = 0.0

    hypothesis = list(reference_operation.get("hypothesis_reconstruction_evidence") or [])
    results = list(reference_operation.get("prioritized_result_evidence") or [])
    return {
        "version": "eligibility_evidence_report_v6_fast_upstream_ids",
        "score": score,
        "documentary_coverage": documentary_coverage,
        "documented_share": documentary_coverage,
        "remaining_documentary_gap": round(max(0.0, 1.0 - documentary_coverage), 4),
        "score_formula": assessment.get("score_formula") or "five_equal_weight_frascati_criteria_20_percent_each",
        "score_basis_group_id": basis_group_id or None,
        "score_basis_operation": basis,
        "reference_operation_group_id": reference_group_id or None,
        "reference_operation": reference_operation,
        "reference_operation_selection_order": [
            "rnd_core_defendable", "rnd_core_partial",
            "insufficient_evidence", "classical_engineering",
        ],
        "operations": operation_reports,
        "hypothesis_reconstruction_evidence": hypothesis,
        "prioritized_result_evidence": results,
        "attachment_policy": {
            "mode": "upstream_evidence_ids_only",
            "semantic_reranking": False,
            "same_document_neighbor_attachment": False,
            "source": "nlp_frascati_precomputed_chain",
        },
        "proof_policy": "reuse_upstream_evidence_ids_no_derived_metric_or_cause",
    }


def _build_eligibility_evidence_report(
    assessment: Dict[str, Any],
    technical_groups: List[Dict[str, Any]],
    additional_passages: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Construit la justification traçable affichée par le frontend.

    Le texte LLM ne décide ni des critères ni du calcul. Cette structure relie
    chaque contribution de 20/10/0 points et chaque maillon causal aux passages
    du NLP qui l'ont déclenché.
    """
    groups_by_id: Dict[str, Dict[str, Any]] = {}
    proof_catalog_by_group: Dict[str, Dict[str, Dict[str, Any]]] = {}
    passage_pool_by_group: Dict[str, List[Dict[str, Any]]] = {}
    additional = _dedupe_nlp_passages(additional_passages or [])

    # Propriété des preuves de base : un passage appartenant explicitement au
    # noyau d'une autre opération ne doit pas fuiter vers l'opération courante.
    passage_owners: Dict[str, set[str]] = {}
    for candidate_group in technical_groups:
        if not isinstance(candidate_group, dict):
            continue
        candidate_group_id = clean_text(candidate_group.get("lock_group_id") or candidate_group.get("passage_id"))
        if not candidate_group_id:
            continue
        for passage in candidate_group.get("supporting_passages") or []:
            if not isinstance(passage, dict):
                continue
            evidence_id = _passage_evidence_id(passage)
            if evidence_id:
                passage_owners.setdefault(evidence_id, set()).add(candidate_group_id)

    for group in technical_groups:
        if not isinstance(group, dict):
            continue
        group_id = clean_text(group.get("lock_group_id") or group.get("passage_id"))
        if not group_id:
            continue
        groups_by_id[group_id] = group
        foreign_owned = {
            evidence_id
            for evidence_id, owners in passage_owners.items()
            if group_id not in owners
        }
        passage_pool = _operation_passage_pool(
            group, additional, foreign_owned_evidence_ids=foreign_owned
        )
        passage_pool_by_group[group_id] = passage_pool
        catalog: Dict[str, Dict[str, Any]] = {}
        for passage in passage_pool:
            proof = _nlp_passage_proof(passage)
            evidence_id = clean_text(proof.get("evidence_id"))
            if evidence_id and proof.get("excerpt") and evidence_id not in catalog:
                catalog[evidence_id] = proof
        proof_catalog_by_group[group_id] = catalog

    raw_assessments = [
        item for item in (assessment.get("group_assessments") or []) if isinstance(item, dict)
    ]
    basis_group_id = clean_text(assessment.get("score_basis_group_id"))
    if not basis_group_id and raw_assessments:
        eligible = [
            item for item in raw_assessments
            if int(item.get("eligibility_recommendation") or 0) == 1
            and clean_text((item.get("demarche_legibility") or {}).get("operation_status"))
            in {"rnd_core_defendable", "rnd_core_partial"}
        ]
        pool = eligible or raw_assessments
        status_priority = {
            "rnd_core_defendable": 4,
            "rnd_core_partial": 3,
            "insufficient_evidence": 2,
            "classical_engineering": 1,
        }
        basis_group_id = clean_text(max(
            pool,
            key=lambda item: (
                status_priority.get(
                    clean_text((item.get("demarche_legibility") or {}).get("operation_status")),
                    0,
                ),
                float(item.get("documentary_coverage") or item.get("eligibility_score") or 0.0),
            ),
        ).get("group_id"))

    operation_reports: List[Dict[str, Any]] = []
    for raw in raw_assessments:
        group_id = clean_text(raw.get("group_id"))
        group = groups_by_id.get(group_id, {})
        catalog = proof_catalog_by_group.get(group_id, {})
        passage_pool = passage_pool_by_group.get(group_id, [])
        demarche = raw.get("demarche_legibility") if isinstance(raw.get("demarche_legibility"), dict) else {}
        breakdown: List[Dict[str, Any]] = []
        for criterion in _criterion_breakdown_from_assessment(raw):
            criterion_key = clean_text(criterion.get("criterion"))
            evidence = _rank_passages_for_purpose(
                passage_pool,
                criterion_key,
                group=group,
                preferred_evidence_ids=criterion.get("evidence_ids") or [],
                max_items=3,
            )
            if not evidence:
                evidence = [
                    catalog[evidence_id]
                    for evidence_id in criterion.get("evidence_ids") or []
                    if evidence_id in catalog
                ][:3]
                for proof in evidence:
                    proof.setdefault("summary_fr", _proof_summary_fr(criterion_key, {}))
            breakdown.append({
                **criterion,
                "reason_fr": _criterion_reason_fr(criterion, evidence),
                "evidence": evidence,
            })

        causal_chain = demarche.get("causal_chain") if isinstance(demarche.get("causal_chain"), dict) else {}
        chain_proofs: Dict[str, List[Dict[str, Any]]] = {}
        for chain_key in (
            "uncertainty_evidence_ids",
            "hypothesis_or_rationale_evidence_ids",
            "experiment_evidence_ids",
            "result_or_learning_evidence_ids",
        ):
            chain_proofs[chain_key] = [
                catalog[evidence_id]
                for evidence_id in (causal_chain.get(chain_key) or [])
                if evidence_id in catalog
            ][:2]

        functional_evidence = {
            "uncertainty": _rank_passages_for_purpose(
                passage_pool, "uncertainty", group=group, max_items=3,
            ),
            "hypothesis": _build_hypothesis_evidence(
                passage_pool, group=group, max_items=4,
            ),
            "experiment": _rank_passages_for_purpose(
                passage_pool, "experiment", group=group, max_items=3,
            ),
            "result": _build_result_evidence(
                passage_pool, group=group, max_items=4,
            ),
            "learning": _rank_passages_for_purpose(
                passage_pool, "learning", group=group, max_items=3,
            ),
        }
        reconstructed_hypothesis = functional_evidence["hypothesis"]
        prioritized_results = functional_evidence["result"]
        if functional_evidence["uncertainty"]:
            chain_proofs["uncertainty_evidence_ids"] = functional_evidence["uncertainty"]
        if reconstructed_hypothesis:
            chain_proofs["hypothesis_or_rationale_evidence_ids"] = reconstructed_hypothesis
        if functional_evidence["experiment"]:
            chain_proofs["experiment_evidence_ids"] = functional_evidence["experiment"]
        result_learning = _dedupe_proofs(
            prioritized_results + functional_evidence["learning"]
        )
        if result_learning:
            chain_proofs["result_or_learning_evidence_ids"] = result_learning[:4]
        causal_coherence = _causal_coherence_report(functional_evidence)
        raw_operation_status = clean_text(demarche.get("operation_status")) or "insufficient_evidence"
        reviewed_operation_status, status_reason_fr = _review_operation_status(
            raw_operation_status,
            causal_coherence,
        )

        activities_by_status: Dict[str, List[Dict[str, Any]]] = {
            "direct_rnd": [],
            "necessary_rnd_support": [],
            "classical_engineering": [],
            "insufficient_evidence": [],
        }
        for activity in demarche.get("activities") or []:
            if not isinstance(activity, dict):
                continue
            status = clean_text(activity.get("activity_status"))
            if status not in activities_by_status or len(activities_by_status[status]) >= 4:
                continue
            evidence_id = clean_text(activity.get("evidence_id"))
            proof = catalog.get(evidence_id) or {
                "evidence_id": evidence_id,
                "document": clean_text(activity.get("document")),
                "source_path": "",
                "page_number": None,
                "section_title": clean_text(activity.get("section_title")),
                "sentence_start": activity.get("sentence_start"),
                "role": "methode",
                "excerpt": truncate(activity.get("text_excerpt"), 700),
            }
            activities_by_status[status].append(proof)

        title = clean_text(
            group.get("text") or group.get("analysis_text") or group.get("section_title")
        )
        anchor_evidence_id = clean_text(group.get("passage_id"))
        anchor_evidence = catalog.get(anchor_evidence_id)
        if anchor_evidence is None and catalog:
            anchor_evidence = next(iter(catalog.values()))
        criteria_with_evidence = sum(1 for item in breakdown if item.get("evidence"))
        semantic_scores = [
            float((proof.get("semantic_link") or {}).get("score") or 0.0)
            for proofs in functional_evidence.values()
            for proof in proofs
            if isinstance(proof, dict) and isinstance(proof.get("semantic_link"), dict)
        ]
        semantic_link_quality_score = round(
            sum(semantic_scores) / len(semantic_scores) if semantic_scores else 0.0,
            4,
        )
        evidence_quality_score = round(
            0.5 * float(causal_coherence.get("score") or 0.0)
            + 0.25 * (criteria_with_evidence / max(len(breakdown), 1))
            + 0.25 * semantic_link_quality_score,
            4,
        )
        consultant_validation_required = bool(
            reviewed_operation_status in {"rnd_core_partial", "insufficient_evidence"}
            or not causal_coherence.get("chain_complete")
            or any(item.get("status") != "documented" for item in breakdown)
        )
        operation_report = {
            "group_id": group_id,
            "title": truncate(title, 320) or f"Opération {len(operation_reports) + 1}",
            "technical_scope": clean_text(group.get("technical_scope")),
            "anchor_evidence": anchor_evidence or {},
            "operation_status": reviewed_operation_status,
            "nlp_operation_status": raw_operation_status,
            "status_review_reason_fr": status_reason_fr,
            "documentary_coverage": raw.get("documentary_coverage") or raw.get("eligibility_score") or 0.0,
            "rnd_defensibility_index": raw.get("rnd_defensibility_index") or raw.get("eligibility_assessment_score") or 0.0,
            "eligibility_recommendation": raw.get("eligibility_recommendation"),
            "risk_level": clean_text(raw.get("risk_level")),
            "evidence_risk_level": "high" if reviewed_operation_status == "insufficient_evidence" else (
                "medium" if consultant_validation_required else clean_text(raw.get("risk_level")) or "low"
            ),
            "consultant_validation_required": consultant_validation_required,
            "evidence_quality_score": evidence_quality_score,
            "semantic_link_quality_score": semantic_link_quality_score,
            "criteria": breakdown,
            "causal_chain_complete": bool(causal_coherence.get("chain_complete")),
            "causal_coherence": causal_coherence,
            "causal_chain_evidence": chain_proofs,
            "functional_evidence": functional_evidence,
            "hypothesis_reconstruction_evidence": reconstructed_hypothesis,
            "prioritized_result_evidence": prioritized_results,
            "activities_by_status": activities_by_status,
        }
        operation_report["justification_fr"] = _operation_justification_fr(operation_report)
        operation_reports.append(operation_report)

    basis = next(
        (item for item in operation_reports if item.get("group_id") == basis_group_id),
        operation_reports[0] if operation_reports else {},
    )
    status_priority = {
        "rnd_core_defendable": 4,
        "rnd_core_partial": 3,
        "insufficient_evidence": 2,
        "classical_engineering": 1,
    }
    reference_operation = max(
        operation_reports,
        key=lambda item: (
            status_priority.get(clean_text(item.get("operation_status")), 0),
            _selection_number(item.get("documentary_coverage")),
            _selection_number(item.get("evidence_quality_score")),
        ),
        default={},
    )
    reference_group_id = clean_text(reference_operation.get("group_id"))
    try:
        score = round(float(assessment.get("rnd_defensibility_index") or assessment.get("eligibility_assessment_score") or 0.0), 4)
    except Exception:
        score = 0.0
    try:
        documentary_coverage = round(float(assessment.get("documentary_coverage") or 0.0), 4)
    except Exception:
        documentary_coverage = 0.0
    reference_passage_pool = passage_pool_by_group.get(reference_group_id, [])
    project_hypothesis_evidence = _build_hypothesis_evidence(
        reference_passage_pool,
        group=groups_by_id.get(reference_group_id),
        max_items=5,
    )
    prioritized_result_evidence = _build_result_evidence(
        reference_passage_pool,
        group=groups_by_id.get(reference_group_id),
        max_items=6,
    )
    if reference_operation and project_hypothesis_evidence:
        reference_operation["hypothesis_reconstruction_evidence"] = project_hypothesis_evidence
        chain = reference_operation.get("causal_chain_evidence") if isinstance(reference_operation.get("causal_chain_evidence"), dict) else {}
        chain["hypothesis_or_rationale_evidence_ids"] = project_hypothesis_evidence
        reference_operation["causal_chain_evidence"] = chain
    if reference_operation and prioritized_result_evidence:
        reference_operation["prioritized_result_evidence"] = prioritized_result_evidence
        chain = reference_operation.get("causal_chain_evidence") if isinstance(reference_operation.get("causal_chain_evidence"), dict) else {}
        chain["result_or_learning_evidence_ids"] = prioritized_result_evidence
        reference_operation["causal_chain_evidence"] = chain
    return {
        "version": "eligibility_evidence_report_v5_separate_coverage_and_rnd_defensibility",
        "score": score,
        "documentary_coverage": documentary_coverage,
        "documented_share": documentary_coverage,
        "remaining_documentary_gap": round(max(0.0, 1.0 - documentary_coverage), 4),
        "score_formula": assessment.get("score_formula") or "five_equal_weight_frascati_criteria_20_percent_each",
        "score_basis_group_id": basis_group_id or None,
        "score_basis_operation": basis,
        "reference_operation_group_id": reference_group_id or None,
        "reference_operation": reference_operation,
        "reference_operation_selection_order": [
            "rnd_core_defendable",
            "rnd_core_partial",
            "insufficient_evidence",
            "classical_engineering",
        ],
        "operations": operation_reports,
        "hypothesis_reconstruction_evidence": project_hypothesis_evidence,
        "prioritized_result_evidence": prioritized_result_evidence,
        "attachment_policy": {
            "roles_allowed": ["objectif", "contribution", "parametre", "verrou", "methode", "resultat"],
            "text_fields_used": ["text", "analysis_text", "context_before", "context_after"],
            "location_fields_used": [
                "document_id", "document", "section_title", "section_path",
                "sentence_start", "page_number", "passage_id",
            ],
            "same_document_neighbor_attachment": True,
            "ranking_functions": ["uncertainty", "hypothesis", "experiment", "result", "learning"],
            "ranking_frascati": ["novelty", "creativity", "systematicity", "transferability"],
            "result_priority": [
                "role_resultat", "results_or_conclusion_section", "quantitative_metrics",
                "observation_or_conclusion_wording",
            ],
            "result_metadata_excluded": True,
            "systematicity_state_of_art_only_penalized": True,
            "no_role_only_decision": True,
        },
        "proof_policy": "each_numeric_or_causal_project_claim_must_reference_source_evidence_no_derived_metric_or_cause",
    }


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
        elif clean_text(meta.get("source_type")) == "nlp_result_current_project":
            # Le pack NLP du projet/année courants est la source documentaire
            # la plus directe pour les petites sections du diagnostic.
            value += 35.0
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
    if clean_text(meta.get("source_type")) == "nlp_result_current_project":
        score += 6.0
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
    normalized_blob = _diag_norm(blob)
    if any(marker in normalized_blob for marker in ("but de cette partie", "objectif de cette partie", "sensibilite du parametrage")):
        score -= 18.0
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
    """Construit du contexte d'explication sans réinjecter Chroma dans l'objectif.

    V5.3 : la liste des verrous et son contexte restent STRICTEMENT inchangés.
    La correction porte uniquement sur les sections factuelles. En mode projet
    courant, ``sections["objectifs"]`` a déjà été nettoyé par le pack NLP :
    une recherche Chroma large ne doit plus l'écraser avec des méthodes,
    résultats ou contenus de littérature.

    La recherche large des verrous est conservée telle quelle et ne devient
    jamais une source primaire de verrou.
    """
    if not isinstance(sections, dict):
        return sections

    current_project_only = str(
        os.getenv("ENNOSMART_DIAG_CURRENT_PROJECT_ONLY", "1")
    ).strip().lower() not in {"0", "false", "no", "off"}

    # V5.6 — équilibrage narratif local, sans Chroma et sans aucune mutation
    # de la liste des verrous. Il récupère les faits cross-role réellement
    # attribuables au projet et diversifie les familles de démarche/résultat.
    if current_project_only:
        try:
            try:
                from agents.EnnoDiagnostic.narrative_evidence_balancer import balance_narrative_sections
            except Exception:
                from narrative_evidence_balancer import balance_narrative_sections
            sections = balance_narrative_sections(agent, sections)
            report = sections.get("_narrative_balance_report") or {}
            print(
                "[EnnoDiagnostic][NARRATIVE_BALANCE_V56] "
                f"objective_context={report.get('objective_context_companions', 0)} "
                f"methodes={report.get('method_facts_balanced', 0)} "
                f"resultats={report.get('result_facts_balanced', 0)} "
                f"parametres={report.get('parameter_facts_balanced', 0)} "
                "chroma=0 locks_unchanged=true",
                flush=True,
            )
        except Exception as balance_exc:
            print(
                f"[EnnoDiagnostic][NARRATIVE_BALANCE_V56][WARN] fallback=v55 error={balance_exc}",
                flush=True,
            )

    # OBJECTIF : en mode sûr, ne jamais refaire une recherche large Chroma.
    # L'objectif officiel reste le pack NLP courant déjà filtré. Le contexte
    # additionnel n'est qu'une vue auxiliaire et ne remplace jamais la section.
    if current_project_only:
        objective_broad: List[Dict[str, Any]] = []
        objective_pool = list(sections.get("objectifs") or [])
    else:
        objective_broad = agent.search_chroma(
            role=None,
            query="but objectif évaluer qualifier valider démontrer comparer capacité besoin finalité technique résultats attendus",
            top_k=22,
        )
        objective_pool = []
        for key in ["objectifs", "global", "methodes", "resultats", "limites", "contributions", "axe_preuves_resultats"]:
            value = sections.get(key)
            if isinstance(value, list):
                objective_pool.extend(value)
        objective_pool.extend(objective_broad)

    # VERROUS : en mode projet courant, la liste primaire a déjà été construite
    # depuis les groupes NLP + récupération stricte. Une recherche Chroma large
    # ici n'ajoute aucun verrou officiel et coûtait plusieurs secondes/minutes.
    # Elle reste disponible uniquement hors mode sûr.
    if current_project_only:
        lock_broad: List[Dict[str, Any]] = []
    else:
        lock_broad = agent.search_chroma(
            role=None,
            query="incertitude technique limite difficulté non démontré à vérifier représentativité transposabilité généralisation robustesse influence paramètres conditions réelles",
            top_k=22,
        )

    lock_pool: List[Dict[str, Any]] = []
    for key in ["verrous", "limites", "methodes", "resultats", "parametres", "axe_problemes_transverses", "axe_contraintes_transverses"]:
        value = sections.get(key)
        if isinstance(value, list):
            lock_pool.extend(value)
    lock_pool.extend(lock_broad)

    objective_context = _diag_rank_sources(objective_pool, _diag_objective_score, max_items=10)
    lock_context = _diag_rank_sources(lock_pool, _diag_lock_score, max_items=14)

    sections["objectif_agent_context"] = objective_context
    sections["verrou_agent_context"] = _diag_dedupe_sources(lock_context, max_items=14)

    if not current_project_only:
        sections["objectifs"] = _diag_dedupe_sources(objective_context, max_items=10)

    sections["_agent_selection_report"] = {
        "version": "v156_narrative_balance_lock_path_unchanged",
        "principle": (
            "L'objectif courant reste l'autorité NLP filtrée. La recherche large "
            "ne remplace plus l'objectif. Les candidats de verrou restent issus "
            "du chemin V5.2 strict, inchangé par ce correctif."
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



_GENERIC_VISIBLE_LOCK_TITLE_RE = re.compile(
    r"^(?:incertitude technique a preciser avant validation cir|"
    r"signal technique a reformuler(?: avant validation cir)?|"
    r"verrou technique a preciser|verrou a preciser)$",
    re.I,
)


def _polish_visible_lock_titles_without_regrouping(
    items: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Améliore uniquement le titre visible, sans toucher aux groupes/sources.

    Invariants :
    - même nombre d'items ;
    - mêmes group_id / member_group_ids / original_nlp_group_ids ;
    - aucune fusion, suppression ou création de verrou.
    """
    output: List[Dict[str, Any]] = []
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        title = clean_text(item.get("title"), 260)
        title_norm = _diag_norm(title)

        # Répare seulement quelques défauts de surface, sans changer le sens.
        title = re.sub(
            r"^Incertitude sur incertaines?\s+",
            "Incertitude sur les ",
            title,
            flags=re.I,
        )
        title = re.sub(
            r"^Incertitude sur les les\s+",
            "Incertitude sur les ",
            title,
            flags=re.I,
        )

        if not title or _GENERIC_VISIBLE_LOCK_TITLE_RE.match(title_norm):
            candidate = ""
            sources = item.get("sources") or []
            if isinstance(sources, dict):
                sources = [sources]
            for source in sources if isinstance(sources, list) else []:
                if not isinstance(source, dict):
                    continue
                text = clean_text(
                    source.get("text")
                    or source.get("excerpt")
                    or source.get("analysis_text"),
                    700,
                )
                text = re.sub(
                    r"^\s*\[?(?:SECTION\s*:\s*)?Verrous?\s+scientifiques?\s+ou\s+techniques?\]?\s*",
                    "",
                    text,
                    flags=re.I,
                )
                text = re.sub(
                    r"^\s*Verrous?\s+scientifiques?\s+ou\s+techniques?\]?\s*",
                    "",
                    text,
                    flags=re.I,
                )
                first = clean_text(
                    re.split(r"(?<=[.!?;])\s+|\n+", text, maxsplit=1)[0],
                    220,
                ).strip(" :-–—|[]")
                if len(first) >= 24:
                    candidate = first
                    break
            if candidate:
                title = candidate

        if title:
            item["title"] = clean_text(title, 220)
        output.append(item)
    return output


# =========================================================
# Préflight local N-1 rapide
# =========================================================

def _local_previous_year_directories(year_root: Path, current_year: Any) -> List[str]:
    """Retourne les années locales antérieures du même projet en quelques ms.

    ``year_root`` pointe vers ``.../years/<N>``. Si aucun autre dossier d'année
    antérieure n'existe sous ``years/``, il est inutile d'appeler les lecteurs
    CIR_MEMORY beaucoup plus coûteux pour conclure qu'il n'y a pas de N-1.
    """
    try:
        current = int(str(current_year).strip())
    except Exception:
        return []
    try:
        resolved_year_root = Path(year_root).resolve()
        years_root = resolved_year_root.parent
        # Hors de la structure canonique ``.../years/<N>``, le préflight local
        # n'est pas une preuve d'absence. On renvoie un marqueur non vide afin
        # de conserver le chemin CIR_MEMORY complet.
        if years_root.name.lower() != "years":
            return ["unknown"]
        candidates: List[int] = []
        for child in years_root.iterdir():
            if not child.is_dir():
                continue
            try:
                value = int(child.name)
            except Exception:
                continue
            if value < current:
                candidates.append(value)
        return [str(value) for value in sorted(set(candidates), reverse=True)]
    except Exception:
        return []


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
        subproject: Optional[str] = None,
        subproject_id: Optional[str] = None,
        **kwargs,
    ):
        self.organisme = organisme_id or organisme or "unknown_organisme"
        self.project = project_id or project or "unknown_project"
        self.subproject = subproject_id or subproject or ""
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
            self.out_dir = _resolve_ennosmart_year_root(
                self.organisme,
                self.project,
                self.year,
                self.subproject,
            )

        self.diagnostic_dir = self.out_dir / "ennodiagnostic"
        self.report_path = self.diagnostic_dir / "ennodiagnostic_report.json"

        from modules.RAG.retriever import EnnoRetriever

        self.retriever = EnnoRetriever(
            organisme=self.organisme,
            project=self.project,
            subproject=self.subproject,
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
            "explicit_lock_section": bool(
                re.search(
                    r"\b(?:verrous? scientifiques?|verrous? techniques?|"
                    r"incertitudes? scientifiques?|incertitudes? techniques?)\b",
                    _diag_norm(" ".join(
                        str(item.get("section_title") or "") for item in ([group] + supports[:8])
                    )),
                    flags=re.I,
                )
            ),
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

    def _nlp_pack_item_to_source(
        self,
        item: Dict[str, Any],
        *,
        pack_key: str,
        role: str,
    ) -> Optional[Dict[str, Any]]:
        """Convertit une preuve du NLP COURANT en source EnnoDiagnostic.

        Aucun autre projet ni mémoire n'est consulté ici. ``analysis_text`` est
        privilégié car il restaure le contexte local autour des fragments NLP
        (par exemple le nom réel d'un angle juste avant sa valeur).
        """
        if not isinstance(item, dict):
            return None
        raw = clean_text(item.get("text"))
        analysis = clean_text(item.get("analysis_text"))
        before = clean_text(item.get("context_before"))
        after = clean_text(item.get("context_after"))
        contextual = analysis or clean_text(" ".join(x for x in (before, raw, after) if x)) or raw
        if len(contextual) < 35:
            return None

        # Écarter seulement le bruit documentaire évident, sans règle métier.
        normalized = _diag_norm(contextual)
        noise_markers = (
            "table des matieres", "documents applicables remis",
            "documents de reference remis", "personne fonction organisme",
        )
        if any(marker in normalized for marker in noise_markers) and len(contextual) < 700:
            return None

        try:
            rank_score = float(item.get("rank_score") or 0.0)
        except Exception:
            rank_score = 0.0
        try:
            confidence = float(item.get("confidence") or 0.0)
        except Exception:
            confidence = 0.0

        meta = {
            "passage_id": clean_text(item.get("passage_id")),
            "document": clean_text(item.get("document")),
            "document_id": clean_text(item.get("document_id")),
            "source_path": clean_text(item.get("source_path")),
            "source_type": "nlp_result_current_project",
            "content_origin": clean_text(item.get("content_origin")) or "project_core",
            "document_type": clean_text(item.get("document_type")),
            "document_category": clean_text(item.get("document_category")),
            "pack_key": pack_key,
            "role": clean_text(item.get("role")) or role,
            "section_title": clean_text(item.get("section_title")),
            "section_path": item.get("section_path"),
            "sentence_start": item.get("sentence_start"),
            "page_number": item.get("page_number"),
            "rank_score": rank_score + 0.35,
            "confidence": max(confidence, 0.72),
            "analysis_text": contextual,
            "context_before": before,
            "context_after": after,
            "source_text_original": raw,
            "current_project_only": True,
            "diagnostic_corpus_selected": bool(item.get("diagnostic_corpus_selected", True)),
            "declared_corpus": clean_text(item.get("declared_corpus")) or "diagnostic_current",
            "semantic_role": clean_text(item.get("semantic_role") or item.get("role") or role),
            "original_model_role": clean_text(item.get("original_model_role") or item.get("role") or role),
            "semantic_role_conflicts": item.get("semantic_role_conflicts") or [],
            "reference_like": bool(item.get("reference_like")),
            "evidence_origin": clean_text(item.get("evidence_origin")),
            "transcription_like": bool(item.get("transcription_like")),
            "unverified_transcription_numeric": bool(item.get("unverified_transcription_numeric")),
            "numeric_corroborated": bool(item.get("numeric_corroborated") or item.get("corroborated_numeric")),
            "execution_status": clean_text(item.get("execution_status")),
            "actor_scope": clean_text(item.get("actor_scope")),
        }
        return {
            "text": contextual,
            "analysis_text": contextual,
            "context_before": before,
            "context_after": after,
            "document": meta["document"],
            "source_path": meta["source_path"],
            "metadata": meta,
        }

    def _load_current_nlp_evidence_sections(self) -> Dict[str, List[Dict[str, Any]]]:
        """Charge le pack NLP courant avec résolution de provenance inter-rôles.

        Le même corpus peut contenir travaux projet + état de l'art. Le rôle NLP
        décrit la fonction sémantique, pas l'acteur. Avant toute restitution, on
        marque comme ``reference_like`` les passages explicitement bibliographiques
        ou fortement recouvrants avec ``etat_art_local``. Ils ne peuvent ensuite
        plus devenir objectif, démarche, résultat ou paramètre du projet courant.
        """
        path = self._find_current_nlp_result_path()
        empty = {key: [] for key in ("objectifs", "methodes", "resultats", "parametres", "limites", "contributions")}
        if path is None or not path.exists():
            return empty
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            print(f"[EnnoDiagnostic][CURRENT_NLP_PACK][WARN] lecture impossible: {exc}")
            return empty
        pack = payload.get("multi_document_evidence_pack_for_ennodiagnostic") or {}
        if not isinstance(pack, dict):
            return empty

        def ntext(value: Any) -> str:
            return _diag_norm(value)

        def item_blob(item: Dict[str, Any]) -> str:
            return clean_text(" ".join(
                str(item.get(key) or "")
                for key in ("section_title", "context_before", "text", "analysis_text", "context_after")
            ), 12000)

        external_phrase_re = re.compile(
            r"\b(?:dans (?:le|l['’]?) papier|the paper|les auteurs?|the authors?|"
            r"selon (?:les auteurs?|l['’]?etude|l['’]?article)|"
            r"l['’]?etude a (?:inclus|porte|teste|evalue)|une etude empirique|"
            r"analyse de \d+ (?:papers?|articles?|publications?)|"
            r"revue (?:de la litterature|bibliographique)|survey|systematic review|"
            r"certaines etudes (?:montrent|suggerent|indiquent)|"
            r"des etudes (?:montrent|suggerent|indiquent)|"
            r"ils ont (?:utilise|cree|entraine|propose|compare|evalue|configure)|"
            r"une approche .{0,120}(?:a ete|est) proposee|"
            r"les resultats montrent que .{0,220}\b(?:couramment|frequemment)\b|"
            r"travaux anterieurs|et al\.)\b",
            re.I,
        )
        state_art_section_re = re.compile(
            r"\b(?:etat de l art|state of the art|related work|revue de la litterature|"
            r"bibliograph|references?|l etude a inclus \d+ articles?|"
            r"analyse de \d+ (?:papers?|articles?|publications?)|survey)\b",
            re.I,
        )
        project_anchor_re = re.compile(
            r"\b(?:dans ce projet|dans le cadre du projet|notre (?:projet|demarche|prototype|benchmark)|"
            r"nous avons|nous on a|on a (?:teste|evalue|mesure|genere|developpe|compare|utilise|fait)|"
            r"l equipe a (?:teste|evalue|mesure|genere|developpe|compare|utilise|realise))\b",
            re.I,
        )

        def sentence_token_sets(value: Any) -> List[set[str]]:
            raw = ntext(value)
            rows: List[set[str]] = []
            for sentence in re.split(r"(?<=[.!?;])\s+|[\r\n]+", raw):
                tokens = {
                    tok for tok in sentence.split()
                    if len(tok) >= 4 and not tok.isdigit()
                }
                if len(tokens) >= 9:
                    rows.append(tokens)
            return rows

        # ``etat_art_local`` peut lui-même contenir quelques passages de réunion.
        # On ne construit donc des empreintes externes qu'avec les lignes qui
        # portent un signal bibliographique/tiers explicite.
        state_art_sentences: List[set[str]] = []
        for ref in pack.get("etat_art_local") or []:
            if not isinstance(ref, dict):
                continue
            ref_blob = item_blob(ref)
            ref_norm = ntext(ref_blob)
            ref_section = ntext(ref.get("section_title"))
            if not (
                state_art_section_re.search(ref_section)
                or external_phrase_re.search(ref_norm)
            ):
                continue
            state_art_sentences.extend(sentence_token_sets(ref_blob))

        def repeated_state_art_sentence(blob_raw: str) -> float:
            best = 0.0
            for tokens in sentence_token_sets(blob_raw):
                for ref_tokens in state_art_sentences:
                    shared = len(tokens & ref_tokens)
                    if shared < 8:
                        continue
                    containment = shared / max(1, min(len(tokens), len(ref_tokens)))
                    best = max(best, containment)
                    if best >= 0.82:
                        return best
            return best

        def is_reference_like(item: Dict[str, Any]) -> Tuple[bool, List[str]]:
            blob_raw = item_blob(item)
            blob = ntext(blob_raw)
            section = ntext(item.get("section_title"))
            reasons: List[str] = []
            strong_project_anchor = bool(project_anchor_re.search(blob))

            if state_art_section_re.search(section):
                reasons.append("state_of_art_section")
            if external_phrase_re.search(blob):
                reasons.append("external_attribution_or_survey_language")

            repeated = repeated_state_art_sentence(blob_raw)
            if repeated >= 0.82 and not strong_project_anchor:
                reasons.append(f"repeated_state_of_art_sentence:{repeated:.2f}")

            # Une généralisation de revue (« les résultats montrent que les X
            # sont couramment utilisés... ») n'est jamais un résultat du projet
            # si aucune action de l'équipe n'est formulée dans le même passage.
            if (
                re.search(r"\bles resultats montrent que\b", blob, flags=re.I)
                and re.search(r"\b(?:couramment|frequemment|dans les etudes)\b", blob, flags=re.I)
                and not strong_project_anchor
            ):
                reasons.append("generic_literature_result_without_project_actor")

            # Les supporting_passages d'un groupe peuvent révéler que la phrase
            # a été propagée depuis une section bibliographique même si le titre
            # du passage principal a été perdu lors du regroupement.
            external_supports = 0
            support_total = 0
            for support in item.get("supporting_passages") or []:
                if not isinstance(support, dict):
                    continue
                support_total += 1
                support_blob = ntext(" ".join([
                    str(support.get("section_title") or ""),
                    str(support.get("text") or support.get("excerpt") or ""),
                ]))
                if state_art_section_re.search(ntext(support.get("section_title"))) or external_phrase_re.search(support_blob):
                    external_supports += 1
            if support_total >= 2 and external_supports >= max(1, support_total // 2) and not strong_project_anchor:
                reasons.append("majority_supporting_passages_external")

            return bool(reasons), reasons

        # Les nombres prononcés dans une transcription sont conservés comme
        # preuve brute, mais ils ne sont pas publiés comme paramètres techniques
        # tant qu'une deuxième source non-transcrite du projet ne les corrobore.
        # Cela évite de transformer une erreur de transcription en valeur projet.
        number_re = re.compile(r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)?")
        corroborating_numeric_blobs: List[str] = []
        for pack_key in ("parametres_locaux", "methodes_locales", "resultats_locaux", "limites_locales", "contributions_locales"):
            for row in pack.get(pack_key) or []:
                if not isinstance(row, dict):
                    continue
                document_name = clean_text(row.get("document")).lower()
                if "transcription" in document_name or "enregistrement" in document_name:
                    continue
                row_blob = item_blob(row)
                row_norm = ntext(row_blob)
                if is_reference_like(row)[0]:
                    continue
                if project_anchor_re.search(row_norm) or re.search(
                    r"\b(?:travaux realises|resultats des experimentations|description des experimentations|"
                    r"configuration du projet|protocole experimental)\b",
                    row_norm,
                    flags=re.I,
                ):
                    corroborating_numeric_blobs.append(row_norm)

        def numeric_transcription_is_corroborated(item: Dict[str, Any]) -> bool:
            document_name = clean_text(item.get("document")).lower()
            if "transcription" not in document_name and "enregistrement" not in document_name:
                return True
            blob = ntext(item_blob(item))
            values = {
                token.replace(",", ".")
                for token in number_re.findall(blob)
            }
            if not values:
                return True
            for value in values:
                pattern = rf"(?<![a-z0-9]){re.escape(value)}(?![a-z0-9])"
                if any(re.search(pattern, candidate) for candidate in corroborating_numeric_blobs):
                    return True
            return False

        mapping = {
            "objectifs": ("objectifs_locaux", "objectif"),
            "methodes": ("methodes_locales", "methode"),
            "resultats": ("resultats_locaux", "resultat"),
            "parametres": ("parametres_locaux", "parametre"),
            "limites": ("limites_locales", "limite"),
            "contributions": ("contributions_locales", "contribution"),
        }
        output: Dict[str, List[Dict[str, Any]]] = {}
        externalized = 0
        for section_key, (pack_key, role) in mapping.items():
            converted: List[Dict[str, Any]] = []
            for raw_item in pack.get(pack_key) or []:
                if not isinstance(raw_item, dict):
                    continue
                item = dict(raw_item)
                reference_like, reasons = is_reference_like(item)
                if reference_like:
                    item["reference_like"] = True
                    item["evidence_origin"] = "external_literature"
                    item["semantic_role_conflicts"] = list(dict.fromkeys([
                        *(item.get("semantic_role_conflicts") or []), "etat_art"
                    ]))
                    item["provenance_resolution_reasons"] = reasons
                    externalized += 1
                    # Les rôles factuels du projet ne doivent jamais être alimentés
                    # par un passage résolu comme état de l'art.
                    if section_key in {"objectifs", "methodes", "resultats", "parametres", "contributions"}:
                        continue
                document_name = clean_text(item.get("document")).lower()
                item["transcription_like"] = "transcription" in document_name or "enregistrement" in document_name
                if (
                    section_key == "parametres"
                    and item["transcription_like"]
                    and not numeric_transcription_is_corroborated(item)
                ):
                    item["unverified_transcription_numeric"] = True
                    continue
                source = self._nlp_pack_item_to_source(item, pack_key=pack_key, role=role)
                if source is not None:
                    converted.append(source)

            # V5.4 — filtrer AVANT la troncature. En V5.3, les vraies méthodes
            # et vrais résultats du projet pouvaient se trouver après le top-30
            # brut et disparaissaient avant même que le gate de provenance ne les
            # voie. Le gate ne détecte ni ne regroupe les verrous : il ne concerne
            # que les sections narratives factuelles.
            gate_section = {
                "objectifs": "objectif_global",
                "methodes": "demarche_detectee",
                "resultats": "resultats_metriques",
                "parametres": "parametres_contraintes",
            }.get(section_key)
            if gate_section:
                try:
                    try:
                        from agents.EnnoDiagnostic.project_fact_gate import filter_project_facts
                    except Exception:
                        from project_fact_gate import filter_project_facts
                    converted = filter_project_facts(converted, gate_section)
                except Exception as gate_exc:
                    print(
                        f"[EnnoDiagnostic][PROJECT_FACT_GATE][WARN] section={section_key} "
                        f"fallback=provenance_only error={gate_exc}",
                        flush=True,
                    )

            # Après filtrage, conserver l'ensemble des faits qualifiés (dans une
            # limite large de sécurité) : ils sont déjà peu nombreux et cette
            # étape ne doit plus évincer les vrais éléments situés tard dans le pack.
            output[section_key] = merge_ranked_sources(converted, max_items=60)

        self._current_nlp_payload_for_diagnostic = payload
        print(
            "[EnnoDiagnostic][CURRENT_NLP_PACK] "
            + " ".join(f"{key}={len(value)}" for key, value in output.items())
            + f" externalized={externalized} source={path}"
        )
        return output

    def _load_recovered_missing_lock_candidates(
        self,
        *,
        existing_lock_sources: Sequence[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Récupère prudemment une incertitude oubliée par les groupes NLP.

        Ce chemin reste aval et ne modifie ni le classifieur ni le regroupement
        principal. Il accepte deux formes génériques : une contrainte forte
        explicite, ou un résultat expérimental qui combine échec/non-résolution,
        investigation et compromis persistant. Une preuve proche d'un verrou NLP
        existant est reconnue comme doublon ; seule une incertitude conceptuellement
        nouvelle devient un candidat distinct, toujours soumis à validation humaine.
        """
        payload = getattr(self, "_current_nlp_payload_for_diagnostic", None)
        if not isinstance(payload, dict):
            try:
                path = self._find_current_nlp_result_path()
                payload = json.loads(path.read_text(encoding="utf-8-sig")) if path and path.exists() else {}
            except Exception:
                payload = {}
        pack = payload.get("multi_document_evidence_pack_for_ennodiagnostic") or {}
        catalog = pack.get("evidence_catalog") or []
        if not isinstance(catalog, list):
            return []

        explicit_constraint_re = re.compile(
            r"\b(?:forte contrainte|contrainte (?:forte|majeure|non negociable)|"
            r"exigence non negociable|obligation technique|restriction forte)\b",
            re.I,
        )
        unresolved_re = re.compile(
            r"\b(?:n est pas possible|ne (?:peut|peuvent|permet|permettent) pas|"
            r"n (?:a|ont) pas (?:permis|pu|ete (?:trouve|trouvee|trouves|trouvees|"
            r"valide|validee|valides|validees|correle|correlee|correles|correlees))|"
            r"impossible|non (?:resolu|resolue|maitrise|maitrisee|explique|expliquee|"
            r"valide|validee)|reste(?:nt)? a (?:comprendre|demontrer|expliquer|valider|"
            r"determiner|identifier|explorer)|incertitude|resultats? (?:ne sont|n est) pas "
            r"(?:bon|bons|bonne|bonnes|satisfaisant|satisfaisants|satisfaisante|satisfaisantes)|"
            r"(?:seul|seuls|seule|seules) .{0,70}(?:correspond|converge|satisfait|valide)|"
            r"pas (?:tous|toutes) .{0,70}(?:correle|correlee|correles|correlees|"
            r"trouve|trouvee|trouves|trouvees|valide|validee|valides|validees))\b",
            re.I,
        )
        investigation_re = re.compile(
            r"\b(?:essais?|tests?|simulations?|mesures?|comparaisons?|variations?|"
            r"configurations?|scenarios?|hypotheses?|modelis(?:er|ation|e|ee|es)|"
            r"analys(?:e|er|es)|recal(?:age|er|e|ee)|faire varier|ont ete testes?|"
            r"a ete teste|ont ete comparees?|a ete comparee?)\b",
            re.I,
        )
        discriminating_re = re.compile(
            r"\b(?:bien que|malgre|cependant|toutefois|alors que|tandis que|"
            r"plusieurs (?:essais?|tests?|simulations?|configurations?)|"
            r"d autres pistes|d autres conditions|"
            r"(?:augmente|amelior\w*|reduit|diminue) .{0,140}\bmais\b|"
            r"\bmais\b .{0,140}(?:augmente|degrade|diminue|reste|n est pas|ne peut pas))\b",
            re.I,
        )
        project_anchor_re = re.compile(
            r"\b(?:nous|notre|nos|l equipe|dans ce projet|dans le cadre du projet|on)\b",
            re.I,
        )
        external_re = re.compile(
            r"\b(?:etat de l art|dans le papier|the paper|les auteurs|the authors|selon l etude|"
            r"selon l article|survey|revue de la litterature|analyse de \d+ (?:papers?|articles?))\b",
            re.I,
        )
        noise_re = re.compile(
            r"\b(?:table des matieres|diffusion|masque des diapositives|code couleur|"
            r"titre du verrou|est ce qu on peut commencer)\b",
            re.I,
        )

        concept_stopwords = {
            "alors", "apres", "aussi", "autres", "avoir", "avec", "bien", "cela",
            "cette", "comme", "concernant", "dans", "depuis", "donc", "entre", "essai",
            "essais", "etre", "etude", "faire", "fait", "fois", "lorsque", "mais",
            "meme", "moins", "plus", "plusieurs", "pour", "premier", "realise",
            "realisee", "realises", "resultat", "resultats", "selon", "seule", "seuls",
            "sont", "sous", "suite", "toutes", "toute", "tous", "tres", "ainsi",
        }

        def concept_tokens(value: Any) -> set[str]:
            return {
                token
                for token in re.findall(r"[a-z0-9]+", _diag_norm(value))
                if len(token) >= 4 and not token.isdigit() and token not in concept_stopwords
            }

        def is_current_project_evidence(raw: Dict[str, Any]) -> bool:
            declared_corpus = _diag_norm(raw.get("declared_corpus"))
            origin = _diag_norm(raw.get("content_origin") or raw.get("source_type"))
            return bool(
                raw.get("current_project_evidence")
                or raw.get("declared_raw_document")
                or raw.get("diagnostic_corpus_selected")
                or "diagnostic" in declared_corpus
                or origin in {"raw client document", "project core", "ambiguous current dossier"}
            )

        def signal_report(raw: Dict[str, Any], normalized: str) -> Dict[str, Any]:
            features = raw.get("lock_candidate_features") or {}
            if not isinstance(features, dict):
                features = {}
            role = _diag_norm(raw.get("semantic_role") or raw.get("role"))
            explicit = bool(explicit_constraint_re.search(normalized))
            unresolved = bool(unresolved_re.search(normalized))
            investigation = bool(investigation_re.search(normalized))
            discriminating = bool(discriminating_re.search(normalized))
            technical = bool(
                features.get("technical")
                or role in {"verrou", "limite", "resultat", "methode", "parametre", "contribution"}
            )
            implicit = bool(unresolved and investigation and discriminating and technical)
            score = (
                (5 if explicit else 0)
                + (3 if unresolved else 0)
                + (2 if investigation else 0)
                + (2 if discriminating else 0)
                + (1 if technical else 0)
            )
            return {
                "explicit_constraint": explicit,
                "unresolved_outcome": unresolved,
                "investigation": investigation,
                "discriminating_or_persistent": discriminating,
                "technical": technical,
                "implicit_experimental_uncertainty": implicit,
                "score": score,
            }

        existing_profiles: List[Tuple[Dict[str, Any], set[str], set[str]]] = []
        existing_passage_ids: set[str] = set()
        for source in existing_lock_sources:
            if not isinstance(source, dict):
                continue
            meta = meta_of(source)
            supports = [
                row for row in (meta.get("supporting_passages") or [])
                if isinstance(row, dict)
            ]
            profile_text = " ".join([
                source_text(source),
                clean_text(meta.get("candidate_group_label")),
                *[
                    clean_text(row.get("analysis_text") or row.get("text"), 2200)
                    for row in supports[:40]
                ],
            ])
            support_ids = {
                clean_text(row.get("passage_id") or row.get("id"))
                for row in supports
                if clean_text(row.get("passage_id") or row.get("id"))
            }
            direct_id = clean_text(meta.get("passage_id"))
            if direct_id:
                support_ids.add(direct_id)
            existing_passage_ids.update(support_ids)
            existing_profiles.append((source, concept_tokens(profile_text), support_ids))

        by_section: Dict[str, List[Dict[str, Any]]] = {}
        for raw in catalog:
            if not isinstance(raw, dict):
                continue
            blob = clean_text(" ".join(str(raw.get(k) or "") for k in (
                "context_before", "text", "analysis_text", "context_after"
            )), 7000)
            normalized = _diag_norm(blob)
            signals = signal_report(raw, normalized)
            current_project = is_current_project_evidence(raw)
            explicit_allowed = bool(
                signals["explicit_constraint"]
                and (project_anchor_re.search(normalized) or current_project)
            )
            implicit_allowed = bool(
                signals["implicit_experimental_uncertainty"] and current_project
            )
            if not (explicit_allowed or implicit_allowed):
                continue
            if (
                raw.get("reference_like")
                or external_re.search(normalized)
                or noise_re.search(normalized)
            ):
                continue
            # Un tableau numérique brut n'est pas un verrou. Une conclusion
            # narrative portant les trois signaux ci-dessus reste admissible,
            # même si elle provient d'une section intitulée « Tableau ».
            if blob.count("|") >= 10 and not signals["implicit_experimental_uncertainty"]:
                continue
            section = clean_text(raw.get("section_title"), 500)
            document = clean_text(raw.get("document"), 500)
            signature = f"{document.lower()}|{_diag_norm(section)}"
            qualified = dict(raw)
            qualified["_missing_lock_recovery_signals"] = signals
            by_section.setdefault(signature, []).append(qualified)

        recovered: List[Dict[str, Any]] = []
        duplicate_existing_count = 0
        duplicate_existing_passage_ids: List[str] = []
        ranked_sections = sorted(
            by_section.items(),
            key=lambda item: max(
                int((row.get("_missing_lock_recovery_signals") or {}).get("score") or 0)
                for row in item[1]
            ),
            reverse=True,
        )
        try:
            max_new_candidates = max(
                0,
                min(5, int(os.getenv("ENNOSMART_DIAG_MAX_IMPLICIT_LOCK_RECOVERY", "3"))),
            )
        except Exception:
            max_new_candidates = 3

        recovered_profiles: List[set[str]] = []
        for signature, rows in ranked_sections:
            # Conserver au plus les trois passages les plus informatifs de la section.
            rows = sorted(
                rows,
                key=lambda row: (
                    int((row.get("_missing_lock_recovery_signals") or {}).get("score") or 0),
                    len(clean_text(row.get("analysis_text") or row.get("text"))),
                ),
                reverse=True,
            )[:3]
            rows = [
                row for row in rows
                if clean_text(row.get("passage_id") or row.get("id")) not in existing_passage_ids
            ]
            if not rows:
                continue
            combined = clean_text(" ".join(clean_text(row.get("analysis_text") or row.get("text"), 1800) for row in rows), 4200)
            tokens = concept_tokens(combined)
            if len(tokens) < 5:
                continue

            # Une similarité conceptuelle forte indique que l'incertitude est déjà
            # couverte. On ne modifie pas le groupe NLP : cette passe améliore le
            # rappel sans réécrire sa composition ni sa décision Frascati.
            best_similarity = 0.0
            best_shared = 0
            for _source, profile, _support_ids in existing_profiles:
                shared = len(tokens & profile)
                if shared < 4:
                    continue
                containment = shared / max(1, min(len(tokens), len(profile)))
                similarity = containment + min(0.18, shared * 0.015)
                if similarity > best_similarity:
                    best_similarity = similarity
                    best_shared = shared
            if best_shared >= 4 and best_similarity >= 0.38:
                for row in rows:
                    passage_id = clean_text(row.get("passage_id") or row.get("id"))
                    if passage_id:
                        duplicate_existing_passage_ids.append(passage_id)
                duplicate_existing_count += len(rows)
                continue

            # Eviter plusieurs nouveaux candidats qui décrivent le même problème
            # avec des formulations différentes dans des documents voisins.
            if any(
                len(tokens & profile) >= 4
                and len(tokens & profile) / max(1, min(len(tokens), len(profile))) >= 0.45
                for profile in recovered_profiles
            ):
                continue
            if len(recovered) >= max_new_candidates:
                continue

            primary = rows[0]
            primary_signals = primary.get("_missing_lock_recovery_signals") or {}
            recovery_kind = (
                "explicit_constraint"
                if primary_signals.get("explicit_constraint")
                else "implicit_experimental_uncertainty"
            )
            sentence = next((
                clean_text(part, 500)
                for part in re.split(r"(?<=[.!?])\s+|[\r\n]+", combined)
                if (
                    explicit_constraint_re.search(_diag_norm(part))
                    or unresolved_re.search(_diag_norm(part))
                )
            ), clean_text(primary.get("section_title"), 500) or "Incertitude expérimentale à qualifier")
            group_id = "recovered_uncertainty_" + hashlib.sha1(signature.encode("utf-8", errors="ignore")).hexdigest()[:14]
            recovery_score = int(primary_signals.get("score") or 0)
            recovery_confidence = min(0.86, 0.68 + max(0, recovery_score - 7) * 0.03)
            clean_supports: List[Dict[str, Any]] = []
            for row in rows:
                clean_row = dict(row)
                clean_row.pop("_missing_lock_recovery_signals", None)
                clean_supports.append(clean_row)
            meta = {
                "role": "verrou",
                "semantic_role": "verrou",
                "source_type": "recovered_missing_lock_candidate",
                "lock_group_id": group_id,
                "candidate_group_label": sentence,
                "document": primary.get("document"),
                "document_id": primary.get("document_id"),
                "source_path": primary.get("source_path"),
                "section_title": primary.get("section_title"),
                "passage_id": primary.get("passage_id"),
                "content_origin": primary.get("content_origin") or "raw_client_document",
                "diagnostic_corpus_selected": True,
                "declared_corpus": "diagnostic_current",
                "recovered_missing_lock_candidate": True,
                "recovery_kind": recovery_kind,
                "recovery_signals": {
                    key: value for key, value in primary_signals.items()
                    if key != "score"
                },
                "recovery_score": recovery_score,
                "recovery_confidence": recovery_confidence,
                "display_as_main_lock": True,
                "technical_scope": "project_structuring_lock",
                "needs_human_validation": True,
                "supporting_passages": clean_supports,
            }
            recovered.append({
                "text": combined,
                "source_text": combined,
                "document": meta["document"],
                "source_path": meta["source_path"],
                "metadata": meta,
                "recovered_missing_lock_candidate": True,
            })
            recovered_profiles.append(tokens)

        self._last_missing_lock_recovery_report = {
            "qualified_sections": len(by_section),
            "duplicate_passages_already_covered_by_existing_locks": duplicate_existing_count,
            "duplicate_existing_passage_ids": list(dict.fromkeys(duplicate_existing_passage_ids)),
            "new_candidates_for_human_validation": len(recovered),
            "max_new_candidates": max_new_candidates,
            "primary_lock_logic_changed": False,
        }
        if recovered or duplicate_existing_count:
            print(
                "[EnnoDiagnostic][MISSING_LOCK_RECOVERY] "
                f"doublons_existants={duplicate_existing_count} candidats_nouveaux={len(recovered)}",
                flush=True,
            )
        return recovered

    def retrieve_all_sections(self) -> Dict[str, List[Dict[str, Any]]]:
        sections: Dict[str, List[Dict[str, Any]]] = {}

        # V5.4 — FAST NLP AUTHORITY.
        # ``Préparer les sources`` a déjà extrait/classé les preuves et construit
        # les groupes de verrous. Refaire 8 à 10 recherches vectorielles (dont
        # top_k=250 pour les verrous, avec oversampling) était redondant et
        # expliquait une grande partie du temps de Diagnostic.
        current_project_only = str(
            os.getenv("ENNOSMART_DIAG_CURRENT_PROJECT_ONLY", "1")
        ).strip().lower() not in {"0", "false", "no", "off"}
        fast_nlp_authority = str(
            os.getenv("ENNOSMART_DIAG_FAST_NLP_AUTHORITY", "1")
        ).strip().lower() not in {"0", "false", "no", "off"}

        if current_project_only and fast_nlp_authority:
            t_fast = time.time()
            current_nlp_sections = self._load_current_nlp_evidence_sections()
            direct_nlp_groups = self._load_nlp_lock_group_sources()
            initial_direct_lock_count = len(direct_nlp_groups)

            # Le mode rapide n'est activé que si le NLP courant est réellement
            # disponible. Sinon on retombe intégralement sur l'ancien RAG.
            has_current_nlp = bool(
                direct_nlp_groups
                or any(current_nlp_sections.get(key) for key in (
                    "objectifs", "methodes", "resultats",
                    "parametres", "limites", "contributions",
                ))
            )
            if has_current_nlp:
                recovered_missing_locks = self._load_recovered_missing_lock_candidates(
                    existing_lock_sources=direct_nlp_groups,
                )
                direct_nlp_groups = merge_ranked_sources(
                    direct_nlp_groups,
                    recovered_missing_locks,
                    max_items=max(1, len(direct_nlp_groups) + len(recovered_missing_locks)),
                )

                # Important : aucune nouvelle logique de regroupement principal.
                # Les preuves implicites proches sont reconnues comme déjà couvertes ;
                # une preuve réellement nouvelle reste un candidat humain séparé.
                sections["verrous"] = dedupe_sources(
                    direct_nlp_groups,
                    max_items=max(1, len(direct_nlp_groups)),
                )
                sections["_nlp_verrou_candidates"] = list(sections["verrous"])
                sections["_frascati_verrous"] = list(sections["verrous"])

                for key in (
                    "objectifs", "methodes", "resultats",
                    "parametres", "limites", "contributions",
                ):
                    sections[key] = list(current_nlp_sections.get(key) or [])

                # Le contexte global est construit à partir des preuves NLP
                # courantes déjà qualifiées, sans recherche Chroma supplémentaire.
                direct_global: List[Dict[str, Any]] = []
                for key in (
                    "objectifs", "contributions", "limites",
                    "methodes", "resultats", "parametres",
                ):
                    direct_global.extend((sections.get(key) or [])[:10])
                sections["global"] = merge_ranked_sources(
                    direct_global,
                    max_items=min(40, max(1, len(direct_global))),
                ) if direct_global else []

                # Les axes transverses restent du CONTEXTE, jamais une source de
                # création de verrou. En mode rapide ils sont dérivés localement.
                sections["axe_problemes_transverses"] = list(
                    (sections.get("limites") or [])[:12]
                )
                sections["axe_contraintes_transverses"] = merge_ranked_sources(
                    sections.get("parametres") or [],
                    sections.get("limites") or [],
                    max_items=12,
                )
                sections["axe_preuves_resultats"] = list(
                    (sections.get("resultats") or [])[:12]
                )
                sections["verrou_support_context"] = merge_ranked_sources(
                    sections.get("limites") or [],
                    sections.get("axe_problemes_transverses") or [],
                    sections.get("axe_contraintes_transverses") or [],
                    max_items=30,
                )

                sections = _diag_enrich_sections_for_real_agent(self, sections)
                sections["_retrieval_report"] = {
                    "mode": "fast_nlp_authority_v54",
                    "chroma_queries": 0,
                    "elapsed_seconds": round(time.time() - t_fast, 3),
                    "direct_lock_groups": initial_direct_lock_count,
                    "recovered_lock_candidates": len(recovered_missing_locks),
                    "implicit_recovery": dict(
                        getattr(self, "_last_missing_lock_recovery_report", {}) or {}
                    ),
                    "final_lock_sources_before_consultant_synthesis": len(sections.get("verrous") or []),
                    "objective_facts": len(sections.get("objectifs") or []),
                    "method_facts": len(sections.get("methodes") or []),
                    "result_facts": len(sections.get("resultats") or []),
                    "parameter_facts": len(sections.get("parametres") or []),
                    "lock_policy": "unchanged_nlp_groups_plus_strict_generic_implicit_recovery",
                }
                print(
                    "[EnnoDiagnostic][FAST_NLP_AUTHORITY] "
                    f"chroma=0 verrous_sources={len(sections.get('verrous') or [])} "
                    f"objectifs={len(sections.get('objectifs') or [])} "
                    f"methodes={len(sections.get('methodes') or [])} "
                    f"resultats={len(sections.get('resultats') or [])} "
                    f"parametres={len(sections.get('parametres') or [])} "
                    f"elapsed={round(time.time() - t_fast, 2)}s",
                    flush=True,
                )
                return sections

        # Fallback historique : utilisé si le NLP courant n'existe pas ou si
        # ENNOSMART_DIAG_FAST_NLP_AUTHORITY=0.
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
        recovered_missing_locks = self._load_recovered_missing_lock_candidates(
            existing_lock_sources=direct_nlp_groups,
        )
        direct_nlp_groups = merge_ranked_sources(
            direct_nlp_groups, recovered_missing_locks,
            max_items=len(direct_nlp_groups) + len(recovered_missing_locks),
        )
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

        # Source de vérité documentaire : preuves déjà extraites par le NLP du
        # projet/année COURANTS. Chroma reste un complément, jamais l'inverse.
        current_nlp_sections = self._load_current_nlp_evidence_sections()
        merge_limits = {
            "objectifs": 24, "methodes": 30, "resultats": 30,
            "parametres": 24, "limites": 24, "contributions": 24,
        }
        strict_current_fact_sections = {
            "objectifs", "methodes", "resultats", "parametres", "contributions",
        }
        current_project_only = str(
            os.getenv("ENNOSMART_DIAG_CURRENT_PROJECT_ONLY", "1")
        ).strip().lower() not in {"0", "false", "no", "off"}
        for section_key, limit in merge_limits.items():
            direct = current_nlp_sections.get(section_key) or []
            existing = sections.get(section_key) or []
            if current_project_only and section_key in strict_current_fact_sections:
                # Le pack NLP courant est l'autorité factuelle. Des chunks Chroma
                # plus anciens ou sans résolution de provenance ne doivent jamais
                # réintroduire une publication externe déjà exclue ci-dessus.
                sections[section_key] = list(direct[:limit])
            elif direct:
                sections[section_key] = merge_ranked_sources(direct, existing, max_items=limit)

        # Le contexte global reçoit lui aussi les preuves du projet courant afin
        # que l'objectif ne dépende jamais d'un index Chroma incomplet.
        direct_global: List[Dict[str, Any]] = []
        for key in ("objectifs", "contributions", "limites", "methodes", "resultats"):
            direct_global.extend((current_nlp_sections.get(key) or [])[:8])
        if direct_global:
            sections["global"] = merge_ranked_sources(direct_global, sections.get("global", []), max_items=30)

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
                if not group_id:
                    continue
                group_assessments.append({
                    "group_id": group_id,
                    "eligibility_score": round(score, 4),
                    "documentary_coverage": raw.get("documentary_coverage"),
                    "eligibility_assessment_score": raw.get("eligibility_assessment_score"),
                    "rnd_defensibility_index": raw.get("rnd_defensibility_index"),
                    "eligibility_recommendation": raw.get("eligibility_recommendation"),
                    "risk_level": clean_text(raw.get("risk_level")) or None,
                    "interpretation": clean_text(raw.get("interpretation")) or None,
                    "questions_to_ask": raw.get("questions_to_ask")
                    if isinstance(raw.get("questions_to_ask"), list) else [],
                    "dimensions": raw.get("dimensions")
                    if isinstance(raw.get("dimensions"), dict) else {},
                    "criteria_score_breakdown": _criterion_breakdown_from_assessment(raw),
                    "demarche_legibility": compact_demarche_audit(raw.get("demarche_legibility")),
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
                    "operation_title": truncate(
                        group.get("text") or group.get("analysis_text") or group.get("section_title"),
                        320,
                    ),
                }

        additional_passages: List[Dict[str, Any]] = []
        fastjudge_audit = guard.get("fastjudge_verrou_signals_audit") or []
        if isinstance(fastjudge_audit, list):
            additional_passages.extend(
                item for item in fastjudge_audit if isinstance(item, dict)
            )
        evidence_pack = payload.get("multi_document_evidence_pack_for_ennodiagnostic") or {}
        if isinstance(evidence_pack, dict):
            evidence_catalog = evidence_pack.get("evidence_catalog") or []
            if isinstance(evidence_catalog, list):
                additional_passages.extend(
                    item for item in evidence_catalog if isinstance(item, dict)
                )
        additional_passages = _dedupe_nlp_passages(additional_passages)

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
        fast_frascati_evidence = str(
            os.getenv("ENNOSMART_DIAG_FAST_FRASCATI_EVIDENCE", "1")
        ).strip().lower() not in {"0", "false", "no", "off"}
        report_builder = (
            _build_eligibility_evidence_report_fast
            if fast_frascati_evidence
            else _build_eligibility_evidence_report
        )
        eligibility_evidence_report = report_builder(
            assessment,
            [group for group in technical_groups if isinstance(group, dict)],
            additional_passages=additional_passages,
        )

        return {
            "ok": global_score is not None or bool(group_assessments),
            "source_path": str(path),
            "score_source": score_source,
            "average_frascati_score": global_score,
            "eligibility_assessment_score": eligibility_assessment_score,
            "eligibility_assessment_score_semantics": assessment.get("eligibility_assessment_score_semantics"),
            "rnd_defensibility_index": assessment.get("rnd_defensibility_index"),
            "documentary_coverage": assessment.get("documentary_coverage"),
            "documented_share": assessment.get("documented_share"),
            "remaining_documentary_gap": assessment.get("remaining_documentary_gap"),
            "score_formula": assessment.get("score_formula"),
            "score_basis_group_id": assessment.get("score_basis_group_id"),
            "score_basis_operation_status": assessment.get("score_basis_operation_status"),
            "portfolio_criteria_coverage": assessment.get("portfolio_criteria_coverage"),
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
            "eligibility_evidence_report": eligibility_evidence_report,
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
            "rnd_defensibility_index": official.get("rnd_defensibility_index"),
            "documentary_coverage": official.get("documentary_coverage"),
            "documented_share": official.get("documented_share"),
            "remaining_documentary_gap": official.get("remaining_documentary_gap"),
            "score_formula": official.get("score_formula"),
            "score_basis_group_id": official.get("score_basis_group_id"),
            "score_basis_operation_status": official.get("score_basis_operation_status"),
            "portfolio_criteria_coverage": official.get("portfolio_criteria_coverage"),
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
            "eligibility_evidence_report": official.get("eligibility_evidence_report") or {},
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
                "rnd_defensibility_index": frascati_summary.get("rnd_defensibility_index"),
                "documentary_coverage": frascati_summary.get("documentary_coverage"),
                "portfolio_criteria_coverage": frascati_summary.get("portfolio_criteria_coverage"),
                "remaining_documentary_gap": frascati_summary.get("remaining_documentary_gap"),
                "score_formula": frascati_summary.get("score_formula"),
                "score_basis_group_id": frascati_summary.get("score_basis_group_id"),
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
                "eligibility_evidence_report": frascati_summary.get("eligibility_evidence_report") or {},
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
                subproject=self.subproject,
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
        for index, item in enumerate(comparisons[:max_items], start=1):
            if not isinstance(item, dict):
                continue

            current = item.get("current_item") if isinstance(item.get("current_item"), dict) else {}
            best_match = item.get("best_match") if isinstance(item.get("best_match"), dict) else {}
            previous = (
                best_match.get("previous_candidate")
                if isinstance(best_match.get("previous_candidate"), dict)
                else item.get("previous_candidate")
                if isinstance(item.get("previous_candidate"), dict)
                else {}
            )
            decision = (
                item.get("decision")
                if isinstance(item.get("decision"), dict)
                else best_match.get("final_scores")
                if isinstance(best_match.get("final_scores"), dict)
                else {}
            )

            current_title = clean_text(
                current.get("section_title")
                or current.get("title")
                or item.get("current_title")
                or item.get("current_label")
            )
            previous_title = clean_text(
                previous.get("section_title")
                or previous.get("title")
                or item.get("previous_title")
                or item.get("previous_label")
            )
            previous_year = clean_text(
                previous.get("previous_year")
                or previous.get("year")
                or item.get("previous_year")
            )
            label = clean_text(
                decision.get("label")
                or decision.get("status")
                or item.get("status")
                or item.get("relation")
                or item.get("comparison_status")
            )

            def score_text(value: Any) -> str:
                try:
                    score = float(value)
                except (TypeError, ValueError):
                    return ""
                percent = score * 100 if abs(score) <= 1 else score
                return f"{percent:.0f}%"

            continuity = score_text(
                decision.get("continuity_score")
                if decision.get("continuity_score") is not None
                else best_match.get("similarity_score")
                if best_match.get("similarity_score") is not None
                else item.get("similarity")
            )
            novelty = score_text(decision.get("novelty_score"))

            values = []
            if current_title:
                values.append(f"verrou courant={truncate(current_title, 300)}")
            if previous_title:
                previous_label = f"passage CIR {previous_year}" if previous_year else "passage CIR antérieur"
                values.append(f"{previous_label}={truncate(previous_title, 300)}")
            if label:
                values.append(f"statut={truncate(label, 180)}")
            if continuity:
                values.append(f"continuité={continuity}")
            if novelty:
                values.append(f"apport courant={novelty}")

            # Compatibilité avec les anciens rapports qui utilisaient des champs plats.
            if not values:
                for key in (
                    "comparison", "match_type", "reason", "explanation",
                ):
                    value = item.get(key)
                    if isinstance(value, (str, int, float)) and clean_text(value):
                        values.append(f"{key}={truncate(value, 260)}")
            if values:
                lines.append(f"- Comparaison {index} : " + " | ".join(values))
        if len(lines) == 4:
            if comparisons:
                lines.append("- Des rapprochements existent, mais leurs détails ne sont pas lisibles dans ce format de rapport.")
            else:
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

            # MODE PROJET COURANT : aucune formulation, exemple ou contexte provenant
            # d'un autre projet ne doit influencer la reformulation des verrous.
            # Les preuves NLP/RAG du projet courant sont l'unique base factuelle.
            style_block = ""
            synthesis = synthesize_consultant_verrous(
                sections=sections,
                frascati_summary=frascati_summary,
                llm=self.llm,
                style_block=style_block,
                memory_v2_report=None,
                previous_cir_context=None,
                cache_path=self.diagnostic_dir / "cache" / "verrou_reformulation_v191.json",
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
                subproject=self.subproject,
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
        trust_local_preflight = str(
            os.getenv("ENNOSMART_DIAG_TRUST_LOCAL_YEAR_PREFLIGHT", "0")
        ).strip().lower() not in {"0", "false", "no", "off"}
        local_previous_years = _local_previous_year_directories(self.out_dir, self.year)
        if trust_local_preflight and not local_previous_years:
            print(
                "⏩ Comparaison CIR précédent ignorée immédiatement : "
                "aucun dossier d'année antérieure pour ce projet.",
                flush=True,
            )
            return {
                "ok": True,
                "has_previous_cir": False,
                "previous_cir_available": False,
                "previous_cir_years_used": [],
                "previous_years": [],
                "comparisons": [],
                "verrou_comparisons": [],
                "previous_cir_source": None,
                "preflight_no_previous": True,
                "preflight_mode": "local_year_directory",
                "managed_by_ennodiagnostic": True,
                "in_prompt": False,
                "current_verrous_count": len([
                    item for item in (current_verrous or []) if isinstance(item, dict)
                ]),
            }

        try:
            from modules.CIR_MEMORY.cir_memory import (
                load_or_create_cir_memory_comparison,
                load_previous_cir_memory_items,
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
                subproject=self.subproject,
                current_year=self.year,
            )

            root = Path(
                os.getenv("ENNOSMART_ROOT")
                or Path(__file__).resolve().parents[2]
            )
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
                    self.subproject,
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

            # Préflight léger : ne pas lancer le matching N/N-1 coûteux lorsque
            # la mémoire officielle confirme qu'aucune année antérieure n'existe.
            # En cas d'erreur du préflight, on conserve l'ancien chemin complet.
            try:
                previous_years_probe, _previous_items_probe = load_previous_cir_memory_items(
                    organisme=self.organisme,
                    project=self.project,
                    current_year=self.year,
                    subproject=self.subproject,
                    max_previous_years=max_previous_years,
                )
                if not previous_years_probe:
                    report = {
                        "ok": True,
                        "has_previous_cir": False,
                        "previous_cir_available": False,
                        "previous_cir_years_used": [],
                        "previous_years": [],
                        "comparisons": [],
                        "verrou_comparisons": [],
                        "previous_cir_source": None,
                        "preflight_no_previous": True,
                        "managed_by_ennodiagnostic": True,
                        "in_prompt": False,
                        "current_verrous_count": len(normalized_current_verrous),
                        "current_verrous_hash": current_verrous_hash,
                    }
                    print(
                        "⏩ Comparaison CIR précédent ignorée : aucune année antérieure disponible (préflight).",
                        flush=True,
                    )
                    return report
            except Exception as preflight_exc:
                print(
                    f"[EnnoDiagnostic][CIR_PREVIOUS_PREFLIGHT][WARN] {preflight_exc}",
                    flush=True,
                )

            report = load_or_create_cir_memory_comparison(
                organisme=self.organisme,
                project=self.project,
                subproject=self.subproject,
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
            "- Indice de défendabilité R&D (sans multiplication par un ratio d'activités) : "
            f"{frascati_summary.get('eligibility_assessment_score')}"
        )
        demarche = compact_demarche_audit(frascati_summary.get("demarche_legibility"))
        if demarche:
            lines.append(
                "- Étude séparée de la démarche : "
                f"statut={demarche.get('project_status') or demarche.get('operation_status')} ; "
                f"opérations R&D défendables={demarche.get('rnd_core_defendable_operations_count', 0)}, "
                f"opérations partielles={demarche.get('rnd_core_partial_operations_count', 0)}, "
                f"opérations classiques={demarche.get('classical_engineering_operations_count', 0)}."
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
            sublocks = [
                clean_text(value)
                for value in (
                    item.get("subproblems_current")
                    or item.get("sublocks")
                    or item.get("sous_verrous")
                    or []
                )
                if clean_text(value)
            ]
            if sublocks:
                lines.append(
                    "Sous-verrous / sous-problèmes associés : "
                    + " ".join(
                        f"{sub_index}. {value}"
                        for sub_index, value in enumerate(dict.fromkeys(sublocks), start=1)
                    )
                )
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
        stage_timings: Dict[str, float] = {}
        self.diagnostic_dir.mkdir(parents=True, exist_ok=True)

        _stage_t0 = time.time()
        sections = self.retrieve_all_sections()
        stage_timings["retrieve_sections"] = round(time.time() - _stage_t0, 3)
        frascati_sources = sections.get("_frascati_verrous") or sections.get("verrous") or []
        _stage_t0 = time.time()
        frascati_summary = self.frascati_summary_from_chroma(frascati_sources)
        stage_timings["frascati_summary"] = round(time.time() - _stage_t0, 3)
        _stage_t0 = time.time()
        ai_detection_report = self.load_ai_detection_report()
        stage_timings["ai_detection_report"] = round(time.time() - _stage_t0, 3)
        current_project_only = str(os.getenv("ENNOSMART_DIAG_CURRENT_PROJECT_ONLY", "1")).strip().lower() not in {
            "0", "false", "no", "off"
        }
        if current_project_only:
            # Ne charge pas de texte d'autres projets dans la génération du diagnostic courant.
            # Les comparaisons historiques éventuelles restent des vues séparées et ne servent
            # jamais à produire Objectif / Synthèse / Verrous / Démarche / Résultats / Paramètres.
            style_memory_report = {
                "ok": False, "available": False, "disabled_for_current_project_only": True,
                "principle": "current_project_only",
            }
            memory_v2_report = {
                "ok": False, "available": False, "disabled_for_current_project_only": True,
                "principle": "current_project_only",
            }
            previous_verrou_context = {
                "ok": False, "available": False, "previous_years": [], "examples_count": 0,
                "disabled_for_current_project_only": True,
            }
            print(
                "[EnnoDiagnostic][CURRENT_PROJECT_ONLY] "
                "style_memory=disabled memory_v2=disabled previous_lock_context=disabled",
                flush=True,
            )
        else:
            style_memory_report = self.load_style_memory_context(sections)
            memory_v2_report = self.load_memory_v2_context(sections)
            previous_verrou_context = self.load_previous_verrou_context()

        self._last_style_memory_report = style_memory_report
        self._last_memory_v2_report = memory_v2_report
        self._last_previous_verrou_context = previous_verrou_context

        core_result: Dict[str, Any] = {}
        static_diagnostic: Dict[str, Any] = {}
        presenter_error: Optional[str] = None
        presenter_generate_core = None
        presenter_build_final = None
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
            presenter_generate_core = generate_structured_diagnostic_core
            presenter_build_final = build_final_static_diagnostic
        except Exception as exc:
            presenter_error = str(exc)
            print(f"[EnnoDiagnostic][V182_PRESENTER][WARN] {exc}")

        # La composition des verrous vient du NLP. Le LLM ne fait que reformuler.
        _stage_t0 = time.time()
        llm_reformulated_verrous = self.build_llm_reformulated_verrous(
            content="",
            sections=sections,
            frascati_summary=frascati_summary,
        )
        llm_reformulated_verrous = self._enrich_verrous_with_frascati(
            llm_reformulated_verrous,
            frascati_summary,
        )
        llm_reformulated_verrous = _polish_visible_lock_titles_without_regrouping(
            llm_reformulated_verrous
        )
        stage_timings["verrou_reformulation"] = round(time.time() - _stage_t0, 3)

        # La réconciliation N/N-1 reste une passe de continuité séparée. Elle ne
        # recalcule jamais le score Frascati et ne transforme jamais l'historique
        # en preuve du projet courant.
        pre_reconciliation_verrous_count = len(llm_reformulated_verrous)
        historical_continuity_report: Dict[str, Any] = {
            "ok": False,
            "has_previous_cir": False,
            "policy": "fail-open: keep current NLP candidates",
            "reconciled_verrous": llm_reformulated_verrous,
        }
        _stage_t0 = time.time()
        skip_historical_reconciliation = False
        historical_preflight_enabled = str(
            os.getenv("ENNOSMART_DIAG_HISTORICAL_PREFLIGHT", "1")
        ).strip().lower() not in {"0", "false", "no", "off"}

        if historical_preflight_enabled:
            local_years_probe = _local_previous_year_directories(self.out_dir, self.year)
            trust_local_preflight = str(
                os.getenv("ENNOSMART_DIAG_TRUST_LOCAL_YEAR_PREFLIGHT", "0")
            ).strip().lower() not in {"0", "false", "no", "off"}
            if trust_local_preflight and not local_years_probe:
                skip_historical_reconciliation = True
                historical_continuity_report = {
                    **historical_continuity_report,
                    "ok": True,
                    "has_previous_cir": False,
                    "preflight_no_previous": True,
                    "preflight_mode": "local_year_directory",
                    "policy": "no_previous_local_year_skip_reconciliation",
                    "reconciled_verrous": llm_reformulated_verrous,
                }
                print(
                    "⏩ Réconciliation historique ignorée immédiatement : "
                    "aucun dossier d'année antérieure pour ce projet.",
                    flush=True,
                )
            else:
                try:
                    from modules.CIR_MEMORY.cir_memory import load_previous_cir_memory_items
                    previous_years_probe, _previous_items_probe = load_previous_cir_memory_items(
                        organisme=self.organisme,
                        project=self.project,
                        current_year=self.year,
                        subproject=self.subproject,
                        max_previous_years=max(
                            1, int(os.getenv("ENNOSMART_CIR_MEMORY_MAX_PREVIOUS_YEARS", "3"))
                        ),
                    )
                    if not previous_years_probe:
                        skip_historical_reconciliation = True
                        historical_continuity_report = {
                            **historical_continuity_report,
                            "ok": True,
                            "has_previous_cir": False,
                            "preflight_no_previous": True,
                            "preflight_mode": "cir_memory",
                            "policy": "no_previous_cir_skip_reconciliation",
                            "reconciled_verrous": llm_reformulated_verrous,
                        }
                        print(
                            "⏩ Réconciliation historique ignorée : aucune année CIR antérieure disponible.",
                            flush=True,
                        )
                except Exception as preflight_exc:
                    print(
                        f"[EnnoDiagnostic][HISTORICAL_PREFLIGHT][WARN] {preflight_exc}",
                        flush=True,
                    )

        if not skip_historical_reconciliation:
            try:
                try:
                    from agents.EnnoDiagnostic.historical_continuity_reconciler import (
                        reconcile_historical_continuity,
                    )
                except Exception:
                    from historical_continuity_reconciler import reconcile_historical_continuity

                historical_continuity_report = reconcile_historical_continuity(
                    organisme=self.organisme,
                    project=self.project,
                    subproject=self.subproject,
                    year=self.year,
                    current_verrous=llm_reformulated_verrous,
                    current_sections=sections,
                    search_current=self.search_chroma,
                    llm=self.llm,
                    output_dir=self.diagnostic_dir,
                )
                reconciled = historical_continuity_report.get("reconciled_verrous")
                if isinstance(reconciled, list):
                    llm_reformulated_verrous = [
                        item for item in reconciled if isinstance(item, dict)
                    ]
                llm_reformulated_verrous = self._enrich_verrous_with_frascati(
                    llm_reformulated_verrous,
                    frascati_summary,
                )
                llm_reformulated_verrous = _polish_visible_lock_titles_without_regrouping(
                    llm_reformulated_verrous
                )
            except Exception as exc:
                historical_continuity_report = {
                    **historical_continuity_report,
                    "ok": False,
                    "error": str(exc),
                    "reconciled_verrous": llm_reformulated_verrous,
                }
                print(f"[EnnoDiagnostic][HISTORICAL_CONTINUITY][WARN] {exc}", flush=True)

        stage_timings["historical_continuity"] = round(time.time() - _stage_t0, 3)

        # V6 : les verrous atomiques validés restent l'autorité et ne sont JAMAIS
        # supprimés, fusionnés ou remplacés. Une couche optionnelle et conservative
        # peut seulement AJOUTER un axe parent transversal lorsqu'au moins deux
        # verrous courants, chacun sourcé, partagent un mécanisme scientifique
        # explicitement démontré. Aucun nombre cible de verrous n'est imposé.
        #
        # Les autres sections (objectif, démarche, résultats, paramètres, Frascati)
        # continuent d'être générées depuis ``atomic_verrous`` afin qu'une vue
        # d'abstraction supplémentaire ne modifie pas les sorties déjà stabilisées.
        _axis_t0 = time.time()
        atomic_verrous = list(llm_reformulated_verrous)
        display_verrous = list(atomic_verrous)
        transversal_parent_verrous: List[Dict[str, Any]] = []
        transversal_selection_audit: List[Dict[str, Any]] = []

        scientific_axis_report: Dict[str, Any] = {
            "ok": False,
            "disabled": True,
            "policy": "atomic_authority_additive_transversal_parent_only",
            "atomic_verrous": atomic_verrous,
            "scientific_axes": [],
            "axis_count": 0,
            "transversal_additions_count": 0,
            "transversal_additions": [],
            "transversal_selection_audit": [],
        }

        use_axis = (
            len(atomic_verrous) >= 2
            and str(
                os.getenv(
                    "ENNOSMART_DIAG_USE_SCIENTIFIC_AXIS_CONSOLIDATION",
                    "1",
                )
            ).strip().lower()
            in {"1", "true", "yes", "oui", "on"}
        )

        if use_axis:
            try:
                try:
                    from agents.EnnoDiagnostic.scientific_axis_synthesizer import (
                        select_transversal_axis_additions,
                        synthesize_scientific_axes,
                    )
                except Exception:
                    from scientific_axis_synthesizer import (
                        select_transversal_axis_additions,
                        synthesize_scientific_axes,
                    )

                scientific_axis_report = synthesize_scientific_axes(
                    current_verrous=atomic_verrous,
                    historical_continuity_report=historical_continuity_report,
                    current_sections=sections,
                    current_year=self.year,
                    llm=self.llm,
                    output_dir=self.diagnostic_dir,
                )

                (
                    transversal_parent_verrous,
                    transversal_selection_audit,
                ) = select_transversal_axis_additions(
                    atomic_verrous=atomic_verrous,
                    scientific_axis_report=scientific_axis_report,
                )

                # ADDITIF UNIQUEMENT : les cartes atomiques restent dans le même
                # ordre et inchangées ; les éventuels parents sont ajoutés après.
                display_verrous = [
                    *atomic_verrous,
                    *transversal_parent_verrous,
                ]

                scientific_axis_report["disabled"] = False
                scientific_axis_report["audit_only"] = False
                scientific_axis_report["augmentation_only"] = True
                scientific_axis_report["atomic_authority_preserved"] = True
                scientific_axis_report["transversal_additions_count"] = len(
                    transversal_parent_verrous
                )
                scientific_axis_report["transversal_additions"] = (
                    transversal_parent_verrous
                )
                scientific_axis_report["transversal_selection_audit"] = (
                    transversal_selection_audit
                )
                scientific_axis_report["display_verrous_count"] = len(display_verrous)

            except Exception as exc:
                # Fail-open absolu : une erreur d'abstraction ne peut jamais faire
                # perdre un verrou existant ni modifier les autres sections.
                display_verrous = list(atomic_verrous)
                transversal_parent_verrous = []
                scientific_axis_report = {
                    **scientific_axis_report,
                    "ok": False,
                    "disabled": False,
                    "augmentation_only": True,
                    "atomic_authority_preserved": True,
                    "transversal_additions_count": 0,
                    "transversal_additions": [],
                    "error": str(exc),
                    "fallback_policy": "keep_all_atomic_verrous_unchanged",
                }
                print(
                    f"[EnnoDiagnostic][SCIENTIFIC_AXIS_AUGMENTATION][WARN] {exc}",
                    flush=True,
                )

        # La liste affichée gagne éventuellement des parents transversaux, mais
        # ``atomic_verrous`` reste utilisée pour toutes les autres analyses.
        llm_reformulated_verrous = display_verrous
        stage_timings["scientific_axis_augmentation"] = round(
            time.time() - _axis_t0,
            3,
        )

        synthesis_report = dict(getattr(self, "_last_verrou_synthesis_report", {}) or {})
        synthesis_report["atomic_verrous"] = atomic_verrous
        synthesis_report["transversal_parent_verrous"] = transversal_parent_verrous
        synthesis_report["transversal_parent_count"] = len(transversal_parent_verrous)
        synthesis_report["scientific_axis_report"] = scientific_axis_report
        synthesis_report["llm_reformulated_verrous"] = llm_reformulated_verrous
        synthesis_report["final_items"] = llm_reformulated_verrous
        synthesis_report["final_count"] = len(llm_reformulated_verrous)
        synthesis_report["atomic_candidates_preserved"] = True
        synthesis_report["scientific_axis_consolidation_applied"] = bool(
            transversal_parent_verrous
        )
        self._last_verrou_synthesis_report = synthesis_report

        # Les sections déjà stabilisées restent rédigées depuis les verrous
        # atomiques. L'augmentation transversale est réservée à la vue des
        # verrous afin de ne pas modifier objectif/démarche/résultats/paramètres.
        _stage_t0 = time.time()
        if presenter_generate_core is not None:
            try:
                core_result = presenter_generate_core(
                    llm=self.llm,
                    sections=sections,
                    frascati_summary=frascati_summary,
                    style_memory_report=None if current_project_only else style_memory_report,
                    ai_detection_report=ai_detection_report,
                    memory_v2_report=None if current_project_only else memory_v2_report,
                    historical_axes=atomic_verrous,
                    cache_dir=self.diagnostic_dir / "cache",
                )
            except Exception as exc:
                presenter_error = str(exc)
                print(f"[EnnoDiagnostic][V323_PRESENTER][WARN] {exc}")
        stage_timings["section_generation"] = round(time.time() - _stage_t0, 3)

        _stage_t0 = time.time()
        cir_memory_report = self.load_cir_memory_report(
            current_verrous=atomic_verrous,
        )
        self._last_cir_memory_report = cir_memory_report
        stage_timings["cir_previous_comparison"] = round(time.time() - _stage_t0, 3)
        memory_v2_usage_report = self.build_memory_v2_usage_report(
            memory_v2_report=memory_v2_report,
            style_memory_report=style_memory_report,
            sections=sections,
        )

        content = ""
        prompt = clean_text(core_result.get("prompt"))
        if core_result and presenter_build_final is not None:
            try:
                static_diagnostic = presenter_build_final(
                    core_result=core_result,
                    sections=sections,
                    frascati_summary=frascati_summary,
                    frascati_justification_result=(core_result.get("section_payloads_by_key") or {}).get("justification_frascati"),
                    memory_v2_usage_report=memory_v2_usage_report,
                    llm_reformulated_verrous=atomic_verrous,
                )
                static_diagnostic["transversal_parent_verrous"] = transversal_parent_verrous
                static_diagnostic["historical_continuity_report"] = historical_continuity_report
                static_diagnostic["scientific_axis_report"] = scientific_axis_report
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
                    ("Étude d'éligibilité", values.get("lecture_frascati")),
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
        print(
            "[EnnoDiagnostic][PERF] "
            + " ".join(f"{name}={value}s" for name, value in stage_timings.items())
            + f" total={elapsed}s",
            flush=True,
        )
        report: Dict[str, Any] = {
            "ok": True,
            "status": "completed",
            "version": "ennodiagnostic_v329_additive_transversal_lock_augmentation",
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
            "historical_continuity_report": historical_continuity_report,
            "scientific_axis_report": scientific_axis_report,
            "cir_memory_report": cir_memory_report,
            "chroma_sections": sections,
            "context_engineering": core_result,
            "inputs_status": {
                "rag_sections_loaded": True,
                "style_memory_available": bool(style_memory_report.get("ok")),
                "memory_v2_available": bool(memory_v2_report.get("ok")),
                "previous_verrou_context_available": bool(previous_verrou_context.get("available")),
                "historical_continuity_available": bool(historical_continuity_report.get("has_previous_cir")),
                "scientific_axis_consolidation_available": bool(scientific_axis_report.get("ok")),
                "previous_cir_available": bool(cir_memory_report.get("has_previous_cir")),
            },
            "telemetry": {
                "elapsed_seconds": elapsed,
                "stage_timings_seconds": stage_timings,
                "retrieval_report": sections.get("_retrieval_report") or {},
                "presenter_error": presenter_error,
                "structured_presenter_loaded": presenter_generate_core is not None,
                "prompt_chars": len(prompt),
                "main_verrous_count": len(llm_reformulated_verrous),
                "main_verrous_before_historical_reconciliation": pre_reconciliation_verrous_count,
                "atomic_verrous_count": len(atomic_verrous),
                "scientific_axis_count": int(scientific_axis_report.get("axis_count") or 0),
                "transversal_parent_verrous_count": len(transversal_parent_verrous),
                "historical_reconciliation_merged_groups": int(historical_continuity_report.get("merged_groups_count") or 0),
                "historical_reconciliation_gap_recovered": int(historical_continuity_report.get("recovered_gap_candidates_count") or 0),
                "previous_verrou_examples_count": int(previous_verrou_context.get("examples_count") or 0),
                "previous_verrou_context_in_prompt": bool(previous_verrou_context.get("available")),
                "previous_verrou_context_factual_use_allowed": False,
                "demarche_llm_review_recommended": bool(
                    (frascati_summary.get("demarche_legibility") or {}).get("llm_review_recommended")
                ),
                "demarche_llm_review_policy": "section_generation_from_atomic_locks_transversal_display_augmentation_only",
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
            f"✅ EnnoDiagnostic V191 terminé | sections={len(diagnostic_sections_by_key)} "
            f"| verrous={len(llm_reformulated_verrous)} | N-1={bool(cir_memory_report.get('has_previous_cir'))} "
            f"| temps={elapsed}s",
            flush=True,
        )
        return report
