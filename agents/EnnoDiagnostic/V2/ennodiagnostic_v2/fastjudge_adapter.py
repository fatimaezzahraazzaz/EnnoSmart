from dataclasses import replace
from typing import Callable, Iterable, Optional, Protocol, Tuple
from .schemas import ExtractionItem

class FastJudgeProtocol(Protocol):
    def predict(self, text: str) -> Tuple[str, Optional[float]]:
        ...

class CallableFastJudgeAdapter:
    def __init__(self, fn: Callable[[str], Tuple[str, Optional[float]]]):
        self.fn = fn
    def predict(self, text: str) -> Tuple[str, Optional[float]]:
        return self.fn(text)

EXPECTED = {
    "objective": "objectif",
    "lock": "verrou",
    "method": "methode",
    "parameter": "parametre",
    "result": "resultat",
}

def annotate_with_fastjudge(items: Iterable[ExtractionItem], fastjudge: Optional[FastJudgeProtocol]):
    # FastJudge annote, il ne filtre jamais.
    if fastjudge is None:
        return list(items)
    out = []
    for item in items:
        label, score = fastjudge.predict(item.evidence.quote)
        label = str(label or "").lower().strip()
        disagreement = bool(label and EXPECTED.get(item.kind) != label)
        out.append(replace(
            item,
            fastjudge_label=label or None,
            fastjudge_score=score,
            fastjudge_disagreement=disagreement,
            needs_review=item.needs_review or disagreement,
        ))
    return out
