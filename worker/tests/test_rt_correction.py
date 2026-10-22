import importlib.util
import os
import unittest
from pathlib import Path


def _load_module():
    root = Path(os.environ.get("WORKFLOW_PIPELINE_ROOT", "workflows/nextflow4ms-dial-msdial5-clean"))
    path = root / "adapters" / "rt_correction.py"
    spec = importlib.util.spec_from_file_location("rt_correction", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class RtCorrectionTests(unittest.TestCase):
    def test_linear_curve_matches_msdial_default_boundaries(self) -> None:
        module = _load_module()
        anchors = [
            {"observed_rt_min": 2.0, "shift_min": 0.2},
            {"observed_rt_min": 6.0, "shift_min": -0.2},
        ]

        shifts = [module.interpolate_shift(rt, anchors) for rt in (0.0, 1.0, 2.0, 4.0, 6.0, 8.0)]

        self.assertEqual(shifts, [0.0, 0.1, 0.2, 0.0, -0.2, -0.2])
        self.assertAlmostEqual(module.correct_rt(2.0, anchors), 1.8)
        self.assertAlmostEqual(module.correct_rt(8.0, anchors), 8.2)

    def test_single_anchor_ramps_from_zero_then_holds_last_shift(self) -> None:
        module = _load_module()
        anchors = [{"observed_rt_min": 4.0, "shift_min": 0.4}]

        self.assertAlmostEqual(module.interpolate_shift(2.0, anchors), 0.2)
        self.assertAlmostEqual(module.interpolate_shift(8.0, anchors), 0.4)

    def test_corrected_axis_enforces_msdial_minimum_step(self) -> None:
        module = _load_module()
        anchors = [{"observed_rt_min": 1.0, "shift_min": 2.0}]

        corrected = module.correct_rt_axis([0.0, 0.5, 1.0], anchors)

        self.assertEqual(corrected, [0.00001, 0.00002, 0.00003])

    def test_empty_or_duplicate_anchor_axis_is_rejected(self) -> None:
        module = _load_module()
        with self.assertRaisesRegex(ValueError, "at least one"):
            module.interpolate_shift(1.0, [])
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            module.interpolate_shift(
                1.0,
                [
                    {"observed_rt_min": 1.0, "shift_min": 0.1},
                    {"observed_rt_min": 1.0, "shift_min": 0.2},
                ],
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
