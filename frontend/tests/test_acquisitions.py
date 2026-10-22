import unittest

from callbacks.files_sample_metadata import render_editable_sample_metadata_table
from services import sample_service


def components(node):
    if isinstance(node, (list, tuple)):
        for child in node:
            yield from components(child)
    elif hasattr(node, "to_plotly_json"):
        yield node
        yield from components(getattr(node, "children", None))


class AcquisitionUITests(unittest.TestCase):
    def test_sample_editor_does_not_write_injection_metadata(self):
        table = render_editable_sample_metadata_table([{"id": 1, "name": "S1", "sample_type": "sample"}])
        ids = [c.id for c in components(table) if hasattr(c, "id") and isinstance(c.id, dict)]
        self.assertFalse(any(i["type"] in {"sample-batch-id-input", "sample-injection-order-input"} for i in ids))

    def test_folder_preview_reuses_exact_names_but_never_guesses_rerun_suffixes(self):
        self.assertTrue(callable(getattr(sample_service, "build_folder_preview_rows", None)))
        rows = sample_service.build_folder_preview_rows([
            {"sample_code": "S1", "source_path": "/data/S1.raw", "original_filename": "S1.raw"},
            {"sample_code": "S1_repeat", "source_path": "/data/S1_repeat.raw", "original_filename": "S1_repeat.raw"},
        ], [{"id": 42, "name": "S1"}])
        self.assertEqual([r["existing_sample_id"] for r in rows], [42, ""])
        self.assertEqual([r["injection_order"] for r in rows], [1, 2])

    def test_acquisition_editor_distinguishes_two_injections_of_one_sample(self):
        from callbacks.files_acquisitions import render_acquisitions
        table = render_acquisitions([
            {"id": 11, "sample_id": 1, "sample_name": "S1", "filenames": ["a.raw"], "annotations_json": {"batch_id": "1", "injection_order": 1}},
            {"id": 12, "sample_id": 1, "sample_name": "S1", "filenames": ["b.raw"], "annotations_json": {"batch_id": "2", "injection_order": 7}},
        ])
        self.assertEqual([r["id"] for r in table.data], [11, 12])
        self.assertEqual([r["injection_order"] for r in table.data], [1, 7])
