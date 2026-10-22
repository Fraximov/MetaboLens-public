from copy import deepcopy
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.errors import bad_request, not_found
from app.core.permissions import PermissionChecker, require_allowed
from app.core.sample_context import ACQUISITION_ANNOTATION_KEYS
from app.core.sample_metadata import normalize_sample_annotations, normalize_sample_type
from app.core.states import STUDY_ALLOWED_TRANSITIONS, STUDY_STATUSES
from app.models.models import (
    Acquisition,
    Assay,
    AssaySampleLink,
    AuditEvent,
    Dataset,
    DatasetFile,
    DatasetSampleLink,
    GCMSAssayDetails,
    LCMSAssayDetails,
    MetadataSubmission,
    MetabolomicsAssay,
    NMRAssayDetails,
    RoleAssignment,
    Sample,
    Study,
    User,
    WorkflowJob,
)
from app.schemas.assay import AssayCreate, normalize_metabolomics_assay_type, normalize_platform
from app.schemas.study import SampleCreate, SamplePatch, StudyCreate, StudyPatch
from app.services.audit_service import AuditService
from app.services.package_references import linking_revisions
from app.services.role_service import RoleService


DEFAULT_ANALYTE_DOMAIN = "metabolomics"
DEFAULT_MEASUREMENT_TYPE = "metabolite_profiling"
PLATFORM_METABOLOMICS = "metabolomics"


class StudyService:
    def __init__(self, db: Session):
        self.db = db
        self.roles = RoleService(db)
        self.perms = PermissionChecker(self.roles)
        self.audit = AuditService(db)

    def _default_assay_id_for_study(self, study_id: int) -> int | None:
        row = self.db.query(Assay.id).filter(Assay.study_id == study_id, Assay.is_default.is_(True)).first()
        return row[0] if row else None

    def serialize_study(self, study: Study) -> dict:
        return {
            "id": study.id,
            "public_or_internal_identifier": study.public_or_internal_identifier,
            "project_id": study.project_id,
            "title": study.title,
            "description": study.description,
            "status": study.status,
            "created_by": study.created_by,
            "created_at": study.created_at,
            "updated_at": study.updated_at,
            "default_assay_id": self._default_assay_id_for_study(study.id),
        }

    def list_for_user(self, user_id: int) -> list[dict]:
        has_global = (
            self.db.query(RoleAssignment)
            .filter(RoleAssignment.user_id == user_id, RoleAssignment.study_id.is_(None))
            .first()
            is not None
        )

        base_query = (
            self.db.query(
                Study,
                func.count(func.distinct(Sample.id)).label("sample_count"),
                func.count(func.distinct(Assay.id)).label("assay_count"),
            )
            .outerjoin(Sample, Sample.study_id == Study.id)
            .outerjoin(Assay, Assay.study_id == Study.id)
            .group_by(Study.id)
            .order_by(Study.id.desc())
        )

        if not has_global:
            study_ids = (
                self.db.query(RoleAssignment.study_id)
                .filter(RoleAssignment.user_id == user_id, RoleAssignment.study_id.is_not(None))
                .all()
            )
            filtered_ids = [row[0] for row in study_ids if row[0] is not None]
            if not filtered_ids:
                return []
            base_query = base_query.filter(Study.id.in_(filtered_ids))

        rows = base_query.all()
        result: list[dict] = []
        for study, sample_count, assay_count in rows:
            serialized = self.serialize_study(study)
            serialized["sample_count"] = int(sample_count or 0)
            serialized["assay_count"] = int(assay_count or 0)
            result.append(serialized)
        return result

    def _new_assay(
        self,
        *,
        study: Study,
        name: str,
        platform: str,
        metabolomics_assay_type: str,
        created_by: int,
        is_default: bool,
    ) -> Assay:
        assay = Assay(
            study_id=study.id,
            name=name,
            platform=platform,
            status="draft",
            is_default=is_default,
            created_by=created_by,
        )
        if platform == PLATFORM_METABOLOMICS:
            assay.metabolomics_assay = self._build_metabolomics_assay(metabolomics_assay_type)
        self.db.add(assay)
        self.db.flush()
        return assay

    @staticmethod
    def _default_assay_technique(metabolomics_assay_type: str) -> str:
        normalized_type = normalize_metabolomics_assay_type(metabolomics_assay_type)
        if normalized_type == "GC_MS":
            return "gas_chromatography_mass_spectrometry"
        if normalized_type == "NMR":
            return "nuclear_magnetic_resonance"
        return "liquid_chromatography_mass_spectrometry"

    def _build_metabolomics_assay(self, metabolomics_assay_type: str) -> MetabolomicsAssay:
        normalized_type = normalize_metabolomics_assay_type(metabolomics_assay_type)
        metabolomics_assay = MetabolomicsAssay(
            assay_type=normalized_type,
            assay_technique=self._default_assay_technique(normalized_type),
            measurement_type=DEFAULT_MEASUREMENT_TYPE,
            analyte_domain=DEFAULT_ANALYTE_DOMAIN,
        )
        if normalized_type == "GC_MS":
            metabolomics_assay.gcms_details = GCMSAssayDetails()
        elif normalized_type == "NMR":
            metabolomics_assay.nmr_details = NMRAssayDetails()
        else:
            metabolomics_assay.lcms_details = LCMSAssayDetails()
        return metabolomics_assay

    def _create_default_assay(self, study: Study) -> Assay:
        return self._new_assay(
            study=study,
            name=f"{study.public_or_internal_identifier}-default-assay",
            platform=PLATFORM_METABOLOMICS,
            metabolomics_assay_type="LC_MS",
            created_by=study.created_by,
            is_default=True,
        )

    def _normalize_sample_metadata(self, sample_type: str | None, annotations_json: dict | None) -> tuple[str, dict]:
        try:
            return normalize_sample_type(sample_type), normalize_sample_annotations(annotations_json)
        except ValueError as exc:
            raise bad_request(str(exc)) from exc

    def get_default_assay(self, study_id: int) -> Assay:
        assay = self.db.query(Assay).filter(Assay.study_id == study_id, Assay.is_default.is_(True)).first()
        if assay is None:
            raise not_found("Default assay not found")
        return assay

    def create(self, user_id: int, payload: StudyCreate) -> Study:
        identifier = f"STD-{uuid4().hex[:10].upper()}"
        study = Study(
            public_or_internal_identifier=identifier,
            project_id=payload.project_id,
            title=payload.title,
            description=payload.description,
            status="draft",
            created_by=user_id,
        )
        self.db.add(study)
        self.db.flush()
        self.roles.assign_role(user_id=user_id, role="submitter", study_id=study.id, granted_by=user_id)
        default_assay = self._create_default_assay(study)
        self.audit.log(user_id, "study.create", "study", str(study.id), {"status": study.status, "default_assay_id": default_assay.id})
        self.db.commit()
        self.db.refresh(study)
        return study

    def get(self, user_id: int, study_id: int) -> Study:
        study = self.db.query(Study).filter(Study.id == study_id).first()
        if not study:
            raise not_found("Study not found")
        require_allowed(self.perms.can_read_study(user_id, study.id), "No access to study")
        return study

    def get_assay(self, user_id: int, assay_id: int) -> Assay:
        """Return the assay after checking that the user may read its study."""
        assay = self.db.query(Assay).filter(Assay.id == assay_id).first()
        if not assay:
            raise not_found("Assay not found")
        self.get(user_id, assay.study_id)
        return assay

    def patch(self, user_id: int, study_id: int, payload: StudyPatch) -> Study:
        self.get(user_id, study_id)
        # Share the metadata editor's row lock and revision so older forms cannot overwrite this edit.
        study = self.db.query(Study).filter_by(id=study_id).populate_existing().with_for_update().one()
        self.perms.require_edit_study(user_id, study.id, study.status)
        changes = {key: value for key, value in payload.model_dump(exclude_none=True).items()
                   if value != getattr(study, key)}
        for key, value in changes.items():
            setattr(study, key, value)
        if changes:
            metadata = study.metadata_json or {}
            study.metadata_json = {**metadata, "revision": metadata.get("revision", 0) + 1}
        self.audit.log(user_id, "study.update", "study", str(study.id), {"status": study.status})
        self.db.commit()
        self.db.refresh(study)
        return study

    def submit(self, user_id: int, study_id: int) -> Study:
        study = self.get(user_id, study_id)
        self.perms.require_edit_study(user_id, study.id, study.status)
        self._transition(study, "submitted")
        self.audit.log(user_id, "study.submit", "study", str(study.id), {"new_status": study.status})
        self.db.commit()
        self.db.refresh(study)
        return study

    def transition(self, user_id: int, study_id: int, new_status: str) -> Study:
        study = self.get(user_id, study_id)
        require_allowed(self.perms.can_review_study(user_id, study.id), "Review transition not allowed")
        self._transition(study, new_status, strict_from_current=False)
        self.audit.log(user_id, "study.transition", "study", str(study.id), {"new_status": study.status})
        self.db.commit()
        self.db.refresh(study)
        return study

    def reopen_to_draft(self, user_id: int, study_id: int) -> Study:
        study = self.get(user_id, study_id)
        require_allowed(self.perms.can_review_study(user_id, study.id), "Only operator/admin can reopen")
        self._transition(study, "draft", strict_from_current=False)
        self.audit.log(user_id, "study.reopen", "study", str(study.id), {"new_status": "draft"})
        self.db.commit()
        self.db.refresh(study)
        return study

    def _transition(self, study: Study, new_status: str, strict_from_current: bool = True) -> None:
        normalized_status = (new_status or "").strip().lower()
        if normalized_status not in STUDY_STATUSES:
            valid = ", ".join(sorted(STUDY_STATUSES))
            raise bad_request(f"Invalid study status '{new_status}'. Valid statuses: {valid}")
        if strict_from_current:
            allowed = STUDY_ALLOWED_TRANSITIONS.get(study.status, set())
            if normalized_status not in allowed:
                raise bad_request(f"Invalid status transition: {study.status} -> {normalized_status}")
        study.status = normalized_status

    def add_sample(self, user_id: int, study_id: int, payload: SampleCreate) -> Sample:
        study = self.get(user_id, study_id)
        self.perms.require_edit_study(user_id, study.id, study.status)
        sample_type, annotations_json = self._normalize_sample_metadata(payload.sample_type, payload.annotations_json)
        sample = Sample(
            study_id=study_id,
            name=payload.name,
            description=payload.description,
            replicate_number=payload.replicate_number,
            dilution_factor=payload.dilution_factor,
            sample_type=sample_type,
            organism=payload.organism,
            matrix=payload.matrix,
            annotations_json=annotations_json,
        )
        self.db.add(sample)
        self.db.flush()
        default_assay = self.get_default_assay(study_id)
        self.db.add(AssaySampleLink(assay_id=default_assay.id, sample_id=sample.id, sample_role="input"))
        self.audit.log(user_id, "sample.create", "sample", "new", {"study_id": study_id, "sample_name": payload.name})
        self.db.commit()
        self.db.refresh(sample)
        return sample

    def list_samples(self, user_id: int, study_id: int) -> list[Sample]:
        self.get(user_id, study_id)
        return self.db.query(Sample).filter(Sample.study_id == study_id).order_by(Sample.id.desc()).all()

    def update_sample(self, user_id: int, study_id: int, sample_id: int, payload: SamplePatch) -> Sample:
        study = self.get(user_id, study_id)
        require_allowed(self.perms.can_edit_sample_details(user_id, study.id, study.status),
                        "Submitter, operator or admin role is required; archived Studies are read-only.")
        sample = self.db.query(Sample).filter(Sample.id == sample_id, Sample.study_id == study_id).first()
        if sample is None:
            raise not_found("Sample not found")

        updates = payload.model_dump(exclude_unset=True)
        if ACQUISITION_ANNOTATION_KEYS.intersection(updates.get("annotations_json") or {}):
            raise bad_request("Edit injection metadata on the assay acquisition, not the study sample")
        before = {field: deepcopy(getattr(sample, field)) for field in updates}
        if "sample_type" in updates:
            sample.sample_type = updates["sample_type"]
        if "annotations_json" in updates:
            existing_annotations = sample.annotations_json if isinstance(sample.annotations_json, dict) else {}
            sample.annotations_json = {**existing_annotations, **(updates["annotations_json"] or {})}
        for field in ("name", "description", "replicate_number", "dilution_factor", "organism", "matrix"):
            if field in updates:
                setattr(sample, field, updates[field])

        self.audit.log(
            user_id,
            "sample.update",
            "sample",
            str(sample.id),
            # Values before and after make corrections outside draft traceable.
            {"study_id": study_id, "study_status": study.status, "updated_fields": sorted(updates.keys()),
             "changes": {field: {"before": before[field], "after": getattr(sample, field)}
                         for field in sorted(updates) if before[field] != getattr(sample, field)}},
        )
        self.db.commit()
        self.db.refresh(sample)
        return sample

    def delete_sample(self, user_id: int, study_id: int, sample_id: int) -> dict:
        study = self.get(user_id, study_id)
        self.perms.require_edit_study(user_id, study.id, study.status)
        sample = self.db.query(Sample).filter(Sample.id == sample_id, Sample.study_id == study_id).first()
        if sample is None:
            raise not_found("Sample not found")

        study_dataset_ids = self.db.query(Dataset.id).filter(Dataset.study_id == study_id)
        raw_dataset_ids = self.db.query(Dataset.id).filter(Dataset.study_id == study_id, Dataset.dataset_kind == "raw")
        # Deleting the sample removes or unlinks its registered files; linked package
        # revisions would then point at nothing. Their deletion must come first.
        sample_files = {file_id: name for file_id, name in self.db.query(DatasetFile.id, DatasetFile.original_filename)
                        .filter(DatasetFile.sample_id == sample_id, DatasetFile.dataset_id.in_(study_dataset_ids))}
        linked = linking_revisions(self.db, study_id, sample_files)
        if linked:
            listed = "; ".join(f"{sample_files[file_id]} (revision {', '.join(map(str, revisions))})"
                               for file_id, revisions in sorted(linked.items()))
            raise HTTPException(status_code=409, detail=(
                f"Sample {sample.name} has spectral files linked by ISA package revisions: {listed}. "
                "Delete those revisions on the packaging builds page first."))

        deleted_raw_file_count = (
            self.db.query(DatasetFile)
            .filter(DatasetFile.sample_id == sample_id, DatasetFile.dataset_id.in_(raw_dataset_ids))
            .delete(synchronize_session=False)
        )
        cleared_file_sample_count = (
            self.db.query(DatasetFile)
            .filter(DatasetFile.sample_id == sample_id, DatasetFile.dataset_id.in_(study_dataset_ids))
            .update({DatasetFile.sample_id: None}, synchronize_session=False)
        )
        # Derived file references must not retain an acquisition after its sample is removed.
        acquisition_ids = self.db.query(Acquisition.id).filter(Acquisition.sample_id == sample_id)
        self.db.query(DatasetFile).filter(DatasetFile.acquisition_id.in_(acquisition_ids)).update(
            {DatasetFile.acquisition_id: None}, synchronize_session=False)
        self.db.query(Acquisition).filter(Acquisition.sample_id == sample_id).delete(synchronize_session=False)
        self.db.query(DatasetSampleLink).filter(
            DatasetSampleLink.sample_id == sample_id,
            DatasetSampleLink.dataset_id.in_(study_dataset_ids),
        ).delete(synchronize_session=False)
        study_assay_ids = self.db.query(Assay.id).filter(Assay.study_id == study_id)
        self.db.query(AssaySampleLink).filter(
            AssaySampleLink.sample_id == sample_id,
            AssaySampleLink.assay_id.in_(study_assay_ids),
        ).delete(synchronize_session=False)
        self.db.delete(sample)
        self.audit.log(
            user_id,
            "sample.delete",
            "sample",
            str(sample_id),
            {
                "study_id": study_id,
                "deleted_raw_file_count": int(deleted_raw_file_count or 0),
                "cleared_file_sample_count": int(cleared_file_sample_count or 0),
            },
        )
        self.db.commit()
        return {
            "deleted_sample_id": sample_id,
            "deleted_raw_file_count": int(deleted_raw_file_count or 0),
            "cleared_file_sample_count": int(cleared_file_sample_count or 0),
        }

    def create_assay(self, user_id: int, study_id: int, payload: AssayCreate) -> Assay:
        study = self.get(user_id, study_id)
        self.perms.require_edit_study(user_id, study.id, study.status)

        assay_count = self.db.query(func.count(Assay.id)).filter(Assay.study_id == study_id).scalar() or 0
        assay_name = (payload.name or "").strip() or f"{study.public_or_internal_identifier}-assay-{int(assay_count) + 1}"
        platform = normalize_platform(payload.platform)
        metabolomics_assay_type = normalize_metabolomics_assay_type(payload.metabolomics_assay_type)

        assay = self._new_assay(
            study=study,
            name=assay_name,
            platform=platform,
            metabolomics_assay_type=metabolomics_assay_type,
            created_by=user_id,
            is_default=False,
        )
        self.audit.log(
            user_id,
            "assay.create",
            "assay",
            str(assay.id),
            {
                "study_id": study_id,
                "name": assay.name,
                "platform": assay.platform,
                "metabolomics_assay_type": assay.metabolomics_assay.assay_type if assay.metabolomics_assay else None,
                "is_default": assay.is_default,
            },
        )
        self.db.commit()
        self.db.refresh(assay)
        return assay

    def list_assays(self, user_id: int, study_id: int) -> list[Assay]:
        self.get(user_id, study_id)
        assays = self.db.query(Assay).filter(Assay.study_id == study_id).order_by(Assay.id.asc()).all()
        latest_job_at = dict(
            self.db.query(WorkflowJob.assay_id, func.max(WorkflowJob.created_at))
            .filter(WorkflowJob.study_id == study_id, WorkflowJob.deleted_at.is_(None))
            .group_by(WorkflowJob.assay_id)
            .all()
        )
        for assay in assays:
            # Read by AssayOut; not a mapped column.
            assay.last_workflow_job_at = latest_job_at.get(assay.id)
        return assays

    def get_structure(self, user_id: int, study_id: int) -> dict:
        study = self.get(user_id, study_id)

        assays = self.db.query(Assay).filter(Assay.study_id == study_id).order_by(Assay.id.asc()).all()
        samples = self.db.query(Sample).filter(Sample.study_id == study_id).order_by(Sample.id.asc()).all()
        datasets = (
            self.db.query(Dataset)
            .filter(Dataset.study_id == study_id)
            .order_by(Dataset.assay_id.asc(), Dataset.id.asc())
            .all()
        )
        dataset_ids = [dataset.id for dataset in datasets]
        assay_ids = [assay.id for assay in assays]

        assay_sample_rows = []
        if assay_ids:
            assay_sample_rows = (
                self.db.query(AssaySampleLink.assay_id, Sample.id, Sample.name, Sample.sample_type, Sample.organism, Sample.matrix)
                .join(Sample, Sample.id == AssaySampleLink.sample_id)
                .filter(AssaySampleLink.assay_id.in_(assay_ids))
                .order_by(AssaySampleLink.assay_id.asc(), Sample.id.asc())
                .all()
            )

        dataset_sample_rows = []
        if dataset_ids:
            dataset_sample_rows = (
                self.db.query(
                    DatasetSampleLink.dataset_id,
                    Sample.id,
                    Sample.name,
                    Sample.sample_type,
                    Sample.organism,
                    Sample.matrix,
                )
                .join(Sample, Sample.id == DatasetSampleLink.sample_id)
                .filter(DatasetSampleLink.dataset_id.in_(dataset_ids))
                .order_by(DatasetSampleLink.dataset_id.asc(), Sample.id.asc())
                .all()
            )

        dataset_file_counts = {}
        if dataset_ids:
            dataset_file_counts = {
                int(dataset_id): int(file_count)
                for dataset_id, file_count in (
                    self.db.query(DatasetFile.dataset_id, func.count(DatasetFile.id))
                    .filter(DatasetFile.dataset_id.in_(dataset_ids))
                    .group_by(DatasetFile.dataset_id)
                    .all()
                )
            }

        assay_samples_by_assay: dict[int, list[dict]] = {}
        linked_sample_ids: set[int] = set()
        for assay_id, sample_id, sample_name, sample_type, organism, matrix in assay_sample_rows:
            linked_sample_ids.add(int(sample_id))
            assay_samples_by_assay.setdefault(int(assay_id), []).append(
                {
                    "sample_id": int(sample_id),
                    "sample_name": sample_name,
                    "sample_type": sample_type,
                    "organism": organism,
                    "matrix": matrix,
                }
            )

        dataset_samples_by_dataset: dict[int, list[dict]] = {}
        dataset_sample_ids_by_dataset: dict[int, set[int]] = {}
        for dataset_id, sample_id, sample_name, sample_type, organism, matrix in dataset_sample_rows:
            dataset_samples_by_dataset.setdefault(int(dataset_id), []).append(
                {
                    "sample_id": int(sample_id),
                    "sample_name": sample_name,
                    "sample_type": sample_type,
                    "organism": organism,
                    "matrix": matrix,
                }
            )
            dataset_sample_ids_by_dataset.setdefault(int(dataset_id), set()).add(int(sample_id))

        datasets_by_assay: dict[int, list[Dataset]] = {}
        for dataset in datasets:
            datasets_by_assay.setdefault(int(dataset.assay_id), []).append(dataset)

        root_id = f"study:{study.id}"
        nodes: list[dict] = []

        def add_node(
            *,
            node_id: str,
            parent_id: str | None,
            label: str,
            node_type: str,
            value: int,
            assay_id: int | None = None,
            dataset_id: int | None = None,
            sample_id: int | None = None,
            dataset_kind: str | None = None,
            details: dict | None = None,
        ) -> None:
            nodes.append(
                {
                    "id": node_id,
                    "parent_id": parent_id,
                    "label": label,
                    "node_type": node_type,
                    "value": value,
                    "study_id": study.id,
                    "assay_id": assay_id,
                    "dataset_id": dataset_id,
                    "sample_id": sample_id,
                    "dataset_kind": dataset_kind,
                    "details": details or {},
                }
            )

        add_node(
            node_id=root_id,
            parent_id=None,
            label=study.title or study.public_or_internal_identifier,
            node_type="study",
            value=0,
            details={
                "identifier": study.public_or_internal_identifier,
                "status": study.status,
                "sample_count": len(samples),
                "assay_count": len(assays),
            },
        )

        for assay in assays:
            assay_node_id = f"assay:{assay.id}"
            add_node(
                node_id=assay_node_id,
                parent_id=root_id,
                label=assay.name,
                node_type="assay",
                value=0,
                assay_id=assay.id,
                details={
                    "status": assay.status,
                    "is_default": assay.is_default,
                    "platform": assay.platform,
                    "metabolomics_assay_type": assay.metabolomics_assay.assay_type if assay.metabolomics_assay else None,
                },
            )

            assay_dataset_sample_ids: set[int] = set()
            for dataset in datasets_by_assay.get(int(assay.id), []):
                dataset_node_id = f"dataset:{dataset.id}"
                dataset_samples = dataset_samples_by_dataset.get(int(dataset.id), [])
                dataset_sample_ids = dataset_sample_ids_by_dataset.get(int(dataset.id), set())
                assay_dataset_sample_ids.update(dataset_sample_ids)

                add_node(
                    node_id=dataset_node_id,
                    parent_id=assay_node_id,
                    label=dataset.name,
                    node_type="dataset",
                    value=0,
                    assay_id=assay.id,
                    dataset_id=dataset.id,
                    dataset_kind=dataset.dataset_kind,
                    details={
                        "dataset_kind": dataset.dataset_kind,
                        "dataset_type": dataset.dataset_type,
                        "status": dataset.status,
                        "file_count": dataset_file_counts.get(int(dataset.id), 0),
                    },
                )

                for sample in dataset_samples:
                    add_node(
                        node_id=f"dataset-sample:{dataset.id}:{sample['sample_id']}",
                        parent_id=dataset_node_id,
                        label=sample["sample_name"],
                        node_type="sample",
                        value=1,
                        assay_id=assay.id,
                        dataset_id=dataset.id,
                        sample_id=sample["sample_id"],
                        dataset_kind=dataset.dataset_kind,
                        details={
                            "sample_name": sample["sample_name"],
                            "sample_id": sample["sample_id"],
                            "sample_type": sample["sample_type"],
                            "organism": sample["organism"],
                            "matrix": sample["matrix"],
                        },
                    )

            assay_samples = assay_samples_by_assay.get(int(assay.id), [])
            assay_orphan_samples = [
                sample
                for sample in assay_samples
                if int(sample["sample_id"]) not in assay_dataset_sample_ids
            ]

            if assay_orphan_samples:
                bucket_id = f"bucket:assay:{assay.id}:no-dataset"
                add_node(
                    node_id=bucket_id,
                    parent_id=assay_node_id,
                    label="No dataset yet",
                    node_type="bucket",
                    value=0,
                    assay_id=assay.id,
                    details={"sample_count": len(assay_orphan_samples)},
                )
                for sample in assay_orphan_samples:
                    add_node(
                        node_id=f"assay-sample:{assay.id}:{sample['sample_id']}",
                        parent_id=bucket_id,
                        label=sample["sample_name"],
                        node_type="sample",
                        value=1,
                        assay_id=assay.id,
                        sample_id=sample["sample_id"],
                        details={
                            "sample_name": sample["sample_name"],
                            "sample_id": sample["sample_id"],
                            "sample_type": sample["sample_type"],
                            "organism": sample["organism"],
                            "matrix": sample["matrix"],
                        },
                    )
            elif not datasets_by_assay.get(int(assay.id)):
                bucket_id = f"bucket:assay:{assay.id}:empty"
                add_node(
                    node_id=bucket_id,
                    parent_id=assay_node_id,
                    label="No linked samples yet",
                    node_type="bucket",
                    value=1,
                    assay_id=assay.id,
                    details={"sample_count": 0},
                )

        unlinked_samples = [sample for sample in samples if int(sample.id) not in linked_sample_ids]
        if unlinked_samples:
            bucket_id = f"bucket:study:{study.id}:unlinked"
            add_node(
                node_id=bucket_id,
                parent_id=root_id,
                label="Unlinked samples",
                node_type="bucket",
                value=0,
                details={"sample_count": len(unlinked_samples)},
            )
            for sample in unlinked_samples:
                add_node(
                    node_id=f"study-sample:{sample.id}",
                    parent_id=bucket_id,
                    label=sample.name,
                    node_type="sample",
                    value=1,
                    sample_id=sample.id,
                    details={
                        "sample_name": sample.name,
                        "sample_id": sample.id,
                        "sample_type": sample.sample_type,
                        "organism": sample.organism,
                        "matrix": sample.matrix,
                    },
                )

        aggregated_values: dict[str, int] = {}
        for node in reversed(nodes):
            node_id = node["id"]
            total_value = aggregated_values.get(node_id, int(node["value"] or 0))
            node["value"] = total_value
            parent_id = node.get("parent_id")
            if parent_id is not None:
                aggregated_values[parent_id] = aggregated_values.get(parent_id, 0) + total_value

        return {"study_id": study.id, "nodes": nodes}

    def get_summary(self, user_id: int, study_id: int) -> dict:
        study = self.get(user_id, study_id)

        sample_count = self.db.query(func.count(Sample.id)).filter(Sample.study_id == study_id).scalar() or 0

        dataset_status_rows = (
            self.db.query(DatasetFile.status, func.count(DatasetFile.id))
            .join(Dataset, Dataset.id == DatasetFile.dataset_id)
            .filter(Dataset.study_id == study_id)
            .group_by(DatasetFile.status)
            .all()
        )
        dataset_status_counts = {status: int(count) for status, count in dataset_status_rows}
        dataset_file_count = sum(dataset_status_counts.values())

        latest_metadata = (
            self.db.query(MetadataSubmission)
            .filter(MetadataSubmission.study_id == study_id)
            .order_by(MetadataSubmission.id.desc())
            .first()
        )

        workflow_status_rows = (
            self.db.query(WorkflowJob.status, func.count(WorkflowJob.id))
            .filter(WorkflowJob.study_id == study_id, WorkflowJob.deleted_at.is_(None))
            .group_by(WorkflowJob.status)
            .all()
        )
        workflow_status_counts = {status: int(count) for status, count in workflow_status_rows}
        workflow_job_count = sum(workflow_status_counts.values())

        return {
            "study": self.serialize_study(study),
            "sample_count": int(sample_count),
            "dataset_file_count": int(dataset_file_count),
            "dataset_status_counts": dataset_status_counts,
            "latest_metadata_status": latest_metadata.validation_status if latest_metadata else None,
            "latest_metadata_submitted_at": latest_metadata.submitted_at if latest_metadata else None,
            "workflow_job_count": int(workflow_job_count),
            "workflow_status_counts": workflow_status_counts,
        }

    def list_activity(self, user_id: int, study_id: int, limit: int = 100) -> list[dict]:
        self.get(user_id, study_id)
        safe_limit = max(1, min(int(limit), 500))
        rows = (
            self.db.query(AuditEvent, User.email)
            .outerjoin(User, User.id == AuditEvent.actor_user_id)
            .filter(
                or_(
                    (AuditEvent.target_type == "study") & (AuditEvent.target_id == str(study_id)),
                    AuditEvent.payload_json["study_id"].as_string() == str(study_id),
                )
            )
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .limit(safe_limit)
            .all()
        )
        return [
            {
                "id": event.id,
                "created_at": event.created_at,
                "actor_user_id": event.actor_user_id,
                "actor_email": actor_email,
                "event_type": event.event_type,
                "target_type": event.target_type,
                "target_id": event.target_id,
                "payload_json": event.payload_json,
            }
            for event, actor_email in rows
        ]
