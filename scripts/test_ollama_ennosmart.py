# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(r"C:\EnnoSmart")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.LLM.llm_client import LLMClient


def main() -> int:
    llm = LLMClient()
    prompt = """
Réponds uniquement en JSON valide, sans markdown, sans texte autour.
Format exact :
{"ok": true, "provider": "ollama", "verrou_reformule": "Incertitude sur la représentativité des données radar synthétiques pour l'entraînement ATR"}
""".strip()

    out = llm.generate(prompt, temperature=0.02, max_output_tokens=300, retries=0)
    print("=== REPONSE LLM ===")
    print(out)
    try:
        data = json.loads(out)
        print("\nJSON OK =", data.get("ok"))
    except Exception as e:
        print("\nJSON NON PARSEABLE :", e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
