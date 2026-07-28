"""Tests for the web UI backend (FastAPI JSON layer)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from drp_editor.web.server import create_app
from drp_editor.web.session import Session

from .conftest import SAMPLE_XML, build_drp


@pytest.fixture()
def client(tmp_path: Path):
    return TestClient(create_app(Session()))


@pytest.fixture()
def opened(client: TestClient, drp_file: Path):
    response = client.post("/api/open", json={"path": str(drp_file)})
    assert response.status_code == 200, response.text
    return response.json()


class TestOpen:
    def test_open_by_path(self, opened):
        assert opened["name"] == "Demo Project"
        assert len(opened["timelines"]) == 2
        assert opened["clip_count"] == 3
        assert opened["validation"] == []

    def test_open_missing_path_is_400(self, client: TestClient):
        response = client.post("/api/open", json={"path": "Z:/nope/ghost.drp"})
        assert response.status_code == 400
        assert "not found" in response.json()["detail"]

    def test_upload(self, client: TestClient, drp_file: Path):
        response = client.post(
            "/api/upload",
            files={"file": ("sample.drp", drp_file.read_bytes(), "application/octet-stream")},
        )
        assert response.status_code == 200
        assert response.json()["name"] == "Demo Project"

    def test_no_project_is_400(self, client: TestClient):
        assert client.get("/api/project").status_code == 400

    def test_broken_project_reports_validation(self, client: TestClient, tmp_path: Path):
        broken = build_drp(
            tmp_path / "broken.drp",
            xml=SAMPLE_XML.replace(b'Source="m-002"', b'Source="m-999"'),
        )
        response = client.post("/api/open", json={"path": str(broken)})
        assert response.status_code == 200
        codes = [i["code"] for i in response.json()["validation"]]
        assert "broken-reference" in codes


class TestBrowse:
    def test_list_all_clips(self, client: TestClient, opened):
        clips = client.get("/api/clips").json()
        assert {c["uuid"] for c in clips} == {"c-001", "c-002", "c-003"}

    def test_filter_by_timeline(self, client: TestClient, opened):
        clips = client.get("/api/clips", params={"timeline": "t-002"}).json()
        assert [c["uuid"] for c in clips] == ["c-003"]

    def test_search(self, client: TestClient, opened):
        clips = client.get("/api/clips", params={"q": "camera001"}).json()
        assert [c["uuid"] for c in clips] == ["c-001"]

    def test_clip_details_include_blob(self, client: TestClient, opened):
        detail = client.get("/api/object/c-001").json()
        assert detail["type"] == "clip"
        assert detail["blob"]["size"] == 80
        assert "00000040" in detail["blob"]["hex_dump"]

    def test_unknown_token_is_400(self, client: TestClient, opened):
        assert client.get("/api/object/ghost").status_code == 400


class TestMutations:
    def test_set_property(self, client: TestClient, opened):
        response = client.post(
            "/api/set-property",
            json={"token": "c-001", "property": "name", "value": "Renamed.mov"},
        )
        assert response.status_code == 200
        assert response.json()["patch"]["new_value"] == "Renamed.mov"
        detail = client.get("/api/object/c-001").json()
        assert detail["name"] == "Renamed.mov"

    def test_remove_clips(self, client: TestClient, opened):
        response = client.post("/api/remove", json={"tokens": ["c-001", "c-002"]})
        assert response.status_code == 200
        assert len(response.json()["removed"]) == 2
        clips = client.get("/api/clips").json()
        assert [c["uuid"] for c in clips] == ["c-003"]

    def test_remove_bad_token_removes_nothing(self, client: TestClient, opened):
        response = client.post("/api/remove", json={"tokens": ["c-001", "ghost"]})
        assert response.status_code == 400
        assert len(client.get("/api/clips").json()) == 3

    def test_remove_timeline(self, client: TestClient, opened):
        client.post("/api/remove", json={"tokens": ["t-002"]})
        project = client.get("/api/project").json()
        assert [t["uuid"] for t in project["timelines"]] == ["t-001"]
        assert project["clip_count"] == 2

    def test_undo(self, client: TestClient, opened):
        client.post("/api/remove", json={"tokens": ["c-001"]})
        response = client.post("/api/undo")
        assert response.json()["undone"]["property"] == "__removed__"
        assert len(client.get("/api/clips").json()) == 3

    def test_undo_nothing(self, client: TestClient, opened):
        assert client.post("/api/undo").json()["undone"] is None


class TestSaving:
    def test_save_version_increments(self, client: TestClient, opened, drp_file: Path):
        client.post("/api/remove", json={"tokens": ["c-002"]})
        first = client.post("/api/save-version").json()
        assert first["path"].endswith("sample_v2.drp")
        assert Path(first["path"]).exists()

        client.post("/api/remove", json={"tokens": ["c-001"]})
        second = client.post("/api/save-version").json()
        assert second["path"].endswith("sample_v3.drp")
        assert [Path(v).name for v in second["versions"]] == ["sample_v2.drp", "sample_v3.drp"]

    def test_saved_version_is_valid_drp(self, client: TestClient, opened, drp_file: Path):
        client.post("/api/remove", json={"tokens": ["c-002"]})
        path = Path(client.post("/api/save-version").json()["path"])
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("project.xml")
        assert b"c-002" not in xml
        assert b"c-001" in xml

    def test_download(self, client: TestClient, opened):
        response = client.get("/api/download")
        assert response.status_code == 200
        assert response.headers["content-disposition"].endswith('_edited.drp"')
        assert response.content[:2] == b"PK"

    def test_static_index_served(self, client: TestClient):
        response = client.get("/")
        assert response.status_code == 200
        assert b"drp" in response.content
