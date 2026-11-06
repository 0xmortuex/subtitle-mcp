"""The MCP tools, called as functions against real files in a temp directory."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fixtures import (
    ASS_SAMPLE,
    MESSY_SRT,
    SIMPLE_SRT,
    TURKISH_CP1254,
    TURKISH_TEXT_SRT,
    VTT_COMPLEX,
)

from subtitle_mcp import server as S
from subtitle_mcp.errors import SubtitleError
from subtitle_mcp.files import Sandbox


@pytest.fixture
def box(tmp_path: Path) -> Iterator[Path]:
    old = S._State.sandbox
    S._State.sandbox = Sandbox([tmp_path])
    yield tmp_path
    S._State.sandbox = old


def put(box: Path, name: str, data: bytes) -> Path:
    p = box / name
    p.write_bytes(data)
    return p


def text(p: Path) -> str:
    return p.read_bytes().decode("utf-8")


# --- subtitle_info / read_cues / search --------------------------------------------------


def test_info_on_messy_file(box: Path) -> None:
    put(box, "messy.srt", MESSY_SRT)
    info = json.loads(S.subtitle_info("messy.srt"))
    assert info["format"] == "srt"
    assert info["encoding"]["name"] == "utf-8-sig" and info["encoding"]["bom"] is True
    assert info["newlines"]["mixed"] is True
    assert info["cue_count"] == 8
    assert info["first_start"] == "00:00:01,000" and info["last_end"] == "100:00:02,000"
    assert info["overlaps"]["count"] == 3
    assert info["max_line_length"] == 84
    assert info["reading_speed_chars_per_sec"]["max"] == 112.0
    assert info["reading_speed_chars_per_sec"]["cues_over_limit"] == 2
    kinds = {p["kind"]: p for p in info["problems"]}
    assert set(kinds) == {
        "bom", "mixed_newlines", "blank_line_in_cue", "out_of_order", "bad_duration", "overlap",
        "index_missing", "index_duplicate", "long_line", "reading_speed",
    }
    assert kinds["bad_duration"]["cues"] == [2] and kinds["out_of_order"]["cues"] == [3]
    assert kinds["reading_speed"]["fixable"] is False
    assert info["gaps"]["longest_after_cue"] == 7


def test_info_turkish_cp1254_detected_and_readable(box: Path) -> None:
    put(box, "tr.srt", TURKISH_CP1254)
    info = json.loads(S.subtitle_info("tr.srt"))
    assert info["encoding"]["name"] == "cp1254"
    shown = S.read_cues("tr.srt", start_cue=2, end_cue=2)
    assert "İstanbul'da çok güzel bir gün." in shown
    assert "Ğazi'nin şarkısı öyle..." in shown


def test_info_encoding_override_is_used(box: Path) -> None:
    put(box, "tr.srt", TURKISH_CP1254)
    info = json.loads(S.subtitle_info("tr.srt", encoding="cp1252"))
    assert info["encoding"]["confidence"] == "user-specified"
    with pytest.raises(SubtitleError, match="cannot decode the file as utf-8"):
        S.subtitle_info("tr.srt", encoding="utf-8")


def test_info_vtt_and_ass(box: Path) -> None:
    put(box, "a.vtt", VTT_COMPLEX)
    put(box, "b.ass", ASS_SAMPLE)
    assert json.loads(S.subtitle_info("a.vtt"))["first_start"] == "00:00:01.000"
    ass = json.loads(S.subtitle_info("b.ass"))
    assert ass["format"] == "ass" and ass["cue_count"] == 2
    assert any("styles, fonts" in w for w in ass["parse_warnings"])


def test_read_cues_by_number_time_and_limits(box: Path) -> None:
    put(box, "s.srt", SIMPLE_SRT)
    assert S.read_cues("s.srt", start_cue=2, end_cue=2) == (
        "1 matching cue(s) of 3; showing 1.\n"
        "    2  00:00:04,000 --> 00:00:06,000\n"
        "       Second line\n"
        "       with two rows"
    )
    by_time = S.read_cues("s.srt", from_time="00:00:05", to_time="00:00:10")
    assert "Second line" in by_time and "First line" not in by_time and "Third" not in by_time
    out = S.read_cues("s.srt", limit=2)
    assert "3 matching cue(s) of 3; showing 2." in out
    assert "Continue with start_cue=3." in out
    with pytest.raises(SubtitleError, match="either start_cue"):
        S.read_cues("s.srt", start_cue=1, from_time="0")
    with pytest.raises(SubtitleError, match="only 3 cues"):
        S.read_cues("s.srt", start_cue=9)
    with pytest.raises(SubtitleError, match="limit"):
        S.read_cues("s.srt", limit=0)


def test_search(box: Path) -> None:
    put(box, "s.srt", SIMPLE_SRT)
    hit = S.search_subtitles("s.srt", "LINE with")  # case-insensitive, spans a line break
    assert hit.startswith("1 cue(s) match 'LINE with'") and "00:00:04,000 --> 00:00:06,000" in hit
    assert S.search_subtitles("s.srt", "^(First|Third)", regex=True).startswith("2 cue(s) match")
    assert S.search_subtitles("s.srt", "Line", case_sensitive=True).startswith("No cue matches")
    with pytest.raises(SubtitleError, match="invalid regular expression"):
        S.search_subtitles("s.srt", "(", regex=True)
    with pytest.raises(SubtitleError, match="query is empty"):
        S.search_subtitles("s.srt", "")


# --- shift / resync ---------------------------------------------------------------------


def test_shift_offset_exact_output(box: Path) -> None:
    put(box, "s.srt", SIMPLE_SRT)
    msg = S.shift("s.srt", offset_ms=1500)
    assert "Shifted 3 cue(s) by 1500 ms." in msg
    assert text(box / "s.shifted.srt") == (
        "1\n00:00:02,500 --> 00:00:04,500\nFirst line\n\n"
        "2\n00:00:05,500 --> 00:00:07,500\nSecond line\nwith two rows\n\n"
        "3\n00:00:11,500 --> 00:00:13,500\nThird\n"
    )
    assert text(box / "s.srt") == SIMPLE_SRT.decode()  # the original is untouched


def test_shift_frames_at_2997(box: Path) -> None:
    put(box, "s.srt", SIMPLE_SRT)
    msg = S.shift("s.srt", frames=24, fps=29.97, output="out.srt")
    assert "24 frame(s) at 29.97 fps = 801 ms" in msg
    assert "00:00:01,801 --> 00:00:03,801" in text(box / "out.srt")


def test_shift_from_cue_and_overlap_note(box: Path) -> None:
    put(box, "s.srt", SIMPLE_SRT)
    msg = S.shift("s.srt", offset_ms=-2000, from_cue=3, output="out.srt")
    assert "starting at cue 3" in msg
    assert text(box / "out.srt").endswith("3\n00:00:08,000 --> 00:00:10,000\nThird\n")


def test_shift_overlap_note_when_it_really_overlaps(box: Path) -> None:
    put(box, "s.srt", SIMPLE_SRT)
    msg = S.shift("s.srt", offset_ms=-5000, from_cue=3, output="o.srt")
    assert "created 1 new overlap(s)" in msg


def test_shift_rejects_bad_requests_and_writes_nothing(box: Path) -> None:
    put(box, "s.srt", SIMPLE_SRT)
    with pytest.raises(SubtitleError, match="would start at -1000 ms"):
        S.shift("s.srt", offset_ms=-2000)
    assert not (box / "s.shifted.srt").exists()
    with pytest.raises(SubtitleError, match="exactly one of"):
        S.shift("s.srt")
    with pytest.raises(SubtitleError, match="exactly one of"):
        S.shift("s.srt", offset_ms=5, frames=5, fps=25)
    with pytest.raises(SubtitleError, match="frames needs fps"):
        S.shift("s.srt", frames=5)
    with pytest.raises(SubtitleError, match="fps only applies"):
        S.shift("s.srt", offset_ms=5, fps=25)
    with pytest.raises(SubtitleError, match="from_cue must be between 1 and 3"):
        S.shift("s.srt", offset_ms=5, from_cue=4)


def _drifting_pair() -> tuple[bytes, bytes]:
    """Correct file and a copy that is truth = bad*1001/1000 + 2500 ms off (drift + offset)."""
    bad = [10_000, 600_000, 3_000_000, 6_000_000]
    truth = [b * 1001 // 1000 + 2500 for b in bad]
    assert all(b * 1001 % 1000 == 0 for b in bad)

    def build(starts: list[int], dur: int) -> bytes:
        from subtitle_mcp.formats import write_srt
        from subtitle_mcp.model import Cue, Subtitle

        return write_srt(Subtitle("srt", [Cue(s, s + dur, f"line {i}") for i, s in enumerate(starts, 1)])).encode()

    return build(bad, 1000), build(truth, 1001)


def test_resync_recovers_drift_and_offset_exactly(box: Path) -> None:
    bad, truth = _drifting_pair()
    put(box, "bad.srt", bad)
    msg = S.resync(
        "bad.srt",
        anchors=[{"cue": 1, "to": "00:00:12,510"}, {"cue": 4, "to": "01:40:08,500"}],
    )
    assert "exact: a = 1001/1000, b = 2500 ms" in msg
    assert "Two anchors define the line exactly." in msg
    assert "+0 ms, +0 ms; largest 0 ms." in msg
    assert (box / "bad.resynced.srt").read_bytes() == truth  # middle cues exact too


def test_resync_with_at_times_and_three_anchors(box: Path) -> None:
    bad, truth = _drifting_pair()
    put(box, "bad.srt", bad)
    msg = S.resync(
        "bad.srt",
        anchors=[
            {"at": "00:00:10", "to": "00:00:12.510"},
            {"cue": 3, "to": "00:50:05,500"},
            {"cue": 4, "to": "01:40:08,500"},
        ],
        output="fit3.srt",
    )
    assert "Least-squares fit through 3 anchors." in msg
    assert (box / "fit3.srt").read_bytes() == truth  # consistent anchors: LS == exact


def test_resync_reports_residual_for_inconsistent_anchors(box: Path) -> None:
    bad, _ = _drifting_pair()
    put(box, "bad.srt", bad)
    msg = S.resync(
        "bad.srt",
        anchors=[{"cue": 1, "to": "00:00:12,510"}, {"cue": 2, "to": "00:10:03,100"},
                 {"cue": 4, "to": "01:40:08,600"}],  # last anchor 100 ms off
    )
    assert "largest" in msg and "largest 0 ms" not in msg


def test_resync_fps_mode(box: Path) -> None:
    put(box, "s.srt", SIMPLE_SRT)
    msg = S.resync("s.srt", fps_from=25, fps_to=23.976, output="o.srt")
    assert "x 1001/960" in msg  # 25025/24000 reduced
    # 1000 ms * 25025/24000 = 1042.708 -> 1043; 10000 -> 10427.08 -> 10427
    assert "00:00:01,043 --> 00:00:03,128" in text(box / "o.srt")
    assert "00:00:10,427" in text(box / "o.srt")


def test_resync_error_messages(box: Path) -> None:
    put(box, "s.srt", SIMPLE_SRT)
    good = {"cue": 1, "to": "00:00:02"}
    with pytest.raises(SubtitleError, match="either anchors"):
        S.resync("s.srt")
    with pytest.raises(SubtitleError, match="either anchors"):
        S.resync("s.srt", anchors=[good, {"cue": 3, "to": "0:20"}], fps_from=25, fps_to=24)
    with pytest.raises(SubtitleError, match="at least two"):
        S.resync("s.srt", anchors=[good])
    with pytest.raises(SubtitleError, match="same current time"):
        S.resync("s.srt", anchors=[good, {"cue": 1, "to": "00:00:09"}])
    with pytest.raises(SubtitleError, match="reverse or collapse"):
        S.resync("s.srt", anchors=[good, {"cue": 3, "to": "00:00:01"}])
    with pytest.raises(SubtitleError, match="anchor 2: cue 9 does not exist"):
        S.resync("s.srt", anchors=[good, {"cue": 9, "to": "00:00:09"}])
    with pytest.raises(SubtitleError, match="anchor 2: give 'to'"):
        S.resync("s.srt", anchors=[good, {"cue": 2}])
    with pytest.raises(SubtitleError, match="exactly one of 'cue'"):
        S.resync("s.srt", anchors=[good, {"cue": 2, "at": "0:05", "to": "0:07"}])
    with pytest.raises(SubtitleError, match="together"):
        S.resync("s.srt", fps_from=25)
    with pytest.raises(SubtitleError, match="before 00:00:00"):
        S.resync("s.srt", anchors=[{"cue": 2, "to": "00:00:01"}, {"cue": 3, "to": "00:00:10"}])


# --- convert ----------------------------------------------------------------------------


def test_convert_vtt_to_srt_file_and_report(box: Path) -> None:
    put(box, "a.vtt", VTT_COMPLEX)
    msg = S.convert("a.vtt", "srt")
    assert "Converted VTT (utf-8) -> SRT (utf-8): 3 cue(s)." in msg
    assert "dropped WebVTT cue settings on 2 cue(s)" in msg
    assert text(box / "a.converted.srt").startswith("1\n00:00:01,000 --> 00:00:03,500\n<v Alice>")


def test_convert_ass_to_srt_and_vtt_states_styling_is_dropped(box: Path) -> None:
    put(box, "a.ass", ASS_SAMPLE)
    msg = S.convert("a.ass", "srt")
    assert "ASS styling (fonts, colours, positions, effects) was dropped" in msg
    assert text(box / "a.converted.srt") == (
        "1\n00:00:01,250 --> 00:00:03,000\nLine one\nLine two\n\n"
        "2\n00:00:05,500 --> 00:00:07,000\nSecond in time, first in file, with comma\n"
    )
    S.convert("a.ass", "vtt")
    assert text(box / "a.converted.vtt").startswith("WEBVTT\n\n00:00:01.250 --> 00:00:03.000\nLine one\nLine two")
    with pytest.raises(SubtitleError, match="ASS cannot be written"):
        S.convert("a.ass", "ass")


def test_convert_reencodes_cp1254_to_utf8_and_back(box: Path) -> None:
    put(box, "tr.srt", TURKISH_CP1254)
    S.convert("tr.srt", "srt", output="tr8.srt")
    assert (box / "tr8.srt").read_bytes() == TURKISH_TEXT_SRT.encode("utf-8")
    S.convert("tr8.srt", "srt", output="trback.srt", output_encoding="cp1254")
    assert (box / "trback.srt").read_bytes() == TURKISH_CP1254
    S.convert("tr8.srt", "srt", output="trbom.srt", output_encoding="utf-8-sig")
    assert (box / "trbom.srt").read_bytes().startswith(b"\xef\xbb\xbf1\n")
    S.convert("tr8.srt", "srt", output="tr16.srt", output_encoding="utf-16")
    assert json.loads(S.subtitle_info("tr16.srt"))["encoding"]["name"] == "utf-16"


def test_convert_unencodable_output_fails_clearly(box: Path) -> None:
    put(box, "tr.srt", TURKISH_CP1254)
    with pytest.raises(SubtitleError, match="cannot be written as cp1252"):
        S.convert("tr.srt", "srt", output="x.srt", output_encoding="cp1252")
    assert not (box / "x.srt").exists()


def test_convert_removes_blank_lines_and_says_so(box: Path) -> None:
    put(box, "messy.srt", MESSY_SRT)
    msg = S.convert("messy.srt", "vtt")
    assert "removed blank lines inside 1 cue(s)" in msg


# --- fix_common_problems ----------------------------------------------------------------


def test_fix_dry_run_lists_each_fix_and_writes_nothing(box: Path) -> None:
    put(box, "messy.srt", MESSY_SRT)
    msg = S.fix_common_problems("messy.srt")
    for needle in (
        "Would apply 8 kind(s) of fix",
        "[bom]",
        "[mixed_newlines] all line endings normalised to LF",
        "[blank_line_in_cue] removed blank lines inside 1 cue(s)",
        "[out_of_order]",
        "cue 2: end 00:00:03,500 -> 00:00:04,633",
        "cue 1: end 00:00:03,000 -> 00:00:02,000 (cue 3 starts)",
        "cue 5: end 00:00:12,500 -> 00:00:12,000 (cue 6 starts)",
        "[long_line] re-wrapped 2 cue(s)",
        "[renumber]",
        "Flagged, not changed:",
        "cue 7: 110.0 chars/s",
        "Dry run: nothing written.",
    ):
        assert needle in msg, needle
    assert not (box / "messy.fixed.srt").exists()


def test_fix_apply_exact_output_and_idempotence(box: Path) -> None:
    put(box, "messy.srt", MESSY_SRT)
    msg = S.fix_common_problems("messy.srt", apply=True)
    assert "Applied 8 kind(s) of fix" in msg and "Still present" not in msg
    assert text(box / "messy.fixed.srt") == (
        "1\n00:00:01,000 --> 00:00:02,000\nHello there.\n\n"
        "2\n00:00:02,000 --> 00:00:03,500\n<i>Out of order and duplicate index</i>\n\n"
        "3\n00:00:03,500 --> 00:00:04,633\nZero duration cue\n\n"
        "4\n00:00:06,000 --> 00:00:09,000\nIndex separated from timing\nby a blank line inside the cue.\n\n"
        "5\n00:00:10,000 --> 00:00:12,000\nNo index and dot milliseconds\n\n"
        "6\n00:00:12,000 --> 00:00:14,000\n"
        '<font color="#ff0000">This is a very long line that\ndefinitely needs rewrapping\n'
        "because it is far too long</font>\n\n"
        "7\n00:00:14,000 --> 00:00:14,500\nWay too fast for anyone to\nread this sentence completely\n\n"
        "8\n100:00:00,000 --> 100:00:02,000\nHour past ninety-nine\n"
    )
    again = S.fix_common_problems("messy.fixed.srt")
    assert "nothing to fix" in again and "Flagged, not changed" in again
    assert json.loads(S.subtitle_info("messy.fixed.srt"))["newlines"]["mixed"] is False


def test_fix_strip_tags_option(box: Path) -> None:
    put(box, "messy.srt", MESSY_SRT)
    S.fix_common_problems("messy.srt", apply=True, strip_tags=True, output="nt.srt")
    out = text(box / "nt.srt")
    assert "<i>" not in out and "<font" not in out and "</font>" not in out
    assert "Out of order and duplicate index" in out


def test_fix_keeps_dialogue_dashes_one_speaker_per_line(box: Path) -> None:
    src = (
        "1\n00:00:01,000 --> 00:00:09,000\n- Short.\n- And here the second speaker goes on "
        "and on for far too long indeed\n"
    )
    put(box, "d.srt", src.encode())
    S.fix_common_problems("d.srt", apply=True, max_line_length=30)
    assert text(box / "d.fixed.srt") == (
        "1\n00:00:01,000 --> 00:00:09,000\n- Short.\n- And here the second\n"
        "speaker goes on and on\nfor far too long indeed\n"
    )


def test_fix_cps_is_flagged_never_changed(box: Path) -> None:
    put(box, "m.srt", MESSY_SRT)
    S.fix_common_problems("m.srt", apply=True)
    assert "00:00:14,000 --> 00:00:14,500" in text(box / "m.fixed.srt")


def test_fix_same_start_overlap_is_flagged(box: Path) -> None:
    src = "1\n00:00:01,000 --> 00:00:03,000\nA\n\n2\n00:00:01,000 --> 00:00:02,000\nB\n"
    put(box, "t.srt", src.encode())
    msg = S.fix_common_problems("t.srt")
    assert "cues 1 and 2 start at the same time" in msg


def test_fix_vtt_keeps_notes_and_settings(box: Path) -> None:
    put(box, "a.vtt", VTT_COMPLEX)
    msg = S.fix_common_problems("a.vtt", apply=True)
    assert "nothing to fix" in msg or "Applied" in msg
    out = text(box / "a.fixed.vtt")
    assert "NOTE between cues" in out and "align:start position:10%" in out and "STYLE\n::cue" in out


# --- merge / split ----------------------------------------------------------------------


def test_merge_default_places_second_after_first(box: Path) -> None:
    put(box, "a.srt", SIMPLE_SRT)
    put(box, "b.srt", b"1\n00:00:00,500 --> 00:00:01,500\nPart two\n")
    msg = S.merge_or_split("a.srt", "merge", path2="b.srt")
    assert "second file placed after the last cue of the first file (offset 12000 ms)" in msg
    assert text(box / "a.merged.srt").endswith("4\n00:00:12,500 --> 00:00:13,500\nPart two\n")


def test_merge_with_offset_sorts_and_converts_format(box: Path) -> None:
    put(box, "a.srt", SIMPLE_SRT)
    put(box, "b.vtt", VTT_COMPLEX)
    msg = S.merge_or_split("a.srt", "merge", path2="b.vtt", offset_ms=0, output="m.srt")
    assert "Merged 3 + 3 = 6 cue(s)" in msg and "dropped WebVTT cue settings" in msg
    assert "NOTE/STYLE/REGION block(s) of the second file were not carried over" in msg
    starts = [ln.split(" --> ")[0] for ln in text(box / "m.srt").split("\n") if " --> " in ln]
    assert starts == sorted(starts) and len(starts) == 6


def test_split_exact_and_rezero(box: Path) -> None:
    put(box, "a.srt", SIMPLE_SRT)
    msg = S.merge_or_split("a.srt", "split", at="00:00:05")
    assert "part 1 has 2 cue(s), part 2 has 2 cue(s)" in msg
    assert "1 cue(s) straddle the cut" in msg
    assert text(box / "a.part1.srt") == (
        "1\n00:00:01,000 --> 00:00:03,000\nFirst line\n\n"
        "2\n00:00:04,000 --> 00:00:05,000\nSecond line\nwith two rows\n"
    )
    assert text(box / "a.part2.srt") == (
        "1\n00:00:00,000 --> 00:00:01,000\nSecond line\nwith two rows\n\n"
        "2\n00:00:05,000 --> 00:00:07,000\nThird\n"
    )


def test_split_and_merge_errors(box: Path) -> None:
    put(box, "a.srt", SIMPLE_SRT)
    with pytest.raises(SubtitleError, match="leaves one part empty"):
        S.merge_or_split("a.srt", "split", at="00:00:00,500")
    with pytest.raises(SubtitleError, match="split needs at"):
        S.merge_or_split("a.srt", "split")
    with pytest.raises(SubtitleError, match="merge needs path2"):
        S.merge_or_split("a.srt", "merge")
    with pytest.raises(SubtitleError, match="mode must be"):
        S.merge_or_split("a.srt", "weld")
    with pytest.raises(SubtitleError, match="only apply to mode='merge'"):
        S.merge_or_split("a.srt", "split", at="5", offset_ms=1)


# --- translation contract ---------------------------------------------------------------


def test_chunking_contract(box: Path) -> None:
    put(box, "a.srt", SIMPLE_SRT)
    one = json.loads(S.chunk_for_translation("a.srt"))
    assert one["total_cues"] == 3 and one["chunk_count"] == 1
    assert one["cues"] == [
        {"id": "1", "text": "First line"},
        {"id": "2", "text": "Second line\nwith two rows"},
        {"id": "3", "text": "Third"},
    ]
    assert "apply_translation" in one["next"]


def test_chunking_splits_by_max_chars_in_stable_order(box: Path) -> None:
    from subtitle_mcp.formats import write_srt
    from subtitle_mcp.model import Cue, Subtitle

    cues = [Cue(i * 1000, i * 1000 + 900, "x" * 90) for i in range(10)]
    put(box, "big.srt", write_srt(Subtitle("srt", cues)).encode())
    first = json.loads(S.chunk_for_translation("big.srt", max_chars=300))
    assert first["chunk_count"] == 4  # 3 cues of 90 chars fit in 300; 3+3+3+1
    assert [c["id"] for c in first["cues"]] == ["1", "2", "3"]
    assert "chunk=2" in first["next"]
    last = json.loads(S.chunk_for_translation("big.srt", max_chars=300, chunk=4))
    assert [c["id"] for c in last["cues"]] == ["10"]
    again = json.loads(S.chunk_for_translation("big.srt", max_chars=300))
    assert again == first  # deterministic
    with pytest.raises(SubtitleError, match="chunk must be between 1 and 4"):
        S.chunk_for_translation("big.srt", max_chars=300, chunk=5)
    with pytest.raises(SubtitleError, match="at least 200"):
        S.chunk_for_translation("big.srt", max_chars=50)


def test_apply_translation_preserves_timing_tags_and_vtt_structure(box: Path) -> None:
    put(box, "a.vtt", VTT_COMPLEX)
    mapping = {
        "1": "<v Alice>Sizi dizide hoş geldiniz.</v>\nBu kayıtın ikinci satırı.",
        "2": "<c.yellow>Zaman</c> etiketli <00:00:05.000>bir satır",
        "3": "Bir saat sonra, tanımlayıcı yok.",
    }
    msg = S.apply_translation("a.vtt", mapping)
    assert "Translated 3 of 3 cue(s); timing unchanged." in msg and "No warnings." in msg
    out = text(box / "a.translated.vtt")
    assert out == (
        "WEBVTT - Example captions\nKind: captions\nLanguage: en\n\n"
        "STYLE\n::cue { color: lime }\n\nREGION\nid:top\nwidth:40%\n\n"
        "NOTE This is a comment\nthat spans two lines\n\n"
        "intro\n00:00:01.000 --> 00:00:03.500 align:start position:10%\n"
        "<v Alice>Sizi dizide hoş geldiniz.</v>\nBu kayıtın ikinci satırı.\n\n"
        "2\n00:00:04.000 --> 00:00:06.250 region:top\n"
        "<c.yellow>Zaman</c> etiketli <00:00:05.000>bir satır\n\n"
        "NOTE between cues\n\n"
        "01:00:00.000 --> 01:00:02.000\nBir saat sonra, tanımlayıcı yok.\n"
    )


def test_apply_translation_validates_ids(box: Path) -> None:
    put(box, "a.srt", SIMPLE_SRT)
    with pytest.raises(SubtitleError, match=r"2 of 3 cue\(s\) have no translation: IDs 2, 3"):
        S.apply_translation("a.srt", {"1": "Bir"})
    with pytest.raises(SubtitleError, match=r"1 ID\(s\) that are not cues of this file.*'1'..'3'.*: 7"):
        S.apply_translation("a.srt", {"1": "a", "2": "b", "3": "c", "7": "d"})
    with pytest.raises(SubtitleError, match="keys must be cue IDs"):
        S.apply_translation("a.srt", {"first": "a"})
    assert not (box / "a.translated.srt").exists()


def test_apply_translation_partial_keeps_the_rest(box: Path) -> None:
    put(box, "a.srt", SIMPLE_SRT)
    msg = S.apply_translation("a.srt", {"3": "Üçüncü"}, partial=True)
    assert "Translated 1 of 3 cue(s)" in msg
    out = text(box / "a.translated.srt")
    assert "First line" in out and "Üçüncü" in out and "Third" not in out


def test_apply_translation_warnings(box: Path) -> None:
    src = (
        "1\n00:00:01,000 --> 00:00:05,000\n<i>This is a moderately long sentence.</i>\n\n"
        "2\n00:00:06,000 --> 00:00:07,000\nShort and sweet, really.\n\n"
        "3\n00:00:08,000 --> 00:00:12,000\nAnother perfectly ordinary line.\n"
    )
    put(box, "w.srt", src.encode())
    msg = S.apply_translation(
        "w.srt",
        {
            "1": "Bu uzun bir cümle.",  # lost <i> tags
            "2": "Evet. " * 12,  # much longer, reads too fast
            "3": "",  # empty
        },
    )
    assert "cue 1: formatting tags differ" in msg
    assert "cue 2: length changed a lot (24 -> 72 characters, x3.00)" in msg
    assert "cue 2: the translation reads at" in msg
    assert "cue 3: the translation is empty but the source has text" in msg
    assert "3 warning(s)" not in msg and "4 warning(s)" in msg


# --- safety -----------------------------------------------------------------------------


def test_never_overwrites_unless_asked(box: Path) -> None:
    put(box, "s.srt", SIMPLE_SRT)
    S.shift("s.srt", offset_ms=1000)
    first = (box / "s.shifted.srt").read_bytes()
    with pytest.raises(SubtitleError, match="already exists; pass overwrite=true"):
        S.shift("s.srt", offset_ms=2000)
    assert (box / "s.shifted.srt").read_bytes() == first
    S.shift("s.srt", offset_ms=2000, overwrite=True)
    assert (box / "s.shifted.srt").read_bytes() != first
    with pytest.raises(SubtitleError, match="already exists"):  # output == input counts too
        S.shift("s.srt", offset_ms=1, output="s.srt")
    S.shift("s.srt", offset_ms=1000, output="s.srt", overwrite=True)  # explicit in-place edit
    assert "00:00:02,000 --> 00:00:04,000" in text(box / "s.srt")


def test_no_temp_files_left_behind(box: Path) -> None:
    put(box, "s.srt", SIMPLE_SRT)
    S.shift("s.srt", offset_ms=1000)
    with pytest.raises(SubtitleError):
        S.shift("s.srt", offset_ms=1000)
    assert sorted(p.name for p in box.iterdir()) == ["s.shifted.srt", "s.srt"]


def test_write_is_atomic_when_replace_fails(box: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    put(box, "s.srt", SIMPLE_SRT)
    put(box, "t.srt", b"precious")

    def boom(src: str, dst: object) -> None:
        raise OSError("disk exploded")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disk exploded"):
        S.shift("s.srt", offset_ms=1000, output="t.srt", overwrite=True)
    assert (box / "t.srt").read_bytes() == b"precious"  # destination untouched
    with pytest.raises(OSError, match="disk exploded"):
        S.shift("s.srt", offset_ms=1000)
    assert sorted(p.name for p in box.iterdir()) == ["s.srt", "t.srt"]  # no temp, no empty placeholder


def test_sandbox_blocks_escapes(box: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    outside = tmp_path_factory.mktemp("outside")
    (outside / "secret.srt").write_bytes(SIMPLE_SRT)
    put(box, "s.srt", SIMPLE_SRT)
    with pytest.raises(SubtitleError, match="outside the allowed directories"):
        S.subtitle_info(str(outside / "secret.srt"))
    with pytest.raises(SubtitleError, match="outside the allowed directories"):
        S.subtitle_info("../" + outside.name + "/secret.srt")
    with pytest.raises(SubtitleError, match="outside the allowed directories"):
        S.shift("s.srt", offset_ms=1, output=str(outside / "o.srt"))
    assert not (outside / "o.srt").exists()


def test_sandbox_blocks_symlink_escape(box: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    outside = tmp_path_factory.mktemp("outside2")
    (outside / "secret.srt").write_bytes(SIMPLE_SRT)
    try:
        os.symlink(outside / "secret.srt", box / "link.srt")
    except (OSError, NotImplementedError):
        pytest.skip("this account cannot create symlinks (Windows needs developer mode)")
    with pytest.raises(SubtitleError, match="outside the allowed directories"):
        S.subtitle_info("link.srt")


def test_extension_and_missing_file_checks(box: Path) -> None:
    put(box, "notes.txt", b"hello")
    put(box, "ok.srt", SIMPLE_SRT)
    with pytest.raises(SubtitleError, match="only .srt, .vtt, .ass, .ssa files"):
        S.subtitle_info("notes.txt")
    with pytest.raises(SubtitleError, match="does not exist"):
        S.subtitle_info("nope.srt")
    with pytest.raises(SubtitleError, match="only .srt, .vtt can be written"):
        S.shift("ok.srt", offset_ms=1, output="evil.exe", overwrite=True)
    with pytest.raises(SubtitleError, match="content is SRT; name it with .srt"):
        S.shift("ok.srt", offset_ms=1, output="wrong.vtt")
    with pytest.raises(SubtitleError, match="folder .* does not exist"):
        S.shift("ok.srt", offset_ms=1, output="no/such/dir/o.srt")
    put(box, "empty.srt", b"")
    with pytest.raises(SubtitleError, match="is empty"):
        S.subtitle_info("empty.srt")
    put(box, "prose.srt", b"hello world\nthis is not subtitles\n")
    with pytest.raises(SubtitleError, match="no subtitle cues found"):
        S.subtitle_info("prose.srt")


def test_ass_is_read_only_for_editing_tools(box: Path) -> None:
    put(box, "a.ass", ASS_SAMPLE)
    for call in (
        lambda: S.shift("a.ass", offset_ms=1),
        lambda: S.resync("a.ass", fps_from=25, fps_to=24),
        lambda: S.fix_common_problems("a.ass"),
        lambda: S.chunk_for_translation("a.ass"),
    ):
        with pytest.raises(SubtitleError, match="ASS/SSA, which is read-only here"):
            call()


def test_relative_paths_resolve_inside_the_first_allowed_dir(box: Path) -> None:
    (box / "sub").mkdir()
    put(box / "sub", "x.srt", SIMPLE_SRT)
    assert json.loads(S.subtitle_info("sub/x.srt"))["cue_count"] == 3
    assert json.loads(S.subtitle_info(str(box / "sub" / "x.srt")))["cue_count"] == 3
