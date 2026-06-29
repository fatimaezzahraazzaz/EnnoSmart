# -*- coding: utf-8 -*-
from __future__ import annotations

"""
consultant_verrou_synthesizer.py - V127

Objectif : transformer les preuves NLP/RAG de EnnoDiagnostic en verrous R&D
candidats reformulés proprement pour validation consultant.

V127 : explication contextuelle structurée consultant par verrou + post-traitement métier générique CIR :
- ne complète plus automatiquement une bonne sortie LLM avec des fallbacks RAG ;
- fusionne les méthodes/preuves avec le verrou structurant le plus proche ;
- calcule un score verrou normalisé sur 0..1 ;
- conserve un fallback seulement si le LLM ne produit pas assez de verrous exploitables ;
- ajoute pour chaque verrou une explication contextuelle non générique destinée au consultant ;
- reste générique : aucune règle liée à un projet, organisme ou fichier précis.

Principes :
- pas de codage dur projet / domaine ;
- pas de recalcul Frascati ;
- pas d'invention : chaque verrou garde des preuves sources ;
- les chunks RAG sont de la matière première, pas une sortie finale ;
- la mémoire CIR, si fournie, est seulement un style de rédaction.
"""

import json
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple


# ---------------------------------------------------------------------
# Nettoyage / accès générique aux sources Chroma
# ---------------------------------------------------------------------

def clean_text(value: Any) -> str:
    return str(value or "").strip()


def repair_mojibake(value: Any) -> str:
    s = clean_text(value)
    if not s:
        return ""
    replacements = {
        "Ã©": "é", "Ã¨": "è", "Ãª": "ê", "Ã«": "ë", "Ã ": "à",
        "Ã¢": "â", "Ã§": "ç", "Ã´": "ô", "Ã¹": "ù", "Ã»": "û",
        "Ã®": "î", "Ã¯": "ï", "Ã‰": "É", "â€™": "’", "â€œ": "“",
        "â€\x9d": "”", "â€“": "–", "â€”": "—",
    }
    out = s
    for bad, good in replacements.items():
        out = out.replace(bad, good)
    return out


def truncate(value: Any, max_chars: int = 700) -> str:
    text = repair_mojibake(value)
    text = re.sub(r"[ \t]+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"


def meta_of(src: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(src, dict):
        return {}
    meta = src.get("metadata")
    return meta if isinstance(meta, dict) else {}


def source_text(src: Dict[str, Any]) -> str:
    if not isinstance(src, dict):
        return ""
    return repair_mojibake(
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


def source_role(src: Dict[str, Any]) -> str:
    meta = meta_of(src)
    return clean_text(meta.get("role") or meta.get("final_role") or src.get("role") or "")


def source_pack_key(src: Dict[str, Any]) -> str:
    meta = meta_of(src)
    return clean_text(meta.get("pack_key") or meta.get("source_pack_key") or src.get("pack_key") or "")


def is_universal_reconstruction_source(src: Dict[str, Any]) -> bool:
    if not isinstance(src, dict):
        return False
    meta = meta_of(src)
    joined = " ".join([
        clean_text(meta.get("verrou_source")),
        clean_text(meta.get("theme_id")),
        clean_text(meta.get("rag_chunk_id")),
        clean_text(meta.get("final_role")),
        clean_text(src.get("id")),
        clean_text(src.get("passage_id")),
        source_text(src)[:260],
    ]).lower()
    return (
        "universal_theme_reconstruction" in joined
        or "verrou_implicit_universal" in joined
        or joined.strip().startswith("verrou implicite possible")
    )


# ---------------------------------------------------------------------
# Sélection et déduplication des preuves
# ---------------------------------------------------------------------

_STOPWORDS = {
    "afin", "ainsi", "alors", "avec", "avoir", "cette", "ceux", "dans", "des", "donc", "dont",
    "elle", "elles", "entre", "etre", "être", "fait", "font", "hors", "leur", "leurs", "mais", "meme",
    "même", "nous", "pour", "plus", "sans", "sont", "sous", "tout", "tous", "tres", "très", "une",
    "vers", "voir", "dont", "aussi", "apres", "après", "avant", "comme", "niveau", "source", "sources",
    "document", "documents", "projet", "dossier", "technique", "techniques", "verrou", "verrous", "signal",
    "signaux", "candidat", "candidats", "validation", "consultant", "frascati", "score", "role", "rôle",
    "page", "texte", "resultat", "résultat", "methode", "méthode", "objectif", "limite", "preuve", "preuves",
}

_GENERIC_TITLES = {
    "performance insuffisante sous contrainte",
    "qualite de sortie non conforme",
    "qualité de sortie non conforme",
    "non transferabilite des solutions existantes",
    "non transférabilité des solutions existantes",
    "compromis entre contraintes contradictoires",
    "identification de la cause racine",
    "adaptation a un contexte technique specifique",
    "adaptation à un contexte technique spécifique",
    "fiabilite usure ou degradation en fonctionnement",
    "fiabilité usure ou dégradation en fonctionnement",
    "comportement instable ou non maitrise",
    "comportement instable ou non maîtrisé",
}


def normalize_key(value: Any) -> str:
    s = repair_mojibake(value).lower()
    table = str.maketrans("àâäéèêëîïôöùûüç’'", "aaaeeeeiioouuuc__")
    s = s.translate(table)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def is_generic_title(title: Any) -> bool:
    key = normalize_key(title)
    if not key:
        return True
    if key in {normalize_key(x) for x in _GENERIC_TITLES}:
        return True
    if len(key.split()) <= 2:
        return True
    # Un titre exploitable doit contenir au moins un terme technique spécifique.
    generic_words = {"probleme", "difficulte", "performance", "qualite", "contrainte", "risque", "validation"}
    words = set(key.split())
    return len(words - generic_words) < 2


def token_set(value: Any) -> set[str]:
    s = normalize_key(value)
    return {tok for tok in re.findall(r"[a-z0-9]{3,}", s) if tok not in _STOPWORDS}


def source_blob_for_matching(src: Dict[str, Any]) -> str:
    meta = meta_of(src)
    return "\n".join([
        source_text(src),
        clean_text(meta.get("theme_label")),
        clean_text(meta.get("theme_id")),
        clean_text(meta.get("technical_signature")),
        clean_text(meta.get("section_title")),
        clean_text(meta.get("pack_key")),
    ])


def source_similarity(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    ta = token_set(source_blob_for_matching(a))
    tb = token_set(source_blob_for_matching(b))
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    return inter / max(1, min(len(ta), len(tb)))


def evidence_score(src: Dict[str, Any]) -> float:
    meta = meta_of(src)
    score = 0.0
    role = normalize_key(source_role(src))
    pack = normalize_key(source_pack_key(src))
    text = source_text(src)

    if not is_universal_reconstruction_source(src):
        score += 100.0
    if "verrou" in role:
        score += 30.0
    if "verrous rnd locaux" in pack or "limites locales" in pack:
        score += 25.0
    if source_doc(src):
        score += 5.0
    if len(text) > 120:
        score += min(len(text), 1200) / 120.0

    for key, weight in [
        ("frascati_score", 8.0),
        ("rank_score", 3.0),
        ("confidence", 3.0),
        ("verrou_score", 5.0),
    ]:
        try:
            score += float(meta.get(key) or 0) * weight
        except Exception:
            pass
    return score


def collect_candidate_sources(sections: Dict[str, List[Dict[str, Any]]], max_items: int = 40) -> List[Dict[str, Any]]:
    if not isinstance(sections, dict):
        return []

    preferred_keys = [
        "verrous",
        "limites",
        "axe_problemes_transverses",
        "axe_contraintes_transverses",
        "methodes",
        "resultats",
        "parametres",
        "objectifs",
        "axe_preuves_resultats",
    ]

    raw: List[Dict[str, Any]] = []
    for key in preferred_keys:
        values = sections.get(key) or []
        if isinstance(values, list):
            raw.extend([v for v in values if isinstance(v, dict) and source_text(v)])

    # Déduplication textuelle stable.
    seen = set()
    deduped: List[Dict[str, Any]] = []
    for src in sorted(raw, key=evidence_score, reverse=True):
        txt = source_text(src)
        doc = source_doc(src)
        key = (normalize_key(doc), normalize_key(txt[:260]))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(src)

    # Si on a des preuves spécifiques, on écarte les reconstructions universelles.
    specific = [s for s in deduped if not is_universal_reconstruction_source(s)]
    pool = specific if len(specific) >= 3 else deduped
    return pool[:max_items]


def cluster_sources(sources: List[Dict[str, Any]], max_groups: int = 8) -> List[List[Dict[str, Any]]]:
    groups: List[List[Dict[str, Any]]] = []

    for src in sources:
        placed = False
        best_i = -1
        best_score = 0.0
        for i, group in enumerate(groups):
            sim = max(source_similarity(src, other) for other in group[:4])
            if sim > best_score:
                best_score = sim
                best_i = i

        if best_i >= 0 and best_score >= 0.26:
            groups[best_i].append(src)
            placed = True

        if not placed:
            groups.append([src])

    # Fusion légère des groupes très proches après un premier passage.
    merged = True
    while merged:
        merged = False
        for i in range(len(groups)):
            if merged:
                break
            for j in range(i + 1, len(groups)):
                sim = max(source_similarity(a, b) for a in groups[i][:4] for b in groups[j][:4])
                if sim >= 0.34:
                    groups[i].extend(groups[j])
                    del groups[j]
                    merged = True
                    break

    groups = [sorted(g, key=evidence_score, reverse=True)[:5] for g in groups]
    groups = sorted(groups, key=lambda g: sum(evidence_score(s) for s in g), reverse=True)
    return groups[:max_groups]


# ---------------------------------------------------------------------
# Titrage fallback générique sans codage dur projet
# ---------------------------------------------------------------------

def title_from_source(src: Dict[str, Any], max_chars: int = 180) -> str:
    text = repair_mojibake(source_text(src))
    meta = meta_of(src)

    candidates = [
        clean_text(meta.get("llm_title")),
        clean_text(meta.get("verrou_title")),
        clean_text(meta.get("theme_label")) if not is_universal_reconstruction_source(src) else "",
        clean_text(meta.get("section_title")),
    ]

    patterns = [
        r"V\s*\d+\s*\|\s*([^|:\n]{8,150})(?:\s*:\s*([^|\n.]{8,220}))?",
        r"Verrou\s*(?:R&D|scientifique|technique)?\s*\d*\s*[:\-–—]\s*([^\n.]{12,220})",
        r"OBJ\s*\d+\s*[-–—:]\s*([^\n.]{12,220})",
        r"P\s*\d+(?:\.\d+)*\s+([^:\n]{8,150})(?:\s*:\s*([^\n.]{8,220}))?",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            part1 = clean_text(match.group(1) or "")
            part2 = clean_text(match.group(2) or "") if match.lastindex and match.lastindex >= 2 else ""
            candidates.append(f"{part1} : {part2}" if part2 else part1)

    for line in re.split(r"[\n.!?]+", text):
        line = re.sub(r"^[-*•\d.)\s]+", "", clean_text(line))
        if len(line) >= 18:
            candidates.append(line)
            break

    for candidate in candidates:
        candidate = clean_text(candidate).strip(" |:-–—")
        candidate = re.sub(r"^Verrou implicite possible\s*[—–:-]\s*", "", candidate, flags=re.I)
        candidate = re.sub(r"^Question de qualification\s*:\s*", "", candidate, flags=re.I)
        if candidate and not is_generic_title(candidate):
            return truncate(candidate, max_chars)

    toks = []
    for src_tok in token_set(text):
        if len(src_tok) > 3:
            toks.append(src_tok)
        if len(toks) >= 6:
            break
    if toks:
        return truncate("Incertitude technique sur " + " ".join(toks[:6]), max_chars)
    return "Verrou R&D candidat à reformuler"


def group_fallback_title(group: List[Dict[str, Any]]) -> str:
    for src in group:
        title = title_from_source(src)
        if title and not is_generic_title(title):
            return title
    return "Verrou R&D candidat à reformuler"


def normalize_score_value(value: Any) -> Optional[float]:
    """Normalise les scores hétérogènes du pipeline vers 0..1.

    Le NLP/RAG peut fournir :
    - frascati_score déjà en 0..1 ;
    - rank_score autour de 0..2 ;
    - pourcentage 0..100 ;
    - petites valeurs de ranking inutilisables.

    Le frontend affiche ensuite formatScore(score).
    """
    try:
        v = float(value)
    except Exception:
        return None

    if not (v == v) or v <= 0:
        return None

    if 0 < v <= 1:
        return round(v, 4)

    # Certains rank_score de regroupement sont sur une échelle proche de 0..2.
    if 1 < v <= 2.5:
        return round(min(v / 2.0, 1.0), 4)

    # Scores déjà exprimés en pourcentage.
    if 2.5 < v <= 100:
        return round(min(v / 100.0, 1.0), 4)

    return 1.0


def _score_from_meta(meta: Dict[str, Any]) -> Optional[float]:
    # Priorité aux vrais scores verrou/Frascati si présents.
    for key in [
        "verrou_score",
        "frascati_score",
        "cir_score",
        "eligibility_score",
        "confidence",
        "score",
        "rank_score",
    ]:
        value = normalize_score_value(meta.get(key))
        if value is not None:
            return value
    return None


def _avg_score(group: List[Dict[str, Any]], frascati_summary: Dict[str, Any]) -> Optional[float]:
    scores: List[float] = []

    for src in group:
        meta = meta_of(src)
        value = _score_from_meta(meta)
        if value is not None:
            scores.append(value)

    if scores:
        # On évite les 1%/2% liés à de petits scores de ranking non significatifs.
        avg = sum(scores) / len(scores)
        if avg >= 0.05:
            return round(avg, 4)

    for key in ["average_frascati_score", "global_frascati_score", "score"]:
        value = normalize_score_value((frascati_summary or {}).get(key))
        if value is not None:
            return value

    return None


def _main_decision(group: List[Dict[str, Any]]) -> str:
    counts: Dict[str, int] = {}
    for src in group:
        meta = meta_of(src)
        d = clean_text(meta.get("frascati_decision") or meta.get("decision") or meta.get("verrou_candidate_level"))
        if d:
            counts[d] = counts.get(d, 0) + 1
    if not counts:
        return "verrou_a_verifier"
    return sorted(counts.items(), key=lambda x: (-x[1], x[0]))[0][0]


def tag_from_score(score: Optional[float], decision: str = "") -> str:
    if decision and "rej" in normalize_key(decision):
        return "FAIBLE POUR CIR"
    try:
        if score is not None and float(score) >= 0.68:
            return "PERTINENT POUR CIR"
        if score is not None and float(score) >= 0.45:
            return "MOYEN POUR CIR"
    except Exception:
        pass
    return "À VÉRIFIER"


# ---------------------------------------------------------------------
# V125 - Post-traitement métier générique CIR
# ---------------------------------------------------------------------

_LOCK_ARCHETYPES: Dict[str, List[str]] = {
    # Ces archétypes sont génériques. Ils ne sont pas liés à un projet précis.
    "integration_systeme": [
        "integration", "intégration", "compatibilite", "compatibilité", "systeme", "système",
        "configuration", "assemblage", "interface", "transposabilite", "transposabilité",
        "ouvrage", "echelle", "échelle", "multi contrainte", "multi-contrainte",
    ],
    "durabilite_stabilite": [
        "durabilite", "durabilité", "stabilite", "stabilité", "vieillissement", "long terme",
        "tassement", "degradation", "dégradation", "usure", "deriv", "dériv", "pérenn", "perenn",
    ],
    "comportement_physique": [
        "therm", "hygro", "humid", "diffusiv", "effusiv", "inertie", "dephas", "déphas",
        "confort", "temperature", "température", "condensation", "fongique", "moisiss",
    ],
    "mecanique_structure": [
        "mecan", "mécan", "structure", "charge", "effort", "seisme", "séisme", "vent",
        "vibrat", "ductil", "connexion", "connecteur", "goujon", "ruine", "diaphragme",
    ],
    "feu_securite": [
        "feu", "incendie", "rei", "reaction au feu", "réaction au feu", "resistance au feu",
        "résistance au feu", "propagation", "securite", "sécurité",
    ],
    "acoustique_vibratoire": [
        "acoust", "bruit", "reverber", "réverbér", "affaiblissement", "vibrat",
        "choc", "impact", "isolement",
    ],
    "reglementaire_validation": [
        "norm", "reglement", "règlement", "referentiel", "référentiel", "eurocode",
        "atex", "validation", "justification", "controle", "contrôle", "label", "carbone",
        "environnement", "durabilite", "durabilité",
    ],
    "procede_mise_echelle": [
        "procede", "procédé", "prefabric", "préfabric", "industrialisation", "mise en œuvre",
        "mise en oeuvre", "fabrication", "insufflation", "calepinage", "protocole", "essai",
    ],
}

_METHOD_ONLY_MARKERS = [
    "procedure de", "procédure de", "procedures de", "procédures de",
    "protocole de", "protocoles de", "suivi de", "suivi ",
    "controle de", "contrôle de", "campagne de", "essai de", "essais de",
    "mesure de", "mesures de", "simulation de", "modelisation de", "modélisation de",
]

_LOCK_MARKERS = [
    "incertitude", "verrou", "maitrise", "maîtrise", "comportement", "stabilite", "stabilité",
    "resistance", "résistance", "validation", "justification", "optimisation", "compatibilite",
    "compatibilité", "impossibilite", "impossibilité", "non resolu", "non résolu",
    "absence de", "manque de", "limite", "risque", "capacite", "capacité",
]

_FALLBACK_MARKERS = [
    "signal technique candidat extrait des preuves rag/nlp",
    "la reformulation llm dediee n a pas produit de json exploitable",
    "la reformulation llm dédiée n a pas produit de json exploitable",
    "fallback grouped rag verrou synthesis",
]


def archetype_scores(text: Any) -> Dict[str, int]:
    key = normalize_key(text)
    scores: Dict[str, int] = {}
    for name, words in _LOCK_ARCHETYPES.items():
        score = 0
        for word in words:
            w = normalize_key(word)
            if w and w in key:
                score += 1
        if score:
            scores[name] = score
    return scores


def main_archetype(text: Any) -> str:
    scores = archetype_scores(text)
    if not scores:
        return "incertitude_technique"
    return sorted(scores.items(), key=lambda x: (-x[1], x[0]))[0][0]


def is_fallback_like_text(text: Any) -> bool:
    key = normalize_key(text)
    return any(normalize_key(marker) in key for marker in _FALLBACK_MARKERS)


def is_method_or_evidence_only(item: Dict[str, Any]) -> bool:
    title = clean_text(item.get("title"))
    body = clean_text(item.get("justification") or item.get("text"))
    key_title = normalize_key(title)
    key_all = normalize_key(title + " " + body)

    if not key_title:
        return False

    has_method_marker = any(normalize_key(m) in key_title for m in _METHOD_ONLY_MARKERS)
    has_lock_marker_in_title = any(normalize_key(m) in key_title for m in _LOCK_MARKERS)
    has_lock_marker_in_body = any(normalize_key(m) in key_all for m in _LOCK_MARKERS)

    # Un protocole/essai/suivi seul devient preuve, sauf si le titre porte déjà
    # explicitement une incertitude ou une capacité technique à valider.
    return has_method_marker and not has_lock_marker_in_title and has_lock_marker_in_body


def merge_sources_payload(a: Dict[str, Any], b: Dict[str, Any], max_sources: int = 8) -> List[Dict[str, Any]]:
    sources: List[Dict[str, Any]] = []
    for item in [a, b]:
        src_json = item.get("source_json") if isinstance(item.get("source_json"), dict) else {}
        raw_sources = src_json.get("sources") if isinstance(src_json.get("sources"), list) else []
        for src in raw_sources:
            if not isinstance(src, dict):
                continue
            sig = (normalize_key(src.get("document")), normalize_key(src.get("text")[:180] if src.get("text") else ""))
            if not any((normalize_key(x.get("document")), normalize_key(x.get("text")[:180] if x.get("text") else "")) == sig for x in sources):
                sources.append(src)
            if len(sources) >= max_sources:
                return sources
    return sources


def merge_evidence_into_lock(lock: Dict[str, Any], evidence: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(lock)
    src_json = dict(out.get("source_json") or {})

    evidence_title = clean_text(evidence.get("title"))
    evidence_summary = clean_text(evidence.get("evidence_summary") or evidence.get("justification") or evidence.get("text"))

    existing_evidence = clean_text(out.get("evidence_summary") or src_json.get("evidence_summary"))
    if evidence_title or evidence_summary:
        addition = truncate(f"{evidence_title}. {evidence_summary}".strip(), 600)
        if addition and normalize_key(addition) not in normalize_key(existing_evidence):
            merged_evidence = truncate((existing_evidence + "\n" + addition).strip(), 1200)
            out["evidence_summary"] = merged_evidence
            src_json["evidence_summary"] = merged_evidence

    src_json["sources"] = merge_sources_payload(lock, evidence)
    src_json.setdefault("merged_method_or_evidence", [])
    if isinstance(src_json["merged_method_or_evidence"], list):
        src_json["merged_method_or_evidence"].append({
            "title": evidence_title,
            "justification": truncate(evidence_summary, 600),
            "source": evidence.get("source"),
        })

    # Score = max/avg simple conservateur, toujours 0..1.
    scores = [normalize_score_value(out.get("score")), normalize_score_value(evidence.get("score"))]
    scores = [s for s in scores if s is not None]
    if scores:
        out["score"] = round(max(scores), 4)
        src_json["frascati_score"] = out["score"]

    out["source_json"] = src_json
    out["justification"] = "\n".join([
        clean_text(out.get("scientific_lock")),
        clean_text(out.get("why_not_simple_engineering")),
        clean_text(out.get("evidence_summary") or src_json.get("evidence_summary")),
    ]).strip() or out.get("justification")
    out["text"] = out["justification"]
    return out


def item_similarity(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    text_a = " ".join([
        clean_text(a.get("title")),
        clean_text(a.get("scientific_lock")),
        clean_text(a.get("evidence_summary")),
        clean_text(a.get("justification")),
    ])
    text_b = " ".join([
        clean_text(b.get("title")),
        clean_text(b.get("scientific_lock")),
        clean_text(b.get("evidence_summary")),
        clean_text(b.get("justification")),
    ])
    ta, tb = token_set(text_a), token_set(text_b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(1, min(len(ta), len(tb)))


def postprocess_final_verrous(items: List[Dict[str, Any]], max_verrous: int = 8, drop_fallback_like: bool = True) -> List[Dict[str, Any]]:
    """Nettoie la liste finale sans règle projet-spécifique.

    - supprime les fallbacks si on travaille sur une sortie LLM ;
    - fusionne méthodes/preuves avec le verrou structurant le plus proche ;
    - déduplique par proximité sémantique ;
    - normalise les scores sur 0..1.
    """
    cleaned: List[Dict[str, Any]] = []
    evidence_only: List[Dict[str, Any]] = []

    for raw in items:
        if not isinstance(raw, dict):
            continue

        item = dict(raw)
        title = clean_text(item.get("title"))
        body = clean_text(item.get("justification") or item.get("text"))
        if not title or is_generic_title(title):
            continue
        if drop_fallback_like and is_fallback_like_text(title + " " + body):
            continue

        score = normalize_score_value(item.get("score"))
        item["score"] = score
        src_json = dict(item.get("source_json") or {})
        src_json["frascati_score"] = score
        src_json["lock_archetype"] = main_archetype(title + " " + body)
        item["source_json"] = src_json

        if is_method_or_evidence_only(item):
            evidence_only.append(item)
        else:
            cleaned.append(item)

    # Fusion des méthodes/preuves dans le verrou le plus proche.
    for evidence in evidence_only:
        if not cleaned:
            cleaned.append(evidence)
            continue
        e_arch = evidence.get("source_json", {}).get("lock_archetype")
        best_i = -1
        best_score = 0.0
        for i, lock in enumerate(cleaned):
            l_arch = lock.get("source_json", {}).get("lock_archetype")
            sim = item_similarity(evidence, lock)
            if e_arch and l_arch and e_arch == l_arch:
                sim += 0.20
            if sim > best_score:
                best_i = i
                best_score = sim

        if best_i >= 0 and best_score >= 0.33:
            cleaned[best_i] = merge_evidence_into_lock(cleaned[best_i], evidence)
        else:
            cleaned.append(evidence)

    # Déduplication/fusion des verrous très proches.
    final: List[Dict[str, Any]] = []
    for item in cleaned:
        merged = False
        for i, prev in enumerate(final):
            same_arch = prev.get("source_json", {}).get("lock_archetype") == item.get("source_json", {}).get("lock_archetype")
            sim = item_similarity(prev, item)
            if sim >= 0.55 or (same_arch and sim >= 0.38):
                final[i] = merge_evidence_into_lock(prev, item)
                merged = True
                break
        if not merged:
            final.append(item)

    # Priorité : preuves fortes, score disponible, formulations issues LLM.
    def order_key(item: Dict[str, Any]) -> Tuple[float, int, int]:
        score = normalize_score_value(item.get("score")) or 0.0
        src_json = item.get("source_json") if isinstance(item.get("source_json"), dict) else {}
        sources = src_json.get("sources") if isinstance(src_json.get("sources"), list) else []
        llm_bonus = 1 if "llm" in normalize_key(item.get("source")) else 0
        return (score, len(sources), llm_bonus)

    final = sorted(final, key=order_key, reverse=True)[:max_verrous]

    for item in final:
        item["tag_cir"] = tag_from_score(normalize_score_value(item.get("score")), item.get("frascati_decision") or "")
        item["consultant_status"] = item.get("consultant_status") or "en_attente"
        item["needs_human_validation"] = True

    return final


def evidence_payload(group: List[Dict[str, Any]], max_sources: int = 4) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for src in group[:max_sources]:
        out.append({
            "document": source_doc(src),
            "source_path": source_path(src),
            "role": source_role(src),
            "pack_key": source_pack_key(src),
            "text": truncate(source_text(src), 900),
            "metadata": meta_of(src),
        })
    return out


# ---------------------------------------------------------------------
# Prompt LLM dédié à la reformulation des verrous
# ---------------------------------------------------------------------

def _source_id(group_index: int, source_index: int) -> str:
    return f"G{group_index}.S{source_index}"


def build_llm_prompt_for_groups(
    groups: List[List[Dict[str, Any]]],
    frascati_summary: Dict[str, Any],
    style_block: str = "",
    max_verrous: int = 8,
    min_verrous: int = 4,
) -> Tuple[str, Dict[str, Dict[str, Any]]]:
    source_map: Dict[str, Dict[str, Any]] = {}
    lines: List[str] = []

    lines.append("Tu es consultant CIR senior spécialisé dans la qualification de verrous R&D.")
    lines.append("Ta mission : transformer des preuves NLP/RAG brutes en verrous R&D candidats propres.")
    lines.append("")
    lines.append("Règles absolues :")
    lines.append("- N'invente aucun fait, aucune valeur, aucun document, aucune norme.")
    lines.append("- Chaque verrou doit être relié à au moins une preuve source fournie.")
    lines.append("- Ne copie pas les chunks comme titres : fusionne les preuves proches.")
    lines.append("- Ne crée pas un verrou par chunk. Plusieurs chunks proches doivent devenir un seul verrou CIR structurant.")
    lines.append("- Le titre doit suivre : objet technique + phénomène/incertitude + contrainte ou condition d’usage.")
    lines.append("- Ne garde pas de titres génériques comme performance insuffisante, qualité non conforme, cause racine, non-transférabilité.")
    lines.append("- Un protocole, un essai, un suivi, une mesure ou une simulation ne doit pas devenir un verrou seul : rattache-le au verrou technique qu’il permet de lever.")
    lines.append("- Un résultat chiffré ou une exigence réglementaire seule n'est pas un verrou : formule l'incertitude technique qu'il faut démontrer.")
    lines.append("- Remonte au bon niveau CIR : assez global pour couvrir plusieurs preuves, mais pas vague.")
    lines.append("- Ne complète pas artificiellement la liste avec des signaux faibles ou des chunks bruts.")
    lines.append("- Ne valide rien : tous les verrous restent candidats à valider par le consultant et EnnoScholar.")
    lines.append("- Pour chaque verrou, rédige consultant_explanation : 2 ou 3 phrases contextualisées qui expliquent pourquoi EnnoDiagnostic l’a détecté comme verrou dans CE projet. Ne répète pas scientific_lock, why_not_simple_engineering et evidence_summary en bloc.")
    lines.append("- scientific_lock doit formuler l’incertitude technique sous forme de question ou de problème à résoudre.")
    lines.append("- why_not_simple_engineering doit expliquer pourquoi les documents du projet ne montrent pas une simple application standard suffisante. Évite les affirmations absolues sur l’état de l’art.")
    lines.append("- evidence_summary doit citer les indices sources utilisés, sans inventer de données.")
    lines.append("- N’écris pas 'aucun procédé existant' ou 'aucune solution existante'. Écris plutôt : 'les documents fournis ne montrent pas de solution directement applicable'.")
    lines.append("- La mémoire de style, si présente, sert seulement au ton, jamais aux faits.")
    lines.append("")
    lines.append("Archétypes CIR génériques possibles, à utiliser seulement s'ils ressortent des preuves :")
    lines.append("- intégration système / compatibilité multi-contraintes ; stabilité / durabilité ; comportement physique ; mécanique/structure ; feu/sécurité ; acoustique/vibratoire ; validation réglementaire/environnementale ; procédé/mise à l’échelle.")
    lines.append("")
    lines.append(f"Produis entre {min_verrous} et {max_verrous} verrous si les preuves le permettent. Si seulement 5 ou 6 verrous forts existent, retourne seulement 5 ou 6 verrous.")
    lines.append("Retourne uniquement un JSON valide, sans Markdown, avec cette forme exacte :")
    lines.append('{"llm_reformulated_verrous":[{"title":"...","scientific_lock":"Question/problème technique contextualisé à résoudre.","why_not_simple_engineering":"Pourquoi les documents ne montrent pas une solution standard directement applicable.","evidence_summary":"Indices sources utilisés par l’agent.","consultant_explanation":"Pourquoi EnnoDiagnostic le détecte comme verrou dans ce projet, sans répéter tous les autres champs.","source_ids":["G1.S1"],"risk_level":"moyen"}]}')
    lines.append("")
    lines.append("Résumé Frascati déjà calculé, à reprendre sans recalcul :")
    lines.append(json.dumps(frascati_summary or {}, ensure_ascii=False, indent=2))
    lines.append("")

    if style_block:
        lines.append("Mémoire de style CIR courte, uniquement pour le style :")
        lines.append(truncate(style_block, 900))
        lines.append("")

    lines.append("Preuves regroupées par proximité sémantique :")
    for gi, group in enumerate(groups, start=1):
        lines.append(f"\n### Groupe {gi}")
        for si, src in enumerate(group[:5], start=1):
            sid = _source_id(gi, si)
            source_map[sid] = src
            meta = meta_of(src)
            lines.append(
                f"- {sid} | document={source_doc(src) or '-'} | rôle={source_role(src) or '-'} | "
                f"pack={source_pack_key(src) or '-'} | frascati={meta.get('frascati_decision') or meta.get('decision') or '-'} | "
                f"score={meta.get('frascati_score') or meta.get('score') or '-'}\n"
                f"  Texte : {truncate(source_text(src), 620)}"
            )

    prompt = "\n".join(lines)
    return prompt, source_map


def parse_llm_json(value: Any) -> Dict[str, Any]:
    text = repair_mojibake(value)
    if not text:
        return {}
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I).strip()
    text = re.sub(r"\s*```$", "", text).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        pass
    # Récupération si le modèle ajoute du texte autour du JSON.
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            data = json.loads(m.group(0))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def sanitize_explanation_text(value: str) -> str:
    text = clean_text(value)
    if not text:
        return ""
    replacements = [
        ("Aucun procédé existant ne garantit", "Les documents fournis ne montrent pas de solution directement applicable garantissant"),
        ("aucun procédé existant ne garantit", "les documents fournis ne montrent pas de solution directement applicable garantissant"),
        ("Aucune solution existante ne garantit", "Les documents fournis ne montrent pas de solution directement applicable garantissant"),
        ("aucune solution existante ne garantit", "les documents fournis ne montrent pas de solution directement applicable garantissant"),
        ("Aucun procédé existant n'est démontré", "Les documents fournis ne démontrent pas de procédé directement applicable"),
        ("aucun procédé existant n'est démontré", "les documents fournis ne démontrent pas de procédé directement applicable"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    return re.sub(r"\s+", " ", text).strip()


def build_contextual_consultant_explanation(
    title: str,
    scientific_lock: str,
    why: str,
    evidence_summary: str,
    selected_sources: List[Dict[str, Any]],
) -> str:
    """
    Explication lisible par le consultant : pourquoi l'agent considère ce point
    comme un verrou R&D dans le contexte du projet.

    Cette fonction ne doit pas inventer : elle reformule uniquement le titre,
    l'incertitude, la raison non-standard et les preuves fournies.
    """
    docs: List[str] = []
    for src in selected_sources[:4]:
        doc = source_doc(src)
        if doc and doc not in docs:
            docs.append(doc)

    parts: List[str] = []
    intro = f"EnnoDiagnostic identifie ce point comme un verrou car les sources du projet font apparaître une incertitude technique autour de : {title}."
    if docs:
        intro += f" Les indices proviennent notamment de : {' ; '.join(docs[:3])}."
    parts.append(intro)

    if len(parts) == 1 and selected_sources:
        parts.append(f"Le passage source principal indique : {truncate(source_text(selected_sources[0]), 420)}")

    return truncate(sanitize_explanation_text(" ".join(parts)), 900)


def _candidate_from_llm_item(
    item: Dict[str, Any],
    index: int,
    source_map: Dict[str, Dict[str, Any]],
    groups: List[List[Dict[str, Any]]],
    frascati_summary: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    title = truncate(clean_text(item.get("title")), 260)
    raw_blob = " ".join([
        title,
        clean_text(item.get("scientific_lock")),
        clean_text(item.get("why_not_simple_engineering")),
        clean_text(item.get("evidence_summary")),
        clean_text(item.get("consultant_explanation")),
        clean_text(item.get("agent_reasoning")),
        clean_text(item.get("why_agent_found_verrou")),
    ])
    if is_generic_title(title) or is_fallback_like_text(raw_blob):
        return None

    source_ids = item.get("source_ids")
    if not isinstance(source_ids, list):
        source_ids = []

    selected_sources: List[Dict[str, Any]] = []
    for sid in source_ids:
        src = source_map.get(str(sid))
        if src and src not in selected_sources:
            selected_sources.append(src)

    if not selected_sources and index <= len(groups):
        selected_sources = groups[index - 1][:4]
    if not selected_sources:
        return None

    score = _avg_score(selected_sources, frascati_summary)
    decision = _main_decision(selected_sources)
    sources = evidence_payload(selected_sources)
    docs = []
    for src in selected_sources:
        doc = source_doc(src)
        if doc and doc not in docs:
            docs.append(doc)

    scientific_lock = truncate(sanitize_explanation_text(item.get("scientific_lock")), 900)
    why = truncate(sanitize_explanation_text(item.get("why_not_simple_engineering")), 900)
    evidence_summary = truncate(sanitize_explanation_text(item.get("evidence_summary")), 900)
    consultant_explanation = truncate(
        sanitize_explanation_text(
            item.get("consultant_explanation")
            or item.get("agent_reasoning")
            or item.get("why_agent_found_verrou")
        ),
        900,
    )
    if not consultant_explanation:
        consultant_explanation = build_contextual_consultant_explanation(
            title=title,
            scientific_lock=scientific_lock,
            why=why,
            evidence_summary=evidence_summary,
            selected_sources=selected_sources,
        )

    justification_parts = [p for p in [consultant_explanation, scientific_lock, why, evidence_summary] if p]
    justification = "\n".join(justification_parts) or truncate(source_text(selected_sources[0]), 900)

    return {
        "title": title,
        "tag_cir": tag_from_score(score, decision),
        "score": score,
        "frascati_decision": decision,
        "consultant_status": "en_attente",
        "document": "; ".join(docs[:6]) or "Sources Chroma à vérifier",
        "justification": justification,
        "text": justification,
        "scientific_lock": scientific_lock,
        "why_not_simple_engineering": why,
        "evidence_summary": evidence_summary,
        "consultant_explanation": consultant_explanation,
        "agent_reasoning": consultant_explanation,
        "why_agent_found_verrou": consultant_explanation,
        "source": "llm_grouped_rag_verrou_synthesis",
        "needs_human_validation": True,
        "source_json": {
            "source": "llm_grouped_rag_verrou_synthesis",
            "llm_title": title,
            "llm_block": justification,
            "scientific_lock": scientific_lock,
            "why_not_simple_engineering": why,
            "evidence_summary": evidence_summary,
            "consultant_explanation": consultant_explanation,
            "agent_reasoning": consultant_explanation,
            "why_agent_found_verrou": consultant_explanation,
            "risk_level": item.get("risk_level") or "moyen",
            "source_ids": source_ids,
            "sources": sources,
            "frascati_decision": decision,
            "frascati_score": score,
            "lock_archetype": main_archetype(title + " " + justification),
            "principle": "Titre et formulation produits par LLM à partir de groupes de preuves RAG ; preuves et scores issus de Chroma/NLP/Frascati.",
        },
    }


def fallback_candidates_from_groups(groups: List[List[Dict[str, Any]]], frascati_summary: Dict[str, Any], max_verrous: int = 8) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()
    for group in groups[:max_verrous]:
        title = group_fallback_title(group)
        if is_generic_title(title):
            continue
        key = normalize_key(title)
        if key in seen:
            continue
        seen.add(key)
        score = _avg_score(group, frascati_summary)
        decision = _main_decision(group)
        sources = evidence_payload(group)
        docs = []
        for src in group:
            doc = source_doc(src)
            if doc and doc not in docs:
                docs.append(doc)
        evidence = truncate(" ".join(source_text(src) for src in group[:3]), 1000)
        consultant_explanation = build_contextual_consultant_explanation(
            title=title,
            scientific_lock=evidence,
            why="les preuves regroupées signalent une difficulté technique ou une incertitude à valider, mais la reformulation LLM dédiée n’a pas produit de JSON exploitable",
            evidence_summary=evidence,
            selected_sources=group,
        )
        justification = (
            "Signal technique candidat extrait des preuves RAG/NLP. "
            "Le titre reste à consolider par le consultant car la reformulation LLM dédiée n'a pas produit de JSON exploitable.\n"
            + consultant_explanation
        )
        out.append({
            "title": title,
            "tag_cir": tag_from_score(score, decision),
            "score": score,
            "frascati_decision": decision,
            "consultant_status": "en_attente",
            "document": "; ".join(docs[:6]) or "Sources Chroma à vérifier",
            "justification": justification,
            "text": justification,
            "consultant_explanation": consultant_explanation,
            "agent_reasoning": consultant_explanation,
            "why_agent_found_verrou": consultant_explanation,
            "source": "fallback_grouped_rag_verrou_synthesis",
            "needs_human_validation": True,
            "source_json": {
                "source": "fallback_grouped_rag_verrou_synthesis",
                "llm_title": title,
                "llm_block": justification,
                "consultant_explanation": consultant_explanation,
                "agent_reasoning": consultant_explanation,
                "why_agent_found_verrou": consultant_explanation,
                "sources": sources,
                "frascati_decision": decision,
                "frascati_score": score,
                "principle": "Fallback déterministe sans codage dur projet : regroupement sémantique des preuves RAG.",
            },
        })
    return out


def dedupe_final_verrous(items: List[Dict[str, Any]], max_verrous: int = 8) -> List[Dict[str, Any]]:
    # Conservé pour compatibilité avec d'anciens imports.
    return postprocess_final_verrous(items, max_verrous=max_verrous, drop_fallback_like=False)


# ---------------------------------------------------------------------
# API principale appelée par ennodiagnostic_agent.py
# ---------------------------------------------------------------------

def synthesize_consultant_verrous(
    sections: Dict[str, List[Dict[str, Any]]],
    frascati_summary: Dict[str, Any],
    llm: Any = None,
    style_block: str = "",
    max_verrous: int = 8,
    min_verrous: int = 4,
) -> Dict[str, Any]:
    sources = collect_candidate_sources(sections, max_items=48)
    groups = cluster_sources(sources, max_groups=max_verrous)

    if not groups:
        return {
            "ok": False,
            "mode": "no_sources",
            "llm_reformulated_verrous": [],
            "message": "Aucune preuve RAG/NLP exploitable pour reformuler des verrous.",
        }

    source_map: Dict[str, Dict[str, Any]] = {}
    llm_items: List[Dict[str, Any]] = []
    mode = "fallback_grouped_rag"
    error = None
    prompt_chars = 0

    if llm is not None:
        try:
            prompt, source_map = build_llm_prompt_for_groups(
                groups=groups,
                frascati_summary=frascati_summary,
                style_block=style_block,
                max_verrous=max_verrous,
                min_verrous=min_verrous,
            )
            prompt_chars = len(prompt)
            raw = llm.generate(
                prompt,
                temperature=0.05,
                max_output_tokens=2200,
                retries=1,
            )
            parsed = parse_llm_json(raw)
            raw_items = parsed.get("llm_reformulated_verrous")
            if isinstance(raw_items, list):
                for idx, item in enumerate(raw_items, start=1):
                    if isinstance(item, dict):
                        cand = _candidate_from_llm_item(item, idx, source_map, groups, frascati_summary)
                        if cand:
                            llm_items.append(cand)
            if llm_items:
                mode = "llm_grouped_json"
        except Exception as exc:
            error = str(exc)
            llm_items = []
            mode = "fallback_after_llm_error"

    fallback_items = fallback_candidates_from_groups(groups, frascati_summary, max_verrous=max_verrous)

    # V125 : si le LLM a produit assez de verrous exploitables, on ne complète PAS
    # avec des fallbacks RAG. Cela évite les faux verrous 7/8 issus de chunks bruts.
    llm_final = postprocess_final_verrous(llm_items, max_verrous=max_verrous, drop_fallback_like=True)
    fallback_used = False

    if len(llm_final) >= min_verrous:
        final = llm_final
    elif llm_final:
        fallback_used = True
        final = postprocess_final_verrous(llm_final + fallback_items, max_verrous=max_verrous, drop_fallback_like=False)
    else:
        fallback_used = True
        final = postprocess_final_verrous(fallback_items, max_verrous=max_verrous, drop_fallback_like=False)

    final_mode = mode if llm_final and not fallback_used else ("llm_with_fallback_completion" if llm_final else "fallback_grouped_rag")

    return {
        "ok": bool(final),
        "mode": final_mode,
        "error": error,
        "prompt_chars": prompt_chars,
        "sources_count": len(sources),
        "groups_count": len(groups),
        "llm_candidates_count": len(llm_items),
        "llm_clean_count": len(llm_final),
        "fallback_candidates_count": len(fallback_items),
        "fallback_used": fallback_used,
        "final_count": len(final),
        "llm_reformulated_verrous": final,
        "principle": (
            "Les chunks RAG/NLP sont regroupés puis reformulés en verrous R&D candidats. "
            "V127 ne complète pas une bonne sortie LLM par des chunks fallback. "
            "Chaque verrou reçoit une explication contextuelle destinée au consultant. "
            "Les méthodes/preuves sont fusionnées avec les verrous structurants et les scores sont normalisés sur 0..1."
        ),
    }


def build_verrous_markdown(verrous: List[Dict[str, Any]], title: str = "Signaux de verrous R&D candidats") -> str:
    lines = [f"## {title}"]
    if not verrous:
        lines.append("Aucun verrou candidat reformulé disponible.")
        return "\n".join(lines)

    for i, item in enumerate(verrous, start=1):
        item_title = clean_text(item.get("title")) or f"Verrou candidat {i}"
        score = item.get("score")
        decision = item.get("frascati_decision") or "à vérifier"
        scientific_lock = clean_text(item.get("scientific_lock"))
        why = clean_text(item.get("why_not_simple_engineering"))
        consultant_explanation = clean_text(item.get("consultant_explanation") or item.get("agent_reasoning") or item.get("why_agent_found_verrou"))
        evidence_summary = clean_text(item.get("evidence_summary")) or clean_text(item.get("justification"))
        src_json = item.get("source_json") if isinstance(item.get("source_json"), dict) else {}
        sources = src_json.get("sources") if isinstance(src_json.get("sources"), list) else []

        lines.append(f"{i}. **{item_title}**")
        lines.append(f"   - Statut : candidat à valider par le consultant.")
        lines.append(f"   - Frascati : {decision} ; score {score if score is not None else 'non disponible'}.")
        if consultant_explanation:
            lines.append(f"   - Pourquoi l’agent le considère comme verrou : {truncate(consultant_explanation, 520)}")
        if scientific_lock:
            lines.append(f"   - Incertitude technique : {truncate(scientific_lock, 420)}")
        if why:
            lines.append(f"   - Pourquoi ce n’est pas seulement de l’ingénierie : {truncate(why, 420)}")
        if evidence_summary:
            lines.append(f"   - Preuves synthétisées : {truncate(evidence_summary, 420)}")
        if sources:
            docs = []
            for src in sources[:3]:
                if isinstance(src, dict):
                    doc = clean_text(src.get("document"))
                    if doc and doc not in docs:
                        docs.append(doc)
            if docs:
                lines.append(f"   - Documents sources : {' ; '.join(docs)}")
        lines.append("")

    return "\n".join(lines).strip()
