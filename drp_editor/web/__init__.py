"""Local web UI for browsing and repairing .drp projects.

Launch with ``drp ui [project.drp]`` and open http://127.0.0.1:8765.
The server is a thin JSON layer over :class:`drp_editor.models.Project`;
all data-preservation guarantees of the library apply unchanged.
"""

from .server import create_app

__all__ = ["create_app"]
