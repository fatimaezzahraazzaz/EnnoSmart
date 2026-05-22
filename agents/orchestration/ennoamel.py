"""
modules/orchestration/ennoamel.py — EnnoSmart / EnnoAmel POC
──────────────────────────────────────────────────────────────────────────────
Orchestrateur central EnnoAmel.

Rôle :
  - Relier Extraction → NLP → RAG.
  - Préparer un document brut ou un JSON NLP existant.
  - Utiliser modules/chat pour comprendre la conversation humaine avant le RAG.
  - Comprendre la demande utilisateur via intent_router.py.
  - Interroger le RAG avec l'intention détectée.
  - Répondre directement ou rediriger vers l'agent spécialisé :
      EnnoDiagnostic, EnnoScholar, EnnoValor.

Architecture :
  Document brut / JSON NLP
      → EnnoAmel.prepare_document()
      → extraction.router.extract()
      → NLP.router.process_extraction()
      → NLP.router.to_json()
      → RAGPipeline.ingest()
      → modules/chat.EnnoChat
      → EnnoAmel.ask()

Important :
  Ce fichier est un POC orchestrateur.
  Il ne remplace pas les agents spécialisés.
  Il donne une réponse préliminaire et indique l'agent cible si nécessaire.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from agents.orchestration.intent_router import (
    Intent,
    AgentName,
    IntentDecision,
    detect_intent,
)

from modules.chat import EnnoChat, EnnoChatConfig

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# CONFIG DEFAULT
# ══════════════════════════════════════════════════════════════════════════════

DEFAULT_EMBEDDING_MODEL = "BAAI/bge-m3"
DEFAULT_LLM_MODEL = "ollama:mistral:7b-instruct"
DEFAULT_CHAT_MODEL = "ollama:llama3.2:3b"

DEFAULT_RAG_INDEX_DIR = Path("modules/RAG/.chroma")
DEFAULT_RAG_CACHE_DIR = Path("modules/RAG/.cache")


# ══════════════════════════════════════════════════════════════════════════════
# DATACLASSES
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class PreparationReport:
    """
    Résultat de préparation documentaire :
      document brut → extraction → NLP → RAG
      OU
      JSON NLP → RAG
    """
    ok: bool
    file_path: Optional[str] = None
    file_name: Optional[str] = None
    mode: str = "unknown"
    indexed_chunks: int = 0

    already_indexed: bool = False
    file_hash: Optional[str] = None
    document_id: Optional[str] = None
    organisme_name: Optional[str] = None
    organisme_id: Optional[str] = None

    extraction_done: bool = False
    nlp_done: bool = False
    rag_done: bool = False

    extraction_errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    processing_time: float = 0.0

    document_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "file_path": self.file_path,
            "file_name": self.file_name,
            "mode": self.mode,
            "indexed_chunks": self.indexed_chunks,
            "already_indexed": self.already_indexed,
            "file_hash": self.file_hash,
            "document_id": self.document_id,
            "organisme_name": self.organisme_name,
            "organisme_id": self.organisme_id,
            "extraction_done": self.extraction_done,
            "nlp_done": self.nlp_done,
            "rag_done": self.rag_done,
            "extraction_errors": self.extraction_errors,
            "warnings": self.warnings,
            "processing_time": round(float(self.processing_time), 2),
            "document_metadata": self.document_metadata,
        }


@dataclass
class EnnoAmelResponse:
    """
    Réponse finale renvoyée par EnnoAmel.
    """
    answer: str
    intent: str
    recommended_agent: str
    action: str
    confidence: float

    sources: list[dict[str, Any]] = field(default_factory=list)
    needs_specialized_agent: bool = False
    rag_used: bool = False
    chunks_used: int = 0

    route_explanation: str = ""
    processing_time: float = 0.0
    error: Optional[str] = None

    debug: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "intent": self.intent,
            "recommended_agent": self.recommended_agent,
            "action": self.action,
            "confidence": round(float(self.confidence), 3),
            "sources": self.sources,
            "needs_specialized_agent": self.needs_specialized_agent,
            "rag_used": self.rag_used,
            "chunks_used": self.chunks_used,
            "route_explanation": self.route_explanation,
            "processing_time": round(float(self.processing_time), 2),
            "error": self.error,
            "debug": self.debug,
        }


@dataclass
class EnnoAmelState:
    """
    État courant de l'orchestrateur.
    """
    has_document: bool = False
    has_extraction: bool = False
    has_nlp: bool = False
    has_rag_index: bool = False

    current_file_path: Optional[str] = None
    current_file_name: Optional[str] = None

    current_organisme_name: Optional[str] = None
    current_organisme_id: Optional[str] = None
    current_file_hash: Optional[str] = None
    current_document_id: Optional[str] = None
    current_document_metadata: dict[str, Any] = field(default_factory=dict)

    extraction_result: Optional[Any] = None
    nlp_result: Optional[Any] = None
    nlp_json: Optional[dict[str, Any]] = None

    indexed_chunks: int = 0
    last_preparation: Optional[PreparationReport] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "has_document": self.has_document,
            "has_extraction": self.has_extraction,
            "has_nlp": self.has_nlp,
            "has_rag_index": self.has_rag_index,
            "current_file_path": self.current_file_path,
            "current_file_name": self.current_file_name,
            "current_organisme_name": self.current_organisme_name,
            "current_organisme_id": self.current_organisme_id,
            "current_file_hash": self.current_file_hash,
            "current_document_id": self.current_document_id,
            "current_document_metadata": self.current_document_metadata,
            "indexed_chunks": self.indexed_chunks,
            "last_preparation": (
                self.last_preparation.to_dict()
                if self.last_preparation
                else None
            ),
        }


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _load_json_file(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json_file(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _is_nlp_json_path(path: Path) -> bool:
    """
    Détecte un JSON NLP.
    On accepte :
      - *.nlp.json
      - *.json contenant document_metadata + chunks
    """
    name = path.name.lower()
    return name.endswith(".nlp.json") or name.endswith(".json")


def _slugify(value: str) -> str:
    """Identifiant stable pour organisme/document."""
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "unknown"


def _sha256_file(path: Path, block_size: int = 1024 * 1024) -> str:
    """Hash stable du contenu, utilisé pour éviter la réindexation."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(block_size), b""):
            h.update(block)
    return h.hexdigest()


def _resolve_document_identity(
    *,
    path: Path,
    organisme_name: Optional[str],
    organisme_id: Optional[str],
    file_hash: Optional[str],
    document_id: Optional[str],
) -> tuple[str, str, Optional[str], Optional[str]]:
    """
    Résout les clés métier du document.

    organisme_id + file_hash = clé anti-réindexation dans ChromaDB.
    document_id = clé lisible/stable stockée dans chaque chunk.
    """
    org_name = re.sub(r"\s+", " ", str(organisme_name or "Organisme inconnu")).strip()
    org_id = _slugify(organisme_id or org_name)

    fh = str(file_hash or "").strip() or None
    if fh is None and path.exists() and not _is_nlp_json_path(path):
        try:
            fh = _sha256_file(path)
        except Exception:
            fh = None

    doc_id = str(document_id or "").strip() or None
    if doc_id is None:
        if fh:
            doc_id = f"{org_id}_{fh[:16]}"
        else:
            doc_id = f"{org_id}_{_slugify(path.stem)}"

    return org_name, org_id, fh, doc_id


def _apply_document_identity_to_nlp_json(
    nlp_json: dict[str, Any],
    *,
    organisme_name: Optional[str],
    organisme_id: Optional[str],
    file_hash: Optional[str],
    document_id: Optional[str],
) -> dict[str, Any]:
    """
    Force l'identité documentaire dans document_metadata et chunks[*].metadata.
    Utile pour les anciens JSON NLP ou quand l'identité vient de l'interface.
    """
    if not isinstance(nlp_json, dict):
        return nlp_json

    doc_meta = dict(nlp_json.get("document_metadata", {}) or {})

    if organisme_name:
        doc_meta["organisme_name"] = organisme_name
    if organisme_id:
        doc_meta["organisme_id"] = organisme_id
    if file_hash:
        doc_meta["file_hash"] = file_hash
    if document_id:
        doc_meta["document_id"] = document_id

    nlp_json["document_metadata"] = doc_meta

    for chunk in nlp_json.get("chunks", []) or []:
        if not isinstance(chunk, dict):
            continue
        meta = dict(chunk.get("metadata", {}) or {})
        for key in ["organisme_name", "organisme_id", "file_hash", "document_id"]:
            if doc_meta.get(key):
                meta[key] = doc_meta[key]
        chunk["metadata"] = meta

    return nlp_json


def _restore_metadata_value(value: Any) -> Any:
    """Restaure les listes/dicts stockés comme JSON string dans Chroma metadata."""
    if not isinstance(value, str):
        return value

    text = value.strip()
    if not text:
        return value

    if (text.startswith("[") and text.endswith("]")) or (
        text.startswith("{") and text.endswith("}")
    ):
        try:
            return json.loads(text)
        except Exception:
            return value

    return value


def _metadata_to_list(value: Any) -> list[str]:
    """Convertit une metadata Chroma/NLP en liste de textes propres."""
    value = _restore_metadata_value(value)

    if value is None:
        return []

    if isinstance(value, str):
        cleaned = re.sub(r"\s+", " ", value).strip()
        return [cleaned] if cleaned else []

    if isinstance(value, (int, float, bool)):
        return [str(value)]

    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_metadata_to_list(item))
        return _dedupe_texts(out)

    if isinstance(value, dict):
        out: list[str] = []
        for item in value.values():
            out.extend(_metadata_to_list(item))
        return _dedupe_texts(out)

    return [str(value).strip()] if str(value).strip() else []


def _dedupe_texts(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []

    for item in items:
        text = re.sub(r"\s+", " ", str(item or "")).strip()
        key = unicodedata.normalize("NFKD", text.lower())
        key = "".join(ch for ch in key if not unicodedata.combining(ch))

        if not key or key in seen:
            continue

        seen.add(key)
        out.append(text)

    return out


def _build_chroma_where_for_document(
    *,
    organisme_id: Optional[str] = None,
    file_hash: Optional[str] = None,
    document_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Filtre Chroma valide : plusieurs conditions doivent passer par $and."""
    conditions: list[dict[str, Any]] = []

    if organisme_id:
        conditions.append({"organisme_id": str(organisme_id).strip()})

    if file_hash:
        conditions.append({"file_hash": str(file_hash).strip()})
    elif document_id:
        conditions.append({"document_id": str(document_id).strip()})

    conditions = [c for c in conditions if list(c.values())[0]]

    if not conditions:
        return None

    if len(conditions) == 1:
        return conditions[0]

    return {"$and": conditions}


STRUCTURED_METADATA_FIELDS = [
    "file_name",
    "organisme_name",
    "organisme_id",
    "file_hash",
    "document_id",
    "domaine_principal",
    "objet_recherche",
    "verrous_techniques",
    "objectifs_rd",
    "hypotheses_rd",
    "methodes_rd",
    "protocoles_experimentaux",
    "outils_technologies",
    "modeles_algorithmes",
    "architectures_systeme",
    "jeux_donnees_benchmarks",
    "metriques_evaluation",
    "parametres_variables",
    "normes_techniques",
    "materiaux_composants",
    "limitations_perspectives",
    "resultats_rd",
    "livrables",
]


def _short_agent_note(intent: str, agent: str) -> str:
    """
    Ajoute une note POC courte selon l'agent recommandé.
    """
    if intent in {"eligibility", "diagnostic"}:
        return (
            "\n\n---\n"
            "**Note POC :** **EnnoDiagnostic** est encore en cours de construction "
            "pour produire le score d’éligibilité CIR, les risques détaillés et la validation humaine."
        )

    if intent == "scholar":
        return (
            "\n\n---\n"
            "**Note POC :** **EnnoScholar** est encore en cours de construction "
            "pour rédiger un état de l’art complet avec recherche d’articles, bibliographie et citations."
        )

    if intent == "valor":
        return (
            "\n\n---\n"
            "**Note POC :** **EnnoValor** est encore en cours de construction "
            "pour la partie financière/RH, Excel, Cerfa et livrables administratifs."
        )

    return ""


def _help_message() -> str:
    return """
Je suis **EnnoAmel**, l'orchestrateur POC d'EnnoSmart.

Je peux :
- préparer un document avec Extraction → NLP → RAG ;
- donner une idée générale du projet ;
- répondre à des questions sur le dossier avec sources ;
- orienter vers EnnoDiagnostic pour le score d'éligibilité CIR ;
- rediriger vers EnnoDiagnostic, EnnoScholar ou EnnoValor selon le besoin.

Exemples de questions :
- Donne-moi une idée générale du projet.
- Quels sont les verrous techniques ?
- Quels outils, méthodes et résultats sont mentionnés ?
- Fais-moi une première analyse des risques.
""".strip()


# ══════════════════════════════════════════════════════════════════════════════
# ENNOAMEL ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════════════════

class EnnoAmelOrchestrator:
    """
    Orchestrateur principal EnnoAmel.

    Exemple usage :
      from modules.orchestration.ennoamel import EnnoAmelOrchestrator

      amel = EnnoAmelOrchestrator()
      report = amel.prepare_document("mon_doc.docx")
      response = amel.ask("Donne-moi une idée générale du projet")
      print(response.answer)
    """

    def __init__(
        self,
        rag_pipeline: Optional[Any] = None,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        llm_model: str = DEFAULT_LLM_MODEL,
        chat_model: str = DEFAULT_CHAT_MODEL,
        embedding_device: str = "cpu",
        index_dir: Optional[Path] = None,
        cache_dir: Optional[Path] = None,
        top_k: int = 5,
        min_score: float = -1.0,
        auto_load_rag: bool = True,
    ):
        self.state = EnnoAmelState()

        self.embedding_model = embedding_model
        self.llm_model = llm_model
        self.chat_model = chat_model
        self.embedding_device = embedding_device
        self.index_dir = Path(index_dir or DEFAULT_RAG_INDEX_DIR)
        self.cache_dir = Path(cache_dir or DEFAULT_RAG_CACHE_DIR)
        self.top_k = top_k
        self.min_score = min_score

        if rag_pipeline is not None:
            self.rag = rag_pipeline
        else:
            from modules.RAG.rag_pipeline import RAGPipeline

            self.rag = RAGPipeline(
                embedding_model=embedding_model,
                embedding_device=embedding_device,
                llm_model=llm_model,
                index_dir=self.index_dir,
                cache_dir=self.cache_dir,
                top_k=top_k,
                min_score=min_score,
                auto_load=auto_load_rag,
            )

        if getattr(self.rag, "total_chunks", 0) > 0:
            self.state.has_rag_index = True
            self.state.indexed_chunks = int(getattr(self.rag, "total_chunks", 0))

        # Module chat intelligent, séparé du RAG et de l'analyse documentaire.
        self.chat = EnnoChat(
            EnnoChatConfig(
                model=self.chat_model,
                timeout=30,
                temperature=0.2,
                num_predict=180,
                debug=False,
            )
        )

    def _get_document_metadata_from_rag(
        self,
        *,
        organisme_id: Optional[str] = None,
        file_hash: Optional[str] = None,
        document_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Récupère les métadonnées structurées d'un document déjà indexé dans ChromaDB.

        Pourquoi : quand on ouvre un document existant, on ne relance pas Extraction/NLP,
        mais le module chat et EnnoAmel ont quand même besoin des champs JSON NLP :
        verrous, méthodes, outils, modèles, benchmarks, résultats, etc.
        """
        try:
            store = getattr(self.rag, "store", None)
            if store is None or not hasattr(store, "_load_client"):
                return {}

            _, collection = store._load_client()
            if collection.count() == 0:
                return {}

            where = _build_chroma_where_for_document(
                organisme_id=organisme_id,
                file_hash=file_hash,
                document_id=document_id,
            )

            if where:
                data = collection.get(where=where, include=["metadatas"])
            else:
                data = collection.get(include=["metadatas"])

            metadatas = data.get("metadatas", []) or []
            if not metadatas:
                return {}

            merged: dict[str, Any] = {}

            for field in [
                "file_name",
                "organisme_name",
                "organisme_id",
                "file_hash",
                "document_id",
                "domaine_principal",
            ]:
                for meta in metadatas:
                    value = _restore_metadata_value((meta or {}).get(field))
                    if value not in (None, "", [], {}):
                        merged[field] = value
                        break

            for field in STRUCTURED_METADATA_FIELDS:
                if field in {
                    "file_name",
                    "organisme_name",
                    "organisme_id",
                    "file_hash",
                    "document_id",
                    "domaine_principal",
                }:
                    continue

                values: list[str] = []
                for meta in metadatas:
                    values.extend(_metadata_to_list((meta or {}).get(field)))

                values = _dedupe_texts(values)
                if values:
                    merged[field] = values

            return merged

        except Exception as exc:
            logger.warning("Impossible de récupérer document_metadata depuis RAG : %s", exc)
            return {}

    # ──────────────────────────────────────────────────────────────────────
    # DOCUMENT PREPARATION
    # ──────────────────────────────────────────────────────────────────────

    def prepare_document(
        self,
        file_path: str | Path,
        *,
        vision_mode: str = "full",
        source_tag: str = "DE_DOC",
        use_gliner: bool = True,
        use_regex: bool = True,
        use_llm_extractor: bool = True,
        llm_extractor_model: str = DEFAULT_LLM_MODEL,
        ner_on_visual_chunks: bool = False,
        include_debug: bool = False,
        save_nlp_json: bool = True,
        output_json_path: Optional[str | Path] = None,
        clear_previous_index: bool = False,
        organisme_name: Optional[str] = None,
        organisme_id: Optional[str] = None,
        file_hash: Optional[str] = None,
        document_id: Optional[str] = None,
        enable_formulas: bool = True,
    ) -> PreparationReport:
        """
        Prépare un document pour le chat RAG.

        Cas 1 : fichier brut PDF/DOCX/PPTX/Excel/email/image
          → extraction
          → NLP
          → JSON NLP
          → RAG ingest

        Cas 2 : fichier .nlp.json
          → chargement JSON
          → RAG ingest

        Retourne :
          PreparationReport
        """
        t0 = time.time()
        path = Path(file_path)

        report = PreparationReport(
            ok=False,
            file_path=str(path),
            file_name=path.name,
        )

        if clear_previous_index:
            try:
                self.rag.clear(delete_files=True)
                self.state.has_rag_index = False
                self.state.indexed_chunks = 0
            except Exception as exc:
                report.warnings.append(f"Impossible de vider l'index RAG : {exc}")

        if not path.exists():
            report.processing_time = time.time() - t0
            report.extraction_errors.append(f"Fichier introuvable : {path}")
            self.state.last_preparation = report
            return report

        resolved_org_name, resolved_org_id, resolved_file_hash, resolved_document_id = _resolve_document_identity(
            path=path,
            organisme_name=organisme_name,
            organisme_id=organisme_id,
            file_hash=file_hash,
            document_id=document_id,
        )

        report.organisme_name = resolved_org_name
        report.organisme_id = resolved_org_id
        report.file_hash = resolved_file_hash
        report.document_id = resolved_document_id

        try:
            if (
                not clear_previous_index
                and resolved_file_hash
                and hasattr(self.rag, "document_exists")
                and self.rag.document_exists(
                    organisme_id=resolved_org_id,
                    file_hash=resolved_file_hash,
                )
            ):
                report.ok = True
                report.mode = "already_indexed"
                report.already_indexed = True
                report.extraction_done = False
                report.nlp_done = False
                report.rag_done = True

                try:
                    report.indexed_chunks = self.rag.count_document_chunks(
                        organisme_id=resolved_org_id,
                        file_hash=resolved_file_hash,
                    )
                except Exception:
                    report.indexed_chunks = 0

                report.processing_time = time.time() - t0

                rag_metadata = self._get_document_metadata_from_rag(
                    organisme_id=resolved_org_id,
                    file_hash=resolved_file_hash,
                    document_id=resolved_document_id,
                )

                report.document_metadata = {
                    "file_name": path.stem,
                    "organisme_name": resolved_org_name,
                    "organisme_id": resolved_org_id,
                    "file_hash": resolved_file_hash,
                    "document_id": resolved_document_id,
                    "already_indexed": True,
                    **(rag_metadata or {}),
                }

                self.state.has_document = True
                self.state.has_rag_index = True
                self.state.current_file_path = str(path)
                self.state.current_file_name = path.name
                self.state.current_organisme_name = resolved_org_name
                self.state.current_organisme_id = resolved_org_id
                self.state.current_file_hash = resolved_file_hash
                self.state.current_document_id = resolved_document_id
                self.state.current_document_metadata = report.document_metadata or {}
                self.state.indexed_chunks = int(getattr(self.rag, "total_chunks", 0))
                self.state.last_preparation = report

                logger.info(
                    "EnnoAmel.prepare_document | déjà indexé | organisme=%s | file_hash=%s",
                    resolved_org_id,
                    resolved_file_hash[:12],
                )

                return report

            if _is_nlp_json_path(path):
                report.mode = "nlp_json"
                nlp_json = _load_json_file(path)

                if not isinstance(nlp_json, dict) or "chunks" not in nlp_json:
                    raise ValueError(
                        "Le fichier JSON ne semble pas être un JSON NLP valide : champ 'chunks' absent."
                    )

                nlp_json = _apply_document_identity_to_nlp_json(
                    nlp_json,
                    organisme_name=resolved_org_name,
                    organisme_id=resolved_org_id,
                    file_hash=resolved_file_hash,
                    document_id=resolved_document_id,
                )

                self.state.nlp_json = nlp_json
                self.state.has_document = True
                self.state.has_nlp = True
                self.state.current_file_path = str(path)
                self.state.current_file_name = (
                    nlp_json.get("document_metadata", {}).get("file_name")
                    or path.stem
                )
                self.state.current_organisme_name = resolved_org_name
                self.state.current_organisme_id = resolved_org_id
                self.state.current_file_hash = resolved_file_hash
                self.state.current_document_id = resolved_document_id

                n = self.rag.ingest(nlp_json)

                report.indexed_chunks = n
                report.rag_done = n > 0
                report.nlp_done = True
                report.extraction_done = False
                report.document_metadata = nlp_json.get("document_metadata", {}) or {}
                self.state.current_document_metadata = report.document_metadata or {}

            else:
                report.mode = "raw_document"

                extraction_result = self._run_extraction(
                    file_path=path,
                    vision_mode=vision_mode,
                    source_tag=source_tag,
                    enable_formulas=enable_formulas,
                )

                self.state.extraction_result = extraction_result
                self.state.has_extraction = True
                report.extraction_done = True

                extraction_errors = list(getattr(extraction_result, "extraction_errors", []) or [])
                report.extraction_errors.extend(extraction_errors)

                if not getattr(extraction_result, "is_valid", False):
                    report.processing_time = time.time() - t0
                    report.warnings.append(
                        "Extraction terminée mais aucun chunk exploitable n'a été produit."
                    )
                    self.state.last_preparation = report
                    return report

                nlp_result, nlp_json = self._run_nlp(
                    extraction_result=extraction_result,
                    use_gliner=use_gliner,
                    use_regex=use_regex,
                    use_llm_extractor=use_llm_extractor,
                    llm_extractor_model=llm_extractor_model,
                    ner_on_visual_chunks=ner_on_visual_chunks,
                    include_debug=include_debug,
                    organisme_name=resolved_org_name,
                    organisme_id=resolved_org_id,
                    file_hash=resolved_file_hash,
                    document_id=resolved_document_id,
                )

                self.state.nlp_result = nlp_result
                self.state.nlp_json = nlp_json
                self.state.has_nlp = True
                report.nlp_done = True

                if save_nlp_json:
                    if output_json_path is None:
                        default_out = path.with_suffix(path.suffix + ".nlp.json")
                    else:
                        default_out = Path(output_json_path)

                    try:
                        _save_json_file(nlp_json, default_out)
                    except Exception as exc:
                        report.warnings.append(
                            f"Impossible de sauvegarder le JSON NLP : {exc}"
                        )

                n = self.rag.ingest(nlp_json)

                report.indexed_chunks = n
                report.rag_done = n > 0
                report.document_metadata = nlp_json.get("document_metadata", {}) or {}
                self.state.current_document_metadata = report.document_metadata or {}

                self.state.current_file_path = str(path)
                self.state.current_file_name = getattr(extraction_result, "file_name", path.name)
                self.state.current_organisme_name = resolved_org_name
                self.state.current_organisme_id = resolved_org_id
                self.state.current_file_hash = resolved_file_hash
                self.state.current_document_id = resolved_document_id
                self.state.has_document = True

            self.state.has_rag_index = bool(
                report.rag_done or getattr(self.rag, "total_chunks", 0) > 0
            )
            self.state.indexed_chunks = int(
                getattr(self.rag, "total_chunks", report.indexed_chunks)
            )

            report.ok = bool(report.rag_done)
            report.processing_time = time.time() - t0

            self.state.last_preparation = report

            logger.info(
                "EnnoAmel.prepare_document | ok=%s | mode=%s | chunks=%d | %.2fs",
                report.ok,
                report.mode,
                report.indexed_chunks,
                report.processing_time,
            )

            return report

        except Exception as exc:
            logger.error("Erreur prepare_document : %s", exc, exc_info=True)
            report.ok = False
            report.processing_time = time.time() - t0
            report.extraction_errors.append(str(exc))
            self.state.last_preparation = report
            return report

    def prepare_nlp_json(
        self,
        nlp_json: dict[str, Any],
        *,
        file_name: str = "document_nlp",
        clear_previous_index: bool = False,
        organisme_name: Optional[str] = None,
        organisme_id: Optional[str] = None,
        file_hash: Optional[str] = None,
        document_id: Optional[str] = None,
    ) -> PreparationReport:
        """
        Prépare directement un JSON NLP déjà en mémoire.
        """
        t0 = time.time()

        report = PreparationReport(
            ok=False,
            file_name=file_name,
            mode="nlp_json_memory",
        )

        try:
            if clear_previous_index:
                self.rag.clear(delete_files=True)

            if not isinstance(nlp_json, dict) or "chunks" not in nlp_json:
                raise ValueError("nlp_json invalide : champ 'chunks' absent.")

            doc_meta = nlp_json.get("document_metadata", {}) or {}

            org_name = organisme_name or doc_meta.get("organisme_name") or "Organisme inconnu"
            org_id = _slugify(organisme_id or doc_meta.get("organisme_id") or org_name)
            fh = file_hash or doc_meta.get("file_hash")
            doc_id = document_id or doc_meta.get("document_id") or (
                f"{org_id}_{str(fh)[:16]}" if fh else f"{org_id}_{_slugify(file_name)}"
            )

            if (
                not clear_previous_index
                and fh
                and hasattr(self.rag, "document_exists")
                and self.rag.document_exists(organisme_id=org_id, file_hash=fh)
            ):
                report.ok = True
                report.mode = "already_indexed"
                report.already_indexed = True
                report.rag_done = True
                report.file_hash = fh
                report.document_id = doc_id
                report.organisme_name = org_name
                report.organisme_id = org_id
                report.indexed_chunks = (
                    self.rag.count_document_chunks(organisme_id=org_id, file_hash=fh)
                    if hasattr(self.rag, "count_document_chunks")
                    else 0
                )
                report.document_metadata = {
                    **doc_meta,
                    "organisme_name": org_name,
                    "organisme_id": org_id,
                    "file_hash": fh,
                    "document_id": doc_id,
                }
                report.processing_time = time.time() - t0

                self.state.has_document = True
                self.state.has_rag_index = True
                self.state.current_file_name = file_name
                self.state.current_organisme_name = org_name
                self.state.current_organisme_id = org_id
                self.state.current_file_hash = fh
                self.state.current_document_id = doc_id
                self.state.current_document_metadata = report.document_metadata or {}
                self.state.last_preparation = report

                return report

            nlp_json = _apply_document_identity_to_nlp_json(
                nlp_json,
                organisme_name=org_name,
                organisme_id=org_id,
                file_hash=fh,
                document_id=doc_id,
            )

            n = self.rag.ingest(nlp_json)

            self.state.nlp_json = nlp_json
            self.state.has_document = True
            self.state.has_nlp = True
            self.state.has_rag_index = n > 0 or getattr(self.rag, "total_chunks", 0) > 0
            self.state.indexed_chunks = int(getattr(self.rag, "total_chunks", n))
            self.state.current_file_name = (
                nlp_json.get("document_metadata", {}).get("file_name")
                or file_name
            )
            self.state.current_organisme_name = org_name
            self.state.current_organisme_id = org_id
            self.state.current_file_hash = fh
            self.state.current_document_id = doc_id

            report.ok = n > 0
            report.rag_done = n > 0
            report.file_hash = fh
            report.document_id = doc_id
            report.organisme_name = org_name
            report.organisme_id = org_id
            report.nlp_done = True
            report.indexed_chunks = n
            report.document_metadata = nlp_json.get("document_metadata", {}) or {}
            self.state.current_document_metadata = report.document_metadata or {}
            report.processing_time = time.time() - t0

            self.state.last_preparation = report

            return report

        except Exception as exc:
            report.ok = False
            report.extraction_errors.append(str(exc))
            report.processing_time = time.time() - t0
            self.state.last_preparation = report
            return report

    # ──────────────────────────────────────────────────────────────────────
    # ASK / ROUTING
    # ──────────────────────────────────────────────────────────────────────

    def ask(
        self,
        question: str,
        *,
        top_k: Optional[int] = None,
        filter_meta: Optional[dict[str, Any]] = None,
        include_debug: bool = False,
    ) -> EnnoAmelResponse:
        """
        Répond à une question utilisateur.

        Étapes :
          0. modules/chat : conversation humaine avant RAG.
          1. Détection d'intention.
          2. Si HELP : réponse directe.
          3. Si document/RAG manquant : message clair.
          4. RAG ask avec intent + recommended_agent.
          5. Ajout note POC si agent spécialisé.
        """
        t0 = time.time()

        # ──────────────────────────────────────────────────────────────
        # MODULE CHAT : compréhension conversationnelle avant EnnoAmel/RAG
        # ──────────────────────────────────────────────────────────────
        try:
            chat_decision = self.chat.understand(
                question,
                has_document=self.state.has_document,
                has_rag_index=self.state.has_rag_index,
                document_metadata=self.state.current_document_metadata,
                chat_history=None,
            )

            if chat_decision.handled:
                return EnnoAmelResponse(
                    answer=chat_decision.answer,
                    intent=chat_decision.intent,
                    recommended_agent=chat_decision.recommended_agent,
                    action=chat_decision.action,
                    confidence=chat_decision.confidence,
                    sources=[],
                    needs_specialized_agent=chat_decision.needs_specialized_agent,
                    rag_used=False,
                    chunks_used=0,
                    route_explanation="Réponse gérée par modules/chat avant EnnoAmel/RAG.",
                    processing_time=time.time() - t0,
                    debug=chat_decision.debug if include_debug else {},
                )

        except Exception as exc:
            logger.warning("Module chat indisponible, fallback EnnoAmel/RAG : %s", exc)

        # Si EnnoChat a compris une intention documentaire/spécialisée,
        # on la convertit en IntentDecision pour éviter de refaire une mauvaise détection.
        # Si EnnoChat est tombé en fallback, on utilise le routeur local classique.
        decision = self._intent_decision_from_chat(
            chat_decision=locals().get("chat_decision"),
            question=question,
        )
        # ─────────────────────────────────────────────────────────────
        # Réponse directe du LLM de chat
        # ─────────────────────────────────────────────────────────────
        # Si le module chat décide handled=True :
        # - on répond immédiatement ;
        # - on NE va PAS vers detect_intent ;
        # - on NE va PAS vers le RAG ;
        # - on NE cite PAS de sources.
        chat_decision_obj = locals().get("chat_decision")
        if chat_decision_obj is not None and getattr(chat_decision_obj, "handled", False):
            direct_answer = str(getattr(chat_decision_obj, "answer", "") or "").strip()
            direct_answer = direct_answer.replace("EnnoAmel", "Orchestrateur")

            return EnnoAmelResponse(
                answer=direct_answer or "Bonjour, je suis là. Que souhaitez-vous faire ?",
                intent=str(getattr(chat_decision_obj, "intent", "small_talk")),
                recommended_agent=str(
                    getattr(chat_decision_obj, "recommended_agent", "Orchestrateur")
                ).replace("EnnoAmel", "Orchestrateur"),
                action="chat_direct_answer_no_rag",
                confidence=float(getattr(chat_decision_obj, "confidence", 0.95) or 0.95),
                sources=[],
                needs_specialized_agent=bool(getattr(chat_decision_obj, "needs_specialized_agent", False)),
                rag_used=False,
                chunks_used=0,
                route_explanation="Réponse directe du module de chat intelligent. RAG désactivé.",
                processing_time=time.time() - t0,
                debug={
                    "chat_decision": getattr(chat_decision_obj, "__dict__", {}),
                    "rag_skipped": True,
                    "detect_intent_skipped": True,
                } if include_debug else {},
            )

        if decision is None:
            decision = detect_intent(
                user_message=question,
                has_document=self.state.has_document,
                has_rag_index=self.state.has_rag_index,
            )

        chat_decision_obj = locals().get("chat_decision")
        chat_rag_instruction = str(getattr(chat_decision_obj, "rag_instruction", "") or "").strip()
        chat_rag_search_query = str(getattr(chat_decision_obj, "rag_search_query", "") or "").strip()

        if decision.intent == Intent.HELP:
            return EnnoAmelResponse(
                answer=_help_message(),
                intent=decision.intent.value,
                recommended_agent=decision.recommended_agent.value,
                action=decision.action,
                confidence=decision.confidence,
                needs_specialized_agent=False,
                rag_used=False,
                chunks_used=0,
                route_explanation=decision.explanation,
                processing_time=time.time() - t0,
                debug={"decision": decision.to_dict()} if include_debug else {},
            )

        if decision.intent in {Intent.ELIGIBILITY, Intent.DIAGNOSTIC}:
            return EnnoAmelResponse(
                answer=(
                    "EnnoDiagnostic est encore en cours de construction pour produire le score "
                    "d’éligibilité CIR, l’analyse des risques et la validation détaillée. "
                    "Pour le moment, je peux vous aider à lire le dossier et à résumer les objectifs, "
                    "verrous, méthodes et résultats à partir des sources indexées."
                ),
                intent=decision.intent.value,
                recommended_agent=AgentName.ENNODIAGNOSTIC.value,
                action="ennodiagnostic_under_construction",
                confidence=max(float(decision.confidence), 0.85),
                sources=[],
                needs_specialized_agent=True,
                rag_used=False,
                chunks_used=0,
                route_explanation="Demande réservée à EnnoDiagnostic, encore en cours de construction.",
                processing_time=time.time() - t0,
                debug={"decision": decision.to_dict()} if include_debug else {},
            )

        if decision.intent == Intent.SCHOLAR:
            return EnnoAmelResponse(
                answer=(
                    "EnnoScholar est encore en cours de construction pour rédiger un état de l’art complet "
                    "avec recherche d’articles, bibliographie, citations et gap analysis. "
                    "Pour le moment, je peux seulement résumer l’état de l’art présent dans le document indexé."
                ),
                intent=decision.intent.value,
                recommended_agent=AgentName.ENNOSCHOLAR.value,
                action="ennoscholar_under_construction",
                confidence=max(float(decision.confidence), 0.85),
                sources=[],
                needs_specialized_agent=True,
                rag_used=False,
                chunks_used=0,
                route_explanation="Demande réservée à EnnoScholar, encore en cours de construction.",
                processing_time=time.time() - t0,
                debug={"decision": decision.to_dict()} if include_debug else {},
            )

        if decision.intent == Intent.VALOR:
            return EnnoAmelResponse(
                answer=(
                    "EnnoValor est encore en cours de construction pour traiter la partie financière, RH, "
                    "Excel, Cerfa et livrables administratifs. Pour le moment, je peux vous aider "
                    "sur la compréhension documentaire du dossier."
                ),
                intent=decision.intent.value,
                recommended_agent=AgentName.ENNOVALOR.value,
                action="ennovalor_under_construction",
                confidence=max(float(decision.confidence), 0.85),
                sources=[],
                needs_specialized_agent=True,
                rag_used=False,
                chunks_used=0,
                route_explanation="Demande réservée à EnnoValor, encore en cours de construction.",
                processing_time=time.time() - t0,
                debug={"decision": decision.to_dict()} if include_debug else {},
            )

        # Résumé direct depuis le JSON NLP si disponible :
        # plus rapide, plus stable, aucun appel LLM/RAG pour la vue globale.
        if (
            decision.intent == Intent.SUMMARY
            and self.state.nlp_json
            and not chat_rag_instruction
        ):
            summary = self._build_summary_from_nlp_json(self.state.nlp_json)
            if summary:
                return EnnoAmelResponse(
                    answer=summary,
                    intent=decision.intent.value,
                    recommended_agent=decision.recommended_agent.value,
                    action="answer_summary_from_nlp_json",
                    confidence=max(float(decision.confidence), 0.90),
                    sources=[],
                    needs_specialized_agent=False,
                    rag_used=False,
                    chunks_used=0,
                    route_explanation="Résumé construit directement depuis le JSON NLP enrichi.",
                    processing_time=time.time() - t0,
                    debug={
                        "decision": decision.to_dict(),
                        "state": self.state.to_dict(),
                        "source": "nlp_json_direct",
                    } if include_debug else {},
                )

        if decision.action == "need_document_first":
            return EnnoAmelResponse(
                answer=(
                    "Je dois d'abord préparer un document avant de répondre. "
                    "Importe un PDF/DOCX/PPTX/Excel/email/image ou un fichier `.nlp.json`, "
                    "puis lance `prepare_document()`."
                ),
                intent=decision.intent.value,
                recommended_agent=decision.recommended_agent.value,
                action=decision.action,
                confidence=decision.confidence,
                needs_specialized_agent=decision.is_specialized_agent_required,
                rag_used=False,
                chunks_used=0,
                route_explanation=decision.explanation,
                processing_time=time.time() - t0,
                debug={
                    "decision": decision.to_dict(),
                    "state": self.state.to_dict(),
                } if include_debug else {},
            )

        if decision.action == "need_rag_index_first":
            return EnnoAmelResponse(
                answer=(
                    "Le document est chargé, mais l'index RAG n'est pas prêt. "
                    "Relance la préparation du document ou vérifie l'étape `rag.ingest()`."
                ),
                intent=decision.intent.value,
                recommended_agent=decision.recommended_agent.value,
                action=decision.action,
                confidence=decision.confidence,
                needs_specialized_agent=decision.is_specialized_agent_required,
                rag_used=False,
                chunks_used=0,
                route_explanation=decision.explanation,
                processing_time=time.time() - t0,
                debug={
                    "decision": decision.to_dict(),
                    "state": self.state.to_dict(),
                } if include_debug else {},
            )

        effective_filter_meta = dict(filter_meta or {})

        if self.state.current_organisme_id and "organisme_id" not in effective_filter_meta:
            effective_filter_meta["organisme_id"] = self.state.current_organisme_id

        if self.state.current_document_id and "document_id" not in effective_filter_meta:
            effective_filter_meta["document_id"] = self.state.current_document_id

        if decision.intent in {Intent.EXTRACTION, Intent.NLP}:
            return self._answer_pipeline_status(
                decision=decision,
                include_debug=include_debug,
                start_time=t0,
            )

        if decision.intent == Intent.RAG_DEBUG:
            return self.debug_search(
                question=decision.rag_query or question,
                top_k=top_k or self.top_k,
                filter_meta=effective_filter_meta,
                decision=decision,
                include_debug=include_debug,
                start_time=t0,
            )

        try:
            if chat_rag_instruction:
                effective_rag_question = (
                    f"Question utilisateur :\n{question}\n\n"
                    f"Recherche documentaire ciblée :\n{chat_rag_search_query or decision.rag_query or question}\n\n"
                    f"Instruction de réponse :\n{chat_rag_instruction}\n\n"
                    "Réponds de façon naturelle, concise si demandé, et uniquement sur le sujet demandé."
                )
            else:
                effective_rag_question = decision.rag_query or question

            rag_response = self.rag.ask(
                question=effective_rag_question,
                filter_meta=effective_filter_meta,
                top_k=top_k or self.top_k,
                intent=decision.intent.value,
                recommended_agent=decision.recommended_agent.value,
            )

            answer = str(getattr(rag_response, "answer", "") or "").strip()

            if not answer:
                answer = (
                    "Le RAG a récupéré des sources, mais le modèle n'a pas produit de réponse exploitable."
                )

            if decision.is_specialized_agent_required:
                answer += _short_agent_note(
                    intent=decision.intent.value,
                    agent=decision.recommended_agent.value,
                )

            sources = list(getattr(rag_response, "sources", []) or [])
            chunks_used = int(getattr(rag_response, "chunks_used", len(sources)) or 0)

            # Résumé court projet : le RAG sert seulement de contexte interne.
            # On masque les sources pour éviter l'affichage "Sources utilisées (5)"
            # et on évite les citations [S1] dans la réponse.
            no_source_summary = False
            try:
                instr_lower = str(chat_rag_instruction or "").lower()
                no_source_summary = (
                    decision.intent.value in {"summary", "qa"}
                    and (
                        "petit résumé clair" in instr_lower
                        or "ne pas citer" in instr_lower
                        or "ne pas afficher de sources" in instr_lower
                        or "un seul paragraphe" in instr_lower
                    )
                )
            except Exception:
                no_source_summary = False

            if no_source_summary:
                sources = []
                chunks_used = 0
                answer = re.sub(r"\s*\[S\d+\]", "", answer).strip()
                answer = re.sub(r"(?is)\n*Sources utilisées.*$", "", answer).strip()

            return EnnoAmelResponse(
                answer=answer,
                intent=decision.intent.value,
                recommended_agent=decision.recommended_agent.value,
                action=decision.action,
                confidence=decision.confidence,
                sources=sources,
                needs_specialized_agent=decision.is_specialized_agent_required,
                rag_used=True,
                chunks_used=chunks_used,
                route_explanation=decision.explanation,
                processing_time=time.time() - t0,
                error=getattr(rag_response, "error", None),
                debug={
                    "decision": decision.to_dict(),
                    "state": self.state.to_dict(),
                    "rag_response": (
                        rag_response.to_dict()
                        if hasattr(rag_response, "to_dict")
                        else {}
                    ),
                } if include_debug else {},
            )

        except Exception as exc:
            logger.error("Erreur EnnoAmel.ask : %s", exc, exc_info=True)

            return EnnoAmelResponse(
                answer=(
                    "Erreur lors de la génération de la réponse RAG. "
                    "Vérifie le RAG, ChromaDB et le modèle Ollama."
                ),
                intent=decision.intent.value,
                recommended_agent=decision.recommended_agent.value,
                action=decision.action,
                confidence=decision.confidence,
                sources=[],
                needs_specialized_agent=decision.is_specialized_agent_required,
                rag_used=False,
                chunks_used=0,
                route_explanation=decision.explanation,
                processing_time=time.time() - t0,
                error=str(exc),
                debug={
                    "decision": decision.to_dict(),
                    "state": self.state.to_dict(),
                } if include_debug else {},
            )

    def _intent_decision_from_chat(
        self,
        *,
        chat_decision: Optional[Any],
        question: str,
    ) -> Optional[IntentDecision]:
        """
        Convertit la décision de modules/chat.EnnoChat vers le routeur EnnoAmel.

        Pourquoi :
          - EnnoChat détecte les messages simples localement ;
          - pour les demandes documentaires, il peut aussi recommander EnnoAmel,
            EnnoDiagnostic, EnnoScholar ou EnnoValor ;
          - cette méthode rend cette sortie compatible avec IntentDecision.
        """
        if chat_decision is None:
            return None

        raw_intent = str(getattr(chat_decision, "intent", "") or "").strip()
        if not raw_intent or raw_intent in {
            "fallback_to_ennoamel",
            "clarification",
            "unknown",
            "small_talk",
            "thanks",
            "help",
        }:
            return None

        intent_map: dict[str, Intent] = {
            "project_summary": Intent.SUMMARY,
            "summary": Intent.SUMMARY,
            "keywords": Intent.QA,
            "entities": Intent.QA,
            "verrous": Intent.QA,
            "objectives": Intent.QA,
            "methods": Intent.QA,
            "technologies": Intent.QA,
            "materials": Intent.QA,
            "people": Intent.QA,
            "organisms": Intent.QA,
            "sections": Intent.QA,
            "etat_art": Intent.QA,
            "results": Intent.QA,
            "source_proof": Intent.QA,
            "document_question": Intent.QA,
            "eligibility": Intent.ELIGIBILITY,
            "diagnostic_detail": Intent.DIAGNOSTIC,
            "diagnostic": Intent.DIAGNOSTIC,
            "scholar": Intent.SCHOLAR,
            "valorisation": Intent.VALOR,
            "valor": Intent.VALOR,
        }

        intent = intent_map.get(raw_intent)
        if intent is None:
            return None

        recommended = str(
            getattr(chat_decision, "recommended_agent", "") or "EnnoAmel"
        ).strip()

        agent_map = {
            "EnnoAmel": AgentName.ENNOAMEL,
            "Orchestrateur": AgentName.ENNOAMEL,
            "EnnoDiagnostic": AgentName.ENNODIAGNOSTIC,
            "EnnoScholar": AgentName.ENNOSCHOLAR,
            "EnnoValor": AgentName.ENNOVALOR,
        }
        agent = agent_map.get(recommended)

        if agent is None:
            if intent in {Intent.ELIGIBILITY, Intent.DIAGNOSTIC}:
                agent = AgentName.ENNODIAGNOSTIC
            elif intent == Intent.SCHOLAR:
                agent = AgentName.ENNOSCHOLAR
            elif intent == Intent.VALOR:
                agent = AgentName.ENNOVALOR
            else:
                agent = AgentName.ENNOAMEL

        confidence = float(getattr(chat_decision, "confidence", 0.75) or 0.75)
        confidence = max(0.0, min(confidence, 1.0))

        specialized = bool(getattr(chat_decision, "needs_specialized_agent", False))
        if agent != AgentName.ENNOAMEL:
            specialized = True

        needs_doc = intent not in {Intent.HELP}
        needs_rag = intent not in {Intent.HELP, Intent.EXTRACTION, Intent.NLP}

        action_map: dict[Intent, str] = {
            Intent.SUMMARY: "answer_summary_with_rag",
            Intent.QA: "answer_question_with_rag",
            Intent.ELIGIBILITY: "preliminary_cir_estimation_then_redirect",
            Intent.DIAGNOSTIC: "diagnostic_preview_then_redirect",
            Intent.SCHOLAR: "answer_available_context_then_redirect_scholar",
            Intent.VALOR: "answer_available_context_then_redirect_valor",
        }
        action = action_map.get(intent, "fallback_rag_answer")

        explanation = (
            f"Intention détectée par modules/chat : {raw_intent}. "
            f"Routage converti vers {intent.value} / {agent.value}."
        )

        chat_rag_instruction = str(getattr(chat_decision, "rag_instruction", "") or "").strip()
        chat_rag_search_query = str(getattr(chat_decision, "rag_search_query", "") or "").strip()

        if chat_rag_instruction:
            rag_query = (
                f"{chat_rag_search_query or question}\n\n"
                f"Instruction de réponse : {chat_rag_instruction}"
            )
        else:
            rag_query = self._build_rag_query_from_chat_intent(
                question=question,
                chat_intent=raw_intent,
                intent=intent,
            )

        if needs_doc and not self.state.has_document:
            action = "need_document_first"
            needs_rag = False
            explanation += " Aucun document n'est encore préparé."
        elif needs_rag and not self.state.has_rag_index:
            action = "need_rag_index_first"
            explanation += " Le document existe mais le RAG n'est pas encore indexé."

        return IntentDecision(
            intent=intent,
            recommended_agent=agent,
            confidence=confidence,
            action=action,
            rag_query=rag_query,
            needs_rag=needs_rag,
            needs_document=needs_doc,
            is_specialized_agent_required=specialized,
            explanation=explanation,
            matched_keywords=[raw_intent],
            scores=[],
        )

    @staticmethod
    def _build_rag_query_from_chat_intent(
        *,
        question: str,
        chat_intent: str,
        intent: Intent,
    ) -> str:
        """
        Enrichit légèrement la requête envoyée au RAG selon l'intention
        détectée par EnnoChat.
        """
        msg = str(question or "").strip()

        additions = {
            "project_summary": "Résumé stratégique : domaine, objet de recherche, objectifs, verrous, méthodes, outils, résultats.",
            "keywords": "Mots-clés, entités importantes, technologies, domaines, composants et concepts du projet.",
            "verrous": "Verrous techniques, incertitudes, limites scientifiques ou techniques.",
            "objectives": "Objectifs R&D, objectifs techniques, finalités du projet.",
            "methods": "Méthodes R&D, protocoles, approches, expérimentations, modélisation.",
            "technologies": "Outils, technologies, frameworks, logiciels, modèles, architectures.",
            "materials": "Matériaux, composants, équipements et éléments techniques.",
            "people": "Personnes, équipe projet, fonctions et contributions.",
            "organisms": "Organismes, partenaires, entreprises, laboratoires et consortium.",
            "sections": "Sections du document, structure, plan et passages.",
            "etat_art": "État de l'art présent dans le document, travaux existants, limites et gap.",
            "results": "Résultats, métriques, performances, validations, limites et perspectives.",
            "source_proof": "Sources exactes, preuves, passages du document et justification.",
            "eligibility": "Éligibilité CIR : verrous, incertitudes, démarche R&D, preuves, résultats, risques.",
            "diagnostic_detail": "Diagnostic CIR détaillé : verrous, preuves, risques, justification, points faibles.",
            "scholar": "État de l'art : travaux existants, articles, publications, limites, gap analysis.",
            "valorisation": "Valorisation : dépenses, RH, ETP, montants, budget, Excel, Cerfa.",
        }

        extra = additions.get(chat_intent, "")
        if not extra:
            return msg
        return f"{msg}\n{extra}"

    def _build_summary_from_nlp_json(self, nlp_json: dict[str, Any]) -> str:
        """
        Construit un résumé structuré directement depuis document_metadata.
        Aucun appel RAG ni LLM.
        """
        if not isinstance(nlp_json, dict):
            return ""

        meta = nlp_json.get("document_metadata", {}) or {}

        domaine = meta.get("domaine_principal", "")
        objet = meta.get("objet_recherche", "")
        mots_cles = meta.get("mots_cles_projet", {}) or {}
        mots_cles_high = []
        if isinstance(mots_cles, dict):
            mots_cles_high = mots_cles.get("high_confidence", []) or []
        elif isinstance(mots_cles, list):
            mots_cles_high = mots_cles

        objectifs = meta.get("objectifs_rd", [])
        verrous = meta.get("verrous_techniques", [])
        hypotheses = meta.get("hypotheses_rd", [])
        methodes = meta.get("methodes_rd", [])
        outils = meta.get("outils_technologies", [])
        resultats = meta.get("resultats_rd", [])
        limites = meta.get("limitations_perspectives", [])

        if not any([
            domaine,
            objet,
            mots_cles_high,
            objectifs,
            verrous,
            hypotheses,
            methodes,
            outils,
            resultats,
            limites,
        ]):
            return ""

        def fmt_list(value: Any, limit: int = 8) -> str:
            items = _metadata_to_list(value)
            items = _dedupe_texts(items)[:limit]
            return "\n".join(f"- {item}" for item in items) if items else "_Non renseigné_"

        lines = ["## Résumé du projet"]

        file_name = meta.get("file_name")
        if file_name:
            lines.append(f"\n**Document :** `{file_name}`")
        if domaine:
            lines.append(f"\n**Domaine principal :** {domaine}")
        if objet:
            lines.append(f"\n**Objet de recherche :**\n{fmt_list(objet, limit=3)}")
        if mots_cles_high:
            lines.append(f"\n**Mots-clés principaux :**\n{fmt_list(mots_cles_high, limit=12)}")
        if objectifs:
            lines.append(f"\n**Objectifs R&D :**\n{fmt_list(objectifs)}")
        if verrous:
            lines.append(f"\n**Verrous techniques / incertitudes :**\n{fmt_list(verrous)}")
        if hypotheses:
            lines.append(f"\n**Hypothèses R&D :**\n{fmt_list(hypotheses)}")
        if methodes:
            lines.append(f"\n**Méthodes / protocoles :**\n{fmt_list(methodes)}")
        if outils:
            lines.append(f"\n**Outils / technologies :**\n{fmt_list(outils)}")
        if resultats:
            lines.append(f"\n**Résultats mentionnés :**\n{fmt_list(resultats)}")
        if limites:
            lines.append(f"\n**Limites / perspectives :**\n{fmt_list(limites)}")

        lines.append(
            "\n---\n"
            "_Résumé construit directement depuis le JSON NLP enrichi, sans appel LLM._"
        )

        return "\n".join(lines)

    def get_keywords_with_refs(self) -> dict[str, list[dict[str, Any]]]:
        """
        Retourne les mots-clés / verrous / méthodes / outils avec références.

        Compatible avec deux cas :
          1. Document fraîchement préparé : self.state.nlp_json est disponible.
          2. Document ouvert depuis ChromaDB après reload Streamlit :
             self.state.nlp_json peut être vide, mais current_document_metadata existe.

        Correction importante :
          - évite les doublons causés par les métadonnées documentaires copiées
            dans chaque chunk RAG ;
          - privilégie les sources propres du JSON NLP :
              1) document_metadata.aggregated_evidence
              2) document_metadata.fiche_cir
              3) document_metadata direct
              4) chunks seulement en fallback
          - dédoublonne par valeur normalisée, pas par chunk_id.
        """
        nlp_json = self.state.nlp_json or {}

        if isinstance(nlp_json, dict) and nlp_json.get("document_metadata"):
            doc_meta = nlp_json.get("document_metadata", {}) or {}
        else:
            doc_meta = self.state.current_document_metadata or {}

        if not doc_meta and not nlp_json:
            return {}

        result: dict[str, list[dict[str, Any]]] = {
            "verrous_techniques": [],
            "objectifs_rd": [],
            "methodes_rd": [],
            "outils_technologies": [],
            "resultats_rd": [],
            "modeles_algorithmes": [],
        }

        seen: dict[str, set[str]] = {
            field: set()
            for field in result.keys()
        }

        def normalize_value(value: str) -> str:
            text = str(value or "").lower().strip()
            text = unicodedata.normalize("NFKD", text)
            text = "".join(ch for ch in text if not unicodedata.combining(ch))
            text = text.replace("’", "'")
            text = re.sub(r"\s+", " ", text)
            text = text.rstrip(".;: ")
            return text

        def add_entry(
            field: str,
            value: Any,
            *,
            chunk_id: str = "",
            excerpt: str = "",
            source: str = "",
        ) -> None:
            if field not in result:
                result[field] = []
                seen[field] = set()

            value = str(value or "").strip()
            if not value:
                return

            key = normalize_value(value)
            if not key or key in seen[field]:
                return

            seen[field].add(key)

            result[field].append(
                {
                    "value": value,
                    "chunk_id": str(chunk_id or ""),
                    "field": field,
                    "excerpt": str(excerpt or value).replace("\n", " ").strip(),
                    "source": source,
                }
            )

        def first_passage_id(item: dict[str, Any]) -> str:
            passage_ids = item.get("passage_ids") or []
            if isinstance(passage_ids, list) and passage_ids:
                return str(passage_ids[0])
            return ""

        # 1. Source prioritaire : aggregated_evidence.by_role
        aggregated = doc_meta.get("aggregated_evidence", {}) or {}
        by_role = aggregated.get("by_role", {}) or {}

        role_to_field = {
            "verrou": "verrous_techniques",
            "objectif": "objectifs_rd",
            "demarche": "methodes_rd",
            "essai": "methodes_rd",
            "resultat": "resultats_rd",
        }

        for role, field in role_to_field.items():
            items = by_role.get(role, []) or []

            for item in items:
                if not isinstance(item, dict):
                    continue

                phrase = item.get("phrase") or ""
                chunk_id = first_passage_id(item)

                add_entry(
                    field,
                    phrase,
                    chunk_id=chunk_id,
                    excerpt=phrase,
                    source="aggregated_evidence",
                )

        # 2. Source propre : fiche_cir
        fiche_cir = doc_meta.get("fiche_cir", {}) or {}

        fiche_mapping = {
            "objectifs": "objectifs_rd",
            "verrous": "verrous_techniques",
            "essais": "methodes_rd",
            "demarche": "methodes_rd",
            "resultats": "resultats_rd",
        }

        for fiche_key, field in fiche_mapping.items():
            items = fiche_cir.get(fiche_key, []) or []

            if isinstance(items, dict):
                items = [items]

            for item in items:
                if not isinstance(item, dict):
                    continue

                value = item.get("resume") or ""
                preuves = item.get("preuves") or []

                excerpt = ""
                if isinstance(preuves, list) and preuves:
                    excerpt = str(preuves[0])
                elif isinstance(preuves, str):
                    excerpt = preuves

                add_entry(
                    field,
                    value,
                    excerpt=excerpt or value,
                    source="fiche_cir",
                )

        # 3. Concepts / technologies depuis aggregated_evidence.concepts
        concepts = aggregated.get("concepts", []) or []

        for concept in concepts:
            if not isinstance(concept, dict):
                continue

            value = concept.get("text") or ""
            passage_ids = concept.get("passage_ids") or []
            chunk_id = ""

            if isinstance(passage_ids, list) and passage_ids:
                chunk_id = str(passage_ids[0])

            add_entry(
                "outils_technologies",
                value,
                chunk_id=chunk_id,
                excerpt=value,
                source="aggregated_concepts",
            )

        # 4. Source document_metadata directe ou current_document_metadata
        direct_fields = {
            "verrous_techniques": "verrous_techniques",
            "objectifs_rd": "objectifs_rd",
            "methodes_rd": "methodes_rd",
            "outils_technologies": "technologies",
            "resultats_rd": "resultats_rd",
            "modeles_algorithmes": "modeles_algorithmes",
        }

        for output_field, meta_field in direct_fields.items():
            value = doc_meta.get(meta_field)

            if not value:
                continue

            items = value if isinstance(value, list) else [value]

            for item in items:
                add_entry(
                    output_field,
                    item,
                    excerpt=str(item),
                    source="document_metadata",
                )

        # 5. mots_cles_projet.high_confidence vers outils_technologies
        mots_cles = doc_meta.get("mots_cles_projet", {}) or {}
        if isinstance(mots_cles, dict):
            high_conf = mots_cles.get("high_confidence", []) or []
            for item in high_conf:
                add_entry(
                    "outils_technologies",
                    item,
                    excerpt=str(item),
                    source="mots_cles_high_confidence",
                )

        # 6. Fallback chunks seulement si nlp_json existe vraiment
        chunks = []
        if isinstance(nlp_json, dict):
            chunks = nlp_json.get("chunks", []) or []

        chunk_fields = [
            "verrous_techniques",
            "objectifs_rd",
            "methodes_rd",
            "outils_technologies",
            "resultats_rd",
            "modeles_algorithmes",
        ]

        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue

            meta = chunk.get("metadata", {}) or {}
            content = str(chunk.get("content", "") or "")
            content_lower = content.lower()
            chunk_id = (
                chunk.get("chunk_id")
                or meta.get("chunk_id")
                or ""
            )

            for field in chunk_fields:
                # Si le champ a déjà été rempli par une source propre,
                # on évite de reprendre les mêmes métadonnées copiées dans tous les chunks.
                if result.get(field):
                    continue

                value = meta.get(field)
                if not value:
                    continue

                items = _metadata_to_list(value)

                for item in items:
                    item_str = re.sub(r"\s+", " ", str(item or "")).strip()
                    if not item_str:
                        continue

                    excerpt = ""
                    idx = content_lower.find(item_str.lower())

                    if idx >= 0:
                        start = max(0, idx - 100)
                        end = min(len(content), idx + len(item_str) + 100)
                        excerpt = content[start:end].replace("\n", " ").strip()
                    else:
                        excerpt = content[:260].replace("\n", " ").strip()

                    add_entry(
                        field,
                        item_str,
                        chunk_id=chunk_id,
                        excerpt=excerpt,
                        source="chunk_metadata_fallback",
                    )

        return {
            field: items
            for field, items in result.items()
            if items
        }





    def debug_search(
        self,
        question: str,
        *,
        top_k: int = 5,
        filter_meta: Optional[dict[str, Any]] = None,
        decision: Optional[IntentDecision] = None,
        include_debug: bool = True,
        start_time: Optional[float] = None,
    ) -> EnnoAmelResponse:
        """
        Recherche RAG sans appeler le LLM.
        Utile pour voir les chunks récupérés.
        """
        t0 = start_time or time.time()

        if decision is None:
            decision = detect_intent(
                question,
                has_document=self.state.has_document,
                has_rag_index=self.state.has_rag_index,
            )

        effective_filter_meta = dict(filter_meta or {})

        if self.state.current_organisme_id and "organisme_id" not in effective_filter_meta:
            effective_filter_meta["organisme_id"] = self.state.current_organisme_id

        if self.state.current_document_id and "document_id" not in effective_filter_meta:
            effective_filter_meta["document_id"] = self.state.current_document_id

        # Réponse directe du LLM de chat AVANT toute exigence document.
        # Les messages sociaux et les agents en construction ne doivent jamais tomber dans need_document_first.
        chat_decision_obj = locals().get("chat_decision")
        if chat_decision_obj is not None and getattr(chat_decision_obj, "handled", False):
            direct_answer = str(getattr(chat_decision_obj, "answer", "") or "").strip()
            direct_answer = direct_answer.replace("EnnoAmel", "Orchestrateur")

            return EnnoAmelResponse(
                answer=direct_answer or "Bonjour, je suis là. Que souhaitez-vous faire ?",
                intent=str(getattr(chat_decision_obj, "intent", "small_talk")),
                recommended_agent=str(
                    getattr(chat_decision_obj, "recommended_agent", "Orchestrateur")
                ).replace("EnnoAmel", "Orchestrateur"),
                action="chat_direct_answer_before_document_check",
                confidence=float(getattr(chat_decision_obj, "confidence", 0.95) or 0.95),
                sources=[],
                needs_specialized_agent=bool(getattr(chat_decision_obj, "needs_specialized_agent", False)),
                rag_used=False,
                chunks_used=0,
                route_explanation="Réponse directe du module de chat intelligent avant contrôle document.",
                processing_time=time.time() - t0,
                debug={
                    "chat_decision": getattr(chat_decision_obj, "__dict__", {}),
                    "rag_skipped": True,
                    "need_document_skipped": True,
                } if include_debug else {},
            )

        if not self.state.has_rag_index:
            return EnnoAmelResponse(
                answer="Aucun index RAG prêt. Prépare d'abord un document.",
                intent=decision.intent.value,
                recommended_agent=decision.recommended_agent.value,
                action="debug_search_failed_no_rag",
                confidence=decision.confidence,
                route_explanation=decision.explanation,
                processing_time=time.time() - t0,
            )

        try:
            results = self.rag.search(
                question=question,
                top_k=top_k,
                filter_meta=effective_filter_meta,
                intent=decision.intent.value,
            )

            if not results:
                answer = "Aucun chunk pertinent trouvé."
            else:
                lines = ["Voici les chunks récupérés par le RAG :"]

                for i, r in enumerate(results, 1):
                    chunk = r.get("chunk", {}) or {}
                    meta = r.get("metadata", {}) or chunk.get("metadata", {}) or {}
                    content = str(chunk.get("content") or r.get("content") or "")
                    excerpt = content[:500].replace("\n", " ")

                    lines.append(
                        "\n"
                        f"[S{i}] score={r.get('final_score', r.get('score'))} | "
                        f"vector={r.get('score')} | "
                        f"bonus={r.get('metadata_bonus', 0)} | "
                        f"file={meta.get('file_name', '')}\n"
                        f"{excerpt}..."
                    )

                answer = "\n".join(lines)

            sources = []

            for i, r in enumerate(results, 1):
                chunk = r.get("chunk", {}) or {}
                meta = r.get("metadata", {}) or chunk.get("metadata", {}) or {}

                sources.append(
                    {
                        "ref": f"S{i}",
                        "chunk_id": r.get("chunk_id") or chunk.get("chunk_id") or meta.get("chunk_id"),
                        "file_name": meta.get("file_name", ""),
                        "score": r.get("final_score", r.get("score")),
                        "vector_score": r.get("score"),
                        "metadata_bonus": r.get("metadata_bonus", 0),
                    }
                )

            return EnnoAmelResponse(
                answer=answer,
                intent=decision.intent.value,
                recommended_agent=decision.recommended_agent.value,
                action="debug_retrieval_sources",
                confidence=decision.confidence,
                sources=sources,
                needs_specialized_agent=False,
                rag_used=True,
                chunks_used=len(results),
                route_explanation=decision.explanation,
                processing_time=time.time() - t0,
                debug={
                    "decision": decision.to_dict(),
                    "raw_results": results,
                    "state": self.state.to_dict(),
                } if include_debug else {},
            )

        except Exception as exc:
            return EnnoAmelResponse(
                answer="Erreur pendant la recherche RAG debug.",
                intent=decision.intent.value,
                recommended_agent=decision.recommended_agent.value,
                action="debug_search_error",
                confidence=decision.confidence,
                route_explanation=decision.explanation,
                processing_time=time.time() - t0,
                error=str(exc),
            )

    # ──────────────────────────────────────────────────────────────────────
    # INTERNAL PIPELINE CALLS
    # ──────────────────────────────────────────────────────────────────────

    def _run_extraction(
        self,
        file_path: Path,
        *,
        vision_mode: str,
        source_tag: str,
        enable_formulas: bool = True,
    ) -> Any:
        """
        Appelle modules.extraction.router.extract().
        """
        from modules.extraction.router import extract
        from modules.extraction.base import SourceTag

        try:
            source_tag_obj = SourceTag(source_tag)
        except Exception:
            source_tag_obj = SourceTag.DE_DOC

        kwargs = {
            "file_path": str(file_path),
            "source_tag": source_tag_obj,
            "vision_mode": vision_mode,
            "enable_formulas": enable_formulas,
        }

        supported = set(inspect.signature(extract).parameters.keys())
        kwargs = {k: v for k, v in kwargs.items() if k in supported}

        return extract(**kwargs)

    def _run_nlp(
        self,
        extraction_result: Any,
        *,
        use_gliner: bool,
        use_regex: bool,
        use_llm_extractor: bool,
        llm_extractor_model: str,
        ner_on_visual_chunks: bool,
        include_debug: bool,
        organisme_name: Optional[str] = None,
        organisme_id: Optional[str] = None,
        file_hash: Optional[str] = None,
        document_id: Optional[str] = None,
    ) -> tuple[Any, dict[str, Any]]:
        """
        Appelle modules.NLP.router.process_extraction() + to_json().

        Compatible ancien NLP + nouveau NLP V7 : on filtre automatiquement
        les paramètres selon la signature réelle de NLPConfig.
        """
        from modules.NLP.router import NLPConfig, process_extraction, to_json

        try:
            params = inspect.signature(NLPConfig).parameters
        except Exception:
            params = {}

        candidate_kwargs = {
            # Ancienne config / compatibilité
            "use_gliner": use_gliner,
            "use_spacy": False,
            "use_regex": use_regex,
            "use_llm_refiner": False,
            "use_llm_extractor": use_llm_extractor,
            "llm_extractor_model": llm_extractor_model,
            "ner_on_visual_chunks": ner_on_visual_chunks,
            "terminology_text_only": True,
            "include_debug": include_debug,
            "organisme_name": organisme_name,
            "organisme_id": organisme_id,
            "file_hash": file_hash,
            "document_id": document_id,

            # Nouveau NLP V7.1+
            "use_document_structure_mapper": True,
            "use_section_extractor": True,
            "use_role_postprocessor": True,
            "use_evidence_validator": True,
            "use_technical_terms_extractor": True,
            "use_quality_reporter": True,
            "use_domain_classifier": True,
            "use_synthesizer": True,
            "use_final_taxonomy_mapper": True,
            "use_evidence_mapper": True,
            "max_llm_passages": 24,

            # GLiNER V7
            "use_gliner_ner": use_gliner,
            "gliner_model": "urchade/gliner_multi-v2.1",
        }

        # Ne passer que les champs acceptés par NLPConfig.
        if params:
            filtered_kwargs = {
                key: value
                for key, value in candidate_kwargs.items()
                if key in params
            }
        else:
            filtered_kwargs = candidate_kwargs

        try:
            config = NLPConfig(**filtered_kwargs)
        except TypeError as exc:
            forbidden = {
                "ner_on_visual_chunks",
                "terminology_text_only",
                "use_spacy",
                "use_regex",
                "use_gliner",
                "use_llm_refiner",
                "file_hash",
                "document_id",
            }
            fallback_kwargs = {
                key: value
                for key, value in filtered_kwargs.items()
                if key not in forbidden
            }
            logger.warning(
                "NLPConfig fallback après erreur %s. kwargs=%s",
                exc,
                sorted(fallback_kwargs.keys()),
            )
            config = NLPConfig(**fallback_kwargs)

        nlp_result = process_extraction(extraction_result, config)
        nlp_json = to_json(nlp_result)

        # Forcer l'identité documentaire même si le router ne la gère pas.
        nlp_json = _apply_document_identity_to_nlp_json(
            nlp_json,
            organisme_name=organisme_name,
            organisme_id=organisme_id,
            file_hash=file_hash,
            document_id=document_id,
        )

        return nlp_result, nlp_json

    def _answer_pipeline_status(
        self,
        decision: IntentDecision,
        include_debug: bool,
        start_time: float,
    ) -> EnnoAmelResponse:
        """
        Réponse directe pour questions sur extraction/NLP.
        """
        last = self.state.last_preparation

        if not last:
            answer = (
                "Aucun document n'a encore été préparé. "
                "Lance d'abord `prepare_document()`."
            )
        else:
            answer = (
                "État du pipeline EnnoAmel :\n\n"
                f"- Document : {self.state.current_file_name or 'inconnu'}\n"
                f"- Extraction : {'OK' if last.extraction_done else 'non lancée / JSON NLP direct'}\n"
                f"- NLP : {'OK' if last.nlp_done else 'non'}\n"
                f"- RAG : {'OK' if last.rag_done else 'non'}\n"
                f"- Chunks indexés : {last.indexed_chunks}\n"
                f"- Temps préparation : {last.processing_time:.2f}s\n"
            )

            if last.extraction_errors:
                answer += "\nErreurs / warnings :\n"
                for err in last.extraction_errors[:5]:
                    answer += f"- {err}\n"

            if last.warnings:
                answer += "\nWarnings :\n"
                for warn in last.warnings[:5]:
                    answer += f"- {warn}\n"

        return EnnoAmelResponse(
            answer=answer,
            intent=decision.intent.value,
            recommended_agent=decision.recommended_agent.value,
            action=decision.action,
            confidence=decision.confidence,
            sources=[],
            needs_specialized_agent=False,
            rag_used=False,
            chunks_used=0,
            route_explanation=decision.explanation,
            processing_time=time.time() - start_time,
            debug={
                "decision": decision.to_dict(),
                "state": self.state.to_dict(),
            } if include_debug else {},
        )

    # ──────────────────────────────────────────────────────────────────────
    # STATE / ADMIN
    # ──────────────────────────────────────────────────────────────────────

    def list_documents(self, organisme_id: Optional[str] = None) -> list[dict[str, Any]]:
        """
        Liste les documents uniques déjà présents dans la base RAG/ChromaDB.
        """
        org_id = organisme_id or self.state.current_organisme_id

        if not hasattr(self.rag, "list_documents"):
            return []

        return self.rag.list_documents(organisme_id=org_id)

    def use_existing_document(
        self,
        *,
        organisme_id: str,
        organisme_name: Optional[str] = None,
        file_name: Optional[str] = None,
        file_hash: Optional[str] = None,
        document_id: Optional[str] = None,
    ) -> PreparationReport:
        """
        Sélectionne un document déjà indexé pour ouvrir le chat directement.

        Aucun traitement lourd n'est lancé :
          - pas d'extraction ;
          - pas de NLP ;
          - pas d'ingestion RAG.
        """
        t0 = time.time()
        org_id = _slugify(organisme_id or "organisme_inconnu")
        org_name = re.sub(r"\s+", " ", str(organisme_name or org_id)).strip()
        fh = str(file_hash or "").strip() or None
        doc_id = str(document_id or "").strip() or None

        exists = False
        chunks_count = 0

        if hasattr(self.rag, "document_exists"):
            exists = self.rag.document_exists(
                organisme_id=org_id,
                file_hash=fh,
                document_id=doc_id,
            )

        if exists and hasattr(self.rag, "count_document_chunks"):
            chunks_count = self.rag.count_document_chunks(
                organisme_id=org_id,
                file_hash=fh,
                document_id=doc_id,
            )

        rich_metadata = self._get_document_metadata_from_rag(
            organisme_id=org_id,
            file_hash=fh,
            document_id=doc_id,
        ) if exists else {}

        report = PreparationReport(
            ok=bool(exists),
            file_path=None,
            file_name=file_name or (rich_metadata.get("file_name") if rich_metadata else None) or "Document indexé",
            mode="existing_document_selected" if exists else "existing_document_missing",
            indexed_chunks=chunks_count,
            already_indexed=bool(exists),
            file_hash=fh,
            document_id=doc_id,
            organisme_name=org_name,
            organisme_id=org_id,
            extraction_done=False,
            nlp_done=False,
            rag_done=bool(exists),
            processing_time=time.time() - t0,
            document_metadata={
                "file_name": file_name or "Document indexé",
                "organisme_name": org_name,
                "organisme_id": org_id,
                "file_hash": fh,
                "document_id": doc_id,
                "already_indexed": bool(exists),
                **(rich_metadata or {}),
            },
        )

        if not exists:
            report.warnings.append("Document introuvable dans la base RAG.")

        if exists:
            self.state.has_document = True
            self.state.has_rag_index = True
            self.state.current_file_path = None
            self.state.current_file_name = file_name or "Document indexé"
            self.state.current_organisme_name = org_name
            self.state.current_organisme_id = org_id
            self.state.current_file_hash = fh
            self.state.current_document_id = doc_id
            self.state.current_document_metadata = report.document_metadata or {}
            self.state.indexed_chunks = int(getattr(self.rag, "total_chunks", chunks_count))

        self.state.last_preparation = report
        return report

    def get_status(self) -> dict[str, Any]:
        """
        État courant de l'orchestrateur.
        """
        status = self.state.to_dict()

        try:
            status["rag_stats"] = self.rag.stats()
        except Exception:
            status["rag_stats"] = {
                "total_chunks": getattr(self.rag, "total_chunks", None)
            }

        return status

    def reset(
        self,
        *,
        clear_rag: bool = False,
        delete_rag_files: bool = False,
    ) -> None:
        """
        Reset l'état EnnoAmel.

        clear_rag=True :
          vide aussi l'index ChromaDB.

        delete_rag_files=True :
          supprime les fichiers Chroma locaux.
        """
        self.state = EnnoAmelState()

        if clear_rag:
            self.rag.clear(delete_files=delete_rag_files)

    def save_current_nlp_json(self, output_path: str | Path) -> bool:
        """
        Sauvegarde le JSON NLP courant.
        """
        if not self.state.nlp_json:
            return False

        _save_json_file(self.state.nlp_json, Path(output_path))
        return True


# ══════════════════════════════════════════════════════════════════════════════
# TEST LOCAL RAPIDE
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(name)s | %(message)s",
    )

    parser = argparse.ArgumentParser(description="Test EnnoAmel orchestrateur")
    parser.add_argument("file", help="Chemin document brut ou .nlp.json")
    parser.add_argument(
        "--question",
        default="Donne-moi une idée générale du projet avec les verrous principaux.",
    )
    parser.add_argument("--cuda", action="store_true")
    parser.add_argument("--fast-vision", action="store_true")
    parser.add_argument("--clear", action="store_true")

    args = parser.parse_args()

    amel = EnnoAmelOrchestrator(
        embedding_device="cuda" if args.cuda else "cpu",
        llm_model=DEFAULT_LLM_MODEL,
        chat_model=DEFAULT_CHAT_MODEL,
    )

    report = amel.prepare_document(
        args.file,
        vision_mode="fast" if args.fast_vision else "full",
        clear_previous_index=args.clear,
        include_debug=False,
    )

    print("\n── PREPARATION ─────────────────────────")
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))

    print("\n── QUESTION ────────────────────────────")
    print(args.question)

    response = amel.ask(args.question, include_debug=False)

    print("\n── REPONSE ─────────────────────────────")
    print(response.answer)

    print("\n── SOURCES ─────────────────────────────")
    print(json.dumps(response.sources, ensure_ascii=False, indent=2))

    print("\n── STATUS ──────────────────────────────")
    print(json.dumps(amel.get_status(), ensure_ascii=False, indent=2))

# Compatibilité nouveau nom interface
Orchestrator = EnnoAmelOrchestrator
OrchestratorResponse = EnnoAmelResponse
