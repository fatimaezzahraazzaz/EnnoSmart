from __future__ import annotations

import argparse
import asyncio
import json
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def run(
    url: str,
    title: str,
    doi: str | None,
    force_refresh: bool,
    search_all: bool,
) -> None:
    async with streamable_http_client(url) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(
                "resolve_legal_fulltext",
                arguments={
                    "title": title,
                    "doi": doi,
                    "authors": [],
                    "known_urls": [],
                    "force_refresh": force_refresh,
                    "search_all": search_all,
                },
            )
            structured = getattr(result, "structuredContent", None) or getattr(result, "structured_content", None)
            if structured is not None:
                print(json.dumps(structured, ensure_ascii=False, indent=2, default=str))
                return
            for block in result.content:
                text = getattr(block, "text", None)
                if text:
                    print(text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=os.getenv("ENNOSCHOLAR_LEGAL_MCP_URL", "http://127.0.0.1:8010/mcp"))
    parser.add_argument("--title", required=True)
    parser.add_argument("--doi", default=None)
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--search-all", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.url, args.title, args.doi, args.force_refresh, args.search_all))


if __name__ == "__main__":
    main()
