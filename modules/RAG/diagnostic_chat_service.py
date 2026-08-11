# -*- coding: utf-8 -*-
from __future__ import annotations

"""
Chat RAG flottant d'EnnoDiagnostic.

Règles :
- isolation stricte par organisme / projet / année ;
- la collection Chroma EnnoDiagnostic existante reste la source sémantique principale ;
- les passages bruts des documents clients sont indexés dans une collection compagnon
  propre au même projet ;
- les sorties EnnoDiagnostic sont utilisées comme analyse secondaire, jamais comme
  preuve brute du client ;
- aucune information d'un autre projet ou d'une autre année n'est chargée.
"""

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from modules.LLM.llm_client import LLMClient
from modules.RAG.project_store import ProjectStore
from modules.RAG.retriever import EnnoRetriever, detect_role_filter
from modules.RAG.vector_store import RAGVectorStore, encode_texts


CHAT_SERVICE_VERSION = "diagnostic_rag_chat_v7_6_9_unlimited_citations"
RAW_COLLECTION_SUFFIX = "_client_raw_chat_v7"

DOCUMENT_LIST_KEYS = (
    "documents",
    "raw_documents",
    "processed_documents",
    "extracted_documents",
    "documents_extracted",
    "input_documents",
)

SOURCE_TEXT_KEYS = (
    "raw_text",
    "original_text",
    "source_text",
    "extracted_text",
    "text",
    "content",
    "body",
    "transcription",
)

NESTED_SOURCE_KEYS = (
    "chunks",
    "text_chunks",
    "pages",
    "paragraphs",
    "blocks",
    "sections",
    "passages",
    "supporting_passages",
)

GENERATED_KEYS = {
    "reformulation",
    "summary",
    "resume",
    "synthesis",
    "synthese",
    "diagnostic",
    "diagnostic_complet",
    "llm_output",
    "answer",
    "content_generated",
    "generated_text",
}

BROAD_QUESTION_MARKERS = (
    "de quoi parle",
    "résume",
    "resume",
    "résumé",
    "globalement",
    "présente le dossier",
    "presente le dossier",
    "explique le projet",
)

STRENGTH_MARKERS = (
    "point fort",
    "points forts",
    "avantage",
    "apport",
    "contribution",
    "résultat positif",
    "resultat positif",
)

WEAKNESS_MARKERS = (
    "point faible",
    "points faibles",
    "faiblesse",
    "limite",
    "manque",
    "risque",
    "insuffisance",
    "preuve manquante",
)

LOCK_MARKERS = (
    "verrou",
    "incertitude",
    "difficulté scientifique",
    "difficulte scientifique",
    "difficulté technique",
    "difficulte technique",
)

CAUSAL_MARKERS = (
    "pourquoi",
    "pour quelle raison",
    "qu est ce qui justifie",
    "qu'est-ce qui justifie",
    "necessaire",
    "nécessaire",
    "au lieu de",
    "en raison de",
)

TECHNICAL_COMPARISON_MARKERS = (
    "difference technique",
    "différence technique",
    "principe technique",
    "fonctionnement",
    "approche technique",
    "complementaire",
    "complémentaire",
)

PROTOCOL_CONTRAST_MARKERS = (
    "maintenu identique",
    "maintenus identiques",
    "maintenue identique",
    "maintenues identiques",
    "strictement identique",
    "pas identique",
    "non identique",
    "mêmes paramètres",
    "memes parametres",
    "comparaison équitable",
    "comparaison equitable",
    "élément n'a pas pu",
    "element n a pas pu",
)


REFERENCE_NOISE_MARKERS = (
    "documents applicables",
    "documents de référence",
    "documents de reference",
    "table des matières",
    "table des matieres",
    "liste des figures",
    "liste des tableaux",
    "références bibliographiques",
    "references bibliographiques",
)

SECONDARY_BROAD_DOCUMENT_TYPES = {
    "etat art bibliographie",
    "norme reglementation",
    "template formulaire",
    "administratif",
}


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _safe_int(value: Any, default: int = -1) -> int:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except Exception:
        return default


def _normalise(value: Any) -> str:
    text = str(value or "").lower()
    table = str.maketrans("àâäéèêëîïôöùûüç’'", "aaaeeeeiioouuuc__")
    text = text.translate(table)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _hash(*values: Any, size: int = 16) -> str:
    raw = "|".join(_clean(value) for value in values)
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:size]


def _split_text(text: str, max_chars: int = 1700, overlap: int = 220) -> List[str]:
    text = _clean(text)
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    sentences = re.split(r"(?<=[.!?;:])\s+|\n+", text)
    chunks: List[str] = []
    current = ""

    for sentence in sentences:
        sentence = _clean(sentence)
        if not sentence:
            continue

        if len(current) + len(sentence) + 1 <= max_chars:
            current = f"{current} {sentence}".strip()
            continue

        if current:
            chunks.append(current)

        if len(sentence) <= max_chars:
            current = sentence
        else:
            for start in range(0, len(sentence), max_chars - overlap):
                part = sentence[start : start + max_chars].strip()
                if part:
                    chunks.append(part)
            current = ""

    if current:
        chunks.append(current)

    if overlap <= 0 or len(chunks) <= 1:
        return chunks

    with_overlap: List[str] = []
    previous_tail = ""
    for chunk in chunks:
        merged = f"{previous_tail} {chunk}".strip() if previous_tail else chunk
        with_overlap.append(merged[: max_chars + overlap])
        previous_tail = chunk[-overlap:]
    return with_overlap


def _document_name(item: Mapping[str, Any], default: str = "document_client") -> str:
    for key in ("document", "file_name", "filename", "source_document", "name"):
        value = _clean(item.get(key))
        if value:
            return Path(value).name
    return default


def _location_metadata(item: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "document_id": _clean(
            item.get("document_id")
            or item.get("source_document_id")
            or item.get("id")
        ),
        "source_path": _clean(
            item.get("source_path")
            or item.get("stored_path")
            or item.get("file_path")
            or item.get("path")
        ),
        "page_number": _safe_int(
            item.get("page_number")
            if item.get("page_number") is not None
            else item.get("page")
        ),
        "paragraph_index": _safe_int(
            item.get("paragraph_index")
            if item.get("paragraph_index") is not None
            else item.get("paragraph")
        ),
        "char_start": _safe_int(item.get("char_start") or item.get("start_char")),
        "char_end": _safe_int(item.get("char_end") or item.get("end_char")),
        "section_title": _clean(
            item.get("section_title")
            or item.get("section")
            or item.get("title")
        ),
    }


def _extract_text_nodes(
    node: Any,
    *,
    document: str,
    inherited_meta: Optional[Mapping[str, Any]] = None,
    path: str = "",
) -> List[Tuple[str, Dict[str, Any]]]:
    """
    Extrait seulement les champs source connus d'un objet document.

    Les clés de sortie LLM sont explicitement ignorées. Cette fonction n'est
    appelée que sur les listes de documents bruts ou sur les fichiers du dossier
    documents/processed du projet courant.
    """
    output: List[Tuple[str, Dict[str, Any]]] = []
    inherited = dict(inherited_meta or {})

    if isinstance(node, str):
        text = _clean(node)
        if len(text) >= 35:
            output.append((text, inherited))
        return output

    if isinstance(node, list):
        for index, child in enumerate(node):
            output.extend(
                _extract_text_nodes(
                    child,
                    document=document,
                    inherited_meta=inherited,
                    path=f"{path}[{index}]",
                )
            )
        return output

    if not isinstance(node, Mapping):
        return output

    local_meta = {**inherited, **_location_metadata(node)}
    local_document = _document_name(node, document) or document

    for key in SOURCE_TEXT_KEYS:
        if key in GENERATED_KEYS:
            continue
        value = node.get(key)
        if isinstance(value, str):
            text = _clean(value)
            if len(text) >= 35:
                output.append(
                    (
                        text,
                        {
                            **local_meta,
                            "document": local_document,
                            "json_path": f"{path}.{key}".strip("."),
                        },
                    )
                )

    for key in NESTED_SOURCE_KEYS:
        value = node.get(key)
        if isinstance(value, (list, dict)):
            output.extend(
                _extract_text_nodes(
                    value,
                    document=local_document,
                    inherited_meta=local_meta,
                    path=f"{path}.{key}".strip("."),
                )
            )

    return output


def _extract_markdown_sections(markdown: str) -> List[Tuple[str, str]]:
    text = str(markdown or "").strip()
    if not text:
        return []

    matches = list(re.finditer(r"(?m)^##\s+(.+?)\s*$", text))
    if not matches:
        return [("Rapport EnnoDiagnostic", text)]

    output: List[Tuple[str, str]] = []
    for index, match in enumerate(matches):
        title = _clean(match.group(1))
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if title and body:
            output.append((title, body))
    return output


def _iter_diagnostic_sections(payload: Mapping[str, Any]) -> Iterable[Tuple[str, str]]:
    """
    Lit les différentes formes persistées par DiagnosticRun V143+.
    """
    candidates: List[Mapping[str, Any]] = []

    for key in (
        "diagnostic_snapshot",
        "report",
        "script_or_pipeline_result",
        "display",
        "static_diagnostic",
    ):
        value = payload.get(key)
        if isinstance(value, Mapping):
            candidates.append(value)

    candidates.append(payload)

    seen: set[Tuple[str, str]] = set()

    def emit(title: Any, body: Any):
        clean_title = _clean(title)
        clean_body = _clean(body)
        signature = (_normalise(clean_title), _normalise(clean_body)[:500])
        if clean_title and len(clean_body) >= 25 and signature not in seen:
            seen.add(signature)
            return clean_title, clean_body
        return None

    for candidate in candidates:
        for key in (
            "canonical_sections",
            "sections_by_key",
            "sections_by_title",
            "report_sections",
            "diagnostic_sections_by_key",
            "diagnostic_sections",
        ):
            sections = candidate.get(key)
            if isinstance(sections, Mapping):
                for title, body in sections.items():
                    item = emit(title, body)
                    if item:
                        yield item

        markdown_values = [
            candidate.get("report_markdown"),
            candidate.get("content"),
            (candidate.get("diagnostic") or {}).get("content")
            if isinstance(candidate.get("diagnostic"), Mapping)
            else None,
        ]
        for markdown in markdown_values:
            if isinstance(markdown, str):
                for title, body in _extract_markdown_sections(markdown):
                    item = emit(title, body)
                    if item:
                        yield item


def _cosine_rank(query: str, values: Sequence[str]) -> List[float]:
    if not values:
        return []
    vectors = encode_texts([query, *values])
    if len(vectors) != len(values) + 1:
        return [0.0] * len(values)

    query_vector = vectors[0]
    scores: List[float] = []
    for vector in vectors[1:]:
        score = sum(float(a) * float(b) for a, b in zip(query_vector, vector))
        scores.append(score)
    return scores


DOCUMENT_SCOPE_ANAPHORA = (
    "ce document",
    "dans ce document",
    "du document",
    "sur ce document",
    "ce rapport",
    "dans ce rapport",
    "le rapport",
)

DIAGNOSTIC_ANALYSIS_MARKERS = (
    "diagnostic",
    "score cir",
    "score d eligibilite",
    "score eligibilite",
    "niveau de risque",
    "risque cir",
    "eligibilite cir",
    "pourquoi l agent",
    "sortie ennodiagnostic",
)

INTENT_ROLE_PLAN = {
    "broad": ["objectif", "methode", "resultat", "limite"],
    "causal": ["objectif", "limite", "methode", "verrou"],
    "objective": ["objectif", "methode", "resultat"],
    "technical_comparison": ["methode", "limite", "parametre", "objectif", "resultat"],
    "protocol_contrast": ["parametre", "methode", "limite", "objectif", "resultat"],
    "comparison": ["methode", "objectif", "parametre", "resultat", "limite"],
    "method": ["methode", "parametre", "objectif", "resultat"],
    "result": ["resultat", "methode", "parametre", "limite"],
    "limitation": ["limite", "verrou", "parametre", "resultat"],
    "lock": ["verrou", "limite", "objectif", "methode"],
    "strength": ["contribution", "resultat", "methode", "objectif"],
    "general": ["objectif", "methode", "resultat", "verrou", "limite"],
}


def _document_key(value: Any) -> str:
    raw = str(value or "").replace("\\", "/")
    name = raw.split("/")[-1]
    name = re.sub(r"_[a-f0-9]{10,64}(?=\.[^.]+$)", "", name, flags=re.I)
    name = re.sub(r"\.(pdf|docx?|docm|xlsx?|pptx?|msg|txt|json)$", "", name, flags=re.I)
    return _normalise(name)


def _text_signature(value: Any) -> str:
    normalized = _normalise(value)
    return _hash(normalized[:2200], size=28)


def _token_shingles(value: Any, size: int = 3) -> set[str]:
    tokens = _normalise(value).split()
    if len(tokens) < size:
        return set(tokens)
    return {
        " ".join(tokens[index : index + size])
        for index in range(0, len(tokens) - size + 1)
    }


def _near_duplicate_text(left: Any, right: Any) -> bool:
    left_norm = _normalise(left)
    right_norm = _normalise(right)
    if not left_norm or not right_norm:
        return False
    if left_norm == right_norm:
        return True
    if min(len(left_norm), len(right_norm)) >= 180 and (
        left_norm in right_norm or right_norm in left_norm
    ):
        return True

    left_shingles = _token_shingles(left_norm)
    right_shingles = _token_shingles(right_norm)
    if not left_shingles or not right_shingles:
        return False

    overlap = len(left_shingles & right_shingles)
    containment = overlap / max(1, min(len(left_shingles), len(right_shingles)))
    return containment >= 0.76


def _source_metadata(source: Mapping[str, Any]) -> Dict[str, Any]:
    meta = source.get("metadata")
    return dict(meta) if isinstance(meta, Mapping) else {}


def _source_kind(source: Mapping[str, Any]) -> str:
    meta = _source_metadata(source)
    return _clean(meta.get("source_kind") or "nlp_rag")


def _source_document(source: Mapping[str, Any]) -> str:
    meta = _source_metadata(source)
    return Path(_clean(meta.get("document")) or "Document client").name


def _evidence_nature(source: Mapping[str, Any]) -> str:
    meta = _source_metadata(source)
    role = _normalise(meta.get("role") or meta.get("final_role"))
    text = _normalise(source.get("text"))
    source_kind = _source_kind(source)

    if source_kind == "diagnostic_output":
        return "analyse_agent_secondaire"
    if any(marker in text for marker in ("pourrait", "susceptible", "hypothese", "a confirmer")):
        return "hypothese_ou_piste"
    if role == "objectif" or any(marker in text[:500] for marker in ("objectif", "le but", "vise a")):
        return "objectif"
    if role == "resultat" or re.search(r"\b\d+(?:[,.]\d+)?\s*%", str(source.get("text") or "")):
        return "resultat_mesure"
    if role in {"methode", "parametre"}:
        return "methode_ou_parametre"
    if role in {"limite", "verrou"}:
        return "limite_ou_incertitude"
    return "preuve_documentaire"


class DiagnosticRAGChatService:
    def __init__(
        self,
        *,
        organisme: str,
        project: str,
        year: str | int,
    ) -> None:
        self.store = ProjectStore(
            organisme=organisme,
            project=project,
            year=year,
        ).ensure()

        self.base_collection_name = (
            f"ennosmart_{self.store.organisme_id}_"
            f"{self.store.project_id}_{self.store.year_id}"
        )
        self.raw_collection_name = self.base_collection_name + RAW_COLLECTION_SUFFIX

        self.retriever = EnnoRetriever(
            organisme=organisme,
            project=project,
            year=year,
        )
        self.vector_store = RAGVectorStore(self.store.chroma_dir)
        self.llm = LLMClient()

    # ------------------------------------------------------------------
    # Index brut compagnon
    # ------------------------------------------------------------------

    @property
    def raw_manifest_path(self) -> Path:
        return self.store.rag_dir / "diagnostic_chat_raw_manifest.json"

    @property
    def fulltext_corpus_dir(self) -> Path:
        return self.store.documents_processed_dir / "fulltext_rag_v1"

    def _raw_fingerprint(self) -> str:
        rows: List[str] = []

        nlp_path = self.store.nlp_dir / "nlp_result.json"
        if nlp_path.exists():
            stat = nlp_path.stat()
            rows.append(f"{nlp_path}:{stat.st_size}:{stat.st_mtime_ns}")

        if self.fulltext_corpus_dir.exists():
            for path in sorted(self.fulltext_corpus_dir.rglob("*.json")):
                if path.is_file():
                    stat = path.stat()
                    rows.append(f"{path}:{stat.st_size}:{stat.st_mtime_ns}")
        elif self.store.documents_processed_dir.exists():
            for path in sorted(self.store.documents_processed_dir.rglob("*")):
                if path.is_file() and path.suffix.lower() in {".json", ".txt"}:
                    stat = path.stat()
                    rows.append(f"{path}:{stat.st_size}:{stat.st_mtime_ns}")

        return _hash(*rows, CHAT_SERVICE_VERSION, size=32)

    def _collection_count(self, collection_name: str) -> int:
        try:
            return int(self.vector_store.collection(collection_name).count())
        except Exception:
            return 0

    def _build_raw_chunks(self) -> List[Dict[str, Any]]:
        chunks: List[Dict[str, Any]] = []
        seen: set[str] = set()

        def add_text(
            text: str,
            *,
            document: str,
            metadata: Optional[Mapping[str, Any]] = None,
        ) -> None:
            clean_text = _clean(text)
            if len(clean_text) < 35:
                return

            for index, part in enumerate(_split_text(clean_text), start=1):
                signature = _hash(document, part[:1200], size=24)
                if signature in seen:
                    continue
                seen.add(signature)

                meta = dict(metadata or {})
                chunk_id = f"raw_{signature}_{index}"
                chunks.append(
                    {
                        "id": chunk_id,
                        "text": part,
                        "embedding_text": part,
                        "metadata": {
                            "project_id": self.store.project_id,
                            "year": self.store.year,
                            "annee": self.store.year,
                            "document": Path(document or "document_client").name,
                            "document_id": _clean(meta.get("document_id")),
                            "source_path": _clean(meta.get("source_path")),
                            "section_title": _clean(meta.get("section_title")),
                            "page_number": _safe_int(meta.get("page_number")),
                            "paragraph_index": _safe_int(meta.get("paragraph_index")),
                            "char_start": _safe_int(meta.get("char_start")),
                            "char_end": _safe_int(meta.get("char_end")),
                            "role": "document_brut",
                            "final_role": "document_brut",
                            "document_type": "client_raw_document",
                            "source_policy": "client_current_project_only",
                            "source_kind": "client_raw",
                            "chunk_level": "client_raw_passage",
                            "is_supporting_passage": False,
                            "chat_raw_version": CHAT_SERVICE_VERSION,
                            "json_path": _clean(meta.get("json_path")),
                        },
                    }
                )

        # V7 : source prioritaire = corpus complet persisté juste après extraction.
        # Il contient le texte intégral de chaque document avant la réduction NLP
        # en evidence packs. Si ce corpus existe, on n'indexe pas les dérivés NLP
        # comme pseudo-documents bruts.
        fulltext_files = []
        if self.fulltext_corpus_dir.exists():
            fulltext_files = [
                path for path in sorted(self.fulltext_corpus_dir.glob("*.json"))
                if path.is_file() and path.name != "manifest.json"
            ]

        if fulltext_files:
            for path in fulltext_files:
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if not isinstance(payload, Mapping):
                    continue

                document_name = _document_name(payload, path.stem)
                base_meta = {
                    "document_id": payload.get("document_id"),
                    "source_path": payload.get("source_path"),
                }
                sections = payload.get("sections") or []
                section_count = 0
                if isinstance(sections, list):
                    for section in sections:
                        if not isinstance(section, Mapping):
                            continue
                        section_text = _clean(section.get("text"))
                        if len(section_text) < 35:
                            continue
                        section_count += 1
                        add_text(
                            section_text,
                            document=document_name,
                            metadata={
                                **base_meta,
                                "section_title": section.get("section_title"),
                                "paragraph_index": section.get("paragraph_index"),
                                "char_start": section.get("char_start"),
                                "char_end": section.get("char_end"),
                                "json_path": f"fulltext.sections[{section_count - 1}]",
                            },
                        )

                # Documents sans titres structurés : indexer quand même le texte complet.
                if section_count == 0:
                    add_text(
                        _clean(payload.get("text")),
                        document=document_name,
                        metadata={
                            **base_meta,
                            "json_path": "fulltext.text",
                        },
                    )

            if chunks:
                return chunks

        nlp_path = self.store.nlp_dir / "nlp_result.json"
        if nlp_path.exists():
            try:
                data = json.loads(nlp_path.read_text(encoding="utf-8"))
            except Exception:
                data = {}

            if isinstance(data, Mapping):
                for list_key in DOCUMENT_LIST_KEYS:
                    documents = data.get(list_key)
                    if not isinstance(documents, list):
                        continue

                    for index, document_item in enumerate(documents):
                        if isinstance(document_item, Mapping):
                            document_name = _document_name(
                                document_item,
                                f"document_{index + 1}",
                            )
                        else:
                            document_name = f"document_{index + 1}"

                        for text, metadata in _extract_text_nodes(
                            document_item,
                            document=document_name,
                            path=list_key,
                        ):
                            add_text(
                                text,
                                document=metadata.get("document") or document_name,
                                metadata=metadata,
                            )

        processed_dir = self.store.documents_processed_dir
        if processed_dir.exists():
            for path in sorted(processed_dir.rglob("*")):
                if not path.is_file():
                    continue
                if path.name.lower().startswith("diagnostic_"):
                    continue

                if path.suffix.lower() == ".txt":
                    try:
                        add_text(
                            path.read_text(encoding="utf-8", errors="ignore"),
                            document=path.name,
                            metadata={"source_path": str(path)},
                        )
                    except Exception:
                        continue

                elif path.suffix.lower() == ".json":
                    try:
                        value = json.loads(
                            path.read_text(encoding="utf-8", errors="ignore")
                        )
                    except Exception:
                        continue

                    for text, metadata in _extract_text_nodes(
                        value,
                        document=path.name,
                        inherited_meta={"source_path": str(path)},
                        path=path.name,
                    ):
                        add_text(
                            text,
                            document=metadata.get("document") or path.name,
                            metadata=metadata,
                        )

        return chunks

    def ensure_raw_index(self) -> Dict[str, Any]:
        fingerprint = self._raw_fingerprint()
        previous: Dict[str, Any] = {}

        if self.raw_manifest_path.exists():
            try:
                previous = json.loads(
                    self.raw_manifest_path.read_text(encoding="utf-8")
                )
            except Exception:
                previous = {}

        current_count = self._collection_count(self.raw_collection_name)
        if (
            previous.get("fingerprint") == fingerprint
            and current_count > 0
        ):
            return {
                "ok": True,
                "reused": True,
                "chunks_indexed": current_count,
                "collection_name": self.raw_collection_name,
            }

        chunks = self._build_raw_chunks()

        if not chunks:
            return {
                "ok": True,
                "reused": False,
                "chunks_indexed": 0,
                "collection_name": self.raw_collection_name,
                "message": "Aucun document brut supplémentaire trouvé.",
            }

        report = self.vector_store.add_chunks(
            collection_name=self.raw_collection_name,
            chunks=chunks,
            reset=True,
        )

        manifest = {
            "version": CHAT_SERVICE_VERSION,
            "fingerprint": fingerprint,
            "collection_name": self.raw_collection_name,
            "chunks_indexed": int(report.get("added") or 0),
        }
        self.raw_manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return {
            "ok": True,
            "reused": False,
            **manifest,
        }

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    @staticmethod
    def _history_query(
        question: str,
        history: Sequence[Mapping[str, Any]],
    ) -> str:
        clean_question = _clean(question)
        normalized = _normalise(clean_question)

        # Les anciennes questions ne doivent pas polluer une nouvelle recherche.
        # On n'utilise le dernier tour que pour une formulation anaphorique.
        if not any(_normalise(marker) in normalized for marker in DOCUMENT_SCOPE_ANAPHORA):
            return clean_question

        previous_user_messages = [
            _clean(item.get("content"))
            for item in history
            if _clean(item.get("role")).lower() == "user"
            and _clean(item.get("content"))
        ][-1:]
        return "\n".join([*previous_user_messages, clean_question]).strip()

    @staticmethod
    def _question_intent(question: str) -> str:
        q = _normalise(question)

        if any(_normalise(marker) in q for marker in BROAD_QUESTION_MARKERS):
            return "broad"
        if any(_normalise(marker) in q for marker in CAUSAL_MARKERS):
            return "causal"
        if any(_normalise(marker) in q for marker in PROTOCOL_CONTRAST_MARKERS) or (
            any(marker in q for marker in ("identique", "meme", "mêmes", "memes"))
            and any(marker in q for marker in ("non identique", "pas identique", "strictement", "en revanche", "exception"))
        ):
            return "protocol_contrast"

        comparison = any(
            marker in q
            for marker in ("difference", "compar", "versus", "par rapport")
        )
        if comparison and any(
            _normalise(marker) in q
            for marker in TECHNICAL_COMPARISON_MARKERS
        ):
            return "technical_comparison"
        if comparison:
            return "comparison"
        if any(marker in q for marker in ("objectif", "quel est le but", "vise a", "finalite")):
            return "objective"
        if any(marker in q for marker in ("resultat", "performance", "precision", "accuracy", "mesure")):
            return "result"
        if any(_normalise(marker) in q for marker in WEAKNESS_MARKERS):
            return "limitation"
        if any(_normalise(marker) in q for marker in LOCK_MARKERS):
            return "lock"
        if any(_normalise(marker) in q for marker in STRENGTH_MARKERS):
            return "strength"
        if any(marker in q for marker in ("methode", "procedure", "protocole", "parametre", "comment")):
            return "method"
        return "general"

    @classmethod
    def _expanded_queries(cls, question: str) -> List[str]:
        intent = cls._question_intent(question)
        additions = {
            "causal": (
                "raison justification nécessité contrainte impossibilité variabilité "
                "alternative données réelles données synthétiques"
            ),
            "objective": (
                "objectif exact but de cette étude évaluer capacités démarche de comparaison"
            ),
            "technical_comparison": (
                "différence technique principe approche fonctionnement limite méthode alternative "
                "complémentarité effets radar majeurs phénomènes complexes méthode asymptotique "
                "lancer de rayons"
            ),
            "protocol_contrast": (
                "protocole comparaison équitable éléments maintenus identiques exactement les mêmes "
                "exception non identique différent différence cependant en revanche paramètres modèles "
                "données conditions propriétés configuration description"
            ),
            "comparison": (
                "comparaison différences points communs protocole identique résultats respectifs"
            ),
            "method": (
                "méthode procédure protocole paramètres étapes conditions expérimentales"
            ),
            "result": (
                "résultats mesurés résultat principal résultat final meilleure performance "
                "précision accuracy gain amélioration valeur conclusion expérimentale"
            ),
            "limitation": (
                "limite principale faiblesse contrainte insuffisance restriction discussion "
                "travaux futurs future work perspective conclusion"
            ),
            "lock": (
                "verrou scientifique incertitude difficulté non résolue preuve expérimentale"
            ),
        }
        queries = [_clean(question)]
        extra = additions.get(intent)
        if extra:
            queries.append(f"{_clean(question)}\nRecherche ciblée : {extra}")
        return queries

    @staticmethod
    def _intent_score_adjustment(
        *,
        intent: str,
        question: str,
        text: str,
        section: str,
        nature: str,
    ) -> float:
        q = _normalise(question)
        content = _normalise(f"{section} {text}")
        adjustment = 0.0

        def has_any(values: Sequence[str]) -> bool:
            return any(_normalise(value) in content for value in values)

        if intent == "causal":
            if has_any((
                "en raison", "impossibilite", "variabilite", "il n y a donc",
                "alternative", "necessaire", "difficile", "ne peut",
            )):
                adjustment += 1.20
            if nature == "resultat_mesure":
                adjustment -= 0.70
            if "synthet" in q and "synthet" in content:
                adjustment += 0.45

        elif intent == "objective":
            if has_any(("le but de cette etude", "objectif", "vise a", "evaluer les capacites")):
                adjustment += 1.35
            if nature == "resultat_mesure" and not any(
                marker in q for marker in ("resultat", "performance", "conclusion")
            ):
                adjustment -= 0.80

        elif intent == "protocol_contrast":
            features = DiagnosticRAGChatService._protocol_contrast_features(text)

            if features["same_hits"]:
                adjustment += 0.95
            if features["difference_hits"]:
                adjustment += 2.15
            if features["strong_pair"]:
                adjustment += 3.10
            elif features["weak_pair"]:
                adjustment -= 0.85

            if features["contrast_hits"] and features["difference_hits"]:
                adjustment += 0.35
            if features["approximate_same_hits"] and not features["difference_hits"]:
                adjustment -= 0.90

            if has_any((
                "parametre", "parametres", "protocole", "procedure",
                "configuration", "condition", "conditions", "modele",
                "modeles", "propriete", "proprietes", "description",
            )):
                adjustment += 0.40
            if nature == "resultat_mesure":
                adjustment -= 1.15
            if has_any((
                "travaux futurs", "sera teste ulterieurement",
                "perspective", "a l avenir",
            )) and not any(marker in q for marker in ("futur", "perspective")):
                adjustment -= 1.50

        elif intent == "technical_comparison":
            if has_any((
                "approche d analyse geometrique", "effets radar majeurs",
                "phenomenes electromagnetiques complexes", "methode asymptotique",
                "lancer de rayons", "technique de calcul alternative",
                "caracteristiques complementaires",
            )):
                adjustment += 1.55
            if has_any(("mocem", "salsa")):
                adjustment += 0.40
            if nature == "resultat_mesure" and not any(
                marker in q for marker in ("resultat", "performance", "meilleur", "superieur")
            ):
                adjustment -= 0.95

        elif intent == "comparison":
            if has_any(("comparer", "comparaison", "meme", "identique", "respectivement")):
                adjustment += 0.85

        elif intent == "method":
            if nature == "methode_ou_parametre" or has_any((
                "procedure", "methode", "protocole", "parametre", "entrainer", "evaluer",
            )):
                adjustment += 1.00

        elif intent == "result":
            if nature == "resultat_mesure":
                adjustment += 1.40

            # V7.6.4 : lorsqu'un document contient plusieurs résultats
            # intermédiaires, favoriser les passages qui présentent explicitement
            # un résultat central/final ou une conclusion expérimentale.
            if has_any((
                "resultat principal",
                "resultat final",
                "meilleure performance",
                "meilleur resultat",
                "atteint",
                "atteignent",
                "gain",
                "amelioration",
                "augmente",
                "increase",
                "improvement",
                "we reach",
                "we reached",
                "we found",
                "we demonstrate",
                "we conclude",
            )):
                adjustment += 0.65

            section_norm = _normalise(section)
            if any(
                marker in section_norm
                for marker in ("result", "conclusion")
            ):
                adjustment += 0.35

        elif intent in {"limitation", "lock"}:
            if nature == "limite_ou_incertitude" or has_any((
                "limite", "incertitude", "impossibilite", "reste", "probleme", "contrainte",
            )):
                adjustment += 1.15

            # Distinguer les limites du travail courant des limites de travaux
            # cités dans l'état de l'art. Les formulations auto-référentes et
            # les perspectives du document courant sont privilégiées.
            if has_any((
                "dans cette etude",
                "dans ce travail",
                "in this study",
                "in this work",
                "nous n avons pas",
                "nous ne",
                "we did not",
                "we do not",
                "reste limite",
                "remains limited",
                "travaux futurs",
                "future work",
                "nous prevoyons",
                "we plan",
                "perspective",
            )):
                adjustment += 0.70

            section_norm = _normalise(section)
            question_norm = _normalise(question)
            asks_literature = any(
                marker in question_norm
                for marker in (
                    "etat de l art",
                    "litterature",
                    "related work",
                    "related works",
                    "travaux existants",
                )
            )
            if (
                not asks_literature
                and any(
                    marker in section_norm
                    for marker in (
                        "related work",
                        "related works",
                        "etat de l art",
                        "litterature",
                        "bibliographie",
                    )
                )
            ):
                adjustment -= 1.10

        question_tokens = {
            token for token in q.split()
            if len(token) >= 5 and token not in {
                "quelle", "quelles", "pourquoi", "comment", "document",
                "rapport", "projet", "exactement", "utiliser",
            }
        }
        common = sum(1 for token in question_tokens if token in content)
        adjustment += min(0.55, common * 0.08)
        return adjustment

    @classmethod
    def _role_plan_for_question(cls, question: str) -> List[str]:
        return list(INTENT_ROLE_PLAN.get(cls._question_intent(question), INTENT_ROLE_PLAN["general"]))

    @staticmethod
    def _wants_diagnostic_analysis(question: str) -> bool:
        normalized = _normalise(question)
        return any(_normalise(marker) in normalized for marker in DIAGNOSTIC_ANALYSIS_MARKERS)

    @staticmethod
    def _source_signature(source: Mapping[str, Any]) -> str:
        # Déduplication par contenu réel et non par chunk_id : deux chunks
        # différents contenant le même passage ne doivent produire qu'une carte.
        return _text_signature(source.get("text"))

    @staticmethod
    def _is_broad_document_question(question: str) -> bool:
        normalized = _normalise(question)
        return any(
            _normalise(marker) in normalized
            for marker in BROAD_QUESTION_MARKERS
        )

    @staticmethod
    def _asks_for_references(question: str) -> bool:
        normalized = _normalise(question)
        return any(
            marker in normalized
            for marker in (
                "reference",
                "bibliographie",
                "document applicable",
                "document de reference",
                "source documentaire",
            )
        )

    @staticmethod
    def _wants_exhaustive_multi_document_coverage(question: str) -> bool:
        """Détecte une demande explicite portant sur chacun/tous les documents.

        Cette détection est volontairement générique. Elle ne connaît aucun nom
        de fichier, projet, client ou domaine métier.
        """
        normalized = _normalise(question)

        strong_markers = (
            "pour chacun des documents",
            "pour chaque document",
            "chacun des documents",
            "chaque document",
            "tous les documents",
            "l ensemble des documents",
            "dans chacun des documents",
            "dans chaque document",
            "for each document",
            "each document",
            "all documents",
        )

        if any(marker in normalized for marker in strong_markers):
            return True

        # Formulations proches : "les documents du projet, séparément..."
        has_documents = "document" in normalized
        has_exhaustive = any(
            marker in normalized
            for marker in (
                "chacun",
                "chaque",
                "tous",
                "toutes",
                "ensemble",
                "separement",
                "un par un",
            )
        )
        return bool(has_documents and has_exhaustive)

    @staticmethod
    def _is_noise_for_question(
        item: Mapping[str, Any],
        question: str,
    ) -> bool:
        if DiagnosticRAGChatService._asks_for_references(question):
            return False

        meta = _source_metadata(item)
        section = _normalise(meta.get("section_title"))
        text = _normalise(item.get("text"))[:1400]
        document_type = _normalise(meta.get("document_type"))

        if document_type in SECONDARY_BROAD_DOCUMENT_TYPES:
            return True

        return any(
            _normalise(marker) in section
            or _normalise(marker) in text[:500]
            for marker in REFERENCE_NOISE_MARKERS
        )

    def available_documents(self) -> List[Dict[str, Any]]:
        documents: Dict[str, Dict[str, Any]] = {}

        for collection_name in (self.base_collection_name, self.raw_collection_name):
            try:
                collection = self.vector_store.collection(collection_name)
                payload = collection.get(include=["metadatas"])
                metadatas = payload.get("metadatas") or []
            except Exception:
                continue

            for raw_meta in metadatas:
                meta = raw_meta if isinstance(raw_meta, Mapping) else {}
                name = Path(_clean(meta.get("document")) or "").name
                if not name or name == "Sortie EnnoDiagnostic":
                    continue

                key = _document_key(name)
                if not key:
                    continue

                current = documents.get(key, {})
                documents[key] = {
                    "document_id": _clean(meta.get("document_id") or current.get("document_id")),
                    "document_name": name,
                    "source_path": _clean(meta.get("source_path") or current.get("source_path")),
                }

        return sorted(documents.values(), key=lambda item: _normalise(item.get("document_name")))

    def _resolve_document_scope(
        self,
        question: str,
        requested_scope: Optional[Mapping[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        scope = dict(requested_scope or {})
        requested_id = _clean(scope.get("document_id"))
        requested_name = Path(_clean(scope.get("document_name")) or "").name

        if requested_id or requested_name:
            return {
                "document_id": requested_id,
                "document_name": requested_name,
                "mode": "explicit",
            }

        normalized_question = _normalise(question)
        matches: List[Tuple[int, Dict[str, Any]]] = []
        for document in self.available_documents():
            key = _document_key(document.get("document_name"))
            if not key:
                continue

            score = 0
            if key in normalized_question:
                score = 100
            else:
                important_tokens = [token for token in key.split() if len(token) >= 4]
                common = sum(1 for token in important_tokens if token in normalized_question)
                if important_tokens and common >= max(2, len(important_tokens) - 1):
                    score = 70 + common

            if score:
                matches.append((score, document))

        if not matches:
            return None

        matches.sort(key=lambda item: item[0], reverse=True)
        best_score, best_document = matches[0]
        if len(matches) > 1 and matches[1][0] == best_score:
            return None

        return {
            **best_document,
            "mode": "auto",
        }

    @staticmethod
    def _matches_document_scope(
        item: Mapping[str, Any],
        scope: Optional[Mapping[str, Any]],
    ) -> bool:
        if not scope:
            return True

        meta = _source_metadata(item)
        scope_id = _clean(scope.get("document_id"))
        item_id = _clean(meta.get("document_id"))
        scope_name = _document_key(scope.get("document_name"))
        item_name = _document_key(meta.get("document"))

        if scope_id and item_id:
            return scope_id == item_id
        if scope_name and item_name:
            return scope_name == item_name
        return False

    @staticmethod
    def _with_default_source_metadata(item: Mapping[str, Any]) -> Dict[str, Any]:
        output = dict(item)
        meta = _source_metadata(item)
        if not _clean(meta.get("source_kind")):
            meta["source_kind"] = "nlp_rag"
        if not _clean(meta.get("source_policy")):
            meta["source_policy"] = "project_current_documentary_source"
        output["metadata"] = meta
        return output

    @staticmethod
    def _protocol_contrast_features(text: str) -> Dict[str, Any]:
        """Détecte un contraste explicite sans vocabulaire métier codé en dur.

        V7.1 : un simple connecteur ("cependant", "mais"...) ne suffit plus
        à déclarer qu'un passage contient l'exception demandée. Un passage fort
        doit contenir à la fois :
        - un marqueur de similitude ;
        - un marqueur lexical explicite de différence / non-identité.
        """
        content = _normalise(text)
        same_markers = (
            "exactement les memes", "les memes", "meme parametre",
            "memes parametres", "meme modele", "memes modeles",
            "meme procedure", "meme protocole",
            "identique", "identiques", "similaire", "similaires",
        )
        strong_difference_markers = (
            "ne sont pas identique", "ne sont pas identiques",
            "n est pas identique", "n a pas ete identique",
            "n ont pas ete identiques", "n a pas pu etre identique",
            "ne peut pas etre identique", "ne pouvait pas etre identique",
            "ne sont pas les memes", "ne sont pas les meme",
            "pas les memes", "non identique", "pas identique",
            "pas strictement identique", "different", "differente",
            "differents", "differentes", "difference", "differences",
            "utilise moins", "utilisent moins", "moins de parametres",
            "utilise davantage", "utilisent davantage", "exception",
        )
        contrast_markers = (
            "cependant", "en revanche", "alors que", "tandis que",
            "mais", "sauf", "a l exception",
        )
        approximate_same_markers = (
            "presque a l identique", "presque identique",
            "quasi identique", "pratiquement identique",
        )

        same_hits = [marker for marker in same_markers if marker in content]
        diff_hits = [marker for marker in strong_difference_markers if marker in content]
        contrast_hits = [marker for marker in contrast_markers if marker in content]
        approximate_hits = [marker for marker in approximate_same_markers if marker in content]

        strong_pair = bool(same_hits and diff_hits)
        weak_pair = bool(same_hits and contrast_hits and not diff_hits)

        return {
            "same_hits": same_hits,
            "difference_hits": diff_hits,
            "contrast_hits": contrast_hits,
            "approximate_same_hits": approximate_hits,
            "strong_pair": strong_pair,
            "weak_pair": weak_pair,
            "explicit_pair": strong_pair,
        }

    def _protocol_contrast_anchor_candidates(
        self,
        *,
        question: str,
        document_scope: Optional[Mapping[str, Any]],
        limit: int = 24,
    ) -> List[Dict[str, Any]]:
        """Trouve en priorité les paragraphes qui portent réellement les deux côtés du contraste.

        V7.2 : avant de consulter le ranking vectoriel, on balaie directement le
        corpus FullText persisté après extraction. Cela évite qu'un chunk RAG très
        proche sémantiquement mais incomplet masque le paragraphe exact.
        Aucun vocabulaire métier ou nom de projet n'est codé en dur.
        """
        if self._question_intent(question) != "protocol_contrast":
            return []

        q_tokens = {
            token for token in _normalise(question).split()
            if len(token) >= 5 and token not in {
                "quelle", "quelles", "elements", "element",
                "maintenus", "identiques", "strictement", "entre",
                "comparaison", "equitable", "protocole",
            }
        }
        found: List[Dict[str, Any]] = []

        # 1) Source prioritaire : paragraphes du corpus documentaire complet.
        if self.fulltext_corpus_dir.exists():
            for path in sorted(self.fulltext_corpus_dir.glob("*.json")):
                if not path.is_file() or path.name == "manifest.json":
                    continue
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if not isinstance(payload, Mapping):
                    continue

                document_name = _document_name(payload, path.stem)
                scope_name = _document_key((document_scope or {}).get("document_name"))
                if scope_name and _document_key(document_name) != scope_name:
                    continue

                base_meta = {
                    "document": Path(document_name).name,
                    "document_id": _clean(payload.get("document_id")),
                    "source_path": _clean(payload.get("source_path")),
                    "role": "document_brut",
                    "final_role": "document_brut",
                    "document_type": "client_raw_document",
                    "source_policy": "client_current_project_only",
                    "source_kind": "client_raw",
                    "chunk_level": "client_raw_paragraph",
                    "is_supporting_passage": False,
                    "chat_raw_version": CHAT_SERVICE_VERSION,
                }

                sections = payload.get("sections") or []
                if not isinstance(sections, list) or not sections:
                    sections = [{
                        "section_title": "",
                        "text": _clean(payload.get("text")),
                        "char_start": 0,
                    }]

                for section_index, section in enumerate(sections):
                    if not isinstance(section, Mapping):
                        continue
                    body = _clean(section.get("text"))
                    if not body:
                        continue

                    # L'extracteur Office conserve les paragraphes avec des lignes
                    # vides. En cas de texte aplati, on garde aussi le bloc complet.
                    paragraphs = [
                        _clean(part)
                        for part in re.split(r"\n\s*\n+", body)
                        if len(_clean(part)) >= 35
                    ]
                    if not paragraphs:
                        paragraphs = [body]

                    units: List[Tuple[str, int]] = []
                    for paragraph_index, paragraph in enumerate(paragraphs):
                        units.append((paragraph, paragraph_index))
                        # Si le contraste est réparti sur deux paragraphes voisins,
                        # leur concaténation reste une preuve locale acceptable.
                        if paragraph_index + 1 < len(paragraphs):
                            neighbour = f"{paragraph}\n\n{paragraphs[paragraph_index + 1]}".strip()
                            if len(neighbour) <= 3200:
                                units.append((neighbour, paragraph_index))

                    for text_value, paragraph_index in units:
                        features = self._protocol_contrast_features(text_value)
                        if not features.get("explicit_pair"):
                            continue

                        normalized = _normalise(text_value)
                        token_overlap = sum(1 for token in q_tokens if token in normalized)
                        anchor_score = 18.0
                        anchor_score += 1.10 * len(features.get("same_hits") or [])
                        anchor_score += 1.45 * len(features.get("difference_hits") or [])
                        anchor_score += 0.55 * len(features.get("contrast_hits") or [])
                        anchor_score += min(1.8, 0.22 * token_overlap)
                        # Préférer une preuve locale et concise à une très grande section.
                        anchor_score -= min(1.2, max(0.0, (len(text_value) - 1800) / 1800.0))

                        local_start = body.find(paragraphs[paragraph_index]) if paragraphs else -1
                        section_start = _safe_int(section.get("char_start"))
                        char_start = (
                            section_start + local_start
                            if section_start >= 0 and local_start >= 0
                            else -1
                        )
                        char_end = char_start + len(text_value) if char_start >= 0 else -1

                        item = {
                            "id": f"fulltext_contrast_{_hash(document_name, section_index, paragraph_index, text_value[:900], size=24)}",
                            "text": text_value,
                            "distance": 0.0,
                            "metadata": {
                                **base_meta,
                                "section_title": _clean(section.get("section_title")),
                                "paragraph_index": paragraph_index,
                                "char_start": char_start,
                                "char_end": char_end,
                                "json_path": f"fulltext.sections[{section_index}]",
                            },
                            "_protocol_contrast_anchor_score": round(anchor_score, 4),
                            "_protocol_contrast_features": features,
                            "_protocol_contrast_anchor_origin": "fulltext_direct_scan",
                        }
                        if self._matches_document_scope(item, document_scope):
                            found.append(item)

        # 2) Compatibilité : balayage des collections Chroma déjà existantes.
        for collection_name in (self.raw_collection_name, self.base_collection_name):
            try:
                collection = self.vector_store.collection(collection_name)
                if int(collection.count()) <= 0:
                    continue
                payload = collection.get(include=["documents", "metadatas"])
            except Exception:
                continue

            ids = list(payload.get("ids") or [])
            documents = list(payload.get("documents") or [])
            metadatas = list(payload.get("metadatas") or [])

            for index, raw_text in enumerate(documents):
                text = _clean(raw_text)
                if not text:
                    continue
                meta = dict(metadatas[index] or {}) if index < len(metadatas) else {}
                item = {
                    "id": str(ids[index]) if index < len(ids) else _hash(collection_name, text),
                    "text": text,
                    "metadata": meta,
                    "distance": 0.0,
                }
                item = self._with_default_source_metadata(item)
                if not self._matches_document_scope(item, document_scope):
                    continue
                if self._is_noise_for_question(item, question):
                    continue

                features = self._protocol_contrast_features(text)
                if not features.get("explicit_pair"):
                    continue

                normalized = _normalise(text)
                token_overlap = sum(1 for token in q_tokens if token in normalized)
                anchor_score = 7.0
                anchor_score += 0.75 * len(features.get("same_hits") or [])
                anchor_score += 1.05 * len(features.get("difference_hits") or [])
                anchor_score += 0.45 * len(features.get("contrast_hits") or [])
                anchor_score += min(1.5, 0.18 * token_overlap)

                item["_protocol_contrast_anchor_score"] = round(anchor_score, 4)
                item["_protocol_contrast_features"] = features
                item["_protocol_contrast_anchor_origin"] = "chroma_scan"
                found.append(item)

        found.sort(
            key=lambda item: (
                1 if item.get("_protocol_contrast_anchor_origin") == "fulltext_direct_scan" else 0,
                float(item.get("_protocol_contrast_anchor_score") or 0.0),
            ),
            reverse=True,
        )

        output: List[Dict[str, Any]] = []
        for item in found:
            if any(_near_duplicate_text(item.get("text"), existing.get("text")) for existing in output):
                continue
            output.append(item)
            if len(output) >= limit:
                break
        return output

    def _rank_and_dedupe_sources(
        self,
        *,
        question: str,
        candidates: Sequence[Mapping[str, Any]],
        document_scope: Optional[Mapping[str, Any]],
        top_k: int,
    ) -> List[Dict[str, Any]]:
        prepared = [
            self._with_default_source_metadata(item)
            for item in candidates
            if isinstance(item, Mapping)
            and _clean(item.get("text"))
            and self._matches_document_scope(item, document_scope)
            and not self._is_noise_for_question(item, question)
        ]
        if not prepared:
            return []

        intent = self._question_intent(question)
        role_plan = self._role_plan_for_question(question)
        semantic_scores = _cosine_rank(
            question,
            [
                f"{_clean(_source_metadata(item).get('section_title'))} "
                f"{_clean(_source_metadata(item).get('role'))} "
                f"{_clean(item.get('text'))}"
                for item in prepared
            ],
        )

        ranked: List[Dict[str, Any]] = []
        for item, semantic_score in zip(prepared, semantic_scores):
            meta = _source_metadata(item)
            kind = _source_kind(item)
            role = _clean(meta.get("role") or meta.get("final_role"))
            text = _clean(item.get("text"))
            section = _clean(meta.get("section_title"))
            nature = _evidence_nature(item)
            try:
                distance = float(item.get("distance") or 0.0)
            except Exception:
                distance = 0.0

            score = float(semantic_score) * 2.35 + (1.0 / (1.0 + max(distance, 0.0)))
            if kind == "client_raw":
                score += 0.72
                if bool(meta.get("local_neighbor_expansion")):
                    score += 0.35
            elif kind == "nlp_rag":
                score += 0.42
            elif kind == "diagnostic_output":
                score -= 1.35

            if role in role_plan:
                score += max(0.15, 0.70 - 0.11 * role_plan.index(role))
            if _clean(meta.get("chunk_level")) == "nlp_main_item":
                score += 0.25
            if document_scope:
                score += 1.20

            anchor_score = float(item.get("_protocol_contrast_anchor_score") or 0.0)
            if intent == "protocol_contrast" and anchor_score > 0:
                # Un passage qui exprime explicitement les deux côtés du contraste
                # doit battre un passage seulement sémantiquement proche.
                score += min(5.5, 0.55 * anchor_score)

            score += self._intent_score_adjustment(
                intent=intent,
                question=question,
                text=text,
                section=section,
                nature=nature,
            )

            enriched = dict(item)
            enriched["metadata"] = meta
            enriched["_chat_score"] = round(score, 6)
            enriched["_chat_intent"] = intent
            enriched["_evidence_nature"] = nature
            ranked.append(enriched)

        ranked.sort(key=lambda item: float(item.get("_chat_score") or 0.0), reverse=True)

        output: List[Dict[str, Any]] = []
        per_document: Dict[str, int] = {}
        max_per_document = 5 if document_scope else 3

        for item in ranked:
            text = _clean(item.get("text"))
            document = _source_document(item)
            if per_document.get(document, 0) >= max_per_document:
                continue
            if any(
                _near_duplicate_text(text, existing.get("text"))
                for existing in output
            ):
                continue

            output.append(item)
            per_document[document] = per_document.get(document, 0) + 1
            if len(output) >= top_k:
                break

        return output


    @classmethod
    def _coverage_subquestions(cls, question: str) -> List[str]:
        """Décompose une question multi-besoins sans appel LLM.

        V7.6.6 :
        - conserve les séparations interrogatives déjà supportées ;
        - détecte aussi les énumérations nominales du type
          "indique l'objectif, la méthode, le résultat et la limite" ;
        - cette détection reste générique et s'appuie uniquement sur les
          catégories d'intention déjà utilisées par le RAG ;
        - aucune connaissance projet, document, classe ou domaine n'est codée.
        """
        clean_question = _clean(question).rstrip(" ?")
        if not clean_question:
            return []

        if cls._question_intent(clean_question) == "protocol_contrast":
            return [clean_question]

        interrogative = (
            r"(?:pourquoi|comment|quels?|quelles?|quel|quelle|"
            r"où|ou|combien|dans\s+quel(?:le)?s?)"
        )

        boundary = re.compile(
            rf"\s*;\s*"
            rf"|\s*,\s+(?={interrogative}\b)"
            rf"|\s*,\s+(?:et|mais)\s+(?={interrogative}\b)"
            rf"|\s+\b(?:et|mais)\s+(?={interrogative}\b)",
            flags=re.I,
        )

        parts = [
            _clean(part).strip(" ,;")
            for part in boundary.split(clean_question)
            if _clean(part).strip(" ,;")
        ]

        # Expansion symétrique : "avec A et avec B" -> deux recherches.
        repeated_prep = re.compile(
            r"\b(avec|pour|chez|sur)\s+([^,;?]{1,60}?)\s+et\s+\1\s+([^,;?]{1,60}?)(?=,|;|\?|$)",
            flags=re.I,
        )

        expanded: List[str] = []
        for part in parts:
            match = repeated_prep.search(part)
            if match:
                entity_a = _clean(match.group(2))
                entity_b = _clean(match.group(3))

                if (
                    entity_a
                    and entity_b
                    and len(entity_a.split()) <= 8
                    and len(entity_b.split()) <= 8
                ):
                    for entity in (entity_a, entity_b):
                        expanded.append(
                            _clean(
                                part[:match.start()]
                                + f"{match.group(1)} {entity}"
                                + part[match.end():]
                            )
                        )
                    continue

            expanded.append(part)

        # Fallback V7.6.6 : une énumération nominale peut ne contenir qu'un
        # seul interrogatif. Exemple générique :
        # "indique l'objectif, la méthode, le résultat et la limite".
        # Si au moins trois familles d'intention sont explicitement demandées,
        # on crée une sous-requête par famille pour garantir la couverture.
        if len(expanded) <= 1:
            normalized = _normalise(clean_question)

            need_families = [
                (
                    "objective",
                    ("objectif", "but", "finalite"),
                    "objectif principal du document",
                ),
                (
                    "method",
                    ("methode", "outil", "approche", "protocole", "technique", "procedure"),
                    "methode ou outil principal decrit dans le document",
                ),
                (
                    "result",
                    ("resultat", "resultats", "performance", "precision", "mesure"),
                    "resultat ou performance principale rapporte dans le document",
                ),
                (
                    "limitation",
                    ("limite", "limites", "faiblesse", "contrainte", "insuffisance"),
                    "principale limite ou contrainte identifiee dans le document",
                ),
                (
                    "lock",
                    ("verrou", "incertitude", "difficulte"),
                    "principal verrou ou incertitude identifie dans le document",
                ),
            ]

            detected: List[str] = []
            for _, markers, query_text in need_families:
                if any(marker in normalized for marker in markers):
                    detected.append(query_text)

            # Trois familles ou plus indiquent clairement une demande structurée.
            # On évite ainsi de fragmenter une question simple contenant seulement
            # un couple "méthode et résultat".
            if len(detected) >= 3:
                expanded = detected

        deduped: List[str] = []
        seen: set[str] = set()

        for item in expanded:
            normalized = _normalise(item)
            if len(normalized) < 8 or normalized in seen:
                continue

            seen.add(normalized)
            deduped.append(item)

        return deduped[:6] or [clean_question]


    @staticmethod
    def _coverage_source_key(source: Mapping[str, Any]) -> str:
        meta = _source_metadata(source)
        explicit = _clean(meta.get("rag_chunk_id") or source.get("id"))
        if explicit:
            return explicit
        return _hash(
            _source_document(source),
            _clean(meta.get("section_title")),
            _clean(source.get("text"))[:1200],
            size=28,
        )


    @staticmethod
    def _local_section_key(value: Any) -> str:
        """Normalise un titre de section sans dépendre du domaine métier."""
        text = _normalise(value)
        text = re.sub(r"^\[?\s*section\s*[:\-]\s*", "", text)
        return text.strip(" []:-")

    @staticmethod
    def _local_sentence_tokens(value: Any) -> set[str]:
        stop = {
            "avec", "dans", "pour", "plus", "moins", "entre", "cette",
            "leurs", "quelle", "quelles", "quels", "quel", "comment",
            "pourquoi", "ainsi", "donnees", "resultats", "rapport",
        }
        return {
            token
            for token in re.findall(r"[a-z0-9]{4,}", _normalise(value))
            if token not in stop
        }

    @classmethod
    def _materially_extends_local_context(cls, candidate: Any, seed: Any) -> bool:
        """True si `candidate` conserve le seed mais ajoute du contexte utile.

        Une fenêtre locale contient volontairement une grande partie du seed.
        La déduplication classique la classait donc comme doublon et supprimait
        précisément les phrases suivantes recherchées. Ici on n'autorise
        l'extension que si elle est sensiblement plus longue ET apporte de
        nouveaux tokens informatifs. Aucun terme métier n'est codé en dur.
        """
        candidate_text = _clean(candidate)
        seed_text = _clean(seed)
        if not candidate_text or not seed_text:
            return False
        if len(candidate_text) < len(seed_text) + 120:
            return False

        candidate_tokens = cls._local_sentence_tokens(candidate_text)
        seed_tokens = cls._local_sentence_tokens(seed_text)
        if not seed_tokens:
            return len(candidate_text) >= int(len(seed_text) * 1.30)

        shared_ratio = len(candidate_tokens & seed_tokens) / max(1, len(seed_tokens))
        novel_tokens = candidate_tokens - seed_tokens
        normalized_candidate = _normalise(candidate_text)
        normalized_seed = _normalise(seed_text)
        contains_seed = bool(normalized_seed and normalized_seed in normalized_candidate)

        return (
            (contains_seed or shared_ratio >= 0.60)
            and len(novel_tokens) >= 4
            and len(candidate_text) >= int(len(seed_text) * 1.18)
        )

    def _local_neighbor_candidates(
        self,
        *,
        seed: Mapping[str, Any],
        question: str,
        document_scope: Optional[Mapping[str, Any]],
        limit: int = 2,
    ) -> List[Dict[str, Any]]:
        """Récupère le voisinage documentaire local d'une preuve déjà trouvée.

        V7.5 ne lance aucune nouvelle recherche métier. Elle repart d'un seed
        réellement retrouvé par le retriever, ouvre la section FullText du même
        document, localise le seed puis construit deux fenêtres complémentaires :
        contexte précédent et contexte suivant. Les fenêtres sont ensuite
        rerankées avec la sous-question par le scorer existant.
        """
        if limit <= 0 or not self.fulltext_corpus_dir.exists():
            return []
        if not self._matches_document_scope(seed, document_scope):
            return []

        seed_text = _clean(seed.get("text"))
        if len(seed_text) < 35:
            return []

        seed_meta = _source_metadata(seed)
        seed_document = _document_key(_source_document(seed))
        seed_section = self._local_section_key(seed_meta.get("section_title"))
        if not seed_document:
            return []

        question_tokens = self._local_sentence_tokens(question)
        seed_tokens = self._local_sentence_tokens(seed_text)
        results: List[Dict[str, Any]] = []

        for path in sorted(self.fulltext_corpus_dir.glob("*.json")):
            if not path.is_file() or path.name == "manifest.json":
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, Mapping):
                continue

            document_name = _document_name(payload, path.stem)
            if _document_key(document_name) != seed_document:
                continue

            base_meta = {
                "document_id": _clean(payload.get("document_id") or seed_meta.get("document_id")),
                "source_path": _clean(payload.get("source_path") or seed_meta.get("source_path")),
            }
            sections = payload.get("sections") or []
            if not isinstance(sections, list):
                continue

            section_candidates: List[Tuple[float, int, Mapping[str, Any], str]] = []
            for section_index, section in enumerate(sections):
                if not isinstance(section, Mapping):
                    continue
                section_text = _clean(section.get("text"))
                if len(section_text) < 80:
                    continue
                section_title = self._local_section_key(section.get("section_title"))
                normalized_section = _normalise(section_text)
                normalized_seed = _normalise(seed_text)

                score = 0.0
                if seed_section and section_title:
                    if seed_section == section_title:
                        score += 12.0
                    elif seed_section in section_title or section_title in seed_section:
                        score += 7.0
                if normalized_seed and normalized_seed in normalized_section:
                    score += 30.0
                else:
                    section_tokens = self._local_sentence_tokens(section_text)
                    score += min(9.0, 0.55 * len(seed_tokens & section_tokens))
                    score += min(4.0, 0.35 * len(question_tokens & section_tokens))

                if score > 0:
                    section_candidates.append((score, section_index, section, section_text))

            section_candidates.sort(key=lambda row: row[0], reverse=True)
            for section_score, section_index, section, section_text in section_candidates[:2]:
                sentences = [
                    _clean(sentence)
                    for sentence in re.split(r"(?<=[.!?])\s+|\n+", section_text)
                    if len(_clean(sentence)) >= 18
                ]
                if not sentences:
                    continue

                normalized_seed = _normalise(seed_text)
                best_index = 0
                best_score = -1.0
                for sentence_index, sentence in enumerate(sentences):
                    normalized_sentence = _normalise(sentence)
                    sentence_tokens = self._local_sentence_tokens(sentence)
                    score = 0.0
                    if normalized_sentence and normalized_sentence in normalized_seed:
                        score += 20.0
                    if normalized_seed and normalized_seed in normalized_sentence:
                        score += 20.0
                    score += 1.2 * len(seed_tokens & sentence_tokens)
                    score += 0.45 * len(question_tokens & sentence_tokens)
                    if score > best_score:
                        best_score = score
                        best_index = sentence_index

                # Deux fenêtres complémentaires autour du seed :
                # - backward : utile si la valeur précédente manque ;
                # - forward  : utile si les détails/conclusions suivent le seed.
                spans = (
                    (max(0, best_index - 5), min(len(sentences), best_index + 3), "backward"),
                    (max(0, best_index - 1), min(len(sentences), best_index + 12), "forward"),
                )
                for start, end, direction in spans:
                    text = _clean(" ".join(sentences[start:end]))
                    if len(text) < 80:
                        continue
                    if (
                        _near_duplicate_text(text, seed_text)
                        and not self._materially_extends_local_context(text, seed_text)
                    ):
                        continue

                    signature = _hash(
                        document_name,
                        section_index,
                        start,
                        end,
                        direction,
                        size=22,
                    )
                    results.append(
                        {
                            "id": f"local_neighbor_{signature}",
                            "text": text,
                            "distance": max(0.0, float(seed.get("distance") or 0.0)),
                            "metadata": {
                                **base_meta,
                                "document": Path(document_name).name,
                                "section_title": _clean(section.get("section_title")),
                                "paragraph_index": _safe_int(section.get("paragraph_index")),
                                "role": "document_brut",
                                "final_role": "document_brut",
                                "document_type": "client_raw_document",
                                "source_policy": "client_current_project_only",
                                "source_kind": "client_raw",
                                "chunk_level": "local_neighbor_window",
                                "local_neighbor_expansion": True,
                                "local_neighbor_direction": direction,
                                "local_neighbor_seed_id": _clean(seed.get("id")),
                                "local_neighbor_section_index": section_index,
                                "local_neighbor_sentence_start": start,
                                "local_neighbor_sentence_end": end,
                                "json_path": f"fulltext.sections[{section_index}]",
                            },
                        }
                    )

                if len(results) >= max(2, limit * 2):
                    break
            break

        # Déduplication locale avant reranking. Une fenêtre forward plus
        # longue est prioritaire car elle prolonge typiquement un résultat chiffré
        # avec les détails/conclusions immédiatement suivants.
        results.sort(
            key=lambda item: (
                _clean(_source_metadata(item).get("local_neighbor_direction")) == "forward",
                len(_clean(item.get("text"))),
            ),
            reverse=True,
        )
        output: List[Dict[str, Any]] = []
        for item in results:
            if any(_near_duplicate_text(item.get("text"), old.get("text")) for old in output):
                continue
            output.append(item)
            if len(output) >= limit:
                break
        return output


    def _best_material_local_extension(
        self,
        *,
        seed: Mapping[str, Any],
        neighbors: Sequence[Mapping[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Choisit une vraie extension du seed AVANT tout reranking global.

        Le bug V7.5.2 venait du fait que les voisins étaient d'abord rerankés en
        ``top_k=1``. Une fenêtre backward plus courte pouvait alors gagner et la
        fenêtre forward contenant la suite utile était perdue. Ici on ne demande
        pas au reranker de choisir entre deux variantes du même passage : on
        privilégie directement la variante qui conserve le seed et ajoute le plus
        de contexte documentaire, dans le même document et la même section.

        Cette logique est purement structurelle : aucun terme métier, projet,
        classe, simulateur ou valeur numérique n'est codé en dur.
        """
        seed_text = _clean(seed.get("text"))
        if not seed_text:
            return None

        seed_document = _document_key(_source_document(seed))
        seed_meta = _source_metadata(seed)
        seed_section = self._local_section_key(seed_meta.get("section_title"))
        seed_tokens = self._local_sentence_tokens(seed_text)

        ranked: List[Tuple[float, int, Dict[str, Any]]] = []
        for raw_neighbor in neighbors:
            neighbor = dict(raw_neighbor)
            text = _clean(neighbor.get("text"))
            if not text:
                continue
            if not self._materially_extends_local_context(text, seed_text):
                continue
            if _document_key(_source_document(neighbor)) != seed_document:
                continue

            meta = _source_metadata(neighbor)
            neighbor_section = self._local_section_key(meta.get("section_title"))
            same_section = bool(
                seed_section
                and neighbor_section
                and (
                    seed_section == neighbor_section
                    or seed_section in neighbor_section
                    or neighbor_section in seed_section
                )
            )
            direction = _clean(meta.get("local_neighbor_direction")).lower()
            forward = direction == "forward"

            neighbor_tokens = self._local_sentence_tokens(text)
            shared_ratio = (
                len(seed_tokens & neighbor_tokens) / max(1, len(seed_tokens))
                if seed_tokens
                else 0.0
            )
            novel_tokens = neighbor_tokens - seed_tokens
            added_chars = max(0, len(text) - len(seed_text))

            # Priorités structurelles : même section > forward > couverture du
            # seed > quantité réelle de nouveau contexte > longueur ajoutée.
            score = 0.0
            score += 100.0 if same_section else 0.0
            score += 30.0 if forward else 0.0
            score += 20.0 * min(1.0, shared_ratio)
            score += min(20.0, 0.75 * len(novel_tokens))
            score += min(15.0, added_chars / 120.0)

            ranked.append((score, len(text), neighbor))

        if not ranked:
            return None

        ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
        return dict(ranked[0][2])

    @staticmethod
    def _experimental_stage_preference(question: str) -> str:
        """Détecte un qualificatif ordinal/temporel explicite dans la question.

        Cette logique est générique : elle ne connaît aucun projet, simulateur,
        classe ou valeur métier. Elle sert uniquement à distinguer plusieurs
        occurrences expérimentales d'une même section de résultats.
        """
        q = _normalise(question)

        earliest = (
            "premiere", "premier", "1ere", "1er", "initiale", "initial",
            "au debut", "dans un premier temps", "premiere evaluation",
            "premiere experience", "premier test", "premiere phase",
        )
        second = (
            "deuxieme", "2eme", "2e", "seconde", "second",
            "deuxieme evaluation", "deuxieme experience", "deuxieme phase",
        )
        latest = (
            "derniere", "dernier", "finale", "final", "ultime",
            "derniere evaluation", "derniere experience", "phase finale",
        )

        if any(marker in q for marker in earliest):
            return "earliest"
        if any(marker in q for marker in second):
            return "second"
        if any(marker in q for marker in latest):
            return "latest"
        return ""

    @classmethod
    def _experimental_result_like_source(cls, source: Mapping[str, Any]) -> bool:
        """True pour une preuve représentant un résultat/une évaluation mesurée."""
        meta = _source_metadata(source)
        section = _normalise(meta.get("section_title"))
        nature = _normalise(source.get("_evidence_nature") or meta.get("evidence_nature"))
        role = _normalise(meta.get("role") or meta.get("final_role"))
        text = _normalise(source.get("text"))

        if "result" in section or "evaluation" in section or "mesure" in section:
            return True
        if "resultat" in nature or "mesure" in nature or role == "resultat":
            return True
        # Filet générique pour les passages chiffrés issus d'une évaluation.
        evaluation_markers = (
            "precision", "performance", "matrice de confusion", "score",
            "taux", "pourcent", "%", "evaluation",
        )
        return sum(1 for marker in evaluation_markers if marker in text) >= 2


    def _experimental_fulltext_source(
        self,
        *,
        question: str,
        document_scope: Optional[Mapping[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Retrouve l'occurrence expérimentale demandée dans le FullText complet.

        Cette méthode inspecte directement le document sélectionné, mais ne
        considère comme résultats que :
        - les sections dont le titre indique explicitement un résultat,
          une évaluation ou une mesure ;
        - ou les passages sans titre contenant à la fois un marqueur
          expérimental fort et une valeur mesurée.

        Elle ne force jamais artificiellement le rôle ``resultat`` sur toutes
        les sections. Les objectifs, motivations et protocoles ne peuvent donc
        plus être choisis comme « première évaluation ».
        """
        stage = self._experimental_stage_preference(question)

        if not stage or not document_scope:
            return None

        scope_name = _document_key(document_scope.get("document_name"))
        if not scope_name or not self.fulltext_corpus_dir.exists():
            return None

        candidates: List[Dict[str, Any]] = []

        for path in sorted(self.fulltext_corpus_dir.glob("*.json")):
            if not path.is_file() or path.name == "manifest.json":
                continue

            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue

            if not isinstance(payload, Mapping):
                continue

            document_name = _document_name(payload, path.stem)
            if _document_key(document_name) != scope_name:
                continue

            sections = payload.get("sections") or []
            if not isinstance(sections, list):
                continue

            for section_index, section in enumerate(sections):
                if not isinstance(section, Mapping):
                    continue

                text = _clean(section.get("text"))
                title = _clean(section.get("section_title"))
                paragraph_index = _safe_int(section.get("paragraph_index"))

                if len(text) < 80 or paragraph_index < 0:
                    continue

                normalized_title = _normalise(title)
                normalized_text = _normalise(text)

                title_is_result = any(
                    marker in normalized_title
                    for marker in (
                        "resultat",
                        "resultats",
                        "evaluation",
                        "evaluations",
                        "mesure",
                        "mesures",
                    )
                )

                has_measured_value = bool(
                    re.search(
                        r"\b\d+(?:[,.]\d+)?\s*%",
                        text,
                    )
                )

                strong_experimental_markers = (
                    "matrice de confusion",
                    "precision moyenne",
                    "resultats montrent",
                    "resultat montre",
                    "nous observons",
                    "nous constatons",
                    "atteint une precision",
                    "atteignent une precision",
                    "gain de precision",
                    "taux de",
                )

                has_strong_experimental_marker = any(
                    marker in normalized_text
                    for marker in strong_experimental_markers
                )

                if not title_is_result and not (
                    has_measured_value
                    and has_strong_experimental_marker
                ):
                    continue

                probe = {
                    "text": text,
                    "metadata": {
                        "section_title": title,
                        "paragraph_index": paragraph_index,
                        "role": "document_brut",
                        "final_role": "document_brut",
                    },
                }

                if not self._experimental_result_like_source(probe):
                    continue

                candidates.append(
                    {
                        "id": (
                            "experimental_fulltext_"
                            + _hash(
                                document_name,
                                section_index,
                                paragraph_index,
                                text[:900],
                                size=24,
                            )
                        ),
                        "text": text,
                        "distance": 0.0,
                        "metadata": {
                            "document": Path(document_name).name,
                            "document_id": _clean(payload.get("document_id")),
                            "source_path": _clean(payload.get("source_path")),
                            "section_title": title,
                            "paragraph_index": paragraph_index,
                            "char_start": _safe_int(section.get("char_start")),
                            "char_end": _safe_int(section.get("char_end")),
                            "role": "document_brut",
                            "final_role": "document_brut",
                            "document_type": "client_raw_document",
                            "source_policy": "client_current_project_only",
                            "source_kind": "client_raw",
                            "chunk_level": "experimental_fulltext_section",
                            "experimental_fulltext_anchor": True,
                            "json_path": f"fulltext.sections[{section_index}]",
                        },
                    }
                )

            break

        if not candidates:
            return None

        positions = sorted(
            {
                _safe_int(_source_metadata(item).get("paragraph_index"))
                for item in candidates
                if _safe_int(_source_metadata(item).get("paragraph_index")) >= 0
            }
        )

        if not positions:
            return None

        if stage == "earliest":
            target_position = positions[0]
        elif stage == "latest":
            target_position = positions[-1]
        else:
            target_position = positions[1] if len(positions) >= 2 else positions[0]

        target_candidates = [
            item
            for item in candidates
            if _safe_int(
                _source_metadata(item).get("paragraph_index")
            ) == target_position
        ]

        if not target_candidates:
            return None

        semantic_scores = _cosine_rank(
            question,
            [
                (
                    f"{_clean(_source_metadata(item).get('section_title'))} "
                    f"{_clean(item.get('text'))}"
                )
                for item in target_candidates
            ],
        )

        ranked = sorted(
            zip(target_candidates, semantic_scores),
            key=lambda row: (
                float(row[1]),
                len(_clean(row[0].get("text"))),
            ),
            reverse=True,
        )

        selected = dict(ranked[0][0])
        selected["_experimental_context_stage"] = stage
        selected["_experimental_context_target_paragraph"] = target_position
        selected["_experimental_context_enforced"] = True
        selected["_experimental_fulltext_anchor"] = True
        selected["_chat_score"] = max(
            100.0,
            float(selected.get("_chat_score") or 0.0),
        )

        return selected

    @classmethod
    def _prioritize_experimental_context(
        cls,
        question: str,
        sources: Sequence[Mapping[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Impose le stade exp?rimental explicitement demand?.

        Lorsqu'une question contient un qualificatif comme ? premi?re ?,
        ? deuxi?me ? ou ? derni?re ? ?valuation, un simple r?ordonnancement
        ne suffit pas : le LLM pourrait encore s?lectionner une autre
        exp?rience plus explicite lexicalement.

        Si une preuve FullText positionn?e correspond au stade demand? :
        - les r?sultats de cette occurrence sont conserv?s ;
        - les autres passages de r?sultats exp?rimentaux sont exclus ;
        - les passages non exp?rimentaux restent disponibles comme contexte.

        Sans qualificatif ordinal explicite, le comportement reste inchang?.
        """
        items = [
            dict(item)
            for item in sources
            if isinstance(item, Mapping)
        ]

        stage = cls._experimental_stage_preference(question)

        if not stage or len(items) <= 1:
            return items

        positioned_results: List[Tuple[int, Dict[str, Any]]] = []

        for item in items:
            if not cls._experimental_result_like_source(item):
                continue

            meta = _source_metadata(item)
            paragraph_index = _safe_int(meta.get("paragraph_index"))

            if paragraph_index >= 0:
                positioned_results.append(
                    (paragraph_index, item)
                )

        unique_positions = sorted({
            paragraph_index
            for paragraph_index, _ in positioned_results
        })

        if not unique_positions:
            return items

        if stage == "earliest":
            target = unique_positions[0]

        elif stage == "latest":
            target = unique_positions[-1]

        else:  # second
            target = (
                unique_positions[1]
                if len(unique_positions) >= 2
                else unique_positions[0]
            )

        target_results: List[Dict[str, Any]] = []
        contextual_sources: List[Dict[str, Any]] = []
        dropped_positions: List[int] = []

        for item in items:
            result_like = cls._experimental_result_like_source(item)
            meta = _source_metadata(item)
            paragraph_index = _safe_int(meta.get("paragraph_index"))

            if result_like:
                if paragraph_index == target:
                    target_results.append(item)
                else:
                    dropped_positions.append(paragraph_index)

                # En pr?sence d'un stade explicite, aucun r?sultat provenant
                # d'une autre occurrence exp?rimentale ne reste dans le
                # catalogue factuel.
                continue

            contextual_sources.append(item)

        # Filet de s?curit? : ne rien supprimer si aucune vraie preuve cible
        # n'a finalement ?t? trouv?e.
        if not target_results:
            return items

        # Pr?f?rer la fen?tre documentaire compl?te lorsqu'il existe plusieurs
        # repr?sentations du m?me paragraphe cible.
        target_results.sort(
            key=lambda item: (
                bool(item.get("_local_neighbor_forced_context")),
                bool(item.get("_local_neighbor_replaces_short_seed")),
                len(_clean(item.get("text"))),
                float(item.get("_chat_score") or 0.0),
            ),
            reverse=True,
        )

        ordered = [
            *target_results,
            *contextual_sources,
        ]

        removed = sorted({
            position
            for position in dropped_positions
            if position >= 0 and position != target
        })

        for rank, item in enumerate(ordered):
            item["_experimental_context_stage"] = stage
            item["_experimental_context_target_paragraph"] = target
            item["_experimental_context_rank"] = rank
            item["_experimental_context_enforced"] = True
            item["_experimental_context_dropped_paragraphs"] = removed

        return ordered

    def _retrieve_with_local_neighbors(
        self,
        question: str,
        history: Sequence[Mapping[str, Any]],
        *,
        top_k: int,
        document_scope: Optional[Mapping[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Retrieval + remplacement déterministe du seed par son extension locale.

        V7.5.3 corrige uniquement l'intégrité de la fenêtre de preuve. Pour les
        trois meilleurs seeds du retriever, on construit les voisins FullText et,
        lorsqu'une vraie extension existe, on remplace immédiatement le seed
        court par la meilleure extension. Aucun reranking ``top_k=1`` n'est
        autorisé entre le seed et ses variantes locales : le reranking global
        intervient seulement après que la preuve documentaire a été rendue
        complète.
        """
        base = self._retrieve_project_sources(
            question,
            history,
            top_k=max(top_k, 6),
            document_scope=document_scope,
        )
        if not base:
            return []

        output: List[Dict[str, Any]] = []
        exact_seen: set[str] = set()
        replaced_seed_keys: set[str] = set()

        def item_key(item: Mapping[str, Any]) -> str:
            return _hash(
                _source_document(item),
                _clean(_source_metadata(item).get("section_title")),
                _normalise(_clean(item.get("text"))),
                size=30,
            )

        def add_exact(item: Mapping[str, Any]) -> bool:
            text = _clean(item.get("text"))
            if not text:
                return False
            signature = item_key(item)
            if signature in exact_seen:
                return False
            exact_seen.add(signature)
            output.append(dict(item))
            return True

        # Les seeds restent ceux du retriever existant. Seule leur représentation
        # documentaire est enrichie si une fenêtre locale matériellement plus
        # complète existe.
        for seed in base:
            if len(output) >= top_k:
                break

            neighbors = self._local_neighbor_candidates(
                seed=seed,
                question=question,
                document_scope=document_scope,
                limit=4,
            )
            extension = self._best_material_local_extension(
                seed=seed,
                neighbors=neighbors,
            )

            if extension is not None:
                extension["_local_neighbor_forced_context"] = True
                extension["_local_neighbor_replaces_short_seed"] = True
                extension["_local_neighbor_selected_before_rerank"] = True
                replaced_seed_keys.add(item_key(seed))
                add_exact(extension)
            else:
                add_exact(seed)

        # Compléter avec le ranking original, mais ne jamais réintroduire un seed
        # court qui a déjà été remplacé par son contexte complet.
        for item in base:
            if len(output) >= top_k:
                break
            if item_key(item) in replaced_seed_keys:
                continue
            add_exact(item)

        return self._prioritize_experimental_context(question, output[:top_k])

    def _retrieve_project_sources_with_coverage(
        self,
        question: str,
        history: Sequence[Mapping[str, Any]],
        top_k: int = 8,
        document_scope: Optional[Mapping[str, Any]] = None,
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        """Retrieval round-robin garantissant la représentation des sous-questions.

        Chaque sous-question utilise le retriever/reranker existant, donc aucune
        logique de scoring déjà validée n'est remplacée. V7.4 change seulement
        la *couverture* : au moins un bon candidat de chaque besoin est conservé
        avant de remplir les places restantes avec le ranking global.
        """
        subquestions = self._coverage_subquestions(question)
        if len(subquestions) <= 1:
            return (
                self._retrieve_with_local_neighbors(
                    question,
                    history,
                    top_k=top_k,
                    document_scope=document_scope,
                ),
                subquestions,
            )

        merged: Dict[str, Dict[str, Any]] = {}
        bucket_keys: List[List[str]] = []

        for unit_index, subquestion in enumerate(subquestions):
            # Les sous-recherches sont autonomes : pas d'ancienne question ajoutée.
            bucket = self._retrieve_with_local_neighbors(
                subquestion,
                (),
                top_k=7,
                document_scope=document_scope,
            )

            keys: List[str] = []

            for source in bucket:
                key = self._coverage_source_key(source)
                keys.append(key)
                score = float(source.get("_chat_score") or 0.0)

                if key not in merged:
                    merged[key] = dict(source)
                    merged[key]["_coverage_unit_indices"] = [unit_index]
                    merged[key]["_coverage_unit_questions"] = [subquestion]
                else:
                    existing = merged[key]
                    if score > float(existing.get("_chat_score") or 0.0):
                        preserved_indices = list(existing.get("_coverage_unit_indices") or [])
                        preserved_questions = list(existing.get("_coverage_unit_questions") or [])
                        merged[key] = dict(source)
                        merged[key]["_coverage_unit_indices"] = preserved_indices
                        merged[key]["_coverage_unit_questions"] = preserved_questions
                    if unit_index not in merged[key]["_coverage_unit_indices"]:
                        merged[key]["_coverage_unit_indices"].append(unit_index)
                    if subquestion not in merged[key]["_coverage_unit_questions"]:
                        merged[key]["_coverage_unit_questions"].append(subquestion)

            bucket_keys.append(list(dict.fromkeys(keys)))

        # Ranking global conservé comme complément, sans écraser les représentants.
        global_sources = self._retrieve_project_sources(
            question,
            history,
            top_k=max(top_k, 8),
            document_scope=document_scope,
        )
        global_keys: List[str] = []
        for source in global_sources:
            key = self._coverage_source_key(source)
            global_keys.append(key)
            if key not in merged:
                merged[key] = dict(source)
                merged[key]["_coverage_unit_indices"] = []
                merged[key]["_coverage_unit_questions"] = []

        selected_keys: List[str] = []
        selected_sources: List[Dict[str, Any]] = []

        def try_add(key: str) -> bool:
            if key in selected_keys or key not in merged:
                return False
            candidate = merged[key]
            candidate_text = _clean(candidate.get("text"))
            if not candidate_text:
                return False
            if any(
                _near_duplicate_text(candidate_text, _clean(existing.get("text")))
                for existing in selected_sources
            ):
                return False
            selected_keys.append(key)
            selected_sources.append(candidate)
            return True

        # Deux tours maximum : cela permet à une sous-question chiffrée de garder
        # deux passages complémentaires sans monopoliser tout le catalogue.
        for round_index in range(2):
            for keys in bucket_keys:
                if len(selected_sources) >= top_k:
                    break
                available = [key for key in keys if key not in selected_keys]
                if round_index >= len(available):
                    continue
                # Si le candidat visé est un doublon, on essaie les suivants.
                for key in available[round_index:]:
                    if try_add(key):
                        break
            if len(selected_sources) >= top_k:
                break

        # Complément par le ranking global existant.
        for key in global_keys:
            if len(selected_sources) >= top_k:
                break
            try_add(key)

        # Dernier filet : meilleurs scores restants.
        if len(selected_sources) < top_k:
            remaining = sorted(
                (item for key, item in merged.items() if key not in selected_keys),
                key=lambda item: float(item.get("_chat_score") or 0.0),
                reverse=True,
            )
            for item in remaining:
                if len(selected_sources) >= top_k:
                    break
                try_add(self._coverage_source_key(item))

        return selected_sources, subquestions

    def _retrieve_all_documents_with_coverage(
        self,
        *,
        question: str,
        history: Sequence[Mapping[str, Any]],
        per_document_limit: int = 4,
    ) -> Tuple[List[Dict[str, Any]], List[str], List[Dict[str, Any]]]:
        """Récupère une couverture équilibrée pour chaque document du projet.

        V7.6.5 :
        - la liste des documents vient de ``available_documents()`` ;
        - chaque document est interrogé avec une portée stricte indépendante ;
        - les sous-besoins (objectif, méthode, résultat, limite, etc.) utilisent
          le mécanisme de couverture déjà existant ;
        - au moins une place du catalogue est réservée à chaque document ayant
          une preuve exploitable ;
        - aucun nom de document ou connaissance métier n'est codé en dur.
        """
        documents = self.available_documents()
        subquestions = self._coverage_subquestions(question)

        if not documents:
            return [], subquestions, []

        per_document_limit = max(1, min(int(per_document_limit), 6))

        selected: List[Dict[str, Any]] = []
        report: List[Dict[str, Any]] = []

        for document_order, document in enumerate(documents):
            scope = {
                "document_id": _clean(document.get("document_id")),
                "document_name": _clean(document.get("document_name")),
                "mode": "exhaustive_multi_document",
            }

            bucket, bucket_subquestions = self._retrieve_project_sources_with_coverage(
                question,
                history,
                top_k=max(per_document_limit, 6),
                document_scope=scope,
            )

            # Sélection locale équilibrée : le retrieval de couverture place déjà
            # les représentants des sous-questions en tête. On garde donc les
            # premiers passages non redondants du document.
            local_selected: List[Dict[str, Any]] = []

            for source in bucket:
                candidate = dict(source)
                candidate_text = _clean(candidate.get("text"))
                if not candidate_text:
                    continue
                if any(
                    _near_duplicate_text(
                        candidate_text,
                        existing.get("text"),
                    )
                    for existing in local_selected
                ):
                    continue

                candidate["_multi_document_order"] = document_order
                candidate["_multi_document_name"] = _clean(
                    document.get("document_name")
                )
                candidate["_multi_document_id"] = _clean(
                    document.get("document_id")
                )
                candidate["_multi_document_scope"] = True

                local_selected.append(candidate)

                if len(local_selected) >= per_document_limit:
                    break

            selected.extend(local_selected)

            report.append(
                {
                    "document_order": document_order,
                    "document_id": _clean(document.get("document_id")),
                    "document_name": _clean(document.get("document_name")),
                    "source_count": len(local_selected),
                    "candidate_found": bool(local_selected),
                    "subquestions": list(bucket_subquestions or subquestions),
                }
            )

        return selected, subquestions, report

    @staticmethod
    def _multi_document_coverage_plan(
        document_report: Sequence[Mapping[str, Any]],
        evidence: Sequence[Mapping[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Construit le plan document -> preuves réellement présentes."""
        plan: List[Dict[str, Any]] = []

        for document in document_report:
            document_name = _clean(document.get("document_name"))
            document_key = _document_key(document_name)

            evidence_ids = [
                _clean(item.get("evidence_id"))
                for item in evidence
                if _clean(item.get("evidence_id"))
                and _document_key(item.get("document")) == document_key
            ]

            plan.append(
                {
                    "document_order": _safe_int(
                        document.get("document_order"),
                        default=len(plan),
                    ),
                    "document_id": _clean(document.get("document_id")),
                    "document_name": document_name,
                    "candidate_evidence_ids": list(dict.fromkeys(evidence_ids)),
                    "candidate_found": bool(evidence_ids),
                    "subquestions": list(document.get("subquestions") or []),
                }
            )

        return plan

    @staticmethod
    def _document_coverage_from_sources(
        sources: Sequence[Mapping[str, Any]],
    ) -> set[str]:
        return {
            _document_key(item.get("document"))
            for item in sources
            if _document_key(item.get("document"))
        }

    @staticmethod
    def _coverage_plan(
        subquestions: Sequence[str],
        evidence: Sequence[Mapping[str, Any]],
    ) -> List[Dict[str, Any]]:
        if len(subquestions) <= 1:
            return []

        plan: List[Dict[str, Any]] = []
        for index, question in enumerate(subquestions):
            evidence_ids = [
                _clean(item.get("evidence_id"))
                for item in evidence
                if index in (item.get("coverage_unit_indices") or [])
                and _clean(item.get("evidence_id"))
            ]
            plan.append(
                {
                    "unit": index + 1,
                    "question": question,
                    "candidate_evidence_ids": list(dict.fromkeys(evidence_ids)),
                    "candidate_found": bool(evidence_ids),
                }
            )
        return plan

    def _retrieve_project_sources(
        self,
        question: str,
        history: Sequence[Mapping[str, Any]],
        top_k: int = 8,
        document_scope: Optional[Mapping[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        base_query = self._history_query(question, history)
        queries = self._expanded_queries(base_query)
        role_plan = self._role_plan_for_question(question)
        strict = bool(document_scope)
        fetch_k = 60 if strict else 28

        candidates: List[Dict[str, Any]] = []

        # Recherche originale + expansion déterministe liée à l'intention.
        # Le filtre document est appliqué après sur-échantillonnage pour rester
        # compatible avec le VectorStore existant.
        for query in queries:
            candidates.extend(
                self.vector_store.search(
                    collection_name=self.base_collection_name,
                    query=query,
                    top_k=fetch_k,
                    role_filter=role_plan,
                    document_type_exclude=[],
                    oversample=10,
                )
            )
            candidates.extend(
                self.vector_store.search(
                    collection_name=self.base_collection_name,
                    query=query,
                    top_k=fetch_k,
                    role_filter=None,
                    document_type_exclude=[],
                    oversample=10,
                )
            )

            if self._collection_count(self.raw_collection_name) > 0:
                candidates.extend(
                    self.vector_store.search(
                        collection_name=self.raw_collection_name,
                        query=query,
                        top_k=fetch_k,
                        role_filter=None,
                        oversample=10,
                    )
                )

        if self._question_intent(question) == "protocol_contrast":
            candidates.extend(
                self._protocol_contrast_anchor_candidates(
                    question=question,
                    document_scope=document_scope,
                    limit=24,
                )
            )

        return self._rank_and_dedupe_sources(
            question=question,
            candidates=candidates,
            document_scope=document_scope,
            top_k=top_k,
        )

    def _retrieve_diagnostic_sources(
        self,
        question: str,
        diagnostic_payload: Mapping[str, Any],
        top_k: int = 1,
    ) -> List[Dict[str, Any]]:
        sections = list(_iter_diagnostic_sections(diagnostic_payload))
        if not sections:
            return []

        titles = [title for title, _ in sections]
        bodies = [body for _, body in sections]
        scores = _cosine_rank(
            question,
            [f"{title}\n{body}" for title, body in sections],
        )

        ranked = sorted(
            zip(sections, scores),
            key=lambda item: item[1],
            reverse=True,
        )

        output: List[Dict[str, Any]] = []
        for index, ((title, body), score) in enumerate(ranked[:top_k], start=1):
            output.append(
                {
                    "id": f"diagnostic_{_hash(title, body, size=18)}",
                    "text": body,
                    "distance": max(0.0, 1.0 - float(score)),
                    "metadata": {
                        "project_id": self.store.project_id,
                        "year": self.store.year,
                        "annee": self.store.year,
                        "document": "Sortie EnnoDiagnostic",
                        "section_title": title,
                        "role": "diagnostic",
                        "final_role": "diagnostic",
                        "document_type": "diagnostic_output",
                        "source_kind": "diagnostic_output",
                        "source_policy": "secondary_analysis_not_raw_proof",
                        "chunk_level": "diagnostic_section",
                        "rag_chunk_id": f"diagnostic_{index}_{_hash(title, size=10)}",
                    },
                }
            )
        return output

    # ------------------------------------------------------------------
    # Prompt et réponse
    # ------------------------------------------------------------------

    @staticmethod
    def _sentence_aware_excerpt(value: Any, max_chars: int) -> str:
        text = _clean(value)
        if not text or max_chars <= 0:
            return ""
        if len(text) <= max_chars:
            return text

        sentences = [
            _clean(part)
            for part in re.split(r"(?<=[.!?])\s+|\n+", text)
            if _clean(part)
        ]
        if not sentences:
            clipped = text[:max_chars].rstrip()
            return clipped.rsplit(" ", 1)[0] if " " in clipped else clipped

        kept: List[str] = []
        size = 0
        for sentence in sentences:
            extra = len(sentence) + (1 if kept else 0)
            if kept and size + extra > max_chars:
                break
            if not kept and len(sentence) > max_chars:
                clipped = sentence[:max_chars].rstrip()
                return clipped.rsplit(" ", 1)[0] if " " in clipped else clipped
            kept.append(sentence)
            size += extra
        return " ".join(kept).strip()

    @staticmethod
    def _evidence_catalog(
        sources: Sequence[Mapping[str, Any]],
        max_sources: int = 8,
        max_chars: int = 2200,
    ) -> List[Dict[str, Any]]:
        evidence: List[Dict[str, Any]] = []

        for source in sources[:max_sources]:
            meta = _source_metadata(source)
            text = _clean(source.get("text"))
            if not text:
                continue

            page_number = _safe_int(meta.get("page_number"))
            paragraph_index = _safe_int(meta.get("paragraph_index"))
            char_start = _safe_int(meta.get("char_start"))
            char_end = _safe_int(meta.get("char_end"))

            evidence.append(
                {
                    "evidence_id": f"E{len(evidence) + 1}",
                    "rag_chunk_id": _clean(meta.get("rag_chunk_id") or source.get("id")),
                    "passage_id": _clean(
                        meta.get("passage_id")
                        or meta.get("original_passage_id")
                    ),
                    "document_id": _clean(meta.get("document_id")),
                    "source_path": _clean(meta.get("source_path")),
                    "source_kind": _clean(meta.get("source_kind") or "nlp_rag"),
                    "source_policy": _clean(meta.get("source_policy")),
                    "role": _clean(meta.get("role")),
                    "final_role": _clean(meta.get("final_role")),
                    "evidence_nature": _evidence_nature(source),
                    "support_score": round(float(source.get("_chat_score") or 0.0), 4),
                    "document": Path(
                        _clean(meta.get("document")) or "Document client"
                    ).name,
                    "filename": Path(
                        _clean(meta.get("document")) or "Document client"
                    ).name,
                    "section_title": _clean(meta.get("section_title")),
                    "year": _clean(meta.get("year") or meta.get("annee")),
                    "page_number": page_number if page_number >= 0 else None,
                    "paragraph_index": (
                        paragraph_index if paragraph_index >= 0 else None
                    ),
                    "char_start": char_start if char_start >= 0 else None,
                    "char_end": char_end if char_end >= 0 else None,
                    "coverage_unit_indices": list(source.get("_coverage_unit_indices") or []),
                    "coverage_unit_questions": list(source.get("_coverage_unit_questions") or []),
                    "coverage_document_name": _clean(source.get("_multi_document_name")),
                    "coverage_document_id": _clean(source.get("_multi_document_id")),
                    "coverage_document_order": (
                        _safe_int(source.get("_multi_document_order"))
                        if source.get("_multi_document_order") is not None
                        else None
                    ),
                    "excerpt": DiagnosticRAGChatService._sentence_aware_excerpt(
                        text, max_chars
                    ),
                }
            )

        return evidence

    @staticmethod
    def _history_block(history: Sequence[Mapping[str, Any]]) -> str:
        """Contexte conversationnel non probant.

        V7.3 : seuls les messages du consultant sont conservés pour résoudre
        une référence conversationnelle ("ce document", "et pour Salsa ?", etc.).
        Les anciennes réponses de l'assistant ne sont jamais réinjectées dans
        le prompt factuel et ne peuvent donc pas devenir une preuve implicite.
        """
        lines: List[str] = []
        for item in history[-10:]:
            role = _clean(item.get("role")).lower()
            content = _clean(item.get("content"))
            if role != "user" or not content:
                continue
            lines.append(f"Consultant : {content[:900]}")
        return "\n".join(lines[-6:]) or "Aucun contexte utilisateur précédent."

    @staticmethod
    def _build_prompt(
        *,
        question: str,
        history: Sequence[Mapping[str, Any]],
        evidence: Sequence[Mapping[str, Any]],
        document_scope: Optional[Mapping[str, Any]],
        coverage_plan: Optional[Sequence[Mapping[str, Any]]] = None,
        multi_document_plan: Optional[Sequence[Mapping[str, Any]]] = None,
    ) -> str:
        scope_label = (
            _clean(document_scope.get("document_name"))
            if document_scope
            else "Tous les documents du projet sélectionné"
        )
        strict_rule = (
            "La réponse doit porter EXCLUSIVEMENT sur ce document. "
            "N'utilise aucun autre document, même s'il contient un passage similaire."
            if document_scope
            else "La réponse peut croiser les documents du projet uniquement lorsque cela est utile."
        )
        question_intent = DiagnosticRAGChatService._question_intent(question)
        coverage_block = (
            json.dumps(list(coverage_plan or []), ensure_ascii=False, indent=2)
            if coverage_plan
            else "Question simple : aucun plan multi-besoins nécessaire."
        )
        multi_document_block = (
            json.dumps(list(multi_document_plan or []), ensure_ascii=False, indent=2)
            if multi_document_plan
            else "Aucune couverture exhaustive multi-document active."
        )
        return f"""
Tu es l'assistant documentaire flottant d'EnnoDiagnostic.

PORTÉE DOCUMENTAIRE ACTIVE
{scope_label}
{strict_rule}

TYPE DE QUESTION DÉTECTÉ
{question_intent}

OBJECTIF
Répondre exactement à la question du consultant à partir des passages récupérés.
Ne transforme pas la réponse en résumé global du dossier.

RÈGLES ABSOLUES DE GROUNDING
1. Utilise uniquement le catalogue de preuves ci-dessous.
2. `client_raw` et `nlp_rag` sont les seules preuves documentaires directes.
3. `diagnostic_output` est une analyse secondaire. Ne l'utilise jamais à la place
   d'un passage documentaire disponible.
4. Chaque source citée doit contenir l'idée exacte de la phrase qu'elle soutient.
5. En portée stricte, aucune source d'un autre document n'est autorisée.
6. Ne transforme pas une hypothèse en résultat : « pourrait », « susceptible »,
   « envisagé » et « à confirmer » doivent conserver ce niveau d'incertitude.
7. N'affirme une amélioration, une supériorité ou une performance que si une
   preuve `resultat_mesure` l'établit explicitement.
8. Pour une différence technique, cite d'abord les passages décrivant les principes,
   méthodes ou limites. N'utilise les résultats qu'en phrase séparée, si la question
   demande aussi l'effet expérimental.
9. Pour une question causale (« pourquoi »), donne uniquement les causes nécessaires.
   N'ajoute pas une comparaison avec un simulateur si elle n'est pas indispensable.
10. Pour une question d'objectif, ne développe pas les résultats sauf demande explicite.
11. Si le document ne soutient pas un point, écris :
    « Ce point n'est pas établi dans les passages retrouvés. »
12. Ignore sommaires, listes de documents et bibliographies sauf demande explicite.
13. Cite immédiatement les affirmations avec [E1] ou [E1, E2].
14. Cite toutes les preuves réellement nécessaires pour soutenir la réponse, sans limite numérique fixe.
    En mode multi-document exhaustif, cite au moins une preuve réelle de CHAQUE document
    et ajoute autant de citations supplémentaires que nécessaire pour soutenir les objectifs,
    méthodes, résultats et limites mentionnés. Évite seulement les citations inutiles ou répétitives.
15. N'invente aucun identifiant de preuve.
16. Ne termine PAS par une ligne « Références ». Les cartes de sources sont affichées
    séparément par l'interface ; conserve seulement les citations inline [E1], [E2].
17. Si le type est `protocol_contrast`, cherche obligatoirement un passage qui énonce
    explicitement à la fois des éléments identiques ET une différence/non-identité formulée
    lexicalement dans la preuve. Un simple « cependant », « mais » ou « presque à l'identique »
    ne suffit pas à établir l'exception. Ne remplace jamais l'exception demandée par une autre
    différence technique simplement voisine dans le document.
18. Dans ce cas, réponds sous la forme :
    « Éléments maintenus identiques : ... » puis « Élément non strictement identique : ... ».
    Reprends la formulation du document et n'infère aucune différence absente du passage.

FORMAT
- Première phrase : réponse directe.
- Puis une justification courte, sans répétition.
- N'ajoute JAMAIS automatiquement les rubriques « Objectif exact », « Comparaison technique »,
  « Résultat » ou « Verrou ». Utilise une rubrique seulement si la question la demande réellement.
- Pour `protocol_contrast`, utilise uniquement deux rubriques :
  « Éléments maintenus identiques » et « Élément non strictement identique ».
- Ne généralise pas une différence technique qui n'est pas explicitement écrite dans les preuves.

CONTEXTE CONVERSATIONNEL NON PROBANT
{DiagnosticRAGChatService._history_block(history)}
RÈGLE : ce bloc sert UNIQUEMENT à comprendre une référence conversationnelle.
Il ne constitue jamais une preuve et aucun fait de la réponse ne peut provenir de ce bloc.
Tout fait doit être explicitement présent dans le CATALOGUE DE PREUVES du tour courant.

QUESTION
{_clean(question)}

PLAN DE COUVERTURE MULTI-BESOINS
{coverage_block}

PLAN DE COUVERTURE EXHAUSTIVE MULTI-DOCUMENT
{multi_document_block}

RÈGLES DE COUVERTURE
19. Si le plan contient plusieurs unités, traite CHAQUE unité dans la réponse.
20. Utilise en priorité les `candidate_evidence_ids` associés à chaque unité, puis les autres preuves seulement en complément.
21. Une unité sans preuve candidate doit être signalée comme non établie ; n'invente jamais la réponse.
22. Ne remplace pas un chiffre, une classe, une limite ou une cause demandée par un résumé général si une preuve spécifique est disponible.
23. Pour une question demandant plusieurs résultats chiffrés, restitue tous les résultats explicitement présents dans les preuves retenues et pertinents pour les unités du plan.
24. Si plusieurs résultats intermédiaires et un résultat final/principal sont présents,
    ne remplace jamais le résultat final par un résultat intermédiaire. Distingue-les
    brièvement et donne en priorité la performance présentée comme centrale par le document.
25. Pour les limites, distingue strictement :
    - les limites du travail courant ;
    - les limites d'un dataset, d'un article cité ou de travaux antérieurs.
    N'attribue au travail courant qu'une limite explicitement formulée comme telle dans
    ses propres résultats, discussion, conclusion ou perspectives. Si seule une limite
    de la littérature est disponible, indique que la limite du travail courant n'est pas établie.
26. Pour chaque unité du plan, utilise d'abord les preuves associées à cette unité.
    Une preuve d'objectif ne doit pas remplacer une preuve de résultat, et une limite
    de l'état de l'art ne doit pas remplacer une limite du travail courant.
27. Si le PLAN DE COUVERTURE EXHAUSTIVE MULTI-DOCUMENT contient des documents,
    crée obligatoirement une partie distincte pour CHACUN d'eux, dans l'ordre du plan.
28. Pour chaque document dont `candidate_found=true`, cite au moins une preuve appartenant
    à CE document. Ne cite jamais une preuve d'un autre fichier pour compléter sa partie.
29. Si `candidate_found=false` pour un document, conserve quand même sa partie et écris
    explicitement que l'information n'est pas établie dans les passages retrouvés.
30. Une demande « pour chaque/chacun/tous les documents » interdit d'omettre silencieusement
    un fichier, même si plusieurs autres documents contiennent des informations similaires.
31. Dans ce mode, ne termine pas par une synthèse globale qui remplace les parties par document ;
    une courte conclusion est possible uniquement après avoir traité tous les fichiers.

CATALOGUE DE PREUVES
{json.dumps(list(evidence), ensure_ascii=False, indent=2)}
""".strip()

    @staticmethod
    def _extract_cited_ids(answer: str) -> List[str]:
        ids: List[str] = []
        for value in re.findall(r"\bE\d+\b", str(answer or ""), flags=re.I):
            normalized = value.upper()
            if normalized not in ids:
                ids.append(normalized)
        return ids

    @staticmethod
    def _strip_reference_line(answer: str) -> str:
        return re.sub(
            r"(?im)\n?\s*Références?\s*:\s*(?:E\d+[\s,;]*)+[.。]?\s*$",
            "",
            str(answer or ""),
        ).strip()

    @staticmethod
    def _claim_preferred_natures(claim: str) -> set[str]:
        normalized = _normalise(claim)
        if re.search(r"\b\d+(?:[,.]\d+)?\s*%", claim) or any(
            marker in normalized
            for marker in (
                "performance", "precision", "superieur", "meilleur",
                "resultat", "atteint", "gain",
            )
        ):
            return {"resultat_mesure"}
        if any(
            marker in normalized
            for marker in ("objectif", "le but", "vise a", "evaluer les capacites")
        ):
            return {"objectif", "preuve_documentaire"}
        if any(
            marker in normalized
            for marker in (
                "maintenu identique", "maintenus identiques", "strictement identique",
                "non identique", "pas identique", "mêmes paramètres", "memes parametres",
                "comparaison equitable", "comparaison équitable",
            )
        ):
            return {
                "methode_ou_parametre",
                "preuve_documentaire",
                "limite_ou_incertitude",
            }
        if any(
            marker in normalized
            for marker in (
                "difference technique", "approche", "methode", "principe",
                "fonctionnement", "lancer de rayons", "asymptotique",
                "effets radar", "phenomenes electromagnetiques",
            )
        ):
            return {
                "methode_ou_parametre",
                "limite_ou_incertitude",
                "preuve_documentaire",
                "objectif",
            }
        if any(
            marker in normalized
            for marker in ("limite", "incertitude", "faiblesse", "contrainte", "verrou")
        ):
            return {"limite_ou_incertitude", "preuve_documentaire"}
        return set()

    @staticmethod
    def _claim_context(answer: str, citation_start: int) -> str:
        prefix = str(answer or "")[:citation_start]
        boundaries = [
            prefix.rfind("."), prefix.rfind("!"), prefix.rfind("?"),
            prefix.rfind("\n"), prefix.rfind(":"),
        ]
        start = max(boundaries) + 1
        return _clean(prefix[start:])[-900:]

    @classmethod
    def _best_evidence_ids_for_claim(
        cls,
        claim: str,
        evidence: Sequence[Mapping[str, Any]],
        existing_ids: Sequence[str],
        max_ids: int = 2,
    ) -> List[str]:
        if not evidence:
            return []

        texts = [
            f"{_clean(item.get('section_title'))} {_clean(item.get('excerpt'))}"
            for item in evidence
        ]
        semantic = _cosine_rank(claim, texts)
        preferred = cls._claim_preferred_natures(claim)
        existing = {str(value).upper() for value in existing_ids}

        ranked: List[Tuple[float, str]] = []
        for item, score in zip(evidence, semantic):
            evidence_id = str(item.get("evidence_id") or "").upper()
            if not evidence_id:
                continue
            nature = _clean(item.get("evidence_nature"))
            adjusted = float(score)
            if preferred:
                adjusted += 0.32 if nature in preferred else -0.28
            if evidence_id in existing:
                adjusted += 0.07

            claim_tokens = {token for token in _normalise(claim).split() if len(token) >= 5}
            excerpt_normalized = _normalise(item.get("excerpt"))
            overlap = sum(1 for token in claim_tokens if token in excerpt_normalized)
            adjusted += min(0.35, overlap * 0.04)
            ranked.append((adjusted, evidence_id))

        ranked.sort(key=lambda row: row[0], reverse=True)
        if not ranked:
            return []

        selected = [ranked[0][1]]
        if (
            max_ids > 1
            and len(ranked) > 1
            and ranked[1][0] >= ranked[0][0] - 0.08
            and ranked[1][0] >= 0.15
        ):
            selected.append(ranked[1][1])
        return selected[:max_ids]

    @staticmethod
    def _collapse_adjacent_citations(answer: str) -> str:
        """Fusionne [E1][E1][E2] en [E1, E2] sans modifier le texte."""
        pattern = re.compile(
            r"(?:\s*\[(?:\s*E\d+\s*[,;]?\s*)+\]){2,}",
            flags=re.I,
        )

        def merge(match: re.Match[str]) -> str:
            ids: List[str] = []
            for value in re.findall(r"E\d+", match.group(0), flags=re.I):
                normalized = value.upper()
                if normalized not in ids:
                    ids.append(normalized)
            return " [" + ", ".join(ids) + "]" if ids else ""

        return pattern.sub(merge, str(answer or ""))

    @classmethod
    def _align_and_renumber_citations(
        cls,
        answer: str,
        evidence: Sequence[Mapping[str, Any]],
        max_sources: Optional[int] = None,
    ) -> Tuple[str, List[Dict[str, Any]], Dict[str, Any]]:
        """Préserve l'intégrité claim -> evidence sans substitution automatique.

        V7.3 :
        - garde uniquement les IDs réellement cités par le LLM et présents dans
          le catalogue du tour courant ;
        - ne remplace JAMAIS E1 par une preuve jugée "plus proche" ;
        - supprime les IDs inexistants au lieu de leur inventer un substitut ;
        - n'ajoute aucune citation si le LLM n'en a pas produit ;
        - renumérote seulement l'affichage final E1..En, sans changer la source.
        """
        cleaned = cls._strip_reference_line(answer)
        evidence_by_id = {
            str(item.get("evidence_id") or "").upper(): dict(item)
            for item in evidence
            if _clean(item.get("evidence_id"))
        }

        citation_pattern = re.compile(r"\[((?:\s*E\d+\s*[,;]?\s*)+)\]", re.I)

        valid_order: List[str] = []
        invalid_ids: List[str] = []

        # 1) Collecte stricte : uniquement les IDs réellement écrits par le LLM.
        for match in citation_pattern.finditer(cleaned):
            for value in re.findall(r"E\d+", match.group(1), flags=re.I):
                evidence_id = value.upper()
                if evidence_id in evidence_by_id:
                    if evidence_id not in valid_order:
                        valid_order.append(evidence_id)
                elif evidence_id not in invalid_ids:
                    invalid_ids.append(evidence_id)

        # V7.6.9 : aucune limite numérique fixe de citations.
        # Tous les IDs valides réellement cités par le LLM sont conservés.
        cited_order = (
            valid_order
            if max_sources is None
            else valid_order[:max(0, int(max_sources))]
        )
        allowed = set(cited_order)
        mapping = {
            old_id: f"E{index + 1}"
            for index, old_id in enumerate(cited_order)
        }

        # 2) Réécriture purement mécanique. Aucun scoring, aucune substitution.
        def rewrite_group(match: re.Match[str]) -> str:
            old_ids = [
                value.upper()
                for value in re.findall(r"E\d+", match.group(1), flags=re.I)
            ]
            new_ids: List[str] = []
            for old_id in old_ids:
                if old_id not in allowed:
                    continue
                new_id = mapping[old_id]
                if new_id not in new_ids:
                    new_ids.append(new_id)
            return "[" + ", ".join(new_ids) + "]" if new_ids else ""

        final_answer = citation_pattern.sub(rewrite_group, cleaned)
        final_answer = cls._collapse_adjacent_citations(final_answer)
        final_answer = re.sub(r"\s+([,.;:!?])", r"\1", final_answer)
        final_answer = re.sub(r"[ \t]{2,}", " ", final_answer).strip()

        selected_sources: List[Dict[str, Any]] = []
        for old_id in cited_order:
            item = dict(evidence_by_id[old_id])
            item["original_evidence_id"] = old_id
            item["evidence_id"] = mapping[old_id]
            selected_sources.append(item)

        return final_answer, selected_sources, {
            "mode": "preserve_valid_citations_only",
            "semantic_realignment_disabled": True,
            "selected_original_ids": cited_order,
            "dropped_invalid_ids": invalid_ids,
            "renumber_mapping": mapping,
            "sources_added_without_citation": 0,
        }

    @classmethod
    def _ensure_multi_document_citation_coverage(
        cls,
        *,
        answer: str,
        selected_sources: Sequence[Mapping[str, Any]],
        evidence: Sequence[Mapping[str, Any]],
        multi_document_plan: Sequence[Mapping[str, Any]],
    ) -> Tuple[str, List[Dict[str, Any]], Dict[str, Any]]:
        """Garantit une citation réelle pour chaque document couvert.

        V7.6.7 - garde purement documentaire :
        - ne réécrit aucun fait ;
        - ne crée aucune preuve ;
        - n'attache qu'une preuve déjà présente dans le catalogue du tour ;
        - intervient seulement si un document `candidate_found=true` n'est
          toujours pas cité après la génération/réparation LLM ;
        - ajoute la citation au titre/bloc du document lorsque celui-ci est
          détectable dans la réponse, sinon ajoute une ligne neutre
          "Source documentaire pour ...".
        """
        final_answer = str(answer or "").strip()
        final_sources: List[Dict[str, Any]] = [
            dict(item) for item in selected_sources
        ]
        expected_documents: List[Dict[str, Any]] = []
        for item in multi_document_plan or []:
            document_name = _clean(item.get("document_name"))
            document_key = _document_key(document_name)
            if not item.get("candidate_found") or not document_key:
                continue
            expected_documents.append(
                {
                    "document_name": document_name,
                    "document_key": document_key,
                    "candidate_evidence_ids": [
                        str(value).upper()
                        for value in (item.get("candidate_evidence_ids") or [])
                        if _clean(value)
                    ],
                }
            )

        evidence_by_id = {
            str(item.get("evidence_id") or "").upper(): dict(item)
            for item in evidence
            if _clean(item.get("evidence_id"))
        }

        evidence_by_document: Dict[str, List[Dict[str, Any]]] = {}
        for item in evidence:
            document_key = _document_key(item.get("document"))
            if not document_key:
                continue
            evidence_by_document.setdefault(document_key, []).append(dict(item))

        covered_keys = cls._document_coverage_from_sources(final_sources)
        added_documents: List[str] = []
        unresolved_documents: List[str] = []

        def attach_to_document_block(
            text: str,
            *,
            document_name: str,
            document_key: str,
            citation_id: str,
        ) -> str:
            lines = text.splitlines()

            # Priorité : ligne/titre qui nomme explicitement le document.
            for index, line in enumerate(lines):
                normalized_line = _normalise(line)
                if document_key and document_key in normalized_line:
                    if re.search(rf"\[{re.escape(citation_id)}\]", line, flags=re.I):
                        return text
                    lines[index] = line.rstrip() + f" [{citation_id}]"
                    return "\n".join(lines)

            # Fallback sûr : aucune affirmation factuelle ajoutée, uniquement
            # une référence documentaire explicite.
            suffix = f"Source documentaire pour {document_name}: [{citation_id}]"
            if text:
                return text.rstrip() + "\n\n" + suffix
            return suffix

        for expected in expected_documents:
            document_name = expected["document_name"]
            document_key = expected["document_key"]

            if document_key in covered_keys:
                continue

            # V7.6.8 : `max_sources` est une limite souple pour la génération,
            # jamais une raison d'abandonner un document obligatoire.
            # Si tous les emplacements ont été consommés par d'autres fichiers,
            # la garde ajoute quand même UNE preuve réelle pour le document
            # manquant. Le nombre d'ajouts reste borné par le nombre de documents.
            candidate: Optional[Dict[str, Any]] = None

            # Respecte d'abord l'ordre des preuves prévu dans le plan.
            for evidence_id in expected["candidate_evidence_ids"]:
                item = evidence_by_id.get(evidence_id)
                if (
                    item
                    and _document_key(item.get("document")) == document_key
                ):
                    candidate = dict(item)
                    break

            if candidate is None:
                bucket = evidence_by_document.get(document_key) or []
                if bucket:
                    candidate = dict(bucket[0])

            if candidate is None:
                unresolved_documents.append(document_name)
                continue

            original_id = str(candidate.get("evidence_id") or "").upper()
            new_id = f"E{len(final_sources) + 1}"

            candidate["original_evidence_id"] = (
                candidate.get("original_evidence_id")
                or original_id
            )
            candidate["evidence_id"] = new_id
            candidate["multidoc_citation_fallback"] = True

            final_sources.append(candidate)
            final_answer = attach_to_document_block(
                final_answer,
                document_name=document_name,
                document_key=document_key,
                citation_id=new_id,
            )

            covered_keys.add(document_key)
            added_documents.append(document_name)

        return final_answer, final_sources, {
            "mode": "deterministic_document_citation_completion",
            "added_documents": added_documents,
            "added_count": len(added_documents),
            "unresolved_documents": unresolved_documents,
            "complete": not unresolved_documents,
        }

    def answer(
        self,
        *,
        question: str,
        history: Sequence[Mapping[str, Any]],
        diagnostic_payload: Mapping[str, Any],
        document_scope: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        clean_question = _clean(question)
        if not clean_question:
            raise ValueError("La question est vide.")

        raw_index_report = self.ensure_raw_index()
        resolved_scope = self._resolve_document_scope(
            clean_question,
            document_scope,
        )

        exhaustive_multi_document = (
            resolved_scope is None
            and self._wants_exhaustive_multi_document_coverage(clean_question)
        )

        multi_document_report: List[Dict[str, Any]] = []

        if exhaustive_multi_document:
            (
                project_sources,
                coverage_subquestions,
                multi_document_report,
            ) = self._retrieve_all_documents_with_coverage(
                question=clean_question,
                history=history,
                per_document_limit=4,
            )

        elif self._question_intent(clean_question) == "protocol_contrast":
            project_sources = self._retrieve_project_sources(
                clean_question,
                history,
                top_k=8,
                document_scope=resolved_scope,
            )
            coverage_subquestions = [clean_question]

        else:
            project_sources, coverage_subquestions = self._retrieve_project_sources_with_coverage(
                clean_question,
                history,
                top_k=8,
                document_scope=resolved_scope,
            )

        diagnostic_sources: List[Dict[str, Any]] = []
        if (
            not exhaustive_multi_document
            and not resolved_scope
            and (
                self._wants_diagnostic_analysis(clean_question)
                or not project_sources
            )
        ):
            diagnostic_sources = self._retrieve_diagnostic_sources(
                clean_question,
                diagnostic_payload,
                top_k=1,
            )

        combined_sources = [*project_sources, *diagnostic_sources]

        # Garde expérimentale déjà validée pour les questions ordinales
        # ("première/deuxième/dernière évaluation").
        experimental_anchor = self._experimental_fulltext_source(
            question=clean_question,
            document_scope=resolved_scope,
        )

        if experimental_anchor is not None:
            target_paragraph = _safe_int(
                _source_metadata(experimental_anchor).get("paragraph_index")
            )

            filtered_sources: List[Dict[str, Any]] = []

            for source in combined_sources:
                if not self._experimental_result_like_source(source):
                    filtered_sources.append(dict(source))
                    continue

                paragraph_index = _safe_int(
                    _source_metadata(source).get("paragraph_index")
                )

                if paragraph_index == target_paragraph:
                    filtered_sources.append(dict(source))

            combined_sources = [
                experimental_anchor,
                *filtered_sources,
            ]

        combined_sources = self._prioritize_experimental_context(
            clean_question,
            combined_sources,
        )

        if self._question_intent(clean_question) == "protocol_contrast":
            explicit = [
                item for item in combined_sources
                if self._protocol_contrast_features(
                    _clean(item.get("text"))
                ).get("explicit_pair")
            ]
            if explicit:
                combined_sources = explicit[:2]

        if exhaustive_multi_document:
            document_count = max(1, len(multi_document_report))
            evidence_limit = min(20, max(8, document_count * 4))
            evidence_excerpt_chars = 1050

        else:
            evidence_limit = 8
            evidence_excerpt_chars = 2200

        evidence = self._evidence_catalog(
            combined_sources,
            max_sources=evidence_limit,
            max_chars=evidence_excerpt_chars,
        )

        coverage_plan = self._coverage_plan(
            coverage_subquestions,
            evidence,
        )

        multi_document_plan = (
            self._multi_document_coverage_plan(
                multi_document_report,
                evidence,
            )
            if exhaustive_multi_document
            else []
        )

        if not evidence:
            if resolved_scope:
                scope_name = (
                    _clean(resolved_scope.get("document_name"))
                    or "le document sélectionné"
                )
                answer = (
                    f"Je n'ai trouvé aucun passage suffisamment pertinent dans {scope_name}. "
                    "Vérifiez que ce document a bien été extrait pendant « Préparer les sources »."
                )
            else:
                answer = (
                    "Je ne trouve pas encore de passage exploitable dans le Chroma "
                    "du projet sélectionné. Relancez la préparation des sources, puis "
                    "EnnoDiagnostic."
                )

            return {
                "ok": True,
                "version": CHAT_SERVICE_VERSION,
                "answer": answer,
                "sources": [],
                "retrieval": {
                    "document_scope": resolved_scope,
                    "base_collection_name": self.base_collection_name,
                    "raw_collection_name": self.raw_collection_name,
                    "raw_index_report": raw_index_report,
                    "exhaustive_multi_document": exhaustive_multi_document,
                    "multi_document_plan": multi_document_plan,
                },
            }

        prompt = self._build_prompt(
            question=clean_question,
            history=history,
            evidence=evidence,
            document_scope=resolved_scope,
            coverage_plan=coverage_plan,
            multi_document_plan=multi_document_plan,
        )

        raw_answer = self.llm.generate(
            prompt,
            temperature=0.0,
            max_input_tokens=7800,
            max_output_tokens=1400 if exhaustive_multi_document else 900,
            retries=1,
            json_mode=False,
            request_name="ennodiagnostic:rag_chat_v7_6_9",
        )

        answer, selected_sources, citation_guard = self._align_and_renumber_citations(
            raw_answer,
            evidence,
            max_sources=None,
        )

        # V7.6.5 : si le LLM omet encore un document qui dispose pourtant d'une
        # preuve dans le catalogue, une seule régénération corrective est permise.
        multi_document_repair_used = False

        if exhaustive_multi_document and multi_document_plan:
            expected_keys = {
                _document_key(item.get("document_name"))
                for item in multi_document_plan
                if item.get("candidate_found")
                and _document_key(item.get("document_name"))
            }
            covered_keys = self._document_coverage_from_sources(selected_sources)
            missing_keys = expected_keys - covered_keys

            if missing_keys:
                missing_names = [
                    _clean(item.get("document_name"))
                    for item in multi_document_plan
                    if _document_key(item.get("document_name")) in missing_keys
                ]

                repair_prompt = (
                    prompt
                    + "\n\nCORRECTION OBLIGATOIRE DE COUVERTURE\n"
                    + "Le brouillon précédent n'a pas cité tous les documents disposant "
                      "de preuves. Produis une nouvelle réponse complète depuis zéro. "
                      "Traite tous les documents du plan et cite au moins une preuve "
                      "provenant réellement de chacun. Documents omis à corriger : "
                    + json.dumps(missing_names, ensure_ascii=False)
                )

                repaired_raw = self.llm.generate(
                    repair_prompt,
                    temperature=0.0,
                    max_input_tokens=7800,
                    max_output_tokens=1400,
                    retries=1,
                    json_mode=False,
                    request_name="ennodiagnostic:rag_chat_v7_6_9_multidoc_repair",
                )

                (
                    repaired_answer,
                    repaired_sources,
                    repaired_guard,
                ) = self._align_and_renumber_citations(
                    repaired_raw,
                    evidence,
                    max_sources=None,
                )

                repaired_coverage = self._document_coverage_from_sources(
                    repaired_sources
                )

                # V7.6.7 : une réparation n'est considérée comme réussie
                # que si TOUS les documents attendus sont réellement cités.
                if expected_keys.issubset(repaired_coverage):
                    answer = repaired_answer
                    selected_sources = repaired_sources
                    citation_guard = repaired_guard
                    multi_document_repair_used = True

        # V7.6.7 : garde finale exhaustive multi-document.
        # Si le LLM n'a toujours pas cité un document après la seule réparation
        # autorisée, on complète uniquement la couverture documentaire avec une
        # preuve réelle déjà présente dans le catalogue. Aucun fait n'est inventé.
        multi_document_citation_completion = {
            "mode": "inactive",
            "added_documents": [],
            "added_count": 0,
            "unresolved_documents": [],
            "complete": True,
        }

        if exhaustive_multi_document and multi_document_plan:
            (
                answer,
                selected_sources,
                multi_document_citation_completion,
            ) = self._ensure_multi_document_citation_coverage(
                answer=answer,
                selected_sources=selected_sources,
                evidence=evidence,
                multi_document_plan=multi_document_plan,
            )

        # Garde finale : en portée stricte aucune source d'un autre document et
        # aucune synthèse d'agent ne peut être renvoyée.
        if resolved_scope:
            scope_key = _document_key(resolved_scope.get("document_name"))
            selected_sources = [
                item
                for item in selected_sources
                if item.get("source_kind") != "diagnostic_output"
                and (
                    not scope_key
                    or _document_key(item.get("document")) == scope_key
                )
            ]

            strict_mapping = {
                str(item.get("evidence_id")): f"E{index + 1}"
                for index, item in enumerate(selected_sources)
            }

            if strict_mapping:
                def strict_rewrite(match: re.Match[str]) -> str:
                    ids = [
                        strict_mapping.get(value.upper())
                        for value in re.findall(
                            r"E\d+",
                            match.group(1),
                            flags=re.I,
                        )
                    ]
                    clean_ids = [value for value in ids if value]
                    return (
                        "[" + ", ".join(dict.fromkeys(clean_ids)) + "]"
                        if clean_ids
                        else ""
                    )

                pattern = re.compile(
                    r"\[((?:\s*E\d+\s*[,;]?\s*)+)\]",
                    re.I,
                )
                answer = pattern.sub(strict_rewrite, answer)
                answer = self._collapse_adjacent_citations(answer)

                for old_id, new_id in strict_mapping.items():
                    for item in selected_sources:
                        if str(item.get("evidence_id")) == old_id:
                            item["evidence_id"] = new_id

            elif selected_sources == []:
                answer = re.sub(
                    r"\[((?:\s*E\d+\s*[,;]?\s*)+)\]",
                    "",
                    answer,
                    flags=re.I,
                ).strip()

        generation_meta = self.llm.get_last_generation_meta()

        final_document_coverage = (
            self._document_coverage_from_sources(selected_sources)
            if exhaustive_multi_document
            else set()
        )

        expected_document_keys = {
            _document_key(item.get("document_name"))
            for item in multi_document_plan
            if item.get("candidate_found")
            and _document_key(item.get("document_name"))
        }

        missing_document_names = [
            _clean(item.get("document_name"))
            for item in multi_document_plan
            if item.get("candidate_found")
            and _document_key(item.get("document_name"))
            not in final_document_coverage
        ]

        return {
            "ok": True,
            "version": CHAT_SERVICE_VERSION,
            "answer": answer,
            "sources": selected_sources,
            "document_scope": resolved_scope,
            "model": generation_meta.get("model"),
            "provider": generation_meta.get("provider"),
            "usage": {
                "prompt_tokens": generation_meta.get("prompt_tokens"),
                "completion_tokens": generation_meta.get("completion_tokens"),
                "total_tokens": generation_meta.get("total_tokens"),
                "elapsed_seconds": generation_meta.get("elapsed_seconds"),
            },
            "retrieval": {
                "document_scope": resolved_scope,
                "base_collection_name": self.base_collection_name,
                "raw_collection_name": self.raw_collection_name,
                "project_sources_count": len(project_sources),
                "coverage_subquestions": coverage_subquestions,
                "coverage_plan": coverage_plan,
                "coverage_active": len(coverage_subquestions) > 1,
                "diagnostic_sources_count": len(diagnostic_sources),
                "evidence_count": len(evidence),
                "citation_guard": citation_guard,
                "raw_index_report": raw_index_report,
                "exhaustive_multi_document": exhaustive_multi_document,
                "multi_document_plan": multi_document_plan,
                "multi_document_expected_count": len(expected_document_keys),
                "multi_document_cited_count": len(
                    final_document_coverage & expected_document_keys
                ),
                "multi_document_missing_documents": missing_document_names,
                "multi_document_repair_used": multi_document_repair_used,
                "multi_document_citation_completion": multi_document_citation_completion,
                "citation_limit": None,
                "citation_limit_mode": "unlimited_valid_citations",
                "evidence_limit": evidence_limit,
            },
        }

