from __future__ import annotations

from difflib import SequenceMatcher
from urllib.parse import urlparse

from .models import ArticleIdentity, FulltextCandidate, IdentityValidation
from .normalizers import author_last_name, normalize_doi, normalize_title


TRUSTED_REPOSITORY_DOMAINS = {
    "arxiv.org",
    "export.arxiv.org",
    "zenodo.org",
    "hal.science",
    "archives-ouvertes.fr",
    "europepmc.org",
    "ncbi.nlm.nih.gov",
    "pmc.ncbi.nlm.nih.gov",
    "ssrn.com",
    "papers.ssrn.com",
    "deliverypdf.ssrn.com",
    "publica.fraunhofer.de",
}

REPOSITORY_DOI_PREFIXES = (
    "10.48550/arxiv.",
    "10.5281/zenodo.",
    "10.2139/ssrn.",
)


def _jaccard(a: str, b: str) -> float:
    sa = set(a.split())
    sb = set(b.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def title_similarity(a: str | None, b: str | None) -> float:
    na = normalize_title(a)
    nb = normalize_title(b)
    if not na or not nb:
        return 0.0
    sequence = SequenceMatcher(None, na, nb).ratio()
    jaccard = _jaccard(na, nb)
    return round((sequence * 0.58) + (jaccard * 0.42), 6)


def author_similarity(selected: list[str], candidate: list[str]) -> float:
    selected_names = {author_last_name(x) for x in selected if author_last_name(x)}
    candidate_names = {author_last_name(x) for x in candidate if author_last_name(x)}
    if not selected_names or not candidate_names:
        return 0.5
    return round(len(selected_names & candidate_names) / max(1, len(selected_names)), 6)


def _candidate_domain(candidate: FulltextCandidate) -> str:
    if candidate.source_domain:
        return candidate.source_domain.lower().strip()
    for value in [candidate.pdf_url, candidate.landing_url]:
        try:
            host = (urlparse(value or "").hostname or "").lower()
            if host:
                return host
        except Exception:
            continue
    return ""


def _trusted_repository(candidate: FulltextCandidate) -> bool:
    if (candidate.host_type or "").lower() in {"repository", "institutional_repository"}:
        return True
    if (candidate.access_type or "").lower() in {"repository_copy", "preprint", "public_author_copy"}:
        return True
    domain = _candidate_domain(candidate)
    return any(domain == item or domain.endswith("." + item) for item in TRUSTED_REPOSITORY_DOMAINS)


def _repository_alias_doi(value: str | None) -> bool:
    doi = normalize_doi(value)
    return any(doi.startswith(prefix) for prefix in REPOSITORY_DOI_PREFIXES)


def validate_identity(
    selected: ArticleIdentity,
    candidate: FulltextCandidate,
    *,
    min_identity_score: float = 0.90,
    min_title_score: float = 0.82,
    allow_title_match: bool = True,
    doi_title_conflict_score: float = 0.55,
    exact_title_repository_score: float = 0.985,
    allow_exact_title_repository_match: bool = True,
) -> IdentityValidation:
    selected_doi = normalize_doi(selected.doi)
    candidate_doi = normalize_doi(candidate.candidate_doi)
    candidate_title = candidate.candidate_title or ""
    t_score = title_similarity(selected.title, candidate_title)

    if selected_doi and candidate_doi:
        if selected_doi == candidate_doi:
            # Un DOI identique ne doit pas masquer des métadonnées manifestement incohérentes.
            if selected.title and candidate_title and t_score < doi_title_conflict_score:
                return IdentityValidation(
                    same_article=False,
                    method="doi_title_conflict",
                    score=0.0,
                    title_score=t_score,
                    warnings=[
                        "Le DOI est identique mais le titre candidat est incompatible. "
                        "Le DOI source est probablement erroné ou associé au mauvais article."
                    ],
                )
            return IdentityValidation(
                same_article=True,
                method="same_doi",
                score=1.0,
                title_score=t_score or 1.0,
                author_score=author_similarity(selected.authors, candidate.candidate_authors),
                year_score=1.0,
            )

        # Une prépublication peut avoir un DOI de dépôt différent du DOI éditeur.
        repository_alias = _repository_alias_doi(selected_doi) or _repository_alias_doi(candidate_doi)
        if not repository_alias:
            return IdentityValidation(
                same_article=False,
                method="doi_mismatch",
                score=0.0,
                title_score=t_score,
                warnings=["Le DOI candidat est différent du DOI sélectionné."],
            )

    if not allow_title_match:
        return IdentityValidation(
            same_article=False,
            method="insufficient_metadata",
            score=0.0,
            warnings=["Validation par métadonnées désactivée et DOI identique indisponible."],
        )

    if t_score < min_title_score:
        return IdentityValidation(
            same_article=False,
            method="title_mismatch",
            score=t_score,
            title_score=t_score,
            warnings=["La similarité du titre est insuffisante."],
        )

    a_score = author_similarity(selected.authors, candidate.candidate_authors)
    if selected.year and candidate.candidate_year:
        delta = abs(selected.year - candidate.candidate_year)
        y_score = 1.0 if delta == 0 else 0.8 if delta == 1 else 0.0
    else:
        y_score = 0.5

    explicit_author_conflict = bool(
        selected.authors and candidate.candidate_authors and a_score < 0.25
    )
    explicit_year_conflict = bool(
        selected.year and candidate.candidate_year and abs(selected.year - candidate.candidate_year) > 1
    )
    if explicit_author_conflict or explicit_year_conflict:
        conflicts: list[str] = []
        if explicit_author_conflict:
            conflicts.append("les auteurs sont incompatibles")
        if explicit_year_conflict:
            conflicts.append("les années sont incompatibles")
        return IdentityValidation(
            same_article=False,
            method="insufficient_metadata",
            score=0.0,
            title_score=t_score,
            author_score=a_score,
            year_score=y_score,
            warnings=["Le titre ressemble au titre sélectionné, mais " + " et ".join(conflicts) + "."],
        )

    score = (t_score * 0.70) + (a_score * 0.20) + (y_score * 0.10)

    # Cas sûr et fréquent : le titre est exact sur une source universitaire ou
    # un dépôt, et certaines métadonnées secondaires manquent d'un côté. Les
    # conflits explicites ont déjà été rejetés ci-dessus.
    partial_metadata = (
        not selected.authors
        or not candidate.candidate_authors
        or selected.year is None
        or candidate.candidate_year is None
    )
    if (
        allow_exact_title_repository_match
        and partial_metadata
        and t_score >= exact_title_repository_score
        and _trusted_repository(candidate)
    ):
        warnings = [
            "Correspondance acceptée par titre quasi exact sur un dépôt scientifique reconnu ; "
            "une partie des auteurs ou de l'année était absente des métadonnées."
        ]
        if selected_doi and candidate_doi and selected_doi != candidate_doi:
            warnings.append("Le DOI candidat est traité comme un DOI de dépôt/prépublication.")
        return IdentityValidation(
            same_article=True,
            method="exact_title_repository_match",
            score=max(round(score, 6), 0.92),
            title_score=t_score,
            author_score=a_score,
            year_score=y_score,
            warnings=warnings,
        )

    same = score >= min_identity_score
    method = "metadata_match" if same else "insufficient_metadata"
    if same and selected_doi and candidate_doi and selected_doi != candidate_doi:
        method = "repository_doi_alias_match"

    return IdentityValidation(
        same_article=same,
        method=method,
        score=round(score, 6),
        title_score=t_score,
        author_score=a_score,
        year_score=y_score,
        warnings=[] if same else ["La correspondance titre/auteurs/année est insuffisante."],
    )
