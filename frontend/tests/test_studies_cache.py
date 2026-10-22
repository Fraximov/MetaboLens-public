import unittest
from types import SimpleNamespace
from unittest.mock import patch

import dash
from dash import Input, Output

from callbacks.studies import (
    _cache_rows_after_fetch_failure,
    _context_rows,
    _reuse_rows_on_route,
    register_callbacks,
)
from services.page_cache import build_page_cache, page_cache_data, page_cache_matches
from services.api_client import APIError


class _CallbackCapture:
    def __init__(self) -> None:
        self.dependencies = []
        self.callbacks = {}

    def callback(self, *dependencies, **_kwargs):
        self.dependencies.append(dependencies)

        def decorator(callback):
            self.callbacks[callback.__name__] = callback
            return callback

        return decorator


class StudiesCacheTests(unittest.TestCase):
    def test_successful_study_create_signals_mutation_when_followup_list_fails(self) -> None:
        app = _CallbackCapture()
        register_callbacks(app)

        with patch("callbacks.studies.resolve_page_slug", return_value=("studies", "/studies")), patch(
            "callbacks.studies.dash.ctx", SimpleNamespace(triggered_id="study-create-btn")
        ), patch(
            "callbacks.studies.study_service.create_study", return_value={"id": 10}
        ), patch("callbacks.studies.study_service.list_studies", side_effect=APIError("offline")):
            result = app.callbacks["create_or_refresh_studies"](
                "/studies", "token", 0, 1, 0, "Pilot", "", None, [], {}, 7
            )

        self.assertEqual(result[-1], 8)

    def test_successful_assay_create_signals_mutation_when_followup_list_fails(self) -> None:
        app = _CallbackCapture()
        register_callbacks(app)

        with patch("callbacks.studies.resolve_page_slug", return_value=("studies", "/studies")), patch(
            "callbacks.studies.dash.ctx", SimpleNamespace(triggered_id="assay-create-btn")
        ), patch(
            "callbacks.studies.study_service.create_study_assay", return_value={"id": 18}
        ), patch("callbacks.studies.study_service.list_study_assays", side_effect=APIError("offline")):
            result = app.callbacks["create_or_refresh_assays"](
                "/studies", "token", 9, 0, 1, "LC-MS", "LC_MS", None, [], {}, 7
            )

        self.assertEqual(result[-1], 8)

    def test_auth_mismatch_hides_study_rows_and_prevents_route_reuse(self) -> None:
        rows = [{"id": 9, "title": "Prior user's study"}]
        context = build_page_cache(rows, "token-a")

        self.assertEqual(_context_rows(rows, context, "token-b"), [])
        self.assertFalse(_reuse_rows_on_route("url", rows, context, "token-b"))

    def test_study_mismatch_hides_assay_rows_and_prevents_route_reuse(self) -> None:
        rows = [{"id": 18, "study_id": 9, "name": "Prior study assay"}]
        context = build_page_cache(rows, "token", study_id=9)

        self.assertEqual(_context_rows(rows, context, "token", study_id=10), [])
        self.assertFalse(_reuse_rows_on_route("url", rows, context, "token", study_id=10))

    def test_failed_assay_fetch_discards_mismatched_rows_and_replaces_context(self) -> None:
        rows = [{"id": 18, "study_id": 9, "name": "Prior study assay"}]
        context = build_page_cache(rows, "token", study_id=9)

        recovered_rows, recovered_context = _cache_rows_after_fetch_failure(
            rows,
            context,
            "token",
            study_id=10,
        )

        self.assertEqual(recovered_rows, [])
        self.assertEqual(page_cache_data(recovered_context, []), [])
        self.assertTrue(page_cache_matches(recovered_context, "token", study_id=10))

    def test_studies_callbacks_register_context_outputs_and_inputs(self) -> None:
        app = _CallbackCapture()
        register_callbacks(app)

        study_fetch = next(
            dependencies
            for dependencies in app.dependencies
            if any(
                isinstance(dependency, Output) and dependency.component_id == "studies-store"
                for dependency in dependencies
            )
        )
        assay_fetch = next(
            dependencies
            for dependencies in app.dependencies
            if any(
                isinstance(dependency, Output) and dependency.component_id == "assays-store"
                for dependency in dependencies
            )
        )

        study_outputs = {
            dependency.component_id for dependency in study_fetch if isinstance(dependency, Output)
        }
        assay_outputs = {
            dependency.component_id for dependency in assay_fetch if isinstance(dependency, Output)
        }
        study_inputs = {
            dependency.component_id for dependency in study_fetch if isinstance(dependency, Input)
        }
        assay_inputs = {
            dependency.component_id for dependency in assay_fetch if isinstance(dependency, Input)
        }

        self.assertIn("studies-context-store", study_outputs)
        self.assertIn("assays-context-store", assay_outputs)
        self.assertIn("token-store", study_inputs)
        self.assertIn("token-store", assay_inputs)

        callback_inputs = [
            {
                dependency.component_id
                for dependency in dependencies
                if isinstance(dependency, Input) and isinstance(dependency.component_id, str)
            }
            for dependencies in app.dependencies
        ]
        self.assertTrue(
            any({"token-store", "studies-context-store"}.issubset(inputs) for inputs in callback_inputs)
        )
        self.assertTrue(
            any({"token-store", "assays-context-store"}.issubset(inputs) for inputs in callback_inputs)
        )
        self.assertTrue(
            any(
                {"token-store", "studies-context-store", "assays-context-store"}.issubset(inputs)
                for inputs in callback_inputs
            )
        )


if __name__ == "__main__":
    unittest.main()


class StudyActionClickTests(unittest.TestCase):
    """Submit, Reopen and Transition change a study; only a real click may."""

    def _action(self, clicks):
        app = _CallbackCapture()
        register_callbacks(app)
        ctx = SimpleNamespace(triggered_id="study-transition-btn",
                              triggered=[{"prop_id": "study-transition-btn.n_clicks", "value": clicks}])
        with patch("callbacks.studies.dash.ctx", ctx), \
                patch("callbacks.studies.study_service.transition_study", return_value={"status": "draft"}) as transition:
            result = app.callbacks["study_actions"](0, 0, clicks, "tok", 9, "draft")
        return result, transition

    def test_a_render_time_trigger_changes_nothing(self):
        result, transition = self._action(0)
        self.assertEqual(result, (dash.no_update, dash.no_update))
        transition.assert_not_called()

    def test_a_click_still_transitions(self):
        _, transition = self._action(1)
        transition.assert_called_once_with("tok", 9, "draft")


class PreferredAssayTests(unittest.TestCase):
    def test_the_assay_with_the_newest_run_wins(self):
        from callbacks.studies import preferred_assay_id
        assays = [
            {"id": 17, "is_default": True, "last_workflow_job_at": None},
            {"id": 18, "last_workflow_job_at": "2026-09-23T09:59:42.594534Z"},
            {"id": 19, "last_workflow_job_at": "2026-06-29T13:06:39+00:00"},
        ]
        self.assertEqual(preferred_assay_id(assays), 18)

    def test_without_runs_the_default_then_the_first(self):
        from callbacks.studies import preferred_assay_id
        self.assertEqual(preferred_assay_id([{"id": 3}, {"id": 4, "is_default": True}]), 4)
        self.assertEqual(preferred_assay_id([{"id": 3}, {"id": 4}]), 3)
        self.assertIsNone(preferred_assay_id([]))
