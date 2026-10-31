"""Model-facing projections only. Durable scientific records and evidence remain exact."""
from __future__ import annotations

import json
import math
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


class SelectionContextError(ValueError):
    """An incumbent and its challenger cannot be fairly compared in this budget, after every
    safe reduction. Deterministic: the same corpus and budget fail the same way (details say why)."""
    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details or {}


def selection_duplicates(memory, state):
    """Views a triage batch neither judges from nor needs, once the need is structured: the
    topic and scope restating research_need, the exploration options and queries (they serve
    the discovery review), the long list of unexamined ids. Nothing scientific about a candidate
    is touched; every removal is listed (run 111cf83c: ~9 000 characters of restatements)."""
    removed = []
    if not state.get("research_need"):
        return removed
    topic = memory.get("topic")
    if isinstance(topic, dict) and set(topic) - {"topic"}:
        memory["topic"] = {"topic": topic.get("topic")}
        removed.append("topic : reformulations du besoin (research_need fait foi)")
    scope = memory.get("scientific_scope")
    if isinstance(scope, dict):
        dropped = [k for k in ("topic", "technical_context", "objectives", "uncertainties") if k in scope]
        for key in dropped:
            scope.pop(key)
        if dropped:
            removed.append("scientific_scope : " + ", ".join(dropped) + " (research_need fait foi)")
    progress = memory.get("research_objective_progress")
    if isinstance(progress, dict):
        dropped = [k for k in ("exploration_options", "unexamined_ids", "guidance", "discovery_review", "review") if progress.get(k)]
        for key in dropped:
            progress.pop(key)
        if dropped:
            removed.append("research_objective_progress : " + ", ".join(dropped) + " (revue de découverte)")
    need = memory.get("research_need")
    if isinstance(need, dict) and (need.get("families") or need.get("uncertainties")):
        memory["research_need"] = {**{k: v for k, v in need.items() if k != "uncertainties"},
            "families": [f["family"] if isinstance(f, dict) else f for f in need.get("families", [])]}
        removed.append("research_need : requêtes des familles et incertitudes (revue de découverte)")
    return removed


RELEVANCE_KEPT = ("criteria", "fit", "uncertainties", "missing_information")


def compact_selection_dossiers(memory, level):
    """Progressive, traceable reduction of the comparison dossiers before any challenger is
    deferred: 1) earlier justifications are shortened (the full text stays stored), 2) the
    free-text fields of earlier relevance profiles are omitted, keeping the per-criterion
    judgments, fit, uncertainties and missing information. Abstracts, criteria, provenance and
    the incumbents themselves are never removed."""
    comparison = memory["selection_comparison"]
    for dossier in [*comparison.get("incumbents", []), *comparison.get("challengers", [])]:
        assessment = dossier.get("assessment") or {}
        if level >= 1 and len(assessment.get("justification") or "") > 240:
            assessment["justification"] = assessment["justification"][:240]
            assessment["justification_truncated"] = True
        relevance = dossier.get("relevance")
        if level >= 2 and isinstance(relevance, dict) and set(relevance) - set(RELEVANCE_KEPT):
            dossier["relevance_omitted_fields"] = sorted(set(relevance) - set(RELEVANCE_KEPT))
            dossier["relevance"] = {k: relevance[k] for k in RELEVANCE_KEPT if k in relevance}
    return {1: "justifications antérieures abrégées à 240 caractères (texte complet conservé)",
        2: "champs libres des profils antérieurs omis ; critères, adéquation, incertitudes et informations manquantes conservés"}[level]


# Each request serves one task. The durable memory (articles, analyses, evidence,
# relations, plan, drafts, decisions) is never reduced; only what is transmitted
# for the current task is chosen here, everything else stays retrievable by tools.
COMMON_KEYS = {"topic", "summary", "focus", "outline", "corpus_total", "guidance", "writing_progress", "scientific_scope",
    "consultant_instructions", "context_task", "retrieval", "argument_blueprint_status", "selected_articles", "diagnostic_context",
    "turn_goal", "lock_coverage", "conversation_focus", "research_objective_progress", "research_provider_health", "research_need"}
TASK_KEYS = {
    # Reading one article needs its passages, not the project or the lock ledger.
    "reading": (COMMON_KEYS - {"diagnostic_context", "lock_coverage", "research_need"}) | {"automatic_scientific_reading", "scientific_reading_status"},
    "argumentation": COMMON_KEYS | {"argumentation_focus", "user_message_sources", "project_facts", "scientific_reading_status"},
    # A section works from its own dossier (sources, proof-backed claims, project work units):
    # corpus-wide views (selected articles, lock coverage, reading ledger) stay in storage.
    "section": {"topic", "outline", "scientific_scope", "consultant_instructions", "context_task", "diagnostic_context",
        "turn_goal", "writing_progress", "section_drafting", "conversation_focus", "manuscript_coordination"},
    "planning": COMMON_KEYS | {"conversation_science", "conversation_guidance", "argument_blueprint", "project_facts",
        "scientific_reading_status", "coverage_review", "displayed_article_order", "plan_sources", "plan_sources_total", "recent_searches", "plan_part",
        "other_plan_sources", "manuscript_coordination"},
    "selection": COMMON_KEYS | {"selection_candidates", "selection_candidates_total", "recent_searches", "coverage_review",
        "displayed_article_order", "candidate_index", "candidate_index_total", "selection_science", "selection_comparison"},
    "recovery": COMMON_KEYS | {"scientific_reading_recovery", "scientific_reading_status", "candidate_index",
        "candidate_index_total", "candidate_articles", "candidate_guidance", "recent_searches", "displayed_article_order"},
}
# Optional, recoverable views removed first when a focused request is too large;
# the task's own packet is reduced last, by rebuilding it smaller (never clipped).
OPTIONAL_ORDER = ("manuscript_coordination", "recent_searches", "candidate_index", "coverage_review", "displayed_article_order", "argument_blueprint",
    "project_facts", "scientific_reading_status", "section_sources_status", "summary", "focus", "candidate_articles",
    "selected_articles", "plan_sources", "scientific_scope", "outline")
# Views removed before the scientific packet of a request shrinks (final fitting).
SECONDARY_ORDER = ("manuscript_coordination", "recent_searches", "coverage_review", "scientific_reading_status", "writing_context", "lock_coverage",
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


def scientific_dossier(state, aid):
    """Same projection for incumbent and challenger; no scientific score or new claim."""
    from .research import candidate_signature, version_groups, card_level, LEVEL_RANK, qualification
    article = state.get("articles", {}).get(aid, {})
    assessment = article.get("assessment") or {}
    groups = version_groups(state)
    versions = [other for other in groups if groups[other] == groups.get(aid) and
        state["articles"][other].get("selection_override") is not False and other not in state.get("held_article_ids", [])]
    source_id = min(versions, key=lambda other: (LEVEL_RANK[card_level(state, other)[0]], other != aid)) if len(versions) > 1 else aid
    source = state.get("articles", {}).get(source_id, article)
    analysis = source.get("analysis") or {}
    claims = {}
    for key in ("problem", "method", "contribution", "results", "limitations", "validation_protocol"):
        rows = analysis.get(key) or []
        if isinstance(rows, list):
            claims[key] = [{"article_id": source_id, "statement": str(c.get("statement", ""))[:400],
                "statement_truncated": len(str(c.get("statement", ""))) > 400,
                "evidence_ids": c.get("evidence_ids", [])[:4]} for c in rows[:2] if isinstance(c, dict)]
    abstract = article.get("abstract") or ""
    return {"id": aid, "title": article.get("title"), "version": candidate_signature(state, aid),
        "abstract": abstract[:900], "abstract_truncated": len(abstract) > 900, "abstract_chars": len(abstract),
        "relevance": assessment.get("relevance"),
        # Same reading of the model's own criteria judgments for incumbents and challengers.
        "qualification": {k: v for k, v in qualification(state, aid).items() if k in {"tier", "label", "current", "essential"}}
            if assessment else None,
        "assessment": {"decision": assessment.get("decision"),
            "contribution": assessment.get("contribution"), "justification": assessment.get("justification", "")[:600],
            "justification_truncated": len(assessment.get("justification", "")) > 600,
            **({"contribution_consistency": assessment["contribution_consistency"]}
                if assessment.get("contribution_consistency") else {})}, "saved_claims": claims,
        "provenance": {"basis": source.get("analysis_basis") or ("abstract" if abstract else "metadata"),
            "science_source_article_id": source_id, "verified_version_ids": versions,
            "reading_coverage": analysis.get("reading_coverage"), "assessment_is_model_judgment": True,
            "claims_are_bounded": True}}


def fit_selection(memory, state):
    """Remove a whole challenger, never an incumbent's scientific dossier."""
    incumbents = set(memory.get("selection_comparison", {}).get("incumbent_ids", []))
    rows = memory.get("selection_candidates", [])
    removable = [i for i, row in enumerate(rows) if row["id"] not in incumbents]
    if len(removable) <= 1:
        return False
    rows.pop(removable[-1])
    comparison = memory.get("selection_comparison", {})
    kept = {row["id"] for row in rows}
    comparison["challengers"] = [row for row in comparison.get("challengers", []) if row["id"] in kept]
    comparison["deferred_challengers"] = comparison.get("deferred_challengers", 0)+1
    return True


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
            if focus["stage"] == "discovery_review":
                packet = memory["argumentation_focus"]["packet"]
                if packet.get("pending_candidates"):
                    packet["pending_candidates"].pop()
                elif packet.get("challengers"):
                    packet["challengers"].pop()
                elif packet.get("reference_leads"):
                    packet["reference_leads"].pop()
                else:
                    break
                continue
            current = len(json.dumps(memory["argumentation_focus"]["packet"], ensure_ascii=False))
            memory["argumentation_focus"]["packet"] = bounded_argument_packet(argument_packet(state, toolkit,
                focus.get("article_ids"), claim_offsets=focus.get("claim_offsets")), max(1500, min(int(current*factor), current-excess-256)),
                proof_mode="reference")
        elif task == "planning" and memory.get("conversation_science", {}).get("papers"):
            from .argumentation import argument_packet
            current = len(json.dumps(memory["conversation_science"], ensure_ascii=False))
            ids = [paper["id"] for paper in memory["conversation_science"]["papers"]]
            memory["conversation_science"] = targeted_packet(argument_packet(state, toolkit, ids, active_only=True),
                max(1500, min(int(current*factor), current-excess-256)), ids)
        elif task == "selection" and "selection_candidates" not in keep:
            if memory.get("selection_comparison"):
                if not fit_selection(memory, state):
                    break
            elif len(memory.get("selection_candidates", [])) > 3:
                memory["selection_candidates"] = memory["selection_candidates"][:max(3, int(len(memory["selection_candidates"])*factor))]
            else:
                break
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
                if all(key in value and (value[key] == field or
                        # An archived proof can acquire a stricter display/view
                        # reservation from its source table, without changing
                        # its immutable source receipt. Never accept a downgrade.
                        key == "table_structure_status" and field is None and value[key] == "ambiguous")
                        for key, field in projected.items()):
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


def table_qualifications(analysis, evidence, snapshots):
    """Link an earlier broad DTW summary to its explicit, cell-backed qualification.

    This only exposes an existing correction: no new scientific conclusion is
    generated, no durable claim/proof is removed, and ambiguous cells never qualify.
    """
    if not evidence or snapshots is None:
        return {}
    corrections = []
    for field in ('results', 'comparisons'):
        for claim in analysis.get(field, []):
            text = claim.get('statement', '')
            if not re.search(r'\bDTW\b', text, re.I) or not re.search(r'\bmais\b|nuanc|ne .*pas|\bhowever\b|\bnot\b', text, re.I):
                continue
            if not claim.get('evidence_ids') or any(eid not in evidence or not exact_source_valid(evidence[eid], snapshots)
                    for eid in claim['evidence_ids']):
                continue
            rows = [evidence[eid] for eid in claim['evidence_ids']
                if (evidence[eid].get('table') or {}).get('structure_status') == 'verified_cells']
            groups = {}
            for proof in rows:
                table = proof['table']
                key = (proof.get('article_id'), proof.get('document_sha256'), proof.get('source_url'), table.get('number'))
                groups.setdefault(key, []).append(proof)
            for key, proofs in groups.items():
                # More than one scenario and more than one winning model are
                # necessary before qualifying any blanket "best DTW" wording.
                winners, scenarios = set(), set()
                try:
                    for proof in proofs:
                        table = proof['table']; cells = table['row']; headers = table['headers']
                        if len(cells) != len(headers) or not re.search(r'\bDTWs?\b', table.get('caption', ''), re.I):
                            raise ValueError('Not a comparable DTW row')
                        values = [float(v.replace(',', '.')) for v in cells[1:]]
                        if not all(math.isfinite(v) for v in values):
                            raise ValueError('Non-finite cells')
                        winners.update(headers[i+1] for i,v in enumerate(values) if v == min(values))
                        scenarios.add(cells[0])
                except (ValueError, KeyError, TypeError):
                    continue
                if len(scenarios) > 1 and len(winners) > 1 and all(w.casefold() in text.casefold() for w in winners):
                    corrections.append((key, claim))
    qualified = {}
    for field in ('results', 'comparisons'):
        for index, claim in enumerate(analysis.get(field, [])):
            text = claim.get('statement', '')
            if not re.search(r'\bDTW\b', text, re.I) or not re.search(r'meilleur|\bbest\b', text, re.I):
                continue
            if any((evidence.get(eid, {}).get('table') or {}).get('structure_status') == 'verified_cells'
                    for eid in claim.get('evidence_ids', [])):
                continue
            for key, correction in corrections:
                if correction is claim or not re.search(r'(?:tableau|table)\s*'+re.escape(str(key[3]))+r'\b', text, re.I):
                    continue
                if not any((evidence.get(eid, {}).get('article_id'), evidence.get(eid, {}).get('document_sha256'),
                        evidence.get(eid, {}).get('source_url')) == key[:3] for eid in claim.get('evidence_ids', [])):
                    continue
                qualified[field, index] = {'statement': correction['statement'], 'evidence_ids': correction['evidence_ids']}
    return qualified


def analysis_projection(analysis, *, claims=None, statement_chars=None, evidence=None, active_only=False, snapshots=None):
    """Model view of a card. With the evidence store, claims backed only by
    related-work passages are tagged subject="background" (a prior work described
    by this publication, not its own contribution)."""
    from .attribution import claim_subject
    result = {}
    qualifications = table_qualifications(analysis, evidence, snapshots)
    for name in ANALYSIS_FIELDS:
        value = analysis.get(name)
        if not value:
            continue
        if isinstance(value, list) and value and isinstance(value[0], dict):
            result[name] = [{"statement": item["statement"][:statement_chars] if statement_chars else item["statement"],
                "evidence_ids": item["evidence_ids"], "origin": item.get("origin", "source_statement"),
                "basis": item.get("basis", analysis.get("analysis_basis")),
                **({"subject": "background"} if evidence is not None and claim_subject(item, evidence) == "background" else {}),
                **({'interpretation_status': 'qualified_by_verified_cells', 'qualification': qualifications[name,index]}
                    if (name,index) in qualifications else {})}
                for index,item in enumerate(value[:claims] if claims else value)
                if not active_only or (name,index) not in qualifications]
        elif isinstance(value, list):
            # Always a copy: a bounded view is trimmed in place, the card never.
            result[name] = list(value[:claims] if claims else value)
        else:
            result[name] = value
    return result


def evidence_projection(evidence, snapshots=None):
    row = {name: evidence.get(name) for name in ("evidence_id", "article_id", "exact_quote", "section",
        "page_number", "source_url", "source_version", "basis", "document_version_id", "document_sha256",
        "source_identifiers", "version_status", "printed_page", "pdf_page_label", "page_verification", "table", "table_structure_status")}
    if evidence.get("equation"):
        row["equation"] = evidence["equation"]
    if not row["version_status"]:
        row["source_version"], row["version_status"] = "legacy_unverified", "legacy_unverified"
    if evidence.get("table_header"):
        row["table_header"] = evidence["table_header"]
    # Old page-sized proofs may predate the table-specific evidence rows. Their
    # exact source snapshot already contains the extraction reservations. Expose
    # those as view metadata, never rewrite the archived quote/fingerprint.
    source = (snapshots or {}).get(evidence.get("source_fingerprint"), {})
    if source.get("article_id") == evidence.get("article_id"):
        explicit = (evidence.get("table") or {}).get("number")
        reservations = {}
        for chunk in source.get("chunks", []):
            table = chunk.get("table") or {}
            number = table.get("number")
            if not number or (explicit and str(number) != str(explicit)):
                continue
            if not explicit and not re.search(r"\bTable(?:au)?\s+"+re.escape(str(number))+r"\b",
                    evidence.get("exact_quote", ""), re.I):
                continue
            reservations[str(number)] = {"number": number, "structure_status": table.get("structure_status", "ambiguous"),
                "source_fingerprint": evidence["source_fingerprint"],
                "restriction": "Cell/column association is not verified; do not assign numerical results."
                    if table.get("structure_status") != "verified_cells" else "Verified extracted cells of this table only."}
        if reservations:
            row["table_restrictions"] = list(reservations.values())
            if any(t["structure_status"] != "verified_cells" for t in reservations.values()):
                row["table_structure_status"] = "ambiguous"
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


# Dimensions of a final scientific answer, in transmission priority: results come first
# (run 53292dc4: only one « method » claim per work reached the final request, every saved
# figure — TetGAN FID 23,35/12,27, Non-iterative 22 % — was then called unavailable).
FINAL_GROUPS = (("results", ("results", "metrics", "tables_figures")),
    ("limitations", ("limitations", "conditions_of_validity", "generalization", "open_questions")),
    ("method", ("method", "technical_mechanism", "equations")),
    ("protocol", ("experimental_setup", "validation_protocol", "datasets_materials", "parameters")),
    ("comparisons", ("comparisons",)),
    ("problem", ("problem", "objective", "contribution")))
# Room the final scientific dossier may take beyond the configured finalization memory,
# within the unchanged request limit (about 15 000 input tokens at most).
FINAL_SCIENCE_CAP = 60000
# Beside that dossier: the question, its goal, references, the selection and the bibliography.
FINAL_REST_CHARS = 9000
_QUANTIFIED = re.compile(r"\d")
# An explicit study dimension restricted to the plane (« two-dimensional elasticity problems »):
# it must not be presented as a volumetric demonstration. Context words exclude « 2D images ».
_PLANE_STUDY = re.compile(r"\b(?:two[- ]dimensional|2[- ]?D|bidimensionnel\w*|plane[- ]strain|plane[- ]stress)\b"
    r"(?:\W+\w+){0,4}?\W+(?:problems?|domains?|examples?|cases?|geometr\w*|elasticity|simulations?|benchmarks?|"
    r"tests?|meshes|mesh|boundary[- ]value|BVPs?|probl[eè]mes?|domaines?|cas\b|exemples?)", re.I)


def study_scope_flags(article, evidence=None):
    """Explicit characteristics of a study read from its stored notice and proofs (exact
    quotations, never inferred): a validation restricted to the plane is reported."""
    flags = []
    sources = [("abstract", article.abstract or "")]
    sources += [(e["evidence_id"], e.get("exact_quote", "")) for e in (article.analysis or {}).get("evidence", [])
        if isinstance(e, dict)]
    for source, text in sources:
        match = _PLANE_STUDY.search(text or "")
        if match:
            start = max(0, text.rfind(".", 0, match.start())+1)
            end = text.find(".", match.end())
            flags.append({"flag": "validation_2d", "source": source,
                "quote": text[start:end+1 if end != -1 else len(text)].strip()[:400]})
            break
    return flags


def final_article_ids(state, toolkit, selected):
    """Works of the final answer: the canonical selection of an open literature search
    (the counter, the depth check and the published list use the same one)."""
    from .research import active, canonical_selection
    if active(state):
        chosen = [aid for aid in canonical_selection(state)["selected_ids"] if aid in toolkit.registry]
        if chosen:
            return chosen
    return [aid for aid in selected if aid in toolkit.registry and toolkit.registry[aid].analysis]


# Essential claims of a work in a final answer, sent before any other: every result and
# limit and the first claims of its method, protocol, comparisons and problem (tier 1,
# the room grows for them); then its other quantified claims and a few more (tier 2).
FINAL_ESSENTIAL = {"results": None, "limitations": None, "method": 2, "protocol": 2, "comparisons": 1, "problem": 1}
FINAL_SECONDARY = {"method": 3, "protocol": 3, "comparisons": 2, "problem": 2}


def _final_order(paper):
    """Essential claims first, one dimension in turn (quantified claims first in each);
    then the other claims; claims repeating an earlier one (same words) last. Lists are
    reordered, never shortened. Returns the number of essential claims."""
    analysis = paper["analysis"]
    streams = []
    for _, fields in FINAL_GROUPS:
        rows = [(f, c) for f in fields for c in analysis.get(f, []) if isinstance(c, dict)]
        rows.sort(key=lambda row: not _QUANTIFIED.search(row[1]["statement"]))
        streams.append(rows)
    grouped = {f for _, fields in FINAL_GROUPS for f in fields}
    streams.append([(f, c) for f, value in analysis.items() if f not in grouped and isinstance(value, list)
        for c in value if isinstance(c, dict)])
    names = [name for name, _ in FINAL_GROUPS] + ["other"]
    essential, secondary, rest, repeats, seen, kept = [], [], [], [], [], {}
    for index in range(max((len(s) for s in streams), default=0)):
        for name, stream in zip(names, streams):
            if index >= len(stream):
                continue
            field, claim = stream[index]
            # A repeat says the same thing from the same proofs (values and names included):
            # TetGAN's card states its representation three times from one passage.
            words = set(re.findall(r"\w+", claim["statement"].casefold()))
            proofs = frozenset(claim.get("evidence_ids", []))
            if words and any(len(words & other)/len(words | other) >= (.6 if proofs == other_proofs else .85)
                    for other, other_proofs in seen):
                repeats.append((field, claim))
                continue
            seen.append((words, proofs))
            cap = FINAL_ESSENTIAL.get(name, 0)
            kept[name] = kept.get(name, 0)+1
            if cap is None or kept[name] <= cap:
                essential.append((field, claim))
            elif kept[name] <= FINAL_SECONDARY.get(name, 0) or (name != "other" and _QUANTIFIED.search(claim["statement"])):
                secondary.append((field, claim))
            else:
                rest.append((field, claim))
    ordered = [*essential, *secondary, *rest, *repeats]
    paper["secondary_claims"] = len(secondary)
    lists = {}
    for field, claim in ordered:
        lists.setdefault(field, []).append(claim)
    paper["analysis"] = {**{k: v for k, v in analysis.items() if not (isinstance(v, list) and v and isinstance(v[0], dict))}, **lists}
    paper["claim_order"] = [field for field, _ in ordered]
    paper["repeated_claims"] = len(repeats)
    paper["essential_claims"] = len(essential)
    return paper


def _groups(analysis):
    return [name for name, fields in FINAL_GROUPS if any(analysis.get(f) for f in fields)]


def final_science_packet(state, toolkit, ids, limit):
    """The scientific dossier of a final answer, built from the saved cards and proofs of
    every reading (not the last window): problem, method, protocol, results, comparisons and
    limits of each selected work, with their exact proof references. Every saved dimension
    of every work is transmitted; the room grows (up to FINAL_SCIENCE_CAP) before any is
    left out, and whatever could not be sent is reported as such, never as unavailable."""
    from .argumentation import argument_packet, bounded_argument_packet
    from .research import card_level
    packet = argument_packet(state, toolkit, ids)
    tasks = {t["article_id"]: t for t in state.get("scientific_reading", {}).get("tasks", [])}
    names = {"results": "résultats", "limitations": "limites", "method": "méthode", "protocol": "protocole expérimental",
        "comparisons": "comparaisons", "problem": "problème étudié"}
    for paper in packet["papers"]:
        _final_order(paper)
        # Each reading's own limit (« ces trois passages ne donnent aucun résultat numérique ») is
        # true of its window only; the card's limit is computed over all readings (run 53292dc4:
        # the last window's note hid the FID values saved by an earlier one).
        documented = _groups(paper["analysis"])
        analysis = paper["analysis"]
        superseded = len(analysis.pop("analysis_limits", None) or []) or int(bool(analysis.get("analysis_limit")))
        analysis.pop("analysis_limit", None)
        examined = len(tasks.get(paper["id"], {}).get("examined_evidence_ids", []))
        analysis["analysis_limit"] = (f"Fiche cumulée sur toutes les lectures ({examined} passage(s) examiné(s), lecture ciblée non "
            "exhaustive). Documenté : " + (", ".join(names[g] for g in documented) or "aucune dimension")
            + ". Non trouvé dans les passages examinés : " + (", ".join(names[g] for g, _ in FINAL_GROUPS if g not in documented) or "—") + ".")
        paper["window_limits_superseded"] = superseded
    saved = {p["id"]: _groups(p["analysis"]) for p in packet["papers"]}

    essential = {p["id"]: p["essential_claims"] for p in packet["papers"]}

    def variant(tier):
        """The packet with each work's claims cut after its tier (claim totals unchanged, so
        what is left out stays counted as not transmitted)."""
        cut = {k: v for k, v in packet.items() if k != "papers"}
        cut["papers"] = []
        for paper in packet["papers"]:
            n = {"essential": paper["essential_claims"], "secondary": paper["essential_claims"]+paper["secondary_claims"]}.get(tier)
            offsets, pairs = {}, []
            for field in paper["claim_order"][:n]:
                pairs.append((field, paper["analysis"][field][offsets.get(field, 0)]))
                offsets[field] = offsets.get(field, 0)+1
            lists = {}
            for field, claim in pairs:
                lists.setdefault(field, []).append(claim)
            cut["papers"].append({**paper, "analysis": {**{k: v for k, v in paper["analysis"].items()
                if not (isinstance(v, list) and v and isinstance(v[0], dict))}, **lists}, "claim_order": [f for f, _ in pairs]})
        return cut
    variants = {tier: variant(tier) for tier in ("all", "secondary", "essential")}

    def missing(candidate):
        sent = {p["id"]: _groups(p["analysis"]) for p in candidate.get("papers", [])}
        taken = {p["id"]: sum(len(v) for v in p["analysis"].values() if isinstance(v, list)) for p in candidate.get("papers", [])}
        return ({aid: [g for g in groups if g not in sent.get(aid, [])] for aid, groups in saved.items()},
            {aid: max(0, n-taken.get(aid, 0)) for aid, n in essential.items()})
    best, room = None, limit
    while True:
        done = False
        for mode in ("exact", "reference"):
            for tier in ("all", "secondary", "essential"):
                candidate = bounded_argument_packet(variants[tier], room, proof_mode=mode)
                gaps, short = missing(candidate)
                score = (sum(len(v) for v in gaps.values()), sum(short.values()), -sum(len(v) for p in candidate.get("papers", [])
                    for v in p["analysis"].values() if isinstance(v, list)))
                if best is None or score < best[0]:
                    best = (score, candidate, gaps, room, short)
                if not any(gaps.values()) and not any(short.values()):
                    done = True
                    break
            if done:
                break
        if done or room >= FINAL_SCIENCE_CAP:
            break
        room = min(FINAL_SCIENCE_CAP, int(room*1.3))
    _, result, gaps, room, short = best
    tasks = {t["article_id"]: t for t in state.get("scientific_reading", {}).get("tasks", [])}
    labels = {"documented": "passages ciblés du texte intégral (lecture non exhaustive)",
        "fulltext_partial": "passages ciblés du texte intégral, dimensions principales incomplètes",
        "abstract_only": "abstract seul : aucune analyse du texte intégral", "pending": "fiche non vérifiée"}
    report = {}
    for paper in packet["papers"]:
        aid = paper["id"]
        level = card_level(state, aid)[0]
        sent = next((p for p in result.get("papers", []) if p["id"] == aid), None)
        report[aid] = {"analysis_depth": labels[level], "level": level,
            "passages_examined": len(tasks.get(aid, {}).get("examined_evidence_ids", [])),
            "dimensions_saved": saved[aid], "dimensions_sent": _groups(sent["analysis"]) if sent else [],
            "dimensions_not_sent": gaps[aid],
            "claims_saved": paper["claim_total"], "claims_sent": sum(len(v) for v in (sent or {}).get("analysis", {}).values()
                if isinstance(v, list)), "repeated_claims": paper.get("repeated_claims", 0),
            "essential_claims": paper["essential_claims"], "essential_claims_not_sent": short[aid],
            "dimensions_not_documented": [name for name, _ in FINAL_GROUPS if name not in saved[aid]],
            **({"scope_flags": flags} if (flags := study_scope_flags(toolkit.registry[aid])) else {})}
    result["transmission"] = {"complete": not any(gaps.values()) and not any(short.values()), "room": room, "works": report}
    result["guidance"] = ("Fiches et preuves déjà enregistrées pour chaque publication sélectionnée, cumulées sur toutes "
        "les lectures. Exploiter réellement les problèmes, méthodes, protocoles, résultats chiffrés, comparaisons et limites "
        "transmis, avec leurs evidence_ids. dimensions_not_documented : non trouvé dans les passages examinés (à dire ainsi). "
        "dimensions_not_sent : enregistré mais non transmis ici, jamais à présenter comme indisponible. analysis_depth : "
        "abstract seul ≠ texte intégral, lecture ciblée ≠ exhaustive. scope_flags : caractéristique explicite de l'étude "
        "(validation_2d : ne pas la présenter comme une démonstration volumique 3D).")
    return result


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
    from .research import active as research_active, status as research_status
    if research_active(state):
        memory["research_objective_progress"] = research_status(state)
        observed = memory["research_objective_progress"]
        # Do not transmit the same depth judgments twice (review.article_reviews
        # duplicates depth_review). Complete records and exact proofs stay durable.
        observed["review"] = {k: v for k, v in observed["review"].items() if k not in {"article_reviews", "actions", "signature"}}
        observed["depth_review"] = {**observed["depth_review"], "articles": {aid: {
            "article_type": row["article_type"], "sufficient": row["sufficient"],
            "dimensions": [{"dimension": d["dimension"], "status": d["status"], "scope": d.get("scope", "examined_passages"), "reason": d["reason"][:400],
                "reason_truncated": len(d["reason"]) > 400, "evidence_ids": d["evidence_ids"][:8],
                "evidence_total": len(d["evidence_ids"])} for d in row["dimensions"]]}
            for aid, row in observed["depth_review"]["articles"].items()}}
        from .research import exploration_options
        memory["research_objective_progress"]["exploration_options"] = exploration_options(state)
        memory["research_provider_health"] = state.get("research_provider_health", {})
        if state.get("research_exploration_focus"):
            memory["adaptive_discovery"] = state["research_exploration_focus"]
    if task in {"section", "planning"}:
        from .editorial import coordination
        memory["manuscript_coordination"] = coordination(state, (state.get("writing_focus") or {}).get("section_id"))
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
        memory["turn_goal"] = {k: state["turn_goal"][k] for k in ("deliverable", "lock_ids", "reading_depth", "understanding", 'plan_operation')
            if k in state['turn_goal']}
    if state.get("research_need") and research_active(state):
        # The structured need: every selection, comparison and review judges against these criteria.
        memory["research_need"] = state["research_need"]
    final_protected = False
    if final:
        analyzed = final_article_ids(state, toolkit, selected)
        targets, scope = targeted_ids(state, toolkit)
        if targets:
            # The synthesis room goes to the publications the request is about.
            analyzed = targets if scope == "exclusive" else list(dict.fromkeys([*targets, *analyzed]))
        if research_active(state) and analyzed:
            # The bibliography of the answer is the canonical selection, the same as the counter's.
            selected = list(analyzed)
            memory["selected_articles"] = [article_projection(toolkit.registry[aid], 500, summary=True) for aid in selected]
            memory["response_selection"] = {"article_ids": selected, "guidance": "Publications de la réponse, dans "
                "cet ordre : displayed_article_ids doit être exactement cette liste. Les autres candidats restent "
                "disponibles dans la bibliothèque."}
        # Keep a compact bibliography and dedicate the synthesis room to the
        # already-saved scientific claims, relations and their exact proofs.
        memory["selected_articles"] = [{k:v for k,v in row.items() if k in
            {"id","title","year","doi","arxiv_id","abstract","abstract_truncated",
             "access_status","analysis_basis","verified","assessment"}} for row in memory["selected_articles"]]
        if analyzed:
            from .argumentation import argument_packet
            science_packet = argument_packet(state, toolkit, analyzed)
            # Saved cards of every reading, all their dimensions: protected from the reductions
            # below, the room grows for them within the request limit (run 53292dc4).
            memory["conversation_science"] = final_science_packet(state, toolkit, analyzed,
                int(budget*(.85 if scope == "exclusive" else .78)))
            final_protected = True
            # The bibliography of the answer, compact: the cards above carry the content.
            memory["selected_articles"] = [{k: v for k, v in row.items() if k not in {"abstract", "abstract_truncated", "assessment"}}
                for row in memory["selected_articles"]]
            science_chars = len(json.dumps(memory["conversation_science"], ensure_ascii=False))
            # The memory budget yields to the essential dossier only: the request, its goal and
            # selection keep a fixed share; secondary views are removed first (final fitting).
            budget = max(budget, min(science_chars + FINAL_REST_CHARS, toolkit.config.max_context_chars-6000))
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
        # A previous revision is not this conversation's active writing task. A
        # compact preview suffices; the exact draft remains available by section ID.
        active_section = next((s for s in outline.get("sections", [])
            if s["id"] == state["document"]["active_section_id"]), {})
        draft = (active_section.get("drafts") or [{}])[-1]
        text = draft.get("raw_text") or draft.get("text", "")
        memory["writing_context"] = {"section_id": active_section.get("id"), "title": active_section.get("title"),
            "draft_id": draft.get("id"), "text": text[:min(1500, budget//6)],
            "text_truncated": len(text) > min(1500, budget//6), "retrieval": "write_section(action=read, section_id)"}
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
        if focus["stage"] == "discovery_review":
            from .research import discovery_packet
            memory["argumentation_focus"] = {**focus, "packet": discovery_packet(state)}
        else:
            memory["argumentation_focus"] = {**focus,
                "packet": bounded_argument_packet(argument_packet(state, toolkit, focus.get("article_ids"), claim_offsets=focus.get("claim_offsets")),
                    int(budget*.72), proof_mode="reference")}
        if focus["stage"] == "research_review":
            memory["argumentation_focus"]["depth_questions"] = {
                "experimental": ["method/architecture", "protocol/data/conditions", "results/metrics", "comparisons/ablations", "limits/open_questions"],
                "theoretical": ["hypotheses", "proofs/properties", "scope/limitations"]}
            selected_ids = set(focus.get("article_ids", []))
            memory["argumentation_focus"]["challengers"] = [{"id": aid, "title": a.title,
                "abstract_preview": a.abstract[:300], "abstract_complete": len(a.abstract) <= 300,
                "assessment": a.assessment, "families": a.scientific_families}
                for aid, a in toolkit.registry.items() if aid not in selected_ids and a.selection_override is not False
                and aid not in state.get("held_article_ids", []) and a.assessment.get("decision") != "reject"][:16]
        if focus["stage"] == "project_facts":
            # Exact consultant words are needed only to record project facts.
            memory["user_message_sources"] = [{"id": m["id"], "content": m["content"][:3000]}
                for m in state.get("messages", [])[-8:] if m.get("role") == "user" and m.get("id")]
    if state.get("scientific_reading"):
        reading = state["scientific_reading"]
        memory["scientific_reading_status"] = {"tasks": [{k: task.get(k) for k in
            ("article_id", "axes", "status", "basis", "coverage_sufficient", "remaining_passages", "exhausted", "field_status", "core_covered", "initial_reading_ready", "result_availability")} for task in reading.get("tasks", [])],
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
        memory["selection_candidates"] = [{**{k: v for k, v in article_projection(toolkit.registry[aid],
                len(toolkit.registry[aid].abstract) if research_active(state) else 500, summary=True).items()
             if k != "analysis" and not (research_active(state) and k == "assessment")}, **({"collected_in_earlier_message": True} if aid in earlier else {}),
            # Facts about how the work was found, to weigh with its content; not a score.
            "discovery": {"queries": len(toolkit.registry[aid].matched_queries), "providers": toolkit.registry[aid].sources[:4],
                "citations": toolkit.registry[aid].citation_count}}
            for aid in focus["candidate_ids"] if aid in toolkit.registry]
        memory["selection_candidates_total"] = focus.get("presented_total", len(memory["selection_candidates"]))
        if research_active(state):
            incumbents = state.get("evaluation", {}).get("recommended_article_ids", [])
            target = state.get("research_progress", {}).get("requested_count") or 5
            incumbents = [aid for aid in incumbents if aid in toolkit.registry and
                toolkit.registry[aid].selection_override is not False and aid not in state.get("held_article_ids", [])][:target]
            focus_ids = {row["id"] for row in memory["selection_candidates"]}
            # Incumbents absent from an explicit small action still get the same dossier.
            memory["selection_comparison"] = {"incumbent_ids": incumbents,
                "evaluation_ids": [aid for aid in focus.get("evaluation_ids", focus["candidate_ids"]) if aid in focus_ids],
                "incumbents": [scientific_dossier(state, aid) for aid in incumbents],
                "challengers": [scientific_dossier(state, aid) for aid in focus["candidate_ids"]
                    if aid in focus_ids and aid not in incumbents], "deferred_challengers": 0,
                "contract": "Classement cumulatif : comparer aux finalistes. Toute sortie exige replacements, "
                    "informations utilisées et incertitudes. Une omission ne retire aucun finaliste. "
                    "Les jugements de pertinence ne sont pas des preuves scientifiques."}
            if focus.get("pair_reviews"):
                # Same tier, different contributions: the model compares the two dossiers (their relevance
                # profiles are already in incumbents/challengers) on these dimensions, never on keywords.
                from .research import COMPARISON_DIMENSIONS
                memory["selection_comparison"]["same_tier_reviews"] = [{"challenger_id": p["challenger_id"],
                    "finalist_ids": p["finalist_ids"], "tier": p["tier"], "tier_label": p["label"],
                    "dimensions": list(COMPARISON_DIMENSIONS)} for p in focus["pair_reviews"]]
            # Each exact abstract is sent once. Both kinds of dossier point to
            # the same notice projection instead of duplicating its first 900 chars.
            for dossier in [*memory["selection_comparison"]["incumbents"], *memory["selection_comparison"]["challengers"]]:
                if dossier["id"] in focus_ids:
                    dossier.pop("abstract", None)
                    dossier["abstract_in"] = "selection_candidates"
                    dossier["abstract_truncated"] = False
            removed = selection_duplicates(memory, state)
            if removed:
                memory["selection_comparison"]["context_reductions"] = removed
        # Existing cards have their own bounded scientific view below. Their
        # summary must not push the challengers' full abstracts out of the batch.
        memory["selected_articles"] = [{k: row[k] for k in ("id", "title", "doi", "arxiv_id") if k in row}
            for row in memory.get("selected_articles", [])]
        if focus.get("review") and not research_active(state):
            from .argumentation import argument_packet, bounded_argument_packet
            memory["selection_science"] = bounded_argument_packet(argument_packet(state, toolkit,
                [aid for aid in focus["candidate_ids"] if aid in toolkit.registry and toolkit.registry[aid].analysis]),
                int(budget*.45), proof_mode="reference")
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
        operation = (state.get('turn_goal') or {}).get('plan_operation') or {}
        if operation.get('kind') in {'revision', 'section_revision'}:
            # The previous structure is input to the edit, not optional chat history.
            memory['outline']['sections'] = [{
                **{k:s.get(k) for k in ('id','title','article_ids')},
                'scientific_objective':s.get('scientific_objective','')[:120]}
                for s in state['outline']['sections']]
            memory['outline']['index_partial'] = False
            memory['outline']['retrieval'] = 'manage_outline(read, offset, limit) donne les détails conservés.'
    if task == "conversation" or task == "planning" and not state.get("plan_part"):
        # A plan part carries its own sources' cards (plan_sources); the cards of works
        # outside the plan (earlier, off-topic analyses) do not travel with it.
        plan_ids = [r["id"] for r in memory.get("plan_sources", [])] if task == "planning" else None
        analyzed = [aid for aid in selected if aid in toolkit.registry and toolkit.registry[aid].analysis
            and (plan_ids is None or aid in plan_ids)]
        targets, scope = targeted_ids(state, toolkit) if task == "conversation" else (
            (list(plan_ids), 'exclusive') if (state.get('turn_goal') or {}).get('plan_operation', {}).get('memory_only') else ([], None))
        if targets:
            # The same stable packet of the targeted works in every round of the request,
            # from their saved cards (not from the last tool page only).
            analyzed = targets if scope == "exclusive" else list(dict.fromkeys([*targets, *analyzed]))
        if analyzed:
            from .argumentation import argument_packet, bounded_argument_packet
            science_packet = argument_packet(state, toolkit, analyzed, active_only=task == 'planning')
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
        if final_protected:
            # Read passages beyond the saved cards stay secondary: the dossier above carries
            # the cards of every reading; this view only completes them, within a fixed share.
            budget_evidence = min(8000, budget*.15)
        else:
            budget_evidence = None
        by_article = {aid: [] for aid in selected}
        for aid in selected:
            if aid not in toolkit.registry:
                continue
            for e in toolkit.registry[aid].analysis.get("evidence", [])[:4]:
                if e["evidence_id"] in toolkit.shown_evidence and exact_source_valid(e, toolkit.snapshots):
                    by_article[aid].append(evidence_projection(e, toolkit.snapshots))
        # Also keep passages actually read but not yet incorporated into a saved analysis.
        for eid in sorted(reversed(toolkit.shown_evidence), key=lambda eid: not bool(toolkit.evidence.get(eid, {}).get("table"))):
            e = toolkit.evidence.get(eid)
            if e and e["article_id"] in selected and exact_source_valid(e, toolkit.snapshots) and not any(r["evidence_id"] == eid for r in by_article[e["article_id"]]):
                by_article[e["article_id"]].append(evidence_projection(e, toolkit.snapshots))
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
            if (len(json.dumps(memory, ensure_ascii=False)) > budget * .8 or budget_evidence is not None
                    and len(json.dumps(memory["evidence"], ensure_ascii=False)) > budget_evidence):
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
    if task == "argumentation" and state["argumentation_focus"]["stage"] == "discovery_review":
        # Discovery needs the saved notice dossiers, not every passage/reading task.
        memory.pop("scientific_reading_status", None)
        memory.pop("selected_articles", None)
        observed = memory.get("research_objective_progress", {})
        memory["research_objective_progress"] = {k: v for k, v in observed.items() if k in {
            "requested_count", "verified_analyzed_ids", "candidates_total", "unexamined_count", "gaps",
            "exploration_options", "pending_article_ids"}}
    # Always retain the active question before fitting optional scientific memory.
    conversation = state.get("messages", [])
    if state.get("writing_segment"):
        # A segment of an automatic drafting job answers the consultant's request that
        # opened the job, complete; the automatic answers written after it are not requests.
        from .instructions import current_request
        request = current_request(state)
        position = next((i for i, m in enumerate(conversation) if m is request), len(conversation)-1)
        latest, earlier = conversation[position:position+1], conversation[:position]
    else:
        latest, earlier = conversation[-1:], conversation[:-1]
    messages = [{"role": m["role"], "content": m["content"]} for m in latest]
    # A section or comparison works from durable records and the captured
    # consultant instructions, not from older chat turns.
    history = [] if task in {"section", "argumentation"} else earlier[-(toolkit.config.recent_messages-1):] if toolkit.config.recent_messages > 1 else []
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
    while len(json.dumps(memory, ensure_ascii=False)) + sum(len(m["content"]) for m in messages) > budget and len(memory.get("selection_candidates", [])) > (1 if research_active(state) else 6):
        if memory.get("selection_comparison"):
            if not fit_selection(memory, state):
                break
        else:
            memory["selection_candidates"].pop()
    if task in TASK_KEYS and task != "reading":
        if task == "section" and memory.get("section_drafting", {}).get("packet", {}).get("revision"):
            # The old draft, all source identities and current instructions take
            # the same memory share; unrelated document previews are recoverable.
            memory = {k: v for k, v in memory.items() if k in {
                "section_drafting", "consultant_instructions", "context_task", "diagnostic_context"}}
        memory, messages = fit_focused(memory, messages, budget, task, state, toolkit, section,
            keep=("plan_sources", "other_plan_sources", "lock_coverage", "outline") if task == "planning" else ())
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
            # A research objective, its coverage and the adaptive exploration focus are what
            # an open literature search continues from: never dropped by this reduction.
            **{key: memory[key] for key in ("research_objective_progress", "adaptive_discovery", "research_provider_health",
                "response_selection", "research_need") if key in memory},
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
    for _ in range(8+(len(SECONDARY_ORDER) if coverage_targets or final_protected else 0)):
        excess = len(json.dumps(packed_inputs(), ensure_ascii=False))-budget
        if excess <= 0:
            break
        # A request on targeted publications: secondary views go before any of their proofs.
        secondary = next((key for key in SECONDARY_ORDER if memory.get(key) and not (final_protected and key == "selected_articles")
            and not (task == 'planning' and key == 'outline')),
            None) if coverage_targets or final_protected else None
        if science_packet is not None and memory.get("conversation_science") and secondary:
            # Secondary views go before any proof of a requested publication.
            memory.pop(secondary)
            continue
        if final_protected:
            # The final dossier is never shrunk: older messages go, then the request is sent
            # as built (the agent checks the absolute request limit).
            if len(messages) > 1:
                messages.pop(0)
                continue
            break
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
                    *(("plan_sources", "other_plan_sources", "lock_coverage", "outline") if task == "planning" else ())))
        else:
            break
    if coverage_targets and isinstance(memory.get("conversation_focus"), dict):
        memory["conversation_focus"]["coverage"] = packet_coverage(memory.get("conversation_science"), coverage_targets)
    if task == "selection" and memory.get("selection_comparison") and len(json.dumps(packed_inputs(), ensure_ascii=False)) > budget:
        # Progressive and traceable: shorter earlier justifications, then compact earlier profiles,
        # then a smaller batch (deferred challengers stay for the next one); never a finalist removed.
        reductions = memory["selection_comparison"].setdefault("context_reductions", [])
        for level in (1, 2):
            if len(json.dumps(packed_inputs(), ensure_ascii=False)) <= budget:
                break
            reductions.append(compact_selection_dossiers(memory, level))
        deferred = 0
        while len(json.dumps(packed_inputs(), ensure_ascii=False)) > budget and fit_selection(memory, state):
            deferred += 1
        if deferred:
            reductions.append(f"{deferred} concurrent(s) reporté(s) au lot suivant, entiers")
        size = len(json.dumps(packed_inputs(), ensure_ascii=False))
        if size > budget:
            comparison = memory["selection_comparison"]
            raise SelectionContextError("Dossiers des finalistes et du concurrent trop volumineux pour une comparaison équilibrée.",
                {"size": size, "budget": budget, "reductions": list(reductions),
                    "incumbent_ids": list(comparison.get("incumbent_ids", [])),
                    "candidate_ids": [row["id"] for row in memory.get("selection_candidates", [])],
                    "parts": {k: len(json.dumps(v, ensure_ascii=False)) for k, v in memory.items()}})
    if memory.get("selection_window"):
        for window in toolkit.store.windows:
            if window["window_id"] == memory["selection_window"]["window_id"]:
                window["article_ids"] = [row["id"] for row in memory.get("selection_candidates", [])]
        if memory.get("selection_comparison"):
            delivered_ids = {row["id"] for row in memory.get("selection_candidates", [])}
            memory["selection_comparison"]["evaluation_ids"] = [aid for aid in memory["selection_comparison"]["evaluation_ids"] if aid in delivered_ids]
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
        if isinstance(result.get("claims"), list) and len(result["claims"]) > cap:
            original = result["claims"]
            result["claims"] = original[:cap]
            result["transmission_omitted_claims"] = result.get("transmission_omitted_claims", 0) + len(original)-cap
            result["complete"] = False
            result["next_offset"] = result.get("offset", 0)+len(result["claims"])
            needed = {eid for c in result["claims"] for eid in c.get("evidence_ids", [])}
            result["evidence"] = [e for e in result.get("evidence", []) if e.get("evidence_id") in needed]
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
        data = {"tool_results": [{k: v for k, v in r.items() if k in {"saved", "published", "error", "rejection_code", "window_id",
            "article_id", "offset", "transmission_omitted_claims"}}
            for r in results if isinstance(r, dict)], "guidance": data["guidance"]}
    return packed()
