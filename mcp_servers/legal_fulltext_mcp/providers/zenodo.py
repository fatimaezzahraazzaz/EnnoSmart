from __future__ import annotations

from ..domain.models import ArticleIdentity, FulltextCandidate
from ..domain.normalizers import looks_like_pdf_url, normalize_doi
from ..infrastructure.http import HttpRequestError, ResilientHttpClient
from .base import FulltextProvider
from .source_utils import classify_public_source


class ZenodoProvider(FulltextProvider):
    name = "zenodo"
    priority = 8

    async def search(self, article: ArticleIdentity, http: ResilientHttpClient) -> list[FulltextCandidate]:
        doi = normalize_doi(article.doi)
        title_query = f'title:"{article.title.replace(chr(34), " ")}"'
        queries = [f'doi:"{doi}"', title_query] if doi else [title_query]
        hits: list[dict] = []
        seen_records: set[str] = set()
        first_error: HttpRequestError | None = None
        for query in queries:
            try:
                data = await http.get_json(
                    "https://zenodo.org/api/records",
                    params={"q": query, "size": 10},
                )
            except HttpRequestError as exc:
                first_error = first_error or exc
                continue
            raw_hits = ((data.get("hits") or {}).get("hits") or []) if isinstance(data.get("hits"), dict) else []
            for record in raw_hits:
                if not isinstance(record, dict):
                    continue
                key = str(record.get("id") or record.get("doi") or "").lower()
                if key and key not in seen_records:
                    seen_records.add(key)
                    hits.append(record)
        if not hits and first_error is not None:
            raise first_error
        out: list[FulltextCandidate] = []
        for record in hits:
            metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
            creators = metadata.get("creators") or []
            authors = [str(x.get("name")) for x in creators if isinstance(x, dict) and x.get("name")]
            publication_date = str(metadata.get("publication_date") or "")
            year = int(publication_date[:4]) if publication_date[:4].isdigit() else None
            license_obj = metadata.get("license")
            if isinstance(license_obj, dict):
                license_value = license_obj.get("id") or license_obj.get("title")
            else:
                license_value = str(license_obj) if license_obj else None
            for file_info in record.get("files") or []:
                if not isinstance(file_info, dict):
                    continue
                links = file_info.get("links") if isinstance(file_info.get("links"), dict) else {}
                url = links.get("download") or links.get("self")
                key = str(file_info.get("key") or "")
                file_type = str(file_info.get("type") or file_info.get("mimetype") or "").lower()
                if not isinstance(url, str) or not (
                    looks_like_pdf_url(url) or key.lower().endswith(".pdf") or "application/pdf" in file_type
                ):
                    continue
                access_type, rights_status, domain = classify_public_source(url, license_value=license_value)
                out.append(
                    FulltextCandidate(
                        provider=self.name,
                        provider_priority=self.priority,
                        pdf_url=url,
                        landing_url=(record.get("links") or {}).get("html") if isinstance(record.get("links"), dict) else None,
                        license=license_value,
                        host_type="repository",
                        legal_access=True,
                        access_type=access_type,
                        rights_status=rights_status,
                        source_domain=domain,
                        discovered_via="zenodo",
                        candidate_doi=metadata.get("doi"),
                        candidate_title=metadata.get("title"),
                        candidate_authors=authors,
                        candidate_year=year,
                        raw_metadata={"zenodo_id": record.get("id"), "filename": key},
                    )
                )
        return out
