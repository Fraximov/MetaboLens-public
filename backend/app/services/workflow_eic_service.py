from __future__ import annotations

import copy
import csv
import mmap
import json
import os
import re
import subprocess
from collections import OrderedDict
from pathlib import Path
from threading import Lock
from urllib.parse import unquote, urlparse

from sqlalchemy.orm import Session

from app.core.errors import bad_request, not_found
from app.models.models import DatasetFile, WorkflowDefinition, WorkflowJob, WorkflowRunArtifact
from app.services.msdial_eic_reader import MsdialEicArchiveReader, MsdialEicFormatError
from app.services.study_service import StudyService
from app.services.workflow_definition_service import WorkflowDefinitionService


def _int_env(name: str, default: int, *, minimum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, value)


def _float_env(name: str, default: float, *, minimum: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, value)


EIC_MAX_POINTS_PER_TRACE = 900
EIC_PREFETCH_RADIUS = _int_env("MSDIAL_EIC_PREFETCH_RADIUS", 5, minimum=0)
EIC_CACHE_MAX_SPOTS = _int_env("MSDIAL_EIC_CACHE_MAX_SPOTS", 256, minimum=8)
_EIC_SPOT_CACHE: OrderedDict[tuple[str, int, int, int, int], dict] = OrderedDict()
_EIC_SPOT_CACHE_LOCK = Lock()
_ALIGNMENT_FEATURE_CACHE: dict[tuple[str, int, int], list[dict]] = {}
_ALIGNMENT_SAMPLE_NAME_CACHE: dict[tuple[str, int, int], list[str]] = {}
_ALIGNMENT_SKELETON_CACHE: dict[tuple[str, int, int], list[str | None]] = {}
_ALIGNMENT_CACHE_LOCK = Lock()

# Fragment agreement is drawn, never scored: MS-DIAL owns the verdict and this
# tolerance only decides which sticks are coloured as explained by the reference.
MS2_MATCH_TOLERANCE_DA = _float_env("MSDIAL_MS2_MATCH_TOLERANCE_DA", 0.01, minimum=0.0001)
MS2_MAX_PEAKS = _int_env("MSDIAL_MS2_MAX_PEAKS", 400, minimum=10)
# Libraries reach 200 MB and 290k entries. The index maps an InChIKey skeleton to
# the offsets of that compound's entries and is keyed by the library file alone,
# so opening a second job that shares a library costs nothing.
_MS2_LIBRARY_INDEX_CACHE: dict[tuple[str, int, int], dict[str, tuple[int, ...]]] = {}
_MS2_LIBRARY_CACHE_LOCK = Lock()
# A handful of entries carry no peaks (170 of 291,783 in the merged library), so a
# few offsets per compound are kept and the first usable one wins.
MS2_LIBRARY_MAX_OFFSETS_PER_COMPOUND = 3
MS2_LIBRARY_HEADER_WINDOW = 4096
MS2_SCORE_COLUMNS = {
    "total_score": "Total score",
    "weighted_dot_product": "Weighted dot product",
    "simple_dot_product": "Simple dot product",
    "reverse_dot_product": "Reverse dot product",
    "matched_peaks_count": "Matched peaks count",
    "matched_peaks_percentage": "Matched peaks percentage",
    "rt_similarity": "RT similarity",
    "mz_similarity": "m/z similarity",
}


class WorkflowEicService:
    def __init__(self, db: Session):
        self.db = db
        self.studies = StudyService(db)

    def list_features(self, user_id: int, assay_id: int, eic_file_id: int) -> list[dict]:
        context = self._get_eic_context(user_id, assay_id, eic_file_id)
        return self._features_with_reference_flag(context)

    def search_features(
        self,
        user_id: int,
        assay_id: int,
        eic_file_id: int,
        query: str | None,
        *,
        limit: int = 50,
        include_feature_index: int | None = None,
    ) -> list[dict]:
        context = self._get_eic_context(user_id, assay_id, eic_file_id, include_sample_names=False)
        features = self._features_with_reference_flag(context)
        limit = min(max(int(limit), 1), 100)
        tokens = [token for token in (query or "").strip().lower().split() if token]

        rows: list[dict] = []
        for feature in features:
            if tokens and not self._feature_matches_search(feature, tokens):
                continue
            rows.append(feature)
            if len(rows) >= limit:
                break

        if include_feature_index is not None and 0 <= include_feature_index < len(features):
            selected_feature = features[include_feature_index]
            if all(feature["feature_index"] != selected_feature["feature_index"] for feature in rows):
                rows.insert(0, selected_feature)
        return rows[:limit]

    def get_summary(self, user_id: int, assay_id: int, eic_file_id: int) -> dict:
        context = self._get_eic_context(user_id, assay_id, eic_file_id, include_sample_names=False)
        try:
            summary = MsdialEicArchiveReader(context["eic_path"]).read_summary()
        except MsdialEicFormatError as exc:
            raise bad_request(f"Could not read MS-DIAL EIC archive: {exc}") from exc
        return {
            "eic_file_id": eic_file_id,
            "eic_filename": context["eic_file"].original_filename,
            "alignment_file_id": context["alignment_file"].id,
            "alignment_filename": context["alignment_file"].original_filename,
            **summary,
        }

    def get_feature_trace(self, user_id: int, assay_id: int, eic_file_id: int, feature_index: int) -> dict:
        context = self._get_eic_context(user_id, assay_id, eic_file_id)
        features = self._features_for_alignment(context["alignment_path"])
        feature = (
            features[feature_index]
            if 0 <= feature_index < len(features) and int(features[feature_index]["feature_index"]) == int(feature_index)
            else None
        )
        if feature is None:
            feature = next((item for item in features if int(item["feature_index"]) == int(feature_index)), None)
        if feature is None:
            raise not_found("Feature not found in the paired MS-DIAL alignment table")

        try:
            spot = self._read_eic_spot(context["eic_path"], int(feature_index))
        except IndexError as exc:
            raise bad_request(str(exc)) from exc
        except MsdialEicFormatError as exc:
            raise bad_request(f"Could not read MS-DIAL EIC archive: {exc}") from exc

        sample_names = context["sample_names"]
        for peak in spot["peaks"]:
            file_id = peak.get("file_id")
            peak["sample_name"] = sample_names[file_id] if isinstance(file_id, int) and 0 <= file_id < len(sample_names) else f"File {file_id}"

        return {
            "eic_file_id": eic_file_id,
            "eic_filename": context["eic_file"].original_filename,
            "alignment_file_id": context["alignment_file"].id,
            "alignment_filename": context["alignment_file"].original_filename,
            "reader": spot.pop("reader", "python"),
            "feature": feature,
            "spot": spot,
            "ms2": self._build_ms2_payload(context, int(feature_index)),
        }

    def _package_native_context(self, bundle: dict) -> dict | None:
        """Strict same-job/same-alignment pairing, with no first-file fallback."""
        def paired(role):
            matches = [file for artifact, file in bundle["files"]
                       if artifact.artifact_role == role
                       and self._align_result_key(file.original_filename) == bundle["bundle_key"]]
            if len(matches) > 1:
                raise bad_request("Ambiguous native evidence for this package bundle")
            return matches[0] if matches else None

        alignment = paired("msdial_alignment")
        if alignment is None:
            return None
        path = self._local_path_from_storage_uri(alignment.storage_uri)
        if not path.is_file():
            return None
        return {"job": bundle["job"], "alignment_file": alignment,
                "alignment_path": path, "eic_file": paired("msdial_eic_archive")}

    def package_evidence_features(self, bundle: dict) -> dict[str, dict]:
        context = self._package_native_context(bundle)
        if context is None:
            return {}
        features = self._features_with_reference_flag(context)
        result = {}
        for feature in features:
            identity = str(feature["alignment_id"])
            if feature["alignment_id"] is None or identity in result:
                raise bad_request("Native alignment IDs must be unique and nonempty")
            result[identity] = feature
        return result

    def package_feature_evidence(self, bundle: dict, source_feature_id: str, samples: list[dict], *, evidence_features: dict | None = None) -> dict:
        context = self._package_native_context(bundle)
        unavailable = {"spot": None, "ms2": None, "reader": None,
                       "evidence_message": "Chromatogram evidence is unavailable for this feature."}
        if context is None:
            return unavailable
        native = (self.package_evidence_features(bundle) if evidence_features is None else evidence_features).get(source_feature_id)
        if native is None:
            raise bad_request("Package source feature ID is absent from the paired alignment")
        # MS2 lives in the exported table, whose order may differ from AEF order.
        unavailable["ms2"] = {**self._build_ms2_payload(context, native["feature_index"]), "provider": "msdial"}
        archive = context["eic_file"]
        if archive is None:
            return unavailable
        path = self._local_path_from_storage_uri(archive.storage_uri)
        if not path.is_file():
            return unavailable
        # MS-DIAL 5 AlignmentRefiner.SetAlignmentID assigns the final ID before
        # PeakAligner serializes the same ordered spots. Do not use table row
        # position (or the package feature ID) as the binary address.
        # https://github.com/systemsomicslab/MsdialWorkbench/blob/master/src/MSDIAL5/MsdialCore/Algorithm/Alignment/AlignmentRefiner.cs
        try:
            spot = self._read_eic_spot(path, int(source_feature_id))
        except (ValueError, IndexError, MsdialEicFormatError) as exc:
            raise bad_request(f"Native chromatogram identity could not be resolved: {exc}") from exc
        native_rt = native.get("average_rt")
        center_rt = spot.get("center", {}).get("rt")
        # Native export rounds RT to three decimals; mismatches must not show
        # a convincing chromatogram belonging to another feature.
        if native_rt is not None and center_rt is not None and abs(native_rt - center_rt) > 0.001:
            raise bad_request("Chromatogram center disagrees with the source feature")
        native_mz = native.get("average_mz")
        center_mz = spot.get("center", {}).get("mz")
        # Five-decimal exports and float32 archives have different precision.
        if native_mz is not None and center_mz is not None and abs(native_mz - center_mz) > max(0.00005, abs(native_mz) * 1e-7):
            raise bad_request("Chromatogram mass disagrees with the source feature")
        sample_names = self._sample_names_for_alignment(context["alignment_path"])
        acquisitions = {}
        for sample in samples:
            key = sample.get("engine_source_column")
            if not key or key in acquisitions or not sample.get("acquisition_id"):
                raise bad_request("Package acquisition mapping is missing or ambiguous")
            acquisitions[key] = sample
        for peak in spot["peaks"]:
            index = peak.get("file_id")
            column = sample_names[index] if isinstance(index, int) and 0 <= index < len(sample_names) else None
            sample = acquisitions.get(column)
            if sample is None:
                raise bad_request("Native trace has no exact package acquisition mapping")
            peak.update(sample_name=sample.get("sample_name") or column,
                        acquisition_id=sample["acquisition_id"], file_asset_id=sample.get("file_asset_id"),
                        source_column=column)
        # Binary positions never become selectable feature identities.
        spot.pop("feature_index", None)
        return {"spot": spot, "ms2": unavailable["ms2"],
                "reader": spot.pop("reader", "python"), "evidence_message": None}

    def _get_eic_context(self, user_id: int, assay_id: int, eic_file_id: int, *, include_sample_names: bool = True) -> dict:
        row = (
            self.db.query(WorkflowRunArtifact, WorkflowJob, DatasetFile)
            .join(WorkflowJob, WorkflowJob.id == WorkflowRunArtifact.workflow_job_id)
            .join(DatasetFile, DatasetFile.id == WorkflowRunArtifact.dataset_file_id)
            .filter(
                DatasetFile.id == eic_file_id,
                WorkflowJob.assay_id == assay_id,
                WorkflowJob.deleted_at.is_(None),
            )
            .first()
        )
        if row is None:
            raise not_found("EIC archive not found for this assay")

        artifact, job, eic_file = row
        self.studies.get(user_id, job.study_id)
        if artifact.artifact_role != "msdial_eic_archive":
            raise bad_request("Selected workflow output is not an MS-DIAL EIC archive")

        alignment_file = self._find_paired_alignment_file(job.id, eic_file.original_filename)
        alignment_path = self._local_path_from_storage_uri(alignment_file.storage_uri)
        eic_path = self._local_path_from_storage_uri(eic_file.storage_uri)
        if not alignment_path.exists():
            raise not_found("Paired MS-DIAL alignment table is missing on disk")
        if not eic_path.exists():
            raise not_found("MS-DIAL EIC archive is missing on disk")

        sample_names = self._sample_names_for_alignment(alignment_path) if include_sample_names else []
        return {
            "job": job,
            "eic_file": eic_file,
            "eic_path": eic_path,
            "alignment_file": alignment_file,
            "alignment_path": alignment_path,
            "sample_names": sample_names,
        }

    def _find_paired_alignment_file(self, workflow_job_id: int, eic_filename: str) -> DatasetFile:
        rows = (
            self.db.query(WorkflowRunArtifact, DatasetFile)
            .join(DatasetFile, DatasetFile.id == WorkflowRunArtifact.dataset_file_id)
            .filter(WorkflowRunArtifact.workflow_job_id == workflow_job_id, WorkflowRunArtifact.artifact_role == "msdial_alignment")
            .all()
        )
        if not rows:
            raise not_found("No paired MS-DIAL alignment table is registered for this EIC archive")

        eic_key = self._align_result_key(eic_filename)
        if eic_key:
            for _, dataset_file in rows:
                if self._align_result_key(dataset_file.original_filename) == eic_key:
                    return dataset_file
        return rows[0][1]

    @staticmethod
    def _align_result_key(filename: str) -> str | None:
        match = re.search(r"(AlignResult[^\\/]*?)(?:\.EIC\.aef|\.mdalign|\.mdmsp|\.mzTabM)$", filename)
        return match.group(1) if match else None

    def _read_eic_spot(self, eic_path: Path, feature_index: int) -> dict:
        cached = self._cache_get(eic_path, feature_index)
        if cached is not None:
            cached["reader"] = "rust-batch-cache"
            return cached

        rust_bin = os.environ.get("MSDIAL_AEF_READER_BIN")
        if rust_bin and Path(rust_bin).is_file():
            try:
                payload = self._read_eic_spot_window_with_rust(Path(rust_bin), eic_path, feature_index)
                if payload is not None:
                    payload["reader"] = "rust-batch"
                    return payload
            except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
                pass

        payload = MsdialEicArchiveReader(eic_path).read_spot(feature_index, max_points_per_trace=EIC_MAX_POINTS_PER_TRACE)
        payload["reader"] = "python"
        return payload

    def _read_eic_spot_window_with_rust(self, rust_bin: Path, eic_path: Path, feature_index: int) -> dict | None:
        window_start = max(0, feature_index - EIC_PREFETCH_RADIUS)
        window_count = EIC_PREFETCH_RADIUS * 2 + 1
        completed = subprocess.run(
            [
                str(rust_bin),
                "--file",
                str(eic_path),
                "--start",
                str(window_start),
                "--count",
                str(window_count),
                "--max-points",
                str(EIC_MAX_POINTS_PER_TRACE),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        payload = json.loads(completed.stdout)
        features = payload.get("features") or []
        cache_payloads: dict[int, dict] = {}
        for item in features:
            try:
                index = int(item["index"])
            except (KeyError, TypeError, ValueError):
                continue
            spot = item.get("spot")
            if isinstance(spot, dict):
                cache_payloads[index] = spot
        self._cache_put_many(eic_path, cache_payloads)
        return self._cache_get(eic_path, feature_index)

    @classmethod
    def _features_for_alignment(cls, alignment_path: Path) -> list[dict]:
        key = cls._alignment_cache_key(alignment_path)
        with _ALIGNMENT_CACHE_LOCK:
            cached = _ALIGNMENT_FEATURE_CACHE.get(key)
            if cached is not None:
                return cached
        features = cls._read_alignment_features(alignment_path)
        with _ALIGNMENT_CACHE_LOCK:
            _ALIGNMENT_FEATURE_CACHE[key] = features
        return features

    @classmethod
    def _sample_names_for_alignment(cls, alignment_path: Path) -> list[str]:
        key = cls._alignment_cache_key(alignment_path)
        with _ALIGNMENT_CACHE_LOCK:
            cached = _ALIGNMENT_SAMPLE_NAME_CACHE.get(key)
            if cached is not None:
                return cached
        sample_names = cls._read_alignment_sample_names(alignment_path)
        with _ALIGNMENT_CACHE_LOCK:
            _ALIGNMENT_SAMPLE_NAME_CACHE[key] = sample_names
        return sample_names

    @staticmethod
    def _alignment_cache_key(alignment_path: Path) -> tuple[str, int, int]:
        stat = alignment_path.stat()
        return (str(alignment_path), int(stat.st_mtime_ns), int(stat.st_size))

    @staticmethod
    def _cache_key(eic_path: Path, feature_index: int) -> tuple[str, int, int, int, int]:
        stat = eic_path.stat()
        return (str(eic_path), int(stat.st_mtime_ns), int(stat.st_size), int(feature_index), EIC_MAX_POINTS_PER_TRACE)

    @classmethod
    def _cache_get(cls, eic_path: Path, feature_index: int) -> dict | None:
        key = cls._cache_key(eic_path, feature_index)
        with _EIC_SPOT_CACHE_LOCK:
            cached = _EIC_SPOT_CACHE.get(key)
            if cached is None:
                return None
            _EIC_SPOT_CACHE.move_to_end(key)
            return copy.deepcopy(cached)

    @classmethod
    def _cache_put_many(cls, eic_path: Path, spots_by_index: dict[int, dict]) -> None:
        with _EIC_SPOT_CACHE_LOCK:
            for feature_index, spot in spots_by_index.items():
                key = cls._cache_key(eic_path, feature_index)
                _EIC_SPOT_CACHE[key] = copy.deepcopy(spot)
                _EIC_SPOT_CACHE.move_to_end(key)
            while len(_EIC_SPOT_CACHE) > EIC_CACHE_MAX_SPOTS:
                _EIC_SPOT_CACHE.popitem(last=False)

    @classmethod
    def _read_alignment_features(cls, alignment_path: Path) -> list[dict]:
        header, rows = cls._read_alignment_table(alignment_path)
        column_index = {name: index for index, name in enumerate(header)}
        features: list[dict] = []
        for feature_index, row in enumerate(rows):
            alignment_id = cls._as_int(cls._cell(row, column_index, "Alignment ID"))
            name = cls._clean_text(cls._cell(row, column_index, "Metabolite name")) or "Unknown"
            average_rt = cls._as_float(cls._cell(row, column_index, "Average Rt(min)"))
            average_mz = cls._as_float(cls._cell(row, column_index, "Average Mz"))
            adduct_type = cls._clean_text(cls._cell(row, column_index, "Adduct type"))
            has_ms2 = cls._has_ms2(
                cls._cell(row, column_index, "MS/MS assigned"),
                cls._cell(row, column_index, "MS/MS spectrum"),
            )
            feature = {
                "feature_index": feature_index,
                "alignment_id": alignment_id,
                "metabolite_name": name,
                "adduct_type": adduct_type,
                "average_rt": average_rt,
                "average_mz": average_mz,
                "annotated": cls._is_annotated_name(name),
                "has_ms2": has_ms2,
                "msms_matched": cls._as_bool(cls._cell(row, column_index, "MS/MS matched")),
                "low_score": name.strip().lower().startswith("low score:"),
            }
            feature["label"] = cls._feature_label(feature)
            features.append(feature)
        return features

    @classmethod
    def _read_alignment_sample_names(cls, alignment_path: Path) -> list[str]:
        header, _ = cls._read_alignment_table(alignment_path, read_rows=False)
        markers = [header.index(name) for name in ("MS/MS spectrum", "Spectrum reference file name") if name in header]
        if not markers:
            return []
        # Match the package import bridge: both fields are native metadata.
        sample_start = max(markers) + 1
        return [name for name in header[sample_start:] if cls._clean_text(name)]

    @classmethod
    def _read_alignment_table(cls, alignment_path: Path, *, read_rows: bool = True) -> tuple[list[str], list[list[str]]]:
        with alignment_path.open("r", encoding="utf-8-sig", errors="replace", newline="") as fh:
            reader = csv.reader(fh, delimiter="\t")
            header: list[str] | None = None
            for row in reader:
                if row and row[0] == "Alignment ID":
                    header = row
                    break
            if header is None:
                raise bad_request("MS-DIAL alignment table header was not found")
            if not read_rows:
                return header, []
            rows = [row for row in reader if cls._is_data_row(row)]
        return header, rows

    @staticmethod
    def _is_data_row(row: list[str]) -> bool:
        """The single rule for what counts as a feature row.

        ``feature_index`` is positional, so the full parse and the single-row
        read have to skip identically. If they drift, a feature's chromatogram
        and its spectrum come from different rows and nothing says so.
        """
        return bool(row) and any(cell.strip() for cell in row)

    @staticmethod
    def _as_bool(value: str | None) -> bool:
        return (value or "").strip().lower() == "true"

    @classmethod
    def _read_alignment_row(cls, alignment_path: Path, feature_index: int) -> tuple[list[str], list[str]] | None:
        """Read one feature's row instead of holding every row's scores.

        Scanning to the last row of a 2.9 MB table costs about 1 ms; caching the
        score columns for all 8.8k features costs about 12 MB per run in a cache
        that has no bound.
        """
        with alignment_path.open("r", encoding="utf-8-sig", errors="replace", newline="") as fh:
            header: list[str] | None = None
            index = -1
            for row in csv.reader(fh, delimiter="\t"):
                if header is None:
                    if row and row[0] == "Alignment ID":
                        header = row
                    continue
                if not cls._is_data_row(row):
                    continue
                index += 1
                if index == feature_index:
                    return header, row
        return None

    @staticmethod
    def _parse_peak_list(value: str | None) -> list[tuple[float, float]]:
        """MS-DIAL writes a spectrum as space-separated ``mz:intensity`` pairs."""
        peaks: list[tuple[float, float]] = []
        for token in (value or "").split():
            mz_text, separator, intensity_text = token.partition(":")
            if not separator:
                continue
            try:
                peaks.append((float(mz_text), float(intensity_text)))
            except ValueError:
                continue
        return peaks

    @staticmethod
    def _inchikey_skeleton(value: str | None) -> str | None:
        """The first InChIKey block, which is what actually joins to a library.

        The alignment table writes truncated keys such as
        ``GETQZCLCWQTVFV-UHFFFAOYAA`` that never equal a library's full key.
        """
        skeleton = (value or "").strip().split("-")[0].upper()
        return skeleton if len(skeleton) == 14 and skeleton.isalpha() else None

    @classmethod
    def _skeletons_by_index_for_alignment(cls, alignment_path: Path) -> list[str | None]:
        key = cls._alignment_cache_key(alignment_path)
        with _ALIGNMENT_CACHE_LOCK:
            cached = _ALIGNMENT_SKELETON_CACHE.get(key)
            if cached is not None:
                return cached
        header, rows = cls._read_alignment_table(alignment_path)
        column_index = {name: index for index, name in enumerate(header)}
        skeletons = [cls._inchikey_skeleton(cls._cell(row, column_index, "INCHIKEY")) for row in rows]
        with _ALIGNMENT_CACHE_LOCK:
            _ALIGNMENT_SKELETON_CACHE[key] = skeletons
        return skeletons

    def _features_with_reference_flag(self, context: dict) -> list[dict]:
        """Mark the features that actually have a reference to mirror against.

        Only a named feature whose compound is in the run's own MS2 library can
        show a reference half. On a real run that is a small fraction of the
        table -- 17 of 8,672 on one -- so without this the mirror is there but
        cannot be found. Uses the cached alignment metadata and library index. Flags are not cached on alignment rows because a
        temporarily unavailable library may later be mounted.
        """
        alignment_path = context["alignment_path"]
        features = self._features_for_alignment(alignment_path)
        library_path = self._ms2_library_path_for_job(context["job"])
        library = self._library_index(library_path) if library_path else {}
        skeletons = self._skeletons_by_index_for_alignment(alignment_path)
        return [{**feature, "has_reference": bool(
            feature.get("has_ms2") and index < len(skeletons) and skeletons[index] in library)}
            for index, feature in enumerate(features)]

    @staticmethod
    def _ms2_library_value_from_contract(job) -> str | None:
        """Which MS2 library this run used.

        The library is not registered as a run artifact, so the contract is the
        only record of which one produced the annotation.
        """
        run_root = (getattr(job, "run_root", None) or "").strip()
        if not run_root:
            return None
        try:
            payload = json.loads((Path(run_root) / "contract" / "params.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return ((payload.get("nextflow_params") or {}).get("ms2_library")
                or (payload.get("libraries") or {}).get("ms2_library") or "").strip() or None

    def _library_root_for_job(self, job) -> Path | None:
        definition = (
            self.db.query(WorkflowDefinition)
            .filter(WorkflowDefinition.id == getattr(job, "workflow_definition_id", None))
            .first()
        )
        if definition is None:
            return None
        return WorkflowDefinitionService(self.db).resolve_library_root(definition)

    def _ms2_library_path_for_job(self, job) -> Path | None:
        """Locate that library in the filesystem the backend can actually see.

        The contract records the path the execution plane used, and the backend
        mounts the pipeline somewhere else entirely, so a recorded path that does
        not exist here is normal rather than an error: the contract says which
        library, and the backend's own library root says where it lives.
        """
        recorded = self._ms2_library_value_from_contract(job)
        if recorded is None:
            return None
        direct = Path(recorded)
        if direct.is_file():
            return direct
        library_root = self._library_root_for_job(job)
        if library_root is None or not library_root.is_dir():
            return None
        return next((candidate for candidate in library_root.rglob(direct.name) if candidate.is_file()), None)

    @classmethod
    def _library_index(cls, library_path: Path) -> dict[str, tuple[int, ...]]:
        """InChIKey skeleton -> byte offsets of that compound's library entries.

        Keyed by the library file rather than by the run, which is the point:
        opening a second job that uses the same library costs a dict lookup
        instead of another scan. Built with mmap.find so the work happens in C --
        about half a second for a 209 MB, 290k entry library, against roughly
        five seconds for the per-run parse this replaced.
        """
        stat = library_path.stat()
        key = (str(library_path), int(stat.st_mtime_ns), int(stat.st_size))
        with _MS2_LIBRARY_CACHE_LOCK:
            cached = _MS2_LIBRARY_INDEX_CACHE.get(key)
            if cached is not None:
                return cached
        offsets_by_skeleton: dict[str, list[int]] = {}
        if stat.st_size:
            with library_path.open("rb") as fh:
                mapped = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
                try:
                    for tag in (b"\nInChiKey:", b"\nINCHIKEY:"):
                        position = mapped.find(tag)
                        while position != -1:
                            start = position + len(tag)
                            skeleton = mapped[start:start + 16].strip()[:14]
                            if len(skeleton) == 14 and skeleton.isalpha():
                                offsets = offsets_by_skeleton.setdefault(skeleton.decode("ascii"), [])
                                if len(offsets) < MS2_LIBRARY_MAX_OFFSETS_PER_COMPOUND:
                                    offsets.append(position)
                            position = mapped.find(tag, start)
                finally:
                    mapped.close()
        index = {skeleton: tuple(offsets) for skeleton, offsets in offsets_by_skeleton.items()}
        with _MS2_LIBRARY_CACHE_LOCK:
            _MS2_LIBRARY_INDEX_CACHE[key] = index
        return index

    @classmethod
    def _library_entry(cls, library_path: Path, skeleton: str | None) -> dict | None:
        """One compound's reference spectrum, read straight from its offset."""
        if not skeleton:
            return None
        for offset in cls._library_index(library_path).get(skeleton, ()):
            entry = cls._read_library_entry_at(library_path, offset)
            if entry and entry["peaks"]:
                return entry
        return None

    @classmethod
    def _read_library_entry_at(cls, library_path: Path, offset: int) -> dict | None:
        """Read one entry given the offset of its InChIKey line.

        MSP writes the name and precursor ahead of the key and the peaks after
        it, so the header is read from a bounded window behind the offset and
        the peaks forward until the next entry begins.
        """
        with library_path.open("rb") as fh:
            window_start = max(0, offset - MS2_LIBRARY_HEADER_WINDOW)
            fh.seek(window_start)
            behind = fh.read(offset - window_start)
            name = None
            precursor_mz = None
            for line in reversed(behind.split(b"\n")):
                lowered = line[:12].lower()
                if precursor_mz is None and lowered.startswith(b"precursormz:"):
                    precursor_mz = cls._as_float(line.decode("utf-8", "replace").split(":", 1)[1])
                if lowered.startswith(b"name:"):
                    name = line.decode("utf-8", "replace").split(":", 1)[1].strip()
                    break

            fh.seek(offset + 1)
            inchikey = None
            peaks: list[tuple[float, float]] = []
            for raw in fh:
                if raw[:5].lower().startswith(b"name:"):
                    break
                if inchikey is None and raw[:9].lower().startswith(b"inchikey:"):
                    inchikey = raw.decode("utf-8", "replace").split(":", 1)[1].strip()
                    continue
                if raw[:1].isdigit():
                    parts = raw.split()
                    if len(parts) >= 2:
                        try:
                            peaks.append((float(parts[0]), float(parts[1])))
                        except ValueError:
                            pass
        return {"name": name, "inchikey": inchikey, "precursor_mz": precursor_mz, "peaks": peaks}

    @staticmethod
    def _cap_peaks(peaks: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if len(peaks) <= MS2_MAX_PEAKS:
            return peaks
        return sorted(sorted(peaks, key=lambda item: item[1], reverse=True)[:MS2_MAX_PEAKS])

    @staticmethod
    def _normalized_peaks(peaks: list[tuple[float, float]]) -> list[dict]:
        """Scale to a 100 base peak so the two halves are comparable by eye."""
        base = max((intensity for _, intensity in peaks), default=0.0)
        scale = 100.0 / base if base > 0 else 0.0
        return [{"mz": mz, "intensity": intensity * scale, "matched": False} for mz, intensity in peaks]

    @classmethod
    def _mirror_peaks(cls, experimental: list[tuple[float, float]], reference: list[tuple[float, float]]) -> tuple[list[dict], list[dict]]:
        experimental = cls._cap_peaks(experimental)
        reference = cls._cap_peaks(reference)
        experimental_out = cls._normalized_peaks(experimental)
        reference_out = cls._normalized_peaks(reference)
        if reference:
            for peak in experimental_out:
                peak["matched"] = any(abs(peak["mz"] - mz) <= MS2_MATCH_TOLERANCE_DA for mz, _ in reference)
            for peak in reference_out:
                peak["matched"] = any(abs(peak["mz"] - mz) <= MS2_MATCH_TOLERANCE_DA for mz, _ in experimental)
        return experimental_out, reference_out

    def _build_ms2_payload(self, context: dict, feature_index: int) -> dict:
        """MS-DIAL's own spectrum, scores and library entry for one feature.

        Nothing here is rescored: the similarity numbers are MS-DIAL's and the
        tolerance only decides which sticks are drawn as matched.
        """
        payload: dict = {
            "available": False,
            "match_tolerance_da": MS2_MATCH_TOLERANCE_DA,
            "experimental": [],
            "reference": None,
            "scores": {},
            "reference_rt": None,
            "reference_mz": None,
            "library_filename": None,
        }
        found = self._read_alignment_row(context["alignment_path"], feature_index)
        if found is None:
            return payload
        header, row = found
        column_index = {name: index for index, name in enumerate(header)}

        payload["reference_rt"] = self._as_float(self._cell(row, column_index, "Reference RT"))
        payload["reference_mz"] = self._as_float(self._cell(row, column_index, "Reference m/z"))
        scores = {key: self._as_float(self._cell(row, column_index, column)) for key, column in MS2_SCORE_COLUMNS.items()}
        scores["annotation_tag"] = self._clean_text(self._cell(row, column_index, "Annotation tag (VS1.0)"))
        for key, column in (("rt_matched", "RT matched"), ("mz_matched", "m/z matched"), ("msms_matched", "MS/MS matched")):
            scores[key] = self._as_bool(self._cell(row, column_index, column))
        payload["scores"] = scores

        experimental = self._parse_peak_list(self._cell(row, column_index, "MS/MS spectrum"))
        if not experimental:
            return payload
        payload["available"] = True

        reference_entry = None
        library_path = self._ms2_library_path_for_job(context["job"])
        if library_path is not None:
            payload["library_filename"] = library_path.name
            skeleton = self._inchikey_skeleton(self._cell(row, column_index, "INCHIKEY"))
            reference_entry = self._library_entry(library_path, skeleton)

        experimental_out, reference_out = self._mirror_peaks(experimental, list((reference_entry or {}).get("peaks") or []))
        payload["experimental"] = experimental_out
        if reference_entry:
            payload["reference"] = {
                "name": reference_entry.get("name"),
                "inchikey": reference_entry.get("inchikey"),
                "precursor_mz": reference_entry.get("precursor_mz"),
                "library_filename": payload["library_filename"],
                "peaks": reference_out,
            }
        return payload

    @staticmethod
    def _local_path_from_storage_uri(storage_uri: str) -> Path:
        parsed = urlparse(storage_uri)
        if parsed.scheme != "file":
            raise bad_request("Only local file workflow outputs can be inspected")
        return Path(unquote(parsed.path))

    @staticmethod
    def _cell(row: list[str], column_index: dict[str, int], column: str) -> str | None:
        index = column_index.get(column)
        if index is None or index >= len(row):
            return None
        return row[index]

    @staticmethod
    def _clean_text(value: str | None) -> str | None:
        cleaned = (value or "").strip()
        if not cleaned or cleaned.lower() in {"null", "nan"}:
            return None
        return cleaned

    @staticmethod
    def _as_int(value: str | None) -> int | None:
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _as_float(value: str | None) -> float | None:
        try:
            return float(str(value).strip())
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _is_annotated_name(name: str | None) -> bool:
        normalized = (name or "").strip().lower()
        return bool(normalized and normalized not in {"unknown", "null", "-"} and not normalized.startswith("unknown"))

    @staticmethod
    def _has_ms2(assigned_value: str | None, spectrum_value: str | None) -> bool:
        false_values = {"", "-", "0", "false", "nan", "none", "null", "no"}
        assigned = (assigned_value or "").strip().lower()
        if assigned not in false_values:
            return True
        spectrum = (spectrum_value or "").strip().lower()
        return spectrum not in false_values

    @staticmethod
    def _feature_label(feature: dict) -> str:
        alignment_id = feature.get("alignment_id")
        prefix = f"#{alignment_id}" if alignment_id is not None else f"Index {feature['feature_index']}"
        parts = [prefix, feature.get("metabolite_name") or "Unknown"]
        if feature.get("average_mz") is not None:
            parts.append(f"m/z {feature['average_mz']:.5f}")
        if feature.get("average_rt") is not None:
            parts.append(f"RT {feature['average_rt']:.2f}")
        if feature.get("adduct_type"):
            parts.append(str(feature["adduct_type"]))
        return " | ".join(parts)

    @classmethod
    def _feature_matches_search(cls, feature: dict, tokens: list[str]) -> bool:
        # Two flag words turn the existing search box into the triage filter,
        # rather than adding controls and callbacks for it. "unmatched" is the
        # case worth reviewing: a name MS-DIAL did not confirm with MS2.
        remaining: list[str] = []
        for token in tokens:
            if token == "unmatched":
                if not (feature.get("annotated") and feature.get("has_ms2") and feature.get("msms_matched") is False):
                    return False
            elif token == "lowscore":
                if not feature.get("low_score"):
                    return False
            elif token == "reference":
                if not feature.get("has_reference"):
                    return False
            else:
                remaining.append(token)
        tokens = remaining

        fields = [
            feature.get("label"),
            feature.get("metabolite_name"),
            feature.get("adduct_type"),
            feature.get("feature_index"),
            feature.get("alignment_id"),
        ]
        average_mz = feature.get("average_mz")
        if average_mz is not None:
            fields.extend([f"{average_mz:.5f}", f"{average_mz:.4f}", f"{average_mz:.3f}", f"{average_mz:.2f}"])
        average_rt = feature.get("average_rt")
        if average_rt is not None:
            fields.extend([f"{average_rt:.2f}", f"{average_rt:.1f}"])
        haystack = " ".join(str(field).lower() for field in fields if field is not None)
        return all(token in haystack for token in tokens)
