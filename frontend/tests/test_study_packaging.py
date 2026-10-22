import unittest
from types import SimpleNamespace
from unittest.mock import patch

from callbacks import study_packaging as packaging
from callbacks.navigation import page_key
from pages.study_packaging import study_packaging_layout

CHOICES = {
    "study_id": 14, "title": "Example subset", "status": "approved",
    "assays": [
        {"assay_id": 25, "assay_name": "STD-default-assay", "sources": []},
        {"assay_id": 26, "assay_name": "HILIC positive", "sources": [dict(
            job_id=158, manifest_file_id=19768, run_name="std14_assay26_job158", finished_at="2026-10-01T14:20:00Z",
            review_revision=None, engine="msdial5", engine_version="5.5", polarity="positive",
            separation_mode="HILIC", acquisition_count=10, spectral_bytes=1_500_000_000,
            quantity={"value": "height", "origin": "named by the MS-DIAL export"},
            sample_ids=list(range(112, 122)))]},
    ],
    "last_selection": {"metabolights_id": "", "omitted_assay_ids": [25],
                       "sources": [{"assay_id": 26, "job_id": 158, "manifest_file_id": 19768}]},
}


class Capture:
    def __init__(self):
        self.callbacks = {}

    def callback(self, *args, **kwargs):
        def decorator(function):
            self.callbacks[function.__name__] = function
            return function
        return decorator


def callbacks():
    app = Capture()
    packaging.register_callbacks(app)
    return app.callbacks


class StudyPackagingWorkspaceTests(unittest.TestCase):
    def test_routes_share_one_page_key_per_study(self):
        self.assertEqual(packaging.parse_route("/studies/14/packaging"), (14, "builds", None))  # the landing
        self.assertEqual(packaging.parse_route("/studies/14/packaging/new"), (14, "new", None))
        self.assertEqual(packaging.parse_route("/studies/14/packaging/new/preview"), (14, "preview", None))
        self.assertEqual(packaging.parse_route("/studies/14/packaging/builds/3"), (14, "builds", 3))
        self.assertEqual(packaging.parse_route("/jobs/14/packaging"), (None, None, None))
        keys = {page_key(path) for path in ("studies/14/packaging", "studies/14/packaging/new/preview",
                                            "studies/14/packaging/builds/2")}
        self.assertEqual(keys, {"isa-14"})
        self.assertNotEqual(page_key("studies/15/packaging"), "isa-14")
        self.assertEqual(page_key("studies/14/metadata"), "isa-14")  # the retired page redirects inside
        poll = next(c for c in study_packaging_layout(14) if getattr(c, "id", None) == "pkg-poll")
        self.assertTrue(poll.disabled)  # no polling until a build runs

    def test_selection_defaults_to_each_assays_newest_run_and_records_omissions(self):
        older = dict(CHOICES["assays"][1]["sources"][0], job_id=152, manifest_file_id=19614)
        rerun = dict(CHOICES, last_selection={"metabolights_id": "REQ20261001", "omitted_assay_ids": [25],
                                              "sources": [{"assay_id": 26, "job_id": 152, "manifest_file_id": 19614}]},
                     assays=[CHOICES["assays"][0], dict(CHOICES["assays"][1],
                                                        sources=[older, *CHOICES["assays"][1]["sources"]])])
        # The newest run, not the one the latest build used; the request id carries over.
        self.assertEqual(packaging.selection_for({}, 14, rerun),
                         {"metabolights_id": "REQ20261001", "sources": {"26": "158:19768"}})
        selection = packaging.selection_for({}, 14, CHOICES)
        self.assertEqual(selection, {"metabolights_id": "", "sources": {"26": "158:19768"}})
        self.assertEqual(packaging.selection_payload(selection, CHOICES), {
            "metabolights_id": "", "omitted_assay_ids": [25],
            "sources": [{"assay_id": 26, "job_id": 158, "manifest_file_id": 19768}]})
        saved = {"14": {"metabolights_id": "REQ1", "sources": {"26": "omit"}}}
        self.assertEqual(packaging.selection_payload(packaging.selection_for(saved, 14, CHOICES), CHOICES)["sources"], [])
        self.assertIsNotNone(packaging.request_id_problem("a study about cells"))
        self.assertIsNone(packaging.request_id_problem("MTBLS1234"))

    def test_overview_shows_one_state_per_section_and_where_to_fix_it(self):
        payload = packaging.selection_payload(packaging.selection_for({}, 14, CHOICES), CHOICES)
        result = {"issues": [
            dict(code="missing_request_id", severity="error", section="identifier", message="A real request is required."),
            dict(code="unresolved_annotation_decisions", severity="error", section="workflow", assay_id=26, message="197 await."),
            dict(code="no_principal_investigator", severity="warning", section="study", message="No PI."),
        ]}
        rows = {row[0]: row for row in packaging.overview_rows(result, 14, payload)}
        self.assertEqual(rows["protocols"][1].children, "ready")
        self.assertEqual(rows["workflow results"][1].children, "1 to fix")
        self.assertEqual(rows["workflow results"][3].href, "/jobs/158")
        self.assertEqual(rows["study and people"][1].children, "1 warning")
        self.assertEqual(rows["identifier"][3].href, "/studies/14/packaging/new")

    def test_sources_label_runs_by_name_and_build_rejects_a_description_as_request_id(self):
        label = packaging.source_label(CHOICES["assays"][1]["sources"][0])
        self.assertIn("std14_assay26_job158", label)
        self.assertIn("ms-dial 5.5", label)
        self.assertIn("1.5 GB", label)
        self.assertIn("peak height", label)
        request_build = callbacks()["request_build"]
        store = {"14": {"metabolights_id": "a description", "sources": {"26": "158:19768"}}}
        with patch("callbacks.study_packaging.call_api") as api:
            message, signal, _, _ = request_build(1, store, CHOICES, "/studies/14/packaging/new/preview", "token")
        api.assert_not_called()
        self.assertIn("REQ", str(message))
        store["14"]["metabolights_id"] = ""
        with patch("callbacks.study_packaging.call_api", return_value={"id": 11, "revision": 2}) as api:
            message, signal, poll_disabled, target = request_build(1, store, CHOICES,
                                                                   "/studies/14/packaging/new/preview", "token")
        sent = api.call_args.kwargs["payload"]
        self.assertEqual((sent["omitted_assay_ids"], len(sent["request_key"])), ([25], 36))
        self.assertEqual((signal, poll_disabled), (11, False))
        self.assertEqual(target, "/studies/14/packaging/builds/2")  # the new revision's page follows the build

    def test_builds_poll_only_while_one_is_active_and_detail_groups_findings(self):
        render = callbacks()["render_section"]
        summary = {"errors": 1, "warnings": 1, "sections": {}}
        built = dict(id=10, revision=1, status="validation_failed", created_at="2026-10-01T14:50:00Z",
                     sources=[{"assay_name": "HILIC positive", "job_id": 158}], summary=summary)
        context = SimpleNamespace(triggered_id="url")
        with patch("callbacks.study_packaging.ctx", context), \
                patch("callbacks.study_packaging.call_api", return_value=[{**built, "id": 11, "revision": 2, "status": "building"}, built]):
            view, poll_disabled, _ = render("/studies/14/packaging/builds", CHOICES, 0, {}, "token")
        self.assertFalse(poll_disabled)
        self.assertIn("r2", str(view))
        detail = {**built, "output_uri": "file:///packages/package-10", "metabolights_id": "", "sample_count": 10,
                  "report": {"issues": [
                      dict(code="unverified_area", severity="error", section="workflow", message="not area", assay_id=26),
                      dict(code="rule_i_100_300_002_01", severity="warning", section="official", message="pattern",
                           file="i_Investigation.txt")]}}
        with patch("callbacks.study_packaging.ctx", context), \
                patch("callbacks.study_packaging.call_api", side_effect=[[built], detail]):
            view, poll_disabled, _ = render("/studies/14/packaging/builds/1", CHOICES, 0, {}, "token")
        text = str(view)
        self.assertTrue(poll_disabled)
        for expected in ("fix in the workflow", "official validator", "rule_i_100_300_002_01", "pkg-export",
                         "pkg-delete", "metadata and report; no maf or spectra"):
            self.assertIn(expected, text)
        self.assertNotIn("fix in isa packaging", text)

    def test_polling_never_rerenders_the_new_package_steps(self):
        render = callbacks()["render_section"]
        with patch("callbacks.study_packaging.ctx", SimpleNamespace(triggered_id="pkg-poll")), \
                patch("callbacks.study_packaging.call_api") as api:
            for path in ("/studies/14/packaging/new", "/studies/14/packaging/new/preview", "/studies/14/packaging/sources"):
                with self.subTest(path=path), self.assertRaises(Exception):
                    render(path, CHOICES, 3, {}, "token")
        api.assert_not_called()

    def test_the_workspace_lands_on_builds_and_a_new_package_is_chosen_then_previewed(self):
        self.assertEqual([link.children for link in packaging.nav(14, "preview").children],
                         ["builds", "study", "protocols", "samples"])
        self.assertTrue(packaging.nav(14, "preview").children[0].active)  # the steps belong to builds
        empty = str(packaging.builds_view(14, []))
        self.assertIn("no builds yet", empty)
        self.assertIn("/studies/14/packaging/new", empty)
        step1 = str(packaging.new_view(14, CHOICES, packaging.selection_for({}, 14, CHOICES)))
        self.assertIn("/studies/14/packaging/new/preview", step1)
        self.assertIn("leave this assay out", step1)
        check = {"issues": [], "errors": 0, "warnings": 0, "metadata_revision": 4, "spectral": {"files": 10, "bytes": 1.5e9}}
        with patch("callbacks.study_packaging.call_api", return_value=check):
            step2 = str(packaging.preview_view("token", 14, dict(CHOICES, next_revision=3),
                                               packaging.selection_for({}, 14, CHOICES)))
        self.assertIn("build revision 3", step2)
        self.assertIn("HILIC positive: job 158 (newest) · peak height", step2)
        self.assertIn("STD-default-assay", step2)  # left out, and said so
        with patch("callbacks.study_packaging.call_api", return_value=check):
            nothing = str(packaging.preview_view("token", 14, CHOICES, {"metabolights_id": "", "sources": {}}))
        self.assertIn("choose a workflow result for at least one assay", nothing)
        self.assertIn("disabled=True", nothing)

    def test_old_overview_and_sources_links_land_on_the_new_places(self):
        redirect = callbacks()["redirect_old_route"]
        self.assertEqual(redirect("/studies/14/packaging/sources"), "/studies/14/packaging/new")
        self.assertEqual(redirect("/studies/14/metadata"), "/studies/14/packaging/study")


if __name__ == "__main__":
    unittest.main()
