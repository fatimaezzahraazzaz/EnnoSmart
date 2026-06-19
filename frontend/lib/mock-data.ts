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
  name: "Fatima Ezzahra Azzaz",
  role: "Consultante CIR / Data Scientist",
  initials: "FA",
}

export const currentContext = {
  client: "Girodin",
  project: "TGM100",
  year: "2023",
  domain: "Génie mécanique",
  subdomains: ["Acoustique", "Mécanique des fluides", "Thermique"],
}

export const projects: Project[] = [
  { id: "girodin-tgm100-2023", client: "Girodin", project: "TGM100", year: "2023", status: "Validation consultant requise", activeAgent: "EnnoDiagnostic", updatedAt: "Aujourd’hui", risk: "Moyen", progress: 72 },
  { id: "enodev-aicode-2024", client: "Enodev", project: "AI-CODE", year: "2024", status: "Analyse terminée", activeAgent: "EnnoDiagnostic", updatedAt: "Hier", risk: "Faible", progress: 84 },
  { id: "biomed-genbiomed-2024", client: "Biomed", project: "GEN-BIOMED", year: "2024", status: "Recherche scientifique en cours", activeAgent: "EnnoScholar", updatedAt: "Il y a 2 jours", risk: "Moyen", progress: 58 },
  { id: "demo-valor-2024", client: "Client Démo", project: "Valorisation CIR", year: "2024", status: "Rapport prêt", activeAgent: "Aucun", updatedAt: "Il y a 5 jours", risk: "Faible", progress: 100 },
]

export const documents: DocumentItem[] = [
  { id: "doc-1", name: "CR_Soufflage_carter_TGM_INTERNE_FR_Rev1.docx", type: "Rapport test", origin: "Document brut", status: "Indexé", quality: "Bonne", passages: 31, size: "480 Ko" },
  { id: "doc-2", name: "Etude_réfrigérant_1er_étage_TGM100.docx", type: "Note technique", origin: "Document brut", status: "Indexé", quality: "Bonne", passages: 24, size: "320 Ko" },
  { id: "doc-3", name: "Relevés_de_vibrations_TMG_100.xlsx", type: "Tableur mesures", origin: "Tableur mesures", status: "Extrait", quality: "Moyenne", passages: 12, size: "92 Ko" },
  { id: "doc-4", name: "Analyse_des_segments_après_soufflage_carter.pdf", type: "Rapport analyse", origin: "Rapport test", status: "Indexé", quality: "Bonne", passages: 42, size: "2,1 Mo" },
  { id: "doc-5", name: "GIRODIN_TGM100_CIR-2022_VF.docx", type: "CIR N-1", origin: "CIR précédent", status: "Indexé", quality: "Bonne", passages: 78, size: "5,1 Mo" },
]

export const verrous: Verrou[] = [
  {
    id: "vibration",
    title: "Comportement instable ou non maîtrisé",
    tag: "PERTINENT POUR CIR",
    justification: "Difficulté liée aux vibrations fortes, au déséquilibre de poulie et à l’équilibrage dynamique du compresseur. Le sujet contient une incertitude technique réelle et des preuves documentaires.",
    sources: ["Relevés_de_vibrations_TMG_100.xlsx", "Synthèse_Contrepoids_etude_prix.docx"],
    score: 82,
    frascati: 68,
    selected: true,
    scientificText: "Vibration stability and dynamic balancing of a reciprocating compressor rotating assembly. Pulley imbalance, counterweight mass, static and dynamic balancing, acoustic and vibration behavior.",
    queries: ["reciprocating compressor crankshaft dynamic balancing vibration", "pulley imbalance counterweight reciprocating compressor", "torsional vibration rotating machinery compressor"],
  },
  {
    id: "thermal",
    title: "Maîtrise thermique / refroidissement",
    tag: "MOYEN POUR CIR",
    justification: "Sujet technique réel, mais à reformuler pour distinguer le verrou de la solution de réfrigérant testée. Les documents contiennent beaucoup de résultats et de méthodes.",
    sources: ["Etude_réfrigérant_1er_étage_TGM100.docx", "Comparatif_T_débit_eau.pdf"],
    score: 64,
    frascati: 60,
    selected: true,
    scientificText: "Heat transfer and cooling performance of the first stage of a high-pressure reciprocating compressor under water flow and pressure-drop constraints.",
    queries: ["reciprocating compressor heat transfer cooling water pressure drop", "compressor intercooler refrigerant thermal performance", "high pressure compressor temperature water flow"],
  },
  {
    id: "wear",
    title: "Fiabilité, usure ou dégradation en fonctionnement",
    tag: "PERTINENT POUR CIR",
    justification: "Incertitude liée à l’usure anormale des segments, à la perte d’étanchéité, au soufflage carter et aux conditions d’utilisation en service.",
    sources: ["Analyse_segments_soufflage_carter.pdf", "PV-EM_22-0167.pdf"],
    score: 79,
    frascati: 68,
    selected: true,
    scientificText: "Abnormal piston ring and cylinder liner wear in reciprocating compressors, blow-by, friction, sealing loss and operating-condition influence.",
    queries: ["piston ring cylinder liner wear blow-by reciprocating compressor", "tribology sealing loss piston ring compressor", "friction wear ring liner high pressure compressor"],
  },
  {
    id: "root-cause",
    title: "Identification de la cause racine",
    tag: "MOYEN POUR CIR",
    justification: "Problème intéressant, mais doit être relié à une incertitude technique plus précise pour éviter un verrou trop générique.",
    sources: ["PV-EM_22-0167.pdf", "Analyse_segments_soufflage_carter.pdf"],
    score: 61,
    frascati: 58,
    selected: true,
    scientificText: "Root cause identification for abnormal wear and sealing degradation in reciprocating compressor piston rings under service conditions.",
    queries: ["root cause abnormal wear piston ring compressor", "failure analysis piston ring cylinder liner friction", "sealing degradation compressor operating conditions"],
  },
  {
    id: "performance",
    title: "Performance insuffisante sous contrainte",
    tag: "MOYEN POUR CIR",
    justification: "Le sujet regroupe plusieurs contraintes et mesures. Il peut être utile mais doit être fusionné ou reformulé avec un verrou plus précis.",
    sources: ["CR_Soufflage_carter_TGM_INTERNE_FR_Rev1.docx", "Etude_réfrigérant_1er_étage_TGM100.docx"],
    score: 56,
    frascati: 54,
    selected: false,
    scientificText: "Performance loss under technical constraints in high-pressure reciprocating compressor systems.",
    queries: ["performance loss high pressure reciprocating compressor constraints"],
  },
  {
    id: "non-transfer",
    title: "Non-transférabilité des solutions existantes",
    tag: "FAIBLE POUR CIR",
    justification: "Le passage ressemble surtout à une interprétation globale ou à une preuve de contexte. Il ne suffit pas seul comme verrou principal.",
    sources: ["GIRODIN_TGM100_CIR-2022_VF.docx", "Documents projet 2023"],
    score: 42,
    frascati: 44,
    selected: false,
    scientificText: "Limits of transferring existing compressor solutions to current operating constraints.",
    queries: ["compressor solution transfer limitations operating constraints"],
  },
]

export const articles: Article[] = [
  { id: "a1", title: "Dynamic Balancing Modal Analysis and Vibration Suppressing Design for Reciprocating Compressor Crankshaft", year: 2012, source: "Semantic Scholar", tag: "DIRECTEMENT LIÉ AU VERROU", score: 31, citations: 4, url: "semantic-scholar", verrouId: "vibration", selected: true },
  { id: "a2", title: "Dynamic Balancing Mass Design for the Crank of Reciprocating Compressor", year: 2008, source: "Semantic Scholar", tag: "DIRECTEMENT LIÉ AU VERROU", score: 28, citations: 0, url: "semantic-scholar", verrouId: "vibration", selected: true },
  { id: "a3", title: "An In-Depth Study of Vibration Sensors for Condition Monitoring", year: 2024, source: "OpenAlex", tag: "JUSTE CONCEPT SCIENTIFIQUE", score: 17, citations: 81, url: "openalex", verrouId: "vibration", selected: false },
  { id: "a4", title: "Piston ring–cylinder liner friction and wear mechanisms in reciprocating machines", year: 2020, source: "OpenAlex", tag: "DIRECTEMENT LIÉ AU VERROU", score: 34, citations: 39, url: "openalex", verrouId: "wear", selected: true },
  { id: "a5", title: "Tribological behaviour of piston rings under lubricated sliding conditions", year: 2019, source: "Semantic Scholar", tag: "JUSTE CONCEPT SCIENTIFIQUE", score: 24, citations: 55, url: "semantic-scholar", verrouId: "wear", selected: false },
  { id: "a6", title: "Battery Thermal Management Systems for Electric Vehicles", year: 2024, source: "OpenAlex", tag: "À IGNORER — HORS SUJET", score: 5, citations: 12, url: "openalex", verrouId: "thermal", selected: false },
  { id: "a7", title: "Heat transfer in compressor intercoolers under variable cooling-water flow", year: 2021, source: "OpenAlex", tag: "JUSTE CONCEPT SCIENTIFIQUE", score: 22, citations: 18, url: "openalex", verrouId: "thermal", selected: false },
]

export const evidences: Evidence[] = [
  { id: "e1", role: "Verrou", document: "Relevés_de_vibrations_TMG_100.xlsx", sourceCategory: "verrous_rnd_locaux", score: 76, text: "Vibration extrêmement forte. Le problème vient du fait que la poulie est très déséquilibrée." },
  { id: "e2", role: "Méthode", document: "CR_Soufflage_carter_TGM_INTERNE_FR_Rev1.docx", sourceCategory: "methodes_locales", score: 96, text: "Nous avons décidé de quantifier les débits d’air rejetés par les reniflards des compresseurs." },
  { id: "e3", role: "Résultat", document: "Etude_réfrigérant_1er_étage_TGM100.docx", sourceCategory: "resultats_locaux", score: 76, text: "L’amélioration de la réfrigération s’opère surtout sur le 1er et le 4ème étage. Le rapport d’efficacité passe de 0.59 à 0.63." },
  { id: "e4", role: "Limite", document: "PV-EM_22-0167.pdf", sourceCategory: "limites_locales", score: 87, text: "La microstructure ne semble donc pas en relation avec la problématique d’usure anormale des segments du lot N°2." },
  { id: "e5", role: "Faux verrou rejeté", document: "Synthèse_Contrepoids_etude_prix.docx", sourceCategory: "parametres_locaux", score: 99, text: "Trouver une alternative au plomb permettant l’équilibrage statique. Passage conservé comme paramètre/contexte, pas comme verrou principal." },
]

export const documentComparisons: DocumentComparison[] = [
  { id: "dc1", docA: "Etude_réfrigérant_1er_étage_TGM100.docx", docB: "TGM100_Comparatif_T_débit_eau.pdf", type: "Même sujet technique", score: 86, differences: "Nouvelles mesures de température, débit d’eau et pertes de charge.", impact: "Peut renforcer le verrou thermique s’il est reformulé comme incertitude.", status: "Preuve renforcée" },
  { id: "dc2", docA: "Synthèse_Contrepoids_etude_prix.docx", docB: "Masses_Contrepoids_TGM60.docx", type: "Version proche / étude similaire", score: 78, differences: "Écarts masse, centre de gravité, matériau et équilibrage.", impact: "À vérifier pour le verrou vibration / équilibrage.", status: "À vérifier" },
  { id: "dc3", docA: "Analyse_segments_soufflage_carter.pdf", docB: "CR_Soufflage_carter_TGM_INTERNE_FR_Rev1.docx", type: "Complément de preuve", score: 82, differences: "Le premier document explique l’usure, le second quantifie le soufflage.", impact: "Renforce le verrou usure / étanchéité.", status: "Évolution" },
]

export const cirComparisons: CirComparison[] = [
  { id: "cc1", element: "Verrou thermique", cirN1: "Optimisation refroidissement déjà abordée", current: "Nouveaux réfrigérants 1er étage, contraintes débit d’eau et température", evolution: "Évolution", analysis: "Le sujet continue mais présente de nouvelles contraintes expérimentales.", action: "Valider s’il existe une nouvelle incertitude CIR 2023." },
  { id: "cc2", element: "Soufflage carter / segmentation", cirN1: "Problème connu", current: "Nouvelles mesures, usure segments, perte d’étanchéité", evolution: "Récurrent", analysis: "Défendable si la cause racine reste incertaine et si les preuves 2023 sont nouvelles.", action: "Reformuler comme verrou cause racine / usure en conditions réelles." },
  { id: "cc3", element: "Contrepoids sans plomb", cirN1: "Non présent", current: "Équilibrage, masse, centre de gravité, vibration", evolution: "Nouveau", analysis: "Sujet nouveau à investiguer, lié aux contraintes d’équilibrage et vibrations.", action: "Envoyer vers EnnoScholar après validation consultant." },
]

export const validationChecklist = [
  { id: "c1", label: "Verrou reformulé correctement", done: true },
  { id: "c2", label: "Preuves documentaires suffisantes", done: true },
  { id: "c3", label: "Articles scientifiques pertinents", done: false },
  { id: "c4", label: "Articles hors sujet masqués", done: true },
  { id: "c5", label: "État de l’art validé", done: false },
  { id: "c6", label: "Prêt pour rédaction CIR", done: false },
]

export const stateOfArtDraft = `Les travaux identifiés dans la littérature montrent que l’équilibrage dynamique et la stabilité vibratoire des ensembles de compression alternatifs restent des sujets sensibles, en particulier lorsque la géométrie de l’arbre, la masse des contrepoids et les conditions de fonctionnement introduisent des déséquilibres difficiles à compenser. Les articles directement liés au verrou vibration confirment que le dimensionnement du balourd, l’analyse modale et la réduction des vibrations du vilebrequin constituent des problématiques scientifiques et techniques proches du cas TGM100.\n\nPour le verrou usure / segmentation, les sources scientifiques relatives aux contacts piston-segment-chemise et aux phénomènes de frottement apportent un contexte utile pour justifier la difficulté d’interprétation des pertes d’étanchéité et du soufflage carter. Ce texte reste un brouillon : le consultant doit sélectionner les articles à conserver et vérifier la cohérence avec les preuves du projet 2023.`
