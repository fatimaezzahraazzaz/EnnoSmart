# Automatisation locale de Memory V2

Ces commandes restent locales. Elles ne créent, ne modifient, ne déplacent et
ne suppriment aucun fichier OneDrive/SharePoint.

## Avant chaque lancement

Ouvrir PowerShell dans `C:\EnnoSmart` :

```powershell
cd C:\EnnoSmart
Set-ExecutionPolicy -Scope Process Bypass
```

Le scan peut être lancé avec l'application ouverte. Avant une commande
`apply`, arrêter le backend afin que Windows ne garde aucun fichier Chroma
ouvert. Le backend pourra être relancé après la fin de la commande.

## 1. Pilote CEVAA / CORPLAUX

### Étape A — simulation et manifeste

```powershell
.\scripts\run_cevaa_corplaux.ps1 scan
```

Cette commande doit afficher exactement un CIR retenu avec le classement :

```text
6NAPSE GROUP > CEVAA > Corplaux > 2024
```

Le manifeste à relire est enregistré ici :

```text
C:\EnnoSmartData\power_automate_import\automation\manifests\pilot_corplaux_latest.json
```

Vérifier notamment :

- `source_integrity_verified: true` ;
- `source_write_operations: 0` ;
- `selected_final_versions: 1` ;
- `ready_to_index: 1` ;
- le PDF `CEVAA_CORPLAUX_CIR-2024_VF.pdf` ;
- l'identité `6NAPSE GROUP / CEVAA / Corplaux / 2024`.

### Étape B — indexation du manifeste validé

Arrêter le backend, puis lancer :

```powershell
.\scripts\run_cevaa_corplaux.ps1 apply
```

Le script extrait CORPLAUX, crée ses cartes, construit une nouvelle collection
Chroma globale dans un dossier isolé, puis archive l'ancien index avant le
basculement. L'original OneDrive n'est jamais ouvert en écriture.

### Étape C — contrôle

```powershell
.\scripts\run_cevaa_corplaux.ps1 status
```

Puis relancer le backend et vérifier CORPLAUX dans l'onglet Bibliothèque.

## 2. Répétition limitée avant tous les clients

Pour tester la découverte sur trois dossiers finaux sans indexer :

```powershell
.\scripts\run_all_clients_cir.ps1 scan -MaxScopes 3
```

Ne pas lancer `apply` sur ce manifeste limité si l'objectif est le corpus
complet. Relancer ensuite le scan sans `-MaxScopes`.

## 3. Tous les clients

### Étape A — scan global sans indexation

```powershell
.\scripts\run_all_clients_cir.ps1 scan
```

Le scan global est non récursif par périmètre : il ouvre uniquement les
fichiers candidats listés dans chaque dossier final. Un CIR final placé à la
racine d'un client ne déclenche donc plus le téléchargement de toute
l'arborescence OneDrive du client.

Par défaut, seuls les CIR finaux confirmés sont retenus. Les CIR seulement
probables restent à vérifier manuellement. Pour les inclure explicitement :

```powershell
.\scripts\run_all_clients_cir.ps1 scan -IncludeProbable
```

Le manifeste global est ici :

```text
C:\EnnoSmartData\power_automate_import\automation\manifests\all_clients_latest.json
```

Relire les compteurs, les identités et les conflits avant de continuer.
Une identité déjà présente avec un autre hash reste bloquée et n'est jamais
écrasée automatiquement.

### Étape B — application du dernier manifeste global

Arrêter le backend, puis lancer :

```powershell
.\scripts\run_all_clients_cir.ps1 apply
```

Le traitement écrit un ledger reprenable après chaque document :

```text
C:\EnnoSmartData\power_automate_import\automation\ledgers\all_clients_ledger.json
```

### Étape C — statut

```powershell
.\scripts\run_all_clients_cir.ps1 status
```

## Emplacements persistants

```text
Memory V2 : C:\EnnoSmartData\experience_memory_v2
Chroma    : C:\EnnoSmartData\experience_memory_v2\chroma
SQLite    : C:\EnnoSmartData\experience_memory_v2\chroma\chroma.sqlite3
Collection: ennosmart_memory_v2_global
Audits    : C:\EnnoSmartData\power_automate_import
```
