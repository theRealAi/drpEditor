"""Example: open a .drp file and print its structure.

Usage:
    python examples/inspect_project.py path/to/project.drp
"""

from __future__ import annotations

import sys

import drp_editor
from drp_editor.utils import setup_logging


def main() -> None:
    setup_logging(verbosity=1)
    if len(sys.argv) != 2:
        raise SystemExit("usage: inspect_project.py <project.drp>")

    project = drp_editor.open_project(sys.argv[1])

    print(f"Project: {project.name}")
    print(f"Archive members: {project.archive.namelist()}")

    for timeline in project.timelines:
        print(f"\nTimeline: {timeline.name} ({timeline.uuid})")
        for clip in timeline.clips:
            blob = clip.fields_blob
            blob_info = f"blob {len(blob)} bytes" if blob else "no blob"
            print(f"  Clip: {clip.name} ({clip.uuid}) [{blob_info}]")

    print("\nMedia pool:")
    for item in project.media_pool:
        print(f"  {item.name} -> {item.file_path}")


if __name__ == "__main__":
    main()
