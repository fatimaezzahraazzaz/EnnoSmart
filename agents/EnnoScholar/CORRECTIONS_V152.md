# EnnoScholar V1.52 — Fenêtre CIR N-1 et précision des candidats

## Règle métier temporelle

Pour un dossier CIR d'année **N**, EnnoScholar n'utilise que des publications disponibles **au plus tard en N-1**.

La borne basse n'est pas une année absolue codée en dur. Elle est calculée dynamiquement :

`N - ENNOSCHOLAR_CIR_LOOKBACK_YEARS`

Valeur par défaut : **30 ans**.

Exemple CIR 2024 :

- minimum : 1994 ;
- maximum : 2023 ;
- 2024, 2025, 2026 sont exclus ;
- 1900 et les publications beaucoup trop anciennes sont exclues par la borne basse dynamique.

### Configuration

```env
ENNOSCHOLAR_ENFORCE_CIR_YEAR_WINDOW=true
ENNOSCHOLAR_CIR_LOOKBACK_YEARS=30
ENNOSCHOLAR_CIR_REQUIRE_KNOWN_YEAR=true
```

Le recul peut être augmenté pour un domaine où des références fondatrices plus anciennes sont nécessaires, sans modifier le code.

## Ranking / bruit

La liste présentée au consultant est plus stricte :

- suppression des `Hors sujet` ;
- seuils renforcés Direct / Connexe / Fondamental ;
- un article Fondamental doit partager plusieurs ancres spécifiques ;
- limitation de présentation à 15 résultats par défaut.

Configuration :

```env
ENNOSCHOLAR_PRESENTATION_TOP_K=15
```

EnnoAmel applique ensuite un cap final de 12 candidats pour la validation humaine.

## Cache

La version du cache de run a été changée afin qu'un ancien résultat contenant des années incompatibles ne soit pas repris après migration.

## Recherche générale typée

`research_targets` reste un contrat natif d'EnnoScholar : aucun faux verrou n'est nécessaire pour rechercher des publications destinées à renforcer une méthode, un résultat, un contexte, une limitation, une contribution ou un état de l'art.

## Tests

`tests/test_cir_year_window_v152.py` couvre la fenêtre N-1, la borne basse dynamique, les années inconnues et le filtre de présentation.
