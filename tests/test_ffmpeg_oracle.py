"""Cross-check against a real, independent subtitle implementation: ffmpeg.

ffmpeg generates ASS (a format this package only reads) and reads the SRT/WebVTT files
this package writes. If ffmpeg is not installed the tests skip; CI installs it."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from subtitle_mcp.formats import convert_model, parse_subtitle, write_srt, write_vtt
from subtitle_mcp.model import Cue, Subtitle

_WINGET = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe")
FFMPEG = shutil.which("ffmpeg") or (_WINGET if os.path.isfile(_WINGET) else None)

pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")


def run_ffmpeg(*args: str) -> None:
    assert FFMPEG is not None
    proc = subprocess.run(
        [FFMPEG, "-v", "error", "-y", *args], capture_output=True, text=True, timeout=120, check=False
    )
    assert proc.returncode == 0, proc.stderr


CUES = [
    Cue(1000, 3500, "Hello, world"),
    Cue(4000, 6250, "Two\nlines here"),
    Cue(7230, 9990, "G\u00fcnayd\u0131n \u015fimdi \u0130stanbul"),
    Cue(3_600_000, 3_602_500, "An hour in"),
]


def test_real_ass_made_by_ffmpeg_parses_to_the_same_cues(tmp_path: Path) -> None:
    src = tmp_path / "in.srt"
    src.write_text(write_srt(Subtitle("srt", [c.copy() for c in CUES])), encoding="utf-8")
    ass = tmp_path / "out.ass"
    run_ffmpeg("-i", str(src), str(ass))
    sub = parse_subtitle(ass.read_text(encoding="utf-8"))
    assert sub.format == "ass"
    got = [(c.start, c.end, c.text) for c in sub.cues]
    # ASS has centisecond precision: 7230 -> 7230, 9990 -> 9990 are exact multiples of 10 ms
    assert got == [(c.start, c.end, c.text) for c in CUES]


def test_ffmpeg_reads_our_vtt_and_srt_and_agrees(tmp_path: Path) -> None:
    sub = Subtitle("srt", [c.copy() for c in CUES])
    srt = tmp_path / "ours.srt"
    srt.write_text(write_srt(sub), encoding="utf-8")
    vtt = tmp_path / "ours.vtt"
    vtt.write_text(write_vtt(convert_model(sub, "vtt")[0]), encoding="utf-8")
    for source in (srt, vtt):
        back = tmp_path / f"back-{source.suffix[1:]}.srt"
        run_ffmpeg("-i", str(source), str(back))  # ffmpeg's own re-serialisation
        again = parse_subtitle(back.read_text(encoding="utf-8"))
        assert [(c.start, c.end, c.text) for c in again.cues] == [
            (c.start, c.end, c.text) for c in CUES
        ], source.name


def test_ffmpeg_reads_the_fixed_output_of_a_messy_file(tmp_path: Path) -> None:
    from fixtures import MESSY_SRT

    from subtitle_mcp import server as S
    from subtitle_mcp.files import Sandbox

    (tmp_path / "messy.srt").write_bytes(MESSY_SRT)
    old = S._State.sandbox
    S._State.sandbox = Sandbox([tmp_path])
    try:
        S.fix_common_problems("messy.srt", apply=True)
    finally:
        S._State.sandbox = old
    out = tmp_path / "fixed-by-ffmpeg.srt"
    run_ffmpeg("-i", str(tmp_path / "messy.fixed.srt"), str(out))
    assert len(parse_subtitle(out.read_text(encoding="utf-8")).cues) == 8
