import plotly.graph_objects as go
from plotly.colors import hex_to_rgb, qualitative

from constants import SAMPLE_TYPE_COLOR_MAP
from ui.formatting import metabolomics_assay_type_label, sample_type_label, status_label


def empty_structure_figure(message: str = "No study structure available.") -> go.Figure:
    figure = go.Figure()
    figure.update_layout(
        height=520,
        margin={"l": 16, "r": 16, "t": 24, "b": 16},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        annotations=[
            {
                "text": message,
                "xref": "paper",
                "yref": "paper",
                "x": 0.5,
                "y": 0.5,
                "showarrow": False,
                "font": {"size": 15, "color": "#5B6577"},
            }
        ],
    )
    figure.update_xaxes(visible=False)
    figure.update_yaxes(visible=False)
    return figure


def empty_eic_figure(message: str = "Select an EIC archive and feature.") -> go.Figure:
    figure = go.Figure()
    figure.update_layout(
        height=460,
        margin={"l": 56, "r": 24, "t": 32, "b": 48},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        annotations=[
            {
                "text": message,
                "xref": "paper",
                "yref": "paper",
                "x": 0.5,
                "y": 0.5,
                "showarrow": False,
                "font": {"size": 15, "color": "#5B6577"},
            }
        ],
    )
    figure.update_xaxes(visible=False)
    figure.update_yaxes(visible=False)
    return figure


def empty_feature_map_figure(message: str = "Select an EIC archive to show aligned features.") -> go.Figure:
    figure = go.Figure()
    figure.update_layout(
        height=360,
        margin={"l": 56, "r": 24, "t": 32, "b": 48},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        annotations=[
            {
                "text": message,
                "xref": "paper",
                "yref": "paper",
                "x": 0.5,
                "y": 0.5,
                "showarrow": False,
                "font": {"size": 15, "color": "#5B6577"},
            }
        ],
    )
    figure.update_xaxes(visible=False)
    figure.update_yaxes(visible=False)
    return figure


def build_feature_map_figure(features: list[dict] | None) -> go.Figure:
    plottable_features = [
        feature for feature in (features or [])
        if feature.get("average_rt") is not None and feature.get("average_mz") is not None
    ]
    if not plottable_features:
        return empty_feature_map_figure("No aligned features with RT and m/z were found.")

    figure = go.Figure()
    groups = [
        ("MS/MS available", True, "#006A8E"),
        ("No MS/MS", False, "#8A94A6"),
    ]
    for name, has_ms2, color in groups:
        rows = [feature for feature in plottable_features if bool(feature.get("has_ms2")) is has_ms2]
        if not rows:
            continue
        customdata = [
            [
                feature.get("feature_id", feature.get("feature_index")),
                feature.get("source_feature_id", feature.get("alignment_id")),
                feature.get("metabolite_name") or "Unknown",
                feature.get("adduct_type") or "",
                "Annotated" if feature.get("annotated") else "Unknown",
                "MS/MS available" if feature.get("has_ms2") else "No MS/MS",
            ]
            for feature in rows
        ]
        figure.add_trace(
            go.Scattergl(
                x=[feature.get("average_rt") for feature in rows],
                y=[feature.get("average_mz") for feature in rows],
                mode="markers",
                name=name,
                customdata=customdata,
                marker={"size": 6, "color": color, "opacity": 0.72},
                hovertemplate=(
                    "Feature %{customdata[0]} | Source %{customdata[1]}<br>"
                    "%{customdata[2]}<br>"
                    "RT %{x:.3f} min | m/z %{y:.5f}<br>"
                    "Adduct %{customdata[3]}<br>"
                    "%{customdata[4]} | %{customdata[5]}"
                    "<extra></extra>"
                ),
            )
        )

    figure.update_layout(
        title={"text": "RT vs m/z Feature Map", "font": {"size": 15}},
        height=360,
        margin={"l": 56, "r": 24, "t": 56, "b": 48},
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "left", "x": 0},
        clickmode="event+select",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    figure.update_xaxes(title_text="Retention time (min)", showgrid=True, gridcolor="rgba(112,115,114,0.18)")
    figure.update_yaxes(title_text="Precursor m/z", showgrid=True, gridcolor="rgba(112,115,114,0.18)")
    return figure


def build_eic_figure(payload: dict | None) -> go.Figure:
    if not payload:
        return empty_eic_figure()

    feature = payload.get("feature") or {}
    spot = payload.get("spot") or {}
    peaks = spot.get("peaks") or []
    if not peaks:
        return empty_eic_figure("No EIC traces are available for the selected feature.")

    title_parts = [feature.get("metabolite_name") or "Unknown"]
    if feature.get("average_mz") is not None:
        title_parts.append(f"m/z {feature['average_mz']:.5f}")
    if feature.get("average_rt") is not None:
        title_parts.append(f"RT {feature['average_rt']:.2f} min")

    figure = go.Figure()
    apex_lines = []
    for sample_index, peak in enumerate(peaks):
        points = peak.get("points") or []
        if not points:
            continue
        x_values = [point.get("x") for point in points]
        y_values = [point.get("intensity") for point in points]
        sample_name = peak.get("sample_name") or f"File {peak.get('file_id')}"
        color = qualitative.Plotly[sample_index % len(qualitative.Plotly)]
        legend_group = str(sample_index)
        left, right = peak.get("left"), peak.get("right")
        fill_x, fill_y = [], []
        if left is not None and right is not None and left < right:
            # Clip each displayed line segment, interpolating at saved boundaries.
            for x0, x1, y0, y1 in zip(x_values, x_values[1:], y_values, y_values[1:]):
                start, end = max(left, x0), min(right, x1)
                if start >= end:
                    continue
                if not fill_x:
                    fill_x.append(start)
                    fill_y.append(y0 + (y1 - y0) * (start - x0) / (x1 - x0))
                fill_x.append(end)
                fill_y.append(y0 + (y1 - y0) * (end - x0) / (x1 - x0))
        if fill_x:
            red, green, blue = hex_to_rgb(color)
            figure.add_trace(go.Scatter(
                x=fill_x, y=fill_y, mode="lines", line={"width": 0},
                fill="tozeroy", fillcolor=f"rgba({red},{green},{blue},0.15)",
                legendgroup=legend_group, showlegend=False, hoverinfo="skip",
            ))
        figure.add_trace(
            go.Scatter(
                x=x_values,
                y=y_values,
                mode="lines",
                name=sample_name,
                legendgroup=legend_group,
                line={"width": 1.8, "color": color},
                hovertemplate=f"{sample_name}<br>RT %{{x:.3f}}<br>Intensity %{{y:.0f}}<extra></extra>",
            )
        )
        if peak.get("top") is not None:
            apex_lines.append(dict(type="line", x0=peak["top"], x1=peak["top"], xref="x",
                                   y0=0, y1=1, yref="y domain",
                                   line=dict(width=1, dash="dot", color="rgba(24,151,76,0.25)")))

    figure.update_layout(
        shapes=apex_lines,
        title={"text": " | ".join(title_parts), "font": {"size": 15}},
        height=460,
        margin={"l": 56, "r": 24, "t": 56, "b": 48},
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "left", "x": 0},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    figure.update_xaxes(title_text="Retention time (min)", showgrid=True, gridcolor="rgba(112,115,114,0.18)")
    figure.update_yaxes(title_text="Intensity", showgrid=True, gridcolor="rgba(112,115,114,0.18)")
    return figure


def build_is_qc_trace_figure(payload: dict | None, rt_view: str = "original") -> go.Figure:
    if not payload or not payload.get("traces"):
        return empty_eic_figure("Select an IS target to show internal-standard traces.")

    view = rt_view if rt_view in {"original", "corrected", "both"} else "original"
    traces = payload.get("traces") or []
    figure = go.Figure()
    if payload.get("target_name") == "__all__":
        labels = [
            f"{trace.get('sample_name')} · {trace.get('target_name')}"
            for trace in traces
        ]
        marker_symbols = [
            "circle" if trace.get("used_for_rt_model") is not False else "circle-open"
            for trace in traces
        ]
        marker_colors = [
            "#006A8E" if trace.get("used_for_rt_model") is not False else "#9AA3A0"
            for trace in traces
        ]
        if view in {"original", "both"}:
            figure.add_trace(go.Scatter(
                x=[trace.get("observed_rt_min") for trace in traces],
                y=labels,
                mode="markers",
                name="Original RT",
                marker={"symbol": marker_symbols, "color": marker_colors, "size": 9},
                hovertemplate="%{y}<br>Original RT %{x:.3f} min<extra></extra>",
            ))
        if view in {"corrected", "both"}:
            corrected_colors = [
                "#18974c" if trace.get("used_for_rt_model") is not False else "#9AA3A0"
                for trace in traces
            ]
            figure.add_trace(go.Scatter(
                x=[trace.get("corrected_observed_rt_min") for trace in traces],
                y=labels,
                mode="markers",
                name="Corrected RT",
                marker={"symbol": marker_symbols, "color": corrected_colors, "size": 9},
                hovertemplate="%{y}<br>Corrected RT %{x:.3f} min<extra></extra>",
            ))
        figure.update_yaxes(title_text="Sample · internal standard")
    else:
        for trace in traces:
            points = trace.get("points") or []
            sample_name = trace.get("sample_name") or trace.get("input_file") or "Sample"
            sample_type = str(trace.get("sample_type") or "other").strip().lower()
            color = SAMPLE_TYPE_COLOR_MAP.get(sample_type, SAMPLE_TYPE_COLOR_MAP["other"])
            if view in {"original", "both"}:
                figure.add_trace(go.Scatter(
                    x=[point.get("rt_min") for point in points],
                    y=[point.get("intensity") for point in points],
                    mode="lines",
                    name=f"{sample_name} · original" if view == "both" else sample_name,
                    line={"width": 1.8, "color": color},
                    hovertemplate=f"{sample_name}<br>Original RT %{{x:.3f}} min<br>Intensity %{{y:.0f}}<extra></extra>",
                ))
            if view in {"corrected", "both"}:
                figure.add_trace(go.Scatter(
                    x=[point.get("corrected_rt_min") for point in points],
                    y=[point.get("intensity") for point in points],
                    mode="lines",
                    name=f"{sample_name} · corrected" if view == "both" else sample_name,
                    line={"width": 1.8, "color": color, "dash": "dash"},
                    hovertemplate=f"{sample_name}<br>Corrected RT %{{x:.3f}} min<br>Intensity %{{y:.0f}}<extra></extra>",
                ))
        expected_rt = next(
            (
                trace.get("expected_rt_min")
                for trace in traces
                if trace.get("expected_rt_min") is not None
            ),
            None,
        )
        cohort_rt = next(
            (
                trace.get("cohort_mean_rt_min")
                for trace in traces
                if trace.get("cohort_mean_rt_min") is not None
            ),
            None,
        )
        if expected_rt is not None:
            figure.add_vline(
                x=expected_rt,
                line_width=2,
                line_dash="dash",
                line_color="#006A8E",
            )
        if cohort_rt is not None and view in {"corrected", "both"}:
            figure.add_vline(
                x=cohort_rt,
                line_width=2,
                line_dash="dot",
                line_color="#18974c",
            )

    target_name = (
        "All standards (RT overview)"
        if payload.get("target_name") == "__all__"
        else payload.get("target_name") or "Internal standard"
    )
    figure.update_layout(
        title={"text": target_name, "font": {"size": 15}},
        height=420,
        margin={"l": 56, "r": 24, "t": 56, "b": 48},
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "left", "x": 0},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    figure.update_xaxes(title_text="Retention time (min)", showgrid=True, gridcolor="rgba(112,115,114,0.18)")
    if payload.get("target_name") != "__all__":
        figure.update_yaxes(title_text="Intensity", showgrid=True, gridcolor="rgba(112,115,114,0.18)")
    else:
        figure.update_yaxes(showgrid=True, gridcolor="rgba(112,115,114,0.18)")
    return figure


def structure_node_color(node: dict) -> str:
    if node.get("node_type") == "study":
        return "#009B77"
    if node.get("node_type") == "assay":
        return "#C9E27B"
    if node.get("node_type") == "dataset":
        dataset_kind = (node.get("dataset_kind") or "").strip().lower()
        if dataset_kind == "raw":
            return "#D9E6C3"
        if dataset_kind == "derived":
            return "#B7D7A8"
        if dataset_kind == "maf":
            return "#8FBF7A"
        return "#DDE3D7"
    if node.get("node_type") == "sample":
        details = node.get("details") or {}
        sample_type = str(details.get("sample_type") or "sample").strip().lower()
        return SAMPLE_TYPE_COLOR_MAP.get(sample_type, SAMPLE_TYPE_COLOR_MAP["other"])
    return "#F4F4F4"


def structure_hover_text(node: dict) -> str:
    details = node.get("details") or {}
    lines = [f"Node: {str(node.get('node_type') or '').replace('_', ' ').title()}"]
    if details.get("identifier"):
        lines.append(f"Identifier: {details['identifier']}")
    if details.get("status"):
        lines.append(f"Status: {status_label(details['status'])}")
    if details.get("platform"):
        lines.append(f"Platform: {str(details['platform']).replace('_', ' ').title()}")
    if details.get("metabolomics_assay_type"):
        lines.append(f"Metabolomics type: {metabolomics_assay_type_label(details['metabolomics_assay_type'])}")
    if "is_default" in details:
        lines.append(f"Default assay: {'Yes' if details['is_default'] else 'No'}")
    if details.get("dataset_kind"):
        lines.append(f"Dataset kind: {details['dataset_kind']}")
    if details.get("dataset_type"):
        lines.append(f"Dataset type: {details['dataset_type']}")
    if details.get("file_count") is not None:
        lines.append(f"Files: {details['file_count']}")
    if details.get("sample_name"):
        lines.append(f"Sample: {details['sample_name']}")
    if details.get("sample_id") is not None:
        lines.append(f"Sample ID: {details['sample_id']}")
    if details.get("sample_type"):
        lines.append(f"Sample type: {sample_type_label(details['sample_type'])}")
    if details.get("organism"):
        lines.append(f"Organism: {details['organism']}")
    if details.get("matrix"):
        lines.append(f"Matrix: {details['matrix']}")
    if details.get("sample_count") is not None and node.get("node_type") == "bucket":
        lines.append(f"Sample count: {details['sample_count']}")
    return "<br>".join(lines)


def build_study_structure_figure(structure: dict | None) -> go.Figure:
    nodes = (structure or {}).get("nodes") or []
    visible_nodes = [node for node in nodes if int(node.get("value") or 0) > 0]
    if not visible_nodes:
        return empty_structure_figure("No study structure available for the selected study.")

    return go.Figure(
        go.Icicle(
            ids=[str(node.get("id")) for node in visible_nodes],
            labels=[str(node.get("label") or "-") for node in visible_nodes],
            parents=[str(node.get("parent_id") or "") for node in visible_nodes],
            values=[int(node.get("value") or 0) for node in visible_nodes],
            branchvalues="total",
            textinfo="label",
            sort=False,
            marker={"colors": [structure_node_color(node) for node in visible_nodes]},
            customdata=[
                [
                    node.get("node_type"),
                    node.get("assay_id"),
                    node.get("dataset_id"),
                    node.get("sample_id"),
                ]
                for node in visible_nodes
            ],
            hovertext=[structure_hover_text(node) for node in visible_nodes],
            hovertemplate="%{label}<br>%{hovertext}<extra></extra>",
            tiling={"pad": 4},
            root={"color": "#F4F4F4"},
        )
    ).update_layout(
        height=520,
        margin={"l": 16, "r": 16, "t": 24, "b": 16},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )


# In a mirror plot position already says which spectrum a stick belongs to, so
# colour is free to carry the question actually being asked: which fragments the
# reference explains. Both were validated against the white card with the
# palette checker -- lightness band, chroma floor, CVD separation, normal-vision
# separation and 3:1 contrast all pass. They are deliberately not
# STATE_COLOR_MAP values, which stay reserved for run state.
MS2_MATCHED_COLOR = "#0E82B4"
MS2_UNMATCHED_COLOR = "#B87D18"


def empty_ms2_figure(message: str = "Select a feature to show its MS2 spectrum.") -> go.Figure:
    figure = go.Figure()
    figure.update_layout(
        height=300,
        margin={"l": 56, "r": 24, "t": 32, "b": 48},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        annotations=[
            {
                "text": message,
                "xref": "paper",
                "yref": "paper",
                "x": 0.5,
                "y": 0.5,
                "showarrow": False,
                "font": {"size": 15, "color": "#5B6577"},
            }
        ],
    )
    figure.update_xaxes(visible=False)
    figure.update_yaxes(visible=False)
    return figure


def build_ms2_mirror_figure(payload: dict | None) -> go.Figure:
    """Acquired MS2 above, the run's library reference mirrored below.

    Both halves are normalised to a 100 base peak so they can be compared by
    eye. Nothing here is rescored: the similarity numbers shown beside the plot
    come from the processing engine, and colour only reports which sticks fall within the match
    tolerance of a reference fragment.
    """
    ms2 = (payload or {}).get("ms2") or {}
    experimental = ms2.get("experimental") or []
    if not experimental:
        return empty_ms2_figure("No MS2 spectrum was acquired for this feature.")
    reference_entry = ms2.get("reference") or {}
    reference = reference_entry.get("peaks") or []

    positions = [peak["mz"] for peak in experimental] + [peak["mz"] for peak in reference]
    span = (max(positions) - min(positions)) if len(positions) > 1 else 1.0
    # Sticks scale with the displayed m/z range; a fixed width is a hairline
    # across 400 Da and a slab across 20.
    width = min(max(span / 260.0, 0.08), 1.2)

    figure = go.Figure()
    seen_labels: set[str] = set()
    halves = [(experimental, 1, "Experimental")]
    if reference:
        halves.append((reference, -1, "Reference"))

    for peaks, sign, side in halves:
        # Without a reference nothing has been compared, so the sticks carry no
        # matched/unmatched claim and the plot shows one unlabelled series.
        groups = (
            ((True, MS2_MATCHED_COLOR, "Matched"), (False, MS2_UNMATCHED_COLOR, "Unmatched"))
            if reference
            else ((False, MS2_MATCHED_COLOR, None),)
        )
        for matched, color, label in groups:
            subset = [peak for peak in peaks if bool(peak.get("matched")) is matched]
            if not subset:
                continue
            figure.add_trace(
                go.Bar(
                    x=[peak["mz"] for peak in subset],
                    y=[sign * peak["intensity"] for peak in subset],
                    customdata=[peak["intensity"] for peak in subset],
                    width=width,
                    marker={"color": color, "line": {"width": 0}},
                    name=label or "Spectrum",
                    legendgroup=label or "spectrum",
                    showlegend=bool(label) and label not in seen_labels,
                    hovertemplate=f"{side}<br>m/z %{{x:.4f}}<br>%{{customdata:.1f}}% of base peak<extra></extra>",
                )
            )
            if label:
                seen_labels.add(label)

    # A matched fragment appears at the same m/z in both halves, so without a
    # baseline drawn over the bars the pair reads as one stick through the axis.
    figure.add_hline(y=0, line_width=2, line_color="rgba(0,0,0,0.55)", layer="above")
    # Direct labels, so which half is which never depends on reading the legend.
    annotations = [{
        "text": "Acquired", "xref": "paper", "yref": "paper", "x": 0, "y": 1.0,
        "xanchor": "left", "yanchor": "bottom", "showarrow": False,
        "font": {"size": 11, "color": "#5B6577"},
    }]
    if reference:
        reference_name = reference_entry.get("name") or "Library reference"
        annotations.append({
            # Below the tick labels, not at the plot floor, where it landed on them.
            "text": f"Reference · {reference_name}", "xref": "paper", "yref": "paper",
            "x": 0, "y": -0.30, "xanchor": "left", "yanchor": "top", "showarrow": False,
            "font": {"size": 11, "color": "#5B6577"},
        })

    figure.update_layout(
        height=300,
        barmode="overlay",
        margin={"l": 56, "r": 24, "t": 34, "b": 74},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        annotations=annotations,
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "right", "x": 1},
        hovermode="closest",
    )
    figure.update_xaxes(title_text="m/z", gridcolor="rgba(0,0,0,0.06)", zeroline=False)
    figure.update_yaxes(
        title_text="Relative intensity (%)",
        gridcolor="rgba(0,0,0,0.06)",
        zeroline=False,
        range=[-118, 118] if reference else [0, 118],
        # Both halves read 0-100: the sign is the mirror, not a negative value.
        tickvals=[-100, -50, 0, 50, 100] if reference else [0, 50, 100],
        ticktext=["100", "50", "0", "50", "100"] if reference else ["0", "50", "100"],
    )
    return figure
