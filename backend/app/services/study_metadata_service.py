"""Read and save a Study's submission details, keeping sample and job data intact."""
from collections import Counter
from copy import deepcopy

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.errors import bad_request
from app.core.permissions import require_allowed
from app.core.sample_context import effective_sample_annotations
from app.models.models import Assay, AssayProtocolLink, AssaySampleLink, Protocol, Sample, Study
from app.schemas.study_metadata import AssaySubmissionMetadata, StudySubmissionMetadata
from app.services.audit_service import AuditService
from app.services.study_service import StudyService


ASSAY_PROTOCOLS = ("Extraction", "Chromatography", "Mass spectrometry")
STORED_FIELDS = {"revision", "submission_date", "public_release_date",
                 "contacts", "design_descriptors", "publications", "factors"}
# Saved by the withdrawn example-prefill prototype; dropped on the next save.
RETIRED_FIELDS = {"contains_examples"}


def column_value(annotations: dict, column: str) -> str:
    """One recorded sample-sheet value: group_label or a factors./characteristics. key."""
    if column == "group_label":
        value = annotations.get("group_label")
    else:
        section, name = column.split(".", 1)
        value = (annotations.get(section) or {}).get(name)
    return "" if value is None or isinstance(value, (dict, list)) else str(value).strip()


def protocol_kind(value: str) -> str:
    """Accept the readable and underscore spellings used by imported protocols."""
    return value.strip().lower().replace("_", " ")


class StudyMetadataService:
    def __init__(self, db: Session):
        self.db = db
        self.studies = StudyService(db)

    def can_edit(self, user_id: int, study: Study) -> bool:
        return self.studies.perms.can_manage_submission(user_id, study.id, study.status)

    def collection_record(self, study_id: int) -> Protocol | None:
        return next((p for p in self.db.query(Protocol).filter_by(study_id=study_id).order_by(Protocol.id)
                     if protocol_kind(p.protocol_type) == "sample collection"), None)

    def assay_protocol_records(self, study_id: int, assay_id: int) -> dict:
        rows = self.db.query(Protocol, AssayProtocolLink).join(
            AssayProtocolLink, AssayProtocolLink.protocol_id == Protocol.id).filter(
            Protocol.study_id == study_id, AssayProtocolLink.assay_id == assay_id).order_by(
            AssayProtocolLink.sort_order, Protocol.id)
        records = {}
        for protocol, link in rows:
            for name in ASSAY_PROTOCOLS:
                if protocol_kind(protocol.protocol_type) == protocol_kind(name):
                    records.setdefault(name, (protocol, link))
        return records

    def snapshot_metadata(self, study: Study) -> StudySubmissionMetadata:
        """Read current owned records; callers can freeze this into a package."""
        stored = {key: value for key, value in (study.metadata_json or {}).items() if key in STORED_FIELDS}
        stored.setdefault("revision", 0)
        collection = self.collection_record(study.id)
        assays = []
        for assay in self.db.query(Assay).filter_by(study_id=study.id).order_by(Assay.id):
            records = self.assay_protocol_records(study.id, assay.id)
            assays.append(AssaySubmissionMetadata(assay_id=assay.id, assay_name=assay.name,
                protocols={name: (records[name][0].summary or "") if name in records else ""
                           for name in ASSAY_PROTOCOLS}))
        return StudySubmissionMetadata(**stored, title=study.title, description=study.description or "",
            collection_protocol=collection.summary or "" if collection else "", assays=assays)

    def sample_context(self, study_id: int) -> tuple[list[dict], list[dict]]:
        samples = self.db.query(Sample).filter_by(study_id=study_id).order_by(Sample.id).all()
        links = self.db.query(AssaySampleLink).join(Assay).join(Sample).filter(
            Assay.study_id == study_id, Sample.study_id == study_id).all()
        columns = set()
        for annotations in [s.annotations_json or {} for s in samples] + [l.annotations_json or {} for l in links]:
            if str(annotations.get("group_label") or "").strip():
                columns.add("group_label")
            for section in ("factors", "characteristics"):
                values = annotations.get(section) or {}
                if isinstance(values, dict):
                    columns.update(f"{section}.{key}" for key, value in values.items()
                                   if isinstance(value, (str, int, float, bool)) and str(value).strip())
        ordered = sorted(columns)
        # Effective values per assay, as packaging reads them: assay-link values override sample defaults.
        sample_by_id = {s.id: s for s in samples}
        by_sample = {}
        for link in links:
            effective = effective_sample_annotations(sample_by_id[link.sample_id].annotations_json, link.annotations_json)
            by_sample.setdefault(link.sample_id, {})[link.assay_id] = {c: column_value(effective, c) for c in ordered}
        summaries = []
        for column in ordered:
            # A populated value and an empty override conflict in the package too.
            per_sample = {sample_id: {values[column] for values in assays.values()}
                          for sample_id, assays in by_sample.items()}
            counts = Counter(value for values in per_sample.values() for value in values if value)
            summaries.append(dict(value=column, label="Sample group" if column == "group_label" else column,
                                  values=[dict(value=v, count=n) for v, n in sorted(counts.items())],
                                  conflicts=sorted(sample_by_id[sid].name for sid, values in per_sample.items()
                                                   if len(values) > 1)))
        return ([dict(sample_id=s.id, name=s.name, organism=s.organism or "", organism_part=s.matrix or "",
                      sample_type=s.sample_type or "", annotations=deepcopy(s.annotations_json or {}),
                      assay_values=by_sample.get(s.id, {})) for s in samples], summaries)

    def protocol_sharing(self, study_id: int) -> dict[int, dict[str, list[str]]]:
        """For each assay and protocol, the other assays linked to the same record.

        Saving a changed shared protocol gives this assay its own copy, so the
        editor can say so before the operator saves.
        """
        users = {}
        for link, name in self.db.query(AssayProtocolLink, Assay.name).join(
                Assay, Assay.id == AssayProtocolLink.assay_id).filter(Assay.study_id == study_id):
            users.setdefault(link.protocol_id, []).append((link.assay_id, name))
        sharing = {}
        for assay in self.db.query(Assay).filter_by(study_id=study_id):
            for name, (protocol, _) in self.assay_protocol_records(study_id, assay.id).items():
                others = sorted(other for assay_id, other in users.get(protocol.id, []) if assay_id != assay.id)
                if others:
                    sharing.setdefault(assay.id, {})[name] = others
        return sharing

    def get(self, user_id: int, study_id: int) -> dict:
        study = self.studies.get(user_id, study_id)
        metadata = self.snapshot_metadata(study)
        samples, columns = self.sample_context(study_id)
        return dict(metadata=metadata, can_edit=self.can_edit(user_id, study), samples=samples, factor_columns=columns,
                    protocol_sharing=self.protocol_sharing(study_id))

    def save_assay_protocols(self, study: Study, assay: AssaySubmissionMetadata) -> None:
        records = self.assay_protocol_records(study.id, assay.assay_id)
        for order, name in enumerate(ASSAY_PROTOCOLS, 1):
            summary = assay.protocols.get(name, "")
            existing = records.get(name)
            if existing:
                protocol, link = existing
                if (protocol.summary or "") == summary:
                    continue
                shared = self.db.query(AssayProtocolLink.id).filter(
                    AssayProtocolLink.protocol_id == protocol.id,
                    AssayProtocolLink.assay_id != assay.assay_id).first()
                if not shared:
                    protocol.summary = summary
                    continue
                # A shared protocol belongs to other assays too. Preserve their version.
                replacement = Protocol(study_id=study.id, name=protocol.name, protocol_type=protocol.protocol_type,
                                       summary=summary, parameters_json=deepcopy(protocol.parameters_json))
                self.db.add(replacement)
                self.db.flush()
                link.protocol_id = replacement.id
            elif summary:
                protocol = Protocol(study_id=study.id, name=f"Assay {assay.assay_id}: {name}",
                                    protocol_type=name, summary=summary)
                self.db.add(protocol)
                self.db.flush()
                self.db.add(AssayProtocolLink(assay_id=assay.assay_id, protocol_id=protocol.id, sort_order=order))

    def save(self, user_id: int, study_id: int, payload: StudySubmissionMetadata) -> dict:
        self.studies.get(user_id, study_id)
        # Row locking serializes edits on PostgreSQL; revision checking rejects stale forms.
        study = self.db.query(Study).filter_by(id=study_id).populate_existing().with_for_update().one()
        require_allowed(self.can_edit(user_id, study), "Study editing or operator permission is required to save submission metadata")
        before = self.snapshot_metadata(study)
        if "revision" not in payload.model_fields_set:
            raise bad_request("The current metadata revision is required")
        if payload.revision != before.revision:
            raise HTTPException(status_code=409, detail="Study metadata changed. Reload the page before saving again.")
        if not payload.title.strip():
            raise bad_request("Study title is required")
        own_assays = {a.assay_id for a in before.assays}
        submitted = [a.assay_id for a in payload.assays]
        if len(set(submitted)) != len(submitted) or set(submitted) != own_assays:
            raise bad_request("Metadata must contain each assay belonging to this Study exactly once")
        _, columns = self.sample_context(study_id)
        available = {c["value"] for c in columns}
        if any(f.source_column not in available for f in payload.factors):
            raise bad_request("Choose an existing sample-sheet group, factor or characteristic column")
        names = [f.name.casefold() for f in payload.factors]
        if len(set(names)) != len(names):
            raise bad_request("Study factor names must be unique")

        updated = payload.model_dump(mode="json")
        previous = before.model_dump(mode="json")
        changed_fields = [key for key, value in updated.items()
                          if key != "revision" and value != previous[key]]
        study.title, study.description = payload.title, payload.description
        kept = {key: value for key, value in (study.metadata_json or {}).items() if key not in RETIRED_FIELDS}
        study.metadata_json = {**kept, **{key: updated[key] for key in STORED_FIELDS}, "revision": before.revision + 1}
        collection = self.collection_record(study.id)
        if collection:
            collection.summary = payload.collection_protocol
        elif payload.collection_protocol:
            self.db.add(Protocol(study_id=study.id, name="Sample collection", protocol_type="Sample collection",
                                 summary=payload.collection_protocol))
        for assay in payload.assays:
            self.save_assay_protocols(study, assay)
        AuditService(self.db).log(user_id, "study.submission_metadata_updated", "study", str(study_id),
                                 {"revision": before.revision + 1, "changed_fields": changed_fields})
        self.db.commit()
        return self.get(user_id, study_id)
