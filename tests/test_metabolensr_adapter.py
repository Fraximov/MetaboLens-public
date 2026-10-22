import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "workflows/nextflow4ms-dial-msdial5-clean/adapters/mdalign_to_metabolensr.py"


class AcquisitionAdapterTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), "MetaboLensR acquisition adapter is missing")
        spec = importlib.util.spec_from_file_location("mdalign_to_metabolensr", SCRIPT)
        self.adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.adapter)

    def test_file_identity_survives_reordering_reinjection_and_repeated_batch_orders(self):
        rows = [
            ["Alignment ID", "Metabolite name", "Average Rt(min)", "Average Mz", "MS/MS spectrum", "B", "A"],
            ["0", "Compound", "1.2", "123.4", "100:10", "200", "100"],
            ["5", "IS: Standard", "1.3", "124.4", "", "40", "20"],
        ]
        sheet = [
            dict(file_asset_id="11", input_filename="A.raw", sample_name="Liver", sample_id="1", sample_type="sample", group_label="Case", batch_id="1", injection_order="1"),
            dict(file_asset_id="12", input_filename="B.raw", sample_name="Liver", sample_id="1", sample_type="sample", group_label="Case", batch_id="2", injection_order="1"),
        ]
        table, mapping = self.adapter.adapt(rows, sheet, "group_label")
        self.assertEqual(table[4][-2:], ["file-12", "file-11"])
        self.assertEqual(table[5][-2:], ["200", "100"])
        self.assertEqual([r["source_column"] for r in mapping], ["file-12", "file-11"])
        self.assertEqual([r["file_asset_id"] for r in mapping], ["12", "11"])
        self.assertEqual([r["sample_name"] for r in mapping], ["Liver", "Liver"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mapping.csv"
            self.adapter.write_mapping(path, mapping)
            with path.open(newline="") as handle:
                self.assertEqual(list(csv.DictReader(handle)), mapping)

    def test_ambiguous_filenames_unknown_columns_and_duplicate_file_ids_fail(self):
        rows = [["Alignment ID", "MS/MS spectrum", "A"], ["0", "", "1"]]
        row = dict(file_asset_id="1", input_filename="A.raw", sample_name="S", sample_type="sample", group_label="G", batch_id="1", injection_order="1")
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            self.adapter.adapt(rows, [row, dict(row, file_asset_id="2")], "group_label")
        with self.assertRaisesRegex(ValueError, "match"):
            self.adapter.adapt(rows, [dict(row, input_filename="B.raw")], "group_label")
        rows[0].append("B"); rows[1].append("2")
        with self.assertRaisesRegex(ValueError, "acquisition"):
            self.adapter.adapt(rows, [row, dict(row, input_filename="B.raw", injection_order="2")], "group_label")

    def test_staged_filename_is_used_when_original_names_repeat(self):
        rows = [["Alignment ID", "MS/MS spectrum", "1_A", "2_A"], ["0", "", "10", "20"]]
        row = dict(input_filename="A.raw", sample_name="S", sample_type="sample", group_label="G", batch_id="1")
        table, mapping = self.adapter.adapt(rows, [dict(row, file_asset_id="1", staged_filename="1_A.raw", injection_order="1"), dict(row, file_asset_id="2", staged_filename="2_A.raw", injection_order="2")], "group_label")
        self.assertEqual(table[4][-2:], ["file-1", "file-2"])
        self.assertEqual([r["source_filename"] for r in mapping], ["1_A.raw", "2_A.raw"])


if __name__ == "__main__":
    unittest.main()
