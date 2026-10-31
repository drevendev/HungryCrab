"""Exercise an installed MCP executable over the real stdio transport, without a model."""

from __future__ import annotations

import os
from pathlib import Path

import anyio
from mcp import Client
from mcp.client.stdio import StdioServerParameters


async def probe(command: str | list[str], workspace: Path, cache: Path) -> None:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"GH_TOKEN", "GITHUB_TOKEN", "PYTHONPATH"}
    }
    env["PYTHONIOENCODING"] = "utf-8"
    invocation = [command] if isinstance(command, str) else command
    parameters = StdioServerParameters(
        command=invocation[0],
        args=[*invocation[1:], "--cache-dir", str(cache), "mcp", "--root", str(workspace)],
        env=env,
        cwd=workspace,
    )
    for mode in ("auto", "legacy"):
        with anyio.fail_after(90):
            async with Client(parameters, mode=mode, read_timeout_seconds=60) as client:
                listing = await client.list_tools()
                assert {tool.name for tool in listing.tools} == {"crab_digest", "crab_menu"}
                denied = await client.call_tool("crab_digest", {"prey": "example/network-disabled"})
                assert denied.is_error
                digest = await client.call_tool("crab_digest", {"prey": "prey"})
                assert not digest.is_error and digest.structured_content
                assert digest.structured_content["manifest"]["schema"] == "hungry-crab.digest/1"
                page = await client.call_tool(
                    "crab_digest", {"prey": "prey", "page": "inventory.md"}
                )
                assert not page.is_error and page.structured_content["page"]["text"]
                menu = await client.call_tool(
                    "crab_menu", {"prey": "prey", "maw": "maw", "limit": 2}
                )
                assert not menu.is_error and menu.structured_content["candidates"]
                assert menu.structured_content["side_effects"]["ledger_written"] is False
                assert not (workspace / "maw" / ".crab" / "ledger.json").exists()
