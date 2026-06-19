# -*- coding: utf-8 -*-
from __future__ import annotations
import re
from typing import Any, Dict, List, Optional
from .cir_structure_detector import detect_cir_structure
from .cir_section_mapper import section_type_to_role, section_type_to_pack_key, section_label
try:
    from ..frascati_guard import apply_frascati_guard
except Exception:
    apply_frascati_guard = None

PACK_KEYS = ['objectifs_locaux','verrous_rnd_locaux','methodes_locales','resultats_locaux','limites_locales','contributions_locales','etat_art_local','parametres_locaux']
MAX_TEXT_CHARS = 15000


def _empty_pack() -> Dict[str, List[Dict[str, Any]]]:
    return {k: [] for k in PACK_KEYS}


def _norm(text: str) -> str:
    text = str(text or '').lower()
    tr = str.maketrans('àâäéèêëîïôöùûüç’', "aaaeeeeiioouuuc'")
    text = text.translate(tr)
    text = re.sub(r'[^a-z0-9]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def _safe_id(text: str) -> str:
    return re.sub(r'[^a-zA-Z0-9]+', '_', str(text or 'doc')).strip('_')[:80]


def _shorten(text: str, max_chars: int = MAX_TEXT_CHARS) -> str:
    text = str(text or '').strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    last = max(cut.rfind('.'), cut.rfind('\n'))
    return (cut[:last+1] if last > 700 else cut).strip()


def _is_real_content(section: Dict[str, Any]) -> bool:
    title = str(section.get('title') or '').strip()
    text = str(section.get('text') or '').strip()
    stype = section.get('section_type')
    if stype in {'administratif','annexe','project_title'}:
        return False
    if not text or len(text) < 70:
        return False
    if _norm(text) == _norm(title):
        return False
    # Supprime les sections presque uniquement figure/tableau/plan
    low = _norm(text[:600])
    if any(k in low for k in ['echelle scale', 'modification date nom name', 'planche sheet']) and len(text) < 500:
        return False
    return True


def _make_item(section: Dict[str, Any], document: str, source_path: str) -> Optional[Dict[str, Any]]:
    stype = section.get('section_type') or 'unknown'
    key = section_type_to_pack_key(stype)
    if not key:
        return None
    role = section_type_to_role(stype)
    title = str(section.get('title') or '').strip()
    text = str(section.get('text') or '').strip()
    final_text = f'{title}\n{text}'.strip() if title and not _norm(text).startswith(_norm(title)[:30]) else text
    return {
        'passage_id': f"cir_{_safe_id(document)}_{_safe_id(section.get('section_id') or title)}",
        'document': document,
        'source_path': source_path,
        'source_type': 'cir_structured',
        'content_origin': 'cir_structured',
        'source_weight': 1.15,
        'section_id': section.get('section_id'),
        'section_title': title,
        'section_type': stype,
        'section_label': section_label(stype),
        'text': _shorten(final_text),
        'role': role,
        'model_confidence': 1.0,
        'confidence': 1.0,
        'verrou_score': 1.0 if role == 'verrou' else 0.0,
        'quality_status': 'section_direct',
        'rank_score': 1.2 if role == 'verrou' else 1.0,
        'accepted_for_synthesis': True,
        'needs_human_validation': role == 'verrou',
    }


def _dedupe_add(pack: Dict[str, List[Dict[str, Any]]], key: str, item: Dict[str, Any]) -> None:
    sig = (_norm(item.get('document','')), _norm(item.get('section_id') or item.get('section_title') or ''), _norm(item.get('text',''))[:160])
    for old in pack.get(key, []):
        osig = (_norm(old.get('document','')), _norm(old.get('section_id') or old.get('section_title') or ''), _norm(old.get('text',''))[:160])
        if sig == osig:
            return
    pack.setdefault(key, []).append(item)


def _build_pack(sections: List[Dict[str, Any]], document: str, source_path: str) -> Dict[str, List[Dict[str, Any]]]:
    pack = _empty_pack()
    for sec in sections:
        if not _is_real_content(sec):
            continue
        item = _make_item(sec, document, source_path)
        if not item:
            continue
        key = section_type_to_pack_key(sec.get('section_type'))
        if key:
            _dedupe_add(pack, key, item)
    return pack


def _merge_pack(dst: Dict[str, List[Dict[str, Any]]], src: Dict[str, List[Dict[str, Any]]]) -> None:
    for k, arr in (src or {}).items():
        for item in arr or []:
            _dedupe_add(dst, k, item)


def _make_outline(sections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    roots, stack = [], []
    for sec in sections:
        title = str(sec.get('title') or '').strip()
        if not title:
            continue
        level = int(sec.get('level') or 1)
        node = {
            'section_id': sec.get('section_id'), 'title': title, 'level': level,
            'section_type': sec.get('section_type') or 'unknown', 'section_label': section_label(sec.get('section_type') or 'unknown'),
            'page': sec.get('page'), 'text_chars': len(str(sec.get('text') or '')), 'children': []
        }
        while stack and int(stack[-1]['level']) >= level:
            stack.pop()
        if stack:
            stack[-1]['children'].append(node)
        else:
            roots.append(node)
        stack.append(node)
    return roots


def run_cir_pipeline(documents: List[Dict[str, Any]]) -> Dict[str, Any]:
    all_pack = _empty_pack()
    all_sections: List[Dict[str, Any]] = []
    outline_by_document = []
    reports = []
    used = 0
    for doc in documents or []:
        name = doc.get('document') or doc.get('file_name') or 'document'
        source_path = doc.get('source_path') or ''
        report = detect_cir_structure(doc.get('text') or '', document=name)
        sections = report.get('sections') or []
        reports.append({k: report.get(k) for k in ['document','is_cir_structured','confidence','keyword_hits','heading_count','numbered_heading_count','important_heading_hits']})
        reports[-1]['sections'] = len(sections)
        if not report.get('is_cir_structured'):
            continue
        used += 1
        for s in sections:
            s['document'] = name
        all_sections.extend(sections)
        _merge_pack(all_pack, _build_pack(sections, name, source_path))
        outline_by_document.append({'document': name, 'outline': _make_outline(sections)})
    frascati_guard = None
    qualified_pack = all_pack
    if apply_frascati_guard is not None:
        try:
            frascati_guard = apply_frascati_guard(
                pack=all_pack,
                documents=documents or [],
                mode='cir_audit',
                domain=None,
            )
            qualified_pack = frascati_guard.get('qualified_pack_for_ennodiagnostic') or all_pack
        except Exception as e:
            frascati_guard = {'error': f'FrascatiGuard CIR audit failed: {e}'}
            qualified_pack = all_pack

    return {
        'version': 'v26_cir_structured_frascati_audit',
        'pipeline_type': 'cir_structured',
        'stats': {
            'documents_input': len(documents or []), 'documents_used': used, 'sections_detected': len(all_sections),
            'verrous_structured': len(all_pack['verrous_rnd_locaux']), 'objectifs_structured': len(all_pack['objectifs_locaux']),
            'methodes_structured': len(all_pack['methodes_locales']), 'resultats_structured': len(all_pack['resultats_locaux']),
            'frascati_verrous_probables': len((frascati_guard or {}).get('verrous_probables', [])) if isinstance(frascati_guard, dict) else 0,
            'frascati_verrous_a_verifier': len((frascati_guard or {}).get('verrous_a_verifier', [])) if isinstance(frascati_guard, dict) else 0,
            'frascati_faux_verrous_rejetes': len((frascati_guard or {}).get('faux_verrous_rejetes', [])) if isinstance(frascati_guard, dict) else 0,
        },
        'detection_reports': reports,
        'outline_by_document': outline_by_document,
        'sections': all_sections,
        'evidence_pack_before_frascati': all_pack,
        'frascati_guard': frascati_guard,
        'evidence_pack_for_ennodiagnostic': qualified_pack,
    }


def run_pipeline(documents: List[Dict[str, Any]]) -> Dict[str, Any]:
    return run_cir_pipeline(documents)
