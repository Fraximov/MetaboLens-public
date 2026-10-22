"""The files of one package revision: what goes to MetaboLights, read in the app.

    /studies/<id>/packaging/builds/<rev>/files           the submission folder and its investigation
    /studies/<id>/packaging/builds/<rev>/files/<file>    one ISA file, parsed

The backend lists the folder without following links and parses the ISA files;
this module only lays them out. A validator finding sits on the column it names,
with its message on hover, and every finding of the file is listed above it.
"""
import re
from urllib.parse import quote, unquote

import dash_bootstrap_components as dbc
from dash import dash_table, dcc, html

from services.api_client import call_api
from ui.components import render_table, section_header
from ui.packaging import AMBER, RED, mono, size_label

FILES_ROUTE = re.compile(r"/studies/([1-9]\d*)/packaging/builds/([1-9]\d*)/files(?:/(.+?))?/?")
PREVIEWED = {"investigation", "samples", "assay", "maf"}
ISA_ORDER = {"investigation": 0, "samples": 1, "assay": 2, "maf": 3}
SOFT = {RED: "#fdf1f0", AMBER: "#fff7e8"}


def files_route(path):
    """(study_id, revision, file or None) for a files route, else None."""
    match = FILES_ROUTE.fullmatch(path or "")
    if not match:
        return None
    return int(match[1]), int(match[2]), unquote(match[3]) if match[3] else None


def files_href(study_id, revision, file=None):
    base = f"/studies/{study_id}/packaging/builds/{revision}/files"
    return f"{base}/{quote(file, safe='/')}" if file else base


def findings_mark(errors, warnings):
    """'2 err · 1 warn' in the colour of the worst, or nothing."""
    if not errors and not warnings:
        return ""
    parts = [f"{errors} err"] * bool(errors) + [f"{warnings} warn"] * bool(warnings)
    return html.Span(" · ".join(parts), className="font-monospace", style={"color": RED if errors else AMBER})


# -- tree ---------------------------------------------------------------------

def tree_row(glyph, name, kind="", size="", mark="", selected=False, title=None):
    return html.Div([html.Span([html.Span(glyph, className="pkg-tree-glyph"), name], className="pkg-tree-name", title=title),
                     html.Span(kind, className="pkg-tree-kind"), html.Span(size, className="pkg-tree-size"),
                     html.Span(mark, className="pkg-tree-mark")],
                    className="pkg-tree-row is-selected" if selected else "pkg-tree-row")


def spectrum_title(entry):
    lines = [entry.get("source") or "", f"sha256 {entry['sha256']}" if entry.get("sha256") else "",
             f"checksum {entry['verification']}" if entry.get("verification") else ""]
    return "\n".join(line for line in lines if line)


def tree(entries, study_id, revision, selected):
    """The submission folder as `tree` prints it; single-folder chains join, spectra fold away."""
    children = {}
    for entry in entries:
        children.setdefault(entry["path"].rpartition("/")[0], []).append(entry)
    for items in children.values():  # ISA files in reading order, then other files, folders last
        items.sort(key=lambda e: (e["kind"] == "dir", ISA_ORDER.get(e.get("role"), len(ISA_ORDER)), e["name"].lower()))

    def below(path):
        nested = [e for e in entries if e["path"].startswith(path + "/")]
        return nested, [e for e in nested if e["kind"] != "dir"]

    def rows(parent, prefix):
        items = children.get(parent, [])
        out = []
        for position, entry in enumerate(items):
            last = position == len(items) - 1
            glyph, inner = prefix + ("└─ " if last else "├─ "), prefix + ("   " if last else "│  ")
            if entry["kind"] == "dir":
                name = entry["name"]
                while len(children.get(entry["path"], [])) == 1 and children[entry["path"]][0]["kind"] == "dir":
                    entry = children[entry["path"]][0]
                    name += "/" + entry["name"]
                nested, leaves = below(entry["path"])
                linked = sum(e["kind"] == "link" for e in leaves)
                summary = tree_row(glyph, name + "/", f"{linked} linked" if linked else f"{len(leaves)} files",
                                   size_label(sum(e.get("size") or 0 for e in leaves)),
                                   findings_mark(sum(e["errors"] for e in nested), sum(e["warnings"] for e in nested)))
                out.append(html.Details([html.Summary(summary), *rows(entry["path"], inner)], className="pkg-tree-dir"))
                continue
            mark = findings_mark(entry["errors"], entry["warnings"])
            if entry.get("role") == "spectrum":
                missing = entry["kind"] == "link" and not entry.get("available")
                out.append(tree_row(glyph, entry["name"], "linked" if entry["kind"] == "link" else "copied",
                                    "missing" if missing else size_label(entry.get("size")),
                                    mark, title=spectrum_title(entry)))
                continue
            name = (dcc.Link(entry["name"], href=files_href(study_id, revision, entry["path"]))
                    if entry.get("role") in PREVIEWED else entry["name"])
            out.append(tree_row(glyph, name, entry.get("role") or "", size_label(entry.get("size")), mark,
                                selected=entry["path"] == selected, title=entry["name"]))  # long names clip beside the panel
        return out

    return html.Div([tree_row("", "submission/", "study root"), *rows("", "")], className="pkg-tree")


# -- previews -----------------------------------------------------------------

def finding_line(finding):
    """Two short lines: the message, then where and which rule; the whole message on hover."""
    color = RED if finding["severity"] == "error" else AMBER
    return html.Div([
        html.Span(className="pkg-finding-dot", style={"backgroundColor": color}),
        html.Div([html.Div(finding["message"], className="pkg-finding-message"),
                  html.Div(f"{finding['column'] or 'whole file'} · {finding['code']}", className="pkg-finding-meta")]),
    ], className="pkg-finding", title=f"{finding['severity']} · {finding['code']}\n{finding['message']}")


def findings_panel(preview):
    """The selected file's findings, errors first, as a compact list that scrolls beside the tree."""
    found = sorted((preview or {}).get("findings", []), key=lambda f: f["severity"] != "error")
    errors = sum(f["severity"] == "error" for f in found)
    lines = [finding_line(f) for f in found] or [html.Div("no findings in this file", className="pkg-finding-none")]
    return html.Div([html.Div([html.Span("findings"), findings_mark(errors, len(found) - errors)],
                              className="pkg-panel-head"),
                     html.Div(lines, className="pkg-findings")], className="pkg-panel")


def investigation_view(preview):
    """Each section's filled fields as a table; its empty fields folded into one line that opens."""
    parts = []
    for section in preview["sections"]:
        filled = [f for f in section["fields"] if any(v.strip() for v in f["values"])]
        empty = [f["label"] for f in section["fields"] if f not in filled]
        children = [html.Div(section["name"].lower(), className="pkg-isa-section")]
        if filled:
            width = max(len(field["values"]) for field in filled)
            headers = ["field"] + (["value"] if width == 1 else [str(n) for n in range(1, width + 1)])
            children.append(render_table(headers, [[f["label"], *(f["values"] + [""] * (width - len(f["values"])))]
                                                   for f in filled]))
        if empty:
            children.append(html.Details([
                html.Summary(f"all {len(empty)} fields empty" if not filled and len(empty) > 1
                             else f"{len(empty)} empty field{'s' if len(empty) > 1 else ''}", className="small text-muted"),
                html.Div(" · ".join(empty), className="small text-muted font-monospace mt-1")], className="mt-1"))
        parts.append(html.Div(children, className="mb-4"))
    return html.Div(parts)


def column_marks(findings):
    """Per column: its findings' colour and the hover text."""
    marks = {}
    for finding in findings:
        if finding.get("column_index") is not None:
            marks.setdefault(str(finding["column_index"]), []).append(finding)
    return {column: (RED if any(f["severity"] == "error" for f in found) else AMBER,
                     "\n".join(f"{f['severity']}: {f['message']}" for f in found)) for column, found in marks.items()}


def table_view(preview):
    marks = column_marks(preview["findings"])
    columns = [{"id": str(index), "name": name} for index, name in enumerate(preview["columns"])]
    data = [{str(index): value for index, value in enumerate(row)} for row in preview["rows"]]
    shown, total = len(preview["rows"]), preview["total_rows"]
    caption = (f"{total} rows" if shown == total else f"first {shown} of {total:,} rows") + f" · {len(columns)} columns"
    if marks:
        caption += f" · {len(marks)} flagged, hover a coloured header"
    # Sample and assay rows are read by their first column (Source or Sample Name), so it stays in view;
    # a pinned column needs a set width, or its header drifts from the scrolling part.
    pinned = preview["role"] in {"samples", "assay"}
    return html.Div([
        html.Div(mono(caption), className="small mb-2"),
        dash_table.DataTable(
            data=data, columns=columns, page_action="none",
            fixed_columns={"headers": True, "data": 1} if pinned else {},
            style_table={"overflowX": "auto", "minWidth": "100%"},
            style_cell={"textAlign": "left", "padding": "6px 8px", "whiteSpace": "nowrap", "maxWidth": "28rem",
                        "overflow": "hidden", "textOverflow": "ellipsis"},
            style_cell_conditional=[{"if": {"column_id": "0"}, "width": "12rem", "minWidth": "12rem",
                                     "maxWidth": "12rem"}] if pinned else [],
            tooltip_header={column: {"value": text, "type": "text"} for column, (_, text) in marks.items()},
            tooltip_delay=0, tooltip_duration=None,
            style_header_conditional=[{"if": {"column_id": column}, "color": color, "boxShadow": f"inset 0 3px 0 {color}"}
                                      for column, (color, _) in marks.items()],
            style_data_conditional=[{"if": {"column_id": column}, "backgroundColor": SOFT[color]}
                                    for column, (color, _) in marks.items()]),
    ])


def preview_view(preview):
    body = investigation_view(preview) if preview["role"] == "investigation" else table_view(preview)
    return html.Div([section_header(preview["path"], action=mono(preview["role"])), body], className="mt-4")


def files_view(token, study_id, package, selected):
    """The tree beside the selected file's findings, then the file; by default the investigation."""
    listing = call_api("GET", f"/studies/{study_id}/packages/{package['id']}/files", token=token)
    entries = listing["entries"]
    if selected is None:
        selected = next((e["path"] for e in entries if e.get("role") == "investigation"), None)
    revision = package["revision"]
    parts = [section_header(f"revision {revision} files", hint="what goes to MetaboLights: the submission folder; "
                                                               "spectra are linked, hover one for its source and checksum",
                            action=dcc.Link(f"revision {revision} →",
                                            href=f"/studies/{study_id}/packaging/builds/{revision}"))]
    preview = (call_api("GET", f"/studies/{study_id}/packages/{package['id']}/files/{quote(selected, safe='/')}",
                        token=token) if selected else None)
    parts.append(dbc.Row([dbc.Col(tree(entries, study_id, revision, selected), lg=7),
                          dbc.Col(findings_panel(preview), lg=5)], className="g-3"))
    if preview:
        parts.append(preview_view(preview))
    return html.Div(parts)
