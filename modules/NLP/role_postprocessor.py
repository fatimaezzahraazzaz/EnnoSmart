"""
modules/NLP/role_postprocessor.py
──────────────────────────────────────────────────────────────────────────────
NLP V7 — Correction post-LLM des rôles CIR/R&D.

Pourquoi ce module existe ?
- section_extractor/evidence_mapper peuvent parfois classer une phrase de
  verrou en contexte/démarche, ou une solution technique en objectif.
- Ce module applique des règles UNIVERSSELLES de cohérence CIR :
  objectif = but à atteindre
  verrou = incertitude / limite / absence / difficulté / non-résolution
  démarche = action réalisée / méthode / simulation / conception
  résultat = constat obtenu / performance mesurée / validation / observation

Important :
- Ce module ne fait PAS d'extraction.
- Il ne fait PAS de NER.
- Il ne contient PAS de vocabulaire métier par domaine.
- Il corrige uniquement le rôle des preuves déjà extraites.

API principale :
    postprocess_evidence_roles(evidence_map, sections=None, strict=True)

Entrée acceptée :
- dict {"mappings": [...]}
- objet avec attribut .mappings
- liste de mappings

Sortie :
- même objet si possible, modifié en place
- sinon dict {"mappings": [...]}
"""

from __future__ import annotations

import logging
import re
from copy import deepcopy
from typing import Any, Iterable

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# SIGNAUX UNIVERSELS
# ══════════════════════════════════════════════════════════════════════════════

VERROU_STRONG_RE = re.compile(
    r"\b("
    r"verrou(?:x)?|incertitude(?:s)?|difficult[ée](?:s)?|obstacle(?:s)?|blocage(?:s)?|"
    r"reste(?:nt)?\s+en\s+suspens|questions?\s+suivantes?\s+reste(?:nt)?\s+en\s+suspens|"
    r"absence\s+de|manque\s+de|lacune(?:s)?|limite(?:s)?|insuffisamment\s+document[ée](?:e|s)?|"
    r"ne\s+permet(?:tent)?\s+pas|ne\s+propose(?:nt)?\s+pas|ne\s+fournit\s+pas|"
    r"aucune\s+(?:donn[ée]e|solution|m[ée]thode|approche|strat[ée]gie)|"
    r"n['’]existe\s+pas|non\s+r[ée]solu(?:e|s)?|difficilement\s+pr[ée]dictible|"
    r"conditions?\s+exactes?|robustesse|reproductibilit[ée]|g[ée]n[ée]ralisables?|"
    r"transposition|dimensionnement\s+(?:optimal|standardis[ée]|op[ée]rationnel)|"
    r"compromis\s+entre|couplage\s+optimal"
    r")\b",
    re.I | re.U,
)

OBJECTIF_STRONG_RE = re.compile(
    r"\b("
    r"objectif(?:s)?|vise(?:nt)?\s+[àa]|vis[ée]s?|but(?:s)?|finalit[ée](?:s)?|"
    r"l['’]enjeu\s+(?:est|de)|consiste\s+[àa]|chercher\s+[àa]|rechercher\s+et\s+exp[ée]rimenter|"
    r"nos\s+travaux\s+visent\s+[àa]|nous\s+visons\s+[àa]|nous\s+cherchons\s+[àa]|"
    r"permettre\s+de|afin\s+de\s+(?:d[ée]velopper|valider|assurer|r[ée]duire|am[ée]liorer)"
    r")\b",
    re.I | re.U,
)

DEMARCHE_STRONG_RE = re.compile(
    r"\b("
    r"nous\s+avons\s+(?:d[ée]velopp[ée]|r[ée]alis[ée]|utilis[ée]|exploit[ée]|adopt[ée]|mis\s+en\s+oeuvre|"
    r"mis\s+en\s+œuvre|structur[ée]|consid[ée]r[ée]|effectu[ée]|mod[ée]lis[ée]|simul[ée]|"
    r"d[ée]fini|con[çc]u|retenu|dimensionn[ée]|test[ée]|exp[ée]riment[ée])|"
    r"la\s+m[ée]thode|la\s+d[ée]marche|le\s+protocole|l['’]approche|"
    r"simulation(?:s)?|mod[ée]lisation|conception|dimensionnement|mise\s+en\s+oeuvre|mise\s+en\s+œuvre|"
    r"cas\s+d['’][ée]tude|terrain(?:s)?\s+d['’]exp[ée]rimentation"
    r")\b",
    re.I | re.U,
)

RESULTAT_STRONG_RE = re.compile(
    r"\b("
    r"r[ée]sultat(?:s)?\s+(?:montre(?:nt)?|r[ée]v[èe]le(?:nt)?|indique(?:nt)?|confirme(?:nt)?)|"
    r"nous\s+constatons|on\s+observe|l['’]analyse\s+montre|a\s+permis\s+de\s+d[ée]montrer|"
    r"permet\s+de\s+montrer|besoins?\s+en\s+chauffage|performance(?:s)?\s+(?:obtenue|mesur[ée]e|satisfaisante)|"
    r"gain\s+de|r[ée]duction\s+de|am[ée]lioration\s+de|diminution\s+de|augmentation\s+de|"
    r"tr[èe]s\s+bon\s+comportement|faibles?|surconsommation|validation\s+de"
    r")\b",
    re.I | re.U,
)

# Phrases qui sont clairement des solutions / technologies déjà décrites :
# elles ne doivent pas finir dans objectifs.
SOLUTION_NOT_OBJECTIF_RE = re.compile(
    r"\b("
    r"cette\s+technologie\s+exploite|ce\s+syst[èe]me\s+repose|sera\s+[ée]quip[ée]|sont\s+[ée]quip[ée]s|"
    r"la\s+CTA\s+est\s+aliment[ée]e|nous\s+avons\s+mis\s+en\s+oeuvre|nous\s+avons\s+mis\s+en\s+œuvre|"
    r"nous\s+avons\s+d[ée]velopp[ée]|nous\s+avons\s+r[ée]alis[ée]|nous\s+avons\s+effectu[ée]|"
    r"chemin[ée]e\s+solaire|puits\s+climatique|brise-soleil|CTA|ouvrants?|simulation(?:s)?"
    r")\b",
    re.I | re.U,
)

# Phrases purement bibliographiques : on évite de les transformer en verrous
# sauf si elles contiennent un signal verrou très fort.
BIBLIO_RE = re.compile(
    r"\b("
    r"article|revue|th[èe]se|auteurs?|litt[ée]rature|travaux\s+de|publi[ée]|"
    r"Manzano|Givoni|Izard|Chahwane|Al-Shamkhee|Zillante|Bugenings|Kamari"
    r")\b",
    re.I | re.U,
)

SECTION_ROLE_ALIASES = {
    "objectifs": "objectif",
    "objectif": "objectif",
    "verrous": "verrou",
    "verrou": "verrou",
    "incertitudes": "verrou",
    "etat_art": "etat_art",
    "état_art": "etat_art",
    "travaux": "demarche",
    "demarche": "demarche",
    "démarche": "demarche",
    "essais": "essai",
    "essai": "essai",
    "resultats": "resultat",
    "résultats": "resultat",
    "resultat": "resultat",
    "conclusion": "resultat",
}


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS OBJETS / DICTS
# ══════════════════════════════════════════════════════════════════════════════

def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)

def _set(obj: Any, name: str, value: Any) -> None:
    if isinstance(obj, dict):
        obj[name] = value
    else:
        try:
            setattr(obj, name, value)
        except Exception:
            pass

def _phrase(evidence: Any) -> str:
    return str(
        _get(evidence, "phrase_source", "")
        or _get(evidence, "phrase", "")
        or _get(evidence, "text", "")
        or ""
    ).strip()

def _role(evidence: Any) -> str:
    return str(_get(evidence, "role", "") or "").strip()

def _section_role(evidence: Any, mapping: Any = None) -> str:
    sr = str(_get(evidence, "section_role", "") or "").strip()
    if sr:
        return sr
    return str(_get(mapping, "section_role", "") or "").strip()

def _mapping_evidences(mapping: Any) -> list:
    evs = _get(mapping, "evidences", []) or []
    return evs if isinstance(evs, list) else []

def _mappings_container(evidence_map: Any) -> tuple[list, Any]:
    """
    Retourne (mappings, original_container).
    """
    if isinstance(evidence_map, list):
        return evidence_map, evidence_map
    if isinstance(evidence_map, dict):
        mappings = evidence_map.get("mappings", [])
        return mappings if isinstance(mappings, list) else [], evidence_map
    mappings = getattr(evidence_map, "mappings", [])
    return mappings if isinstance(mappings, list) else [], evidence_map

def _norm_role(role: str) -> str:
    role = str(role or "").strip().lower()
    return SECTION_ROLE_ALIASES.get(role, role)

def _has_strong_verrou(text: str) -> bool:
    return bool(VERROU_STRONG_RE.search(text or ""))

def _has_strong_objectif(text: str) -> bool:
    return bool(OBJECTIF_STRONG_RE.search(text or ""))

def _has_strong_demarche(text: str) -> bool:
    return bool(DEMARCHE_STRONG_RE.search(text or ""))

def _has_strong_resultat(text: str) -> bool:
    return bool(RESULTAT_STRONG_RE.search(text or ""))

def _is_solution_not_objectif(text: str) -> bool:
    return bool(SOLUTION_NOT_OBJECTIF_RE.search(text or ""))

def _is_biblio(text: str) -> bool:
    return bool(BIBLIO_RE.search(text or ""))


# ══════════════════════════════════════════════════════════════════════════════
# DÉCISION DE RÔLE
# ══════════════════════════════════════════════════════════════════════════════

def decide_role(
    phrase: str,
    current_role: str = "",
    section_role: str = "",
    strict: bool = True,
) -> tuple[str, str]:
    """
    Retourne (new_role, reason).
    """
    text = phrase or ""
    current = _norm_role(current_role)
    section = _norm_role(section_role)

    if not text.strip():
        return current or "hors_sujet", "empty"

    # 1) Résultat très explicite : priorité haute.
    if _has_strong_resultat(text):
        return "resultat", "strong_resultat_signal"

    # 2) Verrou très explicite : priorité haute.
    # Même dans état_art, une phrase peut exprimer une limite de l'état de l'art.
    if _has_strong_verrou(text):
        # Si c'est très bibliographique et sans vrais marqueurs de blocage, garder état_art.
        # Mais les marqueurs forts "aucune", "absence", "ne propose pas", etc. restent verrou.
        return "verrou", "strong_verrou_signal"

    # 3) Objectif explicite, mais pas si la phrase décrit une solution déjà implémentée.
    if _has_strong_objectif(text) and not _is_solution_not_objectif(text):
        return "objectif", "strong_objectif_signal"

    # 4) Démarche explicite.
    if _has_strong_demarche(text):
        return "demarche", "strong_demarche_signal"

    # 5) Cohérence avec section si rôle faible ou contexte.
    if strict and current in {"", "contexte", "preuve", "metrique"}:
        if section in {"objectif", "verrou", "demarche", "essai", "resultat"}:
            return section, f"section_role_{section}"

    # 6) Ne pas laisser un objectif être une solution technique.
    if current == "objectif" and _is_solution_not_objectif(text):
        return "demarche", "solution_not_objectif"

    return current or "contexte", "keep_current"


def postprocess_evidence_roles(
    evidence_map: Any,
    sections: Any = None,
    strict: bool = True,
    add_debug: bool = True,
) -> Any:
    """
    Corrige les rôles dans evidence_map.

    Modifie en place quand possible et retourne l'objet.
    """
    mappings, container = _mappings_container(evidence_map)
    stats = {
        "processed": 0,
        "changed": 0,
        "by_reason": {},
    }

    for mapping in mappings:
        map_roles = []
        for ev in _mapping_evidences(mapping):
            phrase = _phrase(ev)
            old_role = _role(ev)
            sec_role = _section_role(ev, mapping)
            new_role, reason = decide_role(
                phrase=phrase,
                current_role=old_role,
                section_role=sec_role,
                strict=strict,
            )

            stats["processed"] += 1
            stats["by_reason"][reason] = stats["by_reason"].get(reason, 0) + 1

            if new_role and new_role != old_role:
                _set(ev, "role_original", old_role)
                _set(ev, "role", new_role)
                if add_debug:
                    _set(ev, "role_postprocess_reason", reason)
                stats["changed"] += 1

            role_final = _role(ev)
            if role_final and role_final not in map_roles:
                map_roles.append(role_final)

        # Mettre à jour roles_cir du mapping
        if map_roles:
            _set(mapping, "roles_cir", map_roles)

    # Ajouter stats au container si dict/objet.
    if isinstance(container, dict):
        container["role_postprocess_stats"] = stats
    else:
        try:
            setattr(container, "role_postprocess_stats", stats)
        except Exception:
            pass

    logger.info(
        "Role postprocessor : processed=%d changed=%d reasons=%s",
        stats["processed"], stats["changed"], stats["by_reason"],
    )
    return container
