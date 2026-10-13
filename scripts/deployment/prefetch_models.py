"""Précharge les modèles de la logique actuelle, sans diagnostic ni appel LLM."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Mapping


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
NLI_MODEL = "MoritzLaurer/multilingual-MiniLMv2-L6-mnli-xnli"


def enabled(env: Mapping[str, str], name: str, default: str = "1") -> bool:
    return env.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def planned_models(env: Mapping[str, str], *, core_only: bool = False) -> list[tuple[str, str]]:
    models = [("embeddings", env.get("ENNOSMART_EMBEDDING_MODEL") or
               "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")]
    # Le regroupement final est LLM ; le Frascati amont actuel appelle encore
    # semantic_lock_finalizer. Ce modèle sert aussi au validateur EnnoScholar.
    if enabled(env, "ENNOSMART_PRELOAD_NLI"):
        models.append(("nlp_nli", NLI_MODEL))
        editorial_model = env.get("ENNOSCHOLAR_EDITORIAL_NLI_MODEL") or NLI_MODEL
        if editorial_model != NLI_MODEL:
            models.append(("editorial_nli", editorial_model))
    if core_only:
        return models
    if enabled(env, "ENNOSCHOLAR_ENABLE_BGE_RERANKER"):
        models.append(("reranker", env.get("ENNOSCHOLAR_RERANKER_MODEL") or "BAAI/bge-reranker-v2-m3"))
    if enabled(env, "ENNOSMART_RUN_AI_DETECTOR"):
        models.append(("ai_detector", env.get("AI_DETECTOR_MODEL") or
                       "AICodexLab/answerdotai-ModernBERT-base-ai-detector"))
    if enabled(env, "ENNOSMART_PRELOAD_TRANSLATION"):
        models.append(("translation", env.get("ENNOSCHOLAR_OPUS_MODEL") or
                       env.get("ENNOSCHOLAR_TRANSLATION_OPUS_MODEL") or
                       "Helsinki-NLP/opus-mt-tc-big-en-fr"))
    return models


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core-only", action="store_true", help="Embeddings et NLI actifs uniquement")
    parser.add_argument("--offline", action="store_true", help="Vérifier le cache, sans téléchargement")
    args = parser.parse_args()
    from huggingface_hub import constants, snapshot_download

    cache = Path(constants.HF_HUB_CACHE)
    cache.mkdir(parents=True, exist_ok=True)
    results = []
    for role, model in planned_models(os.environ, core_only=args.core_only):
        target_cache = Path(os.getenv("ENNOSMART_NLI_CACHE_DIR") or cache) if role == "nlp_nli" else cache
        print(f"[models-init] {role}: {model}", flush=True)
        local = Path(model).is_dir()
        snapshot = str(Path(model).resolve()) if local else snapshot_download(
            repo_id=model,
            cache_dir=str(target_cache),
            local_files_only=args.offline,
            # Les runtimes actifs utilisent PyTorch, pas les exports TF/ONNX.
            ignore_patterns=["onnx/*", "openvino/*", "*.onnx", "*.h5", "*.msgpack", "rust_model.ot"],
        )
        results.append({"role": role, "model": model, "snapshot": snapshot})

    if not args.core_only and enabled(os.environ, "ENNOSMART_ENABLE_TRANSCRIPTION"):
        from faster_whisper.utils import download_model

        model = os.getenv("TRANSCRIPTION_MODEL", "small").strip() or "small"
        print(f"[models-init] transcription: {model}", flush=True)
        snapshot = str(Path(model).resolve()) if Path(model).is_dir() else download_model(
            model, cache_dir=str(cache), local_files_only=args.offline,
        )
        results.append({"role": "transcription", "model": model, "snapshot": snapshot})

    # Vérifier réellement les deux modèles nécessaires au pipeline NLP/RAG.
    from sentence_transformers import SentenceTransformer
    embedding = next(item for item in results if item["role"] == "embeddings")
    model = SentenceTransformer(embedding["snapshot"], local_files_only=True, device="cpu")
    model.encode(["Vérification des embeddings EnnoSmart"], show_progress_bar=False)
    del model
    if any(item["role"] == "nlp_nli" for item in results):
        from modules.NLP.semantic_lock_adjudicator import SemanticLockAdjudicator
        nli = next(item for item in results if item["role"] == "nlp_nli")
        judge = SemanticLockAdjudicator(model_name=nli["snapshot"])
        del judge

    (cache.parent / "ennosmart-preloaded-models.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print("MODEL_PRELOAD_OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
