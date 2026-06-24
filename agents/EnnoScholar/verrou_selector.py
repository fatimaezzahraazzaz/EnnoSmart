# -*- coding: utf-8 -*-
from __future__ import annotations

"""
verrou_selector.py — EnnoScholar V2.2

Rôle :
Ne pas envoyer n'importe quel extrait NLP à EnnoScholar.

Problème observé :
- Des paramètres/preuves comme "120 l/min = 7.2m3/h @250bar AVERTISSEMENT..."
  étaient envoyés comme verrous.
- EnnoScholar cherchait donc des articles sur le un mauvais objet technique au lieu de chercher
  le vrai problème scientifique.

Méthode :
1. Lire le pack NLP/Frascati.
2. Rejeter les items explicitement rejetés comme verrou.
3. Transformer les thèmes Frascati génériques en sujets techniques à partir des meilleurs passages sources.
4. Ne garder que les sujets qui portent un phénomène technique : fuite, usure, vibration, thermique,
   acoustique, dégradation, précision, robustesse, etc.
5. Les paramètres deviennent contexte, pas verrou principal.

Cette étape est générique : elle ne dépend pas d'un domaine fermé.
"""

import re
from typing import Any, Dict, List, Tuple

from .utils import clean_text, clean_title, dedupe_keep_order, norm, remove_frascati_question_text, token_set


PACK_KEYS = [
    "verrous_rnd_locaux",
    "limites_locales",
    "methodes_locales",
    "resultats_locaux",
    "parametres_locaux",
    "objectifs_locaux",
    "contributions_locales",
]


REJECT_SOURCE_CATEGORIES = {
    "parametres_locaux",
    "objectifs_locaux",
    "resultats_locaux",
    "contributions_locales",
}

TECHNICAL_PROBLEM_MARKERS = {
    # général
    "incertitude", "verrou", "limite", "limitation", "risque", "défaillance", "defaillance",
    "non conforme", "difficile", "instable", "dégradation", "degradation", "usure",
    "erreur", "precision", "précision", "robustesse", "performance",
    # mécanique/physique
    "technical_issue", "technical_issue", "technical_issue", "technical_issue", "technical_issue", "technical_issue", "technical_issue", "technical_issue",
    "technical_issue", "frottement", "friction", "vibration", "acoustique", "bruit", "thermal",
    "thermique", "refroidissement", "température", "temperature", "condensat", "séchage",
    # chimie/bio/logiciel/etc.
    "corrosion", "oxydation", "stabilité", "stabilite", "toxicité", "toxicite",
    "classification", "détection", "detection", "latence", "scalabilité", "scalabilite",
    "biais", "sensibilité", "sensibilite", "spécificité", "specificite",
}

GENERIC_FRASCATI_LABELS = {
    "performance insuffisante sous contrainte",
    "comportement instable ou non maitrise",
    "comportement instable ou non maîtrisé",
    "maitrise thermique refroidissement",
    "maîtrise thermique refroidissement",
    "qualite sortie non conforme difficile garantir",
    "qualité de sortie non conforme ou difficile à garantir",
    "fiabilite usure degradation fonctionnement",
    "fiabilité usure ou dégradation en fonctionnement",
}

BOILERPLATE_PATTERNS = [
    r"^120\s*l/min.*?avertissement\s*",
    r"avertissement\s+le\s+débitmètre.*?(?=si\s+|$)",
    r"avertissement\s+le\s+debitmetre.*?(?=si\s+|$)",
    r"^préambule\s*:?",
    r"^preambule\s*:?",
    r"^révisions?\s+[a-z]\s*\|.*",
    r"^revisions?\s+[a-z]\s*\|.*",
    r"^ci-dessous\s+les\s+relevés.*?(?=\.|;|$)",
    r"^ci-dessous\s+les\s+releves.*?(?=\.|;|$)",
]


def _get_pack(nlp_result: Dict[str, Any]) -> Dict[str, Any]:
    fg = nlp_result.get("frascati_guard") or {}
    if isinstance(fg, dict) and isinstance(fg.get("qualified_pack_for_ennodiagnostic"), dict):
        return fg["qualified_pack_for_ennodiagnostic"]

    return (
        nlp_result.get("multi_document_evidence_pack_for_ennodiagnostic")
        or nlp_result.get("merged_evidence_pack_for_ennodiagnostic")
        or nlp_result.get("evidence_pack_for_ennodiagnostic")
        or {}
    )


def _domain_detection(nlp_result: Dict[str, Any]) -> Dict[str, Any]:
    d = nlp_result.get("domain_detection")
    if isinstance(d, dict):
        return d

    for key in ["raw_result", "pre_cir_structured_result", "cir_structured_result"]:
        obj = nlp_result.get(key)
        if isinstance(obj, dict) and isinstance(obj.get("domain_detection"), dict):
            return obj["domain_detection"]
    return {}


def _decision(item: Dict[str, Any]) -> str:
    fr = item.get("frascati") or {}
    if isinstance(fr, dict) and fr.get("decision"):
        return str(fr.get("decision"))
    return str(item.get("frascati_decision") or item.get("quality_status") or "")


def _is_rejected(item: Dict[str, Any]) -> bool:
    if not isinstance(item, dict):
        return True
    if item.get("rejected_as_verrou"):
        return True
    final_role = str(item.get("final_role") or "").lower()
    decision = _decision(item).lower()
    if "faux_verrou" in decision:
        return True
    if "rejected" in decision:
        return True
    if "parametre_ou_contrainte" in final_role:
        return True
    return False


def _item_category(item: Dict[str, Any], fallback: str = "") -> str:
    return str(item.get("_source_category") or item.get("source_category") or fallback or "")


def _clean_passage(text: Any) -> str:
    s = remove_frascati_question_text(text)
    for pat in BOILERPLATE_PATTERNS:
        s = re.sub(pat, " ", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip()
    return clean_text(s, 1200)


def _sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?;])\s+|\n+", text)
    return [clean_text(p, 500) for p in parts if len(clean_text(p, 500)) >= 20]


def _problem_score(text: str) -> float:
    n = norm(text)
    score = 0.0
    for marker in TECHNICAL_PROBLEM_MARKERS:
        if norm(marker) in n:
            score += 1.0

    # Unités = contexte, mais pas forcément verrou. Petit score seulement.
    units = re.findall(r"\b\d+(?:[,.]\d+)?\s?(?:bar|bars|mpa|kw|w|°c|m3/h|m³/h|hz|db|mm|cm|l/min)\b", text, flags=re.I)
    score += min(len(units), 3) * 0.2

    # Phrase conditionnelle ou action de correction = signal utile.
    if re.search(r"\b(si|lorsque|quand|en cas de)\b", n):
        score += 0.8
    if re.search(r"\b(changer|intervenir|corriger|réduire|ameliorer|améliorer|maitriser|maîtriser|garantir)\b", n):
        score += 0.8

    # pénalité pour warning/mesure brute
    if "avertissement" in n or "debitmetre" in n or "débitmètre" in n:
        score -= 1.0
    if n.startswith("120 l/min") or n.startswith("ci dessous") or n.startswith("ci-dessous"):
        score -= 1.0

    return score


def _best_problem_sentence(text: str) -> str:
    cleaned = _clean_passage(text)
    sents = _sentences(cleaned)
    if not sents:
        return cleaned

    ranked = sorted(sents, key=_problem_score, reverse=True)
    best = ranked[0]
    if _problem_score(best) <= 0.3:
        # fallback : texte nettoyé mais court
        return clean_text(cleaned, 350)
    return best


def _extract_passages(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    passages = []

    def add(text: Any, source: Dict[str, Any] | None = None):
        txt = _clean_passage(text)
        if not txt:
            return
        src = dict(source or {})
        src["text"] = txt
        src["problem_sentence"] = _best_problem_sentence(txt)
        src["problem_score"] = _problem_score(src["problem_sentence"])
        passages.append(src)

    # item text
    add(item.get("text"), item)

    # supporting passages
    supporting = item.get("supporting_passages") or []
    if isinstance(supporting, list):
        for sp in supporting[:10]:
            if isinstance(sp, dict):
                add(sp.get("text") or sp.get("source_text"), sp)

    # raw item supporting
    raw = item.get("raw_item") or {}
    if isinstance(raw, dict):
        add(raw.get("text"), raw)
        supp = raw.get("supporting_passages") or []
        if isinstance(supp, list):
            for sp in supp[:10]:
                if isinstance(sp, dict):
                    add(sp.get("text") or sp.get("source_text"), sp)

    return passages


def _is_scientific_topic_sentence(sentence: str) -> bool:
    return _problem_score(sentence) >= 1.0


def _topic_title_from_sentence(sentence: str) -> str:
    s = _clean_passage(sentence)

    # raccourcir les introductions
    s = re.sub(r"^si\s+", "", s, flags=re.I)
    s = re.sub(r"^lorsque\s+", "", s, flags=re.I)

    # titre jusqu'à première virgule/point si suffisant
    parts = re.split(r"[,.;:]", s)
    for p in parts:
        p = clean_title(p)
        if len(p) >= 25:
            return clean_text(p, 140)

    return clean_text(clean_title(s), 140)


def _similar_topic(a: str, b: str) -> bool:
    ta, tb = token_set(a), token_set(b)
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / max(1, min(len(ta), len(tb)))
    return overlap >= 0.55


def _context_for_topic(topic_sentence: str, pack: Dict[str, Any]) -> Dict[str, List[str]]:
    ctx = {"objectifs": [], "methodes": [], "resultats": [], "parametres": [], "limites": []}
    mapping = {
        "objectifs": "objectifs_locaux",
        "methodes": "methodes_locales",
        "resultats": "resultats_locaux",
        "parametres": "parametres_locaux",
        "limites": "limites_locales",
    }
    topic_tokens = token_set(topic_sentence)

    for out_key, pack_key in mapping.items():
        scored = []
        for it in pack.get(pack_key) or []:
            if not isinstance(it, dict):
                continue
            txt = _clean_passage(it.get("text"))
            if not txt:
                continue
            toks = token_set(txt)
            if not toks:
                continue
            score = len(topic_tokens & toks) / max(1, min(len(topic_tokens), len(toks)))
            if score >= 0.12:
                scored.append((score, txt))
        scored.sort(reverse=True)
        ctx[out_key] = [x[1] for x in scored[:3]]

    return ctx


def select_scholar_verrous_from_nlp(nlp_result: Dict[str, Any], max_verrous: int = 8) -> Dict[str, Any]:
    pack = _get_pack(nlp_result)
    domain = _domain_detection(nlp_result)

    candidate_items: List[Tuple[str, Dict[str, Any]]] = []

    # Priorité aux vrais verrous, mais on inspecte aussi limites/méthodes pour reconstruire si utile.
    for key in PACK_KEYS:
        for item in pack.get(key) or []:
            if isinstance(item, dict):
                candidate_items.append((key, item))

    topics = []

    for category, item in candidate_items:
        # Ne pas prendre un paramètre/objectif comme verrou direct,
        # mais ses supporting passages peuvent contenir une phrase de problème.
        rejected = _is_rejected(item)
        passages = _extract_passages(item)

        for p in passages:
            sent = p.get("problem_sentence") or ""
            if not _is_scientific_topic_sentence(sent):
                continue

            # Si l'item est rejeté ou vient de paramètre, il faut un signal technique fort.
            if rejected or category in REJECT_SOURCE_CATEGORIES:
                if _problem_score(sent) < 2.0:
                    continue

            title = _topic_title_from_sentence(sent)
            if not title:
                continue

            # éviter doublons
            duplicate = False
            for t in topics:
                if _similar_topic(title, t["title"]) or _similar_topic(sent, t["text"]):
                    duplicate = True
                    # enrichir sources si doublon
                    t["sources"].append({
                        "document": p.get("document") or item.get("document"),
                        "section": p.get("section_title") or item.get("section_title"),
                        "source_path": p.get("source_path") or item.get("source_path"),
                        "excerpt": sent,
                        "source_category": category,
                    })
                    break
            if duplicate:
                continue

            topics.append({
                "verrou_id": item.get("verrou_id") or item.get("theme_id") or f"scholar_topic_{len(topics)+1}",
                "title": title,
                "text": sent,
                "domain_detection": domain,
                "frascati": item.get("frascati") or {
                    "score": item.get("frascati_score"),
                    "decision": _decision(item),
                },
                "nlp_scores": {
                    "confidence": item.get("confidence"),
                    "verrou_score": item.get("verrou_score"),
                    "rank_score": item.get("rank_score"),
                    "quality_status": item.get("quality_status"),
                    "final_role": item.get("final_role"),
                    "source_category": category,
                    "selector_problem_score": _problem_score(sent),
                    "selector_version": "v2_2_verrou_selector",
                    "selected_from_rejected_or_context_item": bool(rejected or category in REJECT_SOURCE_CATEGORIES),
                },
                "context": _context_for_topic(sent, pack),
                "sources": [{
                    "document": p.get("document") or item.get("document"),
                    "section": p.get("section_title") or item.get("section_title"),
                    "source_path": p.get("source_path") or item.get("source_path"),
                    "excerpt": sent,
                    "source_category": category,
                }],
                "raw_item": item,
            })

    # Trier : vrais verrous d'abord, puis score problème.
    topics.sort(
        key=lambda t: (
            0 if t["nlp_scores"].get("selected_from_rejected_or_context_item") else 1,
            float(t["nlp_scores"].get("selector_problem_score") or 0),
            float(t["nlp_scores"].get("verrou_score") or 0),
            float(t["nlp_scores"].get("rank_score") or 0),
        ),
        reverse=True,
    )

    return {
        "domain_detection": domain,
        "verrous": topics[:max_verrous],
        "pack_counts": {k: len(pack.get(k) or []) for k in PACK_KEYS},
        "selector": {
            "version": "v2_2_verrou_selector",
            "topics_found": len(topics),
            "selection_rule": "technical_problem_sentence_from_source_evidence",
        },
    }
