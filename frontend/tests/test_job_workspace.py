import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dash import Input, Output, State, html

from callbacks.job_workspace import (
    _ms2_evidence,
    SECTIONS,
    active_job_count,
    compact_launch_time,
    job_href,
    status_colour,
    job_id_from_pathname,
    is_qc_evidence,
    is_qc_standards,
    is_qc_state_line,
    is_qc_table_data,
    selected_target_ids,
    short_run_name,
    job_from_store,
    job_list_items,
    outputs_view,
    overview_view,
    parse_job_route,
    register_callbacks,
    section_nav,
)
from callbacks.navigation import page_key
from pages.job_workspace import job_workspace_layout
from services.api_client import APIError
from services.page_cache import build_page_cache, page_cache_matches



MSDIAL_ENGINE_MANIFEST = {"engine": {"descriptor": {"review_transitions": {"is_qc_review": "qc_preview", "qc_preview": "full"}}}}

class _CallbackCapture:
    def __init__(self) -> None:
        self.callbacks = {}
        self.dependencies = {}
        self.clientside = {}

    def clientside_callback(self, function, *dependencies, **_kwargs):
        self.clientside[function.function_name] = dependencies

    def callback(self, *dependencies, **_kwargs):
        def decorator(callback):
            self.callbacks[callback.__name__] = callback
            self.dependencies[callback.__name__] = dependencies
            return callback

        return decorator


class JobRouteParsingTests(unittest.TestCase):
    def test_native_scores_keep_their_provider_and_unknown_match_status(self):
        rendered = str(_ms2_evidence({'provider': 'Third engine', 'available': True,
            'scores': {'similarity': 0.87, 'search_mode': 'identity', 'msms_matched': None}}))
        self.assertIn('Third engine', rendered)
        self.assertIn('similarity 0.87', rendered)
        self.assertIn('search mode identity', rendered)
        self.assertIn('MS2 unknown', rendered)
        self.assertNotIn('not matched', rendered)
        self.assertNotIn('MS-DIAL', rendered)
        self.assertNotIn('total score', rendered)

    def test_native_known_match_flags_and_legacy_scores_are_retained(self):
        rendered = str(_ms2_evidence({'provider': 'MS-DIAL', 'scores': {
            'rt_matched': True, 'mz_matched': False, 'msms_matched': None,
            'total_score': 850, 'weighted_dot_product': 0, 'matched_peaks_percentage': 80}}))
        for text in ('MS-DIAL', 'RT matched', 'm/z not matched', 'MS2 unknown',
                     'total score 850.00', 'weighted dot 0.00', 'matched peaks 80.00%'):
            self.assertIn(text, rendered)

    def test_job_and_section_are_read_from_the_route(self) -> None:
        self.assertEqual(parse_job_route("/jobs/128"), (128, "overview"))
        self.assertEqual(parse_job_route("/jobs/128/eic"), (128, "eic"))
        self.assertEqual(parse_job_route("/jobs/128/isqc"), (128, "isqc"))
        self.assertEqual(parse_job_route("/jobs/128/outputs"), (128, "outputs"))

    def test_bare_list_route_selects_no_job(self) -> None:
        self.assertEqual(parse_job_route("/jobs"), (None, "overview"))

    def test_unknown_section_falls_back_to_overview(self) -> None:
        self.assertEqual(parse_job_route("/jobs/128/nonsense"), (128, "overview"))

    def test_unparsable_and_foreign_routes_select_no_job(self) -> None:
        for pathname in ["/jobs/abc", "/jobs/1/2/3", "/workflows", "", None]:
            with self.subTest(pathname=pathname):
                self.assertIsNone(job_id_from_pathname(pathname))

    def test_hrefs_round_trip_through_the_parser(self) -> None:
        for section in SECTIONS:
            with self.subTest(section=section):
                self.assertEqual(parse_job_route(job_href(7, section)), (7, section))

    def test_every_job_route_shares_one_page_key(self) -> None:
        # This is what keeps the workspace mounted while the job or section changes.
        for path in ["jobs", "jobs/128", "jobs/128/eic"]:
            with self.subTest(path=path):
                self.assertEqual(page_key(path), "jobs")
        self.assertNotEqual(page_key("jobs"), page_key("workflows"))


class JobListTests(unittest.TestCase):
    def test_each_job_links_to_its_own_route(self) -> None:
        items = job_list_items([{"id": 128, "status": "running"}, {"id": 127, "status": "succeeded"}])
        self.assertEqual([item.href for item in items], ["/jobs/128", "/jobs/127"])

    def test_switching_job_keeps_the_reader_in_the_same_section(self) -> None:
        items = job_list_items([{"id": 5, "status": "succeeded"}], section="eic")
        self.assertEqual(items[0].href, "/jobs/5/eic")

    def test_selection_highlight_follows_job_identity_across_sections(self) -> None:
        for section in ('overview', 'outputs', 'review', 'eic'):
            items = job_list_items([{"id": 5}, {"id": 50}], section=section, selected_job_id=5)
            self.assertEqual([item.active for item in items], [True, False])

    def test_selected_job_row_is_actually_styled(self) -> None:
        """The class is set in Python and styled in CSS; keep the two in step.

        `active=True` only adds Bootstrap's `.active`. Without a rule of our
        own the selected row is indistinguishable, so renaming the class here
        without moving the stylesheet would silently drop the highlight.
        """

        row = job_list_items([{"id": 5, "status": "queued"}])[0]
        self.assertIn("jw-job-link", row.className)
        stylesheet = (Path(__file__).resolve().parents[1] / "assets" / "embl_theme.css").read_text()
        self.assertIn(".jw-job-link.active", stylesheet)

    def test_live_tracking_counts_only_unsettled_jobs(self) -> None:
        jobs = [{"id": 1, "status": "running"}, {"id": 2, "status": "queued"},
                {"id": 3, "status": "succeeded"}, {"id": 4, "status": "failed"}]
        self.assertEqual(active_job_count(jobs), 2)


class SectionViewTests(unittest.TestCase):
    def test_is_qc_navigation_follows_the_frozen_descriptor_capability(self):
        for descriptor, expected in [(None, True), ({}, False),
                                     ({'capabilities': ['native_eic', 'qc_preview']}, False),
                                     ({'capabilities': ['is_qc_review']}, True)]:
            with self.subTest(descriptor=descriptor):
                job = {'id': 12, 'input_manifest_json': {'engine': {'descriptor': descriptor}}}
                links = section_nav(12, 'eic', job)[0].children
                self.assertEqual(any(link.id == 'jw-isqc-nav' for link in links), expected)
                self.assertIn('/jobs/12/eic', [link.href for link in links])

    def test_section_nav_offers_every_section_for_the_open_job(self) -> None:
        nav = section_nav(12, "eic")
        rendered = str(nav)
        for section in SECTIONS:
            with self.subTest(section=section):
                if section != "isqc":
                    self.assertIn(job_href(12, section), rendered)

    def test_is_qc_tab_starts_disabled_until_job_data_arrives(self) -> None:
        is_qc_link = section_nav(12, "overview")[0].children[1]
        self.assertTrue(is_qc_link.disabled)
        self.assertIsNone(is_qc_link.href)

    def test_is_qc_tab_stays_disabled_when_the_job_skipped_the_step(self) -> None:
        job = {"id": 12, "parameter_values_json": {"workflow_stage": "full"}}
        stale_outputs = {"job_id": 99, "outputs": [{"artifact_role": "is_qc_summary"}]}

        link = section_nav(12, "overview", job, stale_outputs)[0].children[1]
        self.assertTrue(link.disabled)
        self.assertIsNone(link.href)

    def test_is_qc_tab_opens_for_selected_or_completed_is_qc(self) -> None:
        cases = [
            ({"id": 12, "parameter_values_json": {"workflow_stage": "is_qc_review"}}, None),
            ({"id": 12, "parameter_values_json": {"workflow_stage": "full", "apply_rt_correction": True}}, None),
            ({"id": 12, "parameter_values_json": {"workflow_stage": "full"},
              "status_payload_json": {"is_qc_summary": {"overall_status": "pass"}}}, None),
            ({"id": 12, "parameter_values_json": {"workflow_stage": "full"}},
             {"job_id": 12, "outputs": [{"artifact_role": "is_qc_summary"}]}),
        ]
        for job, outputs in cases:
            with self.subTest(job=job, outputs=outputs):
                link = section_nav(12, "overview", job, outputs)[0].children[1]
                self.assertFalse(link.disabled)
                self.assertEqual(link.href, "/jobs/12/isqc")

    def test_no_section_nav_without_an_open_job(self) -> None:
        self.assertEqual(section_nav(None, "overview"), [])

    def test_only_settled_jobs_offer_deletion(self) -> None:
        for status, disabled in [('running', True), ('succeeded', False)]:
            header = overview_view({'id': 1, 'status': status})[0]
            delete_button = header.children[1].children
            self.assertEqual(delete_button.disabled, disabled)

    def test_outputs_view_lists_registered_artifacts(self) -> None:
        rendered = str(outputs_view([
            {"artifact_role": "msdial_alignment", "original_filename": "a.msdial", "file_size_bytes": 2048}
        ]))
        self.assertIn("msdial_alignment", rendered)
        self.assertIn("1 registered output(s)", rendered)

    def test_outputs_put_report_and_normalized_tables_before_other_files(self) -> None:
        view = outputs_view([
            {"artifact_role": "msdial_alignment", "original_filename": "alignment.msdial", "file_id": 1},
            {"artifact_role": "metabolensr_normalized_tic", "original_filename": "normalized_tic.tsv", "file_id": 2},
            {"artifact_role": "metabolensr_manifest", "original_filename": "metabolensr_manifest.json", "file_id": 3},
            {"artifact_role": "metabolensr_report", "original_filename": "qc_report.html", "file_id": 4,
             "checksum_sha256": "abcdef123456" + "0" * 52},
            {"artifact_role": "metabolensr_normalized_raw", "original_filename": "normalized_raw.tsv", "file_id": 5},
        ])

        featured_table = view[2]
        filenames = [row.children[1].children for row in featured_table.children[1].children]
        self.assertEqual(filenames, ["qc_report.html", "normalized_raw.tsv", "normalized_tic.tsv"])
        self.assertEqual(featured_table.children[1].children[0].children[3].children, "abcdef123456")
        self.assertTrue(all("jw-output-table" in table.className for table in
                            (featured_table, view[4], view[5].children[1])))
        self.assertIn("metabolensr_manifest.json", str(view[4]))
        self.assertIsInstance(view[5], html.Details)
        self.assertIn("1 other registered file", str(view[5].children[0]))
        self.assertIn("alignment.msdial", str(view[5]))

    def test_outputs_view_handles_an_empty_job(self) -> None:
        self.assertIn("No registered outputs", str(outputs_view([])))


class WorkspaceWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = _CallbackCapture()
        register_callbacks(self.app)

    def _ids(self, name, kind):
        return [self._cid(d) for d in self.app.dependencies[name] if isinstance(d, kind)]

    @staticmethod
    def _cid(dependency):
        """A hashable id. Pattern-matching ids are dicts; use their type."""
        cid = dependency.component_id
        return cid.get("type", str(cid)) if isinstance(cid, dict) else cid

    def _key(self, dependency):
        return (self._cid(dependency), dependency.component_property)

    def test_the_job_list_is_the_only_place_that_fetches_jobs(self) -> None:
        """Everything else reads jw-jobs-store, so a job switch costs one request."""
        import inspect
        import callbacks.job_workspace as mod
        source = inspect.getsource(mod.register_callbacks)
        blocks = source.split("@app.callback")
        fetchers = [b.split("def ")[1].split("(")[0] for b in blocks
                    if "list_workflow_jobs(" in b and "def " in b]
        self.assertEqual(fetchers, ["load_job_list"])

    def test_readers_of_a_single_job_go_through_the_store(self) -> None:
        for name in ["render_overview_pane", "gate_continue"]:
            with self.subTest(callback=name):
                self.assertIn("jw-jobs-store", self._ids(name, Input))

    def test_the_list_refreshes_on_a_poll_tick(self) -> None:
        self.assertIn("jw-poll", self._ids("load_job_list", Input))

    def test_no_callback_gates_on_panel_visibility(self) -> None:
        gates = {"wf-trace-review-details", "wf-trace-review-summary", "wf-outputs-details",
                 "wf-trace-review-tabs"}
        for name, deps in self.app.dependencies.items():
            referenced = {self._cid(d) for d in deps if isinstance(d, (Input, Output, State))}
            with self.subTest(callback=name):
                self.assertEqual(referenced & gates, set())

    def test_no_component_belongs_to_the_workflows_page(self) -> None:
        """Borrowing a wf- component means borrowing a callback that is not here.

        The IS-QC table originally reused the Workflows page's row renderer, whose
        View traces button and RT-model checkbox were inert on this route.
        """
        for name, deps in self.app.dependencies.items():
            foreign = sorted(c for c in (self._cid(d) for d in deps) if c.startswith("wf-"))
            with self.subTest(callback=name):
                self.assertEqual(foreign, [])

    def test_feature_selection_has_exactly_one_resolver(self) -> None:
        writers = [
            name for name, deps in self.app.dependencies.items()
            if any(isinstance(d, Output) and d.component_id == "jw-eic-feature-store" for d in deps)
        ]
        self.assertEqual(writers, ["resolve_feature_selection"])

    def test_the_plot_reads_the_resolved_selection_not_the_raw_controls(self) -> None:
        inputs = self._ids("render_eic_trace", Input)
        self.assertIn("jw-eic-feature-store", inputs)
        for raw in ["jw-eic-slider", "jw-eic-map", "jw-eic-feature"]:
            with self.subTest(control=raw):
                self.assertNotIn(raw, inputs)

    def test_only_control_mirrors_read_properties_they_also_write(self) -> None:
        """Three controls showing one value must mirror each other, and in Dash
        that means writing properties that are also Inputs. That cycle is
        unavoidable. Search filtering runs only in the browser."""
        looping = set()
        for name, deps in self.app.dependencies.items():
            written = {self._key(d) for d in deps if isinstance(d, Output)}
            read = {self._key(d) for d in deps if isinstance(d, Input)}
            if written & read:
                looping.add(name)
        self.assertEqual(looping, {"resolve_feature_selection"})

    def test_shared_writes_are_only_the_declared_ones(self) -> None:
        """Contention is per (id, property); two callbacks writing different
        properties of one component do not contend.

        The two continuation actions are triggered by separate operator clicks.
        Both refresh the same job store; neither is a polling/background writer.
        """
        permitted = {('jw-continue-result', 'data'): ['continue_automatic_preview', 'continue_open_job']}
        writers = {}
        for name, deps in self.app.dependencies.items():
            for d in deps:
                if isinstance(d, Output):
                    writers.setdefault(self._key(d), set()).add(name)
        contested = {k: sorted(v) for k, v in writers.items() if len(v) > 1}
        self.assertEqual(contested, permitted)

    def test_deleting_needs_no_pattern_matching_callback(self) -> None:
        ids = [d.component_id for d in self.app.dependencies["delete_open_job"]]
        self.assertTrue(all(isinstance(i, str) for i in ids), ids)


class DetailBehaviourTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = _CallbackCapture()
        register_callbacks(self.app)
        self.jobs = [{"id": 128, "status": "running", "execution_profile": "local_docker",
                      "run_name": "run_128"}]

    def test_overview_renders_the_job_named_in_the_route(self) -> None:
        pane = self.app.callbacks["render_overview_pane"](
            "/jobs/128", {"jobs": self.jobs, "open_job": self.jobs[0], "section": "overview"}, None, "tok", 7)
        self.assertIn("run_128", str(pane))

    def test_overview_waits_rather_than_erroring_before_the_store_arrives(self) -> None:
        pane = self.app.callbacks["render_overview_pane"]("/jobs/128", None, None, "tok", 7)
        self.assertIn("Loading", str(pane))

    def test_eic_route_renders_the_viewer(self) -> None:
        body = self.app.callbacks["render_job_detail"]("/jobs/128/eic", "tok", 7, None)
        self.assertIn("jw-eic-archive", str(body))

    def test_detail_is_idle_on_the_bare_list_route(self) -> None:
        body = self.app.callbacks["render_job_detail"]("/jobs", "tok", 7, None)
        self.assertIn("Select a job", str(body))

    def test_navigation_updates_when_the_matching_job_descriptor_arrives(self):
        render = self.app.callbacks['render_section_nav']
        self.assertEqual(render('/jobs/128/eic', 'tok', 7, None, None), [])
        unrelated = {'open_job': {'id': 129, 'input_manifest_json': {'engine': {
            'descriptor': {'capabilities': ['is_qc_review']}}}}}
        self.assertEqual(render('/jobs/128/eic', 'tok', 7, unrelated, None), [])
        job = {'id': 128, 'input_manifest_json': {'engine': {'descriptor': {'capabilities': ['native_eic']}}}}
        links = [link.href for link in render('/jobs/128/eic', 'tok', 7, {'open_job': job}, None)[0].children]
        self.assertNotIn('/jobs/128/isqc', links)
        self.assertIn('/jobs/128/eic', links)

    def test_navigation_enables_is_qc_for_current_job_output(self):
        render = self.app.callbacks['render_section_nav']
        job = {'id': 128, 'input_manifest_json': {'engine': {'descriptor': {'capabilities': ['is_qc_review']}}}}
        store = {'open_job': job}
        unavailable = render('/jobs/128', 'tok', 7, store, None)[0].children[1]
        available = render('/jobs/128', 'tok', 7, store,
                           {'job_id': 128, 'outputs': [{'artifact_role': 'is_qc_summary'}]})[0].children[1]
        self.assertTrue(unavailable.disabled)
        self.assertIsNone(unavailable.href)
        self.assertFalse(available.disabled)
        self.assertEqual(available.href, '/jobs/128/isqc')

    def test_outputs_are_tagged_with_the_job_they_belong_to(self) -> None:
        with patch("callbacks.job_workspace.workflow_service.list_workflow_output_files", return_value=[{"a": 1}]):
            store = self.app.callbacks["load_job_outputs"]("/jobs/128/outputs", "tok", 7, 0, None)
        self.assertEqual(store["job_id"], 128)

    def test_a_stale_output_store_is_not_shown_for_another_job(self) -> None:
        pane = self.app.callbacks["render_outputs_pane"]({"job_id": 999, "outputs": []}, "/jobs/128/outputs")
        self.assertIn("Loading", str(pane))

    def test_is_qc_is_not_fetched_from_another_section(self) -> None:
        with patch("callbacks.job_workspace.workflow_service.list_is_qc_targets") as targets:
            self.app.callbacks["load_is_qc_standards"]("/jobs/128/eic", "tok", 7)
        targets.assert_not_called()


class PollingDoesNotDisturbTheReaderTests(unittest.TestCase):
    """Regressions for a poll that reset the EIC viewer every fifteen seconds.

    Two independent causes: the detail pane rebuilt its section on every tick,
    and the outputs store rewrote an equal-but-new object, which cascaded through
    the archive dropdown into the feature selection and wiped the plot.
    """

    def setUp(self) -> None:
        self.app = _CallbackCapture()
        register_callbacks(self.app)
        context = patch("callbacks.job_workspace.dash.ctx", SimpleNamespace(triggered_prop_ids={"jw-poll.n_intervals": "jw-poll"}))
        context.start()
        self.addCleanup(context.stop)

    def _inputs(self, name):
        return [d.component_id for d in self.app.dependencies[name] if isinstance(d, Input)]

    def test_the_section_shell_is_not_rebuilt_by_the_poll(self) -> None:
        self.assertNotIn("jw-poll", self._inputs("render_job_detail"))
        self.assertNotIn("jw-jobs-store", self._inputs("render_job_detail"))

    def test_the_review_sections_are_not_polled(self) -> None:
        for name in ["load_is_qc_standards", "refresh_is_qc_review", "select_is_qc_target", "render_is_qc_evidence",
                     "hydrate_eic_archives", "hydrate_eic_context", "render_eic_trace",
                     "resolve_feature_selection", "continue_open_job"]:
            with self.subTest(callback=name):
                self.assertNotIn("jw-poll", self._inputs(name))

    def test_live_data_reaches_the_overview_through_the_store(self) -> None:
        # A tick refreshes the store; the store refresh redraws the overview.
        self.assertIn("jw-poll", self._inputs("load_job_list"))
        self.assertNotIn("jw-poll", self._inputs("render_overview_pane"))
        self.assertIn("jw-jobs-store", self._inputs("render_overview_pane"))

    def test_unchanged_selection_does_not_re_render_the_list(self) -> None:
        """The rows are identical, so the callback declines to rewrite them."""
        import dash
        jobs = [{"id": 128, "status": "running", "run_name": "run_128"}]
        store = {"jobs": jobs, "open_job": jobs[0], "section": "overview", "selected_job_id": 128,
                 "cache": build_page_cache(None, "tok", assay_id=7)}
        with patch("callbacks.job_workspace.workflow_service.list_workflow_jobs", return_value=jobs):
            rows, status, payload = self.app.callbacks["load_job_list"](
                "tok", 7, 0, 0, None, None, "/jobs/128", store)
        for value in (rows, status, payload):
            self.assertIs(value, dash.no_update)

    def test_changing_section_does_re_render_the_list(self) -> None:
        """Rows link into the current section, so those hrefs must follow."""
        jobs = [{"id": 128, "status": "running", "run_name": "run_128"}]
        store = {"jobs": jobs, "section": "overview"}
        with patch("callbacks.job_workspace.workflow_service.list_workflow_jobs", return_value=jobs):
            rows, _status, payload = self.app.callbacks["load_job_list"](
                "tok", 7, 0, 0, None, None, "/jobs/128/eic", store)
        self.assertEqual(payload["section"], "eic")
        self.assertEqual(rows[0].href, "/jobs/128/eic")
        self.assertIs(rows[0].active, True)

    def test_switching_job_updates_selected_row(self) -> None:
        jobs = [{"id": 128}, {"id": 129}]
        store = {"jobs": jobs, "section": "eic", "selected_job_id": 128}
        with patch("callbacks.job_workspace.workflow_service.list_workflow_jobs", return_value=jobs):
            rows, _, payload = self.app.callbacks["load_job_list"](
                "tok", 7, 0, 0, None, None, "/jobs/129/eic", store)
        self.assertEqual([row.active for row in rows], [False, True])
        self.assertEqual(payload['selected_job_id'], 129)

    def test_unchanged_outputs_do_not_rewrite_the_store(self) -> None:
        import dash
        existing = {"job_id": 128, "outputs": [{"file_id": 1}],
                    "cache": build_page_cache(None, "tok", assay_id=7)}
        with patch("callbacks.job_workspace.workflow_service.list_workflow_output_files",
                   return_value=[{"file_id": 1}]):
            result = self.app.callbacks["load_job_outputs"]("/jobs/128/eic", "tok", 7, 3, existing)
        self.assertIs(result, dash.no_update)

    def test_changed_outputs_do_rewrite_the_store(self) -> None:
        existing = {"job_id": 128, "outputs": []}
        with patch("callbacks.job_workspace.workflow_service.list_workflow_output_files",
                   return_value=[{"file_id": 9}]):
            result = self.app.callbacks["load_job_outputs"]("/jobs/128/eic", "tok", 7, 3, existing)
        self.assertEqual(result["job_id"], 128)
        self.assertEqual(result["outputs"], [{"file_id": 9}])
        self.assertTrue(page_cache_matches(result["cache"], "tok", assay_id=7))


class WorkspaceRouteCacheTests(unittest.TestCase):
    def setUp(self):
        self.app = _CallbackCapture()
        register_callbacks(self.app)
        self.jobs = [{"id": 128}, {"id": 129}]
        self.cache = build_page_cache(None, "tok", assay_id=7)

    def _load(self, name, current, *, triggers=("url.pathname",), token="tok", assay_id=7, job_id=128):
        ctx = SimpleNamespace(triggered_prop_ids={key: key.split(".")[0] for key in triggers})
        with patch("callbacks.job_workspace.dash.ctx", ctx):
            if name == "load_job_list":
                return self.app.callbacks[name](token, assay_id, 0, 0, None, None,
                                                f"/jobs/{job_id}/eic", current)
            return self.app.callbacks[name](f"/jobs/{job_id}/eic", token, assay_id, 0, current)

    def test_section_change_reuses_metadata_and_preserves_selected_row(self):
        current = {"jobs": self.jobs, "open_job": self.jobs[0], "section": "overview",
                   "selected_job_id": 128, "cache": self.cache}
        with patch("callbacks.job_workspace.workflow_service.list_workflow_jobs") as api:
            rows, status, payload = self._load("load_job_list", current)
        api.assert_not_called()
        self.assertEqual([row.href for row in rows], ["/jobs/128/eic", "/jobs/129/eic"])
        self.assertEqual([row.active for row in rows], [True, False])
        self.assertEqual(payload["jobs"], self.jobs)
        self.assertEqual(status, "2 jobs")
        outputs = {"job_id": 128, "outputs": [{"file_id": 9}], "cache": self.cache}
        with patch("callbacks.job_workspace.workflow_service.list_workflow_output_files") as api:
            result = self._load("load_job_outputs", outputs)
        api.assert_not_called()
        import dash
        self.assertIs(result, dash.no_update)

    def test_metadata_refetches_for_active_triggers_or_different_context(self):
        cases = [dict(triggers=(trigger,)) for trigger in (
            "jw-poll.n_intervals", "jw-refresh-btn.n_clicks", "token-store.data", "assay-select-store.data")]
        cases += [dict(triggers=("url.pathname", "jw-poll.n_intervals")),
                  dict(token="new-token"), dict(assay_id=8), dict(job_id=129)]
        for name, method, data in [("load_job_list", "list_workflow_jobs", self.jobs),
                                   ("load_job_outputs", "list_workflow_output_files", [{"file_id": 9}])]:
            current = ({"jobs": self.jobs, "section": "overview", "selected_job_id": 128}
                       if name == "load_job_list" else {"job_id": 128, "outputs": data})
            current["cache"] = self.cache
            for case in cases:
                with self.subTest(callback=name, case=case), \
                        patch(f"callbacks.job_workspace.workflow_service.{method}", return_value=data) as api:
                    self._load(name, current, **case)
                    api.assert_called_once()

    def test_failed_fetch_discards_old_context_and_retries_on_next_route(self):
        for name, method in [("load_job_list", "list_workflow_jobs"),
                             ("load_job_outputs", "list_workflow_output_files")]:
            current = {"jobs": self.jobs, "outputs": [{"file_id": 9}], "job_id": 128,
                       "selected_job_id": 128, "cache": self.cache}
            with self.subTest(callback=name), \
                    patch(f"callbacks.job_workspace.workflow_service.{method}", side_effect=APIError("offline")):
                result = self._load(name, current, token="new-token")
            failed = result[2] if name == "load_job_list" else result
            self.assertTrue(failed["failed"])
            self.assertFalse(failed.get("jobs") or failed.get("outputs"))
            with patch(f"callbacks.job_workspace.workflow_service.{method}", return_value=[]) as api:
                self._load(name, failed, token="new-token")
            api.assert_called_once()

    def test_store_keeps_list_rows_and_only_the_open_job_in_full(self):
        jobs = [{"id": 128, "status": "running", "run_name": "run_128", "input_manifest_json": {"files": ["a"]},
                 "status_payload_json": {"tasks": []}, "finished_at": None},
                {"id": 129, "status": "failed", "run_name": "run_129", "input_manifest_json": {"files": ["b"]}}]
        with patch("callbacks.job_workspace.workflow_service.list_workflow_jobs", return_value=jobs):
            payload = self._load("load_job_list", None)[2]
        self.assertEqual(payload["jobs"], [{"id": 128, "status": "running", "run_name": "run_128"},
                                           {"id": 129, "status": "failed", "run_name": "run_129"}])
        self.assertEqual(payload["open_job"], jobs[0])
        self.assertIs(job_from_store(payload, 128), jobs[0])

    def test_list_rows_never_stand_in_for_the_open_job(self):
        store = {"jobs": [{"id": 128}, {"id": 129}], "open_job": {"id": 128, "status": "running"}}
        self.assertIsNone(job_from_store(store, 129))
        self.assertIsNone(job_from_store({"jobs": [{"id": 128}]}, 128))

    def test_store_written_before_open_job_is_refetched(self):
        current = {"jobs": self.jobs, "section": "overview", "selected_job_id": 128, "cache": self.cache}
        with patch("callbacks.job_workspace.workflow_service.list_workflow_jobs", return_value=self.jobs) as api:
            payload = self._load("load_job_list", current)[2]
        api.assert_called_once()
        self.assertEqual(payload["open_job"], {"id": 128})

    def test_fetched_context_uses_auth_fingerprint(self):
        with patch("callbacks.job_workspace.workflow_service.list_workflow_jobs", return_value=self.jobs):
            payload = self._load("load_job_list", None)[2]
        self.assertTrue(page_cache_matches(payload["cache"], "tok", assay_id=7))
        self.assertNotIn("tok", repr(payload))


class MinimalPickerTests(unittest.TestCase):
    """The list identifies a job; everything about it lives in the sections."""

    def test_the_picker_carries_no_progress_or_metrics(self) -> None:
        job = {"id": 1, "status": "running", "run_name": "run_1",
               "execution_profile": "local_docker", "started_at": "2026-09-02T10:00:00Z",
               "status_payload_json": {"completed_tasks": 3, "total_tasks": 10}}
        row = str(job_list_items([job])[0].children)
        for noise in ["Progress", "3 / 10", "tasks"]:
            with self.subTest(absent=noise):
                self.assertNotIn(noise, row)

    def test_the_repeated_study_and_assay_prefix_is_dropped(self) -> None:
        # Every job here belongs to the selected study and assay, and the sidebar
        # names both, so std9_assay18_ on each row is noise.
        self.assertEqual(short_run_name({"id": 130, "run_name": "std9_assay18_job130"}), "job130")

    def test_only_that_exact_prefix_is_dropped(self) -> None:
        for run_name, expected in [
            ("hpc_smoke_42", "hpc_smoke_42"),
            ("std9_job128", "job128"),
            ("mystudy9_assay18_job1", "mystudy9_assay18_job1"),
        ]:
            with self.subTest(run_name=run_name):
                self.assertEqual(short_run_name({"id": 1, "run_name": run_name}), expected)

    def test_a_job_without_a_run_name_still_reads(self) -> None:
        self.assertEqual(short_run_name({"id": 129, "run_name": ""}), "job_129")
        self.assertEqual(short_run_name({"id": 129}), "job_129")

    def test_the_full_name_survives_in_the_hover(self) -> None:
        row = job_list_items([{"id": 130, "status": "succeeded",
                               "run_name": "std9_assay18_job130"}])[0]
        self.assertIn("std9_assay18_job130", row.children.title)
        self.assertEqual(row.children.children[1].children, "job130")

    def test_the_picker_still_shows_name_and_status(self) -> None:
        rendered = str(job_list_items([{"id": 1, "status": "running", "run_name": "run_1"}]))
        self.assertIn("run_1", rendered)
        self.assertIn("running", rendered.lower())

    def test_status_is_a_coloured_square_not_a_badge(self) -> None:
        row = job_list_items([{"id": 1, "status": "succeeded", "run_name": "run_1"}])[0]
        square = row.children.children[0]
        self.assertEqual(square.className, "bg-success")
        self.assertEqual(square.style["width"], "9px")

    def test_every_status_maps_to_a_colour(self) -> None:
        for status, colour in [("succeeded", "success"), ("failed", "danger"),
                               ("running", "info"), ("queued", "warning"),
                               ("cancelled", "secondary"), ("nonsense", "light")]:
            with self.subTest(status=status):
                self.assertEqual(status_colour(status), colour)

    def test_the_full_status_is_available_on_hover(self) -> None:
        row = job_list_items([{"id": 1, "status": "failed", "run_name": "run_1",
                               "execution_profile": "local_docker"}])[0]
        self.assertIn("failed", row.children.title)
        self.assertIn("local_docker", row.children.title)

    def test_launch_time_is_compact(self) -> None:
        self.assertEqual(
            compact_launch_time({"started_at": "2026-09-02T14:07:00+00:00"}),
            "02 Sep 14:07",
        )

    def test_launch_time_falls_back_to_creation(self) -> None:
        self.assertEqual(
            compact_launch_time({"created_at": "2026-09-02T09:30:00+00:00"}),
            "02 Sep 09:30",
        )

    def test_an_older_year_is_shown_instead_of_the_clock(self) -> None:
        self.assertEqual(compact_launch_time({"started_at": "2019-03-04T11:00:00+00:00"}),
                         "04 Mar 19")

    def test_a_missing_or_unparsable_time_is_blank(self) -> None:
        for job in [{}, {"started_at": "not a date"}, {"started_at": None}]:
            with self.subTest(job=job):
                self.assertEqual(compact_launch_time(job), "")

    def test_the_overview_keeps_the_metrics(self) -> None:
        rendered = str(overview_view({
            "id": 1, "status": "running", "run_name": "run_1",
            "execution_profile": "local_docker",
            "status_payload_json": {"completed_tasks": 3, "total_tasks": 10},
        }))
        self.assertIn("local_docker", rendered)
        self.assertIn("3 / 10", rendered)


class EicPlotSizingTests(unittest.TestCase):
    """The shared builders set their own layout height, which plotly honours over
    the container's CSS. Sizing the div alone let the chromatogram overflow into
    the feature map below it."""

    def test_the_builders_really_do_set_their_own_height(self) -> None:
        from ui.figures import build_eic_figure, empty_feature_map_figure
        self.assertIsNotNone(build_eic_figure(None).layout.height)
        self.assertIsNotNone(empty_feature_map_figure().layout.height)

    def test_sizing_overrides_the_builder(self) -> None:
        from ui.figures import build_eic_figure
        from callbacks.job_workspace import EIC_TRACE_HEIGHT, sized
        figure = sized(build_eic_figure(None), EIC_TRACE_HEIGHT)
        self.assertEqual(figure.layout.height, EIC_TRACE_HEIGHT)

    def test_both_plots_are_sized_to_sit_together(self) -> None:
        from callbacks.job_workspace import EIC_MAP_HEIGHT, EIC_TRACE_HEIGHT
        self.assertLessEqual(EIC_TRACE_HEIGHT + EIC_MAP_HEIGHT, 640)

    def test_the_chromatogram_stays_taller_than_the_map(self) -> None:
        """Equal heights made the chromatogram look squat; the builders use
        460 against 360 and that ratio is worth keeping."""
        from callbacks.job_workspace import EIC_MAP_HEIGHT, EIC_TRACE_HEIGHT
        self.assertGreater(EIC_TRACE_HEIGHT, EIC_MAP_HEIGHT)
        self.assertAlmostEqual(EIC_TRACE_HEIGHT / EIC_MAP_HEIGHT, 460 / 360, delta=0.12)

    @staticmethod
    def _chromatogram():
        """A payload with real peaks. build_eic_figure(None) returns a placeholder
        with different margins, so asserting against it proves nothing."""
        from ui.figures import build_eic_figure
        return build_eic_figure({
            "feature": {"metabolite_name": "Caffeine", "average_mz": 195.0877,
                        "average_rt": 4.12},
            "spot": {"peaks": [{"sample_name": "QC_01", "left": 4.0, "right": 4.3,
                                "points": [{"x": 4.0, "intensity": 10},
                                           {"x": 4.15, "intensity": 900},
                                           {"x": 4.3, "intensity": 20}]}]},
        })

    def test_sizing_leaves_the_builders_margins_alone(self) -> None:
        """The chromatogram's title and horizontal legend both sit above the plot
        area at y=1.02, inside the top margin. Shrinking it drops the legend onto
        the traces."""
        from callbacks.job_workspace import EIC_TRACE_HEIGHT, sized
        before = self._chromatogram().layout.margin
        after = sized(self._chromatogram(), EIC_TRACE_HEIGHT).layout.margin
        self.assertEqual((after.t, after.b, after.l, after.r),
                         (before.t, before.b, before.l, before.r))

    def test_the_legend_still_has_room_above_the_plot(self) -> None:
        from callbacks.job_workspace import EIC_TRACE_HEIGHT, sized
        figure = sized(self._chromatogram(), EIC_TRACE_HEIGHT)
        self.assertGreaterEqual(figure.layout.legend.y, 1.0)
        self.assertGreaterEqual(figure.layout.margin.t, 48)

    def test_the_starting_map_is_sized_too(self) -> None:
        """The empty map rendered before an archive loads must match, or the page
        jumps once real data arrives."""
        from callbacks.job_workspace import EIC_MAP_HEIGHT, eic_view

        def find(node, target):
            if getattr(node, "id", None) == target:
                return node
            children = getattr(node, "children", None)
            for child in (children if isinstance(children, list) else [children]):
                if child is None or isinstance(child, str):
                    continue
                found = find(child, target)
                if found is not None:
                    return found
            return None

        for block in eic_view():
            graph = find(block, "jw-eic-map")
            if graph is not None:
                self.assertEqual(graph.figure.layout.height, EIC_MAP_HEIGHT)
                return
        self.fail("jw-eic-map is not in the EIC view")


class MetroMapOnTheOverviewTests(unittest.TestCase):
    def test_the_map_is_drawn_during_materialisation(self):
        from callbacks.job_workspace import metro_map
        blocks = metro_map({"id": 1, "status": "running", "status_payload_json": {}})
        self.assertEqual([type(b).__name__ for b in blocks], ["Div", "Img"])
        self.assertIn("waiting for the first task", str(blocks[0]))

    def test_the_map_reports_progress_once_tasks_run(self):
        from callbacks.job_workspace import metro_map
        job = {"id": 1, "status": "running", "status_payload_json": {
            "process_states": {"prepare_msdial5_input": "done",
                               "RUN_MSDIAL5_PREPARED_TO_STATS_TXT:peak_detection_msdial5": "running"}}}
        rendered = str(metro_map(job)[0])
        self.assertIn("1 done", rendered)
        self.assertIn("1 running", rendered)

    def test_a_metabolensr_run_is_drawn_on_its_own_route_map(self):
        from urllib.parse import unquote
        from callbacks.job_workspace import metro_map
        stages = [
            {"id": "engine", "label": "MS-DIAL", "patterns": ["peak_detection_msdial5"]},
            {"id": "qc_preview", "label": "Preview", "patterns": ["METABOLENSR_PREVIEW"]},
            {"id": "finalize", "label": "Finalize", "patterns": ["METABOLENSR_FINALIZE"]},
        ]
        job = {"id": 1, "status": "running",
               "input_manifest_json": {"engine": {"descriptor": {"stages": stages}}},
               "status_payload_json": {"process_states": {
                   "peak_detection_msdial5": "done",
                   "METABOLENSR_QC:METABOLENSR_PREVIEW": "running"}}}
        blocks = metro_map(job)
        src = unquote(blocks[1].src)
        self.assertIn("MetaboLensR preview", src)
        self.assertIn('rect[data-station-id="qc_preview"]{stroke:#193F90', src)
        self.assertEqual(len(blocks), 2)  # nothing left "Not on the map"


class LayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.layout = job_workspace_layout()
        self.card = [x for x in self.layout if type(x).__name__ == "Card"][0]

    def test_the_page_carries_no_explanatory_prose(self) -> None:
        rendered = str(self.layout)
        for prose in ["Job workspace", "address bar", "reloaded"]:
            with self.subTest(absent=prose):
                self.assertNotIn(prose, rendered)

    def test_the_picker_lives_in_the_shell_sidebar(self) -> None:
        from pages.shell import app_layout
        shell = str(app_layout())
        for component in ["sidebar-jobs", "jw-job-list", "jw-job-list-status", "jw-refresh-btn"]:
            with self.subTest(component=component):
                self.assertIn(f"id='{component}'", shell)
                self.assertNotIn(f"id='{component}'", str(self.layout))

    def test_the_list_is_fetched_only_on_the_jobs_page(self) -> None:
        # The poll and the store are Inputs of the list callback; kept in the
        # page, Dash never runs it on other routes.
        ids = {getattr(x, "id", None) for x in self.layout}
        self.assertTrue({"jw-poll", "jw-jobs-store"} <= ids)

    def test_the_picker_shows_on_jobs_routes_only(self) -> None:
        app = _CallbackCapture()
        register_callbacks(app)
        show = app.callbacks["show_sidebar_jobs"]
        self.assertFalse(show("/jobs"))
        self.assertFalse(show("/jobs/146/eic"))
        self.assertTrue(show("/workflows"))
        self.assertTrue(show("/jobsearch"))
        self.assertTrue(show(None))

    def test_wide_content_scrolls_inside_the_detail_card(self) -> None:
        self.assertEqual(self.card.style["minWidth"], "0")

    def test_the_section_nav_sits_inside_the_detail_card(self) -> None:
        body = self.card.children.children
        self.assertEqual([c.id for c in body], ["jw-section-nav", "jw-detail"])

    def test_no_nav_no_gap_when_no_job_is_open(self) -> None:
        self.assertEqual(section_nav(None, "overview"), [])


class ReviewCheckpointTests(unittest.TestCase):
    def test_output_download_uses_registered_file_for_the_open_job(self):
        app = _CallbackCapture()
        register_callbacks(app)
        output = {'file_id': 88, 'original_filename': 'qc_report.html', 'artifact_role': 'metabolensr_report'}
        with (patch('callbacks.job_workspace.dash.ctx', SimpleNamespace(triggered_id={'type': 'jw-output-download-btn', 'file': 88})),
              patch('callbacks.job_workspace.workflow_service.workflow_output_bytes', return_value=(b'<html>QC</html>', 'text/html')) as download):
            result, status = app.callbacks['download_registered_output']([1], '/jobs/128/outputs',
                {'job_id': 128, 'outputs': [output]}, 'tok', 7)
            self.assertEqual(result['filename'], 'qc_report.html')
            self.assertEqual(status, '')
            download.assert_called_once_with('tok', 7, 88)
            download.reset_mock()
            app.callbacks['download_registered_output']([1], '/jobs/129/outputs',
                {'job_id': 128, 'outputs': [output]}, 'tok', 7)
            download.assert_not_called()

    def test_preview_continue_uses_automatic_decisions_without_sending_rt_controls(self):
        app = _CallbackCapture()
        register_callbacks(app)
        with patch('callbacks.job_workspace.workflow_service.continue_workflow_job', return_value={'id': 128}) as cont:
            app.callbacks['continue_automatic_preview'](1, '/jobs/128', 'tok', 7)
        cont.assert_called_once_with('tok', 7, 128)

    def setUp(self) -> None:
        self.app = _CallbackCapture()
        register_callbacks(self.app)

    def test_continue_is_blocked_for_a_job_that_cannot_resume(self) -> None:
        running = [{"id": 128, "status": "running", "run_root": "/runs/128", "input_manifest_json": MSDIAL_ENGINE_MANIFEST}]
        disabled, reason = self.app.callbacks["gate_continue"](
            "/jobs/128/isqc", None, {"open_job": running[0], "section": "isqc"})
        self.assertTrue(disabled)
        self.assertIn("still running", reason)

    def test_continue_is_offered_for_a_paused_is_qc_job(self) -> None:
        paused = [{"id": 128, "status": "succeeded", "run_root": "/runs/128", "input_manifest_json": MSDIAL_ENGINE_MANIFEST,
                   "parameter_values_json": {"workflow_stage": "is_qc_review"}}]
        disabled, reason = self.app.callbacks["gate_continue"](
            "/jobs/128/isqc", None, {"open_job": paused[0], "section": "isqc"})
        self.assertFalse(disabled)
        self.assertIn("paused after IS-QC", reason)

    STANDARDS = {"standards": [{"id": "t1", "target_id": "t1", "target_name": "A"},
                               {"id": "t2", "target_id": "t2", "target_name": "B"},
                               {"id": "t3", "target_id": "t3", "target_name": "C"}]}

    def test_rt_targets_are_sent_only_when_correction_is_applied(self) -> None:
        with patch("callbacks.job_workspace.workflow_service.continue_workflow_job",
                   return_value={"id": 130}) as cont:
            self.app.callbacks["continue_open_job"](1, "/jobs/128/isqc", True, "note", [0, 2], self.STANDARDS, "tok", 7)
            self.assertEqual(cont.call_args.kwargs["rt_correction_target_ids"], ["t1", "t3"])
            cont.reset_mock()
            self.app.callbacks["continue_open_job"](1, "/jobs/128/isqc", False, None, [0, 2], self.STANDARDS, "tok", 7)
            self.assertIsNone(cont.call_args.kwargs["rt_correction_target_ids"])

    def test_loading_ticks_every_standard_that_has_an_id(self) -> None:
        rows = [{"target_id": "t1", "target_name": "A"}, {"target_name": "Unidentified"},
                {"target_id": "t2", "target_name": "B"}]
        with patch("callbacks.job_workspace.workflow_service.list_is_qc_targets", return_value=rows):
            store, ticked = self.app.callbacks["load_is_qc_standards"]("/jobs/128/isqc", "tok", 7)
        self.assertEqual([s["target_name"] for s in store["standards"]], ["A", "Unidentified", "B"])
        self.assertEqual(ticked, [0, 2])

    def test_a_failed_load_is_shown_on_the_status_line(self) -> None:
        state = self.app.callbacks["refresh_is_qc_review"]([], {"error": "404: gone"}, "/jobs/128/isqc", "tok", 7)[1]
        self.assertIn("404: gone", str(state))

    def test_unticking_a_standard_recomputes_the_table(self) -> None:
        with (
            patch("callbacks.job_workspace.workflow_service.list_is_qc_targets", return_value=[]) as targets,
            patch("callbacks.job_workspace.workflow_service.get_is_qc_summary", return_value={}),
        ):
            _data, state, _count = self.app.callbacks["refresh_is_qc_review"](
                [0], self.STANDARDS, "/jobs/128/isqc", "tok", 7)
        self.assertEqual(targets.call_args.kwargs["rt_correction_target_ids"], ["t1"])
        self.assertIn("1 in the RT model", state)

    def _select(self, trigger, cell=None, current=None):
        from types import SimpleNamespace
        with patch("callbacks.job_workspace.dash.ctx", SimpleNamespace(triggered_id=trigger)):
            return self.app.callbacks["select_is_qc_target"](cell, 0, 0, self.STANDARDS, current)

    def test_the_first_standard_is_shown_when_the_list_loads(self) -> None:
        self.assertEqual(self._select("jw-isqc-standards-store"), ("A", "Standard 1 of 3"))

    def test_clicking_a_row_chooses_whose_traces_to_show(self) -> None:
        # row is the index on the visible page; row_id names the standard.
        name, _ = self._select("jw-isqc-table", {"row": 0, "row_id": "t2", "column_id": "qc"}, current="A")
        self.assertEqual(name, "B")

    def test_previous_and_next_step_through_the_standards_and_stop_at_the_ends(self) -> None:
        self.assertEqual(self._select("jw-isqc-next", current="B")[0], "C")
        self.assertEqual(self._select("jw-isqc-next", current="C")[0], "C")
        self.assertEqual(self._select("jw-isqc-prev", current="A")[0], "A")

    def test_a_tick_in_the_original_view_leaves_the_plot_alone(self) -> None:
        import dash
        from types import SimpleNamespace
        with (
            patch("callbacks.job_workspace.dash.ctx", SimpleNamespace(triggered_id="jw-isqc-table")),
            patch("callbacks.job_workspace.workflow_service.get_is_qc_trace") as trace,
        ):
            result = self.app.callbacks["render_is_qc_evidence"](
                "A", "original", [0], self.STANDARDS, "/jobs/128/isqc", "tok", 7)
        self.assertEqual(result, (dash.no_update, dash.no_update))
        trace.assert_not_called()

    def test_corrected_traces_follow_the_ticked_standards(self) -> None:
        from types import SimpleNamespace
        with (
            patch("callbacks.job_workspace.dash.ctx", SimpleNamespace(triggered_id="jw-isqc-table")),
            patch("callbacks.job_workspace.workflow_service.list_is_qc_targets", return_value=[]),
            patch("callbacks.job_workspace.workflow_service.get_is_qc_trace", return_value={}) as trace,
        ):
            self.app.callbacks["render_is_qc_evidence"](
                "A", "corrected", [1, 2], self.STANDARDS, "/jobs/128/isqc", "tok", 7)
        self.assertEqual(trace.call_args.kwargs["rt_correction_target_ids"], ["t2", "t3"])

    def test_ticked_rows_map_back_to_target_ids_in_table_order(self) -> None:
        self.assertEqual(selected_target_ids([2, 0, 7], self.STANDARDS["standards"]), ["t1", "t3"])
        self.assertEqual(selected_target_ids(None, self.STANDARDS["standards"]), [])

    def test_table_rows_and_the_standards_store_share_one_order_and_id(self) -> None:
        rows = [{"target_id": "t2", "target_name": "B", "status": "pass"},
                {"target_id": "t1", "target_name": "A", "status": "pass"},
                {"target_id": "t2", "target_name": "B", "status": "fail"}]
        self.assertEqual([r["id"] for r in is_qc_table_data(rows)], [s["id"] for s in is_qc_standards(rows)])

    def test_the_verdict_is_the_summarys_threshold_not_a_pass_count(self) -> None:
        rows = [{"target_id": "t1", "target_name": "A", "status": "pass", "corrected_rt_min": "9.01"},
                {"target_id": "t1", "target_name": "A", "status": "fail", "corrected_rt_min": "9.03"}]
        summary = {"target_statuses": {"A": {"status": "pass"}}}
        row = is_qc_table_data(rows, summary)[0]
        self.assertEqual((row["status"], row["qc"], row["corrected_rt"]), ("pass", "pass · 1/2", "9.010–9.030"))
        self.assertEqual(is_qc_table_data(rows)[0]["status"], "fail")

    def test_the_status_line_reads_the_summary_counts(self) -> None:
        summary = {"overall_status": "pass", "counts": {"passing_targets": 17, "targets": 20}}
        self.assertEqual(is_qc_state_line(summary, 20, 19),
                         "IS-QC pass · 17/20 standards passed · 19 in the RT model")
        self.assertEqual(is_qc_state_line({}, 3, 3), "3 standards · 3 in the RT model")

    def test_evidence_gives_each_samples_result_and_opens_on_a_failure(self) -> None:
        rows = [{"target_id": "1", "target_name": "A", "expected_mz": "94.06205452", "expected_rt_min": "9.0",
                 "sample_name": "s1", "observed_rt_min": "8.9", "rt_error_min": "-0.1", "status": "pass", "message": "OK"},
                {"target_id": "1", "target_name": "A", "sample_name": "s2", "observed_rt_min": "9.645",
                 "rt_error_min": "0.645", "status": "fail", "message": "RT error 0.645 min exceeds +/- 0.5 min"},
                {"target_id": "2", "target_name": "B", "sample_name": "s1", "status": "pass"}]
        header, facts, verdict, details = is_qc_evidence("A", rows)
        self.assertEqual(header.children, "A")
        self.assertEqual(facts.children, "ID 1 · m/z 94.06205 · expected RT 9.00 min")
        self.assertEqual(verdict.children, "QC: 1 / 2 samples passing")
        self.assertTrue(details.open)
        samples = [li.children for li in details.children[1].children]
        self.assertEqual(samples, ["s1: pass · RT 8.900 min (-0.100)",
                                   "s2: fail · RT 9.645 min (+0.645) · RT error 0.645 min exceeds +/- 0.5 min"])

    def test_a_finished_continue_reports_the_new_job(self) -> None:
        result = {"job_id": 128, "ok": True, "new_job_id": 130}
        disabled, reason = self.app.callbacks["gate_continue"]("/jobs/128/isqc", result, None)
        self.assertTrue(disabled)
        self.assertIn("130", reason)


class FeatureResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = _CallbackCapture()
        register_callbacks(self.app)
        self.resolve = self.app.callbacks["resolve_feature_selection"]

    def _with_trigger(self, trigger, *args):
        from types import SimpleNamespace
        with patch("callbacks.job_workspace.dash.ctx", SimpleNamespace(triggered_id=trigger)):
            return self.resolve(*args)

    def _catalogue(self, archive):
        return self.app.callbacks['hydrate_eic_context'](archive, 'tok', 7)

    def test_typing_and_browser_search_catalogue_never_reach_a_server_callback(self):
        for name, dependencies in self.app.dependencies.items():
            with self.subTest(callback=name):
                self.assertNotIn(('jw-eic-feature', 'search_value'),
                                 [(d.component_id, d.component_property) for d in dependencies if isinstance(d, Input)])
                self.assertNotIn('jw-eic-search-store',
                                 [d.component_id for d in dependencies if isinstance(d, (Input, State))])
        browser = self.app.clientside['filterOptions']
        self.assertIn('jw-eic-search-store', [d.component_id for d in browser if isinstance(d, Input)])
        self.assertIn('jw-eic-feature', [d.component_id for d in browser if isinstance(d, Output)])

    def test_search_catalogue_keeps_exact_flags_and_rounded_numeric_search_fields(self):
        features = [{'feature_id': 'opaque/0', 'label': 'Reference standard', 'metabolite_name': 'Caffeine',
                     'adduct_type': '[M+H]+', 'average_mz': 195.08765, 'average_rt': 1.234,
                     'annotated': True, 'has_ms2': True, 'msms_matched': False,
                     'has_reference': False, 'low_score': True, 'decisions': {'ignored': True}}]
        with patch('callbacks.job_workspace.workflow_service.get_package_feature_map', return_value=features):
            catalogue = self._catalogue('bundle:128:90')[5]
        self.assertIsInstance(catalogue, dict)
        self.assertEqual(catalogue['archive'], 'bundle:128:90')
        option = catalogue['options'][0]
        self.assertEqual(option['value'], 'opaque/0')
        self.assertEqual(option['flags'], ['unmatched', 'lowscore'])
        for token in ['caffeine', '[m+h]+', '195.08765', '195.0876', '195.088', '195.09', '1.23', '1.2']:
            self.assertIn(token, option['search'])
        self.assertNotIn('decisions', option)

    def test_initial_package_map_and_complete_dropdown_share_one_catalogue_request(self):
        features = [{'feature_id': str(i), 'label': f'Source ID {i}',
                     'average_rt': 1.0, 'average_mz': 100.0} for i in range(81)]
        values = {('jw-eic-archive', 'value'): 'bundle:128:90',
                  ('jw-eic-feature', 'search_value'): None,
                  ('jw-eic-feature-store', 'data'): None,
                  ('token-store', 'data'): 'tok', ('assay-select-store', 'data'): 7}
        requests = []

        def catalogue(method, path, **kwargs):
            requests.append((method, path))
            return features

        with patch('services.workflow_service.call_api', side_effect=catalogue), \
                patch('callbacks.job_workspace.dash.ctx', SimpleNamespace(triggered_id=None)):
            for name, dependencies in self.app.dependencies.items():
                outputs = [d for d in dependencies if isinstance(d, Output)]
                if not any((d.component_id, d.component_property) in {
                        ('jw-eic-map', 'figure'), ('jw-eic-feature', 'options')} for d in outputs):
                    continue
                args = [values.get((d.component_id, d.component_property)) for d in dependencies
                        if isinstance(d, (Input, State))]
                result = self.app.callbacks[name](*args)
                results = result if len(outputs) > 1 else [result]
                values.update({(d.component_id, d.component_property): value
                               for d, value in zip(outputs, results)})
        self.assertEqual(len(requests), 1, 'Opening a package should download its catalogue once')
        self.assertEqual(len(values[('jw-eic-search-store', 'data')]['options']), 81)
        self.assertEqual(values[('jw-eic-search-store', 'data')]['options'][-1]['value'], '80')
        self.assertEqual(values[('jw-eic-context-store', 'data')]['feature_ids'][-1], '80')
        self.assertEqual(set(values[('jw-eic-context-store', 'data')]), {'bundle', 'feature_ids'})

    def test_changing_archive_clears_previous_search_without_a_second_request(self):
        requests = []

        def catalogue(method, path, **kwargs):
            requests.append(path)
            return [{'feature_id': 'engine/id:2', 'label': 'Unknown'}]

        with patch('services.workflow_service.call_api', side_effect=catalogue):
            result = self._catalogue('bundle:129:92')
        self.assertEqual(requests, ['/assays/7/workflow-jobs/129/features/map?preview_file_id=92'])
        self.assertEqual(result[4], {'bundle': 'bundle:129:92', 'feature_ids': ['engine/id:2']})
        self.assertEqual(result[5]['archive'], 'bundle:129:92')
        self.assertEqual(result[5]['options'][0]['value'], 'engine/id:2')
        self.assertIsNone(result[6])

    def test_package_dropdown_includes_more_than_two_hundred_features(self):
        features = [{'feature_id': str(i), 'label': f'Source ID {i}'} for i in range(251)]
        with patch('callbacks.job_workspace.workflow_service.get_package_feature_map', return_value=features):
            options = self._catalogue('bundle:128:90')[5]['options']
        self.assertEqual(len(options), 251)
        self.assertEqual(options[-1]['value'], '250')

    def test_legacy_archive_also_uses_complete_local_search_catalogue(self):
        features = [{'feature_index': i, 'label': f'Index {i}'} for i in range(251)]
        with patch('callbacks.job_workspace.workflow_service.list_eic_features', return_value=features):
            result = self._catalogue(91)
        self.assertEqual(len(result[5]['options']), 251)
        self.assertEqual(result[5]['options'][0]['value'], 0)
        self.assertEqual(result[5]['archive'], 91)
        self.assertIsNone(result[4])

    def test_original_archive_remains_selectable_with_package(self):
        outputs = [{'artifact_role': 'metabolensr_preview_manifest', 'file_id': 90},
                   {'artifact_role': 'msdial_eic_archive', 'file_id': 91}]
        options, value, _ = self.app.callbacks['hydrate_eic_archives'](
            {'job_id': 128, 'outputs': outputs}, '/jobs/128/eic')
        self.assertEqual([o['value'] for o in options], ['bundle:128:90', 91])
        self.assertEqual(value, 'bundle:128:90')

    def test_package_slider_resolves_opaque_id_not_binary_position(self):
        context = {"bundle": "bundle:128:90", "feature_ids": ["5", "0", "engine/id:2"]}
        store, dropdown, _ = self._with_trigger("jw-eic-slider", None, 2, None, "bundle:128:90", context)
        self.assertEqual(store["feature_id"], "engine/id:2")
        self.assertEqual(store["preview_file_id"], 90)
        self.assertEqual(dropdown, "engine/id:2")
        self.assertNotIn("feature_index", store)

    def test_package_map_click_preserves_zero_and_sets_slider_ordinal(self):
        context = {"bundle": "bundle:128:90", "feature_ids": ["5", "0", "2"]}
        click = {"points": [{"customdata": ["0"]}]}
        store, dropdown, slider = self._with_trigger("jw-eic-map", None, 0, click, "bundle:128:90", context)
        self.assertEqual((store["feature_id"], dropdown, slider), ("0", "0", 1))

    def test_stale_bundle_context_cannot_select_feature(self):
        import dash
        context = {"bundle": "bundle:128:89", "feature_ids": ["0"]}
        result = self._with_trigger("jw-eic-slider", None, 0, None, "bundle:128:90", context)
        self.assertTrue(all(x is dash.no_update for x in result))

    def test_empty_final_view_explains_statistics_selection(self):
        with patch('callbacks.job_workspace.workflow_service.get_package_feature_map', return_value=[]):
            _, status, maximum, _, context, _, _ = self._catalogue('bundle:128:92')
        self.assertIn('retained for statistics', status)
        self.assertIn('original archive', status)
        self.assertEqual(maximum, 0)
        self.assertEqual(context['feature_ids'], [])

    def test_final_bundle_is_default_instead_of_preview(self):
        outputs = [{'artifact_role': 'metabolensr_preview_manifest', 'file_id': 90},
                   {'artifact_role': 'metabolensr_manifest', 'file_id': 92},
                   {'artifact_role': 'msdial_eic_archive', 'file_id': 91}]
        options, value, status = self.app.callbacks['hydrate_eic_archives'](
            {'job_id': 128, 'outputs': outputs}, '/jobs/128/eic')
        self.assertEqual(value, 'bundle:128:92')
        self.assertIn('final', options[0]['label'])
        self.assertIn('statistics', options[0]['label'])
        self.assertEqual([o['value'] for o in options], ['bundle:128:92', 91])

    def test_package_bundle_is_browsable_without_an_archive(self):
        output = {"artifact_role": "metabolensr_preview_manifest", "file_id": 90, "workflow_job_id": 128,
                  "original_filename": "qc_preview_summary.json"}
        options, value, _ = self.app.callbacks["hydrate_eic_archives"]({"job_id":128,"outputs":[output]},"/jobs/128/eic")
        self.assertEqual(value,"bundle:128:90")
        self.assertEqual(len(options),2)

    def test_all_engine_features_remain_selectable_after_finalization(self):
        outputs = [{'artifact_role': 'metabolensr_preview_manifest', 'file_id': 90},
                   {'artifact_role': 'metabolensr_manifest', 'file_id': 92}]
        options, value, _ = self.app.callbacks['hydrate_eic_archives'](
            {'job_id': 128, 'outputs': outputs}, '/jobs/128/eic')
        self.assertEqual(value, 'bundle:128:92')
        self.assertEqual([o['value'] for o in options], ['bundle:128:92', 'engine:128:90'])
        self.assertIn('all aligned features', options[1]['label'])
        features = [{'feature_id': f'native/{i}', 'label': f'Source ID {i}'} for i in range(81)]
        with patch('services.workflow_service.call_api', return_value=features) as api:
            result = self._catalogue('engine:128:90')
        self.assertEqual(api.call_args.args[1], '/assays/7/workflow-jobs/128/features/map?preview_file_id=90')
        self.assertEqual(len(result[5]['options']), 81)
        store, dropdown, _ = self._with_trigger('jw-eic-slider', None, 80, None, 'engine:128:90', result[4])
        self.assertEqual(dropdown, 'native/80')
        with patch('callbacks.job_workspace.workflow_service.get_package_feature_trace', return_value={}) as trace:
            self.app.callbacks['render_eic_trace'](store, 'engine:128:90', 'tok', 7)
        trace.assert_called_once_with('tok', 7, 128, 90, 'native/80')

    def test_map_click_wins_when_the_map_fired(self) -> None:
        click = {"points": [{"customdata": [42]}]}
        store, dropdown, slider = self._with_trigger("jw-eic-map", 1, 2, click, 5)
        self.assertEqual(store, {"feature_index": 42, "source": "jw-eic-map"})
        self.assertEqual((dropdown, slider), (42, 42))

    def test_the_triggering_control_is_not_written_back(self) -> None:
        import dash
        store, dropdown, slider = self._with_trigger("jw-eic-slider", None, 9, None, 5)
        self.assertEqual(store["feature_index"], 9)
        self.assertEqual(dropdown, 9)
        self.assertIs(slider, dash.no_update)

    def test_changing_archive_clears_the_selection(self) -> None:
        store, dropdown, slider = self._with_trigger("jw-eic-archive", 3, 3, None, 9)
        self.assertIsNone(store)
        self.assertIsNone(dropdown)
        self.assertEqual(slider, 0)


if __name__ == "__main__":
    unittest.main()
