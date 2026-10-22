#!/usr/bin/env python3
"""Extract internal-standard EICs from prepared mzML inputs."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from statistics import mean, stdev
from typing import Any


NAME_ALIASES = {"name", "metabolite", "compound", "target", "target_name"}
MZ_ALIASES = {"m/z(da)", "m/z", "mz", "mz(da)", "mz_da", "m_z_da", "precursor_mz"}
RT_ALIASES = {"rt (min)", "rt(min)", "rt", "rt_min", "retention time", "retention_time", "retention time (min)"}
TRACE_MAX_POINTS = 300


def parse_target_file(path: Path) -> list[dict[str, Any]]:
    """Read the facility-style tab-separated IS target file."""

    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if not reader.fieldnames:
            raise ValueError("IS target file is empty or missing a header")
        field_map = _target_field_map(reader.fieldnames)
        targets: list[dict[str, Any]] = []
        for index, row in enumerate(reader, start=1):
            name = str(row.get(field_map["name"]) or "").strip()
            if not name:
                continue
            try:
                mz = float(str(row.get(field_map["mz"]) or "").strip())
                rt_min = float(str(row.get(field_map["rt"]) or "").strip())
            except ValueError as exc:
                raise ValueError(f"Invalid numeric m/z or RT in IS target row {index}: {name}") from exc
            targets.append(
                {
                    "target_id": index,
                    "name": name,
                    "mz": mz,
                    "rt_min": rt_min,
                }
            )
    if not targets:
        raise ValueError("IS target file did not contain any usable target rows")
    return targets


def score_detection(
    *,
    expected_rt_min: float,
    observed_rt_min: float | None,
    apex_intensity: float,
    rt_tolerance_min: float,
    min_peak_height: float,
) -> dict[str, Any]:
    """Apply the v1 IS-QC pass/fail rule for one sample-target pair."""

    if observed_rt_min is None:
        return {"passed": False, "status": "fail", "rt_error_min": None, "message": "No MS1 signal found in RT window"}
    rt_error = observed_rt_min - expected_rt_min
    messages: list[str] = []
    if apex_intensity < min_peak_height:
        messages.append(f"Peak height below {min_peak_height:g}")
    if abs(rt_error) > rt_tolerance_min:
        messages.append(f"RT error {rt_error:.3f} min exceeds +/- {rt_tolerance_min:g} min")
    passed = not messages
    return {
        "passed": passed,
        "status": "pass" if passed else "fail",
        "rt_error_min": rt_error,
        "message": "OK" if passed else "; ".join(messages),
    }


def summarize_qc_rows(rows: list[dict[str, Any]], thresholds: dict[str, Any]) -> dict[str, Any]:
    """Summarize pair-level rows into operator-facing target and overall status."""

    min_sample_fraction = float(thresholds["is_qc_min_sample_pass_fraction"])
    min_target_fraction = float(thresholds["is_qc_min_target_pass_fraction"])
    max_qc_cv = float(thresholds["is_qc_max_qc_cv_percent"])
    target_names = sorted({str(row.get("target_name") or "") for row in rows if row.get("target_name")})
    target_statuses: dict[str, Any] = {}

    for target_name in target_names:
        target_rows = [row for row in rows if row.get("target_name") == target_name]
        pass_fraction = sum(1 for row in target_rows if _truthy(row.get("passed"))) / len(target_rows) if target_rows else 0.0
        qc_rows = [row for row in target_rows if _sample_type(row.get("sample_type")) == "qc"]
        qc_cv_percent: float | None = None
        qc_cv_status = "not_applicable"
        if len(qc_rows) >= 2:
            intensities = [float(row.get("apex_intensity") or 0.0) for row in qc_rows]
            avg = mean(intensities)
            qc_cv_percent = (stdev(intensities) / avg * 100.0) if avg else None
            qc_cv_status = "pass" if qc_cv_percent is not None and qc_cv_percent <= max_qc_cv else "fail"
        sample_fraction_pass = pass_fraction >= min_sample_fraction
        qc_cv_pass = qc_cv_status in {"pass", "not_applicable"}
        target_statuses[target_name] = {
            "status": "pass" if sample_fraction_pass and qc_cv_pass else "fail",
            "sample_pass_fraction": pass_fraction,
            "passing_sample_count": sum(1 for row in target_rows if _truthy(row.get("passed"))),
            "sample_count": len(target_rows),
            "qc_cv_percent": qc_cv_percent,
            "qc_cv_status": qc_cv_status,
        }

    passing_targets = sum(1 for status in target_statuses.values() if status["status"] == "pass")
    target_pass_fraction = passing_targets / len(target_statuses) if target_statuses else 0.0
    overall_status = "pass" if target_pass_fraction >= min_target_fraction else "fail"
    message = (
        f"{passing_targets}/{len(target_statuses)} internal-standard target(s) passed QC."
        if target_statuses
        else "No internal-standard targets were evaluated."
    )
    return {
        "overall_status": overall_status,
        "message": message,
        "target_pass_fraction": target_pass_fraction,
        "target_statuses": target_statuses,
        "counts": {
            "targets": len(target_statuses),
            "passing_targets": passing_targets,
            "sample_target_pairs": len(rows),
            "passing_sample_target_pairs": sum(1 for row in rows if _truthy(row.get("passed"))),
        },
    }


def downsample_points(points: list[dict[str, Any]], max_points: int = TRACE_MAX_POINTS) -> list[dict[str, Any]]:
    """Return evenly spaced points while keeping the first and last point."""

    if max_points <= 0 or len(points) <= max_points:
        return list(points)
    if max_points == 1:
        return [points[0]]
    last = len(points) - 1
    indexes = sorted({round(index * last / (max_points - 1)) for index in range(max_points)})
    return [points[index] for index in indexes]


def run_extraction(args: argparse.Namespace) -> None:
    targets = parse_target_file(Path(args.targets))
    samples = resolve_samples(Path(args.input_dir), Path(args.samplesheet))
    thresholds = {
        "is_qc_mz_tolerance_da": float(args.mz_tolerance_da),
        "is_qc_rt_tolerance_min": float(args.rt_tolerance_min),
        "is_qc_min_peak_height": float(args.min_peak_height),
        "is_qc_min_sample_pass_fraction": float(args.min_sample_pass_fraction),
        "is_qc_min_target_pass_fraction": float(args.min_target_pass_fraction),
        "is_qc_max_qc_cv_percent": float(args.max_qc_cv_percent),
    }
    rows: list[dict[str, Any]] = []
    traces: list[dict[str, Any]] = []

    for target in targets:
        for sample in samples:
            points = extract_trace_points(
                mzml_path=sample["path"],
                target_mz=float(target["mz"]),
                expected_rt_min=float(target["rt_min"]),
                mz_tolerance_da=thresholds["is_qc_mz_tolerance_da"],
                rt_tolerance_min=thresholds["is_qc_rt_tolerance_min"],
            )
            apex = apex_from_points(points)
            score = score_detection(
                expected_rt_min=float(target["rt_min"]),
                observed_rt_min=apex["observed_rt_min"],
                apex_intensity=apex["apex_intensity"],
                rt_tolerance_min=thresholds["is_qc_rt_tolerance_min"],
                min_peak_height=thresholds["is_qc_min_peak_height"],
            )
            row = {
                "target_id": target["target_id"],
                "target_name": target["name"],
                "expected_mz": target["mz"],
                "expected_rt_min": target["rt_min"],
                "sample_name": sample["sample_name"],
                "sample_type": sample["sample_type"],
                "input_file": sample["input_file"],
                "observed_rt_min": apex["observed_rt_min"],
                "rt_error_min": score["rt_error_min"],
                "apex_intensity": apex["apex_intensity"],
                "status": score["status"],
                "passed": score["passed"],
                "message": score["message"],
            }
            rows.append(row)
            traces.append(
                {
                    "target_id": target["target_id"],
                    "target_name": target["name"],
                    "expected_mz": target["mz"],
                    "expected_rt_min": target["rt_min"],
                    "sample_name": sample["sample_name"],
                    "sample_type": sample["sample_type"],
                    "input_file": sample["input_file"],
                    "observed_rt_min": apex["observed_rt_min"],
                    "apex_intensity": apex["apex_intensity"],
                    "passed": score["passed"],
                    "points": downsample_points(points, TRACE_MAX_POINTS),
                }
            )

    summary = summarize_qc_rows(rows, thresholds)
    summary["thresholds"] = thresholds
    summary["sample_count"] = len(samples)
    summary["target_count"] = len(targets)
    summary["operator_message"] = (
        "IS-QC passed. Continue if manual review agrees."
        if summary["overall_status"] == "pass"
        else "IS-QC failed automatic thresholds. Add an operator note to continue after manual review."
    )
    write_outputs(Path(args.outdir), summary, rows, targets, samples, traces, thresholds)


def resolve_samples(input_dir: Path, samplesheet: Path) -> list[dict[str, Any]]:
    if not input_dir.exists():
        raise ValueError(f"Prepared input directory does not exist: {input_dir}")
    file_by_lower_name = {path.name.lower(): path for path in input_dir.iterdir() if path.is_file()}
    mzml_files = [path for path in file_by_lower_name.values() if path.suffix.lower() == ".mzml"]
    unsupported_files = [path for path in file_by_lower_name.values() if path.suffix.lower() == ".abf"]
    if not mzml_files and unsupported_files:
        raise ValueError("IS-QC v1 requires mzML inputs; selected prepared inputs contain only unsupported ABF files")
    if not mzml_files:
        raise ValueError("IS-QC v1 requires at least one prepared mzML input")

    rows = read_samplesheet(samplesheet)
    if not rows:
        return [
            {
                "sample_name": path.stem,
                "sample_type": "",
                "input_file": path.name,
                "path": path,
            }
            for path in sorted(mzml_files)
        ]

    samples: list[dict[str, Any]] = []
    for row in rows:
        input_filename = str(row.get("staged_filename") or row.get("input_filename") or "").strip()
        candidates = prepared_input_candidates(input_filename)
        prepared_path = next((file_by_lower_name[name.lower()] for name in candidates if name.lower() in file_by_lower_name), None)
        if prepared_path is None or prepared_path.suffix.lower() != ".mzml":
            continue
        samples.append(
            {
                "sample_name": str(row.get("sample_name") or Path(input_filename).stem or prepared_path.stem),
                "sample_type": str(row.get("sample_type") or "").strip().lower(),
                "input_file": prepared_path.name,
                "path": prepared_path,
            }
        )
    if not samples:
        raise ValueError("IS-QC v1 could not match samplesheet rows to prepared mzML inputs")
    return samples


def read_samplesheet(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def prepared_input_candidates(input_filename: str) -> list[str]:
    source = Path(input_filename).name
    if not source:
        return []
    suffix = Path(source).suffix.lower()
    if suffix == ".raw":
        return [f"{Path(source).stem}.mzML", f"{Path(source).stem}.mzml"]
    return [source]


def extract_trace_points(
    *,
    mzml_path: Path,
    target_mz: float,
    expected_rt_min: float,
    mz_tolerance_da: float,
    rt_tolerance_min: float,
) -> list[dict[str, Any]]:
    try:
        import pymzml  # type: ignore
    except ImportError as exc:
        raise RuntimeError("pymzML is required for IS-QC mzML extraction") from exc

    lower_mz = target_mz - mz_tolerance_da
    upper_mz = target_mz + mz_tolerance_da
    rt_window = max(rt_tolerance_min * 2.0, 0.75)
    lower_rt = expected_rt_min - rt_window
    upper_rt = expected_rt_min + rt_window
    points: list[dict[str, Any]] = []

    reader = pymzml.run.Reader(str(mzml_path))
    for spectrum in reader:
        if int(getattr(spectrum, "ms_level", 0) or 0) != 1:
            continue
        rt_min = scan_time_minutes(spectrum)
        if rt_min is None or rt_min < lower_rt or rt_min > upper_rt:
            continue
        intensity = 0.0
        for mz, peak_intensity in spectrum.peaks("centroided"):
            if lower_mz <= float(mz) <= upper_mz:
                intensity += float(peak_intensity)
        points.append({"rt_min": round(rt_min, 6), "intensity": intensity})
    return points


def scan_time_minutes(spectrum) -> float | None:
    try:
        return float(spectrum.scan_time_in_minutes())
    except Exception:
        pass
    try:
        scan_time = spectrum.scan_time
        value = float(scan_time[0])
        unit = str(scan_time[1] if len(scan_time) > 1 else "").lower()
        return value / 60.0 if "second" in unit else value
    except Exception:
        return None


def apex_from_points(points: list[dict[str, Any]]) -> dict[str, Any]:
    if not points:
        return {"observed_rt_min": None, "apex_intensity": 0.0}
    apex = max(points, key=lambda item: float(item.get("intensity") or 0.0))
    return {
        "observed_rt_min": float(apex["rt_min"]),
        "apex_intensity": float(apex["intensity"]),
    }


def write_outputs(
    outdir: Path,
    summary: dict[str, Any],
    rows: list[dict[str, Any]],
    targets: list[dict[str, Any]],
    samples: list[dict[str, Any]],
    traces: list[dict[str, Any]],
    thresholds: dict[str, Any],
) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "is_qc_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8")
    fieldnames = [
        "target_id",
        "target_name",
        "expected_mz",
        "expected_rt_min",
        "sample_name",
        "sample_type",
        "input_file",
        "observed_rt_min",
        "rt_error_min",
        "apex_intensity",
        "status",
        "passed",
        "message",
    ]
    with (outdir / "is_qc_targets.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    trace_payload = {
        "version": "is_qc_v1",
        "thresholds": thresholds,
        "targets": targets,
        "samples": [
            {key: value for key, value in sample.items() if key != "path"}
            for sample in samples
        ],
        "traces": traces,
    }
    (outdir / "is_qc_traces.json").write_text(json.dumps(trace_payload, indent=2, sort_keys=True), encoding="utf-8")


def _target_field_map(fieldnames: list[str]) -> dict[str, str]:
    normalized = {_normalize_header(name): name for name in fieldnames}
    mapping = {
        "name": _first_match(normalized, NAME_ALIASES),
        "mz": _first_match(normalized, MZ_ALIASES),
        "rt": _first_match(normalized, RT_ALIASES),
    }
    missing = [label for label, value in mapping.items() if value is None]
    if missing:
        raise ValueError("IS target file requires columns equivalent to Name, m/z(Da), and RT (min)")
    return {key: str(value) for key, value in mapping.items()}


def _normalize_header(value: str) -> str:
    return " ".join(str(value or "").strip().lower().replace("_", " ").split())


def _first_match(normalized: dict[str, str], aliases: set[str]) -> str | None:
    alias_set = {_normalize_header(alias) for alias in aliases}
    for key, original in normalized.items():
        if key in alias_set:
            return original
    return None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "pass"}


def _sample_type(value: Any) -> str:
    return str(value or "").strip().lower()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract internal-standard EICs from prepared mzML files")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--targets", required=True)
    parser.add_argument("--samplesheet", required=True)
    parser.add_argument("--outdir", required=True)
    parser.add_argument("--mz-tolerance-da", type=float, default=0.01)
    parser.add_argument("--rt-tolerance-min", type=float, default=0.5)
    parser.add_argument("--min-peak-height", type=float, default=1000)
    parser.add_argument("--min-sample-pass-fraction", type=float, default=0.8)
    parser.add_argument("--min-target-pass-fraction", type=float, default=0.8)
    parser.add_argument("--max-qc-cv-percent", type=float, default=30)
    return parser


def main() -> None:
    run_extraction(build_arg_parser().parse_args())


if __name__ == "__main__":
    main()
