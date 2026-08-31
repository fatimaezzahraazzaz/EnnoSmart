# Audit du zéro d’éligibilité — 30 août 2026

Périmètre : Scalian / AI_RADAR / 2024 et SMART4 ENGINEERING / LRT GROUPE / Km / 2025.

## Cause reproduite

L’affichage transmet correctement le score enregistré. Avant correction, les deux
évaluations NLP avaient une couverture documentaire de 0,9, mais un indice de
défendabilité de 0 : aucune opération n’était reconnue comme noyau R&D.

Le contrôle de démarche appelait le filtre narratif `gate_project_fact` qui
refusait une expérience si son passage n’avait pas un rôle méthode/paramètre.
Les passages mixtes classés résultat ou contribution étaient donc exclus, même
lorsqu’ils décrivaient explicitement une expérience réalisée. La détection des
activités répétait cette exclusion. Certaines formes verbales telles que
« une étude a consisté à étudier » étaient également ignorées.

Preuves lues dans les documents et retrouvées dans le NLP :

- AI Radar, `R25802 - 1.0_RT_NP_Etude_poste1`, passage
  `r25802_1_0_rt_np_etude_poste1_5df1dad7abd1_docx_bcf95d8e602e164b` :
  entraînement de modèles ATR avec deux types de masques, suivi d’une comparaison
  des résultats. Passage classé résultat, rejeté comme méthode.
- Même document, passage `..._1221fb31f4aeeb7e` : étude de sensibilité aux
  matériaux EM utilisés dans les simulations, rejetée comme non exécutée.
- KM, `MC2_Rapport_R&I_KDIR`, passage
  `mc2_rapport_r_i_kdir_28e55216644c_docx_29a8453338c9447c` : tests sur documents
  avec verrous et sans activité de recherche, classés contribution.
- Même rapport, passage `..._14e444baa341e7ef` : validation humaine comparée
  à l’évaluation du modèle et accord déclaré de 94 %. Le rapport complet décrit
  six itérations ; ce pourcentage est une mesure rapportée, pas une garantie
  de généralisation ni une décision fiscale.

## Correction ciblée

- Reconnaître une action explicitement exécutée dans le texte original,
  indépendamment de l’étiquette résultat/contribution, sans changer cette étiquette.
- Conserver les contrôles de provenance ; un contexte voisin, une intention
  future, une valeur isolée ou une référence bibliographique ne suffit pas.
- Faire parvenir ces mêmes expériences à la section narrative de démarche.
- Conserver le calcul Frascati, les seuils, les groupes de verrous et la
  distinction ingénierie/R&D. Un test de conformité standard reste de l’ingénierie.

Fichiers modifiés pour cette correction :
`agents/EnnoDiagnostic/project_fact_gate.py`,
`modules/NLP/demarche_legibility.py`,
`agents/EnnoDiagnostic/ennodiagnostic_agent.py`.
Les autres modifications déjà présentes dans le dépôt n’ont pas été remplacées.

## Vérifications

Recalcul local sur les mêmes groupes et preuves, sans appel LLM ni réindexation :

| Dossier | Indice enregistré avant | Recalcul local |
| --- | ---: | ---: |
| AI Radar 2024 | 0 | 0,9 |
| KM 2025 | 0 | 0,7 |
| AI-Code 2024 | 0,9 | 0,9 |
| CEVAA / Vecame, organisme 6NAPSE GROUP | 0,7 | 0,7 |

Ces valeurs sont des résultats de reproduction locale, pas des diagnostics
republiés ni une validation d’éligibilité administrative.

42 tests logiciels ciblés passent. Trois anciens échecs ont été reproduits
avec les nouveaux chemins désactivés : fallback d’objectif, récupération de
contrainte, et fixture de nettoyage dépourvue de `chroma_dir`. Ils ne sont pas
modifiés dans ce correctif. Le dernier lot exclut cette dernière fixture.

Aucun agent n’a été relancé. La tentative de régénération KM via le fournisseur
LLM configuré a été bloquée avant exécution par le contrôle de sécurité. L’utilisateur
a ensuite choisi de faire les tests des agents manuellement. Le script de
régénération a été retiré et le diagnostic KM enregistré n’a pas été modifié.

## Nettoyage demandé

Suppression du projet applicatif AI_RADAR 2024, id 3, avec ses données associées :
1 diagnostic, 9 verrous, 7 runs Scholar, 134 articles, 14 sessions de recherche
guidée et 106 messages. Les rattachements de documents et d’accès du projet sont
supprimés aussi ; les fichiers sources physiques sont conservés.

Les dossiers de sortie `ennodiagnostic`, `ennoscholar`, `document_compare`,
`cir_memory` et les rapports de diagnostic correspondants ont été retirés.
Les autres projets sont toujours présents. Les fichiers sources et NLP sont
identiques selon les empreintes avant/après. Le rapport de préparation est conservé.

Chroma n’a été ni supprimé, ni réindexé, ni ouvert par un client Chroma pour le
nettoyage. Quatre fichiers HNSW ont évolué pendant l’opération, alors qu’un
processus Chroma les utilisait déjà ; aucune restauration ni interruption de ce
processus n’a été tentée. Il ne faut donc pas prétendre que tous les octets de
Chroma sont restés identiques pendant l’intervalle.

Sauvegarde locale hors des dossiers actifs :
`.codex_tmp/eligibility_repair_20260830/`.
Le manifeste `radar_reset_verified.json` documente les contrôles effectués.

## Test manuel

Redémarrer le backend pour charger le correctif. Pour KM, refaire la préparation
des sources puis le diagnostic : le score est calculé dans le NLP et l’ancien
diagnostic conserve son ancien score tant qu’il n’est pas régénéré.
AI Radar 2024 peut être recréé pour un nouvel essai.
