"""Classification de provenance des passages avant l'extraction sémantique."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List

from .schemas import Chunk


ROLES = {"project_evidence", "literature", "admin", "context", "unknown"}
EXTERNAL_ATTRIBUTION_RE = re.compile(
    r"\b(?:les auteurs|the authors|cet article|ce papier|"
    r"this paper|dans cette publication|l'étude publiée)\b",
    flags=re.IGNORECASE,
)


def has_explicit_external_attribution(text: str) -> bool:
    # « Nous avons étudié cet article » décrit une action du projet.
    if re.search(r"\b(?:nous avons|nous étudions|notre projet|le projet a)\b", text or "", re.I):
        return False
    return bool(EXTERNAL_ATTRIBUTION_RE.search(text or ""))

PROVENANCE_SYSTEM = """
Tu classes la provenance de passages d'un dossier R&D brut, avant toute extraction.
Réponds uniquement en JSON : {"passages": [{"id": "P1", "role": "..."}]}.
Un rôle par identifiant fourni, sans en omettre et sans inventer de passage.

project_evidence : faits concernant les travaux, objectifs, essais, difficultés,
méthodes ou résultats du projet analysé, même lorsque ces travaux sont prévus.
literature : articles, auteurs, solutions externes et leurs limites ou résultats.
admin : budget, recrutement, gouvernance, suivi CIR et formalités.
context : définitions, contexte général, besoins du domaine sans attribution claire
au projet ou à une publication. Utilise unknown si l'attribution reste incertaine.

Le titre de section est un indice, pas une preuve. « Réaliser un état de l'art »
est une activité du projet ; le contenu d'un article décrit ensuite reste literature.
Une limite d'un article ne prouve jamais que le projet l'a rencontrée.
Pour chaque phrase, recherche son sujet dans les phrases voisines : « le modèle »,
« cette approche », « ils » et « leurs résultats » peuvent continuer la description
d'une publication. Le fait que le document appartienne au dossier du projet ne
transforme pas une expérience publiée en expérience du projet. Un tableau issu
d'une publication reste literature ; un tableau d'essais attribués au projet est
project_evidence. Dans un même paragraphe, sépare les faits si leur auteur change.
En l'absence d'attribution fiable au projet, préfère unknown à project_evidence.
""".strip()


@dataclass(frozen=True)
class Passage:
    passage_id: str
    text: str
    start: int
    end: int
    role: str = "unknown"


def split_passages(text: str) -> List[Passage]:
    """Découpe les lignes longues en phrases sans perdre leurs positions source."""
    passages = []
    for line in re.finditer(r"[^\n]+", text):
        raw = line.group()
        # Le découpage fin ne concerne que les lignes contenant plusieurs phrases.
        boundaries = [0]
        boundaries.extend(
            match.end() for match in re.finditer(
                r"(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Ý«\"“])", raw
            )
        )
        boundaries.append(len(raw))
        for begin, end in zip(boundaries, boundaries[1:]):
            part = raw[begin:end]
            stripped = part.strip()
            if not stripped:
                continue
            leading = len(part) - len(part.lstrip())
            start = line.start() + begin + leading
            passages.append(Passage(
                passage_id=f"P{len(passages) + 1}",
                text=stripped,
                start=start,
                end=start + len(stripped),
            ))
    return passages


def classify_passages(llm, chunk: Chunk) -> List[Passage]:
    passages = split_passages(chunk.text)
    if not passages:
        return []
    prompt = (
        f"DOCUMENT: {chunk.document_name}\n"
        f"SECTION INDICATIVE: {chunk.section_title or 'non identifiée'}\n"
        f"PLAN DU DOCUMENT: {chunk.metadata.get('document_outline', '')}\n"
        f"CONTEXTE AVANT: {chunk.context_before[:400]}\n"
        f"CONTEXTE APRÈS: {chunk.context_after[:400]}\n\n"
        "PASSAGES À CLASSER (chaque passage peut avoir un rôle différent) :\n"
        + "\n".join(f"[{passage.passage_id}] {passage.text}" for passage in passages)
    )
    # Import local : semantic_extractor utilise ce module et fournit le parseur.
    from .semantic_extractor import parse_json_payload

    labels = {}
    expected_ids = {passage.passage_id for passage in passages}
    for attempt in range(2):
        try:
            correction = (
                "\n\nTa réponse précédente était incomplète ou invalide. "
                "Retourne exactement un rôle pour chaque identifiant P donné."
                if attempt else ""
            )
            data = parse_json_payload(
                llm.extract_json(PROVENANCE_SYSTEM, prompt + correction)
            )
            if not isinstance(data, dict):
                continue
            current = {}
            for value in data.get("passages") or []:
                if not isinstance(value, dict):
                    continue
                role = str(value.get("role") or "unknown").strip().lower()
                passage_id = str(value.get("id") or "")
                if passage_id in expected_ids and role in ROLES:
                    current[passage_id] = role
            labels = current
            if set(labels) == expected_ids:
                break
        except (TypeError, ValueError, KeyError):
            continue

    return [
        Passage(p.passage_id, p.text, p.start, p.end, labels.get(p.passage_id, "unknown"))
        for p in passages
    ]


def role_for_quote(quote: str, source: str, passages: Iterable[Passage]) -> str:
    """Attribue le rôle du passage cité ; un mélange ou une ambiguïté reste unknown."""
    passages = list(passages)
    if not quote or not passages:
        return "unknown"
    matches = [match.start() for match in re.finditer(re.escape(quote), source)]
    if matches:
        roles = set()
        for start in matches:
            end = start + len(quote)
            covered = [p for p in passages if p.start < end and p.end > start]
            role = covered[0].role if covered and len({p.role for p in covered}) == 1 else "unknown"
            # Un énoncé explicitement attribué à des auteurs/publications
            # externes ne peut devenir une preuve du projet par erreur de vote.
            if role == "project_evidence" and has_explicit_external_attribution(quote):
                role = "literature"
            roles.add(role)
        return next(iter(roles)) if len(roles) == 1 else "unknown"

    # La vérification de citation historique normalise les espaces. On ne déduit
    # un rôle que si la citation normalisée figure dans un seul type de passage.
    normalize = lambda value: re.sub(r"\s+", " ", value).strip().casefold()
    needle = normalize(quote)
    roles = {p.role for p in passages if needle and needle in normalize(p.text)}
    return next(iter(roles)) if len(roles) == 1 else "unknown"
