#!/usr/bin/env python3
"""
Keep the Helm chart's CRDs in step with crds/.

Helm applies everything under a chart's crds/ directory before its templates and
never templates it, so the files are a straight copy.

Usage:
    python tools/sync_chart.py [--check]
"""
import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "crds"
TARGET = ROOT / "charts" / "agentbox" / "crds"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    wanted = {p.name: p.read_text() for p in sorted(SOURCE.glob("*.yaml"))
              if p.name != "kustomization.yaml"}
    current = {p.name: p.read_text() for p in sorted(TARGET.glob("*.yaml"))}

    if args.check:
        if wanted != current:
            print("charts/agentbox/crds is out of date")
            return 1
        print(f"charts/agentbox/crds is up to date ({len(wanted)} CRDs)")
        return 0

    TARGET.mkdir(parents=True, exist_ok=True)
    for stale in set(current) - set(wanted):
        (TARGET / stale).unlink()
    for name, content in wanted.items():
        (TARGET / name).write_text(content)
    print(f"synced {len(wanted)} CRDs into the chart")
    return 0


if __name__ == "__main__":
    sys.exit(main())
