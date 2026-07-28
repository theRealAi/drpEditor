"""Tests for the archive layer: round-trips, verification, corruption."""

from __future__ import annotations

from pathlib import Path

import pytest

from drp_editor.archive import RAW_XML_MEMBER, DRPArchive
from drp_editor.exceptions import ArchiveError

from .conftest import BINARY_MEMBER, SAMPLE_XML, build_drp


class TestOpen:
    def test_open_lists_members(self, drp_file: Path):
        with DRPArchive.open(drp_file) as archive:
            assert archive.namelist() == ["project.xml", "render/thumbnail.bin"]

    def test_read_member(self, drp_file: Path):
        with DRPArchive.open(drp_file) as archive:
            assert archive.read("project.xml") == SAMPLE_XML
            assert archive.read("render/thumbnail.bin") == BINARY_MEMBER

    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(ArchiveError):
            DRPArchive.open(tmp_path / "nope.drp")

    def test_garbage_file_raises(self, tmp_path: Path):
        bad = tmp_path / "bad.drp"
        bad.write_bytes(b"\x00\x01\x02 not an archive")
        with pytest.raises(ArchiveError):
            DRPArchive.open(bad)

    def test_missing_member_raises(self, drp_file: Path):
        with DRPArchive.open(drp_file) as archive, pytest.raises(ArchiveError):
            archive.read("nope.bin")

    def test_bare_xml_wrapped(self, tmp_path: Path):
        bare = tmp_path / "bare.drp"
        bare.write_bytes(SAMPLE_XML)
        with DRPArchive.open(bare) as archive:
            assert not archive.is_zip
            assert archive.namelist() == [RAW_XML_MEMBER]
            assert archive.read(RAW_XML_MEMBER) == SAMPLE_XML


class TestRoundTrip:
    def test_untouched_archive_saves_byte_identical(self, drp_file: Path, tmp_path: Path):
        out = tmp_path / "copy.drp"
        with DRPArchive.open(drp_file) as archive:
            archive.save(out)
        assert out.read_bytes() == drp_file.read_bytes()

    def test_replacing_with_identical_bytes_stays_clean(self, drp_file: Path):
        with DRPArchive.open(drp_file) as archive:
            archive.replace("project.xml", SAMPLE_XML)
            assert not archive.dirty

    def test_modified_archive_preserves_other_members(self, drp_file: Path, tmp_path: Path):
        out = tmp_path / "mod.drp"
        with DRPArchive.open(drp_file) as archive:
            archive.replace("project.xml", b"<Project/>")
            archive.save(out)
        with DRPArchive.open(out) as reopened:
            assert reopened.read("project.xml") == b"<Project/>"
            assert reopened.read("render/thumbnail.bin") == BINARY_MEMBER
            assert reopened.namelist() == ["project.xml", "render/thumbnail.bin"]

    def test_add_member(self, drp_file: Path, tmp_path: Path):
        out = tmp_path / "added.drp"
        with DRPArchive.open(drp_file) as archive:
            archive.add("notes.txt", b"hello")
            archive.save(out)
        with DRPArchive.open(out) as reopened:
            assert reopened.read("notes.txt") == b"hello"

    def test_replace_missing_member_raises(self, drp_file: Path):
        with DRPArchive.open(drp_file) as archive, pytest.raises(ArchiveError):
            archive.replace("ghost.xml", b"")

    def test_bare_xml_round_trip(self, tmp_path: Path):
        bare = tmp_path / "bare.drp"
        bare.write_bytes(SAMPLE_XML)
        out = tmp_path / "copy.drp"
        with DRPArchive.open(bare) as archive:
            archive.save(out)
        assert out.read_bytes() == SAMPLE_XML


class TestVerifyAndExtract:
    def test_verify_clean(self, drp_file: Path):
        with DRPArchive.open(drp_file) as archive:
            assert archive.verify() == []

    def test_verify_detects_corruption(self, tmp_path: Path):
        path = build_drp(tmp_path / "corrupt.drp")
        raw = bytearray(path.read_bytes())
        # Flip bytes in the middle of the compressed stream.
        raw[60:64] = b"\xde\xad\xbe\xef"
        path.write_bytes(bytes(raw))
        with DRPArchive.open(path) as archive:
            assert archive.verify() != []

    def test_extract(self, drp_file: Path, tmp_path: Path):
        dest = tmp_path / "extracted"
        with DRPArchive.open(drp_file) as archive:
            written = archive.extract(dest)
        assert (dest / "project.xml").read_bytes() == SAMPLE_XML
        assert (dest / "render" / "thumbnail.bin").read_bytes() == BINARY_MEMBER
        assert len(written) == 2
