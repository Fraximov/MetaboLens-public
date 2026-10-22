import hashlib
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from app.config import get_settings
from app.core.errors import not_found


class QCService:
    def __init__(self):
        self.settings = get_settings()

    @staticmethod
    def _local(tag: str) -> str:
        if "}" in tag:
            return tag.split("}", 1)[1]
        return tag

    def _resolve_example_path(self) -> Path:
        candidates = [
            Path(self.settings.example_mzml_path),
            Path("examples/data/Metabolomics_1.mzML"),
            Path("/app/examples/data/Metabolomics_1.mzML"),
        ]
        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                return candidate
        raise not_found("Example mzML file not found. Run scripts/fetch_example_mzml.sh first.")

    @staticmethod
    def _sha256(path: Path) -> str:
        hasher = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def analyze_example_mzml(self) -> dict:
        mzml_path = self._resolve_example_path()

        spectra_count = 0
        chromatogram_count = 0
        ms_levels: dict[str, int] = {}

        rt_unit = None
        rt_min = None
        rt_max = None

        tic_values: list[float] = []

        started = time.perf_counter()

        for _, elem in ET.iterparse(mzml_path, events=("end",)):
            tag = self._local(elem.tag)

            if tag == "spectrum":
                spectra_count += 1
                spectrum_ms_level = "unknown"

                for child in elem.iter():
                    if self._local(child.tag) != "cvParam":
                        continue

                    accession = child.attrib.get("accession")
                    value = child.attrib.get("value")
                    if not value:
                        continue

                    if accession == "MS:1000511":
                        spectrum_ms_level = value

                    elif accession == "MS:1000016":
                        try:
                            rt = float(value)
                            if rt_min is None or rt < rt_min:
                                rt_min = rt
                            if rt_max is None or rt > rt_max:
                                rt_max = rt
                            if rt_unit is None:
                                rt_unit = child.attrib.get("unitName") or child.attrib.get("unitAccession")
                        except ValueError:
                            pass

                    elif accession == "MS:1000285":
                        try:
                            tic_values.append(float(value))
                        except ValueError:
                            pass

                ms_levels[spectrum_ms_level] = ms_levels.get(spectrum_ms_level, 0) + 1
                elem.clear()

            elif tag == "chromatogram":
                chromatogram_count += 1
                elem.clear()

        duration = round(time.perf_counter() - started, 4)
        tic_min = min(tic_values) if tic_values else None
        tic_max = max(tic_values) if tic_values else None
        tic_mean = round(sum(tic_values) / len(tic_values), 6) if tic_values else None

        return {
            "source_path": str(mzml_path.resolve()),
            "file_name": mzml_path.name,
            "file_size_bytes": mzml_path.stat().st_size,
            "sha256": self._sha256(mzml_path),
            "spectra_count": spectra_count,
            "chromatogram_count": chromatogram_count,
            "ms_level_counts": ms_levels,
            "retention_time_unit": rt_unit,
            "retention_time_min": rt_min,
            "retention_time_max": rt_max,
            "total_ion_current_mean": tic_mean,
            "total_ion_current_min": tic_min,
            "total_ion_current_max": tic_max,
            "parse_duration_seconds": duration,
        }
