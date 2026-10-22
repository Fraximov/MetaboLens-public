"""Scientific regression fixture: two polarities, repeated injection, unknowns.

The hand-written abundances are intentionally unlike peak heights. Reordering
rows must not change their feature identity, and statistics exclusions must not
remove annotation-included features from the MAF.
"""
import csv
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.schemas.study_package import PackageSnapshot
from app.services import metabolights_export


def write_table(path, header, rows):
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def example_snapshot(root):
    """Synthetic values only; no real contact, specimen, or repository identifier."""
    assays = []
    for assay_id, polarity, sample_ids in [(1, "positive", [1, 1, 2]), (2, "negative", [1, 3])]:
        folder = root / f"source-{assay_id}"
        folder.mkdir()
        files = []

        def register(path, role, file_id):
            record = dict(file_id=file_id, filename=path.name, storage_uri=path.as_uri(),
                          sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                          size_bytes=path.stat().st_size, role=role)
            files.append(record)
            return record

        acquisitions = []
        for index, sample_id in enumerate(sample_ids):
            file_id = assay_id * 100 + index
            raw = folder / f"injection-{index}.mzML"
            raw.write_text("<mzML>synthetic file inventory fixture, not spectra</mzML>\n")
            record = register(raw, "input", file_id)
            acquisitions.append(dict(acquisition_id=f"file-{file_id}", sample_id=sample_id, file=record))
        columns = [a["acquisition_id"] for a in acquisitions]
        audit = folder / "feature_audit_final.tsv"
        write_table(audit, ["feature_id", "feature_name", "mz", "rt_min", "adduct", "Formula", "INCHIKEY",
                            "effective_decision_annotation", "effective_decision_statistics"], [
            ["7", "Known molecule", "201.14577", "4.485", "[M+H]+", "C10H20O4", "not-a-full-inchi", "include", "include"],
            ["2", "Unknown", "155.012", "2.1", "[M+H]+", "null", "null", "include", "exclude"],
            ["9", "Artifact", "299.1", "3.0", "", "", "", "exclude", "include"],
        ])
        register(audit, "metabolensr_feature_audit_final", assay_id * 100 + 10)
        matrix = folder / "normalized_raw.tsv"
        write_table(matrix, ["feature_id", *columns], [
            ["2", *(["0", "NA", "55.250"][:len(columns)])],
            ["9", *(["999"] * len(columns))],
            ["7", *(["388133", "123456", "987654"][:len(columns)])],
        ])
        register(matrix, "metabolensr_normalized_raw", assay_id * 100 + 11)
        samples = folder / "sample_metadata.tsv"
        write_table(samples, ["acquisition_id", "file_asset_id", "sample_id", "sample_name"],
                    [[a["acquisition_id"], a["file"]["file_id"], a["sample_id"], "Same display name"] for a in acquisitions])
        register(samples, "metabolensr_sample_metadata", assay_id * 100 + 12)
        manifest = folder / "metabolensr_manifest.json"
        manifest.write_text(json.dumps(dict(schema_version="2.0", bundle_type="final",
            source=dict(tool="msdial", version="5.5", quantitation_type="area", retention_time_unit="minute"),
            matrix_feature_scope="all_features", outputs=[dict(file=p.name, md5=hashlib.md5(p.read_bytes()).hexdigest())
                for p in (audit, matrix, samples)])))
        register(manifest, "metabolensr_manifest", assay_id * 100 + 13)
        assays.append(dict(assay_id=assay_id, assay_name=polarity, job_id=10 + assay_id,
            manifest_file_id=assay_id * 100 + 13, polarity=polarity,
            files=[f for f in files if f["role"] != "input"], acquisitions=acquisitions))
    return PackageSnapshot(study_id=1, title="Synthetic dual polarity fixture", description="For local tests only.",
        samples=[dict(sample_id=i, name="Same display name", sample_type=t) for i,t in [(1,"sample"),(2,"QC"),(3,"blank")]],
        assays=assays)


def with_example_metadata(snapshot):
    """Explicit fake metadata for export/conformance tests; never a real study."""
    data = snapshot.model_dump()
    data.update(submission_date="2026-09-30", public_release_date="2027-03-30",
        description="EXAMPLE — Replace this text with the study background, biological question, experimental groups, specimen collection, and untargeted LC-MS analysis. These descriptions demonstrate metadata storage and do not describe real experimental work.",
        collection_protocol="EXAMPLE: collect specimens and immediately freeze them.",
        contacts=[{"First Name": "Example", "Last Name": "Researcher", "Email": "pi@example.org",
                   "Affiliation": "Example Research Institute", "Roles": "principal investigator"}],
        design_descriptors=[{"name": "EXAMPLE — " + name, "source": "", "accession": ""}
                            for name in ("case-control study", "treatment comparison", "metabolite profiling")],
        publications=[{"title": "EXAMPLE — Study manuscript in preparation", "authors": "Example Researcher", "status": "in preparation", "doi": ""}],
        factors=[{"name": "condition", "source_column": "group_label", "type": {"name": "condition", "source": "", "accession": ""}}])
    for sample, value in zip(data["samples"], ["treated", "QC", "blank"]):
        sample.update(organism="Homo sapiens", organism_part="blood", factor_values={"condition": value},
            ontology_terms={"organism": {"name": "Homo sapiens", "source": "NCBITaxon", "accession": "http://purl.obolibrary.org/obo/NCBITaxon_9606"},
                            "organism_part": {"name": "blood", "source": "UBERON", "accession": "http://purl.obolibrary.org/obo/UBERON_0000178"}})
    for assay in data["assays"]:
        assay["protocols"] = {name: "EXAMPLE: replace with the actual laboratory method."
                              for name in ("Extraction", "Chromatography", "Mass spectrometry", "Data transformation", "Metabolite identification")}
    return PackageSnapshot.model_validate(data)


def change_annotation_decision(snapshot, decision):
    """Keep the bundle's two checksum inventories consistent while changing a decision."""
    audit_record = snapshot.assays[0].files[0]
    audit = metabolights_export.local_file(audit_record.storage_uri)
    audit.write_text(audit.read_text().replace("include\tinclude", f"{decision}\tinclude"))
    audit_record.sha256 = hashlib.sha256(audit.read_bytes()).hexdigest()
    audit_record.size_bytes = audit.stat().st_size
    manifest_record = snapshot.assays[0].files[-1]
    manifest_path = metabolights_export.local_file(manifest_record.storage_uri)
    manifest = json.loads(manifest_path.read_text())
    manifest["outputs"][0]["md5"] = hashlib.md5(audit.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    manifest_record.sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    manifest_record.size_bytes = manifest_path.stat().st_size


class MetabolightsExportTests(unittest.TestCase):
    def test_saved_metadata_exports_without_losing_repeated_ontology_columns(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = with_example_metadata(example_snapshot(root))
            snapshot.assays[0] = snapshot.assays[0].model_copy(update={
                "method": {"Parameter Value[Column type]": "HILIC"},
                "method_ontology_terms": {"Parameter Value[Column type]": {
                    "name": "HILIC", "source": "MTBLS",
                    "accession": "http://www.ebi.ac.uk/metabolights/ontology/MTBLS_000856"}}})
            report = metabolights_export.build_package(snapshot, root / "export")
            with (root / "export/submission/i_Investigation.txt").open() as stream:
                investigation = {row[0]: row[1:] for row in csv.reader(stream, delimiter="\t")}
            self.assertEqual(investigation["Study Submission Date"], ["2026-09-30"])
            self.assertEqual(investigation["Study Public Release Date"], ["2027-03-30"])
            self.assertEqual(investigation["Study Design Type"], ["EXAMPLE — case-control study", "EXAMPLE — treatment comparison", "EXAMPLE — metabolite profiling"])
            self.assertEqual(investigation["Study Design Type Term Source REF"], ["", "", ""])
            self.assertEqual(investigation["Study Publication Title"], ["EXAMPLE — Study manuscript in preparation"])
            self.assertEqual(investigation["Study Publication DOI"], [""])
            self.assertEqual(investigation["Study Publication Status Term Source REF"], ["EFO"])
            self.assertEqual(investigation["Study Publication Status Term Accession Number"], ["http://www.ebi.ac.uk/efo/EFO_0001795"])
            self.assertEqual(investigation["Study Factor Name"], ["condition"])
            self.assertEqual(investigation["Study Factor Type"], ["condition"])
            self.assertIn("EXAMPLE: collect specimens", investigation["Study Protocol Description"][0])
            self.assertEqual(investigation["Study Person Email"], ["pi@example.org"])
            self.assertEqual(investigation["Study Person Roles"], ["Principal Investigator"])
            self.assertEqual(investigation["Study Person Roles Term Source REF"], ["NCIT"])
            self.assertEqual(investigation["Study Person Roles Term Accession Number"],
                             ["http://purl.obolibrary.org/obo/NCIT_C19924"])
            with next((root / "export/submission").glob("a_*_positive_*.txt")).open() as stream:
                assay_header, *assay_rows = csv.reader(stream, delimiter="\t")
            column = assay_header.index("Parameter Value[Column type]")
            self.assertEqual(assay_rows[0][column:column+3],
                             ["HILIC", "MTBLS", "http://www.ebi.ac.uk/metabolights/ontology/MTBLS_000856"])
            ion_source = assay_header.index("Parameter Value[Ion source]")
            self.assertEqual(assay_rows[0][ion_source:ion_source+3], ["", "", ""])
            self.assertIn("EXAMPLE:", investigation["Study Protocol Description"][4])
            self.assertIn("quantity: peak area (declared by the run)", investigation["Study Protocol Description"][4])
            with next((root / "export/submission").glob("s_*.txt")).open() as stream:
                header, *rows = csv.reader(stream, delimiter="\t")
            organism = header.index("Characteristics[Organism]")
            part = header.index("Characteristics[Organism part]")
            self.assertEqual(rows[0][organism:organism+3], ["Homo sapiens", "NCBITaxon", "http://purl.obolibrary.org/obo/NCBITaxon_9606"])
            self.assertEqual(rows[0][part:part+3], ["blood", "UBERON", "http://purl.obolibrary.org/obo/UBERON_0000178"])
            self.assertEqual([row[header.index("Factor Value[condition]")] for row in rows], ["treated", "QC", "blank"])
            factor = header.index("Factor Value[condition]")
            self.assertEqual(header[factor:factor+3], ["Factor Value[condition]", "Term Source REF", "Term Accession Number"])
            self.assertEqual(rows[0][factor:factor+3], ["treated", "", ""])
            codes = [issue["code"] for issue in report["issues"]]
            self.assertNotIn("example_metadata", codes)
            self.assertNotIn("missing_collection_protocol", codes)
            self.assertNotIn("missing_contacts", codes)
            self.assertNotIn("missing_protocol", codes)
            self.assertFalse(report["submission_ready"])

    def test_controlled_terms_match_case_insensitively_and_never_guess(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = with_example_metadata(example_snapshot(root))
            snapshot.publications = [{"title": "T", "authors": "A", "status": "In Preparation", "doi": ""},
                                     {"title": "U", "authors": "B", "status": "accepted", "doi": ""}]
            snapshot.contacts = [{**snapshot.contacts[0], "Roles": "principal  Investigator"},
                                 {**snapshot.contacts[0], "Roles": "study contact"}]
            metabolights_export.build_package(snapshot, root / "export")
            with (root / "export/submission/i_Investigation.txt").open() as stream:
                investigation = {row[0]: row[1:] for row in csv.reader(stream, delimiter="\t")}
        self.assertEqual(investigation["Study Publication Status"], ["in preparation", "accepted"])
        self.assertEqual(investigation["Study Publication Status Term Source REF"], ["EFO", ""])
        self.assertEqual(investigation["Study Person Roles"], ["Principal Investigator", "study contact"])
        self.assertEqual(investigation["Study Person Roles Term Accession Number"],
                         ["http://purl.obolibrary.org/obo/NCIT_C19924", ""])

    def test_assay_identity_keeps_lcms_in_the_official_readers_filename_position(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = example_snapshot(root)
            snapshot.assays[0].method = {"Parameter Value[Column type]": "Amide"}
            metabolights_export.build_package(snapshot, root / "export")
            assay_files = sorted((root / "export/submission").glob("a_*.txt"))
            self.assertEqual([path.name.split("_")[2] for path in assay_files], ["LC-MS", "LC-MS"])
            with (root / "export/submission/i_Investigation.txt").open() as stream:
                investigation = {row[0]: row[1:] for row in csv.reader(stream, delimiter="\t")}
            self.assertEqual(investigation["Study Assay Technology Platform"], ["Liquid Chromatography MS"] * 2)
            positive = next(path for path in assay_files if "_positive_" in path.name)
            rows = metabolights_export.read_table(positive)[1]
            self.assertEqual(rows[0]["Parameter Value[Column type]"], "Amide")

    def test_review_decisions_produce_a_blocked_draft_without_becoming_included_annotations(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = example_snapshot(root)
            change_annotation_decision(snapshot, "review")
            report = metabolights_export.build_package(snapshot, root / "export")
            finding = next(i for i in report["issues"] if i["code"] == "unresolved_annotation_decisions")
            self.assertEqual(finding["severity"], "error")
            self.assertEqual(finding["feature_ids"], ["7"])
            maf = next((root / "export/submission").glob("m_*_positive_*.tsv"))
            rows = metabolights_export.read_table(maf)[1]
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0]["metabolite_identification"].startswith("Unknown"))
            audit = metabolights_export.read_table(root / "export/provenance/assay-1/feature_audit_final.tsv")[1]
            self.assertEqual(next(r for r in audit if r["feature_id"] == "7")["effective_decision_annotation"], "review")

    def test_missing_input_checksum_is_recorded_when_copying_without_claiming_historical_verification(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = example_snapshot(root)
            record = snapshot.assays[0].acquisitions[0].file
            expected_hash = record.sha256
            record.sha256 = None
            report = metabolights_export.build_package(snapshot, root / "export")
            rows = metabolights_export.read_table(root / "export/provenance/file-map.tsv")[1]
            copied = next(row for row in rows if row["file_id"] == str(record.file_id))
            self.assertEqual(copied["sha256"], expected_hash)
            self.assertEqual(copied["checksum_origin"], "packaging")
            self.assertEqual(copied["verification"], "hashed")
            self.assertEqual(rows[1]["checksum_origin"], "registered")
            self.assertTrue(any(i["code"] == "input_checksums_recorded" and i["file_ids"] == [record.file_id]
                                for i in report["issues"]))
            self.assertIsNone(snapshot.assays[0].acquisitions[0].file.sha256)
            record.size_bytes += 1
            with self.assertRaisesRegex(ValueError, "size changed"):
                metabolights_export.copy_verified(record, root / "bad-size.raw")
            record.role = "metabolensr_normalized_raw"
            with self.assertRaisesRegex(ValueError, "registered checksum"):
                metabolights_export.copy_verified(record, root / "missing-output-checksum.tsv")

    def test_union_separate_assays_exact_values_and_annotation_decisions(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = example_snapshot(root)
            result = metabolights_export.build_package(snapshot, root / "export")
            submission = root / "export/submission"
            sample_rows = metabolights_export.read_table(next(submission.glob("s_*.txt")))[1]
            self.assertEqual(len(sample_rows), 3)
            self.assertEqual(len({r["Sample Name"] for r in sample_rows}), 3)
            mafs = sorted(submission.glob("m_*.tsv"))
            self.assertEqual([maf.name.split("_")[2] for maf in mafs], ["negative", "positive"])
            for maf, expected_columns in zip(mafs, [2, 3]):
                header, rows = metabolights_export.read_table(maf)
                self.assertEqual(len(header), 21 + expected_columns)
                self.assertEqual(len(rows), 2)
                known = next(r for r in rows if r["metabolite_identification"] == "Known molecule")
                self.assertEqual(known[header[21]], "388133")
                self.assertEqual(known["inchi"], "")
                unknown = next(r for r in rows if r["metabolite_identification"].startswith("Unknown"))
                self.assertEqual(unknown[header[21]], "0")
                self.assertEqual(unknown[header[22]], "")
            self.assertTrue(any(i["code"] == "missing_contacts" for i in result["issues"]))
            self.assertFalse(result["submission_ready"])
            self.assertEqual(len(list((submission / "FILES").rglob("*.mzML"))), 5)
            self.assertNotIn("Please update", (submission / "i_Investigation.txt").read_text())
            # A later metadata change cannot rewrite an existing package.
            previous = (submission / "i_Investigation.txt").read_bytes()
            snapshot.title = "Changed later"
            with self.assertRaises(FileExistsError):
                metabolights_export.build_package(snapshot, root / "export")
            self.assertEqual((submission / "i_Investigation.txt").read_bytes(), previous)

    def test_recorded_names_are_exported_and_qualified_only_on_collision(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = example_snapshot(root)
            snapshot.samples[1].name = "QC_02"
            metabolights_export.build_package(snapshot, root / "export")
            submission = root / "export/submission"
            samples = metabolights_export.read_table(submission / "s_local-draft.txt")[1]
            # Two samples share a recorded name, so both carry their sample ID.
            self.assertEqual([row["Sample Name"] for row in samples],
                             ["Same display name-s1", "QC_02", "Same display name-s3"])
            self.assertEqual(sorted(p.name for p in submission.glob("a_*.txt")),
                             ["a_local-draft_LC-MS_negative_metabolite_profiling.txt",
                              "a_local-draft_LC-MS_positive_metabolite_profiling.txt"])
            # File names must be unique study-wide: names shared by both assays carry
            # the assay key, injection-2 exists once and keeps its recorded name.
            self.assertEqual(sorted(p.relative_to(submission).as_posix() for p in (submission / "FILES").rglob("*.mzML")),
                             ["FILES/DERIVED_FILES/negative/negative-injection-0.mzML",
                              "FILES/DERIVED_FILES/negative/negative-injection-1.mzML",
                              "FILES/DERIVED_FILES/positive/injection-2.mzML",
                              "FILES/DERIVED_FILES/positive/positive-injection-0.mzML",
                              "FILES/DERIVED_FILES/positive/positive-injection-1.mzML"])
            positive = metabolights_export.read_table(submission / "a_local-draft_LC-MS_positive_metabolite_profiling.txt")[1]
            self.assertEqual([row["MS Assay Name"] for row in positive],
                             ["positive-injection-0", "positive-injection-1", "injection-2"])
            self.assertEqual(positive[2]["Derived Spectral Data File"], "FILES/DERIVED_FILES/positive/injection-2.mzML")
            self.assertEqual(positive[2]["Sample Name"], "QC_02")
            header = metabolights_export.read_table(submission / "m_local-draft_positive_metabolite_profiling_v2_maf.tsv")[0]
            self.assertEqual(header[-3:], ["positive-injection-0", "positive-injection-1", "injection-2"])
            sample_map = metabolights_export.read_table(root / "export/provenance/sample-map.tsv")[1]
            self.assertEqual(sample_map[0], {"sample_id": "1", "original_name": "Same display name",
                                             "Sample Name": "Same display name-s1"})

    def test_same_file_name_twice_in_one_assay_gets_file_ids(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = example_snapshot(root)
            for acquisition in snapshot.assays[0].acquisitions[:2]:
                acquisition.file.filename = "Injection.mzML"
            snapshot.assays[1].acquisitions[1].file.filename = "injection.mzml"
            metabolights_export.build_package(snapshot, root / "export")
            rows = metabolights_export.read_table(root / "export/provenance/file-map.tsv")[1]
            self.assertEqual([row["export_path"].rsplit("/", 1)[1] for row in rows],
                             ["Injection-f100.mzML", "Injection-f101.mzML", "injection-2.mzML",
                              "injection-0.mzML", "injection.mzml"])
            self.assertEqual([row["ms_assay_name"] for row in rows],
                             ["Injection-f100", "Injection-f101", "injection-2", "injection-0", "injection"])

    def test_spectra_are_linked_and_checksums_reused_only_while_files_are_unchanged(self):
        import os
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = example_snapshot(root)
            first = metabolights_export.build_package(snapshot, root / "first")
            link = root / "first/submission/FILES/DERIVED_FILES/positive/injection-2.mzML"  # Unique, so unchanged.
            self.assertTrue(link.is_symlink())
            self.assertEqual(link.resolve(), (root / "source-1/injection-2.mzML").resolve())
            linked = [entry for entry in first["files"] if entry.get("linked")]
            self.assertEqual(len(linked), 5)
            self.assertEqual({entry["verification"] for entry in first["spectral_files"]}, {"hashed"})

            verified = {entry["file_id"]: entry for entry in first["spectral_files"]}
            metabolights_export.build_package(snapshot, root / "second", verified)
            rows = metabolights_export.read_table(root / "second/provenance/file-map.tsv")[1]
            self.assertEqual({row["verification"] for row in rows}, {"reused"})

            # A changed modification time forces a fresh read of that file only.
            source = root / "source-1/injection-0.mzML"
            os.utime(source, ns=(source.stat().st_atime_ns, source.stat().st_mtime_ns + 10**9))
            metabolights_export.build_package(snapshot, root / "third", verified)
            rows = metabolights_export.read_table(root / "third/provenance/file-map.tsv")[1]
            self.assertEqual([row["verification"] for row in rows], ["hashed", "reused", "reused", "reused", "reused"])

            # Same size, new bytes: the registered checksum stops the build.
            source.write_text(source.read_text().replace("synthetic", "Synthetic"))
            with self.assertRaisesRegex(ValueError, "checksum"):
                metabolights_export.build_package(snapshot, root / "edited", verified)
            source.unlink()
            with self.assertRaisesRegex(ValueError, "unavailable"):
                metabolights_export.build_package(snapshot, root / "missing", verified)

    def test_changed_source_and_acquisition_mismatch_fail_closed(self):
        for damage in ("checksum", "acquisition", "decision"):
            with self.subTest(damage=damage), TemporaryDirectory() as tmp:
                root = Path(tmp)
                snapshot = example_snapshot(root)
                if damage == "checksum":
                    (root / "source-1/normalized_raw.tsv").write_text("changed")
                elif damage == "acquisition":
                    snapshot.assays[0].acquisitions[0].sample_id = 3
                else:
                    change_annotation_decision(snapshot, "invalid-decision")
                with self.assertRaises(ValueError):
                    metabolights_export.build_package(snapshot, root / "export")
                self.assertFalse((root / "export/provenance/manifest.json").exists())

    def test_repeat_build_is_deterministic_and_never_relabels_unknown_quantity(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = example_snapshot(root)
            for name in ("first", "second"):
                metabolights_export.build_package(snapshot, root / name)
            first = {str(p.relative_to(root/"first")): p.read_bytes() for p in (root/"first/submission").rglob("*") if p.is_file()}
            second = {str(p.relative_to(root/"second")): p.read_bytes() for p in (root/"second/submission").rglob("*") if p.is_file()}
            self.assertEqual(first, second)
            path = root / "source-1/metabolensr_manifest.json"
            path.write_text(path.read_text().replace('"area"', '"unspecified"'))
            snapshot.assays[0].files[-1].sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
            snapshot.assays[0].files[-1].size_bytes = path.stat().st_size
            result = metabolights_export.build_package(snapshot, root / "unknown")
            # Neither the run nor an MS-DIAL export says what the values are.
            self.assertTrue(any(i["code"] == "unknown_quantity" for i in result["issues"]))

    def test_an_undeclared_run_takes_its_quantity_from_its_ms_dial_export(self):
        from app.schemas.study_package import PackageFile
        from app.services.package_readiness import export_quantity, resolve_quantity

        def add_export(snapshot, folder, unit):
            export = folder / "AlignResult-1.mzTabM"
            export.write_text("MTD\tmzTab-version\t2.0.0-M\n"
                              f"MTD\tsmall_molecule_feature-quantification_unit\t[,,precursor intensity ({unit}), ]\n"
                              "SMH\tSML_ID\n")
            snapshot.assays[0].files.append(PackageFile(
                file_id=199, filename=export.name, storage_uri=export.as_uri(), role="msdial_mztabm",
                sha256=hashlib.sha256(export.read_bytes()).hexdigest(), size_bytes=export.stat().st_size))
            return export

        def declare(root, snapshot, quantity):
            path = root / "source-1/metabolensr_manifest.json"
            path.write_text(path.read_text().replace('"area"', f'"{quantity}"'))
            snapshot.assays[0].files[3].sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
            snapshot.assays[0].files[3].size_bytes = path.stat().st_size

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = example_snapshot(root)
            export = add_export(snapshot, root / "source-1", "peak height")
            self.assertEqual(export_quantity(export), "height")
            declare(root, snapshot, "unspecified")
            result = metabolights_export.build_package(snapshot, root / "undeclared")
            self.assertFalse(any(i["code"] in {"unknown_quantity", "quantity_conflict"} for i in result["issues"]))
            self.assertIn("quantity: peak height (named by the MS-DIAL export)",
                          (root / "undeclared/submission/i_Investigation.txt").read_text())
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = example_snapshot(root)
            add_export(snapshot, root / "source-1", "peak height")
            result = metabolights_export.build_package(snapshot, root / "contradicted")  # declares area
            self.assertTrue(any(i["code"] == "quantity_conflict" for i in result["issues"]))
        self.assertEqual(resolve_quantity("height", None), {"value": "height", "origin": "declared by the run"})
        self.assertEqual(resolve_quantity("unspecified", None)["value"], None)

    def test_empty_final_inventory_is_not_a_complete_bundle(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = example_snapshot(root)
            path = root / "source-1/metabolensr_manifest.json"
            manifest = json.loads(path.read_text())
            manifest["outputs"] = []
            path.write_text(json.dumps(manifest))
            snapshot.assays[0].files[-1].sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
            snapshot.assays[0].files[-1].size_bytes = path.stat().st_size
            with self.assertRaises(ValueError):
                metabolights_export.build_package(snapshot, root / "export")
