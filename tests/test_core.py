"""Parsers, writers, time arithmetic, encodings and resync math, against messy
real-world-like inputs with exact expected outputs."""

from __future__ import annotations

import random
from fractions import Fraction

import pytest
from fixtures import (
    ASS_SAMPLE,
    CYRILLIC_CP1251,
    FRENCH_CP1252,
    MESSY_SRT,
    POLISH_ISO8859_2,
    POLISH_TEXT_SRT,
    SIMPLE_SRT,
    TURKISH_CP1254,
    TURKISH_TEXT_SRT,
    UTF16_SRT,
    VTT_COMPLEX,
)

from subtitle_mcp.encoding import decode, detect_encoding, encode
from subtitle_mcp.errors import SubtitleError
from subtitle_mcp.formats import (
    convert_model,
    parse_subtitle,
    render,
    sniff_format,
    write_srt,
    write_vtt,
)
from subtitle_mcp.model import Cue, Subtitle
from subtitle_mcp.timecode import (
    fps_fraction,
    format_time,
    frames_to_ms,
    parse_time_arg,
    round_half_up,
)
from subtitle_mcp.timeops import LinearMap, apply_map, fit_linear, fps_ratio, residuals


def parse_bytes(data: bytes) -> Subtitle:
    text, _ = decode(data, None)
    return parse_subtitle(text)


# --- time -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, ms",
    [
        ("00:01:02,500", 62_500),
        ("00:01:02.500", 62_500),
        ("1:02.5", 62_500),  # MM:SS.f, one fraction digit is tenths
        ("62.5", 62_500),  # plain seconds
        ("100:00:00,000", 360_000_000),  # hour > 99
        ("00:00:00.0005", 1),  # 0.5 ms rounds half up
        ("00:00:00.0004", 0),
        ("00:00:00,9996", 1000),  # rounding carries into the seconds
        ("0:00:01.50", 1500),  # ASS centiseconds
    ],
)
def test_parse_time_arg(text: str, ms: int) -> None:
    assert parse_time_arg(text) == ms


def test_parse_time_rejects_garbage() -> None:
    with pytest.raises(SubtitleError, match="cannot read time"):
        parse_time_arg("soon")


def test_format_time_and_hour_past_99() -> None:
    assert format_time(0) == "00:00:00,000"
    assert format_time(360_002_000) == "100:00:02,000"
    assert format_time(3_600_000, ".") == "01:00:00.000"
    with pytest.raises(SubtitleError, match="before 00:00:00"):
        format_time(-1)


def test_frame_arithmetic_is_exact() -> None:
    assert fps_fraction(29.97) == Fraction(30000, 1001)
    assert fps_fraction(23.976) == Fraction(24000, 1001)
    assert fps_fraction(25) == Fraction(25)
    assert frames_to_ms(30000, 29.97) == 1_001_000  # exactly 1001 s, no float drift
    assert frames_to_ms(24, 23.976) == 1001
    assert frames_to_ms(24, 29.97) == 801  # 800.8
    assert frames_to_ms(-24, 29.97) == -801  # symmetric rounding
    assert frames_to_ms(1, 25) == 40
    with pytest.raises(SubtitleError):
        fps_fraction(0)


def test_round_half_up() -> None:
    assert [round_half_up(Fraction(n, 2)) for n in (-3, -1, 1, 3)] == [-1, 0, 1, 2]


# --- parsing messy files --------------------------------------------------------------


def test_messy_srt_parses_exactly() -> None:
    sub = parse_bytes(MESSY_SRT)
    assert sub.format == "srt"
    got = [(c.start, c.end, c.text, c.index) for c in sub.cues]
    assert got == [
        (1000, 3000, "Hello there.", 1),
        (3500, 3500, "Zero duration cue", 2),
        (2000, 4000, "<i>Out of order and duplicate index</i>", 2),
        (6000, 9000, "Index separated from timing\n\nby a blank line inside the cue.", 5),
        (10000, 12500, "No index and dot milliseconds", None),
        (12000, 14000,
         '<font color="#ff0000">This is a very long line that definitely needs rewrapping '
         "because it is far too long</font>", 7),
        (14000, 14500, "Way too fast for anyone to read this sentence completely", 8),
        (360_000_000, 360_002_000, "Hour past ninety-nine", 9),
    ]
    assert sub.warnings == [
        "cue 4: a blank line inside the cue text was kept in the text "
        "(run fix_common_problems to remove it)"
    ]


def test_srt_cues_glued_together_without_blank_line() -> None:
    text = "1\n00:00:01,000 --> 00:00:02,000\nA\n2\n00:00:03,000 --> 00:00:04,000\nB\n"
    sub = parse_subtitle(text)
    assert [(c.index, c.text) for c in sub.cues] == [(1, "A"), (2, "B")]
    assert any("no blank line" in w for w in sub.warnings)


def test_srt_variants_arrow_spacing_and_coordinates() -> None:
    text = "1\n00:00:01,000->00:00:02,000  X1:10 X2:20 Y1:5 Y2:9\nHi\n"
    sub = parse_subtitle(text)
    assert sub.cues[0].settings == "X1:10 X2:20 Y1:5 Y2:9"
    assert (sub.cues[0].start, sub.cues[0].end) == (1000, 2000)


def test_srt_unicode_line_separator_is_text_not_a_newline() -> None:
    text = "1\n00:00:01,000 --> 00:00:02,000\nA B\n"
    assert parse_subtitle(text).cues[0].text == "A B"


def test_vtt_complex_parses_exactly() -> None:
    sub = parse_bytes(VTT_COMPLEX)
    assert sub.format == "vtt"
    assert sub.header == "WEBVTT - Example captions\nKind: captions\nLanguage: en"
    assert sub.extras == [
        (0, "STYLE\n::cue { color: lime }"),
        (0, "REGION\nid:top\nwidth:40%"),
        (0, "NOTE This is a comment\nthat spans two lines"),
        (2, "NOTE between cues"),
    ]
    assert [(c.ident, c.start, c.end, c.settings, c.text) for c in sub.cues] == [
        ("intro", 1000, 3500, "align:start position:10%",
         "<v Alice>Welcome to the show.</v>\nSecond line of the cue."),
        ("2", 4000, 6250, "region:top", "<c.yellow>Cue with</c> a <00:00:05.000>timestamp tag"),
        (None, 3_600_000, 3_602_000, "", "An hour in, no identifier."),
    ]


def test_vtt_write_preserves_everything() -> None:
    sub = parse_bytes(VTT_COMPLEX)
    assert write_vtt(sub) == (
        "WEBVTT - Example captions\nKind: captions\nLanguage: en\n\n"
        "STYLE\n::cue { color: lime }\n\n"
        "REGION\nid:top\nwidth:40%\n\n"
        "NOTE This is a comment\nthat spans two lines\n\n"
        "intro\n00:00:01.000 --> 00:00:03.500 align:start position:10%\n"
        "<v Alice>Welcome to the show.</v>\nSecond line of the cue.\n\n"
        "2\n00:00:04.000 --> 00:00:06.250 region:top\n"
        "<c.yellow>Cue with</c> a <00:00:05.000>timestamp tag\n\n"
        "NOTE between cues\n\n"
        "01:00:00.000 --> 01:00:02.000\nAn hour in, no identifier.\n"
    )


def test_vtt_to_srt_exact_and_reports_losses() -> None:
    sub = parse_bytes(VTT_COMPLEX)
    out, notes = convert_model(sub, "srt")
    assert write_srt(out) == (
        "1\n00:00:01,000 --> 00:00:03,500\n<v Alice>Welcome to the show.</v>\nSecond line of the cue.\n\n"
        "2\n00:00:04,000 --> 00:00:06,250\n<c.yellow>Cue with</c> a <00:00:05.000>timestamp tag\n\n"
        "3\n01:00:00,000 --> 01:00:02,000\nAn hour in, no identifier.\n"
    )
    assert "dropped WebVTT cue settings on 2 cue(s)" in notes
    assert "dropped 4 WebVTT NOTE/STYLE/REGION block(s)" in notes


def test_srt_to_vtt_exact() -> None:
    out, notes = convert_model(parse_bytes(SIMPLE_SRT), "vtt")
    assert notes == []
    assert write_vtt(out) == (
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:03.000\nFirst line\n\n"
        "00:00:04.000 --> 00:00:06.000\nSecond line\nwith two rows\n\n"
        "00:00:10.000 --> 00:00:12.000\nThird\n"
    )


def test_ass_reads_text_and_timing_only() -> None:
    sub = parse_bytes(ASS_SAMPLE)
    assert sub.format == "ass"
    assert [(c.start, c.end, c.text) for c in sub.cues] == [
        (1250, 3000, "Line one\nLine two"),
        (5500, 7000, "Second in time, first in file, with comma"),
    ]
    joined = " | ".join(sub.warnings)
    assert "styles, fonts, colours, positions" in joined
    assert "1 Comment: line(s) ignored" in joined
    assert "1 vector drawing cue(s) skipped" in joined
    out, _ = convert_model(sub, "srt")
    assert write_srt(out) == (
        "1\n00:00:01,250 --> 00:00:03,000\nLine one\nLine two\n\n"
        "2\n00:00:05,500 --> 00:00:07,000\nSecond in time, first in file, with comma\n"
    )


def test_ass_cannot_be_written() -> None:
    with pytest.raises(SubtitleError, match="only srt and vtt"):
        render(parse_bytes(ASS_SAMPLE), "ass")


def test_sniff_and_errors() -> None:
    assert sniff_format("﻿WEBVTT\n\n") == "vtt"
    assert sniff_format("1\n00:00:01,000 --> 00:00:02,000\nx") == "srt"
    with pytest.raises(SubtitleError, match="no subtitle cues found"):
        sniff_format("just some prose\n")
    with pytest.raises(SubtitleError, match="empty"):
        sniff_format("\n  \n")


def test_blank_lines_in_cue_are_dropped_on_write() -> None:
    sub = parse_bytes(MESSY_SRT)
    text = write_srt(sub)
    assert "Index separated from timing\nby a blank line inside the cue.\n" in text
    assert parse_subtitle(text).cues[3].text == "Index separated from timing\nby a blank line inside the cue."


# --- round-trip property tests -------------------------------------------------------

_WORDS = [
    "hello", "world", "42", "it's", "café", "şimdi", "ğüç", "İstanbul",
    "<i>tag</i>", "{\\an8}top", "x:y", "100%", "\U0001f600", "a b", "--", ">", "NOTE", "WEBVTT",
]
_IDENTS = ["intro", "12", "cue-3", "ñ", "a b"]
_SETTINGS = ["", "align:start", "line:0 position:10% align:center"]
_EXTRAS = ["NOTE hi there", "STYLE\n::cue { color: red }", "REGION\nid:r1\nwidth:30%", "NOTE a\nb"]


def _line(rng: random.Random) -> str:
    line = " ".join(rng.choice(_WORDS) for _ in range(rng.randint(1, 5)))
    if rng.random() < 0.2:
        line = "  " + line
    if rng.random() < 0.1:
        line += " "
    return line


def _random_subtitle(rng: random.Random, fmt: str) -> Subtitle:
    n = rng.choice([0, 1, 1, 2, 3, 5, 8])
    cues = []
    for _ in range(n):
        start = rng.choice([0, rng.randint(0, 5_000_000), rng.randint(0, 400_000_000)])
        end = start + rng.choice([0, 1, 999, rng.randint(0, 20_000)])
        text = "\n".join(_line(rng) for _ in range(rng.choice([0, 1, 1, 2, 3])))
        cue = Cue(start, end, text)
        if fmt == "vtt":
            if rng.random() < 0.4:
                cue.ident = rng.choice(_IDENTS)
            cue.settings = rng.choice(_SETTINGS)
        else:
            cue.settings = rng.choice(["", "", "X1:1 X2:2 Y1:3 Y2:4"])
        cues.append(cue)
    sub = Subtitle(fmt, cues)
    if fmt == "vtt":
        sub.header = rng.choice(["WEBVTT", "WEBVTT - title\nKind: captions"])
        sub.extras = sorted(
            ((rng.randint(0, n), rng.choice(_EXTRAS)) for _ in range(rng.choice([0, 0, 1, 3]))),
            key=lambda e: e[0],
        )
    return sub


@pytest.mark.parametrize("fmt", ["srt", "vtt"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_roundtrip_parse_write_is_identity(fmt: str, newline: str) -> None:
    rng = random.Random(f"{fmt}{newline!r}")
    for case in range(600):  # 2 newline styles x 600 x 2 formats = 2400 generated subtitles
        sub = _random_subtitle(rng, fmt)
        text = render(sub, fmt, newline)
        again = parse_subtitle(text, fmt)
        assert again == sub, f"case {case}:\n{text!r}"
        assert render(again, fmt, newline) == text


def test_roundtrip_through_bytes_and_other_format() -> None:
    rng = random.Random(7)
    for _ in range(300):
        sub = _random_subtitle(rng, "srt")
        for enc in ("utf-8", "utf-8-sig", "utf-16"):
            back, _ = decode(encode(write_srt(sub), enc), None)
            assert parse_subtitle(back, "srt") == sub
        via_vtt, _ = convert_model(sub, "vtt")
        back_srt, _ = convert_model(parse_subtitle(write_vtt(via_vtt), "vtt"), "srt")
        assert [(c.start, c.end, c.text) for c in back_srt.cues] == [
            (c.start, c.end, c.text) for c in sub.cues
        ]


# --- encodings -----------------------------------------------------------------------


def test_detect_turkish_cp1254_and_decode_exactly() -> None:
    g = detect_encoding(TURKISH_CP1254)
    assert g.name == "cp1254" and g.confidence == "high"
    text, _ = decode(TURKISH_CP1254, None)
    assert text == TURKISH_TEXT_SRT
    assert "İstanbul" in text and "ışık" in text


def test_cp1252_would_misread_turkish_and_override_exists() -> None:
    wrong, _ = decode(TURKISH_CP1254, "cp1252")
    assert wrong != TURKISH_TEXT_SRT  # the explicit override is honoured, even when wrong
    right, g = decode(TURKISH_CP1254, "cp1254")
    assert right == TURKISH_TEXT_SRT and g.confidence == "user-specified"


def test_detect_other_encodings() -> None:
    assert detect_encoding(SIMPLE_SRT).name == "utf-8"
    assert detect_encoding(SIMPLE_SRT).confidence == "certain"
    assert detect_encoding(MESSY_SRT).name == "utf-8-sig"
    assert detect_encoding(UTF16_SRT).name == "utf-16"
    assert detect_encoding(FRENCH_CP1252).name == "cp1252"
    assert decode(FRENCH_CP1252, None)[0].count("’") == 1
    pl = detect_encoding(POLISH_ISO8859_2)
    assert pl.name == "iso-8859-2"
    assert decode(POLISH_ISO8859_2, None)[0] == POLISH_TEXT_SRT
    bomless16 = TURKISH_TEXT_SRT.encode("utf-16-le")
    assert detect_encoding(bomless16).name == "utf-16-le"
    assert decode(bomless16, None)[0] == TURKISH_TEXT_SRT
    assert detect_encoding(TURKISH_TEXT_SRT.encode("utf-8")).name == "utf-8"


def test_refuses_to_guess_cyrillic() -> None:
    with pytest.raises(SubtitleError, match="non-Latin legacy encoding"):
        detect_encoding(CYRILLIC_CP1251)
    text, _ = decode(CYRILLIC_CP1251, "cp1251")
    assert "Привет" in text


def test_bad_override_messages() -> None:
    with pytest.raises(SubtitleError, match="unknown encoding"):
        decode(b"abc", "klingon")
    with pytest.raises(SubtitleError, match="cannot decode the file as utf-8"):
        decode(TURKISH_CP1254, "utf-8")
    with pytest.raises(SubtitleError, match="cannot be written as cp1252"):
        encode("ı", "cp1252")


# --- resync math ---------------------------------------------------------------------


def test_two_anchor_fit_is_exact_rational() -> None:
    # truth: new = 1.001 * old + 2500 ms
    m = fit_linear([(1_000_000, 1_003_500), (5_000_000, 5_007_500)])
    assert m.a == Fraction(1001, 1000)
    assert m.b == Fraction(2500)
    assert m.apply(3_000_000) == 3_005_500
    assert residuals(m, [(1_000_000, 1_003_500), (5_000_000, 5_007_500)]) == [0, 0]


def test_fit_with_fractional_slope_rounds_half_up_once() -> None:
    m = fit_linear([(0, 0), (2, 3)])
    assert (m.a, m.b) == (Fraction(3, 2), Fraction(0))
    assert m.apply(1) == 2  # 1.5 -> 2
    assert m.apply(3) == 5  # 4.5 -> 5
    assert m.exact(1) == Fraction(3, 2)


def test_least_squares_fit_hand_checked() -> None:
    # n=4, sx=6, sy=17, sxx=14, sxy=37 -> a = 46/20, b = 4/5
    pts = [(0, 1), (1, 3), (2, 5), (3, 8)]
    m = fit_linear(pts)
    assert m.a == Fraction(23, 10) and m.b == Fraction(4, 5)
    assert residuals(m, pts) == [0, 0, 0, 0]  # after rounding to whole ms


def test_least_squares_leaves_visible_residual_for_inconsistent_anchors() -> None:
    pts = [(0, 0), (10_000, 10_000), (20_000, 20_100)]  # last anchor disagrees by 100 ms
    m = fit_linear(pts)
    res = residuals(m, pts)
    # exact: a = 201/200, b = -50/3 ms; applied - wanted = -16.67, +33.33, -16.67 -> rounded
    assert m.a == Fraction(201, 200) and m.b == Fraction(-50, 3)
    assert res == [-17, 33, -17]


def test_fit_rejects_degenerate_anchors() -> None:
    with pytest.raises(SubtitleError, match="same current time"):
        fit_linear([(5, 7), (5, 9)])
    with pytest.raises(SubtitleError, match="at least two"):
        fit_linear([(5, 7)])


def test_fps_conversion_factor_exact() -> None:
    m = fps_ratio(25, 23.976)
    assert m.a == Fraction(25) / Fraction(24000, 1001) == Fraction(25025, 24000)
    assert m.apply(24_000) == 25_025  # 24 s of PAL timing is 25.025 s on a 23.976 video


def test_apply_map_refuses_negative_and_changes_nothing() -> None:
    sub = parse_bytes(SIMPLE_SRT)
    with pytest.raises(SubtitleError, match="cue 1 would start at -1000 ms"):
        apply_map(sub, LinearMap(Fraction(1), Fraction(-2000)))
    assert sub.cues[0].start == 1000  # untouched after the failure
    apply_map(sub, LinearMap(Fraction(1), Fraction(-500)), from_index=1)
    assert [c.start for c in sub.cues] == [1000, 3500, 9500]
