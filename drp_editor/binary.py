"""Reusable binary parsing framework.

:class:`BinaryReader` and :class:`BinaryWriter` wrap ``struct`` with a
cursor, explicit endianness, and bounds checking so that blob decoders
never duplicate low-level parsing code.

All read errors raise :class:`~drp_editor.exceptions.BinaryDecodeError`
rather than ``struct.error`` so callers only deal with the package's
exception hierarchy.
"""

from __future__ import annotations

import struct
from typing import Literal

from .exceptions import BinaryDecodeError

__all__ = ["BinaryReader", "BinaryWriter", "Endianness"]

Endianness = Literal["little", "big"]

_PREFIX: dict[Endianness, str] = {"little": "<", "big": ">"}


class BinaryReader:
    """Cursor-based reader over an immutable ``bytes`` buffer.

    Example:
        >>> reader = BinaryReader(b"\\x01\\x00\\x00\\x00")
        >>> reader.read_uint32()
        1
    """

    def __init__(self, data: bytes, *, endianness: Endianness = "little") -> None:
        self._data = data
        self._pos = 0
        self._prefix = _PREFIX[endianness]

    @property
    def data(self) -> bytes:
        """The underlying buffer."""
        return self._data

    def tell(self) -> int:
        """Return the current cursor position."""
        return self._pos

    def seek(self, offset: int) -> None:
        """Move the cursor to *offset* (absolute)."""
        if not 0 <= offset <= len(self._data):
            raise BinaryDecodeError(f"seek to {offset} outside buffer of {len(self._data)} bytes")
        self._pos = offset

    def skip(self, count: int) -> None:
        """Advance the cursor by *count* bytes."""
        self.seek(self._pos + count)

    def remaining(self) -> int:
        """Number of unread bytes."""
        return len(self._data) - self._pos

    def eof(self) -> bool:
        """``True`` when the cursor is at the end of the buffer."""
        return self._pos >= len(self._data)

    def read_bytes(self, count: int) -> bytes:
        """Read exactly *count* raw bytes."""
        if count < 0:
            raise BinaryDecodeError(f"cannot read negative count {count}")
        if self._pos + count > len(self._data):
            raise BinaryDecodeError(
                f"read of {count} bytes at offset {self._pos} exceeds "
                f"buffer of {len(self._data)} bytes"
            )
        result = self._data[self._pos : self._pos + count]
        self._pos += count
        return result

    def _unpack(self, fmt: str) -> int | float:
        full_fmt = self._prefix + fmt
        size = struct.calcsize(full_fmt)
        raw = self.read_bytes(size)
        value: int | float = struct.unpack(full_fmt, raw)[0]
        return value

    def read_uint8(self) -> int:
        """Read an unsigned 8-bit integer."""
        return int(self._unpack("B"))

    def read_int8(self) -> int:
        """Read a signed 8-bit integer."""
        return int(self._unpack("b"))

    def read_uint16(self) -> int:
        """Read an unsigned 16-bit integer."""
        return int(self._unpack("H"))

    def read_int16(self) -> int:
        """Read a signed 16-bit integer."""
        return int(self._unpack("h"))

    def read_uint32(self) -> int:
        """Read an unsigned 32-bit integer."""
        return int(self._unpack("I"))

    def read_int32(self) -> int:
        """Read a signed 32-bit integer."""
        return int(self._unpack("i"))

    def read_uint64(self) -> int:
        """Read an unsigned 64-bit integer."""
        return int(self._unpack("Q"))

    def read_int64(self) -> int:
        """Read a signed 64-bit integer."""
        return int(self._unpack("q"))

    def read_float(self) -> float:
        """Read a 32-bit IEEE-754 float."""
        return float(self._unpack("f"))

    def read_double(self) -> float:
        """Read a 64-bit IEEE-754 float."""
        return float(self._unpack("d"))

    def read_string(self, *, encoding: str = "utf-8") -> str:
        """Read a uint32-length-prefixed string.

        This is the most common string layout observed in Resolve blobs;
        decoders needing other layouts should compose primitives instead.
        """
        length = self.read_uint32()
        raw = self.read_bytes(length)
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError as exc:
            raise BinaryDecodeError(f"invalid {encoding} string at offset {self._pos}") from exc

    def read_cstring(self, *, encoding: str = "utf-8") -> str:
        """Read a NUL-terminated string."""
        end = self._data.find(b"\x00", self._pos)
        if end < 0:
            raise BinaryDecodeError(f"unterminated cstring at offset {self._pos}")
        raw = self.read_bytes(end - self._pos)
        self.skip(1)  # consume terminator
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError as exc:
            raise BinaryDecodeError(f"invalid {encoding} cstring at offset {self._pos}") from exc


class BinaryWriter:
    """Cursor-less append writer producing a ``bytes`` buffer."""

    def __init__(self, *, endianness: Endianness = "little") -> None:
        self._buffer = bytearray()
        self._prefix = _PREFIX[endianness]

    def getvalue(self) -> bytes:
        """Return everything written so far."""
        return bytes(self._buffer)

    def tell(self) -> int:
        """Number of bytes written so far."""
        return len(self._buffer)

    def write_bytes(self, data: bytes) -> None:
        """Append raw bytes."""
        self._buffer.extend(data)

    def _pack(self, fmt: str, value: int | float) -> None:
        try:
            self._buffer.extend(struct.pack(self._prefix + fmt, value))
        except struct.error as exc:
            raise BinaryDecodeError(f"cannot pack {value!r} as {fmt!r}") from exc

    def write_uint8(self, value: int) -> None:
        """Append an unsigned 8-bit integer."""
        self._pack("B", value)

    def write_int8(self, value: int) -> None:
        """Append a signed 8-bit integer."""
        self._pack("b", value)

    def write_uint16(self, value: int) -> None:
        """Append an unsigned 16-bit integer."""
        self._pack("H", value)

    def write_int16(self, value: int) -> None:
        """Append a signed 16-bit integer."""
        self._pack("h", value)

    def write_uint32(self, value: int) -> None:
        """Append an unsigned 32-bit integer."""
        self._pack("I", value)

    def write_int32(self, value: int) -> None:
        """Append a signed 32-bit integer."""
        self._pack("i", value)

    def write_uint64(self, value: int) -> None:
        """Append an unsigned 64-bit integer."""
        self._pack("Q", value)

    def write_int64(self, value: int) -> None:
        """Append a signed 64-bit integer."""
        self._pack("q", value)

    def write_float(self, value: float) -> None:
        """Append a 32-bit IEEE-754 float."""
        self._pack("f", value)

    def write_double(self, value: float) -> None:
        """Append a 64-bit IEEE-754 float."""
        self._pack("d", value)

    def write_string(self, value: str, *, encoding: str = "utf-8") -> None:
        """Append a uint32-length-prefixed string."""
        raw = value.encode(encoding)
        self.write_uint32(len(raw))
        self.write_bytes(raw)

    def write_cstring(self, value: str, *, encoding: str = "utf-8") -> None:
        """Append a NUL-terminated string."""
        self.write_bytes(value.encode(encoding))
        self.write_bytes(b"\x00")
