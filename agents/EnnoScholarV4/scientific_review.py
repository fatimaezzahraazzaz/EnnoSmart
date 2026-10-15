"""Integrity checks are deterministic; entailment and coverage remain scientific judgments."""
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from .evidence import exact_source_valid
from .scientific_scope import literature_coverage


def validate_number(fact, toolkit):
    evidence = toolkit.evidence.get(fact["evidence_id"])
    if not evidence or fact["evidence_id"] not in toolkit.shown_evidence or not exact_source_valid(evidence, toolkit.snapshots):
        raise ValueError("Résultat numérique sans preuve exacte consultée.")
    table = evidence.get("table") or {}
    if table.get("structure_status") != "verified_cells" or fact["column"] not in table.get("headers", []):
        raise ValueError("La colonne du résultat doit être vérifiée dans un tableau structuré.")
    index = table["headers"].index(fact["column"])
    cell = table["row"][index]
    if cell.strip() != fact["value"].strip():
        raise ValueError("Valeur différente de la cellule source ; aucune correction silencieuse.")
    if fact["unit"] != (table.get("unit") or ""):
        raise ValueError("Unité différente de la source ou non vérifiée.")
    if fact["metric"] not in [*table.get("metric_labels", []), fact["column"]]:
        raise ValueError("Métrique absente de la légende ou de la colonne source.")
    try:
        value = Decimal(re.sub(r"[%\s]", "", cell).replace(",", "."))
        if not value.is_finite():
            raise InvalidOperation
    except InvalidOperation:
        raise ValueError("Cellule non numérique univoque : lire et qualifier avant un graphique.") from None
    return {**fact, "numeric_value": float(value), "article_id": evidence["article_id"],
        "dataset": table.get("dataset"), "table_number": table.get("number"),
        "source_version": evidence.get("source_version"), "source_url": evidence["source_url"]}


def deterministic_review(state, toolkit, outline=None):
    outline = outline or state.get("outline", {})
    issues = []
    def issue(code, message, section_id=None, evidence_id=None):
        issues.append({"code": code, "message": message, "section_id": section_id, "evidence_id": evidence_id, "origin": "deterministic"})
    for section in outline.get("sections", []):
        drafts = section.get("drafts", [])
        if not drafts:
            issue("unwritten_section", "Section non rédigée.", section["id"])
            continue
        draft = drafts[-1]
        ids, eids = set(draft["article_ids"]), set(draft["evidence_ids"])
        for aid in ids:
            article = toolkit.registry.get(aid)
            if not article or not article.verified or article.metadata_errors:
                issue("unverified_identity", "Identité bibliographique à vérifier.",section["id"])
        for eid in eids:
            e = toolkit.evidence.get(eid)
            if not e or e["article_id"] not in ids or not exact_source_valid(e, toolkit.snapshots):
                issue("invalid_evidence", "Citation ou provenance différente de la source conservée.",section["id"],eid)
            elif e.get("version_status") in {None,"legacy_unverified"}:
                issue("unverified_version", "Version documentaire non vérifiée.",section["id"],eid)
        raw = draft.get("raw_text", draft["text"])
        for marker in re.findall(r"\[((?:paper_|ev_)[\w-]+)\]",raw):
            if marker not in ids|eids:
                issue("citation_mapping", "Référence hors de la bibliographie ou des preuves déclarées.",section["id"])
        claims = draft.get("claims", [])
        if not claims:
            issue("claim_mapping_missing", "Correspondance fine affirmation/preuve non enregistrée ; la revue sémantique reste nécessaire.",section["id"])
        for claim in claims:
            if claim["statement"] not in raw or not set(claim["evidence_ids"]) <= eids:
                issue("invalid_claim_mapping", "Affirmation ou preuve absente du brouillon actuel.",section["id"])
        for fact in draft.get("numeric_claims", []):
            try:
                validate_number(fact, toolkit)
            except ValueError as exc:
                issue("invalid_number", str(exc), section["id"], fact["evidence_id"])
        for calculation in draft.get("calculations",[]):
            try:
                validate_calculation(calculation,toolkit)
            except ValueError as exc:
                issue("invalid_calculation",str(exc),section["id"])
        # Flag unattributed experimental numbers rather than claiming semantic proof.
        for paragraph in re.split(r"\n\s*\n", raw):
            numbers=re.findall(r"(\d+(?:[.,]\d+)?)\s*%",paragraph)
            values={Decimal(f["value"].replace(",",".")) for f in draft.get("numeric_claims",[]) if re.fullmatch(r"\d+(?:[.,]\d+)?",f["value"])}
            values.update(Decimal(c["result"].replace(",",".")) for c in draft.get("calculations",[]) if c["unit"]=="%" and re.fullmatch(r"\d+(?:[.,]\d+)?",c["result"]))
            if any(Decimal(n.replace(",",".")) not in values for n in numbers):
                issue("numeric_mapping_missing", "Résultat en pourcentage sans cellule source explicitement liée.",section["id"])
    coverage = literature_coverage(state, toolkit.registry)
    if not coverage["coverage_sufficient"]:
        issue("coverage_incomplete", "Couverture scientifique et temporelle non établie.")
    current = state.get("scientific_review", {}).get("scientific", {})
    return {"document_revision": state.get("document", {}).get("revision",0), "issues":issues,
        "integrity_passed": not any(i["code"] in {"invalid_evidence","unverified_identity","citation_mapping","invalid_claim_mapping","invalid_number","invalid_calculation"} for i in issues),
        "coverage_sufficient":coverage["coverage_sufficient"], "scientific":current,
        "scientific_assessment_current": not state.get("document",{}).get("review_stale",True),
        "guidance":"Le code vérifie identités, version, citation et cellule source, pas l'entailment, la complétude ou l'éligibilité CIR. Examiner protocoles, solutions récentes, contradictions, transitions et proportionnalité de la conclusion."}


def validate_calculation(calculation,toolkit):
    left=validate_number(calculation["left"],toolkit); right=validate_number(calculation["right"],toolkit)
    keys=("article_id","table_number","source_version","dataset","metric","unit","column")
    if any(left[k]!=right[k] for k in keys):
        raise ValueError("Calcul entre protocoles ou métriques différents interdit.")
    a=Decimal(left["value"].replace(",",".")); b=Decimal(right["value"].replace(",","."))
    if calculation["operation"]=="difference":
        result=a-b; unit="points de pourcentage" if left["unit"]=="%" else left["unit"]
    else:
        if b==0:
            raise ValueError("Variation relative avec référence nulle.")
        result=(a-b)/b*100; unit="%"
    try:
        provided=Decimal(calculation["result"].replace(",","."))
    except InvalidOperation:
        raise ValueError("Résultat calculé non numérique.") from None
    rounded=result.quantize(Decimal(1).scaleb(-calculation["rounding_digits"]),rounding=ROUND_HALF_UP)
    if provided!=rounded or calculation["unit"]!=unit:
        raise ValueError("Résultat ou unité de calcul différents des cellules sources.")
    return {**calculation,"computed_result":str(rounded),"evidence_ids":[left["evidence_id"],right["evidence_id"]]}
