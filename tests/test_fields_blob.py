"""Tests for the FieldsBlob system: decoding, editing, preservation."""

from __future__ import annotations

import pytest

from drp_editor.exceptions import BinaryDecodeError
from drp_editor.fields_blob import (
    BlobSchema,
    BlobSchemaRegistry,
    FieldsBlob,
    FieldSpec,
    diff_bytes,
    field_diff,
)


@pytest.fixture()
def schema() -> BlobSchema:
    schema = BlobSchema(name="clip")
    schema.add(FieldSpec(name="mode", offset=4, type="uint8"))
    schema.add(FieldSpec(name="scale", offset=8, type="float"))
    schema.add(FieldSpec(name="tag", offset=12, type="bytes", size=4))
    return schema


def make_blob(schema: BlobSchema) -> FieldsBlob:
    raw = bytes(range(32))
    return FieldsBlob(raw, schema=schema)


class TestHexRoundTrip:
    def test_unmodified_blob_preserves_exact_original_text(self):
        original = "DEADBEEF00"
        blob = FieldsBlob.from_hex(original)
        assert blob.to_hex() == original

    def test_case_preserved_after_modification(self, schema: BlobSchema):
        blob = FieldsBlob.from_hex("00" * 32, schema=schema)
        upper = FieldsBlob.from_hex("AB" * 32, schema=schema)
        blob.set_field("mode", 7)
        upper.set_field("mode", 7)
        assert blob.to_hex() == blob.raw_bytes.hex()
        assert upper.to_hex() == upper.raw_bytes.hex().upper()

    def test_invalid_hex_raises(self):
        with pytest.raises(BinaryDecodeError):
            FieldsBlob.from_hex("zz")

    def test_odd_length_hex_raises(self):
        with pytest.raises(BinaryDecodeError):
            FieldsBlob.from_hex("abc")

    def test_empty_blob(self):
        blob = FieldsBlob.from_hex("")
        assert blob.raw_bytes == b""
        assert blob.to_hex() == ""


class TestFieldAccess:
    def test_decode_known_fields(self, schema: BlobSchema):
        blob = make_blob(schema)
        values = blob.decode()
        assert values["mode"] == 4
        assert values["tag"] == bytes([12, 13, 14, 15])

    def test_set_field_touches_only_its_bytes(self, schema: BlobSchema):
        blob = make_blob(schema)
        before = blob.raw_bytes
        blob.set_field("mode", 0xEE)
        after = blob.raw_bytes
        assert after[4] == 0xEE
        # every other byte is untouched
        assert before[:4] == after[:4]
        assert before[5:] == after[5:]

    def test_set_field_returns_old_and_new(self, schema: BlobSchema):
        blob = make_blob(schema)
        old, new = blob.set_field("mode", 9)
        assert old == bytes([4])
        assert new == bytes([9])

    def test_unknown_field_raises(self, schema: BlobSchema):
        with pytest.raises(BinaryDecodeError):
            make_blob(schema).get_field("nope")

    def test_no_schema_raises(self):
        with pytest.raises(BinaryDecodeError):
            FieldsBlob(b"\x00" * 8).get_field("mode")

    def test_field_beyond_blob_skipped_in_decode(self, schema: BlobSchema):
        blob = FieldsBlob(b"\x00" * 6, schema=schema)  # too short for scale/tag
        assert set(blob.decode()) == {"mode"}

    def test_unknown_ranges(self, schema: BlobSchema):
        blob = make_blob(schema)
        assert blob.unknown_ranges() == [(0, 4), (5, 8), (16, 32)]

    def test_set_bytes_bounds_checked(self, schema: BlobSchema):
        blob = make_blob(schema)
        with pytest.raises(BinaryDecodeError):
            blob.set_bytes(31, b"\x00\x00")


class TestDiff:
    def test_identical_blobs_no_changes(self):
        a = FieldsBlob(b"\x01\x02\x03")
        assert a.diff(FieldsBlob(b"\x01\x02\x03")) == []

    def test_single_byte_change_reports_offset(self):
        old = FieldsBlob(bytes(16))
        new_raw = bytearray(16)
        new_raw[9] = 0xFF
        changes = old.diff(FieldsBlob(bytes(new_raw)))
        assert len(changes) == 1
        assert changes[0].offset == 9
        assert changes[0].old == b"\x00"
        assert changes[0].new == b"\xff"

    def test_contiguous_changes_grouped(self):
        changes = diff_bytes(b"\x00\x00\x00\x00", b"\x00\xaa\xbb\x00")
        assert len(changes) == 1
        assert changes[0].offset == 1
        assert changes[0].new == b"\xaa\xbb"

    def test_length_difference_reported(self):
        changes = diff_bytes(b"\x01\x02", b"\x01\x02\x03\x04")
        assert changes[-1].offset == 2
        assert changes[-1].new == b"\x03\x04"

    def test_field_diff(self, schema: BlobSchema):
        old = make_blob(schema)
        new = make_blob(schema)
        new.set_field("mode", 99)
        assert field_diff(old, new) == {"mode": (4, 99)}


class TestRegistry:
    def test_load_json(self, tmp_path):
        db = tmp_path / "sigs.json"
        db.write_text(
            '{"clip": [{"name": "super_scale", "offset": 72, "type": "uint8"}]}',
            encoding="utf-8",
        )
        registry = BlobSchemaRegistry()
        registry.load_json(db)
        assert registry.get("clip") is not None
        spec = registry.get("clip").get("super_scale")
        assert spec is not None and spec.offset == 72

    def test_duplicate_field_rejected(self):
        registry = BlobSchemaRegistry()
        registry.schema("clip").add(FieldSpec(name="a", offset=0, type="uint8"))
        with pytest.raises(ValueError):
            registry.schema("clip").add(FieldSpec(name="a", offset=1, type="uint8"))

    def test_bytes_field_requires_size(self):
        with pytest.raises(ValueError):
            FieldSpec(name="x", offset=0, type="bytes")
