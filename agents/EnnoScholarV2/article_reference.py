"""Local identity lookup only: no retrieval, scientific ranking or corpus mutation."""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import unquote, urlsplit

from .context import compact_analysis, compact_article, fulltext_status
from .tools.scientific_search import normalize_doi
from .canonical import ArticleStore, version_kind


ABSENT_MESSAGE = ("Je n'ai pas cet article dans les sources de cette session.\n"
    "Fournissez le DOI, l'URL ou le PDF, ou autorisez-moi à le rechercher.")
DISCOVERY_TOOLS = frozenset({"search_scientific", "web_search", "web_search_preview", "find_similar_articles"})
REFERENCE_INSTRUCTION = """Résolution locale des références d'articles : la sélection active
n'est pas le registre complet. Avant d'affirmer qu'un article mentionné est absent,
utilise resolve_article_reference(reference=...) avec son titre, alias, DOI, URL ou
identifiant arXiv. L'outil cherche uniquement dans les notices déjà stockées, sans réseau.
Un titre légèrement abrégé peut suffire. Utilise ensuite l'article_id résolu en interne
pour get_articles, get_fulltext ou analyze_articles ; ne le demande jamais à l'utilisateur.
Si le résultat est ambiguous, pose une courte clarification entre les titres proposés,
sans choisir silencieusement. Un article identifié sans PDF n'est pas un article absent :
get_fulltext peut vérifier son accès, puis analyze_articles peut analyser le matériau
réellement disponible. Un échec fulltext limite l'analyse à l'abstract/métadonnées.
Si no_new_search=true, aucune recherche bibliographique ni Web ni similarité n'est
autorisée. La résolution locale et get_fulltext/analyze_articles sur des notices connues
restent permis. Si la résolution locale est not_found, demande DOI/URL/PDF ou autorisation
de recherche, jamais un article_id EnnoScholar. Aucun nouveau classement scientifique."""


def normalized_reference(value: str) -> str:
    text = unicodedata.normalize("NFKD", value).casefold()
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[\W_]+", " ", text).strip()


def explicitly_no_new_search(message: str) -> bool:
    # A user permission restriction, never a scientific relevance classifier.
    # Inspect only this turn's human message, never abstracts or stored history.
    for clause in re.split(r"[.!?\n;]", message):
        text = normalized_reference(clause)
        patterns = (
            r"\b(?:ne|n) (?:fais|faites|lance|lancez|relance|relancez|effectue|effectuez|cherche|cherchez|recherche|recherchez)\b.{0,60}\b(?:aucune?|pas|jamais)\b.{0,60}\b(?:recherche|recherches|articles|sources)\b",
            r"\b(?:sans|pas de|aucune) (?:(?:aucune|nouvelle|nouvelles|autre|autres) )?(?:recherche|recherches)\b",
            r"\b(?:do not|don t|never) (?:(?:run|start|perform|conduct|launch|do) )?(?:(?:any|a|another|new|additional|literature) )*(?:search|research)\b",
            r"\bno (?:new|additional|further) (?:literature )?search\b",
        )
        if any(re.search(pattern, text) for pattern in patterns):
            return True
    return False


def _clean_identifier(value):
    value = value.strip().rstrip(".,;:!?'»\u201d]\"")
    while value.endswith(")") and value.count(")") > value.count("("):
        value = value[:-1]
    return value


def _url_key(value):
    try:
        parsed = urlsplit(_clean_identifier(value))
        return ((parsed.hostname or "").casefold(), parsed.path.rstrip("/"), parsed.query)
    except ValueError:
        return None


def _arxiv_key(value):
    identifier = r"((?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?)"
    match = re.search(r"(?:arxiv\.org/(?:abs|pdf|html)/|arxiv\s*:\s*)" + identifier, value, re.I)
    if match is None:
        match = re.fullmatch(identifier, value.strip().removesuffix(".pdf"), re.I)
    return re.sub(r"v\d+$", "", match.group(1)).casefold() if match else ""


def _doi_keys(article):
    keys = {normalize_doi(article.doi)}
    for url in [article.url]:
        key = _url_key(url) if url else None
        if key and key[0] in {"doi.org", "dx.doi.org"}:
            keys.add(normalize_doi(unquote(key[1].lstrip("/"))))
    return keys


def _aliases(title):
    prefix, separator, _ = title.partition(":")
    # Short named title prefixes, not arbitrary topic words from the abstract.
    if separator and 4 <= len(prefix.strip()) <= 60 and len(prefix.split()) == 1:
        if sum(char.isupper() for char in prefix) >= 2 or any(char.isdigit() for char in prefix):
            return [normalized_reference(prefix)]
    return []


def _contained(needle, haystack):
    return bool(needle) and f" {needle} " in f" {haystack} "


def _close_title(reference, title):
    left, right = reference.split(), title.split()
    if len(left) < 6 or len(reference) < 35 or len(left) < 0.75 * len(right):
        return False
    if left[:2] != right[:2]:
        return False
    if {word for word in left if any(c.isdigit() for c in word)} != {word for word in right if any(c.isdigit() for c in word)}:
        return False
    # Slight abbreviation: tokens must remain in source order; no synonym ranking.
    iterator = iter(right)
    if all(any(token == candidate for candidate in iterator) for token in left):
        return True
    # One character typo after the stable opening; never arbitrary word replacement.
    if len(left) == len(right) and SequenceMatcher(None, reference, title, autojunk=False).ratio() >= 0.98:
        edits = SequenceMatcher(None, reference, title, autojunk=False).get_opcodes()
        return sum(max(b - a, d - c) for tag, a, b, c, d in edits if tag != "equal") <= 1
    return False


def _resolution(identifiers, method, registry):
    identifiers = list(dict.fromkeys(identifiers))
    if not identifiers:
        return {"status": "not_found", "message": ABSENT_MESSAGE}
    store = ArticleStore(registry)
    works = list(dict.fromkeys(store.work_id(key) for key in identifiers))
    if len(works) > 1:
        return {"status": "ambiguous", "matched_by": method, "candidate_count": len(works),
            "candidates": [{"canonical_id": work_id, "article_id": next(key for key in identifiers if store.work_id(key) == work_id),
                "title": store.by_id[work_id].article.title[:500], "doi": store.by_id[work_id].article.doi}
                for work_id in works[:5]], "message": "Plusieurs travaux distincts correspondent. Lequel souhaitez-vous analyser ?"}
    identifier = identifiers[0]
    record = registry[identifier]
    notice = compact_article(record.article)
    if record.fulltext:
        notice["fulltext"] = fulltext_status(identifier, record.fulltext)
    if record.analysis:
        notice["analysis"] = compact_analysis(record.analysis)
    return {"status": "resolved", "matched_by": method, "article_id": identifier,
        "canonical_id": works[0], "source_version": version_kind(record.article), "article": notice}


def resolve_article_reference(reference: str, registry) -> dict:
    if not isinstance(reference, str) or not reference.strip() or len(reference) > 20000:
        raise ValueError("Une référence d'article non vide et bornée est nécessaire.")
    value = reference.strip()
    matches = [key for key in registry if key == value or ((key.startswith("art_") or any(char.isdigit() for char in key))
        and re.search(r"(?<![\w])" + re.escape(key) + r"(?![\w])", value))]
    if matches:
        return _resolution(matches, "article_id", registry)
    dois = {normalize_doi(_clean_identifier(unquote(item))) for item in re.findall(r"10\.\d{4,9}/[^\s<>\"]+", value, re.I)}
    if dois:
        matches = [key for key, record in registry.items() if dois & _doi_keys(record.article)]
        if matches:
            return _resolution(matches, "doi", registry)
    arxiv = _arxiv_key(value)
    if arxiv:
        matches = [key for key, record in registry.items() if arxiv in {
            _arxiv_key(item) for item in [record.article.arxiv_id, record.article.url, record.article.pdf_url] if item}]
        matches.sort(key=lambda key: version_kind(registry[key].article) != "preprint")
        return _resolution(matches, "arxiv", registry)
    urls = re.findall(r"https?://[^\s<>\"]+", value)
    url_keys = {_url_key(url) for url in urls} - {None}
    matches = [key for key, record in registry.items() if url_keys & {_url_key(url)
        for url in [record.article.url, record.article.pdf_url] if url}]
    if matches:
        return _resolution(matches, "url", registry)
    # Known associated URLs resolve to works, never become each version's own DOI.
    matches = [key for key, record in registry.items() if url_keys & {_url_key(url) for url in record.article.work_urls if url}]
    if matches:
        return _resolution(matches, "work_url", registry)
    if dois:
        return _resolution([], "doi", registry)
    normalized = normalized_reference(value)
    matches = [key for key, record in registry.items() if normalized == normalized_reference(record.article.title)]
    if matches:
        return _resolution(matches, "title", registry)
    matches = [key for key, record in registry.items() if len(normalized_reference(record.article.title)) >= 35
        and _contained(normalized_reference(record.article.title), normalized)]
    if matches:
        return _resolution(matches, "title", registry)
    matches = [key for key, record in registry.items() if ":" not in value
        and any(_contained(alias, normalized) for alias in _aliases(record.article.title))]
    if matches:
        return _resolution(matches, "alias", registry)
    return _resolution([key for key, record in registry.items() if _close_title(normalized, normalized_reference(record.article.title))], "title_fuzzy", registry)


def resolve_message_reference(message, registry):
    """Pre-resolve a single explicitly labelled reference, or an exact known mention.

    Arbitrary conversation text is not treated as a missing paper. The model can
    always call the local tool for other phrasings.
    """
    labelled = re.search(r"\b(?:article|publication|paper)\s*[:：]\s*([^\n]+)", message, re.I)
    if labelled:
        reference = re.split(r"[.;]\s+(?=(?:utilise|use|ne\b|n['’]|sans\b|do not\b))",
            labelled.group(1).strip(), maxsplit=1, flags=re.I)[0]
        return resolve_article_reference(reference, registry)
    result = resolve_article_reference(message, registry)
    # No fuzzy matching over an entire unstructured instruction paragraph.
    return result if result["status"] == "resolved" and result.get("matched_by") != "title_fuzzy" else None


def clarification_message(result):
    lines = [result["message"]]
    for number, candidate in enumerate(result.get("candidates", []), 1):
        doi = f" — DOI : {candidate['doi']}" if candidate["doi"] else ""
        lines.append(f"{number}. {candidate['title']}{doi}")
    if result.get("candidate_count", 0) > len(result.get("candidates", [])):
        lines.append("Vous pouvez préciser le titre complet ou le DOI.")
    return "\n".join(lines)


def resolve_pending_reference(message, registry, pending):
    """Resolve ordinal replies against server-owned candidates, never corpus order."""
    if not pending or not pending.get("candidates"):
        return None
    text = normalized_reference(message)
    choices = {"premier": 1, "premiere": 1, "first": 1, "deuxieme": 2, "second": 2, "seconde": 2,
        "troisieme": 3, "third": 3, "quatrieme": 4, "fourth": 4, "cinquieme": 5, "fifth": 5}
    match = re.fullmatch(r"(?:(?:le|la|the|article|numero|n|choix) )?(premier|premiere|first|deuxieme|second|seconde|troisieme|third|quatrieme|fourth|cinquieme|fifth|[1-5])(?: (?:article|publication|paper|svp|please))?", text)
    if match:
        number = choices.get(match[1], int(match[1]) if match[1].isdigit() else 0)
        candidates = pending["candidates"]
        if 1 <= number <= len(candidates):
            candidate = candidates[number - 1]
            key = candidate.get("article_id") or candidate.get("canonical_id")
            if key in registry:
                return _resolution([key], "clarification", registry)
        return pending
    if text in {"celui la", "celle la", "celui ci", "celle ci", "that one", "this one"}:
        if len(pending["candidates"]) == 1:
            key = pending["candidates"][0]["article_id"]
            return _resolution([key], "clarification", registry) if key in registry else None
        return pending  # A bare demonstrative cannot identify one of two works.
    return None
