# -*- coding: utf-8 -*-
"""
build_verrou_detection_dataset.py
Créer un dataset binaire VerrouDetector depuis role_classification_dataset.jsonl.

Usage :
cd C:\EnnoSmart
python tools\build_verrou_detection_dataset.py
"""

from __future__ import annotations
import argparse, csv, hashlib, json, re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows = []
    if not path.exists():
        raise FileNotFoundError(f"Fichier introuvable : {path}")
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
            n += 1
    return n


def write_json(path: Path, data: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def write_csv(path: Path, rows: List[Dict[str, Any]]):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "review_status", "review_priority", "is_verrou_evidence", "verrou_type",
        "verrou_confidence_auto", "verrou_rule", "hard_negative",
        "role_gold", "candidate_role", "project_id", "source_type", "source_doc",
        "text", "context_before", "context_after", "comment"
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def clean(x: Any) -> str:
    s = str(x or "").replace("\u00a0", " ").replace("\ufeff", " ")
    return re.sub(r"\s+", " ", s).strip()


def norm(x: Any) -> str:
    s = clean(x).lower().replace("’", "'").replace("œ", "oe")
    return re.sub(r"\s+", " ", s).strip()


def has_any(n: str, keys: List[str]) -> bool:
    return any(k in n for k in keys)


def md5_text(x: Any) -> str:
    return hashlib.md5(norm(x).encode("utf-8", errors="ignore")).hexdigest()


def is_noise_like(row: Dict[str, Any]) -> bool:
    role = norm(row.get("role_gold"))
    text = clean(row.get("text"))
    n = norm(text)
    if role == "bruit" or len(text) < 35:
        return True
    if len(text) < 220 and has_any(n, [
        "sommaire", "bibliographie", "remerciements", "copyright",
        "table des matières", "table des matieres", "formulaire", "cerfa", "siren", "siret"
    ]):
        return True
    return False


def detect_verrou_type(row: Dict[str, Any]) -> Tuple[str, float, str]:
    text = clean(row.get("text"))
    full = f"{row.get('context_before','')} {text} {row.get('context_after','')}"
    n = norm(text)
    nf = norm(full)
    role = norm(row.get("role_gold"))
    cand = norm(row.get("candidate_role"))

    if has_any(n, ["verrou", "verrous"]) and has_any(nf, [
        "difficulté", "difficulte", "incertitude", "complex", "ne permet",
        "absence", "manque", "non maîtr", "non maitris", "problème", "probleme"
    ]):
        return "verrou_explicit", 0.95, "explicit_verrou_keyword"

    if has_any(nf, [
        "solutions existantes", "approches existantes", "dans la littérature", "dans la litterature",
        "état de l'art", "etat de l'art", "peu d'études", "peu d’etudes", "peu d études",
        "ne parviennent pas", "ne permettent pas", "ne permet pas", "se limitent",
        "se concentre généralement", "se concentrent généralement", "reste limitée", "reste limitee",
        "limite leur portée", "limite leur portee"
    ]):
        return "limite_etat_art", 0.88, "state_of_art_limitation"

    if has_any(nf, [
        "absence de référentiel", "absence de referentiel", "aucun référentiel", "aucun referentiel",
        "aucun protocole", "pas de protocole", "aucune méthode officielle", "aucune methode officielle",
        "pas de méthode standard", "pas de methode standard", "non documenté", "non documente",
        "manque de données", "manque de donnees", "données rares", "donnees rares",
        "données insuffisantes", "donnees insuffisantes", "jeux de données sont rares",
        "jeux de donnees sont rares"
    ]):
        if has_any(nf, ["données", "donnees", "dataset", "jeux de données", "jeux de donnees"]):
            return "manque_donnees", 0.90, "missing_data"
        return "absence_referentiel", 0.90, "missing_reference_or_protocol"

    if has_any(nf, [
        "grande échelle", "grande echelle", "à grande échelle", "a grande echelle",
        "passage à l'échelle", "passage a l'echelle", "scalabilité", "scalabilite",
        "temps réel", "temps reel", "latence", "latence minimale", "volume massif",
        "grands ensembles de données", "grands ensembles de donnees",
        "vastes ensembles de données", "vastes ensembles de donnees"
    ]):
        if has_any(nf, ["temps réel", "temps reel", "latence"]):
            return "temps_reel_latency", 0.90, "real_time_latency_scale"
        return "passage_echelle", 0.88, "scale_issue"

    if has_any(nf, [
        "variabilité", "variabilite", "fluctuer", "fluctuation", "conditions variables",
        "conditions expérimentales", "conditions experimentales", "non maîtrisées", "non maitrisees",
        "non maîtrisé", "non maitrise", "hétérogène", "heterogene", "instable",
        "reproductibilité", "reproductibilite", "difficile à reproduire", "difficile a reproduire"
    ]):
        if has_any(nf, ["reproductibilité", "reproductibilite", "reproduire"]):
            return "non_reproductibilite", 0.86, "reproducibility_issue"
        return "variabilite_conditions", 0.84, "variability_conditions"

    if has_any(nf, [
        "modéliser", "modeliser", "modélisation", "modelisation", "complexité", "complexite",
        "complexe", "non linéaire", "non lineaire", "multi-factoriel", "multifactoriel",
        "multivarié", "multivarie", "phénomène", "phenomene", "comportement du système",
        "comportement du systeme"
    ]):
        if has_any(nf, ["modél", "model", "simulation"]):
            return "difficulte_modelisation", 0.84, "modeling_difficulty"
        return "complexite_systeme", 0.82, "system_complexity"

    if has_any(nf, [
        "difficulté de mesure", "difficulte de mesure", "mesure fiable", "mesures fiables",
        "précision", "precision", "bruit de mesure", "capteur", "calibration", "étalonnage",
        "etalonnage", "protocole insuffisant", "protocole ne permet pas",
        "méthode ne permet pas", "methode ne permet pas"
    ]):
        if has_any(nf, ["protocole", "méthode", "methode"]):
            return "protocole_insuffisant", 0.86, "protocol_insufficient"
        return "difficulte_mesure", 0.84, "measurement_difficulty"

    if has_any(nf, [
        "aucune différence significative", "aucune difference significative",
        "pas de différence significative", "pas de difference significative",
        "n'a pas permis", "n’a pas permis", "ne permet pas encore",
        "performance insuffisante", "performances insuffisantes",
        "résultats insuffisants", "resultats insuffisants", "pas suffisant",
        "non concluant", "ne semble pas", "pas encore optimales", "pas optimale"
    ]):
        if role == "resultat":
            return "performance_insuffisante", 0.84, "non_conclusive_result"
        return "incertitude_resultat", 0.82, "uncertain_or_insufficient_result"

    if role == "limite" and has_any(nf, [
        "à confirmer", "a confirmer", "devront être poursuivis", "devront etre poursuivis",
        "doivent être poursuivis", "doivent etre poursuivis", "incertain", "incertaine",
        "incertitude", "probablement", "pourrait", "pourraient", "semble", "pas encore"
    ]):
        return "incertitude_resultat", 0.78, "general_uncertainty_limit"

    if role == "verrou" or cand == "verrou":
        return "autre_verrou", 0.70, "role_gold_or_candidate_verrou"

    return "", 0.0, "no_verrou_signal"


def is_hard_negative(row: Dict[str, Any], verrou_type: str) -> bool:
    if verrou_type:
        return False
    role = norm(row.get("role_gold"))
    n = norm(row.get("text"))
    if role in {"methode", "parametre", "resultat", "contribution"} and has_any(n, [
        "méthode", "methode", "analyse", "mesure", "modèle", "modele",
        "résultats", "resultats", "performance", "paramètre", "parametre",
        "données", "donnees", "protocole", "approche"
    ]):
        return True
    if has_any(n, [
        "est une méthode", "est une methode", "est un algorithme",
        "repose sur", "est basée sur", "est basee sur", "utilise", "emploie", "permet de"
    ]) and not has_any(n, [
        "ne permet pas", "ne parvient pas", "limite", "difficile",
        "incertitude", "insuffisant", "problème", "probleme"
    ]):
        return True
    return False


def label_row(row: Dict[str, Any]) -> Dict[str, Any]:
    role = norm(row.get("role_gold"))

    if is_noise_like(row):
        return {"is_verrou_evidence": False, "verrou_type": "", "verrou_confidence_auto": 0.95,
                "verrou_rule": "noise_or_bruit_negative", "hard_negative": False}

    vtype, score, rule = detect_verrou_type(row)
    positive = bool(vtype and score >= 0.65)

    if role in {"methode", "parametre", "contribution"} and score < 0.80:
        positive = False

    if role == "resultat" and vtype not in {"performance_insuffisante", "incertitude_resultat", "non_reproductibilite", "variabilite_conditions"}:
        if score < 0.88:
            positive = False

    if role == "limite" and not vtype:
        positive, vtype, score, rule = True, "incertitude_resultat", 0.68, "role_limite_default_positive"

    if role == "verrou" and not positive:
        positive, vtype, score, rule = True, "autre_verrou", max(score, 0.72), "role_verrou_default_positive"

    if not positive:
        vtype = ""

    return {"is_verrou_evidence": bool(positive), "verrou_type": vtype,
            "verrou_confidence_auto": round(float(score if score else 0.65), 3),
            "verrou_rule": rule, "hard_negative": is_hard_negative(row, vtype)}


def dedup(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    best = {}
    for r in rows:
        h = md5_text(r.get("text"))
        old = best.get(h)
        if old is None or (r["is_verrou_evidence"] and not old["is_verrou_evidence"]) or \
           float(r.get("verrou_confidence_auto", 0)) > float(old.get("verrou_confidence_auto", 0)):
            best[h] = r
    return list(best.values())


def balance(rows: List[Dict[str, Any]], max_neg_ratio: float = 1.7) -> List[Dict[str, Any]]:
    pos = [r for r in rows if r["is_verrou_evidence"]]
    neg = [r for r in rows if not r["is_verrou_evidence"]]
    hard = [r for r in neg if r["hard_negative"]]
    easy = [r for r in neg if not r["hard_negative"]]

    target_neg = min(len(neg), max(300, int(len(pos) * max_neg_ratio)))
    hard_target = min(len(hard), int(target_neg * 0.60))
    easy_target = target_neg - hard_target

    hard = sorted(hard, key=lambda r: -float(r.get("verrou_confidence_auto", 0)))[:hard_target]
    easy = sorted(easy, key=lambda r: str(r.get("role_gold", "")))[:easy_target]
    return sorted(pos + hard + easy, key=lambda r: (not r["is_verrou_evidence"], r.get("verrou_type",""), r.get("project_id","")))


def build_dataset(rows: List[Dict[str, Any]], do_balance=True):
    out = []
    for row in rows:
        lab = label_row(row)
        item = dict(row)
        item.update({
            "is_verrou_evidence": lab["is_verrou_evidence"],
            "verrou_type": lab["verrou_type"],
            "verrou_confidence_auto": lab["verrou_confidence_auto"],
            "verrou_rule": lab["verrou_rule"],
            "hard_negative": lab["hard_negative"],
            "dataset_version": "verrou_detection_dataset_v1",
            "created_at": datetime.now().isoformat(timespec="seconds"),
        })

        if item["is_verrou_evidence"] and item["verrou_confidence_auto"] < 0.75:
            priority = "HIGH"
        elif item["hard_negative"]:
            priority = "MEDIUM"
        else:
            priority = "LOW"

        item["review_status"] = "auto_labeled"
        item["review_priority"] = priority
        item["comment"] = f"Auto-label verrou: {item['verrou_rule']} | is_verrou_evidence={item['is_verrou_evidence']} | type={item['verrou_type']}"
        out.append(item)

    out = dedup(out)
    final = balance(out) if do_balance else out

    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "dataset_version": "verrou_detection_dataset_v1",
        "input_rows": len(rows),
        "after_dedup_rows": len(out),
        "final_rows": len(final),
        "balance_applied": do_balance,
        "positive_count": sum(1 for r in final if r["is_verrou_evidence"]),
        "negative_count": sum(1 for r in final if not r["is_verrou_evidence"]),
        "by_verrou_type": dict(Counter(r["verrou_type"] for r in final if r["is_verrou_evidence"])),
        "by_role_gold": dict(Counter(r.get("role_gold", "") for r in final)),
        "by_positive_role": dict(Counter(r.get("role_gold", "") for r in final if r["is_verrou_evidence"])),
        "by_negative_role": dict(Counter(r.get("role_gold", "") for r in final if not r["is_verrou_evidence"])),
        "by_rule": dict(Counter(r.get("verrou_rule", "") for r in final).most_common(40)),
        "hard_negative_count": sum(1 for r in final if r["hard_negative"]),
        "review_priority": dict(Counter(r["review_priority"] for r in final)),
        "warning": "Dataset auto-labellisé. Utilisable pour V1, mais les HIGH peuvent être vérifiés plus tard."
    }
    return final, summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-jsonl", default=r"C:\EnnoSmart\data\training\role_classification_dataset.jsonl")
    parser.add_argument("--output-jsonl", default=r"C:\EnnoSmart\data\training\verrou_detection_dataset.jsonl")
    parser.add_argument("--summary-json", default=r"C:\EnnoSmart\data\training\verrou_detection_dataset_summary.json")
    parser.add_argument("--review-csv", default=r"C:\EnnoSmart\data\training\verrou_detection_review_sample.csv")
    parser.add_argument("--no-balance", action="store_true")
    args = parser.parse_args()

    rows = read_jsonl(Path(args.input_jsonl))
    dataset, summary = build_dataset(rows, do_balance=not args.no_balance)

    write_jsonl(Path(args.output_jsonl), dataset)
    write_json(Path(args.summary_json), summary)

    review_rows = [r for r in dataset if r["review_priority"] in {"HIGH", "MEDIUM"}]
    review_rows += [r for r in dataset if r["review_priority"] == "LOW"][:400]
    write_csv(Path(args.review_csv), review_rows)

    print("VERROU DATASET CRÉÉ")
    print(json.dumps({
        "final_rows": summary["final_rows"],
        "positive_count": summary["positive_count"],
        "negative_count": summary["negative_count"],
        "by_verrou_type": summary["by_verrou_type"],
        "by_positive_role": summary["by_positive_role"],
        "by_negative_role": summary["by_negative_role"],
        "hard_negative_count": summary["hard_negative_count"],
        "review_priority": summary["review_priority"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
