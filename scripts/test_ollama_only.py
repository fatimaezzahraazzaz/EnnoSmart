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
    out = llm.generate(
        'Réponds uniquement en JSON valide : {"ok": true, "provider": "ollama_only"}',
        temperature=0.02,
        max_output_tokens=200,
        retries=0,
    )
    print("=== REPONSE ===")
    print(out)
    try:
        data = json.loads(out)
        print("JSON OK =", data.get("ok"))
    except Exception as exc:
        print("JSON NON PARSEABLE =", exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
