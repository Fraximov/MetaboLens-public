"""Map application records into frozen, Study-owned packaging requests.

This is the only place to extend when LabID supplies new metadata. File writing
and scientific validation live outside the request transaction in the worker.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
from urllib.parse import unquote, urlparse

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.errors import bad_request, not_found
from app.core.permissions import require_allowed
from app.core.states import ACCEPTED_INPUT_FILE_STATES, PACKAGE_BUILT_STATES, PACKAGE_PENDING_STATES
from app.core.sample_context import effective_sample_annotations
from app.config import get_settings
from app.models.models import (Assay, AssaySampleLink, Dataset, DatasetFile, MetadataSubmission, ProvenanceEvent,
                               Sample, Study, StudyPackage, WorkflowJob, WorkflowRunArtifact, utcnow)
from app.schemas.study_package import (AcquisitionSnapshot, AssaySnapshot, PackageCheck, PackageFile, PackageRequest,
                                      PackageSnapshot, SampleSnapshot)
from app.services import package_files
from app.services.audit_service import AuditService
from app.services.metabolights_export import controlled_term, controlled_terms, generated_protocols, local_file
from app.services.package_readiness import (export_quantity, finding, metadata_findings, preflight_findings,
                                            resolve_quantity, section_of,
                                            source_findings, summarize)
from app.services.provenance_service import ProvenanceService
from app.services.study_service import StudyService
from app.services.study_metadata_service import StudyMetadataService


ISSUE_LIST_LIMIT = 20
EXPORT_ACTIVE = {"queued", "exporting"}


def problem(message: str, code: str, section: str) -> HTTPException:
    """A 400 for a build request that the readiness check reports as a sectioned finding."""
    error = bad_request(message)
    error.finding = (code, section)
    return error


def packaging_vocabularies() -> dict:
    """Choices the UI offers so typed values match the exported controlled terms."""
    terms = controlled_terms()
    return dict(publication_statuses=[term["name"] for term in terms["publication_status"].values()],
                contact_roles=[term["name"] for term in terms["contact_roles"].values()])


def compact_report(report: dict) -> dict:
    """Bound database and browser payloads; complete lists stay in validation/report.json.

    One unresolved-review finding can list thousands of feature IDs. Each list keeps
    its first entries and records its full length; no finding is dropped.
    """
    issues = []
    for issue in report.get("issues", []):
        issue = dict(issue)
        for key, value in list(issue.items()):
            if isinstance(value, list) and len(value) > ISSUE_LIST_LIMIT:
                issue[key] = value[:ISSUE_LIST_LIMIT]
                issue[f"{key}_total"] = len(value)
        issues.append(issue)
    return {**report, "issues": issues}


class StudyPackagingService:
    def __init__(self, db: Session):
        self.db = db
        self.studies = StudyService(db)

    def get(self, user_id: int, study_id: int, package_id: int) -> StudyPackage:
        self.studies.get(user_id, study_id)
        package = self.db.query(StudyPackage).filter_by(id=package_id, study_id=study_id).first()
        if package is None:
            raise not_found("Study package not found")
        return package

    def list(self, user_id: int, study_id: int) -> list[dict]:
        self.studies.get(user_id, study_id)
        # Summaries only: one build's findings come from get().
        return [self.serialize(p, detail=False) for p in self.db.query(StudyPackage).filter_by(study_id=study_id)
                .order_by(StudyPackage.revision.desc()).all()]

    def request_export(self, user_id: int, study_id: int, package_id: int) -> StudyPackage:
        """Queue a self-contained copy of a built revision; the worker copies and re-verifies it."""
        package = self.get(user_id, study_id, package_id)
        study = self.db.get(Study, study_id)
        require_allowed(self.studies.perms.can_manage_submission(user_id, study_id, study.status),
                        "Study editing or operator permission is required to export a package")
        if package.status not in PACKAGE_BUILT_STATES or not package.output_uri:
            raise bad_request("Only a built revision can be exported")
        if package.export_status in EXPORT_ACTIVE:
            return package
        package.export_status, package.export_requested_by, package.export_requested_at = "queued", user_id, utcnow()
        package.export_uri = package.export_error = package.export_finished_at = None
        AuditService(self.db).log(user_id, "study.package_export_requested", "study_package", str(package.id),
                                 {"study_id": study_id, "revision": package.revision})
        self.db.commit()
        self.db.refresh(package)
        return package

    def delete(self, user_id: int, study_id: int, package_id: int) -> dict:
        """Remove a revision and its folder; links are removed, never followed, and exports are kept."""
        package = self.get(user_id, study_id, package_id)
        require_allowed(self.studies.perms.can_review_study(user_id, study_id),
                        "Operator or admin permission is required to delete a package revision")
        if package.status in PACKAGE_PENDING_STATES or package.export_status in EXPORT_ACTIVE:
            raise bad_request("Wait until this revision's build or export finishes before deleting it")
        root = Path(get_settings().study_package_root).resolve()
        folder = root / f"package-{package.id}"
        freed = 0
        if folder.exists() or folder.is_symlink():
            if folder.is_symlink() or folder.resolve().parent != root:
                raise bad_request("The package folder is not where packages are stored")
            freed = sum(path.lstat().st_size for path in folder.rglob("*") if path.is_file() and not path.is_symlink())
            shutil.rmtree(folder)
        result = dict(deleted_package_id=package.id, revision=package.revision, freed_bytes=freed,
                      kept_export_uri=package.export_uri)
        AuditService(self.db).log(user_id, "study.package_deleted", "study_package", str(package.id),
                                 {"study_id": study_id, **result})
        self.db.delete(package)
        self.db.commit()
        return result

    def _next_revision(self, study_id: int) -> int:
        """Numbers are never reused, so a deleted revision's audit entries and export folder stay unambiguous."""
        kept = self.db.query(func.max(StudyPackage.revision)).filter_by(study_id=study_id).scalar() or 0
        requested = (self.db.query(ProvenanceEvent.output_ref_json)
                     .filter_by(study_id=study_id, event_type="study.package_requested"))
        return max([kept, *(int((ref or {}).get("revision") or 0) for (ref,) in requested)]) + 1

    def metadata_download(self, user_id: int, study_id: int, package_id: int) -> Path:
        package = self.get(user_id, study_id, package_id)
        if package.status not in PACKAGE_BUILT_STATES or not package.output_uri:
            raise bad_request("This package has no completed metadata download")
        root = Path(get_settings().study_package_root).resolve()
        path = root / f"package-{package.id}" / "metadata-review.zip"
        if path.resolve().parent != root / f"package-{package.id}" or path.is_symlink() or not path.is_file():
            raise not_found("Package metadata download is unavailable")
        if path.stat().st_size > 10 * 1024 * 1024:
            raise bad_request("Metadata review exceeds the 10 MB browser limit; use the prepared folder")
        expected = package.report_json.get("metadata_download_sha256")
        if not expected or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise bad_request("Package metadata checksum changed")
        return path

    def _built_folder(self, package: StudyPackage) -> Path:
        if package.status not in PACKAGE_BUILT_STATES or not package.output_uri:
            raise bad_request("This revision has no built files")
        root = Path(get_settings().study_package_root).resolve()
        folder = root / f"package-{package.id}"
        if folder.is_symlink() or not folder.is_dir():
            raise not_found("This revision's folder is unavailable")
        return folder

    def files(self, user_id: int, study_id: int, package_id: int) -> dict:
        """What this revision sends to MetaboLights: its submission folder, links not followed."""
        package = self.get(user_id, study_id, package_id)
        issues = (package.report_json or {}).get("issues", [])
        return dict(package_id=package.id, revision=package.revision,
                    entries=package_files.listing(self._built_folder(package), issues))

    def file_preview(self, user_id: int, study_id: int, package_id: int, path: str) -> dict:
        package = self.get(user_id, study_id, package_id)
        issues = (package.report_json or {}).get("issues", [])
        return package_files.preview(self._built_folder(package), path, issues)

    @staticmethod
    def serialize(package: StudyPackage, detail: bool = True) -> dict:
        """A build with per-section counts; ``detail`` adds its findings with bounded ID lists."""
        snapshot = package.snapshot_json
        # Spectral references serve checksum reuse in the worker, not the browser.
        stored = {key: value for key, value in (package.report_json or {}).items() if key not in {"files", "spectral_files"}}
        issues = [dict(issue, section=section_of(issue)) for issue in stored.get("issues", [])]
        result = dict(id=package.id, study_id=package.study_id, revision=package.revision, status=package.status,
            created_at=package.created_at, started_at=package.started_at, finished_at=package.finished_at,
            output_uri=package.output_uri, error_message=package.error_message, summary=summarize(issues),
            metabolights_id=snapshot.get("metabolights_id", ""),
            sources=[{key: a.get(key) for key in ("assay_id", "assay_name", "job_id", "manifest_file_id",
                     "polarity", "review_revision")} for a in snapshot.get("assays", [])],
            omitted_assay_ids=snapshot.get("omitted_assay_ids", []),
            sample_count=len(snapshot.get("samples", [])),
            export=dict(status=package.export_status, uri=package.export_uri, error=package.export_error,
                        requested_at=package.export_requested_at, finished_at=package.export_finished_at))
        if detail:
            result["report"] = compact_report({**stored, "issues": issues})
        return result

    def choices(self, user_id: int, study_id: int) -> dict:
        study = self.studies.get(user_id, study_id)
        assays = []
        for assay in self.db.query(Assay).filter_by(study_id=study_id).order_by(Assay.id):
            rows = self.db.query(WorkflowJob, DatasetFile).join(
                WorkflowRunArtifact, WorkflowRunArtifact.workflow_job_id == WorkflowJob.id).join(
                DatasetFile, DatasetFile.id == WorkflowRunArtifact.dataset_file_id).filter(
                WorkflowJob.study_id == study_id, WorkflowJob.assay_id == assay.id,
                WorkflowJob.status == "succeeded", WorkflowJob.deleted_at.is_(None),
                WorkflowRunArtifact.artifact_role == "metabolensr_manifest").order_by(WorkflowJob.id.desc()).all()
            submissions = {record.id: record.normalized_payload_json or {} for record in
                           self.db.query(MetadataSubmission).filter_by(study_id=study_id, assay_id=assay.id)}
            sources = []
            for job, file in rows:
                inputs = job.input_manifest_json or {}
                descriptor = (inputs.get("engine") or {}).get("descriptor") or {}
                if descriptor.get("engine_id") not in {None, "msdial5"}:
                    continue
                acquisitions = inputs.get("selected_files", [])
                package = descriptor.get("package") or {}
                source_metadata = dict((package.get("config") or {}).get("source_metadata") or {})
                quantity = resolve_quantity(source_metadata.get("quantitation_type"), self._export_quantity(job.id))
                method = submissions.get(inputs.get("metadata_submission_id"), {})
                sources.append(dict(job_id=job.id, manifest_file_id=file.id,
                    bundle_name=Path(file.original_filename).name, run_name=job.run_name,
                    created_at=job.created_at, finished_at=job.finished_at,
                    review_revision=(inputs.get("feature_review") or {}).get("revision"),
                    engine=descriptor.get("engine_id") or "msdial5",
                    engine_version=source_metadata.get("version"), metabolensr_version=package.get("version"),
                    quantity=quantity,
                    # What a build will append to these protocols for this run.
                    generated_protocols=generated_protocols(source_metadata, package.get("version") or "", quantity),
                    polarity=(method.get("instrument") or {}).get("ms_polarity_mode") or "",
                    separation_mode=(method.get("chromatography") or {}).get("separation_mode") or "",
                    column_name=(method.get("chromatography") or {}).get("column_name") or "",
                    spectral_bytes=sum(a.get("file_size_bytes") or 0 for a in acquisitions),
                    acquisition_count=len(acquisitions),
                    sample_ids=sorted({a["sample_id"] for a in acquisitions if a.get("sample_id") is not None}),
                    unmapped_acquisition_count=sum(a.get("sample_id") is None for a in acquisitions)))
            assays.append(dict(assay_id=assay.id, assay_name=assay.name, sources=sources))
        samples = self.db.query(Sample).filter_by(study_id=study_id).order_by(Sample.id).all()
        allowed = self.studies.perms.can_manage_submission(user_id, study_id, study.status)
        latest = self.db.query(StudyPackage).filter_by(study_id=study_id).order_by(StudyPackage.revision.desc()).first()
        last_selection = ({key: latest.request_json.get(key) for key in ("metabolights_id", "sources", "omitted_assay_ids")}
                          if latest else None)
        return dict(study_id=study.id, title=study.title, description=study.description or "", status=study.status,
                    assays=assays, samples=[dict(sample_id=s.id, name=s.name) for s in samples],
                    can_build=allowed, last_selection=last_selection, next_revision=self._next_revision(study_id),
                    vocabularies=packaging_vocabularies())

    def _export_quantity(self, job_id: int) -> str | None:
        """What this run's MS-DIAL mzTab-M export names its values: "height", "area" or None."""
        record = self.db.query(DatasetFile).join(
            WorkflowRunArtifact, WorkflowRunArtifact.dataset_file_id == DatasetFile.id).filter(
            WorkflowRunArtifact.workflow_job_id == job_id, WorkflowRunArtifact.artifact_role == "msdial_mztabm").first()
        try:
            return export_quantity(local_file(record.storage_uri)) if record else None
        except ValueError:
            return None

    def sample_snapshots(self, records, assays, factors):
        """Reuse recorded sample-sheet values; never guess a group's meaning.

        A shared biological sample has one factor value in ISA. If selected
        assays disagree, the operator must resolve that conflict in the sheet.
        """
        links = {(link.assay_id, link.sample_id): link.annotations_json for link in
            self.db.query(AssaySampleLink).filter(
                AssaySampleLink.assay_id.in_([a.assay_id for a in assays]),
                AssaySampleLink.sample_id.in_([s.id for s in records]))}
        assay_samples = {assay.assay_id: {a.sample_id for a in assay.acquisitions} for assay in assays}
        snapshots = []
        for sample in records:
            factor_values = {}
            for factor in factors:
                values = set()
                for assay in assays:
                    if sample.id not in assay_samples[assay.assay_id]:
                        continue
                    annotations = effective_sample_annotations(
                        sample.annotations_json, links.get((assay.assay_id, sample.id)))
                    if factor.source_column == "group_label":
                        value = annotations.get("group_label")
                    else:
                        section, name = factor.source_column.split(".", 1)
                        value = (annotations.get(section) or {}).get(name)
                    if isinstance(value, (dict, list)):
                        raise problem(f"Factor {factor.name} needs a single recorded value for sample {sample.name}", "factor_conflict", "samples")
                    values.add("" if value is None else str(value).strip())
                if len(values) > 1:
                    raise problem(f"Factor {factor.name} has conflicting values across selected assays for sample {sample.name}. Correct the sample sheet before packaging.", "factor_conflict", "samples")
                factor_values[factor.name] = next(iter(values), "")
            snapshots.append(SampleSnapshot(sample_id=sample.id, name=sample.name,
                organism=sample.organism or "", organism_part=sample.matrix or "", sample_type=sample.sample_type or "",
                ontology_terms=(sample.annotations_json or {}).get("ontology_terms") or {},
                factor_values=factor_values))
        return snapshots

    @staticmethod
    def file_snapshot(file: DatasetFile, role: str) -> PackageFile:
        if not file.checksum_sha256 and role != "input":
            raise bad_request(f"File {file.id} needs a registered checksum before packaging")
        return PackageFile(file_id=file.id, filename=file.original_filename, storage_uri=file.storage_uri,
                           sha256=file.checksum_sha256, size_bytes=file.file_size_bytes, role=role)

    def assay_snapshot(self, study_id: int, selection) -> AssaySnapshot:
        job = self.db.query(WorkflowJob).filter_by(id=selection.job_id, study_id=study_id,
                    assay_id=selection.assay_id, status="succeeded", deleted_at=None).first()
        if job is None:
            raise bad_request("Select a completed, available job belonging to this Study and assay")
        assay = self.db.query(Assay).filter_by(id=selection.assay_id, study_id=study_id).one()
        if assay.metabolomics_assay and assay.metabolomics_assay.assay_type != "LC_MS":
            raise bad_request("MetaboLights packaging currently supports LC-MS assays")
        artifacts = self.db.query(WorkflowRunArtifact, DatasetFile).join(
            DatasetFile, DatasetFile.id == WorkflowRunArtifact.dataset_file_id).join(
            Dataset, Dataset.id == DatasetFile.dataset_id).filter(
            WorkflowRunArtifact.workflow_job_id == job.id, Dataset.study_id == study_id,
            Dataset.assay_id == assay.id).all()
        selected = [file for artifact, file in artifacts if file.id == selection.manifest_file_id
                    and artifact.artifact_role == "metabolensr_manifest"]
        if len(selected) != 1:
            raise bad_request("Select a registered final MetaboLensR manifest for this job")
        manifest_file = selected[0]
        try:
            manifest_path = local_file(manifest_file.storage_uri)
            content = manifest_path.read_bytes()
            if hashlib.sha256(content).hexdigest() != manifest_file.checksum_sha256:
                raise ValueError("Selected manifest checksum changed")
            manifest = json.loads(content)
            if (manifest.get("schema_version") != "2.0" or manifest.get("bundle_type") != "final"
                    or manifest.get("source", {}).get("tool") != "msdial"):
                raise ValueError("Select a final schema-2 MS-DIAL bundle")
            files = []
            for artifact, file in artifacts:
                is_member = Path(unquote(urlparse(file.storage_uri).path)).parent == manifest_path.parent
                is_annotation = artifact.artifact_role in {"msdial_alignment", "msdial_spectra", "msdial_mztabm"} or artifact.artifact_role.startswith("sirius_")
                if is_member or is_annotation:
                    files.append(self.file_snapshot(file, artifact.artifact_role))
        except (OSError, ValueError) as error:
            raise bad_request(str(error)) from error

        acquisitions = []
        inputs = job.input_manifest_json.get("selected_files", [])
        if not inputs:
            raise bad_request("This job has no frozen acquisition identities")
        for selected_file in inputs:
            file = self.db.query(DatasetFile).join(Dataset).filter(
                DatasetFile.id == selected_file["file_asset_id"], Dataset.study_id == study_id,
                Dataset.assay_id == assay.id, DatasetFile.status.in_(ACCEPTED_INPUT_FILE_STATES)).first()
            if (file is None or file.sample_id is None or file.sample_id != selected_file.get("sample_id") or
                    file.checksum_sha256 != selected_file.get("checksum_sha256") or
                    file.storage_uri != selected_file.get("storage_uri")):
                raise bad_request("A registered acquisition no longer matches the frozen job input")
            acquisitions.append(AcquisitionSnapshot(acquisition_id=f"file-{file.id}", sample_id=file.sample_id,
                                file=self.file_snapshot(file, "input")))

        metadata_id = job.input_manifest_json.get("metadata_submission_id")
        metadata = self.db.query(MetadataSubmission).filter_by(id=metadata_id, study_id=study_id, assay_id=assay.id).first()
        payload = metadata.normalized_payload_json if metadata else {}
        instrument = payload.get("instrument", {})
        chromatography = payload.get("chromatography", {})
        acquisition = payload.get("acquisition", {})
        # Explicit mapping: a newly imported field does not silently become ISA metadata.
        method = {"Parameter Value[Instrument]": instrument.get("ms_model", ""),
                  "Parameter Value[Ion source]": instrument.get("ms_ion_source", ""),
                  "Parameter Value[Chromatography Instrument]": chromatography.get("lc_system", ""),
                  "Parameter Value[Column model]": chromatography.get("column_name", ""),
                  "Parameter Value[Column type]": chromatography.get("separation_mode", ""),
                  "Parameter Value[Scan m/z range]": acquisition.get("mz_range", "")}
        method = {key: str(value or "") for key, value in method.items()}
        # Exact aliases only: unrecognised values stay visible without a guessed term.
        # Amide is stationary-phase chemistry, not a separation mode.
        method_terms = {}
        for field in controlled_terms()["assay_parameters"]:
            term = controlled_term("assay_parameters", method[field], field)
            if term:
                method[field] = term["name"]
                method_terms[field] = term
        return AssaySnapshot(assay_id=assay.id, assay_name=assay.name, job_id=job.id,
            manifest_file_id=manifest_file.id, polarity=instrument.get("ms_polarity_mode", "") or "",
            method=method, method_ontology_terms=method_terms, metadata_submission_id=metadata_id,
            review_revision=(job.input_manifest_json.get("feature_review") or {}).get("revision"),
            workflow={"input_manifest": deepcopy(job.input_manifest_json), "parameters": deepcopy(job.parameter_values_json)},
            files=files, acquisitions=acquisitions)

    @staticmethod
    def _study_fields(study: Study, metadata, metabolights_id: str) -> dict:
        """Study-level snapshot values from the saved submission metadata."""
        return dict(study_id=study.id, title=study.title, description=study.description or "",
            metabolights_id=metabolights_id,
            contacts=[{"First Name": c.first_name, "Last Name": c.last_name, "Email": c.email,
                       "Affiliation": c.affiliation, "Roles": c.role} for c in metadata.contacts],
            submission_date=metadata.submission_date.isoformat() if metadata.submission_date else "",
            public_release_date=metadata.public_release_date.isoformat() if metadata.public_release_date else "",
            design_descriptors=[d.model_dump() for d in metadata.design_descriptors],
            publications=[p.model_dump() for p in metadata.publications],
            factors=[f.model_dump() for f in metadata.factors],
            collection_protocol=metadata.collection_protocol,
            metadata_origin=f"Saved Study metadata revision {metadata.revision} and sample records at build request time")

    # No list[...] annotation here: the class's own list() method shadows the builtin.
    def _snapshot(self, study: Study, metabolights_id: str, sources, omitted) -> PackageSnapshot:
        """Freeze a source selection and the current records; raises 400 for an invalid selection."""
        selected_ids = [s.assay_id for s in sources]
        study_ids = {row[0] for row in self.db.query(Assay.id).filter_by(study_id=study.id)}
        if (len(set(selected_ids)) != len(selected_ids) or len(set(omitted)) != len(omitted)
                or set(selected_ids) & set(omitted) or set(selected_ids) | set(omitted) != study_ids):
            raise bad_request("Include each Study assay once or explicitly omit it")
        assays = [self.assay_snapshot(study.id, s) for s in sources]
        sample_ids = {a.sample_id for assay in assays for a in assay.acquisitions}
        records = self.db.query(Sample).filter(Sample.study_id == study.id, Sample.id.in_(sample_ids)).order_by(Sample.id).all()
        if {s.id for s in records} != sample_ids:
            raise problem("A selected acquisition lacks a biological sample in this Study",
                          "acquisition_without_sample", "samples")
        metadata = StudyMetadataService(self.db).snapshot_metadata(study)
        protocol_by_assay = {a.assay_id: a.protocols for a in metadata.assays}
        for assay in assays:
            assay.protocols = dict(protocol_by_assay.get(assay.assay_id, {}))
        return PackageSnapshot(**self._study_fields(study, metadata, metabolights_id), assays=assays,
                               omitted_assay_ids=omitted, samples=self.sample_snapshots(records, assays, metadata.factors))

    def check(self, user_id: int, study_id: int, payload: PackageCheck) -> dict:
        """Readiness of a source selection without building, copying or hashing anything."""
        study = self.studies.get(user_id, study_id)
        issues, snapshot = [], None
        if payload.sources:
            try:
                snapshot = self._snapshot(study, payload.metabolights_id, payload.sources, payload.omitted_assay_ids)
            except HTTPException as error:
                code, section = getattr(error, "finding", ("selection_invalid", "sources"))
                issues.append(finding(code, section, str(error.detail)))
        else:
            issues.append(finding("no_sources", "sources", "Select a completed workflow result for at least one assay."))
        metadata = StudyMetadataService(self.db).snapshot_metadata(study)
        if snapshot is None:
            snapshot = PackageSnapshot(**self._study_fields(study, metadata, payload.metabolights_id), assays=[], samples=[])
        verified = {reference["file_id"] for package in self.db.query(StudyPackage).filter(
                        StudyPackage.study_id == study_id, StudyPackage.status.in_(PACKAGE_BUILT_STATES))
                    for reference in (package.report_json or {}).get("spectral_files", [])}
        source_issues, spectral = source_findings(snapshot, verified)
        issues = [*metadata_findings(snapshot), *preflight_findings(snapshot), *issues, *source_issues]
        return dict(compact_report({"issues": issues}), spectral=spectral, metadata_revision=metadata.revision,
                    **summarize(issues))

    def request_build(self, user_id: int, study_id: int, payload: PackageRequest) -> StudyPackage:
        self.studies.get(user_id, study_id)
        # Refresh after acquiring the same lock used by metadata saves. A cached
        # Study may predate a save that committed while this request was waiting.
        study = self.db.query(Study).filter_by(id=study_id).populate_existing().with_for_update().one()
        require_allowed(self.studies.perms.can_manage_submission(user_id, study_id, study.status),
                        "Study editing or operator permission is required to build a package")
        request = payload.model_dump(mode="json")
        existing = self.db.query(StudyPackage).filter_by(study_id=study_id, request_key=str(payload.request_key)).first()
        if existing:
            if existing.request_json != request:
                raise bad_request("This request key was already used for a different source selection")
            return existing
        snapshot = self._snapshot(study, payload.metabolights_id, payload.sources, payload.omitted_assay_ids)
        revision = self._next_revision(study_id)
        package = StudyPackage(study_id=study_id, revision=revision, request_key=str(payload.request_key),
                               requested_by=user_id, request_json=request, snapshot_json=snapshot.model_dump(mode="json"))
        self.db.add(package)
        self.db.flush()
        AuditService(self.db).log(user_id, "study.package_requested", "study_package", str(package.id), request)
        ProvenanceService(self.db).log(study_id, "study.package_requested", source_ref={"sources": request["sources"]},
                                       output_ref={"package_id": package.id, "revision": revision})
        self.db.commit()
        self.db.refresh(package)
        return package
