export type CirTag = "PERTINENT POUR CIR" | "MOYEN POUR CIR" | "FAIBLE POUR CIR"
export type ArticleTag = "DIRECTEMENT LIÉ AU VERROU" | "JUSTE CONCEPT SCIENTIFIQUE" | "À IGNORER — HORS SUJET"
export type ProjectStatus = "Analyse terminée" | "Validation consultant requise" | "Recherche scientifique en cours" | "Rapport prêt"

export type Project = {
  id: string
  client: string
  project: string
  year: string
  status: ProjectStatus
  activeAgent: "EnnoDiagnostic" | "EnnoScholar" | "EnnoAmel" | "Aucun"
  updatedAt: string
  risk: "Faible" | "Moyen" | "Élevé"
  progress: number
}

export type DocumentItem = {
  id: string
  name: string
  type: string
  origin: "Document brut" | "CIR précédent" | "Rapport test" | "Tableur mesures" | "Note consultant"
  status: "Extrait" | "En attente" | "Erreur OCR" | "Indexé"
  quality: string
  passages: number
  size: string
}

export type Verrou = {
  id: string
  title: string
  tag: CirTag
  justification: string
  sources: string[]
  score: number
  frascati: number
  selected: boolean
  scientificText: string
  queries: string[]
}

export type Article = {
  id: string
  title: string
  year: number
  source: "OpenAlex" | "Semantic Scholar" | "ArXiv"
  tag: ArticleTag
  score: number
  citations: number
  url: string
  verrouId: string
  selected: boolean
}

export type Evidence = {
  id: string
  role: "Verrou" | "Objectif" | "Méthode" | "Résultat" | "Paramètre" | "Limite" | "Faux verrou rejeté"
  document: string
  sourceCategory: string
  score: number
  text: string
}

export type DocumentComparison = {
  id: string
  docA: string
  docB: string
  type: string
  score: number
  differences: string
  impact: string
  status: "Évolution" | "Contradiction possible" | "Preuve renforcée" | "À vérifier"
}

export type CirComparison = {
  id: string
  element: string
  cirN1: string
  current: string
  evolution: "Nouveau" | "Évolution" | "Récurrent" | "Risque de répétition" | "À reformuler"
  analysis: string
  action: string
}

export const currentConsultant = {
  name: "Consultant CIR",
  role: "Consultant CIR / Data Scientist",
  initials: "CC",
}

export const currentContext = {
  client: "Client Démo",
  project: "Projet R&D Démo",
  year: "2024",
  domain: "Ingénierie industrielle",
  subdomains: ["Mécanique", "Thermique", "Simulation", "Essais"],
}

export const projects: Project[] = [
  {
    id: "client-demo-projet-rd-2024",
    client: "Client Démo",
    project: "Projet R&D Démo",
    year: "2024",
    status: "Validation consultant requise",
    activeAgent: "EnnoDiagnostic",
    updatedAt: "Aujourd’hui",
    risk: "Moyen",
    progress: 72,
  },
  {
    id: "client-demo-logiciel-2024",
    client: "Client Démo",
    project: "Plateforme logicielle expérimentale",
    year: "2024",
    status: "Analyse terminée",
    activeAgent: "EnnoDiagnostic",
    updatedAt: "Hier",
    risk: "Faible",
    progress: 84,
  },
  {
    id: "client-demo-biotech-2024",
    client: "Client Démo",
    project: "Procédé expérimental",
    year: "2024",
    status: "Recherche scientifique en cours",
    activeAgent: "EnnoScholar",
    updatedAt: "Il y a 2 jours",
    risk: "Moyen",
    progress: 58,
  },
  {
    id: "client-demo-valorisation-2024",
    client: "Client Démo",
    project: "Valorisation CIR",
    year: "2024",
    status: "Rapport prêt",
    activeAgent: "Aucun",
    updatedAt: "Il y a 5 jours",
    risk: "Faible",
    progress: 100,
  },
]

export const documents: DocumentItem[] = [
  {
    id: "doc-1",
    name: "rapport_essais_prototype_rev1.docx",
    type: "Rapport test",
    origin: "Document brut",
    status: "Indexé",
    quality: "Bonne",
    passages: 31,
    size: "480 Ko",
  },
  {
    id: "doc-2",
    name: "note_technique_performance_systeme.docx",
    type: "Note technique",
    origin: "Document brut",
    status: "Indexé",
    quality: "Bonne",
    passages: 24,
    size: "320 Ko",
  },
  {
    id: "doc-3",
    name: "releves_mesures_experimentales.xlsx",
    type: "Tableur mesures",
    origin: "Tableur mesures",
    status: "Extrait",
    quality: "Moyenne",
    passages: 12,
    size: "92 Ko",
  },
  {
    id: "doc-4",
    name: "analyse_defaillances_conditions_reelles.pdf",
    type: "Rapport analyse",
    origin: "Rapport test",
    status: "Indexé",
    quality: "Bonne",
    passages: 42,
    size: "2,1 Mo",
  },
  {
    id: "doc-5",
    name: "cir_final_precedent_2023.docx",
    type: "CIR N-1",
    origin: "CIR précédent",
    status: "Indexé",
    quality: "Bonne",
    passages: 78,
    size: "5,1 Mo",
  },
]

export const verrous: Verrou[] = [
  {
    id: "stability",
    title: "Comportement instable ou non maîtrisé du système",
    tag: "PERTINENT POUR CIR",
    justification:
      "Difficulté liée à la stabilité du système sous conditions réelles, avec des variations de comportement et des essais nécessaires pour comprendre les causes techniques.",
    sources: ["releves_mesures_experimentales.xlsx", "rapport_essais_prototype_rev1.docx"],
    score: 82,
    frascati: 68,
    selected: true,
    scientificText:
      "System stability, dynamic behavior, experimental validation, vibration or instability analysis under constrained operating conditions.",
    queries: [
      "dynamic stability experimental validation industrial system",
      "system instability root cause experimental analysis",
      "mechanical system vibration stability operating conditions",
    ],
  },
  {
    id: "thermal",
    title: "Maîtrise thermique et performance sous contraintes",
    tag: "MOYEN POUR CIR",
    justification:
      "Sujet technique réel, à reformuler pour distinguer le verrou scientifique de la solution testée. Les documents contiennent des mesures, des protocoles et des résultats.",
    sources: ["note_technique_performance_systeme.docx", "comparatif_conditions_essais.pdf"],
    score: 64,
    frascati: 60,
    selected: true,
    scientificText:
      "Heat transfer, cooling performance, thermal regulation and performance degradation under variable operating constraints.",
    queries: [
      "thermal management performance under variable operating conditions",
      "heat transfer experimental validation constrained system",
      "cooling performance industrial prototype water flow temperature",
    ],
  },
  {
    id: "wear",
    title: "Fiabilité, usure ou dégradation en fonctionnement",
    tag: "PERTINENT POUR CIR",
    justification:
      "Incertitude liée à une dégradation anormale observée en conditions réelles, nécessitant une analyse des causes, des essais et une validation expérimentale.",
    sources: ["analyse_defaillances_conditions_reelles.pdf", "pv_analyse_materiaux.pdf"],
    score: 79,
    frascati: 68,
    selected: true,
    scientificText:
      "Abnormal wear, degradation mechanisms, reliability analysis and failure investigation under real operating conditions.",
    queries: [
      "abnormal wear degradation mechanisms operating conditions",
      "failure analysis reliability industrial component experimental validation",
      "tribology wear root cause analysis mechanical system",
    ],
  },
  {
    id: "root-cause",
    title: "Identification de la cause technique principale",
    tag: "MOYEN POUR CIR",
    justification:
      "Problème intéressant, mais à rattacher à une incertitude technique précise pour éviter un verrou trop générique.",
    sources: ["pv_analyse_materiaux.pdf", "analyse_defaillances_conditions_reelles.pdf"],
    score: 61,
    frascati: 58,
    selected: true,
    scientificText:
      "Root cause identification for abnormal technical behavior, degradation or performance loss in industrial systems.",
    queries: [
      "root cause analysis abnormal degradation industrial system",
      "failure analysis experimental investigation operating conditions",
      "technical uncertainty root cause validation prototype",
    ],
  },
  {
    id: "performance",
    title: "Performance insuffisante sous contrainte",
    tag: "MOYEN POUR CIR",
    justification:
      "Le sujet regroupe plusieurs contraintes et mesures. Il peut être utile mais doit être fusionné ou reformulé avec un verrou plus précis.",
    sources: ["rapport_essais_prototype_rev1.docx", "note_technique_performance_systeme.docx"],
    score: 56,
    frascati: 54,
    selected: false,
    scientificText:
      "Performance loss under technical constraints in a prototype or industrial system.",
    queries: ["performance loss technical constraints prototype experimental validation"],
  },
  {
    id: "non-transfer",
    title: "Non-transférabilité des solutions existantes",
    tag: "FAIBLE POUR CIR",
    justification:
      "Le passage ressemble surtout à une interprétation globale ou à une preuve de contexte. Il ne suffit pas seul comme verrou principal.",
    sources: ["cir_final_precedent_2023.docx", "documents_projet_courant"],
    score: 42,
    frascati: 44,
    selected: false,
    scientificText:
      "Limits of transferring existing solutions to new operating conditions or technical constraints.",
    queries: ["technology transfer limitations operating constraints industrial system"],
  },
]

export const articles: Article[] = [
  {
    id: "a1",
    title: "Dynamic Stability and Vibration Control in Mechanical Systems",
    year: 2021,
    source: "Semantic Scholar",
    tag: "DIRECTEMENT LIÉ AU VERROU",
    score: 31,
    citations: 18,
    url: "semantic-scholar",
    verrouId: "stability",
    selected: true,
  },
  {
    id: "a2",
    title: "Experimental Modal Analysis for Industrial Rotating Assemblies",
    year: 2019,
    source: "Semantic Scholar",
    tag: "DIRECTEMENT LIÉ AU VERROU",
    score: 28,
    citations: 11,
    url: "semantic-scholar",
    verrouId: "stability",
    selected: true,
  },
  {
    id: "a3",
    title: "An In-Depth Study of Vibration Sensors for Condition Monitoring",
    year: 2024,
    source: "OpenAlex",
    tag: "JUSTE CONCEPT SCIENTIFIQUE",
    score: 17,
    citations: 81,
    url: "openalex",
    verrouId: "stability",
    selected: false,
  },
  {
    id: "a4",
    title: "Wear Mechanisms and Degradation Analysis in Mechanical Components",
    year: 2020,
    source: "OpenAlex",
    tag: "DIRECTEMENT LIÉ AU VERROU",
    score: 34,
    citations: 39,
    url: "openalex",
    verrouId: "wear",
    selected: true,
  },
  {
    id: "a5",
    title: "Tribological Behaviour of Components under Lubricated Sliding Conditions",
    year: 2019,
    source: "Semantic Scholar",
    tag: "JUSTE CONCEPT SCIENTIFIQUE",
    score: 24,
    citations: 55,
    url: "semantic-scholar",
    verrouId: "wear",
    selected: false,
  },
  {
    id: "a6",
    title: "Battery Thermal Management Systems for Electric Vehicles",
    year: 2024,
    source: "OpenAlex",
    tag: "À IGNORER — HORS SUJET",
    score: 5,
    citations: 12,
    url: "openalex",
    verrouId: "thermal",
    selected: false,
  },
  {
    id: "a7",
    title: "Heat Transfer and Cooling Performance under Variable Flow Conditions",
    year: 2021,
    source: "OpenAlex",
    tag: "JUSTE CONCEPT SCIENTIFIQUE",
    score: 22,
    citations: 18,
    url: "openalex",
    verrouId: "thermal",
    selected: false,
  },
]

export const evidences: Evidence[] = [
  {
    id: "e1",
    role: "Verrou",
    document: "releves_mesures_experimentales.xlsx",
    sourceCategory: "verrous_rnd_locaux",
    score: 76,
    text: "Le système présente un comportement instable dans certaines conditions d’essai, avec des écarts importants entre les mesures attendues et observées.",
  },
  {
    id: "e2",
    role: "Méthode",
    document: "rapport_essais_prototype_rev1.docx",
    sourceCategory: "methodes_locales",
    score: 96,
    text: "Une campagne d’essais a été définie afin de quantifier l’influence des paramètres de fonctionnement sur la performance du prototype.",
  },
  {
    id: "e3",
    role: "Résultat",
    document: "note_technique_performance_systeme.docx",
    sourceCategory: "resultats_locaux",
    score: 76,
    text: "Les résultats montrent une amélioration partielle de la performance, mais certains écarts persistent sous contraintes élevées.",
  },
  {
    id: "e4",
    role: "Limite",
    document: "analyse_defaillances_conditions_reelles.pdf",
    sourceCategory: "limites_locales",
    score: 87,
    text: "Les observations disponibles ne permettent pas encore d’expliquer entièrement l’origine de la dégradation constatée.",
  },
  {
    id: "e5",
    role: "Faux verrou rejeté",
    document: "synthese_options_techniques.docx",
    sourceCategory: "parametres_locaux",
    score: 99,
    text: "Le choix d’une alternative technique est conservé comme paramètre de conception, mais ne constitue pas seul un verrou R&D.",
  },
]

export const documentComparisons: DocumentComparison[] = [
  {
    id: "dc1",
    docA: "note_technique_performance_systeme.docx",
    docB: "comparatif_conditions_essais.pdf",
    type: "Même sujet technique",
    score: 86,
    differences: "Nouvelles mesures, nouvelles conditions d’essai et évolution des paramètres de performance.",
    impact: "Peut renforcer le verrou si le sujet est reformulé comme incertitude technique.",
    status: "Preuve renforcée",
  },
  {
    id: "dc2",
    docA: "synthese_options_techniques.docx",
    docB: "ancienne_etude_solution_similaire.docx",
    type: "Version proche / étude similaire",
    score: 78,
    differences: "Écarts sur les paramètres de conception, les contraintes et les hypothèses d’essai.",
    impact: "À vérifier pour distinguer contexte industriel et incertitude R&D.",
    status: "À vérifier",
  },
  {
    id: "dc3",
    docA: "analyse_defaillances_conditions_reelles.pdf",
    docB: "rapport_essais_prototype_rev1.docx",
    type: "Complément de preuve",
    score: 82,
    differences: "Le premier document décrit les dégradations, le second document apporte les essais et les mesures.",
    impact: "Renforce le verrou lié à la fiabilité ou à l’identification de cause.",
    status: "Évolution",
  },
]

export const cirComparisons: CirComparison[] = [
  {
    id: "cc1",
    element: "Verrou thermique",
    cirN1: "Optimisation de performance déjà abordée",
    current: "Nouvelles conditions d’essai, contraintes de température et mesures complémentaires",
    evolution: "Évolution",
    analysis: "Le sujet continue mais présente de nouvelles contraintes expérimentales.",
    action: "Valider s’il existe une nouvelle incertitude CIR pour l’année courante.",
  },
  {
    id: "cc2",
    element: "Fiabilité en conditions réelles",
    cirN1: "Problème déjà mentionné",
    current: "Nouvelles observations, nouvelles mesures et analyse des causes potentielles",
    evolution: "Récurrent",
    analysis: "Défendable si la cause technique reste incertaine et si les preuves de l’année courante sont nouvelles.",
    action: "Reformuler comme verrou lié à la cause technique ou à la dégradation en conditions réelles.",
  },
  {
    id: "cc3",
    element: "Nouvelle solution de conception",
    cirN1: "Non présent",
    current: "Nouveaux paramètres de conception, nouvelles contraintes et essais de validation",
    evolution: "Nouveau",
    analysis: "Sujet nouveau à investiguer, à rattacher à une incertitude technique mesurable.",
    action: "Envoyer vers EnnoScholar après validation consultant.",
  },
]

export const validationChecklist = [
  { id: "c1", label: "Verrou reformulé correctement", done: true },
  { id: "c2", label: "Preuves documentaires suffisantes", done: true },
  { id: "c3", label: "Articles scientifiques pertinents", done: false },
  { id: "c4", label: "Articles hors sujet masqués", done: true },
  { id: "c5", label: "État de l’art validé", done: false },
  { id: "c6", label: "Prêt pour rédaction CIR", done: false },
]

export const stateOfArtDraft = `Les travaux identifiés dans la littérature montrent que la stabilité dynamique, la maîtrise des paramètres de fonctionnement et l’analyse expérimentale des systèmes industriels restent des sujets sensibles lorsque les conditions réelles introduisent des écarts de performance difficiles à anticiper. Les articles directement liés au verrou de stabilité confirment que l’analyse modale, la caractérisation des comportements instables et la réduction des vibrations peuvent constituer des problématiques scientifiques et techniques proches du dossier étudié.

Pour le verrou lié à la fiabilité et à la dégradation en fonctionnement, les sources scientifiques relatives aux mécanismes d’usure, aux phénomènes de frottement et à l’analyse de défaillance apportent un contexte utile pour justifier la difficulté d’interprétation des pertes de performance. Ce texte reste un brouillon : le consultant doit sélectionner les articles à conserver et vérifier la cohérence avec les preuves du projet courant.`
