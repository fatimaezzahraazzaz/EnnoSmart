from types import SimpleNamespace

from agents.EnnoDiagnostic.consultant_verrou_synthesizer import (
    synthesize_consultant_verrous,
)
from agents.EnnoDiagnostic.ennodiagnostic_agent import dedupe_sources
from modules.NLP.semantic_lock_adjudicator import _resolve_local_snapshot
from modules.NLP.semantic_lock_finalizer import (
    SemanticLockFinalizer,
    _clean_seed_text,
)
from modules.RAG import indexer as rag_indexer


LOCK_TOP_SCORES = {
    "LOCK_CORE": 0.43,
    "SUPPORT_EVIDENCE": 0.22,
    "METHOD_CONTEXT": 0.20,
    "NOISE": 0.15,
}


def test_nli_snapshot_is_resolved_locally_without_hub_lookup(tmp_path):
    model_cache = tmp_path / "models--org--nli-model"
    snapshot = model_cache / "snapshots" / "revision123"
    snapshot.mkdir(parents=True)
    (model_cache / "refs").mkdir()
    (model_cache / "refs" / "main").write_text("revision123", encoding="utf-8")
    (snapshot / "config.json").write_text("{}", encoding="utf-8")
    (snapshot / "tokenizer_config.json").write_text("{}", encoding="utf-8")
    (snapshot / "model.safetensors").write_bytes(b"local")

    assert _resolve_local_snapshot("org/nli-model", str(tmp_path)) == snapshot


def _role_finalizer():
    finalizer = object.__new__(SemanticLockFinalizer)
    finalizer._score_hypotheses = lambda profile, hypotheses: dict(LOCK_TOP_SCORES)
    return finalizer


def test_editorial_prefix_is_removed_without_losing_scientific_content():
    cleaned = _clean_seed_text({
        "text": (
            "To cite this version: Simulation offers full control. "
            "Synthetic datasets are not as representative as real measurements."
        )
    })

    assert not cleaned.lower().startswith("to cite this version")
    assert cleaned.startswith("Simulation offers full control")
    assert "not as representative as real measurements" in cleaned


def test_role_adjudication_separates_lock_result_and_method_without_hard_filter():
    finalizer = _role_finalizer()
    group = {"project_lock_seed_count": 1}

    lock = finalizer.classify_group(
        "Synthetic datasets are not as representative as real measurements, "
        "so models cannot generalize well on real measurements.",
        group,
    )
    observation = finalizer.classify_group(
        "The score includes angles where classification might be very difficult or impossible.",
        group,
    )
    metric_finalizer = object.__new__(SemanticLockFinalizer)
    metric_finalizer._score_hypotheses = lambda profile, hypotheses: {
        "LOCK_CORE": 0.61,
        "SUPPORT_EVIDENCE": 0.21,
        "METHOD_CONTEXT": 0.08,
        "NOISE": 0.10,
    }
    method = metric_finalizer.classify_group(
        "This score gives us a metric to compare the representativeness of the synthetic data.",
        group,
    )

    assert lock["role"] == "LOCK_CORE"
    assert observation["role"] == "SUPPORT_EVIDENCE"
    assert method["role"] == "METHOD_CONTEXT"
    assert observation["raw_top_role"] == "LOCK_CORE"
    assert method["raw_top_role"] == "LOCK_CORE"


def test_balanced_similarity_bridge_requires_two_real_lock_formulations():
    finalizer = object.__new__(SemanticLockFinalizer)
    finalizer._score_hypotheses = lambda profile, hypotheses: {
        "SAME_PARENT_LOCK": 0.2944,
        "SUPPORTS_LOCK": 0.2721,
        "DISTINCT_LOCK": 0.4335,
    }

    class FakeJudge:
        @staticmethod
        def _predict_direction(premises, hypotheses, batch_size=2):
            return [
                SimpleNamespace(entailment=0.45, contradiction=0.20),
                SimpleNamespace(entailment=0.35, contradiction=0.18),
            ]

    finalizer.judge = FakeJudge()

    same_problem = finalizer.compare_groups(
        "Which modelling choices are responsible for the generalization issues?",
        "What simplifying assumptions are necessary and sufficient to avoid the generalization gap?",
        cosine=0.697,
    )
    method_only = finalizer.compare_groups(
        "Which modelling choices are responsible for the generalization issues?",
        "We use an accuracy metric to compare two simulation procedures.",
        cosine=0.80,
    )

    assert same_problem["balanced_similarity_bridge"] is True
    assert same_problem["same_parent"] is True
    assert same_problem["strong_distinct"] is False
    assert method_only["balanced_similarity_bridge"] is False
    assert method_only["same_parent"] is False


def _nlp_source(group_id: str, text: str):
    return {
        "id": f"chunk_{group_id}",
        "text": text,
        "metadata": {
            "role": "verrou",
            "pack_key": "verrous_rnd_locaux",
            "lock_group_id": group_id,
            "passage_id": f"passage_{group_id}",
            "candidate_group_label": f"Groupe {group_id}",
            "technical_scope": "project_structuring_lock",
            "display_as_main_lock": True,
            "frascati_decision": 0,
            "supporting_passages": [
                {
                    "passage_id": f"evidence_{group_id}",
                    "document": f"{group_id}.pdf",
                    "text": text,
                }
            ],
        },
    }


def test_agent_deduplication_never_collapses_distinct_nlp_group_ids():
    shared_text = "The same technical introduction appears before two distinct uncertainties."
    first = _nlp_source("nlp_g1", shared_text)
    second = _nlp_source("nlp_g2", shared_text)

    assert len(dedupe_sources([first, second])) == 2


def test_ennodiagnostic_ignores_legacy_rag_clusters_and_keeps_nlp_ids_one_to_one():
    sections = {
        "_nlp_verrou_candidates": [
            _nlp_source("nlp_g1", "Uncertainty remains about the validity of the model under real conditions."),
            _nlp_source("nlp_g2", "The accuracy and computing-time trade-off remains unresolved."),
        ]
    }
    legacy_merged_cluster = {
        "clusters": [
            {
                "cluster_id": "legacy_merge",
                "member_group_ids": ["nlp_g1", "nlp_g2"],
                "display_as_lock": True,
                "cluster_role": "verrou_scientifique",
            }
        ]
    }

    report = synthesize_consultant_verrous(
        sections=sections,
        llm=None,
        lock_clusters=legacy_merged_cluster,
    )

    assert report["cluster_source"] == "nlp_groups_from_sections"
    assert report["consolidation_mode"] == "disabled_nlp_group_passthrough"
    assert report["downstream_regrouping_applied"] is False
    assert report["final_count"] == 2
    assert {
        item["cluster_id"] for item in report["llm_reformulated_verrous"]
    } == {"nlp_g1", "nlp_g2"}
    assert all(
        item["member_group_ids"] == [item["cluster_id"]]
        for item in report["llm_reformulated_verrous"]
    )


def test_rag_indexer_projects_nlp_groups_without_creating_cluster_file(tmp_path, monkeypatch):
    class FakeProjectStore:
        def __init__(self, *args, **kwargs):
            self.project_id = "project"
            self.organisme_id = "org"
            self.year = "2026"
            self.year_id = "2026"
            self.project_dir = tmp_path
            self.rag_dir = tmp_path / "rag"
            self.chroma_dir = tmp_path / "chroma"

        def ensure(self):
            self.rag_dir.mkdir(parents=True, exist_ok=True)
            self.chroma_dir.mkdir(parents=True, exist_ok=True)
            return self

        def save_json(self, name, value):
            return None

        def write_metadata(self, value):
            self.metadata = value

    class FakeVectorStore:
        def __init__(self, path):
            self.path = path

        def add_chunks(self, **kwargs):
            return {"added": 1, "deduplicated": 0, "embedding_model": "fake"}

    chunks = [
        {
            "id": "chunk_g1",
            "text": "Uncertainty remains about model validity under real conditions.",
            "metadata": {
                "role": "verrou",
                "chunk_level": "nlp_main_item",
                "lock_group_id": "nlp_g1",
            },
        }
    ]
    monkeypatch.setattr(rag_indexer, "ProjectStore", FakeProjectStore)
    monkeypatch.setattr(rag_indexer, "RAGVectorStore", FakeVectorStore)
    monkeypatch.setattr(rag_indexer, "nlp_json_to_chunks", lambda *args, **kwargs: chunks)

    report = rag_indexer.index_nlp_result("org", "project", {})

    assert report["nlp_lock_groups_count"] == 1
    assert report["downstream_lock_regrouping_enabled"] is False
    assert report["lock_clusters_mode"] == "disabled_nlp_group_passthrough"
    assert not (tmp_path / "rag" / "lock_clusters.json").exists()
