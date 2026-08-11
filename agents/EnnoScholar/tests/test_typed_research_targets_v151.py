from __future__ import annotations

from agents.EnnoScholar import scholar_agent as scholar_module


def test_run_search_accepts_native_research_targets_without_verrous(monkeypatch, tmp_path):
    calls = []

    class FakeAgent:
        verrou_workers = 1

        @staticmethod
        def search_for_research_target(target, domain_detection, research_context):
            calls.append((target, domain_detection, research_context))
            return {
                "subject_kind": "research_target",
                "research_target_id": target["research_target_id"],
                "research_target_title": target["title"],
                "research_target_type": target["research_target_type"],
                "decision": "pertinent",
                "articles": [],
            }

        @staticmethod
        def search_for_verrou(*args, **kwargs):
            raise AssertionError("Le chemin verrou ne doit pas être utilisé")

    monkeypatch.setattr(scholar_module, "_run_cache_key", lambda payload, agent: "typed-target")
    monkeypatch.setattr(
        scholar_module,
        "_run_cache_path",
        lambda key: tmp_path / f"{key}.json",
    )

    report = scholar_module.EnnoScholarAgent.run_search(
        FakeAgent(),
        {
            "project": "Projet test",
            "year": 2024,
            "force_refresh": True,
            "domain_detection": {"main_domain_label": "Radar"},
            "research_context": {"context_kind": "lightweight_research_context"},
            "research_targets": [
                {
                    "research_target_id": "section-2",
                    "research_target_type": "method_search",
                    "title": "Méthode de simulation SAR",
                    "text": "Comparaison entre simulation et mesures.",
                }
            ],
        },
    )

    assert len(calls) == 1
    assert report["version"] == "v152_typed_targets_cir_year_window"
    assert report["research_targets_analyzed"] == 1
    assert report["verrous_analyzed"] == 0
    assert report["subjects_analyzed"] == 1
    assert report["multi_verrou_coverage"]["enabled"] is False
    assert report["results"][0]["research_target_id"] == "section-2"
