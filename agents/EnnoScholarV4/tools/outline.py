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
    scientific_objective: str = Field(default="", max_length=3000)
    questions: list[str] = Field(default_factory=list, max_length=30)
    article_roles: list["ArticleRole"] = Field(default_factory=list, max_length=80)
    relation_ids: list[str] = Field(default_factory=list, max_length=80)
    experimental_results: list[str] = Field(default_factory=list, max_length=30)
    limitations: list[str] = Field(default_factory=list, max_length=30)
    evidence_ids: list[str] = Field(default_factory=list, max_length=80)
    missing_information: list[str] = Field(default_factory=list, max_length=30)
    project_connection: str = Field(default="", max_length=3000)
    figure_suggestions: list[str] = Field(default_factory=list, max_length=20)


class ArticleRole(BaseModel):
    model_config = ConfigDict(extra="forbid")
    article_id: str
    role: str = Field(min_length=1, max_length=2500)


SectionInput.model_rebuild()


class OutlineArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["read", "create", "rename", "add", "delete", "move", "merge", "associate", "update", "dissociate"]
    title: str | None = Field(max_length=500)
    section_id: str | None
    target_section_id: str | None
    position: int | None = Field(ge=1, le=100)
    sections: list[SectionInput] | None = Field(max_length=100)
    article_ids: list[str] | None = Field(max_length=80)
    offset: int = Field(default=0,ge=0)
    limit: int = Field(default=8,ge=1,le=20)


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
        from ..writing_context import section_projection
        return {"outline":{**{k:v for k,v in current.items() if k!="sections"},
            "sections":[section_projection(s,800,compact=True) for s in current["sections"][args.offset:args.offset+args.limit]]},
            "sections_total":len(current["sections"]), "next_offset":args.offset+args.limit,
            "focus": state.get("focus", {}),"guidance":"Plan complet conservé. Lire les autres sections avec offset ; write_section/read et review_document donnent les brouillons ciblés."}
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
        elif args.action == "dissociate":
            removed = set(args.article_ids or [])
            section["article_ids"] = [aid for aid in section["article_ids"] if aid not in removed]
            section["article_roles"] = [r for r in section.get("article_roles", []) if r["article_id"] not in removed]
        elif args.action == "update":
            if not args.sections or len(args.sections) != 1:
                raise ValueError("update exige exactement une section argumentée, sans modifier son identité ou ses brouillons.")
            section.update(args.sections[0].model_dump())
        state.setdefault("focus", {})["section_id"] = section["id"] if section in sections else ""
    for section in proposed["sections"]:
        if any(aid not in registry for aid in section["article_ids"]):
            raise ValueError("Association avec un article inconnu interdite.")
        if any(r["article_id"] not in section["article_ids"] for r in section.get("article_roles", [])):
            raise ValueError("Le rôle d'une publication exige son association à la section.")
        relations = {r["id"] for r in state.get("scientific_relations", [])}
        if not set(section.get("relation_ids", [])) <= relations:
            raise ValueError("Relation scientifique inconnue.")
        for eid in section.get("evidence_ids", []):
            proof = state.get("evidence", {}).get(eid)
            if not proof or proof["article_id"] not in section["article_ids"]:
                raise ValueError("Preuve inconnue ou hors de la section.")
    state.setdefault("outline_versions", []).append(copy.deepcopy(current))
    proposed["version"] = current["version"] + 1
    proposed["plan_revision"] = current.get("plan_revision", current["version"]) + 1
    proposed["status"] = "proposed" if args.action == "create" else "modified"
    proposed.pop("approval", None)
    state["outline"] = proposed
    state.setdefault("document", {})["review_stale"] = True
    from ..manuscript import save_document_revision
    save_document_revision(state)
    return {"saved": True, "outline": proposed}
