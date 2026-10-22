import dash_bootstrap_components as dbc
from dash import dcc, html

from constants import DEFAULT_WORKFLOW_ANALYSIS_MODE, DEFAULT_WORKFLOW_EXECUTION_TARGET, WORKFLOW_EXECUTION_TARGET_OPTIONS
from ui.figures import empty_eic_figure, empty_feature_map_figure
from ui.workflow_studio import (
    command_preview_card,
    configuration_wizard_card,
    control_group,
    page_header,
    pipeline_overview_card,
    status_card_grid,
    validation_alert,
)


def _pipeline_tab() -> html.Div:
    return html.Div(
        [
            dbc.Row(
                [
                    dbc.Col(
                        control_group(
                            "Processing engine / workflow",
                            dcc.Dropdown(id="wf-id", options=[], value=None, placeholder="Select workflow"),
                            html_for="wf-id",
                        ),
                        lg=8,
                    ),
                    dbc.Col(
                        dbc.Button("Refresh catalog", id="wf-list-btn", n_clicks=0, color="light", className="nf-secondary-action w-100"),
                        lg=4,
                        className="nf-field-button-col",
                    ),
                ],
                className="g-3",
            ),
            html.Div(id="wf-definition-status", className="nf-inline-status"),
            html.Div([
                control_group(
                    "MetaboLensR profile",
                    dcc.Dropdown(id="wf-metabolensr-profile", options=[], value=None,
                                 clearable=False, disabled=True),
                    html_for="wf-metabolensr-profile",
                ),
                html.Div(id="wf-curation-rules"),
                dcc.Store(id="wf-curation-rule-context"),
                html.Div([
                    html.Div([
                        html.Span(id="wf-curation-selection-status", className="fw-semibold"),
                        dbc.Button("Reset to preset", id="wf-curation-reset", n_clicks=0,
                                   color="link", size="sm", disabled=True),
                    ], className="d-flex align-items-center justify-content-between mb-2"),
                    dbc.Row([
                        dbc.Col([
                            html.H6(label),
                            dbc.Checklist(id=f"wf-curation-{domain}", options=[], value=None, switch=True),
                            html.Div(id=f"wf-curation-{domain}-status", className="text-muted small"),
                        ], md=6) for domain, label in (("statistics", "Statistics"), ("annotation", "Annotation"))
                    ], className="g-3"),
                ], id="wf-curation-rule-controls", style={"display": "none"}),
            ], id="wf-curation-profile-section", className="nf-mini-card", style={"display": "none"}),
        ],
        id="workflow-pipeline",
        className="nf-tab-pane",
    )


def _inputs_tab() -> html.Div:
    return html.Div(
        [
            control_group(
                html.Span("Parameter template", id="wf-template-label"),
                dcc.Dropdown(id="wf-msdial-template", options=[], value=None, placeholder="Select parameter template"),
                html_for="wf-msdial-template",
            ),
            dbc.Row(
                [
                    dbc.Col(
                        control_group(
                            "MS1 library",
                            dcc.Dropdown(id="wf-ms1-library", options=[], value=None, placeholder="Select MS1 library"),
                            None,
                            "wf-ms1-library",
                        ),
                        lg=6,
                    ),
                    dbc.Col(
                        control_group(
                            "MS2 library",
                            dcc.Dropdown(id="wf-ms2-library", options=[], value=None, placeholder="Select MS2 library"),
                            None,
                            "wf-ms2-library",
                        ),
                        lg=6,
                    ),
                ],
                className="g-3",
            ),
            html.Div(id="wf-library-status", className="nf-inline-status"),
            control_group(
                "Reference file",
                html.Div([
                    dcc.Dropdown(id="wf-reference-file", options=[], value=None, placeholder="Select alignment reference file"),
                    html.Div(id="wf-reference-status", className="nf-inline-status"),
                ]),
                html_for="wf-reference-file",
            ),
            html.Div(id="wf-selected-files-status", className="nf-inline-status"),
        ],
        id="workflow-inputs",
        className="nf-tab-pane",
    )


def _compute_tab() -> html.Div:
    return html.Div(
        [
            dbc.Row(
                [
                    dbc.Col(
                        control_group(
                            "Execution target",
                            dcc.Dropdown(
                                id="wf-execution-target",
                                options=WORKFLOW_EXECUTION_TARGET_OPTIONS,
                                value=DEFAULT_WORKFLOW_EXECUTION_TARGET,
                                clearable=False,
                            ),
                            html_for="wf-execution-target",
                        ),
                        lg=4,
                    ),
                    dbc.Col(
                        control_group(
                            "Review pauses",
                            dbc.Checklist(id="wf-review-stages", options=[], value=None, switch=True),
                            html_for="wf-review-stages",
                        ),
                        lg=4,
                    ),
                    dbc.Col(
                        control_group(
                            "Analysis mode",
                            dcc.Dropdown(
                                id="wf-analysis-mode",
                                options=[
                                    {"label": "Full facility analysis", "value": "full"},
                                    {"label": "QC only", "value": "qc_only"},
                                    {"label": "Discovery", "value": "discovery"},
                                ],
                                value=DEFAULT_WORKFLOW_ANALYSIS_MODE,
                                clearable=False,
                            ),
                            None,
                            "wf-analysis-mode",
                        ),
                        lg=4,
                    ),
                ],
                className="g-3",
            ),
            dbc.Row(
                [
                    dbc.Col(
                        control_group(
                            "SIRIUS annotation",
                            dbc.Switch(
                                id="wf-enable-sirius",
                                label="Run SIRIUS after MS-DIAL",
                                value=False,
                            ),
                            None,
                            "wf-enable-sirius",
                        ),
                        lg=6,
                    ),
                    dbc.Col(
                        control_group(
                            "Annotation mode",
                            dcc.Dropdown(
                                id="wf-sirius-annotation-mode",
                                options=[
                                    {"label": "Formulas", "value": "formulas"},
                                    {"label": "CANOPUS classes", "value": "canopus"},
                                    {"label": "CSI:FingerID structures", "value": "structures"},
                                    {"label": "Full annotation", "value": "full"},
                                ],
                                value="formulas",
                                clearable=False,
                                disabled=True,
                            ),
                            None,
                            "wf-sirius-annotation-mode",
                        ),
                        lg=6,
                    ),
                ],
                className="g-3 mt-1",
            ),
        ],
        id="workflow-compute",
        className="nf-tab-pane",
    )


def _review_tab() -> html.Div:
    return html.Div(
        [
            control_group(
                "Pipeline parameters JSON",
                dcc.Textarea(id="wf-params", value="{}", className="nf-json-editor"),
                "Merged with the selections above at launch.",
                "wf-params",
            ),
            html.Details(
                [
                    html.Summary("Full Nextflow DAG", className="nf-details-summary"),
                    html.Div(
                        html.Img(
                            id="wf-dag-image",
                            src="/assets/msdial5-workflow-dag-v2-full.svg",
                            alt="MS-DIAL 5 Nextflow DAG showing input staging, MS-DIAL processing, alignment adaptation, and modular StatPipeline v2 outputs.",
                            className="workflow-dag-image",
                        ),
                        id="workflow-full-dag",
                        className="workflow-dag-frame nf-dag-frame",
                    ),
                ],
                className="nf-details",
            ),
        ],
        className="nf-tab-pane",
    )


def workflows_page_layout() -> list:
    return [
        html.Div(
            [
                dcc.Store(id="wf-page-ready", data=True),
                dcc.Store(id="wf-definitions-store", data={}, storage_type="session"),
                dcc.Store(id="wf-library-store", data={}, storage_type="session"),
                dcc.Store(id="wf-library-selection-store", data={}, storage_type="session"),
                dcc.Store(id="wf-msdial-template-source-store", data={}),
                dcc.Store(id="wf-workflow-command-result-store", data={}),
                dcc.Download(id="wf-msdial-template-download"),
                html.Main(
                    [
                        page_header(),
                        status_card_grid(),
                        html.Div(id="wf-status", className="status-box nf-launch-status"),
                        dbc.Row(
                            [
                                dbc.Col(
                                    configuration_wizard_card(
                                        _pipeline_tab(),
                                        _inputs_tab(),
                                        _compute_tab(),
                                        _review_tab(),
                                    ),
                                    xl=7,
                                    className="mb-3 nf-config-column",
                                ),
                                dbc.Col(
                                    [
                                        command_preview_card(),
                                        validation_alert(),
                                    ],
                                    xl=5,
                                    className="mb-3 nf-command-column",
                                ),
                            ],
                            className="g-3 nf-config-command-row align-items-stretch",
                        ),
                        pipeline_overview_card(),
                    ],
                    className="nf-studio-main",
                ),
            ],
            className="nf-studio-frame",
        )
    ]
