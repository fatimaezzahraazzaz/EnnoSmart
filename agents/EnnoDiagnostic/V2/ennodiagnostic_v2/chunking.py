import hashlib
import re
from typing import List, Tuple
from .config import ChunkConfig
from .document_router import detect_document_mode
from .schemas import Chunk, DocumentInput

HEADING_RE = re.compile(
    r"^(?:\d+(?:\.\d+)*[\s\-–—]+)?"
    r"([A-ZÀ-ÖØ-Ý][A-Za-zÀ-ÿ0-9 /'’()\-–—]{2,100})$"
)

def _clean(text: str) -> str:
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def _split_units(text: str) -> List[Tuple[str, str]]:
    text = _clean(text)
    if not text:
        return []
    units = []
    section = ""
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        lines = [x.strip() for x in block.split("\n") if x.strip()]
        if len(lines) == 1 and len(lines[0]) <= 110:
            if HEADING_RE.match(lines[0]) and len(lines[0].split()) <= 14:
                section = lines[0]
                continue

        bullets = [x for x in lines if re.match(r"^(?:[-•▪◦]|\d+[.)])\s+", x)]
        if bullets and len(bullets) == len(lines):
            units.extend((section, x) for x in lines)
        else:
            units.append((section, " ".join(lines)))
    return units

def _chunk_id(doc_id: str, index: int, text: str) -> str:
    d = hashlib.sha1(f"{doc_id}|{index}|{text}".encode()).hexdigest()[:12]
    return f"ch_{d}"

def build_chunks(document: DocumentInput, config: ChunkConfig) -> List[Chunk]:
    route = detect_document_mode(document.text, document.name, document.declared_mode)
    units = _split_units(document.text)
    if not units:
        return []

    raw_chunks = []
    current = []
    current_section = ""

    for section, unit in units:
        if section:
            current_section = section
        candidate = "\n".join(current + [unit]).strip()
        if current and len(candidate) > config.max_chars:
            text = "\n".join(current).strip()
            raw_chunks.append((current_section, text))
            overlap = text[-config.overlap_chars:] if config.overlap_chars else ""
            current = ([overlap] if overlap else []) + [unit]
        else:
            current.append(unit)

    if current:
        raw_chunks.append((current_section, "\n".join(current).strip()))

    # On ne jette pas les petits fragments : on les rattache au chunk précédent.
    merged = []
    for section, text in raw_chunks:
        if merged and len(text) < config.min_chunk_chars:
            ps, pt = merged[-1]
            merged[-1] = (ps or section, f"{pt}\n{text}".strip())
        else:
            merged.append((section, text))

    out = []
    for i, (section, text) in enumerate(merged):
        prev_text = merged[i - 1][1] if i else ""
        next_text = merged[i + 1][1] if i + 1 < len(merged) else ""
        out.append(Chunk(
            chunk_id=_chunk_id(document.document_id, i, text),
            document_id=document.document_id,
            document_name=document.name,
            text=text,
            context_before=prev_text[-config.context_chars:],
            context_after=next_text[:config.context_chars],
            section_title=section,
            document_mode=str(route["mode"]),
            metadata={
                **document.metadata,
                "route_confidence": route["confidence"],
                "route_reason": route["reason"],
                "chunk_index": i,
            },
        ))
    return out
