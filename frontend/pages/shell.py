import dash_bootstrap_components as dbc
from dash import dcc, html

def app_layout() -> html.Div:
    return html.Div(
        [
            dcc.Store(id="token-store", storage_type="session"),
            dcc.Store(id="studies-store", data=[], storage_type="session"),
            dcc.Store(id="studies-context-store", data={}, storage_type="session"),
            dcc.Store(id="study-select-store", data=None, storage_type="session"),
            dcc.Store(id="assays-store", data=[], storage_type="session"),
            dcc.Store(id="assays-context-store", data={}, storage_type="session"),
            dcc.Store(id="assay-select-store", data=None, storage_type="session"),
            dcc.Store(id="study-transition-refresh-signal", data=0),
            dcc.Store(id="page-data-revision-store", data=0, storage_type="session"),
            dcc.Store(id="page-data-mutation-signal", data=0, storage_type="session"),
            dcc.Store(id="workflow-mutation-signal", data=0, storage_type="session"),
            dcc.Store(id="workflow-jobs-revision-store", data=None, storage_type="session"),
            dcc.Store(id="dashboard-store", data={}, storage_type="session"),
            dcc.Store(id="dashboard-resume-pending-store", data={}, storage_type="session"),
            dcc.Store(id="last-context-store", data={}, storage_type="local"),
            dcc.Store(id="summary-store", data={}, storage_type="session"),
            dcc.Store(id="metadata-profiles-store", data=[], storage_type="session"),
            dcc.Store(id="samples-page-store", data={}, storage_type="session"),
            dcc.Store(id="sample-upload-refresh-signal", data=0, storage_type="session"),
            dcc.Store(id="sample-folder-browser-store", data={"current_path": None, "parent_path": None, "entries": []}, storage_type="session"),
            dcc.Store(
                id="workflow-file-selection-store",
                storage_type="session",
                data={"study_id": None, "assay_id": None, "mode": "all"},
            ),
            dcc.Store(id="current-page-store", data=None),
            dcc.Location(id="url", refresh=False),
            html.Div(id="scroll-reset-signal", style={"display": "none"}),
            dbc.Navbar(
                dbc.Container(
                    [
                        dbc.NavbarBrand(
                            html.Img(
                                src="/assets/logo/metabolens-logo-banner.svg",
                                alt="MetaboLens",
                                width=269,
                                height=48,
                                className="metabolens-banner-logo",
                            ),
                            className="m-0 p-0 mw-100",
                            style={"minWidth": 0},
                        ),
                        html.Span(
                            "EMBL Metabolomics Facility",
                            className="metabolens-facility-name text-white d-none d-sm-inline",
                        ),
                        html.Details(
                            [
                                html.Summary("Session", className="navbar-session-trigger"),
                                html.Div(
                                    [
                                        dbc.Input(id="login-email", type="email", placeholder="Email", size="sm", className="mb-2"),
                                        dbc.Input(id="login-password", type="password", placeholder="Password", size="sm", className="mb-2"),
                                        html.Div(
                                            [
                                                dbc.Button("Login", id="login-btn", n_clicks=0, color="primary", size="sm"),
                                            ],
                                            className="d-flex gap-2",
                                        ),
                                        html.Div(id="login-status", className="small mt-2", role="status"),
                                    ],
                                    className="navbar-session-menu",
                                ),
                            ],
                            className="navbar-session",
                        ),
                    ],
                    fluid=True,
                    className="gap-3 flex-nowrap align-items-center",
                ),
                dark=True,
                className="embl-navbar",
            ),
            dbc.Container(
                fluid=True,
                className="metabo-shell",
                children=[
                    dbc.Row(
                        [
                            dbc.Col(
                                dbc.Card(
                                    dbc.CardBody(
                                        [
                                            dbc.Nav(
                                                [
                                                    dbc.NavLink("Dashboard", href="/dashboard", active="exact"),
                                                    dbc.NavLink("Studies", href="/studies", active="exact"),
                                                    dbc.NavLink("Files", href="/files", active="exact"),
                                                    dbc.NavLink("Summary", href="/summary", active="exact"),
                                                    dbc.NavLink("Workflows", href="/workflows", active="exact"),
                                                    dbc.NavLink("Jobs (prototype)", href="/jobs", active="partial"),
                                                ],
                                                pills=True,
                                                vertical=True,
                                                className="sidebar-nav",
                                            ),
                                            html.Hr(className="my-3"),
                                            html.Div("Current Context", className="sidebar-context-title"),
                                            html.Div(id="sidebar-selection-reminder"),
                                            # The /jobs picker. Jobs are assay-scoped, so the list is
                                            # the next level of the context above it. It is shown only
                                            # on /jobs routes; its poll and store live in that page, so
                                            # it is not fetched anywhere else.
                                            html.Div(
                                                [
                                                    html.Hr(className="my-3"),
                                                    html.Div(
                                                        [
                                                            html.Div("Jobs", className="sidebar-context-title mb-0"),
                                                            dbc.Button(
                                                                "↻",
                                                                id="jw-refresh-btn",
                                                                n_clicks=0,
                                                                size="sm",
                                                                color="link",
                                                                className="p-0 lh-1 text-decoration-none",
                                                                title="Refresh the job list",
                                                            ),
                                                        ],
                                                        className="d-flex justify-content-between align-items-center",
                                                    ),
                                                    html.Div(id="jw-job-list-status",
                                                             className="text-muted small text-truncate mb-2"),
                                                    html.Div(id="jw-job-list", className="sidebar-job-list"),
                                                ],
                                                id="sidebar-jobs",
                                                className="sidebar-jobs",
                                                hidden=True,
                                            ),
                                        ]
                                    ),
                                    className="metabo-card sidebar-wrap",
                                ),
                                id="shell-sidebar-column",
                                lg=2,
                                md=3,
                                className="pt-4 sidebar-column",
                            ),
                            dbc.Col(
                                id="shell-main-column",
                                className="main-content",
                                children=[html.Div(id="page-content")],
                                lg=10,
                                md=9,
                            ),
                        ],
                        className="g-3",
                    )
                ],
            ),
        ]
    )
