"""Tests for project validation."""

from __future__ import annotations

from pathlib import Path

from drp_editor import DRPArchive, DRPParser
from drp_editor.validation import Validator

from .conftest import SAMPLE_XML, build_drp


def load(path: Path):
    return DRPParser().parse(DRPArchive.open(path))


def issues_for(xml: bytes, tmp_path: Path, **kwargs):
    path = build_drp(tmp_path / "case.drp", xml=xml)
    return Validator(load(path), **kwargs).run()


class TestValidator:
    def test_clean_project(self, drp_file: Path):
        assert Validator(load(drp_file)).run() == []

    def test_duplicate_uuid_detected(self, tmp_path: Path):
        xml = SAMPLE_XML.replace(b'Uuid="c-002"', b'Uuid="c-001"')
        issues = issues_for(xml, tmp_path)
        assert any(i.code == "duplicate-uuid" and i.object_id == "c-001" for i in issues)

    def test_broken_reference_detected(self, tmp_path: Path):
        xml = SAMPLE_XML.replace(b'Source="m-002"', b'Source="m-999"')
        issues = issues_for(xml, tmp_path)
        assert any(i.code == "broken-reference" for i in issues)

    def test_corrupt_blob_detected(self, tmp_path: Path):
        # Replace one blob with odd-length hex.
        xml = SAMPLE_XML.replace(b'Fields="', b'Fields="f', 1)  # makes the first blob odd-length
        issues = issues_for(xml, tmp_path)
        assert any(i.code == "corrupt-blob" for i in issues)

    def test_missing_media_files_flagged(self, tmp_path: Path, drp_file: Path):
        issues = Validator(load(drp_file), check_files=True).run()
        assert any(i.code == "missing-media" for i in issues)

    def test_empty_model_warning(self, tmp_path: Path):
        xml = b'<?xml version="1.0"?>\n<Project Name="Empty"/>\n'
        issues = issues_for(xml, tmp_path)
        assert any(i.code == "empty-model" and i.severity == "warning" for i in issues)
