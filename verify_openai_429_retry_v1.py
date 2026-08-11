# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import importlib.util
import os
import py_compile
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=r"C:\EnnoSmart")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    path = root / "modules" / "LLM" / "usage_budget.py"
    if not path.exists():
        raise SystemExit(f"MISSING: {path}")

    py_compile.compile(str(path), doraise=True)
    text = path.read_text(encoding="utf-8")

    required = (
        "BEGIN ENNOSMART_OPENAI_429_RETRY_V1",
        "_install_openai_429_retry(llm_class)",
        "[LLM-RATE][429]",
        "ENNOSMART_OPENAI_429_MAX_RETRIES",
        "ENNOSMART_GPT41_MIN_GAP_SECONDS",
    )
    missing = [item for item in required if item not in text]
    if missing:
        raise SystemExit("OPENAI_429_RETRY_VERIFY_FAILED: " + ", ".join(missing))

    spec = importlib.util.spec_from_file_location("usage_budget_retry_test", path)
    if spec is None or spec.loader is None:
        raise SystemExit("Impossible de charger usage_budget.py")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    os.environ["ENNOSMART_OPENAI_429_MAX_RETRIES"] = "3"
    os.environ["ENNOSMART_OPENAI_429_RETRY_BUFFER_SECONDS"] = "0"
    os.environ["ENNOSMART_GPT41_MIN_GAP_SECONDS"] = "0"

    class DummyLLM:
        def __init__(self):
            self.calls = 0
            self.provider = "openai"
            self.model_name = "gpt-4.1"
            self._last_generation_meta = {}

        def _generate_openai(self, prompt, model, *args, **kwargs):
            self.calls += 1
            if self.calls < 3:
                raise RuntimeError(
                    "OpenAI: HTTP 429 rate_limit_exceeded. "
                    "Please try again in 0.01s."
                )
            return "OK", {
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "total_tokens": 12,
            }

        def generate(self, prompt, request_name=None, **kwargs):
            content, usage = self._generate_openai(
                prompt,
                "gpt-4.1",
                0.1,
                100,
                0,
                False,
            )
            self._last_generation_meta = {
                **usage,
                "request_name": request_name,
                "provider": "openai",
                "model": "gpt-4.1",
            }
            return content

        def get_last_generation_meta(self):
            return dict(self._last_generation_meta)

    module.install_llm_budget_hooks(DummyLLM)
    client = DummyLLM()
    result = client.generate("test", request_name="ennoscholar:phase5:test")

    if result != "OK":
        raise SystemExit("Le retry n'a pas retourne le resultat attendu.")
    if client.calls != 3:
        raise SystemExit(f"Nombre d'appels incorrect : {client.calls}, attendu 3.")

    print("OPENAI_429_RETRY_VERIFY_OK")
    print("Controles : detection 429, lecture retry-after, attente et meme modele retente.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
