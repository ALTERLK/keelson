"""Reading and writing source files without damaging them.

The tools used to read with ``errors="replace"`` and write back UTF-8 with
``\\n``: one edit to a GBK file replaced every Chinese character with U+FFFD,
and one edit to a CRLF file rewrote every line ending in it. Here:

* the encoding is detected: BOM, then strict UTF-8, then strict GBK, then GB18030;
  a file is only editable when decoding and re-encoding gives back exactly the
  original bytes, so writing can never silently change what was not edited;
* binary files are refused;
* the original BOM and line endings are kept, and only the edited text changes;
* writes go to a temporary file in the same folder and replace the original
  in one step, so an interrupted write never leaves half a file.
"""

from __future__ import annotations

import codecs
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

SNIFF_BYTES = 8192


class NotTextError(ValueError):
    """The file is binary, or its encoding cannot be told safely."""


@dataclass
class TextFile:
    text: str            # exactly as decoded: line endings not translated
    encoding: str        # codec used to encode it back (without the BOM)
    bom: bytes = b""
    newline: str = "\n"  # the file's dominant line ending
    mixed_newlines: bool = False

    @property
    def lines(self) -> list[str]:
        """Lines without their endings, for display."""
        return self.text.replace("\r\n", "\n").split("\n")

    def encode(self, text: str) -> bytes:
        return self.bom + text.encode(self.encoding)

    def to_file_newlines(self, text: str) -> str:
        """Model-written text (``\\n``) in this file's line ending."""
        if self.newline == "\r\n":
            return text.replace("\r\n", "\n").replace("\n", "\r\n")
        return text


_BOMS = (
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)


def _newline_style(text: str) -> tuple[str, bool]:
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    if crlf and lf:
        return ("\r\n" if crlf > lf else "\n"), True
    return ("\r\n" if crlf else "\n"), False


def looks_binary(data: bytes) -> bool:
    return b"\x00" in data[:SNIFF_BYTES]


def decode(data: bytes, *, name: str = "file") -> TextFile:
    for bom, encoding in _BOMS:
        if data.startswith(bom):
            try:
                text = data[len(bom):].decode(encoding)
            except UnicodeDecodeError as exc:
                raise NotTextError(f"{name} starts with a {encoding} byte-order mark but is not valid {encoding}") from exc
            newline, mixed = _newline_style(text)
            return TextFile(text, encoding, bom, newline, mixed)

    if looks_binary(data):
        raise NotTextError(f"{name} looks like a binary file")

    # Strict GBK before GB18030: GB18030 can encode anything, so an edit would
    # happily write an emoji into a file that old GBK-only tools must still read.
    for encoding in ("utf-8", "gbk", "gb18030"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if text.encode(encoding) != data:
            continue  # would not round-trip: writing it back could change bytes nobody edited
        newline, mixed = _newline_style(text)
        return TextFile(text, encoding, b"", newline, mixed)

    raise NotTextError(f"{name} is neither UTF-8 nor GBK text; its encoding cannot be told safely")


def read(path: str | os.PathLike) -> TextFile:
    path = Path(path)
    return decode(path.read_bytes(), name=path.name)


def new_file() -> TextFile:
    """How a file Keelson creates is written: UTF-8, no BOM, LF."""
    return TextFile("", "utf-8")


def write(path: str | os.PathLike, text: str, like: TextFile | None = None) -> None:
    """Write ``text`` (already in the file's line endings) with ``like``'s encoding and BOM.

    Raises UnicodeEncodeError when the text has characters the file's encoding
    cannot hold (say, an emoji in a GBK file): changing the encoding of a whole
    file is not something to do as a side effect of an edit.
    """
    path = Path(path)
    data = (like or new_file()).encode(text)
    atomic_write_bytes(path, data)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".keelson-tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            try:
                os.chmod(tmp, path.stat().st_mode)
            except OSError:
                pass
        try:
            os.replace(tmp, path)
        except PermissionError:
            # Windows: another program holds the file open without sharing
            # delete access (an editor, an indexer). Fall back to writing in place.
            path.write_bytes(data)
            os.unlink(tmp)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def describe(tf: TextFile) -> str:
    """A short note for the model when the file is not plain UTF-8 + LF."""
    parts = []
    if tf.encoding != "utf-8" or tf.bom:
        parts.append(tf.encoding.upper() + (" with BOM" if tf.bom else ""))
    if tf.newline == "\r\n":
        parts.append("CRLF line endings")
    if tf.mixed_newlines:
        parts.append("mixed line endings")
    return ", ".join(parts)
