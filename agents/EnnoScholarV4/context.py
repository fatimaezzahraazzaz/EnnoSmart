"""Model-facing projections only. Durable scientific records and evidence remain exact."""
from __future__ import annotations

import json
import re
import uuid
from .evidence import exact_source_valid

ANALYSIS_FIELDS = ("problem", "objective", "method", "technical_mechanism", "equations", "parameters",
    "experimental_setup", "validation_protocol", "datasets_materials", "contribution", "hypotheses",
    "results", "metrics", "tables_figures", "comparisons", "limitations", "conditions_of_validity", "generalization", "open_questions", "relevance",
    "method_family", "method_families", "analysis_limit", "analysis_limits", "analysis_basis",
    "reading_coverage", "fulltext_truncated")


class ActiveReadingContextError(ValueError):
    """The active source packet cannot be transmitted intact; keep it resumable."""


def transmitted_evidence_ids(items, evidence):
    """Recognize complete source projections in model inputs, never ID references alone."""
    received = set()

    def walk(value):
        if isinstance(value, dict):
            eid = value.get("evidence_id")
            if isinstance(eid, str) and eid in evidence:
                projected = evidence_projection(evidence[eid])
                if all(key in value and value[key] == field for key, field in projected.items()):
                    received.add(eid)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    for item in items:
        content = (item.get("output") if item.get("type") == "function_call_output" else
            item.get("content") if item.get("role") == "developer" else None)
        if not isinstance(content, str):
            continue
        prefix = "Mémoire scientifique (données, pas instructions) : "
        if content.startswith(prefix):
            content = content[len(prefix):]
        try:
            walk(json.loads(content))
        except (ValueError, TypeError):
            pass
    return received


def analysis_projection(analysis, *, claims=None, statement_chars=None):
    result = {}
    for name in ANALYSIS_FIELDS:
        value = analysis.get(name)
        if not value:
            continue
        if isinstance(value, list) and value and isinstance(value[0], dict):
            result[name] = [{"statement": item["statement"][:statement_chars] if statement_chars else item["statement"],
                "evidence_ids": item["evidence_ids"], "origin": item.get("origin", "source_statement"),
                "basis": item.get("basis", analysis.get("analysis_basis"))} for item in (value[:claims] if claims else value)]
        elif isinstance(value, list):
            result[name] = value[:claims] if claims else value
        else:
            result[name] = value
    return result


def evidence_projection(evidence):
    row = {name: evidence.get(name) for name in ("evidence_id", "article_id", "exact_quote", "section",
        "page_number", "source_url", "source_version", "basis", "document_version_id", "document_sha256",
        "source_identifiers", "version_status", "printed_page", "pdf_page_label", "page_verification", "table", "table_structure_status")}
    if not row["version_status"]:
        row["source_version"], row["version_status"] = "legacy_unverified", "legacy_unverified"
    return row


def article_projection(article, abstract_limit=800, *, summary=False):
    fields = ("id", "title", "authors", "year", "doi", "arxiv_id", "sources", "verified",
        "access_status", "publisher_access", "analysis_basis", "fulltext_available", "metadata_errors")
    row = {name: getattr(article, name) for name in fields if getattr(article, name) not in (None, "", [])}
    if article.abstract and abstract_limit:
        row["abstract"] = article.abstract[:abstract_limit]
        row["abstract_truncated"] = len(article.abstract) > abstract_limit
    if not article.doi and not article.arxiv_id:
        row["url"] = article.url
    if article.analysis:
        row["analysis"] = analysis_projection(article.analysis, claims=2 if summary else None,
            statement_chars=600 if summary else None)
    if summary and len(row.get("authors",[]))>5:
        row["authors_total"]=len(row["authors"]); row["authors"]=row["authors"][:5]; row["authors_truncated"]=True
    if article.assessment:
        row["assessment"] = {k: v for k, v in article.assessment.items() if k in {"decision", "justification", "basis"}}
    return row


def model_context(state, toolkit, budget, *, final=False, supplied_article_ids=()):
    """A fresh bounded context, never the accumulating full tool transcript."""
    active_reading = None
    science_packet = None
    order = state.get("displayed_article_ids") or state.get("last_response", {}).get("cited_article_ids", [])
    from .writing_context import active_article_ids, section_projection, writing_context
    selected = active_article_ids(state, toolkit.registry)
    outline=state.get("outline",{})
    section_index=[{"id":s["id"],"title":s["title"][:160],"article_ids":s["article_ids"][:8]} for s in outline.get("sections",[])[:12]]
    memory = {"topic": state.get("research_state", {}), "summary": state.get("summary", ""),
        "focus": state.get("focus", {}), "outline": {**{k:outline[k] for k in ("title","version","plan_revision","status","approval") if k in outline},
            "sections":section_index,"sections_total":len(outline.get("sections",[])),"index_partial":len(outline.get("sections",[]))>12},
        "displayed_article_order": [{"number": i+1, "id": aid, "title": toolkit.registry[aid].title}
            for i, aid in enumerate(order) if aid in toolkit.registry],
        "corpus_total": len(toolkit.registry), "selected_articles": [article_projection(toolkit.registry[aid],
            500 if final else 160, summary=True) for aid in selected if aid in toolkit.registry],
        "recent_searches": [{k: v for k, v in s.items() if k in {"query", "family", "warnings"}}
            for s in state.get("recent_searches", [])[-8:]],
        "discovered_url_count": len(state.get("web_sources", [])),
        "guidance": "Les notices complètes, anciens messages et passages exacts restent dans les outils. Une URL découverte n'est pas un texte lu."}
    if final:
        # Keep a compact bibliography and dedicate the synthesis room to the
        # already-saved scientific claims, relations and their exact proofs.
        memory["selected_articles"] = [{k:v for k,v in row.items() if k in
            {"id","title","year","doi","arxiv_id","abstract","abstract_truncated",
             "access_status","analysis_basis","verified","assessment"}} for row in memory["selected_articles"]]
        from .argumentation import argument_packet, bounded_argument_packet
        analyzed = [aid for aid in selected if aid in toolkit.registry and toolkit.registry[aid].analysis]
        if analyzed:
            science_packet = argument_packet(state, toolkit, analyzed)
            memory["conversation_science"] = bounded_argument_packet(science_packet, int(budget*.78))
            memory["conversation_science"]["guidance"] = (
                "Ces fiches et relations sont des résultats déjà enregistrés. Expliquer réellement "
                "les méthodes, mécanismes, protocoles, résultats et limites étayés ; distinguer "
                "les chiffres vérifiés des détails inconnus. Ne pas déduire de supériorité "
                "générale lorsque les protocoles diffèrent.")
        memory["selected_articles_total"]=len(memory["selected_articles"])
        while len(json.dumps(memory["selected_articles"],ensure_ascii=False))>budget*.25 and len(memory["selected_articles"])>1:
            memory["selected_articles"].pop()
    if state.get("scientific_scope"):
        from .scientific_scope import temporal_scope
        memory["scientific_scope"] = temporal_scope(state)
        memory["scientific_scope"]={k:([str(x)[:200] for x in v[:5]] if isinstance(v,list) else v[:750] if isinstance(v,str) else v) for k,v in memory["scientific_scope"].items()}
        review=state.get("literature_coverage_review", {})
        memory["coverage_review"]={k:([str(x)[:250] for x in v[:5]] if isinstance(v,list) else v) for k,v in review.items() if k in {"sufficient","missing_axes","uncertainties","origin"}}
    if state.get("document", {}).get("active_section_id") and not state.get("automatic_reading_focus"):
        memory["writing_context"] = writing_context(state, toolkit, budget=min(6500, budget//3))
    progress = state.get("document", {}).get("writing_progress")
    if progress:
        memory["writing_progress"] = {k: progress.get(k) for k in
            ("id", "mode", "target_ids", "saved_draft_ids", "status", "approval_required", "errors")}
    if not final and state.get("writing_focus"):
        from .tools.outline import resolve_section
        section = resolve_section(state, state["writing_focus"]["section_id"])
        memory["section_drafting"] = {"focus": state["writing_focus"],
            "packet": writing_context(state, toolkit, section, budget=min(14000, int(budget*.72)))}
        memory.pop("writing_context", None)
    if not final and state.get("automatic_reading_focus"):
        from .scientific_reading import reading_context
        active_reading = reading_context(state, toolkit)
        memory["automatic_scientific_reading"] = active_reading
    from .argumentation import blueprint_status
    memory["argument_blueprint_status"] = blueprint_status(state, toolkit.registry)
    if state.get("project_facts"):
        memory["project_facts"] = [{k: fact.get(k) for k in ("id", "category", "statement", "exact_quote", "source_type", "source_id")}
            for fact in state["project_facts"][-12:]]
    if state.get("argument_blueprint") and not state.get("argumentation_focus"):
        blueprint = state["argument_blueprint"]
        memory["argument_blueprint"] = {"id": blueprint["id"], "version": blueprint["version"],
            "stale": memory["argument_blueprint_status"]["stale"],
            "axes": [{"axis": axis["axis"], "scientific_problem": axis["scientific_problem"][:300],
                "steps": [{"kind": step["kind"], "statement": step["statement"][:280],
                    "article_ids": step["article_ids"], "evidence_ids": step["evidence_ids"],
                    "relation_ids": step["relation_ids"], "project_fact_ids": step["project_fact_ids"]}
                    for step in axis["steps"][:5]]} for axis in blueprint["axes"][:6]]}
    if not final and state.get("argumentation_focus"):
        from .argumentation import argument_packet, bounded_argument_packet
        focus = state["argumentation_focus"]
        memory["argumentation_focus"] = {**focus,
            "packet": bounded_argument_packet(argument_packet(state, toolkit, focus.get("article_ids"), claim_offsets=focus.get("claim_offsets")),
                int(budget*.72))}
        memory["user_message_sources"] = [{"id": m["id"], "content": m["content"][:3000]}
            for m in state.get("messages", [])[-8:] if m.get("role") == "user" and m.get("id")]
    if state.get("scientific_reading"):
        reading = state["scientific_reading"]
        memory["scientific_reading_status"] = {"tasks": [{k: task.get(k) for k in
            ("article_id", "axes", "status", "basis", "coverage_sufficient", "remaining_passages", "exhausted", "field_status")} for task in reading.get("tasks", [])],
            "remaining_article_ids": reading.get("remaining_article_ids", []), "blocked_reason": reading.get("blocked_reason"),
            "coverage_by_axis": reading.get("coverage_by_axis", {})}
        if final:
            # The synthesis room goes to saved claims; per-axis lists repeat the
            # task rows and stay in the durable ledger.
            memory["scientific_reading_status"].pop("coverage_by_axis", None)
            for task in memory["scientific_reading_status"]["tasks"]:
                statuses = task.pop("field_status", {})
                task.pop("axes", None)
                task["undocumented_fields"] = [f for f, status in (statuses or {}).items() if status != "documented"]
    if not final and state.get("selection_focus"):
        # Selection checkpoint: the presented candidates with their abstracts, so
        # the model judges them from the notices themselves, not from titles.
        focus = state["selection_focus"]
        memory["selection_candidates"] = [{k: v for k, v in article_projection(toolkit.registry[aid], 500, summary=True).items()
            if k != "analysis"} for aid in focus["candidate_ids"] if aid in toolkit.registry]
        memory["selection_candidates_total"] = focus.get("presented_total", len(memory["selection_candidates"]))
    if not final and state.get("reading_recovery_focus"):
        from .scientific_reading import recovery_context
        memory["scientific_reading_recovery"] = recovery_context(state, toolkit.registry)
    if toolkit.registry and not final:
        # An index across the corpus lets GPT target known titles beyond the first
        # reading window. Notices of this message, then unexamined ones, come first
        # so a long session cannot hide new candidates; no scientific scoring.
        examined = set(toolkit.store.examined)
        turn = getattr(toolkit, "turn_article_ids", [])
        order = list(dict.fromkeys([*(aid for aid in turn if aid not in examined),
            *(aid for aid in toolkit.registry if aid not in examined), *toolkit.registry]))
        memory["candidate_index"] = [{"id": a.id, "title": a.title[:180], "abstract_available": bool(a.abstract),
            **({"examined": True} if a.id in examined else {})}
            for a in (toolkit.registry[aid] for aid in order[:100]) if a.id not in selected and a.selection_override is not False]
        memory["candidate_index_total"] = len(toolkit.registry)
    if not selected and toolkit.registry and (final or not supplied_article_ids):
        ids = list(dict.fromkeys([*toolkit.store.examined, *toolkit.store.exposed,
            *[aid for w in toolkit.store.windows if w["status"] == "queued" for aid in w["article_ids"]]]))
        if not ids:
            ids = list(toolkit.registry)
        memory["candidate_articles"] = [article_projection(toolkit.registry[aid], 1600, summary=True)
            for aid in ids if aid in toolkit.registry and toolkit.registry[aid].selection_override is not False][:8]
        memory["candidate_guidance"] = "Candidats documentés, pas finalistes. Évalue leurs abstracts et parcours les autres fenêtres avant de sélectionner ; aucune lecture intégrale n'est supposée."
    if memory.get("automatic_scientific_reading"):
        memory["selected_articles"] = [{k: row[k] for k in ("id", "title", "doi", "arxiv_id") if k in row}
            for row in memory["selected_articles"]]
        memory.pop("candidate_index", None)
        memory.pop("candidate_articles", None)
    if memory.get("argumentation_focus"):
        memory["selected_articles"] = [{k: row[k] for k in ("id", "title", "doi", "arxiv_id") if k in row}
            for row in memory["selected_articles"]]
        memory.pop("candidate_index", None)
        memory.pop("candidate_articles", None)
    if memory.get("section_drafting"):
        memory["selected_articles"] = [{k: row[k] for k in ("id", "title", "doi", "arxiv_id") if k in row}
            for row in memory["selected_articles"]]
        memory.pop("candidate_index", None)
        memory.pop("candidate_articles", None)
    if (not final and not any(memory.get(key) for key in
            ("automatic_scientific_reading", "argumentation_focus", "section_drafting"))):
        analyzed = [aid for aid in selected if aid in toolkit.registry and toolkit.registry[aid].analysis]
        if analyzed:
            from .argumentation import argument_packet, bounded_argument_packet
            science_packet = argument_packet(state, toolkit, analyzed)
            memory["conversation_science"] = bounded_argument_packet(science_packet, int(budget*.65))
            for row in memory["selected_articles"]:
                row.pop("analysis", None)
            memory["conversation_guidance"] = (
                "Les fiches et relations enregistrées servent déjà à la réponse du chat. "
                "Développe leurs méthodes, mécanismes, expériences, résultats et limites vérifiés "
                "avant de finaliser ; le plan proposé reste dans la conversation.")
    if final:
        memory["finalization"] = "Réponds à la question scientifique avec la profondeur permise par les analyses, comparaisons et preuves enregistrées. Explique les mécanismes, protocoles, résultats chiffrés et limites réellement étayés. Aucune recherche supplémentaire. Utilise les marqueurs paper_/ev_ pour les liens. Le plan se discute dans le chat ; seuls les brouillons sauvegardés constituent le manuscrit."
        memory["validation_failures"] = [{k: a.get(k) for k in ("rejection_code", "error", "invalid_article_ids")}
            for a in state.get("finalization_attempts", [])[-3:] if a.get("error")]
        by_article = {aid: [] for aid in selected}
        for aid in selected:
            if aid not in toolkit.registry:
                continue
            for e in toolkit.registry[aid].analysis.get("evidence", [])[:4]:
                if e["evidence_id"] in toolkit.shown_evidence and exact_source_valid(e, toolkit.snapshots):
                    by_article[aid].append(evidence_projection(e))
        # Also keep passages actually read but not yet incorporated into a saved analysis.
        for eid in sorted(reversed(toolkit.shown_evidence), key=lambda eid: not bool(toolkit.evidence.get(eid, {}).get("table"))):
            e = toolkit.evidence.get(eid)
            if e and e["article_id"] in selected and exact_source_valid(e, toolkit.snapshots) and not any(r["evidence_id"] == eid for r in by_article[e["article_id"]]):
                by_article[e["article_id"]].append(evidence_projection(e))
        memory["source_material"] = [{"article_id": aid, "structured_analysis_saved": bool(toolkit.registry[aid].analysis),
            "abstract_passages_shown": sum(e["basis"] == "abstract" for e in rows),
            "body_passages_shown": sum(e["basis"] == "fulltext" for e in rows),
            "sections_shown": list(dict.fromkeys(e["section"] for e in rows if e.get("section")))}
            for aid, rows in by_article.items() if aid in toolkit.registry]
        # Balance already-read proofs across the selected works instead of
        # letting one large article consume the entire synthesis context.
        useful = [rows[index] for index in range(max((len(rows) for rows in by_article.values()), default=0))
            for rows in by_article.values() if index < len(rows)]
        essential = []
        # Tables and quantified passages first, whatever the domain's metric names.
        for e in sorted(useful,key=lambda e:(not bool(e.get("table")), not re.search(r"\d", e.get("exact_quote","")))):
            essential.append(e)
            if len(json.dumps(essential,ensure_ascii=False))>min(6500,budget*.28):
                essential.pop()
        memory["source_guidance"] = "L'absence d'analyse structurée sauvegardée ne signifie pas absence de passages consultés. Les preuves du corps permettent une analyse ciblée, jamais une lecture exhaustive. Si tu les utilises, cite leurs evidence_ids et sauvegarde ton analyse dans finalize_response(analyses)."
        memory["evidence"] = []
        for e in useful:
            memory["evidence"].append(e)
            if len(json.dumps(memory, ensure_ascii=False)) > budget * .8:
                memory["evidence"].pop()
                break
        if science_packet is not None:
            saved_ids = {e["evidence_id"] for paper in science_packet["papers"] for e in paper["proofs"]}
            memory["evidence"] = [e for e in memory["evidence"] if e["evidence_id"] not in saved_ids]
            essential = [e for e in essential if e["evidence_id"] not in saved_ids]
            saved_articles = {p["id"] for p in science_packet["papers"]}
            memory["source_material"] = [row for row in memory["source_material"] if row["article_id"] not in saved_articles]
            memory["source_guidance"] = "Les preuves des fiches structurées sont transmises dans conversation_science ; les autres passages restent récupérables dans les outils."
    # Always retain the active question before fitting optional scientific memory.
    latest = state.get("messages", [])[-1:]
    messages = [{"role": m["role"], "content": m["content"]} for m in latest]
    for message in reversed(state.get("messages", [])[-toolkit.config.recent_messages:-1]):
        text = message["content"]
        if message["role"] != "user":
            text = text[:min(toolkit.config.max_message_context_chars, 1800 if final else 3000)]
        messages.insert(0, {"role": message["role"], "content": text})
        if len(json.dumps(memory, ensure_ascii=False)) + sum(len(m["content"]) for m in messages) > budget:
            messages.pop(0)
            break
    # Remove optional historical data first. Scientific records are never changed.
    while len(json.dumps(memory, ensure_ascii=False)) + sum(len(m["content"]) for m in messages) > budget and memory["recent_searches"]:
        memory["recent_searches"].pop(0)
    while len(json.dumps(memory, ensure_ascii=False)) + sum(len(m["content"]) for m in messages) > budget and memory.get("evidence"):
        memory["evidence"].pop()
    if not final:
        # On recovery of an existing corpus, preserve the title index across the
        # corpus before spending memory on repeated abstract previews.
        for row in memory.get("candidate_articles", []):
            if len(row.get("abstract", "")) > 400:
                row["abstract"] = row["abstract"][:400]
                row["abstract_truncated"] = True
        while len(json.dumps(memory, ensure_ascii=False)) + sum(len(m["content"]) for m in messages) > budget and len(memory.get("candidate_articles", [])) > 2:
            memory["candidate_articles"].pop()
    while len(json.dumps(memory, ensure_ascii=False)) + sum(len(m["content"]) for m in messages) > budget and memory.get("candidate_index"):
        memory["candidate_index"].pop()
    while len(json.dumps(memory, ensure_ascii=False)) + sum(len(m["content"]) for m in messages) > budget and len(memory.get("selection_candidates", [])) > 6:
        memory["selection_candidates"].pop()
    if len(json.dumps(memory, ensure_ascii=False)) + sum(len(m["content"]) for m in messages) > budget:
        memory["outline"] = {"title": state.get("outline", {}).get("title"), "sections": [
            {k: s[k] for k in ("id", "title")} for s in state.get("outline", {}).get("sections", [])]}
        for row in [*memory["selected_articles"], *memory.get("candidate_articles", [])]:
            if row.get("abstract"):
                row["abstract"] = row["abstract"][:600]
                row["abstract_truncated"] = True
            for value in row.get("analysis", {}).values():
                if isinstance(value, list):
                    for claim in value:
                        claim["statement"] = claim["statement"][:250 if final else 160]
    if len(json.dumps(memory, ensure_ascii=False)) + sum(len(m["content"]) for m in messages) > budget:
        # An exceptional oversized object is referenced, not silently reintroduced.
        memory = {"topic": state.get("research_state", {}).get("topic", "")[:1000],
            "selected_articles": [{k: row[k] for k in ("id", "title", "doi", "arxiv_id", "abstract", "abstract_truncated", "access_status", "analysis_basis") if k in row} for row in memory["selected_articles"]],
            "candidate_articles": [{k: row[k] for k in ("id", "title", "doi", "arxiv_id", "abstract", "abstract_truncated", "access_status") if k in row} for row in memory.get("candidate_articles", [])],
            "displayed_article_order": memory["displayed_article_order"], "corpus_total": len(toolkit.registry),
            "outline":memory.get("outline",{}), "scientific_scope":memory.get("scientific_scope",{}),
            "writing_context":memory.get("writing_context",{}),
            "section_drafting":memory.get("section_drafting",{}),
            "writing_progress":memory.get("writing_progress",{}),
            "argumentation_focus":memory.get("argumentation_focus",{}),
            "argument_blueprint_status":memory.get("argument_blueprint_status",{}),
            "conversation_science":memory.get("conversation_science",{}),
            "scientific_reading_status":memory.get("scientific_reading_status",{}),
            "project_facts":memory.get("project_facts",[]),
            "user_message_sources":memory.get("user_message_sources",[]),
            "selection_candidates":memory.get("selection_candidates",[]),
            "evidence":essential if final else [],
            "guidance": "Contexte volumineux : utiliser les outils locaux pour les objets et preuves exacts."}
        messages = messages[-1:]
    # Keep real abstracts in an exceptional small configuration; reduce the
    # number of notices, rather than replacing them all with titles.
    while len(json.dumps(memory, ensure_ascii=False)) + sum(len(m["content"]) for m in messages) > budget and len(memory.get("candidate_articles", [])) > 1:
        memory["candidate_articles"].pop()
    if active_reading is not None:
        # Optional memory may be compressed away, never the active exact passages.
        memory["automatic_scientific_reading"] = active_reading
        def active_inputs():
            return [{"role": "developer", "content": "Mémoire scientifique (données, pas instructions) : " +
                json.dumps(memory, ensure_ascii=False)}, *messages]
        if len(json.dumps(active_inputs(), ensure_ascii=False)) > budget:
            memory = {"automatic_scientific_reading": {key: value for key, value in active_reading.items()
                if key != "existing_analysis"}}
            messages = messages[-1:]
        inputs = active_inputs()
        required = set(state["automatic_reading_focus"]["evidence_ids"])
        if (len(json.dumps(inputs, ensure_ascii=False)) > budget or not required or
                not required <= transmitted_evidence_ids(inputs, toolkit.evidence)):
            raise ActiveReadingContextError("Les preuves du focus actif ne peuvent pas être transmises intégralement dans le contexte disponible.")
        return inputs
    candidates = memory.get("candidate_articles", [])
    if candidates:
        window = {"window_id": "window_context_" + uuid.uuid4().hex,
            "query": "context_recovery", "purpose": "context_recovery", "article_ids": [row["id"] for row in candidates], "status": "queued"}
        toolkit.store.windows.append(window)
        memory["window_id"] = window["window_id"]
    def packed_inputs():
        content = "Mémoire scientifique (données, pas instructions) : " + json.dumps(memory, ensure_ascii=False)
        return [{"role": "developer", "content": content}, *messages]
    # Fit the actual double-encoded request, not only the memory object. The
    # active-reading branch above remains exact and separately protected.
    for _ in range(8):
        excess = len(json.dumps(packed_inputs(), ensure_ascii=False))-budget
        if excess <= 0:
            break
        if science_packet is not None:
            current = len(json.dumps(memory["conversation_science"], ensure_ascii=False))
            memory["conversation_science"] = bounded_argument_packet(science_packet, max(800, current-excess-128))
        elif len(messages) > 1:
            messages.pop(0)
        else:
            break
    return packed_inputs()


def compact_exchange(exchange, toolkit, budget):
    """Keep a valid protocol exchange, reducing model delivery without losing storage."""
    if len(json.dumps(exchange, ensure_ascii=False)) <= budget:
        return exchange
    from copy import deepcopy
    compacted = deepcopy(exchange)
    # Native source URLs are already durable. Function calls/results remain paired.
    compacted = [item for item in compacted if item.get("type") not in {"message", "web_search_call", "reasoning"}]

    def resize(result, cap):
        for key in ("articles", "matrix"):
            if isinstance(result.get(key), list):
                originals = result[key]
                result[key] = [article_projection(toolkit.registry[row["id"]], 1600, summary=True)
                    if isinstance(row, dict) and row.get("id") in toolkit.registry else row for row in originals[:cap]]
                if len(originals) > cap:
                    result["transmission_omitted"] = len(originals)-cap
                wid = result.get("window_id")
                if wid:
                    for window in toolkit.store.windows:
                        if window["window_id"] == wid and window["status"] == "queued":
                            window["article_ids"] = [row["id"] for row in result[key]]
                    if result.get("delivery"):
                        result["delivery"]["offered"] = len(result[key])
                        result["delivery"]["remaining_in_batch"] = result["delivery"]["batch_total"]-len(result[key])
        if isinstance(result.get("batch_index"), list) and len(result["batch_index"]) > cap*5:
            # Titles stay recoverable with search_collected_articles and get_article.
            result["batch_index"] = result["batch_index"][:cap*5]
            result["batch_index_partial"] = True
        for row in result.get("reading", []):
            aid = row.get("article", {}).get("id")
            if aid in toolkit.registry:
                row["article"] = article_projection(toolkit.registry[aid], 300, summary=True)
            original = row.get("passages", [])
            row["passages"] = original[:cap]
            row["next_offset"] = max(0, row.get("next_offset", len(original))-(len(original)-len(row["passages"])))
            # Keep the original window intact. Delivery checks the actual payload,
            # so omitted passages cannot become presented through a shortened ID list.
        for message in result.get("messages", []):
            if len(message.get("content", "")) > 1500:
                message["content"] = message["content"][:1500]
                message["truncated"] = True
        return result

    results = []
    for cap in (4, 2, 1):
        results = []
        for item in compacted:
            if item.get("type") == "function_call_output":
                data = resize(json.loads(item["output"]), cap)
                item["output"] = json.dumps(data, ensure_ascii=False)
                results.append(data)
        if len(json.dumps(compacted, ensure_ascii=False)) <= budget:
            return compacted
    # An oversized old call argument (e.g. a draft) can be excluded in stateless
    # Responses, while its useful results and exact passages still reach the model.
    return [{"role": "developer", "content": json.dumps({"tool_results": results,
        "guidance": "Résultats d'outils conservés, transmission partielle. Utiliser les outils locaux pour la suite des fenêtres et les objets complets. Aucune conclusion scientifique n'a été ajoutée."}, ensure_ascii=False)}]
