"""Validate publication, retain safe refusal diagnostics, and build a grounded fallback."""
from __future__ import annotations

import copy
import re
from urllib.parse import unquote, urlsplit

from .models import normalize_doi, public_link
from .evidence import exact_source_valid
from .tools.article_analysis import ArticleAnalyzer


def url_key(url):
    if not public_link(url):
        return ""
    parsed = urlsplit(url)
    if parsed.hostname.lower() in {"doi.org", "dx.doi.org"}:
        doi = normalize_doi(unquote(parsed.path.lstrip("/")))
        return "doi:" + doi if doi else ""
    return unquote(parsed._replace(netloc=parsed.netloc.lower(), fragment="").geturl()).rstrip("/") + ("#"+parsed.fragment if parsed.fragment else "")


def label_matches(label, article):
    from .sources import title_key
    key, title = title_key(label), title_key(article.title)
    # An exact DOI label is an identity, not an abbreviated title. Accept only
    # the publication's own DOI; a different DOI still fails the same validator.
    literal = label.strip().strip("`")
    if re.fullmatch(r"(?:https?://(?:dx\.)?doi\.org/)?10\.\d{4,9}/\S+", literal, re.I):
        return normalize_doi(literal) == normalize_doi(article.doi)
    if label.casefold() in {"doi", "publication", "article", "source"} or re.fullmatch(r"\[?\d+\]?", label):
        return True
    if key == title or (len(key) >= 4 and title.startswith(key + " ")):
        return True
    # A shortened title may omit words, but must preserve the words and their order.
    words = key.split()
    if len(words) < 2:
        return False
    iterator = iter(title.split())
    return all(any(candidate == word for candidate in iterator) for word in words)


def article_url(article):
    if article.doi:
        return "https://doi.org/"+article.doi
    if article.arxiv_id:
        return "https://arxiv.org/abs/"+article.arxiv_id
    return article.url if "cookies_not_supported" not in article.url.casefold() else ""


def readable_source_url(url, article):
    """Keep the exact source in storage; cite a stable landing page for expired PDF URLs."""
    if "cookies_not_supported" in url.casefold():
        stable = article_url(article)
        return stable if stable != url and public_link(stable) else ""
    return url if public_link(url) else ""


def escaped(value):
    return str(value).replace("|", "\\|").replace("[", "").replace("]", "").replace("\n", " ")


def canonical(toolkit, aid):
    article = toolkit.registry.get(aid)
    return (article.canonical_work_id or aid) if article else aid


def reference_mismatch(toolkit, ids, evidence_ids=()):
    """Local check before publication (run ce08ad31): when the request targets resolved
    publications, the answer must treat them, and an exclusive request must not be about
    other works. Two records of one work count as one. None when the answer fits."""
    goal = toolkit.state.get("turn_goal") or {}
    refs = goal.get("references") or {}
    targets = refs.get("article_ids") or []
    if getattr(toolkit, "saving_section", False) or goal.get("deliverable") in {"draft", "plan", "plan_revision"}:
        # A manuscript section cites its plan sources, not the works named for the chat answer
        # (run dcf5d05b replay: the first section was refused for not citing the turn's ten works).
        return None
    if not targets or refs.get("ambiguous") or refs.get("scope") not in {"exclusive", "primary"}:
        return None
    cited = {canonical(toolkit, aid) for aid in ids}
    wanted = {canonical(toolkit, aid): aid for aid in targets}
    missing = [aid for key, aid in wanted.items() if key not in cited]
    others = [aid for aid in ids if canonical(toolkit, aid) not in wanted]
    # Each targeted work with stored, valid proofs is treated from them (run 8cb4d68a: LDPoly had
    # six valid proofs and none reached the final request). The correction stays local: the
    # available evidence ids are returned, no new reading is needed.
    cited_by = {}
    for eid in evidence_ids:
        row = toolkit.evidence.get(eid)
        if row:
            cited_by.setdefault(canonical(toolkit, row["article_id"]), []).append(eid)
    unproven = {}
    for key, aid in wanted.items():
        if key in cited and not cited_by.get(key):
            available = [e["evidence_id"] for e in (toolkit.registry[aid].analysis or {}).get("evidence", [])
                if e["evidence_id"] in toolkit.shown_evidence and exact_source_valid(e, toolkit.snapshots)]
            if available:
                unproven[aid] = available[:8]
    if not missing and not unproven and not (refs["scope"] == "exclusive" and len(others) >= len(wanted)):
        return None
    if not missing and not others:
        return {"message": "Chaque publication visée doit être présentée à partir de ses preuves enregistrées : citer "
            "les evidence_ids disponibles (" + "; ".join(f"{aid} → {', '.join(eids)}" for aid, eids in unproven.items())
            + ") dans les phrases qui les utilisent.", "target_article_ids": targets, "missing_article_ids": [],
            "other_article_ids": [], "available_evidence_ids": unproven}
    title = lambda aid: (toolkit.registry[aid].title if aid in toolkit.registry else aid)[:90]
    return {"message": "La réponse doit porter sur les publications visées par la demande "
        + "; ".join(f"[{aid}] {title(aid)}" for aid in targets)
        + (f". Non traitées : {', '.join(missing)}" if missing else "")
        + (f". Hors demande : {', '.join(others)} — ne les présenter que comme contexte secondaire" if others else "")
        + ". Corriger la réponse à partir de leurs fiches et preuves déjà transmises (aucune nouvelle lecture), ou dire "
        "précisément ce qui manque.", "target_article_ids": targets, "missing_article_ids": missing,
        "other_article_ids": others}


_VALUE = re.compile(r"(?<![\w.,])\d+(?:[   ]\d{3})*(?:[.,]\d+)?(?:\s?%)?")
_PLANE = re.compile(r"\b2\s?D\b|\bdeux dimensions\b|bidimensionn|two[- ]dimensional|\ben 2 dimensions\b|\bplan(?:e|aire)s?\b", re.I)


def _values(text):
    """Reported values of a text, normalised (« 23,35 » = « 23.35 », « 9 900 » = « 9900 »);
    small integers and years are left out (they identify nothing)."""
    found = set()
    for raw in _VALUE.findall(text or ""):
        value = re.sub(r"[   ]", "", raw).replace(",", ".")
        digits = re.sub(r"\D", "", value)
        if "%" in value or "." in value or len(digits) >= 3 and not re.fullmatch(r"(?:19|20)\d{2}", digits):
            found.add(value.rstrip("%"))
    return found


def response_consistency(toolkit, ids, displayed, text):
    """A research answer is built on the canonical selection (counter, cards and depth check
    use the same works), states an explicit restricted study dimension, and reports the saved
    figures of the works it presents. None when the answer fits; local checks, no new call."""
    from .research import active, canonical_selection, version_groups
    state = toolkit.state
    if not active(state) or getattr(toolkit, "saving_section", False):
        return None
    selection = canonical_selection(state)
    expected = [aid for aid in selection["selected_ids"] if aid in toolkit.registry]
    provisional = False
    if not expected:
        # No card yet: the panel shows the provisional preselection; the answer presents the same works, each with
        # its reading level (run d964ec6e: four published, five recommended, no reason given).
        expected = [aid for aid in selection["ranked_ids"] if aid in toolkit.registry]
        provisional = True
    if not expected:
        return None
    groups = version_groups(state)
    work = lambda aid: groups.get(aid, aid)
    shown = list(displayed) if displayed is not None else list(ids)
    if not shown:
        # The answer presents no publication (a question about the process, a refusal).
        return None
    # Every problem is named in one refusal, so that one correction can fix them all.
    issues = []
    if {work(aid) for aid in shown} != {work(aid) for aid in expected}:
        title = lambda aid: toolkit.registry[aid].title[:90]
        issues.append({"rejection_code": "selection_mismatch", "message": ("La liste affichée doit être la présélection "
            "provisoire du panneau (aucune fiche encore enregistrée), chacune avec son niveau de lecture : un travail jugé "
            "insuffisant y reste avec cette réserve explicite : " if provisional else "La liste affichée doit être la sélection de la "
            "recherche, la même que le compteur et les fiches : ") + "; ".join(f"[{aid}] {title(aid)}" for aid in expected)
            + ". Les autres candidats restent disponibles dans la bibliothèque et peuvent être mentionnés hors de "
            "cette liste.", "expected_article_ids": expected,
            "unexpected_article_ids": [aid for aid in shown if work(aid) not in {work(x) for x in expected}],
            "missing_article_ids": [aid for aid in expected if work(aid) not in {work(x) for x in shown}]})
    request = (state.get("turn_goal") or {}).get("understanding", "") + " " + " ".join(
        m.get("content", "") for m in state.get("messages", [])[-3:] if m.get("role") == "user")
    if re.search(r"\b3\s?D\b|volumi|tridimension|three[- ]dimensional", request, re.I) and not _PLANE.search(text):
        from .context import study_scope_flags
        flagged = {aid: flags for aid in expected if (flags := study_scope_flags(toolkit.registry[aid]))}
        if flagged:
            issues.append({"rejection_code": "study_scope_omitted", "message": "Validation explicitement limitée au plan "
                "(2D) non signalée : " + "; ".join(f"[{aid}] « {flags[0]['quote'][:200]} »" for aid, flags in flagged.items())
                + ". Préciser la dimension réellement étudiée ; ne pas la présenter comme une démonstration volumique 3D.",
                "flagged_article_ids": list(flagged)})
    if not getattr(toolkit, "results_refused", False):
        answered = _values(text)
        omitted = {}
        for aid in expected:
            analysis = toolkit.registry[aid].analysis or {}
            claims = [c for f in ("results", "metrics", "tables_figures") for c in analysis.get(f, []) if isinstance(c, dict)
                and all(eid in toolkit.shown_evidence and eid in toolkit.evidence for eid in c.get("evidence_ids", []))]
            values = set().union(*(_values(c["statement"]) for c in claims)) if claims else set()
            if values and not values & answered:
                omitted[aid] = sorted(values)[:8]
        if omitted:
            # Once per run: the saved figures are named; a deliberate omission is then accepted.
            toolkit.results_refused = True
            issues.append({"rejection_code": "saved_results_omitted", "message": "Résultats chiffrés enregistrés non "
                "repris pour : " + "; ".join(f"[{aid}] {', '.join(v)}" for aid, v in omitted.items()) + ". Ils figurent "
                "dans conversation_science avec leurs preuves : les présenter (avec leurs conditions) au lieu de les "
                "dire indisponibles.", "omitted_values": omitted})
    if not issues:
        return None
    merged = {k: v for issue in issues for k, v in issue.items() if k not in {"rejection_code", "message"}}
    return {"rejection_code": issues[0]["rejection_code"], "message": " ".join(i["message"] for i in issues),
        "issues": [i["rejection_code"] for i in issues], **merged}


def publish(toolkit, args, deadline):
    ids = list(dict.fromkeys(args.article_ids))
    attempt = {"draft": args.answer, "article_ids": ids, "evidence_ids": args.evidence_ids,
        "phase": "finalization" if toolkit.finalization_only else "exploration", "published": False}
    attempts = toolkit.state.setdefault("finalization_attempts", [])
    attempts.append(attempt)
    del attempts[:-20]

    def reject(code, message, **fields):
        result = {"published": False, "rejection_code": code, "error": message, **fields}
        attempt.update({k: v for k, v in result.items() if k != "bibliography"})
        return result

    try:
        if (not ids and toolkit.state.get("discovery_mode") == "original" and
                toolkit.final_verification_incomplete):
            # A rejected mixed bibliography must not turn into an empty success.
            # The existing grounded fallback can render the verified subset;
            # never delete citations from a draft or publish its unchecked claims.
            from .stops import StopResearch
            raise StopResearch("bibliography_verification_incomplete")
        from .research import active, status as research_status
        if active(toolkit.state) and not getattr(toolkit, "saving_section", False):
            observed = research_status(toolkit.state)
            if args.coverage_sufficient and not observed["complete"]:
                return reject("scientific_review_required", "Résultat partiel : la revue scientifique actuelle ne justifie pas une couverture suffisante.")
        if any(aid not in toolkit.registry for aid in ids):
            return reject("unknown_article", "Référence inconnue : utiliser uniquement les identifiants de la bibliothèque.")
        pending = {}
        for item in args.analyses or []:
            if item.article_id not in ids:
                return reject("analysis_outside_bibliography", "Une analyse finale doit concerner un article cité.")
            article = toolkit.store.article(item.article_id).model_copy(deep=True)
            ArticleAnalyzer.save(article, item.analysis, toolkit.evidence, toolkit.shown_evidence, toolkit.snapshots)
            pending[article.id] = article
        if toolkit.state.get("discovery_mode") == "original":
            from .research import original_obligations
            obligations = original_obligations(toolkit.state, toolkit)
            # Do not turn prose or a downloaded file into an analysis. Ask the
            # same model to persist the claims it uses, through the existing
            # validated inline-save path and bounded publication repairs.
            used_science = {toolkit.evidence[eid]["article_id"] for eid in args.evidence_ids
                if eid in toolkit.evidence and toolkit.evidence[eid].get("basis") in {"fulltext", "abstract"}}
            unsaved = [row for row in obligations["pending_analyses"] if row["article_id"] in ids
                and row["article_id"] in used_science and row["article_id"] not in pending]
            if unsaved:
                return reject("analysis_persistence_required", "Ces passages sont exploités scientifiquement, mais leur "
                    "analyse n'est pas enregistrée. Fournir analyses dans finalize_response, avec seulement les affirmations "
                    "étayées par les passages réellement lus et leur limite de lecture. Les analyses existantes restent inchangées.",
                    pending_analysis_article_ids=[row["article_id"] for row in unsaved],
                    evidence_ids_by_article={row["article_id"]: row["evidence_ids"] for row in unsaved})
        if toolkit.finalization_only and getattr(toolkit, "final_verification_allowed", False):
            # Verify only cited, presented works. Each existing verify call
            # checks cancellation/deadline and checkpoints its completed record.
            # Batch size is bounded by those budgets, never a fixed paper count.
            toolkit.final_verification_ids = list(dict.fromkeys([*toolkit.final_verification_ids, *ids]))
            unchecked = [aid for aid in ids if aid not in toolkit.checked_ids and not toolkit.registry[aid].verified
                and aid in toolkit.store.exposed
                and aid not in toolkit.final_verification_attempted
                and (toolkit.registry[aid].doi or toolkit.registry[aid].arxiv_id)]
            try:
                from .stops import StopResearch
                for aid in unchecked:
                    toolkit.final_verification_attempted.add(aid)
                    try:
                        toolkit.verify([aid], deadline)
                    except StopResearch:
                        raise
                    except Exception as exc:
                        # A failed registry request leaves this work unverified;
                        # other candidates can still be checked in the same budget.
                        toolkit.activity("verify_bibliography", "failed", article_id=aid,
                            error_type=type(exc).__name__)
            finally:
                progress = toolkit.verify(ids, deadline, local_only=True)
                attempt["verified_article_ids"] = [r["article_id"] for r in progress["articles"] if r["valid"]]
                attempt["unverified_article_ids"] = [r["article_id"] for r in progress["articles"] if not r["valid"]]
                toolkit.final_verification_incomplete = not progress["valid"]
                toolkit.save()
        checked = toolkit.verify(ids, deadline, local_only=toolkit.finalization_only)
        saved_inline = set()
        if toolkit.state.get("discovery_mode") == "original":
            valid = {row["article_id"] for row in checked["articles"] if row["valid"]}
            for item in args.analyses or []:
                if item.article_id not in valid or item.article_id not in toolkit.store.exposed:
                    continue
                # Preserve the registry's freshly verified identity. A valid
                # scientific card is independent of another citation or a
                # later formatting refusal; the same save validator is applied.
                article = toolkit.registry[item.article_id].model_copy(deep=True)
                ArticleAnalyzer.save(article, item.analysis, toolkit.evidence, toolkit.shown_evidence, toolkit.snapshots)
                toolkit.registry[article.id] = article
                toolkit.store.examined_article(article.id)
                saved_inline.add(article.id)
            if saved_inline:
                attempt["saved_analysis_ids"] = sorted(saved_inline)
                toolkit.save()
        if not checked["valid"]:
            return reject("invalid_bibliography", "Certaines références ne sont pas vérifiées ou sont invalides. "
                "Conserver les références de verified_article_ids dans la réponse partielle ; corriger uniquement les autres. "
                "Une vérification non terminée peut être reprise, sans recommencer celles déjà réussies.",
                verified_article_ids=[r["article_id"] for r in checked["articles"] if r["valid"]],
                unverified_article_ids=[r["article_id"] for r in checked["articles"] if not r["valid"]],
                invalid_article_ids=[r["article_id"] for r in checked["articles"] if not r["valid"]], bibliography=checked)
        if len({toolkit.store.article(aid).canonical_work_id or aid for aid in ids}) != len(ids):
            return reject("duplicate_work", "Plusieurs versions du même travail sont citées : conserver un représentant et expliquer sa version.")
        off_target = reference_mismatch(toolkit, ids, args.evidence_ids)
        if off_target:
            return reject("off_target_publications", off_target["message"], **{k: v for k, v in off_target.items() if k != "message"})
        for eid in args.evidence_ids:
            e = toolkit.evidence.get(eid)
            if not e or eid not in toolkit.shown_evidence or e["article_id"] not in ids:
                cause = ("inconnue" if not e else "non consultée" if eid not in toolkit.shown_evidence else
                    f"extérieure aux articles cités (elle appartient à {e['article_id']})")
                return reject("unread_or_unknown_evidence", f"Preuve {eid} {cause}.", invalid_evidence_ids=[eid])
            if not exact_source_valid(e, toolkit.snapshots):
                return reject("evidence_integrity", f"Citation exacte ou localisation de {eid} incohérente avec la source "
                    "conservée.", invalid_evidence_ids=[eid])
        for article in pending.values():
            if article.id not in toolkit.store.exposed:
                return reject("unread_article", "Un article analysé doit avoir été effectivement présenté au modèle.")
        displayed = args.displayed_article_ids
        if displayed is not None and (len(displayed) != len(set(displayed)) or not set(displayed) <= set(ids)):
            return reject("invalid_display_order", "L'ordre affiché doit contenir des articles cités uniques.")
        inconsistent = response_consistency(toolkit, ids, displayed, args.answer)
        if inconsistent:
            return reject(inconsistent.pop("rejection_code"), inconsistent.pop("message"), **inconsistent)
        allowed = {url_key(c["url"]) for c in toolkit.state.get("web_sources", [])}
        for aid in ids:
            a = toolkit.store.article(aid)
            allowed.update(url_key(u) for u in [article_url(a), a.url, a.pdf_url, a.fulltext_source,
                *a.work_urls, *a.verification_sources] if u)
        allowed.update(url_key(toolkit.evidence[eid]["source_url"]) for eid in args.evidence_ids)
        # A verified consulted preprint can have a DOI different from the work's
        # journal notice. Only exact, already-shown document evidence can admit it.
        source_dois = {}
        for eid in args.evidence_ids:
            e = toolkit.evidence[eid]
            doi = normalize_doi(e.get("source_identifiers", {}).get("doi", ""))
            if doi and e.get("version_status") == "document_identified":
                source_dois[doi] = toolkit.store.article(e["article_id"])
                allowed.add("doi:"+doi)
        text = args.answer
        links = re.findall(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", text)
        # GFM also renders bare URLs. Apply the same allowlist to those links.
        used_urls = []
        for raw_url in re.findall(r'https?://[^\s<>\[\]"|]+', text):
            url = next((candidate for candidate in (raw_url, raw_url.rstrip(".,;:)"))
                if url_key(candidate) and url_key(candidate) in allowed), None)
            if url is None:
                return reject("unknown_url", "Lien non obtenu par les outils : utiliser les marqueurs d'articles ou les URL enregistrées.")
            used_urls.append(url)
        for label, url in links:
            if not url_key(url) or url_key(url) not in allowed:
                return reject("unknown_url", "Lien non obtenu par les outils : utiliser les marqueurs d'articles ou les URL enregistrées.")
            if url_key(url).startswith("doi:"):
                article = next((toolkit.store.article(aid) for aid in ids if "doi:"+toolkit.store.article(aid).doi == url_key(url)), None)
                article = article or source_dois.get(url_key(url).removeprefix("doi:"))
                if not article or not label_matches(label, article):
                    return reject("doi_label_mismatch", "Le libellé ne correspond pas à cette publication : utiliser son titre, un titre abrégé identifiable ou son marqueur paper_.")
        for url in used_urls:
            if "cookies_not_supported" not in url.casefold():
                continue
            article = next((toolkit.store.article(aid) for aid in ids if any(
                url == toolkit.evidence[eid]["source_url"] for eid in args.evidence_ids
                if toolkit.evidence[eid]["article_id"] == aid)), None)
            stable = readable_source_url(url, article) if article else ""
            text = text.replace(url, stable or "Source accessible via la référence bibliographique")
        markers = re.findall(r"\[(paper_[\w]+|ev_[\w]+)\]", text)
        unknown = list(dict.fromkeys(marker for marker in markers if marker not in ids and marker not in args.evidence_ids))
        if unknown:
            # The exact markers are named (run 95f3507d: a truncated proof id was refused three
            # times under a generic message and the model "repaired" the section title instead).
            return reject("unknown_marker", "Référence ou preuve non déclarée dans la réponse : "
                + ", ".join(unknown[:8]) + ". Utiliser l'identifiant complet d'une publication de article_ids "
                "ou d'une preuve de evidence_ids, ou retirer ce marqueur.", unknown_markers=unknown[:20])
        known_dois = {toolkit.store.article(aid).doi for aid in ids if toolkit.store.article(aid).doi}
        known_dois.update(source_dois)
        detected = re.findall(r"10\.\d{4,9}/[^\s<>\[\]|`*]+", unquote(text))
        if any(normalize_doi(doi.rstrip(".,;:)")) not in known_dois and normalize_doi(doi.rstrip(".,;:")) not in known_dois for doi in detected):
            return reject("unknown_doi", "La réponse contient un DOI non déclaré ou non vérifié.")
        used = {}
        def cite(url, title, source_kind):
            if public_link(url):
                used[url_key(url)] = {"url": url, "title": title, "cited": True, "source_kind": source_kind}
        for url in used_urls:
            if "cookies_not_supported" not in url.casefold():
                cite(url, "Source utilisée", "response_link")
        for aid in ids:
            a = toolkit.store.article(aid)
            url, title = article_url(a), escaped(a.title)
            if "["+aid+"]" in text:
                text = text.replace("["+aid+"]", f"[{title}]({url})" if public_link(url) else title + " (document fourni)")
            cite(url, a.title, "recommended_publication")
        for eid in args.evidence_ids:
            if "["+eid+"]" not in text:
                continue
            e = toolkit.evidence[eid]
            from .citation_render import proof_locator
            label = proof_locator(e, toolkit.snapshots)["section"] or "Passage source"
            if e["page_number"] is not None:
                label += f", page PDF {e['page_number']}"
                if e.get("printed_page") and e.get("page_verification") == "margin_text":
                    label += f", page imprimée {e['printed_page']}"
            source_url = readable_source_url(e["source_url"], toolkit.store.article(e["article_id"]))
            text = text.replace("["+eid+"]", f"[{escaped(label)}]({source_url})" if source_url else escaped(label))
            cite(source_url, label, "evidence")
        # answer is the public scientific prose. Structured uncertainties and
        # engine diagnostics remain in last_response and response_history below.
        # Native Web annotation IDs are not Markdown URLs. The verified article
        # and evidence links above remain the public references; retain raw IDs
        # in the draft/trace without displaying an unrenderable API annotation.
        text = re.sub(r" ?cite[^]*", "", text)
        # Other pipelines retain their existing commit point; original-engine
        # cards already saved above must not overwrite newly verified metadata.
        for article in pending.values():
            if article.id not in saved_inline:
                toolkit.registry[article.id] = article
                toolkit.store.examined_article(article.id)
        if displayed:
            from .research import record_original_proposals
            record_original_proposals(toolkit.state, displayed)
            toolkit.state["displayed_article_ids"] = list(displayed)
        order = list(toolkit.state.get("displayed_article_ids", []))
        refs = (toolkit.state.get("turn_goal") or {}).get("references") or {}
        targets = [aid for aid in refs.get("article_ids") or [] if aid in ids or canonical(toolkit, aid) in
            {canonical(toolkit, x) for x in ids}]
        toolkit.finished = {"answer": text, "citations": list(used.values()), "cited_article_ids": ids,
            "displayed_article_ids": order, "primary_article_ids": list(displayed or ids),
            # What this answer was about (the request's targets first), and whether it set a new
            # displayed list: « le deuxième » next turn refers to this, not to an older table.
            "discussed_article_ids": list(dict.fromkeys([*[a for a in targets if a in ids], *(displayed or ids)])),
            "displayed_list_current": displayed is not None,
            "cited_evidence_ids": list(dict.fromkeys(args.evidence_ids)), "coverage_sufficient": args.coverage_sufficient,
            "remaining_uncertainties": args.remaining_uncertainties, "bibliography": checked}
        toolkit.state["last_response"] = {k: v for k, v in toolkit.finished.items() if k != "answer"}
        toolkit.state.setdefault("response_history", []).append(copy.deepcopy(toolkit.state["last_response"]))
        attempt["published"] = True
        toolkit.save()
        return {"published": True, "cited_article_ids": ids, "displayed_article_ids": order}
    except ValueError as exc:
        # These validators emit our own messages, never upstream response bodies or URLs.
        return reject("invalid_analysis_or_evidence", str(exc)[:500])


def fallback(toolkit, reason, drafting_attempted=True):
    """No model or provider call: only validated metadata and exact saved claims.

    Execution diagnostics stay in the result/trace, not in the scientific prose.
    """
    toolkit.finalization_only = True
    held = set(toolkit.state.get("held_article_ids", []))
    def eligible(aid):
        return aid in toolkit.registry and aid not in held and toolkit.registry[aid].selection_override is not False
    proposed = [aid for aid in toolkit.state.get("evaluation", {}).get("recommended_article_ids", [])
        if eligible(aid)]
    if not proposed:
        proposed = [a.id for a in toolkit.registry.values() if a.assessment.get("decision") == "retain"
            and eligible(a.id)]
    no_scientific_selection = not proposed
    verification_scope = getattr(toolkit, "final_verification_ids", [])
    if verification_scope:
        # Preserve only the model's proposed bibliography, not unrelated cached
        # works that happen to be verified. Local validation below stays strict.
        proposed = [aid for aid in verification_scope if eligible(aid)]
    if no_scientific_selection and not verification_scope:
        # Available abstracts can still be shown verbatim without inventing a
        # contribution or claiming GPT selected/read these papers.
        proposed = [aid for aid in toolkit.store.exposed if eligible(aid) and toolkit.registry[aid].verified and toolkit.registry[aid].abstract]
        if not proposed:
            proposed = [a.id for a in toolkit.registry.values() if eligible(a.id) and a.verified and a.abstract]
    checks = toolkit.verify([aid for aid in proposed if aid in toolkit.registry], float("inf"), local_only=True)
    valid = [row["article_id"] for row in checks["articles"] if row["valid"]]
    primary, seen = [], set()
    for aid in valid:
        a = toolkit.store.article(aid)
        canonical = a.canonical_work_id or aid
        if canonical not in seen:
            primary.append(aid); seen.add(canonical)
    answer = "Voici les éléments étayés par les sources disponibles. La comparaison reste partielle."
    citations, evidence_ids, details = {}, [], []
    validity = {}
    def supported(claim, aid):
        for eid in claim.get("evidence_ids", []):
            if eid not in validity:
                e = toolkit.evidence.get(eid)
                validity[eid] = bool(e and exact_source_valid(e, toolkit.snapshots))
            if not validity[eid] or toolkit.evidence[eid]["article_id"] != aid:
                return False
        return bool(claim.get("evidence_ids"))
    def proof_links(claim, article):
        links = []
        for eid in claim["evidence_ids"]:
            e = toolkit.evidence[eid]
            evidence_ids.append(eid)
            url = readable_source_url(e["source_url"], article)
            if public_link(url):
                from .citation_render import proof_locator
                label = proof_locator(e, toolkit.snapshots)["section"] or "Passage source"
                if e.get("page_number") is not None:
                    label += f", page PDF {e['page_number']}"
                links.append(f"[{escaped(label)}]({url})")
                citations[url_key(url)] = {"url": url, "title": label, "cited": True, "source_kind": "evidence"}
        return " ".join(dict.fromkeys(links))
    if any(toolkit.store.article(aid).analysis for aid in primary):
        answer += "\n\nLes éléments ci-dessous proviennent des analyses enregistrées et de leurs preuves exactes ; ils ne constituent pas une nouvelle synthèse comparative."
    if primary and not no_scientific_selection and not any(toolkit.store.article(aid).analysis for aid in primary):
        # Run 9c7625c8: five works proposed, none read. A preselection on notices is labelled as such.
        answer += ("\n\nPrésélection provisoire établie sur les notices et abstracts : aucune fiche d’analyse (méthodes, "
            "résultats, limites avec preuves) n’a encore été enregistrée pour ces publications ; leur texte intégral n’a pas été analysé.")
    if primary:
        if no_scientific_selection:
            answer += "\n\nLa sélection scientifique n’a pas été achevée. Les notices suivantes sont documentées, mais ne constituent pas un classement des meilleurs travaux."
        answer += "\n\n| N° | Publication | Contribution enregistrée | Accès réellement vérifié |\n|---|---|---|---|\n"
        for i, aid in enumerate(primary):
            a = toolkit.store.article(aid)
            claims = [c for field in ("contribution", "method", "technical_mechanism") for c in a.analysis.get(field, [])]
            available = [c for c in claims if supported(c, aid)]
            contribution = available[0]["statement"] if available else (
                "Analyse enregistrée ; les preuves nécessaires à cette restitution ne sont pas validées." if a.analysis else
                "Extrait du résumé conservé, sans analyse approfondie : « " + a.abstract[:600] + ("…" if len(a.abstract)>600 else "") + " »"
                if a.abstract else "Notice validée ; contribution détaillée à confirmer.")
            source_links = proof_links(available[0], a) if available else ""
            url = article_url(a)
            label = f"[{escaped(a.title)}]({url})" if public_link(url) else escaped(a.title)
            access = ("Analyse de passages du texte intégral" if a.analysis_basis == "fulltext" else
                "Analyse du résumé" if a.analysis_basis == "abstract" else
                "Texte extrait, non analysé" if a.fulltext.get("text") else
                "Résumé disponible, non analysé" if a.abstract else "Notice bibliographique")
            answer += f"| {i+1} | {label} | {escaped(contribution)} {source_links} | {access} |\n"
            if public_link(url):
                citations[url_key(url)] = {"url": url, "title": a.title, "cited": True, "source_kind": "recommended_publication"}
            for category, heading in [("results", "Résultats enregistrés"), ("limitations", "Limites enregistrées")]:
                claim = next((c for c in a.analysis.get(category, []) if supported(c, aid)), None)
                if claim:
                    details.append(f"\n**{escaped(a.title)} — {heading} :** {claim['statement']}\n"+proof_links(claim, a))
        answer += "\n" + "\n".join(details)
        toolkit.state["displayed_article_ids"] = primary
        answer += "\n\nLes autres publications restent dans la bibliothèque. Vous pouvez demander « approfondis l’article 4 » ou « continue la recherche »."
    else:
        answer += "\n\nAucune sélection suffisamment vérifiée n’est disponible pour être citée comme établie. Les notices trouvées restent conservées dans la bibliothèque."
    if toolkit.state.get("discovery_mode") == "original":
        from .research import original_progress
        original_progress(toolkit.state)
    return {"answer": answer, "citations": list(citations.values()), "partial": True, "fallback": True,
        "reason": reason, "primary_article_ids": primary, "displayed_article_ids": list(toolkit.state.get("displayed_article_ids", [])),
        "cited_article_ids": primary, "cited_evidence_ids": list(dict.fromkeys(evidence_ids)), "coverage_sufficient": False}
