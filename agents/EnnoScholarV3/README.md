# EnnoScholar V3

Agent conversationnel indépendant de V2. La validation scientifique mesurée est décrite dans `docs/ennoscholar-v3-validation.md` ; le fonctionnement des routes ne suffit pas à valider la recherche.

## Architecture

- `agent.py` : boucle OpenAI Responses en streaming, `web_search` natif, appels de fonctions avec leurs `call_id`, contexte et limites.
- `tools.py` : recherche académique, résolution bibliographique, lecture, analyses étayées, comparaison, mémoire et évaluation du classement.
- `sources.py` : instances privées des clients Semantic Scholar, OpenAlex et arXiv de V2, vérification Crossref/DataCite. Aucun singleton ou fichier V2 modifié.
- `network.py` / `fulltext.py` : téléchargements publics bornés, DNS épinglé et revérifié après redirection, extraction PDF/HTML et contrôle d'identité.
- `session.py` / `service.py` : tables PostgreSQL propres à V3, propriétaire authentifié, historique, conservation des articles, traces factuelles, annulation et exclusion des exécutions concurrentes sur une conversation.

Le modèle initial est `gpt-5.5`, vérifié dans le compte utilisé pour les essais. [Documentation du modèle](https://developers.openai.com/api/docs/models/gpt-5.5), [recherche Web native](https://developers.openai.com/api/docs/guides/tools-web-search), [appels de fonctions](https://developers.openai.com/api/docs/guides/function-calling). Le raisonnement chiffré est rejoué seulement dans le tour courant, jamais sauvegardé ni exposé. Les traces montrent les opérations et sources.

## Outils

`search_scientific`, `get_article_metadata`, `get_fulltext`, `analyze_article`, `find_similar_articles`, `compare_articles`, `update_research_state`, `evaluate_research`, plus `web_search` hébergé par OpenAI.

La stratégie dépend de la demande. Une salutation ou un suivi peut utiliser la mémoire seule. Une recherche nouvelle combine Web et bases académiques, reformule les concepts et explore les lacunes. Le budget temporel réserve l'analyse et la synthèse ; aucune liste d'articles n'est fournie à l'agent. arXiv recherche les concepts dans les titres et abstracts plutôt que de laisser les requêtes en prose correspondre à des noms d'auteurs. [Syntaxe arXiv officielle](https://info.arxiv.org/help/api/user-manual.html).

## Contrat scientifique

`publisher_access` indique l'accès éditeur observé, indépendamment du téléchargement. `fulltext_available=true` exige une extraction lisible dont l'identité a été contrôlée. `analysis_basis=fulltext` exige une analyse terminée à partir de passages extraits. Un PDF trouvé, téléchargé ou payé ne suffit donc pas à déclarer une analyse intégrale. Les extractions bornées portent `fulltext_truncated` et les preuves indiquent URL, page/section et passage. Une analyse sans preuve valide ou une sortie tronquée ne produit pas de conclusion validée.

Les références payantes restent candidates. Les DOI sont vérifiés auprès du registre ; une association DOI/titre incohérente est bloquée. Les échecs temporaires peuvent être retentés. Une synthèse contenant un DOI non résolu est bloquée. Les difficultés CIR extraites du dialogue restent des hypothèses à valider par le consultant.

## API et frontend

Toutes les routes utilisent l'authentification EnnoSmart et vérifient le propriétaire :

| Méthode | Route | Usage |
|---|---|---|
| POST | `/scholar-v3/chat` | Lancer un tour asynchrone, réponse 202 |
| POST | `/scholar-v3/sessions` | Créer une conversation |
| GET | `/scholar-v3/sessions` | Historique du propriétaire |
| GET | `/scholar-v3/sessions/{id}` | Conversation et articles |
| GET | `/scholar-v3/sessions/{id}/articles` | Articles paginés |
| PATCH | `/scholar-v3/sessions/{id}/articles/{article_id}` | Conservation manuelle |
| GET | `/scholar-v3/runs/{id}` | État, résultat et activité |
| POST | `/scholar-v3/runs/{id}/cancel` | Annulation coopérative |

Le frontend `/ennoscholar-v3` est intégré à la sidebar existante. Il utilise le mécanisme existant de connexion/rafraîchissement des jetons. Historique et articles sont stockés côté serveur ; le navigateur ne conserve que l'identifiant de la conversation à rouvrir. Les cartes classées sont séparées des candidats à évaluer. La sélection reste disponible après rechargement.

## Configuration et exploitation

Les paramètres sont documentés dans `.env.example` sous `ENNOSCHOLAR_V3_*`. Valeurs initiales : 600 secondes, 48 appels d'outils, 14 recherches académiques, 18 tours Responses et plafond estimé de 4 USD par tour. L'estimation utilise les tokens réellement retournés et le tarif Web ; elle n'est pas une facture, et un appel interrompu peut avoir une consommation non retournée. Un autre modèle exige des tarifs explicites. `ENNOSCHOLAR_V3_ENABLED=false` désactive les recherches nouvelles.

Les deux nouvelles tables sont créées par le mécanisme SQLAlchemy existant au démarrage. Le serveur conserve un bail par conversation et sauvegarde progressivement les découvertes terminées. Un worker interrompu est identifié à expiration du bail. Les sélections du consultant restent prioritaires sur celles d'un worker.

Les recherches nécessitent `OPENAI_API_KEY` et l'accès à Responses/Web. Les clés Semantic Scholar/OpenAlex sont facultatives, mais les quotas publics peuvent limiter la couverture. Les téléchargements ne contournent aucune protection d'accès.

## Évaluation reproductible

Les références du benchmark résident exclusivement dans `tests/ennoscholar_v3/research`. `evaluate.py` transmet seulement la demande à l'agent, puis compare les identifiants après l'exécution. Les tests unitaires désactivent le réseau ; ils ne sont pas une évaluation de pertinence.

```powershell
.venv/Scripts/python.exe -m pytest tests/ennoscholar_v3 -q
.venv/Scripts/python.exe tests/ennoscholar_v3/research/validate_references.py --output tests/ennoscholar_v3/research/validated_reference.json
.venv/Scripts/python.exe tests/ennoscholar_v3/research/evaluate.py --live --engine both --output .codex_tmp/scholar-v3-evaluation
```

`--live` active des appels réellement facturés. Les fichiers conservent les activités, classements, accès, durées et rappels du benchmark. Precision@10 reste indéfinie tant que les dix positions n'ont pas d'annotations indépendantes ; les étiquettes du modèle ne sont pas assimilées à une validation humaine.
