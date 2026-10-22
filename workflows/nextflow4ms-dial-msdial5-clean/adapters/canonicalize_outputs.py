#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


FEATURE_HEADERS = [
    "run_id",
    "canonical_feature_id",
    "source_tool",
    "source_feature_id",
    "mz",
    "rt",
    "polarity",
    "adduct",
    "charge",
    "has_ms2",
    "spectrum_id",
    "source_row_json",
]
FEATURE_QUANT_HEADERS = [
    "run_id",
    "canonical_feature_id",
    "sample_id",
    "raw_area",
    "normalized_area",
    "log_area",
    "qc_flag",
]
R_RESULTS_HEADERS = [
    "run_id",
    "canonical_feature_id",
    "result_name",
    "normalization_method",
    "contrast",
    "statistic",
    "p_value",
    "q_value",
    "log2_fc",
    "extra_json",
]
FEATURE_ANNOTATION_HEADERS = [
    "run_id",
    "canonical_feature_id",
    "annotation_job_id",
    "provider",
    "execution_backend",
    "annotation_type",
    "candidate_rank",
    "name",
    "formula",
    "adduct",
    "smiles",
    "inchi",
    "inchikey",
    "source_database",
    "score",
    "confidence",
    "evidence_json",
    "raw_record_json",
]
NETWORK_EDGE_HEADERS = [
    "run_id",
    "annotation_job_id",
    "provider",
    "source_canonical_feature_id",
    "target_canonical_feature_id",
    "score",
    "matched_peaks",
    "delta_mz",
    "component_id",
    "evidence_json",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export MS-DIAL/StatPipeline outputs to MetaboLens canonical TSV/JSON files.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--outdir", required=True)
    parser.add_argument("--params-json", default="")
    parser.add_argument("--stats-table", default="")
    parser.add_argument("--search-root", action="append", default=[])
    return parser.parse_args()


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8", errors="ignore") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def read_xlsx(path: Path) -> list[dict[str, str]]:
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

    def shared_strings(workbook: zipfile.ZipFile) -> list[str]:
        if "xl/sharedStrings.xml" not in workbook.namelist():
            return []
        root = ElementTree.fromstring(workbook.read("xl/sharedStrings.xml"))
        return ["".join(text.text or "" for text in item.findall(".//x:t", namespace)) for item in root.findall("x:si", namespace)]

    def column_index(cell_ref: str) -> int:
        column_name = re.sub(r"\d", "", cell_ref)
        index = 0
        for char in column_name:
            index = index * 26 + ord(char.upper()) - 64
        return max(index - 1, 0)

    def cell_value(cell: ElementTree.Element, strings: list[str]) -> str:
        cell_type = cell.attrib.get("t", "")
        if cell_type == "inlineStr":
            return "".join(text.text or "" for text in cell.findall(".//x:t", namespace))
        value = cell.find("x:v", namespace)
        if value is None or value.text is None:
            return ""
        if cell_type == "s":
            try:
                return strings[int(value.text)]
            except (IndexError, ValueError):
                return ""
        return value.text

    with zipfile.ZipFile(path) as workbook:
        if "xl/worksheets/sheet1.xml" not in workbook.namelist():
            return []
        strings = shared_strings(workbook)
        root = ElementTree.fromstring(workbook.read("xl/worksheets/sheet1.xml"))

    rows: list[list[str]] = []
    for row in root.findall(".//x:row", namespace):
        values: list[str] = []
        for cell in row.findall("x:c", namespace):
            index = column_index(cell.attrib.get("r", "A1"))
            while len(values) <= index:
                values.append("")
            values[index] = cell_value(cell, strings).strip()
        if any(values):
            rows.append(values)
    if not rows:
        return []
    headers = rows[0]
    return [dict(zip(headers, row)) for row in rows[1:] if any(cell.strip() for cell in row)]


def write_tsv(path: Path, headers: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({header: row.get(header, "") for header in headers})


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def compact_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def first_value(row: dict[str, str], names: list[str]) -> str:
    lower_lookup = {key.lower(): value for key, value in row.items()}
    for name in names:
        if name in row:
            return (row.get(name) or "").strip()
        if name.lower() in lower_lookup:
            return (lower_lookup.get(name.lower()) or "").strip()
    return ""


def numeric_sort_key(value: str) -> tuple[int, float | str]:
    try:
        return (0, float(value))
    except (TypeError, ValueError):
        return (1, value)


def canonical_id(source_feature_id: str) -> str:
    return f"msdial5_{source_feature_id}"


def infer_polarity(adduct: str) -> str:
    if "+" in adduct:
        return "positive"
    if "-" in adduct:
        return "negative"
    return ""


def normalize_bool(value: str) -> str:
    token = str(value or "").strip().lower()
    if token in {"1", "true", "yes", "y"}:
        return "true"
    if token in {"0", "false", "no", "n"}:
        return "false"
    return ""


def find_feature_metadata(search_roots: list[Path]) -> Path | None:
    for root in search_roots:
        candidates = find_files(root, "feature_metadata.tsv")
        if candidates:
            return candidates[0]
    return None


def find_files(root: Path, pattern: str) -> list[Path]:
    if not root.exists():
        return []
    matches: list[Path] = []
    visited: set[Path] = set()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        current = Path(dirpath)
        try:
            resolved = current.resolve()
        except OSError:
            dirnames[:] = []
            continue
        if resolved in visited:
            dirnames[:] = []
            continue
        visited.add(resolved)
        kept_dirs = []
        for dirname in dirnames:
            try:
                child_resolved = (current / dirname).resolve()
            except OSError:
                continue
            if child_resolved not in visited:
                kept_dirs.append(dirname)
        dirnames[:] = kept_dirs
        for filename in filenames:
            path = current / filename
            if path.match(pattern):
                matches.append(path)
    return sorted(matches)


def read_msdial_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    rows: list[list[str]]
    with path.open(newline="", encoding="utf-8", errors="ignore") as handle:
        rows = list(csv.reader(handle, delimiter="\t"))
    header_index = next((index for index, row in enumerate(rows) if row and row[0].strip() == "Alignment ID"), None)
    if header_index is None:
        return []
    headers = rows[header_index]
    return [dict(zip(headers, row)) for row in rows[header_index + 1 :] if row and any(cell.strip() for cell in row)]


def build_features(run_id: str, stats_table: Path | None, search_roots: list[Path]) -> tuple[list[dict[str, str]], dict[str, str]]:
    feature_path = find_feature_metadata(search_roots)
    source_rows = read_tsv(feature_path) if feature_path else []
    if not source_rows and stats_table is not None:
        source_rows = read_msdial_rows(stats_table)

    features: list[dict[str, str]] = []
    id_map: dict[str, str] = {}
    for source_row in sorted(source_rows, key=lambda item: numeric_sort_key(first_value(item, ["Alignment ID", "source_feature_id", "feature_id"]))):
        source_feature_id = first_value(source_row, ["Alignment ID", "source_feature_id", "feature_id"])
        if not source_feature_id:
            continue
        feature_id = canonical_id(source_feature_id)
        if source_feature_id in id_map:
            continue
        id_map[source_feature_id] = feature_id
        adduct = first_value(source_row, ["Adduct type", "adduct"])
        features.append(
            {
                "run_id": run_id,
                "canonical_feature_id": feature_id,
                "source_tool": "msdial5",
                "source_feature_id": source_feature_id,
                "mz": first_value(source_row, ["Average Mz", "Average m/z", "mz", "m/z"]),
                "rt": first_value(source_row, ["Average Rt(min)", "Average RT", "rt", "retention_time"]),
                "polarity": infer_polarity(adduct),
                "adduct": adduct,
                "charge": first_value(source_row, ["Charge", "charge"]),
                "has_ms2": normalize_bool(first_value(source_row, ["MS/MS assigned", "has_ms2"])),
                "spectrum_id": first_value(source_row, ["Spectrum reference file name", "spectrum_id"]),
                "source_row_json": compact_json(source_row),
            }
        )
    return features, id_map


def selected_normalized_matrix(search_roots: list[Path]) -> Path | None:
    selected = ""
    for root in search_roots:
        for path in find_files(root, "selected_normalization.json"):
            payload = read_json(path)
            selected = str(payload.get("selected_normalization_method") or "").strip()
            if selected:
                matrix = path.parent / "normalized_matrices" / f"normalized_{safe_slug(selected)}_matrix.tsv"
                if matrix.is_file():
                    return matrix
    for root in search_roots:
        candidates = find_files(root, "normalized_matrices/normalized_*_matrix.tsv")
        if candidates:
            return candidates[0]
    return None


def safe_slug(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "_" for char in value)


def find_raw_matrix(search_roots: list[Path]) -> Path | None:
    for root in search_roots:
        candidates = find_files(root, "raw_feature_matrix.tsv")
        if candidates:
            return candidates[0]
    return None


def read_matrix(path: Path | None, id_map: dict[str, str]) -> dict[tuple[str, str], str]:
    if path is None or not path.is_file():
        return {}
    rows = read_tsv(path)
    values: dict[tuple[str, str], str] = {}
    for row in rows:
        sample_id = first_value(row, ["Sample", "sample_id", "Name"])
        if not sample_id:
            continue
        for source_feature_id, canonical_feature_id in id_map.items():
            value = (row.get(source_feature_id) or "").strip()
            if value:
                values[(canonical_feature_id, sample_id)] = value
    return values


def build_feature_quant(run_id: str, id_map: dict[str, str], search_roots: list[Path]) -> list[dict[str, str]]:
    raw = read_matrix(find_raw_matrix(search_roots), id_map)
    normalized = read_matrix(selected_normalized_matrix(search_roots), id_map)
    keys = sorted(set(raw) | set(normalized), key=lambda item: (numeric_sort_key(item[0].replace("msdial5_", "", 1)), item[1]))
    return [
        {
            "run_id": run_id,
            "canonical_feature_id": feature_id,
            "sample_id": sample_id,
            "raw_area": raw.get((feature_id, sample_id), ""),
            "normalized_area": normalized.get((feature_id, sample_id), ""),
            "log_area": "",
            "qc_flag": "",
        }
        for feature_id, sample_id in keys
    ]


def find_statistical_tables(search_roots: list[Path]) -> list[Path]:
    tables: list[Path] = []
    for root in search_roots:
        tables.extend(find_files(root, "statistical_results_*.txt"))
        tables.extend(find_files(root, "statistical_results_*.tsv"))
        tables.extend(find_files(root, "statistical_results_*.xlsx"))
    return list(dict.fromkeys(tables))


def read_statistical_table(path: Path) -> list[dict[str, str]]:
    if path.suffix.lower() == ".xlsx":
        return read_xlsx(path)
    return read_tsv(path)


def infer_normalization_method(path: Path) -> str:
    parts = list(path.parts)
    if "statistics" not in parts:
        return ""
    index = parts.index("statistics")
    if len(parts) - index < 4:
        return ""
    method = parts[index + 1]
    return re.sub(r"^\d+_", "", method)


def build_r_results(run_id: str, id_map: dict[str, str], search_roots: list[Path]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for table in find_statistical_tables(search_roots):
        table_rows = read_statistical_table(table)
        normalization_method = infer_normalization_method(table)
        q_value_column = next((key for key in source_row_keys(table_rows) if key.lower().startswith("p_adj")), "")
        for source_row in table_rows:
            source_feature_id = first_value(source_row, ["ID", "Alignment ID", "source_feature_id", "feature_id", "Feature"])
            canonical_feature_id = id_map.get(source_feature_id)
            if not canonical_feature_id:
                continue
            known_columns = {
                "ID",
                "Alignment ID",
                "source_feature_id",
                "feature_id",
                "Feature",
                "contrast",
                "Contrast",
                "statistic",
                "Statistic",
                "Stat_value",
                "t",
                "p_value",
                "p.value",
                "P.Value",
                "pvalue",
                "q_value",
                "adj.P.Val",
                "FDR",
                "qvalue",
                "log2_fc",
                "logFC",
                "log2FC",
            }
            if q_value_column:
                known_columns.add(q_value_column)
            extra = {key: value for key, value in source_row.items() if key not in known_columns}
            rows.append(
                {
                    "run_id": run_id,
                    "canonical_feature_id": canonical_feature_id,
                    "result_name": table.stem,
                    "normalization_method": normalization_method,
                    "contrast": first_value(source_row, ["contrast", "Contrast"]) or table.parent.name,
                    "statistic": first_value(source_row, ["Stat_value", "statistic", "Statistic", "t"]),
                    "p_value": first_value(source_row, ["p_value", "p.value", "P.Value", "pvalue"]),
                    "q_value": first_value(source_row, [q_value_column, "q_value", "adj.P.Val", "FDR", "qvalue"]),
                    "log2_fc": first_value(source_row, ["log2FC", "log2_fc", "logFC"]),
                    "extra_json": compact_json(extra),
                }
            )
    return rows


def source_row_keys(rows: list[dict[str, str]]) -> list[str]:
    return list(rows[0]) if rows else []


def build_manifest(run_id: str, params_json: Path | None, output_assets: list[str]) -> dict[str, Any]:
    params_payload = read_json(params_json) if params_json is not None else {}
    params_hash = sha256(params_json) if params_json is not None and params_json.is_file() else hashlib.sha256(b"").hexdigest()
    nextflow_params = params_payload.get("nextflow_params") if isinstance(params_payload.get("nextflow_params"), dict) else {}
    return {
        "run_id": run_id,
        "pipeline_name": "msdial5-nextflow",
        "pipeline_version": str(params_payload.get("workflow_version") or "1.0dev-msdial5"),
        "preprocessing_provider": "MS-DIAL 5",
        "params_hash": params_hash,
        "params_json": nextflow_params,
        "input_assets": params_payload.get("selected_files") or [],
        "output_assets": output_assets,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def build_artifacts(run_id: str, outdir: Path, filenames: list[str]) -> dict[str, Any]:
    assets = []
    for filename in filenames:
        path = outdir / filename
        if not path.is_file():
            continue
        assets.append(
            {
                "asset_type": Path(filename).stem,
                "uri": path.resolve().as_uri(),
                "format": "json" if path.suffix == ".json" else "tsv",
                "checksum": sha256(path),
                "created_by_step": "canonicalize",
            }
        )
    return {"run_id": run_id, "assets": assets}


def export_canonical_outputs(
    *,
    run_id: str,
    outdir: Path,
    params_json: Path | None = None,
    stats_table: Path | None = None,
    search_roots: list[Path] | None = None,
) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    roots = [root for root in (search_roots or []) if root]

    features, id_map = build_features(run_id, stats_table, roots)
    feature_quant = build_feature_quant(run_id, id_map, roots)
    r_results = build_r_results(run_id, id_map, roots)

    write_tsv(outdir / "features.tsv", FEATURE_HEADERS, features)
    write_tsv(outdir / "feature_quant.tsv", FEATURE_QUANT_HEADERS, feature_quant)
    write_tsv(outdir / "r_results.tsv", R_RESULTS_HEADERS, r_results)
    write_tsv(outdir / "feature_annotations.tsv", FEATURE_ANNOTATION_HEADERS, [])
    write_tsv(outdir / "network_edges.tsv", NETWORK_EDGE_HEADERS, [])

    canonical_tsv_filenames = [
        "features.tsv",
        "feature_quant.tsv",
        "r_results.tsv",
        "feature_annotations.tsv",
        "network_edges.tsv",
    ]
    canonical_filenames = ["run_manifest.json", *canonical_tsv_filenames, "artifacts.json"]
    manifest = build_manifest(run_id, params_json, canonical_filenames)
    write_json(outdir / "run_manifest.json", manifest)
    write_json(outdir / "artifacts.json", build_artifacts(run_id, outdir, ["run_manifest.json", *canonical_tsv_filenames]))


def maybe_path(value: str) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.exists() else None


def main() -> None:
    args = parse_args()
    export_canonical_outputs(
        run_id=args.run_id,
        outdir=Path(args.outdir),
        params_json=maybe_path(args.params_json),
        stats_table=maybe_path(args.stats_table),
        search_roots=[Path(value) for value in args.search_root],
    )


if __name__ == "__main__":
    main()
