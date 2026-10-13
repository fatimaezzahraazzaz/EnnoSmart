from typing import Iterable, Optional
from .prompts import FRASCATI_SYSTEM
from .schemas import CanonicalEntity, FrascatiAssessment, FrascatiCriterion
from .semantic_extractor import LLMClientProtocol, parse_json_payload

def _prompt(lock, related):
    direct = "\n".join(
        f"- {e.evidence_id} | {e.document_name} | {e.quote}"
        for e in lock.evidences
    )
    others = "\n".join(
        f"- {x.kind} | {e.evidence_id} | {e.quote}"
        for x in related for e in x.evidences
    )
    return f"""VERROU À QUALIFIER:
{lock.entity_id}
{lock.statement}

PREUVES DIRECTES:
{direct or "- aucune"}

ÉLÉMENTS LIÉS:
{others or "- aucun"}

Utilise uniquement ces preuves.
"""

class FrascatiEvaluator:
    def __init__(self, llm: Optional[LLMClientProtocol]):
        self.llm = llm

    def assess(self, entities: Iterable[CanonicalEntity]):
        entities = list(entities)
        locks = [x for x in entities if x.kind == "lock"]
        related = [x for x in entities if x.kind != "lock"]

        if self.llm is None:
            return [
                FrascatiAssessment(
                    lock_entity_id=x.entity_id,
                    status="not_evaluated",
                    questions=["Qualification Frascati à effectuer."],
                )
                for x in locks
            ]

        out = []
        for lock in locks:
            data = parse_json_payload(
                self.llm.extract_json(FRASCATI_SYSTEM, _prompt(lock, related))
            )
            criteria = []
            for c in data.get("criteria") or []:
                criteria.append(FrascatiCriterion(
                    criterion=str(c.get("criterion") or ""),
                    status=str(c.get("status") or "insufficient"),
                    explanation=str(c.get("explanation") or ""),
                    evidence_ids=[str(v) for v in c.get("evidence_ids") or []],
                    questions=[str(v) for v in c.get("questions") or []],
                ))
            out.append(FrascatiAssessment(
                lock_entity_id=lock.entity_id,
                status=str(data.get("status") or "insufficient"),
                criteria=criteria,
                questions=[str(v) for v in data.get("questions") or []],
            ))
        return out
