"""Indexed native MassCube 1.2.22 EIC/MS2 evidence, without feature truncation."""
import argparse
from contextlib import ExitStack
import csv
import gc
import hashlib
import json
import math
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory


def checksum(path, algorithm="sha256"):
    digest = hashlib.new(algorithm)
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def finite(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Native evidence contains a non-finite number")
    return number


def read_table(path, delimiter="\t"):
    with Path(path).open(newline="") as stream:
        return list(csv.DictReader(stream, delimiter=delimiter))


def spectrum(value):
    peaks = [[finite(part) for part in peak.split(";")]
             for peak in value.split("|") if peak]
    if any(len(peak) != 2 or peak[0] <= 0 or peak[1] < 0 for peak in peaks):
        raise ValueError("Invalid native MS2 spectrum")
    maximum = max((peak[1] for peak in peaks), default=0)
    return [{"mz": mz, "intensity": intensity * 100 / maximum if maximum else 0,
             "matched": False} for mz, intensity in peaks]


def ms2_evidence(row, metadata):
    experimental = spectrum(row.get("MS2", ""))
    reference = spectrum(row.get("matched_MS2", ""))
    tolerance = finite(metadata["configuration"]["mz_tol_ms2"])
    for peak in experimental:
        peak["matched"] = any(abs(peak["mz"] - ref["mz"]) <= tolerance for ref in reference)
    for peak in reference:
        peak["matched"] = any(abs(peak["mz"] - exp["mz"]) <= tolerance for exp in experimental)
    return {"provider": "MassCube", "available": bool(experimental),
            "match_tolerance_da": tolerance, "experimental": experimental,
            "reference": {"name": row.get("annotation") or None, "inchikey": row.get("InChIKey") or None,
                          "precursor_mz": None, "library_filename": metadata.get("library", {}).get("file"),
                          "peaks": reference} if reference else None,
            "scores": {"similarity": finite(row["similarity"]) if row.get("similarity") else None,
                       "search_mode": row.get("search_mode") or None},
            "reference_rt": None, "reference_mz": None,
            "library_filename": metadata.get("library", {}).get("file")}


def load_cache(path):
    from masscube.mzpkl import read_mzpkl_to_MSData
    from masscube.raw_data_utils import MSData
    return read_mzpkl_to_MSData(MSData(), str(path))


def write_indexed_evidence(native_dir, output_dir=None, *, cache_loader=load_cache):
    native_dir = Path(native_dir)
    output_dir = Path(output_dir) if output_dir else native_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    aligned_path = native_dir / "aligned_feature_table.tsv"
    acquisition_path = native_dir / "acquisition_evidence.tsv"
    rows = read_table(aligned_path)
    samples = read_table(native_dir / "samplesheet.csv", ",")
    metadata = json.loads((native_dir / "source_metadata.json").read_text())
    config = metadata["configuration"]
    if config["correct_rt"] is not False or config["merge_features"] is not False:
        raise ValueError("Indexed evidence requires uncorrected, unmerged native features")
    ids = [row["feature_ID"] for row in rows]
    acquisition_ids = [sample["acquisition_id"] for sample in samples]
    source_columns = [sample["source_column"] for sample in samples]
    if len(set(ids)) != len(ids) or not all(ids):
        raise ValueError("Aligned feature IDs must be unique and nonempty")
    if len(set(acquisition_ids)) != len(samples) or len(set(source_columns)) != len(samples):
        raise ValueError("Native acquisition identities must be unique")
    cells = {}
    for cell in read_table(acquisition_path):
        key = (cell["source_feature_id"], cell["acquisition_id"])
        if key in cells:
            raise ValueError("Duplicate acquisition evidence cell")
        cells[key] = cell
    if set(cells) != {(fid, aid) for fid in ids for aid in acquisition_ids}:
        raise ValueError("Native evidence must contain every feature/acquisition exactly once")
    # Recover the exact native alignment coordinates, before TSV export rounding.
    # MassCube updates aligned m/z to the median detected m/z; aligned RT comes
    # from its highest-height reference acquisition.
    coordinates = {}
    for row in rows:
        fid = row["feature_ID"]
        detected = [cells[fid, aid] for aid in acquisition_ids if cells[fid, aid]["detected"].lower() == "true"]
        reference_aid = acquisition_ids[source_columns.index(row["alignment_reference_file"])]
        coordinates[fid] = (median(finite(cell["native_mz"]) for cell in detected),
                            finite(cells[fid, reference_aid]["native_rt_min"]))

    index = {"schema_version": "1.0", "adapter_id": "masscube-indexed-v1",
             "engine": "masscube", "retention_time_unit": "minute", "bundle_id": "aligned",
             "data_file": "evidence.jsonl", "aligned_table_md5": checksum(aligned_path, "md5"),
             "acquisition_evidence_md5": checksum(acquisition_path, "md5"),
             "acquisitions": [{key: sample[key] for key in ("acquisition_id", "source_column", "file_asset_id", "sample_name")} for sample in samples],
             "features": {}}
    # Read one native cache at a time. Disk-backed acquisition streams avoid
    # retaining every study trace or every mzpkl scan cache in memory.
    with TemporaryDirectory(prefix="evidence-", dir=output_dir) as temp:
        for position, sample in enumerate(samples):
            print(f"Indexing native traces for {sample['acquisition_id']}", flush=True)
            cache = cache_loader(native_dir / "cache" / (sample["source_column"] + ".mzpkl"))
            with (Path(temp) / str(position)).open("w") as stream:
                for row in rows:
                    fid, aid = row["feature_ID"], sample["acquisition_id"]
                    cell = cells[fid, aid]
                    detected = cell["detected"].lower() == "true"
                    filled = cell["gap_filled"].lower() == "true"
                    mz, aligned_rt = coordinates[fid]
                    top = finite(cell["native_rt_min"]) if detected else None
                    if detected:
                        mz = finite(cell["native_mz"])
                    if filled:
                        gap_times, gap_signals, _ = cache.get_eic_data(
                            mz, aligned_rt, finite(config["mz_tol_alignment"]), finite(config["gap_filling_rt_window"]))
                        if len(gap_times):
                            apex = max(range(len(gap_times)), key=lambda i: gap_signals[i][1])
                            if gap_signals[apex][1] > 0:
                                top = finite(gap_times[apex])
                    context = [min(aligned_rt, top if top is not None else aligned_rt) - 0.5,
                               max(aligned_rt, top if top is not None else aligned_rt) + 0.5]
                    times, signals, _ = cache.get_eic_data(
                        mz, mz_tol=finite(config["mz_tol_ms1"] if detected else config["mz_tol_alignment"]), rt_range=context)
                    peak = {"acquisition_id": aid, "native_feature_id": cell["native_feature_id"] or None,
                            "detected": detected, "gap_filled": filled, "top": top if len(times) else None,
                            "left": None, "right": None,
                            "rt": [finite(value) for value in times],
                            "intensity": [finite(signal[1]) for signal in signals]}
                    stream.write(json.dumps(peak, separators=(",", ":"), allow_nan=False) + "\n")
            del cache
            gc.collect()
        digest = hashlib.sha256()
        with ExitStack() as stack:
            streams = [stack.enter_context((Path(temp) / str(i)).open()) for i in range(len(samples))]
            output = stack.enter_context((output_dir / "evidence.jsonl").open("wb"))
            for row in rows:
                fid = row["feature_ID"]
                ms2 = ms2_evidence(row, metadata)
                record = {"schema_version": "1.0", "source_feature_id": fid,
                          "mz": finite(row["m/z"]), "rt_min": finite(row["RT"]),
                          "peaks": [json.loads(next(stream)) for stream in streams], "ms2": ms2}
                payload = (json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n").encode()
                index["features"][fid] = {"offset": output.tell(), "length": len(payload),
                                          "has_ms2": ms2["available"], "has_reference": bool(ms2["reference"]),
                                          "msms_matched": None, "low_score": None}
                output.write(payload)
                digest.update(payload)
        index["data_sha256"] = digest.hexdigest()
        (output_dir / "evidence.index.json").write_text(json.dumps(index, separators=(",", ":"), allow_nan=False))
    return index


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    write_indexed_evidence(args.native_dir, args.output_dir)
