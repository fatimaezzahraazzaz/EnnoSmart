"""
modules/NLP/evidence_validator.py
──────────────────────────────────────────────────────────────────────────────
Validation finale des preuves CIR/R&D après section_extractor + role_postprocessor.

Version : 7.2.0  (V7 + apport V8.1)

Changements V7.2.0 vs V7.1.3 :
- NOUVEAU : filtre NER humains dans objectifs/verrous.
  V8.1 avait identifié que les noms de personnes (ressources humaines) se
  retrouvaient mélangés dans objectifs_rd. Ce filtre les rejette proprement.
- NOUVEAU : filtre "état de l'art" dans résultats.
  V8.1 avait identifié que des résultats de littérature contaminaient les
  résultats projet. On les repousse vers etat_art.
- Tout le reste (logique de base, seuils, rôles forts) est identique à V7.1.3.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

logger = logging.getLogger(__name__)

VALID_ROLES = {
    "contexte",
    "objectif",
    "verrou",
    "etat_art",
    "demarche",
    "essai",
    "resultat",
    "preuve",
    "metrique",
    "administratif",
    "hors_sujet",
}

ROLE_ALIASES = {
    "objectifs": "objectif",
    "verrous": "verrou",
    "démarche": "demarche",
    "méthode": "demarche",
    "methodologie": "demarche",
    "méthodologie": "demarche",
    "travaux": "demarche",
    "résultat": "resultat",
    "resultats": "resultat",
    "résultats": "resultat",
    "essais": "essai",
    "état_art": "etat_art",
    "etat de l'art": "etat_art",
    "état de l'art": "etat_art",
}

TITLE_ONLY_RE = re.compile(
    r"^\s*(?:"
    r"(?:\d+(?:\.\d+)*\.?\s*)?"
    r"(?:objectifs?|verrous?|incertitudes?|contexte|"
    r"[ée]tat\s+de\s+l['']art|d[ée]marche|travaux|"
    r"r[ée]sultats?|conclusion|annexes?|mots[\s-]cl[ée]s?)"
    r"\s*)$",
    re.I | re.U,
)

BAD_EVIDENCE_RE = re.compile(
    r"^\s*(?:"
    r"figure\s+\d+|tableau\s+\d+|source\s*:|comment by|"
    r"voir\s+figure|voir\s+annexe|page\s+\d+|"
    r"\(?x\)?|oui|non|n/?a"
    r")\s*$",
    re.I | re.U,
)

# ── NOUVEAU V8.1 : filtre NER humains ────────────────────────────────────────
# Pattern : NOM PRÉNOM complet ou "Prénom. NOM" style tableau RH.
# Ex: "AIT YOUNES TARIK", "CHEVALLIER NICOLAS", "S. DE NOUAL"
# Ces entrées viennent de la section "Description des ressources humaines"
# et ne doivent JAMAIS finir dans objectifs_rd ou verrous_techniques.
_HUMAN_NER_RE = re.compile(
    r"(?:"
    # Forme "NOM PRÉNOM PRÉNOM" tout en majuscules (style tableau RH)
    r"^[A-ZÉÈÀÂÎÏÔÛÙÇ]{2,}(?:\s+[A-ZÉÈÀÂÎÏÔÛÙÇ]{2,}){1,4}$"
    r"|"
    # Forme "S. DE NOUAL" ou "T. Powolny"
    r"^[A-ZÉÈÀÂÎÏÔÛÙÇ]\.\s+[A-ZÉÈÀÂÎÏÔÛÙÇ][A-Za-zÉÈÀÂÎÏÔÛÙÇéèàâîïôûùç\s\-]{1,50}$"
    r"|"
    # Forme avec pipe (tableau RH) : "NOM | Diplôme | Poste | …"
    r"^[A-ZÉÈÀÂÎÏÔÛÙÇ\s]{3,40}\s*\|"
    r")",
    re.U,
)

# Champs CIR où les noms humains sont interdits.
_ROLES_NO_HUMAN = {"objectif", "verrou", "demarche", "resultat", "essai"}

# ── NOUVEAU V8.1 : filtre résultats littérature dans résultats projet ────────
# Si une preuve classée "resultat" contient des marqueurs bibliographiques
# forts, on la reclasse en etat_art plutôt que de la rejeter.
_BIBLIO_RESULT_RE = re.compile(
    r"\b("
    r"th[èe]se\s+de\s+[A-Z]|"
    r"article\s+de\s+[A-Z]|"
    r"travaux\s+(?:acad[ée]miques|de\s+[A-Z])|"
    r"litt[ée]rature|"
    r"publi[ée]|publications?|"
    r"auteurs?\s+soulignent|"
    r"[A-Z][a-z]+\s+et\s+al\.|"
    r"selon\s+[A-Z][a-z]+"
    r")\b",
    re.I | re.U,
)

# ─────────────────────────────────────────────────────────────────────────────

# ── CORRECTION DÉFINITIVE : limites d'état de l'art ≠ verrous projet ─────────
_ETAT_ART_LIMIT_RE = re.compile(
    r"\b("
    r"syst[èe]mes?\s+(?:passifs?|actifs?|semi-actifs?)|"
    r"solutions?\s+(?:existantes?|traditionnelles?|classiques?)|"
    r"technologies?\s+(?:existantes?|d['']isolation)|"
    r"plots?\s+en\s+(?:caoutchouc|[ée]lastom[èe]re)|"
    r"ressorts?|isolateurs?|c[aâ]bles\s+m[ée]talliques|"
    r"litt[ée]rature|publications?\s+acad[ée]miques|travaux\s+acad[ée]miques|"
    r"th[èe]se|articles?\s+scientifiques?|auteurs?\s+soulignent|"
    r"onur|ranjbar|essassi|coja|kari|somanath"
    r")\b",
    re.I | re.U,
)

_PROJECT_VERROU_RE = re.compile(
    r"\b("
    r"nous\s+(?:avons|devons|cherchons|visons)|"
    r"notre\s+(?:projet|op[ée]ration|[ée]tude|objectif|d[ée]marche)|"
    r"ce\s+projet|cette\s+op[ée]ration|dans\s+le\s+cadre\s+de\s+ce\s+projet|"
    r"a\s+n[ée]cessit[ée]|nous\s+a\s+mis\s+face|"
    r"verrou\s+scientifique\s+[àa]\s+lever|incertitude\s+important[e]?"
    r")\b",
    re.I | re.U,
)


def _section_role_of(evidence: Any, mapping: Any = None) -> str:
    role = (
        _get(evidence, "section_role", "")
        or _get(evidence, "section", "")
        or _get(mapping, "section_role", "")
        or _get(mapping, "source_section_role", "")
        or ""
    )
    return _norm_role(str(role).strip().lower()) if role else ""


def _should_stay_etat_art(text: str, role: str = "", section_role: str = "") -> bool:
    """
    Empêche les limites de solutions existantes / état de l'art d'être classées
    comme verrous projet. Compatible avec appels à 2 ou 3 arguments.
    """
    clean = _clean_phrase(text)
    sr = _norm_text(section_role)
    r = _norm_text(role)
    if not clean:
        return False

    if _PROJECT_VERROU_RE.search(clean):
        return False

    # Si la phrase vient d'une section état de l'art et décrit une limite existante.
    if sr in {"etat_art", "etat de l'art", "état de l'art"} and _ETAT_ART_LIMIT_RE.search(clean):
        return True

    # Si rôle mal passé en 2e argument (ancien appel), le traiter comme section_role.
    if r in {"etat_art", "etat de l'art", "état de l'art"} and _ETAT_ART_LIMIT_RE.search(clean):
        return True

    # Même hors section explicite, les phrases bibliographiques / solutions existantes restent état de l'art.
    if _ETAT_ART_LIMIT_RE.search(clean) and _BIBLIO_RESULT_RE.search(clean):
        return True

    return False



STRONG_ROLE_SIGNAL_RE = re.compile(
    r"\b("
    r"objectif|vise|visent|enjeu|développer|developper|expérimenter|experimenter|valider|"
    r"verrou|incertitude|absence|manque|reste en suspens|ne propose pas|aucune donnée|"
    r"nous avons|simulation|modélisation|modelisation|comparé|compare|développé|developpe|"
    r"résultat|resultat|résultats|resultats|montre|montrent|indique|indiquent|révèle|revele|"
    r"réduction|reduction|diminution|amélioration|amelioration|confirme|confirment|performance"
    r")\b",
    re.I | re.U,
)

MIN_EVIDENCE_CHARS = 35
MIN_STRONG_ROLE_CHARS = 18
DEDUP_SIMILARITY = 0.92


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


def _set_phrase(evidence: Any, phrase: str) -> None:
    if isinstance(evidence, dict):
        if "phrase_source" in evidence:
            evidence["phrase_source"] = phrase
        elif "phrase" in evidence:
            evidence["phrase"] = phrase
        else:
            evidence["phrase_source"] = phrase
    else:
        if hasattr(evidence, "phrase_source"):
            setattr(evidence, "phrase_source", phrase)
        elif hasattr(evidence, "phrase"):
            setattr(evidence, "phrase", phrase)


def _role(evidence: Any) -> str:
    return str(_get(evidence, "role", "") or "").strip()


def _norm_role(role: str) -> str:
    role = str(role or "").strip().lower()
    role = ROLE_ALIASES.get(role, role)
    return role if role in VALID_ROLES else "contexte"


def _norm_text(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or "").lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm_text(a), _norm_text(b)).ratio()


def _clean_phrase(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip(" \t\n\r;:,.")
    text = re.sub(r"\b(\d)\s+(\d)\b", r"\1\2", text)
    return text


def _has_verb_or_strong_signal(text: str) -> bool:
    return bool(
        STRONG_ROLE_SIGNAL_RE.search(text or "")
        or re.search(
            r"\b(est|sont|permet|permettent|montre|montrent|vise|visent|reste|"
            r"consiste|avons|doit|doivent|a\s+permis|révèle|revele|indique|confirme)\b",
            text or "",
            re.I | re.U,
        )
    )


def _is_human_ner(text: str) -> bool:
    """
    Détecte si le texte est un nom de personne (style tableau RH).
    Utilisé uniquement pour les rôles CIR principaux.
    """
    clean = text.strip()
    if not clean:
        return False
    # Longueur max : un nom de personne ne dépasse pas 80 caractères
    if len(clean) > 80:
        return False
    return bool(_HUMAN_NER_RE.match(clean))


def _is_biblio_result(text: str) -> bool:
    """
    Détecte si une preuve classée 'resultat' est en réalité un résultat
    de littérature (état de l'art), pas un résultat du projet.
    """
    return bool(_BIBLIO_RESULT_RE.search(text or ""))


def _is_bad_evidence(text: str, role: str) -> tuple[bool, str]:
    """
    Retourne (is_bad, reason).
    reason peut être :
      - "empty", "bad_marker", "title_only", "too_short", "fragment_without_verb"
      - "human_ner_in_cir_field"   (NOUVEAU V8.1)
      - "biblio_reclassified"      (NOUVEAU V8.1 — reclassé en etat_art, pas rejeté)
      - "strong_role_kept", "metric_kept", "kept"
    """
    clean = _clean_phrase(text)
    role = _norm_role(role)

    if not clean:
        return True, "empty"

    if BAD_EVIDENCE_RE.match(clean):
        return True, "bad_marker"

    if TITLE_ONLY_RE.match(clean):
        return True, "title_only"

    # ── NOUVEAU V8.1 : rejeter noms humains dans champs CIR ──────────────────
    if role in _ROLES_NO_HUMAN and _is_human_ner(clean):
        return True, "human_ner_in_cir_field"
    # ─────────────────────────────────────────────────────────────────────────

    # Préserver les rôles forts même si phrase courte.
    if role in {"objectif", "verrou", "demarche", "essai", "resultat"}:
        if len(clean) >= MIN_STRONG_ROLE_CHARS and _has_verb_or_strong_signal(clean):
            return False, "strong_role_kept"

    # Préserver les métriques si chiffre + unité/signe.
    if role == "metrique" and re.search(r"\d", clean):
        return False, "metric_kept"

    if len(clean) < MIN_EVIDENCE_CHARS:
        return True, "too_short"

    # Une phrase sans verbe et courte est souvent un fragment.
    if len(clean) < 80 and not _has_verb_or_strong_signal(clean):
        return True, "fragment_without_verb"

    return False, "kept"


def _mappings_container(evidence_map: Any) -> tuple[list, Any]:
    if isinstance(evidence_map, list):
        return evidence_map, evidence_map
    if isinstance(evidence_map, dict):
        mappings = evidence_map.get("mappings", [])
        return mappings if isinstance(mappings, list) else [], evidence_map
    mappings = getattr(evidence_map, "mappings", [])
    return mappings if isinstance(mappings, list) else [], evidence_map


def _mapping_evidences(mapping: Any) -> list:
    evs = _get(mapping, "evidences", []) or []
    return evs if isinstance(evs, list) else []


def _replace_evidences(mapping: Any, evidences: list) -> None:
    _set(mapping, "evidences", evidences)


def validate_evidence(
    evidence_map: Any,
    sections: Any = None,
    strict: bool = True,
) -> Any:
    mappings, container = _mappings_container(evidence_map)

    report = {
        "processed": 0,
        "kept": 0,
        "rejected": 0,
        "deduplicated": 0,
        "role_normalized": 0,
        "reclassified": 0,
        "rejection_reasons": {},
        "kept_reasons": {},
    }

    for mapping in mappings:
        kept = []
        seen_phrases: list[str] = []

        for ev in _mapping_evidences(mapping):
            report["processed"] += 1
            phrase = _clean_phrase(_phrase(ev))
            role_before = _role(ev)
            role = _norm_role(role_before)

            if role != role_before:
                _set(ev, "role", role)
                report["role_normalized"] += 1

            # ── NOUVEAU V8.1 : reclassifier résultats bibliographiques ───────
            # Avant de valider, si le rôle est "resultat" mais que la phrase
            # décrit un résultat de littérature, on la reclasse en etat_art
            # plutôt que de la laisser polluer les résultats projet.
            if role == "resultat" and _is_biblio_result(phrase):
                _set(ev, "role_original", role)
                _set(ev, "role", "etat_art")
                _set(ev, "validation_reason", "biblio_reclassified_to_etat_art")
                role = "etat_art"
                report["reclassified"] += 1

            # CORRECTION : une limite décrite dans l'état de l'art ne doit pas devenir
            # un verrou du projet, sauf si la phrase parle explicitement du projet.
            section_role = _section_role_of(ev, mapping)
            if role == "verrou" and _should_stay_etat_art(phrase, role, section_role):
                _set(ev, "role_original", role)
                _set(ev, "role", "etat_art")
                _set(ev, "validation_reason", "etat_art_limit_reclassified_to_etat_art")
                role = "etat_art"
                report["reclassified"] += 1
            is_bad, reason = _is_bad_evidence(phrase, role)
            if is_bad:
                _set(ev, "validated", False)
                _set(ev, "validation_reason", reason)
                report["rejected"] += 1
                report["rejection_reasons"][reason] = report["rejection_reasons"].get(reason, 0) + 1
                continue

            # V7.4.0 : garde-fous rôle universels
            nphrase = _norm_text(phrase)
            if role == "objectif" and re.search(
                r"(l['’]?objectif\s+de\s+[^.]{0,80}(?:structuration|regrouper|organisation|moyens?\s+humains?|moyens?\s+mat[ée]riels?)|"
                r"(?:nous|ce\s+projet|ces\s+travaux|cette\s+[ée]tude)\s+(?:a|ont)\s+permis\s+d['’]?(?:acqu[ée]rir|identifier|d[ée]velopper|mettre|contribuer)|"
                r"agr[ée][ée]?\s+au\s+CIR)",
                nphrase,
                re.I | re.U,
            ):
                _set(ev, "validated", False)
                _set(ev, "validation_reason", "false_objective_context_or_result")
                report["rejected"] += 1
                report["rejection_reasons"]["false_objective_context_or_result"] = report["rejection_reasons"].get("false_objective_context_or_result", 0) + 1
                continue

            if role == "verrou" and re.search(
                r"(nous\s+avons\s+(?:r[ée]alis[ée]|d[ée]fini|d[ée]velopp[ée]|retenu|choisi|men[ée]|mis\s+en\s+[œo]uvre|obtenu)|"
                r"(?:les\s+)?r[ée]sultats?\s+(?:de\s+R&D\s+)?(?:montrent|ont\s+permis|nous\s+ont\s+permis)|"
                r"sommes\s+parvenus|architecture\s+de\s+notre|solution\s+technique\s+retenue)",
                nphrase,
                re.I | re.U,
            ):
                _set(ev, "validated", False)
                _set(ev, "validation_reason", "demarche_or_result_not_verrou")
                report["rejected"] += 1
                report["rejection_reasons"]["demarche_or_result_not_verrou"] = report["rejection_reasons"].get("demarche_or_result_not_verrou", 0) + 1
                continue

            if role == "etat_art" and re.search(r"(brevet|d[ée]p[ôo]t|N[°o]\s*de\s+d[ée]p[ôo]t|indicateurs?\s+de\s+R&D)", nphrase, re.I | re.U):
                _set(ev, "validated", False)
                _set(ev, "validation_reason", "brevet_not_etat_art")
                report["rejected"] += 1
                report["rejection_reasons"]["brevet_not_etat_art"] = report["rejection_reasons"].get("brevet_not_etat_art", 0) + 1
                continue

            duplicate = False
            for old in seen_phrases:
                if _similar(phrase, old) >= DEDUP_SIMILARITY:
                    duplicate = True
                    break

            if duplicate:
                _set(ev, "validated", False)
                _set(ev, "validation_reason", "duplicate")
                report["deduplicated"] += 1
                report["rejected"] += 1
                report["rejection_reasons"]["duplicate"] = report["rejection_reasons"].get("duplicate", 0) + 1
                continue

            _set_phrase(ev, phrase)
            _set(ev, "validated", True)
            _set(ev, "validation_reason", reason)
            kept.append(ev)
            seen_phrases.append(phrase)

            report["kept"] += 1
            report["kept_reasons"][reason] = report["kept_reasons"].get(reason, 0) + 1

        _replace_evidences(mapping, kept)

        roles = []
        for ev in kept:
            r = _role(ev)
            if r and r not in roles:
                roles.append(r)
        _set(mapping, "roles_cir", roles if roles else ["hors_sujet"])

    if isinstance(container, dict):
        container["validation_report"] = report
    else:
        try:
            setattr(container, "validation_report", report)
        except Exception:
            pass

    logger.info(
        "Evidence validation v7.4.0 : kept=%d rejected=%d dedup=%d reclassified=%d normalized=%d",
        report["kept"], report["rejected"], report["deduplicated"],
        report["reclassified"], report["role_normalized"],
    )
    return container