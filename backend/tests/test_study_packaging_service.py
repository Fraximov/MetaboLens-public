"""Package ownership, frozen sources and permissions use a real SQLite session."""
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from fastapi import HTTPException
from app.models.models import (Assay, AssayProtocolLink, AssaySampleLink, Dataset, DatasetFile,
                               MetadataSubmission, Protocol, Sample, Study, WorkflowJob, WorkflowRunArtifact)
from app.schemas.study_package import PackageRequest
from app.services.study_packaging_service import StudyPackagingService
from tests import test_advanced_backend_services as fixtures
from tests.test_metabolights_export import example_snapshot


class StudyPackagingServiceTests(unittest.TestCase):
    setUp = fixtures.AdvancedBackendServiceTests.setUp
    tearDown = fixtures.AdvancedBackendServiceTests.tearDown
    _user = fixtures.AdvancedBackendServiceTests._user
    _workflow_definition = fixtures.AdvancedBackendServiceTests._workflow_definition
    _create_study = fixtures.AdvancedBackendServiceTests._create_study
    _default_assay = fixtures.AdvancedBackendServiceTests._default_assay

    def fixture(self, root):
        snapshot = example_snapshot(root)
        self.study = self._create_study()
        self.user = self.study.created_by
        self.service = StudyPackagingService(self.db)
        for sample in snapshot.samples:
            self.db.add(Sample(id=sample.sample_id, study_id=self.study.id, name=sample.name,
                               sample_type=sample.sample_type))
        self.db.flush()
        self.sources = []
        for source in snapshot.assays:
            assay = self.db.get(Assay, source.assay_id)
            if not assay:
                assay = Assay(id=source.assay_id, study_id=self.study.id, name=source.assay_name, created_by=self.user)
                self.db.add(assay)
            dataset = Dataset(study_id=self.study.id, assay_id=assay.id, name="fixture", dataset_kind="derived",
                              dataset_type="workflow_outputs", created_by=self.user)
            self.db.add(dataset)
            metadata = MetadataSubmission(study_id=self.study.id, assay_id=assay.id, submitted_by=self.user,
                normalized_payload_json={"instrument": {"ms_polarity_mode": source.polarity}})
            self.db.add(metadata)
            self.db.flush()
            records = [*source.files, *(a.file for a in source.acquisitions)]
            for record in records:
                acquisition = next((a for a in source.acquisitions if a.file.file_id == record.file_id), None)
                self.db.add(DatasetFile(id=record.file_id, dataset_id=dataset.id, file_role=record.role,
                    original_filename=record.filename, storage_uri=record.storage_uri, checksum_sha256=record.sha256,
                    file_size_bytes=record.size_bytes, sample_id=acquisition.sample_id if acquisition else None,
                    created_by=self.user))
            job = WorkflowJob(id=source.job_id, study_id=self.study.id, assay_id=assay.id,
                workflow_definition_id=self._workflow_definition().id, requested_by=self.user, status="succeeded",
                input_manifest_json={"metadata_submission_id": metadata.id, "selected_files": [dict(
                    file_asset_id=a.file.file_id, sample_id=a.sample_id, storage_uri=a.file.storage_uri,
                    checksum_sha256=a.file.sha256) for a in source.acquisitions]})
            self.db.add(job)
            self.db.flush()
            for record in source.files:
                self.db.add(WorkflowRunArtifact(workflow_job_id=job.id, dataset_file_id=record.file_id, artifact_role=record.role))
            self.sources.append(dict(assay_id=assay.id, job_id=job.id, manifest_file_id=source.manifest_file_id))
        self.db.commit()
        self.request = PackageRequest(request_key=uuid4(), sources=self.sources)

    def test_freezes_all_sources_idempotently_without_mutating_scientific_jobs(self):
        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            job = self.db.get(WorkflowJob, self.sources[0]["job_id"])
            job.input_manifest_json = {**job.input_manifest_json, "feature_review": {"revision": 2}}
            self.db.commit()
            package = self.service.request_build(self.user, self.study.id, self.request)
            repeated = self.service.request_build(self.user, self.study.id, self.request)
            self.assertEqual(package.id, repeated.id)
            self.assertEqual(package.status, "queued")
            self.assertEqual(len(package.snapshot_json["assays"]), 2)
            self.assertEqual(len(package.snapshot_json["samples"]), 3)
            self.assertEqual(package.snapshot_json["contacts"], [])
            self.assertEqual(package.snapshot_json["assays"][0]["review_revision"], 2)
            self.assertEqual(self.service.serialize(package)["sources"][0]["review_revision"], 2)
            self.assertIsNone(package.snapshot_json["assays"][1]["review_revision"])
            self.study.title = "Changed after request"
            self.db.commit()
            self.assertNotEqual(package.snapshot_json["title"], self.study.title)
            self.assertEqual({j.status for j in self.db.query(WorkflowJob)}, {"succeeded"})
            self.assertEqual(package.revision, 1)

    def test_choices_show_distinct_samples_and_hide_known_unsupported_engines(self):
        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            negative = self.db.get(WorkflowJob, self.sources[1]["job_id"])
            inputs = negative.input_manifest_json["selected_files"]
            # Equal injection counts must not imply equal biological coverage.
            negative.input_manifest_json = {**negative.input_manifest_json,
                "selected_files": [*inputs, inputs[0]]}
            self.db.commit()
            choices = self.service.choices(self.user, self.study.id)
            positive_source, negative_source = [a["sources"][0] for a in choices["assays"]]
            self.assertEqual(positive_source["acquisition_count"], 3)
            self.assertEqual(negative_source["acquisition_count"], 3)
            self.assertEqual(positive_source["sample_ids"], [1, 2])
            self.assertEqual(negative_source["sample_ids"], [1, 3])
            self.assertEqual([s["sample_id"] for s in choices["samples"]], [1, 2, 3])
            negative.input_manifest_json = {**negative.input_manifest_json,
                "engine": {"descriptor": {"engine_id": "masscube"}}}
            self.db.commit()
            self.assertEqual(self.service.choices(self.user, self.study.id)["assays"][1]["sources"], [])

    def test_column_type_uses_recorded_separation_mode_not_stationary_phase(self):
        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            for index, source in enumerate(self.sources):
                job = self.db.get(WorkflowJob, source["job_id"])
                metadata = self.db.get(MetadataSubmission, job.input_manifest_json["metadata_submission_id"])
                metadata.normalized_payload_json = {"chromatography": {
                    "column_type": "Amide", "separation_mode": "HILIC" if index == 0 else "",
                    "lc_system": "Vanquish UHPLC"}}
            self.db.commit()
            package = self.service.request_build(self.user, self.study.id, self.request)
            positive, negative = package.snapshot_json["assays"]
            field = "Parameter Value[Column type]"
            self.assertEqual(positive["method"][field], "HILIC")
            self.assertEqual(positive["method_ontology_terms"][field], {
                "name": "HILIC", "source": "MTBLS",
                "accession": "http://www.ebi.ac.uk/metabolights/ontology/MTBLS_000856"})
            self.assertEqual(positive["method"]["Parameter Value[Chromatography Instrument]"],
                             "Thermo Scientific Vanquish UHPLC System")
            self.assertEqual(negative["method"][field], "")  # Amide alone cannot establish HILIC.
            self.assertNotIn(field, negative["method_ontology_terms"])
            self.assertEqual(metadata.normalized_payload_json["chromatography"]["column_type"], "Amide")

    def test_saved_metadata_and_sample_sheet_values_are_frozen_in_new_packages(self):
        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            self.study.metadata_json = {
                "revision": 2, "contains_examples": True,
                "contacts": [{"first_name": "Example", "last_name": "Researcher",
                              "email": "pi@example.org", "affiliation": "Example laboratory",
                              "role": "Principal Investigator"}],
                "submission_date": "2026-09-30",
                "design_descriptors": [{"name": "Example study design", "source": "", "accession": ""}],
                "factors": [{"name": "Treatment", "source_column": "group_label",
                             "type": {"name": "treatment", "source": "", "accession": ""}}],
            }
            sample = self.db.get(Sample, 1)
            sample.annotations_json = {"group_label": "control"}
            collection = Protocol(study_id=self.study.id, name="Sample collection",
                                  protocol_type="Sample collection", summary="Example collection description.")
            extraction = Protocol(study_id=self.study.id, name="Extraction",
                                  protocol_type="Extraction", summary="Example extraction description.")
            self.db.add_all([collection, extraction])
            self.db.flush()
            self.db.add(AssayProtocolLink(assay_id=self.sources[0]["assay_id"], protocol_id=extraction.id))
            self.db.commit()
            package = self.service.request_build(self.user, self.study.id, self.request)
            snapshot = package.snapshot_json
            self.assertEqual(snapshot["contacts"], [{"First Name": "Example", "Last Name": "Researcher",
                "Email": "pi@example.org", "Affiliation": "Example laboratory", "Roles": "Principal Investigator"}])
            # A key left by the withdrawn example prototype is not frozen into packages.
            self.assertNotIn("contains_examples", snapshot)
            self.assertEqual(snapshot["submission_date"], "2026-09-30")
            self.assertEqual(snapshot["design_descriptors"][0]["name"], "Example study design")
            self.assertEqual(snapshot["factors"][0]["name"], "Treatment")
            self.assertEqual(snapshot["collection_protocol"], "Example collection description.")
            self.assertEqual(snapshot["assays"][0]["protocols"]["Extraction"], "Example extraction description.")
            frozen_sample = next(s for s in snapshot["samples"] if s["sample_id"] == 1)
            self.assertEqual(frozen_sample["factor_values"], {"Treatment": "control"})
            sample.annotations_json = {"group_label": "changed after packaging"}
            self.study.metadata_json = {"revision": 3, "contacts": []}
            self.db.commit()
            self.assertEqual(package.snapshot_json["contacts"][0]["First Name"], "Example")
            self.assertEqual(frozen_sample["factor_values"], {"Treatment": "control"})

    def test_readiness_check_reports_sections_without_building_or_reading_spectra(self):
        from unittest.mock import patch
        from app.models.models import StudyPackage
        from app.schemas.study_package import PackageCheck
        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            with patch("app.services.metabolights_export.file_digest", side_effect=AssertionError("hashed")):
                result = self.service.check(self.user, self.study.id, PackageCheck(sources=self.sources))
            self.assertEqual(self.db.query(StudyPackage).count(), 0)
            codes = {(issue["code"], issue["section"]) for issue in result["issues"]}
            for expected in (("missing_request_id", "identifier"), ("missing_contacts", "study"),
                             ("missing_publication", "study"), ("missing_organism", "samples"),
                             ("missing_collection_protocol", "protocols"), ("missing_assay_method", "sources")):
                self.assertIn(expected, codes)
            self.assertEqual(result["spectral"], {"files": 5, "bytes": result["spectral"]["bytes"], "unhashed": 0})
            self.assertGreater(result["spectral"]["bytes"], 0)
            self.assertEqual(result["sections"]["identifier"], {"errors": 1, "warnings": 0})
            self.assertEqual(result["errors"], sum(s["errors"] for s in result["sections"].values()))
            # Same names in the fixture: every sample is reported as qualified.
            shared = next(i for i in result["issues"] if i["code"] == "sample_names_qualified")
            self.assertEqual(shared["items"], ["Same display name"])

            empty = self.service.check(self.user, self.study.id, PackageCheck())
            self.assertIn("no_sources", [i["code"] for i in empty["issues"]])
            self.assertEqual(empty["spectral"]["files"], 0)

            Path(tmp, "source-1/injection-0.mzML").unlink()
            missing = self.service.check(self.user, self.study.id, PackageCheck(sources=self.sources))
            lost = next(i for i in missing["issues"] if i["code"] == "spectral_file_unavailable")
            self.assertEqual((lost["section"], lost["items"]), ("sources", ["injection-0.mzML"]))

    def test_readiness_counts_unhashed_spectra_until_an_earlier_build_verified_them(self):
        from app.models.models import StudyPackage
        from app.schemas.study_package import PackageCheck
        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            first = self.sources[0]
            job = self.db.get(WorkflowJob, first["job_id"])
            file_id = job.input_manifest_json["selected_files"][0]["file_asset_id"]
            self.db.get(DatasetFile, file_id).checksum_sha256 = None
            job.input_manifest_json = {**job.input_manifest_json, "selected_files": [
                {**entry, "checksum_sha256": None} if entry["file_asset_id"] == file_id else entry
                for entry in job.input_manifest_json["selected_files"]]}
            self.db.commit()
            check = lambda: self.service.check(self.user, self.study.id, PackageCheck(sources=self.sources))  # noqa: E731
            self.assertEqual(check()["spectral"]["unhashed"], 1)
            self.assertIn("input_checksums_pending", [i["code"] for i in check()["issues"]])
            self.db.add(StudyPackage(study_id=self.study.id, revision=1, request_key="earlier", requested_by=self.user,
                                     request_json={}, snapshot_json={}, status="validation_failed",
                                     report_json={"spectral_files": [{"file_id": file_id, "sha256": "0" * 64}]}))
            self.db.commit()
            self.assertEqual(check()["spectral"]["unhashed"], 0)

    def test_readiness_check_turns_selection_errors_into_sectioned_findings(self):
        from app.schemas.study_package import PackageCheck
        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            self.study.metadata_json = {"factors": [{"name": "Treatment", "source_column": "group_label",
                "type": {"name": "treatment", "source": "", "accession": ""}}]}
            self.db.get(Sample, 1).annotations_json = {"group_label": "control"}
            self.db.add(AssaySampleLink(assay_id=self.sources[1]["assay_id"], sample_id=1,
                                       annotations_json={"group_label": "treated"}))
            self.db.commit()
            result = self.service.check(self.user, self.study.id, PackageCheck(sources=self.sources))
            conflict = next(i for i in result["issues"] if i["code"] == "factor_conflict")
            self.assertEqual(conflict["section"], "samples")
            self.assertIn("Treatment", conflict["message"])
            partial = self.service.check(self.user, self.study.id, PackageCheck(sources=self.sources[:1]))
            self.assertIn(("selection_invalid", "sources"),
                          {(i["code"], i["section"]) for i in partial["issues"]})

    def test_export_requests_and_revision_deletion_keep_sources_and_exports(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from app.models.models import AuditEvent, RoleAssignment, StudyPackage
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            package = self.service.request_build(self.user, self.study.id, self.request)
            with self.assertRaises(HTTPException):  # nothing built yet
                self.service.request_export(self.user, self.study.id, package.id)
            folder = root / "packages" / f"package-{package.id}"
            (folder / "submission").mkdir(parents=True)
            source = root / "source-1/injection-0.mzML"
            (folder / "submission/injection-0.mzML").symlink_to(source)
            (folder / "README.txt").write_text("package")
            package.status, package.output_uri = "validation_failed", folder.as_uri()
            self.db.commit()
            queued = self.service.request_export(self.user, self.study.id, package.id)
            self.assertEqual(queued.export_status, "queued")
            again = self.service.request_export(self.user, self.study.id, package.id)
            self.assertEqual(again.export_requested_at, queued.export_requested_at)
            self.assertEqual(self.service.serialize(again)["export"]["status"], "queued")
            settings = SimpleNamespace(study_package_root=str(root / "packages"))
            with patch("app.services.study_packaging_service.get_settings", return_value=settings):
                with self.assertRaises(HTTPException):  # export still active
                    self.service.delete(self.user, self.study.id, package.id)
                package.export_status, package.export_uri = "exported", (root / "exports/study-1-r1").as_uri()
                self.db.commit()
                submitter = self._user("submitter@metabolens.local")
                self.db.add(RoleAssignment(user_id=submitter.id, study_id=self.study.id, role="submitter"))
                self.db.commit()
                with self.assertRaises(HTTPException) as denied:
                    self.service.delete(submitter.id, self.study.id, package.id)
                self.assertEqual(denied.exception.status_code, 403)
                result = self.service.delete(self.user, self.study.id, package.id)
            self.assertFalse(folder.exists())
            self.assertTrue(source.is_file())  # links are removed, never followed
            self.assertEqual(result["kept_export_uri"], (root / "exports/study-1-r1").as_uri())
            self.assertEqual(self.db.query(StudyPackage).count(), 0)
            self.assertEqual(self.db.query(AuditEvent).filter_by(event_type="study.package_deleted").count(), 1)
            rebuilt = self.service.request_build(self.user, self.study.id,
                                                 self.request.model_copy(update={"request_key": uuid4()}))
            self.assertEqual(rebuilt.revision, 2)  # the deleted revision's number is not reused
            self.assertEqual(self.service.choices(self.user, self.study.id)["next_revision"], 3)

    def test_files_are_listed_and_previewed_only_once_a_revision_is_built(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            package = self.service.request_build(self.user, self.study.id, self.request)
            settings = SimpleNamespace(study_package_root=str(root / "packages"))
            with patch("app.services.study_packaging_service.get_settings", return_value=settings):
                with self.assertRaises(HTTPException):  # queued: nothing built yet
                    self.service.files(self.user, self.study.id, package.id)
                submission = root / "packages" / f"package-{package.id}" / "submission"
                submission.mkdir(parents=True)
                (submission / "i_Investigation.txt").write_text("STUDY\nStudy Title\tExample subset\n")
                package.status, package.output_uri = "validation_failed", submission.parent.as_uri()
                package.report_json = {"issues": [dict(code="rule_i", severity="warning", message="m",
                                                       file="i_Investigation.txt")]}
                self.db.commit()
                listing = self.service.files(self.user, self.study.id, package.id)
                preview = self.service.file_preview(self.user, self.study.id, package.id, "i_Investigation.txt")
        self.assertEqual([(e["path"], e["warnings"]) for e in listing["entries"]], [("i_Investigation.txt", 1)])
        self.assertEqual(preview["sections"], [{"name": "STUDY", "fields": [{"label": "Study Title",
                                                                             "values": ["Example subset"]}]}])

    def test_samples_with_linked_spectra_cannot_be_deleted_until_their_revisions_are(self):
        from app.models.models import StudyPackage
        from app.services.study_service import StudyService
        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            package = self.service.request_build(self.user, self.study.id, self.request)
            studies = StudyService(self.db)
            with self.assertRaises(HTTPException) as blocked:  # a queued build will link the file
                studies.delete_sample(self.user, self.study.id, 2)
            self.assertEqual(blocked.exception.status_code, 409)
            self.assertIn("injection-2.mzML (revision 1)", blocked.exception.detail)
            # A revision that copied its spectra does not depend on the originals.
            package.status, package.report_json = "validation_failed", {"issues": []}
            self.db.commit()
            self.assertEqual(studies.delete_sample(self.user, self.study.id, 2)["deleted_sample_id"], 2)
            package.report_json = {"spectral_files": [{"file_id": 100}]}
            self.db.commit()
            with self.assertRaises(HTTPException):
                studies.delete_sample(self.user, self.study.id, 1)
            self.db.delete(self.db.get(StudyPackage, package.id))
            self.db.commit()
            self.assertEqual(studies.delete_sample(self.user, self.study.id, 1)["deleted_sample_id"], 1)

    def test_serialized_reports_keep_every_finding_but_bound_id_lists(self):
        from app.models.models import StudyPackage
        review = dict(code="unresolved_annotation_decisions", severity="error",
                      feature_ids=[str(i) for i in range(3663)], message="3663 features require review.")
        package = StudyPackage(id=6, study_id=9, revision=6, status="validation_failed", snapshot_json={},
            report_json={"issues": [review, dict(code="missing_request_id", severity="error", message="")],
                         "spectral_files": [{"file_id": 567, "sha256": "0" * 64}], "files": [{"path": "x"}]})
        report = StudyPackagingService.serialize(package)["report"]
        self.assertEqual([issue["code"] for issue in report["issues"]],
                         ["unresolved_annotation_decisions", "missing_request_id"])
        self.assertEqual(report["issues"][0]["feature_ids"], [str(i) for i in range(20)])
        self.assertEqual(report["issues"][0]["feature_ids_total"], 3663)
        self.assertNotIn("spectral_files", report)
        self.assertNotIn("files", report)
        self.assertEqual(len(package.report_json["issues"][0]["feature_ids"]), 3663)

    def test_conflicting_assay_groups_cannot_be_exported_as_one_biological_factor(self):
        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            self.study.metadata_json = {"factors": [{"name": "Treatment", "source_column": "group_label",
                "type": {"name": "treatment", "source": "", "accession": ""}}]}
            self.db.get(Sample, 1).annotations_json = {"group_label": "control"}
            self.db.add(AssaySampleLink(assay_id=self.sources[1]["assay_id"], sample_id=1,
                                       annotations_json={"group_label": "treated"}))
            self.db.commit()
            with self.assertRaises(HTTPException) as raised:
                self.service.request_build(self.user, self.study.id, self.request)
            self.assertEqual(raised.exception.status_code, 400)
            self.assertIn("Treatment", str(raised.exception.detail))

    def test_factor_preview_reports_empty_assay_overrides_as_build_conflicts(self):
        from app.services.study_metadata_service import StudyMetadataService
        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            self.study.metadata_json = {"factors": [{"name": "Treatment", "source_column": "group_label",
                "type": {"name": "treatment"}}]}
            sample = self.db.get(Sample, 1)
            sample.annotations_json = {"group_label": "control"}
            positive = AssaySampleLink(assay_id=self.sources[0]["assay_id"], sample_id=sample.id,
                                      annotations_json={})
            negative = AssaySampleLink(assay_id=self.sources[1]["assay_id"], sample_id=sample.id,
                                      annotations_json={"group_label": ""})
            self.db.add_all([positive, negative])
            self.db.commit()
            metadata = StudyMetadataService(self.db)
            summary = next(c for c in metadata.get(self.user, self.study.id)["factor_columns"]
                           if c["value"] == "group_label")
            self.assertEqual(summary["conflicts"], [sample.name])
            self.assertEqual(summary["values"], [{"value": "control", "count": 1}])
            with self.assertRaises(HTTPException) as raised:
                self.service.request_build(self.user, self.study.id, self.request)
            self.assertEqual(raised.exception.status_code, 400)
            self.assertIn("conflicting values", str(raised.exception.detail))

            # Matching empty values are missing metadata, not a cross-assay conflict.
            positive.annotations_json = {"group_label": ""}
            self.db.commit()
            summary = next(c for c in metadata.get(self.user, self.study.id)["factor_columns"]
                           if c["value"] == "group_label")
            self.assertEqual((summary["conflicts"], summary["values"]), ([], []))

    def test_build_refreshes_metadata_committed_after_its_initial_study_read(self):
        from unittest.mock import patch
        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            get_study = self.service.studies.get

            def read_then_save_metadata(user_id, study_id):
                loaded = get_study(user_id, study_id)
                with self.SessionLocal() as other:
                    changed = other.get(Study, study_id)
                    changed.title = "Metadata saved before the build lock"
                    changed.metadata_json = {"revision": 4, "contacts": [
                        {"first_name": "Ada", "last_name": "Lovelace", "email": "", "affiliation": "", "role": ""}]}
                    other.add(Protocol(study_id=study_id, name="Sample collection",
                        protocol_type="Sample collection", summary="Collection from metadata revision 4."))
                    other.commit()
                return loaded

            with patch.object(self.service.studies, "get", side_effect=read_then_save_metadata):
                package = self.service.request_build(self.user, self.study.id, self.request)
            self.assertEqual(package.snapshot_json["title"], "Metadata saved before the build lock")
            self.assertEqual(package.snapshot_json["contacts"][0]["Last Name"], "Lovelace")
            self.assertIn("revision 4", package.snapshot_json["metadata_origin"])
            self.assertEqual(package.snapshot_json["collection_protocol"], "Collection from metadata revision 4.")

    def test_registered_inputs_without_checksums_can_be_queued_without_rewriting_job_history(self):
        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            job = self.db.get(WorkflowJob, self.sources[0]["job_id"])
            inputs = [{**item, "checksum_sha256": None} for item in job.input_manifest_json["selected_files"]]
            job.input_manifest_json = {**job.input_manifest_json, "selected_files": inputs}
            for item in inputs:
                self.db.get(DatasetFile, item["file_asset_id"]).checksum_sha256 = None
            self.study.description = "A simple study description."
            self.db.commit()
            package = self.service.request_build(self.user, self.study.id, self.request)
            self.assertEqual(package.status, "queued")
            self.assertEqual(package.snapshot_json["description"], "A simple study description.")
            self.assertIsNone(package.snapshot_json["assays"][0]["acquisitions"][0]["file"]["sha256"])
            self.assertEqual(job.input_manifest_json["selected_files"], inputs)
            # Missing historical input checksums must not relax final-output checks.
            output = self.db.get(DatasetFile, self.sources[0]["manifest_file_id"])
            output.checksum_sha256 = None
            with self.assertRaises(HTTPException):
                self.service.file_snapshot(output, "metabolensr_manifest")

    def test_rejects_foreign_deleted_preview_or_duplicate_sources(self):
        for change in ("foreign", "deleted", "preview", "duplicate", "omission"):
            with self.subTest(change=change), TemporaryDirectory() as tmp:
                # Fresh DB for every invalid input, so failed requests cannot mask each other.
                self.tearDown()
                self.setUp()
                self.fixture(Path(tmp))
                request = self.request.model_copy(deep=True)
                if change == "foreign":
                    request.sources[0].assay_id = 999
                elif change == "deleted":
                    self.db.get(WorkflowJob, self.sources[0]["job_id"]).deleted_at = datetime.now(timezone.utc)
                    self.db.commit()
                elif change == "preview":
                    self.db.query(WorkflowRunArtifact).filter_by(dataset_file_id=self.sources[0]["manifest_file_id"]).one().artifact_role = "metabolensr_preview_manifest"
                    self.db.commit()
                elif change == "duplicate":
                    request.sources.append(request.sources[0])
                else:
                    request.sources.pop()
                with self.assertRaises(HTTPException):
                    self.service.request_build(self.user, self.study.id, request)

    def test_read_access_does_not_allow_build_and_completed_packages_survive_job_deletion(self):
        from app.models.models import RoleAssignment, StudyPackage
        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            viewer = self._user("submitter@metabolens.local")
            self.db.add(RoleAssignment(user_id=viewer.id, study_id=self.study.id, role="viewer"))
            self.db.commit()
            with self.assertRaises(HTTPException) as error:
                self.service.request_build(viewer.id, self.study.id, self.request)
            self.assertEqual(error.exception.status_code, 403)
            package = self.service.request_build(self.user, self.study.id, self.request)
            package.status = "validation_failed"
            self.db.get(WorkflowJob, self.sources[0]["job_id"]).deleted_at = datetime.now(timezone.utc)
            self.db.commit()
            self.assertEqual(self.service.get(viewer.id, self.study.id, package.id).id, package.id)
            self.assertEqual(self.db.query(StudyPackage).count(), 1)

    def test_real_build_survives_source_deletion_and_unavailable_validator_is_visible(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from worker.services.study_package_builder import PackageBuildRunner
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            package = self.service.request_build(self.user, self.study.id, self.request)
            settings = SimpleNamespace(study_package_root=str(root / "packages"),
                metabolights_python="/missing/validator", metabolights_opa="/missing/opa")
            runner = PackageBuildRunner(self.SessionLocal, settings)
            try:
                runner.build(package.id)
            finally:
                runner.close()
            self.db.expire_all()
            self.assertEqual(package.status, "validation_failed")
            self.assertTrue(any(i["code"] == "validator_unavailable" for i in package.report_json["issues"]))
            self.assertTrue((root / "packages" / f"package-{package.id}" / "submission/i_Investigation.txt").exists())
            self.db.get(WorkflowJob, self.sources[0]["job_id"]).deleted_at = datetime.now(timezone.utc)
            self.db.commit()
            with patch("app.services.study_packaging_service.get_settings", return_value=settings):
                download = self.service.metadata_download(self.user, self.study.id, package.id)
                self.assertTrue(download.is_file())
                download.write_bytes(b"tampered")
                with self.assertRaises(HTTPException):
                    self.service.metadata_download(self.user, self.study.id, package.id)
