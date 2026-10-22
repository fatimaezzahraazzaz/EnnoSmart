"""The consultant's request as understood by the model, and what this turn must report.

Understanding stays with the model: set_turn_goal records ITS reading of a free
message (deliverable, targeted locks, reading depth, a scope change quoted from
the consultant). The orchestrator only adapts its existing stages to that goal;
the earlier lexical signals remain the fallback when no goal is declared.
Per-lock coverage is a factual ledger (queries, retained works, analyses, plan
sections), never a quota or a relevance score.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
import re
from .research import NeedProfile


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


DELIVERABLES = ("answer", "literature_search", "plan", "plan_revision", "comparison", "deep_reading", "draft")


class TurnGoalArgs(Args):
    requested_article_count: int | None = Field(default=None, ge=1, le=200,
        description="Nombre de publications demandé explicitement ; null si aucun. Ce nombre n'est pas une preuve de pertinence.")
    deliverable: Literal["answer", "literature_search", "plan", "plan_revision", "comparison", "deep_reading", "draft"] = Field(
        description="Attente du message : answer (discuter, expliquer), literature_search, plan, plan_revision, comparison "
            "(y compris au projet), deep_reading, draft (seulement si la rédaction est demandée).")
    lock_ids: list[str] = Field(max_length=20, description="Mode lié : verrous actifs visés ([] = tous). Express : [].")
    focus_lock_ids: list[str] = Field(max_length=20, description="Nouveau périmètre actif demandé par le consultant ; [] sinon.")
    exact_quote: str = Field(max_length=500, description="Mots exacts du consultant justifiant focus_lock_ids, sinon \"\".")
    reading_depth: Literal["none", "abstracts", "core", "deep"] = Field(
        description="none : mémoire seule ; abstracts : notices ; core : travaux clés ; deep : lecture et comparaisons.")
    understanding: str = Field(max_length=600, description="Demande reformulée, références au contexte résolues.")
    comparison_requested: bool | None = Field(default=None, description="true si le message demande aussi de comparer des "
        "travaux (méthodes, protocoles, résultats, limites), quel que soit le livrable (ex. « rédige et compare ») ; null sinon.")
    search_needed: bool | None = Field(default=None, description="true : de nouvelles publications doivent être recherchées "
        "pour ce message ; false : la réponse s'appuie sur le corpus et les fiches déjà disponibles ; null si incertain.")
    article_ids: list[str] | None = Field(default=None, max_length=12, description="Identifiants paper_… des publications "
        "visées par la demande, dans l'ordre du dialogue (conversation_focus.active_publications pour « ces articles », "
        "« le deuxième », « leurs limites ») ; [] si aucune ; null si la demande est une recherche ouverte.")
    article_scope: Literal["exclusive", "primary", "open"] | None = Field(default=None, description="exclusive : travailler "
        "uniquement sur article_ids ; primary : d'abord elles, d'autres travaux si la demande le justifie ; open : exploration.")
    references_ambiguous: bool | None = Field(default=None, description="true si plusieurs ensembles de publications restent "
        "plausibles : demander une clarification au lieu de choisir.")
    constraints: list[Literal["no_search", "no_download", "no_reanalysis", "no_manuscript_change"]] | None = Field(
        default=None, max_length=4, description="Interdictions explicites du consultant pour ce message.")
    research_need: NeedProfile | None = Field(default=None, description="literature_search : besoin scientifique structuré "
        "(objet, problème, résultat recherché, méthodes acceptables, contexte, contraintes, critères dont les essentiels, "
        "sens de direct/connexe/fondamental, familles d'approches avec requête, incertitudes), tiré du message seul ; "
        "null pour les autres livrables ou si le besoin reste celui déjà enregistré.")


def _norm(text):
    return " ".join(str(text or "").replace("’", "'").casefold().split())


def manuscript_permissions(state, goal, message):
    """Resolve the narrow, explicit additive request against a model's broad prohibition.

    Preservation is a write scope, not a prohibition of every manuscript operation.
    A real global prohibition always wins, even against a declared draft goal.
    """
    from .writing_progress import MULTI_SECTION_REQUEST, WRITING_ACTION
    text = _norm(message)
    explicit_stop = bool(re.search(
        r"\bne\s+modifi\w*\s+(?:rien\b|pas\s*(?:[.!?]|$))|"
        r"\bne\s+(?:rédig\w*|redig\w*|écri\w*|ecri\w*|ajout\w*)\s+(?:pas|rien|aucun\w*)|"
        r"\baucune\s+(?:nouvelle\s+)?(?:rédaction|redaction|section)|"
        r"\b(?:aucune|sans|pas de)\s+modification\s+(?:(?:de|du|mon|notre|le|ce|au)\s+|l')*"
        r"(?:manuscrit|document|état\s+de\s+l'art|etat\s+de\s+l'art)|"
        r"\bne\s+modifi\w*\s+(?:pas\s+)?(?:le\s+|mon\s+|ce\s+)?(?:manuscrit|document|état|etat)|"
        r"\b(?:do not|don't)\s+(?:write|add|modify)\b", text))
    preserve = bool(re.search(r"\b(?:conserv\w*|préserv\w*|preserv\w*|garde\w*)\b.{0,100}"
        r"(?:plan|sections?.{0,30}(?:déjà|deja|existantes?|rédigées|redigees))", text))
    additive = bool(goal.get("deliverable") == "draft" and WRITING_ACTION.search(text)
        and MULTI_SECTION_REQUEST.search(text) and preserve and not explicit_stop)
    if explicit_stop:
        goal["constraints"] = list(dict.fromkeys([*(goal.get("constraints") or []), "no_manuscript_change"]))
    if not additive:
        return None
    goal["constraints"] = [c for c in goal.get("constraints") or [] if c != "no_manuscript_change"]
    sections = state.get("outline", {}).get("sections", [])
    return {"mode": "add_missing_sections", "preserve_plan": True,
        "protected_section_ids": [s["id"] for s in sections if s.get("drafts")],
        "target_section_ids": [s["id"] for s in sections if not s.get("drafts")],
        "basis": "explicit_missing_sections_and_preservation"}


def record_goal(state, args, message, registry):
    """Store the model's reading of this message; a scope change needs the consultant's words."""
    from .diagnostic_link import set_scope
    goal = args.model_dump()
    permissions = manuscript_permissions(state, goal, message)
    if permissions:
        goal["manuscript_permissions"] = permissions
    linked = state.get("mode") == "diagnostic_linked"
    link = state.get("diagnostic_link") or {}
    selected = link.get("selected_lock_ids", [])
    if not linked and (goal["lock_ids"] or goal["focus_lock_ids"]):
        raise ValueError("Conversation Express : aucun verrou de diagnostic ; lock_ids et focus_lock_ids doivent être [].")
    focus = list(dict.fromkeys(goal["focus_lock_ids"]))
    if focus:
        if set(focus)-set(selected):
            raise ValueError(f"Verrous hors de la sélection confirmée : {sorted(set(focus)-set(selected))}. Sélection : {selected}.")
        if not goal["exact_quote"].strip() or _norm(goal["exact_quote"]) not in _norm(message):
            raise ValueError("Changement de périmètre : exact_quote doit reprendre mot pour mot la phrase du consultant.")
        if sorted(focus) != sorted(link["active_lock_ids"]):
            set_scope(state, focus, "consultant_message")
            link["history"][-1]["exact_quote"] = goal["exact_quote"]
    if goal["lock_ids"] and set(goal["lock_ids"])-set(link.get("active_lock_ids", [])):
        raise ValueError(f"lock_ids doit appartenir aux verrous actifs {link.get('active_lock_ids')} ; "
            "pour travailler sur d'autres verrous sélectionnés, utiliser focus_lock_ids avec les mots du consultant.")
    state["turn_goal"] = {**goal, "lock_ids": list(dict.fromkeys(goal["lock_ids"])), "focus_lock_ids": focus,
        "references": goal_references(state, goal, registry)}
    need = state["turn_goal"].pop("research_need", None)
    if need and goal.get("deliverable") == "literature_search":
        # The structured need is kept apart from the turn goal: every relevance judgment
        # depends on it (need_signature) and a continuation keeps it with the job's goal.
        if need != state.get("research_need"):
            if state.get("research_need"):
                state.setdefault("research_need_history", []).append(state["research_need"])
            state["research_need"] = need
    if goal.get("deliverable") == "literature_search" and goal.get("search_needed") is not False:
        from .research import begin
        begin(state, message, state["turn_goal"]["references"].get("message_id"))
    result = {"saved": True, "goal": state["turn_goal"]}
    coverage = lock_coverage(state, registry)
    if coverage:
        result["active_lock_ids"] = link["active_lock_ids"]
        result["lock_coverage"] = coverage
    return result


def goal_references(state, goal, registry):
    """The publications this message targets, kept with the goal until the request ends.

    The model's declared ids (from conversation_focus) win when they exist in the
    registry; otherwise the local resolution of the message (references.resolve) is
    used. Ids are checked against this session's registry only (no other dossier)."""
    local = state.get("conversation_references") or {}
    declared = goal.get("article_ids")
    if declared is not None:
        unknown = [aid for aid in declared if aid not in registry]
        if unknown:
            raise ValueError(f"article_ids inconnus dans cette conversation : {unknown}. Utiliser les identifiants paper_… "
                "de conversation_focus ou de la bibliothèque.")
        ids = list(dict.fromkeys(declared))
        source = local.get("source") if ids and ids == local.get("article_ids") else ("model" if ids else "none")
        resolved_from = local.get("resolved_from") if source == local.get("source") else local.get("message_id")
    else:
        ids, source, resolved_from = list(local.get("article_ids") or []), local.get("source", "none"), local.get("resolved_from")
    open_goal = goal["deliverable"] in {"literature_search", "plan"} or goal.get("search_needed") is True
    # A draft or plan uses the targets first and its sections' own sources too.
    scope = goal.get("article_scope") or ("open" if not ids else "primary" if open_goal or goal["deliverable"] in {
        "draft", "plan_revision"} else "exclusive")
    if goal["deliverable"] in {"draft", "plan", "plan_revision"} and scope == "exclusive":
        # A draft or a plan cites its sections' sources; the works named for the turn come first
        # without excluding them (run dcf5d05b).
        scope = "primary"
    # A local ambiguity asks for a clarification only for a request about earlier works; a
    # draft, a plan or a search defines its own sources.
    ambiguous = bool(goal.get("references_ambiguous")) or (bool(local.get("ambiguous")) and not ids and goal["deliverable"]
        not in {"draft", "plan", "plan_revision", "literature_search"})
    constraints = list(dict.fromkeys([*(goal.get("constraints") or []), *(local.get("constraints") or [])]))
    permissions = goal.get("manuscript_permissions")
    if permissions:
        constraints = [c for c in constraints if c != "no_manuscript_change"]
    return {"article_ids": ids, "scope": scope, "source": source, "resolved_from": resolved_from,
        "message_id": local.get("message_id"), "ambiguous": ambiguous, "constraints": constraints,
        **({"manuscript_permissions": permissions} if permissions else {}),
        **({"clarification": local.get("clarification"), "candidates": local.get("candidates", [])} if ambiguous else {})}


def lock_coverage(state, registry):
    """Per active lock: attributed queries, retained and analysed works, plan sections.

    Attribution comes only from the model's own lock_ids on searches, decisions and
    sections; unattributed searches/decisions are counted, never guessed.
    """
    link = state.get("diagnostic_link")
    if state.get("mode") != "diagnostic_linked" or not link:
        return None
    titles = {v["id"]: v["title"] for v in link["snapshot"]["locks"]}
    searches = state.get("recent_searches", [])
    finalists = state.get("evaluation", {}).get("recommended_article_ids", [])
    sections = state.get("outline", {}).get("sections", [])
    retained_all = [aid for aid, a in registry.items() if a.selection_override is not False and
        ((a.assessment or {}).get("decision") == "retain" or a.selection_override is True)]
    rows = []
    for lid in link["active_lock_ids"]:
        queries = [s["query"] for s in searches if lid in (s.get("lock_ids") or [])]
        retained = [aid for aid in retained_all if lid in (registry[aid].assessment or {}).get("lock_ids", [])]
        retained.sort(key=lambda aid: aid not in finalists)
        analysed = [aid for aid in retained if registry[aid].analysis]
        in_plan = [s["id"] for s in sections if lid in s.get("lock_ids", [])]
        status = ("analysed" if analysed else "candidates_retained" if retained else
            "searched_no_relevant_result" if queries else "not_searched")
        rows.append({"lock_id": lid, "title": titles.get(lid, "")[:160], "status": status,
            "queries": queries[-5:], "queries_total": len(queries),
            "retained_article_ids": retained[:10], "retained_total": len(retained),
            "analysed_article_ids": analysed[:10], "plan_section_ids": in_plan})
    return {"locks": rows,
        "not_searched": [r["lock_id"] for r in rows if r["status"] == "not_searched"],
        "without_relevant_publication": [r["lock_id"] for r in rows if r["status"] == "searched_no_relevant_result"],
        "searches_without_lock_ids": sum(not s.get("lock_ids") for s in searches),
        "retained_without_lock_ids": sum(not (registry[aid].assessment or {}).get("lock_ids") for aid in retained_all),
        "guidance": "État factuel par verrou, sans quota. Indiquer lock_ids dans search_scientific et record_candidate_decisions. "
            "Un verrou non recherché se recherche ou se signale ; un verrou sans publication pertinente après recherche est une "
            "lacune à expliciter, jamais une preuve de nouveauté."}


def lock_status(state, registry):
    """Compact per-lock status returned with each linked search."""
    coverage = lock_coverage(state, registry)
    if not coverage:
        return None
    return {"locks": [{k: row[k] for k in ("lock_id", "status", "queries_total", "retained_total")} for row in coverage["locks"]],
        "not_searched": coverage["not_searched"], "searches_without_lock_ids": coverage["searches_without_lock_ids"]}


def validated(article):
    """A publication the plan may present as selected: retained by the model,
    accepted by the consultant, or analysed. Pending or rejected notices are not."""
    decision = (article.assessment or {}).get("decision")
    return article.selection_override is True or decision == "retain" or (bool(article.analysis) and decision not in {"reject", "pending"})


def source_status(article):
    if article.analysis and article.analysis_basis == "fulltext":
        return "analysée (extraits du texte intégral)"
    if article.analysis:
        return "analysée sur résumé"
    return "retenue sur résumé, non analysée"


def plan_source_ids(state, registry, limit=30):
    """Every work the plan may present as selected: finalists first, then the other
    active, retained or analysed works. Pending, rejected or withdrawn ones are not."""
    from .writing_context import active_article_ids
    ids = list(dict.fromkeys([*state.get("evaluation", {}).get("recommended_article_ids", []),
        *active_article_ids(state, registry),
        *(aid for aid, a in registry.items() if (a.assessment or {}).get("decision") == "retain")]))
    return [aid for aid in ids if aid in registry and registry[aid].selection_override is not False
        and validated(registry[aid])][:limit]


# Card fields a plan part needs to argue (methods, results, limits), in this order.
PLAN_CARD_FIELDS = ("method", "technical_mechanism", "results", "experimental_setup", "limitations")


def source_row(article, abstract_chars=500, claims=0, evidence=None):
    """One plan source. With claims > 0, the saved card's first whole claims per field
    (methods, results, limits) travel with their evidence ids; nothing is summarized."""
    row = {"id": article.id, "title": article.title[:220], "year": article.year, "status": source_status(article),
        "lock_ids": (article.assessment or {}).get("lock_ids", []),
        "selection_reason": str((article.assessment or {}).get("justification", ""))[:300],
        **({"contribution": article.assessment["contribution"]} if (article.assessment or {}).get("contribution") else {}),
        "analysis_saved": bool(article.analysis), "analysis_basis": article.analysis_basis, "access_status": article.access_status}
    if article.abstract and abstract_chars:
        row["abstract"] = article.abstract[:abstract_chars]
        row["abstract_truncated"] = len(article.abstract) > abstract_chars
    if claims and article.analysis:
        from .context import analysis_projection
        view = analysis_projection(article.analysis, evidence=evidence)
        row["card"] = {field: [{"statement": c["statement"], "evidence_ids": c["evidence_ids"]} for c in view[field][:claims]]
            for field in PLAN_CARD_FIELDS if isinstance(view.get(field), list) and view[field] and isinstance(view[field][0], dict)}
        row["card_claims_total"] = sum(len(v) for v in view.values() if isinstance(v, list) and v and isinstance(v[0], dict))
    return row


def plan_sources(state, registry, limit=30, abstract_chars=500):
    """Works the plan can use: finalists, active works and retained candidates, from abstracts and cards."""
    return [source_row(registry[aid], abstract_chars) for aid in plan_source_ids(state, registry, limit)]


def source_parts(state, registry, ids):
    """The plan part (lock, or Express axis) each source belongs to: the model's own
    lock attribution, or for Express the axis its finding searches covered. Ordering
    only, never a relevance judgment."""
    from .store import query_terms
    if state.get("mode") == "diagnostic_linked":
        return {aid: list((registry[aid].assessment or {}).get("lock_ids") or []) for aid in ids}
    axes = state.get("research_state", {}).get("search_axes", [])[:4]
    found = {}
    for search in state.get("recent_searches", []):
        terms = query_terms((search.get("query") or "") + " " + (search.get("family") or ""))
        for aid in search.get("article_ids", []):
            found.setdefault(aid, set()).update(terms)
    result = {}
    for aid in ids:
        text = query_terms(registry[aid].title + " " + (registry[aid].abstract or "")[:600])
        result[aid] = [axis for axis in axes if (lambda t: t and (len(t & found.get(aid, set())) >= max(1, (len(t)+1)//2)
            or len(t & text) >= 2))(query_terms(axis))]
    return result


def part_sources(state, registry, part, budget=9000, evidence=None):
    """Sources of one plan part with their cards, and every other plan source as a
    compact reference, so a part sees the whole bibliography (no finalist hidden) and
    cites another part's work only when it adds something to its own question."""
    import json
    ids = plan_source_ids(state, registry)
    parts = source_parts(state, registry, ids)
    key = part["lock_ids"][0] if part.get("lock_ids") else part.get("label")
    own = [aid for aid in ids if key in parts.get(aid, [])]
    others = [{"id": aid, "title": registry[aid].title[:160], "year": registry[aid].year,
        "status": source_status(registry[aid]), "parts": parts.get(aid, [])} for aid in ids if aid not in own]
    # Details shrink (claims, then abstract) before any source of this part is dropped.
    for claims, chars in ((2, 600), (1, 400), (1, 220), (0, 220), (0, 0)):
        rows = [source_row(registry[aid], chars, claims, evidence) for aid in own]
        if len(json.dumps([rows, others], ensure_ascii=False)) <= budget:
            break
    return rows, others


NUMBERING = __import__("re").compile(r"^\s*(?:(?:section|partie|chapitre)\s+)?(?:\d+(?:\.\d+)*\s*[.):—–-]|[IVX]+\s*[.)])\s+", __import__("re").I)


def strip_numbering(title):
    """A title without its own leading number: the plan numbers its sections once (C8)."""
    stripped = NUMBERING.sub("", title or "", count=1).strip()
    return stripped or (title or "").strip()


def plan_answer(state, registry, note=""):
    """The saved plan rendered for the chat; the model wrote every scientific field."""
    from .publication import article_url, escaped
    from .models import public_link
    outline = state.get("outline", {})
    titles = {v["id"]: v["title"] for v in (state.get("diagnostic_link") or {}).get("snapshot", {}).get("locks", [])}
    def ref(aid):
        a = registry.get(aid)
        if not a:
            return aid
        url = article_url(a)
        label = escaped(a.title) + (f" ({a.year})" if a.year else "")
        return f"[{label}]({url})" if public_link(url) else label
    lines = []
    if note.strip():
        lines += [note.strip(), ""]
    lines.append(f"## {outline.get('title') or 'État de l’art'} — plan proposé (version {outline.get('version')}, brouillon)")
    for number, section in enumerate(outline.get("sections", []), 1):
        lines += ["", f"### {number}. {strip_numbering(section['title'])}"]
        if section.get("scientific_objective"):
            lines.append(f"**Question scientifique.** {section['scientific_objective']}")
        if section.get("questions"):
            lines += [f"- {q}" for q in section["questions"]]
        if section.get("lock_ids"):
            lines.append("**Verrous.** " + " ; ".join(f"{lid} — {titles.get(lid, '')[:110]}" for lid in section["lock_ids"]))
        if section.get("description"):
            lines.append(section["description"])
        roles = {r["article_id"]: r["role"] for r in section.get("article_roles", [])}
        if section.get("article_ids"):
            lines.append("**Publications.**")
            # A notice the model did not retain is never shown as a selected publication.
            # Each publication shows how far it was examined (C10); a notice the model did
            # not retain is never shown as a selected publication.
            lines += [f"- {ref(aid)}" + (f" — {roles[aid]}" if roles.get(aid) else "") +
                (f" *({source_status(registry[aid])})*" if aid in registry and validated(registry[aid])
                    else " — *candidat non évalué, à vérifier*")
                for aid in section["article_ids"]]
        for key, label in (("experimental_results", "Méthodes et résultats à exploiter"), ("limitations", "Limites"),
                ("missing_information", "À compléter")):
            if section.get(key):
                lines.append(f"**{label}.**")
                lines += [f"- {item}" for item in section[key]]
        if section.get("project_connection"):
            lines.append(f"**Confrontation au projet.** {section['project_connection']}")
    coverage = lock_coverage(state, registry)
    if coverage:
        labels = {"analysed": "publications retenues dont au moins une analysée", "candidates_retained": "publications retenues, analyse à approfondir",
            "searched_no_relevant_result": "recherché, aucune publication pertinente retenue à ce stade", "not_searched": "pas encore recherché"}
        lines += ["", "### Couverture des verrous"]
        for row in coverage["locks"]:
            detail = f"{row['retained_total']} publication(s) retenue(s)" if row["retained_total"] else ""
            lines.append(f"- Verrou {row['lock_id']} ({row['title'][:90]}) : {labels[row['status']]}" + (f", {detail}" if detail else "") +
                ("" if row["plan_section_ids"] else " ; absent du plan"))
    return "\n".join(lines)


def provisional_plan(state, registry, reason="time"):
    """A useful plan without any model call, when the full plan cannot be produced
    in time. It only arranges what is recorded: retained or analysed publications,
    candidates not yet evaluated (explicitly labelled) and gaps. No result is stated."""
    from .publication import article_url, escaped
    from .models import public_link
    from .store import query_terms
    link = state.get("diagnostic_link") or {}
    locks = [v for v in link.get("snapshot", {}).get("locks", []) if v["id"] in link.get("active_lock_ids", [])] if state.get("mode") == "diagnostic_linked" else []
    groups = [(f"Verrou {v['id']} — {v['title']}", v["id"], query_terms(v["title"])) for v in locks]
    if not groups:
        axes = state.get("research_state", {}).get("search_axes", [])[:6] or [state.get("research_state", {}).get("topic") or "Sujet"]
        groups = [(axis, None, query_terms(axis)) for axis in axes]
    searched = {aid for s in state.get("recent_searches", []) for aid in s.get("article_ids", [])}
    # Candidates follow the lock of the search that found them (lock titles are often
    # French and publications English: shared words cannot attribute them), or the
    # axis whose concepts the search query shares in Express.
    found_for = {}
    for s in state.get("recent_searches", []):
        for aid in s.get("article_ids", []):
            found_for.setdefault(aid, set()).update(s.get("lock_ids") or [])
            found_for.setdefault(aid, set()).update({f"query:{t}" for t in query_terms((s.get("query") or "") + " " + (s.get("family") or ""))})
    def ref(a):
        url = article_url(a)
        label = escaped(a.title) + (f" ({a.year})" if a.year else "")
        return f"[{label}]({url})" if public_link(url) else label
    def overlap(a, terms):
        return len(terms & query_terms(a.title + " " + (a.abstract or "")[:600]))
    lines = [("**Plan provisoire.** Le plan complet n'a pas pu être généré dans le temps disponible. Ce plan organise "
        "uniquement ce qui est enregistré : il n'affirme aucun résultat. Les publications retenues ont été évaluées "
        "(sur résumé ou par analyse) ; les candidats listés ensuite n'ont pas encore été évalués et ne sont pas validés."), ""]
    used = set()
    for number, (title, lock_id, terms) in enumerate(groups, 1):
        lines += [f"### {number}. {title}"]
        retained = [a for a in registry.values() if a.selection_override is not False and validated(a) and
            (lock_id in ((a.assessment or {}).get("lock_ids") or []) if lock_id else overlap(a, terms) >= 2)]
        retained.sort(key=lambda a: -overlap(a, terms))
        if retained:
            lines.append("**Publications retenues :**")
            lines += [f"- {ref(a)} — {source_status(a)}" + (f". Motif : {str(a.assessment.get('justification'))[:180]}"
                if (a.assessment or {}).get("justification") else "") for a in retained[:5]]
            used.update(a.id for a in retained)
        else:
            lines.append("**Publications retenues :** aucune à ce stade.")
        def belongs(a):
            origin = found_for.get(a.id, set())
            if lock_id:
                return lock_id in origin or lock_id in ((a.assessment or {}).get("lock_ids") or [])
            return len({t for t in terms if f"query:{t}" in origin}) >= max(1, (len(terms)+1)//2) or overlap(a, terms) >= 2
        pending = [a for a in registry.values() if a.id in searched and a.id not in used and a.selection_override is not False
            and not validated(a) and (a.assessment or {}).get("decision") != "reject" and belongs(a)]
        pending.sort(key=lambda a: -overlap(a, terms))
        if pending:
            lines.append("**Candidats non évalués à examiner (non validés) :**")
            lines += [f"- {ref(a)}" for a in pending[:5]]
            used.update(a.id for a in pending[:5])
        gaps = []
        if lock_id:
            row = next((r for r in (lock_coverage(state, registry) or {}).get("locks", []) if r["lock_id"] == lock_id), {})
            gaps.append({"not_searched": "aucune recherche attribuée à ce verrou", "searched_no_relevant_result":
                "recherche faite, aucune publication pertinente retenue", "candidates_retained": "publications retenues non encore analysées",
                "analysed": "analyse ciblée disponible ; lecture complète non garantie"}.get(row.get("status"), "couverture non établie"))
        if not retained:
            gaps.append("question à documenter avant rédaction")
        lines.append("**Lacunes :** " + " ; ".join(gaps or ["à préciser"]) + ".")
        lines.append("")
    lines.append("Voulez-vous que je poursuive l'évaluation des candidats, que je complète la recherche d'un verrou, "
        "ou que je génère le plan détaillé à partir de cette base ?")
    return "\n".join(lines)


def call_estimate(durations, default=120.0):
    """Expected duration of the next long generation (analysis, plan), from this run.

    Measured on the run's own calls producing long outputs; the default applies
    before any such call. It schedules work inside the unchanged deadline.
    """
    # Never below the default: a short first analysis (abstract only, 88 s in run
    # 85a926db) must not make the next full-text one (170 s) look affordable.
    return max([default, *(seconds for seconds, output in durations if output >= 1500)])
