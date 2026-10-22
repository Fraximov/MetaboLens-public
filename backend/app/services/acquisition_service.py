"""Own instrument acquisition metadata without duplicating study samples."""
from copy import deepcopy

from app.core.errors import bad_request, not_found
from app.core.permissions import require_allowed
from app.core.sample_context import normalize_acquisition_annotations, split_acquisition_annotations
from app.models.models import Acquisition, Assay, DatasetFile, Sample
from app.services.audit_service import AuditService


class AcquisitionService:
    def __init__(self, db):
        self.db = db

    def create(self, assay_id: int, sample_id: int, annotations: dict) -> Acquisition:
        assay = self.db.get(Assay, assay_id)
        sample = self.db.get(Sample, sample_id)
        if assay is None or sample is None or sample.study_id != assay.study_id:
            raise bad_request("Acquisition sample must belong to the assay's study")
        _, legacy = split_acquisition_annotations(sample.annotations_json)
        if not annotations and legacy and self.db.query(Acquisition.id).filter_by(assay_id=assay_id, sample_id=sample_id).first() is None:
            # Older two-step clients supplied sequence metadata before registering their first file.
            annotations = legacy
        try:
            annotations = normalize_acquisition_annotations(annotations)
        except (ValueError, TypeError, OverflowError) as exc:
            raise bad_request(str(exc)) from exc
        acquisition = Acquisition(assay_id=assay_id, sample_id=sample_id, annotations_json=annotations)
        self.db.add(acquisition)
        self.db.flush()
        return acquisition

    def list_for_assay(self, user_id: int, assay_id: int) -> list[dict]:
        from app.services.study_service import StudyService
        StudyService(self.db).get_assay(user_id, assay_id)
        rows = self.db.query(Acquisition, Sample.name).join(Sample, Sample.id == Acquisition.sample_id).filter(
            Acquisition.assay_id == assay_id).order_by(Acquisition.id).all()
        ids = [a.id for a, _ in rows]
        files = self.db.query(DatasetFile).filter(DatasetFile.acquisition_id.in_(ids)).order_by(DatasetFile.id).all() if ids else []
        files_by_acquisition = {}
        for file in files:
            files_by_acquisition.setdefault(file.acquisition_id, []).append(file)
        return [dict(id=a.id, assay_id=a.assay_id, sample_id=a.sample_id, sample_name=name,
                     file_ids=[f.id for f in files_by_acquisition.get(a.id, [])],
                     filenames=[f.original_filename for f in files_by_acquisition.get(a.id, [])],
                     annotations_json=a.annotations_json, metadata_review_required=a.metadata_review_required,
                     created_at=a.created_at) for a, name in rows]

    def update(self, user_id: int, assay_id: int, acquisition_id: int, payload) -> dict:
        from app.services.study_service import StudyService
        studies = StudyService(self.db)
        assay = studies.get_assay(user_id, assay_id)
        study = studies.get(user_id, assay.study_id)
        require_allowed(studies.perms.can_edit_sample_details(user_id, study.id, study.status),
                        "Submitter, operator or admin role is required; archived Studies are read-only.")
        acquisition = self.db.query(Acquisition).filter_by(id=acquisition_id, assay_id=assay_id).first()
        if acquisition is None:
            raise not_found("Acquisition not found")
        before = deepcopy(acquisition.annotations_json)
        acquisition.annotations_json = {**(before or {}), **payload.annotations_json}
        annotations = acquisition.annotations_json
        if str(annotations.get("batch_id") or "").strip() and annotations.get("injection_order") not in (None, ""):
            acquisition.metadata_review_required = False
        AuditService(self.db).log(user_id, "acquisition.update", "acquisition", str(acquisition.id),
            {"study_id": study.id, "assay_id": assay_id, "before": before, "after": acquisition.annotations_json})
        self.db.commit()
        return next(a for a in self.list_for_assay(user_id, assay_id) if a["id"] == acquisition_id)
