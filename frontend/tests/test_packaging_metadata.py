import unittest
from types import SimpleNamespace
from unittest.mock import patch

from dash import html

from callbacks import packaging_metadata as sections
from callbacks import study_packaging as packaging
from callbacks.navigation import register_callbacks as register_navigation

METADATA = {
    "revision": 3, "title": "Example subset", "description": "Toy set.", "submission_date": "2026-10-01",
    "public_release_date": "2027-10-01", "collection_protocol": "Collected on ice.",
    "contacts": [{"first_name": "Alex", "last_name": "Researcher", "email": "alex.researcher@example.org",
                  "affiliation": "EMBL", "role": "principal investigator"}],
    "publications": [], "design_descriptors": [{"name": "untargeted metabolomics", "source": "", "accession": ""}],
    "factors": [{"name": "Morphology", "source_column": "group_label", "type": {"name": "morphology", "source": "", "accession": ""}}],
    "assays": [{"assay_id": 25, "assay_name": "default", "protocols": {}},
               {"assay_id": 26, "assay_name": "HILIC positive", "protocols": {"Extraction": "Methanol."}}],
}
ENVELOPE = {
    "metadata": METADATA, "can_edit": True,
    "samples": [
        {"sample_id": 112, "name": "Blank_01", "sample_type": "blank", "organism": "Homo sapiens", "organism_part": "blank",
         "annotations": {"ontology_terms": {"organism": {"source": "NCBITaxon",
                                                         "accession": "http://purl.obolibrary.org/obo/NCBITaxon_9606"}}},
         "assay_values": {"26": {"group_label": "Blank"}}},
        {"sample_id": 116, "name": "1_Cystic", "sample_type": "sample", "organism": "Homo sapiens", "organism_part": "",
         "annotations": {}, "assay_values": {"26": {"group_label": "Cystic"}}},
    ],
    "factor_columns": [{"value": "group_label", "label": "Sample group",
                        "values": [{"value": "Blank", "count": 1}, {"value": "Cystic", "count": 1}], "conflicts": ["QC_01"]}],
    "protocol_sharing": {"26": {"Extraction": ["Negative"]}},
}
CHOICES = {
    "study_id": 14, "vocabularies": {"publication_statuses": ["in preparation", "published"],
                                     "contact_roles": ["Principal Investigator"]},
    "assays": [{"assay_id": 25, "assay_name": "default", "sources": []},
               {"assay_id": 26, "assay_name": "HILIC positive", "sources": [dict(
                   job_id=158, manifest_file_id=19768, polarity="positive", separation_mode="HILIC",
                   column_name="BEH Z-HILIC", generated_protocols={"Data transformation": "MS-DIAL 5.5; generated."})]}],
}
PATH = "/studies/14/packaging/study"


class Capture:
    def __init__(self):
        self.callbacks = {}

    def callback(self, *args, **kwargs):
        def decorator(function):
            self.callbacks[function.__name__] = function
            return function
        return decorator

    def clientside_callback(self, *args, **kwargs):
        return None


def callbacks():
    app = Capture()
    packaging.register_callbacks(app)
    return app.callbacks


def _walk(node):
    yield node
    children = getattr(node, "children", None)
    for child in children if isinstance(children, list) else [children]:
        if child is not None and not isinstance(child, (str, int, float)):
            yield from _walk(child)


def ids_and_values(kind, rows):
    ids = [sections.field_id(kind, field, index) for index, row in enumerate(rows) for field in row]
    return ids, [value for row in rows for value in row.values()]


class PackagingMetadataSectionTests(unittest.TestCase):
    def test_study_save_sends_the_whole_document_with_its_revision_and_drops_empty_rows(self):
        contacts = [METADATA["contacts"][0], {"first_name": "", "last_name": "", "email": "", "affiliation": "", "role": ""}]
        contact_ids, contact_values = ids_and_values("contact", contacts)
        publication_ids, publication_values = ids_and_values("publication", [
            {"title": "Toy data set", "authors": "MZ", "doi": "", "status": "in preparation"}])
        with patch("callbacks.packaging_metadata.call_api",
                   return_value={"metadata": {**METADATA, "revision": 4}}) as api:
            message, envelope = callbacks()["save_study"](
                1, "Example subset", "Toy set, edited.", "2026-10-01", "", contact_values, contact_ids,
                publication_values, publication_ids, [], [], ENVELOPE, PATH, "token")
        method, path = api.call_args.args
        sent = api.call_args.kwargs["payload"]
        self.assertEqual((method, path), ("PUT", "/studies/14/submission-metadata"))
        self.assertEqual(sent["revision"], 3)
        self.assertEqual(len(sent["contacts"]), 1)
        self.assertEqual(sent["publications"][0]["status"], "in preparation")
        self.assertEqual((sent["public_release_date"], sent["design_descriptors"]), (None, []))
        self.assertEqual(sent["factors"], METADATA["factors"])  # other sections are untouched
        self.assertIn("revision 4", str(message))
        self.assertEqual(envelope["metadata"]["revision"], 4)

    def test_protocols_show_sharing_and_build_text_and_save_per_assay(self):
        view = str(sections.protocols_view(ENVELOPE, CHOICES, {}))
        self.assertIn("shared with Negative", view)
        self.assertIn("MS-DIAL 5.5; generated.", view)
        self.assertIn("positive · HILIC · BEH Z-HILIC · job 158", view)
        ids = [{"type": "pkg-protocol", "assay": 26, "name": name} for name in sections.LAB_PROTOCOLS]
        with patch("callbacks.packaging_metadata.call_api", return_value={"metadata": METADATA}) as api:
            callbacks()["save_protocols"](1, "On ice.", ["Methanol, cold.", "HILIC", "Exploris"], ids, ENVELOPE,
                                          "/studies/14/packaging/protocols", "token")
        sent = api.call_args.kwargs["payload"]
        self.assertEqual(sent["collection_protocol"], "On ice.")
        self.assertEqual(sent["assays"][1]["protocols"],
                         {"Extraction": "Methanol, cold.", "Chromatography": "HILIC", "Mass spectrometry": "Exploris"})
        self.assertEqual(sent["assays"][0]["protocols"], {name: "" for name in sections.LAB_PROTOCOLS})

    def test_samples_show_effective_values_and_patch_only_changed_samples(self):
        rows = sections.sample_rows(ENVELOPE, "26")
        self.assertEqual([r["col:group_label"] for r in rows], ["Blank", "Cystic"])
        self.assertEqual(rows[0]["organism_term"], "NCBITaxon:9606")
        rows[1]["organism_part"] = "cultured cells"
        self.assertEqual(sections.changed_samples(rows, ENVELOPE), [(116, {"matrix": "cultured cells"})])
        with patch("callbacks.packaging_metadata.call_api", side_effect=[{}, ENVELOPE]) as api:
            message, _ = callbacks()["save_samples"](1, rows, ENVELOPE, "/studies/14/packaging/samples", "token")
        self.assertEqual(api.call_args_list[0].args, ("PATCH", "/studies/14/samples/116"))
        self.assertIn("updated 1 sample", str(message))
        values = str(sections.factor_values("group_label", ENVELOPE))
        self.assertIn("Blank 1 · Cystic 1", values)
        self.assertIn("differs between assays for QC_01", values)

    def test_factors_save_with_their_term_and_repeatable_rows_add_and_remove(self):
        rows = [{"name": "Morphology", "source_column": "group_label", "type_name": "morphology",
                 "type_source": "", "type_accession": ""}]
        ids, values = ids_and_values("factor", rows)
        with patch("callbacks.packaging_metadata.call_api", return_value={"metadata": METADATA}) as api:
            callbacks()["save_factors"](1, values, ids, ENVELOPE, "/studies/14/packaging/samples", "token")
        self.assertEqual(api.call_args.kwargs["payload"]["factors"], METADATA["factors"])
        edit = callbacks()["edit_contacts"]
        contact_ids, contact_values = ids_and_values("contact", METADATA["contacts"])
        with patch("callbacks.packaging_metadata.ctx", SimpleNamespace(triggered_id="pkg-contact-add", triggered=[])):
            self.assertEqual(len(edit(1, [0], contact_values, contact_ids, CHOICES, ENVELOPE)), 2)
        removal = {"type": "pkg-contact-remove", "index": 0}
        with patch("callbacks.packaging_metadata.ctx", SimpleNamespace(triggered_id=removal, triggered=[{"value": 1}])):
            self.assertIn("none yet", str(edit(0, [1], contact_values, contact_ids, CHOICES, ENVELOPE)))

    def test_read_only_viewers_get_disabled_forms_and_the_old_route_redirects(self):
        study = sections.study_view({**ENVELOPE, "can_edit": False}, CHOICES)
        self.assertIn("read only", str(study))
        self.assertTrue(study.children[2].disabled)
        app = Capture()
        register_navigation(app)
        with patch("callbacks.navigation.resolve_page_slug", return_value=("studies/14/metadata", None)):
            _, key = app.callbacks["render_page"]("/studies/14/metadata", None)
        self.assertEqual(key, "isa-14")
        redirect = callbacks()["redirect_old_route"]
        self.assertEqual(redirect("/studies/14/metadata"), "/studies/14/packaging/study")
        with self.assertRaises(Exception):
            redirect("/studies/14/packaging/builds/1")

    def test_export_and_delete_actions_call_the_api_and_return_to_builds(self):
        handlers = callbacks()
        with patch("callbacks.study_packaging.ctx", SimpleNamespace(triggered_id={"type": "pkg-export", "package": 10})), \
                patch("callbacks.study_packaging.call_api", return_value={}) as api:
            message, poll_disabled = handlers["request_export"]([1], "/studies/14/packaging/builds/1", "token")
        self.assertEqual(api.call_args.args, ("POST", "/studies/14/packages/10/export"))
        self.assertFalse(poll_disabled)
        with patch("callbacks.study_packaging.ctx", SimpleNamespace(triggered_id={"type": "pkg-delete", "package": 10})), \
                patch("callbacks.study_packaging.call_api", return_value={"revision": 1, "freed_bytes": 25_000_000}) as api:
            message, target = handlers["delete_revision"]([1], "/studies/14/packaging/builds/1", "token")
        self.assertEqual(api.call_args.args, ("DELETE", "/studies/14/packages/10"))
        self.assertEqual(target, "/studies/14/packaging")  # the build list is the landing
        self.assertIn("freed 25.0 MB", str(message))
        built = {"id": 10, "revision": 1, "status": "validation_failed", "output_uri": "file:///p/package-10",
                 "summary": {"errors": 1, "warnings": 0}, "sources": [],
                 "export": {"status": "exported", "uri": "file:///x/study-14-r1"}}
        queued = {**built, "id": 11, "revision": 2, "status": "queued", "output_uri": None, "export": {}}
        listing = packaging.builds_view(14, [queued, built])
        actions = {str(button.id): button.disabled for button in _walk(listing) if isinstance(button, html.Button)}
        self.assertEqual(actions, {
            str({"type": "pkg-download", "package": 11}): True, str({"type": "pkg-export", "package": 11}): True,
            str({"type": "pkg-download", "package": 10}): False, str({"type": "pkg-export", "package": 10}): False})
        self.assertIn("study-14-r1", str(packaging.export_state(built)))  # the folder shows on hover


if __name__ == "__main__":
    unittest.main()
