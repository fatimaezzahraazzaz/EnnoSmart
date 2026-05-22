"""
modules/RAG/rag_pipeline.py — EnnoSmart RAG v2 structured ChromaDB + EnnoAmel POC
──────────────────────────────────────────────────────────────────────────────
Pipeline RAG complet :
  - ingestion de JSON NLP enrichis ;
  - construction de chunks RAG structurés :
      1) sections réelles du document
      2) field cards : objectifs, verrous, état de l'art, méthodes, résultats
      3) entity cards : technologies, matériaux, personnes, organismes, métriques
      4) raw chunks optionnels
  - stockage ChromaDB ;
  - recherche metadata-aware ;
  - réponse via QueryEngine ;
  - compatible avec EnnoAmel orchestrateur.

Architecture :
  Extraction → NLP router → JSON NLP final → RAG → EnnoAmel

Entrée :
  JSON produit par modules.NLP.router.to_json()

Sortie :
  RAGResponse avec :
    - answer
    - sources
    - intent
    - recommended_agent
    - chunks_used
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ChromaDB local
DEFAULT_INDEX_DIR = Path("modules/RAG/.chroma")

# Cache embeddings
DEFAULT_CACHE_DIR = Path("modules/RAG/.cache")

# Recommandé pour EnnoSmart/CIR : FR + EN + technique + multi-domaines
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-m3"

# LLM local recommandé pour le POC
DEFAULT_LLM_MODEL = "ollama:mistral:7b-instruct"


# ════════════════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════════════════

def slugify_org(name: str) -> str:
    """
    Transforme le nom saisi dans le frontend en organisme_id stable.

    Exemple :
      "ABINNOV" → "abinnov"
      "GIRODIN SAUER" → "girodin_sauer"
    """
    text = unicodedata.normalize("NFKD", str(name or "").lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "organisme_inconnu"


def _clean_metadata_str(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text if text else default


def _clean_text(value: Any) -> str:
    text = str(value or "").replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [value]


def _flatten_text_items(value: Any) -> list[str]:
    """
    Transforme une valeur JSON quelconque en liste de textes propres.
    Gère strings, listes, dicts, objets simples.
    """
    out: list[str] = []

    for item in _as_list(value):
        if item is None:
            continue

        if isinstance(item, str):
            txt = _clean_text(item)
            if txt:
                out.append(txt)
            continue

        if isinstance(item, dict):
            preferred_keys = [
                "resume",
                "phrase",
                "phrase_source",
                "text",
                "label",
                "name",
                "titre",
                "title",
                "numero",
                "date",
                "inventeurs",
                "deposant",
            ]

            parts = []
            for key in preferred_keys:
                if key in item:
                    val = item.get(key)
                    if isinstance(val, list):
                        parts.extend(_flatten_text_items(val))
                    else:
                        txt = _clean_text(val)
                        if txt:
                            parts.append(txt)

            if parts:
                out.append(" | ".join(parts))
            else:
                txt = _clean_text(json.dumps(item, ensure_ascii=False))
                if txt:
                    out.append(txt)
            continue

        txt = _clean_text(item)
        if txt:
            out.append(txt)

    seen = set()
    deduped = []
    for x in out:
        key = x.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(x)

    return deduped


def _safe_doc_meta(nlp_json: dict[str, Any]) -> dict[str, Any]:
    doc_meta = nlp_json.setdefault("document_metadata", {})
    if not isinstance(doc_meta, dict):
        doc_meta = {}
        nlp_json["document_metadata"] = doc_meta
    return doc_meta


def force_organisme_metadata(
    nlp_json: dict[str, Any],
    organisme_name: str,
    organisme_id: Optional[str] = None,
    file_hash: str = "",
    document_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    Force organisme_name / organisme_id dans document_metadata et dans tous les chunks.

    Pourquoi :
      Le NLP peut deviner l'organisme depuis le nom du fichier.
      Mais dans ton application, l'organisme vient du champ de saisie frontend.
      Cette fonction rend ce champ prioritaire et évite de mélanger les clients.
    """
    if not isinstance(nlp_json, dict):
        raise TypeError("force_organisme_metadata() attend un dict NLP JSON.")

    org_name = _clean_metadata_str(organisme_name, "Organisme inconnu")
    org_id = _clean_metadata_str(organisme_id, slugify_org(org_name))
    file_hash = _clean_metadata_str(file_hash, "")

    doc_meta = _safe_doc_meta(nlp_json)
    doc_meta["organisme_name"] = org_name
    doc_meta["organisme_id"] = org_id

    if file_hash:
        doc_meta["file_hash"] = file_hash

    final_file_hash = _clean_metadata_str(doc_meta.get("file_hash"), file_hash)
    file_name = _clean_metadata_str(doc_meta.get("file_name"), "document")

    if document_id:
        final_document_id = str(document_id).strip()
    elif final_file_hash:
        final_document_id = f"{org_id}_{final_file_hash[:16]}"
    else:
        final_document_id = f"{org_id}_{slugify_org(file_name)}"

    doc_meta["document_id"] = final_document_id
    doc_meta["file_hash"] = final_file_hash

    for chunk in nlp_json.get("chunks", []) or []:
        if not isinstance(chunk, dict):
            continue

        meta = chunk.setdefault("metadata", {})
        if not isinstance(meta, dict):
            meta = {}
            chunk["metadata"] = meta

        meta["organisme_name"] = org_name
        meta["organisme_id"] = org_id
        meta["document_id"] = final_document_id
        meta["file_hash"] = final_file_hash

        chunk["organisme_name"] = org_name
        chunk["organisme_id"] = org_id
        chunk["document_id"] = final_document_id
        chunk["file_hash"] = final_file_hash

    return nlp_json


def build_organisme_filter(
    organisme_name: Optional[str] = None,
    organisme_id: Optional[str] = None,
    document_id: Optional[str] = None,
    file_hash: Optional[str] = None,
) -> dict[str, str]:
    """
    Construit un filtre RAG robuste depuis le frontend.
    organisme_id est prioritaire. Si absent, il est calculé depuis organisme_name.
    """
    org_id = _clean_metadata_str(organisme_id, "")
    if not org_id and organisme_name:
        org_id = slugify_org(organisme_name)

    if not org_id:
        raise ValueError("organisme_id ou organisme_name est obligatoire pour filtrer le RAG.")

    f: dict[str, str] = {"organisme_id": org_id}

    if document_id:
        f["document_id"] = str(document_id).strip()

    if file_hash:
        f["file_hash"] = str(file_hash).strip()

    return f


# ════════════════════════════════════════════════════════════════════════════
# RAG V2 STRUCTURED CHUNKS
# ════════════════════════════════════════════════════════════════════════════

def build_rag_chunks_from_nlp_json(
    nlp_json: dict[str, Any],
    include_raw_chunks: bool = False,
) -> list[dict]:
    """
    Construit des chunks RAG structurés depuis le JSON NLP final.

    Paramètres :
      nlp_json           : JSON produit par le router NLP.
      include_raw_chunks : si True, ajoute aussi les chunks bruts NLP.
                           si False, garde seulement section / field_card / entity_card.
                           si aucune structure n'existe, les chunks bruts sont utilisés en fallback.

    Types de chunks créés :
      - section     : sections réelles du document
      - field_card  : champs R&D structurés
      - entity_card : entités / mots-clés / technologies / personnes / organismes
      - raw_chunk   : optionnel ou fallback

    Objectif :
      éviter que le RAG dépende uniquement des chunks bruts ou de métadonnées
      globales bruitées.
    """
    if not isinstance(nlp_json, dict):
        return []

    doc_meta = _safe_doc_meta(nlp_json)

    file_name = _clean_metadata_str(doc_meta.get("file_name"), "document")
    document_id = _clean_metadata_str(doc_meta.get("document_id"), file_name)
    organisme_name = _clean_metadata_str(doc_meta.get("organisme_name"), "Organisme inconnu")
    organisme_id = _clean_metadata_str(doc_meta.get("organisme_id"), "organisme_inconnu")
    file_hash = _clean_metadata_str(doc_meta.get("file_hash"), "")
    title = _clean_metadata_str(doc_meta.get("title"), "")

    chunks: list[dict] = []

    def base_meta(extra: Optional[dict] = None) -> dict:
        meta = {
            "file_name": file_name,
            "file_category": doc_meta.get("file_category", ""),
            "source_tag": doc_meta.get("source_tag", ""),
            "organisme_name": organisme_name,
            "organisme_id": organisme_id,
            "file_hash": file_hash,
            "document_id": document_id,
            "title": title,
            "domaine_principal": doc_meta.get("domaine_principal", ""),
            "domaine_applicatif": doc_meta.get("domaine_applicatif", ""),
            "domaine_scientifique_detaille": doc_meta.get("domaine_scientifique_detaille", ""),
        }
        if extra:
            meta.update(extra)
        return meta

    def add_chunk(
        chunk_id: str,
        content: str,
        source_type: str,
        meta_extra: Optional[dict] = None,
        index: Optional[int] = None,
    ) -> None:
        content = _clean_text(content)
        if not content:
            return

        meta = base_meta(
            {
                "chunk_source_type": source_type,
                "source_type": source_type,
                **(meta_extra or {}),
            }
        )

        idx = len(chunks) if index is None else index

        chunks.append(
            {
                "chunk_id": chunk_id,
                "index": idx,
                "content": content,
                "source": source_type,
                "organisme_name": organisme_name,
                "organisme_id": organisme_id,
                "document_id": document_id,
                "file_hash": file_hash,
                "metadata": meta,
            }
        )

    # 1) Sections réelles du document
    structure = doc_meta.get("document_structure", {}) or {}
    sections = structure.get("sections", []) if isinstance(structure, dict) else []

    for i, sec in enumerate(sections):
        if not isinstance(sec, dict):
            continue

        section_id = _clean_metadata_str(sec.get("section_id"), f"{document_id}_section_{i:04d}")
        sec_title = _clean_text(sec.get("title"))
        sec_role = _clean_text(sec.get("role"))
        sec_content = _clean_text(sec.get("content") or sec.get("text"))

        if sec_title and sec_content:
            content = f"{sec_title}\n\n{sec_content}"
        else:
            content = sec_title or sec_content

        add_chunk(
            chunk_id=f"{document_id}_section_{i:04d}",
            content=content,
            source_type="section",
            meta_extra={
                "section_id": section_id,
                "section_title": sec_title,
                "section_role": sec_role,
                "source_chunk_indexes": sec.get("source_chunk_indexes", []),
                "start_line": sec.get("start_line"),
                "end_line": sec.get("end_line"),
            },
        )

    # 2) Field cards R&D
    field_map = {
        "objet_recherche": "Objet de recherche",
        "objectifs_rd": "Objectifs R&D",
        "verrous_techniques": "Verrous techniques",
        "etat_art": "État de l'art",
        "methodes_rd": "Méthodes / démarche R&D",
        "resultats_rd": "Résultats R&D",
        "limitations_perspectives": "Limites / perspectives",
        "brevets": "Brevets",
    }

    for field, label in field_map.items():
        values = _flatten_text_items(doc_meta.get(field))
        if not values:
            continue

        content = f"{label}\n" + "\n".join(f"- {x}" for x in values)

        add_chunk(
            chunk_id=f"{document_id}_field_{field}",
            content=content,
            source_type="field_card",
            meta_extra={
                "field_name": field,
                "field_label": label,
                "section_role": field,
            },
        )

    # 3) Entity cards
    entity_map = {
        "mots_cles_projet": "Mots-clés projet",
        "technologies": "Technologies",
        "materiaux_composants": "Matériaux / composants / éléments techniques",
        "equipements": "Équipements",
        "metriques_evaluation": "Métriques / paramètres",
        "metriques": "Métriques / paramètres",
        "normes_techniques": "Normes / standards",
        "normes": "Normes / standards",
        "personnes": "Personnes détectées",
        "organismes": "Organismes détectés",
        "partenaires_rd": "Partenaires R&D",
    }

    for field, label in entity_map.items():
        raw = doc_meta.get(field)

        if field == "mots_cles_projet" and isinstance(raw, dict):
            values = []
            values.extend(_flatten_text_items(raw.get("high_confidence")))
            values.extend(_flatten_text_items(raw.get("candidates")))
        else:
            values = _flatten_text_items(raw)

        if not values:
            continue

        content = f"{label}\n" + "\n".join(f"- {x}" for x in values)

        add_chunk(
            chunk_id=f"{document_id}_entity_{field}",
            content=content,
            source_type="entity_card",
            meta_extra={
                "field_name": field,
                "field_label": label,
                "entity_type": field,
                "extraction_status": (
                    "automatic_to_verify"
                    if field in {"personnes", "organismes", "materiaux_composants"}
                    else "automatic"
                ),
            },
        )

    # 4) Chunks bruts optionnels
    # include_raw_chunks=False : on garde seulement section / field_card / entity_card
    # include_raw_chunks=True  : on ajoute aussi les chunks bruts NLP
    # si aucune structure n'existe, on utilise les chunks bruts en fallback
    if include_raw_chunks or not chunks:
        for i, chunk in enumerate(nlp_json.get("chunks", []) or []):
            if not isinstance(chunk, dict):
                continue

            content = _clean_text(chunk.get("content"))
            if not content:
                continue

            raw_meta = dict(chunk.get("metadata", {}) or {})
            add_chunk(
                chunk_id=_clean_metadata_str(
                    chunk.get("chunk_id") or raw_meta.get("chunk_id"),
                    f"{document_id}_raw_{i:04d}",
                ),
                content=content,
                source_type="raw_chunk",
                meta_extra={
                    "chunk_index": i,
                    **raw_meta,
                },
                index=i,
            )

    return chunks


# ════════════════════════════════════════════════════════════════════════════
# PIPELINE
# ════════════════════════════════════════════════════════════════════════════

class RAGPipeline:
    """
    Pipeline RAG complet pour EnnoSmart.

    Rôle :
      - recevoir le JSON NLP enrichi ;
      - construire des chunks structurés ;
      - embedder les chunks ;
      - les stocker dans ChromaDB ;
      - rechercher les sources pertinentes ;
      - générer une réponse sourcée via QueryEngine.

    Ce pipeline ne remplace pas les agents.
    Il sert de mémoire documentaire à EnnoAmel.
    """

    def __init__(
        self,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        llm_model: str = DEFAULT_LLM_MODEL,
        index_dir: Optional[Path] = None,
        cache_dir: Optional[Path] = None,
        top_k: int = 5,
        min_score: float = -1.0,
        embedding_device: str = "cpu",
        auto_load: bool = True,
    ):
        from modules.RAG.embedder import Embedder
        from modules.RAG.vector_store import VectorStore
        from modules.RAG.retriever import Retriever
        from modules.RAG.query_engine import QueryEngine

        self.embedder = Embedder(
            model_name=embedding_model,
            device=embedding_device,
            cache=True,
            cache_dir=cache_dir or DEFAULT_CACHE_DIR,
            normalize_embeddings=True,
        )

        self.store = VectorStore(
            path=index_dir or DEFAULT_INDEX_DIR,
        )

        self.retriever = Retriever(
            store=self.store,
            embedder=self.embedder,
            top_k=top_k,
            min_score=min_score,
        )

        self.engine = QueryEngine(
            retriever=self.retriever,
            model=llm_model,
            top_k=top_k,
            min_score=min_score,
        )

        self.embedding_model = embedding_model
        self.embedding_device = embedding_device
        self.llm_model = llm_model
        self.top_k = top_k
        self.min_score = min_score

        if auto_load:
            self.store.load()

        logger.info(
            "RAGPipeline prêt | vector_store=ChromaDB | embedding=%s | device=%s | llm=%s | chunks_indexés=%d",
            embedding_model,
            embedding_device,
            llm_model,
            self.store.total_chunks,
        )

    # ──────────────────────────────────────────────────────────────────────
    # INGESTION
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _merge_document_metadata_into_chunks(nlp_json: dict[str, Any]) -> list[dict]:
        """
        Compatibilité ancien comportement.

        V2 :
          on construit d'abord des chunks structurés avec
          build_rag_chunks_from_nlp_json().

        Pourquoi :
          les chunks structurés évitent de répéter les metadata globales bruitées
          partout et permettent d'afficher :
            - sections exactes
            - field cards R&D
            - entity cards
        """
        return build_rag_chunks_from_nlp_json(
            nlp_json,
            include_raw_chunks=False,
        )

    def ingest(
        self,
        nlp_json: dict,
        save: bool = True,
        organisme_name: Optional[str] = None,
        organisme_id: Optional[str] = None,
        file_hash: str = "",
        document_id: Optional[str] = None,
        include_raw_chunks: bool = False,
    ) -> int:
        """
        Indexe les chunks d'un document NLP.

        Paramètres :
          nlp_json           : sortie de router.to_json(nlp_result)
          organisme_name     : nom saisi dans le frontend, ex. "ABINNOV"
          organisme_id       : id stable optionnel. Si absent, calculé depuis organisme_name.
          file_hash          : hash du fichier, recommandé pour éviter les doublons.
          document_id        : id logique optionnel.
          include_raw_chunks : si True, ajoute aussi les chunks bruts NLP dans l'index.

        Important :
          Si organisme_name/organisme_id est fourni, il est forcé dans le JSON
          avant l'indexation. C'est le comportement recommandé avec ton frontend.
        """
        if not isinstance(nlp_json, dict):
            raise TypeError("ingest() attend un dict JSON NLP.")

        if organisme_name or organisme_id:
            nlp_json = force_organisme_metadata(
                nlp_json=nlp_json,
                organisme_name=organisme_name or organisme_id or "Organisme inconnu",
                organisme_id=organisme_id,
                file_hash=file_hash,
                document_id=document_id,
            )

        chunks = build_rag_chunks_from_nlp_json(
            nlp_json,
            include_raw_chunks=include_raw_chunks,
        )

        if not chunks:
            logger.warning("ingest() : aucun chunk valide dans le JSON NLP.")
            return 0

        vectors, embedded_chunks = self.embedder.embed_chunks(chunks)

        if vectors is None or vectors.shape[0] == 0:
            logger.warning("ingest() : embedding a produit 0 vecteurs.")
            return 0

        n = self.store.add(vectors, embedded_chunks)

        if save:
            self.store.save()

        doc_meta = nlp_json.get("document_metadata", {}) or {}
        file_name = doc_meta.get("file_name", "inconnu")
        org_id = doc_meta.get("organisme_id", "organisme_inconnu")
        fh = doc_meta.get("file_hash", "")

        logger.info(
            "ingest() : '%s' | organisme=%s | hash=%s → %d chunks indexés | total store=%d",
            file_name,
            org_id,
            str(fh)[:12] if fh else "",
            n,
            self.store.total_chunks,
        )

        return n

    def ingest_file(
        self,
        json_path: str | Path,
        save: bool = True,
        organisme_name: Optional[str] = None,
        organisme_id: Optional[str] = None,
        include_raw_chunks: bool = False,
    ) -> int:
        """
        Charge et indexe un fichier .nlp.json.
        """
        path = Path(json_path)

        if not path.exists():
            raise FileNotFoundError(f"Fichier JSON introuvable : {path}")

        with open(path, "r", encoding="utf-8") as f:
            nlp_json = json.load(f)

        return self.ingest(
            nlp_json,
            save=save,
            organisme_name=organisme_name,
            organisme_id=organisme_id,
            include_raw_chunks=include_raw_chunks,
        )

    def ingest_batch(self, nlp_jsons: list[dict], save: bool = True) -> dict[str, int]:
        """
        Indexe plusieurs documents NLP d'un coup.

        Retour :
          {file_name: n_chunks_indexés}
        """
        results: dict[str, int] = {}

        for nlp_json in nlp_jsons:
            doc_meta = nlp_json.get("document_metadata", {}) or {}
            file_name = doc_meta.get("file_name", "inconnu")
            n = self.ingest(nlp_json, save=False)
            results[file_name] = n

        if save:
            self.store.save()

        logger.info(
            "ingest_batch() : %d documents indexés | total store=%d",
            len(nlp_jsons),
            self.store.total_chunks,
        )

        return results

    def ingest_folder(
        self,
        folder: str | Path,
        pattern: str = "**/*.nlp.json",
        save: bool = True,
    ) -> dict[str, int]:
        """
        Indexe tous les fichiers .nlp.json d'un dossier.
        """
        folder_path = Path(folder)

        if not folder_path.exists():
            raise FileNotFoundError(f"Dossier introuvable : {folder_path}")

        results: dict[str, int] = {}

        for json_file in folder_path.glob(pattern):
            try:
                n = self.ingest_file(json_file, save=False)
                results[str(json_file)] = n
            except Exception as exc:
                logger.warning("Erreur ingestion %s : %s", json_file, exc)
                results[str(json_file)] = 0

        if save:
            self.store.save()

        return results

    # ──────────────────────────────────────────────────────────────────────
    # RECHERCHE / QUESTION
    # ──────────────────────────────────────────────────────────────────────

    def ask(
        self,
        question: str,
        filter_meta: Optional[dict] = None,
        top_k: Optional[int] = None,
        intent: str = "qa",
        recommended_agent: Optional[str] = "EnnoAmel",
        organisme_name: Optional[str] = None,
        organisme_id: Optional[str] = None,
        document_id: Optional[str] = None,
        file_hash: Optional[str] = None,
    ):
        """
        Répond à une question en langage naturel.

        Tu peux soit passer filter_meta directement,
        soit passer organisme_name/organisme_id pour filtrer automatiquement.
        """
        if filter_meta is None and (organisme_name or organisme_id):
            filter_meta = build_organisme_filter(
                organisme_name=organisme_name,
                organisme_id=organisme_id,
                document_id=document_id,
                file_hash=file_hash,
            )

        return self.engine.ask(
            question=question,
            filter_meta=filter_meta,
            top_k=top_k,
            intent=intent,
            recommended_agent=recommended_agent,
        )

    def search(
        self,
        question: str,
        top_k: Optional[int] = None,
        filter_meta: Optional[dict] = None,
        intent: str = "qa",
        organisme_name: Optional[str] = None,
        organisme_id: Optional[str] = None,
        document_id: Optional[str] = None,
        file_hash: Optional[str] = None,
    ) -> list[dict]:
        """
        Recherche les chunks pertinents sans appeler le LLM.
        """
        if filter_meta is None and (organisme_name or organisme_id):
            filter_meta = build_organisme_filter(
                organisme_name=organisme_name,
                organisme_id=organisme_id,
                document_id=document_id,
                file_hash=file_hash,
            )

        return self.retriever.search(
            query=question,
            top_k=top_k or self.top_k,
            min_score=self.min_score,
            filter_meta=filter_meta,
            intent=intent,
        )

    def search_multi(
        self,
        queries: list[str],
        top_k: Optional[int] = None,
        filter_meta: Optional[dict] = None,
        intent: str = "qa",
    ) -> list[dict]:
        """
        Recherche multi-requêtes.
        """
        return self.retriever.search_multi(
            queries=queries,
            top_k=top_k or self.top_k,
            min_score=self.min_score,
            filter_meta=filter_meta,
            intent=intent,
        )

    # ──────────────────────────────────────────────────────────────────────
    # GESTION INDEX
    # ──────────────────────────────────────────────────────────────────────

    def document_exists(
        self,
        *,
        organisme_id: str,
        file_hash: Optional[str] = None,
        document_id: Optional[str] = None,
    ) -> bool:
        """Vérifie dans ChromaDB si un document est déjà indexé."""
        if not hasattr(self.store, "document_exists"):
            return False
        return self.store.document_exists(
            organisme_id=organisme_id,
            file_hash=file_hash,
            document_id=document_id,
        )

    def count_document_chunks(
        self,
        *,
        organisme_id: str,
        file_hash: Optional[str] = None,
        document_id: Optional[str] = None,
    ) -> int:
        """Nombre de chunks déjà indexés pour un document."""
        if not hasattr(self.store, "count_document_chunks"):
            return 0
        return self.store.count_document_chunks(
            organisme_id=organisme_id,
            file_hash=file_hash,
            document_id=document_id,
        )

    def list_documents(self, organisme_id: Optional[str] = None) -> list[dict[str, Any]]:
        """
        Liste les documents uniques réellement indexés dans ChromaDB.
        """
        if not hasattr(self.store, "list_documents"):
            return []
        return self.store.list_documents(organisme_id=organisme_id)

    def delete_document(
        self,
        file_name: Optional[str] = None,
        *,
        organisme_id: Optional[str] = None,
        file_hash: Optional[str] = None,
        document_id: Optional[str] = None,
    ) -> int:
        """
        Supprime tous les chunks d'un document de l'index.

        Compatibilité :
        - ancien usage : delete_document(file_name)
        - usage recommandé : delete_document(document_id=..., file_hash=..., organisme_id=...)
        """
        if not hasattr(self.store, "delete_document"):
            logger.warning("VectorStore ne supporte pas delete_document().")
            return 0

        n = self.store.delete_document(
            file_name=file_name,
            organisme_id=organisme_id,
            file_hash=file_hash,
            document_id=document_id,
        )

        if n > 0:
            self.store.save()

        return n

    def delete_organisme(self, organisme_id: str) -> int:
        """
        Supprime tous les chunks d'un organisme si VectorStore le supporte.
        """
        if hasattr(self.store, "delete_organisme"):
            n = self.store.delete_organisme(organisme_id)
            if n > 0:
                self.store.save()
            return n

        logger.warning("VectorStore ne supporte pas delete_organisme().")
        return 0

    def save(self) -> None:
        """
        Compatibilité.
        Avec ChromaDB, la persistance est automatique.
        """
        self.store.save()

    def load(self) -> bool:
        """
        Charge/init la base ChromaDB.
        """
        return self.store.load()

    def clear(self, delete_files: bool = False) -> None:
        """
        Vide le store.
        delete_files=True supprime aussi les fichiers Chroma locaux.
        """
        self.store.clear(delete_files=delete_files)

    # ──────────────────────────────────────────────────────────────────────
    # STATS
    # ──────────────────────────────────────────────────────────────────────

    @property
    def total_chunks(self) -> int:
        return self.store.total_chunks

    @property
    def is_ready(self) -> bool:
        return not self.store.is_empty

    def stats(self) -> dict:
        dims = None
        try:
            dims = self.embedder.dims if self.store.total_chunks > 0 else None
        except Exception:
            try:
                dims = self.embedder._load_model().get_embedding_dimension()
            except Exception:
                dims = None

        return {
            "vector_store": "ChromaDB",
            "total_chunks": self.store.total_chunks,
            "embedding_model": self.embedder.model_name,
            "embedding_device": getattr(self.embedder, "device", "unknown"),
            "embedding_dims": dims,
            "llm_model": self.llm_model,
            "top_k": self.top_k,
            "min_score": self.min_score,
            "index_ready": self.is_ready,
            "rag_version": "v2-structured-include-raw-fix",
        }
