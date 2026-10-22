"""Callbacks for the route-driven job workspace.

The route names both the job and the section, so a section's components exist
only while its route is open. Dash then declines to run callbacks whose inputs
are absent, which is the lazy loading the Workflows page implements by hand in
ten callbacks' worth of panel-visibility checks.

Two consequences worth keeping in mind when reading this file:

* The job list is rendered whole on every poll. That is safe only because the
  selection lives in the URL, so replacing the rows cannot disturb which job is
  open. On the Workflows page the same poll had to preserve component state.
* Feature selection has exactly one resolver. `resolve_feature_selection` reads
  the dropdown, slider and map, decides which won, and writes a store that the
  plot reads. The Workflows page computes that decision twice.
"""
import collections
import math
import re
from datetime import datetime

import dash
import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, dash_table, dcc, html

from callbacks.workflows.analysis import (
    _corrected_rt_summary,
    _eic_archive_options,
    _eic_slider_marks,
    _feature_index_from_map_click,
)
from callbacks.workflows.monitoring import _job_can_resume
from services import metro_service
from services import sample_strip
from services import workflow_service
from services.api_client import APIError
from services.page_cache import build_page_cache, page_cache_matches
from ui.components import REVIEW_TABLE_STYLE, render_table
from ui.figures import (
    build_eic_figure,
    build_ms2_mirror_figure,
    build_feature_map_figure,
    build_is_qc_trace_figure,
    empty_feature_map_figure,
)
from ui.formatting import format_bytes
from ui.workflow_studio import _duration, _progress_bar, _run_status_badge, _task_progress


JOB_ROUTE_PREFIX = "jobs"
SECTIONS = ("overview", "isqc", "review", "eic", "outputs")
SECTION_LABELS = {
    "overview": "Overview",
    "outputs": "Outputs",
    "isqc": "IS-QC review",
    "review": "Feature review",
    "eic": "Aligned EIC",
}

# Chromatogram above, feature map below, both on screen at once. The builders
# use 460 and 360; these keep that ratio at about four fifths of the size, since
# equal heights made the chromatogram look squat next to the map.
EIC_TRACE_HEIGHT = 360
EIC_MS2_HEIGHT = 300
EIC_MAP_HEIGHT = 280


def _package_bundle(value):
    if isinstance(value, str) and value.startswith(("bundle:", "engine:")):
        _, job_id, preview_id = value.split(":")
        return int(job_id), int(preview_id)
    return None


def _eic_search_catalogue(features, archive):
    """Compact browser search rows, matching the API's tokens and numeric formats."""
    options = []
    for feature in features:
        fields = [feature.get(key) for key in ("label", "metabolite_name", "adduct_type", "feature_index", "alignment_id")]
        for key, precisions in (("average_mz", (5, 4, 3, 2)), ("average_rt", (2, 1))):
            if feature.get(key) is not None:
                fields.extend(f"{feature[key]:.{precision}f}" for precision in precisions)
        flags = {"reference": feature.get("has_reference"),
                 "unmatched": feature.get("annotated") and feature.get("has_ms2") and feature.get("msms_matched") is False,
                 "lowscore": feature.get("low_score")}
        options.append({"label": feature.get("label"),
                        "value": feature.get("feature_id", feature.get("feature_index")),
                        "search": " ".join(str(field).lower() for field in fields if field is not None),
                        "flags": [name for name, enabled in flags.items() if enabled]})
    return {"archive": archive, "options": options}


def sized(figure, height: int):
    """Resize a shared figure builder's output for this page.

    The builders in ui/figures.py set their own layout height, and plotly honours
    that over the container's CSS height, so constraining the div alone makes the
    plot overflow whatever follows it. The height has to be set on the figure.

    Height only. The margins are the builder's business: the chromatogram puts
    its title and a horizontal legend above the plot area at y=1.02, and both
    live in the top margin, so shrinking it drops the legend onto the traces.
    """
    figure.update_layout(height=height)
    return figure


ACTIVE_STATUSES = {"queued", "submitted", "running"}
DELETABLE_STATUSES = {"succeeded", "failed"}

# Bootstrap colour per status, so the square in the picker and the badge in the
# overview always agree about what a colour means.
STATUS_COLOURS = {
    "succeeded": "success",
    "failed": "danger",
    "cancelled": "secondary",
    "running": "info",
    "queued": "warning",
    "submitted": "warning",
    "preparing": "warning",
}


# --------------------------------------------------------------------------
# route
# --------------------------------------------------------------------------

def parse_job_route(pathname: str | None) -> tuple[int | None, str]:
    """Return (job_id, section) for `/jobs/<id>[/<section>]`.

    Parsed directly rather than through `dash.strip_relative_path`, which needs a
    live app context and so cannot be unit tested.
    """
    parts = [p for p in (pathname or "").split("/") if p]
    if not parts or parts[0] != JOB_ROUTE_PREFIX or len(parts) > 3:
        return None, "overview"
    if len(parts) == 1:
        return None, "overview"
    try:
        job_id = int(parts[1])
    except ValueError:
        return None, "overview"
    section = parts[2] if len(parts) == 3 else "overview"
    if section not in SECTIONS:
        return job_id, "overview"
    return job_id, section


def job_id_from_pathname(pathname: str | None) -> int | None:
    return parse_job_route(pathname)[0]


def job_href(job_id: int | str, section: str = "overview") -> str:
    if section == "overview":
        return f"/{JOB_ROUTE_PREFIX}/{job_id}"
    return f"/{JOB_ROUTE_PREFIX}/{job_id}/{section}"


# --------------------------------------------------------------------------
# list
# --------------------------------------------------------------------------

def active_job_count(jobs: list[dict] | None) -> int:
    return sum(1 for j in jobs or [] if str(j.get("status") or "").lower() in ACTIVE_STATUSES)


IS_QC_COLUMNS = (("target_id", "ID"), ("target_name", "Standard"), ("qc", "QC"),
                 ("corrected_rt", "Corrected RT (min)"))
IS_QC_TRACE_HEIGHT = 310


def _number(value) -> float | None:
    """A finite float from an IS-QC table cell, which arrives as TSV text."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def group_is_qc_rows(rows: list[dict] | None) -> list[tuple[str, str, list[dict]]]:
    """Group the flat IS-QC rows by standard, as (target_id, target_name, rows)."""
    grouped: dict[str, list[dict]] = {}
    for row in rows or []:
        key = str(row.get("target_id") or "") or f"name:{row.get('target_name') or ''}"
        grouped.setdefault(key, []).append(row)
    out = []
    for compound_rows in grouped.values():
        first = compound_rows[0]
        target_id = str(first.get("target_id") or "")
        out.append((target_id, first.get("target_name") or target_id, compound_rows))
    return out


def _is_qc_row_id(target_id: str, target_name: str) -> str:
    return target_id or f"name:{target_name}"


def is_qc_standards(rows: list[dict] | None) -> list[dict]:
    """The standards in table order: what the selection callbacks read.

    Kept to identities so the store stays small. The table's `selected_rows`
    are indices into this list, since the table lists standards in this order.
    """
    return [{"id": _is_qc_row_id(target_id, target_name), "target_id": target_id, "target_name": target_name}
            for target_id, target_name, _ in group_is_qc_rows(rows)]


def is_qc_table_data(rows: list[dict] | None, summary: dict | None = None) -> list[dict]:
    """One table row per standard.

    The verdict is the summary's, which applies the run's sample-pass
    threshold; counting passing samples is the fallback for a summary that
    lacks per-standard statuses.
    """
    statuses = (summary or {}).get("target_statuses") or {}
    data = []
    for target_id, target_name, compound_rows in group_is_qc_rows(rows):
        passing = sum(str(r.get("status") or "").lower() == "pass" for r in compound_rows)
        verdict = str((statuses.get(target_name) or {}).get("status")
                      or ("pass" if passing == len(compound_rows) else "fail")).lower()
        data.append({
            "id": _is_qc_row_id(target_id, target_name),
            "target_id": target_id,
            "target_name": target_name,
            "status": verdict,
            "qc": f"{verdict} · {passing}/{len(compound_rows)}",
            "corrected_rt": _corrected_rt_summary(compound_rows),
        })
    return data


def selected_target_ids(selected_rows: list[int] | None, standards: list[dict] | None) -> list[str]:
    """Target ids of the ticked rows, in table order."""
    standards = standards or []
    return [standards[i]["target_id"] for i in sorted(selected_rows or [])
            if 0 <= i < len(standards) and standards[i].get("target_id")]


def is_qc_state_line(summary: dict | None, standards: int, in_model: int) -> str:
    """The board's status line, like the feature review's revision line."""
    summary = summary or {}
    counts = summary.get("counts") or {}
    parts = []
    if summary.get("overall_status"):
        parts.append(f"IS-QC {summary['overall_status']}")
    if counts.get("passing_targets") is not None:
        parts.append(f"{counts['passing_targets']}/{counts.get('targets', standards)} standards passed")
    else:
        parts.append(f"{standards} standards")
    parts.append(f"{in_model} in the RT model")
    return " · ".join(parts)


def is_qc_evidence(target_name: str, rows: list[dict] | None) -> list:
    """The selected standard's facts and per-sample results.

    Laid out as the feature review lays out a feature: name, a muted line of
    identifiers, the verdict, then the reasons behind it in a disclosure that
    opens by itself when a sample failed.
    """
    compound_rows = [r for r in rows or [] if (r.get("target_name") or "") == target_name]
    if not compound_rows:
        return [html.H6(target_name)]
    first = compound_rows[0]
    facts = [f"ID {first['target_id']}"] if first.get("target_id") else []
    mz, rt = _number(first.get("expected_mz")), _number(first.get("expected_rt_min"))
    if mz is not None:
        facts.append(f"m/z {mz:.5f}")
    if rt is not None:
        facts.append(f"expected RT {rt:.2f} min")
    passing = sum(str(r.get("status") or "").lower() == "pass" for r in compound_rows)
    samples = []
    for row in compound_rows:
        parts = [str(row.get("status") or "unknown")]
        observed, error = _number(row.get("observed_rt_min")), _number(row.get("rt_error_min"))
        if observed is not None:
            parts.append(f"RT {observed:.3f} min" + (f" ({error:+.3f})" if error is not None else ""))
        if row.get("message") and row["message"] != "OK":
            parts.append(row["message"])
        samples.append(html.Li(f"{row.get('sample_name') or row.get('input_file') or 'sample'}: {' · '.join(parts)}"))
    return [
        html.H6(target_name),
        html.P(" · ".join(facts), className="small text-muted"),
        html.P(f"QC: {passing} / {len(compound_rows)} samples passing"),
        html.Details([html.Summary("Per-sample QC results"), html.Ul(samples)],
                     open=passing < len(compound_rows)),
    ]


JOB_LIST_FIELDS = ("id", "status", "run_name", "execution_profile",
                   "created_at", "started_at", "finished_at", "updated_at")


def job_list_row(job: dict) -> dict:
    """The fields the picker and the job-change fingerprint read.

    Full records carry manifests and status payloads. The store keeps those only
    for the open job, since Dash uploads it with every callback that reads it.
    """
    return {key: job[key] for key in JOB_LIST_FIELDS if job.get(key) is not None}


def _same_job(job: dict | None, job_id: int | None) -> bool:
    try:
        return job is not None and job_id is not None and int(job.get("id")) == int(job_id)
    except (TypeError, ValueError):
        return False


def job_from_store(store: dict | None, job_id: int | None) -> dict | None:
    """The open job's full record, from the store the picker already filled.

    Every reader goes through here rather than calling the API again, which is
    what keeps a job switch to a single request. The list rows are not a
    fallback: they lack the manifest and payload these readers decide from.
    """
    job = (store or {}).get("open_job")
    return job if _same_job(job, job_id) else None


def status_colour(status: str | None) -> str:
    """Bootstrap colour name for a job status, shared by the square and the badge."""
    return STATUS_COLOURS.get(str(status or "").strip().lower(), "light")


def compact_launch_time(job: dict) -> str:
    """`02 Sep 14:07` — enough to tell two runs apart at a glance.

    Falls back to the creation time for a job that never started, and adds the
    year only when it is not the current one, so the common case stays short.
    """
    raw = job.get("started_at") or job.get("created_at")
    if not raw:
        return ""
    try:
        moment = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return ""
    if moment.year != datetime.now(moment.tzinfo).year:
        return moment.strftime("%d %b %y")
    return moment.strftime("%d %b %H:%M")


_RUN_NAME_CONTEXT = re.compile(r"^std\d+_(?:assay\d+_)?", re.IGNORECASE)


def short_run_name(job: dict) -> str:
    """`std9_assay18_job130` -> `job130`.

    Every job in the picker belongs to the selected study and assay, and the
    sidebar's Current Context already names both with their ids, so repeating
    them on each row is noise. The full name stays in the row's hover text.

    Only this exact prefix is stripped; a run named any other way is left alone.
    """
    run_name = job.get("run_name") or ""
    trimmed = _RUN_NAME_CONTEXT.sub("", run_name).strip("_")
    return trimmed or run_name or f"job_{job.get('id')}"


def status_square(status: str | None) -> html.Span:
    return html.Span(
        className=f"bg-{status_colour(status)}",
        style={"width": "9px", "height": "9px", "borderRadius": "2px",
               "flexShrink": "0", "display": "inline-block"},
    )


def job_list_items(jobs: list[dict] | None, section: str = "overview", selected_job_id: int | None = None) -> list:
    """One NavLink per job: just enough to recognise and pick one.

    Deliberately minimal. Progress, duration, profile and task counts live in the
    overview section, which is a click away and has the width for them. Putting
    them here forced the picker column wide enough to crowd the detail pane.
    """
    rows = []
    for job in jobs or []:
        job_id = job.get("id")
        status = str(job.get("status") or "unknown").strip().lower()
        full_name = job.get("run_name") or f"job_{job_id}"
        when = compact_launch_time(job)
        hover = " · ".join(p for p in [full_name, status, job.get("execution_profile"), when] if p)
        rows.append(
            dbc.NavLink(
                html.Div(
                    [
                        status_square(status),
                        html.Span(short_run_name(job), className="text-truncate flex-grow-1"),
                        html.Span(when, className="small text-muted flex-shrink-0"),
                    ],
                    className="d-flex align-items-center gap-2",
                    # The full status reads on hover; the square carries it at a glance.
                    title=hover,
                ),
                href=job_href(job_id, section),
                active=job_id == selected_job_id,
                className="jw-job-link py-1 px-2",
            )
        )
    return rows


def section_nav(job_id: int | None, section: str, job: dict | None = None,
                outputs_store: dict | None = None) -> list:
    if job_id is None:
        return []
    descriptor = ((((job or {}).get('input_manifest_json') or {}).get('engine') or {}).get('descriptor'))
    params = (job or {}).get("parameter_values_json") or {}
    payload = (job or {}).get("status_payload_json") or {}
    outputs = ((outputs_store or {}).get("outputs") or []) if (outputs_store or {}).get("job_id") == job_id else []
    is_qc_available = (
        params.get("workflow_stage") == "is_qc_review"
        or params.get("apply_rt_correction") is True
        or bool(payload.get("is_qc_summary"))
        or any(row.get("artifact_role") == "is_qc_summary" for row in outputs)
    )
    # The margin belongs to the nav, not to a wrapper, so the detail pane sits
    # flush against the top of the card when no job is open.
    return [
        dbc.Nav(
            [
                dbc.NavLink(SECTION_LABELS[s], href=None if s == "isqc" and not is_qc_available else job_href(job_id, s),
                            active=(s == section),
                            id=f"jw-{s}-nav",
                            disabled=(s == "isqc" and not is_qc_available))
                for s in SECTIONS
                if s != 'isqc' or descriptor is None or 'is_qc_review' in descriptor.get('capabilities', [])
            ],
            pills=True,
            className="mb-3",
        )
    ]


# --------------------------------------------------------------------------
# section views
# --------------------------------------------------------------------------

def overview_view(job: dict, task_rows=None, samples: list[dict] | None = None) -> list:
    status = str(job.get("status") or "").strip().lower()
    run_id = job.get("run_name") or f"job_{job.get('id')}"
    tasks, percent = _task_progress(job)
    header = html.Div(
        [
            html.Div([html.Span(run_id, className="section-title me-3"), _run_status_badge(job.get("status"))]),
            dcc.ConfirmDialogProvider(
                dbc.Button("Delete", color="danger", outline=True, size="sm",
                           disabled=status not in DELETABLE_STATUSES),
                id="jw-delete-confirm",
                message=f"Delete {run_id} from MetaboLens? Its generated files can be purged after the retention period.",
            ),
        ],
        className="d-flex justify-content-between align-items-center mb-3",
    )
    facts = dbc.Row(
        [
            dbc.Col([html.Div("Profile", className="small text-muted"),
                     html.Div(job.get("execution_profile") or "—")], md=3),
            dbc.Col([html.Div("External run", className="small text-muted"),
                     html.Div(job.get("external_run_id") or "—")], md=3),
            dbc.Col([html.Div("Duration", className="small text-muted"),
                     html.Div(_duration(job.get("started_at"), job.get("finished_at")))], md=3),
            dbc.Col([html.Div("Tasks", className="small text-muted"),
                     html.Div([_progress_bar(percent), html.Span(tasks, className="small ms-1")])], md=3),
        ],
        className="mb-3 g-2",
    )
    error = job.get("error_message")
    blocks = [header, facts]
    if error:
        blocks.append(html.Pre(str(error)[:1200], className="status-box text-danger small"))
    blocks.extend(metro_map(job, task_rows))
    is_preview = (job.get('parameter_values_json') or {}).get('workflow_stage') == 'qc_preview'
    can_resume, reason = _job_can_resume(job)
    blocks.append(html.Div([
        html.P(reason, className='small text-muted'),
        dbc.Button('Review feature decisions', href=job_href(job['id'], 'review'), color='primary', className='me-2'),
        dbc.Button('Finalize with automatic decisions', id='jw-auto-continue-btn',
                   n_clicks=0, color='success', disabled=not (is_preview and can_resume)),
        html.Span(id='jw-auto-continue-status', className='small ms-3'),
    ], className='my-3', style={} if is_preview else {'display': 'none'}))
    blocks.extend(sample_overview(job, samples))
    return blocks


def artifact_file_id(outputs: list[dict] | None, role: str) -> int | None:
    for output in outputs or []:
        if output.get("artifact_role") == role:
            return output.get("file_id")
    return None


def trace_file_id(outputs: list[dict] | None) -> int | None:
    return artifact_file_id(outputs, "execution_trace")


SAMPLE_TYPE_LABELS = {"sample": "study", "qc": "QC", "blank": "blank", "other": "other"}


def sample_marks(samples: list[dict]) -> html.Div:
    """One bar per acquisition, laid out left to right in injection order.

    Built from HTML rather than an inline SVG so each bar carries a native
    tooltip: the sample name, its group, batch, size and whether it is the RT
    reference. A strip with no way to ask which bar is which is decoration.
    """
    largest = max((s["size_bytes"] or 0) for s in samples) or 1
    marks = []
    for index, sample in enumerate(samples):
        if index and sample["batch"] != samples[index - 1]["batch"]:
            marks.append(html.Div(style={
                "width": "1px", "alignSelf": "stretch",
                "borderLeft": "1px dashed var(--strip-rule)",
                "marginInline": f"{sample_strip.GAP}px",
            }))
        marks.append(html.Div(
            style={
                "width": f"{sample_strip.BAR_W}px",
                "height": f"{sample_strip.bar_height(sample, largest)}px",
                "background": f"var(--sample-{sample['type']})",
                # Rounded data-end, square at the baseline.
                "borderRadius": "3px 3px 0 0",
                "flexShrink": "0",
            },
            title=sample_strip.hover_text(sample),
        ))
    return html.Div(
        marks,
        style={
            "display": "flex", "alignItems": "flex-end",
            "gap": f"{sample_strip.GAP}px",
            "height": f"{sample_strip.BAR_MAX + 6}px",
            "borderBottom": "1px solid var(--strip-rule)",
            "paddingBottom": "2px", "minWidth": "min-content",
        },
        role="img",
        **{"aria-label": sample_strip.summary_text(samples)},
    )


def sample_overview(job: dict, samples: list[dict] | None) -> list:
    """The run's acquisitions, in injection order, as one strip.

    Height is RAW file size and colour is sample type, so batch layout and
    acquisition completeness are both read in one glance without an axis.
    """
    if not samples:
        return []
    tally = sample_strip.type_tally(samples)
    short = sample_strip.short_acquisitions(samples)
    legend = [
        html.Span(
            [html.Span(style={"width": "9px", "height": "9px", "borderRadius": "2px",
                              "display": "inline-block", "flexShrink": "0",
                              "background": f"var(--sample-{kind})"},
                       className="me-1"),
             f"{count} {SAMPLE_TYPE_LABELS.get(kind, kind)}"],
            className="small text-muted me-3 d-inline-flex align-items-center",
        )
        for kind, count in tally
    ]
    blocks = [
        html.Div(
            [html.Span(f"{len(samples)} acquisitions", className="fw-semibold small me-3"), *legend],
            className="d-flex align-items-center mt-4 mb-1 flex-wrap",
        ),
        html.Div(sample_marks(samples), className="jw-sample-strip",
                 style={"overflowX": "auto"}),
        html.Div("Injection order, left to right. Bar height is RAW file size.",
                 className="text-muted", style={"fontSize": "0.72rem"}),
    ]
    if short:
        blocks.append(html.Div(
            "Unusually small file(s): " + ", ".join(s["name"] for s in short[:4])
            + " — check the acquisition completed.",
            className="text-warning", style={"fontSize": "0.75rem"},
        ))
    return blocks


def metro_map(job: dict, task_rows=None) -> list:
    """The pipeline route map with this run's stations coloured.

    `tasks` is the complete run when the caller fetched the registered execution
    trace. Falling back to the job's status payload keeps the map working
    without it, but that payload retains only the last eight tasks, so a full
    run loses the MS-DIAL stages that ran first.
    """
    payload = job.get("status_payload_json") or {}
    if task_rows is None:
        task_rows = metro_service.payload_tasks(payload)
    # The worker's states are the only source that knows what is running; the
    # trace rows only ever describe tasks that already ended.
    live_states = metro_service.process_states_from_payload(payload)
    descriptor = ((job.get('input_manifest_json') or {}).get('engine') or {}).get('descriptor') or {}
    stage_definitions = descriptor.get('stages') or None
    # A descriptor-backed run is drawn on the nf-metro map that carries all of
    # its stages; only an engine with no drawn map gets the plain stage list.
    drawn = metro_service.engine_map(stage_definitions)
    if drawn:
        svg_path, stage_definitions = drawn
    snapshot = metro_service.station_snapshot(task_rows, live_states, station_definitions=stage_definitions)
    states = metro_service.snapshot_states(snapshot)
    process_states = {**metro_service.roll_up(task_rows), **live_states}
    # The map is drawn even with nothing coloured. During materialisation, which
    # can run a couple of minutes before the first task starts, an empty panel
    # read as a failure; the uncoloured route is the useful thing to show.
    tally = collections.Counter(states.values())
    legend = [
        html.Span(
            [html.Span("●", className="me-1",
                       style={"color": metro_service.STATE_COLOURS[state]}),
             f"{tally[state]} {state}"],
            className="small text-muted me-3",
        )
        for state in ("done", "running", "queued", "failed") if tally.get(state)
    ]
    finished = sum(e["done"] for e in snapshot.values())
    scheduled = sum(e["total"] for e in snapshot.values())
    if scheduled:
        legend.append(html.Span(f"{finished}/{scheduled} tasks",
                                className="small text-muted"))
    if not states:
        legend = [html.Span("waiting for the first task", className="small text-muted")]
    unmatched = metro_service.unmatched_processes(process_states, station_definitions=stage_definitions)
    blocks = [
        html.Div(
            [html.Span("Pipeline route map", className="fw-semibold small me-3"), *legend],
            className="d-flex align-items-center mt-4 mb-1 flex-wrap",
        ),
        html.Img(src=(metro_service.data_uri(states, svg_path) if drawn
                      else metro_service.engine_stage_data_uri(stage_definitions, snapshot)
                      if stage_definitions else metro_service.data_uri(states)), className="w-100",
                 style={"maxHeight": "420px", "objectFit": "contain"},
                 alt="Workflow route map with this run's completed, running and failed stations coloured."),
    ]
    if unmatched:
        # Named rather than hidden: after the R pipeline is replaced these are
        # the processes still waiting for a station pattern.
        blocks.append(html.Div(
            f"Not on the map: {', '.join(unmatched[:6])}",
            className="text-muted", style={"fontSize": "0.75rem"},
        ))
    return blocks


def outputs_view(outputs: list[dict] | None) -> list:
    if not outputs:
        return [html.Div("No registered outputs for this job yet.", className="text-muted")]

    primary_roles = {
        role: rank for rank, role in enumerate((
            "metabolensr_report", "metabolensr_normalized_raw", "metabolensr_normalized_tic",
            "metabolensr_normalized_pqn", "metabolensr_normalized_bmis",
        ))
    }

    def name(output):
        return str(output.get("original_filename") or output.get("uri") or "").rsplit("/", 1)[-1]

    primary = sorted((o for o in outputs if o.get("artifact_role") in primary_roles),
                     key=lambda o: (primary_roles[o["artifact_role"]], name(o).lower()))
    supporting = sorted((o for o in outputs if str(o.get("artifact_role") or "").startswith("metabolensr_")
                         and o.get("artifact_role") not in primary_roles),
                        key=lambda o: (o.get("artifact_role") or "", name(o).lower()))
    other = sorted((o for o in outputs if not str(o.get("artifact_role") or "").startswith("metabolensr_")),
                   key=lambda o: (o.get("artifact_role") or "", name(o).lower()))

    def table(items):
        rows = [[
            o.get("artifact_role") or "—", name(o) or "—",
            format_bytes(o.get("file_size_bytes")), (o.get("checksum_sha256") or "")[:12] or "—",
            dbc.Button('Download', id={'type': 'jw-output-download-btn', 'file': o['file_id']},
                       n_clicks=0, color='link', size='sm') if o.get('file_id') else '',
        ] for o in items]
        output_table = render_table(["Role", "File", "Size", "SHA-256", "Download"], rows)
        output_table.className += " jw-output-table"
        return output_table

    view = [html.Div(f"{len(outputs)} registered output(s)", className="fw-semibold mb-2")]
    if primary:
        view.extend([html.H5("Key results", className="mt-3"), table(primary)])
    if supporting:
        view.extend([html.H5("MetaboLensR supporting files", className="mt-3"), table(supporting)])
    if other:
        if primary or supporting:
            count = len(other)
            view.append(html.Details([
                html.Summary(f"{count} other registered file{'s' if count != 1 else ''}"),
                table(other),
            ], className="mt-3"))
        else:
            view.append(table(other))
    return view


def _ms2_evidence(ms2: dict) -> list:
    """Native annotation scores and explicit match verdicts beside the mirror plot."""
    if not ms2:
        return []
    scores = ms2.get("scores") or {}

    def number(value) -> str:
        if value is None:
            return 'n/a'
        return f"{value:.2f}" if isinstance(value, (int, float)) else str(value)

    flags = (("rt_matched", "RT"), ("mz_matched", "m/z"), ("msms_matched", "MS2"))
    labels = {'weighted_dot_product': 'weighted dot', 'reverse_dot_product': 'reverse dot',
              'matched_peaks_percentage': 'matched peaks'}
    score_details = [
        f"{labels.get(key, key.replace('_', ' '))} {number(value)}"
        + ('%' if key == 'matched_peaks_percentage' and value is not None else '')
        for key, value in scores.items() if key not in dict(flags)
    ]

    parts = [
        html.Div(
            " · ".join(
                f"{label} {'matched' if scores.get(key) is True else 'not matched' if scores.get(key) is False else 'unknown'}"
                for key, label in flags
            ),
            className="fw-semibold",
        ),
        html.Div(
            (ms2.get('provider') or 'Native annotation evidence')
            + (': ' + ' · '.join(score_details) if score_details else '')
        ),
    ]

    tail: list[str] = []
    if ms2.get("reference_rt") is not None:
        tail.append(f"reference RT {ms2['reference_rt']:.2f} min")
    reference = ms2.get("reference") or {}
    if reference.get("name"):
        tail.append(f"reference {reference['name']}")
    elif ms2.get("available"):
        tail.append("no library reference for this compound")
    if ms2.get("library_filename"):
        tail.append(f"library {ms2['library_filename']}")
    if tail:
        parts.append(html.Div(" · ".join(tail)))
    return parts


def eic_view() -> list:
    """The aligned EIC viewer.

    These components exist only on `/jobs/<id>/eic`, so their callbacks run only
    while this route is open. No visibility flag is consulted anywhere.
    """
    return [
        dbc.Row(
            [
                dbc.Col(
                    [
                        html.Div("Feature bundle / EIC archive", className="small text-muted"),
                        dcc.Dropdown(id="jw-eic-archive", options=[], value=None, clearable=False),
                        html.Div(id="jw-eic-archive-status", className="small text-muted mt-1"),
                    ],
                    md=7,
                ),
                dbc.Col(
                    [
                        html.Div("Feature", className="small text-muted"),
                        dcc.Dropdown(id="jw-eic-feature", options=[], value=None, placeholder="Search name, m/z, RT"),
                    ],
                    md=5,
                ),
            ],
            className="g-2 mb-3",
        ),
        html.Div(
            [
                html.Div("Browse features by position (not source ID)", className="small text-muted"),
                dcc.Slider(id="jw-eic-slider", min=0, max=0, step=1, value=0, marks={0: "0"},
                           tooltip={"placement": "bottom"}),
            ],
            className="mb-3",
        ),
        # Stacked, not side by side: both plots share the retention-time axis, so
        # reading them one above the other lines up RT between the map and the
        # chromatogram. Side by side halves the width of both for no gain.
        html.Div(
            [
                dcc.Loading(html.Div(id="jw-eic-graph-shell"), type="dot"),
                html.Div(id="jw-eic-plot-status", className="small text-muted"),
            ],
            className="mb-4",
        ),
        html.Div(
            [
                # Both plots are sized so the chromatogram and the map are on
                # screen together; plotly's default 450px put the map below the
                # fold and made comparing them a scroll.
                dcc.Graph(
                    id="jw-eic-map",
                    figure=sized(empty_feature_map_figure(), EIC_MAP_HEIGHT),
                    config={"displayModeBar": False, "responsive": True},
                ),
                html.Div(id="jw-eic-map-status", className="small text-muted"),
            ]
        ),
        # Below the map, not between it and the chromatogram: those two share a
        # retention-time axis and are read against each other. The spectrum has
        # an m/z axis and answers a different question about the same feature.
        html.Div(
            [
                html.Div("MS2 spectrum", className="small text-muted mt-4"),
                dcc.Loading(html.Div(id="jw-eic-ms2-shell"), type="dot"),
            ]
        ),
        dcc.Store(id="jw-eic-feature-store", data=None),
        dcc.Store(id="jw-eic-context-store", data=None),
        dcc.Store(id="jw-eic-search-store", data=None),
    ]


def is_qc_view() -> list:
    """IS-QC review, laid out like the feature review board.

    The same skeleton on purpose: a title, a status line, the standards table
    on the left, the selected standard's evidence on the right, and the action
    that continues the run underneath. A row's tick puts that standard in the
    RT model; clicking the row shows its traces.
    """
    return [
        dcc.Store(id="jw-isqc-standards-store", data=None),
        dcc.Store(id="jw-isqc-target-store", data=None),
        html.H5("Internal standard QC review"),
        html.P("Inspect each standard's traces, choose the standards for the RT model, then continue the run. "
               "Original RT is kept unless you apply the correction.", className="text-muted"),
        html.Div(id="jw-isqc-state", className="my-2"),
        dbc.Row([
            dbc.Col([
                dash_table.DataTable(
                    id="jw-isqc-table", data=[], columns=[{"name": label, "id": key} for key, label in IS_QC_COLUMNS],
                    row_selectable="multi", selected_rows=[], cell_selectable=True,
                    page_action="native", page_size=25,
                    **REVIEW_TABLE_STYLE,
                    style_data_conditional=[{"if": {"filter_query": "{status} = fail"},
                                             "backgroundColor": "var(--ml-warning-soft, #fff7e8)"}],
                ),
                html.Div(id="jw-isqc-count", className="small text-muted my-2"),
            ], md=5),
            dbc.Col([
                dbc.ButtonGroup([dbc.Button("Previous", id="jw-isqc-prev", n_clicks=0, outline=True),
                                 dbc.Button("Next", id="jw-isqc-next", n_clicks=0, outline=True)],
                                size="sm", className="mb-2"),
                html.Div(id="jw-isqc-nav-status", className="small text-muted"),
                html.Div(id="jw-isqc-evidence"),
                dbc.RadioItems(
                    id="jw-isqc-rt-view",
                    options=[{"label": "Original RT", "value": "original"},
                             {"label": "Corrected RT", "value": "corrected"}],
                    value="original",
                    inline=True,
                    class_name="small mb-1",
                ),
                dcc.Loading(html.Div(id="jw-isqc-trace"), type="dot"),
            ], md=7),
        ]),
        continue_controls(),
    ]


def continue_controls() -> html.Div:
    """The review checkpoint: decide on RT correction, then resume the job.

    Placed and coloured as the feature review's finalize action, with the
    resume gate's reason in the note beneath the button.
    """
    return html.Div(
        [
            dbc.Checkbox(id="jw-continue-rt", label="Apply the RT correction from the ticked standards",
                         value=False),
            dbc.Textarea(id="jw-continue-note", placeholder="Operator note (optional)", className="my-2"),
            dbc.Button("Continue with Nextflow resume", id="jw-continue-btn",
                       n_clicks=0, color="success", disabled=True),
            html.P(id="jw-continue-status", className="small text-muted mt-2"),
        ],
        className="mt-3",
    )


# --------------------------------------------------------------------------
# callbacks
# --------------------------------------------------------------------------

def register_callbacks(app):
    @app.callback(
        Output('jw-output-download', 'data'), Output('jw-output-download-status', 'children'),
        Input({'type': 'jw-output-download-btn', 'file': ALL}, 'n_clicks'),
        State('url', 'pathname'), State('jw-outputs-store', 'data'),
        State('token-store', 'data'), State('assay-select-store', 'data'),
        prevent_initial_call=True,
    )
    def download_registered_output(clicks, pathname, store, token, assay_id):
        job_id, _ = parse_job_route(pathname)
        trigger = dash.ctx.triggered_id
        if (not any(clicks or []) or not isinstance(trigger, dict) or not token or not assay_id
                or (store or {}).get('job_id') != job_id):
            return dash.no_update, dash.no_update
        output = next((row for row in store.get('outputs', []) if row.get('file_id') == trigger.get('file')), None)
        if output is None:
            return dash.no_update, 'This output is no longer available for the open job.'
        try:
            content, media_type = workflow_service.workflow_output_bytes(token, assay_id, output['file_id'])
        except (APIError, ValueError) as exc:
            return dash.no_update, f'Download failed: {exc}'
        filename = str(output.get('original_filename') or f"output-{output['file_id']}").rsplit('/', 1)[-1]
        return dcc.send_bytes(content, filename, type=media_type), ''

    # -- list -------------------------------------------------------------
    @app.callback(Output("sidebar-jobs", "hidden"), Input("url", "pathname"))
    def show_sidebar_jobs(pathname):
        """The sidebar job picker belongs to /jobs routes only."""
        path = str(pathname or "")
        return not (path == "/jobs" or path.startswith("/jobs/"))

    @app.callback(
        Output("jw-job-list", "children"),
        Output("jw-job-list-status", "children"),
        Output("jw-jobs-store", "data"),
        Input("token-store", "data"),
        Input("assay-select-store", "data"),
        Input("jw-refresh-btn", "n_clicks"),
        Input("jw-poll", "n_intervals"),
        Input("jw-delete-result", "data"),
        Input("jw-continue-result", "data"),
        Input("url", "pathname"),
        State("jw-jobs-store", "data"),
    )
    def load_job_list(token, assay_id, _clicks, _ticks, _deleted, _continued, pathname, current):
        """The one place that fetches the job list.

        Everything else that needs a job reads `jw-jobs-store` rather than
        fetching again, so switching job costs one request instead of three.

        `url` is an Input because the rows link into whichever section the reader
        is in; as a State those links went stale after a section change. Rows are
        only re-rendered when the jobs, section or selected job differ; unchanged
        polling leaves the list alone. Selection follows the job across tabs.
        """
        def unchanged_or(payload, rows, status):
            if current == payload:
                return dash.no_update, dash.no_update, dash.no_update
            return rows, status, payload

        if not token:
            return unchanged_or({"jobs": [], "section": None, "error": "auth"}, [], "Login first.")
        if not assay_id:
            return unchanged_or({"jobs": [], "section": None, "error": "assay"}, [], "Select an assay.")
        job_id, section = parse_job_route(pathname)
        cache = (current or {}).get("cache")
        same_context = page_cache_matches(cache, token, assay_id=assay_id)
        if (same_context and not current.get("failed") and current.get("selected_job_id") == job_id
                and "open_job" in current and set(dash.ctx.triggered_prop_ids) == {"url.pathname"}):
            rows, open_job = current["jobs"], current["open_job"]
        else:
            try:
                jobs = workflow_service.list_workflow_jobs(token, assay_id)
            except (APIError, ValueError) as exc:
                return [], f"Job list failed: {exc}", {"jobs": [], "failed": True}
            rows = [job_list_row(job) for job in jobs]
            open_job = next((job for job in jobs if _same_job(job, job_id)), None)
        payload = {"jobs": rows, "open_job": open_job, "section": section, "selected_job_id": job_id,
                   "cache": cache if same_context else build_page_cache(None, token, assay_id=assay_id)}
        if not rows:
            return unchanged_or(payload, [], "No jobs yet.")
        active = active_job_count(rows)
        # One short line under the sidebar title.
        status = f"{len(rows)} jobs" + (f" · {active} live" if active else "")
        return unchanged_or(payload, job_list_items(rows, section, job_id), status)

    # -- job scope: outputs fetched once, tagged with the job they belong to
    @app.callback(
        Output("jw-outputs-store", "data"),
        Input("url", "pathname"),
        Input("token-store", "data"),
        Input("assay-select-store", "data"),
        Input("jw-poll", "n_intervals"),
        State("jw-outputs-store", "data"),
    )
    def load_job_outputs(pathname, token, assay_id, _ticks, current):
        """Outputs for the open job, rewritten only when they actually change.

        This store feeds the EIC archive dropdown, which feeds the feature
        selection, which feeds the plot. Writing an equal-but-new object on every
        poll would walk that whole chain and reset the reader's selection every
        fifteen seconds, so an unchanged result must return no_update.
        """
        job_id, _ = parse_job_route(pathname)
        if job_id is None or not token or not assay_id:
            return None if current is not None else dash.no_update
        cache = (current or {}).get("cache")
        same_context = page_cache_matches(cache, token, assay_id=assay_id)
        if (same_context and not current.get("failed") and current.get("job_id") == job_id
                and set(dash.ctx.triggered_prop_ids) == {"url.pathname"}):
            return dash.no_update
        try:
            outputs = workflow_service.list_workflow_output_files(token, assay_id, job_id)
        except (APIError, ValueError):
            payload = {"job_id": job_id, "outputs": [], "failed": True}
        else:
            payload = {"job_id": job_id, "outputs": outputs,
                       "cache": cache if same_context else build_page_cache(None, token, assay_id=assay_id)}
        if current == payload:
            return dash.no_update
        return payload

    @app.callback(
        Output("jw-section-nav", "children"),
        Input("url", "pathname"),
        Input("token-store", "data"),
        Input("assay-select-store", "data"),
        Input("jw-jobs-store", "data"),
        Input("jw-outputs-store", "data"),
    )
    def render_section_nav(pathname, token, assay_id, store, outputs_store):
        # Capability changes update navigation without remounting the active view.
        job_id, section = parse_job_route(pathname)
        job = job_from_store(store, job_id)
        return section_nav(job_id, section, job, outputs_store) if token and assay_id and job else []

    @app.callback(
        Output("jw-detail", "children"),
        Input("url", "pathname"),
        Input("token-store", "data"),
        Input("assay-select-store", "data"),
        Input("jw-delete-result", "data"),
    )
    def render_job_detail(pathname, token, assay_id, _deleted):
        """Mount a section's shell. Deliberately not driven by the poll.

        Re-rendering this would rebuild the section's components, discarding
        whatever the reader had selected inside it. Live data belongs in the
        panes below, which replace their own contents.
        """
        job_id, section = parse_job_route(pathname)
        if job_id is None:
            return "Select a job from the list."
        if not token or not assay_id:
            return "Login and select an assay first."
        if section == "review":
            from callbacks.feature_review import review_view
            return review_view()
        if section == "eic":
            return eic_view()
        if section == "isqc":
            return is_qc_view()
        if section == "outputs":
            return html.Div(id="jw-outputs-pane")
        return html.Div(id="jw-overview-pane")

    @app.callback(
        Output("jw-overview-pane", "children"),
        Input("url", "pathname"),
        Input("jw-jobs-store", "data"),
        Input("jw-outputs-store", "data"),
        State("token-store", "data"),
        State("assay-select-store", "data"),
    )
    def render_overview_pane(pathname, store, outputs_store, token, assay_id):
        # Reads the store the list already filled. The poll is not an input here:
        # a tick refreshes the store, and that refresh is what redraws this pane.
        job_id, _ = parse_job_route(pathname)
        if job_id is None:
            return html.Div("Select a job.", className="text-muted")
        job = job_from_store(store, job_id)
        if job is None:
            return html.Div("Loading…", className="text-muted")
        # The registered trace covers every task; the job payload keeps only the
        # last eight, which drops MS-DIAL on a full run.
        task_rows = None
        outputs = (outputs_store or {}).get("outputs") if (outputs_store or {}).get("job_id") == job_id else None
        file_id = trace_file_id(outputs)
        if file_id and token and assay_id:
            try:
                text = workflow_service.workflow_execution_trace(token, assay_id, file_id)
                task_rows = metro_service.trace_tasks(text)
            except (APIError, ValueError):
                task_rows = None
        # The samplesheet is the contract the run was launched with; the manifest
        # on the job record carries the file sizes it does not.
        samples = None
        sheet_id = artifact_file_id(outputs, "run_samplesheet")
        if sheet_id and token and assay_id:
            try:
                text = workflow_service.workflow_samplesheet(token, assay_id, sheet_id)
                samples = sample_strip.parse_samplesheet(
                    text, sample_strip.sizes_from_manifest(job.get("input_manifest_json")))
            except (APIError, ValueError):
                samples = None
        return overview_view(job, task_rows, samples)

    @app.callback(
        Output("jw-outputs-pane", "children"),
        Input("jw-outputs-store", "data"),
        Input("url", "pathname"),
    )
    def render_outputs_pane(store, pathname):
        job_id, _ = parse_job_route(pathname)
        if not store or store.get("job_id") != job_id:
            return html.Div("Loading outputs…", className="text-muted")
        return outputs_view(store.get("outputs"))

    @app.callback(
        Output("jw-delete-result", "data"),
        Input("jw-delete-confirm", "submit_n_clicks"),
        State("url", "pathname"),
        State("token-store", "data"),
        State("assay-select-store", "data"),
        prevent_initial_call=True,
    )
    def delete_open_job(submit_clicks, pathname, token, assay_id):
        # One open job means one fixed button id -- no pattern-matching callback,
        # which a row-per-job table would have needed.
        job_id, _ = parse_job_route(pathname)
        if not submit_clicks or job_id is None or not token or not assay_id:
            return dash.no_update
        try:
            workflow_service.delete_workflow_job(token, assay_id, job_id)
        except (APIError, ValueError) as exc:
            return {"job_id": job_id, "ok": False, "error": str(exc)}
        return {"job_id": job_id, "ok": True}

    # -- EIC viewer -------------------------------------------------------
    @app.callback(
        Output("jw-eic-archive", "options"),
        Output("jw-eic-archive", "value"),
        Output("jw-eic-archive-status", "children"),
        Input("jw-outputs-store", "data"),
        State("url", "pathname"),
    )
    def hydrate_eic_archives(store, pathname):
        job_id, _ = parse_job_route(pathname)
        if not store or store.get("job_id") != job_id:
            return [], None, "Loading job outputs…"
        outputs = store.get("outputs") or []
        finals = [output for output in outputs if output.get("artifact_role") == "metabolensr_manifest"]
        previews = [output for output in outputs if output.get("artifact_role") == "metabolensr_preview_manifest"]
        bundles = finals or previews
        title = "MetaboLensR final · retained for statistics" if finals else "MetaboLensR preview · all features"
        options = [{"label": f"{title} · file {row['file_id']}",
                    "value": f"bundle:{job_id}:{row['file_id']}"} for row in bundles]
        archives = _eic_archive_options(store.get("outputs"))
        options.extend({**option, "label": f"Original MS-DIAL archive · {option['label']}"}
                       for option in archives)
        if not archives:
            # The preview catalogue retains every native ID, including features
            # excluded by final statistics. Traces still use the engine adapter.
            options.extend({"label": f"Original engine · all aligned features · file {row['file_id']}",
                            "value": f"engine:{job_id}:{row['file_id']}"} for row in previews)
        if not options:
            return [], None, "This job registered no feature bundle or EIC archive."
        return options, options[0]["value"], (
            "MetaboLensR features use the selected engine's native chromatograms and spectra. "
            "Source IDs are the engine's original feature IDs; "
            "the slider shows browsing position."
        )

    @app.callback(
        Output("jw-eic-map", "figure"),
        Output("jw-eic-map-status", "children"),
        Output("jw-eic-slider", "max"),
        Output("jw-eic-slider", "marks"),
        Output("jw-eic-context-store", "data"),
        Output("jw-eic-search-store", "data"),
        Output("jw-eic-feature", "search_value"),
        Input("jw-eic-archive", "value"),
        State("token-store", "data"),
        State("assay-select-store", "data"),
    )
    def hydrate_eic_context(eic_file_id, token, assay_id):
        """One catalogue supplies the initial map, slider and complete dropdown.

        Search rows stay in the browser. Changing archive clears the old search;
        typing never uploads the catalogue or calls the feature API again.
        """
        if not eic_file_id or not token or not assay_id:
            return sized(empty_feature_map_figure(), EIC_MAP_HEIGHT), "Select a feature bundle or archive.", 0, {0: "0"}, None, None, None
        try:
            bundle = _package_bundle(eic_file_id)
            features = (workflow_service.get_package_feature_map(token, assay_id, *bundle) if bundle
                        else workflow_service.list_eic_features(token, assay_id, eic_file_id))
        except (APIError, ValueError) as exc:
            return (sized(empty_feature_map_figure("Feature map could not be loaded."), EIC_MAP_HEIGHT),
                    f"Feature map failed: {exc}", 0, {0: "0"}, None, None, None)
        mapped = sum(1 for f in features if f.get("average_rt") is not None and f.get("average_mz") is not None)
        ms2 = sum(1 for f in features if f.get("has_ms2"))
        references = sum(1 for f in features if f.get("has_reference"))
        count = len(features)
        if bundle and not features:
            message = ("No features available in this package view. Final bundles show only features retained "
                       "for statistics. Select the all aligned features source or original archive to inspect all engine features.")
            return (sized(empty_feature_map_figure(message), EIC_MAP_HEIGHT), message, 0, {0: "0"},
                    {"bundle": eic_file_id, "feature_ids": []}, _eic_search_catalogue([], eic_file_id), None)
        return (
            sized(build_feature_map_figure(features), EIC_MAP_HEIGHT),
            f"{mapped} feature(s) mapped; {ms2} with MS/MS; {references} with a library reference. "
            f"Filter the feature box with: reference · unmatched · lowscore",
            max(0, count - 1),
            _eic_slider_marks(count),
            {"bundle": eic_file_id, "feature_ids": [f["feature_id"] for f in features]} if bundle else None,
            _eic_search_catalogue(features, eic_file_id),
            None,
        )

    app.clientside_callback(
        dash.ClientsideFunction(namespace="eic", function_name="filterOptions"),
        Output("jw-eic-feature", "options"),
        Input("jw-eic-search-store", "data"),
        Input("jw-eic-feature", "search_value"),
        Input("jw-eic-feature-store", "data"),
        Input("jw-eic-archive", "value"),
    )

    @app.callback(
        Output("jw-eic-feature-store", "data"),
        Output("jw-eic-feature", "value"),
        Output("jw-eic-slider", "value"),
        Input("jw-eic-feature", "value"),
        Input("jw-eic-slider", "value"),
        Input("jw-eic-map", "clickData"),
        Input("jw-eic-archive", "value"),
        Input("jw-eic-context-store", "data"),
        prevent_initial_call=True,
    )
    def resolve_feature_selection(dropdown_index, slider_index, map_click, _archive, context=None):
        """The single place that decides which feature is selected.

        Returns `no_update` for whichever control raised the event, so writing the
        others back cannot re-trigger this callback through its own inputs.
        """
        trigger = dash.ctx.triggered_id
        if trigger == "jw-eic-archive":
            return None, None, 0
        bundle = _package_bundle(_archive)
        if bundle:
            identities = (context or {}).get("feature_ids") or []
            if (context or {}).get("bundle") != _archive or not identities:
                return dash.no_update, dash.no_update, dash.no_update
            if trigger == "jw-eic-context-store":
                identity = identities[0]
            elif trigger == "jw-eic-slider":
                position = int(slider_index or 0)
                identity = identities[position] if 0 <= position < len(identities) else None
            elif trigger == "jw-eic-map":
                points = (map_click or {}).get("points") or []
                data = points[0].get("customdata") if points else None
                identity = data[0] if isinstance(data, list) and data else None
            else:
                identity = dropdown_index
            if identity not in identities:
                return dash.no_update, dash.no_update, dash.no_update
            return ({"feature_id": identity, "job_id": bundle[0], "preview_file_id": bundle[1],
                     "bundle": _archive, "source": trigger},
                    dash.no_update if trigger == "jw-eic-feature" else identity,
                    dash.no_update if trigger == "jw-eic-slider" else identities.index(identity))
        if trigger == "jw-eic-map":
            index = _feature_index_from_map_click(map_click)
        elif trigger == "jw-eic-feature":
            index = dropdown_index
        elif trigger == "jw-eic-slider":
            index = slider_index
        else:
            index = None
        if index is None:
            return dash.no_update, dash.no_update, dash.no_update
        index = int(index)
        return (
            {"feature_index": index, "source": trigger},
            dash.no_update if trigger == "jw-eic-feature" else index,
            dash.no_update if trigger == "jw-eic-slider" else index,
        )

    @app.callback(
        Output("jw-eic-graph-shell", "children"),
        Output("jw-eic-plot-status", "children"),
        Output("jw-eic-ms2-shell", "children"),
        Input("jw-eic-feature-store", "data"),
        Input("jw-eic-archive", "value"),
        State("token-store", "data"),
        State("assay-select-store", "data"),
    )
    def render_eic_trace(selection, eic_file_id, token, assay_id):
        bundle = _package_bundle(eic_file_id)
        feature_index = (selection or {}).get("feature_id" if bundle else "feature_index")
        if bundle and (selection or {}).get("bundle") != eic_file_id:
            return [], "Pick a feature from this bundle.", []
        if not eic_file_id or feature_index is None:
            return [], "Pick a feature from the dropdown, the slider, or the map.", []
        if not token or not assay_id:
            return [], "Login and select an assay first.", []
        try:
            payload = (workflow_service.get_package_feature_trace(token, assay_id, *bundle, feature_index) if bundle
                       else workflow_service.get_eic_trace(token, assay_id, eic_file_id, feature_index))
        except (APIError, ValueError) as exc:
            return [], f"EIC render failed: {exc}", []
        feature = payload.get("feature") or {}
        spot = payload.get("spot") or {}
        alignment_id = feature.get("alignment_id")
        label = feature.get("feature_id") if bundle else (f"#{alignment_id}" if alignment_id is not None else f"index {feature.get('feature_index')}")
        graph = dcc.Graph(figure=sized(build_eic_figure(payload), EIC_TRACE_HEIGHT),
                          config={"displayModeBar": True, "responsive": True})
        source = (selection or {}).get("source", "")
        via = {"jw-eic-map": "map", "jw-eic-slider": "slider", "jw-eic-feature": "dropdown"}.get(source, "selection")
        # The spectrum rides on this response rather than a second request: the
        # payload already carries it once the feature has been selected.
        ms2_children = [
            dcc.Graph(figure=sized(build_ms2_mirror_figure(payload), EIC_MS2_HEIGHT),
                      config={"displayModeBar": False, "responsive": True}),
            html.Div(_ms2_evidence(payload.get("ms2") or {}), className="small text-muted"),
        ]
        return graph, (
            (payload.get("evidence_message") + " " if payload.get("evidence_message") else "") +
            f"Feature {label} from {spot.get('trace_count') or 0} aligned trace(s), "
            f"{payload.get('reader') or 'python'} reader, chosen via the {via}."
        ), ms2_children

    # -- IS-QC review -----------------------------------------------------
    @app.callback(
        Output("jw-isqc-standards-store", "data"),
        Output("jw-isqc-table", "selected_rows"),
        Input("url", "pathname"),
        State("token-store", "data"),
        State("assay-select-store", "data"),
    )
    def load_is_qc_standards(pathname, token, assay_id):
        """List the standards once for the open job, every one ticked.

        Not polled: a job paused for review does not change under the reader, and
        re-ticking would undo the RT model being assembled. No other callback
        writes the ticks; after this, only the reader changes them.
        """
        job_id, section = parse_job_route(pathname)
        if job_id is None or section != "isqc" or not token or not assay_id:
            return None, []
        try:
            rows = workflow_service.list_is_qc_targets(token, assay_id, job_id)
        except (APIError, ValueError) as exc:
            return {"error": str(exc)}, []
        standards = is_qc_standards(rows)
        return {"standards": standards}, [i for i, s in enumerate(standards) if s["target_id"]]

    @app.callback(
        Output("jw-isqc-table", "data"),
        Output("jw-isqc-state", "children"),
        Output("jw-isqc-count", "children"),
        Input("jw-isqc-table", "selected_rows"),
        Input("jw-isqc-standards-store", "data"),
        State("url", "pathname"),
        State("token-store", "data"),
        State("assay-select-store", "data"),
    )
    def refresh_is_qc_review(selected_rows, store, pathname, token, assay_id):
        """Recompute corrected retention times for the ticked standards."""
        job_id, section = parse_job_route(pathname)
        if job_id is None or section != "isqc" or not token or not assay_id or not store:
            return [], "", ""
        if store.get("error"):
            return [], dbc.Alert(f"IS-QC load failed: {store['error']}", color="danger"), ""
        targets = selected_target_ids(selected_rows, store.get("standards"))
        try:
            rows = workflow_service.list_is_qc_targets(token, assay_id, job_id,
                                                       rt_correction_target_ids=targets)
            summary = workflow_service.get_is_qc_summary(token, assay_id, job_id,
                                                         rt_correction_target_ids=targets)
        except (APIError, ValueError) as exc:
            return dash.no_update, dbc.Alert(f"IS-QC load failed: {exc}", color="danger"), dash.no_update
        data = is_qc_table_data(rows, summary)
        count = f"{len(data)} standards · ticked standards form the RT model; click a row for its traces."
        return data, is_qc_state_line(summary, len(data), len(targets)), count

    @app.callback(
        Output("jw-continue-btn", "disabled"),
        Output("jw-continue-status", "children"),
        Input("url", "pathname"),
        Input("jw-continue-result", "data"),
        Input("jw-jobs-store", "data"),
    )
    def gate_continue(pathname, result, store):
        job_id, section = parse_job_route(pathname)
        if job_id is None or section != "isqc":
            return True, ""
        if result and result.get("job_id") == job_id:
            if result.get("ok"):
                return True, f"Queued job #{result.get('new_job_id')} to continue."
            return True, f"Continue failed: {result.get('error')}"
        job = job_from_store(store, job_id)
        if job is None:
            return True, ""
        can_resume, reason = _job_can_resume(job)
        return (not can_resume), reason

    @app.callback(
        Output('jw-continue-result', 'data', allow_duplicate=True),
        Output('jw-auto-continue-status', 'children'),
        Input('jw-auto-continue-btn', 'n_clicks'),
        State('url', 'pathname'), State('token-store', 'data'), State('assay-select-store', 'data'),
        prevent_initial_call=True,
    )
    def continue_automatic_preview(clicks, pathname, token, assay_id):
        job_id, _ = parse_job_route(pathname)
        if not clicks or job_id is None or not token or not assay_id:
            return dash.no_update, dash.no_update
        try:
            continued = workflow_service.continue_workflow_job(token, assay_id, job_id)
        except (APIError, ValueError) as exc:
            return {'job_id': job_id, 'ok': False, 'error': str(exc)}, f'Continue failed: {exc}'
        return {'job_id': job_id, 'ok': True, 'new_job_id': continued['id']}, 'Finalization queued.'

    @app.callback(
        Output("jw-continue-result", "data"),
        Input("jw-continue-btn", "n_clicks"),
        State("url", "pathname"),
        State("jw-continue-rt", "value"),
        State("jw-continue-note", "value"),
        State("jw-isqc-table", "selected_rows"),
        State("jw-isqc-standards-store", "data"),
        State("token-store", "data"),
        State("assay-select-store", "data"),
        prevent_initial_call=True,
    )
    def continue_open_job(clicks, pathname, apply_rt, note, selected_rows, store, token, assay_id):
        job_id, _ = parse_job_route(pathname)
        if not clicks or job_id is None or not token or not assay_id:
            return dash.no_update
        targets = selected_target_ids(selected_rows, (store or {}).get("standards"))
        try:
            continued = workflow_service.continue_workflow_job(
                token, assay_id, job_id,
                operator_note=note,
                apply_rt_correction=bool(apply_rt),
                rt_correction_target_ids=targets if apply_rt else None,
            )
        except (APIError, ValueError) as exc:
            return {"job_id": job_id, "ok": False, "error": str(exc)}
        return {"job_id": job_id, "ok": True, "new_job_id": continued.get("id")}

    @app.callback(
        Output("jw-isqc-target-store", "data"),
        Output("jw-isqc-nav-status", "children"),
        Input("jw-isqc-table", "active_cell"),
        Input("jw-isqc-prev", "n_clicks"),
        Input("jw-isqc-next", "n_clicks"),
        Input("jw-isqc-standards-store", "data"),
        State("jw-isqc-target-store", "data"),
    )
    def select_is_qc_target(cell, _prev, _next, store, current):
        """Choose whose traces to show: a clicked row, Previous/Next, else the first.

        Sole writer of the store, so nothing competes over which standard is
        displayed. The evidence pane reads the store and nothing else.
        """
        standards = [s for s in (store or {}).get("standards") or [] if s.get("target_name")]
        if not standards:
            return None, ""
        names = [s["target_name"] for s in standards]
        name = current if current in names else names[0]
        trigger = dash.ctx.triggered_id
        if trigger == "jw-isqc-table" and cell:
            # The row id survives paging; the row index is only the visible page's.
            row_id = cell.get("row_id")
            clicked = next((s for s in standards if s["id"] == row_id), None)
            if clicked is None and row_id is None and 0 <= cell.get("row", -1) < len(standards):
                clicked = standards[cell["row"]]
            name = clicked["target_name"] if clicked else name
        elif trigger in {"jw-isqc-prev", "jw-isqc-next"}:
            position = names.index(name) + (1 if trigger == "jw-isqc-next" else -1)
            name = names[max(0, min(position, len(names) - 1))]
        return name, f"Standard {names.index(name) + 1} of {len(names)}"

    @app.callback(
        Output("jw-isqc-evidence", "children"),
        Output("jw-isqc-trace", "children"),
        Input("jw-isqc-target-store", "data"),
        Input("jw-isqc-rt-view", "value"),
        Input("jw-isqc-table", "selected_rows"),
        State("jw-isqc-standards-store", "data"),
        State("url", "pathname"),
        State("token-store", "data"),
        State("assay-select-store", "data"),
    )
    def render_is_qc_evidence(target_name, rt_view, selected_rows, store, pathname, token, assay_id):
        """The selected standard's facts, per-sample results and traces.

        Corrected traces follow the ticked standards, so a tick redraws the plot
        in that view. In the original view a tick changes nothing shown here, and
        redrawing would only reset the reader's zoom.
        """
        job_id, section = parse_job_route(pathname)
        if job_id is None or section != "isqc" or not target_name or not token or not assay_id:
            return html.Div("Select a standard.", className="text-muted small"), []
        if dash.ctx.triggered_id == "jw-isqc-table" and rt_view != "corrected":
            return dash.no_update, dash.no_update
        targets = selected_target_ids(selected_rows, (store or {}).get("standards"))
        try:
            rows = workflow_service.list_is_qc_targets(token, assay_id, job_id)
            payload = workflow_service.get_is_qc_trace(token, assay_id, job_id, target_name,
                                                       rt_correction_target_ids=targets)
        except (APIError, ValueError) as exc:
            return html.Div(f"Evidence unavailable: {exc}", className="text-danger small"), []
        figure = build_is_qc_trace_figure(payload, rt_view or "original")
        # The name heads the evidence above, as in the feature review.
        figure.update_layout(height=IS_QC_TRACE_HEIGHT, title=None)
        return is_qc_evidence(target_name, rows), dcc.Graph(figure=figure, config={"responsive": True})
