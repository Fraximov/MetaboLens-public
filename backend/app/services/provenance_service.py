from sqlalchemy.orm import Session

from app.models.models import ProvenanceEvent


class ProvenanceService:
    def __init__(self, db: Session):
        self.db = db

    def log(
        self,
        study_id: int,
        event_type: str,
        *,
        assay_id: int | None = None,
        workflow_job_id: int | None = None,
        source_ref: dict | None = None,
        output_ref: dict | None = None,
        software_versions: dict | None = None,
    ) -> ProvenanceEvent:
        event = ProvenanceEvent(
            study_id=study_id,
            assay_id=assay_id,
            workflow_job_id=workflow_job_id,
            event_type=event_type,
            source_ref_json=source_ref or {},
            output_ref_json=output_ref or {},
            software_versions_json=software_versions or {},
        )
        self.db.add(event)
        return event
