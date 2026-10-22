"""Native evidence contract checks; the cache double replaces only mzpkl I/O."""
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ADAPTERS = Path(__file__).resolve().parents[1] / "adapters"
sys.path.insert(0, str(ADAPTERS))


class NativeCache:
    def get_eic_data(self, mz, rt=None, mz_tol=0.005, rt_tol=0.3, rt_range=None):
        if rt_range is None:
            # The exact native gap window peaks at 2.04, whereas the display
            # context has a stronger unrelated peak at 2.3.
            return [1.96, 2.04], [[mz, 10], [mz, 20]], [0, 1]
        return [1.6, 2.04, 2.3], [[mz, 1], [mz, 20], [mz, 90]], [0, 1, 2]


class MassCubeEvidenceTests(unittest.TestCase):
    def adapter(self):
        path = ADAPTERS / "masscube_evidence.py"
        self.assertTrue(path.is_file(), "MassCube indexed evidence producer is missing")
        spec = importlib.util.spec_from_file_location("masscube_evidence", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def fixture(self, root, count=2):
        sheet = [dict(acquisition_id="file-1", source_column="a", file_asset_id="1", sample_name="same"),
                 dict(acquisition_id="file-2", source_column="b", file_asset_id="2", sample_name="same")]
        with (root / "samplesheet.csv").open("w") as stream:
            writer = csv.DictWriter(stream, fieldnames=sheet[0]); writer.writeheader(); writer.writerows(sheet)
        fields = ["feature_ID", "m/z", "RT", "alignment_reference_file", "MS2", "matched_MS2", "similarity", "search_mode", "a", "b"]
        with (root / "aligned_feature_table.tsv").open("w") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t"); writer.writeheader()
            writer.writerows(dict(zip(fields, [str(i), "123.4", "2.0", "a", "100.0;2|110.0;4", "100.01;3|120;1", "0.87", "identity_search", "90", "20"])) for i in range(1, count + 1))
        evidence_fields = ["source_feature_id", "acquisition_id", "native_feature_id", "detected", "gap_filled", "intensity", "native_mz", "native_rt_min", "scan_idx"]
        with (root / "acquisition_evidence.tsv").open("w") as stream:
            writer = csv.writer(stream, delimiter="\t"); writer.writerow(evidence_fields)
            for i in range(1, count + 1):
                writer.writerow([i, "file-1", i + 70, True, False, 90, 123.40001, 2.0, 2])
                writer.writerow([i, "file-2", "", False, True, 20, "", "", ""])
        (root / "source_metadata.json").write_text(json.dumps({"configuration": {"correct_rt": False, "merge_features": False, "mz_tol_alignment": 0.01, "mz_tol_ms1": 0.01, "mz_tol_ms2": 0.015, "gap_filling_rt_window": 0.05}, "library": {"file": "library.msp"}}))

    def test_complete_index_bound_to_inputs_and_exact_gap_window(self):
        adapter = self.adapter()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self.fixture(root)
            adapter.write_indexed_evidence(root, cache_loader=lambda path: NativeCache())
            index = json.loads((root / "evidence.index.json").read_text())
            data = (root / "evidence.jsonl").read_bytes()
            self.assertEqual(index["data_sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(index["aligned_table_md5"], hashlib.md5((root / "aligned_feature_table.tsv").read_bytes()).hexdigest())
            self.assertEqual(index["acquisition_evidence_md5"], hashlib.md5((root / "acquisition_evidence.tsv").read_bytes()).hexdigest())
            self.assertEqual(list(index["features"]), ["1", "2"])
            entry = index["features"]["2"]
            record = json.loads(data[entry["offset"]:entry["offset"] + entry["length"]])
            self.assertEqual(record["source_feature_id"], "2")
            self.assertEqual([peak["acquisition_id"] for peak in record["peaks"]], ["file-1", "file-2"])
            self.assertEqual(record["peaks"][0]["native_feature_id"], "72")
            self.assertEqual(record["peaks"][1]["top"], 2.04)
            self.assertIsNone(record["peaks"][1]["left"])
            self.assertEqual(record["peaks"][1]["intensity"], [1, 20, 90])
            self.assertEqual(record["ms2"]["experimental"], [{"mz": 100.0, "intensity": 50.0, "matched": True}, {"mz": 110.0, "intensity": 100.0, "matched": False}])
            self.assertEqual(record["ms2"]["scores"], {"similarity": 0.87, "search_mode": "identity_search"})
            self.assertEqual(record["ms2"]["reference"]["peaks"][0], {"mz": 100.01, "intensity": 100.0, "matched": True})
            self.assertIsNone(entry["msms_matched"])
            self.assertIsNone(entry["low_score"])

    def test_all_features_written_beyond_twenty_thousand(self):
        adapter = self.adapter()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self.fixture(root, count=20001)
            adapter.write_indexed_evidence(root, cache_loader=lambda path: NativeCache())
            index = json.loads((root / "evidence.index.json").read_text())
            self.assertEqual(len(index["features"]), 20001)
            self.assertIn("20001", index["features"])

    def test_missing_acquisition_fails_instead_of_substituting_another(self):
        adapter = self.adapter()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self.fixture(root)
            path = root / "acquisition_evidence.tsv"
            path.write_text("\n".join(path.read_text().splitlines()[:-1]) + "\n")
            with self.assertRaisesRegex(ValueError, "acquisition"):
                adapter.write_indexed_evidence(root, cache_loader=lambda path: NativeCache())


if __name__ == "__main__":
    unittest.main()
