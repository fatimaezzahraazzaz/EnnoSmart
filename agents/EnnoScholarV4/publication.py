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
        if any(aid not in toolkit.registry for aid in ids):
            return reject("unknown_article", "Référence inconnue : utiliser uniquement les identifiants de la bibliothèque.")
        pending = {}
        for item in args.analyses or []:
            if item.article_id not in ids:
                return reject("analysis_outside_bibliography", "Une analyse finale doit concerner un article cité.")
            article = toolkit.store.article(item.article_id).model_copy(deep=True)
            ArticleAnalyzer.save(article, item.analysis, toolkit.evidence, toolkit.shown_evidence, toolkit.snapshots)
            pending[article.id] = article
        if toolkit.finalization_only and getattr(toolkit, "final_verification_allowed", False):
            # Publication phase: a cited work actually presented to the model but
            # never checked gets one bounded registry verification (DOI/arXiv
            # identity only, no scientific search), instead of rejecting every
            # reference of a turn that ended before the reading pipeline.
            unchecked = [aid for aid in ids if aid not in toolkit.checked_ids and not toolkit.registry[aid].verified
                and aid in toolkit.store.exposed
                and aid not in toolkit.final_verification_attempted
                and (toolkit.registry[aid].doi or toolkit.registry[aid].arxiv_id)][:6]
            if unchecked:
                toolkit.final_verification_attempted.update(unchecked)
                toolkit.verify(unchecked, deadline)
        checked = toolkit.verify(ids, deadline, local_only=toolkit.finalization_only)
        if not checked["valid"]:
            return reject("invalid_bibliography", "Bibliographie non validée : retirer les références incertaines de la réponse principale et les conserver dans la bibliothèque.",
                invalid_article_ids=[r["article_id"] for r in checked["articles"] if not r["valid"]], bibliography=checked)
        if len({toolkit.store.article(aid).canonical_work_id or aid for aid in ids}) != len(ids):
            return reject("duplicate_work", "Plusieurs versions du même travail sont citées : conserver un représentant et expliquer sa version.")
        off_target = reference_mismatch(toolkit, ids, args.evidence_ids)
        if off_target:
            return reject("off_target_publications", off_target["message"], **{k: v for k, v in off_target.items() if k != "message"})
        for eid in args.evidence_ids:
            e = toolkit.evidence.get(eid)
            if not e or eid not in toolkit.shown_evidence or e["article_id"] not in ids:
                return reject("unread_or_unknown_evidence", "Preuve inconnue, non consultée ou extérieure aux articles cités.")
            if not exact_source_valid(e, toolkit.snapshots):
                return reject("evidence_integrity", "Citation exacte ou localisation incohérente avec la source conservée.")
        for article in pending.values():
            if article.id not in toolkit.store.exposed:
                return reject("unread_article", "Un article analysé doit avoir été effectivement présenté au modèle.")
        displayed = args.displayed_article_ids
        if displayed is not None and (len(displayed) != len(set(displayed)) or not set(displayed) <= set(ids)):
            return reject("invalid_display_order", "L'ordre affiché doit contenir des articles cités uniques.")
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
        if any(marker not in ids and marker not in args.evidence_ids for marker in markers):
            return reject("unknown_marker", "Référence ou preuve non déclarée dans la réponse.")
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
            label = e["section"] or "Passage source"
            if e["page_number"] is not None:
                label += f", page PDF {e['page_number']}"
                if e.get("printed_page") and e.get("page_verification") == "margin_text":
                    label += f", page imprimée {e['printed_page']}"
            source_url = readable_source_url(e["source_url"], toolkit.store.article(e["article_id"]))
            text = text.replace("["+eid+"]", f"[{escaped(label)}]({source_url})" if source_url else escaped(label))
            cite(source_url, label, "evidence")
        if args.remaining_uncertainties:
            text += "\n\nLimites de cette réponse : " + " ; ".join(args.remaining_uncertainties)
        # Native Web annotation IDs are not Markdown URLs. The verified article
        # and evidence links above remain the public references; retain raw IDs
        # in the draft/trace without displaying an unrenderable API annotation.
        text = re.sub(r" ?cite[^]*", "", text)
        # Analyses and ordering are committed only after every publication check passes.
        for article in pending.values():
            toolkit.registry[article.id] = article
            toolkit.store.examined_article(article.id)
        if displayed:
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


def fallback(toolkit, reason):
    """No model or provider call: only validated metadata and exact saved claims."""
    toolkit.finalization_only = True
    proposed = [aid for aid in toolkit.state.get("evaluation", {}).get("recommended_article_ids", [])
        if aid in toolkit.registry and toolkit.registry[aid].selection_override is not False]
    if not proposed:
        proposed = [a.id for a in toolkit.registry.values() if a.assessment.get("decision") == "retain"
            and a.selection_override is not False]
    no_scientific_selection = not proposed
    if no_scientific_selection:
        # Available abstracts can still be shown verbatim without inventing a
        # contribution or claiming GPT selected/read these papers.
        proposed = [aid for aid in toolkit.store.exposed if toolkit.registry[aid].verified and toolkit.registry[aid].abstract]
        if not proposed:
            proposed = [a.id for a in toolkit.registry.values() if a.verified and a.abstract]
    checks = toolkit.verify([aid for aid in proposed if aid in toolkit.registry], float("inf"), local_only=True)
    valid = [row["article_id"] for row in checks["articles"] if row["valid"]]
    primary, seen = [], set()
    for aid in valid:
        a = toolkit.store.article(aid)
        canonical = a.canonical_work_id or aid
        if canonical not in seen:
            primary.append(aid); seen.add(canonical)
    primary = primary[:8]
    answer = "La rédaction finale n’a pas pu passer toutes ses vérifications. Voici les résultats déjà validés et conservés ; cette synthèse de secours reste partielle."
    citations, evidence_ids, details = {}, [], []
    if primary:
        if no_scientific_selection:
            answer += "\n\nLa sélection scientifique n’a pas été achevée. Les notices suivantes sont documentées, mais ne constituent pas un classement des meilleurs travaux."
        answer += "\n\n| N° | Publication | Contribution enregistrée | Accès réellement vérifié |\n|---|---|---|---|\n"
        for i, aid in enumerate(primary):
            a = toolkit.store.article(aid)
            claims = a.analysis.get("contribution") or a.analysis.get("method") or []
            supported = [c for c in claims if c.get("evidence_ids") and all(eid in toolkit.evidence and eid in toolkit.shown_evidence and
                toolkit.evidence[eid]["article_id"] == aid and exact_source_valid(toolkit.evidence[eid], toolkit.snapshots) for eid in c["evidence_ids"])]
            contribution = supported[0]["statement"] if supported else (
                "Extrait du résumé conservé, sans analyse approfondie : « " + a.abstract[:600] + ("…" if len(a.abstract)>600 else "") + " »"
                if a.abstract else "Notice validée ; contribution détaillée à confirmer.")
            url = article_url(a)
            label = f"[{escaped(a.title)}]({url})" if public_link(url) else escaped(a.title)
            answer += f"| {i+1} | {label} | {escaped(contribution)} | {a.access_status} ; analyse : {a.analysis_basis or 'non effectuée'} |\n"
            if public_link(url):
                citations[url_key(url)] = {"url": url, "title": a.title, "cited": True, "source_kind": "recommended_publication"}
            if i < 3:
                for category, heading in [("results", "Résultats enregistrés"), ("limitations", "Limites enregistrées")]:
                    for claim in a.analysis.get(category, [])[:1]:
                        if not claim.get("evidence_ids") or not all(eid in toolkit.evidence and eid in toolkit.shown_evidence and
                            toolkit.evidence[eid]["article_id"] == aid and exact_source_valid(toolkit.evidence[eid], toolkit.snapshots) for eid in claim["evidence_ids"]):
                            continue
                        explanation = f"\n**{escaped(a.title)} — {heading} :** {claim['statement']}\n"
                        for eid in claim["evidence_ids"]:
                            e = toolkit.evidence[eid]; evidence_ids.append(eid)
                            if public_link(e["source_url"]):
                                locator = e["section"] or "Passage source"
                                if e.get("page_number") is not None: locator += f", page PDF {e['page_number']}"
                                explanation += f"[{escaped(locator)}]({e['source_url']}) "
                                citations[url_key(e["source_url"])] = {"url": e["source_url"], "title": locator, "cited": True, "source_kind": "evidence"}
                        details.append(explanation)
        answer += "\n" + "\n".join(details)
        toolkit.state["displayed_article_ids"] = primary
        answer += "\n\nLes autres publications restent dans la bibliothèque. Vous pouvez demander « approfondis l’article 4 » ou « continue la recherche »."
    else:
        answer += "\n\nAucune sélection suffisamment vérifiée n’est disponible pour être citée comme établie. Les notices trouvées restent conservées dans la bibliothèque."
    return {"answer": answer, "citations": list(citations.values()), "partial": True, "fallback": True,
        "reason": reason, "primary_article_ids": primary, "displayed_article_ids": list(toolkit.state.get("displayed_article_ids", [])),
        "cited_article_ids": primary, "cited_evidence_ids": list(dict.fromkeys(evidence_ids)), "coverage_sufficient": False}
