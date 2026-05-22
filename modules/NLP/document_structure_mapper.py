"""
modules/NLP/document_structure_mapper.py — NLP V7 SECTION-FIRST
──────────────────────────────────────────────────────────────────────────────
Rôle
----
Reconstruire une structure documentaire exploitable AVANT l'extraction CIR.

Pourquoi ?
- Les objectifs, verrous, travaux et résultats ne doivent pas dépendre seulement
  d'un passage isolé sélectionné par score.
- Ce module regroupe le document en sections longues, puis attribue un rôle
  documentaire : objectifs, verrous, etat_art, travaux, resultats, etc.

Sortie principale : DocumentStructure.sections
Chaque section contient : section_id, title, role, content, confidence, source.

Ce module ne fait PAS d'appel LLM. Il est volontairement déterministe,
non destructif et universel.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

SECTION_ROLES = {
    "titre_motscles",
    "administratif",
    "contexte",
    "objectifs",
    "etat_art",
    "verrous",
    "travaux",
    "demarche",
    "essais",
    "resultats",
    "conclusion",
    "ressources",
    "annexe",
    "unknown",
}

# Patterns sur titres. On garde des notions CIR/R&D universelles, pas métier.
ROLE_TITLE_PATTERNS: list[tuple[str, list[str]]] = [
    ("titre_motscles", [
        r"intitul[ée]\s+de\s+l['’]?op[ée]ration",
        r"nom\s+de\s+l['’]?op[ée]ration",
        r"titre\s+du\s+projet",
        r"mots?\s*[- ]?cl[ée]s?",
        r"th[ée]saurus",
        r"fiche\s+descriptive",
    ]),
    ("administratif", [
        r"chef\(s\)\s+de\s+projet",
        r"date\s+de\s+d[ée]but",
        r"date\s+de\s+fin",
        r"rescrit",
        r"agr[ée]ment",
        r"d[ée]penses?",
        r"budget",
        r"co[uû]ts?",
        r"etp",
        r"ressources?\s+humaines?",
        r"description\s+des\s+ressources",
    ]),
    ("contexte", [
        r"contexte",
        r"pr[ée]sentation",
        r"probl[ée]matique",
        r"introduction",
        r"enjeux?",
        r"background",
    ]),
    ("objectifs", [
        r"objectifs?",
        r"objectifs?\s+vis[ée]s?",
        r"finalit[ée]s?",
        r"buts?",
        r"ambition",
        r"performances?\s+[àa]\s+atteindre",
    ]),
    ("etat_art", [
        r"[ée]tat\s+de\s+l['’]?art",
        r"analyse\s+de\s+l['’]?[ée]tat\s+de\s+l['’]?art",
        r"bibliographie",
        r"travaux\s+existants",
        r"litt[ée]rature",
        r"prior\s+art",
        r"state\s+of\s+the\s+art",
    ]),
    ("verrous", [
        r"verrous?",
        r"incertitudes?",
        r"limites?\s+(?:scientifiques?|techniques?|technologiques?)",
        r"limitations?",
        r"difficult[ée]s",
        r"risques?\s+(?:scientifiques?|techniques?)",
        r"obstacles?",
        r"points?\s+durs?",
    ]),
    ("travaux", [
        r"travaux",
        r"description\s+des\s+travaux",
        r"travaux\s+r[ée]alis[ée]s?",
        r"r[ée]alisations?",
        r"d[ée]veloppement",
        r"mise\s+en\s+[œo]uvre",
        r"conception",
        r"mod[ée]lisation",
        r"simulation",
    ]),
    ("demarche", [
        r"d[ée]marche",
        r"m[ée]thodologie",
        r"approche\s+(?:retenue|propos[ée]e|scientifique)",
        r"protocole",
        r"plan\s+d['’]?essais?",
    ]),
    ("essais", [
        r"essais?",
        r"tests?",
        r"exp[ée]rimentations?",
        r"validation\s+exp[ée]rimentale",
        r"mesures?",
        r"campagne\s+d['’]?essais?",
    ]),
    ("resultats", [
        r"r[ée]sultats?",
        r"performances?\s+(?:obtenues?|mesur[ée]es?)",
        r"validation",
        r"analyse\s+des\s+r[ée]sultats",
        r"contribution\s+(?:scientifique|technique|technologique)",
    ]),
    ("conclusion", [
        r"conclusion",
        r"perspectives?",
        r"travaux\s+futurs?",
        r"synth[èe]se",
    ]),
    ("annexe", [
        r"annexes?",
        r"appendix",
    ]),
]


# ── TABLES RH / ADMINISTRATIF ────────────────────────────────────────────────
RH_TABLE_RE = re.compile(
    r"(NOM\s*Pr[ée]nom|Dipl[ôo]me\s+le\s+plus\s+[ée]lev[ée]|Fonction\s+dans\s+l['’]?op[ée]ration|"
    r"Contribution\s+directe\s+[àa]\s+l['’]?acquisition|Technicien\s+R&I|Responsable\s+R&I|Chef\s+de\s+projet|"
    r"Directeur\s+R&D|Ing[ée]nieur\s+R&D|Description\s+des\s+ressources\s+humaines)", re.I | re.U)


def _is_rh_section(title: str, content: str) -> bool:
    text = f"{title}\n{content}"
    return bool(RH_TABLE_RE.search(text) and (text.count("|") >= 3 or re.search(r"\b(BTS|Ing[ée]nieur|Technicien|Responsable)\b", text, re.I)))

_COMPILED_ROLE_TITLE_PATTERNS = [
    (role, re.compile(r"(?:" + "|".join(patterns) + r")", re.I | re.U))
    for role, patterns in ROLE_TITLE_PATTERNS
]

# Signaux de contenu utilisés seulement en fallback si le titre est absent.
CONTENT_ROLE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("verrous", re.compile(
        r"\b(verrou|incertitude|difficult[ée]|limite|manque|absence de|ne permet pas|"
        r"non r[ée]solu|insuffisamment document[ée]|reste difficile|obstacle|blocage|"
        r"incapacit[ée]|n['’]?existe pas|aucune solution)\b",
        re.I | re.U,
    )),
    ("objectifs", re.compile(
        r"\b(l['’]?objectif est|les objectifs? sont|ce projet vise|nous visons|"
        r"nous cherchons [àa]|l['’]?enjeu est|le but est|afin de|dans le but de|"
        r"permettre de|d[ée]velopper une solution)\b",
        re.I | re.U,
    )),
    ("resultats", re.compile(
        r"\b(r[ée]sultats? montrent|nous avons obtenu|on observe|les tests? ont|"
        r"les mesures? montrent|performance obtenue|gain de|r[ée]duction de|"
        r"am[ée]lioration de|a permis de valider|confirment)\b",
        re.I | re.U,
    )),
    ("travaux", re.compile(
        r"\b(nous avons r[ée]alis[ée]|nous avons d[ée]velopp[ée]|nous avons mis en place|"
        r"nous avons utilis[ée]|nous avons mod[ée]lis[ée]|simulation thermique|"
        r"prototype|d[ée]veloppement|conception|impl[ée]mentation)\b",
        re.I | re.U,
    )),
    ("etat_art", re.compile(
        r"\b(l['’]?[ée]tat de l['’]?art|la litt[ée]rature|travaux existants|"
        r"publications?|articles?|bibliographie|mod[èe]les existants)\b",
        re.I | re.U,
    )),
]

_HEADING_NUMBER_RE = re.compile(r"^\s*(?:\d+(?:\.\d+){0,6}\.?|[IVXLC]+\.|[A-Z]\.)\s+", re.I | re.U)
_PAGE_LINE_RE = re.compile(r"^\s*(?:page\s+)?\d+\s*(?:/|sur|of)\s*\d+\s*$", re.I | re.U)
_SHORT_TITLE_MAX = 150
_MIN_SECTION_CHARS = 120
_MAX_SECTION_CHARS = 9500


@dataclass
class DocumentSection:
    section_id: str
    title: str
    role: str
    content: str
    confidence: float = 0.6
    source_chunk_indexes: list[int] = field(default_factory=list)
    start_line: int = 0
    end_line: int = 0

    def to_dict(self) -> dict:
        return {
            "section_id": self.section_id,
            "title": self.title,
            "role": self.role,
            "content": self.content,
            "confidence": round(float(self.confidence), 3),
            "source_chunk_indexes": self.source_chunk_indexes,
            "start_line": self.start_line,
            "end_line": self.end_line,
        }


@dataclass
class DocumentStructure:
    sections: list[DocumentSection] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "sections": [s.to_dict() for s in self.sections],
            "stats": self.stats,
        }


def _norm_line(line: str) -> str:
    return re.sub(r"\s+", " ", str(line or "").strip())


def _strip_heading_number(line: str) -> str:
    return _HEADING_NUMBER_RE.sub("", _norm_line(line)).strip(" .\t")


def _role_from_title(title: str) -> tuple[str, float]:
    clean = _strip_heading_number(title)
    if not clean:
        return "unknown", 0.0
    if _is_rh_section(clean, ""):
        return "administratif", 0.99
    for role, pat in _COMPILED_ROLE_TITLE_PATTERNS:
        if pat.search(clean):
            return role, 0.95
    return "unknown", 0.0


def _role_from_content(text: str) -> tuple[str, float]:
    clean = str(text or "")[:2500]
    if _is_rh_section("", clean):
        return "administratif", 0.99
    for role, pat in CONTENT_ROLE_PATTERNS:
        if pat.search(clean):
            return role, 0.68
    return "unknown", 0.35


def _looks_like_heading(line: str) -> bool:
    line = _norm_line(line)
    if not line or _PAGE_LINE_RE.match(line):
        return False
    if len(line) > _SHORT_TITLE_MAX:
        return False
    if line.endswith(('.', ';', ',')) and not _HEADING_NUMBER_RE.match(line):
        return False
    role, _ = _role_from_title(line)
    if role != "unknown":
        return True
    if _HEADING_NUMBER_RE.match(line) and len(line.split()) <= 14:
        return True
    # Titres courts en majuscules ou style titre.
    letters = [c for c in line if c.isalpha()]
    if letters:
        upper_ratio = sum(1 for c in letters if c.isupper()) / max(len(letters), 1)
        if upper_ratio > 0.65 and len(line.split()) <= 12:
            return True
    return False


def _split_long_section(section: DocumentSection) -> list[DocumentSection]:
    content = section.content.strip()
    if len(content) <= _MAX_SECTION_CHARS:
        return [section]
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", content) if p.strip()]
    out: list[DocumentSection] = []
    buf: list[str] = []
    part = 1
    for p in paragraphs:
        if sum(len(x) for x in buf) + len(p) > _MAX_SECTION_CHARS and buf:
            clone = DocumentSection(
                section_id=f"{section.section_id}_{part}",
                title=f"{section.title} — partie {part}",
                role=section.role,
                content="\n\n".join(buf),
                confidence=section.confidence,
                source_chunk_indexes=list(section.source_chunk_indexes),
                start_line=section.start_line,
                end_line=section.end_line,
            )
            out.append(clone)
            buf = []
            part += 1
        buf.append(p)
    if buf:
        clone = DocumentSection(
            section_id=f"{section.section_id}_{part}",
            title=f"{section.title} — partie {part}" if part > 1 else section.title,
            role=section.role,
            content="\n\n".join(buf),
            confidence=section.confidence,
            source_chunk_indexes=list(section.source_chunk_indexes),
            start_line=section.start_line,
            end_line=section.end_line,
        )
        out.append(clone)
    return out


def _merge_tiny_sections(sections: list[DocumentSection]) -> list[DocumentSection]:
    if not sections:
        return []
    merged: list[DocumentSection] = []
    for sec in sections:
        if merged and len(sec.content) < _MIN_SECTION_CHARS and sec.role == "unknown":
            prev = merged[-1]
            prev.content = (prev.content.rstrip() + "\n" + sec.title + "\n" + sec.content).strip()
            prev.end_line = max(prev.end_line, sec.end_line)
            prev.source_chunk_indexes = sorted(set(prev.source_chunk_indexes + sec.source_chunk_indexes))
        else:
            merged.append(sec)
    return merged


def map_document_structure(chunks: list[str] | str, doc_id: str = "doc") -> DocumentStructure:
    """Reconstruit les sections du document à partir des chunks normalisés."""
    if isinstance(chunks, str):
        raw_chunks = [chunks]
    else:
        raw_chunks = [str(c or "") for c in chunks or []]

    # On conserve l'index de chunk pour debug.
    lines: list[tuple[str, int]] = []
    for idx, chunk in enumerate(raw_chunks):
        for line in str(chunk or "").splitlines():
            clean = _norm_line(line)
            if clean:
                lines.append((clean, idx))
        # Séparateur léger entre chunks.
        lines.append(("", idx))

    sections: list[DocumentSection] = []
    current_title = "Début du document"
    current_role = "unknown"
    current_conf = 0.35
    current_lines: list[str] = []
    current_indexes: set[int] = set()
    start_line = 0

    def flush(end_line: int) -> None:
        nonlocal current_title, current_role, current_conf, current_lines, current_indexes, start_line
        content = "\n".join([l for l in current_lines if l.strip()]).strip()
        if not content and not current_title:
            return
        role = current_role
        conf = current_conf
        if _is_rh_section(current_title, content):
            role, conf = "administratif", 0.99
        elif role == "unknown":
            inferred_role, inferred_conf = _role_from_content(content)
            role, conf = inferred_role, inferred_conf
        sec_id = f"{doc_id}_S{len(sections)+1:04d}"
        sections.append(DocumentSection(
            section_id=sec_id,
            title=current_title or f"Section {len(sections)+1}",
            role=role if role in SECTION_ROLES else "unknown",
            content=content,
            confidence=conf,
            source_chunk_indexes=sorted(current_indexes),
            start_line=start_line,
            end_line=end_line,
        ))

    for i, (line, chunk_idx) in enumerate(lines):
        if not line:
            if current_lines and current_lines[-1] != "":
                current_lines.append("")
            continue
        if _looks_like_heading(line):
            if current_lines:
                flush(i)
            title = _strip_heading_number(line)
            role, conf = _role_from_title(title)
            current_title = title or line
            current_role = role
            current_conf = conf if conf else 0.45
            current_lines = []
            current_indexes = {chunk_idx}
            start_line = i
        else:
            current_lines.append(line)
            current_indexes.add(chunk_idx)

    if current_lines or current_title:
        flush(len(lines))

    sections = [s for sec in _merge_tiny_sections(sections) for s in _split_long_section(sec)]
    # Supprimer sections vides très faibles.
    sections = [s for s in sections if s.content.strip() or s.role in {"titre_motscles"}]

    role_counts: dict[str, int] = {}
    for s in sections:
        role_counts[s.role] = role_counts.get(s.role, 0) + 1

    result = DocumentStructure(
        sections=sections,
        stats={
            "chunks": len(raw_chunks),
            "lines": len(lines),
            "sections": len(sections),
            "role_counts": role_counts,
            "structured_ratio": round(
                sum(1 for s in sections if s.role != "unknown") / max(len(sections), 1), 3
            ),
        },
    )
    logger.info("Structure document : %d sections | rôles=%s", len(sections), role_counts)
    return result


if __name__ == "__main__":
    import json
    import sys
    path = sys.argv[1]
    text = open(path, encoding="utf-8").read()
    print(json.dumps(map_document_structure(text, "debug").to_dict(), ensure_ascii=False, indent=2)[:5000])
