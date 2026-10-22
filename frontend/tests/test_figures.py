import unittest
from unittest.mock import patch

import plotly.graph_objects as go

from constants import SAMPLE_TYPE_COLOR_MAP
from ui.figures import MS2_MATCHED_COLOR, MS2_UNMATCHED_COLOR, build_eic_figure, build_feature_map_figure, build_ms2_mirror_figure, build_is_qc_trace_figure, build_study_structure_figure, empty_feature_map_figure, structure_node_color, structure_hover_text


class EicFigureTests(unittest.TestCase):
    def test_third_provider_reordered_acquisitions_keep_their_trace_values(self):
        payload = {'reader': 'third-fixture-v1',
                   'feature': {'feature_id': 'pkg/a:2', 'source_feature_id': 'native/a:2',
                               'metabolite_name': 'Fixture feature', 'average_mz': 102, 'average_rt': 1},
                   'spot': {'peaks': [
                       {'acquisition_id': 'file-12', 'sample_name': 'Same sample', 'top': None, 'left': None, 'right': None,
                        'points': [{'x': 1, 'intensity': 22}, {'x': 1.1, 'intensity': 23}]},
                       {'acquisition_id': 'file-11', 'sample_name': 'Same sample', 'top': None, 'left': None, 'right': None,
                        'points': [{'x': 1, 'intensity': 21}, {'x': 1.1, 'intensity': 22}]}]}}
        figure = build_eic_figure(payload)
        self.assertEqual([tuple(trace.y) for trace in figure.data], [(22, 23), (21, 22)])
        self.assertEqual([tuple(trace.x) for trace in figure.data], [(1, 1.1), (1, 1.1)])
        self.assertEqual([trace.name for trace in figure.data], ['Same sample', 'Same sample'])
        self.assertEqual([trace.legendgroup for trace in figure.data], ['0', '1'])
        self.assertFalse(figure.layout.shapes)

    def test_unknown_native_boundaries_do_not_invent_apices_or_integrations(self):
        figure = build_eic_figure({'spot': {'peaks': [
            {'sample_name': 'Detected', 'top': None, 'left': None, 'right': None,
             'points': [{'x': 2.1, 'intensity': 10}, {'x': 2.2, 'intensity': 20}]},
            {'sample_name': 'Unavailable', 'top': None, 'left': None, 'right': None, 'points': []}]}})
        self.assertEqual(len(figure.data), 1)
        self.assertEqual(tuple(figure.data[0].x), (2.1, 2.2))
        self.assertFalse(figure.layout.shapes)
        self.assertFalse(any(trace.fill == 'tozeroy' for trace in figure.data))

    def test_apex_shapes_preserve_order_zero_and_axis_domain(self):
        figure = build_eic_figure({"spot": {"peaks": [
            {"sample_name": "QC", "top": 1.25, "points": [{"x": 1, "intensity": 10}]},
            {"sample_name": "Empty", "top": 99, "points": []},
            {"file_id": 7, "top": 0, "points": [{"x": 0, "intensity": 5}]},
            {"sample_name": "No apex", "points": [{"x": 2, "intensity": 20}]},
        ]}})
        self.assertEqual(figure.to_plotly_json()["layout"]["shapes"], [
            {"type": "line", "x0": 1.25, "x1": 1.25, "xref": "x",
             "y0": 0, "y1": 1, "yref": "y domain",
             "line": {"width": 1, "dash": "dot", "color": "rgba(24,151,76,0.25)"}},
            {"type": "line", "x0": 0, "x1": 0, "xref": "x",
             "y0": 0, "y1": 1, "yref": "y domain",
             "line": {"width": 1, "dash": "dot", "color": "rgba(24,151,76,0.25)"}},
        ])
        self.assertEqual([trace.name for trace in figure.data], ["QC", "File 7", "No apex"])
        self.assertEqual([trace.line.color for trace in figure.data], ["#636EFA", "#00CC96", "#AB63FA"])
        self.assertEqual(figure.data[1].hovertemplate,
                         "File 7<br>RT %{x:.3f}<br>Intensity %{y:.0f}<extra></extra>")

    def test_many_apices_do_not_repeat_plotly_vline_expansion(self):
        # Keep real Plotly behavior: the spy measures the expensive operation,
        # while output assertions ensure that skipping the markers cannot pass.
        with patch.object(go.Figure, "add_vline", autospec=True,
                          side_effect=go.Figure.add_vline) as add_vline:
            figure = build_eic_figure({"spot": {"peaks": [
                {"top": top, "points": [{"x": top, "intensity": 10}]}
                for top in (0, 1, 2)
            ]}})
        self.assertEqual([shape.x0 for shape in figure.layout.shapes], [0, 1, 2])
        self.assertEqual(len(figure.data), 3)
        self.assertLessEqual(add_vline.call_count, 1)

    def test_integration_fill_follows_curve_and_clips_to_boundaries(self):
        for left, right, expected_x, expected_y in [
            (0.5, 1.5, (0.5, 1, 1.5), (5, 10, 5)),
            (0.2, 0.4, (0.2, 0.4), (2, 4)),
            (-1, 3, (0, 1, 2), (0, 10, 0)),
            (3, 4, (), ()),
            (1.5, 0.5, (), ()),
            (None, 1, (), ()),
        ]:
            with self.subTest(left=left, right=right):
                figure = build_eic_figure({"spot": {"peaks": [{
                    "sample_name": "QC", "left": left, "right": right,
                    "points": [{"x": 0, "intensity": 0},
                               {"x": 1, "intensity": 10},
                               {"x": 2, "intensity": 0}],
                }]}})
                fills = [trace for trace in figure.data if trace.fill == "tozeroy"]
                self.assertEqual(len(fills), int(bool(expected_x)))
                if fills:
                    fill = fills[0]
                    self.assertEqual(tuple(fill.x), expected_x)
                    self.assertEqual(tuple(fill.y), expected_y)
                    line = next(trace for trace in figure.data if trace.showlegend is not False)
                    self.assertEqual(fill.legendgroup, line.legendgroup)
                    self.assertEqual(fill.fillcolor, "rgba(99,110,250,0.15)")
                    self.assertEqual(line.line.color, "#636EFA")
                self.assertFalse(any(shape.type == "rect" for shape in figure.layout.shapes))


class StudyStructureFigureTests(unittest.TestCase):
    def test_sample_nodes_are_colored_by_sample_type(self) -> None:
        self.assertEqual(structure_node_color({"node_type": "sample", "details": {"sample_type": "sample"}}), SAMPLE_TYPE_COLOR_MAP["sample"])
        self.assertEqual(structure_node_color({"node_type": "sample", "details": {"sample_type": "blank"}}), SAMPLE_TYPE_COLOR_MAP["blank"])
        self.assertEqual(structure_node_color({"node_type": "sample", "details": {"sample_type": "qc"}}), SAMPLE_TYPE_COLOR_MAP["qc"])
        self.assertEqual(structure_node_color({"node_type": "sample", "details": {"sample_type": "other"}}), SAMPLE_TYPE_COLOR_MAP["other"])

    def test_sample_hover_uses_human_readable_sample_type_label(self) -> None:
        hover = structure_hover_text({"node_type": "sample", "details": {"sample_type": "qc"}})

        self.assertIn("Sample type: QC", hover)

    def test_figure_marker_colors_include_sample_type_colors(self) -> None:
        figure = build_study_structure_figure(
            {
                "nodes": [
                    {"id": "study:1", "parent_id": None, "label": "Study", "node_type": "study", "value": 2, "details": {}},
                    {"id": "sample:1", "parent_id": "study:1", "label": "S1", "node_type": "sample", "value": 1, "details": {"sample_type": "blank"}},
                    {"id": "sample:2", "parent_id": "study:1", "label": "QC1", "node_type": "sample", "value": 1, "details": {"sample_type": "qc"}},
                ]
            }
        )

        colors = list(figure.data[0].marker.colors)
        self.assertIn(SAMPLE_TYPE_COLOR_MAP["blank"], colors)
        self.assertIn(SAMPLE_TYPE_COLOR_MAP["qc"], colors)


class FeatureMapFigureTests(unittest.TestCase):
    def test_empty_feature_map_figure_has_no_traces(self) -> None:
        figure = empty_feature_map_figure()

        self.assertEqual(len(figure.data), 0)
        self.assertEqual(figure.layout.height, 360)

    def test_feature_map_uses_scattergl_traces_for_ms2_availability(self) -> None:
        figure = build_feature_map_figure(
            [
                {
                    "feature_index": 1,
                    "alignment_id": 10,
                    "metabolite_name": "Known",
                    "adduct_type": "[M+H]+",
                    "average_rt": 1.25,
                    "average_mz": 100.12345,
                    "annotated": True,
                    "has_ms2": True,
                },
                {
                    "feature_index": 2,
                    "alignment_id": 11,
                    "metabolite_name": "Unknown",
                    "adduct_type": "[M+Na]+",
                    "average_rt": 2.5,
                    "average_mz": 200.12345,
                    "annotated": False,
                    "has_ms2": False,
                },
                {
                    "feature_index": 3,
                    "average_rt": None,
                    "average_mz": 300.0,
                    "has_ms2": True,
                },
            ]
        )

        self.assertEqual([trace.type for trace in figure.data], ["scattergl", "scattergl"])
        self.assertEqual([trace.name for trace in figure.data], ["MS/MS available", "No MS/MS"])
        self.assertEqual(figure.data[0].customdata[0][0], 1)
        self.assertEqual(figure.data[1].customdata[0][0], 2)
        self.assertEqual(figure.layout.clickmode, "event+select")


class IsQcTraceFigureTests(unittest.TestCase):
    def test_is_qc_selected_trace_can_overlay_original_and_corrected_coordinates(self) -> None:
        payload = {
            "target_name": "IS:A",
            "traces": [{
                "sample_name": "S1",
                "sample_type": "qc",
                "observed_rt_min": 1.1,
                "corrected_observed_rt_min": 1.0,
                "cohort_mean_rt_min": 1.0,
                "points": [
                    {"rt_min": 0.9, "corrected_rt_min": 0.82, "intensity": 10.0},
                    {"rt_min": 1.1, "corrected_rt_min": 1.0, "intensity": 20.0},
                ],
            }],
        }

        figure = build_is_qc_trace_figure(payload, rt_view="both")

        self.assertEqual(
            [trace.name for trace in figure.data],
            ["S1 · original", "S1 · corrected"],
        )
        self.assertEqual(list(figure.data[0].x), [0.9, 1.1])
        self.assertEqual(list(figure.data[1].x), [0.82, 1.0])

    def test_is_qc_all_standard_overview_uses_feature_markers_not_trace_lines(self) -> None:
        payload = {
            "target_name": "__all__",
            "traces": [
                {
                    "target_name": "IS:A",
                    "sample_name": "S1",
                    "used_for_rt_model": False,
                    "observed_rt_min": 1.1,
                    "corrected_observed_rt_min": 1.0,
                    "points": [],
                },
                {
                    "target_name": "IS:B",
                    "sample_name": "S1",
                    "used_for_rt_model": True,
                    "observed_rt_min": 5.2,
                    "corrected_observed_rt_min": 5.0,
                    "points": [],
                },
            ],
        }

        figure = build_is_qc_trace_figure(payload, rt_view="both")

        self.assertEqual([trace.mode for trace in figure.data], ["markers", "markers"])
        self.assertEqual(list(figure.data[0].x), [1.1, 5.2])
        self.assertEqual(list(figure.data[1].x), [1.0, 5.0])
        self.assertEqual(list(figure.data[0].marker.symbol), ["circle-open", "circle"])
        self.assertEqual(list(figure.data[1].marker.symbol), ["circle-open", "circle"])


class Ms2MirrorFigureTests(unittest.TestCase):
    PAYLOAD = {"ms2": {"experimental": [{"mz": 50.0, "intensity": 100.0, "matched": True},
                                        {"mz": 70.0, "intensity": 40.0, "matched": False}],
                       "reference": {"name": "Caffeine",
                                     "peaks": [{"mz": 50.0, "intensity": 100.0, "matched": True},
                                               {"mz": 90.0, "intensity": 25.0, "matched": False}]}}}

    def test_reference_is_mirrored_below_the_axis(self):
        figure = build_ms2_mirror_figure(self.PAYLOAD)
        values = [value for trace in figure.data for value in trace.y]
        self.assertEqual(max(values), 100.0)
        self.assertEqual(min(values), -100.0)
        # The sign is the mirror, so both halves still read 0-100 on the axis.
        self.assertEqual(tuple(figure.layout.yaxis.ticktext), ("100", "50", "0", "50", "100"))

    def test_colour_carries_agreement_and_the_legend_names_it_once(self):
        figure = build_ms2_mirror_figure(self.PAYLOAD)
        by_colour = {}
        for trace in figure.data:
            by_colour.setdefault(trace.marker.color, []).extend(trace.x)
        self.assertEqual(sorted(by_colour[MS2_MATCHED_COLOR]), [50.0, 50.0])
        self.assertEqual(sorted(by_colour[MS2_UNMATCHED_COLOR]), [70.0, 90.0])
        legend_names = [trace.name for trace in figure.data if trace.showlegend]
        self.assertEqual(sorted(legend_names), ["Matched", "Unmatched"])

    def test_without_a_reference_nothing_is_claimed_unmatched(self):
        """No comparison was made, so the sticks carry no agreement claim."""
        figure = build_ms2_mirror_figure({"ms2": {"experimental": [{"mz": 50.0, "intensity": 10.0, "matched": False}],
                                                  "reference": None}})
        self.assertEqual(len(figure.data), 1)
        self.assertFalse(any(trace.showlegend for trace in figure.data))
        self.assertNotIn(MS2_UNMATCHED_COLOR, [trace.marker.color for trace in figure.data])
        self.assertEqual(tuple(figure.layout.yaxis.range), (0, 118))
        self.assertTrue(all(value >= 0 for trace in figure.data for value in trace.y))

    def test_missing_spectrum_renders_the_empty_state(self):
        for payload in ({"ms2": {"experimental": []}}, {"ms2": {}}, {}, None):
            with self.subTest(payload=payload):
                figure = build_ms2_mirror_figure(payload)
                self.assertEqual(len(figure.data), 0)
                self.assertTrue(figure.layout.annotations)
                self.assertFalse(figure.layout.xaxis.visible)


if __name__ == "__main__":
    unittest.main()
