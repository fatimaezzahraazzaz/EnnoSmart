"""
modules/orchestration/workflow.py — EnnoSmart / EnnoAmel POC
──────────────────────────────────────────────────────────────────────────────
Workflow technique utilisé par l'orchestrateur EnnoAmel.

Rôle :
  - Lancer l'extraction documentaire.
  - Lancer le pipeline NLP.
  - Convertir le résultat NLP en JSON RAG-ready.
  - Indexer le JSON NLP dans le RAG.
  - Préparer un document brut ou un JSON NLP existant.

Architecture :
  Document brut
      → extraction.router.extract()
      → ExtractionResult
      → NLP.router.process_extraction()
      → NLPResult
      → NLP.router.to_json()
      → RAGPipeline.ingest()

  JSON NLP existant
      → load JSON
      → RAGPipeline.ingest()

Ce fichier ne décide pas quel agent utiliser.
La décision reste dans :
  - intent_router.py
  - ennoamel.py
"""

from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Optional

from agents.orchestration.schemas import (
    WorkflowMode,
    PipelineStep,
    StepStatus,
    StepReport,
    WorkflowReport,
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# JSON HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def load_json(path: str | Path) -> dict[str, Any]:
    """
    Charge un fichier JSON UTF-8.
    """
    p = Path(path)

    if not p.exists():
        raise FileNotFoundError(f"Fichier JSON introuvable : {p}")

    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data: dict[str, Any], path: str | Path) -> Path:
    """
    Sauvegarde un dict en JSON UTF-8 indenté.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return p


def is_nlp_json(data: dict[str, Any]) -> bool:
    """
    Vérifie rapidement si un dict ressemble à un JSON NLP EnnoSmart.
    """
    if not isinstance(data, dict):
        return False

    return (
        "document_metadata" in data
        and "chunks" in data
        and isinstance(data.get("chunks"), list)
    )


def is_nlp_json_file(path: str | Path) -> bool:
    """
    Vérifie si un chemin correspond probablement à un JSON NLP.
    """
    p = Path(path)
    name = p.name.lower()

    return name.endswith(".nlp.json") or name.endswith(".json")


def default_nlp_json_path(file_path: str | Path) -> Path:
    """
    Génère le chemin de sortie JSON NLP par défaut.

    Exemple :
      document.docx → document.docx.nlp.json
    """
    p = Path(file_path)
    return p.with_suffix(p.suffix + ".nlp.json")


# ══════════════════════════════════════════════════════════════════════════════
# ORGANISME HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def clean_organisme_name(value: Optional[str]) -> str:
    """
    Nettoie le nom d'organisme affichable.

    Exemple :
      "  Infinergies SAS  " -> "Infinergies SAS"
    """
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text or "Organisme inconnu"


def slugify_organisme(value: Optional[str]) -> str:
    """
    Convertit un nom organisme en identifiant stable pour ChromaDB.

    Exemple :
      "AriMayi SAS" -> "arimayi_sas"
      "SCALIAN DS" -> "scalian_ds"
    """
    text = clean_organisme_name(value).lower()

    if text == "organisme inconnu":
        return "organisme_inconnu"

    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")

    return text or "organisme_inconnu"


def resolve_organisme_info(
    organisme_name: Optional[str] = None,
    organisme_id: Optional[str] = None,
    existing_metadata: Optional[dict[str, Any]] = None,
) -> tuple[str, str]:
    """
    Résout organisme_name / organisme_id avec priorité :

      1. valeurs passées par l'interface/orchestrateur ;
      2. valeurs déjà présentes dans document_metadata ;
      3. fallback "Organisme inconnu".
    """
    meta = existing_metadata or {}

    name = organisme_name or meta.get("organisme_name") or meta.get("organisme") or None
    oid = organisme_id or meta.get("organisme_id") or None

    name_clean = clean_organisme_name(name)
    oid_clean = str(oid or "").strip() or slugify_organisme(name_clean)

    return name_clean, oid_clean


def apply_organisme_to_nlp_json(
    nlp_json: dict[str, Any],
    *,
    organisme_name: Optional[str] = None,
    organisme_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    Injecte organisme_name / organisme_id dans document_metadata et chaque chunk.

    Utile pour :
      - JSON NLP déjà existant ;
      - JSON généré par un ancien router sans organisme ;
      - forcer l'organisme saisi dans l'interface client.
    """
    if not isinstance(nlp_json, dict):
        raise TypeError("nlp_json doit être un dict.")

    doc_meta = dict(nlp_json.get("document_metadata", {}) or {})
    name, oid = resolve_organisme_info(
        organisme_name=organisme_name,
        organisme_id=organisme_id,
        existing_metadata=doc_meta,
    )

    doc_meta["organisme_name"] = name
    doc_meta["organisme_id"] = oid

    # Compatibilité avec l'ancien champ "organismes".
    orgs = doc_meta.get("organismes", [])
    if not isinstance(orgs, list):
        orgs = [str(orgs)]
    if name not in orgs:
        orgs = [name] + orgs
    doc_meta["organismes"] = [o for i, o in enumerate(orgs) if o and o not in orgs[:i]]

    chunks = []
    for chunk in nlp_json.get("chunks", []) or []:
        if not isinstance(chunk, dict):
            continue
        c = dict(chunk)
        meta = dict(c.get("metadata", {}) or {})
        meta["organisme_name"] = name
        meta["organisme_id"] = oid
        c["metadata"] = meta
        chunks.append(c)

    out = dict(nlp_json)
    out["document_metadata"] = doc_meta
    out["chunks"] = chunks

    return out


# ══════════════════════════════════════════════════════════════════════════════
# STEP RUNNERS
# ══════════════════════════════════════════════════════════════════════════════

def run_extraction(
    file_path: str | Path,
    *,
    vision_mode: str = "full",
    source_tag: str = "DE_DOC",
) -> tuple[Any, StepReport]:
    """
    Lance l'extraction documentaire.

    Paramètres :
      file_path   : PDF/DOCX/PPTX/Excel/email/image.
      vision_mode : "full", "fast" ou "text_only".
      source_tag  : "DE_DOC", "NOTES" ou "ARCHIVE".

    Retour :
      extraction_result, StepReport
    """
    t0 = time.time()
    report = StepReport(
        step=PipelineStep.EXTRACTION,
        status=StepStatus.RUNNING,
        message="Extraction en cours.",
    )

    path = Path(file_path)

    try:
        if not path.exists():
            raise FileNotFoundError(f"Fichier introuvable : {path}")

        from modules.extraction.router import extract
        from modules.extraction.base import SourceTag

        try:
            source_tag_obj = SourceTag(source_tag)
        except Exception:
            source_tag_obj = SourceTag.DE_DOC

        extraction_result = extract(
            file_path=str(path),
            source_tag=source_tag_obj,
            vision_mode=vision_mode,
        )

        errors = list(getattr(extraction_result, "extraction_errors", []) or [])
        total_chunks = int(getattr(extraction_result, "total_chunks", 0) or 0)
        is_valid = bool(getattr(extraction_result, "is_valid", False))

        report.duration = time.time() - t0
        report.ok = is_valid
        report.status = StepStatus.OK if is_valid else StepStatus.WARNING
        report.message = (
            f"Extraction terminée : {total_chunks} chunks produits."
            if is_valid
            else "Extraction terminée, mais aucun chunk exploitable n'a été produit."
        )
        report.errors = errors
        report.metadata = {
            "file_name": getattr(extraction_result, "file_name", path.name),
            "file_category": str(getattr(extraction_result, "file_category", "")),
            "text_chunks": len(getattr(extraction_result, "text_chunks", []) or []),
            "visual_chunks": len(getattr(extraction_result, "visual_chunks", []) or []),
            "total_chunks": total_chunks,
            "confidence_score": getattr(extraction_result, "confidence_score", None),
            "tags": list(getattr(extraction_result, "tags", []) or []),
        }

        return extraction_result, report

    except Exception as exc:
        report.duration = time.time() - t0
        report.ok = False
        report.status = StepStatus.ERROR
        report.message = "Erreur pendant l'extraction."
        report.errors.append(str(exc))
        logger.error("run_extraction error: %s", exc, exc_info=True)
        return None, report


def run_nlp(
    extraction_result: Any,
    *,
    use_gliner: bool = True,
    use_regex: bool = True,
    use_llm_extractor: bool = True,
    llm_extractor_model: str = "ollama:mistral:7b-instruct",
    ner_on_visual_chunks: bool = False,
    include_debug: bool = False,
    organisme_name: Optional[str] = None,
    organisme_id: Optional[str] = None,
) -> tuple[Any, Optional[dict[str, Any]], StepReport]:
    """
    Lance le pipeline NLP sur un ExtractionResult.

    Retour :
      nlp_result, nlp_json, StepReport
    """
    t0 = time.time()
    report = StepReport(
        step=PipelineStep.NLP,
        status=StepStatus.RUNNING,
        message="NLP en cours.",
    )

    try:
        if extraction_result is None:
            raise ValueError("extraction_result est None.")

        if not bool(getattr(extraction_result, "is_valid", False)):
            raise ValueError("ExtractionResult invalide : aucun chunk exploitable.")

        from modules.NLP.router import NLPConfig, process_extraction, to_json

        config = NLPConfig(
            use_gliner=use_gliner,
            use_spacy=False,
            use_regex=use_regex,
            use_llm_refiner=False,
            use_llm_extractor=use_llm_extractor,
            llm_extractor_model=llm_extractor_model,
            ner_on_visual_chunks=ner_on_visual_chunks,
            terminology_text_only=True,
            include_debug=include_debug,
            organisme_name=organisme_name,
            organisme_id=organisme_id,
        )

        nlp_result = process_extraction(extraction_result, config)
        nlp_json = to_json(nlp_result)
        nlp_json = apply_organisme_to_nlp_json(
            nlp_json,
            organisme_name=organisme_name,
            organisme_id=organisme_id,
        )

        chunks = nlp_json.get("chunks", []) or []
        doc_meta = nlp_json.get("document_metadata", {}) or {}

        report.duration = time.time() - t0
        report.ok = True
        report.status = StepStatus.OK
        report.message = f"NLP terminé : {len(chunks)} chunks enrichis."
        report.metadata = {
            "file_name": doc_meta.get("file_name"),
            "domaine_principal": doc_meta.get("domaine_principal"),
            "organisme_name": doc_meta.get("organisme_name"),
            "organisme_id": doc_meta.get("organisme_id"),
            "chunks": len(chunks),
            "use_gliner": use_gliner,
            "use_regex": use_regex,
            "use_llm_extractor": use_llm_extractor,
            "llm_extractor_model": llm_extractor_model,
        }

        return nlp_result, nlp_json, report

    except Exception as exc:
        report.duration = time.time() - t0
        report.ok = False
        report.status = StepStatus.ERROR
        report.message = "Erreur pendant le NLP."
        report.errors.append(str(exc))
        logger.error("run_nlp error: %s", exc, exc_info=True)
        return None, None, report


def run_rag_ingest(
    rag_pipeline: Any,
    nlp_json: dict[str, Any],
) -> tuple[int, StepReport]:
    """
    Indexe un JSON NLP dans le RAG.

    Retour :
      indexed_chunks, StepReport
    """
    t0 = time.time()
    report = StepReport(
        step=PipelineStep.RAG,
        status=StepStatus.RUNNING,
        message="Indexation RAG en cours.",
    )

    try:
        if rag_pipeline is None:
            raise ValueError("rag_pipeline est None.")

        if not is_nlp_json(nlp_json):
            raise ValueError("nlp_json invalide : document_metadata/chunks absents.")

        indexed_chunks = int(rag_pipeline.ingest(nlp_json) or 0)
        total_chunks = int(getattr(rag_pipeline, "total_chunks", indexed_chunks) or 0)

        report.duration = time.time() - t0
        report.ok = indexed_chunks > 0
        report.status = StepStatus.OK if indexed_chunks > 0 else StepStatus.WARNING
        report.message = (
            f"RAG indexé : {indexed_chunks} chunks ajoutés."
            if indexed_chunks > 0
            else "RAG lancé, mais aucun chunk n'a été indexé."
        )
        report.metadata = {
            "indexed_chunks": indexed_chunks,
            "total_rag_chunks": total_chunks,
        }

        try:
            report.metadata["rag_stats"] = rag_pipeline.stats()
        except Exception:
            pass

        return indexed_chunks, report

    except Exception as exc:
        report.duration = time.time() - t0
        report.ok = False
        report.status = StepStatus.ERROR
        report.message = "Erreur pendant l'indexation RAG."
        report.errors.append(str(exc))
        logger.error("run_rag_ingest error: %s", exc, exc_info=True)
        return 0, report


# ══════════════════════════════════════════════════════════════════════════════
# FULL WORKFLOWS
# ══════════════════════════════════════════════════════════════════════════════

def prepare_raw_document(
    file_path: str | Path,
    rag_pipeline: Any,
    *,
    vision_mode: str = "full",
    source_tag: str = "DE_DOC",
    use_gliner: bool = True,
    use_regex: bool = True,
    use_llm_extractor: bool = True,
    llm_extractor_model: str = "ollama:mistral:7b-instruct",
    ner_on_visual_chunks: bool = False,
    include_debug: bool = False,
    save_nlp_json: bool = True,
    output_json_path: Optional[str | Path] = None,
    organisme_name: Optional[str] = None,
    organisme_id: Optional[str] = None,
) -> tuple[WorkflowReport, Optional[Any], Optional[Any], Optional[dict[str, Any]]]:
    """
    Workflow complet pour document brut.

    Retour :
      workflow_report, extraction_result, nlp_result, nlp_json
    """
    t0 = time.time()
    path = Path(file_path)

    workflow = WorkflowReport(
        ok=False,
        mode=WorkflowMode.RAW_DOCUMENT,
        file_path=str(path),
        file_name=path.name,
    )

    # 1. Extraction
    extraction_result, extraction_report = run_extraction(
        path,
        vision_mode=vision_mode,
        source_tag=source_tag,
    )
    workflow.extraction = extraction_report

    if not extraction_report.ok:
        workflow.processing_time = time.time() - t0
        workflow.errors.extend(extraction_report.errors)
        workflow.warnings.extend(extraction_report.warnings)
        return workflow, extraction_result, None, None

    # 2. NLP
    nlp_result, nlp_json, nlp_report = run_nlp(
        extraction_result,
        use_gliner=use_gliner,
        use_regex=use_regex,
        use_llm_extractor=use_llm_extractor,
        llm_extractor_model=llm_extractor_model,
        ner_on_visual_chunks=ner_on_visual_chunks,
        include_debug=include_debug,
        organisme_name=organisme_name,
        organisme_id=organisme_id,
    )
    workflow.nlp = nlp_report

    if not nlp_report.ok or nlp_json is None:
        workflow.processing_time = time.time() - t0
        workflow.errors.extend(nlp_report.errors)
        workflow.warnings.extend(nlp_report.warnings)
        return workflow, extraction_result, nlp_result, nlp_json

    # 3. Sauvegarde JSON NLP
    if save_nlp_json:
        try:
            out_path = Path(output_json_path) if output_json_path else default_nlp_json_path(path)
            save_json(nlp_json, out_path)
            workflow.output_json_path = str(out_path)
        except Exception as exc:
            workflow.warnings.append(f"Impossible de sauvegarder le JSON NLP : {exc}")

    # 4. RAG
    indexed_chunks, rag_report = run_rag_ingest(rag_pipeline, nlp_json)
    workflow.rag = rag_report
    workflow.indexed_chunks = indexed_chunks

    doc_meta = nlp_json.get("document_metadata", {}) or {}
    workflow.document_metadata = doc_meta
    workflow.total_chunks = len(nlp_json.get("chunks", []) or [])
    workflow.file_name = doc_meta.get("file_name") or workflow.file_name

    workflow.processing_time = time.time() - t0
    workflow.ok = bool(extraction_report.ok and nlp_report.ok and rag_report.ok)

    workflow.errors.extend(extraction_report.errors)
    workflow.errors.extend(nlp_report.errors)
    workflow.errors.extend(rag_report.errors)

    workflow.warnings.extend(extraction_report.warnings)
    workflow.warnings.extend(nlp_report.warnings)
    workflow.warnings.extend(rag_report.warnings)

    return workflow, extraction_result, nlp_result, nlp_json


def prepare_nlp_json_file(
    json_path: str | Path,
    rag_pipeline: Any,
    *,
    organisme_name: Optional[str] = None,
    organisme_id: Optional[str] = None,
) -> tuple[WorkflowReport, Optional[dict[str, Any]]]:
    """
    Workflow pour un fichier .nlp.json déjà généré.

    Retour :
      workflow_report, nlp_json
    """
    t0 = time.time()
    path = Path(json_path)

    workflow = WorkflowReport(
        ok=False,
        mode=WorkflowMode.NLP_JSON_FILE,
        file_path=str(path),
        file_name=path.name,
    )

    # Extraction et NLP sont sautés.
    workflow.extraction = StepReport(
        step=PipelineStep.EXTRACTION,
        status=StepStatus.SKIPPED,
        ok=True,
        message="Extraction sautée : JSON NLP fourni.",
    )
    workflow.nlp = StepReport(
        step=PipelineStep.NLP,
        status=StepStatus.SKIPPED,
        ok=True,
        message="NLP sauté : JSON NLP fourni.",
    )

    try:
        nlp_json = load_json(path)

        if not is_nlp_json(nlp_json):
            raise ValueError(
                "Le fichier ne semble pas être un JSON NLP valide : "
                "champs document_metadata/chunks absents."
            )

        nlp_json = apply_organisme_to_nlp_json(
            nlp_json,
            organisme_name=organisme_name,
            organisme_id=organisme_id,
        )

        indexed_chunks, rag_report = run_rag_ingest(rag_pipeline, nlp_json)

        workflow.rag = rag_report
        workflow.indexed_chunks = indexed_chunks
        workflow.total_chunks = len(nlp_json.get("chunks", []) or [])
        workflow.document_metadata = nlp_json.get("document_metadata", {}) or {}
        workflow.file_name = (
            workflow.document_metadata.get("file_name")
            or path.stem
        )
        workflow.processing_time = time.time() - t0
        workflow.ok = bool(rag_report.ok)

        workflow.errors.extend(rag_report.errors)
        workflow.warnings.extend(rag_report.warnings)

        return workflow, nlp_json

    except Exception as exc:
        workflow.processing_time = time.time() - t0
        workflow.ok = False
        workflow.rag = StepReport(
            step=PipelineStep.RAG,
            status=StepStatus.ERROR,
            ok=False,
            message="Erreur préparation JSON NLP.",
            duration=0.0,
            errors=[str(exc)],
        )
        workflow.errors.append(str(exc))
        logger.error("prepare_nlp_json_file error: %s", exc, exc_info=True)
        return workflow, None


def prepare_nlp_json_memory(
    nlp_json: dict[str, Any],
    rag_pipeline: Any,
    *,
    file_name: str = "document_nlp",
    organisme_name: Optional[str] = None,
    organisme_id: Optional[str] = None,
) -> WorkflowReport:
    """
    Workflow pour JSON NLP déjà en mémoire.

    Utile pour Streamlit ou tests.
    """
    t0 = time.time()

    workflow = WorkflowReport(
        ok=False,
        mode=WorkflowMode.NLP_JSON_MEMORY,
        file_name=file_name,
    )

    workflow.extraction = StepReport(
        step=PipelineStep.EXTRACTION,
        status=StepStatus.SKIPPED,
        ok=True,
        message="Extraction sautée : JSON NLP fourni en mémoire.",
    )
    workflow.nlp = StepReport(
        step=PipelineStep.NLP,
        status=StepStatus.SKIPPED,
        ok=True,
        message="NLP sauté : JSON NLP fourni en mémoire.",
    )

    try:
        if not is_nlp_json(nlp_json):
            raise ValueError("nlp_json invalide : document_metadata/chunks absents.")

        nlp_json = apply_organisme_to_nlp_json(
            nlp_json,
            organisme_name=organisme_name,
            organisme_id=organisme_id,
        )

        indexed_chunks, rag_report = run_rag_ingest(rag_pipeline, nlp_json)

        workflow.rag = rag_report
        workflow.indexed_chunks = indexed_chunks
        workflow.total_chunks = len(nlp_json.get("chunks", []) or [])
        workflow.document_metadata = nlp_json.get("document_metadata", {}) or {}
        workflow.file_name = (
            workflow.document_metadata.get("file_name")
            or file_name
        )
        workflow.processing_time = time.time() - t0
        workflow.ok = bool(rag_report.ok)

        workflow.errors.extend(rag_report.errors)
        workflow.warnings.extend(rag_report.warnings)

        return workflow

    except Exception as exc:
        workflow.processing_time = time.time() - t0
        workflow.ok = False
        workflow.rag = StepReport(
            step=PipelineStep.RAG,
            status=StepStatus.ERROR,
            ok=False,
            message="Erreur préparation JSON NLP mémoire.",
            errors=[str(exc)],
        )
        workflow.errors.append(str(exc))
        logger.error("prepare_nlp_json_memory error: %s", exc, exc_info=True)
        return workflow


def prepare_document_auto(
    file_path: str | Path,
    rag_pipeline: Any,
    *,
    vision_mode: str = "full",
    source_tag: str = "DE_DOC",
    use_gliner: bool = True,
    use_regex: bool = True,
    use_llm_extractor: bool = True,
    llm_extractor_model: str = "ollama:mistral:7b-instruct",
    ner_on_visual_chunks: bool = False,
    include_debug: bool = False,
    save_nlp_json: bool = True,
    output_json_path: Optional[str | Path] = None,
    organisme_name: Optional[str] = None,
    organisme_id: Optional[str] = None,
) -> tuple[WorkflowReport, Optional[Any], Optional[Any], Optional[dict[str, Any]]]:
    """
    Prépare automatiquement :
      - un fichier .nlp.json
      - ou un document brut.

    Retour standard :
      workflow_report, extraction_result, nlp_result, nlp_json

    Pour un .nlp.json :
      extraction_result = None
      nlp_result = None
      nlp_json = dict
    """
    path = Path(file_path)

    if is_nlp_json_file(path):
        workflow, nlp_json = prepare_nlp_json_file(
            path,
            rag_pipeline,
            organisme_name=organisme_name,
            organisme_id=organisme_id,
        )
        return workflow, None, None, nlp_json

    return prepare_raw_document(
        file_path=path,
        rag_pipeline=rag_pipeline,
        vision_mode=vision_mode,
        source_tag=source_tag,
        use_gliner=use_gliner,
        use_regex=use_regex,
        use_llm_extractor=use_llm_extractor,
        llm_extractor_model=llm_extractor_model,
        ner_on_visual_chunks=ner_on_visual_chunks,
        include_debug=include_debug,
        save_nlp_json=save_nlp_json,
        output_json_path=output_json_path,
        organisme_name=organisme_name,
        organisme_id=organisme_id,
    )


# ══════════════════════════════════════════════════════════════════════════════
# STATUS / FORMAT HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def workflow_summary_markdown(workflow: WorkflowReport) -> str:
    """
    Résumé Markdown simple pour Streamlit.
    """
    if workflow is None:
        return "Aucun workflow exécuté."

    def step_line(label: str, step: StepReport) -> str:
        emoji = {
            StepStatus.OK: "✅",
            StepStatus.WARNING: "⚠️",
            StepStatus.ERROR: "❌",
            StepStatus.SKIPPED: "⏭️",
            StepStatus.RUNNING: "⏳",
            StepStatus.NOT_STARTED: "○",
        }.get(step.status, "○")

        return f"- {emoji} **{label}** : {step.message} ({step.duration:.2f}s)"

    lines = [
        "### Rapport de préparation",
        f"- **Fichier** : {workflow.file_name or 'inconnu'}",
        f"- **Mode** : {workflow.mode.value}",
        f"- **OK** : {'oui' if workflow.ok else 'non'}",
        f"- **Chunks NLP** : {workflow.total_chunks}",
        f"- **Chunks indexés RAG** : {workflow.indexed_chunks}",
        f"- **Temps total** : {workflow.processing_time:.2f}s",
        "",
        step_line("Extraction", workflow.extraction),
        step_line("NLP", workflow.nlp),
        step_line("RAG", workflow.rag),
    ]

    if workflow.document_metadata:
        org_name = workflow.document_metadata.get("organisme_name")
        org_id = workflow.document_metadata.get("organisme_id")
        if org_name:
            lines.append(f"- **Organisme** : {org_name}")
        if org_id:
            lines.append(f"- **Organisme ID** : `{org_id}`")

        domain = workflow.document_metadata.get("domaine_principal")
        if domain:
            lines.append(f"- **Domaine principal** : {domain}")

    if workflow.output_json_path:
        lines.append(f"- **JSON NLP sauvegardé** : `{workflow.output_json_path}`")

    if workflow.warnings:
        lines.append("\n**Warnings :**")
        for w in workflow.warnings[:5]:
            lines.append(f"- {w}")

    if workflow.errors:
        lines.append("\n**Erreurs :**")
        for e in workflow.errors[:5]:
            lines.append(f"- {e}")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# TEST LOCAL RAPIDE
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(name)s | %(message)s",
    )

    parser = argparse.ArgumentParser(description="Test workflow EnnoAmel")
    parser.add_argument("file", help="Document brut ou .nlp.json")
    parser.add_argument("--cuda", action="store_true")
    parser.add_argument("--clear", action="store_true")
    parser.add_argument("--fast-vision", action="store_true")
    parser.add_argument("--organisme", default=None, help="Nom de l'organisme client")
    parser.add_argument("--organisme-id", default=None, help="Identifiant organisme stable")
    args = parser.parse_args()

    from modules.RAG.rag_pipeline import RAGPipeline

    rag = RAGPipeline(
        embedding_model="BAAI/bge-m3",
        embedding_device="cuda" if args.cuda else "cpu",
        llm_model="ollama:mistral:7b-instruct",
        auto_load=True,
    )

    if args.clear:
        # Sous Windows, éviter delete_files=True car chroma.sqlite3 peut être verrouillé.
        rag.clear(delete_files=False)

    report, extraction_result, nlp_result, nlp_json = prepare_document_auto(
        file_path=args.file,
        rag_pipeline=rag,
        vision_mode="fast" if args.fast_vision else "full",
        include_debug=False,
        organisme_name=args.organisme,
        organisme_id=args.organisme_id,
    )

    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    print()
    print(workflow_summary_markdown(report))