# Changelog

## 0.1.0 - 2026-11-06

First release.

- Formats: SRT and WebVTT read and written by hand-written, tolerant parsers (missing or
  duplicate indices, an index separated from its timing by a blank line, blank lines inside a
  cue, cues glued together, dot or comma milliseconds, any number of fraction digits with
  half-up rounding, hours past 99, SRT coordinates, WebVTT header metadata, NOTE/STYLE/REGION
  blocks, cue identifiers and cue settings). ASS/SSA is read (timing and text only) and can be
  converted to SRT/VTT; styling is dropped and reported.
- Encodings: UTF-8 / UTF-8 BOM / UTF-16 / UTF-32, plus a documented heuristic for cp1254
  (Turkish), cp1252 and iso-8859-2, with an explicit `encoding` override on every tool and
  `output_encoding` on every writer. It refuses to guess Cyrillic/Greek legacy encodings.
- Tools: `subtitle_info`, `read_cues`, `search_subtitles`, `shift` (ms or frames at an exact
  frame rate), `resync` (exact least-squares line through 2+ anchors, or an fps ratio),
  `convert`, `fix_common_problems` (dry run by default), `merge_or_split`,
  `chunk_for_translation` and `apply_translation`.
- Safety: `--dir` sandbox, extension allow-list, no overwrite unless `overwrite=true`, atomic
  writes.
