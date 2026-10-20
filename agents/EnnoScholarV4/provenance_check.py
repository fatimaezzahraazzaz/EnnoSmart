"""Typed provenance of drafted claims: publication, project, comparison, interpretation.

A publication's passage proves what the publication reports; an EnnoDiagnostic passage
proves what the project did or observed. Neither replaces the other. Deterministic, no
LLM call; the existing publication validators (claim_union, attribution, numbers,
bibliography) still run on every scientific proof.
"""
from __future__ import annotations

import re

from .attribution import MARKER, PROJECT_MARKER, sentences
from .diagnostic_link import diagnostic_evidence

KINDS = ("scientific", "project", "comparison", "interpretation")


def _project_ids(toolkit):
    project = diagnostic_evidence(toolkit.state)
    # A publication passage keeps its meaning if an identifier ever existed in both stores.
    return project, (lambda eid: eid in project and eid not in toolkit.evidence)


def normalize_provenance(toolkit, args):
    """Route diagnostic proofs to project_evidence_ids and [projet:…] markers, then check
    that each one exists, is a verified quote and was delivered to the model."""
    project, is_project = _project_ids(toolkit)
    text = args.text or ""
    if project:
        text = MARKER.sub(lambda m: f"[projet:{m.group(1)}]" if is_project(m.group(1)) else m.group(0), text)
    args.text = text or args.text
    moved = [eid for eid in args.evidence_ids if is_project(eid)]
    args.evidence_ids = [eid for eid in args.evidence_ids if not is_project(eid)]
    args.project_evidence_ids = list(dict.fromkeys([*args.project_evidence_ids, *moved, *PROJECT_MARKER.findall(text)]))
    for claim in args.claims:
        moved = [eid for eid in claim.evidence_ids if is_project(eid)]
        if moved:
            claim.evidence_ids = [eid for eid in claim.evidence_ids if not is_project(eid)]
            claim.project_evidence_ids = list(dict.fromkeys([*claim.project_evidence_ids, *moved]))
    if not args.project_evidence_ids:
        return
    unknown = [pid for pid in args.project_evidence_ids if pid not in project]
    if unknown:
        raise ValueError(f"Preuves projet inconnues : {unknown[:5]}. Citer uniquement les identifiants de preuve de "
            "diagnostic_context ou de get_project_context, sous la forme [projet:evidence_id].")
    unverified = [pid for pid in args.project_evidence_ids if project[pid].get("quote_verified") is not True]
    if unverified:
        raise ValueError(f"Preuves projet non vérifiées : {unverified[:5]}. Présenter ces points comme observations à "
            "confirmer, sans les citer comme preuves, ou citer une preuve projet vérifiée.")
    delivered = toolkit.state.get("delivered_project_evidence_ids")
    if delivered is not None:
        # Aliases of one passage are the same proof: delivery is checked on the passage.
        from .diagnostic_link import canonical_ids
        received = set(canonical_ids(toolkit.state, delivered))
        missing = [pid for pid in args.project_evidence_ids if canonical_ids(toolkit.state, [pid])[0] not in received]
        if missing:
            raise ValueError(f"Preuves projet non consultées dans cette conversation : {missing[:5]}. Les relire avec "
                "get_project_context(kind=evidence, ids=[…]) avant de les citer.")


def anchor_claims(claims, text):
    """Keep each declared claim on the exact text (formatting aside). A declared claim
    the text does not contain is refused, never replaced: the stored claims are the
    manuscript's own words and nothing the writer meant to say is dropped silently."""
    from .toolkit import anchor_in_text
    kept, dropped = [], []
    for claim in claims:
        if claim["statement"] in text:
            kept.append(claim)
            continue
        exact = anchor_in_text(claim["statement"], text)
        if exact:
            kept.append({**claim, "statement": exact})
        else:
            dropped.append(claim)
    return kept, dropped


def type_claims(toolkit, claims, args):
    """Every sentence citing a proof becomes a claim typed by its proofs; each claim is
    then checked against its declared type."""
    declared = {c["statement"] for c in claims}
    for sentence in sentences(args.text):
        eids = [eid for eid in dict.fromkeys(MARKER.findall(sentence)) if eid in args.evidence_ids]
        pids = [pid for pid in dict.fromkeys(PROJECT_MARKER.findall(sentence)) if pid in args.project_evidence_ids]
        if (eids or pids) and sentence not in declared and not any(sentence in c["statement"] or c["statement"] in sentence for c in claims):
            claims.append({"statement": sentence, "evidence_ids": eids, "project_evidence_ids": pids, "origin": "citation_mapping",
                "kind": "comparison" if eids and pids else "scientific" if eids else "project"})
    problems = []
    for claim in claims:
        claim.setdefault("project_evidence_ids", [])
        claim.setdefault("kind", "scientific")
        science, project = claim["evidence_ids"], claim["project_evidence_ids"]
        kind = claim["kind"]
        if kind in {"scientific", "project"} and science and project:
            # Both origins cited: the sentence relates them.
            kind = claim["kind"] = "comparison"
        if kind == "comparison" and science and not project and len({(toolkit.evidence.get(eid) or {}).get("article_id")
                for eid in science} - {None}) >= 2:
            # Contract: « comparison » relates a publication and the project. Two publications set
            # side by side are an argued reading supported by both (run dcf5d05b: refused).
            kind = claim["kind"] = "interpretation"
            claim["compared_publications"] = True
        if kind == "scientific" and not science:
            problems.append((claim, "un résultat ou une méthode de publication exige une preuve de l'article [ev_…] ; "
                "une preuve projet ne l'établit pas" if project else "affirmation scientifique sans preuve de publication"))
        elif kind == "project" and not project:
            problems.append((claim, "un fait du projet exige sa preuve [projet:…] ; une publication ne prouve pas ce que le "
                "projet a réalisé"))
        elif kind == "comparison" and not (science and project):
            problems.append((claim, "une comparaison cite la publication [ev_…] et le projet [projet:…]"))
        elif kind == "interpretation":
            if not (science or project):
                problems.append((claim, "une interprétation indique ses appuis (publication ou projet)"))
            claim["origin"] = "inference"
    if problems:
        details = " | ".join(f"« {claim['statement'][:160]} » : {reason}" for claim, reason in problems[:4])
        raise ValueError("Provenance des affirmations non respectée : " + details +
            (f" (+{len(problems)-4} autres)" if len(problems) > 4 else "") +
            ". Corriger le type (scientific, project, comparison, interpretation) ou les preuves de ces phrases.")
    return claims


def render_project_markers(toolkit, text):
    """[projet:id] becomes a readable documentary reference to the project's passage."""
    from .publication import escaped
    project = diagnostic_evidence(toolkit.state)

    def label(match):
        row = project.get(match.group(1)) or {}
        parts = ["projet", row.get("document_name") or "document", row.get("section_title") or "",
            f"p. {row['page']}" if row.get("page") else ""]
        return "(" + escaped(", ".join(p for p in parts if p).replace("projet, ", "projet : ", 1)) + ")"
    return PROJECT_MARKER.sub(label, text)


def delivered_project_evidence(state, inputs):
    """Diagnostic evidence ids present in the content actually sent to the model."""
    project = diagnostic_evidence(state)
    if not project:
        return set()
    import json
    blob = json.dumps(inputs, ensure_ascii=False)
    return {pid for pid in project if pid in blob}


def project_relations(state, project_ids, claims, section):
    """What a draft does with the project's work: each cited project proof located in the
    diagnostic's work units (lock, documentary unit, role, fact), and how the section
    confronts it with publications. A record for review, never a new attribution: a proof
    outside the units of the section's locks is reported as such."""
    from .diagnostic_link import work_units, canonical_ids
    link = state.get("diagnostic_link") if state.get("mode") == "diagnostic_linked" else None
    if not link:
        return [], None
    section_locks = set(section.get("lock_ids") or [])
    located, available = {}, 0
    for lock in link["snapshot"]["locks"]:
        if lock["id"] not in link.get("active_lock_ids", []):
            continue
        for unit in work_units(state, lock):
            if lock["id"] in section_locks:
                available += 1
            for role, facts in unit["roles"].items():
                for fact in facts:
                    for eid in fact["evidence_ids"]:
                        located.setdefault(eid, []).append({"lock_id": lock["id"], "unit": unit["unit"], "role": role,
                            "fact_id": fact["id"], "link": fact.get("link")})
    relations = []
    for eid in project_ids:
        # Units list canonical passages; a draft may cite any alias of the same passage.
        canonical = canonical_ids(state, [eid])[0]
        rows = located.get(canonical) or [{"lock_id": None, "unit": None, "role": None, "fact_id": None, "link": "general_context"}]
        # A proof shared by several locks' units is outside only if none of them is the section's.
        outside = bool(section_locks and rows[0]["lock_id"] and not any(r["lock_id"] in section_locks for r in rows))
        for row in rows:
            relations.append({"evidence_id": eid, **row, "outside_section_locks": outside})
    own = [r for r in relations if r["lock_id"] in section_locks]
    compared = {pid for c in claims if c.get("kind") == "comparison" for pid in c.get("project_evidence_ids", [])}
    compared |= {canonical_ids(state, [pid])[0] for pid in compared}
    for row in relations:
        row["canonical_evidence_id"] = canonical_ids(state, [row["evidence_id"]])[0]
    confrontation = {"section_lock_ids": sorted(section_locks), "units_available": available,
        "units_cited": len({(r["lock_id"], r["unit"]) for r in own}),
        "roles_cited": sorted({r["role"] for r in own if r["role"]}),
        "comparison_claims": sum(c.get("kind") == "comparison" for c in claims),
        "compared_units": len({(r["lock_id"], r["unit"]) for r in own if r["evidence_id"] in compared
            or r["canonical_evidence_id"] in compared}),
        "outside_section_locks": sorted({r["evidence_id"] for r in relations if r["outside_section_locks"]})}
    return relations, confrontation


def draft_confrontation(state, section, draft):
    """Confrontation of a saved draft recomputed from its own text and claims, with the
    current passage identities (the stored trace may predate them). The draft is not
    changed. None for a draft saved before typed provenance existed (legacy)."""
    if "provenance" not in draft:
        return None
    raw = draft.get("raw_text") or draft.get("text") or ""
    ids = list(dict.fromkeys([*draft.get("project_evidence_ids", []), *PROJECT_MARKER.findall(raw)]))
    return project_relations(state, ids, draft.get("claims", []), section)[1]


# A reported value: a percentage, or a decimal number (« 84,7 », « 0.45 »). Integers, years,
# versions and references to figures/tables/sections are not results to trace.
NUMBER = re.compile(r"(?<![\w.,/-])(\d+(?:[.,]\d+)?)\s?%|(?<![\w.,/-])(\d+[.,]\d+)(?![\w.,]*\d)(?![.,]?\w)")
HEADING_NUMBER = re.compile(r"(?m)^(\s*(?:#{1,6}\s*|[-*]\s+)?)(\d+(?:\.\d+)+\.?)(?=\s+\S)")
REFERENCE_WORD = re.compile(r"(?:section|figure|fig\.|tableau|table|équation|equation|eq\.|chapitre|annexe|version|v)\s*$", re.I)


def _value_variants(value):
    return {value, value.replace(",", "."), value.replace(".", ",")}


def _proof_texts(toolkit, eids, pids):
    texts = []
    for eid in eids:
        row = toolkit.evidence.get(eid)
        if row:
            table = row.get("table") if isinstance(row.get("table"), dict) else {}
            texts.append((eid, "publication", " ".join([row.get("exact_quote", ""), str(table.get("caption") or ""),
                *map(str, table.get("headers") or []), *map(str, table.get("row") or [])])))
    project = diagnostic_evidence(toolkit.state)
    for pid in pids:
        if pid in project:
            texts.append((pid, "project", str(project[pid].get("quote") or "")))
    return [(eid, kind, re.sub(r"[\s\u00a0\u202f]+", " ", text)) for eid, kind, text in texts]


def numeric_trace(toolkit, text, evidence_ids, project_ids):
    """Each reported value of a draft located in a proof cited in the same paragraph.
    Returns (trace, missing): the trace records which proof contains which value; the
    missing values have no cited proof containing them (never matched to an uncited one)."""
    trace, missing = [], []
    for index, paragraph in enumerate(p for p in re.split(r"\n\s*\n", text or "") if p.strip()):
        eids = [e for e in dict.fromkeys(MARKER.findall(paragraph)) if e in evidence_ids]
        pids = [p for p in dict.fromkeys(PROJECT_MARKER.findall(paragraph)) if p in project_ids]
        proofs = _proof_texts(toolkit, eids, pids)
        plain = PROJECT_MARKER.sub(" ", MARKER.sub(" ", re.sub(r"\[paper_[\w-]+\]", " ", paragraph)))
        # Numbering of a heading or of an enumerated item (« ### 3.1 Extraire… », « 2.3. Résultats »)
        # is a position in the document, not a reported value (run dcf5d05b).
        plain = HEADING_NUMBER.sub(lambda m: m.group(1)+" "*len(m.group(2)), plain)
        for match in NUMBER.finditer(plain):
            if REFERENCE_WORD.search(plain[:match.start()][-14:]):
                continue
            value = match.group(1) or match.group(2)
            found = [(eid, kind) for eid, kind, quote in proofs if any(v in quote for v in _value_variants(value))]
            if found:
                trace.append({"paragraph": index, "value": value, "percent": bool(match.group(1)),
                    "evidence_ids": [eid for eid, _ in found], "origins": sorted({kind for _, kind in found})})
            else:
                missing.append({"paragraph": index, "value": value, "context": plain[max(0, match.start()-80):match.end()+40].strip()})
    return trace, missing
