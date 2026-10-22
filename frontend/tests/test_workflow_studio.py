import unittest

from dash import html

from services.workflow_diagram_service import WORKFLOW_METRO_MAP
from ui.workflow_studio import _run_status_badge, _task_progress, command_preview_card, format_command_preview, pipeline_overview_card, recent_runs_table


def _walk(component):
    yield component
    children = getattr(component, "children", None)
    if children is None:
        return
    if not isinstance(children, (list, tuple)):
        children = [children]
    for child in children:
        if hasattr(child, "children"):
            yield from _walk(child)


def _component_by_id(component, component_id):
    return next(node for node in _walk(component) if getattr(node, "id", None) == component_id)


def _table_job_ids(component):
    tbody = next(node for node in _walk(component) if isinstance(node, html.Tbody))
    rows = tbody.children if isinstance(tbody.children, (list, tuple)) else [tbody.children]
    return [int(row.children[0].children) for row in rows]


class WorkflowStudioTests(unittest.TestCase):
    def test_command_preview_card_has_command_and_template_tabs(self) -> None:
        tabs = _component_by_id(command_preview_card(), "wf-command-template-tabs")

        self.assertEqual(tabs.active_tab, "command")
        self.assertEqual([tab.label for tab in tabs.children], ["Command", "Template"])

    def test_format_command_preview_adds_syntax_token_classes(self) -> None:
        rendered = format_command_preview("nextflow run main.nf \\\n  --workflow_stage full\n# note")

        spans = [node for node in rendered if isinstance(node, html.Span)]
        classes = {span.className for span in spans}

        self.assertIn("nf-token-command", classes)
        self.assertIn("nf-token-subcommand", classes)
        self.assertIn("nf-token-path", classes)
        self.assertIn("nf-token-option", classes)
        self.assertIn("nf-token-comment", classes)

    def test_recent_run_status_badge_uses_lightweight_class(self) -> None:
        badge = _run_status_badge("succeeded")

        self.assertEqual(badge.children, "Succeeded")
        self.assertIn("nf-run-status", badge.className)
        self.assertIn("nf-run-status-succeeded", badge.className)

    def test_task_progress_accepts_zero_completed_tasks(self) -> None:
        label, percent = _task_progress({"status_payload_json": {"completed_tasks": 0, "total_tasks": 4}})

        self.assertEqual(label, "0 / 4")
        self.assertEqual(percent, 0)

    def test_task_progress_shows_completed_count_without_percentage_for_unknown_total(self) -> None:
        for status in ("running", "failed"):
            with self.subTest(status=status):
                label, percent = _task_progress({
                    "status": status,
                    "status_payload_json": {"completed_tasks": 1, "observed_tasks": 1, "total_tasks": 0},
                })

                self.assertEqual(label, "1 complete")
                self.assertEqual(percent, 0)

    def test_task_progress_shows_complete_fraction_with_known_success_total(self) -> None:
        label, percent = _task_progress({
            "status": "succeeded", "status_payload_json": {"completed_tasks": 3, "total_tasks": 3},
        })

        self.assertEqual(label, "3 / 3")
        self.assertEqual(percent, 100)

    def test_pipeline_overview_uses_the_shared_metro_asset(self) -> None:
        image = next(node for node in _walk(pipeline_overview_card()) if getattr(node, "id", None) == "wf-overview-dag-image")

        self.assertEqual(image.src, WORKFLOW_METRO_MAP["src"])
        self.assertEqual(image.alt, WORKFLOW_METRO_MAP["alt"])

    def test_recent_runs_table_paginates_eight_rows_and_disables_edge_arrows(self) -> None:
        jobs = [{"id": job_id, "run_name": str(job_id), "status": "succeeded"} for job_id in range(10, 0, -1)]

        first_page = recent_runs_table(jobs, page=0)
        second_page = recent_runs_table(jobs, page=1)

        self.assertEqual(_table_job_ids(first_page), list(range(10, 2, -1)))
        self.assertEqual(_table_job_ids(second_page), [2, 1])
        self.assertTrue(_component_by_id(first_page, "wf-recent-runs-prev").disabled)
        self.assertFalse(_component_by_id(first_page, "wf-recent-runs-next").disabled)
        self.assertFalse(_component_by_id(second_page, "wf-recent-runs-prev").disabled)
        self.assertTrue(_component_by_id(second_page, "wf-recent-runs-next").disabled)
        self.assertEqual(_component_by_id(first_page, "wf-recent-runs-page-status").children, "1–8 of 10")
        self.assertEqual(_component_by_id(second_page, "wf-recent-runs-page-status").children, "9–10 of 10")

    def test_recent_run_row_renders_view_and_confirmed_delete_actions(self) -> None:
        table = recent_runs_table([{"id": 42, "run_name": "job_42", "status": "succeeded"}])

        view = _component_by_id(table, {"type": "wf-open-job-btn", "index": 42})
        confirm = _component_by_id(table, {"type": "wf-delete-job-confirm", "index": 42})

        self.assertEqual(view.children, "View")
        self.assertIn("job_42", confirm.message)
        self.assertFalse(confirm.children.disabled)

    def test_recent_run_delete_is_disabled_for_running_job(self) -> None:
        table = recent_runs_table([{"id": 42, "run_name": "job_42", "status": "running"}])

        confirm = _component_by_id(table, {"type": "wf-delete-job-confirm", "index": 42})

        self.assertTrue(confirm.children.disabled)

    def test_pipeline_route_map_is_collapsible_and_open_by_default(self) -> None:
        details = next(node for node in _walk(pipeline_overview_card()) if isinstance(node, html.Details))

        self.assertEqual(details.id, "wf-route-map-details")
        self.assertTrue(details.open)


if __name__ == "__main__":
    unittest.main()
