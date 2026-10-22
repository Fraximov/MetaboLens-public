import hashlib
import importlib.util
import os
import re
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


def _load_module():
    root = Path(os.environ.get("WORKFLOW_PIPELINE_ROOT", "workflows/nextflow4ms-dial-msdial5-clean"))
    path = root / "adapters" / "rewrite_mzml_rt.py"
    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("rewrite_mzml_rt", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _mzml(scan_values: list[str], *, unit_accession: str = "UO:0000031", checksum: str = "0") -> bytes:
    spectra = "".join(
        f'<spectrum id="scan={index}"><scanList><scan><cvParam accession="MS:1000016" value="{value}" unitAccession="{unit_accession}"/></scan></scanList><binaryDataArrayList count="0"/></spectrum>'
        for index, value in enumerate(scan_values, start=1)
    )
    prefix = f'<?xml version="1.0" encoding="ISO-8859-1"?><indexedmzML><mzML><run><spectrumList count="{len(scan_values)}">{spectra}</spectrumList></run></mzML>'
    offsets = [match.start() for match in re.finditer("<spectrum ", prefix)]
    index = '<indexList count="1"><index name="spectrum">' + "".join(
        f'<offset idRef="scan={number}">{offset}</offset>' for number, offset in enumerate(offsets, start=1)
    ) + "</index></indexList>"
    index_offset = len(prefix.encode("latin-1"))
    return (prefix + index + f"<indexListOffset>{index_offset}</indexListOffset><fileChecksum>{checksum}</fileChecksum></indexedmzML>").encode("latin-1")


class MzmlRtRewriterTests(unittest.TestCase):
    def test_rewrite_preserves_length_offsets_and_placeholder_checksum(self) -> None:
        module = _load_module()
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "source.mzML"
            output = root / "corrected.mzML"
            source.write_bytes(_mzml(["1.000000", "2.000000", "3.000000"]))
            original = source.read_bytes()

            with patch.object(module, "validate_with_pymzml"):
                report = module.rewrite_mzml_file(
                    source,
                    output,
                    [{"observed_rt_min": 2.0, "shift_min": 0.2}],
                )
            corrected = output.read_bytes()

            self.assertEqual(source.read_bytes(), original)
        self.assertEqual(len(corrected), len(original))
        self.assertEqual(report["spectrum_count"], 3)
        self.assertEqual(re.findall(rb"<offset[^>]*>([^<]+)", corrected), re.findall(rb"<offset[^>]*>([^<]+)", original))
        self.assertIn(b"<fileChecksum>0</fileChecksum>", corrected)
        self.assertEqual(module.scan_times_minutes(corrected), sorted(module.scan_times_minutes(corrected)))

    def test_seconds_are_corrected_in_minutes_then_written_in_seconds(self) -> None:
        module = _load_module()
        with TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "source.mzML"
            output = Path(tmpdir) / "corrected.mzML"
            source.write_bytes(_mzml(["60.000000", "120.000000"], unit_accession="UO:0000010"))

            with patch.object(module, "validate_with_pymzml"):
                module.rewrite_mzml_file(source, output, [{"observed_rt_min": 2.0, "shift_min": 0.2}])

            self.assertEqual(module.scan_times_minutes(output.read_bytes()), [0.9, 1.8])

    def test_valid_checksum_is_recalculated_at_same_width(self) -> None:
        module = _load_module()
        with TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "source.mzML"
            output = Path(tmpdir) / "corrected.mzML"
            source.write_bytes(_mzml(["1.000000", "2.000000"], checksum="0" * 40))

            with patch.object(module, "validate_with_pymzml"):
                module.rewrite_mzml_file(source, output, [{"observed_rt_min": 2.0, "shift_min": 0.2}])
            data = output.read_bytes()

        match = re.search(rb"<fileChecksum>([0-9a-f]{40})</fileChecksum>", data)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.group(1).decode(), hashlib.sha1(data[:match.start(1)]).hexdigest())

    def test_insufficient_numeric_width_fails_without_output(self) -> None:
        module = _load_module()
        with TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "source.mzML"
            output = Path(tmpdir) / "corrected.mzML"
            source.write_bytes(_mzml(["1", "2"]))

            with self.assertRaisesRegex(ValueError, "byte width"):
                module.rewrite_mzml_file(source, output, [{"observed_rt_min": 2.0, "shift_min": 0.123456}])

            self.assertFalse(output.exists())

    def test_directory_rewrite_requires_every_planned_sample(self) -> None:
        module = _load_module()
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            (input_dir / "S1.mzML").write_bytes(_mzml(["1.000000", "2.000000"]))
            plan = {
                "can_apply": True,
                "samples": [
                    {"input_file": "S1.mzML", "eligible": True, "anchors": [{"observed_rt_min": 2.0, "shift_min": 0.2}]},
                    {"input_file": "S2.mzML", "eligible": True, "anchors": [{"observed_rt_min": 2.0, "shift_min": 0.2}]},
                ],
            }

            with self.assertRaisesRegex(ValueError, "S2.mzML"):
                module.rewrite_input_directory(input_dir, plan, output_dir)

    def test_main_filters_plan_to_selected_target_ids_before_rewrite(self) -> None:
        module = _load_module()
        plan = {
            "can_apply": True,
            "samples": [
                {
                    "input_file": "S1.mzML",
                    "eligible": True,
                    "anchors": [
                        {"target_id": "1", "observed_rt_min": 1.0, "shift_min": 0.1},
                        {"target_id": "2", "observed_rt_min": 2.0, "shift_min": 0.2},
                    ],
                }
            ],
            "features": [
                {"target_id": "1", "input_file": "S1.mzML", "observed_rt_min": 1.0},
                {"target_id": "2", "input_file": "S1.mzML", "observed_rt_min": 2.0},
            ],
        }
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            plan_path = root / "plan.json"
            plan_path.write_text(__import__("json").dumps(plan), encoding="utf-8")
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "rewrite_mzml_rt.py",
                        "--input-dir", str(root / "input"),
                        "--plan", str(plan_path),
                        "--outdir", str(root / "output"),
                        "--target-ids-json", '["2"]',
                    ],
                ),
                patch.object(module, "rewrite_input_directory") as rewrite,
            ):
                module.main()

        selected_plan = rewrite.call_args.args[1]
        self.assertEqual(
            [anchor["target_id"] for anchor in selected_plan["samples"][0]["anchors"]],
            ["2"],
        )

    def test_real_indexed_fixture_remains_readable_and_source_is_immutable(self) -> None:
        if importlib.util.find_spec("pymzml") is None:
            self.skipTest("pymzML is available in the IS-QC container, not the host test environment")
        module = _load_module()
        source = Path(os.environ.get(
            "RT_CORRECTION_REAL_MZML",
            "runtime/msdial-debug/example_metabolomics1/input/Metabolomics_1.mzML",
        ))
        if not source.is_file():
            self.skipTest(f"real mzML fixture is unavailable: {source}")
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        original_times = module.scan_times_minutes(source.read_bytes())
        anchor_rt = original_times[len(original_times) // 2]
        with TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / source.name
            module.rewrite_mzml_file(
                source,
                output,
                [{"observed_rt_min": anchor_rt, "shift_min": 0.01}],
            )
            corrected_times = module.scan_times_minutes(output.read_bytes())
            self.assertEqual(output.stat().st_size, source.stat().st_size)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)
        self.assertEqual(len(corrected_times), len(original_times))
        self.assertTrue(all(right > left for left, right in zip(corrected_times, corrected_times[1:])))


if __name__ == "__main__":
    unittest.main(verbosity=2)
