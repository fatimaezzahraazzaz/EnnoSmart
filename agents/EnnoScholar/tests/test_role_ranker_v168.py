from __future__ import annotations

from EnnoScholar.paper_ranker import rank_papers_for_intent


def _row(term: str):
    return {"term_en": term}


def _tgm_intent():
    return {
        "scientific_query_plan": {
            "scientific_object": [_row("compressor")],
            "independent_variables": [_row("cooling water flow rate"), _row("refrigerant type")],
            "response_variables": [_row("compressor outlet temperature")],
            "operating_conditions": [_row("severe operating conditions")],
            "phenomena": [_row("outlet temperature variation with cooling water flow")],
            "methods": [_row("experimental testing")],
            "validation_concepts": [_row("performance validation")],
            "local_identifiers": [{"value": "TGM100"}],
        },
        "primary_core_concepts": ["compressor"],
    }


def _v1_intent():
    return {
        "scientific_query_plan": {
            "scientific_object": [_row("SAR automatic target recognition")],
            "independent_variables": [_row("synthetic training data")],
            "response_variables": [_row("ATR performance on measured SAR data")],
            "operating_conditions": [],
            "phenomena": [_row("synthetic to measured generalization gap")],
            "methods": [_row("domain adaptation")],
            "validation_concepts": [_row("evaluation on measured SAR data")],
            "local_identifiers": [],
        },
        "primary_core_concepts": ["synthetic aperture radar", "automatic target recognition"],
        "core_concepts": [
            "synthetic aperture radar", "automatic target recognition",
            "synthetic training data", "measured SAR data", "sim-to-real generalization",
        ],
        "concept_aliases": {
            "synthetic aperture radar": ["SAR", "synthetic aperture radar"],
            "automatic target recognition": ["ATR", "automatic target recognition"],
            "synthetic training data": ["synthetic training data", "synthetic data"],
            "measured SAR data": ["measured SAR data", "real SAR data"],
            "sim-to-real generalization": ["synthetic-to-measured", "sim-to-real", "domain gap"],
        },
    }


def _v2_intent():
    return {
        "scientific_query_plan": {
            "scientific_object": [_row("SAR simulation")],
            "independent_variables": [_row("view angle")],
            "response_variables": [_row("agreement with measured SAR data")],
            "operating_conditions": [_row("specular directions")],
            "phenomena": [_row("simulation to measurement calibration instability")],
            "methods": [_row("electromagnetic scattering simulation")],
            "validation_concepts": [_row("comparison against ground truth measurements")],
            "local_identifiers": [],
        },
        "primary_core_concepts": ["SAR simulation"],
    }


def _v3_intent():
    return {
        "scientific_query_plan": {
            "scientific_object": [_row("bistatic radar simulation")],
            "independent_variables": [_row("ray launch density")],
            "response_variables": [_row("simulation accuracy"), _row("computational performance")],
            "operating_conditions": [],
            "phenomena": [_row("accuracy computation trade-off")],
            "methods": [_row("ray tracing")],
            "validation_concepts": [_row("convergence assessment")],
            "local_identifiers": [],
        },
        "primary_core_concepts": ["bistatic radar simulation"],
    }


def test_tgm_exact_relation_is_direct_but_heat_pump_only_is_connexe():
    papers = [
        {
            "title": "Effect of cooling water flow rate on compressor discharge temperature",
            "abstract": "Experimental testing varies cooling water flow rate and measures compressor outlet temperature under severe operating conditions.",
            "year": 2024,
        },
        {
            "title": "Operating characteristics of transcritical CO2 heat pump for water heating",
            "abstract": "Water mass flow rate and inlet temperatures affect heat pump COP and water outlet temperature.",
            "year": 2011,
        },
    ]
    ranked = rank_papers_for_intent(papers, _tgm_intent(), top_n=10)
    by_title = {p["title"]: p for p in ranked}
    assert by_title[papers[0]["title"]]["tag"] == "Direct"
    assert by_title[papers[1]["title"]]["tag"] != "Direct"


def test_v1_real_synthetic_gap_has_true_directs():
    papers = [
        {
            "title": "Towards Assessing the Synthetic-to-Measured Vulnerability of SAR ATR",
            "abstract": "SAR automatic target recognition models trained with synthetic training data are evaluated on measured SAR data, exposing a synthetic-to-measured domain gap.",
            "year": 2024,
        },
        {
            "title": "Target Recognition in SAR Images by Deep Learning with Training Data Augmentation",
            "abstract": "Automatic target recognition uses data augmentation for classification accuracy on a benchmark.",
            "year": 2023,
        },
        {
            "title": "Research advances of SAR remote sensing for agriculture applications: A review",
            "abstract": "A review of agricultural remote sensing applications using synthetic aperture radar.",
            "year": 2019,
        },
    ]
    ranked = rank_papers_for_intent(papers, _v1_intent(), top_n=10)
    by_title = {p["title"]: p for p in ranked}
    assert by_title[papers[0]["title"]]["tag"] == "Direct"
    assert by_title[papers[1]["title"]]["tag"] in {"Connexe", "Fondamental"}
    assert by_title[papers[2]["title"]]["tag"] != "Direct"


def test_technical_simulator_papers_are_technique_not_direct():
    papers = [
        {
            "title": "SARCASTIC v2.0—High-Performance SAR Simulation for Next-Generation ATR Systems",
            "abstract": "A high-performance SAR simulator implementation for automatic target recognition dataset generation.",
            "year": 2022,
        },
        {
            "title": "CASpatch: A SAR image simulation code to support ATR applications",
            "abstract": "Simulation code and software implementation for generating SAR imagery used by ATR systems.",
            "year": 2009,
        },
        {
            "title": "Hardware-Accelerated SAR Simulation with NVIDIA-RTX Technology",
            "abstract": "GPU-accelerated implementation of a SAR simulator.",
            "year": 2020,
        },
    ]
    ranked = rank_papers_for_intent(papers, _v1_intent(), top_n=10)
    assert {p["tag"] for p in ranked} == {"Technique"}


def test_v2_unrelated_simulation_domains_are_out_of_scope():
    papers = [
        {
            "title": "Cross Sensor Simulation of Tomographic SAR Stacks",
            "abstract": "Synthetic aperture radar simulation is compared across sensors and acquisition geometries.",
            "year": 2019,
        },
        {
            "title": "Systematic Review of Extended Reality for Lighting Design Simulations",
            "abstract": "A review of extended reality simulation methods for building lighting design.",
            "year": 2024,
        },
        {
            "title": "Classification Scheme of Heating Risk During MRI Scans",
            "abstract": "MRI simulations assess heating around orthopaedic implants.",
            "year": 2022,
        },
    ]
    ranked = rank_papers_for_intent(papers, _v2_intent(), top_n=10)
    by_title = {p["title"]: p for p in ranked}
    assert by_title[papers[0]["title"]]["tag"] in {"Connexe", "Fondamental"}
    assert by_title[papers[1]["title"]]["tag"] == "Hors sujet"
    assert by_title[papers[2]["title"]]["tag"] == "Hors sujet"


def test_v3_requires_ray_density_relation_for_direct():
    papers = [
        {
            "title": "Impact of ray launch density on bistatic radar simulation accuracy and computation time",
            "abstract": "Ray tracing simulations vary ray launch density and quantify convergence, simulation accuracy and computational performance.",
            "year": 2024,
        },
        {
            "title": "Ray-based synthesis of bistatic ground-penetrating radar profiles",
            "abstract": "A ray tracing approach synthesizes bistatic radar profiles for subsurface imaging.",
            "year": 1995,
        },
        {
            "title": "Analysis of polarimetric SAR and visible light data fusion",
            "abstract": "Polarimetric synthetic aperture radar imagery is fused with passive visible light imagery for remote sensing.",
            "year": 2013,
        },
    ]
    ranked = rank_papers_for_intent(papers, _v3_intent(), top_n=10)
    by_title = {p["title"]: p for p in ranked}
    assert by_title[papers[0]["title"]]["tag"] == "Direct"
    assert by_title[papers[1]["title"]]["tag"] in {"Connexe", "Fondamental"}
    assert by_title[papers[2]["title"]]["tag"] == "Hors sujet"


def test_fundamental_review_is_fundamental_not_direct():
    paper = {
        "title": "A Comprehensive Survey on SAR Automatic Target Recognition",
        "abstract": "This survey reviews principles, datasets and methods for synthetic aperture radar automatic target recognition.",
        "year": 2023,
    }
    ranked = rank_papers_for_intent([paper], _v1_intent(), top_n=5)
    assert ranked[0]["tag"] == "Fondamental"


def test_no_domain_hardcoding_marker_and_explainability_fields():
    paper = {
        "title": "Effect of cooling water flow rate on compressor discharge temperature",
        "abstract": "Experimental testing measures compressor outlet temperature while varying cooling water flow rate.",
    }
    row = rank_papers_for_intent([paper], _tgm_intent(), top_n=5)[0]
    details = row["score_details"]
    assert details["ranker_version"] == "v168_role_coverage_classifier"
    assert details["domain_specific_ontology_used"] is False
    assert "role_hits" in details
    assert "relation_evidence" in details


def test_output_filter_keeps_technique_and_fundamental(monkeypatch):
    from EnnoScholar.scholar_agent import _select_relevant_articles_for_output
    ranked = rank_papers_for_intent([
        {
            "title": "A Comprehensive Survey on SAR Automatic Target Recognition",
            "abstract": "This survey reviews principles and methods for synthetic aperture radar automatic target recognition.",
            "year": 2023,
        },
        {
            "title": "CASpatch: A SAR image simulation code to support ATR applications",
            "abstract": "Simulation code and software implementation for SAR ATR applications.",
            "year": 2009,
        },
    ], _v1_intent(), top_n=10)
    selected, report = _select_relevant_articles_for_output(ranked, 10)
    tags = {x["tag"] for x in selected}
    assert "Fondamental" in tags
    assert "Technique" in tags
    assert report["counts_after"]["Technique"] == 1


def test_bge_can_reorder_but_never_change_v168_category(monkeypatch):
    import EnnoScholar.paper_reranker_model as rr
    papers = rank_papers_for_intent([
        {
            "title": "A Comprehensive Survey on SAR Automatic Target Recognition",
            "abstract": "Survey of principles and methods for SAR automatic target recognition.",
        },
        {
            "title": "CASpatch: A SAR image simulation code to support ATR applications",
            "abstract": "Simulation code and software implementation for SAR ATR applications.",
        },
    ], _v1_intent(), top_n=10)
    before = {p["title"]: p["tag"] for p in papers}
    monkeypatch.setattr(rr, "is_bge_reranker_enabled", lambda: True)
    monkeypatch.setattr(rr._CrossEncoderReranker, "predict", classmethod(lambda cls, q, docs: [100.0, -100.0]))
    after, report = rr.rerank_papers_with_bge(papers, _v1_intent(), top_n=10)
    assert report["policy"] == "v168_bge_order_only_category_locked"
    assert report["requalified_count"] == 0
    assert {p["title"]: p["tag"] for p in after} == before
