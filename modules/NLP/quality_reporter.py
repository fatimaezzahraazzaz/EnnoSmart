"""
modules/NLP/quality_reporter.py — NLP V7.2.0

Changements V7.2.0 vs V7.1.4 (apports V8.1) :
- NOUVEAU : score plus strict.
  V7.1.4 renvoyait 1.0 même avec des noms RH dans objectifs et organismes groupés.
  V7.2.0 pénalise :
    - bruit RH dans objectifs (noms de personnes détectés)
    - organismes non nettoyés (format "nos partenaires, LEM 3 et GEMTEX" groupé)
    - etat_art vide malgré des sections etat_art détectées
    - domaine_applicatif absent
- NOUVEAU : champ etat_art dans la couverture.
- Tout le reste (logique, signatures, compatibilité) est identique à V7.1.4.
"""

from __future__ import annotations

import re
from typing import Any


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _to_dict(obj: Any) -> dict:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "to_dict"):
        try:
            d = obj.to_dict()
            if isinstance(d, dict):
                return d
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        return dict(vars(obj))
    return {}


def _sections_list(sections: Any) -> list:
    if isinstance(sections, dict):
        return sections.get("sections", []) or []
    if isinstance(sections, list):
        return sections
    return getattr(sections, "sections", []) or []


def _count_section_roles(sections: Any) -> dict:
    counts = {}
    for s in _sections_list(sections):
        role = _get(s, "role", None) or _get(s, "section_role", None) or "unknown"
        counts[str(role)] = counts.get(str(role), 0) + 1
    return counts


def _aggregated_role_counts(aggregated: Any = None, final_taxonomy: Any = None, evidence_map: Any = None) -> dict:
    counts = {}

    ad = _to_dict(aggregated)
    by_role = ad.get("by_role") or ad.get("evidence_by_role") or {}
    if isinstance(by_role, dict):
        for role, vals in by_role.items():
            if isinstance(vals, list):
                counts[str(role)] = len(vals)
            elif vals:
                counts[str(role)] = 1

    if not counts and evidence_map is not None:
        mappings = evidence_map.get("mappings", []) if isinstance(evidence_map, dict) else getattr(evidence_map, "mappings", [])
        for m in mappings or []:
            evs = m.get("evidences", []) if isinstance(m, dict) else getattr(m, "evidences", [])
            for ev in evs or []:
                role = _get(ev, "role", "")
                if role:
                    counts[str(role)] = counts.get(str(role), 0) + 1

    fd = _to_dict(final_taxonomy)
    mapping = {
        "objectif": "objectifs_rd",
        "verrou": "verrous_techniques",
        "demarche": "methodes_rd",
        "resultat": "resultats_rd",
    }
    for role, key in mapping.items():
        if role not in counts and isinstance(fd.get(key), list):
            counts[role] = len(fd.get(key, []))

    return counts


def _list_len(obj: Any, key: str) -> int:
    d = _to_dict(obj)
    val = d.get(key, [])
    return len(val) if isinstance(val, list) else 0


# ── NOUVEAU V8.1 : détection bruit RH dans objectifs ─────────────────────────
_RH_PATTERN_RE = re.compile(
    r"(?:"
    r"[A-ZÉÈÀÂÎÏÔÛÙÇ]{2,}\s+[A-ZÉÈÀÂÎÏÔÛÙÇ]{2,}(?:\s+[A-ZÉÈÀÂÎÏÔÛÙÇ]{2,})*\s*\|"
    r"|Dipl[ôo]me|Ing[ée]nieur\s+R&D|Chef\s+de\s+projet|Directeur\s+R&D"
    r"|Gestion\s+de\s+l['']op[ée]ration"
    r")",
    re.I | re.U,
)

_GROUPED_ORG_RE = re.compile(
    r"nos\s+partenaires|LEM\s+3\s+et\s+GEMTEX|minist[eè]re\s+de\s+la\s+d[ée]fense",
    re.I | re.U,
)


def _count_rh_noise_in_objectives(objectifs: list) -> int:
    """Compte les lignes objectifs qui ressemblent à des ressources humaines."""
    count = 0
    for obj in objectifs or []:
        if _RH_PATTERN_RE.search(str(obj or "")):
            count += 1
    return count


def _has_grouped_organisms(organismes: list) -> bool:
    """Détecte les organismes non nettoyés (format groupé V7.1.x)."""
    for org in organismes or []:
        if _GROUPED_ORG_RE.search(str(org or "")):
            return True
    return False
# ─────────────────────────────────────────────────────────────────────────────



# ── V7.4.0 : checks qualité supplémentaires universels ──────────────────────
_FALSE_OBJECTIVE_Q_RE = re.compile(
    r"(l['’]?objectif\s+de\s+[^.]{0,80}(?:structuration|regrouper|organisation|moyens?\s+humains?|moyens?\s+mat[ée]riels?)|"
    r"(?:nous|ce\s+projet|ces\s+travaux|cette\s+[ée]tude)\s+(?:a|ont)\s+permis\s+d['’]?(?:acqu[ée]rir|identifier|d[ée]velopper|mettre|contribuer))",
    re.I,
)
_FALSE_VERROU_Q_RE = re.compile(
    r"(nous\s+avons\s+(?:r[ée]alis[ée]|d[ée]fini|d[ée]velopp[ée]|retenu|choisi|mis\s+en\s+[œo]uvre)|"
    r"(?:les\s+)?r[ée]sultats?\s+(?:de\s+R&D\s+)?(?:montrent|ont\s+permis|nous\s+ont\s+permis)|"
    r"sommes\s+parvenus)",
    re.I,
)
_BAD_ENTITY_Q_RE = re.compile(
    r"(\||\b(?:germes?|[ée]quipe\s+pluridisciplinaire|Liquid\s+Silicone|Amortissement\s+R[ée]sistance|Velfort\s+Cependant|"
    r"Lyc[ée]e\s+\w+|Justificatif\s+Des)\b)",
    re.I,
)
_BAD_ETAT_ART_Q_RE = re.compile(r"(brevet|d[ée]p[ôo]t|N[°o]\s*de\s+d[ée]p[ôo]t|indicateurs?\s+de\s+R&D)", re.I)
_BAD_TERM_Q_RE = re.compile(r"(\||\([^)]*$|\b(?:il\s+manqu|cependant|toutefois)\b)", re.I)

def _count_matching(values: list, pattern: re.Pattern) -> int:
    return sum(1 for v in values or [] if pattern.search(str(v or "")))

def _count_bad_entities(values: list) -> int:
    return _count_matching(values, _BAD_ENTITY_Q_RE)

def _count_bad_terms(values: list) -> int:
    return _count_matching(values, _BAD_TERM_Q_RE)
def build_quality_report(
    sections: Any = None,
    final_taxonomy: Any = None,
    evidence: Any = None,
    evidence_map: Any = None,
    metadata: Any = None,
    aggregated: Any = None,
    **kwargs: Any,
) -> dict:
    evidence_map = evidence_map or evidence
    section_roles = _count_section_roles(sections)
    role_counts = _aggregated_role_counts(aggregated=aggregated, final_taxonomy=final_taxonomy, evidence_map=evidence_map)

    fd = _to_dict(final_taxonomy)
    md = _to_dict(metadata)

    def get_field(key: str) -> list:
        val = fd.get(key)
        if val is None:
            val = md.get(key)
        return val if isinstance(val, list) else []

    objectifs = get_field("objectifs_rd")
    verrous = get_field("verrous_techniques")
    methodes = get_field("methodes_rd")
    resultats = get_field("resultats_rd")
    etat_art = get_field("etat_art")
    organismes = get_field("organismes") or get_field("partenaires_rd")
    personnes = get_field("personnes")
    technologies = get_field("technologies")
    materiaux = get_field("materiaux_composants") or get_field("materiaux")
    brevets = get_field("brevets")

    # ── NOUVEAU V8.1 : champs enrichis ───────────────────────────────────────
    domaine_applicatif = fd.get("domaine_applicatif") or md.get("domaine_applicatif")
    # ─────────────────────────────────────────────────────────────────────────

    missing = []
    if not objectifs:
        missing.append("objectifs_rd")
    if not verrous:
        missing.append("verrous_techniques")
    if not methodes:
        missing.append("methodes_rd")
    if not resultats:
        missing.append("resultats_rd")

    weak = []
    if 0 < len(objectifs) < 1:
        weak.append("objectifs_rd")
    if 0 < len(verrous) < 2:
        weak.append("verrous_techniques")
    if 0 < len(resultats) < 1:
        weak.append("resultats_rd")

    # ── NOUVEAU V8.1 : pénalités qualité ─────────────────────────────────────
    rh_noise = _count_rh_noise_in_objectives(objectifs)
    grouped_orgs = _has_grouped_organisms(organismes)
    etat_art_missing = not etat_art and section_roles.get("etat_art", 0) > 0
    applicatif_missing = not domaine_applicatif

    false_objectives = _count_matching(objectifs, _FALSE_OBJECTIVE_Q_RE)
    false_verrous = _count_matching(verrous, _FALSE_VERROU_Q_RE)
    bad_etat_art = _count_matching(etat_art, _BAD_ETAT_ART_Q_RE)
    bad_people = _count_bad_entities(personnes)
    bad_organisms = _count_bad_entities(organismes)
    bad_terms = _count_bad_terms(technologies + materiaux)
    brevets_missing_signal = bool(
        not brevets and re.search(r"brevet|d[ée]p[ôo]t", " ".join(map(str, etat_art + resultats + methodes)), re.I)
    )
    # ─────────────────────────────────────────────────────────────────────────

    score = 1.0
    score -= 0.12 * len(missing)
    score -= 0.05 * len(weak)

    # ── NOUVEAU V8.1 : pénalités score ───────────────────────────────────────
    if rh_noise > 0:
        score -= min(0.10, 0.04 * rh_noise)  # max -0.10 pour bruit RH
    if grouped_orgs:
        score -= 0.05
    if etat_art_missing:
        score -= 0.03
    if applicatif_missing:
        score -= 0.02

    score -= min(0.18, 0.06 * false_objectives)
    score -= min(0.18, 0.06 * false_verrous)
    score -= min(0.12, 0.06 * bad_etat_art)
    score -= min(0.12, 0.03 * bad_people)
    score -= min(0.10, 0.04 * bad_organisms)
    score -= min(0.08, 0.02 * bad_terms)
    if brevets_missing_signal:
        score -= 0.04
    # ─────────────────────────────────────────────────────────────────────────

    score = max(0.0, min(1.0, round(score, 2)))

    recommendations = []
    if missing:
        recommendations.append("Certains champs obligatoires sont vides : vérifier section_extractor, aggregator et final_taxonomy_mapper.")
    if "objectifs_rd" in missing and section_roles.get("objectifs", 0):
        recommendations.append("Une section objectifs existe mais aucun objectif n'est sorti : vérifier la priorité Objectifs visés.")
    if "verrous_techniques" in missing and section_roles.get("verrous", 0):
        recommendations.append("Une section verrous existe mais aucun verrou n'est sorti : vérifier role_postprocessor/evidence_validator.")
    if role_counts == {}:
        recommendations.append("aggregated_role_counts est vide : vérifier le format objet/dict transmis à aggregator.")

    # ── NOUVEAU V8.1 : recommandations enrichies ─────────────────────────────
    if rh_noise > 0:
        recommendations.append(
            f"objectifs_rd contient {rh_noise} ligne(s) de ressources humaines : vérifier evidence_validator (filtre human_ner_in_cir_field)."
        )
    if grouped_orgs:
        recommendations.append(
            "organismes/partenaires_rd contient des entrées groupées ('nos partenaires, LEM 3 et GEMTEX') : technical_terms_extractor.organismes_detectes non branché."
        )
    if etat_art_missing:
        recommendations.append(
            "Des sections etat_art sont détectées mais le champ etat_art est vide dans final_taxonomy_mapper."
        )
    if applicatif_missing:
        recommendations.append(
            "domaine_applicatif est absent : domain_classifier V7.2.0 doit être utilisé pour l'extraire."
        )
    if false_objectives:
        recommendations.append("objectifs_rd contient des objectifs organisationnels ou des résultats : renforcer final_taxonomy_mapper/evidence_validator.")
    if false_verrous:
        recommendations.append("verrous_techniques contient des démarches/résultats : filtrer les verbes 'nous avons réalisé/défini'.")
    if bad_etat_art:
        recommendations.append("etat_art contient un brevet/dépôt : déplacer vers brevets ou résultats.")
    if bad_people or bad_organisms:
        recommendations.append("personnes/organismes contient du bruit NER : renforcer les filtres d'entités.")
    if bad_terms:
        recommendations.append("technologies/matériaux contient des fragments de tableau ou entités tronquées.")
    if brevets_missing_signal:
        recommendations.append("Un brevet est mentionné mais le champ brevets est vide : vérifier le parser de footnotes.")
    # ─────────────────────────────────────────────────────────────────────────

    return {
        "global_score": score,
        "missing_fields": missing,
        "weak_fields": weak,
        "role_conflicts": [],
        "coverage": {
            "sections_objectifs_detectees": section_roles.get("objectifs", 0),
            "sections_verrous_detectees": section_roles.get("verrous", 0),
            "sections_travaux_detectees": section_roles.get("travaux", 0),
            "sections_resultats_detectees": section_roles.get("resultats", 0) + section_roles.get("conclusion", 0),
            "sections_etat_art_detectees": section_roles.get("etat_art", 0),
            "objectifs_extraits": len(objectifs),
            "verrous_extraits": len(verrous),
            "methodes_extraites": len(methodes),
            "resultats_extraits": len(resultats),
            # NOUVEAU V8.1
            "etat_art_extraits": len(etat_art),
            "organismes_detectes": len(organismes),
            "domaine_applicatif_present": bool(domaine_applicatif),
            "brevets_detectes": len(brevets),
        },
        "recommendations": recommendations,
        "quality_issues": {
            # NOUVEAU V8.1 : détail des problèmes qualité détectés
            "rh_noise_in_objectives": rh_noise,
            "grouped_organisms_detected": grouped_orgs,
            "etat_art_missing_despite_sections": etat_art_missing,
            "domaine_applicatif_missing": applicatif_missing,
            "false_objectives": false_objectives,
            "false_verrous": false_verrous,
            "bad_etat_art": bad_etat_art,
            "bad_people_entities": bad_people,
            "bad_organisms": bad_organisms,
            "bad_terms": bad_terms,
            "brevets_missing_signal": brevets_missing_signal,
        },
        "stats": {
            "section_roles": section_roles,
            "aggregated_role_counts": role_counts,
            "technical_terms": {
                "keywords": len((fd.get("mots_cles_projet") or {}).get("high_confidence", [])) if isinstance(fd.get("mots_cles_projet"), dict) else 0,
                "metrics": _list_len(final_taxonomy, "metriques_evaluation"),
                "partners": _list_len(final_taxonomy, "partenaires_rd"),
                "people": _list_len(final_taxonomy, "personnes"),
                "organisms": len(organismes),
            },
            "version": "7.4.0",
        },
    }