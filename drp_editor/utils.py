"""Shared helpers: logging setup, hex utilities, and hex dumps.

Nothing in this module knows about Resolve project semantics; it is pure
plumbing used by the higher layers.
"""

from __future__ import annotations

import logging
import re

__all__ = [
    "hex_dump",
    "is_hex_string",
    "normalize_hex",
    "setup_logging",
]

_HEX_RE = re.compile(r"\A(?:[0-9a-fA-F]{2})+\Z")

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def setup_logging(verbosity: int = 0) -> None:
    """Configure root logging for the CLI.

    Args:
        verbosity: 0 = WARNING, 1 = INFO, 2+ = DEBUG.
    """
    level = logging.WARNING
    if verbosity == 1:
        level = logging.INFO
    elif verbosity >= 2:
        level = logging.DEBUG
    logging.basicConfig(level=level, format=_LOG_FORMAT)


def is_hex_string(text: str) -> bool:
    """Return ``True`` if *text* is a non-empty even-length hex string."""
    return bool(_HEX_RE.match(text.strip()))


def normalize_hex(text: str) -> str:
    """Strip whitespace from a hex string without changing its case.

    Case is preserved deliberately: when we re-encode a blob we want to
    emit exactly the style the original file used.
    """
    return "".join(text.split())


def hex_dump(data: bytes, *, start_offset: int = 0, width: int = 16) -> str:
    """Render *data* as a classic offset / hex / ASCII dump.

    Args:
        data: Bytes to render.
        start_offset: Offset label of the first byte (purely cosmetic).
        width: Number of bytes per row.

    Returns:
        A multi-line string. Empty input yields an empty string.
    """
    if not data:
        return ""
    lines: list[str] = []
    for row_start in range(0, len(data), width):
        chunk = data[row_start : row_start + width]
        hex_part = " ".join(f"{b:02x}" for b in chunk).ljust(width * 3 - 1)
        ascii_part = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in chunk)
        lines.append(f"{start_offset + row_start:08x}  {hex_part}  |{ascii_part}|")
    return "\n".join(lines)
