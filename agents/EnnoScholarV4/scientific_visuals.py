"""Editable SVG recipes and verified numeric charts, without image hallucination."""
import hashlib
import html
import textwrap
import uuid
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from .tools.outline import resolve_section


class Node(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    label: str = Field(min_length=1,max_length=180)
    evidence_ids: list[str] = Field(min_length=1,max_length=8)
    origin: Literal["source_statement","inference"]


class Edge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    target: str


class Point(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1,max_length=100)
    evidence_id: str
    column: str
    value: str
    unit: str
    metric: str


class VisualArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    section_id: str
    figure_id: str | None = None
    kind: Literal["diagram","chart","original"]
    title: str = Field(min_length=1,max_length=300)
    caption: str = Field(min_length=1,max_length=2500)
    scientific_argument: str = Field(min_length=1,max_length=2500)
    nodes: list[Node] = Field(default_factory=list,max_length=12)
    edges: list[Edge] = Field(default_factory=list,max_length=20)
    points: list[Point] = Field(default_factory=list,max_length=20)
    evidence_id: str | None = None
    license_evidence_id: str | None = None
    license_url: str | None = None
    crop: list[float] | None = Field(default=None,min_length=4,max_length=4)


def svg_base(width,height,body):
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}"><rect width="100%" height="100%" fill="white"/>{body}</svg>'


def figure_png(figure):
    import fitz
    if figure["kind"]=="original":
        from .pdf_store import pdf_path
        with fitz.open(pdf_path(figure["source_file"])) as source:
            return source[figure["page_number"]-1].get_pixmap(matrix=fitz.Matrix(2,2),clip=fitz.Rect(figure["recipe"]["crop"])).tobytes("png")
    with fitz.open(stream=figure["svg"].encode(),filetype="svg") as svg:
        pdf=svg.convert_to_pdf()
    with fitz.open(stream=pdf,filetype="pdf") as doc:
        return doc[0].get_pixmap(matrix=fitz.Matrix(2,2)).tobytes("png")


def create_visual(toolkit,args):
    from .scientific_review import validate_number
    from .manuscript import save_document_revision
    section=resolve_section(toolkit.state,args.section_id)
    toolkit.state.setdefault("document",{})["active_section_id"]=section["id"]
    fig={"id":args.figure_id or "figure_"+uuid.uuid4().hex,"section_id":section["id"],"kind":args.kind,
        "title":args.title,"caption":args.caption,"scientific_argument":args.scientific_argument,
        "recipe":args.model_dump(),"origin":"agent_synthesis","evidence_ids":[]}
    source_ids=section["article_ids"]
    if args.kind=="diagram":
        if not args.nodes or len({n.id for n in args.nodes})!=len(args.nodes):
            raise ValueError("Un schéma exige des nœuds distincts avec des preuves.")
        ids={n.id for n in args.nodes}
        if any(e.source not in ids or e.target not in ids for e in args.edges):
            raise ValueError("Lien vers un nœud inexistant.")
        toolkit.claim_union([{"evidence_ids":n.evidence_ids} for n in args.nodes],source_ids)
        width=800; height=120+130*len(args.nodes)
        locations={n.id:(400,110+i*130) for i,n in enumerate(args.nodes)}
        body=f'<text x="400" y="35" text-anchor="middle" font-family="sans-serif" font-size="19">{html.escape(args.title)}</text>'
        for edge in args.edges:
            x,y=locations[edge.source]; tx,ty=locations[edge.target]
            body+=f'<path d="M {x} {y+42} L {tx} {ty-42}" stroke="#64748b" stroke-width="2" fill="none"/>'
            body+=f'<path d="M {tx-5} {ty-50} L {tx} {ty-42} L {tx+5} {ty-50}" stroke="#64748b" fill="none"/>'
        for node in args.nodes:
            x,y=locations[node.id]
            body+=f'<rect x="{x-310}" y="{y-42}" width="620" height="84" rx="8" fill="#eef4fa" stroke="#334155"/>'
            for i,line in enumerate(textwrap.wrap(node.label,70)):
                body+=f'<text x="{x}" y="{y-12+i*20}" text-anchor="middle" font-size="15" font-family="sans-serif">{html.escape(line)}</text>'
            fig["evidence_ids"].extend(node.evidence_ids)
        fig["svg"]=svg_base(width,height,body)
    elif args.kind=="chart":
        if not args.points:
            raise ValueError("Graphique sans données vérifiées.")
        points=[]
        for point in args.points:
            validated=validate_number(point.model_dump(exclude={"label"}),toolkit)
            proof=toolkit.evidence[point.evidence_id]
            if point.label!=proof["table"]["row"][0]:
                raise ValueError("L'étiquette doit correspondre exactement à la ligne source.")
            points.append({**validated,"label":point.label})
        if any(p["article_id"] not in source_ids for p in points):
            raise ValueError("Source du graphique hors de la section.")
        protocols={(p["article_id"],p["table_number"],p["source_version"],p["dataset"],p["metric"],p["unit"],p["column"]) for p in points}
        if len(protocols)!=1 or not points[0]["dataset"]:
            raise ValueError("Un graphique comparatif exige le même tableau, version, dataset, métrique, unité et condition. Séparer les protocoles.")
        if any(p["numeric_value"]<0 for p in points):
            raise ValueError("Ce graphique à axe zéro exige des résultats non négatifs.")
        maximum=100 if points[0]["unit"]=="%" else max(1,max(p["numeric_value"] for p in points)*1.1)
        if any(p["numeric_value"]>maximum for p in points):
            raise ValueError("Valeur hors de l'unité pourcentage.")
        width=850; height=180+55*len(points); body=""
        for i,line in enumerate(textwrap.wrap(args.title,75)):
            body+=f'<text x="30" y="{30+22*i}" font-size="18" font-family="sans-serif">{html.escape(line)}</text>'
        body+=f'<text x="30" y="80" font-size="14" font-family="sans-serif">{html.escape(points[0]["metric"]+" "+points[0]["unit"]+" · "+points[0]["dataset"])}</text>'
        for i,p in enumerate(points):
            y=115+55*i; length=500*p["numeric_value"]/maximum
            body+=f'<text x="20" y="{y+22}" font-size="14" font-family="sans-serif">{html.escape(p["label"][:22])}</text><rect x="210" y="{y}" width="{length}" height="30" fill="#315f86"/><text x="{225+length}" y="{y+22}" font-size="14" font-family="sans-serif">{html.escape(p["value"]+" "+p["unit"])}</text>'
        body+=f'<line x1="210" x2="710" y1="{height-38}" y2="{height-38}" stroke="#334155"/><text x="210" y="{height-14}" font-size="12">0</text><text x="690" y="{height-14}" font-size="12">{maximum:g}</text>'
        fig.update(svg=svg_base(width,height,body),data=points,evidence_ids=[p["evidence_id"] for p in points])
    else:
        if not args.evidence_id or not args.license_evidence_id or not args.crop or not args.license_url:
            raise ValueError("Figure originale : preuve de légende, de permission, licence et zone PDF requises.")
        toolkit.claim_union([{"evidence_ids":[args.evidence_id,args.license_evidence_id]}],source_ids)
        proof=toolkit.evidence[args.evidence_id]; license_proof=toolkit.evidence[args.license_evidence_id]
        permitted={"https://creativecommons.org/licenses/by/4.0/","https://creativecommons.org/licenses/by-sa/4.0/","https://creativecommons.org/publicdomain/zero/1.0/"}
        if args.license_url not in permitted or args.license_url not in license_proof["exact_quote"] or license_proof["article_id"]!=proof["article_id"]:
            raise ValueError("Autorisation de réutilisation non prouvée ; l'accès ouvert seul ne suffit pas.")
        if not proof.get("source_file") or not proof.get("page_number") or args.caption not in proof["exact_quote"]:
            raise ValueError("Figure originale : PDF, page et légende exacte vérifiés requis.")
        import fitz
        from .pdf_store import pdf_path
        with fitz.open(pdf_path(proof["source_file"])) as pdf:
            page=pdf[proof["page_number"]-1]; rect=fitz.Rect(args.crop)
            if rect.is_empty or not page.rect.contains(rect):
                raise ValueError("Zone de figure hors de la page PDF.")
        fig.update(origin="publication_reuse",source_file=proof["source_file"],page_number=proof["page_number"],source_url=proof["source_url"],
            source_version=proof["source_version"],license_url=args.license_url,evidence_ids=[args.evidence_id,args.license_evidence_id])
    fig["evidence_ids"]=list(dict.fromkeys(fig["evidence_ids"]))
    fig["sha256"]=hashlib.sha256(figure_png(fig)).hexdigest()
    figures=toolkit.state.setdefault("scientific_figures",[])
    old=next((f for f in figures if f["id"]==fig["id"]),None)
    if args.figure_id and old is None:
        raise ValueError("Figure à modifier introuvable.")
    if old:
        toolkit.state.setdefault("figure_versions",[]).append(old)
        figures.remove(old)
    figures.append(fig)
    toolkit.state["outline"]["version"]+=1
    save_document_revision(toolkit.state)
    return {"saved":True,"figure":{k:v for k,v in fig.items() if k not in {"svg","recipe"}}}
