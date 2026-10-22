"""Create and optionally submit a tiny SLURM/Singularity runner smoke job.

This tool is for operator/developer testing of the worker profile runner. It
does not use the database and it does not launch the real MS-DIAL workflow. It
uses the same smoke materializer as the app-triggered diagnostic job, then asks
the SLURM runner to generate the same `run_nextflow.sbatch` script that a real
worker job would use.
"""

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for import_root in (REPO_ROOT / "worker", REPO_ROOT / "backend"):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from config import WorkerSettings
from launchers.profile_runners.slurm_singularity import SlurmSingularityProfileRunner
from launchers.profile_runners.slurm_singularity_ssh import SlurmSingularitySshProfileRunner
from services.hpc_smoke_materializer import prepare_smoke_run


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render or submit a tiny Nextflow job through the slurm_singularity runner.",
    )
    parser.add_argument("--run-root", required=True, help="Cluster-visible run folder to create.")
    parser.add_argument("--nextflow-profile", default="embl_slurm", help="Nextflow profile name embedded in nextflow.config.")
    parser.add_argument("--container", default="", help="Optional Singularity/Apptainer image path for the tiny Python process.")
    parser.add_argument("--submit", action="store_true", help="Submit with sbatch --wait after rendering the script.")
    parser.add_argument("--sbatch-bin", default="sbatch", help="Path/name of sbatch.")
    parser.add_argument("--nextflow-bin", default="nextflow", help="Path/name of nextflow inside the batch job.")
    parser.add_argument("--nextflow-module", default="Nextflow", help="Module loaded in the batch job; use '' to skip.")
    parser.add_argument("--partition", default="", help="Optional SLURM partition for the Nextflow driver job.")
    parser.add_argument("--account", default="", help="Optional SLURM account for the Nextflow driver job.")
    parser.add_argument("--driver-cpus", type=int, default=1, help="CPUs for the small Nextflow driver job.")
    parser.add_argument("--driver-memory", default="2G", help="Memory for the small Nextflow driver job.")
    parser.add_argument("--driver-time", default="00:30:00", help="Wall time for the small Nextflow driver job.")
    parser.add_argument("--work-root", default="", help="Optional scratch root; runner will use <work-root>/job_9001/work.")
    parser.add_argument("--cache-dir", default="", help="Optional APPTAINER/SINGULARITY cache directory.")
    parser.add_argument("--tmp-dir", default="", help="Optional APPTAINER/SINGULARITY temp directory.")
    parser.add_argument("--ssh-host", default="", help="Optional login host for SSH submission from outside the cluster.")
    parser.add_argument("--ssh-user", default="", help="Optional SSH username.")
    parser.add_argument("--ssh-options", default="", help="Optional SSH options, for example '-o BatchMode=yes'.")
    parser.add_argument("--local-mount-root", default="", help="Local group-share mount prefix, for example /Volumes/example-lab.")
    parser.add_argument("--remote-mount-root", default="", help="Cluster group-share prefix, for example /g/example-lab.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_root = Path(args.run_root).expanduser().resolve()
    run = prepare_smoke_run(
        run_root=run_root,
        job_id=9001,
        study_id=1,
        assay_id=1,
        workflow_definition_id=1,
        execution_profile="slurm_singularity_ssh" if args.ssh_host else "slurm_singularity",
        nextflow_profile=args.nextflow_profile,
        container_image=args.container,
        run_name="smoke_slurm_singularity_job9001",
        config_source="smoke_tool",
    )
    settings = WorkerSettings(
        database_url="sqlite://",
        workflow_nextflow_bin=args.nextflow_bin,
        workflow_slurm_sbatch_bin=args.sbatch_bin,
        workflow_slurm_nextflow_module=args.nextflow_module,
        workflow_slurm_partition=args.partition,
        workflow_slurm_account=args.account,
        workflow_slurm_driver_cpus=args.driver_cpus,
        workflow_slurm_driver_memory=args.driver_memory,
        workflow_slurm_driver_time=args.driver_time,
        workflow_slurm_work_root=args.work_root,
        workflow_slurm_container_cache_dir=args.cache_dir,
        workflow_slurm_container_tmp_dir=args.tmp_dir,
        workflow_slurm_ssh_host=args.ssh_host,
        workflow_slurm_ssh_user=args.ssh_user,
        workflow_slurm_ssh_options=args.ssh_options,
        workflow_slurm_local_mount_root=args.local_mount_root,
        workflow_slurm_remote_mount_root=args.remote_mount_root,
    )
    runner = SlurmSingularitySshProfileRunner(settings) if args.ssh_host else SlurmSingularityProfileRunner(settings)
    if isinstance(runner, SlurmSingularitySshProfileRunner):
        remote_run = runner.remote_run_for(run)
        script_path = runner.write_sbatch_script(run, remote_run)
        submit_command = runner.build_command(remote_run, script_path)
        display_run = remote_run
    else:
        script_path = runner.write_sbatch_script(run)
        submit_command = runner.build_command(script_path)
        display_run = run
    print(f"Generated: {script_path}")
    print(f"Run root:  {display_run.run_root}")
    print(f"Results:   {display_run.results_path}")
    print(f"Work dir:  {runner.work_dir_for(display_run)}")
    print()
    print("Submit command:")
    print(shlex.join(submit_command))
    if not args.submit:
        print()
        print("Dry run only. Add --submit to launch the generated script.")
        return 0

    handle = runner.submit(run)
    exit_code = handle.process.wait()
    handle.execution_log_handle.close()
    print()
    print(f"sbatch wrapper exit code: {exit_code}")
    print(f"Worker execution log: {handle.execution_log_path}")
    return int(exit_code or 0)


if __name__ == "__main__":
    raise SystemExit(main())
