import os
import sys
import json
from dotenv import load_dotenv

sys.path.insert(0, r"C:\EnnoSmart")
load_dotenv(r"C:\EnnoSmart\backend_api\.env", override=True)

from agents.EnnoScholar.article_summarizer import summarize_candidate_articles

print("GEMINI_API_KEY loaded =", bool(os.getenv("GEMINI_API_KEY")))

intent = {
    "verrou_id": "TEST-ATR",
    "verrou_title": "Représentativité des données synthétiques pour la classification ATR SAR",
    "scientific_problem": "Validation of synthetic radar data representativeness for SAR ATR classification",
    "technical_object": "SAR ATR classification",
    "phenomenon": "domain shift between synthetic and measured radar data",
    "key_terms_en": ["SAR", "ATR", "synthetic data", "measured data", "domain shift"],
    "key_terms_fr": ["données synthétiques", "données mesurées", "représentativité"]
}

articles = [
    {
        "title": "Domain Generalization for SAR Automatic Target Recognition",
        "abstract": "This paper studies synthetic and measured SAR data for automatic target recognition classification and domain shift.",
        "year": 2024,
        "venue": "IEEE",
        "source": "test",
        "tag": "Direct",
        "relevance_score": 0.91,
        "score_details": {
            "matched_anchors": ["SAR", "ATR", "synthetic data", "measured data"]
        }
    }
]

out, report = summarize_candidate_articles(articles, intent, top_n=1)

print("SUMMARY REPORT:")
print(json.dumps(report, ensure_ascii=False, indent=2))

print("ARTICLE SUMMARY:")
print(json.dumps(out[0].get("article_summary"), ensure_ascii=False, indent=2))
