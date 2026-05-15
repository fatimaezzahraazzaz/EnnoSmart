"""
build_domains_json.py
──────────────────────────────────────────────────────────────────────────────
Génère modules/NLP/data/domains.json depuis la nomenclature officielle MESR.

À LANCER UNE SEULE FOIS (ou quand la nomenclature change) :

    python build_domains_json.py nomenclature-scientifique-de-domaines-de-recherche.xlsx

Sortie : domains.json — référentiel figé utilisé par domain_classifier.py.
Ce n'est PAS du code métier : c'est une donnée de référence.

Structure du JSON produit :
{
  "_meta": {...},
  "niv1": { "A": "SCIENCES ET TECHNOLOGIES DU NUMERIQUE...", ... },     # 4 domaines
  "niv2": { "A1": {"label": "...", "parent": "A"}, ... },               # 33 sous-domaines
  "niv3": { "A1a": {"label": "...", "parent": "A1"}, ... }              # 130 sections
}
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:
    import pandas as pd
except ImportError:
    print("pandas requis : pip install pandas openpyxl")
    sys.exit(1)


def _clean(value) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def _code_sort_key(code: str) -> tuple:
    """Trie A1, A2, ... A10, A11 dans l'ordre numérique correct."""
    m = re.match(r"([A-Z])(\d+)", code or "")
    return (m.group(1), int(m.group(2))) if m else (code or "", 0)


def _code3_sort_key(code: str) -> tuple:
    """Trie les sections niv3 (A1a, A1b, ...)."""
    m = re.match(r"([A-Z]\d+)([a-z]*)", code or "")
    if not m:
        return ((code or "", 0), "")
    return (_code_sort_key(m.group(1)), m.group(2))


def build(xlsx_path: str, output_path: str = "domains.json") -> dict:
    df = pd.read_excel(xlsx_path, sheet_name=0)

    expected = ["code1", "DOMAINES niv1", "code2", "Sous-domaines niv2",
                "code3", "SECTION niv3"]
    missing = [c for c in expected if c not in df.columns]
    if missing:
        print(f"Colonnes manquantes dans le xlsx : {missing}")
        print(f"Colonnes trouvées : {list(df.columns)}")
        sys.exit(1)

    niv1: dict[str, str] = {}
    niv2: dict[str, dict] = {}
    niv3: dict[str, dict] = {}

    for _, row in df.iterrows():
        c1, l1 = _clean(row["code1"]), _clean(row["DOMAINES niv1"])
        c2, l2 = _clean(row["code2"]), _clean(row["Sous-domaines niv2"])
        c3, l3 = _clean(row["code3"]), _clean(row["SECTION niv3"])

        if c1 and l1:
            niv1.setdefault(c1, l1)

        # Ignore les lignes corrompues où le label == le code (bug source connu).
        if c2 and l2 and l2 != c2 and c1:
            niv2.setdefault(c2, {"label": l2, "parent": c1})

        if c3 and l3 and c2:
            niv3.setdefault(c3, {"label": l3, "parent": c2})

    # ── Corrections manuelles de bugs connus du fichier source ────────────
    # D3 est étiqueté "Sciences économiques" (identique à D2) dans le xlsx.
    # Le vrai libellé MESR est "Sciences de gestion et du management".
    if niv2.get("D3", {}).get("label") == "Sciences économiques":
        niv2["D3"]["label"] = "Sciences de gestion et du management"

    domains = {
        "_meta": {
            "source": "Nomenclature scientifique de domaines de recherche (MESR)",
            "levels": [
                f"niv1: {len(niv1)} domaines",
                f"niv2: {len(niv2)} sous-domaines",
                f"niv3: {len(niv3)} sections",
            ],
            "usage": (
                "Referentiel fige. domain_classifier.py demande au LLM de choisir "
                "un code parmi cette liste, puis valide le code contre ce fichier. "
                "Aucune regle metier n'est codee en dur."
            ),
        },
        "niv1": {c: niv1[c] for c in sorted(niv1)},
        "niv2": {c: niv2[c] for c in sorted(niv2, key=_code_sort_key)},
        "niv3": {c: niv3[c] for c in sorted(niv3, key=_code3_sort_key)},
    }

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(domains, f, ensure_ascii=False, indent=2)

    print(f"OK -> {out}")
    print(f"  niv1: {len(domains['niv1'])}")
    print(f"  niv2: {len(domains['niv2'])}")
    print(f"  niv3: {len(domains['niv3'])}")
    return domains


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage : python build_domains_json.py <nomenclature.xlsx> [output.json]")
        sys.exit(1)
    xlsx = sys.argv[1]
    output = sys.argv[2] if len(sys.argv) > 2 else "domains.json"
    build(xlsx, output)