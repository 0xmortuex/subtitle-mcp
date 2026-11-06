"""MCP stdio smoke test: spawns the real, installed server as a subprocess, lists the
tools, runs a real session over JSON-RPC, and checks that instructive errors survive the
trip to the agent (MCPServer hides any exception that is not a ToolError)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from fixtures import MESSY_SRT, SIMPLE_SRT, TURKISH_CP1254  # noqa: E402
from mcp import ClientSession  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402

EXPECTED_TOOLS = {
    "subtitle_info",
    "read_cues",
    "search_subtitles",
    "shift",
    "resync",
    "convert",
    "fix_common_problems",
    "merge_or_split",
    "chunk_for_translation",
    "apply_translation",
}


def params(directory: Path) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable, args=["-m", "subtitle_mcp", "--dir", str(directory)]
    )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_tools_and_a_full_session_over_stdio(tmp_path: Path) -> None:
    (tmp_path / "messy.srt").write_bytes(MESSY_SRT)
    (tmp_path / "tr.srt").write_bytes(TURKISH_CP1254)
    (tmp_path / "s.srt").write_bytes(SIMPLE_SRT)
    async with stdio_client(params(tmp_path)) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        assert init.server_info.name == "subtitle"
        tools = (await session.list_tools()).tools
        assert {t.name for t in tools} == EXPECTED_TOOLS
        assert all(t.description and len(t.description) > 100 for t in tools)

        res = await session.call_tool("subtitle_info", {"path": "tr.srt"})
        assert not res.is_error
        info = json.loads(res.content[0].text)  # type: ignore[union-attr]
        assert info["encoding"]["name"] == "cp1254" and info["cue_count"] == 3

        res = await session.call_tool("fix_common_problems", {"path": "messy.srt"})
        assert not res.is_error and "Dry run: nothing written" in res.content[0].text  # type: ignore[union-attr]
        assert not (tmp_path / "messy.fixed.srt").exists()
        res = await session.call_tool("fix_common_problems", {"path": "messy.srt", "apply": True})
        assert (tmp_path / "messy.fixed.srt").exists()

        res = await session.call_tool("shift", {"path": "s.srt", "frames": 24, "fps": 29.97})
        assert not res.is_error and "801 ms" in res.content[0].text  # type: ignore[union-attr]

        res = await session.call_tool("chunk_for_translation", {"path": "s.srt"})
        chunk = json.loads(res.content[0].text)  # type: ignore[union-attr]
        mapping = {c["id"]: c["text"].upper() for c in chunk["cues"]}
        res = await session.call_tool("apply_translation", {"path": "s.srt", "mapping": mapping})
        assert not res.is_error and "Translated 3 of 3" in res.content[0].text  # type: ignore[union-attr]
        assert "SECOND LINE\nWITH TWO ROWS" in (tmp_path / "s.translated.srt").read_text(encoding="utf-8")


@pytest.mark.anyio
async def test_instructive_errors_reach_the_agent(tmp_path: Path) -> None:
    (tmp_path / "s.srt").write_bytes(SIMPLE_SRT)
    async with stdio_client(params(tmp_path)) as (read, write), ClientSession(read, write) as session:
        await session.initialize()

        res = await session.call_tool("subtitle_info", {"path": "missing.srt"})
        assert res.is_error and "does not exist" in res.content[0].text  # type: ignore[union-attr]

        res = await session.call_tool("subtitle_info", {"path": str(tmp_path.parent / "x.srt")})
        assert res.is_error and "outside the allowed directories" in res.content[0].text  # type: ignore[union-attr]

        res = await session.call_tool("shift", {"path": "s.srt", "offset_ms": -9000})
        assert res.is_error and "would start at -8000 ms" in res.content[0].text  # type: ignore[union-attr]

        await session.call_tool("shift", {"path": "s.srt", "offset_ms": 1})
        res = await session.call_tool("shift", {"path": "s.srt", "offset_ms": 2})
        assert res.is_error and "overwrite=true" in res.content[0].text  # type: ignore[union-attr]

        res = await session.call_tool("apply_translation", {"path": "s.srt", "mapping": {"1": "x"}})
        assert res.is_error and "2 of 3 cue(s) have no translation" in res.content[0].text  # type: ignore[union-attr]


def test_cli_rejects_a_missing_dir() -> None:
    import subprocess

    proc = subprocess.run(
        [sys.executable, "-m", "subtitle_mcp", "--dir", "/definitely/not/here"],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert proc.returncode == 2 and "is not an existing directory" in proc.stderr
