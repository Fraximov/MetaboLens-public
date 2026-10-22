#!/usr/bin/env python3
"""Build cached MS-DIAL-compatible RT correction review artifacts."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from rt_correction import build_correction_plan, build_preview


def run(is_qc_dir: Path, outdir: Path) -> None:
    with (is_qc_dir / "is_qc_targets.tsv").open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    traces = json.loads((is_qc_dir / "is_qc_traces.json").read_text(encoding="utf-8"))
    plan = build_correction_plan(rows, traces)
    preview = build_preview(plan, traces)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "rt_correction_plan.json").write_text(
        json.dumps(plan, indent=2, sort_keys=True), encoding="utf-8"
    )
    (outdir / "rt_correction_preview.json").write_text(
        json.dumps(preview, indent=2, sort_keys=True), encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build an IS-QC RT correction plan")
    parser.add_argument("--is-qc-dir", required=True, type=Path)
    parser.add_argument("--outdir", required=True, type=Path)
    args = parser.parse_args()
    run(args.is_qc_dir, args.outdir)


if __name__ == "__main__":
    main()
