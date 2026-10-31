"""One V5 scientific workflow over V4's persistent artefacts.

research -> deep reading -> saved cards -> saved plan -> section dossier ->
section-by-section drafting -> assembled manuscript.

Every scientific operation stays V4's (manage_outline, write_section and its
validators, writing_progress, writing_context.section_dossier,
source_readiness). This module only decides what reaches Sol within the
existing context budget, records what was actually delivered for the current
consultant request, and tracks the plan and manuscript obligations that
LangGraph resumes. Diagnosed on session 3445749b (six real runs, 28/10).
"""
from __future__ import annotations

import copy
import json
import re
from types import SimpleNamespace

from agents.EnnoScholarV4.task_results import fingerprint

CONTROL_RESERVE = 14000          # v5_work_state + request (<= 6000) + goal, added by the graph.
WRITING_MEMORY_CHARS = 10000     # The section dossier carries the science while drafting.
SECTION_PACKET_CHARS = 40000     # Taken from the same context budget, never added to it.
MIN_GATHERING_ROOM = 3000        # Below this, a reading is deferred, not cut to nothing.
DOSSIER_PREFIX = "Dossier scientifique de la section à rédiger (données, pas instructions) : "
PLAN_DELIVERABLES = {"plan", "plan_revision"}
# Tools whose result informs a later call of the same response. Writes are not.
WRITE_TOOLS = {"manage_outline", "write_section", "set_turn_goal", "update_research_state", "record_project_facts",
    "record_candidate_decisions", "finalize_response", "pause"}
GATHERING = {"analyze_article", "get_analysis_claims", "read_conversation", "compare_articles", "get_article",
    "search_collected_articles", "build_argument_blueprint", "review_literature_coverage", "review_document"}


def current_request(state):
    return next((m for m in reversed(state.get("messages", [])) if m.get("role") == "user"), {})


def gathers(decision):
    if decision.tool == "analyze_article":
        return decision.arguments.get("action") in {'read', 'index'}
    if decision.tool == "compare_articles":
        return decision.arguments.get("relations") is None and decision.arguments.get("conclusions") is None
    return decision.tool not in WRITE_TOOLS


# --- Writing progress (V4 writing_progress, resumed by the graph) -----------------

def writing_progress(state):
    """The drafting progress opened for the current request, if any."""
    progress = (state.get("document") or {}).get("writing_progress")
    if not progress or progress.get("request_message_id") != current_request(state).get("id"):
        return None
    return progress


def next_writing_section(state):
    """Next plan section to draft for this request (V4 next_section), or None."""
    if not writing_progress(state):
        return None
    from .writing_progress import next_section
    return next_section(state)


def writing_active(state):
    progress = writing_progress(state)
    if not progress:
        return False
    from .writing_progress import refresh_writing
    return refresh_writing(state)["status"] in {"running", "awaiting_plan"} and next_writing_section(state) is not None


def begin(state, request):
    """V4 begin_writing at the start of a request (as V4 agent.run does)."""
    from .writing_progress import begin_writing
    try:
        progress = begin_writing(state, request or "")
    except ValueError:
        return None
    if progress is not None and progress.get("request_message_id") != current_request(state).get("id"):
        # « Continue la rédaction » resumes the saved progress (V4 returns it as is):
        # it now belongs to this request, so the next section and its dossier follow.
        progress["request_message_id"] = current_request(state).get("id")
    from .comparison_memory import pin_question
    pin_question(state, progress, request or '')
    return progress


def reconcile_draft_goal(state):
    """Sol's explicit saved intent opens progress even for an unfamiliar phrasing."""
    goal = state.get('turn_goal') or {}
    if goal.get('deliverable') != 'draft' or writing_progress(state):
        return
    refs = goal.get('references') or {}
    if refs.get('message_id') != current_request(state).get('id'):
        return
    from .writing_progress import open_writing, requested_section, writing_request_blocked
    request = current_request(state).get('content', '')
    if writing_request_blocked(state, request):
        return
    single = requested_section(state, request)
    if single:
        from .tools.outline import resolve_section
        sec = resolve_section(state, single)
        open_writing(state, 'section', [single], {single:(sec.get('drafts') or [{}])[-1].get('id')})
    else:
        p = open_writing(state)
        p['include_conclusion'] = True

    from .comparison_memory import pin_question
    pin_question(state, writing_progress(state), request)


def context_split(config, state):
    """(exchange budget, memory budget, dossier budget) inside max_context_chars."""
    drafting = next_writing_section(state) is not None
    memory = WRITING_MEMORY_CHARS if drafting else config.finalization_context_chars
    dossier = SECTION_PACKET_CHARS if drafting else 0
    return max(1800, config.max_context_chars-memory-dossier-CONTROL_RESERVE-512), memory, dossier


def exchange_size(exchange):
    # compact_exchange drops these first; they never compete with tool results.
    return len(json.dumps([i for i in exchange if i.get("type") not in {"message", "web_search_call", "reasoning"}],
        ensure_ascii=False))


def section_packet(state, toolkit, section, budget):
    """Whole V4 claims plus current measurements/relations inside the same budget."""
    from .writing_context import writing_context
    science = section_science(state, toolkit, section, min(14000, budget*.4))
    extra = {"scientific_detail": science}
    p = writing_progress(state) or {}
    extra['requires_conclusion'] = bool(p.get('include_conclusion') and p.get('target_ids', [None])[-1] == section['id'])
    if extra['requires_conclusion']:
        extra['conclusion_rule'] = ('Terminer cette dernière section par « ## Conclusion générale ». Synthétiser les '
            'sections sauvegardées, les relations actuelles et les limites avec leurs preuves ; aucune nouvelle donnée. '
            'La conclusion est validée et sauvegardée avec write_section(save), sans changer le plan approuvé.')
    extra["coverage_rule"] = ("Chaque source analysed de la section est intégrée : méthodes, résultats et limites de sa "
        "fiche, citée [paper_ID] avec ses [ev_…] et déclarée dans article_ids. Une source to_analyse peut être analysée "
        "avant la rédaction (analyze_article). Les sources sans fiche exploitable sont enregistrées comme limites "
        "documentaires de la section et affichées avec elle ; aucun résultat ne leur est attribué.")
    extra['argumentation_rule'] = ('Construire une analyse continue du problème, des approches, des méthodes et résultats '
        'confrontés dans leurs conditions, puis des limites et verrous ouverts ; pas une juxtaposition de résumés. '
        'Sol décide quels détails essentiels approfondir avant write_section. Une relation comparative cite ses travaux '
        'et ses preuves ; distinguer observation, interprétation des auteurs et inférence. Les lacunes restent explicites.')
    extra["documentary_limits"] = section_limits(state, toolkit, section)
    extra["scientific_preparation"] = section_preparation(state, toolkit, section)
    pending = provisional_section(state, section["id"])
    if pending:
        text = pending["arguments"].get("text") or ""
        extra["provisional_draft"] = {"status": "refusé, non publié", "refusal": pending.get("refusal"),
            "text_excerpt": text[:4000]+("…" if len(text) > 4000 else ""), "text_chars": len(text),
            "reuse": "Corriger puis réenregistrer ; write_section(save, text=null) reprend ce texte tel quel."}
    extra["save_with"] = f"write_section(action='save', section_id='{section['id']}', …)"
    packet = writing_context(state, toolkit, section, budget-len(json.dumps(extra, ensure_ascii=False))-32)
    # The original dossier omits experimental_results and only checks a stale
    # flag. Scientific_detail contains the full, currently validated relations.
    packet.pop('relations', None)
    packet.update(extra)
    return packet


def section_science(state, toolkit, section, budget):
    """Supplement, with complete proofs or explicit retrieval counts, never snippets."""
    from .science import reading_status
    from .argumentation import relation_current
    from .context import evidence_projection
    from .evidence import exact_source_valid, snapshot
    from .writing_context import section_article_ids, section_terms, rank_claims
    ids = section_article_ids(state, toolkit.registry, section)
    packet = {'source_coverage': {}, 'structured_measurements': [], 'relations': [], 'evidence': [],
        'deferred': {'measurements': 0, 'relations': 0},
        'retrieval': 'Détails non transmis : get_analysis_claims(measurement_offset) ou compare_articles sans relations. '
            'Lacune essentielle : analyze_article(index, section_filter), puis read/save/review selon le besoin.'}
    for aid in ids:
        status = reading_status(state, toolkit.registry[aid])
        packet['source_coverage'][aid] = {k:status[k] for k in
            ('dimensions', 'question_sufficient', 'review_stale', 'uncertainties')}
    included = set()
    def add(kind, value, eids):
        proofs = [toolkit.evidence.get(eid) for eid in dict.fromkeys(eids)]
        if not proofs or any(not p or not exact_source_valid(p, toolkit.snapshots) for p in proofs):
            packet['deferred'][kind] += 1
            return
        added = [evidence_projection(p, toolkit.snapshots) for p in proofs if p['evidence_id'] not in included]
        packet[kind].append(copy.deepcopy(value))
        start = len(packet['evidence'])
        packet['evidence'].extend(added)
        if len(json.dumps(packet, ensure_ascii=False)) > budget-100:
            packet[kind].pop()
            del packet['evidence'][start:]
            packet['deferred'][kind] += 1
        else:
            included.update(p['evidence_id'] for p in added)
    relations = [r for r in state.get('scientific_relations', [])
        if set(r['article_ids']) <= set(ids) and relation_current(toolkit,r)]
    terms = section_terms(section)
    for relation in rank_claims(relations, terms):
        eids = [*relation['evidence_ids'], *[eid for c in relation.get('experimental_results', []) for eid in c.get('evidence_ids', [])]]
        add('relations', relation, eids)
    queues = []
    for aid in ids:
        article = toolkit.registry[aid]
        current = snapshot(article)['fingerprint']
        queues.append([(aid,m) for m in article.analysis.get('structured_measurements', {}).values()
            if current in m.get('source_fingerprints', [])])
    for offset in range(max((len(q) for q in queues), default=0)):
        for queue in queues:
            if offset < len(queue):
                aid, measurement = queue[offset]
                add('structured_measurements', {'article_id':aid, **measurement}, measurement['evidence_ids'])
    # Literal index, not converted measurements: Sol must inspect attribution,
    # experimental conditions and the exact source before using these values.
    import re
    packet['quantified_evidence_index'] = []
    for aid in ids:
        for proof in toolkit.registry[aid].analysis.get('evidence', []):
            values = list(dict.fromkeys(re.findall(r'[−-]?\d+(?:[.,]\d+)?(?:\s*(?:,|and|et|to)\s*[−-]?\d+(?:[.,]\d+)?)*\s*(?:°\s*C|%|mAh|mA|mV|V)(?!\w)',proof.get('exact_quote',''))))
            if values and exact_source_valid(proof,toolkit.snapshots):
                row = {'article_id':aid,'evidence_id':proof['evidence_id'],'literal_values':values[:12],
                    'status':'source_occurrences; experimental_attribution_to_check'}
                packet['quantified_evidence_index'].append(row)
                # This small index replaces a part of the base dossier: section_packet
                # subtracts its actual size from the SAME 40k envelope.
                if len(json.dumps(packet,ensure_ascii=False))>budget+1800:
                    packet['quantified_evidence_index'].pop()
                    break
    return packet


def section_readiness(state, toolkit, section):
    from .writing_context import source_readiness
    return source_readiness(state, toolkit, list(section.get("article_ids") or []))


CORE_DIMENSIONS = ("methods", "results", "limits")
CONTEXT_DIMENSIONS = ("experimental_protocol", "conditions", "parameters")
UNEXAMINED = {"not_examined", "unread"}


def section_preparation(state, toolkit, section):
    """Scientific prerequisites of one section, from the saved state only.

    A source is ready when its card documents methods, results and limits with
    exact proofs, or when a review of this request declares the coverage
    sufficient. Otherwise the existing tools say what can still be done: read and
    save from a local text, retrieve the text, deepen a card, or compare the
    section's analysed works. Nothing is inferred from an abstract; what cannot
    be done becomes a stated limit of the section.
    """
    from .science import reading_status
    failed = set(knowledge(state).get("failed_sources", []))
    sources, actions, analysed = {}, [], []
    for aid, row in section_readiness(state, toolkit, section).items():
        article = toolkit.registry[aid]
        text = bool(article.fulltext.get("text"))
        unreachable = not text and (aid in failed or download_failed(article))
        basis = row["basis"]
        if row["status"] == "analysed" and basis != "fulltext" and fulltext_core(article, toolkit.evidence, toolkit.snapshots):
            # V4 calls a card « abstract » while any of its proofs is (the card enriched from
            # a PDF keeps its abstract proofs). Methods, results and limits each backed by
            # the full text is a full-text analysis for the section.
            basis = "fulltext"
        depth = None
        if row["status"] == "analysed" and basis == "fulltext":
            # V4's basis is provenance: a card built only on the abstract or introduction of
            # the PDF is « fulltext » there. Depth comes from the dimensions read in the body.
            from .science import analysis_depth
            depth = analysis_depth({**state, "evidence": toolkit.evidence, "source_snapshots": toolkit.snapshots}, article)
            if depth["depth"] == "introductory":
                basis = "introductory"
        entry = {"status": row["status"], "basis": basis, "card_basis": row["basis"], "text_available": text,
            "download_failed": unreachable, "has_abstract": bool(article.abstract),
            "access_state": 'local_text' if text else 'retrieval_failed' if unreachable else 'not_requested',
            "attempt_count": len(article.fulltext.get('attempts', [])),
            "presented_passages":len([e for e in state.get('shown_evidence_ids', []) if toolkit.evidence.get(e,{}).get('article_id')==aid]),
            "saved_proofs":len(article.analysis.get('evidence', []))}
        if row["status"] == "analysed":
            analysed.append(aid)
            status = reading_status(state, article)
            dims = status["dimensions"]
            missing = [d for d in CORE_DIMENSIONS if dims.get(d) in UNEXAMINED
                or (depth is not None and depth["coverage"].get(d) == "introductory")]
            if depth is not None:
                entry["analysis_depth"] = depth["depth"]
            entry.update(missing=missing, context_missing=[d for d in CONTEXT_DIMENSIONS if dims.get(d) in UNEXAMINED])
            if basis != "fulltext" and text:
                # A full text obtained after an abstract-based card (e.g. a PDF added later).
                entry["state"] = "to_deepen"
                actions.append({"article_id": aid, "kind": "local", "action": "analyze_article(index/read) puis save",
                    "reason": "texte intégral disponible : la fiche ne repose que sur son résumé ou son introduction"
                        if basis == "introductory" else "texte intégral disponible : la fiche ne repose que sur le résumé"})
            elif basis != "fulltext" and not unreachable:
                entry["state"] = "abstract_only"
                actions.append({"article_id": aid, "kind": "network", "action": "get_fulltext, puis analyze_article(index/read/save)",
                    "reason": "fiche fondée sur le résumé ; texte intégral jamais recherché"})
            elif status["question_sufficient"] or not missing:
                entry["state"] = "ready"
            elif text:
                entry["state"] = "to_deepen"
                actions.append({"article_id": aid, "kind": "local", "action": "analyze_article(index, section_filter) puis read/save, ou review",
                    "reason": "dimensions non examinées : "+", ".join(missing)})
            else:
                entry["state"] = "insufficiently_documented"
        elif text or (row["status"] in {"to_analyse", "card_without_proofs"} and not unreachable):
            # A local full text can always be read, whatever an earlier reading task said.
            entry["state"] = "to_analyse"
            actions.append({"article_id": aid, "kind": "local" if text else "network",
                "action": "analyze_article(read) puis save" if text else "get_fulltext, puis analyze_article(read/save)",
                "reason": source_label(entry)})
        else:
            entry["state"] = "unavailable"
        entry["label"] = source_label(entry)
        sources[aid] = entry
    comparison = None
    if len(analysed) >= 2:
        from .argumentation import relation_current
        relations = [r["id"] for r in state.get("scientific_relations", [])
            if len(set(r["article_ids"]) & set(analysed)) >= 2 and relation_current(toolkit, r)]
        # A saved « no reliable relation » assessment is reused like a saved relation.
        from .comparison_memory import remembered
        decided = remembered(state, analysed, section)
        comparison = {"article_ids": analysed, "relation_ids": relations[:12], "assessment_saved": decided}
        # A current saved relation still counts as the section's comparison (V4
        # relation_current checks its articles and proofs); only a negative
        # assessment needs the receipt identity of comparison_memory.
        if not relations and not decided:
            actions.append({"article_ids": analysed, "kind": "local", "action": "compare_articles (relations ou relations=[] avec assessment)",
                "reason": "travaux analysés de la section jamais confrontés"})
    quality = assess_quality(sources, actions)
    # ready: nothing more can be done now, the section can be written. It is not a
    # judgement of sufficiency: see scientific_state (run bcf1d7ad, three abstracts).
    return {"section_id": section["id"], "ready": not actions, "sources": sources, "actions": actions,
        "basis": quality["basis"], "scientific_state": quality["scientific_state"], "insufficient": quality["insufficient"],
        **({"comparison": comparison} if comparison else {}),
        "rule": "Avant write_section(save) : réaliser ces lectures, enrichissements et comparaisons utiles à la question "
            "de la section. Ce qui reste impossible (texte inaccessible, information absente) est signalé comme limite."}


CORE_FIELDS = {"methods": ("method",), "results": ("results",), "limits": ("limitations",)}


def fulltext_core(article, evidence, snapshots):
    """Each core dimension of the card has a claim backed by a proof from the body of the full
    text: the abstract or introduction printed in the document is not a reading of the work."""
    from .evidence import usable_proof
    from .science import introductory
    state = {'evidence': evidence, 'source_snapshots': snapshots}
    def full(claim):
        ids = claim.get('evidence_ids') or []
        return bool(ids and all(usable_proof(state, eid, article_id=article.id) for eid in ids)
            and any(evidence[eid].get('basis') == 'fulltext' and not introductory(evidence[eid]) for eid in ids))
    return all(any(full(c) for f in fields for c in article.analysis.get(f, []) or []
        if isinstance(c, dict)) for fields in CORE_FIELDS.values())


def source_label(entry):
    """What is actually known of a source, never more (run bcf1d7ad: « texte disponible »
    was written for a review whose three downloads had failed)."""
    if entry["status"] == "analysed":
        if entry["basis"] == "fulltext":
            missing = [GAP_LABELS.get(d, d) for d in entry.get("missing", [])]
            return "texte intégral analysé" + (f" ; {', '.join(missing)} non documentés" if missing else "")
        if entry["basis"] == "introductory":
            return "résumé ou introduction du texte intégral seulement ; méthodes, résultats et limites non lus"
        label = "analyse fondée sur le résumé"
        if entry.get("text_available"):
            return label+" ; texte intégral disponible, non encore analysé"
        return label+(" ; texte intégral non obtenu" if entry.get("download_failed") else " ; texte intégral non encore recherché")
    if entry["status"] == "card_without_proofs":
        return "fiche sans preuve exploitable"
    if entry.get("text_available"):
        return "texte intégral disponible, non analysé"
    if entry.get("download_failed"):
        return "texte intégral non obtenu ; aucun résultat attribué"
    if not entry.get("has_abstract"):
        return "notice seule, texte intégral non obtenu ; aucun résultat attribué"
    return "texte intégral non encore recherché ; aucun résultat attribué"


def assess_quality(sources, actions):
    """basis: what the analysed sources rest on; scientific_state: prepared only when every
    planned source is analysed on its full text with methods, results and limits."""
    analysed = [r for r in sources.values() if r["status"] == "analysed"]
    fulltext = [r for r in analysed if r["basis"] == "fulltext"]
    basis = ("none" if not analysed else "fulltext" if len(fulltext) == len(analysed) else
        "abstract_only" if not fulltext else "mixed")
    insufficient = [{"article_id": aid, "label": r["label"]} for aid, r in sources.items()
        if not (r["status"] == "analysed" and r["basis"] == "fulltext" and r.get("state") == "ready")]
    if any(a.get("action", "").startswith("compare_articles") for a in actions):
        insufficient.append({"article_id": None, "label": "travaux analysés jamais confrontés"})
    return {"basis": basis, "insufficient": insufficient,
        "scientific_state": "prepared" if basis == "fulltext" and not insufficient else "limited"}


def section_quality(state, section, toolkit=None):
    """Recomputed from the current cards and documents: a saved text is never modified."""
    prep = section_preparation(state, toolkit or readiness_toolkit(state), section)
    return {"section_id": section["id"], "basis": prep["basis"], "scientific_state": prep["scientific_state"],
        "writable": prep["ready"], "insufficient": prep["insufficient"],
        "sources": {aid: r["label"] for aid, r in prep["sources"].items()}}


def download_failed(article):
    """Recorded download attempts without any text (run b748605d: 386c, two HTTP 403).
    A new attempt needs candidate_urls; it is not a preparation step to impose."""
    attempts = article.fulltext.get("attempts") or []
    return not article.fulltext.get("text") and any(a.get("http_status") or a.get("error") for a in attempts
        if a.get("stage") != "oa_resolution")


def uncarded_sources(state, toolkit, section, args):
    """write_section sources without a scientific card, split by whether the text cites them.

    V4 refuses the whole section when one declared source has no card (run
    b748605d). A source cited with [paper_ID] would be presented as analysed: V5
    refuses it with the source and the possible action. A source only declared is
    removed from article_ids; it stays in the plan and is stated as a documentary
    limit of the section. No citation is removed and no proof is added.
    """
    text = " ".join([str(args.get("text") or ""), *(str(p.get("text", "")) for p in args.get("paragraphs") or []
        if isinstance(p, dict))])
    readiness = section_readiness(state, toolkit, section)
    cited, declared = [], []
    for aid in dict.fromkeys(args.get("article_ids") or []):
        article = toolkit.registry.get(aid)
        if article is None or article.analysis:
            continue
        row = readiness.get(aid, {})
        accessible = bool(article.fulltext.get("text")) or not download_failed(article)
        entry = {"article_id": aid, "title": article.title[:200], "status": row.get("status", "not_in_section"),
            "action": ("analyser la source (analyze_article read puis save) puis citer ses preuves, ou retirer le marqueur"
                if accessible else "retirer le marqueur [%s] : la source reste au plan comme limite documentaire, "
                "ajoutée automatiquement à la section" % aid)}
        (cited if f"[{aid}]" in text or aid in text else declared).append(entry)
    return cited, declared


def plan_approved(state):
    """The consultant approved this very revision of the plan (an edit withdraws it)."""
    outline = state.get("outline") or {}
    approval = outline.get("approval") or {}
    return bool(approval) and approval.get("plan_revision", outline.get("plan_revision")) == outline.get(
        "plan_revision", approval.get("plan_revision"))


PLAN_APPROVAL_REQUIRED = ("Le plan n'est pas encore validé par le consultant : aucune section n'est enregistrée avant "
    "sa validation. En attendant, préparer les sources (analyze_article index/read/save, get_fulltext) et les "
    "comparaisons (compare_articles) ; terminer par un point sur la préparation.")


def save_ready(state):
    """The current section is scientifically prepared and may be saved now."""
    section = next_writing_section(state) if writing_active(state) else None
    return bool(section and plan_approved(state) and section_preparation(state, readiness_toolkit(state), section)["ready"])


def dossier_in_context(state, args):
    """write_section(read) of the section whose dossier is already sent with each decision.

    Run 152fc86a: three reads returned the same 16,000 characters (plus a V4
    guidance asking for more readings) next to the injected 40,000-character
    dossier; they filled the exchange, deferred the claims Sol had asked for and
    ended in stagnation. The dossier stays the single copy."""
    section = next_writing_section(state) if writing_active(state) else None
    if args.get("action") != "read" or section is None:
        return None
    from .tools.outline import resolve_section
    try:
        if resolve_section(state, args.get("section_id"))["id"] != section["id"]:
            return None
    except ValueError:
        return None
    prep = section_preparation(state, readiness_toolkit(state), section)
    return {"not_executed": True, "reason": "dossier_already_in_context", "section_id": section["id"],
        "preparation_ready": prep["ready"], "actions": prep["actions"][:8],
        "guidance": ("Le dossier complet de cette section (fiches, preuves, relations, limites) est déjà fourni dans le "
            "contexte : « Dossier scientifique de la section à rédiger ». "
            + (PLAN_APPROVAL_REQUIRED if not plan_approved(state) else
               f"Préparation suffisante : rédiger et enregistrer write_section(action='save', section_id='{section['id']}')."
               if prep["ready"] else "Réaliser d'abord les actions de préparation listées."))}


def preparation_gaps(state, toolkit, section):
    """Sources the section cites with undocumented core dimensions (stated, not silent)."""
    prep = section_preparation(state, toolkit, section)
    return [{"article_id": aid, "title": toolkit.registry[aid].title[:200], "missing": row.get("missing", [])}
        for aid, row in prep["sources"].items() if row["status"] == "analysed" and row.get("state") != "ready"]


def section_limits(state, toolkit, section):
    """Associated sources without a usable card: stated limits, never silent omissions."""
    return [{"article_id": aid, "title": toolkit.registry[aid].title[:200], "status": row["status"], "basis": row["basis"]}
        for aid, row in section_readiness(state, toolkit, section).items() if row["status"] != "analysed"]


def missing_section_sources(state, toolkit, section, raw):
    """Analysed sources of the section absent from a proposed text.

    V4 accepts [paper_ID] only for analysed works declared in article_ids, and
    its attribution check refuses naming an unread work. Analysed sources must
    therefore be cited; the others become the section's documentary limits
    (section_limits). Run 572ad911: two of five analysed works disappeared.
    """
    text = " ".join([str(raw.get("text") or ""), *(str(p.get("text", "")) for p in raw.get("paragraphs") or []
        if isinstance(p, dict))])
    return [{"article_id": aid, "title": toolkit.registry[aid].title[:160], "status": row["status"], "basis": row["basis"]}
        for aid, row in section_readiness(state, toolkit, section).items() if row["status"] == "analysed" and aid not in text]


def record_section_limits(state, toolkit, section_id):
    from .tools.outline import resolve_section
    section = resolve_section(state, section_id)
    limits = section_limits(state, toolkit, section)
    state.setdefault("v5_section_limits", {})[section["id"]] = limits
    state.setdefault("v5_section_gaps", {})[section["id"]] = preparation_gaps(state, toolkit, section)
    draft = (section.get("drafts") or [{}])[-1]
    state.setdefault("v5_section_quality", {}).setdefault(section["id"], []).append(
        {**section_quality(state, section, toolkit), "draft_id": draft.get("id")})
    return limits


def preparation_refusal(state, toolkit, section):
    """At most one refusal per section and request: then the save goes through and the
    remaining gaps are stated with the section (no loop, no fixed reading quota)."""
    prep = section_preparation(state, toolkit, section)
    restrictions = set((state.get('turn_goal', {}).get('references') or {}).get('constraints') or [])
    untried = [a for a in prep['actions'] if a.get('kind')=='network' and
        prep['sources'].get(a.get('article_id'),{}).get('access_state')=='not_requested']
    local = [a for a in prep['actions'] if a.get('kind')=='local' and a.get('article_id') and
        prep['sources'].get(a.get('article_id'),{}).get('text_available') and
        (prep['sources'][a['article_id']]['status']!='analysed' or prep['sources'][a['article_id']]['basis']!='fulltext')]
    if untried and 'no_download' not in restrictions or local:
        return {'saved':False,'rejection_code':'section_preparation_required','preparation':prep,
            'error':'Récupérer les textes des sources déjà sélectionnées, puis analyser les dimensions utiles avant '
                'la sauvegarde. search_needed=False limite la découverte, pas get_fulltext. '
                'Un échec documenté autorise une section limitée ; une source non recherchée ne prouve aucune inaccessibilité.'}
    if state.get("v5_finalization_mode"):
        return None  # Only genuinely unavailable work may be saved with limitations.
    if prep["ready"]:
        return None
    request = current_request(state).get("id")
    seen = state.setdefault("v5_section_preparation", {})
    record = seen.get(section["id"])
    if not isinstance(record, dict) or record.get("request_id") != request:
        record = {"request_id": request, "refusals": 0}
    # A local text or comparison can always be done: up to LOCAL_REFUSALS. A
    # download may fail: one refusal. Then the section is saved as provisional.
    local = any(a.get("kind") == "local" for a in prep["actions"])
    if record["refusals"] >= (LOCAL_REFUSALS if local else 1):
        return None
    record["refusals"] += 1
    seen[section["id"]] = record
    return {"saved": False, "rejection_code": "section_preparation_required", "preparation": prep,
        "error": "Préparation scientifique incomplète pour cette section : réaliser d'abord les actions listées "
            "(lectures ciblées, enrichissement des fiches, comparaison), puis réenregistrer avec un texte révisé. Si elles "
            "sont impossibles ou inutiles pour la question : write_section(action='save', section_id, text=null) "
            "enregistre le texte proposé, et les lacunes restantes sont signalées avec la section."}


def proposed_section(state, section_id, args):
    """A refused proposal confirmed by save with text=null (not resent); V4 validates it again."""
    pending = provisional_section(state, section_id)
    if args.get("action") == "save" and args.get("text") is None and pending:
        args.update({k: v for k, v in pending["arguments"].items() if k != "section_id"})
        return True
    return False


def provisional_section(state, section_id):
    pending = (state.get("v5_pending_sections") or {}).get(section_id)
    if pending and pending.get("plan_revision") == state.get("outline", {}).get("plan_revision"):
        return pending
    return None


def keep_proposed_section(state, section_id, args, reason=None):
    """The last refused text of a section, for a resumption without rewriting it.
    It is not a draft: assembled_document only shows validated drafts."""
    if args.get("text") is None:
        return
    state.setdefault("v5_pending_sections", {})[section_id] = {"request_id": current_request(state).get("id"),
        "plan_revision": state.get("outline", {}).get("plan_revision"), "refusal": str(reason or "")[:800],
        "arguments": copy.deepcopy(args)}


def drop_provisional_section(state, section_id):
    (state.get("v5_pending_sections") or {}).pop(section_id, None)


def record_section_refusal(state, section_id, reason, limit):
    """Bounded corrections per section; then the section is set aside, others continue."""
    attempts = state.setdefault("v5_section_attempts", {})
    attempts[section_id] = attempts.get(section_id, 0)+1
    progress = writing_progress(state)
    if progress is not None and attempts[section_id] >= limit:
        progress.setdefault("blocked", {})[section_id] = str(reason)[:500]
        return True
    return False


GAP_LABELS = {"methods": "méthodes", "results": "résultats", "limits": "limites"}
LOCAL_REFUSALS = 2
RELAUNCH_REQUEST = " Demandez « continue la rédaction » pour reprendre les sections restantes ; les sections enregistrées sont conservées."
MISSION_NOTE = (" Rédaction automatique : les sections restantes sont reprises sans nouvelle demande, dans le budget de "
    "la mission ; l'avancement est affiché avec la conversation.")
BASIS_LABELS = {"fulltext": "textes intégraux analysés", "mixed": "textes intégraux et résumés",
    "abstract_only": "résumés seulement", "none": "aucune source analysée"}


def shortfall(quality):
    """Why a section is provisional, in one phrase."""
    missing = [r for r in quality["insufficient"] if r["article_id"]]
    parts = [f"{len(missing)} source(s) sur {len(quality['sources'])} sans analyse complète du texte intégral"] if missing else []
    if len(missing) != len(quality["insufficient"]):
        parts.append("travaux analysés jamais confrontés")
    return " ; ".join(parts)+f" ; base : {BASIS_LABELS[quality['basis']]}"
LIMIT_LABELS = {"to_analyse": "texte disponible, non analysé", "card_without_proofs": "fiche sans preuve exploitable",
    "unavailable": "texte inaccessible"}


def assembled_document(state):
    """The saved sections of the plan, in plan order (V4 rendered drafts)."""
    progress = writing_progress(state) or (state.get("document") or {}).get("writing_progress") or {}
    targets = set(progress.get("target_ids") or [])
    toolkit = readiness_toolkit(state)
    parts = []
    for section in state.get("outline", {}).get("sections", []):
        if targets and section["id"] not in targets or not section.get("drafts"):
            continue
        draft = section["drafts"][-1]
        parts.append(f"## {section['title']}\n\n{without_title(draft['text'], section['title'])}"
            f"{documentary_note(state, section, toolkit)}")
    return "\n\n".join(parts)


def without_title(text, title):
    """Sol's text sometimes starts with the section title that the manuscript adds."""
    import re
    norm = lambda s: re.sub(r"[\W_]+", " ", s).strip().casefold()
    lines = str(text).lstrip().split("\n", 1)
    if lines and lines[0].lstrip().startswith("#") and norm(lines[0].lstrip("# ")) == norm(title):
        return lines[1].lstrip("\n") if len(lines) > 1 else ""
    return text


def documentary_note(state, section, toolkit):
    """Documented facts, interpretations and unverified information of a saved section."""
    quality = section_quality(state, section, toolkit)
    titles = {aid: toolkit.registry[aid].title for aid in quality["sources"] if aid in toolkit.registry}
    def group(predicate):
        return [f"« {titles.get(aid, aid)[:160]} »" for aid, label in quality["sources"].items() if predicate(label)]
    lines = []
    if quality["scientific_state"] == "limited":
        lines.append(f"Section provisoire : couverture scientifique insuffisante ({shortfall(quality)}).")
    full = group(lambda label: label.startswith("texte intégral analysé"))
    if full:
        lines.append("Faits documentés par le texte intégral : " + ", ".join(full) + ".")
    abstract = group(lambda label: label.startswith("analyse fondée sur le résumé"))
    if abstract:
        lines.append("Analyse fondée sur le résumé (méthodes, paramètres, résultats détaillés et limites non vérifiés) : "
            + ", ".join(abstract) + ".")
    limits = [f"« {titles.get(aid, aid)[:160]} » ({label})" for aid, label in quality["sources"].items()
        if not label.startswith(("texte intégral analysé", "analyse fondée sur le résumé"))]
    if limits:
        lines.append("Limites documentaires de la section : " + " ; ".join(limits) + ".")
    gaps = [f"« {titles.get(aid, aid)[:160]} » ({label.split(' ; ', 1)[1]})" for aid, label in quality["sources"].items()
        if label.startswith("texte intégral analysé ;")]
    if gaps:
        lines.append("Informations non documentées dans les fiches : " + " ; ".join(gaps) + ".")
    ids = set(quality["sources"])
    inferences = [r for r in state.get("scientific_relations", []) if not r.get("stale") and set(r["article_ids"]) <= ids
        and r.get("origin") == "comparative_inference"]
    if inferences:
        lines.append(f"Interprétations : {len(inferences)} relation(s) entre travaux, inférences comparatives fondées sur "
            "les preuves citées" + (" ; protocoles non comparables, sans classement" if any(
                r.get("comparable_protocols") is not True for r in inferences) else "") + ".")
    return "".join(f"\n\n*{line}*" for line in lines)


def provisional_sections(state, progress):
    sections = {s["id"]: s for s in state.get("outline", {}).get("sections", [])}
    toolkit = readiness_toolkit(state)
    rows = []
    for sid in progress.get("saved_draft_ids", {}):
        if sid in sections:
            quality = section_quality(state, sections[sid], toolkit)
            if quality["scientific_state"] != "prepared":
                rows.append({"section_id": sid, "title": sections[sid]["title"], **quality})
    return rows


def writing_result(state, stop_reason=None):
    """Local publication of a drafting request: V4's progress answer and the manuscript."""
    from .writing_progress import refresh_writing, writing_answer
    progress = refresh_writing(state, final=True)
    header = writing_answer(state, stop_reason) or ""
    document = assembled_document(state)
    provisional = provisional_sections(state, progress)
    # A draft is complete only if its sections are scientifically prepared, not
    # merely saved (run bcf1d7ad: section 2 rested on three abstracts).
    complete = progress["status"] == "draft_complete" and not progress.get("blocked") and not provisional
    if provisional:
        header += ("\n\n*Sections provisoires, couverture scientifique insuffisante : " + " ; ".join(
            f"{row['title']} ({shortfall(row)})" for row in provisional)
            + ". Elles seront enrichies si de nouveaux textes intégraux deviennent disponibles.*")
    if document and not state.get("outline", {}).get("approval"):
        header += ("\n\n*Plan proposé, non encore validé par le consultant : ce brouillon reste à valider "
            "avant la finalisation du dossier.*")
    return {"answer": header+("\n\n---\n\n"+document if document else ""), "citations": [],
        "partial": not complete, "coverage_sufficient": complete, "writing_progress": copy.deepcopy(progress),
        "provisional_sections": [{k: row[k] for k in ("section_id", "basis", "scientific_state", "insufficient")}
            for row in provisional],
        "displayed_article_ids": list(dict.fromkeys(aid for s in state.get("outline", {}).get("sections", [])
            if s["id"] in progress.get("target_ids", []) for aid in s.get("article_ids", [])))}


# --- Plan obligations -----------------------------------------------------------

def plan_saved_for_request(state):
    start = state.get("v5_request_start") or {}
    if start.get("request_id") != current_request(state).get("id"):
        return bool(state.get("outline", {}).get("sections"))
    return state.get("outline", {}).get("plan_revision") != start.get("plan_revision") and bool(
        state.get("outline", {}).get("sections"))


def plan_required(state):
    goal = state.get("turn_goal") or {}
    if goal.get("deliverable") in PLAN_DELIVERABLES and not plan_saved_for_request(state):
        return True
    pending = state.get("v5_pending_draft") or {}
    if pending.get("request_id") == current_request(state).get("id") and not plan_saved_for_request(state):
        return True  # « Ajoute une section … puis rédige … » : the plan edit comes first.
    # « Recherche, propose un plan et rédige » : V4 opened the drafting in
    # awaiting_plan; without a saved plan nothing can be drafted section by section.
    progress = writing_progress(state)
    return bool(progress and progress.get("status") == "awaiting_plan" and not state.get("outline", {}).get("sections")
        and state.get("articles"))


def plan_unchanged_allowed(state):
    """A request about an existing plan may end without changing it, once the plan gate refused.

    « Je veux modifier le plan déjà validé » gives no change to make: Sol must be able to
    ask for the precision, or state why a change is impossible, instead of being refused
    until the run fails. A new plan (no saved plan) is always saved first. The answer then
    says the plan is unchanged (v5_plan_unchanged)."""
    if not state.get("outline", {}).get("sections"):
        return False
    request = current_request(state).get("id")
    record = state.get("v5_plan_gate") or {}
    if (state.get("turn_goal") or {}).get("references_ambiguous") or (
            record.get("request_id") == request and record.get("refusals", 0) >= 1):
        state["v5_plan_unchanged"] = request
        return True
    state["v5_plan_gate"] = {"request_id": request, "refusals": (record.get("refusals", 0) if record.get("request_id") == request else 0)+1}
    return False


def explicit_plan_request(state):
    """The consultant's current message asks for a change of the existing plan.

    Such an edit by Sol is the consultant's own (V4 consultant edit): applied, with the
    previous version kept and the approval withdrawn, to be given again in the État de
    l'art space. V4 accepted it only for a plan_revision goal; a plan goal for this very
    message, or a request that lexically edits the plan, left an invisible proposal that
    nothing could accept (and blocked the publication of a plan goal)."""
    if not (state.get("outline") or {}).get("sections"):
        return False
    request = current_request(state)
    from .writing_progress import plan_edit_request
    if request.get("id") and plan_edit_request(state, request.get("content", "")):
        return True
    goal = state.get("turn_goal") or {}
    return bool(request.get("id") and goal.get("deliverable") in PLAN_DELIVERABLES
        and (goal.get("references") or {}).get("message_id") == request.get("id"))


PLAN_REVALIDATION = ("*Plan modifié à votre demande : la validation précédente est retirée et la version antérieure "
    "est conservée. Validez le plan modifié dans l'espace État de l'art avant toute nouvelle rédaction ; "
    "les sections déjà rédigées restent inchangées.*")
PLAN_TO_VALIDATE = "*Plan modifié : à valider dans l'espace État de l'art avant toute rédaction.*"
PLAN_UNCHANGED = "*Plan inchangé pour cette demande.*"
PLAN_PROPOSED = "*Plan proposé : à valider dans l'espace État de l'art avant toute rédaction.*"


def plan_note(state):
    """What the answer says about the plan after a plan request (None when nothing applies)."""
    note = _plan_note(state)
    pending = state.get("v5_pending_draft") or {}
    if not pending:
        return note
    asked = f"« {pending.get('clause', '').strip().rstrip('.')} »"
    if pending.get("request_id") == current_request(state).get("id"):
        if note == PLAN_UNCHANGED or note is None:
            return PLAN_UNCHANGED + f" *La rédaction demandée ({asked}) n'est pas lancée.*"
        return note + (f" *La rédaction demandée ({asked}) reprendra après votre validation du plan : demandez "
            "alors « Continue la rédaction ».*")
    if not plan_approved(state):
        return (note + " " if note else "") + (f"*Plan à valider : la rédaction demandée ({asked}) reprendra "
            "après votre validation du plan.*")
    return note


def _plan_note(state):
    start = state.get("v5_request_start") or {}
    if start.get("request_id") != current_request(state).get("id"):
        return None
    outline = state.get("outline") or {}
    if outline.get("sections") and outline.get("plan_revision") != start.get("plan_revision") and not plan_approved(state):
        if start.get("plan_revision") is None:
            return PLAN_PROPOSED
        return PLAN_REVALIDATION if start.get("plan_approved") else PLAN_TO_VALIDATE
    if state.get("v5_plan_unchanged") == start.get("request_id") and outline.get("plan_revision") == start.get("plan_revision"):
        return PLAN_UNCHANGED
    return None


def plan_readiness(state, toolkit):
    """source_readiness of every planned section, for Sol and the consultant."""
    rows = []
    for s in state.get("outline", {}).get("sections", []):
        prep = section_preparation(state, toolkit, s)
        rows.append({"section_id": s["id"], "title": s["title"][:160], "sources": section_readiness(state, toolkit, s),
            "preparation": {"ready": prep["ready"], "actions": prep["actions"][:12]}})
    return rows


# --- What was delivered for the current request ----------------------------------

def knowledge(state):
    request = current_request(state).get("id")
    record = state.get("v5_request_knowledge") or {}
    if record.get("request_id") != request:
        record = {"request_id": request, "keys": [], "failed_sources": []}
        state["v5_request_knowledge"] = record
    return record


def record_delivery(state, decision, output, *, confirmed=True):
    """Stored knowledge Sol actually received for this request.

    Reading saved cards, the plan, a section dossier or the conversation is
    progress for writing (run 572ad911 stopped as "stagnation" after reading four
    cards). Identical replays are served from receipts and add nothing, so true
    loops stay without progress.
    """
    if not isinstance(output, dict) or output.get("error") or output.get("not_executed"):
        return
    record = knowledge(state)
    keys = delivery_keys(decision, output)
    tool, args = decision.tool, decision.arguments
    if tool == "get_fulltext" and not output.get("reused_local"):
        if output.get("access_status") == "access_blocked" or (output.get("access_level") != "fulltext" and output.get("attempts")):
            record["failed_sources"] = sorted(set(record["failed_sources"]) | {args.get("article_id")})
    if tool == "get_article":
        # Notice receipts, per work and version, as transmitted (not as acknowledged later):
        # reused by policy.known_notices only once Sol actually received them.
        from experiments.ennoscholar_v5.policy import notice_state
        prepared = record.setdefault("prepared_notices", {})
        for a in output.get("articles", []):
            if isinstance(a, dict) and a.get("id") in state.get("articles", {}):
                if confirmed:
                    record.setdefault("notices", {})[a["id"]] = prepared.pop(a["id"], None) or notice_state(state, a["id"])
                else:
                    prepared[a["id"]] = notice_state(state, a["id"])
    if confirmed:
        record["keys"] = sorted(set(record["keys"]) | {k for k in keys if k})
        if tool == "analyze_article" and output.get("indexes"):
            # Exact cursors of the index pages actually received (P3 of run 48d2c500).
            cursors = record.setdefault("index_cursors", {})
            for row in output["indexes"]:
                if isinstance(row, dict) and row.get("article_id"):
                    cursors.setdefault(row["article_id"], {})[str(row.get("section_filter") or "")] = {
                        "next_offset": row.get("next_offset"), "remaining": row.get("remaining_after_page"),
                        "total": row.get("total")}
        if tool == "compare_articles" and output.get("packet") and args.get("relations") is None and args.get("conclusions") is None:
            record.setdefault("matrices", {})[matrix_key(state, args)] = (output.get("packet") or {}).get("next_claim_offsets") or {}


def delivery_keys(decision, output):
    """Identity of each scientific item a result carries (claim, passage, index…)."""
    tool, args, keys = decision.tool, decision.arguments, []
    if tool == "get_analysis_claims":
        # Content identity, not the position in this page: a field subset renumbers
        # the same claims (run f147eded) and must not look like new knowledge.
        keys = [f"claim:{output.get('article_id')}:{claim_receipt(c.get('field'), c)}" for c in output.get("claims", [])]
        keys += [f"measurement:{output.get('article_id')}:{m.get('id', fingerprint(m))}" for m in output.get('structured_measurements', [])]
    elif tool == "read_conversation":
        keys = [f"message:{m.get('id')}" for m in output.get("messages", [])]
    elif tool == "manage_outline":
        outline = output.get("outline") or {}
        keys = [f"outline:{outline.get('plan_revision', outline.get('version'))}:{args.get('offset', 0)}"]
    elif tool == "write_section" and args.get("action") == "read":
        keys = [f"dossier:{(output.get('section') or {}).get('id')}:{fingerprint(output.get('writing_context'))}"]
    elif tool == "get_article":
        keys = [f"notice:{a.get('id')}" for a in output.get("articles", []) if isinstance(a, dict)]
    elif tool == "analyze_article" and output.get("reading"):
        keys = [f"passage:{p.get('evidence_id')}" for r in output["reading"] for p in r.get("passages", [])]
    elif tool == 'analyze_article' and output.get('indexes'):
        # Credit each locator once, independent of window, filter or order.
        # Source changes remain distinct (the N05 identity issue is not migrated).
        keys = ['index:'+fingerprint([r['article_id'], r['source_fingerprint'], p['evidence_id']])
            for r in output['indexes'] for p in r['passages']]
    elif tool == "compare_articles":
        # research_context is echoed in the output: a reworded request for the same
        # matrix is not new knowledge (run fd41608c: four identical matrices).
        keys = [f"claim:{aid}:{claim_receipt(field, c)}" for aid, field, c in comparison_claims(output)]
        keys += [f"relation:{r.get('id')}" for r in output.get("scientific_relations", []) if isinstance(r, dict)]
        if output.get("saved"):
            keys.append(f"compare_saved:{fingerprint(output.get('scientific_relations'))}")
    elif tool in {"build_argument_blueprint", "review_literature_coverage", "review_document"}:
        keys = [f"{tool}:{fingerprint(output)}"]
    return [k for k in keys if k]


def comparison_claims(output):
    """(article_id, field, claim) carried by a compare_articles result."""
    rows = [*(output.get("matrix") or []), *((output.get("packet") or {}).get("papers") or [])]
    found = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        for field, claims in (row.get("analysis") or {}).items():
            if isinstance(claims, list):
                found += [(row.get("id"), field, c) for c in claims if isinstance(c, dict) and c.get("statement")]
    return found


def novel_delivery(state, decision, output):
    """True only when a result carries items never delivered in this request.

    Run f147eded: decisions 4-8 re-requested saved cards by field subsets; 229
    claims were sent again, none new, yet each call was credited as progress and
    stagnation never triggered. Claims are identified by their content receipt,
    so a field subset or another page of known claims is not new. Items prepared
    earlier in the same response count once.
    """
    if not isinstance(output, dict) or output.get("error") or output.get("not_executed"):
        return False
    record = knowledge(state)
    keys = set(delivery_keys(decision, output))
    known = set(record.get("keys", [])) | set(record.get("prepared", []))
    new = keys-known
    claims = ([(output.get("article_id"), c.get("field"), c) for c in output.get("claims", [])]
        if decision.tool == "get_analysis_claims" else comparison_claims(output) if decision.tool == "compare_articles" else [])
    if claims:
        fresh = set()
        for aid, field, claim in claims:
            receipt = claim_receipt(field, claim)
            seen = set(record.get("cards", {}).get(aid, [])) | set(record.get("prepared_claims", {}).get(aid, []))
            if receipt not in seen:
                fresh.add(receipt)
            record.setdefault("prepared_claims", {})[aid] = sorted(seen | {receipt})
        new = {k for k in new if not k.startswith("claim:")} | fresh
    record["prepared"] = sorted(set(record.get("prepared", [])) | keys)
    return bool(new)


def claim_receipt(field, claim):
    return fingerprint([field, claim.get('statement'), claim.get('evidence_ids', [])])


def acknowledge_inputs(state, inputs):
    """Only a received Responses exchange acknowledges these exact payloads."""
    from .runtime import MEMORY_PREFIX
    from .science import task_for
    from .toolkit import transmitted_evidence_ids
    record = knowledge(state)
    cards = record.setdefault('cards', {})
    # Full claims in THIS received context, not merely somewhere in old memory.
    # A clipped working-memory preview cannot replace a requested exact claim.
    present = record['in_context_claims'] = {}
    calls = {i['call_id']:i for i in inputs if i.get('type')=='function_call'}
    for item in inputs:
        content = item.get('content', '')
        if isinstance(content, str) and content.startswith(MEMORY_PREFIX):
            memory = json.loads(content[len(MEMORY_PREFIX):])
            for card in [*memory.get('selected_articles', []), *memory.get('consulted_articles', [])]:
                known = cards.setdefault(card['id'], [])
                for field, claims in (card.get('analysis') or {}).items():
                    if isinstance(claims, list):
                        known.extend(claim_receipt(field,c) for c in claims if isinstance(c,dict))
                        present.setdefault(card['id'],[]).extend(claim_receipt(field,c) for c in claims if isinstance(c,dict))
                cards[card['id']] = sorted(set(known))
        if item.get('type') == 'function_call_output' and item.get('call_id') in calls:
            call = calls[item['call_id']]
            args = json.loads(call.get('arguments') or '{}')
            output = json.loads(item['output'])
            record_delivery(state, SimpleNamespace(tool=call['name'], arguments=args), output)
            if call['name'] == 'get_analysis_claims' and output.get('article_id') and not output.get('error'):
                known = cards.setdefault(output['article_id'], [])
                known.extend(claim_receipt(c['field'],c) for c in output.get('claims', []))
                present.setdefault(output['article_id'],[]).extend(claim_receipt(c['field'],c) for c in output.get('claims',[]))
                cards[output['article_id']] = sorted(set(known))
            if call['name'] == 'compare_articles' and not output.get('error') and not output.get('not_executed'):
                for aid, field, claim in comparison_claims(output):
                    cards[aid] = sorted(set(cards.get(aid, [])) | {claim_receipt(field, claim)})
    receipts = [{**item, 'content':item['content'][len(DOSSIER_PREFIX):]} if
        isinstance(item.get('content'),str) and item['content'].startswith(DOSSIER_PREFIX) else item for item in inputs]
    actual = transmitted_evidence_ids(receipts, state.get('evidence', {}))
    # Keep only IDs, in delivery order, rather than another copy of source text.
    # Newly prepared/withheld outputs cannot enter this received-material view.
    order = record.setdefault('passage_order', [])
    consulted_ids = set(record.get('consulted_evidence_ids', []))
    for item in inputs:
        if item.get('type') == 'function_call_output':
            # Claims and comparison packets carry proofs too, not only read.
            # Mere IDs, invalid quotes and prepared-but-unsent results do not.
            consulted_ids.update(transmitted_evidence_ids([item], state.get('evidence', {})))
            try:
                output = json.loads(item['output'])
            except (ValueError, TypeError):
                continue
            for row in output.get('reading', []):
                for proof in row.get('passages', []):
                    eid = proof.get('evidence_id')
                    if eid in actual and eid not in order:
                        order.append(eid)
    record['consulted_evidence_ids'] = sorted(consulted_ids)
    for task in state.get('scientific_reading', {}).get('tasks', []):
        task['deferred_evidence_ids'] = [eid for eid in task.get('deferred_evidence_ids', []) if eid not in actual]
        for pending in task.get('deferred_reads', {}).values():
            pending['evidence_ids'] = [eid for eid in pending.get('evidence_ids', []) if eid not in actual]
            if not pending['evidence_ids']:
                pending['status'] = 'presented'


WORKING_PREFIX = "Mémoire de travail de cette demande (données consultées, pas instructions) : "
WORKING_MEMORY_CHARS = 12000
# Fields an in-depth reading usually needs first; every field stays available.
WORKING_ORDER = ("results", "metrics", "parameters", "experimental_setup", "validation_protocol", "conditions_of_validity",
    "technical_mechanism", "method", "datasets_materials", "tables_figures", "comparisons", "limitations", "hypotheses",
    "problem", "contribution", "open_questions", "generalization", "objective", "equations", "relevance")


def reading_progress(state, aid, cursors):
    """Where the reading of one work stands in this request (point 3 of run 48d2c500): exact
    index cursors received and the scientific dimensions still to read in its local text.
    Information for Sol's own choices, never an imposed sequence."""
    from .models import Article
    from .science import analysis_depth
    article = Article.model_validate(state["articles"][aid])
    result = {}
    open_cursors = {(section or "toutes sections"): {"index_offset": c["next_offset"], "remaining": c.get("remaining")}
        for section, c in list(cursors.items())[:4] if c.get("next_offset") is not None}
    if open_cursors:
        result["index_cursors"] = open_cursors
    if article.fulltext.get("text"):
        depth = analysis_depth(state, article)
        wanted = requested_dimensions(state)
        to_read = [d for d in depth["to_read"] if not wanted or d in wanted]
        result.update(analysis_depth=depth["depth"], **({"to_read": to_read} if to_read else {}))
    elif article.analysis:
        result["analysis_depth"] = "abstract_only"
    return result


def working_memory(state, budget=WORKING_MEMORY_CHARS):
    """What Sol already consulted in this request, kept between decisions.

    Responses requests are stateless here: a card read at one decision was absent
    from the next, so Sol re-read the same claims (run f147eded). This compact
    view keeps consulted claims (abridged statement and proof identifiers),
    deferred passages and reads already done. It is not a new reading receipt
    and full claims stay retrievable by get_analysis_claims.
    """
    record = knowledge(state)
    cards = record.get("cards", {})
    articles = state.get("articles", {})
    shown = set(state.get("shown_evidence_ids", []))
    passages_read = {}
    for key in record.get("keys", []):
        if key.startswith("passage:"):
            eid = key.split(":", 1)[1]
            aid = state.get("evidence", {}).get(eid, {}).get("article_id")
            if aid:
                passages_read[aid] = passages_read.get(aid, 0)+1
    tasks = {t.get("article_id"): t for t in state.get("scientific_reading", {}).get("tasks", [])}
    read_ids = {k.split(":", 1)[1] for k in record.get("keys", []) if k.startswith("passage:")}
    cursors = record.get("index_cursors") or {}
    rows, queues, excerpts = {}, {}, {}
    for aid in list(dict.fromkeys([*cards, *passages_read, *cursors,
            *(a for a, t in tasks.items() if t.get("deferred_evidence_ids"))])):
        article = articles.get(aid)
        if not article:
            continue
        analysis = article.get("analysis") or {}
        receipts = set(cards.get(aid, []))
        # A saved claim built on a passage read in this request is consulted too:
        # run fd41608c read the "14 h at 400 mA cm-2" passage, never its claim.
        consulted = [(field, c) for field in WORKING_ORDER for c in analysis.get(field, []) or []
            if isinstance(c, dict) and (claim_receipt(field, c) in receipts or set(c.get("evidence_ids", [])) & read_ids)]
        consulted.sort(key=lambda fc: not set(fc[1].get("evidence_ids", [])) & read_ids)  # Read passages first.
        cited = {eid for _, c in consulted for eid in c.get("evidence_ids", [])}
        excerpts[aid] = [(eid, sentence) for eid in sorted(read_ids-cited)
            if state.get("evidence", {}).get(eid, {}).get("article_id") == aid
            for sentence in quantified_sentences(state["evidence"][eid].get("exact_quote", ""))[:1]]
        deferred_ids = [e for e in tasks.get(aid, {}).get("deferred_evidence_ids", []) if e not in shown]
        rows[aid] = {"article_id": aid, "title": article.get("title", "")[:90], "consulted": [],
            "consulted_total": len(consulted), "passages_read_this_request": passages_read.get(aid, 0),
            **({"deferred_evidence_ids": deferred_ids[:8], "deferred_total": len(deferred_ids)} if deferred_ids else {}),
            **reading_progress(state, aid, cursors.get(aid) or {})}
        queues[aid] = consulted
    notices = sorted(set(record.get("notices") or {}) - set(rows))
    if not rows and not request_relations(state) and not notices:
        return None
    view = {"articles": list(rows.values()),
        **({"notices_received": notices[:40], "notices_note": "Notices déjà reçues dans cette demande : get_article les "
            "renverrait sous forme de reçu ; lire le texte avec analyze_article(index/read)."} if notices else {}),
        "use": "Affirmations déjà consultées : ne pas les redemander. Approfondir les lacunes de v5_work_state par "
            "analyze_article(action='index', section_filter) puis read, ou review ; les passages reportés se lisent par "
            "analyze_article(read, evidence_ids). get_analysis_claims seulement pour un détail absent d'ici."}
    relations = request_relations(state)
    if relations:
        view["saved_relations"] = [{"id": r["id"], "kind": r["kind"], "article_ids": r["article_ids"],
            "statement": r["statement"][:220], "comparable_protocols": r.get("comparable_protocols"),
            "conditions": str(r.get("conditions", ""))[:160], "evidence_ids": r["evidence_ids"][:4]} for r in relations[:8]]
    matrix = record.get("matrices") or {}
    if matrix:
        view["comparison_matrix"] = {"windows_received": len(matrix), "next_claim_offsets": list(matrix.values())[-1],
            "note": "Fenêtre déjà reçue : ne pas la redemander ; suite par claim_offsets=next_claim_offsets."}
    size = lambda: len(json.dumps(view, ensure_ascii=False))
    if size() > budget:
        return None
    # Round-robin across works so one long card cannot hide the others.
    index = 0
    while any(index < len(q) for q in queues.values()):
        full = False
        for aid, queue in queues.items():
            if index >= len(queue):
                continue
            field, claim = queue[index]
            statement = " ".join(str(claim.get("statement", "")).split())
            rows[aid]["consulted"].append({"field": field, "statement": statement[:220]+("…" if len(statement) > 220 else ""),
                "evidence_ids": list(claim.get("evidence_ids", []))[:3]})
            if size() > budget:
                rows[aid]["consulted"].pop()
                full = True
                break
        if full:
            break
        index += 1
    # Then quantified sentences of passages read without a saved claim yet.
    for aid, items in excerpts.items():
        for eid, sentence in items[:4]:
            rows[aid].setdefault("read_values", []).append({"evidence_id": eid, "excerpt": sentence})
            if size() > budget:
                rows[aid]["read_values"].pop()
                break
    for row in rows.values():
        row["consulted_not_shown_here"] = row["consulted_total"]-len(row["consulted"])
    while size() > budget:
        row = max(rows.values(), key=lambda r: len(r["consulted"]), default=None)
        if not row or not row["consulted"]:
            return None
        row["consulted"].pop()
        row["consulted_not_shown_here"] += 1
    return view


def quantified_sentences(text):
    """Sentences of an exact quote that carry a value with a unit (verbatim, abridged)."""
    import re
    sentences = re.split(r"(?<=[.;])\s+", " ".join(str(text).split()))
    unit = re.compile(r"\d+(?:[.,]\d+)?\s*(?:%|h\b|min\b|mA|A\b|V\b|mV|M\b|mM|°C|K\b|nm|µm|mg|g\b|mL|sccm|cm|wt|eV|bar)", re.I)
    return [s[:200]+("…" if len(s) > 200 else "") for s in sentences if unit.search(s)]


# --- Comparison obligations ---------------------------------------------------------

def request_article_ids(state):
    goal = state.get("turn_goal") or {}
    return list(dict.fromkeys((goal.get("references") or {}).get("article_ids") or goal.get("article_ids")
        or state.get("current_turn_article_ids") or []))


def request_relations(state):
    """Current saved relations among the works of this request."""
    from .argumentation import relation_current
    from .models import Article
    ids = set(request_article_ids(state))
    if len(ids) < 2:
        return []
    view = SimpleNamespace(registry={aid: Article.model_validate(state["articles"][aid]) for aid in ids if aid in state.get("articles", {})},
        evidence=state.get("evidence", {}), snapshots=state.get("source_snapshots", {}))
    return [r for r in state.get("scientific_relations", []) if set(r.get("article_ids", [])) <= ids and relation_current(view, r)]


def analytical_request(state):
    """A request whose purpose is to study works (not a documentary look-up)."""
    goal = state.get("turn_goal") or {}
    constraints = set(goal.get("constraints") or []) | set((goal.get("references") or {}).get("constraints") or [])
    if "no_reanalysis" in constraints or (goal.get("deliverable") == "answer" and goal.get("reading_depth") == "none"):
        return False
    return (goal.get("deliverable") == "comparison" or goal.get("reading_depth") == "deep"
        or (goal.get("deliverable") == "deep_reading" and goal.get("reading_depth") in {"core", "deep"}))


def cards_to_save(state, toolkit, args):
    """D2: works studied in this request and used by the answer, whose reading has no saved card.

    A card may be partial (only what the received passages support); an abstract-based card
    is updated when full-text passages were read in this request. Inline analyses of the same
    finalize call count as saved. Returns {article_id: received evidence_ids}."""
    if not analytical_request(state):
        return {}
    from .consulted import evidence_ids
    from .evidence import active_article
    evidence = state.get("evidence", {})
    received = {}
    for eid in evidence_ids(state):
        if eid in evidence:
            received.setdefault(evidence[eid]["article_id"], []).append(eid)
    cited = set(args.get("article_ids") or []) | {evidence[e]["article_id"] for e in args.get("evidence_ids") or [] if e in evidence}
    inline = {item.get("article_id") for item in args.get("analyses") or [] if isinstance(item, dict)}
    missing = {}
    for aid, eids in received.items():
        if aid not in cited or aid in inline or aid not in toolkit.registry:
            continue
        card = active_article(state, toolkit.registry[aid])
        claims = any(isinstance(v, list) and any(isinstance(c, dict) and c.get("statement") for c in v)
            for v in card.analysis.values())
        fulltext_read = any(evidence[e].get("basis") == "fulltext" for e in eids)
        if not claims or (card.analysis_basis != "fulltext" and fulltext_read):
            missing[aid] = eids
    return missing


_REQUESTED = {"methods": r"m[ée]thod|approche|approach|formulation|fabrication|synth[eè]se|pr[ée]paration|proc[ée]d",
    "conditions": r"condition|protocol|param[eè]tre|parameter|temp[ée]rature|setup|dispositif",
    "results": r"r[ée]sultat|valeur|mesur|performance|result|value|measur",
    "limits": r"limite|limitation|incertitude|uncertaint|weakness|faiblesse"}
_SECTIONS = {"methods": r"method|materials?\b|experimental|experiment|preparation|synthesis|fabrication|protocol|characteri[sz]|measurement|procedure|setup|mat[ée]riel|m[ée]thode|protocole|dispositif",
    "results": r"result|discussion|performance|r[ée]sultat"}
_SECTIONS["conditions"] = _SECTIONS["methods"]
_SECTIONS["limits"] = r"limit|conclusion|perspective|outlook|discussion"
_BACK_MATTER = r"acknowledg|author contribution|funding|conflicts? of interest|^references|data availability|informed consent|review board|footnote|associated data|roles\b|supplementary"


def requested_dimensions(state):
    """Dimensions the request asks about, plus what its objective needs (D3)."""
    from .instructions import current_request
    text = (current_request(state) or {}).get("content", "")
    goal = state.get("turn_goal") or {}
    wanted = {d for d, pattern in _REQUESTED.items() if re.search(pattern, text, re.I)}
    if goal.get("deliverable") == "comparison":
        wanted |= {"results", "conditions"}
    if goal.get("reading_depth") == "deep" or goal.get("deliverable") == "deep_reading":
        wanted |= {"methods", "results", "limits"}
    return sorted(wanted)


_CARD_FIELDS = {"methods": ("method",), "results": ("results", "metrics"), "limits": ("limitations",),
    "conditions": ("experimental_setup", "validation_protocol", "parameters", "conditions_of_validity", "datasets_materials")}
COVERED_SHARE = 0.25


def reading_gaps(state, toolkit, args):
    """D3: requested dimensions of a cited, studied work with a local full text that are neither
    documented by its card (or the inline analysis) nor read for at least a quarter of their
    passages. Lists the unread sections that answer them. {article_id: [gap, ...]}."""
    if not analytical_request(state):
        return {}
    from .evidence import passages, active_article
    wanted = requested_dimensions(state)
    if not wanted:
        return {}
    evidence = state.get("evidence", {})
    read = {e for e in state.get("shown_evidence_ids", []) if e in evidence}
    cited = set(args.get("article_ids") or [])
    inline = {item.get("article_id"): item.get("analysis") or {} for item in args.get("analyses") or [] if isinstance(item, dict)}
    gaps = {}
    for aid in studied_article_ids(state):
        article = toolkit.registry.get(aid)
        if aid not in cited or article is None or not article.fulltext.get("text"):
            continue
        from .science import analysis_depth, introductory
        coverage = analysis_depth(state, article)["coverage"]
        evidence = state.get("evidence", {})
        def inline_body(fields):
            # A claim of the inline card covers a dimension only through a proof from the body.
            return any(evidence.get(e, {}).get("basis") == "fulltext" and not introductory(evidence[e])
                for f in fields for c in (inline.get(aid) or {}).get(f) or [] if isinstance(c, dict)
                for e in c.get("evidence_ids") or [] if e in evidence)
        sections = {}
        for row in passages(article):
            name = str(row.get("section") or "")
            if re.search(_BACK_MATTER, name.split(" / ")[-1], re.I):
                continue
            counts = sections.setdefault(name, [0, 0])
            counts[1] += 1
            counts[0] += row["evidence_id"] in read
        found = []
        for dimension in wanted:
            if coverage.get(dimension) == "body" or inline_body(_CARD_FIELDS[dimension]):
                continue
            relevant = {n: c for n, c in sections.items() if re.search(_SECTIONS[dimension], n, re.I)}
            total = sum(c[1] for c in relevant.values())
            if not total or sum(c[0] for c in relevant.values()) >= COVERED_SHARE * total:
                continue
            found.extend({"section": n, "dimension": dimension, "read_passages": c[0], "passages": c[1]}
                for n, c in relevant.items() if c[0] < c[1] and n not in {g["section"] for g in found})
        if found:
            gaps[aid] = found[:8]
    return gaps


def documentary_status(state, toolkit, aid):
    """What can honestly be said of an unread work: inaccessible only after a failed retrieval."""
    article = toolkit.registry.get(aid)
    if article is None or article.fulltext.get("text"):
        return "texte intégral disponible"
    return "inaccessible (récupération échouée)" if download_failed(article) else "texte intégral non recherché"


READING_GAPS = ("Sections demandées non lues alors que le texte intégral est disponible : les lire avec "
    "analyze_article(read, section_filter) si elles sont utiles à la question, ou écrire explicitement « non lu » dans la "
    "réponse. Réserver « inaccessible » ou « non vérifiable » aux sources dont la récupération a échoué.")


SAVE_CARDS = ("Ces travaux ont été étudiés pour cette réponse sans fiche scientifique enregistrée (ou avec une fiche "
    "fondée sur le résumé alors que le texte intégral a été lu). Fournir analyses=[{article_id, analysis}] dans le même "
    "finalize_response : une fiche partielle suffit, avec seulement les affirmations étayées par les passages reçus "
    "(evidence_ids) et la limite de lecture ; les fiches existantes sont enrichies, jamais effacées.")


def studied_article_ids(state):
    """Works whose exact passages were received in this request (an index preview is not a read)."""
    from .consulted import evidence_ids
    evidence = state.get("evidence", {})
    return list(dict.fromkeys(evidence[e]["article_id"] for e in evidence_ids(state) if e in evidence))


def comparison_article_ids(state, cited=None):
    """Works of a requested comparison (D1): the declared references, plus the works this
    request studied or the answer cites, including works found during the request."""
    known = state.get("articles", {})
    extra = [*studied_article_ids(state), *(cited or [])]
    return list(dict.fromkeys([*request_article_ids(state), *(a for a in extra if a in known)]))


def comparison_required(state, cited=None):
    """A requested comparison is published after saved relations or an explicit no-relation assessment.

    The saved comparison must include every declared reference and at least one other work of
    the request; works discovered during the request count (run 259f55c8: one declared DOI, the
    compared study found by Sol, published with no relation)."""
    goal = state.get("turn_goal") or {}
    ids = comparison_article_ids(state, cited)
    if goal.get("deliverable") != "comparison" or len(ids) < 2:
        return False
    from .comparison_memory import current
    scope, references = set(ids), set(request_article_ids(state))
    for row in state.get("v5_comparison_decisions", []):
        compared = set(row.get("article_ids") or [])
        if len(compared) >= 2 and compared <= scope and references <= compared and current(state, row, row.get("article_ids") or []):
            return False
    return True


SAVE_RELATIONS = ("Enregistrer la comparaison : compare_articles(article_ids, research_context, relations=[{article_ids, kind, "
    "statement, origin, conditions, experimental_results, evidence_ids (au moins une preuve exacte de chaque publication), "
    "comparable_protocols, uncertainties}], conclusions, assessment). Une supériorité exige des protocoles comparables "
    "examinés (comparable_protocols=true) ; sinon kind alternative, complementarity ou partial_resolution avec les "
    "conditions et incertitudes. Si aucune relation n'est fiable : relations=[] et assessment explicite.")


def matrix_key(state, args):
    """Scientific identity of a read-only comparison window (not its wording)."""
    ids = sorted(dict.fromkeys(args.get("article_ids") or []))
    offsets = sorted((o.get("article_id"), o.get("offset")) for o in (args.get("claim_offsets") or []) if isinstance(o, dict))
    # Only dependencies of this matrix invalidate its delivery. Automatically
    # preparing an unrelated passage is not a scientific change to its claims.
    cards = {aid: (state.get('articles', {}).get(aid) or {}).get('analysis', {}) for aid in ids}
    refs = {eid for card in cards.values() for values in card.values() if isinstance(values, list)
        for claim in values if isinstance(claim, dict) for eid in claim.get('evidence_ids', [])}
    refs.update(p['evidence_id'] for card in cards.values() for p in card.get('evidence', []))
    proofs = {eid: state.get('evidence', {}).get(eid) for eid in sorted(refs)}
    snapshots = {p.get('source_fingerprint'): state.get('source_snapshots', {}).get(p.get('source_fingerprint'))
        for p in proofs.values() if p}
    return fingerprint([ids, offsets, cards, proofs, snapshots,
        [r.get("id") for r in state.get("scientific_relations", [])]])


def comparison_check(state, args):
    """Refusals before V4 compare_articles runs (None lets it run)."""
    reading = args.get("relations") is None and args.get("conclusions") is None
    if reading and matrix_key(state, args) in knowledge(state).get("matrices", {}):
        # Same works, same window, same cards and relations: the matrix was already
        # received in this request (run fd41608c re-read it four times, 33k chars each).
        offsets = knowledge(state)["matrices"][matrix_key(state, args)]
        return {"not_executed": True, "reason": "matrix_already_delivered", "article_ids": args.get("article_ids"),
            "next_claim_offsets": offsets, "scientific_relations": request_relations(state),
            "guidance": "Matrice déjà reçue dans cette demande (voir mémoire de travail). Pour la suite des affirmations : "
                "claim_offsets=next_claim_offsets ; pour un détail : analyze_article(read) ou get_analysis_claims. "+SAVE_RELATIONS}
    if args.get("relations") == [] and not str(args.get("assessment") or "").strip():
        return {"saved": False, "rejection_code": "comparison_assessment_required",
            "error": "Aucune relation enregistrée : expliquer dans assessment pourquoi aucune relation n'est fiable "
                "(protocoles non comparables, preuves manquantes…)."}
    return None


def after_comparison(state, args, result):
    """Read: say how to save. Save: keep the decision of this request, relations or not."""
    if args.get("relations") is None:
        if not result.get("not_executed"):
            result["save_with"] = SAVE_RELATIONS
        return result
    if result.get("saved"):
        from .comparison_memory import identity
        section = next_writing_section(state)
        ids = {r.get("id") for r in result.get("scientific_relations", [])}
        record = {"request_id": current_request(state).get("id"),
            "article_ids": list(args.get("article_ids") or []), "assessment": str(args.get("assessment") or "")[:1200],
            "relation_ids": sorted(i for i in ids if i), "no_reliable_relation": not args.get("relations"),
            'identity_version':1, 'identity':identity(state,args.get('article_ids') or [],section),
            'section_id':section.get('id') if section else None}
        from .argumentation import relation_key
        decisions = state.setdefault("v5_comparison_decisions", [])
        if not any(relation_key(old) == relation_key(record) for old in decisions):
            decisions.append(record)
        if not args.get("relations"):
            result["guidance"] = ("Aucune relation fiable enregistrée ; le bilan est conservé. La réponse doit le dire "
                "explicitement et ne pas classer les travaux.")
    return result


def compact_known_cards(state, inputs, *, final=False):
    """Saved data stays intact; repeat context contains references to known claims."""
    if final:
        return
    from .runtime import MEMORY_PREFIX
    cards = knowledge(state).get('cards', {})
    for item in inputs:
        text = item.get('content', '')
        if not isinstance(text,str) or not text.startswith(MEMORY_PREFIX):
            continue
        memory = json.loads(text[len(MEMORY_PREFIX):])
        for card in [*memory.get('selected_articles', []), *memory.get('consulted_articles', [])]:
            known = set(cards.get(card['id'], []))
            if not known:
                continue
            analysis = card.get('analysis') or {}
            for field, claims in list(analysis.items()):
                if isinstance(claims, list):
                    analysis[field] = [c for c in claims if claim_receipt(field,c) not in known]
            card['saved_card_ref'] = {'article_id':card['id'], 'known_claims':len(known),
                'consulted_claims':'mémoire de travail de cette demande',
                'retrieve':'get_analysis_claims(article_id, fields, offset) ; preuves par analyze_article(read, evidence_ids)'}
        item['content'] = MEMORY_PREFIX+json.dumps(memory, ensure_ascii=False)


def remember_deferred(state, decision, output):
    if decision.tool != 'analyze_article' or decision.arguments.get('action') != 'read':
        return
    from .science import task_for
    from .models import Article
    from .evidence import passages, choose_passages
    ids = output.get('withheld_evidence_ids') or {}
    ids = {aid:list(eids) for aid,eids in ids.items()} if isinstance(ids,dict) else {}
    for row in output.get('reading', []):
        ids.setdefault(row['article']['id'], []).extend(row.get('withheld_evidence_ids', []))
    if output.get('transmission_deferred') and not ids:
        for aid in decision.arguments['article_ids']:
            requested = decision.arguments.get('evidence_ids')
            if not requested:
                requested = [r['evidence_id'] for r in choose_passages(passages(Article.model_validate(state['articles'][aid])),
                    decision.arguments.get('passage_query',''), decision.arguments.get('passage_offset',0),
                    decision.arguments.get('passages_per_article',8))]
            ids[aid] = requested
    for aid,eids in ids.items():
        if not eids:
            continue
        task = task_for(state, aid)
        task['deferred_evidence_ids'] = sorted(set(task.get('deferred_evidence_ids', [])) | set(eids))
        key = fingerprint([aid, decision.arguments])
        task.setdefault('deferred_reads', {})[key] = {'evidence_ids':list(dict.fromkeys(eids)),
            'query':decision.arguments.get('passage_query'), 'offset':decision.arguments.get('passage_offset'),
            'section_filter':decision.arguments.get('section_filter'), 'status':'pending'}


def delivered_signature(state):
    record = state.get("v5_request_knowledge") or {}
    return fingerprint([record.get("request_id"), record.get("keys", [])])


# --- Transmission budget per Sol decision ------------------------------------------

def deferred(reason="deferred_transmission_budget"):
    return {"not_executed": True, "reason": reason, "transmission_deferred": True,
        "guidance": "Résultat non transmis faute de place dans cette réponse : renouveler l'appel à la décision suivante ; "
            "aucun passage n'est compté comme lu."}


def fit_indexes(output, room, size):
    """Shorten index pages to the room, never losing a locator (run 48d2c500: index pages of
    80 and 60 entries over three works were deferred whole by the generic compactor).

    Auxiliary lists shrink first; then the largest page gives back its last entries and its
    next_offset becomes the stable position of the first entry not transmitted. Only the
    transmitted entries reach Sol and are credited (delivery_keys reads this result)."""
    rows = output["indexes"]
    for row in rows:
        if size() <= room:
            break
        kept = {str(p.get("section") or "") for p in row.get("passages", [])}
        if len(row.get("sections", [])) > 24:
            row["sections"] = [s for s in row["sections"] if s in kept][:24] or row["sections"][:24]
            row["sections_truncated"] = True
        coverage = row.get("coverage")
        if isinstance(coverage, dict):
            row["coverage"] = {k: coverage[k] for k in ("dimensions", "question_sufficient", "review_stale") if k in coverage}
    while size() > room and any(row.get("passages") for row in rows):
        row = max(rows, key=lambda r: len(r.get("passages", [])))
        withheld = row["passages"].pop()
        row["withheld_count"] = row.get("withheld_count", 0)+1
        if isinstance(withheld.get("position"), int):
            row["next_offset"] = withheld["position"]
        elif isinstance(row.get("offset"), int):
            row["next_offset"] = row["offset"]+len(row["passages"])
        # The note is part of the size checked by this loop.
        row["transmission"] = (f"partielle : {row['withheld_count']} entrée(s) non transmise(s) faute de place ; "
            f"suite exacte avec analyze_article(index, index_offset={row['next_offset']}, même section_filter)")
    if not any(row.get("passages") for row in rows) or size() > room:
        return deferred()
    return output


def fit_output(state, toolkit, decision, output, room):
    """Fit one tool result into the room left by earlier calls of the same response.

    compact_exchange cut every result of a response to the same cap (4, 2, then 1
    passage), so a batch of readings delivered one passage per work (run
    2d882893). Here earlier calls keep their passages; a later one gives back the
    passages that do not fit, whose windows are never acknowledged as read.
    """
    size = lambda: len(json.dumps(output, ensure_ascii=False, default=str))+300
    if not isinstance(output, dict) or size() <= room:
        return output
    if decision.tool == "analyze_article" and isinstance(output.get("reading"), list):
        rows = output["reading"]
        for row in rows:
            if size() <= room:
                break
            if row.get("scientific_structures"):
                row["scientific_structures"] = row["scientific_structures"][:4]
        explicit = bool(decision.arguments.get("evidence_ids"))
        while size() > room and any(row.get("passages") for row in rows):
            row = max(rows, key=lambda r: len(r.get("passages", [])))
            withheld = row["passages"].pop()
            row.setdefault("withheld_evidence_ids", []).insert(0, withheld["evidence_id"])
            if not explicit and isinstance(row.get("next_offset"), int):
                row["next_offset"] = max(0, row["next_offset"]-1)
        windows = {w["window_id"]: w for w in state.get("reading_windows", [])}
        for row in rows:
            window = windows.get(row.get("reading_window_id"))
            if window and window.get("status") == "queued":
                window["evidence_ids"] = [p["evidence_id"] for p in row.get("passages", [])]
                if not window["evidence_ids"]:
                    window["status"] = "withheld"
            if row.get("withheld_evidence_ids"):
                row["transmission"] = ("partielle : passages retenus faute de place, non lus ; "
                    "les demander avec analyze_article(read, evidence_ids) à la décision suivante")
        if size() <= room and any(row.get("passages") for row in rows):
            return output
        return {**deferred(), "withheld_evidence_ids": {row["article"]["id"]: row.get("withheld_evidence_ids", [])
            for row in rows if isinstance(row.get("article"), dict)}}
    if decision.tool == "analyze_article" and isinstance(output.get("indexes"), list):
        return fit_indexes(output, room, size)
    # Other results: V4's own reduction, applied to this result alone.
    from .original import compact_exchange
    item = {"type": "function_call_output", "call_id": "fit", "output": json.dumps(output, ensure_ascii=False, default=str)}
    reduced = compact_exchange([item], toolkit, max(1800, room-300))
    if reduced and reduced[0].get("type") == "function_call_output":
        fitted = json.loads(reduced[0]["output"])
        if len(json.dumps(fitted, ensure_ascii=False))+300 <= room:
            return fitted
    if decision.tool in WRITE_TOOLS or output.get("published") or output.get("saved"):
        # A saved write keeps its receipt; only its echo is shortened.
        return {k: v for k, v in output.items() if k not in {"text", "outline", "proposal", "editorial_review"}} | {
            "transmission": "écho abrégé ; le contenu enregistré reste lisible par les outils"}
    return deferred()


# --- Work view (progress projection) ------------------------------------------------

def readiness_toolkit(state):
    """A read-only view with the attributes V4's source_readiness uses."""
    from .models import Article
    registry = {aid: Article.model_validate(a) for aid, a in state.get("articles", {}).items()}
    return SimpleNamespace(registry=registry, shown_evidence=list(state.get("shown_evidence_ids", [])),
        evidence=state.get("evidence", {}), snapshots=state.get("source_snapshots", {}))


def workflow_view(state, budget=2600):
    """Plan and manuscript obligations, factual and bounded (no section is imposed)."""
    outline = state.get("outline", {})
    goal = (state.get("turn_goal") or {}).get("deliverable")
    view = {}
    progress = writing_progress(state)
    current = next_writing_section(state) if progress else None
    if current is not None:
        # Run 152fc86a: the section was ready, yet the view opened on the missing
        # analyses of the four later sections and Sol kept reading.
        prep = section_preparation(state, readiness_toolkit(state), current)
        approved = plan_approved(state)
        steps = " ; ".join(f"{a.get('article_id') or ', '.join(a.get('article_ids', []))} — {a['action']}"
            for a in prep["actions"][:6])
        view["current_section"] = {"section_id": current["id"], "title": current["title"][:120], "ready": prep["ready"],
            "scientific_state": prep["scientific_state"], "basis": prep["basis"], "plan_approved": approved,
            "next_action": (PLAN_APPROVAL_REQUIRED + (" Préparation restante : "+steps if steps else "")
                if not approved else
                f"Rédiger maintenant la section {current['id']} et l'enregistrer par write_section(action='save') "
                "à partir du dossier fourni (fiches, preuves, relations) ; aucune relecture n'est nécessaire."
                if prep["ready"] else "Préparer d'abord : "+steps)}
    from .science import comparison_work
    comparisons = comparison_work(state)
    if current is not None:
        comparisons = [row for row in comparisons if row.get("group") == current["id"]]
    if comparisons:
        view['scientific_comparisons'] = [{k:v for k,v in row.items() if k!='guidance'} for row in comparisons]
    if goal in PLAN_DELIVERABLES:
        view["plan"] = {"saved_for_this_request": plan_saved_for_request(state), "plan_revision": outline.get("plan_revision"),
            "status": outline.get("status"), "approved": bool(outline.get("approval")),
            "rule": "Un plan est enregistré avec manage_outline (sections, article_ids, article_roles) avant sa publication ; "
                "l'approbation reste au consultant."}
    if progress or (goal in PLAN_DELIVERABLES | {"draft"} and outline.get("sections")):
        toolkit = readiness_toolkit(state)
        sections = []
        for section in outline.get("sections", []):
            ready = section_readiness(state, toolkit, section)
            sections.append({"id": section["id"], "title": section["title"][:90], "drafted": bool(section.get("drafts")),
                "sources": {aid: row["status"] for aid, row in ready.items()}})
        view["sections"] = sections
        if len(json.dumps(view, ensure_ascii=False)) > budget:
            # Compact form: analysed count, and every source that is not analysed.
            for row in sections:
                statuses = row.pop("sources")
                row["analysed_sources"] = sum(v == "analysed" for v in statuses.values())
                row["not_analysed"] = {aid: v for aid, v in statuses.items() if v != "analysed"}
        while sections and len(json.dumps(view, ensure_ascii=False)) > budget:
            sections.pop()
            view["sections_omitted"] = view.get("sections_omitted", 0)+1
    if progress:
        section = next_writing_section(state)
        view["writing"] = {"mode": progress.get("mode"), "status": progress.get("status"),
            "saved": len(progress.get("saved_draft_ids", {})), "targets": len(progress.get("target_ids", [])),
            "blocked": list(progress.get("blocked", {})), "next_section_id": section["id"] if section else None,
            "rule": "Une section par réponse : write_section(save) avec toutes ses publications ; le dossier de la section suivante est fourni automatiquement."}
    return view
