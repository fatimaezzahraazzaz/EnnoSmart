"""Read-only adapter for persisted diagnostics. No diagnostic execution or file reads."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timezone

def permitted_project(db, owner, project_id):
    from db.models import User
    from services.project_service import get_project_for_user
    user = db.get(User, owner)
    if user is None:
        raise ValueError("Utilisateur introuvable.")
    return get_project_for_user(db, project_id, user)


def records(value):
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    if isinstance(value, dict):
        return records(value.get("items", value.get("entities", value.get("families", []))))
    return []


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def normalize_records(entities, extractions, synthesis, locks, sections, diagnostic_id, cards=()):
    """Normalize stored evidence without checking files or inventing verification.

    Original records and locators survive. An old E1 is an alias, not a global
    identity: conflicting passages receive stable diagnostic-scoped identifiers.
    """
    entries = [(x, f"entities[{i}]") for i, x in enumerate(entities)]
    entries += [(x, f"extractions[{i}]") for i, x in enumerate(extractions)]
    categories = {"objectif_global": "objective", "objectif_principal": "objective",
        "sous_objectifs": "objective", "objectifs_secondaires": "objective",
        "demarche_detectee": "demarche", "methodes": "method", "methodes_rnd": "method",
        "parametres_contraintes": "parameter", "resultats_metriques": "result",
        "limites": "difficulty", "difficultes": "difficulty", "contexte_technique": "context"}
    for i, card in enumerate(cards):
        kind = categories.get(card.get("key"))
        if kind:
            for j, item in enumerate(records(card.get("items"))):
                entries.append(({**copy.deepcopy(item), "kind": kind,
                    "item_id": f"diagnostic_card_{i}_{j}", "origin": "diagnostic_summary",
                    "needs_review": True, "source_role": "unknown"}, f"diagnostic_cards[{i}].items[{j}]"))
    candidates, aliases = {}, {}

    def proof_key(row):
        quote = row.get("quote") or row.get("excerpt") or row.get("text")
        if not isinstance(quote, str) or not quote.strip() or not any(row.get(k) for k in ("evidence_id", "passage_id", "chunk_id", "rag_chunk_id")):
            return None
        return digest({"original_id": row.get("evidence_id"), "quote": quote,
            "document": row.get("document_id") or row.get("source_path") or row.get("document_name") or row.get("document"),
            "passage": row.get("passage_id") or row.get("chunk_id") or row.get("rag_chunk_id"),
            "page": row.get("page", row.get("page_number")), "start": row.get("char_start"), "end": row.get("char_end")})

    def collect(value, path):
        if isinstance(value, dict):
            key = proof_key(value)
            if key:
                candidate = candidates.setdefault(key, {"row": copy.deepcopy(value), "paths": [], "verified": []})
                candidate["paths"].append(path)
                candidate["verified"].append(value.get("quote_verified"))
                if value.get("evidence_id"):
                    aliases.setdefault(str(value["evidence_id"]), set()).add(key)
            for k, child in value.items():
                collect(child, path+"."+k)
        elif isinstance(value, list):
            for i, child in enumerate(value):
                collect(child, f"{path}[{i}]")

    for item, path in entries:
        collect(item, path)
    collect(synthesis, "project_synthesis")
    collect(cards, "diagnostic_cards")
    for lock in locks:
        collect(lock["source"], f"verrous[{lock['id']}].source_json")
    evidence, keys = {}, {}
    for key, candidate in candidates.items():
        original = candidate["row"]
        alias = str(original.get("evidence_id") or "")
        eid = alias if alias and len(aliases[alias]) == 1 else f"diag_ev_{diagnostic_id}_{key[:24]}"
        keys[key] = eid
        verified = candidate["verified"]
        status = True if all(v is True for v in verified) else False if any(v is False for v in verified) else None
        evidence[eid] = {**original, "evidence_id": eid, "original_evidence_id": alias or None,
            "quote": original.get("quote") or original.get("excerpt") or original.get("text"),
            "quote_verified": status, "verification_status": "verified" if status is True else "unverified" if status is False else "not_established",
            "document_name": original.get("document_name") or original.get("document"),
            "page": original.get("page", original.get("page_number")),
            "chunk_id": original.get("chunk_id") or original.get("rag_chunk_id") or original.get("passage_id"),
            "provenance_paths": list(dict.fromkeys(candidate["paths"]))}

    def proof_ids(value):
        found = []
        if isinstance(value, dict):
            key = proof_key(value)
            if key in keys:
                found.append(keys[key])
            for child in value.values():
                found.extend(proof_ids(child))
        elif isinstance(value, list):
            for child in value:
                found.extend(proof_ids(child))
        return list(dict.fromkeys(found))

    facts = []
    for index, (item, path) in enumerate(entries):
        statement = item.get("statement") or item.get("text")
        if not statement:
            continue
        ids, unresolved = proof_ids(item), []
        for alias in item.get("evidence_ids", []):
            matches = aliases.get(str(alias), set())
            if len(matches) == 1:
                ids.append(keys[next(iter(matches))])
            elif str(alias) in evidence:
                ids.append(str(alias))
            else:
                unresolved.append(str(alias))
        facts.append({"id": str(item.get("entity_id") or item.get("item_id") or f"fact_{index}"),
            "kind": item.get("kind", "context"), "statement": statement,
            "evidence_ids": list(dict.fromkeys(ids)), "unresolved_evidence_ids": unresolved,
            "source_path": path, "source": copy.deepcopy(item)})

    # A section is a reported diagnostic observation, not an extracted and
    # verified experimental result. Preserve its exact paragraphs and source.
    for section, kind in categories.items():
        if not isinstance(sections.get(section), str):
            continue
        for i, paragraph in enumerate(re.split(r"\n\s*\n", sections[section])):
            if paragraph.strip():
                facts.append({"id": f"diagnostic_section_{section}_{i}", "kind": kind, "statement": paragraph,
                    "evidence_ids": [], "source_path": f"canonical_sections.{section}[{i}]",
                    "source": {"text": paragraph, "origin": "diagnostic_summary", "needs_review": True,
                               "temporal_status": "unclear", "source_role": "unknown"}})
    # Old passages remain useful evidence to inspect, without labelling their
    # contents as methods/results, or turning a consultant's Garder into proof.
    for lock in locks:
        for eid in proof_ids(lock["source"]):
            row = evidence[eid]
            if not row.get("excerpt") and not row.get("text"):
                continue
            facts.append({"id": f"diagnostic_passage_{lock['id']}_{digest(eid)[:20]}", "kind": "source_passage",
                "statement": row["quote"], "evidence_ids": [eid], "declared_lock_ids": [lock["id"]],
                "source_path": f"verrous[{lock['id']}].source_json", "source": {
                    "origin": "stored_passage", "source_role": "unknown", "needs_review": row["quote_verified"] is not True}})
    return facts, evidence


def source_snapshot(db, owner, project_id, diagnostic_id=None):
    from sqlalchemy import select
    from db.models import DiagnosticRun, Verrou
    project = permitted_project(db, owner, project_id)
    query = select(DiagnosticRun).where(DiagnosticRun.project_id == project_id)
    if diagnostic_id is not None:
        query = query.where(DiagnosticRun.id == diagnostic_id)
    runs = db.scalars(query.order_by(DiagnosticRun.created_at.desc(), DiagnosticRun.id.desc())).all()
    for run in runs:
        raw = run.raw_result_json if isinstance(run.raw_result_json, dict) else {}
        report = raw.get("report") if isinstance(raw.get("report"), dict) else {}
        snapshot = raw.get("diagnostic_snapshot") or report.get("diagnostic_snapshot") or report.get("diagnostic_v2") or raw.get("diagnostic_v2") or raw
        if not isinstance(snapshot, dict):
            continue
        core = snapshot.get("core") if isinstance(snapshot.get("core"), dict) else {}
        verrou_rows = db.scalars(select(Verrou).where(Verrou.diagnostic_run_id == run.id).order_by(Verrou.id)).all()
        if not verrou_rows or not isinstance(snapshot, dict) or not snapshot:
            continue
        locks = [{"id": str(v.id), "title": v.title, "decision": v.consultant_status,
                  "justification": v.justification, "source": copy.deepcopy(v.source_json or {})} for v in verrou_rows]
        entities = records(snapshot.get("entities") or core.get("entities"))
        extractions = records(snapshot.get("extractions") or core.get("extractions"))
        synthesis = snapshot.get("project_synthesis") or report.get("project_synthesis") or {}
        if not isinstance(synthesis, dict):
            synthesis = {}
        context_sections = {**(snapshot.get("sections_by_key") if isinstance(snapshot.get("sections_by_key"), dict) else {}),
                            **(snapshot.get("canonical_sections") if isinstance(snapshot.get("canonical_sections"), dict) else {})}
        cards = records(snapshot.get("diagnostic_cards"))
        for card in cards:
            if card.get("key") and isinstance(card.get("body"), str):
                context_sections.setdefault(card["key"], card["body"])
        synthesis = copy.deepcopy(synthesis)
        if not synthesis.get("main_objective"):
            synthesis["main_objective"] = context_sections.get("objectif_global") or context_sections.get("objectif_principal") or ""
        if not synthesis.get("objective_points"):
            points = context_sections.get("sous_objectifs") or context_sections.get("objectifs_secondaires") or []
            synthesis["objective_points"] = points if isinstance(points, list) else [points]
        if not synthesis.get("technical_context") and context_sections.get("contexte_technique"):
            synthesis["technical_context"] = context_sections["contexte_technique"]
        if not (entities or extractions or synthesis or context_sections or any(v["source"] for v in locks)):
            continue
        families = records(snapshot.get("lock_families") or report.get("lock_families"))
        # Preserve synthesis-only structured points (older snapshots may omit entities).
        for category in ("methods", "demarches", "parameters", "metrics", "results", "difficulties", "limits", "constraints"):
            for index, point in enumerate(records(synthesis.get(category))):
                extractions.append({**copy.deepcopy(point), "item_id": point.get("item_id") or f"synthesis_{category}_{index}",
                                    "kind": {"difficulties": "difficulty", "limits": "difficulty"}.get(category, category.rstrip("s")),
                                    "statement": point.get("text") or point.get("statement", "")})
        facts, evidence = normalize_records(entities, extractions, synthesis, locks, context_sections, run.id, cards)
        result = {"project": {"id": project.id, "name": project.project_name, "organisme": project.organisme,
                              "year": project.year, "subproject_name": project.subproject_name},
                  "diagnostic_id": run.id, "diagnostic_status": run.status,
                  "diagnostic_date": (run.completed_at or run.created_at).isoformat(),
                  "normalization_version": 2, "locks": locks, "synthesis": copy.deepcopy(synthesis), "facts": facts,
                  "context_sections": context_sections,
                  "families": copy.deepcopy(families), "evidence": evidence,
                  "rnd_approach": copy.deepcopy(snapshot.get("rnd_approach") or {}),
                  "temporal_context": {k: copy.deepcopy(snapshot[k]) for k in
                     ("period", "rnd_start_date", "work_period", "project_frascati") if k in snapshot}}
        result["source_version"] = hashlib.sha256(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
        return result
    raise ValueError("Aucun diagnostic structuré exploitable pour ce projet.")


def list_projects(db, owner):
    from fastapi import HTTPException
    from sqlalchemy import select
    from db.models import Project
    result = []
    for project_id in db.scalars(select(Project.id).order_by(Project.id)).all():
        try:
            snapshot = source_snapshot(db, owner, project_id)
        except (HTTPException, ValueError):
            continue
        result.append({k: snapshot[k] for k in ("project", "diagnostic_id", "diagnostic_status", "diagnostic_date", "source_version")})
    return result


def validate_selection(snapshot, ids, allow_unkept=False):
    ids = list(dict.fromkeys(ids))
    locks = {v["id"]: v for v in snapshot["locks"]}
    if not ids or any(x not in locks for x in ids):
        raise ValueError("Sélectionnez au moins un verrou disponible.")
    kept = [v for v in locks.values() if v["decision"] == "garde"]
    if any(locks[x]["decision"] != "garde" for x in ids) and (kept or not allow_unkept):
        raise ValueError("Sans verrou Garder, confirmer explicitement le choix de verrous non validés.")
    # Display and ordinal order always follow the stable source order.
    return [x for x in locks if x in ids]


def make_link(snapshot, ids, allow_unkept=False):
    selected = validate_selection(snapshot, ids, allow_unkept)
    return {"snapshot": snapshot, "selected_lock_ids": selected, "active_lock_ids": selected[:],
            "revision": 1, "history": [], "source_history": []}


def summary(link):
    source = link["snapshot"]
    return {**{k: source[k] for k in ("project", "diagnostic_id", "source_version", "diagnostic_date", "diagnostic_status")},
            "revision": link["revision"], "selected_lock_ids": link["selected_lock_ids"],
            "active_lock_ids": link["active_lock_ids"], "locks": [
                {k: v[k] for k in ("id", "title", "decision", "justification")} for v in source["locks"]]}


def set_scope(state, ids, origin="authenticated_human"):
    link = state["diagnostic_link"]
    ids = list(dict.fromkeys(ids))
    if not ids or not set(ids) <= set(link["selected_lock_ids"]):
        raise ValueError("Le périmètre actif doit appartenir aux verrous sélectionnés.")
    previous_key = ",".join(sorted(link["active_lock_ids"]))
    next_key = ",".join(sorted(ids))
    work = link.setdefault("scope_work", {})
    if previous_key != next_key:
        work[previous_key] = {k: copy.deepcopy(state.get(k, {})) for k in ("research_state", "scientific_scope")}
        current = work.get(next_key)
        if current:
            state.update(copy.deepcopy(current))
        else:
            titles = [v["title"] for v in link["snapshot"]["locks"] if v["id"] in ids]
            state["research_state"] = {"topic": "; ".join(titles), "objectives": [], "search_axes": []}
            dates = {k: state["scientific_scope"][k] for k in ("cir_year", "start_date", "reference_date")
                     if k in state.get("scientific_scope", {})}
            state["scientific_scope"] = {**dates, "topic": "; ".join(titles), "unconfirmed_assumptions": [], "work_type": "exploration"}
    link["history"].append({"at": datetime.now(timezone.utc).isoformat(), "origin": origin,
                            "previous_active_lock_ids": link["active_lock_ids"][:], "active_lock_ids": ids[:]})
    link["active_lock_ids"] = ids
    link["revision"] += 1
    if state.get("argument_blueprint"):
        state["argument_blueprint"]["stale"] = True


def scope_from_message(state, text):
    if state.get("mode") != "diagnostic_linked":
        return
    # A standalone scope instruction, never an article's ordinal in prose.
    match = re.fullmatch(r"\s*(?:je (?:veux|souhaite) )?(?:commencer|travailler|passer|se concentrer|rechercher)(?: seulement| uniquement)? (?:par|sur) (?:le |les |l['’])?(premier|deuxième|deuxieme|second|troisième|troisieme|tous)(?: verrou(?:x)?)?[.!]?\s*", text, re.I)
    if match:
        selected = state["diagnostic_link"]["selected_lock_ids"]
        word = match[1].lower()
        index = {"premier": 0, "deuxième": 1, "deuxieme": 1, "second": 1, "troisième": 2, "troisieme": 2}.get(word)
        if word == "tous" or index is not None and index < len(selected):
            set_scope(state, selected if word == "tous" else [selected[index]], "consultant_message")


def scoped_records(state):
    """Explicit semantic membership only; co-occurring evidence is not a link."""
    link = state.get("diagnostic_link")
    if not link:
        return []
    source = link["snapshot"]
    active = set(link["active_lock_ids"])
    lock_ids = {v["id"] for v in source["locks"]}
    entity_locks = {str(v["source"].get("entity_id") or (v["source"].get("v2_entity") or {}).get("entity_id")): v["id"]
                    for v in source["locks"] if v["source"].get("entity_id") or (v["source"].get("v2_entity") or {}).get("entity_id")}
    memberships = {}
    for lock in source["locks"]:
        # This is a declared member list on the lock itself, never v2_family.
        for fid in lock["source"].get("member_entity_ids", []):
            memberships.setdefault(str(fid), set()).add(lock["id"])
    declared = {}
    for fact in source["facts"]:
        metadata = fact["source"].get("metadata") or {}
        values = fact.get("declared_lock_ids") or fact["source"].get("related_lock_ids") or metadata.get("related_lock_ids") or fact["source"].get("lock_ids") or metadata.get("lock_ids") or []
        if fact["kind"] == "lock":
            # Source items of a canonical lock may describe a different lock in
            # a shared paragraph. Only the canonical active entity is a lock fact.
            ids = {entity_locks[fact["id"]]} if fact["id"] in entity_locks else set()
            declared[fact["id"]] = (ids, "canonical_lock" if ids else "unassigned_lock", False)
            continue
        ids = {entity_locks.get(str(v), str(v)) for v in values}
        ids.update(memberships.get(fact["id"], set()))
        unresolved = bool(ids-lock_ids)
        declared[fact["id"]] = (ids & lock_ids, "explicit_membership" if ids else "not_recorded", unresolved)
    # Synthesis points can inherit an explicit relation through their original
    # item identifiers. Never inherit from an evidence ID, family or passage.
    for fact in source["facts"]:
        ids, basis, unresolved = declared[fact["id"]]
        if not ids and fact["kind"] != "lock":
            for fid in fact["source"].get("original_item_ids", []) + fact["source"].get("source_item_ids", []):
                parent = declared.get(str(fid))
                if parent and parent[1] != "canonical_lock":
                    ids = ids | parent[0]
            if ids:
                basis = "explicit_original_item"
        scope = ("active_lock" if ids & active else "other_lock") if ids else (
            "unresolved" if unresolved or fact["kind"] == "lock" else "project_context_not_linked_to_lock")
        yield {**fact, "lock_ids": sorted(ids), "scope": scope, "association_basis": basis}


def active_records(state):
    link = state.get("diagnostic_link")
    if not link:
        return [], []
    active = set(link["active_lock_ids"])
    return ([v for v in link["snapshot"]["locks"] if v["id"] in active],
            [f for f in scoped_records(state) if f["scope"] == "active_lock"])


def retrieve(state, kind="facts", offset=0, limit=5, ids=None, scope="active"):
    if state.get("mode") != "diagnostic_linked":
        raise ValueError("Cette conversation est en mode Express.")
    source = state["diagnostic_link"]["snapshot"]
    locks, facts = active_records(state)
    if scope == "all":
        locks, facts = source["locks"], list(scoped_records(state))
    elif scope == "general":
        locks, facts = [], [f for f in scoped_records(state) if f["scope"] == "project_context_not_linked_to_lock"]
    if kind == "locks":
        rows = locks
    elif kind == "facts":
        rows = facts
    elif kind == "evidence":
        allowed = {eid for f in facts for eid in f["evidence_ids"]}
        rows = [v for key, v in source["evidence"].items() if scope == "all" or key in allowed]
    else:
        rows = [{"synthesis": source["synthesis"], "rnd_approach": source["rnd_approach"], "temporal_context": source["temporal_context"]},
                *[{"section": key, "text": value, "scope": "general_project_context"} for key, value in source.get("context_sections", {}).items()]]
    if ids:
        rows = [r for r in rows if str(r.get("id") or r.get("evidence_id")) in ids or
                kind == "evidence" and r.get("original_evidence_id") in ids]
    page = copy.deepcopy(rows[offset:offset+limit])
    return {"source_type": "diagnostic_snapshot", "source_version": source["source_version"],
            "project": copy.deepcopy(source["project"]),
            "scope": scope, "active_lock_ids": state["diagnostic_link"]["active_lock_ids"][:],
            "diagnostic_id": source["diagnostic_id"], "items": page, "total": len(rows),
            "next_offset": offset+len(page) if offset+len(page) < len(rows) else None}


LINKED_GUIDANCE = ("Contexte projet issu d'un snapshot EnnoDiagnostic en lecture seule. Seuls les verrous actifs pilotent les recherches et comparaisons. "
    "Exploiter méthodes, paramètres, résultats et difficultés ; récupérer les détails exacts avec get_project_context (scope=general pour le projet sans attribution, scope=all pour les autres verrous sans les activer). "
    "Une preuve partagée ne relie pas une méthode ou un résultat au verrou ; seuls les faits déclarés pour le verrou ou extraits des passages cités comme ses preuves lui sont rapprochés (lien documentaire indiqué). Les observations générales ne lui sont pas attribuées ; expliciter ce lien manquant. "
    "Les résumés anciens et citations non vérifiées sont des observations rapportées à confirmer, jamais des faits expérimentaux établis. "
    "Séparer faits du projet, littérature citée dans le diagnostic et preuves des publications lues : un identifiant diagnostic n'est jamais un evidence_id scientifique ; il se cite [projet:evidence_id], une publication [paper_…] avec ses [ev_…]. "
    "Respecter source_roles et temporal_status : planned ne signifie pas réalisé, unknown exige vérification. "
    "Comparer conditions et limites, sans conclure à la nouveauté par absence de résultats ni valider automatiquement le CIR. "
    "L'année CIR n'est pas une date de démarrage R&D prouvée : demander cette date si absente ; distinguer publications antérieures et ultérieures. Validation humaine requise.")


# Words too common in diagnostic prose to relate a general fact to a lock.
_GENERIC = {"les", "des", "une", "pour", "dans", "avec", "sur", "par", "aux", "est", "pas", "plus", "sont", "ont", "qui", "que",
    "cette", "ces", "ses", "leur", "leurs", "elle", "ils", "malgre", "afin", "ainsi", "entre", "comme", "sans", "tres", "trop",
    "projet", "methode", "methodes", "approche", "approches", "donnees", "resultats", "resultat", "solution", "permis", "permet",
    "the", "and", "for", "with", "from", "this", "that"}


def _terms(text):
    from .store import tokens
    return {t for t in tokens(text) if len(t) >= 4 and t not in _GENERIC}


def technical_context(state, locks):
    """General project facts (methods, approaches, parameters, results, difficulties)
    ordered by lexical proximity to the active locks. The proximity only orders the
    rows and is reported as such: it never attributes a fact to a lock."""
    lock_terms = {v["id"]: _terms(v["title"] + " " + (v.get("justification") or "")) for v in locks}
    rows = []
    for fact in scoped_records(state):
        if fact["scope"] != "project_context_not_linked_to_lock" or fact["kind"] in {"lock", "objective", "source_passage"}:
            continue
        terms = _terms(fact["statement"])
        near = [lid for lid, words in lock_terms.items() if len(terms & words) >= 2]
        score = max((len(terms & words) for words in lock_terms.values()), default=0)
        rows.append((score, fact, near))
    rows.sort(key=lambda row: -row[0])
    # One row per kind in turn, so results and parameters are not starved by methods.
    kinds = list(dict.fromkeys(fact["kind"] for _, fact, _ in rows))
    groups = {kind: [row for row in rows if row[1]["kind"] == kind] for kind in kinds}
    ordered = [group[i] for i in range(max((len(g) for g in groups.values()), default=0))
        for group in groups.values() if i < len(group)]
    ordered.sort(key=lambda row: row[0] == 0)
    return ordered


def active_packet(state, budget=4200):
    link = state.get("diagnostic_link")
    if not link:
        return None
    source = link["snapshot"]
    locks, facts = active_records(state)
    synthesis = source["synthesis"]
    packet = {"project": source["project"], "diagnostic_id": source["diagnostic_id"],
              "source_version": source["source_version"], "active_lock_ids": link["active_lock_ids"],
              "locks": [], "objectives": [], "facts": [], "facts_total": len(facts),
              "retrieval": "get_project_context(kind, offset, limit, ids) : enregistrements intégraux et preuves ; aucune réanalyse."}
    from collections import Counter
    scopes = Counter(f["scope"] for f in scoped_records(state))
    packet["available_context"] = dict(scopes)
    packet["unverified_evidence_total"] = sum(e.get("quote_verified") is not True for e in source["evidence"].values())

    def fits():
        return len(json.dumps(packet, ensure_ascii=False)) <= budget
    # Counters first, so the optional records below are fitted to the final size.
    packet.update(partial=False, objectives_partial=False, active_locks_total=len(locks))
    for lock in locks:
        packet["locks"].append({k: lock[k] for k in ("id", "title", "decision")})
    # Whole records only: omissions are explicit and recoverable, never clipped quotes.
    if synthesis.get("main_objective"):
        packet["objectives"].append({"main_objective": synthesis["main_objective"]})
        if not fits():
            packet["objectives"].pop()
    for fact in facts:
        # The canonical lock entity restates the lock listed above; it stays retrievable.
        if fact["kind"] == "lock":
            continue
        row = {k: fact[k] for k in ("id", "kind", "statement", "evidence_ids", "scope")}
        metadata = fact["source"].get("metadata") or {}
        row["source_roles"] = {k: copy.deepcopy(fact["source"].get(k, metadata.get(k, "unknown"))) for k in
                               ("source_role", "source_roles", "evidence_source_roles", "temporal_status", "needs_review")}
        packet["facts"].append(row)
        if not fits():
            packet["facts"].pop()
    packet["partial"] = len(packet["facts"])+sum(f["kind"] == "lock" for f in facts) < len(facts)
    # The project's general technical facts make its difficulties concrete for
    # queries and comparisons; they stay labelled as not linked to a lock.
    general = technical_context(state, locks)
    if general:
        packet["project_technical_context"] = []
        packet["technical_context_note"] = ("Faits généraux du projet, non rattachés à un verrou dans le diagnostic. "
            "lexical_overlap_with_locks ordonne seulement leur présentation : ce n'est pas une relation établie.")
        packet["technical_context_total"] = len(general)
        shown = set()
        for score, fact, near in general:
            # Diagnostics may repeat one statement under several entities.
            if len(fact["statement"]) > 400 or fact["statement"] in shown:
                continue
            temporal = fact["source"].get("temporal_status") or (fact["source"].get("metadata") or {}).get("temporal_status")
            row = {"id": fact["id"], "kind": fact["kind"], "statement": fact["statement"],
                **({"lexical_overlap_with_locks": near} if near else {}),
                **({"temporal_status": temporal} if temporal and temporal != "unclear" else {}),
                **({"needs_review": True} if fact["source"].get("needs_review") else {})}
            packet["project_technical_context"].append(row)
            if not fits():
                # Whole records only: a shorter later record may still fit.
                packet["project_technical_context"].pop()
            else:
                shown.add(fact["statement"])
    for key in ("objective_points", "technical_context"):
        if synthesis.get(key):
            packet["objectives"].append({key: synthesis[key]})
            if not fits():
                packet["objectives"].pop()
    for row, lock in zip(packet["locks"], locks):
        if lock.get("justification"):
            row["justification"] = lock["justification"]
            if not fits():
                row.pop("justification")
    if not fits():
        packet["locks"] = [{"id": x["id"]} for x in locks]
        packet["partial"] = True
    packet["objectives_partial"] = len(packet["objectives"]) < sum(bool(synthesis.get(k)) for k in
                                                               ("main_objective", "objective_points", "technical_context"))
    packet["active_locks_total"] = len(locks)
    while not fits() and packet.get("project_technical_context"):
        packet["project_technical_context"].pop()
    while not fits() and packet["facts"]:
        packet["facts"].pop()
        packet["partial"] = True
    while not fits() and packet["objectives"]:
        packet["objectives"].pop()
        packet["objectives_partial"] = True
    while not fits() and packet["locks"]:
        packet["locks"].pop()
        packet["partial"] = True
    while not fits() and packet["active_lock_ids"]:
        packet["active_lock_ids"] = packet["active_lock_ids"][:-1]
        packet["partial"] = True
    if not fits():
        packet["project"] = {"id": source["project"]["id"]}
        packet["partial"] = True
    if general and not packet.get("project_technical_context"):
        packet.pop("project_technical_context", None)
        packet.pop("technical_context_note", None)
    return packet


# Order of a lock's project facts in a bounded dossier: what was tried and observed first.
DOSSIER_KINDS = ("result", "demarche", "method", "parameter", "metric", "difficulty", "objective", "context")


def lock_passages(source, lock):
    """The documentary passages the diagnostic cites as this lock's own evidence."""
    v2 = lock["source"].get("v2_entity") or {}
    ids = lock["source"].get("evidence_ids") or [e.get("evidence_id") for e in v2.get("evidences", [])]
    by_original = {e.get("original_evidence_id"): key for key, e in source["evidence"].items() if e.get("original_evidence_id")}
    rows = []
    for eid in dict.fromkeys(i for i in ids if i):
        key = eid if eid in source["evidence"] else by_original.get(eid)
        evidence = source["evidence"].get(key) if key else None
        if not evidence or not isinstance(evidence.get("quote"), str) or not evidence["quote"].strip():
            continue
        rows.append({"evidence_id": key, "document_name": evidence.get("document_name"),
            "section_title": evidence.get("section_title"), "page": evidence.get("page"),
            "quote": evidence["quote"], "quote_verified": evidence.get("quote_verified")})
    return rows


def _passage_keys(source, evidence_ids):
    keys = set()
    for eid in evidence_ids:
        evidence = source["evidence"].get(eid)
        if evidence and (evidence.get("chunk_id") or evidence.get("passage_id")):
            keys.add((str(evidence.get("document_id") or evidence.get("document_name")),
                str(evidence.get("chunk_id") or evidence.get("passage_id"))))
    return keys


def lock_records(state, lock, records=None):
    """Project facts documented for one lock, by an established link only:
    declared: the diagnostic attributes the fact to this lock;
    lock_evidence_passage: the fact was extracted from a passage the diagnostic cites
    as this lock's own evidence (a documentary link, not an attribution).
    Nothing is inferred from vocabulary or from evidence shared with other facts."""
    source = state["diagnostic_link"]["snapshot"]
    lock_keys = _passage_keys(source, [p["evidence_id"] for p in lock_passages(source, lock)])
    rows, seen = [], set()
    for fact in records if records is not None else scoped_records(state):
        if fact["kind"] in {"lock", "source_passage"} or fact["statement"] in seen:
            continue
        if lock["id"] in fact["lock_ids"]:
            link = "declared"
        elif fact["scope"] == "project_context_not_linked_to_lock" and lock_keys & _passage_keys(source, fact["evidence_ids"]):
            link = "lock_evidence_passage"
        else:
            continue
        seen.add(fact["statement"])
        rows.append((link, fact))
    rows.sort(key=lambda row: (row[0] != "declared", DOSSIER_KINDS.index(row[1]["kind"]) if row[1]["kind"] in DOSSIER_KINDS else len(DOSSIER_KINDS)))
    return rows


def _fact_row(fact, link=None):
    metadata = fact["source"].get("metadata") or {}
    temporal = fact["source"].get("temporal_status") or metadata.get("temporal_status")
    return {"id": fact["id"], "kind": fact["kind"], "statement": fact["statement"], "evidence_ids": fact["evidence_ids"][:3],
        **({"link": link} if link else {}),
        **({"temporal_status": temporal} if temporal and temporal != "unclear" else {}),
        **({"needs_review": True} if fact["source"].get("needs_review") or metadata.get("needs_review") else {})}


# Role of a diagnostic fact in the project's R&D chain. Generic: these are the
# diagnostic's own extraction kinds, for any domain.
ROLES = {"objective": "objectives", "demarche": "approaches", "method": "methods", "parameter": "parameters",
    "metric": "parameters", "result": "results", "difficulty": "limits", "context": "context"}
ROLE_ORDER = ("approaches", "methods", "parameters", "results", "limits", "objectives", "context")


# A numbered heading inside a project passage (« Essai 5 — …», « Trial 2 - …», « Étape 3 — … »).
HEADING = re.compile(r"(?m)^\s*([A-ZÀ-Ý][\w’'À-ÿ]+)\s+(\d+)\s*[—–-]\s+(\S[^\n]{0,150})")
_PASSAGES = {}


def _number(value):
    return int(re.sub(r"\D", "", str(value or "")) or 0)


def _heading_key(title):
    match = HEADING.match(title or "")
    return (match.group(1).casefold(), match.group(2)) if match else None


def passage_index(state):
    """Canonical identity and effective locator of every diagnostic passage, computed on
    read; the stored snapshot is never rewritten.

    Identity: same document, same passage locator and same quote → one canonical id,
    every original id kept as an alias (similar text in another document stays distinct).
    Locator: the diagnostic's section_title can lag behind the document (a passage that
    starts at the end of trial 4 and opens trial 5 keeps « trial 4 », and so do its
    continuations). A numbered heading of the same family found in the passage text
    becomes the effective locator; contiguous continuations inherit it. A passage that
    crosses two headings is reported as spanning both."""
    source = (state.get("diagnostic_link") or {}).get("snapshot") or {}
    key = source.get("source_version")
    if key in _PASSAGES:
        return _PASSAGES[key]
    evidence = source.get("evidence", {})
    groups = {}
    for eid, row in evidence.items():
        identity = (str(row.get("document_id") or row.get("document_name") or ""),
            str(row.get("chunk_id") or row.get("passage_id") or ""), row.get("char_start"),
            " ".join(str(row.get("quote") or "").split()))
        groups.setdefault(identity, []).append(eid)
    canonical, aliases, locators = {}, {}, {}
    for identity, ids in groups.items():
        ids = sorted(ids)
        for eid in ids:
            canonical[eid] = ids[0]
        aliases[ids[0]] = ids
    by_document = {}
    for cid in aliases:
        row = evidence[cid]
        by_document.setdefault(str(row.get("document_id") or row.get("document_name") or ""), []).append(cid)
    for document, ids in by_document.items():
        ids.sort(key=lambda cid: (_number(evidence[cid].get("chunk_id") or evidence[cid].get("passage_id")),
            evidence[cid].get("char_start") or 0))
        current, previous = None, None
        for cid in ids:
            row = evidence[cid]
            title = row.get("section_title") or ""
            number = _number(row.get("chunk_id") or row.get("passage_id"))
            # A continuation of the previous passage under the same stored title inherits the
            # heading actually reached in the document.
            if previous and number - previous[0] <= 1 and title == previous[1] and current:
                start = current
            else:
                start = title
            family = _heading_key(start)
            heads = [m.group(0).strip() for m in HEADING.finditer(row.get("quote") or "")
                if not family or m.group(1).casefold() == family[0]]
            quote = (row.get("quote") or "").lstrip()
            opens = bool(heads) and quote.startswith(heads[0][:20])
            spans = list(dict.fromkeys(([] if opens else [start]) + heads)) if heads else [start]
            effective = spans[-1]
            locators[cid] = {"stored_section_title": title, "effective_section_title": effective,
                "spans": spans if len(spans) > 1 else [], "corrected": effective != title or len(spans) > 1}
            current, previous = effective, (number, title)
    _PASSAGES[key] = result = {"canonical": canonical, "aliases": aliases, "locators": locators}
    if len(_PASSAGES) > 32:
        _PASSAGES.clear()
        _PASSAGES[key] = result
    return result


def canonical_ids(state, ids):
    index = passage_index(state)
    return list(dict.fromkeys(index["canonical"].get(eid, eid) for eid in ids))


def _first_location(source, fact, state=None):
    """(document, effective section, passage) of a fact, in document order. A passage that
    crosses two document sections is placed where the fact's own text names the section
    (« Essai 5 — … »), otherwise in its opening section and reported as spanning."""
    rows = [(eid, source["evidence"][eid]) for eid in fact["evidence_ids"] if eid in source["evidence"]]
    if not rows:
        return None
    index = passage_index(state) if state is not None else {"canonical": {}, "locators": {}}
    eid, row = rows[0]
    locator = index["locators"].get(index["canonical"].get(eid, eid), {})
    title = locator.get("effective_section_title") or row.get("section_title") or ""
    spans = locator.get("spans") or []
    for candidate_id, candidate in rows[1:]:
        other = index["locators"].get(index["canonical"].get(candidate_id, candidate_id), {})
        if spans and other and not other.get("spans"):
            title, spans = other.get("effective_section_title") or title, []
            break
    if spans:
        named = [head for head in spans if (k := _heading_key(head)) and re.search(
            rf"\b{re.escape(k[0])}\s+{k[1]}\b", fact["statement"], re.I)]
        title = named[-1] if named else spans[0]
    passage = str(row.get("chunk_id") or row.get("passage_id") or "")
    return (str(row.get("document_name") or row.get("document_id") or ""), title, _number(passage), bool(spans) and not named
        if spans else False)


def work_units(state, lock, records=None):
    """The project's work related to one lock, grouped by documentary unit (the section
    of the project document its facts come from: a trial, an experiment, a study).

    A unit is linked to the lock when one of its facts is declared for the lock, or was
    extracted from a passage the diagnostic cites as the lock's evidence. Its other facts
    come from the same document section: a documentary grouping to confirm, never a
    causal link. Roles follow the diagnostic's own fact kinds; a role without any fact
    is reported as not documented, never filled."""
    source = state["diagnostic_link"]["snapshot"]
    records = list(records if records is not None else scoped_records(state))
    linked = {fact["id"]: link for link, fact in lock_records(state, lock, records)}
    by_unit, order, spanning = {}, {}, set()
    # Sections where the lock's own evidence passages lie (the opening section of a passage
    # that crosses into the next one).
    index = passage_index(state)
    lock_sections = set()
    for passage in lock_passages(source, lock):
        locator = index["locators"].get(index["canonical"].get(passage["evidence_id"], passage["evidence_id"]), {})
        lock_sections.add((locator.get("spans") or [locator.get("effective_section_title") or passage.get("section_title")])[0])
    for fact in records:
        if fact["kind"] in {"lock", "source_passage"} or fact["scope"] not in {"active_lock", "project_context_not_linked_to_lock"}:
            continue
        location = _first_location(source, fact, state)
        if not location:
            continue
        key = location[:2]
        by_unit.setdefault(key, []).append(fact)
        order[key] = min(order.get(key, location[2]), location[2])
        if location[3]:
            spanning.add(fact["id"])
    units, seen = [], set()
    for key in sorted(by_unit, key=lambda k: (k[0], order[k])):
        facts = by_unit[key]
        links = [linked[f["id"]] for f in facts if f["id"] in linked]
        if not links:
            continue
        roles = {role: [] for role in ROLE_ORDER}
        for fact in facts:
            if fact["statement"] in seen:
                continue
            seen.add(fact["statement"])
            row = _fact_row({**fact, "evidence_ids": canonical_ids(state, fact["evidence_ids"])},
                linked.get(fact["id"], "same_document_section"))
            if fact["id"] in spanning:
                row["locator"] = "passage_spans_two_document_sections"
            roles[ROLES.get(fact["kind"], "context")].append(row)
        # The unit is named by the project document's own section title (its segmentation).
        link_kind = "declared" if "declared" in links else "lock_evidence_passage"
        if link_kind == "lock_evidence_passage" and lock_sections and key[1] not in lock_sections:
            # Reached only through a lock passage that crosses into this section: to confirm.
            link_kind = "lock_evidence_passage_partial"
        units.append({"unit": key[1] or key[0], "document": key[0],
            "link": link_kind, "roles": roles,
            "roles_not_documented": [r for r in ("approaches", "methods", "parameters", "results", "limits") if not roles[r]]})
    return units


DOSSIER_GUIDANCE = ("Travaux du projet (jamais des publications), par unité documentaire (section du document projet). "
    "Lien au verrou : declared, lock_evidence_passage, ou lock_evidence_passage_partial (passage du verrou débordant sur cette "
    "section, à confirmer) ; same_document_section = même section, rapprochement à confirmer, "
    "non causal. roles_not_documented : lacune, ne pas compléter. Citer [projet:evidence_id]. planned n'est pas réalisé.")


def lock_dossier(state, lock_ids=None, budget=6000, *, general=True):
    """What a plan part, a section or a comparison needs from the diagnostic for its
    locks: identity, justification, the project's work units for each lock (approaches,
    methods, parameters, results, limits with their evidence), the lock's own evidence
    passages, then (optionally) general facts labelled as not linked.

    Whole records only. Under a small budget every lock and every unit keeps its first
    facts before any gets more; omissions are counted and recoverable."""
    link = state.get("diagnostic_link")
    if not link:
        return None
    source = link["snapshot"]
    active = link["active_lock_ids"]
    ids = [lid for lid in (lock_ids or active) if lid in active] or list(active)
    locks = [v for v in source["locks"] if v["id"] in ids]
    records = list(scoped_records(state))
    packet = {"project": {k: source["project"].get(k) for k in ("id", "name", "year")},
        "diagnostic_id": source["diagnostic_id"], "source_version": source["source_version"],
        "active_lock_ids": active, "locks": [], "guidance": DOSSIER_GUIDANCE, "partial": False}
    temporal = {k: source.get("temporal_context", {}).get(k) for k in ("rnd_start_date", "period", "work_period")
        if source.get("temporal_context", {}).get(k)}
    packet["rnd_reference_date"] = temporal or {"status": "unknown", "note": "Date de démarrage R&D absente : l'année CIR "
        "n'en tient pas lieu ; antériorité des publications à confirmer avec le consultant."}
    general_rows = [(score, fact) for score, fact, _ in technical_context(state, locks)] if general else []
    if general:
        packet["general_context_total"] = len(general_rows)
    fits = lambda: len(json.dumps(packet, ensure_ascii=False)) <= budget
    queues, gaps = [], []
    for lock in locks:
        row = {"id": lock["id"], "title": lock["title"], "decision": lock["decision"]}
        packet["locks"].append(row)
        units = work_units(state, lock, records)
        passages = lock_passages(source, lock)
        shells = [{"unit": u["unit"], "link": u["link"], "roles": {}} for u in units]
        gaps.append([(shell, u["roles_not_documented"]) for shell, u in zip(shells, units)])
        # A unit appears in the row with its first fact (no empty shells under a small budget).
        row.update(work_units=[], evidence_passages=[],
            facts_total=sum(len(v) for u in units for v in u["roles"].values()), passages_total=len(passages),
            facts_shown=0, passages_shown=0)
        # Round-robin across units, results and approaches first: each trial keeps what
        # was tried and what it gave before any unit gets its details.
        unit_items = [[(shell, role, fact) for role in ("results", "approaches", "methods", "parameters", "limits", "objectives", "context")
            for fact in unit["roles"][role]] for shell, unit in zip(shells, units)]
        items = [("fact", entry) for index in range(max((len(x) for x in unit_items), default=0))
            for x in unit_items if index < len(x) for entry in [x[index]]]
        items += [("evidence_passages", p) for p in passages]
        if lock.get("justification"):
            items.insert(min(1, len(items)), ("justification", lock["justification"]))
        queues.append((row, items))
    if not fits():
        packet["locks"] = [{"id": v["id"], "title": v["title"]} for v in locks]
        packet["partial"] = True
        return packet

    def count(row):
        row["facts_shown"] = sum(len(v) for u in row["work_units"] for v in u["roles"].values())
        row["passages_shown"] = len(row["evidence_passages"])

    def add(row, key, value):
        if key == "justification":
            row["justification"] = value
            if fits():
                return True
            row.pop("justification")
            return False
        added_shell = key == "fact" and not any(u is value[0] for u in row["work_units"])
        if added_shell:
            row["work_units"].append(value[0])
        target = value[0]["roles"].setdefault(value[1], []) if key == "fact" else row[key]
        target.append(value[2] if key == "fact" else value)
        count(row)
        if fits():
            return True
        target.pop()
        if key == "fact" and not target:
            value[0]["roles"].pop(value[1])
        if added_shell:
            row["work_units"].remove(value[0])
        count(row)
        return False
    pointers = [0]*len(queues)
    while any(p < len(items) for p, (_, items) in zip(pointers, queues)):
        for index, (row, items) in enumerate(queues):
            while pointers[index] < len(items):
                key, value = items[pointers[index]]
                pointers[index] += 1
                if add(row, key, value):
                    break
    # The documentation gaps of each unit, once every lock has its facts.
    shown_units = {id(u) for row in packet["locks"] for u in row["work_units"]}
    for lock_gaps in gaps:
        for shell, missing in lock_gaps:
            if missing and id(shell) in shown_units:
                shell["roles_not_documented"] = missing
                if not fits():
                    shell.pop("roles_not_documented")
    for row in packet["locks"]:
        count(row)
        for unit in row["work_units"]:
            unit["roles"] = {r: unit["roles"][r] for r in ROLE_ORDER if unit["roles"].get(r)}
        if not row["evidence_passages"]:
            row.pop("evidence_passages")
    if source["synthesis"].get("main_objective"):
        packet["main_objective"] = source["synthesis"]["main_objective"]
        if not fits():
            packet.pop("main_objective")
    if general:
        linked_statements = {f["statement"] for row in packet["locks"] for u in row["work_units"]
            for v in u["roles"].values() for f in v}
        packet["general_context"], shown = [], set()
        for _, fact in general_rows:
            if fact["statement"] in linked_statements or fact["statement"] in shown or len(fact["statement"]) > 400:
                continue
            packet["general_context"].append(_fact_row(fact))
            if not fits():
                packet["general_context"].pop()
                break
            shown.add(fact["statement"])
        if not packet["general_context"]:
            packet.pop("general_context")
    packet["partial"] = any(row["facts_shown"] < row["facts_total"] or row["passages_shown"] < row["passages_total"]
        for row in packet["locks"])
    if not packet["partial"] and not fits():
        packet["partial"] = True
    return packet


def dossier_evidence_ids(packet):
    """Diagnostic evidence ids actually present in a dossier sent to the model."""
    ids = set()
    for row in (packet or {}).get("locks", []):
        for unit in row.get("work_units", []):
            for facts in unit.get("roles", {}).values():
                for fact in facts:
                    ids.update(fact.get("evidence_ids", []))
        ids.update(p["evidence_id"] for p in row.get("evidence_passages", []))
    for fact in (packet or {}).get("general_context", []):
        ids.update(fact.get("evidence_ids", []))
    return ids


def diagnostic_evidence(state):
    """The linked diagnostic's evidence passages by id ({} in Express)."""
    link = state.get("diagnostic_link") if state.get("mode") == "diagnostic_linked" else None
    return (link or {}).get("snapshot", {}).get("evidence", {}) or {}


def model_page(result, kind):
    """A retrieval page for the model: whole statements, quotes and statuses, without
    the raw diagnostic payload (source JSON, provenance paths) the consultant view keeps."""
    page = dict(result)
    items = []
    for item in result["items"]:
        if kind in {"facts", "locks"}:
            source = item.get("source") or {}
            metadata = source.get("metadata") or {}
            row = {k: v for k, v in item.items() if k not in {"source", "source_path"}}
            row.update({k: copy.deepcopy(source.get(k, metadata.get(k))) for k in
                ("source_role", "temporal_status", "needs_review") if source.get(k, metadata.get(k)) is not None})
            if kind == "locks":
                row = {k: row[k] for k in ("id", "title", "decision", "justification") if k in row}
        elif kind == "evidence":
            row = {k: item.get(k) for k in ("evidence_id", "original_evidence_id", "quote", "document_name", "page",
                "chunk_id", "quote_verified", "verification_status") if item.get(k) is not None}
        elif "synthesis" in item:
            # Structured points (methods, results...) are the facts already paged by
            # kind=facts with their scope; the synthesis keeps its prose and counts.
            synthesis = item["synthesis"] if isinstance(item["synthesis"], dict) else {}
            row = {**item, "synthesis": {k: (v if isinstance(v, str) or not isinstance(v, list) or not v or not isinstance(v[0], dict)
                else {"records": len(v), "retrieval": "get_project_context(kind=facts, scope=general|active)"})
                for k, v in synthesis.items()}}
        else:
            row = item
        items.append(row)
    page["items"] = items
    return page


def comparison_project_packet(state, budget=3400):
    """A concise project side of the comparison; complete records stay in tools.

    General observations are deliberately separate, never assigned to the lock.
    No numeric/semantic extraction from legacy prose is fabricated here.
    """
    if state.get("mode") != "diagnostic_linked":
        return None
    source = state["diagnostic_link"]["snapshot"]
    rows = [f for f in scoped_records(state) if f["scope"] in {"active_lock", "project_context_not_linked_to_lock"}]
    packet = {"diagnostic_id": source["diagnostic_id"], "source_version": source["source_version"],
        "active_lock_ids": state["diagnostic_link"]["active_lock_ids"][:],
        "active_locks": [{k: v[k] for k in ("id", "title", "decision")} for v in source["locks"] if v["id"] in state["diagnostic_link"]["active_lock_ids"]],
        "observations": [], "observations_total": len(rows), "partial": False,
        "retrieval": "get_project_context(kind=facts, scope=active|general, ids=[source_fact_id]); kind=evidence pour les citations exactes. Les observations générales ne sont pas attribuées au verrou actif."}
    facts_by_id = {}
    for fact in project_facts_view(state):
        if fact.get("source_fact_id"):
            facts_by_id.setdefault(fact["source_fact_id"], []).append(fact["id"])
    # One whole record per category before later rows of the same category.
    # This prevents a long objectives list from starving methods and results.
    groups = []
    for kind in ("method", "parameter", "result", "difficulty", "demarche", "objective", "source_passage", "context", "metric", "lock"):
        candidates = [f for f in rows if f["kind"] == kind]
        candidates.sort(key=lambda f: f["scope"] != "active_lock")
        if candidates:
            groups.append(candidates)
    # The documented main objective remains visible even without extraction.
    if source["synthesis"].get("main_objective"):
        packet["main_objective"] = source["synthesis"]["main_objective"]
    packet["missing_information"] = source["synthesis"].get("missing_information", [])
    for index in range(max((len(g) for g in groups), default=0)):
        for group in groups:
            if index >= len(group):
                continue
            fact = group[index]
            proofs = [source["evidence"][eid] for eid in fact["evidence_ids"] if eid in source["evidence"]]
            row = {"source_fact_id": fact["id"], "category": fact["kind"], "statement": fact["statement"],
                "scope": fact["scope"], "lock_ids": fact["lock_ids"],
                "verification_status": "verified_quote" if proofs and all(e.get("quote_verified") is True for e in proofs) else "not_established",
                "temporal_status": fact["source"].get("temporal_status") or (fact["source"].get("metadata") or {}).get("temporal_status", "unclear"),
                "project_fact_ids": facts_by_id.get(fact["id"], [])[:1], "diagnostic_evidence_ids": fact["evidence_ids"][:2],
                "evidence_total": len(fact["evidence_ids"]), "source_role": fact["source"].get("source_role") or
                    (fact["source"].get("metadata") or {}).get("source_role") or (fact["source"].get("metadata") or {}).get("source_roles", ["unknown"])}
            packet["observations"].append(row)
            if len(json.dumps(packet, ensure_ascii=False)) > budget:
                packet["observations"].pop()
    packet["partial"] = len(packet["observations"]) < len(rows)
    if len(json.dumps(packet, ensure_ascii=False)) > budget:
        packet.pop("missing_information", None)
        packet.pop("main_objective", None)
        packet["partial"] = True
    if len(json.dumps(packet, ensure_ascii=False)) > budget:
        packet["active_locks"] = [{"id": x} for x in packet["active_lock_ids"]]
    return packet


def project_facts_view(state, source_fact_ids=None):
    """Existing human facts plus source-backed facts of the active diagnostic scope."""
    result = list(state.get("project_facts", []))
    if state.get("mode") != "diagnostic_linked":
        return result
    source = state["diagnostic_link"]["snapshot"]
    facts = [f for f in scoped_records(state) if f["scope"] in {"active_lock", "project_context_not_linked_to_lock"}]
    for fact in facts:
        if source_fact_ids is not None and fact["id"] not in source_fact_ids:
            continue
        # Literature, planned work and uncertain extractions retain their role.
        for eid in fact["evidence_ids"]:
            evidence = source["evidence"].get(eid)
            if not evidence:
                continue
            metadata = fact["source"].get("metadata") or {}
            role = ((metadata.get("evidence_source_roles") or {}).get(eid) or
                    (metadata.get("evidence_source_roles") or {}).get(evidence.get("original_evidence_id")) or
                    fact["source"].get("source_role") or metadata.get("source_role"))
            temporal = fact["source"].get("temporal_status") or metadata.get("temporal_status", "unclear")
            # Unclassified/unverified/literature passages stay retrievable, but
            # cannot satisfy the blueprint's distinct project-fact requirement.
            if role not in {"project_evidence", "project"} or evidence.get("quote_verified") is not True:
                continue
            if fact["source"].get("needs_review") or metadata.get("needs_review") or metadata.get("project_admission") is False:
                continue
            result.append({"id": "project_fact_diag_" + hashlib.sha256((source["source_version"]+fact["id"]+eid).encode()).hexdigest()[:24],
                "category": "uncertainty" if temporal != "performed" and fact["kind"] == "result" else {"lock": "difficulty", "metric": "parameter", "demarche": "method"}.get(fact["kind"], fact["kind"]),
                "statement": fact["statement"], "exact_quote": evidence["quote"],
                "source_type": "diagnostic_snapshot", "source_id": str(source["diagnostic_id"]),
                "source_version": source["source_version"], "diagnostic_evidence_id": eid,
                "source_fact_id": fact["id"], "source_metadata": copy.deepcopy(fact["source"]),
                "proof_reference": copy.deepcopy(evidence), "lock_ids": fact["lock_ids"], "scope": fact["scope"], "association_basis": fact["association_basis"], "value": None, "unit": None})
    return result
