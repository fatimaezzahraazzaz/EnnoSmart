"""Free registry identity checks. Never sends benchmark identities to the agent."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--free-registry-check", action="store_true", required=True)
    args = parser.parse_args()
    path = Path(__file__).with_name("references.json")
    suite = json.loads(path.read_text(encoding="utf-8"))
    with httpx.Client(timeout=25, follow_redirects=True) as client:
        for case in suite["cases"]:
            for row in case["references"]:
                if not row.get("doi") or row["doi"].startswith("10.48550/arxiv."):
                    continue
                url = "https://api.crossref.org/works/"+quote(row["doi"], safe="")
                try:
                    record = client.get(url).raise_for_status().json()["message"]
                    if record["DOI"].casefold() != row["doi"].casefold():
                        raise ValueError("DOI mismatch")
                    row.update(title=record["title"][0], authors=[" ".join(filter(None, [a.get("given"),a.get("family")])) for a in record.get("author", [])],
                        year=(record.get("published") or record.get("issued"))["date-parts"][0][0], bibliographic_status="verified",
                        registry_source=url, registry_checked_at=datetime.now(timezone.utc).isoformat())
                    row["registry_record"] = {k: record.get(k) for k in ("DOI", "title", "author", "published", "issued")}
                except Exception as exc:
                    row["last_check_error"] = type(exc).__name__
                print(case["case"], row["label"], row.get("bibliographic_status", "unverified"), flush=True)
    path.write_text(json.dumps(suite, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__": main()
