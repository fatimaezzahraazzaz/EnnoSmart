from __future__ import annotations

from typing import Optional

from modules.LLM.llm_client import LLMClient


class EnnoSmartLLMAdapter:
    """Branche EnnoDiagnostic V2 sur le client LLM central EnnoSmart."""

    def __init__(
        self,
        client: Optional[LLMClient] = None,
        *,
        request_name: str = "ennodiagnostic:v2:semantic_extract",
        temperature: float = 0.0,
        max_output_tokens: int = 1800,
        retries: int = 1,
    ) -> None:
        self.client = client or LLMClient()
        self.request_name = request_name
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.retries = retries

    def extract_json(self, system_prompt: str, user_prompt: str) -> str:
        prompt = (
            f"INSTRUCTIONS SPECIFIQUES\n"
            f"{str(system_prompt or '').strip()}\n\n"
            f"CONTENU A ANALYSER\n"
            f"{str(user_prompt or '').strip()}"
        )

        return self.client.generate(
            prompt=prompt,
            temperature=self.temperature,
            max_output_tokens=self.max_output_tokens,
            retries=self.retries,
            json_mode=True,
            request_name=self.request_name,
        )
