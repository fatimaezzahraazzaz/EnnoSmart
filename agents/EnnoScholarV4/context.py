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


# Each request serves one task. The durable memory (articles, analyses, evidence,
# relations, plan, drafts, decisions) is never reduced; only what is transmitted
# for the current task is chosen here, everything else stays retrievable by tools.
COMMON_KEYS = {"topic", "summary", "focus", "outline", "corpus_total", "guidance", "writing_progress", "scientific_scope",
    "consultant_instructions", "context_task", "retrieval", "argument_blueprint_status", "selected_articles", "diagnostic_context",
    "turn_goal", "lock_coverage", "conversation_focus"}
TASK_KEYS = {
    # Reading one article needs its passages, not the project or the lock ledger.
    "reading": (COMMON_KEYS - {"diagnostic_context", "lock_coverage"}) | {"automatic_scientific_reading", "scientific_reading_status"},
    "argumentation": COMMON_KEYS | {"argumentation_focus", "user_message_sources", "project_facts", "scientific_reading_status"},
    # A section works from its own dossier (sources, proof-backed claims, project work units):
    # corpus-wide views (selected articles, lock coverage, reading ledger) stay in storage.
    "section": {"topic", "outline", "scientific_scope", "consultant_instructions", "context_task", "diagnostic_context",
        "turn_goal", "writing_progress", "section_drafting", "conversation_focus"},
    "planning": COMMON_KEYS | {"conversation_science", "conversation_guidance", "argument_blueprint", "project_facts",
        "scientific_reading_status", "coverage_review", "displayed_article_order", "plan_sources", "plan_sources_total", "recent_searches", "plan_part",
        "other_plan_sources"},
    "selection": COMMON_KEYS | {"selection_candidates", "selection_candidates_total", "recent_searches", "coverage_review",
        "displayed_article_order", "candidate_index", "candidate_index_total"},
    "recovery": COMMON_KEYS | {"scientific_reading_recovery", "scientific_reading_status", "candidate_index",
        "candidate_index_total", "candidate_articles", "candidate_guidance", "recent_searches", "displayed_article_order"},
}
# Optional, recoverable views removed first when a focused request is too large;
# the task's own packet is reduced last, by rebuilding it smaller (never clipped).
OPTIONAL_ORDER = ("recent_searches", "candidate_index", "coverage_review", "displayed_article_order", "argument_blueprint",
    "project_facts", "scientific_reading_status", "section_sources_status", "summary", "focus", "candidate_articles",
    "selected_articles", "plan_sources", "scientific_scope", "outline")
# Views removed before the scientific packet of a request shrinks (final fitting).
SECONDARY_ORDER = ("recent_searches", "coverage_review", "scientific_reading_status", "writing_context", "lock_coverage",
    "selected_articles", "candidate_articles", "source_material", "evidence", "outline", "summary", "focus",
    "scientific_scope", "argument_blueprint_status", "writing_progress", "displayed_article_order", "project_facts",
    "user_message_sources", "diagnostic_context")
TASK_PACKET = {"section": "section_drafting", "argumentation": "argumentation_focus", "reading": "automatic_scientific_reading",
    "planning": "conversation_science", "selection": "selection_candidates", "recovery": "scientific_reading_recovery"}
READING_ROWS = 12


def diagnostic_task_locks(state, task):
    """Locks whose project dossier a task needs, or None for the general packet."""
    if task == "planning":
        return list((state.get("plan_part") or {}).get("lock_ids") or [])
    if task == "section":
        from .tools.outline import resolve_section
        try:
            section = resolve_section(state, state["writing_focus"]["section_id"])
        except (KeyError, ValueError):
            return []
        return list(section.get("lock_ids") or [])
    if task == "argumentation":
        return list((state.get("turn_goal") or {}).get("lock_ids") or [])
    return None


# Room for the project dossier of one task (whole records; omissions are counted).
DOSSIER_CHARS = {"planning": 9000, "section": 9000, "argumentation": 4500}
# Memory of one plan part (a lock or an axis, generated in parallel), within the
# unchanged max_context_chars: its sources with their cards and the project's work units.
PLAN_PART_CONTEXT_CHARS = 26000
# Memory of one section request, within the unchanged max_context_chars: exact proof
# passages (1.2-1.9 k characters each) are the section's input, not optional detail
# (run b52f582a: 0 complete passage transmitted in 9 section requests at 18 000).
SECTION_CONTEXT_CHARS = 40000


# Below this size a multi-lock dossier would keep lock titles only (run d71d17b0: 716 chars).
DOSSIER_FLOOR = 4500


def dossier_budget(task, budget):
    return max(DOSSIER_FLOOR, min(DOSSIER_CHARS[task], int(budget*.4)))


def lock_dossier(state, lock_ids, budget):
    from .diagnostic_link import lock_dossier as build
    # A lock's part keeps its own facts; general project context is added when room is left.
    return build(state, lock_ids or None, budget)


def context_task(state, final=False):
    """The task this request serves, in the orchestrator's focus precedence."""
    if final:
        return "final"
    for key, task in (("automatic_reading_focus", "reading"), ("argumentation_focus", "argumentation"),
            ("writing_focus", "section"), ("writing_planning", "planning"), ("selection_focus", "selection"),
            ("reading_recovery_focus", "recovery")):
        if state.get(key):
            return task
    return "conversation"


def reading_digest(state, article_ids):
    """Per-work reading state for the works of one task; the full ledger stays stored."""
    rows = []
    for task in state.get("scientific_reading", {}).get("tasks", []):
        if task["article_id"] in article_ids:
            rows.append({"article_id": task["article_id"], "status": task.get("status"), "basis": task.get("basis"),
                "core_covered": task.get("core_covered"), "coverage_sufficient": task.get("coverage_sufficient"),
                "undocumented": [name for name, row in (task.get("dimensions") or {}).items() if row.get("status") != "documented"]})
    return rows


def fit_focused(memory, messages, budget, task, state, toolkit, section=None, keep=()):
    """Bound a focused request: optional views go first, then the task packet is
    rebuilt smaller. Claims stay whole (fewer of them, with resumable offsets);
    durable records are untouched."""
    def size():
        return len(json.dumps(memory, ensure_ascii=False)) + sum(len(m["content"]) for m in messages)
    if size() <= budget:
        return memory, messages
    if memory.get("diagnostic_context") and task not in DOSSIER_CHARS:
        from .diagnostic_link import active_packet
        memory["diagnostic_context"] = active_packet(state, 1200)
    messages = messages[-1:]
    packet_key = TASK_PACKET.get(task)
    for key in OPTIONAL_ORDER:
        if size() <= budget:
            break
        if key != packet_key and key not in keep and key in memory:
            memory.pop(key)
    factor = .8
    while size() > budget and factor > .1:
        excess = size()-budget
        if task == "section" and section is not None:
            from .writing_context import writing_context
            current = len(json.dumps(memory["section_drafting"]["packet"], ensure_ascii=False))
            memory["section_drafting"]["packet"] = writing_context(state, toolkit, section,
                budget=max(2000, current-excess-256 if memory["section_drafting"]["packet"].get("revision")
                    else min(int(current*factor), current-excess-256)))
        elif task == "argumentation":
            from .argumentation import argument_packet, bounded_argument_packet
            focus = state["argumentation_focus"]
            current = len(json.dumps(memory["argumentation_focus"]["packet"], ensure_ascii=False))
            memory["argumentation_focus"]["packet"] = bounded_argument_packet(argument_packet(state, toolkit,
                focus.get("article_ids"), claim_offsets=focus.get("claim_offsets")), max(1500, min(int(current*factor), current-excess-256)),
                proof_mode="reference")
        elif task == "planning" and memory.get("conversation_science", {}).get("papers"):
            from .argumentation import argument_packet, bounded_argument_packet
            current = len(json.dumps(memory["conversation_science"], ensure_ascii=False))
            ids = [paper["id"] for paper in memory["conversation_science"]["papers"]]
            memory["conversation_science"] = bounded_argument_packet(argument_packet(state, toolkit, ids),
                max(1500, min(int(current*factor), current-excess-256)))
        elif task == "selection" and "selection_candidates" not in keep and len(memory.get("selection_candidates", [])) > 3:
            memory["selection_candidates"] = memory["selection_candidates"][:max(3, int(len(memory["selection_candidates"])*factor))]
        else:
            break
        factor -= .2
    if size() > budget and memory.get("consultant_instructions"):
        memory["consultant_instructions"]["items"] = memory["consultant_instructions"]["items"][-5:]
    if size() > budget and task in DOSSIER_CHARS and memory.get("diagnostic_context"):
        # Last resort: the project dossier is rebuilt smaller (whole records, counted
        # omissions), never replaced by lock identifiers alone (run d71d17b0: 716 chars).
        current = len(json.dumps(memory["diagnostic_context"], ensure_ascii=False))
        memory["diagnostic_context"] = lock_dossier(state, diagnostic_task_locks(state, task),
            max(DOSSIER_FLOOR, current-(size()-budget)-256))
    return memory, messages


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


def analysis_projection(analysis, *, claims=None, statement_chars=None, evidence=None):
    """Model view of a card. With the evidence store, claims backed only by
    related-work passages are tagged subject="background" (a prior work described
    by this publication, not its own contribution)."""
    from .attribution import claim_subject
    result = {}
    for name in ANALYSIS_FIELDS:
        value = analysis.get(name)
        if not value:
            continue
        if isinstance(value, list) and value and isinstance(value[0], dict):
            result[name] = [{"statement": item["statement"][:statement_chars] if statement_chars else item["statement"],
                "evidence_ids": item["evidence_ids"], "origin": item.get("origin", "source_statement"),
                "basis": item.get("basis", analysis.get("analysis_basis")),
                **({"subject": "background"} if evidence is not None and claim_subject(item, evidence) == "background" else {})}
                for item in (value[:claims] if claims else value)]
        elif isinstance(value, list):
            # Always a copy: a bounded view is trimmed in place, the card never.
            result[name] = list(value[:claims] if claims else value)
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
        row["assessment"] = {k: v for k, v in article.assessment.items() if k in {"decision", "justification", "basis"} or
            (k in {"lock_ids", "contribution"} and v)}
    return row


def targeted_ids(state, toolkit):
    """Analysed publications this request targets (turn_goal references), in dialogue order,
    and whether they are the only ones (exclusive) or come first (primary)."""
    refs = (state.get("turn_goal") or {}).get("references") or {}
    if refs.get("ambiguous") or refs.get("scope") not in {"exclusive", "primary"}:
        return [], None
    ids = [aid for aid in refs.get("article_ids") or [] if aid in toolkit.registry and toolkit.registry[aid].analysis]
    return ids, refs["scope"] if ids else None


# Dimensions a detailed answer is built from (methods, protocols, results, limits). The other
# fields of a card (problem, objective, relevance…) stay readable page by page.
TARGET_FIELDS = ("method", "technical_mechanism", "equations", "parameters", "experimental_setup", "validation_protocol",
    "datasets_materials", "results", "metrics", "tables_figures", "comparisons", "limitations", "conditions_of_validity",
    "contribution")


def focus_fields(science_packet, targets, fields=TARGET_FIELDS):
    """The targeted works restricted to the requested dimensions, in the card's order."""
    import copy as _copy
    packet = _copy.deepcopy(science_packet)
    for paper in packet["papers"]:
        if paper["id"] not in targets:
            continue
        order = [field for field in paper.get("claim_order", []) if field in fields]
        kept = {field: value for field, value in paper["analysis"].items() if not isinstance(value, list) or field in fields}
        paper["deferred_fields"] = {field: len(value) for field, value in paper["analysis"].items()
            if isinstance(value, list) and value and field not in fields}
        paper["analysis"], paper["claim_order"] = kept, order
        used = {eid for value in kept.values() if isinstance(value, list) for claim in value if isinstance(claim, dict)
            for eid in claim.get("evidence_ids", [])}
        paper["proofs"] = [e for e in paper["proofs"] if e["evidence_id"] in used] or paper["proofs"][:1]
        paper["claim_total"] = len(order)
        # Header metadata of a targeted work stays in the reading ledger and the card.
        for key in ("scientific_families", "analysis_available", "source_version"):
            paper.pop(key, None)
    return packet


def targeted_packet(science_packet, limit, targets):
    """The scientific packet of the targeted publications, never one of them dropped.

    Exact quotations first; when they cannot carry every target with its claims, proof
    locators (evidence ids with section/page; quotations kept in the store and re-checked
    at publication) carry more claims of each target. Run 8cb4d68a: the final request had
    no LDPoly proof and only the last HoliTracer page."""
    from .argumentation import bounded_argument_packet
    if targets:
        # The requested dimensions of the targeted works first; their project context and other
        # records are secondary to their own claims and proofs.
        science_packet = focus_fields(science_packet, targets)

    def coverage(packet):
        papers = {p["id"]: p for p in packet.get("papers", [])}
        return [sum(len(v) for v in papers[aid]["analysis"].values() if isinstance(v, list)) if aid in papers else -1
            for aid in targets]
    exact = bounded_argument_packet(science_packet, limit)
    if not targets:
        return exact
    covered = coverage(exact)
    if min(covered, default=0) > 0 and not any(exact["omitted_claims"].get(aid) for aid in targets):
        return exact
    candidates = [(covered, exact), (coverage(reference := bounded_argument_packet(science_packet, limit, proof_mode="reference")), reference)]
    if science_packet.get("project_context") or science_packet.get("project_facts"):
        # The project side (recoverable with get_project_context) yields when the targeted works'
        # own claims need the room (run 8cb4d68a: 3 claims per work, the rest was project context).
        lean = {k: v for k, v in science_packet.items() if k not in {"project_context", "project_facts"}}
        lean["project_context_deferred"] = "Contexte projet récupérable avec get_project_context."
        for mode in ("exact", "reference"):
            lean_packet = bounded_argument_packet(lean, limit, proof_mode=mode)
            candidates.append((coverage(lean_packet), lean_packet))
    best = max(candidates, key=lambda row: (min(row[0], default=0), sum(row[0])))
    grow = limit
    while min(best[0], default=0) <= 0 and grow < limit*2:
        # Floor: every targeted work keeps at least its header, one claim and its proof locator.
        grow = int(grow*1.25)
        lean = {k: v for k, v in science_packet.items() if k not in {"project_context", "project_facts"}}
        packet = bounded_argument_packet(lean, grow, proof_mode="reference")
        best = max([best, (coverage(packet), packet)], key=lambda row: (min(row[0], default=0), sum(row[0])))
    return best[1]


def packet_coverage(packet, targets):
    """Claims and proofs of each targeted publication actually present in a packet."""
    papers = {p["id"]: p for p in (packet or {}).get("papers", [])}
    return {aid: {"claims_sent": sum(len(v) for v in papers[aid]["analysis"].values() if isinstance(v, list)) if aid in papers else 0,
        "proofs_sent": len(papers[aid].get("proofs", [])) if aid in papers else 0,
        "claims_not_sent": (packet or {}).get("omitted_claims", {}).get(aid, 0) if aid in papers else None}
        for aid in targets}


def model_context(state, toolkit, budget, *, final=False, supplied_article_ids=()):
    """A fresh bounded context, never the accumulating full tool transcript."""
    active_reading = None
    science_packet = None
    task = context_task(state, final)
    # « le deuxième », « ces articles »: the last exchange's own order, not an older table (run ce08ad31).
    from .references import current_order, focus_block
    order, order_source = current_order(state)
    from .writing_context import active_article_ids, section_projection, writing_context
    selected = active_article_ids(state, toolkit.registry)
    outline=state.get("outline",{})
    section_index=[{"id":s["id"],"title":s["title"][:160],"article_ids":s["article_ids"][:8]} for s in outline.get("sections",[])[:12]]
    memory = {"topic": state.get("research_state", {}), "summary": state.get("summary", ""),
        "focus": state.get("focus", {}), "outline": {**{k:outline[k] for k in ("title","version","plan_revision","status","approval") if k in outline},
            "sections":section_index,"sections_total":len(outline.get("sections",[])),"index_partial":len(outline.get("sections",[]))>12},
        "displayed_article_order": [{"number": i+1, "id": aid, "title": toolkit.registry[aid].title}
            for i, aid in enumerate(order) if aid in toolkit.registry],
        "displayed_article_order_source": order_source,
        # Small, protected: the request, its resolved publications, constraints and scope.
        # Never removed by the reductions below (run ce08ad31 lost the goal before publishing).
        "conversation_focus": focus_block(state, toolkit.registry, compact=task not in {"conversation", "final"}),
        "corpus_total": len(toolkit.registry), "selected_articles": [article_projection(toolkit.registry[aid],
            500 if final else 160, summary=True) for aid in selected if aid in toolkit.registry],
        "recent_searches": [{k: v for k, v in s.items() if k in {"query", "family", "warnings"}}
            for s in state.get("recent_searches", [])[-8:]],
        "discovered_url_count": len(state.get("web_sources", [])),
        "guidance": "Les notices complètes, anciens messages et passages exacts restent dans les outils. Une URL découverte n'est pas un texte lu."}
    from .diagnostic_link import active_packet, project_facts_view
    if state.get("mode") == "diagnostic_linked":
        dossier_locks = diagnostic_task_locks(state, task)
        if dossier_locks is not None:
            # A plan part, a section or a comparison receives the project facts of its
            # own locks (trials, results, parameters, verbatim evidence), sized for the
            # task and protected from the optional-view reductions below.
            memory["diagnostic_context"] = lock_dossier(state, dossier_locks, dossier_budget(task, budget))
        else:
            memory["diagnostic_context"] = active_packet(state, min(4200, max(1200, budget//4)))
        from .turn import lock_coverage
        memory["lock_coverage"] = lock_coverage(state, toolkit.registry)
        part_lock = (state.get("plan_part") or {}).get("lock_ids") if task == "planning" else None
        if part_lock and memory["lock_coverage"]:
            # One lock's part needs its own row; the others stay counted.
            coverage = memory["lock_coverage"]
            memory["lock_coverage"] = {**coverage, "locks": [r for r in coverage["locks"] if r["lock_id"] in part_lock],
                "other_locks": [{"lock_id": r["lock_id"], "status": r["status"], "retained_total": r["retained_total"]}
                    for r in coverage["locks"] if r["lock_id"] not in part_lock]}
    if state.get("turn_goal"):
        memory["turn_goal"] = {k: state["turn_goal"][k] for k in ("deliverable", "lock_ids", "reading_depth", "understanding")}
    if final:
        # Keep a compact bibliography and dedicate the synthesis room to the
        # already-saved scientific claims, relations and their exact proofs.
        memory["selected_articles"] = [{k:v for k,v in row.items() if k in
            {"id","title","year","doi","arxiv_id","abstract","abstract_truncated",
             "access_status","analysis_basis","verified","assessment"}} for row in memory["selected_articles"]]
        from .argumentation import argument_packet, bounded_argument_packet
        analyzed = [aid for aid in selected if aid in toolkit.registry and toolkit.registry[aid].analysis]
        targets, scope = targeted_ids(state, toolkit)
        if targets:
            # The synthesis room goes to the publications the request is about.
            analyzed = targets if scope == "exclusive" else list(dict.fromkeys([*targets, *analyzed]))
        if analyzed:
            science_packet = argument_packet(state, toolkit, analyzed)
            # An exclusive request has little else to carry: its works take most of the same budget.
            memory["conversation_science"] = targeted_packet(science_packet, int(budget*(.85 if scope == "exclusive" else .78)), targets)
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
    # The last section touched is useful to a conversation about the document,
    # not to a reading, comparison or another section's drafting.
    if state.get("document", {}).get("active_section_id") and task in {"conversation", "final"}:
        memory["writing_context"] = writing_context(state, toolkit, budget=min(6500, budget//3))
    progress = state.get("document", {}).get("writing_progress")
    if progress:
        # A bounded view: counts per section and only the active section's latest refusal.
        # The full error history stays in the durable progress (audit 91590987: 15 055
        # characters of errors had pushed every proof out of a section's request).
        active = (state.get("writing_focus") or {}).get("section_id")
        errors = progress.get("errors") or []
        memory["writing_progress"] = {**{k: progress.get(k) for k in ("id", "mode", "status", "approval_required")},
            "sections_total": len(progress.get("target_ids") or []), "saved_total": len(progress.get("saved_draft_ids") or {}),
            "blocked_section_ids": list((progress.get("blocked") or {}))[:12],
            "refusals_by_section": {sid: n for sid, n in __import__("collections").Counter(e.get("section_id") for e in errors).items() if sid},
            **({"active_section_last_refusal": next((e.get("message", "")[:700] for e in reversed(errors)
                if e.get("section_id") == active), None)} if active and any(e.get("section_id") == active for e in errors) else {})}
    section = None
    if task == "section":
        from .tools.outline import resolve_section
        section = resolve_section(state, state["writing_focus"]["section_id"])
        memory["section_drafting"] = {"focus": state["writing_focus"],
            "packet": writing_context(state, toolkit, section,
                budget=max(2000, budget-1800) if progress and progress.get("mode") == "section" and section.get("drafts")
                else max(6000, budget-dossier_budget("section", budget)-3500))}
        memory["section_sources_status"] = reading_digest(state, state["writing_focus"].get("article_ids", []))
        memory["retrieval"] = ("Le paquet est une vue bornée de la mémoire permanente. analysis_available donne le nombre "
            "d'affirmations sauvegardées par champ : get_analysis_claims(article_id, fields, offset) les transmet toutes, avec "
            "leurs preuves exactes ; analyze_article(read, evidence_ids) relit des passages connus, analyze_article(read, "
            "passage_query) cherche dans le texte conservé. Aucune nouvelle recherche bibliographique.")
        memory.pop("writing_context", None)
    from .instructions import instruction_packet
    instructions = instruction_packet(state, task, section_id=section["id"] if section else None,
        article_ids=(state.get("writing_focus") or state.get("argumentation_focus") or {}).get("article_ids", ()))
    if instructions:
        memory["consultant_instructions"] = instructions
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
    if task == "argumentation":
        from .argumentation import argument_packet, bounded_argument_packet
        focus = state["argumentation_focus"]
        memory["argumentation_focus"] = {**focus,
            "packet": bounded_argument_packet(argument_packet(state, toolkit, focus.get("article_ids"), claim_offsets=focus.get("claim_offsets")),
                int(budget*.72), proof_mode="reference")}
        if focus["stage"] == "project_facts":
            # Exact consultant words are needed only to record project facts.
            memory["user_message_sources"] = [{"id": m["id"], "content": m["content"][:3000]}
                for m in state.get("messages", [])[-8:] if m.get("role") == "user" and m.get("id")]
    if state.get("scientific_reading"):
        reading = state["scientific_reading"]
        memory["scientific_reading_status"] = {"tasks": [{k: task.get(k) for k in
            ("article_id", "axes", "status", "basis", "coverage_sufficient", "remaining_passages", "exhausted", "field_status")} for task in reading.get("tasks", [])],
            "remaining_article_ids": reading.get("remaining_article_ids", []), "blocked_reason": reading.get("blocked_reason"),
            "coverage_by_axis": reading.get("coverage_by_axis", {})}
        rows = memory["scientific_reading_status"]["tasks"]
        if len(rows) > READING_ROWS and task != "final":
            # A large corpus keeps every task in the ledger; the request carries the
            # active works first and the count of the others.
            active = set(reading.get("active_article_ids") or []) | set(
                (state.get("automatic_reading_focus") or state.get("argumentation_focus") or {}).get("article_ids", []))
            rows.sort(key=lambda row: row["article_id"] not in active)
            memory["scientific_reading_status"]["tasks"] = rows[:READING_ROWS]
            memory["scientific_reading_status"]["tasks_total"] = len(rows)
            memory["scientific_reading_status"].pop("coverage_by_axis", None)
        if final:
            # The synthesis room goes to saved claims; per-axis lists repeat the
            # task rows and stay in the durable ledger.
            memory["scientific_reading_status"].pop("coverage_by_axis", None)
            for row in memory["scientific_reading_status"]["tasks"]:
                statuses = row.pop("field_status", {})
                row.pop("axes", None)
                row["undocumented_fields"] = [f for f, status in (statuses or {}).items() if status != "documented"]
    if not final and state.get("selection_focus"):
        # Selection checkpoint: the presented candidates with their abstracts, so
        # the model judges them from the notices themselves, not from titles.
        focus = state["selection_focus"]
        earlier = set(focus.get("earlier_candidate_ids", []))
        memory["selection_candidates"] = [{**{k: v for k, v in article_projection(toolkit.registry[aid], 500, summary=True).items()
            if k != "analysis"}, **({"collected_in_earlier_message": True} if aid in earlier else {}),
            # Facts about how the work was found, to weigh with its content; not a score.
            "discovery": {"queries": len(toolkit.registry[aid].matched_queries), "providers": toolkit.registry[aid].sources[:4],
                "citations": toolkit.registry[aid].citation_count}}
            for aid in focus["candidate_ids"] if aid in toolkit.registry]
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
    if task == "planning":
        # The plan is built from the analysis: retained works with their abstracts,
        # selection reasons and lock attribution, next to the saved cards.
        from .turn import plan_sources, part_sources
        part = state.get("plan_part")
        if part and part["kind"] in {"lock", "axis"}:
            # One part of a plan generated in parallel: its own sources with their saved
            # cards (methods, results, limits), and every other source as a reference.
            memory["plan_sources"], memory["other_plan_sources"] = part_sources(state, toolkit.registry, part,
                int(budget*.4), toolkit.evidence)
        else:
            memory["plan_sources"] = plan_sources(state, toolkit.registry)
            # Every source stays visible (run d71d17b0: 4 of 8 finalists): abstracts
            # shrink first, then disappear, before any source would be dropped.
            for chars in (300, 160, 0):
                if len(json.dumps(memory["plan_sources"], ensure_ascii=False)) <= budget*.35:
                    break
                memory["plan_sources"] = plan_sources(state, toolkit.registry, abstract_chars=chars)
        memory["plan_sources_total"] = len(memory["plan_sources"])+len(memory.get("other_plan_sources", []))
        if part:
            # One part of a plan generated in parallel (a lock, an axis or the framing).
            memory["plan_part"] = part
    if task == "conversation" or task == "planning" and not state.get("plan_part"):
        # A plan part carries its own sources' cards (plan_sources); the cards of works
        # outside the plan (earlier, off-topic analyses) do not travel with it.
        plan_ids = set(r["id"] for r in memory.get("plan_sources", [])) if task == "planning" else None
        analyzed = [aid for aid in selected if aid in toolkit.registry and toolkit.registry[aid].analysis
            and (plan_ids is None or aid in plan_ids)]
        targets, scope = targeted_ids(state, toolkit) if task == "conversation" else ([], None)
        if targets:
            # The same stable packet of the targeted works in every round of the request,
            # from their saved cards (not from the last tool page only).
            analyzed = targets if scope == "exclusive" else list(dict.fromkeys([*targets, *analyzed]))
        if analyzed:
            from .argumentation import argument_packet, bounded_argument_packet
            science_packet = argument_packet(state, toolkit, analyzed)
            memory["conversation_science"] = targeted_packet(science_packet, int(budget*(.85 if scope == "exclusive" else .65)), targets)
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
            # Only proofs actually transmitted in the bounded packet count as sent.
            saved_ids = {e["evidence_id"] for paper in memory.get("conversation_science", {}).get("papers", [])
                for e in paper.get("proofs", [])}
            memory["evidence"] = [e for e in memory["evidence"] if e["evidence_id"] not in saved_ids]
            essential = [e for e in essential if e["evidence_id"] not in saved_ids]
            saved_articles = {p["id"] for p in memory.get("conversation_science", {}).get("papers", [])}
            memory["source_material"] = [row for row in memory["source_material"] if row["article_id"] not in saved_articles]
            memory["source_guidance"] = "Les preuves des fiches structurées sont transmises dans conversation_science ; les autres passages restent récupérables dans les outils."
    if task in TASK_KEYS:
        # A focused task receives its own packet and the shared essentials only;
        # stale packets of other tasks (an interrupted comparison, the last section
        # touched, the corpus index) stay in storage.
        memory = {key: value for key, value in memory.items() if key in TASK_KEYS[task]}
        memory["context_task"] = task
    # Always retain the active question before fitting optional scientific memory.
    latest = state.get("messages", [])[-1:]
    messages = [{"role": m["role"], "content": m["content"]} for m in latest]
    # A section or comparison works from durable records and the captured
    # consultant instructions, not from older chat turns.
    history = [] if task in {"section", "argumentation"} else state.get("messages", [])[-toolkit.config.recent_messages:-1]
    for message in reversed(history):
        text = message["content"]
        if message["role"] != "user":
            text = text[:min(toolkit.config.max_message_context_chars, 1800 if final else 3000)]
        messages.insert(0, {"role": message["role"], "content": text})
        if len(json.dumps(memory, ensure_ascii=False)) + sum(len(m["content"]) for m in messages) > budget:
            messages.pop(0)
            break
    # Remove optional historical data first. Scientific records are never changed.
    while len(json.dumps(memory, ensure_ascii=False)) + sum(len(m["content"]) for m in messages) > budget and memory.get("recent_searches"):
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
    if task in TASK_KEYS and task != "reading":
        if task == "section" and memory.get("section_drafting", {}).get("packet", {}).get("revision"):
            # The old draft, all source identities and current instructions take
            # the same memory share; unrelated document previews are recoverable.
            memory = {k: v for k, v in memory.items() if k in {
                "section_drafting", "consultant_instructions", "context_task", "diagnostic_context"}}
        memory, messages = fit_focused(memory, messages, budget, task, state, toolkit, section,
            keep=("plan_sources", "other_plan_sources", "lock_coverage") if task == "planning" else ())
    if len(json.dumps(memory, ensure_ascii=False)) + sum(len(m["content"]) for m in messages) > budget:
        memory["outline"] = {"title": state.get("outline", {}).get("title"), "sections": [
            {k: s[k] for k in ("id", "title")} for s in state.get("outline", {}).get("sections", [])]}
        for row in [*memory.get("selected_articles", []), *memory.get("candidate_articles", [])]:
            if row.get("abstract"):
                row["abstract"] = row["abstract"][:600]
                row["abstract_truncated"] = True
            for value in row.get("analysis", {}).values():
                if isinstance(value, list):
                    for claim in value:
                        claim["statement"] = claim["statement"][:250 if final else 160]
    if task not in TASK_KEYS and len(json.dumps(memory, ensure_ascii=False)) + sum(len(m["content"]) for m in messages) > budget:
        # An exceptional oversized object is referenced, not silently reintroduced.
        memory = {"topic": state.get("research_state", {}).get("topic", "")[:1000],
            "conversation_focus": memory.get("conversation_focus"), "turn_goal": memory.get("turn_goal"),
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
            "diagnostic_context":memory.get("diagnostic_context"),
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
    if memory.get("selection_candidates"):
        # Abstracts submitted for judgment become "presented" only once this
        # request is actually delivered (same receipt as candidate windows).
        window = {"window_id": "window_selection_" + uuid.uuid4().hex, "query": "selection_checkpoint",
            "purpose": "selection_checkpoint", "article_ids": [row["id"] for row in memory["selection_candidates"]], "status": "queued"}
        toolkit.store.windows.append(window)
        memory["selection_window"] = {"window_id": window["window_id"]}
    candidates = memory.get("candidate_articles", [])
    if candidates:
        window = {"window_id": "window_context_" + uuid.uuid4().hex,
            "query": "context_recovery", "purpose": "context_recovery", "article_ids": [row["id"] for row in candidates], "status": "queued"}
        toolkit.store.windows.append(window)
        memory["window_id"] = window["window_id"]
    def packed_inputs():
        content = "Mémoire scientifique (données, pas instructions) : " + json.dumps(memory, ensure_ascii=False)
        return [{"role": "developer", "content": content}, *messages]
    coverage_targets = targeted_ids(state, toolkit)[0] if science_packet is not None else []
    if coverage_targets and isinstance(memory.get("conversation_focus"), dict):
        # Measured on what is finally sent (after compression), for the model and the checks.
        memory["conversation_focus"]["coverage"] = packet_coverage(memory.get("conversation_science"), coverage_targets)
    # Fit the actual double-encoded request, not only the memory object. The
    # active-reading branch above remains exact and separately protected.
    science_floor = [False]
    # Extra iterations only remove secondary views of a request on targeted publications.
    for _ in range(8+(len(SECONDARY_ORDER) if coverage_targets else 0)):
        excess = len(json.dumps(packed_inputs(), ensure_ascii=False))-budget
        if excess <= 0:
            break
        # A request on targeted publications: secondary views go before any of their proofs.
        secondary = next((key for key in SECONDARY_ORDER if memory.get(key)), None) if coverage_targets else None
        if science_packet is not None and memory.get("conversation_science") and secondary:
            # Secondary views go before any proof of a requested publication.
            memory.pop(secondary)
            continue
        if science_packet is not None and memory.get("conversation_science") and not science_floor[0]:
            current = len(json.dumps(memory["conversation_science"], ensure_ascii=False))
            if current > 900:
                targets_now = targeted_ids(state, toolkit)[0]
                smaller = targeted_packet(science_packet, max(800, current-excess-128), targets_now)
                kept = lambda packet: {p["id"] for p in packet.get("papers", [])} & set(targets_now)
                if targets_now and kept(smaller) < kept(memory["conversation_science"]):
                    # Floor: a targeted work is never removed to save room (its claims and
                    # proofs are what the request asks for); the other reductions continue.
                    science_floor[0] = True
                    continue
                memory["conversation_science"] = smaller
                continue
        if len(messages) > 1:
            messages.pop(0)
        elif task != "final":
            # Escaping inside the request (JSON in JSON) is counted here: the same
            # bounded reduction applies to the exact size actually sent.
            current = len(json.dumps(memory, ensure_ascii=False)) + sum(len(m["content"]) for m in messages)
            # Candidate windows are already registered: their notices stay in place.
            memory, messages = fit_focused(memory, messages, max(1000, current-excess-256), task, state, toolkit, section,
                keep=("candidate_articles", "selection_candidates", "window_id", "selection_window",
                    # The plan's bibliography is its input: older messages go first.
                    *(("plan_sources", "other_plan_sources", "lock_coverage") if task == "planning" else ())))
        else:
            break
    if coverage_targets and isinstance(memory.get("conversation_focus"), dict):
        memory["conversation_focus"]["coverage"] = packet_coverage(memory.get("conversation_science"), coverage_targets)
    return packed_inputs()


def compact_exchange(exchange, toolkit, budget):
    """Keep a valid protocol exchange, reducing model delivery without losing storage."""
    if len(json.dumps(exchange, ensure_ascii=False)) <= budget:
        return exchange
    from copy import deepcopy
    compacted = deepcopy(exchange)
    # Native source URLs are already durable. Function calls/results remain paired.
    compacted = [item for item in compacted if item.get("type") not in {"message", "web_search_call", "reasoning"}]

    def resize(result, cap, abstract_chars):
        # Candidate windows keep every notice with a shorter abstract before any
        # notice is dropped: several parallel searches must not shrink each other's
        # windows to one notice (run d8606428). Passages and title lists keep their caps.
        for key in ("articles", "matrix"):
            if isinstance(result.get(key), list):
                originals = result[key]
                result[key] = [article_projection(toolkit.registry[row["id"]], abstract_chars, summary=True)
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
        if isinstance(result.get("batch_index"), list) and len(result["batch_index"]) > min(cap, 4)*5:
            # Titles stay recoverable with search_collected_articles and get_article.
            result["batch_index"] = result["batch_index"][:min(cap, 4)*5]
            result["batch_index_partial"] = True
        for row in result.get("reading", []):
            aid = row.get("article", {}).get("id")
            if aid in toolkit.registry:
                row["article"] = article_projection(toolkit.registry[aid], 300, summary=True)
            original = row.get("passages", [])
            row["passages"] = original[:min(cap, 4)]
            row["next_offset"] = max(0, row.get("next_offset", len(original))-(len(original)-len(row["passages"])))
            # Keep the original window intact. Delivery checks the actual payload,
            # so omitted passages cannot become presented through a shortened ID list.
        for message in result.get("messages", []):
            if len(message.get("content", "")) > 1500:
                message["content"] = message["content"][:1500]
                message["truncated"] = True
        return result

    results = []
    from .store import WINDOW_ARTICLES, WINDOW_ABSTRACT
    for cap, chars in ((WINDOW_ARTICLES, WINDOW_ABSTRACT), (WINDOW_ARTICLES, 220), (WINDOW_ARTICLES, 120), (4, 120), (2, 120), (1, 120)):
        results = []
        for item in compacted:
            if item.get("type") == "function_call_output":
                data = resize(json.loads(item["output"]), cap, chars)
                item["output"] = json.dumps(data, ensure_ascii=False)
                results.append(data)
        if len(json.dumps(compacted, ensure_ascii=False)) <= budget:
            return compacted
    # An oversized old call argument (e.g. a draft) can be excluded in stateless
    # Responses, while its useful results and exact passages still reach the model.
    data = {"tool_results": results,
        "guidance": "Résultats d'outils conservés, transmission partielle. Utiliser les outils locaux pour la suite des fenêtres et les objets complets. Aucune conclusion scientifique n'a été ajoutée."}
    packed = lambda: [{"role": "developer", "content": json.dumps(data, ensure_ascii=False)}]
    # Large objects are durable elsewhere (comparison packets in argumentation
    # memory, analyses and passages in the store): reference them rather than
    # exceeding the request budget and stopping the whole turn.
    for key in ("packet", "matrix", "scientific_relations", "batch_index", "reading", "articles", "writing_context", "messages"):
        if len(json.dumps(packed(), ensure_ascii=False)) <= budget:
            break
        for result in results:
            if isinstance(result, dict) and result.get(key):
                value = result[key]
                result[key] = {"omitted_from_context": True, "items": len(value) if isinstance(value, (list, dict)) else 1,
                    "recovery": "Objet conservé : relire avec l'outil correspondant ou la mémoire argumentation_focus."}
    while len(json.dumps(packed(), ensure_ascii=False)) > budget and len(results) > 1:
        results.pop(0)
    if len(json.dumps(packed(), ensure_ascii=False)) > budget:
        data = {"tool_results": [{k: v for k, v in r.items() if k in {"saved", "published", "error", "rejection_code", "window_id"}}
            for r in results if isinstance(r, dict)], "guidance": data["guidance"]}
    return packed()
