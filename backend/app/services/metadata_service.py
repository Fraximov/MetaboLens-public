from copy import deepcopy

from sqlalchemy.orm import Session

from app.core.errors import bad_request, not_found
from app.models.models import Assay, GCMSAssayDetails, LCMSAssayDetails, MetadataSubmission, MetabolomicsAssay, NMRAssayDetails
from app.services.audit_service import AuditService
from app.services.study_service import StudyService
from app.services.metadata_profiles import get_metadata_profile, list_metadata_profiles


class MetadataService:
    def __init__(self, db: Session):
        self.db = db
        self.studies = StudyService(db)
        self.audit = AuditService(db)

    def list_profiles(self, user_id: int) -> list[dict]:
        if not user_id:
            raise bad_request("Authenticated user is required")
        return list_metadata_profiles()

    def _normalize_value(self, value):
        if isinstance(value, dict):
            return {str(key).strip().lower(): self._normalize_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._normalize_value(item) for item in value]
        if isinstance(value, str):
            return value.strip()
        return value

    def _merge_payload(self, base: dict, override: dict) -> dict:
        merged = deepcopy(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = self._merge_payload(merged[key], value)
            else:
                merged[key] = value
        return merged

    def _get_by_path(self, payload: dict, path: str):
        current = payload
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
        return current

    def _normalize(self, payload: dict) -> tuple[dict, str, list]:
        if not isinstance(payload, dict):
            return {}, "invalid", ["Payload must be a JSON object"]

        normalized = self._normalize_value(payload)
        profile_id = normalized.get("profile_id")
        profile = get_metadata_profile(profile_id)
        if profile is None:
            return normalized, "invalid", [f"Unknown metadata profile '{profile_id}'"]

        merged = self._merge_payload(profile["defaults"], normalized)
        messages: list[str] = []
        for path in profile["required_field_paths"]:
            value = self._get_by_path(merged, path)
            if value is None or (isinstance(value, str) and not value.strip()):
                messages.append(f"Missing {path}")

        expected_polarity = self._get_by_path(profile["defaults"], "instrument.ms_polarity_mode")
        actual_polarity = self._get_by_path(merged, "instrument.ms_polarity_mode")
        if expected_polarity and actual_polarity != expected_polarity:
            messages.append(
                "Instrument polarity mode does not match selected profile "
                f"({expected_polarity})"
            )

        status = "valid" if not messages else "invalid"
        return merged, status, messages

    @staticmethod
    def _ensure_lcms_details(metabolomics_assay: MetabolomicsAssay) -> LCMSAssayDetails:
        if metabolomics_assay.lcms_details is None:
            metabolomics_assay.lcms_details = LCMSAssayDetails()
        return metabolomics_assay.lcms_details

    @staticmethod
    def _ensure_gcms_details(metabolomics_assay: MetabolomicsAssay) -> GCMSAssayDetails:
        if metabolomics_assay.gcms_details is None:
            metabolomics_assay.gcms_details = GCMSAssayDetails()
        return metabolomics_assay.gcms_details

    @staticmethod
    def _ensure_nmr_details(metabolomics_assay: MetabolomicsAssay) -> NMRAssayDetails:
        if metabolomics_assay.nmr_details is None:
            metabolomics_assay.nmr_details = NMRAssayDetails()
        return metabolomics_assay.nmr_details

    def _apply_shared_method_fields(self, metabolomics_assay: MetabolomicsAssay, normalized: dict) -> None:
        instrument = normalized.get("instrument", {})
        acquisition = normalized.get("acquisition", {})
        run_context = normalized.get("run_context", {})
        nmr = normalized.get("nmr", {})

        metabolomics_assay.instrument_model = instrument.get("ms_model")
        metabolomics_assay.instrument_vendor = instrument.get("ms_vendor")
        metabolomics_assay.acquisition_software = run_context.get("acquisition_software")

        if metabolomics_assay.assay_type == "GC_MS":
            metabolomics_assay.assay_technique = "gas_chromatography_mass_spectrometry"
            metabolomics_assay.measurement_type = "metabolite_profiling"
            metabolomics_assay.analysis_scope = acquisition.get("acquisition_mode")
            metabolomics_assay.analyte_domain = "metabolomics"
            metabolomics_assay.ms_type = acquisition.get("acquisition_mode")
        elif metabolomics_assay.assay_type == "NMR":
            metabolomics_assay.assay_technique = "nuclear_magnetic_resonance"
            metabolomics_assay.measurement_type = "metabolite_profiling"
            metabolomics_assay.analysis_scope = nmr.get("pulse_sequence_name") or acquisition.get("acquisition_mode")
            metabolomics_assay.analyte_domain = "metabolomics"
            metabolomics_assay.ms_type = None
        else:
            metabolomics_assay.assay_technique = "liquid_chromatography_mass_spectrometry"
            metabolomics_assay.measurement_type = "metabolite_profiling"
            metabolomics_assay.analysis_scope = acquisition.get("acquisition_mode")
            metabolomics_assay.analyte_domain = "metabolomics"
            metabolomics_assay.ms_type = acquisition.get("acquisition_mode")

    def _update_lcms_assay(self, metabolomics_assay: MetabolomicsAssay, normalized: dict) -> None:
        instrument = normalized.get("instrument", {})
        chromatography = normalized.get("chromatography", {})
        acquisition = normalized.get("acquisition", {})
        lcms_details = self._ensure_lcms_details(metabolomics_assay)

        self._apply_shared_method_fields(metabolomics_assay, normalized)
        lcms_details.polarity = instrument.get("ms_polarity_mode")
        lcms_details.column_type = chromatography.get("column_type") or chromatography.get("separation_mode") or chromatography.get("chromatography_type")
        lcms_details.column_guard_type = chromatography.get("column_guard_type")
        lcms_details.chromatography_instrument = chromatography.get("lc_system")
        lcms_details.column_model = chromatography.get("column_name")
        lcms_details.scan_mz_range = acquisition.get("mz_range")
        lcms_details.ion_source = instrument.get("ms_ion_source")
        lcms_details.mass_analyzer = instrument.get("ms_analyzer_type")

    def _update_gcms_assay(self, metabolomics_assay: MetabolomicsAssay, normalized: dict) -> None:
        instrument = normalized.get("instrument", {})
        chromatography = normalized.get("chromatography", {})
        acquisition = normalized.get("acquisition", {})
        gcms_details = self._ensure_gcms_details(metabolomics_assay)

        self._apply_shared_method_fields(metabolomics_assay, normalized)
        gcms_details.derivatization = chromatography.get("derivatization")
        gcms_details.chromatography_instrument = chromatography.get("lc_system")
        gcms_details.column_model = chromatography.get("column_name")
        gcms_details.column_type = chromatography.get("column_type") or chromatography.get("chromatography_type")
        gcms_details.column_guard_type = chromatography.get("column_guard_type")
        gcms_details.scan_mz_range = acquisition.get("mz_range")
        gcms_details.ion_source = instrument.get("ms_ion_source")
        gcms_details.mass_analyzer = instrument.get("ms_analyzer_type")

    def _update_nmr_assay(self, metabolomics_assay: MetabolomicsAssay, normalized: dict) -> None:
        nmr = normalized.get("nmr", {})
        nmr_details = self._ensure_nmr_details(metabolomics_assay)

        self._apply_shared_method_fields(metabolomics_assay, normalized)
        nmr_details.nmr_tube_type = nmr.get("nmr_tube_type")
        nmr_details.solvent = nmr.get("solvent")
        nmr_details.sample_ph = nmr.get("sample_ph")
        nmr_details.temperature = nmr.get("temperature")
        nmr_details.nmr_probe = nmr.get("nmr_probe")
        nmr_details.number_of_transients = nmr.get("number_of_transients")
        nmr_details.pulse_sequence_name = nmr.get("pulse_sequence_name")
        nmr_details.magnetic_field_strength = nmr.get("magnetic_field_strength")

    def _update_metabolomics_assay(self, metabolomics_assay: MetabolomicsAssay, normalized: dict) -> None:
        assay_type = str(normalized.get("metabolomics_assay_type") or metabolomics_assay.assay_type or "LC_MS").strip().upper()
        metabolomics_assay.assay_type = assay_type
        if assay_type == "GC_MS":
            self._update_gcms_assay(metabolomics_assay, normalized)
            return
        if assay_type == "NMR":
            self._update_nmr_assay(metabolomics_assay, normalized)
            return
        self._update_lcms_assay(metabolomics_assay, normalized)

    def submit(self, user_id: int, assay_id: int, payload: dict) -> MetadataSubmission:
        assay = self.db.query(Assay).filter(Assay.id == assay_id).first()
        if assay is None:
            raise not_found("Assay not found")

        study = self.studies.get(user_id, assay.study_id)
        self.studies.perms.require_edit_study(user_id, study.id, study.status)

        normalized, status, messages = self._normalize(payload)
        record = MetadataSubmission(
            study_id=study.id,
            assay_id=assay.id,
            submitted_by=user_id,
            raw_payload_json=payload,
            normalized_payload_json=normalized,
            validation_status=status,
            validation_messages_json=messages,
            schema_version=str(normalized.get("profile_version", "metabolens-v1")),
        )
        self.db.add(record)

        if assay.platform != "metabolomics":
            raise bad_request(f"Metadata submission is only supported for metabolomics assays (found {assay.platform})")

        profile_assay_type = str(normalized.get("metabolomics_assay_type") or "LC_MS").strip().upper()

        metabolomics_assay = assay.metabolomics_assay
        if metabolomics_assay is None:
            metabolomics_assay = MetabolomicsAssay(assay_id=assay.id, assay_type=profile_assay_type)
            assay.metabolomics_assay = metabolomics_assay
        elif metabolomics_assay.assay_type != profile_assay_type:
            raise bad_request(
                f"Selected metadata profile is for {profile_assay_type}, but the active assay is "
                f"{metabolomics_assay.assay_type}"
            )
        self._update_metabolomics_assay(metabolomics_assay, normalized)

        self.audit.log(
            user_id,
            "metadata.submit",
            "assay",
            str(assay.id),
            {
                "study_id": study.id,
                "assay_id": assay.id,
                "validation_status": status,
                "profile_id": normalized.get("profile_id"),
            },
        )
        self.db.commit()
        self.db.refresh(record)
        return record

    def latest(self, user_id: int, assay_id: int) -> dict | None:
        assay = self.studies.get_assay(user_id, assay_id)
        record = (
            self.db.query(MetadataSubmission)
            .filter(MetadataSubmission.assay_id == assay_id)
            .order_by(MetadataSubmission.id.desc())
            .first()
        )
        if record is None:
            return None
        return {
            "id": record.id,
            "study_id": record.study_id,
            "assay_id": record.assay_id,
            "submitted_by": record.submitted_by,
            "raw_payload_json": record.raw_payload_json,
            "normalized_payload_json": record.normalized_payload_json,
            "validation_status": record.validation_status,
            "validation_messages_json": record.validation_messages_json,
            "schema_version": record.schema_version,
            "submitted_at": record.submitted_at,
            "metabolomics_assay": assay.metabolomics_assay,
        }
