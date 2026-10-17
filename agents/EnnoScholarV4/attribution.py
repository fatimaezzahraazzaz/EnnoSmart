"""Deterministic attribution checks between drafted sentences, publications and exact evidence.

No LLM call. A scientific name (method/architecture written with internal capitals
or hyphenated capitals, or « X et al. ») written in a draft must either identify a
publication of the draft (its title or authors) or be supported by an
exact passage cited in the same sentence. A sentence naming one publication must
cite that publication's evidence when it cites evidence at all. Claims backed only
by related-work/background passages are tagged so a cited prior work is never
presented as the studied method.
"""
from __future__ import annotations

import re

RELATED_SECTION = re.compile(r"related\s+works?|state\s+of\s+the\s+art|background|prior\s+works?|previous\s+works?|"
    r"literature\s+review|travaux\s+(?:connexes|antérieurs|anterieurs)|état\s+de\s+l['’]art|etat\s+de\s+l['’]art", re.I)
SELF_REFERENCE = re.compile(r"\b(?:in\s+this\s+(?:work|paper|study|article)|we\s+(?:propose|present|introduce|develop)|"
    r"our\s+(?:method|approach|model|framework|contributions?|work)|this\s+paper|nous\s+proposons)\b", re.I)
METHOD_NAME = re.compile(r"\b(?:[A-Za-z][a-z0-9]*[a-z][A-Z][A-Za-z0-9]*|[A-Z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*-[A-Z][A-Za-z0-9]*)(?:-[A-Za-z0-9]+)*\b")
ET_AL = re.compile(r"\b([A-ZÀ-Ý][\w'’\-]+)\s+(?:\*?et\s+al\.?\*?|et\s+coll\.?|and\s+colleagues)")
MARKER = re.compile(r"\[(ev_[\w-]+)\]")
SENTENCE = re.compile(r"(?<!et al\.)(?<!et coll\.)(?<=[.!?;])\s+(?=[*«(\[A-ZÀ-Ý0-9])|\n+")


def claim_subject(claim, evidence):
    """"background" when every proof is a related-work passage without self-reference."""
    rows = [evidence.get(eid) for eid in claim.get("evidence_ids", [])]
    if rows and all(row and RELATED_SECTION.search(row.get("section") or "") and
            not SELF_REFERENCE.search(row.get("exact_quote", "")) for row in rows):
        return "background"
    return "this_work"


def _key(name):
    return re.sub(r"[^a-z0-9]", "", name.casefold())


def _variants(name):
    key = _key(name)
    return {key, key[:-1] if key.endswith("s") else key + "s"}


def article_identity(article):
    """Names that identify a publication: short title, title method names, authors.
    Abstracts can name baselines or prior works; those names do not establish ownership."""
    title = article.title or ""
    short = title.split(":", 1)[0].strip() if ":" in title and len(title.split(":", 1)[0]) <= 40 else ""
    surnames = [a.strip().split()[-1] for a in (article.authors or []) if a.strip()]
    names = list(dict.fromkeys([*([short] if short else []), *METHOD_NAME.findall(title)]))
    return {"article_id": article.id, "short_name": short or None, "first_author": surnames[0] if surnames else None,
        "year": article.year, "method_names": names, "authors": surnames}


def own_card_names(article, evidence, cited_ids):
    """Component names from card claims supported by this sentence's cited proofs."""
    names = set()
    for value in (article.analysis or {}).values():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            for claim in value:
                if ("statement" in claim and set(claim.get("evidence_ids", [])) <= cited_ids
                        and claim.get("evidence_ids") and claim_subject(claim, evidence) == "this_work"):
                    for name in METHOD_NAME.findall(claim["statement"]):
                        names |= _variants(name)
    return names


def sentences(text):
    return [s.strip() for s in SENTENCE.split(text) if s and s.strip()]


def _proof_text(row):
    """Exact passage plus, for a table row, its caption, headers and cells."""
    table = row.get("table") if isinstance(row.get("table"), dict) else {}
    return _key(" ".join([row.get("exact_quote", ""), str(table.get("caption") or ""),
        *map(str, table.get("headers") or []), *map(str, table.get("row") or [])]))


def check_attribution(text, article_ids, toolkit):
    """Issues of a draft text: unknown names without an exact supporting passage in the
    same sentence, and sentences naming one publication but citing only another's evidence."""
    identities = [article_identity(toolkit.registry[aid]) for aid in article_ids if aid in toolkit.registry]
    allowed = set()
    owners = {}
    for identity in identities:
        for name in [*identity["method_names"], *identity["authors"]]:
            allowed |= _variants(name)
        for name in [identity["short_name"], identity["first_author"], *identity["method_names"]]:
            if name:
                for key in _variants(name):
                    owners.setdefault(key, set()).add(identity["article_id"])
    issues = []
    for sentence, paragraph in ((s, p) for p in re.split(r"\n\s*\n", text) for s in sentences(p)):
        cited = [toolkit.evidence[eid] for eid in MARKER.findall(sentence) if eid in toolkit.evidence]
        quotes = " ".join(_proof_text(row) for row in cited)
        names = [*METHOD_NAME.findall(MARKER.sub("", re.sub(r"\[paper_[\w-]+\]", "", sentence))),
            *ET_AL.findall(sentence)]
        cited_ids = set(MARKER.findall(sentence))
        context = {row["article_id"] for row in cited} & set(article_ids)
        here = allowed.union(*(own_card_names(toolkit.registry[aid], toolkit.evidence, cited_ids) for aid in context))
        if not cited_ids:
            # A paragraph-final citation can support a component of the work
            # explicitly named in this sentence. This exception uses only its
            # own card claims and their exact paragraph proofs, never arbitrary
            # names found somewhere else in an article (or background authors).
            named_owners = {aid for name in names for key in _variants(name) for aid in owners.get(key, ())}
            paragraph_ids = set(MARKER.findall(paragraph))
            here |= set().union(*(own_card_names(toolkit.registry[aid], toolkit.evidence,
                {eid for eid in paragraph_ids if eid in toolkit.evidence and toolkit.evidence[eid]["article_id"] == aid})
                for aid in named_owners))
        unknown = [name for name in dict.fromkeys(names)
            if not _variants(name) & here and not any(v and v in quotes for v in _variants(name))]
        if unknown:
            issues.append({"code": "unsupported_name", "sentence": sentence[:300], "names": unknown})
            continue
        cited_articles = {row["article_id"] for row in cited}
        explicit_articles = set(re.findall(r"\[(paper_[\w-]+)\]", sentence))
        missing, missing_names = set(), []
        for name in dict.fromkeys(names):
            candidates = {aid for key in _variants(name) for aid in owners.get(key, ())}
            # One surname can identify several works. Explicit article markers
            # disambiguate it; without them, any cited owner is sufficient.
            candidates = candidates & explicit_articles or candidates
            if candidates and not candidates & cited_articles:
                missing.update(candidates)
                missing_names.append(name)
        if missing and cited_articles:
            issues.append({"code": "evidence_of_other_publication", "sentence": sentence[:300],
                "names": missing_names,
                "article_id": ", ".join(sorted(missing)), "cited_article_ids": sorted(cited_articles)})
    return issues


def attribution_error(issues):
    lines = []
    for issue in issues[:4]:
        if issue["code"] == "unsupported_name":
            lines.append(f"« {issue['sentence'][:180]} » : {', '.join(issue['names'])} n'identifie aucune publication du "
                "brouillon et n'apparaît dans aucun passage cité dans cette phrase")
        else:
            lines.append(f"« {issue['sentence'][:180]} » nomme {', '.join(issue['names'])} ({issue['article_id']}) mais cite "
                f"seulement des preuves de {', '.join(issue['cited_article_ids'])}")
    return ("Attribution non vérifiable : " + " | ".join(lines) + (f" (+{len(issues)-4} autres)" if len(issues) > 4 else "") +
        ". Citer dans la même phrase la preuve exacte [ev_…] qui nomme ce travail (un travail antérieur se présente comme cité "
        "par la publication qui le décrit), attribuer la phrase à la bonne publication, ou retirer le nom. "
        "Corriger seulement ces phrases et réenregistrer.")


def audit_document(state, toolkit):
    """Attribution issues across every saved section (latest drafts), without LLM."""
    issues = []
    for section in state.get("outline", {}).get("sections", []):
        draft = (section.get("drafts") or [None])[-1]
        if draft:
            for issue in check_attribution(draft.get("raw_text") or draft["text"], draft["article_ids"], toolkit):
                issues.append({**issue, "section_id": section["id"], "section_title": section["title"]})
    return issues
