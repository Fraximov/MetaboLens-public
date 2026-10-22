"""`NEXTFLOW_MODE=mock`: finish at once with placeholder outputs, without Nextflow.

Used by the mock-worker smoke test in docs/DEVELOPMENT.md to exercise the queue,
status and artifact registration end to end.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from services.materialized_run import MaterializedRun


@dataclass
class MockCompletedRun:
    external_run_id: str
    exit_code: int


def write_mock_outputs(run: MaterializedRun) -> None:
    if run.nextflow_params.get("workflow_stage") == "hpc_smoke":
        results_dir = Path(run.results_path)
        logs_dir = Path(run.logs_path)
        results_dir.mkdir(parents=True, exist_ok=True)
        logs_dir.mkdir(parents=True, exist_ok=True)
        (results_dir / "sample_a.upper.txt").write_text("ALPHA\nBETA\n", encoding="utf-8")
        (results_dir / "sample_b.upper.txt").write_text("GAMMA\nDELTA\n", encoding="utf-8")
        (logs_dir / "success_report.txt").write_text("true\n", encoding="utf-8")
        execution_log_path = logs_dir / "execution.log"
        if not execution_log_path.exists():
            execution_log_path.write_text("mock HPC smoke execution\n", encoding="utf-8")
        return

    if run.nextflow_params.get("workflow_stage") == "is_qc_review":
        results_dir = Path(run.results_path)
        is_qc_dir = results_dir / "is-qc"
        rt_dir = results_dir / "rt-correction"
        pipeline_info_dir = results_dir / "pipeline_info"
        logs_dir = Path(run.logs_path)
        is_qc_dir.mkdir(parents=True, exist_ok=True)
        rt_dir.mkdir(parents=True, exist_ok=True)
        pipeline_info_dir.mkdir(parents=True, exist_ok=True)
        logs_dir.mkdir(parents=True, exist_ok=True)
        (is_qc_dir / "is_qc_summary.json").write_text(
            '{"overall_status":"pass","operator_message":"Mock IS-QC passed."}\n',
            encoding="utf-8",
        )
        (is_qc_dir / "is_qc_targets.tsv").write_text(
            "target_name\tsample_name\tsample_type\tstatus\tpassed\tapex_intensity\nIS: mock\tMock\tqc\tpass\ttrue\t1000\n",
            encoding="utf-8",
        )
        (is_qc_dir / "is_qc_traces.json").write_text('{"version":"is_qc_v1","traces":[]}\n', encoding="utf-8")
        (rt_dir / "rt_correction_plan.json").write_text(
            json.dumps(
                {
                    "version": "msdial_rt_correction_v1",
                    "can_apply": False,
                    "blocking_reasons": [
                        "Mock IS-QC data has no usable RT correction anchors."
                    ],
                    "target_means": {},
                    "samples": [],
                    "features": [],
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        (rt_dir / "rt_correction_preview.json").write_text(
            '{"version":"msdial_rt_correction_preview_v1","traces":[]}\n',
            encoding="utf-8",
        )
        (pipeline_info_dir / "execution_report.html").write_text("<html><body>mock IS-QC report</body></html>\n", encoding="utf-8")
        if not (logs_dir / "execution.log").exists():
            (logs_dir / "execution.log").write_text("mock IS-QC execution\n", encoding="utf-8")
        return

    msdial_dir = Path(run.results_path) / "ms-dial"
    pipeline_info_dir = Path(run.results_path) / "pipeline_info"
    logs_dir = Path(run.logs_path)
    msdial_dir.mkdir(parents=True, exist_ok=True)
    pipeline_info_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    (msdial_dir / f"AlignResult-job{run.job_id}.mdalign").write_text("mock alignment\n", encoding="utf-8")
    (msdial_dir / f"AlignResult-job{run.job_id}.mdmsp").write_text("mock spectra\n", encoding="utf-8")
    (msdial_dir / f"AlignResult-job{run.job_id}.mzTabM").write_text("mock mztabm\n", encoding="utf-8")
    (pipeline_info_dir / "execution_report.html").write_text("<html><body>mock report</body></html>\n", encoding="utf-8")
    (pipeline_info_dir / "execution_timeline.html").write_text("<html><body>mock timeline</body></html>\n", encoding="utf-8")
    (pipeline_info_dir / "execution_trace.txt").write_text("task_id\tname\tstatus\n", encoding="utf-8")
    (logs_dir / "success_report.txt").write_text("true\n", encoding="utf-8")
    execution_log_path = logs_dir / "execution.log"
    if not execution_log_path.exists():
        execution_log_path.write_text("mock nextflow execution\n", encoding="utf-8")
