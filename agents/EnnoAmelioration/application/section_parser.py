from __future__ import annotations

import hashlib
import re
import unicodedata

from ..domain.models import ParsedSection


_MARKDOWN_HEADING = re.compile(r"(?m)^(#{1,6})[ \t]+(.+?)[ \t]*$")
_NUMBERED_HEADING = re.compile(
    r"(?m)^(?P<prefix>(?:\d+\.)+|\d+\)|[IVXLC]+\.|[A-Z]\.)[ \t]+(?P<title>[^\n]{3,180}?)[ \t]*$"
)
_LINE = re.compile(r"(?m)^(?P<title>[^\n]+?)[ \t]*$")
_IMMUTABLE_DOCUMENT_BLOCK = re.compile(
    r"(?ms)^\[BLOC DOCUMENT IMMUTABLE\s+id=\"[^\"]+\"[^\]]*\]\s*$"
    r".*?^\[/BLOC DOCUMENT IMMUTABLE\]\s*$"
)


def _looks_like_toc_entry(value: str) -> bool:
    line = str(value or "").strip()
    return bool(re.search(r"(?:\.{3,}|…{2,})[ \t]*\d+(?:\s*[-–]\s*\d+)?$", line))


def _title_without_glued_page_numbers(value: str) -> list[str]:
    """Variantes d'un titre dont le numéro de page a été collé au dernier mot."""

    title = str(value or "").strip()
    trailing = re.search(r"\d{1,6}$", title)
    if trailing is None:
        return []

    variants: list[str] = []
    digit_count = len(trailing.group(0))
    for removed in range(1, min(4, digit_count) + 1):
        candidate = title[:-removed].rstrip(" .\t")
        if (
            len(candidate) >= 3
            and any(char.isalpha() for char in candidate)
            and candidate not in variants
        ):
            variants.append(candidate)
    return variants


def _mapped_toc_body_headings(
    source: str,
    numbered_candidates: list[tuple[re.Match[str], str]],
) -> tuple[dict[int, tuple[int, int, str, int]], int | None]:
    """Rattache un sommaire PDF sans libellé aux vrais titres du corps.

    Certains exports suppriment « Sommaire », les pointillés et la numérotation
    dans le corps, tout en collant le numéro de page au titre du sommaire. On
    reconstruit alors la hiérarchie uniquement lorsque plusieurs titres se
    retrouvent, dans le même ordre, plus loin dans le document.
    """

    line_matches = list(_LINE.finditer(source))
    mapped: dict[int, tuple[int, int, str, int]] = {}
    body_cursor = 0

    for index, (candidate, _) in enumerate(numbered_candidates):
        # Dès que les candidats numérotés appartiennent déjà au corps, ils ne
        # doivent pas être remappés vers une répétition ultérieure.
        if mapped and candidate.start() >= body_cursor:
            continue

        variants = {
            _match_text(value): value
            for value in _title_without_glued_page_numbers(
                candidate.group("title")
            )
            if _match_text(value)
        }
        if not variants:
            continue

        search_from = max(candidate.end(), body_cursor)
        body_match: re.Match[str] | None = None
        body_title = ""
        for line in line_matches:
            if line.start() < search_from:
                continue
            normalized = _match_text(line.group("title"))
            if normalized in variants:
                body_match = line
                body_title = line.group("title").strip()
                break

        if body_match is None:
            continue

        prefix = candidate.group("prefix")
        level = max(1, prefix.count(".")) if prefix[0].isdigit() else 1
        visible_title = f"{prefix} {body_title}".strip()
        mapped[index] = (
            body_match.start(),
            body_match.end(),
            visible_title,
            level,
        )
        body_cursor = body_match.end()

    # Une correspondance isolée pourrait être une répétition normale. Trois
    # titres ordonnés constituent un signal structurel de sommaire.
    if len(mapped) < 3:
        return {}, None
    return mapped, min(value[0] for value in mapped.values())


def repair_section_boundaries(text: str) -> str:
    """Restaure un titre Markdown anciennement collé à la phrase précédente.

    La réparation est uniquement structurelle : aucun intitulé métier ou plan
    CIR n'est connu ici et aucune nouvelle section n'est inventée.
    """

    source = str(text or "")
    return re.sub(r"([^\r\n])(?=#{1,6}[ \t]+\S)", r"\1\n\n", source)


def _mask_immutable_document_blocks(source: str) -> str:
    """Masque les tableaux/figures protégés sans modifier les offsets."""

    def replace(match: re.Match[str]) -> str:
        return "".join("\n" if char == "\n" else " " for char in match.group(0))

    return _IMMUTABLE_DOCUMENT_BLOCK.sub(replace, source)


def _stable_id(title: str, start: int) -> str:
    seed = f"{title.strip().casefold()}:{start}".encode("utf-8", errors="ignore")
    return "sec-" + hashlib.sha1(seed).hexdigest()[:12]


def _match_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return " ".join(re.findall(r"[a-z0-9]+", normalized.casefold()))


def infer_section_from_instruction(
    instruction: str,
    sections: list[ParsedSection],
) -> ParsedSection | None:
    """Rapproche la demande des titres réellement présents dans le document."""

    message = str(instruction or "")
    numeric_refs = re.findall(r"\b\d+(?:\.\d+){1,6}\b", message)
    for reference in numeric_refs:
        for section in sections:
            first_line = (
                str(section.content or "").lstrip().splitlines()[0]
                if section.content
                else ""
            )
            visible_heading = f"{section.title}\n{first_line}"
            found_references = re.findall(
                r"(?<!\d)(\d+(?:\.\d+)+)\.?(?!\d)",
                visible_heading,
            )
            if reference in found_references:
                return section

    normalized_message = _match_text(message)
    message_tokens = set(normalized_message.split())
    ignored = {
        "a", "au", "aux", "avec", "ce", "ces", "cette", "chapitre", "dans",
        "de", "des", "du", "et", "la", "le", "les", "l", "partie", "passage",
        "section", "sous", "texte", "un", "une",
    }
    best: ParsedSection | None = None
    best_score = 0.0
    for section in sections:
        normalized_title = _match_text(section.title)
        if not normalized_title:
            continue
        title_tokens = {
            token
            for token in normalized_title.split()
            if token not in ignored and len(token) >= 3
        }
        if not title_tokens:
            continue
        exact_title_match = bool(
            normalized_title in normalized_message
            and (
                len(title_tokens) >= 2
                or max((len(token) for token in title_tokens), default=0) >= 4
            )
        )
        if exact_title_match:
            score = 100.0 + len(title_tokens)
        else:
            overlap = title_tokens & message_tokens
            if not overlap:
                continue
            coverage = len(overlap) / len(title_tokens)
            precision = len(overlap) / max(1, len(message_tokens))
            score = coverage * 10.0 + precision
            if len(overlap) == 1 and len(title_tokens) > 1:
                score *= 0.35
        if score > best_score:
            best = section
            best_score = score
    return best if best_score >= 3.0 else None


def _looks_like_plain_heading(source: str, match: re.Match[str]) -> bool:
    title = match.group("title").strip()
    words = re.findall(r"[A-Za-zÀ-ÿ0-9][\wÀ-ÿ'-]*", title)
    if not (1 <= len(words) <= 12) or not (3 <= len(title) <= 140):
        return False
    if title.endswith((".", "!", "?", ";")) or "," in title:
        return False
    if title.startswith(("-", "•", "|", "[")):
        return False
    before = source[: match.start()]
    after = source[match.end():]
    isolated_before = not before.strip() or bool(re.search(r"\n\s*\n\s*$", before))
    isolated_after = bool(re.match(r"\s*\n\s*\n", after))
    if not (isolated_before and isolated_after):
        return False
    alpha = [word for word in words if any(char.isalpha() for char in word)]
    uppercase = bool(alpha) and all(word.upper() == word for word in alpha)
    title_case = bool(alpha) and sum(word[:1].isupper() for word in alpha) >= max(1, len(alpha) // 2)
    # Un intitulé court isolé est une structure éditoriale, quel que soit son
    # vocabulaire. Aucun titre métier ou plan CIR n'est codé en dur.
    return uppercase or title_case or len(words) <= 7


def _numbered_heading_score(source: str, match: re.Match[str]) -> float:
    """Evalue une ligne numerotee sans connaitre le vocabulaire du document."""

    title = match.group("title").strip()
    words = re.findall(r"[^\W_][\w'-]*", title, flags=re.UNICODE)
    if not words:
        return -10.0
    alpha = [word for word in words if any(char.isalpha() for char in word)]
    uppercase = bool(alpha) and all(word.upper() == word for word in alpha)
    title_case = bool(alpha) and sum(
        word[:1].isupper() for word in alpha
    ) >= max(1, len(alpha) // 2)
    before = source[: match.start()]
    after = source[match.end():]
    isolated_before = not before.strip() or bool(re.search(r"\n\s*\n\s*$", before))
    isolated_after = not after.strip() or bool(re.match(r"\s*\n\s*\n", after))
    depth = max(1, match.group("prefix").count("."))

    score = 0.0
    if uppercase:
        score += 4.0
    elif title_case:
        score += 2.0
    if isolated_before:
        score += 1.0
    if isolated_after:
        score += 1.0
    if depth >= 2:
        score += 1.5
    if title.endswith((".", "!", "?", ";", ",")):
        score -= 3.0
    if len(words) > 24:
        score -= 1.5
    return score


def _paragraph_sections(source: str) -> list[ParsedSection]:
    blocks = list(re.finditer(r"(?ms)(?P<block>\S.*?\S|\S)(?=\n[ \t]*\n|\Z)", source))
    if len(blocks) <= 1:
        return []
    sections: list[ParsedSection] = []
    for index, match in enumerate(blocks, start=1):
        content = match.group("block")
        words = re.findall(r"\b[\wÀ-ÿ'-]+\b", content)
        excerpt = " ".join(words[:8])
        title = excerpt + ("…" if len(words) > 8 else "")
        sections.append(
            ParsedSection(
                section_id=_stable_id(title or f"Paragraphe {index}", match.start()),
                title=title or f"Paragraphe {index}",
                level=1,
                start=match.start(),
                end=match.end(),
                content=content,
            )
        )
    return sections


def parse_sections(text: str, *, paragraph_fallback: bool = True) -> list[ParsedSection]:
    """Découpe un document selon sa structure réelle, sans liste de titres imposée."""

    source = str(text or "")
    match_source = _mask_immutable_document_blocks(source)
    matches: list[tuple[int, int, str, int]] = []
    occupied: list[tuple[int, int]] = []

    for match in _MARKDOWN_HEADING.finditer(match_source):
        matches.append((match.start(), match.end(), match.group(2).strip(), len(match.group(1))))
        occupied.append((match.start(), match.end()))

    numbered_candidates: list[tuple[re.Match[str], str]] = []
    for match in _NUMBERED_HEADING.finditer(match_source):
        if any(left <= match.start() < right for left, right in occupied):
            continue
        if _looks_like_toc_entry(match.group(0)):
            continue
        prefix_key = match.group("prefix").rstrip(".").casefold()
        numbered_candidates.append((match, prefix_key))

    mapped_toc_headings, inferred_body_start = _mapped_toc_body_headings(
        source,
        numbered_candidates,
    )

    # Un sommaire extrait d'un PDF contient souvent les memes numeros que le
    # corps, parfois sans pointilles lorsque le titre est replie sur deux
    # lignes. Pour un numero repete, l'occurrence la plus tardive correspond au
    # corps du document. Cette regle repose uniquement sur la structure du
    # document et ne connait aucun titre ou plan CIR.
    best_numbered_occurrence: dict[str, int] = {}
    best_numbered_score: dict[str, tuple[float, int]] = {}
    for index, (candidate, prefix_key) in enumerate(numbered_candidates):
        if (
            inferred_body_start is not None
            and candidate.start() < inferred_body_start
        ):
            continue
        rank = (_numbered_heading_score(source, candidate), index)
        if rank > best_numbered_score.get(prefix_key, (-1000.0, -1)):
            best_numbered_score[prefix_key] = rank
            best_numbered_occurrence[prefix_key] = index

    for index, (match, prefix_key) in enumerate(numbered_candidates):
        if (
            inferred_body_start is not None
            and match.start() < inferred_body_start
        ):
            continue
        if best_numbered_occurrence.get(prefix_key) != index:
            continue
        prefix = match.group("prefix")
        if prefix.rstrip(".").isdigit() and prefix.count(".") == 1:
            # Une enumeration simple ``1. phrase`` ne devient une section que
            # si sa presentation ressemble reellement a un titre.
            if _numbered_heading_score(source, match) < 1.0:
                continue
        level = max(1, prefix.count(".")) if prefix[0].isdigit() else 1
        matches.append((match.start(), match.end(), match.group("title").strip(), level))

    matches.extend(mapped_toc_headings.values())

    numbered_hierarchy_count = sum(
        1
        for match, _ in numbered_candidates
        if match.group("prefix")[0].isdigit()
        and match.group("prefix").count(".") >= 2
    )
    has_explicit_numbered_hierarchy = numbered_hierarchy_count >= 3

    # Dans un document long possédant déjà une hiérarchie numérotée, une ligne
    # courte isolée est beaucoup plus souvent une cellule, une légende ou un
    # fragment de mise en page qu'un titre. La typographie PDF structurée et la
    # numérotation restent souveraines ; aucun vocabulaire métier n'est utilisé.
    if not has_explicit_numbered_hierarchy:
        for match in _LINE.finditer(match_source):
            if any(left <= match.start() < right for left, right in occupied):
                continue
            if any(start <= match.start() < end for start, end, _, _ in matches):
                continue
            if _looks_like_plain_heading(match_source, match):
                matches.append((match.start(), match.end(), match.group("title").strip(), 1))

    matches.sort(key=lambda item: item[0])
    if not matches:
        # Une section collée n'est pas un document composé de sous-sections :
        # ses paragraphes restent un seul bloc lorsqu'elle est la cible.
        paragraph_sections = _paragraph_sections(source) if paragraph_fallback else []
        if paragraph_sections:
            return paragraph_sections
        return [
            ParsedSection(
                section_id=_stable_id("Document complet", 0),
                title="Document complet",
                level=1,
                start=0,
                end=len(source),
                content=source,
            )
        ] if source.strip() else []

    sections: list[ParsedSection] = []
    if (
        not has_explicit_numbered_hierarchy
        and matches[0][0] > 0
        and source[: matches[0][0]].strip()
    ):
        sections.append(
            ParsedSection(
                section_id=_stable_id("Préambule", 0),
                title="Préambule",
                level=1,
                start=0,
                end=matches[0][0],
                content=source[: matches[0][0]],
            )
        )

    for index, (start, heading_end, title, level) in enumerate(matches):
        end = matches[index + 1][0] if index + 1 < len(matches) else len(source)
        sections.append(
            ParsedSection(
                section_id=_stable_id(title, start),
                title=title,
                level=level,
                start=start,
                end=end,
                content=source[start:end],
            )
        )
    return sections


def resolve_target(
    full_text: str,
    sections: list[ParsedSection],
    *,
    section_id: str | None = None,
    section_title: str | None = None,
    selected_text: str | None = None,
) -> tuple[str, ParsedSection | None]:
    def with_descendants(index: int) -> tuple[str, ParsedSection]:
        section = sections[index]
        end = len(full_text)
        for following in sections[index + 1:]:
            if following.level <= section.level:
                end = following.start
                break
        return full_text[section.start:end], section

    if selected_text and selected_text.strip() and selected_text.strip() in full_text:
        return selected_text.strip(), None
    if section_id:
        for index, section in enumerate(sections):
            if section.section_id == section_id:
                return with_descendants(index)
    if section_title:
        wanted = section_title.strip().casefold()
        exact = [
            (index, section)
            for index, section in enumerate(sections)
            if section.title.strip().casefold() == wanted
        ]
        if exact:
            return with_descendants(exact[0][0])
        partial = [
            (index, section)
            for index, section in enumerate(sections)
            if wanted in section.title.strip().casefold()
        ]
        if partial:
            return with_descendants(partial[0][0])
    # Lorsqu'un texte chargé ne contient qu'une seule section réelle, « cette
    # section » est non ambigu. La conserver comme cible évite de promouvoir
    # artificiellement la demande en révision de document complet, ce qui créait
    # ensuite un plan dont l'identifiant ne correspondait plus à la section.
    if len(sections) == 1:
        return with_descendants(0)
    return full_text, None


def replace_target(full_text: str, original_target: str, improved_target: str) -> str:
    if not original_target or original_target == full_text:
        return improved_target
    position = full_text.find(original_target)
    if position < 0:
        return full_text
    leading = re.match(r"^\s*", original_target).group(0)
    trailing = re.search(r"\s*$", original_target).group(0)
    replacement = leading + improved_target.strip() + trailing
    after = full_text[position + len(original_target):]
    # Si un ancien texte avait déjà perdu sa frontière, empêcher toute nouvelle
    # fusion entre la dernière phrase et le début de la section suivante.
    if after and not replacement.endswith(("\n", "\r")) and re.match(
        r"(?:#{1,6}\s+|(?:\d+\.)+\s+|[A-ZÀ-Ý][^\n]{2,100}\n)",
        after,
    ):
        replacement += "\n\n"
    return full_text[:position] + replacement + after
