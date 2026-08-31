"""Refresh versioned deterministic assessments without extraction or reindexing."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .demarche_legibility import VERSION as DEMARCHE_VERSION
from .frascati_assessment import VERSION as FRASCATI_VERSION


def refresh_assessment_payload(payload: dict) -> dict:
    guard = payload.get('frascati_guard') or {}
    old = guard.get('frascati_assessment') or {}
    old_demarche = old.get('demarche_legibility') or {}
    if old.get('version') == FRASCATI_VERSION and old_demarche.get('version') == DEMARCHE_VERSION:
        return {'refreshed': False, 'assessment_version': FRASCATI_VERSION, 'demarche_version': DEMARCHE_VERSION}
    groups = guard.get('technical_lock_groups')
    if not isinstance(groups, list):
        raise ValueError('Groupes NLP absents : préparer les sources avant le diagnostic.')

    # Assessment only: no FastJudge, grouping, extraction, LLM or Chroma access.
    from .frascati_guard import assess_groups
    assessed = assess_groups(groups)
    report = assessed['frascati_assessment']
    demarche = report['demarche_legibility']
    by_id = {str(g.get('lock_group_id') or g.get('passage_id')): g for g in assessed['technical_lock_groups']}
    derived_keys = ('frascati_assessment', 'frascati_decision', 'frascati_recommendation',
                    'frascati_recommendation_label', 'frascati_risk_level')
    pack = payload.get('multi_document_evidence_pack_for_ennodiagnostic') or {}
    for container in (guard, pack):
        container['frascati_assessment'] = report
        for key in ('technical_lock_groups', 'verrous_rnd_locaux', 'secondary_technical_groups'):
            for group in container.get(key) or []:
                replacement = by_id.get(str(group.get('lock_group_id') or group.get('passage_id')))
                if replacement:
                    group.update({k: replacement[k] for k in derived_keys})
    recommendation = report['eligibility_recommendation']
    guard.update(decision=recommendation, eligibility_recommendation=recommendation,
                 recommendation_label=report['recommendation_label'], demarche_legibility=demarche)
    for key in ('risk_report', 'consultant_view'):
        view = guard.get(key)
        if isinstance(view, dict):
            for field in list(view):
                if field in report:
                    view[field] = report[field]
            if 'decision' in view:
                view['decision'] = recommendation
            if 'global_frascati_score' in view:
                view['global_frascati_score'] = report['documentary_coverage']
            if 'display_status' in view:
                view['display_status'] = 'candidat_potentiellement_eligible_a_valider' if recommendation else 'non_eligible_potentiel_a_revoir_humainement'
    payload['demarche_legibility'] = demarche
    stats = payload.setdefault('stats', {})
    stats.update(eligibility_assessment_score=report['eligibility_assessment_score'],
                 rnd_defensibility_index=report['rnd_defensibility_index'],
                 global_frascati_score=report['documentary_coverage'])
    for key in list(stats):
        if key.startswith('demarche_'):
            source_key = key[len('demarche_'):] + '_count'
            if source_key in demarche:
                stats[key] = demarche[source_key]
    audit = {
        'refreshed': True, 'previous_demarche_version': old_demarche.get('version'),
        'assessment_version': FRASCATI_VERSION, 'demarche_version': DEMARCHE_VERSION,
        'previous_score': old.get('eligibility_assessment_score'),
        'score': report['eligibility_assessment_score'],
        'refreshed_at': datetime.now(timezone.utc).isoformat(),
        'sources_changed': False, 'lock_groups_changed': False, 'chroma_reindexed': False,
    }
    payload['assessment_refresh'] = audit
    return audit


def refresh_prepared_assessment(path: str | Path) -> dict:
    path = Path(path)
    payload = json.loads(path.read_text(encoding='utf-8-sig'))
    audit = refresh_assessment_payload(payload)
    if not audit['refreshed']:
        return audit
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.assessment-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return audit
