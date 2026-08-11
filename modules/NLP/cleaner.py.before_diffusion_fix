# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import List

TEMPLATE_PATTERNS = [
    r'quelles? sont les questions', r'quels? sont les enjeux', r'quels? environnements',
    r'quelles? démarches envisagez', r'quelles? sont les difficultés opérationnelles',
    r'décrire précisément', r'pour information', r'tapez ici', r'nom de la présentation',
    r'document security', r'charte graphique', r'diffusion', r'nom prénom', r'nombre d.exemplaire',
    r'erreur\s*!\s*nom de propriété', r'tableau obligatoire', r'écrivez le nom',
]

NOISE_TOKENS = [
    'ExtractionResult(', 'file_category=<', 'FileCategory.', 'SourceTag.', 'source_path=',
    'text_chunks=', 'visual_chunks=', 'structured_data=', 'attachments_paths=',
    'extraction_errors=', 'ocr_needed_pages=', 'AppData\\Local\\Temp', 'ennosmart_nlp_uploads',
]

# En-têtes, pieds de page et coordonnées souvent répétés dans les rapports.
# Ces motifs restent génériques : ils ne dépendent d'aucun projet ni domaine.
METADATA_PATTERNS = (
    r"\b(?:https?://|www\.|e-?mail\s*:|courriel\s*:)\b",
    r"\b(?:siret|siren|code\s+ape|tva\s+intracommunautaire|sas\s+au\s+capital|sarl\s+au\s+capital)\b",
    r"\bpage\s+\d+\s*(?:/|sur)\s*\d+\b",
    r"^\s*(?:indice|r[ée]v(?:ision)?|version)\s+[a-z0-9._-]+\s*$",
    r"^\s*(?:r[ée]dig[ée]|written\s+by|v[ée]rifi[ée]|approved\s+by|date|modification)\s*[:|]",
    r"^\s*[a-z0-9._-]{0,8}\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\s+(?:cr[ée]ation|modification|mise\s+[àa]\s+jour|validation)\b",
    r"\b\d{2}(?:[ .]\d{2}){4}\b",
)

TECHNICAL_HINT = re.compile(
    r"\b(?:objectif|essai|mesure|r[ée]sultat|mod[èe]le|simulation|prototype|"
    r"incertitude|contrainte|performance|configuration|validation|param[èe]tre)\b",
    flags=re.I,
)


def is_metadata_line(line: str) -> bool:
    """Détecte une ligne administrative sans supprimer une phrase technique."""
    value = str(line or "").strip()
    if not value:
        return False
    if TECHNICAL_HINT.search(value):
        return False
    return any(re.search(pattern, value, flags=re.I) for pattern in METADATA_PATTERNS)


def clean_text(text: str) -> str:
    if not text:
        return ''
    text = str(text).replace('\xa0', ' ').replace('\u202f', ' ').replace('\ufeff', '')
    text = text.replace('\r', '\n').replace('\t', ' ')
    lines: List[str] = []
    seen_lines: set[str] = set()
    for raw in text.splitlines():
        line = normalize_line(raw.strip())
        if not line:
            lines.append('')
            continue
        if is_noise_line(line):
            continue
        # Enlever préfixes tables/slides tout en gardant le contenu.
        line = re.sub(r'^\[TABLEAU\]\s*', '', line, flags=re.I).strip()
        line = re.sub(r'^\[SLIDE\s+\d+\s*:\s*', '[SLIDE] ', line, flags=re.I).strip()
        if not is_noise_line(line):
            # Les rapports PDF répètent souvent le même en-tête sur chaque page.
            # On déduplique seulement les lignes assez longues, sans fusionner
            # deux documents (clean_text est appelé document par document).
            key = re.sub(r"\W+", " ", line.lower()).strip()
            if len(key) >= 28 and key in seen_lines:
                continue
            if len(key) >= 28:
                seen_lines.add(key)
            lines.append(line)
    text = '\n'.join(lines)
    text = remove_bad_blocks(text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ ]{2,}', ' ', text)
    return text.strip()


def normalize_line(line: str) -> str:
    line = line.replace('\\n', ' ').replace('\\xa0', ' ')
    line = re.sub(r'^\[PAGE\s+\d+\]\s*', '', line, flags=re.I)
    line = re.sub(r'\|\s*\|+', '|', line)
    line = re.sub(r'\s+', ' ', line)
    return line.strip(' -|')


def is_noise_line(line: str) -> bool:
    low = line.lower()
    if any(t.lower() in low for t in NOISE_TOKENS):
        return True
    if is_metadata_line(line):
        return True
    if any(re.search(p, low) for p in TEMPLATE_PATTERNS):
        # Si la ligne contient une vraie phrase utile après la question, elle sera reconstruite par candidates depuis autres fenêtres.
        return True
    if re.search(r'[a-zA-Z]:\\', line):
        return True
    if re.match(r'^[\]\)\}],?\s*$', line):
        return True
    if re.match(r'^[a-zA-Z_]+=', line) and len(line) < 160:
        return True
    words = re.findall(r'[A-Za-zÀ-ÿ]{3,}', line)
    if len(line) > 35 and len(words) < 3:
        return True
    return False


def remove_bad_blocks(text: str) -> str:
    out = []
    for block in re.split(r'\n\s*\n', text):
        b = block.strip()
        if not b:
            continue
        low = b.lower()
        if any(t.lower() in low for t in NOISE_TOKENS):
            continue
        if sum(1 for p in TEMPLATE_PATTERNS if re.search(p, low)) >= 1 and len(b) < 500:
            continue
        words = re.findall(r'[A-Za-zÀ-ÿ]{3,}', b)
        if len(b) > 120 and len(words) < 8:
            continue
        out.append(b)
    return '\n\n'.join(out)


def clean_many(texts: List[str]) -> List[str]:
    return [clean_text(t) for t in texts]
