# -*- coding: utf-8 -*-
"""Portées Chroma centralisées pour EnnoSmart.

Une seule instance Chroma peut servir plusieurs organismes, mais chaque accès
applicatif doit être limité à une portée explicite. Les projets utilisent leur
propre namespace (ProjectStore). La mémoire d'expérience V2 est volontairement
partagée entre projets d'un même organisme, jamais entre organismes.
"""
from __future__ import annotations

import hashlib
import re
from typing import Dict


def safe_scope_identifier(value: object, default: str = "unknown") -> str:
    raw = str(value or "").strip().lower()
    tr = str.maketrans("àâäéèêëîïôöùûüç’'", "aaaeeeeiioouuuc__")
    raw = raw.translate(tr)
    raw = re.sub(r"[^a-z0-9]+", "_", raw)
    raw = re.sub(r"_+", "_", raw).strip("_")
    return raw[:80] or default


def organism_memory_collection(organisme: object) -> str:
    """Nom stable d'une collection Memory V2 isolée par organisme."""
    raw = str(organisme or "").strip()
    if not raw:
        raise ValueError("organisme est obligatoire pour Memory V2")
    slug = safe_scope_identifier(raw, "organisme")
    digest = hashlib.sha256(raw.casefold().encode("utf-8")).hexdigest()[:8]
    return f"ennosmart_memory_v2_org_{slug}_{digest}"


def organism_memory_scope(organisme: object) -> Dict[str, str]:
    raw = str(organisme or "").strip()
    if not raw:
        raise ValueError("organisme est obligatoire pour Memory V2")
    org_id = safe_scope_identifier(raw, "organisme")
    raw_scope = f"organism_memory|{org_id}"
    return {
        "ennosmart_scope_type": "organism_memory",
        "ennosmart_scope_id": hashlib.sha256(raw_scope.encode("utf-8")).hexdigest()[:24],
        "ennosmart_organisme_id": org_id,
    }
