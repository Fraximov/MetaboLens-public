import hashlib
import json
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.engine_contract import sha256_file

from services.materialized_run import MaterializedRun


def engine_output_paths(results_root: Path, pattern: str) -> list[Path]:
    """Files under `results_root` matching one engine-declared output pattern.

    Refuses a pattern, or a symlinked match, that would reach outside the results.
    """

    if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
        raise ValueError(f"Unsafe engine artifact pattern: {pattern}")
    paths = []
    for path in sorted(results_root.glob(pattern)):
        if not path.resolve().is_relative_to(results_root):
            raise ValueError(f"Engine artifact escapes results: {path}")
        if path.is_file():
            paths.append(path)
    return paths


@dataclass
class ArtifactCollection:
    output_records: list[dict[str, Any]]
    status_payload: dict[str, Any]
    result_summary: dict[str, Any]
    software_versions: dict[str, Any]


class ArtifactCollector:
    def collect(self, run: MaterializedRun, *, status_payload: dict[str, Any], exit_code: int) -> ArtifactCollection:
        output_records: list[dict[str, Any]] = []
        output_records.extend(self._collect_contract_artifacts(run))
        output_records.extend(self._collect_result_artifacts(run))
        output_records.extend(self._collect_pipeline_info_artifacts(run))
        output_records.extend(self._collect_log_artifacts(run))

        # Engine adapters declare their own native output roles, added below.
        primary_roles = {
            "is_qc_summary", "is_qc_table", "is_qc_traces",
            "is_qc_rt_correction_plan", "is_qc_rt_correction_preview", "hpc_smoke_output",
            "metabolensr_preview", "metabolensr_result", "metabolensr_report",
        }
        primary_roles.update(role for _, role in run.engine_execution.get("native_outputs", []))
        is_qc_summary = self._read_json(Path(run.results_path) / "is-qc" / "is_qc_summary.json")
        rt_plan = self._read_json(Path(run.results_path) / "rt-correction" / "rt_correction_plan.json")
        manifest_payload = {
            "job_id": run.job_id,
            "status": "succeeded" if exit_code == 0 else "failed",
            "run_root": run.run_root,
            "artifacts": output_records,
            "summary": {
                "artifact_count": len(output_records),
                "primary_output_count": len({
                    record["storage_uri"]
                    for record in output_records
                    if record["artifact_role"] in primary_roles
                }),
                "reference_file_asset_id": run.reference_file_asset_id,
                "config_source": run.config_source,
                "workflow_stage": run.nextflow_params.get("workflow_stage", "full"),
            },
        }
        manifest_path = Path(run.artifact_manifest_path)
        manifest_path.write_text(json.dumps(manifest_payload, indent=2, sort_keys=True), encoding="utf-8")
        output_records.append(self._build_record(manifest_path, "artifact_manifest", "workflow_contract"))

        final_status_payload = {
            **status_payload,
            "phase": "succeeded" if exit_code == 0 else "failed",
            "artifact_count": len(output_records),
            "artifact_manifest_path": run.artifact_manifest_path,
        }
        if is_qc_summary:
            final_status_payload["is_qc_status"] = is_qc_summary.get("overall_status")
            final_status_payload["is_qc_summary"] = is_qc_summary
        if rt_plan:
            final_status_payload["rt_correction"] = {
                "version": rt_plan.get("version"),
                "can_apply": bool(rt_plan.get("can_apply")),
                "blocking_reasons": list(rt_plan.get("blocking_reasons") or []),
                "warnings": [
                    warning
                    for sample in rt_plan.get("samples", [])
                    for warning in sample.get("warnings", [])
                ],
            }
        result_summary = {
            "job_id": run.job_id,
            "status": final_status_payload["phase"],
            "artifact_count": len(output_records),
            "primary_output_count": manifest_payload["summary"]["primary_output_count"],
            "reference_file_asset_id": run.reference_file_asset_id,
            "config_source": run.config_source,
            "workflow_stage": run.nextflow_params.get("workflow_stage", "full"),
        }
        return ArtifactCollection(
            output_records=output_records,
            status_payload=final_status_payload,
            result_summary=result_summary,
            software_versions={"runner": "metabolens-nextflow-v1"},
        )

    def _collect_contract_artifacts(self, run: MaterializedRun) -> list[dict[str, Any]]:
        if run.engine_execution:
            records = [self._build_record(Path(run.params_path), 'run_params', 'workflow_contract')]
            for filename, role in run.engine_execution['artifacts']:
                path = Path(filename)
                if not path.resolve().is_relative_to(Path(run.contract_dir).resolve()):
                    raise ValueError(f'Engine contract artifact escapes run: {filename}')
                records.append(self._build_record(path, role, 'workflow_contract'))
            return records
        # The HPC smoke test writes the same three contract files with placeholder content.
        return [
            self._build_record(Path(run.params_path), "run_params", "workflow_contract"),
            self._build_record(Path(run.samplesheet_path), "run_samplesheet", "workflow_contract", media_type="text/csv"),
            self._build_record(Path(run.staged_msdial_config_path), "staged_msdial_config", "workflow_contract", media_type="text/plain"),
        ]

    def _collect_result_artifacts(self, run: MaterializedRun) -> list[dict[str, Any]]:
        if run.nextflow_params.get("workflow_stage") == "hpc_smoke":
            return self._collect_hpc_smoke_artifacts(Path(run.results_path))

        records: list[dict[str, Any]] = []
        records.extend(self._collect_is_qc_artifacts(Path(run.results_path) / "is-qc"))
        records.extend(self._collect_rt_correction_artifacts(Path(run.results_path) / "rt-correction"))
        records.extend(self._collect_sirius_artifacts(Path(run.results_path) / "sirius"))
        root = Path(run.results_path).resolve()
        for pattern, role in run.engine_execution['native_outputs']:
            for path in engine_output_paths(root, pattern):
                records.append(self._build_record(path, role, role))
        records.extend(self._collect_metabolensr_artifacts(root))
        return records

    def _collect_metabolensr_artifacts(self, results_dir: Path) -> list[dict[str, Any]]:
        """Register package-owned bundle roles after checking the portable manifest.

        RDS files remain opaque. Python validates the inventory and checksums,
        while MetaboLensR owns the experiment and scientific interpretation.
        """
        common = {"samples": ("sample_metadata.tsv", "sample_metadata")}
        contracts = {
            "preview": ("qc_preview_summary.json", "preview_manifest", {
                **common, "preview": ("qc_preview.rds", "preview"),
                "audit": ("feature_audit.tsv", "feature_audit"),
            }),
            "final": ("metabolensr_manifest.json", "manifest", {
                **common, "result": ("metabolensr_result.rds", "result"),
                "audit": ("feature_audit_final.tsv", "feature_audit_final"),
                **{method: (f"normalized_{method}.tsv", f"normalized_{method}")
                   for method in ("raw", "tic", "pqn", "bmis")},
                "normalization_summary": ("normalization_summary.tsv", "normalization_summary"),
                "report": ("qc_report.html", "report"),
            }),
        }
        records: list[dict[str, Any]] = []
        for kind, (manifest_name, manifest_role, roles) in contracts.items():
            package_root = results_dir / "metabolensr"
            manifest_paths = list(package_root.glob(f"*/{kind}/{manifest_name}"))
            flat_manifest = package_root / kind / manifest_name
            if flat_manifest.is_file():
                manifest_paths.append(flat_manifest)
            for manifest_path in sorted(manifest_paths):
                bundle = manifest_path.parent
                if manifest_path.resolve() != manifest_path.absolute():
                    raise ValueError(f"Symlink in MetaboLensR bundle path: {manifest_path}")
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if (not isinstance(manifest, dict) or manifest.get("schema_version") != "2.0"
                        or manifest.get("bundle_type") != kind or not isinstance(manifest.get("outputs"), list)):
                    raise ValueError(f"Unsupported MetaboLensR bundle manifest: {manifest_path}")
                seen = set()
                for output in manifest["outputs"]:
                    role = output.get("role") if isinstance(output, dict) else None
                    if not isinstance(role, str) or role not in roles or role in seen:
                        raise ValueError(f"Unknown or duplicate MetaboLensR output role: {role}")
                    filename, artifact_role = roles[role]
                    path = bundle / filename
                    if (output.get("file") != filename or not path.is_file()
                            or path.resolve() != path.absolute()):
                        raise ValueError(f"Missing or unsafe MetaboLensR output: {filename}")
                    # One read gives both the package's MD5 and the registry's SHA-256.
                    md5, sha256 = hashlib.md5(), hashlib.sha256()
                    with path.open("rb") as handle:
                        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                            md5.update(chunk)
                            sha256.update(chunk)
                    if output.get("md5") != md5.hexdigest():
                        raise ValueError(f"MetaboLensR output checksum mismatch: {filename}")
                    seen.add(role)
                    records.append(self._build_record(
                        path, f"metabolensr_{artifact_role}", f"metabolensr_{artifact_role}",
                        checksum_sha256=sha256.hexdigest(),
                    ))
                # PQN/BMIS are unavailable for some valid experimental designs.
                if not (set(roles) - {"pqn", "bmis"}) <= seen:
                    raise ValueError(f"Incomplete MetaboLensR bundle: {manifest_path}")
                records.append(self._build_record(manifest_path, f"metabolensr_{manifest_role}", f"metabolensr_{manifest_role}"))
        return records

    def _collect_is_qc_artifacts(self, is_qc_dir: Path) -> list[dict[str, Any]]:
        if not is_qc_dir.exists():
            return []
        mapping = {
            "is_qc_summary.json": ("is_qc_summary", "is_qc_summary", "application/json"),
            "is_qc_targets.tsv": ("is_qc_table", "is_qc_table", "text/tab-separated-values"),
            "is_qc_traces.json": ("is_qc_traces", "is_qc_traces", "application/json"),
        }
        records: list[dict[str, Any]] = []
        for filename, (artifact_role, file_kind, media_type) in mapping.items():
            path = is_qc_dir / filename
            if path.is_file():
                records.append(self._build_record(path, artifact_role, file_kind, media_type=media_type))
        return records

    def _collect_rt_correction_artifacts(self, root: Path) -> list[dict[str, Any]]:
        mapping = {
            "rt_correction_plan.json": (
                "is_qc_rt_correction_plan",
                "is_qc_rt_correction_plan",
            ),
            "rt_correction_preview.json": (
                "is_qc_rt_correction_preview",
                "is_qc_rt_correction_preview",
            ),
        }
        return [
            self._build_record(root / filename, role, kind, media_type="application/json")
            for filename, (role, kind) in mapping.items()
            if (root / filename).is_file()
        ]

    def _collect_sirius_artifacts(self, sirius_dir: Path) -> list[dict[str, Any]]:
        if not sirius_dir.exists():
            return []
        mapping = {
            "annotation-input/sirius_input_summary.json": ("sirius_annotation_input_summary", "sirius_annotation_input_summary", "application/json"),
            "annotation-input/sirius_input.msp": ("sirius_annotation_input_msp", "sirius_annotation_input_msp", "text/plain"),
            "sirius_output/project.sirius": ("sirius_project", "sirius_project", "application/octet-stream"),
            "sirius_output/logs/sirius.stdout.log": ("sirius_log", "sirius_log", "text/plain"),
            "sirius_output/logs/sirius.stderr.log": ("sirius_log", "sirius_log", "text/plain"),
            "sirius_output/summaries/formula_identifications.tsv": ("sirius_formula_identifications", "sirius_formula_identifications", "text/tab-separated-values"),
            "sirius_output/summaries/structure_identifications.tsv": ("sirius_structure_identifications", "sirius_structure_identifications", "text/tab-separated-values"),
            "sirius_output/summaries/denovo_structure_identifications.tsv": ("sirius_denovo_structure_identifications", "sirius_denovo_structure_identifications", "text/tab-separated-values"),
            "sirius_output/summaries/spectral_matches.tsv": ("sirius_spectral_matches", "sirius_spectral_matches", "text/tab-separated-values"),
            "sirius_output/summaries/spectral_matches_analog.tsv": ("sirius_spectral_matches_analog", "sirius_spectral_matches_analog", "text/tab-separated-values"),
            "sirius_output/summaries/canopus_formula_summary.tsv": ("sirius_canopus_formula_summary", "sirius_canopus_formula_summary", "text/tab-separated-values"),
            "sirius_output/summaries/canopus_structure_summary.tsv": ("sirius_canopus_structure_summary", "sirius_canopus_structure_summary", "text/tab-separated-values"),
        }
        records: list[dict[str, Any]] = []
        for relative_path, (artifact_role, file_kind, media_type) in mapping.items():
            path = sirius_dir / relative_path
            if path.is_file():
                records.append(self._build_record(path, artifact_role, file_kind, media_type=media_type))
        return records

    def _collect_hpc_smoke_artifacts(self, results_dir: Path) -> list[dict[str, Any]]:
        if not results_dir.exists():
            return []
        return [
            self._build_record(path, "hpc_smoke_output", "hpc_smoke_output", media_type="text/plain")
            for path in sorted(results_dir.glob("*.upper.txt"))
            if path.is_file()
        ]

    def _collect_pipeline_info_artifacts(self, run: MaterializedRun) -> list[dict[str, Any]]:
        pipeline_info_dir = Path(run.results_path) / "pipeline_info"
        mapping = {
            "execution_report.html": ("execution_report", "workflow_report"),
            "execution_timeline.html": ("execution_timeline", "workflow_timeline"),
            "execution_trace.txt": ("execution_trace", "workflow_trace"),
            "pipeline_dag.svg": ("pipeline_dag", "workflow_dag"),
            "pipeline_dag.dot": ("pipeline_dag", "workflow_dag"),
        }
        records: list[dict[str, Any]] = []
        for filename, (artifact_role, file_kind) in mapping.items():
            path = pipeline_info_dir / filename
            if path.exists():
                records.append(self._build_record(path, artifact_role, file_kind))
        return records

    def _collect_log_artifacts(self, run: MaterializedRun) -> list[dict[str, Any]]:
        logs_dir = Path(run.logs_path)
        mapping = {
            "execution.log": ("execution_log", "workflow_log"),
            "success_report.txt": ("success_report", "workflow_status"),
            "error.txt": ("error_report", "workflow_status"),
        }
        records: list[dict[str, Any]] = []
        for filename, (artifact_role, file_kind) in mapping.items():
            path = logs_dir / filename
            if path.exists():
                records.append(self._build_record(path, artifact_role, file_kind))
        return records

    def _build_record(
        self, path: Path, artifact_role: str, file_kind: str, media_type: str | None = None,
        checksum_sha256: str | None = None,
    ) -> dict[str, Any]:
        guessed_media_type = media_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return {
            "artifact_role": artifact_role,
            "file_kind": file_kind,
            "filename": path.name,
            "storage_uri": f"file://{path.resolve()}",
            "media_type": guessed_media_type,
            "file_size_bytes": path.stat().st_size,
            "checksum_sha256": checksum_sha256 or sha256_file(path),
        }

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
