"""Study, protocols and samples sections of the ISA packaging workspace.

Each section edits part of the Study's submission metadata and saves the whole
document with the revision it was loaded with, so a stale page gets a conflict
instead of overwriting newer values. Sample names and characteristics are patched
per sample; they stay editable after a workflow has run. API-only.
"""
from copy import deepcopy

import dash
import dash_bootstrap_components as dbc
from dash import ALL, MATCH, Input, Output, State, ctx, dash_table, html
from dash.exceptions import PreventUpdate

from constants import SAMPLE_TYPE_OPTIONS
from services.api_client import APIError, call_api
from ui.components import section_header

LAB_PROTOCOLS = ("Extraction", "Chromatography", "Mass spectrometry")
PROTOCOL_HINTS = {
    "Extraction": "Solvents, volumes, temperatures, timings and internal standards.",
    "Chromatography": "Column, mobile phases, gradient, flow rate, temperature and injection volume.",
    "Mass spectrometry": "Instrument, polarity, scan range, resolution, source settings and collision energies.",
}
# Repeatable entries: fields in document order, the list container and its add button.
REPEATABLE = {
    "contact": dict(fields=("first_name", "last_name", "email", "affiliation", "role"), container="pkg-contacts",
                    add="pkg-contact-add"),
    "publication": dict(fields=("title", "authors", "doi", "status"), container="pkg-publications",
                        add="pkg-publication-add"),
    "descriptor": dict(fields=("name", "source", "accession"), container="pkg-descriptors", add="pkg-descriptor-add"),
    "factor": dict(fields=("name", "source_column", "type_name", "type_source", "type_accession"),
                   container="pkg-factors", add="pkg-factor-add"),
}
CONTACT_ROLES = ("Principal Investigator", "Submitter", "Study contact")


def field_id(kind, field, index):
    return {"type": f"pkg-{kind}", "field": field, "index": index}


def labelled(label, control, hint=None):
    return html.Div([dbc.Label(label, className="mb-1 small"), control,
                     html.Div(hint, className="form-text") if hint else None], className="mb-3")


def subsection(title, hint=None):
    """A sub-heading; its hint shows on hover."""
    return html.H6(title, title=hint, className="mt-4 mb-2 has-hint" if hint else "mt-4 mb-2")


def collect(kind, ids, values):
    """Rows of a repeatable list from its pattern-matched inputs, in display order."""
    rows = {}
    for identity, value in zip(ids or [], values or []):
        rows.setdefault(identity["index"], {})[identity["field"]] = value or ""
    return [rows[index] for index in sorted(rows)]


def filled(rows, fields):
    """Rows with at least one value, every field present."""
    return [{field: (row.get(field) or "").strip() for field in fields} for row in rows
            if any(str(row.get(field) or "").strip() for field in fields)]


def text_input(kind, field, index, value, placeholder, **extra):
    return dbc.Input(id=field_id(kind, field, index), value=value or "", placeholder=placeholder, size="sm", **extra)


def remove_button(kind, index):
    return dbc.Button("remove", id={"type": f"pkg-{kind}-remove", "index": index}, n_clicks=0,
                      color="link", size="sm", className="px-0")


def term_details(kind, index, row, source_field, accession_field):
    """Ontology reference folded away until an OLS lookup exists."""
    return html.Details([
        html.Summary("term reference", className="small text-muted"),
        dbc.Row([dbc.Col(text_input(kind, source_field, index, row.get(source_field), "source, e.g. EFO"), md=3),
                 dbc.Col(text_input(kind, accession_field, index, row.get(accession_field), "term URL"), md=9)],
                className="g-2 mt-1"),
    ], className="mt-2")


def render_items(kind, rows, envelope=None, statuses=()):
    items = []
    for index, row in enumerate(rows):
        if kind == "contact":
            body = dbc.Row([
                dbc.Col(text_input(kind, "first_name", index, row.get("first_name"), "first name"), md=2),
                dbc.Col(text_input(kind, "last_name", index, row.get("last_name"), "last name"), md=2),
                dbc.Col(text_input(kind, "email", index, row.get("email"), "email", type="email"), md=3),
                dbc.Col(text_input(kind, "affiliation", index, row.get("affiliation"), "affiliation"), md=3),
                dbc.Col(text_input(kind, "role", index, row.get("role"), "role", list="pkg-role-options"), md=2),
            ], className="g-2")
        elif kind == "publication":
            options = [{"label": "status", "value": ""}] + [{"label": s, "value": s} for s in statuses]
            if row.get("status") and row["status"] not in statuses:
                options.append({"label": row["status"], "value": row["status"]})
            body = dbc.Row([
                dbc.Col(text_input(kind, "title", index, row.get("title"), "title"), md=5),
                dbc.Col(text_input(kind, "authors", index, row.get("authors"), "authors"), md=3),
                dbc.Col(text_input(kind, "doi", index, row.get("doi"), "doi"), md=2),
                dbc.Col(dbc.Select(id=field_id(kind, "status", index), options=options, value=row.get("status") or "",
                                   size="sm"), md=2),
            ], className="g-2")
        elif kind == "descriptor":
            body = html.Div([text_input(kind, "name", index, row.get("name"), "e.g. untargeted metabolomics"),
                             term_details(kind, index, row, "source", "accession")])
        else:
            columns = (envelope or {}).get("factor_columns") or []
            options = [{"label": "sample-sheet column", "value": ""}] + [
                {"label": c["label"], "value": c["value"]} for c in columns]
            body = html.Div([
                dbc.Row([dbc.Col(text_input(kind, "name", index, row.get("name"), "factor, e.g. Diet"), md=4),
                         dbc.Col(dbc.Select(id=field_id(kind, "source_column", index), options=options,
                                            value=row.get("source_column") or "", size="sm"), md=4),
                         dbc.Col(text_input(kind, "type_name", index, row.get("type_name"), "type, e.g. diet"), md=4)],
                        className="g-2"),
                html.Div(factor_values(row.get("source_column"), envelope),
                         id={"type": "pkg-factor-values", "index": index}, className="small mt-2"),
                term_details(kind, index, row, "type_source", "type_accession"),
            ])
        items.append(html.Div([body, remove_button(kind, index)], className="border-bottom pb-2 mb-3"))
    return items or [html.Div("none yet", className="text-muted small mb-2")]


def factor_values(column, envelope):
    """Distinct values of a sample-sheet column, with sample counts and cross-assay conflicts."""
    summary = next((c for c in (envelope or {}).get("factor_columns") or [] if c["value"] == column), None)
    if not summary:
        return ""
    values = " · ".join(f"{v['value']} {v['count']}" for v in summary.get("values", [])) or "no values recorded"
    parts = [html.Span(values, className="font-monospace")]
    if summary.get("conflicts"):
        parts.append(html.Div(f"differs between assays for {', '.join(summary['conflicts'])}; correct the sample "
                              "sheet before building", className="text-danger"))
    return parts


def add_button(label, identity):
    return dbc.Button(label, id=identity, n_clicks=0, color="outline-secondary", size="sm", className="mb-2")


def save_row(identity):
    return html.Div([dbc.Button("save", id=f"{identity}-save", n_clicks=0, color="primary", size="sm"),
                     html.Span(id=f"{identity}-message", role="status", className="ms-3 small")], className="mt-3")


def editable_note(envelope):
    if envelope.get("can_edit"):
        return None
    return html.P("read only", title="editing needs study-editing or operator permission",
                  className="text-muted small has-hint")


# -- sections -----------------------------------------------------------------

def study_view(envelope, choices):
    metadata = envelope["metadata"]
    vocabularies = (choices or {}).get("vocabularies") or {}
    roles = list(dict.fromkeys([*vocabularies.get("contact_roles", []), *CONTACT_ROLES]))
    descriptors = metadata.get("design_descriptors") or []
    return html.Div([
        section_header("study and people"),
        editable_note(envelope),
        html.Fieldset([
            labelled("title", dbc.Input(id="pkg-study-title", value=metadata.get("title") or "",
                                        placeholder="e.g. untargeted LC-MS profiling of plasma from lean and obese mice")),
            labelled("description", dbc.Textarea(id="pkg-study-description", value=metadata.get("description") or "",
                                                 rows=5, placeholder="Biological question, design, sample origins and goals.")),
            dbc.Row([dbc.Col(labelled("submission date", dbc.Input(id="pkg-study-submission-date", type="date",
                                                                   value=metadata.get("submission_date") or "")), md=3),
                     dbc.Col(labelled("public release date", dbc.Input(id="pkg-study-release-date", type="date",
                                                                       value=metadata.get("public_release_date") or "")), md=3)]),
            subsection("people", "one entry per person; the principal investigator role exports its NCIT term"),
            html.Div(render_items("contact", metadata.get("contacts") or []), id="pkg-contacts"),
            add_button("add person", "pkg-contact-add"),
            html.Datalist(id="pkg-role-options", children=[html.Option(value=role) for role in roles]),
            subsection("publications", "record a manuscript as 'in preparation' if nothing is published yet"),
            html.Div(render_items("publication", metadata.get("publications") or [],
                                  statuses=vocabularies.get("publication_statuses", [])), id="pkg-publications"),
            add_button("add publication", "pkg-publication-add"),
            subsection("design", "study design descriptors; add the ontology term when known"),
            html.Div(render_items("descriptor", descriptors), id="pkg-descriptors"),
            add_button("add descriptor", "pkg-descriptor-add"),
            save_row("pkg-study"),
        ], disabled=not envelope.get("can_edit")),
    ])


def selected_source(choices, selection, assay_id):
    """The source chosen for an assay in the current selection, else its newest run."""
    assay = next((a for a in (choices or {}).get("assays", []) if a["assay_id"] == assay_id), None)
    if not assay or not assay["sources"]:
        return None
    value = ((selection or {}).get("sources") or {}).get(str(assay_id), "")
    return next((s for s in assay["sources"] if f"{s['job_id']}:{s['manifest_file_id']}" == value), assay["sources"][0])


def protocols_view(envelope, choices, selection):
    metadata = envelope["metadata"]
    sharing = envelope.get("protocol_sharing") or {}
    with_runs = [a["assay_id"] for a in metadata["assays"] if selected_source(choices, selection, a["assay_id"])]
    first_run = f"assay-{(with_runs or [a['assay_id'] for a in metadata['assays']] or [0])[0]}"
    tabs = []
    for assay in metadata["assays"]:
        source = selected_source(choices, selection, assay["assay_id"])
        facts = (" · ".join(filter(None, [source.get("polarity"), source.get("separation_mode"), source.get("column_name"),
                                          f"job {source['job_id']}"])) if source else "no completed run")
        shared = sharing.get(str(assay["assay_id"])) or {}
        fields = [html.Div(facts, className="font-monospace small text-muted mb-3")]
        for name in LAB_PROTOCOLS:
            hint = (html.Span(f"shared with {', '.join(shared[name])}", className="has-hint",
                              title="saving a change gives this assay its own copy") if name in shared else None)
            fields.append(labelled(name.lower(), dbc.Textarea(
                id={"type": "pkg-protocol", "assay": assay["assay_id"], "name": name},
                value=(assay.get("protocols") or {}).get(name, ""), rows=4, placeholder=PROTOCOL_HINTS[name]), hint))
        if source:
            fields.append(subsection("added at build", "from the selected run; not editable"))
            fields.extend(html.Div([html.Div(name.lower(), className="small text-muted"),
                                    html.P(text, className="small")]) for name, text in source["generated_protocols"].items())
        tabs.append(dbc.Tab(html.Div(fields, className="pt-3"), label=assay["assay_name"], tab_id=f"assay-{assay['assay_id']}"))
    return html.Div([
        section_header("protocols"),
        editable_note(envelope),
        html.Fieldset([
            labelled("sample collection", dbc.Textarea(
                id="pkg-collection-protocol", value=metadata.get("collection_protocol") or "", rows=4,
                placeholder="How samples were collected, handled and stored until extraction.")),
            dbc.Tabs(tabs, active_tab=first_run) if tabs else html.Div("no assays", className="text-muted"),
            save_row("pkg-protocols"),
        ], disabled=not envelope.get("can_edit")),
    ])


def sample_assays(envelope, choices):
    """Assays whose samples have values, in the Study's assay order."""
    linked = {key for sample in envelope["samples"] for key in (sample.get("assay_values") or {})}
    return [a for a in (choices or {}).get("assays", []) if str(a["assay_id"]) in linked]


def sample_rows(envelope, assay_id):
    rows = []
    for sample in envelope["samples"]:
        effective = (sample.get("assay_values") or {}).get(str(assay_id), {})
        term = ((sample.get("annotations") or {}).get("ontology_terms") or {}).get("organism") or {}
        rows.append(dict(sample_id=sample["sample_id"], name=sample["name"], sample_type=sample["sample_type"],
                         organism=sample["organism"], organism_part=sample["organism_part"],
                         organism_term=curie(term),
                         **{f"col:{column}": value for column, value in effective.items()}))
    return rows


def curie(term):
    """NCBITaxon:9606 from an OBO term URL, or a dash when no term is recorded."""
    if not term.get("accession"):
        return "—"
    local = term["accession"].rsplit("/", 1)[-1]
    return f"{term['source']}:{local.removeprefix(term['source'] + '_')}"


def sample_columns(envelope):
    fixed = [dict(id="name", name="name", editable=True),
             dict(id="sample_type", name="type", editable=True, presentation="dropdown"),
             dict(id="organism", name="organism", editable=True),
             dict(id="organism_part", name="organism part", editable=True),
             dict(id="organism_term", name="organism term", editable=False)]
    return fixed + [dict(id=f"col:{c['value']}", name=c["label"], editable=False) for c in envelope["factor_columns"]]


def samples_view(envelope, choices, selection):
    assays = sample_assays(envelope, choices)
    chosen = next((a["assay_id"] for a in assays if str(a["assay_id"]) in ((selection or {}).get("sources") or {})
                   and selection["sources"][str(a["assay_id"])] != "omit"), assays[0]["assay_id"] if assays else None)
    metadata = envelope["metadata"]
    factors = [{"name": f["name"], "source_column": f["source_column"], **{f"type_{k}": (f.get("type") or {}).get(k, "")
                for k in ("name", "source", "accession")}} for f in metadata.get("factors") or []]
    return html.Div([
        section_header("samples and factors", hint="names and characteristics stay editable after a run; "
                                                    "runs and builds keep what they froze"),
        editable_note(envelope),
        html.Div([dbc.Label("assay", className="small mb-1"),
                  dbc.Select(id="pkg-sample-assay", value=str(chosen) if chosen else None, size="sm",
                             options=[{"label": a["assay_name"], "value": str(a["assay_id"])} for a in assays])],
                 style={"maxWidth": "20rem"}, className="mb-3"),
        dash_table.DataTable(id="pkg-samples-table", data=sample_rows(envelope, chosen), columns=sample_columns(envelope),
                             editable=bool(envelope.get("can_edit")), page_size=25, style_table={"overflowX": "auto"},
                             dropdown={"sample_type": {"options": SAMPLE_TYPE_OPTIONS, "clearable": False}},
                             style_cell={"textAlign": "left", "padding": "6px 8px", "whiteSpace": "normal", "height": "auto"}),
        save_row("pkg-samples"),
        html.Fieldset([
            subsection("experimental factors", "name what a sample-sheet column means; batch and injection order "
                                               "are not biological factors"),
            html.Div(render_items("factor", factors, envelope), id="pkg-factors"),
            add_button("add factor", "pkg-factor-add"),
            save_row("pkg-factors"),
        ], disabled=not envelope.get("can_edit")),
    ])


# -- saving -------------------------------------------------------------------

def save_document(token, study_id, document):
    """PUT the whole document with its loaded revision; a conflict says to reload."""
    try:
        envelope = call_api("PUT", f"/studies/{study_id}/submission-metadata", token=token, payload=document)
    except APIError as error:
        return html.Span(str(error), className="text-danger"), dash.no_update
    return html.Span(f"saved · revision {envelope['metadata']['revision']}", className="font-monospace"), envelope


def changed_samples(rows, envelope):
    """PATCH payloads for samples whose editable values differ from the loaded ones."""
    loaded = {s["sample_id"]: s for s in envelope["samples"]}
    patches = []
    for row in rows or []:
        before = loaded.get(row["sample_id"])
        if not before:
            continue
        change = {field: (row.get(key) or "").strip() for field, key in
                  (("name", "name"), ("sample_type", "sample_type"), ("organism", "organism"), ("matrix", "organism_part"))
                  if (row.get(key) or "").strip() != (before.get(key) or "")}
        if change:
            patches.append((row["sample_id"], change))
    return patches


def register_callbacks(app, parse_route):
    """parse_route is the workspace's route parser: (study_id, section, revision)."""
    for kind, spec in REPEATABLE.items():
        def edit_items(add_clicks, remove_clicks, values, ids, choices, envelope, kind=kind, spec=spec):
            rows = collect(kind, ids, values)
            if ctx.triggered_id == spec["add"]:
                if not add_clicks:
                    raise PreventUpdate
                rows.append({})
            elif isinstance(ctx.triggered_id, dict) and ctx.triggered and ctx.triggered[0]["value"]:
                positions = sorted({identity["index"] for identity in ids or []})
                if ctx.triggered_id["index"] in positions:
                    del rows[positions.index(ctx.triggered_id["index"])]
            else:
                raise PreventUpdate
            statuses = ((choices or {}).get("vocabularies") or {}).get("publication_statuses", [])
            return render_items(kind, rows, envelope, statuses)

        edit_items.__name__ = f"edit_{kind}s"
        app.callback(Output(spec["container"], "children"), Input(spec["add"], "n_clicks"),
                     Input({"type": f"pkg-{kind}-remove", "index": ALL}, "n_clicks"),
                     State({"type": f"pkg-{kind}", "field": ALL, "index": ALL}, "value"),
                     State({"type": f"pkg-{kind}", "field": ALL, "index": ALL}, "id"),
                     State("pkg-choices", "data"), State("pkg-metadata", "data"), prevent_initial_call=True)(edit_items)

    @app.callback(Output({"type": "pkg-factor-values", "index": MATCH}, "children"),
                  Input({"type": "pkg-factor", "field": "source_column", "index": MATCH}, "value"),
                  State("pkg-metadata", "data"), prevent_initial_call=True)
    def show_factor_values(column, envelope):
        return factor_values(column, envelope)

    @app.callback(Output("pkg-study-message", "children"), Output("pkg-metadata", "data", allow_duplicate=True),
                  Input("pkg-study-save", "n_clicks"),
                  State("pkg-study-title", "value"), State("pkg-study-description", "value"),
                  State("pkg-study-submission-date", "value"), State("pkg-study-release-date", "value"),
                  *(State({"type": f"pkg-{kind}", "field": ALL, "index": ALL}, prop)
                    for kind in ("contact", "publication", "descriptor") for prop in ("value", "id")),
                  State("pkg-metadata", "data"), State("url", "pathname"), State("token-store", "data"),
                  prevent_initial_call=True)
    def save_study(clicks, title, description, submitted, released, contact_values, contact_ids, publication_values,
                   publication_ids, descriptor_values, descriptor_ids, envelope, path, token):
        study_id, _, _ = parse_route(path)
        if not clicks or not study_id or not envelope:
            raise PreventUpdate
        document = deepcopy(envelope["metadata"])
        document.update(
            title=(title or "").strip(), description=(description or "").strip(),
            submission_date=submitted or None, public_release_date=released or None,
            contacts=filled(collect("contact", contact_ids, contact_values), REPEATABLE["contact"]["fields"]),
            publications=filled(collect("publication", publication_ids, publication_values), REPEATABLE["publication"]["fields"]),
            design_descriptors=filled(collect("descriptor", descriptor_ids, descriptor_values), REPEATABLE["descriptor"]["fields"]))
        return save_document(token, study_id, document)

    @app.callback(Output("pkg-protocols-message", "children"), Output("pkg-metadata", "data", allow_duplicate=True),
                  Input("pkg-protocols-save", "n_clicks"), State("pkg-collection-protocol", "value"),
                  State({"type": "pkg-protocol", "assay": ALL, "name": ALL}, "value"),
                  State({"type": "pkg-protocol", "assay": ALL, "name": ALL}, "id"),
                  State("pkg-metadata", "data"), State("url", "pathname"), State("token-store", "data"),
                  prevent_initial_call=True)
    def save_protocols(clicks, collection, values, ids, envelope, path, token):
        study_id, _, _ = parse_route(path)
        if not clicks or not study_id or not envelope:
            raise PreventUpdate
        document = deepcopy(envelope["metadata"])
        document["collection_protocol"] = (collection or "").strip()
        texts = {(identity["assay"], identity["name"]): (value or "").strip() for identity, value in zip(ids, values)}
        for assay in document["assays"]:
            assay["protocols"] = {name: texts.get((assay["assay_id"], name), (assay.get("protocols") or {}).get(name, ""))
                                  for name in LAB_PROTOCOLS}
        return save_document(token, study_id, document)

    @app.callback(Output("pkg-factors-message", "children"), Output("pkg-metadata", "data", allow_duplicate=True),
                  Input("pkg-factors-save", "n_clicks"),
                  State({"type": "pkg-factor", "field": ALL, "index": ALL}, "value"),
                  State({"type": "pkg-factor", "field": ALL, "index": ALL}, "id"),
                  State("pkg-metadata", "data"), State("url", "pathname"), State("token-store", "data"),
                  prevent_initial_call=True)
    def save_factors(clicks, values, ids, envelope, path, token):
        study_id, _, _ = parse_route(path)
        if not clicks or not study_id or not envelope:
            raise PreventUpdate
        document = deepcopy(envelope["metadata"])
        document["factors"] = [
            {"name": row["name"], "source_column": row["source_column"],
             "type": {key: row[f"type_{key}"] for key in ("name", "source", "accession")}}
            for row in filled(collect("factor", ids, values), REPEATABLE["factor"]["fields"])]
        return save_document(token, study_id, document)

    @app.callback(Output("pkg-samples-table", "data"), Input("pkg-sample-assay", "value"),
                  State("pkg-metadata", "data"), prevent_initial_call=True)
    def show_assay_values(assay_id, envelope):
        if not envelope or not assay_id:
            raise PreventUpdate
        return sample_rows(envelope, assay_id)

    @app.callback(Output("pkg-samples-message", "children"), Output("pkg-metadata", "data", allow_duplicate=True),
                  Input("pkg-samples-save", "n_clicks"), State("pkg-samples-table", "data"),
                  State("pkg-metadata", "data"), State("url", "pathname"), State("token-store", "data"),
                  prevent_initial_call=True)
    def save_samples(clicks, rows, envelope, path, token):
        study_id, _, _ = parse_route(path)
        if not clicks or not study_id or not envelope:
            raise PreventUpdate
        patches = changed_samples(rows, envelope)
        if not patches:
            return html.Span("no changes", className="text-muted"), dash.no_update
        try:
            for sample_id, change in patches:
                call_api("PATCH", f"/studies/{study_id}/samples/{sample_id}", token=token, payload=change)
            refreshed = call_api("GET", f"/studies/{study_id}/submission-metadata", token=token)
        except APIError as error:
            return html.Span(str(error), className="text-danger"), dash.no_update
        return html.Span(f"updated {len(patches)} sample{'s' if len(patches) > 1 else ''}", className="font-monospace"), refreshed
