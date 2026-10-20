# Corrections après l'audit du run réel 91590987 (13/14 sections)

19 octobre 2026, branche `feature/ennoscholar-v4-diagnostic-link`, sans commit.

Conditions :

- aucun appel OpenAI ;
- base `ennoscholar_v4_test.db` lue en lecture seule : dernière écriture à 12:40, fin du run réel ; rien depuis ;
- modèle, tarifs et plafonds globaux inchangés ;
- validateurs conservés ;
- les 13 brouillons et le plan v2 ne sont pas modifiés.

Ordre suivi : celui recommandé par l'audit de Codex.

## 1. Provenance : identité des passages et localisateurs

**Cause démontrée**

- Le même passage du diagnostic porte jusqu'à cinq identifiants.
- Le `section_title` du diagnostic est en retard sur le document : `p65` et `p66` relèvent de l'Essai 2, `p69` à `p71` de l'Essai 5, mais restent étiquetés Essai 1 et Essai 4.

**Correction** : `diagnostic_link.passage_index`, calculé à la lecture, snapshot inchangé.

- Une identité canonique par document, localisateur et texte, avec la table des alias. Un même texte dans un autre document reste distinct.
- Un localisateur effectif, déduit des en-têtes numérotés de même famille (« Mot N — Titre ») présents dans le texte et propagé aux passages contigus. Un passage qui chevauche deux sections est signalé (`spans`).
- `work_units` utilise ces localisateurs effectifs.
  - Un fait issu d'un passage chevauchant est rangé dans la section qu'il nomme (« Essai 5 — … ») ; sinon, il est signalé `passage_spans_two_document_sections`.
  - Une unité atteinte seulement par le débordement d'un passage du verrou est qualifiée `lock_evidence_passage_partial`, à confirmer.
- Les dossiers transmis au modèle n'utilisent que les identifiants canoniques.

**Mesuré sur le snapshot réel**

- 113 identifiants ramenés à 59 passages canoniques ; 5 localisateurs corrigés.
- Le verrou 4 est rattaché à l'Essai 5, et non plus à l'Essai 4.
- Le verrou 1 a deux unités : Essai 1 et Essai 2.
- Le verrou 2 a l'Essai 4 et l'Essai 5 en lien partiel.

## 2. Traces, rendu, bibliographie

**Traces de confrontation** : `provenance_check.draft_confrontation`.

- Elles sont recalculées à partir du texte brut et des alias, sans modifier le brouillon.
- Un brouillon antérieur à la provenance typée est signalé `legacy_provenance_unrecorded`, et non comme une confrontation manquante.

**Rendu unique** : `citation_render.py`, utilisé par l'API du document, le frontend et les exports Word/PDF.

- Le corps de chaque section ne répète plus le titre du plan.
- Les marqueurs bruts deviennent `[n]` (référence), `Pn` (passage d'une publication) et `Dn` (passage projet canonique). Les marqueurs adjacents sont groupés, par exemple `[2 ; P4 ; D5]`.
- `raw_text` et tous les alias sont conservés pour l'audit.
- Références : auteurs, année, titre, revue et DOI/URL quand ils sont connus, sans ponctuation vide.
- Les sources projet sont listées avec leur localisateur effectif et, s'il a été corrigé, celui du diagnostic.

**Frontend** (`scholar-v4-workspace.tsx`, `scholar-v4.ts`) : il affiche ce rendu, la bibliographie complète et les panneaux des sources projet et des passages. La vérification de types (`tsc`) passe.

## 3. Contexte

- `writing_progress` dans la vue du modèle : compteurs par section et seulement le dernier refus de la section active (700 caractères au plus). L'historique complet reste stocké. Avant, 15 055 caractères d'erreurs pouvaient évincer toutes les preuves.
- Paquet de section : sources compactes, puis tableaux et affirmations avec leurs preuves, puis détails des sources. La carte du manuscrit remplace les trois extraits de 400 caractères.
- **Valeurs chiffrées** : à la sauvegarde, chaque pourcentage ou décimal doit figurer dans une preuve citée du même paragraphe (passage d'article, cellule de tableau ou passage projet).
  - La correspondance est enregistrée (`numeric_trace`).
  - Sinon, la sauvegarde est refusée avec la liste des valeurs.
  - La revue recalcule ce contrôle pour les anciens brouillons.

## 4. Ordonnancement

**Cause démontrée** : `launch_more` ne lançait aucune analyse dès qu'une section était rédigeable, et comptait les outils sur le plafond d'exploration (48) au lieu de celui de la rédaction.

**Correction**

- Deux analyses restent réservées en arrière-plan pendant la rédaction (`READING_SLOTS_WHILE_WRITING`), dans la limite d'outils de la rédaction. Elles sont lancées avant chaque lot de sections.
- Les sources sans fiche des sections restantes sont lues. Pour une demande `deep`, les fiches sur résumé dont le texte est désormais stocké sont relues ; une seule tentative par travail.
- **Synthèse** (conclusion, perspectives, bilan) : rédigée seulement après les sections scientifiques, sauvegardées ou mises de côté avec leur cause.

## 5. « Rédige et compare »

- `TurnGoalArgs.comparison_requested` garde l'obligation de comparer quand le livrable est une rédaction. Elle n'est jamais déduite des seuls mots.
- `draft_comparison_batch` : avant une section, ses 2 à 4 sources ayant des preuves utilisables sont comparées par `compare_articles`, sur une fenêtre. Au plus 3 comparaisons par requête, jamais une comparaison déjà faite sur les mêmes sources. Une comparaison refusée n'est pas reprogrammée.
- Une section avec une seule source documentée n'est pas forcée à comparer.

## 6. Lectures prioritaires

- Les textes intégraux déjà extraits sans fiche (LoveDA et DANet) sont lus en priorité.
- LDPoly et HoliTracer sont lus dès que leur section en a besoin : texte intégral s'il est réellement accessible, sinon résumé, marqué comme tel. Une fiche mixte garde l'étiquette prudente « abstract » du validateur.

## 7. Mémoire argumentative du manuscrit

- `WriteArgs.argument_summary` (rôle, acquis, limites, liens) est enregistré à la sauvegarde dans `manuscript_memory`.
- Les 13 brouillons existants utilisent un extrait de leur propre texte, signalé comme tel.
- `manuscript_map` donne à chaque section l'état et les acquis des autres. La synthèse en reçoit une part plus large (30 % de son paquet).

## 8. Coûts et arrêts

- Lecture par delta : une page de fiche relue ne renvoie plus que les affirmations nouvelles.
- Une même requête de contexte projet répétée est signalée « déjà reçue ».
- Budget par section en tours séquentiels : 15 % du plafond de tokens (60 000) et 8 tours. La conclusion de l'audit avait consommé 159 631 tokens.
- Refus d'admission : un événement `budget/admission_refused` donne les tokens consommés, l'estimation d'entrée, la réserve de sortie et le plafond. En rédaction, un contexte réduit (18 000 caractères) est tenté une fois avant l'arrêt.
- Plafonds globaux inchangés.

## Mesures

Hors ligne, état réel en mémoire ou rejeux simulés. **Ce ne sont pas des mesures réelles.**

| | Avant | Après |
|---|---|---|
| Identifiants diagnostic / passages canoniques | 113 identifiants, alias non reconnus | 59 passages, alias conservés |
| Fausses alertes de confrontation (13 brouillons réels) | 9 | 0 ; restent 3 comparaisons projet réellement absentes |
| Alertes numériques (13 brouillons réels) | 4 | 0 : toutes les valeurs figurent dans une preuve citée |
| Titres doublés à l'export | 13 sections | 0 |
| Références de l'export | « titre. . URL » quand la revue manque | auteurs, année, titre, revue, DOI quand connus |
| Rejeu `b52f582a` | 14/14 sans lire les sources manquantes | 8 à 11/14 selon la charge : lectures des sources sans fiche d'abord, synthèse en dernier, 3 appels au plus par section |
| Reprise de l'état réel (13 brouillons) | section 7 jamais lancée | section 7 seule produite, 13 brouillons intacts |

## Tests

- `tests/ennoscholar_v4/test_audit_91590987.py` (12 tests) :
  - alias et localisateurs réels ;
  - traces recalculées sans modifier les brouillons ;
  - rendu et export du vrai manuscrit ;
  - formatage des références ;
  - erreurs volumineuses sans perte des preuves ;
  - valeur non prouvée refusée, valeur projet tracée ;
  - parcours complet : lecture, comparaison, rédaction, sauvegarde, revue, export avec « rédige et compare » ;
  - aucune comparaison sans demande ;
  - admission budgétaire expliquée ;
  - reprise de la section 7 ;
  - lecture par delta.
- Rejeux mis à jour : ils reconstituent le plan **archivé** avant chaque run, car les runs suivants ont ajouté des brouillons.
- Non-régression V4, V3 et V3.1 : **529 tests réussis**.

## Limites

- **Accès réseau** : la lecture réelle de LDPoly et HoliTracer dépend d'un accès légal au texte ; le rejeu, sans réseau, les lit sur résumé.
- **Qualité réelle** : les comparaisons et la mémoire argumentative sont produites par le vrai modèle ; leur qualité n'est pas mesurée hors ligne.
- **Faux négatifs** : la correction des localisateurs repose sur des en-têtes numérotés dans le texte ; un document sans tels en-têtes garde ses localisateurs.
- **Revue sémantique** : la revue sémantique globale (`review_document`) et les révisions de transitions par paragraphe ne sont pas lancées automatiquement.
- **Complétude ou profondeur** : lire les sources manquantes avant d'écrire réduit le nombre de sections produites par run ; la reprise termine le reste.
