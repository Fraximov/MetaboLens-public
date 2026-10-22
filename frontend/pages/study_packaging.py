"""ISA packaging workspace for one Study.

    /studies/<id>/packaging                           builds: every revision, and "new package"
    /studies/<id>/packaging/builds/<rev>              one build's findings, downloads, export and deletion
    /studies/<id>/packaging/builds/<rev>/files[/<f>]  what that build sends to MetaboLights
    /studies/<id>/packaging/new                       new package, step 1: one workflow result per assay
    /studies/<id>/packaging/new/preview               step 2: readiness for those runs, then build
    /studies/<id>/packaging/study                      title, description, dates, people, publications, design
    /studies/<id>/packaging/protocols                  sample collection and per-assay protocols
    /studies/<id>/packaging/samples                    sample names and characteristics, factor mapping

Every route shares one page key, so the frame stays mounted and only the section
re-renders, as in /jobs. The URL, not the global active assay, owns the Study and
its source selection.
"""
import dash_bootstrap_components as dbc
from dash import dcc, html


def study_packaging_layout(study_id: int | None = None) -> list:
    return [
        dcc.Store(id="pkg-choices"),
        # The submission metadata a section was rendered from, with its revision.
        dcc.Store(id="pkg-metadata"),
        # Proposed source selections per Study, kept until a build freezes them.
        dcc.Store(id="pkg-selection", storage_type="session"),
        dcc.Store(id="pkg-build-signal"),
        # Enabled only while a build or export is queued or running.
        dcc.Interval(id="pkg-poll", interval=5000, disabled=True),
        dcc.Download(id="pkg-download"),
        # callback-nav also tells the shell's url, so the workspace re-renders the new route.
        dcc.Location(id="pkg-redirect", refresh="callback-nav"),
        html.Div(id="pkg-intro"),
        html.Div(id="pkg-nav"),
        html.Div(id="pkg-section", **{"aria-live": "polite"}),
        html.Div(id="pkg-action-message", role="status", className="small mt-2"),
        html.Div(id="pkg-download-message", role="status", className="small text-danger"),
    ]


def runs_controls(study_id: int = 0, request_id: str = "", summary=None) -> list:
    """Step 1's fixed controls, below the run chosen for each assay."""
    return [
        html.Div([
            dbc.Label("metabolights request or accession", html_for="pkg-request-id", className="mb-1"),
            dbc.Input(id="pkg-request-id", value=request_id, placeholder="REQ… or MTBLS… — empty for a local draft",
                      className="font-monospace", debounce=True),
        ], className="mb-3", style={"maxWidth": "28rem"}),
        html.Div(summary, id="pkg-sources-summary", className="mb-3"),
        dcc.Link("preview →", href=f"/studies/{study_id}/packaging/new/preview", className="btn btn-sm btn-primary"),
    ]


def build_controls(study_id: int = 0, revision: int | None = None, problem: str | None = None) -> html.Div:
    """Step 2's actions: back to the runs, or build what the preview shows."""
    return html.Div([
        html.Div([
            dcc.Link("← change runs", href=f"/studies/{study_id}/packaging/new",
                     className="btn btn-sm btn-outline-primary"),
            dbc.Button(f"build revision {revision}" if revision else "build", id="pkg-build", n_clicks=0,
                       color="primary", size="sm", disabled=bool(problem),
                       title="freeze these runs and the saved metadata into a new revision"),
        ], className="d-flex gap-2"),
        html.Div(problem, className="text-danger small mt-2") if problem else None,
        html.Div(id="pkg-build-message", role="status", className="mt-2"),
    ], className="mt-3")
