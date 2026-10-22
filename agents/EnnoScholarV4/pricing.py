"""Versioned price catalogue and the single cost formula of EnnoScholar V4.

Every amount (run counter, task receipt, ledger reservation and settlement) comes from
here. Provider usage is split into its categories: cache reads and writes are part of
the input tokens and are priced separately; reasoning tokens are part of the output
tokens and are never added twice. A price that cannot be attested gives no amount
(``usd=None``) rather than a misleading precision.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

DEFAULT_CATALOG = Path(__file__).with_name("pricing_catalog.json")
PER_TOKEN = 1_000_000
CATEGORIES = ("input", "cache_read", "cache_write", "output")


@dataclass(frozen=True)
class Price:
    provider: str
    model: str
    tier: str
    version: str
    input: float
    cache_read: float
    cache_write: float
    output: float
    long_context_threshold: int = 0
    long_input_multiplier: float = 1.0
    long_output_multiplier: float = 1.0
    web_call: float = 0.0
    effective_from: str = ""
    source: str = ""
    # catalog | environment | conflict (environment differs from the catalogue: the
    # higher price of each category is used, and amounts are marked unverified).
    status: str = "catalog"
    notes: tuple = field(default_factory=tuple)
    # "explicit": the model accepts explicit cache breakpoints (prompt_cache.layout).
    prompt_cache: str = ""

    @property
    def verified(self):
        return self.status == "catalog"

    def describe(self):
        return {"provider": self.provider, "model": self.model, "tier": self.tier, "price_version": self.version,
            "price_status": self.status, "effective_from": self.effective_from,
            **{f"{name}_usd_per_million": getattr(self, name) for name in CATEGORIES}, "notes": list(self.notes)}


class PriceUnknown(LookupError):
    pass


@lru_cache(maxsize=8)
def _load(path):
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    version = raw["catalog_version"]
    prices = {}
    for row in raw["prices"]:
        missing = [name for name in CATEGORIES if not isinstance(row.get(name), (int, float)) or row[name] < 0]
        if missing:
            raise ValueError(f"Tarif incomplet pour {row.get('model')} : {missing}.")
        prices[(row["provider"], row["model"], row.get("tier", "default"))] = Price(
            provider=row["provider"], model=row["model"], tier=row.get("tier", "default"), version=version,
            input=float(row["input"]), cache_read=float(row["cache_read"]), cache_write=float(row["cache_write"]),
            output=float(row["output"]), long_context_threshold=int(row.get("long_context_threshold") or 0),
            long_input_multiplier=float(row.get("long_input_multiplier", 1)),
            long_output_multiplier=float(row.get("long_output_multiplier", 1)),
            web_call=float(row.get("web_call", 0)), effective_from=row.get("effective_from", ""), source=row.get("source", ""),
            prompt_cache=row.get("prompt_cache", ""))
    return prices


def catalog(path=None):
    return _load(str(path or os.getenv("ENNOSCHOLAR_V4_PRICING_FILE") or DEFAULT_CATALOG))


def resolve(config, model=None, tier=None):
    """The price of a model for this configuration, or PriceUnknown.

    Environment prices (ENNOSCHOLAR_V4_INPUT_PRICE …) remain accepted: equal to the
    catalogue they change nothing; different, the higher of the two is used for each
    category (reservations stay protective) and every amount is marked unverified.
    A model absent from the catalogue needs all four environment prices.
    """
    model = model or config.model
    tier = tier or getattr(config, "service_tier", "default") or "default"
    entry = catalog(getattr(config, "pricing_file", "") or None).get(("openai", model, tier))
    env = {"input": getattr(config, "input_price", 0) or 0, "output": getattr(config, "output_price", 0) or 0,
        "cache_read": getattr(config, "cache_read_price", 0) or 0, "cache_write": getattr(config, "cache_write_price", 0) or 0}
    given = {name: value for name, value in env.items() if value > 0}
    if entry is None:
        if set(given) != set(CATEGORIES):
            raise PriceUnknown(f"Aucun tarif attesté pour {model} ({tier}) : catalogue sans ce modèle et tarifs "
                f"d'environnement incomplets ({sorted(set(CATEGORIES)-set(given))} manquants).")
        return Price(provider="openai", model=model, tier=tier, version="environment", status="environment",
            notes=("Tarifs fournis par l'environnement, non présents dans le catalogue.",), **given)
    conflicts = {name: (getattr(entry, name), value) for name, value in given.items() if abs(getattr(entry, name)-value) > 1e-12}
    if not conflicts:
        return entry
    merged = {name: max(getattr(entry, name), given.get(name, 0)) for name in CATEGORIES}
    return Price(**{**entry.__dict__, **merged, "status": "conflict", "notes": tuple(
        f"{name} : catalogue {catalog_value} / environnement {env_value} ; le plus élevé est retenu"
        for name, (catalog_value, env_value) in conflicts.items())})


def usage_categories(raw):
    """Provider usage split into disjoint categories. None when usage is unavailable."""
    if not raw or "input_tokens" not in raw or "output_tokens" not in raw:
        return None
    details = raw.get("input_tokens_details") or {}
    output_details = raw.get("output_tokens_details") or {}
    total_input, output = int(raw.get("input_tokens") or 0), int(raw.get("output_tokens") or 0)
    read = int(details.get("cached_tokens") or 0)
    # Providers name the write counter differently; all are part of input_tokens.
    write = int(details.get("cache_write_tokens") or details.get("cache_creation_tokens")
        or details.get("cache_creation_input_tokens") or 0)
    ordinary = total_input - read - write
    return {"input_tokens": total_input, "ordinary_input_tokens": max(0, ordinary), "cache_read_tokens": read,
        "cache_write_tokens": write, "output_tokens": output,
        "reasoning_tokens": int(output_details.get("reasoning_tokens") or 0),
        # More cached tokens than input tokens: the split cannot be trusted.
        "consistent": ordinary >= 0}


def cost(raw, price, web_calls=0):
    """Amount of one received response. usd is None when it cannot be computed."""
    tokens = usage_categories(raw)
    if tokens is None:
        return {"status": "usage_unavailable", "usd": None, "tokens": None,
            **({"price": price.describe()} if price else {})}
    if price is None:
        return {"status": "price_unknown", "usd": None, "tokens": tokens}
    long_context = bool(price.long_context_threshold and tokens["input_tokens"] > price.long_context_threshold)
    in_factor = price.long_input_multiplier if long_context else 1
    out_factor = price.long_output_multiplier if long_context else 1
    parts = {"ordinary_input": tokens["ordinary_input_tokens"]*price.input*in_factor/PER_TOKEN,
        "cache_read": tokens["cache_read_tokens"]*price.cache_read*in_factor/PER_TOKEN,
        "cache_write": tokens["cache_write_tokens"]*price.cache_write*in_factor/PER_TOKEN,
        "output": tokens["output_tokens"]*price.output*out_factor/PER_TOKEN,
        "web_calls": web_calls*price.web_call}
    if not tokens["consistent"]:
        # Unknown split: every input token at the highest input price.
        parts.update(ordinary_input=tokens["input_tokens"]*max(price.input, price.cache_write)*in_factor/PER_TOKEN,
            cache_read=0., cache_write=0.)
    return {"status": "computed" if price.verified and tokens["consistent"] else "unverified",
        "usd": round(sum(parts.values()), 9), "parts": {k: round(v, 9) for k, v in parts.items()},
        "tokens": tokens, "long_context": long_context, "price_version": price.version, "price_status": price.status}


def reserve_bound(input_tokens, max_output_tokens, price, web_calls=0, web_tokens=0):
    """Protective amount before a call: no cache hit is assumed (every input token at
    the highest input price, write included), every allowed output token, long context
    as soon as the estimate may cross the threshold."""
    input_tokens = input_tokens + web_tokens
    long_context = bool(price.long_context_threshold and input_tokens > price.long_context_threshold*.9)
    in_factor = price.long_input_multiplier if long_context else 1
    out_factor = price.long_output_multiplier if long_context else 1
    return round((input_tokens*max(price.input, price.cache_write, price.cache_read)*in_factor
        + max_output_tokens*price.output*out_factor)/PER_TOKEN + web_calls*price.web_call, 9)


def legacy_cost(raw, input_price, output_price):
    """The former formula (input and output only), kept to compare receipts in audits."""
    tokens = usage_categories(raw) or {"input_tokens": 0, "output_tokens": 0}
    return round((tokens["input_tokens"]*input_price + tokens["output_tokens"]*output_price)/PER_TOKEN, 9)
