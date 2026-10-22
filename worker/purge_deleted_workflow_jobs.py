from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil

from sqlalchemy.orm import Session

from app.core.states import FINISHED_JOB_STATES
from app.models.models import WorkflowJob
from app.services.workflow_job_deletion_service import WorkflowJobDeletionService


def validated_run_root(job: WorkflowJob, workflow_runs_root: str) -> Path | None:
    if not job.run_root:
        return None
    root = Path(workflow_runs_root).expanduser().absolute()
    target = Path(job.run_root).expanduser().absolute()
    expected = root / f"job_{job.id}"
    if target != expected or target.is_symlink():
        raise ValueError(f"Refusing unexpected workflow run path for job {job.id}: {target}")
    return target


def purge_eligible_jobs(
    db: Session,
    workflow_runs_root: str,
    cutoff: datetime,
    dry_run: bool = False,
) -> dict:
    jobs = (
        db.query(WorkflowJob)
        .filter(
            WorkflowJob.deleted_at.is_not(None),
            WorkflowJob.deleted_at <= cutoff,
            WorkflowJob.purged_at.is_(None),
            WorkflowJob.status.in_(FINISHED_JOB_STATES),
        )
        .order_by(WorkflowJob.id.asc())
        .all()
    )
    purged = 0
    failures: list[dict] = []
    for job in jobs:
        job_id = job.id
        try:
            target = validated_run_root(job, workflow_runs_root)
            if dry_run:
                print(f"would purge workflow job {job_id}: {target or 'no run directory'}")
                continue
            if target is not None and target.exists():
                if not target.is_dir():
                    raise ValueError(f"Workflow run path is not a directory: {target}")
                shutil.rmtree(target)
            WorkflowJobDeletionService(db).purge_generated_records(job_id)
            purged += 1
            print(f"purged workflow job {job_id}")
        except Exception as exc:  # noqa: BLE001 - report every eligible job, then fail the command
            db.rollback()
            failures.append({"job_id": job_id, "error": str(exc)})
            print(f"failed to purge workflow job {job_id}: {exc}")
    return {"eligible": len(jobs), "purged": purged, "failures": failures, "dry_run": dry_run}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Purge files for soft-deleted workflow jobs")
    parser.add_argument("--older-than-days", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.older_than_days < 0:
        parser.error("--older-than-days must be zero or greater")

    from config import get_settings
    from db import SessionLocal

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.older_than_days)
    with SessionLocal() as db:
        result = purge_eligible_jobs(
            db,
            get_settings().workflow_runs_root,
            cutoff,
            dry_run=args.dry_run,
        )
    print(
        f"eligible={result['eligible']} purged={result['purged']} "
        f"failures={len(result['failures'])} dry_run={result['dry_run']}"
    )
    return 1 if result["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
