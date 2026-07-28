"""Repair plugin: disable AI Super Scale on every clip.

Corrupted AI Super Scale settings can prevent Resolve from opening a
project at all. This plugin locates the Super Scale field inside each
clip's FieldsBlob and forces it to ``0`` (disabled), touching no other
bytes.

Prerequisite: the blob field must be mapped. The plugin reads the field
named :data:`SUPER_SCALE_FIELD` from the ``"clip"`` blob schema in the
active :class:`~drp_editor.fields_blob.BlobSchemaRegistry`. Ship or load
a signature database (``drp --signatures db.json ...``) that defines it,
e.g.::

    {"clip": [{"name": "super_scale", "offset": 72, "type": "uint8",
               "description": "AI Super Scale mode; 0 = disabled"}]}

Offsets differ between Resolve versions -- use ``drp diff`` on a
before/after export pair to map the field for your version.
"""

from __future__ import annotations

import logging

from ..exceptions import RepairError
from ..models import Project
from ..patch import Patch
from .base import Repair, RepairFinding, register

__all__ = ["SUPER_SCALE_FIELD", "AIUpscaleRepair"]

logger = logging.getLogger(__name__)

#: Conventional field name for the AI Super Scale mode inside clip blobs.
SUPER_SCALE_FIELD = "super_scale"


@register
class AIUpscaleRepair(Repair):
    """Force AI Super Scale off for every clip in the project."""

    name = "ai-upscale"
    description = "Disable AI Super Scale on all clips (fixes corrupted upscale settings)."

    def scan(self, project: Project) -> list[RepairFinding]:
        """Find clips whose Super Scale field is non-zero."""
        findings: list[RepairFinding] = []
        for clip in project.all_clips():
            blob = clip.fields_blob
            if blob is None:
                continue
            spec = blob.known_fields().get(SUPER_SCALE_FIELD)
            if spec is None:
                continue
            value = blob.get_field(SUPER_SCALE_FIELD)
            if value != 0:
                findings.append(
                    RepairFinding(
                        object_id=clip.uuid,
                        description=(
                            f"clip {clip.name!r}: AI Super Scale enabled "
                            f"(value {value!r} at offset 0x{spec.offset:04x})"
                        ),
                        data={"value": value, "offset": spec.offset},
                    )
                )
        if not findings and not self._field_is_mapped(project):
            raise RepairError(
                f"blob field {SUPER_SCALE_FIELD!r} is not mapped in the 'clip' schema; "
                "load a signature database first (see drp_editor.repairs.ai_upscale docs)"
            )
        return findings

    def repair(self, project: Project) -> list[Patch]:
        """Zero the Super Scale field on every affected clip."""
        patches: list[Patch] = []
        for finding in self.scan(project):
            clip = project.find_clip(uuid=finding.object_id)
            if clip is None:
                raise RepairError(f"clip {finding.object_id} vanished during repair")
            patches.append(project.set_blob_field(clip, SUPER_SCALE_FIELD, 0))
            logger.info("disabled AI Super Scale on clip %r", clip.name)
        return patches

    @staticmethod
    def _field_is_mapped(project: Project) -> bool:
        return any(
            blob is not None and SUPER_SCALE_FIELD in blob.known_fields()
            for blob in (clip.fields_blob for clip in project.all_clips())
        )
