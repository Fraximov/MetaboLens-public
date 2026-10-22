import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

ADAPTERS = Path(__file__).resolve().parents[1] / "adapters"
sys.path.insert(0, str(ADAPTERS))


class MassCubeRunnerTests(unittest.TestCase):
    def test_rt_and_ms1_mz_ranges_reach_native_params_and_reject_inverted_bounds(self):
        adapter = self.adapter()
        params = SimpleNamespace(rt_lower_limit=0, rt_upper_limit=10000,
                                 mz_lower_limit=0, mz_upper_limit=100000)
        adapter.configure_params(params, {'rt_lower_limit': 0.5, 'rt_upper_limit': 18,
                                         'mz_lower_limit': 60, 'mz_upper_limit': 900})
        self.assertEqual((params.rt_lower_limit, params.rt_upper_limit), (0.5, 18))
        self.assertEqual((params.mz_lower_limit, params.mz_upper_limit), (60, 900))
        for config in ({'rt_upper_limit': 0.5}, {'mz_lower_limit': 901}, {'mz_upper_limit': -1}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                adapter.configure_params(params, config)

    def adapter(self):
        path = ADAPTERS / "masscube_runner.py"
        self.assertTrue(path.is_file(), "MassCube application runner is missing")
        spec = importlib.util.spec_from_file_location("masscube_runner", path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        return module

    def test_frozen_config_preserves_native_defaults_and_rejects_unsupported_science(self):
        adapter = self.adapter()
        params = SimpleNamespace(ms1_abs_int_tol=50000, mz_tol_ms1=0.01)
        adapter.configure_params(params, {"mz_tol_ms1": 0.02})
        self.assertEqual(params.ms1_abs_int_tol, 50000)
        self.assertEqual(params.mz_tol_ms1, 0.02)
        self.assertEqual(params.quant_method, "peak_height")
        self.assertIs(params.merge_features, False)
        adapter.configure_params(params, {"ms1_abs_int_tol": 0})
        self.assertEqual(params.ms1_abs_int_tol, 0)
        for config in [{"merge_features": True}, {"correct_rt": True}, {"quant_method": "peak_area"},
                       {"workflow_stage": "full"}, {"mz_tol_ms1": -1}, {"mz_tol_ms1": True},
                       {"detection_rate_cutoff": 2}, {"fuzzy_search": "false"},
                       {"feature_gap_tol": 0}, {"scan_number_cutoff": 0}]:
            with self.subTest(config=config), self.assertRaises(ValueError):
                adapter.configure_params(params, config)

    def test_resolves_actual_mzml_case_and_rejects_dropped_acquisitions(self):
        adapter = self.adapter()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "a.mzml").write_text(""); (root / "b.mzML").write_text("")
            rows = [{"staged_filename": "a.raw", "acquisition_id": "file-1"},
                    {"staged_filename": "b.mzML", "acquisition_id": "file-2"}]
            paths = adapter.resolve_acquisitions(root, rows)
            self.assertEqual([path.name for path in paths], ["a.mzml", "b.mzML"])
            self.assertEqual([row["source_column"] for row in rows], ["a", "b"])
            (root / "b.mzML").unlink()
            with self.assertRaisesRegex(ValueError, "acquisition"):
                adapter.resolve_acquisitions(root, rows)

    def test_package_config_uses_actual_metadata_and_preserves_frozen_qc_settings(self):
        adapter = self.adapter()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            frozen = root / "frozen.json"; metadata = root / "source.json"; output = root / "generated.json"
            frozen.write_text(json.dumps({"schema_version": "1.0", "profile": "strict", "source_tool": "masscube",
                "source_metadata": {"stale": True}, "enabled_rules": {"statistics": [], "annotation": ["has_ms2"]}}))
            metadata.write_text(json.dumps({"version": "1.2.22", "evidence_adapter_id": "masscube-indexed-v1", "acquisition_evidence_file": "acquisition_evidence.tsv"}))
            adapter.write_package_config(frozen, metadata, output)
            config = json.loads(output.read_text())
            self.assertEqual(config["enabled_rules"], {"statistics": [], "annotation": ["has_ms2"]})
            self.assertNotIn("stale", config["source_metadata"])
            self.assertEqual(config["source_metadata"]["evidence_adapter_id"], "masscube-indexed-v1")


if __name__ == "__main__":
    unittest.main()
