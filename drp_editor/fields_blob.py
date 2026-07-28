"""FieldsBlob: hex-encoded binary settings blobs and their gradual decoding.

DaVinci Resolve stores many per-clip and per-timeline settings inside
hexadecimal blobs whose layout is undocumented. This module provides:

* :class:`FieldSpec` -- a declaration of one known field (name, offset, type),
* :class:`BlobSchema` / :class:`BlobSchemaRegistry` -- collections of specs
  that grow as fields are reverse engineered (loadable from JSON so a
  signature database can be shipped separately from code),
* :class:`FieldsBlob` -- the blob itself.

Design rules
------------
1. Unknown bytes are NEVER touched. ``set_field`` patches exactly the bytes
   covered by the field's spec; everything else survives round-trip
   byte-for-byte.
2. Hex case (upper/lower) of the original document is preserved on
   re-encode so unmodified blobs serialize to the identical string.

Known limitations
-----------------
* Only fixed-offset fields are supported. Variable-length layouts will need
  a schema upgrade once such blobs are mapped.
* Field offsets are assumed to be stable across the Resolve versions the
  schema was built for; schemas should record a version hint in their name.
"""

from __future__ import annotations

import json
import logging
import struct
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .exceptions import BinaryDecodeError
from .utils import is_hex_string, normalize_hex

__all__ = [
    "BlobSchema",
    "BlobSchemaRegistry",
    "ByteChange",
    "FieldSpec",
    "FieldsBlob",
    "default_registry",
]

logger = logging.getLogger(__name__)

FieldType = Literal[
    "uint8",
    "int8",
    "uint16",
    "int16",
    "uint32",
    "int32",
    "uint64",
    "int64",
    "float",
    "double",
    "bytes",
]

_STRUCT_FMT: dict[str, str] = {
    "uint8": "B",
    "int8": "b",
    "uint16": "H",
    "int16": "h",
    "uint32": "I",
    "int32": "i",
    "uint64": "Q",
    "int64": "q",
    "float": "f",
    "double": "d",
}


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """Declaration of one reverse-engineered field inside a blob.

    Attributes:
        name: Stable identifier, e.g. ``"super_scale"``.
        offset: Byte offset of the field inside the blob.
        type: Primitive type name; ``"bytes"`` requires an explicit ``size``.
        size: Byte width; derived from ``type`` for numeric fields.
        description: Human-readable notes from the reverse-engineering log.
        endianness: Byte order for multi-byte numeric fields.
    """

    name: str
    offset: int
    type: FieldType
    size: int = 0
    description: str = ""
    endianness: Literal["little", "big"] = "little"

    def __post_init__(self) -> None:
        if self.type == "bytes":
            if self.size <= 0:
                raise ValueError(f"field {self.name!r}: type 'bytes' requires a positive size")
        else:
            derived = struct.calcsize(_STRUCT_FMT[self.type])
            if self.size not in (0, derived):
                raise ValueError(
                    f"field {self.name!r}: size {self.size} conflicts with type {self.type}"
                )
            object.__setattr__(self, "size", derived)

    @property
    def end(self) -> int:
        """Offset one past the last byte of this field."""
        return self.offset + self.size

    def decode(self, blob: bytes) -> int | float | bytes:
        """Extract this field's value from *blob*."""
        if self.end > len(blob):
            raise BinaryDecodeError(
                f"field {self.name!r} spans [{self.offset}, {self.end}) but blob "
                f"is only {len(blob)} bytes"
            )
        raw = blob[self.offset : self.end]
        if self.type == "bytes":
            return raw
        prefix = "<" if self.endianness == "little" else ">"
        value: int | float = struct.unpack(prefix + _STRUCT_FMT[self.type], raw)[0]
        return value

    def encode(self, value: int | float | bytes) -> bytes:
        """Serialize *value* into this field's raw byte representation."""
        if self.type == "bytes":
            if not isinstance(value, bytes) or len(value) != self.size:
                raise BinaryDecodeError(
                    f"field {self.name!r} expects exactly {self.size} raw bytes"
                )
            return value
        if isinstance(value, bytes):
            raise BinaryDecodeError(f"field {self.name!r} expects a number, not bytes")
        prefix = "<" if self.endianness == "little" else ">"
        try:
            return struct.pack(prefix + _STRUCT_FMT[self.type], value)
        except struct.error as exc:
            raise BinaryDecodeError(
                f"value {value!r} does not fit field {self.name!r} ({self.type})"
            ) from exc


@dataclass(slots=True)
class BlobSchema:
    """A named collection of :class:`FieldSpec` for one kind of blob."""

    name: str
    fields: dict[str, FieldSpec] = field(default_factory=dict)

    def add(self, spec: FieldSpec, *, replace: bool = False) -> None:
        """Register *spec*, rejecting duplicate names unless *replace*."""
        if spec.name in self.fields and not replace:
            raise ValueError(f"duplicate field {spec.name!r} in schema {self.name!r}")
        self.fields[spec.name] = spec

    def get(self, name: str) -> FieldSpec | None:
        """Look up a field spec by name."""
        return self.fields.get(name)

    def __iter__(self) -> Iterator[FieldSpec]:
        return iter(self.fields.values())


class BlobSchemaRegistry:
    """Registry mapping blob kinds (e.g. ``"clip"``) to their schemas.

    Schemas can be built in code or loaded from a JSON signature database::

        {
          "clip": [
            {"name": "super_scale", "offset": 72, "type": "uint8",
             "description": "AI Super Scale mode; 0 disables"}
          ]
        }
    """

    def __init__(self) -> None:
        self._schemas: dict[str, BlobSchema] = {}

    def schema(self, kind: str) -> BlobSchema:
        """Return (creating if needed) the schema for *kind*."""
        if kind not in self._schemas:
            self._schemas[kind] = BlobSchema(name=kind)
        return self._schemas[kind]

    def get(self, kind: str) -> BlobSchema | None:
        """Return the schema for *kind*, or ``None`` if unknown."""
        return self._schemas.get(kind)

    def kinds(self) -> list[str]:
        """All registered blob kinds."""
        return sorted(self._schemas)

    def load_json(self, path: Path | str) -> None:
        """Merge a JSON signature database file into this registry."""
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise BinaryDecodeError(f"signature database {path} must be a JSON object")
        for kind, entries in raw.items():
            schema = self.schema(kind)
            for entry in entries:
                schema.add(FieldSpec(**entry), replace=True)
        logger.info("loaded signature database from %s", path)


#: Process-wide default registry. Repairs and applications may populate it
#: at startup (e.g. via ``default_registry.load_json(...)``).
default_registry = BlobSchemaRegistry()


@dataclass(frozen=True, slots=True)
class ByteChange:
    """One contiguous run of differing bytes between two blobs."""

    offset: int
    old: bytes
    new: bytes


class FieldsBlob:
    """A hex-encoded binary blob with partially known structure.

    The blob keeps its raw bytes as the source of truth. Known fields are
    windows onto those bytes; writing a field patches only its window.

    Args:
        raw: The decoded binary content.
        schema: Optional schema describing known fields.
        hex_case: ``"lower"``, ``"upper"``, or ``"mixed"`` -- the case style
            of the original hex text, preserved on re-encode.
        original_hex: The exact original hex text, returned verbatim by
            :meth:`to_hex` while the blob is unmodified.
    """

    def __init__(
        self,
        raw: bytes,
        *,
        schema: BlobSchema | None = None,
        hex_case: str = "lower",
        original_hex: str | None = None,
    ) -> None:
        self._data = bytearray(raw)
        self._schema = schema
        self._hex_case = hex_case
        self._original_hex = original_hex
        self._dirty = False

    # -- construction -------------------------------------------------

    @classmethod
    def from_hex(cls, text: str, *, schema: BlobSchema | None = None) -> FieldsBlob:
        """Parse a hex string as found in the project XML.

        Raises:
            BinaryDecodeError: if *text* is not valid even-length hex.
        """
        normalized = normalize_hex(text)
        if not normalized:
            return cls(b"", schema=schema, original_hex=text)
        if not is_hex_string(normalized):
            raise BinaryDecodeError(f"not a valid hex blob: {text[:40]!r}...")
        alpha = [c for c in normalized if c.isalpha()]
        if not alpha or all(c.islower() for c in alpha):
            case = "lower"
        elif all(c.isupper() for c in alpha):
            case = "upper"
        else:
            case = "mixed"
        return cls(bytes.fromhex(normalized), schema=schema, hex_case=case, original_hex=text)

    # -- basic accessors ----------------------------------------------

    @property
    def raw_bytes(self) -> bytes:
        """Current binary content."""
        return bytes(self._data)

    @property
    def schema(self) -> BlobSchema | None:
        """Schema describing this blob's known fields, if any."""
        return self._schema

    @schema.setter
    def schema(self, schema: BlobSchema | None) -> None:
        self._schema = schema

    @property
    def dirty(self) -> bool:
        """``True`` once any byte has been modified."""
        return self._dirty

    def __len__(self) -> int:
        return len(self._data)

    # -- encode / decode ----------------------------------------------

    def to_hex(self) -> str:
        """Serialize back to hex text.

        Unmodified blobs return the exact original text (including any
        whitespace quirks). Modified blobs are re-encoded preserving the
        original case style; ``"mixed"`` falls back to lowercase.
        """
        if not self._dirty and self._original_hex is not None:
            return self._original_hex
        text = self._data.hex()
        if self._hex_case == "upper":
            return text.upper()
        return text

    def encode(self) -> bytes:
        """Return the raw bytes (alias of :attr:`raw_bytes`)."""
        return self.raw_bytes

    def decode(self) -> dict[str, int | float | bytes]:
        """Decode all known fields into a name -> value mapping.

        Fields whose window falls outside the blob are skipped with a
        warning rather than failing the whole decode: shorter blob
        variants exist across Resolve versions.
        """
        result: dict[str, int | float | bytes] = {}
        if self._schema is None:
            return result
        for spec in self._schema:
            try:
                result[spec.name] = spec.decode(bytes(self._data))
            except BinaryDecodeError:
                logger.warning(
                    "field %r does not fit blob of %d bytes; skipping",
                    spec.name,
                    len(self._data),
                )
        return result

    def known_fields(self) -> dict[str, FieldSpec]:
        """Specs for fields that fit inside this blob."""
        if self._schema is None:
            return {}
        return {s.name: s for s in self._schema if s.end <= len(self._data)}

    def unknown_ranges(self) -> list[tuple[int, int]]:
        """Byte ranges ``[start, end)`` not covered by any known field."""
        covered = sorted((s.offset, s.end) for s in self.known_fields().values())
        ranges: list[tuple[int, int]] = []
        cursor = 0
        for start, end in covered:
            if start > cursor:
                ranges.append((cursor, start))
            cursor = max(cursor, end)
        if cursor < len(self._data):
            ranges.append((cursor, len(self._data)))
        return ranges

    # -- field access -------------------------------------------------

    def get_field(self, name: str) -> int | float | bytes:
        """Read one known field by name."""
        spec = self._require_spec(name)
        return spec.decode(bytes(self._data))

    def set_field(self, name: str, value: int | float | bytes) -> tuple[bytes, bytes]:
        """Write one known field, touching only its bytes.

        Returns:
            ``(old_raw, new_raw)`` -- the field's bytes before and after.
        """
        spec = self._require_spec(name)
        if spec.end > len(self._data):
            raise BinaryDecodeError(
                f"field {name!r} spans [{spec.offset}, {spec.end}) but blob "
                f"is only {len(self._data)} bytes"
            )
        old = bytes(self._data[spec.offset : spec.end])
        new = spec.encode(value)
        if new != old:
            self._data[spec.offset : spec.end] = new
            self._dirty = True
            logger.debug(
                "blob field %r changed at 0x%04x: %s -> %s",
                name,
                spec.offset,
                old.hex(),
                new.hex(),
            )
        return old, new

    def set_bytes(self, offset: int, new: bytes) -> tuple[bytes, bytes]:
        """Overwrite raw bytes at *offset* (advanced / repair use).

        Length is never changed; the write must fit inside the blob.
        """
        if offset < 0 or offset + len(new) > len(self._data):
            raise BinaryDecodeError(
                f"write of {len(new)} bytes at 0x{offset:04x} exceeds blob "
                f"of {len(self._data)} bytes"
            )
        old = bytes(self._data[offset : offset + len(new)])
        if new != old:
            self._data[offset : offset + len(new)] = new
            self._dirty = True
        return old, new

    def _require_spec(self, name: str) -> FieldSpec:
        if self._schema is None:
            raise BinaryDecodeError(f"blob has no schema; cannot access field {name!r}")
        spec = self._schema.get(name)
        if spec is None:
            raise BinaryDecodeError(f"unknown field {name!r} in schema {self._schema.name!r}")
        return spec

    # -- diffing -------------------------------------------------------

    def diff(self, other: FieldsBlob) -> list[ByteChange]:
        """Byte-level diff against *other* (self = old, other = new)."""
        return diff_bytes(self.raw_bytes, other.raw_bytes)


def diff_bytes(old: bytes, new: bytes) -> list[ByteChange]:
    """Compare two byte strings, returning contiguous change runs.

    Length differences are reported as one trailing change covering the
    extra bytes. This is a positional diff (no alignment/LCS): ideal for
    the fixed-layout blobs we target, wrong for inserted bytes -- a known
    limitation documented for reverse-engineering workflows.
    """
    changes: list[ByteChange] = []
    common = min(len(old), len(new))
    run_start: int | None = None
    for i in range(common):
        if old[i] != new[i]:
            if run_start is None:
                run_start = i
        elif run_start is not None:
            changes.append(ByteChange(run_start, old[run_start:i], new[run_start:i]))
            run_start = None
    if run_start is not None:
        changes.append(ByteChange(run_start, old[run_start:common], new[run_start:common]))
    if len(old) != len(new):
        changes.append(ByteChange(common, old[common:], new[common:]))
    return changes


def field_diff(old: FieldsBlob, new: FieldsBlob) -> dict[str, tuple[Any, Any]]:
    """Diff two blobs at the known-field level.

    Returns:
        Mapping of field name to ``(old_value, new_value)`` for fields
        whose decoded values differ. Only fields known to *old*'s schema
        are considered.
    """
    old_values = old.decode()
    new_values = new.decode()
    return {
        name: (old_values[name], new_values[name])
        for name in old_values
        if name in new_values and old_values[name] != new_values[name]
    }
