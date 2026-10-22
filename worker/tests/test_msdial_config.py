import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from services.msdial_config import MsdialConfigRenderer


class MsdialConfigRendererTests(unittest.TestCase):
    def test_renderer_patches_library_and_raw_reference_filenames(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            template_path = root / "template.txt"
            output_path = root / "generated.txt"
            template_path.write_text(
                "MS-DIAL ver. 5.5\n"
                "MSP file path: old_ms2.msp\n"
                "Text DB file path: old_ms1.txt\n"
                "Alignment reference file ID: old_reference\n"
                "#Adduct ion setting\n"
                "Searched adduct ions: [M+H]+\n"
                "Unchanged line: yes\n",
                encoding="utf-8",
            )

            generation = MsdialConfigRenderer.render(
                template_path=template_path,
                output_path=output_path,
                ms1_library_path=root / "selected_ms1.txt",
                ms2_library_path=root / "selected_ms2.msp",
                reference_path=root / "sample_A.raw",
            )

            rendered = output_path.read_text(encoding="utf-8")
            self.assertIn("MSP file path: selected_ms2.msp\n", rendered)
            self.assertIn("Text DB file path: selected_ms1.txt\n", rendered)
            self.assertIn("Alignment reference file ID: sample_A\n", rendered)
            self.assertIn("Unchanged line: yes\n", rendered)
            self.assertEqual(generation.patched_fields["MSP file path"], "selected_ms2.msp")
            self.assertEqual(generation.patched_fields["Text DB file path"], "selected_ms1.txt")
            self.assertEqual(generation.patched_fields["Alignment reference file ID"], "sample_A")
            self.assertEqual(generation.alignment_reference_keys, ["Alignment reference file ID"])
            self.assertEqual(generation.effective_sections["adducts"]["Searched adduct ions"], "[M+H]+")

    def test_renderer_accepts_legacy_alignment_reference_name(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            template_path = root / "template.txt"
            output_path = root / "generated.txt"
            template_path.write_text(
                "MSP file path: old_ms2.msp\n"
                "Text DB file path: old_ms1.txt\n"
                "Alignment reference file name: old_reference\n",
                encoding="utf-8",
            )

            generation = MsdialConfigRenderer.render(
                template_path=template_path,
                output_path=output_path,
                ms1_library_path=root / "selected_ms1.txt",
                ms2_library_path=root / "selected_ms2.msp",
                reference_path=root / "sample_A.raw",
            )

            self.assertIn("Alignment reference file name: sample_A\n", output_path.read_text(encoding="utf-8"))
            self.assertEqual(generation.alignment_reference_keys, ["Alignment reference file name"])

    def test_renderer_patches_tab_separated_msdial_template_fields(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            template_path = root / "template.txt"
            output_path = root / "generated.txt"
            template_path.write_text(
                "MS-DIAL ver. 4.9.221218\n"
                "#MSP file and MS/MS identification setting\n"
                "MSP file\told_ms2.msp\n"
                "#Text file and post identification (retention time and accurate mass based) setting\n"
                "Text file\told_ms1.txt\n"
                "#Alignment parameters setting\n"
                "Reference file\told_reference.abf\n",
                encoding="utf-8",
            )

            generation = MsdialConfigRenderer.render(
                template_path=template_path,
                output_path=output_path,
                ms1_library_path=root / "selected_ms1.txt",
                ms2_library_path=root / "selected_ms2.msp",
                reference_path=root / "sample_A.raw",
            )

            rendered = output_path.read_text(encoding="utf-8")
            self.assertIn("MSP file\tselected_ms2.msp\n", rendered)
            self.assertIn("Text file\tselected_ms1.txt\n", rendered)
            self.assertIn("Reference file\tsample_A.mzML\n", rendered)
            self.assertEqual(generation.patched_fields["MSP file"], "selected_ms2.msp")
            self.assertEqual(generation.patched_fields["Text file"], "selected_ms1.txt")
            self.assertEqual(generation.patched_fields["Reference file"], "sample_A.mzML")
            self.assertEqual(generation.alignment_reference_keys, ["Reference file"])
            self.assertEqual(generation.effective_sections["msp_annotation"]["MSP file"], "selected_ms2.msp")

    def test_renderer_rejects_templates_missing_required_fields(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            template_path = root / "template.txt"
            output_path = root / "generated.txt"
            template_path.write_text("MSP file path: old_ms2.msp\n", encoding="utf-8")

            with self.assertRaises(ValueError) as ctx:
                MsdialConfigRenderer.render(
                    template_path=template_path,
                    output_path=output_path,
                    ms1_library_path=root / "selected_ms1.txt",
                    ms2_library_path=root / "selected_ms2.msp",
                    reference_path=root / "sample_A.raw",
                )

            self.assertIn("Text DB file path", str(ctx.exception))

    def test_renderer_rejects_templates_missing_alignment_reference_labels(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            template_path = root / "template.txt"
            output_path = root / "generated.txt"
            template_path.write_text(
                "MSP file path: old_ms2.msp\n"
                "Text DB file path: old_ms1.txt\n",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError) as ctx:
                MsdialConfigRenderer.render(
                    template_path=template_path,
                    output_path=output_path,
                    ms1_library_path=root / "selected_ms1.txt",
                    ms2_library_path=root / "selected_ms2.msp",
                    reference_path=root / "sample_A.raw",
                )

            self.assertIn("Alignment reference file ID, Alignment reference file name, or Reference file", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
