from __future__ import annotations

from .models import FulltextCandidate


_VERSION_SCORES = {
    "publishedversion": 30,
    "acceptedversion": 24,
    "submittedversion": 18,
    "published": 30,
    "accepted": 24,
    "submitted": 18,
}

_ACCESS_SCORES = {
    "publisher_open_access": 26,
    "repository_copy": 22,
    "public_author_copy": 18,
    "preprint": 16,
    "public_pdf": 12,
}

_RIGHTS_SCORES = {
    "license_explicit": 20,
    "repository_terms": 14,
    "publicly_accessible_license_unknown": 6,
}


def candidate_rank_score(candidate: FulltextCandidate) -> float:
    provider_score = max(0, 140 - (candidate.provider_priority * 10))
    pdf_score = 80 if candidate.verified_pdf else 30 if candidate.pdf_url else 0
    identity_score = candidate.identity_score * 100
    license_score = 16 if candidate.license else 0
    version_score = _VERSION_SCORES.get((candidate.version or "").lower(), 8 if candidate.version else 0)
    direct_score = 12 if candidate.pdf_url else 0
    access_score = _ACCESS_SCORES.get(candidate.access_type or "", 0)
    rights_score = _RIGHTS_SCORES.get(candidate.rights_status or "", 0)
    return round(
        provider_score
        + pdf_score
        + identity_score
        + license_score
        + version_score
        + direct_score
        + access_score
        + rights_score,
        4,
    )


def sort_candidates(candidates: list[FulltextCandidate]) -> list[FulltextCandidate]:
    return sorted(candidates, key=candidate_rank_score, reverse=True)
