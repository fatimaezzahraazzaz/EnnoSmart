import hashlib
import re
import unicodedata
from typing import List, Tuple

from .config import ChunkConfig
from .document_router import detect_document_mode
from .schemas import Chunk, DocumentInput


# Titres numérotés : 1.3.2.1. Limitations...
NUMBERED_HEADING_RE = re.compile(
    r"^\s*\d+(?:\.\d+)*\.?\s+(.+?)\s*$"
)

# Titres non numérotés courts : "Etat de l'art", "Verrous et incertitudes..."
PLAIN_HEADING_RE = re.compile(
    r"^[A-ZÀ-ÖØ-Ý][A-Za-zÀ-ÿ0-9 /'’()\-–—]{2,140}$"
)

BULLET_RE = re.compile(r"^(?:[-•▪◦ü✓✔☑]|(?:\d+)[.)])\s+")

# Dans une section décrivant les travaux, ces lignes sont des étapes, même
# lorsqu'un export DOCX les rend identiques à des titres non numérotés.
WORK_SECTION_RE = re.compile(
    r"(?:organisation|plan|etapes?|deroulement|methodologie|workflow|protocole)"
    r".*(?:travaux|recherche|technique|experimental|experimentation)"
    r"|^(?:methodologie|travaux (?:de recherche|r.?d|techniques?))$"
)
STEP_START_RE = re.compile(
    r"^(?:etat de l.art|etude|analyse|conception|definition|recherche|"
    r"collecte|preparation|nettoyage|annotation|modelisation|comparaison|"
    r"proposition|developpement|implementation|experimentation|essais?|"
    r"tests?|validation|evaluation|simulation|prototypage|entrainement|"
    r"optimisation|integration|mesure)\b"
)


def _fold(text: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKD", text.lower())
        if not unicodedata.combining(char)
    )


def explicit_work_step_lines(section: str, text: str) -> List[str]:
    """Repère les entrées autonomes d'une liste de travaux techniques."""
    if not WORK_SECTION_RE.search(_fold(section)):
        return []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    steps = [
        line for line in lines
        if STEP_START_RE.match(_fold(BULLET_RE.sub("", line)))
        and not re.search(
            r"\b(?:comite|eligibilite|budget|recrutement|ressources? humaines?|"
            r"sujets? de recherche|dossiers? cir|validation administrative)\b",
            _fold(line),
        )
    ]
    # Une ligne isolée peut être un simple sous-titre. Le rattrapage ne
    # s'applique qu'aux séquences de plusieurs étapes explicitement ordonnées.
    return steps if len(steps) >= 2 else []


def _clean(text: str) -> str:
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _is_heading(line: str) -> bool:
    line = str(line or "").strip()
    if not line:
        return False

    # Evite de transformer des phrases ordinaires en titres.
    if line.endswith((".", ";", "?", "!")):
        return False

    numbered = NUMBERED_HEADING_RE.match(line)
    if numbered:
        title = numbered.group(1).strip()
        return len(title) <= 180 and len(title.split()) <= 24

    return (
        len(line) <= 140
        and len(line.split()) <= 16
        and bool(PLAIN_HEADING_RE.match(line))
    )


def _split_units(text: str) -> List[Tuple[str, str]]:
    """
    Découpe le document en unités sémantiques sans perdre de contenu.

    Différence importante par rapport à l'ancienne version :
    - les titres sont détectés ligne par ligne, même sans ligne vide autour ;
    - une nouvelle section ne peut plus être fusionnée avec la précédente ;
    - aucun contenu n'est filtré.
    """
    text = _clean(text)
    if not text:
        return []

    units: List[Tuple[str, str]] = []
    section = ""

    def flush(buffer: List[str]) -> None:
        if buffer:
            value = " ".join(x.strip() for x in buffer if x.strip()).strip()
            if value:
                units.append((section, value))
            buffer.clear()

    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue

        buffer: List[str] = []

        # Important : on inspecte CHAQUE ligne.
        # Cela permet de détecter un titre qui se trouve au milieu d'un bloc
        # extrait d'un DOCX sans paragraphes vides.
        for raw_line in block.split("\n"):
            line = raw_line.strip()
            if not line:
                continue

            is_work_step = (
                bool(WORK_SECTION_RE.search(_fold(section)))
                and bool(STEP_START_RE.match(_fold(line)))
            )
            if _is_heading(line) and not is_work_step:
                flush(buffer)
                section = line
                continue

            if BULLET_RE.match(line) or is_work_step:
                flush(buffer)
                units.append((section, line))
                continue

            buffer.append(line)

        flush(buffer)

    return units


def _chunk_id(doc_id: str, index: int, text: str) -> str:
    d = hashlib.sha1(f"{doc_id}|{index}|{text}".encode()).hexdigest()[:12]
    return f"ch_{d}"


def build_chunks(document: DocumentInput, config: ChunkConfig) -> List[Chunk]:
    route = detect_document_mode(
        document.text,
        document.name,
        document.declared_mode,
    )

    units = _split_units(document.text)
    if not units:
        return []

    raw_chunks: List[Tuple[str, str]] = []
    current: List[str] = []
    current_section = ""

    def flush_current() -> None:
        nonlocal current
        if current:
            text = "\n".join(current).strip()
            if text:
                raw_chunks.append((current_section, text))
            current = []

    for section, unit in units:
        effective_section = section or current_section

        # REGLE PRINCIPALE :
        # dès qu'une nouvelle section commence, on ferme le chunk précédent.
        # On ne mélange donc plus Etat de l'art / Verrous / Travaux R&D.
        if current and effective_section != current_section:
            flush_current()

        current_section = effective_section

        candidate = "\n".join(current + [unit]).strip()

        if current and len(candidate) > config.max_chars:
            text = "\n".join(current).strip()
            raw_chunks.append((current_section, text))

            # L'overlap est autorisé uniquement DANS LA MEME SECTION.
            overlap = (
                text[-config.overlap_chars:]
                if config.overlap_chars
                else ""
            )
            current = ([overlap] if overlap else []) + [unit]
        else:
            current.append(unit)

    flush_current()

    # Les petits fragments peuvent être rattachés seulement à un chunk
    # de la MEME section. Jamais à la section précédente.
    merged: List[Tuple[str, str]] = []

    for section, text in raw_chunks:
        if (
            merged
            and len(text) < config.min_chunk_chars
            and merged[-1][0] == section
        ):
            previous_section, previous_text = merged[-1]
            merged[-1] = (
                previous_section,
                f"{previous_text}\n{text}".strip(),
            )
        else:
            merged.append((section, text))

    out: List[Chunk] = []

    for i, (section, text) in enumerate(merged):
        # Le contexte avant/après reste lui aussi limité à la même section.
        # Cela évite qu'un passage de littérature influence la provenance
        # d'une preuve située dans une section projet, et inversement.
        prev_text = ""
        next_text = ""

        if i and merged[i - 1][0] == section:
            prev_text = merged[i - 1][1]

        if i + 1 < len(merged) and merged[i + 1][0] == section:
            next_text = merged[i + 1][1]

        out.append(
            Chunk(
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
                    "section_boundary_guard": True,
                },
            )
        )

    return out
