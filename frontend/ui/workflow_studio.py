from __future__ import annotations

from datetime import datetime
import re

import dash_bootstrap_components as dbc
from dash import dcc, html

from services.workflow_diagram_service import WORKFLOW_METRO_MAP
from ui.formatting import status_label, timestamp_cell


RECENT_RUNS_PAGE_SIZE = 8


def _command_token_class(token: str) -> str:
    if token == "nextflow":
        return "nf-token-command"
    if token == "run":
        return "nf-token-subcommand"
    if token == "\\":
        return "nf-token-continuation"
    if token.startswith("-"):
        return "nf-token-option"
    if token.startswith("<") and token.endswith(">"):
        return "nf-token-placeholder"
    if token in {"true", "false"} or token.replace(".", "", 1).isdigit():
        return "nf-token-value"
    if "/" in token or re.search(r"\.(config|csv|msp|nf|raw|txt)$", token):
        return "nf-token-path"
    return "nf-token-text"


def format_command_preview(command: str) -> list:
    """Render a shell-like command string as lightweight syntax-highlighted spans."""

    rendered = []
    lines = (command or "").splitlines() or [""]
    for line_index, line in enumerate(lines):
        if line.startswith("#"):
            rendered.append(html.Span(line, className="nf-token-comment"))
        else:
            for token in re.split(r"(\s+)", line):
                if not token:
                    continue
                if token.isspace():
                    rendered.append(token)
                else:
                    rendered.append(html.Span(token, className=_command_token_class(token)))
        if line_index < len(lines) - 1:
            rendered.append(html.Br())
    return rendered


def page_header() -> html.Div:
    return html.Div(
        [
            html.Div(
                [
                    html.Img(src="/assets/logo/metabolens-mark.svg", alt="", className="nf-header-mark"),
                    html.Div(
                        [
                            html.H1("Launch a new run", className="nf-page-title"),
                        ],
                        className="nf-header-copy",
                    ),
                ],
                className="nf-header-main",
            ),
            html.Div(
                [
                    dbc.Button("Launch run", id="wf-launch-btn", n_clicks=0, color="success", className="nf-launch-button"),
                ],
                className="nf-header-actions",
            ),
        ],
        className="nf-page-header",
    )


def status_card(label: str, value_id: str, helper_id: str | None = None, tone: str = "neutral") -> html.Div:
    return html.Div(
        [
            html.Div("", className=f"nf-status-icon nf-status-icon-{tone}"),
            html.Div(
                [
                    html.Div(label, className="nf-status-label"),
                    html.Div(id=value_id, children="-", className=f"nf-status-value nf-status-value-{tone}"),
                    html.Div(id=helper_id, children="", className="nf-status-helper") if helper_id else None,
                ],
                className="nf-status-copy",
            ),
        ],
        className="nf-status-card",
    )


def status_card_grid() -> html.Div:
    return html.Div(
        [
            status_card("Run status", "wf-summary-run-status", "wf-summary-run-helper", "success"),
            status_card("Pipeline", "wf-summary-pipeline", "wf-summary-pipeline-helper"),
            status_card("Profile", "wf-summary-profile", None, "profile"),
            status_card("Container", "wf-summary-container", None, "container"),
            status_card("Resume", "wf-summary-resume", None, "resume"),
            status_card("Last validated", "wf-summary-last-validated", None, "time"),
        ],
        className="nf-status-grid",
    )


def control_group(label: str, control, helper: str | None = None, html_for: str | None = None) -> html.Div:
    children = [dbc.Label(label, html_for=html_for, className="nf-field-label"), control]
    if helper:
        children.append(html.Div(helper, className="nf-field-helper"))
    return html.Div(children, className="nf-field", **({'id': f'{html_for}-field'} if html_for else {}))


def configuration_wizard_card(pipeline_tab, inputs_tab, compute_tab, review_tab) -> dbc.Card:
    return dbc.Card(
        [
            dbc.Tabs(
                [
                    dbc.Tab(pipeline_tab, label="1  Pipeline", tab_id="pipeline", labelClassName="nf-step-label"),
                    dbc.Tab(inputs_tab, label="2  Inputs", tab_id="inputs", labelClassName="nf-step-label"),
                    dbc.Tab(compute_tab, label="3  Compute", tab_id="compute", labelClassName="nf-step-label"),
                    dbc.Tab(review_tab, label="4  Review", tab_id="review", labelClassName="nf-step-label"),
                ],
                id="wf-config-tabs",
                active_tab="pipeline",
                className="nf-step-tabs",
            ),
        ],
        className="nf-panel nf-config-panel",
    )


def command_preview_card() -> dbc.Card:
    return dbc.Card(
        dbc.CardBody(
            [
                html.Div("Run materialization", className="nf-panel-title nf-materialization-title"),
                dbc.Tabs(
                    [
                        dbc.Tab(
                            html.Pre(
                                id="wf-command-preview",
                                children=format_command_preview("nextflow run <select-workflow>"),
                                className="nf-terminal",
                            ),
                            label="Command",
                            tab_id="command",
                        ),
                        dbc.Tab(
                            html.Div(
                                [
                                    dcc.Textarea(
                                        id="wf-msdial-template-editor",
                                        value="",
                                        placeholder="Select a parameter template to load its content.",
                                        className="nf-template-editor",
                                    ),
                                    html.Div(
                                        "This run uses the edited text.",
                                        id="wf-template-helper",
                                        className="nf-field-helper",
                                    ),
                                    html.Div(
                                        [
                                            dbc.Button(
                                                "Reset",
                                                id="wf-msdial-template-reset",
                                                n_clicks=0,
                                                color="light",
                                                size="sm",
                                            ),
                                            dbc.Button(
                                                "Download",
                                                id="wf-msdial-template-download-btn",
                                                n_clicks=0,
                                                color="outline-success",
                                                size="sm",
                                            ),
                                        ],
                                        className="nf-template-editor-actions",
                                    ),
                                    html.Div(id="wf-msdial-template-editor-status", className="nf-inline-status nf-template-editor-status"),
                                ],
                                className="nf-template-editor-pane",
                            ),
                            label="Template",
                            tab_id="template",
                            id="wf-template-tab",
                        ),
                    ],
                    id="wf-command-template-tabs",
                    active_tab="command",
                    className="nf-preview-tabs",
                ),
            ]
        ),
        className="nf-panel nf-command-panel",
    )


def validation_alert() -> dbc.Alert:
    return dbc.Alert(
        "Select a workflow and assay to validate this run.",
        id="wf-validation-alert",
        color="warning",
        className="nf-validation-alert",
    )


def pipeline_overview_card() -> dbc.Card:
    return dbc.Card(
        html.Details(
            [
                html.Summary(
                    html.Div(
                        [
                            html.Div("Pipeline route map", className="nf-panel-title"),
                        ],
                        className="nf-collapsible-summary-copy",
                    ),
                    className="nf-collapsible-summary",
                ),
                html.Div(
                    [
                        html.Div(
                            [
                                html.Img(
                                    id="wf-overview-dag-image",
                                    src=WORKFLOW_METRO_MAP["src"],
                                    alt=WORKFLOW_METRO_MAP["alt"],
                                    className="nf-overview-dag-image",
                                ),
                            ],
                            className="nf-overview-dag-frame",
                        ),
                        html.Div(id="wf-overview-dag-source", className="nf-overview-dag-source"),
                    ],
                    className="nf-collapsible-body",
                ),
            ],
            id="wf-route-map-details",
            open=True,
            className="nf-collapsible",
        ),
        className="nf-panel nf-overview-panel",
    )


def recent_runs_card(children) -> dbc.Card:
    return dbc.Card(
        html.Details(
            [
                html.Summary(
                    html.Div(
                        [
                            html.Div("Recent runs", className="nf-panel-title"),
                        ],
                        className="nf-collapsible-summary-copy",
                    ),
                    className="nf-collapsible-summary",
                ),
                html.Div(
                    [
                        html.Div(
                            dbc.Button("Refresh jobs", id="wf-jobs-btn", n_clicks=0, color="light", size="sm", className="nf-copy-button"),
                            className="nf-collapsible-action",
                        ),
                        children,
                    ],
                    className="nf-collapsible-body",
                ),
            ],
            id="wf-recent-runs-details",
            open=False,
            className="nf-collapsible",
        ),
        id="workflow-recent-runs",
        className="nf-panel nf-recent-panel",
    )


def _duration(started_at: str | None, finished_at: str | None) -> str:
    if not started_at or not finished_at:
        return "-"
    try:
        started = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        finished = datetime.fromisoformat(str(finished_at).replace("Z", "+00:00"))
    except ValueError:
        return "-"
    seconds = max(0, int((finished - started).total_seconds()))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _task_progress(job: dict) -> tuple[str, int]:
    payload = job.get("status_payload_json") or {}
    completed = payload.get("completed_tasks", payload.get("tasks_completed"))
    total = payload.get("total_tasks", payload.get("tasks_total"))
    try:
        completed_int = int(completed)
        total_int = int(total)
    except (TypeError, ValueError):
        return "-", 0
    if total_int == 0:
        return f"{completed_int} complete", 0
    if total_int <= 0:
        return "-", 0
    return f"{completed_int} / {total_int}", max(0, min(100, int(completed_int * 100 / total_int)))


def _progress_bar(percent: int) -> html.Div:
    return html.Div(
        [
            html.Div(style={"width": f"{percent}%"}, className="nf-task-progress-fill"),
        ],
        className="nf-task-progress",
    )


def _run_status_badge(value: str | None) -> html.Span:
    raw = (value or "unknown").strip().lower()
    status_class = re.sub(r"[^a-z0-9-]+", "-", raw.replace("_", "-")).strip("-") or "unknown"
    return html.Span(status_label(raw), className=f"nf-run-status nf-run-status-{status_class}")


def _job_row(job: dict) -> html.Tr:
    job_id = job.get("id")
    pipeline_id = job.get("workflow_definition_id") or "-"
    parameters = job.get("parameter_values_json") or {}
    run_id = job.get("run_name") or f"job_{job_id}"
    profile = job.get("execution_profile") or parameters.get("nextflow_profile") or parameters.get("profile") or "-"
    tasks, percent = _task_progress(job)
    status = str(job.get("status") or "").strip().lower()
    can_delete = job_id is not None and status in {"succeeded", "failed"}
    actions = html.Div(
        [
            dbc.Button(
                "View",
                id={"type": "wf-open-job-btn", "index": int(job_id)} if job_id is not None else None,
                color="light",
                size="sm",
                className="nf-action-button",
                disabled=job_id is None,
            ),
            dcc.ConfirmDialogProvider(
                dbc.Button(
                    "Delete",
                    color="danger",
                    outline=True,
                    size="sm",
                    className="nf-action-button",
                    disabled=not can_delete,
                ),
                id={"type": "wf-delete-job-confirm", "index": int(job_id)} if job_id is not None else None,
                message=f"Delete {run_id} from MetaboLens? Its generated files can be purged after the retention period.",
            ),
        ],
        className="nf-run-actions",
    )
    return html.Tr(
        [
            html.Td(run_id),
            html.Td(pipeline_id),
            html.Td(profile),
            html.Td(_run_status_badge(job.get("status"))),
            html.Td(timestamp_cell(job.get("started_at") or job.get("created_at"))),
            html.Td(_duration(job.get("started_at"), job.get("finished_at"))),
            html.Td(html.Div([_progress_bar(percent), html.Span(tasks, className="nf-task-count")], className="nf-task-cell")),
            html.Td(actions),
        ]
    )


def recent_runs_table(jobs: list[dict] | None = None, page: int = 0) -> html.Div:
    headers = ["Run ID", "Pipeline", "Profile", "Status", "Started", "Duration", "Tasks", "Actions"]
    has_jobs = bool(jobs)
    if has_jobs:
        total = len(jobs)
        page_count = max(1, (total + RECENT_RUNS_PAGE_SIZE - 1) // RECENT_RUNS_PAGE_SIZE)
        try:
            page = int(page)
        except (TypeError, ValueError):
            page = 0
        page = max(0, min(page, page_count - 1))
        start = page * RECENT_RUNS_PAGE_SIZE
        page_jobs = jobs[start : start + RECENT_RUNS_PAGE_SIZE]
        page_status = f"{start + 1}–{min(start + RECENT_RUNS_PAGE_SIZE, total)} of {total}"
        previous_disabled = page == 0
        next_disabled = page == page_count - 1
    else:
        page_jobs = []
        page_status = ""
        previous_disabled = True
        next_disabled = True
    rows = ([_job_row(job) for job in page_jobs] if has_jobs
            else [html.Tr(html.Td("No runs for this assay yet.", colSpan=len(headers), className="text-muted"))])
    note = None
    return html.Div(
        [
            note,
            html.Div(
                [
                    html.Div(page_status, id="wf-recent-runs-page-status", className="nf-recent-pagination-status"),
                    html.Div(
                        [
                            html.Button(
                                "‹",
                                id="wf-recent-runs-prev",
                                n_clicks=0,
                                type="button",
                                title="Older jobs",
                                className="btn btn-light nf-recent-pagination-button",
                                **{"aria-label": "Older jobs"},
                                disabled=previous_disabled,
                            ),
                            html.Button(
                                "›",
                                id="wf-recent-runs-next",
                                n_clicks=0,
                                type="button",
                                title="Newer jobs",
                                className="btn btn-light nf-recent-pagination-button",
                                **{"aria-label": "Newer jobs"},
                                disabled=next_disabled,
                            ),
                        ],
                        className="nf-recent-pagination-buttons",
                    ),
                ],
                className="nf-recent-pagination",
            ),
            dbc.Table(
                [
                    html.Thead(html.Tr([html.Th(header) for header in headers])),
                    html.Tbody(rows),
                ],
                hover=True,
                responsive=True,
                className="nf-recent-table align-middle mb-0",
            ),
        ],
        className="nf-recent-table-wrap",
    )
