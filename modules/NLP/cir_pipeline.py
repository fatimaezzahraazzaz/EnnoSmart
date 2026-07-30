# -*- coding: utf-8 -*-
"""Compatibilité avec ENNOSMART_CIR_NLP_MODULE=modules.NLP.cir_pipeline.

Le vrai point d'entrée est désormais pipeline_route.run_nlp_pipeline_routed.
Le sous-package ``modules.NLP.CIR`` reste distinct et n'est pas remplacé ici.
"""

from .pipeline_route import (
    run_nlp_pipeline_route,
    run_nlp_pipeline_routed,
    run_pipeline,
    run_pipeline_route,
)

run_nlp_pipeline = run_nlp_pipeline_routed
run_cir_nlp_pipeline = run_nlp_pipeline_routed

__all__ = [
    "run_nlp_pipeline",
    "run_cir_nlp_pipeline",
    "run_nlp_pipeline_routed",
    "run_nlp_pipeline_route",
    "run_pipeline",
    "run_pipeline_route",
]
