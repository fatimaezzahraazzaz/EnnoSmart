# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Any


def _norm(s: str) -> str:
    s = str(s or '').lower()
    table = str.maketrans('àâäéèêëîïôöùûüç’', 'aaaeeeeiioouuuc\'')
    s = s.translate(table)
    s = re.sub(r'[^a-z0-9]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


STATE_OF_ART_PATTERNS = [
    r'\barticle\b', r'\bsurvey\b', r'\betat de l art\b', r'\bstate of art\b',
    r'\blitterature\b', r'\bliterature\b', r'\barxiv\b', r'\bieee\b',
    r'\bpaper\b', r'\bpublication\b', r'\breferences?\b', r'\bbibliographie\b',
    r'\bbenchmark\b', r'\bcomparatif\b', r'\bllm for unit test generation\b',
]

PROJECT_CORE_PATTERNS = [
    r'\bpresentation\b', r'\bprésentation\b', r'\bdemarche\b', r'\bdémarche\b',
    r'\bexperimentale\b', r'\bexpérimentale\b', r'\bpoint\b', r'\bnote de cadrage\b',
    r'\bavancement\b', r'\btravaux\b', r'\bresultats metriques\b', r'\brésultats métriques\b',
    r'\bmetrics\b', r'\bmetriques\b', r'\bmétriques\b', r'\bai code\b', r'\bai-code\b',
]

CIR_FINAL_PATTERNS = [
    r'\bcir final\b', r'\bdt .*\b2024\b', r'\btpl dt\b', r'\bdossier cir\b',
    r'\bjustificatif des travaux\b', r'\bcredit imp[oô]t recherche\b', r'\bcrédit imp[oô]t recherche\b',
]

META_DOC_PATTERNS = [r'\brésumé de la documentation\b', r'\bresume de la documentation\b', r'\bdocumentation\b']


def infer_origin(file_name: str, text: str = '') -> Dict[str, Any]:
    """Retourne project_core / state_of_art / cir_final / metadata / unknown."""
    name = _norm(Path(file_name).name)
    sample = _norm(str(text or '')[:4000])
    joined = f'{name} {sample}'

    if any(re.search(p, joined) for p in CIR_FINAL_PATTERNS):
        # Exception: "Demarche Experimentale" peut contenir des parties CIR mais reste projet.
        if not re.search(r'\bdemarche|\bpoint|\bpresentation|\brésultats|\bresultats', name):
            return {'content_origin': 'cir_final', 'source_weight': 0.0, 'reason': 'filename_or_text_cir_final'}

    if any(re.search(p, name) for p in META_DOC_PATTERNS) and len(sample) < 2500:
        return {'content_origin': 'metadata', 'source_weight': 0.15, 'reason': 'documentation_summary'}

    if any(re.search(p, name) for p in STATE_OF_ART_PATTERNS):
        return {'content_origin': 'state_of_art', 'source_weight': 0.45, 'reason': 'filename_state_of_art'}

    # Les fichiers projet doivent passer avant les mots état de l'art présents dans leur contenu.
    if any(re.search(p, name) for p in PROJECT_CORE_PATTERNS):
        return {'content_origin': 'project_core', 'source_weight': 1.0, 'reason': 'filename_project_core'}

    # Si le contenu est surtout bibliographique / article.
    state_hits = sum(1 for p in STATE_OF_ART_PATTERNS if re.search(p, joined))
    project_hits = sum(1 for p in PROJECT_CORE_PATTERNS if re.search(p, joined))
    if state_hits >= 3 and project_hits == 0:
        return {'content_origin': 'state_of_art', 'source_weight': 0.45, 'reason': 'text_state_of_art'}
    if project_hits >= 1:
        return {'content_origin': 'project_core', 'source_weight': 0.92, 'reason': 'text_project_core'}

    return {'content_origin': 'unknown', 'source_weight': 0.75, 'reason': 'unknown'}
