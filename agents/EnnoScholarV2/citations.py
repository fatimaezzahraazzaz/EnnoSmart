from __future__ import annotations

from .models import Citation


def parse_output(output: list[dict]) -> tuple[str, list[Citation]]:
    """Keep exact text offsets, including across multiple output text blocks."""
    parts: list[str] = []
    citations: list[Citation] = []
    seen_sources: set[str] = set()
    offset = 0
    for item in output:
        if item.get("type") == "web_search_call":
            for source in (item.get("action") or {}).get("sources", []):
                url = source.get("url", "")
                if url.startswith(("https://", "http://")) and url not in seen_sources:
                    seen_sources.add(url)
                    citations.append(Citation(url=url, title=source.get("title", ""), cited=False))
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") != "output_text":
                continue
            value = content.get("text", "")
            if parts:
                offset += 1
            for annotation in content.get("annotations", []):
                if annotation.get("type") != "url_citation":
                    continue
                url = annotation.get("url", "")
                if not url.startswith(("https://", "http://")):
                    continue
                start, end = annotation.get("start_index"), annotation.get("end_index")
                valid = isinstance(start, int) and isinstance(end, int) and 0 <= start <= end <= len(value)
                citations.append(Citation(url=url, title=annotation.get("title", ""),
                    start_index=offset + start if valid else None,
                    end_index=offset + end if valid else None))
            parts.append(value)
            offset += len(value)
    return "\n".join(parts), citations
