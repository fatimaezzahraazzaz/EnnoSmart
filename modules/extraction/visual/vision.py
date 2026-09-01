"""
modules/extraction/visual/vision.py — EnnoSmart v6.0

Optimisations vitesse + qualité RAG :
  1. Qwen chargé UNE SEULE FOIS (singleton persistant en mémoire)
  2. Prompts courts → max 80 mots utiles pour le RAG (pas de roman)
  3. max_new_tokens réduit à 200 (suffisant pour RAG)
  4. Cache disque persistant (2ème traitement quasi instantané)
  5. Parallélisme 2 workers CUDA
  6. Fallback Ollama si Qwen échoue

Backends :
  PRIMARY  → Qwen2-VL NVIDIA CUDA int4 NF4
  FALLBACK → Ollama llama3.2-vision (déjà en mémoire)
"""
from __future__ import annotations

import base64, io, logging, time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from PIL import Image
from modules.extraction.visual.image import ProcessedImage, VisualType
from modules.common.runtime_paths import cache_root

logger = logging.getLogger(__name__)

QWEN_MODEL_ID = "Qwen/Qwen2-VL-7B-Instruct"
LLAMA_MODEL   = "llama3.2-vision"

# ── Réduit de 500 → 150 tokens : descriptions courtes, denses, RAG-ready
MAX_TOKENS    = 120
FIXED_SIZE    = 448

# Garde-fous : la vision reste disponible, mais bornée.
VISION_TIMEOUT_SECONDS = 60
MAX_IMAGES_PER_BATCH = 3

CACHE_DIR = cache_root() / "vision"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Singletons — chargés une seule fois, jamais rechargés
_BACKEND: Optional["VisionBackend"] = None
_QWEN_MODEL  = None
_QWEN_PROC   = None
_QWEN_LOADED = False   # Guard pour éviter double chargement


class VisionBackend(str, Enum):
    QWEN_CUDA = "qwen_cuda"
    OLLAMA    = "ollama"
    NONE      = "none"

class DescriptionQuality(str, Enum):
    FULL      = "full"
    HEURISTIC = "heuristic"
    FAILED    = "failed"

@dataclass
class VisionResult:
    image_hash: str; source_path: Optional[str]
    page_number: Optional[int]; slide_number: Optional[int]
    visual_type: VisualType; description: str
    quality: DescriptionQuality; backend_used: VisionBackend
    processing_time_ms: int; token_count_estimate: int
    tags: list[str] = field(default_factory=list)
    error: Optional[str] = None


# ══════════════════════════════════════════════════════════════════════════════
# PROMPTS COURTS — RAG-FIRST
# Description dense et précise, max 80 mots utiles
# Pas de répétition de la question, pas de conclusion générique
# ══════════════════════════════════════════════════════════════════════════════

_PROMPTS: dict[VisualType, str] = {
    VisualType.SCHEMA_TECHNIQUE:
        "{context}"
        "En 2 phrases professionnelles en français : "
        "décris ce schéma R&D (type, composants clés, flux) "
        "et son rôle dans le projet. Sois précis, factuel, pas de généralité.",

    VisualType.GRAPHIQUE:
        "{context}"
        "En 2 phrases professionnelles en français : "
        "décris ce graphique (axes, unités, valeurs clés, tendance) "
        "et ce qu'il démontre pour la R&D. Chiffres exacts obligatoires.",

    VisualType.TABLEAU_IMAGE:
        "{context}"
        "En 2 phrases professionnelles en français : "
        "transcris les données clés de ce tableau (colonnes, valeurs importantes) "
        "et leur signification technique.",

    VisualType.EQUATION:
        "{context}"
        "En 1 phrase : transcris la formule exacte avec ses variables et unités SI.",

    VisualType.PHOTO:
        "{context}"
        "En 2 phrases professionnelles en français : "
        "décris ce que montre cette photo (équipement, matériau, protocole) "
        "et sa pertinence pour la R&D.",

    VisualType.CAPTURE_ECRAN:
        "{context}"
        "En 2 phrases professionnelles en français : "
        "décris ce logiciel/interface (nom si visible, données affichées, paramètres clés) "
        "et son usage dans le projet.",

    VisualType.PLAN:
        "{context}"
        "En 2 phrases professionnelles en français : "
        "décris ce plan technique (type, composants, dimensions si visibles) "
        "et sa fonction dans le projet.",

    VisualType.INCONNU:
        "{context}"
        "En 2 phrases professionnelles en français : "
        "identifie ce visuel et explique sa pertinence pour ce projet R&D.",
}


def _get_prompt(visual_type: VisualType, context_hint: str = "") -> str:
    """Retourne le prompt enrichi avec le contexte slide/page/formules."""
    ctx = f"Contexte : {context_hint}\n" if context_hint else ""
    return _PROMPTS[visual_type].format(context=ctx)


# ── Cache disque ──────────────────────────────────────────────────────────────

def _cache_get(h: str) -> Optional[str]:
    f = CACHE_DIR / f"{h}.txt"
    return f.read_text(encoding="utf-8") if f.exists() else None

def _cache_set(h: str, desc: str):
    try: (CACHE_DIR / f"{h}.txt").write_text(desc, encoding="utf-8")
    except Exception: pass


# ── Utilitaires ───────────────────────────────────────────────────────────────

def _to_fixed(image_bytes: bytes) -> bytes:
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        img.thumbnail((FIXED_SIZE, FIXED_SIZE), Image.LANCZOS)
        canvas = Image.new("RGB", (FIXED_SIZE, FIXED_SIZE), (255, 255, 255))
        canvas.paste(img, ((FIXED_SIZE-img.width)//2, (FIXED_SIZE-img.height)//2))
        buf = io.BytesIO(); canvas.save(buf, "PNG"); return buf.getvalue()
    except Exception:
        return image_bytes

def _clean(text: str) -> str:
    """Anti-boucle + nettoyage minimal."""
    lines, seen, out = text.split("\n"), set(), []
    for l in lines:
        s = l.strip()
        if s and s in seen: break
        seen.add(s); out.append(l)
    # Supprimer les formules de politesse finales inutiles
    result = "\n".join(out).strip()
    for ending in ["Cordialement,", "Merci pour", "N'hésitez pas", "Je suis prêt"]:
        if ending in result:
            result = result[:result.index(ending)].strip()
    return result

def _valid(text: str) -> tuple[bool, str]:
    w = text.split()
    if len(w) < 10: return False, f"trop court ({len(w)} mots)"
    if len(set(w))/len(w) < 0.30: return False, "boucle détectée"
    return True, "ok"


# ── Détection backend ─────────────────────────────────────────────────────────

def _detect_backend() -> VisionBackend:
    global _BACKEND
    if _BACKEND:
        return _BACKEND
    try:
        import torch
        if torch.cuda.is_available():
            vram = torch.cuda.get_device_properties(0).total_memory / 1e9
            name = torch.cuda.get_device_name(0)
            if vram >= 4.0:
                logger.info("Backend : Qwen2-VL CUDA sur %s (%.1f Go)", name, vram)
                _BACKEND = VisionBackend.QWEN_CUDA
                return _BACKEND
    except Exception:
        pass
    try:
        import ollama as sdk
        if any(LLAMA_MODEL in m.model for m in sdk.list().models):
            logger.info("Backend : Ollama %s", LLAMA_MODEL)
            _BACKEND = VisionBackend.OLLAMA
            return _BACKEND
    except Exception:
        pass
    _BACKEND = VisionBackend.NONE
    return _BACKEND


# ══════════════════════════════════════════════════════════════════════════════
# CHARGEMENT QWEN — SINGLETON (une seule fois, jamais rechargé)
# ══════════════════════════════════════════════════════════════════════════════

def load_qwen_once() -> bool:
    """
    Charge Qwen2-VL en mémoire GPU une seule fois.
    Appeler AU DÉMARRAGE de l'application ou du service,
    avant tout traitement de fichier.

    Retourne True si chargé avec succès.
    """
    global _QWEN_MODEL, _QWEN_PROC, _QWEN_LOADED
    if _QWEN_LOADED:
        logger.debug("Qwen déjà chargé — skip")
        return True
    try:
        import torch
        from transformers import (
            Qwen2VLForConditionalGeneration,
            AutoProcessor,
            BitsAndBytesConfig,
        )

        logger.info("=" * 50)
        logger.info("Chargement Qwen2-VL (unique, persistant)…")
        logger.info("Ce message apparaît UNE SEULE FOIS par session.")
        logger.info("=" * 50)

        # Config optimale RTX 1000 6Go — 20s/image mesurée
        quant = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.float16,
            bnb_4bit_use_double_quant=False,  # False = plus rapide (20s vs 25s)
            bnb_4bit_quant_type="nf4",
        )
        _QWEN_PROC = AutoProcessor.from_pretrained(QWEN_MODEL_ID)
        _QWEN_MODEL = Qwen2VLForConditionalGeneration.from_pretrained(
            QWEN_MODEL_ID,
            quantization_config=quant,
            device_map="cuda",
            torch_dtype=torch.float16,
        )
        _QWEN_MODEL.eval()
        _QWEN_LOADED = True
        logger.info("✅ Qwen2-VL prêt — modèle en mémoire GPU")
        return True

    except Exception as exc:
        logger.warning("Chargement Qwen échoué : %s → Ollama sera utilisé", exc)
        global _BACKEND
        _BACKEND = VisionBackend.OLLAMA
        return False


def warmup_vision_backend() -> bool:
    """
    Pré-charge Qwen si CUDA disponible.
    Appeler une fois avant extract_batch().
    """
    backend = _detect_backend()
    if backend == VisionBackend.QWEN_CUDA:
        return load_qwen_once()
    if backend == VisionBackend.OLLAMA:
        logger.info("Ollama prêt (pas de warmup nécessaire)")
        return True
    return False


# ── Inférence Qwen CUDA ───────────────────────────────────────────────────────

def _call_qwen(image_bytes: bytes, visual_type: VisualType, context_hint: str = "") -> tuple[str, int]:
    """
    Inférence Qwen2-VL — utilise le singleton déjà en mémoire.
    Ne recharge JAMAIS le modèle.
    """
    import torch

    if not _QWEN_LOADED:
        load_qwen_once()

    fixed = _to_fixed(image_bytes)
    img   = Image.open(io.BytesIO(fixed)).convert("RGB")

    messages = [{"role": "user", "content": [
        {"type": "image", "image": img},
        {"type": "text",  "text":  _get_prompt(visual_type, context_hint)},
    ]}]

    text   = _QWEN_PROC.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )
    inputs = _QWEN_PROC(
        text=[text], images=[img], padding=True, return_tensors="pt"
    ).to("cuda")

    t0 = time.time()
    with torch.no_grad():
        output_ids = _QWEN_MODEL.generate(
            **inputs,
            max_new_tokens=120,         # 120 tokens = ~90 mots, 2 phrases complètes
            do_sample=False,
            temperature=None,           # désactivé explicitement (évite le warning)
            top_p=None,                 # désactivé explicitement
            top_k=None,                 # désactivé explicitement
            repetition_penalty=1.1,
            pad_token_id=_QWEN_PROC.tokenizer.eos_token_id,
        )
    elapsed = int((time.time() - t0) * 1000)

    trimmed = [o[len(i):] for i, o in zip(inputs.input_ids, output_ids)]
    raw = _QWEN_PROC.batch_decode(trimmed, skip_special_tokens=True)[0].strip()

    # Libérer la mémoire inputs (pas le modèle !)
    del inputs, output_ids, trimmed
    torch.cuda.empty_cache()

    return _clean(raw), elapsed


# ── Inférence Ollama ─────────────────────────────────────────────────────────

def _call_ollama(image_bytes: bytes, visual_type: VisualType, context_hint: str = "") -> tuple[str, int]:
    import ollama as sdk
    b64 = base64.b64encode(_to_fixed(image_bytes)).decode()
    t0  = time.time()
    r   = sdk.chat(
        model=LLAMA_MODEL,
        messages=[{"role":"user","content":_get_prompt(visual_type, context_hint),"images":[b64]}],
        options={"temperature":0.1,"num_predict":MAX_TOKENS,"repeat_penalty":1.3},
    )
    return _clean(r.message.content.strip()), int((time.time()-t0)*1000)


# ── Traitement d'une image ────────────────────────────────────────────────────

def _process_one(processed: ProcessedImage, backend: VisionBackend, context_hint: str = "") -> VisionResult:
    # Cache disque — instantané
    cached = _cache_get(processed.image_hash) if processed.image_hash else None
    if cached:
        loc  = (f" | PAGE {processed.page_number}" if processed.page_number
                else f" | SLIDE {processed.slide_number}" if processed.slide_number else "")
        r = VisionResult(
            processed.image_hash, processed.source_path, processed.page_number,
            processed.slide_number, processed.visual_type,
            f"[IMAGE | {processed.visual_type.value}{loc}]\n[QUALITÉ: full | cache]\n\n{cached}",
            DescriptionQuality.FULL, backend, 0, len(cached.split()),
        )
        r.tags = [f"VISION:{r.visual_type.value.upper()}", "FROM_CACHE_DISK"]
        return r

    raw, elapsed, error, quality = "", 0, None, DescriptionQuality.HEURISTIC
    used = VisionBackend.NONE

    # Essai Qwen → fallback Ollama
    chain = ([(VisionBackend.QWEN_CUDA, _call_qwen),
              (VisionBackend.OLLAMA,    _call_ollama)]
             if backend == VisionBackend.QWEN_CUDA
             else [(VisionBackend.OLLAMA, _call_ollama)])

    for b, fn in chain:
        try:
            raw, elapsed = fn(processed.image_bytes, processed.visual_type, context_hint)
            ok, reason   = _valid(raw)
            if not ok: raise ValueError(reason)
            quality = DescriptionQuality.FULL
            used    = b
            _cache_set(processed.image_hash, raw)
            logger.info("✓ %s %dms | %d mots | slide=%s",
                        b.value, elapsed, len(raw.split()), processed.slide_number)
            break
        except Exception as exc:
            logger.warning("Vision %s : %s", b.value, exc)
            error, raw = str(exc), ""

    if not raw:
        raw = (f"[LLM indisponible] {processed.visual_type.value} "
               f"| {processed.width}×{processed.height}px")

    loc  = (f" | PAGE {processed.page_number}" if processed.page_number
            else f" | SLIDE {processed.slide_number}" if processed.slide_number else "")
    desc = f"[IMAGE | {processed.visual_type.value}{loc}]\n[QUALITÉ: {quality.value}]\n\n{raw}"
    r = VisionResult(
        processed.image_hash, processed.source_path, processed.page_number,
        processed.slide_number, processed.visual_type, desc, quality,
        used, elapsed, len(raw.split()), error=error,
    )
    r.tags = [f"VISION:{r.visual_type.value.upper()}", f"BACKEND:{used.value.upper()}",
              f"QUALITY:{quality.value.upper()}"]
    return r


# ── Points d'entrée ───────────────────────────────────────────────────────────


def _timeout_result(processed: ProcessedImage, backend: VisionBackend, reason: str) -> VisionResult:
    """Résultat de secours quand une image dépasse le temps autorisé."""
    r = VisionResult(
        processed.image_hash,
        processed.source_path,
        processed.page_number,
        processed.slide_number,
        processed.visual_type,
        "",
        DescriptionQuality.FAILED,
        backend,
        VISION_TIMEOUT_SECONDS * 1000,
        0,
        error=reason,
    )
    r.tags = [
        f"VISION:{processed.visual_type.value.upper()}",
        "SKIP_TIMEOUT",
        f"TIMEOUT:{VISION_TIMEOUT_SECONDS}s",
    ]
    return r

def describe_image(processed: ProcessedImage, force_heuristic: bool = False) -> VisionResult:
    if processed.skip_vision:
        r = VisionResult(
            processed.image_hash, processed.source_path, processed.page_number,
            processed.slide_number, processed.visual_type, "",
            DescriptionQuality.FAILED, VisionBackend.NONE, 0, 0, error=processed.skip_reason,
        )
        r.tags = [f"VISION:{r.visual_type.value.upper()}", "SKIP_VISION"]
        return r
    return _process_one(processed,
                        VisionBackend.NONE if force_heuristic else _detect_backend())


def describe_image_batch(
    processed_images: list[ProcessedImage],
    force_heuristic: bool = False,
    context_hints: Optional[dict[int, str]] = None,
) -> list[VisionResult]:
    """
    Batch vision borné.

    Règles :
    - La vision est désactivée par défaut côté extraction router.
    - Si activée, on traite au maximum MAX_IMAGES_PER_BATCH images.
    - Chaque image a un timeout pipeline de VISION_TIMEOUT_SECONDS.

    Note : Python ne peut pas tuer proprement un appel CUDA déjà lancé dans un thread.
    Ce timeout empêche le pipeline d'attendre indéfiniment et renvoie SKIP_TIMEOUT.
    """
    backend = VisionBackend.NONE if force_heuristic else _detect_backend()

    if not processed_images:
        return []

    original_count = len(processed_images)
    processed_images = processed_images[:MAX_IMAGES_PER_BATCH]
    if original_count > len(processed_images):
        logger.info(
            "Vision batch limité : %d images reçues → %d traitées",
            original_count,
            len(processed_images),
        )

    results: dict[int, VisionResult] = {}
    remaining = []

    for p in processed_images:
        if p.skip_vision:
            r = VisionResult(
                p.image_hash, p.source_path, p.page_number, p.slide_number,
                p.visual_type, "", DescriptionQuality.FAILED, VisionBackend.NONE,
                0, 0, error=p.skip_reason,
            )
            r.tags = [f"VISION:{r.visual_type.value.upper()}", "SKIP_VISION"]
            results[id(p)] = r
            continue

        cached = _cache_get(p.image_hash) if p.image_hash else None
        if cached:
            loc = (
                f" | PAGE {p.page_number}" if p.page_number
                else f" | SLIDE {p.slide_number}" if p.slide_number else ""
            )
            r = VisionResult(
                p.image_hash, p.source_path, p.page_number, p.slide_number, p.visual_type,
                f"[IMAGE | {p.visual_type.value}{loc}]\n[QUALITÉ: full | cache]\n\n{cached}",
                DescriptionQuality.FULL, backend, 0, len(cached.split()),
            )
            r.tags = [f"VISION:{r.visual_type.value.upper()}", "FROM_CACHE_DISK"]
            results[id(p)] = r
        else:
            remaining.append(p)

    logger.info(
        "Batch vision : %d total | %d cache/skip | %d à traiter | timeout=%ss",
        len(processed_images),
        len(processed_images) - len(remaining),
        len(remaining),
        VISION_TIMEOUT_SECONDS,
    )

    if remaining:
        workers = 1 if backend == VisionBackend.QWEN_CUDA else 2
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {
                ex.submit(
                    _process_one,
                    p,
                    backend,
                    (context_hints or {}).get(id(p), ""),
                ): p
                for p in remaining
            }

            pending = set(futures.keys())
            started_at = {future: time.time() for future in pending}

            while pending:
                done, pending = wait(pending, timeout=1, return_when=FIRST_COMPLETED)

                for future in done:
                    p = futures[future]
                    try:
                        results[id(p)] = future.result()
                    except Exception as exc:
                        r = VisionResult(
                            p.image_hash, p.source_path, p.page_number, p.slide_number,
                            p.visual_type, "", DescriptionQuality.FAILED,
                            VisionBackend.NONE, 0, 0, error=str(exc),
                        )
                        r.tags = ["VISION_ERROR"]
                        results[id(p)] = r

                now = time.time()
                timed_out = [
                    future for future in list(pending)
                    if now - started_at.get(future, now) >= VISION_TIMEOUT_SECONDS
                ]
                for future in timed_out:
                    p = futures[future]
                    future.cancel()
                    results[id(p)] = _timeout_result(
                        p,
                        backend,
                        f"Vision timeout après {VISION_TIMEOUT_SECONDS}s",
                    )
                    pending.remove(future)
                    logger.warning(
                        "Vision timeout : image_hash=%s type=%s slide=%s page=%s",
                        p.image_hash,
                        p.visual_type.value,
                        p.slide_number,
                        p.page_number,
                    )

    return [results[id(p)] for p in processed_images]
