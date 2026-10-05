"""Deterministic provenance, LOCK signal and coverage contracts.

Signals request a decision; they never manufacture a fact or a CIR status.
Semantic decisions remain the model's responsibility, with explicit references.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

CONTROL_VERSION = "lock-controls-v1"
DECISIONS = {"covered_by_existing_lock", "new_lock_added", "not_a_lock"}
NOT_LOCK_REASONS = {"objective_only", "requirement_only", "business_constraint",
                    "metric_only", "literature_only", "engineering_context",
                    "task_only", "context_only", "isolated_result",
                    "no_technical_uncertainty", "insufficient_project_evidence"}
SECTION_SIGNAL = re.compile(r"\b(verrous?|difficultes? techniques?|incertitudes?|"
                            r"problemes? techniques?|limitations?)\b")
CONTENT_SIGNAL = re.compile(r"\b(probleme\w*|absence\w*|difficul\w*|incertain\w*|incertitude\w*|echec\w*|"
    r"instab\w*|limite\w*|contraint\w*|non maitr\w*|non reproduct\w*|"
    r"reproductib\w*|fiab\w*|insuffisant\w*|persist\w*|impossib\w*|"
    r"plusieurs essais|plusieurs corrections|ne permet\w*|ne peut\w*)\b")
PROJECT_SECTION = re.compile(r"\b(travaux|projet|protocole experimental|demarche|"
    r"objectifs?|verrous? scientifiques|organisation des travaux|experiences|"
    r"resultats|contraintes|livrables?)\b")
ACTION = re.compile(r"\b(definir|concevoir|identifier|evaluer|comparer|etudier|"
    r"tester|implementer|realiser|developper|choisir|configurer|mesurer|"
    r"generer|identification|conception|evaluation|comparaison|etude|etapes?|"
    r"contraintes?|objectif\w*|protocole\w*|travaux|essais)\b")
PROJECT_SUBJECT = re.compile(r"\b(nous|notre projet|nos travaux|ce projet|le projet)\b")
EXTERNAL = re.compile(r"\b(etat de l.art|literature|litterature|cet article|"
    r"cette publication|les auteurs|this paper|the authors|solution externe)\b")


def folded(text):
    return "".join(c for c in unicodedata.normalize("NFKD", str(text or "").lower())
                   if not unicodedata.combining(c))


def suspicious_project_context(row, passages):
    if row.get("source_role") != "context":
        return False
    statement = folded(row.get("statement"))
    if EXTERNAL.search(statement):
        return False
    for passage in passages:
        body = folded(passage.text)
        # A mixed passage's article is not promoted because of a project heading.
        if EXTERNAL.search(body) and not PROJECT_SUBJECT.search(body):
            continue
        headings = folded(passage.section_title + " " + " ".join(
            re.findall(r"\[SECTION\s*:\s*([^\]]+)\]", passage.text, re.I)))
        attributed = PROJECT_SUBJECT.search(body) or PROJECT_SUBJECT.search(statement)
        activity_in_section = PROJECT_SECTION.search(headings) and ACTION.search(statement)
        if attributed or activity_in_section:
            return True
    return False


def lock_signals(passages, facts):
    signals = {}
    for passage in passages:
        body = folded(passage.text)
        headings = folded(passage.section_title + " " + " ".join(
            re.findall(r"\[SECTION\s*:\s*([^\]]+)\]", passage.text, re.I)))
        reasons = []
        if SECTION_SIGNAL.search(headings):
            reasons.append("section_signal")
        related = [(i, f) for i, f in enumerate(facts, 1)
                   if passage.passage_id in f["passage_ids"]]
        for _, fact in related:
            if CONTENT_SIGNAL.search(folded(fact["statement"])):
                reasons.append("fact_content_signal")
            if (fact["kind"] == "objective" and fact["source_role"] == "project_evidence"
                    and fact["temporal_status"] == "planned"):
                reasons.append("planned_project_objective_to_examine")
        if CONTENT_SIGNAL.search(body):
            reasons.append("passage_content_signal")
        if not reasons:
            continue
        key = "ls_" + hashlib.sha256((passage.document_id + ":" + passage.passage_id).encode()).hexdigest()[:12]
        signals[key] = {"signal_id": key, "passage_ids": [passage.passage_id],
                        "document_ids": [passage.document_id],
                        "related_fact_ids": [f"f{i}" for i, _ in related],
                        "signals": sorted(set(reasons))}
    return list(signals.values())


def validate_lock_coverage(decisions, signals, initial, additions_by_id, registry):
    expected = {s["signal_id"]: s for s in signals}
    initial_locks = {f"f{i}": f for i, f in enumerate(initial, 1)
                     if f["kind"] == "lock" and f["source_role"] == "project_evidence"}
    valid, errors = {}, {}
    if not isinstance(decisions, list):
        return [], {key: "missing_lock_coverage_decision" for key in expected}
    duplicates = set()
    for row in decisions:
        if not isinstance(row, dict) or row.get("signal_id") not in expected:
            errors["__unexpected__"] = "invented_lock_signal"
            continue
        key = row["signal_id"]
        if key in valid or key in errors:
            duplicates.add(key)
        try:
            decision = row.get("decision")
            if decision not in DECISIONS:
                raise ValueError("invalid_lock_coverage_enum")
            ids = row.get("passage_ids")
            if (not isinstance(ids, list) or not ids or
                    not all(isinstance(p, str) and p in registry for p in ids) or
                    not set(expected[key]["passage_ids"]).issubset(ids)):
                raise ValueError("invalid_lock_coverage_passages")
            if not isinstance(row.get("reason"), str) or not row["reason"].strip():
                raise ValueError("missing_lock_coverage_reason")
            existing, new = row.get("existing_lock_ids", []), row.get("new_lock_ids", [])
            if not all(isinstance(v, list) and all(isinstance(x, str) for x in v) for v in (existing, new)):
                raise ValueError("invalid_lock_coverage_references")
            if decision == "not_a_lock":
                if existing or new or row.get("reason_code") not in NOT_LOCK_REASONS:
                    raise ValueError("invalid_not_a_lock_reason")
            else:
                targets = initial_locks if decision == "covered_by_existing_lock" else additions_by_id
                refs = existing if decision == "covered_by_existing_lock" else new
                other = new if decision == "covered_by_existing_lock" else existing
                if not refs or other or any(ref not in targets for ref in refs):
                    raise ValueError("unknown_lock_coverage_reference")
                linked = [targets[ref] for ref in refs]
                if any(f["kind"] != "lock" or f["source_role"] != "project_evidence" for f in linked):
                    raise ValueError("non_project_lock_as_coverage")
                # The decision must reference actual support for both the signal
                # and the lock; lexical similarity never substitutes for evidence.
                proof = {p for f in linked for p in f["passage_ids"]}
                if any(p not in registry for p in proof):
                    raise ValueError("invalid_referenced_lock_support")
                # Existing IDs already resolve to fully validated facts. Do not
                # ask the model to recopy the same support chain in every row.
                # Enrich traceability deterministically from those references.
                row = {**row, "resolved_lock_passage_ids": sorted(proof)}
            valid[key] = dict(row)
        except (ValueError, TypeError) as exc:
            errors[key] = str(exc)
    for key in expected:
        if key in duplicates:
            valid.pop(key, None)
            errors[key] = "duplicate_lock_coverage_decision"
        elif key not in valid and key not in errors:
            errors[key] = "missing_lock_coverage_decision"
    return list(valid.values()), errors
