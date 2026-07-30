# test_ennoscholar_ranker_v32.py
# Test local du nouveau ranker Direct/Connexe sans appeler les bases web.
# À lancer depuis C:\EnnoSmart\backend_api

import sys
from pathlib import Path

# backend_api est dans C:\EnnoSmart\backend_api.
# On ajoute C:\EnnoSmart au PYTHONPATH pour importer agents.EnnoScholar.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.EnnoScholar.paper_ranker import rank_papers_for_intent
from agents.EnnoScholar.verrou_scientific_validator import validate_verrou_scientifically


def run_case(profile, title, papers):
    intent = {
        "backend_enrichment_profile": profile,
        "scientific_problem": title,
        "technical_object": title,
        "phenomenon": "",
        "constraints": ["300 bar"],
        "methods": [],
        "key_terms_en": [],
    }

    ranked = rank_papers_for_intent(papers, intent, top_n=10)
    validation = validate_verrou_scientifically(intent, ranked)

    print("\n==", profile, "==")
    for p in ranked:
        print(p["tag"], p["relevance_score"], "|", p["title"])
        print("  details:", p["score_details"])
    print("decision:", validation["decision"], validation["scientific_support_score"])


def main():
    run_case(
        "blowby_segments_crankcase",
        "Maîtrise du soufflage carter lié à l’usure des segments et à l’étanchéité du compresseur haute pression TGM100",
        [
            {
                "source": "semantic_scholar",
                "query": "reciprocating compressor piston rings blow-by leakage crankcase pressure",
                "title": "Investigations on the nonlinear deformation behaviour and sealing performance of spring-energised seal ring for a high-pressure oil-free compressor",
                "abstract": "High-pressure oil-free compressor sealing ring leakage performance and deformation are investigated.",
                "year": 2025,
                "citation_count": 4,
                "fields_of_study": ["Mechanical engineering"],
            },
            {
                "source": "openalex",
                "query": "reciprocating compressor piston rings blow-by leakage crankcase pressure",
                "title": "Crankcase pressure control in an internal combustion engine: GT-Power simulation",
                "abstract": "Crankcase pressure and blow-by ventilation are simulated for an engine.",
                "year": 2014,
                "citation_count": 6,
                "fields_of_study": ["Mechanical engineering"],
            },
        ],
    )

    run_case(
        "thermal_cooling_intercooler",
        "Maîtrise du refroidissement du premier étage d’un compresseur haute pression TGM100 sous variation du débit d’eau",
        [
            {
                "source": "openalex",
                "query": "high pressure reciprocating compressor intercooler water cooling temperature",
                "title": "Reciprocating Compressor 1D Thermofluid Dynamic Simulation: Problems and Comparison with Experimental Data",
                "abstract": "Thermofluid dynamic simulation of reciprocating compressor stages and heat transfer.",
                "year": 2012,
                "citation_count": 12,
                "fields_of_study": ["Mechanical engineering", "Thermodynamics"],
            },
            {
                "source": "semantic_scholar",
                "query": "high pressure reciprocating compressor intercooler water cooling temperature",
                "title": "Optimal Nanoparticle Size in SiO2/Al2O3-Water Nanofluids for Enhancing Intercooler Thermal Enhancement",
                "abstract": "Intercooler heat transfer enhancement using water nanofluids and conjugate heat transfer.",
                "year": 2025,
                "citation_count": 1,
                "fields_of_study": ["Thermal engineering"],
            },
        ],
    )


if __name__ == "__main__":
    main()
