"""Exclusive V4 provider policy. Historical receipts are never normalized here."""
MODEL = "gpt-6-luna"


class ModelPolicyError(ValueError):
    """Non-retryable configuration refusal with a safe machine-readable code."""
    code = "incompatible_model_parameters"


def request_policy(kwargs):
    """Normalize legacy selectors; reject unpriced modes before reserving/sending."""
    sent = dict(kwargs)
    sent["model"] = MODEL
    # Only Standard is attested by the active V4 catalogue. Never inherit a
    # provider/project default that could silently select Fast processing.
    if sent.get("service_tier", "default") not in {None, "default"}:
        raise ModelPolicyError("EnnoScholar V4 exige le service_tier default (Standard).")
    sent["service_tier"] = "default"
    effort = (sent.get("reasoning") or {}).get("effort", "medium")
    if effort not in {"none", "low", "medium", "high", "xhigh", "max"}:
        raise ModelPolicyError("reasoning.effort incompatible avec gpt-6-luna.")
    if sent.get("max_output_tokens", 1) > 128000:
        raise ModelPolicyError("gpt-6-luna : max_output_tokens dépasse 128000.")
    extra = sent.get("extra_body") or {}
    if any(key in extra for key in ("model", "service_tier", "reasoning", "max_output_tokens")):
        raise ModelPolicyError("Un extra_body ne peut pas remplacer la politique LLM V4.")
    cache = extra.get("prompt_cache_options") or sent.get("prompt_cache_options") or {}
    if cache.get("ttl", "30m") != "30m":
        raise ModelPolicyError("EnnoScholar V4 ne tarifie que le cache 30m.")
    return sent
