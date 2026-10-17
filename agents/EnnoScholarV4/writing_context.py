"""Recoverable section context built from active sources, never stale rankings alone."""
import json
import re
from .evidence import exact_source_valid


def active_article_ids(state, registry):
    document = state.get("document", {})
    section = next((s for s in state.get("outline", {}).get("sections", [])
        if s["id"] == document.get("active_section_id")), {})
    focused = state.get("focus", {}).get("article_id")
    ids = [*state.get("current_turn_article_ids", []), *([focused] if focused else []),
        *section.get("article_ids", []), *[aid for aid, article in registry.items() if article.selected],
        *state.get("evaluation", {}).get("recommended_article_ids", []),
        *(state.get("displayed_article_ids") or state.get("last_response", {}).get("cited_article_ids", []))]
    # Works put on hold by the consultant wait unless named, focused or in this section.
    held = set(state.get("held_article_ids", [])) - {*state.get("current_turn_article_ids", []), focused,
        *section.get("article_ids", [])}
    return list(dict.fromkeys(aid for aid in ids if aid in registry and aid not in held
        and registry[aid].selection_override is not False))


def section_article_ids(state, registry, section):
    blueprint = state.get("argument_blueprint", {})
    from .argumentation import blueprint_status
    related = []
    if blueprint and not blueprint_status(state, registry)["stale"]:
        for axis in blueprint.get("axes", []):
            if axis.get("section_id") == section.get("id"):
                related.extend(aid for step in axis["steps"] for aid in step["article_ids"])
    ids = [*section.get("article_ids", []), *related]
    if not ids:
        ids = active_article_ids(state, registry)
    return list(dict.fromkeys(aid for aid in ids if aid in registry and registry[aid].selection_override is not False))


# Under a tight window, these card fields are deferred first (retrievable with
# get_analysis_claims); methods, mechanisms, parameters, protocols, results and
# limitations are kept to the end.
DEFERRABLE_FIELDS = ("relevance", "open_questions", "generalization", "hypotheses", "comparisons", "contribution",
    "objective", "problem", "datasets_materials", "tables_figures", "validation_protocol", "metrics",
    "conditions_of_validity", "equations")


def section_terms(section):
    """Content words of a plan section (title, aims, roles) for deterministic ordering."""
    text = " ".join(str(v) for k, v in section.items() if k not in {"id", "drafts", "article_ids", "evidence_ids"}
        and isinstance(v, (str, list)))
    return {w for w in re.findall(r"[a-zà-ÿ0-9]{5,}", text.casefold())}


def rank_claims(claims, terms):
    """Stable lexical order by overlap with the section; no scientific score."""
    if not terms:
        return list(claims)
    return sorted(claims, key=lambda c: -len(terms & set(re.findall(r"[a-zà-ÿ0-9]{5,}", str(c.get("statement", "")).casefold()))))


def section_projection(section, text_chars=1800, compact=False):
    row = {k: v for k, v in section.items() if k != "drafts"}
    if compact:
        row = {k:([v[:180] if isinstance(v,str) else v for v in value[:3]] if isinstance(value,list)
            else value[:500] if isinstance(value,str) else value) for k,value in row.items()}
        row["article_ids"] = section.get("article_ids", [])[:12]
        row["article_roles"] = [{"article_id":r["article_id"],"role":r["role"][:180]} for r in section.get("article_roles", [])[:4]]
    drafts = section.get("drafts", [])
    row["draft_count"] = len(drafts)
    if drafts:
        row["latest_draft"] = {**{k: v for k, v in drafts[-1].items() if k not in {"text", "raw_text", "paragraphs", "claims", "numeric_claims", "calculations"}},
            "paragraph_index":[{"id":p["id"],"preview":p["text"][:100]} for p in drafts[-1].get("paragraphs",[])[:20]],
            "text": drafts[-1]["text"][:text_chars], "text_truncated": len(drafts[-1]["text"]) > text_chars}
    return row


def revision_context(state, toolkit, section, budget):
    """An existing section is the primary input of a revision, never a preview.
    Keep every source's identity and one supported core claim before optional detail."""
    from .context import analysis_projection, evidence_projection, ActiveReadingContextError
    from .argumentation import proof_reference
    from .attribution import article_identity, check_attribution, claim_subject, _variants, _proof_text
    ids = section_article_ids(state, toolkit.registry, section)
    draft = section["drafts"][-1]
    raw = draft.get("raw_text", draft["text"])
    issues = check_attribution(raw, draft["article_ids"], toolkit)
    number_input = lambda fact: {k: fact[k] for k in ("evidence_id", "column", "value", "unit", "metric")}
    packet = {"section": {"id": section["id"], "title": section["title"], "article_ids": ids},
        "revision": {"draft_id": draft["id"], "raw_text": raw, "text_truncated": False,
            "article_ids": draft["article_ids"], "evidence_ids": draft["evidence_ids"],
            "numeric_claims": [number_input(f) for f in draft.get("numeric_claims", [])],
            "calculations": [{**{k: c[k] for k in ("operation", "result", "rounding_digits", "unit")},
                "left": number_input(c["left"]), "right": number_input(c["right"])} for c in draft.get("calculations", [])],
            "attribution_issues": issues},
        "identities": [article_identity(toolkit.registry[aid]) for aid in ids],
        "articles": [], "evidence": [], "table_evidence_index": [], "source_mentions": [],
        "evidence_owners": {eid: toolkit.evidence[eid]["article_id"] for eid in draft["evidence_ids"] if eid in toolkit.evidence},
        "guidance": "Réviser revision.raw_text en entier, puis write_section(save). Conserver les informations étayées, "
            "corriger les attributions avec les fiches et preuves fournies. Les alertes ci-dessus concernent cette section seule. "
            "Une mention dans une preuve ne démontre pas que ce travail est la contribution de l'article. "
            "Ne pas relire toutes les fiches : récupérer seulement une preuve manquante par son evidence_id."}
    primary = []
    for aid in ids:
        article = toolkit.registry[aid]
        view = analysis_projection(article.analysis, evidence=toolkit.evidence)
        candidates = [(field, claim) for field in ("technical_mechanism", "method", "contribution")
            for claim in view.get(field, []) if claim_subject(claim, toolkit.evidence) == "this_work"
            and all(eid in toolkit.shown_evidence and eid in toolkit.evidence
                and toolkit.evidence[eid]["article_id"] == aid
                and exact_source_valid(toolkit.evidence[eid], toolkit.snapshots) for eid in claim["evidence_ids"])]
        # Prefer a complete claim requiring fewer separate passages; never relabel
        # a related-work claim as the article's own method to save space.
        chosen = min(candidates, key=lambda row: len(row[1]["evidence_ids"]), default=None)
        packet["articles"].append({"id": aid, "title": article.title,
            "analysis": {chosen[0]: [chosen[1]]} if chosen else {},
            "claims_available": sum(len(v) for v in view.values() if isinstance(v, list))})
        if chosen:
            primary.extend(chosen[1]["evidence_ids"])
    proofs = {eid: row for eid, row in toolkit.evidence.items() if row["article_id"] in ids
        and eid in toolkit.shown_evidence and exact_source_valid(row, toolkit.snapshots)}
    for name in dict.fromkeys(n for issue in issues for n in issue["names"]):
        for eid, row in proofs.items():
            if any(key and key in _proof_text(row) for key in _variants(name)):
                packet["source_mentions"].append({"name": name, "subject": claim_subject({"evidence_ids": [eid]}, toolkit.evidence),
                    "proof": proof_reference(row)})
                break
    for eid in draft["evidence_ids"]:
        if eid in proofs and isinstance(proofs[eid].get("table"), dict):
            packet["table_evidence_index"].append({"evidence_id": eid, "article_id": proofs[eid]["article_id"],
                "table": {k: proofs[eid]["table"].get(k) for k in ("number", "caption", "row", "headers")}})
    blueprint = state.get("argument_blueprint", {})
    if blueprint:
        packet["argument_blueprint"] = {"id": blueprint.get("id"), "axes": [
            {"axis": a["axis"], "steps": a["steps"][:1]} for a in blueprint.get("axes", [])[:1]]}
    size = lambda: len(json.dumps(packet, ensure_ascii=False))
    for optional in ("argument_blueprint", "table_evidence_index"):
        if size() > budget-4500:
            packet[optional] = {} if optional == "argument_blueprint" else []
    if size() > budget:
        raise ActiveReadingContextError("Le texte intégral de la section et ses identités dépassent le contexte de révision disponible.")
    # Whole exact passages, not substrings. Reverse order balances the earlier
    # works that the ordinary card packet used to favour at the expense of later ones.
    extra = [row["proof"]["evidence_id"] for row in packet.get("source_mentions", [])]
    for eid in dict.fromkeys([*reversed(primary), *extra, *draft["evidence_ids"]]):
        if eid not in proofs:
            continue
        # These passages have already been delivered and validated. Repeat their
        # exact text and locator, without duplicating the stored version metadata.
        packet["evidence"].append({k: proofs[eid][k] for k in (
            "evidence_id", "article_id", "exact_quote", "section", "page_number", "basis", "table") if k in proofs[eid]})
        if size() > budget:
            packet["evidence"].pop()
    packet["transmission_partial"] = not set(primary) <= {e["evidence_id"] for e in packet["evidence"]}
    return packet


def writing_context(state, toolkit, section=None, budget=7000):
    from .context import evidence_projection, article_projection
    outline = state.get("outline", {})
    if section is None:
        section = next((s for s in outline.get("sections", [])
            if s["id"] == state.get("document", {}).get("active_section_id")), {})
    progress = state.get("document", {}).get("writing_progress", {})
    if progress.get("mode") == "section" and section.get("id") in progress.get("target_ids", []) and section.get("drafts"):
        return revision_context(state, toolkit, section, budget)
    ids = section_article_ids(state, toolkit.registry, section)
    packet = {"title": outline.get("title"), "plan_revision": outline.get("plan_revision"),
        "plan_status": outline.get("status", "proposed"), "section": section_projection(section,800,compact=True),
        "preceding_argument": [{"id": s["id"], "title": s["title"],
            "established_text": (s.get("drafts") or [{}])[-1].get("text", "")[:500],
            "article_ids": s["article_ids"]} for s in outline.get("sections", [])[:next(
                (i for i,s in enumerate(outline.get("sections", [])) if s.get("id")==section.get("id")),0)]][-6:],
        "articles": [article_projection(toolkit.registry[aid], 400, summary=True) for aid in ids if aid in toolkit.registry],
        "relations": [{k:v for k,v in r.items() if k != "experimental_results"}
            for r in state.get("scientific_relations", []) if set(r["article_ids"]) <= set(ids)],
        "figures": [{k:f[k] for k in ("id","kind","title","section_id","evidence_ids")} for f in state.get("scientific_figures",[]) if f["section_id"]==section.get("id")],
        "evidence": [], "evidence_available": 0,
        "guidance": "Contexte borné ; sources, anciennes versions et preuves exactes restent en stockage. Relire passages/tableaux ciblés si nécessaire. "
            "analysis_available indique toutes les affirmations sauvegardées de chaque fiche (paramètres, équations, protocoles, résultats, limites) : "
            "get_article(ID) transmet la fiche complète et analyze_article(read, evidence_ids) ses preuves exactes, sans nouvelle recherche. "
            "Le plan doit être validé par le consultant ; aucune approbation du modèle."}
    # Canonical identity of each publication (title, first author, own method
    # names): the draft is checked against it before saving.
    from .attribution import article_identity
    packet["identities"] = [{k: v for k, v in article_identity(toolkit.registry[aid]).items() if v and k != "authors"}
        for aid in ids if aid in toolkit.registry]
    from .argumentation import blueprint_status
    blueprint = state.get("argument_blueprint", {})
    status = blueprint_status(state, toolkit.registry)
    if blueprint and not status["stale"]:
        packet["argument_blueprint"] = {"id": blueprint["id"], "version": blueprint["version"],
            "axes": [{"axis": axis["axis"], "scientific_problem": axis["scientific_problem"],
                "transition": axis["transition"], "steps": [{**step, "statement": step["statement"][:600]}
                    for step in axis["steps"][:12]]} for axis in blueprint["axes"] if
                axis.get("section_id") == section.get("id") or
                (not axis.get("section_id") and set(aid for step in axis["steps"] for aid in step["article_ids"]) & set(ids))][:3]}
        fact_ids = {fid for axis in packet["argument_blueprint"]["axes"] for step in axis["steps"]
            for fid in step["project_fact_ids"]}
        packet["project_facts"] = [fact for fact in state.get("project_facts", []) if fact["id"] in fact_ids]
    # Keep multiple claims per field when the section needs them; the durable
    # card remains complete even if this particular reading window is bounded.
    terms = section_terms(section)
    for article in packet["articles"]:
        from .context import analysis_projection
        aid = article["id"]
        # The claims closest to this section come first; whole statements, so
        # numbers, units and conditions are never cut. The others stay retrievable.
        article["analysis"] = {field: (rank_claims(value, terms)[:3] if isinstance(value, list) and value and isinstance(value[0], dict) else value)
            for field, value in analysis_projection(toolkit.registry[aid].analysis, evidence=toolkit.evidence).items()}
        article["analysis_available"] = {k: len(v) for k,v in toolkit.registry[aid].analysis.items()
            if isinstance(v,list) and v and k not in {"evidence","method_families","analysis_limits"}}
    for key in ("preceding_argument","figures","relations"):
        while len(json.dumps(packet,ensure_ascii=False))>budget*.7 and packet[key]:
            packet[key].pop()
    if len(json.dumps(packet,ensure_ascii=False))>budget*.7:
        packet["section"] = {"id":section.get("id"),"title":section.get("title"),"article_ids":ids}
    # Reserve room for exact proof passages before reducing scientific cards.
    # A long analysis must not crowd every source quotation out of the window.
    target = max(0, budget-1600)
    for claim_cap in (2, 1):
        if len(json.dumps(packet,ensure_ascii=False)) <= target:
            break
        for article in packet["articles"]:
            for value in article.get("analysis", {}).values():
                if isinstance(value, list) and len(value) > claim_cap:
                    del value[claim_cap:]
    for article in packet["articles"]:
        if len(json.dumps(packet,ensure_ascii=False)) <= target:
            break
        article["abstract"] = article.get("abstract", "")[:180]
    for article in reversed(packet["articles"]):
        # Fewer whole claims rather than shortened ones: drop the least related
        # fields of the last works first; analysis_available still counts them.
        for field in DEFERRABLE_FIELDS:
            if len(json.dumps(packet,ensure_ascii=False)) <= target:
                break
            if article.get("analysis", {}).pop(field, None) is not None:
                article.setdefault("deferred_fields", []).append(field)
    while len(json.dumps(packet,ensure_ascii=False)) > target and len(packet["articles"]) > 1:
        packet["articles"].pop()
    pinned = set(section.get("evidence_ids", []))
    pinned.update(e for d in section.get("drafts", [])[-1:] for e in d.get("evidence_ids", []))
    proofs = [toolkit.evidence[e] for e in reversed(toolkit.shown_evidence) if e in toolkit.evidence
        and toolkit.evidence[e]["article_id"] in ids and exact_source_valid(toolkit.evidence[e], toolkit.snapshots)]
    proofs.sort(key=lambda e: (e["evidence_id"] not in pinned, not bool(e.get("table"))))
    packet["table_evidence_index"] = [{"evidence_id": proof["evidence_id"],
        "article_id": proof["article_id"], "table": {key: proof["table"].get(key)
            for key in ("number", "caption", "row", "headers") if proof["table"].get(key)}}
        for proof in proofs if isinstance(proof.get("table"), dict)][:12]
    groups = {aid: [e for e in proofs if e["article_id"]==aid] for aid in ids}
    packet["evidence_available"] = len(proofs)
    for i in range(max((len(g) for g in groups.values()), default=0)):
        for group in groups.values():
            if i >= len(group):
                continue
            projected = evidence_projection(group[i])
            packet["evidence"].append(projected)
            if len(json.dumps(packet,ensure_ascii=False))>budget:
                packet["evidence"].pop()
    # Trim context, never the durable manuscript or exact source quotation.
    for key in ("preceding_argument", "relations", "figures"):
        while len(json.dumps(packet,ensure_ascii=False))>budget and packet[key]:
            packet[key].pop()
    while len(json.dumps(packet,ensure_ascii=False))>budget and packet["evidence"]:
        packet["evidence"].pop()
    while len(json.dumps(packet,ensure_ascii=False))>budget and len(packet["articles"])>1:
        packet["articles"].pop()
    if len(json.dumps(packet,ensure_ascii=False))>budget and packet.get("argument_blueprint"):
        packet["argument_blueprint"] = {"id": packet["argument_blueprint"]["id"],
            "version": packet["argument_blueprint"]["version"],
            "axes": [{"axis": axis["axis"], "step_ids": [step["evidence_ids"] for step in axis["steps"]]}
                for axis in packet["argument_blueprint"]["axes"]]}
    if len(json.dumps(packet,ensure_ascii=False))>budget:
        packet["section"] = {"id":section.get("id"),"title":section.get("title"),"article_ids":ids}
    packet["transmission_partial"] = len(packet["evidence"]) < len(proofs)
    return packet
