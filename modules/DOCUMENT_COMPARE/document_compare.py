# -*- coding: utf-8 -*-
from __future__ import annotations

"""
EnnoSmart DocumentCompare V4.2

But :
- Comparer Document A et Document B sans notion ancien/nouveau.
- Détecter les différences utiles pour le diagnostic CIR.
- Auto-détection équilibrée : pas trop large, pas trop stricte.
- Quand une paire est sélectionnée, la page affiche uniquement le rapport de cette paire.

Catégories :
- Commun aux deux
- Différent entre A et B
- Seulement dans A
- Seulement dans B
"""

import html
import itertools
import json
import re
import difflib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".pptx", ".ppt",
    ".xlsx", ".xls", ".txt", ".msg",
    ".png", ".jpg", ".jpeg",
}


# =========================================================
# Normalisation générale
# =========================================================

def normalize_text(text: str) -> str:
    text = str(text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def simple_norm(text: str) -> str:
    text = str(text or "").lower()
    text = text.replace("œ", "oe")
    text = re.sub(r"[\u2018\u2019\u201a\u201b]", "'", text)
    text = re.sub(r"[\u201c\u201d\u201e\u201f]", '"', text)
    text = re.sub(r"[^a-z0-9àâäéèêëîïôöùûüç]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def ratio(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def safe_id(text: str, max_len: int = 90) -> str:
    s = simple_norm(text)
    s = re.sub(r"\s+", "_", s).strip("_")
    return (s[:max_len] or "unknown")


# =========================================================
# Valeurs numériques
# =========================================================

def extract_numbers(text: str) -> List[str]:
    nums = re.findall(r"\d+(?:[,.]\d+)?", str(text or ""))
    return [n.replace(",", ".") for n in nums]


def main_numeric_values(text: str) -> List[float]:
    """
    Extrait les valeurs numériques principales.
    Évite les références techniques et les dates autant que possible.
    """
    s = str(text or "")

    s = re.sub(r"\[SECTION\s*:[^\]]+\]", " ", s, flags=re.I)
    s = re.sub(r"Réf\.?\s*G?\d+", " ", s, flags=re.I)
    s = re.sub(r"\bG\d{4,}\b", " ", s, flags=re.I)

    raw = re.findall(r"\d+(?:[,.]\d+)?", s)
    values: List[float] = []

    for n in raw:
        try:
            v = float(n.replace(",", "."))
        except Exception:
            continue

        if v >= 1000:
            continue

        values.append(v)

    return values


def numeric_conflict(a: str, b: str) -> bool:
    va = main_numeric_values(a)
    vb = main_numeric_values(b)

    if not va or not vb:
        return False

    max_len = min(len(va), len(vb), 2)

    for i in range(max_len):
        diff = abs(va[i] - vb[i])
        tol = max(0.003, 0.001 * max(abs(va[i]), abs(vb[i]), 1.0))

        if diff > tol:
            return True

    if abs(len(va) - len(vb)) >= 2:
        return True

    return False


def same_numeric_signature(a: str, b: str) -> bool:
    va = main_numeric_values(a)
    vb = main_numeric_values(b)

    if not va and not vb:
        return True

    if len(va) != len(vb):
        return False

    for x, y in zip(va, vb):
        if abs(x - y) > max(0.003, 0.001 * max(abs(x), abs(y), 1.0)):
            return False

    return True


# =========================================================
# Chargement document
# =========================================================

def load_text_from_file(path: str) -> Dict[str, Any]:
    p = Path(path)

    if not p.exists():
        return {
            "ok": False,
            "document": p.name,
            "path": str(p),
            "text": "",
            "error": f"Fichier introuvable : {p}",
        }

    ext = p.suffix.lower()

    if ext not in SUPPORTED_EXTENSIONS:
        return {
            "ok": False,
            "document": p.name,
            "path": str(p),
            "text": "",
            "error": f"Extension non supportée : {ext}",
        }

    try:
        from modules.NLP.document_loader import load_documents

        docs = load_documents(
            [str(p)],
            use_ennosmart_extraction=True,
            include_cir_final=True,
        )

        if not docs:
            return {
                "ok": False,
                "document": p.name,
                "path": str(p),
                "text": "",
                "error": "Aucun texte extrait.",
            }

        text = "\n\n".join(str(d.get("text") or "") for d in docs)
        text = normalize_text(text)

        return {
            "ok": True,
            "document": p.name,
            "path": str(p),
            "extension": ext,
            "text": text,
            "chars": len(text),
            "loader": docs[0].get("loader"),
            "error": None,
        }

    except Exception as e:
        if ext == ".txt":
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
                text = normalize_text(text)
                return {
                    "ok": True,
                    "document": p.name,
                    "path": str(p),
                    "extension": ext,
                    "text": text,
                    "chars": len(text),
                    "loader": "txt_fallback",
                    "error": None,
                }
            except Exception as e2:
                return {
                    "ok": False,
                    "document": p.name,
                    "path": str(p),
                    "text": "",
                    "error": f"Erreur extraction : {e} | fallback txt : {e2}",
                }

        return {
            "ok": False,
            "document": p.name,
            "path": str(p),
            "text": "",
            "error": f"Erreur extraction : {e}",
        }


# =========================================================
# Découpage section-aware
# =========================================================

SECTION_KEYWORDS = [
    "masse", "mémo", "memo", "total", "section", "analyse", "contrôle",
    "controle", "conclusion", "résultat", "resultat", "essai", "test",
    "objectif", "verrou", "méthode", "methode", "réf", "ref",
    "ensemble", "réfrigérant", "refrigerant", "contrepoids",
]

VALUE_KEYWORDS = [
    "kg", "g", "bar", "°c", "sdw", "réelle", "reelle", "valeur",
    "différence", "difference", "écart", "ecart", "pesée", "pesee",
]


def is_heading_block(text: str) -> bool:
    s = simple_norm(text)
    raw = str(text or "").strip()

    if not raw:
        return False

    if raw.startswith("[SECTION"):
        return True

    if raw.endswith(":"):
        return True

    if len(raw) <= 140:
        if any(k in s for k in SECTION_KEYWORDS):
            if not any(v in s for v in VALUE_KEYWORDS):
                return True

    if s.startswith("section ajoutee") or s.startswith("conclusion"):
        return True

    return False


def clean_heading_label(text: str) -> str:
    raw = str(text or "")

    raw = re.sub(r"\[SECTION\s*:[^\]]+\]", " ", raw, flags=re.I)
    raw = normalize_text(raw)
    raw = re.sub(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", " ", raw)
    raw = re.sub(r"\b\d{4}\b", " ", raw)
    raw = re.sub(r"Réf\.?", "ref", raw, flags=re.I)

    return simple_norm(raw)


def context_key_from_heading(text: str) -> str:
    label = clean_heading_label(text)

    if "masse contrepoids a vide" in label or "masse contrepoids à vide" in label:
        return "masse_contrepoids_a_vide_ref_13163"

    if "masse equipee avec vis" in label or "masse équipée avec vis" in label:
        return "masse_equipee_avec_vis_ref_38183"

    if "masse plomb" in label:
        return "masse_plomb_apres_equilibrage"

    if "total reel" in label or "total réel" in label or "total reel apres recalcul" in label:
        return "total_reel"

    if "non equilibre" in label or "non équilibré" in label:
        return "masse_contrepoids_non_equilibre_ref_g57801"

    if "memo" in label or "mémo" in label:
        return "memo_technique"

    if "equilibre statiquement" in label or "équilibré statiquement" in label:
        return "masse_contrepoids_equilibre_statiquement_ref_g57801"

    if "analyse preliminaire" in label or "analyse préliminaire" in label:
        return "analyse_preliminaire_ecarts"

    if "controles complementaires" in label or "contrôles complémentaires" in label:
        return "controles_complementaires"

    if "conclusion" in label:
        return "conclusion"

    return safe_id(label, 80)


def infer_line_kind(text: str) -> str:
    s = simple_norm(text)

    if is_heading_block(text):
        return "heading"

    if "valeur sdw" in s:
        return "valeur_sdw"

    if "modifiee par l utilisateur" in s or "modifiée par l utilisateur" in s:
        return "valeur_modifiee_sdw"

    if "reelle" in s or "réelle" in s or "pesée" in s or "pesee" in s:
        return "valeur_reelle"

    if "difference" in s or "différence" in s or "ecart" in s or "écart" in s:
        return "ecart"

    if "kg" in s or re.search(r"\b\d+(?:[,.]\d+)?\s*g\b", s):
        return "valeur"

    return "text"


def split_into_blocks(text: str, min_chars: int = 20) -> List[Dict[str, Any]]:
    text = normalize_text(text)
    raw_blocks = re.split(r"\n\s*\n+", text)

    blocks: List[Dict[str, Any]] = []
    idx = 0
    current_context = "document_start"
    current_heading = ""

    for raw in raw_blocks:
        raw = normalize_text(raw)

        if len(raw) < min_chars:
            continue

        pieces = [raw]

        if len(raw) > 1800:
            sentences = re.split(r"(?<=[.!?])\s+", raw)
            pieces = []
            temp = ""

            for s in sentences:
                s = s.strip()
                if not s:
                    continue

                if len(temp) + len(s) < 900:
                    temp += (" " + s)
                else:
                    if len(temp.strip()) >= min_chars:
                        pieces.append(temp.strip())
                    temp = s

            if len(temp.strip()) >= min_chars:
                pieces.append(temp.strip())

        for piece in pieces:
            piece = normalize_text(piece)

            if len(piece) < min_chars:
                continue

            heading = is_heading_block(piece)

            if heading:
                current_context = context_key_from_heading(piece)
                current_heading = piece

            kind = infer_line_kind(piece)

            blocks.append({
                "index": idx,
                "text": piece,
                "norm": simple_norm(piece),
                "numbers": extract_numbers(piece),
                "main_numbers": main_numeric_values(piece),
                "is_heading": heading,
                "line_kind": kind,
                "context_key": current_context,
                "context_label": current_heading,
            })
            idx += 1

    return blocks


# =========================================================
# Matching
# =========================================================

def line_kind_compatible(a_kind: str, b_kind: str) -> bool:
    if a_kind == b_kind:
        return True

    compatible = {
        ("valeur_reelle", "valeur"),
        ("valeur", "valeur_reelle"),
        ("valeur_sdw", "valeur"),
        ("valeur", "valeur_sdw"),
        ("ecart", "text"),
        ("text", "ecart"),
    }

    return (a_kind, b_kind) in compatible


def candidate_score(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    base = ratio(a.get("norm", ""), b.get("norm", ""))

    same_context = a.get("context_key") == b.get("context_key")
    same_kind = line_kind_compatible(a.get("line_kind", ""), b.get("line_kind", ""))

    if same_context:
        base += 0.20
    else:
        base -= 0.30

    if same_kind:
        base += 0.10
    else:
        base -= 0.15

    if bool(a.get("is_heading")) != bool(b.get("is_heading")):
        base -= 0.30

    if numeric_conflict(a.get("text", ""), b.get("text", "")):
        base = min(base - 0.08, 0.88)

    if not same_context and numeric_conflict(a.get("text", ""), b.get("text", "")):
        base -= 0.35

    return max(0.0, min(round(base, 4), 1.0))


def find_best_match(
    a: Dict[str, Any],
    blocks_b: List[Dict[str, Any]],
    used_b: set,
) -> Tuple[Optional[Dict[str, Any]], float]:
    same_context_candidates = [
        b for b in blocks_b
        if b["index"] not in used_b
        and b.get("context_key") == a.get("context_key")
    ]

    candidates = same_context_candidates

    if not candidates:
        candidates = [
            b for b in blocks_b
            if b["index"] not in used_b
        ]

    best = None
    best_score = 0.0

    for b in candidates:
        sc = candidate_score(a, b)

        if sc > best_score:
            best_score = sc
            best = b

    return best, best_score


def inline_diff_html(a: str, b: str) -> Dict[str, str]:
    a_words = str(a or "").split()
    b_words = str(b or "").split()

    sm = difflib.SequenceMatcher(None, a_words, b_words)

    left: List[str] = []
    right: List[str] = []

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        aw = " ".join(a_words[i1:i2])
        bw = " ".join(b_words[j1:j2])

        if tag == "equal":
            esc = html.escape(aw)
            left.append(esc)
            right.append(esc)
        elif tag == "delete":
            left.append(f"<mark class='del'>{html.escape(aw)}</mark>")
        elif tag == "insert":
            right.append(f"<mark class='add'>{html.escape(bw)}</mark>")
        elif tag == "replace":
            left.append(f"<mark class='chg'>{html.escape(aw)}</mark>")
            right.append(f"<mark class='chg'>{html.escape(bw)}</mark>")

    return {
        "left_html": " ".join(x for x in left if x.strip()),
        "right_html": " ".join(x for x in right if x.strip()),
    }


def compare_blocks(
    blocks_a: List[Dict[str, Any]],
    blocks_b: List[Dict[str, Any]],
    same_threshold: float = 0.94,
    different_threshold: float = 0.58,
) -> Dict[str, Any]:
    used_b = set()

    identical: List[Dict[str, Any]] = []
    different: List[Dict[str, Any]] = []
    only_a: List[Dict[str, Any]] = []

    for a in blocks_a:
        b, score = find_best_match(a, blocks_b, used_b)

        if not b:
            only_a.append({
                "a_index": a["index"],
                "text": a["text"],
                "context_key": a.get("context_key"),
                "line_kind": a.get("line_kind"),
                "score": 0.0,
            })
            continue

        same_context = a.get("context_key") == b.get("context_key")

        if not same_context and score < 0.82:
            only_a.append({
                "a_index": a["index"],
                "text": a["text"],
                "context_key": a.get("context_key"),
                "line_kind": a.get("line_kind"),
                "best_score": round(score, 4),
                "best_b_index": b.get("index"),
            })
            continue

        num_conflict = numeric_conflict(a.get("text", ""), b.get("text", ""))

        if (
            score >= same_threshold
            and not num_conflict
            and same_numeric_signature(a.get("text", ""), b.get("text", ""))
        ):
            identical.append({
                "a_index": a["index"],
                "b_index": b["index"],
                "score": round(score, 4),
                "text": a["text"],
                "context_key": a.get("context_key"),
                "line_kind": a.get("line_kind"),
            })
            used_b.add(b["index"])

        elif score >= different_threshold:
            diff = inline_diff_html(a["text"], b["text"])
            different.append({
                "a_index": a["index"],
                "b_index": b["index"],
                "score": round(score, 4),
                "a_text": a["text"],
                "b_text": b["text"],
                "left_html": diff["left_html"],
                "right_html": diff["right_html"],
                "context_key": a.get("context_key"),
                "a_line_kind": a.get("line_kind"),
                "b_line_kind": b.get("line_kind"),
                "numeric_conflict": num_conflict,
            })
            used_b.add(b["index"])

        else:
            only_a.append({
                "a_index": a["index"],
                "text": a["text"],
                "context_key": a.get("context_key"),
                "line_kind": a.get("line_kind"),
                "best_score": round(score, 4),
                "best_b_index": b.get("index"),
            })

    only_b: List[Dict[str, Any]] = []

    for b in blocks_b:
        if b["index"] not in used_b:
            best_a = None
            best_score = 0.0

            for a in blocks_a:
                sc = candidate_score(b, a)

                if sc > best_score:
                    best_score = sc
                    best_a = a

            only_b.append({
                "b_index": b["index"],
                "text": b["text"],
                "context_key": b.get("context_key"),
                "line_kind": b.get("line_kind"),
                "best_score": round(best_score, 4),
                "best_a_index": best_a["index"] if best_a else None,
            })

    return {
        "identical": identical,
        "different_between_a_b": different,
        "only_in_a": only_a,
        "only_in_b": only_b,

        # aliases
        "modified": different,
        "removed_from_a": only_a,
        "added_in_b": only_b,
    }


def compare_documents(path_a: str, path_b: str) -> Dict[str, Any]:
    doc_a = load_text_from_file(path_a)
    doc_b = load_text_from_file(path_b)

    if not doc_a.get("ok"):
        return {
            "ok": False,
            "error": f"Document A non chargé : {doc_a.get('error')}",
            "doc_a": doc_a,
            "doc_b": doc_b,
        }

    if not doc_b.get("ok"):
        return {
            "ok": False,
            "error": f"Document B non chargé : {doc_b.get('error')}",
            "doc_a": doc_a,
            "doc_b": doc_b,
        }

    blocks_a = split_into_blocks(doc_a["text"])
    blocks_b = split_into_blocks(doc_b["text"])

    cmp_result = compare_blocks(blocks_a, blocks_b)

    total_a = len(blocks_a)
    total_b = len(blocks_b)

    summary = {
        "doc_a": doc_a["document"],
        "doc_b": doc_b["document"],
        "chars_a": doc_a.get("chars", 0),
        "chars_b": doc_b.get("chars", 0),
        "blocks_a": total_a,
        "blocks_b": total_b,
        "identical_count": len(cmp_result["identical"]),
        "different_count": len(cmp_result["different_between_a_b"]),
        "only_in_a_count": len(cmp_result["only_in_a"]),
        "only_in_b_count": len(cmp_result["only_in_b"]),

        # aliases
        "modified_count": len(cmp_result["different_between_a_b"]),
        "removed_count": len(cmp_result["only_in_a"]),
        "added_count": len(cmp_result["only_in_b"]),
    }

    denom = max(total_a, total_b, 1)
    raw_rate = (
        summary["different_count"]
        + summary["only_in_a_count"]
        + summary["only_in_b_count"]
    ) / denom

    summary["change_rate"] = round(min(raw_rate, 1.0), 3)

    return {
        "ok": True,
        "summary": summary,
        "doc_a": doc_a,
        "doc_b": doc_b,
        "blocks_a": blocks_a,
        "blocks_b": blocks_b,
        "comparison": cmp_result,
    }


# =========================================================
# Auto-détection équilibrée
# =========================================================

def normalize_filename_group(filename: str) -> str:
    name = Path(filename).stem.lower()

    replacements = [
        r"\(\d+\)",
        r"\bcopy\b",
        r"\bcopie\b",
        r"\bversion\b",
        r"\bmodifiee\b",
        r"\bmodifiée\b",
        r"\bmodifie\b",
        r"\bmodifié\b",
        r"\bcompare\b",
        r"\bcomparaison\b",
        r"\bv\d+\b",
        r"\brev\d+\b",
        r"\brev\s*\d+\b",
        r"\bmaj\b",
        r"\bupdate\b",
        r"\b\d{1,2}[-_/]\d{1,2}[-_/]\d{2,4}\b",
    ]

    for pat in replacements:
        name = re.sub(pat, " ", name, flags=re.I)

    name = re.sub(r"[_\-.]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()

    return simple_norm(name)


def filename_similarity(a: str, b: str) -> float:
    return ratio(normalize_filename_group(a), normalize_filename_group(b))


def extract_leading_drawing_code(filename: str) -> str:
    stem = Path(filename).stem.strip()
    m = re.match(r"^\s*(\d{4,6})\b", stem)
    return m.group(1) if m else ""


def has_version_marker(filename: str) -> bool:
    name = Path(filename).stem.lower()

    patterns = [
        r"\(\d+\)",
        r"\bcopy\b",
        r"\bcopie\b",
        r"\bversion\b",
        r"\bv\d+\b",
        r"\brev\d+\b",
        r"\brev\s*\d+\b",
        r"\bmodifiee\b",
        r"\bmodifiée\b",
        r"\bmodifie\b",
        r"\bmodifié\b",
        r"\bcompare\b",
        r"\bcomparaison\b",
        r"\bmaj\b",
        r"\bupdate\b",
    ]

    return any(re.search(p, name, flags=re.I) for p in patterns)


def important_tokens(filename: str) -> set:
    name = normalize_filename_group(filename)
    tokens = set(name.split())

    stop = {
        "de", "du", "des", "et", "en", "la", "le", "les", "un", "une",
        "pour", "avec", "sans", "ss", "sur", "au", "aux",
        "pdf", "docx", "xlsx", "tgm100", "tgm60", "tgm", "ng",
        "ensemble", "etude", "étude", "details", "détails",
    }

    return {t for t in tokens if len(t) >= 3 and t not in stop}


def classify_pair(file_a: str, file_b: str) -> Dict[str, Any]:
    """
    Retourne une décision équilibrée :
    - strong : doublon ou vraie version probable
    - medium : candidat utile mais à confirmer
    - reject : trop large / thème seulement
    """
    name_a = Path(file_a).name
    name_b = Path(file_b).name

    group_a = normalize_filename_group(name_a)
    group_b = normalize_filename_group(name_b)
    sim = ratio(group_a, group_b)

    code_a = extract_leading_drawing_code(name_a)
    code_b = extract_leading_drawing_code(name_b)

    marker = has_version_marker(name_a) or has_version_marker(name_b)

    tokens_a = important_tokens(name_a)
    tokens_b = important_tokens(name_b)
    common = tokens_a & tokens_b

    if group_a == group_b:
        return {
            "decision": "strong",
            "reason": "doublon_ou_meme_nom_normalise",
            "similarity": sim,
            "common_tokens": sorted(common),
        }

    if code_a and code_b and code_a == code_b:
        return {
            "decision": "strong",
            "reason": "meme_code_dessin",
            "similarity": sim,
            "common_tokens": sorted(common),
        }

    if marker and sim >= 0.70 and len(common) >= 2:
        return {
            "decision": "strong",
            "reason": "version_ou_modification_probable",
            "similarity": sim,
            "common_tokens": sorted(common),
        }

    # Codes différents : on ne rejette pas toujours, mais on baisse en medium si le thème est très proche.
    if code_a and code_b and code_a != code_b:
        if sim >= 0.82 and len(common) >= 2:
            return {
                "decision": "medium",
                "reason": "codes_differents_mais_theme_proche",
                "similarity": sim,
                "common_tokens": sorted(common),
            }
        return {
            "decision": "reject",
            "reason": "codes_dessin_differents",
            "similarity": sim,
            "common_tokens": sorted(common),
        }

    if sim >= 0.78 and len(common) >= 3:
        return {
            "decision": "medium",
            "reason": "noms_proches_a_confirmer",
            "similarity": sim,
            "common_tokens": sorted(common),
        }

    return {
        "decision": "reject",
        "reason": "similarite_trop_large_ou_theme_seul",
        "similarity": sim,
        "common_tokens": sorted(common),
    }


def discover_comparable_file_pairs(
    project_uploaded_dir: str,
    min_similarity: float = 0.70,
    include_medium: bool = True,
    max_pairs: int = 30,
) -> List[Dict[str, Any]]:
    folder = Path(project_uploaded_dir)

    if not folder.exists():
        return []

    files = [
        p for p in sorted(folder.iterdir(), key=lambda x: x.name.lower())
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    ]

    pairs: List[Dict[str, Any]] = []

    for a, b in itertools.combinations(files, 2):
        cls = classify_pair(a.name, b.name)

        if cls["similarity"] < min_similarity:
            continue

        if cls["decision"] == "reject":
            continue

        if cls["decision"] == "medium" and not include_medium:
            continue

        pairs.append({
            "file_a": str(a).replace("\\", "/"),
            "file_b": str(b).replace("\\", "/"),
            "name_a": a.name,
            "name_b": b.name,
            "similarity": round(cls["similarity"], 4),
            "decision": cls["decision"],
            "reason": cls["reason"],
            "common_tokens": cls["common_tokens"],
            "group_a": normalize_filename_group(a.name),
            "group_b": normalize_filename_group(b.name),
        })

    decision_rank = {"strong": 0, "medium": 1}
    pairs = sorted(
        pairs,
        key=lambda x: (decision_rank.get(x["decision"], 9), -x["similarity"]),
    )

    return pairs[:max_pairs]


def report_filename_for_pair(file_a: str, file_b: str) -> str:
    a = safe_id(Path(file_a).stem, 45)
    b = safe_id(Path(file_b).stem, 45)
    return f"compare__{a}__VS__{b}.json"


def compare_pair_to_report(
    file_a: str,
    file_b: str,
    output_dir: str,
    force: bool = True,
) -> Dict[str, Any]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    report_path = out / report_filename_for_pair(file_a, file_b)

    if report_path.exists() and not force:
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["_report_path"] = str(report_path).replace("\\", "/")
            return report
        except Exception:
            pass

    report = compare_documents(file_a, file_b)
    report["_report_path"] = str(report_path).replace("\\", "/")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    return report


def auto_compare_project_pairs(
    project_uploaded_dir: str,
    output_dir: str,
    min_similarity: float = 0.70,
    include_medium: bool = True,
    force: bool = False,
) -> Dict[str, Any]:
    """
    Ne compare pas tout automatiquement.
    Prépare l'index des paires détectées.
    La comparaison complète est faite quand l'utilisateur clique sur une paire.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    pairs = discover_comparable_file_pairs(
        project_uploaded_dir,
        min_similarity=min_similarity,
        include_medium=include_medium,
    )

    index = {
        "ok": True,
        "project_uploaded_dir": str(project_uploaded_dir),
        "output_dir": str(out).replace("\\", "/"),
        "pairs_count": len(pairs),
        "pairs": pairs,
        "reports": [],
    }

    (out / "auto_compare_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return index
