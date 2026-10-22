import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


def _load(name: str):
    root = Path(os.environ.get("WORKFLOW_PIPELINE_ROOT", "workflows/nextflow4ms-dial-msdial5-clean"))
    path = root / "adapters" / f"{name}.py"
    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class RtCorrectionPlanTests(unittest.TestCase):
    def _rows(self):
        return [
            {"target_id": "1", "target_name": "IS:A", "sample_name": "S1", "input_file": "S1.mzML", "observed_rt_min": "1.1", "passed": "True"},
            {"target_id": "1", "target_name": "IS:A", "sample_name": "S2", "input_file": "S2.mzML", "observed_rt_min": "0.9", "passed": "True"},
            {"target_id": "2", "target_name": "IS:B", "sample_name": "S1", "input_file": "S1.mzML", "observed_rt_min": "5.2", "passed": "True"},
            {"target_id": "2", "target_name": "IS:B", "sample_name": "S2", "input_file": "S2.mzML", "observed_rt_min": "4.8", "passed": "True"},
        ]

    def _traces(self):
        return {
            "version": "is_qc_v1",
            "samples": [
                {"sample_name": "S1", "input_file": "S1.mzML"},
                {"sample_name": "S2", "input_file": "S2.mzML"},
            ],
            "traces": [
                {
                    "target_id": 1,
                    "target_name": "IS:A",
                    "sample_name": "S1",
                    "input_file": "S1.mzML",
                    "observed_rt_min": 1.1,
                    "passed": True,
                    "points": [{"rt_min": 1.0, "intensity": 10.0}, {"rt_min": 1.1, "intensity": 20.0}],
                }
            ],
        }

    def test_plan_uses_run_wide_standard_means(self) -> None:
        module = _load("rt_correction")

        plan = module.build_correction_plan(self._rows(), self._traces())

        self.assertTrue(plan["can_apply"])
        self.assertEqual(plan["method"]["rt_difference"], "sample_minus_sample_average")
        self.assertAlmostEqual(plan["target_means"]["1"]["mean_rt_min"], 1.0)
        s1 = next(sample for sample in plan["samples"] if sample["input_file"] == "S1.mzML")
        self.assertEqual(s1["anchor_count"], 2)
        self.assertAlmostEqual(s1["anchors"][0]["shift_min"], 0.1)
        feature = next(item for item in plan["features"] if item["target_id"] == "1" and item["input_file"] == "S1.mzML")
        self.assertAlmostEqual(feature["corrected_rt_min"], 1.0)

    def test_missing_sample_model_blocks_the_complete_run(self) -> None:
        module = _load("rt_correction")
        rows = [row for row in self._rows() if row["sample_name"] == "S1"]

        plan = module.build_correction_plan(rows, self._traces())

        self.assertFalse(plan["can_apply"])
        self.assertTrue(any("S2.mzML" in reason for reason in plan["blocking_reasons"]))

    def test_one_anchor_is_eligible_with_warning_and_duplicate_rt_is_excluded(self) -> None:
        module = _load("rt_correction")
        rows = [
            {"target_id": "1", "target_name": "IS:A", "sample_name": "S1", "input_file": "S1.mzML", "observed_rt_min": "1.0", "passed": "True"},
            {"target_id": "2", "target_name": "IS:B", "sample_name": "S1", "input_file": "S1.mzML", "observed_rt_min": "1.0", "passed": "True"},
            {"target_id": "3", "target_name": "IS:C", "sample_name": "S1", "input_file": "S1.mzML", "observed_rt_min": "5.0", "passed": "True"},
        ]
        payload = {"samples": [{"sample_name": "S1", "input_file": "S1.mzML"}], "traces": []}

        plan = module.build_correction_plan(rows, payload)

        sample = plan["samples"][0]
        self.assertTrue(sample["eligible"])
        self.assertEqual(sample["anchor_count"], 1)
        self.assertTrue(any("duplicate" in warning.lower() for warning in sample["warnings"]))
        self.assertTrue(any("one usable" in warning.lower() for warning in sample["warnings"]))

    def test_preview_contains_precomputed_corrected_trace_coordinates(self) -> None:
        module = _load("rt_correction")
        plan = module.build_correction_plan(self._rows(), self._traces())

        preview = module.build_preview(plan, self._traces())

        trace = preview["traces"][0]
        self.assertAlmostEqual(trace["cohort_mean_rt_min"], 1.0)
        self.assertAlmostEqual(trace["corrected_observed_rt_min"], 1.0)
        self.assertIn("corrected_rt_min", trace["points"][0])

    def test_selected_compound_is_the_only_model_anchor_but_all_features_remain(self) -> None:
        module = _load("rt_correction")
        plan = module.build_correction_plan(self._rows(), self._traces())

        selected = module.select_correction_plan(plan, ["2"])

        self.assertTrue(selected["can_apply"])
        self.assertEqual(selected["selected_target_ids"], ["2"])
        for sample in selected["samples"]:
            self.assertEqual(sample["anchor_count"], 1)
            self.assertEqual([anchor["target_id"] for anchor in sample["anchors"]], ["2"])
        self.assertEqual(len(selected["features"]), len(plan["features"]))
        excluded = next(
            feature for feature in selected["features"]
            if feature["target_id"] == "1" and feature["input_file"] == "S1.mzML"
        )
        included = next(
            feature for feature in selected["features"]
            if feature["target_id"] == "2" and feature["input_file"] == "S1.mzML"
        )
        self.assertFalse(excluded["used_for_rt_model"])
        self.assertTrue(included["used_for_rt_model"])
        self.assertAlmostEqual(included["corrected_rt_min"], 5.0)
        self.assertNotAlmostEqual(excluded["corrected_rt_min"], 1.0)

    def test_unknown_or_empty_compound_selection_is_rejected_or_ineligible(self) -> None:
        module = _load("rt_correction")
        plan = module.build_correction_plan(self._rows(), self._traces())

        with self.assertRaisesRegex(ValueError, "Unknown RT correction target"):
            module.select_correction_plan(plan, ["99"])

        selected = module.select_correction_plan(plan, [])
        self.assertFalse(selected["can_apply"])
        self.assertTrue(any("No internal standards" in reason for reason in selected["blocking_reasons"]))

    def test_cli_writes_versioned_plan_and_preview(self) -> None:
        module = _load("build_rt_correction_plan")
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            is_qc = root / "is-qc"
            out = root / "out"
            is_qc.mkdir()
            (is_qc / "is_qc_targets.tsv").write_text(
                "target_id\ttarget_name\tsample_name\tinput_file\tobserved_rt_min\tpassed\n"
                "1\tIS:A\tS1\tS1.mzML\t1.0\tTrue\n",
                encoding="utf-8",
            )
            (is_qc / "is_qc_traces.json").write_text(
                json.dumps({"samples": [{"sample_name": "S1", "input_file": "S1.mzML"}], "traces": []}),
                encoding="utf-8",
            )

            module.run(is_qc, out)

            plan = json.loads((out / "rt_correction_plan.json").read_text(encoding="utf-8"))
            preview = json.loads((out / "rt_correction_preview.json").read_text(encoding="utf-8"))
        self.assertEqual(plan["version"], "msdial_rt_correction_v1")
        self.assertEqual(preview["version"], "msdial_rt_correction_preview_v1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
