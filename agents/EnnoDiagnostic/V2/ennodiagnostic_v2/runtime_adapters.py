from __future__ import annotations

from typing import Optional

from modules.LLM.llm_client import LLMClient
from .config import GlobalFastPathConfig, GlobalReviewerConfig


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
        prompt_char_limit: Optional[int] = None,
        fail_on_truncation: bool = False,
    ) -> None:
        self.supports_fork = client is None
        self.client = client or LLMClient()
        self.request_name = request_name
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.retries = retries
        self.prompt_char_limit = prompt_char_limit
        self.fail_on_truncation = fail_on_truncation

    def fork(self) -> Optional["EnnoSmartLLMAdapter"]:
        """Donne à un worker son propre client et ses propres métadonnées d'appel."""
        if not self.supports_fork:
            return None
        return EnnoSmartLLMAdapter(
            request_name=self.request_name,
            temperature=self.temperature,
            max_output_tokens=self.max_output_tokens,
            retries=self.retries,
            prompt_char_limit=self.prompt_char_limit,
            fail_on_truncation=self.fail_on_truncation,
        )

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
            prompt_char_limit=self.prompt_char_limit,
            fail_on_truncation=self.fail_on_truncation,
        )


class GlobalReasonerLLMAdapter:
    """Modèle dédié ; refuse toute coupure automatique du dossier."""

    def __init__(self, config: GlobalReviewerConfig):
        if config.provider not in {"openai", "openrouter"}:
            raise ValueError("Le raisonnement élevé est supporté ici par openai/openrouter.")
        self.config = config
        self.client = LLMClient(model=config.global_reasoner_model, provider=config.provider)
        # Aucun basculement implicite vers le modèle économique de l'extraction.
        self.client.allow_cross_provider_fallback = False
        self.client.openai_fallback_models = []
        self.client.openrouter_fallback_models = []

    def extract_json(self, system_prompt: str, user_prompt: str) -> str:
        prompt = (
            "INSTRUCTIONS SPECIFIQUES\n" + system_prompt.strip()
            + "\n\nDOSSIER A REVOIR\n" + user_prompt.strip()
        )
        return self.client.generate(
            prompt=prompt,
            temperature=0.0,
            max_output_tokens=self.config.max_output_tokens,
            retries=1,
            json_mode=True,
            request_name="ennodiagnostic:v2:global_project_review",
            prompt_char_limit=self.config.max_prompt_chars,
            fail_on_truncation=True,
            reasoning_effort=self.config.reasoning_effort,
            background=self.config.provider == "openai",
        )


class GlobalFastLLMAdapter:
    """Appel dédié à l'extraction du dossier brut, avec limite sans troncature."""

    def __init__(self, config: GlobalFastPathConfig, *, request_name: str = "ennodiagnostic:v2:global_fast_extract"):
        if config.provider not in {"openai", "openrouter"}:
            raise ValueError("Global Fast Path requiert openai/openrouter.")
        self.config = config
        self.request_name = request_name
        self.client = LLMClient(model=config.model, provider=config.provider)
        self.client.allow_cross_provider_fallback = False
        self.client.openai_fallback_models = []
        self.client.openrouter_fallback_models = []

    def extract_json(self, system_prompt: str, user_prompt: str) -> str:
        return self.client.generate(
            prompt="INSTRUCTIONS SPECIFIQUES\n" + system_prompt.strip()
            + "\n\nDOSSIER BRUT\n" + user_prompt.strip(),
            temperature=0.0,
            max_output_tokens=self.config.max_output_tokens,
            retries=0,
            json_mode=True,
            request_name=getattr(self, "request_name", "ennodiagnostic:v2:global_fast_extract"),
            prompt_char_limit=self.config.max_prompt_chars,
            fail_on_truncation=True,
            reasoning_effort=self.config.reasoning_effort,
            background=self.config.provider == "openai",
        )
