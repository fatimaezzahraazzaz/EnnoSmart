"""
modules/NLP/domain_classifier.py — NLP V7.2.0

Changements V7.2.0 vs V7.1.x (apports V8.1) :
- NOUVEAU : extraction du domaine applicatif (domaine_applicatif).
  V8.1 renvoyait bien "Motorisation électrique / véhicules électriques / naval de défense"
  là où V7 ne renvoyait rien. Ce champ est maintenant extrait via le LLM
  en même temps que le domaine scientifique.
- NOUVEAU : domaine_scientifique_detaille — libellé plus précis que domaine_principal.
  V8.1 renvoyait "Matériaux / Vibroacoustique / Mécanique des structures"
  là où V7 renvoyait "Mécanique, Génie mécanique, Génie civil [B4]" (trop large).
- La classification officielle MESR (domains.json, codes niv1/niv2/niv3) est conservée.
- Tout le reste de la logique V7 est inchangé.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

try:
    import ollama
except ImportError:
    ollama = None

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

try:
    import openpyxl
except ImportError:
    openpyxl = None

logger = logging.getLogger(__name__)

DEFAULT_LLM_MODEL = "ollama:qwen3:4b-instruct"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
LOCAL_MODEL_PREFIXES = ("ollama:", "local:")
TIMEOUT_SECONDS = 60
MAX_RETRIES = 1

DEFAULT_DOMAINS_PATH = Path(__file__).parent / "data" / "domains.json"



def _norm_space(text: Any) -> str:
    text = str(text or "").replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _domain_entry_to_dict(entry: Any) -> Optional[dict]:
    """
    Normalise une entrée de référentiel domaine.

    Pourquoi :
    - certains domains.json contiennent des listes de strings ou des formats mixtes ;
    - l'ancien code faisait entry.get(...), donc plantait avec :
      "'str' object has no attribute 'get'".
    """
    if isinstance(entry, dict):
        return entry
    return None


def _iter_domain_entries(domains: Optional[dict], level: str = "niv2") -> list[dict]:
    """
    Retourne uniquement des dicts exploitables, quel que soit le format interne :
    - list[dict]
    - dict[str, dict]
    - list[str] ignorée proprement
    """
    if not isinstance(domains, dict):
        return []

    raw = domains.get(level, [])
    if isinstance(raw, dict):
        raw = list(raw.values())
    if not isinstance(raw, list):
        return []

    out: list[dict] = []
    for entry in raw:
        d = _domain_entry_to_dict(entry)
        if d:
            out.append(d)
    return out


def _dedupe_domain_entries(entries: list[dict], code_key: str, label_key: str) -> list[dict]:
    out: list[dict] = []
    seen = set()
    for e in entries:
        code = _norm_space(e.get(code_key, "")).upper()
        label = _norm_space(e.get(label_key, ""))
        if not code or not label:
            continue
        key = (code, _norm_space(label).lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def _load_domains_from_xlsx(path: Path) -> Optional[dict]:
    """
    Charge directement l'Excel MESR :
    colonnes attendues :
      code1, DOMAINES niv1, code2, Sous-domaines niv2,
      code3, SECTION niv3, code4, Sous-sections niv4
    """
    if openpyxl is None:
        logger.warning("openpyxl non installé : impossible de lire le référentiel Excel %s", path)
        return None

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active

    niv1_map: dict[str, dict] = {}
    niv2_map: dict[str, dict] = {}
    niv3_map: dict[str, dict] = {}
    niv4_map: dict[str, dict] = {}

    rows = ws.iter_rows(values_only=True)
    headers = next(rows, None)

    for row in rows:
        if not row:
            continue
        code1 = _norm_space(row[0] if len(row) > 0 else "")
        lab1 = _norm_space(row[1] if len(row) > 1 else "")
        code2 = _norm_space(row[2] if len(row) > 2 else "")
        lab2 = _norm_space(row[3] if len(row) > 3 else "")
        code3 = _norm_space(row[4] if len(row) > 4 else "")
        lab3 = _norm_space(row[5] if len(row) > 5 else "")
        code4 = _norm_space(row[6] if len(row) > 6 else "")
        lab4 = _norm_space(row[7] if len(row) > 7 else "")

        if code1 and lab1 and code1 not in niv1_map:
            niv1_map[code1] = {"code_niv1": code1, "label_niv1": lab1}

        if code2 and lab2 and code2 not in niv2_map:
            niv2_map[code2] = {
                "code_niv1": code1,
                "label_niv1": lab1,
                "code_niv2": code2,
                "label_niv2": lab2,
            }

        if code3 and lab3 and code3 not in niv3_map:
            niv3_map[code3] = {
                "code_niv1": code1,
                "label_niv1": lab1,
                "code_niv2": code2,
                "label_niv2": lab2,
                "code_niv3": code3,
                "label_niv3": lab3,
            }

        if code4 and lab4 and code4 not in niv4_map:
            niv4_map[code4] = {
                "code_niv1": code1,
                "label_niv1": lab1,
                "code_niv2": code2,
                "label_niv2": lab2,
                "code_niv3": code3,
                "label_niv3": lab3,
                "code_niv4": code4,
                "label_niv4": lab4,
            }

    data = {
        "niv1": list(niv1_map.values()),
        "niv2": list(niv2_map.values()),
        "niv3": list(niv3_map.values()),
        "niv4": list(niv4_map.values()),
    }
    logger.info(
        "Référentiel Excel chargé : %d niv1, %d niv2, %d niv3, %d niv4",
        len(data["niv1"]), len(data["niv2"]), len(data["niv3"]), len(data["niv4"]),
    )
    return data


# ══════════════════════════════════════════════════════════════════════════════
# CHARGEMENT DU RÉFÉRENTIEL
# ══════════════════════════════════════════════════════════════════════════════

_DOMAINS_CACHE: Optional[dict] = None


def load_domains(path: str | Path | None = None) -> Optional[dict]:
    global _DOMAINS_CACHE
    if _DOMAINS_CACHE is not None and path is None:
        return _DOMAINS_CACHE

    p = Path(path) if path else DEFAULT_DOMAINS_PATH
    if not p.exists():
        for alt in [
            Path(__file__).parent / "domains.json",
            Path("domains.json"),
            Path(__file__).parent / "data" / "nomenclature-scientifique-de-domaines-de-recherche--38201.xlsx",
            Path("nomenclature-scientifique-de-domaines-de-recherche--38201.xlsx"),
        ]:
            if alt.exists():
                p = alt
                break

    if not p.exists():
        logger.warning(
            "Référentiel domaines introuvable (%s). La classification de domaine sera désactivée.", p,
        )
        return None

    try:
        if p.suffix.lower() in {".xlsx", ".xlsm"}:
            data = _load_domains_from_xlsx(p)
        else:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)

        if not isinstance(data, dict):
            logger.warning("Référentiel domaines mal formé : racine non-dict.")
            return None

        # Normalisation défensive : aucune entrée string ne doit arriver jusqu'à entry.get(...)
        for level in ("niv1", "niv2", "niv3", "niv4"):
            if level in data:
                data[level] = _iter_domain_entries(data, level)

        if "niv1" not in data or "niv2" not in data:
            logger.warning("Référentiel domaines mal formé : clés niv1/niv2 manquantes.")
            return None

        if not data.get("niv2"):
            logger.warning("Référentiel domaines mal formé : niv2 vide.")
            return None

        if path is None:
            _DOMAINS_CACHE = data

        logger.info(
            "Référentiel domaines chargé : %d niv1, %d niv2, %d niv3",
            len(data.get("niv1", [])), len(data.get("niv2", [])), len(data.get("niv3", [])),
        )
        return data
    except Exception as exc:
        logger.warning("Erreur lecture référentiel domaines : %s", exc)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# DATACLASS
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class DomainClassification:
    domaine_principal: str = "non_classifié"
    code_niv1: Optional[str] = None
    label_niv1: Optional[str] = None
    code_niv2: Optional[str] = None
    label_niv2: Optional[str] = None
    code_niv3: Optional[str] = None
    label_niv3: Optional[str] = None
    confidence: float = 0.0
    reasoning: str = ""
    classified: bool = False
    llm_calls: int = 0
    backend: str = "unknown"
    error: Optional[str] = None

    # ── NOUVEAU V8.1 ─────────────────────────────────────────────────────────
    domaine_scientifique_detaille: Optional[str] = None
    domaine_applicatif: Optional[str] = None
    # ─────────────────────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "domaine_principal": self.domaine_principal,
            "code_niv1": self.code_niv1,
            "label_niv1": self.label_niv1,
            "code_niv2": self.code_niv2,
            "label_niv2": self.label_niv2,
            "code_niv3": self.code_niv3,
            "label_niv3": self.label_niv3,
            "confidence": round(float(self.confidence), 3),
            "reasoning": self.reasoning,
            "classified": self.classified,
            "llm_calls": self.llm_calls,
            "backend": self.backend,
            "error": self.error,
            # NOUVEAU V8.1
            "domaine_scientifique_detaille": self.domaine_scientifique_detaille,
            "domaine_applicatif": self.domaine_applicatif,
        }


# ══════════════════════════════════════════════════════════════════════════════
# LLM BACKEND
# ══════════════════════════════════════════════════════════════════════════════

def _is_local_model(model: str) -> bool:
    return any(model.startswith(p) for p in LOCAL_MODEL_PREFIXES)


def _strip_model_prefix(model: str) -> str:
    for p in LOCAL_MODEL_PREFIXES:
        if model.startswith(p):
            return model[len(p):]
    return model


def _call_llm(prompt: str, model: str) -> tuple[str, str]:
    if _is_local_model(model):
        raw_model = _strip_model_prefix(model)
        if ollama is None:
            raise RuntimeError("ollama non installé.")
        resp = ollama.chat(
            model=raw_model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.0, "num_predict": 256, "num_ctx": 2048},
        )
        # Gère les deux formes possibles : dict legacy et objet pydantic (ollama >= 0.2)
        if isinstance(resp, dict):
            text = resp.get("message", {}).get("content", "")
        else:
            # Objet pydantic : resp.message est un objet ChatMessage avec .content
            msg = getattr(resp, "message", None)
            if msg is None:
                text = ""
            elif isinstance(msg, dict):
                text = msg.get("content", "")
            else:
                text = getattr(msg, "content", "") or ""
        return str(text or ""), "ollama"

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY manquant.")
    if OpenAI is None:
        raise RuntimeError("openai non installé.")
    client = OpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=512,
        timeout=TIMEOUT_SECONDS,
    )
    text = resp.choices[0].message.content if resp.choices else ""
    return str(text or ""), "openrouter"


def _extract_json_block(text: str) -> str:
    m = re.search(r"```(?:json)?\s*([\s\S]+?)```", text)
    if m:
        return m.group(1).strip()
    m = re.search(r"\{[\s\S]+\}", text)
    if m:
        return m.group(0).strip()
    return text.strip()


def _build_domains_list(domains: dict) -> str:
    """
    Liste compacte mais complète des niv2.
    Ancien bug : lines[:40] pouvait couper la nomenclature avant les domaines B/C.
    """
    entries = _dedupe_domain_entries(_iter_domain_entries(domains, "niv2"), "code_niv2", "label_niv2")
    lines = []
    for entry in entries:
        label = _norm_space(entry.get("label_niv2", ""))
        code = _norm_space(entry.get("code_niv2", ""))
        label_niv1 = _norm_space(entry.get("label_niv1", ""))
        if label and code:
            lines.append(f"  [{code}] {label_niv1} — {label}")
    return "\n".join(lines)


# ── NOUVEAU V8.1 : prompt élargi avec domaine applicatif ─────────────────────

def _build_prompt(summary: str, domains_list: str) -> str:
    return f"""Tu es un expert en classification R&D française (CIR/CII).

Voici un résumé des travaux R&D d'un projet :

{summary[:1500]}

Voici la liste des domaines scientifiques officiels (code — libellé) :
{domains_list}

Réponds UNIQUEMENT avec un objet JSON valide contenant exactement ces clés :
{{
  "code_niv2": "code officiel choisi parmi la liste ci-dessus, ex: B4",
  "confidence": 0.0 à 1.0,
  "reasoning": "explication courte en français (max 2 phrases)",
  "domaine_scientifique_detaille": "libellé précis du sous-domaine scientifique (ex: Matériaux / Vibroacoustique / Mécanique des structures)",
  "domaine_applicatif": "domaine d'application industrielle principal (ex: Motorisation électrique / véhicules électriques, ou Naval de défense, ou Bâtiment, laisser null si non identifiable)"
}}

Règles :
- code_niv2 doit être un code exact de la liste ci-dessus.
- domaine_scientifique_detaille : plus précis que le code officiel, décrit le vrai sous-domaine.
- domaine_applicatif : secteur industriel d'application finale, pas le domaine scientifique.
- Ne pas ajouter de texte hors du JSON.
"""


def _build_prompt_no_domains(summary: str) -> str:
    """Fallback si domains.json est indisponible."""
    return f"""Tu es un expert en R&D française (CIR/CII).

Voici un résumé des travaux R&D d'un projet :

{summary[:1500]}

Réponds UNIQUEMENT avec un objet JSON valide contenant exactement ces clés :
{{
  "domaine_principal": "domaine scientifique principal en français",
  "confidence": 0.0 à 1.0,
  "reasoning": "explication courte en français (max 2 phrases)",
  "domaine_scientifique_detaille": "libellé précis du sous-domaine scientifique",
  "domaine_applicatif": "domaine d'application industrielle principal, ou null"
}}
"""

# ─────────────────────────────────────────────────────────────────────────────


def _parse_response(text: str, domains: Optional[dict]) -> dict:
    raw = _extract_json_block(text)
    try:
        data = json.loads(raw)
    except Exception:
        data = {}

    if not isinstance(data, dict):
        data = {}

    return data


def _resolve_niv2(code: str, domains: dict) -> Optional[dict]:
    if not code or not domains:
        return None
    target = str(code).strip().upper()
    for entry in _iter_domain_entries(domains, "niv2"):
        if str(entry.get("code_niv2", "")).strip().upper() == target:
            return entry
    return None
    for entry in domains.get("niv2", []):
        if str(entry.get("code_niv2", "")).upper() == str(code).upper():
            return entry
    return None


def _get_aggregated_summary(aggregated: Any) -> str:
    if hasattr(aggregated, "summary_for_llm"):
        try:
            return aggregated.summary_for_llm()
        except Exception:
            pass

    d = {}
    if hasattr(aggregated, "to_dict"):
        try:
            d = aggregated.to_dict()
        except Exception:
            pass
    elif isinstance(aggregated, dict):
        d = aggregated

    lines = []
    role_map = {
        "objectif": "OBJECTIFS",
        "verrou": "VERROUS",
        "demarche": "DÉMARCHE",
        "resultat": "RÉSULTATS",
        "etat_art": "ÉTAT DE L'ART",
    }
    by_role = d.get("by_role", {})
    for role, label in role_map.items():
        items = by_role.get(role, [])
        if items:
            lines.append(f"\n## {label}")
            for item in items[:8]:
                phrase = item.get("phrase", "") if isinstance(item, dict) else str(item)
                if phrase:
                    lines.append(f"- {phrase}")

    concepts = d.get("concepts", [])
    if concepts:
        lines.append("\n## CONCEPTS TECHNIQUES")
        lines.append(", ".join(
            (c.get("text", "") if isinstance(c, dict) else str(c))
            for c in concepts[:20]
        ))

    return "\n".join(lines).strip()



def _norm_key(text: Any) -> str:
    text = unicodedata.normalize("NFKD", str(text or "").lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_official_thesaurus(text: str) -> Optional[dict]:
    m = re.search(r"TH[ÉE]SAURUS[\s\S]{0,800}?\b([A-Z]\d+[a-z]?\d*)\s*[:\-]\s*([^\n\|]+)", text or "", re.I | re.U)
    if not m:
        m = re.search(r"\b([A-Z]\d+[a-z]?\d*)\s*[:\-]\s*([^\n\|]{8,120})", text or "", re.I | re.U)
    if not m: return None
    return {"code": _norm_space(m.group(1)), "label": _norm_space(m.group(2))}


def _resolve_domain_code(code: str, domains: Optional[dict]) -> Optional[dict]:
    if not code or not domains: return None
    target = str(code).strip().upper()
    for level in ("niv4", "niv3", "niv2", "niv1"):
        for entry in _iter_domain_entries(domains, level):
            for key in ("code_niv4", "code_niv3", "code_niv2", "code_niv1"):
                if str(entry.get(key, "")).strip().upper() == target:
                    return entry
    return None


def _classification_from_entry(entry: dict, confidence: float, backend: str, reasoning: str, detail: Optional[str] = None, applicatif: Optional[str] = None) -> DomainClassification:
    label = entry.get("label_niv4") or entry.get("label_niv3") or entry.get("label_niv2") or entry.get("label_niv1") or "non_classifié"
    return DomainClassification(
        domaine_principal=label,
        code_niv1=entry.get("code_niv1"), label_niv1=entry.get("label_niv1"),
        code_niv2=entry.get("code_niv2"), label_niv2=entry.get("label_niv2"),
        code_niv3=entry.get("code_niv3"), label_niv3=entry.get("label_niv3"),
        confidence=confidence, reasoning=reasoning, classified=True, backend=backend,
        domaine_scientifique_detaille=detail or label, domaine_applicatif=applicatif)

def _heuristic_domain_from_summary(summary: str, domains: Optional[dict]) -> Optional[DomainClassification]:
    official = _extract_official_thesaurus(summary)
    if official:
        entry = _resolve_domain_code(official["code"], domains)
        if entry:
            return _classification_from_entry(entry, 0.95, "official_thesaurus", "Domaine extrait depuis le thésaurus officiel du document.", official.get("label"), _infer_applicative_domain(summary))
        return DomainClassification(domaine_principal=official.get("label") or official.get("code") or "non_classifié", code_niv2=official.get("code"), confidence=0.92, reasoning="Domaine extrait depuis le thésaurus officiel du document, mais code non résolu dans le référentiel.", classified=True, backend="official_thesaurus_unresolved", domaine_scientifique_detaille=official.get("label"), domaine_applicatif=_infer_applicative_domain(summary))

    text = _norm_key(summary)
    if re.search(r"emballage|conditionnement|barriere sterile|opercule|blister|thermoformage|packaging", text):
        for code in ("B7c12", "B7"):
            entry = _resolve_domain_code(code, domains)
            if entry:
                return _classification_from_entry(entry, 0.86, "heuristic_fallback", "Signaux forts : emballage, conditionnement, thermoformage, dispositif médical.", "Industrie de l'emballage / emballage médical / matériaux et procédés de conditionnement", _infer_applicative_domain(summary) or "Emballage médical / dispositifs médicaux")
        return DomainClassification(domaine_principal="Industrie de l'emballage / emballage médical", code_niv2="B7", confidence=0.84, reasoning="Signaux forts : emballage, conditionnement, thermoformage, dispositif médical.", classified=True, backend="heuristic_fallback", domaine_scientifique_detaille="Industrie de l'emballage / emballage médical / matériaux et procédés de conditionnement", domaine_applicatif=_infer_applicative_domain(summary) or "Emballage médical / dispositifs médicaux")

    mat = bool(re.search(r"materiau|materiaux|composite|polymere|auxetique|pla|tpu|epdm|aramide", text))
    vib = bool(re.search(r"vibro|vibration|acoust|mecanique|structure|resonance|decouplage", text))
    if mat or vib:
        code = "B3" if mat else "B4"
        detail = "Matériaux composites / vibroacoustique / mécanique des structures" if mat and vib else ("Matériaux" if mat else "Mécanique / vibroacoustique")
        entry = _resolve_domain_code(code, domains)
        if entry:
            return _classification_from_entry(entry, 0.80, "heuristic_fallback", "Signaux forts : matériaux, composites, vibrations ou mécanique.", detail, _infer_applicative_domain(summary))
        return DomainClassification(domaine_principal=detail, code_niv2=code, confidence=0.78, reasoning="Signaux forts : matériaux, composites, vibrations ou mécanique.", classified=True, backend="heuristic_fallback", domaine_scientifique_detaille=detail, domaine_applicatif=_infer_applicative_domain(summary))
    return None


def _infer_applicative_domain(text: str) -> Optional[str]:
    t = _norm_key(text)
    apps = []
    if re.search(r"dispositif medical|medical|steril|chirurg", t): apps.append("Emballage médical / dispositifs médicaux")
    if re.search(r"moteur electrique|vehicule electrique|motorisation", t): apps.append("Motorisation électrique / véhicules électriques")
    if re.search(r"naval|defense|sous-marin|batiment naval", t): apps.append("Naval de défense")
    if re.search(r"batiment|construction|energie|thermique", t): apps.append("Bâtiment / énergie")
    return " / ".join(dict.fromkeys(apps)) if apps else None


def classify_domain(
    aggregated: Any,
    model: str = DEFAULT_LLM_MODEL,
    enabled: bool = True,
    domains_path: Optional[str] = None,
) -> DomainClassification:
    result = DomainClassification()

    if not enabled:
        result.error = "Classification désactivée."
        return result

    summary = _get_aggregated_summary(aggregated)
    if not summary.strip():
        result.error = "Résumé aggregated vide — classification impossible."
        return result

    domains = load_domains(domains_path)

    official = _extract_official_thesaurus(summary)
    if official:
        entry = _resolve_domain_code(official["code"], domains)
        if entry:
            return _classification_from_entry(entry, 0.95, "official_thesaurus", "Domaine extrait depuis le thésaurus officiel du document.", official.get("label"), _infer_applicative_domain(summary))

    for attempt in range(MAX_RETRIES + 1):
        try:
            if domains:
                domains_list = _build_domains_list(domains)
                prompt = _build_prompt(summary, domains_list)
            else:
                prompt = _build_prompt_no_domains(summary)

            t0 = time.time()
            raw_text, backend = _call_llm(prompt, model)
            elapsed = time.time() - t0

            result.llm_calls += 1
            result.backend = backend

            data = _parse_response(raw_text, domains)

            # ── NOUVEAU V8.1 : extraire domaine applicatif + détaillé ─────────
            result.domaine_scientifique_detaille = (
                str(data.get("domaine_scientifique_detaille") or "").strip() or None
            )
            result.domaine_applicatif = (
                str(data.get("domaine_applicatif") or "").strip() or None
            )
            if result.domaine_applicatif and result.domaine_applicatif.lower() in ("null", "none", ""):
                result.domaine_applicatif = None
            # ─────────────────────────────────────────────────────────────────

            result.confidence = float(data.get("confidence", 0.0) or 0.0)
            result.reasoning = str(data.get("reasoning", "") or "")

            if domains:
                code = str(data.get("code_niv2", "") or "").strip()
                entry = _resolve_domain_code(code, domains)
                if entry:
                    result.code_niv2 = entry.get("code_niv2")
                    result.label_niv2 = entry.get("label_niv2")
                    result.code_niv1 = entry.get("code_niv1")
                    result.label_niv1 = entry.get("label_niv1")
                    result.code_niv3 = entry.get("code_niv3")
                    result.label_niv3 = entry.get("label_niv3")
                    label_niv3 = entry.get("label_niv3") or ""
                    label_niv2 = entry.get("label_niv2") or ""
                    result.domaine_principal = label_niv3 or label_niv2 or "non_classifié"
                    result.classified = True
                else:
                    result.domaine_principal = str(data.get("domaine_principal") or "non_classifié")
                    result.classified = False
            else:
                result.domaine_principal = str(data.get("domaine_principal") or "non_classifié")
                result.classified = bool(result.domaine_principal and result.domaine_principal != "non_classifié")

            # Si le LLM n'a pas réussi à choisir un code valide, fallback déterministe.
            if not result.classified:
                heuristic = _heuristic_domain_from_summary(summary, domains)
                if heuristic is not None:
                    heuristic.llm_calls = result.llm_calls
                    heuristic.backend = f"{result.backend}+heuristic_fallback" if result.backend else "heuristic_fallback"
                    heuristic.error = result.error
                    result = heuristic

            # Si domaine_scientifique_detaille est renseigné, il enrichit domaine_principal.
            if result.domaine_scientifique_detaille and not result.classified:
                result.domaine_principal = result.domaine_scientifique_detaille

            logger.info(
                "Domain classifier v7.2.0 : %s | applicatif=%s | conf=%.2f | %.2fs",
                result.domaine_principal, result.domaine_applicatif, result.confidence, elapsed,
            )
            return result

        except Exception as exc:
            logger.warning("Domain classifier erreur (attempt %d) : %s", attempt + 1, exc)
            result.error = str(exc)
            if attempt < MAX_RETRIES:
                time.sleep(1.0)

    return result