"""Recoverable section context built from active sources, never stale rankings alone."""
import json
import re
from .evidence import exact_source_valid


def active_article_ids(state, registry):
    from .references import current_order
    document = state.get("document", {})
    section = next((s for s in state.get("outline", {}).get("sections", [])
        if s["id"] == document.get("active_section_id")), {})
    focused = state.get("focus", {}).get("article_id")
    ids = [*state.get("current_turn_article_ids", []), *([focused] if focused else []),
        *section.get("article_ids", []), *[aid for aid, article in registry.items() if article.selected],
        *state.get("evaluation", {}).get("recommended_article_ids", []),
        *current_order(state)[0]]
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
    ids = [aid for aid in ids if aid in registry and registry[aid].selection_override is not False]
    # A record without card whose twin (same work, same document, same card schema) was
    # read is written from the twin's card: one reading per document. The plan keeps its
    # own ids; the substitution is recorded for traceability.
    from .scientific_reading import reading_twins
    twins = {aid: group for group in reading_twins(state, registry).values() for aid in group}
    resolved = []
    for aid in ids:
        if not registry[aid].analysis and aid in twins:
            reader = next((other for other in twins[aid] if other != aid and registry[other].analysis), None)
            if reader:
                state.setdefault("reading_aliases", {})[aid] = reader
                aid = reader
        resolved.append(aid)
    return list(dict.fromkeys(resolved))


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


def section_fields(section):
    """Use the existing card fields in the order needed by this section."""
    topic = section.get("title", "") + " " + section.get("scientific_objective", "")
    if re.search(r"architect|mécanism|mecanism|mechanism|représentation|representation|apprentissage", topic, re.I):
        first = ("technical_mechanism", "parameters", "method", "equations", "experimental_setup")
        return (*first, *(f for f in SECTION_FIELDS if f not in first))
    return SECTION_FIELDS


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
    packet["proof_backed_claims"] = []
    terms = section_terms(section)
    queues = []
    for aid in ids:
        article = toolkit.registry[aid]
        view = analysis_projection(article.analysis, evidence=toolkit.evidence, active_only=True, snapshots=toolkit.snapshots)
        candidates = [(field, claim) for field in section_fields(section)
            for claim in rank_claims(view.get(field, []), terms) if isinstance(claim, dict)
            and claim_subject(claim, toolkit.evidence) == "this_work"
            and all(eid in toolkit.shown_evidence and eid in toolkit.evidence
                and toolkit.evidence[eid]["article_id"] == aid
                and exact_source_valid(toolkit.evidence[eid], toolkit.snapshots) for eid in claim["evidence_ids"])]
        # Prefer this section's fields and lexical terms; never relabel a
        # related-work claim as the article's own method to save space.
        chosen = next(iter(candidates), None)
        packet["articles"].append({"id": aid, "title": article.title,
            "analysis": {chosen[0]: [chosen[1]]} if chosen else {},
            "claims_available": sum(len(v) for v in view.values() if isinstance(v, list))})
        if chosen:
            primary.extend(chosen[1]["evidence_ids"])
        queues.append((aid, candidates))
    proofs = {eid: row for eid, row in toolkit.evidence.items() if row["article_id"] in ids
        and eid in toolkit.shown_evidence and exact_source_valid(row, toolkit.snapshots)}
    for name in dict.fromkeys(n for issue in issues for n in issue["names"]):
        for eid, row in proofs.items():
            if any(key and key in _proof_text(row) for key in _variants(name)):
                packet["source_mentions"].append({"name": name, "subject": claim_subject({"evidence_ids": [eid]}, toolkit.evidence),
                    "proof": proof_reference(row, toolkit.snapshots)})
                break
    for eid in draft["evidence_ids"]:
        if eid in proofs and isinstance(proofs[eid].get("table"), dict):
            packet["table_evidence_index"].append({"evidence_id": eid, "article_id": proofs[eid]["article_id"],
                "table": {k: proofs[eid]["table"].get(k) for k in ("number", "caption", "row", "headers")}})
    blueprint = state.get("argument_blueprint", {})
    if blueprint:
        packet["argument_blueprint"] = {"id": blueprint.get("id"), "axes": [
            {"axis": a["axis"], "steps": a["steps"][:1]} for a in blueprint.get("axes", [])[:1]]}
    packet["transmission_partial"] = False
    packet["retrieval_gaps"] = retrieval_gaps(state, toolkit, source_readiness(state, toolkit, ids), set())
    packet["retrieval"] = "Les affirmations ci-dessus sont disponibles maintenant ; leurs evidence_ids permettent " \
        "une relecture locale exacte avec analyze_article(read). get_analysis_claims(fields, offset) récupère " \
        "les autres affirmations pertinentes. Une lecture historique n'assure pas sa présence dans cet appel."
    size = lambda: len(json.dumps(packet, ensure_ascii=False))
    for optional in ("argument_blueprint", "table_evidence_index"):
        if size() > budget-4500:
            packet[optional] = {} if optional == "argument_blueprint" else []
    if size() > budget:
        raise ActiveReadingContextError("Le texte intégral de la section et ses identités dépassent le contexte de révision disponible.")
    # Compact claims retain their proof IDs; full quotes are loaded selectively below.
    # Alternate works so one large card cannot hide the other comparison partner.
    for index in range(max((len(rows) for _, rows in queues), default=0)):
        for aid, rows in queues:
            if index >= len(rows):
                continue
            field, claim = rows[index]
            row = {"article_id": aid, "field": field, "statement": claim["statement"],
                "evidence_ids": claim["evidence_ids"]}
            packet["proof_backed_claims"].append(row)
            if size() > min(budget*.65, budget-3500):
                packet["proof_backed_claims"].pop()
            else:
                primary.extend(claim["evidence_ids"])
    # Whole exact passages, not substrings. Reverse order balances the earlier
    # works that the ordinary card packet used to favour at the expense of later ones.
    extra = [row["proof"]["evidence_id"] for row in packet.get("source_mentions", [])]
    for eid in dict.fromkeys([*primary, *extra, *draft["evidence_ids"]]):
        if eid not in proofs:
            continue
        # These passages have already been delivered and validated. Repeat their
        # exact text and locator, without duplicating the stored version metadata.
        packet["evidence"].append(evidence_projection(proofs[eid], toolkit.snapshots))
        if size() > budget:
            packet["evidence"].pop()
    packet["transmission_partial"] = not set(primary) <= {e["evidence_id"] for e in packet["evidence"]}
    available = {claim_key(c) for c in packet["proof_backed_claims"]}
    packet["retrieval_gaps"] = retrieval_gaps(state, toolkit, source_readiness(state, toolkit, ids), available)
    return packet


# Card fields a section argues with, in the order their proof-backed claims are packed:
# what the work found, how, under which conditions, and where it stops.
SECTION_FIELDS = ("results", "method", "technical_mechanism", "experimental_setup", "parameters", "limitations",
    "comparisons", "datasets_materials", "validation_protocol", "conditions_of_validity", "metrics", "tables_figures",
    "contribution", "problem", "objective", "generalization", "hypotheses", "equations", "open_questions")


def usable_proofs(toolkit, article, cache=None):
    """Evidence of a card that can support a draft: stored, delivered to the model
    earlier (shown), and still exact against its source version."""
    cache = {} if cache is None else cache
    if "__shown__" not in cache:
        cache["__shown__"] = set(toolkit.shown_evidence)
    shown = cache["__shown__"]
    rows = {}
    for eid in {e["evidence_id"] for e in article.analysis.get("evidence", [])} | {
            eid for v in article.analysis.values() if isinstance(v, list) for c in v if isinstance(c, dict)
            for eid in c.get("evidence_ids", [])}:
        if eid not in cache:
            # Validity of the passage only; ownership is checked per article below.
            row = toolkit.evidence.get(eid)
            cache[eid] = row if (row and eid in shown and exact_source_valid(row, toolkit.snapshots)) else None
        if cache[eid] and cache[eid]["article_id"] == article.id:
            rows[eid] = cache[eid]
    return rows


def source_readiness(state, toolkit, ids):
    """Documentary state of each source of a section, never inferred from an abstract:
    analysed (a card with usable exact proofs), card_without_proofs, to_analyse (a
    reading can still run), unavailable (nothing readable: a documented limit)."""
    tasks = {t["article_id"]: t for t in state.get("scientific_reading", {}).get("tasks", [])}
    rows, cache = {}, {}
    for aid in ids:
        article = toolkit.registry.get(aid)
        if not article or article.selection_override is False:
            continue
        task = tasks.get(aid) or {}
        if article.analysis:
            status = "analysed" if usable_proofs(toolkit, article, cache) else "card_without_proofs"
        elif not article.abstract and not article.fulltext.get("text"):
            status = "unavailable"
        elif task.get("exhausted") or task.get("status") in {"source_unavailable", "needs_review"}:
            status = "unavailable"
        else:
            status = "to_analyse"
        rows[aid] = {"status": status, "basis": article.analysis_basis or ("fulltext" if article.fulltext.get("text") else
            "abstract" if article.abstract else "metadata"), "reading": {k: task.get(k) for k in
            ("status", "core_covered", "coverage_sufficient", "remaining_passages") if task.get(k) is not None}}
    return rows


def section_dossier(state, toolkit, section, budget):
    """The scientific side of one section: its question, its sources and their state,
    then proof-backed claims (a card claim always travels with its exact passages,
    never without them), round-robin across works and fields. Secondary context
    (previous sections, relations) takes what remains. Durable cards are untouched."""
    from .context import evidence_projection, analysis_projection
    from .attribution import article_identity, claim_subject
    outline = state.get("outline", {})
    ids = section_article_ids(state, toolkit.registry, section)
    readiness = source_readiness(state, toolkit, ids)
    terms = section_terms(section)
    packet = {"title": outline.get("title"), "plan_revision": outline.get("plan_revision"),
        "plan_status": outline.get("status", "proposed"),
        "section": {k: section.get(k) for k in ("id", "title", "scientific_objective", "lock_ids", "article_ids",
            "article_roles", "experimental_results", "limitations", "missing_information", "project_connection") if section.get(k)},
        "sources": [], "proof_backed_claims": [], "evidence": [], "table_evidence_index": [],
        "guidance": "Résultats, méthodes et limites des publications : uniquement ceux de proof_backed_claims, en citant "
            "[paper_ID] et les [ev_…] de leurs preuves dans la même phrase. Faits du projet : diagnostic_context, cités "
            "[projet:evidence_id]. Une comparaison cite les deux. Une source unavailable ou to_analyse est une limite "
            "documentaire à nommer, jamais un résultat. analysis_basis=abstract n'est pas une lecture intégrale."}
    # Sources start compact (identity and state); their details are added after the proofs.
    details = {}
    for aid, row in readiness.items():
        article = toolkit.registry[aid]
        identity = {k: v for k, v in article_identity(article).items() if v and k not in {"authors", "article_id"}}
        packet["sources"].append({"id": aid, "title": article.title[:120], "year": article.year,
            **{k: identity[k] for k in ("short_name", "first_author") if k in identity},
            "status": row["status"], "analysis_basis": row["basis"]})
        details[aid] = {"title": article.title[:220], **({"method_names": identity["method_names"]} if identity.get("method_names") else {}),
            "reading": row["reading"], **({"abstract": article.abstract[:300]} if row["status"] != "analysed" and article.abstract else {})}
    size = lambda: len(json.dumps(packet, ensure_ascii=False))
    proof_cache = {}
    queues = []
    for aid, row in readiness.items():
        if row["status"] != "analysed":
            continue
        article = toolkit.registry[aid]
        proofs = usable_proofs(toolkit, article, proof_cache)
        view = analysis_projection(article.analysis, evidence=toolkit.evidence, active_only=True, snapshots=toolkit.snapshots)
        claims = []
        fields = section_fields(section)
        for field in fields:
            value = view.get(field)
            if not (isinstance(value, list) and value and isinstance(value[0], dict)):
                continue
            for claim in rank_claims(value, terms):
                if claim.get("evidence_ids") and all(eid in proofs for eid in claim["evidence_ids"]):
                    claims.append((field, claim))
        # Alternate fields so results and limits are not crowded out by many method claims.
        by_field = {}
        for field, claim in claims:
            by_field.setdefault(field, []).append(claim)
        order = [(field, by_field[field][i]) for i in range(max((len(v) for v in by_field.values()), default=0))
            for field in fields if field in by_field and i < len(by_field[field])]
        queues.append((aid, proofs, order, len(claims)))
    included = set()
    shown_claims = {aid: 0 for aid, *_ in queues}
    # Other exact passages already delivered for these analysed works. Table rows come
    # first (numeric claims are checked against their cells; long claims must not crowd
    # them out), then the proof-backed claims, then quantified and other passages.
    extra = {aid: [] for aid, *_ in queues}
    for eid in reversed(toolkit.shown_evidence):
        row = toolkit.evidence.get(eid)
        if row and row["article_id"] in extra and exact_source_valid(row, toolkit.snapshots):
            extra[row["article_id"]].append(row)
    for rows in extra.values():
        rows.sort(key=lambda e: (not isinstance(e.get("table"), dict), not re.search(r"\d", e.get("exact_quote", ""))))

    def add_extra(limit, tables_only):
        for index in range(max((len(v) for v in extra.values()), default=0)):
            for rows in extra.values():
                if index >= len(rows) or rows[index]["evidence_id"] in included:
                    continue
                if tables_only and not isinstance(rows[index].get("table"), dict):
                    continue
                packet["evidence"].append(evidence_projection(rows[index], toolkit.snapshots))
                if size() > limit:
                    packet["evidence"].pop()
                else:
                    included.add(rows[index]["evidence_id"])
    if section_fields(section)[0] == "results":
        add_extra(budget*.3, True)
    for index in range(max((len(q[2]) for q in queues), default=0)):
        for aid, proofs, order, _ in queues:
            if index >= len(order):
                continue
            field, claim = order[index]
            added = [eid for eid in claim["evidence_ids"] if eid not in included]
            packet["proof_backed_claims"].append({"article_id": aid, "field": field, "statement": claim["statement"],
                "evidence_ids": claim["evidence_ids"], **({"subject": claim["subject"]} if claim.get("subject") else {})})
            packet["evidence"].extend(evidence_projection(proofs[eid], toolkit.snapshots) for eid in added)
            if size() > budget*.75:
                packet["proof_backed_claims"].pop()
                del packet["evidence"][len(packet["evidence"])-len(added):]
                continue
            included.update(added)
            shown_claims[aid] += 1
    for aid, proofs, order, total in queues:
        source = next(s for s in packet["sources"] if s["id"] == aid)
        source.update(claims_with_proofs=total, claims_shown=shown_claims[aid])
        details[aid]["claims_available"] = {k: len(v) for k, v in toolkit.registry[aid].analysis.items()
            if isinstance(v, list) and v and isinstance(v[0], dict) and k not in {"evidence"}}
    add_extra(budget*.9, False)
    for eid in included:
        proof = toolkit.evidence[eid]
        if isinstance(proof.get("table"), dict):
            packet["table_evidence_index"].append({"evidence_id": eid, "article_id": proof["article_id"],
                "table": {k: proof["table"].get(k) for k in ("number", "caption", "row", "headers") if proof["table"].get(k)}})
    # Source details (full title, method names, reading state, card counts), when room is left.
    for source in packet["sources"]:
        before = dict(source)
        source.update(details[source["id"]])
        if size() > budget*.92:
            source.clear()
            source.update(before)
    # Secondary context, within what is left: the saved storyline of this section first.
    from .argumentation import blueprint_status
    blueprint = state.get("argument_blueprint", {})
    if blueprint and not blueprint_status(state, toolkit.registry)["stale"]:
        packet["argument_blueprint"] = {"id": blueprint["id"], "version": blueprint["version"],
            "axes": [{"axis": axis["axis"], "scientific_problem": axis["scientific_problem"],
                "transition": axis["transition"], "steps": [{**step, "statement": step["statement"][:600]}
                    for step in axis["steps"][:12]]} for axis in blueprint["axes"] if
                axis.get("section_id") == section.get("id") or
                (not axis.get("section_id") and set(aid for step in axis["steps"] for aid in step["article_ids"]) & set(ids))][:3]}
        fact_ids = {fid for axis in packet["argument_blueprint"]["axes"] for step in axis["steps"] for fid in step["project_fact_ids"]}
        from .diagnostic_link import project_facts_view
        packet["project_facts"] = [fact for fact in project_facts_view(state) if fact["id"] in fact_ids]
        if size() > budget:
            packet["argument_blueprint"] = {"id": blueprint["id"], "version": blueprint["version"],
                "axes": [{"axis": a["axis"], "step_evidence_ids": [st["evidence_ids"] for st in a["steps"]]}
                    for a in packet["argument_blueprint"]["axes"]]}
            packet["project_facts"] = [{k: f[k] for k in ("id", "statement") if k in f} for f in packet["project_facts"]]
    # What the other sections establish (manuscript memory): transitions refer to it and
    # the synthesis builds on it instead of re-reading and repeating the same results.
    synthesis = bool(re.search(r"\b(?:synth[eè]se|conclusions?|perspectives|bilan|summary|outlook)\b", section.get("title", ""), re.I))
    packet["manuscript_map"] = manuscript_map(state, toolkit, section, budget*(.3 if synthesis else .12))
    for relation in state.get("scientific_relations", []):
        if set(relation["article_ids"]) <= set(ids) and not relation.get("stale"):
            packet.setdefault("relations", []).append({k: v for k, v in relation.items() if k != "experimental_results"})
            if size() > budget:
                packet["relations"].pop()
                break
    packet["transmission_partial"] = any(s.get("claims_shown", 0) < s.get("claims_with_proofs", 0) for s in packet["sources"])
    # Gaps describe this packet, not past calls: earlier claims may have left the
    # bounded context. The model chooses which missing details this section needs.
    delivered = {claim_key(c) for c in packet["proof_backed_claims"]}
    gaps = retrieval_gaps(state, toolkit, readiness, delivered)
    packet["retrieval_gaps"] = gaps
    packet["retrieval"] = ("Lacunes du contexte actuel, pas de l'historique. Récupérer seulement les détails utiles : "
        "get_analysis_claims(article_id, fields, offset) ; passage exact local : analyze_article(read, evidence_ids).")
    for optional in ("manuscript_map", "argument_blueprint", "project_facts", "relations"):
        if size() <= budget:
            break
        if optional in packet:
            packet.pop(optional)
            packet.setdefault("deferred_context_keys", []).append(optional)
    return packet


def claim_key(claim):
    """Identity of a card claim as transmitted (statement and its proofs)."""
    from .task_results import fingerprint
    return fingerprint([claim.get("statement"), sorted(claim.get("evidence_ids", []))])


def retrieval_gaps(state, toolkit, readiness, delivered):
    from .context import analysis_projection
    tasks = {t["article_id"]: t for t in state.get("scientific_reading", {}).get("tasks", [])}
    gaps = {"claims_not_transmitted": {}, "sources_without_card": [], "abstract_only": [], "dimensions_not_examined": {}}
    for aid, row in readiness.items():
        if row["status"] != "analysed":
            gaps["sources_without_card"].append(aid)
            continue
        view = analysis_projection(toolkit.registry[aid].analysis, evidence=toolkit.evidence, active_only=True, snapshots=toolkit.snapshots)
        # A count per work keeps the packet small; get_analysis_claims returns the claims themselves.
        missing = sum(claim_key(c) not in delivered for value in view.values()
            if isinstance(value, list) and value and isinstance(value[0], dict) for c in value)
        if missing:
            gaps["claims_not_transmitted"][aid] = missing
        if row["basis"] == "abstract":
            gaps["abstract_only"].append(aid)
        dims = [name for name, value in (tasks.get(aid, {}).get("dimensions") or {}).items() if value.get("status") == "not_examined"]
        if dims:
            gaps["dimensions_not_examined"][aid] = dims
    gaps["complete"] = not any(gaps[k] for k in ("claims_not_transmitted", "sources_without_card", "dimensions_not_examined"))
    return gaps


def section_packet(inputs):
    """The section packet actually sent in a request (None outside section drafting)."""
    for item in inputs:
        content = item.get("content") if isinstance(item, dict) and item.get("role") == "developer" else None
        if isinstance(content, str) and content.startswith("Mémoire scientifique"):
            try:
                return (json.loads(content.split(" : ", 1)[1]).get("section_drafting") or {}).get("packet")
            except (ValueError, IndexError, AttributeError):
                return None
    return None


def current_claim_keys(inputs):
    """Claims actually present after compaction, never the historical delivery ledger."""
    keys = set()
    def walk(value):
        if isinstance(value, dict):
            if value.get("statement") and value.get("evidence_ids"):
                keys.add(claim_key(value))
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    for item in inputs:
        content = item.get("output") if item.get("type") == "function_call_output" else (
            item.get("content") if item.get("role") == "developer" else None)
        if isinstance(content, str):
            if content.startswith("Mémoire scientifique"):
                content = content.split(" : ", 1)[-1]
            try:
                walk(json.loads(content))
            except (ValueError, TypeError):
                pass
    return keys


def writing_context(state, toolkit, section=None, budget=7000):
    outline = state.get("outline", {})
    if section is None:
        section = next((s for s in outline.get("sections", [])
            if s["id"] == state.get("document", {}).get("active_section_id")), {})
    progress = state.get("document", {}).get("writing_progress", {})
    if progress.get("mode") == "section" and section.get("id") in progress.get("target_ids", []) and section.get("drafts"):
        return revision_context(state, toolkit, section, budget)
    return section_dossier(state, toolkit, section, budget)


def _sentences(text):
    from .attribution import sentences
    return [x for x in sentences(re.sub(r"^\s*#{1,6}\s+[^\n]*\n", "", text or "")) if len(x) > 40]


def section_memory(state, section):
    """Compact memory of one saved section: its role, what it establishes, its limits and
    links (written by the model at save time), its sources and the project work it cites.
    Drafts saved without such a summary get an extract of their own first and last
    sentences, labelled as such. Never a new fact: the proofs stay in the draft."""
    draft = (section.get("drafts") or [None])[-1]
    if not draft:
        return None
    stored = (state.get("manuscript_memory") or {}).get(section["id"])
    if stored and stored.get("draft_id") == draft["id"]:
        return stored
    rows = _sentences(draft.get("raw_text") or draft.get("text"))
    return {"section_id": section["id"], "draft_id": draft["id"], "origin": "extract_of_saved_text",
        "established": rows[-1:] if rows else [], "opening": rows[:1], "article_ids": draft.get("article_ids", []),
        "project_units": sorted({f"{r['lock_id']}:{r['unit']}" for r in draft.get("project_relations", []) if r.get("unit")})}


def record_section_memory(state, section, draft, summary):
    """Store the model's own summary of a saved section (manuscript memory)."""
    memory = state.setdefault("manuscript_memory", {})
    memory[section["id"]] = {"section_id": section["id"], "draft_id": draft["id"], "origin": "writer_summary",
        **{k: summary.get(k) for k in ("role", "established", "limits", "links") if summary.get(k)},
        "article_ids": draft.get("article_ids", []),
        "project_units": sorted({f"{r['lock_id']}:{r['unit']}" for r in draft.get("project_relations", []) if r.get("unit")})}
    return memory[section["id"]]


def manuscript_map(state, toolkit, section, budget):
    """Every other section of the plan, in order: its state, and for saved ones what it
    establishes, its limits and sources (short identities). Bounded; the sections nearest
    to this one come first when room runs out."""
    sections = state.get("outline", {}).get("sections", [])
    progress = state.get("document", {}).get("writing_progress") or {}
    position = next((i for i, s in enumerate(sections) if s.get("id") == section.get("id")), 0)
    order = sorted((i for i, s in enumerate(sections) if s.get("id") != section.get("id")), key=lambda i: abs(i-position))
    rows = {}
    for i in order:
        other = sections[i]
        memory = section_memory(state, other)
        if memory:
            names = [toolkit.registry[a].title.split(":")[0][:60] for a in memory.get("article_ids", []) if a in toolkit.registry]
            row = {"number": i+1, "title": other["title"][:160], "status": "saved",
                **{k: memory[k] for k in ("role", "established", "limits", "links", "opening") if memory.get(k)},
                "sources": names[:6], **({"project_units": memory["project_units"]} if memory.get("project_units") else {}),
                **({"memory_origin": memory["origin"]} if memory.get("origin") != "writer_summary" else {})}
        else:
            row = {"number": i+1, "title": other["title"][:160],
                "status": "blocked" if other["id"] in progress.get("blocked", {}) else "not_written",
                **({"cause": progress["blocked"][other["id"]][:200]} if other["id"] in progress.get("blocked", {}) else {})}
        rows[i] = row
        if len(json.dumps(list(rows.values()), ensure_ascii=False)) > budget:
            rows.pop(i)
            rows[i] = {"number": i+1, "title": other["title"][:120], "status": row["status"]}
            if len(json.dumps(list(rows.values()), ensure_ascii=False)) > budget:
                rows.pop(i)
                break
    return {"sections": [rows[i] for i in sorted(rows)], "sections_total": len(sections)-1,
        "guidance": "Ce que les autres sections établissent déjà : y renvoyer brièvement au lieu de répéter leurs "
            "résultats ; la synthèse s'appuie sur cette carte et nomme les sections non rédigées comme limites."}
