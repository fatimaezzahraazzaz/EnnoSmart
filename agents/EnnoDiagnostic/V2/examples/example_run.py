from ennodiagnostic_v2 import EnnoDiagnosticV2, DocumentInput
from ennodiagnostic_v2.semantic_extractor import CallableLLMAdapter

def my_llm(system_prompt, user_prompt):
    # Remplacer par le client LLM EnnoSmart réel.
    return {
        "objectives": [],
        "locks": [],
        "methods": [],
        "parameters": [],
        "results": [],
    }

pipeline = EnnoDiagnosticV2(
    semantic_llm=CallableLLMAdapter(my_llm),
    fastjudge=None,
    frascati_llm=None,
)

result = pipeline.run([
    DocumentInput(
        document_id="doc_1",
        name="rapport_test.txt",
        text="Texte du rapport...",
        declared_mode="raw",
    )
])

print(result.to_dict())
