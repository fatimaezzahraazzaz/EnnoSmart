"""
modules/extraction/formula/formula.py
──────────────────────────────────────────────────────────────────────────────
Détection, extraction et explication des formules R&D / CIR.

Pipeline :
  1. Détection légère (regex unicode universels, ~1ms)
  2. LLM Mistral via Ollama SDK → JSON {latex, explanation, domain}
  3. Fallback heuristique si LLM indisponible

"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)

DETECTION_THRESHOLD  = 0.25
MAX_FORMULAS         = 8


# ── Enums ─────────────────────────────────────────────────────────────────────

class FormulaDomain(str, Enum):
    MECANIQUE     = "mécanique"
    CHIMIE        = "chimie"
    MATHEMATIQUES = "mathématiques"
    INFORMATIQUE  = "informatique"
    PHYSIQUE      = "physique"
    ELECTRONIQUE  = "électronique"
    BIOLOGIE      = "biologie"
    STATISTIQUES  = "statistiques"
    INCONNU       = "inconnu"

class FormulaSource(str, Enum):
    TEXT_NATIVE = "text_native"
    IMAGE       = "image"
    OMML        = "omml"
    EXCEL       = "excel"

class ExtractionQuality(str, Enum):
    LLM       = "llm"
    PIX2TEX   = "pix2tex"
    HEURISTIC = "heuristic"
    FAILED    = "failed"


@dataclass
class FormulaResult:
    source:      FormulaSource
    domain:      FormulaDomain
    latex:       str
    explanation: str
    rag_chunk:   str
    quality:     ExtractionQuality
    confidence:  float
    page_number: Optional[int] = None
    tags:        list[str]     = field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════════
# DÉTECTION — regex universels couvrant tous les symboles unicode
# ══════════════════════════════════════════════════════════════════════════════

_UNICODE_MATH = re.compile(
    r"[\u0391-\u03C9"    # Grec Α-Ω α-ω
    r"\u2200-\u22FF"     # Opérateurs math (∑∏∫∂∇∞≤≥≠±×...)
    r"\u2100-\u214F"     # Lettre-like (ℝℂ...)
    r"\u2190-\u21FF"     # Flèches (→⇌...)
    r"\u00B2\u00B3\u00B9"
    r"\u2080-\u2089]"    # Indices ₀₁₂...
)

_EQ_PATTERNS = [
    re.compile(r"\b[A-Za-zΑ-Ωα-ω]\w*\s*=\s*[A-Za-zΑ-Ωα-ω0-9\(\)\+\-\*/\^\.\\]"),
    re.compile(r"[A-Za-z0-9][²³¹\^_]"),
    re.compile(r"\b(?:sin|cos|tan|exp|log|ln|lim|det|grad|div)\s*[\(\[{]", re.I),
    re.compile(r"\\[a-zA-Z]{2,}|\$[^$]{2,}\$"),
    re.compile(r"\d+\.?\d*\s*(?:kg|m/s²?|N|Pa|J|W|Hz|mol|K|°C|rad|V|A|Ω)\b"),
    re.compile(r"\b[A-Z][a-z]?\d+(?:[A-Z][a-z]?\d*)+\b"),
    re.compile(r"[→⇌⇒⇔]"),
]


import unicodedata
import re

def _detect(text: str, context: str = "") -> tuple[bool, float]:
    """
    Détection universelle et robuste de formules R&D.
    Utilise la normalisation NFKC pour aplatir les variantes de styles Unicode
    et des patterns structurels pour détecter Variable = Valeur [Unité].
    """
    if not text:
        return False, 0.0

    # 1. Normalisation NFKC (Crucial : transforme 𝜈 en ν, 𝝆 en ρ, etc.)
    t_norm = unicodedata.normalize('NFKC', text)
    
    # 2. Ignorer les chunks qui sont majoritairement des tableaux Markdown
    lines = [l.strip() for l in t_norm.split("\n") if l.strip()]
    if lines:
        table_lines = sum(1 for l in lines if l.startswith("|"))
        if table_lines / len(lines) > 0.5:
            return False, 0.0

    # 3. Pattern STRUCTUREL Universel (Variable = Valeur)
    # - Variable : 1 à 3 lettres grecques (\u0370-\u03ff) ou romaines
    # - Opérateur : =, ≈, ~, ± ou :
    # - Valeur : Chiffre (avec gestion notation scientifique 1.2e-3)
    assignment_pattern = re.compile(
        r'(?:[\u0370-\u03ff]|[a-zA-Z]){1,3}\s*[=≈~±:]\s*[\d.,]+(?:[eE][-+]?\d+)?',
        re.U
    )

    # 4. Pattern de Grandeurs Physiques (Nombre + Unité R&D)
    # Détecte "1670 Hz", "10 N", etc. sans forcément de signe "="
    unit_pattern = re.compile(
        r'[\d.,]+\s*(?:Hz|N|Pa|GPa|MPa|kPa|kg|m/s|mm|rad|°C|V|A|Ω|%)',
        re.I
    )

    # 5. Calcul du score
    score = 0.0
    
    # A. Bonus Structure (Assignation technique)
    if assignment_pattern.search(t_norm):
        score += 0.55
    
    # B. Bonus Unités physiques
    if unit_pattern.search(t_norm):
        score += 0.40

    # C. Comptage des symboles mathématiques et grecs (Plages Unicode)
    # \u0370-\u03ff : Grec et Copte
    # \u2200-\u22ff : Opérateurs mathématiques (∑, ∏, ∫, √, etc.)
    math_hits = len(re.findall(r'[\u0370-\u03ff\u2200-\u22ff]', t_norm))
    if math_hits > 0:
        score += min(math_hits * 0.15, 0.50)

    # D. Présence de patterns d'équations classiques (exposants, parenthèses complexes)
    if re.search(r'[²³¹\^_]|[\(\[].*[\)\]]', t_norm):
        score += 0.15

    # 6. Décision finale
    # On garde ton seuil DETECTION_THRESHOLD (généralement 0.25)
    is_formula = score >= 0.25
    return is_formula, min(round(score, 2), 1.0)
# ══════════════════════════════════════════════════════════════════════════════
# LLM — appel direct Ollama SDK (contourne import modules/)
# ══════════════════════════════════════════════════════════════════════════════

_LLM_SYSTEM = """Tu es un expert en mécanique et physique pour dossiers R&D / CIR.
Extrais TOUTES les formules du texte. Réponds UNIQUEMENT avec ce JSON :
{
  "formulas": [
    {
      "latex": "<LaTeX standard>",
      "explanation": "<Rôle, variables avec unités SI, domaine R&D>",
      "domain": "<mécanique|physique|...>",
      "confidence": <0.0-1.0>
    }
  ]
}

RÈGLES CRITIQUES D'INTERPRÉTATION :
1. COEFFICIENT DE POISSON : Si tu vois 'v', 'nu', 'ϑ' ou '𝜈' associé à une valeur entre 0 et 0.5 sans unité (ex: v=0.35), c'est TOUJOURS le "Coefficient de Poisson". Ce n'est JAMAIS un diamètre (d) ni une vitesse.
2. UNITÉS : Ne transforme jamais une unité physique en fraction algébrique. "1 mm" reste "1 mm" ou "10^{-3} m", pas "\\frac{1}{mm}".
3. MODULE D'YOUNG : 'E' associé à GPa ou MPa est le Module d'Young.
4. Si 0 formule → {"formulas": []}. JSON uniquement."""

_LLM_USER = 'Texte :\n"""\n{text}\n"""\nContexte : {context}\nJSON uniquement.'

# Modèles préférés dans l'ordre
_PREFERRED_MODELS = [
    "mistral:7b-instruct",
    "mistral:7b",
    "mistral",
    "llama3.1:8b",
    "llama3.2-vision:latest",
]

# Cache du modèle résolu (évite de lister les modèles à chaque appel)
_resolved_model: Optional[str] = None


def _resolve_model() -> Optional[str]:
    """Trouve le meilleur modèle disponible dans Ollama."""
    global _resolved_model
    if _resolved_model:
        return _resolved_model
    try:
        import ollama as sdk
        available = [m.model for m in sdk.list().models]
        for preferred in _PREFERRED_MODELS:
            if any(preferred in name for name in available):
                _resolved_model = next(n for n in available if preferred in n)
                logger.debug("Modèle LLM sélectionné : %s", _resolved_model)
                return _resolved_model
        if available:
            _resolved_model = available[0]
            logger.warning("Mistral non trouvé, utilisation de %s", _resolved_model)
            return _resolved_model
    except Exception as exc:
        logger.debug("Ollama indisponible : %s", exc)
    return None


def _call_llm(prompt_text: str, context: str) -> Optional[dict]:
    """Appel Ollama SDK direct — pas de dépendance sur modules/llm_gateway."""
    model = _resolve_model()
    if not model:
        return None
    try:
        import ollama as sdk
        response = sdk.chat(
            model=model,
            messages=[
                {"role": "system", "content": _LLM_SYSTEM},
                {"role": "user",   "content": _LLM_USER.format(
                    text=prompt_text[:3000],
                    context=context[:200] or "document R&D",
                )},
            ],
            options={"temperature": 0.05, "num_predict": 600},
        )
        raw = response.message.content.strip()
        return _parse_json(raw)
    except ImportError:
        logger.debug("SDK ollama non installé")
        return None
    except Exception as exc:
        logger.warning("Erreur LLM : %s", exc)
        return None


def _parse_json(raw: str) -> Optional[dict]:
    for text in (raw, re.sub(r"```(?:json)?|```", "", raw).strip()):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


def _llm_extract(
    text: str, context: str,
    page_number: Optional[int], source: FormulaSource,
) -> list[FormulaResult]:
    """
    Appelle le LLM pour extraire les formules et élimine les doublons 
    au sein d'un même chunk de texte.
    """
    data = _call_llm(text, context)
    if not data:
        return []

    results = []
    # Set pour suivre les formules déjà vues dans ce traitement
    seen_latex = set()

    for f in (data.get("formulas") or [])[:MAX_FORMULAS]:
        latex = (f.get("latex") or "").strip()
        
        if not latex:
            continue

        # --- DÉDUPLICATION ---
        # On normalise la chaîne (pas d'espaces, minuscule) pour comparer le sens
        # Cela évite d'avoir "F = 1670 Hz" et "F=1670Hz" en double
        normalized_latex = latex.replace(" ", "").lower()
        
        if normalized_latex in seen_latex:
            logger.debug(f"Doublon de formule ignoré : {latex}")
            continue
            
        seen_latex.add(normalized_latex)
        # ---------------------

        expl   = (f.get("explanation") or "").strip()
        domain = _map_domain(f.get("domain", "inconnu"))
        conf   = float(f.get("confidence", 0.75))

        results.append(FormulaResult(
            source=source, 
            domain=domain, 
            latex=latex, 
            explanation=expl,
            rag_chunk=_chunk(latex, expl, domain, source, ExtractionQuality.LLM, page_number, conf),
            quality=ExtractionQuality.LLM, 
            confidence=conf,
            page_number=page_number,
            tags=[f"FORMULA:LLM", f"DOMAIN:{domain.value.upper()}"],
        ))
        
    return results


def _map_domain(s: str) -> FormulaDomain:
    m = {
        "mécanique": FormulaDomain.MECANIQUE, "mecanique": FormulaDomain.MECANIQUE,
        "chimie": FormulaDomain.CHIMIE,
        "mathématiques": FormulaDomain.MATHEMATIQUES, "mathematiques": FormulaDomain.MATHEMATIQUES,
        "math": FormulaDomain.MATHEMATIQUES,
        "informatique": FormulaDomain.INFORMATIQUE, "physique": FormulaDomain.PHYSIQUE,
        "électronique": FormulaDomain.ELECTRONIQUE, "electronique": FormulaDomain.ELECTRONIQUE,
        "biologie": FormulaDomain.BIOLOGIE, "statistiques": FormulaDomain.STATISTIQUES,
    }
    return m.get(s.lower(), FormulaDomain.INCONNU)


# ══════════════════════════════════════════════════════════════════════════════
# CONVERSION unicode → LaTeX (fallback heuristique)
# ══════════════════════════════════════════════════════════════════════════════

# Substitutions unicode → commande LaTeX
# Clé = caractère unicode, valeur = commande LaTeX SANS espace
# L'espace sera ajoutée intelligemment après substitution
_U2L: dict[str, str] = {
    "ρ": "\\rho",     # Rho standard
    "ϱ": "\\rho",     # Variante Rho
    "h o": "\\rho",   # AUTO-FIX : si le parser a déjà fait l'erreur "h o"
    "θ": "\\theta",
    "ϑ": "\\nu",      # Ton fix pour Poisson
    "ν": "\\nu",
    # Grec minuscule
    "α":"\\alpha","β":"\\beta","γ":"\\gamma","δ":"\\delta","ε":"\\epsilon",
    "ζ":"\\zeta","η":"\\eta","θ":"\\theta","ι":"\\iota","κ":"\\kappa",
    "λ":"\\lambda","μ":"\\mu","ν":"\\nu","ξ":"\\xi","π":"\\pi",
    "ρ":"\\rho","σ":"\\sigma","τ":"\\tau","υ":"\\upsilon","φ":"\\phi",
    "χ":"\\chi","ψ":"\\psi","ω":"\\omega",
    # Grec majuscule
    "Γ":"\\Gamma","Δ":"\\Delta","Θ":"\\Theta","Λ":"\\Lambda","Ξ":"\\Xi",
    "Π":"\\Pi","Σ":"\\Sigma","Υ":"\\Upsilon","Φ":"\\Phi","Ψ":"\\Psi","Ω":"\\Omega",
    # Opérateurs (pas d'espace nécessaire après ceux-ci)
    "∑":"\\sum ","∏":"\\prod ","∫":"\\int ","∂":"\\partial ","∇":"\\nabla ",
    "∞":"\\infty","√":"\\sqrt","∀":"\\forall ","∃":"\\exists ",
    "∈":"\\in","∉":"\\notin","⊂":"\\subset","⊃":"\\supset",
    "∩":"\\cap","∪":"\\cup","≤":"\\leq","≥":"\\geq","≠":"\\neq",
    "≈":"\\approx","≡":"\\equiv","∝":"\\propto","±":"\\pm","∓":"\\mp",
    "×":"\\times","÷":"\\div","·":"\\cdot","⊗":"\\otimes","⊕":"\\oplus",
    "→":"\\rightarrow","←":"\\leftarrow","↔":"\\leftrightarrow",
    "⇒":"\\Rightarrow","⇔":"\\Leftrightarrow","⇌":"\\rightleftharpoons",
    # Exposants/indices unicode
    "²":"^{2}","³":"^{3}","¹":"^{1}","⁰":"^{0}",
    "₀":"_{0}","₁":"_{1}","₂":"_{2}","₃":"_{3}","₄":"_{4}",
    "₅":"_{5}","₆":"_{6}","₇":"_{7}","₈":"_{8}","₉":"_{9}",
}

# Commandes LaTeX alphabétiques qui doivent être séparées du token suivant
# si ce token commence par une lettre ou un chiffre

_ALPHA_CMD = re.compile(r"(\\[a-zA-Z]+)(?![^a-zA-Z])([a-zA-Z0-9])")
import unicodedata
import unicodedata
import re

# ── Normalisation unicode mathématique (OMML) ─────────────────────────────────
_MATH_ITALIC_MAP: dict[str, str] = {}
for _i, _c in enumerate('abcdefghijklmnopqrstuvwxyz'):
    _MATH_ITALIC_MAP[chr(0x1D44E + _i)] = _c   # 𝑎-𝑧
for _i, _c in enumerate('ABCDEFGHIJKLMNOPQRSTUVWXYZ'):
    _MATH_ITALIC_MAP[chr(0x1D434 + _i)] = _c   # 𝐴-𝑍
for _i, _c in enumerate('αβγδεζηθικλμνξοπρστυφχψω'):
    _MATH_ITALIC_MAP[chr(0x1D6FC + _i)] = _c   # 𝛼-𝜔
_MATH_ITALIC_MAP.update({
    '\U0001D717': 'ϑ',   # theta variant = coeff Poisson
    '\U0001D70C': 'ρ',   # rho italique
    '\U0001D70F': 'τ',   # tau italique
    '\U0001D708': 'ν',   # nu italique
    '\U0001D463': 'v',   # v italique
    '−': '-',             # signe moins OMML
})

def _normalize_math_unicode(text: str) -> str:
    """Convertit les caractères unicode mathématiques italiques OMML → ASCII/grec standard."""
    return ''.join(_MATH_ITALIC_MAP.get(c, c) for c in text)


def _to_latex(text: str) -> str:
    """
    Conversion texte -> LaTeX robuste pour EnnoSmart v4.0.
    Gère la normalisation Unicode, les erreurs de lecture (h o) et 
    le "recollage" des commandes LaTeX splitées.
    """
    t = text.strip()
    if not t:
        return ""

    # ÉTAPE A : Normalisation NFKC 
    # (Écrase les styles gras/italiques mathématiques 𝜈 -> ν)
    t = unicodedata.normalize('NFKC', t)

    # ÉTAPE B : Correctif spécifique "h o" -> Rho
    # On le fait AVANT le dictionnaire pour être sûr de l'intercepter
    t = t.replace("h o", "ρ")

    # ÉTAPE 1 : Substitution unicode -> commande LaTeX (via ton dictionnaire _U2L)
    # Assure-toi que ton dictionnaire contient : "ϑ": "\\nu" et "ρ": "\\rho"
    for char, cmd in _U2L.items():
        t = t.replace(char, cmd)

    # ÉTAPE 2 : Ajout d'espace intelligent entre commande et texte
    # On utilise une regex qui vérifie que le caractère suivant n'est pas déjà une commande
    t = _ALPHA_CMD.sub(r"\1 \2", t)

    # ÉTAPE 3 : RECOLLAGE (Anti-hallucination)
    # Si le parser a généré "\thet a" ou "\epsilo n", on recolle les morceaux
    # Cette regex cherche une commande LaTeX suivie d'un espace et d'une seule lettre
    t = re.sub(r'\\([a-z]{3,})\s+([a-z])\b', r'\\\1\2', t)

    # ÉTAPE 4 : Règles structurelles (exposants et indices)
    # On utilise des parenthèses protectrices pour le LaTeX ^{ }
    t = re.sub(r"(\w)\^([^{\s])", r"\1^{\2}", t)
    t = re.sub(r"(\w)_([^{_\s])", r"\1_{\2}", t)

    # ÉTAPE 5 : Fractions simples
    t = re.sub(r"(?<!\w)(½)(?!\w)", r"\\frac{1}{2}", t)
    t = re.sub(r"(?<!\w)(⅓)(?!\w)", r"\\frac{1}{3}", t)
    t = re.sub(r"(?<!\w)(¼)(?!\w)", r"\\frac{1}{4}", t)

    # ÉTAPE 6 : Nettoyage final des espaces
    t = re.sub(r" {2,}", " ", t).strip()

    return t
def _heuristic_extract(
    text: str, page_number: Optional[int], source: FormulaSource,
) -> list[FormulaResult]:
    """
    Fallback : groupe les lignes mathématiques et déduplique les résultats.
    """
    groups: list[list[str]] = []
    current: list[str] = []

    # Regroupement des lignes mathématiques consécutives
    for line in text.split("\n"):
        s = line.strip()
        # On utilise ton regex _UNICODE_MATH et tes patterns d'équations
        is_math = bool(
            _UNICODE_MATH.search(s) or
            any(p.search(s) for p in _EQ_PATTERNS[:4])
        )
        if is_math and s:
            current.append(s)
        elif current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)

    # Déduplication basée sur le LaTeX normalisé
    seen_hashes: set[str] = set()
    results: list[FormulaResult] = []
    
    expl = "Formule mathématique extraite automatiquement."

    for group in groups[:MAX_FORMULAS]:
        # Conversion via la nouvelle fonction _to_latex
        latex_raw = _to_latex("\n".join(group))
        
        # Normalisation pour la déduplication (minuscule, sans espaces)
        formula_hash = latex_raw.replace(" ", "").lower()
        
        if not latex_raw or formula_hash in seen_hashes:
            continue
            
        seen_hashes.add(formula_hash)
        
        results.append(FormulaResult(
            source=source, 
            domain=FormulaDomain.INCONNU,
            latex=latex_raw, 
            explanation=expl,
            rag_chunk=_chunk(latex_raw, expl, FormulaDomain.INCONNU, source,
                             ExtractionQuality.HEURISTIC, page_number, 0.35),
            quality=ExtractionQuality.HEURISTIC, 
            confidence=0.35,
            page_number=page_number,
            tags=["FORMULA:HEURISTIC", "NEEDS_REVIEW"]
        )) 
    return results
# ══════════════════════════════════════════════════════════════════════════════
# SOURCES SPÉCIALES
# ══════════════════════════════════════════════════════════════════════════════

def _from_image(image_bytes: bytes) -> tuple[str, float]:
    try:
        from pix2tex.cli import LatexOCR
        from PIL import Image
        import io
        latex = LatexOCR()(Image.open(io.BytesIO(image_bytes)).convert("RGB"))
        return (latex.strip(), 0.85) if latex else ("", 0.0)
    except ImportError:
        logger.warning("pix2tex non installé : pip install pix2tex")
        return "", 0.0
    except Exception as exc:
        logger.warning("pix2tex : %s", exc)
        return "", 0.0


def _from_omml(xml: str) -> str:
    """
    Parseur OMML récursif : fractions <f>, exposants <sSup>,
    indices <sSub>, radicaux <rad>, exposant+indice <sSubSup>.
    Normalise les caractères unicode mathématiques italiques.
    """
    try:
        import xml.etree.ElementTree as ET
        import unicodedata as _ud
        ns = "http://schemas.openxmlformats.org/officeDocument/2006/math"

        def _norm(t: str) -> str:
            t2 = _ud.normalize("NFKC", (t or "").replace("\xa0", ""))
            return _normalize_math_unicode(t2).strip()

        def _pc(el) -> str:
            if el is None:
                return ""
            parts = []
            if el.text and el.text.strip():
                parts.append(_norm(el.text))
            for child in el:
                parts.append(_parse(child))
            return "".join(parts)

        def _parse(el) -> str:
            tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
            if tag == "t":
                return _norm((el.text or "").replace("\xa0", ""))
            if tag == "f":
                num = _pc(el.find(f"{{{ns}}}num"))
                den = _pc(el.find(f"{{{ns}}}den"))
                return f"\\frac{{{num}}}{{{den}}}" if (num and den) else f"{num}/{den}"
            if tag == "sSup":
                base = _pc(el.find(f"{{{ns}}}e"))
                exp  = _pc(el.find(f"{{{ns}}}sup"))
                return f"{base}^{{{exp}}}" if exp else base
            if tag == "sSub":
                base = _pc(el.find(f"{{{ns}}}e"))
                sub  = _pc(el.find(f"{{{ns}}}sub"))
                return f"{base}_{{{sub}}}" if sub else base
            if tag == "sSubSup":
                base = _pc(el.find(f"{{{ns}}}e"))
                sub  = _pc(el.find(f"{{{ns}}}sub"))
                sup  = _pc(el.find(f"{{{ns}}}sup"))
                return f"{base}_{{{sub}}}^{{{sup}}}"
            if tag == "rad":
                base = _pc(el.find(f"{{{ns}}}e"))
                deg  = _pc(el.find(f"{{{ns}}}deg"))
                return f"\\sqrt[{deg}]{{{base}}}" if deg.strip() else f"\\sqrt{{{base}}}"
            return _pc(el)

        result = _pc(ET.fromstring(xml))
        # =− → = - (signe OMML)
        result = result.replace("=−", "= -").replace("=\u2212", "= -").strip()
        return result
    except Exception:
        return ""


def _from_excel(formula: str) -> str:
    if not formula.startswith("="):
        return formula
    mapping = {
        "SUM(": "\\sum(", "SQRT(": "\\sqrt{", "ABS(": "|",
        "EXP(": "e^{",    "LOG(": "\\log(",   "LN(": "\\ln(",
        "SIN(": "\\sin(", "COS(": "\\cos(",   "TAN(": "\\tan(",
        "PI()": "\\pi",   "AVERAGE(": "\\bar{x}=\\frac{1}{n}\\sum(",
        "STDEV(": "\\sigma(", "VAR(": "\\sigma^{2}(",
        "MAX(": "\\max(", "MIN(": "\\min(", "*": "\\cdot ",
    }
    t = formula[1:]
    for k, v in mapping.items():
        t = t.replace(k, v)
    return t


# ══════════════════════════════════════════════════════════════════════════════
# CHUNK RAG
# ══════════════════════════════════════════════════════════════════════════════

def _chunk(
    latex: str, explanation: str, domain: FormulaDomain,
    source: FormulaSource, quality: ExtractionQuality,
    page_number: Optional[int], confidence: float,
) -> str:
    page = f" | PAGE {page_number}" if page_number else ""
    return (
        f"[FORMULE | {domain.value} | {source.value}{page}]\n"
        f"[QUALITÉ: {quality.value} | confiance: {confidence:.2f}]\n"
        f"LaTeX       : {latex}\n"
        f"Explication : {explanation}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# POINT D'ENTRÉE UNIQUE
# ══════════════════════════════════════════════════════════════════════════════

def extract_formulas(
    text:          Optional[str]   = None,
    image_bytes:   Optional[bytes] = None,
    omml_xml:      Optional[str]   = None,
    excel_formula: Optional[str]   = None,
    context:       str             = "",
    page_number:   Optional[int]   = None,
    force:         bool            = False,
) -> list[FormulaResult]:
    """
    Détecte et extrait toutes les formules d'un contenu R&D.

    - Détection : regex universels unicode (~1ms, zéro LLM)
    - Extraction : LLM Mistral via Ollama SDK → JSON
    - Multi-ligne : 1 formule sur N lignes = 1 FormulaResult
    - Fallback heuristique si Ollama indisponible
    """
    # ── Image ──────────────────────────────────────────────────────────────
    if image_bytes:
        latex, conf = _from_image(image_bytes)
        if not latex:
            return []
        results = _llm_extract(f"LaTeX : {latex}", "image OCR", page_number, FormulaSource.IMAGE)
        if not results:
            expl = "Formule extraite par OCR image."
            results = [FormulaResult(
                source=FormulaSource.IMAGE, domain=FormulaDomain.INCONNU,
                latex=latex, explanation=expl,
                rag_chunk=_chunk(latex, expl, FormulaDomain.INCONNU,
                                 FormulaSource.IMAGE, ExtractionQuality.PIX2TEX, page_number, conf),
                quality=ExtractionQuality.PIX2TEX, confidence=conf,
                page_number=page_number, tags=["FORMULA:IMAGE"],
            )]
        return results

    # ── OMML ───────────────────────────────────────────────────────────────
    if omml_xml:
        latex = _from_omml(omml_xml)
        if not latex:
            return []
        results = _llm_extract(f"Formule : {latex}", context, page_number, FormulaSource.OMML)
        if not results:
            expl = "Équation Word/PowerPoint extraite."
            results = [FormulaResult(
                source=FormulaSource.OMML, domain=FormulaDomain.INCONNU,
                latex=latex, explanation=expl,
                rag_chunk=_chunk(latex, expl, FormulaDomain.INCONNU,
                                 FormulaSource.OMML, ExtractionQuality.HEURISTIC, page_number, 0.65),
                quality=ExtractionQuality.HEURISTIC, confidence=0.65,
                page_number=page_number, tags=["FORMULA:OMML"],
            )]
        return results

    # ── Excel ──────────────────────────────────────────────────────────────
    if excel_formula:
        latex = _from_excel(excel_formula)
        results = _llm_extract(
            f"Formule Excel : {excel_formula}\nNotation math : {latex}",
            context, page_number, FormulaSource.EXCEL,
        )
        if not results:
            expl = f"Formule Excel : {excel_formula}"
            results = [FormulaResult(
                source=FormulaSource.EXCEL, domain=FormulaDomain.MATHEMATIQUES,
                latex=latex, explanation=expl,
                rag_chunk=_chunk(latex, expl, FormulaDomain.MATHEMATIQUES,
                                 FormulaSource.EXCEL, ExtractionQuality.HEURISTIC, page_number, 0.55),
                quality=ExtractionQuality.HEURISTIC, confidence=0.55,
                page_number=page_number, tags=["FORMULA:EXCEL"],
            )]
        return results

    # ── Texte natif ────────────────────────────────────────────────────────
    if not text:
        return []

    has_formula, score = _detect(text, context)
    logger.debug("Détection formule : score=%.2f has=%s", score, has_formula)

    if not has_formula and not force:
        return []

    results = _llm_extract(text, context, page_number, FormulaSource.TEXT_NATIVE)
    if not results:
        results = _heuristic_extract(text, page_number, FormulaSource.TEXT_NATIVE)

    return results


# ── Debug / tests ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.DEBUG)

    tests = [
        ("mécanique",  "La loi fondamentale : F = m·a où F est la force en N"),
        ("chimie",     "Réaction : H₂O → H₂ + ½O₂  ΔH = +285 kJ/mol"),
        ("physique",   "E = mc² représente l'énergie de masse au repos"),
        ("math",       "∂f/∂x = lim_{h→0} [f(x+h)-f(x)]/h"),
        ("stats ρ",    "ρ = Σ(xi-x̄)(yi-ȳ) / √[Σ(xi-x̄)²·Σ(yi-ȳ)²]"),
        ("multi-line", "Système :\ndx/dt = αx - βxy\ndy/dt = δxy - γy"),
        ("info",       "Complexité O(n log n) pour le tri fusion"),
        ("rien",       "Le projet a été réalisé en 2023 par l'équipe."),
        ("excel",      None),
    ]

    if len(sys.argv) > 1:
        t = open(sys.argv[1], encoding="utf-8").read()
        for fr in extract_formulas(text=t):
            print(fr.rag_chunk)
    else:
        for name, t in tests:
            r = (
                extract_formulas(excel_formula="=SQRT(SUM((B2:B10-AVERAGE(B2:B10))^2)/COUNT(B2:B10))")
                if name == "excel" else extract_formulas(text=t)
            )
            print(f"\n{'─'*60}\nTest [{name}] → {len(r)} formule(s)")
            for fr in r:
                print(f"  Domaine : {fr.domain.value} | Qualité : {fr.quality.value}")
                print(f"  LaTeX   : {fr.latex}")
                print(f"\n{fr.rag_chunk}")