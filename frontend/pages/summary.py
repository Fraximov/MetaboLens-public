import dash_bootstrap_components as dbc
from dash import dcc, html

from ui.components import section_card
from ui.figures import empty_structure_figure


def summary_page_layout() -> list:
    return [
        section_card(
            "Study Structure",
            [
                dbc.Alert(
                    "A sample appears once per assay or dataset it belongs to. Click a node to make its assay active.",
                    color="light",
                    className="mb-3",
                ),
                dcc.Graph(
                    id="summary-structure-chart",
                    figure=empty_structure_figure(),
                    config={"displayModeBar": False},
                    style={"height": "520px"},
                ),
            ],
        ),
        section_card(
            "Study Summary",
            [
                dbc.Row(
                    [
                        dbc.Col(
                            dbc.Button("Refresh Summary", id="summary-refresh-btn", n_clicks=0, color="outline-secondary", className="w-100"),
                            md=3,
                            className="mb-2",
                        ),
                    ],
                    className="g-2",
                ),
                html.Div(id="summary-status", className="status-box"),
                html.Div(id="summary-overview-table", className="mt-3"),
            ],
        ),
        section_card(
            "Assay Summary",
            [
                html.Div(id="summary-assay-table"),
            ],
        ),
        section_card(
            "Dataset Overview",
            [
                html.Div(id="summary-dataset-table"),
            ],
        ),
        section_card(
            "Activity Log",
            [
                html.Div(id="summary-activity-table"),
            ],
        ),
    ]
