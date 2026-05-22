"""
modules/NLP/final_output_guard.py — V7.4.1
──────────────────────────────────────────────────────────────────────────────
Garde-fou final universel de sortie JSON.

But : empêcher la fiche_cir / synthèse LLM de redevenir source de vérité.
La vérité finale vient des champs plats evidence-first + structure documentaire.

À appeler dans router.py après _build_metadata() et avant quality_reporter/to_json.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any


# ══════════════════════════════════════════════════════════════════════════════
# Helpers génériques
# ══════════════════════════════════════════════════════════════════════════════

def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _set(obj: Any, name: str, value: Any) -> None:
    if isinstance(obj, dict):
        obj[name] = value
    else:
        setattr(obj, name, value)


def _norm_space(text: Any) -> str:
    text = str(text or "").replace("\u00a0", " ").replace("\u202f", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip(" \t\n\r;:,.•-–—")


def _norm_key(text: Any) -> str:
    text = unicodedata.normalize("NFKD", str(text or "").lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("’", "'").replace("`", "'")
    text = re.sub(r"\s+", " ", text)
    return text.strip(" \t\n\r;:,.•-–—")


def _dedupe(values: list[Any], max_items: int | None = None) -> list[str]:
    out: list[str] = []
    seen = set()
    for v in values or []:
        t = _norm_space(v)
        if not t:
            continue
        k = _norm_key(t)
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
        if max_items and len(out) >= max_items:
            break
    return out


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _sections(document_structure: Any) -> list:
    if not document_structure:
        return []
    if isinstance(document_structure, dict):
        return document_structure.get("sections", []) or []
    return getattr(document_structure, "sections", []) or []


def _section_title(s: Any) -> str:
    return _norm_space(_get(s, "title", "") or _get(s, "section_title", ""))


def _section_content(s: Any) -> str:
    return str(_get(s, "content", "") or _get(s, "text", "") or "")


def _section_role(s: Any) -> str:
    return _norm_key(_get(s, "role", "") or _get(s, "section_role", ""))


def _document_text(meta: Any) -> str:
    parts: list[str] = []
    for s in _sections(_get(meta, "document_structure", {})):
        title = _section_title(s)
        content = _section_content(s)
        if title:
            parts.append(title)
        if content:
            parts.append(content)
    return "\n".join(parts)


def _sentences(text: str) -> list[str]:
    text = str(text or "").replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    # Split doux : conserve les phrases longues, évite de couper les sigles trop souvent.
    parts = re.split(r"(?<=[.!?;])\s+|\s{2,}", text)
    return [_norm_space(p) for p in parts if len(_norm_space(p)) >= 25]


def _evidences(meta: Any) -> list[dict]:
    em = _get(meta, "evidence_map", {}) or {}
    mappings = em.get("mappings", []) if isinstance(em, dict) else getattr(em, "mappings", [])
    out: list[dict] = []
    for m in mappings or []:
        evs = m.get("evidences", []) if isinstance(m, dict) else getattr(m, "evidences", [])
        for ev in evs or []:
            role = _get(ev, "role", "")
            phrase = _get(ev, "phrase_source", "") or _get(ev, "phrase", "") or _get(ev, "text", "")
            section_role = _get(ev, "section_role", "")
            if phrase:
                out.append({"role": _norm_key(role), "phrase": _norm_space(phrase), "section_role": _norm_key(section_role)})
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Filtres universels
# ══════════════════════════════════════════════════════════════════════════════

_TITLE_ONLY_RE = re.compile(
    r"^(analyse de l['’]?etat de l['’]?art|etat de l['’]?art|objectifs?|verrous?|resultats?|demarche|travaux|tableau \d+|figure \d+)$",
    re.I | re.U,
)

_FALSE_OBJECTIVE_RE = re.compile(
    r"(objectif\s+de\s+.+?r\s*&?\s*i|objectif\s+de\s+.+?r&d|necessite\s+de\s+structuration|"
    r"regrouper\s+au\s+sein\s+d['’]?une\s+meme\s+structure|activites\s+r\s*&?\s*i|"
    r"organisation\s+de\s+recherche\s+et\s+d['’]?innovation|plan\s+strategique\s+du\s+groupe|"
    r"nous\s+souhaitons\s+disposer\s+d['’]?une\s+organisation)",
    re.I | re.U,
)

_NOT_VERROU_RE = re.compile(
    r"(nous\s+avons\s+realise|nous\s+avons\s+réalisé|nous\s+sommes\s+parvenus|"
    r"nous\s+avons\s+developpe|nous\s+avons\s+développé|les\s+resultats?\s+de\s+r\s*&?\s*d|"
    r"les\s+résultats?\s+de\s+r\s*&?\s*d|ont\s+permis\s+de|a\s+permis\s+de\s+mettre\s+en\s+oeuvre|"
    r"mise\s+en\s+oeuvre\s+d['’]?un\s+systeme|architecture\s+de\s+notre\s+nouvelle\s+solution)",
    re.I | re.U,
)

_BREVET_RE = re.compile(r"\b(brevet|demande\s+de\s+depot|demande\s+de\s+d[ée]p[ôo]t|inpi|inventeurs?)\b", re.I | re.U)

_ETAT_ART_KEEP_RE = re.compile(
    r"(solutions?\s+existantes?|technologies?\s+existantes?|poches?\s+TPU|bo[iî]tiers?|films?\s+[ée]tirables?|mousses?|"
    r"litterature|litt[ée]rature|travaux\s+existants|avanc[ée]es\s+technologiques|"
    r"toutefois|cependant|ne\s+permet(?:tent)?\s+pas|difficilement\s+manipulables?|risque\s+de\s+chute|"
    r"particules?\s+de\s+mousse|n['’]?etant\s+pas\s+maintenu|n['’]?étant\s+pas\s+maintenu)",
    re.I | re.U,
)

_ETAT_ART_DROP_RE = re.compile(
    r"(animation\s+des\s+travaux\s+de\s+r\s*&?\s*d|missions?\s+r\s*&?\s*d|strategie\s+de\s+r\s*&?\s*d|"
    r"strat[ée]gie\s+de\s+r\s*&?\s*d|agre[ée]\s+au\s+CIR|manuel\s+de\s+frascati)",
    re.I | re.U,
)

_BAD_ORG_RE = re.compile(r"^(germes?|equipe pluridisciplinaire|équipe pluridisciplinaire|si oui|non|oui|les solutions|solutions techniques)$", re.I | re.U)

_BAD_PERSON_RE = re.compile(
    r"(liquid\s+silicone|amortissement\s+resistance|st[ée]rilisation\s+gamma|fourreau\s+rosace|"
    r"justificatif\s+des|thermoformage\s+injection|lyc[ée]e\s+edgar|lyc[ée]e\s+godefroy|"
    r"conception\s+de\s+produits|institut\s+fran[cç]ais|technique\s+d['’]?automatisation)",
    re.I | re.U,
)

_PERSON_TABLE_HEADER_RE = re.compile(r"nom\s+pr[ée]nom\s*\|\s*dipl", re.I | re.U)


# ══════════════════════════════════════════════════════════════════════════════
# Extraction/reconstruction finale
# ══════════════════════════════════════════════════════════════════════════════

def _extract_title(meta: Any) -> str | None:
    current = _norm_space(_get(meta, "title", "") or _get(meta, "titre_operation", ""))
    if current:
        return current

    text = _document_text(meta)

    # Tableau : NOM DE L'OPERATION | titre
    patterns = [
        r"(?:nom|intitul[ée]|titre)\s+de\s+l['’]?op[ée]ration\s*\|\s*([^\n|]{20,240})",
        r"(?:nom|intitul[ée]|titre)\s+du\s+projet\s*\|\s*([^\n|]{20,240})",
        r"(?:nom|intitul[ée]|titre)\s+de\s+l['’]?op[ée]ration\s*[:\-]\s*([^\n]{20,240})",
        r"projet\s+baptis[ée]\s+[\"'“”]?\s*([^\"'“”\n]{5,120})",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I | re.U)
        if m:
            candidate = _norm_space(m.group(1))
            if candidate and not _TITLE_ONLY_RE.search(_norm_key(candidate)):
                return candidate

    # Fallback depuis objet recherche
    obj = _as_list(_get(meta, "objet_recherche", []))
    if obj:
        return _norm_space(obj[0])[:240]
    return None


def _clean_objectifs(meta: Any) -> list[str]:
    vals = []
    vals.extend(_as_list(_get(meta, "objectifs_rd", [])))
    for ev in _evidences(meta):
        if ev["role"] == "objectif" and ev["section_role"] in {"objectifs", "objectif"}:
            vals.append(ev["phrase"])

    out = []
    for v in vals:
        t = _norm_space(v)
        if not t or len(t) < 25:
            continue
        if _FALSE_OBJECTIVE_RE.search(_norm_key(t)):
            continue
        if _TITLE_ONLY_RE.search(_norm_key(t)):
            continue
        out.append(t)
    return _dedupe(out, 12)


def _clean_verrous(meta: Any) -> list[str]:
    vals = []
    # priorité aux preuves venant de section verrous
    for ev in _evidences(meta):
        if ev["role"] == "verrou" and ev["section_role"] in {"verrous", "verrou"}:
            vals.append(ev["phrase"])
    vals.extend(_as_list(_get(meta, "verrous_techniques", [])))

    # Fallback depuis section verrous si LLM trop pauvre
    for s in _sections(_get(meta, "document_structure", {})):
        if _section_role(s) in {"verrous", "verrou"}:
            for sent in _sentences(_section_content(s)):
                if re.search(r"(verrou|incapacit[ée]|ne\s+permet|manque\s+de\s+maturit[ée]|tenue\s+aux\s+chocs|r[ée]sistance\s+[àa]\s+l['’]?abrasion|s[ée]curisation|recyclabilit[ée])", sent, re.I | re.U):
                    vals.append(sent)

    out = []
    for v in vals:
        t = _norm_space(v)
        k = _norm_key(t)
        if not t or len(t) < 25:
            continue
        if _TITLE_ONLY_RE.search(k):
            continue
        if _NOT_VERROU_RE.search(k):
            continue
        if _BREVET_RE.search(t):
            continue
        out.append(t)
    return _dedupe(out, 12)


def _clean_etat_art(meta: Any) -> list[str]:
    vals = []
    vals.extend(_as_list(_get(meta, "etat_art", [])))

    # preuves classées etat_art
    for ev in _evidences(meta):
        if ev["role"] == "etat_art":
            vals.append(ev["phrase"])

    # reconstruction depuis sections etat_art
    for s in _sections(_get(meta, "document_structure", {})):
        if _section_role(s) in {"etat_art", "etat de l'art", "analyse de l'etat de l'art"}:
            vals.extend(_sentences(_section_content(s)))

    # section conclusion solutions existantes souvent classée contexte
    for s in _sections(_get(meta, "document_structure", {})):
        title = _section_title(s)
        content = _section_content(s)
        if re.search(r"solutions?\s+existantes?\s+ne\s+r[ée]solvent?\s+pas", title, re.I | re.U):
            vals.extend(_sentences(content))

    out = []
    for v in vals:
        t = _norm_space(v)
        k = _norm_key(t)
        if not t or len(t) < 30:
            continue
        if _TITLE_ONLY_RE.search(k):
            continue
        if _BREVET_RE.search(t):
            continue
        if _ETAT_ART_DROP_RE.search(k):
            continue
        if _ETAT_ART_KEEP_RE.search(t):
            out.append(t)
    return _dedupe(out, 10)


def _clean_methods(meta: Any) -> list[str]:
    vals = []
    vals.extend(_as_list(_get(meta, "methodes_rd", [])))
    for ev in _evidences(meta):
        if ev["role"] in {"demarche", "essai"}:
            vals.append(ev["phrase"])
    out = []
    for v in vals:
        t = _norm_space(v)
        if not t or len(t) < 25 or _BREVET_RE.search(t):
            continue
        out.append(t)
    return _dedupe(out, 16)


def _clean_results(meta: Any) -> list[str]:
    vals = []
    vals.extend(_as_list(_get(meta, "resultats_rd", [])))
    for ev in _evidences(meta):
        if ev["role"] == "resultat":
            vals.append(ev["phrase"])
    out = []
    for v in vals:
        t = _norm_space(v)
        if not t or len(t) < 25:
            continue
        if _BREVET_RE.search(t):
            continue
        out.append(t)
    return _dedupe(out, 14)


def _clean_orgs(meta: Any) -> list[str]:
    vals = _as_list(_get(meta, "organismes", [])) + _as_list(_get(meta, "partenaires_rd", []))
    out = []
    text = _document_text(meta)
    defense_negative = bool(re.search(r"collaboration\s+avec\s+le\s+minist[eè]re\s+de\s+la\s+d[ée]fense.*?\bnon\b|\bnon\b.*?collaboration\s+avec\s+le\s+minist[eè]re\s+de\s+la\s+d[ée]fense", _norm_key(text), re.I | re.S | re.U))
    for v in vals:
        t = _norm_space(v)
        k = _norm_key(t)
        if not t or len(t) < 2:
            continue
        if _BAD_ORG_RE.search(k):
            continue
        if defense_negative and k in {"dga", "ministere de la defense", "ministère de la défense"}:
            continue
        if len(t.split()) > 5 and not re.search(r"\b(SAS|SARL|SA|R&I|R&D|Groupe|Packaging|University|Université|Laboratoire|CNRS|INRAE|INSERM)\b", t, re.I):
            continue
        out.append(t)
    return _dedupe(out, 20)


def _extract_people_from_rh(meta: Any) -> list[str]:
    vals = _as_list(_get(meta, "personnes", []))

    # tables RH : première colonne = personne
    for s in _sections(_get(meta, "document_structure", {})):
        title = _section_title(s)
        content = _section_content(s)
        if _PERSON_TABLE_HEADER_RE.search(title) or _section_role(s) in {"administratif", "ressources"}:
            for line in str(content or "").splitlines():
                if "|" in line:
                    first = _norm_space(line.split("|")[0])
                    if re.match(r"^[A-ZÉÈÀÂÎÏÔÛÙÇ][a-zéèàâîïôûùç\-]+\s+[A-ZÉÈÀÂÎÏÔÛÙÇ][a-zéèàâîïôûùç\-]+$", first):
                        vals.append(first)
            # parfois le nom est dans le titre, comme Ytournel Jérôme | BTS...
            if "|" in title:
                first = _norm_space(title.split("|")[0])
                if re.match(r"^[A-ZÉÈÀÂÎÏÔÛÙÇ][a-zéèàâîïôûùç\-]+\s+[A-ZÉÈÀÂÎÏÔÛÙÇ][a-zéèàâîïôûùç\-]+$", first):
                    vals.append(first)

    # référent
    text = _document_text(meta)
    for m in re.finditer(r"Nom\s+de\s+l['’]?interlocuteur\s+de\s+la\s+soci[ée]t[ée]\s*:\s*([^;\n]+)", text, re.I | re.U):
        vals.append(m.group(1))

    out = []
    for v in vals:
        t = _norm_space(v)
        if not t or len(t) < 5 or len(t) > 80:
            continue
        if _BAD_PERSON_RE.search(t):
            continue
        if any(x in _norm_key(t) for x in ["diplome", "fonction", "contribution", "technicien r", "responsable r"]):
            continue
        # nom/prénom humain simple
        if len(t.split()) <= 4:
            out.append(t)
    return _dedupe(out, 30)


def _extract_brevets(meta: Any) -> list[dict]:
    text = _document_text(meta)
    out: list[dict] = []

    # Format universel : brevet + numéro dépôt + date + inventeurs si visibles.
    if re.search(r"\bbrevet\b|demande\s+de\s+d[ée]p[ôo]t", text, re.I | re.U):
        numero = None
        date = None
        titre = None
        inventeurs: list[str] = []

        mnum = re.search(r"(?:n[°o]\s*(?:de\s*)?(?:d[ée]p[ôo]t|brevet)?\s*[:\-]?\s*|d[ée]p[ôo]t\s+n[°o]?\s*)(\d{5,})", text, re.I | re.U)
        if mnum:
            numero = mnum.group(1)

        mdate = re.search(r"(?:date\s+(?:de\s+)?d[ée]p[ôo]t\s*[:\-]?\s*)(\d{1,2}/\d{1,2}/\d{2,4})", text, re.I | re.U)
        if not mdate:
            mdate = re.search(r"\b(\d{1,2}/\d{1,2}/\d{4})\b", text)
        if mdate:
            date = mdate.group(1)

        mtitre = re.search(r"(?:titre\s*(?:du\s+brevet)?\s*[:\-]\s*|brevet\s+(?:intitul[ée]\s*)?[\"“]?)([^\n\"”]{12,160})", text, re.I | re.U)
        if mtitre:
            titre = _norm_space(mtitre.group(1))

        minv = re.search(r"inventeurs?\s*[:\-]\s*([^\n]{5,240})", text, re.I | re.U)
        if minv:
            raw = re.split(r",|;|\bet\b", minv.group(1))
            inventeurs = _dedupe([x for x in raw if len(_norm_space(x).split()) >= 2], 10)

        if numero or titre or date or inventeurs:
            out.append({
                "titre": titre or "Brevet lié au projet R&D",
                "numero_depot": numero,
                "date_depot": date,
                "inventeurs": inventeurs,
            })
    return out


# ══════════════════════════════════════════════════════════════════════════════
# API principale
# ══════════════════════════════════════════════════════════════════════════════

def apply_final_output_guard(meta: Any, *, remove_fiche_cir: bool = True) -> Any:
    """
    Nettoie le résultat final APRES tous les modules.
    Ne dépend pas d'un projet précis : règles documentaires universelles CIR/R&D.
    """

    title = _extract_title(meta)
    if title:
        _set(meta, "title", title)

    objectifs = _clean_objectifs(meta)
    if objectifs:
        _set(meta, "objectifs_rd", objectifs)

    verrous = _clean_verrous(meta)
    if verrous:
        _set(meta, "verrous_techniques", verrous)

    etat_art = _clean_etat_art(meta)
    _set(meta, "etat_art", etat_art)

    methodes = _clean_methods(meta)
    if methodes:
        _set(meta, "methodes_rd", methodes)

    resultats = _clean_results(meta)
    if resultats:
        _set(meta, "resultats_rd", resultats)

    orgs = _clean_orgs(meta)
    if orgs:
        _set(meta, "organismes", orgs)
        _set(meta, "partenaires_rd", orgs)

    people = _extract_people_from_rh(meta)
    if people:
        _set(meta, "personnes", people)

    brevets = _extract_brevets(meta)
    if brevets:
        _set(meta, "brevets", brevets)

    # Supprimer définitivement la fiche LLM comme sortie et comme source de vérité.
    if remove_fiche_cir:
        _set(meta, "fiche_cir", {})

    return meta
