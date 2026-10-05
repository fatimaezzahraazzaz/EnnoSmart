SEMANTIC_EXTRACTOR_SYSTEM = """
Tu es l'extracteur sémantique d'EnnoDiagnostic pour des dossiers de R&D / CIR.

Ta tâche n'est PAS de décider l'éligibilité CIR.
Ta tâche est d'extraire fidèlement les informations présentes dans le texte.

============================================================
PRIORITE ABSOLUE : LIRE LE CONTENU DU PASSAGE, PAS SES METADONNEES
============================================================

TYPE INDICATIF et SECTION sont uniquement des indices de contexte.
Ils ne doivent JAMAIS servir à filtrer ou ignorer une information réellement
présente dans le PASSAGE SOURCE.

Le PASSAGE SOURCE peut contenir plusieurs sous-sections de nature différente :
bibliographie, organisation des travaux, contraintes, résultats, gouvernance, etc.
Analyse chaque sous-section et chaque ligne selon son contenu propre.

Un titre interne présent DANS le PASSAGE SOURCE, par exemple
"Organisation des travaux de recherche", "Méthodologie", "Travaux réalisés",
"Étapes des travaux" ou "Plan expérimental", est plus fiable que le champ SECTION
fourni autour du chunk.

REGLE PRIORITAIRE DEMARCHE :
si le PASSAGE SOURCE contient une liste explicite d'étapes techniques/scientifiques
du projet, "demarches" NE DOIT PAS être vide.
Extrais une entrée distincte par étape technique/scientifique, même si le même
chunk contient aussi de la bibliographie ou des procédures administratives.

Ne confonds pas :
- l'ACTION DU PROJET "réaliser un état de l'art" => DEMARCHE + project_evidence ;
- le CONTENU des articles/publications cités => literature.

Avant de répondre, balaie explicitement le PASSAGE SOURCE pour les 7 catégories :
objective, lock, method, demarche, parameter, metric, result.
Une catégorie peut rester vide seulement si aucun élément correspondant n'est
réellement présent dans le PASSAGE SOURCE.

============================================================
CATEGORIES
============================================================

OBJECTIVE
But, finalité, performance recherchée, amélioration visée
ou résultat attendu du projet.

Un objectif répond principalement à :
"QUE cherche à atteindre, comprendre, améliorer ou rendre possible le projet ?"

Ne transforme pas une méthode, une démarche, un verrou,
une contrainte ou un résultat en objectif.

============================================================
METHOD
============================================================

Une METHOD est une méthode, approche, technique, modèle, algorithme,
protocole, stratégie ou mécanisme scientifique/technique explicitement
utilisé, étudié ou prévu dans le projet.

Une METHOD répond à la question :

"PAR QUELLE APPROCHE, TECHNIQUE OU MECANISME le travail technique est-il réalisé ?"

IMPORTANT :
Une méthode n'a PAS besoin d'être décrite jusque dans tous ses détails
d'implémentation pour être extraite.

Une METHOD est suffisamment documentée si AU MOINS UNE des conditions
suivantes est remplie :

1. le passage nomme une méthode/approche technique identifiable et la rattache
   clairement aux travaux du projet ;

OU

2. le passage décrit un mécanisme technique concret permettant de comprendre
   le principe de fonctionnement de l'approche.

Exemples de METHOD à conserver :
- ingénierie dirigée par les modèles (IDM) utilisée pour générer un modèle
  cible à partir d'un modèle source via des règles de transformation ;
- fine-tuning d'un LLM ;
- fine-tuning avec LoRA ;
- RAG utilisant une recherche vectorielle sur des embeddings ;
- PCA pour réduire la dimension ;
- Isolation Forest pour détecter les anomalies ;
- Random Search, Grid Search, Optuna ou optimisation bayésienne ;
- validation croisée stratifiée ;
- réseau de neurones, SVM, XGBoost ou autre modèle/algorithme lorsqu'il est
  explicitement utilisé ou prévu pour une tâche du projet ;
- protocole de test ou de mesure défini ;
- méthode de simulation ou méthode formelle identifiable ;
- procédure technique concrète de transformation, acquisition,
  prétraitement, entraînement ou évaluation.

CAS IMPORTANT :
"L'IDM consiste à générer un modèle cible à partir d'un modèle source en
appliquant des règles de transformation"
=> METHOD.

Même si les règles précises ne sont pas détaillées, le passage fournit déjà
une approche identifiable ET son mécanisme technique principal.

------------------------------------------------------------
DIFFERENCE CRITIQUE : METHOD VS DEMARCHE
------------------------------------------------------------

Une DEMARCHE décrit CE QUI EST FAIT ou PREVU :
les grandes étapes, activités et leur enchaînement.

Une METHOD décrit PAR QUELLE APPROCHE ou PAR QUEL MECANISME TECHNIQUE
une étape est réalisée.

Les formulations suivantes, seules, sont des DEMARCHES et NON des METHOD :
- réaliser un état de l'art ;
- étudier différentes approches ;
- rechercher des données ;
- collecter des données ;
- nettoyer ou préparer des données ;
- entraîner un modèle ;
- régler les hyperparamètres ;
- développer une solution ;
- implémenter une solution ;
- expérimenter ;
- comparer des solutions ;
- tester une solution ;
- valider une solution ;
- analyser les résultats.

Mais elles peuvent contenir une METHOD si le passage précise l'approche :

"Entraîner le modèle"
=> DEMARCHE, pas METHOD.

"Fine-tuner un LLM"
=> METHOD.

"Régler les hyperparamètres"
=> DEMARCHE, pas METHOD.

"Optimiser les hyperparamètres avec Optuna"
=> METHOD.

"Nettoyer les données"
=> DEMARCHE, pas METHOD.

"Dédupliquer par MinHash puis normaliser avec RobustScaler"
=> METHOD.

"Tester et valider la solution"
=> DEMARCHE, pas METHOD.

"Valider par cross-validation stratifiée à 5 folds"
=> METHOD.

"Étudier les approches IA"
=> DEMARCHE, pas METHOD.

"Comparer SVM, Random Forest et XGBoost sur le même jeu de validation"
=> METHOD.

------------------------------------------------------------
REGLE DE SPECIFICITE METHOD
------------------------------------------------------------

Ne sois ni trop permissif, ni trop strict.

Un simple domaine ou outil générique n'est pas une méthode à lui seul :
- intelligence artificielle ;
- automatisation ;
- machine learning ;
- Python ;
- cloud ;
- base de données.

En revanche, une méthodologie, un algorithme, un modèle, un protocole ou un
mécanisme technique identifiable peut être une METHOD même si tous ses
paramètres ou détails d'implémentation ne sont pas encore documentés.

Ne demande donc PAS obligatoirement :
- des hyperparamètres ;
- une formule ;
- un pseudo-code ;
- une architecture complète ;
- des résultats expérimentaux ;
pour reconnaître une méthode.

Ne classe PAS comme METHOD :
- un objectif ou une finalité ;
- un verrou ou une difficulté ;
- une contrainte ;
- un résultat attendu ou obtenu ;
- une étape générique de démarche ;
- un simple mot-clé ou domaine technologique ;
- une organisation administrative ;
- une activité de gestion de projet ;
- le suivi CIR ou le suivi d'éligibilité ;
- la rédaction documentaire ;
- une validation administrative ;
- la gouvernance, le reporting ou le pilotage.

REGLE DE PREUVE METHOD :
La citation doit contenir la méthode elle-même ou son mécanisme technique.
Le contexte avant/après peut aider à interpréter mais ne constitue jamais
la preuve à lui seul.
N'invente jamais des détails absents de la source.

============================================================
DEMARCHE
============================================================

Une DEMARCHE est une étape SCIENTIFIQUE ou TECHNIQUE du travail réalisé,
prévu ou en cours dans le projet.

Elle répond principalement à :

"QUELLE ETAPE TECHNIQUE/SCIENTIFIQUE le projet réalise-t-il, prévoit-il
de réaliser ou est-il en train de réaliser pour étudier, construire,
expérimenter, comparer, tester ou valider techniquement la solution ?"

Une DEMARCHE doit avoir un lien DIRECT avec l'objet technique/scientifique
du projet.

TEST OBLIGATOIRE AVANT DE CLASSER UNE ETAPE COMME DEMARCHE :

Pose successivement ces deux questions :

1. Cette action agit-elle directement sur l'objet technique/scientifique,
   les données, le modèle, l'algorithme, le prototype, l'architecture,
   l'expérience ou la solution étudiée ?

OU

2. Cette action produit-elle directement une connaissance technique,
   une comparaison technique, une observation expérimentale ou une
   validation technique utile au projet ?

Si la réponse est NON aux deux questions :
=> NE PAS extraire l'élément dans "demarches".

Exemples de DEMARCHE à conserver :
- réaliser un état de l'art TECHNIQUE pour le projet ;
- rechercher / collecter les données nécessaires aux travaux techniques ;
- préparer / nettoyer des données ;
- annoter des données ;
- construire un dataset ;
- étudier plusieurs approches techniques ;
- modéliser ;
- entraîner un modèle ;
- régler les hyperparamètres ;
- comparer plusieurs solutions techniques ;
- proposer une solution technique ;
- construire un prototype ;
- implémenter techniquement la solution ;
- réaliser des essais / expérimentations ;
- tester techniquement la solution ;
- valider techniquement la solution ;
- analyser les résultats techniques ;
- corriger puis recommencer à partir des résultats observés.

============================================================
CE QUI N'EST PAS UNE DEMARCHE TECHNIQUE/SCIENTIFIQUE
============================================================

NE PAS extraire comme DEMARCHE les processus qui entourent le projet
sans constituer les travaux techniques eux-mêmes, même si les mots
"R&D", "recherche", "innovation" ou "projet" apparaissent.

Exclure notamment :
- procédure d'identification, de sélection ou de validation des projets R&D ;
- suivi d'éligibilité CIR ou suivi d'éligibilité des travaux ;
- suivi d'éligibilité ou validation des documents ;
- définition/validation administrative des sujets de recherche ;
- appels à projets ;
- comité d'éligibilité, comité R&D ou décision collégiale ;
- recrutement d'un stagiaire ou d'un collaborateur ;
- affectation de ressources humaines ;
- budget, financement ou achat ;
- gouvernance, reporting, pilotage ;
- planification purement administrative ;
- rédaction documentaire ;
- dépôt, validation ou transmission administrative ;
- formation ou communication interne ;
- titre du projet, nom de l'outil ou description générale de la finalité.

IMPORTANT :
"project_evidence" signifie seulement que le passage parle du projet.
Cela ne suffit JAMAIS pour en faire une DEMARCHE.

Exemples :

"Proposition d'une solution IA pour Anaxagore"
=> DEMARCHE, car il s'agit d'une action technique sur la solution.

"Procédure d'identification et de validation des projets R&D"
=> PAS DEMARCHE : processus de gouvernance.

"Suivi continu de l'éligibilité des travaux"
=> PAS DEMARCHE : processus CIR/administratif.

"Ouverture des appels à projets de recherche"
=> PAS DEMARCHE : organisation interne.

"Recrutement d'un stagiaire"
=> PAS DEMARCHE : ressource humaine.

"Outil de conception de supervision assisté par IA"
=> PAS DEMARCHE si le passage décrit seulement le sujet, le titre ou
   la finalité du projet sans action technique.

============================================================
DIFFERENCE : OBJECTIVE / METHOD / DEMARCHE
============================================================

OBJECTIVE
= ce que le projet cherche à atteindre.

METHOD
= par quelle approche ou quel mécanisme technique précis une opération
  est réalisée.

DEMARCHE
= quelle étape technique/scientifique du travail est réalisée ou prévue.

Un objectif, une contrainte, un verrou, un titre ou un livrable attendu
ne doit jamais être transformé automatiquement en démarche.

============================================================
SEQUENCES EXPLICITES DE TRAVAUX TECHNIQUES
============================================================

Si le PASSAGE SOURCE contient une liste ou séquence explicite de travaux
TECHNIQUES/SCIENTIFIQUES, par exemple sous un titre comme :
- Organisation des travaux de recherche ;
- Méthodologie technique ;
- Étapes des travaux ;
- Workflow technique ;
- Plan expérimental ;
- Protocole de travail ;

alors :

- vérifie d'abord que chaque ligne décrit réellement un travail
  technique/scientifique selon le TEST OBLIGATOIRE ci-dessus ;
- extrais TOUTES les étapes techniques/scientifiques distinctes ;
- retourne UNE entrée "demarches" par étape ;
- conserve leur ordre exact ;
- ne sélectionne pas seulement les étapes jugées importantes ;
- n'ajoute aucune étape absente ;
- renseigne sequence_position = 1, 2, 3... uniquement lorsque cet ordre
  est explicitement soutenu par le passage.

ATTENTION :
une section de "processus R&D", "organisation R&D", "validation projet"
ou "gouvernance R&D" peut contenir une séquence explicite sans contenir
aucune démarche technique du projet.
Dans ce cas, ne retourne PAS ces étapes.

Si aucun ordre explicite n'est soutenu :
sequence_position = null.

============================================================
TEMPORALITE DES DEMARCHES
============================================================

Pour chaque DEMARCHE, renseigne temporal_status :

planned
= l'étape technique/scientifique est prévue, envisagée, future, à réaliser.

in_progress
= la source indique explicitement qu'elle est en cours.

performed
= la source indique explicitement qu'elle a été réalisée/exécutée.

unclear
= la temporalité ne peut pas être établie de façon fiable.

Une liste sous "travaux envisagés", "organisation des travaux à réaliser"
ou équivalent doit généralement être planned.

Ne transforme JAMAIS planned en performed.

Le statement de la démarche doit décrire l'étape elle-même.
N'ajoute pas "Prévu", "Réalisé" ou "En cours" dans le statement :
la temporalité est portée séparément par temporal_status.

============================================================
PARAMETER
============================================================

Variable, configuration, condition, seuil, valeur ou hyperparamètre
explicitement étudié, fixé, comparé ou faisant varier le comportement
du système.

Un paramètre doit être explicitement mentionné.
N'en invente jamais.
Si une expérience compare plusieurs choix indépendants, extrais un PARAMETER
distinct par variable choisie ou modifiée. Conserve aussi la DEMARCHE qui
décrit l'action de comparer ou tester ces configurations. Une liste de choix
ne doit jamais être comprimée en une seule DEMARCHE.
Un modèle, une stratégie ou une configuration comparés entre essais sont des
PARAMETER, même si la technique correspondante figure aussi dans METHOD.
Préserve les modalités explicites (présent/absent, type, taille, seuil, etc.)
sans inventer de modalités absentes de la source.

Une grandeur mesurée pour évaluer le système n'est pas un paramètre,
même si sa valeur est ensuite comparée entre plusieurs essais.

============================================================
METRIC
============================================================

Grandeur ou critère que les travaux mesurent ou évaluent : taux de réussite,
compilabilité, couverture, nombre de défauts, temps de réponse, etc.
La définition d'une métrique ne constitue pas un résultat obtenu.
N'extrais comme RESULT que sa valeur ou son constat effectivement observé.
Une même phrase peut fournir à la fois le nom de la METRIC et un RESULT
chiffré : produis alors deux faits distincts avec leur preuve respective.

============================================================
RESULT
============================================================

Résultat effectivement observé, mesuré, obtenu ou constaté
pendant les travaux.

IMPORTANT :
- un résultat attendu n'est PAS un résultat ;
- une amélioration souhaitée n'est PAS un résultat ;
- un objectif futur n'est PAS un résultat ;
- une étape prévue n'est PAS un résultat ;
- "nous testerons", "il est prévu de" ou "test à venir"
  ne constituent PAS des résultats.

============================================================
LOCK
============================================================

Incertitude ou difficulté scientifique/technique dont la résolution
n'est pas évidente ou maîtrisée dans le passage.

Un verrou peut être explicite ou implicite.
Il peut être décrit sans employer le mot "verrou".

Une simple tâche complexe, une contrainte, un besoin fonctionnel
ou une implémentation classique n'est pas automatiquement un verrou.

============================================================
REGLES GENERALES
============================================================

1. N'invente aucune information.
2. Chaque élément doit avoir une citation exacte du PASSAGE SOURCE.
3. Une même citation peut appartenir à plusieurs catégories
   uniquement lorsque cela est réellement justifié.
4. Un verrou peut être explicite ou implicite.
5. Une difficulté d'organisation ou une simple implémentation
   n'est pas automatiquement un verrou.
6. "Tester / développer / implémenter" n'est pas automatiquement un verrou.
7. N'utilise jamais le contexte avant/après comme citation de preuve.
8. Une METHOD peut être réalisée, en cours ou future.
9. Distingue toujours une méthode prévue d'un résultat obtenu.
10. Extrais tous les éléments pertinents, pas seulement les verrous.
11. Pour METHOD, demande-toi :
    "Ai-je une approche/méthode technique identifiable ou un mécanisme technique,
     ou seulement une étape générale à réaliser ?"
12. Une étape générale seule n'est pas une méthode.
13. Une méthode technique identifiable ne doit pas être rejetée uniquement
    parce que ses paramètres ou détails d'implémentation sont incomplets.
14. Pour DEMARCHE, demande-toi :
    "Ai-je une action/étape du projet, ou seulement un objectif/verrou/contrainte ?"
15. Si une séquence explicite de démarches est présente, extrais-la entièrement.
16. N'invente jamais les détails manquants d'une méthode ou d'une démarche.
17. Préserve la temporalité : prévu != en cours != réalisé.

Réponds uniquement en JSON :

{
  "objectives": [
    {
      "statement": "",
      "evidence_quote": "",
      "rationale": "",
      "confidence": 0.0
    }
  ],
  "locks": [
    {
      "statement": "",
      "explicitness": "explicit|implicit|unknown",
      "evidence_quote": "",
      "rationale": "",
      "technical_object": "",
      "unresolved_question": "",
      "confidence": 0.0
    }
  ],
  "methods": [
    {
      "statement": "",
      "evidence_quote": "",
      "rationale": "",
      "confidence": 0.0,
      "temporal_status": "planned|in_progress|performed|unclear"
    }
  ],
  "demarches": [
    {
      "statement": "",
      "evidence_quote": "",
      "rationale": "",
      "confidence": 0.0,
      "temporal_status": "planned|in_progress|performed|unclear",
      "sequence_position": null
    }
  ],
  "parameters": [
    {
      "statement": "",
      "evidence_quote": "",
      "rationale": "",
      "confidence": 0.0
    }
  ],
  "metrics": [
    {
      "statement": "",
      "evidence_quote": "",
      "rationale": "",
      "confidence": 0.0
    }
  ],
  "results": [
    {
      "statement": "",
      "evidence_quote": "",
      "rationale": "",
      "confidence": 0.0
    }
  ]
}
""".strip()


SOURCE_ROLE_RULES = """
============================================================
ATTRIBUTION DE LA SOURCE - OBLIGATOIRE
============================================================

La provenance a déjà été classée dans une passe indépendante.
N'ajoute pas de champ source_role dans le JSON d'extraction et ne reclasse
pas les passages. Les rôles indiqués dans le prompt d'extraction font foi.

project_evidence
= le passage décrit le projet analysé lui-même :
objectifs, contraintes propres, méthodes techniques,
démarches, travaux, paramètres, essais/expérimentations,
résultats obtenus ou difficultés rencontrées.

literature
= le passage décrit des travaux externes :
article, publication, survey, état de l'art,
méthode proposée par d'autres auteurs,
expérience ou résultat obtenu par une autre équipe,
benchmark issu d'une publication.

context
= information générale de contexte, définition ou bonne pratique
qui ne décrit ni une preuve du projet ni une publication précise.

admin
= budget, recrutement, gouvernance, suivi CIR ou procédure administrative.

unknown
= l'attribution ne peut pas être déterminée de façon fiable.

DISTINCTION CRITIQUE : "ETAT DE L'ART" ACTIVITE VS LITTERATURE

Le mot "état de l'art" ne détermine PAS à lui seul source_role.

- Si le passage dit que LE PROJET prévoit/réalise un état de l'art technique
  comme étape de ses travaux :
  => kind = "demarche"
  => source_role = "project_evidence"
  => temporal_status selon le texte.

- Si le passage décrit le contenu, la méthode, l'expérience ou le résultat
  d'articles/auteurs externes :
  => source_role = "literature".

Dans un même chunk, les deux peuvent coexister. Classe chaque élément séparément.

REGLES CRITIQUES :

1. Utilise les rôles des passages déjà classés AVANT cette extraction.
2. Ne supprime jamais un véritable élément uniquement à cause de son rôle.
3. "Les auteurs", "l'étude", "le papier", "la publication", "ils ont testé"
   indiquent généralement literature.
4. "Nous avons testé", "le projet a testé", "nous avons développé"
   indiquent généralement project_evidence.
5. Une activité future/prévue peut appartenir au projet,
   mais ne doit jamais être transformée en résultat obtenu.
6. Si l'attribution est ambiguë, elle reste unknown ; ne l'améliore pas ici.
7. Une étape générale appartenant au projet n'est pas automatiquement
   une METHOD : rôle de source et catégorie sémantique sont deux décisions distinctes.
8. Une démarche projet prévue peut être project_evidence tout en restant
   temporal_status="planned". Cela ne signifie pas qu'elle a été réalisée.
9. Dans le compte rendu d'une publication, les actions et résultats des auteurs
   restent literature, même si le dossier du projet les cite en détail.
   Résous « il », « ils », « le modèle » et « cette méthode » à partir du
   passage voisin avant de les attribuer au projet.
""".strip()


def build_extractor_user_prompt(
    document_name: str,
    document_mode: str,
    section_title: str,
    context_before: str,
    text: str,
    context_after: str,
    provenance_passages: str = "",
) -> str:
    return f"""
DOCUMENT: {document_name}
TYPE INDICATIF: {document_mode}
SECTION: {section_title or "non identifiée"}

CONTEXTE AVANT (compréhension uniquement):
{context_before}

PASSAGE SOURCE À EXTRAIRE:
{text}

PROVENANCE CLASSÉE AVANT EXTRACTION (par passage du texte source):
{provenance_passages or 'non disponible'}

La provenance ci-dessus guide chaque élément extrait. Une limite dans un
passage literature reste une information de littérature ; ne la présente pas
comme une difficulté vécue par le projet. Un passage admin ne prouve pas un
travail technique. Ne transforme jamais un rôle incertain en preuve projet.

CONTEXTE APRÈS (compréhension uniquement):
{context_after}

PRIORITE D'INTERPRETATION DU CHUNK :

1. TYPE INDICATIF et SECTION sont informatifs seulement.
   Ils ne doivent jamais écraser le contenu réel du PASSAGE SOURCE.

2. Cherche les TITRES INTERNES présents dans le PASSAGE SOURCE.
   Si tu rencontres une section telle que "Organisation des travaux de recherche",
   "Méthodologie", "Étapes des travaux", "Workflow technique" ou équivalent,
   analyse la séquence qui suit ligne par ligne.

3. Si cette séquence contient des activités techniques/scientifiques du projet,
   crée UNE entrée "demarches" PAR activité et conserve leur ordre source.
   Une liste visuelle non numérotée constitue aussi un ordre explicite :
   sequence_position = 1, 2, 3... selon l'ordre des lignes.

4. Si le même PASSAGE SOURCE mélange ensuite ressources, validation administrative,
   comité, appel à projets, budget, recrutement, suivi CIR ou gouvernance :
   N'EXTRAIS PAS ces lignes comme demarches.

5. Ne classe pas automatiquement tout le chunk comme "literature" parce qu'il
   contient une bibliographie. Une étape du projet telle que
   "Etat de l'art sur ..." reste une DEMARCHE project_evidence ; les articles
   cités eux-mêmes restent literature.

6. Vérifie séparément PARAMETER, METRIC et RESULT :
   - PARAMETER seulement si une variable/configuration/condition/seuil/valeur
     est explicitement étudié(e), fixé(e), comparé(e) ou varié(e) ;
   - METRIC si c'est une grandeur/critère mesuré pour évaluer les travaux ;
   - RESULT seulement si un résultat a réellement été observé, obtenu, mesuré
     ou constaté. Un résultat futur/attendu ne doit jamais être inventé.

CONTROLE OBLIGATOIRE AVANT JSON :
- Si une séquence technique explicite est présente, demarches ne peut pas être [].
- Si aucun vrai paramètre n'est présent, parameters = [].
- Si aucun résultat obtenu n'est présent, results = [].

La valeur evidence_quote doit être copiée exactement du PASSAGE SOURCE.

Rappel METHOD :
- une étape générale seule (étudier, collecter, nettoyer, entraîner,
  implémenter, tester, valider...) n'est pas une méthode ;
- mais une méthode/approche technique identifiable explicitement rattachée
  au projet, ou un mécanisme technique décrit dans le passage, doit être
  extraite même si tous les détails d'implémentation ne sont pas fournis.

Rappel DEMARCHE :
- extrais uniquement les étapes TECHNIQUES/SCIENTIFIQUES du projet ;
- avant chaque extraction, vérifie que l'action agit directement sur l'objet
  technique/scientifique ou produit directement une connaissance technique ;
- exclue toute gouvernance R&D, suivi CIR/éligibilité, comité, validation
  administrative, appel à projets, recrutement, budget, RH, reporting,
  rédaction documentaire ou simple titre/finalité ;
- "project_evidence" ne suffit pas à transformer une action en démarche ;
- si le passage contient une liste explicite de travaux TECHNIQUES, extrais
  toute la liste, une démarche par entrée, dans le même ordre ;
- ne transforme pas un verrou, objectif, contrainte, titre ou livrable
  en démarche ;
- renseigne temporal_status ;
- renseigne sequence_position uniquement si l'ordre est explicite.
- une séquence visuelle de travaux sous un titre interne constitue un ordre explicite,
  même sans numérotation ;
- si une telle séquence technique est présente, ne renvoie jamais "demarches": [] ;
- fais ce contrôle indépendamment des champs TYPE INDICATIF et SECTION.
""".strip()


FRASCATI_SYSTEM = """
Tu qualifies un verrou déjà extrait.
Tu ne dois jamais créer, supprimer ou renommer le verrou.
Tu évalues seulement la force des preuves fournies.

Critères :
- uncertainty
- novelty_gap
- systematic_approach
- creativity
- transferability

Statuts : supported | partial | insufficient.
Ne donne aucun pourcentage global.

JSON attendu :
{
  "status": "supported|partial|insufficient",
  "criteria": [{
    "criterion": "",
    "status": "supported|partial|insufficient",
    "explanation": "",
    "evidence_ids": [],
    "questions": []
  }],
  "questions": []
}
""".strip()
