import csv
import json
import math
from pathlib import Path
from urllib.parse import unquote, urlparse

from sqlalchemy.orm import Session

from app.core.errors import bad_request, not_found
from app.models.models import DatasetFile, WorkflowJob, WorkflowRunArtifact
from app.services.study_service import StudyService


def _positive_float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _correct_rt(rt_min: float, anchors: list[dict]) -> float:
    points = [(0.0, 0.0), *sorted(
        (float(anchor["observed_rt_min"]), float(anchor["shift_min"]))
        for anchor in anchors
    )]
    rt = float(rt_min)
    if rt >= points[-1][0]:
        return rt - points[-1][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if rt <= x1:
            return rt - (y0 + (rt - x0) / (x1 - x0) * (y1 - y0))
    return rt - points[-1][1]


def select_correction_plan(plan: dict, selected_target_ids: list[str] | None) -> dict:
    known_ids = list(dict.fromkeys(
        str(feature.get("target_id") or "")
        for feature in plan.get("features", [])
        if str(feature.get("target_id") or "")
    ))
    selected_ids = known_ids if selected_target_ids is None else list(dict.fromkeys(
        str(target_id) for target_id in selected_target_ids if str(target_id)
    ))
    unknown = [target_id for target_id in selected_ids if target_id not in known_ids]
    if unknown:
        raise bad_request(f"Unknown RT correction target ID(s): {', '.join(unknown)}")
    selected = set(selected_ids)

    samples = []
    model_by_file = {}
    for source in plan.get("samples", []):
        anchors = [
            dict(anchor) for anchor in source.get("anchors", [])
            if str(anchor.get("target_id") or "") in selected
        ]
        warnings = [
            warning for warning in source.get("warnings", [])
            if "Only one usable internal standard" not in warning
        ]
        if len(anchors) == 1:
            warnings.append("Only one usable internal standard supports this sample correction curve.")
        sample = {
            **source,
            "eligible": bool(anchors),
            "anchor_count": len(anchors),
            "anchors": anchors,
            "warnings": warnings,
        }
        samples.append(sample)
        model_by_file[str(sample.get("input_file") or "")] = sample

    features = []
    for source in plan.get("features", []):
        feature = dict(source)
        target_id = str(feature.get("target_id") or "")
        observed = _positive_float(feature.get("observed_rt_min"))
        model = model_by_file.get(str(feature.get("input_file") or ""))
        corrected = (
            _correct_rt(observed, model["anchors"])
            if observed is not None and model and model["eligible"] else None
        )
        feature.update({
            "corrected_rt_min": corrected,
            "shift_min": observed - corrected if observed is not None and corrected is not None else None,
            "used_for_rt_model": target_id in selected,
        })
        features.append(feature)

    blocking_reasons = []
    if not selected_ids:
        blocking_reasons.append("No internal standards are selected for RT correction.")
    blocking_reasons.extend(
        f"Sample {sample['input_file']} has no usable selected RT correction anchor."
        for sample in samples
        if not sample["eligible"]
    )
    return {
        **plan,
        "can_apply": bool(samples) and not blocking_reasons,
        "blocking_reasons": blocking_reasons,
        "selected_target_ids": selected_ids,
        "samples": samples,
        "features": features,
    }


class WorkflowIsQcService:
    """Read registered IS-QC review artifacts for one workflow job."""

    def __init__(self, db: Session):
        self.db = db
        self.studies = StudyService(db)

    def get_summary(
        self,
        user_id: int,
        assay_id: int,
        job_id: int,
        rt_correction_target_ids: list[str] | None = None,
    ) -> dict:
        summary_path = self._artifact_path(user_id, assay_id, job_id, "is_qc_summary")
        assert summary_path is not None
        summary = self._read_json(summary_path)
        plan_path = self._artifact_path(
            user_id,
            assay_id,
            job_id,
            "is_qc_rt_correction_plan",
            required=False,
        )
        plan = self._read_json(plan_path) if plan_path else None
        return {
            **summary,
            "rt_correction": select_correction_plan(plan, rt_correction_target_ids) if plan else None,
        }

    def get_correction_plan(
        self,
        user_id: int,
        assay_id: int,
        job_id: int,
        *,
        required: bool = True,
    ) -> dict | None:
        path = self._artifact_path(
            user_id,
            assay_id,
            job_id,
            "is_qc_rt_correction_plan",
            required=required,
        )
        return self._read_json(path) if path else None

    def list_targets(
        self,
        user_id: int,
        assay_id: int,
        job_id: int,
        rt_correction_target_ids: list[str] | None = None,
    ) -> list[dict]:
        path = self._artifact_path(user_id, assay_id, job_id, "is_qc_table")
        assert path is not None
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
        plan_path = self._artifact_path(
            user_id,
            assay_id,
            job_id,
            "is_qc_rt_correction_plan",
            required=False,
        )
        plan = self._read_json(plan_path) if plan_path else {}
        if plan:
            plan = select_correction_plan(plan, rt_correction_target_ids)
        features = {
            (str(item.get("target_id") or ""), str(item.get("input_file") or "")): item
            for item in plan.get("features", [])
        }
        for row in rows:
            feature = features.get(
                (str(row.get("target_id") or ""), str(row.get("input_file") or "")),
                {},
            )
            row["corrected_rt_min"] = feature.get("corrected_rt_min")
            row["rt_shift_min"] = feature.get("shift_min")
            row["used_for_rt_model"] = feature.get("used_for_rt_model")
        return rows

    def get_traces(
        self,
        user_id: int,
        assay_id: int,
        job_id: int,
        target_name: str | None = None,
        rt_correction_target_ids: list[str] | None = None,
    ) -> dict:
        path = self._artifact_path(
            user_id,
            assay_id,
            job_id,
            "is_qc_rt_correction_preview",
            required=False,
        )
        if path is None:
            path = self._artifact_path(user_id, assay_id, job_id, "is_qc_traces")
        assert path is not None
        payload = self._read_json(path)
        source_plan = self.get_correction_plan(user_id, assay_id, job_id, required=False)
        if source_plan is None:
            if target_name:
                payload = {
                    **payload,
                    "traces": [
                        trace for trace in payload.get("traces", [])
                        if str(trace.get("target_name") or "") == target_name
                    ],
                    "target_name": target_name,
                }
            return payload
        plan = select_correction_plan(source_plan, rt_correction_target_ids)
        models = {
            str(sample.get("input_file") or ""): sample
            for sample in plan.get("samples", [])
        }
        features = {
            (str(feature.get("target_id") or ""), str(feature.get("input_file") or "")): feature
            for feature in plan.get("features", [])
        }
        projected = []
        for source in payload.get("traces", []):
            trace = dict(source)
            key = (str(trace.get("target_id") or ""), str(trace.get("input_file") or ""))
            feature = features.get(key, {})
            model = models.get(str(trace.get("input_file") or ""))
            trace.update({
                "corrected_observed_rt_min": feature.get("corrected_rt_min"),
                "rt_shift_min": feature.get("shift_min"),
                "used_for_rt_model": feature.get("used_for_rt_model"),
                "points": [
                    {
                        **point,
                        "corrected_rt_min": _correct_rt(float(point["rt_min"]), model["anchors"])
                        if model and model.get("eligible") else None,
                    }
                    for point in trace.get("points", [])
                ],
            })
            projected.append(trace)
        payload = {**payload, "traces": projected, "rt_correction": plan}
        if target_name:
            traces = [
                trace for trace in payload.get("traces", [])
                if str(trace.get("target_name") or "") == target_name
            ]
            payload = {**payload, "traces": traces, "target_name": target_name}
        return payload

    def _artifact_path(
        self,
        user_id: int,
        assay_id: int,
        job_id: int,
        artifact_role: str,
        *,
        required: bool = True,
    ) -> Path | None:
        row = (
            self.db.query(WorkflowRunArtifact, WorkflowJob, DatasetFile)
            .join(WorkflowJob, WorkflowJob.id == WorkflowRunArtifact.workflow_job_id)
            .join(DatasetFile, DatasetFile.id == WorkflowRunArtifact.dataset_file_id)
            .filter(
                WorkflowJob.id == job_id,
                WorkflowJob.assay_id == assay_id,
                WorkflowJob.deleted_at.is_(None),
                WorkflowRunArtifact.artifact_role == artifact_role,
            )
            .first()
        )
        if row is None:
            if not required:
                return None
            raise not_found("IS-QC artifact not found for this workflow job")

        _, job, dataset_file = row
        self.studies.get(user_id, job.study_id)
        parsed = urlparse(dataset_file.storage_uri or "")
        if parsed.scheme != "file":
            raise bad_request("Only local IS-QC artifacts can be read")
        path = Path(unquote(parsed.path)).expanduser().resolve()
        if not path.is_file():
            raise not_found("IS-QC artifact is no longer available on disk")
        return path

    @staticmethod
    def _read_json(path: Path) -> dict:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise bad_request(f"Could not parse IS-QC JSON artifact: {exc}") from exc
