from ennodiagnostic_v2 import EnnoDiagnosticV2, DocumentInput
from ennodiagnostic_v2.semantic_extractor import CallableLLMAdapter

SOURCE = (
    "Les performances se dégradent fortement lorsque la densité des points diminue. "
    "Deux stratégies ont été comparées mais aucune ne permet de maintenir une géométrie stable."
)

def fake_llm(system_prompt, user_prompt):
    if "Tu qualifies un verrou" in system_prompt:
        return {
            "status": "partial",
            "criteria": [],
            "questions": ["Documenter les résultats détaillés."]
        }
    return {
        "objectives": [],
        "locks": [{
            "statement": "Maintenir une géométrie stable avec des données clairsemées",
            "explicitness": "implicit",
            "evidence_quote": SOURCE,
            "rationale": "Comportement non maîtrisé malgré des essais.",
            "technical_object": "géométrie",
            "unresolved_question": "Comment conserver la stabilité ?",
            "confidence": 0.85
        }],
        "methods": [{
            "statement": "Comparer deux stratégies",
            "evidence_quote": SOURCE,
            "rationale": "Deux stratégies sont comparées.",
            "confidence": 0.8
        }],
        "parameters": [],
        "results": [{
            "statement": "Les performances se dégradent quand la densité diminue",
            "evidence_quote": SOURCE,
            "rationale": "Observation rapportée.",
            "confidence": 0.9
        }]
    }

def test_multilabel_pipeline():
    llm = CallableLLMAdapter(fake_llm)
    pipeline = EnnoDiagnosticV2(semantic_llm=llm, frascati_llm=llm)
    result = pipeline.run([
        DocumentInput(document_id="d1", name="rapport.txt", text=SOURCE, declared_mode="raw")
    ])
    kinds = {x.kind for x in result.entities}
    assert {"lock", "method", "result"} <= kinds
    assert result.audit["unverified_quotes"] == []
    assert len(result.review_queue) >= 1
