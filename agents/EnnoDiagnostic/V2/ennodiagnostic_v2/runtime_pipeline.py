from __future__ import annotations

from .config import PipelineConfig
from .pipeline import EnnoDiagnosticV2
from .runtime_adapters import EnnoSmartLLMAdapter


def build_runtime_pipeline(
    *,
    run_frascati: bool = False,
) -> EnnoDiagnosticV2:

    semantic_llm = EnnoSmartLLMAdapter(
        request_name="ennodiagnostic:v2:semantic_extract",
        temperature=0.0,
        max_output_tokens=1800,
    )

    consolidation_llm = EnnoSmartLLMAdapter(
        request_name="ennodiagnostic:v2:global_lock_consolidation",
        temperature=0.0,
        max_output_tokens=2200,
    )

    qualification_llm = EnnoSmartLLMAdapter(
        request_name="ennodiagnostic:v2:lock_qualification",
        temperature=0.0,
        max_output_tokens=1800,
    )

    frascati_llm = None
    if run_frascati:
        frascati_llm = EnnoSmartLLMAdapter(
            request_name="ennodiagnostic:v2:frascati",
            temperature=0.0,
            max_output_tokens=1800,
        )

    config = PipelineConfig(
        run_frascati=run_frascati,
        require_human_review_for_locks=True,
    )

    return EnnoDiagnosticV2(
        semantic_llm=semantic_llm,
        consolidation_llm=consolidation_llm,
        qualification_llm=qualification_llm,
        frascati_llm=frascati_llm,
        config=config,
    )
