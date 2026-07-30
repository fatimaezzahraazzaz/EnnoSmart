# -*- coding: utf-8 -*-
r"""
validate_domains.py

À lancer :

cd C:\EnnoSmart
python tools\validate_domains.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(r"C:\EnnoSmart")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DOMAINS_PATH = PROJECT_ROOT / "modules" / "NLP" / "data" / "domains.json"


def try_read_file(path: Path):
    print("=" * 80)
    print("CHECK FICHIER")
    print("=" * 80)

    print(f"Chemin attendu : {path}")
    print(f"Existe ? {path.exists()}")

    if not path.exists():
        return None

    print(f"Taille : {path.stat().st_size} octets")

    for enc in ["utf-8", "utf-8-sig", "cp1252", "latin-1"]:
        try:
            raw = path.read_text(encoding=enc, errors="strict")
            print(f"\nLecture texte OK avec encoding : {enc}")
            print("Début du fichier :")
            print(raw[:500])
            return raw
        except Exception as e:
            print(f"Lecture échouée avec {enc} : {repr(e)}")

    return None


def try_parse_json(raw: str):
    print("\n" + "=" * 80)
    print("CHECK JSON")
    print("=" * 80)

    if raw is None:
        print("Impossible de lire le fichier.")
        return None

    try:
        data = json.loads(raw)
        print("JSON valide ✅")
        print(f"Type racine : {type(data).__name__}")

        if isinstance(data, dict):
            print(f"Clés racine : {list(data.keys())[:30]}")
        elif isinstance(data, list):
            print(f"Nombre éléments racine : {len(data)}")
            if data:
                print(f"Type premier élément : {type(data[0]).__name__}")
                print(f"Premier élément aperçu : {str(data[0])[:500]}")

        return data

    except Exception as e:
        print("JSON invalide ❌")
        print(f"Erreur exacte : {repr(e)}")

        # Localiser grossièrement l’erreur si possible
        if hasattr(e, "lineno"):
            lines = raw.splitlines()
            line_no = getattr(e, "lineno", None)
            col_no = getattr(e, "colno", None)
            print(f"Ligne erreur : {line_no}, colonne : {col_no}")
            if line_no and 1 <= line_no <= len(lines):
                print("Ligne concernée :")
                print(lines[line_no - 1][:1000])

        return None


def count_possible_domains(data):
    print("\n" + "=" * 80)
    print("CHECK STRUCTURE DOMAINES")
    print("=" * 80)

    if data is None:
        return

    count_code_label = 0
    count_code = 0
    count_label = 0

    examples = []

    def walk(x):
        nonlocal count_code_label, count_code, count_label, examples

        if isinstance(x, dict):
            has_code = any(k in x for k in ["code", "id", "domain_code", "code_niv1", "code_niv2", "code_niv3"])
            has_label = any(k in x for k in ["label", "libelle", "name", "title", "label_niv1", "label_niv2", "label_niv3"])

            if has_code:
                count_code += 1
            if has_label:
                count_label += 1
            if has_code and has_label:
                count_code_label += 1
                if len(examples) < 5:
                    examples.append(x)

            for v in x.values():
                if isinstance(v, (dict, list)):
                    walk(v)

        elif isinstance(x, list):
            for item in x:
                walk(item)

    walk(data)

    print(f"Noeuds avec code : {count_code}")
    print(f"Noeuds avec label/libelle/name/title : {count_label}")
    print(f"Noeuds avec code + label : {count_code_label}")

    print("\nExemples détectés :")
    for ex in examples:
        print(str(ex)[:800])
        print("-" * 40)


def main():
    raw = try_read_file(DOMAINS_PATH)
    data = try_parse_json(raw)
    count_possible_domains(data)

    print("\n" + "=" * 80)
    print("TEST AVEC domain_classifier ACTUEL")
    print("=" * 80)

    try:
        from modules.NLP.domain_classifier import load_domains, classify_domain

        domains = load_domains(DOMAINS_PATH)
        print(f"Domaines chargés par load_domains() : {len(domains)}")

        if domains:
            print("Premiers domaines :")
            for d in domains[:10]:
                print(d)

            sample = """
            TGM100 compresseur à pistons à barillet multi-étages.
            Mécanique, génie mécanique, pression 300 bars, débit 90 100 m3/h,
            vibrations, nuisances acoustiques, refroidissement, température,
            contrepoids, équilibrage, air sec, condensats, séparateur.
            """
            print("\nClassification test :")
            print(classify_domain(sample, domains_path=DOMAINS_PATH))

    except Exception as e:
        print(f"Erreur import/test domain_classifier : {repr(e)}")


if __name__ == "__main__":
    main()