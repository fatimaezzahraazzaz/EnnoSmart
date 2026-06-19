# -*- coding: utf-8 -*-
from __future__ import annotations
import re
from typing import Optional


def _norm(text: str) -> str:
    text = str(text or '').lower()
    tr = str.maketrans('àâäéèêëîïôöùûüç’', "aaaeeeeiioouuuc'")
    text = text.translate(tr)
    text = re.sub(r'[^a-z0-9]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def map_section_type(title: str, parent_type: Optional[str] = None) -> str:
    """Mappe un titre CIR vers un type métier. Les sous-sections ambiguës héritent du parent."""
    t = _norm(title)
    if not t:
        return parent_type or 'unknown'

    if any(k in t for k in ['annexe', 'appendix']):
        return 'annexe'
    if any(k in t for k in ['ressources humaines', 'chef s de projet', 'chef de projet', 'indicateurs de r d', 'indicateurs rd', 'rescrit cir']):
        return 'administratif'

    if any(k in t for k in ['verrou', 'incertitudes scientifiques', 'incertitudes techniques', 'incertitudes technologiques']):
        return 'verrous'
    if any(k in t for k in ['insuffisance', 'limites des solutions existantes', 'limites de l etat de l art', 'lacune']):
        return 'limites_etat_art'
    if any(k in t for k in ['etat de l art', 'state of the art', 'connaissances existantes', 'bibliograph', 'solutions existantes']):
        return 'etat_art'
    if any(k in t for k in ['objectif', 'performances a atteindre', 'performances visees', 'performances vises']):
        return 'objectifs'
    if 'contexte' in t:
        return 'contexte'
    if any(k in t for k in ['demarche experimentale', 'travaux r d', 'travaux rd', 'travaux realises', 'description des travaux', 'phasage', 'partenariat', 'rappel des travaux', 'rappel des premieres etudes']):
        return 'methodes_travaux'
    if any(k in t for k in ['resultats', 'resultat', 'releves', 'mesures', 'simulation', 'simulations', 'essais', 'analyse des temperatures', 'perte de charge']):
        # si le parent est méthode, les sous-sections "Résultats des simulations" sont des résultats
        return 'resultats'
    if any(k in t for k in ['conclusion et contribution', 'contribution scientifique', 'contribution technique', 'contribution technologique']):
        return 'contribution'
    if t in {'conclusion', 'conclusions'}:
        return parent_type or 'contribution'
    if any(k in t for k in ['intitule', 'fiche descriptive', 'nom de l operation', 'operation de r d', 'projet']):
        if parent_type in {'verrous','etat_art','limites_etat_art','methodes_travaux','resultats','contribution'}:
            return parent_type
        return 'project_title'

    # termes techniques fréquents dans les sous-sections de travaux
    if parent_type == 'methodes_travaux' and any(k in t for k in ['equilibrage', 'contrepoids', 'refroidissement', 'debit', 'temperature', 'chauffe', 'separateur', 'condensats', 'ecoulement', 'prototype', 'developpement']):
        return 'methodes_travaux'
    if parent_type == 'etat_art' and any(k in t for k in ['analyse', 'connaissances', 'caracterisation', 'vibration', 'acoustique', 'compresseur']):
        return 'etat_art'

    return parent_type or 'unknown'


def section_type_to_role(section_type: str) -> str:
    return {
        'project_title': 'project_title',
        'contexte': 'objectif',
        'objectifs': 'objectif',
        'etat_art': 'etat_art',
        'limites_etat_art': 'limite',
        'verrous': 'verrou',
        'methodes_travaux': 'methode',
        'resultats': 'resultat',
        'contribution': 'contribution',
        'annexe': 'annexe',
        'administratif': 'administratif',
    }.get(section_type, 'unknown')


def section_type_to_pack_key(section_type: str) -> Optional[str]:
    return {
        'contexte': 'objectifs_locaux',
        'objectifs': 'objectifs_locaux',
        'etat_art': 'etat_art_local',
        'limites_etat_art': 'limites_locales',
        'verrous': 'verrous_rnd_locaux',
        'methodes_travaux': 'methodes_locales',
        'resultats': 'resultats_locaux',
        'contribution': 'contributions_locales',
    }.get(section_type)


def section_label(section_type: str) -> str:
    return {
        'project_title': 'Intitulé / fiche projet',
        'contexte': 'Contexte du projet',
        'objectifs': 'Objectifs du projet',
        'etat_art': "État de l'art",
        'limites_etat_art': "Insuffisances de l'état de l'art",
        'verrous': 'Verrous et incertitudes R&D',
        'methodes_travaux': 'Démarche expérimentale / travaux R&D',
        'resultats': 'Résultats / essais / simulations',
        'contribution': 'Contribution scientifique, technique ou technologique',
        'annexe': 'Annexe',
        'administratif': 'Administratif / RH / indicateurs',
    }.get(section_type, 'Section CIR')

# Alias compatibles avec les anciennes versions
def map_title_to_role(title: str, parent_type: Optional[str] = None) -> str:
    return map_section_type(title, parent_type=parent_type)
