import re
from typing import Dict, Optional

CIR_HINTS = (
    r"\bdossier\s+cir\b",
    r"\bcr[ée]dit\s+d[' ]?imp[oô]t\s+recherche\b",
    r"\bverrous?\s+(?:scientifiques?|technologiques?)\b",
    r"\b[ée]tat\s+de\s+l['’]art\b",
)
PRE_CIR_HINTS = (
    r"\bpr[ée][ -]?cir\b",
    r"\bfiche\s+(?:technique|projet)\b",
)

def detect_document_mode(text: str, name: str = "", declared_mode: Optional[str] = None) -> Dict[str, object]:
    # Le mode apporte du contexte mais ne choisit jamais un moteur différent.
    if declared_mode:
        mode = declared_mode.lower().strip().replace("-", "_")
        if mode in {"raw", "pre_cir", "cir"}:
            return {"mode": mode, "confidence": 1.0, "reason": "declared_mode"}

    sample = f"{name}\n{text[:12000]}".lower()
    cir_hits = sum(bool(re.search(p, sample, flags=re.I)) for p in CIR_HINTS)
    pre_hits = sum(bool(re.search(p, sample, flags=re.I)) for p in PRE_CIR_HINTS)

    if cir_hits >= 2:
        return {"mode": "cir", "confidence": 0.80, "reason": "structure_hints"}
    if pre_hits:
        return {"mode": "pre_cir", "confidence": 0.65, "reason": "structure_hints"}
    return {"mode": "raw", "confidence": 0.55, "reason": "default_raw"}
