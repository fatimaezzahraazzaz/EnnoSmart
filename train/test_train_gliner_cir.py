from pathlib import Path
import json
import random
import re
import torch
import multiprocessing

from gliner import GLiNER

BASE_DIR = Path(r"C:\EnnoSmart")

TRAIN_PATH = BASE_DIR / "train" / "final_gliner_cir_dataset" / "gliner_cir_train.json"
VAL_PATH = BASE_DIR / "train" / "final_gliner_cir_dataset" / "gliner_cir_val.json"

OUTPUT_DIR = BASE_DIR / "train" / "models" / "gliner_cir_test_run"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

MODEL_NAME = "urchade/gliner_multi-v2.1"

MAX_TRAIN_ITEMS = 120
MAX_VAL_ITEMS = 30

RANDOM_SEED = 42
random.seed(RANDOM_SEED)


def load_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def simple_tokenize_with_offsets(text: str):
    tokens = []
    offsets = []

    for match in re.finditer(r"\w+|[^\w\s]", text, flags=re.UNICODE):
        tokens.append(match.group(0))
        offsets.append((match.start(), match.end()))

    return tokens, offsets


def char_span_to_token_span(start: int, end: int, offsets):
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
    converted = []

    for item in items:
        text = item.get("text", "")
        if not text.strip():
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
                continue

            if not label or start < 0 or end <= start or end > len(text):
                continue

            token_span = char_span_to_token_span(start, end, offsets)

            if token_span is None:
                continue

            ts, te = token_span

            if ts < 0 or te >= len(tokens) or te < ts:
                continue

            ner.append([ts, te, label])

        if ner:
            converted.append({
                "tokenized_text": tokens,
                "ner": ner,
            })

    return converted


def main():
    print("CUDA available:", torch.cuda.is_available())

    if torch.cuda.is_available():
        print("GPU:", torch.cuda.get_device_name(0))
    else:
        print("CUDA non disponible, training très lent sur CPU.")

    print("Loading data...")
    train_raw = load_json(TRAIN_PATH)
    val_raw = load_json(VAL_PATH)

    random.shuffle(train_raw)
    random.shuffle(val_raw)

    train_raw = train_raw[:MAX_TRAIN_ITEMS]
    val_raw = val_raw[:MAX_VAL_ITEMS]

    train_data = convert_to_gliner_train_format(train_raw)
    val_data = convert_to_gliner_train_format(val_raw)

    print(f"Train examples: {len(train_data)}")
    print(f"Val examples  : {len(val_data)}")

    if len(train_data) == 0:
        raise RuntimeError("Aucun exemple train valide après conversion.")

    print("Loading model...")
    model = GLiNER.from_pretrained(MODEL_NAME)

    print("Starting test training...")

    trainer = model.train_model(
        train_dataset=train_data,
        eval_dataset=val_data,
        output_dir=str(OUTPUT_DIR),

        max_steps=30,
        per_device_train_batch_size=1,
        per_device_eval_batch_size=1,

        learning_rate=1e-5,
        others_lr=5e-5,
        weight_decay=0.01,

        save_steps=30,
        save_total_limit=1,
        logging_steps=5,

        dataloader_num_workers=0,
        report_to="none",
    )

    print("Saving model...")
    trainer.save_model(str(OUTPUT_DIR / "final_model"))

    print("\n✅ Test training terminé")
    print(f"Model saved to: {OUTPUT_DIR / 'final_model'}")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()