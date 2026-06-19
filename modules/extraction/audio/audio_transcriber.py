from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import torch
except ImportError:
    torch = None

try:
    from faster_whisper import WhisperModel
    FASTER_WHISPER_AVAILABLE = True
except ImportError:
    FASTER_WHISPER_AVAILABLE = False


class TranscriptionEngine(str, Enum):
    FASTER_WHISPER = "faster_whisper"
    NONE = "none"


@dataclass
class TranscriptionSegment:
    index: int
    start: float
    end: float
    text: str


@dataclass
class TranscriptionResult:
    file_name: str
    source_path: str
    file_type: str = "audio_transcription"

    text_chunks: list[str] = field(default_factory=list)
    full_text: str = ""

    segments: list[TranscriptionSegment] = field(default_factory=list)

    engine_used: TranscriptionEngine = TranscriptionEngine.NONE
    model_name: str = ""
    language: Optional[str] = None

    duration: Optional[float] = None
    confidence_score: float = 0.0

    tags: list[str] = field(default_factory=list)
    extraction_errors: list[str] = field(default_factory=list)


class _WhisperModelCache:
    _model = None
    _loaded_model_name: Optional[str] = None
    _device: Optional[str] = None
    _compute_type: Optional[str] = None

    @classmethod
    def load(
        cls,
        model_name: str = "small",
        device: Optional[str] = None,
        compute_type: Optional[str] = None,
    ) -> bool:
        if not FASTER_WHISPER_AVAILABLE:
            logger.error("faster-whisper non installé")
            return False

        if device is None:
            device = "cuda" if torch is not None and torch.cuda.is_available() else "cpu"

        if compute_type is None:
            if device == "cuda":
                compute_type = "float16"
            else:
                compute_type = "int8"

        if (
            cls._model is not None
            and cls._loaded_model_name == model_name
            and cls._device == device
            and cls._compute_type == compute_type
        ):
            return True

        try:
            logger.info(
                "Chargement faster-whisper | model=%s | device=%s | compute_type=%s",
                model_name,
                device,
                compute_type,
            )

            cls._model = WhisperModel(
                model_name,
                device=device,
                compute_type=compute_type,
            )

            cls._loaded_model_name = model_name
            cls._device = device
            cls._compute_type = compute_type

            logger.info("faster-whisper chargé avec succès")
            return True

        except Exception as exc:
            logger.error("Échec chargement faster-whisper : %s", exc)
            cls._model = None
            return False

    @classmethod
    def get(cls):
        return cls._model


def _format_time(seconds: float) -> str:
    seconds = max(0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _build_segment_chunk(
    file_name: str,
    segment: TranscriptionSegment,
    engine: TranscriptionEngine,
) -> str:
    return (
        f"[AUDIO:{file_name}] "
        f"[SEGMENT {segment.index}] "
        f"[{_format_time(segment.start)} → {_format_time(segment.end)}] "
        f"[TRANSCRIPTION:{engine.value}]\n\n"
        f"{segment.text.strip()}"
    )


def extract_audio_transcription(
    file_path: str | Path,
    model_name: str = "small",
    language: Optional[str] = "fr",
    beam_size: int = 5,
) -> TranscriptionResult:
    """
    Transcrit un fichier audio/vidéo pour l'intégrer dans le pipeline EnnoSmart.

    Formats typiques :
    - audio : .mp3, .wav, .m4a, .aac, .flac
    - vidéo : .mp4, .mov, .avi, .mkv

    Retourne des text_chunks compatibles RAG.
    """
    path = Path(file_path)

    result = TranscriptionResult(
        file_name=path.name,
        source_path=str(path.resolve()),
        model_name=model_name,
    )

    if not path.exists():
        result.extraction_errors.append(f"Fichier introuvable : {path}")
        result.tags = ["TRANSCRIPTION:ERROR:NO_FILE"]
        return result

    if not FASTER_WHISPER_AVAILABLE:
        result.extraction_errors.append(
            "faster-whisper non installé. Installer avec : pip install faster-whisper"
        )
        result.tags = ["TRANSCRIPTION:NONE", "ERROR:NO_ENGINE"]
        return result

    ok = _WhisperModelCache.load(model_name=model_name)
    if not ok:
        result.extraction_errors.append("Impossible de charger faster-whisper")
        result.tags = ["TRANSCRIPTION:NONE", "ERROR:MODEL_LOAD_FAILED"]
        return result

    model = _WhisperModelCache.get()
    result.engine_used = TranscriptionEngine.FASTER_WHISPER

    try:
        segments_iter, info = model.transcribe(
            str(path),
            language=language,
            beam_size=beam_size,
            vad_filter=True,
        )

        result.language = getattr(info, "language", language)
        result.duration = getattr(info, "duration", None)

        all_texts: list[str] = []

        for i, seg in enumerate(segments_iter, start=1):
            text = (seg.text or "").strip()
            if not text:
                continue

            segment = TranscriptionSegment(
                index=i,
                start=float(seg.start),
                end=float(seg.end),
                text=text,
            )

            result.segments.append(segment)
            result.text_chunks.append(
                _build_segment_chunk(path.name, segment, result.engine_used)
            )
            all_texts.append(text)

        result.full_text = "\n".join(all_texts).strip()

        if result.segments:
            result.confidence_score = 0.85
        else:
            result.confidence_score = 0.0
            result.extraction_errors.append("Aucun segment transcrit")

        result.tags = [
            "TRANSCRIPTION:FASTER_WHISPER",
            f"MODEL:{model_name}",
            f"LANG:{result.language or 'auto'}",
        ]

        if torch is not None and torch.cuda.is_available():
            result.tags.append("DEVICE:CUDA")
        else:
            result.tags.append("DEVICE:CPU")

        logger.info(
            "✓ Transcription %s | segments=%d | language=%s | device=%s",
            path.name,
            len(result.segments),
            result.language,
            "cuda" if torch is not None and torch.cuda.is_available() else "cpu",
        )

        return result

    except Exception as exc:
        msg = f"Erreur transcription {path.name} : {exc}"
        logger.error(msg)
        result.extraction_errors.append(msg)
        result.tags = ["TRANSCRIPTION:ERROR"]
        return result