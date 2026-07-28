"""FastAPI backend for the drp_editor web UI.

A thin JSON layer over the library. Every mutation goes through the
normal :class:`~drp_editor.models.Project` API, so patches are recorded,
undo works, and unknown data is preserved byte-for-byte.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..exceptions import BinaryDecodeError, DRPError
from ..models import Clip, MediaItem, Timeline
from ..utils import hex_dump
from ..validation import Validator
from .session import Session

__all__ = ["create_app"]

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"

#: Cap for hex dumps sent to the browser (bytes).
_HEX_DUMP_LIMIT = 4096


class OpenRequest(BaseModel):
    """Body of POST /api/open."""

    path: str


class SetPropertyRequest(BaseModel):
    """Body of POST /api/set-property."""

    token: str
    property: str
    value: str


class RemoveRequest(BaseModel):
    """Body of POST /api/remove."""

    tokens: list[str]


def create_app(session: Session | None = None) -> FastAPI:
    """Build the FastAPI application around one :class:`Session`."""
    state = session or Session()
    app = FastAPI(title="drp-editor", docs_url=None, redoc_url=None)

    @app.exception_handler(DRPError)
    async def drp_error_handler(_request: Request, exc: DRPError) -> JSONResponse:
        logger.debug("request failed", exc_info=exc)
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    # -- serializers (token order must match Session.reindex) ----------

    def timeline_summary(token: str, timeline: Timeline) -> dict[str, Any]:
        return {
            "token": token,
            "uuid": timeline.uuid,
            "name": timeline.name or "(unnamed timeline)",
            "clip_count": len(timeline.clips),
        }

    def clip_summary(token: str, clip: Clip) -> dict[str, Any]:
        return {
            "token": token,
            "uuid": clip.uuid,
            "name": clip.name or "(unnamed clip)",
            "source": clip.source,
            "timeline_uuid": clip.timeline_uuid,
            "has_blob": clip.blob_holder is not None,
        }

    def media_summary(token: str, item: MediaItem) -> dict[str, Any]:
        return {
            "token": token,
            "uuid": item.uuid,
            "name": item.name or "(unnamed media)",
            "file_path": item.file_path,
        }

    def indexed_timelines() -> list[tuple[str, Timeline]]:
        project = state.require_project()
        return [(t.uuid or f"timeline#{i}", t) for i, t in enumerate(project.timelines)]

    def indexed_clips() -> list[tuple[str, Clip]]:
        project = state.require_project()
        return [(c.uuid or f"clip#{i}", c) for i, c in enumerate(project.all_clips())]

    def indexed_media() -> list[tuple[str, MediaItem]]:
        project = state.require_project()
        return [(m.uuid or f"media#{i}", m) for i, m in enumerate(project.media_pool)]

    def project_state() -> dict[str, Any]:
        project = state.require_project()
        issues = Validator(project).run()
        return {
            "name": project.name,
            "source_path": str(state.source_path) if state.source_path else None,
            "xml_member": project.xml_member,
            "archive_members": project.archive.namelist(),
            "timelines": [timeline_summary(tok, t) for tok, t in indexed_timelines()],
            "media": [media_summary(tok, m) for tok, m in indexed_media()],
            "settings": project.settings.to_dict() if project.settings else {},
            "clip_count": len(project.all_clips()),
            "unattached_count": len(project.unattached_clips),
            "validation": [
                {
                    "severity": i.severity,
                    "code": i.code,
                    "message": i.message,
                    "object_id": i.object_id,
                }
                for i in issues
            ],
            "patches": [p.to_dict() for p in project.patch_log],
            "versions": [str(v) for v in state.versions],
        }

    # -- endpoints ----------------------------------------------------------

    @app.post("/api/open")
    def open_project_endpoint(body: OpenRequest) -> dict[str, Any]:
        """Open a .drp file from a local path."""
        path = Path(body.path).expanduser()
        if not path.exists():
            raise DRPError(f"file not found: {path}")
        state.open(path)
        return project_state()

    @app.post("/api/upload")
    async def upload_endpoint(file: UploadFile) -> dict[str, Any]:
        """Open an uploaded .drp file."""
        data = await file.read()
        state.open_bytes(data, file.filename or "uploaded.drp")
        return project_state()

    @app.get("/api/project")
    def project_endpoint() -> dict[str, Any]:
        """Full project state for the sidebar and status bar."""
        return project_state()

    @app.get("/api/clips")
    def clips_endpoint(timeline: str = "", q: str = "") -> list[dict[str, Any]]:
        """List clips, optionally filtered by timeline token and search text."""
        needle = q.lower()
        rows: list[dict[str, Any]] = []
        allowed: set[int] | None
        if timeline == "unattached":
            allowed = {id(c) for c in state.require_project().unattached_clips}
        elif timeline:
            resolved = state.resolve(timeline)
            if not isinstance(resolved, Timeline):
                raise DRPError(f"{timeline!r} is not a timeline")
            allowed = {id(c) for c in resolved.clips}
        else:
            allowed = None
        for token, clip in indexed_clips():
            if allowed is not None and id(clip) not in allowed:
                continue
            if needle and needle not in clip.name.lower() and needle not in clip.uuid.lower():
                continue
            rows.append(clip_summary(token, clip))
        return rows

    @app.get("/api/object/{token}")
    def object_endpoint(token: str) -> dict[str, Any]:
        """Details for one clip / timeline / media item."""
        obj = state.resolve(token)
        if isinstance(obj, Clip):
            detail: dict[str, Any] = clip_summary(token, obj)
            detail["type"] = "clip"
            detail["editable"] = [p for p in ("name", "source") if p in obj.carriers]
            detail["blob"] = _blob_detail(obj)
            return detail
        if isinstance(obj, Timeline):
            detail = timeline_summary(token, obj)
            detail["type"] = "timeline"
            detail["editable"] = [p for p in ("name",) if p in obj.carriers]
            return detail
        detail = media_summary(token, obj)
        detail["type"] = "media"
        detail["editable"] = [p for p in ("name", "file_path") if p in obj.carriers]
        return detail

    def _blob_detail(clip: Clip) -> dict[str, Any] | None:
        if clip.blob_holder is None:
            return None
        try:
            blob = clip.blob_holder.get()
        except BinaryDecodeError as exc:
            return {"error": str(exc)}
        fields = {
            name: (value.hex() if isinstance(value, bytes) else value)
            for name, value in blob.decode().items()
        }
        raw = blob.raw_bytes
        return {
            "size": len(raw),
            "known_fields": fields,
            "hex_dump": hex_dump(raw[:_HEX_DUMP_LIMIT]),
            "truncated": len(raw) > _HEX_DUMP_LIMIT,
        }

    @app.post("/api/set-property")
    def set_property_endpoint(body: SetPropertyRequest) -> dict[str, Any]:
        """Edit one property of one object (records a patch)."""
        project = state.require_project()
        obj = state.resolve(body.token)
        patch = project.set_property(obj, body.property, body.value)
        state.reindex()
        return {"patch": patch.to_dict()}

    @app.post("/api/remove")
    def remove_endpoint(body: RemoveRequest) -> dict[str, Any]:
        """Remove one or more objects (each removal is undoable)."""
        project = state.require_project()
        # Resolve everything up front so a bad token removes nothing.
        objects = [state.resolve(token) for token in body.tokens]
        removed = []
        for obj in objects:
            patch = project.remove_object(obj)
            removed.append(patch.to_dict())
        state.reindex()
        return {"removed": removed}

    @app.post("/api/undo")
    def undo_endpoint() -> dict[str, Any]:
        """Revert the most recent change."""
        project = state.require_project()
        patch = project.undo_last()
        state.reindex()
        return {"undone": patch.to_dict() if patch else None}

    @app.post("/api/save-version")
    def save_version_endpoint() -> dict[str, Any]:
        """Save the current state as the next _vN file next to the source."""
        target = state.save_version()
        return {"path": str(target), "versions": [str(v) for v in state.versions]}

    @app.get("/api/download")
    def download_endpoint() -> Response:
        """Download the current state as a .drp file."""
        project = state.require_project()
        project.flush()
        data = project.archive.rebuild()
        stem = state.source_path.stem if state.source_path else "project"
        return Response(
            content=data,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{stem}_edited.drp"'},
        )

    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
    return app
