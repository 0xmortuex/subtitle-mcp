"""The in-memory subtitle model shared by every format."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Cue:
    start: int
    end: int
    text: str = ""  # lines joined with "\n"; may contain markup tags
    ident: str | None = None  # WebVTT cue identifier
    settings: str = ""  # text after the timestamps: WebVTT cue settings / SRT coordinates
    index: int | None = field(default=None, compare=False)  # number found in an SRT file

    def copy(self) -> Cue:
        return Cue(self.start, self.end, self.text, self.ident, self.settings, self.index)


@dataclass
class Subtitle:
    format: str  # "srt" | "vtt" | "ass"
    cues: list[Cue] = field(default_factory=list)
    header: str = "WEBVTT"  # WebVTT header block (first line plus optional metadata)
    # WebVTT NOTE/STYLE/REGION blocks: (number of cues before it, raw block text)
    extras: list[tuple[int, str]] = field(default_factory=list)
    # Things the parser had to guess, skip or drop. Reported to the user, never hidden.
    warnings: list[str] = field(default_factory=list, compare=False)
