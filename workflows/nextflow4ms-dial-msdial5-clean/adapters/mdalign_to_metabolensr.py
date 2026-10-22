#!/usr/bin/env python3
"""Preserve registered acquisition identity in the MS-DIAL import bridge."""

import argparse
import csv
from pathlib import Path


def source_key(filename):
    name = Path(filename).name
    return name.rsplit(".", 1)[0] if name.lower().endswith((".raw", ".mzml", ".abf")) else name


def adapt(rows, samplesheet, factor_column):
    headers = [i for i, row in enumerate(rows) if row and row[0].strip() == "Alignment ID"]
    if len(headers) != 1:
        raise ValueError("Expected exactly one Alignment ID header")
    header_index = headers[0]
    header = rows[header_index]
    markers = [header.index(name) for name in ("MS/MS spectrum", "Spectrum reference file name") if name in header]
    if not markers:
        raise ValueError("Cannot locate the MS-DIAL intensity columns")
    first_sample = max(markers) + 1
    lookup = {}
    for row in samplesheet:
        filename = row.get("staged_filename") or row.get("input_filename", "")
        key = source_key(filename)
        if not key or key in lookup:
            raise ValueError("Missing or ambiguous input filename in samplesheet")
        lookup[key] = row
    names = header[first_sample:]
    if not names or len(set(names)) != len(names) or set(map(source_key, names)) != set(lookup):
        raise ValueError("MS-DIAL columns must match the samplesheet acquisitions exactly")
    mapping = []
    for name in names:
        row = lookup[source_key(name)]
        file_id = row.get("file_asset_id", "").strip()
        acquisition = row.get("acquisition_id") or (f"file-{file_id}" if file_id else name)
        kind = row.get("sample_type", "").strip().lower()
        types = {"sample": "Sample", "qc": "QC", "blank": "Blank", "solvent_blank": "Blank", "process_blank": "Blank", "standard": "Standard"}
        if kind not in types:
            raise ValueError(f"Unsupported sample_type for {name}: {kind}")
        kind = types[kind]
        group = kind if kind in {"QC", "Blank"} else row.get(factor_column, "").strip()
        batch = row.get("batch_id", "").strip()
        order = row.get("injection_order", "").strip()
        if not group or not batch or not order.isdigit() or int(order) <= 0:
            raise ValueError(f"Missing/invalid group, batch or injection order for {name}")
        mapping.append(dict(source_column=acquisition, acquisition_id=acquisition,
                            sample_name=row.get("sample_name") or name,
                            sample_id=row.get("sample_id", ""), file_asset_id=file_id,
                            source_filename=row.get("staged_filename") or row["input_filename"],
                            input_filename=row["input_filename"], engine_source_column=name,
                            group_label=group, sample_type=kind, batch_id=batch, injection_order=str(int(order))))
    if len({row["acquisition_id"] for row in mapping}) != len(mapping):
        raise ValueError("Duplicate acquisition identity")
    if len({(row["batch_id"], row["injection_order"]) for row in mapping}) != len(mapping):
        raise ValueError("Injection order must be unique within each batch")
    output = [[""] * first_sample + [label] + [row[field] for row in mapping]
              for label, field in (("Class", "group_label"), ("File type", "sample_type"),
                                   ("Injection order", "injection_order"), ("Batch ID", "batch_id"))]
    output.append(header[:first_sample] + [""] + [row["acquisition_id"] for row in mapping])
    for row in rows[header_index + 1:]:
        if not row:
            continue
        if len(row) != len(header):
            raise ValueError("MS-DIAL feature row has an unexpected number of columns")
        output.append(row[:first_sample] + [""] + row[first_sample:])
    return output, mapping


def write_mapping(path, mapping):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(mapping[0]))
        writer.writeheader()
        writer.writerows(mapping)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mdalign", required=True, type=Path)
    parser.add_argument("--samplesheet", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--mapping", required=True, type=Path)
    parser.add_argument("--factor-column", default="group_label")
    args = parser.parse_args()
    with args.mdalign.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.reader(handle, delimiter="\t"))
    with args.samplesheet.open(newline="", encoding="utf-8-sig") as handle:
        sheet = list(csv.DictReader(handle))
    table, mapping = adapt(rows, sheet, args.factor_column)
    with args.out.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle, delimiter="\t").writerows(table)
    write_mapping(args.mapping, mapping)


if __name__ == "__main__":
    main()
