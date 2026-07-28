"""Tests for the binary reader/writer framework."""

from __future__ import annotations

import pytest

from drp_editor.binary import BinaryReader, BinaryWriter
from drp_editor.exceptions import BinaryDecodeError


class TestBinaryReader:
    def test_integers_little_endian(self):
        reader = BinaryReader(b"\x01\x00\x02\x00\x00\x00\xff")
        assert reader.read_uint16() == 1
        assert reader.read_uint32() == 2
        assert reader.read_uint8() == 255
        assert reader.eof()

    def test_integers_big_endian(self):
        reader = BinaryReader(b"\x00\x01", endianness="big")
        assert reader.read_uint16() == 1

    def test_signed(self):
        reader = BinaryReader(b"\xff\xff\xff\xff")
        assert reader.read_int32() == -1

    def test_floats(self):
        writer = BinaryWriter()
        writer.write_float(1.5)
        writer.write_double(-2.25)
        reader = BinaryReader(writer.getvalue())
        assert reader.read_float() == 1.5
        assert reader.read_double() == -2.25

    def test_seek_tell_remaining(self):
        reader = BinaryReader(b"abcdef")
        reader.seek(4)
        assert reader.tell() == 4
        assert reader.remaining() == 2
        reader.skip(2)
        assert reader.eof()

    def test_read_past_end_raises(self):
        reader = BinaryReader(b"ab")
        with pytest.raises(BinaryDecodeError):
            reader.read_uint32()

    def test_seek_out_of_bounds_raises(self):
        with pytest.raises(BinaryDecodeError):
            BinaryReader(b"ab").seek(3)

    def test_length_prefixed_string_round_trip(self):
        writer = BinaryWriter()
        writer.write_string("héllo")
        reader = BinaryReader(writer.getvalue())
        assert reader.read_string() == "héllo"

    def test_cstring_round_trip(self):
        writer = BinaryWriter()
        writer.write_cstring("abc")
        writer.write_uint8(7)
        reader = BinaryReader(writer.getvalue())
        assert reader.read_cstring() == "abc"
        assert reader.read_uint8() == 7

    def test_unterminated_cstring_raises(self):
        with pytest.raises(BinaryDecodeError):
            BinaryReader(b"abc").read_cstring()


class TestBinaryWriter:
    def test_all_integer_widths_round_trip(self):
        writer = BinaryWriter()
        writer.write_uint8(0x12)
        writer.write_int8(-1)
        writer.write_uint16(0x1234)
        writer.write_int16(-2)
        writer.write_uint32(0x12345678)
        writer.write_int32(-3)
        writer.write_uint64(0x123456789ABCDEF0)
        writer.write_int64(-4)
        reader = BinaryReader(writer.getvalue())
        assert reader.read_uint8() == 0x12
        assert reader.read_int8() == -1
        assert reader.read_uint16() == 0x1234
        assert reader.read_int16() == -2
        assert reader.read_uint32() == 0x12345678
        assert reader.read_int32() == -3
        assert reader.read_uint64() == 0x123456789ABCDEF0
        assert reader.read_int64() == -4

    def test_overflow_raises(self):
        with pytest.raises(BinaryDecodeError):
            BinaryWriter().write_uint8(256)

    def test_tell(self):
        writer = BinaryWriter()
        writer.write_uint32(0)
        assert writer.tell() == 4
