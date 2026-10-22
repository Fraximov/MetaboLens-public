from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    database_url: str = "sqlite:///./metabolens.db"
    worker_poll_interval_seconds: int = 5
    study_package_root: str = "/tmp/metabolens_packages"
    # Self-contained package copies; Compose puts it where the host can open it.
    study_package_export_root: str = "/tmp/metabolens_exports"
    metabolights_python: str = "/opt/metabolights/bin/python"
    metabolights_opa: str = "/opt/metabolights/bin/opa"
    nextflow_mode: str = "mock"
    workflow_execution_profile: str = "local_docker"
    workflow_pipeline_root: str = "./workflows/nextflow4ms-dial-msdial5-clean"
    workflow_runs_root: str = "/tmp/metabolens_workflow_runs"
    workflow_local_runs_root: str = ""
    workflow_nextflow_bin: str = "nextflow"
    workflow_nextflow_default_profile: str = "docker"
    workflow_nextflow_poll_seconds: int = 2
    workflow_status_update_seconds: int = 15
    workflow_nextflow_timeout_seconds: int = 0
    workflow_local_max_memory_gb: int = 6
    workflow_local_peak_memory_gb: int = 6
    workflow_local_prepare_memory_gb: int = 4
    workflow_local_default_cpus: int = 1
    workflow_local_peak_cpus: int = 1
    workflow_slurm_sbatch_bin: str = "sbatch"
    workflow_slurm_nextflow_module: str = "Nextflow"
    workflow_slurm_partition: str = ""
    workflow_slurm_account: str = ""
    workflow_slurm_driver_cpus: int = 1
    workflow_slurm_driver_memory: str = "2G"
    workflow_slurm_driver_time: str = "04:00:00"
    workflow_slurm_max_active_jobs: int = 1
    workflow_slurm_work_root: str = ""
    workflow_slurm_container_cache_dir: str = ""
    workflow_slurm_container_tmp_dir: str = ""
    workflow_slurm_ssh_bin: str = "ssh"
    workflow_slurm_ssh_host: str = ""
    workflow_slurm_ssh_user: str = ""
    workflow_slurm_ssh_command_timeout_seconds: int = 30
    workflow_slurm_ssh_options: str = ""
    workflow_slurm_local_mount_root: str = ""
    workflow_slurm_remote_mount_root: str = ""
    workflow_slurm_smoke_container_image: str = ""
    workflow_singularity_image_manifest: str = ""
    workflow_msdial5_container_image: str = ""
    workflow_thermorawfileparser_image: str = ""
    workflow_stats_adapter_container_image: str = ""
    workflow_stats_pipeline_v2_container_image: str = ""
    workflow_is_qc_container_image: str = ""
    workflow_sirius_container_image: str = ""
    workflow_sirius_container_options: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> WorkerSettings:
    return WorkerSettings()
