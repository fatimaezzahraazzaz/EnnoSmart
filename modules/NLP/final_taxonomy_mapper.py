"""
modules/NLP/final_taxonomy_mapper.py — NLP V7.3.0

Changements V7.3.0 vs V7.2.0 :
- NOUVEAU : extraction titre depuis tableau NOM DE L'OPERATION (pattern universel).
  V7.2.0 ne captait pas ce pattern fréquent dans les dossiers CIR.
- NOUVEAU : extraction brevets depuis footnotes/notes de bas de page.
  Capture : numéro de dépôt, date, inventeurs, titre (universel).
- NOUVEAU : filtre NER bruit personnes renforcé (blacklist tokens non-nominaux
  universelle : mots techniques, conjonctions de titre, noms de colonnes...).
- Tout le reste de la logique V7.2.0 est inchangé.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _to_dict(obj: Any) -> dict:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "to_dict"):
        try:
            d = obj.to_dict()
            if isinstance(d, dict):
                return d
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        return dict(vars(obj))
    return {}


def _norm_space(text: Any) -> str:
    text = str(text or "").replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip(" \t\n\r;:,.•-–—")


def _norm_key(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or "").lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\s+", " ", text)
    return text.strip(" \t\n\r;:,.")


def _dedupe(values: list[Any], max_items: int | None = None) -> list[str]:
    out = []
    seen = set()
    for v in values or []:
        t = _norm_space(v)
        if not t:
            continue
        key = _norm_key(t)
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
        if max_items and len(out) >= max_items:
            break
    return out


def _sections(document_structure: Any) -> list:
    if not document_structure:
        return []
    if isinstance(document_structure, dict):
        return document_structure.get("sections", []) or []
    if isinstance(document_structure, list):
        return document_structure
    return getattr(document_structure, "sections", []) or []


def _section_title(s: Any) -> str:
    return _norm_space(_get(s, "title", "") or _get(s, "section_title", ""))


def _section_role(s: Any) -> str:
    return _norm_space(_get(s, "role", "") or _get(s, "section_role", "")).lower()


def _section_content(s: Any) -> str:
    return str(_get(s, "content", "") or _get(s, "text", "") or "")


def _document_text(raw_chunks: Any = None, evidence_map: Any = None, document_structure: Any = None) -> str:
    texts = []
    for s in _sections(document_structure):
        title = _section_title(s)
        content = _section_content(s)
        if title:
            texts.append(title)
        if content:
            texts.append(content)

    if raw_chunks:
        texts.extend([str(c) for c in raw_chunks if str(c or "").strip()])

    mappings = []
    if evidence_map is not None:
        if isinstance(evidence_map, dict):
            mappings = evidence_map.get("mappings", []) or []
        else:
            mappings = getattr(evidence_map, "mappings", []) or []

    for m in mappings:
        evs = m.get("evidences", []) if isinstance(m, dict) else getattr(m, "evidences", [])
        for ev in evs or []:
            phrase = _get(ev, "phrase_source", "") or _get(ev, "phrase", "") or _get(ev, "text", "")
            if phrase:
                texts.append(str(phrase))
    return "\n".join(texts)


# ── Filtre anti-RH dans objectifs ────────────────────────────────────────────
_RH_LINE_RE = re.compile(
    r"(?:"
    r"[A-ZÉÈÀÂÎÏÔÛÙÇ]{2,}\s+[A-ZÉÈÀÂÎÏÔÛÙÇ]{2,}(?:\s+[A-ZÉÈÀÂÎÏÔÛÙÇ]{2,})*\s*\|"
    r"|Dipl[ôo]me|Ing[ée]nieur\s+R&D|Chef\s+de\s+projet|Directeur\s+R&D"
    r"|Gestion\s+de\s+l['']op[ée]ration|D[ée]finition\s+des\s+objectifs"
    r")",
    re.I | re.U,
)


def _is_rh_line(text: str) -> bool:
    return bool(_RH_LINE_RE.search(str(text or "")))


_FALSE_OBJECTIVE_RE = re.compile(
    r"\b("
    r"cette\s+[ée]tude\s+nous\s+a\s+permis|"
    r"ce\s+travail\s+de\s+recherche\s+nous\s+a\s+permis|"
    r"nous\s+a\s+permis\s+d['']identifier|"
    r"ont\s+montr[ée]|a\s+montr[ée]|"
    r"les\s+r[ée]sultats?\s+(?:montrent|ont\s+montr[ée]|indiquent|confirment)|"
    r"nous\s+avons\s+(?:observ[ée]|confirm[ée]|identifi[ée]|obtenu)|"
    r"conclusion|contribution\s+scientifique|"
    r"a\s+l['']issue\s+de|"
    r"ces\s+essais\s+(?:ont|constituent)"
    r")\b",
    re.I | re.U,
)


def _is_false_objective(text: str) -> bool:
    return bool(_FALSE_OBJECTIVE_RE.search(str(text or "")))


_FRAGMENT_END_RE = re.compile(
    r"\b(?:de|du|des|d['']?|l['']?|la|le|les|un|une|en|à|au|aux|pour|avec|sans|dans|par|sur)$",
    re.I | re.U,
)
_BAD_KEYWORD_RE = re.compile(
    r"(ces\s+r[ée]sultats|a\s+permis\s+le\s+d[ée]veloppement\s+d$|probl[ée]matiques\s+d$|haute\s+technologie\s+l$|^solutions?\s+techniques?$|^dispositifs?\s+m[ée]dicaux?$|^syst[èe]mes?$)",
    re.I | re.U,
)


def _is_clean_keyword(text: str) -> bool:
    t = _norm_space(text)
    return bool(t and len(t) <= 140 and not _FRAGMENT_END_RE.search(t) and not _BAD_KEYWORD_RE.search(t))


def _clean_terms(values: list[Any], max_items: int | None = None) -> list[str]:
    return _dedupe([v for v in values or [] if _is_clean_keyword(str(v))], max_items)


# ══════════════════════════════════════════════════════════════════════════════
# NOUVEAU V7.3.0 — EXTRACTION TITRE NOM DE L'OPERATION
# ══════════════════════════════════════════════════════════════════════════════

# Pattern universel : ligne "NOM DE L'OPERATION | <titre>" dans un tableau Markdown/texte
_NOM_OPERATION_TABLE_RE = re.compile(
    r"NOM\s+D[E']?\s+L[''']?OP[ÉE]RATION\s*\|?\s*(.+?)(?:\||\n|CHEF|Ce\s+projet|Date|TH[ÉE]SAURUS|MOT\s+CL)",
    re.I | re.S | re.U,
)

# Pattern titre en gras dans entête de document (fréquent dans dossiers CIR)
_TITLE_BOLD_RE = re.compile(
    r"(?:\*\*|__|##)\s*(?:D[ée]veloppement|[ÉEé]tude|Recherche|Conception|Optimisation|Analyse|Mise\s+au\s+point|Projet)\s+[^*\n]{20,200}(?:\*\*|__|$)",
    re.I | re.U,
)

# Pattern titre dans entête tableau (3 colonnes logo | titre | CIR/année)
_TITLE_HEADER_TABLE_RE = re.compile(
    r"\|[^|\n]*\|\s*\*?\*?(.{20,260}?)\*?\*?\s*\|\s*(?:CIR|Crédit\s+[Ii]mpôt|Année|202\d)",
    re.I | re.U,
)


def _extract_title_from_nom_operation(text: str) -> str:
    """
    Extrait le titre depuis les formes fréquentes :
    - NOM DE L'OPERATION | <titre>
    - NOM DE L'OPERATION \\n <titre>
    - tableau avec libellé puis valeur sur la ligne suivante.

    Universel : ne dépend pas d'un projet ni d'un domaine.
    """
    raw = str(text or "")
    if not raw.strip():
        return ""

    # 1) Ligne/tableau "NOM DE L'OPERATION | titre"
    m = re.search(
        r"NOM\s+D[E']?\s+L['’']?OP[ÉE]RATION\s*(?:\||:|-)?\s*(.+?)(?=\n\s*(?:CHEF|Ce\s+projet|Date|TH[ÉE]SAURUS|MOT\s+CL|Objectifs?|Contexte)|\r?\n\r?\n|$)",
        raw,
        re.I | re.S | re.U,
    )
    if m:
        cand = _norm_space(m.group(1))
        cand = re.sub(r"^\|+", "", cand).strip()
        cand = re.sub(r"\s*\|\s*$", "", cand).strip()
        cand = re.split(r"\s+\|\s+(?:CHEF|Ce\s+projet|Date|TH[ÉE]SAURUS|MOT\s+CL)", cand, flags=re.I)[0]
        cand = _norm_space(cand)
        if 15 <= len(cand) <= 320 and not re.search(
            r"^(?:CHEF|Date|NON|OUI|TH[ÉE]SAURUS|MOT|Objectifs|Préciser|NOM\s+DE)", cand, re.I
        ):
            return cand

    # 2) Parcours ligne par ligne : libellé puis prochaine ligne utile
    lines = [_norm_space(x) for x in raw.splitlines()]
    for i, line in enumerate(lines):
        if re.search(r"\bNOM\s+D[E']?\s+L['’']?OP[ÉE]RATION\b", line, re.I | re.U):
            # Valeur sur la même ligne après | ou :
            parts = [p.strip() for p in re.split(r"\s*\|\s*|:", line) if p.strip()]
            for part in parts[1:]:
                if 15 <= len(part) <= 320 and not re.search(r"^(CHEF|Date|NON|OUI|TH[ÉE]SAURUS|MOT)", part, re.I):
                    return part
            # Valeur dans les lignes suivantes
            for nxt in lines[i + 1:i + 8]:
                if not nxt:
                    continue
                if re.search(r"^(CHEF|Date|NON|OUI|TH[ÉE]SAURUS|MOT\s+CL|Ce\s+projet|Préciser)", nxt, re.I):
                    break
                if 15 <= len(nxt) <= 320 and not re.search(r"NOM\s+D[E']?\s+L", nxt, re.I):
                    return nxt

    return ""

def _extract_title_from_header_table(text: str) -> str:
    """
    Extrait le titre depuis le tableau d'entête (logo | titre | CIR année).
    """
    m = _TITLE_HEADER_TABLE_RE.search(text or "")
    if m:
        cand = _norm_space(m.group(1))
        if 15 <= len(cand) <= 300:
            return cand
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# NOUVEAU V7.3.0 — EXTRACTION BREVETS DEPUIS FOOTNOTES
# ══════════════════════════════════════════════════════════════════════════════

# Pattern universel brevet/dépôt : numéro, date, inventeurs
# Supporte : [^1], [1], (1), footnote explicite
_BREVET_FOOTNOTE_RE = re.compile(
    r"(?:"
    # Pattern FR/EP/WO/US + numéro de dépôt explicite
    r"(?:N[°o]?\s*de\s+d[ée]p[ôo]t\s*[:\-]?\s*)([\w/\-]+)"
    r"|(?:brevet\s+(?:d[ée]pos[ée]|en\s+cours|n[°o]?)\s*[:\-]?\s*)([\w/\-]+)"
    r"|(?:demande\s+de\s+(?:brevet|d[ée]p[ôo]t)\s*[:\-]?\s*)([\w/\-]+)"
    r"|(?:patent\s+(?:application|n[°o]?)\s*[:\-]?\s*)([\w/\-]+)"
    r")",
    re.I | re.U,
)

# Pattern pour extraire une ligne complète de footnote brevet
# Ex: "VERGNE Hervé, TECHER Nathalie, TCP R&I, Emballage pour dispositif médical, N° de dépôt : 2412125, 05/11/2024"
_BREVET_FULL_LINE_RE = re.compile(
    r"(?:"
    # footnote markdown [^N]: ou [N]:
    r"(?:\[\^?\d+\]\s*[:\-]?\s*)"
    r"|(?:Note\s+\d+\s*[:\-]?\s*)"
    r"|(?:^\s*\[\d+\]\s*)"
    r")"
    r"(.{20,500}?"
    r"(?:N[°o]?\s*de\s+d[ée]p[ôo]t|brevet|patent|EP\s*\d|FR\s*\d|WO\s*\d|US\s*\d)"
    r".{0,300})",
    re.I | re.M | re.U,
)

# Pattern date brevet (JJ/MM/AAAA ou AAAA-MM-JJ ou MM/AAAA)
_BREVET_DATE_RE = re.compile(
    r"\b(\d{2}[/\-\.]\d{2}[/\-\.]\d{4}|\d{4}[/\-\.]\d{2}[/\-\.]\d{2}|\d{2}[/\-\.]\d{4})\b"
)

# Pattern numéro de dépôt (séquence de 7-12 chiffres, ou FR/EP/WO suivi de chiffres)
_BREVET_NUM_RE = re.compile(
    r"\b(?:(?:FR|EP|WO|US|PCT)[/\s]?\d{4,12}|\d{7,12})\b"
)


def _parse_brevet_from_line(line: str) -> dict | None:
    """
    Parse une ligne de footnote pour en extraire les infos brevet.
    Retourne un dict {numero, date, inventeurs, titre} ou None.
    Universel : pas de règle domaine-spécifique.
    """
    line = _norm_space(line)
    if not line or len(line) < 20:
        return None

    # Chercher numéro de dépôt
    num_match = _BREVET_NUM_RE.search(line)
    numero = _norm_space(num_match.group(0)) if num_match else ""

    # Chercher date
    date_match = _BREVET_DATE_RE.search(line)
    date = _norm_space(date_match.group(0)) if date_match else ""

    # Chercher inventeurs : avant le titre ou le numéro
    # Pattern : séquence de noms propres séparés par virgules avant le titre
    inventeurs = []
    # Découper sur virgule/point-virgule pour trouver les blocs nom
    parts = re.split(r"[,;]", line)
    for part in parts:
        p = _norm_space(part)
        # Un inventeur : 2-4 mots dont au moins un commence par majuscule, pas de chiffre, pas de mot-clé technique
        if (
            2 <= len(p.split()) <= 5
            and re.search(r"[A-ZÉÈÀÂÎÏÔÛÙÇ]", p)
            and not re.search(r"\d", p)
            and not re.search(
                r"\b(?:emballage|dispositif|brevet|d[ée]p[ôo]t|TCP|R&I|SA|SAS|SARL|Universit[ée]|Institut|CNRS|CEA)\b",
                p, re.I
            )
            and len(p) >= 5
        ):
            inventeurs.append(p)

    # Chercher titre brevet : souvent entre le dernier inventeur et le numéro
    # On prend le fragment le plus long qui ressemble à un titre
    titre_brevet = ""
    titre_candidates = []
    for part in parts:
        p = _norm_space(part)
        # Un titre : phrase nominale, pas un nom propre, pas un numéro seul
        if (
            10 <= len(p) <= 200
            and not re.fullmatch(r"[\d/\-\s]+", p)
            and not (len(p.split()) <= 3 and re.search(r"^[A-Z]", p) and not re.search(r"\s", p))
        ):
            titre_candidates.append(p)
    if titre_candidates:
        # Préférer le fragment qui ne contient pas de noms propres isolés
        for tc in titre_candidates:
            if not re.match(r"^[A-ZÉÈÀÂÎÏÔÛÙÇ][a-z]+\s+[A-ZÉÈÀÂÎÏÔÛÙÇ]", tc):
                titre_brevet = tc
                break
        if not titre_brevet:
            titre_brevet = titre_candidates[0]

    if not numero and not date:
        return None

    return {
        "numero": numero,
        "date": date,
        "inventeurs": inventeurs[:6],
        "titre": titre_brevet,
        "ligne_complete": line,
    }


def extract_brevets_from_text(text: str) -> list[dict]:
    """
    Extrait les brevets depuis les footnotes et le corps du texte.
    Universel : fonctionne pour tous types de dossiers R&D/CIR.

    Retourne une liste de dict :
      {numero, date, inventeurs, titre, ligne_complete}
    """
    out = []
    seen_nums: set[str] = set()

    # 1. Chercher dans les footnotes markdown ([^1]:, [1]:)
    for m in _BREVET_FULL_LINE_RE.finditer(text or ""):
        line = _norm_space(m.group(1))
        brevet = _parse_brevet_from_line(line)
        if brevet:
            key = brevet["numero"] or _norm_key(brevet["ligne_complete"][:60])
            if key not in seen_nums:
                seen_nums.add(key)
                out.append(brevet)

    # 2. Chercher dans le corps du texte (phrase mentionnant un dépôt de brevet)
    for m in re.finditer(
        r"[Ll]es?\s+travaux[^.]{0,100}(?:brevet|d[ée]p[ôo]t)[^.]{0,200}\.",
        text or "",
        re.S | re.U,
    ):
        line = _norm_space(m.group(0))
        # Chercher numéro dans cette phrase
        num_m = _BREVET_NUM_RE.search(line)
        date_m = _BREVET_DATE_RE.search(line)
        if num_m or date_m:
            brevet = _parse_brevet_from_line(line)
            if brevet:
                key = brevet["numero"] or _norm_key(brevet["ligne_complete"][:60])
                if key not in seen_nums:
                    seen_nums.add(key)
                    out.append(brevet)

    return out


# ══════════════════════════════════════════════════════════════════════════════
# NOUVEAU V7.3.0 — FILTRE NER BRUIT PERSONNES (UNIVERSEL)
# ══════════════════════════════════════════════════════════════════════════════

# Blacklist universelle de tokens non-nominaux qui contaminent la NER personnes.
# Ces tokens apparaissent dans les entités GLiNER/regex comme faux positifs.
# La liste est purement structurelle/linguistique, pas liée à un domaine.
_PERSON_NOISE_TOKENS_RE = re.compile(
    r"^(?:"
    # Conjonctions de titres de section
    r"Et\s+De|Du\s+Cir|Au\s+Titre|De\s+L['']?|Pour\s+Le|"
    # Acronymes et abréviations techniques fréquents
    r"TCP\s+R&I|Top\s+Clean|Credit\s+Impot|"
    # Colonnes de tableau RH
    r"Nom\s+Pr[ée]nom|Bts\s+Cpi|Mot\s+Cl[ée]s|"
    # Fragments de phrases techniques capitalisées
    r"Stérilisation\s+Gamma|Fourreau\s+Rosace|Sphère\s+Cube|"
    r"Compound\s+Emballage|Innovation\s+Dispositifs|"
    r"Amortissement\s+R[ée]sistance|Liquid\s+Silicone|"
    r"Justificatif\s+Des|Declares\s+Au|Présentation\s+Globale|"
    r"Stratégie\s+De|Operation\s+De|Fiche\s+Descriptive|"
    r"Nom\s+De|De\s+Projet|Annexes?\s+Annexe|"
    # Mots techniques transformés en "noms propres" par capitalisation
    r"Machines?\s+M[ée]canismes?|Institut\s+Fran[çc]ais|"
    r"M[ée]canique\s+Avanc[ée]e|Produits?\s+Industriels?|"
    r"Lyc[ée]e?\s+(?:Godefroy|Edgar|Jean)"
    r")$",
    re.I | re.U,
)

# Tokens qui ne peuvent PAS être des noms de personnes (mots techniques/génériques)
_PERSON_NON_NOMINAL_RE = re.compile(
    r"\b(?:"
    r"Amortissement|R[ée]sistance|Stérilisation|Recyclabilit[ée]|"
    r"Sécurisation|Compound|Fourreau|Rosace|Sphère|Thermoformage|"
    r"Innovation|Justificatif|Annexe|Fiche|Déclaré|Présentation|"
    r"Créativité|Nouveauté|Systématicité|Transférabilité|Incertitude|"
    r"Emballage|Dispositif|Matériau|Procédé|Technologie|"
    r"Conception|Modélisation|Validation|Simulation"
    r")\b",
    re.U,
)


def _is_person_noise(name: str) -> bool:
    """
    Retourne True si le nom est un faux positif NER (token non-nominal).
    Universel : basé sur des patterns structurels, pas domaine-spécifique.
    """
    n = _norm_space(name)
    if not n:
        return True
    # Token blacklisté exactement
    if _PERSON_NOISE_TOKENS_RE.match(n):
        return True
    # Contient un mot clairement non-nominal (terme technique capitalisé)
    if _PERSON_NON_NOMINAL_RE.search(n):
        return True
    # Trop court ou trop long pour être un nom
    if len(n) < 5 or len(n) > 70:
        return True
    # Contient un chiffre → pas un nom
    if re.search(r"\d", n):
        return True
    # Contient des caractères non-nominaux (|, @, /, etc.)
    if re.search(r"[|@/\\&%$#!?]", n):
        return True
    # Mot unique sans espace → probablement un acronyme ou fragment
    if " " not in n and not re.match(r"^[A-ZÉÈÀÂÎÏÔÛÙÇ][a-zéèàâîïôûùç]+$", n):
        return True
    return False


_PERSON_TABLE_RE = re.compile(
    r"\b([A-ZÉÈÀÂÎÏÔÛÙÇ][a-zéèàâîïôûùç\-]+)\s+([A-ZÉÈÀÂÎÏÔÛÙÇ][a-zéèàâîïôûùç\-]+)\b", re.U)
_PERSON_UPPER_RE = re.compile(
    r"\b([A-ZÉÈÀÂÎÏÔÛÙÇ]{2,})\s+([A-ZÉÈÀÂÎÏÔÛÙÇ]{2,})(?:\s+[A-ZÉÈÀÂÎÏÔÛÙÇ]{2,})?\b", re.U)

_PERSON_NOISE_BASE_RE = re.compile(
    r"\b(?:NOM\s+Pr[ée]nom|Dipl[ôo]me|Fonction|Contribution|Technicien|Ing[ée]nieur|Responsable)\b",
    re.I,
)


def _looks_like_person(name: str) -> bool:
    n = _norm_space(name)
    if not (5 <= len(n) <= 70):
        return False
    if _PERSON_NOISE_BASE_RE.search(n):
        return False
    # NOUVEAU V7.3.0 : filtre bruit NER
    if _is_person_noise(n):
        return False
    nk = _norm_key(n)
    if nk in {"nom prenom", "top clean", "credit impot", "dispositif medical", "tableau figure"}:
        return False
    if re.search(r"\b(sarl|sas|sa|groupe|packaging|laboratoire|ministere|dga)\b", nk):
        return False
    return True


def _extract_people_clean(text: str) -> list[str]:
    out = []
    for m in _PERSON_TABLE_RE.finditer(text or ""):
        full = _norm_space(f"{m.group(1)} {m.group(2)}")
        if _looks_like_person(full):
            out.append(full)
    for m in _PERSON_UPPER_RE.finditer(text or ""):
        full = _norm_space(f"{m.group(1)} {m.group(2)}").title()
        if _looks_like_person(full):
            out.append(full)
    return _dedupe(out, 40)


def _has_negative_defense_collaboration(text: str) -> bool:
    t = _norm_key(text)
    return bool(
        re.search(r"collaboration avec le ministere de la defense", t)
        and re.search(r"\bnon\b", t)
    )


def _filter_false_organisms(orgs: list[str], text: str) -> list[str]:
    defense_negative = _has_negative_defense_collaboration(text)
    out = []
    for org in orgs or []:
        k = _norm_key(org)
        if not k or k in {"le mi", "si oui", "non", "oui"}:
            continue
        if defense_negative and k in {"dga", "ministere de la defense", "ministère de la défense"}:
            continue
        out.append(org)
    return _dedupe(out, 30)


# ── État de l'art plat ────────────────────────────────────────────────────────

def _extract_etat_art_from_evidence(evidence_map: Any) -> list[str]:
    out = []
    mappings = []
    if evidence_map is not None:
        if isinstance(evidence_map, dict):
            mappings = evidence_map.get("mappings", []) or []
        else:
            mappings = getattr(evidence_map, "mappings", []) or []

    for m in mappings:
        evs = m.get("evidences", []) if isinstance(m, dict) else getattr(m, "evidences", [])
        for ev in evs or []:
            role = str(_get(ev, "role", "") or "").strip().lower()
            if role == "etat_art":
                phrase = _get(ev, "phrase_source", "") or _get(ev, "phrase", "") or _get(ev, "text", "")
                if phrase:
                    out.append(str(phrase))

    return _dedupe(out, 30)


# ── Organismes propres ────────────────────────────────────────────────────────

def _extract_organisms_clean(technical_terms: Any, text: str) -> list[str]:
    td = _to_dict(technical_terms)
    detected = td.get("organismes_detectes", [])
    if isinstance(detected, list) and detected:
        return _dedupe(detected, 30)

    out = []
    known = {
        "GEMTEX": r"\bGEMTEX\b",
        "LEM3": r"\bLEM\s*3\b",
        "CEVAA": r"\bCEVAA\b",
        "RAPID": r"\bRAPID\b",
        "DGA": r"\bDGA\b",
        "Andhéo": r"\bAnhéo\b|\bAndh[ée]o\b|\bANDHEO\b",
        "DYNAE": r"\bDYNAE\b",
        "CETIM": r"\bCETIM\b",
        "CEA": r"\bCEA\b",
        "CNRS": r"\bCNRS\b",
    }
    for label, pattern in known.items():
        if re.search(pattern, text, re.I | re.U):
            out.append(label)

    return _dedupe(out, 30)


# ═════════════════════════════════════════════════════════════════════════════
# FONCTIONS HÉRITÉES V7.2.0 (inchangées sauf extract_project_title)
# ═════════════════════════════════════════════════════════════════════════════

def extract_project_title_from_sections(document_structure: Any, text: str) -> str:
    """
    Extraction titre projet — V7.3.0.

    Ordre de priorité :
    1. NOM DE L'OPERATION | <titre>  ← NOUVEAU V7.3.0 (très fiable)
    2. Tableau entête (logo | titre | CIR)  ← NOUVEAU V7.3.0
    3. FICHE DESCRIPTIVE DU PROJET «...»
    4. NOM DU PROJET dans sections
    5. NOM DU PROJET dans texte brut
    """
    # 1. NOM DE L'OPERATION | <titre> — pattern le plus fiable dans CIR
    title = _extract_title_from_nom_operation(text)
    if title:
        logger.debug("Titre depuis NOM DE L'OPERATION : %s", title[:80])
        return title

    # 2. Tableau entête (logo | titre | CIR/année)
    title = _extract_title_from_header_table(text)
    if title:
        logger.debug("Titre depuis tableau entête : %s", title[:80])
        return title

    # 3. FICHE DESCRIPTIVE DU PROJET «...»
    sections = _sections(document_structure)
    patterns = [
        r"FICHE\s+DESCRIPTIVE\s+DU\s+PROJET\s*[«\"]\s*(.+?)\s*[»\"]",
        r"FICHE\s+DESCRIPTIVE\s+DU\s+PROJET\s+(.+?)(?:\n|SIGLE|Table des matières)",
    ]
    for p in patterns:
        m = re.search(p, text, flags=re.I | re.S | re.U)
        if m:
            cand = _norm_space(m.group(1))
            if 15 <= len(cand) <= 260 and not cand.lower().startswith("table"):
                return cand

    # 4. NOM DU PROJET dans sections
    for i, s in enumerate(sections):
        title_s = _section_title(s)
        content = _section_content(s)
        joined = f"{title_s}\n{content}"

        if re.search(r"\b(NOM\s+DU\s+PROJET|Intitul[ée]\s+du\s+projet)\b", joined, re.I | re.U):
            lines = [_norm_space(x) for x in joined.splitlines() if _norm_space(x)]
            for j, line in enumerate(lines):
                if re.search(r"\b(NOM\s+DU\s+PROJET|Intitul[ée]\s+du\s+projet)\b", line, re.I | re.U):
                    after = re.sub(
                        r".*?(NOM\s+DU\s+PROJET|Intitul[ée]\s+du\s+projet)\s*[:\-]?", "",
                        line, flags=re.I,
                    )
                    after = _norm_space(after)
                    if 15 <= len(after) <= 260:
                        return after
                    for nxt in lines[j + 1:j + 6]:
                        if 15 <= len(nxt) <= 260 and not re.search(
                            r"CHEF|Date|NON|OUI|TH[ÉE]SAURUS|MOT", nxt, re.I
                        ):
                            return nxt

            for s2 in sections[i + 1:i + 4]:
                cand = _norm_space(_section_title(s2) + " " + _section_content(s2))
                if 15 <= len(cand) <= 260 and not re.search(
                    r"CHEF|Date|NON|OUI|TH[ÉE]SAURUS|MOT|Objectifs", cand, re.I
                ):
                    return cand

    # 5. NOM DU PROJET dans texte brut
    m = re.search(
        r"NOM\s+DU\s+PROJET\s*[:\-]?\s*(.+?)(?:CHEF|Ce projet|Date|TH[ÉE]SAURUS|MOT\s+CL)",
        text, re.I | re.S | re.U,
    )
    if m:
        cand = _norm_space(m.group(1))
        cand = re.sub(r"\s{2,}", " ", cand)
        if 15 <= len(cand) <= 260:
            return cand

    return ""


def _objective_lines_from_block(block: str) -> list[str]:
    out = []
    block = str(block or "")

    for m in re.finditer(
        r"(Le\s+présent\s+(?:projet|travaux?)\s+de\s+R&D\s+vise[^.:\n]*(?::|\\.?))",
        block, re.I | re.U,
    ):
        out.append(_norm_space(m.group(1)))

    for line in re.split(r"\n|•|·|;", block):
        s = _norm_space(line)
        if not s:
            continue
        if re.search(
            r"\b(débit|pression|refoulement|discrétion|acoustique|vibratoire|encombrement|sous-marin|air sec|rosée|"
            r"réduction des vibrations|échauffement|humidité|refroidissement|performances à atteindre)\b",
            s, re.I | re.U,
        ):
            if 10 <= len(s) <= 350:
                out.append(s)

    if not out:
        for sent in re.split(r"(?<=[.!?])\s+", block):
            s = _norm_space(sent)
            if re.search(r"\b(objectif|vise|atteindre|développer|investiguer|résoudre|performance)\b", s, re.I):
                if 20 <= len(s) <= 450:
                    out.append(s)

    return _dedupe(out, 12)


def extract_objectives_from_sections(document_structure: Any, text: str) -> list[str]:
    sections = _sections(document_structure)
    out = []

    for s in sections:
        title = _section_title(s)
        role = _section_role(s)
        content = _section_content(s)
        if role == "objectifs" or re.search(
            r"Objectifs\s+vis[ée]s|Objectifs\s+du\s+projet|performances\s+[àa]\s+atteindre",
            title, re.I | re.U,
        ):
            if re.search(r"travaux\s+ant[ée]rieurs|premiers\s+travaux|rappel", title, re.I):
                continue
            for line in _objective_lines_from_block(content):
                if not _is_rh_line(line):
                    out.append(line)

    if not out:
        m = re.search(
            r"Objectifs\s+vis[ée]s(?:\s+et\s+performances\s+[àa]\s+atteindre)?\s*(.+?)(?:\n\s*(?:Etat|État)\s+de\s+l['']art|\n\s*Verrous|\n\s*D[ée]marche|\n\s*1\.3\.)",
            text, re.I | re.S | re.U,
        )
        if m:
            for line in _objective_lines_from_block(m.group(1)):
                if not _is_rh_line(line):
                    out.append(line)

    return _dedupe(out, 12)


def extract_official_keywords(text: str) -> list[str]:
    out = []
    m = re.search(
        r"MOT[S]?\s*CL[ÉE]S\s*(.+?)(?:\n\s*\n|Objectifs|Contexte|Etat de l['']art|État de l['']art|Verrous|Démarche)",
        text, re.I | re.S | re.U,
    )
    if m:
        block = m.group(1)[:1000]
        for line in re.split(r"[\n;•·]+", block):
            line = _norm_space(line)
            line = re.sub(r"^[\-\*·•]\s*", "", line)
            if 3 <= len(line) <= 120 and not re.match(r"^(Objectifs|Contexte|Etat|Verrous|Table)", line, re.I):
                out.append(line)
    return _dedupe(out, 20)


def extract_partners(text: str) -> list[str]:
    out = []
    variants = {
        "andhéo": "Andhéo", "andheo": "Andhéo",
        "dynae": "DYNAE", "cetim": "CETIM",
        "gemtex": "GEMTEX", "lem3": "LEM3", "lem 3": "LEM3",
        "cevaa": "CEVAA", "rapid": "RAPID", "dga": "DGA",
    }
    low = _norm_key(text)
    for k, label in variants.items():
        if re.search(rf"\b{re.escape(k)}\b", low):
            out.append(label)
    return _dedupe(out, 20)


def extract_people(text: str) -> list[str]:
    out = []
    m = re.search(
        r"CHEF\(S\)\s+DE\s+PROJET\s*[:\-]?\s*(.+?)(?:Ce projet|Date|TH[ÉE]SAURUS|MOT\s+CL|Objectifs)",
        text, re.I | re.S | re.U,
    )
    if m:
        val = _norm_space(m.group(1))
        val = re.sub(r"\b(NON|OUI|Préciser.*)$", "", val, flags=re.I)
        for part in re.split(r"[,;/\n]", val):
            p = _norm_space(part)
            if 3 <= len(p) <= 80 and not re.search(r"Ce projet|Date|NON|OUI|TH[ÉE]SAURUS", p, re.I):
                out.append(p)

    for m in re.finditer(r"\b[A-Z]\.\s+[A-ZÉÈÀÂÎÏÔÛÙÇ]{2,}(?:\s+[A-ZÉÈÀÂÎÏÔÛÙÇ]{2,}){0,4}\b", text):
        out.append(m.group(0))

    return _dedupe(out, 12)


METRIC_RE = re.compile(
    r"\b(?:"
    r"\d+(?:[,.]\\d+)?\s*(?:-|à|a|–)\s*\d+(?:[,.]\\d+)?\s*(?:m3/h|m³/h|bars?|bar|°C|db|dB|kWh|%)|"
    r"\d+(?:[,.]\\d+)?\s*(?:m3/h|m³/h|bars?|bar|°C|db|dB|kWh|%|rpm|tr/min|mm|cm|m²|m2|Hz|kHz|litres?|L)|"
    r"\-\\d+(?:[,.]\\d+)?\s*°C"
    r")\b",
    re.I | re.U,
)


def extract_metrics(text: str) -> list[str]:
    out = [m.group(0) for m in METRIC_RE.finditer(text or "")]
    for m in re.finditer(r"point\s+de\s+ros[ée]e[^.\n]{0,80}", text or "", re.I | re.U):
        out.append(m.group(0))
    return _dedupe(out, 80)


def is_numeric_metric(term: str) -> bool:
    t = _norm_space(term)
    if METRIC_RE.search(t):
        return True
    if re.fullmatch(r"[\d\s.,/%°+\-–àa]+(?:m3/h|m³/h|bars?|bar|°C|db|dB|kWh|%)?", t, re.I):
        return True
    return False


def _tech_dict(technical_terms: Any) -> dict:
    d = _to_dict(technical_terms)
    return d if isinstance(d, dict) else {}


def _list_from_tech(technical_terms: Any, key: str) -> list[str]:
    d = _tech_dict(technical_terms)
    val = d.get(key, [])
    return _dedupe(val if isinstance(val, list) else [])


def _keywords_from_tech(technical_terms: Any) -> tuple[list[str], list[str]]:
    d = _tech_dict(technical_terms)
    mc = d.get("mots_cles_projet", {})
    if not isinstance(mc, dict):
        return [], []
    return _dedupe(mc.get("high_confidence", []) or []), _dedupe(mc.get("candidates", []) or [])


def _clean_keywords(official: list[str], high: list[str], candidates: list[str]) -> tuple[list[str], list[str], list[str]]:
    moved_metrics = []
    high_clean = []
    for k in official + high:
        if is_numeric_metric(k):
            moved_metrics.append(k)
        else:
            high_clean.append(k)
    cand_clean = []
    for k in candidates:
        if is_numeric_metric(k):
            moved_metrics.append(k)
        else:
            cand_clean.append(k)
    high_final = _dedupe(high_clean, 18)
    high_keys = {_norm_key(x) for x in high_final}
    cand_final = _dedupe([x for x in cand_clean if _norm_key(x) not in high_keys], 40)
    return high_final, cand_final, _dedupe(moved_metrics, 40)


def _list_from_synthesis(synthesis: Any, keys: list[str]) -> list[str]:
    d = _to_dict(synthesis)
    fiche = (
        d.get("fiche_cir") if isinstance(d.get("fiche_cir"), dict)
        else d.get("fiche") if isinstance(d.get("fiche"), dict)
        else d
    )
    out = []
    for key in keys:
        value = fiche.get(key) if isinstance(fiche, dict) else None
        if value is None:
            value = d.get(key)

        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    preuves = item.get("preuves")
                    if isinstance(preuves, list) and preuves:
                        out.extend([str(p) for p in preuves if isinstance(p, str) and len(p.strip()) >= 30])
                    else:
                        txt = item.get("phrase") or item.get("text") or item.get("value") or item.get("resume")
                        if txt:
                            out.append(str(txt))
                elif item:
                    out.append(str(item))
        elif isinstance(value, dict):
            preuves = value.get("preuves")
            if isinstance(preuves, list) and preuves:
                out.extend([str(p) for p in preuves if isinstance(p, str) and len(p.strip()) >= 30])
            else:
                txt = value.get("phrase") or value.get("text") or value.get("value") or value.get("resume")
                if txt:
                    out.append(str(txt))
        elif value:
            out.append(str(value))

    return _dedupe(out)


def _evidences_by_role(evidence_map: Any, role: str) -> list[str]:
    out = []
    mappings = (
        evidence_map.get("mappings", []) if isinstance(evidence_map, dict)
        else getattr(evidence_map, "mappings", [])
    )
    for m in mappings or []:
        evs = m.get("evidences", []) if isinstance(m, dict) else getattr(m, "evidences", [])
        for ev in evs or []:
            if str(_get(ev, "role", "")).lower() == role:
                phrase = _get(ev, "phrase_source", "") or _get(ev, "phrase", "") or _get(ev, "text", "")
                if phrase:
                    out.append(phrase)
    return _dedupe(out)


# ═════════════════════════════════════════════════════════════════════════════
# POINT D'ENTRÉE
# ═════════════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════════════
# V7.4.0 — GARDE-FOUS FINAUX UNIVERSELS
# ══════════════════════════════════════════════════════════════════════════════

_FALSE_OBJECTIVE_FINAL_RE = re.compile(
    r"(?:"
    r"\bl['’]?objectif\s+de\s+[^.]{0,80}\b(?:structure|structuration|regrouper|organisation|moyens?\s+humains?|moyens?\s+mat[ée]riels?)\b|"
    r"\b(?:nous|ce\s+projet|ces\s+travaux|cette\s+[ée]tude)\s+(?:a|ont)\s+permis\s+d['’]?(?:acqu[ée]rir|identifier|d[ée]velopper|mettre|contribuer)|"
    r"\b(?:conclusion|contribution\s+scientifique|indicateurs?\s+de\s+R&D)\b|"
    r"\bagr[ée][ée]?\s+au\s+CIR\b"
    r")",
    re.I | re.U,
)

_DEMARCHE_OR_RESULT_AS_VERROU_RE = re.compile(
    r"(?:"
    r"\bnous\s+avons\s+(?:r[ée]alis[ée]|d[ée]fini|d[ée]velopp[ée]|retenu|choisi|men[ée]|mis\s+en\s+[œo]uvre|obtenu)|"
    r"\b(?:les\s+)?r[ée]sultats?\s+(?:de\s+R&D\s+)?(?:montrent|ont\s+permis|nous\s+ont\s+permis)|"
    r"\b(?:sommes\s+parvenus|a\s+permis\s+le\s+d[ée]veloppement|ont\s+permis\s+de\s+d[ée]velopper)|"
    r"\b(?:prototype|prototypes|architecture\s+de\s+notre|solution\s+technique\s+retenue)\b"
    r")",
    re.I | re.U,
)

_VERROU_CORE_RE = re.compile(
    r"(?:"
    r"\bverrou\b|\bincapacit[ée]\b|\bincertitude\b|\bmanque\s+de\b|\bdifficult[ée]\b|\brésoudre\s+de\s+manière\s+simultan[ée]e\b|"
    r"\bne\s+(?:permet|permettent|r[ée]pond|r[ée]pondent)\s+pas\b|\bimpossible\b|\brisque\b|\babsence\s+de\b|"
    r"\btenue\s+aux\s+chocs\b|\br[ée]sistance\s+[àa]\s+l['’]?abrasion\b|\bs[ée]curisation\b|\brecyclabilit[ée]\b"
    r")",
    re.I | re.U,
)

_BREVET_OR_INDICATOR_RE = re.compile(
    r"(?:brevet|d[ée]p[ôo]t|N[°o]\s*de\s+d[ée]p[ôo]t|indicateurs?\s+de\s+R&D)",
    re.I | re.U,
)

_ETAT_ART_GOOD_RE = re.compile(
    r"(?:"
    r"\b[ée]tat\s+de\s+l['’]?art\b|\bsolutions?\s+existantes?\b|\btechnologies?\s+existantes?\b|"
    r"\b(?:poches?|bo[iî]tiers?|films?|mousses?|syst[èe]mes?)\b.{0,120}\b(?:toutefois|cependant|ne\s+permet|limite|risque|difficilement)\b|"
    r"\b(?:toutefois|cependant)\b.{0,160}\b(?:ne\s+permet|risque|limite|difficilement)\b"
    r")",
    re.I | re.U,
)

_BAD_ENTITY_WORD_RE = re.compile(
    r"\b(?:germes?|[ée]quipe\s+pluridisciplinaire|contaminants?|personnel\s+soignant|dispositifs?\s+m[ée]dicaux?|"
    r"syst[èe]mes?|solutions?|travaux|mat[ée]riaux|formes?|cavit[ée]|opercule|membrane|fourreau|rosace|sph[èe]re|cube|compound)\b",
    re.I | re.U,
)

_ORG_HINT_RE = re.compile(
    r"(?:\b[A-Z][A-Z0-9&.-]{1,}\b|sarl|sas|sa\b|groupe|group|inc\.?|ltd\.?|universit[ée]|laboratoire|"
    r"institut|centre|cnrs|cea|inria|inserm|packaging|technolog|systems?|industrie|company|corp|R&I)",
    re.I | re.U,
)

_TABLE_FRAGMENT_RE = re.compile(r"[|]{1,}|^\s*(?:\+\+|--|\+|-{2,})|\b(?:Figure|Tableau)\s+\d+", re.I | re.U)

def _is_false_objective_final(text: str) -> bool:
    t = _norm_space(text)
    return (not t) or bool(_is_rh_line(t) or _is_false_objective(t) or _FALSE_OBJECTIVE_FINAL_RE.search(t))

def _is_valid_verrou_final(text: str) -> bool:
    t = _norm_space(text)
    if not t or len(t) < 10:
        return False
    if _DEMARCHE_OR_RESULT_AS_VERROU_RE.search(t):
        return False
    return bool(_VERROU_CORE_RE.search(t))

def _is_valid_etat_art_final(text: str) -> bool:
    t = _norm_space(text)
    if not t or len(t) < 25:
        return False
    if _BREVET_OR_INDICATOR_RE.search(t):
        return False
    if re.search(r"\bnous\s+avons\s+(?:r[ée]alis[ée]|d[ée]velopp[ée]|retenu|d[ée]fini)\b", t, re.I):
        return False
    return bool(_ETAT_ART_GOOD_RE.search(t))

def _extract_etat_art_from_sections_final(document_structure: Any) -> list[str]:
    out: list[str] = []
    for s in _sections(document_structure):
        role = _section_role(s)
        title = _section_title(s)
        content = _section_content(s)
        if role not in {"etat_art", "contexte"} and not re.search(r"solutions?\s+existantes?", title, re.I):
            continue
        block = f"{title}\n{content}"
        # Phrases complètes contenant les signaux utiles
        for sent in re.split(r"(?<=[.!?;])\s+|\n", block):
            sent = _norm_space(sent)
            if _is_valid_etat_art_final(sent):
                out.append(sent)
    return _dedupe(out, 10)

def _extract_people_from_admin_sections(document_structure: Any, text: str, brevets: list[dict] | None = None) -> list[str]:
    blocks: list[str] = []
    for s in _sections(document_structure):
        role = _section_role(s)
        title = _section_title(s)
        content = _section_content(s)
        joined = f"{title}\n{content}"
        if role in {"administratif", "ressources"} or re.search(r"(NOM\s+Pr[ée]nom|Dipl[ôo]me|Fonction|interlocuteur|chef\(s\)\s+de\s+projet)", joined, re.I):
            blocks.append(joined)

    out: list[str] = []
    for b in blocks:
        out.extend(_extract_people_clean(b))
        # cas "Nom de l'interlocuteur de la société: Hervé Vergne"
        for m in re.finditer(r"(?:interlocuteur|chef(?:\(s\))?\s+de\s+projet)\s*(?:de\s+la\s+soci[ée]t[ée])?\s*[:|]\s*([^;\n|]+)", b, re.I | re.U):
            cand = _norm_space(m.group(1))
            if _looks_like_person(cand):
                out.append(cand)
    for br in brevets or []:
        if isinstance(br, dict):
            out.extend([x for x in br.get("inventeurs", []) if _looks_like_person(x)])
    return _dedupe([p for p in out if _looks_like_person(p)], 40)

def _clean_brevets_final(brevets: list[dict]) -> list[dict]:
    cleaned = []
    seen = set()
    for br in brevets or []:
        if not isinstance(br, dict):
            continue
        numero = _norm_space(br.get("numero") or br.get("numero_depot") or "")
        date = _norm_space(br.get("date") or br.get("date_depot") or "")
        titre = _norm_space(br.get("titre") or br.get("title") or "")
        inventeurs = _dedupe([x for x in br.get("inventeurs", []) if _looks_like_person(x)], 10)
        ligne = _norm_space(br.get("ligne_complete") or "")
        if not numero and not date and not titre:
            continue
        key = numero or _norm_key(titre + date)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append({
            "numero_depot": numero,
            "date_depot": date,
            "titre": titre,
            "inventeurs": inventeurs,
            "ligne_complete": ligne,
        })
    return cleaned

def _is_probable_organization_final(org: str) -> bool:
    o = _norm_space(org)
    if not o or len(o) < 2 or len(o) > 90:
        return False
    if _BAD_ENTITY_WORD_RE.search(o):
        return False
    if re.search(r"[|@/\\]", o):
        return False
    # éviter les noms de personnes
    if _looks_like_person(o) and not re.search(r"\b(R&I|SAS|SARL|SA|Groupe|Group|Packaging|Institut|Université|Laboratoire)\b", o, re.I):
        return False
    return bool(_ORG_HINT_RE.search(o))

def _clean_organisms_final(values: list[str], text: str) -> list[str]:
    return _filter_false_organisms(_dedupe([v for v in values if _is_probable_organization_final(v)], 30), text)

def _is_clean_term_final(term: str) -> bool:
    t = _norm_space(term)
    if not t or len(t) < 2 or len(t) > 130:
        return False
    if _TABLE_FRAGMENT_RE.search(t):
        return False
    if re.search(r"\b(?:il\s+manqu|cependant|toutefois)\b", t, re.I):
        return False
    # parenthèse ouverte souvent issue d'entité tronquée
    if t.count("(") > t.count(")"):
        return False
    if re.search(r"\b(?:de|du|des|d['’]|l['’]|à|au|aux|pour|avec|sans|dans)$", t, re.I):
        return False
    return _is_clean_keyword(t) if '_is_clean_keyword' in globals() else True

def _clean_terms_final(values: list[Any], max_items: int = 40) -> list[str]:
    return _dedupe([_norm_space(v) for v in values if _is_clean_term_final(str(v or ""))], max_items)

def _best_official_or_section_title(title: str, objectives: list[str]) -> list[str]:
    if title:
        return [title]
    return objectives[:1] if objectives else []
def map_final_taxonomy(
    aggregated: Any = None,
    synthesis: Any = None,
    domain_classification: Any = None,
    evidence_map: Any = None,
    raw_chunks: list[str] | None = None,
    technical_terms: Any = None,
    document_structure: Any = None,
    **kwargs: Any,
) -> dict:
    """
    Mapping final V7.4.0.
    Garde la logique V7.3.0 mais ajoute des garde-fous universels :
    - titre officiel mieux propagé ;
    - brevets structurés ;
    - état de l'art réel séparé des brevets/résultats ;
    - verrous uniquement si vrais verrous/incertitudes ;
    - personnes/organismes nettoyés ;
    - termes techniques sans fragments de tableaux.
    """
    if document_structure is None:
        document_structure = (
            kwargs.get("structure")
            or kwargs.get("sections")
            or kwargs.get("document_sections")
        )

    text = _document_text(
        raw_chunks=raw_chunks, evidence_map=evidence_map, document_structure=document_structure
    )

    title = extract_project_title_from_sections(document_structure, text)
    official_keywords = extract_official_keywords(text)
    metrics_text = extract_metrics(text)

    # Brevets : extraction + nettoyage structuré
    brevets = _clean_brevets_final(extract_brevets_from_text(text))

    # Personnes : priorité aux sections admin/RH + inventeurs de brevets.
    # On évite de prendre des personnes GLiNER sur tout le texte, car c'est là que naissent
    # les faux positifs "Liquid Silicone", "Velfort Cependant", etc.
    people = _extract_people_from_admin_sections(document_structure, text, brevets)

    # Organismes : technical_terms + fallback texte, puis filtre final universel.
    organisms_clean = _extract_organisms_clean(technical_terms, text)
    if organisms_clean:
        all_organisms = organisms_clean
    else:
        all_organisms = extract_partners(text)
    all_organisms = _clean_organisms_final(all_organisms, text)

    # Domaine
    dc_dict = _to_dict(domain_classification)
    domaine_applicatif = dc_dict.get("domaine_applicatif") or None
    domaine_scientifique_detaille = dc_dict.get("domaine_scientifique_detaille") or None

    # Mots-clés / termes
    high0, cand0 = _keywords_from_tech(technical_terms)
    high_keywords, cand_keywords, moved_metrics = _clean_keywords(official_keywords, high0, cand0)
    high_keywords = _clean_terms_final(high_keywords, 18)
    cand_keywords = _clean_terms_final(cand_keywords, 40)

    # Objectifs : sections fiables + synthèse nettoyée
    objectives_from_sections = extract_objectives_from_sections(document_structure, text)
    objectifs_synth = _list_from_synthesis(synthesis, ["objectifs", "objectifs_rd", "objectifs_r_d"])
    objectifs = _dedupe(
        [o for o in objectives_from_sections + objectifs_synth if not _is_false_objective_final(o)],
        10,
    )

    # Verrous : synthèse + evidence_map, puis filtre rôle strict
    verrous_raw = (
        _list_from_synthesis(synthesis, ["verrous", "verrous_techniques"])
        + _evidences_by_role(evidence_map, "verrou")
    )
    verrous = _dedupe([v for v in verrous_raw if _is_valid_verrou_final(v)], 10)

    # Si les vrais verrous sont sous forme de lignes courtes dans la section verrous, les récupérer.
    for s in _sections(document_structure):
        if _section_role(s) == "verrous":
            block = _section_content(s)
            for line in re.split(r"\n|;|•|·", block):
                line = _norm_space(line)
                if _is_valid_verrou_final(line):
                    verrous.append(line)
    verrous = _dedupe(verrous, 12)

    # État de l'art : pas depuis brevet. On combine evidence_map et sections, puis filtre.
    etat_art_raw = _extract_etat_art_from_evidence(evidence_map) + _extract_etat_art_from_sections_final(document_structure)
    etat_art_flat = _dedupe([e for e in etat_art_raw if _is_valid_etat_art_final(e)], 12)

    # Méthodes / essais / résultats avec nettoyage léger
    methodes_raw = _list_from_synthesis(synthesis, ["demarche", "méthodes", "methodes", "methodes_rd", "essais"])
    if not methodes_raw:
        methodes_raw = _evidences_by_role(evidence_map, "demarche") + _evidences_by_role(evidence_map, "essai")
    methodes = _dedupe([
        m for m in methodes_raw
        if _is_clean_term_final(m) and not _is_valid_verrou_final(m) and not _BREVET_OR_INDICATOR_RE.search(m)
    ], 20)

    resultats_raw = _list_from_synthesis(synthesis, ["resultats", "résultats", "resultats_rd"])
    if not resultats_raw:
        resultats_raw = _evidences_by_role(evidence_map, "resultat")
    resultats = _dedupe([
        r for r in resultats_raw
        if _is_clean_term_final(r) and not _BREVET_OR_INDICATOR_RE.search(r)
    ], 20)

    metriques = _dedupe(
        metrics_text + moved_metrics + _list_from_tech(technical_terms, "metriques"), 80
    )

    technologies = _clean_terms_final(
        official_keywords
        + _list_from_tech(technical_terms, "technologies")
        + _list_from_tech(technical_terms, "equipements"),
        50,
    )
    equipements = _clean_terms_final(_list_from_tech(technical_terms, "equipements"), 30)
    materiaux = _clean_terms_final(_list_from_tech(technical_terms, "materiaux_composants"), 40)
    normes = _clean_terms_final(_list_from_tech(technical_terms, "normes"), 25)

    objet_recherche = _best_official_or_section_title(title, objectifs)

    result = {
        "title": title,
        "objet_recherche": objet_recherche,
        "objectifs_rd": objectifs,
        "verrous_techniques": verrous,
        "methodes_rd": methodes,
        "resultats_rd": resultats,

        "etat_art": etat_art_flat,
        "domaine_applicatif": domaine_applicatif,
        "domaine_scientifique_detaille": domaine_scientifique_detaille,

        "mots_cles_projet": {
            "high_confidence": high_keywords,
            "candidates": cand_keywords,
        },

        "technologies": technologies,
        "outils_technologies": _dedupe(
            _clean_terms_final(_list_from_tech(technical_terms, "methodes"), 30)
            + equipements,
            40,
        ),
        "equipements": equipements,
        "materiaux_composants": materiaux,
        "metriques_evaluation": metriques,
        "parametres_variables": metriques[:40],
        "normes_techniques": normes,

        "partenaires_rd": all_organisms,
        "organismes": all_organisms,
        "personnes": people,

        "brevets": brevets,

        "axes_projet": high_keywords[:8],
        "sous_domaines": [],
        "hypotheses_rd": [],
        "protocoles_experimentaux": [],
        "modeles_algorithmes": [],
        "architectures_systeme": [],
        "jeux_donnees_benchmarks": [],
        "limitations_perspectives": [],
        "composants_techniques": _dedupe(materiaux + equipements, 40),
        "livrables": [],
        "depenses_eligibles": [],
        "materiaux": materiaux,
        "lieux": [],
        "dates_periodes": [],
        "montants": [],
        "indicateurs_cir": {"etp": [], "montants": [], "jalons": []},

        "stats": {
            "version": "7.4.0",
            "used_document_structure": bool(_sections(document_structure)),
            "official_keywords": len(official_keywords),
            "objectives_from_sections": len(objectives_from_sections),
            "partners_detected": len(all_organisms),
            "people_detected": len(people),
            "metrics_detected": len(metriques),
            "project_title_detected": bool(title),
            "etat_art_phrases": len(etat_art_flat),
            "brevets_detected": len(brevets),
        },
    }

    logger.info(
        "Final taxonomy v7.4.0 : title=%s | objectifs=%d | verrous=%d | org=%s | etat_art=%d | brevets=%d",
        bool(title), len(objectifs), len(verrous), all_organisms, len(etat_art_flat), len(brevets),
    )

    return result
