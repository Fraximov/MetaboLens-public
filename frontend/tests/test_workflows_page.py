import unittest

from pages.workflows import workflows_page_layout


def _walk(component):
    if isinstance(component, (list, tuple)):
        for child in component:
            yield from _walk(child)
        return
    yield component
    children = getattr(component, "children", None)
    if children is None:
        return
    if not isinstance(children, (list, tuple)):
        children = [children]
    for child in children:
        yield from _walk(child)


class WorkflowPageLayoutTests(unittest.TestCase):
    def test_reference_status_is_hidden_with_the_reference_input(self):
        field = next(component for component in _walk(workflows_page_layout())
                     if getattr(component, 'id', None) == 'wf-reference-file-field')
        self.assertIn('wf-reference-status', [getattr(component, 'id', None) for component in _walk(field)])

    def test_launch_status_is_visible_before_tabbed_configuration(self) -> None:
        components = list(_walk(workflows_page_layout()))
        ids = [getattr(component, "id", None) for component in components]

        self.assertEqual(ids.count("wf-status"), 1)
        self.assertLess(ids.index("wf-status"), ids.index("wf-config-tabs"))

    def test_stats_pipeline_mode_control_is_removed(self) -> None:
        components = list(_walk(workflows_page_layout()))
        stats_mode_components = [component for component in components if getattr(component, "id", None) == "wf-stats-pipeline-mode"]

        self.assertEqual(stats_mode_components, [])
        self.assertNotIn("Statistics pipeline", [component for component in components if isinstance(component, str)])

    def test_compute_tab_exposes_optional_sirius_controls(self) -> None:
        components = list(_walk(workflows_page_layout()))
        by_id = {
            component.id: component
            for component in components
            if getattr(component, "id", None)
        }

        self.assertFalse(by_id["wf-enable-sirius"].value)
        mode = by_id["wf-sirius-annotation-mode"]
        self.assertEqual(mode.value, "formulas")
        self.assertTrue(mode.disabled)
        self.assertEqual(
            [option["value"] for option in mode.options],
            ["formulas", "canopus", "structures", "full"],
        )

    def test_workflow_metadata_stores_are_session_backed(self) -> None:
        components = list(_walk(workflows_page_layout()))
        by_id = {
            component.id: component
            for component in components
            if getattr(component, "id", None)
        }

        for store_id in (
            "wf-definitions-store",
            "wf-library-store",
            "wf-library-selection-store",
        ):
            self.assertEqual(by_id[store_id].storage_type, "session")

        self.assertEqual(
            getattr(by_id["wf-workflow-command-result-store"], "storage_type", "memory"),
            "memory",
        )

    def test_workflow_page_exposes_run_local_template_editor_controls(self) -> None:
        components = list(_walk(workflows_page_layout()))
        by_id = {
            component.id: component
            for component in components
            if getattr(component, "id", None)
        }

        for component_id in (
            "wf-command-template-tabs",
            "wf-msdial-template-editor",
            "wf-msdial-template-reset",
            "wf-msdial-template-download-btn",
            "wf-msdial-template-download",
            "wf-msdial-template-source-store",
            "wf-msdial-template-editor-status",
        ):
            self.assertIn(component_id, by_id)

        self.assertEqual(by_id["wf-msdial-template-source-store"].data, {})


if __name__ == "__main__":
    unittest.main()


class LaunchPageBoundaryTests(unittest.TestCase):
    """Monitoring and review moved to /jobs. The launch page must not carry
    their components: a leftover here is a control with no callback behind it."""

    MOVED = [
        "wf-list-table", "wf-jobs-store", "wf-jobs-status", "wf-jobs-refresh-interval",
        "wf-recent-runs-page", "wf-active-job", "wf-active-job-status",
        "wf-output-files-store", "wf-output-table", "wf-outputs-details",
        "wf-trace-review-details", "wf-trace-review-summary", "wf-trace-review-tabs",
        "wf-eic-archive", "wf-eic-feature", "wf-eic-graph-shell",
        "wf-is-qc-target", "wf-is-qc-summary", "wf-is-qc-rt-view",
        "wf-continue-btn", "wf-continue-note", "wf-apply-rt-correction",
        "wf-delete-job-result-store",
    ]

    def test_no_monitoring_or_review_component_remains(self) -> None:
        ids = {
            component.id
            for component in _walk(workflows_page_layout())
            if getattr(component, "id", None)
        }
        self.assertEqual(sorted(ids & set(self.MOVED)), [])

    def test_the_launch_controls_are_still_there(self) -> None:
        ids = {
            component.id
            for component in _walk(workflows_page_layout())
            if getattr(component, "id", None)
        }
        for needed in ["wf-id", "wf-launch-btn", "wf-command-preview", "wf-status",
                       "wf-msdial-template-editor", "wf-reference-file"]:
            with self.subTest(component=needed):
                self.assertIn(needed, ids)
