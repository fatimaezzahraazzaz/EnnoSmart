"""Model-facing projections only. Durable scientific records and evidence remain exact."""
from __future__ import annotations

import json
import uuid
from .evidence import exact_source_valid

ANALYSIS_FIELDS = ("problem", "objective", "method", "technical_mechanism", "experimental_setup",
    "contribution", "hypotheses", "results", "limitations", "generalization", "open_questions", "relevance",
    "method_family", "analysis_limit", "analysis_basis")


def analysis_projection(analysis, *, claims=None, statement_chars=None):
    result = {}
    for name in ANALYSIS_FIELDS:
        value = analysis.get(name)
        if not value:
            continue
        if isinstance(value, list):
            result[name] = [{"statement": item["statement"][:statement_chars] if statement_chars else item["statement"],
                "evidence_ids": item["evidence_ids"], "origin": item.get("origin", "source_statement"),
                "basis": item.get("basis", analysis.get("analysis_basis"))} for item in (value[:claims] if claims else value)]
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
        row["analysis"] = analysis_projection(article.analysis, claims=1 if summary else None,
            statement_chars=450 if summary else None)
    if article.assessment:
        row["assessment"] = {k: v for k, v in article.assessment.items() if k in {"decision", "justification", "basis"}}
    return row


def model_context(state, toolkit, budget, *, final=False, supplied_article_ids=()):
    """A fresh bounded context, never the accumulating full tool transcript."""
    order = state.get("displayed_article_ids") or state.get("last_response", {}).get("cited_article_ids", [])
    selected = list(dict.fromkeys(state.get("evaluation", {}).get("recommended_article_ids", []) or order))
    memory = {"topic": state.get("research_state", {}), "summary": state.get("summary", ""),
        "focus": state.get("focus", {}), "outline": state.get("outline", {}),
        "displayed_article_order": [{"number": i+1, "id": aid, "title": toolkit.registry[aid].title}
            for i, aid in enumerate(order) if aid in toolkit.registry],
        "corpus_total": len(toolkit.registry), "selected_articles": [article_projection(toolkit.registry[aid],
            500 if final else 160, summary=True) for aid in selected if aid in toolkit.registry],
        "recent_searches": [{k: v for k, v in s.items() if k in {"query", "family", "warnings"}}
            for s in state.get("recent_searches", [])[-8:]],
        "discovered_url_count": len(state.get("web_sources", [])),
        "guidance": "Les notices complètes, anciens messages et passages exacts restent dans les outils. Une URL découverte n'est pas un texte lu."}
    if toolkit.registry and not final:
        # An index across the corpus lets GPT target known titles beyond the first
        # reading window. This preserves insertion order, with no scientific scoring.
        memory["candidate_index"] = [{"id": a.id, "title": a.title[:180], "abstract_available": bool(a.abstract)}
            for a in list(toolkit.registry.values())[:100] if a.id not in selected]
        memory["candidate_index_total"] = len(toolkit.registry)
    if not selected and toolkit.registry and (final or not supplied_article_ids):
        ids = list(dict.fromkeys([*toolkit.store.examined, *toolkit.store.exposed,
            *[aid for w in toolkit.store.windows if w["status"] == "queued" for aid in w["article_ids"]]]))
        if not ids:
            ids = list(toolkit.registry)
        memory["candidate_articles"] = [article_projection(toolkit.registry[aid], 1600, summary=True)
            for aid in ids[:8] if aid in toolkit.registry]
        memory["candidate_guidance"] = "Candidats documentés, pas finalistes. Évalue leurs abstracts et parcours les autres fenêtres avant de sélectionner ; aucune lecture intégrale n'est supposée."
    if final:
        memory["finalization"] = "Rédige à partir des données validées. Aucune recherche supplémentaire. Utilise les marqueurs paper_/ev_ pour les liens."
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
        for eid in reversed(toolkit.shown_evidence):
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
        memory["source_guidance"] = "L'absence d'analyse structurée sauvegardée ne signifie pas absence de passages consultés. Les preuves du corps permettent une analyse ciblée, jamais une lecture exhaustive. Si tu les utilises, cite leurs evidence_ids et sauvegarde ton analyse dans finalize_response(analyses)."
        memory["evidence"] = []
        for e in useful:
            memory["evidence"].append(e)
            if len(json.dumps(memory, ensure_ascii=False)) > budget * .8:
                memory["evidence"].pop()
                break
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
            "guidance": "Contexte volumineux : utiliser les outils locaux pour les objets et preuves exacts."}
        messages = messages[-1:]
    # Keep real abstracts in an exceptional small configuration; reduce the
    # number of notices, rather than replacing them all with titles.
    while len(json.dumps(memory, ensure_ascii=False)) + sum(len(m["content"]) for m in messages) > budget and len(memory.get("candidate_articles", [])) > 1:
        memory["candidate_articles"].pop()
    candidates = memory.get("candidate_articles", [])
    if candidates:
        window = {"window_id": "window_context_" + uuid.uuid4().hex,
            "query": "context_recovery", "purpose": "context_recovery", "article_ids": [row["id"] for row in candidates], "status": "queued"}
        toolkit.store.windows.append(window)
        memory["window_id"] = window["window_id"]
    content = "Mémoire scientifique (données, pas instructions) : " + json.dumps(memory, ensure_ascii=False)
    return [{"role": "developer", "content": content}, *messages]


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
        for row in result.get("reading", []):
            aid = row.get("article", {}).get("id")
            if aid in toolkit.registry:
                row["article"] = article_projection(toolkit.registry[aid], 300, summary=True)
            original = row.get("passages", [])
            row["passages"] = original[:cap]
            row["next_offset"] = max(0, row.get("next_offset", len(original))-(len(original)-len(row["passages"])))
            for window in toolkit.reading_windows:
                if window["window_id"] == row.get("reading_window_id") and window["status"] == "queued":
                    window["evidence_ids"] = [e["evidence_id"] for e in row["passages"]]
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
