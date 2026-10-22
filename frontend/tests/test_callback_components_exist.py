"""Every callback's components must exist on some page.

Dash does not run a callback whose Input is absent from the layout, and it says
nothing when that happens. Deleting a component therefore silently disables every
callback that listened to it, somewhere else in the app.

That is exactly what happened when the monitoring panels moved off the Workflows
page: `cache_invalidation` still listened to `wf-jobs-store`, so the app-wide
page-data revision quietly stopped advancing and the dashboard and summary pages
kept serving stale caches. Nothing failed; it just stopped working.
"""
import unittest

from dash import Input, Output, State, html

from pages.dashboard import dashboard_page_layout
from pages.files import files_page_layout
from pages.job_workspace import job_workspace_layout
from pages.shell import app_layout
from pages.studies import studies_page_layout
from pages.study_packaging import build_controls, runs_controls, study_packaging_layout
from pages.summary import summary_page_layout
from pages.workflows import workflows_page_layout


def _walk(node):
    yield node
    children = getattr(node, "children", None)
    for child in (children if isinstance(children, list) else [children]):
        if child is not None and not isinstance(child, str):
            yield from _walk(child)


def _component_ids():
    """Every string id the application can render.

    Includes the job workspace's section views. Those are mounted by the route
    rather than sitting in the page layout, which is the whole point of that
    design, so they are rendered here to be counted.
    """
    from callbacks.files_acquisitions import render_acquisitions
    from callbacks.files_folder_browser import render_folder_preview
    from callbacks.job_workspace import (
        continue_controls, eic_view, is_qc_view, overview_view, section_nav,
    )
    job = {"id": 1, "status": "succeeded", "run_name": "run_1", "status_payload_json": {}}
    layouts = [
        app_layout(), workflows_page_layout(), dashboard_page_layout(),
        studies_page_layout(), study_packaging_layout(14), runs_controls(14), build_controls(14), *workspace_sections(),
        files_page_layout(), summary_page_layout(), render_acquisitions([]),
        render_folder_preview({"rows": [{"filename": "S1.raw"}], "samples": []}),
        job_workspace_layout(),
        eic_view(), is_qc_view(), overview_view(job), continue_controls(),
        section_nav(1, "overview"),
        # Containers render_job_detail mounts for the overview and outputs
        # sections, which their own callbacks then fill.
        [html.Div(id="jw-overview-pane"), html.Div(id="jw-outputs-pane")],
    ]
    found = set()
    for layout in layouts:
        for root in (layout if isinstance(layout, list) else [layout]):
            for component in _walk(root):
                component_id = getattr(component, "id", None)
                if isinstance(component_id, str):
                    found.add(component_id)
    return found


def workspace_sections():
    """Packaging sections rendered from sample data, so their fixed ids can be counted."""
    from callbacks import packaging_metadata
    from callbacks.study_packaging import build_detail_view
    metadata = {"revision": 1, "title": "T", "description": "", "submission_date": None, "public_release_date": None,
                "contacts": [], "design_descriptors": [], "publications": [], "factors": [],
                "collection_protocol": "", "assays": [{"assay_id": 1, "assay_name": "A", "protocols": {}}]}
    envelope = {"metadata": metadata, "can_edit": True, "samples": [], "factor_columns": [], "protocol_sharing": {}}
    choices = {"assays": [{"assay_id": 1, "assay_name": "A", "sources": []}], "vocabularies": {}}
    package = {"id": 1, "revision": 1, "status": "complete", "summary": {"errors": 0, "warnings": 0},
               "output_uri": "file:///p", "export": {}, "sources": []}
    return [packaging_metadata.study_view(envelope, choices), packaging_metadata.protocols_view(envelope, choices, {}),
            packaging_metadata.samples_view(envelope, choices, {}), build_detail_view(1, package)]


class _Capture:
    def __init__(self):
        self.dependencies = {}

    def callback(self, *dependencies, **_kwargs):
        def decorator(function):
            self.dependencies.setdefault(function.__name__, []).extend(dependencies)
            return function
        return decorator

    def clientside_callback(self, *_args, **_kwargs):
        return None


def _registered():
    from callbacks import (
        auth, cache_invalidation, dashboard, files, job_workspace, metadata,
        navigation, studies, study_packaging, summary, workflows,
    )
    app = _Capture()
    for module in (navigation, job_workspace, auth, dashboard, metadata, studies, study_packaging,
                   files, cache_invalidation, summary, workflows):
        module.register_callbacks(app)
    return app.dependencies


class CallbackComponentsExistTests(unittest.TestCase):
    def setUp(self):
        self.present = _component_ids()
        self.callbacks = _registered()

    @staticmethod
    def _string_ids(dependencies, kinds):
        # Pattern-matching ids are dicts and match whatever is rendered, so only
        # fixed string ids can be checked against the layouts.
        return [
            d.component_id for d in dependencies
            if isinstance(d, kinds) and isinstance(d.component_id, str)
        ]

    def test_every_callback_input_exists_somewhere(self):
        for name, dependencies in self.callbacks.items():
            missing = sorted(
                i for i in self._string_ids(dependencies, Input) if i not in self.present
            )
            with self.subTest(callback=name):
                self.assertEqual(missing, [], f"{name} listens to components no page renders")

    def test_every_callback_output_exists_somewhere(self):
        for name, dependencies in self.callbacks.items():
            missing = sorted(
                i for i in self._string_ids(dependencies, Output) if i not in self.present
            )
            with self.subTest(callback=name):
                self.assertEqual(missing, [], f"{name} writes to components no page renders")

    def test_every_callback_state_exists_somewhere(self):
        for name, dependencies in self.callbacks.items():
            missing = sorted(
                i for i in self._string_ids(dependencies, State) if i not in self.present
            )
            with self.subTest(callback=name):
                self.assertEqual(missing, [], f"{name} reads components no page renders")

    def test_the_job_revision_still_watches_a_real_store(self):
        """The specific regression: cache_invalidation kept listening to
        wf-jobs-store after the monitoring panels moved to /jobs."""
        deps = self.callbacks["advance_page_data_revision_for_workflow_jobs"]
        inputs = self._string_ids(deps, Input)
        self.assertIn("jw-jobs-store", inputs)
        self.assertNotIn("wf-jobs-store", inputs)


if __name__ == "__main__":
    unittest.main()
