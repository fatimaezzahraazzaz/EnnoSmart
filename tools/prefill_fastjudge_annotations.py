# -*- coding: utf-8 -*-
"""
prefill_fastjudge_annotations.py
------------------------------------------------------------
Pré-annotation du fichier fastjudge_annotation_sample.csv.

But :
- remplir automatiquement :
    role_gold
    keep_gold
    useful_for_cir_gold
    annotation_status
    comment
- ajouter :
    prefill_confidence
    prefill_rule
    review_priority

Important :
- Ce script ne remplace pas la validation humaine.
- Il fait une pré-annotation intelligente pour réduire le travail manuel.
- Tu dois vérifier surtout review_priority = HIGH.

Usage :
cd C:\EnnoSmart
python tools\prefill_fastjudge_annotations.py

Entrée par défaut :
C:\EnnoSmart\data\training\fastjudge_annotation_sample.csv

Sorties :
C:\EnnoSmart\data\training\fastjudge_annotation_sample_prefilled.csv
C:\EnnoSmart\data\training\fastjudge_annotation_prefill_summary.json

Ensuite :
1. Ouvre fastjudge_annotation_sample_prefilled.csv dans Excel
2. Filtre review_priority = HIGH
3. Corrige les lignes douteuses
4. Mets annotation_status = done pour les lignes validées
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple


VALID_ROLES = {
    "objectif",
    "verrou",
    "methode",
    "parametre",
    "variable",
    "resultat",
    "limite",
    "contribution",
    "hypothese",
    "bruit",
}

OUTPUT_EXTRA_COLUMNS = [
    "prefill_confidence",
    "prefill_rule",
    "review_priority",
]


# ---------------------------------------------------------------------
# Utils
# ---------------------------------------------------------------------
def norm_text(text: Any) -> str:
    s = str(text or "").lower()
    s = s.replace("’", "'").replace("œ", "oe")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def clean_text(text: Any) -> str:
    s = str(text or "").replace("\u00a0", " ").replace("\ufeff", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def has_any(n: str, patterns: List[str]) -> bool:
    return any(p in n for p in patterns)


def has_regex(text: str, pattern: str) -> bool:
    return bool(re.search(pattern, text, flags=re.I | re.U))


def to_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def bool_text(value: bool) -> str:
    return "true" if value else "false"


# ---------------------------------------------------------------------
# Détection bruit
# ---------------------------------------------------------------------
def is_bruit(text: str) -> Tuple[bool, str]:
    s = clean_text(text)
    n = norm_text(s)

    if len(s) < 30:
        return True, "too_short"

    if len(s) < 180 and has_any(n, [
        "sommaire", "table des matières", "table des matieres",
        "remerciements", "bibliographie", "copyright",
        "confidentiel", "annexe", "liste des figures",
        "liste des tableaux", "glossaire",
    ]):
        return True, "document_noise"

    if has_regex(s, r"^\s*(page\s*)?\d{1,3}\s*/\s*\d{1,3}\s*$"):
        return True, "page_number"

    if has_regex(s, r"^\s*(figure|tableau|fig\.|tab\.)\s*\d+\s*[:\-]?\s*$"):
        return True, "caption_title_only"

    # Ligne administrative / modèle / cerfa / avis, souvent pas une preuve technique.
    if len(s) < 260 and has_any(n, [
        "modele de demande", "modèle de demande", "formulaire",
        "cerfa", "siren", "siret", "adresse du siege",
        "nom prénom", "nom prenom", "rédacteur", "redacteur",
    ]):
        return True, "administrative_noise"

    # Table très numérique.
    digits = sum(ch.isdigit() for ch in s)
    if digits / max(1, len(s)) > 0.50 and len(s) < 350:
        return True, "mostly_numeric_table"

    return False, ""


# ---------------------------------------------------------------------
# Règles de rôle
# ---------------------------------------------------------------------
def classify_prefill(row: Dict[str, Any]) -> Dict[str, Any]:
    text = clean_text(row.get("text", ""))
    n = norm_text(text)
    candidate_role = norm_text(row.get("candidate_role", "unknown"))
    source_type = norm_text(row.get("source_type", ""))
    quality_score = to_float(row.get("quality_score"), 0.0)

    bruit, bruit_rule = is_bruit(text)
    if bruit:
        return make_result(
            role="bruit",
            keep=False,
            useful=False,
            confidence=0.92,
            rule=f"bruit:{bruit_rule}",
            priority="LOW",
        )

    # Très important : phrases de type mesure/comptage/analyse = méthode, pas objectif.
    if has_regex(text, r"^\s*(un|une|le|la|des)?\s*(comptage|mesure|analyse|évaluation|evaluation)\b"):
        return make_result(
            role="methode",
            keep=True,
            useful=True,
            confidence=0.88,
            rule="action_measure_or_counting_at_sentence_start",
            priority="LOW",
        )

    # Résultat / observation
    if has_any(n, [
        "aucune différence significative", "aucune difference significative",
        "différence significative", "difference significative",
        "les résultats montrent", "les resultats montrent",
        "les données montrent", "les donnees montrent",
        "les observations montrent", "a été observé", "a ete observe",
        "ont été observés", "ont ete observes",
        "on observe", "nous observons", "montre que", "montrent que",
        "révèle que", "revele que", "a permis de montrer",
        "augmentation", "diminution", "baisse", "hausse",
    ]):
        return make_result(
            role="resultat",
            keep=True,
            useful=True,
            confidence=0.86,
            rule="result_observation_marker",
            priority="LOW",
        )

    # Limite / incertitude
    if has_any(n, [
        "devront être poursuivis", "devront etre poursuivis",
        "doivent être poursuivis", "doivent etre poursuivis",
        "reste à confirmer", "reste a confirmer",
        "à confirmer", "a confirmer",
        "pas encore", "ne permet pas", "n'a pas permis", "n’a pas permis",
        "ne semble pas", "semble ne pas",
        "incertain", "incertaine", "incertitude",
        "probablement", "pourrait", "pourraient", "serait susceptible",
        "à court terme", "a court terme",
        "limite", "limites", "insuffisant", "insuffisante",
    ]):
        # Si ça décrit une vraie difficulté technique, on peut hésiter limite/verrou.
        # Par défaut limite, à vérifier si candidate_role = verrou.
        priority = "MEDIUM" if candidate_role == "verrou" else "LOW"
        return make_result(
            role="limite",
            keep=True,
            useful=True,
            confidence=0.82,
            rule="limit_uncertainty_marker",
            priority=priority,
        )

    # Verrou : vraie difficulté technique / manque de maîtrise.
    if has_any(n, [
        "verrou", "difficulté", "difficulte", "problème technique", "probleme technique",
        "complexité", "complexite", "manque de maîtrise", "manque de maitrise",
        "difficile à maîtriser", "difficile a maitriser",
        "non maîtrisé", "non maitrise",
        "manque de connaissance", "absence de connaissance",
        "enjeu technique", "frein technique",
        "a posé problème", "a pose probleme",
        "pose problème", "pose probleme",
        "concurrence avec", "développement de campagnols", "developpement de campagnols",
        "fatigue des sols", "résistances aux fongicides", "resistances aux fongicides",
    ]):
        return make_result(
            role="verrou",
            keep=True,
            useful=True,
            confidence=0.84,
            rule="technical_lock_marker",
            priority="MEDIUM",
        )

    # Objectif : finalité technique. Attention aux phrases méthodo avec "pour".
    if has_any(n, [
        "objectif", "objectifs", "le projet vise", "l'étude vise", "l'etude vise",
        "vise à", "vise a", "a pour but", "ont pour but",
        "afin de tester", "afin d'évaluer", "afin d evaluer",
        "évaluer l'efficacité", "evaluer l'efficacite",
        "tester l'efficacité", "tester l'efficacite",
        "caractériser", "caracteriser", "développer", "developper",
        "améliorer", "ameliorer", "optimiser",
    ]):
        # Si phrase commence par mesure/comptage, déjà capturée plus haut.
        return make_result(
            role="objectif",
            keep=True,
            useful=True,
            confidence=0.80,
            rule="objective_marker",
            priority="MEDIUM",
        )

    # Méthode / protocole
    if has_any(n, [
        "méthodologie", "methodologie", "protocole",
        "a été réalisé", "a ete realise", "ont été réalisés", "ont ete realises",
        "est réalisé", "est realise", "sont réalisés", "sont realises",
        "est effectuée", "est effectuee", "a été effectuée", "a ete effectuee",
        "nous avons mesuré", "mesurer", "mesure de", "mesures de",
        "comptage", "comparer", "comparaison", "suivre", "suivi",
        "installer", "installation", "appliquer", "calculer",
        "échantillon", "echantillon", "prélèvement", "prelevement",
        "test de student", "test de wilcoxon", "anova",
        "modélisation", "modelisation", "simulation",
    ]):
        return make_result(
            role="methode",
            keep=True,
            useful=True,
            confidence=0.82,
            rule="method_protocol_marker",
            priority="LOW",
        )

    # Paramètre : condition / facteur / modalité.
    if has_any(n, [
        "modalité", "modalite", "facteur", "paramètre", "parametre",
        "dose", "profondeur", "température", "temperature",
        "vitesse", "ouverture", "pression", "fréquence", "frequence",
        "configuration", "condition expérimentale", "condition experimentale",
        "à 10 cm", "a 10 cm", "20 cm", "30 cm", "50 tonnes",
    ]) or has_regex(text, r"\b\d+(?:[,.]\d+)?\s?(%|°c|mm|cm|m|hz|db|kg|t/ha|tonnes?/ha)\b"):
        return make_result(
            role="parametre",
            keep=True,
            useful=True,
            confidence=0.74,
            rule="parameter_or_condition_marker",
            priority="MEDIUM",
        )

    # Variable : grandeur mesurée courte ou liste de variables.
    if has_any(n, [
        "variables suivies", "variables mesurées", "variables mesurees",
        "paramètres mesurés", "parametres mesures",
        "grandeurs mesurées", "grandeurs mesurees",
        "rendement", "calibre", "taux de sucre", "acidité", "acidite",
        "fermeté", "fermete", "humidité du sol", "humidite du sol",
        "diamètre des troncs", "diametre des troncs",
        "taux de fruits", "taux de chute", "poids des fruits",
        "pression acoustique", "perte par insertion",
    ]):
        return make_result(
            role="variable",
            keep=True,
            useful=True,
            confidence=0.76,
            rule="variable_marker",
            priority="MEDIUM",
        )

    # Contribution
    if has_any(n, [
        "contribution", "apport", "innovation", "nouvelle approche",
        "nouveau protocole", "nouvelle méthode", "nouvelle methode",
        "permet désormais", "permet desormais", "amélioration de", "amelioration de",
    ]):
        return make_result(
            role="contribution",
            keep=True,
            useful=True,
            confidence=0.72,
            rule="contribution_marker",
            priority="MEDIUM",
        )

    # Hypothèse
    if has_any(n, [
        "hypothèse", "hypothese", "nous supposons", "on suppose",
        "il est possible que", "il est probable que",
        "nous pouvons envisager", "on peut envisager",
    ]):
        return make_result(
            role="hypothese",
            keep=True,
            useful=True,
            confidence=0.72,
            rule="hypothesis_marker",
            priority="MEDIUM",
        )

    # Si candidate_role déjà utile et qualité élevée : pré-remplir mais à vérifier.
    if candidate_role in VALID_ROLES and candidate_role != "bruit" and quality_score >= 0.70:
        return make_result(
            role=candidate_role,
            keep=True,
            useful=candidate_role in {"objectif", "verrou", "methode", "resultat", "limite", "contribution"},
            confidence=0.60,
            rule="candidate_role_high_quality_fallback",
            priority="HIGH",
        )

    # Unknown ou douteux : bruit par défaut mais à vérifier.
    if candidate_role == "unknown":
        return make_result(
            role="bruit",
            keep=False,
            useful=False,
            confidence=0.55,
            rule="unknown_default_to_noise_review",
            priority="HIGH",
        )

    # Dernier fallback.
    role = candidate_role if candidate_role in VALID_ROLES else "bruit"
    return make_result(
        role=role,
        keep=role != "bruit",
        useful=role in {"objectif", "verrou", "methode", "resultat", "limite", "contribution"},
        confidence=0.50,
        rule="low_confidence_fallback",
        priority="HIGH",
    )


def make_result(role: str, keep: bool, useful: bool, confidence: float, rule: str, priority: str) -> Dict[str, Any]:
    if role not in VALID_ROLES:
        role = "bruit"

    return {
        "role_gold": role,
        "keep_gold": bool_text(keep),
        "useful_for_cir_gold": bool_text(useful),
        "prefill_confidence": round(float(confidence), 3),
        "prefill_rule": rule,
        "review_priority": priority,
    }


# ---------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------
def read_csv(path: Path) -> Tuple[List[Dict[str, Any]], List[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = [dict(r) for r in reader]
        fieldnames = reader.fieldnames or []
    return rows, fieldnames


def write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    final_fields = list(fieldnames)
    for col in OUTPUT_EXTRA_COLUMNS:
        if col not in final_fields:
            final_fields.append(col)

    # garantir colonnes annotation
    for col in ["annotation_status", "role_gold", "keep_gold", "useful_for_cir_gold", "comment"]:
        if col not in final_fields:
            final_fields.insert(0, col)

    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=final_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def prefill_rows(rows: List[Dict[str, Any]], overwrite: bool = False) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    out = []

    for row in rows:
        r = dict(row)

        already_has_gold = str(r.get("role_gold") or "").strip() in VALID_ROLES
        if already_has_gold and not overwrite:
            # On conserve annotation existante.
            r.setdefault("prefill_confidence", "")
            r.setdefault("prefill_rule", "kept_existing_annotation")
            r.setdefault("review_priority", "MANUAL")
            out.append(r)
            continue

        pred = classify_prefill(r)

        r["role_gold"] = pred["role_gold"]
        r["keep_gold"] = pred["keep_gold"]
        r["useful_for_cir_gold"] = pred["useful_for_cir_gold"]
        r["prefill_confidence"] = pred["prefill_confidence"]
        r["prefill_rule"] = pred["prefill_rule"]
        r["review_priority"] = pred["review_priority"]

        # Important : prefilled != done. Tu peux ensuite valider/corriger.
        r["annotation_status"] = "prefilled"

        # Ajouter commentaire sans écraser l'existant.
        old_comment = str(r.get("comment") or "").strip()
        auto_comment = f"auto_prefill: {pred['prefill_rule']}"
        r["comment"] = old_comment if old_comment else auto_comment

        out.append(r)

    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "total_rows": len(out),
        "by_role_gold": dict(Counter(r.get("role_gold", "") for r in out)),
        "by_review_priority": dict(Counter(r.get("review_priority", "") for r in out)),
        "by_prefill_rule": dict(Counter(r.get("prefill_rule", "") for r in out)),
        "instructions": {
            "HIGH": "À vérifier en priorité.",
            "MEDIUM": "À vérifier si possible.",
            "LOW": "Peut être gardé après contrôle rapide.",
            "annotation_status": "Mettre done après validation humaine.",
        }
    }

    return out, summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input-csv",
        default=r"C:\EnnoSmart\data\training\fastjudge_annotation_sample.csv",
        help="CSV source à pré-annoter.",
    )
    parser.add_argument(
        "--output-csv",
        default=r"C:\EnnoSmart\data\training\fastjudge_annotation_sample_prefilled.csv",
        help="CSV pré-annoté.",
    )
    parser.add_argument(
        "--summary-json",
        default=r"C:\EnnoSmart\data\training\fastjudge_annotation_prefill_summary.json",
        help="Résumé JSON.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Écraser role_gold existant si déjà rempli.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    input_csv = Path(args.input_csv)
    output_csv = Path(args.output_csv)
    summary_json = Path(args.summary_json)

    if not input_csv.exists():
        raise FileNotFoundError(f"CSV introuvable : {input_csv}")

    rows, fieldnames = read_csv(input_csv)
    prefilled, summary = prefill_rows(rows, overwrite=bool(args.overwrite))

    write_csv(output_csv, prefilled, fieldnames)
    write_json(summary_json, summary)

    print("PRÉ-ANNOTATION TERMINÉE")
    print(f"Entrée : {input_csv}")
    print(f"Sortie : {output_csv}")
    print(f"Résumé : {summary_json}")
    print(json.dumps({
        "total_rows": summary["total_rows"],
        "by_role_gold": summary["by_role_gold"],
        "by_review_priority": summary["by_review_priority"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
