import dash
import dash_bootstrap_components as dbc
from dash import Input, Output, State, dcc, html

from pages.dashboard import dashboard_page_layout
from pages.files import files_page_layout
from pages.job_workspace import job_workspace_layout
from pages.studies import studies_page_layout
from pages.study_packaging import study_packaging_layout
from pages.summary import summary_page_layout
from pages.workflows import workflows_page_layout
from ui.formatting import resolve_page_slug


def page_key(normalized: str) -> str:
    """The part of a route that decides which layout is mounted.

    `/jobs` and `/jobs/128` share the key `jobs`, so navigating between jobs
    leaves the workspace mounted and only its detail callback runs. Without this
    the page would be rebuilt on every selection and the job list would blank.
    """
    parts = (normalized or "").split("/")
    if len(parts) >= 3 and parts[0] == "studies" and parts[1].isdigit() and parts[2] == "packaging":
        # Every packaging section of one Study keeps the workspace mounted.
        return f"isa-{parts[1]}"
    if len(parts) == 3 and parts[0] == "studies" and parts[1].isdigit() and parts[2] == "metadata":
        # The retired metadata page: the workspace mounts and redirects to its study section.
        return f"isa-{parts[1]}"
    return parts[0]


def register_callbacks(app):
    @app.callback(
        Output("page-content", "children"),
        Output("current-page-store", "data"),
        Input("url", "pathname"),
        State("current-page-store", "data"),
    )
    def render_page(pathname: str | None, mounted_page: str | None):
        normalized, canonical_path = resolve_page_slug(pathname)
        key = page_key(normalized)
        if key and key == mounted_page and not canonical_path:
            # Same page, deeper path: let the page's own callbacks handle it.
            return dash.no_update, dash.no_update

        def maybe_redirect(children: list):
            if not canonical_path:
                return children
            return html.Div(
                [
                    dcc.Location(id="canonical-route-redirect", pathname=canonical_path, refresh=False),
                    *children,
                ]
            )

        if normalized == "dashboard":
            return maybe_redirect(dashboard_page_layout()), key
        if normalized == "studies":
            return maybe_redirect(studies_page_layout()), key
        if key.startswith("isa-"):
            return study_packaging_layout(int(key.removeprefix("isa-"))), key
        if normalized == "files":
            return maybe_redirect(files_page_layout()), key
        if normalized == "summary":
            return summary_page_layout(), key
        if normalized == "workflows":
            return workflows_page_layout(), key
        if key == "jobs":
            return job_workspace_layout(), key
        return dbc.Alert("Page not found", color="warning"), key

    app.clientside_callback(
        """
        function(pathname) {
            if (pathname !== undefined) {
                window.requestAnimationFrame(function() {
                    window.scrollTo(0, 0);
                });
            }
            return "";
        }
        """,
        Output("scroll-reset-signal", "children"),
        Input("url", "pathname"),
    )
