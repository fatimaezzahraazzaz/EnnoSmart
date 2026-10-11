# EnnoScholar V2 — résolution locale des références, 11 octobre 2026

## Résultat

**236 tests réussis**, dont **54 nouveaux cas déterministes** ; un avertissement
Starlette/AnyIO préexistant. Aucun appel OpenAI réel, fournisseur scientifique réel,
redémarrage ou déploiement. Le processus backend actif n’a pas été interrompu.

## Cause trouvée

Il n’existait pas de resolver de référence utilisateur dans le catalogue de l’agent.
`get_articles` attendait des identifiants internes déjà connus de GPT. `read_article`
résolvait une URL/DOI par accès externe, sans commencer par le registre de session.
ActiveSources savait rapprocher certaines notices techniques, mais n’était pas un
outil permettant au modèle de chercher un titre ou un alias dans le registre complet.

Au début d’un tour, ActiveSources conserve au maximum 12 notices selon sa logique
existante. Une notice présente dans `session.articles` peut donc être absente de cette
fenêtre. L’instruction conversationnelle générale ne suffisait pas à retrouver son
identifiant ; GPT pouvait confondre « absent de la fenêtre active » et « absent de la
session ». Une interdiction de nouvelle recherche n’était pas non plus appliquée au
catalogue et au dispatch des outils par le backend.

Le code explique ce chemin d’échec ; aucune session réelle ni recherche réelle n’a
été relancée pour reproduire le test utilisateur.

## Fichiers

Existant modifié :

- `agents/EnnoScholarV2/agent.py` : nouvel outil local, résolution préalable ciblée,
  ajout de l’instruction de résolution, filtrage/contrôle des recherches interdites,
  inspection de la notice résolue et réponse de clarification/absence.

Nouveaux :

- `agents/EnnoScholarV2/article_reference.py` : resolver d’identité, reconnaissance
  d’interdiction explicite, préparation d’une référence et message de clarification.
- `tests/ennoscholar_v2/test_article_reference.py` : 54 cas, dont deux reproductions
  complètes recherche simulée → registre avec identifiant généré → follow-up sans
  nouvelle recherche → fulltext simulé → analyse avec preuves.
- Ce rapport.

Les fichiers de tests existants n’ont pas été modifiés.

## Mécanisme de résolution

`resolve_article_reference(reference, registry)` travaille uniquement sur le
registre complet fourni à `ScholarChatAgent.run`, qui provient de la session serveur.
Aucun accès réseau, nouvel article, changement de DOI, regroupement de travaux ou
score de pertinence scientifique.

Ordre de correspondance :

1. Identifiant exact de notice déjà enregistré.
2. DOI exact normalisé, y compris préfixe URL/DOI, casse et ponctuation externe.
   Un DOI présent seulement dans une URL DOI connue des métadonnées est aussi reconnu.
3. URL connue exacte normalisée : hôte, chemin et query, sans fragment ; le chemin
   garde sa casse. Les URLs connues des versions associées sont consultables.
4. Identifiant arXiv connu, avec normalisation de version, y compris URL abs/pdf/html.
5. Titre normalisé exact ou titre complet explicitement présent dans le message.
6. Alias évident : préfixe nommé court avant `:` du titre enregistré, avec marqueur
   typographique de nom/acronyme ou chiffres. Aucun alias scientifique inventé.
7. Titre proche, uniquement selon des critères d’identité conservateurs.

La normalisation de titre utilise Unicode, casse minuscule, espaces et suppression
des accents/ponctuations non distinctifs. Elle ne modifie pas les titres stockés.

Le rapprochement de titre abrégé requiert au moins six mots, 35 caractères, 75 % du
nombre de mots du titre original, la même ouverture de deux mots et les mêmes termes
contenant des chiffres. Les mots doivent rester dans l’ordre du titre connu. Une
petite faute de frappe peut être acceptée après cette ouverture stable : même nombre
de mots, similarité de caractères au moins 98 % et au plus une édition de caractère.
Ce calcul est une comparaison d’identité de titre, pas un ranking scientifique.
Les références trop courtes ou les substitutions de tâche/méthode ne sont pas
acceptées comme une recherche thématique.

Si plusieurs notices passent un critère, aucun « meilleur score » ne choisit à la
place de l’utilisateur : statut `ambiguous`, au maximum cinq titres/DOIs présentés
pour clarification. Les identifiants restent internes. Une référence peu sûre doit
être précisée par titre complet ou DOI.

## Intégration au chat

`resolve_message_reference` traite avant GPT une référence explicitement marquée
`article:`, `publication:` ou `paper:`, ainsi qu’une mention exacte fiable d’une
notice déjà connue. Les instructions placées après le titre sur la même ligne sont
séparées du titre lorsqu’elles commencent une nouvelle phrase d’instruction.

Une référence résolue est inspectée avec la méthode **déjà existante**
`ActiveSources.inspect`. La fenêtre reste limitée à 12 notices ; le candidat demandé
est visible, même s’il était hors des 12 actifs. Aucun remplacement du mécanisme
d’ActiveSources et aucune injection des 1 600 autres notices.

Pour les autres formulations, GPT dispose du nouvel outil
`resolve_article_reference(reference=...)`, avec instruction de l’appeler avant
d’affirmer qu’un article est absent. Son retour fournit un seul identifiant/notice
compacte ; le dispatch place cette notice dans la sélection active et ne conserve
pas une seconde copie de ses détails dans la sortie d’outil transmise au contexte.

La notice complète, ses abstracts, textes et analyses restent backend. L’analyse
scientifique, ses prompts, schémas et preuves sont inchangés. GPT continue de décider
si des détails, un fulltext ou une analyse sont utiles : aucun pipeline scientifique
obligatoire ajouté.

Un article résolu sans PDF garde son identité : `get_fulltext` reste disponible.
Le cache et les versions associées sont gérés par l’implémentation fulltext existante.
Si le texte n’est pas récupéré, l’analyse utilise l’abstract/métadonnées disponibles,
sans prétendre que l’article lui-même manque.

## Interdiction de recherche

`explicitly_no_new_search` reconnaît des interdictions explicites usuelles en
français/anglais, notamment la phrase exacte du test : « Ne fais aucune nouvelle
recherche ». Il examine le **message humain courant**, jamais les abstracts ni
une instruction copiée depuis l’historique d’un autre tour.

Pour ce tour :

- `search_scientific`, `web_search` et `find_similar_articles` sont retirés des outils
  envoyés à Responses ; la variante Web preview est aussi exclue.
- Le filtrage reste appliqué lorsque la phase de finition reconstruit son catalogue.
- Un appel de fonction interdit proposé malgré tout est marqué `skipped` avant
  le dispatch ; aucun fournisseur n’est invoqué et il ne consomme pas un appel
  scientifique exécuté.
- Le lecteur `read_article` ne peut pas contourner la restriction par un lookup
  externe : dans ce mode, il est routé vers la résolution locale de sa référence.
- `resolve_article_reference`, `get_articles`, `get_fulltext` et `analyze_articles`
  restent accessibles pour les notices connues, dans les budgets existants.

La récupération du texte d’un article identifié demeure permise. Le resolver
fulltext général et ses accès aux sources de versions connues ne sont pas modifiés.

Si aucune notice fiable n’existe et la recherche est interdite, la réponse est :

> Je n'ai pas cet article dans les sources de cette session.
> Fournissez le DOI, l'URL ou le PDF, ou autorisez-moi à le rechercher.

Le backend peut produire cette réponse ou une clarification directement pour une
référence explicite, ou après l’outil local. Aucun article_id demandé à l’utilisateur.

## Tests exécutés

```powershell
.\.venv\Scripts\python.exe -B -m pytest tests/ennoscholar_v2 -q
```

Résultat final : **236 passed, 1 warning in 61.61s**. Les 182 tests antérieurs restent
inchangés. Les 54 nouveaux cas couvrent :

- identifiant, titre exact, casse, ponctuation, léger abrègement, alias, DOI,
  URL, arXiv et versions ; ponctuation entourant le DOI et parenthèses légitimes ;
- URL PDF connue contenant un chemin DOI, DOI uniquement dans une URL DOI connue,
  et DOI inconnu qui ne peut pas être masqué par un titre déjà connu ;
- registre de 1 601 articles avec la référence hors sélection active, un seul
  candidat compact retourné et aucun changement du corpus ;
- rejet de références thématiques vagues, noms/tâches contradictoires, alias génériques
  et URLs invalides ; faute de frappe limitée et ambiguïté sans sélection silencieuse ;
- follow-up HTTP 200, PDF en cache sans accès réseau et `cache_hit=true`, absence
  de cache avec fulltext autorisé puis analyse et extrait de preuve exact ;
- filtrage de recherche, contrôle d’appels interdits proposés, phase de finition,
  lecteur externe routé localement, absence réelle et clarification ;
- récupération fulltext échouée avec article toujours identifié et analyse abstract ;
- restriction issue uniquement du tour humain courant et message de 20 000 caractères
  accepté sans modification des limites de contexte ;
- instructions suivant un titre abrégé sur la même ligne ;
- première recherche passant réellement par `register`, identifiant `art_...`
  généré, notice ensuite hors des 12 actifs et follow-up par titre exact/abrégé :
  une seule recherche au tour précédent, zéro recherche au follow-up, fulltext,
  analyse et preuves conservés.

Tous les modèles, fournisseurs et téléchargements sont simulés. Les tests vérifient
le contrat prompt/outils/backend, pas l’adhésion d’un GPT réel à la nouvelle instruction.
Le benchmark utilise un sujet synthétique de biologie ; aucun nom du benchmark
maillage n’est hardcodé dans le resolver.

## Backup et invariants

Backup initial avant modification d’`agent.py` :

`D:\EnnoSmart\.ennosmart_patch_backups\scholar_v2_article_reference_20261011_113132_672599`

Sous-dossiers de sauvegarde avant chaque révision des nouveaux fichiers :

- `revision_20261011_114250_664697`
- `revision_20261011_114601_453069`
- `revision_20261011_114747_307756`
- `revision_20261011_115007_486156`
- `revision_20261011_115326_903202`
- `revision_20261011_115917_188412`

Une correction d’encodage a remplacé un guillemet par son échappement Unicode pour
permettre l’inspection Windows-1252 d’un test ancien ; le caractère d’exécution
reste identique et ce test n’a pas été modifié.

Contrôle SHA-256 : `agent.py` est le seul fichier préexistant modifié, **779 autres
fichiers de la baseline sont inchangés**. Comparaison AST : `register`, `read_article`,
les anciennes branches d’`execute_tool`, les timeouts/retries/fallbacks de synthèse
restent identiques. Syntaxe des 45 fichiers Python V2/tests vérifiée ;
`git diff --check` réussi avec avertissements CRLF préexistants.

Fournisseurs, recherche scientifique, ranking, prompts scientifiques,
`compact_results`, ActiveSources, evidence IDs, provenance, analyse, limites de
contexte, V1, EnnoDiagnostic et frontend intacts. Aucun nouveau modèle, table ou cache
persistant. Le diff Git global préexistant du projet n’a pas été réécrit.

Travail terminé et arrêté, sans redémarrage ni déploiement.
