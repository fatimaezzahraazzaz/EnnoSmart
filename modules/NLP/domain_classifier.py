"""
modules/NLP/domain_classifier.py
──────────────────────────────────────────────────────────────────────────────
Classification du domaine de recherche sur la nomenclature officielle MESR.

  aggregator.py ──► domain_classifier.py ──► (alimente le NLPResult)

PRINCIPE
--------
Le problème "le domaine peut être n'importe quoi, et le futur domaine est
inconnu" est résolu en transformant le problème en CLASSIFICATION FERMÉE :

  - On ne demande PAS au LLM "quel est le domaine ?" (réponse libre, instable).
  - On lui donne la liste FINIE des 33 sous-domaines officiels (niv2) et on
    lui demande de CHOISIR le code le plus probable.
  - On VALIDE ensuite que le code renvoyé existe vraiment dans domains.json.
  - S'il n'existe pas / si le LLM hésite trop → "non_classifié".

AUCUNE règle métier, aucun mot-clé hardcodé. Le seul "savoir" est dans
domains.json, qui est une donnée de référence régénérable, pas du code.

domains.json est généré une fois par build_domains_json.py depuis le xlsx MESR.

API : classify_domain(aggregated, model, enabled) -> DomainClassification

Version : 1.0.0
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
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

logger = logging.getLogger(__name__)

DEFAULT_LLM_MODEL = "ollama:qwen3:4b-instruct"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
LOCAL_MODEL_PREFIXES = ("ollama:", "local:")
TIMEOUT_SECONDS = 60
MAX_RETRIES = 1

# Emplacement par défaut du référentiel. Adapte si besoin.
DEFAULT_DOMAINS_PATH = Path(__file__).parent / "data" / "domains.json"


# ══════════════════════════════════════════════════════════════════════════════
# CHARGEMENT DU RÉFÉRENTIEL
# ══════════════════════════════════════════════════════════════════════════════

_DOMAINS_CACHE: Optional[dict] = None


def load_domains(path: str | Path | None = None) -> Optional[dict]:
    """
    Charge domains.json (mis en cache). Retourne None si introuvable.
    """
    global _DOMAINS_CACHE
    if _DOMAINS_CACHE is not None:
        return _DOMAINS_CACHE

    p = Path(path) if path else DEFAULT_DOMAINS_PATH
    if not p.exists():
        # Cherche aussi à côté de ce fichier ou dans le cwd.
        for alt in [Path(__file__).parent / "domains.json", Path("domains.json")]:
            if alt.exists():
                p = alt
                break

    if not p.exists():
        logger.warning(
            "domains.json introuvable (%s). La classification de domaine sera désactivée. "
            "Génère-le avec build_domains_json.py.", p,
        )
        return None

    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        if "niv1" not in data or "niv2" not in data:
            logger.warning("domains.json mal formé : clés niv1/niv2 manquantes.")
            return None
        _DOMAINS_CACHE = data
        logger.info(
            "domains.json chargé : %d niv1, %d niv2, %d niv3",
            len(data.get("niv1", {})),
            len(data.get("niv2", {})),
            len(data.get("niv3", {})),
        )
        return data
    except Exception as exc:
        logger.warning("Échec chargement domains.json : %s", exc)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# DATACLASS
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class DomainClassification:
    """Résultat de classification de domaine."""
    code_niv1: Optional[str] = None
    label_niv1: Optional[str] = None
    code_niv2: Optional[str] = None
    label_niv2: Optional[str] = None
    code_niv3: Optional[str] = None
    label_niv3: Optional[str] = None
    confidence: float = 0.0
    reasoning: str = ""
    classified: bool = False           # False => "non_classifié"
    llm_calls: int = 0
    backend: str = "unknown"
    error: Optional[str] = None

    @property
    def domaine_principal(self) -> str:
        """Libellé lisible pour le NLPResult."""
        if not self.classified:
            return "non_classifié"
        # Le plus précis disponible.
        if self.label_niv2:
            base = self.label_niv2
            if self.code_niv2:
                base = f"{base} [{self.code_niv2}]"
            return base
        if self.label_niv1:
            return self.label_niv1
        return "non_classifié"

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
        }


# ══════════════════════════════════════════════════════════════════════════════
# PROMPT
# ══════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """Tu es un classificateur de domaine de recherche.

On te donne :
1. Une synthèse de preuves extraites d'un dossier R&D (objectifs, verrous,
   démarche, résultats, concepts techniques).
2. La liste FERMÉE des sous-domaines de recherche officiels.

Ta tâche : CHOISIR dans la liste le sous-domaine (code niv2) qui correspond
le mieux au projet décrit.

RÈGLES ABSOLUES :
1. Réponds UNIQUEMENT en JSON valide. Aucun texte avant ou après.
2. Le champ "code_niv2" DOIT être un code EXACT de la liste fournie
   (ex: "A3", "B4", "C5"). N'invente jamais un code.
3. Si vraiment aucun sous-domaine ne convient, ou si la synthèse est trop
   pauvre pour décider, mets "code_niv2": null.
4. "confidence" entre 0 et 1 : ta certitude que le code choisi est correct.
5. "reasoning" : une phrase courte expliquant ton choix, basée uniquement
   sur la synthèse fournie.
6. Ne te laisse pas piéger par un mot isolé. Juge le projet dans son ensemble.

FORMAT JSON ATTENDU :
{
  "code_niv2": "B4",
  "confidence": 0.82,
  "reasoning": "Le projet porte sur la conception mécanique d'un emballage et sa tenue aux chocs."
}
"""

USER_PROMPT_TEMPLATE = """# SYNTHÈSE DES PREUVES DU DOSSIER R&D

{evidence_summary}

# LISTE FERMÉE DES SOUS-DOMAINES OFFICIELS (choisis UN code niv2)

{domain_list}

# CONSIGNE
Choisis le code niv2 le plus probable pour ce projet.
Le code doit exister exactement dans la liste ci-dessus.
Réponds uniquement avec le JSON strict.
"""


def _build_domain_list(domains: dict) -> str:
    """
    Construit la liste lisible des niv2, groupée par niv1.
    C'est ce qui est injecté dans le prompt — la "sortie fermée".
    """
    niv1 = domains.get("niv1", {})
    niv2 = domains.get("niv2", {})

    lines: list[str] = []
    for c1, l1 in niv1.items():
        lines.append(f"\n[{c1}] {l1}")
        for c2, v2 in niv2.items():
            if v2.get("parent") == c1:
                lines.append(f"  {c2} : {v2.get('label', '')}")
    return "\n".join(lines).strip()


# ══════════════════════════════════════════════════════════════════════════════
# APPELS LLM
# ══════════════════════════════════════════════════════════════════════════════

def _is_local_model(model: str) -> bool:
    return str(model or "").startswith(LOCAL_MODEL_PREFIXES)


def _clean_local_model_name(model: str) -> str:
    model = str(model or DEFAULT_LLM_MODEL).strip()
    for prefix in LOCAL_MODEL_PREFIXES:
        if model.startswith(prefix):
            return model[len(prefix):]
    return model


def _get_openrouter_client():
    if OpenAI is None:
        return None
    api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    return OpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)


def _extract_json(content: str) -> Optional[dict]:
    raw = str(content or "").strip()
    if not raw:
        return None
    for c in (raw, re.sub(r"```(?:json)?|```", "", raw, flags=re.I).strip()):
        try:
            d = json.loads(c)
            if isinstance(d, dict):
                return d
        except Exception:
            pass
    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        try:
            d = json.loads(m.group(0))
            if isinstance(d, dict):
                return d
        except Exception:
            pass
    return None


_OLLAMA_SCHEMA = {
    "type": "object",
    "properties": {
        "code_niv2": {"type": ["string", "null"]},
        "confidence": {"type": "number"},
        "reasoning": {"type": "string"},
    },
    "required": ["code_niv2", "confidence"],
}


def _call_ollama(evidence_summary: str, domain_list: str, model: str, retry: int = 0) -> Optional[dict]:
    if ollama is None:
        logger.error("ollama non installé : pip install ollama")
        return None
    local_model = _clean_local_model_name(model)
    try:
        response = ollama.chat(
            model=local_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": USER_PROMPT_TEMPLATE.format(
                    evidence_summary=evidence_summary, domain_list=domain_list)},
            ],
            format=_OLLAMA_SCHEMA,
            options={"temperature": 0, "top_p": 0.1, "num_ctx": 8192, "num_predict": 300},
        )
        content = response.get("message", {}).get("content", "")
        data = _extract_json(content)
        if data is None and retry < MAX_RETRIES:
            time.sleep(1.0)
            return _call_ollama(evidence_summary, domain_list, model, retry + 1)
        return data
    except Exception as exc:
        logger.exception("Erreur Ollama domain_classifier (retry=%d) : %s", retry, exc)
        if retry < MAX_RETRIES:
            time.sleep(1.0)
            return _call_ollama(evidence_summary, domain_list, model, retry + 1)
        return None


def _call_openrouter(evidence_summary: str, domain_list: str, model: str) -> Optional[dict]:
    client = _get_openrouter_client()
    if client is None:
        return None
    try:
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": USER_PROMPT_TEMPLATE.format(
                    evidence_summary=evidence_summary, domain_list=domain_list)},
            ],
            temperature=0,
            max_tokens=400,
            timeout=TIMEOUT_SECONDS,
        )
        return _extract_json(completion.choices[0].message.content)
    except Exception as exc:
        logger.exception("Erreur OpenRouter domain_classifier : %s", exc)
        return None


def _call_llm(evidence_summary: str, domain_list: str, model: str) -> tuple[Optional[dict], str]:
    if _is_local_model(model):
        return _call_ollama(evidence_summary, domain_list, model), "ollama"
    return _call_openrouter(evidence_summary, domain_list, model), "openrouter"


# ══════════════════════════════════════════════════════════════════════════════
# VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

def _validate_and_build(data: dict, domains: dict) -> DomainClassification:
    """
    Valide le code renvoyé par le LLM contre domains.json.
    Si le code n'existe pas → non_classifié. Pas de devinette.
    """
    result = DomainClassification()

    code2 = data.get("code_niv2")
    code2 = str(code2).strip() if code2 else None
    confidence = data.get("confidence", 0.0)
    try:
        confidence = float(confidence)
    except Exception:
        confidence = 0.0
    reasoning = str(data.get("reasoning", "") or "").strip()

    niv1 = domains.get("niv1", {})
    niv2 = domains.get("niv2", {})

    # Le LLM a explicitement dit "aucun" → non_classifié.
    if not code2 or code2.lower() in {"null", "none", "aucun", ""}:
        result.classified = False
        result.confidence = confidence
        result.reasoning = reasoning or "Le classificateur n'a pas trouvé de sous-domaine adapté."
        return result

    # Validation stricte : le code DOIT exister dans le référentiel.
    if code2 not in niv2:
        logger.warning(
            "Code niv2 '%s' renvoyé par le LLM mais ABSENT de domains.json → non_classifié",
            code2,
        )
        result.classified = False
        result.confidence = 0.0
        result.reasoning = f"Code '{code2}' invalide (hors nomenclature) → non classifié."
        result.error = f"invalid_code:{code2}"
        return result

    # Code valide : on remonte la hiérarchie.
    v2 = niv2[code2]
    code1 = v2.get("parent")
    result.code_niv2 = code2
    result.label_niv2 = v2.get("label")
    result.code_niv1 = code1
    result.label_niv1 = niv1.get(code1) if code1 else None
    result.confidence = max(0.0, min(confidence, 1.0))
    result.reasoning = reasoning
    result.classified = True

    # Si confiance trop basse, on garde la classification mais on le signale.
    if result.confidence < 0.35:
        logger.info(
            "Classification domaine à faible confiance (%.2f) : %s",
            result.confidence, result.domaine_principal,
        )

    return result


# ══════════════════════════════════════════════════════════════════════════════
# POINT D'ENTRÉE
# ══════════════════════════════════════════════════════════════════════════════

def classify_domain(
    aggregated: Any,
    model: str = DEFAULT_LLM_MODEL,
    enabled: bool = True,
    domains_path: str | Path | None = None,
) -> DomainClassification:
    """
    Classe le domaine du dossier sur la nomenclature MESR.

    Paramètres
    ----------
    aggregated   : AggregatedEvidence (depuis aggregator.aggregate)
                   doit fournir .summary_for_llm() et .has_diagnostic_content()
    model        : modèle LLM
    enabled      : si False, retourne non_classifié sans appel
    domains_path : chemin vers domains.json (sinon emplacement par défaut)

    Retourne
    --------
    DomainClassification
    """
    result = DomainClassification()

    if not enabled:
        result.reasoning = "Classification désactivée."
        return result

    domains = load_domains(domains_path)
    if domains is None:
        result.reasoning = "domains.json indisponible — classification impossible."
        result.error = "domains_json_missing"
        return result

    # Construire la synthèse des preuves.
    if hasattr(aggregated, "summary_for_llm"):
        evidence_summary = aggregated.summary_for_llm()
    else:
        evidence_summary = str(aggregated or "")

    if not evidence_summary.strip():
        result.reasoning = "Aucune preuve à classer."
        return result

    if hasattr(aggregated, "has_diagnostic_content") and not aggregated.has_diagnostic_content():
        result.reasoning = "Pas de contenu R&D diagnostic — domaine non classifié."
        return result

    domain_list = _build_domain_list(domains)

    data, backend = _call_llm(evidence_summary, domain_list, model)
    result.llm_calls = 1
    result.backend = backend

    if data is None:
        result.reasoning = "Aucune réponse LLM exploitable."
        result.error = "no_llm_response"
        return result

    try:
        validated = _validate_and_build(data, domains)
        validated.llm_calls = result.llm_calls
        validated.backend = backend
        logger.info(
            "Domaine classé : %s (conf=%.2f)",
            validated.domaine_principal, validated.confidence,
        )
        return validated
    except Exception as exc:
        logger.exception("Erreur validation domaine : %s", exc)
        result.reasoning = f"Erreur de validation : {exc}"
        result.error = str(exc)
        return result


# ── Interface rapide (debug / tests) ──────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    # Test du chargement + validation SANS appel LLM réel.
    domains = load_domains(sys.argv[1] if len(sys.argv) > 1 else None)
    if domains is None:
        print("domains.json introuvable. Passe le chemin en argument :")
        print("  python domain_classifier.py /chemin/vers/domains.json")
        sys.exit(1)

    print(f"Référentiel chargé : {len(domains['niv2'])} sous-domaines\n")

    # Simule des réponses LLM et teste la validation.
    print("=== Test validation ===")
    for fake in [
        {"code_niv2": "B4", "confidence": 0.85, "reasoning": "Conception mécanique d'emballage."},
        {"code_niv2": "A3", "confidence": 0.78, "reasoning": "Génération de code par IA."},
        {"code_niv2": "ZZ99", "confidence": 0.9, "reasoning": "Code inventé."},  # invalide
        {"code_niv2": None, "confidence": 0.2, "reasoning": "Trop ambigu."},
    ]:
        r = _validate_and_build(fake, domains)
        print(f"  input={fake['code_niv2']!r:8} → {r.domaine_principal!r:55} "
              f"classified={r.classified} conf={r.confidence}")

    if len(sys.argv) > 2 and sys.argv[2] == "--live":
        from aggregator import aggregate
        # Nécessiterait un EvidenceMapResult réel. Voir test du pipeline complet.
        print("\n(test --live : à lancer via le pipeline complet)")
