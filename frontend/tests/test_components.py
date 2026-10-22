import unittest

import dash_bootstrap_components as dbc
from dash import html

from ui.components import build_selection_reminder, render_table


class TableTests(unittest.TestCase):
    def test_table_keeps_download_button_as_a_component(self) -> None:
        table = render_table(["Download"], [[dbc.Button("Download", id="file-download")]])

        cell = table.children[1].children[0].children[0]
        self.assertIsInstance(cell.children, dbc.Button)
        self.assertEqual(cell.children.id, "file-download")


class SelectionReminderTests(unittest.TestCase):
    def test_compact_reminder_renders_selected_study_and_assay(self) -> None:
        reminder = build_selection_reminder(
            selected_study_id=5,
            studies_data=[{"id": 5, "title": "Lipidomics pilot", "status": "draft"}],
            selected_assay_id=12,
            assays_data=[
                {
                    "id": 12,
                    "name": "LC-MS positive",
                    "status": "processing",
                    "platform": "metabolomics",
                    "metabolomics_assay": {"assay_type": "LC_MS"},
                }
            ],
            compact=True,
        )

        self.assertIsInstance(reminder, html.Details)
        text = str(reminder)
        self.assertIn("Study > Assay", text)
        self.assertIn("Lipidomics pilot", text)
        self.assertIn("Lipidomics pilot > LC-MS positive", text)
        self.assertIn("LC-MS positive", text)
        self.assertIn("Metabolomics LC-MS", text)

    def test_reminder_without_study_shows_empty_state(self) -> None:
        reminder = build_selection_reminder(None, [], None, [], compact=True)

        text = str(reminder)
        self.assertIn("No study selected", text)
        self.assertIn("Use Select in the Studies page.", text)


if __name__ == "__main__":
    unittest.main()
