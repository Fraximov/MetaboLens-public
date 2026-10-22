import unittest
from types import SimpleNamespace
from unittest.mock import patch

from callbacks import packaging_files as files
from callbacks import study_packaging as packaging

ENTRIES = [
    dict(path="FILES", name="FILES", kind="dir", role=None, size=None, errors=0, warnings=0),
    dict(path="FILES/RAW_FILES", name="RAW_FILES", kind="dir", role=None, size=None, errors=0, warnings=0),
    dict(path="FILES/RAW_FILES/HILIC-positive", name="HILIC-positive", kind="dir", role=None, size=None, errors=0,
         warnings=0),
    dict(path="FILES/RAW_FILES/HILIC-positive/QC_02.raw", name="QC_02.raw", kind="link", role="spectrum",
         size=150_000_000, available=True, source="file:///raw_test/Example/QC_02.raw", sha256="b2" * 32,
         verification="reused", errors=0, warnings=0),
    dict(path="FILES/RAW_FILES/HILIC-positive/QC_03.raw", name="QC_03.raw", kind="link", role="spectrum", size=None,
         available=False, source="file:///raw_test/Example/QC_03.raw", sha256=None, verification=None, errors=1,
         warnings=0),
    dict(path="i_Investigation.txt", name="i_Investigation.txt", kind="file", role="investigation", size=9000,
         errors=1, warnings=10),
    dict(path="s_local-draft.txt", name="s_local-draft.txt", kind="file", role="samples", size=3000, errors=1,
         warnings=4),
]
SAMPLES = dict(path="s_local-draft.txt", role="samples", columns=["Source Name", "Characteristics[Organism]",
                                                                    "Term Source REF"],
               rows=[["src", "Homo sapiens", "NCBITaxon"]], total_rows=1, findings=[
                   dict(code="rule_s_org", severity="warning", message="Organism not validated",
                        column="Characteristics[Organism]", column_index=1),
                   dict(code="rule_s_file", severity="error", message="Invalid sample file name", column="",
                        column_index=None)])


class Capture:
    def __init__(self):
        self.callbacks = {}

    def callback(self, *args, **kwargs):
        def decorator(function):
            self.callbacks[function.__name__] = function
            return function
        return decorator


class PackageFilesViewTests(unittest.TestCase):
    def test_files_routes_belong_to_the_builds_section(self):
        self.assertEqual(files.files_route("/studies/14/packaging/builds/2/files"), (14, 2, None))
        self.assertEqual(files.files_route("/studies/14/packaging/builds/2/files/s_local-draft.txt"),
                         (14, 2, "s_local-draft.txt"))
        self.assertIsNone(files.files_route("/studies/14/packaging/builds/2"))
        self.assertEqual(packaging.parse_route("/studies/14/packaging/builds/2/files/s_local-draft.txt"), (14, "builds", 2))

    def test_tree_folds_spectra_and_links_only_isa_files(self):
        text = str(files.tree(ENTRIES, 14, 2, "s_local-draft.txt"))
        self.assertIn("FILES/RAW_FILES/HILIC-positive/", text)  # single-folder chain joined
        self.assertIn("2 linked", text)
        self.assertIn("150.0 MB", text)
        self.assertIn("missing", text)  # the dangling link
        self.assertIn("sha256 " + "b2" * 32, text)  # checksum on hover
        self.assertIn("/studies/14/packaging/builds/2/files/s_local-draft.txt", text)
        self.assertNotIn("/files/FILES", text)  # spectra have no preview
        self.assertIn("is-selected", text)

    def test_tree_lists_isa_files_in_reading_order_and_folders_last(self):
        rows = files.tree(ENTRIES, 14, 2, None).children[1:]
        self.assertIn("i_Investigation.txt", str(rows[0]))
        self.assertIn("s_local-draft.txt", str(rows[1]))
        self.assertIn("FILES/RAW_FILES/HILIC-positive/", str(rows[2]))

    def test_investigation_folds_empty_fields_but_keeps_their_names(self):
        view = files.investigation_view(dict(sections=[
            dict(name="INVESTIGATION", fields=[dict(label="Investigation Title", values=[""])]),
            dict(name="STUDY", fields=[dict(label="Study Title", values=["Example subset"]),
                                       dict(label="Study Identifier", values=[""])])]))
        text = str(view)
        self.assertNotIn("all 1 fields", text)
        self.assertIn("1 empty field", text)
        self.assertIn("Study Identifier", text)  # still named, inside the fold
        self.assertIn("Example subset", text)

    def test_tables_flag_the_columns_findings_name(self):
        view = files.table_view(SAMPLES)
        table = view.children[1]
        self.assertEqual(table.tooltip_header, {"1": {"value": "warning: Organism not validated", "type": "text"}})
        self.assertEqual(table.style_header_conditional[0]["if"], {"column_id": "1"})
        self.assertIn("1 rows · 3 columns · 1 flagged", str(view))
        maf = files.table_view(dict(SAMPLES, rows=SAMPLES["rows"] * 20, total_rows=4372, findings=[]))
        self.assertIn("first 20 of 4,372 rows", str(maf))
        panel = files.findings_panel(SAMPLES)
        self.assertIn("Invalid sample file name", str(panel.children[1].children[0]))  # errors first
        self.assertIn("Characteristics[Organism] · rule_s_org", str(panel))
        self.assertNotIn("Invalid sample file name", str(files.preview_view(SAMPLES)))  # beside the tree, not above the file

    def test_the_files_route_renders_the_tree_and_the_investigation_by_default(self):
        app = Capture()
        packaging.register_callbacks(app)
        built = dict(id=11, revision=2, status="validation_failed", summary={"errors": 5, "warnings": 18})
        investigation = dict(path="i_Investigation.txt", role="investigation", findings=[], sections=[
            dict(name="STUDY", fields=[dict(label="Study Title", values=["Example subset"])])])
        responses = [[built], {"entries": ENTRIES}, investigation]
        with patch("callbacks.study_packaging.ctx", SimpleNamespace(triggered_id="url")), \
                patch("callbacks.study_packaging.call_api", side_effect=responses[:1]), \
                patch("callbacks.packaging_files.call_api", side_effect=responses[1:]) as api:
            view, poll_disabled, _ = app.callbacks["render_section"](
                "/studies/14/packaging/builds/2/files", {"study_id": 14, "assays": []}, 0, {}, "token")
        self.assertTrue(poll_disabled)
        self.assertEqual(api.call_args_list[1].args, ("GET", "/studies/14/packages/11/files/i_Investigation.txt"))
        self.assertIn("Example subset", str(view))
        self.assertIn("revision 2 files", str(view))

    def test_a_built_revision_links_to_its_files_from_its_page_and_the_list(self):
        package = dict(id=11, revision=2, status="validation_failed", output_uri="file:///p/package-11",
                       summary={"errors": 0, "warnings": 0}, sources=[], export={})
        self.assertIn("/studies/14/packaging/builds/2/files", str(packaging.build_detail_view(14, package)))
        queued = dict(package, id=12, revision=3, status="queued", output_uri=None)
        listing = str(packaging.builds_view(14, [queued, package]))
        self.assertIn("/studies/14/packaging/builds/2/files", listing)
        self.assertNotIn("/studies/14/packaging/builds/3/files", listing)  # nothing built to read yet


if __name__ == "__main__":
    unittest.main()
