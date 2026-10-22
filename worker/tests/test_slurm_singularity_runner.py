import unittest
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from config import WorkerSettings
from launchers.profile_runners.slurm_singularity import SlurmSingularityProfileRunner
from launchers.profile_runners.slurm_singularity_ssh import SlurmSingularitySshProfileRunner
from services.materialized_run import MaterializedRun
from launchers.profile_runners import slurm_singularity_ssh as slurm_ssh

def _run(root: Path, nextflow_params: dict[str, str] | None = None) -> MaterializedRun:
    contract_dir = root / "contract"
    results_dir = root / "results"
    logs_dir = root / "logs"
    for path in (contract_dir, results_dir, logs_dir, root / "inputs" / "raw_data", root / "work"):
        path.mkdir(parents=True, exist_ok=True)
    params = {
        "input_dir": str(root / "inputs" / "raw_data"),
        "ref": str(root / "inputs" / "raw_data" / "sample.raw"),
        "msdial_config": str(contract_dir / "msdial_config.txt"),
        "ms1_library": str(root / "libraries" / "ms1.txt"),
        "ms2_library": str(root / "libraries" / "ms2.msp"),
        "is_library": str(root / "libraries" / "is.txt"),
        "samplesheet": str(contract_dir / "samplesheet.csv"),
        "stats_factor_column": "group_label",
        "outdir": str(results_dir),
        "workflow_stage": "full",
    }
    params.update(nextflow_params or {})
    return MaterializedRun(
        job_id=7,
        study_id=1,
        assay_id=2,
        workflow_definition_id=1,
        execution_profile="slurm_singularity",
        pipeline_root=str(root / "pipeline"),
        entrypoint_path=str(root / "pipeline" / "main.nf"),
        nextflow_profile="embl_slurm",
        run_name="std1_assay2_job7",
        run_root=str(root),
        staged_input_dir=str(root / "inputs" / "raw_data"),
        contract_dir=str(contract_dir),
        results_path=str(results_dir),
        logs_path=str(logs_dir),
        work_path=str(root / "work"),
        params_path=str(contract_dir / "params.json"),
        samplesheet_path=str(contract_dir / "samplesheet.csv"),
        staged_msdial_config_path=str(contract_dir / "msdial_config.txt"),
        nextflow_config_override_path=str(contract_dir / "nextflow.override.config"),
        artifact_manifest_path=str(contract_dir / "artifact-manifest.json"),
        reference_file_asset_id=1,
        reference_path=str(root / "inputs" / "raw_data" / "sample.raw"),
        ms1_library_path=str(root / "libraries" / "ms1.txt"),
        ms2_library_path=str(root / "libraries" / "ms2.msp"),
        is_library_path=str(root / "libraries" / "is.txt"),
        config_source="uploaded_file",
        metadata_snapshot={},
        method_metadata={},
        selected_files=[],
        nextflow_params=params,
    )


class SlurmSingularityProfileRunnerTests(unittest.TestCase):
    def test_build_nextflow_command_uses_slurm_work_root_and_resume(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = _run(root, {"nextflow_resume": "true", "analysis_mode": "qc_only"})
            scratch = root / "scratch"
            runner = SlurmSingularityProfileRunner(
                WorkerSettings(
                    database_url="sqlite://",
                    workflow_slurm_work_root=str(scratch),
                )
            )

            command = runner.build_nextflow_command(run)

        self.assertIn("-resume", command)
        self.assertEqual(command[command.index("-resume") + 1], "std1_assay2_job7")
        self.assertNotIn("-name", command)
        self.assertEqual(command[command.index("-profile") + 1], "embl_slurm")
        self.assertEqual(command[command.index("-work-dir") + 1], str(scratch / "job_7" / "work"))
        self.assertIn("--analysis_mode", command)
        self.assertIn("qc_only", command)
        self.assertNotIn("--nextflow_resume", command)

    def test_write_sbatch_script_exports_container_dirs_and_nextflow_command(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = _run(root)
            settings = WorkerSettings(
                database_url="sqlite://",
                workflow_slurm_nextflow_module="Nextflow/25.10.1",
                workflow_slurm_partition="htc-el8",
                workflow_slurm_account="example-lab",
                workflow_slurm_driver_cpus=2,
                workflow_slurm_driver_memory="4G",
                workflow_slurm_driver_time="01:00:00",
                workflow_slurm_work_root=str(root / "scratch"),
                workflow_slurm_container_cache_dir=str(root / "containers" / "cache"),
                workflow_slurm_container_tmp_dir=str(root / "scratch" / "apptainer_tmp"),
            )
            runner = SlurmSingularityProfileRunner(settings)

            script_path = runner.write_sbatch_script(run)
            script = script_path.read_text(encoding="utf-8")

        self.assertEqual(script_path.name, "run_nextflow.sbatch")
        self.assertIn("#SBATCH --job-name=mlens_std1_assay2_job7", script)
        self.assertIn("#SBATCH --partition=htc-el8", script)
        self.assertIn("#SBATCH --account=example-lab", script)
        self.assertIn("#SBATCH --cpus-per-task=2", script)
        self.assertIn("#SBATCH --mem=4G", script)
        self.assertIn("#SBATCH --time=01:00:00", script)
        self.assertIn("module load Nextflow/25.10.1", script)
        self.assertIn("export APPTAINER_CACHEDIR=", script)
        self.assertIn('export SINGULARITY_CACHEDIR="${APPTAINER_CACHEDIR}"', script)
        self.assertIn("export APPTAINER_TMPDIR=", script)
        self.assertIn("nextflow run", script)
        self.assertIn("-profile embl_slurm", script)
        self.assertIn("--workflow_stage full", script)

    def test_build_command_uses_sbatch_wait(self) -> None:
        runner = SlurmSingularityProfileRunner(
            WorkerSettings(database_url="sqlite://", workflow_slurm_sbatch_bin="/usr/bin/sbatch")
        )

        command = runner.build_command(Path("/tmp/run_nextflow.sbatch"))

        self.assertEqual(command, ["/usr/bin/sbatch", "--wait", "/tmp/run_nextflow.sbatch"])


class SlurmSingularitySshProfileRunnerTests(unittest.TestCase):

    def test_build_detached_submit_command_uses_sbatch_parsable(self) -> None:
        with TemporaryDirectory() as tmpdir:
            local_root = Path(tmpdir) / "example-lab" / "members" / "researcher"
            run = _run(local_root / "metabolens" / "runs" / "job_7")
            runner = SlurmSingularitySshProfileRunner(
                WorkerSettings(
                    database_url="sqlite://",
                    workflow_slurm_sbatch_bin="sbatch",
                    workflow_slurm_ssh_bin="ssh",
                    workflow_slurm_ssh_user="researcher",
                    workflow_slurm_ssh_host="login.cluster.example.org",
                    workflow_slurm_ssh_options="-o BatchMode=yes",
                    workflow_slurm_local_mount_root=str(local_root),
                    workflow_slurm_remote_mount_root="/g/example-lab/members/researcher",
                )
            )
            remote_run = runner.remote_run_for(run)

            method = getattr(
                runner,
                "build_detached_submit_command",
                None,
            )
            self.assertIsNotNone(
                method,
                "build_detached_submit_command is not implemented",
            )

            command = method(remote_run)

        self.assertEqual(
            command[:4],
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "researcher@login.cluster.example.org",
            ],
        )
        self.assertEqual(
            command[4],
            "cd /g/example-lab/members/researcher/metabolens/runs/job_7 "
            "&& sbatch --parsable contract/run_nextflow.sbatch",
        )


    @patch(
        "launchers.profile_runners."
        "slurm_singularity_ssh.subprocess.run"
    )
    def test_submit_detached_returns_job_id_and_writes_receipt(
        self,
        run_command,
    ) -> None:
        with TemporaryDirectory() as tmpdir:
            local_root = Path(tmpdir) / "example-lab" / "members" / "researcher"
            run = _run(local_root / "metabolens" / "runs" / "job_7")
            runner = SlurmSingularitySshProfileRunner(
                WorkerSettings(
                    database_url="sqlite://",
                    workflow_slurm_ssh_host="login.cluster.example.org",
                    workflow_slurm_ssh_user="researcher",
                    workflow_slurm_local_mount_root=str(local_root),
                    workflow_slurm_remote_mount_root="/g/example-lab/members/researcher",
                )
            )
            run_command.return_value = subprocess.CompletedProcess(
                args=["ssh"],
                returncode=0,
                stdout="482913;embl\n",
                stderr="",
            )

            method = getattr(runner, "submit_detached", None)
            self.assertIsNotNone(
                method,
                "submit_detached is not implemented",
            )

            job_id = method(run)

            receipt_path = (
                Path(run.contract_dir) / "slurm-job-id.txt"
            )

            self.assertEqual(job_id, "482913")
            self.assertEqual(
                receipt_path.read_text(encoding="utf-8"),
                "482913\n",
            )
            self.assertTrue(
                (Path(run.contract_dir) / "run_nextflow.sbatch").is_file()
            )

    @patch(
        "launchers.profile_runners."
        "slurm_singularity_ssh.subprocess.run"
    )
    def test_submit_detached_does_not_write_receipt_on_ssh_failure(
        self,
        run_command,
    ) -> None:
        with TemporaryDirectory() as tmpdir:
            local_root = Path(tmpdir) / "example-lab" / "members" / "researcher"
            run = _run(local_root / "metabolens" / "runs" / "job_7")
            runner = SlurmSingularitySshProfileRunner(
                WorkerSettings(
                    database_url="sqlite://",
                    workflow_slurm_ssh_host="login.cluster.example.org",
                    workflow_slurm_ssh_user="researcher",
                    workflow_slurm_local_mount_root=str(local_root),
                    workflow_slurm_remote_mount_root="/g/example-lab/members/researcher",
                )
            )
            run_command.return_value = subprocess.CompletedProcess(
                args=["ssh"],
                returncode=255,
                stdout="",
                stderr="Permission denied",
            )

            method = getattr(runner, "submit_detached", None)
            self.assertIsNotNone(
                method,
                "submit_detached is not implemented",
            )

            with self.assertRaisesRegex(
                RuntimeError,
                "Permission denied",
            ):
                method(run)

            receipt_path = (
                Path(run.contract_dir) / "slurm-job-id.txt"
            )
            self.assertFalse(receipt_path.exists())

    @patch(
        "launchers.profile_runners."
        "slurm_singularity_ssh.subprocess.run"
    )
    def test_query_job_status_returns_active_squeue_state(
        self,
        run_command,
    ) -> None:
        runner = SlurmSingularitySshProfileRunner(
            WorkerSettings(
                database_url="sqlite://",
                workflow_slurm_ssh_host="login.cluster.example.org",
                workflow_slurm_ssh_user="researcher",
                workflow_slurm_ssh_options="-o BatchMode=yes",
            )
        )
        run_command.return_value = subprocess.CompletedProcess(
            args=["ssh"],
            returncode=0,
            stdout="RUNNING\n",
            stderr="",
        )

        method = getattr(runner, "query_job_status", None)
        self.assertIsNotNone(
            method,
            "query_job_status is not implemented",
        )

        status = method("482913")

        self.assertIsNotNone(status)
        self.assertEqual(status.state, "RUNNING")
        self.assertFalse(status.terminal)
        self.assertIsNone(status.exit_code)
        self.assertEqual(run_command.call_count, 1)

    @patch(
        "launchers.profile_runners."
        "slurm_singularity_ssh.subprocess.run"
    )
    def test_query_job_status_falls_back_to_sacct(
        self,
        run_command,
    ) -> None:
        runner = SlurmSingularitySshProfileRunner(
            WorkerSettings(
                database_url="sqlite://",
                workflow_slurm_ssh_host="login.cluster.example.org",
                workflow_slurm_ssh_user="researcher",
            )
        )
        run_command.side_effect = [
            subprocess.CompletedProcess(
                args=["ssh", "squeue"],
                returncode=0,
                stdout="",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=["ssh", "sacct"],
                returncode=0,
                stdout=(
                    "482913.batch|COMPLETED|0:0\n"
                    "482913|COMPLETED|0:0\n"
                ),
                stderr="",
            ),
        ]

        method = getattr(runner, "query_job_status", None)
        self.assertIsNotNone(
            method,
            "query_job_status is not implemented",
        )

        status = method("482913")

        self.assertIsNotNone(status)
        self.assertEqual(status.state, "COMPLETED")
        self.assertTrue(status.terminal)
        self.assertEqual(status.exit_code, 0)
        self.assertEqual(run_command.call_count, 2)

    @patch(
        "launchers.profile_runners."
        "slurm_singularity_ssh.subprocess.run"
    )
    def test_query_job_status_falls_back_when_squeue_rejects_finished_job_id(
        self,
        run_command,
    ) -> None:
        runner = SlurmSingularitySshProfileRunner(
            WorkerSettings(
                database_url="sqlite://",
                workflow_slurm_ssh_host="login.cluster.example.org",
                workflow_slurm_ssh_user="researcher",
            )
        )
        run_command.side_effect = [
            subprocess.CompletedProcess(
                args=["ssh", "squeue"],
                returncode=1,
                stdout="",
                stderr=(
                    "slurm_load_jobs error: "
                    "Invalid job id specified\n"
                ),
            ),
            subprocess.CompletedProcess(
                args=["ssh", "sacct"],
                returncode=0,
                stdout="60680141|COMPLETED|0:0\n",
                stderr="",
            ),
        ]

        try:
            status = runner.query_job_status("60680141")
        except slurm_ssh.SlurmStatusUnavailable as exc:
            self.fail(
                "A finished job must fall back to sacct instead of "
                f"reporting the scheduler unavailable: {exc}"
            )

        self.assertIsNotNone(status)
        self.assertEqual(status.state, "COMPLETED")
        self.assertTrue(status.terminal)
        self.assertEqual(status.exit_code, 0)

    @patch(
        "launchers.profile_runners."
        "slurm_singularity_ssh.subprocess.run"
    )
    def test_query_job_status_returns_none_while_accounting_is_delayed(
        self,
        run_command,
    ) -> None:
        runner = SlurmSingularitySshProfileRunner(
            WorkerSettings(
                database_url="sqlite://",
                workflow_slurm_ssh_host="login.cluster.example.org",
            )
        )
        run_command.side_effect = [
            subprocess.CompletedProcess(
                args=["ssh", "squeue"],
                returncode=0,
                stdout="",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=["ssh", "sacct"],
                returncode=0,
                stdout="",
                stderr="",
            ),
        ]

        method = getattr(runner, "query_job_status", None)
        self.assertIsNotNone(
            method,
            "query_job_status is not implemented",
        )

        status = method("482913")

        self.assertIsNone(status)

    @patch(
        "launchers.profile_runners."
        "slurm_singularity_ssh.subprocess.run"
    )
    def test_query_job_status_reports_temporary_ssh_failure(
        self,
        run_command,
    ) -> None:
        runner = SlurmSingularitySshProfileRunner(
            WorkerSettings(
                database_url="sqlite://",
                workflow_slurm_ssh_host="login.cluster.example.org",
            )
        )
        run_command.return_value = subprocess.CompletedProcess(
            args=["ssh"],
            returncode=255,
            stdout="",
            stderr="Connection refused",
        )

        method = getattr(runner, "query_job_status", None)
        self.assertIsNotNone(
            method,
            "query_job_status is not implemented",
        )

        error_type = getattr(
            slurm_ssh,
            "SlurmStatusUnavailable",
            RuntimeError,
        )

        with self.assertRaisesRegex(
            error_type,
            "Connection refused",
        ):
            method("482913")

    @patch(
        "launchers.profile_runners."
        "slurm_singularity_ssh.subprocess.run"
    )
    def test_query_job_status_reports_ssh_timeout(
        self,
        run_command,
    ) -> None:
        runner = SlurmSingularitySshProfileRunner(
            WorkerSettings(
                database_url="sqlite://",
                workflow_slurm_ssh_host="login.cluster.example.org",
            )
        )
        run_command.side_effect = subprocess.TimeoutExpired(
            cmd=["ssh"],
            timeout=30,
        )

        with self.assertRaisesRegex(
            slurm_ssh.SlurmStatusUnavailable,
            "timed out",
        ):
            runner.query_job_status("482913")

    @patch(
        "launchers.profile_runners."
        "slurm_singularity_ssh.subprocess.run"
    )
    def test_submit_detached_records_uncertain_timeout(
        self,
        run_command,
    ) -> None:
        with TemporaryDirectory() as tmpdir:
            local_root = Path(tmpdir) / "example-lab"
            run = _run(local_root / "runs" / "job_7")
            runner = SlurmSingularitySshProfileRunner(
                WorkerSettings(
                    database_url="sqlite://",
                    workflow_slurm_ssh_host="login.cluster.example.org",
                    workflow_slurm_local_mount_root=str(local_root),
                    workflow_slurm_remote_mount_root="/g/example-lab",
                )
            )
            run_command.side_effect = subprocess.TimeoutExpired(
                cmd=["ssh"],
                timeout=30,
            )

            error_type = getattr(
                slurm_ssh,
                "SlurmSubmissionUncertain",
                RuntimeError,
            )

            with self.assertRaisesRegex(
                error_type,
                "may have reached the scheduler",
            ):
                runner.submit_detached(run)

            attempt_path = (
                Path(run.contract_dir)
                / "slurm-submission-attempt.txt"
            )
            receipt_path = (
                Path(run.contract_dir) / "slurm-job-id.txt"
            )

            self.assertTrue(attempt_path.is_file())
            self.assertFalse(receipt_path.exists())

    @patch(
        "launchers.profile_runners."
        "slurm_singularity_ssh.subprocess.run"
    )
    def test_submit_detached_refuses_second_uncertain_attempt(
        self,
        run_command,
    ) -> None:
        with TemporaryDirectory() as tmpdir:
            run = _run(Path(tmpdir))
            attempt_path = (
                Path(run.contract_dir)
                / "slurm-submission-attempt.txt"
            )
            attempt_path.write_text(
                "job_name=mlens_std1_assay2_job7\n",
                encoding="utf-8",
            )
            runner = SlurmSingularitySshProfileRunner(
                WorkerSettings(
                    database_url="sqlite://",
                    workflow_slurm_ssh_host="login.cluster.example.org",
                )
            )
            run_command.side_effect = [
                subprocess.CompletedProcess(
                args=["ssh","squeue"]   ,
                returncode=0,
                stdout="",
                stderr="",
            ),
                subprocess.CompletedProcess(
                args=["ssh","sacct"],
                returncode=0,
                stdout="",
                stderr="",
            ),
            ]
            error_type = getattr(
                slurm_ssh,
                "SlurmSubmissionUncertain",
                RuntimeError,
            )

            with self.assertRaisesRegex(
                error_type,
                "previous SLURM submission",
            ):
                runner.submit_detached(run)

            self.assertEqual(run_command.call_count, 2)
            for call in run_command.call_args_list:
                command = call.args[0]
                self.assertNotIn("sbatch", command[-1])

    @patch("launchers.profile_runners.slurm_singularity_ssh.subprocess.run")
    def test_continuation_has_its_own_receipt_and_recovery_name(self, run_command):
        with TemporaryDirectory() as tmpdir:
            run = _run(Path(tmpdir))
            runner = SlurmSingularitySshProfileRunner(WorkerSettings(
                database_url="sqlite://", workflow_slurm_ssh_host="login.cluster.example.org"))
            runner.write_slurm_job_id_receipt(run, "482913")
            runner.write_slurm_submission_attempt(run)
            original_name = runner.slurm_job_name(run)
            run.submission_attempt = 1
            run_command.return_value = subprocess.CompletedProcess([], 0, "482914\n", "")
            self.assertEqual(runner.submit_detached(run), "482914")
            self.assertNotEqual(runner.slurm_job_name(run), original_name)
            self.assertEqual(runner.submit_detached(run), "482914")
            run_command.assert_called_once()
            self.assertEqual((Path(run.contract_dir) / "slurm-job-id.txt").read_text().strip(), "482913")

    @patch(
        "launchers.profile_runners."
        "slurm_singularity_ssh.subprocess.run"
    )
    def test_submit_detached_reuses_existing_receipt_without_ssh(
        self,
        run_command,
    ) -> None:
        with TemporaryDirectory() as tmpdir:
            run = _run(Path(tmpdir))
            receipt_path = (
                Path(run.contract_dir) / "slurm-job-id.txt"
            )
            receipt_path.write_text(
                "482913\n",
                encoding="utf-8",
            )
            runner = SlurmSingularitySshProfileRunner(
                WorkerSettings(
                    database_url="sqlite://",
                    workflow_slurm_ssh_host="login.cluster.example.org",
                )
            )

            job_id = runner.submit_detached(run)

            self.assertEqual(job_id, "482913")
            run_command.assert_not_called()

    @patch(
        "launchers.profile_runners."
        "slurm_singularity_ssh.subprocess.run"
    )
    def test_submit_detached_rejects_invalid_existing_receipt(
        self,
        run_command,
    ) -> None:
        with TemporaryDirectory() as tmpdir:
            run = _run(Path(tmpdir))
            receipt_path = (
                Path(run.contract_dir) / "slurm-job-id.txt"
            )
            receipt_path.write_text(
                "not-a-job-id\n",
                encoding="utf-8",
            )
            runner = SlurmSingularitySshProfileRunner(
                WorkerSettings(
                    database_url="sqlite://",
                    workflow_slurm_ssh_host="login.cluster.example.org",
                )
            )

            with self.assertRaisesRegex(
                ValueError,
                "Invalid SLURM job receipt",
            ):
                runner.submit_detached(run)

            run_command.assert_not_called()

    @patch(
        "launchers.profile_runners."
        "slurm_singularity_ssh.subprocess.run"
    )
    def test_submit_detached_recovers_active_job_from_squeue(
        self,
        run_command,
    ) -> None:
        with TemporaryDirectory() as tmpdir:
            run = _run(Path(tmpdir))
            attempt_path = (
                Path(run.contract_dir)
                / "slurm-submission-attempt.txt"
            )
            attempt_path.write_text(
                "job_name=mlens_std1_assay2_job7\n",
                encoding="utf-8",
            )
            runner = SlurmSingularitySshProfileRunner(
                WorkerSettings(
                    database_url="sqlite://",
                    workflow_slurm_ssh_host="login.cluster.example.org",
                )
            )
            run_command.return_value = subprocess.CompletedProcess(
                args=["ssh", "squeue"],
                returncode=0,
                stdout=(
                    "482913|mlens_std1_assay2_job7\n"
                ),
                stderr="",
            )

            job_id = runner.submit_detached(run)

            self.assertEqual(job_id, "482913")
            self.assertEqual(run_command.call_count, 1)
            receipt_path = (
                Path(run.contract_dir) / "slurm-job-id.txt"
            )
            self.assertEqual(
                receipt_path.read_text(encoding="utf-8"),
                "482913\n",
            )
            self.assertIn(
                "squeue",
                run_command.call_args.args[0][-1],
            )
            self.assertNotIn(
                "sbatch",
                run_command.call_args.args[0][-1],
            )


    @patch(
        "launchers.profile_runners."
        "slurm_singularity_ssh.subprocess.run"
    )
    def test_submit_detached_recovers_completed_job_from_sacct(
        self,
        run_command,
    ) -> None:
        with TemporaryDirectory() as tmpdir:
            run = _run(Path(tmpdir))
            attempt_path = (
                Path(run.contract_dir)
                / "slurm-submission-attempt.txt"
            )
            attempt_path.write_text(
                "job_name=mlens_std1_assay2_job7\n",
                encoding="utf-8",
            )
            runner = SlurmSingularitySshProfileRunner(
                WorkerSettings(
                    database_url="sqlite://",
                    workflow_slurm_ssh_host="login.cluster.example.org",
                )
            )
            run_command.side_effect = [
                subprocess.CompletedProcess(
                    args=["ssh", "squeue"],
                    returncode=0,
                    stdout="",
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=["ssh", "sacct"],
                    returncode=0,
                    stdout=(
                        "482913|mlens_std1_assay2_job7\n"
                    ),
                    stderr="",
                ),
            ]

            job_id = runner.submit_detached(run)

            self.assertEqual(job_id, "482913")
            self.assertEqual(run_command.call_count, 2)
            receipt_path = (
                Path(run.contract_dir) / "slurm-job-id.txt"
            )
            self.assertEqual(
                receipt_path.read_text(encoding="utf-8"),
                "482913\n",
            )
            self.assertIn(
                "sacct",
                run_command.call_args.args[0][-1],
            )


    @patch(
        "launchers.profile_runners."
        "slurm_singularity_ssh.subprocess.run"
    )
    def test_submit_detached_rejects_ambiguous_name_matches(
        self,
        run_command,
    ) -> None:
        with TemporaryDirectory() as tmpdir:
            run = _run(Path(tmpdir))
            attempt_path = (
                Path(run.contract_dir)
                / "slurm-submission-attempt.txt"
            )
            attempt_path.write_text(
                "job_name=mlens_std1_assay2_job7\n",
                encoding="utf-8",
            )
            runner = SlurmSingularitySshProfileRunner(
                WorkerSettings(
                    database_url="sqlite://",
                    workflow_slurm_ssh_host="login.cluster.example.org",
                )
            )
            run_command.return_value = subprocess.CompletedProcess(
                args=["ssh", "squeue"],
                returncode=0,
                stdout=(
                    "482913|mlens_std1_assay2_job7\n"
                    "482914|mlens_std1_assay2_job7\n"
                ),
                stderr="",
            )

            with self.assertRaisesRegex(
                slurm_ssh.SlurmSubmissionUncertain,
                "Multiple SLURM jobs",
            ):
                runner.submit_detached(run)

            receipt_path = (
                Path(run.contract_dir) / "slurm-job-id.txt"
            )
            self.assertFalse(receipt_path.exists())

    def test_remote_path_maps_mac_example_lab_mount_to_cluster_g_root(self) -> None:
        runner = SlurmSingularitySshProfileRunner(
            WorkerSettings(
                database_url="sqlite://",
                workflow_slurm_local_mount_root="/Volumes/example-lab",
                workflow_slurm_remote_mount_root="/g/example-lab",
            )
        )

        mapped = runner.remote_path("/Volumes/example-lab/members/researcher/metabolens/runs/job_7")

        self.assertEqual(mapped, "/g/example-lab/members/researcher/metabolens/runs/job_7")

    def test_remote_run_maps_local_mount_paths_to_cluster_paths(self) -> None:
        with TemporaryDirectory() as tmpdir:
            local_root = Path(tmpdir) / "example-lab" / "members" / "researcher"
            run = _run(local_root / "metabolens" / "runs" / "job_7")
            runner = SlurmSingularitySshProfileRunner(
                WorkerSettings(
                    database_url="sqlite://",
                    workflow_slurm_local_mount_root=str(local_root),
                    workflow_slurm_remote_mount_root="/g/example-lab/members/researcher",
                )
            )

            remote_run = runner.remote_run_for(run)

        self.assertEqual(remote_run.run_root, "/g/example-lab/members/researcher/metabolens/runs/job_7")
        self.assertEqual(remote_run.contract_dir, "/g/example-lab/members/researcher/metabolens/runs/job_7/contract")
        self.assertEqual(remote_run.nextflow_params["input_dir"], "/g/example-lab/members/researcher/metabolens/runs/job_7/inputs/raw_data")
        self.assertEqual(remote_run.nextflow_params["outdir"], "/g/example-lab/members/researcher/metabolens/runs/job_7/results")

    def test_build_command_submits_remote_relative_script_over_ssh(self) -> None:
        with TemporaryDirectory() as tmpdir:
            local_root = Path(tmpdir) / "example-lab" / "members" / "researcher"
            run = _run(local_root / "metabolens" / "runs" / "job_7")
            runner = SlurmSingularitySshProfileRunner(
                WorkerSettings(
                    database_url="sqlite://",
                    workflow_slurm_sbatch_bin="sbatch",
                    workflow_slurm_ssh_bin="ssh",
                    workflow_slurm_ssh_user="researcher",
                    workflow_slurm_ssh_host="login.cluster.example.org",
                    workflow_slurm_ssh_options="-o BatchMode=yes",
                    workflow_slurm_local_mount_root=str(local_root),
                    workflow_slurm_remote_mount_root="/g/example-lab/members/researcher",
                )
            )
            remote_run = runner.remote_run_for(run)

            command = runner.build_command(remote_run)

        self.assertEqual(command[0:3], ["ssh", "-o", "BatchMode=yes"])
        self.assertEqual(command[3], "researcher@login.cluster.example.org")
        self.assertEqual(
            command[4],
            "cd /g/example-lab/members/researcher/metabolens/runs/job_7 && sbatch --wait contract/run_nextflow.sbatch",
        )

    def test_write_sbatch_script_writes_remote_paths_to_local_file(self) -> None:
        with TemporaryDirectory() as tmpdir:
            local_root = Path(tmpdir) / "example-lab" / "members" / "researcher"
            run = _run(local_root / "metabolens" / "runs" / "job_7")
            runner = SlurmSingularitySshProfileRunner(
                WorkerSettings(
                    database_url="sqlite://",
                    workflow_slurm_local_mount_root=str(local_root),
                    workflow_slurm_remote_mount_root="/g/example-lab/members/researcher",
                    workflow_slurm_work_root="/scratch/lehr/metabolens/work",
                )
            )
            remote_run = runner.remote_run_for(run)

            script_path = runner.write_sbatch_script(run, remote_run)
            script = script_path.read_text(encoding="utf-8")

        self.assertTrue(str(script_path).startswith(str(local_root)))
        self.assertIn("cd /g/example-lab/members/researcher/metabolens/runs/job_7", script)
        self.assertIn("-work-dir /scratch/lehr/metabolens/work/job_7/work", script)
        self.assertIn("--input_dir /g/example-lab/members/researcher/metabolens/runs/job_7/inputs/raw_data", script)
        self.assertNotIn(str(local_root), script)


class SlurmSchedulerParsingTests(unittest.TestCase):
    def test_parse_sbatch_job_id_accepts_cluster_suffix(self) -> None:
        parser = getattr(slurm_ssh, "parse_sbatch_job_id", None)

        self.assertIsNotNone(parser, "parse_sbatch_job_id function not found in slurm_singularity_ssh module")

        job_id = parser("123456;embl\n")
        self.assertEqual(job_id, "123456")

    def test_parse_squeue_status_reports_running_job_as_active(self) -> None:
        parser = getattr(slurm_ssh, "parse_squeue_status", None)

        self.assertIsNotNone(parser, "parse_squeue_status function not found in slurm_singularity_ssh module")
        status = parser("RUNNING")
        self.assertIsNotNone(status)
        self.assertEqual(status.state, "RUNNING")
        self.assertFalse(status.terminal)
        self.assertIsNone(status.exit_code)

    def test_parse_sacct_status_selects_parent_job_and_reports_success(self) -> None:
        parser = getattr(slurm_ssh, "parse_sacct_status", None)

        self.assertIsNotNone(
            parser,
            "parse_sacct_status is not implemented",
        )

        output = (
            "482913.batch|COMPLETED|0:0\n"
            "482913.extern|COMPLETED|0:0\n"
            "482913|COMPLETED|0:0\n"
        )

        status = parser(output, "482913")

        self.assertIsNotNone(status)
        self.assertEqual(status.state, "COMPLETED")
        self.assertTrue(status.terminal)
        self.assertEqual(status.exit_code, 0)


    def test_parse_sacct_status_converts_failure_to_nonzero_exit(self) -> None:
        parser = getattr(slurm_ssh, "parse_sacct_status", None)

        self.assertIsNotNone(
            parser,
            "parse_sacct_status is not implemented",
        )

        status = parser(
            "482913|OUT_OF_MEMORY|0:125\n",
            "482913",
        )

        self.assertIsNotNone(status)
        self.assertEqual(status.state, "OUT_OF_MEMORY")
        self.assertTrue(status.terminal)
        self.assertEqual(status.exit_code, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
