"""Offline reproduction using the real registry snapshot; all LLM grades mocked."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from agents.EnnoScholarV2.agent import StoredArticle
from agents.EnnoScholarV2.config import ScholarConfig
from agents.EnnoScholarV2.context import estimate_tokens, serialized_bytes
from agents.EnnoScholarV2.models import Article
from agents.EnnoScholarV2.tools.scientific_selection import diverse_shortlist, rerank_candidates

# Benchmark identifiers are fixtures only, never production ranking rules.
BENCHMARK = {
    "3DMeshNet": "art_2118d065d84bbc333259",
    "DL-Polycube": "art_942bde322abe27ebd8ff",
    "DDPM-Polycube": "art_7343e4f0cf5a62497734",
    "TetGAN": "art_f298a95c1297f15e123a",
    "MeshingNet": "art_374bda241cb1647f23f9",
    "Survey": "art_0f129976a97a96c91e05",
}
QUESTION = "Je travaille en mécanique sur la génération automatique de maillages 3D à l’aide de modèles entraînés. Je cherche les articles scientifiques les plus pertinents sur ce sujet, y compris les articles payants. Pour les meilleurs articles, indique-moi si tu as uniquement l’abstract ou si tu as réellement pu accéder au texte complet."


def main():
    snapshot = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / ".codex_tmp/scholar-v2-real-ranking-snapshot.json"
    articles = [Article.model_validate(row) for row in json.loads(snapshot.read_text(encoding="utf-8"))["articles"]]
    before = sorted(articles, key=lambda a: (-(a.relevance_score or 0), a.title.casefold(), a.id))
    before_rank = {a.id: i for i, a in enumerate(before, 1)}
    shortlist = diverse_shortlist(articles, None)
    in_shortlist = {identifier for view in shortlist for identifier in view.related_article_ids}
    assert all(identifier in in_shortlist for identifier in BENCHMARK.values())
    registry = {a.id: StoredArticle(a.model_copy(deep=True)) for a in articles}
    client = Mock(); client.with_options.return_value = client
    requests = []
    def create(**kwargs):
        payload = json.loads(kwargs["input"])
        requests.append({"candidates": len(payload["candidates"]), "tokens_estimated": estimate_tokens(kwargs["input"]) + estimate_tokens(kwargs["instructions"]) + estimate_tokens(kwargs["text"]),
            "bytes": serialized_bytes(kwargs["input"]), "tools": kwargs["tools"]})
        return SimpleNamespace(output_text=json.dumps({"articles": [{"article_id": row["article_id"],
            "relevance_label": "FONDAMENTAL" if row["article_id"] == BENCHMARK["Survey"] else ("DIRECT" if row["article_id"] in BENCHMARK.values() else "CONNEXE"),
            "relevance_score": 95 if row["article_id"] in BENCHMARK.values() else 1,
            "reason": "Jugement simulé de benchmark ; ceci n'est pas une validation OpenAI réelle."
        } for row in payload["candidates"]]}))
    client.responses.create.side_effect = create
    selected, stats = rerank_candidates(registry, client, ScholarConfig(model="mock"), QUESTION)
    assert len(registry) == 1413 and len(selected) <= 12
    ranks = {a.id: i for i, a in enumerate(selected, 1)}
    assert all(identifier in ranks for identifier in BENCHMARK.values())
    report = {"mode": "Real 1413-article metadata snapshot; scientific judgments MOCKED; no network, search or running-session mutation.",
        "backend_articles": len(registry), "shortlist": stats, "active": len(selected), "requests": requests,
        "benchmark": {name: {"article_id": identifier, "before_rank": before_rank[identifier],
            "before_score": articles[next(i for i, a in enumerate(articles) if a.id == identifier)].relevance_score,
            "shortlist_included": identifier in in_shortlist, "mocked_after_rank": ranks[identifier],
            "mocked_label": registry[identifier].article.relevance_label,
            "related_article_ids": registry[identifier].article.related_article_ids,
            "work_urls": registry[identifier].article.work_urls} for name, identifier in BENCHMARK.items()}}
    destination = ROOT / ".codex_tmp/scholar-v2-llm-ranking-reproduction.json"
    if destination.exists():
        raise FileExistsError("Preserve the earlier report before regenerating it.")
    destination.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
