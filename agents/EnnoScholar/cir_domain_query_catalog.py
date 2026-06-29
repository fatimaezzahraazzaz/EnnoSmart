# -*- coding: utf-8 -*-
from __future__ import annotations

"""
cir_domain_query_catalog.py — EnnoScholar V130

Catalogue CIR multi-domaines généré depuis la nomenclature scientifique fournie.
Objectif :
- couvrir tous les domaines CIR niveau 1/2/3/4 présents dans l'Excel ;
- construire des requêtes scientifiques/domain-aware sans dépendre d'un seul projet ;
- fournir des sources techniques reconnues par profil de domaine ;
- rester générique : aucune règle liée à YLE, Pirmil ou un client précis.

Le catalogue contient 814 lignes de nomenclature.
"""

import re
import unicodedata
from typing import Any, Dict, List, Tuple


CIR_NOMENCLATURE_ROWS: List[Dict[str, str]] = [
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A1",
    "Sous-domaines niv2": "Automatique, Automatismes et productique, Robotique, Génie informatique",
    "code3": "A1a",
    "SECTION niv3": "Automatique",
    "code4": "A1a1",
    "Sous-sections niv4": "Commande et asservissement des systèmes complexes, analyse structurelle, théorie des graphes"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A1",
    "Sous-domaines niv2": "Automatique, Automatismes et productique, Robotique, Génie informatique",
    "code3": "A1a",
    "SECTION niv3": "Automatique",
    "code4": "A1a2",
    "Sous-sections niv4": "Systèmes temps réel, systèmes embarqués"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A1",
    "Sous-domaines niv2": "Automatique, Automatismes et productique, Robotique, Génie informatique",
    "code3": "A1a",
    "SECTION niv3": "Automatique",
    "code4": "A1a3",
    "Sous-sections niv4": "Diagnostic, contrôle non destructif, surveillance, maintenance, observation, placement de capteurs"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A1",
    "Sous-domaines niv2": "Automatique, Automatismes et productique, Robotique, Génie informatique",
    "code3": "A1a",
    "SECTION niv3": "Automatique",
    "code4": "A1a4",
    "Sous-sections niv4": "Automatique non linéaire"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A1",
    "Sous-domaines niv2": "Automatique, Automatismes et productique, Robotique, Génie informatique",
    "code3": "A1a",
    "SECTION niv3": "Automatique",
    "code4": "A1a5",
    "Sous-sections niv4": "Modélisation, identification et observation"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A1",
    "Sous-domaines niv2": "Automatique, Automatismes et productique, Robotique, Génie informatique",
    "code3": "A1a",
    "SECTION niv3": "Automatique",
    "code4": "A1a6",
    "Sous-sections niv4": "Commande prédictive, robustesse et optimisation"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A1",
    "Sous-domaines niv2": "Automatique, Automatismes et productique, Robotique, Génie informatique",
    "code3": "A1a",
    "SECTION niv3": "Automatique",
    "code4": "A1a7",
    "Sous-sections niv4": "Contrôle des systèmes hybrides, commutés et échantillonnés"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A1",
    "Sous-domaines niv2": "Automatique, Automatismes et productique, Robotique, Génie informatique",
    "code3": "A1a",
    "SECTION niv3": "Automatique",
    "code4": "A1a8",
    "Sous-sections niv4": "Recherche opérationnelle"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A1",
    "Sous-domaines niv2": "Automatique, Automatismes et productique, Robotique, Génie informatique",
    "code3": "A1a",
    "SECTION niv3": "Automatique",
    "code4": "A1a9",
    "Sous-sections niv4": "Réseaux de neurones"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A1",
    "Sous-domaines niv2": "Automatique, Automatismes et productique, Robotique, Génie informatique",
    "code3": "A1b",
    "SECTION niv3": "Automatismes, productique",
    "code4": "A1b1",
    "Sous-sections niv4": "Acquisition, traitement et identification des formes"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A1",
    "Sous-domaines niv2": "Automatique, Automatismes et productique, Robotique, Génie informatique",
    "code3": "A1b",
    "SECTION niv3": "Automatismes, productique",
    "code4": "A1b2",
    "Sous-sections niv4": "Modèles numériques pour la spécification et la maitrise des variations géométriques des produits"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A1",
    "Sous-domaines niv2": "Automatique, Automatismes et productique, Robotique, Génie informatique",
    "code3": "A1b",
    "SECTION niv3": "Automatismes, productique",
    "code4": "A1b3",
    "Sous-sections niv4": "Procédés de fabrication, performances et pilotage des systèmes polyarticulés"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A1",
    "Sous-domaines niv2": "Automatique, Automatismes et productique, Robotique, Génie informatique",
    "code3": "A1b",
    "SECTION niv3": "Automatismes, productique",
    "code4": "A1b4",
    "Sous-sections niv4": "Sûreté de fonctionnement des systèmes de production"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A1",
    "Sous-domaines niv2": "Automatique, Automatismes et productique, Robotique, Génie informatique",
    "code3": "A1c",
    "SECTION niv3": "Robotique",
    "code4": "A1c1",
    "Sous-sections niv4": "Microrobotique"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A1",
    "Sous-domaines niv2": "Automatique, Automatismes et productique, Robotique, Génie informatique",
    "code3": "A1c",
    "SECTION niv3": "Robotique",
    "code4": "A1c2",
    "Sous-sections niv4": "Productique"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A1",
    "Sous-domaines niv2": "Automatique, Automatismes et productique, Robotique, Génie informatique",
    "code3": "A1c",
    "SECTION niv3": "Robotique",
    "code4": "A1c3",
    "Sous-sections niv4": "Design et contrôle de robots manipulateurs"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A1",
    "Sous-domaines niv2": "Automatique, Automatismes et productique, Robotique, Génie informatique",
    "code3": "A1c",
    "SECTION niv3": "Robotique",
    "code4": "A1c4",
    "Sous-sections niv4": "Images et interaction en robotique"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A1",
    "Sous-domaines niv2": "Automatique, Automatismes et productique, Robotique, Génie informatique",
    "code3": "A1c",
    "SECTION niv3": "Robotique",
    "code4": "A1c5",
    "Sous-sections niv4": "Humanoïde, matérialisation robotique ou numérisation virtuelle de l’homme"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A1",
    "Sous-domaines niv2": "Automatique, Automatismes et productique, Robotique, Génie informatique",
    "code3": "A1d",
    "SECTION niv3": "Génie informatique",
    "code4": "A1d1",
    "Sous-sections niv4": "Systèmes d‘exploitation temps réel"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A1",
    "Sous-domaines niv2": "Automatique, Automatismes et productique, Robotique, Génie informatique",
    "code3": "A1d",
    "SECTION niv3": "Génie informatique",
    "code4": "A1d2",
    "Sous-sections niv4": "Architecture, réseaux"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A1",
    "Sous-domaines niv2": "Automatique, Automatismes et productique, Robotique, Génie informatique",
    "code3": "A1d",
    "SECTION niv3": "Génie informatique",
    "code4": "A1d3",
    "Sous-sections niv4": "Systèmes embarqués"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A1",
    "Sous-domaines niv2": "Automatique, Automatismes et productique, Robotique, Génie informatique",
    "code3": "A1d",
    "SECTION niv3": "Génie informatique",
    "code4": "A1d4",
    "Sous-sections niv4": "Objets communicants"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A1",
    "Sous-domaines niv2": "Automatique, Automatismes et productique, Robotique, Génie informatique",
    "code3": "A1d",
    "SECTION niv3": "Génie informatique",
    "code4": "A1d5",
    "Sous-sections niv4": "Security operations centers [SoC], centre d'opérations du réseau [NoC]"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A2",
    "Sous-domaines niv2": "Traitement du signal et de l'image",
    "code3": "A2a",
    "SECTION niv3": "Traitement du signal",
    "code4": "A2a1",
    "Sous-sections niv4": "Science des données et apprentissage statistique"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A2",
    "Sous-domaines niv2": "Traitement du signal et de l'image",
    "code3": "A2a",
    "SECTION niv3": "Traitement du signal",
    "code4": "A2a2",
    "Sous-sections niv4": "Fusion"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A2",
    "Sous-domaines niv2": "Traitement du signal et de l'image",
    "code3": "A2a",
    "SECTION niv3": "Traitement du signal",
    "code4": "A2a3",
    "Sous-sections niv4": "Classification"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A2",
    "Sous-domaines niv2": "Traitement du signal et de l'image",
    "code3": "A2a",
    "SECTION niv3": "Traitement du signal",
    "code4": "A2a4",
    "Sous-sections niv4": "Diagnostic"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A2",
    "Sous-domaines niv2": "Traitement du signal et de l'image",
    "code3": "A2a",
    "SECTION niv3": "Traitement du signal",
    "code4": "A2a5",
    "Sous-sections niv4": "Indexation"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A2",
    "Sous-domaines niv2": "Traitement du signal et de l'image",
    "code3": "A2a",
    "SECTION niv3": "Traitement du signal",
    "code4": "A2a6",
    "Sous-sections niv4": "Traitement de la parole"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A2",
    "Sous-domaines niv2": "Traitement du signal et de l'image",
    "code3": "A2a",
    "SECTION niv3": "Traitement du signal",
    "code4": "A2a7",
    "Sous-sections niv4": "Télécoms"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A2",
    "Sous-domaines niv2": "Traitement du signal et de l'image",
    "code3": "A2a",
    "SECTION niv3": "Traitement du signal",
    "code4": "A2a8",
    "Sous-sections niv4": "Indexage dans des bases de données"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A2",
    "Sous-domaines niv2": "Traitement du signal et de l'image",
    "code3": "A2a",
    "SECTION niv3": "Traitement du signal",
    "code4": "A2a9",
    "Sous-sections niv4": "Localisation, navigation [GPS, …]"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A2",
    "Sous-domaines niv2": "Traitement du signal et de l'image",
    "code3": "A2a",
    "SECTION niv3": "Traitement du signal",
    "code4": "A2a10",
    "Sous-sections niv4": "Télédétection"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A2",
    "Sous-domaines niv2": "Traitement du signal et de l'image",
    "code3": "A2a",
    "SECTION niv3": "Traitement du signal",
    "code4": "A2a11",
    "Sous-sections niv4": "Ultrasons"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A2",
    "Sous-domaines niv2": "Traitement du signal et de l'image",
    "code3": "A2a",
    "SECTION niv3": "Traitement du signal",
    "code4": "A2a12",
    "Sous-sections niv4": "Traitements d'antennes"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A2",
    "Sous-domaines niv2": "Traitement du signal et de l'image",
    "code3": "A2b",
    "SECTION niv3": "Traitement d'image",
    "code4": "A2b1",
    "Sous-sections niv4": "Problèmes inverses"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A2",
    "Sous-domaines niv2": "Traitement du signal et de l'image",
    "code3": "A2b",
    "SECTION niv3": "Traitement d'image",
    "code4": "A2b2",
    "Sous-sections niv4": "Deep learning pour le traitement d'image"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A2",
    "Sous-domaines niv2": "Traitement du signal et de l'image",
    "code3": "A2b",
    "SECTION niv3": "Traitement d'image",
    "code4": "A2b3",
    "Sous-sections niv4": "Optimisation de critères, réseaux de neurones artificiels"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A2",
    "Sous-domaines niv2": "Traitement du signal et de l'image",
    "code3": "A2b",
    "SECTION niv3": "Traitement d'image",
    "code4": "A2b4",
    "Sous-sections niv4": "Adéquation architecture / algorithme"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A2",
    "Sous-domaines niv2": "Traitement du signal et de l'image",
    "code3": "A2b",
    "SECTION niv3": "Traitement d'image",
    "code4": "A2b5",
    "Sous-sections niv4": "Reconnaissance de formes, classification, segmentation, fusion, reconstruction, stéréovision, flot optique, problèmes inverses"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A2",
    "Sous-domaines niv2": "Traitement du signal et de l'image",
    "code3": "A2b",
    "SECTION niv3": "Traitement d'image",
    "code4": "A2b6",
    "Sous-sections niv4": "Capteurs, instrumentation, télédétection et imagerie [couleur, X, ultrasons, IRM, médicale]"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3a",
    "SECTION niv3": "Systèmes d'information",
    "code4": "A3a1",
    "Sous-sections niv4": "Base de données  [BD], gestion des données, entrepôts, progiciels, masse de données, science des données, fouille de données"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3a",
    "SECTION niv3": "Systèmes d'information",
    "code4": "A3a2",
    "Sous-sections niv4": "Recherche d'information, ingénierie des documents, information multimédia"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3a",
    "SECTION niv3": "Systèmes d'information",
    "code4": "A3a3",
    "Sous-sections niv4": "Ingénierie des SI, méthodes et modèles pour la conception, process, SI collaboratifs et répartis, SI spécifiques"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3a",
    "SECTION niv3": "Systèmes d'information",
    "code4": "A3a4",
    "Sous-sections niv4": "Web, interopérabilité, web sémantique, ontologies, réseaux sociaux"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3a",
    "SECTION niv3": "Systèmes d'information",
    "code4": "A3a5",
    "Sous-sections niv4": "Service science, web service, services cloud"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3b",
    "SECTION niv3": "Algorithmique, recherche opérationnelle",
    "code4": "A3b1",
    "Sous-sections niv4": "Optimisation combinatoire"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3b",
    "SECTION niv3": "Algorithmique, recherche opérationnelle",
    "code4": "A3b2",
    "Sous-sections niv4": "Théorie des graphes"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3b",
    "SECTION niv3": "Algorithmique, recherche opérationnelle",
    "code4": "A3b3",
    "Sous-sections niv4": "Algorithmique distribuée, parallèle"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3b",
    "SECTION niv3": "Algorithmique, recherche opérationnelle",
    "code4": "A3b4",
    "Sous-sections niv4": "Calculabilité, complexité"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3b",
    "SECTION niv3": "Algorithmique, recherche opérationnelle",
    "code4": "A3b5",
    "Sous-sections niv4": "Théorie algorithmique des jeux"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3b",
    "SECTION niv3": "Algorithmique, recherche opérationnelle",
    "code4": "A3b6",
    "Sous-sections niv4": "Planification, ordonnancement"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3b",
    "SECTION niv3": "Algorithmique, recherche opérationnelle",
    "code4": "A3b7",
    "Sous-sections niv4": "Métaheuristique"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3c",
    "SECTION niv3": "Informatique fondamentale",
    "code4": "A3c1",
    "Sous-sections niv4": "Informatique théorique, langages formels, automates, modèles de calcul"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3c",
    "SECTION niv3": "Informatique fondamentale",
    "code4": "A3c2",
    "Sous-sections niv4": "Calcul formel, interface mathématiques et informatique, codes correcteurs"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3c",
    "SECTION niv3": "Informatique fondamentale",
    "code4": "A3c3",
    "Sous-sections niv4": "Logique, fondements de la programmation et des données, théorie de la preuve"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3c",
    "SECTION niv3": "Informatique fondamentale",
    "code4": "A3c4",
    "Sous-sections niv4": "Informatique quantique"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3d",
    "SECTION niv3": "Réseaux",
    "code4": "A3d1",
    "Sous-sections niv4": "Architecture, gestion, plateformes, métrologie"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3d",
    "SECTION niv3": "Réseaux",
    "code4": "A3d2",
    "Sous-sections niv4": "Réseaux sans fil, capteurs, internet des objets"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3d",
    "SECTION niv3": "Réseaux",
    "code4": "A3d3",
    "Sous-sections niv4": "Mobilité, réseaux véhiculaires"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3d",
    "SECTION niv3": "Réseaux",
    "code4": "A3d4",
    "Sous-sections niv4": "Cloud, virtualisation des réseaux"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3d",
    "SECTION niv3": "Réseaux",
    "code4": "A3d5",
    "Sous-sections niv4": "Modélisation, évaluation de performances, simulation"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3e",
    "SECTION niv3": "Bioinformatique",
    "code4": "A3e1",
    "Sous-sections niv4": "Inférence et analyse de séquences / réseaux"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3e",
    "SECTION niv3": "Bioinformatique",
    "code4": "A3e2",
    "Sous-sections niv4": "Stockage et fouille"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3e",
    "SECTION niv3": "Bioinformatique",
    "code4": "A3e3",
    "Sous-sections niv4": "Modélisation et simulation [molécules, dynamique des réseaux]"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3f",
    "SECTION niv3": "Systèmes informatiques",
    "code4": "A3f1",
    "Sous-sections niv4": "Systèmes d'exploitation, intergiciels, cloud"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3f",
    "SECTION niv3": "Systèmes informatiques",
    "code4": "A3f2",
    "Sous-sections niv4": "Modèles, spécifications, validation, vérification"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3f",
    "SECTION niv3": "Systèmes informatiques",
    "code4": "A3f3",
    "Sous-sections niv4": "Systèmes critiques, embarqués, temps réel"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3f",
    "SECTION niv3": "Systèmes informatiques",
    "code4": "A3f4",
    "Sous-sections niv4": "Systèmes répartis et distribués"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3g",
    "SECTION niv3": "Génie logiciel et programmation",
    "code4": "A3g1",
    "Sous-sections niv4": "Ingénierie des exigences, méthodes de développement, gestion des processus logiciels"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3g",
    "SECTION niv3": "Génie logiciel et programmation",
    "code4": "A3g2",
    "Sous-sections niv4": "Ingénierie pilotée par les modèles"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3g",
    "SECTION niv3": "Génie logiciel et programmation",
    "code4": "A3g3",
    "Sous-sections niv4": "Approches formelles, spécification, vérification, preuve, validation, test"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3g",
    "SECTION niv3": "Génie logiciel et programmation",
    "code4": "A3g4",
    "Sous-sections niv4": "Architecture logicielle, composants, lignes de produits, services"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3g",
    "SECTION niv3": "Génie logiciel et programmation",
    "code4": "A3g5",
    "Sous-sections niv4": "Méthodes de programmation et paradigmes"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3g",
    "SECTION niv3": "Génie logiciel et programmation",
    "code4": "A3g6",
    "Sous-sections niv4": "Langages, compilation, génération de code, interprétation"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3h",
    "SECTION niv3": "Intelligence artificielle",
    "code4": "A3h1",
    "Sous-sections niv4": "Apprentissage"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3h",
    "SECTION niv3": "Intelligence artificielle",
    "code4": "A3h2",
    "Sous-sections niv4": "Acquisition, représentation et ingénierie des connaissances, formalisation des raisonnements"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3h",
    "SECTION niv3": "Intelligence artificielle",
    "code4": "A3h3",
    "Sous-sections niv4": "Théorie de la décision, théorie du choix social"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3h",
    "SECTION niv3": "Intelligence artificielle",
    "code4": "A3h4",
    "Sous-sections niv4": "Traitement automatique des langues et de la parole"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3h",
    "SECTION niv3": "Intelligence artificielle",
    "code4": "A3h5",
    "Sous-sections niv4": "Contraintes et SAT"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3h",
    "SECTION niv3": "Intelligence artificielle",
    "code4": "A3h6",
    "Sous-sections niv4": "Intelligence artificielle distribuée, systèmes multiagents, modélisation cognitive"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3h",
    "SECTION niv3": "Intelligence artificielle",
    "code4": "A3h7",
    "Sous-sections niv4": "Science des données"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3i",
    "SECTION niv3": "Image, médias, géométrie, vision, perception, interaction",
    "code4": "A3i1",
    "Sous-sections niv4": "Traitement et analyse des images, signaux et médias [audio, images, séries d’images, documents, multimédia], imagerie computationnelle"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3i",
    "SECTION niv3": "Image, médias, géométrie, vision, perception, interaction",
    "code4": "A3i2",
    "Sous-sections niv4": "Vision et perception par ordinateur, apprentissage pour le multimédia"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3i",
    "SECTION niv3": "Image, médias, géométrie, vision, perception, interaction",
    "code4": "A3i3",
    "Sous-sections niv4": "Informatique graphique, informatique géométrique, synthèse de signaux, d'images et de contenu multimédia"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3i",
    "SECTION niv3": "Image, médias, géométrie, vision, perception, interaction",
    "code4": "A3i4",
    "Sous-sections niv4": "Réalité virtuelle augmentée et mixte, interaction 3D et multisensorielle"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3j",
    "SECTION niv3": "Communication et relation homme machine",
    "code4": "A3j1",
    "Sous-sections niv4": "Environnements informatiques pour l'apprentissage humain [EIAH]"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3j",
    "SECTION niv3": "Communication et relation homme machine",
    "code4": "A3j2",
    "Sous-sections niv4": "Communication homme machine, compagnons artificiels, affect, dialogue"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3j",
    "SECTION niv3": "Communication et relation homme machine",
    "code4": "A3j3",
    "Sous-sections niv4": "Analyse de documents"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3j",
    "SECTION niv3": "Communication et relation homme machine",
    "code4": "A3j4",
    "Sous-sections niv4": "Interaction homme machine [IHM], interface, multimodalité, multiutilisateurs"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3k",
    "SECTION niv3": "Architecture des machines",
    "code4": "A3k1",
    "Sous-sections niv4": "Architecture des ordinateurs, processeurs, multiprocesseurs, systèmes mémoire"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3k",
    "SECTION niv3": "Architecture des machines",
    "code4": "A3k2",
    "Sous-sections niv4": "Méthodes de conception, de vérification et de test de matériel"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3k",
    "SECTION niv3": "Architecture des machines",
    "code4": "A3k3",
    "Sous-sections niv4": "Architectures spécialisées, systèmes numériques intégrés sur puce, systèmes embarqués"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3l",
    "SECTION niv3": "Informatique industrielle",
    "code4": "A3l1",
    "Sous-sections niv4": "Architecture dédiée, architectures manycore, systèmes sur puces ou embarqués"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3l",
    "SECTION niv3": "Informatique industrielle",
    "code4": "A3l2",
    "Sous-sections niv4": "Systèmes temps réel, contrôle de processus, cybernétique, modèles pour les systèmes à événements discrets, automate programmable industriel, supervision"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3l",
    "SECTION niv3": "Informatique industrielle",
    "code4": "A3l3",
    "Sous-sections niv4": "Conception assistée par ordinateur, fabrication assistée par ordinateur, programmation de commande numérique, industrie 4.0"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3m",
    "SECTION niv3": "Modélisation, simulation pour les systèmes complexes",
    "code4": "A3m1",
    "Sous-sections niv4": "Formalismes de modélisation"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3m",
    "SECTION niv3": "Modélisation, simulation pour les systèmes complexes",
    "code4": "A3m2",
    "Sous-sections niv4": "Simulation distribuée"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3m",
    "SECTION niv3": "Modélisation, simulation pour les systèmes complexes",
    "code4": "A3m3",
    "Sous-sections niv4": "Vérification, validation de modèles de simulation"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "A3",
    "code3": "A3m",
    "SECTION niv3": "Modélisation, simulation pour les systèmes complexes",
    "code4": "A3m4",
    "Sous-sections niv4": "Transformations de modèles, génération de code à partir des modèles"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3m",
    "SECTION niv3": "Modélisation, simulation pour les systèmes complexes",
    "code4": "A3m5",
    "Sous-sections niv4": "Couplages de modèles, interactions entre systèmes discrets"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3n",
    "SECTION niv3": "Sécurité",
    "code4": "A3n1",
    "Sous-sections niv4": "Codage et cryptographie"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3n",
    "SECTION niv3": "Sécurité",
    "code4": "A3n2",
    "Sous-sections niv4": "Méthodes formelles pour la sécurité"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3n",
    "SECTION niv3": "Sécurité",
    "code4": "A3n3",
    "Sous-sections niv4": "Protection de la vie privée"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3n",
    "SECTION niv3": "Sécurité",
    "code4": "A3n4",
    "Sous-sections niv4": "Sécurité des systèmes, des logiciels, des réseaux"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3n",
    "SECTION niv3": "Sécurité",
    "code4": "A3n5",
    "Sous-sections niv4": "Sécurité des systèmes physiques, matériels"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A3",
    "Sous-domaines niv2": "Informatique",
    "code3": "A3n",
    "SECTION niv3": "Sécurité",
    "code4": "A3n6",
    "Sous-sections niv4": "Sécurité des systèmes d’information"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A4",
    "Sous-domaines niv2": "Mathématiques",
    "code3": "A4a",
    "SECTION niv3": "Mathématiques fondamentales",
    "code4": "A4a1",
    "Sous-sections niv4": "Algèbre"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A4",
    "Sous-domaines niv2": "Mathématiques",
    "code3": "A4a",
    "SECTION niv3": "Mathématiques fondamentales",
    "code4": "A4a2",
    "Sous-sections niv4": "Théorie des ensembles"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A4",
    "Sous-domaines niv2": "Mathématiques",
    "code3": "A4a",
    "SECTION niv3": "Mathématiques fondamentales",
    "code4": "A4a3",
    "Sous-sections niv4": "Théorie des nombres"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A4",
    "Sous-domaines niv2": "Mathématiques",
    "code3": "A4a",
    "SECTION niv3": "Mathématiques fondamentales",
    "code4": "A4a4",
    "Sous-sections niv4": "Analyse"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A4",
    "Sous-domaines niv2": "Mathématiques",
    "code3": "A4a",
    "SECTION niv3": "Mathématiques fondamentales",
    "code4": "A4a5",
    "Sous-sections niv4": "Topologie"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A4",
    "Sous-domaines niv2": "Mathématiques",
    "code3": "A4a",
    "SECTION niv3": "Mathématiques fondamentales",
    "code4": "A4a6",
    "Sous-sections niv4": "Géométrie"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A4",
    "Sous-domaines niv2": "Mathématiques",
    "code3": "A4a",
    "SECTION niv3": "Mathématiques fondamentales",
    "code4": "A4a7",
    "Sous-sections niv4": "Probabilités"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A4",
    "Sous-domaines niv2": "Mathématiques",
    "code3": "A4a",
    "SECTION niv3": "Mathématiques fondamentales",
    "code4": "A4a8",
    "Sous-sections niv4": "Statistique"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A4",
    "Sous-domaines niv2": "Mathématiques",
    "code3": "A4b",
    "SECTION niv3": "Mathématiques appliquées",
    "code4": "A4b1",
    "Sous-sections niv4": "Calcul scientifique"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A4",
    "Sous-domaines niv2": "Mathématiques",
    "code3": "A4b",
    "SECTION niv3": "Mathématiques appliquées",
    "code4": "A4b2",
    "Sous-sections niv4": "Analyse numérique"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A4",
    "Sous-domaines niv2": "Mathématiques",
    "code3": "A4b",
    "SECTION niv3": "Mathématiques appliquées",
    "code4": "A4b3",
    "Sous-sections niv4": "Mathématiques de l'ingénierie"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A4",
    "Sous-domaines niv2": "Mathématiques",
    "code3": "A4b",
    "SECTION niv3": "Mathématiques appliquées",
    "code4": "A4b4",
    "Sous-sections niv4": "Programmation numérique"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A4",
    "Sous-domaines niv2": "Mathématiques",
    "code3": "A4b",
    "SECTION niv3": "Mathématiques appliquées",
    "code4": "A4b5",
    "Sous-sections niv4": "Méthodes d'optimisation"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A4",
    "Sous-domaines niv2": "Mathématiques",
    "code3": "A4b",
    "SECTION niv3": "Mathématiques appliquées",
    "code4": "A4b6",
    "Sous-sections niv4": "Recherche opérationnelle"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A4",
    "Sous-domaines niv2": "Mathématiques",
    "code3": "A4b",
    "SECTION niv3": "Mathématiques appliquées",
    "code4": "A4b7",
    "Sous-sections niv4": "Biomathématiques"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A4",
    "Sous-domaines niv2": "Mathématiques",
    "code3": "A4b",
    "SECTION niv3": "Mathématiques appliquées",
    "code4": "A4b8",
    "Sous-sections niv4": "Théorie de l'information"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A4",
    "Sous-domaines niv2": "Mathématiques",
    "code3": "A4b",
    "SECTION niv3": "Mathématiques appliquées",
    "code4": "A4b9",
    "Sous-sections niv4": "Théorie des jeux"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A4",
    "Sous-domaines niv2": "Mathématiques",
    "code3": "A4b",
    "SECTION niv3": "Mathématiques appliquées",
    "code4": "A4b10",
    "Sous-sections niv4": "Probabilité et statistiques, apprentissage statistique"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A4",
    "Sous-domaines niv2": "Mathématiques",
    "code3": "A4b",
    "SECTION niv3": "Mathématiques appliquées",
    "code4": "A4b11",
    "Sous-sections niv4": "Mathématiques financières, actuariat"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A4",
    "Sous-domaines niv2": "Mathématiques",
    "code3": "A4b",
    "SECTION niv3": "Mathématiques appliquées",
    "code4": "A4b12",
    "Sous-sections niv4": "Cryptographie"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A4",
    "Sous-domaines niv2": "Mathématiques",
    "code3": "A4b",
    "SECTION niv3": "Mathématiques appliquées",
    "code4": "A4b13",
    "Sous-sections niv4": "Combinatoire"
  },
  {
    "code1": "A",
    "DOMAINES niv1": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE, MATHEMATIQUES",
    "code2": "A4",
    "Sous-domaines niv2": "Mathématiques",
    "code3": "A4b",
    "SECTION niv3": "Mathématiques appliquées",
    "code4": "A4b14",
    "Sous-sections niv4": "Théorie des graphes"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B1",
    "Sous-domaines niv2": "Électronique, Télécommunications et réseaux",
    "code3": "B1a",
    "SECTION niv3": "Composants et circuits électroniques",
    "code4": "B1a1",
    "Sous-sections niv4": "Composants semiconducteurs, transistors, triac, diodes, thyristors, [MOS]  [CMOS]  [IGBT]  [SiC]  [GaN]"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B1",
    "Sous-domaines niv2": "Électronique, Télécommunications et réseaux",
    "code3": "B1a",
    "SECTION niv3": "Composants et circuits électroniques",
    "code4": "B1a2",
    "Sous-sections niv4": "CAO électronique, modélisation, optimisation"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B1",
    "Sous-domaines niv2": "Électronique, Télécommunications et réseaux",
    "code3": "B1a",
    "SECTION niv3": "Composants et circuits électroniques",
    "code4": "B1a3",
    "Sous-sections niv4": "Electronique, circuits et systèmes, électronique embarquée, microsystème, nanosystèmes, communication [RFID], [NFC]"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B1",
    "Sous-domaines niv2": "Électronique, Télécommunications et réseaux",
    "code3": "B1a",
    "SECTION niv3": "Composants et circuits électroniques",
    "code4": "B1a4",
    "Sous-sections niv4": "Bioélectronique, électrophysiologie, bioingénierie"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B1",
    "Sous-domaines niv2": "Électronique, Télécommunications et réseaux",
    "code3": "B1a",
    "SECTION niv3": "Composants et circuits électroniques",
    "code4": "B1a5",
    "Sous-sections niv4": "Optoélectronique, composant électroluminescent [LED]"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B1",
    "Sous-domaines niv2": "Électronique, Télécommunications et réseaux",
    "code3": "B1a",
    "SECTION niv3": "Composants et circuits électroniques",
    "code4": "B1a6",
    "Sous-sections niv4": "Electronique organique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B1",
    "Sous-domaines niv2": "Électronique, Télécommunications et réseaux",
    "code3": "B1a",
    "SECTION niv3": "Composants et circuits électroniques",
    "code4": "B1a7",
    "Sous-sections niv4": "Instrumentations, capteurs, biocapteurs"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B1",
    "Sous-domaines niv2": "Électronique, Télécommunications et réseaux",
    "code3": "B1a",
    "SECTION niv3": "Composants et circuits électroniques",
    "code4": "B1a8",
    "Sous-sections niv4": "Photonique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B1",
    "Sous-domaines niv2": "Électronique, Télécommunications et réseaux",
    "code3": "B1a",
    "SECTION niv3": "Composants et circuits électroniques",
    "code4": "B1a9",
    "Sous-sections niv4": "Dispositifs médicaux, eSanté"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B1",
    "Sous-domaines niv2": "Électronique, Télécommunications et réseaux",
    "code3": "B1a",
    "SECTION niv3": "Composants et circuits électroniques",
    "code4": "B1a10",
    "Sous-sections niv4": "Métrologie, mesures physiques"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B1",
    "Sous-domaines niv2": "Électronique, Télécommunications et réseaux",
    "code3": "B1b",
    "SECTION niv3": "Electronique numérique",
    "code4": "B1b1",
    "Sous-sections niv4": "Circuits numériques et convertisseurs"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B1",
    "Sous-domaines niv2": "Électronique, Télécommunications et réseaux",
    "code3": "B1b",
    "SECTION niv3": "Electronique numérique",
    "code4": "B1b2",
    "Sous-sections niv4": "Langages et algorithmes"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B1",
    "Sous-domaines niv2": "Électronique, Télécommunications et réseaux",
    "code3": "B1b",
    "SECTION niv3": "Electronique numérique",
    "code4": "B1b3",
    "Sous-sections niv4": "Processeurs"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B1",
    "Sous-domaines niv2": "Électronique, Télécommunications et réseaux",
    "code3": "B1b",
    "SECTION niv3": "Electronique numérique",
    "code4": "B1b4",
    "Sous-sections niv4": "Echantillonnage"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B1",
    "Sous-domaines niv2": "Électronique, Télécommunications et réseaux",
    "code3": "B1b",
    "SECTION niv3": "Electronique numérique",
    "code4": "B1b5",
    "Sous-sections niv4": "Architecture des circuits"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B1",
    "Sous-domaines niv2": "Électronique, Télécommunications et réseaux",
    "code3": "B1b",
    "SECTION niv3": "Electronique numérique",
    "code4": "B1b6",
    "Sous-sections niv4": "Multiplexage, bus CAN"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B1",
    "Sous-domaines niv2": "Électronique, Télécommunications et réseaux",
    "code3": "B1b",
    "SECTION niv3": "Electronique numérique",
    "code4": "B1b7",
    "Sous-sections niv4": "Calculateurs embarqués"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B1",
    "Sous-domaines niv2": "Électronique, Télécommunications et réseaux",
    "code3": "B1b",
    "SECTION niv3": "Electronique numérique",
    "code4": "B1b8",
    "Sous-sections niv4": "Circuit logique programmable, FPGA, CPLD, microprocesseur, microcontrôleur"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B1",
    "Sous-domaines niv2": "Électronique, Télécommunications et réseaux",
    "code3": "B1c",
    "SECTION niv3": "Microélectronique",
    "code4": "B1c1",
    "Sous-sections niv4": "Microdispositifs"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B1",
    "Sous-domaines niv2": "Électronique, Télécommunications et réseaux",
    "code3": "B1c",
    "SECTION niv3": "Microélectronique",
    "code4": "B1c2",
    "Sous-sections niv4": "Biopuce, laboratoire sur puce"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B1",
    "Sous-domaines niv2": "Électronique, Télécommunications et réseaux",
    "code3": "B1c",
    "SECTION niv3": "Microélectronique",
    "code4": "B1c3",
    "Sous-sections niv4": "Architecture matérielles et logicielles"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B1",
    "Sous-domaines niv2": "Électronique, Télécommunications et réseaux",
    "code3": "B1c",
    "SECTION niv3": "Microélectronique",
    "code4": "B1c4",
    "Sous-sections niv4": "Technologies mémoires électroniques"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B1",
    "Sous-domaines niv2": "Électronique, Télécommunications et réseaux",
    "code3": "B1c",
    "SECTION niv3": "Microélectronique",
    "code4": "B1c5",
    "Sous-sections niv4": "Réseaux de communication intégrés, réseau sur puce [NoC]"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B1",
    "Sous-domaines niv2": "Électronique, Télécommunications et réseaux",
    "code3": "B1c",
    "SECTION niv3": "Microélectronique",
    "code4": "B1c6",
    "Sous-sections niv4": "Sécurité des systèmes et test des circuits"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B1",
    "Sous-domaines niv2": "Électronique, Télécommunications et réseaux",
    "code3": "B1c",
    "SECTION niv3": "Microélectronique",
    "code4": "B1c7",
    "Sous-sections niv4": "Microactionnement [MEMS]"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B1",
    "Sous-domaines niv2": "Électronique, Télécommunications et réseaux",
    "code3": "B1d",
    "SECTION niv3": "Nanoélectronique",
    "code4": "B1d1",
    "Sous-sections niv4": "Etude des nanomatériaux"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B1",
    "Sous-domaines niv2": "Électronique, Télécommunications et réseaux",
    "code3": "B1d",
    "SECTION niv3": "Nanoélectronique",
    "code4": "B1d2",
    "Sous-sections niv4": "Procédés de nanofabrication"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B1",
    "Sous-domaines niv2": "Électronique, Télécommunications et réseaux",
    "code3": "B1d",
    "SECTION niv3": "Nanoélectronique",
    "code4": "B1d3",
    "Sous-sections niv4": "Caractérisation nanométrique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B1",
    "Sous-domaines niv2": "Électronique, Télécommunications et réseaux",
    "code3": "B1d",
    "SECTION niv3": "Nanoélectronique",
    "code4": "B1d4",
    "Sous-sections niv4": "Nanodispositifs"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B1",
    "Sous-domaines niv2": "Électronique, Télécommunications et réseaux",
    "code3": "B1d",
    "SECTION niv3": "Nanoélectronique",
    "code4": "B1d5",
    "Sous-sections niv4": "Spintronique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B1",
    "Sous-domaines niv2": "Électronique, Télécommunications et réseaux",
    "code3": "B1e",
    "SECTION niv3": "Télécommunications et réseaux",
    "code4": "B1e1",
    "Sous-sections niv4": "Systèmes de télécommunications et réseaux"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B1",
    "Sous-domaines niv2": "Électronique, Télécommunications et réseaux",
    "code3": "B1e",
    "SECTION niv3": "Télécommunications et réseaux",
    "code4": "B1e2",
    "Sous-sections niv4": "Codage, compression et protection de l'information, cryptographie"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B1",
    "Sous-domaines niv2": "Électronique, Télécommunications et réseaux",
    "code3": "B1e",
    "SECTION niv3": "Télécommunications et réseaux",
    "code4": "B1e3",
    "Sous-sections niv4": "Communications numériques"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B1",
    "Sous-domaines niv2": "Électronique, Télécommunications et réseaux",
    "code3": "B1e",
    "SECTION niv3": "Télécommunications et réseaux",
    "code4": "B1e4",
    "Sous-sections niv4": "Objets communicants, internet des objets [IoT]  [IdO], téléphonie, applications mobiles"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B1",
    "Sous-domaines niv2": "Électronique, Télécommunications et réseaux",
    "code3": "B1e",
    "SECTION niv3": "Télécommunications et réseaux",
    "code4": "B1e5",
    "Sous-sections niv4": "Electromagnétisme, microondes, antennes"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2a",
    "SECTION niv3": "Matériaux",
    "code4": "B2a1",
    "Sous-sections niv4": "Matériaux magnétiques, ferromagnétiques, aimants, ferrites, terres rares"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2a",
    "SECTION niv3": "Matériaux",
    "code4": "B2a2",
    "Sous-sections niv4": "Matériaux diélectriques, matériaux ferroélectriques, isolants électriques"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2a",
    "SECTION niv3": "Matériaux",
    "code4": "B2a3",
    "Sous-sections niv4": "Supraconducteurs"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2a",
    "SECTION niv3": "Matériaux",
    "code4": "B2a4",
    "Sous-sections niv4": "Matériaux piézoélectriques"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2a",
    "SECTION niv3": "Matériaux",
    "code4": "B2a5",
    "Sous-sections niv4": "Méthodes de caractérisation"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2a",
    "SECTION niv3": "Matériaux",
    "code4": "B2a6",
    "Sous-sections niv4": "Modélisation et lois de comportement"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2b",
    "SECTION niv3": "Composants",
    "code4": "B2b1",
    "Sous-sections niv4": "Composants magnétiques, aimants, électroaimants, bobines magnétiques"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2b",
    "SECTION niv3": "Composants",
    "code4": "B2b2",
    "Sous-sections niv4": "Composants capacitifs, condensateurs, supercondensateurs"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2b",
    "SECTION niv3": "Composants",
    "code4": "B2b3",
    "Sous-sections niv4": "Transformateurs électriques"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2b",
    "SECTION niv3": "Composants",
    "code4": "B2b4",
    "Sous-sections niv4": "Composants piézoélectriques"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2b",
    "SECTION niv3": "Composants",
    "code4": "B2b5",
    "Sous-sections niv4": "Piles à combustibles [PAC], biopile"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2b",
    "SECTION niv3": "Composants",
    "code4": "B2b6",
    "Sous-sections niv4": "Batteries électrochimiques"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2b",
    "SECTION niv3": "Composants",
    "code4": "B2b7",
    "Sous-sections niv4": "Cellule photovoltaïque, panneau solaire"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2c",
    "SECTION niv3": "Production d'électricité, réseaux électriques",
    "code4": "B2c1",
    "Sous-sections niv4": "Centrales électriques"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2c",
    "SECTION niv3": "Production d'électricité, réseaux électriques",
    "code4": "B2c2",
    "Sous-sections niv4": "Réseaux électriques, filtrage des harmoniques, pollution harmonique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2c",
    "SECTION niv3": "Production d'électricité, réseaux électriques",
    "code4": "B2c3",
    "Sous-sections niv4": "Energie éolienne, énergie hydrolienne"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2c",
    "SECTION niv3": "Production d'électricité, réseaux électriques",
    "code4": "B2c4",
    "Sous-sections niv4": "Energie solaire"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2c",
    "SECTION niv3": "Production d'électricité, réseaux électriques",
    "code4": "B2c5",
    "Sous-sections niv4": "Maîtrise des énergies renouvelables,  gestion d’énergie, microréseau, microgrid"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2c",
    "SECTION niv3": "Production d'électricité, réseaux électriques",
    "code4": "B2c6",
    "Sous-sections niv4": "Smartgrid, contrôle hiérarchique, supervision, prédiction, réseau bâtiment, recharge des véhicules électriques"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2c",
    "SECTION niv3": "Production d'électricité, réseaux électriques",
    "code4": "B2c7",
    "Sous-sections niv4": "Optimisation de la production des énergies, simulation de la foudre, composants haute tension [HT], isolateurs [HT]"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2c",
    "SECTION niv3": "Production d'électricité, réseaux électriques",
    "code4": "B2c8",
    "Sous-sections niv4": "Stockage de l'énergie, récupération de l'énergie"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2c",
    "SECTION niv3": "Production d'électricité, réseaux électriques",
    "code4": "B2c9",
    "Sous-sections niv4": "Transmission de l'énergie, câbles et lignes électriques, filtrage des harmoniques réseaux [FACTS]"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2d",
    "SECTION niv3": "Electronique de puissance",
    "code4": "B2d1",
    "Sous-sections niv4": "Composants semiconducteurs de puissance, composants à grand gap,  [MOS]  [CMOS]  [IGBT]  [SiC] [GaN]"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2d",
    "SECTION niv3": "Electronique de puissance",
    "code4": "B2d2",
    "Sous-sections niv4": "Architectures d'électronique de puissance, onduleurs, hacheurs, alimentations à découpage, convertisseurs de puissance"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2d",
    "SECTION niv3": "Electronique de puissance",
    "code4": "B2d3",
    "Sous-sections niv4": "Convertisseurs d'électroniques de puissance"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2d",
    "SECTION niv3": "Electronique de puissance",
    "code4": "B2d4",
    "Sous-sections niv4": "Compatibilité électromagnétique [CEM], filtrage haute fréquence  [HF], modélisation haute fréquence  [HF]"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2d",
    "SECTION niv3": "Electronique de puissance",
    "code4": "B2d5",
    "Sous-sections niv4": "Intégration de composants de puissance"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2d",
    "SECTION niv3": "Electronique de puissance",
    "code4": "B2d6",
    "Sous-sections niv4": "Sûreté de fonctionnement en électronique de puissance, durée de vie, power cycling"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2d",
    "SECTION niv3": "Electronique de puissance",
    "code4": "B2d7",
    "Sous-sections niv4": "Chargeur de batterie, alimentation de puissance"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2e",
    "SECTION niv3": "Electromécanique",
    "code4": "B2e1",
    "Sous-sections niv4": "Conception et fabrication des machines électriques, moteur, alternateur"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2e",
    "SECTION niv3": "Electromécanique",
    "code4": "B2e2",
    "Sous-sections niv4": "Contrôles des machines électriques"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2e",
    "SECTION niv3": "Electromécanique",
    "code4": "B2e3",
    "Sous-sections niv4": "Vibration et bruit acoustique des machines électriques, compensation active"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2e",
    "SECTION niv3": "Electromécanique",
    "code4": "B2e4",
    "Sous-sections niv4": "Sûreté de fonctionnement des machines électriques, diagnostic, suivi de santé, health monitoring"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2e",
    "SECTION niv3": "Electromécanique",
    "code4": "B2e5",
    "Sous-sections niv4": "Machines supraconductrices"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2e",
    "SECTION niv3": "Electromécanique",
    "code4": "B2e6",
    "Sous-sections niv4": "Défaut et panne des machines électriques, isolation électrique, bobinage des machines électriques"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2f",
    "SECTION niv3": "Contrôle des composants et des systèmes électriques",
    "code4": "B2f1",
    "Sous-sections niv4": "Asservissements et automatisation électrique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2f",
    "SECTION niv3": "Contrôle des composants et des systèmes électriques",
    "code4": "B2f2",
    "Sous-sections niv4": "Supervision et gestion optimale"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2f",
    "SECTION niv3": "Contrôle des composants et des systèmes électriques",
    "code4": "B2f3",
    "Sous-sections niv4": "Modèles et méthodes pour l'automatique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2f",
    "SECTION niv3": "Contrôle des composants et des systèmes électriques",
    "code4": "B2f4",
    "Sous-sections niv4": "Diagnostic et sûreté de fonctionnement des composants et des systèmes électriques, tolérance aux défauts, fonctionnement en mode dégradé"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2f",
    "SECTION niv3": "Contrôle des composants et des systèmes électriques",
    "code4": "B2f5",
    "Sous-sections niv4": "Contrôle des batteries et des piles à combustibles [PAC]"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2f",
    "SECTION niv3": "Contrôle des composants et des systèmes électriques",
    "code4": "B2f6",
    "Sous-sections niv4": "Diagnostic des batteries et des piles à combustibles [PAC]"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2f",
    "SECTION niv3": "Contrôle des composants et des systèmes électriques",
    "code4": "B2f7",
    "Sous-sections niv4": "Aide à la conduite [ADAS]"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2f",
    "SECTION niv3": "Contrôle des composants et des systèmes électriques",
    "code4": "B2f8",
    "Sous-sections niv4": "Mécatronique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2f",
    "SECTION niv3": "Contrôle des composants et des systèmes électriques",
    "code4": "B2f9",
    "Sous-sections niv4": "Moteur pas à pas, machine asynchrone, machine synchrone, machine à courant continu [DC machine], machine à réluctance variable [SRM], machine synchrone à aimants [MSAP]"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2g",
    "SECTION niv3": "Systèmes et domaines applicatifs",
    "code4": "B2g1",
    "Sous-sections niv4": "Génération et transport de l'énergie électrique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2g",
    "SECTION niv3": "Systèmes et domaines applicatifs",
    "code4": "B2g2",
    "Sous-sections niv4": "Electrification des moyens de transport routiers, Groupe moto propulseur  [GMP] hybride, frein électrique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2g",
    "SECTION niv3": "Systèmes et domaines applicatifs",
    "code4": "B2g3",
    "Sous-sections niv4": "Electrification des moyens de transport aériens"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2g",
    "SECTION niv3": "Systèmes et domaines applicatifs",
    "code4": "B2g4",
    "Sous-sections niv4": "Electrification des moyens de transport ferroviaires"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2g",
    "SECTION niv3": "Systèmes et domaines applicatifs",
    "code4": "B2g5",
    "Sous-sections niv4": "Electrification des moyens de transport maritimes"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2g",
    "SECTION niv3": "Systèmes et domaines applicatifs",
    "code4": "B2g6",
    "Sous-sections niv4": "Véhicule connecté, véhicule autonome"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2g",
    "SECTION niv3": "Systèmes et domaines applicatifs",
    "code4": "B2g7",
    "Sous-sections niv4": "Installation électrique, protection électrique, fusible, sectionneur, disjoncteur, arc électrique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2g",
    "SECTION niv3": "Systèmes et domaines applicatifs",
    "code4": "B2g8",
    "Sous-sections niv4": "Dispositif médical"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B2",
    "Sous-domaines niv2": "Génie électrique",
    "code3": "B2g",
    "SECTION niv3": "Systèmes et domaines applicatifs",
    "code4": "B2g9",
    "Sous-sections niv4": "Système d'éclairage, lampes"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B3",
    "Sous-domaines niv2": "Matériaux, Métallurgie",
    "code3": "B3a",
    "SECTION niv3": "Matériaux solides inorganiques",
    "code4": "B3a1",
    "Sous-sections niv4": "Synthèse, mise en forme"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B3",
    "Sous-domaines niv2": "Matériaux, Métallurgie",
    "code3": "B3a",
    "SECTION niv3": "Matériaux solides inorganiques",
    "code4": "B3a2",
    "Sous-sections niv4": "Vieillissement, corrosion et recyclage des matériaux"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B3",
    "Sous-domaines niv2": "Matériaux, Métallurgie",
    "code3": "B3a",
    "SECTION niv3": "Matériaux solides inorganiques",
    "code4": "B3a3",
    "Sous-sections niv4": "Caractérisation"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B3",
    "Sous-domaines niv2": "Matériaux, Métallurgie",
    "code3": "B3a",
    "SECTION niv3": "Matériaux solides inorganiques",
    "code4": "B3a4",
    "Sous-sections niv4": "Modélisation multiéchelles"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B3",
    "Sous-domaines niv2": "Matériaux, Métallurgie",
    "code3": "B3a",
    "SECTION niv3": "Matériaux solides inorganiques",
    "code4": "B3a5",
    "Sous-sections niv4": "Matière plastique, plasturgie, matériaux isolants"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B3",
    "Sous-domaines niv2": "Matériaux, Métallurgie",
    "code3": "B3a",
    "SECTION niv3": "Matériaux solides inorganiques",
    "code4": "B3a6",
    "Sous-sections niv4": "Nanomatériaux, procédés de nanofabrication, caractérisation nanométrique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B3",
    "Sous-domaines niv2": "Matériaux, Métallurgie",
    "code3": "B3b",
    "SECTION niv3": "Matériaux céramiques, verres",
    "code4": "B3b1",
    "Sous-sections niv4": "Synthèse, mise en forme, traitement"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B3",
    "Sous-domaines niv2": "Matériaux, Métallurgie",
    "code3": "B3b",
    "SECTION niv3": "Matériaux céramiques, verres",
    "code4": "B3b2",
    "Sous-sections niv4": "Vieillissement, corrosion et recyclage des matériaux"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B3",
    "Sous-domaines niv2": "Matériaux, Métallurgie",
    "code3": "B3b",
    "SECTION niv3": "Matériaux céramiques, verres",
    "code4": "B3b3",
    "Sous-sections niv4": "Caractérisation"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B3",
    "Sous-domaines niv2": "Matériaux, Métallurgie",
    "code3": "B3b",
    "SECTION niv3": "Matériaux céramiques, verres",
    "code4": "B3b4",
    "Sous-sections niv4": "Modélisation multiéchelles"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B3",
    "Sous-domaines niv2": "Matériaux, Métallurgie",
    "code3": "B3c",
    "SECTION niv3": "Matériaux métalliques, métaux, alliages",
    "code4": "B3c1",
    "Sous-sections niv4": "Synthèse, mise en forme"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B3",
    "Sous-domaines niv2": "Matériaux, Métallurgie",
    "code3": "B3c",
    "SECTION niv3": "Matériaux métalliques, métaux, alliages",
    "code4": "B3c2",
    "Sous-sections niv4": "Vieillissement, corrosion et recyclage des matériaux"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B3",
    "Sous-domaines niv2": "Matériaux, Métallurgie",
    "code3": "B3c",
    "SECTION niv3": "Matériaux métalliques, métaux, alliages",
    "code4": "B3c3",
    "Sous-sections niv4": "Caractérisation"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B3",
    "Sous-domaines niv2": "Matériaux, Métallurgie",
    "code3": "B3c",
    "SECTION niv3": "Matériaux métalliques, métaux, alliages",
    "code4": "B3c4",
    "Sous-sections niv4": "Modélisation multiéchelles"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B3",
    "Sous-domaines niv2": "Matériaux, Métallurgie",
    "code3": "B3d",
    "SECTION niv3": "Matériaux polymères",
    "code4": "B3d1",
    "Sous-sections niv4": "Synthèse, mise en forme"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B3",
    "Sous-domaines niv2": "Matériaux, Métallurgie",
    "code3": "B3d",
    "SECTION niv3": "Matériaux polymères",
    "code4": "B3d2",
    "Sous-sections niv4": "Vieillissement, corrosion et recyclage des matériaux"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B3",
    "Sous-domaines niv2": "Matériaux, Métallurgie",
    "code3": "B3d",
    "SECTION niv3": "Matériaux polymères",
    "code4": "B3d3",
    "Sous-sections niv4": "Caractérisation"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B3",
    "Sous-domaines niv2": "Matériaux, Métallurgie",
    "code3": "B3d",
    "SECTION niv3": "Matériaux polymères",
    "code4": "B3d4",
    "Sous-sections niv4": "Modélisation multiéchelles"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B3",
    "Sous-domaines niv2": "Matériaux, Métallurgie",
    "code3": "B3e",
    "SECTION niv3": "Matériaux composites",
    "code4": "B3e1",
    "Sous-sections niv4": "Synthèse, mise en forme"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B3",
    "Sous-domaines niv2": "Matériaux, Métallurgie",
    "code3": "B3e",
    "SECTION niv3": "Matériaux composites",
    "code4": "B3e2",
    "Sous-sections niv4": "Vieillissement, corrosion et recyclage des matériaux"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B3",
    "Sous-domaines niv2": "Matériaux, Métallurgie",
    "code3": "B3e",
    "SECTION niv3": "Matériaux composites",
    "code4": "B3e3",
    "Sous-sections niv4": "Caractérisation"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B3",
    "Sous-domaines niv2": "Matériaux, Métallurgie",
    "code3": "B3e",
    "SECTION niv3": "Matériaux composites",
    "code4": "B3e4",
    "Sous-sections niv4": "Modélisation multiéchelles"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B3",
    "Sous-domaines niv2": "Matériaux, Métallurgie",
    "code3": "B3f",
    "SECTION niv3": "Matériaux hybrides",
    "code4": "B3f1",
    "Sous-sections niv4": "Synthèse, mise en forme"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B3",
    "Sous-domaines niv2": "Matériaux, Métallurgie",
    "code3": "B3f",
    "SECTION niv3": "Matériaux hybrides",
    "code4": "B3f2",
    "Sous-sections niv4": "Vieillissement, corrosion et recyclage des matériaux"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B3",
    "Sous-domaines niv2": "Matériaux, Métallurgie",
    "code3": "B3f",
    "SECTION niv3": "Matériaux hybrides",
    "code4": "B3f3",
    "Sous-sections niv4": "Caractérisation"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B3",
    "Sous-domaines niv2": "Matériaux, Métallurgie",
    "code3": "B3f",
    "SECTION niv3": "Matériaux hybrides",
    "code4": "B3f4",
    "Sous-sections niv4": "Modélisation multiéchelles"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B3",
    "Sous-domaines niv2": "Matériaux, Métallurgie",
    "code3": "B3g",
    "SECTION niv3": "Multimatériaux",
    "code4": "B3g1",
    "Sous-sections niv4": "Synthèse, mise en forme"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B3",
    "Sous-domaines niv2": "Matériaux, Métallurgie",
    "code3": "B3g",
    "SECTION niv3": "Multimatériaux",
    "code4": "B3g2",
    "Sous-sections niv4": "Vieillissement, corrosion et recyclage des matériaux"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B3",
    "Sous-domaines niv2": "Matériaux, Métallurgie",
    "code3": "B3g",
    "SECTION niv3": "Multimatériaux",
    "code4": "B3g3",
    "Sous-sections niv4": "Caractérisation"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B3",
    "Sous-domaines niv2": "Matériaux, Métallurgie",
    "code3": "B3g",
    "SECTION niv3": "Multimatériaux",
    "code4": "B3g4",
    "Sous-sections niv4": "Modélisation multiéchelles"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B3",
    "Sous-domaines niv2": "Matériaux, Métallurgie",
    "code3": "B3h",
    "SECTION niv3": "Matériaux biosourcés, bioinspirés, biomatériaux",
    "code4": "B3h1",
    "Sous-sections niv4": "Synthèse, mise en forme"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B3",
    "Sous-domaines niv2": "Matériaux, Métallurgie",
    "code3": "B3h",
    "SECTION niv3": "Matériaux biosourcés, bioinspirés, biomatériaux",
    "code4": "B3h2",
    "Sous-sections niv4": "Vieillissement, corrosion et recyclage des matériaux"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B3",
    "Sous-domaines niv2": "Matériaux, Métallurgie",
    "code3": "B3h",
    "SECTION niv3": "Matériaux biosourcés, bioinspirés, biomatériaux",
    "code4": "B3h3",
    "Sous-sections niv4": "Caractérisation"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B3",
    "Sous-domaines niv2": "Matériaux, Métallurgie",
    "code3": "B3h",
    "SECTION niv3": "Matériaux biosourcés, bioinspirés, biomatériaux",
    "code4": "B3h4",
    "Sous-sections niv4": "Modélisation multiéchelles"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4a",
    "SECTION niv3": "Mécanique fondamentale",
    "code4": "B4a1",
    "Sous-sections niv4": "Mécanique analytique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4a",
    "SECTION niv3": "Mécanique fondamentale",
    "code4": "B4a2",
    "Sous-sections niv4": "Mécanique des milieux continus"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4a",
    "SECTION niv3": "Mécanique fondamentale",
    "code4": "B4a3",
    "Sous-sections niv4": "Couplages multiphysiques"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4a",
    "SECTION niv3": "Mécanique fondamentale",
    "code4": "B4a4",
    "Sous-sections niv4": "Modélisations multiéchelles"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4b",
    "SECTION niv3": "Mécanique des structures",
    "code4": "B4b1",
    "Sous-sections niv4": "Composites"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4b",
    "SECTION niv3": "Mécanique des structures",
    "code4": "B4b2",
    "Sous-sections niv4": "Flambage"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4b",
    "SECTION niv3": "Mécanique des structures",
    "code4": "B4b3",
    "Sous-sections niv4": "Calcul et tenue des structures, transmission de puissance, boite de vitesse, embrayage, réducteur"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4b",
    "SECTION niv3": "Mécanique des structures",
    "code4": "B4b4",
    "Sous-sections niv4": "Dynamique des structures"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4b",
    "SECTION niv3": "Mécanique des structures",
    "code4": "B4b5",
    "Sous-sections niv4": "Structures adaptatives"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4b",
    "SECTION niv3": "Mécanique des structures",
    "code4": "B4b6",
    "Sous-sections niv4": "Méthodes numériques"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4b",
    "SECTION niv3": "Mécanique des structures",
    "code4": "B4b7",
    "Sous-sections niv4": "Mécanique du contact"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4b",
    "SECTION niv3": "Mécanique des structures",
    "code4": "B4b8",
    "Sous-sections niv4": "Tribologie et contact"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4b",
    "SECTION niv3": "Mécanique des structures",
    "code4": "B4b9",
    "Sous-sections niv4": "Milieux granulaires"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4c",
    "SECTION niv3": "Mécanique des matériaux",
    "code4": "B4c1",
    "Sous-sections niv4": "Lois de comportement, couplages thermomécaniques, alliages à mémoire de forme, chargements multiaxiaux"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4c",
    "SECTION niv3": "Mécanique des matériaux",
    "code4": "B4c2",
    "Sous-sections niv4": "Rhéologie"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4c",
    "SECTION niv3": "Mécanique des matériaux",
    "code4": "B4c3",
    "Sous-sections niv4": "Plasticité"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4c",
    "SECTION niv3": "Mécanique des matériaux",
    "code4": "B4c4",
    "Sous-sections niv4": "Endommagement"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4c",
    "SECTION niv3": "Mécanique des matériaux",
    "code4": "B4c5",
    "Sous-sections niv4": "Rupture"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4c",
    "SECTION niv3": "Mécanique des matériaux",
    "code4": "B4c6",
    "Sous-sections niv4": "Fatigue"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4d",
    "SECTION niv3": "Génie mécanique",
    "code4": "B4d1",
    "Sous-sections niv4": "Mise en forme, fonderie, forge"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4d",
    "SECTION niv3": "Génie mécanique",
    "code4": "B4d2",
    "Sous-sections niv4": "Procédés de fabrication, fabrication additive, usinage des composites, usinage grande vitesse [UGV]"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4e",
    "SECTION niv3": "Mécanique des fluides",
    "code4": "B4e1",
    "Sous-sections niv4": "Aérodynamique, turbulence, instabilités et transition"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4e",
    "SECTION niv3": "Mécanique des fluides",
    "code4": "B4e2",
    "Sous-sections niv4": "Hydrodynamique, machines à fluide, pompage, turbinage"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4e",
    "SECTION niv3": "Mécanique des fluides",
    "code4": "B4e3",
    "Sous-sections niv4": "Convection, transferts convectifs de chaleur et de masse"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4e",
    "SECTION niv3": "Mécanique des fluides",
    "code4": "B4e4",
    "Sous-sections niv4": "Magnétohydrodynamique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4e",
    "SECTION niv3": "Mécanique des fluides",
    "code4": "B4e5",
    "Sous-sections niv4": "Fluides complexes, fluides compressibles, transition de phase liquide / vapeur"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4e",
    "SECTION niv3": "Mécanique des fluides",
    "code4": "B4e6",
    "Sous-sections niv4": "Microfluidique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4e",
    "SECTION niv3": "Mécanique des fluides",
    "code4": "B4e7",
    "Sous-sections niv4": "Nanofluidique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4e",
    "SECTION niv3": "Mécanique des fluides",
    "code4": "B4e8",
    "Sous-sections niv4": "Mécanique des fluides expérimentale"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4e",
    "SECTION niv3": "Mécanique des fluides",
    "code4": "B4e9",
    "Sous-sections niv4": "Mécanique des fluides numérique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4f",
    "SECTION niv3": "Acoustique",
    "code4": "B4f1",
    "Sous-sections niv4": "Aéroacoustique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4f",
    "SECTION niv3": "Acoustique",
    "code4": "B4f2",
    "Sous-sections niv4": "Vibroacoustique, chambre anéchoïque"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4f",
    "SECTION niv3": "Acoustique",
    "code4": "B4f3",
    "Sous-sections niv4": "Imagerie acoustique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4f",
    "SECTION niv3": "Acoustique",
    "code4": "B4f4",
    "Sous-sections niv4": "Acoustique des bâtiments"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4f",
    "SECTION niv3": "Acoustique",
    "code4": "B4f5",
    "Sous-sections niv4": "Ondes ultrasonores, imagerie ultrasonore, contrôle non destructif par ultrasons"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4g",
    "SECTION niv3": "Génie civil",
    "code4": "B4g1",
    "Sous-sections niv4": "Constructions durables"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4g",
    "SECTION niv3": "Génie civil",
    "code4": "B4g2",
    "Sous-sections niv4": "Infrastructures de transport"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4g",
    "SECTION niv3": "Génie civil",
    "code4": "B4g3",
    "Sous-sections niv4": "Dynamique, vibrations"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4g",
    "SECTION niv3": "Génie civil",
    "code4": "B4g4",
    "Sous-sections niv4": "Ecoconception"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4g",
    "SECTION niv3": "Génie civil",
    "code4": "B4g5",
    "Sous-sections niv4": "Construction hydraulique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4g",
    "SECTION niv3": "Génie civil",
    "code4": "B4g6",
    "Sous-sections niv4": "Calculs scientifiques et modélisations en génie civil, fluage, fissuration, dégradation"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4g",
    "SECTION niv3": "Génie civil",
    "code4": "B4g7",
    "Sous-sections niv4": "Génie civil nucléaire, fluage, fissuration, dégradation, durabilité, vieillissement"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4g",
    "SECTION niv3": "Génie civil",
    "code4": "B4g8",
    "Sous-sections niv4": "Construction en bois"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4g",
    "SECTION niv3": "Génie civil",
    "code4": "B4g9",
    "Sous-sections niv4": "Réhabilitation"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4g",
    "SECTION niv3": "Génie civil",
    "code4": "B4g10",
    "Sous-sections niv4": "Mécanique des sols, géomécanique, géotechnique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4g",
    "SECTION niv3": "Génie civil",
    "code4": "B4g11",
    "Sous-sections niv4": "Géomatériaux"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4g",
    "SECTION niv3": "Génie civil",
    "code4": "B4g12",
    "Sous-sections niv4": "Physique du bâtiment, thermique de l'habitat"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4g",
    "SECTION niv3": "Génie civil",
    "code4": "B4g13",
    "Sous-sections niv4": "Matériaux de constructions"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4g",
    "SECTION niv3": "Génie civil",
    "code4": "B4g14",
    "Sous-sections niv4": "Technologies de construction"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4g",
    "SECTION niv3": "Génie civil",
    "code4": "B4g15",
    "Sous-sections niv4": "Contrôle non destructifs des ouvrages"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4g",
    "SECTION niv3": "Génie civil",
    "code4": "B4g16",
    "Sous-sections niv4": "Aménagement technique et environnement"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4g",
    "SECTION niv3": "Génie civil",
    "code4": "B4g17",
    "Sous-sections niv4": "Maîtrise des risques"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4g",
    "SECTION niv3": "Génie civil",
    "code4": "B4g18",
    "Sous-sections niv4": "Modélisation des informations du bâtiment [BIM]"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4g",
    "SECTION niv3": "Génie civil",
    "code4": "B4g19",
    "Sous-sections niv4": "Génie minier"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4h",
    "SECTION niv3": "Biomécanique",
    "code4": "B4h1",
    "Sous-sections niv4": "Rhéologie des fluides et des matériaux biologiques"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4h",
    "SECTION niv3": "Biomécanique",
    "code4": "B4h2",
    "Sous-sections niv4": "Instrumentation"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4h",
    "SECTION niv3": "Biomécanique",
    "code4": "B4h3",
    "Sous-sections niv4": "couplage fluide / structure"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4h",
    "SECTION niv3": "Biomécanique",
    "code4": "B4h4",
    "Sous-sections niv4": "Mécanique du vivant"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4i",
    "SECTION niv3": "Génie industriel",
    "code4": "B4i1",
    "Sous-sections niv4": "Conception des produits et des systèmes, machines outils, engins industriels, usines, systèmes de production"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4i",
    "SECTION niv3": "Génie industriel",
    "code4": "B4i2",
    "Sous-sections niv4": "Ingénierie de la conception et de la fabrication"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4i",
    "SECTION niv3": "Génie industriel",
    "code4": "B4i3",
    "Sous-sections niv4": "Gestion du cycle de vie des produits, analyse du cycle de vie [ACV]"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4i",
    "SECTION niv3": "Génie industriel",
    "code4": "B4i4",
    "Sous-sections niv4": "Qualité"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4i",
    "SECTION niv3": "Génie industriel",
    "code4": "B4i5",
    "Sous-sections niv4": "Maintenance"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4j",
    "SECTION niv3": "Domaines applicatifs",
    "code4": "B4j1",
    "Sous-sections niv4": "Industrie automobile, voiture, réseau routier"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4j",
    "SECTION niv3": "Domaines applicatifs",
    "code4": "B4j2",
    "Sous-sections niv4": "Industrie ferroviaire, train, réseau ferré"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4j",
    "SECTION niv3": "Domaines applicatifs",
    "code4": "B4j3",
    "Sous-sections niv4": "Industrie aéronautique, avion, hélicoptère, ULM, dirigeable, montgolfière, drone"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4j",
    "SECTION niv3": "Domaines applicatifs",
    "code4": "B4j4",
    "Sous-sections niv4": "Génie côtier et industrie offshore"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4j",
    "SECTION niv3": "Domaines applicatifs",
    "code4": "B4j5",
    "Sous-sections niv4": "Génie civil"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4j",
    "SECTION niv3": "Domaines applicatifs",
    "code4": "B4j6",
    "Sous-sections niv4": "Industrie du bâtiment"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4j",
    "SECTION niv3": "Domaines applicatifs",
    "code4": "B4j7",
    "Sous-sections niv4": "Ouvrages industriels"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4j",
    "SECTION niv3": "Domaines applicatifs",
    "code4": "B4j8",
    "Sous-sections niv4": "Industrie maritime, nautique, bateau, voilier"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B4",
    "Sous-domaines niv2": "Mécanique, Génie mécanique, Génie civil",
    "code3": "B4j",
    "SECTION niv3": "Domaines applicatifs",
    "code4": "B4j9",
    "Sous-sections niv4": "Industrie aérospatiale, fusée, satellite"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B5",
    "Sous-domaines niv2": "Chimie",
    "code3": "B5a",
    "SECTION niv3": "Chimie théorique, physique, analytique",
    "code4": "B5a1",
    "Sous-sections niv4": "Mécanismes réactionnels"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B5",
    "Sous-domaines niv2": "Chimie",
    "code3": "B5a",
    "SECTION niv3": "Chimie théorique, physique, analytique",
    "code4": "B5a2",
    "Sous-sections niv4": "Thermodynamique, Cinétique chimique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B5",
    "Sous-domaines niv2": "Chimie",
    "code3": "B5a",
    "SECTION niv3": "Chimie théorique, physique, analytique",
    "code4": "B5a3",
    "Sous-sections niv4": "Spectroscopie, spectroscopie de masse, chromatographie"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B5",
    "Sous-domaines niv2": "Chimie",
    "code3": "B5a",
    "SECTION niv3": "Chimie théorique, physique, analytique",
    "code4": "B5a4",
    "Sous-sections niv4": "Electrochimie"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B5",
    "Sous-domaines niv2": "Chimie",
    "code3": "B5a",
    "SECTION niv3": "Chimie théorique, physique, analytique",
    "code4": "B5a5",
    "Sous-sections niv4": "Photochimie"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B5",
    "Sous-domaines niv2": "Chimie",
    "code3": "B5a",
    "SECTION niv3": "Chimie théorique, physique, analytique",
    "code4": "B5a6",
    "Sous-sections niv4": "Photophysique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B5",
    "Sous-domaines niv2": "Chimie",
    "code3": "B5a",
    "SECTION niv3": "Chimie théorique, physique, analytique",
    "code4": "B5a7",
    "Sous-sections niv4": "Physique moléculaire"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B5",
    "Sous-domaines niv2": "Chimie",
    "code3": "B5a",
    "SECTION niv3": "Chimie théorique, physique, analytique",
    "code4": "B5a8",
    "Sous-sections niv4": "Radiochimie"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B5",
    "Sous-domaines niv2": "Chimie",
    "code3": "B5a",
    "SECTION niv3": "Chimie théorique, physique, analytique",
    "code4": "B5a9",
    "Sous-sections niv4": "Physicochimie, chimie atmosphérique et chimie de l'environnement, l'art et l'archéologie"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B5",
    "Sous-domaines niv2": "Chimie",
    "code3": "B5b",
    "SECTION niv3": "Chimie organique, minérale, industrielle",
    "code4": "B5b1",
    "Sous-sections niv4": "Synthèse organique, chimie verte, synthèse aromatique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B5",
    "Sous-domaines niv2": "Chimie",
    "code3": "B5b",
    "SECTION niv3": "Chimie organique, minérale, industrielle",
    "code4": "B5b2",
    "Sous-sections niv4": "Bioorganique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B5",
    "Sous-domaines niv2": "Chimie",
    "code3": "B5b",
    "SECTION niv3": "Chimie organique, minérale, industrielle",
    "code4": "B5b3",
    "Sous-sections niv4": "Supramoléculaire"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B5",
    "Sous-domaines niv2": "Chimie",
    "code3": "B5b",
    "SECTION niv3": "Chimie organique, minérale, industrielle",
    "code4": "B5b4",
    "Sous-sections niv4": "Inorganique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B5",
    "Sous-domaines niv2": "Chimie",
    "code3": "B5b",
    "SECTION niv3": "Chimie organique, minérale, industrielle",
    "code4": "B5b5",
    "Sous-sections niv4": "Minérale"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B5",
    "Sous-domaines niv2": "Chimie",
    "code3": "B5b",
    "SECTION niv3": "Chimie organique, minérale, industrielle",
    "code4": "B5b6",
    "Sous-sections niv4": "Aspects moléculaires de la chimie industrielle, chimie du bois, papier, monomères, macromonomère, béton"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B5",
    "Sous-domaines niv2": "Chimie",
    "code3": "B5b",
    "SECTION niv3": "Chimie organique, minérale, industrielle",
    "code4": "B5b7",
    "Sous-sections niv4": "Batteries électrochimiques, supercapacités"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B5",
    "Sous-domaines niv2": "Chimie",
    "code3": "B5b",
    "SECTION niv3": "Chimie organique, minérale, industrielle",
    "code4": "B5b8",
    "Sous-sections niv4": "Piles à combustibles [PAC]  [SOFC]  [SOEC]  [PCFC], biopile"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B5",
    "Sous-domaines niv2": "Chimie",
    "code3": "B5b",
    "SECTION niv3": "Chimie organique, minérale, industrielle",
    "code4": "B5b9",
    "Sous-sections niv4": "Polymères"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B5",
    "Sous-domaines niv2": "Chimie",
    "code3": "B5b",
    "SECTION niv3": "Chimie organique, minérale, industrielle",
    "code4": "B5b10",
    "Sous-sections niv4": "Electrochimie, électrodépôt"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B5",
    "Sous-domaines niv2": "Chimie",
    "code3": "B5b",
    "SECTION niv3": "Chimie organique, minérale, industrielle",
    "code4": "B5b11",
    "Sous-sections niv4": "Photochimie"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B5",
    "Sous-domaines niv2": "Chimie",
    "code3": "B5b",
    "SECTION niv3": "Chimie organique, minérale, industrielle",
    "code4": "B5b12",
    "Sous-sections niv4": "Catalyse"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B5",
    "Sous-domaines niv2": "Chimie",
    "code3": "B5b",
    "SECTION niv3": "Chimie organique, minérale, industrielle",
    "code4": "B5b13",
    "Sous-sections niv4": "Nanomatériaux, procédés de nanofabrication, caractérisation nanométrique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6a",
    "SECTION niv3": "Optique",
    "code4": "B6a1",
    "Sous-sections niv4": "Optique géométrique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6a",
    "SECTION niv3": "Optique",
    "code4": "B6a2",
    "Sous-sections niv4": "Optique quantique, optique atomique, spectroscopie optique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6a",
    "SECTION niv3": "Optique",
    "code4": "B6a3",
    "Sous-sections niv4": "Optique et biologie"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6a",
    "SECTION niv3": "Optique",
    "code4": "B6a4",
    "Sous-sections niv4": "Lentilles, verres"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6a",
    "SECTION niv3": "Optique",
    "code4": "B6a5",
    "Sous-sections niv4": "Capteurs optiques"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6a",
    "SECTION niv3": "Optique",
    "code4": "B6a6",
    "Sous-sections niv4": "Composants optiques"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6a",
    "SECTION niv3": "Optique",
    "code4": "B6a7",
    "Sous-sections niv4": "Optique physiologique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6a",
    "SECTION niv3": "Optique",
    "code4": "B6a8",
    "Sous-sections niv4": "Optique photographique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6a",
    "SECTION niv3": "Optique",
    "code4": "B6a9",
    "Sous-sections niv4": "Optronique, optoélectronique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6a",
    "SECTION niv3": "Optique",
    "code4": "B6a10",
    "Sous-sections niv4": "Fibres optiques"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6a",
    "SECTION niv3": "Optique",
    "code4": "B6a11",
    "Sous-sections niv4": "Lasers"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6a",
    "SECTION niv3": "Optique",
    "code4": "B6a12",
    "Sous-sections niv4": "Miroirs optiques"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6a",
    "SECTION niv3": "Optique",
    "code4": "B6a13",
    "Sous-sections niv4": "Microscopes, microscopes optiques, microscopes électroniques"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6a",
    "SECTION niv3": "Optique",
    "code4": "B6a14",
    "Sous-sections niv4": "Télescopes optiques"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6a",
    "SECTION niv3": "Optique",
    "code4": "B6a15",
    "Sous-sections niv4": "Matériaux pour l'optique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6a",
    "SECTION niv3": "Optique",
    "code4": "B6a16",
    "Sous-sections niv4": "Radars optiques, LIDAR"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6a",
    "SECTION niv3": "Optique",
    "code4": "B6a17",
    "Sous-sections niv4": "Applications à l'information quantique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6a",
    "SECTION niv3": "Optique",
    "code4": "B6a18",
    "Sous-sections niv4": "Nanophotonique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6a",
    "SECTION niv3": "Optique",
    "code4": "B6a19",
    "Sous-sections niv4": "Lumière, éclairage"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6b",
    "SECTION niv3": "Physique théorique et expérimentale",
    "code4": "B6b1",
    "Sous-sections niv4": "Structure nucléaire"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6b",
    "SECTION niv3": "Physique théorique et expérimentale",
    "code4": "B6b2",
    "Sous-sections niv4": "Physique des réacteurs"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6b",
    "SECTION niv3": "Physique théorique et expérimentale",
    "code4": "B6b3",
    "Sous-sections niv4": "Astrophysique nucléaire"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6b",
    "SECTION niv3": "Physique théorique et expérimentale",
    "code4": "B6b4",
    "Sous-sections niv4": "Physique hadronique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6b",
    "SECTION niv3": "Physique théorique et expérimentale",
    "code4": "B6b5",
    "Sous-sections niv4": "Plasma quark gluon"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6b",
    "SECTION niv3": "Physique théorique et expérimentale",
    "code4": "B6b6",
    "Sous-sections niv4": "Applications médicales des rayonnements et détecteurs de rayonnements ionisants"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6b",
    "SECTION niv3": "Physique théorique et expérimentale",
    "code4": "B6b7",
    "Sous-sections niv4": "Quarks, leptons"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6b",
    "SECTION niv3": "Physique théorique et expérimentale",
    "code4": "B6b8",
    "Sous-sections niv4": "Astroparticules et cosmologie"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6b",
    "SECTION niv3": "Physique théorique et expérimentale",
    "code4": "B6b9",
    "Sous-sections niv4": "Ondes gravitationnelles"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6b",
    "SECTION niv3": "Physique théorique et expérimentale",
    "code4": "B6b10",
    "Sous-sections niv4": "Matière sombre et énergie noire"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6b",
    "SECTION niv3": "Physique théorique et expérimentale",
    "code4": "B6b11",
    "Sous-sections niv4": "Physique des hautes énergies expérimentale"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6b",
    "SECTION niv3": "Physique théorique et expérimentale",
    "code4": "B6b12",
    "Sous-sections niv4": "Accélérateurs, agrégats, instrumentation"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6c",
    "SECTION niv3": "Physique théorique",
    "code4": "B6c1",
    "Sous-sections niv4": "Champs classiques et quantiques"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6c",
    "SECTION niv3": "Physique théorique",
    "code4": "B6c2",
    "Sous-sections niv4": "Modélisation et simulation"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6c",
    "SECTION niv3": "Physique théorique",
    "code4": "B6c3",
    "Sous-sections niv4": "Physique statistique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6c",
    "SECTION niv3": "Physique théorique",
    "code4": "B6c4",
    "Sous-sections niv4": "Phénomènes non linéaires"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6c",
    "SECTION niv3": "Physique théorique",
    "code4": "B6c5",
    "Sous-sections niv4": "Systèmes dynamiques, systèmes intégrables, processus irréversibles, chaos classique et quantique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6c",
    "SECTION niv3": "Physique théorique",
    "code4": "B6c6",
    "Sous-sections niv4": "Physique mathématique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6c",
    "SECTION niv3": "Physique théorique",
    "code4": "B6c7",
    "Sous-sections niv4": "Biophysique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6c",
    "SECTION niv3": "Physique théorique",
    "code4": "B6c8",
    "Sous-sections niv4": "Physique du vivant"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6c",
    "SECTION niv3": "Physique théorique",
    "code4": "B6c9",
    "Sous-sections niv4": "Electromagnétisme, électrostatique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6c",
    "SECTION niv3": "Physique théorique",
    "code4": "B6c10",
    "Sous-sections niv4": "Géophysique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6c",
    "SECTION niv3": "Physique théorique",
    "code4": "B6c11",
    "Sous-sections niv4": "Physique atmosphérique et océanique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6c",
    "SECTION niv3": "Physique théorique",
    "code4": "B6c12",
    "Sous-sections niv4": "Supraconductivité, bobines supraconductrices, aimants supraconduteurs"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6c",
    "SECTION niv3": "Physique théorique",
    "code4": "B6c13",
    "Sous-sections niv4": "Thermodynamique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B6",
    "Sous-domaines niv2": "Physique",
    "code3": "B6c",
    "SECTION niv3": "Physique théorique",
    "code4": "B6c14",
    "Sous-sections niv4": "Interactions des champs et des systèmes vivants"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7a",
    "SECTION niv3": "Thermique, énergétique",
    "code4": "B7a1",
    "Sous-sections niv4": "Transferts thermiques, échangeurs de chaleur, pyrolyse, cryogénie"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7a",
    "SECTION niv3": "Thermique, énergétique",
    "code4": "B7a2",
    "Sous-sections niv4": "Aérodynamique et contrôle des écoulements, turbomachines"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7a",
    "SECTION niv3": "Thermique, énergétique",
    "code4": "B7a3",
    "Sous-sections niv4": "Magnétohydrodynamique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7a",
    "SECTION niv3": "Thermique, énergétique",
    "code4": "B7a4",
    "Sous-sections niv4": "Energies éoliennes et marines"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7a",
    "SECTION niv3": "Thermique, énergétique",
    "code4": "B7a5",
    "Sous-sections niv4": "Ecoulements des fluides"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7a",
    "SECTION niv3": "Thermique, énergétique",
    "code4": "B7a6",
    "Sous-sections niv4": "Modélisations et calculs scientifiques"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7b",
    "SECTION niv3": "Combustion, plasma",
    "code4": "B7b1",
    "Sous-sections niv4": "Moteurs à combustion"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7b",
    "SECTION niv3": "Combustion, plasma",
    "code4": "B7b2",
    "Sous-sections niv4": "Carburants"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7b",
    "SECTION niv3": "Combustion, plasma",
    "code4": "B7b3",
    "Sous-sections niv4": "Carburants alternatifs, biocarburant, eCarburant, electrofuels [efuels]"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7b",
    "SECTION niv3": "Combustion, plasma",
    "code4": "B7b4",
    "Sous-sections niv4": "Propulsion"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7b",
    "SECTION niv3": "Combustion, plasma",
    "code4": "B7b5",
    "Sous-sections niv4": "Propulsion hybride"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7b",
    "SECTION niv3": "Combustion, plasma",
    "code4": "B7b6",
    "Sous-sections niv4": "Plasmas, torche, soudure"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7c",
    "SECTION niv3": "Génie des procédés, génie industriel",
    "code4": "B7c1",
    "Sous-sections niv4": "Réacteurs électrochimiques"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7c",
    "SECTION niv3": "Génie des procédés, génie industriel",
    "code4": "B7c2",
    "Sous-sections niv4": "Biotechnologies industrielles, génie des bioréacteurs, génie de la réaction biologique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7c",
    "SECTION niv3": "Génie des procédés, génie industriel",
    "code4": "B7c3",
    "Sous-sections niv4": "Génie alimentaire"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7c",
    "SECTION niv3": "Génie des procédés, génie industriel",
    "code4": "B7c4",
    "Sous-sections niv4": "Contrôle et supervision des procédés"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7c",
    "SECTION niv3": "Génie des procédés, génie industriel",
    "code4": "B7c5",
    "Sous-sections niv4": "Logistique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7c",
    "SECTION niv3": "Génie des procédés, génie industriel",
    "code4": "B7c6",
    "Sous-sections niv4": "Management des systèmes d'information"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7c",
    "SECTION niv3": "Génie des procédés, génie industriel",
    "code4": "B7c7",
    "Sous-sections niv4": "Conception et ergonomie"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7c",
    "SECTION niv3": "Génie des procédés, génie industriel",
    "code4": "B7c8",
    "Sous-sections niv4": "Production industrielle"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7c",
    "SECTION niv3": "Génie des procédés, génie industriel",
    "code4": "B7c9",
    "Sous-sections niv4": "Industrie textile"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7c",
    "SECTION niv3": "Génie des procédés, génie industriel",
    "code4": "B7c10",
    "Sous-sections niv4": "Dimensionnement des réacteurs"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7c",
    "SECTION niv3": "Génie des procédés, génie industriel",
    "code4": "B7c11",
    "Sous-sections niv4": "Microfluidique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7c",
    "SECTION niv3": "Génie des procédés, génie industriel",
    "code4": "B7c12",
    "Sous-sections niv4": "Industrie de l'emballage, impression, impression de sécurité, couleur"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7c",
    "SECTION niv3": "Génie des procédés, génie industriel",
    "code4": "B7c13",
    "Sous-sections niv4": "Sûreté de fonctionnement et maintenance"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7c",
    "SECTION niv3": "Génie des procédés, génie industriel",
    "code4": "B7c14",
    "Sous-sections niv4": "Analyse du cycle de vie [ACV]"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7d",
    "SECTION niv3": "Génie de l'environnement",
    "code4": "B7d1",
    "Sous-sections niv4": "Installations de traitement de l'eau, des gaz et des déchets solides"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7d",
    "SECTION niv3": "Génie de l'environnement",
    "code4": "B7d2",
    "Sous-sections niv4": "Traitements des déchets et des effluents"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7d",
    "SECTION niv3": "Génie de l'environnement",
    "code4": "B7d3",
    "Sous-sections niv4": "Surveillance des pollutions"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7d",
    "SECTION niv3": "Génie de l'environnement",
    "code4": "B7d4",
    "Sous-sections niv4": "Réhabilitations des sites et sols pollués"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7d",
    "SECTION niv3": "Génie de l'environnement",
    "code4": "B7d5",
    "Sous-sections niv4": "Atmosphère intérieure et atmosphère extérieure"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7d",
    "SECTION niv3": "Génie de l'environnement",
    "code4": "B7d6",
    "Sous-sections niv4": "Systèmes aquatiques"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7e",
    "SECTION niv3": "Energies",
    "code4": "B7e1",
    "Sous-sections niv4": "Energies solaire thermique, photovoltaïque"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7e",
    "SECTION niv3": "Energies",
    "code4": "B7e2",
    "Sous-sections niv4": "Cellules et panneaux photovoltaïques"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7e",
    "SECTION niv3": "Energies",
    "code4": "B7e3",
    "Sous-sections niv4": "Production de l'énergie éolienne"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7e",
    "SECTION niv3": "Energies",
    "code4": "B7e4",
    "Sous-sections niv4": "Energies hydrolienne, marémotrice, des vagues"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7e",
    "SECTION niv3": "Energies",
    "code4": "B7e5",
    "Sous-sections niv4": "Filière hydrogène, électrolyseurs, pile à combustible"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7e",
    "SECTION niv3": "Energies",
    "code4": "B7e6",
    "Sous-sections niv4": "Energies fossiles, extractions, raffinage"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7e",
    "SECTION niv3": "Energies",
    "code4": "B7e7",
    "Sous-sections niv4": "Transport de l'énergie"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7e",
    "SECTION niv3": "Energies",
    "code4": "B7e8",
    "Sous-sections niv4": "Stockage de l'énergie"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7e",
    "SECTION niv3": "Energies",
    "code4": "B7e9",
    "Sous-sections niv4": "Energie nucléaire"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7e",
    "SECTION niv3": "Energies",
    "code4": "B7e10",
    "Sous-sections niv4": "Energies alternatives, thermique des mers, osmotique, géothermique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B7",
    "Sous-domaines niv2": "Energétique, Génie des procédés, Energies",
    "code3": "B7e",
    "SECTION niv3": "Energies",
    "code4": "B7e11",
    "Sous-sections niv4": "Bilan carbone, neutralité carbone"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B8",
    "Sous-domaines niv2": "Océan, Atmosphère, Terre",
    "code3": "B8a",
    "SECTION niv3": "Météorologie, sciences du climat",
    "code4": "B8a1",
    "Sous-sections niv4": "Physique et chimie de l'atmosphère"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B8",
    "Sous-domaines niv2": "Océan, Atmosphère, Terre",
    "code3": "B8a",
    "SECTION niv3": "Météorologie, sciences du climat",
    "code4": "B8a2",
    "Sous-sections niv4": "Outils numériques et statistiques pour les sciences de la planète"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B8",
    "Sous-domaines niv2": "Océan, Atmosphère, Terre",
    "code3": "B8b",
    "SECTION niv3": "Océanographie",
    "code4": "B8b1",
    "Sous-sections niv4": "Dynamique de l'océan et de l'atmosphère"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B8",
    "Sous-domaines niv2": "Océan, Atmosphère, Terre",
    "code3": "B8b",
    "SECTION niv3": "Océanographie",
    "code4": "B8b2",
    "Sous-sections niv4": "Hydrographie"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B8",
    "Sous-domaines niv2": "Océan, Atmosphère, Terre",
    "code3": "B8b",
    "SECTION niv3": "Océanographie",
    "code4": "B8b3",
    "Sous-sections niv4": "Observations océanographiques"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B8",
    "Sous-domaines niv2": "Océan, Atmosphère, Terre",
    "code3": "B8b",
    "SECTION niv3": "Océanographie",
    "code4": "B8b4",
    "Sous-sections niv4": "Physique et chimie des océans"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B8",
    "Sous-domaines niv2": "Océan, Atmosphère, Terre",
    "code3": "B8c",
    "SECTION niv3": "Sciences de l'environnement",
    "code4": "B8c1",
    "Sous-sections niv4": "Ingénierie pour les observations spatiales"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B8",
    "Sous-domaines niv2": "Océan, Atmosphère, Terre",
    "code3": "B8c",
    "SECTION niv3": "Sciences de l'environnement",
    "code4": "B8c2",
    "Sous-sections niv4": "Systèmes d'information géographiques"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B8",
    "Sous-domaines niv2": "Océan, Atmosphère, Terre",
    "code3": "B8c",
    "SECTION niv3": "Sciences de l'environnement",
    "code4": "B8c3",
    "Sous-sections niv4": "Ingénierie des eaux et forêts"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B8",
    "Sous-domaines niv2": "Océan, Atmosphère, Terre",
    "code3": "B8c",
    "SECTION niv3": "Sciences de l'environnement",
    "code4": "B8c4",
    "Sous-sections niv4": "Hydrologie"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B8",
    "Sous-domaines niv2": "Océan, Atmosphère, Terre",
    "code3": "B8d",
    "SECTION niv3": "Astronomie, astrophysique",
    "code4": "B8d1",
    "Sous-sections niv4": "Astronomie"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B8",
    "Sous-domaines niv2": "Océan, Atmosphère, Terre",
    "code3": "B8d",
    "SECTION niv3": "Astronomie, astrophysique",
    "code4": "B8d2",
    "Sous-sections niv4": "Astrophysique"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B8",
    "Sous-domaines niv2": "Océan, Atmosphère, Terre",
    "code3": "B8d",
    "SECTION niv3": "Astronomie, astrophysique",
    "code4": "B8d3",
    "Sous-sections niv4": "Ingénierie des satellites, ingénierie spatiale"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B8",
    "Sous-domaines niv2": "Océan, Atmosphère, Terre",
    "code3": "B8e",
    "SECTION niv3": "Géosciences",
    "code4": "B8e1",
    "Sous-sections niv4": "Géosciences"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B8",
    "Sous-domaines niv2": "Océan, Atmosphère, Terre",
    "code3": "B8e",
    "SECTION niv3": "Géosciences",
    "code4": "B8e2",
    "Sous-sections niv4": "Géologie"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B8",
    "Sous-domaines niv2": "Océan, Atmosphère, Terre",
    "code3": "B8e",
    "SECTION niv3": "Géosciences",
    "code4": "B8e3",
    "Sous-sections niv4": "Minéralogie"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B8",
    "Sous-domaines niv2": "Océan, Atmosphère, Terre",
    "code3": "B8e",
    "SECTION niv3": "Géosciences",
    "code4": "B8e4",
    "Sous-sections niv4": "Géomorphologie"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B8",
    "Sous-domaines niv2": "Océan, Atmosphère, Terre",
    "code3": "B8e",
    "SECTION niv3": "Géosciences",
    "code4": "B8e5",
    "Sous-sections niv4": "Pétrologie"
  },
  {
    "code1": "B",
    "DOMAINES niv1": "SCIENCES et TECHNIQUES INDUSTRIELLES, PHYSIQUE",
    "code2": "B8",
    "Sous-domaines niv2": "Océan, Atmosphère, Terre",
    "code3": "B8e",
    "SECTION niv3": "Géosciences",
    "code4": "B8e6",
    "Sous-sections niv4": "Stratigraphie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C1",
    "Sous-domaines niv2": "Biologie de la cellule",
    "code3": "C1a",
    "SECTION niv3": "Biochimie, biologie structurale",
    "code4": "C1a1",
    "Sous-sections niv4": "Biomolécules"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C1",
    "Sous-domaines niv2": "Biologie de la cellule",
    "code3": "C1a",
    "SECTION niv3": "Biochimie, biologie structurale",
    "code4": "C1a2",
    "Sous-sections niv4": "Processus cellulaires"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C1",
    "Sous-domaines niv2": "Biologie de la cellule",
    "code3": "C1a",
    "SECTION niv3": "Biochimie, biologie structurale",
    "code4": "C1a3",
    "Sous-sections niv4": "Séparation, chromatographie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C1",
    "Sous-domaines niv2": "Biologie de la cellule",
    "code3": "C1a",
    "SECTION niv3": "Biochimie, biologie structurale",
    "code4": "C1a4",
    "Sous-sections niv4": "Enzymologie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C1",
    "Sous-domaines niv2": "Biologie de la cellule",
    "code3": "C1a",
    "SECTION niv3": "Biochimie, biologie structurale",
    "code4": "C1a5",
    "Sous-sections niv4": "Spectrométrie de masse"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C1",
    "Sous-domaines niv2": "Biologie de la cellule",
    "code3": "C1a",
    "SECTION niv3": "Biochimie, biologie structurale",
    "code4": "C1a6",
    "Sous-sections niv4": "Microscopie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C1",
    "Sous-domaines niv2": "Biologie de la cellule",
    "code3": "C1a",
    "SECTION niv3": "Biochimie, biologie structurale",
    "code4": "C1a7",
    "Sous-sections niv4": "Cristallographie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C1",
    "Sous-domaines niv2": "Biologie de la cellule",
    "code3": "C1a",
    "SECTION niv3": "Biochimie, biologie structurale",
    "code4": "C1a8",
    "Sous-sections niv4": "Résonance magnétique nucléaire [RMN]"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C1",
    "Sous-domaines niv2": "Biologie de la cellule",
    "code3": "C1a",
    "SECTION niv3": "Biochimie, biologie structurale",
    "code4": "C1a9",
    "Sous-sections niv4": "Modélisation moléculaire"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C1",
    "Sous-domaines niv2": "Biologie de la cellule",
    "code3": "C1b",
    "SECTION niv3": "Biologie moléculaire, génétique",
    "code4": "C1b1",
    "Sous-sections niv4": "Séquençage"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C1",
    "Sous-domaines niv2": "Biologie de la cellule",
    "code3": "C1b",
    "SECTION niv3": "Biologie moléculaire, génétique",
    "code4": "C1b2",
    "Sous-sections niv4": "Génome"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C1",
    "Sous-domaines niv2": "Biologie de la cellule",
    "code3": "C1b",
    "SECTION niv3": "Biologie moléculaire, génétique",
    "code4": "C1b3",
    "Sous-sections niv4": "Epigénétique"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C1",
    "Sous-domaines niv2": "Biologie de la cellule",
    "code3": "C1b",
    "SECTION niv3": "Biologie moléculaire, génétique",
    "code4": "C1b4",
    "Sous-sections niv4": "Expression des gènes"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C1",
    "Sous-domaines niv2": "Biologie de la cellule",
    "code3": "C1b",
    "SECTION niv3": "Biologie moléculaire, génétique",
    "code4": "C1b5",
    "Sous-sections niv4": "Métagénomique"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C1",
    "Sous-domaines niv2": "Biologie de la cellule",
    "code3": "C1c",
    "SECTION niv3": "Organisation et fonctions cellulaires",
    "code4": "C1c1",
    "Sous-sections niv4": "Cytosquelette"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C1",
    "Sous-domaines niv2": "Biologie de la cellule",
    "code3": "C1c",
    "SECTION niv3": "Organisation et fonctions cellulaires",
    "code4": "C1c2",
    "Sous-sections niv4": "Organelles, compartimentation"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C1",
    "Sous-domaines niv2": "Biologie de la cellule",
    "code3": "C1c",
    "SECTION niv3": "Organisation et fonctions cellulaires",
    "code4": "C1c3",
    "Sous-sections niv4": "Nutrition, transport membranaire"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C1",
    "Sous-domaines niv2": "Biologie de la cellule",
    "code3": "C1c",
    "SECTION niv3": "Organisation et fonctions cellulaires",
    "code4": "C1c4",
    "Sous-sections niv4": "Respiration, photosynthèse"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C1",
    "Sous-domaines niv2": "Biologie de la cellule",
    "code3": "C1c",
    "SECTION niv3": "Organisation et fonctions cellulaires",
    "code4": "C1c5",
    "Sous-sections niv4": "Croissance, cycle cellulaire"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C1",
    "Sous-domaines niv2": "Biologie de la cellule",
    "code3": "C1c",
    "SECTION niv3": "Organisation et fonctions cellulaires",
    "code4": "C1c6",
    "Sous-sections niv4": "Mouvement, migration"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C1",
    "Sous-domaines niv2": "Biologie de la cellule",
    "code3": "C1c",
    "SECTION niv3": "Organisation et fonctions cellulaires",
    "code4": "C1c7",
    "Sous-sections niv4": "Signalisation cellulaire"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C1",
    "Sous-domaines niv2": "Biologie de la cellule",
    "code3": "C1c",
    "SECTION niv3": "Organisation et fonctions cellulaires",
    "code4": "C1c8",
    "Sous-sections niv4": "Mort cellulaire"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C1",
    "Sous-domaines niv2": "Biologie de la cellule",
    "code3": "C1d",
    "SECTION niv3": "Biologie des systèmes",
    "code4": "C1d1",
    "Sous-sections niv4": "Transcriptomique"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C1",
    "Sous-domaines niv2": "Biologie de la cellule",
    "code3": "C1d",
    "SECTION niv3": "Biologie des systèmes",
    "code4": "C1d2",
    "Sous-sections niv4": "Protéomique"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C1",
    "Sous-domaines niv2": "Biologie de la cellule",
    "code3": "C1d",
    "SECTION niv3": "Biologie des systèmes",
    "code4": "C1d3",
    "Sous-sections niv4": "Métabolomique"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C1",
    "Sous-domaines niv2": "Biologie de la cellule",
    "code3": "C1d",
    "SECTION niv3": "Biologie des systèmes",
    "code4": "C1d4",
    "Sous-sections niv4": "Modélisation"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C1",
    "Sous-domaines niv2": "Biologie de la cellule",
    "code3": "C1e",
    "SECTION niv3": "Bioinformatique, biostatistiques",
    "code4": "C1e1",
    "Sous-sections niv4": "Bioinformatique"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C1",
    "Sous-domaines niv2": "Biologie de la cellule",
    "code3": "C1e",
    "SECTION niv3": "Bioinformatique, biostatistiques",
    "code4": "C1e2",
    "Sous-sections niv4": "Biostatistique"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C1",
    "Sous-domaines niv2": "Biologie de la cellule",
    "code3": "C1e",
    "SECTION niv3": "Bioinformatique, biostatistiques",
    "code4": "C1e3",
    "Sous-sections niv4": "Modélisation"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C1",
    "Sous-domaines niv2": "Biologie de la cellule",
    "code3": "C1e",
    "SECTION niv3": "Bioinformatique, biostatistiques",
    "code4": "C1e4",
    "Sous-sections niv4": "Arbres phylogénétiques"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C1",
    "Sous-domaines niv2": "Biologie de la cellule",
    "code3": "C1f",
    "SECTION niv3": "Microscopie, imagerie",
    "code4": "C1f1",
    "Sous-sections niv4": "Microscopie optique, microscopie photonique"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C1",
    "Sous-domaines niv2": "Biologie de la cellule",
    "code3": "C1f",
    "SECTION niv3": "Microscopie, imagerie",
    "code4": "C1f2",
    "Sous-sections niv4": "Microscopie électronique"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C1",
    "Sous-domaines niv2": "Biologie de la cellule",
    "code3": "C1f",
    "SECTION niv3": "Microscopie, imagerie",
    "code4": "C1f3",
    "Sous-sections niv4": "Microscopie à sonde locale"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C1",
    "Sous-domaines niv2": "Biologie de la cellule",
    "code3": "C1f",
    "SECTION niv3": "Microscopie, imagerie",
    "code4": "C1f4",
    "Sous-sections niv4": "Imagerie moléculaire"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C1",
    "Sous-domaines niv2": "Biologie de la cellule",
    "code3": "C1f",
    "SECTION niv3": "Microscopie, imagerie",
    "code4": "C1f5",
    "Sous-sections niv4": "Imagerie cellulaire"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2a",
    "SECTION niv3": "Microbiologie",
    "code4": "C2a1",
    "Sous-sections niv4": "Bactéries, archébactéries, microbiotes"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2a",
    "SECTION niv3": "Microbiologie",
    "code4": "C2a2",
    "Sous-sections niv4": "Algues"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2a",
    "SECTION niv3": "Microbiologie",
    "code4": "C2a3",
    "Sous-sections niv4": "Champignons"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2a",
    "SECTION niv3": "Microbiologie",
    "code4": "C2a4",
    "Sous-sections niv4": "Virus"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2a",
    "SECTION niv3": "Microbiologie",
    "code4": "C2a5",
    "Sous-sections niv4": "Parasitologie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2b",
    "SECTION niv3": "Biologie de la reproduction et du développement",
    "code4": "C2b1",
    "Sous-sections niv4": "Biologie de la reproduction"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2b",
    "SECTION niv3": "Biologie de la reproduction et du développement",
    "code4": "C2b2",
    "Sous-sections niv4": "Biologie du développement"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2c",
    "SECTION niv3": "Evolution, écologie, biologie des populations",
    "code4": "C2c1",
    "Sous-sections niv4": "Evolution"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2c",
    "SECTION niv3": "Evolution, écologie, biologie des populations",
    "code4": "C2c2",
    "Sous-sections niv4": "Ecologie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2c",
    "SECTION niv3": "Evolution, écologie, biologie des populations",
    "code4": "C2c3",
    "Sous-sections niv4": "Biologie des populations"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2c",
    "SECTION niv3": "Evolution, écologie, biologie des populations",
    "code4": "C2c4",
    "Sous-sections niv4": "Ethologie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2d",
    "SECTION niv3": "Physiologie",
    "code4": "C2d1",
    "Sous-sections niv4": "Système nerveux"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2d",
    "SECTION niv3": "Physiologie",
    "code4": "C2d2",
    "Sous-sections niv4": "Système endocrine"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2d",
    "SECTION niv3": "Physiologie",
    "code4": "C2d3",
    "Sous-sections niv4": "Système musculaire"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2d",
    "SECTION niv3": "Physiologie",
    "code4": "C2d4",
    "Sous-sections niv4": "Système circulatoire"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2d",
    "SECTION niv3": "Physiologie",
    "code4": "C2d5",
    "Sous-sections niv4": "Système respiratoire"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2d",
    "SECTION niv3": "Physiologie",
    "code4": "C2d6",
    "Sous-sections niv4": "Système excréteur"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2d",
    "SECTION niv3": "Physiologie",
    "code4": "C2d7",
    "Sous-sections niv4": "Système digestif"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2d",
    "SECTION niv3": "Physiologie",
    "code4": "C2d8",
    "Sous-sections niv4": "Système reproducteur"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2d",
    "SECTION niv3": "Physiologie",
    "code4": "C2d9",
    "Sous-sections niv4": "Equilibre énergétique"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2e",
    "SECTION niv3": "Médecine humaine",
    "code4": "C2e1",
    "Sous-sections niv4": "Anatomopathologie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2e",
    "SECTION niv3": "Médecine humaine",
    "code4": "C2e2",
    "Sous-sections niv4": "Allergologie, immunologie, infectiologie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2e",
    "SECTION niv3": "Médecine humaine",
    "code4": "C2e3",
    "Sous-sections niv4": "Anesthésiologie, réanimation"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2e",
    "SECTION niv3": "Médecine humaine",
    "code4": "C2e4",
    "Sous-sections niv4": "Cardiologie, maladies vasculaires"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2e",
    "SECTION niv3": "Médecine humaine",
    "code4": "C2e5",
    "Sous-sections niv4": "Chirurgie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2e",
    "SECTION niv3": "Médecine humaine",
    "code4": "C2e6",
    "Sous-sections niv4": "Dermatologie, biologie cutanée, régénération tissulaire, cicatrisation"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2e",
    "SECTION niv3": "Médecine humaine",
    "code4": "C2e7",
    "Sous-sections niv4": "Endocrinologie, métabolisme"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2e",
    "SECTION niv3": "Médecine humaine",
    "code4": "C2e8",
    "Sous-sections niv4": "Gastroentérologie, hépatologie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2e",
    "SECTION niv3": "Médecine humaine",
    "code4": "C2e9",
    "Sous-sections niv4": "Gériatrie, vieillissement"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2e",
    "SECTION niv3": "Médecine humaine",
    "code4": "C2e10",
    "Sous-sections niv4": "Gynécologie, obstétrique"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2e",
    "SECTION niv3": "Médecine humaine",
    "code4": "C2e11",
    "Sous-sections niv4": "Génétique médicale"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2e",
    "SECTION niv3": "Médecine humaine",
    "code4": "C2e12",
    "Sous-sections niv4": "Hématologie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2e",
    "SECTION niv3": "Médecine humaine",
    "code4": "C2e13",
    "Sous-sections niv4": "Médecine du travail"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2e",
    "SECTION niv3": "Médecine humaine",
    "code4": "C2e14",
    "Sous-sections niv4": "Médecine d'urgence"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2e",
    "SECTION niv3": "Médecine humaine",
    "code4": "C2e15",
    "Sous-sections niv4": "Médecine générale"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2e",
    "SECTION niv3": "Médecine humaine",
    "code4": "C2e16",
    "Sous-sections niv4": "Médecine nucléaire"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2e",
    "SECTION niv3": "Médecine humaine",
    "code4": "C2e17",
    "Sous-sections niv4": "Médecine palliative"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2e",
    "SECTION niv3": "Médecine humaine",
    "code4": "C2e18",
    "Sous-sections niv4": "Médecine physique et de réadaptation"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2e",
    "SECTION niv3": "Médecine humaine",
    "code4": "C2e19",
    "Sous-sections niv4": "Médecine préventive"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2e",
    "SECTION niv3": "Médecine humaine",
    "code4": "C2e20",
    "Sous-sections niv4": "Médecine du sport"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2e",
    "SECTION niv3": "Médecine humaine",
    "code4": "C2e21",
    "Sous-sections niv4": "Néonatologie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2e",
    "SECTION niv3": "Médecine humaine",
    "code4": "C2e22",
    "Sous-sections niv4": "Néphrologie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2e",
    "SECTION niv3": "Médecine humaine",
    "code4": "C2e23",
    "Sous-sections niv4": "Neurologie, neurosciences"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2e",
    "SECTION niv3": "Médecine humaine",
    "code4": "C2e24",
    "Sous-sections niv4": "Odontologie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2e",
    "SECTION niv3": "Médecine humaine",
    "code4": "C2e25",
    "Sous-sections niv4": "Oncologie, chimiothérapie, radiothérapie, immunothérapie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2e",
    "SECTION niv3": "Médecine humaine",
    "code4": "C2e26",
    "Sous-sections niv4": "Ophtalmologie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2e",
    "SECTION niv3": "Médecine humaine",
    "code4": "C2e27",
    "Sous-sections niv4": "Orthopédie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2e",
    "SECTION niv3": "Médecine humaine",
    "code4": "C2e28",
    "Sous-sections niv4": "Otorhinolaryngologie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2e",
    "SECTION niv3": "Médecine humaine",
    "code4": "C2e29",
    "Sous-sections niv4": "Pédiatrie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2e",
    "SECTION niv3": "Médecine humaine",
    "code4": "C2e30",
    "Sous-sections niv4": "Pneumologie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2e",
    "SECTION niv3": "Médecine humaine",
    "code4": "C2e31",
    "Sous-sections niv4": "Podologie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2e",
    "SECTION niv3": "Médecine humaine",
    "code4": "C2e32",
    "Sous-sections niv4": "Psychiatrie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2e",
    "SECTION niv3": "Médecine humaine",
    "code4": "C2e33",
    "Sous-sections niv4": "Radiologie, imagerie médicale"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2e",
    "SECTION niv3": "Médecine humaine",
    "code4": "C2e34",
    "Sous-sections niv4": "Rhumatologie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2e",
    "SECTION niv3": "Médecine humaine",
    "code4": "C2e35",
    "Sous-sections niv4": "Urologie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2f",
    "SECTION niv3": "Médecines alternatives et complémentaires",
    "code4": "C2f1",
    "Sous-sections niv4": "Phytothérapie, aromathérapie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2f",
    "SECTION niv3": "Médecines alternatives et complémentaires",
    "code4": "C2f2",
    "Sous-sections niv4": "Ostéopathie, réflexologie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2f",
    "SECTION niv3": "Médecines alternatives et complémentaires",
    "code4": "C2f3",
    "Sous-sections niv4": "Hypnose médicale, sophrologie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2f",
    "SECTION niv3": "Médecines alternatives et complémentaires",
    "code4": "C2f4",
    "Sous-sections niv4": "Acupuncture, homéopathie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2g",
    "SECTION niv3": "Santé publique",
    "code4": "C2g1",
    "Sous-sections niv4": "Hygiène"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2g",
    "SECTION niv3": "Santé publique",
    "code4": "C2g2",
    "Sous-sections niv4": "Lutte contre les maladies transmissibles"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2g",
    "SECTION niv3": "Santé publique",
    "code4": "C2g3",
    "Sous-sections niv4": "Epidémiologie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2g",
    "SECTION niv3": "Santé publique",
    "code4": "C2g4",
    "Sous-sections niv4": "Economie de la santé"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2h",
    "SECTION niv3": "Sciences du comportement",
    "code4": "C2h1",
    "Sous-sections niv4": "Sciences du comportement"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2h",
    "SECTION niv3": "Sciences du comportement",
    "code4": "C2h2",
    "Sous-sections niv4": "Thérapie cognitivocomportementale"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2i",
    "SECTION niv3": "Sport, bien être",
    "code4": "C2i1",
    "Sous-sections niv4": "Sport"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2i",
    "SECTION niv3": "Sport, bien être",
    "code4": "C2i2",
    "Sous-sections niv4": "Activité physique adaptée"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2i",
    "SECTION niv3": "Sport, bien être",
    "code4": "C2i3",
    "Sous-sections niv4": "Kinésithérapie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2i",
    "SECTION niv3": "Sport, bien être",
    "code4": "C2i4",
    "Sous-sections niv4": "Bien être"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2j",
    "SECTION niv3": "Médecine vétérinaire",
    "code4": "C2j1",
    "Sous-sections niv4": "Anatomie pathologique vétérinaire"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2j",
    "SECTION niv3": "Médecine vétérinaire",
    "code4": "C2j2",
    "Sous-sections niv4": "Anesthésie et analgésie vétérinaire"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2j",
    "SECTION niv3": "Médecine vétérinaire",
    "code4": "C2j3",
    "Sous-sections niv4": "Chirurgie des animaux de compagnie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2j",
    "SECTION niv3": "Médecine vétérinaire",
    "code4": "C2j4",
    "Sous-sections niv4": "Chirurgie équine"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2j",
    "SECTION niv3": "Médecine vétérinaire",
    "code4": "C2j5",
    "Sous-sections niv4": "Dermatologie vétérinaire"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2j",
    "SECTION niv3": "Médecine vétérinaire",
    "code4": "C2j6",
    "Sous-sections niv4": "Elevage et pathologie des équidés"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2j",
    "SECTION niv3": "Médecine vétérinaire",
    "code4": "C2j7",
    "Sous-sections niv4": "Gestion de la santé des bovins"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2j",
    "SECTION niv3": "Médecine vétérinaire",
    "code4": "C2j8",
    "Sous-sections niv4": "Gestion de la santé et de la qualité en productions avicoles et cunicoles"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2j",
    "SECTION niv3": "Médecine vétérinaire",
    "code4": "C2j9",
    "Sous-sections niv4": "Gestion de la santé et de la qualité en production laitière"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2j",
    "SECTION niv3": "Médecine vétérinaire",
    "code4": "C2j10",
    "Sous-sections niv4": "Imagerie médicale vétérinaire"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2j",
    "SECTION niv3": "Médecine vétérinaire",
    "code4": "C2j11",
    "Sous-sections niv4": "Médecine du comportement des animaux de compagnie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2j",
    "SECTION niv3": "Médecine vétérinaire",
    "code4": "C2j12",
    "Sous-sections niv4": "Médecine interne des animaux de compagnie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2j",
    "SECTION niv3": "Médecine vétérinaire",
    "code4": "C2j13",
    "Sous-sections niv4": "Médecine interne des équidés"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2j",
    "SECTION niv3": "Médecine vétérinaire",
    "code4": "C2j14",
    "Sous-sections niv4": "Médecine interne des grands animaux"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2j",
    "SECTION niv3": "Médecine vétérinaire",
    "code4": "C2j15",
    "Sous-sections niv4": "Microbiologie vétérinaire"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2j",
    "SECTION niv3": "Médecine vétérinaire",
    "code4": "C2j16",
    "Sous-sections niv4": "Neurologie vétérinaire"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2j",
    "SECTION niv3": "Médecine vétérinaire",
    "code4": "C2j17",
    "Sous-sections niv4": "Nutrition clinique vétérinaire"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2j",
    "SECTION niv3": "Médecine vétérinaire",
    "code4": "C2j18",
    "Sous-sections niv4": "Oncologie vétérinaire"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2j",
    "SECTION niv3": "Médecine vétérinaire",
    "code4": "C2j19",
    "Sous-sections niv4": "Ophtalmologie vétérinaire"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2j",
    "SECTION niv3": "Médecine vétérinaire",
    "code4": "C2j20",
    "Sous-sections niv4": "Pathologie clinique vétérinaire"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2j",
    "SECTION niv3": "Médecine vétérinaire",
    "code4": "C2j21",
    "Sous-sections niv4": "Reproduction animale"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2j",
    "SECTION niv3": "Médecine vétérinaire",
    "code4": "C2j22",
    "Sous-sections niv4": "Santé et productions animales en régions chaudes"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2j",
    "SECTION niv3": "Médecine vétérinaire",
    "code4": "C2j23",
    "Sous-sections niv4": "Santé publique vétérinaire, sciences des aliments"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2j",
    "SECTION niv3": "Médecine vétérinaire",
    "code4": "C2j24",
    "Sous-sections niv4": "Santé publique vétérinaire, médecine des populations"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2j",
    "SECTION niv3": "Médecine vétérinaire",
    "code4": "C2j25",
    "Sous-sections niv4": "Sciences et médecine des animaux de laboratoire"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C2",
    "Sous-domaines niv2": "Biologie humaine et animale",
    "code3": "C2j",
    "SECTION niv3": "Médecine vétérinaire",
    "code4": "C2j26",
    "Sous-sections niv4": "Stomatologie et dentisterie vétérinaires"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C3",
    "Sous-domaines niv2": "Pharmacie, Cosmétique",
    "code3": "C3a",
    "SECTION niv3": "Principes actifs",
    "code4": "C3a1",
    "Sous-sections niv4": "Synthèse de molécules"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C3",
    "Sous-domaines niv2": "Pharmacie, Cosmétique",
    "code3": "C3a",
    "SECTION niv3": "Principes actifs",
    "code4": "C3a2",
    "Sous-sections niv4": "Substances naturelles"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C3",
    "Sous-domaines niv2": "Pharmacie, Cosmétique",
    "code3": "C3a",
    "SECTION niv3": "Principes actifs",
    "code4": "C3a3",
    "Sous-sections niv4": "Molécules olfactives"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C3",
    "Sous-domaines niv2": "Pharmacie, Cosmétique",
    "code3": "C3a",
    "SECTION niv3": "Principes actifs",
    "code4": "C3a4",
    "Sous-sections niv4": "Criblage de molécules"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C3",
    "Sous-domaines niv2": "Pharmacie, Cosmétique",
    "code3": "C3a",
    "SECTION niv3": "Principes actifs",
    "code4": "C3a5",
    "Sous-sections niv4": "Identification de cibles"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C3",
    "Sous-domaines niv2": "Pharmacie, Cosmétique",
    "code3": "C3a",
    "SECTION niv3": "Principes actifs",
    "code4": "C3a6",
    "Sous-sections niv4": "Drug design"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C3",
    "Sous-domaines niv2": "Pharmacie, Cosmétique",
    "code3": "C3a",
    "SECTION niv3": "Principes actifs",
    "code4": "C3a7",
    "Sous-sections niv4": "Chimie extractive, purification de molécules"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C3",
    "Sous-domaines niv2": "Pharmacie, Cosmétique",
    "code3": "C3b",
    "SECTION niv3": "Délivrance de principes actifs",
    "code4": "C3b1",
    "Sous-sections niv4": "Pharmacodynamique"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C3",
    "Sous-domaines niv2": "Pharmacie, Cosmétique",
    "code3": "C3b",
    "SECTION niv3": "Délivrance de principes actifs",
    "code4": "C3b2",
    "Sous-sections niv4": "Pharmacocinétique [ADME]"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C3",
    "Sous-domaines niv2": "Pharmacie, Cosmétique",
    "code3": "C3b",
    "SECTION niv3": "Délivrance de principes actifs",
    "code4": "C3b3",
    "Sous-sections niv4": "Pharmacocinétique analytique"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C3",
    "Sous-domaines niv2": "Pharmacie, Cosmétique",
    "code3": "C3b",
    "SECTION niv3": "Délivrance de principes actifs",
    "code4": "C3b4",
    "Sous-sections niv4": "Toxicologie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C3",
    "Sous-domaines niv2": "Pharmacie, Cosmétique",
    "code3": "C3b",
    "SECTION niv3": "Délivrance de principes actifs",
    "code4": "C3b5",
    "Sous-sections niv4": "Pharmacogénétique"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C3",
    "Sous-domaines niv2": "Pharmacie, Cosmétique",
    "code3": "C3b",
    "SECTION niv3": "Délivrance de principes actifs",
    "code4": "C3b6",
    "Sous-sections niv4": "Galénique, formulation"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C3",
    "Sous-domaines niv2": "Pharmacie, Cosmétique",
    "code3": "C3b",
    "SECTION niv3": "Délivrance de principes actifs",
    "code4": "C3b7",
    "Sous-sections niv4": "Nanoparticules, micelles"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C3",
    "Sous-domaines niv2": "Pharmacie, Cosmétique",
    "code3": "C3b",
    "SECTION niv3": "Délivrance de principes actifs",
    "code4": "C3b8",
    "Sous-sections niv4": "Drug delivery"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C4",
    "Sous-domaines niv2": "Essais cliniques",
    "code3": "C4a",
    "SECTION niv3": "Essais cliniques",
    "code4": "C4a1",
    "Sous-sections niv4": "Essais cliniques"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C5",
    "Sous-domaines niv2": "Dispositifs médicaux, eSanté",
    "code3": "C5a",
    "SECTION niv3": "Dispositifs médicaux",
    "code4": "C5a1",
    "Sous-sections niv4": "Instruments"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C5",
    "Sous-domaines niv2": "Dispositifs médicaux, eSanté",
    "code3": "C5a",
    "SECTION niv3": "Dispositifs médicaux",
    "code4": "C5a2",
    "Sous-sections niv4": "Matières"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C5",
    "Sous-domaines niv2": "Dispositifs médicaux, eSanté",
    "code3": "C5a",
    "SECTION niv3": "Dispositifs médicaux",
    "code4": "C5a3",
    "Sous-sections niv4": "Appareils"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C5",
    "Sous-domaines niv2": "Dispositifs médicaux, eSanté",
    "code3": "C5a",
    "SECTION niv3": "Dispositifs médicaux",
    "code4": "C5a4",
    "Sous-sections niv4": "Equipements"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C5",
    "Sous-domaines niv2": "Dispositifs médicaux, eSanté",
    "code3": "C5a",
    "SECTION niv3": "Dispositifs médicaux",
    "code4": "C5a5",
    "Sous-sections niv4": "Implants, prothèses"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C5",
    "Sous-domaines niv2": "Dispositifs médicaux, eSanté",
    "code3": "C5a",
    "SECTION niv3": "Dispositifs médicaux",
    "code4": "C5a6",
    "Sous-sections niv4": "Logiciels"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C5",
    "Sous-domaines niv2": "Dispositifs médicaux, eSanté",
    "code3": "C5a",
    "SECTION niv3": "Dispositifs médicaux",
    "code4": "C5a7",
    "Sous-sections niv4": "Diagnostic in vitro"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C5",
    "Sous-domaines niv2": "Dispositifs médicaux, eSanté",
    "code3": "C5b",
    "SECTION niv3": "eSanté",
    "code4": "C5b1",
    "Sous-sections niv4": "eSanté"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C6",
    "Sous-domaines niv2": "Biotechnologie",
    "code3": "C6a",
    "SECTION niv3": "Biotechnologies vertes [agronomie]",
    "code4": "C6a1",
    "Sous-sections niv4": "Génie génétique, organismes génétiquement modifiés [OGM]"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C6",
    "Sous-domaines niv2": "Biotechnologie",
    "code3": "C6a",
    "SECTION niv3": "Biotechnologies vertes [agronomie]",
    "code4": "C6a2",
    "Sous-sections niv4": "Culture in vitro"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C6",
    "Sous-domaines niv2": "Biotechnologie",
    "code3": "C6a",
    "SECTION niv3": "Biotechnologies vertes [agronomie]",
    "code4": "C6a3",
    "Sous-sections niv4": "Biomasse, biocarburants"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C6",
    "Sous-domaines niv2": "Biotechnologie",
    "code3": "C6a",
    "SECTION niv3": "Biotechnologies vertes [agronomie]",
    "code4": "C6a4",
    "Sous-sections niv4": "Protection des cultures"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C6",
    "Sous-domaines niv2": "Biotechnologie",
    "code3": "C6a",
    "SECTION niv3": "Biotechnologies vertes [agronomie]",
    "code4": "C6a5",
    "Sous-sections niv4": "Amélioration des plantes"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C6",
    "Sous-domaines niv2": "Biotechnologie",
    "code3": "C6a",
    "SECTION niv3": "Biotechnologies vertes [agronomie]",
    "code4": "C6a6",
    "Sous-sections niv4": "Nouvelles molécules, extraction, purification"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C6",
    "Sous-domaines niv2": "Biotechnologie",
    "code3": "C6b",
    "SECTION niv3": "Biotechnologies rouges [médecine]",
    "code4": "C6b1",
    "Sous-sections niv4": "Diagnostic"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C6",
    "Sous-domaines niv2": "Biotechnologie",
    "code3": "C6b",
    "SECTION niv3": "Biotechnologies rouges [médecine]",
    "code4": "C6b2",
    "Sous-sections niv4": "Biocapteurs, biomarqueurs"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C6",
    "Sous-domaines niv2": "Biotechnologie",
    "code3": "C6b",
    "SECTION niv3": "Biotechnologies rouges [médecine]",
    "code4": "C6b3",
    "Sous-sections niv4": "Médecine personnalisée"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C6",
    "Sous-domaines niv2": "Biotechnologie",
    "code3": "C6b",
    "SECTION niv3": "Biotechnologies rouges [médecine]",
    "code4": "C6b4",
    "Sous-sections niv4": "Délivrance de principes actifs"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C6",
    "Sous-domaines niv2": "Biotechnologie",
    "code3": "C6b",
    "SECTION niv3": "Biotechnologies rouges [médecine]",
    "code4": "C6b5",
    "Sous-sections niv4": "Ingénierie de molécules thérapeutiques"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C6",
    "Sous-domaines niv2": "Biotechnologie",
    "code3": "C6b",
    "SECTION niv3": "Biotechnologies rouges [médecine]",
    "code4": "C6b6",
    "Sous-sections niv4": "Thérapie cellulaire, thérapie génique"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C6",
    "Sous-domaines niv2": "Biotechnologie",
    "code3": "C6b",
    "SECTION niv3": "Biotechnologies rouges [médecine]",
    "code4": "C6b7",
    "Sous-sections niv4": "Ingénierie tissulaire, substitut cutané"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C6",
    "Sous-domaines niv2": "Biotechnologie",
    "code3": "C6b",
    "SECTION niv3": "Biotechnologies rouges [médecine]",
    "code4": "C6b8",
    "Sous-sections niv4": "Organes, organes sur puces"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C6",
    "Sous-domaines niv2": "Biotechnologie",
    "code3": "C6b",
    "SECTION niv3": "Biotechnologies rouges [médecine]",
    "code4": "C6b9",
    "Sous-sections niv4": "Microfluidique"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C6",
    "Sous-domaines niv2": "Biotechnologie",
    "code3": "C6b",
    "SECTION niv3": "Biotechnologies rouges [médecine]",
    "code4": "C6b10",
    "Sous-sections niv4": "Vaccin, vaccinologie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C6",
    "Sous-domaines niv2": "Biotechnologie",
    "code3": "C6c",
    "SECTION niv3": "Biotechnologies blanches [industrie]",
    "code4": "C6c1",
    "Sous-sections niv4": "Génie génétique, organismes génétiquement modifiés [OGM]"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C6",
    "Sous-domaines niv2": "Biotechnologie",
    "code3": "C6c",
    "SECTION niv3": "Biotechnologies blanches [industrie]",
    "code4": "C6c2",
    "Sous-sections niv4": "Microbiologie industrielle, fermentation industrielle"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C6",
    "Sous-domaines niv2": "Biotechnologie",
    "code3": "C6c",
    "SECTION niv3": "Biotechnologies blanches [industrie]",
    "code4": "C6c3",
    "Sous-sections niv4": "Chimie verte"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C6",
    "Sous-domaines niv2": "Biotechnologie",
    "code3": "C6c",
    "SECTION niv3": "Biotechnologies blanches [industrie]",
    "code4": "C6c4",
    "Sous-sections niv4": "Biologie synthétique"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C6",
    "Sous-domaines niv2": "Biotechnologie",
    "code3": "C6c",
    "SECTION niv3": "Biotechnologies blanches [industrie]",
    "code4": "C6c5",
    "Sous-sections niv4": "Bioprocédés"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C6",
    "Sous-domaines niv2": "Biotechnologie",
    "code3": "C6c",
    "SECTION niv3": "Biotechnologies blanches [industrie]",
    "code4": "C6c6",
    "Sous-sections niv4": "Biocatalyse, enzyme"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C6",
    "Sous-domaines niv2": "Biotechnologie",
    "code3": "C6c",
    "SECTION niv3": "Biotechnologies blanches [industrie]",
    "code4": "C6c7",
    "Sous-sections niv4": "Biomatériaux, matériaux biosourcés"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C6",
    "Sous-domaines niv2": "Biotechnologie",
    "code3": "C6c",
    "SECTION niv3": "Biotechnologies blanches [industrie]",
    "code4": "C6c8",
    "Sous-sections niv4": "Bioénergie, biocarburant, méthanisation, conversion de biomasse"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C6",
    "Sous-domaines niv2": "Biotechnologie",
    "code3": "C6c",
    "SECTION niv3": "Biotechnologies blanches [industrie]",
    "code4": "C6c9",
    "Sous-sections niv4": "Valorisation des déchets"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C6",
    "Sous-domaines niv2": "Biotechnologie",
    "code3": "C6c",
    "SECTION niv3": "Biotechnologies blanches [industrie]",
    "code4": "C6c10",
    "Sous-sections niv4": "Chimie extractive, purification de molécules"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C6",
    "Sous-domaines niv2": "Biotechnologie",
    "code3": "C6c",
    "SECTION niv3": "Biotechnologies blanches [industrie]",
    "code4": "C6c11",
    "Sous-sections niv4": "Microfluidique"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C6",
    "Sous-domaines niv2": "Biotechnologie",
    "code3": "C6d",
    "SECTION niv3": "Biotechnologies jaunes [environnement]",
    "code4": "C6d1",
    "Sous-sections niv4": "Détection des polluants"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C6",
    "Sous-domaines niv2": "Biotechnologie",
    "code3": "C6d",
    "SECTION niv3": "Biotechnologies jaunes [environnement]",
    "code4": "C6d2",
    "Sous-sections niv4": "Dépollution des sols et des eaux, bioremédiation, agromine"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C6",
    "Sous-domaines niv2": "Biotechnologie",
    "code3": "C6d",
    "SECTION niv3": "Biotechnologies jaunes [environnement]",
    "code4": "C6d3",
    "Sous-sections niv4": "Traitement de l'eau et de l'air"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C6",
    "Sous-domaines niv2": "Biotechnologie",
    "code3": "C6e",
    "SECTION niv3": "Biotechnologies bleues [mer]",
    "code4": "C6e1",
    "Sous-sections niv4": "Génie génétique, organismes génétiquement modifiés [OGM]"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C6",
    "Sous-domaines niv2": "Biotechnologie",
    "code3": "C6e",
    "SECTION niv3": "Biotechnologies bleues [mer]",
    "code4": "C6e2",
    "Sous-sections niv4": "Algues"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C6",
    "Sous-domaines niv2": "Biotechnologie",
    "code3": "C6e",
    "SECTION niv3": "Biotechnologies bleues [mer]",
    "code4": "C6e3",
    "Sous-sections niv4": "Biodiversité marine"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C6",
    "Sous-domaines niv2": "Biotechnologie",
    "code3": "C6e",
    "SECTION niv3": "Biotechnologies bleues [mer]",
    "code4": "C6e4",
    "Sous-sections niv4": "Nouvelles molécules, extraction, purification"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C6",
    "Sous-domaines niv2": "Biotechnologie",
    "code3": "C6e",
    "SECTION niv3": "Biotechnologies bleues [mer]",
    "code4": "C6e5",
    "Sous-sections niv4": "Valorisation des algues, photobioréacteur"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7a",
    "SECTION niv3": "Production végétales",
    "code4": "C7a1",
    "Sous-sections niv4": "Systèmes de production"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7a",
    "SECTION niv3": "Production végétales",
    "code4": "C7a2",
    "Sous-sections niv4": "Semence"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7a",
    "SECTION niv3": "Production végétales",
    "code4": "C7a3",
    "Sous-sections niv4": "Grandes cultures"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7a",
    "SECTION niv3": "Production végétales",
    "code4": "C7a4",
    "Sous-sections niv4": "Viticulture, œnologie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7a",
    "SECTION niv3": "Production végétales",
    "code4": "C7a5",
    "Sous-sections niv4": "Culture de légumes, maraîchage"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7a",
    "SECTION niv3": "Production végétales",
    "code4": "C7a6",
    "Sous-sections niv4": "Arboriculture"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7a",
    "SECTION niv3": "Production végétales",
    "code4": "C7a7",
    "Sous-sections niv4": "Horticulture"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7a",
    "SECTION niv3": "Production végétales",
    "code4": "C7a8",
    "Sous-sections niv4": "Cultures tropicales"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7a",
    "SECTION niv3": "Production végétales",
    "code4": "C7a9",
    "Sous-sections niv4": "Sylviculture, agroforesterie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7a",
    "SECTION niv3": "Production végétales",
    "code4": "C7a10",
    "Sous-sections niv4": "Essais en champs"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7a",
    "SECTION niv3": "Production végétales",
    "code4": "C7a11",
    "Sous-sections niv4": "Agroécologie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7a",
    "SECTION niv3": "Production végétales",
    "code4": "C7a12",
    "Sous-sections niv4": "Sélection variétale, création variétale"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7b",
    "SECTION niv3": "Productions animales",
    "code4": "C7b1",
    "Sous-sections niv4": "Systèmes d'élevage"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7b",
    "SECTION niv3": "Productions animales",
    "code4": "C7b2",
    "Sous-sections niv4": "Elevage petits ruminants"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7b",
    "SECTION niv3": "Productions animales",
    "code4": "C7b3",
    "Sous-sections niv4": "Elevage gros ruminants"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7b",
    "SECTION niv3": "Productions animales",
    "code4": "C7b4",
    "Sous-sections niv4": "Elevage porcin"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7b",
    "SECTION niv3": "Productions animales",
    "code4": "C7b5",
    "Sous-sections niv4": "Production laitière"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7b",
    "SECTION niv3": "Productions animales",
    "code4": "C7b6",
    "Sous-sections niv4": "Aviculture"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7b",
    "SECTION niv3": "Productions animales",
    "code4": "C7b7",
    "Sous-sections niv4": "Elevage insectes"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7c",
    "SECTION niv3": "Produits de la mer",
    "code4": "C7c1",
    "Sous-sections niv4": "Pêche"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7c",
    "SECTION niv3": "Produits de la mer",
    "code4": "C7c2",
    "Sous-sections niv4": "Pisciculture"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7c",
    "SECTION niv3": "Produits de la mer",
    "code4": "C7c3",
    "Sous-sections niv4": "Conchyliculture, ostréiculture, mytiliculture, huitre, moule"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7c",
    "SECTION niv3": "Produits de la mer",
    "code4": "C7c4",
    "Sous-sections niv4": "Elevage des crustacés"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7c",
    "SECTION niv3": "Produits de la mer",
    "code4": "C7c5",
    "Sous-sections niv4": "Algoculture"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7d",
    "SECTION niv3": "Environnement",
    "code4": "C7d1",
    "Sous-sections niv4": "Zoologie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7d",
    "SECTION niv3": "Environnement",
    "code4": "C7d2",
    "Sous-sections niv4": "Botanique"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7d",
    "SECTION niv3": "Environnement",
    "code4": "C7d3",
    "Sous-sections niv4": "Océanologie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7d",
    "SECTION niv3": "Environnement",
    "code4": "C7d4",
    "Sous-sections niv4": "Evolution, écologie, biologie des populations"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7d",
    "SECTION niv3": "Environnement",
    "code4": "C7d5",
    "Sous-sections niv4": "Ecosystèmes, biodiversité"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7d",
    "SECTION niv3": "Environnement",
    "code4": "C7d6",
    "Sous-sections niv4": "Science des sols"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7d",
    "SECTION niv3": "Environnement",
    "code4": "C7d7",
    "Sous-sections niv4": "Limnologie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7d",
    "SECTION niv3": "Environnement",
    "code4": "C7d8",
    "Sous-sections niv4": "Paysage"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7d",
    "SECTION niv3": "Environnement",
    "code4": "C7d9",
    "Sous-sections niv4": "Télédétection"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7d",
    "SECTION niv3": "Environnement",
    "code4": "C7d10",
    "Sous-sections niv4": "Eau, gestion de l'eau, cycle de l'eau"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7d",
    "SECTION niv3": "Environnement",
    "code4": "C7d11",
    "Sous-sections niv4": "Evaluation environnementale, analyse du cycle de vie [ACV]"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7d",
    "SECTION niv3": "Environnement",
    "code4": "C7d12",
    "Sous-sections niv4": "Ecotoxicologie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7d",
    "SECTION niv3": "Environnement",
    "code4": "C7d13",
    "Sous-sections niv4": "Pollution, dépollution, détection des polluants, gestion des déchets"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7d",
    "SECTION niv3": "Environnement",
    "code4": "C7d14",
    "Sous-sections niv4": "Adaptation au changement climatique"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C7",
    "Sous-domaines niv2": "Agronomie, Environnement",
    "code3": "C7d",
    "SECTION niv3": "Environnement",
    "code4": "C7d15",
    "Sous-sections niv4": "Transition agroécologique, développement durable"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C8",
    "Sous-domaines niv2": "Alimentation humaine et animale",
    "code3": "C8a",
    "SECTION niv3": "Analyse des produits alimentaires",
    "code4": "C8a1",
    "Sous-sections niv4": "Chimie de l'alimentation"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C8",
    "Sous-domaines niv2": "Alimentation humaine et animale",
    "code3": "C8a",
    "SECTION niv3": "Analyse des produits alimentaires",
    "code4": "C8a2",
    "Sous-sections niv4": "Physicochimie de l'alimentation"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C8",
    "Sous-domaines niv2": "Alimentation humaine et animale",
    "code3": "C8b",
    "SECTION niv3": "Microbiologie de l'alimentation",
    "code4": "C8b1",
    "Sous-sections niv4": "Microbiote"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C8",
    "Sous-domaines niv2": "Alimentation humaine et animale",
    "code3": "C8b",
    "SECTION niv3": "Microbiologie de l'alimentation",
    "code4": "C8b2",
    "Sous-sections niv4": "Probiotiques"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C8",
    "Sous-domaines niv2": "Alimentation humaine et animale",
    "code3": "C8b",
    "SECTION niv3": "Microbiologie de l'alimentation",
    "code4": "C8b3",
    "Sous-sections niv4": "Ecologie microbienne des aliments"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C8",
    "Sous-domaines niv2": "Alimentation humaine et animale",
    "code3": "C8c",
    "SECTION niv3": "Procédés agroalimentaires",
    "code4": "C8c1",
    "Sous-sections niv4": "Transformations des matières, rhéologie"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C8",
    "Sous-domaines niv2": "Alimentation humaine et animale",
    "code3": "C8c",
    "SECTION niv3": "Procédés agroalimentaires",
    "code4": "C8c2",
    "Sous-sections niv4": "Valorisation des coproduits"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C8",
    "Sous-domaines niv2": "Alimentation humaine et animale",
    "code3": "C8c",
    "SECTION niv3": "Procédés agroalimentaires",
    "code4": "C8c3",
    "Sous-sections niv4": "Génie des procédés, technologies alimentaires"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C8",
    "Sous-domaines niv2": "Alimentation humaine et animale",
    "code3": "C8d",
    "SECTION niv3": "Contrôles qualité de l'alimentation",
    "code4": "C8d1",
    "Sous-sections niv4": "Contrôles qualité de l'alimentation"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C8",
    "Sous-domaines niv2": "Alimentation humaine et animale",
    "code3": "C8e",
    "SECTION niv3": "Analyse sensorielle",
    "code4": "C8e1",
    "Sous-sections niv4": "Analyse sensorielle"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C8",
    "Sous-domaines niv2": "Alimentation humaine et animale",
    "code3": "C8f",
    "SECTION niv3": "Sécurité alimentaire",
    "code4": "C8f1",
    "Sous-sections niv4": "Risque sanitaire"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C8",
    "Sous-domaines niv2": "Alimentation humaine et animale",
    "code3": "C8f",
    "SECTION niv3": "Sécurité alimentaire",
    "code4": "C8f2",
    "Sous-sections niv4": "Contrôle microbiologique"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C8",
    "Sous-domaines niv2": "Alimentation humaine et animale",
    "code3": "C8f",
    "SECTION niv3": "Sécurité alimentaire",
    "code4": "C8f3",
    "Sous-sections niv4": "Contrôle physicochimique"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C8",
    "Sous-domaines niv2": "Alimentation humaine et animale",
    "code3": "C8f",
    "SECTION niv3": "Sécurité alimentaire",
    "code4": "C8f4",
    "Sous-sections niv4": "Contrôle qualité"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C8",
    "Sous-domaines niv2": "Alimentation humaine et animale",
    "code3": "C8f",
    "SECTION niv3": "Sécurité alimentaire",
    "code4": "C8f5",
    "Sous-sections niv4": "Hygiène"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C8",
    "Sous-domaines niv2": "Alimentation humaine et animale",
    "code3": "C8f",
    "SECTION niv3": "Sécurité alimentaire",
    "code4": "C8f6",
    "Sous-sections niv4": "Procédés de conservation"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C8",
    "Sous-domaines niv2": "Alimentation humaine et animale",
    "code3": "C8g",
    "SECTION niv3": "Nutrition, diététique",
    "code4": "C8g1",
    "Sous-sections niv4": "Nutrition"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C8",
    "Sous-domaines niv2": "Alimentation humaine et animale",
    "code3": "C8g",
    "SECTION niv3": "Nutrition, diététique",
    "code4": "C8g2",
    "Sous-sections niv4": "Diététique"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C8",
    "Sous-domaines niv2": "Alimentation humaine et animale",
    "code3": "C8g",
    "SECTION niv3": "Nutrition, diététique",
    "code4": "C8g3",
    "Sous-sections niv4": "Compléments alimentaires"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C8",
    "Sous-domaines niv2": "Alimentation humaine et animale",
    "code3": "C8g",
    "SECTION niv3": "Nutrition, diététique",
    "code4": "C8g4",
    "Sous-sections niv4": "Nutrition du sportif"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C8",
    "Sous-domaines niv2": "Alimentation humaine et animale",
    "code3": "C8g",
    "SECTION niv3": "Nutrition, diététique",
    "code4": "C8g5",
    "Sous-sections niv4": "Composition de la ration, additifs alimentaires"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C8",
    "Sous-domaines niv2": "Alimentation humaine et animale",
    "code3": "C8g",
    "SECTION niv3": "Nutrition, diététique",
    "code4": "C8g6",
    "Sous-sections niv4": "Macronutriments"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C8",
    "Sous-domaines niv2": "Alimentation humaine et animale",
    "code3": "C8g",
    "SECTION niv3": "Nutrition, diététique",
    "code4": "C8g7",
    "Sous-sections niv4": "Micronutriments"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C8",
    "Sous-domaines niv2": "Alimentation humaine et animale",
    "code3": "C8h",
    "SECTION niv3": "Secteurs d'activité",
    "code4": "C8h1",
    "Sous-sections niv4": "Transformation et conservation de la viande et préparation de produits à base de viande"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C8",
    "Sous-domaines niv2": "Alimentation humaine et animale",
    "code3": "C8h",
    "SECTION niv3": "Secteurs d'activité",
    "code4": "C8h2",
    "Sous-sections niv4": "Transformation et conservation de poisson, de crustacés et de mollusques"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C8",
    "Sous-domaines niv2": "Alimentation humaine et animale",
    "code3": "C8h",
    "SECTION niv3": "Secteurs d'activité",
    "code4": "C8h3",
    "Sous-sections niv4": "Transformation et conservation de fruits et légumes"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C8",
    "Sous-domaines niv2": "Alimentation humaine et animale",
    "code3": "C8h",
    "SECTION niv3": "Secteurs d'activité",
    "code4": "C8h4",
    "Sous-sections niv4": "Fabrication d'huiles et graisses végétales et animales"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C8",
    "Sous-domaines niv2": "Alimentation humaine et animale",
    "code3": "C8h",
    "SECTION niv3": "Secteurs d'activité",
    "code4": "C8h5",
    "Sous-sections niv4": "Fabrication de produits laitiers"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C8",
    "Sous-domaines niv2": "Alimentation humaine et animale",
    "code3": "C8h",
    "SECTION niv3": "Secteurs d'activité",
    "code4": "C8h6",
    "Sous-sections niv4": "Travail des grains, fabrication de produits amylacés"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C8",
    "Sous-domaines niv2": "Alimentation humaine et animale",
    "code3": "C8h",
    "SECTION niv3": "Secteurs d'activité",
    "code4": "C8h7",
    "Sous-sections niv4": "Fabrication de produits de boulangerie / pâtisserie et de pâtes alimentaires"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C8",
    "Sous-domaines niv2": "Alimentation humaine et animale",
    "code3": "C8h",
    "SECTION niv3": "Secteurs d'activité",
    "code4": "C8h8",
    "Sous-sections niv4": "Fabrication d'autres produits alimentaires"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C8",
    "Sous-domaines niv2": "Alimentation humaine et animale",
    "code3": "C8h",
    "SECTION niv3": "Secteurs d'activité",
    "code4": "C8h9",
    "Sous-sections niv4": "Fabrication d'aliments pour animaux"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C8",
    "Sous-domaines niv2": "Alimentation humaine et animale",
    "code3": "C8h",
    "SECTION niv3": "Secteurs d'activité",
    "code4": "C8h10",
    "Sous-sections niv4": "Fabrication de boissons"
  },
  {
    "code1": "C",
    "DOMAINES niv1": "SCIENCES BIOLOGIQUES, MEDICALES ET AGROALIMENTAIRES",
    "code2": "C8",
    "Sous-domaines niv2": "Alimentation humaine et animale",
    "code3": "C8h",
    "SECTION niv3": "Secteurs d'activité",
    "code4": "C8h11",
    "Sous-sections niv4": "Fabrication de produits à base de tabac"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D1",
    "Sous-domaines niv2": "Droit, Sciences juridiques, Sciences politiques",
    "code3": "D1a",
    "SECTION niv3": "Droit privé et sciences criminelles",
    "code4": "D1a1",
    "Sous-sections niv4": "Droit bancaire et financier"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D1",
    "Sous-domaines niv2": "Droit, Sciences juridiques, Sciences politiques",
    "code3": "D1a",
    "SECTION niv3": "Droit privé et sciences criminelles",
    "code4": "D1a2",
    "Sous-sections niv4": "Droit de l'entreprise"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D1",
    "Sous-domaines niv2": "Droit, Sciences juridiques, Sciences politiques",
    "code3": "D1a",
    "SECTION niv3": "Droit privé et sciences criminelles",
    "code4": "D1a3",
    "Sous-sections niv4": "Droit privé"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D1",
    "Sous-domaines niv2": "Droit, Sciences juridiques, Sciences politiques",
    "code3": "D1a",
    "SECTION niv3": "Droit privé et sciences criminelles",
    "code4": "D1a4",
    "Sous-sections niv4": "Droit pénal et sciences criminelles"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D1",
    "Sous-domaines niv2": "Droit, Sciences juridiques, Sciences politiques",
    "code3": "D1b",
    "SECTION niv3": "Droit public",
    "code4": "D1b1",
    "Sous-sections niv4": "Droit administratif"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D1",
    "Sous-domaines niv2": "Droit, Sciences juridiques, Sciences politiques",
    "code3": "D1b",
    "SECTION niv3": "Droit public",
    "code4": "D1b2",
    "Sous-sections niv4": "Droit constitutionnel"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D1",
    "Sous-domaines niv2": "Droit, Sciences juridiques, Sciences politiques",
    "code3": "D1b",
    "SECTION niv3": "Droit public",
    "code4": "D1b3",
    "Sous-sections niv4": "Théorie du droit et histoire des idées"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D1",
    "Sous-domaines niv2": "Droit, Sciences juridiques, Sciences politiques",
    "code3": "D1b",
    "SECTION niv3": "Droit public",
    "code4": "D1b4",
    "Sous-sections niv4": "Droit international public et relations internationales"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D1",
    "Sous-domaines niv2": "Droit, Sciences juridiques, Sciences politiques",
    "code3": "D1b",
    "SECTION niv3": "Droit public",
    "code4": "D1b5",
    "Sous-sections niv4": "Droit communautaire et européen"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D1",
    "Sous-domaines niv2": "Droit, Sciences juridiques, Sciences politiques",
    "code3": "D1b",
    "SECTION niv3": "Droit public",
    "code4": "D1b6",
    "Sous-sections niv4": "Finances publiques et droit fiscal"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D1",
    "Sous-domaines niv2": "Droit, Sciences juridiques, Sciences politiques",
    "code3": "D1c",
    "SECTION niv3": "Histoire du droit et des institutions",
    "code4": "D1c1",
    "Sous-sections niv4": "Histoire du droit et des institutions"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D1",
    "Sous-domaines niv2": "Droit, Sciences juridiques, Sciences politiques",
    "code3": "D1d",
    "SECTION niv3": "Sciences politiques",
    "code4": "D1d1",
    "Sous-sections niv4": "Sciences politiques"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D2",
    "Sous-domaines niv2": "Sciences économiques",
    "code3": "D2a",
    "SECTION niv3": "Sciences économiques",
    "code4": "D2a1",
    "Sous-sections niv4": "Mathématique et méthodes quantitatives"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D2",
    "Sous-domaines niv2": "Sciences économiques",
    "code3": "D2a",
    "SECTION niv3": "Sciences économiques",
    "code4": "D2a2",
    "Sous-sections niv4": "Microéconomie"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D3",
    "Sous-domaines niv2": "Sciences économiques",
    "code3": "D2a",
    "SECTION niv3": "Sciences économiques",
    "code4": "D2a3",
    "Sous-sections niv4": "Economie comportementale"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D2",
    "Sous-domaines niv2": "Sciences économiques",
    "code3": "D2a",
    "SECTION niv3": "Sciences économiques",
    "code4": "D2a4",
    "Sous-sections niv4": "Macroéconomie"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D2",
    "Sous-domaines niv2": "Sciences économiques",
    "code3": "D2a",
    "SECTION niv3": "Sciences économiques",
    "code4": "D2a5",
    "Sous-sections niv4": "Economie financière"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D2",
    "Sous-domaines niv2": "Sciences économiques",
    "code3": "D2a",
    "SECTION niv3": "Sciences économiques",
    "code4": "D2a6",
    "Sous-sections niv4": "Economie publique"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D2",
    "Sous-domaines niv2": "Sciences économiques",
    "code3": "D2a",
    "SECTION niv3": "Sciences économiques",
    "code4": "D2a7",
    "Sous-sections niv4": "Organisation industrielle"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D2",
    "Sous-domaines niv2": "Sciences économiques",
    "code3": "D2a",
    "SECTION niv3": "Sciences économiques",
    "code4": "D2a8",
    "Sous-sections niv4": "Développement économique et Innovation"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D2",
    "Sous-domaines niv2": "Sciences économiques",
    "code3": "D2a",
    "SECTION niv3": "Sciences économiques",
    "code4": "D2a9",
    "Sous-sections niv4": "Systèmes économiques"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D2",
    "Sous-domaines niv2": "Sciences économiques",
    "code3": "D2a",
    "SECTION niv3": "Sciences économiques",
    "code4": "D2a10",
    "Sous-sections niv4": "Econométrie"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D2",
    "Sous-domaines niv2": "Sciences économiques",
    "code3": "D2a",
    "SECTION niv3": "Sciences économiques",
    "code4": "D2a11",
    "Sous-sections niv4": "Outils informatiques, outils mathématiques"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D3",
    "Sous-domaines niv2": "Sciences de gestion et du management",
    "code3": "D3a",
    "SECTION niv3": "Sciences de gestion et du management",
    "code4": "D3a1",
    "Sous-sections niv4": "Comptabilité, contrôle de gestion, Audit"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D3",
    "Sous-domaines niv2": "Sciences de gestion et du management",
    "code3": "D3a",
    "SECTION niv3": "Sciences de gestion et du management",
    "code4": "D3a2",
    "Sous-sections niv4": "Finance de marché"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D3",
    "Sous-domaines niv2": "Sciences de gestion et du management",
    "code3": "D3a",
    "SECTION niv3": "Sciences de gestion et du management",
    "code4": "D3a3",
    "Sous-sections niv4": "Finance d‘entreprise"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D3",
    "Sous-domaines niv2": "Sciences de gestion et du management",
    "code3": "D3a",
    "SECTION niv3": "Sciences de gestion et du management",
    "code4": "D3a4",
    "Sous-sections niv4": "Gestion des institutions financières [assurance, banque]"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D3",
    "Sous-domaines niv2": "Sciences de gestion et du management",
    "code3": "D3a",
    "SECTION niv3": "Sciences de gestion et du management",
    "code4": "D3a5",
    "Sous-sections niv4": "Gestion des risques, gestion d‘actifs"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D3",
    "Sous-domaines niv2": "Sciences de gestion et du management",
    "code3": "D3a",
    "SECTION niv3": "Sciences de gestion et du management",
    "code4": "D3a6",
    "Sous-sections niv4": "Gestion de production et logistique"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D3",
    "Sous-domaines niv2": "Sciences de gestion et du management",
    "code3": "D3a",
    "SECTION niv3": "Sciences de gestion et du management",
    "code4": "D3a7",
    "Sous-sections niv4": "Gestion des ressources humaines"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D3",
    "Sous-domaines niv2": "Sciences de gestion et du management",
    "code3": "D3a",
    "SECTION niv3": "Sciences de gestion et du management",
    "code4": "D3a8",
    "Sous-sections niv4": "Marketing, commerce et vente"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D3",
    "Sous-domaines niv2": "Sciences de gestion et du management",
    "code3": "D3a",
    "SECTION niv3": "Sciences de gestion et du management",
    "code4": "D3a9",
    "Sous-sections niv4": "Stratégie d'entreprise, innovation, changement"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D3",
    "Sous-domaines niv2": "Sciences de gestion et du management",
    "code3": "D3a",
    "SECTION niv3": "Sciences de gestion et du management",
    "code4": "D3a10",
    "Sous-sections niv4": "Systèmes d‘information de gestion, informatique de gestion"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D3",
    "Sous-domaines niv2": "Sciences de gestion et du management",
    "code3": "D3a",
    "SECTION niv3": "Sciences de gestion et du management",
    "code4": "D3a11",
    "Sous-sections niv4": "Théorie des organisations"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D3",
    "Sous-domaines niv2": "Sciences de gestion et du management",
    "code3": "D3a",
    "SECTION niv3": "Sciences de gestion et du management",
    "code4": "D3a12",
    "Sous-sections niv4": "Management, management international"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D3",
    "Sous-domaines niv2": "Sciences de gestion et du management",
    "code3": "D3a",
    "SECTION niv3": "Sciences de gestion et du management",
    "code4": "D3a13",
    "Sous-sections niv4": "Management de l'innovation"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D3",
    "Sous-domaines niv2": "Sciences de gestion et du management",
    "code3": "D3a",
    "SECTION niv3": "Sciences de gestion et du management",
    "code4": "D3a14",
    "Sous-sections niv4": "Responsabilité sociale et environnementale des entreprises et des organisations [RSE], [RSO]"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D3",
    "Sous-domaines niv2": "Sciences de gestion et du management",
    "code3": "D3a",
    "SECTION niv3": "Sciences de gestion et du management",
    "code4": "D3a15",
    "Sous-sections niv4": "Entrepreneuriat"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D3",
    "Sous-domaines niv2": "Sciences de gestion et du management",
    "code3": "D3a",
    "SECTION niv3": "Sciences de gestion et du management",
    "code4": "D3a16",
    "Sous-sections niv4": "Outils informatiques, outils mathématiques"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D4",
    "Sous-domaines niv2": "Littérature, Langues, Linguistique",
    "code3": "D4a",
    "SECTION niv3": "Littérature, langues, linguistique",
    "code4": "D4a1",
    "Sous-sections niv4": "Littérature, langues, linguistique"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D5",
    "Sous-domaines niv2": "Sciences de l'art, Histoire, Archéologie",
    "code3": "D5a",
    "SECTION niv3": "Sciences de l'art",
    "code4": "D5a1",
    "Sous-sections niv4": "Sciences et histoire de l'art"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D5",
    "Sous-domaines niv2": "Sciences de l'art, Histoire, Archéologie",
    "code3": "D5b",
    "SECTION niv3": "Histoire",
    "code4": "D5b1",
    "Sous-sections niv4": "Histoire"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D5",
    "Sous-domaines niv2": "Sciences de l'art, Histoire, Archéologie",
    "code3": "D5c",
    "SECTION niv3": "Archéologie",
    "code4": "D5c1",
    "Sous-sections niv4": "Archéologie"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D5",
    "Sous-domaines niv2": "Sciences de l'art, Histoire, Archéologie",
    "code3": "D5c",
    "SECTION niv3": "Archéologie",
    "code4": "D5c2",
    "Sous-sections niv4": "Archéométrie, géophysique"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D6",
    "Sous-domaines niv2": "Philosophie, Psychologie, Ergonomie",
    "code3": "D6a",
    "SECTION niv3": "Philosophie",
    "code4": "D6a1",
    "Sous-sections niv4": "Philosophie"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D6",
    "Sous-domaines niv2": "Philosophie, Psychologie, Ergonomie",
    "code3": "D6b",
    "SECTION niv3": "Psychologie",
    "code4": "D6b1",
    "Sous-sections niv4": "Psychologie"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D6",
    "Sous-domaines niv2": "Philosophie, Psychologie, Ergonomie",
    "code3": "D6b",
    "SECTION niv3": "Psychologie",
    "code4": "D6b2",
    "Sous-sections niv4": "Psychologie du travail et des organisations"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D6",
    "Sous-domaines niv2": "Philosophie, Psychologie, Ergonomie",
    "code3": "D6b",
    "SECTION niv3": "Psychologie",
    "code4": "D6b3",
    "Sous-sections niv4": "Psychométrie, évaluation, bilan de compétences"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D6",
    "Sous-domaines niv2": "Philosophie, Psychologie, Ergonomie",
    "code3": "D6b",
    "SECTION niv3": "Psychologie",
    "code4": "D6b4",
    "Sous-sections niv4": "Psychologie de l'éducation et du training professionnel"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D6",
    "Sous-domaines niv2": "Philosophie, Psychologie, Ergonomie",
    "code3": "D6b",
    "SECTION niv3": "Psychologie",
    "code4": "D6b5",
    "Sous-sections niv4": "Risques psychosociaux"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D6",
    "Sous-domaines niv2": "Philosophie, Psychologie, Ergonomie",
    "code3": "D6b",
    "SECTION niv3": "Psychologie",
    "code4": "D6b6",
    "Sous-sections niv4": "Neuropsychologie, psychologie cognitive, traitement de l'information"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D6",
    "Sous-domaines niv2": "Philosophie, Psychologie, Ergonomie",
    "code3": "D6c",
    "SECTION niv3": "Ergonomie",
    "code4": "D6c1",
    "Sous-sections niv4": "Modélisation cognitive, communication homme machine, interaction homme machine [IHM]"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D6",
    "Sous-domaines niv2": "Philosophie, Psychologie, Ergonomie",
    "code3": "D6c",
    "SECTION niv3": "Ergonomie",
    "code4": "D6c2",
    "Sous-sections niv4": "Aspects physiques et physiologiques du travail [biomécanique, postures, TMS, postes, espaces…]"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D6",
    "Sous-domaines niv2": "Philosophie, Psychologie, Ergonomie",
    "code3": "D6c",
    "SECTION niv3": "Ergonomie",
    "code4": "D6c3",
    "Sous-sections niv4": "Fonctionnement des organisations"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D6",
    "Sous-domaines niv2": "Philosophie, Psychologie, Ergonomie",
    "code3": "D6c",
    "SECTION niv3": "Ergonomie",
    "code4": "D6c4",
    "Sous-sections niv4": "Aménagement de l'espace"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D6",
    "Sous-domaines niv2": "Philosophie, Psychologie, Ergonomie",
    "code3": "D6c",
    "SECTION niv3": "Ergonomie",
    "code4": "D6c5",
    "Sous-sections niv4": "Sciences et techniques des activités physiques et sportives"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D6",
    "Sous-domaines niv2": "Philosophie, Psychologie, Ergonomie",
    "code3": "D6c",
    "SECTION niv3": "Ergonomie",
    "code4": "D6c6",
    "Sous-sections niv4": "Management du travail, des organisations, du facteur humain, travail de management"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D6",
    "Sous-domaines niv2": "Philosophie, Psychologie, Ergonomie",
    "code3": "D6d",
    "SECTION niv3": "Innovation sociale, sciences participatives",
    "code4": "D6d1",
    "Sous-sections niv4": "Innovation sociale, sciences participatives"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D7",
    "Sous-domaines niv2": "Sciences de l'éducation, Information et communication",
    "code3": "D7a",
    "SECTION niv3": "Sciences de l'éducation et de la formation",
    "code4": "D7a1",
    "Sous-sections niv4": "Processus d‘éducation et de formation, enseignement et apprentissages"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D7",
    "Sous-domaines niv2": "Sciences de l'éducation, Information et communication",
    "code3": "D7a",
    "SECTION niv3": "Sciences de l'éducation et de la formation",
    "code4": "D7a2",
    "Sous-sections niv4": "Dispositifs sociotechniques et usages des technologies"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D7",
    "Sous-domaines niv2": "Sciences de l'éducation, Information et communication",
    "code3": "D7a",
    "SECTION niv3": "Sciences de l'éducation et de la formation",
    "code4": "D7a3",
    "Sous-sections niv4": "Processus de professionnalisation et développement professionnel"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D7",
    "Sous-domaines niv2": "Sciences de l'éducation, Information et communication",
    "code3": "D7b",
    "SECTION niv3": "Information, communication",
    "code4": "D7b1",
    "Sous-sections niv4": "Information, documentation, organisation des connaissances"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D7",
    "Sous-domaines niv2": "Sciences de l'éducation, Information et communication",
    "code3": "D7b",
    "SECTION niv3": "Information, communication",
    "code4": "D7b2",
    "Sous-sections niv4": "Médias, journalisme"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D7",
    "Sous-domaines niv2": "Sciences de l'éducation, Information et communication",
    "code3": "D7b",
    "SECTION niv3": "Information, communication",
    "code4": "D7b3",
    "Sous-sections niv4": "Communication des organisations, publique et politique"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D7",
    "Sous-domaines niv2": "Sciences de l'éducation, Information et communication",
    "code3": "D7b",
    "SECTION niv3": "Information, communication",
    "code4": "D7b4",
    "Sous-sections niv4": "Industries culturelles, cinéma"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D7",
    "Sous-domaines niv2": "Sciences de l'éducation, Information et communication",
    "code3": "D7b",
    "SECTION niv3": "Information, communication",
    "code4": "D7b5",
    "Sous-sections niv4": "Design graphique, web design, design d'applications, communication visuelle des organisations"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D7",
    "Sous-domaines niv2": "Sciences de l'éducation, Information et communication",
    "code3": "D7b",
    "SECTION niv3": "Information, communication",
    "code4": "D7b6",
    "Sous-sections niv4": "Design produit ou d'objet"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D7",
    "Sous-domaines niv2": "Sciences de l'éducation, Information et communication",
    "code3": "D7b",
    "SECTION niv3": "Information, communication",
    "code4": "D7b7",
    "Sous-sections niv4": "Design social"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D7",
    "Sous-domaines niv2": "Sciences de l'éducation, Information et communication",
    "code3": "D7b",
    "SECTION niv3": "Information, communication",
    "code4": "D7b8",
    "Sous-sections niv4": "Médiations patrimoniale et des savoirs"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D8",
    "Sous-domaines niv2": "Sociologie, Démographie, Ethnologie, Anthropologie",
    "code3": "D8a",
    "SECTION niv3": "Sociologie, démographie",
    "code4": "D8a1",
    "Sous-sections niv4": "Sociologie"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D8",
    "Sous-domaines niv2": "Sociologie, Démographie, Ethnologie, Anthropologie",
    "code3": "D8a",
    "SECTION niv3": "Sociologie, démographie",
    "code4": "D8a2",
    "Sous-sections niv4": "Démographie"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D8",
    "Sous-domaines niv2": "Sociologie, Démographie, Ethnologie, Anthropologie",
    "code3": "D8a",
    "SECTION niv3": "Sociologie, démographie",
    "code4": "D8a3",
    "Sous-sections niv4": "Analyse socioenvironnementale"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D8",
    "Sous-domaines niv2": "Sociologie, Démographie, Ethnologie, Anthropologie",
    "code3": "D8b",
    "SECTION niv3": "Ethnologie, anthropologie",
    "code4": "D8b1",
    "Sous-sections niv4": "Ethnologie, ethnographie"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D8",
    "Sous-domaines niv2": "Sociologie, Démographie, Ethnologie, Anthropologie",
    "code3": "D8b",
    "SECTION niv3": "Ethnologie, anthropologie",
    "code4": "D8b2",
    "Sous-sections niv4": "Anthropologie sociale et culturelle, anthropologie biologique, préhistoire"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D9",
    "Sous-domaines niv2": "Géographie, Aménagement de l'espace",
    "code3": "D9a",
    "SECTION niv3": "Géographie",
    "code4": "D9a1",
    "Sous-sections niv4": "Politiques urbaines, politiques territoriales"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D9",
    "Sous-domaines niv2": "Géographie, Aménagement de l'espace",
    "code3": "D9a",
    "SECTION niv3": "Géographie",
    "code4": "D9a2",
    "Sous-sections niv4": "Climatologie"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D9",
    "Sous-domaines niv2": "Géographie, Aménagement de l'espace",
    "code3": "D9a",
    "SECTION niv3": "Géographie",
    "code4": "D9a3",
    "Sous-sections niv4": "Télédétection, analyse spatiale, localisation, géolocalisation"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D9",
    "Sous-domaines niv2": "Géographie, Aménagement de l'espace",
    "code3": "D9a",
    "SECTION niv3": "Géographie",
    "code4": "D9a4",
    "Sous-sections niv4": "Géomorphologie, géopolitique, hydrologie, littoral"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D9",
    "Sous-domaines niv2": "Géographie, Aménagement de l'espace",
    "code3": "D9a",
    "SECTION niv3": "Géographie",
    "code4": "D9a5",
    "Sous-sections niv4": "Désertification, développement"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D9",
    "Sous-domaines niv2": "Géographie, Aménagement de l'espace",
    "code3": "D9a",
    "SECTION niv3": "Géographie",
    "code4": "D9a6",
    "Sous-sections niv4": "Bilan carbone, neutralité carbone"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D9",
    "Sous-domaines niv2": "Géographie, Aménagement de l'espace",
    "code3": "D9b",
    "SECTION niv3": "Aménagement de l'espace",
    "code4": "D9b1",
    "Sous-sections niv4": "Aménagement de l‘espace, aménagement du territoire"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D9",
    "Sous-domaines niv2": "Géographie, Aménagement de l'espace",
    "code3": "D9b",
    "SECTION niv3": "Aménagement de l'espace",
    "code4": "D9b2",
    "Sous-sections niv4": "Environnement, habitat"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D9",
    "Sous-domaines niv2": "Géographie, Aménagement de l'espace",
    "code3": "D9b",
    "SECTION niv3": "Aménagement de l'espace",
    "code4": "D9b3",
    "Sous-sections niv4": "Analyse socioenvironnementale"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D10",
    "Sous-domaines niv2": "Urbanisme, Architecture, Environnement",
    "code3": "D10a",
    "SECTION niv3": "Urbanisme",
    "code4": "D10a1",
    "Sous-sections niv4": "Prospective territoriale, planification, ville"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D10",
    "Sous-domaines niv2": "Urbanisme, Architecture, Environnement",
    "code3": "D10a",
    "SECTION niv3": "Urbanisme",
    "code4": "D10a2",
    "Sous-sections niv4": "Urbanisme, programmation urbaine, projet urbain, projet de paysage"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D10",
    "Sous-domaines niv2": "Urbanisme, Architecture, Environnement",
    "code3": "D10b",
    "SECTION niv3": "Architecture",
    "code4": "D10b1",
    "Sous-sections niv4": "Construction, structure"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D10",
    "Sous-domaines niv2": "Urbanisme, Architecture, Environnement",
    "code3": "D10b",
    "SECTION niv3": "Architecture",
    "code4": "D10b2",
    "Sous-sections niv4": "Ecoconception, matériaux, matériaux biosourcés"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D10",
    "Sous-domaines niv2": "Urbanisme, Architecture, Environnement",
    "code3": "D10b",
    "SECTION niv3": "Architecture",
    "code4": "D10b3",
    "Sous-sections niv4": "Logement, équipements publics, bâtiments d'activités, espaces publics"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D10",
    "Sous-domaines niv2": "Urbanisme, Architecture, Environnement",
    "code3": "D10b",
    "SECTION niv3": "Architecture",
    "code4": "D10b4",
    "Sous-sections niv4": "Smart city"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D10",
    "Sous-domaines niv2": "Urbanisme, Architecture, Environnement",
    "code3": "D10b",
    "SECTION niv3": "Architecture",
    "code4": "D10b5",
    "Sous-sections niv4": "Histoire de l'architecture, patrimoine, réhabilitation"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D10",
    "Sous-domaines niv2": "Urbanisme, Architecture, Environnement",
    "code3": "D10b",
    "SECTION niv3": "Architecture",
    "code4": "D10b6",
    "Sous-sections niv4": "Numérique pour l'architecture, modélisation des informations du bâtiment [BIM]"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D10",
    "Sous-domaines niv2": "Urbanisme, Architecture, Environnement",
    "code3": "D10b",
    "SECTION niv3": "Architecture",
    "code4": "D10b7",
    "Sous-sections niv4": "Analyse du cycle de vie [ACV]"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D10",
    "Sous-domaines niv2": "Urbanisme, Architecture, Environnement",
    "code3": "D10b",
    "SECTION niv3": "Architecture",
    "code4": "D10b8",
    "Sous-sections niv4": "Bilan carbone, neutralité carbone"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D11",
    "Sous-domaines niv2": "Études pluridisciplinaires particulières sur un pays, Continent",
    "code3": "D11a",
    "SECTION niv3": "Études pluridisciplinaires particulières sur un pays, continent",
    "code4": "D11a1",
    "Sous-sections niv4": "Études pluridisciplinaires particulières sur un pays, continent"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D12",
    "Sous-domaines niv2": "Sciences et techniques des activités physiques et sportives",
    "code3": "D12a",
    "SECTION niv3": "Sciences et techniques des activités physiques et sportives",
    "code4": "D12a1",
    "Sous-sections niv4": "Psychologie des pratiques sportives, identité sociale, dynamique de groupe, performance, préparation mentale"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D12",
    "Sous-domaines niv2": "Sciences et techniques des activités physiques et sportives",
    "code3": "D12a",
    "SECTION niv3": "Sciences et techniques des activités physiques et sportives",
    "code4": "D12a2",
    "Sous-sections niv4": "Politique, transformations sociales"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D12",
    "Sous-domaines niv2": "Sciences et techniques des activités physiques et sportives",
    "code3": "D12a",
    "SECTION niv3": "Sciences et techniques des activités physiques et sportives",
    "code4": "D12a3",
    "Sous-sections niv4": "Equipements sportifs"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D12",
    "Sous-domaines niv2": "Sciences et techniques des activités physiques et sportives",
    "code3": "D12a",
    "SECTION niv3": "Sciences et techniques des activités physiques et sportives",
    "code4": "D12a4",
    "Sous-sections niv4": "Economie du sport"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D12",
    "Sous-domaines niv2": "Sciences et techniques des activités physiques et sportives",
    "code3": "D12a",
    "SECTION niv3": "Sciences et techniques des activités physiques et sportives",
    "code4": "D12a5",
    "Sous-sections niv4": "Handicap, réhabilitation motrice"
  },
  {
    "code1": "D",
    "DOMAINES niv1": "SCIENCES HUMAINES ET SOCIALES",
    "code2": "D13",
    "Sous-domaines niv2": "Modélisation, Simulation, Logiciels en SHS",
    "code3": "D13a",
    "SECTION niv3": "Modélisation, simulation, logiciels en SHS",
    "code4": "D13a1",
    "Sous-sections niv4": "Modélisation, simulation, logiciels en SHS"
  }
]


def _norm(text: Any) -> str:
    s = str(text or "").lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("’", "'")
    s = re.sub(r"[^a-z0-9+\-/.% ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _words(text: Any) -> List[str]:
    stop = {
        "the","and","for","with","without","from","into","study","review","analysis",
        "system","systems","method","methods","model","models","performance","technical",
        "uncertainty","solution","project","research","development","approach","using",
        "based","effect","effects","application","applications",
        "les","des","une","dans","avec","pour","sur","par","est","sont","etre","être",
        "projet","travaux","solution","solutions","verrou","incertitude","technique",
        "scientifique","documents","sources","maitrise","maîtrise","performance"
    }
    out = []
    for w in re.findall(r"[a-zA-Z][a-zA-Z0-9+\-/.%]{2,}", _norm(text)):
        if w and w not in stop and w not in out:
            out.append(w)
    return out


def _contains_any(text: str, terms: List[str]) -> bool:
    n = _norm(text)
    return any(_norm(t) in n for t in terms if t)


def _clean_query(q: str, max_chars: int = 220) -> str:
    q = re.sub(r"\s+", " ", str(q or "")).strip()
    q = re.sub(r"\b(technical uncertainty|question qualification|simple engineering)\b", " ", q, flags=re.I)
    q = re.sub(r"\s+", " ", q).strip(" ,.;:-")
    return q[:max_chars].strip()


# Profils génériques couvrant tous les domaines de la nomenclature CIR.
# Chaque profil contient des ancres de requêtes et des termes positifs/négatifs pour le ranking.
PROFILE_TEMPLATES: Dict[str, Dict[str, Any]] = {
    # A — numérique / mathématiques
    "automation_robotics_embedded": {
        "label": "Automatique, robotique, systèmes embarqués et productique",
        "query_seeds": [
            "control systems robust control nonlinear systems experimental validation",
            "embedded real-time system reliability sensor monitoring control",
            "robotics manipulation perception control industrial automation",
            "predictive control hybrid systems optimization production system",
        ],
        "positive_terms": ["control", "robot", "robotics", "automation", "embedded", "real-time", "sensor", "monitoring", "hybrid system", "predictive control", "production"],
        "negative_terms": ["building", "clinical", "genomics", "law"],
        "source_profiles": ["digital_engineering", "automation_robotics"],
    },
    "signal_image_vision": {
        "label": "Traitement du signal, image, vision, perception",
        "query_seeds": [
            "signal processing feature extraction classification detection robustness",
            "image processing computer vision segmentation detection deep learning",
            "sensor fusion signal analysis anomaly detection benchmark",
            "medical image processing if applicable segmentation classification validation",
        ],
        "positive_terms": ["signal", "image", "vision", "segmentation", "classification", "detection", "sensor fusion", "feature extraction", "perception"],
        "negative_terms": ["fire resistance", "timber", "pharmaceutical"],
        "source_profiles": ["digital_engineering", "computer_vision"],
    },
    "software_ai_data_cyber": {
        "label": "Informatique, IA, données, logiciels, cybersécurité",
        "query_seeds": [
            "machine learning robustness generalization dataset benchmark",
            "software architecture maintainability reliability empirical study",
            "cybersecurity vulnerability detection intrusion anomaly system",
            "data mining predictive model validation explainability",
            "algorithm optimization complexity scalable system evaluation",
        ],
        "positive_terms": ["algorithm", "software", "machine learning", "deep learning", "AI", "data", "dataset", "cybersecurity", "security", "architecture", "model", "benchmark"],
        "negative_terms": ["fire resistance", "mould growth", "timber concrete"],
        "source_profiles": ["digital_engineering", "cybersecurity", "ai_data"],
    },
    "mathematics_modeling_simulation": {
        "label": "Mathématiques, modélisation, simulation, optimisation",
        "query_seeds": [
            "mathematical modeling numerical simulation uncertainty quantification",
            "optimization algorithm convergence complexity robust formulation",
            "stochastic model statistical inference parameter estimation",
            "computational simulation validation sensitivity analysis",
        ],
        "positive_terms": ["mathematical", "modeling", "simulation", "optimization", "stochastic", "numerical", "finite element", "uncertainty quantification", "parameter estimation"],
        "negative_terms": ["clinical trial", "legal doctrine"],
        "source_profiles": ["mathematics", "simulation"],
    },

    # B — sciences industrielles / physique
    "electronics_telecom_networks": {
        "label": "Électronique, télécommunications, réseaux",
        "query_seeds": [
            "electronic circuit design reliability noise performance validation",
            "telecommunication network protocol latency throughput reliability",
            "RF antenna electromagnetic compatibility measurement",
            "microelectronics semiconductor device fabrication characterization",
        ],
        "positive_terms": ["electronic", "circuit", "telecommunication", "network", "RF", "antenna", "semiconductor", "microelectronics", "latency", "throughput"],
        "negative_terms": ["fungal", "clinical", "philosophy"],
        "source_profiles": ["electronics_telecom"],
    },
    "electrical_power_energy": {
        "label": "Génie électrique, puissance, réseaux électriques",
        "query_seeds": [
            "power electronics converter efficiency reliability thermal management",
            "electrical grid integration stability fault detection optimization",
            "electric motor control efficiency thermal losses",
            "battery management system state estimation safety",
        ],
        "positive_terms": ["power electronics", "converter", "electrical grid", "motor", "battery", "thermal management", "fault", "state estimation", "efficiency"],
        "negative_terms": ["clinical", "social"],
        "source_profiles": ["electrical_energy"],
    },
    "materials_metallurgy": {
        "label": "Matériaux, métallurgie, composites, surfaces",
        "query_seeds": [
            "material characterization mechanical properties durability microstructure",
            "composite material fatigue fracture thermal mechanical performance",
            "corrosion surface coating degradation resistance experimental study",
            "bio-based material hygrothermal mechanical durability when applicable",
        ],
        "positive_terms": ["material", "materials", "composite", "microstructure", "durability", "fatigue", "fracture", "corrosion", "coating", "surface", "polymer", "alloy"],
        "negative_terms": ["web service", "legal"],
        "source_profiles": ["materials_standards", "chemistry_materials"],
    },
    "mechanical_civil_engineering": {
        "label": "Mécanique, génie mécanique, génie civil",
        "query_seeds": [
            "mechanical system experimental validation fatigue vibration durability",
            "structural engineering load bearing capacity ductility cyclic loading",
            "civil engineering material structure performance finite element validation",
            "acoustic vibration mechanical behaviour building structure if applicable",
        ],
        "positive_terms": ["mechanical", "structure", "structural", "load", "ductility", "fatigue", "vibration", "cyclic loading", "finite element", "civil engineering", "acoustic"],
        "negative_terms": ["software architecture", "clinical trial", "genomics"],
        "source_profiles": ["mechanical_civil", "construction_if_applicable"],
    },
    "chemistry_process": {
        "label": "Chimie, formulation, procédés chimiques",
        "query_seeds": [
            "chemical formulation stability degradation reaction kinetics",
            "catalysis process optimization selectivity yield experimental validation",
            "polymer formulation rheology thermal mechanical properties",
            "analytical chemistry method validation detection limit accuracy",
        ],
        "positive_terms": ["chemistry", "chemical", "formulation", "catalysis", "reaction", "kinetics", "polymer", "rheology", "stability", "selectivity", "yield"],
        "negative_terms": ["architecture", "sociology"],
        "source_profiles": ["chemistry_materials", "process_engineering"],
    },
    "physics_instrumentation": {
        "label": "Physique, instrumentation, mesure",
        "query_seeds": [
            "physical measurement instrumentation uncertainty calibration",
            "optical sensor measurement noise sensitivity experimental validation",
            "thermal physical properties characterization model validation",
            "plasma laser spectroscopy material interaction",
        ],
        "positive_terms": ["physics", "measurement", "instrumentation", "sensor", "calibration", "optical", "laser", "spectroscopy", "thermal properties", "uncertainty"],
        "negative_terms": ["business", "legal"],
        "source_profiles": ["physics_instrumentation"],
    },
    "energy_process_environment": {
        "label": "Énergétique, génie des procédés, environnement industriel",
        "query_seeds": [
            "energy system efficiency heat transfer process optimization",
            "thermal process modeling experimental validation heat exchanger",
            "renewable energy integration storage efficiency lifecycle",
            "process engineering mass transfer fluid flow optimization",
        ],
        "positive_terms": ["energy", "thermal", "heat transfer", "process", "fluid flow", "mass transfer", "heat exchanger", "renewable", "storage", "efficiency"],
        "negative_terms": ["clinical", "legal"],
        "source_profiles": ["energy_process", "environment"],
    },
    "earth_ocean_atmosphere": {
        "label": "Océan, atmosphère, terre, géosciences",
        "query_seeds": [
            "geoscience environmental monitoring modeling uncertainty",
            "atmospheric model climate data assimilation validation",
            "hydrology soil water transfer erosion modeling",
            "oceanography observation remote sensing numerical model",
        ],
        "positive_terms": ["geoscience", "atmospheric", "climate", "hydrology", "soil", "ocean", "remote sensing", "environmental monitoring", "erosion"],
        "negative_terms": ["clinical trial", "software architecture"],
        "source_profiles": ["earth_environment"],
    },

    # C — bio / santé / agro
    "cell_molecular_biology": {
        "label": "Biologie cellulaire, moléculaire, génomique",
        "query_seeds": [
            "cell biology molecular mechanism experimental validation",
            "gene expression omics biomarker detection pathway analysis",
            "protein enzyme activity assay characterization",
            "cell culture phenotype quantification microscopy analysis",
        ],
        "positive_terms": ["cell", "molecular", "gene", "protein", "enzyme", "biomarker", "pathway", "omics", "culture", "phenotype"],
        "negative_terms": ["concrete", "timber", "legal"],
        "source_profiles": ["biomedical_literature"],
    },
    "human_animal_biology": {
        "label": "Biologie humaine et animale, physiologie",
        "query_seeds": [
            "physiology mechanism biomarker animal model experimental study",
            "human biology pathological mechanism diagnostic marker",
            "animal health disease model immune response validation",
            "toxicology biological response dose effect",
        ],
        "positive_terms": ["physiology", "biomarker", "animal model", "human biology", "disease", "immune", "toxicology", "dose", "pathology"],
        "negative_terms": ["timber", "software"],
        "source_profiles": ["biomedical_literature", "toxicology"],
    },
    "pharma_cosmetics": {
        "label": "Pharmacie, cosmétique, galénique, formulation",
        "query_seeds": [
            "pharmaceutical formulation stability bioavailability dissolution",
            "drug delivery system release kinetics formulation optimization",
            "cosmetic formulation skin compatibility stability efficacy",
            "analytical method validation pharmaceutical impurity stability",
        ],
        "positive_terms": ["pharmaceutical", "drug", "formulation", "stability", "bioavailability", "dissolution", "delivery", "cosmetic", "skin", "efficacy"],
        "negative_terms": ["timber", "civil engineering"],
        "source_profiles": ["pharma_regulatory", "biomedical_literature"],
    },
    "clinical_trials": {
        "label": "Essais cliniques, épidémiologie, santé publique",
        "query_seeds": [
            "clinical trial efficacy safety endpoint randomized study",
            "diagnostic performance sensitivity specificity clinical validation",
            "epidemiological cohort risk factor outcome model",
            "real world evidence clinical effectiveness safety",
        ],
        "positive_terms": ["clinical", "trial", "efficacy", "safety", "endpoint", "randomized", "diagnostic performance", "sensitivity", "specificity", "cohort"],
        "negative_terms": ["timber", "concrete", "algorithm complexity"],
        "source_profiles": ["clinical_regulatory", "biomedical_literature"],
    },
    "medical_devices_ehealth": {
        "label": "Dispositifs médicaux, eSanté, imagerie médicale",
        "query_seeds": [
            "medical device performance validation safety usability clinical evaluation",
            "digital health algorithm clinical validation medical device",
            "medical imaging diagnostic accuracy segmentation validation",
            "biocompatibility risk management medical device standard",
        ],
        "positive_terms": ["medical device", "digital health", "clinical validation", "usability", "safety", "imaging", "diagnostic accuracy", "biocompatibility"],
        "negative_terms": ["timber", "building"],
        "source_profiles": ["medical_device_regulatory", "biomedical_literature", "digital_health"],
    },
    "biotechnology": {
        "label": "Biotechnologie, fermentation, bioprocédés",
        "query_seeds": [
            "bioprocess fermentation optimization yield scale-up",
            "biotechnology enzyme production strain improvement",
            "cell culture bioreactor process control metabolite",
            "biomass conversion microbial process validation",
        ],
        "positive_terms": ["bioprocess", "fermentation", "enzyme", "strain", "bioreactor", "microbial", "biomass", "metabolite", "scale-up"],
        "negative_terms": ["timber", "legal"],
        "source_profiles": ["biotechnology", "biomedical_literature"],
    },
    "agronomy_environment": {
        "label": "Agronomie, environnement, végétal, sol",
        "query_seeds": [
            "agronomy crop yield stress resilience field trial",
            "soil microbiome nutrient water stress plant growth",
            "environmental impact agricultural practice lifecycle assessment",
            "plant disease detection resistance phenotype validation",
        ],
        "positive_terms": ["agronomy", "crop", "soil", "plant", "field trial", "yield", "stress", "nutrient", "microbiome", "lifecycle"],
        "negative_terms": ["software architecture", "concrete"],
        "source_profiles": ["agriculture_environment", "food_agri"],
    },
    "food_feed": {
        "label": "Alimentation humaine et animale, procédés alimentaires",
        "query_seeds": [
            "food processing formulation stability sensory quality shelf life",
            "food safety microbiological risk process validation",
            "animal feed formulation digestibility performance health",
            "nutritional quality bioactive compounds food matrix",
        ],
        "positive_terms": ["food", "feed", "nutrition", "shelf life", "sensory", "digestibility", "microbiological", "food safety", "bioactive"],
        "negative_terms": ["timber", "software"],
        "source_profiles": ["food_safety", "agriculture_environment"],
    },

    # D — SHS
    "law_policy_regulation": {
        "label": "Droit, sciences politiques, politiques publiques",
        "query_seeds": [
            "legal framework regulatory compliance policy analysis comparative law",
            "public policy evaluation governance implementation empirical study",
            "data protection regulation risk compliance methodology",
            "intellectual property innovation legal uncertainty",
        ],
        "positive_terms": ["law", "legal", "regulatory", "policy", "governance", "compliance", "public policy", "data protection", "intellectual property"],
        "negative_terms": ["clinical trial", "fire resistance"],
        "source_profiles": ["law_policy"],
    },
    "economics_management": {
        "label": "Économie, gestion, management, innovation",
        "query_seeds": [
            "economic model innovation adoption productivity empirical analysis",
            "management process organizational performance case study",
            "business model innovation value creation empirical study",
            "operations management optimization performance indicator",
        ],
        "positive_terms": ["economic", "management", "business model", "innovation", "adoption", "productivity", "organization", "operations", "value creation"],
        "negative_terms": ["mould growth", "seismic loading"],
        "source_profiles": ["economics_management"],
    },
    "language_arts_humanities": {
        "label": "Littérature, langues, arts, histoire, archéologie, philosophie",
        "query_seeds": [
            "linguistic analysis corpus methodology language processing",
            "heritage conservation material analysis historical study",
            "art history digital humanities methodology corpus",
            "archaeology dating material characterization site analysis",
        ],
        "positive_terms": ["language", "linguistic", "corpus", "heritage", "conservation", "history", "art", "archaeology", "digital humanities", "philosophy"],
        "negative_terms": ["clinical", "connector"],
        "source_profiles": ["humanities"],
    },
    "psychology_ergonomics_education_info": {
        "label": "Psychologie, ergonomie, éducation, information-communication",
        "query_seeds": [
            "user experience ergonomics cognitive workload usability study",
            "educational intervention learning outcome evaluation",
            "information communication behavior adoption empirical study",
            "human factors design usability accessibility validation",
        ],
        "positive_terms": ["psychology", "ergonomics", "usability", "education", "learning", "communication", "human factors", "accessibility", "cognitive"],
        "negative_terms": ["timber", "pharmaceutical"],
        "source_profiles": ["social_sciences", "human_factors"],
    },
    "sociology_geography_urbanism": {
        "label": "Sociologie, géographie, urbanisme, architecture, environnement",
        "query_seeds": [
            "urban planning environmental performance building design evaluation",
            "architecture construction environmental assessment user comfort",
            "sociology adoption practice field study qualitative analysis",
            "geography spatial analysis land use planning resilience",
        ],
        "positive_terms": ["urban", "architecture", "building", "environmental", "planning", "spatial", "sociology", "field study", "land use", "resilience"],
        "negative_terms": ["clinical trial", "semiconductor"],
        "source_profiles": ["urban_environment", "social_sciences"],
    },
    "sports_science": {
        "label": "Sciences et techniques des activités physiques et sportives",
        "query_seeds": [
            "sports science biomechanics performance injury prevention",
            "exercise physiology training load recovery adaptation",
            "movement analysis wearable sensor athlete performance",
            "sport equipment biomechanics safety validation",
        ],
        "positive_terms": ["sports", "exercise", "biomechanics", "training", "athlete", "movement", "wearable", "injury", "performance"],
        "negative_terms": ["timber", "legal"],
        "source_profiles": ["sports_science"],
    },
}


# Mapping complet niveau 2 -> profil générique.
CODE2_TO_PROFILE = {
    "A1": "automation_robotics_embedded",
    "A2": "signal_image_vision",
    "A3": "software_ai_data_cyber",
    "A4": "mathematics_modeling_simulation",
    "B1": "electronics_telecom_networks",
    "B2": "electrical_power_energy",
    "B3": "materials_metallurgy",
    "B4": "mechanical_civil_engineering",
    "B5": "chemistry_process",
    "B6": "physics_instrumentation",
    "B7": "energy_process_environment",
    "B8": "earth_ocean_atmosphere",
    "C1": "cell_molecular_biology",
    "C2": "human_animal_biology",
    "C3": "pharma_cosmetics",
    "C4": "clinical_trials",
    "C5": "medical_devices_ehealth",
    "C6": "biotechnology",
    "C7": "agronomy_environment",
    "C8": "food_feed",
    "D1": "law_policy_regulation",
    "D2": "economics_management",
    "D3": "economics_management",
    "D4": "language_arts_humanities",
    "D5": "language_arts_humanities",
    "D6": "psychology_ergonomics_education_info",
    "D7": "psychology_ergonomics_education_info",
    "D8": "sociology_geography_urbanism",
    "D9": "sociology_geography_urbanism",
    "D10": "sociology_geography_urbanism",
    "D11": "sociology_geography_urbanism",
    "D12": "sports_science",
    "D13": "mathematics_modeling_simulation",
}


# Sous-profils plus précis, détectés par le texte du verrou.
SPECIALIZED_TEXT_PROFILES: List[Tuple[str, List[str]]] = [
    # V131 : l'ordre et les termes sont stricts pour éviter les mauvais profils.
    # On ne déclenche plus "feu" seulement parce que le texte contient paille/chanvre/biosourcé.
    ("bio_based_thermal_inertia", ["effusiv", "diffusiv", "dephas", "déphas", "inertie", "thermal inertia", "thermal mass", "thermal effusivity", "thermal diffusivity", "phase shift", "summer comfort", "confort d ete", "confort ete", "overheating"]),
    ("timber_concrete_seismic_connectors", ["connecteur", "connecteurs", "connectors", "goujon", "goujons", "timber concrete", "bois beton", "bois/beton", "shear connector", "seismic", "seisme", "séisme", "ductility", "ductilite", "ductilité", "cyclic"]),
    ("bio_based_fire_resistance", ["rei", "rei 60", "feu", "incendie", "fire resistance", "fire rating", "reaction to fire", "resistance au feu", "résistance au feu", "charring", "smouldering"]),
    ("bio_based_hygro_fungal_moisture", ["fongique", "fungal", "moisiss", "mould", "mold", "hygro", "humid", "moisture", "perspir", "vapour", "vapor", "condensation", "wufi", "water vapour", "vapor barrier", "pare-vapeur"]),
    ("loose_fill_biobased_insulation_settlement", ["tassement", "settlement", "settling", "compaction", "compactage", "loose fill", "loose-fill", "blown insulation", "chenevotte", "chènevotte", "paille hachee", "paille hachée", "density"]),
    ("building_multiphysics_comfort", ["acoust", "vibrat", "multi physique", "multiphysics", "thermal acoustic", "confort", "inconfort", "degres heures", "degrés-heures"]),
]

# Les profils spécialisés héritent de profils génériques.
PROFILE_TEMPLATES.update({
    "bio_based_thermal_inertia": {
        "label": "Bâtiment biosourcé — inertie, diffusivité, effusivité, confort d'été",
        "query_seeds": [
            "bio-based building materials thermal inertia thermal diffusivity effusivity",
            "hemp concrete thermal effusivity thermal diffusivity summer comfort",
            "straw insulation timber frame wall thermal inertia hygrothermal performance",
            "lightweight timber frame wall summer comfort phase shift bio-based insulation",
            "earth hemp composite thermal inertia building envelope hygrothermal",
            "bio-based wall thermal mass overheating summer comfort",
        ],
        "positive_terms": ["bio-based", "biobased", "biosourced", "hemp", "straw", "thermal inertia", "thermal diffusivity", "thermal effusivity", "phase shift", "summer comfort", "hygrothermal", "wall"],
        "negative_terms": ["technical debt", "software architecture", "photon", "discord", "web service"],
        "source_profiles": ["building_biobased", "construction_if_applicable"],
    },
    "bio_based_fire_resistance": {
        "label": "Bâtiment biosourcé — feu / REI / parois bois ou biosourcées",
        "query_seeds": [
            "fire resistance timber frame wall bio-based insulation",
            "bio-based infill materials fire resistance timber wall assemblies",
            "straw bale wall fire resistance building insulation",
            "hemp concrete fire resistance building wall",
            "bio-based insulation reaction to fire building materials",
            "timber wall fire resistance bio-based materials REI",
        ],
        "positive_terms": ["fire resistance", "reaction to fire", "timber", "wood", "wall", "bio-based", "straw", "hemp", "insulation", "REI", "building"],
        "negative_terms": ["photon isolation", "software", "translation quality", "diphoton"],
        "source_profiles": ["building_biobased", "fire_safety", "construction_if_applicable"],
    },
    "bio_based_hygro_fungal_moisture": {
        "label": "Bâtiment biosourcé — hygrothermie, humidité, moisissures",
        "query_seeds": [
            "hygrothermal behaviour bio-based insulation mould growth",
            "fungal growth risk straw insulation timber frame wall moisture",
            "hemp concrete hygrothermal moisture transfer mould growth",
            "WUFI bio-based wall mould growth hygrothermal",
            "long term durability bio-based insulation moisture fungal growth",
            "vapour open timber frame wall bio-based insulation mould risk",
        ],
        "positive_terms": ["hygrothermal", "moisture", "mould", "mold", "fungal", "bio-based", "straw", "hemp", "timber frame", "vapour", "condensation", "wall"],
        "negative_terms": ["web service", "smart service", "sinusitis", "software-defined"],
        "source_profiles": ["building_biobased", "construction_if_applicable"],
    },
    "loose_fill_biobased_insulation_settlement": {
        "label": "Bâtiment biosourcé — isolants en vrac / tassement",
        "query_seeds": [
            "settlement loose-fill bio-based insulation straw wall density",
            "blown straw insulation settlement density vertical wall durability",
            "hemp shiv loose fill insulation settlement vertical timber frame wall",
            "loose-fill insulation settlement thermal performance building envelope",
            "straw hemp blown insulation compaction density durability wall",
            "measured settlement loose fill insulation timber frame wall",
        ],
        "positive_terms": ["settlement", "loose-fill", "loose fill", "blown insulation", "density", "straw", "hemp", "wall", "timber frame", "insulation", "cavity"],
        "negative_terms": ["traffic flow", "virtual network", "software"],
        "source_profiles": ["building_biobased", "construction_if_applicable"],
    },
    "timber_concrete_seismic_connectors": {
        "label": "Bois-béton — connecteurs, ductilité, séisme/vent",
        "query_seeds": [
            "timber concrete composite shear connector ductility seismic loading",
            "seismic performance connectors timber-concrete composite structures",
            "timber-concrete composite floor shear connectors cyclic loading",
            "wood concrete composite diaphragm shear connection earthquake",
            "CLT concrete composite connector ductility multi-storey buildings",
            "timber concrete composite slabs shear connector experimental ductility",
        ],
        "positive_terms": ["timber concrete", "wood concrete", "composite", "shear connector", "connector", "ductility", "seismic", "cyclic loading", "diaphragm", "CLT", "floor"],
        "negative_terms": ["electrical connector", "traffic", "discord", "software"],
        "source_profiles": ["mechanical_civil", "construction_if_applicable"],
    },
    "building_multiphysics_comfort": {
        "label": "Bâtiment — confort thermique/acoustique/vibratoire",
        "query_seeds": [
            "bio-based building envelope thermal acoustic performance summer comfort",
            "timber frame building thermal acoustic vibration comfort",
            "lightweight timber building vibration acoustic thermal comfort",
            "building envelope multiphysics thermal acoustic hygrothermal performance",
        ],
        "positive_terms": ["thermal", "acoustic", "vibration", "comfort", "building envelope", "bio-based", "timber", "hygrothermal", "summer"],
        "negative_terms": ["discord", "web service", "clinical"],
        "source_profiles": ["construction_if_applicable", "building_biobased"],
    },
})


def _find_domain_rows_by_code(code: str) -> List[Dict[str, str]]:
    code = str(code or "").strip()
    if not code:
        return []
    out = []
    for r in CIR_NOMENCLATURE_ROWS:
        if code in {r.get("code1"), r.get("code2"), r.get("code3"), r.get("code4")}:
            out.append(r)
    return out


def _codes_from_domain_detection(domain_detection: Dict[str, Any]) -> Tuple[str, str, str, str]:
    d = domain_detection or {}
    c1 = str(d.get("broad_domain_code") or d.get("domain_code_niv1") or "").strip()
    c2 = str(d.get("main_domain_code") or d.get("domain_code_niv2") or "").strip()
    c3 = str(d.get("sub_domain_code") or d.get("domain_code_niv3") or "").strip()
    c4 = str(d.get("domain_code_niv4") or "").strip()
    return c1, c2, c3, c4


def _profile_from_code(code2: str, code3: str, code4: str) -> str:
    # Spécificités de quelques sections qui recouvrent plusieurs profils.
    c3 = str(code3 or "")
    c4 = str(code4 or "")
    if c3 in {"A3h"}:
        return "software_ai_data_cyber"
    if c3 in {"A3n"}:
        return "software_ai_data_cyber"
    if c3 in {"A2b", "A3i"}:
        return "signal_image_vision"
    if c3 in {"B4g"}:
        return "mechanical_civil_engineering"
    if c3 in {"D10b"}:
        return "sociology_geography_urbanism"
    if c4.startswith("D13"):
        return "mathematics_modeling_simulation"
    return CODE2_TO_PROFILE.get(str(code2 or "").strip(), "generic")


def _has_word_or_phrase(n: str, term: str) -> bool:
    """Match propre : évite par exemple que REI matche reinforced."""
    t = _norm(term)
    if not t:
        return False
    # Pour les mots courts/ambigus, on impose une frontière de mot.
    if len(t) <= 3 and " " not in t:
        return bool(re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", n))
    return t in n


def _contains_terms(n: str, terms: List[str]) -> bool:
    return any(_has_word_or_phrase(n, t) for t in terms)


def _profile_from_text(text: str, fallback: str = "generic") -> str:
    n = _norm(text)

    # V131 : règles métier explicites, plus fiables que le simple overlap domaine global.
    # 1) Inertie/diffusivité/effusivité : prioritaire si le verrou le mentionne.
    if _contains_terms(n, ["effusiv", "diffusiv", "dephas", "dephasage", "déphasage", "inertie", "thermal inertia", "thermal effusivity", "thermal diffusivity", "phase shift", "summer comfort"]):
        return "bio_based_thermal_inertia"

    # 2) Connecteurs bois/béton : il faut un terme connecteur + un indice structure/séisme/bois-béton.
    has_connector = _contains_terms(n, ["connecteur", "connecteurs", "connectors", "goujon", "goujons", "shear connector"])
    has_tcc = _contains_terms(n, ["bois beton", "bois/beton", "timber concrete", "wood concrete", "seismic", "seisme", "séisme", "ductility", "ductilite", "ductilité", "cyclic", "diaphragm", "diaphragme"])
    if has_connector and has_tcc:
        return "timber_concrete_seismic_connectors"

    # 3) Feu : il faut un vrai terme feu/REI, pas seulement paille/chanvre/biosourcé.
    if _contains_terms(n, ["rei", "rei 60", "feu", "incendie", "fire resistance", "fire rating", "reaction to fire", "resistance au feu", "résistance au feu", "charring", "smouldering"]):
        return "bio_based_fire_resistance"

    # 4) Hygro/fongique : humidité, WUFI, moisissures, condensation, perspirance.
    if _contains_terms(n, ["fongique", "fungal", "moisiss", "mould", "mold", "hygro", "humid", "moisture", "perspir", "vapour", "vapor", "condensation", "wufi", "pare vapeur", "pare-vapeur"]):
        return "bio_based_hygro_fungal_moisture"

    # 5) Tassement : seulement avec vraie mention tassement/settlement/loose-fill, pas insufflation seule.
    if _contains_terms(n, ["tassement", "settlement", "settling", "compaction", "compactage", "loose fill", "loose-fill"]):
        return "loose_fill_biobased_insulation_settlement"

    if _contains_terms(n, ["acoust", "vibrat", "multi physique", "multiphysics", "thermal acoustic", "confort", "inconfort", "degres heures", "degrés-heures"]):
        return "building_multiphysics_comfort"

    return fallback


def get_cir_domain_profile(domain_detection: Dict[str, Any] | None = None, text: Any = "") -> Dict[str, Any]:
    """
    Retourne un profil complet pour EnnoScholar.
    Ne dépend pas d'un seul projet : il combine la nomenclature CIR + le texte du verrou.
    """
    domain_detection = domain_detection or {}
    c1, c2, c3, c4 = _codes_from_domain_detection(domain_detection)

    label_text = " ".join([
        str(domain_detection.get("display_label") or ""),
        str(domain_detection.get("broad_domain_label") or domain_detection.get("domain_label_niv1") or ""),
        str(domain_detection.get("main_domain_label") or domain_detection.get("domain_label_niv2") or ""),
        str(domain_detection.get("sub_domain_label") or domain_detection.get("domain_label_niv3") or ""),
        str(text or ""),
    ])

    by_code = _profile_from_code(c2, c3, c4)
    profile_id = _profile_from_text(label_text, fallback=by_code)
    template = PROFILE_TEMPLATES.get(profile_id) or PROFILE_TEMPLATES.get(by_code) or {}

    rows = _find_domain_rows_by_code(c4) or _find_domain_rows_by_code(c3) or _find_domain_rows_by_code(c2) or _find_domain_rows_by_code(c1)
    labels = []
    for r in rows[:8]:
        labels.extend([
            r.get("DOMAINES niv1", ""),
            r.get("Sous-domaines niv2", ""),
            r.get("SECTION niv3", ""),
            r.get("Sous-sections niv4", ""),
        ])

    domain_terms = []
    for lab in labels + [label_text]:
        for w in _words(lab):
            if w not in domain_terms:
                domain_terms.append(w)
            if len(domain_terms) >= 18:
                break

    return {
        "profile_id": profile_id,
        "base_profile_id": by_code,
        "label": template.get("label") or profile_id,
        "code1": c1,
        "code2": c2,
        "code3": c3,
        "code4": c4,
        "query_seeds": template.get("query_seeds") or [],
        "positive_terms": template.get("positive_terms") or domain_terms[:12],
        "negative_terms": template.get("negative_terms") or [],
        "source_profiles": template.get("source_profiles") or [],
        "domain_terms": domain_terms,
        "nomenclature_coverage": {
            "rows_total": len(CIR_NOMENCLATURE_ROWS),
            "matched_rows": len(rows),
            "source": "nomenclature-scientifique-de-domaines-de-recherche--38201.xlsx",
        },
    }


def _intent_text(intent: Dict[str, Any]) -> str:
    return " ".join([
        str(intent.get("verrou_title") or ""),
        str(intent.get("original_title") or ""),
        str(intent.get("scientific_problem") or ""),
        str(intent.get("technical_object") or ""),
        str(intent.get("phenomenon") or ""),
        " ".join(map(str, intent.get("constraints") or [])),
        " ".join(map(str, intent.get("methods") or [])),
        " ".join(map(str, intent.get("key_terms_fr") or [])),
        " ".join(map(str, intent.get("key_terms_en") or [])),
    ])


def _domain_detection_from_intent(intent: Dict[str, Any]) -> Dict[str, Any]:
    for key in ["domain_detection", "cir_domain_detection"]:
        if isinstance(intent.get(key), dict):
            return intent.get(key) or {}
    return {}


def build_cir_domain_queries(intent: Dict[str, Any], max_queries: int = 8) -> List[Dict[str, str]]:
    """
    Construit les requêtes prioritaires à envoyer aux APIs.
    Principe :
    - d'abord les requêtes spécifiques du profil ;
    - ensuite des requêtes hybrides profil + termes du verrou ;
    - jamais de requêtes génériques de type "technical uncertainty".
    """
    intent = intent or {}
    profile = intent.get("cir_domain_profile") if isinstance(intent.get("cir_domain_profile"), dict) else None
    if not profile:
        profile = get_cir_domain_profile(_domain_detection_from_intent(intent), _intent_text(intent))

    terms = []
    for k in ["technical_object", "phenomenon", "scientific_problem"]:
        terms.extend(_words(intent.get(k)))
    terms.extend([w for w in _words(" ".join(map(str, intent.get("key_terms_en") or [])))])
    # garder quelques termes spécifiques mais pas trop longs
    intent_terms = []
    for w in terms:
        if w not in intent_terms and len(w) >= 4:
            intent_terms.append(w)
        if len(intent_terms) >= 8:
            break

    queries: List[Dict[str, str]] = []
    seen = set()

    def add(q: str, kind: str):
        q = _clean_query(q)
        nq = _norm(q)
        if len(q) < 12 or nq in seen:
            return
        if any(bad in nq for bad in ["technical uncertainty", "question qualification", "simple engineering"]):
            return
        seen.add(nq)
        queries.append({"query": q, "kind": kind})

    for seed in profile.get("query_seeds") or []:
        add(seed, "cir_domain_profile_query")

    label_words = " ".join((profile.get("positive_terms") or [])[:5])
    if intent_terms:
        add(" ".join(intent_terms[:6] + _words(label_words)[:4]), "verrou_terms_domain_context")
        add(" ".join(_words(label_words)[:5] + intent_terms[:5]), "domain_terms_verrou_context")

    # requête en français utile pour HAL/Google-like/OpenAlex parfois.
    fr_terms = " ".join(map(str, intent.get("key_terms_fr") or []))
    if fr_terms:
        add(" ".join(_words(fr_terms)[:8]), "french_key_terms")

    return queries[:max_queries]


def score_text_against_profile(text: Any, profile: Dict[str, Any]) -> Dict[str, Any]:
    n = _norm(text)
    positives = profile.get("positive_terms") or []
    negatives = profile.get("negative_terms") or []
    matched_pos = [p for p in positives if _has_word_or_phrase(n, str(p))]
    matched_neg = [p for p in negatives if _has_word_or_phrase(n, str(p))]
    score = min(len(matched_pos) * 0.15, 0.75) - min(len(matched_neg) * 0.18, 0.55)
    if len(matched_pos) >= 3:
        score += 0.15
    return {
        "domain_profile_score": round(max(0.0, min(1.0, score)), 4),
        "matched_positive_terms": matched_pos[:8],
        "matched_negative_terms": matched_neg[:8],
    }
