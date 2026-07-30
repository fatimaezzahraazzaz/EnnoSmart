from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from .config import get_settings
from .domain.identity import validate_identity
from .domain.models import ArticleIdentity, FulltextCandidate
from .services.resolver import LegalFulltextResolver


settings = get_settings()
resolver = LegalFulltextResolver(settings)

mcp = FastMCP(
    "EnnoScholar Legal Fulltext",
    instructions=(
        "Résoudre uniquement des copies Open Access ou publiquement accessibles du même article "
        "après sélection humaine. Les URLs de la première phase sont testées en priorité. "
        "Ne jamais effectuer de ranking BGE ni de rédaction scientifique."
    ),
    host=settings.host,
    port=settings.port,
    stateless_http=True,
    json_response=True,
)


@mcp.tool()
async def resolve_legal_fulltext(
    title: str,
    doi: str | None = None,
    authors: list[str] | None = None,
    year: int | None = None,
    known_urls: list[str] | None = None,
    article_id: int | str | None = None,
    source: str | None = None,
    search_all: bool = False,
    force_refresh: bool = False,
) -> dict:
    """Trouver une URL PDF légalement accessible correspondant au même article."""
    article = ArticleIdentity(
        article_id=article_id,
        doi=doi,
        title=title,
        authors=authors or [],
        year=year,
        known_urls=known_urls or [],
        source=source,
    )
    result = await resolver.resolve(article, search_all=search_all, force_refresh=force_refresh)
    return result.model_dump(mode="json")


@mcp.tool()
async def get_open_access_locations(
    title: str,
    doi: str | None = None,
    authors: list[str] | None = None,
    year: int | None = None,
    known_urls: list[str] | None = None,
    force_refresh: bool = False,
) -> dict:
    """Retourner toutes les localisations Open Access trouvées et leur validation."""
    article = ArticleIdentity(
        doi=doi,
        title=title,
        authors=authors or [],
        year=year,
        known_urls=known_urls or [],
    )
    result = await resolver.resolve(article, search_all=True, force_refresh=force_refresh)
    return result.model_dump(mode="json")


@mcp.tool()
def validate_article_identity(
    selected_title: str,
    candidate_title: str,
    selected_doi: str | None = None,
    candidate_doi: str | None = None,
    selected_authors: list[str] | None = None,
    candidate_authors: list[str] | None = None,
    selected_year: int | None = None,
    candidate_year: int | None = None,
) -> dict:
    """Vérifier si deux ensembles de métadonnées décrivent le même article."""
    selected = ArticleIdentity(
        doi=selected_doi,
        title=selected_title,
        authors=selected_authors or [],
        year=selected_year,
    )
    candidate = FulltextCandidate(
        provider="manual_validation",
        candidate_doi=candidate_doi,
        candidate_title=candidate_title,
        candidate_authors=candidate_authors or [],
        candidate_year=candidate_year,
    )
    result = validate_identity(
        selected,
        candidate,
        min_identity_score=settings.min_identity_score,
        min_title_score=settings.min_title_score,
        allow_title_match=settings.allow_title_match,
        doi_title_conflict_score=settings.doi_title_conflict_score,
        exact_title_repository_score=settings.exact_title_repository_score,
        allow_exact_title_repository_match=settings.allow_exact_title_repository_match,
    )
    return result.model_dump(mode="json")


@mcp.tool()
def legal_fulltext_health() -> dict:
    """Afficher l'état du MCP et les providers activés."""
    return resolver.health().model_dump(mode="json")


@mcp.custom_route("/api/resolve", methods=["POST"])
async def resolve_rest(request: Request) -> JSONResponse:
    """Passerelle HTTP légère pour le backend FastAPI existant."""
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ValueError("Le corps JSON doit être un objet.")
        article = ArticleIdentity(
            article_id=payload.get("article_id"),
            doi=payload.get("doi"),
            title=payload.get("title") or "",
            authors=payload.get("authors") or [],
            year=payload.get("year"),
            known_urls=payload.get("known_urls") or [],
            source=payload.get("source"),
        )
        result = await resolver.resolve(
            article,
            search_all=bool(payload.get("search_all", False)),
            force_refresh=bool(payload.get("force_refresh", False)),
        )
        return JSONResponse(result.model_dump(mode="json"), status_code=200)
    except Exception as exc:
        return JSONResponse(
            {
                "ok": False,
                "found": False,
                "legal_access": False,
                "same_article": False,
                "status": "invalid_request",
                "failure_code": "invalid_request",
                "best_candidate": None,
                "locations": [],
                "attempts": [],
                "needs_consultant_upload": False,
                "retry_recommended": False,
                "reason": str(exc),
                "provenance": None,
            },
            status_code=400,
        )


@mcp.custom_route("/health", methods=["GET"])
async def health_rest(request: Request) -> JSONResponse:
    del request
    return JSONResponse(resolver.health().model_dump(mode="json"), status_code=200)


@mcp.custom_route("/health/runtime", methods=["GET"])
async def health_runtime_rest(request: Request) -> JSONResponse:
    """État détaillé : providers actifs, exclus, erreurs et cooldowns."""
    del request
    return JSONResponse(resolver.health().model_dump(mode="json"), status_code=200)


def main() -> None:
    if not settings.enabled:
        raise SystemExit("ENNOSCHOLAR_LEGAL_MCP_ENABLED=0 : serveur désactivé.")
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
