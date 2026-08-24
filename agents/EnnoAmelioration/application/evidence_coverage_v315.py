from __future__ import annotations
import json
import re
from typing import Any

POLICY_VERSION = "ennoamel_scoped_optional_sources_v3_25"
_CITATION_RE = re.compile(r"(?<![A-Za-z0-9])A\d+(?![A-Za-z0-9])", re.I)
_PRIORITY_KEYS = (
    "citation_id","article_id","paper_id","title","authors","author","year","doi","url",
    "source_url","source","abstract","evidence_text","evidence","claim","claims","snippet",
    "fulltext_excerpt","full_text_excerpt","quote","quotes","support","rationale","relevance","section_ids",
    "research_target_ids","target_bindings","allowed_claim_scope","section_context_gate",
    "citation_required","required_for_target",
)

def _scholar(evidence: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(evidence, dict): return {}
    value=evidence.get("scholar")
    return value if isinstance(value, dict) else {}

def accepted_evidence_rows(evidence: dict[str, Any] | None) -> list[dict[str, Any]]:
    rows=_scholar(evidence).get("evidence") or []
    if not isinstance(rows, list): return []
    out=[]; seen=set()
    for row in rows:
        if not isinstance(row, dict): continue
        cid=str(row.get("citation_id") or "").strip().upper()
        if not re.fullmatch(r"A\d+", cid, re.I) or cid in seen: continue
        seen.add(cid); out.append(row)
    return out

def required_citation_ids(evidence: dict[str, Any] | None) -> list[str]:
    return [
        str(row.get("citation_id") or "").strip().upper()
        for row in accepted_evidence_rows(evidence)
        if row.get("citation_required") is True or row.get("required_for_target") is True
    ]

def eligible_citation_ids(evidence: dict[str, Any] | None) -> list[str]:
    return [str(r.get("citation_id") or "").strip().upper() for r in accepted_evidence_rows(evidence)]

def citation_ids_in_text(text: str | None) -> list[str]:
    ids={m.group(0).upper() for m in _CITATION_RE.finditer(str(text or ""))}
    return sorted(ids, key=lambda v:int(v[1:]) if v[1:].isdigit() else v)

def build_coverage_report(evidence: dict[str, Any] | None, candidate: str | None) -> dict[str, Any]:
    eligible=eligible_citation_ids(evidence); eligible_set=set(eligible)
    required=required_citation_ids(evidence); used=citation_ids_in_text(candidate); used_set=set(used)
    missing=[cid for cid in required if cid not in used_set]
    used_required=[cid for cid in required if cid in used_set]
    return {
        "policy_version":POLICY_VERSION,
        "eligible_ids":eligible,
        "required_ids":required,
        "used_ids":used,
        "used_eligible_ids":[cid for cid in used if cid in eligible_set],
        "unauthorized_used_ids":[cid for cid in used if cid not in eligible_set],
        "used_required_ids":used_required,
        "missing_required_ids":missing,
        "eligible_count":len(eligible),
        "required_count":len(required),
        "used_required_count":len(used_required),
        "coverage_ratio":1.0 if not required else round(len(used_required)/len(required),6),
        "complete":not missing,
    }

def render_mandatory_evidence_contract(evidence: dict[str, Any] | None) -> str:
    eligible=eligible_citation_ids(evidence)
    required=required_citation_ids(evidence)
    if not eligible:
        return "Aucune nouvelle preuve Scholar acceptée n'est attachée à cette cible. N'invente aucune citation."
    joined=", ".join(eligible)
    required_line = (
        f"Seules les preuves explicitement indispensables sont obligatoires : {', '.join(required)}."
        if required
        else "Aucune preuve n'est obligatoire : une source non pertinente pour une affirmation précise doit rester inutilisée."
    )
    return (
        "CONTRAT DE PERTINENCE — SOURCES AUTORISÉES ET CIBLÉES\n"
        f"Les identifiants autorisés pour cette cible sont : {joined}.\n"
        f"{required_line}\n"
        "- Utilise une source uniquement si elle soutient directement l'affirmation scientifique rédigée.\n"
        "- Respecte strictement target_bindings et allowed_claim_scope : une source liée à un verrou, un passage ou une section ne doit pas être transférée vers un autre.\n"
        "- Chaque citation doit suivre immédiatement l'affirmation exacte qu'elle étaye.\n"
        "- Ne cite jamais pour atteindre un quota, compléter une bibliographie ou donner une apparence scientifique.\n"
        "- Si le lien entre la source et l'affirmation est partiel, formule l'apport avec prudence ou n'utilise pas la source.\n"
        "- N'extrapole jamais au-delà de la méthode, des résultats et des limites fournis dans la fiche de preuve.\n"
        "- Ne supprime aucune information existante pour intégrer une preuve."
    )

def _trim(value: Any, max_chars: int, depth: int=0) -> Any:
    if depth>=5: return "[truncated]"
    if isinstance(value,str):
        value=value.strip(); return value if len(value)<=max_chars else value[:max(80,max_chars-1)]+"…"
    if isinstance(value,dict):
        out={}
        for key in _PRIORITY_KEYS:
            if key in value: out[key]=_trim(value[key],max_chars,depth+1)
        if not out:
            for key,child in list(value.items())[:12]: out[str(key)]=_trim(child,max_chars,depth+1)
        return out
    if isinstance(value,(list,tuple)):
        vals=list(value); per=max(100,max_chars//max(1,min(len(vals),8)))
        return [_trim(x,per,depth+1) for x in vals[:8]]
    return value

def build_mandatory_scholar_payload(evidence: dict[str, Any] | None, max_chars: int=30000) -> str:
    rows=accepted_evidence_rows(evidence); scholar=_scholar(evidence)
    if not rows:
        return json.dumps({"available":bool(scholar.get("available")),"evidence":[]},ensure_ascii=False,separators=(",",":"))
    def pack(per_row:int):
        packed=[]
        for row in rows:
            cid=str(row.get("citation_id") or "").strip().upper()
            item=_trim(row,per_row)
            if not isinstance(item,dict): item={"value":item}
            item["citation_id"]=cid; packed.append(item)
        payload={
            "available":True,
            "policy_version":POLICY_VERSION,
            "accepted_count":len(packed),
            "eligible_citation_ids":[r["citation_id"] for r in packed],
            "required_citation_ids":required_citation_ids(evidence),
            "usage_policy":"optional_exact_claim_support_with_target_binding",
            "evidence":packed,
        }
        return json.dumps(payload,ensure_ascii=False,default=str,separators=(",",":"))
    per=max(500,int((max_chars-1500)/max(1,len(rows))))
    raw=pack(per)
    if len(raw)<=max_chars: return raw
    for per in (1600,1200,900,700,500,350,220):
        raw=pack(per)
        if len(raw)<=max_chars: return raw
    return raw
