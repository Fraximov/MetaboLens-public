import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.models.models import DatasetFile
from services.input_stager import InputStager
from services.samplesheet_builder import SamplesheetBuilder


class InputStagerTests(unittest.TestCase):
    def test_frozen_file_list_must_match_selected_inputs(self):
        with TemporaryDirectory() as temp:
            source = Path(temp) / "sample.raw"
            source.write_bytes(b"RAW")
            file = self._dataset_file(1, source)
            for frozen in ([], [{'file_asset_id': 2}], [{'file_asset_id': 1}, {'file_asset_id': 1}]):
                with self.subTest(frozen=frozen), self.assertRaisesRegex(ValueError, 'identities'):
                    InputStager().stage_raw_input_files([file], Path(temp) / "staged", frozen_files=frozen)

    def test_changed_registered_uri_cannot_replace_the_frozen_input(self):
        with TemporaryDirectory() as temp:
            source = Path(temp) / "replacement.raw"
            source.write_bytes(b"RAW")
            with self.assertRaisesRegex(ValueError, 'storage URI changed'):
                InputStager().stage_one_file(self._dataset_file(1, source), Path(temp) / "staged",
                    frozen_identity={'file_asset_id': 1, 'storage_uri': (Path(temp) / 'original.raw').as_uri()})

    def test_stage_raw_input_files_preserves_selected_order(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            raw_dir = root / "raw"
            staged_dir = root / "staged"
            raw_dir.mkdir()
            staged_dir.mkdir()

            first_source = raw_dir / "B.raw"
            second_source = raw_dir / "A.raw"
            first_source.write_bytes(b"B")
            second_source.write_bytes(b"A")

            selected_files = [
                self._dataset_file(20, first_source),
                self._dataset_file(10, second_source),
            ]

            staged = InputStager().stage_raw_input_files(selected_files, staged_dir)

            self.assertEqual([item["file_asset_id"] for item in staged], [20, 10])
            self.assertEqual((staged_dir / "B.raw").read_bytes(), b"B")
            self.assertEqual((staged_dir / "A.raw").read_bytes(), b"A")
            self.assertEqual(staged[0]["staged_path"], str(staged_dir / "B.raw"))

    def test_stage_raw_input_files_disambiguates_duplicate_filenames(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            first_dir = root / "first"
            second_dir = root / "second"
            staged_dir = root / "staged"
            first_dir.mkdir()
            second_dir.mkdir()
            staged_dir.mkdir()

            first_source = first_dir / "Sample.raw"
            second_source = second_dir / "Sample.raw"
            first_source.write_bytes(b"FIRST")
            second_source.write_bytes(b"SECOND")

            selected_files = [
                self._dataset_file(20, first_source),
                self._dataset_file(21, second_source),
            ]

            staged = InputStager().stage_raw_input_files(selected_files, staged_dir)

            self.assertEqual((staged_dir / "Sample.raw").read_bytes(), b"FIRST")
            self.assertEqual((staged_dir / "21_Sample.raw").read_bytes(), b"SECOND")
            self.assertEqual(staged[0]["original_filename"], "Sample.raw")
            self.assertEqual(staged[1]["original_filename"], "Sample.raw")
            self.assertEqual(staged[1]["staged_path"], str(staged_dir / "21_Sample.raw"))

    def test_stage_one_file_creates_staging_directory(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_path = root / "raw" / "Sample.raw"
            staged_dir = root / "missing" / "staged"
            source_path.parent.mkdir()
            source_path.write_bytes(b"RAW")

            staged = InputStager().stage_one_file(self._dataset_file(20, source_path), staged_dir)

            self.assertEqual((staged_dir / "Sample.raw").read_bytes(), b"RAW")
            self.assertEqual(staged["staged_path"], str(staged_dir / "Sample.raw"))

    def test_stage_one_file_replaces_stale_target_directory(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_path = root / "raw" / "Sample.raw"
            staged_dir = root / "staged"
            stale_target = staged_dir / "Sample.raw"
            source_path.parent.mkdir()
            staged_dir.mkdir()
            stale_target.mkdir()
            (stale_target / "partial").write_text("stale", encoding="utf-8")
            source_path.write_bytes(b"RAW")

            InputStager().stage_one_file(self._dataset_file(20, source_path), staged_dir)

            self.assertEqual(stale_target.read_bytes(), b"RAW")

    def test_stage_one_file_reports_context_and_removes_partial_temp_file(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_path = root / "raw" / "Sample.raw"
            staged_dir = root / "staged"
            target_path = staged_dir / "Sample.raw"
            temp_path = staged_dir / ".Sample.raw.tmp"
            source_path.parent.mkdir()
            staged_dir.mkdir()
            source_path.write_bytes(b"RAW")

            def interrupted_copy(source: Path, target: Path) -> None:
                Path(target).write_bytes(b"partial")
                raise OSError(20, "Not a directory")

            with patch("services.input_stager.shutil.copy2", side_effect=interrupted_copy):
                with self.assertRaises(ValueError) as ctx:
                    InputStager().stage_one_file(self._dataset_file(99, source_path), staged_dir)

            message = str(ctx.exception)
            self.assertIn("Failed to stage workflow input 99", message)
            self.assertIn(str(source_path), message)
            self.assertIn(str(target_path), message)
            self.assertIn("Not a directory", message)
            self.assertFalse(temp_path.exists())
            self.assertFalse(target_path.exists())

    def test_stage_one_file_rejects_short_copy(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_path = root / "raw" / "Sample.raw"
            staged_dir = root / "staged"
            temp_path = staged_dir / ".Sample.raw.tmp"
            source_path.parent.mkdir()
            staged_dir.mkdir()
            source_path.write_bytes(b"RAW")

            def short_copy(source: Path, target: Path) -> None:
                Path(target).write_bytes(b"R")

            with patch("services.input_stager.shutil.copy2", side_effect=short_copy):
                with self.assertRaises(ValueError) as ctx:
                    InputStager().stage_one_file(self._dataset_file(100, source_path), staged_dir)

            self.assertIn("staged file size mismatch", str(ctx.exception))
            self.assertFalse(temp_path.exists())

    def test_resolve_file_asset_path_rejects_non_local_uri(self) -> None:
        file_asset = self._dataset_file(1, Path("remote.raw"), storage_uri="s3://bucket/remote.raw", source_scheme="s3")

        with self.assertRaises(ValueError) as ctx:
            InputStager.resolve_file_asset_path(file_asset)

        self.assertIn("Only local file-backed workflow inputs are supported", str(ctx.exception))

    @staticmethod
    def _dataset_file(
        file_id: int,
        source_path: Path,
        *,
        storage_uri: str | None = None,
        source_scheme: str = "file",
    ) -> DatasetFile:
        return DatasetFile(
            id=file_id,
            dataset_id=1,
            sample_id=None,
            file_role="thermo_raw",
            original_filename=source_path.name,
            storage_uri=storage_uri or f"file://{source_path}",
            source_scheme=source_scheme,
            ingest_mode="registered_path",
            checksum_sha256="a" * 64,
            file_size_bytes=1,
            media_type="application/octet-stream",
            status="registered",
            created_by=1,
        )


class SamplesheetBuilderTests(unittest.TestCase):
    def test_write_preserves_the_exact_staged_filename_and_file_identity(self) -> None:
        with TemporaryDirectory() as temp:
            builder = SamplesheetBuilder(None)
            rows = [{"sample_name": "Same sample", "file_asset_id": 1}, {"sample_name": "Same sample", "file_asset_id": 2}]
            staged = [{"file_asset_id": 2, "staged_path": "/inputs/2_A.raw"}, {"file_asset_id": 1, "staged_path": "/inputs/A.raw"}]
            with patch.object(builder, "build_rows", return_value=rows):
                result = builder.write(selected_files=[], reference_file_asset_id=1,
                                       output_path=Path(temp) / "samplesheet.csv", staged_files=staged)
            self.assertEqual([row["staged_filename"] for row in result.rows], ["A.raw", "2_A.raw"])
            self.assertEqual([row["acquisition_id"] for row in result.rows], ["file-1", "file-2"])
            self.assertIn("staged_filename", result.fieldnames)

    def test_flatten_annotations_keeps_stats_fields_and_scalar_extras(self) -> None:
        flattened = SamplesheetBuilder.flatten_annotations(
            {
                "group_label": "Lean",
                "batch_id": 3,
                "injection_order": 12,
                "plate": "A",
                "is_reinjection": False,
                "nested": {"ignored": True},
                "binary": b"ignored",
            }
        )

        self.assertEqual(
            flattened,
            {
                "group_label": "Lean",
                "batch_id": 3,
                "injection_order": 12,
                "annotation__is_reinjection": False,
                "annotation__plate": "A",
            },
        )

    def test_derive_fieldnames_keeps_core_order_and_appends_used_annotations(self) -> None:
        fieldnames = SamplesheetBuilder.derive_fieldnames(
            [
                {
                    "sample_id": 1,
                    "sample_name": "Sample A",
                    "file_asset_id": 10,
                    "input_filename": "SampleA.raw",
                    "storage_uri": "file:///raw/SampleA.raw",
                    "sample_type": "sample",
                    "organism": "",
                    "matrix": "",
                    "replicate_number": "",
                    "dilution_factor": "",
                    "is_reference": "true",
                    "group_label": "Lean",
                    "annotation__plate": "A",
                }
            ]
        )

        self.assertEqual(
            fieldnames,
            [
                "sample_id",
                "sample_name",
                "file_asset_id",
                "input_filename",
                "storage_uri",
                "sample_type",
                "organism",
                "matrix",
                "replicate_number",
                "dilution_factor",
                "is_reference",
                "group_label",
                "annotation__plate",
            ],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)


class FrozenAcquisitionSamplesheetTests(unittest.TestCase):
    def test_cleared_optional_acquisition_values_can_be_written(self):
        from types import SimpleNamespace
        with TemporaryDirectory() as temp:
            path = Path(temp) / "sheet.csv"
            SamplesheetBuilder(None).write(selected_files=[SimpleNamespace(id=11)], reference_file_asset_id=11,
                output_path=path, frozen_rows=[{"file_asset_id": 11, "sample_id": 1, "annotation__vial": ""}])
            self.assertTrue(path.is_file())

    def test_worker_writes_frozen_metadata_and_stable_file_identities(self):
        import csv
        from types import SimpleNamespace
        with TemporaryDirectory() as temp:
            path = Path(temp) / "samplesheet.csv"
            selected = [SimpleNamespace(id=11), SimpleNamespace(id=12)]
            frozen = [{"file_asset_id": 11, "sample_id": 1, "sample_name": "S1", "batch_id": "1", "injection_order": 1},
                      {"file_asset_id": 12, "sample_id": 1, "sample_name": "S1", "batch_id": "2", "injection_order": 7}]
            SamplesheetBuilder(None).write(selected_files=selected, reference_file_asset_id=11,
                output_path=path, frozen_rows=frozen, staged_files=[
                    {"file_asset_id": 11, "staged_path": "/staged/file-11.raw"},
                    {"file_asset_id": 12, "staged_path": "/staged/file-12.raw"}])
            with path.open() as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual([r["sample_id"] for r in rows], ["1", "1"])
            self.assertEqual([r["injection_order"] for r in rows], ["1", "7"])
            self.assertEqual([r["acquisition_id"] for r in rows], ["file-11", "file-12"])
            self.assertNotIn("staged_filename", frozen[0])
            with self.assertRaises(ValueError):
                SamplesheetBuilder(None).write(selected_files=selected, reference_file_asset_id=11,
                    output_path=path, frozen_rows=[frozen[0], frozen[0]])
