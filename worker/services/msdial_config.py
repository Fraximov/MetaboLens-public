from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MsdialConfigGeneration:
    ms1_library_filename: str
    ms2_library_filename: str
    alignment_reference_value: str
    alignment_reference_keys: list[str]
    patched_fields: dict[str, str]
    effective_sections: dict[str, dict[str, str]]


class MsdialConfigRenderer:
    DYNAMIC_LABELS = {
        "MSP file path": "ms2_library_filename",
        "MSP file": "ms2_library_filename",
        "Text DB file path": "ms1_library_filename",
        "Text file": "ms1_library_filename",
    }
    REQUIRED_DYNAMIC_FIELDS = {
        "ms1_library_filename": "Text DB file path or Text file",
        "ms2_library_filename": "MSP file path or MSP file",
    }
    REFERENCE_LABELS = ("Alignment reference file ID", "Alignment reference file name", "Reference file")
    SECTION_NAMES = {
        "Project": "project",
        "Data collection parameters": "data_collection",
        "Centroid parameters": "centroid",
        "Isotope recognition": "isotope_recognition",
        "Data processing": "data_processing",
        "Peak detection parameters": "peak_detection",
        "Peak spotting parameters": "peak_spotting",
        "Deconvolution parameters": "deconvolution",
        "MSP file and MS/MS identification setting": "msp_annotation",
        "Text DB file and post identification (retention time and accurate mass based) setting": "text_annotation",
        "Advanced setting for identification": "identification_advanced",
        "Adduct ion setting": "adducts",
        "Alignment parameters setting": "alignment",
        "Tracking of isotope labels": "isotope_tracking",
        "Ion mobility": "ion_mobility",
    }

    @classmethod
    def render(
        cls,
        *,
        template_path: Path,
        output_path: Path,
        ms1_library_path: Path,
        ms2_library_path: Path,
        reference_path: Path,
    ) -> MsdialConfigGeneration:
        reference_value = reference_path.stem
        replacement_values = {
            "MSP file path": ms2_library_path.name,
            "MSP file": ms2_library_path.name,
            "Text DB file path": ms1_library_path.name,
            "Text file": ms1_library_path.name,
            "Alignment reference file ID": reference_value,
            "Alignment reference file name": reference_value,
            "Reference file": cls._reference_file_value(reference_path),
        }
        rendered_text, patched_fields, alignment_reference_keys = cls._render_text(
            template_path.read_text(encoding="utf-8"), replacement_values,
        )
        effective_sections = cls._parse_effective_sections(rendered_text)
        output_path.write_text(
            rendered_text,
            encoding="utf-8",
        )
        return MsdialConfigGeneration(
            ms1_library_filename=ms1_library_path.name,
            ms2_library_filename=ms2_library_path.name,
            alignment_reference_value=reference_value,
            alignment_reference_keys=alignment_reference_keys,
            patched_fields=patched_fields,
            effective_sections=effective_sections,
        )

    @classmethod
    def _render_text(
        cls,
        template_text: str,
        replacement_values: dict[str, str],
    ) -> tuple[str, dict[str, str], list[str]]:
        """Return the rendered text, the fields it patched, and the reference labels it saw."""

        patched_fields: dict[str, str] = {}
        seen_dynamic: set[str] = set()
        seen_reference: list[str] = []
        rendered_lines: list[str] = []
        for line in template_text.splitlines(keepends=True):
            line_body = line.rstrip("\r\n")
            newline = line[len(line_body):]
            match = cls._matching_label(line_body)
            if match is None:
                rendered_lines.append(line)
                continue
            label, separator = match
            value = replacement_values[label]
            rendered_lines.append(f"{label}{separator}{value}{newline}")
            patched_fields[label] = value
            if label in cls.DYNAMIC_LABELS:
                seen_dynamic.add(cls.DYNAMIC_LABELS[label])
            elif label in cls.REFERENCE_LABELS:
                seen_reference.append(label)

        missing_dynamic = [label for field, label in cls.REQUIRED_DYNAMIC_FIELDS.items() if field not in seen_dynamic]
        if missing_dynamic:
            raise ValueError(f"MS-DIAL config template is missing required field(s): {', '.join(missing_dynamic)}")
        if not seen_reference:
            raise ValueError(
                "MS-DIAL config template is missing required field(s): Alignment reference file ID, Alignment reference file name, or Reference file"
            )
        return "".join(rendered_lines), patched_fields, seen_reference

    @classmethod
    def _matching_label(cls, line_body: str) -> tuple[str, str] | None:
        for label in (*cls.DYNAMIC_LABELS, *cls.REFERENCE_LABELS):
            if line_body.startswith(f"{label}:"):
                return label, ": "
            if line_body.startswith(f"{label}\t"):
                return label, "\t"
        return None

    @staticmethod
    def _reference_file_value(reference_path: Path) -> str:
        if reference_path.suffix.lower() == ".raw":
            return reference_path.with_suffix(".mzML").name
        return reference_path.name

    @classmethod
    def _parse_effective_sections(cls, rendered_text: str) -> dict[str, dict[str, str]]:
        sections: dict[str, dict[str, str]] = {}
        current_section = "preamble"
        sections[current_section] = {}
        for raw_line in rendered_text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("#"):
                section_label = line.lstrip("#").strip()
                current_section = cls.SECTION_NAMES.get(section_label, cls._normalize_section_name(section_label))
                sections.setdefault(current_section, {})
                continue
            if ":" in line:
                label, value = line.split(":", 1)
            elif "\t" in line:
                label, value = line.split("\t", 1)
            else:
                continue
            sections.setdefault(current_section, {})[label.strip()] = value.strip()
        return sections

    @staticmethod
    def _normalize_section_name(section_label: str) -> str:
        return (
            section_label.lower()
            .replace("&", "and")
            .replace("/", " ")
            .replace("-", " ")
            .replace("(", "")
            .replace(")", "")
            .replace(",", "")
            .replace("%", "percent")
            .replace("  ", " ")
            .strip()
            .replace(" ", "_")
        )
