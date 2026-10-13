"""Extraction documentaire globale, traçable, sans appel LLM par chunk."""

from __future__ import annotations

import hashlib
import copy
import json
import os
import re
import tempfile
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from .chunking import _is_heading
from .provenance import has_explicit_external_attribution
from .config import GlobalFastPathConfig, _global_setting
from .schemas import DocumentInput, EvidenceRef, ExtractionItem
from .semantic_extractor import LLMClientProtocol, parse_json_payload
from .lock_controls import CONTROL_VERSION, suspicious_project_context
from .diagnostic_debug import record

VERSION = "global_fast_path_v2_lock_controls"
KINDS = {"objective", "lock", "method", "demarche", "parameter", "metric", "result"}
ROLES = {"project_evidence", "literature", "context", "admin"}
TEMPORAL = {"planned", "in_progress", "performed", "unclear"}

GLOBAL_SYSTEM = """
Analyse ENSEMBLE tous les documents bruts du dossier R&D. Ils ne sont pas un CIR final.
Chaque ligne P du registre est un passage exact identifié par passage_id, document_id
et positions. Réponds uniquement en JSON compact :
{"reviewed_passage_ids":["p1",...],"facts":[{"kind":"objective|lock|method|demarche|parameter|metric|result",
"statement":"fait précis, sans invention","source_role":"project_evidence|literature|context|admin",
"temporal_status":"planned|in_progress|performed|unclear",
"passage_ids":["p1"],"document_ids":["document_id"],"needs_review":false}]}.
Référence les passage_id ; ne recopie pas leurs textes. Un fait doit avoir une preuve
directe et son énoncé doit être compatible avec cette preuve. Ne retourne pas de fait
sans preuve. Les documents peuvent mélanger littérature et travaux du projet.
Une méthode publiée n'est pas une méthode utilisée par le projet ; un résultat
publié n'est pas un résultat expérimental du projet ; une limite de l'état de
l'art n'est pas un verrou effectivement rencontré. Une démarche est une étape
des travaux ; une méthode est un dispositif/protocole technique ; un paramètre
est une variable choisie ou modifiée ; une métrique est ce qu'on mesure ; un
résultat est la valeur ou l'observation effectivement obtenue. Les étapes
explicites d'une liste de travaux doivent être individualisées sans quota.
En cas d'attribution douteuse, needs_review=true et ne revendique pas une preuve
projet certaine. Conserve les faits de littérature pour audit. Réduis les
doublons uniquement lorsqu'il s'agit du même fait ; garde les résultats distincts.
Ne crée aucun passage_id, document_id, mesure, essai ou lien absent des sources.
reviewed_passage_ids doit contenir TOUS les passage_id lus, même ceux sans fait.
La provenance et la temporalité sont INDEPENDANTES : les objectifs, méthodes,
contraintes et travaux prévus DU PROJET sont project_evidence + planned.
Context désigne un background non attribuable au projet, pas un travail futur.
Les articles et solutions extérieures restent literature, même dans un dossier projet.

LOCK seulement = candidat de difficulté, limite ou incertitude technique
à soumettre ensuite au LockQualifier. Ne qualifie jamais toi-même un candidat
en VALID_LOCK.

Recherche les candidats verrous dans l'ensemble du dossier, qu'ils soient
explicitement nommés ou seulement révélés par plusieurs faits concordants.

Une difficulté peut apparaître dans n'importe quel type de fait déjà extrait :
objective, result, method, demarche, parameter ou metric.

IMPORTANT : les catégories ne sont pas exclusives.
Une information peut rester OBJECTIVE, RESULT, METHOD, DEMARCHE, PARAMETER
ou METRIC et servir EN PLUS de preuve à un candidat LOCK distinct.

Exemples génériques de situations pouvant justifier un candidat LOCK :
- échecs répétés ou performances insuffisantes ;
- comportement instable ou non maîtrisé ;
- correction ou adaptation qui ne résout pas complètement le problème ;
- difficulté à définir, mesurer ou valider de manière fiable la qualité
  ou la performance lorsqu'elle constitue elle-même une incertitude technique ;
- contrainte opérationnelle qui impose une limitation technique réelle
  et oblige à rechercher une solution non évidente ;
- incompatibilité entre les performances recherchées et les contraintes
  de données, ressources, architecture ou environnement ;
- impossibilité d'utiliser directement les solutions existantes pour
  des raisons techniques documentées ;
- nécessité d'expérimenter plusieurs approches parce qu'aucune solution
  n'est encore maîtrisée.

Ne transforme pas automatiquement :
- un simple objectif,
- une simple exigence,
- une simple contrainte métier,
- un mauvais résultat isolé,
- ou une limite uniquement issue de la littérature
en verrou projet.

Pour un verrou implicite, exige des preuves projet concordantes permettant
d'identifier une difficulté technique concrète ou une incertitude réellement
rencontrée.

Une limite de littérature reste littérature tant qu'aucune preuve projet
ne montre qu'elle concerne réellement les travaux.

Garde séparées les difficultés techniquement différentes, même lorsqu'elles
concernent le même projet ou la même technologie.

À la fin du MÊME appel, effectue une relecture dédiée uniquement aux
candidats LOCK.

Reconsidère TOUS les faits que tu viens d'extraire :
OBJECTIVE, RESULT, METHOD, DEMARCHE, PARAMETER et METRIC,
ainsi que leurs preuves projet.

Demande-toi si leur combinaison révèle une difficulté technique,
une limitation ou une incertitude réellement rencontrée dans le projet
qui mérite d'être soumise au LockQualifier.

Si oui, ajoute un fait LOCK distinct avec les passage_ids exacts qui
soutiennent cette difficulté.

N'enlève et ne reclassifie aucun fait déjà extrait :
ajoute le LOCK EN PLUS.

Cette relecture doit chercher les candidats avec un bon rappel,
sans décider de leur éligibilité CIR.
Le LockQualifier fera ensuite le tri.

Contrôle sémantique supplémentaire : repère dans le texte exact ET les titres
les sections « verrou », « difficulté technique », « incertitude scientifique
ou technique » et « problème à résoudre ». Le titre est seulement un SIGNAL.
Pour chaque passage signalé, vérifie obligatoirement : « Ce passage décrit-il
réellement une difficulté/incertitude technique, ou seulement un objectif/exigence ? »
Ne crée jamais un LOCK sur le seul titre de section.

Pour chaque fait important déjà classé OBJECTIVE / DEMARCHE / PARAMETER / METRIC
/ RESULT, demande aussi : « Cette information contient-elle une difficulté ou
incertitude technique DISTINCTE qui mérite un candidat LOCK ? » Relie ses
passages projet pertinents : difficulté non maîtrisée, incertitude scientifique
ou technique, limite persistante, comportement instable ou non reproductible,
incapacité à obtenir une propriété technique attendue, absence de méthode
suffisamment fiable, contradiction ou compromis technique non résolu.
La difficulté à définir ou valider un critère technique peut être un candidat
si cette difficulté empêche elle-même une évaluation fiable et reproductible.
Si les preuves soutiennent cette difficulté, garde le fait initial intact et
ajoute un LOCK distinct avec les mêmes passage_ids ou les passages pertinents.

Garde-fous : un objectif, une tâche, une exigence, un livrable, une activité de
cadrage, une contrainte seule, une métrique seule ou une difficulté business ou
organisationnelle seule ne suffisent pas. La littérature seule ne soutient pas
un LOCK projet. Chaque candidat projet exige des passages projet montrant la
difficulté/incertitude technique ; n'invente pas un échec ou une absence de
méthode fiable à partir du seul souhait de reproductibilité.
« Définir un protocole » reste OBJECTIVE seul. Une mesure de couverture seule
reste METRIC/RESULT. Une difficulté documentée à établir des critères techniques
fiables, ou une couverture insuffisante pour caractériser la qualité sans critère
reproductible satisfaisant établi, peut AUSSI soutenir un candidat LOCK.

Cette relecture se fait dans le MÊME appel.
Aucun nouvel appel LLM ne doit être ajouté.
""".strip()

TARGET_SYSTEM = """
Répare uniquement les faits ciblés de cette extraction R&D. Utilise exclusivement
les passages exacts fournis. Retourne {"facts":[...]} avec candidate_id
pour chaque candidat corrigé et les mêmes champs de fait :
passage_ids/document_ids autorisés. Ne réanalyse pas le reste du dossier.
Une preuve invalide ne devient pas valide par affirmation ; en cas de doute,
needs_review=true. Ne crée aucun fait ou identifiant absent du contexte fourni.
Si l'énoncé n'est pas ancré dans le passage, reformule-le avec les termes
techniques et valeurs réellement écrits dans ce passage, sans ajout.
Pour suspect_project_context, réponds précisément à : project_evidence ou context ?
Un travail prévu appartenant au projet est project_evidence + planned, sans
inventer de résultat réalisé. Si tu maintiens context, fournis provenance_reason
expliquant l'attribution extérieure/background ; en cas de doute needs_review=true.
""".strip()

COVERAGE_SYSTEM = GLOBAL_SYSTEM + "\nPasse ciblée : examine exclusivement les passages fournis ; confirme tous leurs IDs dans reviewed_passage_ids."

RECONCILE_SYSTEM = """
Réconcilie les faits de plusieurs lots du MÊME dossier. Utilise l'index global
et les passages exacts associés. Ne crée aucun nouveau fait. Retourne
{"facts":[{"fact_id":"f1","kind":"...","statement":"...",
"source_role":"...","temporal_status":"...","passage_ids":["..."],
"document_ids":["..."],"needs_review":false}]} pour CHAQUE fact_id fourni,
exactement une fois. Résous les contradictions de provenance/catégorie et les
doublons entre lots ; garde les preuves originales. Si incertain, marque
needs_review=true. Un résultat de littérature ne devient pas projet sans
preuve propre au projet.
""".strip()


@dataclass(frozen=True)
class Passage:
    passage_id: str
    document_id: str
    document_name: str
    char_start: int
    char_end: int
    text: str
    section_title: str = ""


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _fold(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(c for c in value if not unicodedata.combining(c))


def build_passages(documents: Iterable[DocumentInput], max_chars: int = 650) -> list[Passage]:
    """Découpe le texte complet en intervalles exacts, sans omission ni normalisation."""
    if max_chars < 80:
        raise ValueError("passage_max_chars_too_small")
    passages: list[Passage] = []
    seen_docs: set[str] = set()
    for doc in documents:
        if not doc.document_id or doc.document_id in seen_docs:
            raise ValueError("duplicate_or_empty_document_id")
        seen_docs.add(doc.document_id)
        text = doc.text
        section = ""
        heading_positions = {
            match.start(): match.group().strip()
            for match in re.finditer(r"^.*$", text, re.MULTILINE)
            if match.group().strip() and _is_heading(match.group().strip())
        }
        declared_sections = [(m.start(), m.group(1).strip()) for m in
                             re.finditer(r"\[SECTION\s*:\s*([^\]]+)\]", text, re.I)]
        start = 0
        while start < len(text):
            limit = min(start + max_chars, len(text))
            end = limit
            if limit < len(text):
                newline = text.rfind("\n", start + max_chars // 3, limit)
                if newline >= 0:
                    end = newline + 1
                else:
                    space = text.rfind(" ", start + max_chars // 2, limit)
                    if space >= 0:
                        end = space + 1
            boundary = next((position for position in sorted(heading_positions)
                             if start < position < end), None)
            if boundary is not None:
                end = boundary
            piece = text[start:end]
            if start in heading_positions:
                section = heading_positions[start]
            # Explicit document structure overrides guessed headings. Preserve
            # every interval and ID: this only repairs the section metadata.
            declared = [title for position, title in declared_sections if position <= start]
            if declared:
                section = declared[-1]
            elif declared_sections and declared_sections[0][0] < end:
                section = declared_sections[0][1]
            passages.append(Passage(
                passage_id=f"p{len(passages) + 1}", document_id=doc.document_id,
                document_name=doc.name, char_start=start, char_end=end,
                text=piece, section_title=section,
            ))
            start = end
    return passages


def _registry_prompt(passages: list[Passage]) -> str:
    documents = list(dict.fromkeys((p.document_id, p.document_name) for p in passages))
    header = "DOCUMENTS:\n" + "\n".join(
        f"{doc_id} | {name}" for doc_id, name in documents
    )
    rows = [
        f"{p.passage_id}|{p.document_id}|{p.char_start}:{p.char_end}|"
        + json.dumps(p.text, ensure_ascii=False)
        for p in passages
    ]
    return header + "\nPASSAGES EXACTS:\n" + "\n".join(rows)


def _tokens(value: str) -> set[str]:
    stop = {"pour", "dans", "avec", "cette", "leurs", "ainsi", "the", "that", "from", "using", "dans", "comme", "plus", "tout", "sont", "notre", "projet"}
    return {word for word in re.findall(r"[a-z0-9_]{4,}", _fold(value)) if word not in stop}


def _support_compatible(statement: str, passages: list[Passage]) -> bool:
    terms = _tokens(statement)
    source = set().union(*(_tokens(p.text) for p in passages)) if passages else set()
    overlap = terms & source
    return bool(overlap and (len(overlap) >= 2 or len(terms) <= 2 or
                             any(any(c.isdigit() for c in word) for word in overlap)))


def _literature_marker(passage: Passage) -> bool:
    heading = _fold(passage.section_title)
    return bool(re.search(r"etat de l.art|bibliograph|litterature|travaux existants|related work|state of the art", heading))


class GlobalFastExtractor:
    def __init__(self, llm: LLMClientProtocol, config: GlobalFastPathConfig, cache_dir: Path | None = None,
                 *, completeness_auditor=None):
        self.llm = llm
        self.config = config
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._rejected: list[dict] = []
        self._generation_audit: list[dict] = []
        self.completeness_auditor = completeness_auditor
        self.debug_sink = None

    def _audit_completeness(self, rows: list[dict], passages: list[Passage]) -> tuple[list[dict], dict]:
        if self.completeness_auditor is None:
            return rows, {"status": "disabled", "llm_calls": 0}
        try:
            self.completeness_auditor.debug_sink = self.debug_sink
            self.completeness_auditor.repair_callback = self._repair_completeness
            additions, audit = self.completeness_auditor.audit(passages, copy.deepcopy(rows), self._validate)
            # Existing rows retain their order and therefore their item/evidence IDs.
            return [*rows, *additions], audit
        except Exception as exc:
            error = f"{type(exc).__name__}:{exc}"
            print(f"[EnnoDiagnostic][CompletenessAudit] échec isolé: {error}", flush=True)
            return rows, {"status": "failed", "error": error, "llm_calls": 0}

    def preflight(self, documents: Iterable[DocumentInput]) -> dict:
        documents = list(documents)
        passages = build_passages(documents, self.config.passage_max_chars)
        prompt = _registry_prompt(passages)
        prompt_chars = len(GLOBAL_SYSTEM) + len(prompt) + 64
        # Conservative local estimate. The API may use a different tokenizer.
        input_tokens = (prompt_chars + 2) // 3
        cap = _global_setting("ENNOSMART_LLM_MAX_OUTPUT_TOKENS_CAP", "").strip()
        output_tokens = min(self.config.max_output_tokens, int(cap)) if cap.isdigit() and int(cap) > 0 else self.config.max_output_tokens
        estimated_output = max(12000, len(passages) * 140)
        client = getattr(self.llm, "client", None)
        context_window = (
            client._context_window_for_provider(self.config.provider)
            if client is not None and hasattr(client, "_context_window_for_provider")
            else self.config.context_window_tokens
        )
        # The output reservation includes reasoning tokens on GPT-5.
        mode = "global" if (
            prompt_chars <= self.config.max_prompt_chars
            and input_tokens + output_tokens + 4096 < context_window
            and output_tokens >= estimated_output
        ) else "multi_lot"
        return {
            "mode": mode, "documents": len(documents), "passages": len(passages),
            "source_chars": sum(len(doc.text) for doc in documents),
            "prompt_chars": prompt_chars, "estimated_input_tokens": input_tokens,
            "reserved_output_tokens": output_tokens,
            "estimated_output_tokens": estimated_output,
            "context_window_tokens": context_window,
            "no_truncation": True,
        }

    def _fingerprint(self, documents: list[DocumentInput]) -> str:
        signature = {
            "version": VERSION, "prompt_sha": _sha(GLOBAL_SYSTEM + TARGET_SYSTEM + RECONCILE_SYSTEM),
            "schema": "facts-v1", "controls": CONTROL_VERSION, "provider": self.config.provider,
            "model": self.config.model, "reasoning_effort": self.config.reasoning_effort,
            "passage_max_chars": self.config.passage_max_chars,
            # passage_id dépend de l'ordre de chargement ; cet ordre fait donc
            # partie de la clé du cache, même si le contenu est identique.
            "documents": [(doc.document_id, doc.name, _sha(doc.text)) for doc in documents],
        }
        return _sha(json.dumps(signature, ensure_ascii=False, sort_keys=True))

    def _cache_path(self, fingerprint: str) -> Path | None:
        return self.cache_dir / f"{fingerprint}.json" if self.cache_dir else None

    def _read_cache(self, fingerprint: str, by_passage: dict[str, Passage]) -> list[dict] | None:
        path = self._cache_path(fingerprint)
        if not path or not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("fingerprint") != fingerprint or payload.get("version") != VERSION:
                return None
            rows = payload["facts"]
            if not isinstance(rows, list):
                return None
            validated = [self._validate(row, by_passage,
                provenance_reviewed=bool(row.get("provenance_repaired") and row.get("provenance_reason"))) for row in rows]
            record(self, "fast.cache_read", {"fingerprint": fingerprint,
                "facts": validated, "original_debug_reference": payload.get("debug_reference")})
            return validated
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def _write_cache(self, fingerprint: str, rows: list[dict]) -> None:
        path = self._cache_path(fingerprint)
        if not path:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": VERSION, "fingerprint": fingerprint, "facts": rows,
                   "debug_reference": str(self.debug_sink.path) if self.debug_sink else None}
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as handle:
            json.dump(payload, handle, ensure_ascii=False)
            temporary = Path(handle.name)
        os.replace(temporary, path)

    def _validate(self, row: dict, by_passage: dict[str, Passage], *, provenance_reviewed=False) -> dict:
        if not isinstance(row, dict):
            raise ValueError("fact_not_object")
        kind = row.get("kind")
        role = row.get("source_role")
        temporal = row.get("temporal_status")
        statement = str(row.get("statement") or "").strip()
        ids = row.get("passage_ids")
        docs = row.get("document_ids")
        if kind not in KINDS or role not in ROLES or temporal not in TEMPORAL:
            raise ValueError("invalid_enum")
        if not statement or not isinstance(ids, list) or not ids or not isinstance(docs, list) or not docs:
            raise ValueError("missing_statement_or_evidence")
        if not all(isinstance(p, str) and p in by_passage for p in ids):
            raise ValueError("invalid_passage_id")
        evidence = [by_passage[p] for p in dict.fromkeys(ids)]
        real_docs = sorted({p.document_id for p in evidence})
        if sorted(set(docs)) != real_docs:
            raise ValueError("document_id_mismatch")
        if not _support_compatible(statement, evidence):
            raise ValueError("statement_not_supported_by_passage")
        needs_review = row.get("needs_review")
        if not isinstance(needs_review, bool):
            raise ValueError("invalid_needs_review")
        if not needs_review and not provenance_reviewed and suspicious_project_context(row, evidence):
            raise ValueError("suspect_project_context")
        # An article or state-of-art section cannot directly prove a project result.
        if role == "project_evidence" and kind in {"result", "lock", "method"} and all(
            _literature_marker(p) or has_explicit_external_attribution(p.text) for p in evidence
        ):
            raise ValueError("literature_evidence_as_project_fact")
        normalized = {
            "kind": kind, "statement": statement, "source_role": role,
            "temporal_status": temporal, "passage_ids": list(dict.fromkeys(ids)),
            "document_ids": real_docs, "needs_review": needs_review,
        }
        if row.get("validation_error"):
            normalized["validation_error"] = str(row["validation_error"])
        if row.get("reported_source_role") in ROLES:
            normalized["reported_source_role"] = row["reported_source_role"]
        if isinstance(row.get("provenance_reason"), str) and row["provenance_reason"].strip():
            normalized["provenance_reason"] = row["provenance_reason"].strip()
            if provenance_reviewed:
                normalized["provenance_repaired"] = True
        return normalized

    def _retry_passages(self, row: dict, by_passage: dict[str, Passage]) -> list[Passage]:
        ids = row.get("passage_ids") if isinstance(row, dict) else None
        known = [by_passage[p] for p in ids if p in by_passage] if isinstance(ids, list) else []
        if not known and isinstance(row, dict) and isinstance(row.get("document_ids"), list):
            allowed_docs = set(row["document_ids"])
            query = _tokens(str(row.get("statement") or ""))
            ranked = sorted(
                (p for p in by_passage.values() if p.document_id in allowed_docs),
                key=lambda p: -len(query & _tokens(p.text)),
            )
            known = [p for p in ranked[:4] if query & _tokens(p.text)]
        return list({p.passage_id: p for p in known}.values())

    def _retry_batch(self, pending: list[tuple[int, dict, str]],
                     by_passage: dict[str, Passage]) -> tuple[dict[int, dict], int]:
        """Revoit les anomalies ensemble, uniquement avec leurs passages exacts."""
        candidates = []
        for index, row, error in pending:
            known = self._retry_passages(row, by_passage)
            if known:
                candidates.append((index, row, error, known))
        fixed: dict[int, dict] = {}
        calls = 0
        cursor = 0
        while cursor < len(candidates):
            batch = []
            passage_map: dict[str, Passage] = {}
            while cursor < len(candidates):
                entry = candidates[cursor]
                proposed = {**passage_map, **{p.passage_id: p for p in entry[3]}}
                request = [*batch, entry]
                prompt = "CANDIDATS CIBLÉS:\n" + "\n".join(
                    json.dumps({"candidate_id": f"c{idx}", "error": err, "fact": row}, ensure_ascii=False)
                    for idx, row, err, _ in request
                ) + "\n" + _registry_prompt(list(proposed.values()))
                if batch and len(prompt) + len(TARGET_SYSTEM) + 64 > self.config.max_prompt_chars:
                    break
                if len(prompt) + len(TARGET_SYSTEM) + 64 > self.config.max_prompt_chars:
                    cursor += 1
                    break
                batch = request
                passage_map = proposed
                cursor += 1
            if not batch:
                continue
            prompt = "CANDIDATS CIBLÉS:\n" + "\n".join(
                json.dumps({"candidate_id": f"c{idx}", "error": err, "fact": row}, ensure_ascii=False)
                for idx, row, err, _ in batch
            ) + "\n" + _registry_prompt(list(passage_map.values()))
            calls += 1
            try:
                response = self._call(TARGET_SYSTEM, prompt)
                rows = response["facts"]
                if len(batch) == 1 and len(rows) == 1 and "candidate_id" not in rows[0]:
                    rows[0]["candidate_id"] = f"c{batch[0][0]}"
                by_candidate = {}
                for returned in rows:
                    if not isinstance(returned, dict):
                        continue
                    # Accept both flat facts and {candidate_id, fact: {...}}.
                    # Never mix envelope fields into the fact or bypass validation.
                    proposed = returned.get("fact", returned)
                    if not isinstance(proposed, dict):
                        continue
                    candidate_id = returned.get("candidate_id")
                    if proposed.get("candidate_id", candidate_id) != candidate_id:
                        continue
                    by_candidate[candidate_id] = proposed
                if len(by_candidate) != len(rows):
                    continue
                for index, _row, _error, known in batch:
                    proposed = by_candidate.get(f"c{index}")
                    if proposed is None:
                        continue
                    try:
                        reviewed = (_error == "suspect_project_context" and
                            isinstance(proposed.get("provenance_reason"), str) and
                            bool(proposed["provenance_reason"].strip()))
                        normalized = self._validate(proposed, by_passage, provenance_reviewed=reviewed)
                        if set(normalized["passage_ids"]).issubset({p.passage_id for p in known}):
                            fixed[index] = normalized
                    except (ValueError, TypeError, KeyError):
                        pass
            except Exception:
                # L'échec local conserve les faits vérifiables en needs_review.
                pass
        return fixed, calls

    @staticmethod
    def _quarantine(row: dict, error: str, by_passage: dict[str, Passage]) -> dict | None:
        if not isinstance(row, dict):
            return None
        ids = row.get("passage_ids")
        valid = [p for p in ids if isinstance(p, str) and p in by_passage] if isinstance(ids, list) else []
        if not valid:
            return None
        kind = row.get("kind")
        if kind not in KINDS:
            return None
        statement = str(row.get("statement") or "").strip()
        if not statement:
            return None
        passages = [by_passage[p] for p in dict.fromkeys(valid)]
        if not _support_compatible(statement, passages):
            return None
        return {
            "kind": kind, "statement": statement, "source_role": "context",
            "reported_source_role": row.get("source_role"),
            "temporal_status": row.get("temporal_status") if row.get("temporal_status") in TEMPORAL else "unclear",
            "passage_ids": [p.passage_id for p in passages],
            "document_ids": sorted({p.document_id for p in passages}),
            "needs_review": True, "validation_error": error,
        }

    def _validated_rows(self, rows: list, by_passage: dict[str, Passage]) -> tuple[list[dict], int]:
        results: list[dict | None] = [None] * len(rows)
        pending: list[tuple[int, dict, str]] = []
        for index, row in enumerate(rows):
            try:
                normalized = self._validate(row, by_passage)
                if normalized["needs_review"]:
                    pending.append((index, row, "uncertain_fact"))
                results[index] = normalized
            except (ValueError, TypeError, KeyError) as exc:
                pending.append((index, row, str(exc)))
        record(self, "fast.validation_pending", {"candidates": [
            {"candidate_id": f"c{i}", "fact": row, "reason": error}
            for i, row, error in pending]})
        repaired, calls = self._retry_batch(pending, by_passage) if pending else ({}, 0)
        for index, row, error in pending:
            results[index] = repaired.get(index) or results[index] or self._quarantine(row, error, by_passage)
            record(self, "fast.repair_decision", {"candidate_id": f"c{index}",
                "before": row, "after": results[index], "reason": error,
                "repair_accepted": index in repaired})
        for index, result in enumerate(results):
            if result is None:
                self._rejected.append({"candidate": rows[index], "reason": "no_valid_supported_evidence_after_targeted_retry"})
        final = [result for result in results if result is not None]
        record(self, "fast.validated_facts", {"facts": final, "rejected": self._rejected})
        return final, calls

    def _call(self, system: str, prompt: str) -> dict:
        if len(system) + len(prompt) + 64 > self.config.max_prompt_chars:
            raise ValueError("fast_prompt_over_budget")
        raw = self.llm.extract_json(system, prompt)
        record(self, "fast.raw_response", {"system": system, "prompt": prompt, "raw": raw})
        client = getattr(self.llm, "client", None)
        if client is not None and hasattr(client, "get_last_generation_meta"):
            meta = client.get_last_generation_meta()
            if isinstance(meta, dict):
                self._generation_audit.append({key: meta.get(key) for key in (
                    "request_name", "provider", "model", "response_model", "response_id",
                    "elapsed_seconds", "prompt_tokens", "completion_tokens", "total_tokens",
                )})
        payload = parse_json_payload(raw)
        rows = payload.get("facts") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ValueError("invalid_fast_facts_response")
        return payload

    def _repair_completeness(self, system, prompt):
        """Use the existing bounded targeted-repair client, not another model."""
        return self._call(system, prompt)

    def _covered_call(self, passages: list[Passage]) -> tuple[list[dict], int]:
        payload = self._call(GLOBAL_SYSTEM, _registry_prompt(passages))
        expected = {p.passage_id for p in passages}
        reported = payload.get("reviewed_passage_ids")
        covered = set(reported) if isinstance(reported, list) and all(isinstance(p, str) for p in reported) else set()
        if not covered.issubset(expected):
            raise ValueError("invented_coverage_passage_id")
        missing = [p for p in passages if p.passage_id not in covered]
        rows = list(payload["facts"])
        calls = 1
        if missing:
            # A second call is permitted only for the missing source intervals.
            if len(missing) == len(passages):
                raise ValueError("unverifiable_passage_coverage")
            repair = self._call(COVERAGE_SYSTEM, _registry_prompt(missing))
            reviewed = repair.get("reviewed_passage_ids")
            if not isinstance(reviewed, list) or set(reviewed) != {p.passage_id for p in missing}:
                raise ValueError("targeted_coverage_incomplete")
            rows.extend(repair["facts"])
            calls += 1
        return rows, calls

    def _lots(self, passages: list[Passage]) -> list[list[Passage]]:
        """Thèmes lexicaux inter-documents, puis paquets bornés en gardant tout."""
        groups: dict[str, list[Passage]] = {}
        for passage in passages:
            terms = sorted(_tokens(passage.section_title or passage.text[:120]))
            theme = terms[0][:5] if terms else "general"
            groups.setdefault(theme, []).append(passage)
        lots: list[list[Passage]] = []
        max_chars = max(4000, self.config.max_prompt_chars - len(GLOBAL_SYSTEM) - 10000)
        max_count = max(1, self.config.max_output_tokens // 140)
        current: list[Passage] = []
        for group in sorted(groups.values(), key=lambda rows: -len({p.document_id for p in rows})):
            by_doc: dict[str, list[Passage]] = {}
            for passage in group:
                by_doc.setdefault(passage.document_id, []).append(passage)
            interleaved = []
            while any(by_doc.values()):
                for doc_id in sorted(by_doc):
                    if by_doc[doc_id]:
                        interleaved.append(by_doc[doc_id].pop(0))
            for passage in interleaved:
                if current and (len(current) >= max_count or
                                len(_registry_prompt(current + [passage])) > max_chars):
                    lots.append(current)
                    current = []
                current.append(passage)
        if current:
            lots.append(current)
        if sorted(p.passage_id for lot in lots for p in lot) != sorted(p.passage_id for p in passages):
            raise ValueError("lot_passage_coverage_lost")
        return lots

    def _multi_lot(self, passages: list[Passage], by_passage: dict[str, Passage]) -> tuple[list[dict], int, int]:
        lots = self._lots(passages)
        with ThreadPoolExecutor(max_workers=self.config.max_workers) as pool:
            responses = list(pool.map(self._covered_call, lots))
        provisional: list[dict] = []
        retries = 0
        coverage_calls = 0
        for rows, call_count in responses:
            checked, count = self._validated_rows(rows, by_passage)
            provisional.extend(checked)
            retries += count
            coverage_calls += call_count - 1
        # Final review references exact passages. If it cannot fit, legacy handles
        # the dossier rather than silently losing inter-lot context.
        indexed = [{"fact_id": f"f{i+1}", **row} for i, row in enumerate(provisional)]
        if len(indexed) * 140 > self.config.max_output_tokens:
            raise ValueError("reconciliation_output_over_budget")
        used = [by_passage[p] for p in dict.fromkeys(p for row in indexed for p in row["passage_ids"])]
        prompt = "FAITS PROVISOIRES:\n" + json.dumps(indexed, ensure_ascii=False, separators=(",", ":")) + "\n" + _registry_prompt(used)
        reconciled = self._call(RECONCILE_SYSTEM, prompt)["facts"]
        by_id = {row.get("fact_id"): row for row in reconciled if isinstance(row, dict)}
        if set(by_id) != {row["fact_id"] for row in indexed} or len(reconciled) != len(indexed):
            raise ValueError("reconciliation_coverage_lost")
        final = []
        for original in indexed:
            row = by_id[original["fact_id"]]
            checked = self._validate(row, by_passage)
            if set(checked["passage_ids"]) != set(original["passage_ids"]):
                raise ValueError("reconciliation_changed_evidence")
            final.append(checked)
        return final, len(lots), retries + coverage_calls

    @staticmethod
    def _items(rows: list[dict], by_passage: dict[str, Passage]) -> list[ExtractionItem]:
        items = []
        for index, row in enumerate(rows, 1):
            for passage_id in row["passage_ids"]:
                passage = by_passage[passage_id]
                role = row["source_role"] if not row["needs_review"] else "context"
                item_id = f"it_fast_{index}_{passage_id}"
                evidence = EvidenceRef(
                    evidence_id=f"ev_fast_{_sha(item_id)[:16]}",
                    document_id=passage.document_id, document_name=passage.document_name,
                    chunk_id=passage_id, quote=passage.text,
                    section_title=passage.section_title, quote_verified=True,
                    passage_id=passage_id, char_start=passage.char_start,
                    char_end=passage.char_end,
                )
                items.append(ExtractionItem(
                    item_id=item_id, kind=row["kind"], statement=row["statement"],
                    evidence=evidence, temporal_status=row["temporal_status"],
                    needs_review=row["needs_review"],
                    explicitness=("explicit" if _fold(row["statement"]) in _fold(passage.text)
                                  else "implicit"),
                    metadata={
                        "source_role": role, "project_admission": role == "project_evidence" and not row["needs_review"],
                        "fast_path": True, "fact_group": index,
                        "passage_ids": row["passage_ids"], "document_ids": row["document_ids"],
                        "reported_source_role": row.get("reported_source_role") or row["source_role"],
                        "validation_error": row.get("validation_error", ""),
                        "provenance_reason": row.get("provenance_reason", ""),
                        "lock_candidate_admission": (row["kind"] == "lock" and
                            row["source_role"] == "project_evidence" and not row.get("validation_error")),
                    },
                ))
        return items

    def extract(self, documents: Iterable[DocumentInput]) -> tuple[list[ExtractionItem], dict]:
        documents = list(documents)
        self._rejected = []
        self._generation_audit = []
        passages = build_passages(documents, self.config.passage_max_chars)
        by_passage = {p.passage_id: p for p in passages}
        preflight = self.preflight(documents)
        fingerprint = self._fingerprint(documents)
        cached = self._read_cache(fingerprint, by_passage)
        if cached is not None:
            final, audit = self._audit_completeness(cached, passages)
            return self._items(final, by_passage), {**preflight, "cache_hit": True, "llm_calls": audit["llm_calls"], "fingerprint": fingerprint, "rejected_candidates": [], "completeness_audit": audit}
        if preflight["mode"] == "global":
            rows, coverage_calls = self._covered_call(passages)
            checked, retries = self._validated_rows(rows, by_passage)
            calls = coverage_calls + retries
            batches = 1
        else:
            checked, batches, retries = self._multi_lot(passages, by_passage)
            calls = batches + retries + 1
        if not checked and passages:
            raise ValueError("fast_output_has_no_valid_facts")
        # Full passage registry has deterministic 100% source coverage; facts are
        # never allowed to cite outside it. No cache entry for an invalid run.
        if not self._rejected:
            self._write_cache(fingerprint, checked)
        final, audit = self._audit_completeness(checked, passages)
        return self._items(final, by_passage), {
            **preflight, "cache_hit": False, "llm_calls": calls + audit["llm_calls"],
            "batch_count": batches, "targeted_retries": retries,
            "fingerprint": fingerprint, "rejected_candidates": self._rejected,
            "generation_audit": self._generation_audit,
            "completeness_audit": audit,
        }
