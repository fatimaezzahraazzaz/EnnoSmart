"""Recoverable section context built from active sources, never stale rankings alone."""
import json
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
    return list(dict.fromkeys(aid for aid in ids if aid in registry and registry[aid].selection_override is not False))


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


def writing_context(state, toolkit, section=None, budget=7000):
    from .context import evidence_projection, article_projection
    outline = state.get("outline", {})
    if section is None:
        section = next((s for s in outline.get("sections", [])
            if s["id"] == state.get("document", {}).get("active_section_id")), {})
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
        "guidance": "Contexte borné ; sources, anciennes versions et preuves exactes restent en stockage. Relire passages/tableaux ciblés si nécessaire. Le plan doit être validé par le consultant ; aucune approbation du modèle."}
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
    for article in packet["articles"]:
        from .context import analysis_projection
        aid = article["id"]
        article["analysis"] = analysis_projection(toolkit.registry[aid].analysis, claims=3, statement_chars=700)
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
        for value in article.get("analysis", {}).values():
            if isinstance(value, list):
                for claim in value:
                    if isinstance(claim, dict) and isinstance(claim.get("statement"), str):
                        claim["statement"] = claim["statement"][:300]
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
