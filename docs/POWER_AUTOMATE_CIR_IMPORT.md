# Collecte CIR avec Power Automate

## Principe

Power Automate utilise la connexion Microsoft professionnelle de l'utilisatrice
pour lire les documents SharePoint auxquels elle a déjà accès. EnnoSmart ne se
connecte jamais à Microsoft : il lit uniquement les copies présentes dans un
dossier OneDrive professionnel synchronisé sur le poste.

## Actions SharePoint autorisées dans le flux

- `Get files (properties only)`
- `Get file properties`
- `Get file metadata`
- `Get file content`

Ne jamais ajouter une action SharePoint de création, modification, renommage,
déplacement, suppression, partage ou changement de permissions.

## Flux V1 manuel

1. Déclencheur manuel `Lancer collecte CIR`.
2. Action SharePoint `Get files (properties only)` sur la bibliothèque voulue.
3. Filtrer les extensions `.pdf` et `.docx`.
4. Dans `Apply to each`, appeler SharePoint `Get file content` avec
   l'identifiant du fichier.
5. Appeler **OneDrive for Business** `Create file` dans le dossier
   `EnnoSmart_CIR_Import`.

L'étape 5 crée seulement une copie dans le OneDrive professionnel de travail.
Elle ne crée et ne modifie rien dans SharePoint.

Pour éviter les collisions de noms, utiliser par exemple :

```text
<date-modification>_<id-sharepoint>_<nom-original>
```

EnnoSmart calcule ensuite le SHA-256 du contenu. Deux copies identiques sont
reconnues comme doublons, même si leur nom ou leur dossier diffère.

## Flux continu après la V1

Remplacer le déclencheur manuel par `When a file is created or modified
(properties only)`, conserver le filtre PDF/DOCX, puis les mêmes actions
`Get file content` et `OneDrive for Business / Create file`.

## Dossier local EnnoSmart

Après synchronisation OneDrive, renseigner dans `backend_api/.env` :

```env
POWER_AUTOMATE_IMPORT_ROOT=C:/Users/dell/OneDrive - Ennodev/ENNODEV - Clients
```

Redémarrer le backend. Dans `CIR Memory > Collecte automatique`, le choix
`Dossier OneDrive professionnel` devient disponible.

## Garanties EnnoSmart

EnnoSmart n'expose aucune route de création, modification, déplacement ou
suppression dans le dossier d'import. Les opérations autorisées sont : lister,
lire et calculer un hash. L'indexation Chroma reste une action séparée qui
demande une validation explicite du superadmin.
