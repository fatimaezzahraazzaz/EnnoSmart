# -*- coding: utf-8 -*-
"""
json_to_chunks.py — RAG exact NLP + pré-CIR metadata (V178)

1 élément NLP = 1 chunk principal RAG.
Chaque supporting_passage exploitable devient aussi un chunk de preuve afin de
conserver la page, le paragraphe et les offsets nécessaires au frontend.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Optional, Set

PACK_KEYS = ["objectifs_locaux", "verrous_rnd_locaux", "methodes_locales", "resultats_locaux", "limites_locales", "contributions_locales", "etat_art_local", "parametres_locaux"]
ROLE_LABELS = {"objectifs_locaux": "objectif", "verrous_rnd_locaux": "verrou", "methodes_locales": "methode", "resultats_locaux": "resultat", "limites_locales": "limite", "contributions_locales": "contribution", "etat_art_local": "etat_art", "parametres_locaux": "parametre"}


def _safe_text(x: Any) -> str:
    return str(x or "").strip()


def _safe_float(x: Any) -> float:
    try:
        return float(x or 0.0)
    except Exception:
        return 0.0


def _safe_int(x: Any, default: int = -1) -> int:
    try:
        if x in (None, ""):
            return default
        return int(x)
    except Exception:
        return default


def _safe_bool(x: Any) -> bool:
    if isinstance(x, bool):
        return x
    if isinstance(x, (int, float)):
        return bool(x)
    return str(x or "").strip().lower() in {"1", "true", "yes", "oui"}


def _slug(s: str, max_len: int = 90) -> str:
    s = _safe_text(s).lower()
    s = re.sub(r"[^a-z0-9àâäéèêëîïôöùûüç]+", "_", s, flags=re.I)
    s = re.sub(r"_+", "_", s).strip("_")
    return (s[:max_len] or "chunk")


def _hash_short(*parts: Any, n: int = 10) -> str:
    raw = "|".join(_safe_text(p) for p in parts)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:n]


def _unique_id(base_id: str, used_ids: Set[str], *hash_parts: Any) -> str:
    candidate = _safe_text(base_id)[:220] or f"chunk_{_hash_short(*hash_parts)}"
    if candidate not in used_ids:
        used_ids.add(candidate)
        return candidate
    suffix = _hash_short(*hash_parts, len(used_ids), n=8)
    candidate2 = f"{candidate[:200]}_dup_{suffix}"
    counter = 1
    while candidate2 in used_ids:
        counter += 1
        candidate2 = f"{candidate[:195]}_dup_{suffix}_{counter}"
    used_ids.add(candidate2)
    return candidate2


def _dedupe_key_for_pack_item(item: Dict[str, Any], pack_key: str) -> str:
    """Clé stable pour éviter les doublons sans perdre les vrais signaux NLP."""
    if not isinstance(item, dict):
        return ""
    passage_id = _safe_text(item.get("passage_id") or item.get("id"))
    if passage_id:
        return f"{pack_key}|pid|{passage_id}"
    document = _safe_text(item.get("document"))
    text = _safe_text(item.get("text"))
    return f"{pack_key}|txt|{document}|{text[:360]}"


def _technical_scope(item: Dict[str, Any]) -> str:
    """Retourne le statut technique amont sous une forme stable."""
    return _safe_text(item.get("technical_scope")).lower()


def _is_declared_main_lock(item: Dict[str, Any]) -> bool:
    """Vrai seulement si le NLP a déclaré ce groupe structurant.

    Le RAG ne doit pas inventer ce statut à partir d'un nom de document ou
    d'une similarité. Il le conserve lorsqu'il existe. Les anciens résultats,
    qui ne possèdent pas encore ces champs, restent pris en charge plus bas
    comme format de compatibilité.
    """
    if not isinstance(item, dict):
        return False
    return bool(
        _safe_bool(item.get("display_as_main_lock"))
        or _technical_scope(item) == "project_structuring_lock"
    )


def _select_main_lock_groups(items: Any) -> List[Dict[str, Any]]:
    """Conserve les verrous structurants sans casser les anciens JSON.

    Si au moins un élément porte le contrat V177, seuls ces éléments doivent
    devenir des chunks RAG de rôle ``verrou``. S'il n'existe aucun marquage
    V177, le jeu est traité comme un ancien format et reste inchangé.
    """
    values = [item for item in (items or []) if isinstance(item, dict)]
    marked = [item for item in values if _is_declared_main_lock(item)]
    return marked if marked else values


def _merge_pack_into(target: Dict[str, Any], pack: Any) -> None:
    """
    Fusionne un evidence_pack NLP dans le pack RAG final.

    Point important : on ne doit pas remplacer les vrais verrous NLP par le
    seul pack Frascati qualifié. Le pack Frascati peut contenir uniquement des
    catégories transverses reconstruites, alors que les preuves NLP détaillées
    portent les verrous métier précis.
    """
    if not isinstance(pack, dict):
        return

    for pack_key in PACK_KEYS:
        items = pack.get(pack_key) or []
        if not isinstance(items, list):
            continue

        bucket = target.setdefault(pack_key, [])
        seen = target.setdefault("_seen_keys", {}).setdefault(pack_key, set())

        for item in items:
            if not isinstance(item, dict):
                continue
            key = _dedupe_key_for_pack_item(item, pack_key)
            if not key or key in seen:
                continue
            seen.add(key)
            bucket.append(item)


def get_pack(nlp_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Retourne le pack utilisé pour construire les chunks RAG.

    Correction V31 : avant, si frascati_guard.qualified_pack_for_ennodiagnostic
    existait, il remplaçait tout le reste. Dans certains dossiers, ce pack ne
    contient que 1 ou 2 verrous génériques reconstruits, ce qui fait perdre les
    vrais verrous NLP avant l'arrivée dans Chroma/RAG.

    Maintenant on fusionne les packs détaillés NLP + le pack Frascati.
    Le score Frascati reste une métadonnée ; il ne décide plus seul de ce qui
    entre dans le RAG.
    """
    if not isinstance(nlp_result, dict):
        return {}

    merged: Dict[str, Any] = {}

    candidate_packs = [
        nlp_result.get("multi_document_evidence_pack_for_ennodiagnostic"),
        nlp_result.get("merged_evidence_pack_for_ennodiagnostic"),
        nlp_result.get("evidence_pack_for_ennodiagnostic"),
        nlp_result.get("merged_evidence_pack_before_frascati"),
        nlp_result.get("raw_evidence_pack_before_frascati"),
    ]

    fg = nlp_result.get("frascati_guard") or {}
    if isinstance(fg, dict):
        candidate_packs.append(fg.get("qualified_pack_for_ennodiagnostic"))

    for pack in candidate_packs:
        _merge_pack_into(merged, pack)

    merged.pop("_seen_keys", None)

    # V178 : ``technical_lock_groups`` contient aussi les sous-problèmes
    # locaux. Les recopier ici transformait ces sous-problèmes en verrous RAG.
    # La source normale est donc le pack V177 ``verrous_rnd_locaux`` qui ne
    # contient que les groupes structurants. Les groupes techniques complets
    # ne sont utilisés qu'en migration, puis filtrés par leur contrat amont.
    main_locks = _select_main_lock_groups(merged.get("verrous_rnd_locaux"))
    if main_locks:
        merged["verrous_rnd_locaux"] = main_locks
    elif not merged.get("verrous_rnd_locaux"):
        legacy_sources = [
            nlp_result.get("technical_lock_groups"),
            (fg.get("technical_lock_groups") if isinstance(fg, dict) else None),
        ]
        for source in legacy_sources:
            main_locks = _select_main_lock_groups(source)
            if main_locks:
                merged["verrous_rnd_locaux"] = main_locks
                break

    # Dernier fallback ancien format.
    if not any(isinstance(merged.get(k), list) and merged.get(k) for k in PACK_KEYS):
        fallback = nlp_result.get("multi_document_evidence_pack_for_ennodiagnostic") or nlp_result.get("merged_evidence_pack_for_ennodiagnostic") or nlp_result.get("evidence_pack_for_ennodiagnostic") or {}
        return fallback if isinstance(fallback, dict) else {}

    return merged


def _make_base_id(project_id: str, year: Optional[str], pack_key: str, role: str, item: Dict[str, Any], index: int) -> str:
    passage_id = _safe_text(item.get("passage_id"))
    text = _safe_text(item.get("text"))
    document = _safe_text(item.get("document"))
    year_part = _slug(year or "unknown_year", 20)
    if passage_id:
        base = f"{_slug(project_id, 35)}_{year_part}_{_slug(pack_key, 35)}_{_slug(role, 20)}_{_slug(passage_id, 70)}_{index}_{_hash_short(text)}"
    else:
        base = f"{_slug(project_id, 35)}_{year_part}_{_slug(pack_key, 35)}_{_slug(role, 20)}_{index}_{_hash_short(document, text)}"
    return base[:220]


def _frascati(item: Dict[str, Any]) -> Dict[str, Any]:
    fr = item.get("frascati") or {}
    return fr if isinstance(fr, dict) else {}


def _decision(item: Dict[str, Any]) -> str:
    fr = _frascati(item)
    return _safe_text(fr.get("decision") or item.get("frascati_decision"))


def _frascati_score(item: Dict[str, Any]) -> float:
    fr = _frascati(item)
    return _safe_float(fr.get("frascati_score") or item.get("frascati_score"))


def _explicit_verrou(item: Dict[str, Any]) -> bool:
    fr = _frascati(item)
    return _safe_bool(fr.get("explicit_verrou") or item.get("explicit_verrou"))


def _is_pre_cir(item: Dict[str, Any]) -> bool:
    return _safe_bool(item.get("pre_cir_client") or item.get("client_pre_cir") or item.get("document_type") == "pre_cir_client" or item.get("content_origin") == "client_pre_cir")


def _verrou_candidate_level(item: Dict[str, Any], role: str, pack_key: str) -> str:
    final_role = _safe_text(item.get("final_role")).lower()
    q = _safe_text(item.get("quality_status")).lower()
    decision = _decision(item).lower()
    explicit = _explicit_verrou(item)
    if pack_key != "verrous_rnd_locaux" and role != "verrou":
        return "non_verrou_context"
    if item.get("technical_group_candidate") or item.get("lock_group_candidate"):
        return "technical_group_to_validate"
    if item.get("rejected_as_verrou"):
        return "non_verrou_context"
    if explicit or final_role == "verrou_probable" or decision == "verrou_probable" or q == "frascati_probable":
        return "strong_candidate"
    if "verifier" in final_role or "vérifier" in final_role or "validate" in q or decision == "verrou_a_verifier":
        return "to_validate"
    if final_role in {"verrou_potentiel", "verrou_scientifique"} or decision in {"0", "1"}:
        return "to_validate"
    if "implicite" in final_role or item.get("verrou_source") == "universal_theme_reconstruction":
        return "implicit_to_validate"
    if _is_pre_cir(item):
        return "pre_cir_to_validate"
    return "weak_or_context"


def _should_skip_item(pack_key: str, role: str, item: Dict[str, Any]) -> bool:
    if pack_key == "verrous_rnd_locaux":
        # Le contrat V177 est explicite : un sous-problème local ne doit jamais
        # être indexé sous le rôle RAG ``verrou``.
        if (
            _technical_scope(item) == "local_technical_subproblem"
            or item.get("display_as_main_lock") is False
        ):
            return True
        if item.get("rejected_as_verrou"):
            return True
        q = _safe_text(item.get("quality_status")).lower()
        final_role = _safe_text(item.get("final_role")).lower()
        if q.startswith("rejected"):
            return True
        if final_role in {"methode", "resultat", "parametre", "indice_non_verrou"}:
            return True
    return False


def build_rag_text(role: str, item: Dict[str, Any]) -> str:
    # Le document conservé dans Chroma reste le texte source lisible.
    return _safe_text(item.get("text"))


def _profile_values(item: Dict[str, Any], key: str) -> List[str]:
    profile = item.get("concept_profile") or {}
    if not isinstance(profile, dict):
        return []
    values = profile.get(key) or []
    if not isinstance(values, list):
        return []
    return [_safe_text(value) for value in values if _safe_text(value)]


def build_lock_semantic_text(item: Dict[str, Any], max_supporting_passages: int = 8) -> str:
    """Construit le texte uniquement utilisé pour l'embedding d'un groupe.

    Le texte affiché/restitué reste ``item["text"]``. Cette vue enrichie aide
    MiniLM/BGE à comparer l'inconnue globale du groupe plutôt qu'une phrase
    représentative locale. Aucun fait externe n'est ajouté.
    """
    if not isinstance(item, dict):
        return ""

    parts: List[str] = []

    def add(value: Any) -> None:
        text = _safe_text(value)
        if text and text not in parts:
            parts.append(text)

    add(item.get("candidate_group_label"))
    add(item.get("section_title"))
    add(item.get("text"))
    add(item.get("analysis_text"))
    add(item.get("frascati_explanation"))

    top_terms = _profile_values(item, "top_terms")[:20]
    top_phrases = _profile_values(item, "top_phrases")[:16]
    entities = _profile_values(item, "technical_entities")[:12]
    if top_terms:
        add("Concepts techniques : " + "; ".join(top_terms))
    if top_phrases:
        add("Relations techniques : " + "; ".join(top_phrases))
    if entities:
        add("Entités et grandeurs : " + "; ".join(entities))

    supporting_passages = item.get("supporting_passages") or []
    if isinstance(supporting_passages, list):
        for passage in supporting_passages[:max(1, int(max_supporting_passages))]:
            if isinstance(passage, dict):
                add(passage.get("analysis_text") or passage.get("text") or passage.get("excerpt"))
            else:
                add(passage)

    return "\n".join(parts)


def _first_value(item: Dict[str, Any], keys: List[str], default: Any = "") -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return default


def _passage_text(passage: Any) -> str:
    if isinstance(passage, str):
        return _safe_text(passage)
    if not isinstance(passage, dict):
        return ""
    return _safe_text(
        passage.get("text")
        or passage.get("source_text")
        or passage.get("excerpt")
        or passage.get("content")
    )


def _metadata(project_id: str, pack_key: str, role: str, item: Dict[str, Any], year: Optional[str]) -> Dict[str, Any]:
    source_categories = item.get("source_categories") or []
    if not isinstance(source_categories, list):
        source_categories = [str(source_categories)]
    supporting_passages = item.get("supporting_passages") or []
    supporting_count = len(supporting_passages) if isinstance(supporting_passages, list) else 0
    supporting_documents = item.get("supporting_documents") or []
    if not isinstance(supporting_documents, list):
        supporting_documents = []
    group_documents = "; ".join(
        _safe_text(entry.get("document"))
        for entry in supporting_documents
        if isinstance(entry, dict) and _safe_text(entry.get("document"))
    )
    is_pre_cir = _is_pre_cir(item)
    validation_status = _safe_text(item.get("validation_status") or ("consultant_required" if is_pre_cir else ""))
    first_support = next(
        (p for p in supporting_passages if isinstance(p, dict) and _passage_text(p)),
        {},
    ) if isinstance(supporting_passages, list) else {}
    location = dict(first_support)
    location.update({k: v for k, v in item.items() if v not in (None, "")})

    meta = {
        "project_id": _safe_text(project_id),
        "year": _safe_text(year),
        "annee": _safe_text(year),
        "role": _safe_text(role),
        "pack_key": _safe_text(pack_key),
        "document": _safe_text(item.get("document")),
        "document_id": _safe_text(
            _first_value(location, ["document_id", "doc_id", "file_id"])
        ),
        "source_path": _safe_text(item.get("source_path")),
        "section_title": _safe_text(item.get("section_title")),
        "section_role_hint": _safe_text(item.get("section_role_hint")),
        "content_origin": _safe_text(item.get("content_origin")),
        "document_type": _safe_text(item.get("document_type")),
        "document_type_confidence": _safe_float(item.get("document_type_confidence")),
        "source_policy": _safe_text(item.get("source_policy")),
        "confidence": _safe_float(item.get("confidence")),
        "verrou_score": _safe_float(item.get("verrou_score")),
        "rank_score": _safe_float(item.get("rank_score")),
        "quality_status": _safe_text(item.get("quality_status")),
        "needs_human_validation": bool(item.get("needs_human_validation", role == "verrou" or is_pre_cir)),
        "validation_status": validation_status,
        "final_role": _safe_text(item.get("final_role")),
        "rejected_as_verrou": bool(item.get("rejected_as_verrou", False)),
        "frascati_score": _frascati_score(item),
        "frascati_decision": _decision(item),
        "frascati_interpretation": _safe_text((item.get("frascati_assessment") or {}).get("interpretation")),
        "frascati_risk_level": _safe_text((item.get("frascati_assessment") or {}).get("risk_level")),
        "technical_group_candidate": bool(item.get("technical_group_candidate", False)),
        "technical_scope": _technical_scope(item),
        "display_as_main_lock": _safe_bool(item.get("display_as_main_lock")),
        "explicit_verrou": _explicit_verrou(item),
        "theme_id": _safe_text(item.get("theme_id")),
        "theme_label": _safe_text(item.get("theme_label")),
        "theme_question": _safe_text(item.get("theme_question")),
        "verrou_source": _safe_text(item.get("verrou_source")),
        "source_categories": ", ".join(_safe_text(x) for x in source_categories if _safe_text(x)),
        "pre_cir_client": is_pre_cir,
        "client_pre_cir": is_pre_cir,
        "not_final_cir": bool(item.get("not_final_cir", is_pre_cir)),
        "chunk_level": "nlp_main_item",
        "is_supporting_passage": False,
        "supporting_passages_count": supporting_count,
        "lock_group_id": _safe_text(item.get("lock_group_id")),
        "lock_group_candidate": bool(item.get("lock_group_candidate", False)),
        "lock_group_is_final_cir_verrou": bool(item.get("lock_group_is_final_cir_verrou", False)),
        "lock_group_evidence_count": _safe_int(item.get("evidence_count"), 0),
        "lock_group_source_span_count": _safe_int(item.get("source_span_count"), 0),
        "lock_group_documents": group_documents,
        "passage_id": _safe_text(
            _first_value(location, ["passage_id", "id"])
        ),
        "page_number": _safe_int(
            _first_value(location, ["page_number", "page", "page_index"]),
        ),
        "paragraph_index": _safe_int(
            _first_value(location, ["paragraph_index", "paragraph", "block_index"]),
        ),
        "char_start": _safe_int(
            _first_value(location, ["char_start", "start_char", "start"]),
        ),
        "char_end": _safe_int(
            _first_value(location, ["char_end", "end_char", "end"]),
        ),
    }
    meta["verrou_candidate_level"] = _verrou_candidate_level(item, role, pack_key)
    return meta


def _supporting_passage_metadata(parent_meta: Dict[str, Any], passage: Dict[str, Any]) -> Dict[str, Any]:
    """Construit les métadonnées de la preuve, avec sa propre source.

    Un groupe de verrou peut contenir des passages provenant de plusieurs
    documents. Hériter seulement du document du groupe parent rendait tous les
    liens frontend cliquables vers le mauvais fichier. Le rôle RAG du chunk
    reste celui du groupe (``verrou``), mais la localisation vient toujours du
    passage enfant.
    """
    meta = dict(parent_meta)
    meta.update({
        "document": _safe_text(_first_value(passage, ["document", "file_name"], parent_meta.get("document"))),
        "document_id": _safe_text(_first_value(passage, ["document_id", "doc_id", "file_id"], parent_meta.get("document_id"))),
        "source_path": _safe_text(_first_value(passage, ["source_path", "path", "file_path"], parent_meta.get("source_path"))),
        "section_title": _safe_text(_first_value(passage, ["section_title", "title"], parent_meta.get("section_title"))),
        "section_role_hint": _safe_text(_first_value(passage, ["section_role_hint", "role_hint"], parent_meta.get("section_role_hint"))),
        "content_origin": _safe_text(_first_value(passage, ["content_origin"], parent_meta.get("content_origin"))),
        "document_type": _safe_text(_first_value(passage, ["document_type"], parent_meta.get("document_type"))),
        "source_policy": _safe_text(_first_value(passage, ["source_policy"], parent_meta.get("source_policy"))),
        "source_semantic_role": _safe_text(_first_value(passage, ["semantic_role", "role"], "")),
        "frascati_score": _safe_float(_first_value(passage, ["frascati_score"], parent_meta.get("frascati_score"))),
        "frascati_decision": _safe_text(_first_value(passage, ["frascati_decision"], parent_meta.get("frascati_decision"))),
        "final_role": _safe_text(_first_value(passage, ["final_role"], parent_meta.get("final_role"))),
    })
    return meta


def nlp_json_to_chunks(project_id: str, nlp_result: Dict[str, Any], year: Optional[str] = None) -> List[Dict[str, Any]]:
    pack = get_pack(nlp_result)
    chunks: List[Dict[str, Any]] = []
    used_ids: Set[str] = set()
    for pack_key in PACK_KEYS:
        role = ROLE_LABELS.get(pack_key, pack_key)
        items = pack.get(pack_key, []) or []
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            if _should_skip_item(pack_key, role, item):
                continue
            text = _safe_text(item.get("text"))
            if len(text) < 40:
                continue
            base_id = _make_base_id(project_id, year, pack_key, role, item, i)
            chunk_id = _unique_id(base_id, used_ids, project_id, year, pack_key, role, i, text)
            meta = _metadata(project_id, pack_key, role, item, year)
            meta["rag_chunk_id"] = chunk_id
            meta["original_passage_id"] = _safe_text(item.get("passage_id"))
            meta["nlp_item_index"] = i
            main_chunk = {
                "id": chunk_id,
                "text": build_rag_text(role, item),
                "source_text": text,
                "metadata": meta,
                "raw_item": item,
            }
            if role == "verrou" and bool(item.get("lock_group_candidate") or item.get("lock_group_id")):
                main_chunk["embedding_text"] = build_lock_semantic_text(item)
                main_chunk["metadata"]["embedding_text_strategy"] = "lock_group_multiview_v169"
            chunks.append(main_chunk)

            # Les passages de preuve sont indexés séparément. Le frontend peut
            # ainsi ouvrir le document au bon emplacement au lieu d'afficher
            # seulement un nom de fichier dans le texte rédigé.
            supporting_passages = item.get("supporting_passages") or []
            if not isinstance(supporting_passages, list):
                supporting_passages = []

            for passage_index, passage in enumerate(supporting_passages):
                passage_text = _passage_text(passage)
                if len(passage_text) < 20:
                    continue

                passage_data = passage if isinstance(passage, dict) else {"text": passage_text}
                passage_base_id = f"{chunk_id[:175]}_evidence_{passage_index}"
                passage_chunk_id = _unique_id(
                    passage_base_id,
                    used_ids,
                    chunk_id,
                    passage_index,
                    passage_text,
                )
                passage_meta = _supporting_passage_metadata(meta, passage_data)
                passage_meta.update({
                    "rag_chunk_id": passage_chunk_id,
                    "parent_rag_chunk_id": chunk_id,
                    "chunk_level": "supporting_passage",
                    "is_supporting_passage": True,
                    "passage_id": _safe_text(
                        _first_value(passage_data, ["passage_id", "id"], f"{chunk_id}:p{passage_index}")
                    ),
                    "page_number": _safe_int(
                        _first_value(passage_data, ["page_number", "page", "page_index"], meta.get("page_number"))
                    ),
                    "paragraph_index": _safe_int(
                        _first_value(passage_data, ["paragraph_index", "paragraph", "block_index"], meta.get("paragraph_index"))
                    ),
                    "char_start": _safe_int(
                        _first_value(passage_data, ["char_start", "start_char", "start"], meta.get("char_start"))
                    ),
                    "char_end": _safe_int(
                        _first_value(passage_data, ["char_end", "end_char", "end"], meta.get("char_end"))
                    ),
                })
                chunks.append({
                    "id": passage_chunk_id,
                    "text": passage_text,
                    "embedding_text": passage_text,
                    "source_text": passage_text,
                    "metadata": passage_meta,
                    "raw_item": passage_data,
                })
    return chunks
