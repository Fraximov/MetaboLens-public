import unittest

from services import metro_service


JOB_107_TRACE = {
    "prepare_msdial5_input": "done",
    "RUN_MSDIAL5_PREPARED_TO_STATS_TXT:peak_detection_msdial5": "done",
    "RUN_SIRIUS:prepare_sirius_msp": "done",
    "RUN_MSDIAL5_PREPARED_TO_STATS_TXT:adapt_mdalign_to_stats_txt": "done",
    "RUN_SIRIUS:run_sirius": "failed",
    "STATPIPELINE_V2_FROM_TABLE:R_PARSE_MSDIAL": "failed",
}


class ManifestTests(unittest.TestCase):
    def test_engine_stages_use_declared_processes_and_keep_package_stages_distinct(self):
        stages = [
            {'id': 'engine', 'label': 'Fixture engine', 'patterns': ['fixture_detect']},
            {'id': 'preview', 'label': 'QC preview', 'patterns': ['METABOLENSR_PREVIEW']},
            {'id': 'final', 'label': 'Finalization', 'patterns': ['METABOLENSR_FINALIZE']},
        ]
        snapshot = metro_service.station_snapshot(
            [('QC:METABOLENSR_PREVIEW (bundle-a)', 'CACHED'), ('fixture_detect', 'COMPLETED')],
            {'QC:METABOLENSR_FINALIZE': 'running'}, station_definitions=stages,
        )
        self.assertEqual(snapshot['preview']['state'], 'done')
        self.assertEqual(snapshot['final']['state'], 'running')
        self.assertEqual(snapshot['engine']['done'], 1)
        self.assertNotIn('msdial5', snapshot)

    def test_the_manifest_is_read_from_the_rendered_svg(self):
        self.assertGreater(len(metro_service.manifest().get("nodes") or []), 0)

    def test_nf_metro_matches_on_the_fully_qualified_process_name(self):
        """Following nf-metro's own contract is what keeps their live server a
        drop-in option later."""
        match = metro_service.manifest().get("match") or {}
        self.assertEqual(match.get("target"), "fqProcessName")
        self.assertEqual(match.get("type"), "regex")

    def test_every_station_has_an_id_and_a_label(self):
        for station in metro_service.stations():
            with self.subTest(station=station["id"]):
                self.assertTrue(station["id"])
                self.assertTrue(station["label"])


class StationMappingTests(unittest.TestCase):
    def test_a_real_run_lights_the_expected_stations(self):
        states = metro_service.station_states(JOB_107_TRACE)
        self.assertEqual(states["prepare"], "done")
        self.assertEqual(states["msdial5"], "done")
        self.assertEqual(states["sirius"], "failed")
        self.assertEqual(states["parse"], "failed")

    def test_a_station_takes_the_most_serious_state_it_covers(self):
        # SIRIUS prepare succeeded and the run failed; the station is failed.
        states = metro_service.station_states(
            {"RUN_SIRIUS:prepare_sirius_msp": "done", "RUN_SIRIUS:run_sirius": "failed"}
        )
        self.assertEqual(states["sirius"], "failed")

    def test_running_outranks_done(self):
        states = metro_service.station_states(
            {"peak_detection_msdial5": "done", "adapt_mdalign_to_stats_txt": "running"}
        )
        self.assertEqual(states["msdial5"], "running")

    def test_stations_with_no_matching_process_are_left_alone(self):
        states = metro_service.station_states({"prepare_msdial5_input": "done"})
        self.assertEqual(list(states), ["prepare"])

    def test_no_processes_means_no_states(self):
        for value in [None, {}]:
            with self.subTest(value=value):
                self.assertEqual(metro_service.station_states(value), {})


class RenamedPipelineTests(unittest.TestCase):
    """The R pipeline is being replaced by MetaboLensR, so renames are expected."""

    def test_an_unknown_process_is_reported_rather_than_silently_dropped(self):
        self.assertEqual(
            metro_service.unmatched_processes({"METABOLENSR_PREVIEW": "running"}),
            ["METABOLENSR_PREVIEW"],
        )

    def test_todays_processes_are_all_claimed(self):
        self.assertEqual(metro_service.unmatched_processes(JOB_107_TRACE), [])

    def test_a_manifest_pattern_would_win_over_the_local_override(self):
        """Declaring patterns in the .mmd retires the override without code
        changes, which is the point of reading them from the manifest."""
        import re
        source = re.search(r"patterns = (.+)", open(metro_service.__file__).read())
        self.assertIn("node.get(\"patterns\")", source.group(1))


class RenderingTests(unittest.TestCase):
    def test_a_state_becomes_a_halo_not_a_solid_block(self):
        """Filling the station erased the drawn shape. paint-order puts the thick
        stroke behind the fill, so it reads as a ring around the station."""
        css = metro_service.station_css("msdial5", "done")
        self.assertIn("stroke:#18974C", css)
        self.assertIn("paint-order:stroke", css)
        self.assertNotIn("fill:", css)

    def test_the_halo_carries_a_soft_glow(self):
        self.assertIn("drop-shadow(0 0 2.5px rgba(24,151,76,0.45))",
                      metro_service.station_css("msdial5", "done"))

    def test_a_running_station_pulses_and_the_others_do_not(self):
        self.assertIn("animation:jwMetroPulse", metro_service.station_css("x", "running"))
        for state in ("done", "failed"):
            with self.subTest(state=state):
                self.assertNotIn("animation:", metro_service.station_css("x", state))

    def test_running_wears_the_widest_ring(self):
        widths = {s: metro_service.STATE_HALO[s]["ring"] for s in metro_service.STATE_HALO}
        self.assertEqual(max(widths, key=widths.get), "running")

    def test_the_pulse_is_dropped_where_motion_is_unwelcome(self):
        svg = metro_service.styled_svg({"normalize": "running"})
        self.assertIn("prefers-reduced-motion:reduce", svg)
        self.assertIn("animation:none!important", svg)

    def test_no_keyframes_are_added_when_nothing_is_running(self):
        self.assertNotIn("jwMetroPulse", metro_service.styled_svg({"msdial5": "done"}))

    def test_an_unstyled_state_yields_no_rules(self):
        self.assertEqual(metro_service.station_css("x", "pending"), "")

    def test_the_drawing_itself_is_never_rewritten(self):
        plain = metro_service.styled_svg({})
        styled = metro_service.styled_svg({"msdial5": "done"})
        self.assertEqual(len(styled) - len(plain), len(styled) - len(plain))
        self.assertTrue(styled.startswith(plain[:2000]))

    def test_an_unknown_state_colours_nothing(self):
        self.assertEqual(metro_service.styled_svg({"msdial5": "pending"}),
                         metro_service.styled_svg({}))

    def test_the_data_uri_is_an_svg_image(self):
        uri = metro_service.data_uri({"msdial5": "done"})
        self.assertTrue(uri.startswith("data:image/svg+xml;utf8,"))


if __name__ == "__main__":
    unittest.main()


class HistoricalJobFallbackTests(unittest.TestCase):
    """Jobs that finished before the worker published process_states still carry
    recent_tasks, and the map must work for them."""

    RECENT = {"recent_tasks": [
        {"name": "STATPIPELINE_V2_FROM_TABLE:R_PARSE_MSDIAL (AlignResult-x_adapted)",
         "status": "completed"},
        {"name": "STATPIPELINE_V2_FROM_TABLE:R_NORMALIZE (preprocess)", "status": "completed"},
        {"name": "RUN_SIRIUS:run_sirius (sirius_input)", "status": "failed"},
    ]}

    def test_recent_tasks_are_rolled_up_when_process_states_is_absent(self):
        states = metro_service.process_states_from_payload(self.RECENT)
        self.assertEqual(states["STATPIPELINE_V2_FROM_TABLE:R_PARSE_MSDIAL"], "done")
        self.assertEqual(states["RUN_SIRIUS:run_sirius"], "failed")

    def test_the_fallback_lights_stations(self):
        states = metro_service.station_states(
            metro_service.process_states_from_payload(self.RECENT))
        self.assertEqual(states["parse"], "done")
        self.assertEqual(states["sirius"], "failed")

    def test_process_states_is_preferred_when_present(self):
        payload = dict(self.RECENT, process_states={"prepare_msdial5_input": "running"})
        self.assertEqual(metro_service.process_states_from_payload(payload),
                         {"prepare_msdial5_input": "running"})

    def test_an_empty_payload_yields_nothing(self):
        for payload in [None, {}, {"recent_tasks": []}, {"recent_tasks": [{"name": "-", "status": ""}]}]:
            with self.subTest(payload=payload):
                self.assertEqual(metro_service.process_states_from_payload(payload), {})


class FullTraceTests(unittest.TestCase):
    """The status payload keeps only the last eight tasks. On job 130 that
    dropped the three MS-DIAL stages, which ran first."""

    TRUNCATED = {"recent_tasks": [
        {"name": "STATPIPELINE_V2_FROM_TABLE:R_PARSE_MSDIAL (x)", "status": "completed"},
        {"name": "CANONICALIZE_WITH_STATPIPELINE (job130)", "status": "completed"},
    ]}
    TRACE = (
        "task_id\thash\tnative_id\tname\tstatus\texit\n"
        "1\ta/1\t10\tprepare_msdial5_input (1)\tCOMPLETED\t0\n"
        "2\ta/2\t11\tRUN_MSDIAL5_PREPARED_TO_STATS_TXT:peak_detection_msdial5 (1)\tCOMPLETED\t0\n"
        "3\ta/3\t12\tRUN_MSDIAL5_PREPARED_TO_STATS_TXT:adapt_mdalign_to_stats_txt (1)\tCOMPLETED\t0\n"
        "4\ta/4\t13\tSTATPIPELINE_V2_FROM_TABLE:R_PARSE_MSDIAL (x)\tCOMPLETED\t0\n"
    )

    def test_the_truncated_payload_loses_msdial(self):
        states = metro_service.station_states(
            metro_service.process_states_from_payload(self.TRUNCATED))
        self.assertNotIn("msdial5", states)
        self.assertNotIn("prepare", states)

    def test_the_full_trace_recovers_it(self):
        states = metro_service.station_states(
            metro_service.process_states_from_trace(self.TRACE))
        self.assertEqual(states["prepare"], "done")
        self.assertEqual(states["msdial5"], "done")
        self.assertEqual(states["parse"], "done")

    def test_the_header_decides_the_columns(self):
        reordered = ("status\tname\n" "COMPLETED\tprepare_msdial5_input (1)\n")
        self.assertEqual(metro_service.process_states_from_trace(reordered),
                         {"prepare_msdial5_input": "done"})

    def test_a_trace_without_the_needed_columns_is_ignored(self):
        self.assertEqual(metro_service.process_states_from_trace("a\tb\n1\t2\n"), {})

    def test_an_empty_or_header_only_trace_is_ignored(self):
        for text in ["", None, "task_id\tname\tstatus\n"]:
            with self.subTest(text=text):
                self.assertEqual(metro_service.process_states_from_trace(text), {})


class SnapshotContractTests(unittest.TestCase):
    """nf-metro's manifest standard fixes the state vocabulary and the snapshot
    shape but explicitly not the visual style, so the halo is ours to choose and
    these two are not."""

    TASKS = [
        ("prepare_msdial5_input (1)", "COMPLETED"),
        ("RUN_MSDIAL5_PREPARED_TO_STATS_TXT:peak_detection_msdial5 (1)", "COMPLETED"),
        ("RUN_MSDIAL5_PREPARED_TO_STATS_TXT:adapt_mdalign_to_stats_txt (1)", "RUNNING"),
        ("RUN_SIRIUS:run_sirius (x)", "FAILED"),
    ]

    def test_the_snapshot_uses_their_documented_shape(self):
        entry = metro_service.station_snapshot(self.TASKS)["prepare"]
        self.assertEqual(sorted(entry), ["done", "state", "total"])

    def test_counts_are_per_station(self):
        snapshot = metro_service.station_snapshot(self.TASKS)
        self.assertEqual(snapshot["msdial5"], {"state": "running", "done": 1, "total": 2})
        self.assertEqual(snapshot["sirius"], {"state": "failed", "done": 0, "total": 1})

    def test_every_state_is_in_their_vocabulary(self):
        vocabulary = {"pending", "queued", "running", "done", "failed"}
        states = set(metro_service.snapshot_states(
            metro_service.station_snapshot(self.TASKS)).values())
        self.assertTrue(states <= vocabulary, states - vocabulary)

    def test_queued_is_distinguished_from_running(self):
        snapshot = metro_service.station_snapshot([("prepare_msdial5_input (1)", "SUBMITTED")])
        self.assertEqual(snapshot["prepare"]["state"], "queued")
        snapshot = metro_service.station_snapshot([("prepare_msdial5_input (1)", "RUNNING")])
        self.assertEqual(snapshot["prepare"]["state"], "running")

    def test_queued_has_a_colour_and_a_halo(self):
        self.assertIn("queued", metro_service.STATE_COLOURS)
        self.assertIn("queued", metro_service.STATE_HALO)
        self.assertIn("stroke:", metro_service.station_css("prepare", "queued"))

    def test_running_still_outranks_queued_on_a_shared_station(self):
        snapshot = metro_service.station_snapshot([
            ("peak_detection_msdial5 (1)", "SUBMITTED"),
            ("adapt_mdalign_to_stats_txt (1)", "RUNNING"),
        ])
        self.assertEqual(snapshot["msdial5"]["state"], "running")

    def test_stations_with_no_pattern_stay_out_of_the_snapshot(self):
        self.assertNotIn("multiqc", metro_service.station_snapshot(self.TASKS))


class HaloTargetsTheStationOnlyTests(unittest.TestCase):
    """The station label is a <text> carrying the same data-station-id, so an
    unqualified selector put a coloured halo around the name as well."""

    def test_the_selector_is_scoped_to_the_station_rect(self):
        css = metro_service.station_css("msdial5", "done")
        self.assertIn('rect[data-station-id="msdial5"]', css)

    def test_the_label_text_shares_the_attribute(self):
        import re
        svg = metro_service.styled_svg({})
        tags = re.findall(r'<(\w+)[^>]*data-station-id="prepare"', svg)
        self.assertIn("text", tags)
        self.assertIn("rect", tags)

    def test_no_rule_would_match_the_label(self):
        import re
        for state in ("done", "running", "queued", "failed"):
            with self.subTest(state=state):
                css = metro_service.station_css("prepare", state)
                self.assertEqual(re.findall(r'(?<!rect)\[data-station-id', css), [])


class LiveStateReachesTheMapTests(unittest.TestCase):
    """The map was built from trace rows alone, so it could never show a running
    station: the trace gains a row only once a task ends. The worker's
    process_states, which also reads the log's `Submitted process >` lines, is
    the only source that knows what is in flight."""

    ROWS = [
        ("prepare_msdial5_input (1)", "COMPLETED"),
        ("RUN_MSDIAL5_PREPARED_TO_STATS_TXT:peak_detection_msdial5 (1)", "COMPLETED"),
    ]
    LIVE = {
        "prepare_msdial5_input": "done",
        "RUN_MSDIAL5_PREPARED_TO_STATS_TXT:peak_detection_msdial5": "done",
        "STATPIPELINE_V2_FROM_TABLE:R_PARSE_MSDIAL": "running",
    }

    def test_trace_rows_alone_cannot_show_the_running_station(self):
        self.assertNotIn("parse", metro_service.station_snapshot(self.ROWS))

    def test_the_worker_states_bring_it_back(self):
        snapshot = metro_service.station_snapshot(self.ROWS, self.LIVE)
        self.assertEqual(snapshot["parse"]["state"], "running")

    def test_counts_still_come_from_the_trace_rows(self):
        snapshot = metro_service.station_snapshot(self.ROWS, self.LIVE)
        self.assertEqual(snapshot["msdial5"]["done"], 1)

    def test_the_more_serious_source_wins(self):
        # The trace says the SIRIUS task completed; the worker saw a later one fail.
        snapshot = metro_service.station_snapshot(
            [("RUN_SIRIUS:prepare_sirius_msp (x)", "COMPLETED")],
            {"RUN_SIRIUS:run_sirius": "failed"},
        )
        self.assertEqual(snapshot["sirius"]["state"], "failed")

    def test_a_finished_run_is_unaffected(self):
        rows = self.ROWS + [("STATPIPELINE_V2_FROM_TABLE:R_PARSE_MSDIAL (x)", "COMPLETED")]
        live = dict(self.LIVE, **{"STATPIPELINE_V2_FROM_TABLE:R_PARSE_MSDIAL": "done"})
        snapshot = metro_service.station_snapshot(rows, live)
        self.assertEqual({e["state"] for e in snapshot.values()}, {"done"})

    def test_worker_states_alone_are_enough(self):
        """A job whose trace has not been registered yet still colours."""
        snapshot = metro_service.station_snapshot([], self.LIVE)
        self.assertEqual(snapshot["parse"]["state"], "running")
        self.assertEqual(snapshot["prepare"]["state"], "done")


class MapIsAlwaysDrawnTests(unittest.TestCase):
    def test_the_background_is_set_to_the_card_colour(self):
        """The map paints its own grey ground. Both that rect and the halos
        behind the labels read --nfm-map-bg, so one variable covers both."""
        self.assertIn("--nfm-map-bg:#ffffff", metro_service.styled_svg({}))

    def test_the_background_rule_survives_alongside_station_rules(self):
        svg = metro_service.styled_svg({"msdial5": "done"})
        self.assertIn("--nfm-map-bg:#ffffff", svg)
        self.assertIn('rect[data-station-id="msdial5"]', svg)

    def test_a_map_with_no_states_is_still_a_map(self):
        """Materialisation can run for minutes before the first task starts, and
        an empty panel read as a failure."""
        self.assertTrue(metro_service.data_uri({}).startswith("data:image/svg+xml"))


# The descriptor the MetaboLensR engine stores on each job, and the processes
# job 146 ran (a full run: preview, then finalization without a review pause).
ENGINE_JSON = (metro_service.METRO_SVG.parents[2] / "workflows" / "nextflow4ms-dial-msdial5-clean"
               / "engines" / "msdial5" / "engine.json")
JOB_146_STATES = {
    "prepare_msdial5_input": "done",
    "peak_detection_msdial5": "done",
    "MSDIAL_TO_METABOLENSR": "done",
    "METABOLENSR_QC:METABOLENSR_PREVIEW": "done",
    "METABOLENSR_QC:METABOLENSR_FINALIZE": "done",
}


class MetaboLensRMapTests(unittest.TestCase):
    def setUp(self):
        import json
        try:
            self.stages = json.loads(ENGINE_JSON.read_text())["stages"]
        except OSError:
            self.skipTest("workflow tree is not mounted")

    def test_every_descriptor_stage_is_drawn(self):
        """A stage id renamed in engine.json but not on the map would silently
        drop the run back to the plain stage list."""
        drawn = metro_service.engine_map(self.stages)
        self.assertIsNotNone(drawn)
        self.assertEqual(drawn[0], metro_service.METABOLENSR_SVG)

    def test_the_descriptor_supplies_the_patterns(self):
        _, stations = metro_service.engine_map(self.stages)
        by_id = {s["id"]: s for s in stations}
        self.assertEqual(by_id["qc_preview"]["patterns"], ["METABOLENSR_PREVIEW"])
        self.assertEqual(by_id["review_features"]["patterns"], [])

    def test_a_real_run_lights_its_stations(self):
        _, stations = metro_service.engine_map(self.stages)
        snapshot = metro_service.station_snapshot([], JOB_146_STATES, station_definitions=stations)
        self.assertEqual(
            {sid for sid, e in snapshot.items() if e["state"] == "done"},
            {"prepare", "engine", "import", "qc_preview", "finalize"},
        )
        self.assertEqual(metro_service.unmatched_processes(JOB_146_STATES, station_definitions=stations), [])

    def test_the_legacy_r_stations_are_gone(self):
        ids = {n["id"] for n in metro_service.manifest(metro_service.METABOLENSR_SVG)["nodes"]}
        self.assertFalse(ids & {"parse", "normalize", "statistics", "multiqc"})

    def test_every_station_can_wear_a_halo(self):
        svg = metro_service.styled_svg({"finalize": "running"}, metro_service.METABOLENSR_SVG)
        for stage in self.stages:
            self.assertRegex(svg, rf'<rect[^>]*data-station-id="{stage["id"]}"')
        self.assertIn('rect[data-station-id="finalize"]', svg)

    def test_every_station_is_the_same_pill(self):
        """A re-render that skips scripts/uniform_metro_stations.py brings back
        circles and mixed heights."""
        import re
        svg = metro_service._svg_text(metro_service.METABOLENSR_SVG)
        heights = {re.search(r'height="([\d.]+)"', tag).group(1)
                   for tag in re.findall(r'<rect [^>]*rx="[^"]*"[^>]*class="nf-metro-station"[^>]*/>', svg)}
        self.assertEqual(heights, {"22.0"})

    def test_an_engine_with_no_drawn_map_keeps_the_stage_list(self):
        self.assertIsNone(metro_service.engine_map([{"id": "fixture_detect", "patterns": ["x"]}]))


# Job 151: a full MassCube run.
MASSCUBE_JSON = ENGINE_JSON.parents[1] / "masscube" / "engine.json"
JOB_151_STATES = {
    "prepare_acquisition_input": "done",
    "MASSCUBE_PROCESS": "done",
    "METABOLENSR_QC:METABOLENSR_PREVIEW": "done",
    "METABOLENSR_QC:METABOLENSR_FINALIZE": "failed",
}


class MassCubeMapTests(unittest.TestCase):
    def setUp(self):
        import json
        try:
            self.stages = json.loads(MASSCUBE_JSON.read_text())["stages"]
        except OSError:
            self.skipTest("workflow tree is not mounted")

    def test_a_masscube_run_gets_the_masscube_map(self):
        """Its stage ids are a subset of the MS-DIAL map's; the detection
        process, not the ids, decides."""
        drawn = metro_service.engine_map(self.stages)
        self.assertIsNotNone(drawn)
        self.assertEqual(drawn[0], metro_service.MASSCUBE_SVG)

    def test_a_real_run_lights_its_stations(self):
        _, stations = metro_service.engine_map(self.stages)
        states = metro_service.snapshot_states(
            metro_service.station_snapshot([], JOB_151_STATES, station_definitions=stations))
        self.assertEqual(states, {"prepare": "done", "engine": "done",
                                  "qc_preview": "done", "finalize": "failed"})
        self.assertEqual(metro_service.unmatched_processes(JOB_151_STATES, station_definitions=stations), [])

    def test_it_is_drawn_at_the_ms_dial_maps_scale(self):
        """Pages stretch a map to full width; unequal canvases mean unequal
        text and station sizes."""
        import re
        viewbox = lambda path: re.search(r'viewBox="([^"]*)"', metro_service._svg_text(path)).group(1)
        self.assertEqual(viewbox(metro_service.MASSCUBE_SVG), viewbox(metro_service.METABOLENSR_SVG))

    def test_no_ms_dial_only_station_is_drawn(self):
        ids = {n["id"] for n in metro_service.manifest(metro_service.MASSCUBE_SVG)["nodes"]}
        self.assertFalse(ids & {"is_qc", "import", "annotation", "native_outputs"})

    def test_every_station_is_the_same_pill(self):
        import re
        svg = metro_service._svg_text(metro_service.MASSCUBE_SVG)
        heights = {re.search(r'height="([\d.]+)"', tag).group(1)
                   for tag in re.findall(r'<rect [^>]*rx="[^"]*"[^>]*class="nf-metro-station"[^>]*/>', svg)}
        self.assertEqual(heights, {"22.0"})
