from pathlib import Path
import json
import random
import re
import torch
import multiprocessing

from gliner import GLiNER

# ============================================================
# CONFIG
# ============================================================

BASE_DIR = Path(r"C:\EnnoSmart")

TRAIN_PATH = BASE_DIR / "train" / "final_gliner_cir_dataset" / "gliner_cir_train.json"
VAL_PATH = BASE_DIR / "train" / "final_gliner_cir_dataset" / "gliner_cir_val.json"
TEST_PATH = BASE_DIR / "train" / "final_gliner_cir_dataset" / "gliner_cir_test.json"

OUTPUT_DIR = BASE_DIR / "train" / "models" / "gliner_cir_final_multi_v21"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

MODEL_NAME = "urchade/gliner_multi-v2.1"

RANDOM_SEED = 42
random.seed(RANDOM_SEED)

# Training final prudent pour NVIDIA RTX 1000 Ada Laptop
MAX_STEPS = 800
BATCH_SIZE = 1
EVAL_BATCH_SIZE = 1

LEARNING_RATE = 1e-5
OTHERS_LR = 5e-5
WEIGHT_DECAY = 0.01

SAVE_STEPS = 100
LOGGING_STEPS = 20

# Important sous Windows
DATALOADER_NUM_WORKERS = 0


# ============================================================
# UTILS
# ============================================================

def load_json(path: Path):
    if not path.exists():
        raise FileNotFoundError(f"Fichier introuvable : {path}")

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def simple_tokenize_with_offsets(text: str):
    """
    Tokenisation simple avec offsets caractères.
    GLiNER train_model attend tokenized_text + ner en indices tokens.
    """
    tokens = []
    offsets = []

    for match in re.finditer(r"\w+|[^\w\s]", text, flags=re.UNICODE):
        tokens.append(match.group(0))
        offsets.append((match.start(), match.end()))

    return tokens, offsets


def char_span_to_token_span(start: int, end: int, offsets):
    """
    Convertit start/end caractères vers start/end tokens inclusifs.
    """
    token_start = None
    token_end = None

    for i, (tok_start, tok_end) in enumerate(offsets):
        if tok_end > start and tok_start < end:
            if token_start is None:
                token_start = i
            token_end = i

    if token_start is None or token_end is None:
        return None

    return token_start, token_end


def convert_to_gliner_train_format(items):
    """
    Input:
    {
      "text": "...",
      "entities": [
        {"start": 0, "end": 10, "label": "METHODE_RD", "text": "..."}
      ]
    }

    Output:
    {
      "tokenized_text": ["..."],
      "ner": [[token_start, token_end, "METHODE_RD"]]
    }
    """
    converted = []
    skipped_no_entities = 0
    skipped_bad_spans = 0

    for item in items:
        text = item.get("text", "")

        if not text or not text.strip():
            continue

        tokens, offsets = simple_tokenize_with_offsets(text)

        if not tokens:
            continue

        ner = []

        for ent in item.get("entities", []):
            start = ent.get("start")
            end = ent.get("end")
            label = ent.get("label")

            if not isinstance(start, int) or not isinstance(end, int):
                skipped_bad_spans += 1
                continue

            if not label or start < 0 or end <= start or end > len(text):
                skipped_bad_spans += 1
                continue

            token_span = char_span_to_token_span(start, end, offsets)

            if token_span is None:
                skipped_bad_spans += 1
                continue

            ts, te = token_span

            if ts < 0 or te >= len(tokens) or te < ts:
                skipped_bad_spans += 1
                continue

            ner.append([ts, te, label])

        if ner:
            converted.append({
                "tokenized_text": tokens,
                "ner": ner,
            })
        else:
            skipped_no_entities += 1

    return converted, {
        "converted": len(converted),
        "skipped_no_entities": skipped_no_entities,
        "skipped_bad_spans": skipped_bad_spans,
    }


def save_training_report(path: Path, report: dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)


# ============================================================
# MAIN
# ============================================================

def main():
    print("\n🚀 Fine-tuning GLiNER CIR final")
    print("=" * 80)

    print("CUDA available:", torch.cuda.is_available())

    if torch.cuda.is_available():
        print("GPU:", torch.cuda.get_device_name(0))
        print("CUDA device count:", torch.cuda.device_count())
    else:
        print("⚠️ CUDA non disponible. Le training sera très lent sur CPU.")

    print("\nLoading dataset...")
    train_raw = load_json(TRAIN_PATH)
    val_raw = load_json(VAL_PATH)
    test_raw = load_json(TEST_PATH)

    print(f"Raw train items : {len(train_raw)}")
    print(f"Raw val items   : {len(val_raw)}")
    print(f"Raw test items  : {len(test_raw)}")

    random.shuffle(train_raw)
    random.shuffle(val_raw)

    train_data, train_stats = convert_to_gliner_train_format(train_raw)
    val_data, val_stats = convert_to_gliner_train_format(val_raw)
    test_data, test_stats = convert_to_gliner_train_format(test_raw)

    print("\nConverted dataset:")
    print(f"Train examples : {len(train_data)}")
    print(f"Val examples   : {len(val_data)}")
    print(f"Test examples  : {len(test_data)}")

    if len(train_data) == 0:
        raise RuntimeError("Aucun exemple train valide après conversion.")

    if len(val_data) == 0:
        raise RuntimeError("Aucun exemple validation valide après conversion.")

    print("\nLoading model...")
    model = GLiNER.from_pretrained(MODEL_NAME)

    print("\nStarting final training...")
    print(f"Model      : {MODEL_NAME}")
    print(f"Max steps  : {MAX_STEPS}")
    print(f"Batch size : {BATCH_SIZE}")
    print(f"Output     : {OUTPUT_DIR}")

    trainer = model.train_model(
        train_dataset=train_data,
        eval_dataset=val_data,
        output_dir=str(OUTPUT_DIR),

        # Training
        max_steps=MAX_STEPS,
        per_device_train_batch_size=BATCH_SIZE,
        per_device_eval_batch_size=EVAL_BATCH_SIZE,

        learning_rate=LEARNING_RATE,
        others_lr=OTHERS_LR,
        weight_decay=WEIGHT_DECAY,

        # Save / logs
        save_steps=SAVE_STEPS,
        save_total_limit=3,
        logging_steps=LOGGING_STEPS,

        # Windows / stability
        dataloader_num_workers=DATALOADER_NUM_WORKERS,
        report_to="none",
    )

    final_model_dir = OUTPUT_DIR / "final_model"
    final_model_dir.mkdir(parents=True, exist_ok=True)

    print("\nSaving final model...")
    trainer.save_model(str(final_model_dir))

    report = {
        "model_name": MODEL_NAME,
        "output_dir": str(OUTPUT_DIR),
        "final_model_dir": str(final_model_dir),
        "train_path": str(TRAIN_PATH),
        "val_path": str(VAL_PATH),
        "test_path": str(TEST_PATH),
        "raw_counts": {
            "train": len(train_raw),
            "val": len(val_raw),
            "test": len(test_raw),
        },
        "converted_counts": {
            "train": len(train_data),
            "val": len(val_data),
            "test": len(test_data),
        },
        "conversion_stats": {
            "train": train_stats,
            "val": val_stats,
            "test": test_stats,
        },
        "training_config": {
            "max_steps": MAX_STEPS,
            "batch_size": BATCH_SIZE,
            "eval_batch_size": EVAL_BATCH_SIZE,
            "learning_rate": LEARNING_RATE,
            "others_lr": OTHERS_LR,
            "weight_decay": WEIGHT_DECAY,
            "save_steps": SAVE_STEPS,
            "logging_steps": LOGGING_STEPS,
            "dataloader_num_workers": DATALOADER_NUM_WORKERS,
        },
        "cuda": {
            "available": torch.cuda.is_available(),
            "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        }
    }

    save_training_report(OUTPUT_DIR / "training_report.json", report)

    print("\n✅ Fine-tuning final terminé")
    print(f"Final model saved to : {final_model_dir}")
    print(f"Training report      : {OUTPUT_DIR / 'training_report.json'}")
    print("=" * 80)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()