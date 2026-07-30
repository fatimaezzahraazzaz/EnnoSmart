# EnnoScholar Legal Fulltext MCP — v1.1

Serveur MCP autonome chargé de retrouver une copie publiquement accessible du même article scientifique après la sélection du consultant.

## Périmètre exact

Ce composant intervient uniquement après la sélection des articles. Il ne remplace pas :

- la recherche scientifique initiale ;
- le ranking BGE ;
- les tags Direct / Connexe / Fondamental ;
- l'extraction PyMuPDF ;
- la construction des Article Cards ;
- la rédaction de l'état de l'art.

Le MCP reçoit DOI, titre, auteurs, année et URL connues, puis interroge :

1. URL PDF directe déjà connue ;
2. Unpaywall ;
3. CORE ;
4. OpenAlex ;
5. Semantic Scholar ;
6. HAL ;
7. arXiv ;
8. Europe PMC ;
9. Zenodo ;
10. recherche Web publique optionnelle via Brave Search.

La recherche Web publique sert à découvrir les PDF accessibles sur des pages auteurs, laboratoires et dépôts universitaires. Elle n'est activée que si `BRAVE_SEARCH_API_KEY` est renseignée.

Le MCP valide l'identité de l'article, vérifie que l'URL renvoie réellement un PDF et retourne une réponse traçable. Aucun PDF n'est stocké par le MCP.

## Provenance v1.1

Chaque candidat possède maintenant :

```text
access_type
rights_status
source_domain
discovered_via
```

Exemples :

```text
repository_copy + repository_terms
public_author_copy + publicly_accessible_license_unknown
publisher_open_access + license_explicit
preprint + repository_terms
```

Une copie publique dont la licence n'est pas explicitement déclarée reste distinguée d'un article Open Access sous licence claire.

## Installation

Depuis `C:\EnnoSmart` :

```powershell
py -3.12 -m venv .venv-mcp
.\.venv-mcp\Scripts\Activate.ps1
pip install -r .\mcp_servers\legal_fulltext_mcp\requirements.txt
```

Copier les variables de `.env.example` dans `C:\EnnoSmart\.env`, puis renseigner au minimum :

```env
UNPAYWALL_EMAIL=votre-adresse@example.com
```

Pour activer la découverte automatique sur le Web public :

```env
BRAVE_SEARCH_API_KEY=VOTRE_CLE
```

Les providers nécessitant une clé restent désactivés tant que leur clé est absente.

## Lancement

```powershell
cd C:\EnnoSmart
.\.venv-mcp\Scripts\python.exe -m mcp_servers.legal_fulltext_mcp.server
```

Endpoints par défaut :

```text
MCP officiel : http://127.0.0.1:8010/mcp
Passerelle backend : http://127.0.0.1:8010/api/resolve
Santé : http://127.0.0.1:8010/health
```

## Test santé

```powershell
Invoke-RestMethod http://127.0.0.1:8010/health
```

## Test d'un article

```powershell
$body = @{
    title = "Radar Cross Section of General Three-Dimensional Scatterers"
    doi = "10.1109/temc.1983.304133"
    authors = @("Allen Taflove", "Korada R. Umashankar")
    year = 1983
    known_urls = @()
    search_all = $true
    force_refresh = $true
} | ConvertTo-Json -Depth 10

$params = @{
    Uri = "http://127.0.0.1:8010/api/resolve"
    Method = "Post"
    ContentType = "application/json"
    Body = $body
}

$result = Invoke-RestMethod @params
$result | ConvertTo-Json -Depth 12
```

## Tests unitaires

```powershell
pytest .\mcp_servers\legal_fulltext_mcp\tests -q
```

## Intégration EnnoScholar

Copier les fichiers contenus dans :

```text
backend_api/services/
```

puis appliquer :

```text
integration/PATCH_INTEGRATION.md
```

## Conformité

Le serveur utilise uniquement des URLs publiquement accessibles et ne contourne aucun paywall, authentification ou restriction technique. Les domaines connus de redistribution non autorisée sont rejetés par le provider de recherche Web publique.
