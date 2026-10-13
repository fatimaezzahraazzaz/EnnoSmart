"""Stable section identities and transactional, narrowly scoped outline edits."""
import copy
import uuid

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal


class SectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(max_length=4000)
    article_ids: list[str] = Field(max_length=80)


class OutlineArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["read", "create", "rename", "add", "delete", "move", "merge", "associate"]
    title: str | None = Field(max_length=500)
    section_id: str | None
    target_section_id: str | None
    position: int | None = Field(ge=1, le=100)
    sections: list[SectionInput] | None = Field(max_length=100)
    article_ids: list[str] | None = Field(max_length=80)


def resolve_section(state, identifier):
    sections = state.get("outline", {}).get("sections", [])
    identifier = identifier or state.get("focus", {}).get("section_id", "")
    if str(identifier).isdigit() and 1 <= int(identifier) <= len(sections):
        return sections[int(identifier)-1]
    matches = [s for s in sections if s["id"] == identifier or s["title"].casefold() == str(identifier).casefold()]
    if len(matches) != 1:
        raise ValueError("Section ambiguë ou introuvable. Lire le plan pour obtenir son identifiant stable.")
    return matches[0]


def manage_outline(state, args, registry):
    current = state.get("outline", {"title": "État de l’art", "version": 0, "sections": []})
    if args.action == "read":
        return {"outline": current, "focus": state.get("focus", {})}
    proposed = copy.deepcopy(current)
    sections = proposed["sections"]
    if args.action == "create":
        if not args.sections:
            raise ValueError("Un plan exige au moins une section.")
        if sections:
            raise ValueError("Un plan existe déjà. Modifier ses sections pour préserver leurs identifiants.")
        proposed["title"] = args.title or current["title"]
        proposed["sections"] = [{"id": "section_" + uuid.uuid4().hex, **s.model_dump(), "drafts": []} for s in args.sections]
    elif args.action == "add":
        if not args.sections:
            raise ValueError("Fournir les sections à ajouter.")
        position = min(len(sections), (args.position or len(sections)+1)-1)
        for index, s in enumerate(args.sections):
            sections.insert(position+index, {"id": "section_" + uuid.uuid4().hex, **s.model_dump(), "drafts": []})
    else:
        section = resolve_section({**state, "outline": proposed}, args.section_id)
        if args.action == "rename":
            if not args.title or not args.title.strip():
                raise ValueError("Fournir le nouveau titre.")
            section["title"] = args.title.strip()
        elif args.action == "delete":
            sections.remove(section)
        elif args.action == "move":
            if args.position is None or args.position > len(sections):
                raise ValueError("Position hors du plan.")
            sections.remove(section)
            sections.insert(args.position-1, section)
        elif args.action == "merge":
            target = resolve_section({**state, "outline": proposed}, args.target_section_id)
            if target is section:
                raise ValueError("Choisir deux sections différentes.")
            target["description"] += "\n" + section["description"]
            target["article_ids"] = list(dict.fromkeys(target["article_ids"] + section["article_ids"]))
            target["drafts"].extend(section["drafts"])
            sections.remove(section)
            section = target
        elif args.action == "associate":
            section["article_ids"] = list(dict.fromkeys(section["article_ids"] + (args.article_ids or [])))
        state.setdefault("focus", {})["section_id"] = section["id"] if section in sections else ""
    for section in proposed["sections"]:
        if any(aid not in registry for aid in section["article_ids"]):
            raise ValueError("Association avec un article inconnu interdite.")
    state.setdefault("outline_versions", []).append(copy.deepcopy(current))
    proposed["version"] = current["version"] + 1
    state["outline"] = proposed
    return {"saved": True, "outline": proposed}
