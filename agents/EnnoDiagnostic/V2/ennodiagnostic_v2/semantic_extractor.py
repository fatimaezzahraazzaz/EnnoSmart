import hashlib
import json
import re
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Protocol, Union

from .chunking import explicit_work_step_lines
from .prompts import (
    SEMANTIC_EXTRACTOR_SYSTEM,
    SOURCE_ROLE_RULES,
    build_extractor_user_prompt,
)
from .provenance import classify_passages, has_explicit_external_attribution, role_for_quote
from .schemas import Chunk, EvidenceRef, ExtractionItem


class LLMClientProtocol(Protocol):
    def extract_json(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> Union[str, Dict[str, Any]]:
        ...


class CallableLLMAdapter:
    def __init__(
        self,
        fn: Callable[[str, str], Union[str, Dict[str, Any]]],
    ):
        self.fn = fn

    def extract_json(self, system_prompt: str, user_prompt: str):
        return self.fn(system_prompt, user_prompt)


def parse_json_payload(payload):
    if isinstance(payload, dict):
        return payload

    if not isinstance(payload, str):
        raise TypeError("Le client LLM doit retourner dict ou str JSON.")

    text = re.sub(
        r"^```(?:json)?\s*",
        "",
        payload.strip(),
        flags=re.I,
    )
    text = re.sub(r"\s*```$", "", text)

    return json.loads(text)


def _extract_json_with_retry(
    llm,
    system_prompt: str,
    user_prompt: str,
    retries: int = 1,
):
    """Retente l'appel si le LLM renvoie un JSON invalide."""
    last_error = None

    for attempt in range(retries + 1):
        prompt = user_prompt

        if attempt > 0:
            prompt = (
                user_prompt
                + "\n\nIMPORTANT : la réponse précédente n'était pas un JSON valide. "
                "Réponds uniquement avec UN objet JSON strictement valide, complet, "
                "sans commentaire, sans markdown et sans texte avant ou après."
            )

        try:
            payload = llm.extract_json(
                system_prompt,
                prompt,
            )
            return parse_json_payload(payload)

        except json.JSONDecodeError as exc:
            last_error = exc
            print(
                "[EnnoDiagnostic][SemanticExtractor] JSON invalide "
                f"(tentative {attempt + 1}/{retries + 1}) : {exc}"
            )

    raise last_error


def _norm(text: str) -> str:
    text = unicodedata.normalize(
        "NFKC",
        str(text or ""),
    )
    return re.sub(r"\s+", " ", text).strip().lower()


def quote_is_in_source(
    quote: str,
    source: str,
) -> bool:
    q, s = _norm(quote), _norm(source)
    return bool(q) and q in s


PROJECT_TITLE_FIELD_RE = re.compile(
    r"^(?:titre des travaux(?: envisages?)?|titre du projet|"
    r"intitule du projet|nom du projet)\s*:"
)


def _is_project_title_evidence(quote: str, statement: str, source: str) -> bool:
    """Un intitulé de projet ne prouve aucune action technique réalisée/prévue."""
    normalized_quote = _norm(quote)
    for line in source.splitlines():
        folded_line = "".join(
            char for char in unicodedata.normalize("NFKD", line.lower())
            if not unicodedata.combining(char)
        ).strip()
        match = PROJECT_TITLE_FIELD_RE.match(folded_line)
        if not match:
            continue
        title_value = _norm(line.split(":", 1)[1])
        if (
            normalized_quote == _norm(line)
            or _norm(statement) == title_value
            or (
                len(normalized_quote) >= 20
                and len(normalized_quote) >= len(title_value) / 2
                and normalized_quote in title_value
            )
        ):
            return True
    return False


def _id(
    prefix: str,
    *parts: str,
) -> str:
    digest = hashlib.sha1(
        "|".join(parts).encode()
    ).hexdigest()[:14]

    return f"{prefix}_{digest}"


KEY_TO_KIND = {
    "objectives": "objective",
    "locks": "lock",
    "methods": "method",
    "demarches": "demarche",
    "parameters": "parameter",
    "metrics": "metric",
    "results": "result",
}


ALLOWED_TEMPORAL_STATUSES = {
    "planned",
    "in_progress",
    "performed",
    "unclear",
}

VARIABLE_REVIEW_SYSTEM = """
Tu relis des faits extraits d'un dossier R&D pour retrouver les variables
expérimentales distinctes cachées dans une description d'étape.
Réponds uniquement en JSON : {"parameters": [{"statement": "...",
"evidence_quote": "citation exacte du passage"}]}.
Une démarche est l'action de tester/comparer. Un paramètre est chaque choix,
configuration, condition, modèle ou dimension que cette action fait varier.
Une métrique est ce qui est mesuré ; une valeur observée est un résultat.
Crée un paramètre par dimension indépendante explicitement citée, sans
inventer ses modalités. La citation doit être une sous-chaîne exacte du
passage source. Si aucune variable n'est explicite, retourne [].
Ne transforme ni la métrique mesurée ni le résultat obtenu en paramètre.
""".strip()

FACT_PROVENANCE_SYSTEM = """
Tu vérifies la provenance de faits déjà extraits d'un dossier R&D brut.
La classification initiale des passages est fournie comme indice ; résous
l'attribution de chaque citation avec les phrases voisines et le sujet du
passage. Réponds seulement en JSON :
{"roles": [{"item_id": "...", "role": "project_evidence|literature|admin|context|unknown"}]}.
Une publication, ses auteurs, sa méthode et ses résultats restent literature
même si le dossier projet les rapporte. project_evidence exige que le dossier
attribue réellement le fait au projet analysé. Une phrase sans sujet explicite
peut continuer l'article précédent : ne la transforme pas automatiquement en
travail du projet. Pour une ligne de tableau, examine le titre, les colonnes,
le contexte du document et la provenance des essais : une mesure propre au
projet est project_evidence, un benchmark publié est literature. Le nom de
fichier seul ne constitue pas une preuve. Si l'attribution reste incertaine,
utilise unknown.
N'invente pas de fait et ne change pas sa catégorie.
""".strip()


def _may_hide_variables(item: ExtractionItem) -> bool:
    if item.kind != "demarche":
        return False
    text = item.statement.casefold()
    has_variation = re.search(
        r"\b(?:vari\w*|modifi\w*|compar\w*|configur\w*|chois\w*|"
        r"sélection\w*|selection\w*|test\w*)\b", text
    )
    has_multiple = bool(re.search(r"[,;/]|\bou pas\b|\bavec ou sans\b", text))
    return bool(has_variation and has_multiple)


def _duplicate_source_key(chunk: Chunk) -> tuple:
    """Ne rapproche que des copies du même document et du même passage."""
    name = Path(chunk.document_name).name
    stem = re.sub(r"_[0-9a-f]{12}$", "", Path(name).stem, flags=re.I)
    stem = re.sub(r"\s*\(\d+\)$", "", stem).casefold()
    return (
        stem,
        Path(name).suffix.casefold(),
        chunk.document_mode,
        chunk.metadata.get("content_origin"),
        chunk.metadata.get("document_type"),
        chunk.metadata.get("source_policy"),
        chunk.metadata.get("chunk_index"),
        chunk.metadata.get("document_outline"),
        chunk.section_title,
        chunk.context_before,
        chunk.text,
        chunk.context_after,
    )


def _reuse_for_duplicate_document(
    items: List[ExtractionItem], chunk: Chunk,
) -> List[ExtractionItem] | None:
    # Une citation douteuse doit être réévaluée dans le document cible.
    if any(
        not item.evidence.quote_verified
        or not quote_is_in_source(item.evidence.quote, chunk.text)
        for item in items
    ):
        return None
    reused = []
    for item in items:
        evidence = replace(
            item.evidence,
            evidence_id=_id("ev", chunk.chunk_id, item.evidence.quote),
            document_id=chunk.document_id,
            document_name=chunk.document_name,
            chunk_id=chunk.chunk_id,
            section_title=chunk.section_title,
            page=chunk.metadata.get("page"),
        )
        metadata = deepcopy(item.metadata)
        metadata["reused_from_item_id"] = item.item_id
        metadata["reused_from_document_id"] = item.evidence.document_id
        reused.append(replace(
            item,
            item_id=_id("it", chunk.chunk_id, item.item_id),
            evidence=evidence,
            metadata=metadata,
        ))
    return reused


class SemanticExtractor:
    def __init__(
        self,
        llm: LLMClientProtocol,
    ):
        self.llm = llm

    def extract_chunk(
        self,
        chunk: Chunk,
    ) -> List[ExtractionItem]:
        passages = classify_passages(self.llm, chunk)
        provenance = "\n".join(
            f"[{passage.passage_id}: {passage.role}] {passage.text}"
            for passage in passages
        )
        prompt = build_extractor_user_prompt(
            chunk.document_name,
            chunk.document_mode,
            chunk.section_title,
            chunk.context_before,
            chunk.text,
            chunk.context_after,
            provenance,
        )

        data = _extract_json_with_retry(
            self.llm,
            SEMANTIC_EXTRACTOR_SYSTEM
            + "\n\n"
            + SOURCE_ROLE_RULES,
            prompt,
            retries=1,
        )

        out: List[ExtractionItem] = []

        for key, kind in KEY_TO_KIND.items():
            values = data.get(key) or []

            if not isinstance(values, list):
                continue

            for i, value in enumerate(values):
                if not isinstance(value, dict):
                    continue

                statement = str(
                    value.get("statement") or ""
                ).strip()

                quote = str(
                    value.get("evidence_quote") or ""
                ).strip()

                if not statement or not quote:
                    continue

                if kind == "demarche" and _is_project_title_evidence(
                    quote, statement, chunk.text
                ):
                    continue

                verified = quote_is_in_source(
                    quote,
                    chunk.text,
                )

                evidence = EvidenceRef(
                    evidence_id=_id(
                        "ev",
                        chunk.chunk_id,
                        quote,
                    ),
                    document_id=chunk.document_id,
                    document_name=chunk.document_name,
                    chunk_id=chunk.chunk_id,
                    quote=quote,
                    section_title=chunk.section_title,
                    page=chunk.metadata.get("page"),
                    quote_verified=verified,
                )

                confidence = value.get("confidence")

                try:
                    confidence = (
                        float(confidence)
                        if confidence is not None
                        else None
                    )
                except (TypeError, ValueError):
                    confidence = None

                explicitness = str(
                    value.get("explicitness")
                    or "unknown"
                ).strip().lower()

                if explicitness not in {
                    "explicit",
                    "implicit",
                    "unknown",
                }:
                    explicitness = "unknown"

                # Le rôle vient exclusivement de la passe de provenance.
                # Une affirmation différente de la passe d'extraction ne le remplace pas.
                source_role = role_for_quote(quote, chunk.text, passages)

                temporal_status = str(
                    value.get("temporal_status")
                    or "unclear"
                ).strip().lower()

                if temporal_status not in ALLOWED_TEMPORAL_STATUSES:
                    temporal_status = "unclear"

                sequence_position = value.get(
                    "sequence_position"
                )

                try:
                    sequence_position = (
                        int(sequence_position)
                        if sequence_position is not None
                        else None
                    )
                except (TypeError, ValueError):
                    sequence_position = None

                if (
                    sequence_position is not None
                    and sequence_position <= 0
                ):
                    sequence_position = None

                out.append(
                    ExtractionItem(
                        item_id=_id(
                            "it",
                            chunk.chunk_id,
                            key,
                            str(i),
                            statement,
                        ),
                        kind=kind,
                        statement=statement,
                        evidence=evidence,
                        explicitness=explicitness,
                        rationale=str(
                            value.get("rationale") or ""
                        ).strip(),
                        technical_object=str(
                            value.get("technical_object") or ""
                        ).strip(),
                        unresolved_question=str(
                            value.get("unresolved_question")
                            or ""
                        ).strip(),
                        llm_confidence=confidence,
                        needs_review=(not verified or source_role == "unknown"),
                        temporal_status=temporal_status,
                        sequence_position=sequence_position,
                        metadata={
                            "source": "semantic_llm",
                            "raw_category": key,
                            "source_role": source_role,
                            "temporal_status": temporal_status,
                            "sequence_position": sequence_position,
                        },
                    )
                )

        variable_candidates = [item for item in out if _may_hide_variables(item)]
        if variable_candidates:
            review_prompt = "\n\n".join(
                f"DEMARCHE: {item.statement}\nPASSAGE SOURCE: {item.evidence.quote}"
                for item in variable_candidates
            )
            try:
                review = parse_json_payload(self.llm.extract_json(
                    VARIABLE_REVIEW_SYSTEM, review_prompt
                ))
                suggestions = review.get("parameters") or [] if isinstance(review, dict) else []
            except Exception:
                suggestions = []
            seen_parameters = {_norm(item.statement) for item in out if item.kind == "parameter"}
            for index, value in enumerate(suggestions):
                if not isinstance(value, dict):
                    continue
                statement = str(value.get("statement") or "").strip()
                quote = str(value.get("evidence_quote") or "").strip()
                if not statement or not quote or not quote_is_in_source(quote, chunk.text):
                    continue
                if _norm(statement) in seen_parameters:
                    continue
                seen_parameters.add(_norm(statement))
                source_role = role_for_quote(quote, chunk.text, passages)
                evidence = EvidenceRef(
                    evidence_id=_id("ev", chunk.chunk_id, quote),
                    document_id=chunk.document_id,
                    document_name=chunk.document_name,
                    chunk_id=chunk.chunk_id,
                    quote=quote,
                    section_title=chunk.section_title,
                    page=chunk.metadata.get("page"),
                    quote_verified=True,
                )
                out.append(ExtractionItem(
                    item_id=_id("it", chunk.chunk_id, "parameter_review", str(index), statement),
                    kind="parameter",
                    statement=statement,
                    evidence=evidence,
                    explicitness="explicit",
                    rationale="Variable expérimentale distincte du passage source.",
                    needs_review=source_role == "unknown",
                    temporal_status="unclear",
                    metadata={
                        "source": "variable_review_llm",
                        "raw_category": "parameters",
                        "source_role": source_role,
                        "temporal_status": "unclear",
                    },
                ))

        steps = explicit_work_step_lines(chunk.section_title, chunk.text)
        for position, step in enumerate(steps, 1):
            step_text = _norm(step)
            matching = next(
                (
                    item for item in out
                    if item.kind == "demarche"
                    and (
                        _norm(item.evidence.quote) == step_text
                        or (
                            _norm(item.evidence.quote) in step_text
                            and len(_norm(item.evidence.quote)) >= len(step_text) * 0.7
                        )
                    )
                ),
                None,
            )
            if matching is not None:
                matching.sequence_position = position
                matching.metadata["sequence_position"] = position
                # Une séquence de travaux explicite constitue un indice
                # structurel projet, sauf si la provenance dit le contraire.
                if matching.metadata["source_role"] == "unknown":
                    matching.metadata["source_role"] = "project_evidence"
                continue

            step_role = role_for_quote(step, chunk.text, passages)
            if step_role in {"literature", "admin", "context"}:
                continue

            evidence = EvidenceRef(
                evidence_id=_id("ev", chunk.chunk_id, step),
                document_id=chunk.document_id,
                document_name=chunk.document_name,
                chunk_id=chunk.chunk_id,
                quote=step,
                section_title=chunk.section_title,
                page=chunk.metadata.get("page"),
                quote_verified=True,
            )
            out.append(ExtractionItem(
                item_id=_id("it", chunk.chunk_id, "demarches", str(position), step),
                kind="demarche",
                statement=step,
                evidence=evidence,
                explicitness="explicit",
                rationale="Étape explicite dans une séquence de travaux techniques.",
                needs_review=True,
                temporal_status="unclear",
                sequence_position=position,
                metadata={
                    "source": "explicit_work_sequence",
                    "raw_category": "demarches",
                    "source_role": "project_evidence",
                    "temporal_status": "unclear",
                    "sequence_position": position,
                },
            ))

        if steps:
            kind_order = {kind: index for index, kind in enumerate(KEY_TO_KIND.values())}
            out.sort(key=lambda item: (
                kind_order[item.kind],
                item.sequence_position
                if item.kind == "demarche" and item.sequence_position is not None
                else 10**9,
            ))

        # Seconde vérification ciblée des faits non-verrous lorsque le passage
        # comporte des indices de publication. La passe de provenance initiale
        # reste antérieure à l'extraction ; ce contrôle résout les références
        # anaphoriques au niveau du fait cité.
        needs_fact_provenance_review = bool(re.search(
            r"\b(?:article|publication|paper|survey|auteurs?|authors?|"
            r"étude|study|état de l.art|et al\.)\b",
            chunk.text,
            flags=re.IGNORECASE,
        )) or any(
            item.kind != "lock" and item.metadata.get("source_role") == "unknown"
            for item in out
        ) or chunk.document_name.lower().endswith((".xlsx", ".xls", ".csv"))
        if needs_fact_provenance_review:
            candidates = [item for item in out if item.kind != "lock"]
            if candidates:
                review_prompt = (
                    f"DOCUMENT: {chunk.document_name}\nSECTION: {chunk.section_title}\n"
                    f"PLAN: {chunk.metadata.get('document_outline', '')[:800]}\n"
                    f"CONTEXTE AVANT: {chunk.context_before[:600]}\n"
                    f"CONTEXTE APRES: {chunk.context_after[:600]}\n"
                    f"PASSAGE COMPLET: {chunk.text[:3200]}\n"
                    "FAITS ET PROVENANCE INITIALE:\n"
                    + "\n".join(
                        f"{item.item_id} | {item.kind} | "
                        f"{item.metadata.get('source_role')} | {item.statement} | "
                        f"citation: {item.evidence.quote[:500]}"
                        for item in candidates
                    )
                )
                try:
                    review = parse_json_payload(self.llm.extract_json(
                        FACT_PROVENANCE_SYSTEM, review_prompt
                    ))
                    roles = {
                        str(value.get("item_id")): str(value.get("role", "")).strip().lower()
                        for value in review.get("roles", [])
                        if isinstance(value, dict)
                    }
                    for item in candidates:
                        role = roles.get(item.item_id)
                        if role == "project_evidence" and has_explicit_external_attribution(
                            item.evidence.quote
                        ):
                            role = "literature"
                        if role in {"project_evidence", "literature", "admin", "context", "unknown"}:
                            item.metadata["source_role"] = role
                            item.needs_review = item.needs_review or role == "unknown"
                except Exception:
                    pass

        return out

    def extract(
        self,
        chunks: Iterable[Chunk],
    ) -> List[ExtractionItem]:
        chunks = list(chunks)
        representatives: Dict[tuple, int] = {}
        primary_indices: List[int] = []
        source_indices: List[int] = []
        for index, chunk in enumerate(chunks):
            key = _duplicate_source_key(chunk)
            source_index = representatives.get(key)
            if (
                source_index is None
                or chunks[source_index].document_id == chunk.document_id
            ):
                representatives[key] = index
                source_index = index
                primary_indices.append(index)
            source_indices.append(source_index)

        extracted: Dict[int, List[ExtractionItem]] = {}
        if getattr(self.llm, "supports_fork", False) and len(primary_indices) > 1:
            worker_state = threading.local()

            def extract_independently(index: int) -> List[ExtractionItem]:
                worker = getattr(worker_state, "extractor", None)
                if worker is None:
                    forked_llm = self.llm.fork()
                    if forked_llm is None:
                        raise RuntimeError("Client LLM indépendant indisponible.")
                    worker = SemanticExtractor(forked_llm)
                    worker_state.extractor = worker
                return worker.extract_chunk(chunks[index])

            # La file LLM centrale borne aussi les appels concurrents. map()
            # restitue les résultats dans l'ordre des chunks d'origine.
            with ThreadPoolExecutor(max_workers=3) as pool:
                for index, items in zip(
                    primary_indices,
                    pool.map(extract_independently, primary_indices),
                ):
                    extracted[index] = items
        else:
            for index in primary_indices:
                extracted[index] = self.extract_chunk(chunks[index])

        out: List[ExtractionItem] = []
        for index, chunk in enumerate(chunks):
            source_index = source_indices[index]
            if source_index == index:
                out.extend(extracted[index])
                continue
            reused = _reuse_for_duplicate_document(
                extracted[source_index], chunk,
            )
            out.extend(reused if reused is not None else self.extract_chunk(chunk))
        return out
