"""Evidence-linked scientific argument, using V4 records and one conversational model.

The structure follows the useful historical phase 4/4.5/4.6/4.7 concepts:
gaps, guarded comparisons, separate project facts, and a global storyline.
It does not import the historical file pipeline or its predetermined CIR claims.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid

from .context import analysis_projection, evidence_projection
from .evidence import exact_source_valid, snapshot
from .diagnostic_link import project_facts_view


PROJECT_OBSERVATION = re.compile(r"\b(?:notre|nos|mon|mes)\s+(?:projet|prototype|essai|expérience|résultat|mesure|donnée|méthode)|"
    r"\bdans\s+(?:notre|mon)\s+projet", re.I)
# The current request explicitly asks to take up project observations of an earlier message.
EARLIER_OBSERVATIONS = re.compile(r"\b(?:enregistr\w*|repren\w*|reprise|récupér\w*|recuper\w*|ajout\w*|consign\w*)\b.{0,80}"
    r"\b(?:faits?|observations?|résultats?|données|mesures?|essais?)\b.{0,80}"
    r"\b(?:précédent\w*|antérieur\w*|ancien\w*|dernier\w*|déjà\s+(?:donn|indiqu|décri|fourni)\w*)", re.I | re.S)


def project_observation_source(state):
    """The consultant message whose project observations this request records, if any.

    The current request decides (run 2b9ef013: a comparison request recorded the facts of
    an earlier drafting message). Earlier messages are context; one of them is used only
    when the current request explicitly asks to take up its observations."""
    recorded = {fact["source_id"] for fact in state.get("project_facts", [])}
    users = [m for m in state.get("messages", []) if m.get("role") == "user"]
    if not users:
        return None
    current = users[-1]
    if current.get("id") not in recorded and PROJECT_OBSERVATION.search(current.get("content", "")):
        return current["id"]
    if EARLIER_OBSERVATIONS.search(current.get("content", "")):
        for message in reversed(users[-8:-1]):
            if message.get("id") not in recorded and PROJECT_OBSERVATION.search(message.get("content", "")):
                return message["id"]
    return None


def save_project_facts(state, facts):
    stored = state.setdefault("project_facts", [])
    added = []
    pending = []
    for fact in facts:
        row = fact.model_dump()
        quote = row["exact_quote"].strip()
        if row["source_type"] == "consultant_message":
            source = next((m for m in state.get("messages", []) if m.get("id") == row["source_id"]
                and m.get("role") == "user"), None)
            if source is None or quote not in source["content"]:
                raise ValueError("Fait projet absent du message exact du consultant.")
            row["source_version"] = "message:" + row["source_id"]
        else:
            source = state.get("articles", {}).get(row["source_id"])
            if not source or source.get("fulltext", {}).get("source_kind") != "user_uploaded_document" or \
                    quote not in source["fulltext"].get("text", ""):
                raise ValueError("Fait projet absent du document projet importé.")
            row["source_version"] = source.get("source_version", "")
        if row.get("value") and row["value"] not in quote:
            raise ValueError("Valeur projet absente de la citation exacte.")
        if row.get("unit") and row["unit"] not in quote:
            raise ValueError("Unité projet absente de la citation exacte.")
        key = (row["category"], row["statement"], row["source_type"], row["source_id"], quote)
        old = next((item for item in stored if (item["category"], item["statement"],
            item["source_type"], item["source_id"], item["exact_quote"]) == key), None)
        if old:
            added.append(old["id"])
            continue
        row["id"] = "project_fact_" + uuid.uuid4().hex
        pending.append(row)
        added.append(row["id"])
    stored.extend(pending)
    if pending and state.get("argument_blueprint"):
        state["argument_blueprint"]["stale"] = True
    return {"saved": True, "project_fact_ids": added, "total": len(stored),
        "guidance": "Ces faits proviennent du projet, jamais des publications ; une mesure projet ne devient pas une mesure publiée."}


def active_articles(state, registry, *, include_current=True):
    ids = [*(state.get("current_turn_article_ids", []) if include_current else []),
        *state.get("evaluation", {}).get("recommended_article_ids", []),
        *[aid for aid, article in registry.items() if article.selected],
        *[aid for aid, article in registry.items() if article.assessment.get("decision") == "retain"]]
    # Works put on hold by the consultant wait unless named in this message.
    held = set(state.get("held_article_ids", [])) - set(state.get("current_turn_article_ids", []) if include_current else [])
    return list(dict.fromkeys(aid for aid in ids if aid in registry and aid not in held and
        registry[aid].selection_override is not False and registry[aid].analysis))


def claim_entries(analysis):
    # Interleave categories so bounded windows retain results and limits as well
    # as methods. The model's statements/conditions/IDs are never shortened.
    fields = [(f, value) for f, value in analysis.items() if isinstance(value, list) and
        value and isinstance(value[0], dict) and "statement" in value[0] and "evidence_ids" in value[0]]
    core = {"method", "technical_mechanism", "results", "limitations"}
    for index in range(2):
        for field, claims in fields:
            if field in core and index < len(claims):
                yield field, claims[index]
    for index in range(max((len(v) for _, v in fields), default=0)):
        for field, claims in fields:
            if index < len(claims) and not (field in core and index < 2):
                yield field, claims[index]


def packet_claims(paper):
    if "claim_order" not in paper:
        yield from claim_entries(paper["analysis"])
        return
    offsets = {}
    for field in paper["claim_order"]:
        index = offsets.get(field, 0)
        yield field, paper["analysis"][field][index]
        offsets[field] = index+1


def article_reading_status(state, article):
    task = next((t for t in state.get("scientific_reading", {}).get("tasks", []) if t["article_id"] == article.id), {})
    basis = article.analysis_basis or article.analysis.get("analysis_basis") or "metadata"
    return {"basis": basis, "coverage_sufficient": bool(task.get("coverage_sufficient")),
        "partial": not bool(task.get("coverage_sufficient")),
        "remaining_passages": task.get("remaining_passages"),
        "text_unavailable": not bool(article.fulltext.get("text")),
        "status": task.get("status", "legacy_targeted_analysis"),
        "field_status": task.get("field_status", {}),
        "scope": "targeted_scientific_sections; never an exhaustive whole-document assertion"}


def comparison_source_signature(registry, ids):
    data = [{"id": aid, "source_version": registry[aid].source_version,
        "fingerprint": snapshot(registry[aid])["fingerprint"], "analysis": registry[aid].analysis}
        for aid in sorted(set(ids)) if aid in registry]
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def relation_current(toolkit, relation):
    if relation.get("stale"):
        return False
    ids = relation["article_ids"]
    if any(aid not in toolkit.registry for aid in ids):
        return False
    proofs = [toolkit.evidence.get(eid) for eid in relation["evidence_ids"]]
    if not proofs or any(not e or not exact_source_valid(e, toolkit.snapshots) for e in proofs):
        return False
    signature = relation.get("source_signature")
    if signature:
        return signature == comparison_source_signature(toolkit.registry, ids)
    return all(e["source_fingerprint"] == snapshot(toolkit.registry[e["article_id"]])["fingerprint"] for e in proofs)


def refresh_relations(toolkit):
    for row in toolkit.state.get("scientific_relations", []):
        if not relation_current(toolkit, row):
            row["stale"] = True
            row["stale_reason"] = "Source, scientific claims or exact evidence changed; comparison must be reviewed."


def comparison_batches(state, registry):
    from .scientific_reading import comparison_ready
    ledger = state.get("scientific_reading", {})
    tasks = {t["article_id"]: t for t in ledger.get("tasks", [])}
    # Comparable once core dimensions are documented (partial, flagged as such),
    # not only after an exhaustive traversal that a bounded turn rarely reaches.
    current = set(ledger.get("active_article_ids") or []) | set(state.get("current_turn_article_ids", []))
    ids = [aid for aid in active_articles(state, registry) if registry[aid].verified and
        (not current or aid in current) and (not tasks.get(aid) or comparison_ready(tasks[aid]))]
    if len(ids) < 2:
        return []
    # Windows bound model context, never the full selection or durable corpus.
    return [ids[offset:offset+4] for offset in range(0, len(ids), 3)
        if len(ids[offset:offset+4]) >= 2]


def argument_packet(state, toolkit, ids=None, *, claim_offsets=None):
    ids = active_articles(state, toolkit.registry) if ids is None else ids
    ids = list(dict.fromkeys(aid for aid in ids if aid in toolkit.registry and
        toolkit.registry[aid].selection_override is not False))
    claim_offsets = claim_offsets or {}
    papers = []
    for aid in ids:
        article = toolkit.registry[aid]
        projection = analysis_projection(article.analysis, evidence=toolkit.evidence)
        all_claims = list(claim_entries(projection))
        start = max(0, min(claim_offsets.get(aid, 0), len(all_claims)))
        analysis = {k: v for k, v in projection.items() if not isinstance(v, list) or not v or not isinstance(v[0], dict)}
        proof_ids, order, unavailable = [], [], 0
        for field, claim in all_claims[start:]:
            if not all(eid in toolkit.evidence and eid in toolkit.shown_evidence and
                    exact_source_valid(toolkit.evidence[eid], toolkit.snapshots) for eid in claim["evidence_ids"]):
                unavailable += 1
                continue
            analysis.setdefault(field, []).append(copy.deepcopy(claim))
            order.append(field)
            proof_ids.extend(claim["evidence_ids"])
        proofs = []
        for eid in dict.fromkeys(proof_ids):
            # Omit null metadata only; exact quotes, known provenance and table
            # conditions stay intact. Original receipts remain in the source store.
            proofs.append({k: v for k, v in evidence_projection(toolkit.evidence[eid]).items() if v is not None and v != {}})
        if not proofs:
            anchor = next((e for e in article.analysis.get("evidence", []) if e["evidence_id"] in toolkit.shown_evidence and exact_source_valid(e, toolkit.snapshots)), None)
            if anchor:
                proofs.append({k: v for k, v in evidence_projection(anchor).items() if v is not None and v != {}})
        papers.append({"id": aid, "title": article.title, "year": article.year, "doi": article.doi,
            "source_version": article.source_version, "scientific_families": article.scientific_families,
            "reading_status": article_reading_status(state, article), "analysis": analysis,
            "analysis_available": {k: len(v) for k, v in projection.items() if isinstance(v, list) and v and isinstance(v[0], dict)},
            "proofs": proofs, "claim_order": order, "claim_offset": start,
            "claim_total": len(all_claims), "unavailable_claims": unavailable})
    all_active = active_articles(state, toolkit.registry)
    packet = {"scientific_question": state.get("scientific_scope", {}).get("scientific_question") or
            state.get("research_state", {}).get("scientific_question", ""),
        "papers": papers,
        "active_paper_index": [{"id": aid, "title": toolkit.registry[aid].title[:180],
            "axes": toolkit.registry[aid].scientific_families,
            "analysis_basis": toolkit.registry[aid].analysis_basis} for aid in all_active],
        "active_paper_total": len(all_active), "window_partial": len(all_active) > len(papers),
        "relations": [copy.deepcopy(row) for row in state.get("scientific_relations", []) if
            set(row["article_ids"]) <= set(ids) and relation_current(toolkit, row)],
        "research_gaps": [copy.deepcopy(row) for row in state.get("research_gaps", []) if set(row["article_ids"]) <= set(ids)],
        "project_facts": [{**fact, "source_current": project_fact_valid(state, toolkit.registry, fact)}
            for fact in project_facts_view(state)],
        "existing_blueprint": {"id": state.get("argument_blueprint", {}).get("id"),
            "axes": [{"axis": axis["axis"], "article_ids": list(dict.fromkeys(
                aid for step in axis["steps"] for aid in step["article_ids"]))}
                for axis in state.get("argument_blueprint", {}).get("axes", [])]},
        "outline_sections": [{"id": sec["id"], "title": sec["title"], "article_ids": sec["article_ids"]}
            for sec in state.get("outline", {}).get("sections", [])],
        "guidance": "Comparer les affirmations et leurs conditions exactes, pas une liste de titres. Distinguer source_statement, inference et comparative_inference, les incertitudes, les textes intégraux et les abstracts. Aucune supériorité entre protocoles incompatibles. Les faits projet restent distincts des publications ; aucun verrou CIR n’est présumé."}
    if state.get("mode") == "diagnostic_linked":
        from .diagnostic_link import comparison_project_packet
        packet["project_context"] = comparison_project_packet(state)
    return packet


# Exact-proof locators for comparison/blueprint windows: the quotation stays in the
# evidence store (already validated against each saved claim, and re-checked when a
# relation is saved); the window carries whole claims and where their proofs are.
PROOF_REFERENCE_KEYS = ("evidence_id", "article_id", "section", "page_number", "printed_page", "basis", "table_structure_status")


def proof_reference(evidence):
    row = {k: evidence.get(k) for k in PROOF_REFERENCE_KEYS if evidence.get(k) not in (None, "", {})}
    if isinstance(evidence.get("table"), dict):
        row["table"] = {k: evidence["table"].get(k) for k in ("number", "caption") if evidence["table"].get(k)}
    row["exact_quote_stored"] = True
    return row


def bounded_argument_packet(packet, limit=10000, *, proof_mode="exact"):
    """Balanced, lossless claim windows with stable resumable per-paper offsets.
    proof_mode="reference" transmits proof locators instead of full quotations."""
    original = copy.deepcopy(packet)
    size = lambda obj: len(json.dumps(obj, ensure_ascii=False))
    result = {k: v for k, v in original.items() if k != "papers"}
    result.update(papers=[], next_claim_offsets={}, omitted_claims={}, transmission_partial=False,
        omitted_article_ids=[], deferred_information={}, other_information_partial=False, blocked_by_context=False)
    result["reading_scope"] = "Targeted scientific sections; never an exhaustive whole-document assertion."
    if original.get("project_context"):
        # Keep the project side inside the existing window, reserving room for
        # scientific papers. The large full project_facts list is recoverable.
        context = copy.deepcopy(original["project_context"])
        while size(context) > max(1000, limit*.30) and context.get("observations"):
            context["observations"].pop()
            context["partial"] = True
        if size(context) > max(1000, limit*.30):
            context.pop("missing_information", None)
            context.pop("main_objective", None)
            context["active_locks"] = [{"id": x} for x in context["active_lock_ids"]]
        result["project_context"] = context
        shown = {fid for row in context.get("observations", []) for fid in row["project_fact_ids"]}
        result["project_facts"] = list({fact["id"]: {k: fact[k] for k in ("id", "source_type", "source_fact_id", "scope", "lock_ids")}
            for fact in original.get("project_facts", []) if fact["id"] in shown}.values())
        result["project_facts"].extend(fact for fact in original.get("project_facts", []) if fact.get("source_type") != "diagnostic_snapshot")
        result["project_facts_total"] = len(original.get("project_facts", []))
        if len(result["project_facts"]) < result["project_facts_total"]:
            result["deferred_information"]["project_facts"] = result["project_facts_total"]-len(result["project_facts"])
    if proof_mode == "reference":
        # Counted before the claims are fitted, so the window limit stays exact.
        result["proof_mode"] = ("references : citations exactes conservées et déjà validées pour chaque affirmation ; "
            "citer les evidence_ids, les chiffres sont revérifiés à l'enregistrement")
    # Whole optional records are deferred, never clipped into misleading claims.
    for key in ("existing_blueprint", "outline_sections", "active_paper_index", "project_facts", "research_gaps", "relations"):
        if key == "project_facts" and original.get("project_context"):
            while size(result) > limit*.20+size(result["project_context"]):
                old = next((f for f in reversed(result["project_facts"]) if f.get("source_type") != "diagnostic_snapshot"), None)
                if old is None:
                    break
                result["project_facts"].remove(old)
            result["deferred_information"]["project_facts"] = result["project_facts_total"]-len(result["project_facts"])
            if not result["deferred_information"]["project_facts"]:
                result["deferred_information"].pop("project_facts")
            continue
        if size(result) <= limit*.20 + (size(result["project_context"]) if original.get("project_context") else 0):
            break
        value = result.get(key)
        if value:
            result["deferred_information"][key] = len(value) if isinstance(value, list) else 1
            result[key] = [] if isinstance(value, list) else {}
    streams, proofs, taken = {}, {}, {}
    def add_proof(paper, evidence):
        if proof_mode == "reference":
            paper["proofs"].append(proof_reference(evidence))
            return
        source_keys = {"source_url", "source_version", "document_version_id", "document_sha256", "source_identifiers", "version_status"}
        source = {k: v for k, v in evidence.items() if k in source_keys}
        key = "source_"+hashlib.sha256(json.dumps(source, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
        paper.setdefault("sources", {})[key] = source
        paper["proofs"].append({**{k: v for k, v in evidence.items() if k not in source_keys}, "source_ref": key})
    # Unseen works get a window before works already covered in an earlier one.
    papers = sorted(original["papers"], key=lambda p: (p["claim_offset"] >= p["claim_total"], p["claim_offset"] > 0))
    for paper in papers:
        aid = paper["id"]
        result["next_claim_offsets"][aid] = paper["claim_offset"]
        result["omitted_claims"][aid] = max(0, paper["claim_total"]-paper["claim_offset"])
        header = {k: v for k, v in paper.items() if k not in {"analysis", "proofs", "claim_order"}}
        header["analysis"] = {k: v for k, v in paper["analysis"].items() if not isinstance(v, list)}
        header["proofs"] = []
        header["sources"] = {}
        if paper["proofs"] and proof_mode == "exact":
            add_proof(header, paper["proofs"][0])
        # Detailed field status stays recoverable in the reading ledger.
        header["reading_status"] = {k: v for k, v in header["reading_status"].items() if k not in {"field_status", "scope"}}
        result["papers"].append(header)
        if size(result) > limit*.75 and len(result["papers"]) > 1:
            result["papers"].pop()
            result["omitted_article_ids"].append(aid)
            continue
        streams[aid] = list(packet_claims(paper))
        proofs[aid] = {e["evidence_id"]: e for e in paper["proofs"]}
        taken[aid] = 0
    if not result["omitted_article_ids"] and len(result["papers"]) >= result.get("active_paper_total", 0):
        result["active_paper_index"] = []
    blocked = set()
    while True:
        progress = False
        for paper in result["papers"]:
            aid = paper["id"]
            if aid in blocked or taken[aid] >= len(streams[aid]):
                continue
            field, claim = streams[aid][taken[aid]]
            old_proofs = list(paper["proofs"])
            old_sources = dict(paper["sources"])
            previous_offset = result["next_claim_offsets"][aid]
            previous_omitted = result["omitted_claims"][aid]
            paper["analysis"].setdefault(field, []).append(claim)
            present = {e["evidence_id"] for e in old_proofs}
            for eid in claim["evidence_ids"]:
                if eid not in present:
                    add_proof(paper, proofs[aid][eid])
                    present.add(eid)
            result["next_claim_offsets"][aid] += 1
            result["omitted_claims"][aid] = max(0, previous_omitted-1)
            if size(result) > limit:
                paper["analysis"][field].pop()
                if not paper["analysis"][field]:
                    paper["analysis"].pop(field)
                paper["proofs"] = old_proofs
                paper["sources"] = old_sources
                result["next_claim_offsets"][aid] = previous_offset
                result["omitted_claims"][aid] = previous_omitted
                blocked.add(aid)
                continue
            taken[aid] += 1
            progress = True
        if not progress:
            break
    result["transmission_partial"] = any(result["omitted_claims"].values())
    result["other_information_partial"] = bool(result["deferred_information"])
    result["window_partial"] = original.get("window_partial", False) or result["transmission_partial"]
    while size(result) > limit and result["papers"]:
        paper = result["papers"].pop()
        aid = paper["id"]
        source = next(p for p in original["papers"] if p["id"] == aid)
        result["omitted_article_ids"].append(aid)
        result["next_claim_offsets"][aid] = source["claim_offset"]
        result["omitted_claims"][aid] = source["claim_total"]-source["claim_offset"]
        result["transmission_partial"] = True
    result["blocked_by_context"] = bool(result["transmission_partial"] and not any(taken.values()))
    if size(result) > limit:
        # Even the index can exceed a tiny caller budget. Return a recoverable
        # refusal, never silently clip a scientific claim or exact quotation.
        result = {"papers": [], "transmission_partial": True, "blocked_by_context": True,
            "active_paper_total": original.get("active_paper_total", len(original["papers"])),
            "next_claim_offsets": {}, "omitted_claims": {},
            "guidance": "Scientific packet cannot fit: request a smaller article batch. Durable claims and exact evidence are unchanged."}
        if original.get("project_context"):
            result["project_context"] = {"diagnostic_id": original["project_context"]["diagnostic_id"],
                "active_lock_ids": original["project_context"]["active_lock_ids"], "partial": True,
                "retrieval": original["project_context"]["retrieval"]}
    return result


def source_signature(state, registry):
    # A conversational focus is transient and must never invalidate an already
    # saved blueprint or alter its durable source signature.
    ids = active_articles(state, registry, include_current=False)
    payload = {"articles": [{"id": aid, "source_version": registry[aid].source_version,
        "analysis": registry[aid].analysis, "source_fingerprint": snapshot(registry[aid])["fingerprint"]} for aid in ids],
        "relations": state.get("scientific_relations", []), "gaps": state.get("research_gaps", []),
        "project_facts": project_facts_view(state),
        "project_source_versions": [{"source_id": fact["source_id"],
            "current_version": registry[fact["source_id"]].source_version if fact["source_type"] == "uploaded_project_document"
                and fact["source_id"] in registry else fact["source_version"]}
            for fact in project_facts_view(state)],
        # Draft saves increment the outline version but do not change its plan.
        "outline_plan_revision": state.get("outline", {}).get("plan_revision"),
        "scientific_scope": state.get("scientific_scope", {})}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def project_fact_valid(state, registry, fact):
    if fact["source_type"] == "diagnostic_snapshot":
        return any(row == fact for row in project_facts_view(state, {fact.get("source_fact_id")}))
    if fact["source_type"] == "consultant_message":
        return any(message.get("id") == fact["source_id"] and message.get("role") == "user" and
            fact["exact_quote"] in message.get("content", "") for message in state.get("messages", []))
    source = registry.get(fact["source_id"])
    return bool(source and source.source_version == fact["source_version"] and
        source.fulltext.get("source_kind") == "user_uploaded_document" and
        fact["exact_quote"] in source.fulltext.get("text", ""))


def blueprint_status(state, registry):
    blueprint = state.get("argument_blueprint", {})
    active = set(active_articles(state, registry))
    covered = {aid for axis in blueprint.get("axes", []) for step in axis["steps"] for aid in step["article_ids"]}
    return {"exists": bool(blueprint), "version": blueprint.get("version"),
        "stale": bool(blueprint and (blueprint.get("stale") or blueprint.get("source_signature") != source_signature(state, registry)
            or any(not project_fact_valid(state, registry, fact) for fact in project_facts_view(state)))),
        "complete": bool(blueprint and active and active <= covered),
        "uncovered_article_ids": sorted(active-covered),
        "axes": [{"axis": row["axis"], "steps": len(row["steps"])} for row in blueprint.get("axes", [])]}


def save_blueprint(state, toolkit, args):
    if not args.axes:
        raise ValueError("Le blueprint scientifique exige au moins un axe argumentatif.")
    active = set(active_articles(state, toolkit.registry))
    relations = {row["id"]: row for row in state.get("scientific_relations", []) if relation_current(toolkit, row)}
    project = {row["id"]: row for row in project_facts_view(state)}
    sections = {row["id"] for row in state.get("outline", {}).get("sections", [])}
    saved = args.model_dump()
    saved.pop("action", None)
    saved.pop("merge", None)
    saved.pop("claim_offsets", None)
    for axis in saved["axes"]:
        if axis["section_id"] and axis["section_id"] not in sections:
            raise ValueError("Section du blueprint absente du plan actuel.")
        if not axis["steps"]:
            raise ValueError("Chaque axe exige une progression scientifique étayée.")
        for step in axis["steps"]:
            aids, eids = set(step["article_ids"]), step["evidence_ids"]
            if not aids <= active:
                raise ValueError("Le blueprint cite un article non retenu ou retiré.")
            if not eids:
                raise ValueError("Étape argumentative sans preuve scientifique lue.")
            for eid in eids:
                evidence = toolkit.evidence.get(eid)
                if (not evidence or eid not in toolkit.shown_evidence or evidence["article_id"] not in aids
                        or not exact_source_valid(evidence, toolkit.snapshots)):
                    raise ValueError("Preuve du blueprint inconnue, non lue ou de version invalide.")
            for rid in step["relation_ids"]:
                relation = relations.get(rid)
                if not relation or not set(relation["article_ids"]) <= aids or \
                        not set(relation["evidence_ids"]) <= set(eids):
                    raise ValueError("Comparaison du blueprint non enregistrée ou insuffisamment étayée.")
            if any(fid not in project for fid in step["project_fact_ids"]):
                raise ValueError("Fait projet inconnu dans le blueprint.")
            if any(not project_fact_valid(state, toolkit.registry, project[fid]) for fid in step["project_fact_ids"]):
                raise ValueError("La source d'un fait projet a changé ; revérifier la citation exacte.")
            if step["kind"] == "comparison" and not step["relation_ids"]:
                raise ValueError("Une comparaison doit reprendre une relation scientifique enregistrée.")
            if step["kind"] == "project_confrontation" and (not eids or not step["project_fact_ids"]):
                raise ValueError("La confrontation exige une preuve publiée et un fait projet distinct.")
            if step["kind"] == "project_confrontation" and any(project[fid].get("scope") == "project_context_not_linked_to_lock"
                    for fid in step["project_fact_ids"]) and not step["uncertainty"].strip():
                raise ValueError("Un fait projet général n'est pas attribué au verrou actif : expliciter cette incertitude.")
            if step["kind"] == "partial_resolution" and not step["relation_ids"]:
                raise ValueError("Une résolution partielle doit s'appuyer sur une relation enregistrée.")
    old = state.get("argument_blueprint")
    if args.merge and old and not blueprint_status(state, toolkit.registry)["stale"]:
        merged = copy.deepcopy(old)
        for axis in saved["axes"]:
            existing = next((item for item in merged["axes"] if item["axis"].casefold() == axis["axis"].casefold()), None)
            if existing is None:
                merged["axes"].append(axis)
            else:
                keys = {(step["kind"], step["statement"], tuple(step["article_ids"])) for step in existing["steps"]}
                existing["steps"].extend(step for step in axis["steps"] if
                    (step["kind"], step["statement"], tuple(step["article_ids"])) not in keys)
                existing["transition"] = axis["transition"] or existing["transition"]
        merged["remaining_questions"] = list(dict.fromkeys([*old["remaining_questions"], *saved["remaining_questions"]]))
        if saved["narrative_logic"]:
            merged["narrative_logic"] = "\n".join(dict.fromkeys([old["narrative_logic"], saved["narrative_logic"]]))
        saved = {key: merged[key] for key in ("global_question", "narrative_logic", "axes", "remaining_questions")}
    if old:
        state.setdefault("argument_blueprint_versions", []).append(copy.deepcopy(old))
    saved.update(id="blueprint_" + uuid.uuid4().hex, version=(old or {}).get("version", 0)+1,
        source_signature=source_signature(state, toolkit.registry), stale=False,
        origin="conversation_model_evidence_checked", cir_eligibility="not_established")
    state["argument_blueprint"] = saved
    return {"saved": True, "id": saved["id"], "version": saved["version"],
        "axes": len(saved["axes"]), "status": blueprint_status(state, toolkit.registry)}


COMPARISON_DIMENSIONS = {"methods": ("method", "technical_mechanism"),
    "protocol": ("experimental_setup", "validation_protocol", "datasets_materials"),
    "results": ("results", "metrics"), "limits": ("limitations", "conditions_of_validity")}


def comparable_dimensions(toolkit, ids):
    """Dimension -> works documenting it with valid exact proofs (two or more works only).
    Empty: no relation can be demonstrated between these works from their cards."""
    from .writing_context import usable_proofs
    from .context import analysis_projection
    documented = {}
    for aid in ids:
        article = toolkit.registry[aid]
        proofs = usable_proofs(toolkit, article, {})
        view = analysis_projection(article.analysis, evidence=toolkit.evidence)
        for name, fields in COMPARISON_DIMENSIONS.items():
            if any(isinstance(c, dict) and c.get("evidence_ids") and all(e in proofs for e in c["evidence_ids"])
                    for field in fields for c in (view.get(field) or [])):
                documented.setdefault(name, []).append(aid)
    return {name: members for name, members in documented.items() if len(members) >= 2}
