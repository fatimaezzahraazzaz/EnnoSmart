# -*- coding: utf-8 -*-
from __future__ import annotations

"""Technical-source compatibility shim.

The former static multi-domain catalogue was removed from the active search
because it encoded domain profiles and returned the same predeclared sources.
Technical artifacts are now discovered dynamically by source_router through
GitHub/Hugging Face when enabled.  The public function remains for backward
compatibility with scholar_agent.py.
"""

from typing import Any, Dict, List


def get_technical_sources_for_intent(
    intent: Dict[str, Any],
    max_sources: int = 6,
) -> List[Dict[str, Any]]:
    _ = intent, max_sources
    return []
