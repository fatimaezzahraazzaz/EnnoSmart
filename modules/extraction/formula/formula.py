"""
modules/extraction/formula/formula.py
──────────────────────────────────────────────────────────────────────────────
Extraction rapide des formules R&D / CIR.

Objectif de cette version :
- Par défaut : formules désactivées par défaut pendant la refonte evidence-first.
- Extraction fidèle et rapide : texte natif, OMML Word/PPTX, Excel, image OCR.
- Mode optionnel "explain" si tu veux une explication LLM plus tard.
- Réduction du bruit : on n'invente pas "Module d'Young" ou "Poisson" sans vraie formule.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)

DETECTION_THRESHOLD = 0.42
MAX_FORMULAS = 8

# Modes :
# - off     : aucune formule
# - fast    : extraction rapide sans LLM
# - explain : extraction + explication LLM
DEFAULT_FORMULA_MODE = "off"


class FormulaDomain(str, Enum):
    MECANIQUE = "mécanique"
    CHIMIE = "chimie"
    MATHEMATIQUES = "mathématiques"
    INFORMATIQUE = "informatique"
    PHYSIQUE = "physique"
    ELECTRONIQUE = "électronique"
    BIOLOGIE = "biologie"
    STATISTIQUES = "statistiques"
    INCONNU = "inconnu"


class FormulaSource(str, Enum):
    TEXT_NATIVE = "text_native"
    IMAGE = "image"
    OMML = "omml"
    EXCEL = "excel"


class ExtractionQuality(str, Enum):
    LLM = "llm"
    PIX2TEX = "pix2tex"
    HEURISTIC = "heuristic"
    FAILED = "failed"


@dataclass
class FormulaResult:
    source: FormulaSource
    domain: FormulaDomain
    latex: str
    explanation: str
    rag_chunk: str
    quality: ExtractionQuality
    confidence: float
    page_number: Optional[int] = None
    tags: list[str] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────────
# Détection
# ──────────────────────────────────────────────────────────────────────────────

_UNICODE_MATH = re.compile(
    r"[\u0391-\u03C9\u2200-\u22FF\u2100-\u214F\u2190-\u21FF"
    r"\u00B2\u00B3\u00B9\u2070-\u209F]"
)

_STRONG_FORMULA_PATTERNS = [
    # équation structurée : F = m a, y = ax+b, etc.
    re.compile(r"\b[A-Za-zΑ-Ωα-ω][A-Za-zΑ-Ωα-ω0-9_]{0,8}\s*(?:=|≈|≃|~)\s*[-+]?[\wΑ-Ωα-ω\\\(\)\[\]\{\}\+\-\*/\^., ]{2,}", re.U),
    # fonctions math/science
    re.compile(r"\b(?:sin|cos|tan|exp|log|ln|lim|det|grad|div|sqrt|softmax|sigmoid)\s*[\(\[{]", re.I),
    # LaTeX ou inline math
    re.compile(r"\\(?:frac|sum|int|sqrt|alpha|beta|gamma|theta|lambda|mu|sigma|rho|partial|nabla)\b|\$[^$]{2,}\$", re.I),
    # complexité algorithmique
    re.compile(r"\bO\s*\(\s*[^)]{1,40}\)", re.I),
    # dérivées, intégrales, sommes
    re.compile(r"(?:∑|∫|∂|∇|√|≤|≥|≠|≈|∞)"),
    # chimie réactionnelle
    re.compile(r"\b[A-Z][a-z]?\d*(?:[A-Z][a-z]?\d*)+\s*(?:→|⇌|=>)\s*[A-Z][a-z]?", re.U),
]

_WEAK_UNIT_PATTERN = re.compile(
    r"(?<![A-Za-z])[-+]?\d+(?:[.,]\d+)?\s*(?:Hz|N|Pa|GPa|MPa|kPa|kg|m/s|mm|cm|m|rad|°C|V|A|Ω|%|ms|s)\b",
    re.I,
)

_TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$")
_TOC_LINE_RE = re.compile(r"^\s*\d+(?:\.\d+)*\s+.+\s+\d{1,3}\s*$")


def _is_probably_table_or_toc(text: str) -> bool:
    lines = [l.strip() for l in str(text or "").splitlines() if l.strip()]
    if not lines:
        return False
    table_ratio = sum(1 for l in lines if _TABLE_LINE_RE.match(l)) / len(lines)
    toc_ratio = sum(1 for l in lines if _TOC_LINE_RE.match(l)) / len(lines)
    return table_ratio > 0.45 or toc_ratio > 0.55


def _detect(text: str, context: str = "") -> tuple[bool, float]:
    """
    Détection volontairement plus stricte :
    - Une unité seule ne suffit plus.
    - Une parenthèse seule ne suffit plus.
    - Le score doit venir de vrais signaux de formule.
    """
    if not text:
        return False, 0.0

    t = unicodedata.normalize("NFKC", str(text))
    if _is_probably_table_or_toc(t):
        return False, 0.0

    score = 0.0

    strong_hits = 0
    for pattern in _STRONG_FORMULA_PATTERNS:
        hits = len(pattern.findall(t))
        if hits:
            strong_hits += hits
            score += min(0.35 * hits, 0.70)

    unicode_hits = len(_UNICODE_MATH.findall(t))
    if unicode_hits:
        score += min(unicode_hits * 0.05, 0.35)

    unit_hits = len(_WEAK_UNIT_PATTERN.findall(t))
    # Les unités seules sont seulement un petit signal.
    if unit_hits and strong_hits:
        score += min(unit_hits * 0.08, 0.20)
    elif unit_hits >= 3:
        score += 0.12

    # Signal informatique/math : O(n log n), pertes, scores, probas
    if re.search(r"\b(?:loss|accuracy|precision|recall|entropy|gradient|embedding|similarity|coverage|score)\b", t, re.I) and strong_hits:
        score += 0.10

    score = min(round(score, 2), 1.0)
    return score >= DETECTION_THRESHOLD, score


# ──────────────────────────────────────────────────────────────────────────────
# Conversion LaTeX / heuristiques
# ──────────────────────────────────────────────────────────────────────────────

_U2L = {
    "α": "\\alpha", "β": "\\beta", "γ": "\\gamma", "δ": "\\delta",
    "ε": "\\epsilon", "θ": "\\theta", "ϑ": "\\theta", "λ": "\\lambda",
    "μ": "\\mu", "ν": "\\nu", "π": "\\pi", "ρ": "\\rho",
    "σ": "\\sigma", "τ": "\\tau", "φ": "\\phi", "ω": "\\omega",
    "Δ": "\\Delta", "Ω": "\\Omega",
    "∑": "\\sum", "∏": "\\prod", "∫": "\\int", "∂": "\\partial",
    "∇": "\\nabla", "∞": "\\infty", "√": "\\sqrt",
    "≤": "\\leq", "≥": "\\geq", "≠": "\\neq", "≈": "\\approx",
    "±": "\\pm", "×": "\\times", "÷": "\\div", "·": "\\cdot",
    "→": "\\rightarrow", "←": "\\leftarrow", "⇌": "\\rightleftharpoons",
    "⇒": "\\Rightarrow", "⇔": "\\Leftrightarrow",
    "²": "^{2}", "³": "^{3}", "¹": "^{1}",
    "₀": "_{0}", "₁": "_{1}", "₂": "_{2}", "₃": "_{3}", "₄": "_{4}",
    "₅": "_{5}", "₆": "_{6}", "₇": "_{7}", "₈": "_{8}", "₉": "_{9}",
}

_MATH_ITALIC_MAP: dict[str, str] = {}
for _i, _c in enumerate("abcdefghijklmnopqrstuvwxyz"):
    _MATH_ITALIC_MAP[chr(0x1D44E + _i)] = _c
for _i, _c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
    _MATH_ITALIC_MAP[chr(0x1D434 + _i)] = _c
_MATH_ITALIC_MAP.update({
    "\U0001D70C": "ρ",
    "\U0001D708": "ν",
    "\U0001D717": "θ",
    "−": "-",
})


def _normalize_math_unicode(text: str) -> str:
    return "".join(_MATH_ITALIC_MAP.get(c, c) for c in str(text or ""))


def _to_latex(text: str) -> str:
    t = unicodedata.normalize("NFKC", _normalize_math_unicode(str(text or ""))).strip()
    if not t:
        return ""

    # Nettoyages d'extraction fréquents
    t = t.replace("h o", "ρ")
    t = re.sub(r"\s+", " ", t)

    for char, cmd in _U2L.items():
        t = t.replace(char, cmd)

    # puissance/indice simples
    t = re.sub(r"([A-Za-z0-9\)])\^([A-Za-z0-9])", r"\1^{\2}", t)
    t = re.sub(r"([A-Za-z0-9\)])_([A-Za-z0-9])", r"\1_{\2}", t)

    # O(n log n) -> O(n \log n)
    t = re.sub(r"\blog\b", r"\\log", t)

    return t.strip()


def _guess_domain(latex_or_text: str, context: str = "") -> FormulaDomain:
    s = f"{latex_or_text} {context}".lower()
    if any(x in s for x in ["accuracy", "loss", "softmax", "embedding", "complexité", "complexity", "o("]):
        return FormulaDomain.INFORMATIQUE
    if any(x in s for x in ["mol", "réaction", "reaction", "h2o", "ph", "enthalpie"]):
        return FormulaDomain.CHIMIE
    if any(x in s for x in ["v", "ω", "ohm", "tension", "courant", "diode", "mosfet"]):
        return FormulaDomain.ELECTRONIQUE
    if any(x in s for x in ["gpa", "mpa", "force", "contrainte", "young", "poisson"]):
        return FormulaDomain.MECANIQUE
    if any(x in s for x in ["prob", "variance", "écart", "sigma", "moyenne"]):
        return FormulaDomain.STATISTIQUES
    return FormulaDomain.INCONNU


def _short_explanation(source: FormulaSource, domain: FormulaDomain) -> str:
    # Important : pas d'interprétation spécifique inventée.
    if source == FormulaSource.EXCEL:
        return "Formule Excel extraite sans interprétation LLM."
    if source == FormulaSource.OMML:
        return "Équation native Word/PowerPoint extraite sans interprétation LLM."
    if source == FormulaSource.IMAGE:
        return "Formule extraite depuis une image sans interprétation LLM."
    if domain == FormulaDomain.INFORMATIQUE:
        return "Expression mathématique ou algorithmique extraite sans interprétation LLM."
    return "Formule extraite automatiquement sans interprétation LLM."


def _chunk(
    latex: str,
    explanation: str,
    domain: FormulaDomain,
    source: FormulaSource,
    quality: ExtractionQuality,
    page_number: Optional[int],
    confidence: float,
) -> str:
    page = f" | PAGE {page_number}" if page_number else ""
    return (
        f"[FORMULE | {domain.value} | {source.value}{page}]\n"
        f"[QUALITÉ: {quality.value} | confiance: {confidence:.2f}]\n"
        f"LaTeX: {latex}\n"
        f"Note: {explanation}"
    )


def _formula_hash(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").lower())


def _is_bad_formula_candidate(value: str) -> bool:
    s = str(value or "").strip()
    if len(s) < 3:
        return True
    low = s.lower()
    bad = {
        "module d'young", "coefficient de poisson", "formule mathématique extraite automatiquement",
        "description technique", "gpa", "mpa", "pa", "hz", "ms", "%"
    }
    if low in bad:
        return True
    if low.startswith("description technique"):
        return True
    if re.fullmatch(r"[-+]?\d+(?:[.,]\d+)?\s*(?:gpa|mpa|pa|hz|ms|s|%)", low):
        return True
    return False


def _extract_candidates_from_text(text: str) -> list[str]:
    t = unicodedata.normalize("NFKC", str(text or ""))
    candidates: list[str] = []

    # Lignes avec vrais signaux
    for line in t.splitlines():
        s = line.strip(" \t•-;")
        if not s or len(s) > 280:
            continue
        if any(p.search(s) for p in _STRONG_FORMULA_PATTERNS):
            candidates.append(s)

    # Inline LaTeX
    for m in re.finditer(r"\$([^$]{2,120})\$", t):
        candidates.append(m.group(1).strip())

    # Complexités algorithmiques
    for m in re.finditer(r"\bO\s*\(\s*[^)]{1,40}\)", t, re.I):
        candidates.append(m.group(0).strip())

    # Équations inline courtes non captées ligne entière
    for m in re.finditer(r"\b[A-Za-zΑ-Ωα-ω][A-Za-zΑ-Ωα-ω0-9_]{0,8}\s*(?:=|≈|≃|~)\s*[-+]?[^.;,\n]{2,120}", t):
        candidates.append(m.group(0).strip())

    # Déduplication
    out, seen = [], set()
    for c in candidates:
        c = re.sub(r"\s+", " ", c).strip()
        if _is_bad_formula_candidate(c):
            continue
        k = _formula_hash(c)
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out[:MAX_FORMULAS]


def _heuristic_extract(
    text: str,
    page_number: Optional[int],
    source: FormulaSource,
    context: str = "",
    confidence: float = 0.55,
) -> list[FormulaResult]:
    results: list[FormulaResult] = []
    seen: set[str] = set()

    for candidate in _extract_candidates_from_text(text):
        latex = _to_latex(candidate)
        if not latex or _is_bad_formula_candidate(latex):
            continue
        k = _formula_hash(latex)
        if k in seen:
            continue
        seen.add(k)
        domain = _guess_domain(latex, context)
        expl = _short_explanation(source, domain)
        results.append(FormulaResult(
            source=source,
            domain=domain,
            latex=latex,
            explanation=expl,
            rag_chunk=_chunk(latex, expl, domain, source, ExtractionQuality.HEURISTIC, page_number, confidence),
            quality=ExtractionQuality.HEURISTIC,
            confidence=confidence,
            page_number=page_number,
            tags=["FORMULA:FAST", f"DOMAIN:{domain.value.upper()}", "NO_LLM"],
        ))
    return results


# ──────────────────────────────────────────────────────────────────────────────
# Sources spéciales
# ──────────────────────────────────────────────────────────────────────────────

def _from_image(image_bytes: bytes) -> tuple[str, float]:
    try:
        from pix2tex.cli import LatexOCR
        from PIL import Image
        import io
        latex = LatexOCR()(Image.open(io.BytesIO(image_bytes)).convert("RGB"))
        latex = (latex or "").strip()
        return (latex, 0.82) if latex else ("", 0.0)
    except ImportError:
        logger.debug("pix2tex non installé : image formule ignorée.")
        return "", 0.0
    except Exception as exc:
        logger.debug("pix2tex : %s", exc)
        return "", 0.0


def _from_omml(xml: str) -> str:
    try:
        import xml.etree.ElementTree as ET
        ns = "http://schemas.openxmlformats.org/officeDocument/2006/math"

        def norm(t: str) -> str:
            return _normalize_math_unicode(unicodedata.normalize("NFKC", (t or "").replace("\xa0", ""))).strip()

        def parse_container(el) -> str:
            if el is None:
                return ""
            parts = []
            if el.text and el.text.strip():
                parts.append(norm(el.text))
            for child in el:
                parts.append(parse(child))
            return "".join(parts)

        def parse(el) -> str:
            tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
            if tag == "t":
                return norm(el.text or "")
            if tag == "f":
                num = parse_container(el.find(f"{{{ns}}}num"))
                den = parse_container(el.find(f"{{{ns}}}den"))
                return f"\\frac{{{num}}}{{{den}}}" if num and den else f"{num}/{den}"
            if tag == "sSup":
                base = parse_container(el.find(f"{{{ns}}}e"))
                sup = parse_container(el.find(f"{{{ns}}}sup"))
                return f"{base}^{{{sup}}}" if sup else base
            if tag == "sSub":
                base = parse_container(el.find(f"{{{ns}}}e"))
                sub = parse_container(el.find(f"{{{ns}}}sub"))
                return f"{base}_{{{sub}}}" if sub else base
            if tag == "sSubSup":
                base = parse_container(el.find(f"{{{ns}}}e"))
                sub = parse_container(el.find(f"{{{ns}}}sub"))
                sup = parse_container(el.find(f"{{{ns}}}sup"))
                return f"{base}_{{{sub}}}^{{{sup}}}"
            if tag == "rad":
                base = parse_container(el.find(f"{{{ns}}}e"))
                deg = parse_container(el.find(f"{{{ns}}}deg"))
                return f"\\sqrt[{deg}]{{{base}}}" if deg else f"\\sqrt{{{base}}}"
            return parse_container(el)

        result = parse_container(ET.fromstring(xml))
        return _to_latex(result)
    except Exception as exc:
        logger.debug("OMML parse error: %s", exc)
        return ""


def _from_excel(formula: str) -> str:
    if not str(formula or "").startswith("="):
        return str(formula or "")
    t = str(formula)[1:]
    mapping = {
        "SUM(": "\\sum(", "SQRT(": "\\sqrt{", "ABS(": "|",
        "EXP(": "e^{", "LOG(": "\\log(", "LN(": "\\ln(",
        "SIN(": "\\sin(", "COS(": "\\cos(", "TAN(": "\\tan(",
        "PI()": "\\pi", "AVERAGE(": "\\bar{x}=\\frac{1}{n}\\sum(",
        "STDEV(": "\\sigma(", "VAR(": "\\sigma^{2}(",
        "MAX(": "\\max(", "MIN(": "\\min(", "*": "\\cdot ",
    }
    for k, v in mapping.items():
        t = t.replace(k, v)
    return t


# ──────────────────────────────────────────────────────────────────────────────
# LLM optionnel uniquement pour formula_mode="explain"
# ──────────────────────────────────────────────────────────────────────────────

_LLM_SYSTEM = """Tu expliques brièvement une formule extraite d'un document R&D/CIR.
Réponds uniquement en JSON valide : {"explanation":"...", "domain":"mathématiques|informatique|physique|mécanique|chimie|électronique|biologie|statistiques|inconnu"}.
N'invente pas de domaine mécanique si la formule ne le montre pas clairement.
"""

def _explain_with_llm(result: FormulaResult, context: str = "") -> FormulaResult:
    try:
        import ollama as sdk
        response = sdk.chat(
            model="mistral:7b-instruct",
            messages=[
                {"role": "system", "content": _LLM_SYSTEM},
                {"role": "user", "content": f"Formule: {result.latex}\nContexte: {context[:600]}"},
            ],
            options={"temperature": 0.0, "num_predict": 120},
        )
        raw = response.message.content.strip()
        data = json.loads(re.search(r"\{.*\}", raw, flags=re.S).group(0))
        explanation = str(data.get("explanation") or result.explanation).strip()
        domain = _map_domain(str(data.get("domain") or result.domain.value))
        result.explanation = explanation
        result.domain = domain
        result.quality = ExtractionQuality.LLM
        result.tags = [t for t in result.tags if t != "NO_LLM"] + ["FORMULA:LLM_EXPLAIN"]
        result.rag_chunk = _chunk(result.latex, result.explanation, result.domain, result.source, result.quality, result.page_number, result.confidence)
        return result
    except Exception as exc:
        logger.debug("Explication LLM formule ignorée : %s", exc)
        return result


def _map_domain(s: str) -> FormulaDomain:
    low = str(s or "").lower()
    mapping = {
        "mécanique": FormulaDomain.MECANIQUE, "mecanique": FormulaDomain.MECANIQUE,
        "chimie": FormulaDomain.CHIMIE,
        "mathématiques": FormulaDomain.MATHEMATIQUES, "mathematiques": FormulaDomain.MATHEMATIQUES, "math": FormulaDomain.MATHEMATIQUES,
        "informatique": FormulaDomain.INFORMATIQUE,
        "physique": FormulaDomain.PHYSIQUE,
        "électronique": FormulaDomain.ELECTRONIQUE, "electronique": FormulaDomain.ELECTRONIQUE,
        "biologie": FormulaDomain.BIOLOGIE,
        "statistiques": FormulaDomain.STATISTIQUES,
    }
    return mapping.get(low, FormulaDomain.INCONNU)


# ──────────────────────────────────────────────────────────────────────────────
# Entrée publique
# ──────────────────────────────────────────────────────────────────────────────

def extract_formulas(
    text: Optional[str] = None,
    image_bytes: Optional[bytes] = None,
    omml_xml: Optional[str] = None,
    excel_formula: Optional[str] = None,
    context: str = "",
    page_number: Optional[int] = None,
    force: bool = False,
    formula_mode: str = DEFAULT_FORMULA_MODE,
) -> list[FormulaResult]:
    """
    formula_mode:
      - "off"     : retourne []
      - "fast"    : extraction rapide sans LLM
      - "explain" : extraction rapide + explication LLM courte
    """
    mode = (formula_mode or DEFAULT_FORMULA_MODE).lower().strip()
    if mode in {"off", "none", "disabled", "false"}:
        return []

    results: list[FormulaResult] = []

    if image_bytes:
        latex, conf = _from_image(image_bytes)
        if not latex or _is_bad_formula_candidate(latex):
            return []
        domain = _guess_domain(latex, context)
        expl = _short_explanation(FormulaSource.IMAGE, domain)
        results = [FormulaResult(
            source=FormulaSource.IMAGE,
            domain=domain,
            latex=latex,
            explanation=expl,
            rag_chunk=_chunk(latex, expl, domain, FormulaSource.IMAGE, ExtractionQuality.PIX2TEX, page_number, conf),
            quality=ExtractionQuality.PIX2TEX,
            confidence=conf,
            page_number=page_number,
            tags=["FORMULA:IMAGE", "NO_LLM"],
        )]

    elif omml_xml:
        latex = _from_omml(omml_xml)
        if not latex or _is_bad_formula_candidate(latex):
            return []
        domain = _guess_domain(latex, context)
        expl = _short_explanation(FormulaSource.OMML, domain)
        results = [FormulaResult(
            source=FormulaSource.OMML,
            domain=domain,
            latex=latex,
            explanation=expl,
            rag_chunk=_chunk(latex, expl, domain, FormulaSource.OMML, ExtractionQuality.HEURISTIC, page_number, 0.70),
            quality=ExtractionQuality.HEURISTIC,
            confidence=0.70,
            page_number=page_number,
            tags=["FORMULA:OMML", "NO_LLM"],
        )]

    elif excel_formula:
        latex = _from_excel(excel_formula)
        if not latex or _is_bad_formula_candidate(latex):
            return []
        domain = FormulaDomain.MATHEMATIQUES
        expl = f"Formule Excel extraite : {excel_formula}"
        results = [FormulaResult(
            source=FormulaSource.EXCEL,
            domain=domain,
            latex=latex,
            explanation=expl,
            rag_chunk=_chunk(latex, expl, domain, FormulaSource.EXCEL, ExtractionQuality.HEURISTIC, page_number, 0.65),
            quality=ExtractionQuality.HEURISTIC,
            confidence=0.65,
            page_number=page_number,
            tags=["FORMULA:EXCEL", "NO_LLM"],
        )]

    elif text:
        has_formula, score = _detect(text, context)
        logger.debug("Détection formule fast : score=%.2f has=%s mode=%s", score, has_formula, mode)
        if not has_formula and not force:
            return []
        results = _heuristic_extract(text, page_number, FormulaSource.TEXT_NATIVE, context=context, confidence=max(score, 0.50))

    if mode == "explain":
        results = [_explain_with_llm(r, context=context) for r in results]

    return results[:MAX_FORMULAS]


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    tests = [
        "Complexité O(n log n) pour le tri fusion",
        "La fonction de perte est L = - sum y log(p)",
        "La loi fondamentale : F = m·a où F est la force en N",
        "EvoSuite atteint 90 % de compilabilité avec couverture inférieure à 32 %",
        "Le projet a été réalisé en 2024.",
    ]
    for t in tests:
        print("\n---", t)
        for f in extract_formulas(text=t):
            print(f.rag_chunk)
