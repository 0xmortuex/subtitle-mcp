# subtitle-mcp

**Everyday subtitle work for your AI agent: look inside, fix timing, repair, convert and translate SRT, WebVTT and ASS files. Offline, no API keys, nothing to install beyond `mcp`.**

<!-- mcp-name: io.github.0xmortuex/subtitle-mcp -->

What is different about it: the other subtitle MCP servers I found
([mcp-subtitle](https://github.com/kevinten-ai/mcp-subtitle) for AI transcription and
Google-Cloud translation, [tvsub-mcp](https://github.com/launzi/tvsub-mcp) for a macOS Apple TV
overlay) are built around calling a transcription or translation service. subtitle-mcp is the
opposite: it never calls anything. It is the **toolbox for the files themselves**, with exact
arithmetic (a resync is an exact least-squares line over fractions, 29.97 fps really is 30000/1001),
a repair tool that shows every change as a dry run first, an explicit encoding heuristic that is
honest about what it cannot tell (Turkish cp1254 works), and a **translation contract** where
*your agent* is the translator and the server guarantees nothing is lost on the way back in.

## What it looks like

Real output on a deliberately damaged file (BOM, mixed CRLF/LF, duplicate and missing indices, a
blank line inside a cue, an unreadably fast cue, one cue past hour 99):

```
> fix_common_problems messy.srt
Would apply 8 kind(s) of fix to messy.srt (8 cues -> 8):
  - [bom] byte order mark will not be written (output is UTF-8 without BOM by default)
  - [mixed_newlines] all line endings normalised to LF (the most common one in the file)
  - [blank_line_in_cue] removed blank lines inside 1 cue(s)
        cue 4
  - [out_of_order] re-sorted cues by start time (1 cue(s) were out of order)
        cue 3 started before the cue above it
  - [bad_duration] gave 1 zero/negative-length cue(s) a readable duration (1-7 s at 15 chars/s, never past the next cue)
        cue 2: end 00:00:03,500 -> 00:00:04,633
  - [overlap] trimmed 3 cue(s) that overlapped the next one
        cue 1: end 00:00:03,000 -> 00:00:02,000 (cue 3 starts)
        ...
  - [long_line] re-wrapped 2 cue(s) to at most 42 characters per line
  - [renumber] cues renumbered 1..N (missing, duplicate indices)
Flagged, not changed:
  - [reading_speed] 2 cue(s) read faster than 25 chars/s (not changed; shorten the text or extend the timing)
        cue 7: 110.0 chars/s
Dry run: nothing written. Call again with apply=true to write messy.fixed.srt.
```

A subtitle that drifts (1.001x too slow) and starts 2.5 s late, fixed with two anchors:

```
> resync bad.srt anchors=[{"cue": 1, "to": "00:00:12,510"}, {"cue": 4, "to": "01:40:08,500"}]
Fitted new = 1.001000000 * old + 2500.000 ms  (exact: a = 1001/1000, b = 2500 ms)
Two anchors define the line exactly.
Error at each anchor after rounding to whole ms (applied - wanted): +0 ms, +0 ms; largest 0 ms.
Retimed 4 cue(s): first start 00:00:10,000 -> 00:00:12,510, last end 01:40:01,000 -> 01:40:09,501.
```

## Install

Needs [uv](https://docs.astral.sh/uv/). Point `--dir` at the folder with your videos and subtitles.

**Claude Code**

```bash
claude mcp add subtitle -- uvx subtitle-mcp --dir "/path/to/your/videos"
```

**Claude Desktop** (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "subtitle": {
      "command": "uvx",
      "args": ["subtitle-mcp", "--dir", "C:\\Users\\you\\Videos"]
    }
  }
}
```

Or `pip install subtitle-mcp` and use the `subtitle-mcp` command.

## Try asking

- *"What's wrong with `Movie.srt`? Show me the problems and the reading speed."*
- *"The subtitles in `Movie.srt` are 3 seconds late. Fix them."*
- *"They start in sync but drift: the line 'We have to go back' (cue 12) should be at 00:06:41,300 and 'It was never about the money' (cue 1304) at 01:48:12,900. Resync it."*
- *"This Turkish file looks garbled. Detect the encoding and convert it to UTF-8."*
- *"Convert `episode.ass` to SRT."*
- *"Clean up `old.srt`: show me what you would fix first, then do it and strip the font tags."*
- *"Find every cue where someone says 'password' and give me the timestamps."*
- *"Join `CD1.srt` and `CD2.srt` into one file, and split the result at 01:12:00."*
- *"Translate `Movie.srt` into Turkish."* (the agent calls `chunk_for_translation`, translates each chunk itself, then `apply_translation`)
- *"My video is 23.976 fps and this subtitle was made for 25 fps. Convert it."*

## Tools

| Tool | What it does |
|------|--------------|
| `subtitle_info` | Format, detected encoding (with the reason), line endings, cue count, time span, overlaps, gaps, longest line, reading speed (chars/sec min/mean/median/p95/max), and a problem list with cue numbers. |
| `read_cues` | Show cues by number range or time window, with a cap. |
| `search_subtitles` | Find cues by text or regex, with cue numbers and timestamps. Matches across line breaks. |
| `shift` | Constant offset in ms, or in frames at a frame rate (29.97 is exact). Optionally only from a given cue on. |
| `resync` | Two or more anchors fix offset and drift (exact least squares, residual reported); or `fps_from`/`fps_to` for frame-rate conversion. |
| `convert` | srt, vtt, and ass-to-either; also re-encodes (cp1254 to UTF-8, to UTF-16, ...). Lists what could not be carried over. |
| `fix_common_problems` | Dry run by default. Overlaps, zero/negative durations, ordering, indices, blank lines in cues, BOM, mixed line endings, long lines (balanced re-wrap), optional tag stripping. Reading-speed outliers are flagged, never changed. |
| `merge_or_split` | Merge two files with an offset (default: second after first), or split at a time with re-zeroing. |
| `chunk_for_translation` | Numbered cue batches with stable IDs. |
| `apply_translation` | Takes `{id: text}`, requires every ID, keeps timing/identifiers/notes, warns on empty/much longer/shorter text, lost tags, unreadable speed. |

Cue numbers everywhere are the 1-based position of the cue in the file.

### The translation contract

The server never translates. `chunk_for_translation(path, max_chars)` returns
`{"id": "12", "text": "..."}` records (one chunk per call; `chunk=2`, `3`, ...). Your agent
translates and sends **one** mapping `{"12": "..."}` to `apply_translation`. Missing or unknown IDs
fail with the list of offenders; `partial=true` keeps untranslated cues as they are. Timing is
never touched, and tags such as `<i>` and `{\an8}` that appear in the source but not in the
translation are reported.

## Formats and limits (read this)

- **SRT and WebVTT** are read and written fully. The parser is tolerant (missing, duplicate or
  out-of-order indices; index separated from timing by a blank line; blank lines inside cues;
  `.` or `,` milliseconds; hours over 99; cues glued together; SRT `X1:..` coordinates; WebVTT
  header metadata, NOTE/STYLE/REGION blocks, identifiers, cue settings). Everything it had to
  guess is listed under `parse_warnings` in `subtitle_info`.
- **ASS/SSA is read-only**: timing and text are extracted (tags stripped, `\N` becomes a line
  break) and can be converted to SRT/VTT. **Styling is dropped**: fonts, colours, positions,
  effects, karaoke, drawings. The tools say so. Editing tools refuse ASS and tell you to convert first.
- Converting SRT to WebVTT drops SRT coordinates; WebVTT to SRT drops cue settings and
  NOTE/STYLE/REGION blocks. Blank lines inside a cue are removed on write, because both
  formats end a cue at a blank line.
- **Encoding detection is a heuristic, and an honest one** (the full rules are in
  `src/subtitle_mcp/encoding.py`): BOMs and UTF-16 are certain; valid UTF-8 is trusted;
  otherwise Turkish letters (Ğ İ Ş ğ ı ş) select cp1254, letters between letters in the
  0xA1-0xBF range select iso-8859-2, and anything else is cp1252. It will not guess between
  Cyrillic/Greek code pages (it errors and asks for `encoding=`), cannot tell iso-8859-1 from
  -15, and a Turkish file with none of its six special letters is read as cp1252, which
  decodes identically. If text looks wrong, pass `encoding=` to any tool. Output is UTF-8
  unless you choose `output_encoding`; characters the target cannot hold are an error, not `?`.
- **Resync assumes a straight line** (constant offset plus constant speed ratio). A film cut
  with a scene removed in the middle needs `merge_or_split` plus two resyncs.
- Line wrapping counts visible characters (tags excluded) and never splits a word, so a
  word longer than the limit stays on its own over-long line (and is flagged).

## Security

- Local files only, no network access, no subprocesses, no dependency beyond `mcp`.
- **`--dir DIR` (repeatable) confines every read and write** to those folders; `..` and
  symlinks are resolved before checking. Relative paths resolve against the first `--dir`.
  **Without `--dir` any path the user account can reach is allowed**, so set it.
- Only `.srt .vtt .ass .ssa` files can be read and only `.srt .vtt` written, so the tools cannot be
  steered into overwriting other files.
- Nothing is overwritten unless `overwrite=true`; edits go to a new sibling file by default
  (`movie.shifted.srt`, `movie.fixed.srt`, ...). Writes are atomic (temp file in the same folder, then
  replace), and a failed write leaves the destination untouched.
- Input is capped at 64 MiB. A pathological regular expression passed to `search_subtitles`
  runs unguarded (Python `re`), as with any local tool.

## Tests

```bash
pip install ".[test]" "ruff==0.16.0" "mypy==2.3.0"
ruff check src tests && mypy --strict src
pytest tests
```

Real-world-like messy fixtures (including cp1254 Turkish, UTF-16, iso-8859-2) with exact expected
output, 2,400 generated parse/write round-trips, hand-checked resync math, a real MCP stdio session
against the installed server, and [ffmpeg](https://ffmpeg.org) as an independent oracle
(it produces ASS for the parser, and re-reads the files we write). The oracle tests skip when
ffmpeg is not installed.

Want to learn how it works? [subtitle-mcp-the-hard-way](https://github.com/0xmortuex/subtitle-mcp-the-hard-way)
rebuilds a minimal version in seven runnable modules.

## License

MIT
