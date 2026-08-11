from __future__ import annotations

import gc
import logging
import os
import threading
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
    import ctranslate2
except ImportError:
    ctranslate2 = None

try:
    import whisperx
    from whisperx.diarize import DiarizationPipeline, assign_word_speakers
    WHISPERX_AVAILABLE = True
except ImportError:
    whisperx = None
    DiarizationPipeline = None
    assign_word_speakers = None
    WHISPERX_AVAILABLE = False

try:
    from faster_whisper import WhisperModel
    FASTER_WHISPER_AVAILABLE = True
except ImportError:
    WhisperModel = None
    FASTER_WHISPER_AVAILABLE = False


class TranscriptionEngine(str, Enum):
    WHISPERX = "whisperx"
    FASTER_WHISPER = "faster_whisper"
    NONE = "none"


@dataclass
class TranscriptionSegment:
    index: int
    start: float
    end: float
    text: str
    speaker: Optional[str] = None
    speaker_label: Optional[str] = None


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


HF_TOKEN = os.getenv("HF_TOKEN", "").strip()
DEFAULT_MODEL = os.getenv("TRANSCRIPTION_MODEL", "turbo").strip() or "turbo"
DEFAULT_BATCH_SIZE = max(1, int(os.getenv("TRANSCRIPTION_BATCH_SIZE", "16")))
DEFAULT_NUM_SPEAKERS = max(1, int(os.getenv("TRANSCRIPTION_NUM_SPEAKERS", "2")))
DEFAULT_DIARIZATION = (
    os.getenv("TRANSCRIPTION_DIARIZATION", "true").strip().lower()
    not in {"0", "false", "no", "off"}
)

_TRANSCRIPTION_LOCK = threading.Lock()


def _torch_cuda_available() -> bool:
    return bool(
        torch is not None
        and hasattr(torch, "cuda")
        and torch.cuda.is_available()
    )


def _ctranslate2_cuda_available() -> bool:
    if ctranslate2 is None:
        return False
    try:
        return int(ctranslate2.get_cuda_device_count()) > 0
    except Exception:
        return False


def _detect_asr_device() -> str:
    # Important : faster-whisper/WhisperX utilisent CTranslate2 pour l'ASR.
    # Ne pas décider CUDA uniquement à partir de torch.cuda.is_available().
    if _ctranslate2_cuda_available() or _torch_cuda_available():
        return "cuda"
    return "cpu"


def _detect_torch_device() -> str:
    # Alignement et diarisation utilisent PyTorch.
    return "cuda" if _torch_cuda_available() else "cpu"


def _compute_type_for(device: str) -> str:
    if device == "cuda":
        return os.getenv("TRANSCRIPTION_COMPUTE_TYPE", "float16").strip() or "float16"
    return os.getenv("TRANSCRIPTION_CPU_COMPUTE_TYPE", "int8").strip() or "int8"


def _format_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds or 0.0))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _speaker_key(value: object) -> str:
    text = str(value or "").strip()
    return text if text else "SPEAKER_UNKNOWN"


def _speaker_labels(raw_segments: list[dict]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    counter = 1
    for segment in raw_segments:
        key = _speaker_key(segment.get("speaker"))
        if key not in mapping:
            mapping[key] = f"Interlocuteur {counter}"
            counter += 1
    return mapping


def _merge_speaker_turns(
    raw_segments: list[dict],
    max_gap_seconds: float = 2.5,
) -> list[dict]:
    if not raw_segments:
        return []

    mapping = _speaker_labels(raw_segments)
    merged: list[dict] = []

    for item in raw_segments:
        text = str(item.get("text", "") or "").strip()
        if not text:
            continue

        start = _safe_float(item.get("start"))
        end = _safe_float(item.get("end"), start)
        speaker = _speaker_key(item.get("speaker"))
        label = mapping.get(speaker, speaker)

        if merged:
            previous = merged[-1]
            same_speaker = previous["speaker"] == speaker
            gap = max(0.0, start - previous["end"])
            if same_speaker and gap <= max_gap_seconds:
                previous["text"] = f'{previous["text"]} {text}'.strip()
                previous["end"] = max(previous["end"], end)
                continue

        merged.append({
            "start": start,
            "end": end,
            "text": text,
            "speaker": speaker,
            "speaker_label": label,
        })

    return merged


def _build_readable_chunk(segment: TranscriptionSegment) -> str:
    label = segment.speaker_label or "Transcription"
    return (
        f"[{_format_time(segment.start)} - {_format_time(segment.end)}]\n"
        f"{label} :\n"
        f"{segment.text.strip()}"
    )


def _cleanup_cuda() -> None:
    gc.collect()
    if _torch_cuda_available():
        try:
            torch.cuda.empty_cache()
        except Exception:
            pass


class _WhisperXModelCache:
    _asr_model = None
    _asr_model_name: Optional[str] = None
    _asr_device: Optional[str] = None
    _asr_compute_type: Optional[str] = None

    _align_model = None
    _align_metadata = None
    _align_language: Optional[str] = None
    _align_device: Optional[str] = None

    _diarize_model = None
    _diarize_device: Optional[str] = None
    _diarize_token_signature: Optional[str] = None

    @classmethod
    def get_asr_model(
        cls,
        model_name: str,
        device: str,
        compute_type: str,
        language: Optional[str],
    ):
        if (
            cls._asr_model is not None
            and cls._asr_model_name == model_name
            and cls._asr_device == device
            and cls._asr_compute_type == compute_type
        ):
            return cls._asr_model

        logger.info(
            "Chargement WhisperX ASR | model=%s | device=%s | compute_type=%s",
            model_name,
            device,
            compute_type,
        )

        cls._asr_model = whisperx.load_model(
            model_name,
            device,
            compute_type=compute_type,
            language=language,
        )
        cls._asr_model_name = model_name
        cls._asr_device = device
        cls._asr_compute_type = compute_type
        return cls._asr_model

    @classmethod
    def get_align_model(cls, language: str, device: str):
        if (
            cls._align_model is not None
            and cls._align_language == language
            and cls._align_device == device
        ):
            return cls._align_model, cls._align_metadata

        logger.info(
            "Chargement modèle alignement | language=%s | device=%s",
            language,
            device,
        )
        model_a, metadata = whisperx.load_align_model(
            language_code=language,
            device=device,
        )
        cls._align_model = model_a
        cls._align_metadata = metadata
        cls._align_language = language
        cls._align_device = device
        return model_a, metadata

    @classmethod
    def get_diarize_model(cls, device: str, token: str):
        token_signature = token[-8:] if token else ""
        if (
            cls._diarize_model is not None
            and cls._diarize_device == device
            and cls._diarize_token_signature == token_signature
        ):
            return cls._diarize_model

        logger.info("Chargement modèle diarisation | device=%s", device)
        cls._diarize_model = DiarizationPipeline(
            token=token,
            device=device,
        )
        cls._diarize_device = device
        cls._diarize_token_signature = token_signature
        return cls._diarize_model


def _transcribe_with_whisperx(
    path: Path,
    model_name: str,
    language: Optional[str],
    batch_size: int,
    num_speakers: Optional[int],
    enable_diarization: bool,
) -> TranscriptionResult:
    result = TranscriptionResult(
        file_name=path.name,
        source_path=str(path.resolve()),
        model_name=model_name,
        engine_used=TranscriptionEngine.WHISPERX,
    )

    asr_device = _detect_asr_device()
    torch_device = _detect_torch_device()
    compute_type = _compute_type_for(asr_device)

    logger.info(
        "Transcription WhisperX | file=%s | ASR=%s/%s | torch=%s | batch=%s",
        path.name,
        asr_device,
        compute_type,
        torch_device,
        batch_size,
    )

    audio = whisperx.load_audio(str(path))

    asr_model = _WhisperXModelCache.get_asr_model(
        model_name=model_name,
        device=asr_device,
        compute_type=compute_type,
        language=language,
    )

    # Transcription batchée : beaucoup plus rapide que le décodage séquentiel.
    asr_result = asr_model.transcribe(
        audio,
        batch_size=batch_size,
        language=language,
    )

    raw_segments = list(asr_result.get("segments", []) or [])
    detected_language = asr_result.get("language") or language or "fr"
    result.language = detected_language

    if raw_segments:
        result.duration = max(_safe_float(item.get("end")) for item in raw_segments)

    # Alignement des mots.
    try:
        model_a, metadata = _WhisperXModelCache.get_align_model(
            language=detected_language,
            device=torch_device,
        )
        aligned = whisperx.align(
            raw_segments,
            model_a,
            metadata,
            audio,
            torch_device,
            return_char_alignments=False,
        )
        raw_segments = list(aligned.get("segments", []) or raw_segments)
    except Exception as exc:
        logger.warning("Alignement ignoré : %s", exc)
        result.extraction_errors.append(f"Warning alignement : {exc}")

    diarization_done = False

    if enable_diarization:
        if not HF_TOKEN:
            result.extraction_errors.append(
                "HF_TOKEN absent : transcription créée sans séparation des interlocuteurs."
            )
        elif DiarizationPipeline is None or assign_word_speakers is None:
            result.extraction_errors.append("WhisperX diarization indisponible.")
        else:
            try:
                diarize_model = _WhisperXModelCache.get_diarize_model(
                    device=torch_device,
                    token=HF_TOKEN,
                )
                diarize_kwargs = {}
                if num_speakers is not None and num_speakers > 0:
                    diarize_kwargs["num_speakers"] = int(num_speakers)

                diarize_segments = diarize_model(audio, **diarize_kwargs)
                diarized = assign_word_speakers(
                    diarize_segments,
                    {"segments": raw_segments},
                )
                raw_segments = list(diarized.get("segments", []) or raw_segments)
                diarization_done = True
            except Exception as exc:
                logger.exception("Erreur diarisation WhisperX")
                result.extraction_errors.append(f"Warning diarisation : {exc}")

    if diarization_done:
        readable_segments = _merge_speaker_turns(raw_segments)
    else:
        readable_segments = []
        for item in raw_segments:
            text = str(item.get("text", "") or "").strip()
            if not text:
                continue
            readable_segments.append({
                "start": _safe_float(item.get("start")),
                "end": _safe_float(item.get("end")),
                "text": text,
                "speaker": None,
                "speaker_label": "Transcription",
            })

    for index, item in enumerate(readable_segments, start=1):
        segment = TranscriptionSegment(
            index=index,
            start=_safe_float(item.get("start")),
            end=_safe_float(item.get("end")),
            text=str(item.get("text", "") or "").strip(),
            speaker=item.get("speaker"),
            speaker_label=item.get("speaker_label"),
        )
        if not segment.text:
            continue
        result.segments.append(segment)
        result.text_chunks.append(_build_readable_chunk(segment))

    result.full_text = "\n\n".join(result.text_chunks).strip()
    result.confidence_score = 0.90 if result.segments else 0.0
    result.tags = [
        "TRANSCRIPTION:WHISPERX",
        f"MODEL:{model_name}",
        f"LANG:{result.language or 'auto'}",
        f"ASR_DEVICE:{asr_device.upper()}",
        f"TORCH_DEVICE:{torch_device.upper()}",
        f"BATCH_SIZE:{batch_size}",
        "DIARIZATION:ENABLED" if diarization_done else "DIARIZATION:DISABLED",
    ]

    if num_speakers and diarization_done:
        result.tags.append(f"NUM_SPEAKERS:{num_speakers}")

    if not result.segments:
        result.extraction_errors.append("Aucun segment transcrit.")

    logger.info(
        "Transcription terminée | file=%s | turns=%d | language=%s | ASR=%s | diarization=%s",
        path.name,
        len(result.segments),
        result.language,
        asr_device,
        diarization_done,
    )

    _cleanup_cuda()
    return result


class _FasterWhisperModelCache:
    _model = None
    _model_name: Optional[str] = None
    _device: Optional[str] = None
    _compute_type: Optional[str] = None

    @classmethod
    def get(cls, model_name: str, device: str, compute_type: str):
        if (
            cls._model is not None
            and cls._model_name == model_name
            and cls._device == device
            and cls._compute_type == compute_type
        ):
            return cls._model

        cls._model = WhisperModel(
            model_name,
            device=device,
            compute_type=compute_type,
        )
        cls._model_name = model_name
        cls._device = device
        cls._compute_type = compute_type
        return cls._model


def _transcribe_with_faster_whisper(
    path: Path,
    model_name: str,
    language: Optional[str],
    beam_size: int,
) -> TranscriptionResult:
    result = TranscriptionResult(
        file_name=path.name,
        source_path=str(path.resolve()),
        model_name=model_name,
        engine_used=TranscriptionEngine.FASTER_WHISPER,
    )

    if not FASTER_WHISPER_AVAILABLE:
        result.extraction_errors.append(
            "Ni WhisperX ni faster-whisper ne sont disponibles."
        )
        return result

    device = _detect_asr_device()
    compute_type = _compute_type_for(device)
    model = _FasterWhisperModelCache.get(model_name, device, compute_type)

    segments_iter, info = model.transcribe(
        str(path),
        language=language,
        beam_size=max(1, int(beam_size)),
        vad_filter=True,
        condition_on_previous_text=False,
    )

    raw_segments: list[dict] = []
    for segment in segments_iter:
        text = str(segment.text or "").strip()
        if not text:
            continue
        raw_segments.append({
            "start": float(segment.start),
            "end": float(segment.end),
            "text": text,
        })

    result.language = getattr(info, "language", language)
    result.duration = getattr(info, "duration", None)

    # Fusion en paragraphes lisibles pour le PDF.
    merged: list[dict] = []
    for item in raw_segments:
        if (
            merged
            and item["start"] - merged[-1]["end"] <= 2.5
            and len(merged[-1]["text"]) < 1800
        ):
            merged[-1]["text"] += " " + item["text"]
            merged[-1]["end"] = item["end"]
        else:
            merged.append(dict(item))

    for index, item in enumerate(merged, start=1):
        segment = TranscriptionSegment(
            index=index,
            start=item["start"],
            end=item["end"],
            text=item["text"],
            speaker_label="Transcription",
        )
        result.segments.append(segment)
        result.text_chunks.append(_build_readable_chunk(segment))

    result.full_text = "\n\n".join(result.text_chunks).strip()
    result.confidence_score = 0.85 if result.segments else 0.0
    result.tags = [
        "TRANSCRIPTION:FASTER_WHISPER_FALLBACK",
        f"MODEL:{model_name}",
        f"LANG:{result.language or 'auto'}",
        f"DEVICE:{device.upper()}",
        "DIARIZATION:DISABLED",
    ]

    if not result.segments:
        result.extraction_errors.append("Aucun segment transcrit.")

    _cleanup_cuda()
    return result


def extract_audio_transcription(
    file_path: str | Path,
    model_name: str = DEFAULT_MODEL,
    language: Optional[str] = "fr",
    beam_size: int = 1,
    batch_size: int = DEFAULT_BATCH_SIZE,
    enable_diarization: bool = DEFAULT_DIARIZATION,
    num_speakers: Optional[int] = DEFAULT_NUM_SPEAKERS,
) -> TranscriptionResult:
    """
    Point d'entrée compatible avec modules/extraction/router.py.

    Chemin préféré :
      WhisperX -> batch -> alignement -> diarisation -> blocs Interlocuteur N.

    Fallback :
      faster-whisper sans diarisation.
    """
    path = Path(file_path)

    base_result = TranscriptionResult(
        file_name=path.name,
        source_path=str(path.resolve()) if path.exists() else str(path),
        model_name=model_name,
    )

    if not path.exists():
        base_result.extraction_errors.append(f"Fichier introuvable : {path}")
        base_result.tags = ["TRANSCRIPTION:ERROR:NO_FILE"]
        return base_result

    with _TRANSCRIPTION_LOCK:
        try:
            if WHISPERX_AVAILABLE:
                return _transcribe_with_whisperx(
                    path=path,
                    model_name=model_name,
                    language=language,
                    batch_size=max(1, int(batch_size)),
                    num_speakers=num_speakers,
                    enable_diarization=enable_diarization,
                )

            logger.warning("WhisperX non installé : fallback faster-whisper.")
            return _transcribe_with_faster_whisper(
                path=path,
                model_name=model_name,
                language=language,
                beam_size=beam_size,
            )

        except Exception as exc:
            logger.exception("Erreur transcription %s", path.name)
            base_result.engine_used = TranscriptionEngine.NONE
            base_result.extraction_errors.append(
                f"Erreur transcription {path.name} : {exc}"
            )
            base_result.tags = ["TRANSCRIPTION:ERROR"]
            _cleanup_cuda()
            return base_result
