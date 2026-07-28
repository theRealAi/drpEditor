"""Example: disable AI Super Scale on every clip of a project.

Equivalent to `drp --signatures <db> repair-ai-upscale in.drp out.drp`,
shown here as library code.

Usage:
    python examples/disable_super_scale.py signatures.json in.drp out.drp
"""

from __future__ import annotations

import sys

import drp_editor
from drp_editor.repairs import get_repair
from drp_editor.utils import setup_logging


def main() -> None:
    setup_logging(verbosity=1)
    if len(sys.argv) != 4:
        raise SystemExit("usage: disable_super_scale.py <signatures.json> <in.drp> <out.drp>")
    signatures, source, target = sys.argv[1:4]

    drp_editor.default_registry.load_json(signatures)
    project = drp_editor.open_project(source)

    repair = get_repair("ai-upscale")
    findings = repair.scan(project)
    for finding in findings:
        print(f"found: {finding.description}")

    patches = repair.repair(project)
    issues = repair.validate(project)
    if issues:
        raise SystemExit(f"post-repair validation failed: {issues}")

    project.save(target)
    print(f"applied {len(patches)} patch(es), saved {target}")


if __name__ == "__main__":
    main()
