"""Real GPT behavioral evaluation with manually labeled, explicitly synthetic sources."""
import json
import sys
import time
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "backend_api")]


def evaluate(env_file, output):
    from dotenv import load_dotenv
    load_dotenv(env_file, override=False)
    from agents.EnnoScholarV4.agent import ScholarChatAgent
    from agents.EnnoScholarV4.models import Article
    from agents.EnnoScholarV4.config import ScholarV4Config
    suite = json.loads(Path(__file__).with_name("mesh_relevance.json").read_text(encoding="utf-8"))
    labels = {record["key"]: record["expected_primary"] for record in suite["records"]}
    # Only title/abstract and explicit synthetic provenance cross the model boundary.
    rows = [Article(title=record["title"], abstract=record["abstract"], authors=["Synthetic fixture"], year=2026,
        url="https://example.invalid/fixture/"+record["key"], external_ids={"fixture": record["key"]}, sources=["synthetic_evaluation"], verified=True)
        for record in suite["records"]]
    class Sources:
        def search(self, *a, **kw): return rows, []
        def metadata(self, identifier, *a): return [r for r in rows if identifier in {r.title, r.url}]
        def verify(self, article, *a): return article
    evaluation_context = "Évaluation de pertinence sur des notices explicitement synthétiques, pas des publications réelles. Dans ce test, retenir/recommander signifie choisir une notice du jeu pour le problème décrit. Enregistre les jugements et finalist_ids avec record_candidate_decisions. Ne présente jamais ces notices comme de vrais articles. Utilise le fournisseur scientifique du test pour consulter le lot ; ne cherche pas de publications supplémentaires.\n\n"
    state = {"messages": [{"role": "user", "content": evaluation_context + suite["query"]}], "articles": {}}
    config = replace(ScholarV4Config.from_env(), web_search_enabled=False, max_tool_rounds=4, max_cost_usd=1)
    def abstract_only(article, *a, **kw):
        return {"access_level": "abstract_only", "access_status": "abstract_only", "text": "", "chunks": [], "reason": "Synthetic abstract fixture only."}
    with patch("agents.EnnoScholarV4.toolkit.get_fulltext", abstract_only):
        result = ScholarChatAgent(config, sources=Sources()).run(state,
            emit=lambda event: print(event["tool"], event["status"], flush=True))
    recommended = result["recommended_article_ids"]
    judged = [(aid, labels[state["articles"][aid]["external_ids"]["fixture"]]) for aid in recommended]
    hits = sum(label for _, label in judged)
    metrics = {"evaluation": "real_model_synthetic_sources_manual_gold", "precision": hits/len(judged) if judged else None,
        "recall": hits/sum(labels.values()), "recommended_keys": [state["articles"][aid]["external_ids"]["fixture"] for aid in recommended],
        "partial": result["partial"], "usage": result["usage"], "answer": result["answer"]}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in metrics.items() if k != "answer"}))
    return metrics


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evaluate(args.env_file, args.output)
