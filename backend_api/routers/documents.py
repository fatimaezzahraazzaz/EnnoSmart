# -*- coding: utf-8 -*-
from pathlib import Path
import hashlib
import logging
import mimetypes
import re
import tempfile
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from fpdf import FPDF
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from core.config import settings
from core.deps import get_current_user, get_db
from db.models import Document, User
from modules.extraction.router import extract
from schemas.document import (
    DiagnosticCorpusDecisionRequest,
    DiagnosticCorpusReview,
    DocumentRead,
)
from services.document_corpus_service import (
    CORPUS_DIAGNOSTIC,
    CORPUS_IMPROVEMENT,
    WORK_ITEM_DOCUMENT_TYPE,
    diagnostic_document_review,
    ensure_document_corpus,
    set_diagnostic_decision,
)
from services.file_service import project_output_dir, validate_upload_file
from services.project_service import get_project_for_user


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects/{project_id}/documents", tags=["documents"])


AUDIO_VIDEO_EXTENSIONS = {
    ".mp3",
    ".wav",
    ".m4a",
    ".aac",
    ".flac",
    ".ogg",
    ".opus",
    ".wma",
    ".mp4",
    ".mov",
    ".avi",
    ".mkv",
    ".webm",
    ".mpeg",
    ".mpg",
    ".3gp",
}


def _normalise_path(path: str | Path | None) -> str:
    if not path:
        return ""
    return str(Path(path)).replace("\\", "/")


def _guess_document_type(path: Path) -> str:
    ext = path.suffix.lower()

    if ext == ".pdf":
        return "PDF"
    if ext in {".docx", ".doc"}:
        return "Word"
    if ext in {".xlsx", ".xls"}:
        return "Excel"
    if ext in {".pptx", ".ppt"}:
        return "PowerPoint"
    if ext in {".png", ".jpg", ".jpeg"}:
        return "Image"
    if ext == ".msg":
        return "Email"
    if ext == ".txt":
        return "Texte"

    return "Autre"


def _make_stored_filename(original_filename: str, sha256: str) -> str:
    path = Path(original_filename or "document")
    suffix = path.suffix.lower()
    stem = path.stem or "document"

    safe_stem = (
        stem.replace("\\", "_")
        .replace("/", "_")
        .replace(":", "_")
        .replace("*", "_")
        .replace("?", "_")
        .replace('"', "_")
        .replace("<", "_")
        .replace(">", "_")
        .replace("|", "_")
    )

    return f"{safe_stem}_{sha256[:12]}{suffix}"


def _safe_download_stem(filename: str | None) -> str:
    stem = Path(filename or "media").stem or "media"

    safe = (
        stem.replace("\\", "_")
        .replace("/", "_")
        .replace(":", "_")
        .replace("*", "_")
        .replace("?", "_")
        .replace('"', "_")
        .replace("<", "_")
        .replace(">", "_")
        .replace("|", "_")
        .strip()
    )

    return safe or "media"


def _pdf_safe_text(value: object) -> str:
    """
    PyFPDF 1.7.2 utilise les polices core en encodage latin-1.
    Cette fonction conserve les accents français compatibles et remplace
    les caractères typographiques Unicode qui feraient planter pdf.output().
    """
    text = str(value or "")

    replacements = {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u2192": "->",
        "\u2022": "-",
        "\u2026": "...",
        "\u00a0": " ",
    }

    for source, target in replacements.items():
        text = text.replace(source, target)

    return text.encode("latin-1", errors="replace").decode("latin-1")


def _parse_transcription_chunk(chunk: str) -> tuple[str, str, str]:
    """
    Parse le format produit par audio_transcriber_optimized.py :

    [00:12:31 - 00:13:08]
    Interlocuteur 1 :
    Texte...

    Retourne : (horodatage, interlocuteur, texte)
    """
    cleaned = str(chunk or "").strip()
    if not cleaned:
        return "", "", ""

    lines = [line.strip() for line in cleaned.splitlines()]
    lines = [line for line in lines if line]

    timestamp = ""
    speaker = ""
    text_lines: list[str] = []

    if lines and re.match(r"^\[\d{2}:\d{2}:\d{2}\s*-\s*\d{2}:\d{2}:\d{2}\]$", lines[0]):
        timestamp = lines.pop(0)

    if lines:
        speaker_match = re.match(
            r"^(Interlocuteur\s+\d+|Transcription)\s*:\s*$",
            lines[0],
            flags=re.IGNORECASE,
        )
        if speaker_match:
            speaker = speaker_match.group(1)
            lines.pop(0)

    text_lines = lines

    return timestamp, speaker, " ".join(text_lines).strip()


def _speaker_palette(speaker: str) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """
    Retourne (couleur_titre, couleur_fond).

    Les fonds restent volontairement très clairs pour conserver
    un PDF professionnel et facilement imprimable.
    """
    match = re.search(r"(\d+)", str(speaker or ""))

    if match:
        speaker_number = int(match.group(1))
    else:
        speaker_number = 0

    palettes = [
        ((31, 78, 121), (232, 241, 250)),   # bleu
        ((46, 125, 92), (232, 247, 239)),   # vert
        ((132, 78, 150), (244, 235, 247)),  # violet
        ((174, 100, 35), (252, 241, 226)),  # orange
    ]

    if speaker_number <= 0:
        return (70, 70, 70), (245, 245, 245)

    return palettes[(speaker_number - 1) % len(palettes)]


def _build_transcription_pdf(
    *,
    filename: str,
    text_chunks: list[str],
    duration_seconds: float | None,
    language: str | None,
    model_name: str | None = None,
    engine: str | None = None,
) -> bytes:
    """
    Génère un PDF lisible avec un bloc coloré par interlocuteur.

    model_name / engine restent dans la signature pour compatibilité avec
    l'appel existant, mais ne sont volontairement plus affichés dans le PDF.
    """
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # Titre
    pdf.set_text_color(25, 25, 25)
    pdf.set_font("Arial", "B", 16)
    pdf.multi_cell(
        0,
        9,
        _pdf_safe_text(f"Transcription de {filename}"),
        align="C",
    )
    pdf.ln(3)

    # Métadonnées : seulement durée + langue.
    if duration_seconds is None:
        duration_label = "inconnue"
    else:
        total_seconds = max(0, int(round(float(duration_seconds))))
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        duration_label = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    pdf.set_text_color(90, 90, 90)
    pdf.set_font("Arial", "I", 10)
    pdf.multi_cell(
        0,
        6,
        _pdf_safe_text(
            f"Durée : {duration_label} | Langue : {language or 'auto'}"
        ),
        align="C",
    )
    pdf.ln(7)

    for index, chunk in enumerate(text_chunks, start=1):
        timestamp, speaker, text = _parse_transcription_chunk(chunk)

        if not text and not speaker:
            continue

        if index > 1:
            pdf.ln(3)

        title_color, fill_color = _speaker_palette(speaker)

        # Horodatage discret
        if timestamp:
            pdf.set_text_color(115, 115, 115)
            pdf.set_font("Arial", "I", 9)
            pdf.multi_cell(
                0,
                5,
                _pdf_safe_text(timestamp),
                align="L",
            )

        # Nom de l'interlocuteur
        if speaker:
            pdf.set_text_color(*title_color)
            pdf.set_font("Arial", "B", 11)
            pdf.multi_cell(
                0,
                6,
                _pdf_safe_text(f"{speaker} :"),
                align="L",
            )

        # Texte sur fond coloré clair
        pdf.set_fill_color(*fill_color)
        pdf.set_text_color(30, 30, 30)
        pdf.set_font("Arial", size=11)

        body = text or str(chunk or "").strip()
        pdf.multi_cell(
            0,
            6,
            _pdf_safe_text(body),
            border=0,
            align="L",
            fill=True,
        )

        pdf.ln(1)

    # Réinitialisation
    pdf.set_text_color(0, 0, 0)

    raw_output = pdf.output(dest="S")

    if isinstance(raw_output, str):
        return raw_output.encode("latin-1")

    return bytes(raw_output)


def _existing_file_paths_for_project(project) -> list[Path]:
    """
    Récupère les documents déjà présents dans les dossiers IA du projet.

    Cas historique EnnoSmart :
    <racine-projet>/outputs/safe_rag_upload/{organisme}/{projet}/{annee}/uploaded

    Cette fonction sert seulement à importer d'anciens fichiers disque
    vers PostgreSQL. Les nouveaux uploads ne passent plus par le disque.
    """
    output_dir = project_output_dir(project)

    candidate_dirs = [
        output_dir / "uploaded",
        output_dir / "raw",
        output_dir / "documents",
        output_dir / "input",
        output_dir / "inputs",
    ]

    allowed = settings.allowed_extensions_set
    found: list[Path] = []

    for directory in candidate_dirs:
        if not directory.exists() or not directory.is_dir():
            continue

        for path in directory.rglob("*"):
            if not path.is_file():
                continue

            if path.suffix.lower() not in allowed:
                continue

            found.append(path)

    unique: list[Path] = []
    seen: set[str] = set()

    for path in found:
        key = _normalise_path(path.resolve())
        if key not in seen:
            seen.add(key)
            unique.append(path)

    return unique


@router.get("", response_model=list[DocumentRead])
def list_documents(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)

    return (
        db.query(Document)
        .filter(Document.project_id == project.id)
        .order_by(Document.created_at.desc())
        .all()
    )


@router.get("/diagnostic-review", response_model=DiagnosticCorpusReview)
def get_diagnostic_document_review(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)
    return diagnostic_document_review(db, project.id)


@router.post("/diagnostic-review", response_model=DiagnosticCorpusReview)
def update_diagnostic_document_review(
    project_id: int,
    payload: DiagnosticCorpusDecisionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)
    document_ids = {int(item.document_id) for item in payload.decisions}
    documents = (
        db.query(Document)
        .filter(
            Document.project_id == project.id,
            Document.id.in_(document_ids),
        )
        .all()
        if document_ids
        else []
    )
    by_id = {int(document.id): document for document in documents}
    missing = sorted(document_ids - set(by_id))
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document(s) introuvable(s) dans ce projet : {missing}",
        )

    try:
        for item in payload.decisions:
            set_diagnostic_decision(
                db,
                by_id[int(item.document_id)],
                keep=bool(item.keep),
            )
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    return diagnostic_document_review(db, project.id)


@router.post("/upload", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
async def upload_document(
    project_id: int,
    file: UploadFile = File(...),
    document_type: str | None = Query(default=None),
    corpus_scope: Literal["diagnostic", "improvement"] = Query(default="diagnostic"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Upload dans un corpus explicite : Diagnostic par défaut, ou Amélioration.

    Nouvelle logique :
    - tout fichier envoyé au corpus Diagnostic est un élément de travail,
      y compris un pré-CIR ou un CIR précédent ;
    - le fichier complet est stocké dans PostgreSQL : documents.file_data
    - aucun fichier permanent n'est écrit dans storage/uploads
    - file_path devient seulement un identifiant logique db://...
    """
    project = get_project_for_user(db, project_id, current_user)

    original_filename = file.filename or "document"
    suffix = Path(original_filename).suffix.lower()
    declared_content_type = (file.content_type or "").lower()

    if (
        suffix in AUDIO_VIDEO_EXTENSIONS
        or declared_content_type.startswith("audio/")
        or declared_content_type.startswith("video/")
    ):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                "Les fichiers audio et vidéo doivent être envoyés depuis "
                "l’onglet Vidéo / Audio pour être transcrits."
            ),
        )

    max_bytes = int(settings.MAX_UPLOAD_SIZE_MB) * 1024 * 1024
    file_bytes = await file.read(max_bytes + 1)

    if not file_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Fichier vide ou illisible.",
        )

    validate_upload_file(file, len(file_bytes))

    sha256 = hashlib.sha256(file_bytes).hexdigest()
    stored_filename = _make_stored_filename(original_filename, sha256)

    content_type = (
        file.content_type
        or mimetypes.guess_type(original_filename)[0]
        or "application/octet-stream"
    )

    document = Document(
        project_id=project.id,
        filename=original_filename,
        stored_filename=stored_filename,

        # Pas de chemin disque réel pour les nouveaux uploads.
        file_path=f"db://documents/{sha256}",

        content_type=content_type,
        file_size=len(file_bytes),
        document_type=(
            WORK_ITEM_DOCUMENT_TYPE
            if corpus_scope == CORPUS_DIAGNOSTIC
            else document_type or _guess_document_type(Path(original_filename))
        ),
        upload_status="importé_en_base",

        file_data=file_bytes,
        file_sha256=sha256,
        storage_mode="database",
    )

    db.add(document)
    db.flush()
    ensure_document_corpus(
        db,
        document,
        CORPUS_IMPROVEMENT if corpus_scope == "improvement" else CORPUS_DIAGNOSTIC,
    )
    db.commit()
    db.refresh(document)

    return document


@router.post("/transcribe-video", status_code=status.HTTP_200_OK)
async def transcribe_video(
    project_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Reçoit un fichier audio/vidéo, le transcrit avec le routeur EnnoSmart
    (faster-whisper), génère un PDF et le retourne en téléchargement.

    URL finale :
    POST /projects/{project_id}/documents/transcribe-video
    """
    # Vérifie que le projet existe et appartient au consultant connecté.
    get_project_for_user(db, project_id, current_user)

    original_filename = file.filename or "media"
    suffix = Path(original_filename).suffix.lower()

    if suffix not in AUDIO_VIDEO_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                "Format audio/vidéo non supporté. "
                "Formats acceptés : MP3, WAV, M4A, AAC, FLAC, OGG, OPUS, WMA, "
                "MP4, MOV, AVI, MKV, WEBM, MPEG, MPG, 3GP."
            ),
        )

    tmp_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            file_size = 0

            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break

                file_size += len(chunk)
                tmp.write(chunk)

            tmp.flush()
            tmp_path = Path(tmp.name)

        if file_size == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Fichier vide ou illisible.",
            )

        # extract() est synchrone et potentiellement coûteux.
        # On l'exécute dans un thread pour ne pas bloquer la boucle FastAPI.
        result = await run_in_threadpool(
            extract,
            tmp_path,
            enable_transcription=True,
            transcription_model="turbo",
            transcription_language="fr",
            transcription_beam_size=1,
            transcription_group_chunks=False,
            transcription_chunk_seconds=90,
            transcription_chunk_max_chars=2500,
        )

        text_chunks = [
            str(chunk).strip()
            for chunk in (getattr(result, "text_chunks", None) or [])
            if str(chunk or "").strip()
        ]

        if not text_chunks:
            extraction_errors = getattr(result, "extraction_errors", None) or []
            detail = (
                " | ".join(str(item) for item in extraction_errors if item)
                or "Aucune transcription obtenue. Vérifie le fichier et faster-whisper."
            )

            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=detail,
            )

        pdf_bytes = _build_transcription_pdf(
            filename=original_filename,
            text_chunks=text_chunks,
            duration_seconds=getattr(result, "media_duration_seconds", None),
            language=getattr(result, "transcription_language", None),
            model_name=getattr(result, "transcription_model", None),
            engine=getattr(result, "transcription_engine", None),
        )

        download_name = f"transcription_{_safe_download_stem(original_filename)}.pdf"

        logger.info(
            "Transcription terminée project_id=%s file=%s chunks=%s pdf_bytes=%s",
            project_id,
            original_filename,
            len(text_chunks),
            len(pdf_bytes),
        )

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{download_name}"',
                "Cache-Control": "no-store",
            },
        )

    except HTTPException:
        raise

    except Exception as exc:
        logger.exception(
            "Erreur transcription project_id=%s file=%s",
            project_id,
            original_filename,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur pendant la transcription : {exc}",
        ) from exc

    finally:
        try:
            await file.close()
        except Exception:
            pass

        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception as exc:
                logger.warning(
                    "Impossible de supprimer le fichier temporaire %s : %s",
                    tmp_path,
                    exc,
                )


@router.post("/import-existing", response_model=list[DocumentRead])
def import_existing_documents(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Importe en base les anciens fichiers présents sur disque.

    Ancien comportement :
    - lier le chemin disque dans documents.file_path.

    Nouveau comportement :
    - lire le fichier disque,
    - stocker son contenu dans documents.file_data,
    - mettre storage_mode='database',
    - utiliser file_path='db://documents/<sha256>'.

    Cette route ne supprime pas les fichiers disque automatiquement.
    La suppression doit être faite après vérification avec un script dédié.
    """
    project = get_project_for_user(db, project_id, current_user)
    paths = _existing_file_paths_for_project(project)

    if not paths:
        return []

    existing_sha = {
        doc.file_sha256
        for doc in db.query(Document).filter(Document.project_id == project.id).all()
        if doc.file_sha256
    }

    existing_logical_paths = {
        _normalise_path(doc.file_path)
        for doc in db.query(Document).filter(Document.project_id == project.id).all()
        if doc.file_path
    }

    created: list[Document] = []

    for path in paths:
        if not path.exists() or not path.is_file():
            continue

        file_bytes = path.read_bytes()
        if not file_bytes:
            continue

        sha256 = hashlib.sha256(file_bytes).hexdigest()
        logical_path = f"db://documents/{sha256}"

        if sha256 in existing_sha or _normalise_path(logical_path) in existing_logical_paths:
            continue

        stored_filename = _make_stored_filename(path.name, sha256)

        document = Document(
            project_id=project.id,
            filename=path.name,
            stored_filename=stored_filename,
            file_path=logical_path,
            content_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            file_size=len(file_bytes),
            document_type=_guess_document_type(path),
            upload_status="importé_en_base_depuis_outputs",
            file_data=file_bytes,
            file_sha256=sha256,
            storage_mode="database",
        )

        db.add(document)
        db.flush()
        ensure_document_corpus(db, document, CORPUS_DIAGNOSTIC)
        created.append(document)
        existing_sha.add(sha256)
        existing_logical_paths.add(_normalise_path(logical_path))

    db.commit()

    for document in created:
        db.refresh(document)

    return created
