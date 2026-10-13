"""Benchmark vocabulary lives exclusively in test fixtures, never application ranking."""
from __future__ import annotations

from conftest import evidence_reference

import copy
import json
from types import SimpleNamespace
from unittest.mock import Mock

from agents.EnnoScholarV2.agent import ScholarChatAgent
from agents.EnnoScholarV2.config import ScholarConfig
from agents.EnnoScholarV2.models import Article, FulltextResult, TextChunk
from agents.EnnoScholarV2.tools.scientific_search import article_id
from context_reproduction import QUESTION, function, message, response


INTENT = {
    "target_terms": ["mesh", "meshes", "meshing"],
    "primary_operations": ["generation", "generate", "construction", "construct"],
    "secondary_operations": ["adaptation", "smoothing", "optimization", "sizing"],
    "adjacent_operations": ["simulation", "reconstruction", "segmentation", "rendering"],
    "method_terms": ["deep learning", "neural network", "diffusion", "reinforcement learning", "AI"],
    "context_terms": ["tetrahedral", "hexahedral", "volumetric", "structured", "finite element", "mechanics"],
}
QUERIES = ["neural volumetric mesh generation", "learned tetrahedral finite element meshing",
    "hexahedral structured mesh generation deep learning", "mesh adaptation reinforcement learning mechanics"]
CENTRAL_TITLES = [
    "3DMeshNet: A Three-Dimensional Differential Neural Network for Structured Mesh Generation",
    "DL-Polycube: Deep learning enhanced generalized polycube method for high-quality hexahedral mesh generation and volumetric spline construction",
    "DDPM-Polycube: A Denoising Diffusion Probabilistic Model for Polycube-Based Hexahedral Mesh Generation and Volumetric Spline Construction",
    "TetGAN: A Convolutional Neural Network for Tetrahedral Mesh Generation",
    "MeshingNet: A New Mesh Generation Method based on Deep Learning",
    "Graph Neural Networks for Mesh Generation and Adaptation in Structural and Fluid Mechanics",
    "A survey of AI methods for geometry preparation and mesh generation in engineering simulation",
    "Unstructured surface mesh smoothing method based on deep reinforcement learning",
]
EXCERPT = "A trained neural network generates computational meshes for finite element mechanics."
ABSTRACT = EXCERPT + " Validation of the learned method is described in the available abstract." * 90 + "BACKEND_COMPLETE_ABSTRACT_TAIL"


def fixture_articles():
    articles = [Article(title=title, abstract="" if i == 0 else ABSTRACT, doi=f"10.9999/benchmark-{i}",
        source="semantic_scholar", sources=["semantic_scholar"], access_level="metadata_only" if i == 0 else "abstract_only")
        for i, title in enumerate(CENTRAL_TITLES)]
    noise = [
        "Graph neural network simulation on existing volumetric meshes for mechanics",
        "Deep learning graphical reconstruction with neural networks and 3D rendering",
        "Neural network segmentation of medical images",
    ]
    for i in range(642):
        articles.append(Article(title=f"{noise[i % 3]} — unrelated experiment {i}", abstract=ABSTRACT,
            doi=f"10.9999/noise-{i}", citation_count=100000, source="openalex", sources=["openalex"], pdf_url="https://example.org/open.pdf"))
    for article in articles:
        article.id = article_id(article)
    return articles


def prepare_scientific_run(monkeypatch, *, soft=180, hard=600, timeouts=0, clock=None):
    client = Mock()
    client.with_options.return_value = client
    agent = ScholarChatAgent(ScholarConfig(model="scientific-fixture", soft_time_limit=soft, turn_timeout=hard), client=client)
    articles = fixture_articles()
    requests, provider_calls, retrieved = [], [], []

    def provider(query, limit, *, deadline=None, source="semantic_scholar"):
        provider_calls.append((source, query, limit, deadline))
        if query == "complementary scientific axis":
            rows = articles[600:650]
        else:
            index = ("semantic_scholar", "openalex", "arxiv").index(source) * 200 + QUERIES.index(query) * 50
            rows = articles[index:index + 50]
            # Put central results below the former top-8 collection depth.
            if index == 0:
                rows = [*articles[8:20], *articles[:8], *articles[20:50]]
        return [article.model_copy(deep=True) for article in rows[:limit]]

    for source, method in (("semantic_scholar", "semantic"), ("openalex", "openalex"), ("arxiv", "arxiv")):
        monkeypatch.setattr(agent.search, method, lambda query, limit, deadline=None, source=source: provider(query, limit, deadline=deadline, source=source))

    def retrieve(article, *args, **kwargs):
        retrieved.append((article.id, kwargs.get("deadline")))
        if clock:
            clock.value += 2
        if article.id == articles[0].id:
            return FulltextResult(access_level="metadata_only", reason="Payant, aucun texte légal trouvé.")
        if article.id == articles[1].id:
            return FulltextResult(access_level="fulltext", text=EXCERPT, fulltext_source="institutional fixture",
                chunks=[TextChunk(page=4, section="Methods", text=EXCERPT)])
        return FulltextResult(access_level="abstract_only", text=article.abstract, reason="Abstract uniquement.")

    def create(**kwargs):
        if "text" in kwargs:
            data = json.loads(kwargs["input"])
            assert kwargs["text"]["format"]["name"] == "article_analysis", "No automatic reranking allowed"
            if clock:
                clock.value += 2
            return SimpleNamespace(status="completed", output_text=json.dumps({"method": "Méthode apprise de génération",
                "results": "Unsupported result to remove", "access_level": "fulltext" if data["analysis_basis"] == "fulltext" else "abstract_only", "analysis_basis": data["analysis_basis"],
                "evidence": [evidence_reference(data, EXCERPT, ["method"])]}))
        requests.append(copy.deepcopy(kwargs))
        number = len(requests)
        if number == 1:
            return response(function("search_scientific", {"queries": QUERIES, "top_k": 8, "intent": INTENT}, "search1"))
        if number == 2:
            if clock:
                clock.value += soft + 1
            return response(function("search_scientific", {"queries": ["complementary scientific axis"], "top_k": 8, "intent": INTENT}, "search2"))
        if number == 3:
            return response(function("get_articles", {"article_ids": [article.id for article in articles[:8]]}, "details"))
        if number == 4:
            return response(function("get_fulltext", {"article_ids": [article.id for article in articles[:3]], "candidate_urls": []}, "full"))
        if number == 5:
            return response(function("analyze_articles", {"article_ids": [article.id for article in articles[:3]], "user_context": QUESTION}, "analysis"))
        if timeouts and number in range(6, 6 + timeouts):
            import httpx
            from openai import APITimeoutError
            raise APITimeoutError(request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
        return response(message("Sélection scientifique :\n" + "\n".join(f"- {article.title}" for article in articles[:8]) +
            "\nAccès vérifiés : métadonnées uniquement pour le premier, fulltext et passages pour le deuxième, abstract uniquement pour le troisième."))

    client.responses.create.side_effect = create
    monkeypatch.setattr("agents.EnnoScholarV2.agent.get_fulltext", retrieve)
    if clock:
        monkeypatch.setattr("agents.EnnoScholarV2.agent.time.monotonic", lambda: clock.value)
    return SimpleNamespace(agent=agent, client=client, articles=articles, requests=requests,
        provider_calls=provider_calls, retrieved=retrieved)
