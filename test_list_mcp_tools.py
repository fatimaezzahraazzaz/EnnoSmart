import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

async def main():
    url = "http://127.0.0.1:8010/mcp"

    async with streamable_http_client(url) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.list_tools()

            print("OUTILS MCP DISPONIBLES")
            print("======================")
            for tool in result.tools:
                print(f"- {tool.name}")
                if tool.description:
                    print(f"  {tool.description}")

asyncio.run(main())
