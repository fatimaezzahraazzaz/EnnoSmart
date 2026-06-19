# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Any, Dict, List
import re

PACK_KEYS = ['objectifs_locaux','verrous_rnd_locaux','methodes_locales','resultats_locaux','limites_locales','contributions_locales','etat_art_local','parametres_locaux']

def _norm(s: str) -> str:
    s = str(s or '').lower()
    s = re.sub(r'[^a-z0-9àâäéèêëîïôöùûüç]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()

def _dedupe(arr: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out, seen = [], set()
    for x in arr or []:
        if not isinstance(x, dict):
            continue
        key = (_norm(x.get('document','')), _norm(x.get('role','')), _norm(x.get('text',''))[:220])
        if key in seen:
            continue
        seen.add(key)
        out.append(x)
    return out

def merge_evidence_packs(raw_pack: Dict[str, Any] | None = None, cir_pack: Dict[str, Any] | None = None) -> Dict[str, List[Dict[str, Any]]]:
    raw_pack = raw_pack or {}
    cir_pack = cir_pack or {}
    merged = {k: [] for k in PACK_KEYS}
    for k in PACK_KEYS:
        # CIR d'abord car c'est structuré, puis RAW comme preuves support.
        merged[k] = _dedupe(list(cir_pack.get(k) or []) + list(raw_pack.get(k) or []))
    return merged
