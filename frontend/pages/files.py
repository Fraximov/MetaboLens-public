import dash_bootstrap_components as dbc
from dash import dcc, html

from ui.components import page_intro, section_header


def files_page_layout() -> list:
    return [
        dbc.Card(
            dbc.CardBody(
                [
                    page_intro(title="Files"),
                    section_header(
                        "Import RAW folder",
                        "Preview top-level RAW or mzML files, then choose a new or existing study sample for each acquisition. RAW is preferred when both formats are present; registered paths are skipped.",
                    ),
                    dbc.Row(
                        [
                            dbc.Col(
                                dbc.Input(
                                    id="sample-scan-folder-path",
                                    type="text",
                                    placeholder="/raw_test or /data/raw/batch_01",
                                ),
                                lg=9,
                                className="mb-2",
                            ),
                            dbc.Col(
                                dbc.Button(
                                    "Preview RAW Folder",
                                    id="sample-scan-folder-btn",
                                    n_clicks=0,
                                    color="primary",
                                    className="w-100",
                                ),
                                lg=3,
                                className="mb-2",
                            ),
                        ],
                        className="g-2",
                    ),
                    dbc.Row(
                        [
                            dbc.Col(dbc.Button("Browse Folders", id="sample-browse-folder-btn", n_clicks=0, color="outline-secondary", className="w-100"), md=3, className="mb-2"),
                            dbc.Col(dbc.Button("Up One Level", id="sample-browse-up-btn", n_clicks=0, color="outline-secondary", className="w-100"), md=3, className="mb-2"),
                            dbc.Col(dbc.Button("Refresh Samples", id="sample-list-btn", n_clicks=0, color="outline-secondary", className="w-100"), md=3, className="mb-2"),
                            dbc.Col(dbc.Button("Refresh File List", id="wf-file-refresh-btn", n_clicks=0, color="outline-secondary", className="w-100"), md=3, className="mb-2"),
                        ],
                        className="g-2",
                    ),
                    html.Div(id="sample-scan-status", className="inline-status mt-2"),
                    dcc.Store(id="sample-folder-preview-store"),
                    html.Div(id="sample-folder-preview", className="mt-2"),
                    html.Div(id="sample-folder-import-status", className="inline-status mt-2"),
                    html.Div(id="sample-folder-browser-wrap", className="mt-2", children=html.Div(id="sample-folder-browser")),
                    html.Details(
                        [
                            html.Summary("CSV import", className="sample-scan-toggle-summary"),
                            html.Div(
                                [
                                    html.Div(
                                        "Upload a CSV that already contains sample metadata and RAW source paths. CSV import uses strict row registration and does not skip duplicates.",
                                        className="section-helper compact mb-2",
                                    ),
                                    dbc.Row(
                                        [
                                            dbc.Col(
                                                dcc.Upload(
                                                    id="sample-csv-upload",
                                                    multiple=False,
                                                    children=dbc.Button("Upload CSV", color="outline-secondary", className="w-100"),
                                                ),
                                                md=4,
                                                className="mb-2",
                                            ),
                                            dbc.Col(
                                                dbc.Button(
                                                    "Download CSV Template",
                                                    id="sample-template-download-btn",
                                                    n_clicks=0,
                                                    color="outline-secondary",
                                                    className="w-100",
                                                ),
                                                md=4,
                                                className="mb-2",
                                            ),
                                        ],
                                        className="g-2",
                                    ),
                                    html.Div(id="sample-csv-status", className="inline-status mt-2"),
                                ],
                                className="sample-preview-body",
                            ),
                        ],
                        className="mt-3 sample-scan-toggle workspace-disclosure",
                    ),
                    dcc.Download(id="sample-template-download"),
                    html.Hr(className="workspace-divider"),
                    section_header(
                        "Study samples",
                        action=dbc.Button("Save Visible Page Metadata", id="sample-metadata-save-btn", n_clicks=0, color="outline-secondary"),
                    ),
                    html.Div(
                        id="sample-list-table",
                        className="mt-2",
                    ),
                    dbc.Row(
                        [
                            dbc.Col(html.Div(id="sample-metadata-page-summary", className="section-helper compact"), md=8),
                            dbc.Col(
                                dbc.Pagination(
                                    id="sample-metadata-pagination",
                                    active_page=1,
                                    max_value=1,
                                    fully_expanded=False,
                                    size="sm",
                                ),
                                md=4,
                                className="d-flex justify-content-md-end",
                            ),
                        ],
                        className="g-2 align-items-center mt-2",
                    ),
                    html.Div(id="sample-metadata-save-status", className="inline-status mt-2"),
                ]
            ),
            className="metabo-card workspace-card mb-4",
        ),
        dbc.Card(dbc.CardBody([
            section_header("Assay acquisitions", hint="Each injection keeps its own batch and sequence position."),
            dcc.Store(id="acquisitions-store"),
            html.Div(id="acquisitions-table"),
            dbc.Button("Save acquisition metadata", id="acquisitions-save", n_clicks=0,
                       color="outline-secondary", className="mt-2"),
            html.Div(id="acquisitions-status", className="inline-status mt-2"),
        ]), className="metabo-card workspace-card mb-4"),
        dbc.Card(
            dbc.CardBody(
                [
                    section_header("Workflow input files"),
                    dbc.Row(
                        [
                            dbc.Col(
                                dbc.RadioItems(
                                    id="wf-file-selection-mode",
                                    options=[
                                        {"label": "All files", "value": "all"},
                                        {"label": "Select specific files", "value": "selected"},
                                    ],
                                    value="all",
                                    inline=True,
                                ),
                                lg=8,
                                className="mb-2",
                            ),
                        ],
                        className="g-2",
                    ),
                    dcc.Dropdown(id="wf-file-select", multi=True, placeholder="Choose one or more files", className="mb-2"),
                    html.Div(id="wf-file-selection-summary", className="inline-status"),
                    html.Div(id="wf-file-list-table", className="mt-2"),
                ]
            ),
            className="metabo-card quiet-card mb-4",
        ),
    ]
