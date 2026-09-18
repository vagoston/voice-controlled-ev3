"""Smoke test: spawn the EV3 MCP server over stdio and drive it like a real client."""

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT_ROOT = Path(__file__).resolve().parent.parent


async def main() -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "ev3_mcp.server"],
        cwd=str(PROJECT_ROOT),
        # The SDK passes a filtered environment by default, so the smoke test
        # has to hand the server its own env to force dry-run.
        env={**os.environ, "EV3_DRY_RUN": "1"},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print("tools exposed:")
            for tool in tools.tools:
                print("  -", tool.name)

            print("\nlist_skills ->")
            result = await session.call_tool("list_skills", {})
            print(" ", result.content[0].text)

            print("\nrun_skill(follow_wall) ->")
            result = await session.call_tool(
                "run_skill", {"name": "follow_wall", "args": {"seconds": 2}}
            )
            payload = json.loads(result.content[0].text)
            print("  ok:", payload["ok"], "dry_run:", payload.get("dry_run"))
            print("  program bytes:", len(payload.get("program", "")))


if __name__ == "__main__":
    asyncio.run(main())
