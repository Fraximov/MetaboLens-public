#!/usr/bin/env python3
"""Create transient mzML copies with corrected scan start times."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import mmap
import os
import re
import shutil
from pathlib import Path
from typing import Any, NamedTuple

from rt_correction import correct_rt_axis, select_correction_plan


CV_PARAM_RE = re.compile(rb"<cvParam\b[^>]*>")
VALUE_RE = re.compile(rb'\bvalue="([^"]*)"')
UNIT_RE = re.compile(rb'\bunitAccession="([^"]*)"')
CHECKSUM_RE = re.compile(rb"<fileChecksum>([^<]*)</fileChecksum>")
FORMAT_TOLERANCE_MIN = 5e-7


class ScanTimeField(NamedTuple):
    start: int
    end: int
    value: float
    unit_scale_to_minutes: float


def scan_time_fields(buffer) -> list[ScanTimeField]:
    fields = []
    for tag_match in CV_PARAM_RE.finditer(buffer):
        tag = tag_match.group(0)
        if b'accession="MS:1000016"' not in tag:
            continue
        value_match = VALUE_RE.search(tag)
        unit_match = UNIT_RE.search(tag)
        if value_match is None:
            raise ValueError("scan start time cvParam is missing its value")
        unit = unit_match.group(1) if unit_match else b"UO:0000031"
        if unit == b"UO:0000010":
            scale = 1.0 / 60.0
        elif unit == b"UO:0000031":
            scale = 1.0
        else:
            raise ValueError(
                f"unsupported scan start time unit: {unit.decode('ascii', errors='replace')}"
            )
        start = tag_match.start() + value_match.start(1)
        end = tag_match.start() + value_match.end(1)
        fields.append(ScanTimeField(start, end, float(value_match.group(1)), scale))
    if not fields:
        raise ValueError("mzML contains no MS:1000016 scan start times")
    return fields


def scan_times_minutes(buffer) -> list[float]:
    return [field.value * field.unit_scale_to_minutes for field in scan_time_fields(buffer)]


def format_exact_width(value: float, width: int, *, tolerance: float) -> bytes:
    candidates = []
    for precision in range(max(width, 1), -1, -1):
        candidates.extend((f"{value:.{precision}f}", f"{value:.{precision}e}"))
    for candidate in candidates:
        encoded = candidate.encode("ascii")
        if len(encoded) == width and math.isclose(
            float(candidate), value, rel_tol=0.0, abs_tol=tolerance
        ):
            return encoded
    raise ValueError(
        f"corrected RT cannot fit the existing {width}-byte width at required precision"
    )


def _update_checksum(mapped: mmap.mmap) -> None:
    match = CHECKSUM_RE.search(mapped)
    if match is None:
        return
    current = match.group(1)
    if current == b"0":
        return
    if len(current) != 40 or re.fullmatch(rb"[0-9A-Fa-f]{40}", current) is None:
        raise ValueError("unsupported indexed-mzML checksum field")
    digest = hashlib.sha1(mapped[:match.start(1)]).hexdigest().encode("ascii")
    mapped[match.start(1):match.end(1)] = digest


def validate_with_pymzml(path: Path, expected_count: int, *, require_indexed: bool) -> None:
    try:
        import pymzml  # type: ignore

        spectra = list(pymzml.run.Reader(str(path)))
        count = len(spectra)
        if require_indexed and spectra:
            reader = pymzml.run.Reader(str(path))
            reader[spectra[0].ID]
            reader[spectra[-1].ID]
    except Exception as exc:
        raise ValueError(f"pymzML could not validate {path.name}: {exc}") from exc
    if count != expected_count:
        raise ValueError(
            f"pymzML spectrum count changed for {path.name}: expected {expected_count}, found {count}"
        )


def rewrite_mzml_file(
    source: Path,
    destination: Path,
    anchors: list[dict[str, float]],
) -> dict[str, Any]:
    source = Path(source)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(f".{destination.name}.tmp")
    if temp.exists():
        temp.unlink()
    with source.open("rb") as handle, mmap.mmap(
        handle.fileno(), 0, access=mmap.ACCESS_READ
    ) as original:
        fields = scan_time_fields(original)
        original_minutes = [field.value * field.unit_scale_to_minutes for field in fields]
        corrected_minutes = correct_rt_axis(original_minutes, anchors)
        replacements = [
            format_exact_width(
                corrected / field.unit_scale_to_minutes,
                field.end - field.start,
                tolerance=FORMAT_TOLERANCE_MIN / field.unit_scale_to_minutes,
            )
            for field, corrected in zip(fields, corrected_minutes)
        ]
        source_length = len(original)
        source_offsets = re.findall(rb"<offset[^>]*>([^<]+)", original)
        source_is_indexed = b"<indexedmzML" in original

    try:
        shutil.copy2(source, temp)
        with temp.open("r+b") as handle, mmap.mmap(handle.fileno(), 0) as mapped:
            for field, replacement in zip(fields, replacements):
                mapped[field.start:field.end] = replacement
            _update_checksum(mapped)
            mapped.flush()
        corrected_data = temp.read_bytes()
        corrected_readback = scan_times_minutes(corrected_data)
        if len(corrected_data) != source_length:
            raise ValueError("corrected mzML file length changed")
        if re.findall(rb"<offset[^>]*>([^<]+)", corrected_data) != source_offsets:
            raise ValueError("corrected mzML index offsets changed")
        if any(right <= left for left, right in zip(corrected_readback, corrected_readback[1:])):
            raise ValueError("corrected mzML scan times are not strictly increasing")
        if any(
            not math.isclose(actual, expected, rel_tol=0.0, abs_tol=FORMAT_TOLERANCE_MIN)
            for actual, expected in zip(corrected_readback, corrected_minutes)
        ):
            raise ValueError("corrected mzML scan times failed readback validation")
        validate_with_pymzml(temp, len(fields), require_indexed=source_is_indexed)
        os.replace(temp, destination)
    except Exception:
        if temp.exists():
            temp.unlink()
        raise
    return {
        "filename": destination.name,
        "spectrum_count": len(fields),
        "output_path": str(destination),
    }


def rewrite_input_directory(
    input_dir: Path,
    plan: dict[str, Any],
    outdir: Path,
) -> list[dict[str, Any]]:
    if not plan.get("can_apply"):
        raise ValueError("RT correction plan is not eligible for application")
    input_dir = Path(input_dir)
    outdir = Path(outdir)
    models = {
        str(sample.get("input_file") or ""): sample for sample in plan.get("samples", [])
    }
    missing = [filename for filename in models if not (input_dir / filename).is_file()]
    if missing:
        raise ValueError(f"Prepared mzML missing for correction: {', '.join(missing)}")
    reports = []
    for filename, model in models.items():
        if not model.get("eligible"):
            raise ValueError(f"Sample {filename} is not eligible for RT correction")
        reports.append(
            rewrite_mzml_file(input_dir / filename, outdir / filename, model["anchors"])
        )
    return reports


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rewrite mzML scan times from an approved RT correction plan"
    )
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--outdir", required=True, type=Path)
    parser.add_argument("--target-ids-json")
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    selected_target_ids = (
        json.loads(args.target_ids_json)
        if args.target_ids_json is not None else None
    )
    plan = select_correction_plan(plan, selected_target_ids)
    rewrite_input_directory(args.input_dir, plan, args.outdir)


if __name__ == "__main__":
    main()
