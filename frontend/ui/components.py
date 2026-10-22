import dash_bootstrap_components as dbc
from dash import html
from dash.development.base_component import Component

from ui.formatting import assay_kind_label, state_badge, status_label


def icon(name: str) -> html.Span:
    """A stroke icon from assets/icons, drawn in the current text colour (ui_light.css)."""
    return html.Span(className=f"ml-icon ml-icon-{name}", **{"aria-hidden": "true"})


def icon_button(name: str, component_id: dict | str, label: str, disabled: bool = False) -> html.Button:
    """A square icon action that names itself on hover and to screen readers."""
    return html.Button(icon(name), id=component_id, n_clicks=0, title=label, disabled=disabled,
                       className="icon-action", **{"aria-label": label})


def section_card(title: str, body: list) -> dbc.Card:
    return dbc.Card(
        dbc.CardBody([html.H4(title, className="mb-3 section-title"), *body]),
        className="metabo-card mb-4",
    )


def page_intro(
    title: str,
    description: str | None = None,
    eyebrow: str | None = None,
    meta: object | None = None,
    actions: object | None = None,
) -> html.Div:
    left_children = []
    if eyebrow:
        left_children.append(html.Div(eyebrow, className="page-intro-eyebrow"))
    left_children.extend(
        [
            html.H2(title, className="page-intro-title section-title"),
            html.P(description, className="page-intro-description") if description else None,
        ]
    )

    side_children = []
    if meta is not None:
        side_children.append(html.Div(meta, className="page-intro-meta"))
    if actions is not None:
        side_children.append(html.Div(actions, className="page-intro-actions"))

    return html.Div(
        [
            html.Div(left_children, className="page-intro-main"),
            html.Div(side_children, className="page-intro-side") if side_children else None,
        ],
        className="page-intro",
    )


def section_header(title: str, helper: str | None = None, action: object | None = None, eyebrow: str | None = None,
                   hint: str | None = None) -> html.Div:
    """``hint`` is shown on hover over the heading instead of as a line under it."""
    copy_children = []
    if eyebrow:
        copy_children.append(html.Div(eyebrow, className="section-eyebrow"))
    copy_children.append(html.H5(title, className="section-heading has-hint" if hint else "section-heading", title=hint))
    if helper:
        copy_children.append(html.P(helper, className="section-helper"))

    return html.Div(
        [
            html.Div(copy_children, className="section-header-copy"),
            html.Div(action, className="section-header-action") if action is not None else None,
        ],
        className="section-header",
    )


def metadata_input(label: str, field_id: str, value: str | None = None, textarea: bool = False, md: int = 6) -> dbc.Col:
    control = (
        dbc.Textarea(id=field_id, value=value, className="mb-0", style={"height": "96px"})
        if textarea
        else dbc.Input(id=field_id, type="text", value=value, className="mb-0")
    )
    return dbc.Col([dbc.Label(label, html_for=field_id, className="mb-1"), control], md=md, className="mb-3")


# The review boards (feature review, IS-QC) share their DataTable styling so the
# two read as one design. Each board adds its own row highlighting.
REVIEW_TABLE_STYLE = {
    "style_table": {"overflowX": "auto"},
    "style_cell": {"textAlign": "left", "fontSize": "12px", "maxWidth": "180px", "whiteSpace": "normal"},
}


def render_table(headers: list[str], rows: list[list[object]]) -> dbc.Table:
    body_rows = rows or [[html.Span("No records yet", className="text-muted")] + [""] * (len(headers) - 1)]
    table_rows = []
    for row in body_rows:
        cells = []
        for value in row:
            if isinstance(value, Component):
                cells.append(html.Td(value))
            else:
                cells.append(html.Td("-" if value is None else str(value)))
        table_rows.append(html.Tr(cells))

    return dbc.Table(
        [
            html.Thead(html.Tr([html.Th(h) for h in headers])),
            html.Tbody(table_rows),
        ],
        bordered=True,
        striped=True,
        hover=True,
        responsive=True,
        className="embl-table align-middle mb-0",
    )


def render_field_value_table(rows: list[list[object]]) -> dbc.Table:
    body_rows = rows or [[html.Span("No records yet", className="text-muted"), ""]]
    table_rows = []
    for row in body_rows:
        cells = []
        for value in row:
            if isinstance(value, Component):
                cells.append(html.Td(value))
            else:
                cells.append(html.Td("-" if value is None else str(value)))
        table_rows.append(html.Tr(cells))

    return dbc.Table(
        [
            html.Colgroup(
                [
                    html.Col(style={"width": "32%"}),
                    html.Col(style={"width": "68%"}),
                ]
            ),
            html.Thead(html.Tr([html.Th("Field"), html.Th("Value")])),
            html.Tbody(table_rows),
        ],
        bordered=True,
        striped=True,
        hover=True,
        responsive=True,
        className="embl-table align-middle mb-0",
    )


def render_scrollable_table(headers: list[str], rows: list[list[object]], max_height: str = "320px") -> html.Div:
    return html.Div(
        render_table(headers, rows),
        className="table-scroll-panel",
        style={"maxHeight": max_height},
    )


def build_selection_reminder(
    selected_study_id: int | None,
    studies_data: list[dict] | None,
    selected_assay_id: int | None,
    assays_data: list[dict] | None,
    compact: bool = False,
) -> html.Div:
    selected_study = next((study for study in (studies_data or []) if study.get("id") == selected_study_id), None)
    selected_assay = next((assay for assay in (assays_data or []) if assay.get("id") == selected_assay_id), None)

    if not selected_study_id:
        return html.Div(
            [
                html.Div("No study selected", className="selection-reminder-name"),
                html.Div("Use Select in the Studies page.", className="selection-reminder-empty"),
            ],
            className="selection-reminder-stack",
        )

    study_name = selected_study.get("title") if selected_study else f"Study #{selected_study_id}"
    study_status = selected_study.get("status") if selected_study else None
    study_meta = [html.Span(f"#{selected_study_id}", className="selection-reminder-id"), state_badge(study_status)]

    study_block = html.Div(
        [
            html.Div("Study", className="selection-reminder-label"),
            html.Div(study_name, className="selection-reminder-name"),
            html.Div(study_meta, className="selection-reminder-meta"),
        ],
        className="selection-reminder-block",
    )

    if not selected_assay_id:
        assay_block = html.Div(
            [
                html.Div("Assay", className="selection-reminder-label"),
                html.Div("No assay selected", className="selection-reminder-name"),
                html.Div("Select or create one in the Study page.", className="selection-reminder-empty"),
            ],
            className="selection-reminder-block",
        )
    else:
        assay_name = selected_assay.get("name") if selected_assay else f"Assay #{selected_assay_id}"
        assay_status = selected_assay.get("status") if selected_assay else None
        assay_kind = assay_kind_label(selected_assay)
        assay_meta_children = [html.Span(f"#{selected_assay_id}", className="selection-reminder-id")]
        if assay_kind != "-":
            assay_meta_children.append(html.Span(assay_kind, className="selection-reminder-chip"))
        assay_meta_children.append(state_badge(assay_status))
        assay_block = html.Div(
            [
                html.Div("Assay", className="selection-reminder-label"),
                html.Div(assay_name, className="selection-reminder-name"),
                html.Div(assay_meta_children, className="selection-reminder-meta"),
            ],
            className="selection-reminder-block",
        )

    classes = "selection-reminder selection-reminder-compact" if compact else "selection-reminder"
    if compact:
        compact_assay_name = selected_assay.get("name") if selected_assay else "No assay"
        compact_badges = [state_badge(study_status)]
        if selected_assay_id:
            compact_badges.append(html.Span(">", className="selection-reminder-badge-separator"))
            compact_badges.append(state_badge(selected_assay.get("status") if selected_assay else None))
        return html.Details(
            [
                html.Summary(
                    html.Div(
                        [
                            html.Div("Study > Assay", className="selection-reminder-summary-label"),
                            html.Div(
                                f"{study_name} > {compact_assay_name}",
                                className="selection-reminder-summary-line",
                            ),
                            html.Div(compact_badges, className="selection-reminder-summary-badges"),
                        ],
                        className="selection-reminder-summary-stack",
                    ),
                    className="selection-reminder-toggle-summary",
                ),
                html.Div([study_block, assay_block], className="selection-reminder-toggle-body"),
            ],
            className="selection-reminder-toggle",
        )

    return html.Div(
        [
            html.Div("Selected context", className="selection-reminder-title"),
            study_block,
            assay_block,
            html.Div(
                f"Study status: {status_label(study_status)}. "
                f"Assay status: {status_label(selected_assay.get('status') if selected_assay else None) if selected_assay_id else 'No assay selected'}",
                className="selection-reminder-summary",
            ),
        ],
        className=classes,
    )


def build_page_context_summary(
    selected_study_id: int | None,
    studies_data: list[dict] | None,
    selected_assay_id: int | None,
    assays_data: list[dict] | None,
) -> html.Div:
    selected_study = next((study for study in (studies_data or []) if study.get("id") == selected_study_id), None)
    selected_assay = next((assay for assay in (assays_data or []) if assay.get("id") == selected_assay_id), None)

    if not selected_study_id:
        return html.Div(
            [
                html.Div("Study > Assay", className="page-context-label"),
                html.Div("No active study", className="page-context-line"),
                html.Div("Select a study in the Studies page before loading files.", className="page-context-helper"),
            ],
            className="page-context-card",
        )

    study_name = selected_study.get("title") if selected_study else f"Study #{selected_study_id}"
    study_status = selected_study.get("status") if selected_study else None
    study_line = html.Span(study_name, className="page-context-name")
    study_id_chip = html.Span(f"#{selected_study_id}", className="page-context-chip")

    if not selected_assay_id:
        badges = [study_id_chip, state_badge(study_status)]
        return html.Div(
            [
                html.Div("Study > Assay", className="page-context-label"),
                html.Div([study_line, html.Span(">", className="page-context-separator"), html.Span("No assay selected", className="page-context-empty")], className="page-context-line"),
                html.Div(badges, className="page-context-badges"),
            ],
            className="page-context-card",
        )

    assay_name = selected_assay.get("name") if selected_assay else f"Assay #{selected_assay_id}"
    assay_status = selected_assay.get("status") if selected_assay else None
    assay_kind = assay_kind_label(selected_assay)

    badges = [study_id_chip, state_badge(study_status), html.Span(">", className="page-context-separator"), html.Span(f"#{selected_assay_id}", className="page-context-chip")]
    if assay_kind != "-":
        badges.append(html.Span(assay_kind, className="page-context-chip"))
    badges.append(state_badge(assay_status))

    return html.Div(
        [
            html.Div("Study > Assay", className="page-context-label"),
            html.Div(
                [
                    study_line,
                    html.Span(">", className="page-context-separator"),
                    html.Span(assay_name, className="page-context-name"),
                ],
                className="page-context-line",
            ),
            html.Div(badges, className="page-context-badges"),
        ],
        className="page-context-card",
    )
