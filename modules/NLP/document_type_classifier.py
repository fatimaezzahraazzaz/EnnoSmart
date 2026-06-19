# -*- coding: utf-8 -*-
"""
document_type_classifier.py — V26 generic multi-document + pré-CIR client

But :
- typer chaque document de manière générique, sans règle liée à un projet précis.
- distinguer un CIR final validé d'un pré-CIR client.
- un pré-CIR client est une source courante structurée importante :
    * utile pour objectifs / verrous / méthodes / résultats ;
    * soumis à FrascatiGuard ;
    * validation consultant obligatoire ;
    * jamais mémoire finale par défaut.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any, Dict


def _norm(s: str) -> str:
    s = str(s or "").lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("’", "'").replace("`", "'").replace("´", "'")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _has(pattern: str, text: str) -> bool:
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


TYPE_CONFIG = {
    "pre_cir_client": {"policy": "core_or_useful", "weight": 1.30},
    "cir_final_validated": {"policy": "memory_only", "weight": 1.00},
    "concept_projet": {"policy": "core_or_useful", "weight": 1.25},
    "note_projet": {"policy": "core_or_useful", "weight": 1.15},
    "presentation_projet": {"policy": "core_or_useful", "weight": 1.05},
    "rapport_test": {"policy": "core_or_useful", "weight": 1.20},
    "resultats_mesures": {"policy": "core_or_useful", "weight": 1.20},
    "brevet_invention": {"policy": "core_or_useful", "weight": 1.20},
    "preuve_depot": {"policy": "core_or_useful", "weight": 1.05},
    "methodologie_protocole": {"policy": "secondary", "weight": 0.75},
    "notice_memoire_technique": {"policy": "secondary", "weight": 0.55},
    "etat_art_bibliographie": {"policy": "comparison_only", "weight": 0.55},
    "norme_reglementation": {"policy": "context_only", "weight": 0.25},
    "plan_schema": {"policy": "context_only", "weight": 0.20},
    "administratif": {"policy": "context_only", "weight": 0.10},
    "template_formulaire": {"policy": "context_only", "weight": 0.10},
    "unknown_document": {"policy": "secondary", "weight": 0.55},
}


def _make(doc_type: str, confidence: float, reason: str) -> Dict[str, Any]:
    cfg = TYPE_CONFIG.get(doc_type, TYPE_CONFIG["unknown_document"])
    return {
        "document_type": doc_type,
        "document_type_confidence": float(confidence),
        "source_policy": cfg["policy"],
        "document_weight": float(cfg["weight"]),
        "document_type_reason": reason,
    }


def _is_cir_context(joined: str) -> bool:
    return _has(
        r"\b(cir|credit impot recherche|credit d impot recherche|dossier cir|fiche cir|declaration cir)\b",
        joined,
    )


def classify_document_type(doc: Dict[str, Any]) -> Dict[str, Any]:
    name_raw = str(doc.get("document") or doc.get("file_name") or "")
    name = _norm(Path(name_raw).name)
    text = _norm(str(doc.get("text") or "")[:12000])
    joined = f"{name} {text}"
    origin = str(doc.get("content_origin") or "unknown")
    has_cir = _is_cir_context(joined)

    pre_cir_signal = _has(
        r"\b(pre cir|pre-cir|pre dossier cir|pre dossier|preparation cir|preparatoire cir|"
        r"brouillon cir|draft cir|note cir|fiche cir preparatoire|elements cir|element cir|"
        r"trame cir|projet de dossier cir|dossier cir provisoire|version provisoire cir|"
        r"cir provisoire|premiere version cir)\b",
        joined,
    )
    final_validated_signal = _has(
        r"\b(cir final|dossier cir final|version finale du cir|dossier cir valide|dossier cir validee|"
        r"dossier cir valide|cir valide|declaration cir|declaration de cir|"
        r"declaration credit impot recherche|cir n 1|cir n-1|annee precedente|exercice precedent)\b",
        joined,
    )
    filename_final_signal = has_cir and _has(r"\b(vf|final|version finale)\b", name)

    if has_cir and final_validated_signal:
        return _make("cir_final_validated", 0.96, "CIR final validé / CIR précédent / déclaration CIR")
    if has_cir and pre_cir_signal:
        return _make("pre_cir_client", 0.94, "pré-CIR client / brouillon CIR à exploiter comme brut structuré")
    if filename_final_signal and not pre_cir_signal:
        return _make("cir_final_validated", 0.90, "CIR avec signal de version finale dans le nom")

    if _has(r"\b(concept|concepts|prototype|prototypage|solution retenue|solution technique|architecture de solution|choix technique|conception)\b", joined):
        return _make("concept_projet", 0.92, "concept/prototype/solution technique")
    if _has(r"\b(note de cadrage|cadrage|brief|expression besoin|expression du besoin|fiche projet|note projet|cdc|cahier des charges)\b", joined):
        return _make("note_projet", 0.92, "note projet / cadrage")
    if _has(r"\b(presentation|slides?|pptx|avancement|point projet|travaux)\b", joined):
        return _make("presentation_projet", 0.88, "présentation / avancement")
    if _has(r"\b(brevet|invention|inventeur|revendication|claims?|patent|inpi|propriete industrielle)\b", joined):
        return _make("brevet_invention", 0.92, "brevet / invention")
    if _has(r"\b(depot|recepisse|horodatage|preuve de depot)\b", joined):
        return _make("preuve_depot", 0.86, "preuve de dépôt / traçabilité")
    if _has(r"\b(rapport d essais?|rapport de tests?|pv d essais?|compte rendu d essais?|resultats d essais?|mesures?|campagne de mesure|validation experimentale|test de validation)\b", joined):
        return _make("rapport_test", 0.90, "rapport test / mesures")
    if _has(r"\b(resultats?|metriques?|performances?|courbes?|graphiques?|tableau comparatif|comparaison)\b", name):
        return _make("resultats_mesures", 0.85, "résultats / métriques dans le nom")
    if _has(r"\b(norme|standard|reglement|reglementation|certification|label|iso|astm|ista|en\s?[0-9]{2,}|nf\s?[a-z]?)\b", joined):
        return _make("norme_reglementation", 0.88, "norme / certification / réglementation")
    if _has(r"\b(methodologie|protocole|demarche|plan d essais|plan de test|procedure|mode operatoire)\b", joined):
        return _make("methodologie_protocole", 0.82, "méthodologie / protocole")
    if _has(r"\b(benchmark|comparatif|baseline|etat de l art|state of art|survey|review|bibliographie|references|article scientifique|publication|doi|arxiv|ieee)\b", joined):
        return _make("etat_art_bibliographie", 0.82, "état de l'art / bibliographie")
    if _has(r"\b(plan|coupe|schema|elevation|masse|rdc|r\+\d|niveau|implantation|dessin|a0|a1)\b", name):
        return _make("plan_schema", 0.88, "plan / schéma")
    if _has(r"\b(notice|memoire|descriptif|explicatif|technique|dce|pro|aps|apd|doe|aor)\b", joined):
        return _make("notice_memoire_technique", 0.80, "notice / mémoire technique")
    if _has(r"\b(cerfa|budget|facture|devis|planning|calendrier|administratif|contrat|marche|honoraires|annexe financiere|finance)\b", joined):
        return _make("administratif", 0.78, "administratif / planning / financier")
    if _has(r"\b(template|trame|modele|formulaire|exemple|a remplir)\b", joined):
        return _make("template_formulaire", 0.85, "template / formulaire")

    if origin == "project_core":
        return _make("note_projet", 0.70, "content_origin project_core")
    if origin == "state_of_art":
        return _make("etat_art_bibliographie", 0.70, "content_origin state_of_art")
    if origin == "metadata":
        return _make("administratif", 0.65, "content_origin metadata")
    return _make("unknown_document", 0.50, "fallback")


def enrich_document_type(doc: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(doc or {})
    info = classify_document_type(out)
    out.update(info)
    out["source_weight"] = float(out.get("source_weight") or info.get("document_weight") or 0.55)

    if info["document_type"] == "pre_cir_client":
        out["pre_cir_client"] = True
        out["needs_human_validation"] = True
        out["validation_status"] = out.get("validation_status") or "consultant_required"
        out["content_origin"] = out.get("content_origin") or "client_pre_cir"
    if info["document_type"] == "cir_final_validated":
        out["cir_final_validated"] = True
        out["content_origin"] = out.get("content_origin") or "cir_final_validated"
    return out
