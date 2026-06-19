# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Dict, Any, List
import re

STRICT = {'objectif':0.72,'verrou':0.66,'methode':0.70,'parametre':0.70,'resultat':0.70,'limite':0.70,'contribution':0.70}
RECALL = {'objectif':0.58,'verrou':0.54,'methode':0.58,'parametre':0.58,'resultat':0.58,'limite':0.58,'contribution':0.58}
STRICT_VERROU_DETECTOR = 0.52
RECALL_VERROU_DETECTOR = 0.40
VERROU_SCORE_BOOST_THRESHOLD = 0.68
MAX_OTHER_ROLE_CONFIDENCE_TO_BOOST = 0.55
FORBIDDEN_BOOST_ROLES = {'objectif', 'methode', 'parametre', 'contribution'}
BAD_SYNTH = ['tapez ici','nom de la présentation','document security','charte graphique','diffusion','quelles sont les questions','quels sont les enjeux','quels environnements','quelles démarches']
CONTEXT_ONLY_TYPES = {'norme_reglementation', 'plan_schema', 'administratif', 'template_formulaire'}
SECONDARY_TYPES = {'notice_memoire_technique', 'etat_art_bibliographie'}


def _sf(x: Any) -> float:
    try:
        return float(x or 0.0)
    except Exception:
        return 0.0


def safe_for_synthesis(item: Dict[str, Any]) -> bool:
    text = str(item.get('text','')).lower()
    dtype = item.get('document_type')
    role = item.get('role')

    if item.get('quality_status') not in {'strict','recall','verrou_boosted'}:
        return False
    if len(text) < 45:
        return False
    if any(x in text for x in BAD_SYNTH):
        return False
    if item.get('content_origin') in {'metadata', 'cir_final'}:
        return False

    # Règle générique : les documents contextuels peuvent soutenir méthode/paramètre,
    # mais pas devenir objectif/verrou/contribution principaux.
    if dtype in CONTEXT_ONLY_TYPES and role in {'objectif', 'verrou', 'contribution'}:
        item['non_verrou_reason'] = 'document contextuel/normatif : contrainte ou contexte, pas preuve R&D centrale'
        return False

    if dtype in SECONDARY_TYPES and role == 'verrou':
        item['non_verrou_reason'] = 'document secondaire : verrou à vérifier, pas verrou solide'
        return False

    if '€' in text or 'coût' in text or 'cout' in text:
        return False
    if role == 'parametre' and re.search(r'\b(ref|réf|kg|g\b)', text):
        return False
    return True


def rank_score(item: Dict[str, Any]) -> float:
    conf = _sf(item.get('confidence') or item.get('model_confidence'))
    verrou = _sf(item.get('verrou_score'))
    weight = _sf(item.get('source_weight') or item.get('document_weight') or 0.75)
    role = item.get('role')
    dtype = item.get('document_type')
    base = conf * weight

    if role == 'verrou':
        base += 0.28 * verrou
    else:
        base += 0.10 * verrou

    if item.get('content_origin') == 'project_core':
        base *= 1.08
    if item.get('content_origin') == 'state_of_art':
        base *= 0.70
    if item.get('content_origin') == 'unknown':
        base *= 0.92

    if dtype in {'concept_projet','brevet','preuve_depot_brevet','rapport_test','note_projet'}:
        base *= 1.20
    elif dtype in {'norme_reglementation','plan_schema','administratif','template_formulaire'}:
        base *= 0.35
    elif dtype in {'notice_memoire_technique','etat_art_bibliographie'}:
        base *= 0.65

    hint = item.get('section_role_hint')
    if hint == role:
        base *= 1.08
    elif hint in {'contrainte'} and role == 'verrou':
        base *= 0.55

    return round(base, 4)


def apply_quality_filter(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    kept, rejected = [], []
    for it in items:
        role = it.get('role', 'bruit')
        conf = _sf(it.get('confidence'))
        vs = _sf(it.get('verrou_score'))
        dtype = it.get('document_type')

        scores = it.get('scores', {}) or {}
        max_other_role_conf = max([_sf(s) for r, s in scores.items() if r != 'verrou' and r in STRICT] or [0.0])

        ok = False
        status = 'rejected'

        # Interdiction générique : norme/plan/admin/template ne deviennent pas verrous R&D.
        if dtype in CONTEXT_ONLY_TYPES and role == 'verrou':
            ok = False
            status = 'rejected_context_document_as_verrou'
            it['non_verrou_reason'] = 'document contextuel/normatif : contrainte, pas verrou R&D'

        elif (vs >= VERROU_SCORE_BOOST_THRESHOLD and
              max_other_role_conf < MAX_OTHER_ROLE_CONFIDENCE_TO_BOOST and
              role not in FORBIDDEN_BOOST_ROLES and
              dtype not in CONTEXT_ONLY_TYPES):
            ok = True
            status = 'verrou_boosted'
            if role not in ('verrou', 'limite'):
                it['role'] = 'verrou'
                role = 'verrou'

        elif role in STRICT and conf >= STRICT[role]:
            ok = True
            status = 'strict'
        elif role in RECALL and conf >= RECALL[role]:
            ok = True
            status = 'recall'
        elif role in {'limite','parametre','resultat','methode'} and vs >= STRICT_VERROU_DETECTOR and dtype not in CONTEXT_ONLY_TYPES:
            ok = True
            status = 'strict'
        elif role in {'limite','parametre','resultat','methode'} and vs >= RECALL_VERROU_DETECTOR and dtype not in CONTEXT_ONLY_TYPES:
            ok = True
            status = 'recall'

        if role == 'bruit':
            ok = False

        it['quality_status'] = status
        it['rank_score'] = rank_score(it)
        it['accepted_for_synthesis'] = safe_for_synthesis(it) if ok else False
        (kept if ok else rejected).append(it)

    return {'kept': kept, 'rejected': rejected, 'stats': {
        'input': len(items), 'kept': len(kept), 'rejected': len(rejected),
        'strict': sum(1 for x in kept if x['quality_status']=='strict'),
        'recall_only': sum(1 for x in kept if x['quality_status']=='recall'),
        'verrou_boosted': sum(1 for x in kept if x['quality_status']=='verrou_boosted'),
        'rejected_context_verrou': sum(1 for x in rejected if x.get('quality_status') == 'rejected_context_document_as_verrou'),
    }}


def thresholds() -> Dict[str, float]:
    return {**{f'strict_{k}':v for k,v in STRICT.items()}, **{f'recall_{k}':v for k,v in RECALL.items()}, 'strict_verrou_detector': STRICT_VERROU_DETECTOR, 'recall_verrou_detector': RECALL_VERROU_DETECTOR, 'verrou_boost_threshold': VERROU_SCORE_BOOST_THRESHOLD, 'max_other_role_conf_to_boost': MAX_OTHER_ROLE_CONFIDENCE_TO_BOOST, 'bruit': 0.99}
