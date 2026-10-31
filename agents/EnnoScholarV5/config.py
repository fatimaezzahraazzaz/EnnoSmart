"""V5 inherits every existing resource ceiling; no stage replenishment."""
import os
import math
from dataclasses import dataclass, fields, replace
from agents.EnnoScholarV4.config import ScholarV4Config, ConfigurationError

MISSION_BUDGET_ENV = "ENNOSCHOLAR_V5_MISSION_BUDGET_USD"


def positive(raw, name, *, integer=False):
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ConfigurationError(f"{name} doit être un plafond positif fini.") from None
    if not math.isfinite(value) or value <= 0 or integer and not value.is_integer():
        raise ConfigurationError(f"{name} doit être un plafond positif fini" + (" entier." if integer else "."))
    return int(value) if integer else value


@dataclass(frozen=True)
class ScholarV5Config(ScholarV4Config):
    session_budget_usd: float | None = None
    mission_max_tool_calls: int | None = None
    mission_max_web_calls: int | None = None

    def validate(self):
        # V4 is reused unchanged; reject non-finite resources at the V5 boundary.
        for f in fields(ScholarV4Config):
            value = getattr(self, f.name)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and not math.isfinite(value):
                raise ConfigurationError(f"{f.name} doit être fini.")
        if self.session_budget_usd is not None:
            positive(self.session_budget_usd, 'Budget de session')
        for field, ceiling in (('mission_max_tool_calls', 48), ('mission_max_web_calls', 4)):
            value = getattr(self, field)
            if value is not None and positive(value, field, integer=True) > ceiling:
                raise ConfigurationError(f'{field} ne peut pas dépasser le plafond existant de {ceiling}.')
        super().validate()


def from_env():
    """V4 fields, parsed at the V5 boundary with identical finite/integer rules."""
    defaults = ScholarV4Config()
    values = {}
    for f in fields(ScholarV4Config):
        raw = os.getenv('ENNOSCHOLAR_V4_'+f.name.upper())
        if raw is None:
            continue
        default = getattr(defaults,f.name)
        try:
            if isinstance(default,bool):
                if raw.casefold() not in {'true','false','1','0','yes','no'}:
                    raise ValueError()
                values[f.name] = raw.casefold() in {'true','1','yes'}
            elif isinstance(default,(int,float)):
                value = float(raw)
                if not math.isfinite(value) or isinstance(default,int) and not value.is_integer():
                    raise ValueError()
                values[f.name] = int(value) if isinstance(default,int) else value
            else:
                values[f.name] = raw
        except (ValueError,OverflowError):
            raise ConfigurationError(f'ENNOSCHOLAR_V4_{f.name.upper()} est invalide (valeur finie requise).') from None
    values['api_key'] = os.getenv('OPENAI_API_KEY','').strip()
    values.setdefault('prompt_cache_mode','explicit')
    values.setdefault('research_job_enabled',True)
    values.setdefault('fulltext_prefetch_enabled',True)
    return ScholarV4Config(**values)


def mission_budget():
    """The mission's global dollar cap, only when explicitly authorised (never a default)."""
    raw = os.getenv(MISSION_BUDGET_ENV, "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        raise ConfigurationError(f"{MISSION_BUDGET_ENV} doit être un montant en dollars.") from None
    if not math.isfinite(value) or not value > 0:
        raise ConfigurationError(f"{MISSION_BUDGET_ENV} doit être un montant positif.")
    return value


def runtime_config(config=None):
    base = config or from_env()
    raw = os.getenv('ENNOSCHOLAR_V5_SESSION_BUDGET_USD', '').strip()
    session_cap = getattr(base, 'session_budget_usd', None)
    if raw:
        session_cap = positive(raw, 'ENNOSCHOLAR_V5_SESSION_BUDGET_USD')
    extra = {}
    for field, env in (('mission_max_tool_calls', 'ENNOSCHOLAR_V5_MISSION_MAX_TOOLS'),
                       ('mission_max_web_calls', 'ENNOSCHOLAR_V5_MISSION_MAX_WEB')):
        value = os.getenv(env, '').strip() or getattr(base, field, None)
        extra[field] = positive(value, env, integer=True) if value is not None else None
    base = ScholarV5Config(**{f.name: getattr(base, f.name) for f in fields(ScholarV4Config)}, session_budget_usd=session_cap, **extra)
    for field, ceiling in (('mission_max_tool_calls', 48), ('mission_max_web_calls', 4)):
        value = getattr(base, field)
        if value is not None and value > ceiling:
            raise ConfigurationError(f'{field} dépasse le plafond existant de {ceiling}.')
    # Validate resource values even when a caller constructs a config directly.
    for f in fields(ScholarV4Config):
        value = getattr(base, f.name)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and not math.isfinite(value):
            raise ConfigurationError(f"{f.name} doit être fini.")
    # Validate resources before accepting a profile, even if no API key is needed
    # yet for local session management. This creates no SDK client or network call.
    replace(base,api_key=base.api_key or 'resource-validation-only',enabled=True).validate()
    from .writing_job import mission_limits
    mission_limits()
    # The drafting mission (writing_job.py) opens only with a global dollar cap explicitly
    # authorised in the environment; V4's default manuscript budget is never assumed.
    budget = mission_budget()
    mission = {"writing_job_enabled": False} if budget is None else {
        "writing_job_enabled": True, "writing_job_budget_usd": budget}
    return replace(base, research_job_enabled=False, fulltext_prefetch_enabled=False, **mission)
