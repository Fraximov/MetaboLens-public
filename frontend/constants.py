SAMPLE_TYPE_OPTIONS = [
    {"label": "Sample", "value": "sample"},
    {"label": "QC", "value": "qc"},
    {"label": "Blank", "value": "blank"},
    {"label": "Other", "value": "other"},
    {"label": "Standard", "value": "standard"},
]

SAMPLE_TYPE_LABELS = {option["value"]: option["label"] for option in SAMPLE_TYPE_OPTIONS}

SAMPLE_TYPE_COLOR_MAP = {
    "sample": "#A5D022",
    "blank": "#E0E0E0",
    "qc": "#006F70",
    "other": "#B0B0B0",
    "standard": "#F5A623",
}

DEFAULT_WORKFLOW_EXECUTION_TARGET = "local_docker"
DEFAULT_WORKFLOW_ANALYSIS_MODE = "full"

WORKFLOW_EXECUTION_TARGETS = {
    "local_docker": {
        "label": "Local Docker",
        "execution_profile": "local_docker",
        "nextflow_profile": "docker",
        "runtime_label": "Docker",
    },
    "slurm_singularity_ssh": {
        "label": "HPC SLURM via SSH",
        "execution_profile": "slurm_singularity_ssh",
        "nextflow_profile": "embl_slurm",
        "runtime_label": "Singularity / SLURM",
    },
}

WORKFLOW_EXECUTION_TARGET_OPTIONS = [
    {"label": target["label"], "value": value}
    for value, target in WORKFLOW_EXECUTION_TARGETS.items()
]

METADATA_FORM_FIELDS = [
    "platform_name",
    "ms_vendor",
    "ms_model",
    "ms_analyzer_type",
    "ms_ion_source",
    "ms_polarity_mode",
    "internal_reference",
    "lc_vendor",
    "lc_system",
    "chromatography_type",
    "separation_mode",
    "column_name",
    "column_type",
    "column_guard_type",
    "column_chemistry",
    "derivatization",
    "mobile_phase_a",
    "mobile_phase_b",
    "gradient_program",
    "flow_rate_ml_min",
    "column_temperature_c",
    "autosampler_temperature_c",
    "injection_volume_ul",
    "acquisition_mode",
    "mz_range",
    "resolution",
    "spray_voltage_positive_kv",
    "spray_voltage_negative_kv",
    "vaporizer_temperature_c",
    "transfer_tube_temperature_c",
    "sheath_gas",
    "aux_gas",
    "sweep_gas",
    "rf_lens",
    "operator_name",
    "acquisition_software",
    "batch_id",
    "sample_type",
    "notes",
    "nmr_tube_type",
    "solvent",
    "sample_ph",
    "temperature",
    "nmr_probe",
    "number_of_transients",
    "pulse_sequence_name",
    "magnetic_field_strength",
]

STUDY_STATUS_LABELS = {
    "draft": "Draft",
    "submitted": "Submitted",
    "under_review": "Under Review",
    "approved": "Approved",
    "processing": "Processing",
    "completed": "Completed",
    "archived": "Archived",
}

STUDY_STATUS_ORDER = [
    "draft",
    "submitted",
    "under_review",
    "approved",
    "processing",
    "completed",
    "archived",
]

STATE_COLOR_MAP = {
    "draft": "#707372",
    "completed": "#18974C",
    "approved": "#18974C",
    "succeeded": "#18974C",
    "processing": "#193F90",
    "running": "#193F90",
    "failed": "#B42318",
}
