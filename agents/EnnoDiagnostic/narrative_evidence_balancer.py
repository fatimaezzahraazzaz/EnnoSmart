# -*- coding: utf-8 -*-
from __future__ import annotations

"""Équilibrage générique des preuves narratives EnnoDiagnostic — V5.6.

Ce module ne détecte, ne regroupe et ne reformule aucun verrou.
Il agit uniquement sur Objectif / Démarche / Résultats / Paramètres après le NLP.

Principes :
- enrichir l'objectif avec des passages voisins du même contexte projet ;
- récupérer des démarches exécutées même si le NLP les a rangées dans un autre rôle ;
- récupérer des résultats expérimentaux structurés seulement s'ils sont
  compatibles avec les démarches réellement exécutées et non bibliographiques ;
- ne conserver comme paramètres que des contraintes/paramètres attribuables au projet ;
- diversifier les preuves afin qu'une seule famille technique n'écrase pas les autres.

Aucun nom de projet, modèle, stratégie, langage, framework, métrique ou valeur n'est codé en dur.
"""

import math
import re
import unicodedata
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

try:
    from .project_fact_gate import (
        gate_project_fact,
        is_external_or_reference,
        is_noise_or_interview,
        is_transcription_source,
    )
except Exception:  # pragma: no cover
    from project_fact_gate import (  # type: ignore
        gate_project_fact,
        is_external_or_reference,
        is_noise_or_interview,
        is_transcription_source,
    )

VERSION = "narrative_evidence_balancer_v5_6_generic_cross_role"

_LOCAL_PACK_KEYS = (
    "objectifs_locaux",
    "methodes_locales",
    "resultats_locaux",
    "parametres_locaux",
    "limites_locales",
    "contributions_locales",
)

_STOPWORDS = {
    "avec", "dans", "pour", "sans", "sous", "entre", "vers", "chez", "depuis",
    "des", "les", "une", "aux", "sur", "par", "que", "qui", "dont", "plus", "moins",
    "ainsi", "afin", "leur", "leurs", "cette", "ces", "cela", "comme", "etre", "sont",
    "avoir", "avait", "faire", "fait", "nous", "notre", "projet", "travaux", "etude",
    "analyse", "resultat", "resultats", "methode", "methodes", "objectif", "objectifs",
    "technique", "techniques", "modele", "modeles", "systeme", "systemes", "utiliser",
    "utilise", "utilisee", "utilises", "permettre", "permet", "permettent", "partie",
    "cette", "type", "types", "aussi", "donc", "apres", "avant", "leurs", "cela",
    "with", "without", "from", "into", "that", "this", "these", "those", "their",
    "they", "them", "have", "has", "were", "been", "project", "technical", "scientific",
    "result", "results", "method", "methods", "system", "systems", "model", "models",
}

_OBJECTIVE_ACTION_RE = re.compile(
    r"\b(?:objectif|but|finalite|vise\w*|cherch\w*|voul\w*|souhait\w*|"
    r"automatis\w*|simplifi\w*|amelior\w*|augment\w*|optimis\w*|evalu\w*|"
    r"mesur\w*|compar\w*|qualifi\w*|valid\w*|redui\w*|gagner|gain\w*)\b",
    re.I,
)

_EXECUTED_ACTION_RE = re.compile(
    r"\b(?:nous avons|nous on a|on a|l['’]?equipe a|nous,?\s*on)\s+(?:[^.;:!?]{0,24}\s)?(?:"
    r"test\w*|evalu\w*|mesur\w*|compar\w*|gener\w*|developp\w*|implement\w*|"
    r"configur\w*|adapt\w*|entrain\w*|extrai\w*|reinje\w*|utilis\w*|chois\w*|"
    r"appliqu\w*|constru\w*|execut\w*|lanc\w*|calcul\w*|analys\w*|identifi\w*|"
    r"modifi\w*|travaill\w*|inspir\w*|parc\w*|inject\w*|decoup\w*|retrouv\w*|"
    r"selection\w*|class\w*|regroup\w*|corrig\w*)\b",
    re.I,
)


_OBSERVED_RE = re.compile(
    r"\b(?:nous avons|nous on a|on a)\b.{0,120}\b(?:observe\w*|mesur\w*|obten\w*|"
    r"constat\w*|atteint\w*|montre\w*|donne\w*)\b|"
    r"\b(?:resultat\w*|performance\w*|score\w*|taux|metrique\w*|mesure\w*)\b.{0,120}"
    r"\b(?:obten\w*|mesur\w*|acceptable\w*|faible\w*|eleve\w*|meilleur\w*|"
    r"inferieur\w*|superieur\w*|stable\w*|instable\w*|limite\w*)\b|"
    r"\bn['’]?a pas (?:trop )?(?:augmente|ameliore|monte|progresse)\b|"
    r"\bn['’]?ont pas (?:trop )?(?:augmente|ameliore|monte|progresse)\b",
    re.I,
)

_CONSTRAINT_RE = re.compile(
    r"\b(?:contrainte\w*|limite\w*|restriction\w*|ressource\w*\s+(?:limitee?s?|disponible?s?)|"
    r"capacite\w*\s+(?:limitee?s?|maximale?s?)|compatibilite\w*|confidentialite\w*|souverainete\w*|"
    r"memoire\w*\s+(?:limitee?s?|disponible?s?)|gpu|cpu|latence\w*|fenetre\w*\s+de\s+contexte|"
    r"seuil\w*|maximum|minimum|obligation\w*|non[- ]partage|ne peut pas|ne pouvait pas|impossible)\b",
    re.I,
)

_CONSULTANT_ADMIN_RE = re.compile(
    r"\b(?:eligibilite|non eligib|ineligib|consultant|dossier technique|documents? (?:recu|detaille|structure)|"
    r"approche experimentale eligible|traces? ecrites?|piece jointe|envoyer|documentation)\b",
    re.I,
)

_LITERATURE_CONTEXT_RE = re.compile(
    r"\b(?:des etudes? recentes?|certaines etudes?|les auteurs?|dans (?:le|l['’]?) papier|l['’]?article\s+(?:s['’]?appel|presente|propose)|"
    r"travaux anterieurs|selon l['’]?etude|selon l['’]?article|a ete proposee?|ont compare|"
    r"modeles? proposee?s? dans|etat de l['’ ]?art)\b",
    re.I,
)


_BIBLIO_TABLE_RE = re.compile(
    r"\b(?:doi|url|link|paper|article|auteur\w*|author\w*|annee|year|journal|conference|"
    r"bibliograph\w*|literature|travaux connexes|state of the art|etat de l['’ ]?art|"
    r"hardware for inference|architecture\s*\||databases?\s*\||benchmark\s*\|)\b",
    re.I,
)

_TABLE_RE = re.compile(r"\[(?:TABLEAU|TABLE|DONN[EÉ]ES)[^\]]*\]", re.I)
_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9])[-+]?\d+(?:[.,]\d+)?(?:\s*%)?")
_PIPE_RE = re.compile(r"\|")
_METRIC_HEADER_RE = re.compile(
    r"(?:^|\|)\s*[%#]?[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ _/\-]{2,40}\s*(?=\|)|"
    r"\b(?:metric\w*|mesure\w*|score\w*|taux|accuracy|precision|recall|coverage|"
    r"latency|error\w*|loss|quality|performance|compil\w*)\b",
    re.I,
)


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("’", "'")
    return re.sub(r"\s+", " ", text).strip()


def _clean(value: Any, limit: int = 12000) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _raw_blob(item: Mapping[str, Any], limit: int = 12000) -> str:
    return _clean(" ".join(str(item.get(k) or "") for k in (
        "section_title", "context_before", "text", "analysis_text", "context_after"
    )), limit)


def _meta(source: Mapping[str, Any]) -> Dict[str, Any]:
    value = source.get("metadata") if isinstance(source, Mapping) else None
    return dict(value) if isinstance(value, Mapping) else {}


def _source_blob(source: Mapping[str, Any]) -> str:
    meta = _meta(source)
    return _clean(
        source.get("analysis_text")
        or source.get("text")
        or meta.get("analysis_text")
        or meta.get("source_text_original")
    )


def _tokens(value: Any) -> Set[str]:
    text = _norm(value)
    output: Set[str] = set()
    for token in re.findall(r"[a-z0-9][a-z0-9+._/-]{2,}", text):
        token = token.strip("._-/")
        if len(token) < 4 or token in _STOPWORDS or token.isdigit():
            continue
        output.add(token)
    return output


def _jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    a, b = set(left), set(right)
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def _section_identity(item: Mapping[str, Any]) -> Tuple[str, str]:
    return (
        _norm(item.get("document") or item.get("document_name") or item.get("source_path")),
        _norm(item.get("section_title") or item.get("section_path")),
    )


def _source_signature(source: Mapping[str, Any]) -> Tuple[str, str]:
    meta = _meta(source)
    doc = _norm(source.get("document") or meta.get("document") or source.get("source_path") or meta.get("source_path"))
    return doc, _norm(_source_blob(source))[:900]


def _specificity_score(value: Any) -> float:
    raw = _clean(value)
    norm = _norm(raw)
    toks = _tokens(norm)
    score = min(len(toks), 30) * 0.5
    score += min(len(_NUMBER_RE.findall(raw)), 8) * 0.25
    score += min(len(re.findall(r"\b[A-Z][A-Za-z0-9+._/-]{2,}\b", raw)), 8) * 1.2
    score += min(len(re.findall(r"\b[A-Z]{2,}[A-Z0-9._/-]*\b", raw)), 6) * 1.5
    if "/" in raw or "-" in raw:
        score += 0.8
    return score


def _distinctive_tokens(value: Any) -> Set[str]:
    generic = {
        "prompting", "strategie", "strategies", "configuration", "configurer", "generation",
        "generer", "donnees", "tests", "code", "codes", "resultats", "performance", "modele",
        "modeles", "methode", "methodes", "projet", "projets", "utiliser", "utilise", "faire",
        "partir", "partie", "base", "types", "type", "valeur", "valeurs",
    }
    return {t for t in _tokens(value) if t not in generic}


def _diverse_select(sources: Sequence[Dict[str, Any]], max_items: int, *, seed_terms: Optional[Set[str]] = None) -> List[Dict[str, Any]]:
    if not sources:
        return []
    seed_terms = set(seed_terms or set())
    remaining: List[Tuple[float, Dict[str, Any], Set[str]]] = []
    for source in sources:
        text = _source_blob(source)
        meta = _meta(source)
        toks = _distinctive_tokens(text)
        base = _specificity_score(text)
        try:
            base += float(meta.get("rank_score") or 0.0) * 1.2
            base += float(meta.get("confidence") or 0.0) * 1.5
        except Exception:
            pass
        if seed_terms and toks:
            base += min(4.0, len(toks & seed_terms) * 0.8)
        if _EXECUTED_ACTION_RE.search(_norm(text)):
            base += 3.0
        if _OBSERVED_RE.search(_norm(text)):
            base += 2.0
        remaining.append((base, source, toks))

    selected: List[Tuple[float, Dict[str, Any], Set[str]]] = []
    seen = set()
    while remaining and len(selected) < max_items:
        best_idx = 0
        best_value = -1e9
        for idx, (base, source, toks) in enumerate(remaining):
            signature = _source_signature(source)
            if signature in seen:
                continue
            similarity = max((_jaccard(toks, stoks) for _, _, stoks in selected), default=0.0)
            value = base - 8.0 * similarity
            if value > best_value:
                best_idx, best_value = idx, value
        base, source, toks = remaining.pop(best_idx)
        signature = _source_signature(source)
        if signature in seen:
            continue
        seen.add(signature)
        selected.append((base, source, toks))
    return [source for _, source, _ in selected]


def _pack_rows(pack: Mapping[str, Any], *, include_catalog: bool = False) -> Iterable[Tuple[str, Dict[str, Any]]]:
    for key in _LOCAL_PACK_KEYS:
        for item in pack.get(key) or []:
            if isinstance(item, dict):
                yield key, item
    if include_catalog:
        for item in pack.get("evidence_catalog") or []:
            if isinstance(item, dict):
                yield "evidence_catalog", item


def _convert(agent: Any, item: Mapping[str, Any], *, target_role: str, pack_key: str, extra_meta: Optional[Mapping[str, Any]] = None) -> Optional[Dict[str, Any]]:
    raw = dict(item)
    raw["role"] = target_role
    raw["semantic_role"] = target_role
    raw["original_model_role"] = raw.get("original_model_role") or item.get("role")
    raw["recovered_cross_role"] = True
    if extra_meta:
        raw.update(dict(extra_meta))
    try:
        source = agent._nlp_pack_item_to_source(raw, pack_key=pack_key, role=target_role)
    except Exception:
        return None
    if not isinstance(source, dict):
        return None
    meta = _meta(source)
    meta.update({k: v for k, v in (extra_meta or {}).items()})
    meta["role"] = target_role
    meta["semantic_role"] = target_role
    meta["recovered_cross_role"] = True
    source["metadata"] = meta
    return source


def _state_art_pairs(pack: Mapping[str, Any]) -> Set[Tuple[str, str]]:
    output: Set[Tuple[str, str]] = set()
    for item in pack.get("etat_art_local") or []:
        if isinstance(item, Mapping):
            output.add(_section_identity(item))
    return output


def _project_method_sources(agent: Any, pack: Mapping[str, Any], existing: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = list(existing or [])
    for pack_key, raw in _pack_rows(pack):
        blob = _norm(_raw_blob(raw))
        if not _EXECUTED_ACTION_RE.search(blob):
            continue
        if _CONSULTANT_ADMIN_RE.search(blob) or _LITERATURE_CONTEXT_RE.search(blob) and not _EXECUTED_ACTION_RE.search(blob):
            continue
        source = _convert(
            agent, raw, target_role="methode", pack_key=pack_key,
            extra_meta={"cross_role_method_recovery": True},
        )
        if source is None or is_external_or_reference(source) or is_noise_or_interview(source):
            continue
        decision = gate_project_fact(source, "demarche_detectee")
        if not decision.allowed:
            continue
        source["project_fact_gate"] = {"allowed": True, "reason": decision.reason, "confidence": decision.confidence}
        candidates.append(source)
    return _diverse_select(candidates, 14)


def _objective_companions(agent: Any, pack: Mapping[str, Any], objectives: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not objectives:
        return []
    objective_sections = set()
    objective_terms: Set[str] = set()
    for source in objectives:
        meta = _meta(source)
        objective_sections.add((
            _norm(source.get("document") or meta.get("document") or source.get("source_path") or meta.get("source_path")),
            _norm(meta.get("section_title") or source.get("section_title")),
        ))
        objective_terms |= _distinctive_tokens(_source_blob(source))

    candidates: List[Dict[str, Any]] = []
    for pack_key, raw in _pack_rows(pack):
        identity = _section_identity(raw)
        if identity not in objective_sections:
            continue
        blob = _raw_blob(raw)
        norm = _norm(blob)
        if len(norm) < 55:
            continue
        objective_signal = bool(_OBJECTIVE_ACTION_RE.search(norm))
        task_scope_signal = bool(re.search(
            r"\b(?:afin de|pour|dans le cadre|sur la|dans la)\b.{0,140}\b(?:"
            r"gener\w*|evalu\w*|mesur\w*|compar\w*|automatis\w*|simplifi\w*|amelior\w*)\b",
            norm,
            re.I,
        ))
        evaluation_scope_signal = bool(re.search(
            r"\b(?:critere\w*|metrique\w*|mesure\w*|benchmark\w*)\b", norm, re.I
        ))
        if not (objective_signal or task_scope_signal or evaluation_scope_signal):
            continue
        if _CONSULTANT_ADMIN_RE.search(norm):
            continue
        if _LITERATURE_CONTEXT_RE.search(norm) and not re.search(
            r"\b(?:notre objectif|nous voulions|nous cherchions|le projet vise|objectif du projet)\b",
            norm, re.I,
        ):
            continue
        # Un passage purement méthodologique reste une démarche : il ne complète
        # l'objectif que s'il contient aussi une finalité/portée explicite.
        if _EXECUTED_ACTION_RE.search(norm) and not (objective_signal or task_scope_signal):
            continue
        source = _convert(
            agent, raw, target_role="objectif", pack_key=pack_key,
            extra_meta={
                "objective_context_companion": True,
                "current_project_evidence": True,
                "objective_context_only": True,
            },
        )
        if source is None or is_external_or_reference(source) or is_noise_or_interview(source):
            continue
        # Une transcription voisine n'est contexte d'objectif que si elle est dans
        # exactement la même section de discussion que l'objectif déjà accepté.
        if is_transcription_source(source) and identity not in objective_sections:
            continue
        source["project_fact_gate"] = {
            "allowed": True,
            "reason": "objective_context_companion_same_project_section",
            "confidence": 0.92,
        }
        candidates.append(source)

    return _diverse_select(candidates, 8, seed_terms=objective_terms)


def _table_group_rows(pack: Mapping[str, Any]) -> Dict[Tuple[str, str], List[Tuple[str, Dict[str, Any]]]]:
    grouped: Dict[Tuple[str, str], List[Tuple[str, Dict[str, Any]]]] = defaultdict(list)
    for pack_key, raw in _pack_rows(pack, include_catalog=True):
        section = str(raw.get("section_title") or "")
        if not (_TABLE_RE.search(section) or "|" in _raw_blob(raw)):
            continue
        grouped[_section_identity(raw)].append((pack_key, raw))
    return grouped


def _metric_header_candidates(pack: Mapping[str, Any]) -> Dict[str, List[str]]:
    by_doc: Dict[str, List[str]] = defaultdict(list)
    seen: Dict[str, Set[str]] = defaultdict(set)
    for _, raw in _pack_rows(pack, include_catalog=True):
        blob = _raw_blob(raw, 5000)
        if "|" not in blob or not _METRIC_HEADER_RE.search(blob):
            continue
        doc = _norm(raw.get("document") or raw.get("source_path"))
        norm = _norm(blob)
        if not doc or norm in seen[doc]:
            continue
        seen[doc].add(norm)
        by_doc[doc].append(blob)
    return by_doc


def _table_breadth_score(blob: str) -> float:
    numbers = _NUMBER_RE.findall(blob)
    pipes = len(_PIPE_RE.findall(blob))
    segments = [s.strip() for s in blob.split("|") if s.strip()]
    labels = {
        _norm(s) for s in segments
        if not _NUMBER_RE.fullmatch(s.strip()) and len(_norm(s)) >= 3 and len(_norm(s)) <= 80
    }
    return min(len(numbers), 80) * 0.15 + min(pipes, 120) * 0.04 + min(len(labels), 30) * 0.25


def _recover_experiment_tables(agent: Any, pack: Mapping[str, Any], methods: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    method_terms: Set[str] = set()
    for source in methods:
        method_terms |= _distinctive_tokens(_source_blob(source))
    if not method_terms:
        return []

    state_art = _state_art_pairs(pack)
    groups = _table_group_rows(pack)
    headers = _metric_header_candidates(pack)
    candidates: List[Tuple[float, Dict[str, Any]]] = []

    for identity, rows in groups.items():
        if identity in state_art:
            continue
        unique_blobs: List[str] = []
        seen_blob = set()
        for _, raw in rows:
            blob = _raw_blob(raw, 7000)
            norm = _norm(blob)
            if norm and norm not in seen_blob:
                seen_blob.add(norm)
                unique_blobs.append(blob)
        combined = _clean(" ".join(unique_blobs), 14000)
        norm = _norm(combined)
        if len(_NUMBER_RE.findall(combined)) < 5 or len(_PIPE_RE.findall(combined)) < 5:
            continue
        if _BIBLIO_TABLE_RE.search(norm):
            continue
        overlap = _distinctive_tokens(combined) & method_terms
        if len(overlap) < 1:
            continue
        segments = [seg.strip() for seg in combined.split("|") if seg.strip()]
        numeric_segments = [seg for seg in segments if _NUMBER_RE.search(seg)]
        numeric_density = len(numeric_segments) / max(1, len(segments))
        if numeric_density < 0.45:
            continue
        # Au moins un libellé catégoriel du tableau doit recouper une famille
        # de méthode réellement exécutée. Cela écarte les tableaux de benchmark
        # bibliographique qui partagent seulement le domaine général.
        categorical = [
            _distinctive_tokens(seg) for seg in segments
            if not _NUMBER_RE.search(seg) and 2 <= len(_norm(seg)) <= 80
        ]
        if not any(tokens & method_terms for tokens in categorical):
            continue

        # Les très petits tableaux isolés restent possibles, mais ils doivent
        # être moins prioritaires qu'une comparaison expérimentale large.
        breadth = _table_breadth_score(combined)
        if breadth < 2.5:
            continue

        doc = identity[0]
        header_context = ""
        best_header_overlap = 0
        for header in headers.get(doc, []):
            header_overlap = len(_distinctive_tokens(header) & (_distinctive_tokens(combined) | method_terms))
            if header_overlap > best_header_overlap:
                best_header_overlap = header_overlap
                header_context = header
        has_metric_context = bool(header_context and _METRIC_HEADER_RE.search(header_context))

        pack_key, raw = rows[0]
        enriched = dict(raw)
        enriched["analysis_text"] = combined
        enriched["text"] = combined
        source = _convert(
            agent,
            enriched,
            target_role="resultat",
            pack_key=pack_key,
            extra_meta={
                "execution_status": "observed",
                "actor_scope": "project_team",
                "evidence_origin": "project_direct",
                "document_type": "resultats_mesures",
                "project_experiment_table": True,
                "project_result_corroborated": True,
                "metric_context_available": has_metric_context,
                "metric_header_context": _clean(header_context, 1800) if has_metric_context else "",
                "method_overlap_count": len(overlap),
                "table_breadth_score": round(breadth, 3),
                "table_numeric_density": round(numeric_density, 3),
            },
        )
        if source is None or is_external_or_reference(source) or is_noise_or_interview(source):
            continue
        decision = gate_project_fact(source, "resultats_metriques")
        if not decision.allowed:
            continue
        source["project_fact_gate"] = {"allowed": True, "reason": "corroborated_project_experiment_table", "confidence": 0.96}
        # Conserver le contexte d'entête dans la source sans inventer la liaison
        # des colonnes ; le rédacteur n'utilisera les valeurs que si le header est explicite.
        meta = _meta(source)
        source["metadata"] = meta
        score = breadth + len(overlap) * 2.5 + (5.0 if has_metric_context else 0.0)
        candidates.append((score, source))

    candidates.sort(key=lambda x: x[0], reverse=True)
    return [source for _, source in candidates[:8]]


def _project_result_sources(agent: Any, pack: Mapping[str, Any], existing: Sequence[Dict[str, Any]], methods: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    # Les résultats qualitatifs directement observés restent prioritaires.
    for source in existing or []:
        meta = _meta(source)
        text = _source_blob(source)
        if meta.get("project_experiment_table"):
            candidates.append(source)
            continue
        if _OBSERVED_RE.search(_norm(text)) or meta.get("execution_status") in {"observed", "measured"}:
            candidates.append(source)

    # Récupération cross-role d'observations projet mal classées par le NLP.
    for pack_key, raw in _pack_rows(pack):
        blob = _norm(_raw_blob(raw))
        if not _OBSERVED_RE.search(blob):
            continue
        source = _convert(
            agent, raw, target_role="resultat", pack_key=pack_key,
            extra_meta={"execution_status": "observed", "cross_role_result_recovery": True},
        )
        if source is None or is_external_or_reference(source) or is_noise_or_interview(source):
            continue
        decision = gate_project_fact(source, "resultats_metriques")
        if not decision.allowed:
            continue
        source["project_fact_gate"] = {"allowed": True, "reason": decision.reason, "confidence": decision.confidence}
        candidates.append(source)

    candidates.extend(_recover_experiment_tables(agent, pack, methods))

    # Privilégier les observations directement interprétables et les tableaux
    # comparatifs larges/corroborés. Les petits tableaux isolés passent après.
    for source in candidates:
        meta = _meta(source)
        text = _source_blob(source)
        if _OBSERVED_RE.search(_norm(text)):
            meta["result_priority_boost"] = 5.0
        if meta.get("project_experiment_table"):
            meta["result_priority_boost"] = float(meta.get("table_breadth_score") or 0.0) + (
                5.0 if meta.get("metric_context_available") else 0.0
            )
        source["metadata"] = meta

    selected = _diverse_select(candidates, 12, seed_terms=set().union(*[_distinctive_tokens(_source_blob(m)) for m in methods]) if methods else set())
    return selected


def _project_parameter_sources(agent: Any, pack: Mapping[str, Any], existing: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for source in existing or []:
        if is_external_or_reference(source) or is_noise_or_interview(source):
            continue
        decision = gate_project_fact(source, "parametres_contraintes")
        if decision.allowed:
            source = dict(source)
            source["project_fact_gate"] = {"allowed": True, "reason": decision.reason, "confidence": decision.confidence}
            candidates.append(source)

    # Une contrainte transversale peut avoir été rangée comme limite. Pour une
    # transcription, on exige un acteur projet explicite ; pour un document projet
    # structuré, le gate garde ses propres exigences de provenance.
    for pack_key, raw in _pack_rows(pack):
        blob = _norm(_raw_blob(raw))
        if not _CONSTRAINT_RE.search(blob):
            continue
        if _CONSULTANT_ADMIN_RE.search(blob) or _LITERATURE_CONTEXT_RE.search(blob):
            continue
        # Hors du rôle paramètre natif, une simple mention de contexte n'est pas
        # suffisante : il faut une contrainte explicite portée par le projet.
        explicit_constraint = bool(re.search(
            r"\b(?:contrainte\w*|limite\w*|restriction\w*|ressource\w*\s+limitee?s?|"
            r"ne peut pas|ne pouvait pas|impossible|maximum|minimum|fenetre\w*\s+de\s+contexte|"
            r"confidentialite\w*|souverainete\w*|non[- ]partage)\b",
            blob, re.I,
        ))
        if pack_key != "parametres_locaux" and not explicit_constraint:
            continue
        section_norm = _norm(raw.get("section_title"))
        actor_signal = bool(re.search(
            r"\b(?:nous|notre|nos|on\s+(?:ne|n['’])|on\s+avait|on\s+a|l['’]?equipe)\b",
            blob, re.I,
        ))
        structured_constraint_section = bool(re.search(
            r"\b(?:parametre|configuration|contrainte|limite|ressource)\b",
            section_norm, re.I,
        ))
        if pack_key != "parametres_locaux" and not (actor_signal or structured_constraint_section):
            continue
        source = _convert(
            agent, raw, target_role="parametre", pack_key=pack_key,
            extra_meta={"cross_role_parameter_recovery": True},
        )
        if source is None or is_external_or_reference(source) or is_noise_or_interview(source):
            continue
        if is_transcription_source(source) and not re.search(
            r"\b(?:nous|notre|nos|on\s+(?:ne|n['’])|on\s+avait|on\s+a|l['’]?equipe)\b",
            _norm(_source_blob(source)),
            re.I,
        ):
            continue
        decision = gate_project_fact(source, "parametres_contraintes")
        if not decision.allowed:
            continue
        source["project_fact_gate"] = {"allowed": True, "reason": decision.reason, "confidence": decision.confidence}
        candidates.append(source)

    return _diverse_select(candidates, 8)


def balance_narrative_sections(agent: Any, sections: Dict[str, List[Dict[str, Any]]]) -> Dict[str, List[Dict[str, Any]]]:
    """Enrichit uniquement les sections narratives, sans toucher à ``verrous``."""
    if not isinstance(sections, dict):
        return sections
    payload = getattr(agent, "_current_nlp_payload_for_diagnostic", None)
    if not isinstance(payload, dict):
        return sections
    pack = payload.get("multi_document_evidence_pack_for_ennodiagnostic") or {}
    if not isinstance(pack, Mapping):
        return sections

    original_verrou_signature = [
        _source_signature(item) for item in (sections.get("verrous") or []) if isinstance(item, Mapping)
    ]

    objectives = [item for item in (sections.get("objectifs") or []) if isinstance(item, dict)]
    methods = _project_method_sources(agent, pack, sections.get("methodes") or [])
    results = _project_result_sources(agent, pack, sections.get("resultats") or [], methods)
    params = _project_parameter_sources(agent, pack, sections.get("parametres") or [])
    companions = _objective_companions(agent, pack, objectives)

    sections["methodes"] = methods
    sections["resultats"] = results
    sections["parametres"] = params
    sections["objectif_context_companions"] = companions

    # Reconstituer le contexte global depuis les faits narratifs équilibrés.
    # Aucun Chroma, aucun appel LLM, aucun impact sur la liste des verrous.
    global_sources: List[Dict[str, Any]] = []
    for key in ("objectifs", "objectif_context_companions", "methodes", "resultats", "parametres", "limites", "contributions"):
        global_sources.extend([x for x in (sections.get(key) or []) if isinstance(x, dict)])
    sections["global"] = _diverse_select(global_sources, 36)

    final_verrou_signature = [
        _source_signature(item) for item in (sections.get("verrous") or []) if isinstance(item, Mapping)
    ]
    if final_verrou_signature != original_verrou_signature:
        raise RuntimeError("V5.6 narrative balancer attempted to modify lock sources")

    sections["_narrative_balance_report"] = {
        "version": VERSION,
        "objective_facts": len(objectives),
        "objective_context_companions": len(companions),
        "method_facts_balanced": len(methods),
        "result_facts_balanced": len(results),
        "parameter_facts_balanced": len(params),
        "chroma_queries": 0,
        "locks_unchanged": True,
        "hardcoded_project_terms": False,
    }
    return sections
