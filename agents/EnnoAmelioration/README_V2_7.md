# EnnoAmelioration V2.7 — Editorial-only routing & traceability fix

Cette version corrige le cas fonctionnel « amélioration de style/structure uniquement » observé pendant le test 1.

## Bug confirmé par les logs

La consigne contenait :
- `N’ajoute aucun nouvel argument scientifique`
- `Ne lance aucune recherche scientifique`
- `N’utilise aucune nouvelle source`
- `Ne lance pas EnnoScholar`

Malgré cela, `intention_service.py` repérait le mot `argument` dans une **négation** et créait `ImprovementIntent.ARGUMENTATION`. Cela activait `needs_diagnostic=True`, puis le flux lançait un `ScopedDiagnostic` complet avant le writer.

## Corrections

1. **Négation locale de l'argumentation**
   - `aucun nouvel argument` n'est plus une demande d'argumentation.
   - seules les demandes positives déclenchent `ARGUMENTATION`.

2. **Mode `editorial_only`**
   - une demande purement rédactionnelle devient Writer-only ;
   - `needs_diagnostic=False` ;
   - `needs_project_evidence=False` ;
   - `needs_scholar=False` ;
   - `needs_new_research=False`.

3. **Défense en profondeur**
   - le routage sémantique METHOD/RESULT/etc. ne peut pas réactiver un spécialiste en mode éditorial ;
   - `agent.py` refait un verrou final avant la construction du paquet de preuves.

4. **Contrat Writer à faits constants**
   - pas d'expansion d'acronyme absente du texte cible ;
   - pas de définition, exemple, justification, méthode, résultat ou causalité nouvelle ;
   - réécriture limitée à syntaxe, clarté, fluidité, transitions et structure.

5. **Diff sémantique amélioré**
   - les paraphrases ne sont plus affichées artificiellement comme `insert` + `delete` ;
   - meilleur poids du recouvrement lexical informatif ;
   - coalescence conservatrice des paires INSERT/DELETE adjacentes correspondant à une reformulation.

6. **Traçabilité stricte à faits constants**
   - un vrai nouveau paragraphe reste signalé au consultant ;
   - les reformulations du texte existant ne demandent plus à tort une preuve documentaire.

## Résultat attendu dans le terminal pour le test 1

Après :

```text
[LLMClient] OK request=ennoamelioration:semantic_section_routing ...
```

on doit aller directement vers :

```text
[LLMClient] OK request=ennoamelioration:writer:controlled_revision ...
```

Il ne doit PAS apparaître :

```text
[EnnoAmel][ScopedDiagnostic]
EnnoDiagnostic ...
EnnoScholar ...
```

## Validation locale

- 5 tests de régression V2.7 dédiés au test 1 : OK
- 24 tests ciblés EnnoAmel : OK
- `python -m compileall` : OK

Les tests nécessitant le backend complet (`modules.LLM`, base de données, services backend) ne sont pas exécutables dans l'archive autonome seule.
