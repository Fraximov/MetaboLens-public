import unittest
from unittest.mock import Mock

from config import WorkerSettings
from launchers import nextflow_launcher as launcher_module
from launchers.nextflow_launcher import NextflowLauncher
from launchers.profile_runners import slurm_singularity_ssh as slurm_ssh


class NextflowLauncherSlurmTests(unittest.TestCase):
    def setUp(self) -> None:
        self.launcher = NextflowLauncher(
            WorkerSettings(
                database_url="sqlite://",
                nextflow_mode="real",
            )
        )

    def slurm_handle_type(self):
        handle_type = getattr(
            launcher_module,
            "SlurmRunHandle",
            None,
        )
        self.assertIsNotNone(
            handle_type,
            "SlurmRunHandle is not implemented",
        )
        return handle_type

    def test_submit_uses_detached_slurm_job_id(self) -> None:
        handle_type = self.slurm_handle_type()
        run = Mock()
        run.execution_profile = "slurm_singularity_ssh"
        remote_run = Mock()

        self.launcher.slurm_ssh_runner.submit = Mock(
            return_value=object()
        )
        self.launcher.slurm_ssh_runner.remote_run_for = Mock(
            return_value=remote_run
        )
        self.launcher.slurm_ssh_runner.build_detached_submit_command = Mock(
            return_value=["ssh", "login", "sbatch --parsable"]
        )
        self.launcher.slurm_ssh_runner.submit_detached = Mock(
            return_value="482913"
        )

        handle = self.launcher.submit(run)

        self.assertIsInstance(handle, handle_type)
        self.assertEqual(handle.external_run_id, "482913")
        self.assertEqual(
            handle.command,
            ["slurm-job", "482913"],
        )
        self.launcher.slurm_ssh_runner.submit.assert_not_called()
        self.launcher.slurm_ssh_runner.submit_detached.assert_called_once_with(
            run
        )

    def test_poll_keeps_active_slurm_job_running(self) -> None:
        handle_type = self.slurm_handle_type()
        handle = handle_type(
            run=Mock(),
            external_run_id="482913",
            command=["ssh"],
        )
        self.launcher.slurm_ssh_runner.query_job_status = Mock(
            return_value=slurm_ssh.SlurmJobStatus(
                state="RUNNING",
                terminal=False,
            )
        )

        exit_code = self.launcher.poll(handle)

        self.assertIsNone(exit_code)

    def test_poll_and_wait_return_terminal_slurm_exit_code(self) -> None:
        handle_type = self.slurm_handle_type()
        handle = handle_type(
            run=Mock(),
            external_run_id="482913",
            command=["ssh"],
        )
        self.launcher.slurm_ssh_runner.query_job_status = Mock(
            return_value=slurm_ssh.SlurmJobStatus(
                state="COMPLETED",
                terminal=True,
                exit_code=0,
            )
        )

        exit_code = self.launcher.poll(handle)

        self.assertEqual(exit_code, 0)
        self.assertEqual(self.launcher.wait(handle), 0)
        self.assertEqual(handle.exit_code, 0)

    def test_poll_treats_temporary_ssh_failure_as_still_active(
        self,
    ) -> None:
        handle_type = self.slurm_handle_type()
        handle = handle_type(
            run=Mock(),
            external_run_id="482913",
            command=["ssh"],
        )
        self.launcher.slurm_ssh_runner.query_job_status = Mock(
            side_effect=slurm_ssh.SlurmStatusUnavailable(
                "Connection refused"
            )
        )

        exit_code = self.launcher.poll(handle)

        self.assertIsNone(exit_code)

    def test_attach_slurm_reuses_existing_job_without_submission(
        self,
    ) -> None:
        handle_type = self.slurm_handle_type()
        run = Mock()
        run.execution_profile = "slurm_singularity_ssh"
        self.launcher.slurm_ssh_runner.submit_detached = Mock(
            side_effect=AssertionError(
                "Recovery must not submit another SLURM job"
            )
        )

        handle = self.launcher.attach_slurm(
            run,
            "482913",
        )

        self.assertIsInstance(handle, handle_type)
        self.assertIs(handle.run, run)
        self.assertEqual(handle.external_run_id, "482913")
        self.assertEqual(
            handle.command,
            ["slurm-job", "482913"],
        )
        self.launcher.slurm_ssh_runner.submit_detached.assert_not_called()



if __name__ == "__main__":
    unittest.main(verbosity=2)