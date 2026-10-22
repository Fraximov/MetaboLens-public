import json
import struct
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from app.services.msdial_eic_reader import MsdialEicArchiveReader
from app.services.workflow_eic_service import WorkflowEicService


def _spot_bytes(rt: float, mz: float, peaks: list[tuple[int, float, float, float, list[tuple[float, float]]]]) -> bytes:
    payload = bytearray()
    payload.extend(struct.pack("<ffffBi", rt, 0.0, mz, -1.0, 0, len(peaks)))
    for file_id, top, left, right, points in peaks:
        payload.extend(struct.pack("<iifff", file_id, len(points), top, left, right))
        for x, intensity in points:
            payload.extend(struct.pack("<ff", x, intensity))
    return bytes(payload)


def _write_css1_archive(path: Path, spots: list[bytes]) -> None:
    header_size = 10 + 4 + len(spots) * 8
    pointers = []
    offset = header_size
    for spot in spots:
        pointers.append(offset)
        offset += len(spot)

    payload = bytearray(b"CSS1\x00\x00\x00\x00\x00\x00")
    payload.extend(struct.pack("<i", len(spots)))
    for pointer in pointers:
        payload.extend(struct.pack("<q", pointer))
    for spot in spots:
        payload.extend(spot)
    path.write_bytes(bytes(payload))


class MsdialEicArchiveReaderTests(unittest.TestCase):
    def test_reads_one_feature_without_loading_other_spots_into_result(self) -> None:
        with TemporaryDirectory() as tmpdir:
            archive_path = Path(tmpdir) / "AlignResult-test.EIC.aef"
            _write_css1_archive(
                archive_path,
                [
                    _spot_bytes(4.2, 101.1, [(0, 4.2, 4.0, 4.4, [(4.0, 10.0), (4.1, 20.0), (4.2, 30.0)])]),
                    _spot_bytes(7.5, 202.2, [(0, 7.5, 7.2, 7.8, [(7.2, 100.0), (7.5, 200.0)])]),
                ],
            )

            reader = MsdialEicArchiveReader(archive_path)
            self.assertEqual(reader.read_summary(), {"version": "CSS1", "feature_count": 2})

            spot = reader.read_spot(1)
            self.assertEqual(spot["version"], "CSS1")
            self.assertEqual(spot["feature_count"], 2)
            self.assertAlmostEqual(spot["center"]["rt"], 7.5)
            self.assertAlmostEqual(spot["center"]["mz"], 202.2, places=3)
            self.assertEqual(spot["trace_count"], 1)
            self.assertEqual(spot["peaks"][0]["file_id"], 0)
            self.assertEqual(spot["peaks"][0]["point_count"], 2)
            self.assertEqual(spot["peaks"][0]["returned_point_count"], 2)

    def test_downsamples_long_traces(self) -> None:
        with TemporaryDirectory() as tmpdir:
            archive_path = Path(tmpdir) / "AlignResult-test.EIC.aef"
            points = [(float(index), float(index * 10)) for index in range(10)]
            _write_css1_archive(archive_path, [_spot_bytes(1.0, 100.0, [(0, 1.0, 0.0, 9.0, points)])])

            peak = MsdialEicArchiveReader(archive_path).read_spot(0, max_points_per_trace=3)["peaks"][0]
            self.assertEqual(peak["point_count"], 10)
            self.assertLessEqual(peak["returned_point_count"], 4)
            self.assertEqual(peak["points"][-1], {"x": 9.0, "intensity": 90.0})


class WorkflowEicFeatureParserTests(unittest.TestCase):
    def test_alignment_features_include_ms2_availability(self) -> None:
        with TemporaryDirectory() as tmpdir:
            alignment_path = Path(tmpdir) / "AlignResult-test.mdalign"
            alignment_path.write_text(
                "Header before table\n"
                "Alignment ID\tAverage Rt(min)\tAverage Mz\tMetabolite name\tAdduct type\tMS/MS assigned\tMS/MS spectrum\n"
                "7\t4.25\t123.45678\tKnown feature\t[M+H]+\tTrue\t43.0:100 55.0:200\n"
                "8\t5.50\t234.56789\tUnknown\t[M+Na]+\tFalse\tnull\n",
                encoding="utf-8",
            )

            features = WorkflowEicService._read_alignment_features(alignment_path)

        self.assertEqual(features[0]["feature_index"], 0)
        self.assertEqual(features[0]["alignment_id"], 7)
        self.assertAlmostEqual(features[0]["average_rt"], 4.25)
        self.assertAlmostEqual(features[0]["average_mz"], 123.45678)
        self.assertTrue(features[0]["annotated"])
        self.assertTrue(features[0]["has_ms2"])
        self.assertFalse(features[1]["annotated"])
        self.assertFalse(features[1]["has_ms2"])

    def test_alignment_features_treat_non_empty_ms2_assigned_as_available(self) -> None:
        with TemporaryDirectory() as tmpdir:
            alignment_path = Path(tmpdir) / "AlignResult-test.mdalign"
            alignment_path.write_text(
                "Alignment ID\tAverage Rt(min)\tAverage Mz\tMetabolite name\tAdduct type\tMS/MS assigned\tMS/MS spectrum\n"
                "1\t1.25\t99.123\tKnown\t[M+H]+\tAssigned\t\n",
                encoding="utf-8",
            )

            features = WorkflowEicService._read_alignment_features(alignment_path)

        self.assertTrue(features[0]["has_ms2"])

    def test_alignment_features_default_to_no_ms2_when_columns_are_missing(self) -> None:
        with TemporaryDirectory() as tmpdir:
            alignment_path = Path(tmpdir) / "AlignResult-test.mdalign"
            alignment_path.write_text(
                "Alignment ID\tAverage Rt(min)\tAverage Mz\tMetabolite name\tAdduct type\n"
                "1\t1.25\t99.123\tUnknown\t[M+H]+\n",
                encoding="utf-8",
            )

            features = WorkflowEicService._read_alignment_features(alignment_path)

        self.assertEqual(len(features), 1)
        self.assertFalse(features[0]["has_ms2"])


class WorkflowEicMs2Tests(unittest.TestCase):
    ALIGNMENT_HEADER = (
        "Alignment ID\tAverage Rt(min)\tAverage Mz\tMetabolite name\tAdduct type\tMS/MS assigned\t"
        "Reference RT\tReference m/z\tINCHIKEY\tRT matched\tm/z matched\tMS/MS matched\t"
        "Weighted dot product\tReverse dot product\tMatched peaks percentage\tTotal score\tMS/MS spectrum\n"
    )

    def _alignment(self, tmpdir: str, rows: str) -> Path:
        path = Path(tmpdir) / "AlignResult-test.mdalign"
        path.write_text("Preamble line\n" + self.ALIGNMENT_HEADER + rows, encoding="utf-8")
        return path

    def test_single_row_read_keeps_the_index_contract_across_blank_rows(self) -> None:
        """A blank row must be skipped identically by both readers.

        If they disagree, a feature's chromatogram and its spectrum come from
        different rows and nothing in the payload says so.
        """
        rows = (
            "1\t1.0\t100.0\tFirst\t[M+H]+\tTrue\t1.1\t100.1\tAAAAAAAAAAAAAA-UHFFFAOYAA\tTrue\tTrue\tTrue\t0.9\t0.8\t75.0\t2.5\t50.0:10\n"
            "\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\n"
            "2\t2.0\t200.0\tSecond\t[M+H]+\tTrue\t2.1\t200.1\tBBBBBBBBBBBBBB-UHFFFAOYAA\tTrue\tFalse\tFalse\t0.4\t0.3\t10.0\t1.0\t60.0:20\n"
        )
        with TemporaryDirectory() as tmpdir:
            path = self._alignment(tmpdir, rows)
            features = WorkflowEicService._read_alignment_features(path)
            self.assertEqual([feature["alignment_id"] for feature in features], [1, 2])
            for index, expected_id in enumerate([1, 2]):
                header, row = WorkflowEicService._read_alignment_row(path, index)
                self.assertEqual(row[header.index("Alignment ID")], str(expected_id))
            self.assertIsNone(WorkflowEicService._read_alignment_row(path, 2))

    def test_features_carry_the_two_triage_flags(self) -> None:
        rows = (
            "1\t1.0\t100.0\tlow score: Caffeine\t[M+H]+\tTrue\t\t\t\tTrue\tTrue\tFalse\t\t\t\t\t50.0:10\n"
            "2\t2.0\t200.0\tCaffeine\t[M+H]+\tTrue\t\t\t\tTrue\tTrue\tTrue\t\t\t\t\t60.0:20\n"
        )
        with TemporaryDirectory() as tmpdir:
            features = WorkflowEicService._read_alignment_features(self._alignment(tmpdir, rows))
        self.assertTrue(features[0]["low_score"])
        self.assertFalse(features[0]["msms_matched"])
        self.assertFalse(features[1]["low_score"])
        self.assertTrue(features[1]["msms_matched"])

    def test_search_flags_select_the_triage_cases(self) -> None:
        named_unconfirmed = {"annotated": True, "has_ms2": True, "msms_matched": False, "low_score": True, "label": "x"}
        confirmed = {"annotated": True, "has_ms2": True, "msms_matched": True, "low_score": False, "label": "x"}
        self.assertTrue(WorkflowEicService._feature_matches_search(named_unconfirmed, ["unmatched"]))
        self.assertFalse(WorkflowEicService._feature_matches_search(confirmed, ["unmatched"]))
        self.assertTrue(WorkflowEicService._feature_matches_search(named_unconfirmed, ["lowscore"]))
        self.assertFalse(WorkflowEicService._feature_matches_search(confirmed, ["lowscore"]))
        # A flag still composes with ordinary text search.
        self.assertFalse(WorkflowEicService._feature_matches_search(named_unconfirmed, ["unmatched", "zzz"]))

    def test_reference_search_word_selects_features_with_a_library_entry(self) -> None:
        """The reference half is rare, so it needs its own way to be found."""
        with_reference = {"annotated": True, "has_ms2": True, "has_reference": True, "label": "x"}
        without_reference = {"annotated": True, "has_ms2": True, "has_reference": False, "label": "x"}
        self.assertTrue(WorkflowEicService._feature_matches_search(with_reference, ["reference"]))
        self.assertFalse(WorkflowEicService._feature_matches_search(without_reference, ["reference"]))

    def test_skeletons_are_indexed_by_feature_not_just_collected(self) -> None:
        """The per-feature list and the run's set come from one pass and must line up."""
        rows = (
            "1\t1.0\t100.0\tFirst\t[M+H]+\tTrue\t\t\tAAAAAAAAAAAAAA-UHFFFAOYAA\t\t\t\t\t\t\t\t50.0:10\n"
            "2\t2.0\t200.0\tUnknown\t[M+H]+\tFalse\t\t\t\t\t\t\t\t\t\t\t\n"
            "3\t3.0\t300.0\tThird\t[M+H]+\tTrue\t\t\tBBBBBBBBBBBBBB-UHFFFAOYSA-N\t\t\t\t\t\t\t\t70.0:30\n"
        )
        with TemporaryDirectory() as tmpdir:
            path = self._alignment(tmpdir, rows)
            by_index = WorkflowEicService._skeletons_by_index_for_alignment(path)
            self.assertEqual(by_index, ["AAAAAAAAAAAAAA", None, "BBBBBBBBBBBBBB"])

    def test_peak_list_parsing_ignores_malformed_tokens(self) -> None:
        peaks = WorkflowEicService._parse_peak_list("43.01777:1525746 nonsense 60.03400:19029 55.0: :7")
        self.assertEqual(peaks, [(43.01777, 1525746.0), (60.034, 19029.0)])
        self.assertEqual(WorkflowEicService._parse_peak_list(None), [])

    def test_inchikey_skeleton_joins_truncated_alignment_keys_to_full_library_keys(self) -> None:
        """MS-DIAL writes a truncated key that never equals a library's full key."""
        self.assertEqual(
            WorkflowEicService._inchikey_skeleton("GETQZCLCWQTVFV-UHFFFAOYAA"),
            WorkflowEicService._inchikey_skeleton("GETQZCLCWQTVFV-UHFFFAOYSA-N"),
        )
        self.assertIsNone(WorkflowEicService._inchikey_skeleton("short-XX"))
        self.assertIsNone(WorkflowEicService._inchikey_skeleton(""))

    LIBRARY_BYTES = (
        b"Name: Wanted\r\nPrecursorMz: 103.1230\r\nInChiKey: AAAAAAAAAAAAAA-UHFFFAOYSA-N\r\n"
        b"Num peaks: 2\r\n41.03851\t1.48\r\n86.09634\t100\r\n\r\n"
        b"NAME: Upper\r\nPRECURSORMZ: 55.5\r\nINCHIKEY: BBBBBBBBBBBBBB-UHFFFAOYSA-N\r\n"
        b"Num peaks: 1\r\n70.0\t50\r\n"
    )

    def test_library_index_locates_each_compound_and_reads_both_header_spellings(self) -> None:
        """The merged library concatenates sources, so both spellings appear in one file."""
        with TemporaryDirectory() as tmpdir:
            library = Path(tmpdir) / "library.msp"
            library.write_bytes(self.LIBRARY_BYTES)

            self.assertEqual(sorted(WorkflowEicService._library_index(library)), ["AAAAAAAAAAAAAA", "BBBBBBBBBBBBBB"])

            entry = WorkflowEicService._library_entry(library, "AAAAAAAAAAAAAA")
            self.assertEqual(entry["name"], "Wanted")
            self.assertAlmostEqual(entry["precursor_mz"], 103.1230)
            self.assertEqual(entry["peaks"], [(41.03851, 1.48), (86.09634, 100.0)])

            upper = WorkflowEicService._library_entry(library, "BBBBBBBBBBBBBB")
            self.assertEqual(upper["name"], "Upper")
            self.assertEqual(upper["peaks"], [(70.0, 50.0)])

            self.assertIsNone(WorkflowEicService._library_entry(library, "CCCCCCCCCCCCCC"))
            self.assertIsNone(WorkflowEicService._library_entry(library, None))

    def test_library_entry_falls_through_an_entry_that_carries_no_peaks(self) -> None:
        """170 of the merged library's 291,783 entries have none."""
        with TemporaryDirectory() as tmpdir:
            library = Path(tmpdir) / "library.msp"
            library.write_bytes(
                b"Name: Empty\r\nInChiKey: AAAAAAAAAAAAAA-UHFFFAOYSA-N\r\nNum peaks: 0\r\n\r\n"
                b"Name: Real\r\nInChiKey: AAAAAAAAAAAAAA-UHFFFAOYSA-N\r\nNum peaks: 1\r\n99.0\t7\r\n"
            )
            entry = WorkflowEicService._library_entry(library, "AAAAAAAAAAAAAA")
            self.assertEqual(entry["name"], "Real")
            self.assertEqual(entry["peaks"], [(99.0, 7.0)])

    def test_library_index_handles_an_empty_file(self) -> None:
        with TemporaryDirectory() as tmpdir:
            library = Path(tmpdir) / "empty.msp"
            library.write_bytes(b"")
            self.assertEqual(WorkflowEicService._library_index(library), {})

    def test_mirror_peaks_normalise_to_a_hundred_and_flag_agreement(self) -> None:
        experimental = [(50.0, 10.0), (60.0, 100.0), (70.0, 20.0)]
        reference = [(50.005, 5.0), (80.0, 50.0)]
        experimental_out, reference_out = WorkflowEicService._mirror_peaks(experimental, reference)

        self.assertEqual(max(peak["intensity"] for peak in experimental_out), 100.0)
        self.assertEqual(max(peak["intensity"] for peak in reference_out), 100.0)
        # 50.005 is inside the tolerance of 50.0; 70 and 80 are explained by nothing.
        self.assertEqual([peak["matched"] for peak in experimental_out], [True, False, False])
        self.assertEqual([peak["matched"] for peak in reference_out], [True, False])

    def test_mirror_peaks_make_no_match_claim_without_a_reference(self) -> None:
        experimental_out, reference_out = WorkflowEicService._mirror_peaks([(50.0, 10.0)], [])
        self.assertEqual(reference_out, [])
        self.assertFalse(experimental_out[0]["matched"])

    def _contract(self, tmpdir: str, library_value: str | None) -> Path:
        run_root = Path(tmpdir) / "job_1"
        (run_root / "contract").mkdir(parents=True)
        libraries = {"ms2_library": library_value} if library_value is not None else {}
        (run_root / "contract" / "params.json").write_text(json.dumps({"libraries": libraries}), encoding="utf-8")
        return run_root

    def test_neutral_run_reads_its_frozen_ms2_library_parameter(self):
        with TemporaryDirectory() as tmpdir:
            run_root = self._contract(tmpdir, None)
            library = run_root / "reference.msp"
            library.write_text("Name: Test\n")
            (run_root / "contract" / "params.json").write_text(json.dumps({
                "engine": {"descriptor": {"adapter_id": "msdial5-v1"}},
                "nextflow_params": {"ms2_library": str(library)}}))
            service = WorkflowEicService.__new__(WorkflowEicService)
            service._library_root_for_job = lambda job: None
            self.assertEqual(service._ms2_library_path_for_job(SimpleNamespace(run_root=str(run_root))), library)

    def test_unknown_match_status_is_not_a_negative_match(self):
        feature = {"annotated": True, "has_ms2": True, "msms_matched": None}
        self.assertFalse(WorkflowEicService._feature_matches_search(feature, ["unmatched"]))

    def test_library_is_located_under_the_backend_root_when_the_contract_path_is_elsewhere(self) -> None:
        """The contract records the execution plane's path, which the backend cannot see.

        The pipeline is mounted at a different place in the backend container, so
        a recorded path that does not exist here is normal, not an error.
        """
        with TemporaryDirectory() as tmpdir:
            run_root = self._contract(tmpdir, "/execution/plane/only/libs/lib.msp")
            library_root = Path(tmpdir) / "libraries"
            (library_root / "nested").mkdir(parents=True)
            (library_root / "nested" / "lib.msp").write_text("Name: X\r\n", encoding="utf-8")

            service = WorkflowEicService.__new__(WorkflowEicService)
            service._library_root_for_job = lambda job: library_root
            job = SimpleNamespace(run_root=str(run_root))

            self.assertEqual(service._ms2_library_path_for_job(job), library_root / "nested" / "lib.msp")

            service._library_root_for_job = lambda job: None
            self.assertIsNone(service._ms2_library_path_for_job(job))

    def test_library_resolution_gives_up_quietly_without_a_contract_entry(self) -> None:
        with TemporaryDirectory() as tmpdir:
            service = WorkflowEicService.__new__(WorkflowEicService)
            service._library_root_for_job = lambda job: Path(tmpdir)
            self.assertIsNone(service._ms2_library_path_for_job(SimpleNamespace(run_root=str(self._contract(tmpdir, None)))))
            self.assertIsNone(service._ms2_library_path_for_job(SimpleNamespace(run_root="")))
            self.assertIsNone(service._ms2_library_path_for_job(SimpleNamespace(run_root=str(Path(tmpdir) / "missing"))))

    def test_mirror_peaks_cap_keeps_the_strongest_in_mz_order(self) -> None:
        from app.services.workflow_eic_service import MS2_MAX_PEAKS

        experimental = [(float(index), float(index)) for index in range(MS2_MAX_PEAKS + 50)]
        capped, _ = WorkflowEicService._mirror_peaks(experimental, [])
        self.assertEqual(len(capped), MS2_MAX_PEAKS)
        self.assertEqual([peak["mz"] for peak in capped], sorted(peak["mz"] for peak in capped))
        self.assertEqual(capped[-1]["intensity"], 100.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
