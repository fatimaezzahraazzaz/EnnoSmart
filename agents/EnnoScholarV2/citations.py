from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from .canonical import ArticleStore, arxiv_key, normalize_doi, normalized_title
from .models import Citation


class BibliographyError(ValueError):
    """A final reference cannot be grounded in a stored canonical work."""


class FinalReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    article_id: str
    title: str
    doi: str
    arxiv_id: str
    contribution: str = Field(max_length=1600)
    evidence_ids: list[str] = Field(max_length=3)


class BibliographicDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(max_length=6000)
    references: list[FinalReference] = Field(min_length=1, max_length=12)


def bibliography_format():
    # Same strict structured-output convention as the existing article analysis.
    schema = BibliographicDraft.model_json_schema()
    for definition in [schema, *schema.get("$defs", {}).values()]:
        if definition.get("type") == "object":
            definition["required"] = list(definition.get("properties", {}))
            definition["additionalProperties"] = False
    return {"format": {"type": "json_schema", "name": "scholar_bibliography", "strict": True, "schema": schema}}


BIBLIOGRAPHY_INSTRUCTION = """Pour la réponse bibliographique finale, utilise le format JSON
scholar_bibliography. references contient exactement tes finalistes, dans TON ordre scientifique.
Pour chacun, recopie ensemble article_id, title, doi et arxiv_id depuis LA MÊME notice serveur.
La contribution doit rester fondée sur l'abstract ou les analyses disponibles. Le serveur rendra
les titres, liens, identifiants et statuts d'accès; ne les rédige pas dans summary/contribution.
N'y insère ni URL, référence bibliographique supplémentaire, citation entre guillemets ou numéro
de page. evidence_ids doit être vide ou désigner des preuves déjà vérifiées dans les analyses
de CE travail. Le serveur restituera lui-même l'extrait exact et sa page. Aucun article extérieur
au registre, aucun rapprochement titre/DOI supposé, aucune preuve ou page inventée."""


def validate_reference(reference: FinalReference, store: ArticleStore):
    work = store.by_id.get(reference.article_id)
    if work is None:
        raise BibliographyError("article_id inconnu : " + reference.article_id)
    article = next((version for version in work.versions if version.id == reference.article_id), work.article)
    if (normalized_title(reference.title) != normalized_title(article.title)
            or normalize_doi(reference.doi) != normalize_doi(article.doi)
            or arxiv_key(reference.arxiv_id) != arxiv_key(article.arxiv_id)):
        raise BibliographyError("Association titre/DOI/arXiv/article_id incohérente : " + reference.article_id)
    return work


def render_bibliography(text: str, registry, finalists):
    """Validate the complete manifest, then render identity/access/evidence server-side."""
    try:
        draft = BibliographicDraft.model_validate_json(text)
    except ValueError as exc:
        raise BibliographyError("Réponse bibliographique structurée invalide.") from exc
    store = ArticleStore(registry)
    works = [validate_reference(reference, store) for reference in draft.references]
    expected = {store.work_id(identifier) for identifier in finalists}
    actual = [work.canonical_id for work in works]
    if len(set(actual)) != len(actual) or set(actual) != expected or None in expected:
        raise BibliographyError("Les références doivent correspondre exactement aux finalistes canoniques.")
    # Free prose is never allowed to manufacture another bibliographic identity,
    # quoted evidence or pagination outside the validated manifest.
    prohibited = re.compile(r"https?://|\]\(|\b10\.\d{4,9}/|\b\d{4}\.\d{4,5}(?:v\d+)?\b|\bart_[a-z0-9]+\b|\b(?:pages?|pp?|p\.)\s*\d|[\"«»\u201c\u201d]", re.I)
    if any(prohibited.search(value) for value in [draft.summary, *(row.contribution for row in draft.references)]):
        raise BibliographyError("Référence, citation ou pagination non contrôlée dans le texte libre.")
    lines, citations = [draft.summary.strip(), ""], []
    for index, (reference, work) in enumerate(zip(draft.references, works), 1):
        article = registry[reference.article_id].article
        url = "https://doi.org/" + article.doi if article.doi else "https://arxiv.org/abs/" + article.arxiv_id if article.arxiv_id else article.url
        title = article.title.replace("[", "\\[").replace("]", "\\]")
        label = f"[{title}]({url})" if url.startswith(("http://", "https://")) else title
        identifiers = " — " + ("DOI : `" + article.doi + "`" if article.doi else "arXiv : `" + article.arxiv_id + "`" if article.arxiv_id else "Métadonnées sans DOI/arXiv")
        lines.extend([f"{index}. **{label}**{identifiers}", "", reference.contribution.strip(), ""])
        payloads = store.payloads(work.canonical_id)
        full = any(row.fulltext and row.fulltext.access_level == "fulltext" for row in payloads.values())
        analyzed_full = any(row.analysis and row.analysis.analysis_basis == "fulltext" for row in payloads.values())
        access = ("Texte récupéré et passages réellement analysés (lecture non exhaustive)." if analyzed_full
            else "PDF récupéré mais passages non analysés." if full
            else "Abstract uniquement." if work.article.abstract else "Métadonnées uniquement.")
        lines.extend(["Accès vérifié : " + access, ""])
        evidence = {item.evidence_id: item for row in payloads.values() if row.analysis for item in row.analysis.evidence}
        for identifier in reference.evidence_ids:
            item = evidence.get(identifier)
            if item is None:
                raise BibliographyError("evidence_id non vérifié pour ce travail : " + identifier)
            location = " — page PDF " + str(item.page) if item.page is not None else ""
            source = " — [source réellement lue](" + item.source_url + ")" if item.source_url.startswith(("https://", "http://")) else ""
            lines.extend(["> " + item.excerpt.replace("\n", " ") + location + source, ""])
        if url.startswith(("http://", "https://")):
            citations.append(Citation(url=url, title=article.title, cited=True))
    return "\n".join(lines).strip(), citations, actual


def parse_output(output: list[dict]) -> tuple[str, list[Citation]]:
    """Keep exact text offsets, including across multiple output text blocks."""
    parts: list[str] = []
    citations: list[Citation] = []
    seen_sources: set[str] = set()
    offset = 0
    for item in output:
        if item.get("type") == "web_search_call":
            for source in (item.get("action") or {}).get("sources", []):
                url = source.get("url", "")
                if url.startswith(("https://", "http://")) and url not in seen_sources:
                    seen_sources.add(url)
                    citations.append(Citation(url=url, title=source.get("title", ""), cited=False))
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") != "output_text":
                continue
            value = content.get("text", "")
            if parts:
                offset += 1
            for annotation in content.get("annotations", []):
                if annotation.get("type") != "url_citation":
                    continue
                url = annotation.get("url", "")
                if not url.startswith(("https://", "http://")):
                    continue
                start, end = annotation.get("start_index"), annotation.get("end_index")
                valid = isinstance(start, int) and isinstance(end, int) and 0 <= start <= end <= len(value)
                citations.append(Citation(url=url, title=annotation.get("title", ""),
                    start_index=offset + start if valid else None,
                    end_index=offset + end if valid else None))
            parts.append(value)
            offset += len(value)
    return "\n".join(parts), citations
