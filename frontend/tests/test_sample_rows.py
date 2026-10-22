import unittest

from ui.formatting import sample_type_label
from ui.rows import sample_rows


class SampleRowTests(unittest.TestCase):
    def test_sample_type_label_uses_human_readable_names(self) -> None:
        self.assertEqual(sample_type_label("sample"), "Sample")
        self.assertEqual(sample_type_label("qc"), "QC")
        self.assertEqual(sample_type_label("blank"), "Blank")
        self.assertEqual(sample_type_label("other"), "Other")

    def test_sample_rows_display_sample_type_column(self) -> None:
        rows = sample_rows(
            [
                {
                    "id": 1,
                    "name": "QC1",
                    "description": "Quality control",
                    "replicate_number": 1,
                    "dilution_factor": 1.0,
                    "sample_type": "qc",
                    "organism": "Homo sapiens",
                    "matrix": "serum",
                    "annotations_json": {
                        "group_label": "QC",
                        "batch_id": "batch-1",
                        "injection_order": 3,
                    },
                }
            ]
        )

        self.assertEqual(
            rows[0],
            [1, "QC1", "Quality control", 1, 1.0, "QC", "Homo sapiens", "serum", "QC", "batch-1", 3],
        )


if __name__ == "__main__":
    unittest.main()
