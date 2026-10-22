# EnnoScholar V4 — run 53292dc4 : résultats perdus, sauvegardes refusées, lectures répétées

Session `53292dc472204046a0b48eb5b3408d11`, job de recherche `job_0aa07bfe60db4acab6f2034058478a61`, modèle `gpt-6-luna` (inchangé). Diagnostic et tests hors ligne : session lue en `mode=ro`, travaillée en mémoire ; aucun appel LLM, aucune recherche, aucun téléchargement.

## Causes constatées

1. **Contexte final vidé.** Les fiches cumulées pesaient ~205 000 caractères ; la finalisation disposait de 24 000. Le paquet borné ne gardait qu'une affirmation « méthode » par article, puis la boucle d'ajustement le réduisait à zéro (`blocked_by_context`). Aucune valeur (FID 23,35 / 12,27 de TetGAN, 9 900 maillages de DL-Polycube, +22 % de Non-iterative) n'atteignait le modèle.
2. **Limites locales prises pour globales.** Chaque lecture enregistre sa propre limite (« ces trois passages ne donnent aucun résultat numérique ») ; la dernière était transmise comme limite de la fiche, contredisant les valeurs enregistrées par une lecture antérieure.
3. **Refus pour un identifiant tronqué.** DL-Polycube (version revue) et NeurFrame : analyses complètes (21 et 30 affirmations citées en `E1…E6`) refusées parce qu'un identifiant complet recopié dans `reviewed_evidence_ids` avait perdu un caractère. Le refus n'était pas enregistré (tâche restée « generated »), l'analyse perdue.
4. **Relectures.** Neuf appels LLM de lecture n'ont fait que redemander des passages (`revisit=true`), dont quatre sur DL-Polycube, souvent déjà analysés.
5. **Sélection incohérente.** Compteur sur les recommandations (6/5 en cours de job), lecture des deux versions de DL-Polycube, réponse affichant NVMG à la place de DDPM-Polycube, 3DMeshNet compté comme analysé alors que seul l'abstract l'était.
6. **Étude 2D.** Non-iterative est validé sur des problèmes 2D (abstract) ; la réponse 3D ne le disait pas.

## Corrections

- `context.py` — dossier scientifique final dédié (`final_science_packet`) :
  - **Contenu** : il est construit sur les fiches de toutes les lectures. Tous les résultats et toutes les limites, plus les premières affirmations de méthode, protocole, comparaison et problème, sont transmis en priorité (niveau 1), avec leurs références de preuve.
  - **Place** : elle grandit jusqu'à 60 000 caractères avant que quoi que ce soit ne soit omis. Les vues secondaires sont retirées d'abord, et le dossier n'est jamais réduit.
  - **Rapport `transmission`** : il précise, par article, la profondeur (texte intégral ciblé ou abstract seul), les dimensions enregistrées, transmises, non transmises ou non trouvées, et les drapeaux de portée.
  - **Limite de fiche** : elle est recalculée sur l'ensemble des lectures.
- `research.py` — sélection canonique (`canonical_selection`) :
  - **Versions** : les versions vérifiées sont regroupées (identité vérifiée, preuves jamais fusionnées).
  - **Ordre** : les œuvres analysées viennent en premier, coupées au nombre demandé. Les autres candidats restent disponibles sans bloquer.
  - **Usages** : compteur, contexte final, note de profondeur et liste publiée utilisent cette même sélection.
- `publication.py` — contrôles locaux à la publication d'une recherche :
  - la liste affichée doit être la sélection ;
  - une validation 2D explicite doit être signalée ;
  - un article dont des valeurs chiffrées ont été transmises ne peut pas être présenté sans aucune d'elles (un seul refus par exécution).
- `scientific_reading.py`, `toolkit.py`, `agent.py` — références exactes et réparation :
  - **Détection** : toute référence inexistante ou étrangère à l'article est refusée nommément, sans correspondance approximative.
  - **Conservation** : le refus est conservé dans `analysis_refusals` avec la proposition complète.
  - **Réparation** : `analyze_article(action=repair)` remplace seulement les références nommées par une ref ou un identifiant exact.
  - **Trace** : la tâche est marquée refusée (pas rejouée). Chaque tentative porte son issue : `passages_requested`, `analysis_saved`, `analysis_refused` ou `analysis_repaired`.
- Lectures — trois règles :
  - un passage déjà examiné ou incorporé n'est plus représenté à la lecture automatique ;
  - une fenêtre déjà sauvegardée avant une interruption est clôturée localement ;
  - une autre version d'un travail déjà analysé n'est pas relue sauf demande explicite.
- `evidence.py` — découpage par paragraphes et phrases, tableaux gardés entiers avec en-tête rattaché, priorité aux sections méthodes, expériences, résultats, discussion et limites. Il ne s'applique qu'aux **documents récupérés désormais** : les documents déjà lus gardent leurs identifiants de preuve et leurs curseurs.

## Coût

Le dossier final du run tient en ~64 000 caractères (~16 000 tokens d'entrée, < 0,002 $ au tarif Luna). La réparation d'un refus est un appel de quelques centaines de tokens de sortie au lieu d'une nouvelle analyse.

## Tests

`tests/ennoscholar_v4/test_run_53292dc4.py` couvre :
- le rejeu de la finalisation réelle et l'ancien chemin de perte ;
- les deux sauvegardes refusées et leur réparation exacte ;
- une fenêtre sauvegardée non relancée ;
- les quatre relectures de DL-Polycube ;
- la sélection unique et les versions groupées ;
- le drapeau 2D ;
- le coût et le modèle ;
- un segment de job de bout en bout ;
- la réparation par la boucle réelle de l'agent.
