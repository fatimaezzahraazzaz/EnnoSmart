from __future__ import annotations

from types import SimpleNamespace

from EnnoAmelioration_V2_1_research_context_fix.application import agent_adapters
from EnnoAmelioration_V2_1_research_context_fix.application import research_orchestration_service as svc
from EnnoAmelioration_V2_1_research_context_fix.domain.models import ImprovementRequest, TargetScope


def _request() -> ImprovementRequest:
    return ImprovementRequest(
        instruction=(
            "Lance une nouvelle recherche EnnoScholar sur le synthetic-to-real domain gap, "
            "les biais d'apprentissage et la généralisation vers des mesures réelles."
        ),
        full_text="",
        target_text=(
            "Les fonds MSTAR mesurés peuvent contenir des patterns récurrents. "
            "Les fonds MOCEM simulés reposent sur du bruit et les modèles CAO peuvent "
            "manquer de diversité. Ces écarts peuvent limiter la généralisation ATR."
        ),
        target_scope=TargetScope.SECTION,
        target_section_id="1.3.2.3",
        target_section_title=(
            "Discordance entre les données radar mesurées et simulées "
            "et manque de représentativité"
        ),
        project_name="Projet CIR 2024",
        project_domain="",
    )


def _locks():
    return [
        {
            "evidence_id": "D:verrou:700",
            "type": "diagnostic_lock",
            "title": (
                "Incertitude sur la représentativité des données SAR synthétiques "
                "pour la généralisation des modèles ATR"
            ),
            "text": (
                "MSTAR MOCEM SAR ATR données synthétiques et mesurées, "
                "représentativité, biais des fonds, généralisation."
            ),
        },
        {
            "evidence_id": "D:verrou:701",
            "type": "diagnostic_lock",
            "title": (
                "Incertitude sur la validité des simplifications dans la simulation SAR "
                "affectant la performance des modèles ATR"
            ),
            "text": (
                "MOCEM simplifications de simulation SAR, modèles CAO, fonds simulés, "
                "ATR, performance et transfert vers données mesurées."
            ),
        },
        {
            "evidence_id": "D:verrou:702",
            "type": "diagnostic_lock",
            "title": (
                "Incapacité à garantir la robustesse du compromis précision-vitesse "
                "dans la génération multi-configuration radar sous variations "
                "environnementales et physiques non maîtrisées"
            ),
            "text": (
                "temps de calcul, compromis précision-vitesse, génération radar, "
                "configurations physiques et coût de calcul."
            ),
        },
    ]


def test_recursive_domain_detection_prefers_nlp_payload():
    payload = {
        "report": {
            "nlp_result": {
                "domain_detection": {
                    "display_label": "Imagerie radar et apprentissage",
                    "main_domain_label": "Traitement du signal radar",
                    "sub_domain_label": "SAR automatic target recognition",
                    "domain_key": "radar_sar_atr",
                }
            }
        }
    }
    found = agent_adapters._find_domain_detection(payload)
    assert found["domain_key"] == "radar_sar_atr"
    assert found["sub_domain_label"] == "SAR automatic target recognition"


def test_domain_detection_uses_diagnostic_before_project_fallback():
    diagnostic = {
        "domain_detection": {
            "main_domain_label": "Radar",
            "sub_domain_label": "SAR ATR",
            "domain_key": "sar_atr",
        }
    }
    result = svc._domain_detection(
        SimpleNamespace(domain="generic engineering"),
        _request(),
        diagnostic,
    )
    assert result["domain_key"] == "sar_atr"
    assert result["source"] == "EnnoDiagnostic_NLP"


def test_lock_matching_rejects_neighbor_precision_speed_lock(monkeypatch):
    monkeypatch.setattr(
        agent_adapters,
        "diagnostic_context",
        lambda db, project, target_text: {
            "available": True,
            "domain_detection": {
                "main_domain_label": "Radar",
                "sub_domain_label": "SAR ATR",
            },
            "evidence_items": _locks(),
        },
    )
    # Le service importe la fonction localement depuis le même module.
    context, ids, items = svc._matched_diagnostic_context(
        object(),
        SimpleNamespace(id=1),
        _request(),
    )
    assert ids[0] == "700"
    assert "701" in ids
    assert "702" not in ids
    assert items[0]["title"].startswith("Incertitude sur la représentativité")
    assert context["domain_detection"]["sub_domain_label"] == "SAR ATR"


def test_research_verrou_uses_diagnostic_title_not_section_title():
    req = _request()
    context = {
        "diagnostic_context_text": "local",
        "matched_verrou_ids": ["700"],
    }
    verrous = svc._research_verrous(req, context, [_locks()[0]])
    assert len(verrous) == 1
    assert verrous[0]["verrou_id"] == "700"
    assert verrous[0]["title"].startswith("Incertitude sur la représentativité")
    assert verrous[0]["title"] != req.target_section_title
    assert "MSTAR" in verrous[0]["raw_item"]["source_text"]
    assert verrous[0]["source_json"]["source_section_title"] == req.target_section_title


def test_multiple_scholar_results_keep_their_verrou_coverage():
    req = _request()
    report = {
        "results": [
            {
                "verrou_id": "700",
                "scientific_intent": {
                    "verrou_title": "Représentativité SAR synthétique",
                    "strong_anchors": ["SAR", "ATR", "MSTAR"],
                },
                "queries": [{"query": "SAR ATR synthetic measured generalization"}],
                "search_status": {"precision_tag_counts": {"Direct": 1}},
                "reranking": {"used": True},
                "articles": [{
                    "title": "Synthetic-to-measured SAR ATR",
                    "year": 2024,
                    "doi": "10.1/shared",
                    "tag": "Direct",
                    "relevance_score": 0.95,
                }],
            },
            {
                "verrou_id": "701",
                "scientific_intent": {
                    "verrou_title": "Simplifications de simulation SAR",
                    "strong_anchors": ["SAR", "ATR", "simulation"],
                },
                "queries": [{"query": "SAR ATR simulation model fidelity"}],
                "search_status": {"precision_tag_counts": {"Direct": 1}},
                "reranking": {"used": True},
                "articles": [{
                    "title": "Synthetic-to-measured SAR ATR",
                    "year": 2024,
                    "doi": "10.1/shared",
                    "tag": "Direct",
                    "relevance_score": 0.93,
                }],
            },
        ]
    }
    candidates, meta = svc._extract_precise_candidates(
        report,
        request=req,
        target_verrous=["700", "701"],
        excluded_keys=set(),
        project_year=2024,
    )
    assert len(candidates) == 1
    assert candidates[0]["target_verrous"] == ["700", "701"]
    assert len(meta["scientific_intents"]) == 2
    assert len(meta["queries"]) == 2
