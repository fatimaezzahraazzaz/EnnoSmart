# EnnoDiagnostic V5.6 — Narrative Precision Fix (générique)

## Périmètre
Ce correctif affine uniquement les sections narratives déjà alimentées par le NLP courant :
- objectif global ;
- démarches ;
- résultats / métriques ;
- paramètres / contraintes.

Il ne contient aucun nom de projet, de modèle, de benchmark ou de stratégie propre au dossier utilisé pour les tests.

## Principe
Le module `narrative_evidence_balancer.py` travaille après le NLP et avant la rédaction. Il :
1. enrichit l'objectif avec le contexte technique proche dans les mêmes sections projet ;
2. récupère des démarches réellement exécutées même si le NLP les a rangées dans un rôle voisin ;
3. privilégie les résultats de projet observés et les tableaux expérimentaux larges/corroborés ;
4. filtre les paramètres pour ne conserver que des contraintes attribuables au projet courant ;
5. diversifie les preuves afin d'éviter qu'une seule famille expérimentale monopolise la section.

Le correctif n'ajoute ni requête Chroma ni appel LLM.

## Garantie sur les verrous
Le paquet ne remplace pas :
- `consultant_verrou_synthesizer.py` ;
- `scientific_axis_synthesizer.py`.

L'installateur vérifie aussi les empreintes de quatre fonctions critiques de l'agent avant et après installation :
- `_load_nlp_lock_group_sources` ;
- `_load_recovered_missing_lock_candidates` ;
- `build_llm_reformulated_verrous` ;
- `_enrich_verrous_with_frascati`.

Si ces fonctions ne correspondent pas à la base attendue, l'installation s'arrête au lieu de risquer de modifier les verrous.

## Installation
Arrêter le backend et le worker, puis depuis `C:\EnnoSmart` :

```powershell
python `
  "C:\EnnoSmart\EnnoDiagnostic_narrative_precision_fix_v5_6\install_narrative_precision_fix_v5_6.py" `
  --repo "C:\EnnoSmart"
```

Puis vérifier :

```powershell
python `
  "C:\EnnoSmart\EnnoDiagnostic_narrative_precision_fix_v5_6\verify_narrative_precision_fix_v5_6.py" `
  --repo "C:\EnnoSmart"
```

## Test
Aucun nouveau `Préparer les sources` n'est nécessaire si le `nlp_result.json` courant est déjà celui du dossier à tester. Redémarrer backend + worker puis lancer directement `Diagnostic`.

Le run doit afficher une ligne de ce type :

```text
[EnnoDiagnostic][NARRATIVE_BALANCE_V56] objective_context=... methodes=... resultats=... parametres=... chroma=0 locks_unchanged=true
```

Cette ligne confirme que le balancer a travaillé sans Chroma et sans modifier la liste des verrous.
