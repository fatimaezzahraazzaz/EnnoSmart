# Correctifs après le test manuel AI Radar / KM

## Cause du score persistant

Les fichiers NLP du test manuel utilisent encore
`demarche_legibility_v5_3_strict_project_fact_gate`, malgré le correctif v5_8
présent sur disque. Le serveur Uvicorn sur le port 8002 était démarré depuis
14:15, sans rechargement automatique.

Recalcul déterministe des mêmes groupes, sans extraction, LLM ni indexation :

| Projet | Calcul stocké v5_3 | Calcul corrigé v5_8 |
| --- | ---: | ---: |
| AI_RADAR 2024 (nouveau corpus de 9 documents) | 43,75 % | 80 % |
| SMART4 ENGINEERING / LRT GROUPE / Km / 2025 | 0 % | 70 % |
| Ai-Code 2024 | 90 % | 90 % |
| 6NAPSE GROUP / CEVAA / Vecame / 2025 | 70 % | 70 % |

Ces nombres sont des indices internes de défendabilité documentaire, pas des
décisions administratives. Les rapports existants n'ont pas été artificiellement
remplacés par ces résultats. Le prochain lancement vérifie la version de
l'évaluation préparée et actualise uniquement les calculs déterministes devenus
anciens, en préservant les groupes, rôles, sources et Chroma.

## Temps de calcul

Le journal fourni mesure 1287,05 s au total, dont 1024,765 s pour la continuité
historique, 104,261 s pour la comparaison CIR et 95,029 s pour les sections.
La lecture de la mémoire antérieure prend environ 25 s et était répétée.

Les caractéristiques lexicales et les comparaisons identiques sont maintenant
mémorisées dans des caches bornés. La sélection des trois meilleurs supports
emploie une borne supérieure sûre : aucun candidat pouvant changer le résultat
n'est écarté. La mémoire chargée est transmise aux étapes suivantes. Les sous-étapes
historiques disposent désormais de chronométrages distincts.

Contrôle local sur trois familles et tous leurs supports : 4,463 s avant,
1,087 s après, résultats strictement identiques. Construction complète locale des
familles : 87,636 s après correction. Cela ne constitue pas une mesure du temps
du prochain diagnostic complet : aucun appel LLM ni diagnostic n'a été relancé.

## OCR

L'OCR Tesseract a effectivement été tenté sur la page 3 du PDF EXPLIMA. Sa qualité
0,430 était inférieure à celle du natif (0,488), donc le natif a été conservé.
Les journaux distinguent maintenant absence de texte et OCR de faible qualité.
Le résultat structuré expose le moteur et la sélection par page, au lieu de
déclarer systématiquement un résultat OCR dès qu'une tentative a eu lieu.
La fusion associe chaque résultat à son numéro de page, même si une page
précédente a échoué, sans décaler les textes.

## Nouveau lancement et suppression

À la demande explicite de l'utilisateur, une nouvelle préparation ou un nouveau
diagnostic efface définitivement les anciens DiagnosticRun et leurs verrous,
y compris gardés/manuels, ainsi que les runs Scholar et les conversations de
recherche qui en dépendent dans ce projet. Les copies de rapports et caches
générés sont supprimées. Les documents, autres projets, années précédentes,
Memory V2 et fichiers Chroma ne sont pas supprimés par cette remise à zéro.
La préparation conserve son fonctionnement normal de réindexation du corpus.
Un deuxième lancement simultané dans le même processus est refusé pour éviter
qu'il efface le premier pendant son exécution.

Les validations locales ciblées ont donné 47 tests réussis (un ancien test avec
fixture Chroma obsolète exclu). La suppression a été vérifiée sur SQLite isolée
avec clés étrangères actives et sur fichiers temporaires, jamais sur les runs
actuels de l'utilisateur. Le test fonctionnel complet reste manuel.
