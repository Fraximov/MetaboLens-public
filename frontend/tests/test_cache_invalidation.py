import unittest

import dash_bootstrap_components as dbc
from dash import Input, Output

from callbacks.cache_invalidation import next_page_data_revision, register_callbacks, workflow_jobs_revision
from callbacks.metadata import register_callbacks as register_metadata_callbacks
from callbacks.studies import register_callbacks as register_studies_callbacks
from pages.shell import app_layout
from services.page_cache import build_page_cache


class _CallbackCapture:
    def __init__(self) -> None:
        self.dependencies = []
        self.options = []

    def callback(self, *dependencies, **options):
        self.dependencies.append(dependencies)
        self.options.append(options)

        def decorator(callback):
            return callback

        return decorator


def _walk(component):
    if isinstance(component, (list, tuple)):
        for child in component:
            yield from _walk(child)
        return
    yield component
    children = getattr(component, "children", None)
    if children is None:
        return
    yield from _walk(children)


class CacheInvalidationTests(unittest.TestCase):
    def test_session_controls_use_banner_without_repeating_selected_context(self):
        layout = app_layout()
        navbar = next(component for component in _walk(layout) if isinstance(component, dbc.Navbar))
        main = next(component for component in _walk(layout) if getattr(component, "id", None) == "shell-main-column")
        banner_ids = {getattr(component, "id", None) for component in _walk(navbar)}
        main_ids = {getattr(component, "id", None) for component in _walk(main)}

        self.assertTrue({"login-email", "login-password", "login-btn", "login-status"} <= banner_ids)
        self.assertFalse(banner_ids & main_ids)
        self.assertNotIn("study-current-selection", {getattr(component, "id", None) for component in _walk(layout)})
        self.assertIn("sidebar-selection-reminder", {getattr(component, "id", None) for component in _walk(layout)})

    def test_next_page_data_revision_is_monotonic(self):
        self.assertEqual(next_page_data_revision(None), 1)
        self.assertEqual(next_page_data_revision(0), 1)
        self.assertEqual(next_page_data_revision(7), 8)

    def test_mutation_signals_register_revision_callbacks(self):
        app = _CallbackCapture()
        register_callbacks(app)

        primary_dependencies = next(
            dependencies
            for dependencies in app.dependencies
            if any(
                isinstance(dependency, Input)
                and dependency.component_id == "sample-upload-refresh-signal"
                for dependency in dependencies
            )
        )
        primary_input_ids = {
            dependency.component_id
            for dependency in primary_dependencies
            if isinstance(dependency, Input)
        }
        workflow_dependencies = next(
            dependencies
            for dependencies in app.dependencies
            if any(
                isinstance(dependency, Input) and dependency.component_id == "jw-jobs-store"
                for dependency in dependencies
            )
        )
        workflow_output = next(
            dependency for dependency in workflow_dependencies if isinstance(dependency, Output)
        )

        self.assertEqual(
            primary_input_ids,
            {
                "sample-upload-refresh-signal",
                "study-transition-refresh-signal",
                "page-data-mutation-signal",
                "workflow-mutation-signal",
            },
        )
        self.assertEqual(workflow_output.component_id, "page-data-revision-store")
        self.assertTrue(workflow_output.allow_duplicate)
        self.assertTrue(all(options.get("prevent_initial_call") for options in app.options))

    def test_workflow_jobs_revision_ignores_cache_reload_time_but_tracks_status(self):
        first = build_page_cache(
            [{"id": 110, "status": "running", "updated_at": "2026-07-13T10:00:00Z"}],
            "token",
            study_id=9,
            assay_id=18,
            loaded_at=1,
        )
        reloaded = build_page_cache(
            [{"id": 110, "status": "running", "updated_at": "2026-07-13T10:00:00Z"}],
            "token",
            study_id=9,
            assay_id=18,
            loaded_at=2,
        )
        finished = build_page_cache(
            [{"id": 110, "status": "completed", "updated_at": "2026-07-13T10:05:00Z"}],
            "token",
            study_id=9,
            assay_id=18,
            loaded_at=3,
        )

        self.assertEqual(workflow_jobs_revision(first), workflow_jobs_revision(reloaded))
        self.assertNotEqual(workflow_jobs_revision(first), workflow_jobs_revision(finished))

    def test_study_creation_and_metadata_submit_emit_mutation_signal(self):
        studies_app = _CallbackCapture()
        metadata_app = _CallbackCapture()
        register_studies_callbacks(studies_app)
        register_metadata_callbacks(metadata_app)

        study_mutation_callbacks = [
            dependencies
            for dependencies in studies_app.dependencies
            if any(
                isinstance(dependency, Output)
                and dependency.component_id == "page-data-mutation-signal"
                for dependency in dependencies
            )
        ]
        metadata_mutation_callbacks = [
            dependencies
            for dependencies in metadata_app.dependencies
            if any(
                isinstance(dependency, Output)
                and dependency.component_id == "page-data-mutation-signal"
                for dependency in dependencies
            )
        ]

        self.assertGreaterEqual(len(study_mutation_callbacks), 2)
        self.assertEqual(len(metadata_mutation_callbacks), 1)

    def test_shell_exposes_session_context_and_revision_stores(self):
        stores = {
            component.id: component
            for component in _walk(app_layout())
            if getattr(component, "id", None) in {
                "studies-context-store",
                "assays-context-store",
                "page-data-revision-store",
                "page-data-mutation-signal",
                "workflow-mutation-signal",
                "workflow-jobs-revision-store",
            }
        }

        self.assertEqual(stores["studies-context-store"].data, {})
        self.assertEqual(stores["assays-context-store"].data, {})
        self.assertEqual(stores["page-data-revision-store"].data, 0)
        self.assertEqual(stores["page-data-mutation-signal"].data, 0)
        self.assertEqual(stores["workflow-mutation-signal"].data, 0)
        self.assertIsNone(stores["workflow-jobs-revision-store"].data)
        self.assertTrue(all(store.storage_type == "session" for store in stores.values()))


if __name__ == "__main__":
    unittest.main()
