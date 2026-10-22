import csv
import re
from pathlib import Path
from typing import Any

from services.artifact_collector import engine_output_paths
from services.materialized_run import MaterializedRun, resume_requested


class StatusReader:
    SUCCESS_STATUSES = {"COMPLETED", "CACHED"}
    FAILURE_STATUSES = {"ABORTED", "FAILED", "TERMINATED"}
    RUNNING_STATUSES = {"RUNNING", "SUBMITTED", "NEW", "PENDING"}

    def build_status_payload(
        self,
        run: MaterializedRun,
        *,
        phase: str,
        external_run_id: str | None = None,
        exit_code: int | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        results_dir = Path(run.results_path)
        logs_dir = Path(run.logs_path)
        pipeline_info_dir = results_dir / "pipeline_info"
        workflow_stage = run.nextflow_params.get("workflow_stage", "full")
        payload = {
            "phase": phase,
            "run_name": run.run_name,
            "run_root": run.run_root,
            "results_path": run.results_path,
            "logs_path": run.logs_path,
            "artifact_manifest_path": run.artifact_manifest_path,
            "workflow_stage": workflow_stage,
            "nextflow_resume": resume_requested(run),
            "external_run_id": external_run_id,
            "exit_code": exit_code,
            "error_message": error_message,
            "files": {
                "primary_output_count": self.primary_output_count(results_dir, workflow_stage, run.engine_execution),
                "pipeline_info_count": len(list(pipeline_info_dir.glob("*"))) if pipeline_info_dir.exists() else 0,
                "log_count": len(list(logs_dir.glob("*"))) if logs_dir.exists() else 0,
            },
        }
        payload.update(self.read_task_summary(run))
        if phase == "succeeded" and not payload["estimated_total_tasks"]:
            payload["total_tasks"] = payload["observed_tasks"]
        return payload

    def read_task_summary(self, run: MaterializedRun) -> dict[str, Any]:
        """Summarize Nextflow trace progress without depending on live logs."""

        trace_path = Path(run.results_path) / "pipeline_info" / "execution_trace.txt"
        log_path = Path(run.logs_path) / "execution.log"
        estimated_total = self.estimate_total_tasks(run)
        rows = self._read_trace_rows(trace_path)
        status_counts: dict[str, int] = {}
        recent_tasks: list[dict[str, str]] = []

        for row in rows:
            status = self._row_value(row, "status").strip().upper()
            if status:
                status_counts[status] = status_counts.get(status, 0) + 1
            task_name = self._row_value(row, "name") or self._row_value(row, "process")
            if task_name or status:
                recent_tasks.append({"name": task_name or "-", "status": status.lower() or "unknown"})

        completed_tasks = sum(status_counts.get(status, 0) for status in self.SUCCESS_STATUSES)
        failed_tasks = sum(status_counts.get(status, 0) for status in self.FAILURE_STATUSES)
        running_tasks = sum(status_counts.get(status, 0) for status in self.RUNNING_STATUSES)
        cached_tasks = status_counts.get("CACHED", 0)
        observed_tasks = len(rows)
        # Terminal trace rows do not include all pending work in an active run.
        total_tasks = max(estimated_total, observed_tasks) if estimated_total else 0

        return {
            "completed_tasks": completed_tasks,
            "total_tasks": total_tasks,
            "estimated_total_tasks": estimated_total,
            "observed_tasks": observed_tasks,
            "failed_tasks": failed_tasks,
            "cached_tasks": cached_tasks,
            "running_tasks": running_tasks,
            "task_status_counts": status_counts,
            "recent_tasks": recent_tasks[-8:],
            "process_states": self.process_states(rows, self.submitted_processes(log_path)),
            "task_progress_available": bool(rows),
            "task_progress_source": "nextflow_trace" if rows else "estimated" if estimated_total else "unavailable",
        }

    @classmethod
    def process_name(cls, task_name: str) -> str:
        """`RUN_SIRIUS:run_sirius (sirius_input)` -> `RUN_SIRIUS:run_sirius`.

        The trace names a task as the fully qualified process plus a tag. Keeping
        the subworkflow prefix is deliberate: it is what distinguishes one
        pipeline branch from another, and it is what a consumer matches against.
        """
        return str(task_name or "").split(" (")[0].strip()

    SUBMITTED_RE = re.compile(r"Submitted process > ([A-Za-z0-9_:.\-]+)")

    @classmethod
    def submitted_processes(cls, log_path: Path) -> set[str]:
        """Processes the log says Nextflow started.

        The execution trace only records a task once it terminates -- every row
        it has ever written here is COMPLETED, CACHED, FAILED or ABORTED -- so a
        task in flight is invisible to it. The log announces each start, and
        started minus terminated is what is running now.
        """
        if not log_path.is_file():
            return set()
        found: set[str] = set()
        try:
            with log_path.open("r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    if "Submitted process > " in line:
                        match = cls.SUBMITTED_RE.search(line)
                        if match:
                            found.add(match.group(1))
        except OSError:
            return set()
        return found

    @classmethod
    def process_states(cls, rows: list[dict[str, Any]], submitted: set[str] | None = None) -> dict[str, str]:
        """One state per process, rolled up from its tasks.

        A process is failed if any of its tasks failed, running if any is still
        going, and done only when every task has finished. Reported by fully
        qualified process name and nothing else -- which station a process
        belongs to is a presentation question, not the worker's.
        """
        by_process: dict[str, set[str]] = {}
        for row in rows:
            name = cls.process_name(cls._row_value(row, "name") or cls._row_value(row, "process"))
            status = cls._row_value(row, "status").strip().upper()
            if not name or not status:
                continue
            by_process.setdefault(name, set()).add(status)

        states: dict[str, str] = {}
        for name, statuses in by_process.items():
            if statuses & cls.FAILURE_STATUSES:
                states[name] = "failed"
            elif statuses & cls.RUNNING_STATUSES:
                states[name] = "running"
            elif statuses & cls.SUCCESS_STATUSES:
                states[name] = "done"
            else:
                states[name] = "pending"
        # Started but not yet terminated. Without this nothing is ever reported
        # as running, because the trace only gains a row once a task ends.
        for name in submitted or set():
            states.setdefault(name, "running")
        return states

    @staticmethod
    def estimate_total_tasks(run: MaterializedRun) -> int:
        """Task count known before the trace exists: only the HPC smoke test's two tasks.

        Engine stages group processes, not task instances, so engine jobs use
        the observed trace until a task-count contract exists.
        """

        if not run.engine_execution and (run.nextflow_params or {}).get("workflow_stage") == "hpc_smoke":
            return 2
        return 0

    @staticmethod
    def primary_output_count(
        results_dir: Path, workflow_stage: str | None, engine_execution: dict[str, Any] | None = None,
    ) -> int:
        if not results_dir.exists():
            return 0
        if workflow_stage == "hpc_smoke":
            return len(list(results_dir.glob("*.upper.txt")))
        if workflow_stage == "is_qc_review":
            return len(list((results_dir / "is-qc").glob("is_qc_*")))
        if engine_execution:
            patterns = [pattern for pattern, _ in engine_execution.get("native_outputs", [])]
            patterns.extend(
                f"metabolensr/{prefix}{relative}"
                for prefix in ("", "*/")
                for relative in ("preview/qc_preview.rds", "final/metabolensr_result.rds", "final/qc_report.html")
            )
            root = results_dir.resolve()
            # Overlapping patterns can match the same file; count it once.
            return len({path.resolve() for pattern in patterns for path in engine_output_paths(root, pattern)})
        return 0

    @classmethod
    def _read_trace_rows(cls, trace_path: Path) -> list[dict[str, Any]]:
        if not trace_path.is_file() or trace_path.stat().st_size <= 0:
            return []
        try:
            with trace_path.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
                reader = csv.DictReader(handle, delimiter="\t")
                if not reader.fieldnames:
                    return []
                return [row for row in reader if row]
        except OSError:
            return []

    @staticmethod
    def _row_value(row: dict[str, Any], key: str) -> str:
        for row_key, value in row.items():
            if str(row_key or "").strip().lower() == key:
                return str(value or "")
        return ""
