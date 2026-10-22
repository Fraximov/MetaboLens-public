import importlib.util
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


def _load_extractor_module():
    pipeline_root = Path(os.environ.get("WORKFLOW_PIPELINE_ROOT", "workflows/nextflow4ms-dial-msdial5-clean"))
    script_path = pipeline_root / "adapters" / "extract_is_qc.py"
    spec = importlib.util.spec_from_file_location("extract_is_qc", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class IsQcExtractorTests(unittest.TestCase):
    def test_resolves_reinjections_by_staged_filename_with_same_display_name(self):
        import csv
        module = _load_extractor_module()
        with TemporaryDirectory() as temp:
            root = Path(temp)
            prepared = root / "prepared"
            prepared.mkdir()
            for identity in (11, 12):
                (prepared / f"file-{identity}.mzML").write_text("<mzML/>")
            sheet = root / "samplesheet.csv"
            with sheet.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["sample_name", "input_filename", "staged_filename"])
                writer.writeheader()
                for identity in (11, 12):
                    writer.writerow({"sample_name": "S1", "input_filename": "S1.raw", "staged_filename": f"file-{identity}.raw"})
            rows = module.resolve_samples(prepared, sheet)
            self.assertEqual([r["sample_name"] for r in rows], ["S1", "S1"])
            self.assertEqual([r["input_file"] for r in rows], ["file-11.mzML", "file-12.mzML"])

    def test_parse_target_file_accepts_facility_is_library_columns(self) -> None:
        module = _load_extractor_module()
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "targets.txt"
            path.write_text(
                "Name\tm/z(Da)\tRT (min)\n"
                "IS: L-Alanine-2,3,3,3-d4\t94.0701\t0.72\n",
                encoding="utf-8",
            )

            targets = module.parse_target_file(path)

        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["name"], "IS: L-Alanine-2,3,3,3-d4")
        self.assertAlmostEqual(targets[0]["mz"], 94.0701)
        self.assertAlmostEqual(targets[0]["rt_min"], 0.72)

    def test_parse_target_file_rejects_missing_required_columns(self) -> None:
        module = _load_extractor_module()
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "targets.txt"
            path.write_text("Metabolite\tMZ\nA\t101.1\n", encoding="utf-8")

            with self.assertRaises(ValueError) as ctx:
                module.parse_target_file(path)

        self.assertIn("Name", str(ctx.exception))
        self.assertIn("RT", str(ctx.exception))

    def test_score_detection_uses_peak_height_and_rt_tolerance(self) -> None:
        module = _load_extractor_module()

        passed = module.score_detection(
            expected_rt_min=5.0,
            observed_rt_min=5.2,
            apex_intensity=1200.0,
            rt_tolerance_min=0.5,
            min_peak_height=1000.0,
        )
        low_peak = module.score_detection(
            expected_rt_min=5.0,
            observed_rt_min=5.2,
            apex_intensity=900.0,
            rt_tolerance_min=0.5,
            min_peak_height=1000.0,
        )
        shifted = module.score_detection(
            expected_rt_min=5.0,
            observed_rt_min=5.8,
            apex_intensity=1200.0,
            rt_tolerance_min=0.5,
            min_peak_height=1000.0,
        )

        self.assertTrue(passed["passed"])
        self.assertEqual(passed["status"], "pass")
        self.assertFalse(low_peak["passed"])
        self.assertIn("height", low_peak["message"].lower())
        self.assertFalse(shifted["passed"])
        self.assertIn("RT", shifted["message"])

    def test_summarize_qc_rows_scores_target_fraction_and_qc_cv(self) -> None:
        module = _load_extractor_module()
        rows = [
            {"target_name": "IS: A", "sample_name": "QC1", "sample_type": "qc", "passed": True, "apex_intensity": 1000.0},
            {"target_name": "IS: A", "sample_name": "QC2", "sample_type": "qc", "passed": True, "apex_intensity": 1020.0},
            {"target_name": "IS: A", "sample_name": "S1", "sample_type": "sample", "passed": True, "apex_intensity": 900.0},
            {"target_name": "IS: B", "sample_name": "QC1", "sample_type": "qc", "passed": False, "apex_intensity": 100.0},
            {"target_name": "IS: B", "sample_name": "QC2", "sample_type": "qc", "passed": False, "apex_intensity": 120.0},
            {"target_name": "IS: B", "sample_name": "S1", "sample_type": "sample", "passed": False, "apex_intensity": 80.0},
        ]

        summary = module.summarize_qc_rows(
            rows,
            thresholds={
                "is_qc_min_sample_pass_fraction": 0.8,
                "is_qc_min_target_pass_fraction": 0.8,
                "is_qc_max_qc_cv_percent": 30,
            },
        )

        self.assertEqual(summary["overall_status"], "fail")
        self.assertEqual(summary["counts"]["targets"], 2)
        self.assertEqual(summary["counts"]["passing_targets"], 1)
        self.assertAlmostEqual(summary["target_pass_fraction"], 0.5)
        self.assertEqual(summary["target_statuses"]["IS: A"]["qc_cv_status"], "pass")
        self.assertEqual(summary["target_statuses"]["IS: B"]["status"], "fail")

    def test_zero_signal_qc_reinjections_produce_json_safe_failed_cv(self):
        import json
        module = _load_extractor_module()
        summary = module.summarize_qc_rows(
            [{"target_name": "IS: A", "sample_name": "QC_03", "sample_type": "qc",
              "passed": False, "apex_intensity": 0.0} for _ in range(2)],
            thresholds={"is_qc_min_sample_pass_fraction": 0.8,
                        "is_qc_min_target_pass_fraction": 0.8,
                        "is_qc_max_qc_cv_percent": 30},
        )
        target = summary["target_statuses"]["IS: A"]
        self.assertIsNone(target["qc_cv_percent"])
        self.assertEqual(target["qc_cv_status"], "fail")
        self.assertEqual(summary["overall_status"], "fail")
        json.dumps(summary, allow_nan=False)

    def test_downsample_points_preserves_first_and_last_points(self) -> None:
        module = _load_extractor_module()
        points = [{"rt_min": float(index), "intensity": float(index)} for index in range(10)]

        reduced = module.downsample_points(points, max_points=4)

        self.assertEqual(len(reduced), 4)
        self.assertEqual(reduced[0]["rt_min"], 0.0)
        self.assertEqual(reduced[-1]["rt_min"], 9.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
