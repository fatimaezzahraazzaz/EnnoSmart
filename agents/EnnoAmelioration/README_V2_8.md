# EnnoAmelioration V2.8 — No-research kill switch

Correction du test éditorial : une instruction explicite comme `Ne lance aucune recherche scientifique`, `N'utilise aucune nouvelle source` ou `Ne lance pas EnnoScholar` devient une interdiction absolue pour le tour courant.

- les choix de recherche résiduels de session sont ignorés ;
- `editorial_only` force `forbids_new_research=True` et `forbids_scholar=True` ;
- `pas/sans nouvelle recherche` n'est plus interprété comme `use_existing_sources` ;
- une vérification finale empêche tout appel Scholar lorsqu'un drapeau d'interdiction est actif.
