"""Conversational references to publications, resolved from the existing records.

No new memory: last_response, response_history and the messages already store what
each answer discussed. This module reads them deterministically (no model call):

- discussed: the publications an answer was about (its primary list, else its citations);
- displayed: a list (table) the answer presented; current only when that answer set it;
- selected / recommended / library: never a conversational referent by themselves.

Run ce08ad31: « Donne-moi le détail des 2 articles » after a LDPoly/HoliTracer comparison
was answered on two other publications from an older displayed list, after fifteen
readings of unrelated works.
"""
from __future__ import annotations

import re

ORDINALS = {"premier": 1, "première": 1, "1er": 1, "1re": 1, "first": 1, "deuxième": 2, "second": 2, "seconde": 2,
    "2e": 2, "2ème": 2, "troisième": 3, "third": 3, "3e": 3, "3ème": 3, "quatrième": 4, "4e": 4, "dernier": -1, "dernière": -1,
    "last": -1}
ORDINAL = re.compile(r"\b(?:le|la|l['’]|du|de\s+la|au|à\s+la|the)\s*(" + "|".join(sorted(map(re.escape, ORDINALS), key=len, reverse=True))
    + r")\b(?:\s+(?:article|publication|papier|travail|étude|etude|méthode|methode|paper|one)\b|(?=\s*(?:[.?!,;:]|$)))", re.I)
# A reference to publications already in the dialogue (plural or singular), not a new topic.
# Demonstratives, possessives and counted references only: « les travaux les plus pertinents
# sur … » is a new (open) request, « ces articles », « leurs limites », « des 2 articles » are not.
PUBLICATION_NOUN = (r"(?:articles?|publications?|papiers?|travaux|travail|études?|etudes?|méthodes?|methodes?|approches?|"
    r"papers?|works?|studies|methods?)")
# Strong references: demonstratives and counted publications (« ces articles », « des 2 articles »).
IMPLICIT = re.compile(r"\b(?:ces|ceux-ci|celles-ci|celui-ci|celle-ci|these|those|both)\b|"
    r"\b(?:les|des|aux|ces)\s+(?:deux|2|trois|3|quatre|4)\s+" + PUBLICATION_NOUN + r"|"
    r"\b(?:les|des|aux|ces)\s+(?:deux|2|trois|3|quatre|4)\s*(?:[.?!,;:]|$)|"
    r"\b(?:cet|cette|ce)\s+(?:article|publication|papier|travail|étude|etude|méthode|methode)\b", re.I)
# Possessives (« leurs limites ») refer to earlier works only when the message introduces
# no publications of its own (« les publications pertinentes … compare leurs méthodes »).
POSSESSIVE = re.compile(r"\b(?:leurs?|ses|their|its)\b", re.I)
OWN_SET = re.compile(r"\b(?:les|des|de|d['’]|aux|du|la|une?|of|the)\s+(?:\w+\s+){0,2}?" + PUBLICATION_NOUN + r"\b", re.I)
EARLIER = re.compile(r"\b(?:revenons|reviens|revenir|retour(?:ne|nons)?|tout\s+à\s+l['’]heure|précédemment|precedemment|"
    r"plus\s+tôt|au\s+début|initialement|go\s+back|earlier)\b", re.I)
COUNT = re.compile(r"\b(?:les|des|aux|ces)\s+(deux|2|trois|3|quatre|4)\b(?=\s+" + PUBLICATION_NOUN + r"|\s*(?:[.?!,;:]|$))", re.I)
NUMBERS = {"deux": 2, "2": 2, "trois": 3, "3": 3, "quatre": 4, "4": 4}
CONSTRAINTS = {
    "no_search": re.compile(r"\b(?:aucune?|sans|pas\s+de|ne\s+(?:fais|lance|effectue)\s+(?:aucune?|pas\s+de))\s+"
        r"(?:nouvelle\s+)?(?:recherche|découverte)|\bne\s+(?:cherche|recherche)\s+pas\b|"
        r"\bsans\s+(?:effectuer|faire|lancer)\s+(?:de\s+)?(?:nouvelle\s+)?recherche\b|no\s+new\s+search", re.I),
    "no_download": re.compile(r"\b(?:aucun|sans|pas\s+de)\s+(?:nouveau\s+)?téléchargement|\bne\s+télécharge\s+pas\b", re.I),
    "no_reanalysis": re.compile(r"\b(?:aucune?|sans|pas\s+de|ni)\s+(?:nouvelle\s+)?(?:ré-?analyse|reanalyse|analyse\s+nouvelle|"
        r"nouvelle\s+analyse)|\buniquement\s+(?:les\s+)?(?:analyses|fiches)\s+(?:et\s+preuves\s+)?déjà|"
        r"\buniquement\s+(?:à\s+partir\s+)?(?:des|de\s+leurs|les)\s+(?:preuves|analyses|fiches)\s+(?:\w+\s+){0,2}?"
        r"(?:déjà\s+)?enregistrées|\ben\s+utilisant\s+(?:uniquement\s+)?(?:leurs|les)\s+(?:analyses|fiches|preuves)\s+"
        r"(?:déjà\s+)?enregistrées|sans\s+relancer\s+(?:de\s+)?(?:nouvelle\s+)?analyse", re.I),
    "no_manuscript_change": re.compile(r"\b(?:aucune|sans|pas\s+de)\s+modification\s+(?:de\s+)?(?:mon|du|l['’]|notre)\s*"
        r"(?:état|etat|manuscrit|document|plan)|\bne\s+modifie\s+(?:pas|rien)\b", re.I),
    "no_drafting": re.compile(r"\bne\s+(?:rédige|redige|rédigez|redigez|écris|ecris)\s+(?:pas|rien)\b|"
        r"\bsans\s+(?:encore\s+)?(?:rédiger|rediger|rédaction|redaction)\b", re.I),
}

# Tools each explicit constraint excludes (before the request, and refused by the toolkit).
FORBIDDEN_TOOLS = {
    "no_search": ("search_scientific", "find_similar_articles", "search_for_section", "identify_research_gaps"),
    "no_download": ("get_fulltext",),
    # Reading stored cards and exact passages is local (get_analysis_claims, analyze_article
    # action=read); only a new scientific analysis (analyze_article action=save) is refused,
    # in the toolkit (within_request_scope), since the same tool serves both.
    "no_reanalysis": (),
    "no_manuscript_change": ("write_section", "manage_outline", "create_scientific_visual"),
    "no_drafting": ("write_section", "create_scientific_visual"),
}


def discussed(entry):
    """Publications an answer was about: its primary list, else what it cited."""
    entry = entry or {}
    return list(dict.fromkeys(entry.get("discussed_article_ids") or entry.get("primary_article_ids")
        or entry.get("cited_article_ids") or []))


def displayed_list_current(state):
    """True when the last answer itself presented the stored displayed list (a table)."""
    last = state.get("last_response") or {}
    if "displayed_list_current" in last:
        return bool(last["displayed_list_current"])
    shown = state.get("displayed_article_ids") or []
    return bool(shown) and list(last.get("primary_article_ids") or []) == list(shown)


def current_order(state):
    """The order « le deuxième » refers to: the last answer's own list when it presented
    one, otherwise the publications it discussed; an older displayed list only last."""
    last = state.get("last_response") or {}
    if displayed_list_current(state):
        return list(state.get("displayed_article_ids") or []), "displayed_list"
    if discussed(last):
        return discussed(last), "last_exchange"
    return list(state.get("displayed_article_ids") or []), "displayed_list"


def named_publications(message, registry):
    """Publications the message names (short title, title prefix, method names of the title)."""
    from .attribution import article_identity
    spaced = lambda value: " ".join(re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).split())
    text = " " + spaced(message) + " "
    names_of = {}
    for aid, article in registry.items():
        identity = article_identity(article)
        names = [identity.get("short_name"), *identity.get("method_names", [])]
        prefix = (article.title or "").split(":", 1)[0].strip()
        if len(prefix) >= 5 and len(prefix) <= 40:
            names.append(prefix)
        names_of[aid] = {spaced(name) for name in names if name and len(spaced(name).replace(" ", "")) >= 4}
    # A name designates a publication only when it is distinctive in this library: « LiDAR »
    # in many titles names a topic, « HoliTracer » names one work (its records count as one).
    works = {}
    for aid, names in names_of.items():
        for name in names:
            works.setdefault(name, set()).add(registry[aid].canonical_work_id or aid)
    found = [aid for aid, names in names_of.items()
        if any(len(works[name]) == 1 and " " + name + " " in text for name in names)]
    # One work under several records: the canonical record first, aliases kept for tracing.
    canonical = {}
    for aid in found:
        key = registry[aid].canonical_work_id or aid
        canonical.setdefault(key, []).append(aid)
    return [ids[0] if registry[ids[0]].canonical_work_id in ("", ids[0]) else
        next((a for a in ids if registry[a].canonical_work_id in ("", a)), ids[0]) for ids in canonical.values()]


def constraints_of(message):
    text = message or ""
    constraints = [name for name, pattern in CONSTRAINTS.items() if pattern.search(text)]
    if (re.search(r"\b(?:plan|manuscrit|document|état\s+de\s+l['’]art)\b", text, re.I) and
            re.search(r"\bsans\s+(?:(?:le|la|les)\s+)?(?:modifier|changer|réécrire|reecrire)\b", text, re.I)):
        constraints = list(dict.fromkeys([*constraints, "no_manuscript_change"]))
    return constraints


def opens_literature_search(message):
    """An explicit request for publications, rather than a reference to old works.

    Reuse the research request/object recognition; do not interpret scientific merit.
    « ces travaux » later in that request denotes the works to discover.
    """
    text = message or ""
    if "no_search" in constraints_of(text):
        return False
    # Lexical fallback only. Discovery can be expressed across clauses, or refer
    # to an unfinished selection instead of repeating the word « articles ».
    publication = re.search(r"\b(?:articles?|publications?|papers?|travaux|littérature|literature)\b", text, re.I)
    action = re.search(r"\b(?:recherch\w*|cherch\w*|trouv\w*|search|find|identify)\b", text, re.I)
    continuation = re.search(r"\b(?:poursui\w*|repren\w*|reprends|continu\w*|compl[ée]t\w*)\b", text, re.I)
    unfinished = re.search(r"\b(?:recherche|s[ée]lection|bibliographie|manquants?|autres)\b", text, re.I)
    question = re.search(r"\b(?:existe|existent|connais|connaissez)\b", text, re.I)
    # A past search mentioned while revising a section is not a new search.
    past = re.search(r"\b(?:d[ée]j[àa]\s+(?:trouv[ée]s?|recherch[ée]s?)|(?:avons|avez|a)\s+(?:trouv[ée]|recherch[ée]))\b", text, re.I)
    return bool((publication and action and (not past or continuation or re.search(r"\bnouvea(?:ux|u|lles?)\b", text, re.I))) or
        (continuation and unfinished and (publication or re.search(r"\brecherche\b|\bmanquants?\b", text, re.I))) or
        (action and re.search(r"\b(?:autres|manquants?)\b", text, re.I)) or
        (question and publication and re.search(r"\bd['’]autres\b|\br[ée]cent", text, re.I)))


def resolve(state, registry, message):
    """References of the current message, in priority order:

    1. publications the message names explicitly (a new work replaces the targets);
    2. an implicit reference (« les deux », « leurs limites », « le deuxième ») resolves to
       the publications discussed in the last answer, in their order;
    3. « revenons au premier … » resolves to an earlier answer;
    4. an implicit reference without a single plausible set asks for a clarification.
    No reference: an open request (exploration stays possible)."""
    from .instructions import current_message_id
    message_id = current_message_id(state)
    result = {"message_id": message_id, "article_ids": [], "source": "none", "resolved_from": None,
        "ambiguous": False, "constraints": constraints_of(message)}
    named = [aid for aid in named_publications(message, registry)]
    if named:
        return {**result, "article_ids": named, "source": "current_message", "resolved_from": message_id}
    # Explicit article numbers address the same conversational list as ordinals.
    # This resolves references only; the model still interprets accept/reject.
    numbered = re.findall(r"\b(?:articles?|publications?|papiers?)\s+(\d+(?:\s*(?:,|et|and|&)\s*"
        r"(?:(?:l['’]|les?\s+)?(?:articles?|publications?|papiers?)\s+)?\d+)*)\b", message or "", re.I)
    if numbered:
        positions = list(dict.fromkeys(int(n) for group in numbered for n in re.findall(r"\d+", group)))
        order, source = current_order(state)
        if not order or any(n < 1 or n > len(order) or order[n-1] not in registry for n in positions):
            return {**result, "ambiguous": True, "source": source, "candidates": order,
                "clarification": "Les numéros demandés ne correspondent pas tous aux publications du dernier échange."}
        ids = [order[n-1] for n in positions]
        return {**result, "article_ids": ids, "source": source, "resolved_from": answer_run(state, ids)}
    if opens_literature_search(message or ""):
        return result
    implicit = (IMPLICIT.search(message or "") or ORDINAL.search(message or "") or EARLIER.search(message or "")
        or (POSSESSIVE.search(message or "") and not OWN_SET.search(message or "")))
    if not implicit:
        return result
    history = [entry for entry in state.get("response_history", []) if discussed(entry)]
    if EARLIER.search(message or ""):
        # Back to an earlier exchange: the answer before the last one that discussed works.
        last = discussed(state.get("last_response"))
        earlier = [entry for entry in history if discussed(entry) != last]
        candidates = discussed(earlier[-1]) if earlier else []
        source = "earlier_exchange"
    else:
        order, source = current_order(state)
        candidates = order if source == "last_exchange" or displayed_list_current(state) else []
        source = "last_exchange" if candidates else source
        if not candidates and not discussed(state.get("last_response")):
            # No answer discussed publications yet: the presented list, else the current finalists.
            candidates = list(state.get("displayed_article_ids") or []) or list(
                (state.get("evaluation") or {}).get("recommended_article_ids") or [])
            source = "displayed_list" if state.get("displayed_article_ids") else "recommendations"
    candidates = [aid for aid in candidates if aid in registry]
    ordinal = ORDINAL.search(message or "")
    count = COUNT.search(message or "")
    if not candidates:
        return {**result, "ambiguous": True, "source": "unresolved",
            "clarification": "La demande renvoie à des publications précédentes, mais aucun échange récent ne les identifie."}
    if ordinal:
        position = ORDINALS[ordinal.group(1).casefold()]
        if position == -1:
            position = len(candidates)
        if not 1 <= position <= len(candidates):
            return {**result, "ambiguous": True, "source": source, "candidates": candidates,
                "clarification": f"Le dernier échange ne comporte que {len(candidates)} publication(s)."}
        candidates = [candidates[position-1]]
    elif count and NUMBERS[count.group(1).casefold()] != len(candidates):
        return {**result, "ambiguous": True, "source": source, "candidates": candidates,
            "clarification": f"La demande vise {NUMBERS[count.group(1).casefold()]} publications ; le dernier échange en "
                f"discute {len(candidates)}."}
    return {**result, "article_ids": candidates, "source": source, "resolved_from": answer_run(state, candidates)}


def answer_run(state, ids):
    """The run of the latest assistant message that discussed these publications."""
    wanted = set(ids)
    for message in reversed(state.get("messages", [])):
        if message.get("role") == "assistant" and wanted & set(message.get("primary_article_ids") or []):
            return message.get("run_id") or message.get("id")
    return "last_response"


def focus_block(state, registry, budget_chars=3000, compact=False):
    """The small protected block every decision, tool and final context carries."""
    from .instructions import current_request
    current = current_request(state) or {}
    current_id = current.get("id")
    goal = state.get("turn_goal") or {}
    refs = goal.get("references") or state.get("conversation_references") or {}
    if refs.get("message_id") and current_id and refs["message_id"] != current_id:
        # References resolved for an earlier message never steer the current one.
        refs = {}
    ids = refs.get("article_ids") or []
    block = {"request": current.get("content", "")[:400],
        "active_publications": [{"order": i+1, "id": aid, "title": registry[aid].title[:110],
            **({"year": registry[aid].year} if registry[aid].year else {})} for i, aid in enumerate(ids) if aid in registry],
        "references_source": refs.get("source", "none"), "resolved_from": refs.get("resolved_from"),
        "scope": refs.get("scope") or ("exclusive" if ids else "open"),
        "constraints": refs.get("constraints") or [],
        "guidance": "Publications visées, dans l'ordre du dialogue ; jamais remplacées par une ancienne liste. "
            "scope=exclusive : aucune autre publication lue sans demande explicite."}
    source_message = next((m for m in reversed(state.get("messages", [])) if m.get("role") == "assistant"
        and refs.get("resolved_from") and m.get("run_id") == refs.get("resolved_from")), None)
    if source_message and refs.get("source") in {"last_exchange", "earlier_exchange"}:
        block["resolved_from_excerpt"] = source_message["content"][:400]
    if goal:
        block["goal"] = {k: goal[k] for k in ("deliverable", "reading_depth") if k in goal}
        block["goal"]["understanding"] = (goal.get("understanding") or "")[:300]
    if refs.get("ambiguous"):
        block["clarification_needed"] = refs.get("clarification") or "Références ambiguës."
        if refs.get("candidates"):
            block["candidates"] = [{"id": aid, "title": registry[aid].title[:90]} for aid in refs["candidates"][:6] if aid in registry]
    if compact:
        # A focused task (section, plan part, comparison, reading) carries its own sources: the
        # block keeps only the targeted ids, scope and constraints.
        block = {"active_publications": [{"order": row["order"], "id": row["id"], "title": row["title"][:60]}
            for row in block["active_publications"]], "scope": block["scope"], "constraints": block["constraints"],
            **({"clarification_needed": block["clarification_needed"]} if block.get("clarification_needed") else {})}
    import json as _json
    while len(_json.dumps(block, ensure_ascii=False)) > budget_chars and len(block["active_publications"]) > 1:
        block["active_publications"].pop()
        block["active_publications_partial"] = True
    return block
