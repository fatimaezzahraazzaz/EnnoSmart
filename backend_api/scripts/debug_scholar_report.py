import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.scholar_service import _extract_articles_from_report


DEFAULT_REPORT = Path("ennoscholar_report.json")


def load_json(path: Path):
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return json.loads(path.read_text(encoding=enc))
        except UnicodeDecodeError:
            continue


report_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_REPORT
report = load_json(report_path)

articles = _extract_articles_from_report(report)

print("Rapport :", report_path)
print("Articles détectés :", len(articles))

for idx, article in enumerate(articles[:20], start=1):
    title = article.get("title") or article.get("titre") or article.get("paper_title")
    year = article.get("year") or article.get("publication_year") or article.get("published_year")
    tag = article.get("tag_article") or article.get("tag") or article.get("decision")
    print(f"{idx}. {title} | year={year} | tag={tag}")
