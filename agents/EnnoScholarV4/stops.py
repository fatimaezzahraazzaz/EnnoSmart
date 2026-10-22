"""Stop signals shared by the agent and the spending controller (no dependencies)."""


class ProviderProtocolError(RuntimeError):
    """A provider stream failed or ended without its terminal response."""


class StopResearch(RuntimeError):
    def __init__(self, reason, diagnostic=None):
        self.reason = reason
        self.diagnostic = diagnostic
        super().__init__(reason)


def technical_diagnostic(exc):
    """Safe structural details only: never exception text, request bodies or locals."""
    import traceback
    from pathlib import Path
    from openai import APIError
    from httpx import TransportError
    frames = [{"file": Path(f.filename).name, "function": f.name, "line": f.lineno}
        for f in traceback.extract_tb(exc.__traceback__)[-8:]]
    if isinstance(exc, StopResearch):
        return {"reason": exc.reason, "category": (exc.diagnostic or {}).get("category", "control"),
            "error_type": type(exc).__name__, "retryable": False, "frames": frames}
    from .model_policy import ModelPolicyError
    if isinstance(exc, ModelPolicyError):
        return {"reason": "configuration_error", "category": "configuration", "code": exc.code,
            "error_type": type(exc).__name__, "retryable": False, "frames": frames}
    provider = isinstance(exc, (APIError, ProviderProtocolError, TransportError))
    status = getattr(exc, "status_code", None) if provider else None
    reason = ("configuration_error" if status in {400, 401, 403, 404, 422} else
        "quota_or_rate_limit" if status == 429 else "upstream_error") if provider else "internal_error"
    protocol_code = str(exc) if isinstance(exc, ProviderProtocolError) and str(exc) in {
        "ResponsesStreamError", "ResponsesMissingCompletion"} else None
    return {"reason": reason, "category": "provider" if provider else "internal",
        "error_type": type(exc).__name__, "http_status": status,
        **({"protocol_code": protocol_code} if protocol_code else {}),
        "retryable": provider and (status is None or status == 429 or status >= 500),
        "frames": frames}


def require_tools(tools, *, required=None, forbidden=()):
    """A permission conflict is decided locally, before indexing or paying a call."""
    if not tools:
        raise StopResearch("permission_conflict" if forbidden else "tool_configuration_error",
            {"category": "permissions" if forbidden else "internal", "required_tool": required,
                "forbidden_tools": sorted(forbidden), "retryable": False})
    return tools
