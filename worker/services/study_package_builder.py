"""One background copy/validation task; workflow polling remains responsive.

The runner lives for the polling loop's lifetime, not for one JobExecutor cycle.
Only the background thread opens its build session. After a process restart, an
interrupted revision is failed visibly; a new request creates a new directory.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import logging
from pathlib import Path
import shutil
import subprocess
import zipfile

from app.core.states import PACKAGE_BUILT_STATES
from app.models.models import StudyPackage, WorkflowJob, utcnow
from app.schemas.study_package import PackageSnapshot
from app.services.audit_service import AuditService
from app.services.metabolights_export import TEMPLATES, build_package
from app.services.provenance_service import ProvenanceService
from app.services.study_packaging_service import compact_report

from .study_package_export import export_package


def readable_size(n_bytes):
    for unit, factor in (("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if n_bytes >= factor:
            return f"{n_bytes / factor:.1f} {unit}"
    return f"{n_bytes} B"


def verified_spectral_files(db, study_id, package_id):
    """Checksums earlier builds confirmed, keyed by file ID; the newest wins.

    The exporter reuses one only while the file's size, mtime and inode are
    unchanged, so a later build does not read unchanged RAW files again.
    """
    verified = {}
    for previous in db.query(StudyPackage).filter(
            StudyPackage.study_id == study_id, StudyPackage.id != package_id,
            StudyPackage.status.in_(PACKAGE_BUILT_STATES)).order_by(StudyPackage.id):
        for reference in (previous.report_json or {}).get("spectral_files", []):
            verified[reference["file_id"]] = reference
    return verified


def validation_issues(report):
    """Convert upstream locations without suppressing warnings or parse failures."""
    if report.get("completed") is not True or not isinstance(report.get("violations"), list) or not isinstance(report.get("parser_messages"), dict):
        return [dict(code="validator_unavailable", severity="error", section="official", message="Official validation did not produce a complete report.")]
    issues = []
    for item in report["violations"]:
        if item.get("type") not in {"ERROR", "WARNING", "INFO"} or not item.get("identifier"):
            return [dict(code="invalid_validator_report", severity="error", section="official", message="Official validation report has an unexpected shape.")]
        issues.append(dict(code=item["identifier"], severity=item["type"].lower(), section="official", message=item.get("violation", ""),
            file=item.get("sourceFile", ""), column=item.get("sourceColumnHeader", ""),
            column_index=item.get("sourceColumnIndex", ""), values=item.get("values", [])))
    for filename, messages in report["parser_messages"].items():
        for message in messages:
            issues.append(dict(code="isa_parser", severity="error", section="official", file=filename,
                               message=message.get("detail") or message.get("short") or str(message)))
    return issues


class PackageBuildRunner:
    def __init__(self, sessions, settings):
        self.sessions = sessions
        self.settings = settings
        # ponytail: the application supports one worker replica. Add a durable
        # ownership lease before scaling workers; this pool only bounds one process.
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="study-package")
        self.future = None

    def close(self):
        self.executor.shutdown(wait=False)

    def recover_interrupted(self):
        with self.sessions() as db:
            for package in db.query(StudyPackage).filter_by(status="building"):
                package.status = "failed"
                package.finished_at = utcnow()
                package.error_message = "Worker restarted during this build. Request a new revision; partial files were retained for inspection."
                AuditService(db).log(None, "study.package_interrupted", "study_package", str(package.id), {})
            for package in db.query(StudyPackage).filter_by(export_status="exporting"):
                package.export_status = "export_failed"
                package.export_finished_at = utcnow()
                package.export_error = ("Worker restarted during this export; the incomplete copy keeps a .partial "
                                        "suffix. Request the export again.")
                AuditService(db).log(None, "study.package_export_interrupted", "study_package", str(package.id), {})
            db.commit()

    def poll(self):
        """Start the next queued build, else the next queued export; one at a time."""
        if self.future is not None and not self.future.done():
            return False
        if self.future is not None:
            finished = self.future
            self.future = None
            finished.result()  # Report unexpected failures through the main worker log.
        with self.sessions() as db:
            package = db.query(StudyPackage).filter_by(status="queued").order_by(StudyPackage.id).with_for_update(skip_locked=True).first()
            if package is not None:
                package.status = "building"
                package.started_at = utcnow()
                package_id, task = package.id, self.build
            else:
                package = db.query(StudyPackage).filter_by(export_status="queued").order_by(
                    StudyPackage.export_requested_at).with_for_update(skip_locked=True).first()
                if package is None:
                    return False
                package.export_status = "exporting"
                package_id, task = package.id, self.export
            db.commit()
        self.future = self.executor.submit(task, package_id)
        return True

    def export(self, package_id):
        """Copy a revision, including its linked spectra, into a new self-contained folder."""
        with self.sessions() as db:
            package = db.get(StudyPackage, package_id)
            try:
                source = Path(self.settings.study_package_root).resolve() / f"package-{package_id}"
                root = Path(self.settings.study_package_export_root).resolve()
                root.mkdir(parents=True, exist_ok=True)
                needed = sum(entry["size_bytes"] for entry in
                             json.loads((source / "provenance/manifest.json").read_text())["files"])
                free = shutil.disk_usage(root).free
                if free < needed * 1.05:
                    raise ValueError(f"Export needs {readable_size(needed)} but only {readable_size(free)} are free in {root}")
                destination = root / f"study-{package.study_id}-r{package.revision}-{utcnow():%Y%m%dT%H%M%SZ}"
                db.commit()  # No open transaction while copying large files.
                result = export_package(source, destination)
                package = db.get(StudyPackage, package_id)
                package.export_status, package.export_uri = "exported", destination.as_uri()
                package.export_error, package.export_finished_at = None, utcnow()
                ProvenanceService(db).log(package.study_id, "study.package_exported", source_ref={"package_id": package_id},
                                          output_ref={"uri": package.export_uri, **result})
                AuditService(db).log(None, "study.package_exported", "study_package", str(package_id), result)
                db.commit()
            except Exception as error:
                logging.exception("Study package %s export failed", package_id)
                db.rollback()
                package = db.get(StudyPackage, package_id)
                package.export_status, package.export_error, package.export_finished_at = "export_failed", str(error), utcnow()
                AuditService(db).log(None, "study.package_export_failed", "study_package", str(package_id), {"error": str(error)})
                db.commit()

    def build(self, package_id):
        with self.sessions() as db:
            package = db.get(StudyPackage, package_id)
            destination = Path(self.settings.study_package_root).resolve() / f"package-{package_id}"
            try:
                snapshot = PackageSnapshot.model_validate(package.snapshot_json)
                for assay in snapshot.assays:
                    source = db.query(WorkflowJob).filter_by(id=assay.job_id, study_id=package.study_id,
                        assay_id=assay.assay_id, status="succeeded", deleted_at=None).first()
                    if source is None:
                        raise ValueError(f"Source job {assay.job_id} is no longer available")
                verified = verified_spectral_files(db, package.study_id, package_id)
                # End the read transaction while hashing large files. The frozen
                # snapshot and checked source bytes, not live ORM objects, drive export.
                db.commit()
                report = build_package(snapshot, destination, verified)
                validation_dir = destination / "validation"
                validation_dir.mkdir()
                official = {}
                command = [self.settings.metabolights_python, str(Path(__file__).parents[1] / "tools/validate_metabolights.py"),
                           str(destination / "submission"), str(validation_dir), str(TEMPLATES),
                           "--opa", self.settings.metabolights_opa, "--study-id", snapshot.metabolights_id]
                try:
                    with (validation_dir / "validator.log").open("w") as log:
                        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
                    official = json.loads((validation_dir / "report.json").read_text())
                except (OSError, ValueError, subprocess.SubprocessError) as error:
                    report["validator_error"] = str(error)
                report["issues"].extend(validation_issues(official))
                report["official_validation"] = "completed" if official.get("completed") is True else "failed"
                report["submission_ready"] = not any(i["severity"] == "error" for i in report["issues"])
                (validation_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
                (destination / "provenance/manifest.json").write_text(json.dumps(report, indent=2) + "\n")
                # A small download: intentionally excludes MAFs, matrices and spectra.
                with zipfile.ZipFile(destination / "metadata-review.zip", "w", zipfile.ZIP_DEFLATED) as archive:
                    for path in sorted((destination / "submission").glob("*.txt")):
                        archive.write(path, "submission/" + path.name)
                    archive.write(validation_dir / "report.json", "validation/report.json")
                    archive.writestr("README.txt", "Metadata review only. This download omits MAFs and spectral data and is not a complete submission.\n")
                package = db.get(StudyPackage, package_id)
                package.status = "complete" if report["submission_ready"] else "validation_failed"
                package.output_uri = destination.as_uri()
                package.report_json = compact_report({key: value for key, value in report.items() if key != "files"})
                package.report_json["metadata_download_sha256"] = hashlib.sha256((destination / "metadata-review.zip").read_bytes()).hexdigest()
                package.error_message = None
                package.finished_at = utcnow()
                source_ref = {"job_ids": [a.job_id for a in snapshot.assays]}
                ProvenanceService(db).log(package.study_id, "study.package_built", source_ref=source_ref,
                    output_ref={"package_id": package.id, "uri": package.output_uri, "status": package.status,
                                "manifest_sha256": hashlib.sha256((destination / "provenance/manifest.json").read_bytes()).hexdigest()},
                    software_versions=report["tools"])
                AuditService(db).log(None, "study.package_built", "study_package", str(package.id), {"status": package.status})
                db.commit()
            except Exception as error:
                logging.exception("Study package %s failed", package_id)
                db.rollback()
                package = db.get(StudyPackage, package_id)
                package.status = "failed"
                package.finished_at = utcnow()
                package.error_message = str(error)
                package.output_uri = None
                AuditService(db).log(None, "study.package_failed", "study_package", str(package.id), {"error": str(error)})
                db.commit()
