# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Dict, Any, List

from .cleaner import clean_text
from .candidates import make_candidates
from .models import apply_models
from .filter import apply_quality_filter, thresholds
from .grouping import group_items, build_evidence_pack
from .verrou_builder import enrich_evidence_pack_with_verrous
from .domain_classifier import classify_domain
from .structure_analyzer import analyze_structure
from .document_type_classifier import enrich_document_type
from .nlp_document_aggregator import build_ennodiagnostic_nlp_context
from .frascati_guard import apply_frascati_guard

VERSION = 'v26_multidoc_frascati_guard'


def _safe_clean_text(raw_text: str) -> str:
    raw_text = str(raw_text or "")
    cleaned = clean_text(raw_text)
    # Sécurité : ne jamais perdre un document parce que le cleaner a tout supprimé.
    if not str(cleaned or "").strip() and raw_text.strip():
        return raw_text
    return cleaned


def _domain_text_from_docs(docs: List[Dict[str, Any]]) -> str:
    core_docs = [d for d in docs if d.get("source_policy") in {"core_or_useful", "secondary"}]
    selected = core_docs or docs
    # On ne touche pas à la logique domaine : on garde le classifier existant,
    # mais on donne un texte représentatif multi-documents.
    parts = []
    for d in selected:
        parts.append(str(d.get("document", "")))
        parts.append(str(d.get("text") or "")[:12000])
    return "\n".join(parts)[:250000]


def run_nlp_pipeline_fast(
    documents: List[Dict[str, Any]],
    max_candidates: int = 700,
    include_state_of_art_in_candidates: bool = True,
    include_cir_final: bool = False,
    top_k=None,
) -> Dict[str, Any]:
    skipped = []
    docs: List[Dict[str, Any]] = []

    for d in documents or []:
        d = dict(d or {})
        raw_text = str(d.get('text') or '')
        d['text'] = _safe_clean_text(raw_text)

        if not d['text'].strip():
            skipped.append({'document': d.get('document'), 'reason': 'empty_text'})
            continue
        if d.get('content_origin') == 'cir_final' and not include_cir_final:
            skipped.append({'document': d.get('document'), 'reason': 'cir_final_excluded'})
            continue
        if d.get('content_origin') == 'state_of_art' and not include_state_of_art_in_candidates:
            skipped.append({'document': d.get('document'), 'reason': 'state_of_art_excluded'})
            continue

        # Enrichissement documentaire générique AVANT structure/candidats.
        d = enrich_document_type(d)
        docs.append(d)

    domain_text = _domain_text_from_docs(docs)
    domain = classify_domain(domain_text) if domain_text.strip() else {}
    domain_code = domain.get("domain_code_niv2") or domain.get("domain_code_niv3") or None

    # Le structure_analyzer donne une structure, mais ne remplace pas le vrai document_type.
    structures = []
    for doc in docs:
        try:
            s = analyze_structure(doc.get('text', '')) or {}
        except Exception:
            s = {}
        if "document_type" in s and "structure_type" not in s:
            s["structure_type"] = s.get("document_type")
        structures.append(s)

    candidates = make_candidates(
        docs,
        max_candidates=max_candidates,
        section_info=structures,
        min_candidates_per_doc=8,
    )

    judged = apply_models(candidates) if candidates else []
    filtered = apply_quality_filter(judged)
    kept = filtered.get('kept', []) or []
    groups = group_items(kept)
    pack_before_frascati = build_evidence_pack(groups, top_k=top_k)
    pack_before_frascati = enrich_evidence_pack_with_verrous(pack_before_frascati, domain_context=domain_code)

    # FrascatiGuard ne remplace pas les modèles NLP :
    # il qualifie leurs signaux, filtre les faux verrous et remonte les verrous implicites.
    frascati_guard = apply_frascati_guard(
        pack=pack_before_frascati,
        documents=docs,
        mode="raw_construction",
        domain=domain,
    )
    pack = frascati_guard.get("qualified_pack_for_ennodiagnostic") or pack_before_frascati

    multidoc_context = build_ennodiagnostic_nlp_context(documents=docs, pack=pack)
    multidoc_pack = multidoc_context["multi_document_evidence_pack_for_ennodiagnostic"]

    stats = {
        'documents_input': len(documents or []),
        'documents_used': len(docs),
        'documents_skipped': len(skipped),
        'candidates': len(candidates),
        **(filtered.get('stats', {}) if isinstance(filtered, dict) else {}),
        'groups': len(groups),
        'verrous_final': len(pack.get('verrous_rnd_locaux', [])),
        'frascati_verrous_probables': len(frascati_guard.get('verrous_probables', [])),
        'frascati_verrous_a_verifier': len(frascati_guard.get('verrous_a_verifier', [])),
        'frascati_faux_verrous_rejetes': len(frascati_guard.get('faux_verrous_rejetes', [])),
        'raw_sections': sum(int(s.get('num_sections', 0) or 0) for s in structures),
        'documents_with_evidence': multidoc_context.get('coverage_report', {}).get('documents_with_evidence'),
    }

    return {
        'version': VERSION,
        'logic': 'multi-doc NLP coverage + FrascatiGuard non-bloquant + balanced evidence pack + existing domain classifier unchanged',
        'stats': stats,
        'domain_detection': domain,
        'skipped_documents': skipped,
        'thresholds': thresholds(),
        'documents_summary': [
            {
                k: d.get(k)
                for k in [
                    'document', 'extension', 'loader', 'chars', 'content_origin', 'source_weight',
                    'document_type', 'source_policy', 'document_weight', 'document_type_confidence',
                    'document_type_reason', 'reason'
                ]
            }
            for d in docs
        ],
        'evidence_pack_before_frascati': pack_before_frascati,
        'frascati_guard': frascati_guard,
        'evidence_pack_for_ennodiagnostic': pack,
        # Nouveau : contexte qui représente tous les documents.
        'document_evidence_summaries': multidoc_context['document_evidence_summaries'],
        'multi_document_evidence_pack_for_ennodiagnostic': multidoc_pack,
        'coverage_report': multidoc_context['coverage_report'],
        # Debug utile.
        'kept_items': kept[:3000],
        'groups_preview': groups[:300],
    }


# Alias de compatibilité
run_pipeline = run_nlp_pipeline_fast
