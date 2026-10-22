import unittest
from unittest.mock import patch

import dash
from dash import Input

from callbacks.dashboard import (
    _should_reuse_dashboard_cache,
    build_last_context_record,
    describe_resume_context,
    register_callbacks,
    sorted_recent_studies,
)
from services.page_cache import build_page_cache, page_cache_matches
from ui.formatting import resolve_page_slug


class _CallbackCapture:
    def __init__(self) -> None:
        self.dependencies = []
        self.callbacks = []

    def callback(self, *dependencies, **_kwargs):
        self.dependencies.append(dependencies)

        def decorator(callback):
            self.callbacks.append(callback)
            return callback

        return decorator


class DashboardHelperTests(unittest.TestCase):
    def test_dashboard_route_revisit_reuses_matching_cache(self) -> None:
        cache = build_page_cache({"studies": [{"id": 9}]}, "token", study_id=9, assay_id=18)
        cache["revision"] = 4

        self.assertTrue(_should_reuse_dashboard_cache("url", cache, "token", 9, 18, 4))

    def test_dashboard_refresh_bypasses_matching_cache(self) -> None:
        cache = build_page_cache({"studies": [{"id": 9}]}, "token", study_id=9, assay_id=18)
        cache["revision"] = 4

        self.assertFalse(_should_reuse_dashboard_cache("dashboard-refresh-btn", cache, "token", 9, 18, 4))

    def test_dashboard_route_revisit_rejects_different_authentication(self) -> None:
        cache = build_page_cache({"studies": [{"id": 9}]}, "other-token", study_id=9, assay_id=18)
        cache["revision"] = 4

        self.assertFalse(_should_reuse_dashboard_cache("url", cache, "token", 9, 18, 4))

    def test_dashboard_route_revisit_rejects_stale_revision(self) -> None:
        cache = build_page_cache({"studies": [{"id": 9}]}, "token", study_id=9, assay_id=18)
        cache["revision"] = 4

        self.assertFalse(_should_reuse_dashboard_cache("url", cache, "token", 9, 18, 5))

    def test_dashboard_fetch_records_revision_for_all_result_paths(self) -> None:
        app = _CallbackCapture()
        register_callbacks(app)
        fetch = app.callbacks[1]

        with patch("callbacks.dashboard.resolve_page_slug", return_value=("dashboard", "/dashboard")), patch(
            "callbacks.dashboard.dash.ctx"
        ) as callback_context:
            callback_context.triggered_id = "token-store"
            unauthenticated = fetch("/dashboard", None, 9, 18, None, 4, {})

        self.assertEqual(unauthenticated["revision"], 4)
        self.assertEqual(unauthenticated["context"]["study_id"], 9)
        self.assertEqual(unauthenticated["context"]["assay_id"], 18)

        with patch("callbacks.dashboard.resolve_page_slug", return_value=("dashboard", "/dashboard")), patch(
            "callbacks.dashboard.dash.ctx"
        ) as callback_context, patch("callbacks.dashboard.study_service.list_studies", return_value=[{"id": 9}]), patch(
            "callbacks.dashboard.summary_service.get_study_summary", return_value={}
        ), patch("callbacks.dashboard.summary_service.list_study_assays", return_value=[]), patch(
            "callbacks.dashboard.summary_service.list_study_activity", return_value=[]
        ):
            callback_context.triggered_id = "token-store"
            successful = fetch("/dashboard", None, 99, 18, "token", 5, {})

        self.assertEqual(successful["revision"], 5)
        self.assertEqual(successful["context"]["study_id"], 99)
        self.assertEqual(successful["context"]["assay_id"], 18)
        self.assertIsNone(successful["data"]["study_id"])
        self.assertIsNone(successful["data"]["assay_id"])

        with patch("callbacks.dashboard.resolve_page_slug", return_value=("dashboard", "/dashboard")), patch(
            "callbacks.dashboard.dash.ctx"
        ) as callback_context, patch("callbacks.dashboard.study_service.list_studies", side_effect=ValueError("offline")):
            callback_context.triggered_id = "token-store"
            failed = fetch("/dashboard", None, 9, 18, "token", 6, {})

        self.assertEqual(failed["revision"], 6)
        self.assertEqual(failed["context"]["study_id"], 9)
        self.assertEqual(failed["context"]["assay_id"], 18)

    def test_dashboard_failed_refresh_preserves_same_context_data_across_revision(self) -> None:
        app = _CallbackCapture()
        register_callbacks(app)
        fetch = app.callbacks[1]
        cache = build_page_cache(
            {"status": "Loaded", "studies": [{"id": 9, "title": "Pilot"}]},
            "token",
            study_id=9,
            assay_id=18,
        )
        cache["revision"] = 4

        with patch("callbacks.dashboard.resolve_page_slug", return_value=("dashboard", "/dashboard")), patch(
            "callbacks.dashboard.dash.ctx"
        ) as callback_context, patch("callbacks.dashboard.study_service.list_studies", side_effect=ValueError("offline")):
            callback_context.triggered_id = "page-data-revision-store"
            failed = fetch("/dashboard", None, 9, 18, "token", 5, cache)

        self.assertEqual(failed["data"]["studies"], [{"id": 9, "title": "Pilot"}])
        self.assertEqual(failed["revision"], 5)
        self.assertTrue(failed["refresh_failed"])
        self.assertFalse(_should_reuse_dashboard_cache("url", failed, "token", 9, 18, 5))

    def test_dashboard_callbacks_react_to_cache_context_changes(self) -> None:
        app = _CallbackCapture()
        register_callbacks(app)

        fetch_input_ids = {
            dependency.component_id
            for dependency in app.dependencies[1]
            if isinstance(dependency, Input)
        }
        renderer_input_ids = {
            dependency.component_id
            for dependency in app.dependencies[2]
            if isinstance(dependency, Input)
        }

        for component_id in {"token-store", "page-data-revision-store"}:
            self.assertIn(component_id, fetch_input_ids)
        for component_id in {"token-store", "study-select-store", "assay-select-store", "page-data-revision-store"}:
            self.assertIn(component_id, renderer_input_ids)

    def test_dashboard_fetch_does_not_run_while_hidden(self) -> None:
        app = _CallbackCapture()
        register_callbacks(app)
        fetch = app.callbacks[1]

        with patch("callbacks.dashboard.resolve_page_slug", return_value=("summary", "/summary")):
            result = fetch("/summary", None, 9, 18, "token", 4, {})

        self.assertIs(result, dash.no_update)

    def test_dashboard_renderer_hides_mismatched_cache(self) -> None:
        app = _CallbackCapture()
        register_callbacks(app)
        renderer = app.callbacks[2]
        cache = build_page_cache({"status": "Prior dashboard"}, "token", study_id=9, assay_id=18)
        cache["revision"] = 4

        with patch("callbacks.dashboard.resolve_page_slug", return_value=("dashboard", "/dashboard")):
            rendered = renderer(cache, {}, "token", 9, 18, 5, "/dashboard")

        self.assertEqual(rendered[0], "Refresh the dashboard to load data.")

    def test_persist_last_context_unwraps_dashboard_cache(self) -> None:
        app = _CallbackCapture()
        register_callbacks(app)
        persist = app.callbacks[0]
        cache = build_page_cache(
            {
                "studies": [{"id": 7, "title": "Facility Pilot"}],
                "assays": [{"id": 11, "name": "LC-MS negative"}],
            },
            "token",
            study_id=7,
            assay_id=11,
        )

        record = persist(7, 11, cache, [], [], {})

        self.assertEqual(record["study_title"], "Facility Pilot")
        self.assertEqual(record["assay_name"], "LC-MS negative")

    def test_resume_last_context_unwraps_dashboard_cache(self) -> None:
        app = _CallbackCapture()
        register_callbacks(app)
        resume = next(cb for cb in app.callbacks if cb.__name__ == "resume_last_context")
        cache = build_page_cache({"studies": [{"id": 7, "title": "Facility Pilot"}]}, "token", study_id=7)

        study_id, pending, *_assay_cache = resume(1, {"study_id": 7, "assay_id": 11}, cache)

        self.assertEqual(study_id, 7)
        self.assertEqual(pending, {"study_id": 7, "assay_id": 11})

    def test_root_route_redirects_to_dashboard(self) -> None:
        dash.Dash(__name__)
        self.assertEqual(resolve_page_slug(None), ("dashboard", "/dashboard"))
        self.assertEqual(resolve_page_slug(""), ("dashboard", "/dashboard"))
        self.assertEqual(resolve_page_slug("/"), ("dashboard", "/dashboard"))

    def test_recent_studies_are_sorted_by_latest_update(self) -> None:
        studies = [
            {"id": 1, "title": "Older", "updated_at": "2026-05-04T08:00:00+00:00"},
            {"id": 2, "title": "Newest", "updated_at": "2026-05-05T08:00:00+00:00"},
            {"id": 3, "title": "Middle", "updated_at": "2026-05-04T20:00:00+00:00"},
        ]

        ordered = sorted_recent_studies(studies, limit=2)

        self.assertEqual([study["id"] for study in ordered], [2, 3])

    def test_resume_context_requires_accessible_study(self) -> None:
        copy, disabled, status = describe_resume_context(
            token="token",
            last_context={"study_id": 5, "assay_id": 9, "study_title": "Pilot", "assay_name": "LC-MS POS"},
            studies=[{"id": 3, "title": "Other"}],
        )

        self.assertTrue(disabled)
        self.assertIn("not available", copy)
        self.assertEqual(status, "Saved context unavailable.")

    def test_build_last_context_record_uses_matching_labels(self) -> None:
        record = build_last_context_record(
            study_id=7,
            assay_id=11,
            studies_data=[{"id": 7, "title": "Facility Pilot"}],
            assays_data=[{"id": 11, "name": "LC-MS negative"}],
            previous_store={},
        )

        self.assertEqual(
            record,
            {
                "study_id": 7,
                "assay_id": 11,
                "study_title": "Facility Pilot",
                "assay_name": "LC-MS negative",
            },
        )


if __name__ == "__main__":
    unittest.main()


class WorkflowStatusLinkTests(unittest.TestCase):
    def test_each_job_row_opens_its_job_page(self):
        from callbacks.dashboard import dashboard_workflow_rows
        rows = dashboard_workflow_rows([{"id": 151, "status": "succeeded"}, {"id": 150, "status": "failed"}])
        self.assertEqual([row[0].children.href for row in rows], ["/jobs/151", "/jobs/150"])
        self.assertEqual(rows[0][0].children.children, "Open")

    def test_job_summaries_keep_only_the_shown_jobs_and_columns(self):
        from callbacks.dashboard import dashboard_job_summaries
        jobs = [
            {"id": 160 - n, "status": "failed", "created_at": "t0", "started_at": "t1", "finished_at": "t2",
             "error_message": "x" * 500, "input_manifest_json": {"files": ["a"] * 50}, "status_payload_json": {}}
            for n in range(8)
        ]

        summaries = dashboard_job_summaries(jobs)

        self.assertEqual([job["id"] for job in summaries], [160, 159, 158, 157, 156])
        self.assertEqual(set(summaries[0]), {"id", "status", "created_at", "started_at", "finished_at", "error_message"})
        self.assertLessEqual(len(summaries[0]["error_message"]), 72)
        self.assertIsNone(dashboard_job_summaries([{"id": 1, "error_message": ""}])[0]["error_message"])

    def test_dashboard_fetch_stores_job_summaries(self):
        app = _CallbackCapture()
        register_callbacks(app)
        fetch = app.callbacks[1]
        jobs = [{"id": n, "status": "succeeded", "input_manifest_json": {"files": ["a"]}} for n in range(9, 0, -1)]

        with patch("callbacks.dashboard.resolve_page_slug", return_value=("dashboard", "/dashboard")), patch(
            "callbacks.dashboard.dash.ctx"
        ) as callback_context, patch("callbacks.dashboard.study_service.list_studies", return_value=[{"id": 9}]), patch(
            "callbacks.dashboard.summary_service.get_study_summary", return_value={}
        ), patch("callbacks.dashboard.summary_service.list_study_assays", return_value=[{"id": 18}]), patch(
            "callbacks.dashboard.summary_service.list_study_activity", return_value=[]
        ), patch("callbacks.dashboard.workflow_service.list_workflow_jobs", return_value=jobs):
            callback_context.triggered_id = "token-store"
            stored = fetch("/dashboard", None, 9, 18, "token", 5, {})

        stored_jobs = stored["data"]["workflow_jobs"]
        self.assertEqual([job["id"] for job in stored_jobs], [9, 8, 7, 6, 5])
        self.assertNotIn("input_manifest_json", stored_jobs[0])


class DashboardAssayTests(unittest.TestCase):
    def test_assays_of_the_open_study_can_be_opened(self):
        from callbacks.dashboard import dashboard_assay_rows
        rows = dashboard_assay_rows([{"id": 17, "name": "default"}, {"id": 18, "name": "second_test"}], 18)
        buttons = [row[0].children for row in rows]
        self.assertEqual([b.id["index"] for b in buttons], [17, 18])
        self.assertEqual([b.children for b in buttons], ["Open", "Selected"])
        self.assertTrue(buttons[1].disabled)

    def test_opening_an_assay_needs_a_real_click(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        import dash
        app = _CallbackCapture()
        register_callbacks(app)
        open_assay = next(cb for cb in app.callbacks if cb.__name__ == "open_assay")
        for clicks, expected in ((0, dash.no_update), (1, 19)):
            ctx = SimpleNamespace(triggered_id={"type": "dashboard-assay-open-btn", "index": 19},
                                  triggered=[{"value": clicks}])
            with patch("callbacks.clicks.dash.ctx", ctx), patch("callbacks.dashboard.dash.ctx", ctx):
                self.assertEqual(open_assay([clicks]), expected)


class OpenStudyLoadsAssaysTests(unittest.TestCase):
    def test_opening_a_study_loads_its_assays_into_the_shared_cache(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        app = _CallbackCapture()
        register_callbacks(app)
        open_study = next(cb for cb in app.callbacks if cb.__name__ == "open_recent_study")
        ctx = SimpleNamespace(triggered_id={"type": "dashboard-study-open-btn", "index": 13},
                              triggered=[{"value": 1}])
        assays = [{"id": 30, "study_id": 13}]
        with patch("callbacks.clicks.dash.ctx", ctx), patch("callbacks.dashboard.dash.ctx", ctx), \
                patch("callbacks.dashboard.study_service.list_study_assays", return_value=assays) as fetch:
            study_id, pending, rows, context = open_study([1], "token")
        fetch.assert_called_once_with("token", 13)
        self.assertEqual((study_id, pending, rows), (13, {}, assays))
        self.assertTrue(page_cache_matches(context, "token", study_id=13))
