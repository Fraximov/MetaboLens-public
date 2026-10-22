#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

RAW_SUFFIXES = (".raw", ".mzml", ".abf")
BLANK_TYPES = {"blank", "solvent_blank", "process_blank"}


class AdapterError(ValueError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Adapt an MS-DIAL .mdalign export to the stats .txt layout.")
    parser.add_argument("--mdalign", required=True, help="Path to the MS-DIAL .mdalign file")
    parser.add_argument("--samplesheet", required=True, help="Path to the workflow samplesheet.csv")
    parser.add_argument("--out", required=True, help="Output .txt path")
    parser.add_argument(
        "--factor-column",
        default="group_label",
        help="Samplesheet column used for biological sample Class values (default: group_label)",
    )
    parser.add_argument("--report-json", help="Optional JSON report path")
    return parser.parse_args()


def read_delimited_rows(path: Path, delimiter: str) -> list[list[str]]:
    with path.open(newline="", encoding="utf-8", errors="ignore") as handle:
        return list(csv.reader(handle, delimiter=delimiter))


def parse_samplesheet(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def normalize_sample_token(value: str | None) -> str:
    token = (value or "").strip().lower()
    for suffix in RAW_SUFFIXES:
        if token.endswith(suffix):
            token = token[: -len(suffix)]
            break
    return token


def canonical_file_type(sample_type: str | None) -> str:
    token = normalize_sample_token(sample_type)
    if token == "qc":
        return "QC"
    if token in BLANK_TYPES:
        return "Blank"
    if token == "standard":
        return "Standard"
    return "Sample"


def coerce_positive_int(value: str | None) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = int(float(text))
    except ValueError:
        return None
    return number if number > 0 else None


def find_feature_header_row(rows: list[list[str]]) -> int:
    for index, row in enumerate(rows):
        if row and row[0].strip() == "Alignment ID":
            return index
    raise AdapterError("Could not find the .mdalign feature header row starting with 'Alignment ID'.")


def find_sample_block_start(header_row: list[str]) -> int:
    for marker in ("MS/MS spectrum", "Spectrum reference file name"):
        if marker in header_row:
            return header_row.index(marker) + 1
    raise AdapterError("Could not detect the sample intensity block in the .mdalign header.")


def build_samplesheet_lookup(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    lookup: dict[str, dict[str, str]] = {}
    for row in rows:
        candidates = [
            row.get("sample_name", ""),
            Path(row.get("input_filename", "")).stem,
            row.get("input_filename", ""),
        ]
        for candidate in candidates:
            key = normalize_sample_token(candidate)
            if key and key not in lookup:
                lookup[key] = row
    return lookup


def match_samples(sample_headers: list[str], samplesheet_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    lookup = build_samplesheet_lookup(samplesheet_rows)
    matches: list[dict[str, Any]] = []
    unmatched: list[str] = []

    for header in sample_headers:
        key = normalize_sample_token(header)
        row = lookup.get(key)
        if row is None:
            unmatched.append(header)
            continue
        matches.append({"mdalign_header": header, "samplesheet": row})

    if unmatched:
        raise AdapterError(f"Could not match .mdalign sample column(s) to the samplesheet: {', '.join(unmatched)}")
    return matches


def resolve_factor_value(row: dict[str, str], factor_column: str) -> str:
    sample_type = canonical_file_type(row.get("sample_type"))
    if sample_type == "QC":
        return "QC"
    if sample_type == "Blank":
        return "Blank"
    value = (row.get(factor_column) or "").strip()
    if not value:
        sample_name = row.get("sample_name") or row.get("input_filename") or "unknown"
        raise AdapterError(
            f"Missing biological sample factor '{factor_column}' for sample '{sample_name}'."
        )
    return value


def resolve_injection_orders(
    matches: list[dict[str, Any]],
    mdalign_rows: list[list[str]],
    sample_block_start: int,
) -> list[int]:
    mdalign_order_row = mdalign_rows[2] if len(mdalign_rows) > 2 else []
    resolved: list[int] = []
    for index, match in enumerate(matches, start=1):
        samplesheet_row = match["samplesheet"]
        sample_index = sample_block_start + index - 1
        order = coerce_positive_int(samplesheet_row.get("injection_order"))
        if order is None and sample_index < len(mdalign_order_row):
            order = coerce_positive_int(mdalign_order_row[sample_index])
        if order is None:
            order = index
        resolved.append(order)
    if len(set(resolved)) != len(resolved):
        raise AdapterError(f"Injection orders must be unique; got: {resolved}")
    return resolved


def format_output_labels(classes: list[str], file_types: list[str], injection_orders: list[int], sample_names: list[str]) -> list[str]:
    width = max(2, len(str(max(injection_orders, default=0))))
    used: set[str] = set()
    labels: list[str] = []
    for cls, file_type, injection_order, sample_name in zip(classes, file_types, injection_orders, sample_names):
        if file_type == "QC":
            label = f"QC_{injection_order:0{width}d}"
        elif file_type == "Blank":
            label = f"Blank_{injection_order:0{width}d}"
        else:
            label = f"{injection_order}_{cls}"

        if label in used:
            label = f"{label}__{sample_name}"
        if label in used:
            raise AdapterError(f"Could not create a unique output label for sample '{sample_name}'.")
        used.add(label)
        labels.append(label)
    return labels


def build_output_rows(
    mdalign_rows: list[list[str]],
    samplesheet_rows: list[dict[str, str]],
    factor_column: str,
    batch_default: str = "1",
) -> tuple[list[list[str]], dict[str, Any]]:
    header_row_index = find_feature_header_row(mdalign_rows)
    header_row = mdalign_rows[header_row_index]
    sample_block_start = find_sample_block_start(header_row)
    feature_headers = header_row[:sample_block_start]
    sample_headers = header_row[sample_block_start:]
    if not sample_headers:
        raise AdapterError("No sample intensity columns found in the .mdalign file.")

    matches = match_samples(sample_headers, samplesheet_rows)
    file_types = [canonical_file_type(match["samplesheet"].get("sample_type")) for match in matches]
    classes = [resolve_factor_value(match["samplesheet"], factor_column) for match in matches]
    injection_orders = resolve_injection_orders(matches, mdalign_rows, sample_block_start)
    batch_ids = [(match["samplesheet"].get("batch_id") or batch_default).strip() or batch_default for match in matches]
    sample_names = [match["samplesheet"].get("sample_name") or match["mdalign_header"] for match in matches]
    output_labels = format_output_labels(classes, file_types, injection_orders, sample_names)

    label_column = [""] * len(feature_headers) + ["Class"] + classes
    file_type_row = [""] * len(feature_headers) + ["File type"] + file_types
    injection_row = [""] * len(feature_headers) + ["Injection order"] + [str(value) for value in injection_orders]
    batch_row = [""] * len(feature_headers) + ["Batch ID"] + batch_ids
    output_header = feature_headers + [""] + output_labels

    output_rows = [label_column, file_type_row, injection_row, batch_row, output_header]
    for row in mdalign_rows[header_row_index + 1 :]:
        feature_part = row[:sample_block_start]
        intensity_part = row[sample_block_start: sample_block_start + len(sample_headers)]
        output_rows.append(feature_part + [""] + intensity_part)

    report = {
        "factor_column": factor_column,
        "feature_header_row_index": header_row_index + 1,
        "sample_block_start_column": sample_block_start + 1,
        "matches": [
            {
                "mdalign_header": match["mdalign_header"],
                "sample_name": sample_name,
                "file_type": file_type,
                "class": cls,
                "injection_order": injection_order,
                "batch_id": batch_id,
                "output_label": output_label,
            }
            for match, sample_name, file_type, cls, injection_order, batch_id, output_label in zip(
                matches,
                sample_names,
                file_types,
                classes,
                injection_orders,
                batch_ids,
                output_labels,
            )
        ],
    }
    return output_rows, report


def write_output(path: Path, rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerows(rows)

def adapt_mdalign_to_stats_txt(
    mdalign_path: Path,
    samplesheet_path: Path,
    output_path: Path,
    factor_column: str,
    report_json_path: Path | None = None,
) -> dict[str, Any]:
    mdalign_rows = read_delimited_rows(mdalign_path, delimiter="\t")
    samplesheet_rows = parse_samplesheet(samplesheet_path)
    output_rows, report = build_output_rows(mdalign_rows, samplesheet_rows=samplesheet_rows, factor_column=factor_column)
    write_output(output_path, output_rows)
    if report_json_path is not None:
        report_json_path.parent.mkdir(parents=True, exist_ok=True)
        report_json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    cli_args = parse_args()
    try:
        adapt_mdalign_to_stats_txt(
            mdalign_path=Path(cli_args.mdalign),
            samplesheet_path=Path(cli_args.samplesheet),
            output_path=Path(cli_args.out),
            factor_column=cli_args.factor_column,
            report_json_path=Path(cli_args.report_json) if cli_args.report_json else None,
        )
    except AdapterError as exc:
        raise SystemExit(str(exc)) from exc
