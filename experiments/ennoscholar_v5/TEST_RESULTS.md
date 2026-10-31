# Résultats hors ligne — 27 octobre 2026

**41 tests réussis, 0 échec, 0 test ignoré**, en 180,18 secondes pour la dernière suite complète. Suite limitée à cette expérience : aucune suite globale EnnoSmart, aucun lancement de backend/frontend.

Preuves locales : `.artifacts/tests.xml`, `.artifacts/comparison.json`, `.artifacts/process-restart.json`, `.artifacts/preservation.json`. Ces fichiers et les fixtures scientifiques privées restent ignorés par Git.

## Ce qui a effectivement été exécuté

| Vérification | Résultat démontré |
|---|---|
| État chimie réel | 10 demandés, 50 notices, 5 proposés, 5 manquants ; Li à lire localement ; Fang/Bisztyga à récupérer ; fiches pertinentes sauvegardées distinguées des fiches de mémoire hors sujet |
| Lecture et sauvegarde Li | Véritable lecteur V4 sur le texte archivé ; présentation des passages ; sauvegarde validée sur preuve connue ; intégrité des autres articles et sources préservée |
| Plus de six décisions utiles | Sept lectures paginées suivies d'une pause explicite : aucune finalisation imposée au sixième appel simulé |
| Reprise Stokes | Deux variantes : pages 21–22 avec `next_page=23`, et pages 21–29 avec extraction complète ; aucune récupération réseau |
| Conservation scientifique Stokes | Ancienne analyse et anciennes preuves inchangées pendant la reprise d'extraction ; la fiche reste `targeted_passages`, même quand l'extraction est complète |
| Méthodes mal étiquetées | Passages des pages 18–19 accessibles au lecteur exact malgré le libellé REFERENCES ; libellés non corrigés par le graphe |
| Rédacteur actuel | Le dossier de section V4 reçoit des affirmations sauvegardées et leurs preuves ; aucune rédaction LLM ni modification d'un manuscrit réel |
| Requêtes identiques | Un seul appel fournisseur simulé ; résultat réutilisé depuis le reçu ; aucun nouveau scoring |
| Requêtes différentes sans nouveau résultat | Pause de stagnation après deux opérations sans progrès pour le même objectif ; lectures locales et candidats restent en attente |
| PDF archivé/pages déjà extraites | Réutilisation locale ; complément explicitement motivé et pages restantes toujours autorisés |
| Autre question scientifique | Une relecture explicitement différente n'est pas assimilée au même besoin |
| Fiche identique/version différente | Reçu de sauvegarde réutilisable ; nouvelle version invalide la clé de réutilisation et doit repasser les contrôles V4 |
| Métadonnées en cache | Une analyse enrichie ou une nouvelle décision du consultant invalide l'ancien résultat de `get_article` |
| Récupération en échec | Le vrai récupérateur V4, sous transport HTTP simulé, conserve plusieurs 404 ; leur répétition identique ne redéclenche pas le téléchargement |
| Erreur temporaire | 429 simulé puis source XML alternative identifiée : document conservé avec DOI effectivement présent, sans fiche ni lecture déclarées automatiquement |
| Publication partielle | Publication des références vérifiées avec couverture insuffisante ; compteur cumulatif conservé ; reprise possible pour lire le texte restant |
| Bibliographie invalide | Refus V4 conservé ; le graphe ne déclare pas la référence valide |
| Preuve inconnue | Sauvegarde refusée par le validateur V4 ; analyse précédente intacte |
| Crash après commit d'outil | Réouverture du saver et récupération du reçu : aucun deuxième appel de lecture |
| Crash après réception de décision | Décision déjà reçue récupérée sans nouvel appel du modèle |
| Réponse fournisseur incertaine | Aucune relance automatique ni remise à zéro de la dépense possible |
| Erreur locale de contexte | Erreur avant API distinguée d'un appel fournisseur incertain ; reprise après correction locale sans fausse dépense |
| Budget global | Coût, tokens, temps ou outils épuisés : pause avant nouvelle opération ; une reprise ne remet pas le budget à zéro |
| Proche du plafond financier | Réservation synthétique dépassant le restant refusée avant l'appel simulé |
| Isolation | Autre utilisateur, projet ou session refusés avant accès à l'état expérimental |
| Ancien checkpoint | Refus de rejouer contre une révision scientifique plus récente non associée à un reçu connu |
| Taille des checkpoints | Aucun texte intégral, PDF ou extrait scientifique complet stocké dans chaque état du graphe |
| Modèle et instructions | `gpt-6.1-sol` conservé ; `SYSTEM` et `DISCOVERY_GUIDANCE` V4 transmis sans changement ; aucune limite fixe de tours dans le graphe |
| PostgreSQL | Import du saver officiel et refus d'un schéma non expérimental ; **aucun serveur PostgreSQL testé** |

## Reprise entre deux processus distincts

Les phases `compare_offline --phase start` et `--phase resume` ont été exécutées par deux processus Python séparés.

| Mesure | Premier processus | Second processus, reprise |
|---|---:|---:|
| Décisions simulées cumulées | 2 | 4 |
| Outils réellement exécutés | 1 | 2 |
| Preuves de Li effectivement présentées | 4 | 8 |
| Publications proposées | 5 | 5 |
| Publications encore manquantes | 5 | 5 |
| Dépense API nouvelle | 0 USD | 0 USD |

Cette expérience ne remet pas la science ni les compteurs à zéro et ne recommence pas le premier intervalle de lecture.

## Comparaison à décisions identiques

Même état scientifique archivé, mêmes décisions imposées, mêmes outils V4 et mêmes validateurs. La référence directe est un rejeu des **outils V4**, pas un nouveau run autonome de Sol. Les articles, analyses, preuves et snapshots obtenus sont strictement identiques dans les deux chemins ; le dossier du rédacteur reçoit le passage sauvegardé.

| Mesure | Chimie : outils V4 directs | Chimie : graphe | Stokes : outils V4 directs | Stokes : graphe |
|---|---:|---:|---:|---:|
| Outils exécutés | 2 | 2 | 3 | 3 |
| Décisions simulées | 3 | 3 | 4 | 4 |
| Durée locale | 7,328 s | 9,266 s | 13,984 s | 15,797 s |
| Caractères d'entrée cumulés | 83 378 | 99 162 | 120 049 | 126 303 |
| Appels API payants | 0 | 0 | 0 | 0 |

Le surcoût de cette mesure est d'environ 1,94 s en chimie et 1,81 s pour Stokes. Il comprend le graphe, la projection de progression et le dépôt scientifique de test. Un seul échantillon local ne mesure pas la latence PostgreSQL ni celle d'un run en ligne. Les durées des vrais runs V4 (387,984 s et 214,860 s) ne sont pas comparables à ces rejeux hors ligne.

Les entrées du graphe sont plus grandes à cause de la vue de travail, bornée à 5 000 caractères avant enveloppe JSON. L'égalité des nombres d'appels ne démontre donc pas un coût LLM identique ou inférieur. Les coûts historiques V4 sont 0,5849 et 0,391354 USD enregistrés ; le coût réel V5 avec Sol reste **non mesuré**.

| État scientifique | Avant rejeu | Après rejeu |
|---|---|---|
| Chimie | 5 proposés, 5 manquants, Li non lu | Toujours 5 proposés/5 manquants ; Li lu ; une fiche de test fondée sur un extrait exact sauvegardée ; Fang/Bisztyga restent à récupérer |
| Stokes | 20/29 pages extraites, `next_page=21` | 29/29 pages extraites localement ; `next_page=null` ; nouvelle lecture et enrichissement de test possibles ; analyse toujours ciblée |

Les fiches ajoutées dans ce comparatif sont des **fixtures d'extraits exacts**, non des interprétations scientifiques produites par Sol. Le test ne prouve pas une meilleure comparaison scientifique, ni la découverte automatique des cinq articles manquants.

Plus grand état de checkpoint sérialisé en JSON : 88 159 octets en chimie et 7 445 pour Stokes, contre respectivement 6 750 060 et 1 321 108 octets pour les sessions scientifiques de test. Il ne s'agit pas de la taille totale des historiques du saver. Les textes ne sont pas recopiés dans chaque checkpoint.

## Préservation et limites

- Les 58 fichiers du paquet scientifique V4 sont identiques à leurs empreintes initiales, dans V4 active et dans sa copie expérimentale.
- Les quatre enregistrements audités (deux runs et deux sessions) ont les mêmes empreintes SHA-256 qu'au début ; leurs analyses et références sont préservées.
- Le PDF Stokes original et sa référence expérimentale conservent l'empreinte `7c5a6ac509b734b9b92fd51cbef2d659136fc2fee521c67126197b012f6cdd69`.
- L'environnement V4 conserve LangGraph 1.2.11 et checkpoint 4.2.0 ; les dépendances supplémentaires de l'expérience sont isolées.
- Aucun fichier de clé, aucune API scientifique ou LLM, aucun téléchargement documentaire réel, aucun plan ou manuscrit réel n'ont été modifiés ou utilisés en écriture.
- Aucun reset, commit, push, déploiement ou démarrage d'application.

Restent à valider : serveur PostgreSQL réel, autorisations et concurrence dans le service, intégration du registre budgétaire de production, streaming et Web natif, Sol autonome, pertinence/recall des publications, qualité des analyses et coût facturé. La déduplication sémantique et la suffisance scientifique restent des jugements de Sol ; une pause de stagnation n'établit pas l'absence de travaux pertinents.

Le prototype conserve une limite technique de récursion LangGraph de 1 000 supersteps, distincte d'un nombre de tours scientifiques. Le traitement convivial de ce garde-fou exceptionnel et les essais de concurrence font partie des prérequis avant raccord à l'interface. Les tâches de revue de profondeur sont informatives et ne bloquent pas une finalisation honnête.
