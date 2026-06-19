# -*- coding: utf-8 -*-
r"""
domain_classifier.py

V22.2
Détection domaine basée uniquement sur modules/NLP/data/domains.json.

Correction :
- ne score plus les codes courts A1, A2, B4, B8 quand ils apparaissent seuls
  car ils peuvent venir de cellules Excel, plans, tableaux, références techniques.
- utilise surtout labels + keywords du domains.json.
- aucun fallback métier codé en dur.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_DOMAINS_PATH = Path(__file__).resolve().parent / "data" / "domains.json"

GENERIC_TOKENS = {
    "image", "images", "signal", "signaux", "donnee", "donnees", "data",
    "systeme", "systemes", "modele", "modeles", "analyse", "traitement",
    "simulation", "simulations", "logiciel", "logiciels", "methode",
    "methodes", "mesure", "mesures", "test", "tests", "essai", "essais",
    "resultat", "resultats", "projet", "technique", "techniques",
    "technologie", "technologies", "developpement", "conception",
    "automatique", "informatique", "mathematiques"
}


def norm(text: str) -> str:
    text = str(text or "").lower()
    table = str.maketrans(
        "àâäéèêëîïôöùûüç’",
        "aaaeeeeiioouuuc'"
    )
    text = text.translate(table)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _read_json_file(path: Path) -> Any:
    if not path.exists():
        return None

    for enc in ["utf-8", "utf-8-sig", "cp1252", "latin-1"]:
        try:
            raw = path.read_text(encoding=enc, errors="ignore")
            return json.loads(raw)
        except Exception:
            continue

    return None


def _extract_label_parent(value: Any) -> Tuple[str, Optional[str], List[str]]:
    if isinstance(value, dict):
        label = (
            value.get("label")
            or value.get("libelle")
            or value.get("name")
            or value.get("title")
            or ""
        )
        parent = value.get("parent")
        keywords = value.get("keywords") or value.get("mots_cles") or value.get("mots clés") or []

        if isinstance(keywords, str):
            keywords = [x.strip() for x in re.split(r"[,;|]", keywords) if x.strip()]
        elif not isinstance(keywords, list):
            keywords = []

        return str(label).strip(), str(parent).strip() if parent else None, [str(k) for k in keywords]

    return str(value).strip(), None, []


def _keywords_from_label(label: str) -> List[str]:
    pieces = re.split(r"[,;/()\-]+", str(label or ""))
    out = []
    for p in pieces:
        p = p.strip()
        if len(p) >= 4:
            out.append(p)
    return list(dict.fromkeys(out))


def load_domains(domains_path: Optional[str | Path] = None) -> List[Dict[str, Any]]:
    path = Path(domains_path) if domains_path else DEFAULT_DOMAINS_PATH
    data = _read_json_file(path)

    if not isinstance(data, dict):
        return []

    niv1_raw = data.get("niv1") or {}
    niv2_raw = data.get("niv2") or {}
    niv3_raw = data.get("niv3") or {}

    if not isinstance(niv1_raw, dict):
        niv1_raw = {}
    if not isinstance(niv2_raw, dict):
        niv2_raw = {}
    if not isinstance(niv3_raw, dict):
        niv3_raw = {}

    niv1, niv2, niv3 = {}, {}, {}
    niv2_parent, niv3_parent = {}, {}
    niv1_kw, niv2_kw, niv3_kw = {}, {}, {}

    for code, value in niv1_raw.items():
        label, parent, extra_kw = _extract_label_parent(value)
        code = str(code).strip()
        niv1[code] = label
        niv1_kw[code] = _keywords_from_label(label) + extra_kw

    for code, value in niv2_raw.items():
        label, parent, extra_kw = _extract_label_parent(value)
        code = str(code).strip()
        niv2[code] = label
        niv2_parent[code] = parent or code[:1]
        niv2_kw[code] = _keywords_from_label(label) + extra_kw

    for code, value in niv3_raw.items():
        label, parent, extra_kw = _extract_label_parent(value)
        code = str(code).strip()
        niv3[code] = label
        niv3_parent[code] = parent
        niv3_kw[code] = _keywords_from_label(label) + extra_kw

    rows: List[Dict[str, Any]] = []

    for code, label in niv1.items():
        rows.append({
            "code": code,
            "label": label,
            "level": "niv1",
            "niv1": code,
            "niv1_label": label,
            "niv2": None,
            "niv2_label": None,
            "keywords": niv1_kw.get(code, []),
        })

    for code, label in niv2.items():
        p1 = niv2_parent.get(code) or code[:1]
        rows.append({
            "code": code,
            "label": label,
            "level": "niv2",
            "niv1": p1,
            "niv1_label": niv1.get(p1),
            "niv2": code,
            "niv2_label": label,
            "keywords": niv2_kw.get(code, []),
        })

    for code, label in niv3.items():
        p2 = niv3_parent.get(code)
        if not p2:
            m = re.match(r"^([A-Z]\d+)", code)
            p2 = m.group(1) if m else None

        p1 = None
        if p2:
            p1 = niv2_parent.get(p2) or p2[:1]
        else:
            p1 = code[:1] if code else None

        rows.append({
            "code": code,
            "label": label,
            "level": "niv3",
            "niv1": p1,
            "niv1_label": niv1.get(p1),
            "niv2": p2,
            "niv2_label": niv2.get(p2),
            "keywords": niv3_kw.get(code, []),
        })

    return rows


def _tokens(label: str, min_len: int = 4) -> List[str]:
    toks = re.findall(r"[a-z0-9]+", norm(label))
    return [t for t in toks if len(t) >= min_len and t not in GENERIC_TOKENS]


def _is_explicit_domain_code(text_norm: str, code_norm: str) -> bool:
    """
    On accepte un code domaine seulement s’il est explicitement annoncé.
    Exemples acceptés :
    - domaine b4
    - code b4
    - classification b4
    - secteur b4
    Sinon A1/B4 peut être une cellule Excel ou une référence de plan.
    """
    if not code_norm or len(code_norm) < 2:
        return False

    patterns = [
        rf"\bdomaine\s+{re.escape(code_norm)}\b",
        rf"\bcode\s+{re.escape(code_norm)}\b",
        rf"\bclassification\s+{re.escape(code_norm)}\b",
        rf"\bsecteur\s+{re.escape(code_norm)}\b",
    ]

    return any(re.search(p, text_norm) for p in patterns)


def _score_domain(text_norm: str, domain: Dict[str, Any]) -> float:
    code = str(domain.get("code") or "").strip()
    label = str(domain.get("label") or "")
    niv2_label = str(domain.get("niv2_label") or "")
    level = domain.get("level")

    score = 0.0
    hits = 0

    code_norm = norm(code)
    label_norm = norm(label)

    # Code explicite seulement.
    if _is_explicit_domain_code(text_norm, code_norm):
        score += 30.0
        hits += 3

    # Label complet.
    if label_norm and len(label_norm) > 8 and label_norm in text_norm:
        score += 12.0
        hits += 3

    # Tokens du label.
    for tok in set(_tokens(label, min_len=4)):
        if re.search(rf"\b{re.escape(tok)}\b", text_norm):
            score += 2.0
            hits += 1

    # Parent niv2.
    for tok in set(_tokens(niv2_label, min_len=5)):
        if re.search(rf"\b{re.escape(tok)}\b", text_norm):
            score += 0.7
            hits += 0.5

    # Keywords venant uniquement du JSON.
    for kw in domain.get("keywords") or []:
        kw_norm = norm(kw)
        if not kw_norm or kw_norm in GENERIC_TOKENS:
            continue

        if kw_norm in text_norm:
            if len(kw_norm.split()) >= 2:
                score += 5.0
                hits += 2
            else:
                score += 2.5
                hits += 1

    # Si un domaine a un seul hit faible, il est probablement générique.
    if hits < 2 and score < 8:
        score *= 0.20

    # Les niveaux 1 sont trop larges.
    if level == "niv1":
        score *= 0.15

    # Bonus léger aux feuilles.
    if score > 0:
        if level == "niv3":
            score *= 1.12
        elif level == "niv2":
            score *= 1.03

    return round(score, 4)


def _best_consistent_domain(scored: List[Dict[str, Any]]) -> Dict[str, Any]:
    scored = sorted(scored, key=lambda x: x["score"], reverse=True)

    if not scored:
        return {}

    # Agrégation par niv2 : si plusieurs indices pointent vers B4, B4 gagne.
    by_niv2: Dict[str, float] = {}

    for x in scored:
        n2 = x.get("niv2") or (x.get("code") if x.get("level") == "niv2" else None)
        if not n2:
            continue
        by_niv2[n2] = by_niv2.get(n2, 0.0) + float(x.get("score", 0))

    if by_niv2:
        best_niv2, best_sum = max(by_niv2.items(), key=lambda kv: kv[1])
        top = scored[0]

        family = [
            x for x in scored
            if x.get("niv2") == best_niv2
            or (x.get("level") == "niv2" and x.get("code") == best_niv2)
        ]

        if family and best_sum >= float(top.get("score", 0)) * 1.10:
            return sorted(family, key=lambda x: x["score"], reverse=True)[0]

    return scored[0]


def classify_domain(
    text: str,
    domains_path: Optional[str | Path] = None,
) -> Dict[str, Any]:
    path = Path(domains_path) if domains_path else DEFAULT_DOMAINS_PATH
    domains = load_domains(path)

    if not domains:
        return {
            "domain_code_niv1": None,
            "domain_label_niv1": None,
            "domain_code_niv2": None,
            "domain_label_niv2": None,
            "domain_code_niv3": None,
            "domain_label_niv3": None,
            "confidence": 0.0,
            "top_domains": [],
            "source": str(path),
            "warning": "domains.json lisible mais aucun domaine exploitable trouvé",
        }

    text_norm = norm(str(text or "")[:250000])

    scored = []
    for d in domains:
        s = _score_domain(text_norm, d)
        if s > 0:
            scored.append({**d, "score": s})

    if not scored:
        return {
            "domain_code_niv1": None,
            "domain_label_niv1": None,
            "domain_code_niv2": None,
            "domain_label_niv2": None,
            "domain_code_niv3": None,
            "domain_label_niv3": None,
            "confidence": 0.0,
            "top_domains": [],
            "source": str(path),
            "warning": "Aucun domaine suffisamment proche trouvé dans domains.json",
        }

    scored = sorted(scored, key=lambda x: x["score"], reverse=True)
    best = _best_consistent_domain(scored)
    top = scored[:10]

    top_sum = sum(float(x.get("score", 0)) for x in top[:5]) or 1.0
    confidence = min(0.95, max(0.25, float(best.get("score", 0)) / top_sum))

    if best.get("level") == "niv1":
        code_niv2 = None
        label_niv2 = None
        code_niv3 = None
        label_niv3 = None
    elif best.get("level") == "niv2":
        code_niv2 = best.get("code")
        label_niv2 = best.get("label")
        code_niv3 = None
        label_niv3 = None
    else:
        code_niv2 = best.get("niv2")
        label_niv2 = best.get("niv2_label")
        code_niv3 = best.get("code")
        label_niv3 = best.get("label")

    return {
        "domain_code_niv1": best.get("niv1"),
        "domain_label_niv1": best.get("niv1_label"),
        "domain_code_niv2": code_niv2,
        "domain_label_niv2": label_niv2,
        "domain_code_niv3": code_niv3,
        "domain_label_niv3": label_niv3,
        "confidence": round(confidence, 4),
        "top_domains": [
            {
                "code": x.get("code"),
                "label": x.get("label"),
                "level": x.get("level"),
                "niv1": x.get("niv1"),
                "niv1_label": x.get("niv1_label"),
                "niv2": x.get("niv2"),
                "niv2_label": x.get("niv2_label"),
                "score": x.get("score"),
            }
            for x in top
        ],
        "source": str(path),
        "warning": None,
    }


def classify_domain_from_documents(
    documents: List[Dict[str, Any]],
    domains_path: Optional[str | Path] = None,
) -> Dict[str, Any]:
    parts = []
    for d in documents or []:
        parts.append(str(d.get("document", "")))
        parts.append(str(d.get("text", ""))[:50000])
    return classify_domain("\n\n".join(parts), domains_path=domains_path)


def detect_domain_from_documents(
    documents: List[Dict[str, Any]],
    domains_path: Optional[str | Path] = None,
) -> Dict[str, Any]:
    return classify_domain_from_documents(documents, domains_path=domains_path)


def detect_domain_from_text(
    text: str,
    domains_path: Optional[str | Path] = None,
) -> Dict[str, Any]:
    return classify_domain(text, domains_path=domains_path)