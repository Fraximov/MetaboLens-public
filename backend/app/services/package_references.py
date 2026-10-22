"""Which package revisions link which registered spectral files.

A revision built with linked spectra depends on its sources staying where they
are, so deleting or re-pointing those files must wait until the revision is
deleted. Revisions that copied their spectra (before 2026-10-01) do not depend on
the originals and are ignored. Queued and building revisions will link.
"""
from sqlalchemy.orm import Session

from app.core.states import PACKAGE_PENDING_STATES
from app.models.models import StudyPackage



def linking_revisions(db: Session, study_id: int, file_ids) -> dict[int, list[int]]:
    """For each given file ID, the revisions that link it."""
    wanted, found = set(file_ids), {}
    if not wanted:
        return {}
    for package in db.query(StudyPackage).filter_by(study_id=study_id):
        report = package.report_json or {}
        if "spectral_files" not in report and package.status not in PACKAGE_PENDING_STATES:
            continue
        for assay in (package.snapshot_json or {}).get("assays", []):
            for acquisition in assay.get("acquisitions", []):
                file_id = (acquisition.get("file") or {}).get("file_id")
                if file_id in wanted:
                    found.setdefault(file_id, set()).add(package.revision)
    return {file_id: sorted(revisions) for file_id, revisions in found.items()}
