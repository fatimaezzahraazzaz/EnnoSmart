"""Consultant instructions: exact words, origin, scope and status, kept across turns.

An instruction is what the consultant asked for (a writing preference or a
decision on the scope of the work). It is never a scientific fact: analyses,
evidence and relations stay the only scientific records. Capture is
deterministic (exact sentences of the consultant's own messages); no model
paraphrase is stored here. Article selection decisions keep their own record
(decisions.py); this register only adds the instructions that had no durable
place once older messages left the bounded context.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

DIRECTIVE = re.compile(
    r"\b(?:concentre|focalise|limite|restreins|uniquement|seulement|exclu|exclus|n['’]utilise\s+pas|n['’]inclus\s+pas|"
    r"ne\s+(?:modifie|touche|réécris|reecris|change|cite|mentionne|traite|réécrive|recrée|recree|relance)\s+(?:pas|plus|aucune?)|"
    r"sans\s+(?:modifier|toucher|réécrire|reecrire|changer)|garde|insiste|développe\s+davantage|developpe\s+davantage|approfondis|"
    r"privilégie|privilegie|évite|evite|mets\s+(?:en\s+avant|l['’]accent|plutôt|plutot)|souligne|met[s]?\s+l['’]accent|"
    r"style|ton\s+(?:plus|moins)|plus\s+(?:court|concis|détaillé|detaille|technique)|en\s+anglais|en\s+français|"
    r"toujours|jamais|oublie|annule|ne\s+tiens\s+plus\s+compte|finalement|au\s+lieu\s+de|plutôt\s+que|plutot\s+que)\b", re.I)
CANCEL = re.compile(r"\b(?:oublie|annule|ne\s+tiens\s+plus\s+compte|finalement|au\s+lieu\s+de|plutôt|plutot|"
    r"remplace|n['’]insiste\s+plus|plus\s+besoin|change\s+d['’]avis)\b", re.I)
DECISION = re.compile(r"\b(?:ne\s+(?:modifie|touche|réécris|reecris|change|réécrive|recrée|recree|relance)|sans\s+(?:modifier|toucher|réécrire|reecrire|changer)|"
    r"garde|exclu|exclus|n['’]utilise\s+pas|n['’]inclus\s+pas|uniquement|seulement|concentre|focalise|limite|restreins)", re.I)
ORDINALS = {"premi": 1, "deux": 2, "trois": 3, "quatr": 4, "cinq": 5, "six": 6, "sept": 7, "huit": 8, "neuf": 9, "dix": 10}
FIRST_N = re.compile(r"\b(?:les\s+)?(deux|trois|quatre|cinq|six|sept|\d+)\s+premi[eè]res\s+sections\b", re.I)
SECTION_NUMBERS = re.compile(r"\bsections?\s+((?:\d+\s*(?:,|et|à|a|-|–)?\s*)+)", re.I)
THIS_SECTION = re.compile(r"\b(?:cette|la\s+présente|la\s+presente)\s+section\b", re.I)
SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")
# Duration of an instruction. By default an instruction serves the request that states it
# (run 8cb4d68a: « Donne-moi le détail des deux articles, uniquement à partir des preuves
# enregistrées » was kept as a standing project rule because of « uniquement »). It becomes
# a standing rule only when the consultant says so: for this dossier, for what follows, in
# all (next) sections, from now on, systematically, always, until further notice.
PERSISTENT = re.compile(
    r"\b(?:pour\s+(?:tout\s+)?(?:ce|le|notre|mon)\s+(?:dossier|projet|état\s+de\s+l['’]art|etat\s+de\s+l['’]art|manuscrit)|"
    r"(?:pour|dans)\s+(?:toute\s+)?la\s+suite(?:\s+de\s+notre\s+travail)?|"
    r"(?:dans|pour)\s+(?:toutes\s+)?(?:les\s+)?(?:prochaines|futures|autres|toutes\s+les)\s+(?:sections|réponses|reponses|rédactions|redactions)|"
    r"dans\s+toutes\s+les\s+sections|pour\s+toutes\s+les\s+sections|dans\s+tout\s+(?:le\s+|l['’])?(?:document|manuscrit|état\s+de\s+l['’]art)|"
    r"(?:à|a)\s+partir\s+de\s+maintenant|désormais|desormais|dorénavant|dorenavant|systématiquement|systematiquement|"
    r"(?:à|a)\s+chaque\s+fois|chaque\s+fois\s+que|jusqu['’](?:à|a)\s+nouvel\s+ordre|en\s+permanence|"
    r"de\s+(?:façon|facon|manière|maniere)\s+(?:permanente|générale|generale|systématique|systematique)|"
    r"(?:garde|conserve|retiens|applique)\s+(?:cette|ces)\s+(?:règles?|regles?|consignes?|conventions?|préférences?|preferences?)|"
    r"toujours|jamais|from\s+now\s+on|going\s+forward|for\s+the\s+rest|always|never)\b", re.I)
# Legacy review only: a sentence that orders this request's task (verb of the task itself).
REQUEST_TASK = re.compile(r"^\s*(?:en\s+utilisant\b.*?,\s*)?(?:donne|compare|complète|complete|approfondis|révise|revise|"
    r"rédige|redige|cherche|recherche|analyse|détaille|detaille|présente|presente|liste|résume|resume|explique|trouve|"
    r"lis|lance|propose|reprends|continue|ne\s+te\s+limite)\b|\?\s*$", re.I)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _section_targets(state, sentence):
    sections = [s["id"] for s in state.get("outline", {}).get("sections", [])]
    numbers = []
    first = FIRST_N.search(sentence)
    if first:
        word = first.group(1).lower()
        count = int(word) if word.isdigit() else next((v for k, v in ORDINALS.items() if word.startswith(k)), 0)
        numbers.extend(range(1, count+1))
    for match in SECTION_NUMBERS.finditer(sentence):
        values = [int(n) for n in re.findall(r"\d+", match.group(1))]
        if len(values) == 2 and re.search(r"\d\s*(?:à|a|-|–)\s*\d", match.group(1)):
            values = list(range(values[0], values[1]+1))
        numbers.extend(values)
    ids = [sections[n-1] for n in dict.fromkeys(numbers) if 1 <= n <= len(sections)]
    if not ids and THIS_SECTION.search(sentence):
        current = (state.get("writing_focus") or {}).get("section_id") or state.get("document", {}).get("active_section_id")
        ids = [current] if current else []
    return ids


def _article_targets(state, registry, sentence):
    ids = re.findall(r"\bpaper_[0-9a-f]{8,}\b", sentence)
    lowered = sentence.casefold()
    for aid, article in (registry or {}).items():
        head = article.title.split(":", 1)[0].strip()
        if len(head) >= 5 and head.casefold() in lowered:
            ids.append(aid)
    return [aid for aid in dict.fromkeys(ids) if not registry or aid in registry]


def classify(state, registry, sentence):
    sections = _section_targets(state, sentence)
    articles = _article_targets(state, registry, sentence)
    scope = ("section" if sections else "article" if articles else
        "plan" if re.search(r"\bplan\b", sentence, re.I) else
        "writing" if re.search(r"rédig|redig|rédac|redac|section|écri|ecri|style|ton\b|paragraphe", sentence, re.I) else "project")
    return {"scope": scope, "target_ids": sections or articles,
        "kind": "decision" if DECISION.search(sentence) else "preference",
        "duration": "persistent" if PERSISTENT.search(sentence) else "request"}


def current_message_id(state):
    users = [m for m in state.get("messages", []) if m.get("role") == "user"]
    return users[-1].get("id") if users else None


def applies_now(state, row):
    """Active and in force for the current request: a standing rule, an instruction of the
    current message, or a record captured before durations existed (kept until the
    consultant reviews it: legacy_review proposes, it never changes anything itself)."""
    if row.get("status") != "active":
        return False
    duration = row.get("duration")
    return duration in (None, "persistent") or row.get("source_message_id") == current_message_id(state)


def capture_instructions(state, message, registry=None):
    """Record the directive sentences of one consultant message, verbatim."""
    if not message or message.get("role") != "user":
        return []
    register = state.setdefault("consultant_instructions", [])
    source_id = message.get("id") or "message_" + str(len(state.get("messages", [])))
    if any(item["source_message_id"] == source_id for item in register):
        return []
    added = []
    for raw in SENTENCE.findall(message.get("content", "")):
        sentence = raw.strip()
        if len(sentence) < 8 or not DIRECTIVE.search(sentence):
            continue
        row = {"id": "instruction_" + uuid.uuid4().hex[:16], "text": sentence, "source_message_id": source_id,
            "origin": "consultant_message", "order": len(register)+1, "status": "active", "created_at": _now(),
            **classify(state, registry, sentence)}
        if CANCEL.search(sentence):
            # The consultant revises an earlier instruction of the same scope and
            # targets: the older one is superseded, never deleted.
            for old in register:
                if (old["status"] == "active" and old["scope"] == row["scope"] and
                        (not row["target_ids"] or set(old["target_ids"]) & set(row["target_ids"]))):
                    old.update(status="superseded", superseded_by=row["id"], superseded_at=row["created_at"])
            row["supersedes"] = [old["id"] for old in register if old.get("superseded_by") == row["id"]]
        register.append(row)
        added.append(row)
    return added


def set_status(state, instruction_id, status):
    """Authenticated interface action: cancel or reactivate one instruction."""
    if status not in {"active", "cancelled"}:
        raise ValueError("Statut inconnu : active ou cancelled.")
    row = next((r for r in state.get("consultant_instructions", []) if r["id"] == instruction_id), None)
    if row is None:
        raise ValueError("Consigne introuvable.")
    row.update(status=status, status_changed_at=_now())
    return row


def relevant_instructions(state, task, *, section_id=None, article_ids=(), limit=12):
    """Active instructions applying to this task, oldest first (latest prevails)."""
    scopes = {"section": {"project", "writing", "section", "article"}, "planning": {"project", "plan", "writing", "article"},
        "argumentation": {"project", "article"}, "reading": {"project", "article"}, "selection": {"project", "article"},
        "recovery": {"project"}, "conversation": {"project", "plan", "writing", "section", "article"},
        "final": {"project", "writing", "article"}}.get(task, {"project"})
    rows = []
    for row in state.get("consultant_instructions", []):
        if not applies_now(state, row) or row["scope"] not in scopes:
            continue
        if row["scope"] == "section" and task == "section" and section_id not in row["target_ids"]:
            continue
        if row["scope"] == "article" and article_ids and not set(row["target_ids"]) & set(article_ids):
            continue
        rows.append({**{k: row[k] for k in ("id", "text", "scope", "target_ids", "kind", "order", "source_message_id")},
            "duration": row.get("duration") or "unreviewed"})
    return rows[-limit:]


def legacy_review(state):
    """Records captured before durations existed, classified by the current rule (nothing
    is changed): the proposal shown to the consultant before any targeted cleanup."""
    order = {m["id"]: i for i, m in enumerate(state.get("messages", []))}
    rows = []
    for row in state.get("consultant_instructions", []):
        if row.get("status") != "active" or row.get("duration"):
            continue
        persistent = bool(PERSISTENT.search(row["text"]))
        rows.append({"id": row["id"], "text": row["text"], "scope": row["scope"], "message_index": order.get(row["source_message_id"]),
            "proposal": "keep_persistent" if persistent else "request_only" if REQUEST_TASK.search(row["text"]) else "confirm"})
    return rows


def apply_review(state, decisions):
    """Consultant-approved cleanup: {instruction_id: "request" | "persistent"}. The record
    stays (text, origin, order); only its duration is set, with the review date."""
    for row in state.get("consultant_instructions", []):
        if row["id"] in decisions:
            if decisions[row["id"]] not in {"request", "persistent"}:
                raise ValueError("Durée inconnue : request ou persistent.")
            row.update(duration=decisions[row["id"]], duration_reviewed_at=_now())
    return [row for row in state.get("consultant_instructions", []) if row["id"] in decisions]


def instruction_packet(state, task, **targets):
    rows = relevant_instructions(state, task, **targets)
    if not rows:
        return None
    return {"items": rows, "nature": ("Consignes et décisions du consultant (mots exacts). Ce ne sont pas des faits "
        "scientifiques : elles orientent la tâche, jamais le contenu des preuves. En cas de contradiction, la consigne "
        "d'ordre le plus élevé prévaut.")}
