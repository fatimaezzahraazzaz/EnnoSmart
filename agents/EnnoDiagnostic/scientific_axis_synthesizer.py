# -*- coding: utf-8 -*-
from __future__ import annotations

"""Consolidation prudente des candidats atomiques en axes scientifiques.

Les groupes NLP et la réconciliation N/N-1 restent intacts et auditables. Cette
couche construit une vue consultant plus lisible : un axe parent peut contenir
plusieurs sous-problèmes courants, sans imposer un nombre d'axes et sans utiliser
le CIR précédent comme preuve factuelle de l'année courante.
"""

import hashlib
import json
import os
import re
import unicodedata
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


VERSION = "scientific_axis_synthesizer_v2_explicit_lock_coverage"

_ELIGIBLE_STATUSES = {"rnd_core_defendable", "rnd_core_partial"}
_CONTEXT_STATUSES = {"classical_engineering", "insufficient_evidence"}
_HISTORICAL_CONTINUITY = {
    "continued",
    "refined",
    "sub_lock",
    "partially_lifted",
    "extended_scope",
    "continued_to_confirm",
}
_GENERIC_META_LOCK_PATTERNS = (
    re.compile(r"\blevee? (?:des? )?verrous? technologiques?\b", re.I),
    re.compile(r"\blevee? (?:des? )?obstacles? r d\b", re.I),
    re.compile(r"\bperformances? techniques? quantifiees?.*\bobstacles? r d\b", re.I),
    re.compile(r"\batteindre (?:les? )?performances? (?:techniques? )?attendues?\b", re.I),
    re.compile(r"\bperformances? techniques? (?:definies?|attendues?).*\bannee\b", re.I),
    re.compile(r"\bvalidation (?:rigoureuse )?(?:des? )?performances? techniques? attendues?\b", re.I),
    re.compile(r"\bperformances? techniques? attendues?.*\bcadre r(?:&|et)?d\b", re.I),
    re.compile(r"\bobstacles? technologiques? preliminaires?\b", re.I),
    re.compile(r"\bdifficultes? preliminaires?.*\bvalidation rigoureuse des hypotheses?\b", re.I),
    re.compile(r"\bvalidation rigoureuse des hypotheses?.*\b(?:r d|recherche et developpement)\b", re.I),
)
_USE_CASE_DOCUMENT_PATTERN = re.compile(
    r"(?:^|[^a-z0-9])(?:use cases?|cas d usage|cas usages?)(?:[^a-z0-9]|$)",
    re.I,
)
_PROJECT_LEVEL_SECTION_PATTERN = re.compile(
    r"\b(?:project objectives?|objectifs? du projet|context|contexte|overview|vue d ensemble|kms)\b",
    re.I,
)
_STOPWORDS = {
    "avec", "dans", "pour", "sans", "sous", "entre", "vers", "chez", "depuis",
    "des", "les", "une", "aux", "sur", "par", "que", "qui", "dont", "plus",
    "moins", "ainsi", "afin", "leur", "leurs", "cette", "ces", "cela", "comme",
    "etre", "sont", "avoir", "peut", "doit", "projet", "annee", "technique",
    "scientifique", "travaux", "analyse", "resultat", "resultats", "methode",
    "methodes", "verrou", "verrous", "incertitude", "incertitudes", "systeme",
    "systemes", "capacite", "garantir", "impossibilite", "complexite", "majeure",
}

_PLACEHOLDER_LOCK_RE = re.compile(
    r"\b(?:signal technique a reformuler|comportement technique reste a caracteriser|"
    r"preuves? courantes? montrent? que le comportement)\b",
    re.I,
)
_EXPLICIT_LOCK_HEADING_RE = re.compile(
    r"^(?P<kind>sous[\s-]*verrou|verrou)\s*"
    r"(?P<number>\d+(?:\s*[-.]\s*\d*)?)?\s*:\s*(?P<title>.+?)\s*$",
    re.I,
)


def _clean(value: Any, max_chars: int = 2000) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", _clean(value).lower())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9%+./_-]+", " ", text)).strip()


def _tokens(value: Any) -> Set[str]:
    return {
        token.rstrip("s")
        for token in _norm(value).split()
        if len(token) >= 4 and token not in _STOPWORDS and not token.isdigit()
    }


def _item_scope_tokens(item: Mapping[str, Any]) -> Set[str]:
    source_text = " ".join(
        _clean(source.get("excerpt") or source.get("text"), 600)
        for source in (item.get("sources") or [])[:8]
        if isinstance(source, Mapping)
    )
    return _tokens(" ".join([
        _clean(item.get("title")),
        _clean(item.get("scientific_lock")),
        _clean(item.get("why_not_simple_engineering")),
        source_text,
    ]))


def _float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return max(0.0, min(1.0, number))
    except Exception:
        return default


def _bool_env(name: str, default: bool = True) -> bool:
    return str(os.getenv(name, "1" if default else "0")).strip().lower() in {
        "1", "true", "yes", "oui", "on",
    }


def _extract_json_object(value: Any) -> Optional[Dict[str, Any]]:
    raw = str(value or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(raw[start : end + 1])
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None


def _item_id(item: Mapping[str, Any], index: int) -> str:
    return _clean(item.get("continuity_current_id"), 80) or f"C{index}"


def _group_ids(item: Mapping[str, Any]) -> List[str]:
    values = [
        item.get("group_id"),
        item.get("cluster_id"),
        *(item.get("member_group_ids") or []),
        *(item.get("original_nlp_group_ids") or []),
    ]
    return list(dict.fromkeys(_clean(value, 240) for value in values if _clean(value, 240)))


def _operation_statuses(item: Mapping[str, Any]) -> List[str]:
    statuses: List[str] = []
    for assessment in item.get("frascati_group_assessments") or []:
        if not isinstance(assessment, Mapping):
            continue
        demarche = assessment.get("demarche_legibility")
        demarche = demarche if isinstance(demarche, Mapping) else {}
        status = _clean(demarche.get("operation_status"), 80)
        if status:
            statuses.append(status)
    direct = _clean(item.get("operation_status"), 80)
    if direct:
        statuses.append(direct)
    return list(dict.fromkeys(statuses))


def _item_scientific_strength(item: Mapping[str, Any]) -> str:
    statuses = set(_operation_statuses(item))
    if statuses & _ELIGIBLE_STATUSES:
        return "eligible_candidate"
    if "classical_engineering" in statuses:
        return "classical_engineering"
    if "insufficient_evidence" in statuses:
        return "insufficient_evidence"
    return "unclassified"


def _has_validated_historical_continuity(item: Mapping[str, Any]) -> bool:
    history = item.get("historical_continuity")
    history = history if isinstance(history, Mapping) else {}
    return (
        _clean(history.get("status"), 80) in _HISTORICAL_CONTINUITY
        and _float(history.get("confidence")) >= 0.66
        and bool(_clean(history.get("previous_family_id"), 120))
    )


def _is_generic_meta_lock(item: Mapping[str, Any]) -> bool:
    """Écarte un objectif/meta-verrou sans mécanisme technique propre."""
    text = _norm(" ".join([
        _clean(item.get("title")),
        _clean(item.get("scientific_lock")),
        _clean(item.get("technical_axis")),
    ]))
    return any(pattern.search(text) for pattern in _GENERIC_META_LOCK_PATTERNS)


def _is_placeholder_lock(item: Mapping[str, Any]) -> bool:
    text = _norm(" ".join([
        _clean(item.get("title")),
        _clean(item.get("scientific_lock")),
    ]))
    return bool(_PLACEHOLDER_LOCK_RE.search(text))


def _source_grounded_lock_title(item: Mapping[str, Any]) -> str:
    """Récupère une formulation de verrou déjà écrite dans une preuve.

    Cette réparation ne crée aucun mécanisme. Elle privilégie les listes
    ``Verrous technologiques | 1. ...`` puis les titres de section explicites.
    """
    candidates: List[Tuple[int, str]] = []
    for source in item.get("sources") or []:
        if not isinstance(source, Mapping):
            continue
        excerpt = _clean(source.get("excerpt") or source.get("text"), 1800)
        section = _clean(source.get("section_title"), 500)
        for value in (section, excerpt):
            if not value:
                continue
            section_match = re.search(
                r"(?:\[?SECTION\s*:\s*)?((?:Sous[\s-]*verrou|Verrou)\s*"
                r"\d+(?:\s*[-.]\s*\d*)?\s*:\s*[^\]\n]{18,360})",
                value,
                flags=re.I,
            )
            if section_match:
                title = _clean(section_match.group(1).rstrip(" ]"), 320)
                candidates.append((4, title))
            list_match = re.search(
                r"\bVerrous? technologiques?\s*\|\s*\d+[.)]?\s*([^|\n]{24,360})",
                value,
                flags=re.I,
            )
            if list_match:
                title = _clean(list_match.group(1).rstrip(" .;]"), 320)
                candidates.append((3, title))
        if excerpt:
            # Repli transversal : retenir une phrase qui formule explicitement
            # une difficulté ou une incertitude, quel que soit le domaine du
            # projet. Aucun vocabulaire métier particulier n'est présupposé.
            for sentence_value in re.split(r"(?<=[.;!?])\s+", excerpt):
                normalized_sentence = _norm(sentence_value)
                if re.search(
                    r"\b(?:verrou|sous[ -]?verrou|incertitude|difficulte|limite|"
                    r"impossibilite|non (?:maitris|garanti)|reste a demontrer|"
                    r"inconnu|indetermine)\w*\b",
                    normalized_sentence,
                    flags=re.I,
                ):
                    candidates.append((1, _clean(sentence_value, 320)))
                    break
    if not candidates:
        return ""
    candidates.sort(key=lambda row: (row[0], len(row[1])), reverse=True)
    return candidates[0][1]


def _repair_placeholder_lock(item: Mapping[str, Any]) -> Dict[str, Any]:
    output = deepcopy(dict(item))
    if not _is_placeholder_lock(output):
        return output
    grounded = _source_grounded_lock_title(output)
    if not grounded:
        return output
    output["title"] = grounded
    output["technical_axis"] = grounded
    output["scientific_lock"] = grounded
    output["text"] = grounded
    output["placeholder_lock_repaired"] = True
    output["placeholder_lock_repair_source"] = "explicit_current_evidence"
    return output


def _is_metric_or_method_only_lock(item: Mapping[str, Any]) -> bool:
    """Évite de promouvoir un KPI ou un outil de contrôle en verrou autonome."""
    source_text = " ".join(
        _clean(source.get("excerpt") or source.get("text"), 1400)
        for source in (item.get("sources") or [])
        if isinstance(source, Mapping)
    )
    normalized = _norm(source_text)
    has_explicit_uncertainty = bool(re.search(
        r"\b(?:incertitude|impossibilite|non (?:maitris|garanti)|reste a demontrer|"
        r"aucune solution|etat de l art.*(?:limite|peu de methode))\w*\b",
        normalized,
        flags=re.I,
    ))
    has_explicit_lock_heading = bool(re.search(
        r"\b(?:sous[ -]?verrou|verrou)\s+\d+(?:[-.]\d+)*\s*:\s*",
        source_text,
        flags=re.I,
    ))
    metric_or_tool_markers = len(re.findall(
        r"\b(?:kpi|metrique|mesure|score|precision|taux|seuil|cible|objectif|"
        r"benchmark|mise a jour|update|outil|visualisation|tableau)\w*\b",
        normalized,
        flags=re.I,
    ))
    return (
        metric_or_tool_markers >= 3
        and not has_explicit_uncertainty
        and not has_explicit_lock_heading
    )


def _is_embedded_use_case_lock(item: Mapping[str, Any]) -> bool:
    """Détecte un verrou appartenant à un cas d'usage, pas au projet analysé.

    Un fichier peut légitimement s'appeler « Use Cases » tout en contenant les
    objectifs du projet. On ne déclasse donc que si le mécanisme du verrou est
    porté par une section de scénario spécifique et n'est pas corroboré par les
    autres sources courantes.
    """
    core_tokens = _tokens(" ".join([
        _clean(item.get("title")),
        _clean(item.get("scientific_lock")),
    ]))
    if not core_tokens:
        return False
    use_case_overlap = 0
    other_overlap = 0
    has_specific_use_case_source = False
    for source in item.get("sources") or []:
        if not isinstance(source, Mapping):
            continue
        document = _norm(source.get("document") or source.get("document_name"))
        section = _norm(source.get("section_title"))
        excerpt_tokens = _tokens(source.get("excerpt") or source.get("text"))
        overlap = len(core_tokens & excerpt_tokens)
        is_specific_use_case = bool(
            _USE_CASE_DOCUMENT_PATTERN.search(document)
            and not _PROJECT_LEVEL_SECTION_PATTERN.search(section)
        )
        if is_specific_use_case:
            has_specific_use_case_source = True
            use_case_overlap = max(use_case_overlap, overlap)
        else:
            other_overlap = max(other_overlap, overlap)
    return has_specific_use_case_source and use_case_overlap >= 2 and other_overlap < 2


def _compact_item(item: Mapping[str, Any], index: int) -> Dict[str, Any]:
    history = item.get("historical_continuity")
    history = history if isinstance(history, Mapping) else {}
    sources = []
    for source in item.get("sources") or []:
        if not isinstance(source, Mapping):
            continue
        excerpt = _clean(source.get("excerpt") or source.get("text"), 360)
        if excerpt:
            sources.append(excerpt)
        if len(sources) >= 4:
            break
    return {
        "current_id": _item_id(item, index),
        "title": _clean(item.get("title") or item.get("technical_axis"), 320),
        "scientific_lock": _clean(item.get("scientific_lock") or item.get("text"), 700),
        "why_not_simple_engineering": _clean(item.get("why_not_simple_engineering"), 520),
        "scientific_strength": _item_scientific_strength(item),
        "operation_statuses": _operation_statuses(item),
        "current_evidence_excerpts": sources,
        "historical_continuity": {
            "status": _clean(history.get("status"), 80),
            "family_title": _clean(history.get("historical_family_title"), 300),
            "warning": "contexte N-1 uniquement; jamais une preuve N",
        },
    }


def _parse_explicit_lock_heading(value: Any) -> Optional[Dict[str, Any]]:
    raw = _clean(value, 900)
    raw = re.sub(r"^\[?\s*SECTION\s*:\s*", "", raw, flags=re.I)
    raw = raw.rstrip(" ]")
    match = _EXPLICIT_LOCK_HEADING_RE.match(raw)
    if not match:
        return None
    kind = "sublock" if _norm(match.group("kind")).startswith("sous") else "parent_lock"
    number = re.sub(r"\s+", "", _clean(match.group("number"), 40))
    title = _clean(match.group("title"), 520)
    year_markers = list(dict.fromkeys(re.findall(r"\b20\d{2}\b", title)))
    title_without_year = _clean(
        re.sub(r"\s*\([^)]*\b20\d{2}\b[^)]*\)\s*$", "", title),
        500,
    )
    if len(title_without_year) < 12:
        return None
    parent_number_match = re.match(r"(\d+)", number)
    parent_number = parent_number_match.group(1) if parent_number_match else ""
    return {
        "kind": kind,
        "number": number,
        "parent_number": parent_number,
        "title": title_without_year,
        "declared_title": title,
        "year_markers": year_markers,
    }


def _fulltext_corpus_dirs(output_dir: Optional[Path]) -> List[Path]:
    if output_dir is None:
        return []
    start = Path(output_dir).resolve()
    candidates: List[Path] = []
    for base in [start, *start.parents[:4]]:
        candidate = base / "documents" / "processed" / "fulltext_rag_v1"
        if candidate.is_dir() and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def _explicit_lock_inventory(
    *,
    current_sections: Optional[Mapping[str, Any]],
    output_dir: Optional[Path],
    current_year: str = "",
) -> Dict[str, Any]:
    """Construit le registre obligatoire des titres Verrou/Sous-verrou.

    Le registre vient uniquement des copies textuelles déjà extraites dans le
    dossier du projet. Il ne modifie jamais les documents sources.
    """
    entries: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, str]] = set()

    def add_entry(
        heading: Any,
        *,
        text_value: Any = "",
        document: Any = "",
        source_path: Any = "",
        origin: str,
    ) -> None:
        parsed = _parse_explicit_lock_heading(heading)
        if not parsed:
            return
        doc = _clean(document, 500)
        signature = (_norm(doc), _norm(parsed.get("declared_title")))
        if signature in seen:
            return
        seen.add(signature)
        temporal_status = "current_document_undated"
        years = parsed.get("year_markers") or []
        if current_year and current_year in years:
            temporal_status = "current_explicit"
        elif years:
            temporal_status = "historical_declared_in_current_corpus"
        entry_id = "EL-" + hashlib.sha1(
            f"{doc}|{parsed.get('declared_title')}".encode("utf-8")
        ).hexdigest()[:12]
        entries.append({
            **parsed,
            "explicit_lock_id": entry_id,
            "document": doc,
            "source_path": _clean(source_path, 900),
            "excerpt": _clean(text_value or heading, 1800),
            "origin": origin,
            "temporal_status": temporal_status,
            "current_document_evidence": True,
        })

    # Premier niveau : les passages déjà sélectionnés par le diagnostic.
    for values in (current_sections or {}).values():
        if not isinstance(values, list):
            continue
        for source in values:
            if not isinstance(source, Mapping):
                continue
            meta = source.get("metadata") if isinstance(source.get("metadata"), Mapping) else {}
            document = source.get("document") or meta.get("document")
            source_path = source.get("source_path") or meta.get("source_path")
            text_value = source.get("text") or source.get("source_text") or ""
            add_entry(
                meta.get("section_title") or source.get("section_title"),
                text_value=text_value,
                document=document,
                source_path=source_path,
                origin="selected_current_section",
            )

    # Niveau exhaustif : titres de section des documents complets déjà extraits.
    scanned_files = 0
    for corpus_dir in _fulltext_corpus_dirs(output_dir):
        for path in sorted(corpus_dir.glob("*.json")):
            if path.name.lower() == "manifest.json":
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            scanned_files += 1
            document = payload.get("document") or path.stem
            source_path = payload.get("source_path") or ""
            for section in payload.get("sections") or []:
                if not isinstance(section, Mapping):
                    continue
                add_entry(
                    section.get("section_title"),
                    text_value=section.get("text"),
                    document=document,
                    source_path=source_path,
                    origin="fulltext_current_document_heading",
                )

    parent_by_number = {
        entry.get("parent_number"): entry
        for entry in entries
        if entry.get("kind") == "parent_lock" and entry.get("parent_number")
    }
    families: List[Dict[str, Any]] = []
    used_ids: Set[str] = set()
    for parent_number, parent in parent_by_number.items():
        children = [
            entry for entry in entries
            if entry.get("kind") == "sublock"
            and entry.get("parent_number") == parent_number
        ]
        family_id = f"ELF-{parent_number}-{hashlib.sha1(_norm(parent.get('title')).encode('utf-8')).hexdigest()[:8]}"
        members = [parent, *children]
        used_ids.update(entry.get("explicit_lock_id") for entry in members)
        families.append({
            "explicit_family_id": family_id,
            "parent_number": parent_number,
            "title": parent.get("title"),
            "parent_lock": parent,
            "sublocks": children,
            "member_explicit_lock_ids": [entry.get("explicit_lock_id") for entry in members],
            "temporal_statuses": list(dict.fromkeys(entry.get("temporal_status") for entry in members)),
        })
    # Un sous-verrou sans parent lisible reste visible dans une famille autonome.
    for entry in entries:
        if entry.get("explicit_lock_id") in used_ids:
            continue
        family_id = "ELF-X-" + hashlib.sha1(
            _norm(entry.get("declared_title")).encode("utf-8")
        ).hexdigest()[:8]
        families.append({
            "explicit_family_id": family_id,
            "parent_number": entry.get("parent_number"),
            "title": entry.get("title"),
            "parent_lock": entry if entry.get("kind") == "parent_lock" else None,
            "sublocks": [entry] if entry.get("kind") == "sublock" else [],
            "member_explicit_lock_ids": [entry.get("explicit_lock_id")],
            "temporal_statuses": [entry.get("temporal_status")],
        })
    return {
        "version": "explicit_lock_inventory_v1",
        "entries": entries,
        "families": families,
        "entries_count": len(entries),
        "families_count": len(families),
        "fulltext_files_scanned": scanned_files,
        "source_policy": "read_only_current_project_extracted_corpus",
    }


def _llm_proposal(llm: Any, compact_items: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    if llm is None or not _bool_env("ENNOSMART_SCIENTIFIC_AXIS_USE_LLM", True):
        return {"ok": False, "used": False, "reason": "llm_disabled"}

    prompt = f"""
Tu consolides les candidats atomiques d'EnnoDiagnostic en axes scientifiques lisibles
par un consultant CIR.

CONTRAT
- Ne force aucun nombre d'axes. Le nombre dépend uniquement des mécanismes et
  incertitudes réellement documentés. Plusieurs sous-problèmes du même mécanisme
  peuvent former un axe; deux mécanismes distincts restent séparés.
- Un axe n'est pas un lot de tâches, un objectif annuel, un besoin utilisateur, une
  activité de packaging/déploiement, ni une difficulté d'organisation.
- Un libellé générique comme « lever les verrous » ou « atteindre les performances
  attendues » va dans contextual_items s'il ne nomme pas lui-même un phénomène ou
  mécanisme technique précis.
- Les éléments marqués classical_engineering vont dans contextual_items. Exception
  stricte : si une continuité N-1 est validée, que des preuves N existent et que
  l'élément partage le mécanisme d'un eligible_candidate courant, il peut être
  conservé comme sous-problème historique de cet axe, jamais comme axe autonome.
- Un élément insufficient_evidence va dans contextual_items, sauf s'il est un
  sous-problème clairement rattaché à un axe porté par au moins un eligible_candidate.
- Un verrou décrit seulement dans un cas d'usage ou un exemple métier reste un
  contexte d'évaluation : il ne devient pas un verrou du projet parent.
- Le titre et l'incertitude de chaque axe reposent UNIQUEMENT sur title,
  scientific_lock, why_not_simple_engineering et current_evidence_excerpts de N.
- historical_continuity sert à ne pas oublier une famille et à qualifier son
  évolution. Son texte ne doit jamais créer un fait courant ni justifier un axe.
- Chaque current_id apparaît exactement une fois : soit dans un axe, soit dans
  contextual_items. Ne supprime aucun élément.
- Pour chaque fusion, explique le mécanisme commun précis. Une simple proximité de
  vocabulaire ou l'appartenance au même projet ne suffit pas.
- Ne découpe pas artificiellement un même mécanisme en une carte par étape. Si
  extraction, structuration, validation et transférabilité forment une même chaîne
  expérimentale et partagent des preuves courantes, regroupe-les sous un axe parent
  et conserve chaque difficulté dans subproblems_current. En revanche, sécurité,
  fiabilité du modèle et architecture distribuée restent séparées si leurs mécanismes
  ou critères d'échec diffèrent. En cas de doute, conserve un axe singleton.

CANDIDATS COURANTS
{json.dumps(list(compact_items), ensure_ascii=False, indent=2)}

JSON uniquement :
{{
  "axes": [
    {{
      "title": "titre précis du verrou parent",
      "current_uncertainty": "incertitude de fond formulée avec les faits N",
      "member_current_ids": ["C1", "C2"],
      "common_mechanism": "mécanisme commun démontré",
      "confidence": 0.0
    }}
  ],
  "contextual_items": [
    {{
      "current_id": "C3",
      "classification": "support_constraint|classical_engineering|insufficient_evidence",
      "reason": "raison courte"
    }}
  ]
}}
""".strip()
    try:
        kwargs = {
            "temperature": float(os.getenv("ENNOSMART_SCIENTIFIC_AXIS_TEMPERATURE", "0.01")),
            "max_output_tokens": int(os.getenv("ENNOSMART_SCIENTIFIC_AXIS_MAX_TOKENS", "2600")),
            "retries": int(os.getenv("ENNOSMART_SCIENTIFIC_AXIS_RETRIES", "1")),
            "json_mode": True,
        }
        try:
            raw = llm.generate(
                prompt,
                request_name="ennodiagnostic:scientific_axis_consolidation",
                **kwargs,
            )
        except TypeError:
            kwargs.pop("json_mode", None)
            raw = llm.generate(prompt, **kwargs)
        data = _extract_json_object(raw)
        if not data:
            return {
                "ok": False,
                "used": True,
                "reason": "invalid_json",
                "raw_preview": _clean(raw, 700),
                "prompt_chars": len(prompt),
            }
        return {"ok": True, "used": True, "data": data, "prompt_chars": len(prompt)}
    except Exception as exc:
        return {"ok": False, "used": True, "error": str(exc), "prompt_chars": len(prompt)}


def _title_is_grounded(title: str, members: Sequence[Mapping[str, Any]]) -> bool:
    title_tokens = _tokens(title)
    if not title_tokens:
        return False
    current_tokens: Set[str] = set()
    for item in members:
        source_text = " ".join(
            _clean(source.get("excerpt") or source.get("text"), 500)
            for source in (item.get("sources") or [])[:8]
            if isinstance(source, Mapping)
        )
        current_tokens.update(_tokens(" ".join([
            _clean(item.get("title")),
            _clean(item.get("scientific_lock")),
            _clean(item.get("why_not_simple_engineering")),
            source_text,
        ])))
    return len(title_tokens & current_tokens) / max(1, len(title_tokens)) >= 0.45


def _members_have_bridge(members: Sequence[Mapping[str, Any]]) -> bool:
    if len(members) <= 1:
        return True
    token_sets = [
        _tokens(" ".join([
            _clean(item.get("title")),
            _clean(item.get("scientific_lock")),
            _clean(item.get("why_not_simple_engineering")),
        ]))
        for item in members
    ]
    connected = {0}
    changed = True
    while changed:
        changed = False
        for left in list(connected):
            for right in range(len(token_sets)):
                if right in connected:
                    continue
                shared = token_sets[left] & token_sets[right]
                containment = len(shared) / max(1, min(len(token_sets[left]), len(token_sets[right])))
                # Les tokens génériques ont déjà été retirés. Un ancrage
                # mécanistique rare (extraction, arbre, transfert, etc.) peut
                # donc former le pont d'une chaîne de sous-problèmes, sans
                # exiger que chaque paire partage tout son vocabulaire.
                if len(shared) >= 1 or containment >= 0.14:
                    connected.add(right)
                    changed = True
    return len(connected) == len(members)


def _dedupe_dicts(values: Iterable[Any], id_keys: Sequence[str]) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    seen: Set[Tuple[Any, ...]] = set()
    for raw in values:
        if not isinstance(raw, Mapping):
            continue
        value = deepcopy(dict(raw))
        signature = tuple(_clean(value.get(key), 500) for key in id_keys)
        if not any(signature):
            signature = (_clean(value, 500),)
        if signature in seen:
            continue
        seen.add(signature)
        output.append(value)
    return output


def _history_for_axis(members: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    histories = [
        item.get("historical_continuity")
        for item in members
        if isinstance(item.get("historical_continuity"), Mapping)
    ]
    statuses = list(dict.fromkeys(
        _clean(history.get("status"), 80) for history in histories
        if _clean(history.get("status"), 80)
    ))
    families = list(dict.fromkeys(
        _clean(history.get("historical_family_title"), 320) for history in histories
        if _clean(history.get("historical_family_title"), 320)
    ))
    previous_years = list(dict.fromkeys(
        _clean(history.get("previous_year"), 20) for history in histories
        if _clean(history.get("previous_year"), 20)
    ))
    historical_methods: List[Dict[str, Any]] = []
    historical_parameters: List[Dict[str, Any]] = []
    historical_results: List[Dict[str, Any]] = []
    historical_locks: List[Dict[str, Any]] = []
    seen_story: Set[Tuple[str, str]] = set()
    for history in histories:
        historical_excerpt = _clean(history.get("historical_excerpt"), 1600)
        historical_title = _clean(history.get("historical_family_title"), 360)
        if historical_excerpt or historical_title:
            signature = ("verrou", historical_excerpt or historical_title)
            if signature not in seen_story:
                seen_story.add(signature)
                historical_locks.append({
                    "role": "verrou",
                    "title": historical_title,
                    "text": historical_excerpt or historical_title,
                    "previous_year": _clean(history.get("previous_year"), 20),
                    "source_path": _clean(history.get("historical_document"), 900),
                    "history_is_current_proof": False,
                })
        story = history.get("historical_story")
        story = story if isinstance(story, Mapping) else {}
        for role, target in (
            ("methode", historical_methods),
            ("parametre", historical_parameters),
            ("resultat", historical_results),
        ):
            for raw in story.get(role) or []:
                if not isinstance(raw, Mapping):
                    continue
                text = _clean(raw.get("text"), 800)
                signature = (role, text)
                if not text or signature in seen_story:
                    continue
                seen_story.add(signature)
                target.append({
                    "role": role,
                    "text": text,
                    "section_title": _clean(raw.get("section_title"), 260),
                    "previous_year": _clean(raw.get("previous_year"), 20),
                    "source_path": _clean(raw.get("source_path"), 900),
                    "history_is_current_proof": False,
                })
    continuity = [status for status in statuses if status in _HISTORICAL_CONTINUITY]
    has_new = "new" in statuses
    if continuity and has_new:
        status = "mixed_continuity_and_new_subproblems"
    elif len(continuity) == 1 and len(statuses) == 1:
        status = continuity[0]
    elif continuity:
        status = "mixed_continuity"
    elif statuses == ["new"]:
        status = "new"
    else:
        status = "uncertain"
    return {
        "status": status,
        "component_statuses": statuses,
        "previous_years": previous_years,
        "historical_family_titles": families,
        "historical_lock_context": historical_locks[:12],
        "historical_method_context": historical_methods[:12],
        "historical_parameter_context": historical_parameters[:12],
        "historical_result_context": historical_results[:12],
        "historical_lock_context_count": len(historical_locks),
        "historical_method_context_count": len(historical_methods),
        "historical_parameter_context_count": len(historical_parameters),
        "historical_result_context_count": len(historical_results),
        "integrated_with_current_lock_card": bool(continuity),
        "history_is_current_proof": False,
        "usage": (
            "integrated_n_minus_1_lock_method_parameter_result_context; "
            "current_year_evidence_required_for_current_claims"
        ),
    }


def _build_axis(
    row: Mapping[str, Any],
    members: Sequence[Mapping[str, Any]],
    member_ids: Sequence[str],
    axis_index: int,
) -> Dict[str, Any]:
    title = _clean(row.get("title"), 320)
    uncertainty = _clean(row.get("current_uncertainty"), 1000) or title
    digest = hashlib.sha1("|".join(sorted(member_ids)).encode("utf-8")).hexdigest()[:16]
    group_id = f"scientific_axis_{axis_index:03d}_{digest}"
    sources = _dedupe_dicts(
        (source for item in members for source in (item.get("sources") or [])),
        ("evidence_id", "passage_id", "document", "excerpt"),
    )
    assessments = _dedupe_dicts(
        (assessment for item in members for assessment in (item.get("frascati_group_assessments") or [])),
        ("group_id",),
    )
    scores = [
        _float(item.get("frascati_score"))
        for item in members
        if item.get("frascati_score") is not None
    ]
    axis_score = sum(scores) / len(scores) if scores else None
    operation_statuses = list(dict.fromkeys(
        status for item in members for status in _operation_statuses(item)
    ))
    if "rnd_core_defendable" in operation_statuses and "insufficient_evidence" in operation_statuses:
        axis_qualification = "mixed_defendable_and_insufficient"
    elif "rnd_core_defendable" in operation_statuses:
        axis_qualification = "rnd_core_defendable"
    elif "rnd_core_partial" in operation_statuses:
        axis_qualification = "rnd_core_partial"
    elif "insufficient_evidence" in operation_statuses:
        axis_qualification = "insufficient_evidence"
    else:
        axis_qualification = "to_review"
    subproblems: List[str] = []
    for item in members:
        subproblems.append(_clean(item.get("title") or item.get("technical_axis"), 420))
        subproblems.extend(_clean(value, 420) for value in (item.get("subproblems_current") or []))
    subproblems = list(dict.fromkeys(value for value in subproblems if value))
    group_ids = list(dict.fromkeys(
        group_id_value for item in members for group_id_value in _group_ids(item)
    ))
    source_ids = list(dict.fromkeys(
        _clean(value, 260)
        for item in members
        for value in (item.get("source_ids") or item.get("candidate_source_ids") or [])
        if _clean(value, 260)
    ))
    return {
        "group_id": group_id,
        "cluster_id": group_id,
        "axis_id": f"AXE-{axis_index}",
        "axis_role": "scientific_axis_candidate",
        "lock_scope": "scientific_axis_parent",
        "display_as_main_lock": True,
        "display_as_lock": True,
        "title": title,
        "technical_axis": title,
        "scientific_lock": uncertainty,
        "text": uncertainty,
        "justification": _clean(row.get("common_mechanism"), 1000),
        "common_mechanism": _clean(row.get("common_mechanism"), 1000),
        "confidence": round(_float(row.get("confidence")), 4),
        "score": round(axis_score, 4) if axis_score is not None else None,
        "frascati_score": round(axis_score, 4) if axis_score is not None else None,
        "frascati_score_source": "mean_of_atomic_component_scores",
        "frascati_component_scores": [round(score, 4) for score in scores],
        "frascati_score_range": {
            "min": round(min(scores), 4) if scores else None,
            "max": round(max(scores), 4) if scores else None,
        },
        "axis_qualification": axis_qualification,
        "component_operation_statuses": operation_statuses,
        "contains_insufficient_evidence_subproblem": "insufficient_evidence" in operation_statuses,
        "frascati_group_assessments": assessments,
        "member_current_ids": list(member_ids),
        "atomic_member_ids": list(member_ids),
        "member_group_ids": group_ids,
        "original_nlp_group_ids": group_ids,
        "subproblems_current": subproblems,
        "source_ids": source_ids,
        "candidate_source_ids": source_ids,
        "sources": sources,
        "document": " ; ".join(list(dict.fromkeys(
            _clean(item.get("document"), 500) for item in members if _clean(item.get("document"), 500)
        ))),
        "historical_continuity": _history_for_axis(members),
        "historical_context_is_current_proof": False,
        "candidate_status": "candidate_to_validate",
        "consultant_status": "en_attente",
        "not_final_cir": True,
        "axis_consolidation_version": VERSION,
    }


def _family_tokens(family: Mapping[str, Any]) -> Set[str]:
    values = [_clean(family.get("title"))]
    values.extend(
        _clean(item.get("title"))
        for item in (family.get("sublocks") or [])
        if isinstance(item, Mapping)
    )
    return _tokens(" ".join(values))


def _axis_tokens(axis: Mapping[str, Any]) -> Set[str]:
    return _tokens(" ".join([
        _clean(axis.get("title")),
        _clean(axis.get("scientific_lock")),
        _clean(axis.get("common_mechanism")),
        " ".join(_clean(value) for value in (axis.get("subproblems_current") or [])),
    ]))


def _explicit_family_sources(family: Mapping[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Mapping[str, Any]] = []
    parent = family.get("parent_lock")
    if isinstance(parent, Mapping):
        rows.append(parent)
    rows.extend(
        item for item in (family.get("sublocks") or []) if isinstance(item, Mapping)
    )
    sources: List[Dict[str, Any]] = []
    for row in rows:
        sources.append({
            "evidence_id": row.get("explicit_lock_id"),
            "passage_id": row.get("explicit_lock_id"),
            "document": row.get("document"),
            "document_name": row.get("document"),
            "source_path": row.get("source_path"),
            "section_title": row.get("declared_title"),
            "excerpt": row.get("excerpt") or row.get("declared_title"),
            "role": "verrou_explicitement_declare",
            "current_project_evidence": True,
            "explicit_lock_inventory": True,
            "temporal_status": row.get("temporal_status"),
        })
    return sources


def _build_explicit_family_axis(
    family: Mapping[str, Any],
    axis_index: int,
) -> Dict[str, Any]:
    family_id = _clean(family.get("explicit_family_id"), 120)
    title = _clean(family.get("title"), 420)
    subproblems = [
        _clean(item.get("title"), 420)
        for item in (family.get("sublocks") or [])
        if isinstance(item, Mapping) and _clean(item.get("title"), 420)
    ]
    parent = family.get("parent_lock")
    parent = parent if isinstance(parent, Mapping) else {}
    uncertainty = _clean(parent.get("excerpt"), 1000) or title
    sources = _explicit_family_sources(family)
    return {
        "group_id": f"scientific_axis_explicit_{axis_index:03d}_{family_id}",
        "cluster_id": f"scientific_axis_explicit_{axis_index:03d}_{family_id}",
        "axis_id": f"AXE-{axis_index}",
        "axis_role": "scientific_axis_candidate",
        "lock_scope": "explicit_declared_lock_family",
        "display_as_main_lock": True,
        "display_as_lock": True,
        "title": title,
        "technical_axis": title,
        "scientific_lock": uncertainty,
        "text": uncertainty,
        "justification": (
            "Verrou explicitement déclaré dans les documents du projet courant ; "
            "sa qualification Frascati et sa continuité doivent rester validées par le consultant."
        ),
        "common_mechanism": title,
        "confidence": 0.72,
        "score": None,
        "frascati_score": None,
        "frascati_score_source": "not_scored_explicit_declared_lock_family",
        "frascati_component_scores": [],
        "frascati_score_range": {"min": None, "max": None},
        "axis_qualification": "insufficient_evidence",
        "component_operation_statuses": ["insufficient_evidence"],
        "contains_insufficient_evidence_subproblem": True,
        "frascati_group_assessments": [],
        "member_current_ids": [],
        "atomic_member_ids": [],
        "member_group_ids": [],
        "original_nlp_group_ids": [],
        "subproblems_current": subproblems,
        "source_ids": [row.get("explicit_lock_id") for row in [parent, *(family.get("sublocks") or [])] if isinstance(row, Mapping)],
        "candidate_source_ids": [row.get("explicit_lock_id") for row in [parent, *(family.get("sublocks") or [])] if isinstance(row, Mapping)],
        "sources": sources,
        "document": " ; ".join(list(dict.fromkeys(
            _clean(source.get("document"), 500) for source in sources if _clean(source.get("document"), 500)
        ))),
        "historical_continuity": {
            "status": "to_confirm_from_current_work",
            "component_statuses": [],
            "previous_years": [],
            "historical_family_titles": [],
            "historical_lock_context": [],
            "historical_method_context": [],
            "historical_parameter_context": [],
            "historical_result_context": [],
            "integrated_with_current_lock_card": False,
            "history_is_current_proof": False,
        },
        "historical_context_is_current_proof": False,
        "candidate_status": "candidate_to_validate",
        "consultant_status": "en_attente",
        "not_final_cir": True,
        "axis_consolidation_version": VERSION,
        "explicit_lock_inventory_family_id": family_id,
        "explicit_lock_inventory_ids": list(family.get("member_explicit_lock_ids") or []),
        "explicit_lock_inventory_coverage": "declared_family_recovered",
    }


def _apply_explicit_lock_coverage(
    axes: List[Dict[str, Any]],
    inventory: Mapping[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    """Rattache chaque famille déclarée ou la restitue comme axe à valider."""
    source_axes = [deepcopy(axis) for axis in axes]
    families = [
        family for family in (inventory.get("families") or [])
        if isinstance(family, Mapping)
    ]
    coverage: List[Dict[str, Any]] = []
    warnings: List[str] = []
    assigned_by_family: Dict[str, List[int]] = {
        _clean(family.get("explicit_family_id"), 120): [] for family in families
    }

    # Chaque axe détecté choisit au maximum une famille explicite, sur la base
    # de son vocabulaire distinctif. Cette affectation univoque empêche qu'un
    # même axe serve artificiellement à couvrir deux parents déclarés.
    for axis_index, axis in enumerate(source_axes):
        axis_tokens = _axis_tokens(axis)
        ranked: List[Tuple[float, str]] = []
        for family in families:
            family_id = _clean(family.get("explicit_family_id"), 120)
            family_tokens = _family_tokens(family)
            shared = family_tokens & axis_tokens
            containment = len(shared) / max(1, min(len(family_tokens), len(axis_tokens)))
            if len(shared) >= 2 and containment >= 0.12:
                ranked.append((len(shared) + containment, family_id))
        if ranked:
            ranked.sort(reverse=True)
            assigned_by_family[ranked[0][1]].append(axis_index)

    output: List[Dict[str, Any]] = []
    consumed_axes: Set[int] = set()
    for family in families:
        family_id = _clean(family.get("explicit_family_id"), 120)
        assigned = assigned_by_family.get(family_id) or []
        if not assigned:
            axis = _build_explicit_family_axis(family, len(output) + 1)
            output.append(axis)
            status = "recovered_as_declared_axis"
            warnings.append(f"famille_verrou_explicite_recuperee:{family_id}")
        else:
            # Le meilleur axe devient le parent consultant ; les autres axes de
            # la même famille sont conservés comme sous-problèmes et preuves,
            # sans répéter plusieurs cartes pour le même parent déclaré.
            assigned.sort(
                key=lambda index: (
                    _float(source_axes[index].get("frascati_score")),
                    len(source_axes[index].get("sources") or []),
                ),
                reverse=True,
            )
            primary_index = assigned[0]
            axis = deepcopy(source_axes[primary_index])
            axis["detected_axis_title_before_explicit_parent"] = axis.get("title")
            explicit_parent_title = _clean(family.get("title"), 420)
            if explicit_parent_title:
                axis["title"] = explicit_parent_title
                axis["technical_axis"] = explicit_parent_title
            consumed_axes.update(assigned)
            absorbed = [source_axes[index] for index in assigned[1:]]
            member_ids = list(axis.get("member_current_ids") or [])
            component_scores = list(axis.get("frascati_component_scores") or [])
            for child in absorbed:
                member_ids.extend(child.get("member_current_ids") or [])
                component_scores.extend(child.get("frascati_component_scores") or [])
            axis["member_current_ids"] = list(dict.fromkeys(member_ids))
            axis["atomic_member_ids"] = list(axis["member_current_ids"])
            axis["absorbed_scientific_axis_titles"] = [
                _clean(child.get("title"), 420) for child in absorbed
            ]
            axis["explicit_lock_inventory_family_id"] = family_id
            axis["explicit_lock_inventory_ids"] = list(family.get("member_explicit_lock_ids") or [])
            axis["explicit_lock_inventory_coverage"] = "covered_by_detected_axis"
            axis["sources"] = _dedupe_dicts(
                [
                    *(axis.get("sources") or []),
                    *(source for child in absorbed for source in (child.get("sources") or [])),
                    *_explicit_family_sources(family),
                ],
                ("evidence_id", "passage_id", "document", "excerpt"),
            )
            declared_subproblems = [
                _clean(item.get("title"), 420)
                for item in (family.get("sublocks") or [])
                if isinstance(item, Mapping) and _clean(item.get("title"), 420)
            ]
            axis["subproblems_current"] = list(dict.fromkeys([
                *(axis.get("subproblems_current") or []),
                *(_clean(child.get("title"), 420) for child in absorbed),
                *(value for child in absorbed for value in (child.get("subproblems_current") or [])),
                *declared_subproblems,
            ]))
            if component_scores:
                axis["frascati_component_scores"] = component_scores
                axis["frascati_score"] = round(sum(component_scores) / len(component_scores), 4)
                axis["score"] = axis["frascati_score"]
                axis["frascati_score_range"] = {
                    "min": round(min(component_scores), 4),
                    "max": round(max(component_scores), 4),
                }
            output.append(axis)
            status = "covered_by_detected_axis"
        coverage.append({
            "explicit_family_id": family_id,
            "title": family.get("title"),
            "coverage_status": status,
            "axis_id": output[-1].get("axis_id"),
            "explicit_lock_ids": list(family.get("member_explicit_lock_ids") or []),
        })

    # Les axes nouveaux ou non rattachés à une famille déclarée restent
    # visibles : le registre explicite est un minimum de couverture, pas une
    # liste fermée qui empêcherait la détection d'un verrou émergent.
    output.extend(
        deepcopy(axis) for index, axis in enumerate(source_axes)
        if index not in consumed_axes
    )

    # Renumérotation stable après récupération des familles manquantes.
    for index, axis in enumerate(output, start=1):
        axis["axis_id"] = f"AXE-{index}"
    axis_id_by_family = {
        axis.get("explicit_lock_inventory_family_id"): axis.get("axis_id")
        for axis in output if axis.get("explicit_lock_inventory_family_id")
    }
    for row in coverage:
        row["axis_id"] = axis_id_by_family.get(row.get("explicit_family_id"), row.get("axis_id"))
    return output, coverage, warnings


def _validate_and_build(
    current_verrous: Sequence[Mapping[str, Any]],
    proposal: Mapping[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    by_id = {
        _item_id(item, index): deepcopy(dict(item))
        for index, item in enumerate(current_verrous, start=1)
    }
    axes: List[Dict[str, Any]] = []
    contextual: List[Dict[str, Any]] = []
    errors: List[str] = []
    assigned: Set[str] = set()

    raw_context = proposal.get("contextual_items") or []
    context_by_id = {
        _clean(row.get("current_id"), 80): dict(row)
        for row in raw_context if isinstance(row, Mapping) and _clean(row.get("current_id"), 80) in by_id
    }

    for raw_axis in proposal.get("axes") or []:
        if not isinstance(raw_axis, Mapping):
            continue
        ids = list(dict.fromkeys(
            _clean(value, 80) for value in (raw_axis.get("member_current_ids") or [])
            if _clean(value, 80) in by_id
        ))
        ids = [value for value in ids if value not in assigned]
        if not ids:
            continue
        members = [by_id[value] for value in ids]

        # Les activités explicitement classiques ne deviennent jamais un axe.
        has_eligible_member = any(
            _item_scientific_strength(by_id[value]) == "eligible_candidate"
            for value in ids
        )
        forced_context = []
        for value in ids:
            item = by_id[value]
            classical = _item_scientific_strength(item) == "classical_engineering"
            attachable_historical_support = bool(
                classical
                and len(ids) > 1
                and has_eligible_member
                and _has_validated_historical_continuity(item)
            )
            if (
                (classical and not attachable_historical_support)
                or _is_generic_meta_lock(item)
                or _is_embedded_use_case_lock(item)
                or _is_metric_or_method_only_lock(item)
            ):
                forced_context.append(value)
        for value in forced_context:
            ids.remove(value)
            generic_meta = _is_generic_meta_lock(by_id[value])
            embedded_use_case = _is_embedded_use_case_lock(by_id[value])
            metric_or_method = _is_metric_or_method_only_lock(by_id[value])
            contextual.append({
                "current_id": value,
                "classification": (
                    "support_constraint"
                    if generic_meta or embedded_use_case or metric_or_method
                    else "classical_engineering"
                ),
                "reason": (
                    "objectif ou meta-verrou sans mécanisme scientifique propre"
                    if generic_meta
                    else "verrou d'un cas d'usage conservé comme contexte d'évaluation du projet parent"
                    if embedded_use_case
                    else "KPI, paramètre ou outil expérimental conservé dans la démarche plutôt que promu en verrou autonome"
                    if metric_or_method
                    else "garde Frascati amont: ingénierie classique"
                ),
                "item": by_id[value],
            })
            assigned.add(value)
        members = [by_id[value] for value in ids]
        if not ids:
            continue

        confidence = _float(raw_axis.get("confidence"))
        title = _clean(raw_axis.get("title"), 320)
        uncertainty = _clean(raw_axis.get("current_uncertainty"), 1000) or title
        common_mechanism = _clean(raw_axis.get("common_mechanism"), 1000)
        if forced_context and len(ids) == 1:
            # Si une fusion contenait un faux verrou/KPI/cas d'usage, son titre
            # parent n'est plus valable après retrait. Le membre restant reprend
            # sa formulation atomique sourcée au lieu d'hériter du mélange.
            title = _clean(members[0].get("title") or members[0].get("technical_axis"), 320)
            uncertainty = _clean(
                members[0].get("scientific_lock") or members[0].get("title"),
                1000,
            )
            common_mechanism = _clean(
                members[0].get("why_not_simple_engineering")
                or members[0].get("scientific_lock"),
                1000,
            )
        if (
            confidence < 0.66
            or len(title) < 14
            or not _title_is_grounded(title, members)
            or not _title_is_grounded(uncertainty, members)
            or (common_mechanism and not _title_is_grounded(common_mechanism, members))
        ):
            errors.append(f"axe_rejete_faible_confiance_ou_titre_non_source:{','.join(ids)}")
            continue
        if len(ids) > 1 and not _members_have_bridge(members):
            errors.append(f"axe_rejete_sans_pont_semantique_courant:{','.join(ids)}")
            continue
        if all(_item_scientific_strength(item) in _CONTEXT_STATUSES for item in members):
            errors.append(f"axe_rejete_sans_noyau_rnd:{','.join(ids)}")
            continue

        validated_row = dict(raw_axis)
        validated_row.update({
            "title": title,
            "current_uncertainty": uncertainty,
            "common_mechanism": common_mechanism,
        })
        axes.append(_build_axis(validated_row, members, ids, len(axes) + 1))
        assigned.update(ids)

    for current_id, row in context_by_id.items():
        if current_id in assigned:
            continue
        item = by_id[current_id]
        # Un candidat R&D correctement ancré ne peut pas disparaître parce que
        # le LLM l'a rangé dans le contexte. Il passera par la réparation
        # singleton ci-dessous si aucun axe valide ne l'a retenu.
        if (
            _item_scientific_strength(item) == "eligible_candidate"
            and not _is_generic_meta_lock(item)
            and not _is_embedded_use_case_lock(item)
            and not _is_metric_or_method_only_lock(item)
        ):
            errors.append(f"contexte_llm_rejete_candidat_rnd_eligible:{current_id}")
            continue
        classification = _clean(row.get("classification"), 80)
        if classification not in {
            "support_constraint", "classical_engineering", "insufficient_evidence",
        }:
            classification = "support_constraint"
        contextual.append({
            "current_id": current_id,
            "classification": classification,
            "reason": _clean(row.get("reason"), 700),
            "item": by_id[current_id],
        })
        assigned.add(current_id)

    missing = [current_id for current_id in by_id if current_id not in assigned]
    for current_id in missing:
        item = by_id[current_id]
        strength = _item_scientific_strength(item)
        if (
            strength == "eligible_candidate"
            and not _is_generic_meta_lock(item)
            and not _is_embedded_use_case_lock(item)
            and not _is_metric_or_method_only_lock(item)
        ):
            # Réparation prudente : un axe proposé trop large ou mal formulé ne
            # doit pas annuler tous les axes valides. Le candidat revient comme
            # axe singleton strictement fondé sur sa formulation atomique.
            fallback_row = {
                "title": _clean(item.get("title") or item.get("technical_axis"), 320),
                "current_uncertainty": _clean(
                    item.get("scientific_lock") or item.get("title"), 1000
                ),
                "common_mechanism": _clean(
                    item.get("why_not_simple_engineering") or item.get("scientific_lock"),
                    1000,
                ),
                "confidence": 0.70,
            }
            axes.append(_build_axis(
                fallback_row,
                [item],
                [current_id],
                len(axes) + 1,
            ))
            assigned.add(current_id)
            errors.append(f"axe_repare_en_singleton:{current_id}")
            continue

        classification = (
            "classical_engineering"
            if strength == "classical_engineering"
            else "insufficient_evidence"
            if strength == "insufficient_evidence"
            else "support_constraint"
        )
        contextual.append({
            "current_id": current_id,
            "classification": classification,
            "reason": (
                "objectif ou meta-verrou sans mécanisme scientifique propre"
                if _is_generic_meta_lock(item)
                else "verrou d'un cas d'usage conservé comme contexte d'évaluation du projet parent"
                if _is_embedded_use_case_lock(item)
                else "KPI, paramètre ou outil expérimental conservé dans la démarche"
                if _is_metric_or_method_only_lock(item)
                else "reclassement prudent après rejet du regroupement proposé"
            ),
            "item": item,
        })
        assigned.add(current_id)
        errors.append(f"candidat_reclasse_en_contexte:{current_id}")

    # Une continuité N-1 confirmée peut rattacher une composante actuellement
    # classique à l'axe R&D courant qui poursuit le même mécanisme. Elle reste
    # un sous-problème/support, jamais un axe R&D autonome.
    remaining_context: List[Dict[str, Any]] = []
    for context_row in contextual:
        current_id = _clean(context_row.get("current_id"), 80)
        item = by_id.get(current_id) or {}
        if not (
            context_row.get("classification") == "classical_engineering"
            and _has_validated_historical_continuity(item)
        ):
            remaining_context.append(context_row)
            continue

        item_tokens = _item_scope_tokens(item)
        best_axis_index: Optional[int] = None
        best_score: Tuple[int, float] = (0, 0.0)
        for axis_index, axis in enumerate(axes):
            member_ids = [
                value for value in (axis.get("member_current_ids") or []) if value in by_id
            ]
            if not any(
                _item_scientific_strength(by_id[value]) == "eligible_candidate"
                for value in member_ids
            ):
                continue
            axis_tokens: Set[str] = set()
            for value in member_ids:
                axis_tokens.update(_item_scope_tokens(by_id[value]))
            shared = item_tokens & axis_tokens
            containment = len(shared) / max(1, min(len(item_tokens), len(axis_tokens)))
            score = (len(shared), containment)
            if score > best_score:
                best_axis_index, best_score = axis_index, score

        if best_axis_index is None or best_score[0] < 3 or best_score[1] < 0.10:
            remaining_context.append(context_row)
            continue

        existing_axis = axes[best_axis_index]
        member_ids = list(existing_axis.get("member_current_ids") or []) + [current_id]
        members = [by_id[value] for value in member_ids if value in by_id]
        row = {
            "title": existing_axis.get("title"),
            "current_uncertainty": existing_axis.get("scientific_lock"),
            "common_mechanism": existing_axis.get("common_mechanism"),
            "confidence": existing_axis.get("confidence"),
        }
        axes[best_axis_index] = _build_axis(row, members, member_ids, best_axis_index + 1)
        errors.append(
            f"support_classique_n1_rattache_a_axe:{current_id}:AXE-{best_axis_index + 1}"
        )

    contextual = remaining_context
    if not axes:
        errors.append("aucun_axe_valide")
    return axes, contextual, errors


def synthesize_scientific_axes(
    *,
    current_verrous: Sequence[Mapping[str, Any]],
    historical_continuity_report: Optional[Mapping[str, Any]] = None,
    current_sections: Optional[Mapping[str, Any]] = None,
    current_year: Any = "",
    llm: Any = None,
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    current = [
        _repair_placeholder_lock(item)
        for item in current_verrous
        if isinstance(item, Mapping)
    ]
    explicit_inventory = _explicit_lock_inventory(
        current_sections=current_sections,
        output_dir=output_dir,
        current_year=_clean(current_year, 20),
    )
    compact = [_compact_item(item, index) for index, item in enumerate(current, start=1)]
    llm_report = _llm_proposal(llm, compact)
    proposal = llm_report.get("data") if isinstance(llm_report.get("data"), Mapping) else {}
    axes, contextual, validation_errors = _validate_and_build(current, proposal)
    axes, explicit_coverage, explicit_warnings = _apply_explicit_lock_coverage(
        axes,
        explicit_inventory,
    )
    validation_errors.extend(explicit_warnings)
    fatal_errors = [
        error for error in validation_errors
        if error == "aucun_axe_valide" or error.startswith("current_ids_non_assignes:")
    ]
    ok = bool(axes) and not fatal_errors

    report: Dict[str, Any] = {
        "ok": ok,
        "version": VERSION,
        "policy": {
            "target_axis_count": None,
            "force_four_axes": False,
            "atomic_candidates_preserved": True,
            "historical_cir_is_current_proof": False,
            "historical_usage": "continuity_and_gap_control_only",
            "fail_open": "keep_reconciled_atomic_candidates",
            "explicit_declared_locks_are_mandatory_coverage": True,
            "llm_may_not_delete_explicit_lock_family": True,
        },
        "input_atomic_count": len(current),
        "axis_count": len(axes),
        "contextual_count": len(contextual),
        "scientific_axes": axes,
        "contextual_items": contextual,
        "atomic_verrous": current,
        "explicit_lock_inventory": explicit_inventory,
        "explicit_lock_family_coverage": explicit_coverage,
        "explicit_lock_family_coverage_counts": {
            "covered_by_detected_axis": sum(
                1 for row in explicit_coverage
                if row.get("coverage_status") == "covered_by_detected_axis"
            ),
            "recovered_as_declared_axis": sum(
                1 for row in explicit_coverage
                if row.get("coverage_status") == "recovered_as_declared_axis"
            ),
        },
        "validation_errors": validation_errors,
        "validation_warnings": validation_errors if ok else [],
        "fatal_validation_errors": fatal_errors,
        "llm_report": llm_report,
        "historical_reconciliation_version": _clean(
            (historical_continuity_report or {}).get("version"), 120
        ),
    }
    if output_dir is not None:
        try:
            path = Path(output_dir) / "scientific_axis_report.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            report["output_path"] = str(path)
        except Exception as exc:
            report["save_error"] = str(exc)
    return report


__all__ = ["VERSION", "synthesize_scientific_axes"]
