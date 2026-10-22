from pydantic import BaseModel


class MzMLQCReport(BaseModel):
    source_path: str
    file_name: str
    file_size_bytes: int
    sha256: str
    spectra_count: int
    chromatogram_count: int
    ms_level_counts: dict[str, int]
    retention_time_unit: str | None = None
    retention_time_min: float | None = None
    retention_time_max: float | None = None
    total_ion_current_mean: float | None = None
    total_ion_current_min: float | None = None
    total_ion_current_max: float | None = None
    parse_duration_seconds: float
