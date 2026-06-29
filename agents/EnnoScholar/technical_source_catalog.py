# -*- coding: utf-8 -*-
from __future__ import annotations

"""
technical_source_catalog.py — EnnoScholar V131

Catalogue multi-domaines de sources techniques reconnues pour compléter
Semantic Scholar / OpenAlex / ArXiv.

Important :
- générique à tous les domaines CIR ;
- activation par profil détecté, pas par projet/client ;
- les sources sont proposées au consultant, pas validées automatiquement ;
- elles sont normalisées comme des "papers" pour réutiliser le pipeline existant.
"""

from typing import Any, Dict, List

from .utils import clean_text, norm
from .cir_domain_query_catalog import get_cir_domain_profile, score_text_against_profile


TECHNICAL_SOURCES: List[Dict[str, Any]] = [
    # Sources transverses utiles à tous les domaines
    {
        "paper_id": "tech:hal-open-archive",
        "title": "HAL — archive ouverte pluridisciplinaire de publications scientifiques",
        "venue": "HAL",
        "url": "https://hal.science",
        "year": None,
        "authors": ["HAL"],
        "profiles": ["digital_engineering", "materials_standards", "mechanical_civil", "biomedical_literature", "social_sciences", "humanities", "mathematics", "simulation"],
        "abstract": "Archive ouverte pluridisciplinaire utile pour rechercher des thèses, articles, rapports et communications scientifiques dans tous les domaines de la nomenclature CIR.",
        "fields_of_study": ["Multidisciplinary", "Scientific literature"],
    },
    {
        "paper_id": "tech:zenodo-openaire",
        "title": "Zenodo / OpenAIRE — publications, données et livrables de projets de recherche",
        "venue": "Zenodo / OpenAIRE",
        "url": "https://zenodo.org",
        "year": None,
        "authors": ["Zenodo", "OpenAIRE"],
        "profiles": ["digital_engineering", "materials_standards", "mechanical_civil", "energy_process", "biomedical_literature", "social_sciences", "humanities"],
        "abstract": "Dépôt européen utile pour retrouver articles, jeux de données, rapports et livrables techniques associés à des projets de recherche.",
        "fields_of_study": ["Research outputs", "Datasets", "Reports"],
    },

    # A — numérique / mathématiques / robotique
    {
        "paper_id": "tech:ieee-xplore",
        "title": "IEEE Xplore — électronique, informatique, IA, réseaux, signal, robotique",
        "venue": "IEEE",
        "url": "https://ieeexplore.ieee.org",
        "year": None,
        "authors": ["IEEE"],
        "profiles": ["digital_engineering", "automation_robotics", "electronics_telecom", "cybersecurity", "ai_data", "computer_vision", "electrical_energy"],
        "abstract": "Base scientifique et technique majeure pour l'informatique, l'IA, le traitement du signal, l'électronique, les télécommunications, l'embarqué, l'automatique, la robotique et les réseaux électriques.",
        "fields_of_study": ["Computer science", "Engineering", "Electronics", "AI", "Networks"],
    },
    {
        "paper_id": "tech:acm-digital-library",
        "title": "ACM Digital Library — informatique, génie logiciel, systèmes, interaction humain-machine",
        "venue": "ACM",
        "url": "https://dl.acm.org",
        "year": None,
        "authors": ["ACM"],
        "profiles": ["digital_engineering", "ai_data", "cybersecurity", "computer_vision", "human_factors"],
        "abstract": "Source scientifique pour les systèmes informatiques, algorithmes, génie logiciel, interaction humain-machine, apprentissage automatique et sécurité.",
        "fields_of_study": ["Computer science", "Software engineering", "HCI", "AI"],
    },
    {
        "paper_id": "tech:inria-hal",
        "title": "INRIA / HAL — informatique, automatique, IA, mathématiques appliquées",
        "venue": "INRIA / HAL",
        "url": "https://hal.inria.fr",
        "year": None,
        "authors": ["INRIA"],
        "profiles": ["digital_engineering", "ai_data", "mathematics", "simulation", "automation_robotics"],
        "abstract": "Publications et rapports de recherche en informatique, intelligence artificielle, automatique, optimisation, simulation et mathématiques appliquées.",
        "fields_of_study": ["Computer science", "Applied mathematics", "AI", "Control"],
    },
    {
        "paper_id": "tech:nist-ai-rmf",
        "title": "NIST — AI Risk Management Framework et mesures de robustesse IA",
        "venue": "NIST",
        "url": "https://www.nist.gov/itl/ai-risk-management-framework",
        "year": 2023,
        "authors": ["NIST"],
        "profiles": ["ai_data", "digital_engineering"],
        "abstract": "Cadre technique pour la gestion des risques IA : robustesse, explicabilité, gouvernance, validation, évaluation et maîtrise des limites des systèmes d'intelligence artificielle.",
        "fields_of_study": ["Artificial intelligence", "Risk management", "Validation"],
    },
    {
        "paper_id": "tech:owasp",
        "title": "OWASP — référentiels de sécurité applicative",
        "venue": "OWASP",
        "url": "https://owasp.org",
        "year": None,
        "authors": ["OWASP"],
        "profiles": ["cybersecurity", "digital_engineering"],
        "abstract": "Référentiels techniques de sécurité applicative, vulnérabilités, bonnes pratiques de test et modèles de menace pour les systèmes logiciels.",
        "fields_of_study": ["Cybersecurity", "Software security"],
    },
    {
        "paper_id": "tech:enisa",
        "title": "ENISA — cybersécurité, réseaux, résilience des systèmes numériques",
        "venue": "ENISA",
        "url": "https://www.enisa.europa.eu",
        "year": None,
        "authors": ["ENISA"],
        "profiles": ["cybersecurity", "digital_engineering", "electronics_telecom"],
        "abstract": "Agence européenne de cybersécurité : rapports techniques sur menaces, résilience, sécurité réseau, systèmes industriels et méthodes d'évaluation des risques.",
        "fields_of_study": ["Cybersecurity", "Risk", "Networks"],
    },
    {
        "paper_id": "tech:ietf-rfc",
        "title": "IETF RFC — standards et protocoles réseaux",
        "venue": "IETF",
        "url": "https://www.rfc-editor.org",
        "year": None,
        "authors": ["IETF"],
        "profiles": ["electronics_telecom", "digital_engineering", "cybersecurity"],
        "abstract": "Référentiel de standards techniques pour les protocoles Internet, réseaux, sécurité, interopérabilité et architecture de communication.",
        "fields_of_study": ["Networks", "Protocols", "Cybersecurity"],
    },
    {
        "paper_id": "tech:w3c",
        "title": "W3C — standards web, accessibilité, interopérabilité",
        "venue": "W3C",
        "url": "https://www.w3.org",
        "year": None,
        "authors": ["W3C"],
        "profiles": ["digital_engineering", "human_factors"],
        "abstract": "Standards web, accessibilité, interfaces, interopérabilité, données et technologies web.",
        "fields_of_study": ["Web standards", "Accessibility", "Interoperability"],
    },

    # B — industrie / physique / énergie / matériaux / bâtiment
    {
        "paper_id": "tech:iso-standards",
        "title": "ISO — normes internationales de méthode, qualité, essais et performance",
        "venue": "ISO",
        "url": "https://www.iso.org",
        "year": None,
        "authors": ["ISO"],
        "profiles": ["materials_standards", "mechanical_civil", "process_engineering", "medical_device_regulatory", "food_safety", "quality_methods"],
        "abstract": "Normes internationales utiles pour situer les méthodes d'essai, exigences de qualité, validation, métrologie, dispositifs médicaux, management et performance technique.",
        "fields_of_study": ["Standards", "Testing", "Quality", "Validation"],
    },
    {
        "paper_id": "tech:astm-standards",
        "title": "ASTM International — normes matériaux, essais, construction, mécanique",
        "venue": "ASTM",
        "url": "https://www.astm.org",
        "year": None,
        "authors": ["ASTM"],
        "profiles": ["materials_standards", "mechanical_civil", "construction_if_applicable", "energy_process"],
        "abstract": "Normes et méthodes d'essai pour matériaux, construction, propriétés mécaniques, durabilité, feu, thermique et procédés industriels.",
        "fields_of_study": ["Materials", "Testing", "Construction", "Mechanical engineering"],
    },
    {
        "paper_id": "tech:asme",
        "title": "ASME Digital Collection — mécanique, procédés, énergie, conception",
        "venue": "ASME",
        "url": "https://asmedigitalcollection.asme.org",
        "year": None,
        "authors": ["ASME"],
        "profiles": ["mechanical_civil", "energy_process", "process_engineering"],
        "abstract": "Source scientifique et technique pour la mécanique, fatigue, vibration, énergie, échange thermique, fluides, conception et validation expérimentale.",
        "fields_of_study": ["Mechanical engineering", "Energy", "Thermal", "Fluids"],
    },
    {
        "paper_id": "tech:sae",
        "title": "SAE International — mobilité, mécanique, systèmes embarqués, batteries",
        "venue": "SAE",
        "url": "https://www.sae.org",
        "year": None,
        "authors": ["SAE"],
        "profiles": ["mechanical_civil", "electrical_energy", "automation_robotics", "electronics_telecom"],
        "abstract": "Références techniques sur véhicules, mécanique, batteries, systèmes embarqués, motorisation, sécurité, validation et essais.",
        "fields_of_study": ["Automotive", "Mechanical", "Battery", "Embedded systems"],
    },
    {
        "paper_id": "tech:iec",
        "title": "IEC — normes électrotechniques, électronique, dispositifs et sécurité",
        "venue": "IEC",
        "url": "https://www.iec.ch",
        "year": None,
        "authors": ["IEC"],
        "profiles": ["electrical_energy", "electronics_telecom", "medical_device_regulatory"],
        "abstract": "Normes internationales pour équipements électriques, électronique, sécurité, dispositifs médicaux électriques, essais et performance.",
        "fields_of_study": ["Electrical engineering", "Electronics", "Safety", "Medical electrical equipment"],
    },
    {
        "paper_id": "tech:echa",
        "title": "ECHA — substances chimiques, REACH, CLP, toxicologie réglementaire",
        "venue": "ECHA",
        "url": "https://echa.europa.eu",
        "year": None,
        "authors": ["ECHA"],
        "profiles": ["chemistry_materials", "toxicology", "pharma_regulatory"],
        "abstract": "Base réglementaire européenne sur substances chimiques, sécurité, toxicologie, classification, restriction et données de danger.",
        "fields_of_study": ["Chemistry", "Toxicology", "Regulation"],
    },
    {
        "paper_id": "tech:oecd-test-guidelines",
        "title": "OECD Test Guidelines — essais chimiques, toxicologiques, environnementaux",
        "venue": "OECD",
        "url": "https://www.oecd.org/chemicalsafety/testing/oecdguidelinesforthetestingofchemicals.htm",
        "year": None,
        "authors": ["OECD"],
        "profiles": ["chemistry_materials", "toxicology", "agriculture_environment", "pharma_regulatory"],
        "abstract": "Méthodes internationalement reconnues pour essais chimiques, toxicologiques, écotoxicologiques, environnementaux et validation réglementaire.",
        "fields_of_study": ["Testing guidelines", "Chemistry", "Toxicology", "Environment"],
    },
    {
        "paper_id": "tech:cea",
        "title": "CEA — énergie, physique, matériaux, électronique, instrumentation",
        "venue": "CEA",
        "url": "https://www.cea.fr",
        "year": None,
        "authors": ["CEA"],
        "profiles": ["physics_instrumentation", "energy_process", "materials_standards", "electronics_telecom"],
        "abstract": "Organisme de recherche sur énergie, matériaux, instrumentation, électronique, physique, procédés et technologies industrielles.",
        "fields_of_study": ["Energy", "Physics", "Materials", "Electronics"],
    },
    {
        "paper_id": "tech:brgm",
        "title": "BRGM — géosciences, sols, eau, risques et environnement",
        "venue": "BRGM",
        "url": "https://www.brgm.fr",
        "year": None,
        "authors": ["BRGM"],
        "profiles": ["earth_environment", "agriculture_environment"],
        "abstract": "Référence technique française pour géosciences, sols, hydrogéologie, risques naturels, ressources, environnement et données de terrain.",
        "fields_of_study": ["Geoscience", "Soil", "Water", "Environment"],
    },
    {
        "paper_id": "tech:ifpen",
        "title": "IFPEN — énergie, procédés, mobilité, environnement",
        "venue": "IFPEN",
        "url": "https://www.ifpenergiesnouvelles.fr",
        "year": None,
        "authors": ["IFPEN"],
        "profiles": ["energy_process", "process_engineering", "chemistry_materials"],
        "abstract": "Recherche appliquée en énergie, procédés, fluides, moteurs, mobilité, hydrogène, matériaux et environnement.",
        "fields_of_study": ["Energy", "Process engineering", "Hydrogen", "Mobility"],
    },
    {
        "paper_id": "tech:iea",
        "title": "International Energy Agency — énergie, efficacité, scénarios et systèmes",
        "venue": "IEA",
        "url": "https://www.iea.org",
        "year": None,
        "authors": ["IEA"],
        "profiles": ["energy_process", "urban_environment"],
        "abstract": "Rapports et données sur systèmes énergétiques, efficacité, décarbonation, technologies bas carbone et scénarios.",
        "fields_of_study": ["Energy", "Efficiency", "Decarbonization"],
    },
    {
        "paper_id": "tech:ipcc",
        "title": "IPCC — climat, adaptation, émissions, impacts",
        "venue": "IPCC",
        "url": "https://www.ipcc.ch",
        "year": None,
        "authors": ["IPCC"],
        "profiles": ["earth_environment", "urban_environment", "agriculture_environment", "energy_process"],
        "abstract": "Rapports scientifiques sur changement climatique, émissions, impacts, adaptation, atténuation et risques environnementaux.",
        "fields_of_study": ["Climate", "Environment", "Energy", "Adaptation"],
    },

    # B/D — bâtiment, architecture, environnement construit
    {
        "paper_id": "tech:cerema",
        "title": "Cerema — bâtiment, matériaux, mobilité, environnement, aménagement",
        "venue": "Cerema",
        "url": "https://www.cerema.fr",
        "year": None,
        "authors": ["Cerema"],
        "profiles": ["construction_if_applicable", "building_biobased", "urban_environment", "earth_environment"],
        "abstract": "Centre technique français sur construction, matériaux, environnement, aménagement, risques, mobilité, bâtiment durable et retours d'expérience.",
        "fields_of_study": ["Construction", "Environment", "Urban planning", "Materials"],
    },
    {
        "paper_id": "tech:cstb",
        "title": "CSTB — bâtiment, ATEx, avis techniques, feu, hygrothermie, procédés innovants",
        "venue": "CSTB",
        "url": "https://www.cstb.fr",
        "year": None,
        "authors": ["CSTB"],
        "profiles": ["construction_if_applicable", "building_biobased", "fire_safety", "mechanical_civil"],
        "abstract": "Référence technique pour le bâtiment : ATEx, avis techniques, essais, feu, acoustique, hygrothermie, durabilité et évaluation des procédés innovants.",
        "fields_of_study": ["Construction", "ATEx", "Fire", "Hygrothermal", "Acoustics"],
    },
    {
        "paper_id": "tech:fcba",
        "title": "FCBA — bois construction, durabilité, humidité, feu, performances",
        "venue": "FCBA",
        "url": "https://www.fcba.fr",
        "year": None,
        "authors": ["FCBA"],
        "profiles": ["construction_if_applicable", "building_biobased", "fire_safety", "materials_standards"],
        "abstract": "Institut technologique forêt-cellulose-bois-construction-ameublement : bois construction, durabilité, humidité, feu, essais et performances des systèmes bois.",
        "fields_of_study": ["Timber", "Construction", "Durability", "Fire"],
    },
    {
        "paper_id": "tech:codifab",
        "title": "CODIFAB — bois construction, humidité chantier, systèmes bois",
        "venue": "CODIFAB",
        "url": "https://www.codifab.fr",
        "year": None,
        "authors": ["CODIFAB"],
        "profiles": ["construction_if_applicable", "building_biobased"],
        "abstract": "Guides et ressources techniques pour la construction bois, notamment gestion de l'humidité, préfabrication, durabilité et performances.",
        "fields_of_study": ["Timber construction", "Moisture", "Durability"],
    },
    {
        "paper_id": "tech:efectis",
        "title": "Efectis — résistance au feu, ingénierie incendie, essais",
        "venue": "Efectis",
        "url": "https://www.efectis.com",
        "year": None,
        "authors": ["Efectis"],
        "profiles": ["fire_safety", "construction_if_applicable"],
        "abstract": "Laboratoire et expertise technique sur résistance au feu, réaction au feu, ingénierie incendie et essais pour bâtiments et systèmes constructifs.",
        "fields_of_study": ["Fire resistance", "Fire safety", "Testing"],
    },
    {
        "paper_id": "tech:enviroboite-apave",
        "title": "Guide technique des matériaux biosourcés & géosourcés — EnviroBOITE / APAVE",
        "venue": "EnviroBOITE / APAVE",
        "url": "https://envirobatbdm.eu",
        "year": 2022,
        "authors": ["EnviroBOITE", "APAVE"],
        "profiles": ["building_biobased", "construction_if_applicable", "fire_safety"],
        "abstract": "Guide technique sur matériaux biosourcés et géosourcés : eau, vapeur d'eau, remontées capillaires, feu, référentiels, ATEx, essais, assurabilité et mise en œuvre.",
        "fields_of_study": ["Bio-based materials", "Construction", "Fire", "Moisture"],
    },
    {
        "paper_id": "tech:ffb-biosources",
        "title": "FFB — points de vigilance lors de la mise en œuvre de matériaux biosourcés",
        "venue": "Fédération Française du Bâtiment",
        "url": "https://www.ffbatiment.fr",
        "year": None,
        "authors": ["FFB"],
        "profiles": ["building_biobased", "construction_if_applicable", "fire_safety"],
        "abstract": "Source professionnelle sur matériaux biosourcés : humidité, moisissures, stockage, transport, tassement des isolants en vrac, feu et points de vigilance chantier.",
        "fields_of_study": ["Bio-based materials", "Moisture", "Mould", "Fire", "Loose-fill insulation"],
    },
    {
        "paper_id": "tech:inrae-biobased-building",
        "title": "INRAE — matériaux biosourcés pour verdir le bâtiment",
        "venue": "INRAE",
        "url": "https://www.inrae.fr",
        "year": None,
        "authors": ["INRAE"],
        "profiles": ["building_biobased", "agriculture_environment", "materials_standards"],
        "abstract": "Source scientifique et technique sur biomasse, chanvre, matériaux biosourcés, formulation, liants, durabilité et filières végétales pour le bâtiment.",
        "fields_of_study": ["Bio-based materials", "Biomass", "Construction"],
    },

    # C — santé, bio, pharma, agroalimentaire
    {
        "paper_id": "tech:pubmed",
        "title": "PubMed / MEDLINE — littérature biomédicale",
        "venue": "PubMed",
        "url": "https://pubmed.ncbi.nlm.nih.gov",
        "year": None,
        "authors": ["NIH", "NLM"],
        "profiles": ["biomedical_literature", "clinical_regulatory", "medical_device_regulatory", "biotechnology"],
        "abstract": "Base bibliographique biomédicale pour biologie, médecine, clinique, dispositifs médicaux, biotechnologie, pharmacologie et santé publique.",
        "fields_of_study": ["Biomedical", "Clinical", "Biology", "Medicine"],
    },
    {
        "paper_id": "tech:clinicaltrials",
        "title": "ClinicalTrials.gov — essais cliniques enregistrés",
        "venue": "ClinicalTrials.gov",
        "url": "https://clinicaltrials.gov",
        "year": None,
        "authors": ["NIH"],
        "profiles": ["clinical_regulatory", "medical_device_regulatory", "pharma_regulatory"],
        "abstract": "Registre d'essais cliniques utile pour comparer protocoles, critères d'évaluation, populations, endpoints, sécurité et état de l'art clinique.",
        "fields_of_study": ["Clinical trials", "Endpoints", "Safety"],
    },
    {
        "paper_id": "tech:cochrane",
        "title": "Cochrane Library — revues systématiques en santé",
        "venue": "Cochrane",
        "url": "https://www.cochranelibrary.com",
        "year": None,
        "authors": ["Cochrane"],
        "profiles": ["clinical_regulatory", "biomedical_literature"],
        "abstract": "Revues systématiques et méta-analyses utiles pour positionner l'état des connaissances cliniques, efficacité, sécurité et limites de preuve.",
        "fields_of_study": ["Clinical evidence", "Systematic reviews"],
    },
    {
        "paper_id": "tech:ema",
        "title": "EMA — évaluation réglementaire médicaments et dispositifs associés",
        "venue": "EMA",
        "url": "https://www.ema.europa.eu",
        "year": None,
        "authors": ["European Medicines Agency"],
        "profiles": ["pharma_regulatory", "clinical_regulatory"],
        "abstract": "Sources réglementaires européennes pour médicaments : guidelines, évaluation, qualité, sécurité, efficacité, pharmacovigilance et développement clinique.",
        "fields_of_study": ["Pharmaceutical regulation", "Clinical", "Safety"],
    },
    {
        "paper_id": "tech:fda",
        "title": "FDA — guidance médicaments, dispositifs médicaux, essais et sécurité",
        "venue": "FDA",
        "url": "https://www.fda.gov",
        "year": None,
        "authors": ["FDA"],
        "profiles": ["pharma_regulatory", "clinical_regulatory", "medical_device_regulatory", "food_safety"],
        "abstract": "Guidances et référentiels réglementaires pour médicaments, dispositifs médicaux, logiciels médicaux, essais, sécurité, alimentation et validation.",
        "fields_of_study": ["Regulatory", "Medical devices", "Pharma", "Food"],
    },
    {
        "paper_id": "tech:ansm",
        "title": "ANSM — produits de santé, sécurité, dispositifs, médicaments",
        "venue": "ANSM",
        "url": "https://ansm.sante.fr",
        "year": None,
        "authors": ["ANSM"],
        "profiles": ["pharma_regulatory", "medical_device_regulatory", "clinical_regulatory"],
        "abstract": "Source réglementaire française sur produits de santé, médicaments, dispositifs médicaux, sécurité, surveillance et recommandations.",
        "fields_of_study": ["Health products", "Regulation", "Safety"],
    },
    {
        "paper_id": "tech:has",
        "title": "HAS — évaluation médicale, dispositifs, santé numérique, recommandations",
        "venue": "HAS",
        "url": "https://www.has-sante.fr",
        "year": None,
        "authors": ["HAS"],
        "profiles": ["clinical_regulatory", "medical_device_regulatory", "digital_health"],
        "abstract": "Recommandations et référentiels d'évaluation en santé, dispositifs médicaux, santé numérique, service médical et méthodologies d'évaluation clinique.",
        "fields_of_study": ["Health technology assessment", "Clinical evaluation", "Digital health"],
    },
    {
        "paper_id": "tech:who",
        "title": "WHO — santé publique, maladies, normes et données",
        "venue": "WHO",
        "url": "https://www.who.int",
        "year": None,
        "authors": ["WHO"],
        "profiles": ["clinical_regulatory", "biomedical_literature", "food_safety", "agriculture_environment"],
        "abstract": "Références internationales en santé publique, maladies, recommandations, données épidémiologiques et sécurité sanitaire.",
        "fields_of_study": ["Public health", "Epidemiology", "Guidelines"],
    },
    {
        "paper_id": "tech:efsa",
        "title": "EFSA — sécurité alimentaire, nutrition, risques, alimentation animale",
        "venue": "EFSA",
        "url": "https://www.efsa.europa.eu",
        "year": None,
        "authors": ["EFSA"],
        "profiles": ["food_safety", "agriculture_environment", "toxicology"],
        "abstract": "Évaluations scientifiques sur sécurité alimentaire, nutrition, alimentation animale, contaminants, risques biologiques et toxicologie.",
        "fields_of_study": ["Food safety", "Risk assessment", "Nutrition", "Toxicology"],
    },
    {
        "paper_id": "tech:inrae-agriculture-food",
        "title": "INRAE — agriculture, alimentation, environnement, biotechnologies",
        "venue": "INRAE",
        "url": "https://www.inrae.fr",
        "year": None,
        "authors": ["INRAE"],
        "profiles": ["agriculture_environment", "food_safety", "biotechnology", "building_biobased"],
        "abstract": "Recherche appliquée en agronomie, environnement, alimentation, microbiologie, biotechnologie, biomasse et filières végétales.",
        "fields_of_study": ["Agronomy", "Food", "Biotechnology", "Environment"],
    },

    # D — SHS, droit, économie, urbanisme
    {
        "paper_id": "tech:hal-shs",
        "title": "HAL-SHS — sciences humaines et sociales",
        "venue": "HAL-SHS",
        "url": "https://shs.hal.science",
        "year": None,
        "authors": ["HAL-SHS"],
        "profiles": ["social_sciences", "humanities", "law_policy", "economics_management", "urban_environment"],
        "abstract": "Archive ouverte spécialisée en sciences humaines et sociales : sociologie, économie, droit, gestion, urbanisme, psychologie, éducation, langues et arts.",
        "fields_of_study": ["Social sciences", "Humanities", "Law", "Economics"],
    },
    {
        "paper_id": "tech:openedition",
        "title": "OpenEdition — sciences humaines et sociales",
        "venue": "OpenEdition",
        "url": "https://www.openedition.org",
        "year": None,
        "authors": ["OpenEdition"],
        "profiles": ["social_sciences", "humanities", "law_policy", "urban_environment"],
        "abstract": "Portail de revues, livres et carnets scientifiques en sciences humaines et sociales.",
        "fields_of_study": ["Humanities", "Social sciences"],
    },
    {
        "paper_id": "tech:cairn",
        "title": "Cairn.info — revues SHS francophones",
        "venue": "Cairn.info",
        "url": "https://www.cairn.info",
        "year": None,
        "authors": ["Cairn"],
        "profiles": ["social_sciences", "humanities", "economics_management", "law_policy"],
        "abstract": "Revues et ouvrages en sciences humaines et sociales, gestion, économie, droit, psychologie, sociologie et information-communication.",
        "fields_of_study": ["Social sciences", "Management", "Law", "Psychology"],
    },
    {
        "paper_id": "tech:oecd",
        "title": "OECD — économie, politiques publiques, innovation, environnement",
        "venue": "OECD",
        "url": "https://www.oecd.org",
        "year": None,
        "authors": ["OECD"],
        "profiles": ["economics_management", "law_policy", "social_sciences", "urban_environment", "agriculture_environment"],
        "abstract": "Rapports, données et analyses sur économie, innovation, politiques publiques, productivité, environnement, éducation et gouvernance.",
        "fields_of_study": ["Economics", "Policy", "Innovation", "Environment"],
    },
    {
        "paper_id": "tech:world-bank",
        "title": "World Bank — développement, économie, indicateurs, politiques publiques",
        "venue": "World Bank",
        "url": "https://www.worldbank.org",
        "year": None,
        "authors": ["World Bank"],
        "profiles": ["economics_management", "law_policy", "social_sciences", "urban_environment"],
        "abstract": "Données et rapports sur économie, développement, politiques publiques, villes, environnement et indicateurs socio-économiques.",
        "fields_of_study": ["Economics", "Development", "Policy", "Urban"],
    },
    {
        "paper_id": "tech:insee",
        "title": "INSEE — statistiques publiques françaises",
        "venue": "INSEE",
        "url": "https://www.insee.fr",
        "year": None,
        "authors": ["INSEE"],
        "profiles": ["economics_management", "social_sciences", "urban_environment"],
        "abstract": "Données statistiques françaises utiles pour économie, démographie, territoires, entreprises, emploi, population et politiques publiques.",
        "fields_of_study": ["Statistics", "Economics", "Demography", "Territories"],
    },
    {
        "paper_id": "tech:eurostat",
        "title": "Eurostat — statistiques européennes",
        "venue": "Eurostat",
        "url": "https://ec.europa.eu/eurostat",
        "year": None,
        "authors": ["Eurostat"],
        "profiles": ["economics_management", "social_sciences", "urban_environment", "agriculture_environment"],
        "abstract": "Statistiques européennes pour économie, démographie, énergie, environnement, agriculture, territoires et société.",
        "fields_of_study": ["Statistics", "Europe", "Economics", "Environment"],
    },
    {
        "paper_id": "tech:cnil-gdpr",
        "title": "CNIL / RGPD — données personnelles, IA, conformité numérique",
        "venue": "CNIL",
        "url": "https://www.cnil.fr",
        "year": None,
        "authors": ["CNIL"],
        "profiles": ["law_policy", "digital_engineering", "ai_data"],
        "abstract": "Références réglementaires sur données personnelles, conformité RGPD, analyse d'impact, systèmes numériques et IA.",
        "fields_of_study": ["Data protection", "Law", "Digital regulation"],
    },
    {
        "paper_id": "tech:unesco",
        "title": "UNESCO — éducation, culture, patrimoine, sciences sociales",
        "venue": "UNESCO",
        "url": "https://www.unesco.org",
        "year": None,
        "authors": ["UNESCO"],
        "profiles": ["humanities", "social_sciences", "psychology_education", "urban_environment"],
        "abstract": "Sources internationales sur éducation, patrimoine, culture, sciences sociales, politiques culturelles et développement durable.",
        "fields_of_study": ["Education", "Culture", "Heritage", "Social sciences"],
    },
]


# Correspondance profil EnnoScholar -> profils de sources.
PROFILE_TO_SOURCE_PROFILES: Dict[str, List[str]] = {
    "automation_robotics_embedded": ["digital_engineering", "automation_robotics"],
    "signal_image_vision": ["digital_engineering", "computer_vision"],
    "software_ai_data_cyber": ["digital_engineering", "ai_data", "cybersecurity"],
    "mathematics_modeling_simulation": ["mathematics", "simulation"],
    "electronics_telecom_networks": ["electronics_telecom", "digital_engineering"],
    "electrical_power_energy": ["electrical_energy", "energy_process"],
    "materials_metallurgy": ["materials_standards", "chemistry_materials"],
    "mechanical_civil_engineering": ["mechanical_civil", "construction_if_applicable"],
    "chemistry_process": ["chemistry_materials", "process_engineering"],
    "physics_instrumentation": ["physics_instrumentation"],
    "energy_process_environment": ["energy_process", "environment"],
    "earth_ocean_atmosphere": ["earth_environment"],
    "cell_molecular_biology": ["biomedical_literature"],
    "human_animal_biology": ["biomedical_literature", "toxicology"],
    "pharma_cosmetics": ["pharma_regulatory", "biomedical_literature"],
    "clinical_trials": ["clinical_regulatory", "biomedical_literature"],
    "medical_devices_ehealth": ["medical_device_regulatory", "digital_health", "biomedical_literature"],
    "biotechnology": ["biotechnology", "biomedical_literature"],
    "agronomy_environment": ["agriculture_environment", "food_agri"],
    "food_feed": ["food_safety", "agriculture_environment"],
    "law_policy_regulation": ["law_policy"],
    "economics_management": ["economics_management"],
    "language_arts_humanities": ["humanities"],
    "psychology_ergonomics_education_info": ["social_sciences", "human_factors"],
    "sociology_geography_urbanism": ["urban_environment", "social_sciences"],
    "sports_science": ["sports_science"],
    "bio_based_thermal_inertia": ["building_biobased", "construction_if_applicable"],
    "bio_based_fire_resistance": ["building_biobased", "fire_safety", "construction_if_applicable"],
    "bio_based_hygro_fungal_moisture": ["building_biobased", "construction_if_applicable"],
    "loose_fill_biobased_insulation_settlement": ["building_biobased", "construction_if_applicable"],
    "timber_concrete_seismic_connectors": ["mechanical_civil", "construction_if_applicable"],
    "building_multiphysics_comfort": ["construction_if_applicable", "building_biobased"],
}


def _source_score(src: Dict[str, Any], profile: Dict[str, Any], text: str) -> float:
    source_profiles = set(src.get("profiles") or [])
    wanted = set(profile.get("source_profiles") or [])
    wanted.update(PROFILE_TO_SOURCE_PROFILES.get(profile.get("profile_id"), []))
    wanted.update(PROFILE_TO_SOURCE_PROFILES.get(profile.get("base_profile_id"), []))

    score = 0.0
    if wanted & source_profiles:
        score += 0.55

    profile_score = score_text_against_profile(
        " ".join([
            src.get("title", ""),
            src.get("abstract", ""),
            " ".join(src.get("fields_of_study") or []),
            text,
        ]),
        profile,
    )
    score += float(profile_score.get("domain_profile_score") or 0) * 0.35
    return max(0.0, min(score, 1.0))


def get_technical_sources_for_intent(intent: Dict[str, Any], max_sources: int = 6) -> List[Dict[str, Any]]:
    """
    Retourne des sources techniques reconnues sous forme normalisée article-like.
    V131 : ces sources sont proposées dans un bloc séparé et ne doivent jamais
    augmenter le score scientifique comme des articles Direct/Connexe.
    """
    intent = intent or {}
    profile = intent.get("cir_domain_profile") if isinstance(intent.get("cir_domain_profile"), dict) else None
    if not profile:
        text = " ".join([
            str(intent.get("verrou_title") or ""),
            str(intent.get("scientific_problem") or ""),
            str(intent.get("technical_object") or ""),
            str(intent.get("phenomenon") or ""),
            " ".join(map(str, intent.get("key_terms_en") or [])),
            " ".join(map(str, intent.get("key_terms_fr") or [])),
        ])
        profile = get_cir_domain_profile(intent.get("domain_detection") or {}, text)

    text = norm(" ".join([
        str(intent.get("verrou_title") or ""),
        str(intent.get("scientific_problem") or ""),
        str(intent.get("technical_object") or ""),
        str(intent.get("phenomenon") or ""),
        " ".join(map(str, intent.get("key_terms_en") or [])),
        " ".join(map(str, intent.get("key_terms_fr") or [])),
    ]))

    scored = []
    for src in TECHNICAL_SOURCES:
        s = _source_score(src, profile, text)
        if s >= 0.42:
            scored.append((s, src))

    scored.sort(key=lambda x: x[0], reverse=True)

    out = []
    for score, src in scored[:max_sources]:
        tag = "Technique"
        out.append({
            "source": "technical_catalog",
            "paper_id": src.get("paper_id"),
            "title": src.get("title"),
            "abstract": src.get("abstract"),
            "year": src.get("year"),
            "venue": src.get("venue"),
            "url": src.get("url"),
            "doi": "",
            "authors": src.get("authors") or [],
            "citation_count": 0,
            "influential_citation_count": 0,
            "publication_types": ["technical_reference"],
            "fields_of_study": src.get("fields_of_study") or [],
            "tldr": "",
            "query": "technical_source_catalog",
            "tag": tag,
            "source_type": "technical_reference",
            "source_kind": "Source technique reconnue à consulter",
            "catalog_profile": profile.get("profile_id"),
            "catalog_score": round(score, 4),
            "relevance_score": round(score, 4),
            "reason": (
                "Source technique reconnue proposée en complément. Elle ne constitue pas "
                "à elle seule un article scientifique direct et doit être vérifiée par le consultant. "
                f"Profil : {profile.get('profile_id')}."
            ),
        })

    return out
