"""Tests for patch objects and the patch log."""

from __future__ import annotations

import pytest

from drp_editor.exceptions import PatchError
from drp_editor.patch import Patch, PatchLog


class TestPatch:
    def test_round_trip_dict(self):
        patch = Patch(
            object_type="clip",
            object_id="c-001",
            property="name",
            old_value="a",
            new_value="b",
        )
        assert Patch.from_dict(patch.to_dict()) == patch

    def test_malformed_record_raises(self):
        with pytest.raises(PatchError):
            Patch.from_dict({"bogus": True})

    def test_timestamp_is_iso(self):
        patch = Patch("clip", "id", "name", "a", "b")
        assert "T" in patch.timestamp


class TestPatchLog:
    def test_record_and_iterate(self):
        log = PatchLog()
        log.record(object_type="clip", object_id="1", property="name", old_value="a", new_value="b")
        log.record(object_type="clip", object_id="2", property="name", old_value="c", new_value="d")
        assert len(log) == 2
        assert [p.object_id for p in log] == ["1", "2"]

    def test_pop(self):
        log = PatchLog()
        assert log.pop() is None
        log.record(object_type="clip", object_id="1", property="name", old_value="a", new_value="b")
        assert log.pop().object_id == "1"
        assert len(log) == 0

    def test_save_and_load(self, tmp_path):
        log = PatchLog()
        log.record(object_type="clip", object_id="1", property="name", old_value="a", new_value="b")
        path = tmp_path / "patches.json"
        log.save(path)
        loaded = PatchLog.load(path)
        assert len(loaded) == 1
        assert loaded.patches[0].new_value == "b"

    def test_load_invalid_raises(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("not json", encoding="utf-8")
        with pytest.raises(PatchError):
            PatchLog.load(bad)
