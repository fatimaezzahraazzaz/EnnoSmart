from ennodiagnostic_v2.qualification import LockQualifier
from ennodiagnostic_v2.runtime_adapters import EnnoSmartLLMAdapter
from ennodiagnostic_v2.schemas import CanonicalEntity, EvidenceRef


def make_entity(
    entity_id,
    document_id,
    document_name,
    statement,
    quote,
):
    evidence = EvidenceRef(
        evidence_id=f"ev_{entity_id}",
        document_id=document_id,
        document_name=document_name,
        chunk_id=f"chunk_{entity_id}",
        quote=quote,
        quote_verified=True,
    )

    return CanonicalEntity(
        entity_id=entity_id,
        kind="lock",
        statement=statement,
        source_item_ids=[f"item_{entity_id}"],
        evidences=[evidence],
        document_ids=[document_id],
    )


entities = [

    # ======================================================
    # CAS 1 - MAT?RIAUX
    # Incertitude r?ellement d?montr?e
    # ======================================================

    make_entity(
        "materials_lock",
        "materials_doc",
        "essais_composite.docx",
        (
            "Ma?triser la variabilit? de porosit? "
            "du composite apr?s polym?risation."
        ),
        (
            "? param?tres de polym?risation identiques, "
            "trois s?ries d'essais ont pr?sent? des taux "
            "de porosit? variant de 3 % ? 11 %. "
            "Les ajustements de temp?rature et de pression "
            "test?s n'ont pas permis d'obtenir un niveau "
            "de porosit? stable inf?rieur ? 5 %."
        ),
    ),

    # ======================================================
    # CAS 2 - M?CANIQUE
    # Simple exigence
    # ======================================================

    make_entity(
        "mechanical_requirement",
        "mechanical_doc",
        "specifications_mecaniques.docx",
        (
            "Garantir la r?sistance m?canique "
            "de l'?quipement."
        ),
        (
            "L'?quipement devra r?sister ? des chocs "
            "de 20 g et fonctionner entre -40 ?C et 85 ?C "
            "sans d?gradation de ses performances."
        ),
    ),

    # ======================================================
    # CAS 3 - INT?GRATION INDUSTRIELLE
    # Difficult? d'ing?nierie classique
    # ======================================================

    make_entity(
        "integration_difficulty",
        "integration_doc",
        "integration_capteurs.docx",
        (
            "Int?grer des ?quipements provenant "
            "de fournisseurs diff?rents."
        ),
        (
            "Les diff?rents fournisseurs utilisent "
            "des protocoles et API propri?taires. "
            "L'?quipe d?veloppe donc un adaptateur sp?cifique "
            "pour chaque famille d'?quipements afin d'unifier "
            "les ?changes de donn?es."
        ),
    ),

    # ======================================================
    # CAS 4 - PROC?D? / CHIMIE
    # Probl?me r?el mais encore mal d?crit
    # ======================================================

    make_entity(
        "process_reformulation",
        "process_doc",
        "passage_echelle.docx",
        (
            "Ma?triser la pr?cipitation observ?e "
            "lors du passage ? l'?chelle."
        ),
        (
            "Lors du passage du proc?d? du laboratoire "
            "au pilote, des ?pisodes de pr?cipitation "
            "apparaissent et d?gradent la puret? du produit. "
            "Les conditions pr?cises d'apparition du ph?nom?ne "
            "et son m?canisme ne sont pas encore d?crits "
            "dans le dossier."
        ),
    ),
]


EXPECTED = {
    "materials_lock": "VALID_LOCK",
    "mechanical_requirement": "REQUIREMENT_ONLY",
    "integration_difficulty": "ENGINEERING_DIFFICULTY",
    "process_reformulation": "NEEDS_REFORMULATION",
}


llm = EnnoSmartLLMAdapter(
    request_name=(
        "ennodiagnostic:v2:"
        "cross_domain_qualification_benchmark"
    ),
    temperature=0.0,
    max_output_tokens=2200,
)

qualifier = LockQualifier(llm)

results = qualifier.qualify(entities)


print("\n" + "=" * 70)
print("BENCHMARK QUALIFICATION MULTI-DOMAINES")
print("=" * 70)

ok = 0

for entity in results:

    actual = entity.metadata.get(
        "qualification_status"
    )

    expected = EXPECTED.get(
        entity.entity_id
    )

    match = actual == expected

    if match:
        ok += 1

    print()
    print("CAS :", entity.entity_id)
    print("ATTENDU :", expected)
    print("OBTENU  :", actual)
    print("RESULTAT :", "OK" if match else "A REVOIR")
    print(
        "RAISON :",
        entity.metadata.get(
            "qualification_reason"
        )
    )

    suggested = entity.metadata.get(
        "suggested_statement"
    )

    if suggested:
        print(
            "REFORMULATION :",
            suggested,
        )

    missing = entity.metadata.get(
        "missing_evidence"
    ) or []

    if missing:
        print(
            "PREUVES MANQUANTES :",
            missing,
        )


print()
print("=" * 70)
print(
    f"SCORE BENCHMARK : {ok}/{len(EXPECTED)}"
)
print("=" * 70)

if ok == len(EXPECTED):
    print(
        "La qualification conserve un comportement "
        "coh?rent sur plusieurs domaines."
    )
else:
    print(
        "Une ou plusieurs r?gles doivent encore "
        "?tre calibr?es avant Frascati."
    )
