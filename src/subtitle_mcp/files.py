"""Reading and writing subtitle files safely: --dir sandbox, extension allow-list,
size cap, never overwrite unless asked, atomic replace."""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .encoding import EncodingGuess, decode, encode
from .errors import SubtitleError
from .formats import parse_subtitle, render
from .model import Subtitle

READ_EXTENSIONS = (".srt", ".vtt", ".ass", ".ssa")
WRITE_EXTENSIONS = (".srt", ".vtt")
MAX_BYTES = 64 * 1024 * 1024


class Sandbox:
    """Optional restriction of every path (inputs and outputs) to a set of directories.
    Symlinks and '..' are resolved first, so they cannot escape."""

    def __init__(self, roots: Sequence[str | Path] = ()) -> None:
        self.roots = [Path(r).expanduser().resolve() for r in roots]
        for r in self.roots:
            if not r.is_dir():
                raise SubtitleError(f"--dir {r} is not an existing directory")

    def resolve(self, path: str) -> Path:
        if not path or not path.strip():
            raise SubtitleError("path is empty")
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = (self.roots[0] if self.roots else Path.cwd()) / p
        p = p.resolve()
        if self.roots and not any(p.is_relative_to(r) for r in self.roots):
            allowed = ", ".join(str(r) for r in self.roots)
            raise SubtitleError(
                f"{p} is outside the allowed directories ({allowed}). "
                "Use a path inside them, or restart the server with a different --dir."
            )
        return p


@dataclass
class RawInfo:
    """Facts about the bytes on disk that the parsed model no longer carries."""

    encoding: EncodingGuess
    size: int
    crlf: int
    lf: int
    cr: int

    @property
    def mixed_newlines(self) -> bool:
        return sum(1 for n in (self.crlf, self.lf, self.cr) if n) > 1

    @property
    def newline(self) -> str:
        """The dominant line ending, used when writing."""
        if self.crlf > self.lf and self.crlf >= self.cr:
            return "\r\n"
        if self.cr > self.lf and self.cr > self.crlf:
            return "\r"
        return "\n"


@dataclass
class Loaded:
    path: Path
    sub: Subtitle
    raw: RawInfo


def _newline_counts(text: str) -> tuple[int, int, int]:
    crlf = text.count("\r\n")
    return crlf, text.count("\n") - crlf, text.count("\r") - crlf


def load(sandbox: Sandbox, path: str, encoding: str | None = None) -> Loaded:
    p = sandbox.resolve(path)
    if p.suffix.lower() not in READ_EXTENSIONS:
        raise SubtitleError(
            f"{p.name}: only {', '.join(READ_EXTENSIONS)} files are handled (got {p.suffix or 'no extension'})"
        )
    if not p.exists():
        raise SubtitleError(f"{p} does not exist")
    if not p.is_file():
        raise SubtitleError(f"{p} is not a file")
    size = p.stat().st_size
    if size > MAX_BYTES:
        raise SubtitleError(f"{p.name} is {size} bytes; the limit is {MAX_BYTES}")
    data = p.read_bytes()
    if not data.strip(b"\x00 \t\r\n\xef\xbb\xbf"):
        raise SubtitleError(f"{p.name} is empty")
    text, guess = decode(data, encoding)
    crlf, lf, cr = _newline_counts(text)
    sub = parse_subtitle(text)
    return Loaded(p, sub, RawInfo(guess, size, crlf, lf, cr))


def default_output(src: Path, suffix: str, fmt: str | None = None) -> str:
    """movie.en.srt + 'shifted' -> movie.en.shifted.srt (extension from fmt if given)."""
    ext = f".{fmt}" if fmt else src.suffix
    return str(src.with_name(f"{src.stem}.{suffix}{ext}"))


def write_atomic(dest: Path, data: bytes, overwrite: bool) -> None:
    """Write via a temp file in the same directory and an atomic replace. Without
    overwrite, the destination is reserved with O_EXCL first, so two writers cannot
    both think the name is free."""
    if not dest.parent.is_dir():
        raise SubtitleError(f"the folder {dest.parent} does not exist")
    if dest.is_dir():
        raise SubtitleError(f"{dest} is a directory")
    reserved = False
    if not overwrite:
        try:
            os.close(os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            reserved = True
        except FileExistsError:
            raise SubtitleError(
                f"{dest} already exists; pass overwrite=true to replace it, or choose another output"
            ) from None
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".subtitle-mcp-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, dest)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        if reserved and dest.exists() and dest.stat().st_size == 0:
            dest.unlink()
        raise


def save(
    sandbox: Sandbox,
    sub: Subtitle,
    fmt: str,
    output: str,
    overwrite: bool,
    output_encoding: str,
    newline: str,
) -> Path:
    dest = sandbox.resolve(output)
    if dest.suffix.lower() not in WRITE_EXTENSIONS:
        raise SubtitleError(
            f"output {dest.name}: only {', '.join(WRITE_EXTENSIONS)} can be written"
        )
    if dest.suffix.lower() != f".{fmt}":
        raise SubtitleError(
            f"output {dest.name} has extension {dest.suffix} but the content is {fmt.upper()}; "
            f"name it with .{fmt}"
        )
    data = encode(render(sub, fmt, newline), output_encoding)
    write_atomic(dest, data, overwrite)
    return dest


_WS = re.compile(r"\s+")


def collapse(text: str) -> str:
    return _WS.sub(" ", text).strip()
