from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.full_runtime import (
    ContextualLLMAdapter,
    parse_domain_tags,
)

class CaptureLLM:
    def __init__(self):
        self.last_user = ""

    def extract_json(self, system_prompt: str, user_prompt: str) -> str:
        self.last_user = user_prompt
        return "{}"

tags = parse_domain_tags(
    "Intelligence artificielle & Data | Informatique & Logiciels | Intelligence artificielle & Data"
)

assert tags == [
    "Intelligence artificielle & Data",
    "Informatique & Logiciels",
]

capture = CaptureLLM()
ContextualLLMAdapter(capture, tags).extract_json(
    "SYSTEM",
    "Contenu du dossier",
)

assert "Intelligence artificielle & Data" in capture.last_user
assert "Informatique & Logiciels" in capture.last_user
assert "ne constituent jamais une preuve" in capture.last_user
assert "Contenu du dossier" in capture.last_user

print("Tags :", tags)
print("SMOKE TEST DOMAIN CONTEXT REUSSI")
