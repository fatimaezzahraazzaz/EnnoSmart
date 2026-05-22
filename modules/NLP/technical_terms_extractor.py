"""
modules/NLP/technical_terms_extractor.py — V7.4.2

Objectif :
- garder la base evidence-first ;
- corriger le warning tokenizer GLiNER / Transformers :
  "incorrect regex pattern ... fix_mistral_regex=True" ;
- éviter la troncature GLiNER ;
- filtrer plus strictement les faux positifs NER personnes ;
- garder une sortie compatible avec router.py :
  mots_cles_projet, technologies, materiaux_composants, equipements,
  metriques, normes, methodes, organismes_detectes, personnes_detectees,
  partenaires_rd, stats.
"""

from __future__ import annotations

import logging
import re
import unicodedata
import warnings
from collections import Counter
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

GLiNER = None


def _load_gliner_class():
    global GLiNER

    if GLiNER is not None:
        return GLiNER

    try:
        from gliner import GLiNER as _GLiNER
        GLiNER = _GLiNER
        return GLiNER
    except Exception as exc:
        raise RuntimeError(f"Impossible de charger GLiNER : {exc}") from exc

# ══════════════════════════════════════════════════════════════════════════════
# PATCH TOKENIZER — supprime le warning et force fix_mistral_regex=True
# ══════════════════════════════════════════════════════════════════════════════

def _install_tokenizer_regex_fix() -> None:
    """
    Corrige le warning :
    The tokenizer you are loading from 'microsoft/mdeberta-v3-base'
    with an incorrect regex pattern...
    You should set fix_mistral_regex=True.

    Important :
    - GLiNER charge parfois le tokenizer en interne.
    - On monkey-patch AutoTokenizer.from_pretrained AVANT GLiNER.from_pretrained.
    - Si ta version transformers ne supporte pas fix_mistral_regex, on relance sans.
    """
    warnings.filterwarnings(
        "ignore",
        message=r".*resume_download.*deprecated.*",
        category=UserWarning,
    )
    warnings.filterwarnings(
        "ignore",
        message=r".*incorrect regex pattern.*fix_mistral_regex=True.*",
        category=UserWarning,
    )

    try:
        from transformers import AutoTokenizer
    except Exception:
        return

    if getattr(AutoTokenizer, "_ennosmart_fix_mistral_regex_installed", False):
        return

    original_from_pretrained = AutoTokenizer.from_pretrained

    def patched_from_pretrained(pretrained_model_name_or_path, *args, **kwargs):
        name = str(pretrained_model_name_or_path or "").lower()

        if (
            "mdeberta-v3-base" in name
            or "mistral" in name
            or "gliner" in name
            or "deberta" in name
        ):
            kwargs.setdefault("fix_mistral_regex", True)

        try:
            return original_from_pretrained(
                pretrained_model_name_or_path,
                *args,
                **kwargs,
            )
        except TypeError:
            # Compatibilité anciennes versions Transformers.
            kwargs.pop("fix_mistral_regex", None)
            return original_from_pretrained(
                pretrained_model_name_or_path,
                *args,
                **kwargs,
            )

    AutoTokenizer.from_pretrained = patched_from_pretrained
    AutoTokenizer._ennosmart_fix_mistral_regex_installed = True


# Patch non installé au moment de l'import.
# Il sera installé uniquement dans _run_gliner() quand GLiNER est réellement activé.
# _install_tokenizer_regex_fix()


# ══════════════════════════════════════════════════════════════════════════════
# REGEX / PATTERNS
# ══════════════════════════════════════════════════════════════════════════════

METRIC_RE = re.compile(
    r"\b(?:"
    r"-?\d+(?:[,.]\d+)?\s*(?:-|à|a|–)\s*-?\d+(?:[,.]\d+)?\s*"
    r"(?:m3/h|m³/h|bars?|bar|°C|db|dB|kWh|%|rpm|tr/min|mm|cm|m²|m2|Hz|kHz|MHz|GHz|ns|µs|us|ms|s|octets?|bits?|V|Ah|mW|W|A|MPa|GPa|kN|N/m)"
    r"|"
    r"-?\d+(?:[,.]\d+)?\s*"
    r"(?:m3/h|m³/h|bars?|bar|°C|db|dB|kWh|%|rpm|tr/min|mm|cm|m²|m2|Hz|kHz|MHz|GHz|ns|µs|us|ms|s|octets?|bits?|V|Ah|mW|W|A|MPa|GPa|kN|N/m)"
    r")\b",
    re.I | re.U,
)

TECH_PATTERNS = [
    # général
    r"\bimpression\s+3D\b",
    r"\bthermoformage\b",
    r"\binjection\b",
    r"\bsoudure\s+haute\s+fr[ée]quence\b",
    r"\b[ée]l[ée]ments?\s+finis?\b",
    r"\bmod[ée]lisation\s+[ée]l[ée]ments?\s+finis?\b",
    r"\bsimulations?\s+thermiques?\s+dynamiques?\b",
    r"\bmod[ée]lisation\s+(?:num[ée]rique|thermique|a[ée]raulique|m[ée]canique)\b",

    # vibro / matériaux
    r"\bcomposites?\s+aux[ée]tiques?\b",
    r"\bstructures?\s+aux[ée]tiques?\b",
    r"\bmat[ée]riaux\s+aux[ée]tiques?\b",
    r"\bd[ée]couplage\s+vibratoire\b",
    r"\bisolation\s+vibratoire\b",
    r"\bfiltration\s+vibratoire\b",
    r"\bvibro-?acoustique\b",
    r"\bcoefficient\s+de\s+Poisson\s+n[ée]gatif\b",
    r"\bcoefficient\s+de\s+Poisson\b",
    r"\braideur\s+dynamique\b",
    r"\bcomportement\s+vibratoire\b",
    r"\bfr[ée]quences?\s+de\s+r[ée]sonance\b",
    r"\bfr[ée]quences?\s+propres?\b",
    r"\br[ée]sonances?\s+parasites?\b",
    r"\bbandes?\s+interdites?\b",
    r"\bbandgaps?\b",
    r"\boptimisation\s+topologique\b",
    r"\bhomog[ée]n[ée]isation\s+(?:micromorphique|multi-?[ée]chelle)?\b",
    r"\bstructures?\s+(?:r[ée]entrantes?|nid[s]?\s+d['’]abeilles?|chirales?|sabliers?)\b",
    r"\bplots?\s+de\s+d[ée]couplage\b",
    r"\banalyse\s+m[ée]canique\s+dynamique\b",
    r"\bbanc\s+DMA\b",
    r"\bbanc\s+RAPID\b",
    r"\bviscoanalyseur\b",
    r"\bmarteaux?\s+d['’]impact\b",

    # emballage médical
    r"\bemballage\s+m[ée]dical\b",
    r"\bsyst[èe]me\s+d['’]emballage\s+suspendu\b",
    r"\bemballage\s+suspendu\b",
    r"\bdispositifs?\s+m[ée]dicaux\b",
    r"\bst[ée]rilisation\s+ETO\b",
    r"\bst[ée]rilisation\s+Gamma\b",
    r"\bst[ée]rilisation\s+[àa]\s+la\s+vapeur\b",
    r"\bsyst[èe]mes?\s+de\s+barri[èe]re\s+st[ée]rile\b",
    r"\btests?\s+de\s+chocs?\b",
    r"\btests?\s+d['’]abrasion\b",
    r"\bASTM\s*4169\b",
    r"\bASTM4169\b",

    # électronique / défense / FPGA
    r"\bautodirecteur(?:s)?\b",
    r"\bguidage\s+laser\b",
    r"\bguidage\s+infrarouge\b",
    r"\bguidage\s+IR\b",
    r"\bbanc\s+de\s+mise\s+en\s+[œoe]uvre\b",
    r"\bbanc\s+d['’]essais?\b",
    r"\bBMO\b",
    r"\bBMO\s+NG\b",
    r"\bAD\s+IR\b",
    r"\bAD\s+LASER\b",
    r"\bFPGA\b",
    r"\bGbit\s+Ethernet\b",
    r"\bGigabit\s+Ethernet\b",
    r"\bEthernet\b",
    r"\bRS\s*485\b",
    r"\bRS485\b",
    r"\bRS\s*422\b",
    r"\bRS422\b",
    r"\bLVDS\b",
    r"\bLVTTL\b",
    r"\bUDP\b",
    r"\bPCAP\b",
    r"\bUART\b",
    r"\bModbus\b",
    r"\bMAC\s+TX\b",
    r"\bMAC\s+RX\b",
    r"\bRX_PROCESS\b",
    r"\bIO_GEN\b",
    r"\bRTX64\b",
    r"\bWindows\s+10\s+IoT\b",
    r"\bKintex[-\s]?7\b",
    r"\bKintex\s*7325T\b",
    r"\bVxWorks\b",
    r"\bPXI\b",
    r"\bCOTS\b",
    r"\bIHM\b",
    r"\bPRF\b",
    r"\bdiode\s+laser\b",
    r"\bmicrocontr[ôo]leur\b",
    r"\bcapteurs?\b",
    r"\boptronique\b",
    r"\b[ée]lectro-?optique\b",
    r"\bsyst[èe]mes?\s+de\s+guidage\b",
    r"\btransmission\s+de\s+donn[ée]es\b",
    r"\barchitecture\s+logicielle\b",
    r"\barchitecture\s+mat[ée]rielle\b",
]

MATERIAL_PATTERNS = [
    r"\bTPU\b",
    r"\bTPE\b",
    r"\bPETG(?:/TPU)?\b",
    r"\bPLA\b(?:\s+biosourc[ée])?\b",
    r"\bLSR\b",
    r"\bLiquid\s+Silicone\s+Rubber\b",
    r"\bsilicone\b",
    r"\bEPDM\b",
    r"\bNylon\s+[A-Z]?\b",
    r"\bpolyur[ée]thane(?:\s+thermoplastique)?\b",
    r"\b[ée]lastom[èe]re(?:\s+thermoplastique)?\b",
    r"\bcaoutchouc\b",
    r"\bfibres?\s+de\s+lin\b",
    r"\bfibres?\s+de\s+carbone\b",
    r"\bfibres?\s+d['’]aramide\b",
    r"\bTwaron\b",
    r"\bmatrice\s+[ée]poxy\b",
    r"\bcomposite\s+fibreux\b",
    r"\bpolypropyl[èe]ne\b",
    r"\baluminium\b",
    r"\bacier\b",
    r"\bplomb\b",
    r"\bgraphite\b",
    r"\bmolybd[èe]ne\b",
    r"\bhuile\b",
    r"\bcondensats?\b",
    r"\bmembrane\b",
    r"\bmousse\s+d[ée]coup[ée]e\b",
    r"\bfilm\s+TPU\b",
]

EQUIPMENT_PATTERNS = [
    r"\bviscoanalyseur\b",
    r"\bbanc\s+DMA\b",
    r"\bDMA\b",
    r"\bbanc\s+RAPID\b",
    r"\bpot\s+vibrant\b",
    r"\bmarteaux?\s+d['’]impact\b",
    r"\bcapteurs?\s+triaxes?\b",
    r"\bacc[ée]l[ée]rom[èe]tres?\b",
    r"\bcapteur[s]?\s+d['’]effort\b",
    r"\boutillage\b",
    r"\bbanc\s+d['’]essais?\b",
    r"\bbanc\s+de\s+test\b",
    r"\bbanc\s+de\s+mise\s+en\s+[œoe]uvre\b",
    r"\bmoule\s+(?:imprim[ée]\s+3D|en\s+PLA|m[ée]tallique)\b",
    r"\b[ée]tuve\b",
    r"\benceinte\s+climatique\b",
    r"\bchambre\s+an[ée]cho[ïi]que\b",
    r"\bcarte\s+FPGA\b",
    r"\bmodule\s+FPGA\b",
    r"\bcarte\s+FMC\b",
    r"\bcartes?\s+[ée]lectroniques?\b",
    r"\bPC\s+industriels?\b",
    r"\bcontr[ôo]leur\s+PXI\b",
    r"\bch[âa]ssis\s+PXI\b",
]

ORGANISM_PATTERNS = [
    (r"\bGEMTEX\b", "GEMTEX"),
    (r"\bLEM\s*3\b", "LEM3"),
    (r"\bCEVAA\b", "CEVAA"),
    (r"\bRAPID\b(?:\s+[–-]\s+R[ée]gime[^,\n]{0,60})?", "RAPID"),
    (r"\bANDHEO\b|\bAnhéo\b|\bANDHÉO\b", "Andhéo"),
    (r"\bDYNAE\b", "DYNAE"),
    (r"\bCETIM\b", "CETIM"),
    (r"\bCEA\b", "CEA"),
    (r"\bCNRS\b", "CNRS"),
    (r"\bINSA\b(?:\s+\w+)?", "INSA"),
    (r"\bINRAE?\b", "INRA"),
    (r"\bIFSTTAR\b", "IFSTTAR"),
    (r"\bESIEE\b", "ESIEE"),
    (r"\bTCP\s+R&I\b", "TCP R&I"),
    (r"\bTop\s+Clean\s+Packaging\b", "Top Clean Packaging"),
    (r"\bSAFRAN[-\s]?ED\b", "SAFRAN-ED"),
    (r"\bSafran\b", "Safran"),
    (r"\bAMETRA\b", "AMETRA"),
    (r"\bTHEORIS\b|\bTh[ée]oris\b", "THEORIS"),
    (r"\bLockheed\s+Martin(?:\s+Corporation)?\b", "Lockheed Martin"),
    (r"\bGeotest\s+Marvin\s+Inc\b", "Geotest Marvin Inc"),
    (r"\bGuidance\s+System\s+Evaluation\s+Laboratory\b", "Guidance System Evaluation Laboratory"),
    # DGA est gardé seulement si pas exclu par collaboration NON.
    (r"\bDGA\b", "DGA"),
]

_COMPILED_ORGANISM_PATTERNS = [
    (re.compile(p, re.I | re.U), label) for p, label in ORGANISM_PATTERNS
]

DEFAULT_GLINER_LABELS = [
    "PERSONNE",
    "ORGANISME",
    "TECHNOLOGIE",
    "MATERIAU",
    "COMPOSANT",
    "EQUIPEMENT",
    "NORME",
    "METRIQUE",
    "PROCEDE",
    "PRODUIT",
    "LOGICIEL",
]

DEFAULT_GLINER_MODELS = [
    "urchade/gliner_multi-v2.1",
    "gliner-community/gliner_small-v2.5",
    "urchade/gliner_base",
]

STOP_TERMS = {
    "projet", "travaux", "objectif", "objectifs", "verrou", "verrous",
    "resultat", "résultat", "résultats", "resultats", "contexte",
    "démarche", "demarche", "document", "cir", "r&d",
}

_FRAGMENT_END_RE = re.compile(
    r"\b(?:de|du|des|d['’]?|l['’]?|la|le|les|un|une|en|à|au|aux|pour|avec|sans|dans|par|sur)$",
    re.I | re.U,
)

_BAD_TERM_RE = re.compile(
    r"(?:"
    r"^ces\s+|^cette\s+|^cet\s+|^nous\s+|^ils\s+|^elles\s+|"
    r"a\s+permis\s+le\s+d[ée]veloppement\s+d$|"
    r"r[ée]solution\s+des\s+probl[ée]matiques\s+d$|"
    r"haute\s+technologie\s+l$|"
    r"^figure\s+\d+|^tableau\s+\d+|"
    r"^solution(?:s)?\s+technique(?:s)?$|"
    r"^dispositif(?:s)?\s+m[ée]dical(?:aux)?$|"
    r"^syst[èe]mes?$|^équipements?$|^equipements?$|^mat[ée]riels?$|"
    r"^travaux\s+de\s+r&d$|^recherche\s+et\s+d[ée]veloppement$|"
    r"^la\s+figure$|^le\s+tableau$|^adresse\s+ip\s+(?:source|destination)$|"
    r"^bit\s+\d+$"
    r")",
    re.I | re.U,
)

_GENERIC_EQUIPMENT_RE = re.compile(
    r"^(?:dispositif(?:s)?\s+m[ée]dical(?:aux)?|solutions?\s+techniques?|syst[èe]mes?|équipements?|equipements?|mat[ée]riels?)$",
    re.I | re.U,
)

_PERSON_NOISE_RE = re.compile(
    r"\b(?:nom\s+pr[ée]nom|dipl[ôo]me|fonction|contribution|technicien|ing[ée]nieur|responsable)\b",
    re.I | re.U,
)

_PERSON_NOISE_BLACKLIST_RE = re.compile(
    r"^(?:"
    r"Et\s+De|Du\s+Cir|Au\s+Titre|De\s+L['’]?|Pour\s+Le|"
    r"TCP\s+R&I|Top\s+Clean|Credit\s+Impot|"
    r"Nom\s+Pr[ée]nom|Bts\s+Cpi|Mot\s+Cl[ée]s|"
    r"St[ée]rilisation\s+Gamma|Fourreau\s+Rosace|Sph[èe]re\s+Cube|"
    r"Compound\s+Emballage|Innovation\s+Dispositifs|"
    r"Amortissement\s+R[ée]sistance|Liquid\s+Silicone|"
    r"Justificatif\s+Des|Declares\s+Au|Pr[ée]sentation\s+Globale|"
    r"Strat[ée]gie\s+De|Operation\s+De|Fiche\s+Descriptive|"
    r"Nom\s+De|De\s+Projet|Annexes?\s+Annexe|"
    r"Machines?\s+M[ée]canismes?|Institut\s+Fran[çc]ais|"
    r"M[ée]canique\s+Avanc[ée]e|Produits?\s+Industriels?|"
    r"Lyc[ée]e?\s+(?:Godefroy|Edgar|Jean)|"
    r"Gbit\s+Ethernet|Bmo\s+Ng|Mica\s+Ng|Ad\s+Ir|Ad\s+Laser|Bmo\s+Ir|"
    r"La\s+Figure|Avionics\s+Full|Guidance\s+System|Evaluation\s+Laboratory|"
    r"Lockheed\s+Martin|Geotest\s+Marvin"
    r")$",
    re.I | re.U,
)

_PERSON_TECH_WORD_RE = re.compile(
    r"\b(?:"
    r"Amortissement|R[ée]sistance|St[ée]rilisation|Recyclabilit[ée]|"
    r"S[ée]curisation|Compound|Fourreau|Rosace|Sph[èe]re|Thermoformage|"
    r"Innovation|Justificatif|Annexe|Fiche|D[ée]clar[ée]|Pr[ée]sentation|"
    r"Cr[ée]ativit[ée]|Nouveaut[ée]|Syst[ée]maticit[ée]|Transf[ée]rabilit[ée]|"
    r"Incertitude|Emballage|Dispositif|Mat[ée]riau|Proc[ée]d[ée]|Technologie|"
    r"Conception|Mod[ée]lisation|Validation|Simulation|Vibration|Choc|"
    r"Ethernet|Gbit|BMO|MICA|AD|IR|LASER|Figure|Guidance|System|Evaluation|"
    r"Laboratory|Avionics|Full|FPGA|RTX64|VxWorks|Modbus|UART|UDP|PCAP|PXI"
    r")\b",
    re.U,
)


# ══════════════════════════════════════════════════════════════════════════════
# UTILITAIRES
# ══════════════════════════════════════════════════════════════════════════════

def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _norm(text: Any) -> str:
    text = unicodedata.normalize("NFKC", str(text or ""))
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip(" \t\n\r;:,.()[]{}•-–—")


def _norm_key(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or "").lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _dedupe(values: list[Any], max_items: int | None = None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for v in values or []:
        t = _norm(v)
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


def _is_metric(term: str) -> bool:
    t = _norm(term)
    if METRIC_RE.search(t):
        return True
    if re.fullmatch(
        r"[\d\s.,/%°+\-–àa]+(?:m3/h|m³/h|bars?|bar|°C|db|dB|kWh|Hz|kHz|MHz|GHz|ns|µs|us|ms|s|octets?|bits?|%)?",
        t,
        re.I,
    ):
        return True
    return False


def _is_good_term(term: str) -> bool:
    t = _norm(term)
    if not t:
        return False
    if len(t) < 3 or len(t) > 140:
        return False
    if _norm_key(t) in STOP_TERMS:
        return False
    if re.fullmatch(r"[\d\s.,/%°+\-–àa]+", t):
        return False
    if _FRAGMENT_END_RE.search(t):
        return False
    if _BAD_TERM_RE.search(t):
        return False
    if len(t.split()) > 10 and re.search(
        r"\b(est|sont|avons|avez|ont|permet|permettent|développer|developper|répondre|repondre|réaliser|realiser|mettre|montrent|montre)\b",
        t,
        re.I | re.U,
    ):
        return False
    return True


def _iter_sections(sections: Any):
    if not sections:
        return []
    if isinstance(sections, dict):
        return sections.get("sections", []) or []
    if isinstance(sections, list):
        return sections
    return getattr(sections, "sections", []) or []


def _iter_mappings(evidence_map: Any):
    if not evidence_map:
        return []
    if isinstance(evidence_map, dict):
        return evidence_map.get("mappings", []) or []
    if isinstance(evidence_map, list):
        return evidence_map
    return getattr(evidence_map, "mappings", []) or []


def _collect_texts(sections: Any, evidence_map: Any) -> list[str]:
    texts: list[str] = []

    for s in _iter_sections(sections):
        texts.append(str(_get(s, "title", "") or ""))
        texts.append(str(_get(s, "content", "") or _get(s, "text", "") or ""))

    for m in _iter_mappings(evidence_map):
        concepts = m.get("concepts", []) if isinstance(m, dict) else getattr(m, "concepts", [])
        for c in concepts or []:
            if isinstance(c, dict):
                texts.append(str(c.get("text") or c.get("label") or ""))
            elif c:
                texts.append(str(c))

        evs = m.get("evidences", []) if isinstance(m, dict) else getattr(m, "evidences", [])
        for ev in evs or []:
            texts.append(str(_get(ev, "phrase_source", "") or _get(ev, "phrase", "") or ""))

    return [t for t in texts if str(t).strip()]


# ══════════════════════════════════════════════════════════════════════════════
# EXTRACTION REGEX
# ══════════════════════════════════════════════════════════════════════════════

def _official_keywords_from_text(text: str) -> list[str]:
    out: list[str] = []
    m = re.search(
        r"MOT[S]?\s*CL[ÉE]S\s*(.+?)(?:\n\s*\n|Objectifs|Contexte|Etat de l['’]art|État de l['’]art|Verrous|Démarche|TH[ÉE]SAURUS)",
        text or "",
        re.I | re.S | re.U,
    )
    if m:
        block = m.group(1)[:1200]
        for line in re.split(r"[\n;•·]+", block):
            line = _norm(line)
            line = re.sub(r"^[\-\*·•]\s*", "", line)
            if 3 <= len(line) <= 120 and not re.match(r"^(Objectifs|Contexte|Etat|Verrous|Table)", line, re.I):
                out.append(line)
    return _dedupe(out, 30)


def _extract_metrics(text: str) -> list[str]:
    out = [m.group(0) for m in METRIC_RE.finditer(text or "")]
    for p in [
        r"point\s+de\s+ros[ée]e[^.\n]{0,80}",
        r"\b\d+(?:[,.]\d+)?\s*KiloGrays?\b",
        r"\b\d+(?:[,.]\d+)?\s*coeurs?\s+physiques?\b",
        r"\b\d+(?:[,.]\d+)?\s*octets?\b",
        r"\b\d+(?:[,.]\d+)?\s*bits?\b",
        r"\b\d+(?:[,.]\d+)?\s*MHz\b",
        r"\b\d+(?:[,.]\d+)?\s*GHz\b",
    ]:
        for m in re.finditer(p, text or "", re.I | re.U):
            out.append(m.group(0))
    return _dedupe(out, 80)


def _extract_regex_terms(text: str) -> list[str]:
    out: list[str] = []

    for p in TECH_PATTERNS:
        for m in re.finditer(p, text or "", re.I | re.U):
            term = _norm(m.group(0))
            if _is_good_term(term):
                out.append(term)

    for p in MATERIAL_PATTERNS:
        for m in re.finditer(p, text or "", re.I | re.U):
            term = _norm(m.group(0))
            if _is_good_term(term):
                out.append(term)

    for p in EQUIPMENT_PATTERNS:
        for m in re.finditer(p, text or "", re.I | re.U):
            term = _norm(m.group(0))
            if _is_good_term(term):
                out.append(term)

    # Groupes "analyse/réduction/étude/développement de ..."
    for m in re.finditer(
        r"\b(?:analyse|réduction|reduction|étude|etude|développement|developpement|caractérisation|caracterisation|conversion|traitement)\s+(?:de|du|des|d['’])\s+([a-zàâçéèêëîïôûùüÿñæœ0-9\-\s]{4,90})",
        text or "",
        re.I | re.U,
    ):
        term = _norm(m.group(1))
        if _is_good_term(term) and not _is_metric(term):
            out.append(term)

    return out


def _extract_organisms(text: str) -> list[str]:
    out: list[str] = []
    for pattern, label in _COMPILED_ORGANISM_PATTERNS:
        if pattern.search(text or ""):
            out.append(label)
    return _dedupe(out, 40)


def _has_negative_defense_collaboration(text: str) -> bool:
    t = _norm_key(text)
    return bool(
        re.search(r"collaboration avec le ministere de la defense", t)
        and re.search(r"\bnon\b", t)
    )


def _filter_false_organisms(orgs: list[str], text: str) -> list[str]:
    defense_negative = _has_negative_defense_collaboration(text)
    out: list[str] = []

    for org in orgs or []:
        k = _norm_key(org)

        if not k or k in {"le mi", "si oui", "non", "oui", "germes", "equipe pluridisciplinaire"}:
            continue

        # BMO n'est pas un organisme.
        if k in {"bmo", "bmo ng", "bmo ir", "bmo laser"}:
            continue

        # DGA : si le tableau indique NON, ne pas garder comme partenaire.
        if defense_negative and k in {"dga", "ministere de la defense", "ministère de la défense"}:
            continue

        out.append(org)

    return _dedupe(out, 40)


# ══════════════════════════════════════════════════════════════════════════════
# PERSONNES
# ══════════════════════════════════════════════════════════════════════════════

_PERSON_TABLE_RE = re.compile(
    r"\b([A-ZÉÈÀÂÎÏÔÛÙÇ][a-zéèàâîïôûùç\-]+)\s+([A-ZÉÈÀÂÎÏÔÛÙÇ][a-zéèàâîïôûùç\-]+)\b",
    re.U,
)

_PERSON_UPPER_RE = re.compile(
    r"\b([A-ZÉÈÀÂÎÏÔÛÙÇ]{2,})\s+([A-ZÉÈÀÂÎÏÔÛÙÇ]{2,})(?:\s+[A-ZÉÈÀÂÎÏÔÛÙÇ]{2,})?\b",
    re.U,
)


def _is_person_noise_token(name: str) -> bool:
    n = str(name or "").strip()
    if not n:
        return True

    if _PERSON_NOISE_BLACKLIST_RE.match(n):
        return True

    if _PERSON_TECH_WORD_RE.search(n):
        return True

    if re.search(r"\d", n):
        return True

    if re.search(r"[|@/\\&%$#!?_:]", n):
        return True

    parts = n.split()
    if len(parts) < 2:
        return True

    # Deux mots courts tout en majuscule = souvent acronyme technique.
    if all(p.isupper() for p in parts) and sum(len(p) for p in parts) <= 8:
        return True

    # Connecteurs ou mots non nominaux.
    bad_words = {
        "cependant", "toutefois", "figure", "tableau", "adresse", "source",
        "destination", "laser", "ethernet", "gbit", "bmo", "mica", "guidance",
        "system", "evaluation", "laboratory", "avionics", "full", "fpga",
        "modbus", "uart", "udp", "pcap", "pxi", "theoris", "safran",
    }
    if any(_norm_key(p) in bad_words for p in parts):
        return True

    return False


def _looks_like_person(name: str) -> bool:
    n = _norm(name)
    if not (5 <= len(n) <= 70):
        return False
    if _PERSON_NOISE_RE.search(n):
        return False
    if _is_person_noise_token(n):
        return False

    nk = _norm_key(n)
    if nk in {
        "nom prenom",
        "top clean",
        "credit impot",
        "tableau figure",
        "dispositif medical",
        "gbit ethernet",
        "bmo ng",
        "mica ng",
        "ad ir",
        "ad laser",
        "bmo ir",
        "la figure",
    }:
        return False

    if re.search(
        r"\b(sarl|sas|sa|groupe|packaging|laboratoire|ministere|dga|theoris|safran|ametra|lockheed|geotest|marvin|laboratory|system|ethernet|laser|fpga)\b",
        nk,
    ):
        return False

    return True


def _normalize_person_order(name: str) -> str:
    """
    Garde la forme telle qu'elle est, mais permet plus tard une déduplication simple.
    Exemple : "Vergne Hervé" et "Hervé Vergne" restent deux chaînes différentes ici,
    car le choix dépend du format souhaité dans final_output_guard.
    """
    return _norm(name)


def _extract_people_clean(text: str) -> list[str]:
    out: list[str] = []

    # Noms dans tableaux RH : "Berry Alexis", "Jeannin Céline", etc.
    for m in _PERSON_TABLE_RE.finditer(text or ""):
        full = _normalize_person_order(f"{m.group(1)} {m.group(2)}")
        if _looks_like_person(full):
            out.append(full)

    # Noms en majuscules.
    for m in _PERSON_UPPER_RE.finditer(text or ""):
        full = _normalize_person_order(f"{m.group(1)} {m.group(2)}").title()
        if _looks_like_person(full):
            out.append(full)

    return _dedupe(out, 60)


# ══════════════════════════════════════════════════════════════════════════════
# GLiNER
# ══════════════════════════════════════════════════════════════════════════════

def _split_long_text_for_gliner(text: str, max_chars: int = 1100) -> list[str]:
    text = str(text or "").strip()
    if not text:
        return []

    parts: list[str] = []

    for para in re.split(r"\n{2,}", text):
        para = para.strip()
        if not para:
            continue

        if len(para) <= max_chars:
            parts.append(para)
            continue

        sentences = re.split(r"(?<=[.!?;:])\s+", para)
        current = ""

        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue

            if len(current) + len(sent) + 1 <= max_chars:
                current = (current + " " + sent).strip()
            else:
                if current:
                    parts.append(current)
                current = sent

        if current:
            if len(current) <= max_chars:
                parts.append(current)
            else:
                # Dernier recours : découpe par mots, jamais par caractères bruts.
                buf = ""
                for w in current.split():
                    if len(buf) + len(w) + 1 <= max_chars:
                        buf = (buf + " " + w).strip()
                    else:
                        if buf:
                            parts.append(buf)
                        buf = w
                if buf:
                    parts.append(buf)

    return parts


def _chunk_for_gliner(texts: list[str], max_chars_per_chunk: int = 1100) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for t in texts:
        for piece in _split_long_text_for_gliner(str(t or ""), max_chars=max_chars_per_chunk):
            if current_len + len(piece) + 1 > max_chars_per_chunk and current:
                chunks.append("\n".join(current))
                current = []
                current_len = 0

            current.append(piece)
            current_len += len(piece) + 1

    if current:
        chunks.append("\n".join(current))

    return chunks


def _run_gliner(
    texts: list[str],
    model_name: str,
    labels: list[str],
    max_chars: int = 12000,
) -> list[dict]:
    """
    Lance GLiNER uniquement quand use_gliner=True.

    Important :
    - le module technical_terms_extractor.py peut être importé sans charger GLiNER ;
    - GLiNER / Transformers / Torch ne sont chargés qu'ici ;
    - cela évite de bloquer modules.NLP.router au démarrage.
    """
    try:
        GLiNERClass = _load_gliner_class()
    except Exception as exc:
        logger.warning("GLiNER indisponible : %s", exc)
        return []

    _install_tokenizer_regex_fix()

    candidates = [model_name] + [m for m in DEFAULT_GLINER_MODELS if m != model_name]

    for candidate in candidates:
        try:
            # Le patch AutoTokenizer est déjà installé.
            model = GLiNERClass.from_pretrained(candidate)
            chunks = _chunk_for_gliner(texts, max_chars_per_chunk=1100)

            seen_texts: set[str] = set()
            out: list[dict] = []

            for chunk in chunks:
                try:
                    ents = model.predict_entities(chunk, labels, threshold=0.35)
                except Exception as chunk_exc:
                    logger.debug("GLiNER chunk erreur : %s", chunk_exc)
                    continue

                for e in ents or []:
                    t = _norm(e.get("text", ""))
                    label = str(e.get("label", "") or "").upper()

                    if not _is_good_term(t):
                        continue

                    # Ne jamais accepter les faux-personnes évidentes.
                    if label == "PERSONNE" and not _looks_like_person(t):
                        continue

                    k = f"{label}:{_norm_key(t)}"
                    if k in seen_texts:
                        continue

                    seen_texts.add(k)
                    out.append(
                        {
                            "text": t,
                            "label": label,
                            "score": round(float(e.get("score", 0.0) or 0.0), 3),
                        }
                    )

            logger.info("GLiNER utilisé : %s | %d chunks | %d entités", candidate, len(chunks), len(out))
            return out

        except Exception as exc:
            logger.warning("GLiNER erreur avec %s : %s", candidate, exc)

    return []


# ══════════════════════════════════════════════════════════════════════════════
# API PUBLIQUE
# ══════════════════════════════════════════════════════════════════════════════

def extract_technical_terms(
    sections: Any = None,
    evidence_map: Any = None,
    use_gliner: bool = False,
    gliner_model: str = "urchade/gliner_multi-v2.1",
    gliner_labels: list[str] | None = None,
    max_high_confidence: int = 18,
    max_candidates: int = 35,
) -> dict:
    texts = _collect_texts(sections, evidence_map)
    joined = "\n".join(texts)

    official = _official_keywords_from_text(joined)
    metrics = _extract_metrics(joined)
    organisms_clean = _extract_organisms(joined)

    counter: Counter = Counter()
    mat_counter: Counter = Counter()
    equip_counter: Counter = Counter()
    norm_counter: Counter = Counter()
    method_counter: Counter = Counter()

    people_clean: list[str] = [p for p in _extract_people_clean(joined) if _looks_like_person(p)]

    for k in official:
        if _is_good_term(k) and not _is_metric(k):
            counter[k] += 8

    for m in _iter_mappings(evidence_map):
        concepts = m.get("concepts", []) if isinstance(m, dict) else getattr(m, "concepts", [])
        for c in concepts or []:
            if isinstance(c, dict):
                term = c.get("text") or c.get("label") or c.get("name")
            else:
                term = c

            term = _norm(term)
            if _is_good_term(term):
                if _is_metric(term):
                    metrics.append(term)
                else:
                    counter[term] += 2

    for term in _extract_regex_terms(joined):
        if _is_metric(term):
            metrics.append(term)
        else:
            counter[term] += 3

    for p in MATERIAL_PATTERNS:
        for m in re.finditer(p, joined, re.I | re.U):
            t = _norm(m.group(0))
            if _is_good_term(t):
                mat_counter[t] += 4

    for p in EQUIPMENT_PATTERNS:
        for m in re.finditer(p, joined, re.I | re.U):
            t = _norm(m.group(0))
            if _is_good_term(t) and not _GENERIC_EQUIPMENT_RE.match(t):
                equip_counter[t] += 4

    gliner_entities: list[dict] = []

    if use_gliner:
        gliner_entities = _run_gliner(
            texts,
            gliner_model,
            gliner_labels or DEFAULT_GLINER_LABELS,
        )

        for ent in gliner_entities:
            term = _norm(ent.get("text", ""))
            label = str(ent.get("label", "") or "").upper()

            if not _is_good_term(term):
                continue

            if _is_metric(term) or label == "METRIQUE":
                metrics.append(term)

            elif label == "PERSONNE":
                if _looks_like_person(term):
                    people_clean.append(term)

            elif label == "ORGANISME":
                organisms_clean.append(term)

            elif label in {"MATERIAU", "COMPOSANT"}:
                mat_counter[term] += 3

            elif label == "EQUIPEMENT":
                if not _GENERIC_EQUIPMENT_RE.match(term):
                    equip_counter[term] += 3

            elif label == "NORME":
                norm_counter[term] += 3

            elif label in {"TECHNOLOGIE", "PROCEDE", "PRODUIT", "LOGICIEL", "METHODE"}:
                counter[term] += 2

    # Classements
    best: dict[str, tuple[str, int]] = {}
    for term, score in counter.items():
        k = _norm_key(term)
        if k not in best or score > best[k][1]:
            best[k] = (term, score)

    ranked = [
        t for t, s in sorted(best.values(), key=lambda x: (-x[1], x[0].lower()))
        if _is_good_term(t)
    ]

    mat_best: dict[str, tuple[str, int]] = {}
    for term, score in mat_counter.items():
        k = _norm_key(term)
        if k not in mat_best or score > mat_best[k][1]:
            mat_best[k] = (term, score)

    equip_best: dict[str, tuple[str, int]] = {}
    for term, score in equip_counter.items():
        k = _norm_key(term)
        if k not in equip_best or score > equip_best[k][1]:
            equip_best[k] = (term, score)

    norm_best: dict[str, tuple[str, int]] = {}
    for term, score in norm_counter.items():
        k = _norm_key(term)
        if k not in norm_best or score > norm_best[k][1]:
            norm_best[k] = (term, score)

    technologies: list[str] = []
    normes: list[str] = []
    methodes: list[str] = []

    for t in ranked:
        n = _norm_key(t)

        if re.search(r"\b(re2020|rt2012|norme|règlement|reglement|astm|iso|udp|modbus|pcap)\b", n):
            normes.append(t)
        elif re.search(r"\b(analyse|étude|etude|développement|developpement|modélisation|modelisation|simulation|test|essai|conversion|traitement)\b", n):
            methodes.append(t)
            technologies.append(t)
        else:
            technologies.append(t)

    high = [t for t in ranked if not _is_metric(t)][:max_high_confidence]
    high_keys = {_norm_key(h) for h in high}
    candidates = [
        t for t in ranked
        if not _is_metric(t) and _norm_key(t) not in high_keys
    ][:max_candidates]

    materiaux_list = _dedupe(
        [t for t, s in sorted(mat_best.values(), key=lambda x: (-x[1], x[0].lower())) if _is_good_term(t)],
        40,
    )

    equipements_list = _dedupe(
        [t for t, s in sorted(equip_best.values(), key=lambda x: (-x[1], x[0].lower())) if _is_good_term(t)],
        40,
    )

    normes_list = _dedupe(
        [t for t, s in sorted(norm_best.values(), key=lambda x: (-x[1], x[0].lower())) if _is_good_term(t)] + normes,
        40,
    )

    organisms_final = _filter_false_organisms(organisms_clean, joined)
    people_final = _dedupe([p for p in people_clean if _looks_like_person(p)], 60)

    # Sécurité finale : retirer les organismes des personnes.
    org_keys = {_norm_key(o) for o in organisms_final}
    people_final = [
        p for p in people_final
        if _norm_key(p) not in org_keys and _looks_like_person(p)
    ]

    metrics_final = _dedupe(metrics, 100)

    high = _dedupe(high, max_high_confidence)
    candidates = _dedupe(candidates, max_candidates)

    return {
        "mots_cles_projet": {
            "high_confidence": high,
            "candidates": candidates,
        },
        "technologies": _dedupe(technologies, 60),
        "materiaux_composants": materiaux_list,
        "equipements": equipements_list,
        "metriques": metrics_final,
        "normes": normes_list,
        "methodes": _dedupe(methodes, 40),
        "organismes_detectes": organisms_final,
        "partenaires_rd": organisms_final,
        "personnes_detectees": people_final,
        "gliner_entities_count": len(gliner_entities),
        "stats": {
            "keywords": len(high) + len(candidates),
            "metrics": len(metrics_final),
            "partners": len(organisms_final),
            "people": len(people_final),
            "organisms": len(organisms_final),
            "gliner_entities": len(gliner_entities),
            "version": "7.4.2-tokenizer-fix",
        },
    }


# Alias compatibilité si ton router appelle un autre nom.
def extract_terms(*args, **kwargs) -> dict:
    return extract_technical_terms(*args, **kwargs)
