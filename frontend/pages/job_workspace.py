"""Master-detail job workspace: the job list in the shell sidebar, a job-scoped detail pane here.

The route carries both the job and the section it is showing:

    /jobs                  the list
    /jobs/<id>             overview
    /jobs/<id>/outputs     registered artifacts
    /jobs/<id>/isqc        IS-QC review and RT correction
    /jobs/<id>/eic         aligned EIC viewer

`render_page` keys on the page slug, so moving between jobs and sections leaves
this layout mounted and only the detail pane re-renders. Sections are routes
rather than collapsible panels, which means the components of a section exist
only while that route is open -- Dash then skips their callbacks on its own, and
no callback has to ask whether a panel is visible.
"""
import dash_bootstrap_components as dbc
from dash import dcc, html


def job_workspace_layout() -> list:
    return [
        dcc.Interval(id="jw-poll", interval=15000, n_intervals=0),
        dcc.Store(id="jw-delete-result", data=None),
        dcc.Store(id="jw-continue-result", data=None),
        dcc.Store(id="jw-jobs-store", data=None),
        dcc.Store(id="jw-outputs-store", data=None),
        dcc.Download(id='jw-output-download'),
        html.Div(id='jw-output-download-status', className='text-danger small'),
        # The job picker lives in the shell sidebar (see pages/shell.py), so the
        # detail card has the full width of the main column.
        dbc.Card(
            dbc.CardBody(
                [
                    html.Div(id="jw-section-nav"),
                    html.Div(
                        "Select a job from the list in the sidebar.",
                        id="jw-detail",
                        className="text-muted",
                    ),
                ]
            ),
            className="metabo-card",
            # minWidth 0 lets wide tables scroll inside the card instead of
            # forcing the page wider.
            style={"minWidth": "0"},
        ),
    ]
