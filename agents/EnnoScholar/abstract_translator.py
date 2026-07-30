# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
from typing import Any


_TRANSLATOR_CACHE: dict[str, Any] = {
    "opus_model_name": None,
    "device": None,
    "opus_tokenizer": None,
    "opus_model": None,
}


_COMMON_LANG_MAP = {
    "en": "eng_Latn",
    "eng": "eng_Latn",
    "english": "eng_Latn",
    "anglais": "eng_Latn",
    "eng_latn": "eng_Latn",

    "fr": "fra_Latn",
    "fra": "fra_Latn",
    "fre": "fra_Latn",
    "french": "fra_Latn",
    "français": "fra_Latn",
    "francais": "fra_Latn",
    "fra_latn": "fra_Latn",
}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _safe_text(value: Any, max_chars: int = 1000) -> str:
    if value is None:
        return ""
    text = str(value).replace("\x00", " ").strip()
    return text[:max_chars] if text else ""


def _normalize_lang_code(value: Any) -> str:
    raw = _safe_text(value, 80)
    if not raw:
        return ""

    raw_clean = raw.strip()
    raw_norm = raw_clean.lower().replace("-", "_").replace(" ", "_")

    if raw_norm in _COMMON_LANG_MAP:
        return _COMMON_LANG_MAP[raw_norm]

    if re.match(r"^[a-z]{3}_[A-Za-z]{4}$", raw_clean):
        return raw_clean

    return ""


def _looks_like_french(text: str) -> bool:
    sample = f" {str(text or '').lower()} "

    french_markers = [
        " le ",
        " la ",
        " les ",
        " des ",
        " une ",
        " dans ",
        " avec ",
        " pour ",
        " cette ",
        " méthode ",
        " résultats ",
        " apprentissage ",
    ]

    english_markers = [
        " the ",
        " and ",
        " with ",
        " using ",
        " proposed ",
        " results ",
        " method ",
        " this paper ",
        " this study ",
        " this work ",
    ]

    fr_count = sum(1 for marker in french_markers if marker in sample)
    en_count = sum(1 for marker in english_markers if marker in sample)

    return fr_count >= 3 and fr_count > en_count


def _resolve_source_lang(
    abstract: str,
    context: dict[str, Any] | None = None,
) -> str:
    context = context if isinstance(context, dict) else {}

    candidates = [
        context.get("source_language"),
        context.get("language"),
        context.get("lang"),
        os.getenv("ENNOSCHOLAR_TRANSLATION_SOURCE_LANG"),
    ]

    for candidate in candidates:
        code = _normalize_lang_code(candidate)
        if code:
            return code

    if _looks_like_french(abstract):
        return "fra_Latn"

    return "eng_Latn"


def _resolve_target_lang() -> str:
    target = (
        os.getenv("ENNOSCHOLAR_TRANSLATION_TARGET_LANG")
        or os.getenv("ENNOSCHOLAR_TRANSLATION_TGT_LANG")
        or "fra_Latn"
    )

    code = _normalize_lang_code(target)
    return code or "fra_Latn"


def _resolve_device() -> str:
    requested = os.getenv("ENNOSCHOLAR_TRANSLATION_DEVICE", "auto").strip().lower()

    if requested in {"cpu", "cuda"}:
        return requested

    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def _get_torch_dtype(device: str):
    import torch

    use_fp16 = _env_bool("ENNOSCHOLAR_TRANSLATION_FP16", True)

    if device == "cuda" and use_fp16:
        return torch.float16

    return torch.float32


def _load_opus_model(model_name: str, device: str):
    """
    Charge OPUS EN->FR une seule fois.
    """
    global _TRANSLATOR_CACHE

    cached_ok = (
        _TRANSLATOR_CACHE.get("opus_model_name") == model_name
        and _TRANSLATOR_CACHE.get("device") == device
        and _TRANSLATOR_CACHE.get("opus_tokenizer") is not None
        and _TRANSLATOR_CACHE.get("opus_model") is not None
    )

    if cached_ok:
        return _TRANSLATOR_CACHE["opus_tokenizer"], _TRANSLATOR_CACHE["opus_model"]

    try:
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
    except Exception as exc:
        raise RuntimeError(
            "Dépendances manquantes pour OPUS-MT. Lance : "
            "pip install transformers sentencepiece accelerate torch sacremoses"
        ) from exc

    tokenizer = AutoTokenizer.from_pretrained(model_name)

    model = AutoModelForSeq2SeqLM.from_pretrained(
        model_name,
        torch_dtype=_get_torch_dtype(device),
    )

    model.to(device)
    model.eval()

    _TRANSLATOR_CACHE = {
        "opus_model_name": model_name,
        "device": device,
        "opus_tokenizer": tokenizer,
        "opus_model": model,
    }

    return tokenizer, model


def _word_count(text: str) -> int:
    return len(re.findall(r"\w+", str(text or "")))


def _normalize_text(text: str) -> str:
    value = str(text or "").replace("\x00", " ").strip()
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def clean_translation(text: str) -> str:
    """
    Nettoie uniquement les artefacts de sortie.
    Ne reformule pas la traduction.
    """
    value = _normalize_text(text)

    value = re.sub(
        r"^```(?:text|markdown|fr|french)?",
        "",
        value,
        flags=re.IGNORECASE,
    ).strip()
    value = re.sub(r"```$", "", value).strip()

    prefixes = [
        "traduction française :",
        "traduction :",
        "voici la traduction :",
        "résumé traduit :",
        "resume traduit :",
    ]

    lower = value.lower()
    for prefix in prefixes:
        if lower.startswith(prefix):
            value = value[len(prefix):].strip()
            break

    value = value.replace(" ,", ",").replace(" .", ".")
    value = value.replace(" ;", ";").replace(" :", ":")
    value = value.replace("( ", "(").replace(" )", ")")

    # Corrections légères des collages observés avec OPUS.
    replacements = {
        "navigationautonome": "navigation autonome",
        "unetechnique": "une technique",
        "pourextraire": "pour extraire",
        "pourobtenir": "pour obtenir",
        "laperformance": "la performance",
        "cérébraledu": "cérébrale du",
        "cérébrale du": "cérébrale du",
        "d'unetechnique": "d'une technique",
        "laperformance": "la performance",
    }

    for bad, good in replacements.items():
        value = value.replace(bad, good)

    value = re.sub(r"\s+", " ", value).strip()
    return value


def _split_sentences(text: str) -> list[str]:
    """
    Découpe l'abstract phrase par phrase.

    C'est la correction importante :
    OPUS perd des informations quand un chunk contient plusieurs phrases longues.
    On force donc une traduction phrase par phrase pour éviter les troncatures.
    """
    clean = _normalize_text(text)
    if not clean:
        return []

    # Découpe principale sur ponctuation de fin de phrase.
    sentences = re.split(r"(?<=[.!?])\s+", clean)

    result: list[str] = []

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        result.append(sentence)

    return result


def _split_long_sentence(
    sentence: str,
    tokenizer: Any,
    max_tokens: int,
) -> list[str]:
    """
    Si une phrase est trop longue, on la découpe par virgules/segments.
    """
    sentence = sentence.strip()
    if not sentence:
        return []

    try:
        token_count = len(tokenizer(sentence, add_special_tokens=True).input_ids)
    except Exception:
        token_count = max(1, len(sentence) // 4)

    if token_count <= max_tokens:
        return [sentence]

    parts = re.split(r"(?<=[,;:])\s+", sentence)

    chunks: list[str] = []
    current = ""

    def count_tokens(value: str) -> int:
        try:
            return len(tokenizer(value, add_special_tokens=True).input_ids)
        except Exception:
            return max(1, len(value) // 4)

    for part in parts:
        part = part.strip()
        if not part:
            continue

        candidate = f"{current} {part}".strip() if current else part

        if count_tokens(candidate) <= max_tokens:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = ""

        if count_tokens(part) <= max_tokens:
            current = part
        else:
            step = int(os.getenv("ENNOSCHOLAR_TRANSLATION_CHAR_CHUNK", "500"))
            for i in range(0, len(part), step):
                sub = part[i:i + step].strip()
                if sub:
                    chunks.append(sub)

    if current:
        chunks.append(current)

    return chunks


def _build_translation_chunks(
    text: str,
    tokenizer: Any,
    max_tokens: int,
) -> list[str]:
    """
    Construit les chunks finaux.

    Par défaut :
    - une phrase = un chunk ;
    - une phrase trop longue = plusieurs petits chunks.
    """
    sentences = _split_sentences(text)

    chunks: list[str] = []

    for sentence in sentences:
        chunks.extend(
            _split_long_sentence(
                sentence=sentence,
                tokenizer=tokenizer,
                max_tokens=max_tokens,
            )
        )

    return [chunk for chunk in chunks if chunk.strip()]


def _translate_chunk_opus(
    chunk: str,
    tokenizer: Any,
    model: Any,
    device: str,
    max_new_tokens: int,
) -> str:
    import torch

    max_input_tokens = int(os.getenv("ENNOSCHOLAR_OPUS_MAX_INPUT_TOKENS", "120"))

    inputs = tokenizer(
        chunk,
        return_tensors="pt",
        truncation=True,
        max_length=max_input_tokens,
    )

    inputs = {key: value.to(device) for key, value in inputs.items()}

    generation_kwargs = {
        "max_new_tokens": max_new_tokens,
        "num_beams": int(os.getenv("ENNOSCHOLAR_TRANSLATION_NUM_BEAMS", "4")),
        "length_penalty": float(os.getenv("ENNOSCHOLAR_TRANSLATION_LENGTH_PENALTY", "1.0")),
    }

    no_repeat = int(os.getenv("ENNOSCHOLAR_TRANSLATION_NO_REPEAT_NGRAM", "0"))
    if no_repeat > 0:
        generation_kwargs["no_repeat_ngram_size"] = no_repeat

    with torch.no_grad():
        generated_tokens = model.generate(
            **inputs,
            **generation_kwargs,
        )

    translated = tokenizer.batch_decode(
        generated_tokens,
        skip_special_tokens=True,
    )[0]

    return clean_translation(translated)


def _quality_guard(original: str, translated: str, chunks_count: int) -> tuple[bool, str]:
    """
    Vérifie que la traduction n'est pas manifestement tronquée.
    """
    src = _normalize_text(original)
    tgt = _normalize_text(translated)

    if not tgt:
        return False, "empty_translation"

    src_words = _word_count(src)
    tgt_words = _word_count(tgt)

    src_chars = len(src)
    tgt_chars = len(tgt)

    if src_words >= 80 and tgt_words < src_words * 0.50:
        return False, f"too_short_words src={src_words} tgt={tgt_words}"

    if src_chars >= 600 and tgt_chars < src_chars * 0.45:
        return False, f"too_short_chars src={src_chars} tgt={tgt_chars}"

    if chunks_count >= 4 and tgt_words < chunks_count * 8:
        return False, f"too_short_for_chunks chunks={chunks_count} tgt_words={tgt_words}"

    return True, "ok"


def _translate_with_opus_en_fr(
    original: str,
    device: str,
) -> dict[str, Any]:
    opus_model_name = (
        os.getenv("ENNOSCHOLAR_OPUS_MODEL")
        or os.getenv("ENNOSCHOLAR_TRANSLATION_OPUS_MODEL")
        or "Helsinki-NLP/opus-mt-tc-big-en-fr"
    ).strip()

    tokenizer, model = _load_opus_model(opus_model_name, device)

    try:
        max_input_tokens = int(os.getenv("ENNOSCHOLAR_OPUS_MAX_INPUT_TOKENS", "120"))
    except Exception:
        max_input_tokens = 120

    try:
        max_new_tokens = int(os.getenv("ENNOSCHOLAR_OPUS_MAX_NEW_TOKENS", "220"))
    except Exception:
        max_new_tokens = 220

    chunks = _build_translation_chunks(
        text=original,
        tokenizer=tokenizer,
        max_tokens=max_input_tokens,
    )

    if not chunks:
        raise RuntimeError("Impossible de découper l'abstract pour OPUS.")

    translations: list[str] = []
    chunk_report: list[dict[str, Any]] = []

    for index, chunk in enumerate(chunks, start=1):
        translated_chunk = _translate_chunk_opus(
            chunk=chunk,
            tokenizer=tokenizer,
            model=model,
            device=device,
            max_new_tokens=max_new_tokens,
        )

        if not translated_chunk:
            raise RuntimeError(f"Chunk {index}/{len(chunks)} traduit vide.")

        translations.append(translated_chunk)

        chunk_report.append(
            {
                "index": index,
                "source_chars": len(chunk),
                "source_words": _word_count(chunk),
                "translated_chars": len(translated_chunk),
                "translated_words": _word_count(translated_chunk),
            }
        )

    translated = clean_translation(" ".join(translations))

    ok, reason = _quality_guard(
        original=original,
        translated=translated,
        chunks_count=len(chunks),
    )

    if not ok:
        raise RuntimeError(f"Traduction OPUS rejetée : {reason}")

    return {
        "abstract_fr": translated,
        "provider": "opus",
        "model": opus_model_name,
        "device": device,
        "source_lang": "eng_Latn",
        "target_lang": "fra_Latn",
        "chunks_count": len(chunks),
        "chunk_report": chunk_report,
        "context_used": False,
        "prompt_mode": "opus_mt_sentence_by_sentence_translation",
        "quality_guard": reason,
        "already_target_language": False,
    }


def translate_abstract_to_french(
    abstract: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Traduction fidèle d'un abstract scientifique.

    Stratégie :
    - anglais -> français avec OPUS EN-FR ;
    - traduction phrase par phrase pour éviter les troncatures ;
    - pas d'analyse ;
    - pas de résumé ;
    - pas de reformulation en état de l'art.
    """
    original = _normalize_text(abstract)

    if not original:
        raise RuntimeError("Aucun abstract à traduire.")

    source_lang = _resolve_source_lang(original, context)
    target_lang = _resolve_target_lang()

    if source_lang == target_lang:
        return {
            "abstract_fr": original,
            "provider": "none",
            "model": None,
            "source_lang": source_lang,
            "target_lang": target_lang,
            "context_used": False,
            "prompt_mode": "already_target_language",
            "quality_guard": "ok",
            "already_target_language": True,
        }

    if not (source_lang == "eng_Latn" and target_lang == "fra_Latn"):
        raise RuntimeError(
            f"Traduction non supportée dans cette version : {source_lang} -> {target_lang}. "
            "Pour éviter les sorties NLLB mauvaises, seule la traduction anglais -> français avec OPUS est activée."
        )

    device = _resolve_device()

    return _translate_with_opus_en_fr(
        original=original,
        device=device,
    )