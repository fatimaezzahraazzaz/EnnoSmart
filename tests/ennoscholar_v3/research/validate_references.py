"""Explicit, unbilled metadata verification against DOI registry + original pages."""
import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup


def verify(row):
    result = dict(row)
    with httpx.Client(timeout=25, follow_redirects=True, headers={"User-Agent": "EnnoScholarV3/1.0"}) as client:
        try:
            if row.get("doi"):
                response = client.get("https://api.crossref.org/works/" + quote(row["doi"], safe=""))
                if response.status_code in {429, 500, 502, 503, 504}:
                    response = client.get("https://api.crossref.org/works/" + quote(row["doi"], safe=""))
                result["registry_http_status"] = response.status_code
                response.raise_for_status()
                data = response.json()["message"]
                result.update(title=data.get("title", [""])[0],
                    authors=[" ".join(filter(None, [a.get("given"), a.get("family")])) for a in data.get("author", [])],
                    year=(data.get("published") or data.get("issued"))["date-parts"][0][0],
                    publisher=data.get("publisher"), url=data.get("resource", {}).get("primary", {}).get("URL") or data["URL"],
                    registry_verified=data.get("DOI", "").lower() == row["doi"].lower(),
                    registry_source="https://api.crossref.org/works/" + row["doi"])
                url = "https://doi.org/" + row["doi"]
            else:
                url = "https://arxiv.org/abs/" + row["arxiv_id"]
            response = client.get(url)
            result["original_url"] = str(response.url)
            result["original_http_status"] = response.status_code
            response.raise_for_status()
            soup = BeautifulSoup(response.content, "html.parser")
            def meta(name):
                node = soup.find("meta", attrs={"name": name})
                return str(node.get("content", "")) if node else ""
            title = meta("citation_title") or meta("dc.title")
            authors = [str(node.get("content", "")) for node in soup.find_all("meta", attrs={"name": "citation_author"})]
            result.update(original_title=title, original_authors=authors, original_date=meta("citation_date") or meta("citation_publication_date"),
                original_doi=meta("citation_doi"), original_verified=bool(title))
            if not row.get("doi"):
                date = result["original_date"]
                result.update(title=title, authors=authors, year=int(date[:4]) if date[:4].isdigit() else None,
                    url=url, registry_verified=False)
            if title and result.get("title") and " ".join(title.lower().split()) != " ".join(result["title"].lower().split()):
                result["metadata_warning"] = "Original page and registry titles differ; human review required."
        except Exception as exc:
            result["original_error"] = type(exc).__name__
    # Bibliographic validation is distinct from human scientific relevance annotation.
    result["bibliographic_status"] = "verified" if result.get("original_verified") or result.get("registry_verified") else "unverified"
    result["human_relevance"] = None
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = json.loads(Path(__file__).with_name("reference.json").read_text(encoding="utf-8"))
    with ThreadPoolExecutor(max_workers=4) as pool:
        source["references"] = list(pool.map(verify, source["references"]))
    source["verified_at"] = datetime.now(timezone.utc).isoformat()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(source, ensure_ascii=False, indent=2), encoding="utf-8")
    for row in source["references"]:
        print(row["label"], "|", row["bibliographic_status"], "|", row.get("year"), "|", row.get("title", "?"), "| original:", row.get("original_http_status"))


if __name__ == "__main__":
    main()
