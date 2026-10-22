#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


NUM_PEAKS_RE = re.compile(r"^Num Peaks:\s*(\d+)\s*$", re.IGNORECASE | re.MULTILINE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare an MS-DIAL .mdmsp alignment spectra file for SIRIUS MSP import.")
    parser.add_argument("--input", required=True, help="MS-DIAL AlignResult*.mdmsp file.")
    parser.add_argument("--output", required=True, help="SIRIUS-facing .msp file.")
    parser.add_argument("--summary-json", default="", help="Optional adapter summary JSON path.")
    parser.add_argument("--keep-empty", action="store_true", help="Keep records with Num Peaks: 0.")
    return parser.parse_args()


def split_records(text: str) -> list[str]:
    return [record.strip() for record in re.split(r"\n\s*\n", text) if record.strip()]


def num_peaks(record: str) -> int | None:
    match = NUM_PEAKS_RE.search(record)
    return int(match.group(1)) if match else None


def prepare_sirius_msp(source: Path, target: Path, *, keep_empty: bool = False) -> dict[str, int | str]:
    records = split_records(source.read_text(encoding="utf-8", errors="ignore"))
    kept: list[str] = []
    skipped_empty = 0
    for record in records:
        peaks = num_peaks(record)
        if peaks == 0 and not keep_empty:
            skipped_empty += 1
            continue
        kept.append(record)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(("\n\n".join(kept) + "\n") if kept else "", encoding="utf-8")
    return {
        "source": str(source),
        "target": str(target),
        "records_read": len(records),
        "records_written": len(kept),
        "records_skipped_empty": skipped_empty,
    }


def main() -> None:
    args = parse_args()
    summary = prepare_sirius_msp(Path(args.input), Path(args.output), keep_empty=args.keep_empty)
    if args.summary_json:
        summary_path = Path(args.summary_json)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
