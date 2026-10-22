"""Explicit prompt-cache layout of one provider request (GPT-5.6 and later).

In the provider's implicit mode, a GPT-5.6 request writes its whole prefix to the cache
(1.25x the input price) up to its last message. Section requests never repeat that
prefix (run 95f3507d: about 16 000 tokens written per section, 0 read), so the cache
cost more than no cache. In explicit mode only the stable prefix is written once: the
task's instructions and tools, moved into a first developer block that carries the single
breakpoint. Everything after it (memory, packet, exchange, the round's instruction) is
billed at the ordinary input price, with no write. Nothing is removed from the request.

Applied at the edge of the metered client, after reservation and prefix probing: the
agent's request (and the reservation computed on it) is unchanged.
"""
from __future__ import annotations

import json

BREAKPOINT = {"mode": "explicit"}
# The provider caches a prefix only from 1 024 visible input tokens (GPT-5.6 and later).
MIN_PREFIX_TOKENS = 1024


def stable_tokens(kwargs):
    from .agent import request_tokens
    return request_tokens(len((kwargs.get("instructions") or "").encode())
        + len(json.dumps(kwargs.get("tools", []) or []).encode()))


def layout(kwargs, price, mode):
    """(request to send, receipt). The request is returned unchanged in implicit mode or
    for a model without explicit caching."""
    if mode not in {"explicit", "off"} or price is None or getattr(price, "prompt_cache", "") != "explicit":
        return kwargs, None
    sent = dict(kwargs)
    sent["extra_body"] = {**(kwargs.get("extra_body") or {}), "prompt_cache_options": {"mode": "explicit"}}
    instructions = kwargs.get("instructions")
    prefix = stable_tokens(kwargs)
    if mode == "off" or not instructions or prefix < MIN_PREFIX_TOKENS:
        # Explicit mode without breakpoint: no cache write at all (a prefix below the
        # minimum would be written and never reused).
        return sent, {"mode": "explicit", "breakpoint": None, "stable_prefix_tokens": prefix}
    sent.pop("instructions")
    sent["input"] = [{"role": "developer", "content": [{"type": "input_text", "text": instructions,
        "prompt_cache_breakpoint": dict(BREAKPOINT)}]}, *(kwargs.get("input") or [])]
    return sent, {"mode": "explicit", "breakpoint": "instructions", "stable_prefix_tokens": prefix}
