# Intégration dans le code EnnoScholar actuel — v1.1

Le MCP intervient uniquement après la sélection du consultant et avant l'extraction directe. La recherche scientifique initiale, BGE et les tags ne changent pas.

## 1. Copier les services backend

Copier :

```text
backend_api/services/legal_fulltext_mcp_client.py
backend_api/services/legal_fulltext_mcp_sdk_client.py
backend_api/services/scholar_legal_fulltext_bridge.py
```

vers :

```text
C:\EnnoSmart\backend_api\services\
```

## 2. `backend_api/services/scholar_fulltext_fetcher.py`

Ajouter l'import :

```python
from services.scholar_legal_fulltext_bridge import build_mcp_candidates_for_article
```

Dans `build_candidate_urls_for_article(article)`, juste après :

```python
candidates: List[Dict[str, Any]] = []
```

ajouter :

```python
try:
    mcp_candidates = build_mcp_candidates_for_article(article)
    candidates.extend(mcp_candidates)
except Exception as exc:
    print(
        f"[EnnoScholar][LegalMCP] article_id={getattr(article, 'id', None)} "
        f"fallback_local reason={exc}",
        flush=True,
    )
```

Si ton annotation actuelle est `List[Dict[str, str]]`, remplace-la par :

```python
List[Dict[str, Any]]
```

car les candidats MCP contiennent aussi des booléens, nombres et métadonnées de provenance.

La fonction de déduplication doit conserver tout le dictionnaire candidat, pas seulement `url`, `kind` et `source`.

## 3. `backend_api/services/scholar_pdf_direct_extractor.py`

Dans le résultat de succès de `extract_direct_fulltext_for_article`, après :

```python
"pdf_source_resolver": candidate.get("source"),
```

ajouter :

```python
"retrieved_via_mcp": bool(candidate.get("retrieved_via_mcp")),
"legal_access": candidate.get("legal_access"),
"legal_provider": (
    str(candidate.get("source") or "").split(":", 1)[1]
    if str(candidate.get("source") or "").startswith("legal_mcp:")
    else None
),
"legal_license": candidate.get("license"),
"legal_version": candidate.get("version"),
"access_type": candidate.get("access_type"),
"rights_status": candidate.get("rights_status"),
"source_domain": candidate.get("source_domain"),
"discovered_via": candidate.get("discovered_via"),
"identity_score": candidate.get("identity_score"),
"identity_method": candidate.get("identity_method"),
"same_article": candidate.get("same_article"),
```

Le PDF continue d'être téléchargé en mémoire et n'est pas conservé.

## 4. `backend_api/services/article_card_builder.py`

Quand tu construis la provenance du texte intégral, ajouter :

```python
"fulltext_provenance": {
    "retrieved_via_mcp": bool(fulltext_payload.get("retrieved_via_mcp")),
    "provider": fulltext_payload.get("legal_provider"),
    "license": fulltext_payload.get("legal_license"),
    "version": fulltext_payload.get("legal_version"),
    "access_type": fulltext_payload.get("access_type"),
    "rights_status": fulltext_payload.get("rights_status"),
    "source_domain": fulltext_payload.get("source_domain"),
    "discovered_via": fulltext_payload.get("discovered_via"),
    "identity_score": fulltext_payload.get("identity_score"),
    "identity_method": fulltext_payload.get("identity_method"),
    "same_article": fulltext_payload.get("same_article"),
    "pdf_saved": False,
},
```

## 5. `backend_api/services/ennoscholar_state_of_art_orchestrator.py`

Ajouter les imports :

```python
from services.scholar_fulltext_fetcher import fetch_fulltext_pdf_for_selected_articles
from services.scholar_pdf_direct_extractor import extract_direct_fulltext_for_selected_articles
```

Après la Phase 1 et avant la Phase 2D Article Cards, ajouter :

```python
print("[EnnoScholar][SOA] Phase 2A START legal_fulltext_resolution")
fulltext_resolution = fetch_fulltext_pdf_for_selected_articles(
    db=db,
    project=project,
    force=force_article_cards,
    max_articles=None,
)
print(
    "[EnnoScholar][SOA] Phase 2A OK "
    f"pdf_available={fulltext_resolution.get('pdf_available_count')} "
    f"need_upload={fulltext_resolution.get('need_upload_count')}"
)

print("[EnnoScholar][SOA] Phase 2B START direct_text_extraction")
direct_extraction = extract_direct_fulltext_for_selected_articles(
    db=db,
    project=project,
    force=force_article_cards,
    max_articles=None,
)
print(
    "[EnnoScholar][SOA] Phase 2B OK "
    f"text_extracted={direct_extraction.get('text_extracted_count')} "
    f"blocked={direct_extraction.get('blocked_count')} "
    f"no_pdf={direct_extraction.get('no_pdf_count')}"
)
```

Puis conserver la Phase 2D actuelle :

```python
article_cards_payload = build_article_cards_for_selected_articles(...)
```

## 6. `.env` canonique `C:\EnnoSmart\.env`

Ajouter ou compléter :

```env
ENNOSCHOLAR_LEGAL_MCP_ENABLED=1
ENNOSCHOLAR_LEGAL_MCP_HOST=127.0.0.1
ENNOSCHOLAR_LEGAL_MCP_PORT=8010
ENNOSCHOLAR_LEGAL_MCP_URL=http://127.0.0.1:8010/mcp
ENNOSCHOLAR_LEGAL_MCP_REST_URL=http://127.0.0.1:8010/api/resolve

ENNOSCHOLAR_LEGAL_MCP_TIMEOUT_SECONDS=25
ENNOSCHOLAR_LEGAL_MCP_MAX_RETRIES=3
ENNOSCHOLAR_LEGAL_MCP_VERIFY_PDF=1
ENNOSCHOLAR_LEGAL_MCP_STOP_ON_FIRST_VERIFIED=1
ENNOSCHOLAR_LEGAL_MCP_CACHE_ENABLED=1
ENNOSCHOLAR_LEGAL_MCP_CACHE_TTL_SECONDS=86400

UNPAYWALL_EMAIL=VOTRE_EMAIL
OPENALEX_API_KEY=
CORE_API_KEY=
SEMANTIC_SCHOLAR_API_KEY=

# Optionnel : découverte automatique de pages auteurs/dépôts publics
BRAVE_SEARCH_API_KEY=
ENNOSCHOLAR_PUBLIC_WEB_COUNT=10
ENNOSCHOLAR_PUBLIC_WEB_COUNTRY=US
ENNOSCHOLAR_PUBLIC_WEB_SEARCH_LANG=en

ENNOSCHOLAR_LEGAL_MCP_PROVIDER_ORDER=existing_url,unpaywall,core,openalex,semantic_scholar,hal,arxiv,europe_pmc,zenodo,public_web
```

## 7. Relancer le serveur MCP

```powershell
cd C:\EnnoSmart
Ctrl+C
.\run_server.ps1
```

Puis vérifier :

```powershell
Invoke-RestMethod http://127.0.0.1:8010/health
```

## 8. Test automatique sans `known_urls`

Avec `BRAVE_SEARCH_API_KEY` configurée :

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

$result = Invoke-RestMethod -Uri "http://127.0.0.1:8010/api/resolve" -Method Post -ContentType "application/json" -Body $body
$result | ConvertTo-Json -Depth 12
```

Regarder particulièrement :

```text
best_candidate.provider
best_candidate.access_type
best_candidate.rights_status
best_candidate.source_domain
best_candidate.discovered_via
```
