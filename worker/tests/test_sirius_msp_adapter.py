import importlib.util
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[2]
ADAPTER_PATH = REPO_ROOT / "workflows" / "nextflow4ms-dial-msdial5-clean" / "adapters" / "prepare_sirius_msp.py"


def load_adapter():
    spec = importlib.util.spec_from_file_location("prepare_sirius_msp", ADAPTER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class SiriusMspAdapterTests(unittest.TestCase):
    def test_adapter_filters_empty_spectra_and_preserves_peak_ids(self) -> None:
        adapter = load_adapter()

        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "AlignResult-test.mdmsp"
            target = root / "sirius_input.msp"
            source.write_text(
                "NAME: keep\n"
                "PRECURSORMZ: 100.1\n"
                "PRECURSORTYPE: [M+H]+\n"
                "COMMENT: |PEAKID=1|ISOTOPE=M+0\n"
                "Num Peaks: 2\n"
                "50.0\t100\n"
                "75.0\t200\n"
                "\n"
                "NAME: drop\n"
                "PRECURSORMZ: 101.1\n"
                "PRECURSORTYPE: [M+H]+\n"
                "COMMENT: |PEAKID=2|ISOTOPE=M+0\n"
                "Num Peaks: 0\n"
                "\n",
                encoding="utf-8",
            )

            summary = adapter.prepare_sirius_msp(source, target)

            self.assertEqual(summary["records_read"], 2)
            self.assertEqual(summary["records_written"], 1)
            self.assertEqual(summary["records_skipped_empty"], 1)
            text = target.read_text(encoding="utf-8")
            self.assertIn("NAME: keep", text)
            self.assertIn("COMMENT: |PEAKID=1|ISOTOPE=M+0", text)
            self.assertNotIn("NAME: drop", text)


if __name__ == "__main__":
    unittest.main()
