"""ISA packaging workspace callbacks: API-only; the route owns the Study and section.

Findings come from the backend readiness check and build reports, which already
name the section that fixes them. This module arranges them in the light shell's
vocabulary: ● states, row-rule tables and monospace machine facts.
"""
import re
from uuid import uuid4

import dash
import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, ctx, dcc, html
from dash.exceptions import PreventUpdate

from callbacks import packaging_files, packaging_metadata
from pages.study_packaging import build_controls, runs_controls
from services.api_client import APIError, call_api, call_api_bytes
from ui.components import icon, icon_button, page_intro, render_field_value_table, render_table, section_header
from ui.formatting import short_timestamp, state_badge, timestamp_cell
from ui.packaging import AMBER, BLUE, GRAY, GREEN, RED, clipped, mono, pill, severity_pill, size_label

ROUTE = re.compile(r"/studies/([1-9]\d*)/packaging(?:/(builds|study|protocols|samples|new|sources)"
                   r"(?:/([1-9]\d*|preview)(?:/files(?:/.*)?)?)?)?/?")
OLD_METADATA_ROUTE = re.compile(r"/studies/([1-9]\d*)/metadata/?")
OLD_SOURCES_ROUTE = re.compile(r"/studies/([1-9]\d*)/packaging/sources/?")
REQUEST_ID = re.compile(r"(?:REQ|MTBLS)\d+")
ACTIVE_STATUSES = {"queued", "building"}
EXPORT_ACTIVE = {"queued", "exporting"}
# label, colour, data-state (running pulses, like every other live state)
PACKAGE_STATES = {
    "queued": ("queued", GRAY, "queued"),
    "building": ("building", BLUE, "running"),
    "failed": ("build failed", RED, "failed"),
    "validation_failed": ("corrections needed", AMBER, "validation_failed"),
    "complete": ("locally valid", GREEN, "complete"),
}
# section key, label, where it is fixed
READINESS_SECTIONS = (
    ("study", "study and people", "study"),
    ("protocols", "protocols", "protocols"),
    ("samples", "samples and factors", "samples"),
    ("sources", "runs", "new"),
    ("workflow", "workflow results", "job"),
    ("identifier", "identifier", "new"),
)
FINDING_GROUPS = (
    ("fix in isa packaging", {"study", "protocols", "samples", "sources", "identifier"}),
    ("fix in the workflow", {"workflow"}),
    ("official validator", {"official"}),
)


def parse_route(path):
    """(study_id, section, revision) for a packaging route, else (None, None, None).

    The workspace lands on the build list; a new package is chosen at /new ("new")
    and checked and built at /new/preview ("preview").
    """
    match = ROUTE.fullmatch(path or "")
    if not match:
        return None, None, None
    section, tail = match[2] or "builds", match[3]
    if section == "new":
        return int(match[1]), "preview" if tail == "preview" else "new", None
    return int(match[1]), section, int(tail) if tail and tail.isdigit() else None


def base_path(study_id):
    return f"/studies/{study_id}/packaging"


def package_state(status):
    label, color, state = PACKAGE_STATES.get(status, (status or "unknown", GRAY, status or "unknown"))
    return pill(label, color, state)


def counts_label(errors, warnings):
    return f"{errors} err · {warnings} warn"


# -- selection ----------------------------------------------------------------

def run_value(source):
    return f"{source['job_id']}:{source['manifest_file_id']}"


def newest(assay):
    return max(assay["sources"], key=lambda source: source["job_id"]) if assay["sources"] else None


def selection_for(store, study_id, choices):
    """The proposed selection: kept in the session, else the newest completed run of each assay.

    The request id carries over from the latest build: it names the Study at MetaboLights.
    """
    saved = (store or {}).get(str(study_id))
    if saved is not None:
        return saved
    last = (choices or {}).get("last_selection") or {}
    return dict(metabolights_id=last.get("metabolights_id") or "",
                sources={str(a["assay_id"]): run_value(newest(a)) for a in (choices or {}).get("assays", [])
                         if a["sources"]})


def selection_payload(selection, choices):
    """Check/build payload: unselected assays are recorded as explicitly omitted."""
    sources, omitted = [], []
    for assay in (choices or {}).get("assays", []):
        value = ((selection or {}).get("sources") or {}).get(str(assay["assay_id"]), "omit")
        if value == "omit":
            omitted.append(assay["assay_id"])
            continue
        job_id, manifest_id = str(value).split(":")
        sources.append(dict(assay_id=assay["assay_id"], job_id=int(job_id), manifest_file_id=int(manifest_id)))
    return dict(metabolights_id=((selection or {}).get("metabolights_id") or "").strip(), sources=sources,
                omitted_assay_ids=omitted)


def request_id_problem(request_id):
    if request_id and not REQUEST_ID.fullmatch(request_id):
        return "the request id must look like REQ… or MTBLS… followed by digits; leave it empty for a local draft"
    return None


QUANTITY_NAMES = {"height": "peak height", "area": "peak area"}


def quantity_name(source):
    """"peak height", "peak area" or "quantity unknown" for a run."""
    return QUANTITY_NAMES.get(((source or {}).get("quantity") or {}).get("value"), "quantity unknown")


def source_label(source):
    method = " ".join(filter(None, [source.get("polarity"), source.get("separation_mode")]))
    review = f"review r{source['review_revision']}" if source.get("review_revision") is not None else "no review"
    engine = f"ms-dial {source['engine_version']}" if source.get("engine_version") else source.get("engine")
    parts = [f"job {source['job_id']}", source.get("run_name"), short_timestamp(source.get("finished_at")), review,
             engine, method, quantity_name(source), f"{source.get('acquisition_count', 0)} acq",
             size_label(source.get("spectral_bytes"))]
    return " · ".join(str(part) for part in parts if part and part != "-")


def check(token, study_id, payload):
    if request_id_problem(payload["metabolights_id"]):
        payload = {**payload, "metabolights_id": ""}
    return call_api("POST", f"/studies/{study_id}/packaging/check", token=token, payload=payload)


# -- preview ------------------------------------------------------------------

def fix_link(where, study_id, items, payload):
    if where in {"study", "protocols", "samples"}:
        return dcc.Link(f"{where}\u00a0→", href=f"{base_path(study_id)}/{where}")
    if where == "new":
        return dcc.Link("runs\u00a0→", href=f"{base_path(study_id)}/new")
    jobs = {s["assay_id"]: s["job_id"] for s in payload["sources"]}
    job = next((jobs[i["assay_id"]] for i in items if i.get("assay_id") in jobs), None)
    return dcc.Link(f"job {job}\u00a0→", href=f"/jobs/{job}") if job else ""


def section_state(items):
    errors = sum(i["severity"] == "error" for i in items)
    warnings = len(items) - errors
    if errors:
        return pill(f"{errors} to fix", RED, "failed")
    if warnings:
        return pill(f"{warnings} warning{'s' if warnings > 1 else ''}", AMBER, "warning")
    return pill("ready", GREEN, "complete")


def overview_rows(result, study_id, payload):
    rows = []
    for key, label, where in READINESS_SECTIONS:
        items = [i for i in result["issues"] if i.get("section") == key]
        detail = [html.Div(i["message"]) for i in items[:2]]
        if len(items) > 2:
            detail.append(html.Div(f"+{len(items) - 2} more", className="text-muted small"))
        rows.append([label, section_state(items), html.Div(detail) if detail else "-",
                     fix_link(where, study_id, items, payload) if items else ""])
    return rows


def steps(study_id, current):
    """1 choose runs → 2 preview and build: the current step in bold, the other a link."""
    parts = []
    for key, label, href in (("new", "1 choose runs", f"{base_path(study_id)}/new"),
                             ("preview", "2 preview and build", f"{base_path(study_id)}/new/preview")):
        if parts:
            parts.append(html.Span("\u00a0→\u00a0", className="text-muted"))
        parts.append(html.Strong(label) if key == current else dcc.Link(label, href=href))
    return html.Div(parts, className="small font-monospace mb-3")


def chosen_runs(choices, payload):
    """Each chosen run, marked when it is not the assay's newest."""
    chosen = {s["assay_id"]: s for s in payload["sources"]}
    runs = []
    for assay in choices["assays"]:
        source = chosen.get(assay["assay_id"])
        if source:
            latest = newest(assay)
            mark = "newest" if latest and latest["job_id"] == source["job_id"] else f"newest is job {latest['job_id']}"
            run = next((s for s in assay["sources"] if s["job_id"] == source["job_id"]), None)
            runs.append(f"{assay['assay_name']}: job {source['job_id']} ({mark}) · {quantity_name(run)}")
    return mono(" · ".join(runs) or "none")


def preview_view(token, study_id, choices, selection):
    """What building now would package: the chosen runs, readiness per section, then the build."""
    payload = selection_payload(selection, choices)
    result = check(token, study_id, payload)
    spectral = result["spectral"]
    omitted = [a["assay_name"] for a in choices["assays"] if a["assay_id"] in payload["omitted_assay_ids"]]
    problem = (request_id_problem(payload["metabolights_id"])
               or (None if payload["sources"] else "choose a workflow result for at least one assay"))
    facts = [["runs", chosen_runs(choices, payload)],
             ["left out", ", ".join(omitted) or "-"],
             ["request id", mono(payload["metabolights_id"] or "local draft")],
             ["metadata", mono(f"revision {result['metadata_revision']}")],
             ["spectra", mono(f"{spectral['files']} files · {size_label(spectral['bytes'])} linked")]]
    return html.Div([
        section_header("new package", hint="checked now against the chosen runs and the saved metadata; "
                                           "nothing is built or copied until you build"),
        steps(study_id, "preview"),
        render_field_value_table(facts),
        html.Div(render_table(["section", "state", "findings", ""], overview_rows(result, study_id, payload)),
                 className="mt-3"),
        build_controls(study_id, choices.get("next_revision"), problem),
    ])


# -- choose runs --------------------------------------------------------------

def coverage_line(choices, payload):
    chosen = {s["assay_id"]: s for s in payload["sources"]}
    sources = {a["assay_id"]: next((s for s in a["sources"] if s["job_id"] == chosen[a["assay_id"]]["job_id"]
                                     and s["manifest_file_id"] == chosen[a["assay_id"]]["manifest_file_id"]), None)
               for a in choices["assays"] if a["assay_id"] in chosen}
    sample_sets = [set(s["sample_ids"]) for s in sources.values() if s]
    union = set().union(*sample_sets) if sample_sets else set()
    shared = set.intersection(*sample_sets) if len(sample_sets) > 1 else None
    omitted = [a["assay_name"] for a in choices["assays"] if a["assay_id"] in payload["omitted_assay_ids"]]
    parts = [f"{len(union)} samples", f"{sum(s['acquisition_count'] for s in sources.values() if s)} acquisitions",
             f"{size_label(sum(s['spectral_bytes'] for s in sources.values() if s))} to link"]
    if shared is not None:
        parts.append(f"{len(shared)} in every assay")
    return [mono(" · ".join(parts)), html.Div("left out: " + (", ".join(omitted) or "none"), className="text-muted small")]


def runs_summary(choices, selection):
    """Coverage of the chosen runs; the readiness check waits for the preview."""
    payload = selection_payload(selection, choices)
    problem = request_id_problem(payload["metabolights_id"])
    if not payload["sources"]:
        return html.Div("choose a workflow result for at least one assay", className="text-muted")
    return html.Div([html.Div(coverage_line(choices, payload)),
                     html.Div(problem, className="text-danger small") if problem else None])


def new_view(study_id, choices, selection):
    rows = []
    for assay in choices["assays"]:
        options = [{"label": "leave this assay out", "value": "omit"}] + [
            {"label": source_label(s), "value": f"{s['job_id']}:{s['manifest_file_id']}"} for s in assay["sources"]]
        value = (selection.get("sources") or {}).get(str(assay["assay_id"]), "omit")
        if value not in {o["value"] for o in options}:
            value = "omit"
        control = (dcc.Dropdown(id={"type": "pkg-source", "assay": assay["assay_id"]}, options=options, value=value,
                                clearable=False, className="small")
                   if assay["sources"] else html.Span("no completed ms-dial result · left out", className="text-muted"))
        rows.append([assay["assay_name"], control])
    return html.Div([
        section_header("new package", hint="one completed workflow result per assay, the newest proposed; "
                                           "an assay left out is recorded as omitted"),
        steps(study_id, "new"),
        render_table(["assay", "workflow result"], rows),
        html.Div(runs_controls(study_id, selection.get("metabolights_id") or "", runs_summary(choices, selection)),
                 className="mt-3"),
    ])


# -- builds -------------------------------------------------------------------

def export_state(package):
    """The export's pill, with its folder or error on hover; empty before a first export."""
    export = package.get("export") or {}
    status = export.get("status")
    if status in EXPORT_ACTIVE:
        return pill(status, BLUE, "running")
    if status == "exported":
        return html.Span(pill("exported", GREEN, "complete"), title=export.get("uri") or "")
    if status == "export_failed":
        return html.Span(pill("export failed", RED, "failed"), title=export.get("error") or "")
    return None


def is_built(package):
    return package["status"] in {"complete", "validation_failed"} and bool(package.get("output_uri"))


def build_actions(study_id, package):
    exporting = (package.get("export") or {}).get("status") in EXPORT_ACTIVE
    files_label = "files: what goes to MetaboLights"
    files = (dcc.Link(icon("files"), href=packaging_files.files_href(study_id, package["revision"]),
                      className="icon-action", title=files_label)
             if is_built(package) else html.Span(icon("files"), className="icon-action is-disabled", title=files_label))
    return html.Div([
        files,
        icon_button("download", {"type": "pkg-download", "package": package["id"]},
                    "download metadata and report (no maf or spectra)", disabled=not is_built(package)),
        icon_button("export", {"type": "pkg-export", "package": package["id"]},
                    "export a self-contained copy with the spectra", disabled=not is_built(package) or exporting),
    ], className="d-flex gap-1 justify-content-end")


def builds_view(study_id, packages):
    rows = [[dcc.Link(mono(f"r{p['revision']}"), href=f"{base_path(study_id)}/builds/{p['revision']}"),
             timestamp_cell(p.get("created_at")),
             ", ".join(f"{s['assay_name']}: job {s['job_id']}" for s in p.get("sources", [])) or "-",
             package_state(p["status"]), mono(counts_label(p["summary"]["errors"], p["summary"]["warnings"])),
             export_state(p) or "-", build_actions(study_id, p)]
            for p in packages]
    new = dcc.Link("new package", href=f"{base_path(study_id)}/new", className="btn btn-sm btn-primary")
    return html.Div([
        section_header("builds", hint="each build freezes its runs and metadata; later edits never change it",
                       action=new),
        render_table(["revision", "requested", "sources", "state", "findings", "export", ""], rows) if rows
        else html.Div("no builds yet", className="text-muted"),
    ])


def finding_rows(items):
    rows = []
    for issue in items:
        where = " · ".join(str(issue[key]) for key in ("file", "column") if issue.get(key))
        if not where and issue.get("assay_id"):
            where = f"assay {issue['assay_id']}"
        total = next((issue[k] for k in issue if k.endswith("_total")), None)
        message = issue.get("message", "") + (f" ({total} listed in the full report)" if total else "")
        rows.append([severity_pill(issue.get("severity")), mono(issue.get("code", "")), clipped(message),
                     mono(where) if where else "-"])
    return rows


def export_line(package):
    """The export's state with its folder, or nothing before a first export."""
    export = package.get("export") or {}
    status = export.get("status")
    if status in EXPORT_ACTIVE:
        return html.Div([pill(status, BLUE, "running"), " ", mono("copying and re-checking the spectra")])
    if status == "exported":
        return html.Div([pill("exported", GREEN, "complete"), " ", mono(export.get("uri")), " ",
                         timestamp_cell(export.get("finished_at"))])
    if status == "export_failed":
        return html.Div([pill("export failed", RED, "failed"), " ", clipped(export.get("error") or "")])
    return None


def revision_actions(study_id, package):
    """Download, export and delete in one row; each says what it does on hover."""
    status = (package.get("export") or {}).get("status")
    busy = package["status"] in ACTIVE_STATUSES or status in EXPORT_ACTIVE
    actions = []
    if is_built(package):
        actions.append(dcc.Link("files", href=packaging_files.files_href(study_id, package["revision"]),
                                className="btn btn-sm btn-outline-primary", title="read what goes to MetaboLights"))
        actions.append(dbc.Button("download", id={"type": "pkg-download", "package": package["id"]}, n_clicks=0,
                                  color="outline-primary", size="sm", title="metadata and report; no maf or spectra"))
        actions.append(dbc.Button("export again" if status in {"exported", "export_failed"} else "export",
                                  id={"type": "pkg-export", "package": package["id"]}, n_clicks=0, size="sm",
                                  color="outline-primary", disabled=status in EXPORT_ACTIVE,
                                  title="a self-contained copy with the spectra, for upload"))
    if not busy:
        actions.append(dcc.ConfirmDialogProvider(
            dbc.Button("delete", color="outline-danger", size="sm", title="remove this revision's folder"),
            id={"type": "pkg-delete", "package": package["id"]},
            message=f"Delete revision {package['revision']}? Its folder and links are removed; "
                    "the RAW files and any export are kept."))
    return html.Div(actions, className="d-flex gap-2 mt-3") if actions else None


def build_detail_view(study_id, package):
    summary, report = package["summary"], package.get("report") or {}
    facts = [
        ["state", package_state(package["status"])],
        ["requested", timestamp_cell(package.get("created_at"))],
        ["finished", timestamp_cell(package.get("finished_at"))],
        ["sources", ", ".join(f"{s['assay_name']}: job {s['job_id']}" for s in package.get("sources", [])) or "-"],
        ["request id", mono(package.get("metabolights_id") or "local draft")],
        ["samples", mono(package.get("sample_count", 0))],
        ["findings", mono(counts_label(summary["errors"], summary["warnings"]))],
    ]
    if package.get("output_uri"):
        facts.append(["folder", mono(package["output_uri"])])
    if (package.get("export") or {}).get("status"):
        facts.append(["export", export_line(package)])
    parts = [section_header(f"revision {package['revision']}"), render_field_value_table(facts)]
    if package.get("error_message"):
        parts.append(html.Div(package["error_message"], className="text-danger mt-2"))
    parts.append(revision_actions(study_id, package))
    issues = report.get("issues", [])
    for label, sections in FINDING_GROUPS:
        items = [i for i in issues if i.get("section") in sections]
        if items:
            errors = sum(i.get("severity") == "error" for i in items)
            parts.append(html.Div([section_header(label, action=mono(counts_label(errors, len(items) - errors))),
                                   render_table(["", "code", "message", "where"], finding_rows(items))], className="mt-4"))
    return html.Div(parts)


def is_active(package):
    return package["status"] in ACTIVE_STATUSES or (package.get("export") or {}).get("status") in EXPORT_ACTIVE


METADATA_VIEWS = {
    "study": lambda envelope, choices, selection: packaging_metadata.study_view(envelope, choices),
    "protocols": packaging_metadata.protocols_view,
    "samples": packaging_metadata.samples_view,
}


def nav(study_id, section):
    """builds (with the new-package steps) first, then the metadata the packages are made from."""
    current = "builds" if section in {"builds", "new", "preview"} else section
    links = [dbc.NavLink(key, href=base_path(study_id) + ("" if key == "builds" else f"/{key}"), active=(current == key))
             for key in ("builds", "study", "protocols", "samples")]
    return dbc.Nav(links, pills=True, className="mb-3")


def register_callbacks(app):
    @app.callback(Output("pkg-choices", "data"), Input("url", "pathname"), Input("token-store", "data"),
                  State("pkg-choices", "data"))
    def load_choices(path, token, choices):
        study_id, _, _ = parse_route(path)
        if not study_id or not token:
            raise PreventUpdate
        if choices and choices.get("study_id") == study_id and ctx.triggered_id == "url":
            raise PreventUpdate  # moving between sections keeps the loaded choices
        try:
            return call_api("GET", f"/studies/{study_id}/packaging", token=token)
        except APIError as error:
            return dict(study_id=study_id, error=str(error), assays=[])

    @app.callback(Output("pkg-intro", "children"), Input("pkg-choices", "data"))
    def intro(choices):
        if not choices or choices.get("error"):
            raise PreventUpdate
        return page_intro(choices["title"], eyebrow="isa packaging",
                          meta=[state_badge(choices.get("status")), " ", mono(f"study {choices['study_id']}")])

    @app.callback(Output("pkg-redirect", "pathname"), Input("url", "pathname"))
    def redirect_old_route(path):
        """Old links: /studies/<id>/metadata lands on the study section, packaging/sources on step 1."""
        match = OLD_METADATA_ROUTE.fullmatch(path or "")
        if match:
            return f"{base_path(int(match[1]))}/study"
        match = OLD_SOURCES_ROUTE.fullmatch(path or "")
        if match:
            return f"{base_path(int(match[1]))}/new"
        raise PreventUpdate

    @app.callback(Output("pkg-nav", "children"), Input("url", "pathname"))
    def render_nav(path):
        study_id, section, _ = parse_route(path)
        if not study_id:
            raise PreventUpdate
        return nav(study_id, section)

    packaging_metadata.register_callbacks(app, parse_route)

    @app.callback(Output("pkg-section", "children"), Output("pkg-poll", "disabled"), Output("pkg-metadata", "data"),
                  Input("url", "pathname"), Input("pkg-choices", "data"), Input("pkg-poll", "n_intervals"),
                  State("pkg-selection", "data"), State("token-store", "data"))
    def render_section(path, choices, _ticks, store, token):
        study_id, section, revision = parse_route(path)
        if not study_id or not token or not choices or choices.get("study_id") != study_id:
            raise PreventUpdate
        if section == "sources" or (ctx.triggered_id == "pkg-poll" and section != "builds"):
            raise PreventUpdate  # an old sources link is redirected; only builds poll
        if choices.get("error"):
            return html.Div(choices["error"], className="text-danger"), True, dash.no_update
        selection = selection_for(store, study_id, choices)
        try:
            if section in METADATA_VIEWS:
                envelope = call_api("GET", f"/studies/{study_id}/submission-metadata", token=token)
                return METADATA_VIEWS[section](envelope, choices, selection), True, envelope
            if section == "new":
                return new_view(study_id, choices, selection), True, dash.no_update
            if section == "preview":
                return preview_view(token, study_id, choices, selection), True, dash.no_update
            packages = call_api("GET", f"/studies/{study_id}/packages", token=token)
            if revision is None:
                return builds_view(study_id, packages), not any(map(is_active, packages)), dash.no_update
            match = next((p for p in packages if p["revision"] == revision), None)
            if match is None:
                return (html.Div(f"revision {revision} does not exist for this Study", className="text-muted"),
                        not any(map(is_active, packages)), dash.no_update)
            files = packaging_files.files_route(path)
            if files:
                return packaging_files.files_view(token, study_id, match, files[2]), True, dash.no_update
            package = call_api("GET", f"/studies/{study_id}/packages/{match['id']}", token=token)
            return build_detail_view(study_id, package), not is_active(package), dash.no_update
        except APIError as error:
            return html.Div(str(error), className="text-danger"), True, dash.no_update

    @app.callback(Output("pkg-action-message", "children"), Output("pkg-poll", "disabled", allow_duplicate=True),
                  Input({"type": "pkg-export", "package": ALL}, "n_clicks"),
                  State("url", "pathname"), State("token-store", "data"), prevent_initial_call=True)
    def request_export(clicks, path, token):
        study_id, _, _ = parse_route(path)
        if not any(clicks or []) or not study_id or not token or not isinstance(ctx.triggered_id, dict):
            raise PreventUpdate
        try:
            call_api("POST", f"/studies/{study_id}/packages/{ctx.triggered_id['package']}/export", token=token)
        except APIError as error:
            return html.Span(str(error), className="text-danger"), dash.no_update
        return html.Span("export queued", className="font-monospace"), False

    @app.callback(Output("pkg-action-message", "children", allow_duplicate=True), Output("pkg-redirect", "pathname", allow_duplicate=True),
                  Input({"type": "pkg-delete", "package": ALL}, "submit_n_clicks"),
                  State("url", "pathname"), State("token-store", "data"), prevent_initial_call=True)
    def delete_revision(confirmations, path, token):
        study_id, _, _ = parse_route(path)
        if not any(confirmations or []) or not study_id or not token or not isinstance(ctx.triggered_id, dict):
            raise PreventUpdate
        try:
            result = call_api("DELETE", f"/studies/{study_id}/packages/{ctx.triggered_id['package']}", token=token)
        except APIError as error:
            return html.Span(str(error), className="text-danger"), dash.no_update
        return (html.Span(f"deleted revision {result['revision']} · freed {size_label(result['freed_bytes'])}",
                          className="font-monospace"), base_path(study_id))

    @app.callback(Output("pkg-selection", "data"),
                  Input({"type": "pkg-source", "assay": ALL}, "value"), Input("pkg-request-id", "value"),
                  State({"type": "pkg-source", "assay": ALL}, "id"), State("pkg-selection", "data"),
                  State("url", "pathname"), prevent_initial_call=True)
    def update_selection(values, request_id, ids, store, path):
        study_id, section, _ = parse_route(path)
        if not study_id or section != "new":
            raise PreventUpdate
        store = dict(store or {})
        store[str(study_id)] = dict(metabolights_id=(request_id or "").strip(),
                                    sources={str(identity["assay"]): value for identity, value in zip(ids, values)})
        return store

    @app.callback(Output("pkg-sources-summary", "children"), Input("pkg-selection", "data"),
                  State("pkg-choices", "data"), State("url", "pathname"), State("token-store", "data"),
                  prevent_initial_call=True)
    def refresh_sources_summary(store, choices, path, token):
        study_id, section, _ = parse_route(path)
        if section != "new" or not choices:
            raise PreventUpdate
        return runs_summary(choices, selection_for(store, study_id, choices))

    @app.callback(Output("pkg-build-message", "children"), Output("pkg-build-signal", "data"),
                  Output("pkg-poll", "disabled", allow_duplicate=True),
                  Output("pkg-redirect", "pathname", allow_duplicate=True),
                  Input("pkg-build", "n_clicks"), State("pkg-selection", "data"), State("pkg-choices", "data"),
                  State("url", "pathname"), State("token-store", "data"), prevent_initial_call=True)
    def request_build(clicks, store, choices, path, token):
        study_id, _, _ = parse_route(path)
        if not clicks or not study_id or not token or not choices:
            raise PreventUpdate
        payload = selection_payload(selection_for(store, study_id, choices), choices)
        problem = (request_id_problem(payload["metabolights_id"])
                   or (None if payload["sources"] else "select a workflow result for at least one assay"))
        if problem:
            return html.Div(problem, className="text-danger small"), dash.no_update, dash.no_update, dash.no_update
        try:
            package = call_api("POST", f"/studies/{study_id}/packages", token=token,
                               payload={**payload, "request_key": str(uuid4())})
        except APIError as error:
            return html.Div(str(error), className="text-danger small"), dash.no_update, dash.no_update, dash.no_update
        # The new revision's page follows the build; it polls while the build is queued or running.
        return (html.Span(["queued ", mono(f"r{package['revision']}")]), package["id"], False,
                f"{base_path(study_id)}/builds/{package['revision']}")

    @app.callback(Output("pkg-download", "data"), Output("pkg-download-message", "children"),
                  Input({"type": "pkg-download", "package": ALL}, "n_clicks"),
                  State("url", "pathname"), State("token-store", "data"), prevent_initial_call=True)
    def download(clicks, path, token):
        study_id, _, _ = parse_route(path)
        if not any(clicks or []) or not study_id or not token or not isinstance(ctx.triggered_id, dict):
            raise PreventUpdate
        package_id = ctx.triggered_id["package"]
        try:
            content, _ = call_api_bytes("GET", f"/studies/{study_id}/packages/{package_id}/metadata", token=token)
        except APIError as error:
            return dash.no_update, str(error)
        return dcc.send_bytes(content, f"study-{study_id}-package-{package_id}-metadata-review.zip"), ""
