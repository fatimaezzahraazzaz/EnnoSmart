# -*- coding: utf-8 -*-
from __future__ import annotations
import re

def normalize_text(text: str) -> str:
    if not text:
        return ''
    text = str(text)
    replacements = {'’':"'", '“':'"', '”':'"', '–':'-', '—':'-', '…':'...', ' ':' ', ' ':' '}
    for a,b in replacements.items(): text = text.replace(a,b)
    text = re.sub(r'(?<=[a-zA-ZÀ-ÿ])\.(?=[A-ZÀ-ÿ])', '. ', text)
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\.{2,}', '.', text)
    return text.strip()
