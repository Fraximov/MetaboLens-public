import base64
import unittest
from unittest.mock import call, patch

from services.sample_service import (
    SAMPLE_IMPORT_TEMPLATE_COLUMNS,
    blank_sample_row,
    build_sample_metadata_patch,
    build_sample_payload,
    delete_study_sample,
    import_raw_folder,
    parse_sample_import_csv,
    sample_import_template_csv,
    update_sample_metadata_rows,
)


class SampleServiceTests(unittest.TestCase):
    def test_sample_import_template_csv_uses_flat_editor_columns(self) -> None:
        template = sample_import_template_csv()

        self.assertEqual(template, ",".join(SAMPLE_IMPORT_TEMPLATE_COLUMNS) + "\n")

    def test_blank_sample_row_defaults_batch_id_to_one(self) -> None:
        self.assertEqual(blank_sample_row()["batch_id"], "1")

    def test_parse_sample_import_csv_normalizes_rows(self) -> None:
        csv_text = (
            "existing_sample_id,sample_code,description,replicate_number,dilution_factor,sample_type,organism,group_label,batch_id,injection_order,source_path\n"
            ",S1,Serum sample,2,1.5,QC,,Lean,2,7,/data/raw/S1.raw\n"
        )
        contents = "data:text/csv;base64," + base64.b64encode(csv_text.encode("utf-8")).decode("ascii")

        rows = parse_sample_import_csv(contents)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sample_code"], "S1")
        self.assertEqual(rows[0]["sample_type"], "qc")
        self.assertEqual(rows[0]["organism"], "Homo sapiens")
        self.assertEqual(rows[0]["replicate_number"], 2)
        self.assertEqual(rows[0]["dilution_factor"], 1.5)
        self.assertEqual(rows[0]["group_label"], "Lean")
        self.assertEqual(rows[0]["batch_id"], "2")
        self.assertEqual(rows[0]["injection_order"], 7)

    def test_parse_sample_import_csv_rejects_missing_columns(self) -> None:
        csv_text = "sample_code,source_path\nS1,/data/raw/S1.raw\n"
        contents = "data:text/csv;base64," + base64.b64encode(csv_text.encode("utf-8")).decode("ascii")

        with self.assertRaisesRegex(ValueError, "missing required columns"):
            parse_sample_import_csv(contents)

    def test_build_sample_payload_reuses_existing_sample_id(self) -> None:
        payload = build_sample_payload(
            [
                {
                    "existing_sample_id": 7,
                    "group_label": "Lean",
                    "batch_id": "2",
                    "injection_order": 7,
                    "source_path": "/data/raw/S1.raw",
                }
            ]
        )

        self.assertEqual(
            payload,
            [
                {
                    "existing_sample_id": 7,
                    "annotations_json": {"group_label": "Lean", "batch_id": "2", "injection_order": 7},
                    "raw_file": {"source_path": "/data/raw/S1.raw"},
                }
            ],
        )

    def test_build_sample_payload_creates_new_sample_when_no_existing_sample_id(self) -> None:
        payload = build_sample_payload(
            [
                {
                    "sample_code": "S1",
                    "description": "Serum sample",
                    "sample_type": "qc",
                    "organism": "Homo sapiens",
                    "group_label": "Lean",
                    "batch_id": "2",
                    "injection_order": 7,
                    "source_path": "/data/raw/S1.raw",
                }
            ]
        )

        self.assertEqual(payload[0]["sample"]["name"], "S1")
        self.assertEqual(payload[0]["sample"]["sample_type"], "qc")
        self.assertEqual(payload[0]["annotations_json"], {"group_label": "Lean", "batch_id": "2", "injection_order": 7})
        self.assertEqual(payload[0]["raw_file"]["source_path"], "/data/raw/S1.raw")

    @patch("services.sample_service.ensure_draft_study")
    @patch("services.sample_service.call_api")
    def test_import_raw_folder_calls_assay_endpoint(self, call_api_mock, ensure_draft_mock) -> None:
        call_api_mock.return_value = {"imported_count": 2, "skipped_existing_count": 1}

        result = import_raw_folder("token", 4, 9, " /raw_test ")

        ensure_draft_mock.assert_called_once_with("token", 4)
        call_api_mock.assert_called_once_with(
            "POST",
            "/assays/9/raw-paths/import",
            token="token",
            payload={"folder_path": "/raw_test"},
            timeout_seconds=120,
        )
        self.assertEqual(result["imported_count"], 2)

    def test_import_raw_folder_rejects_empty_folder(self) -> None:
        with self.assertRaisesRegex(ValueError, "Folder path is required"):
            import_raw_folder("token", 4, 9, " ")

    @patch("services.sample_service.ensure_draft_study")
    @patch("services.sample_service.call_api")
    def test_delete_study_sample_calls_study_endpoint(self, call_api_mock, ensure_draft_mock) -> None:
        call_api_mock.return_value = {"deleted_sample_id": 8, "deleted_raw_file_count": 1, "cleared_file_sample_count": 0}

        result = delete_study_sample("token", 4, 8)

        ensure_draft_mock.assert_called_once_with("token", 4)
        call_api_mock.assert_called_once_with("DELETE", "/studies/4/samples/8", token="token")
        self.assertEqual(result["deleted_sample_id"], 8)

    @patch("services.sample_service.ensure_sample_details_editable")
    @patch("services.sample_service.call_api")
    def test_update_sample_metadata_rows_skips_stale_sample_ids(self, call_api_mock, ensure_draft_mock) -> None:
        call_api_mock.side_effect = [
            [{"id": 12, "name": "Current"}],
            {"id": 12},
        ]

        result = update_sample_metadata_rows(
            "token",
            4,
            [
                {"sample_id": 11, "matrix": "plasma", "group_label": "Lean", "batch_id": "1", "injection_order": "1"},
                {"sample_id": 12, "matrix": "serum", "group_label": "Obese", "batch_id": "1", "injection_order": "2"},
            ],
        )

        ensure_draft_mock.assert_called_once_with("token", 4)
        self.assertEqual(result, {"updated_count": 1, "skipped_missing_count": 1})
        self.assertEqual(
            call_api_mock.mock_calls,
            [
                call("GET", "/studies/4/samples", token="token"),
                call(
                    "PATCH",
                    "/studies/4/samples/12",
                    token="token",
                    payload={
                        "sample_type": "sample",
                        "matrix": "serum",
                        "annotations_json": {"group_label": "Obese"},
                    },
                ),
            ],
        )

    def test_build_sample_metadata_patch_normalizes_downstream_fields(self) -> None:
        payload = build_sample_metadata_patch("samples", " plasma ", " Lean ")

        self.assertEqual(
            payload,
            {
                "sample_type": "sample",
                "matrix": "plasma",
                "annotations_json": {
                    "group_label": "Lean",
                },
            },
        )

    def test_sample_patch_contains_no_acquisition_fields(self) -> None:
        payload = build_sample_metadata_patch("sample", "plasma", "Lean")
        self.assertEqual(payload["annotations_json"], {"group_label": "Lean"})

    def test_sample_details_stay_editable_after_a_run_but_not_when_archived(self) -> None:
        from services.study_service import ensure_sample_details_editable
        with patch("services.study_service.get_study_status", return_value="processing"):
            ensure_sample_details_editable("token", 4)
        with patch("services.study_service.get_study_status", return_value="archived"), \
                self.assertRaisesRegex(ValueError, "archived"):
            ensure_sample_details_editable("token", 4)


if __name__ == "__main__":
    unittest.main()
