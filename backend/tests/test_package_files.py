import csv
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi import HTTPException

from app.services import package_files

SAMPLE_HEADER = ["Source Name", "Characteristics[Organism]", "Term Source REF", "Term Accession Number",
                 "Characteristics[Organism part]", "Term Source REF", "Term Accession Number", "Sample Name"]
ISSUES = [
    dict(code="rule_i", severity="error", message="Study identifier '' does not match pattern", file="i_Investigation.txt",
         column="", column_index=""),
    # The validator's index for an owned reference column points one further (here at Term Accession Number).
    dict(code="rule_s_ref", severity="warning", message="Term Source REF of Organism part is empty",
         file="s_draft.txt", column="Term Source REF (of Characteristics[Organism part])", column_index="6"),
    dict(code="rule_s_org", severity="warning", message="Organism terms not validated", file="s_draft.txt",
         column="Characteristics[Organism]", column_index="1"),
    dict(code="rule_s_file", severity="error", message="Invalid sample file name", file="s_draft.txt", column="",
         column_index=""),
    dict(code="rule_f", severity="error", message="Missing file", file="FILES/RAW_FILES/pos/B.raw"),
    dict(code="missing_request_id", severity="error", message="A real request is required."),
]


def write_tsv(path: Path, rows) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        csv.writer(stream, delimiter="\t", lineterminator="\n").writerows(rows)


def revision(root: Path) -> Path:
    """A built revision: ISA files, one linked and one dangling spectrum, and the build's file map."""
    folder = root / "package-7"
    submission = folder / "submission"
    spectra = submission / "FILES" / "RAW_FILES" / "pos"
    spectra.mkdir(parents=True)
    (folder / "provenance").mkdir()
    write_tsv(submission / "i_Investigation.txt", [
        ["ONTOLOGY SOURCE REFERENCE"], ["Term Source Name", "NCBITaxon", "EFO"],
        ["STUDY"], ["Study Identifier", ""], ["Study Title", "Example subset"], [""],
        ["STUDY CONTACTS"], ["Study Person Last Name", "Researcher", "Example"]])
    write_tsv(submission / "s_draft.txt", [SAMPLE_HEADER] + [
        ["src", "Homo sapiens", "NCBITaxon", "http://purl.obolibrary.org/obo/NCBITaxon_9606", "blank", "", "",
         f"S{index}"] for index in range(3)])
    write_tsv(submission / "m_draft_maf.tsv", [["database_identifier", "QC_01"]] + [[f"F{i}", str(i)] for i in range(30)])
    source = root / "raw" / "A.raw"
    source.parent.mkdir()
    source.write_bytes(b"spectra")
    (spectra / "A.raw").symlink_to(source)
    (spectra / "B.raw").symlink_to(root / "raw" / "missing.raw")
    write_tsv(folder / "provenance" / "file-map.tsv", [
        ["export_path", "source_uri", "sha256", "verification"],
        ["FILES/RAW_FILES/pos/A.raw", "file:///raw_test/A.raw", "ab" * 32, "reused"]])
    return folder


class PackageFilesTests(unittest.TestCase):
    def test_listing_shows_the_submission_folder_without_entering_links(self):
        with TemporaryDirectory() as tmp:
            entries = {e["path"]: e for e in package_files.listing(revision(Path(tmp)), ISSUES)}
        self.assertEqual(list(entries), ["FILES", "FILES/RAW_FILES", "FILES/RAW_FILES/pos", "FILES/RAW_FILES/pos/A.raw",
                                         "FILES/RAW_FILES/pos/B.raw", "i_Investigation.txt", "m_draft_maf.tsv",
                                         "s_draft.txt"])
        self.assertEqual((entries["s_draft.txt"]["role"], entries["s_draft.txt"]["errors"],
                          entries["s_draft.txt"]["warnings"]), ("samples", 1, 2))
        linked = entries["FILES/RAW_FILES/pos/A.raw"]
        self.assertEqual((linked["kind"], linked["role"], linked["size"], linked["available"], linked["source"],
                          linked["verification"]), ("link", "spectrum", 7, True, "file:///raw_test/A.raw", "reused"))
        dangling = entries["FILES/RAW_FILES/pos/B.raw"]
        self.assertEqual((dangling["available"], dangling["size"], dangling["errors"]), (False, None, 1))
        self.assertEqual(entries["FILES"]["kind"], "dir")

    def test_previews_place_findings_on_their_columns_by_name(self):
        with TemporaryDirectory() as tmp:
            folder = revision(Path(tmp))
            samples = package_files.preview(folder, "s_draft.txt", ISSUES)
            investigation = package_files.preview(folder, "i_Investigation.txt", ISSUES)
            maf = package_files.preview(folder, "m_draft_maf.tsv", ISSUES)
        self.assertEqual((samples["columns"], samples["total_rows"], len(samples["rows"])), (SAMPLE_HEADER, 3, 3))
        placed = {f["code"]: f["column_index"] for f in samples["findings"]}
        self.assertEqual(placed, {"rule_s_ref": 5, "rule_s_org": 1, "rule_s_file": None})
        self.assertEqual([s["name"] for s in investigation["sections"]],
                         ["ONTOLOGY SOURCE REFERENCE", "STUDY", "STUDY CONTACTS"])
        self.assertEqual(investigation["sections"][1]["fields"][1], {"label": "Study Title", "values": ["Example subset"]})
        self.assertEqual([f["code"] for f in investigation["findings"]], ["rule_i"])
        self.assertEqual((len(maf["rows"]), maf["total_rows"]), (package_files.MAF_PREVIEW_ROWS, 30))

    def test_previews_refuse_links_escapes_and_other_files(self):
        with TemporaryDirectory() as tmp:
            folder = revision(Path(tmp))
            (folder / "provenance" / "secret.txt").write_text("outside submission")
            for path, status in (("../provenance/file-map.tsv", 400), ("FILES/RAW_FILES/pos/A.raw", 400),
                                 ("/etc/passwd", 400), ("FILES", 404), ("s_missing.txt", 404),
                                 ("FILES/RAW_FILES/pos", 404)):
                with self.subTest(path=path), self.assertRaises(HTTPException) as refused:
                    package_files.preview(folder, path, ISSUES)
                self.assertEqual(refused.exception.status_code, status)


if __name__ == "__main__":
    unittest.main()
