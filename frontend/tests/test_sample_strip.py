import unittest

from services import sample_strip
from callbacks.job_workspace import sample_marks, sample_overview

SHEET = """sample_id,sample_name,file_asset_id,sample_type,group_label,batch_id,injection_order,is_reference
5,S3,105,sample,Filo,2,5,false
1,S1,101,sample,Cystic,1,1,false
3,S2,103,sample,Cystic,1,3,false
2,QC_01,102,qc,QC,1,2,true
4,Blank_01,104,blank,Blank,1,4,false
"""
SIZES = {"101": 150_000_000, "102": 148_000_000, "103": 60_000_000,
         "104": 140_000_000, "105": 152_000_000}


class ParsingTests(unittest.TestCase):
    def test_rows_are_sorted_into_acquisition_order(self):
        """The file is not written in injection order, so sorting by that column
        is what makes this a sequence rather than a list."""
        rows = sample_strip.parse_samplesheet(SHEET, SIZES)
        self.assertEqual([r["injection_order"] for r in rows], [1, 2, 3, 4, 5])
        self.assertEqual([r["name"] for r in rows], ["S1", "QC_01", "S2", "Blank_01", "S3"])

    def test_sizes_come_from_the_manifest_join(self):
        """The samplesheet has no file size and the manifest has no sample type;
        they share file_asset_id."""
        rows = sample_strip.parse_samplesheet(SHEET, SIZES)
        self.assertEqual(rows[0]["size_bytes"], 150_000_000)

    def test_without_the_join_there_is_simply_no_height_data(self):
        rows = sample_strip.parse_samplesheet(SHEET)
        self.assertTrue(all(r["size_bytes"] is None for r in rows))

    def test_manifest_sizes_are_keyed_by_asset_id(self):
        manifest = {"selected_files": [
            {"file_asset_id": 568, "file_size_bytes": 154593117},
            {"file_asset_id": 565, "file_size_bytes": None},
        ]}
        self.assertEqual(sample_strip.sizes_from_manifest(manifest), {"568": 154593117})

    def test_an_unknown_type_falls_into_a_slot_rather_than_vanishing(self):
        rows = sample_strip.parse_samplesheet(
            "sample_name,sample_type,injection_order\nX,calibrant,1\n")
        self.assertEqual(rows[0]["type"], "other")
        self.assertEqual(rows[0]["raw_type"], "calibrant")

    def test_junk_input_yields_no_samples(self):
        for text in [None, "", "   ", "not,a,samplesheet\n"]:
            with self.subTest(text=text):
                self.assertEqual(
                    [r for r in sample_strip.parse_samplesheet(text) if r["name"] != "?"], [])


class BatchQualityTests(unittest.TestCase):
    def test_a_truncated_acquisition_is_flagged(self):
        rows = sample_strip.parse_samplesheet(SHEET, SIZES)
        self.assertEqual([s["name"] for s in sample_strip.short_acquisitions(rows)], ["S2"])

    def test_a_consistent_run_flags_nothing(self):
        sizes = {k: 150_000_000 for k in SIZES}
        rows = sample_strip.parse_samplesheet(SHEET, sizes)
        self.assertEqual(sample_strip.short_acquisitions(rows), [])

    def test_the_median_is_used_so_one_short_file_cannot_hide_the_next(self):
        sizes = dict(SIZES, **{"103": 10_000_000, "105": 12_000_000})
        rows = sample_strip.parse_samplesheet(SHEET, sizes)
        self.assertEqual(sorted(s["name"] for s in sample_strip.short_acquisitions(rows)),
                         ["S2", "S3"])

    def test_too_few_files_to_judge(self):
        rows = sample_strip.parse_samplesheet(
            "sample_name,sample_type,file_asset_id,injection_order\nA,sample,1,1\n", {"1": 5})
        self.assertEqual(sample_strip.short_acquisitions(rows), [])

    def test_the_tally_keeps_the_fixed_slot_order(self):
        rows = sample_strip.parse_samplesheet(SHEET, SIZES)
        self.assertEqual(sample_strip.type_tally(rows),
                         [("sample", 3), ("qc", 1), ("blank", 1)])


class MarkTests(unittest.TestCase):
    def setUp(self):
        self.rows = sample_strip.parse_samplesheet(SHEET, SIZES)

    def test_height_encodes_size_against_the_largest_in_the_run(self):
        largest = max(s["size_bytes"] for s in self.rows)
        tallest = max(sample_strip.bar_height(s, largest) for s in self.rows)
        shortest = min(sample_strip.bar_height(s, largest) for s in self.rows)
        self.assertEqual(tallest, sample_strip.BAR_MAX)
        self.assertLess(shortest, tallest / 2)

    def test_a_sample_with_no_size_still_gets_a_visible_mark(self):
        self.assertEqual(sample_strip.bar_height({"size_bytes": None}, 100), sample_strip.BAR_MIN)

    def test_every_bar_carries_a_tooltip_naming_its_sample(self):
        strip = sample_marks(self.rows)
        bars = [c for c in strip.children if getattr(c, "title", None)]
        self.assertEqual(len(bars), len(self.rows))
        self.assertIn("QC_01", " ".join(b.title for b in bars))
        self.assertIn("RT reference", " ".join(b.title for b in bars))

    def test_a_batch_change_draws_a_divider(self):
        strip = sample_marks(self.rows)
        dividers = [c for c in strip.children if not getattr(c, "title", None)]
        self.assertEqual(len(dividers), 1)

    def test_identity_is_never_colour_alone(self):
        """A legend is always present, so the strip does not rely on hue."""
        blocks = sample_overview({"id": 1}, self.rows)
        legend = str(blocks[0])
        for label in ["3 study", "1 QC", "1 blank"]:
            with self.subTest(label=label):
                self.assertIn(label, legend)

    def test_nothing_is_drawn_without_samples(self):
        self.assertEqual(sample_overview({"id": 1}, None), [])
        self.assertEqual(sample_overview({"id": 1}, []), [])

    def test_a_short_acquisition_is_called_out_in_words(self):
        rendered = str(sample_overview({"id": 1}, self.rows))
        self.assertIn("Unusually small file", rendered)
        self.assertIn("S2", rendered)
