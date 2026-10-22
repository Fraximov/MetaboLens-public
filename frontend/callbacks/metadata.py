import dash
from dash import Input, Output, State

from constants import METADATA_FORM_FIELDS
from callbacks.clicks import real_click
from services import metadata_service
from services.api_client import APIError
from ui.formatting import metabolomics_assay_type_label, metabolomics_assay_type_value, pretty, resolve_page_slug
from ui.metadata import metadata_defaults_for_profile


def _active_assay_subtype(selected_assay_id, assays_data):
    selected_assay = next(
        (
            assay
            for assay in (assays_data or [])
            if str(assay.get("id")) == str(selected_assay_id)
        ),
        None,
    )
    return metabolomics_assay_type_value(selected_assay)


def _visible_style(is_visible: bool) -> dict:
    return {} if is_visible else {"display": "none"}


def register_callbacks(app):
    @app.callback(
        Output("metadata-profiles-store", "data"),
        Input("url", "pathname"),
        Input("token-store", "data"),
    )
    def load_metadata_profiles(pathname, token):
        normalized, _ = resolve_page_slug(pathname)
        if normalized != "studies" or not token:
            return dash.no_update
        try:
            return metadata_service.list_metadata_profiles(token)
        except APIError:
            return []

    @app.callback(
        Output("metadata-profile", "options"),
        Output("metadata-profile", "value"),
        Output("metadata-profile-help", "children"),
        Input("url", "pathname"),
        Input("metadata-profiles-store", "data"),
        Input("assay-select-store", "data"),
        Input("assays-store", "data"),
        State("metadata-profile", "value"),
    )
    def hydrate_metadata_profile_selector(pathname, profiles, selected_assay_id, assays_data, current_profile_id):
        normalized, _ = resolve_page_slug(pathname)
        if normalized != "studies":
            return dash.no_update, dash.no_update, dash.no_update

        assay_subtype = _active_assay_subtype(selected_assay_id, assays_data)
        profile_list = profiles or []
        if assay_subtype:
            profile_list = [
                profile for profile in profile_list
                if str(profile.get("metabolomics_assay_type") or "").strip().upper() == assay_subtype
            ]

        options = [{"label": profile.get("label"), "value": profile.get("id")} for profile in profile_list]
        if not selected_assay_id:
            return [], None, "Select an assay to load metadata profiles."
        if not profile_list:
            return [], None, "No metadata profiles are available for the active assay subtype."

        selected_profile = next((profile for profile in profile_list if profile.get("id") == current_profile_id), profile_list[0])
        help_text = selected_profile.get("description", "")
        return options, selected_profile.get("id"), help_text

    @app.callback(
        Output("metadata-assay-subtype-help", "children"),
        Output("metadata-ms-card", "style"),
        Output("metadata-chromatography-card", "style"),
        Output("metadata-acquisition-card", "style"),
        Output("metadata-nmr-card", "style"),
        Output("metadata-lcms-only-fields", "style"),
        Output("metadata-gcms-only-fields", "style"),
        Input("url", "pathname"),
        Input("assay-select-store", "data"),
        Input("assays-store", "data"),
    )
    def update_metadata_form_visibility(pathname, selected_assay_id, assays_data):
        normalized, _ = resolve_page_slug(pathname)
        if normalized != "studies":
            no_update = dash.no_update
            return no_update, no_update, no_update, no_update, no_update, no_update, no_update

        assay_subtype = _active_assay_subtype(selected_assay_id, assays_data)
        if not assay_subtype:
            helper = "Active assay subtype: not selected"
        else:
            helper = f"Active assay subtype: {metabolomics_assay_type_label(assay_subtype)}"

        show_ms = assay_subtype in {"LC_MS", "GC_MS"}
        show_chrom = assay_subtype in {"LC_MS", "GC_MS"}
        show_acquisition = assay_subtype in {"LC_MS", "GC_MS"}
        show_nmr = assay_subtype == "NMR"
        show_lcms_only = assay_subtype == "LC_MS"
        show_gcms_only = assay_subtype == "GC_MS"
        return (
            helper,
            _visible_style(show_ms),
            _visible_style(show_chrom),
            _visible_style(show_acquisition),
            _visible_style(show_nmr),
            _visible_style(show_lcms_only),
            _visible_style(show_gcms_only),
        )

    @app.callback(
        Output("metadata-platform-name", "value"),
        Output("metadata-ms-vendor", "value"),
        Output("metadata-ms-model", "value"),
        Output("metadata-ms-analyzer-type", "value"),
        Output("metadata-ms-ion-source", "value"),
        Output("metadata-ms-polarity-mode", "value"),
        Output("metadata-internal-reference", "value"),
        Output("metadata-lc-vendor", "value"),
        Output("metadata-lc-system", "value"),
        Output("metadata-chromatography-type", "value"),
        Output("metadata-separation-mode", "value"),
        Output("metadata-column-name", "value"),
        Output("metadata-column-type", "value"),
        Output("metadata-column-guard-type", "value"),
        Output("metadata-column-chemistry", "value"),
        Output("metadata-derivatization", "value"),
        Output("metadata-mobile-phase-a", "value"),
        Output("metadata-mobile-phase-b", "value"),
        Output("metadata-gradient-program", "value"),
        Output("metadata-flow-rate", "value"),
        Output("metadata-column-temperature", "value"),
        Output("metadata-autosampler-temperature", "value"),
        Output("metadata-injection-volume", "value"),
        Output("metadata-acquisition-mode", "value"),
        Output("metadata-mz-range", "value"),
        Output("metadata-resolution", "value"),
        Output("metadata-spray-voltage-positive", "value"),
        Output("metadata-spray-voltage-negative", "value"),
        Output("metadata-vaporizer-temperature", "value"),
        Output("metadata-transfer-tube-temperature", "value"),
        Output("metadata-sheath-gas", "value"),
        Output("metadata-aux-gas", "value"),
        Output("metadata-sweep-gas", "value"),
        Output("metadata-rf-lens", "value"),
        Output("metadata-operator-name", "value"),
        Output("metadata-acquisition-software", "value"),
        Output("metadata-batch-id", "value"),
        Output("metadata-sample-type", "value"),
        Output("metadata-notes", "value"),
        Output("metadata-nmr-tube-type", "value"),
        Output("metadata-solvent", "value"),
        Output("metadata-sample-ph", "value"),
        Output("metadata-temperature", "value"),
        Output("metadata-nmr-probe", "value"),
        Output("metadata-number-of-transients", "value"),
        Output("metadata-pulse-sequence-name", "value"),
        Output("metadata-magnetic-field-strength", "value"),
        Input("url", "pathname"),
        Input("metadata-profile", "value"),
        Input("metadata-profiles-store", "data"),
    )
    def apply_metadata_profile_defaults(pathname, profile_id, profiles):
        normalized, _ = resolve_page_slug(pathname)
        if normalized != "studies":
            return [dash.no_update] * len(METADATA_FORM_FIELDS)
        if not profile_id:
            return [dash.no_update] * len(METADATA_FORM_FIELDS)
        selected_profile = next((profile for profile in (profiles or []) if profile.get("id") == profile_id), None)
        if selected_profile is None:
            # Keep user-entered values while profile definitions are still loading.
            return [dash.no_update] * len(METADATA_FORM_FIELDS)
        return metadata_defaults_for_profile(selected_profile)

    @app.callback(
        Output("metadata-status", "children"),
        Output("page-data-mutation-signal", "data", allow_duplicate=True),
        Input("metadata-submit-btn", "n_clicks"),
        Input("metadata-get-btn", "n_clicks"),
        State("token-store", "data"),
        State("study-select-store", "data"),
        State("assay-select-store", "data"),
        State("metadata-profile", "value"),
        State("metadata-platform-name", "value"),
        State("metadata-ms-vendor", "value"),
        State("metadata-ms-model", "value"),
        State("metadata-ms-analyzer-type", "value"),
        State("metadata-ms-ion-source", "value"),
        State("metadata-ms-polarity-mode", "value"),
        State("metadata-internal-reference", "value"),
        State("metadata-lc-vendor", "value"),
        State("metadata-lc-system", "value"),
        State("metadata-chromatography-type", "value"),
        State("metadata-separation-mode", "value"),
        State("metadata-column-name", "value"),
        State("metadata-column-type", "value"),
        State("metadata-column-guard-type", "value"),
        State("metadata-column-chemistry", "value"),
        State("metadata-derivatization", "value"),
        State("metadata-mobile-phase-a", "value"),
        State("metadata-mobile-phase-b", "value"),
        State("metadata-gradient-program", "value"),
        State("metadata-flow-rate", "value"),
        State("metadata-column-temperature", "value"),
        State("metadata-autosampler-temperature", "value"),
        State("metadata-injection-volume", "value"),
        State("metadata-acquisition-mode", "value"),
        State("metadata-mz-range", "value"),
        State("metadata-resolution", "value"),
        State("metadata-spray-voltage-positive", "value"),
        State("metadata-spray-voltage-negative", "value"),
        State("metadata-vaporizer-temperature", "value"),
        State("metadata-transfer-tube-temperature", "value"),
        State("metadata-sheath-gas", "value"),
        State("metadata-aux-gas", "value"),
        State("metadata-sweep-gas", "value"),
        State("metadata-rf-lens", "value"),
        State("metadata-operator-name", "value"),
        State("metadata-acquisition-software", "value"),
        State("metadata-batch-id", "value"),
        State("metadata-sample-type", "value"),
        State("metadata-notes", "value"),
        State("metadata-nmr-tube-type", "value"),
        State("metadata-solvent", "value"),
        State("metadata-sample-ph", "value"),
        State("metadata-temperature", "value"),
        State("metadata-nmr-probe", "value"),
        State("metadata-number-of-transients", "value"),
        State("metadata-pulse-sequence-name", "value"),
        State("metadata-magnetic-field-strength", "value"),
        State("page-data-mutation-signal", "data"),
        prevent_initial_call=True,
    )
    def metadata_action(
        _, __, token, study_id, assay_id, profile_id, platform_name, ms_vendor, ms_model,
        ms_analyzer_type, ms_ion_source, ms_polarity_mode, internal_reference, lc_vendor,
        lc_system, chromatography_type, separation_mode, column_name, column_type,
        column_guard_type, column_chemistry, derivatization, mobile_phase_a, mobile_phase_b,
        gradient_program, flow_rate_ml_min, column_temperature_c, autosampler_temperature_c,
        injection_volume_ul, acquisition_mode, mz_range, resolution, spray_voltage_positive_kv,
        spray_voltage_negative_kv, vaporizer_temperature_c, transfer_tube_temperature_c,
        sheath_gas, aux_gas, sweep_gas, rf_lens, operator_name, acquisition_software,
        batch_id, sample_type, notes, nmr_tube_type, solvent, sample_ph, temperature,
        nmr_probe, number_of_transients, pulse_sequence_name, magnetic_field_strength, mutation_signal,
    ):
        if not real_click():
            return dash.no_update, dash.no_update
        if not token or not study_id or not assay_id:
            return "Login and select a study and assay", dash.no_update
        try:
            if dash.ctx.triggered_id == "metadata-submit-btn":
                form_values = {
                    "platform_name": platform_name,
                    "ms_vendor": ms_vendor,
                    "ms_model": ms_model,
                    "ms_analyzer_type": ms_analyzer_type,
                    "ms_ion_source": ms_ion_source,
                    "ms_polarity_mode": ms_polarity_mode,
                    "internal_reference": internal_reference,
                    "lc_vendor": lc_vendor,
                    "lc_system": lc_system,
                    "chromatography_type": chromatography_type,
                    "separation_mode": separation_mode,
                    "column_name": column_name,
                    "column_type": column_type,
                    "column_guard_type": column_guard_type,
                    "column_chemistry": column_chemistry,
                    "derivatization": derivatization,
                    "mobile_phase_a": mobile_phase_a,
                    "mobile_phase_b": mobile_phase_b,
                    "gradient_program": gradient_program,
                    "flow_rate_ml_min": flow_rate_ml_min,
                    "column_temperature_c": column_temperature_c,
                    "autosampler_temperature_c": autosampler_temperature_c,
                    "injection_volume_ul": injection_volume_ul,
                    "acquisition_mode": acquisition_mode,
                    "mz_range": mz_range,
                    "resolution": resolution,
                    "spray_voltage_positive_kv": spray_voltage_positive_kv,
                    "spray_voltage_negative_kv": spray_voltage_negative_kv,
                    "vaporizer_temperature_c": vaporizer_temperature_c,
                    "transfer_tube_temperature_c": transfer_tube_temperature_c,
                    "sheath_gas": sheath_gas,
                    "aux_gas": aux_gas,
                    "sweep_gas": sweep_gas,
                    "rf_lens": rf_lens,
                    "operator_name": operator_name,
                    "acquisition_software": acquisition_software,
                    "batch_id": batch_id,
                    "sample_type": sample_type,
                    "notes": notes,
                    "nmr_tube_type": nmr_tube_type,
                    "solvent": solvent,
                    "sample_ph": sample_ph,
                    "temperature": temperature,
                    "nmr_probe": nmr_probe,
                    "number_of_transients": number_of_transients,
                    "pulse_sequence_name": pulse_sequence_name,
                    "magnetic_field_strength": magnetic_field_strength,
                }
                res = metadata_service.submit_metadata(token, assay_id, profile_id, form_values)
            else:
                res = metadata_service.get_metadata(token, assay_id)
            mutation_update = int(mutation_signal or 0) + 1 if dash.ctx.triggered_id == "metadata-submit-btn" else dash.no_update
            return pretty(res), mutation_update
        except (APIError, ValueError) as exc:
            return f"Metadata action failed: {exc}", dash.no_update
