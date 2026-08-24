# -*- coding: utf-8 -*-
"""Classification documentaire générique du pipeline NLP CIR.

Principes V37
-------------
- aucune règle propre à un projet, une entreprise ou un secteur ;
- les indices présents dans le nom du fichier sont plus fiables que des mots
  isolés trouvés dans le corps du document ;
- un rapport expérimental ne devient pas un état de l'art parce qu'il contient
  les mots « comparaison », « référence » ou « bibliographie » une fois ;
- les documents de conception restent disponibles comme preuves techniques,
  mais ne suffisent jamais seuls à établir un verrou CIR.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").lower())
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.replace("’", "'").replace("`", "'").replace("´", "'")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _has(pattern: str, text: str) -> bool:
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _count(patterns: Iterable[str], text: str) -> int:
    return sum(1 for pattern in patterns if _has(pattern, text))


TYPE_CONFIG = {
    "pre_cir_client": {"policy": "core_or_useful", "weight": 1.30},
    "cir_final_validated": {"policy": "memory_only", "weight": 1.00},
    "concept_projet": {"policy": "core_or_useful", "weight": 1.25},
    "note_projet": {"policy": "core_or_useful", "weight": 1.15},
    "presentation_projet": {"policy": "core_or_useful", "weight": 1.05},
    "rapport_test": {"policy": "core_or_useful", "weight": 1.20},
    "resultats_mesures": {"policy": "core_or_useful", "weight": 1.20},
    "etude_technique": {"policy": "core_or_useful", "weight": 1.10},
    "conception_technique": {"policy": "secondary", "weight": 0.85},
    "publication_scientifique": {"policy": "secondary", "weight": 0.85},
    "brevet_invention": {"policy": "core_or_useful", "weight": 1.20},
    "preuve_depot": {"policy": "core_or_useful", "weight": 1.05},
    "methodologie_protocole": {"policy": "secondary", "weight": 0.75},
    "notice_memoire_technique": {"policy": "secondary", "weight": 0.65},
    "etat_art_bibliographie": {"policy": "comparison_only", "weight": 0.55},
    "norme_reglementation": {"policy": "context_only", "weight": 0.25},
    "plan_schema": {"policy": "context_only", "weight": 0.25},
    "administratif": {"policy": "context_only", "weight": 0.10},
    "template_formulaire": {"policy": "context_only", "weight": 0.10},
    "unknown_document": {"policy": "secondary", "weight": 0.55},
}


def _make(doc_type: str, confidence: float, reason: str) -> Dict[str, Any]:
    config = TYPE_CONFIG.get(doc_type, TYPE_CONFIG["unknown_document"])
    return {
        "document_type": doc_type,
        "document_type_confidence": round(float(confidence), 4),
        "source_policy": config["policy"],
        "document_weight": float(config["weight"]),
        "document_type_reason": reason,
    }


def _is_cir_context(joined: str) -> bool:
    return _has(
        r"\b(cir|credit impot recherche|credit d impot recherche|dossier cir|fiche cir|declaration cir)\b",
        joined,
    )


def classify_document_type(doc: Dict[str, Any]) -> Dict[str, Any]:
    name_raw = str(doc.get("document") or doc.get("file_name") or "")
    name = _norm(Path(name_raw).stem)
    extension = Path(name_raw).suffix.lower()
    # Le début et la fin contiennent souvent la nature du document et sa
    # conclusion. Une fenêtre limitée évite qu'un mot isolé au milieu domine.
    raw_text = str(doc.get("text") or "")
    text = _norm((raw_text[:16000] + " " + raw_text[-6000:]) if len(raw_text) > 16000 else raw_text)
    joined = f"{name} {text}"
    origin = str(doc.get("content_origin") or "unknown")
    has_cir = _is_cir_context(joined)

    pre_cir_signal = _has(
        r"\b(pre cir|pre dossier cir|preparation cir|preparatoire cir|brouillon cir|draft cir|"
        r"note cir|fiche cir preparatoire|elements cir|trame cir|projet de dossier cir|"
        r"dossier cir provisoire|version provisoire cir|cir provisoire|premiere version cir)\b",
        joined,
    )
    final_validated_signal = _has(
        r"\b(cir final|dossier cir final|version finale du cir|dossier cir valide|cir valide|"
        r"declaration cir|declaration de cir|declaration credit impot recherche|cir n 1|cir n-1|"
        r"annee precedente|exercice precedent)\b",
        joined,
    )
    filename_final_signal = has_cir and _has(r"\b(vf|final|version finale)\b", name)

    if has_cir and final_validated_signal:
        return _make("cir_final_validated", 0.97, "CIR final, validé ou exercice précédent")
    if has_cir and pre_cir_signal:
        return _make("pre_cir_client", 0.95, "pré-CIR ou brouillon CIR client")
    if filename_final_signal and not pre_cir_signal:
        return _make("cir_final_validated", 0.91, "CIR avec signal de version finale dans le nom")

    if _has(r"\b(template|trame|modele vierge|formulaire|exemple a remplir)\b", name) or _has(
        r"\b(tapez ici|champ a remplir|document modele vierge)\b", text
    ):
        return _make("template_formulaire", 0.88, "template ou formulaire")

    if _has(r"\b(cerfa|budget|facture|devis|planning|calendrier|administratif|contrat|marche|honoraires|annexe financiere|finance)\b", name):
        return _make("administratif", 0.82, "document administratif, contractuel ou financier")

    if _has(r"\b(brevet|invention|inventeur|revendication|claims?|patent|inpi|propriete industrielle)\b", joined):
        return _make("brevet_invention", 0.93, "brevet ou invention")
    if _has(r"\b(depot|recepisse|horodatage|preuve de depot)\b", joined):
        return _make("preuve_depot", 0.87, "preuve de dépôt ou de traçabilité")

    # Les rapports et résultats expérimentaux sont détectés avant les contenus
    # bibliographiques. Un simple mot « comparaison » ne peut donc plus les
    # détourner vers l'état de l'art.
    experimental_name = _has(
        r"\b(rapport|cr|compte rendu|pv|proces verbal|essai|essais|test|tests|releve|releves|"
        r"mesure|mesures|campagne|analyse experimentale|validation|comparatif|comparaison|"
        r"observation|controle)\b",
        name,
    )
    experimental_text_hits = _count(
        (
            r"\b(protocole d essai|conditions d essai|resultats? d essai|campagne de mesure)\b",
            r"\b(mesure|mesurer|releve|capteur|instrumentation|acquisition)\b",
            r"\b(resultat|observation|courbe|graphique|tableau|ecart|gain)\b",
            r"\b(validation experimentale|verification experimentale|banc d essai)\b",
        ),
        text,
    )
    if experimental_name or experimental_text_hits >= 3:
        return _make("rapport_test", 0.93 if experimental_name else 0.84, "rapport, relevé ou validation expérimentale")

    result_name = _has(
        r"\b(resultat|resultats|metrique|metriques|performance|performances|courbe|courbes|"
        r"mesure|mesures|releve|releves|comparaison|comparatif)\b",
        name,
    )
    if result_name or (
        extension in {".xlsx", ".xls", ".xlsm", ".csv"}
        and _has(r"\b(valeur|mesure|resultat|pression|temperature|debit|vitesse|puissance|temps)\b", joined)
    ):
        return _make("resultats_mesures", 0.88, "résultats ou mesures structurées")

    explicit_state_of_art_name = _has(
        r"\b(etat de l art|state of the art|literature review|revue de litterature|bibliographie|survey|related work|review paper)\b",
        name,
    )
    bibliographic_hits = _count(
        (
            r"\b(doi|arxiv|isbn|issn)\b",
            r"\b(references bibliographiques|bibliographie|liste des references)\b",
            r"\b(et al|journal|conference|proceedings|volume [0-9]+)\b",
            r"\b(related work|literature review|state of the art|prior work)\b",
        ),
        text,
    )
    if explicit_state_of_art_name or bibliographic_hits >= 2:
        return _make("etat_art_bibliographie", 0.92 if explicit_state_of_art_name else 0.82, "état de l'art ou bibliographie explicite")

    if _has(r"\b(article|publication|paper|manuscript|conference paper|journal paper)\b", name) and bibliographic_hits:
        return _make("publication_scientifique", 0.87, "publication scientifique")

    norm_name = _has(r"\b(norme|standard|reglement|reglementation|certification|directive)\b", name)
    norm_text_hits = len(re.findall(r"\b(norme|standard|reglementation|certification|exigence normative|conformite)\b", text))
    if norm_name or (norm_text_hits >= 4 and not experimental_name):
        return _make("norme_reglementation", 0.90 if norm_name else 0.78, "norme, certification ou réglementation")

    if _has(r"\b(plan|coupe|schema|dessin|implantation|nomenclature|mise en plan|elevation)\b", name):
        return _make("plan_schema", 0.90, "plan, schéma ou nomenclature")

    if _has(r"\b(conception|design|dimensionnement|assemblage|ensemble|mecanisme|composant|piece|prototype)\b", name):
        return _make("conception_technique", 0.82, "document de conception ou de définition technique")

    if _has(r"\b(methodologie|protocole|plan d essais|plan de test|procedure|mode operatoire)\b", name) or len(
        re.findall(r"\b(methodologie|protocole|plan d essais|procedure experimentale|mode operatoire)\b", text)
    ) >= 2:
        return _make("methodologie_protocole", 0.84, "méthodologie ou protocole")

    if _has(r"\b(etude|analyse|investigation|expertise|simulation|modelisation|synthese technique|dimensionnement)\b", name):
        return _make("etude_technique", 0.86, "étude ou analyse technique")

    if _has(r"\b(concept|prototype|prototypage|architecture de solution|choix technique)\b", joined):
        return _make("concept_projet", 0.92, "concept, prototype ou choix technique")
    if _has(r"\b(note de cadrage|cadrage|brief|expression du besoin|fiche projet|note projet|cdc|cahier des charges)\b", name):
        return _make("note_projet", 0.92, "note projet ou cadrage")
    if extension in {".ppt", ".pptx", ".odp"} or _has(r"\b(presentation|slides?|avancement|point projet)\b", name):
        return _make("presentation_projet", 0.88, "présentation ou avancement")
    if _has(r"\b(notice|memoire technique|descriptif technique|dce|aps|apd|doe|aor)\b", name):
        return _make("notice_memoire_technique", 0.82, "notice ou mémoire technique")

    technical_hits = _count(
        (
            r"\b(objectif technique|probleme technique|solution technique|configuration)\b",
            r"\b(essai|simulation|mesure|calcul|dimensionnement|prototype)\b",
            r"\b(parametre|contrainte|performance|limite|resultat)\b",
        ),
        text,
    )
    if technical_hits >= 2:
        return _make("etude_technique", 0.68, "contenu technique structuré sans type explicite")

    if origin == "project_core":
        return _make("note_projet", 0.72, "origine documentaire project_core")
    if origin == "state_of_art":
        return _make("etat_art_bibliographie", 0.72, "origine documentaire state_of_art")
    if origin == "metadata":
        return _make("administratif", 0.66, "origine documentaire metadata")
    return _make("unknown_document", 0.50, "type documentaire non déterminé")


def enrich_document_type(doc: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(doc or {})
    declared_current_evidence = bool(
        out.get("current_project_evidence")
        or out.get("declared_raw_document")
        or str(out.get("declared_mode") or "").strip().lower() == "raw"
    )
    info = classify_document_type(out)
    out.update(info)
    out["source_weight"] = float(out.get("source_weight") or info.get("document_weight") or 0.55)

    if info["document_type"] == "pre_cir_client":
        out["pre_cir_client"] = True
        out["needs_human_validation"] = True
        out["validation_status"] = out.get("validation_status") or "consultant_required"
        out["content_origin"] = out.get("content_origin") or "client_pre_cir"
    elif info["document_type"] == "cir_final_validated":
        out["cir_final_validated"] = True
        out["content_origin"] = out.get("content_origin") or "cir_final_validated"

    if declared_current_evidence:
        out.update(
            {
                "content_origin": "raw_client_document",
                "source_policy": "core_or_useful",
                "current_project_evidence": True,
                "declared_raw_document": True,
                "cir_final_validated": False,
                "not_final_cir": True,
            }
        )
        out["document_weight"] = max(
            float(out.get("document_weight") or 0.0),
            1.0,
        )
        out["source_weight"] = max(
            float(out.get("source_weight") or 0.0),
            float(out["document_weight"]),
        )
    return out
