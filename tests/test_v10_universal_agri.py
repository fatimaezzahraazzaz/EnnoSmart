# -*- coding: utf-8 -*-
from modules.NLP.raw_project_pipeline import process_raw_project_results

docs = [
    {
        "document_metadata": {"file_name": "Compte_rendu_resilience.docx", "document_id": "resilience"},
        "text_chunks": [
            """
            L'objectif est d'améliorer la résilience des vergers de pommiers face aux aléas climatiques
            en étudiant différents leviers agronomiques. Les essais comparent des apports de compost,
            des systèmes de conduite, des porte-greffes et des modalités d'irrigation.
            Le suivi porte sur la vigueur, le calibre, le rendement, le taux de matière organique
            et les analyses foliaires.
            Les premiers résultats montrent que l'effet du compost n'est pas observable à court terme.
            La minéralisation de la matière organique dépend fortement des conditions climatiques
            et pédologiques, ce qui rend difficile la prévision de la réponse agronomique.
            """
        ],
    }
]

out = process_raw_project_results(docs, use_gliner=False)

print("Pipeline:", out.get("pipeline_version"))
print("Objectif:", out["objectif_global"]["resume"][:120])
print("Verrous:", out["summary_counts"]["verrous_candidats"])
print("Passages utiles:", out["summary_counts"]["passages_utiles_consultant"])
print("Roles:", out["evidence_graph_v2"]["counts"])

assert out["pipeline_version"] == "v10_universal_raw_project"
assert out["summary_counts"]["verrous_candidats"] >= 1
assert out["summary_counts"]["passages_utiles_consultant"] >= 3
assert len(out["roles_cir"].get("objectif", [])) >= 1
assert len(out["roles_cir"].get("incertitude", [])) >= 1
assert len(out["roles_cir"].get("resultat", [])) >= 1

print("TEST PASSED - V10 universal agri OK")
