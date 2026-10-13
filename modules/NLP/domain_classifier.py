# -*- coding: utf-8 -*-
"""
Domain classifier EnnoSmart - affichage simplifié domaine/sous-domaine.

Objectif :
- Garder la compatibilité avec l'ancien JSON : niv1 / niv2 / niv3.
- Ajouter un affichage plus clair :
    Domaine principal = niv2  (ex: Informatique)
    Sous-domaine      = niv3  (ex: Intelligence artificielle)
    Domaine large     = niv1  (ex: Sciences et technologies du numérique...)

Aucune règle projet spécifique n'est codée ici.
Le scoring s'appuie uniquement sur domains.json.
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union


BASE_DIR = Path(__file__).resolve().parents[2] if len(Path(__file__).resolve().parents) >= 3 else Path.cwd()
DEFAULT_DOMAINS_PATH = BASE_DIR / "modules" / "NLP" / "data" / "domains.json"


# ---------------------------------------------------------------------
# Normalisation texte
# ---------------------------------------------------------------------

def _strip_accents(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def normalize_text(text: str) -> str:
    text = _strip_accents(text).lower()
    text = re.sub(r"[^a-z0-9+#.%µμ\-_/ ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _tokens(text: str) -> List[str]:
    text = normalize_text(text)
    return [t for t in re.split(r"\s+", text) if len(t) >= 2]


def _text_from_input(data: Union[str, List[Dict[str, Any]], Dict[str, Any], List[str]]) -> str:
    """Accepte string, document dict, liste de documents, nlp_result, etc."""
    if data is None:
        return ""

    if isinstance(data, str):
        return data

    if isinstance(data, list):
        parts: List[str] = []
        for item in data:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                for k in ("title", "document", "section_title", "text", "content", "passage", "raw_text"):
                    v = item.get(k)
                    if isinstance(v, str) and v.strip():
                        parts.append(v)
        return "\n".join(parts)

    if isinstance(data, dict):
        parts = []

        # Documents bruts
        docs = data.get("documents") or data.get("docs") or data.get("raw_documents")
        if isinstance(docs, list):
            parts.append(_text_from_input(docs))

        # Evidence packs NLP
        for key in (
            "merged_evidence_pack_for_ennodiagnostic",
            "multi_document_evidence_pack_for_ennodiagnostic",
            "evidence_pack_for_ennodiagnostic",
        ):
            pack = data.get(key)
            if isinstance(pack, dict):
                for v in pack.values():
                    if isinstance(v, list):
                        parts.append(_text_from_input(v))
                    elif isinstance(v, str):
                        parts.append(v)

        # Résumés par document
        summaries = data.get("document_evidence_summaries")
        if isinstance(summaries, list):
            parts.append(_text_from_input(summaries))

        # Champs texte simples
        for k in ("text", "content", "summary", "resume", "objective", "objectif"):
            v = data.get(k)
            if isinstance(v, str):
                parts.append(v)

        return "\n".join([p for p in parts if p])

    return str(data)


# ---------------------------------------------------------------------
# Chargement référentiel
# ---------------------------------------------------------------------

def load_domains(domains_path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    path = Path(domains_path or DEFAULT_DOMAINS_PATH)
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data


def _get_parent_chain(domains: Dict[str, Any], code: str) -> Tuple[Optional[str], Optional[str]]:
    """Retourne (niv2_code, niv1_code) pour un code niv3, ou équivalent."""
    niv1 = domains.get("niv1", {})
    niv2 = domains.get("niv2", {})
    niv3 = domains.get("niv3", {})

    if code in niv3:
        n2 = niv3[code].get("parent")
        n1 = niv2.get(n2, {}).get("parent") if n2 else None
        return n2, n1

    if code in niv2:
        return code, niv2[code].get("parent")

    if code in niv1:
        return None, code

    return None, None


# ---------------------------------------------------------------------
# Scoring générique par mots-clés
# ---------------------------------------------------------------------

def _keyword_score(text_norm: str, token_counter: Counter, keywords: Iterable[str], level_weight: float = 1.0) -> float:
    score = 0.0

    for kw in keywords or []:
        kw_norm = normalize_text(kw)
        if not kw_norm:
            continue

        # Match expression exacte : plus fort
        if len(kw_norm) >= 4 and kw_norm in text_norm:
            # Bonus selon taille de l'expression
            word_count = max(1, len(kw_norm.split()))
            score += level_weight * (2.5 + min(word_count, 6) * 0.4)

        # Match token par token : plus faible
        for t in kw_norm.split():
            if len(t) < 3:
                continue
            freq = token_counter.get(t, 0)
            if freq:
                score += level_weight * min(freq, 8) * 0.15

    return score


def _build_scores(domains: Dict[str, Any], text: str) -> Dict[str, float]:
    text_norm = normalize_text(text)
    token_counter = Counter(_tokens(text))

    scores: Dict[str, float] = defaultdict(float)

    niv1 = domains.get("niv1", {}) or {}
    niv2 = domains.get("niv2", {}) or {}
    niv3 = domains.get("niv3", {}) or {}

    # Score niveau 1 : large, moins déterminant
    niv1_scores: Dict[str, float] = {}
    for code, obj in niv1.items():
        niv1_scores[code] = _keyword_score(text_norm, token_counter, obj.get("keywords", []), level_weight=0.45)

    # Score niveau 2 : domaine principal affiché
    niv2_scores: Dict[str, float] = {}
    for code, obj in niv2.items():
        parent = obj.get("parent")
        base = _keyword_score(text_norm, token_counter, obj.get("keywords", []), level_weight=0.85)
        base += 0.25 * niv1_scores.get(parent, 0.0)
        niv2_scores[code] = base

    # Score niveau 3 : sous-domaine affiché
    for code, obj in niv3.items():
        parent2 = obj.get("parent")
        parent1 = niv2.get(parent2, {}).get("parent")

        score = _keyword_score(text_norm, token_counter, obj.get("keywords", []), level_weight=1.15)
        score += 0.35 * niv2_scores.get(parent2, 0.0)
        score += 0.12 * niv1_scores.get(parent1, 0.0)

        # Petit bonus si le label exact du niv3 apparaît
        label = obj.get("label", "")
        if label and normalize_text(label) in text_norm:
            score += 2.0

        scores[code] = score

    # Si aucun niv3 ne score mais un niv2 score, créer score virtuel pour ses sections
    # afin d'éviter une sortie vide.
    if scores and max(scores.values() or [0]) <= 0:
        for n2_code, n2_score in niv2_scores.items():
            children = [c for c, o in niv3.items() if o.get("parent") == n2_code]
            for c in children:
                scores[c] += n2_score * 0.5

    return dict(scores)


# ---------------------------------------------------------------------
# Format sortie
# ---------------------------------------------------------------------

def _format_top_item(domains: Dict[str, Any], code: str, score: float) -> Dict[str, Any]:
    niv1 = domains.get("niv1", {})
    niv2 = domains.get("niv2", {})
    niv3 = domains.get("niv3", {})

    n2, n1 = _get_parent_chain(domains, code)
    return {
        "code": code,
        "label": niv3.get(code, {}).get("label") or niv2.get(code, {}).get("label") or niv1.get(code, {}).get("label") or code,
        "level": "niv3" if code in niv3 else "niv2" if code in niv2 else "niv1",
        "niv1": n1,
        "niv1_label": niv1.get(n1, {}).get("label") if n1 else None,
        "niv2": n2,
        "niv2_label": niv2.get(n2, {}).get("label") if n2 else None,
        "score": round(float(score), 3),
    }


def _make_empty_result(domains_path: Path, warning: str) -> Dict[str, Any]:
    return {
        "domain_code_niv1": None,
        "domain_label_niv1": None,
        "domain_code_niv2": None,
        "domain_label_niv2": None,
        "domain_code_niv3": None,
        "domain_label_niv3": None,
        "confidence": 0.0,
        "top_domains": [],
        "source": str(domains_path),
        "warning": warning,
        "display": {
            "main_domain_code": None,
            "main_domain_label": None,
            "sub_domain_code": None,
            "sub_domain_label": None,
            "broad_domain_code": None,
            "broad_domain_label": None,
            "display_label": "Domaine non détecté",
        },
    }


def classify_domain(
    data: Union[str, List[Dict[str, Any]], Dict[str, Any], List[str]],
    domains_path: Optional[Union[str, Path]] = None,
    top_k: int = 10,
) -> Dict[str, Any]:
    """
    Fonction principale utilisée par le pipeline NLP.

    Retourne toujours l'ancien format + un bloc display :
    - Ancien format : domain_code_niv1/niv2/niv3, labels, confidence, top_domains.
    - Nouveau format : display.main_domain_label = niv2, display.sub_domain_label = niv3.
    """
    path = Path(domains_path or DEFAULT_DOMAINS_PATH)

    try:
        domains = load_domains(path)
    except Exception as e:
        return _make_empty_result(path, f"Impossible de charger domains.json : {e}")

    text = _text_from_input(data)
    if not text.strip():
        return _make_empty_result(path, "Texte vide pour la détection du domaine.")

    scores = _build_scores(domains, text)
    if not scores:
        return _make_empty_result(path, "Aucun score domaine calculé.")

    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    best_code, best_score = sorted_scores[0]

    # Si tout est à zéro, on garde une sortie propre.
    if best_score <= 0:
        return _make_empty_result(path, "Aucun domaine suffisamment détecté.")

    n2, n1 = _get_parent_chain(domains, best_code)
    niv1 = domains.get("niv1", {})
    niv2 = domains.get("niv2", {})
    niv3 = domains.get("niv3", {})

    # Confiance simple : best / somme top scores, bornée.
    positive_scores = [s for _, s in sorted_scores[: max(3, top_k)] if s > 0]
    denom = sum(positive_scores) or best_score
    confidence = best_score / denom if denom else 0.0
    confidence = max(0.0, min(1.0, confidence))

    top_domains = [_format_top_item(domains, c, s) for c, s in sorted_scores[:top_k] if s > 0]

    main_domain_code = n2
    main_domain_label = niv2.get(n2, {}).get("label") if n2 else None
    sub_domain_code = best_code if best_code in niv3 else None
    sub_domain_label = niv3.get(best_code, {}).get("label") if best_code in niv3 else None
    broad_domain_code = n1
    broad_domain_label = niv1.get(n1, {}).get("label") if n1 else None

    display_label = main_domain_label or broad_domain_label or "Domaine non détecté"
    if sub_domain_label and main_domain_label and sub_domain_label != main_domain_label:
        display_label = f"{main_domain_label} → {sub_domain_label}"

    return {
        # Ancien format conservé
        "domain_code_niv1": broad_domain_code,
        "domain_label_niv1": broad_domain_label,
        "domain_code_niv2": main_domain_code,
        "domain_label_niv2": main_domain_label,
        "domain_code_niv3": sub_domain_code,
        "domain_label_niv3": sub_domain_label,
        "confidence": round(float(confidence), 4),
        "top_domains": top_domains,
        "source": str(path),
        "warning": None,

        # Nouveau format pour affichage simple
        "display": {
            "main_domain_code": main_domain_code,
            "main_domain_label": main_domain_label,
            "sub_domain_code": sub_domain_code,
            "sub_domain_label": sub_domain_label,
            "broad_domain_code": broad_domain_code,
            "broad_domain_label": broad_domain_label,
            "display_label": display_label,
        },

        # Alias pratiques pour l'interface
        "main_domain_code": main_domain_code,
        "main_domain_label": main_domain_label,
        "sub_domain_code": sub_domain_code,
        "sub_domain_label": sub_domain_label,
        "broad_domain_code": broad_domain_code,
        "broad_domain_label": broad_domain_label,
        "display_label": display_label,
    }


# ---------------------------------------------------------------------
# Compatibilité avec anciens appels possibles
# ---------------------------------------------------------------------

def detect_domain(data: Any, domains_path: Optional[Union[str, Path]] = None, top_k: int = 10) -> Dict[str, Any]:
    return classify_domain(data=data, domains_path=domains_path, top_k=top_k)


def classify_project_domain(data: Any, domains_path: Optional[Union[str, Path]] = None, top_k: int = 10) -> Dict[str, Any]:
    return classify_domain(data=data, domains_path=domains_path, top_k=top_k)


class DomainClassifier:
    def __init__(self, domains_path: Optional[Union[str, Path]] = None):
        self.domains_path = Path(domains_path or DEFAULT_DOMAINS_PATH)

    def classify(self, data: Any, top_k: int = 10) -> Dict[str, Any]:
        return classify_domain(data=data, domains_path=self.domains_path, top_k=top_k)

    def detect(self, data: Any, top_k: int = 10) -> Dict[str, Any]:
        return self.classify(data=data, top_k=top_k)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--text", type=str, default="")
    parser.add_argument("--file", type=str, default="")
    parser.add_argument("--domains", type=str, default=str(DEFAULT_DOMAINS_PATH))
    args = parser.parse_args()

    txt = args.text
    if args.file:
        txt = Path(args.file).read_text(encoding="utf-8", errors="ignore")

    print(json.dumps(classify_domain(txt, args.domains), ensure_ascii=False, indent=2))
