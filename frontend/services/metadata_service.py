from services.api_client import call_api

def list_metadata_profiles(token: str) -> list[dict]:
    return call_api("GET", "/metadata/profiles", token=token)


def build_metadata_payload(profile_id: str | None, form_values: dict) -> dict:
    if not profile_id:
        raise ValueError("Select a metadata profile")
    return {
        "profile_id": profile_id,
        "profile_version": "v1",
        "instrument": {
            "platform_name": form_values.get("platform_name"),
            "ms_vendor": form_values.get("ms_vendor"),
            "ms_model": form_values.get("ms_model"),
            "ms_analyzer_type": form_values.get("ms_analyzer_type"),
            "ms_ion_source": form_values.get("ms_ion_source"),
            "ms_polarity_mode": form_values.get("ms_polarity_mode"),
            "internal_reference": form_values.get("internal_reference"),
        },
        "chromatography": {
            "lc_vendor": form_values.get("lc_vendor"),
            "lc_system": form_values.get("lc_system"),
            "chromatography_type": form_values.get("chromatography_type"),
            "separation_mode": form_values.get("separation_mode"),
            "column_name": form_values.get("column_name"),
            "column_type": form_values.get("column_type"),
            "column_guard_type": form_values.get("column_guard_type"),
            "column_chemistry": form_values.get("column_chemistry"),
            "derivatization": form_values.get("derivatization"),
            "mobile_phase_a": form_values.get("mobile_phase_a"),
            "mobile_phase_b": form_values.get("mobile_phase_b"),
            "gradient_program": form_values.get("gradient_program"),
            "flow_rate_ml_min": form_values.get("flow_rate_ml_min"),
            "column_temperature_c": form_values.get("column_temperature_c"),
            "autosampler_temperature_c": form_values.get("autosampler_temperature_c"),
            "injection_volume_ul": form_values.get("injection_volume_ul"),
        },
        "acquisition": {
            "acquisition_mode": form_values.get("acquisition_mode"),
            "mz_range": form_values.get("mz_range"),
            "resolution": form_values.get("resolution"),
            "spray_voltage_positive_kv": form_values.get("spray_voltage_positive_kv"),
            "spray_voltage_negative_kv": form_values.get("spray_voltage_negative_kv"),
            "vaporizer_temperature_c": form_values.get("vaporizer_temperature_c"),
            "transfer_tube_temperature_c": form_values.get("transfer_tube_temperature_c"),
            "sheath_gas": form_values.get("sheath_gas"),
            "aux_gas": form_values.get("aux_gas"),
            "sweep_gas": form_values.get("sweep_gas"),
            "rf_lens": form_values.get("rf_lens"),
        },
        "run_context": {
            "operator_name": form_values.get("operator_name"),
            "acquisition_software": form_values.get("acquisition_software"),
            "batch_id": form_values.get("batch_id"),
            "sample_type": form_values.get("sample_type"),
            "notes": form_values.get("notes"),
        },
        "nmr": {
            "nmr_tube_type": form_values.get("nmr_tube_type"),
            "solvent": form_values.get("solvent"),
            "sample_ph": form_values.get("sample_ph"),
            "temperature": form_values.get("temperature"),
            "nmr_probe": form_values.get("nmr_probe"),
            "number_of_transients": form_values.get("number_of_transients"),
            "pulse_sequence_name": form_values.get("pulse_sequence_name"),
            "magnetic_field_strength": form_values.get("magnetic_field_strength"),
        },
    }


def submit_metadata(token: str, assay_id: int, profile_id: str | None, form_values: dict) -> dict:
    payload = build_metadata_payload(profile_id, form_values)
    return call_api("POST", f"/assays/{assay_id}/metadata", token=token, payload={"payload": payload})


def get_metadata(token: str, assay_id: int) -> dict:
    return call_api("GET", f"/assays/{assay_id}/metadata", token=token)
