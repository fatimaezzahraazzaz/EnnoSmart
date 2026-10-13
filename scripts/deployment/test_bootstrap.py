from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import Mock
import zlib

import yaml

from scripts.deployment import prefetch_models

ROOT = Path(__file__).resolve().parents[2]


def test_current_models_and_optional_features():
    models = dict(prefetch_models.planned_models({}))
    assert models["nlp_nli"] == "MoritzLaurer/multilingual-MiniLMv2-L6-mnli-xnli"
    assert models["embeddings"] == "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    assert models["reranker"] == "BAAI/bge-reranker-v2-m3"
    assert models["translation"] == "Helsinki-NLP/opus-mt-tc-big-en-fr"
    env = {name: "0" for name in (
        "ENNOSMART_PRELOAD_NLI", "ENNOSCHOLAR_ENABLE_BGE_RERANKER",
        "ENNOSMART_RUN_AI_DETECTOR", "ENNOSMART_PRELOAD_TRANSLATION",
    )}
    assert set(dict(prefetch_models.planned_models(env))) == {"embeddings"}


def test_custom_models_are_respected():
    env = {"ENNOSMART_EMBEDDING_MODEL": "custom/embedding",
           "ENNOSCHOLAR_EDITORIAL_NLI_MODEL": "custom/nli",
           "ENNOSCHOLAR_OPUS_MODEL": "custom/translation"}
    models = dict(prefetch_models.planned_models(env))
    assert models["embeddings"] == "custom/embedding"
    assert models["editorial_nli"] == "custom/nli"
    assert models["translation"] == "custom/translation"
    assert "reranker" not in dict(prefetch_models.planned_models(env, core_only=True))


def test_nli_resolves_persistent_cache_without_downloading(monkeypatch, tmp_path):
    from modules.NLP import semantic_lock_adjudicator as nli
    monkeypatch.setenv("ENNOSMART_NLI_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("ENNOSMART_NLI_ALLOW_DOWNLOAD", "0")
    resolve = Mock(return_value=None)
    monkeypatch.setattr(nli, "_resolve_local_snapshot", resolve)
    import pytest
    with pytest.raises(FileNotFoundError):
        nli.SemanticLockAdjudicator()
    resolve.assert_called_once_with(nli.DEFAULT_MODEL_NAME, str(tmp_path))


def test_preload_offline_uses_same_cache_and_loads_core_models(monkeypatch, tmp_path):
    import sys
    cache = tmp_path / "hub"
    monkeypatch.setenv("ENNOSMART_NLI_CACHE_DIR", str(cache))
    monkeypatch.setenv("ENNOSMART_PRELOAD_NLI", "1")
    monkeypatch.delenv("ENNOSMART_EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("ENNOSCHOLAR_EDITORIAL_NLI_MODEL", raising=False)
    download = Mock(side_effect=lambda **kw: str(cache / kw["repo_id"].replace("/", "--")))
    embedding_model = Mock()
    nli_model = Mock()
    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(
        constants=SimpleNamespace(HF_HUB_CACHE=str(cache)), snapshot_download=download,
    ))
    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=embedding_model))
    monkeypatch.setitem(sys.modules, "modules.NLP.semantic_lock_adjudicator", SimpleNamespace(SemanticLockAdjudicator=nli_model))
    monkeypatch.setattr(sys, "argv", ["prefetch_models.py", "--core-only", "--offline"])
    assert prefetch_models.main() == 0
    assert download.call_count == 2
    assert all(call.kwargs["local_files_only"] for call in download.call_args_list)
    assert all(call.kwargs["cache_dir"] == str(cache) for call in download.call_args_list)
    embedding_model.return_value.encode.assert_called_once()
    nli_model.assert_called_once()
    assert (tmp_path / "ennosmart-preloaded-models.json").is_file()


def test_compose_workers_wait_for_models_and_share_current_image():
    compose = yaml.safe_load((ROOT / "docker-compose.ovh.yml").read_text("utf-8"))
    services = compose["services"]
    for name in ("api", "scholar-worker", "cir-worker"):
        assert services[name]["depends_on"]["models-init"]["condition"] == "service_completed_successfully"
        assert services[name]["image"] == services["models-init"]["image"]
        assert services[name]["environment"]["ENNOSMART_NLI_CACHE_DIR"] == services[name]["environment"]["HF_HUB_CACHE"]
    assert services["models-init"]["restart"] == "no"
    assert not services["models-init"].get("depends_on")


def test_lock_matches_fastjudge_version_and_cpu_runtime():
    blob = zlib.decompress((ROOT / "models/fastjudge/fastjudge_linearsvc_C025.joblib").read_bytes())
    version = re.search(rb"_sklearn_version.{0,8}?([0-9]+\.[0-9]+\.[0-9]+)", blob).group(1).decode()
    requirements = (ROOT / "requirements.txt").read_text("utf-8")
    lock = (ROOT / "deploy/ovh/requirements.lock").read_text("utf-8")
    assert f"scikit-learn=={version}" in requirements
    assert f"scikit-learn=={version}" in lock
    assert re.search(r"^torch==[\w.+-]+\+cpu(?:\s|$)", lock, re.MULTILINE)
    assert not re.search(r"^(nvidia-|triton==)", lock, re.MULTILINE)
    assert re.search(r"^openai==", lock, re.MULTILINE)
