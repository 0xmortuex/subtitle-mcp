"""subtitle-mcp: MCP tools for everyday subtitle work."""

from __future__ import annotations

import argparse
import functools
import json
import re
from collections.abc import Callable
from fractions import Fraction
from typing import Any, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import __version__
from .analysis import DEFAULT_MAX_CPS, DEFAULT_MAX_LINE, analyze, overlaps, time_str
from .errors import SubtitleError
from .files import Loaded, Sandbox, default_output, load, save
from .fixes import fix_subtitle
from .formats import convert_model, has_blank_line
from .model import Cue, Subtitle
from .timecode import frames_to_ms, parse_time_arg
from .timeops import LinearMap, apply_map, fit_linear, fps_ratio, residuals
from .translate import apply_mapping, check_ids, make_chunks

mcp = MCPServer(
    "subtitle",
    version=__version__,
    instructions=(
        "Everyday subtitle work on SRT and WebVTT files (ASS/SSA can be read and converted to "
        "SRT/VTT, never written; styling is dropped). Paths are absolute, or relative to the "
        "server's allowed folder. Start with subtitle_info to see the format, detected "
        "encoding, timing statistics and a problem list. Then: read_cues / search_subtitles to "
        "look inside; shift (constant offset, or frames at a frame rate) and resync (two or "
        "more anchor points fix offset AND drift; or fps_from/fps_to) to fix timing; "
        "fix_common_problems (dry run by default) to repair overlaps, bad durations, ordering, "
        "indices, long lines; convert between srt/vtt or to another text encoding; "
        "merge_or_split for multi-part files. To translate: chunk_for_translation hands you "
        "numbered batches; YOU translate them (the server never calls a translation service) and "
        "return everything to apply_translation, which keeps timing and checks that no cue was "
        "lost. No tool overwrites a file unless you pass overwrite=true; edits go to a new file "
        "next to the original by default."
    ),
)


class _State:
    sandbox = Sandbox()


F = TypeVar("F", bound=Callable[..., Any])

# MCPServer only forwards a ToolError's message to the client; anything else arrives as a
# bare "Error executing tool X". These are the failures a caller can act on.
_EXPECTED = (SubtitleError, OSError, re.error)


def tool(fn: F) -> F:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except _EXPECTED as e:
            raise ToolError(str(e)) from e

    mcp.tool()(wrapper)
    return fn


def _load(path: str, encoding: str | None, editable: bool = False, what: str = "") -> Loaded:
    ld = load(_State.sandbox, path, encoding)
    if editable and ld.sub.format == "ass":
        raise SubtitleError(
            f"{ld.path.name} is ASS/SSA, which is read-only here. Run convert(path, "
            f"to_format='srt') first, then use {what or 'this tool'} on the SRT it creates."
        )
    return ld


def _describe_encoding(ld: Loaded) -> str:
    return ld.raw.encoding.name


def _write(
    ld: Loaded, sub: Subtitle, fmt: str, output: str | None, suffix: str, overwrite: bool,
    output_encoding: str,
) -> str:
    out = output or default_output(ld.path, suffix, fmt)
    dest = save(_State.sandbox, sub, fmt, out, overwrite, output_encoding, ld.raw.newline)
    return str(dest)


def _fmt_cue(i: int, c: Cue, fmt: str) -> str:
    head = f"{i:>5}  {time_str(c.start, fmt)} --> {time_str(c.end, fmt)}"
    lines = c.text.split("\n") if c.text else [""]
    return head + "\n" + "\n".join("       " + ln for ln in lines)


def _warn_tail(sub: Subtitle) -> str:
    if not sub.warnings:
        return ""
    shown = sub.warnings[:5]
    more = f" (+{len(sub.warnings) - 5} more, see subtitle_info)" if len(sub.warnings) > 5 else ""
    return "\nParser notes: " + "; ".join(shown) + more


@tool
def subtitle_info(path: str, encoding: str | None = None) -> str:
    """Inspect a subtitle file: format, detected encoding, line endings, cue count, time span,
    overlaps, gaps, longest line, reading speed (characters per second: min/mean/median/p95/max)
    and a list of problems with the cue numbers affected. Read-only. Run this first.

    The encoding is detected (UTF-8/16/32 by byte order mark or structure; Turkish cp1254,
    Western cp1252 and Central European iso-8859-2 by letter evidence) and the reason and
    confidence are reported. If the text looks garbled, pass the right `encoding` and rerun.

    Args:
        path: .srt, .vtt, .ass or .ssa file.
        encoding: Python codec name to force (for example 'cp1254'); default is auto-detect.
    """
    ld = _load(path, encoding)
    info = analyze(ld.sub, ld.raw)
    info["file"] = str(ld.path)
    info["size_bytes"] = ld.raw.size
    return json.dumps(info, indent=2, ensure_ascii=False)


@tool
def read_cues(
    path: str,
    start_cue: int | None = None,
    end_cue: int | None = None,
    from_time: str | None = None,
    to_time: str | None = None,
    limit: int = 100,
    encoding: str | None = None,
) -> str:
    """Show cues, selected either by number or by time. Cue numbers are 1-based positions in
    the file (the same numbers every other tool uses).

    Args:
        path: subtitle file.
        start_cue: first cue number (inclusive). Use with end_cue.
        end_cue: last cue number (inclusive).
        from_time: show cues that overlap [from_time, to_time). Format 00:01:02,500 or 62.5.
        to_time: end of the time window (default: end of file).
        limit: maximum cues to return (default 100, at most 500).
        encoding: force a text encoding.
    """
    ld = _load(path, encoding)
    cues = ld.sub.cues
    fmt = ld.sub.format
    by_num = start_cue is not None or end_cue is not None
    by_time = from_time is not None or to_time is not None
    if by_num and by_time:
        raise SubtitleError("choose either start_cue/end_cue or from_time/to_time, not both")
    if not 1 <= limit <= 500:
        raise SubtitleError("limit must be between 1 and 500")
    chosen: list[tuple[int, Cue]]
    if by_time:
        lo = parse_time_arg(from_time, "from_time") if from_time else 0
        hi = parse_time_arg(to_time, "to_time") if to_time else 1 << 62
        if hi <= lo:
            raise SubtitleError("to_time must be after from_time")
        chosen = [(i, c) for i, c in enumerate(cues, 1) if c.start < hi and max(c.end, c.start + 1) > lo]
    else:
        a = start_cue if start_cue is not None else 1
        b = end_cue if end_cue is not None else len(cues)
        if a > len(cues):
            raise SubtitleError(f"the file has only {len(cues)} cues")
        if a < 1 or b < a:
            raise SubtitleError(f"invalid cue range {a}..{b}; cue numbers start at 1")
        chosen = [(i, cues[i - 1]) for i in range(a, min(b, len(cues)) + 1)]
    shown = chosen[:limit]
    if not shown:
        return f"No cues match (the file has {len(cues)} cues)."
    out = [f"{len(chosen)} matching cue(s) of {len(cues)}; showing {len(shown)}."]
    out += [_fmt_cue(i, c, fmt) for i, c in shown]
    if len(chosen) > len(shown):
        out.append(f"... {len(chosen) - len(shown)} more. Continue with start_cue={shown[-1][0] + 1}.")
    return "\n".join(out)


@tool
def search_subtitles(
    path: str,
    query: str,
    regex: bool = False,
    case_sensitive: bool = False,
    limit: int = 50,
    encoding: str | None = None,
) -> str:
    """Find cues whose text contains a phrase (or matches a regular expression), with cue number
    and timestamps. Matching ignores line breaks inside a cue (they count as a space), so a
    phrase split over two lines is still found. Markup tags are searched too.

    Args:
        path: subtitle file.
        query: text to find, or a Python regular expression when regex=true.
        regex: treat query as a regular expression.
        case_sensitive: default is case-insensitive.
        limit: maximum matches to show (default 50).
        encoding: force a text encoding.
    """
    if not query:
        raise SubtitleError("query is empty")
    ld = _load(path, encoding)
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        pat = re.compile(query if regex else re.escape(query), flags)
    except re.error as e:
        raise SubtitleError(f"invalid regular expression {query!r}: {e}") from e
    hits = [(i, c) for i, c in enumerate(ld.sub.cues, 1) if pat.search(c.text.replace("\n", " "))]
    if not hits:
        return f"No cue matches {query!r} ({len(ld.sub.cues)} cues searched)."
    out = [f"{len(hits)} cue(s) match {query!r}; showing {min(len(hits), limit)}."]
    out += [_fmt_cue(i, c, ld.sub.format) for i, c in hits[:limit]]
    if len(hits) > limit:
        out.append(f"... {len(hits) - limit} more; raise limit or narrow the query.")
    return "\n".join(out)


@tool
def shift(
    path: str,
    offset_ms: int | None = None,
    frames: int | None = None,
    fps: float | None = None,
    from_cue: int | None = None,
    output: str | None = None,
    overwrite: bool = False,
    encoding: str | None = None,
    output_encoding: str = "utf-8",
) -> str:
    """Move cues later (positive) or earlier (negative) by a constant amount, either in
    milliseconds or in frames at a frame rate (frames=24, fps=29.97 is exact: 29.97 means
    30000/1001). Optionally only from a given cue to the end of the file. Fails instead of
    producing a time before 00:00:00. SRT and WebVTT only; writes a new file.

    For a subtitle that is early/late by a different amount at the start and the end, use
    resync instead.

    Args:
        path: .srt or .vtt file.
        offset_ms: milliseconds to add (negative = earlier). Give this OR frames+fps.
        frames: number of frames to add (negative = earlier); needs fps.
        fps: frame rate for frames, e.g. 23.976, 24, 25, 29.97.
        from_cue: 1-based cue number to start shifting from (default: every cue).
        output: where to write (default: <name>.shifted.<ext> next to the input).
        overwrite: allow replacing an existing output file (default false).
        encoding: force the input text encoding.
        output_encoding: encoding of the written file (default utf-8).
    """
    if (offset_ms is None) == (frames is None):
        raise SubtitleError("give exactly one of offset_ms or frames (with fps)")
    if frames is not None:
        if fps is None:
            raise SubtitleError("frames needs fps (for example fps=23.976 or fps=25)")
        delta = frames_to_ms(frames, fps)
        how = f"{frames} frame(s) at {fps:g} fps = {delta} ms"
    else:
        if fps is not None:
            raise SubtitleError("fps only applies together with frames")
        assert offset_ms is not None
        delta = offset_ms
        how = f"{delta} ms"
    ld = _load(path, encoding, editable=True, what="shift")
    sub = ld.sub
    start_pos = 0
    if from_cue is not None:
        if not 1 <= from_cue <= len(sub.cues):
            raise SubtitleError(f"from_cue must be between 1 and {len(sub.cues)}")
        start_pos = from_cue - 1
    before_overlaps = len(overlaps(sub))
    first = sub.cues[start_pos]
    old_first = first.start
    n = apply_map(sub, LinearMap(Fraction(1), Fraction(delta)), start_pos)
    dest = _write(ld, sub, sub.format, output, "shifted", overwrite, output_encoding)
    fmt = sub.format
    msg = [
        f"Shifted {n} cue(s) by {how}"
        + (f" starting at cue {from_cue}" if from_cue else "") + ".",
        f"Cue {start_pos + 1}: {time_str(old_first, fmt)} -> {time_str(first.start, fmt)}.",
        f"Wrote {dest}",
    ]
    after = len(overlaps(sub))
    if after > before_overlaps:
        msg.insert(2, f"Note: this created {after - before_overlaps} new overlap(s) with cues that were not shifted.")
    return "\n".join(msg) + _warn_tail(sub)


def _anchor_point(sub: Subtitle, a: dict[str, Any], k: int) -> tuple[int, int]:
    if "to" not in a or (("cue" in a) == ("at" in a)):
        raise SubtitleError(
            f"anchor {k}: give 'to' (the correct time) and exactly one of 'cue' (cue number whose "
            "start is wrong) or 'at' (the wrong time itself), e.g. {\"cue\": 3, \"to\": \"00:01:05,500\"}"
        )
    if "cue" in a:
        n = int(a["cue"])
        if not 1 <= n <= len(sub.cues):
            raise SubtitleError(f"anchor {k}: cue {n} does not exist (the file has {len(sub.cues)} cues)")
        x = sub.cues[n - 1].start
    else:
        x = parse_time_arg(str(a["at"]), f"anchor {k} 'at'")
    return x, parse_time_arg(str(a["to"]), f"anchor {k} 'to'")


@tool
def resync(
    path: str,
    anchors: list[dict[str, Any]] | None = None,
    fps_from: float | None = None,
    fps_to: float | None = None,
    output: str | None = None,
    overwrite: bool = False,
    encoding: str | None = None,
    output_encoding: str = "utf-8",
) -> str:
    """Fix a subtitle that is out of sync by an amount that CHANGES through the film (drift) as
    well as a constant offset. Give anchor points: a line in the file and the time it should
    really appear. Two anchors (one near the start, one near the end) define the straight line
    new = a*old + b exactly; with three or more a least-squares line is fitted. The tool applies
    it to every start and end time and reports the error left at each anchor.

    Find anchors with search_subtitles / read_cues: take a distinctive line near the start and
    one near the end, play the video to find when each is really spoken.

    Alternatively give fps_from and fps_to (no anchors) when the subtitle was timed for a video at
    one frame rate and your video runs at another (for example fps_from=25, fps_to=23.976).

    Args:
        path: .srt or .vtt file.
        anchors: list of {"cue": 12, "to": "00:03:10,400"} (cue = cue number, its START is
            moved) or {"at": "00:03:05,000", "to": "00:03:10,400"} (at = the current wrong time).
        fps_from: frame rate the subtitle was made for.
        fps_to: frame rate of the video you have.
        output: where to write (default: <name>.resynced.<ext>).
        overwrite: allow replacing an existing output file (default false).
        encoding: force the input text encoding.
        output_encoding: encoding of the written file (default utf-8).
    """
    use_fps = fps_from is not None or fps_to is not None
    if use_fps == bool(anchors):
        raise SubtitleError("give either anchors (two or more) or fps_from together with fps_to, not both or neither")
    ld = _load(path, encoding, editable=True, what="resync")
    sub = ld.sub
    fmt = sub.format
    report: list[str]
    if use_fps:
        if fps_from is None or fps_to is None:
            raise SubtitleError("fps_from and fps_to must be given together")
        m = fps_ratio(fps_from, fps_to)
        report = [f"Frame-rate conversion {fps_from:g} -> {fps_to:g} fps: every time x {m.a} = x {float(m.a):.9f}."]
    else:
        assert anchors is not None
        if len(anchors) < 2:
            raise SubtitleError("resync needs at least two anchors")
        pts = [_anchor_point(sub, a, k) for k, a in enumerate(anchors, 1)]
        m = fit_linear(pts)
        if m.a <= 0:
            raise SubtitleError(
                f"the anchors imply a speed factor of {float(m.a):.6f}, which would reverse or collapse "
                "time. Check that each 'to' time belongs to the same line as its anchor."
            )
        res = residuals(m, pts)
        report = [
            f"Fitted new = {float(m.a):.9f} * old + {float(m.b):.3f} ms  (exact: a = {m.a}, b = {m.b} ms)",
            ("Two anchors define the line exactly." if len(pts) == 2
             else f"Least-squares fit through {len(pts)} anchors."),
            "Error at each anchor after rounding to whole ms (applied - wanted): "
            + ", ".join(f"{r:+d} ms" for r in res)
            + f"; largest {max(abs(r) for r in res)} ms.",
        ]
    old_first, old_last = sub.cues[0].start, sub.cues[-1].end
    n = apply_map(sub, m)
    dest = _write(ld, sub, fmt, output, "resynced", overwrite, output_encoding)
    report += [
        f"Retimed {n} cue(s): first start {time_str(old_first, fmt)} -> {time_str(sub.cues[0].start, fmt)}, "
        f"last end {time_str(old_last, fmt)} -> {time_str(sub.cues[-1].end, fmt)}.",
        f"Wrote {dest}",
    ]
    return "\n".join(report) + _warn_tail(sub)


@tool
def convert(
    path: str,
    to_format: str,
    output: str | None = None,
    overwrite: bool = False,
    encoding: str | None = None,
    output_encoding: str = "utf-8",
) -> str:
    """Convert between SRT and WebVTT, turn an ASS/SSA file into SRT or VTT, or just re-encode
    a file (same format in and out, for example cp1254 Turkish -> UTF-8). Timing and text are
    kept. What cannot be carried over is listed in the answer: ASS styling (fonts, colours,
    positions, effects) is always dropped, WebVTT cue settings and NOTE/STYLE/REGION blocks
    do not exist in SRT, and blank lines inside a cue are removed because both formats end a
    cue at a blank line.

    Args:
        path: .srt, .vtt, .ass or .ssa file.
        to_format: 'srt' or 'vtt'.
        output: where to write (default: <name>.converted.<new ext>).
        overwrite: allow replacing an existing output file (default false).
        encoding: force the input text encoding.
        output_encoding: encoding of the written file: utf-8 (default), utf-8-sig, utf-16,
            cp1254, cp1252, iso-8859-9, ...
    """
    fmt = to_format.lower().lstrip(".")
    if fmt not in ("srt", "vtt"):
        raise SubtitleError("to_format must be 'srt' or 'vtt' (ASS cannot be written)")
    ld = _load(path, encoding)
    new, notes = convert_model(ld.sub, fmt)
    blanks = sum(1 for c in new.cues if has_blank_line(c.text) and c.text.strip())
    if blanks:
        notes.append(f"removed blank lines inside {blanks} cue(s)")
    if ld.sub.format == "ass":
        notes.insert(0, "ASS styling (fonts, colours, positions, effects) was dropped; only timing and text remain")
    dest = _write(ld, new, fmt, output, "converted", overwrite, output_encoding)
    msg = [
        f"Converted {ld.sub.format.upper()} ({_describe_encoding(ld)}) -> {fmt.upper()} ({output_encoding}): "
        f"{len(new.cues)} cue(s).",
        f"Wrote {dest}",
    ]
    if notes:
        msg.append("Not carried over / changed: " + "; ".join(notes) + ".")
    return "\n".join(msg) + _warn_tail(ld.sub)


@tool
def fix_common_problems(
    path: str,
    apply: bool = False,
    strip_tags: bool = False,
    max_line_length: int = DEFAULT_MAX_LINE,
    max_cps: float = DEFAULT_MAX_CPS,
    output: str | None = None,
    overwrite: bool = False,
    encoding: str | None = None,
    output_encoding: str = "utf-8",
) -> str:
    """Repair the usual damage in SRT/WebVTT files. DRY RUN BY DEFAULT: it lists every fix it
    would make (with cue numbers and before/after times) and writes nothing until you call
    again with apply=true.

    Fixes: out-of-order cues are re-sorted; cues with end <= start get a readable duration
    (never past the next cue); overlapping cues are trimmed to the next cue's start; empty cues
    and blank lines inside cues are removed; missing/duplicate/wrong SRT index numbers are
    renumbered; lines longer than max_line_length are re-wrapped (balanced; dialogue dashes keep
    one speaker per line); the BOM and mixed CR/LF line endings are normalised. With
    strip_tags=true, HTML/font/WebVTT/ASS tags are removed from the text.

    Flagged but never changed: reading-speed outliers (more than max_cps characters per
    second) and cues on screen for over 10 seconds; overlapping cues that start together.

    Args:
        path: .srt or .vtt file.
        apply: false (default) = report only; true = write the repaired file.
        strip_tags: also remove <i>, <b>, <font ...>, <v ...>, {\\an8} etc. (default false).
        max_line_length: wrap lines longer than this many visible characters (default 42).
        max_cps: reading-speed flag threshold in characters per second (default 25).
        output: where to write when apply=true (default: <name>.fixed.<ext>).
        overwrite: allow replacing an existing output file (default false).
        encoding: force the input text encoding.
        output_encoding: encoding of the written file (default utf-8).
    """
    if max_line_length < 10:
        raise SubtitleError("max_line_length must be at least 10")
    ld = _load(path, encoding, editable=True, what="fix_common_problems")
    fixed, rep = fix_subtitle(ld.sub, ld.raw, strip_tags, max_line_length, max_cps)
    out: list[str] = []
    if not rep.fixes:
        out.append(f"{ld.path.name}: nothing to fix.")
    else:
        verb = "Applied" if apply else "Would apply"
        out.append(f"{verb} {len(rep.fixes)} kind(s) of fix to {ld.path.name} ({len(ld.sub.cues)} cues -> {len(fixed.cues)}):")
        for f in rep.fixes:
            out.append(f"  - [{f.kind}] {f.summary}")
            out += [f"        {d}" for d in f.details[:8]]
            if len(f.details) > 8:
                out.append(f"        ... and {len(f.details) - 8} more")
    if rep.flagged:
        out.append("Flagged, not changed:")
        for f in rep.flagged:
            out.append(f"  - [{f.kind}] {f.summary}")
            out += [f"        {d}" for d in f.details[:8]]
            if len(f.details) > 8:
                out.append(f"        ... and {len(f.details) - 8} more")
    if apply:
        dest = _write(ld, fixed, fixed.format, output, "fixed", overwrite, output_encoding)
        out.append(f"Wrote {dest}")
        if rep.remaining:
            out.append("Still present after fixing: " + ", ".join(rep.remaining))
    else:
        target = output or default_output(ld.path, "fixed", fixed.format)
        out.append(f"Dry run: nothing written. Call again with apply=true to write {target}.")
    return "\n".join(out) + _warn_tail(ld.sub)


@tool
def merge_or_split(
    path: str,
    mode: str,
    path2: str | None = None,
    offset_ms: int | None = None,
    at: str | None = None,
    output: str | None = None,
    output2: str | None = None,
    overwrite: bool = False,
    encoding: str | None = None,
    output_encoding: str = "utf-8",
) -> str:
    """Join two subtitle files or cut one in two. SRT and WebVTT only.

    mode='merge': the cues of path2 are moved by offset_ms and combined with those of path
    (result sorted by start time, in the format of the first file). If offset_ms is omitted, the
    second file is placed right after the last cue of the first (typical for CD1 + CD2 rips).
    Use offset_ms=0 to overlay two files, such as two languages.

    mode='split': cut at time `at`. Part 1 holds everything that starts before `at`; part 2
    holds everything from `at` on, re-zeroed so `at` becomes 00:00:00. A cue that straddles the cut
    appears in both parts (clipped to the cut in part 1).

    Args:
        path: first (or only) file.
        mode: 'merge' or 'split'.
        path2: second file (merge only).
        offset_ms: milliseconds added to every cue of path2 (merge only; may be negative).
        at: split time such as 00:45:10,000 (split only).
        output: merged file / first part (default <name>.merged.<ext> or <name>.part1.<ext>).
        output2: second part (split only, default <name>.part2.<ext>).
        overwrite: allow replacing existing output files (default false).
        encoding: force the input text encoding (applies to both inputs).
        output_encoding: encoding of the written files (default utf-8).
    """
    if mode not in ("merge", "split"):
        raise SubtitleError("mode must be 'merge' or 'split'")
    ld = _load(path, encoding, editable=True, what="merge_or_split")
    a = ld.sub
    fmt = a.format
    if mode == "merge":
        if path2 is None:
            raise SubtitleError("merge needs path2")
        if at is not None or output2 is not None:
            raise SubtitleError("at and output2 only apply to mode='split'")
        ld2 = _load(path2, encoding, editable=True, what="merge_or_split")
        b, notes = convert_model(ld2.sub, fmt)
        if offset_ms is None:
            offset = max((c.end for c in a.cues), default=0)
            how = f"placed after the last cue of the first file (offset {offset} ms)"
        else:
            offset = offset_ms
            how = f"offset {offset} ms"
        bshift = Subtitle(fmt, [c.copy() for c in b.cues])
        apply_map(bshift, LinearMap(Fraction(1), Fraction(offset)))
        merged = Subtitle(fmt, [c.copy() for c in a.cues] + bshift.cues, a.header, list(a.extras))
        merged.cues.sort(key=lambda c: (c.start, c.end))  # stable
        dest = _write(ld, merged, fmt, output, "merged", overwrite, output_encoding)
        msg = [f"Merged {len(a.cues)} + {len(b.cues)} = {len(merged.cues)} cue(s); second file {how}.", f"Wrote {dest}"]
        if ld2.sub.extras:
            notes.append(f"{len(ld2.sub.extras)} NOTE/STYLE/REGION block(s) of the second file were not carried over")
        if notes:
            msg.append("Not carried over: " + "; ".join(notes) + ".")
        return "\n".join(msg) + _warn_tail(a)
    # split
    if at is None:
        raise SubtitleError("split needs at, for example at='00:45:10,000'")
    if path2 is not None or offset_ms is not None:
        raise SubtitleError("path2 and offset_ms only apply to mode='merge'")
    cut = parse_time_arg(at, "at")
    idx1 = [i for i, c in enumerate(a.cues) if c.start < cut]
    idx2 = [i for i, c in enumerate(a.cues) if c.start >= cut or c.end > cut]
    if not idx1 or not idx2:
        raise SubtitleError(
            f"splitting at {at} leaves one part empty (cues run from "
            f"{time_str(min(c.start for c in a.cues), fmt)} to {time_str(max(c.end for c in a.cues), fmt)})"
        )
    p1 = Subtitle(fmt, [], a.header)
    for i in idx1:
        c = a.cues[i].copy()
        c.end = min(c.end, cut)
        p1.cues.append(c)
    p2 = Subtitle(fmt, [], a.header)
    for i in idx2:
        c = a.cues[i].copy()
        c.start = max(c.start, cut) - cut
        c.end = c.end - cut
        p2.cues.append(c)
    first2 = idx2[0]
    for pos, text in a.extras:
        if pos == 0:
            p1.extras.append((0, text))
            p2.extras.append((0, text))
        elif pos < first2:
            p1.extras.append((pos, text))
        else:
            p2.extras.append((sum(1 for k in idx2 if k < pos), text))
    straddle = [i + 1 for i in idx1 if a.cues[i].end > cut]
    d1 = _write(ld, p1, fmt, output, "part1", overwrite, output_encoding)
    d2 = _write(ld, p2, fmt, output2, "part2", overwrite, output_encoding)
    msg = [
        f"Split at {time_str(cut, fmt)}: part 1 has {len(p1.cues)} cue(s), part 2 has {len(p2.cues)} cue(s) (re-zeroed).",
        f"Wrote {d1}",
        f"Wrote {d2}",
    ]
    if straddle:
        msg.append(f"{len(straddle)} cue(s) straddle the cut and appear in both parts: {straddle[:10]}")
    return "\n".join(msg) + _warn_tail(a)


@tool
def chunk_for_translation(
    path: str, max_chars: int = 1500, chunk: int = 1, encoding: str | None = None
) -> str:
    """Step 1 of translating a subtitle. Returns one batch of cues as JSON: each cue has a stable
    `id` (its 1-based position in the file) and its `text` (line breaks as \\n, tags included).
    The server never translates: YOU translate the texts, then call apply_translation with a
    mapping {"<id>": "<translated text>"} covering every id of the file.

    Rules for the translation: keep every id; keep formatting tags such as <i> and {\\an8}
    exactly; keep line breaks (\\n) where they make sense; keep it about as long as the original
    (subtitles are read against a clock). The response says how many chunks exist; call again with
    chunk=2, 3, ... until all are done, then send ONE mapping with all ids.

    Args:
        path: .srt or .vtt file.
        max_chars: approximate size of a batch in characters of cue text (default 1500, min 200).
        chunk: which batch to return, starting at 1.
        encoding: force the input text encoding.
    """
    ld = _load(path, encoding, editable=True, what="chunk_for_translation")
    chunks = make_chunks(ld.sub, max_chars)
    if not chunks:
        raise SubtitleError("the file has no cues to translate")
    if not 1 <= chunk <= len(chunks):
        raise SubtitleError(f"chunk must be between 1 and {len(chunks)} (this file has {len(chunks)} chunks at max_chars={max_chars})")
    doc: dict[str, Any] = {
        "file": str(ld.path),
        "format": ld.sub.format,
        "total_cues": len(ld.sub.cues),
        "chunk": chunk,
        "chunk_count": len(chunks),
        "cues": [{"id": i, "text": t} for i, t in chunks[chunk - 1]],
        "next": (f"call chunk_for_translation again with chunk={chunk + 1}" if chunk < len(chunks)
                 else "this is the last chunk: now call apply_translation with the translations of ALL chunks"),
    }
    return json.dumps(doc, indent=1, ensure_ascii=False)


@tool
def apply_translation(
    path: str,
    mapping: dict[str, str],
    output: str | None = None,
    overwrite: bool = False,
    partial: bool = False,
    encoding: str | None = None,
    output_encoding: str = "utf-8",
) -> str:
    """Step 2 of translating a subtitle: write the translated file. Timing, cue order, WebVTT
    identifiers/settings/notes are kept exactly; only the text changes. Every id of the file
    must be present in `mapping` (otherwise the call fails and lists the missing ids; partial=true
    keeps untranslated cues as they are). Warnings are reported for translations that are
    empty, much longer or shorter than the source, that lose or change formatting tags, or that
    would read too fast.

    Args:
        path: the SAME file given to chunk_for_translation.
        mapping: {"1": "translated text", "2": "..."}; keys are the ids from chunk_for_translation.
        output: where to write (default: <name>.translated.<ext>).
        overwrite: allow replacing an existing output file (default false).
        partial: allow a mapping that does not cover every cue.
        encoding: force the input text encoding.
        output_encoding: encoding of the written file (default utf-8; use utf-8 for non-Latin
            target languages).
    """
    ld = _load(path, encoding, editable=True, what="apply_translation")
    check_ids(mapping)
    sub = Subtitle(ld.sub.format, [c.copy() for c in ld.sub.cues], ld.sub.header, list(ld.sub.extras))
    replaced, warnings = apply_mapping(sub, mapping, partial)
    dest = _write(ld, sub, sub.format, output, "translated", overwrite, output_encoding)
    msg = [f"Translated {replaced} of {len(sub.cues)} cue(s); timing unchanged.", f"Wrote {dest}"]
    if warnings:
        msg.append(f"{len(warnings)} warning(s):")
        msg += [f"  - {w}" for w in warnings[:40]]
        if len(warnings) > 40:
            msg.append(f"  ... and {len(warnings) - 40} more")
    else:
        msg.append("No warnings.")
    return "\n".join(msg)


def main() -> None:
    parser = argparse.ArgumentParser(prog="subtitle-mcp", description="MCP server for subtitle files (stdio).")
    parser.add_argument(
        "--dir", action="append", default=[], metavar="DIR",
        help="restrict all reading and writing to this directory (repeatable). "
        "Relative paths are resolved against the first one. Without --dir any path is allowed.",
    )
    parser.add_argument("--version", action="version", version=f"subtitle-mcp {__version__}")
    args = parser.parse_args()
    try:
        _State.sandbox = Sandbox(args.dir)
    except SubtitleError as e:
        parser.error(str(e))
    mcp.run()


if __name__ == "__main__":
    main()
