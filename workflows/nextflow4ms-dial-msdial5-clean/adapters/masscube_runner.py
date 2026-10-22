"""MassCube aligned execution, based on the validated .9002 offline pilot.

Native detection, alignment filters and gap filling are unchanged. MetaboLensR
owns downstream QC and normalization; native caches remain available as evidence.
"""
import argparse
import csv
import gc
from importlib.metadata import version
import json
import math
from pathlib import Path
import platform

from masscube_evidence import checksum, read_table, write_indexed_evidence

FIXED = {
    "quant_method": "peak_height", "merge_features": False, "correct_rt": False,
    "group_features_after_alignment": False, "group_features_single_file": False,
    "sample_normalization": False, "signal_normalization": False,
    "run_statistics": False, "fill_gaps": True, "gap_filling_method": "local_maximum",
}
POSITIVE = {"mz_tol_ms1", "mz_tol_ms2", "mz_tol_alignment", "feature_gap_tol",
            "scan_number_cutoff", "rt_tol_alignment", "gap_filling_rt_window", "rt_tol_annotation"}
NONNEGATIVE = {"ms1_abs_int_tol", "ms2_abs_int_tol", "noise_tol",
               "rt_lower_limit", "rt_upper_limit", "mz_lower_limit", "mz_upper_limit"}
FRACTIONS = {"ms2_rel_int_tol", "detection_rate_cutoff", "gaussian_similarity_tol", "ms2_sim_tol"}
BOOLEANS = {"fuzzy_search", "consider_rt"}


def configure_params(params, configuration):
    if not isinstance(configuration, dict):
        raise ValueError("MassCube config must be a JSON object")
    for key, value in configuration.items():
        if key in FIXED:
            if type(value) is not type(FIXED[key]) or value != FIXED[key]:
                raise ValueError(f"Unsupported MassCube setting: {key}")
        elif key in BOOLEANS:
            if not isinstance(value, bool):
                raise ValueError(f"MassCube {key} must be boolean")
        elif key in POSITIVE | NONNEGATIVE | FRACTIONS:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"MassCube {key} must be finite numeric")
            if value < 0 or (key in POSITIVE and value == 0) or (key in FRACTIONS and value > 1):
                raise ValueError(f"MassCube {key} is outside its supported range")
            if key in {"feature_gap_tol", "scan_number_cutoff"} and int(value) != value:
                raise ValueError(f"MassCube {key} must be an integer")
        else:
            raise ValueError(f"Unknown MassCube configuration key: {key}")
        setattr(params, key, value)
    for prefix, upper in (("rt", 10000), ("mz", 100000)):
        if getattr(params, f"{prefix}_lower_limit", 0) >= getattr(params, f"{prefix}_upper_limit", upper):
            raise ValueError(f"MassCube {prefix}_lower_limit must be less than {prefix}_upper_limit")
    for key, value in FIXED.items():
        setattr(params, key, value)


def resolve_acquisitions(directory, rows):
    if len(rows) < 2:
        raise ValueError("MassCube requires at least two acquisitions")
    for row in rows:
        row["source_column"] = Path(row["staged_filename"]).stem
    columns = [row["source_column"] for row in rows]
    ids = [row["acquisition_id"] for row in rows]
    if len(set(columns)) != len(rows) or len(set(ids)) != len(rows) or not all(ids):
        raise ValueError("Source columns and acquisition IDs must be unique and nonempty")
    prepared = [path for path in Path(directory).iterdir() if path.is_file()]
    if any(path.suffix.lower() != ".mzml" for path in prepared):
        raise ValueError("MassCube requires prepared centroid mzML acquisitions")
    paths = {path.stem: path for path in prepared}
    if len(paths) != len(prepared) or set(paths) != set(columns):
        raise ValueError("Prepared files must match every samplesheet acquisition exactly")
    return [paths[column] for column in columns]


def write_package_config(frozen_path, metadata_path, output_path):
    config = json.loads(Path(frozen_path).read_text())
    if config.get("profile") != "strict" or config.get("source_tool") != "masscube":
        raise ValueError("MassCube requires a frozen strict MetaboLensR masscube configuration")
    config["source_metadata"] = json.loads(Path(metadata_path).read_text())
    Path(output_path).write_text(json.dumps(config, indent=2, allow_nan=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--samplesheet", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--metabolensr-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--container-image", required=True)
    parser.add_argument("--library", type=Path)
    args = parser.parse_args()
    if version("masscube") != "1.2.22":
        raise ValueError("This runner requires MassCube 1.2.22")
    import pandas as pd
    from masscube.alignment import convert_features_to_df, feature_alignment, output_feature_table
    from masscube.annotation import annotate_aligned_features
    from masscube.params import Params
    from masscube.raw_data_utils import find_raw_data_info
    from masscube.workflows import process_single_file

    args.output.mkdir(parents=True, exist_ok=True)
    if any(args.output.iterdir()):
        raise ValueError("Use an empty output directory to avoid mixing runs")
    rows = read_table(args.samplesheet, ",")
    paths = resolve_acquisitions(args.inputs, rows)
    sheet = pd.DataFrame(rows)
    raw_info = [find_raw_data_info(str(path)) for path in paths]
    if any(info[:3] != raw_info[0][:3] for info in raw_info) or not raw_info[0][2]:
        raise ValueError("Inputs must share instrument, polarity and centroid mode")
    sheet.to_csv(args.output / "samplesheet.csv", index=False)
    params = Params()
    params.set_default(raw_info[0][0], raw_info[0][1])
    configure_params(params, json.loads(args.config.read_text()))
    for field, folder in (("single_file_dir", "single_files"), ("tmp_file_dir", "cache"),
                          ("project_file_dir", "project")):
        path = args.output / folder
        path.mkdir()
        setattr(params, field, str(path))
    params.sample_metadata = pd.DataFrame({
        "sample_name": sheet.source_column,
        "is_qc": sheet.sample_type.str.lower() == "qc",
        "is_blank": sheet.sample_type.str.lower() == "blank",
        "VALID": True,
    })
    if args.library:
        params.ms2_library_path = str(args.library)
    provenance = {
        "version": version("masscube"), "python_version": platform.python_version(),
        "container_image": args.container_image, "quantitation_type": "height",
        "retention_time_unit": "minute", "evidence_adapter_id": "masscube-indexed-v1",
        "configuration": {key: value for key, value in vars(params).items() if key != "sample_metadata"},
        "grouping_performed": False, "annotation_performed": bool(args.library),
        "upstream_filters": "Native detection and alignment quality/scan-count/detection-rate filters retained; no normalization or downstream statistical filtering.",
        "inputs": [{"file": path.name, "acquisition_id": aid, "sha256": checksum(path)}
                   for path, aid in zip(paths, sheet.acquisition_id)],
        "runner_sha256": checksum(__file__),
        "evidence_producer_sha256": checksum(Path(__file__).with_name("masscube_evidence.py")),
        "scientific_config_sha256": checksum(args.config),
        "acquisition_evidence_file": "acquisition_evidence.tsv",
    }
    if args.library:
        provenance["library"] = {"file": args.library.name, "sha256": checksum(args.library)}
    (args.output / "source_metadata.json").write_text(json.dumps(provenance, indent=2, allow_nan=False))
    for path in paths:
        print(f"Processing {path.name}", flush=True)
        data = process_single_file(str(path), params=params, group_features=False,
                                   annotate_ms2=False, output_dir=params.single_file_dir)
        if data is None or not (Path(params.single_file_dir) / (path.stem + ".txt")).exists():
            raise RuntimeError(f"MassCube failed to process {path.name}")
        if not (Path(params.tmp_file_dir) / (path.stem + ".mzpkl")).exists():
            raise RuntimeError(f"Missing gap-fill cache for {path.name}")
        del data
        gc.collect()
    print("Aligning and filling gaps", flush=True)
    features = feature_alignment(params.single_file_dir, params)
    if list(params.sample_metadata.iloc[:, 0]) != list(sheet.source_column):
        raise RuntimeError("MassCube dropped or reordered an acquisition")
    if args.library:
        print("Annotating aligned features", flush=True)
        annotate_aligned_features(features, params)
    table = convert_features_to_df(features, list(sheet.source_column), "peak_height")
    output_feature_table(table, str(args.output / "aligned_feature_table.tsv"))
    with (args.output / "acquisition_evidence.tsv").open("w") as stream:
        writer = csv.writer(stream, delimiter="\t")
        writer.writerow(["source_feature_id", "acquisition_id", "native_feature_id", "detected",
                         "gap_filled", "intensity", "native_mz", "native_rt_min", "scan_idx"])
        for feature in features:
            for i, aid in enumerate(sheet.acquisition_id):
                detected = feature.feature_id_arr[i] != -1
                writer.writerow([str(feature.id), aid, str(feature.feature_id_arr[i]) if detected else "",
                                 bool(detected), bool(not detected and feature.peak_height_arr[i] > 0),
                                 int(feature.peak_height_arr[i]), feature.mz_arr[i] if detected else "",
                                 feature.rt_arr[i] if detected else "", feature.scan_idx_arr[i] if detected else ""])
    del features, table
    gc.collect()
    write_indexed_evidence(args.output)
    write_package_config(args.metabolensr_config, args.output / "source_metadata.json",
                         args.output / "metabolensr_config.json")
    print(f"Exported aligned features and indexed evidence across {len(sheet)} acquisitions", flush=True)


if __name__ == "__main__":
    main()
