# -*- coding: utf-8 -*-
from __future__ import annotations

"""Universal, evidence-grounded scientific query planner for EnnoScholar.

The module is deliberately domain-agnostic:
- it never contains customer/project/domain vocabulary;
- it separates local identifiers from transferable scientific concepts;
- optional LLM use is constrained by verbatim evidence spans;
- generated queries are rebuilt deterministically from validated concepts;
- query guards remove metadata/noise and reject local identifiers;
- provider adapters only change query length/shape, never scientific meaning;
- retrieval feedback detects topic drift and can produce precision rescue queries.
"""

import hashlib
import importlib
import json
import os
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


PLANNER_VERSION = "v166_3_role_contract_query_planner"

_ADMIN_NOISE = {
    "cir", "frascati", "consultant", "dossier", "document", "documents",
    "source", "sources", "preuve", "preuves", "evidence", "project", "projet",
    "verrou", "verrous", "diagnostic", "ennodiagnostic", "ennoscholar",
    "json", "api", "pdf", "docx", "session", "workflow", "frontend", "backend",
}

_LITERAL_NOISE = {
    "true", "false", "null", "none", "undefined", "yes", "no", "oui", "non",
    "nan", "inf", "infinity",
}

# Function words are removed only when building search queries. They stay in
# the evidence/source phrases used for traceability. This prevents malformed
# search strings such as ``impact of and ... on ...`` without maintaining a
# domain-specific vocabulary.
_FUNCTION_WORDS = {
    "a", "an", "the", "and", "or", "of", "on", "in", "to", "for", "from",
    "with", "without", "under", "over", "into", "between", "through", "by",
    "at", "as", "than", "that", "this", "these", "those",
    "de", "du", "des", "d", "la", "le", "les", "un", "une", "et", "ou",
    "sur", "dans", "pour", "par", "avec", "sans", "sous", "entre", "vers",
    "au", "aux", "en", "ce", "cet", "cette", "ces",
}

_WEAK_RELATIONAL = {
    "impact", "effect", "effects", "influence", "performance", "performances",
    "condition", "conditions", "limitation", "limitations", "limit", "limits",
    "validation", "evaluation", "comparison", "comparaison", "study", "analysis",
    "research", "result", "results", "problem", "issue", "method", "methods",
    "model", "models", "system", "systems", "approach", "technical", "scientific",
    "severe", "severes", "severe", "robustness", "generalization", "generalisation",
}

_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_SESSION_RE = re.compile(r"\b(?:session|run|trace|request|job)[-_:/ ]?[0-9a-zA-Z-]{6,}\b", re.I)
_MIXED_ID_RE = re.compile(r"\b(?=[A-Za-z0-9_-]{5,}\b)(?=[A-Za-z0-9_-]*[A-Za-z])(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]+\b")
_TOKEN_RE = re.compile(r"[A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9+./_-]{1,}")

# Public fields kept for backward compatibility with the existing ranker.
_ROLE_FIELDS = (
    "scientific_object",
    "phenomena",
    "variables",
    "methods",
    "constraints",
    "validation_concepts",
)

# V166.3 extraction contract. Variables are split by causal/experimental role
# before being merged back into the legacy ``variables`` field.
_EXTRACT_ROLE_FIELDS = (
    "scientific_object",
    "phenomena",
    "independent_variables",
    "response_variables",
    "operating_conditions",
    "methods",
    "validation_concepts",
)


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = re.sub(r"[^a-z0-9+./_-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _clean(value: Any, max_chars: int = 600) -> str:
    text = str(value or "").replace("\x00", " ")
    text = _UUID_RE.sub(" ", text)
    text = _SESSION_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def _unique(values: Iterable[str], limit: int = 20) -> List[str]:
    out: List[str] = []
    seen = set()
    for raw in values:
        value = _clean(raw, 160)
        key = _norm(value)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(value)
        if len(out) >= limit:
            break
    return out


def _tokens(value: Any) -> List[str]:
    out: List[str] = []
    for token in _TOKEN_RE.findall(_clean(value, 4000)):
        key = _norm(token)
        if (
            len(key) < 2
            or key in _ADMIN_NOISE
            or key in _LITERAL_NOISE
            or key in _FUNCTION_WORDS
        ):
            continue
        if _UUID_RE.fullmatch(token):
            continue
        out.append(token)
    return out


def _strip_structural_noise(text: str) -> str:
    text = _clean(text, 8000)
    text = re.sub(r"\b(?:true|false|null|none|undefined)\b", " ", text, flags=re.I)
    text = re.sub(r"\b[a-f0-9]{24,}\b", " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    return text



def _source_evidence(intent: Mapping[str, Any]) -> str:
    """Build the planner evidence from the *actual* V161 EnnoScholar contract.

    V161 exposes linked passages as ``source_passages`` and under
    ``source_basis.linked_passages_excerpt``.  Older branches used
    ``source_text_excerpt`` / ``context_relevant_excerpt``.  We support both
    shapes without inventing or flattening arbitrary metadata.
    """
    source_basis = intent.get("source_basis") if isinstance(intent.get("source_basis"), Mapping) else {}
    parts: List[str] = []

    def add(value: Any, max_items: int = 6) -> None:
        if value is None:
            return
        if isinstance(value, str):
            value = _strip_structural_noise(value)
            if value:
                parts.append(value)
            return
        if isinstance(value, Mapping):
            # Only known text-bearing fields; never stringify arbitrary JSON
            # booleans/ids into scientific evidence.
            for key in (
                "text", "source_text", "excerpt", "content", "passage",
                "description", "title", "section_title",
            ):
                if isinstance(value.get(key), str):
                    add(value.get(key))
            return
        if isinstance(value, (list, tuple)):
            for item in list(value)[:max_items]:
                add(item)

    add(intent.get("verrou_title") or intent.get("original_title"))
    add(source_basis.get("verrou_title") or source_basis.get("title"))
    add(intent.get("source_passages"), max_items=6)
    add(source_basis.get("linked_passages_excerpt"), max_items=6)
    add(source_basis.get("source_text_excerpt"), max_items=6)
    add(source_basis.get("context_relevant_excerpt"), max_items=3)
    add(source_basis.get("relevant_diagnostic_context"), max_items=3)
    add(intent.get("scientific_problem"))

    # Diagnostic context is a low-priority supplement. Keep only sentences
    # sharing at least two title tokens, so another operation/project cannot
    # contaminate the query plan.
    diagnostic = str(intent.get("diagnostic_context_text") or "")
    if diagnostic:
        title_tokens = {
            token for token in _norm(intent.get("verrou_title") or "").split()
            if len(token) >= 3
        }
        kept_sentences: List[str] = []
        for sentence in re.split(r"(?<=[.!?;])\s+|\n+", diagnostic):
            st = {token for token in _norm(sentence).split() if len(token) >= 3}
            if title_tokens and len(title_tokens & st) >= 2:
                kept_sentences.append(sentence)
            if len(kept_sentences) >= 3:
                break
        add(kept_sentences, max_items=3)

    # Preserve order but remove duplicate snippets.
    unique_parts: List[str] = []
    seen = set()
    for part in parts:
        key = _norm(part)
        if not key or key in seen:
            continue
        seen.add(key)
        unique_parts.append(part)

    return _strip_structural_noise("\n".join(unique_parts))[:9000]

def _evidence_fingerprint(intent: Mapping[str, Any]) -> str:
    payload = _source_evidence(intent)
    return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()[:20]


def _appears_in_evidence(phrase: str, evidence: str) -> bool:
    p = _norm(phrase)
    e = _norm(evidence)
    if not p or not e:
        return False
    if p in e:
        return True
    pt = [x for x in p.split() if len(x) >= 3]
    et = set(e.split())
    if not pt:
        return False
    return len(set(pt) & et) / max(1, len(set(pt))) >= 0.8



def _explicit_local_identifiers(intent: Mapping[str, Any], evidence: str) -> List[str]:
    """Return only high-confidence local identifiers.

    The V161 ``local_names`` field contains *all* acronyms found in evidence,
    including legitimate scientific acronyms.  Therefore pure acronyms are not
    automatically blocked.  Mixed alpha+digit product/model identifiers are
    high-confidence local names, while project/tool names are only considered
    when an upstream field explicitly marks them as such.  The LLM planner may
    additionally classify a pure-name acronym as local from its evidence.
    """
    candidates: List[str] = []

    # Explicit upstream local/tool fields, when available.
    for key in ("project_tool_terms", "local_identifiers"):
        value = intent.get(key)
        if isinstance(value, list):
            candidates.extend(
                str(x.get("value") if isinstance(x, Mapping) else x)
                for x in value
            )

    # Mixed alpha+digit identifiers are usually machine/product/model codes.
    # The minimum length avoids short scientific formulae.
    candidates.extend(_MIXED_ID_RE.findall(evidence))

    out: List[str] = []
    for item in candidates:
        item = _clean(item, 80)
        ni = _norm(item)
        if not ni or ni in _ADMIN_NOISE or ni in _LITERAL_NOISE:
            continue
        if not _appears_in_evidence(item, evidence):
            continue
        out.append(item)
    return _unique(out, 16)

def _load_llm_client_class():
    for module_name in (
        "modules.LLM.llm_client",
        "modules.LLM",
        "backend_api.modules.LLM.llm_client",
    ):
        try:
            module = importlib.import_module(module_name)
            cls = getattr(module, "LLMClient", None)
            if cls is not None:
                return cls
        except Exception:
            continue
    return None


def _llm_enabled() -> bool:
    raw = str(os.getenv("ENNOSCHOLAR_QUERY_PLANNER_LLM_ENABLED", "1") or "1").strip().lower()
    return raw in {"1", "true", "yes", "on", "oui"}


def _planner_schema() -> Dict[str, Any]:
    concept = {
        "type": "object",
        "properties": {
            "term_en": {"type": "string"},
            "source_phrase": {"type": "string"},
        },
        "required": ["term_en", "source_phrase"],
        "additionalProperties": False,
    }
    return {
        "title": "ennoscholar_scientific_query_plan_v166_3",
        "type": "object",
        "properties": {
            **{field: {"type": "array", "items": concept} for field in _EXTRACT_ROLE_FIELDS},
            "local_identifiers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "value": {"type": "string"},
                        "source_phrase": {"type": "string"},
                    },
                    "required": ["value", "source_phrase"],
                    "additionalProperties": False,
                },
            },
            "ambiguities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "source_term": {"type": "string"},
                        "resolved_en": {"type": "string"},
                        "source_phrase": {"type": "string"},
                    },
                    "required": ["source_term", "resolved_en", "source_phrase"],
                    "additionalProperties": False,
                },
            },
        },
        "required": [*_EXTRACT_ROLE_FIELDS, "local_identifiers", "ambiguities"],
        "additionalProperties": False,
    }


def _call_llm_planner(evidence: str, explicit_local_ids: Sequence[str]) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    if not _llm_enabled():
        return None, {"used": False, "reason": "disabled_by_env"}
    Client = _load_llm_client_class()
    if Client is None:
        return None, {"used": False, "reason": "llm_client_unavailable"}

    prompt = f"""
Tu extrais l'intention scientifique d'un verrou pour préparer une recherche bibliographique.
Le système est multi-domaines : aucune ontologie métier, aucun client et aucun projet ne sont connus à l'avance.

RÈGLES DE PREUVE
1. Utilise uniquement PREUVES.
2. Chaque source_phrase doit être un extrait VERBATIM réellement présent dans PREUVES.
3. term_en est une traduction/désambiguïsation scientifique fidèle de source_phrase, jamais une invention.
4. Si PREUVES ne permettent pas un rôle, retourne [] pour ce rôle.

CONTRAT DES RÔLES
- scientific_object : système, composant, matériau, population, procédé ou objet TECHNIQUE étudié.
  Ce champ ne doit jamais être un effet, une incertitude, une condition, une simple sortie ou une grandeur mesurée.
- independent_variables : paramètres/quantités que les essais, calculs ou conditions font varier ou comparent.
- response_variables : quantités/observables dont on mesure, prédit ou analyse la réponse.
- operating_conditions : contexte opératoire ou contraintes imposées ; ne pas y mettre l'objet ni une grandeur de réponse.
- phenomena : comportement, mécanisme ou relation scientifique observée ; écrire une expression nominale autonome.
- methods : protocoles, essais, méthodes de mesure, simulation ou analyse explicitement présents.
- validation_concepts : comparaison, robustesse, répétabilité, mesures expérimentales ou autre logique de validation explicitement présente.

QUALITÉ DES TERMES
5. Chaque term_en doit être une expression scientifique AUTONOME et directement recherchable, idéalement 1 à 6 mots.
6. N'utilise jamais comme concept autonome des fragments génériques tels que « impact of », « effect of », « influence of », « severe conditions », « output », « performance » ou « uncertainty » s'ils ne nomment pas à eux seuls une entité scientifique précise.
7. Pour une grandeur, conserve son contexte lorsqu'il est présent : évite des termes vagues comme « temperature », « flow » ou « output » si PREUVES permettent une expression plus précise.
8. Ne fabrique pas de causalité absente de PREUVES. Sépare seulement les rôles déjà décrits.

IDENTIFIANTS ET AMBIGUÏTÉS
9. Sépare les identifiants locaux (nom de machine, produit, prototype, logiciel interne, code projet) des concepts scientifiques transférables.
10. Un acronyme scientifique n'est local que si PREUVES montrent qu'il désigne réellement un identifiant local.
11. Résous chaque terme ambigu avec ses mots voisins. Le resolved_en doit correspondre au sens technique imposé par PREUVES.
12. Ignore true/false/null, UUID, identifiants session/run/request, champs JSON et métadonnées administratives.
13. Ne génère AUCUNE requête : le code les construira ensuite de manière déterministe.
14. Retourne uniquement le JSON demandé.

IDENTIFIANTS LOCAUX DÉJÀ SUSPECTÉS
{json.dumps(list(explicit_local_ids), ensure_ascii=False)}

PREUVES
---
{evidence}
---
""".strip()

    try:
        client = Client()
        raw = client.generate(
            prompt=prompt,
            temperature=0.0,
            max_output_tokens=1200,
            retries=1,
            json_mode=True,
            response_schema=_planner_schema(),
            request_name="ennoscholar:scientific_query_planner:v166_3",
        )
        text = str(raw or "").strip()
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S)
        data = json.loads(text)
        meta = getattr(client, "get_last_generation_meta", lambda: {})()
        return data if isinstance(data, dict) else None, {
            "used": True,
            "ok": isinstance(data, dict),
            "provider": meta.get("provider"),
            "model": meta.get("model"),
            "total_tokens": meta.get("total_tokens"),
        }
    except Exception as exc:
        return None, {"used": True, "ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _role_term_is_usable(term: str, role: str) -> bool:
    """Generic syntax/semantic-shape guard for LLM extracted roles.

    It rejects relational fragments and malformed phrases without embedding
    vocabulary for any project/domain. Scientific meaning still comes from the
    evidence-grounded LLM extraction.
    """
    clean = _clean(term, 140)
    ntokens = [_norm(t) for t in _tokens(clean) if _norm(t)]
    if not ntokens or len(ntokens) > 8:
        return False
    if all(t in _WEAK_RELATIONAL for t in ntokens):
        return False
    n = _norm(clean)
    bad_prefixes = (
        "impact ", "impact of ", "effect ", "effect of ", "effects of ",
        "influence ", "influence of ", "uncertainty ", "uncertainty on ",
    )
    if any(n.startswith(prefix) for prefix in bad_prefixes):
        return False
    if role == "scientific_object":
        # An object must contain at least one non-relational content token and
        # must not be merely a generic endpoint/condition phrase.
        if len([t for t in ntokens if t not in _WEAK_RELATIONAL]) < 1:
            return False
    if role in {"independent_variables", "response_variables"}:
        # Bare relation words cannot masquerade as measured/controlled quantities.
        if len([t for t in ntokens if t not in _WEAK_RELATIONAL]) < 1:
            return False
    return True


def _merge_legacy_role_fields(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Expose V166.3 causal roles and legacy fields simultaneously."""
    independent = list(plan.get("independent_variables") or [])
    response = list(plan.get("response_variables") or [])
    operating = list(plan.get("operating_conditions") or [])
    plan["variables"] = (independent + response)[:10]
    plan["constraints"] = operating[:8]
    return plan


def _validate_llm_payload(payload: Optional[Mapping[str, Any]], evidence: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {field: [] for field in _EXTRACT_ROLE_FIELDS}
    out["local_identifiers"] = []
    out["ambiguities"] = []
    if not isinstance(payload, Mapping):
        return _merge_legacy_role_fields(out)

    # Backward-compatible ingestion for cached/tests produced by V166.2.
    # New LLM calls always use the V166.3 schema, but this avoids breaking an
    # already serialized V166.2 plan during a rolling deployment.
    payload_view: Dict[str, Any] = dict(payload)
    if not payload_view.get("independent_variables") and not payload_view.get("response_variables"):
        legacy_variables = list(payload_view.get("variables") or [])
        if legacy_variables:
            payload_view["independent_variables"] = legacy_variables
    if not payload_view.get("operating_conditions") and payload_view.get("constraints"):
        payload_view["operating_conditions"] = list(payload_view.get("constraints") or [])

    for field in _EXTRACT_ROLE_FIELDS:
        for row in payload_view.get(field) or []:
            if not isinstance(row, Mapping):
                continue
            term = _clean(row.get("term_en"), 120)
            source_phrase = _clean(row.get("source_phrase"), 180)
            nt = _norm(term)
            if not term or not source_phrase or not _appears_in_evidence(source_phrase, evidence):
                continue
            if nt in _ADMIN_NOISE or nt in _LITERAL_NOISE:
                continue
            if not _role_term_is_usable(term, field):
                continue
            out[field].append({"term_en": term, "source_phrase": source_phrase})
        seen = set()
        deduped = []
        for row in out[field]:
            key = _norm(row["term_en"])
            if key and key not in seen:
                seen.add(key)
                deduped.append(row)
        out[field] = deduped[:8]

    for row in payload.get("local_identifiers") or []:
        if not isinstance(row, Mapping):
            continue
        value = _clean(row.get("value"), 80)
        source_phrase = _clean(row.get("source_phrase"), 160)
        if value and source_phrase and _appears_in_evidence(source_phrase, evidence) and _appears_in_evidence(value, evidence):
            out["local_identifiers"].append({"value": value, "source_phrase": source_phrase})

    for row in payload.get("ambiguities") or []:
        if not isinstance(row, Mapping):
            continue
        source_term = _clean(row.get("source_term"), 80)
        resolved_en = _clean(row.get("resolved_en"), 120)
        source_phrase = _clean(row.get("source_phrase"), 160)
        if (
            source_term and resolved_en and source_phrase
            and _appears_in_evidence(source_term, evidence)
            and _appears_in_evidence(source_phrase, evidence)
            and _role_term_is_usable(resolved_en, "independent_variables")
        ):
            out["ambiguities"].append({
                "source_term": source_term,
                "resolved_en": resolved_en,
                "source_phrase": source_phrase,
            })

    return _merge_legacy_role_fields(out)


def _fallback_plan(intent: Mapping[str, Any], evidence: str, local_ids: Sequence[str]) -> Dict[str, Any]:
    """Conservative deterministic fallback when the planner LLM is unavailable.

    The fallback does not pretend to infer causal variable roles. It preserves
    existing V161 normalized fields and returns fewer queries rather than
    guessing.
    """
    local_norm = {_norm(x) for x in local_ids}

    def clean_values(values: Any, limit: int = 8) -> List[Dict[str, str]]:
        seq = values if isinstance(values, list) else [values]
        rows: List[Dict[str, str]] = []
        for raw in seq:
            value = _clean(raw, 120)
            nv = _norm(value)
            if not nv or nv in local_norm or nv in _ADMIN_NOISE or nv in _LITERAL_NOISE:
                continue
            if any(_norm(local) and _norm(local) in nv.split() for local in local_ids):
                continue
            if not _role_term_is_usable(value, "fallback"):
                continue
            rows.append({"term_en": value, "source_phrase": ""})
            if len(rows) >= limit:
                break
        return rows

    object_rows = clean_values(intent.get("technical_object") or [], 4)
    phenomena_rows = clean_values(intent.get("phenomenon") or [], 4)
    method_rows = clean_values(intent.get("methods") or [], 6)
    operating_rows = clean_values(intent.get("constraints") or [], 6)
    key_rows = clean_values(intent.get("key_terms_en") or [], 12)

    used = {_norm(row["term_en"]) for row in object_rows + phenomena_rows + method_rows + operating_rows}
    # Legacy terms cannot be reliably split into controlled vs response variables,
    # so keep them as response candidates only for query fallback.
    response_rows = [row for row in key_rows if _norm(row["term_en"]) not in used][:6]

    plan = {
        "scientific_object": object_rows,
        "phenomena": phenomena_rows,
        "independent_variables": [],
        "response_variables": response_rows,
        "operating_conditions": operating_rows,
        "methods": method_rows,
        "validation_concepts": [],
        "local_identifiers": [{"value": x, "source_phrase": ""} for x in local_ids],
        "ambiguities": [],
    }
    return _merge_legacy_role_fields(plan)


def _terms(rows: Sequence[Mapping[str, Any]]) -> List[str]:
    return _unique((str(row.get("term_en") or "") for row in rows), 10)


def _blocked_source_terms(plan: Mapping[str, Any]) -> List[str]:
    values = []
    for row in plan.get("ambiguities") or []:
        if isinstance(row, Mapping):
            source = _clean(row.get("source_term"), 80)
            resolved = _clean(row.get("resolved_en"), 120)
            if source and resolved and _norm(source) != _norm(resolved):
                values.append(source)
    return _unique(values, 20)


def _query_words(parts: Sequence[Any], *, max_words: int = 11) -> str:
    words: List[str] = []
    seen = set()
    for part in parts:
        values = part if isinstance(part, (list, tuple)) else [part]
        for value in values:
            for token in _tokens(value):
                nt = _norm(token)
                if not nt or nt in seen or nt in _LITERAL_NOISE or nt in _ADMIN_NOISE:
                    continue
                seen.add(nt)
                words.append(token)
                if len(words) >= max_words:
                    return " ".join(words)
    return " ".join(words)


def _concept_token_sets(plan: Mapping[str, Any]) -> List[set[str]]:
    sets: List[set[str]] = []
    for field in _ROLE_FIELDS:
        for term in _terms(plan.get(field) or []):
            tokens = {_norm(t) for t in _tokens(term) if len(_norm(t)) >= 3}
            if tokens:
                sets.append(tokens)
    return sets


def query_is_safe(query: str, intent_or_plan: Mapping[str, Any], *, allow_local: bool = False) -> bool:
    if not isinstance(intent_or_plan, Mapping):
        return False
    plan = intent_or_plan.get("scientific_query_plan") if isinstance(intent_or_plan.get("scientific_query_plan"), Mapping) else intent_or_plan
    q = _clean(query, 240)
    nq = _norm(q)
    words = [_norm(x) for x in _tokens(q)]
    if len(words) < 3 or len(words) > 14:
        return False
    if any(w in _LITERAL_NOISE or w in _ADMIN_NOISE for w in words):
        return False
    if _UUID_RE.search(q) or _SESSION_RE.search(q):
        return False

    local_ids = [
        str(row.get("value") or "") if isinstance(row, Mapping) else str(row)
        for row in (plan.get("local_identifiers") or [])
    ]
    if not allow_local:
        for local in local_ids:
            nl = _norm(local)
            if nl and re.search(rf"(?<![a-z0-9]){re.escape(nl)}(?![a-z0-9])", nq):
                return False

    for blocked in _blocked_source_terms(plan):
        nb = _norm(blocked)
        if nb and re.search(rf"(?<![a-z0-9]){re.escape(nb)}(?![a-z0-9])", nq):
            return False

    concept_sets = _concept_token_sets(plan)
    qset = set(words)
    hits = 0
    for concept in concept_sets:
        if concept.issubset(qset) or (len(concept & qset) / max(1, len(concept))) >= 0.67:
            hits += 1
    if concept_sets and hits < 2:
        return False

    useful = [w for w in words if w not in _WEAK_RELATIONAL]
    return len(useful) >= 2


def _build_query_families(plan: Mapping[str, Any]) -> List[Dict[str, Any]]:
    obj = _terms(plan.get("scientific_object") or [])
    independent = _terms(plan.get("independent_variables") or [])
    response = _terms(plan.get("response_variables") or [])
    phen = _terms(plan.get("phenomena") or [])
    operating = _terms(plan.get("operating_conditions") or [])
    methods = _terms(plan.get("methods") or [])
    validation = _terms(plan.get("validation_concepts") or [])

    candidates: List[Tuple[str, List[Any]]] = []

    # Highest precision: system + controlled/compared quantity + measured response.
    if obj and independent and response:
        candidates.append(("direct", [obj[:1], independent[:1], response[:1]]))
        if len(independent) > 1:
            candidates.append(("variable_relation", [obj[:1], independent[:2], response[:1]]))
    elif obj and response:
        candidates.append(("direct", [obj[:1], response[:2], phen[:1]]))
    elif obj and independent:
        candidates.append(("direct", [obj[:1], independent[:2], phen[:1]]))

    if obj and independent and response and phen:
        candidates.append(("mechanism", [obj[:1], independent[:1], response[:1], phen[:1]]))
    elif obj and phen:
        candidates.append(("mechanism", [obj[:1], phen[:1], response[:1], independent[:1]]))

    if obj and operating:
        candidates.append(("operating_conditions", [obj[:1], response[:1], operating[:1], independent[:1]]))

    if obj and (methods or validation):
        candidates.append(("experimental", [obj[:1], independent[:1], response[:1], methods[:1], validation[:1]]))

    # Broad-but-still-grounded fallback family; no generic "impact/limitations"
    # tokens are injected by the query builder itself.
    if obj and len(response + independent + phen) >= 2:
        candidates.append(("secondary_axis", [obj[:1], (response + independent + phen)[:3]]))

    out: List[Dict[str, Any]] = []
    seen = set()
    for family, parts in candidates:
        q = _query_words(parts, max_words=11)
        nq = _norm(q)
        if not nq or nq in seen or not query_is_safe(q, plan):
            continue
        seen.add(nq)
        out.append({
            "query": q,
            "kind": f"v166_3_{family}",
            "family": family,
            "planner_version": PLANNER_VERSION,
        })
    return out


def build_scientific_query_plan(intent: Mapping[str, Any]) -> Dict[str, Any]:
    evidence = _source_evidence(intent)
    fingerprint = _evidence_fingerprint(intent)
    existing = intent.get("scientific_query_plan") if isinstance(intent.get("scientific_query_plan"), Mapping) else None
    if existing and existing.get("evidence_fingerprint") == fingerprint and existing.get("planner_version") == PLANNER_VERSION:
        return dict(existing)

    explicit_local = _explicit_local_identifiers(intent, evidence)
    raw_llm, llm_meta = _call_llm_planner(evidence, explicit_local)
    validated = _validate_llm_payload(raw_llm, evidence)

    llm_has_core = bool(validated.get("scientific_object")) and bool(
        validated.get("independent_variables")
        or validated.get("response_variables")
        or validated.get("phenomena")
        or validated.get("methods")
    )
    if not llm_has_core:
        validated = _fallback_plan(intent, evidence, explicit_local)
        planning_mode = "deterministic_fallback"
    else:
        planning_mode = "llm_evidence_grounded"

    # Merge explicit local identifiers with LLM-confirmed local identifiers.
    local_ids = list(explicit_local)
    local_ids.extend(
        str(row.get("value") or "")
        for row in validated.get("local_identifiers") or []
        if isinstance(row, Mapping)
    )
    validated["local_identifiers"] = [
        {"value": value, "source_phrase": ""}
        for value in _unique(local_ids, 16)
    ]

    plan: Dict[str, Any] = {
        **validated,
        "planner_version": PLANNER_VERSION,
        "planning_mode": planning_mode,
        "evidence_fingerprint": fingerprint,
        "evidence_chars": len(evidence),
        "llm": llm_meta,
    }
    plan["blocked_source_terms"] = _blocked_source_terms(plan)
    plan["queries"] = _build_query_families(plan)
    plan["query_count"] = len(plan["queries"])
    return plan


def _flatten_plan_terms(plan: Mapping[str, Any]) -> List[str]:
    values: List[str] = []
    for field in _ROLE_FIELDS:
        values.extend(_terms(plan.get(field) or []))
    return _unique(values, 24)


def attach_query_plan(intent: Mapping[str, Any], max_queries: int = 14) -> Dict[str, Any]:
    out = dict(intent or {})
    plan = build_scientific_query_plan(out)
    out["scientific_query_plan"] = plan
    out["search_queries"] = list(plan.get("queries") or [])[: max(1, int(max_queries or 14))]
    out["query_builder_version"] = PLANNER_VERSION

    # Feed the existing ranker with the same validated scientific intent without
    # changing the ranker algorithm in this patch.
    objects = _terms(plan.get("scientific_object") or [])
    phenomena = _terms(plan.get("phenomena") or [])
    independent = _terms(plan.get("independent_variables") or [])
    response = _terms(plan.get("response_variables") or [])
    variables = _unique(independent + response, 10)
    methods = _terms(plan.get("methods") or [])
    operating = _terms(plan.get("operating_conditions") or [])
    core = _unique(objects + independent + response + phenomena, 10)
    out["core_concepts"] = core
    out["primary_core_concepts"] = _unique(objects[:1] + independent[:1] + response[:1], 3) or core[:3]
    out["method_anchors"] = methods
    out["phenomenon_anchors"] = _unique(phenomena + response[:2] + operating[:2], 10)
    out["project_tool_terms"] = []
    out["local_names"] = [
        str(row.get("value") or "")
        for row in plan.get("local_identifiers") or []
        if isinstance(row, Mapping)
    ]
    out["query_planner_terms"] = _flatten_plan_terms(plan)
    return out


def build_queries(intent: Mapping[str, Any], max_queries: int = 14) -> List[Dict[str, Any]]:
    return attach_query_plan(intent, max_queries=max_queries).get("search_queries", [])


def _query_similarity(a: str, b: str) -> float:
    sa = {_norm(x) for x in _tokens(a) if len(_norm(x)) >= 3}
    sb = {_norm(x) for x in _tokens(b) if len(_norm(x)) >= 3}
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / max(1, len(sa | sb))


def select_queries(queries: Sequence[Any], intent: Mapping[str, Any], max_queries: int = 3) -> List[Dict[str, Any]]:
    enriched = attach_query_plan(intent)
    plan = enriched["scientific_query_plan"]
    candidates: List[Dict[str, Any]] = []
    seen = set()
    for raw in queries or enriched.get("search_queries") or []:
        item = dict(raw) if isinstance(raw, Mapping) else {"query": str(raw), "kind": "external"}
        q = _clean(item.get("query"), 240)
        nq = _norm(q)
        if not nq or nq in seen or not query_is_safe(q, plan):
            continue
        seen.add(nq)
        item["query"] = q
        item.setdefault("family", str(item.get("kind") or "").replace("v166_", "") or "external")
        item["selection_score"] = {
            "direct": 1.00,
            "variable_relation": 0.96,
            "operating_conditions": 0.93,
            "experimental": 0.90,
            "mechanism": 0.88,
            "secondary_axis": 0.80,
        }.get(str(item.get("family") or ""), 0.60)
        candidates.append(item)

    candidates.sort(key=lambda row: float(row.get("selection_score") or 0.0), reverse=True)
    selected: List[Dict[str, Any]] = []
    families = set()
    for item in candidates:
        if len(selected) >= max(1, int(max_queries or 3)):
            break
        family = str(item.get("family") or item.get("kind") or "")
        if family in families:
            continue
        if any(_query_similarity(item["query"], other["query"]) >= 0.72 for other in selected):
            continue
        families.add(family)
        selected.append(item)
    return selected


def adapt_query_for_provider(query: str, provider: str, intent: Optional[Mapping[str, Any]] = None) -> str:
    """Provider-shape adaptation only; never changes scientific semantics."""
    provider = _norm(provider)
    max_words = 11
    if provider in {"crossref", "core", "zenodo"}:
        max_words = 8
    elif provider in {"github", "huggingface"}:
        max_words = 9
    elif provider in {"semantic_scholar", "openalex", "arxiv", "hal", "doaj", "europe_pmc"}:
        max_words = 11
    q = _query_words([query], max_words=max_words)
    if intent is not None:
        enriched = attach_query_plan(intent)
        if not query_is_safe(q, enriched["scientific_query_plan"]):
            return ""
    return q


def _article_text(article: Mapping[str, Any]) -> str:
    return _clean(" ".join([
        str(article.get("title") or ""),
        str(article.get("abstract") or article.get("summary") or article.get("tldr") or ""),
    ]), 4000)


def assess_topic_drift(intent: Mapping[str, Any], papers: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    enriched = attach_query_plan(intent)
    plan = enriched["scientific_query_plan"]
    objects = _terms(plan.get("scientific_object") or [])
    other = _unique(
        _terms(plan.get("phenomena") or [])
        + _terms(plan.get("variables") or [])
        + _terms(plan.get("methods") or [])
        + _terms(plan.get("constraints") or []),
        16,
    )
    rows = [p for p in papers or [] if isinstance(p, Mapping) and p.get("title")][:30]
    if not rows or not objects:
        return {"checked": False, "triggered": False, "reason": "insufficient_evidence_or_results"}

    def phrase_hit(text_tokens: set[str], phrase: str) -> bool:
        pt = {_norm(x) for x in _tokens(phrase) if len(_norm(x)) >= 3}
        return bool(pt) and len(pt & text_tokens) / max(1, len(pt)) >= 0.67

    aligned = 0
    examples = []
    for paper in rows:
        text = _article_text(paper)
        tt = {_norm(x) for x in _tokens(text) if len(_norm(x)) >= 3}
        object_hit = any(phrase_hit(tt, obj) for obj in objects)
        support_hit = any(phrase_hit(tt, term) for term in other) if other else object_hit
        ok = object_hit and support_hit
        aligned += int(ok)
        if not ok and len(examples) < 5:
            examples.append(_clean(paper.get("title"), 180))

    ratio = aligned / max(1, len(rows))
    threshold = float(os.getenv("ENNOSCHOLAR_QUERY_DRIFT_MIN_ALIGNMENT", "0.35") or 0.35)
    return {
        "checked": True,
        "results_checked": len(rows),
        "aligned_count": aligned,
        "alignment_ratio": round(ratio, 4),
        "threshold": threshold,
        "triggered": bool(len(rows) >= 5 and ratio < threshold),
        "offtopic_examples": examples,
    }


def build_feedback_queries(
    intent: Mapping[str, Any],
    papers: Sequence[Mapping[str, Any]],
    existing_queries: Sequence[Any],
    max_queries: int = 2,
) -> Tuple[List[str], Dict[str, Any]]:
    drift = assess_topic_drift(intent, papers)
    if not drift.get("triggered"):
        return [], {**drift, "feedback_queries": []}

    enriched = attach_query_plan(intent)
    plan = enriched["scientific_query_plan"]
    obj = _terms(plan.get("scientific_object") or [])
    phen = _terms(plan.get("phenomena") or [])
    independent = _terms(plan.get("independent_variables") or [])
    response = _terms(plan.get("response_variables") or [])
    methods = _terms(plan.get("methods") or [])
    operating = _terms(plan.get("operating_conditions") or [])
    validation = _terms(plan.get("validation_concepts") or [])

    raw_candidates = [
        _query_words([obj[:1], independent[:1], response[:1], methods[:1], validation[:1]], max_words=12),
        _query_words([obj[:1], response[:1], operating[:1], independent[:1], phen[:1]], max_words=12),
    ]
    existing = [
        _clean(x.get("query") if isinstance(x, Mapping) else x, 240)
        for x in existing_queries or []
    ]
    out: List[str] = []
    for q in raw_candidates:
        if not q or not query_is_safe(q, plan):
            continue
        if any(_query_similarity(q, old) >= 0.78 for old in existing + out):
            continue
        out.append(q)
        if len(out) >= max(1, int(max_queries or 2)):
            break
    return out, {**drift, "feedback_queries": out, "planner_version": PLANNER_VERSION}


def build_rescue_queries(intent: Mapping[str, Any], existing_queries: Sequence[Any], max_queries: int = 6) -> List[str]:
    enriched = attach_query_plan(intent)
    plan = enriched["scientific_query_plan"]
    base = list(plan.get("queries") or [])
    selected = select_queries(base, enriched, max_queries=max_queries)
    existing = [
        _clean(x.get("query") if isinstance(x, Mapping) else x, 240)
        for x in existing_queries or []
    ]
    out: List[str] = []
    for item in selected:
        q = _clean(item.get("query"), 240)
        if q and not any(_query_similarity(q, old) >= 0.78 for old in existing + out):
            out.append(q)
    return out[: max(1, int(max_queries or 6))]
