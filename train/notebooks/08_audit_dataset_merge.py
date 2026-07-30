import json
from pathlib import Path
from collections import Counter, defaultdict

BASE_DIR = Path(r"C:\EnnoSmart")
DATASET_DIR = BASE_DIR / "dataset_final"

CHAR_FILE = DATASET_DIR / "gliner_dataset_char_based.json"
TOKEN_FILE = DATASET_DIR / "gliner_dataset_complete.json"

LABELS_CORE = [
    "VERROU_TECH",
    "METHODE_RD",
    "TECHNOLOGIE_RD",
    "EQUIPEMENT_RD",
    "COMPOSANT_TECHNIQUE",
    "MATERIAU_SPECIFIQUE",
    "DOMAINE_RD",
    "RESULTAT_RD",
    "OBJECTIF_RD",
]


def norm(x: str) -> str:
    return " ".join((x or "").lower().strip().split())


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    print("=" * 80)
    print("AUDIT MERGE DATASET : CHAR-BASED vs TOKEN-BASED")
    print("=" * 80)

    char_data = load_json(CHAR_FILE)
    token_data = load_json(TOKEN_FILE)

    char_by_id = {
        item["metadata"]["chunk_id"]: item
        for item in char_data
    }

    token_by_id = {
        item["chunk_id"]: item
        for item in token_data
    }

    print(f"\nChar-based chunks  : {len(char_by_id)}")
    print(f"Token-based chunks : {len(token_by_id)}")

    missing_in_token = set(char_by_id) - set(token_by_id)
    missing_in_char = set(token_by_id) - set(char_by_id)

    print(f"Missing in token : {len(missing_in_token)}")
    print(f"Missing in char  : {len(missing_in_char)}")

    total_char_entities = 0
    total_token_entities = 0

    exact_matches = 0
    weak_matches = 0
    no_matches = 0

    label_counter_char = Counter()
    label_counter_token = Counter()
    bad_examples = []

    long_entities = []
    suspicious_single_tokens = []

    for chunk_id, token_item in token_by_id.items():
        if chunk_id not in char_by_id:
            continue

        char_item = char_by_id[chunk_id]

        char_entities = char_item.get("entities", [])
        tokens = token_item.get("tokenized_text", [])
        ner = token_item.get("ner", [])

        char_set = set()
        char_texts_by_label = defaultdict(list)

        for ent in char_entities:
            text = ent.get("text", "")
            label = ent.get("label", "")

            total_char_entities += 1
            label_counter_char[label] += 1

            char_set.add((label, norm(text)))
            char_texts_by_label[label].append(text)

            if len(text.split()) > 12:
                long_entities.append({
                    "chunk_id": chunk_id,
                    "label": label,
                    "text": text,
                    "n_words": len(text.split()),
                })

            if len(text.split()) == 1 and label in {
                "VERROU_TECH", "OBJECTIF_RD", "METHODE_RD", "RESULTAT_RD"
            }:
                suspicious_single_tokens.append({
                    "chunk_id": chunk_id,
                    "label": label,
                    "text": text,
                })

        for span in ner:
            if len(span) < 3:
                continue

            s, e, label = span[0], span[1], span[2]
            total_token_entities += 1
            label_counter_token[label] += 1

            if not isinstance(s, int) or not isinstance(e, int):
                no_matches += 1
                bad_examples.append({
                    "chunk_id": chunk_id,
                    "reason": "span_not_int",
                    "span": span,
                })
                continue

            if s < 0 or e >= len(tokens) or s > e:
                no_matches += 1
                bad_examples.append({
                    "chunk_id": chunk_id,
                    "reason": "span_out_of_bounds",
                    "span": span,
                    "n_tokens": len(tokens),
                })
                continue

            token_text = " ".join(tokens[s:e + 1])
            token_key = (label, norm(token_text))

            if token_key in char_set:
                exact_matches += 1
            else:
                # Match faible : texte inclus dans une entité char ou inversement
                found_weak = False
                token_norm = norm(token_text)

                for char_text in char_texts_by_label[label]:
                    char_norm = norm(char_text)
                    if token_norm in char_norm or char_norm in token_norm:
                        found_weak = True
                        break

                if found_weak:
                    weak_matches += 1
                else:
                    no_matches += 1
                    if len(bad_examples) < 50:
                        bad_examples.append({
                            "chunk_id": chunk_id,
                            "reason": "token_entity_not_found_in_char_entities",
                            "label": label,
                            "token_span": [s, e],
                            "token_text": token_text,
                            "char_entities_same_label": char_texts_by_label[label][:10],
                        })

    print("\n" + "=" * 80)
    print("RÉSULTATS ALIGNEMENT")
    print("=" * 80)

    print(f"Total char entities  : {total_char_entities}")
    print(f"Total token entities : {total_token_entities}")
    print(f"Exact matches        : {exact_matches}")
    print(f"Weak matches         : {weak_matches}")
    print(f"No matches           : {no_matches}")

    match_rate = (exact_matches + weak_matches) / max(total_token_entities, 1) * 100
    exact_rate = exact_matches / max(total_token_entities, 1) * 100

    print(f"\nExact match rate      : {exact_rate:.2f}%")
    print(f"Total match rate      : {match_rate:.2f}%")

    print("\nDistribution CHAR:")
    for label in LABELS_CORE:
        print(f"  {label:<22} {label_counter_char[label]}")

    print("\nDistribution TOKEN:")
    for label in LABELS_CORE:
        print(f"  {label:<22} {label_counter_token[label]}")

    print("\nEntités longues > 12 mots :", len(long_entities))
    for item in long_entities[:20]:
        print(f"  [{item['label']}] {item['n_words']} mots | {item['text'][:160]}")

    print("\nEntités single-token suspectes :", len(suspicious_single_tokens))
    for item in suspicious_single_tokens[:30]:
        print(f"  [{item['label']}] {item['text']} | {item['chunk_id']}")

    print("\nMauvais exemples d'alignement :", len(bad_examples))
    for ex in bad_examples[:20]:
        print("\n---")
        print(json.dumps(ex, ensure_ascii=False, indent=2))

    report = {
        "total_char_entities": total_char_entities,
        "total_token_entities": total_token_entities,
        "exact_matches": exact_matches,
        "weak_matches": weak_matches,
        "no_matches": no_matches,
        "exact_match_rate": exact_rate,
        "total_match_rate": match_rate,
        "label_counter_char": dict(label_counter_char),
        "label_counter_token": dict(label_counter_token),
        "long_entities_count": len(long_entities),
        "suspicious_single_tokens_count": len(suspicious_single_tokens),
        "bad_examples": bad_examples,
        "long_entities_sample": long_entities[:100],
        "suspicious_single_tokens_sample": suspicious_single_tokens[:100],
    }

    out_file = DATASET_DIR / "audit_merge_report.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\nRapport sauvegardé :", out_file)

    if match_rate < 95:
        print("\n❌ PROBLÈME : le merge/token mapping semble mauvais.")
    elif exact_rate < 90:
        print("\n⚠️ Attention : beaucoup de weak matches. À inspecter.")
    else:
        print("\n✅ Le merge semble globalement correct.")


if __name__ == "__main__":
    main()