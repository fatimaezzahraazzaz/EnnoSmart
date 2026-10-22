"""Non-destructive editorial diagnostics; source reuse is not itself redundancy.

These signals request a scientific judgment. They never merge sections, rewrite
drafts, remove facts or mark a manuscript approved.
"""
import itertools
import re
from .evidence import exact_source_valid


def words(text):
    return {w for w in re.findall(r"[\wÀ-ÿ]+", str(text).casefold()) if len(w) > 4}


def overlap(left, right):
    return len(left & right) / min(len(left), len(right)) if left and right else 0


def plan_review(outline):
    issues = []
    for section in outline.get("sections", []):
        if not section.get("scientific_objective") or not section.get("questions"):
            issues.append({"code": "section_role_to_clarify", "section_ids": [section["id"]],
                "message": "Préciser la fonction et la question scientifique propres à cette section."})
    for a, b in itertools.combinations(outline.get("sections", []), 2):
        left = words(a.get("scientific_objective", "") + " " + " ".join(a.get("questions", [])))
        right = words(b.get("scientific_objective", "") + " " + " ".join(b.get("questions", [])))
        shared_sources = overlap(set(a.get("article_ids", [])), set(b.get("article_ids", [])))
        if left and right and overlap(left, right) >= .65 and shared_sources >= .6:
            issues.append({"code": "plan_overlap_to_review", "section_ids": [a["id"], b["id"]],
                "message": "Questions et sources fortement communes : distinguer les contributions ou proposer un regroupement."})
    return {"issues": issues, "consultant_approval_required": bool(outline.get("approval") and issues),
        "guidance": "Signaux éditoriaux indicatifs, pas décisions de pertinence. Un plan approuvé reste inchangé "
            "jusqu'à validation du consultant. Ne pas supprimer de contenu scientifique utile."}


def coordination(state, section_id=None, budget_chars=5000):
    """What the other sections already establish, bounded: their question, a few saved
    claims and their reported values, and which of this section's proofs another section
    already develops. Signals for the model's own judgment; nothing is rewritten."""
    import json
    sections = state.get("outline", {}).get("sections", [])
    current = next((s for s in sections if s["id"] == section_id), None)
    own = set((current or {}).get("evidence_ids", [])) | {e for d in (current or {}).get("drafts", [])[-1:]
        for e in d.get("evidence_ids", [])}
    rows, owners = [], {}
    for section in sections:
        if section["id"] == section_id:
            continue
        draft = (section.get("drafts") or [{}])[-1]
        for eid in draft.get("evidence_ids", []):
            if not own or eid in own:
                owners.setdefault(eid, []).append(section["id"])
        rows.append({"section_id": section["id"], "title": section["title"][:160],
            "scientific_objective": str(section.get("scientific_objective", ""))[:300],
            "questions": [str(q)[:200] for q in section.get("questions", [])[:3]],
            "established_claims": [{"statement": c["statement"][:240], "evidence_ids": c.get("evidence_ids", [])[:4]}
                for c in draft.get("claims", [])[:4]],
            "numeric_results": sorted({row["value"] for row in draft.get("numeric_trace", [])})[:12]})
    result = {"other_sections": rows, "proof_owners": owners, "plan_review": plan_review(state.get("outline", {})),
        "guidance": "Choisir une contribution distincte. Pour un résultat déjà détaillé, renvoyer à la section qui "
            "le développe et n'en reprendre que ce qui est nécessaire à l'argument. La synthèse compare, distingue "
            "les conditions de validité et déduit des perspectives ; elle ne réexpose pas toutes les méthodes. "
            "Signaler les contradictions avec les preuves, notamment quand de nouveaux résultats deviennent disponibles. "
            "Ne pas étendre une ablation à tous les jeux cités par une publication : rattacher chaque chiffre au "
            "jeu, à la variante, à la métrique et au protocole effectivement évalués."}
    # Bounded: the furthest details go first (claims, then values), never the section index.
    for key in ("established_claims", "numeric_results"):
        for row in reversed(rows):
            if len(json.dumps(result, ensure_ascii=False)) <= budget_chars:
                break
            row[key] = []
    while len(json.dumps(result, ensure_ascii=False)) > budget_chars and result["proof_owners"]:
        result["proof_owners"].pop(next(iter(result["proof_owners"])))
    # Last resort, still bounded: fewer sections listed (their count is kept), then no plan signals.
    while len(json.dumps(result, ensure_ascii=False)) > budget_chars and rows:
        rows.pop()
        result["other_sections_omitted"] = result.get("other_sections_omitted", 0) + 1
    if len(json.dumps(result, ensure_ascii=False)) > budget_chars:
        result["plan_review"] = {"issues": result["plan_review"]["issues"][:2]}
    return result


def manuscript_review(state):
    issues = []
    sections = state.get("outline", {}).get("sections", [])
    for a, b in itertools.combinations(sections, 2):
        da, db = (a.get("drafts") or [{}])[-1], (b.get("drafts") or [{}])[-1]
        ea, eb = set(da.get("evidence_ids", [])), set(db.get("evidence_ids", []))
        if len(ea & eb) >= 3 and overlap(ea, eb) >= .6:
            issues.append({"code": "shared_evidence_to_review", "section_ids": [a["id"], b["id"]],
                "shared_evidence_ids": sorted(ea & eb), "message": "Vérifier que les mêmes preuves servent des questions différentes."})
        # The same proof paired with incompatible quantitative-availability claims
        # is not resolved automatically; new evidence can invalidate an older gap.
    numeric = {}
    for section in sections:
        draft = (section.get("drafts") or [{}])[-1]
        for row in draft.get("numeric_trace", []):
            key = (row["value"].replace(",", "."), tuple(sorted(row["evidence_ids"])))
            numeric.setdefault(key, set()).add(section["id"])
    for (value, evidence), owners in numeric.items():
        if len(owners) >= 3:
            issues.append({"code": "repeated_numeric_result", "section_ids": sorted(owners), "value": value,
                "evidence_ids": list(evidence), "message": "Résultat répété : conserver le développement dans sa section de référence."})
    # New documented results can outdate an older claim of missing quantitative
    # information. This is a review signal, never an automatic contradiction verdict:
    # the new metric may concern a different protocol or target.
    absence = re.compile(r"(?:pas|aucun\w*|sans).{0,65}(?:valeur\w*|évaluation\w*|résultat\w*|score\w*).{0,35}numérique", re.I)
    for section in sections:
        draft = (section.get("drafts") or [{}])[-1]
        for claim in draft.get("claims", []):
            if not absence.search(claim.get("statement", "")):
                continue
            owners = {state.get("evidence", {}).get(eid, {}).get("article_id") for eid in claim.get("evidence_ids", [])} - {None}
            for aid in owners:
                analysis = state.get("articles", {}).get(aid, {}).get("analysis") or {}
                current = [eid for field in ("results", "metrics") for row in analysis.get(field, []) if isinstance(row, dict)
                    and re.search(r"\d", row.get("statement", "")) for eid in row.get("evidence_ids", [])
                    if state.get("evidence", {}).get(eid, {}).get("article_id") == aid and
                    exact_source_valid(state["evidence"][eid], state.get("source_snapshots", {}))]
                if current:
                    issues.append({"code": "quantitative_availability_to_review", "section_ids": [section["id"]],
                        "article_id": aid, "statement": claim["statement"], "evidence_ids": sorted(set(current)),
                        "message": "Une ancienne absence de résultat numérique doit être réexaminée avec les résultats "
                            "désormais documentés, en distinguant métriques, protocoles et portée de la conclusion."})
    return issues


def document_quality(state):
    sections = state.get("outline", {}).get("sections", [])
    saved = [s for s in sections if s.get("drafts")]
    planned = {aid for s in sections for aid in s.get("article_ids", [])}
    missing = sorted(aid for aid in planned if not state.get("articles", {}).get(aid, {}).get("analysis"))
    eids = {eid for s in saved for eid in s["drafts"][-1].get("evidence_ids", [])}
    invalid = sorted(eid for eid in eids if eid not in state.get("evidence", {}) or
        not exact_source_valid(state["evidence"][eid], state.get("source_snapshots", {})))
    doc = state.get("document", {})
    review = state.get("scientific_review", {}).get("scientific", {})
    return {"saved_sections": len(saved), "total_sections": len(sections),
        "structurally_complete": bool(sections) and len(saved) == len(sections),
        "bibliography_sufficient": not missing and bool(state.get("literature_coverage_review", {}).get("sufficient")),
        "planned_articles_without_analysis": missing, "proofs_verified": bool(eids) and not invalid,
        "invalid_evidence_ids": invalid, "scientific_review_current": bool(review) and not doc.get("review_stale", True),
        "consultant_approved": bool(doc.get("approval")), "editorial_issues": manuscript_review(state)}
