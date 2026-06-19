# -*- coding: utf-8 -*-
from __future__ import annotations

"""
CIR_STYLE_MEMORY — Mémoire rédactionnelle CIR

But :
- Enregistrer des CIR validés comme exemples de style rédactionnel.
- Ne PAS utiliser Frascati ici.
- Ne PAS copier les anciens CIR.
- Donner au LLM des exemples de rédaction pour qu'il reformule le diagnostic R&D
  avec une structure et un vocabulaire proches des CIR validés.

Architecture :
CIR final validé
→ extraction structurée CIR
→ exemples de style par rôle : objectif, verrou, etat_art, limite, methode, resultat, contribution
→ mémoire persistante JSON par organisme
→ reformulation R&D/CIR du dossier courant avec sources courantes + style memory
"""

import json
import re
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


BASE_DIR = Path(r"C:\EnnoSmart")
STORAGE_DIR = BASE_DIR / "storage" / "organismes"
OUTPUTS_DIR = BASE_DIR / "outputs" / "safe_rag_upload"

PACK_KEYS = [
    "objectifs_locaux",
    "verrous_rnd_locaux",
    "methodes_locales",
    "resultats_locaux",
    "limites_locales",
    "contributions_locales",
    "etat_art_local",
    "parametres_locaux",
]

ROLE_BY_PACK = {
    "objectifs_locaux": "objectif",
    "verrous_rnd_locaux": "verrou",
    "methodes_locales": "methode",
    "resultats_locaux": "resultat",
    "limites_locales": "limite",
    "contributions_locales": "contribution",
    "etat_art_local": "etat_art",
    "parametres_locaux": "parametre",
}

STYLE_ROLE_ORDER = [
    "objectif",
    "etat_art",
    "limite",
    "verrou",
    "methode",
    "resultat",
    "contribution",
    "parametre",
]

STOP_WORDS = {
    "avec", "dans", "pour", "plus", "moins", "entre", "comme", "cette",
    "cela", "ainsi", "afin", "etre", "être", "sont", "nous", "notre",
    "leur", "leurs", "des", "les", "une", "aux", "sur", "par", "que",
    "qui", "quoi", "dont", "de", "du", "la", "le", "un", "en", "et",
    "ou", "au", "ce", "ces", "son", "ses", "projet", "travaux"
}

STYLE_CONNECTORS = [
    "afin de", "dans le cadre", "il s'agit", "l'objectif", "les travaux",
    "ont permis", "mettent en évidence", "il convient", "à ce stade",
    "ce phénomène", "cette approche", "en particulier", "notamment",
    "au regard", "dans ces conditions", "par conséquent", "ce qui",
    "a conduit à", "vise à", "consiste à", "permettant de", "nécessite de",
]

CIR_WRITING_PATTERNS = {
    "objectif": [
        "l'objectif de ce projet est de",
        "ce projet vise à",
        "les travaux ont pour objectif de",
        "afin de permettre",
    ],
    "verrou": [
        "verrous et incertitudes",
        "incertitude technique",
        "la difficulté réside dans",
        "la maîtrise de",
        "la cause technique",
        "ne permet pas de garantir",
        "reste à déterminer",
    ],
    "etat_art": [
        "les solutions existantes",
        "l'état de l'art",
        "les connaissances disponibles",
        "ne permettent pas",
        "les approches connues",
    ],
    "limite": [
        "insuffisances",
        "limites des solutions existantes",
        "ne sont pas directement transposables",
        "ne permettent pas de répondre",
    ],
    "methode": [
        "les travaux réalisés ont consisté à",
        "une campagne d'essais",
        "des analyses ont été menées",
        "un prototype",
        "une simulation",
    ],
    "resultat": [
        "les résultats obtenus",
        "les essais ont montré",
        "les mesures mettent en évidence",
        "a permis de",
        "n'a pas permis de",
    ],
    "contribution": [
        "les travaux ont permis d'acquérir",
        "contribution technique",
        "connaissances nouvelles",
        "meilleure compréhension",
    ],
}


# ---------------------------------------------------------------------
# Domaine technique officiel
# ---------------------------------------------------------------------

def _domain_is_valid(domain: Any) -> bool:
    if not isinstance(domain, dict):
        return False
    if domain.get("warning") and not (
        domain.get("domain_code_niv1")
        or domain.get("domain_code_niv2")
        or domain.get("domain_code_niv3")
        or domain.get("main_domain_code")
    ):
        return False
    return bool(
        domain.get("domain_code_niv1")
        or domain.get("domain_code_niv2")
        or domain.get("domain_code_niv3")
        or domain.get("main_domain_code")
        or domain.get("display_label")
    )


def _domain_key(domain: Optional[Dict[str, Any]]) -> str:
    """
    Clé utilisée pour filtrer la mémoire de style.
    On privilégie le domaine principal niv2, car il est plus stable que le sous-domaine niv3.
    """
    if not isinstance(domain, dict):
        return "unknown"
    return (
        domain.get("main_domain_code")
        or domain.get("domain_code_niv2")
        or domain.get("domain_code_niv1")
        or domain.get("domain_code_niv3")
        or "unknown"
    )


def _domain_label(domain: Optional[Dict[str, Any]]) -> str:
    if not isinstance(domain, dict):
        return "Domaine non détecté"
    return (
        domain.get("display_label")
        or (domain.get("display") or {}).get("display_label")
        or domain.get("main_domain_label")
        or domain.get("domain_label_niv2")
        or domain.get("domain_label_niv1")
        or domain.get("domain_label_niv3")
        or "Domaine non détecté"
    )


def _normalize_domain_detection(domain: Optional[Dict[str, Any]], source: str = "") -> Dict[str, Any]:
    domain = domain if isinstance(domain, dict) else {}
    out = dict(domain)
    display = out.get("display") if isinstance(out.get("display"), dict) else {}

    out["domain_key"] = _domain_key(out)
    out["domain_label"] = _domain_label(out)
    out["domain_source_in_style_memory"] = source or out.get("source") or "unknown"

    # Alias lisibles
    out["main_domain_code"] = out.get("main_domain_code") or out.get("domain_code_niv2") or display.get("main_domain_code")
    out["main_domain_label"] = out.get("main_domain_label") or out.get("domain_label_niv2") or display.get("main_domain_label")
    out["sub_domain_code"] = out.get("sub_domain_code") or out.get("domain_code_niv3") or display.get("sub_domain_code")
    out["sub_domain_label"] = out.get("sub_domain_label") or out.get("domain_label_niv3") or display.get("sub_domain_label")
    out["broad_domain_code"] = out.get("broad_domain_code") or out.get("domain_code_niv1") or display.get("broad_domain_code")
    out["broad_domain_label"] = out.get("broad_domain_label") or out.get("domain_label_niv1") or display.get("broad_domain_label")
    out["display_label"] = out.get("display_label") or display.get("display_label") or out["domain_label"]

    if not out.get("domain_key"):
        out["domain_key"] = "unknown"
    return out


def _find_domain_detection_in_nlp(nlp_result: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Cherche le domaine déjà calculé par le pipeline NLP.
    Priorité :
    - nlp_result["domain_detection"]
    - raw_result/domain_detection
    - cir_structured_result/domain_detection
    - sous-objets fréquents
    """
    if not isinstance(nlp_result, dict):
        return None, "empty"

    direct_keys = [
        "domain_detection",
        "domain",
        "project_domain",
        "domain_result",
    ]

    for key in direct_keys:
        val = nlp_result.get(key)
        if _domain_is_valid(val):
            return val, f"nlp_result.{key}"

    nested_keys = [
        "raw_result",
        "raw_nlp_result",
        "brut_result",
        "cir_structured_result",
        "cir_result",
        "final_result",
        "pipeline_result",
    ]

    for parent in nested_keys:
        obj = nlp_result.get(parent)
        if not isinstance(obj, dict):
            continue
        for key in direct_keys:
            val = obj.get(key)
            if _domain_is_valid(val):
                return val, f"nlp_result.{parent}.{key}"

    return None, "not_found"


def _pack_to_text_for_domain(data: Any) -> str:
    """
    Convertit un nlp_result ou pack en texte pour les signatures domain_classifier
    qui attendent une liste de textes ou une chaîne.
    """
    texts = []

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k in {"text", "source_text", "section_title", "title", "section_label"} and isinstance(v, str):
                    if len(v.strip()) > 20:
                        texts.append(v.strip())
                else:
                    walk(v)
        elif isinstance(x, list):
            for y in x:
                walk(y)

    walk(data)
    return "\n".join(texts[:300])


def _official_classify_domain(data: Any) -> Dict[str, Any]:
    """
    Fallback : utilise le domain_classifier officiel du NLP.
    On essaie plusieurs chemins et signatures, car ton projet a plusieurs versions.
    """
    errors = []
    text = _pack_to_text_for_domain(data)

    candidates = [
        "modules.NLP.domain_classifier",
        "modules.NLP.pipeline.domain_classifier",
        "domain_classifier",
    ]

    function_names = [
        "classify_domain",
        "detect_domain",
        "classify_project_domain",
        "infer_domain",
    ]

    for module_name in candidates:
        try:
            mod = __import__(module_name, fromlist=function_names)
        except Exception as e:
            errors.append(f"{module_name}: import={e}")
            continue

        for fname in function_names:
            fn = getattr(mod, fname, None)
            if not callable(fn):
                continue

            # Signature 1 : fn(data)
            try:
                res = fn(data)
                if _domain_is_valid(res):
                    return res
            except Exception as e:
                errors.append(f"{module_name}.{fname}(data): {e}")

            # Signature 2 : fn(text)
            try:
                res = fn(text)
                if _domain_is_valid(res):
                    return res
            except Exception as e:
                errors.append(f"{module_name}.{fname}(text): {e}")

            # Signature 3 : fn(documents=[...])
            try:
                res = fn(documents=[{"text": text}])
                if _domain_is_valid(res):
                    return res
            except Exception as e:
                errors.append(f"{module_name}.{fname}(documents=): {e}")

            # Signature 4 : fn(texts=[...])
            try:
                res = fn(texts=[text])
                if _domain_is_valid(res):
                    return res
            except Exception as e:
                errors.append(f"{module_name}.{fname}(texts=): {e}")

    # Dernier fallback très léger : seulement si le domain_classifier officiel n'est pas appelable.
    low = norm(text)
    if any(k in low for k in ["compresseur", "piston", "segment", "carter", "pression", "vibration", "acoustique", "réfrigérant", "refrigerant"]):
        return {
            "domain_code_niv1": "industrie",
            "domain_label_niv1": "Industrie",
            "domain_code_niv2": "mecanique_industrielle",
            "domain_label_niv2": "Mécanique / industriel",
            "domain_code_niv3": "compresseur_mecanique",
            "domain_label_niv3": "Compresseurs / mécanique",
            "main_domain_code": "mecanique_industrielle",
            "main_domain_label": "Mécanique / industriel",
            "sub_domain_code": "compresseur_mecanique",
            "sub_domain_label": "Compresseurs / mécanique",
            "display_label": "Mécanique / industriel > Compresseurs / mécanique",
            "confidence": 0.55,
            "top_domains": [],
            "warning": "Fallback léger utilisé car domain_classifier officiel non appelable.",
        }

    return {
        "domain_code_niv1": None,
        "domain_label_niv1": None,
        "domain_code_niv2": None,
        "domain_label_niv2": None,
        "domain_code_niv3": None,
        "domain_label_niv3": None,
        "main_domain_code": None,
        "main_domain_label": None,
        "sub_domain_code": None,
        "sub_domain_label": None,
        "display_label": "Domaine non détecté",
        "confidence": 0.0,
        "top_domains": [],
        "warning": "Impossible d'appeler le domain_classifier officiel : " + " | ".join(errors[-8:]),
    }


def detect_domain_for_style_memory(nlp_result: Dict[str, Any], pack: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Fonction centrale :
    1) réutilise le domaine officiel déjà présent dans le nlp_result ;
    2) si absent, appelle le domain_classifier officiel sur le nlp_result ;
    3) si encore insuffisant, appelle le domain_classifier officiel sur le pack.
    """
    found, source = _find_domain_detection_in_nlp(nlp_result)
    if _domain_is_valid(found):
        return _normalize_domain_detection(found, source=source)

    classified = _official_classify_domain(nlp_result)
    if _domain_is_valid(classified):
        return _normalize_domain_detection(classified, source="official_domain_classifier.nlp_result")

    if isinstance(pack, dict):
        classified_pack = _official_classify_domain(pack)
        if _domain_is_valid(classified_pack):
            return _normalize_domain_detection(classified_pack, source="official_domain_classifier.pack")

    return _normalize_domain_detection(classified, source="not_detected")


def slug(x: Any) -> str:
    x = str(x or "").strip().lower()
    x = re.sub(r"[^\w\-]+", "_", x, flags=re.UNICODE)
    x = re.sub(r"_+", "_", x).strip("_")
    return x or "unknown"


def clean_text(x: Any) -> str:
    x = str(x or "")
    x = x.replace("\r\n", "\n").replace("\r", "\n")
    x = re.sub(r"[ \t]+", " ", x)
    x = re.sub(r"\n{3,}", "\n\n", x)
    return x.strip()


def norm(x: Any) -> str:
    x = clean_text(x).lower()
    tr = str.maketrans("àâäéèêëîïôöùûüç’", "aaaeeeeiioouuuc'")
    x = x.translate(tr)
    x = re.sub(r"[^\w%/.,\-]+", " ", x)
    x = re.sub(r"\s+", " ", x)
    return x.strip()


def short_text(text: Any, limit: int = 1600) -> str:
    text = clean_text(text)
    if len(text) <= limit:
        return text
    cut = text[:limit]
    pos = max(cut.rfind("."), cut.rfind("\n"), cut.rfind(";"))
    if pos < 450:
        pos = limit
    return cut[:pos].strip() + "…"


def sha(text: str) -> str:
    return hashlib.sha1(clean_text(text).encode("utf-8", errors="ignore")).hexdigest()[:16]


def read_json(path: str | Path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: str | Path, data: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def organisme_dir(organisme: str) -> Path:
    return STORAGE_DIR / slug(organisme)


def project_year_dir(organisme: str, project: str, year: str) -> Path:
    return organisme_dir(organisme) / "projects" / slug(project) / "years" / str(year)


def style_memory_dir(organisme: str) -> Path:
    return organisme_dir(organisme) / "cir_style_memory"


def style_memory_path(organisme: str) -> Path:
    return style_memory_dir(organisme) / "style_memory.json"


def reformulation_output_path(organisme: str, project: str, year: str) -> Path:
    return project_year_dir(organisme, project, year) / "diagnostics" / "reformulation_rnd_style_cir.json"


def current_nlp_default_path(organisme: str, project: str, year: str) -> Path:
    return OUTPUTS_DIR / organisme / project / str(year) / "nlp_result.json"


def _safe_pack(pack: Any) -> Dict[str, List[Dict[str, Any]]]:
    out = {k: [] for k in PACK_KEYS}
    if isinstance(pack, dict):
        for k in PACK_KEYS:
            arr = pack.get(k)
            if isinstance(arr, list):
                out[k] = [x for x in arr if isinstance(x, dict)]
    return out


def get_cir_pack_without_frascati(nlp_result: Dict[str, Any]) -> Tuple[Dict[str, List[Dict[str, Any]]], str]:
    """
    Pour apprendre le style d'un CIR validé :
    priorité aux sections CIR structurées AVANT Frascati.
    """
    if not isinstance(nlp_result, dict):
        return _safe_pack({}), "empty"

    cir = nlp_result.get("cir_structured_result")
    if isinstance(cir, dict) and isinstance(cir.get("evidence_pack_before_frascati"), dict):
        return _safe_pack(cir["evidence_pack_before_frascati"]), "cir_structured_result.evidence_pack_before_frascati"

    if nlp_result.get("pipeline_type") == "cir_structured" and isinstance(nlp_result.get("evidence_pack_before_frascati"), dict):
        return _safe_pack(nlp_result["evidence_pack_before_frascati"]), "top_level_cir_structured.evidence_pack_before_frascati"

    if isinstance(nlp_result.get("merged_evidence_pack_before_frascati"), dict):
        return _safe_pack(nlp_result["merged_evidence_pack_before_frascati"]), "merged_evidence_pack_before_frascati"

    if isinstance(nlp_result.get("evidence_pack_before_frascati"), dict):
        return _safe_pack(nlp_result["evidence_pack_before_frascati"]), "top_level.evidence_pack_before_frascati"

    return _safe_pack({}), "not_found"


def get_current_pack_for_reformulation(nlp_result: Dict[str, Any]) -> Tuple[Dict[str, List[Dict[str, Any]]], str]:
    """
    Pour le dossier courant brut :
    on utilise le pack qualifié après Frascati si disponible.
    """
    if not isinstance(nlp_result, dict):
        return _safe_pack({}), "empty"

    fg = nlp_result.get("frascati_guard")
    if isinstance(fg, dict) and isinstance(fg.get("qualified_pack_for_ennodiagnostic"), dict):
        return _safe_pack(fg["qualified_pack_for_ennodiagnostic"]), "frascati_guard.qualified_pack_for_ennodiagnostic"

    for key in [
        "multi_document_evidence_pack_for_ennodiagnostic",
        "merged_evidence_pack_for_ennodiagnostic",
        "evidence_pack_for_ennodiagnostic",
        "merged_evidence_pack_before_frascati",
        "evidence_pack_before_frascati",
    ]:
        if isinstance(nlp_result.get(key), dict):
            return _safe_pack(nlp_result[key]), key

    return _safe_pack({}), "not_found"


def item_text(item: Dict[str, Any]) -> str:
    title = clean_text(item.get("section_title") or item.get("title") or "")
    label = clean_text(item.get("section_label") or "")
    text = clean_text(item.get("text") or item.get("source_text") or "")
    parts = []
    if label:
        parts.append(label)
    if title and norm(title) not in norm(text[:260]):
        parts.append(title)
    parts.append(text)
    return "\n".join([p for p in parts if p]).strip()


def split_sentences(text: str) -> List[str]:
    raw = re.split(r"(?<=[.!?])\s+|\n+", clean_text(text))
    return [x.strip() for x in raw if len(x.strip()) > 25]


def words(text: str, limit: int = 80) -> List[str]:
    ws = re.findall(r"\b[\wÀ-ÿ'-]{4,}\b", norm(text))
    ws = [w for w in ws if w not in STOP_WORDS]
    freq: Dict[str, int] = {}
    for w in ws:
        freq[w] = freq.get(w, 0) + 1
    return [w for w, _ in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]


def detect_style_features(text: str, role: str = "") -> Dict[str, Any]:
    sents = split_sentences(text)
    lengths = [len(s.split()) for s in sents] or [0]
    low = norm(text)

    connectors = [c for c in STYLE_CONNECTORS if norm(c) in low]
    role_patterns = []
    for pat in CIR_WRITING_PATTERNS.get(role, []):
        if norm(pat) in low:
            role_patterns.append(pat)

    return {
        "sentences_count": len(sents),
        "avg_sentence_words": round(sum(lengths) / max(1, len(lengths)), 1),
        "connectors": connectors[:12],
        "role_patterns": role_patterns[:12],
        "keywords": words(text, limit=35),
        "tone": "technique_cir_formel",
    }


def pack_to_style_examples(
    pack: Dict[str, Any],
    organisme: str,
    project: str,
    year: str,
    source_file: str = "",
    max_examples_per_role: int = 8,
    domain_detection: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    examples: List[Dict[str, Any]] = []
    per_role: Dict[str, int] = {}
    domain_detection = _normalize_domain_detection(domain_detection, source=(domain_detection or {}).get("domain_source_in_style_memory", "unknown"))
    domain_key = domain_detection.get("domain_key", "unknown")
    domain_label = domain_detection.get("domain_label") or domain_detection.get("display_label") or "Domaine non détecté"

    for pack_key in PACK_KEYS:
        role = ROLE_BY_PACK.get(pack_key, "general")
        arr = pack.get(pack_key) or []
        for idx, item in enumerate(arr):
            if not isinstance(item, dict):
                continue
            txt = item_text(item)
            if len(txt) < 120:
                continue

            if per_role.get(role, 0) >= max_examples_per_role:
                continue

            example = {
                "example_id": sha(f"{organisme}|{project}|{year}|{role}|{txt[:2000]}"),
                "organisme": organisme,
                "project": project,
                "year": str(year),
                "source_file": source_file,
                "role": role,
                "domain_key": domain_key,
                "domain_label": domain_label,
                "domain_detection": domain_detection,
                "pack_key": pack_key,
                "section_title": item.get("section_title") or item.get("title"),
                "section_type": item.get("section_type"),
                "section_label": item.get("section_label"),
                "text": short_text(txt, 1800),
                "style_features": detect_style_features(txt, role=role),
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "validated": True,
                "use_for_style_only": True,
                "warning": "Exemple utilisé uniquement pour le style, pas comme source factuelle du nouveau dossier.",
            }
            examples.append(example)
            per_role[role] = per_role.get(role, 0) + 1

    return examples


def empty_memory(organisme: str) -> Dict[str, Any]:
    return {
        "version": "cir_style_memory_v1",
        "organisme": organisme,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "principle": (
            "Mémoire rédactionnelle : les anciens CIR servent uniquement d'exemples de style. "
            "Le LLM ne doit pas copier ni réutiliser les faits historiques comme faits du nouveau dossier."
        ),
        "examples": [],
        "stats": {
            "examples_count": 0,
            "roles": {},
            "projects": {},
            "years": {},
        },
    }


def recompute_stats(memory: Dict[str, Any]) -> Dict[str, Any]:
    examples = memory.get("examples") or []
    roles: Dict[str, int] = {}
    projects: Dict[str, int] = {}
    years: Dict[str, int] = {}
    domains: Dict[str, int] = {}

    for ex in examples:
        roles[ex.get("role") or "unknown"] = roles.get(ex.get("role") or "unknown", 0) + 1
        projects[ex.get("project") or "unknown"] = projects.get(ex.get("project") or "unknown", 0) + 1
        years[str(ex.get("year") or "unknown")] = years.get(str(ex.get("year") or "unknown"), 0) + 1
        dk = ex.get("domain_key") or (ex.get("domain_detection") or {}).get("domain_key") or "unknown"
        dl = ex.get("domain_label") or (ex.get("domain_detection") or {}).get("domain_label") or dk
        domains[f"{dk} | {dl}"] = domains.get(f"{dk} | {dl}", 0) + 1

    memory["updated_at"] = datetime.now().isoformat(timespec="seconds")
    memory["stats"] = {
        "examples_count": len(examples),
        "roles": dict(sorted(roles.items())),
        "projects": dict(sorted(projects.items())),
        "years": dict(sorted(years.items())),
        "domains": dict(sorted(domains.items())),
    }
    return memory


def load_style_memory(organisme: str) -> Dict[str, Any]:
    path = style_memory_path(organisme)
    memory = read_json(path, None)
    if not isinstance(memory, dict):
        memory = empty_memory(organisme)
    return recompute_stats(memory)


def save_style_memory(organisme: str, memory: Dict[str, Any]) -> Path:
    memory = recompute_stats(memory)
    path = style_memory_path(organisme)
    write_json(path, memory)
    return path


def register_cir_style_from_nlp_result(
    organisme: str,
    project: str,
    year: str,
    nlp_result_path: str | Path,
    source_file: str = "",
    max_examples_per_role: int = 8,
) -> Dict[str, Any]:
    nlp_path = Path(nlp_result_path)
    if not nlp_path.exists():
        raise FileNotFoundError(f"nlp_result introuvable : {nlp_path}")

    nlp = read_json(nlp_path, {})
    pack, pack_source = get_cir_pack_without_frascati(nlp)
    domain_detection = detect_domain_for_style_memory(nlp, pack=pack)

    examples = pack_to_style_examples(
        pack=pack,
        organisme=organisme,
        project=project,
        year=year,
        source_file=source_file,
        max_examples_per_role=max_examples_per_role,
        domain_detection=domain_detection,
    )

    if not examples:
        raise RuntimeError(
            "Aucun exemple de style CIR trouvé. Vérifie que le nlp_result contient "
            "des sections CIR structurées avant Frascati."
        )

    memory = load_style_memory(organisme)
    existing_ids = {ex.get("example_id") for ex in memory.get("examples") or []}

    added = []
    skipped = []
    updated_domain = []

    # Index existant par id pour mettre à jour les anciens exemples sans domaine.
    existing_by_id = {
        ex.get("example_id"): ex
        for ex in memory.get("examples") or []
        if isinstance(ex, dict) and ex.get("example_id")
    }

    for ex in examples:
        ex_id = ex["example_id"]
        if ex_id in existing_ids:
            old_ex = existing_by_id.get(ex_id)
            if isinstance(old_ex, dict):
                old_domain = old_ex.get("domain_key") or (old_ex.get("domain_detection") or {}).get("domain_key") or "unknown"
                new_domain = ex.get("domain_key") or (ex.get("domain_detection") or {}).get("domain_key") or "unknown"

                # Mise à jour ciblée : l'exemple existe déjà, mais il n'avait pas encore le domaine.
                if old_domain in {"", None, "unknown"} and new_domain not in {"", None, "unknown"}:
                    old_ex["domain_key"] = ex.get("domain_key")
                    old_ex["domain_label"] = ex.get("domain_label")
                    old_ex["domain_detection"] = ex.get("domain_detection")
                    old_ex["updated_at"] = datetime.now().isoformat(timespec="seconds")
                    updated_domain.append(old_ex)
                else:
                    # Même si le domaine reste unknown, on stocke le domain_detection pour debug.
                    if not old_ex.get("domain_detection") and ex.get("domain_detection"):
                        old_ex["domain_detection"] = ex.get("domain_detection")
                        old_ex["domain_key"] = ex.get("domain_key")
                        old_ex["domain_label"] = ex.get("domain_label")
                        old_ex["updated_at"] = datetime.now().isoformat(timespec="seconds")
                        updated_domain.append(old_ex)
            skipped.append(ex)
            continue

        memory.setdefault("examples", []).append(ex)
        existing_ids.add(ex_id)
        added.append(ex)

    path = save_style_memory(organisme, memory)

    report = {
        "ok": True,
        "version": "register_cir_style_memory_v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "organisme": organisme,
        "project": project,
        "year": str(year),
        "nlp_result_path": str(nlp_path),
        "source_file": source_file,
        "pack_source": pack_source,
        "domain_detection": domain_detection,
        "examples_found": len(examples),
        "examples_added": len(added),
        "examples_updated_domain": len(updated_domain),
        "examples_skipped_duplicates": len(skipped),
        "memory_path": str(path),
        "memory_stats": load_style_memory(organisme).get("stats"),
    }

    write_json(style_memory_dir(organisme) / f"register_{slug(project)}_{year}.json", report)
    return report


def score_style_example(example: Dict[str, Any], target_role: str, query_text: str, project: str = "", target_domain_key: str = "unknown") -> float:
    ex_role = example.get("role") or ""
    ex_text = example.get("text") or ""
    score = 0.0

    if ex_role == target_role:
        score += 2.0
    elif target_role == "verrou" and ex_role in {"limite", "etat_art"}:
        score += 0.9
    elif target_role == "methode" and ex_role in {"resultat", "contribution"}:
        score += 0.5
    elif target_role == "resultat" and ex_role in {"methode", "contribution"}:
        score += 0.5
    else:
        score += 0.1

    q_words = set(words(query_text, 80))
    e_words = set(words(ex_text, 80))
    if q_words and e_words:
        score += len(q_words & e_words) / max(1, len(q_words | e_words)) * 3.0

    ex_domain_key = example.get("domain_key") or (example.get("domain_detection") or {}).get("domain_key") or "unknown"
    if target_domain_key and target_domain_key != "unknown":
        if ex_domain_key == target_domain_key:
            score += 2.2
        elif ex_domain_key == "unknown":
            score += 0.1
        else:
            score -= 1.8

    if project and slug(example.get("project")) == slug(project):
        score += 0.6

    # favoriser exemples riches mais pas trop longs
    length = len(ex_text)
    if 250 <= length <= 1800:
        score += 0.4

    return round(score, 4)


def retrieve_style_examples(
    organisme: str,
    target_role: str,
    query_text: str,
    project: str = "",
    top_k: int = 5,
    target_domain_key: str = "unknown",
    strict_domain: bool = True,
) -> List[Dict[str, Any]]:
    memory = load_style_memory(organisme)
    examples = [ex for ex in memory.get("examples") or [] if isinstance(ex, dict)]

    if target_domain_key and target_domain_key != "unknown" and strict_domain:
        same_domain = [
            ex for ex in examples
            if (ex.get("domain_key") or (ex.get("domain_detection") or {}).get("domain_key") or "unknown") == target_domain_key
        ]
        if same_domain:
            examples = same_domain

    scored = []
    for ex in examples:
        scored.append((score_style_example(ex, target_role, query_text, project=project, target_domain_key=target_domain_key), ex))

    scored.sort(key=lambda x: x[0], reverse=True)
    out = []
    seen = set()
    for sc, ex in scored:
        if ex.get("example_id") in seen:
            continue
        seen.add(ex.get("example_id"))
        y = dict(ex)
        y["style_match_score"] = sc
        out.append(y)
        if len(out) >= top_k:
            break
    return out


def build_style_block(examples: List[Dict[str, Any]], max_chars_per_example: int = 900) -> str:
    if not examples:
        return "Aucun exemple de style CIR disponible."

    lines = []
    lines.append("EXEMPLES DE STYLE CIR VALIDÉS")
    lines.append(
        "Ces exemples servent uniquement à imiter le style, la structure argumentative et le vocabulaire. "
        "Ils ne doivent pas être copiés et leurs faits ne doivent pas être réutilisés comme faits du nouveau dossier."
    )

    for i, ex in enumerate(examples, 1):
        feat = ex.get("style_features") or {}
        lines.append(f"\n[STYLE {i}] rôle={ex.get('role')} | domaine={ex.get('domain_label') or (ex.get('domain_detection') or {}).get('display_label')} | projet={ex.get('project')} | année={ex.get('year')}")
        if ex.get("section_title"):
            lines.append(f"Titre section : {ex.get('section_title')}")
        if feat.get("connectors"):
            lines.append("Connecteurs fréquents : " + ", ".join(feat.get("connectors") or []))
        if feat.get("role_patterns"):
            lines.append("Formulations typiques : " + ", ".join(feat.get("role_patterns") or []))
        lines.append("Extrait de style :")
        lines.append(short_text(ex.get("text"), max_chars_per_example))

    return "\n".join(lines).strip()


def pack_role_items(pack: Dict[str, Any], pack_keys: List[str], max_items: int = 12) -> List[Dict[str, Any]]:
    out = []
    for key in pack_keys:
        role = ROLE_BY_PACK.get(key, key)
        for item in pack.get(key) or []:
            if not isinstance(item, dict):
                continue
            txt = item_text(item)
            if len(txt) < 40:
                continue
            y = dict(item)
            y["_role"] = role
            y["_pack_key"] = key
            y["_text"] = txt
            out.append(y)
    # tri simple : verrous / direct / score
    def sc(x):
        try:
            rank = float(x.get("rank_score") or 0)
        except Exception:
            rank = 0
        try:
            conf = float(x.get("confidence") or x.get("model_confidence") or 0)
        except Exception:
            conf = 0
        return rank + conf
    out = sorted(out, key=sc, reverse=True)
    return out[:max_items]


def build_current_sources_block(items: List[Dict[str, Any]], max_chars: int = 1200) -> str:
    if not items:
        return "Aucune source courante disponible."

    lines = []
    for i, item in enumerate(items, 1):
        role = item.get("_role") or item.get("role") or ""
        doc = item.get("document") or ""
        title = item.get("section_title") or ""
        fr = item.get("frascati") or {}
        lines.append(f"[SOURCE {i}] rôle={role} | document={doc} | section={title}")
        if fr:
            lines.append(f"Frascati: décision={fr.get('decision')} | score={fr.get('frascati_score')}")
        lines.append(short_text(item.get("_text") or item_text(item), max_chars))
        lines.append("")
    return "\n".join(lines).strip()


def build_reformulation_prompt(
    section_name: str,
    target_role: str,
    current_sources: List[Dict[str, Any]],
    style_examples: List[Dict[str, Any]],
    has_cir_memory: bool = True,
) -> str:
    current_block = build_current_sources_block(current_sources)
    style_block = build_style_block(style_examples)

    return f"""
Tu es EnnoDiagnostic, agent de rédaction CIR/R&D.

Objectif :
Rédiger la section suivante du diagnostic en style CIR :
{section_name}

Règle principale :
- Les SOURCES COURANTES sont les seules sources factuelles autorisées.
- Les EXEMPLES DE STYLE CIR servent uniquement à apprendre la manière de rédiger.
- Tu ne dois jamais copier une phrase entière d'un ancien CIR.
- Tu ne dois jamais reprendre un fait d'un ancien CIR s'il n'est pas présent dans les sources courantes.
- Si une information manque, écris clairement "à confirmer par le consultant" ou "preuve à compléter".
- Le style doit être technique, précis, sobre, orienté CIR/R&D.

Règle temporelle importante :
- N'écris pas automatiquement au futur.
- Si les sources décrivent des travaux déjà réalisés, rédige au passé composé ou au présent analytique.
- Si les sources décrivent une observation, écris : "les documents indiquent que", "les essais montrent que", "les mesures mettent en évidence que".
- Utilise le futur uniquement si la source dit explicitement que l'action est prévue, à réaliser, ou non encore effectuée.
- Ne transforme pas un essai déjà réalisé en action future.

Ce que tu dois imiter depuis les exemples :
- structure argumentative ;
- vocabulaire R&D ;
- manière d'introduire l'objectif, l'incertitude, les travaux et les résultats ;
- niveau de détail ;
- prudence dans les conclusions.

Règle domaine :
- Les exemples de style fournis sont filtrés selon le domaine technique détecté par le NLP.
- Respecte le vocabulaire du domaine courant.
- Ne mélange pas des formulations très spécifiques d'un autre domaine technique.

Ce que tu ne dois pas imiter :
- noms, chiffres, résultats ou faits historiques absents des sources courantes ;
- formulation copiée mot à mot ;
- conclusion d'éligibilité automatique.

SOURCES COURANTES :
{current_block}

{style_block}

Réponse attendue :
- Titre markdown court.
- Texte rédigé en français professionnel.
- 2 à 5 paragraphes selon la richesse des sources.
- Une courte liste "Points à valider" si nécessaire.
""".strip()


def _get_llm():
    """
    Charge le client LLM réel du projet.
    Ton projet a évolué : le LLM n'est pas forcément dans modules.RAG.gemini_client.
    On teste plusieurs emplacements compatibles.
    """

    # 1) Nouveau gateway LLM recommandé
    try:
        from modules.LLM.llm_client import LLMClient
        return LLMClient()
    except Exception:
        pass

    # 2) Variante possible du gateway
    try:
        from modules.LLM.llm_client import EnnoLLMClient
        return EnnoLLMClient()
    except Exception:
        pass

    # 3) Client Gemini dans modules/LLM
    try:
        from modules.LLM.gemini_client import GeminiClient
        return GeminiClient()
    except Exception:
        pass

    try:
        from modules.LLM.gemini_client import GeminiLLM
        return GeminiLLM()
    except Exception:
        pass

    # 4) Ancien emplacement dans modules/RAG
    try:
        from modules.RAG.gemini_client import GeminiClient
        return GeminiClient()
    except Exception:
        pass

    try:
        from modules.RAG.gemini_client import GeminiLLM
        return GeminiLLM()
    except Exception:
        pass

    # 5) Client à la racine éventuelle
    try:
        from gemini_client import GeminiClient
        return GeminiClient()
    except Exception:
        pass

    raise ImportError(
        "Aucun client LLM trouvé. Attendu : modules.LLM.llm_client.LLMClient "
        "ou modules.LLM.gemini_client.GeminiClient."
    )


def _llm_generate(llm, prompt: str, temperature: float, max_output_tokens: int) -> str:
    """
    Appelle le LLM en supportant plusieurs signatures :
    - generate(prompt=..., temperature=..., max_output_tokens=...)
    - generate(prompt)
    - chat(prompt)
    - complete(prompt)
    """
    if hasattr(llm, "generate"):
        try:
            return llm.generate(
                prompt=prompt,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                retries=2,
            )
        except TypeError:
            try:
                return llm.generate(
                    prompt=prompt,
                    temperature=temperature,
                    max_tokens=max_output_tokens,
                )
            except TypeError:
                try:
                    return llm.generate(prompt=prompt)
                except TypeError:
                    return llm.generate(prompt)

    if hasattr(llm, "chat"):
        try:
            return llm.chat(prompt=prompt)
        except TypeError:
            return llm.chat(prompt)

    if hasattr(llm, "complete"):
        try:
            return llm.complete(prompt=prompt)
        except TypeError:
            return llm.complete(prompt)

    raise AttributeError("Le client LLM ne possède pas generate(), chat() ou complete().")


def generate_section_with_style(
    organisme: str,
    project: str,
    section_name: str,
    target_role: str,
    current_sources: List[Dict[str, Any]],
    temperature: float = 0.12,
    max_output_tokens: int = 1200,
    use_llm: bool = True,
    target_domain_key: str = "unknown",
) -> Dict[str, Any]:
    query = "\n".join(item.get("_text") or item_text(item) for item in current_sources)
    examples = retrieve_style_examples(
        organisme=organisme,
        target_role=target_role,
        query_text=query,
        project=project,
        top_k=5,
        target_domain_key=target_domain_key,
        strict_domain=True,
    )

    prompt = build_reformulation_prompt(
        section_name=section_name,
        target_role=target_role,
        current_sources=current_sources,
        style_examples=examples,
        has_cir_memory=bool(examples),
    )

    if not use_llm:
        content = (
            f"## {section_name}\n\n"
            "Mode LLM désactivé. Sources prêtes pour reformulation.\n\n"
            "### Sources principales\n"
            + "\n".join(f"- {short_text(x.get('_text') or item_text(x), 220)}" for x in current_sources[:6])
        )
        status = "llm_disabled"
    else:
        try:
            llm = _get_llm()
            content = _llm_generate(
                llm=llm,
                prompt=prompt,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
            )
            status = "ok"
        except Exception as e:
            content = (
                f"## {section_name}\n\n"
                f"Erreur LLM : {e}\n\n"
                "Sources disponibles mais reformulation non générée."
            )
            status = "llm_error"

    return {
        "section_name": section_name,
        "target_role": target_role,
        "status": status,
        "content": content,
        "current_sources_count": len(current_sources),
        "target_domain_key": target_domain_key,
        "style_examples_count": len(examples),
        "style_examples": examples,
        "prompt_preview": prompt[:4000],
    }


def rewrite_diagnostic_with_style_memory(
    organisme: str,
    project: str,
    year: str,
    nlp_result_path: Optional[str | Path] = None,
    use_llm: bool = True,
) -> Dict[str, Any]:
    nlp_path = Path(nlp_result_path) if nlp_result_path else current_nlp_default_path(organisme, project, year)
    if not nlp_path.exists():
        raise FileNotFoundError(f"nlp_result courant introuvable : {nlp_path}")

    nlp = read_json(nlp_path, {})
    pack, pack_source = get_current_pack_for_reformulation(nlp)
    current_domain_detection = detect_domain_for_style_memory(nlp, pack=pack)
    current_domain_key = current_domain_detection.get("domain_key", "unknown")

    sections_config = [
        {
            "key": "objectif_global",
            "section_name": "Objectif global reformulé en style CIR",
            "target_role": "objectif",
            "pack_keys": ["objectifs_locaux", "limites_locales", "verrous_rnd_locaux", "methodes_locales"],
            "max_items": 12,
            "max_tokens": 1000,
        },
        {
            "key": "verrous_rnd",
            "section_name": "Verrous R&D reformulés en style CIR",
            "target_role": "verrou",
            "pack_keys": ["verrous_rnd_locaux", "limites_locales", "etat_art_local", "objectifs_locaux"],
            "max_items": 14,
            "max_tokens": 1400,
        },
        {
            "key": "demarche_experimentale",
            "section_name": "Démarche expérimentale reformulée en style CIR",
            "target_role": "methode",
            "pack_keys": ["methodes_locales", "parametres_locaux", "resultats_locaux", "verrous_rnd_locaux"],
            "max_items": 14,
            "max_tokens": 1200,
        },
        {
            "key": "resultats_metriques",
            "section_name": "Résultats et métriques reformulés en style CIR",
            "target_role": "resultat",
            "pack_keys": ["resultats_locaux", "methodes_locales", "parametres_locaux"],
            "max_items": 12,
            "max_tokens": 1100,
        },
        {
            "key": "contribution_points_validation",
            "section_name": "Contribution potentielle et points à valider",
            "target_role": "contribution",
            "pack_keys": ["contributions_locales", "resultats_locaux", "limites_locales", "verrous_rnd_locaux"],
            "max_items": 12,
            "max_tokens": 1000,
        },
    ]

    generated_sections: Dict[str, Any] = {}

    for cfg in sections_config:
        current_sources = pack_role_items(
            pack=pack,
            pack_keys=cfg["pack_keys"],
            max_items=cfg["max_items"],
        )
        generated_sections[cfg["key"]] = generate_section_with_style(
            organisme=organisme,
            project=project,
            section_name=cfg["section_name"],
            target_role=cfg["target_role"],
            current_sources=current_sources,
            max_output_tokens=cfg["max_tokens"],
            use_llm=use_llm,
            target_domain_key=current_domain_key,
        )

    full_markdown_parts = []
    for cfg in sections_config:
        sec = generated_sections.get(cfg["key"]) or {}
        full_markdown_parts.append(sec.get("content") or "")
    full_markdown = "\n\n---\n\n".join([x for x in full_markdown_parts if x.strip()])

    memory = load_style_memory(organisme)

    report = {
        "ok": True,
        "version": "reformulation_rnd_style_cir_v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "organisme": organisme,
        "project": project,
        "year": str(year),
        "nlp_result_path": str(nlp_path),
        "current_pack_source": pack_source,
        "current_domain_detection": current_domain_detection,
        "style_memory_path": str(style_memory_path(organisme)),
        "style_memory_stats": memory.get("stats"),
        "principle": (
            "La mémoire CIR est utilisée comme style rédactionnel uniquement. "
            "Les faits doivent provenir du dossier courant."
        ),
        "sections": generated_sections,
        "full_markdown": full_markdown,
    }

    out_path = reformulation_output_path(organisme, project, year)
    write_json(out_path, report)
    return report
