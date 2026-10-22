import unittest
from types import SimpleNamespace
from unittest.mock import patch

import dash
from dash import Input

from callbacks.summary import (
    _should_reuse_summary_cache,
    _summary_refresh_failure,
    register_callbacks,
    summarize_datasets,
)
from services.page_cache import build_page_cache, page_cache_data


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


class SummaryCacheTests(unittest.TestCase):
    def test_summary_route_revisit_reuses_matching_cache(self):
        cache = build_page_cache({"summary": {"sample_count": 3}}, "token", study_id=9, assay_id=18)
        cache["revision"] = 4

        self.assertTrue(_should_reuse_summary_cache("url", cache, "token", 9, 18, 4))

    def test_summary_route_revisit_refetches_when_revision_differs(self):
        cache = build_page_cache({"summary": {"sample_count": 3}}, "token", study_id=9, assay_id=18)
        cache["revision"] = 4

        self.assertFalse(_should_reuse_summary_cache("url", cache, "token", 9, 18, 5))

    def test_summary_refresh_fetches_matching_context_again(self):
        cache = build_page_cache({"summary": {"sample_count": 3}}, "token", study_id=9, assay_id=18)
        cache["revision"] = 4

        self.assertFalse(_should_reuse_summary_cache("summary-refresh-btn", cache, "token", 9, 18, 4))

    def test_summary_failed_refresh_keeps_matching_data_with_warning(self):
        cache = build_page_cache(
            {"status": "Loaded", "summary": {"sample_count": 3}},
            "token",
            study_id=9,
            assay_id=18,
        )
        cache["revision"] = 4

        failed = _summary_refresh_failure(cache, "token", 9, 18, 4, "offline")

        data = page_cache_data(failed, {})
        self.assertEqual(data["summary"], {"sample_count": 3})
        self.assertEqual(data["status"], "Summary load failed: offline")
        self.assertEqual(failed["revision"], 4)

    def test_summary_failed_refresh_preserves_same_context_data_across_revision_and_retries(self):
        cache = build_page_cache(
            {"status": "Loaded", "summary": {"sample_count": 3}},
            "token",
            study_id=9,
            assay_id=18,
        )
        cache["revision"] = 4

        failed = _summary_refresh_failure(cache, "token", 9, 18, 5, "offline")

        self.assertEqual(page_cache_data(failed, {})["summary"], {"sample_count": 3})
        self.assertEqual(failed["revision"], 5)
        self.assertTrue(failed["refresh_failed"])
        self.assertFalse(_should_reuse_summary_cache("url", failed, "token", 9, 18, 5))

    def test_summary_failed_refresh_discards_mismatched_data(self):
        cache = build_page_cache(
            {"status": "Loaded", "summary": {"sample_count": 3}},
            "other-token",
            study_id=9,
            assay_id=18,
        )
        cache["revision"] = 4

        failed = _summary_refresh_failure(cache, "token", 9, 18, 4, "offline")

        self.assertIsNone(page_cache_data(failed, {})["summary"])
        self.assertEqual(failed["context"]["study_id"], 9)
        self.assertEqual(failed["context"]["assay_id"], 18)
        self.assertEqual(failed["revision"], 4)

    def test_summary_renderer_does_not_expose_mismatched_cache(self):
        app = _CallbackCapture()
        register_callbacks(app)
        renderer = app.callbacks[1]
        cache = build_page_cache(
            {"status": "Loaded prior context", "summary": {"sample_count": 3}},
            "other-token",
            study_id=9,
            assay_id=18,
        )
        cache["revision"] = 4

        with patch("callbacks.summary.resolve_page_slug", return_value=("summary", "/summary")):
            rendered = renderer(cache, "summary", "token", 9, 18, 4)

        self.assertEqual(rendered[0], "Click refresh to load summary.")

    def test_summary_renderer_hides_matching_context_cache_with_stale_revision(self):
        app = _CallbackCapture()
        register_callbacks(app)
        renderer = app.callbacks[1]
        cache = build_page_cache(
            {"status": "Loaded prior revision", "summary": {"sample_count": 3}},
            "token",
            study_id=9,
            assay_id=18,
        )
        cache["revision"] = 4

        with patch("callbacks.summary.resolve_page_slug", return_value=("summary", "/summary")):
            rendered = renderer(cache, "summary", "token", 9, 18, 5)

        self.assertEqual(rendered[0], "Click refresh to load summary.")

    def test_summary_renderer_unwraps_matching_cache(self):
        app = _CallbackCapture()
        register_callbacks(app)
        renderer = app.callbacks[1]
        cache = build_page_cache(
            {"status": "Cached summary", "summary": None},
            "token",
            study_id=9,
            assay_id=18,
        )
        cache["revision"] = 4

        with patch("callbacks.summary.resolve_page_slug", return_value=("summary", "/summary")):
            rendered = renderer(cache, "summary", "token", 9, 18, 4)

        self.assertEqual(rendered[0], "Cached summary")

    def test_summary_renderer_reacts_to_context_changes(self):
        app = _CallbackCapture()
        register_callbacks(app)

        renderer_input_ids = {
            dependency.component_id
            for dependency in app.dependencies[1]
            if isinstance(dependency, Input)
        }

        self.assertIn("token-store", renderer_input_ids)
        self.assertIn("study-select-store", renderer_input_ids)
        self.assertIn("assay-select-store", renderer_input_ids)
        self.assertIn("page-data-revision-store", renderer_input_ids)

    def test_summary_fetch_reacts_to_auth_changes(self):
        app = _CallbackCapture()
        register_callbacks(app)

        fetch_input_ids = {
            dependency.component_id
            for dependency in app.dependencies[0]
            if isinstance(dependency, Input)
        }

        self.assertIn("token-store", fetch_input_ids)
        self.assertIn("page-data-revision-store", fetch_input_ids)

    def test_summarize_datasets_keeps_one_row_per_dataset(self):
        files = [
            {"id": 3, "dataset_id": 7, "dataset_name": "QC pool", "dataset_kind": "raw", "dataset_type": "mzML",
             "dataset_sample_ids": [1], "dataset_sample_names": ["QC1"], "storage_uri": "file:///a"},
            {"id": 4, "dataset_id": 2, "dataset_name": "Batch A", "dataset_kind": "raw", "dataset_type": "mzML"},
            {"id": 5, "dataset_id": 7, "dataset_name": "QC pool", "storage_uri": "file:///b"},
            {"id": 6, "dataset_id": None},
        ]

        rows = summarize_datasets(files)

        self.assertEqual([row["dataset_id"] for row in rows], [2, 7])
        self.assertEqual([row["file_count"] for row in rows], [1, 2])
        self.assertEqual(rows[1]["dataset_sample_names"], ["QC1"])
        self.assertNotIn("storage_uri", rows[1])

    def test_summary_fetch_stores_dataset_rows_not_file_records(self):
        app = _CallbackCapture()
        register_callbacks(app)
        fetch = app.callbacks[0]
        files = [{"id": n, "dataset_id": 7, "dataset_name": "QC pool", "storage_uri": f"file:///{n}"} for n in range(5)]

        with patch("callbacks.summary.resolve_page_slug", return_value=("summary", "/summary")), \
                patch("callbacks.summary.dash.ctx", SimpleNamespace(triggered_id="url")), \
                patch("callbacks.summary.summary_service") as summary_service, \
                patch("callbacks.summary.study_service") as study_service, \
                patch("callbacks.summary.file_service") as file_service:
            summary_service.get_study_summary.return_value = {"sample_count": 3}
            summary_service.get_study_structure.return_value = {}
            summary_service.list_study_activity.return_value = []
            study_service.list_study_assays.return_value = []
            file_service.list_assay_files.return_value = files
            stored = fetch(None, "/summary", 9, 18, "token", 4, {})

        data = page_cache_data(stored, {})
        self.assertNotIn("files", data)
        self.assertEqual(data["datasets"], summarize_datasets(files))

    def test_summary_store_written_before_datasets_is_not_reused(self):
        cache = build_page_cache({"summary": {"sample_count": 3}, "files": []}, "token", study_id=9, assay_id=18)
        cache["revision"] = 4

        self.assertFalse(_should_reuse_summary_cache("url", cache, "token", 9, 18, 4))

    def test_summary_renderer_draws_dataset_table_from_stored_rows(self):
        app = _CallbackCapture()
        register_callbacks(app)
        renderer = app.callbacks[1]
        rows = summarize_datasets([{"dataset_id": 7, "dataset_name": "QC pool"}] * 3)
        cache = build_page_cache(
            {"status": "", "summary": {"study": {"id": 9}}, "datasets": rows},
            "token",
            study_id=9,
            assay_id=18,
        )
        cache["revision"] = 4

        with patch("callbacks.summary.resolve_page_slug", return_value=("summary", "/summary")), \
                patch("callbacks.summary.build_study_structure_figure", return_value={}):
            rendered = renderer(cache, "summary", "token", 9, 18, 4)

        self.assertIn("QC pool", repr(rendered[4]))

    def test_summary_callbacks_do_not_fetch_on_hidden_pages(self):
        app = _CallbackCapture()
        register_callbacks(app)

        fetch = app.callbacks[0]

        with patch("callbacks.summary.resolve_page_slug", return_value=("dashboard", "/dashboard")):
            self.assertIs(fetch(None, "dashboard", 9, 18, "token", 4, {}), dash.no_update)


if __name__ == "__main__":
    unittest.main()
