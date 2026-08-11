# -*- coding: utf-8 -*-
from __future__ import annotations
import argparse, importlib.util, py_compile, sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=r"C:\EnnoSmart")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    budget = root / "modules" / "LLM" / "usage_budget.py"
    llm = root / "modules" / "LLM" / "llm_client.py"
    orch = root / "backend_api" / "services" / "ennoscholar_state_of_art_orchestrator.py"
    for path in (budget, llm, orch):
        if not path.exists():
            raise SystemExit(f"MISSING: {path}")
        py_compile.compile(str(path), doraise=True)
    if "install_llm_budget_hooks(LLMClient)" not in llm.read_text(encoding="utf-8"):
        raise SystemExit("Hook LLM absent")
    if '@budgeted_pipeline(run_type="ennoscholar_state_of_art")' not in orch.read_text(encoding="utf-8"):
        raise SystemExit("Décorateur pipeline absent")

    spec = importlib.util.spec_from_file_location("usage_budget_test", budget)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    usage = module.normalize_usage({"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100})
    cost = module.compute_cost("gpt-4.1", "openai", usage)["cost_usd"]
    expected = (1000 * 2 + 100 * 8) / 1_000_000
    if abs(cost - expected) > 1e-9:
        raise SystemExit(f"Coût incorrect: {cost} != {expected}")
    print("BUDGET_LOGGING_VERIFY_OK")
    print("Contrôles : syntaxe, hook, décorateur, tokens et coût GPT-4.1.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
