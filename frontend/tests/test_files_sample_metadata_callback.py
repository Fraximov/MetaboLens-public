import unittest
from unittest.mock import patch

from dash import Input, Output

import callbacks.files_sample_metadata as sample_metadata_callbacks

from callbacks.files_sample_metadata import (
    cache_samples_after_fetch_failure,
    _should_reuse_samples_cache,
    clicked_delete_sample_id,
    register_callbacks,
    render_cached_samples_table,
    render_editable_sample_metadata_table,
)
from services.page_cache import build_page_cache, page_cache_data
from pages.files import files_page_layout


def _walk(component):
    if isinstance(component, (list, tuple)):
        for child in component:
            yield from _walk(child)
        return
    yield component
    children = getattr(component, "children", None)
    if children is not None:
        yield from _walk(children)


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


class FilesSampleMetadataCallbackTests(unittest.TestCase):
    def test_files_failed_fetch_preserves_matching_cache_with_warning(self) -> None:
        cache = build_page_cache([{"id": 61, "name": "QC_03"}], "token", study_id=9)

        recovered = cache_samples_after_fetch_failure(cache, "token", 9, ValueError("offline"))

        self.assertEqual(page_cache_data(recovered), [{"id": 61, "name": "QC_03"}])
        self.assertEqual(recovered["warning"], "Study samples refresh failed: offline")
        self.assertNotIn("error", recovered)

    def test_files_failed_fetch_ignores_mismatched_cache(self) -> None:
        cache = build_page_cache([{"id": 61, "name": "QC_03"}], "other-token", study_id=9)

        recovered = cache_samples_after_fetch_failure(cache, "token", 9, ValueError("offline"))

        self.assertEqual(page_cache_data(recovered), [])
        self.assertEqual(recovered["error"], "Study samples load failed: offline")

    def test_files_route_revisit_reuses_matching_samples_cache(self) -> None:
        cache = build_page_cache([{"id": 61, "name": "QC_03"}], "token", study_id=9)

        self.assertTrue(_should_reuse_samples_cache("url", cache, "token", 9))

    def test_files_upload_signal_forces_sample_reload(self) -> None:
        cache = build_page_cache([{"id": 61, "name": "QC_03"}], "token", study_id=9)

        self.assertFalse(_should_reuse_samples_cache("sample-upload-refresh-signal", cache, "token", 9))

    def test_files_sample_renderer_uses_cached_rows(self) -> None:
        cache = build_page_cache([{"id": 61, "name": "QC_03", "sample_type": "qc"}], "token", study_id=9)
        rows = page_cache_data(cache, [])

        rendered = render_editable_sample_metadata_table(rows)

        self.assertIsNotNone(rendered)

    def test_sample_metadata_table_renders_only_first_page(self) -> None:
        samples = [
            {"id": sample_id, "name": f"Sample {sample_id}", "sample_type": "sample"}
            for sample_id in range(1, 61)
        ]

        rendered = render_editable_sample_metadata_table(samples)
        body_rows = rendered.children.children[1].children

        self.assertEqual(len(body_rows), 25)
        self.assertEqual(body_rows[0].children[0].children, 1)
        self.assertEqual(body_rows[-1].children[0].children, 25)

    def test_sample_metadata_table_renders_requested_page(self) -> None:
        samples = [
            {"id": sample_id, "name": f"Sample {sample_id}", "sample_type": "sample"}
            for sample_id in range(1, 61)
        ]

        rendered = render_editable_sample_metadata_table(samples, page=3)
        body_rows = rendered.children.children[1].children

        self.assertEqual(len(body_rows), 10)
        self.assertEqual(body_rows[0].children[0].children, 51)
        self.assertEqual(body_rows[-1].children[0].children, 60)

    def test_sample_metadata_page_summary_describes_visible_range(self) -> None:
        summary = sample_metadata_callbacks.sample_metadata_page_summary(60, page=2)

        self.assertEqual(summary, "Showing samples 26–50 of 60. Save this page before changing pages.")

    def test_files_page_exposes_sample_metadata_pagination_controls(self) -> None:
        by_id = {
            component.id: component
            for component in _walk(files_page_layout())
            if getattr(component, "id", None)
        }

        self.assertIn("sample-metadata-pagination", by_id)
        self.assertIn("sample-metadata-page-summary", by_id)
        self.assertEqual(by_id["sample-metadata-save-btn"].children, "Save Visible Page Metadata")

    def test_files_render_does_not_expose_mismatched_cache_rows(self) -> None:
        cache = build_page_cache([{"id": 61, "name": "prior-user-sample"}], "other-token", study_id=9)

        rendered = render_cached_samples_table(cache, "token", 9)

        self.assertEqual(rendered.children, "Study samples are loading.")

    def test_files_renderer_reacts_to_auth_and_study_changes(self) -> None:
        app = _CallbackCapture()
        register_callbacks(app)

        renderer_input_ids = {
            dependency.component_id
            for dependency in app.dependencies[1]
            if isinstance(dependency, Input)
        }

        self.assertIn("token-store", renderer_input_ids)
        self.assertIn("study-select-store", renderer_input_ids)
        self.assertIn("sample-metadata-pagination", renderer_input_ids)

        renderer_output_ids = {
            dependency.component_id
            for dependency in app.dependencies[1]
            if isinstance(dependency, Output)
        }
        self.assertIn("sample-metadata-page-summary", renderer_output_ids)
        self.assertIn("sample-metadata-pagination", renderer_output_ids)

    def test_files_renderer_clamps_pagination_widget_to_available_pages(self) -> None:
        app = _CallbackCapture()
        register_callbacks(app)
        cache = build_page_cache(
            [
                {"id": sample_id, "name": f"Sample {sample_id}", "sample_type": "sample"}
                for sample_id in range(1, 31)
            ],
            "token",
            study_id=9,
        )

        with patch(
            "callbacks.files_sample_metadata.resolve_page_slug",
            return_value=("files", None),
        ):
            result = app.callbacks[1](cache, "/files", "token", 9, 4)

        self.assertEqual(len(result), 5)
        self.assertEqual(result[-1], 2)

    def test_clicked_delete_sample_id_ignores_zero_click_table_render(self) -> None:
        sample_id = clicked_delete_sample_id(
            {"type": "sample-delete-btn", "sample_id": 10},
            [{"type": "sample-delete-btn", "sample_id": 10}],
            [0],
        )

        self.assertIsNone(sample_id)

    def test_clicked_delete_sample_id_returns_clicked_sample(self) -> None:
        sample_id = clicked_delete_sample_id(
            {"type": "sample-delete-btn", "sample_id": 12},
            [
                {"type": "sample-delete-btn", "sample_id": 10},
                {"type": "sample-delete-btn", "sample_id": 12},
            ],
            [0, 1],
        )

        self.assertEqual(sample_id, 12)


if __name__ == "__main__":
    unittest.main()
