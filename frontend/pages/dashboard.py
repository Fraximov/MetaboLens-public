import dash_bootstrap_components as dbc
from dash import html

from ui.components import page_intro, section_card


def dashboard_page_layout() -> list:
    return [
        dbc.Card(
            dbc.CardBody(
                [
                    page_intro(
                        title="Dashboard",
                        actions=html.Div(
                            [
                                dbc.Button(
                                    "Resume Last Setup",
                                    id="dashboard-resume-btn",
                                    n_clicks=0,
                                    color="primary",
                                ),
                                dbc.Button(
                                    "Refresh Dashboard",
                                    id="dashboard-refresh-btn",
                                    n_clicks=0,
                                    color="outline-secondary",
                                ),
                            ],
                            className="d-flex gap-2 flex-wrap justify-content-end",
                        ),
                    ),
                    # What Resume would restore, from this browser's last session.
                    html.Div(id="dashboard-resume-copy", className="section-helper compact"),
                    html.Div(id="dashboard-resume-status", className="inline-status mt-2"),
                    html.Div(id="dashboard-status", className="status-box"),
                ]
            ),
            className="metabo-card mb-4",
        ),
        html.Div(id="dashboard-kpi-strip", className="dashboard-kpi-strip mb-4"),
        dbc.Row(
            [
                dbc.Col(
                    section_card("Recent Studies", [html.Div(id="dashboard-recent-studies-table")]),
                    lg=7,
                ),
                dbc.Col(
                    section_card("Assays", [html.Div(id="dashboard-assays-table")]),
                    lg=5,
                ),
            ],
            className="g-3",
        ),
        section_card("Workflow Status", [html.Div(id="dashboard-workflow-table")]),
        section_card("Recent Activity", [html.Div(id="dashboard-activity-table")]),
    ]
