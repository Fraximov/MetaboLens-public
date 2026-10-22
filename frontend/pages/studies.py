import dash_bootstrap_components as dbc
from dash import dcc, html

from ui.components import metadata_input, section_card
from ui.figures import empty_structure_figure


def studies_page_layout() -> list:
    return [
        section_card(
            "Study List",
            [
                dbc.Row(
                    [
                        dbc.Col(dbc.Input(id="study-title", type="text", placeholder="Study title"), lg=4, md=6, className="mb-2"),
                        dbc.Col(dbc.Input(id="study-description", type="text", placeholder="Description"), lg=4, md=6, className="mb-2"),
                        dbc.Col(dbc.Button("Create Study", id="study-create-btn", n_clicks=0, color="primary", className="w-100"), lg=2, md=6, className="mb-2"),
                        dbc.Col(dbc.Button("Refresh Studies", id="study-refresh-btn", n_clicks=0, color="outline-secondary", className="w-100"), lg=2, md=6, className="mb-2"),
                    ],
                    className="g-2",
                ),
                html.Div(id="study-status", className="status-box"),
                html.Div(id="study-list-table", className="mt-3"),
            ],
        ),
        section_card(
            "Studies",
            [
                dbc.Card(
                    dbc.CardBody(
                        [
                            dbc.Form(
                                [
                                    dbc.Row(
                                        [
                                            dbc.Col(
                                                [
                                                    dbc.Label("Transition Target", html_for="study-transition", className="mb-1"),
                                                    dcc.Dropdown(
                                                        id="study-transition",
                                                        options=[],
                                                        value=None,
                                                        placeholder="Select transition target",
                                                        clearable=False,
                                                        className="mb-3",
                                                    ),
                                                    html.Div(id="study-transition-help", className="text-muted small mb-2"),
                                                ],
                                                lg=6,
                                            ),
                                            dbc.Col(
                                                [
                                                    dbc.Label("Submission Actions", className="mb-1"),
                                                    dbc.Button(
                                                        "Submit Study",
                                                        id="study-submit-btn",
                                                        n_clicks=0,
                                                        color="primary",
                                                        className="w-100 mb-3",
                                                    ),
                                                ],
                                                lg=6,
                                            ),
                                        ],
                                        className="g-3",
                                    ),
                                    dbc.Row(
                                        [
                                            dbc.Col(
                                                dbc.Button(
                                                    "Reopen to Draft",
                                                    id="study-reopen-btn",
                                                    n_clicks=0,
                                                    color="outline-secondary",
                                                    className="w-100",
                                                ),
                                                lg=6,
                                                className="mb-3",
                                            ),
                                            dbc.Col(
                                                dbc.Button(
                                                    "Transition",
                                                    id="study-transition-btn",
                                                    n_clicks=0,
                                                    color="outline-secondary",
                                                    className="w-100",
                                                ),
                                                lg=6,
                                                className="mb-3",
                                            ),
                                        ],
                                        className="g-3",
                                    ),
                                ]
                            )
                        ]
                    ),
                    className="mb-3",
                ),
                html.Pre(id="study-action-status", className="status-box mt-3"),
            ],
        ),
        section_card(
            "Assays",
            [
                dbc.Row(
                    [
                        dbc.Col(dbc.Input(id="assay-name", type="text", placeholder="Assay name"), lg=5, md=6, className="mb-2"),
                        dbc.Col(
                            dcc.Dropdown(
                                id="assay-type",
                                options=[
                                    {"label": "Metabolomics LC-MS", "value": "LC_MS"},
                                    {"label": "Metabolomics GC-MS", "value": "GC_MS"},
                                    {"label": "Metabolomics NMR", "value": "NMR"},
                                ],
                                value="LC_MS",
                                clearable=False,
                            ),
                            lg=3,
                            md=6,
                            className="mb-2",
                        ),
                        dbc.Col(dbc.Button("Create Assay", id="assay-create-btn", n_clicks=0, color="primary", className="w-100"), lg=2, md=6, className="mb-2"),
                        dbc.Col(dbc.Button("Refresh Assays", id="assay-refresh-btn", n_clicks=0, color="outline-secondary", className="w-100"), lg=2, md=6, className="mb-2"),
                    ],
                    className="g-2",
                ),
                html.Div(id="assay-status", className="status-box"),
                html.Div(id="assay-list-table", className="mt-3"),
            ],
        ),
        section_card(
            "Metadata",
            [
                dbc.Alert("Submit and Load apply to the active assay.", color="light", className="mb-3"),
                dbc.Card(
                    dbc.CardBody(
                        [
                            dbc.Form(
                                [
                                    dbc.Row(
                                        [
                                            dbc.Col(
                                                [
                                                    dbc.Label("Metadata Profile", html_for="metadata-profile", className="mb-1"),
                                                    dcc.Dropdown(
                                                        id="metadata-profile",
                                                        options=[],
                                                        value=None,
                                                        placeholder="Select metadata profile",
                                                        clearable=False,
                                                        className="mb-2",
                                                    ),
                                                    html.Div(id="metadata-profile-help", className="text-muted small"),
                                                ],
                                                md=8,
                                                className="mb-3",
                                            ),
                                            dbc.Col(
                                                html.Div(id="metadata-assay-subtype-help", className="text-muted small mt-md-4 pt-md-2"),
                                                md=4,
                                                className="mb-3",
                                            ),
                                        ],
                                        className="g-3",
                                    ),
                                    dbc.Card(
                                        dbc.CardBody(
                                            [
                                                html.H6("Shared Method Context", className="mb-3"),
                                                dbc.Row(
                                                    [
                                                        metadata_input("Platform Name", "metadata-platform-name"),
                                                        metadata_input("MS Vendor", "metadata-ms-vendor"),
                                                        metadata_input("MS Model", "metadata-ms-model"),
                                                        metadata_input("Operator", "metadata-operator-name"),
                                                        metadata_input("Acquisition Software", "metadata-acquisition-software"),
                                                        metadata_input("Batch ID", "metadata-batch-id"),
                                                        metadata_input("Sample Type", "metadata-sample-type"),
                                                        metadata_input("Notes", "metadata-notes", textarea=True, md=12),
                                                    ],
                                                    className="g-3",
                                                ),
                                            ]
                                        ),
                                        className="mb-3",
                                        id="metadata-shared-card",
                                    ),
                                    dbc.Card(
                                        dbc.CardBody(
                                            [
                                                html.H6("MS Source And Analyzer", className="mb-3"),
                                                dbc.Row(
                                                    [
                                                        metadata_input("Analyzer Type", "metadata-ms-analyzer-type"),
                                                        metadata_input("Ion Source", "metadata-ms-ion-source"),
                                                        metadata_input("Polarity Mode", "metadata-ms-polarity-mode"),
                                                        metadata_input("Internal Reference", "metadata-internal-reference"),
                                                    ],
                                                    className="g-3",
                                                ),
                                            ]
                                        ),
                                        className="mb-3",
                                        id="metadata-ms-card",
                                    ),
                                    dbc.Card(
                                        dbc.CardBody(
                                            [
                                                html.H6("Chromatography", className="mb-3"),
                                                dbc.Row(
                                                    [
                                                        metadata_input("Chromatography Vendor", "metadata-lc-vendor"),
                                                        metadata_input("Chromatography Instrument", "metadata-lc-system"),
                                                        metadata_input("Chromatography Type", "metadata-chromatography-type"),
                                                        metadata_input("Separation Mode", "metadata-separation-mode"),
                                                        metadata_input("Column Model", "metadata-column-name"),
                                                        metadata_input("Column Type", "metadata-column-type"),
                                                        metadata_input("Guard Column Type", "metadata-column-guard-type"),
                                                        metadata_input("Column Chemistry", "metadata-column-chemistry"),
                                                    ],
                                                    className="g-3",
                                                ),
                                                html.Div(
                                                    dbc.Row(
                                                        [
                                                            metadata_input("Flow Rate (mL/min)", "metadata-flow-rate"),
                                                            metadata_input("Column Temperature (C)", "metadata-column-temperature"),
                                                            metadata_input("Autosampler Temperature (C)", "metadata-autosampler-temperature"),
                                                            metadata_input("Injection Volume (uL)", "metadata-injection-volume"),
                                                            metadata_input("Mobile Phase A", "metadata-mobile-phase-a", md=12),
                                                            metadata_input("Mobile Phase B", "metadata-mobile-phase-b", md=12),
                                                            metadata_input("Gradient Program", "metadata-gradient-program", textarea=True, md=12),
                                                        ],
                                                        className="g-3",
                                                    ),
                                                    id="metadata-lcms-only-fields",
                                                ),
                                                html.Div(
                                                    dbc.Row(
                                                        [
                                                            metadata_input("Derivatization", "metadata-derivatization", textarea=True, md=12),
                                                        ],
                                                        className="g-3",
                                                    ),
                                                    id="metadata-gcms-only-fields",
                                                ),
                                            ]
                                        ),
                                        className="mb-3",
                                        id="metadata-chromatography-card",
                                    ),
                                    dbc.Card(
                                        dbc.CardBody(
                                            [
                                                html.H6("Acquisition", className="mb-3"),
                                                dbc.Row(
                                                    [
                                                        metadata_input("Acquisition Mode", "metadata-acquisition-mode"),
                                                        metadata_input("m/z Range", "metadata-mz-range"),
                                                        metadata_input("Resolution", "metadata-resolution"),
                                                        metadata_input("Spray Voltage Positive (kV)", "metadata-spray-voltage-positive"),
                                                        metadata_input("Spray Voltage Negative (kV)", "metadata-spray-voltage-negative"),
                                                        metadata_input("Vaporizer Temperature (C)", "metadata-vaporizer-temperature"),
                                                        metadata_input("Transfer Tube Temperature (C)", "metadata-transfer-tube-temperature"),
                                                        metadata_input("Sheath Gas", "metadata-sheath-gas"),
                                                        metadata_input("Aux Gas", "metadata-aux-gas"),
                                                        metadata_input("Sweep Gas", "metadata-sweep-gas"),
                                                        metadata_input("RF Lens", "metadata-rf-lens"),
                                                    ],
                                                    className="g-3",
                                                ),
                                            ]
                                        ),
                                        className="mb-3",
                                        id="metadata-acquisition-card",
                                    ),
                                    dbc.Card(
                                        dbc.CardBody(
                                            [
                                                html.H6("NMR Acquisition", className="mb-3"),
                                                dbc.Row(
                                                    [
                                                        metadata_input("NMR Tube Type", "metadata-nmr-tube-type"),
                                                        metadata_input("Solvent", "metadata-solvent"),
                                                        metadata_input("Sample pH", "metadata-sample-ph"),
                                                        metadata_input("Temperature", "metadata-temperature"),
                                                        metadata_input("Probe", "metadata-nmr-probe"),
                                                        metadata_input("Number Of Transients", "metadata-number-of-transients"),
                                                        metadata_input("Pulse Sequence", "metadata-pulse-sequence-name"),
                                                        metadata_input("Magnetic Field Strength", "metadata-magnetic-field-strength"),
                                                    ],
                                                    className="g-3",
                                                ),
                                            ]
                                        ),
                                        className="mb-3",
                                        id="metadata-nmr-card",
                                    ),
                                    dbc.Row(
                                        [
                                            dbc.Col(
                                                dbc.Button(
                                                    "Submit Metadata",
                                                    id="metadata-submit-btn",
                                                    n_clicks=0,
                                                    color="primary",
                                                    className="w-100",
                                                ),
                                                lg=4,
                                                md=6,
                                                className="mb-3",
                                            ),
                                            dbc.Col(
                                                dbc.Button(
                                                    "Get Latest Metadata",
                                                    id="metadata-get-btn",
                                                    n_clicks=0,
                                                    color="outline-secondary",
                                                    className="w-100",
                                                ),
                                                lg=4,
                                                md=6,
                                                className="mb-3",
                                            ),
                                        ],
                                        className="g-3",
                                    ),
                                ]
                            )
                        ]
                    ),
                    className="mb-3",
                ),
                html.Pre(id="metadata-status", className="status-box mt-3"),
            ],
        ),
    ]
