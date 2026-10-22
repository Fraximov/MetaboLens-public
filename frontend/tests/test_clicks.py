"""Callbacks that change data act only on a real click.

Rendering a page can fire a button callback with n_clicks=0 despite
prevent_initial_call. Each case below is such a render-time trigger; none may
reach the service call that changes data.
"""
import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import dash

import callbacks.files_acquisitions as acquisitions
import callbacks.files_folder_browser as folder_browser
import callbacks.files_sample_metadata as sample_metadata
import callbacks.metadata as metadata
from callbacks.clicks import real_click


class _CallbackCapture:
    def __init__(self):
        self.callbacks = {}

    def callback(self, *_args, **_kwargs):
        def decorator(callback):
            self.callbacks[callback.__name__] = callback
            return callback
        return decorator


def _render_ctx(button):
    return SimpleNamespace(triggered_id=button, triggered=[{"prop_id": f"{button}.n_clicks", "value": 0}])


class RealClickTests(unittest.TestCase):
    def test_zero_and_missing_clicks_are_not_clicks(self):
        for triggered in ([], [{"value": 0}], [{"value": None}]):
            with patch("callbacks.clicks.dash.ctx", SimpleNamespace(triggered=triggered)):
                self.assertFalse(real_click())
        with patch("callbacks.clicks.dash.ctx", SimpleNamespace(triggered=[{"value": 1}])):
            self.assertTrue(real_click())

    def test_render_time_triggers_change_nothing(self):
        cases = [
            (metadata, "metadata_action", "metadata-submit-btn", "metadata_service.submit_metadata"),
            (folder_browser, "preview", "sample-scan-folder-btn", "call_api"),
            (folder_browser, "import_preview", "sample-confirm-folder-btn", "sample_service.register_samples_with_paths"),
            (acquisitions, "save", "acquisitions-save", "call_api"),
            (sample_metadata, "save_sample_metadata", "sample-metadata-save-btn", "sample_service.update_sample_metadata_rows"),
        ]
        for module, name, button, service in cases:
            with self.subTest(callback=name):
                app = _CallbackCapture()
                module.register_callbacks(app)
                callback = app.callbacks[name]
                args = [0] + ["x"] * (len(inspect.signature(callback).parameters) - 1)
                with patch.object(dash, "ctx", _render_ctx(button)), \
                        patch(f"{module.__name__}.{service}") as call:
                    result = callback(*args)
                self.assertTrue(all(value is dash.no_update for value in result))
                call.assert_not_called()
