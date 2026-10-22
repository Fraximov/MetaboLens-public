"""Submission metadata reuses study/sample records without changing run history."""
import unittest

from fastapi import HTTPException

from app.models.models import (Assay, AssayProtocolLink, AssaySampleLink, AuditEvent,
                               Protocol, RoleAssignment, Sample, User)
from app.services.study_metadata_service import StudyMetadataService
from tests import test_advanced_backend_services as fixtures


class StudyMetadataServiceTests(unittest.TestCase):
    setUp = fixtures.AdvancedBackendServiceTests.setUp
    tearDown = fixtures.AdvancedBackendServiceTests.tearDown
    _user = fixtures.AdvancedBackendServiceTests._user
    _create_study = fixtures.AdvancedBackendServiceTests._create_study
    _default_assay = fixtures.AdvancedBackendServiceTests._default_assay

    def service(self):
        return StudyMetadataService(self.db)

    def test_unsaved_metadata_loads_empty_without_invented_values(self):
        study = self._create_study()
        study.description = ""
        sample = Sample(study_id=study.id, name="S1", organism="human", matrix="plasma",
                        annotations_json={"group_label": "Control", "batch_id": "1",
                                          "factors": {"treatment": "vehicle"}})
        self.db.add(sample)
        self.db.commit()
        view = self.service().get(study.created_by, study.id)
        metadata = view["metadata"]
        self.assertEqual(metadata.title, "T")
        self.assertEqual((metadata.description, metadata.contacts, metadata.design_descriptors,
                          metadata.publications, metadata.collection_protocol), ("", [], [], [], ""))
        self.assertEqual(set(metadata.assays[0].protocols.values()), {""})
        self.assertEqual(view["samples"][0]["annotations"]["group_label"], "Control")
        self.assertEqual({c["value"] for c in view["factor_columns"]}, {"group_label", "factors.treatment"})
        self.db.expire_all()
        self.assertEqual(study.metadata_json, {})
        self.assertEqual(self.db.query(Protocol).count(), 0)

    def test_save_persists_reviewed_fields_and_protocols_without_creating_people(self):
        from app.schemas.study_metadata import StudyContact
        study = self._create_study()
        study.status = "completed"
        # contains_examples was stored by the withdrawn example prototype.
        study.metadata_json = {"labid_reference": "preserve me", "contains_examples": True}
        self.db.commit()
        service = self.service()
        user_count = self.db.query(User).count()
        payload = service.get(study.created_by, study.id)["metadata"]
        payload.description = "A real description supplied by the researcher."
        payload.contacts = [StudyContact(first_name="Ada", last_name="Lovelace", email="pi@example.org",
                                         affiliation="Analytical Engines Lab", role="principal investigator")]
        payload.collection_protocol = "Plasma collected on ice and stored at -80 °C."
        payload.assays[0].protocols = {"Extraction": "Methanol extraction.", "Chromatography": "HILIC gradient.",
                                       "Mass spectrometry": "Orbitrap, positive mode."}
        saved = service.save(study.created_by, study.id, payload)["metadata"]
        self.assertEqual(saved.revision, 1)
        self.assertEqual(saved.description, payload.description)
        self.assertEqual(saved.contacts[0].email, "pi@example.org")
        self.assertEqual(saved.assays[0].protocols, payload.assays[0].protocols)
        self.assertEqual(study.metadata_json["labid_reference"], "preserve me")
        self.assertNotIn("contains_examples", study.metadata_json)
        self.assertEqual(study.status, "completed")
        self.assertEqual(self.db.query(User).count(), user_count)
        self.assertEqual(self.db.query(Protocol).count(), 4)
        self.assertEqual(self.db.query(AssayProtocolLink).count(), 3)
        event = self.db.query(AuditEvent).filter_by(event_type="study.submission_metadata_updated").one()
        self.assertIn("contacts", event.payload_json["changed_fields"])
        self.assertNotIn("pi@example.org", str(event.payload_json))
        with self.assertRaises(HTTPException) as error:
            service.save(study.created_by, study.id, payload)
        self.assertEqual(error.exception.status_code, 409)

    def test_readers_cannot_save_and_non_draft_submitters_cannot_edit(self):
        study = self._create_study("submitter@metabolens.local")
        viewer = self._user("operator@metabolens.local")
        # Strip this fixture user's global role to make an actual study viewer.
        self.db.query(RoleAssignment).filter_by(user_id=viewer.id).delete()
        self.db.add(RoleAssignment(user_id=viewer.id, study_id=study.id, role="viewer"))
        self.db.commit()
        service = self.service()
        payload = service.get(viewer.id, study.id)["metadata"]
        self.assertFalse(service.get(viewer.id, study.id)["can_edit"])
        with self.assertRaises(HTTPException) as error:
            service.save(viewer.id, study.id, payload)
        self.assertEqual(error.exception.status_code, 403)
        self.assertTrue(service.get(study.created_by, study.id)["can_edit"])
        study.status = "approved"
        self.db.commit()
        with self.assertRaises(HTTPException) as error:
            service.save(study.created_by, study.id, payload)
        self.assertEqual(error.exception.status_code, 403)

    def test_sample_details_stay_correctable_after_processing_and_are_audited(self):
        from app.schemas.study import SampleCreate, SamplePatch
        from app.services.study_service import StudyService
        study = self._create_study()
        studies = StudyService(self.db)
        sample = studies.add_sample(study.created_by, study.id, SampleCreate(name="1_Cystc", description="",
                                                                            organism="Homo sapiens"))
        study.status = "processing"
        self.db.commit()
        corrected = studies.update_sample(study.created_by, study.id, sample.id,
                                          SamplePatch(name="1_Cystic", matrix="cultured cells"))
        self.assertEqual((corrected.name, corrected.matrix), ("1_Cystic", "cultured cells"))
        event = self.db.query(AuditEvent).filter_by(event_type="sample.update").one()
        self.assertEqual(event.payload_json["study_status"], "processing")
        self.assertEqual(event.payload_json["changes"]["name"], {"before": "1_Cystc", "after": "1_Cystic"})
        viewer = self._user("operator@metabolens.local")
        self.db.query(RoleAssignment).filter_by(user_id=viewer.id).delete()
        self.db.add(RoleAssignment(user_id=viewer.id, study_id=study.id, role="viewer"))
        study.status = "archived"
        self.db.commit()
        for user_id in (viewer.id, study.created_by):
            with self.subTest(user_id=user_id), self.assertRaises(HTTPException) as error:
                studies.update_sample(user_id, study.id, sample.id, SamplePatch(matrix="plasma"))
            self.assertEqual(error.exception.status_code, 403)
        # Adding samples stays draft-only.
        study.status = "processing"
        self.db.commit()
        with self.assertRaises(HTTPException):
            studies.add_sample(study.created_by, study.id, SampleCreate(name="new", description=""))

    def test_study_edit_invalidates_older_metadata_forms_without_losing_other_fields(self):
        from app.schemas.study import StudyPatch
        from app.services.study_service import StudyService
        study = self._create_study()
        study.metadata_json = {"labid_reference": "preserve me"}
        self.db.commit()
        service = self.service()
        form = service.get(study.created_by, study.id)["metadata"]
        StudyService(self.db).patch(study.created_by, study.id, StudyPatch(description="New study description"))
        with self.assertRaises(HTTPException) as error:
            service.save(study.created_by, study.id, form)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(study.description, "New study description")
        self.assertEqual(study.metadata_json, {"labid_reference": "preserve me", "revision": 1})
        # Saving identical values should not needlessly invalidate another form.
        StudyService(self.db).patch(study.created_by, study.id, StudyPatch(description="New study description"))
        self.assertEqual(study.metadata_json["revision"], 1)
        StudyService(self.db).patch(study.created_by, study.id, StudyPatch(title="Updated title"))
        self.assertEqual(study.metadata_json["revision"], 2)

    def test_factor_definitions_reference_existing_values_and_assay_overrides(self):
        from app.schemas.study_metadata import FactorDefinition
        study = self._create_study()
        sample = Sample(study_id=study.id, name="S1", annotations_json={"group_label": "Control"})
        self.db.add(sample)
        self.db.flush()
        self.db.add(AssaySampleLink(assay_id=self._default_assay(study.id).id, sample_id=sample.id,
                                   annotations_json={"characteristics": {"sex": "female"}, "batch_id": "2"}))
        self.db.commit()
        service = self.service()
        view = service.get(study.created_by, study.id)
        self.assertEqual({c["value"] for c in view["factor_columns"]}, {"group_label", "characteristics.sex"})
        # The preview shows effective per-assay values, not only the Study default.
        assay_id = self._default_assay(study.id).id
        self.assertEqual(view["samples"][0]["assay_values"][assay_id],
                         {"characteristics.sex": "female", "group_label": "Control"})
        sex = next(c for c in view["factor_columns"] if c["value"] == "characteristics.sex")
        self.assertEqual((sex["values"], sex["conflicts"]), ([{"value": "female", "count": 1}], []))
        payload = view["metadata"]
        payload.factors = [FactorDefinition(name="Treatment", source_column="group_label",
                                            type={"name": "treatment"})]
        saved = service.save(study.created_by, study.id, payload)["metadata"]
        self.assertEqual(saved.factors[0].source_column, "group_label")
        self.assertEqual(sample.annotations_json, {"group_label": "Control"})
        saved.factors[0].source_column = "batch_id"
        with self.assertRaises(HTTPException) as error:
            service.save(study.created_by, study.id, saved)
        self.assertEqual(error.exception.status_code, 400)

    def test_factor_names_stay_identical_between_investigation_and_sample_headers(self):
        from pydantic import ValidationError
        from app.schemas.study_metadata import FactorDefinition
        factor = FactorDefinition(name="Sample  group", source_column="factors.original  key")
        self.assertEqual(factor.name, "Sample group")
        self.assertEqual(factor.source_column, "factors.original  key")
        for name in ("Dose [mM]", "Dose]", "Dose\tgroup", "Dose\ngroup", "Dose\rgroup"):
            with self.subTest(name=name), self.assertRaises(ValidationError):
                FactorDefinition(name=name, source_column="group_label")

    def test_assay_protocol_update_preserves_shared_and_foreign_records(self):
        study = self._create_study()
        assay = self._default_assay(study.id)
        other = Assay(study_id=study.id, name="Negative", created_by=study.created_by)
        foreign = self._create_study()
        protocol = Protocol(study_id=study.id, name="Existing extraction", protocol_type="Extraction",
                            summary="Original", parameters_json={"solvent": "methanol"})
        foreign_protocol = Protocol(study_id=foreign.id, name="Foreign", protocol_type="Chromatography",
                                    summary="Must remain private")
        self.db.add_all([other, protocol, foreign_protocol])
        self.db.flush()
        links = [AssayProtocolLink(assay_id=a.id, protocol_id=protocol.id) for a in (assay, other)]
        self.db.add_all([*links, AssayProtocolLink(assay_id=assay.id, protocol_id=foreign_protocol.id)])
        self.db.commit()
        service = self.service()
        view = service.get(study.created_by, study.id)
        # Both assays link the same extraction record, so each names the other.
        self.assertEqual(view["protocol_sharing"], {assay.id: {"Extraction": ["Negative"]},
                                                    other.id: {"Extraction": [assay.name]}})
        payload = view["metadata"]
        self.assertEqual(payload.assays[0].protocols["Chromatography"], "")
        payload.assays[0].protocols["Extraction"] = "Corrected extraction"
        saved = service.save(study.created_by, study.id, payload)["metadata"]
        self.assertEqual(saved.assays[0].protocols["Extraction"], "Corrected extraction")
        self.assertEqual(saved.assays[1].protocols["Extraction"], "Original")
        self.assertEqual(protocol.summary, "Original")
        self.assertEqual(foreign_protocol.summary, "Must remain private")
        replacement = self.db.get(Protocol, links[0].protocol_id)
        self.assertEqual(replacement.parameters_json, {"solvent": "methanol"})
        saved.assays[0].assay_id = self._default_assay(foreign.id).id
        with self.assertRaises(HTTPException) as error:
            service.save(study.created_by, study.id, saved)
        self.assertEqual(error.exception.status_code, 400)
