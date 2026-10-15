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
from .evidence import exact_source_valid


def project_observation_source(state):
    recorded = {fact["source_id"] for fact in state.get("project_facts", [])}
    for message in reversed(state.get("messages", [])[-8:]):
        if (message.get("role") == "user" and message.get("id") not in recorded and
                re.search(r"\b(?:notre|nos|mon|mes)\s+(?:projet|prototype|essai|expérience|résultat|mesure|donnée|méthode)|\bdans\s+(?:notre|mon)\s+projet", message.get("content", ""), re.I)):
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
    return list(dict.fromkeys(aid for aid in ids if aid in registry and
        registry[aid].selection_override is not False and registry[aid].analysis))


def comparison_batches(state, registry):
    ids = active_articles(state, registry)
    if len(ids) < 2:
        return []
    # Windows bound model context, never the full selection or durable corpus.
    return [ids[offset:offset+4] for offset in range(0, len(ids), 3)
        if len(ids[offset:offset+4]) >= 2]


def argument_packet(state, toolkit, ids=None):
    ids = ids or active_articles(state, toolkit.registry)
    ids = [aid for aid in ids if aid in toolkit.registry and
        toolkit.registry[aid].selection_override is not False]
    papers = []
    for aid in ids[:4]:
        article = toolkit.registry[aid]
        projection = analysis_projection(article.analysis, claims=1, statement_chars=260)
        claims = {key: value for key, value in projection.items() if key in {"problem", "method", "technical_mechanism",
            "equations", "parameters", "experimental_setup", "validation_protocol", "results", "tables_figures",
            "contribution", "limitations", "conditions_of_validity", "analysis_basis", "analysis_limit"}}
        proof_fields = ("method", "results", "limitations", "technical_mechanism", "experimental_setup", "equations", "parameters")
        proof_ids = list(dict.fromkeys(eid for field in proof_fields for claim in claims.get(field, [])
            if isinstance(claim, dict) for eid in claim.get("evidence_ids", [])))
        proofs = []
        for eid in proof_ids[:3]:
            if eid in toolkit.evidence and exact_source_valid(toolkit.evidence[eid], toolkit.snapshots):
                proof = evidence_projection(toolkit.evidence[eid])
                proof["quote_truncated"] = len(proof["exact_quote"]) > 750
                proof["exact_quote"] = proof["exact_quote"][:750]
                proofs.append(proof)
        papers.append({"id": aid, "title": article.title, "year": article.year, "doi": article.doi,
            "source_version": article.source_version, "scientific_families": article.scientific_families,
            "analysis": claims, "analysis_available": {key: len(value) for key, value in article.analysis.items()
                if isinstance(value, list) and value and key != "evidence"}, "proofs": proofs})
    all_active = active_articles(state, toolkit.registry)
    return {"scientific_question": state.get("scientific_scope", {}).get("scientific_question") or
            state.get("research_state", {}).get("scientific_question", ""),
        "papers": papers,
        "active_paper_index": [{"id": aid, "title": toolkit.registry[aid].title[:180],
            "axes": toolkit.registry[aid].scientific_families,
            "analysis_basis": toolkit.registry[aid].analysis_basis} for aid in all_active[:30]],
        "active_paper_total": len(all_active),
        "window_partial": len(all_active) > len(papers),
        "relations": [{"id": row["id"], "article_ids": row["article_ids"], "kind": row["kind"],
            "statement": row["statement"][:500], "conditions": row["conditions"][:500],
            "evidence_ids": row["evidence_ids"], "origin": row["origin"],
            "comparable_protocols": row.get("comparable_protocols"), "uncertainties": row.get("uncertainties", [])[:3],
            "experimental_results": row.get("experimental_results", [])[:2]}
            for row in state.get("scientific_relations", []) if set(row["article_ids"]) <= set(ids)],
        "research_gaps": [{"article_ids": row["article_ids"], "gaps": [
            {**gap, "statement": gap["statement"][:450], "scope": gap["scope"][:350]}
            for gap in row["gaps"][:4]]} for row in state.get("research_gaps", []) if
            set(row["article_ids"]) <= set(ids)],
        "project_facts": [{**fact, "source_current": project_fact_valid(state, toolkit.registry, fact)}
            for fact in state.get("project_facts", [])[-30:]],
        "existing_blueprint": {"id": state.get("argument_blueprint", {}).get("id"),
            "axes": [{"axis": axis["axis"], "article_ids": list(dict.fromkeys(
                aid for step in axis["steps"] for aid in step["article_ids"]))}
                for axis in state.get("argument_blueprint", {}).get("axes", [])]},
        "outline_sections": [{"id": s["id"], "title": s["title"], "article_ids": s["article_ids"]}
            for s in state.get("outline", {}).get("sections", [])],
        "guidance": "Comparer mécanismes, protocoles, résultats et conditions. Une année seule ne prouve ni progrès ni supériorité. Les faits projet sont distincts des travaux publiés. Aucune difficulté ne devient automatiquement un verrou CIR."}


def bounded_argument_packet(packet, limit=10000):
    """Fit the conversational window; retain IDs and availability in durable storage."""
    packet = copy.deepcopy(packet)
    packet["transmission_partial"] = False
    if len(json.dumps(packet, ensure_ascii=False)) <= limit:
        return packet
    packet["transmission_partial"] = True
    packet["relations_total"] = len(packet["relations"])
    packet["research_gaps_total"] = len(packet["research_gaps"])
    packet["relations"] = packet["relations"][-8:]
    packet["research_gaps"] = packet["research_gaps"][-8:]
    packet["project_facts"] = packet["project_facts"][-8:]
    for fact in packet["project_facts"]:
        fact["exact_quote"] = fact["exact_quote"][:400]
    for paper in packet["papers"]:
        for proof in paper["proofs"]:
            proof["quote_truncated"] = proof.get("quote_truncated", False) or len(proof["exact_quote"]) > 400
            proof["exact_quote"] = proof["exact_quote"][:400]
        for value in paper["analysis"].values():
            if isinstance(value, list):
                for claim in value:
                    if isinstance(claim, dict):
                        claim["statement"] = claim["statement"][:180]
    for field in ("conditions_of_validity", "parameters", "equations", "tables_figures", "validation_protocol",
            "contribution", "experimental_setup", "technical_mechanism", "problem"):
        if len(json.dumps(packet, ensure_ascii=False)) <= limit:
            break
        for paper in packet["papers"]:
            paper["analysis"].pop(field, None)
    while len(json.dumps(packet, ensure_ascii=False)) > limit and any(len(p["proofs"]) > 1 for p in packet["papers"]):
        for paper in packet["papers"]:
            if len(paper["proofs"]) > 1:
                paper["proofs"].pop()
    while len(json.dumps(packet, ensure_ascii=False)) > limit and packet["active_paper_index"]:
        packet["active_paper_index"].pop()
    for key in ("research_gaps", "relations", "project_facts"):
        while len(json.dumps(packet, ensure_ascii=False)) > limit and len(packet[key]) > 1:
            packet[key].pop(0)
    if len(json.dumps(packet, ensure_ascii=False)) > limit:
        for paper in packet["papers"]:
            for proof in paper["proofs"]:
                proof["quote_truncated"] = True
                proof["exact_quote"] = proof["exact_quote"][:180]
    return packet


def source_signature(state, registry):
    # A conversational focus is transient and must never invalidate an already
    # saved blueprint or alter its durable source signature.
    ids = active_articles(state, registry, include_current=False)
    payload = {"articles": [{"id": aid, "source_version": registry[aid].source_version,
        "analysis": registry[aid].analysis} for aid in ids],
        "relations": state.get("scientific_relations", []), "gaps": state.get("research_gaps", []),
        "project_facts": state.get("project_facts", []),
        "project_source_versions": [{"source_id": fact["source_id"],
            "current_version": registry[fact["source_id"]].source_version if fact["source_type"] == "uploaded_project_document"
                and fact["source_id"] in registry else fact["source_version"]}
            for fact in state.get("project_facts", [])],
        # Draft saves increment the outline version but do not change its plan.
        "outline_plan_revision": state.get("outline", {}).get("plan_revision"),
        "scientific_scope": state.get("scientific_scope", {})}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def project_fact_valid(state, registry, fact):
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
            or any(not project_fact_valid(state, registry, fact) for fact in state.get("project_facts", [])))),
        "complete": bool(blueprint and active and active <= covered),
        "uncovered_article_ids": sorted(active-covered),
        "axes": [{"axis": row["axis"], "steps": len(row["steps"])} for row in blueprint.get("axes", [])]}


def save_blueprint(state, toolkit, args):
    if not args.axes:
        raise ValueError("Le blueprint scientifique exige au moins un axe argumentatif.")
    active = set(active_articles(state, toolkit.registry))
    relations = {row["id"]: row for row in state.get("scientific_relations", [])}
    project = {row["id"]: row for row in state.get("project_facts", [])}
    sections = {row["id"] for row in state.get("outline", {}).get("sections", [])}
    saved = args.model_dump()
    saved.pop("action", None)
    saved.pop("merge", None)
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
