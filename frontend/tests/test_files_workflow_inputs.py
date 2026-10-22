import unittest
from types import SimpleNamespace
from unittest.mock import patch

from callbacks.files_workflow_inputs import (
    register_callbacks,
    selected_files_and_summary,
    workflow_file_selection_store,
)
from pages.shell import app_layout
from ui.rows import worker_input_file_rows


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
        self.callbacks = []

    def callback(self, *_dependencies, **_kwargs):
        def decorator(callback):
            self.callbacks.append(callback)
            return callback

        return decorator


class FilesWorkflowInputsTests(unittest.TestCase):
    def test_callback_requests_only_eligible_workflow_input_files(self) -> None:
        app = _CallbackCapture()
        register_callbacks(app)
        eligible = {
            "id": 11,
            "original_filename": "sample.raw",
            "file_role": "thermo_raw",
            "dataset_kind": "raw",
            "status": "uploaded",
            "sample_id": 101,
            "file_sample_name": "Sample 1",
            "dataset_sample_ids": [101],
            "dataset_sample_names": ["Sample 1"],
        }
        all_assay_files = [
            eligible,
            {
                "id": 12,
                "original_filename": "report.json",
                "file_role": "workflow_report",
                "dataset_kind": "derived",
                "status": "validated",
                "sample_id": None,
                "file_sample_name": None,
                "dataset_sample_ids": [],
                "dataset_sample_names": [],
            },
            {
                "id": 13,
                "original_filename": "failed.raw",
                "file_role": "thermo_raw",
                "dataset_kind": "raw",
                "status": "failed",
                "sample_id": 103,
                "file_sample_name": "Sample 3",
                "dataset_sample_ids": [103],
                "dataset_sample_names": ["Sample 3"],
            },
        ]

        def list_assay_files(_token, _assay_id, *, workflow_inputs_only=False):
            return [eligible] if workflow_inputs_only else all_assay_files

        with (
            patch("callbacks.files_workflow_inputs.resolve_page_slug", return_value=("files", None)),
            patch("callbacks.files_workflow_inputs.dash.ctx", SimpleNamespace(triggered_id="study-select-store")),
            patch("callbacks.files_workflow_inputs.file_service.list_assay_files", side_effect=list_assay_files),
            patch("callbacks.files_workflow_inputs.workflow_service.list_workflow_output_files", return_value=[]),
        ):
            options, _, _, summary, store = app.callbacks[0](
                "/files", 9, 18, 0, 0, "all", None, "token", {}
            )

        self.assertEqual([option["value"] for option in options], [11])
        self.assertEqual([row["id"] for row in store["available_files"]], [11])
        self.assertEqual(summary, "All eligible files selected for workers (1 file(s)).")

    def test_callback_filters_ineligible_files_from_existing_session_cache(self) -> None:
        app = _CallbackCapture()
        register_callbacks(app)
        stored_selection = {
            "study_id": 9,
            "assay_id": 18,
            "mode": "all",
            "selected_dropdown_ids": [],
            "available_files": [
                {
                    "id": 11,
                    "original_filename": "sample.raw",
                    "file_role": "thermo_raw",
                    "dataset_kind": "raw",
                    "status": "registered",
                    "sample_id": 101,
                },
                {
                    "id": 12,
                    "original_filename": "report.json",
                    "file_role": "workflow_report",
                    "dataset_kind": "derived",
                    "status": "validated",
                },
                {
                    "id": 13,
                    "original_filename": "unlinked.raw",
                    "file_role": "thermo_raw",
                    "dataset_kind": "raw",
                    "status": "uploaded",
                    "sample_id": None,
                },
            ],
            "workflow_output_file_ids": [],
        }

        with (
            patch("callbacks.files_workflow_inputs.resolve_page_slug", return_value=("files", None)),
            patch("callbacks.files_workflow_inputs.dash.ctx", SimpleNamespace(triggered_id="url")),
        ):
            options, _, _, summary, store = app.callbacks[0](
                "/files", 9, 18, 0, 0, "all", None, "token", stored_selection
            )

        self.assertEqual([option["value"] for option in options], [11])
        self.assertEqual([row["id"] for row in store["available_files"]], [11])
        self.assertEqual(summary, "All eligible files selected for workers (1 file(s)).")

    def test_all_mode_uses_symbolic_selection(self) -> None:
        selected_file_ids, summary = selected_files_and_summary("all", [], [11, 12, 13])

        self.assertIsNone(selected_file_ids)
        self.assertEqual(summary, "All eligible files selected for workers (3 file(s)).")

    def test_all_mode_store_does_not_expand_file_ids(self) -> None:
        store = workflow_file_selection_store(
            study_id=7,
            assay_id=9,
            mode="all",
            selected_file_ids=None,
            selected_dropdown_ids=[],
            input_files=[{"id": 11}, {"id": 12}, {"id": 13}],
            workflow_output_ids=set(),
        )

        self.assertEqual(store["mode"], "all")
        self.assertNotIn("selected_file_ids", store)
        self.assertNotIn("available_file_ids", store)

    def test_symbolic_all_selection_marks_every_displayed_file_selected(self) -> None:
        rows = worker_input_file_rows(
            [
                {"id": 11, "original_filename": "a.raw"},
                {"id": 12, "original_filename": "b.raw"},
            ],
            selected_ids=None,
        )

        self.assertEqual([row[-1].children for row in rows], ["Selected", "Selected"])

    def test_initial_all_selection_store_contains_no_file_id_arrays(self) -> None:
        selection_store = next(
            component
            for component in _walk(app_layout())
            if getattr(component, "id", None) == "workflow-file-selection-store"
        )

        self.assertEqual(
            selection_store.data,
            {"study_id": None, "assay_id": None, "mode": "all"},
        )


if __name__ == "__main__":
    unittest.main()
