import hashlib
import json
import shutil
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory, gettempdir

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import BigInteger, create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.api.routes import assays as assay_routes
from app.models.models import Acquisition, Assay, AssaySampleLink, AuditEvent, Dataset, DatasetFile, DatasetSampleLink, MetadataSubmission, ProvenanceEvent, ResultSet, RoleAssignment, Sample, User, WorkflowDefinition, WorkflowJob, WorkflowRunArtifact
from app.schemas.assay import AssayCreate
from app.schemas.workflow import WorkflowLaunch, WorkflowOutputFileOut
from app.schemas.study import SampleBulkCreateItem, SampleBulkPathCreateItem, SampleCreate, SamplePatch, SamplePathReference, StudyCreate
from app.services.study_service import StudyService
from app.services.workflow_launch_service import WorkflowLaunchService
from app.services.workflow_is_qc_service import WorkflowIsQcService
from app.services.file_service import FileService
from app.services.metadata_service import MetadataService
from app.services.seed_service import SeedService
from app.services.workflow_artifact_registration_service import WorkflowArtifactRegistrationService
from app.services.workflow_eic_service import WorkflowEicService
from app.services.workflow_job_deletion_service import WorkflowJobDeletionService
from app.services.workflow_definition_service import WorkflowDefinitionService
from app.services.workflow_job_read_service import WorkflowJobReadService
from app.services.workflow_job_state_service import WorkflowJobStateService

# The frozen part of an MS-DIAL engine job that continuation reads.
MSDIAL_ENGINE_MANIFEST = {"engine": {"descriptor": {"review_transitions": {"is_qc_review": "qc_preview", "qc_preview": "full"}}}}


class AdvancedBackendServiceTests(unittest.TestCase):
    def test_masscube_template_preview_launch_and_frozen_edited_values(self):
        admin = self._user('admin@metabolens.local')
        study = self._create_study()
        StudyService(self.db).transition(admin.id, study.id, 'approved')
        assay = self._default_assay(study.id)
        self._add_metadata(study.id, assay.id, submitted_by=admin.id, status='valid')
        files = [self._add_file(study.id, assay.id, created_by=admin.id, status='uploaded',
                               name=f'sample_{i}.mzML', with_sample=True).id for i in (1, 2)]
        workflow = self.db.query(WorkflowDefinition).filter_by(name='metabolensr-masscube').one()
        definitions = WorkflowDefinitionService(self.db)
        launches = WorkflowLaunchService(self.db)
        with TemporaryDirectory() as temp:
            root = Path(temp)
            relative = 'templates/masscube/comparison.csv'
            source = root / relative
            source.parent.mkdir(parents=True)
            original = 'parameter,value,description\nms1_abs_int_tol,30000,Scan intensity\n'
            source.write_text(original)
            (root / 'fixture.nf').write_text('// fixture')
            descriptor = {**workflow.static_input_config_json['engine'],
                          'source_paths': ['fixture.nf'], 'entrypoint': 'fixture.nf'}
            workflow.static_input_config_json = {'engine': descriptor, 'masscube_config_path': relative}
            workflow.pipeline_root = str(root)
            self.db.commit()
            catalog = definitions.list_libraries(admin.id, workflow.id)
            self.assertEqual(catalog['default_masscube_config_path'], relative)
            self.assertEqual(catalog['masscube_templates'][0]['value'], relative)
            preview = definitions.get_masscube_template_content(admin.id, workflow.id, relative)
            self.assertEqual(preview['content'], original)
            edited = original.replace('30000', '42000')
            job = assay_routes.launch_workflow(assay.id, workflow.id, WorkflowLaunch(
                parameters={'masscube_config_path': relative}, file_ids=files,
                masscube_template_text=edited), db=self.db, user=admin)
            self.assertEqual(job.parameter_values_json['masscube_parameters']['ms1_abs_int_tol'], 42000)
            self.assertEqual(job.input_manifest_json['engine']['parameters'], job.parameter_values_json)
            snapshot = job.input_manifest_json['masscube_template']
            self.assertEqual(snapshot['content'], edited)
            self.assertEqual(snapshot['checksum_sha256'], hashlib.sha256(edited.encode()).hexdigest())
            self.assertEqual(snapshot['source_path'], relative)
            source.write_text(original.replace('30000', '99999'))
            self.assertEqual(job.input_manifest_json['masscube_template'], snapshot)
            default = launches.launch(admin.id, assay.id, workflow.id, file_ids=files)
            self.assertEqual(default.parameter_values_json['masscube_parameters']['ms1_abs_int_tol'], 99999)
            legacy = launches.launch(admin.id, assay.id, workflow.id,
                                    parameters={'masscube_parameters': {'mz_tol_ms1': .02}}, file_ids=files)
            self.assertEqual(legacy.parameter_values_json['masscube_parameters'], {'mz_tol_ms1': .02})
            self.assertNotIn('masscube_template', legacy.input_manifest_json)
            for params, text in [({'masscube_config_path': relative, 'masscube_parameters': {'mz_tol_ms1': .02}}, edited),
                                 ({'masscube_parameters': {'rt_upper_limit': 0}}, None),
                                 ({'masscube_config_path': relative}, edited + 'correct_rt,true,unsupported\n')]:
                count = self.db.query(WorkflowJob).count()
                with self.assertRaises(HTTPException) as error:
                    launches.launch(admin.id, assay.id, workflow.id, parameters=params,
                                   file_ids=files, masscube_template_text=text)
                self.assertEqual(error.exception.status_code, 400)
                self.assertEqual(self.db.query(WorkflowJob).count(), count)
            outside = root / 'outside.csv'
            outside.write_text(original)
            (source.parent / 'escape.csv').symlink_to(outside)
            for path in ('outside.csv', 'templates/masscube/escape.csv', '../outside.csv', 'missing.csv'):
                with self.subTest(path=path), self.assertRaises(HTTPException):
                    definitions.get_masscube_template_content(admin.id, workflow.id, path)

    def test_engine_launch_freezes_independent_review_stops_outside_scientific_parameters(self):
        from backend.tests.test_engine_contract import fixture_descriptor
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'fixture.nf').write_text('// fixture')
            admin = self._user('admin@metabolens.local')
            study = self._create_study()
            StudyService(self.db).transition(admin.id, study.id, 'approved')
            assay = self._default_assay(study.id)
            self._add_metadata(study.id, assay.id, submitted_by=admin.id, status='valid')
            raw = self._add_file(study.id, assay.id, created_by=admin.id, status='uploaded', name='sample.raw', with_sample=True)
            workflow = self._workflow_definition()
            workflow.pipeline_root = str(root)
            workflow.nextflow_entrypoint = 'fixture.nf'
            # Deliberately reversed: database JSON key order is not stage order.
            chains = [
                ({'qc_preview': 'full', 'is_qc_review': 'qc_preview'}, [
                    ([], 'full'), (['is_qc_review'], 'is_qc_review'),
                    (['qc_preview'], 'qc_preview'),
                    (['qc_preview', 'is_qc_review'], 'is_qc_review'),
                ]),
                ({'qc_preview': 'full'}, [([], 'full'), (['qc_preview'], 'qc_preview')]),
            ]
            for transitions, cases in chains:
                workflow.static_input_config_json = {'engine': {**fixture_descriptor(), 'review_transitions': transitions}}
                workflow.parameter_schema_json = {
                    'type': 'object', 'properties': {'workflow_stage': {
                        'type': 'string', 'enum': ['full', *transitions], 'default': 'full'}},
                    'additionalProperties': False,
                }
                self.db.commit()
                for selected, expected in cases:
                    with self.subTest(transitions=transitions, selected=selected):
                        job = assay_routes.launch_workflow(assay.id, workflow.id, WorkflowLaunch(
                            parameters={'workflow_stage': 'full'}, file_ids=[raw.id], review_stages=selected,
                        ), db=self.db, user=admin)
                        self.assertEqual(job.parameter_values_json['workflow_stage'], expected)
                        self.assertEqual(job.input_manifest_json.get('review_stages'), selected)
                        self.assertNotIn('review_stages', job.parameter_values_json)
                        self.assertNotIn('review_stages', job.input_manifest_json['engine']['parameters'])
                for invalid in (['unknown'], ['qc_preview', 'qc_preview'], ['is_qc_review'] if len(transitions) == 1 else ['full']):
                    with self.subTest(invalid=invalid), self.assertRaises(HTTPException) as error:
                        assay_routes.launch_workflow(assay.id, workflow.id, WorkflowLaunch(
                            file_ids=[raw.id], review_stages=invalid,
                        ), db=self.db, user=admin)
                    self.assertEqual(error.exception.status_code, 400)
                old_client = WorkflowLaunchService(self.db).launch(admin.id, assay.id, workflow.id,
                    parameters={'workflow_stage': 'qc_preview'}, file_ids=[raw.id])
                self.assertNotIn('review_stages', old_client.input_manifest_json)
                self.assertEqual(old_client.parameter_values_json['workflow_stage'], 'qc_preview')

    def test_is_qc_continuation_obeys_frozen_pause_selection_and_preserves_rt_choice(self):
        for selected, expected in [(['is_qc_review'], 'full'),
                                   (['is_qc_review', 'qc_preview'], 'qc_preview'),
                                   (None, 'qc_preview')]:
            with self.subTest(selected=selected):
                admin, assay, job = self._is_qc_review_job_with_rt_plan(can_apply=True)
                frozen_engine = {'descriptor': {'review_transitions': {
                    'qc_preview': 'full', 'is_qc_review': 'qc_preview'}}}
                job.input_manifest_json = {**job.input_manifest_json, 'engine': frozen_engine,
                    **({'review_stages': selected} if selected is not None else {})}
                self.db.commit()
                continued = WorkflowLaunchService(self.db).continue_review_stage(
                    admin.id, assay.id, job.id, apply_rt_correction=True)
                self.assertEqual(continued.parameter_values_json['workflow_stage'], expected)
                self.assertEqual(continued.input_manifest_json['engine'], frozen_engine)
                self.assertEqual(continued.input_manifest_json.get('review_stages'), selected)
                self.assertTrue(continued.parameter_values_json['nextflow_resume'])
                self.assertTrue(continued.parameter_values_json['apply_rt_correction'])
                targets = continued.parameter_values_json['rt_correction_target_ids']
                self.assertTrue(targets)
                if expected == 'qc_preview':
                    continued.status = 'succeeded'
                    self.db.commit()
                    final = WorkflowLaunchService(self.db).continue_review_stage(admin.id, assay.id, job.id)
                    self.assertEqual(final.parameter_values_json['workflow_stage'], 'full')
                    self.assertEqual(final.parameter_values_json['rt_correction_target_ids'], targets)
                    self.assertTrue(final.parameter_values_json['apply_rt_correction'])
                    self.assertEqual(final.input_manifest_json['submission_attempt'], 2)

    def test_failed_selected_review_retries_current_stage(self):
        admin, assay, job = self._is_qc_review_job_with_rt_plan(can_apply=True)
        job.input_manifest_json = {**job.input_manifest_json, 'review_stages': ['is_qc_review'],
            'engine': {'descriptor': {'review_transitions': {'is_qc_review': 'qc_preview', 'qc_preview': 'full'}}}}
        job.status = 'failed'
        job.status_payload_json = {'workflow_stage': 'is_qc_review', 'exit_code': 1}
        self.db.commit()
        retried = WorkflowLaunchService(self.db).continue_review_stage(admin.id, assay.id, job.id, operator_note='Retry failed task')
        self.assertEqual(retried.parameter_values_json['workflow_stage'], 'is_qc_review')
        self.assertEqual(retried.input_manifest_json['review_stages'], ['is_qc_review'])

    def test_engine_preview_continues_using_frozen_transition(self):
        admin = self._user('admin@metabolens.local')
        workflow = self._workflow_definition()
        study = self._create_study()
        assay = self._default_assay(study.id)
        job = WorkflowJob(
            study_id=study.id, assay_id=assay.id, workflow_definition_id=workflow.id,
            requested_by=admin.id, status='succeeded', run_root='/tmp/engine-preview',
            input_manifest_json={'engine': {'descriptor': {'review_transitions': {'qc_preview': 'full'}}}},
            parameter_values_json={'workflow_stage': 'qc_preview', 'analysis_mode': 'qc_only'},
            status_payload_json={'workflow_stage': 'qc_preview'},
        )
        self.db.add(job)
        self.db.commit()
        continued = WorkflowLaunchService(self.db).continue_review_stage(admin.id, assay.id, job.id)
        self.assertEqual(continued.status, 'queued')
        self.assertEqual(continued.input_manifest_json['submission_attempt'], 1)
        self.assertEqual(continued.parameter_values_json, {
            'workflow_stage': 'full', 'analysis_mode': 'qc_only', 'nextflow_resume': True,
        })
        self.assertEqual(continued.status_payload_json['continued_from_workflow_stage'], 'qc_preview')
        continued.input_manifest_json = {'engine': {'descriptor': {'review_transitions': {}}}}
        continued.parameter_values_json = {'workflow_stage': 'msdial_review'}
        continued.status = 'succeeded'
        self.db.commit()
        with self.assertRaises(HTTPException):
            WorkflowLaunchService(self.db).continue_review_stage(admin.id, assay.id, job.id)

    def test_assay_list_reports_each_assays_latest_live_workflow_job(self):
        admin = self._user('admin@metabolens.local')
        workflow = self._workflow_definition()
        study = self._create_study()
        default = self._default_assay(study.id)
        busy = StudyService(self.db).create_assay(admin.id, study.id, AssayCreate(name="busy", platform="metabolomics"))
        def job(assay, when, deleted=False):
            self.db.add(WorkflowJob(
                study_id=study.id, assay_id=assay.id, workflow_definition_id=workflow.id,
                requested_by=admin.id, status='succeeded', created_at=when,
                deleted_at=when if deleted else None,
                input_manifest_json={}, parameter_values_json={}, status_payload_json={},
            ))
        job(busy, datetime(2026, 9, 1, tzinfo=timezone.utc))
        job(busy, datetime(2026, 9, 20, tzinfo=timezone.utc))
        # A deleted run does not count as recent work.
        job(busy, datetime(2026, 9, 30, tzinfo=timezone.utc), deleted=True)
        self.db.commit()
        by_id = {a.id: a for a in StudyService(self.db).list_assays(admin.id, study.id)}
        self.assertIsNone(by_id[default.id].last_workflow_job_at)
        self.assertEqual(by_id[busy.id].last_workflow_job_at.replace(tzinfo=timezone.utc),
                         datetime(2026, 9, 20, tzinfo=timezone.utc))

    def test_engine_launch_freezes_descriptor_and_does_not_require_a_reference(self):
        from backend.tests.test_engine_contract import fixture_descriptor
        with TemporaryDirectory() as temp:
            root = Path(temp); (root / 'fixture.nf').write_text('// fixture')
            admin = self._user('admin@metabolens.local')
            study = self._create_study()
            StudyService(self.db).transition(admin.id, study.id, 'approved')
            assay = self._default_assay(study.id)
            self._add_metadata(study.id, assay.id, submitted_by=admin.id, status='valid')
            raw = self._add_file(study.id, assay.id, created_by=admin.id, status='uploaded', name='sample.raw', with_sample=True)
            workflow = self._workflow_definition()
            workflow.pipeline_root = str(root)
            workflow.nextflow_entrypoint = 'fixture.nf'
            workflow.static_input_config_json = {'engine': fixture_descriptor()}
            workflow.parameter_schema_json = {'type': 'object', 'properties': {'threshold': {'type': 'number', 'minimum': 0, 'default': 2}}, 'additionalProperties': False}
            self.db.commit()
            job = WorkflowLaunchService(self.db).launch(admin.id, assay.id, workflow.id, parameters={}, file_ids=[raw.id])
            self.assertIsNone(job.input_manifest_json['reference_file_asset_id'])
            self.assertEqual(job.input_manifest_json['engine']['descriptor']['adapter_id'], 'fixture-v1')
            self.assertEqual(job.parameter_values_json, {'threshold': 2})
            self.assertEqual(job.input_manifest_json['pipeline_root'], str(root))
            descriptor = fixture_descriptor()
            descriptor['package']['curation_profiles'] = {
                name: {'name': name, 'statistics': [], 'annotation': []}
                for name in ('strict', 'fixture')}
            descriptor['package']['supports_rule_selection'] = True
            workflow.static_input_config_json = {'engine': descriptor}
            self.db.commit()
            selected = assay_routes.launch_workflow(assay.id, workflow.id,
                WorkflowLaunch(file_ids=[raw.id], metabolensr_profile='fixture',
                               metabolensr_enabled_rules={'statistics': []},
                               metabolensr_expected_image=descriptor['package']['container_image']), db=self.db, user=admin)
            self.assertEqual(selected.input_manifest_json['engine']['descriptor']['package']['config']['profile'], 'fixture')
            self.assertEqual(selected.parameter_values_json, {'threshold': 2})
            self.assertEqual(selected.input_manifest_json['engine']['descriptor']['package']['config']['enabled_rules'],
                             {'statistics': []})
            self.assertNotIn('enabled_rules', workflow.static_input_config_json['engine']['package']['config'])
            self.assertEqual(workflow.static_input_config_json['engine']['package']['config']['profile'], 'strict')
            count = self.db.query(WorkflowJob).count()
            with self.assertRaises(HTTPException) as invalid_profile:
                assay_routes.launch_workflow(assay.id, workflow.id,
                    WorkflowLaunch(file_ids=[raw.id], metabolensr_profile='unavailable'), db=self.db, user=admin)
            self.assertEqual(invalid_profile.exception.status_code, 400)
            self.assertEqual(self.db.query(WorkflowJob).count(), count)
            with self.assertRaises(HTTPException) as invalid_rules:
                assay_routes.launch_workflow(assay.id, workflow.id,
                    WorkflowLaunch(file_ids=[raw.id], metabolensr_enabled_rules={'statistics': ['unknown']}),
                    db=self.db, user=admin)
            self.assertEqual(invalid_rules.exception.status_code, 400)
            self.assertEqual(self.db.query(WorkflowJob).count(), count)
            with self.assertRaises(HTTPException) as stale_package:
                assay_routes.launch_workflow(assay.id, workflow.id,
                    WorkflowLaunch(file_ids=[raw.id], metabolensr_expected_image='old@sha256:' + '7' * 64),
                    db=self.db, user=admin)
            self.assertEqual(stale_package.exception.status_code, 400)
            self.assertIn('Refresh', stale_package.exception.detail)
            self.assertEqual(self.db.query(WorkflowJob).count(), count)
            with self.assertRaises(HTTPException) as error:
                WorkflowLaunchService(self.db).launch(admin.id, assay.id, workflow.id, parameters={'threshold': -1}, file_ids=[raw.id])
            self.assertEqual(error.exception.status_code, 400)
            for limits in ({'minimum_acquisitions': 2}, {'input_extensions': ['.mzml']}):
                workflow.static_input_config_json = {'engine': {**fixture_descriptor(), **limits}}
                self.db.commit()
                count = self.db.query(WorkflowJob).count()
                with self.assertRaises(HTTPException) as error:
                    WorkflowLaunchService(self.db).launch(admin.id, assay.id, workflow.id, parameters={}, file_ids=[raw.id])
                self.assertEqual(error.exception.status_code, 400)
                self.assertEqual(self.db.query(WorkflowJob).count(), count)

    def test_dataset_file_size_supports_large_artifacts(self) -> None:
        self.assertIsInstance(DatasetFile.__table__.c.file_size_bytes.type, BigInteger)

    def test_workflow_job_soft_deletion_fields_default_to_none(self) -> None:
        admin = self._user("admin@metabolens.local")
        study = self._create_study()
        assay = self._default_assay(study.id)
        workflow = self._workflow_definition()
        job = WorkflowJob(
            study_id=study.id,
            assay_id=assay.id,
            workflow_definition_id=workflow.id,
            requested_by=admin.id,
            status="failed",
            input_manifest_json={},
            parameter_values_json={},
            status_payload_json={},
        )
        self.db.add(job)
        self.db.commit()

        self.assertIsNone(job.deleted_at)
        self.assertIsNone(job.deleted_by)
        self.assertIsNone(job.purged_at)

    def setUp(self) -> None:
        from app.config import get_settings
        self.original_raw_path_browse_roots = get_settings().raw_path_browse_roots
        get_settings().raw_path_browse_roots = gettempdir()
        self.pipeline_fixture = TemporaryDirectory()
        self.engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, future=True)
        Base.metadata.create_all(self.engine)
        self.db = self.SessionLocal()
        SeedService(self.db).run()
        self.db.commit()

    def tearDown(self) -> None:
        from app.config import get_settings
        get_settings().raw_path_browse_roots = self.original_raw_path_browse_roots
        self.db.close()
        self.engine.dispose()
        self.pipeline_fixture.cleanup()

    def _user(self, email: str) -> User:
        user = self.db.query(User).filter(User.email == email).first()
        assert user is not None
        return user

    def _workflow_definition(self) -> WorkflowDefinition:
        workflow = self.db.query(WorkflowDefinition).filter_by(name="metabolensr-msdial5").one()
        root = Path(self.pipeline_fixture.name)
        if not (root / workflow.nextflow_entrypoint).is_file():
            AdvancedBackendServiceTests._use_engine_pipeline_copy(self, workflow, root)
        workflow.pipeline_root = str(root)
        return workflow

    def _use_engine_pipeline_copy(self, workflow: WorkflowDefinition, root: Path) -> None:
        """Point `workflow` at `root`, holding a copy of its engine sources and default support files."""

        from app.services.workflow_definition_service import WorkflowDefinitionService
        source_root = WorkflowDefinitionService(self.db).resolve_pipeline_root(workflow)
        static = workflow.static_input_config_json
        defaults = [static[key] for key in static["engine"]["support_files"] if static.get(key)]
        for relative in [*static["engine"]["source_paths"], *defaults]:
            source, target = source_root / relative, root / relative
            if source.is_dir():
                shutil.copytree(source, target, dirs_exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                if relative in defaults and not source.is_file():
                    # Tests freeze bytes without executing engines; scientific libraries are operator-supplied.
                    target.write_text("synthetic test fixture\n", encoding="utf-8")
                else:
                    shutil.copyfile(source, target)
        workflow.pipeline_root = str(root)

    def _create_study(self, creator_email: str = "admin@metabolens.local"):
        creator = self._user(creator_email)
        return StudyService(self.db).create(creator.id, StudyCreate(title="T", description="D"))

    def _default_assay(self, study_id: int) -> Assay:
        assay = self.db.query(Assay).filter(Assay.study_id == study_id, Assay.is_default.is_(True)).first()
        assert assay is not None
        return assay

    def _terminal_job(self, *, status: str = "succeeded") -> WorkflowJob:
        admin = self._user("admin@metabolens.local")
        study = self._create_study()
        assay = self._default_assay(study.id)
        job = WorkflowJob(
            study_id=study.id,
            assay_id=assay.id,
            workflow_definition_id=self._workflow_definition().id,
            requested_by=admin.id,
            status=status,
            input_manifest_json={},
            parameter_values_json={},
            status_payload_json={},
        )
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        return job

    def test_soft_delete_terminal_workflow_job_preserves_execution_status(self) -> None:
        admin = self._user("admin@metabolens.local")
        job = self._terminal_job(status="succeeded")

        deleted = WorkflowJobDeletionService(self.db).soft_delete(admin.id, job.assay_id, job.id)

        self.assertEqual(deleted.status, "succeeded")
        self.assertEqual(deleted.deleted_by, admin.id)
        self.assertIsNotNone(deleted.deleted_at)
        event = self.db.query(AuditEvent).filter(
            AuditEvent.event_type == "workflow.delete",
            AuditEvent.target_type == "workflow_job",
            AuditEvent.target_id == str(job.id),
        ).one()
        self.assertEqual(event.actor_user_id, admin.id)

    def test_soft_delete_rejects_active_workflow_job(self) -> None:
        admin = self._user("admin@metabolens.local")
        job = self._terminal_job(status="running")

        with self.assertRaises(HTTPException) as raised:
            WorkflowJobDeletionService(self.db).soft_delete(admin.id, job.assay_id, job.id)

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIsNone(job.deleted_at)

    def test_soft_delete_requires_workflow_operator_permission(self) -> None:
        admin = self._user("admin@metabolens.local")
        submitter = self._user("submitter@metabolens.local")
        job = self._terminal_job(status="failed")
        self.db.add(
            RoleAssignment(
                user_id=submitter.id,
                study_id=job.study_id,
                role="submitter",
                granted_by=admin.id,
            )
        )
        self.db.commit()

        with self.assertRaises(HTTPException) as raised:
            WorkflowJobDeletionService(self.db).soft_delete(submitter.id, job.assay_id, job.id)

        self.assertEqual(raised.exception.status_code, 403)
        self.assertIsNone(job.deleted_at)

    def test_soft_delete_is_idempotent(self) -> None:
        admin = self._user("admin@metabolens.local")
        job = self._terminal_job(status="failed")
        service = WorkflowJobDeletionService(self.db)

        first = service.soft_delete(admin.id, job.assay_id, job.id)
        first_deleted_at = first.deleted_at
        second = service.soft_delete(admin.id, job.assay_id, job.id)

        self.assertEqual(second.deleted_at, first_deleted_at)
        self.assertEqual(
            self.db.query(AuditEvent)
            .filter(
                AuditEvent.event_type == "workflow.delete",
                AuditEvent.target_id == str(job.id),
            )
            .count(),
            1,
        )

    def test_purge_generated_records_preserves_job_raw_input_and_provenance(self) -> None:
        admin = self._user("admin@metabolens.local")
        job = self._terminal_job(status="succeeded")
        raw_dataset = Dataset(
            study_id=job.study_id,
            assay_id=job.assay_id,
            name="raw-inputs",
            dataset_kind="raw",
            dataset_type="mzml",
            status="registered",
            created_by=admin.id,
        )
        output_dataset = Dataset(
            study_id=job.study_id,
            assay_id=job.assay_id,
            name=f"job-{job.id}-derived",
            dataset_kind="derived",
            dataset_type="workflow_outputs",
            status="validated",
            created_by=admin.id,
        )
        self.db.add_all([raw_dataset, output_dataset])
        self.db.flush()
        raw_file = DatasetFile(
            dataset_id=raw_dataset.id,
            file_role="raw_input",
            original_filename="sample.mzML",
            storage_uri="file:///data/uploads/sample.mzML",
            status="registered",
            created_by=admin.id,
        )
        output_file = DatasetFile(
            dataset_id=output_dataset.id,
            file_role="report",
            original_filename="report.json",
            storage_uri=f"file:///runs/job_{job.id}/results/report.json",
            status="validated",
            created_by=admin.id,
        )
        self.db.add_all([raw_file, output_file])
        self.db.flush()
        job.input_manifest_json = {
            "file_ids": [raw_file.id],
            "selected_files": [{"file_asset_id": raw_file.id}],
            "reference_file_asset_id": raw_file.id,
        }
        self.db.add_all(
            [
                WorkflowRunArtifact(
                    workflow_job_id=job.id,
                    dataset_file_id=output_file.id,
                    artifact_role="report",
                ),
                WorkflowRunArtifact(
                    workflow_job_id=job.id,
                    dataset_file_id=raw_file.id,
                    artifact_role="legacy_raw_link",
                ),
            ]
        )
        self.db.add(
            ResultSet(
                study_id=job.study_id,
                assay_id=job.assay_id,
                workflow_job_id=job.id,
                result_type="workflow_run_summary",
                summary_json={},
            )
        )
        self.db.add(
            ProvenanceEvent(
                study_id=job.study_id,
                assay_id=job.assay_id,
                workflow_job_id=job.id,
                event_type="workflow.finished",
                source_ref_json={},
                output_ref_json={},
                software_versions_json={},
            )
        )
        self.db.commit()
        raw_file_id = raw_file.id
        output_file_id = output_file.id
        output_dataset_id = output_dataset.id
        WorkflowJobDeletionService(self.db).soft_delete(admin.id, job.assay_id, job.id)

        summary = WorkflowJobDeletionService(self.db).purge_generated_records(job.id)

        self.assertEqual(summary["deleted_file_count"], 1)
        self.assertEqual(summary["deleted_dataset_count"], 1)
        self.assertEqual(summary["deleted_result_count"], 1)
        self.assertIsNotNone(job.purged_at)
        self.assertIsNotNone(self.db.get(WorkflowJob, job.id))
        self.assertIsNotNone(self.db.get(DatasetFile, raw_file_id))
        self.assertIsNone(self.db.get(DatasetFile, output_file_id))
        self.assertIsNone(self.db.get(Dataset, output_dataset_id))
        self.assertEqual(self.db.query(ProvenanceEvent).filter(ProvenanceEvent.workflow_job_id == job.id).count(), 1)
        purge_event = self.db.query(AuditEvent).filter(
            AuditEvent.event_type == "workflow.purge",
            AuditEvent.target_id == str(job.id),
        ).one()
        self.assertIsNone(purge_event.actor_user_id)

    def test_soft_deleted_job_and_outputs_are_hidden(self) -> None:
        admin = self._user("admin@metabolens.local")
        job = self._terminal_job(status="succeeded")
        dataset = Dataset(
            study_id=job.study_id,
            assay_id=job.assay_id,
            name=f"job-{job.id}-derived",
            dataset_kind="derived",
            dataset_type="workflow_outputs",
            status="validated",
            created_by=admin.id,
        )
        self.db.add(dataset)
        self.db.flush()
        output = DatasetFile(
            dataset_id=dataset.id,
            sample_id=None,
            file_role="report",
            original_filename="report.json",
            storage_uri=f"file:///tmp/job_{job.id}/results/report.json",
            status="validated",
            created_by=admin.id,
        )
        self.db.add(output)
        self.db.flush()
        self.db.add(
            WorkflowRunArtifact(
                workflow_job_id=job.id,
                dataset_file_id=output.id,
                artifact_role="report",
            )
        )
        self.db.commit()

        WorkflowJobDeletionService(self.db).soft_delete(admin.id, job.assay_id, job.id)

        self.assertEqual(WorkflowJobReadService(self.db).list_jobs(admin.id, job.assay_id), [])
        self.assertEqual(WorkflowJobReadService(self.db).list_output_files(admin.id, job.assay_id), [])
        with self.assertRaises(HTTPException) as raised:
            WorkflowJobReadService(self.db).get_job(admin.id, job.id)
        self.assertEqual(raised.exception.status_code, 404)

    def test_soft_deleted_is_qc_artifact_is_hidden(self) -> None:
        admin, assay, job = self._is_qc_review_job_with_rt_plan(can_apply=True)
        WorkflowJobDeletionService(self.db).soft_delete(admin.id, assay.id, job.id)

        with self.assertRaises(HTTPException) as raised:
            WorkflowIsQcService(self.db).get_summary(admin.id, assay.id, job.id)

        self.assertEqual(raised.exception.status_code, 404)

    def test_soft_deleted_eic_artifact_is_hidden(self) -> None:
        admin = self._user("admin@metabolens.local")
        job = self._terminal_job(status="succeeded")
        dataset = Dataset(
            study_id=job.study_id,
            assay_id=job.assay_id,
            name=f"job-{job.id}-derived",
            dataset_kind="derived",
            dataset_type="workflow_outputs",
            status="validated",
            created_by=admin.id,
        )
        self.db.add(dataset)
        self.db.flush()
        tempdir = TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        root = Path(tempdir.name)
        eic_path = root / "AlignResult0.EIC.aef"
        alignment_path = root / "AlignResult0.mdalign"
        eic_path.write_bytes(b"EIC")
        alignment_path.write_text("Alignment ID\n", encoding="utf-8")
        eic_file = DatasetFile(
            dataset_id=dataset.id,
            sample_id=None,
            file_role="msdial_eic",
            original_filename=eic_path.name,
            storage_uri=f"file://{eic_path}",
            status="validated",
            created_by=admin.id,
        )
        alignment_file = DatasetFile(
            dataset_id=dataset.id,
            sample_id=None,
            file_role="msdial_alignment",
            original_filename=alignment_path.name,
            storage_uri=f"file://{alignment_path}",
            status="validated",
            created_by=admin.id,
        )
        self.db.add_all([eic_file, alignment_file])
        self.db.flush()
        self.db.add_all(
            [
                WorkflowRunArtifact(
                    workflow_job_id=job.id,
                    dataset_file_id=eic_file.id,
                    artifact_role="msdial_eic_archive",
                ),
                WorkflowRunArtifact(
                    workflow_job_id=job.id,
                    dataset_file_id=alignment_file.id,
                    artifact_role="msdial_alignment",
                ),
            ]
        )
        self.db.commit()
        WorkflowJobDeletionService(self.db).soft_delete(admin.id, job.assay_id, job.id)

        with self.assertRaises(HTTPException) as raised:
            WorkflowEicService(self.db)._get_eic_context(admin.id, job.assay_id, eic_file.id)

        self.assertEqual(raised.exception.status_code, 404)

    def test_soft_deleted_job_cannot_be_continued(self) -> None:
        admin = self._user("admin@metabolens.local")
        job = self._terminal_job(status="succeeded")
        job.parameter_values_json = {"workflow_stage": "qc_preview"}
        job.run_root = f"/tmp/job_{job.id}"
        self.db.commit()
        WorkflowJobDeletionService(self.db).soft_delete(admin.id, job.assay_id, job.id)

        with self.assertRaises(HTTPException) as raised:
            WorkflowLaunchService(self.db).continue_review_stage(admin.id, job.assay_id, job.id)

        self.assertEqual(raised.exception.status_code, 404)

    def test_study_summary_excludes_soft_deleted_jobs(self) -> None:
        admin = self._user("admin@metabolens.local")
        job = self._terminal_job(status="failed")
        WorkflowJobDeletionService(self.db).soft_delete(admin.id, job.assay_id, job.id)

        summary = StudyService(self.db).get_summary(admin.id, job.study_id)

        self.assertEqual(summary["workflow_job_count"], 0)
        self.assertEqual(summary["workflow_status_counts"], {})

    def _add_metadata(self, study_id: int, assay_id: int, submitted_by: int, status: str = "valid") -> MetadataSubmission:
        record = MetadataSubmission(
            study_id=study_id,
            assay_id=assay_id,
            submitted_by=submitted_by,
            raw_payload_json={"instrument": "QTOF"},
            normalized_payload_json={"instrument": "QTOF"},
            validation_status=status,
            validation_messages_json=[],
            schema_version="metabolens-v1",
            submitted_at=datetime.now(timezone.utc),
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return record

    def _is_qc_review_job_with_rt_plan(self, *, can_apply: bool):
        admin = self._user("admin@metabolens.local")
        workflow = self._workflow_definition()
        study = self._create_study("admin@metabolens.local")
        assay = self._default_assay(study.id)
        job = WorkflowJob(
            study_id=study.id,
            assay_id=assay.id,
            workflow_definition_id=workflow.id,
            requested_by=admin.id,
            status="succeeded",
            input_manifest_json={"file_ids": [], **MSDIAL_ENGINE_MANIFEST},
            parameter_values_json={"workflow_stage": "is_qc_review"},
            execution_backend="nextflow",
            execution_profile="local_docker",
            run_root=f"/tmp/metabolens-rt-job-{study.id}",
            status_payload_json={"workflow_stage": "is_qc_review", "is_qc_status": "pass"},
        )
        dataset = Dataset(
            study_id=study.id,
            assay_id=assay.id,
            name="RT correction review",
            dataset_kind="derived",
            dataset_type="workflow_output",
            status="registered",
            created_by=admin.id,
        )
        self.db.add_all([job, dataset])
        self.db.flush()

        tempdir = TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        root = Path(tempdir.name)
        payloads = {
            "is_qc_summary": ("is_qc_summary.json", {"overall_status": "pass", "counts": {}}),
            "is_qc_table": (
                "is_qc_targets.tsv",
                "target_id\ttarget_name\tsample_name\tinput_file\tobserved_rt_min\n"
                "1\tIS:A\tS1\tS1.mzML\t1.1\n"
                "2\tIS:B\tS1\tS1.mzML\t5.2\n",
            ),
            "is_qc_traces": ("is_qc_traces.json", {"traces": []}),
            "is_qc_rt_correction_plan": (
                "rt_correction_plan.json",
                {
                    "version": "msdial_rt_correction_v1",
                    "can_apply": can_apply,
                    "blocking_reasons": [] if can_apply else ["S1.mzML has no usable anchor"],
                    "samples": ([{
                        "sample_name": "S1",
                        "input_file": "S1.mzML",
                        "eligible": True,
                        "anchor_count": 2,
                        "anchors": [
                            {"target_id": "1", "target_name": "IS:A", "observed_rt_min": 1.1, "shift_min": 0.1},
                            {"target_id": "2", "target_name": "IS:B", "observed_rt_min": 5.2, "shift_min": 0.2},
                        ],
                        "warnings": [],
                    }] if can_apply else []),
                    "features": [
                        {
                            "target_id": "1",
                            "target_name": "IS:A",
                            "input_file": "S1.mzML",
                            "observed_rt_min": 1.1,
                            "corrected_rt_min": 1.0,
                            "shift_min": 0.1,
                        },
                        {
                            "target_id": "2",
                            "target_name": "IS:B",
                            "input_file": "S1.mzML",
                            "observed_rt_min": 5.2,
                            "corrected_rt_min": 5.0,
                            "shift_min": 0.2,
                        },
                    ],
                },
            ),
            "is_qc_rt_correction_preview": (
                "rt_correction_preview.json",
                {
                    "traces": [
                        {
                            "target_id": 1,
                            "target_name": "IS:A",
                            "input_file": "S1.mzML",
                            "observed_rt_min": 1.1,
                            "points": [{"rt_min": 1.1, "corrected_rt_min": 1.0, "intensity": 10.0}],
                        },
                        {
                            "target_id": 2,
                            "target_name": "IS:B",
                            "input_file": "S1.mzML",
                            "observed_rt_min": 5.2,
                            "points": [{"rt_min": 5.2, "corrected_rt_min": 5.0, "intensity": 20.0}],
                        },
                    ]
                },
            ),
        }
        for role, (filename, payload) in payloads.items():
            path = root / filename
            text = payload if isinstance(payload, str) else json.dumps(payload)
            path.write_text(text, encoding="utf-8")
            dataset_file = DatasetFile(
                dataset_id=dataset.id,
                file_role=role,
                original_filename=filename,
                storage_uri=f"file://{path}",
                checksum_sha256="a" * 64,
                file_size_bytes=path.stat().st_size,
                media_type="application/json" if filename.endswith(".json") else "text/tab-separated-values",
                status="registered",
                created_by=admin.id,
            )
            self.db.add(dataset_file)
            self.db.flush()
            self.db.add(
                WorkflowRunArtifact(
                    workflow_job_id=job.id,
                    dataset_file_id=dataset_file.id,
                    artifact_role=role,
                )
            )
        self.db.commit()
        return admin, assay, job

    def _ensure_raw_dataset(self, study_id: int, assay_id: int, created_by: int) -> Dataset:
        dataset = (
            self.db.query(Dataset)
            .filter(Dataset.study_id == study_id, Dataset.assay_id == assay_id, Dataset.dataset_kind == "raw")
            .first()
        )
        if dataset is None:
            dataset = Dataset(
                study_id=study_id,
                assay_id=assay_id,
                name="default-raw",
                dataset_kind="raw",
                dataset_type="thermo_raw_acquisition",
                status="registered",
                created_by=created_by,
            )
            self.db.add(dataset)
            self.db.commit()
            self.db.refresh(dataset)
        return dataset

    def _add_file(
        self,
        study_id: int,
        assay_id: int,
        created_by: int,
        status: str = "uploaded",
        name: str = "sample.raw",
        with_sample: bool = False,
        sample_metadata_complete: bool = True,
    ) -> DatasetFile:
        dataset = self._ensure_raw_dataset(study_id, assay_id, created_by)
        sample_id = None
        if with_sample:
            sample = Sample(
                study_id=study_id,
                name=Path(name).stem,
                description=Path(name).stem,
                replicate_number=1,
                dilution_factor=1.0,
                sample_type="sample",
                organism="Homo sapiens",
                matrix="plasma" if sample_metadata_complete else "",
                annotations_json=(
                    {"group_label": "Group A", "batch_id": "1", "injection_order": 1}
                    if sample_metadata_complete
                    else {}
                ),
            )
            self.db.add(sample)
            self.db.flush()
            sample_id = sample.id
            self.db.add(DatasetSampleLink(dataset_id=dataset.id, sample_id=sample.id, sample_role="input"))
        dataset_file = DatasetFile(
            dataset_id=dataset.id,
            sample_id=sample_id,
            file_role="thermo_raw",
            original_filename=name,
            storage_uri=f"file:///tmp/{study_id}/{name}",
            checksum_sha256="a" * 64,
            file_size_bytes=10,
            media_type="application/octet-stream",
            status=status,
            created_by=created_by,
        )
        self.db.add(dataset_file)
        self.db.commit()
        self.db.refresh(dataset_file)
        return dataset_file

    def test_admin_can_transition_any_direction(self) -> None:
        admin = self._user("admin@metabolens.local")
        study = self._create_study("admin@metabolens.local")
        service = StudyService(self.db)

        study = service.transition(admin.id, study.id, "completed")
        self.assertEqual(study.status, "completed")
        study = service.transition(admin.id, study.id, "draft")
        self.assertEqual(study.status, "draft")
        study = service.transition(admin.id, study.id, "archived")
        self.assertEqual(study.status, "archived")

    def test_serialized_study_after_create_exposes_default_assay_id(self) -> None:
        admin = self._user("admin@metabolens.local")
        study = self._create_study("admin@metabolens.local")

        serialized = StudyService(self.db).serialize_study(study)

        self.assertIsNotNone(serialized["default_assay_id"])

    def test_study_list_reports_sample_and_assay_counts(self) -> None:
        admin = self._user("admin@metabolens.local")
        study_service = StudyService(self.db)
        study = self._create_study("admin@metabolens.local")
        study_service.add_sample(admin.id, study.id, SampleCreate(name="S1"))
        study_service.create_assay(admin.id, study.id, AssayCreate(name="Secondary", assay_type="metabolomics_lcms"))

        rows = study_service.list_for_user(admin.id)
        row = next(item for item in rows if item["id"] == study.id)

        self.assertEqual(row["sample_count"], 1)
        self.assertEqual(row["assay_count"], 2)

    def test_add_sample_defaults_and_normalizes_sample_type(self) -> None:
        admin = self._user("admin@metabolens.local")
        study = self._create_study("admin@metabolens.local")
        service = StudyService(self.db)

        default_sample = service.add_sample(admin.id, study.id, SampleCreate(name="S1"))
        qc_sample = service.add_sample(admin.id, study.id, SampleCreate(name="QC1", sample_type="QC"))
        other_sample = service.add_sample(admin.id, study.id, SampleCreate(name="Other1", sample_type="Others"))
        standard_sample = service.add_sample(admin.id, study.id, SampleCreate(name="STD1", sample_type="std"))
        self.assertEqual(default_sample.sample_type, "sample")
        self.assertEqual(qc_sample.sample_type, "qc")
        self.assertEqual(other_sample.sample_type, "other")
        self.assertEqual(standard_sample.sample_type, "standard")

    def test_update_sample_merges_annotations(self) -> None:
        admin = self._user("admin@metabolens.local")
        study = self._create_study("admin@metabolens.local")
        service = StudyService(self.db)
        sample = service.add_sample(admin.id, study.id, SampleCreate(name="S1", annotations_json={"existing_key": "keep"}))

        updated = service.update_sample(
            admin.id,
            study.id,
            sample.id,
            SamplePatch(annotations_json={"group_label": "Lean"}),
        )

        self.assertEqual(updated.annotations_json["existing_key"], "keep")
        self.assertEqual(updated.annotations_json["group_label"], "Lean")
        self.assertNotIn("injection_order", updated.annotations_json)

    def test_sample_schema_rejects_invalid_sample_type_and_annotation_envelopes(self) -> None:
        with self.assertRaises(ValidationError):
            SampleCreate(name="S1", sample_type="control")

        with self.assertRaises(ValidationError):
            SampleCreate(name="S1", annotations_json={"factors": ["condition"]})

    def test_submitter_cannot_use_review_transition(self) -> None:
        submitter = self._user("submitter@metabolens.local")
        study = self._create_study("submitter@metabolens.local")
        service = StudyService(self.db)

        with self.assertRaises(HTTPException) as ctx:
            service.transition(submitter.id, study.id, "approved")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_workflow_launch_uses_selected_file_ids(self) -> None:
        admin = self._user("admin@metabolens.local")
        workflow = self._workflow_definition()
        study = self._create_study("admin@metabolens.local")
        StudyService(self.db).transition(admin.id, study.id, "approved")
        assay = self._default_assay(study.id)

        self._add_metadata(study.id, assay.id, submitted_by=admin.id, status="valid")
        file_a = self._add_file(study.id, assay.id, created_by=admin.id, status="uploaded", name="a.raw", with_sample=True)
        file_b = self._add_file(study.id, assay.id, created_by=admin.id, status="validated", name="b.raw", with_sample=True)

        job = WorkflowLaunchService(self.db).launch(
            user_id=admin.id,
            assay_id=assay.id,
            workflow_definition_id=workflow.id,
            parameters={},
            file_ids=[file_b.id],
        )
        self.assertEqual(job.input_manifest_json["file_ids"], [file_b.id])
        self.assertNotIn(file_a.id, job.input_manifest_json["file_ids"])
        self.assertEqual(job.input_manifest_json["reference_file_asset_id"], file_b.id)
        self.assertEqual(job.status_payload_json["phase"], "queued")
        self.assertEqual(job.execution_profile, "local_docker")

    def test_workflow_launch_cannot_bypass_is_qc_rt_correction_review(self) -> None:
        admin = self._user("admin@metabolens.local")
        workflow = self._workflow_definition()
        study = self._create_study("admin@metabolens.local")
        StudyService(self.db).transition(admin.id, study.id, "approved")
        assay = self._default_assay(study.id)
        self._add_metadata(study.id, assay.id, submitted_by=admin.id, status="valid")

        with self.assertRaises(HTTPException) as ctx:
            WorkflowLaunchService(self.db).launch(
                user_id=admin.id,
                assay_id=assay.id,
                workflow_definition_id=workflow.id,
                parameters={"apply_rt_correction": True},
            )

        self.assertIn("only when continuing", str(ctx.exception.detail).lower())

    def test_workflow_launch_accepts_hpc_execution_target(self) -> None:
        admin = self._user("admin@metabolens.local")
        workflow = self._workflow_definition()
        study = self._create_study("admin@metabolens.local")
        StudyService(self.db).transition(admin.id, study.id, "approved")
        assay = self._default_assay(study.id)

        self._add_metadata(study.id, assay.id, submitted_by=admin.id, status="valid")
        raw_file = self._add_file(study.id, assay.id, created_by=admin.id, status="uploaded", name="hpc.raw", with_sample=True)

        job = WorkflowLaunchService(self.db).launch(
            user_id=admin.id,
            assay_id=assay.id,
            workflow_definition_id=workflow.id,
            parameters={"execution_profile": "slurm_singularity_ssh"},
            file_ids=[raw_file.id],
        )

        self.assertEqual(job.execution_profile, "slurm_singularity_ssh")
        # The execution target is frozen beside, not inside, the scientific parameters.
        self.assertNotIn("execution_profile", job.parameter_values_json)
        self.assertEqual(job.input_manifest_json["execution_profile"], "slurm_singularity_ssh")
        self.assertEqual(job.input_manifest_json["nextflow_profile"], "embl_slurm")

    def test_workflow_launch_rejects_mismatched_execution_target_profile(self) -> None:
        admin = self._user("admin@metabolens.local")
        workflow = self._workflow_definition()
        study = self._create_study("admin@metabolens.local")
        StudyService(self.db).transition(admin.id, study.id, "approved")
        assay = self._default_assay(study.id)

        self._add_metadata(study.id, assay.id, submitted_by=admin.id, status="valid")

        with self.assertRaises(HTTPException) as ctx:
            WorkflowLaunchService(self.db).launch(
                user_id=admin.id,
                assay_id=assay.id,
                workflow_definition_id=workflow.id,
                parameters={"execution_profile": "slurm_singularity_ssh", "nextflow_profile": "docker"},
            )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("nextflow_profile 'docker' is not valid", str(ctx.exception.detail))

    def test_hpc_smoke_launch_creates_diagnostic_job_without_inputs_or_metadata(self) -> None:
        admin = self._user("admin@metabolens.local")
        workflow = self._workflow_definition()
        study = self._create_study("admin@metabolens.local")
        assay = self._default_assay(study.id)

        job = WorkflowLaunchService(self.db).launch_hpc_smoke_test(admin.id, assay.id, workflow.id)

        self.assertEqual(job.status, "queued")
        self.assertEqual(job.execution_profile, "slurm_singularity_ssh")
        self.assertEqual(job.parameter_values_json["workflow_stage"], "hpc_smoke")
        self.assertEqual(job.input_manifest_json["job_kind"], "hpc_smoke_test")
        self.assertEqual(job.input_manifest_json["file_ids"], [])
        self.assertIsNone(job.input_manifest_json["metadata_submission_id"])
        self.assertTrue(job.status_payload_json["diagnostic"])
        self.db.refresh(study)
        self.assertEqual(study.status, "draft")
        self.assertIsNotNone(
            self.db.query(AuditEvent)
            .filter(AuditEvent.event_type == "workflow.hpc_smoke.launch", AuditEvent.target_id == str(job.id))
            .first()
        )
        self.assertIsNotNone(
            self.db.query(ProvenanceEvent)
            .filter(
                ProvenanceEvent.event_type == "workflow.hpc_smoke_queued",
                ProvenanceEvent.workflow_job_id == job.id,
            )
            .first()
        )

    def test_hpc_smoke_launch_rejects_submitter(self) -> None:
        submitter = self._user("submitter@metabolens.local")
        workflow = self._workflow_definition()
        study = self._create_study("submitter@metabolens.local")
        assay = self._default_assay(study.id)

        with self.assertRaises(HTTPException) as ctx:
            WorkflowLaunchService(self.db).launch_hpc_smoke_test(submitter.id, assay.id, workflow.id)

        self.assertEqual(ctx.exception.status_code, 403)

    def test_hpc_smoke_launch_rejects_missing_workflow_definition(self) -> None:
        admin = self._user("admin@metabolens.local")
        study = self._create_study("admin@metabolens.local")
        assay = self._default_assay(study.id)

        with self.assertRaises(HTTPException) as ctx:
            WorkflowLaunchService(self.db).launch_hpc_smoke_test(admin.id, assay.id, 999999)

        self.assertEqual(ctx.exception.status_code, 404)

    def test_workflow_launch_rejects_missing_required_sample_metadata(self) -> None:
        admin = self._user("admin@metabolens.local")
        workflow = self._workflow_definition()
        study = self._create_study("admin@metabolens.local")
        StudyService(self.db).transition(admin.id, study.id, "approved")
        assay = self._default_assay(study.id)

        self._add_metadata(study.id, assay.id, submitted_by=admin.id, status="valid")
        raw_file = self._add_file(
            study.id,
            assay.id,
            created_by=admin.id,
            status="uploaded",
            name="missing.raw",
            with_sample=True,
            sample_metadata_complete=False,
        )

        with self.assertRaises(HTTPException) as ctx:
            WorkflowLaunchService(self.db).launch(
                user_id=admin.id,
                assay_id=assay.id,
                workflow_definition_id=workflow.id,
                parameters={},
                file_ids=[raw_file.id],
            )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("missing required sample metadata", str(ctx.exception.detail))
        self.assertIn("group_label", str(ctx.exception.detail))
        self.assertIn("injection_order", str(ctx.exception.detail))
        self.assertNotIn("batch_id", str(ctx.exception.detail))
        self.assertNotIn("matrix", str(ctx.exception.detail))

    def test_workflow_launch_defaults_missing_batch_id_to_one(self) -> None:
        admin = self._user("admin@metabolens.local")
        workflow = self._workflow_definition()
        study = self._create_study("admin@metabolens.local")
        StudyService(self.db).transition(admin.id, study.id, "approved")
        assay = self._default_assay(study.id)

        self._add_metadata(study.id, assay.id, submitted_by=admin.id, status="valid")
        raw_file = self._add_file(
            study.id,
            assay.id,
            created_by=admin.id,
            status="uploaded",
            name="missing_batch.raw",
            with_sample=True,
            sample_metadata_complete=True,
        )
        sample = self.db.query(Sample).filter(Sample.id == raw_file.sample_id).one()
        sample.annotations_json = {"group_label": "Group A", "injection_order": 1}
        self.db.commit()

        job = WorkflowLaunchService(self.db).launch(
            user_id=admin.id,
            assay_id=assay.id,
            workflow_definition_id=workflow.id,
            parameters={},
            file_ids=[raw_file.id],
        )

        self.assertEqual(job.status, "queued")
        self.db.refresh(sample)
        self.assertNotIn("batch_id", sample.annotations_json)
        assay_link = (
            self.db.query(AssaySampleLink)
            .filter(AssaySampleLink.assay_id == assay.id, AssaySampleLink.sample_id == sample.id)
            .one()
        )
        self.assertNotIn("batch_id", assay_link.annotations_json)
        self.assertEqual(raw_file.acquisition.annotations_json.get("batch_id"), "1")

    def test_workflow_launch_uses_assay_sample_annotations(self) -> None:
        admin = self._user("admin@metabolens.local")
        workflow = self._workflow_definition()
        study = self._create_study("admin@metabolens.local")
        StudyService(self.db).transition(admin.id, study.id, "approved")
        assay = self._default_assay(study.id)

        self._add_metadata(study.id, assay.id, submitted_by=admin.id, status="valid")
        raw_file = self._add_file(
            study.id,
            assay.id,
            created_by=admin.id,
            status="uploaded",
            name="assay_context.raw",
            with_sample=True,
            sample_metadata_complete=False,
        )
        sample = self.db.query(Sample).filter(Sample.id == raw_file.sample_id).one()
        sample.annotations_json = {"group_label": "Default group"}
        self.db.add(
            AssaySampleLink(
                assay_id=assay.id,
                sample_id=sample.id,
                sample_role="input",
                annotations_json={"group_label": "Assay group", "batch_id": "2", "injection_order": 5},
            )
        )
        self.db.commit()

        job = WorkflowLaunchService(self.db).launch(
            user_id=admin.id,
            assay_id=assay.id,
            workflow_definition_id=workflow.id,
            parameters={},
            file_ids=[raw_file.id],
        )

        self.assertEqual(job.status, "queued")

    def test_workflow_launch_rejects_non_lcms_assays(self) -> None:
        admin = self._user("admin@metabolens.local")
        workflow = self._workflow_definition()
        study = self._create_study("admin@metabolens.local")
        assay = StudyService(self.db).create_assay(
            admin.id,
            study.id,
            AssayCreate(name="GC-MS assay", metabolomics_assay_type="GC_MS"),
        )

        with self.assertRaises(HTTPException) as ctx:
            WorkflowLaunchService(self.db).launch(
                user_id=admin.id,
                assay_id=assay.id,
                workflow_definition_id=workflow.id,
            )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("LC-MS assays", str(ctx.exception.detail))

    def test_workflow_launch_ignores_non_raw_artifact_files(self) -> None:
        admin = self._user("admin@metabolens.local")
        workflow = self._workflow_definition()
        study = self._create_study("admin@metabolens.local")
        StudyService(self.db).transition(admin.id, study.id, "approved")
        assay = self._default_assay(study.id)

        self._add_metadata(study.id, assay.id, submitted_by=admin.id, status="valid")
        raw_file = self._add_file(study.id, assay.id, created_by=admin.id, status="registered", name="input.raw", with_sample=True)

        derived_dataset = Dataset(
            study_id=study.id,
            assay_id=assay.id,
            name="workflow-derived",
            dataset_kind="derived",
            dataset_type="workflow_outputs",
            status="registered",
            created_by=admin.id,
        )
        self.db.add(derived_dataset)
        self.db.commit()
        self.db.refresh(derived_dataset)

        artifact_file = DatasetFile(
            dataset_id=derived_dataset.id,
            sample_id=None,
            file_role="workflow_contract",
            original_filename="params.json",
            storage_uri="file:///tmp/params.json",
            checksum_sha256="b" * 64,
            file_size_bytes=100,
            media_type="application/json",
            status="uploaded",
            created_by=admin.id,
        )
        self.db.add(artifact_file)
        self.db.commit()

        job = WorkflowLaunchService(self.db).launch(
            user_id=admin.id,
            assay_id=assay.id,
            workflow_definition_id=workflow.id,
        )

        self.assertEqual(job.input_manifest_json["file_ids"], [raw_file.id])
        self.assertNotIn(artifact_file.id, job.input_manifest_json["file_ids"])

    def test_workflow_launch_rejects_invalid_selected_files(self) -> None:
        admin = self._user("admin@metabolens.local")
        workflow = self._workflow_definition()
        study = self._create_study("admin@metabolens.local")
        StudyService(self.db).transition(admin.id, study.id, "approved")
        assay = self._default_assay(study.id)

        self._add_metadata(study.id, assay.id, submitted_by=admin.id, status="valid")
        failed_file = self._add_file(study.id, assay.id, created_by=admin.id, status="failed", name="failed.raw", with_sample=True)

        with self.assertRaises(HTTPException) as ctx_ineligible:
            WorkflowLaunchService(self.db).launch(
                user_id=admin.id,
                assay_id=assay.id,
                workflow_definition_id=workflow.id,
                file_ids=[failed_file.id],
            )
        self.assertEqual(ctx_ineligible.exception.status_code, 400)

        with self.assertRaises(HTTPException) as ctx_missing:
            WorkflowLaunchService(self.db).launch(
                user_id=admin.id,
                assay_id=assay.id,
                workflow_definition_id=workflow.id,
                file_ids=[999999],
            )
        self.assertEqual(ctx_missing.exception.status_code, 400)

    def test_summary_reports_counts_and_metadata_timestamp(self) -> None:
        admin = self._user("admin@metabolens.local")
        workflow = self._workflow_definition()
        study = self._create_study("admin@metabolens.local")
        assay = self._default_assay(study.id)

        self._add_metadata(study.id, assay.id, submitted_by=admin.id, status="invalid")
        latest = self._add_metadata(study.id, assay.id, submitted_by=admin.id, status="valid")
        self._add_file(study.id, assay.id, created_by=admin.id, status="uploaded", name="c.raw")
        self._add_file(study.id, assay.id, created_by=admin.id, status="validated", name="d.raw")

        job = WorkflowJob(
            study_id=study.id,
            assay_id=assay.id,
            workflow_definition_id=workflow.id,
            requested_by=admin.id,
            status="queued",
            input_manifest_json={"file_ids": []},
            parameter_values_json={},
            execution_backend="nextflow",
            execution_profile="local_docker",
            status_payload_json={},
        )
        self.db.add(job)
        self.db.commit()

        summary = StudyService(self.db).get_summary(admin.id, study.id)
        self.assertEqual(summary["sample_count"], 0)
        self.assertEqual(summary["dataset_file_count"], 2)
        self.assertEqual(summary["latest_metadata_status"], "valid")
        self.assertEqual(summary["latest_metadata_submitted_at"], latest.submitted_at)
        self.assertEqual(summary["workflow_job_count"], 1)
        self.assertEqual(summary["workflow_status_counts"].get("queued"), 1)

    def test_seed_makes_neutral_metabolensr_engine_the_default(self) -> None:
        workflow = self.db.query(WorkflowDefinition).filter(
            WorkflowDefinition.name == "metabolensr-msdial5",
        ).one()
        from app.services.workflow_definition_service import WorkflowDefinitionService
        self.assertEqual(
            [item.name for item in WorkflowDefinitionService(self.db).list_definitions()],
            ["metabolensr-msdial5", "metabolensr-masscube"],
        )
        # A fresh database gets no retired legacy definition, and seeding is idempotent.
        self.assertIsNone(self.db.query(WorkflowDefinition).filter_by(name="msdial5-nextflow").first())
        SeedService(self.db).run()
        self.assertEqual(self.db.query(WorkflowDefinition).count(), 2)
        self.assertEqual(workflow.version, workflow.static_input_config_json["engine"]["package"]["version"])
        self.assertEqual(workflow.nextflow_entrypoint, "main_metabolensr.nf")
        self.assertEqual(workflow.execution_profile, "local_docker")
        descriptor = workflow.static_input_config_json["engine"]
        self.assertEqual(descriptor["engine_id"], "msdial5")
        self.assertEqual(descriptor["execution_profiles"], ["local_docker", "slurm_singularity_ssh"])
        self.assertEqual(descriptor["adapter_id"], "msdial5-v1")
        self.assertIn("reference_file_id", descriptor["required_inputs"])
        self.assertIn("workflow_stage", workflow.parameter_schema_json["properties"])
        self.assertEqual(workflow.static_input_config_json["ms1_library_path"], "libraries/ms1.txt")

    def test_masscube_catalog_uses_native_contract_without_msdial_requirements(self):
        workflow = self.db.query(WorkflowDefinition).filter_by(name='metabolensr-masscube').one()
        descriptor = workflow.static_input_config_json['engine']
        self.assertEqual(workflow.nextflow_entrypoint, 'main_masscube.nf')
        self.assertEqual(workflow.version, descriptor['package']['version'])
        self.assertEqual(descriptor['adapter_id'], 'masscube-v1')
        self.assertEqual(descriptor['evidence_adapter_id'], 'masscube-indexed-v1')
        self.assertEqual(descriptor['required_inputs'], {})
        self.assertEqual(workflow.static_input_config_json['masscube_config_path'],
                         'templates/masscube/orbitrap-msdial-comparison-v1.csv')
        from app.core.engine_contract import validate_engine_parameters
        self.assertEqual(validate_engine_parameters(workflow.parameter_schema_json, {})['masscube_parameters'], {})
        for unsupported in ({'enable_sirius': True}, {'workflow_stage': 'is_qc_review'},
                            {'masscube_parameters': {'correct_rt': True}},
                            {'masscube_parameters': {'mz_tol_ms1': 0}}):
            with self.subTest(parameters=unsupported), self.assertRaises(ValueError):
                validate_engine_parameters(workflow.parameter_schema_json, unsupported)

    def test_seed_keeps_retired_legacy_definition_inactive_with_its_jobs(self) -> None:
        from unittest.mock import patch
        admin = self._user("admin@metabolens.local")
        study = self._create_study()
        assay = self._default_assay(study.id)
        legacy = WorkflowDefinition(name="msdial5-nextflow", version="5.5", execution_backend="nextflow",
                                    nextflow_entrypoint="main_msdial5.nf", is_active=True)
        self.db.add(legacy)
        self.db.flush()
        job = WorkflowJob(
            study_id=study.id, assay_id=assay.id, workflow_definition_id=legacy.id,
            requested_by=admin.id, status="succeeded", run_root="/historical/run",
            input_manifest_json={"original": True}, parameter_values_json={"workflow_stage": "full"},
            status_payload_json={},
        )
        self.db.add(job)
        self.db.commit()
        SeedService(self.db).run()
        self.db.refresh(job)
        self.assertFalse(legacy.is_active)
        self.assertEqual(job.workflow_definition_id, legacy.id)
        self.assertEqual(job.status, "succeeded")
        self.assertEqual(job.input_manifest_json, {"original": True})
        self.assertEqual(job.run_root, "/historical/run")
        # Without engine files there is nothing to launch; the legacy definition is not a fallback.
        with patch.object(SeedService, "_ensure_metabolensr_engine_workflow_definition", return_value=None):
            SeedService(self.db).run()
        self.assertFalse(legacy.is_active)

    def test_sirius_parameters_reject_unknown_annotation_mode(self) -> None:
        workflow = self._workflow_definition()
        with self.assertRaises(HTTPException) as ctx:
            WorkflowLaunchService._validate_sirius_parameters(
                workflow,
                {
                    "enable_sirius": True,
                    "sirius_annotation_mode": "unknown",
                },
            )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("sirius_annotation_mode", str(ctx.exception.detail))

    def test_is_qc_continue_persists_approved_rt_correction(self) -> None:
        admin, assay, job = self._is_qc_review_job_with_rt_plan(can_apply=True)

        continued = WorkflowLaunchService(self.db).continue_review_stage(
            admin.id,
            assay.id,
            job.id,
            apply_rt_correction=True,
            rt_correction_target_ids=["2"],
        )

        self.assertTrue(continued.parameter_values_json["apply_rt_correction"])
        self.assertEqual(continued.parameter_values_json["rt_correction_target_ids"], ["2"])
        self.assertEqual(continued.status_payload_json["rt_correction_target_ids"], ["2"])
        self.assertEqual(continued.status_payload_json["rt_correction_decision"], "corrected")
        self.assertEqual(continued.parameter_values_json["workflow_stage"], "qc_preview")

    def test_is_qc_continue_rejects_ineligible_rt_correction(self) -> None:
        admin, assay, job = self._is_qc_review_job_with_rt_plan(can_apply=False)

        with self.assertRaises(HTTPException) as ctx:
            WorkflowLaunchService(self.db).continue_review_stage(
                admin.id,
                assay.id,
                job.id,
                apply_rt_correction=True,
            )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("not eligible", str(ctx.exception.detail).lower())

    def test_is_qc_continue_defaults_to_original_and_later_continue_preserves_choice(self) -> None:
        admin, assay, job = self._is_qc_review_job_with_rt_plan(can_apply=True)
        first = WorkflowLaunchService(self.db).continue_review_stage(admin.id, assay.id, job.id)
        self.assertFalse(first.parameter_values_json["apply_rt_correction"])
        first.status = "succeeded"
        first.parameter_values_json = {**first.parameter_values_json, "workflow_stage": "qc_preview"}
        self.db.commit()

        second = WorkflowLaunchService(self.db).continue_review_stage(admin.id, assay.id, first.id)

        self.assertFalse(second.parameter_values_json["apply_rt_correction"])

    def test_is_qc_rt_correction_choice_cannot_change_after_continuation(self) -> None:
        admin, assay, job = self._is_qc_review_job_with_rt_plan(can_apply=True)
        first = WorkflowLaunchService(self.db).continue_review_stage(
            admin.id,
            assay.id,
            job.id,
            apply_rt_correction=True,
            rt_correction_target_ids=["2"],
        )
        first.status = "succeeded"
        first.parameter_values_json = {**first.parameter_values_json, "workflow_stage": "qc_preview"}
        self.db.commit()

        with self.assertRaises(HTTPException) as ctx:
            WorkflowLaunchService(self.db).continue_review_stage(
                admin.id,
                assay.id,
                first.id,
                apply_rt_correction=True,
                rt_correction_target_ids=["1"],
            )

        self.assertIn("cannot be changed", str(ctx.exception.detail).lower())

    def test_is_qc_service_enriches_existing_review_responses(self) -> None:
        admin, assay, job = self._is_qc_review_job_with_rt_plan(can_apply=True)
        service = WorkflowIsQcService(self.db)

        summary = service.get_summary(admin.id, assay.id, job.id)
        targets = service.list_targets(admin.id, assay.id, job.id)
        traces = service.get_traces(admin.id, assay.id, job.id)

        self.assertTrue(summary["rt_correction"]["can_apply"])
        self.assertEqual(targets[0]["corrected_rt_min"], 1.0)
        self.assertIn("corrected_rt_min", traces["traces"][0]["points"][0])

    def test_is_qc_service_reads_raw_traces_before_rt_correction_plan_exists(self) -> None:
        admin, assay, job = self._is_qc_review_job_with_rt_plan(can_apply=True)
        raw_trace_artifact, raw_trace_file = (
            self.db.query(WorkflowRunArtifact, DatasetFile)
            .join(DatasetFile, DatasetFile.id == WorkflowRunArtifact.dataset_file_id)
            .filter(
                WorkflowRunArtifact.workflow_job_id == job.id,
                WorkflowRunArtifact.artifact_role == "is_qc_traces",
            )
            .one()
        )
        Path(raw_trace_file.storage_uri.removeprefix("file://")).write_text(
            json.dumps({
                "traces": [{
                    "target_id": 1,
                    "target_name": "IS:A",
                    "input_file": "S1.mzML",
                    "points": [{"rt_min": 1.1, "intensity": 10.0}],
                }]
            }),
            encoding="utf-8",
        )
        self.db.query(WorkflowRunArtifact).filter(
            WorkflowRunArtifact.workflow_job_id == job.id,
            WorkflowRunArtifact.artifact_role.in_({
                "is_qc_rt_correction_plan",
                "is_qc_rt_correction_preview",
            }),
        ).delete(synchronize_session=False)
        self.db.commit()

        traces = WorkflowIsQcService(self.db).get_traces(
            admin.id,
            assay.id,
            job.id,
            target_name="IS:A",
            rt_correction_target_ids=["1"],
        )

        self.assertEqual(len(traces["traces"]), 1)
        self.assertEqual(traces["traces"][0]["points"][0], {"rt_min": 1.1, "intensity": 10.0})

    def test_is_qc_service_projects_selected_compounds_without_hiding_excluded_features(self) -> None:
        admin, assay, job = self._is_qc_review_job_with_rt_plan(can_apply=True)
        service = WorkflowIsQcService(self.db)

        summary = service.get_summary(admin.id, assay.id, job.id, ["2"])
        targets = service.list_targets(admin.id, assay.id, job.id, ["2"])
        traces = service.get_traces(admin.id, assay.id, job.id, rt_correction_target_ids=["2"])

        self.assertTrue(summary["rt_correction"]["can_apply"])
        self.assertEqual(summary["rt_correction"]["selected_target_ids"], ["2"])
        self.assertEqual(len(targets), 2)
        self.assertFalse(targets[0]["used_for_rt_model"])
        self.assertTrue(targets[1]["used_for_rt_model"])
        self.assertNotEqual(targets[0]["corrected_rt_min"], 1.0)
        self.assertFalse(traces["traces"][0]["used_for_rt_model"])
        self.assertTrue(traces["traces"][1]["used_for_rt_model"])
        self.assertNotEqual(traces["traces"][0]["points"][0]["corrected_rt_min"], 1.0)

    def test_is_qc_service_rejects_unknown_selected_compound(self) -> None:
        admin, assay, job = self._is_qc_review_job_with_rt_plan(can_apply=True)

        with self.assertRaises(HTTPException) as ctx:
            WorkflowIsQcService(self.db).get_summary(admin.id, assay.id, job.id, ["99"])

        self.assertIn("unknown", str(ctx.exception.detail).lower())

    def test_is_qc_continue_rejects_selection_without_usable_anchor(self) -> None:
        admin, assay, job = self._is_qc_review_job_with_rt_plan(can_apply=True)

        with self.assertRaises(HTTPException) as ctx:
            WorkflowLaunchService(self.db).continue_review_stage(
                admin.id,
                assay.id,
                job.id,
                apply_rt_correction=True,
                rt_correction_target_ids=[],
            )

        self.assertIn("not eligible", str(ctx.exception.detail).lower())

    def test_retired_legacy_job_cannot_be_continued_or_retried(self) -> None:
        admin = self._user("admin@metabolens.local")
        workflow = self._workflow_definition()
        study = self._create_study("admin@metabolens.local")
        assay = self._default_assay(study.id)
        paused = WorkflowJob(
            study_id=study.id, assay_id=assay.id, workflow_definition_id=workflow.id, requested_by=admin.id,
            status="succeeded", input_manifest_json={"file_ids": []},
            parameter_values_json={"workflow_stage": "msdial_review"}, run_root="/tmp/metabolens/job_1",
            status_payload_json={"workflow_stage": "msdial_review"}, finished_at=datetime.now(timezone.utc),
        )
        failed = WorkflowJob(
            study_id=study.id, assay_id=assay.id, workflow_definition_id=workflow.id, requested_by=admin.id,
            status="failed", input_manifest_json={"file_ids": []},
            parameter_values_json={"workflow_stage": "full", "nextflow_resume": True}, run_root="/tmp/metabolens/job_2",
            status_payload_json={"workflow_stage": "full"}, finished_at=datetime.now(timezone.utc),
        )
        self.db.add_all([paused, failed])
        self.db.commit()

        for job, status in ((paused, "succeeded"), (failed, "failed")):
            with self.assertRaises(HTTPException) as raised:
                WorkflowLaunchService(self.db).continue_review_stage(admin.id, assay.id, job.id)
            self.assertEqual(raised.exception.status_code, 400)
            self.assertIn("retired MS-DIAL workflow", raised.exception.detail)
            self.db.refresh(job)
            self.assertEqual(job.status, status)

    def test_continue_after_failed_is_qc_review_requires_operator_note_and_requeues_next_review(self) -> None:
        admin = self._user("admin@metabolens.local")
        workflow = self._workflow_definition()
        study = self._create_study("admin@metabolens.local")
        assay = self._default_assay(study.id)
        job = WorkflowJob(
            study_id=study.id,
            assay_id=assay.id,
            workflow_definition_id=workflow.id,
            requested_by=admin.id,
            status="succeeded",
            input_manifest_json={"file_ids": [], **MSDIAL_ENGINE_MANIFEST},
            parameter_values_json={"workflow_stage": "is_qc_review"},
            execution_backend="nextflow",
            execution_profile="local_docker",
            run_root="/tmp/metabolens/job_is_qc",
            status_payload_json={"workflow_stage": "is_qc_review", "is_qc_status": "fail"},
            external_run_id="nf-local-is-qc",
            finished_at=datetime.now(timezone.utc),
        )
        self.db.add(job)
        self.db.commit()

        with self.assertRaises(HTTPException) as ctx:
            WorkflowLaunchService(self.db).continue_review_stage(admin.id, assay.id, job.id)
        self.assertIn("operator note", str(ctx.exception.detail).lower())

        continued = WorkflowLaunchService(self.db).continue_review_stage(
            admin.id,
            assay.id,
            job.id,
            operator_note="IS peaks inspected manually; RT shift is acceptable.",
        )

        self.assertEqual(continued.status, "queued")
        self.assertEqual(continued.parameter_values_json["workflow_stage"], "qc_preview")
        self.assertTrue(continued.parameter_values_json["nextflow_resume"])
        self.assertEqual(continued.status_payload_json["workflow_stage"], "qc_preview")
        self.assertEqual(continued.status_payload_json["continued_from_workflow_stage"], "is_qc_review")
        self.assertTrue(continued.status_payload_json["review_override"])
        self.assertIn("IS peaks inspected", continued.status_payload_json["operator_note"])

    def test_retry_uncertain_submission_preserves_attempt_and_review_stage(self) -> None:
        admin = self._user("admin@metabolens.local")
        study = self._create_study()
        assay = self._default_assay(study.id)
        workflow = self._workflow_definition()
        job = WorkflowJob(
            study_id=study.id, assay_id=assay.id, workflow_definition_id=workflow.id,
            requested_by=admin.id, status="failed", run_root="/tmp/uncertain",
            input_manifest_json={"submission_attempt": 2, "engine": {"descriptor": {
                "review_transitions": {"qc_preview": "full"}}}},
            parameter_values_json={"workflow_stage": "qc_preview", "nextflow_resume": True},
            status_payload_json={"workflow_stage": "qc_preview"},
            error_message="SLURM SSH submission timed out and may have reached the scheduler",
        )
        self.db.add(job)
        self.db.commit()
        continued = WorkflowLaunchService(self.db).continue_review_stage(admin.id, assay.id, job.id)
        self.assertEqual(continued.input_manifest_json["submission_attempt"], 2)
        self.assertEqual(continued.parameter_values_json["workflow_stage"], "qc_preview")
        # A confirmed terminal failure can create a fresh driver, still at the failed stage.
        continued.status = "failed"
        continued.status_payload_json = {"workflow_stage": "qc_preview", "exit_code": 1}
        self.db.commit()
        retried = WorkflowLaunchService(self.db).continue_review_stage(admin.id, assay.id, job.id)
        self.assertEqual(retried.input_manifest_json["submission_attempt"], 3)
        self.assertEqual(retried.parameter_values_json["workflow_stage"], "qc_preview")

    def test_continue_after_failed_resume_requeues_same_job(self) -> None:
        admin = self._user("admin@metabolens.local")
        workflow = self._workflow_definition()
        study = self._create_study("admin@metabolens.local")
        assay = self._default_assay(study.id)
        job = WorkflowJob(
            study_id=study.id,
            assay_id=assay.id,
            workflow_definition_id=workflow.id,
            requested_by=admin.id,
            status="failed",
            input_manifest_json={"file_ids": [], **MSDIAL_ENGINE_MANIFEST},
            parameter_values_json={"workflow_stage": "full", "nextflow_resume": True},
            execution_backend="nextflow",
            execution_profile="local_docker",
            run_root="/tmp/metabolens/job_2",
            status_payload_json={"workflow_stage": "full", "nextflow_resume": True},
            external_run_id="nf-local-2",
            error_message="R statistics failed",
            finished_at=datetime.now(timezone.utc),
        )
        self.db.add(job)
        self.db.commit()

        continued = WorkflowLaunchService(self.db).continue_review_stage(admin.id, assay.id, job.id)

        self.assertEqual(continued.id, job.id)
        self.assertEqual(continued.status, "queued")
        self.assertTrue(continued.parameter_values_json["nextflow_resume"])
        self.assertIsNone(continued.error_message)
        self.assertIsNone(continued.finished_at)

    def test_artifact_registration_updates_existing_artifact_for_resume(self) -> None:
        admin = self._user("admin@metabolens.local")
        workflow = self._workflow_definition()
        study = self._create_study("admin@metabolens.local")
        assay = self._default_assay(study.id)
        job = WorkflowJob(
            study_id=study.id,
            assay_id=assay.id,
            workflow_definition_id=workflow.id,
            requested_by=admin.id,
            status="succeeded",
            input_manifest_json={"file_ids": []},
            parameter_values_json={"workflow_stage": "full"},
            execution_backend="nextflow",
            execution_profile="local_docker",
            status_payload_json={},
        )
        self.db.add(job)
        self.db.commit()
        service = WorkflowArtifactRegistrationService(self.db)
        record = {
            "artifact_role": "artifact_manifest",
            "file_kind": "workflow_contract",
            "filename": "artifact-manifest.json",
            "storage_uri": "file:///tmp/job_1/contract/artifact-manifest.json",
            "media_type": "application/json",
            "file_size_bytes": 10,
            "checksum_sha256": "a" * 64,
        }

        first_ids = service.register_output_records(job, [record])
        second_ids = service.register_output_records(job, [{**record, "file_size_bytes": 20, "checksum_sha256": "b" * 64}])
        self.db.commit()

        self.assertEqual(second_ids, first_ids)
        self.assertEqual(self.db.query(WorkflowRunArtifact).filter(WorkflowRunArtifact.workflow_job_id == job.id).count(), 1)
        dataset_file = self.db.query(DatasetFile).filter(DatasetFile.id == first_ids[0]).one()
        self.assertEqual(dataset_file.file_size_bytes, 20)
        self.assertEqual(dataset_file.checksum_sha256, "b" * 64)

    def test_artifact_registration_keeps_derived_datasets_per_workflow_job(self) -> None:
        admin = self._user("admin@metabolens.local")
        workflow = self._workflow_definition()
        study = self._create_study("admin@metabolens.local")
        assay = self._default_assay(study.id)
        jobs = [
            WorkflowJob(
                study_id=study.id,
                assay_id=assay.id,
                workflow_definition_id=workflow.id,
                requested_by=admin.id,
                status="succeeded",
                input_manifest_json={"file_ids": []},
                parameter_values_json={},
                execution_backend="nextflow",
                execution_profile="local_docker",
                status_payload_json={},
            )
            for _ in range(2)
        ]
        self.db.add_all(jobs)
        self.db.commit()
        service = WorkflowArtifactRegistrationService(self.db)

        file_ids = [
            service.register_output_records(
                job,
                [
                    {
                        "artifact_role": "msdial_alignment",
                        "file_kind": "msdial_alignment",
                        "filename": f"job-{job.id}.mdalign",
                        "storage_uri": f"file:///tmp/job-{job.id}.mdalign",
                        "media_type": "application/octet-stream",
                        "file_size_bytes": 10,
                        "checksum_sha256": str(job.id) * 64,
                    }
                ],
            )[0]
            for job in jobs
        ]
        self.db.commit()

        dataset_ids = {self.db.query(DatasetFile).filter(DatasetFile.id == file_id).one().dataset_id for file_id in file_ids}
        dataset_names = {dataset.name for dataset in self.db.query(Dataset).filter(Dataset.id.in_(dataset_ids)).all()}

        self.assertEqual(len(dataset_ids), 2)
        self.assertEqual(dataset_names, {f"job-{job.id}-derived" for job in jobs})

    def test_workflow_libraries_are_scanned_from_workflow_library_folder(self) -> None:
        admin = self._user("admin@metabolens.local")
        workflow = self._workflow_definition()
        with TemporaryDirectory() as tmpdir:
            pipeline_root = Path(tmpdir)
            library_root = pipeline_root / "libraries"
            default_template_root = pipeline_root / "functional_test" / "sample_data"
            template_root = pipeline_root / "templates" / "msdial" / "embl"
            nested_root = library_root / "nist"
            nested_root.mkdir(parents=True)
            default_template_root.mkdir(parents=True)
            template_root.mkdir(parents=True)
            (library_root / "local_ms1.txt").write_text("mz\trt\tname\n", encoding="utf-8")
            (nested_root / "local_ms2.msp").write_text("Name: Test\n", encoding="utf-8")
            (library_root / "ignore.csv").write_text("ignored\n", encoding="utf-8")
            (default_template_root / "msdial_params.txt").write_text("MSP file path: old.msp\n", encoding="utf-8")
            (default_template_root / "facility_params.txt").write_text(
                "MS-DIAL ver. 4.9.221218\n"
                "MSP file\told_ms2.msp\n"
                "Text file\told_ms1.txt\n"
                "Reference file\told_reference.abf\n",
                encoding="utf-8",
            )
            (default_template_root / "ms1_lib.txt").write_text("mz\trt\tname\n", encoding="utf-8")
            (template_root / "hilic_pos.txt").write_text("MSP file path: old.msp\n", encoding="utf-8")
            (template_root / "ignore.csv").write_text("ignored\n", encoding="utf-8")
            workflow.pipeline_root = str(pipeline_root)
            workflow.static_input_config_json = {
                "msdial_config_path": "functional_test/sample_data/msdial_params.txt",
                "ms1_library_path": "libraries/local_ms1.txt",
                "ms2_library_path": "libraries/nist/local_ms2.msp",
            }

            libraries = WorkflowDefinitionService(self.db).list_libraries(admin.id, workflow.id)

        self.assertEqual(libraries["default_ms1_library_path"], "libraries/local_ms1.txt")
        self.assertEqual(libraries["default_ms2_library_path"], "libraries/nist/local_ms2.msp")
        self.assertEqual(libraries["default_msdial_config_path"], "functional_test/sample_data/msdial_params.txt")
        self.assertEqual(libraries["ms1_libraries"], [{"label": "local_ms1.txt", "value": "libraries/local_ms1.txt"}])
        self.assertEqual(libraries["ms2_libraries"], [{"label": "nist/local_ms2.msp", "value": "libraries/nist/local_ms2.msp"}])
        self.assertEqual(
            libraries["msdial_templates"],
            [
                {"label": "Default - functional_test/sample_data/msdial_params.txt", "value": "functional_test/sample_data/msdial_params.txt"},
                {"label": "embl/hilic_pos.txt", "value": "templates/msdial/embl/hilic_pos.txt"},
                {"label": "facility_params.txt", "value": "functional_test/sample_data/facility_params.txt"},
            ],
        )

    def test_workflow_template_content_reads_allow_listed_text_with_checksum(self) -> None:
        admin = self._user("admin@metabolens.local")
        workflow = self._workflow_definition()
        template_text = (
            "MS-DIAL ver. 5.5\n"
            "MSP file path: old_ms2.msp\n"
            "Text DB file path: old_ms1.txt\n"
            "Alignment reference file ID: old_reference\n"
        )
        with TemporaryDirectory() as tmpdir:
            pipeline_root = Path(tmpdir)
            template_root = pipeline_root / "templates" / "msdial"
            template_root.mkdir(parents=True)
            (template_root / "selected_params.txt").write_text(template_text, encoding="utf-8")
            workflow.pipeline_root = str(pipeline_root)

            result = WorkflowDefinitionService(self.db).get_msdial_template_content(
                admin.id,
                workflow.id,
                "templates/msdial/selected_params.txt",
            )

        self.assertEqual(result["path"], "templates/msdial/selected_params.txt")
        self.assertEqual(result["content"], template_text)
        self.assertEqual(
            result["checksum_sha256"],
            hashlib.sha256(template_text.encode("utf-8")).hexdigest(),
        )

    def test_workflow_template_content_rejects_invalid_msdial_text(self) -> None:
        admin = self._user("admin@metabolens.local")
        workflow = self._workflow_definition()
        with TemporaryDirectory() as tmpdir:
            pipeline_root = Path(tmpdir)
            template_root = pipeline_root / "templates" / "msdial"
            template_root.mkdir(parents=True)
            (template_root / "invalid.txt").write_text("MS-DIAL ver. 5.5\n", encoding="utf-8")
            workflow.pipeline_root = str(pipeline_root)

            with self.assertRaises(HTTPException) as ctx:
                WorkflowDefinitionService(self.db).get_msdial_template_content(
                    admin.id,
                    workflow.id,
                    "templates/msdial/invalid.txt",
                )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("required MS-DIAL fields", str(ctx.exception.detail))

    def test_workflow_template_content_rejects_oversized_or_non_utf8_text(self) -> None:
        admin = self._user("admin@metabolens.local")
        workflow = self._workflow_definition()
        valid_prefix = (
            "MS-DIAL ver. 5.5\n"
            "MSP file path: old_ms2.msp\n"
            "Text DB file path: old_ms1.txt\n"
            "Alignment reference file ID: old_reference\n"
        )
        with TemporaryDirectory() as tmpdir:
            pipeline_root = Path(tmpdir)
            template_root = pipeline_root / "templates" / "msdial"
            template_root.mkdir(parents=True)
            (template_root / "oversized.txt").write_text(valid_prefix + ("x" * 65536), encoding="utf-8")
            (template_root / "invalid_utf8.txt").write_bytes(b"MS-DIAL\xff")
            workflow.pipeline_root = str(pipeline_root)
            service = WorkflowDefinitionService(self.db)

            with self.assertRaises(HTTPException) as oversized_ctx:
                service.get_msdial_template_content(admin.id, workflow.id, "templates/msdial/oversized.txt")
            with self.assertRaises(HTTPException) as utf8_ctx:
                service.get_msdial_template_content(admin.id, workflow.id, "templates/msdial/invalid_utf8.txt")

        self.assertEqual(oversized_ctx.exception.status_code, 400)
        self.assertEqual(str(oversized_ctx.exception.detail), "MS-DIAL parameter template must be at most 65536 bytes")
        self.assertEqual(utf8_ctx.exception.status_code, 400)
        self.assertEqual(str(utf8_ctx.exception.detail), "MS-DIAL parameter template must be UTF-8 text")

    def test_edited_template_validation_rejects_empty_oversized_or_missing_labels(self) -> None:
        valid_text = (
            "MS-DIAL ver. 5.5\n"
            "MSP file path: old_ms2.msp\n"
            "Text DB file path: old_ms1.txt\n"
            "Alignment reference file ID: old_reference\n"
        )
        cases = [
            ("", "MS-DIAL parameter template cannot be empty"),
            (valid_text + ("x" * 65536), "MS-DIAL parameter template must be at most 65536 bytes"),
            (valid_text.replace("MSP file path: old_ms2.msp\n", ""), "MS-DIAL parameter template is missing required MS-DIAL fields"),
            (valid_text.replace("Text DB file path: old_ms1.txt\n", ""), "MS-DIAL parameter template is missing required MS-DIAL fields"),
            (valid_text.replace("Alignment reference file ID: old_reference\n", ""), "MS-DIAL parameter template is missing required MS-DIAL fields"),
        ]

        for content, message in cases:
            with self.subTest(message=message), self.assertRaises(HTTPException) as ctx:
                WorkflowDefinitionService(self.db).msdial_template_snapshot("templates/msdial/selected.txt", content)
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertEqual(str(ctx.exception.detail), message)

    def test_workflow_launch_persists_selected_library_paths(self) -> None:
        admin = self._user("admin@metabolens.local")
        workflow = self._workflow_definition()
        study = self._create_study("admin@metabolens.local")
        StudyService(self.db).transition(admin.id, study.id, "approved")
        assay = self._default_assay(study.id)
        self._add_metadata(study.id, assay.id, submitted_by=admin.id, status="valid")
        self._add_file(study.id, assay.id, created_by=admin.id, status="uploaded", name="sample.raw", with_sample=True)

        with TemporaryDirectory() as tmpdir:
            pipeline_root = Path(tmpdir)
            library_root = pipeline_root / "libraries"
            template_root = pipeline_root / "templates" / "msdial"
            library_root.mkdir(parents=True)
            template_root.mkdir(parents=True)
            (library_root / "selected_ms1.txt").write_text("mz\trt\tname\n", encoding="utf-8")
            (library_root / "selected_ms2.msp").write_text("Name: Test\n", encoding="utf-8")
            (template_root / "selected_params.txt").write_text("MSP file path: old.msp\n", encoding="utf-8")
            self._use_engine_pipeline_copy(workflow, pipeline_root)

            job = WorkflowLaunchService(self.db).launch(
                user_id=admin.id,
                assay_id=assay.id,
                workflow_definition_id=workflow.id,
                parameters={
                    "msdial_config_path": "templates/msdial/selected_params.txt",
                    "ms1_library_path": "libraries/selected_ms1.txt",
                    "ms2_library_path": "libraries/selected_ms2.msp",
                },
            )

        self.assertEqual(job.parameter_values_json["msdial_config_path"], "templates/msdial/selected_params.txt")
        self.assertEqual(job.parameter_values_json["ms1_library_path"], "libraries/selected_ms1.txt")
        self.assertEqual(job.parameter_values_json["ms2_library_path"], "libraries/selected_ms2.msp")

    def test_workflow_launch_snapshots_edited_template_text(self) -> None:
        admin = self._user("admin@metabolens.local")
        workflow = self._workflow_definition()
        study = self._create_study("admin@metabolens.local")
        StudyService(self.db).transition(admin.id, study.id, "approved")
        assay = self._default_assay(study.id)
        self._add_metadata(study.id, assay.id, submitted_by=admin.id, status="valid")
        self._add_file(study.id, assay.id, created_by=admin.id, status="uploaded", name="sample.raw", with_sample=True)
        edited_text = (
            "MS-DIAL ver. 5.5\n"
            "MSP file path: old_ms2.msp\n"
            "Text DB file path: old_ms1.txt\n"
            "Alignment reference file ID: old_reference\n"
            "Minimum peak height: 2500\n"
        )

        with TemporaryDirectory() as tmpdir:
            pipeline_root = Path(tmpdir)
            library_root = pipeline_root / "libraries"
            template_root = pipeline_root / "templates" / "msdial"
            library_root.mkdir(parents=True)
            template_root.mkdir(parents=True)
            (library_root / "selected_ms1.txt").write_text("mz\trt\tname\n", encoding="utf-8")
            (library_root / "selected_ms2.msp").write_text("Name: Test\n", encoding="utf-8")
            (template_root / "selected_params.txt").write_text(edited_text.replace("2500", "1000"), encoding="utf-8")
            self._use_engine_pipeline_copy(workflow, pipeline_root)

            job = WorkflowLaunchService(self.db).launch(
                user_id=admin.id,
                assay_id=assay.id,
                workflow_definition_id=workflow.id,
                parameters={
                    "msdial_config_path": "templates/msdial/selected_params.txt",
                    "ms1_library_path": "libraries/selected_ms1.txt",
                    "ms2_library_path": "libraries/selected_ms2.msp",
                },
                msdial_template_text=edited_text,
            )

        snapshot = job.input_manifest_json["msdial_template"]
        self.assertEqual(snapshot["source_path"], "templates/msdial/selected_params.txt")
        self.assertEqual(snapshot["content"], edited_text)
        self.assertEqual(snapshot["checksum_sha256"], hashlib.sha256(edited_text.encode("utf-8")).hexdigest())
        self.assertNotIn("msdial_template_text", job.parameter_values_json)

    def test_workflow_library_validation_rejects_unsafe_or_wrong_paths(self) -> None:
        workflow = self._workflow_definition()
        with TemporaryDirectory() as tmpdir:
            pipeline_root = Path(tmpdir)
            library_root = pipeline_root / "libraries"
            library_root.mkdir(parents=True)
            (library_root / "valid_ms1.txt").write_text("mz\trt\tname\n", encoding="utf-8")
            (library_root / "wrong_type.msp").write_text("Name: Test\n", encoding="utf-8")
            workflow.pipeline_root = str(pipeline_root)
            service = WorkflowDefinitionService(self.db)

            with self.assertRaises(HTTPException) as traversal_ctx:
                service.validate_library_path(workflow, "../outside.txt", ".txt", "MS1 library")
            self.assertEqual(traversal_ctx.exception.status_code, 400)

            with self.assertRaises(HTTPException) as extension_ctx:
                service.validate_library_path(workflow, "libraries/wrong_type.msp", ".txt", "MS1 library")
            self.assertEqual(extension_ctx.exception.status_code, 400)

            with self.assertRaises(HTTPException) as missing_ctx:
                service.validate_library_path(workflow, "libraries/missing.txt", ".txt", "MS1 library")
            self.assertEqual(missing_ctx.exception.status_code, 400)

    def test_workflow_template_validation_rejects_unsafe_or_wrong_paths(self) -> None:
        workflow = self._workflow_definition()
        with TemporaryDirectory() as tmpdir:
            pipeline_root = Path(tmpdir)
            template_root = pipeline_root / "templates" / "msdial"
            default_root = pipeline_root / "functional_test" / "sample_data"
            template_root.mkdir(parents=True)
            default_root.mkdir(parents=True)
            (template_root / "valid_params.txt").write_text("MSP file path: old.msp\n", encoding="utf-8")
            (template_root / "wrong_type.csv").write_text("ignored\n", encoding="utf-8")
            (default_root / "msdial_params.txt").write_text("MSP file path: old.msp\n", encoding="utf-8")
            (pipeline_root / "outside_template.txt").write_text("MSP file path: old.msp\n", encoding="utf-8")
            workflow.pipeline_root = str(pipeline_root)
            workflow.static_input_config_json = {
                "msdial_config_path": "functional_test/sample_data/msdial_params.txt",
            }
            service = WorkflowDefinitionService(self.db)

            self.assertEqual(
                service.validate_msdial_config_path(workflow, "templates/msdial/valid_params.txt"),
                "templates/msdial/valid_params.txt",
            )
            self.assertEqual(
                service.validate_msdial_config_path(workflow, "functional_test/sample_data/msdial_params.txt"),
                "functional_test/sample_data/msdial_params.txt",
            )

            for invalid_path in ("../outside.txt", "templates/msdial/wrong_type.csv", "templates/msdial/missing.txt", "outside_template.txt"):
                with self.assertRaises(HTTPException) as ctx:
                    service.validate_msdial_config_path(workflow, invalid_path)
                self.assertEqual(ctx.exception.status_code, 400)

    def test_activity_contains_actor_email(self) -> None:
        admin = self._user("admin@metabolens.local")
        study = self._create_study("admin@metabolens.local")
        service = StudyService(self.db)
        service.transition(admin.id, study.id, "approved")

        events = service.list_activity(admin.id, study.id, limit=20)
        self.assertTrue(events)
        self.assertTrue(any(event["event_type"] == "study.create" for event in events))
        self.assertTrue(any(event["actor_email"] == "admin@metabolens.local" for event in events))

    def test_metadata_profiles_are_listed(self) -> None:
        admin = self._user("admin@metabolens.local")
        profiles = MetadataService(self.db).list_profiles(admin.id)
        self.assertEqual(len(profiles), 5)
        self.assertTrue(any(profile["id"] == "exploris240_vanquish_hilic_pos" for profile in profiles))
        self.assertTrue(any(profile["id"] == "qexactive_trace1310_gcms" for profile in profiles))
        self.assertTrue(any(profile["id"] == "bruker_avance_neo_600_1d_noesy" for profile in profiles))
        self.assertTrue(any(profile["metabolomics_assay_type"] == "GC_MS" for profile in profiles))

    def test_seed_service_restores_global_roles_for_existing_seed_users(self) -> None:
        self.db.query(RoleAssignment).delete()
        self.db.commit()

        SeedService(self.db).run()
        self.db.commit()

        assignments = {(row.user_id, row.role, row.study_id) for row in self.db.query(RoleAssignment).all()}
        admin = self._user("admin@metabolens.local")
        operator = self._user("operator@metabolens.local")

        self.assertIn((admin.id, "admin", None), assignments)
        self.assertIn((operator.id, "operator", None), assignments)

    def test_metadata_profile_validation_is_applied_and_updates_assay(self) -> None:
        admin = self._user("admin@metabolens.local")
        study = self._create_study("admin@metabolens.local")
        assay = self._default_assay(study.id)

        record = MetadataService(self.db).submit(
            admin.id,
            assay.id,
            {
                "profile_id": "exploris240_vanquish_hilic_pos",
                "profile_version": "v1",
                "instrument": {
                    "ms_vendor": "Thermo Fisher Scientific",
                    "ms_model": "Orbitrap Exploris 240",
                    "ms_analyzer_type": "Orbitrap",
                    "ms_ion_source": "H-ESI",
                    "ms_polarity_mode": "",
                },
            },
        )

        self.assertEqual(record.validation_status, "invalid")
        self.assertTrue(any("instrument.ms_polarity_mode" in message for message in record.validation_messages_json))

        assay = self._default_assay(study.id)
        self.assertIsNotNone(assay.metabolomics_assay)
        assert assay.metabolomics_assay is not None
        self.assertEqual(assay.metabolomics_assay.instrument_model, "Orbitrap Exploris 240")
        self.assertEqual(assay.platform, "metabolomics")
        self.assertEqual(assay.metabolomics_assay.assay_type, "LC_MS")
        self.assertIsNotNone(assay.metabolomics_assay.lcms_details)
        assert assay.metabolomics_assay.lcms_details is not None
        self.assertEqual(assay.metabolomics_assay.lcms_details.ion_source, "H-ESI")

    def test_create_assay_creates_linked_metabolomics_assay(self) -> None:
        admin = self._user("admin@metabolens.local")
        study = self._create_study("admin@metabolens.local")

        assay = StudyService(self.db).create_assay(
            admin.id,
            study.id,
            AssayCreate(name="Positive Polar HILIC", assay_type="metabolomics_lcms"),
        )

        self.assertFalse(assay.is_default)
        self.assertEqual(assay.study_id, study.id)
        self.assertEqual(assay.name, "Positive Polar HILIC")
        self.assertIsNotNone(assay.metabolomics_assay)
        assert assay.metabolomics_assay is not None
        self.assertEqual(assay.platform, "metabolomics")
        self.assertEqual(assay.metabolomics_assay.assay_id, assay.id)
        self.assertEqual(assay.metabolomics_assay.assay_type, "LC_MS")
        self.assertIsNotNone(assay.metabolomics_assay.lcms_details)

    def test_create_assay_can_initialize_gcms_subtype(self) -> None:
        admin = self._user("admin@metabolens.local")
        study = self._create_study("admin@metabolens.local")

        assay = StudyService(self.db).create_assay(
            admin.id,
            study.id,
            AssayCreate(name="GC-MS assay", metabolomics_assay_type="GC_MS"),
        )

        self.assertEqual(assay.platform, "metabolomics")
        self.assertIsNotNone(assay.metabolomics_assay)
        assert assay.metabolomics_assay is not None
        self.assertEqual(assay.metabolomics_assay.assay_type, "GC_MS")
        self.assertIsNotNone(assay.metabolomics_assay.gcms_details)
        self.assertIsNone(assay.metabolomics_assay.lcms_details)

    def test_metadata_submission_updates_gcms_assay_details(self) -> None:
        admin = self._user("admin@metabolens.local")
        study = self._create_study("admin@metabolens.local")
        assay = StudyService(self.db).create_assay(
            admin.id,
            study.id,
            AssayCreate(name="GC-MS assay", metabolomics_assay_type="GC_MS"),
        )

        record = MetadataService(self.db).submit(
            admin.id,
            assay.id,
            {
                "profile_id": "qexactive_trace1310_gcms",
                "profile_version": "v1",
                "chromatography": {
                    "derivatization": "Methoxyamination + MSTFA",
                },
            },
        )

        self.assertEqual(record.validation_status, "valid")
        assay = self.db.query(Assay).filter(Assay.id == assay.id).first()
        assert assay is not None
        assert assay.metabolomics_assay is not None
        assert assay.metabolomics_assay.gcms_details is not None
        self.assertEqual(assay.metabolomics_assay.assay_type, "GC_MS")
        self.assertEqual(assay.metabolomics_assay.gcms_details.derivatization, "Methoxyamination + MSTFA")
        self.assertEqual(assay.metabolomics_assay.gcms_details.chromatography_instrument, "TRACE 1310 GC")

    def test_metadata_submission_updates_nmr_assay_details(self) -> None:
        admin = self._user("admin@metabolens.local")
        study = self._create_study("admin@metabolens.local")
        assay = StudyService(self.db).create_assay(
            admin.id,
            study.id,
            AssayCreate(name="NMR assay", metabolomics_assay_type="NMR"),
        )

        record = MetadataService(self.db).submit(
            admin.id,
            assay.id,
            {
                "profile_id": "bruker_avance_neo_600_1d_noesy",
                "profile_version": "v1",
                "nmr": {
                    "pulse_sequence_name": "cpmgpr1d",
                    "number_of_transients": "128",
                },
            },
        )

        self.assertEqual(record.validation_status, "valid")
        assay = self.db.query(Assay).filter(Assay.id == assay.id).first()
        assert assay is not None
        assert assay.metabolomics_assay is not None
        assert assay.metabolomics_assay.nmr_details is not None
        self.assertEqual(assay.metabolomics_assay.assay_type, "NMR")
        self.assertEqual(assay.metabolomics_assay.nmr_details.pulse_sequence_name, "cpmgpr1d")
        self.assertEqual(assay.metabolomics_assay.nmr_details.number_of_transients, "128")

    def test_create_samples_with_paths_registers_local_file_sources(self) -> None:
        admin = self._user("admin@metabolens.local")
        study = self._create_study("admin@metabolens.local")
        service = FileService(self.db)

        with TemporaryDirectory() as tmpdir:
            raw_path = Path(tmpdir) / "sample_a.raw"
            raw_path.write_bytes(b"RAW")

            samples = service.create_samples_with_paths(
                user_id=admin.id,
                study_id=study.id,
                rows=[
                    SampleBulkPathCreateItem(
                        sample=SampleBulkCreateItem(
                            name="S1",
                            description="Serum sample",
                            replicate_number=1,
                            dilution_factor=1.0,
                            sample_type="Blank",
                            organism="Homo sapiens",
                        ),
                        raw_file=SamplePathReference(source_path=str(raw_path)),
                    )
                ],
            )

        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0].sample_type, "blank")
        dataset_file = self.db.query(DatasetFile).filter(DatasetFile.sample_id == samples[0].id).first()
        self.assertIsNotNone(dataset_file)
        assert dataset_file is not None
        self.assertEqual(dataset_file.source_scheme, "file")
        self.assertEqual(dataset_file.ingest_mode, "registered_path")
        self.assertEqual(dataset_file.status, "registered")
        self.assertTrue(dataset_file.storage_uri.startswith("file://"))
        self.assertEqual(dataset_file.original_filename, "sample_a.raw")
        self.assertEqual(dataset_file.checksum_sha256, hashlib.sha256(b"RAW").hexdigest())
        self.assertEqual(dataset_file.file_size_bytes, 3)
        dataset_sample_link = self.db.query(DatasetSampleLink).filter(DatasetSampleLink.dataset_id == dataset_file.dataset_id, DatasetSampleLink.sample_id == samples[0].id).first()
        self.assertIsNotNone(dataset_sample_link)

    def test_create_samples_with_paths_does_not_select_per_row(self) -> None:
        admin = self._user("admin@metabolens.local")
        single_study = self._create_study("admin@metabolens.local")
        batch_study = self._create_study("admin@metabolens.local")
        service = FileService(self.db)

        def count_selects(action) -> int:
            count = 0

            def count_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
                nonlocal count
                if statement.lstrip().upper().startswith("SELECT"):
                    count += 1

            event.listen(self.engine, "before_cursor_execute", count_statement)
            try:
                action()
            finally:
                event.remove(self.engine, "before_cursor_execute", count_statement)
            return count

        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            single_path = root / "single.raw"
            single_path.write_bytes(b"RAW")
            batch_paths = [root / f"batch_{index:02d}.raw" for index in range(12)]
            for path in batch_paths:
                path.write_bytes(b"RAW")

            single_selects = count_selects(
                lambda: service.create_samples_with_paths(
                    user_id=admin.id,
                    study_id=single_study.id,
                    rows=[
                        SampleBulkPathCreateItem(
                            sample=SampleBulkCreateItem(name="single", description="single"),
                            raw_file=SamplePathReference(source_path=str(single_path)),
                        )
                    ],
                )
            )
            batch_selects = count_selects(
                lambda: service.create_samples_with_paths(
                    user_id=admin.id,
                    study_id=batch_study.id,
                    rows=[
                        SampleBulkPathCreateItem(
                            sample=SampleBulkCreateItem(name=path.stem, description=path.stem),
                            raw_file=SamplePathReference(source_path=str(path)),
                        )
                        for path in batch_paths
                    ],
                )
            )

        self.assertLessEqual(batch_selects, single_selects + 3)

    def test_assay_path_registration_can_reuse_existing_study_sample(self) -> None:
        admin = self._user("admin@metabolens.local")
        study = self._create_study("admin@metabolens.local")
        default_assay = self._default_assay(study.id)
        extra_assay = StudyService(self.db).create_assay(admin.id, study.id, AssayCreate(name="Negative mode", assay_type="metabolomics_lcms"))
        service = FileService(self.db)

        with TemporaryDirectory() as tmpdir:
            assay_a_dir = Path(tmpdir) / "assay_a"
            assay_b_dir = Path(tmpdir) / "assay_b"
            assay_a_dir.mkdir()
            assay_b_dir.mkdir()
            raw_a = assay_a_dir / "S1.raw"
            raw_b = assay_b_dir / "S1.raw"
            raw_a.write_bytes(b"RAW A")
            raw_b.write_bytes(b"RAW B")

            samples = service.create_samples_with_paths_for_assay(
                user_id=admin.id,
                assay_id=default_assay.id,
                rows=[
                    SampleBulkPathCreateItem(
                        sample=SampleBulkCreateItem(name="S1", description="Shared biological sample", sample_type="Sample"),
                        raw_file=SamplePathReference(source_path=str(raw_a)),
                    )
                ],
            )
            reused_samples = service.create_samples_with_paths_for_assay(
                user_id=admin.id,
                assay_id=extra_assay.id,
                rows=[
                    SampleBulkPathCreateItem(
                        existing_sample_id=samples[0].id,
                        raw_file=SamplePathReference(source_path=str(raw_b)),
                    )
                ],
            )

        self.assertEqual(reused_samples[0].id, samples[0].id)
        self.assertEqual(self.db.query(Sample).filter(Sample.study_id == study.id).count(), 1)
        assay_links = (
            self.db.query(AssaySampleLink)
            .filter(AssaySampleLink.sample_id == samples[0].id)
            .order_by(AssaySampleLink.assay_id.asc())
            .all()
        )
        self.assertEqual([link.assay_id for link in assay_links], [default_assay.id, extra_assay.id])
        rows = service.list_for_assay(admin.id, extra_assay.id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["original_filename"], "S1.raw")
        self.assertEqual(rows[0]["sample_id"], samples[0].id)
        self.assertEqual(rows[0]["dataset_sample_ids"], [samples[0].id])

    def test_assay_path_registration_stores_reuse_annotations_on_assay_link(self) -> None:
        admin = self._user("admin@metabolens.local")
        study = self._create_study("admin@metabolens.local")
        default_assay = self._default_assay(study.id)
        extra_assay = StudyService(self.db).create_assay(admin.id, study.id, AssayCreate(name="Stats assay", assay_type="metabolomics_lcms"))
        service = FileService(self.db)

        with TemporaryDirectory() as tmpdir:
            raw_a = Path(tmpdir) / "S1_a.raw"
            raw_b = Path(tmpdir) / "S1_b.raw"
            raw_a.write_bytes(b"RAW A")
            raw_b.write_bytes(b"RAW B")

            samples = service.create_samples_with_paths_for_assay(
                user_id=admin.id,
                assay_id=default_assay.id,
                rows=[
                    SampleBulkPathCreateItem(
                        sample=SampleBulkCreateItem(name="S1", description="Shared biological sample", sample_type="Sample"),
                        raw_file=SamplePathReference(source_path=str(raw_a)),
                    )
                ],
            )
            reused_samples = service.create_samples_with_paths_for_assay(
                user_id=admin.id,
                assay_id=extra_assay.id,
                rows=[
                    SampleBulkPathCreateItem(
                        existing_sample_id=samples[0].id,
                        annotations_json={"group_label": "Lean", "batch_id": "2", "injection_order": 7},
                        raw_file=SamplePathReference(source_path=str(raw_b)),
                    )
                ],
            )

        self.assertEqual(reused_samples[0].id, samples[0].id)
        refreshed = self.db.query(Sample).filter(Sample.id == samples[0].id).first()
        self.assertIsNotNone(refreshed)
        self.assertEqual(refreshed.annotations_json, {})
        assay_link = (
            self.db.query(AssaySampleLink)
            .filter(AssaySampleLink.assay_id == extra_assay.id, AssaySampleLink.sample_id == samples[0].id)
            .one()
        )
        self.assertEqual(assay_link.annotations_json, {"group_label": "Lean"})
        acquisition = self.db.query(Acquisition).filter_by(assay_id=extra_assay.id, sample_id=samples[0].id).one()
        self.assertEqual(acquisition.annotations_json, {"batch_id": "2", "injection_order": 7})

    def test_assay_path_registration_reuses_existing_sample_by_name(self) -> None:
        admin = self._user("admin@metabolens.local")
        study = self._create_study("admin@metabolens.local")
        default_assay = self._default_assay(study.id)
        extra_assay = StudyService(self.db).create_assay(admin.id, study.id, AssayCreate(name="Positive mode", assay_type="metabolomics_lcms"))
        service = FileService(self.db)

        with TemporaryDirectory() as tmpdir:
            raw_a = Path(tmpdir) / "assay_a_S1.raw"
            raw_b = Path(tmpdir) / "assay_b_S1.raw"
            raw_a.write_bytes(b"RAW A")
            raw_b.write_bytes(b"RAW B")

            samples = service.create_samples_with_paths_for_assay(
                user_id=admin.id,
                assay_id=default_assay.id,
                rows=[
                    SampleBulkPathCreateItem(
                        sample=SampleBulkCreateItem(name="S1", description="Shared biological sample", sample_type="Sample"),
                        raw_file=SamplePathReference(source_path=str(raw_a)),
                    )
                ],
            )
            reused_samples = service.create_samples_with_paths_for_assay(
                user_id=admin.id,
                assay_id=extra_assay.id,
                rows=[
                    SampleBulkPathCreateItem(
                        sample=SampleBulkCreateItem(name="s1", description="Duplicate row should reuse existing sample", sample_type="QC"),
                        raw_file=SamplePathReference(source_path=str(raw_b)),
                    )
                ],
            )

        self.assertEqual(reused_samples[0].id, samples[0].id)
        self.assertEqual(reused_samples[0].sample_type, "sample")
        self.assertEqual(self.db.query(Sample).filter(Sample.study_id == study.id).count(), 1)
        assay_links = self.db.query(AssaySampleLink).filter(AssaySampleLink.sample_id == samples[0].id).all()
        self.assertEqual({link.assay_id for link in assay_links}, {default_assay.id, extra_assay.id})

    def test_assay_path_registration_does_not_trim_stored_sample_names_when_matching(self) -> None:
        admin = self._user("admin@metabolens.local")
        study = self._create_study("admin@metabolens.local")
        assay = self._default_assay(study.id)
        spaced_sample = Sample(
            study_id=study.id,
            name=" S1 ",
            description="Legacy name containing spaces",
            replicate_number=1,
            dilution_factor=1.0,
            sample_type="sample",
            organism="Homo sapiens",
            annotations_json={},
        )
        self.db.add(spaced_sample)
        self.db.commit()
        service = FileService(self.db)

        with TemporaryDirectory() as tmpdir:
            raw_path = Path(tmpdir) / "S1.raw"
            raw_path.write_bytes(b"RAW")

            samples = service.create_samples_with_paths_for_assay(
                user_id=admin.id,
                assay_id=assay.id,
                rows=[
                    SampleBulkPathCreateItem(
                        sample=SampleBulkCreateItem(name="S1", description="Unspaced sample"),
                        raw_file=SamplePathReference(source_path=str(raw_path)),
                    )
                ],
            )

        self.assertNotEqual(samples[0].id, spaced_sample.id)
        self.assertEqual(samples[0].name, "S1")
        self.assertEqual(self.db.query(Sample).filter(Sample.study_id == study.id).count(), 2)

    def test_assay_path_registration_rejects_same_source_path_across_assays(self) -> None:
        admin = self._user("admin@metabolens.local")
        study = self._create_study("admin@metabolens.local")
        default_assay = self._default_assay(study.id)
        extra_assay = StudyService(self.db).create_assay(admin.id, study.id, AssayCreate(name="Negative mode", assay_type="metabolomics_lcms"))
        service = FileService(self.db)

        with TemporaryDirectory() as tmpdir:
            raw_path = Path(tmpdir) / "S1.raw"
            raw_path.write_bytes(b"RAW")

            samples = service.create_samples_with_paths_for_assay(
                user_id=admin.id,
                assay_id=default_assay.id,
                rows=[
                    SampleBulkPathCreateItem(
                        sample=SampleBulkCreateItem(name="S1", description="Shared biological sample", sample_type="Sample"),
                        raw_file=SamplePathReference(source_path=str(raw_path)),
                    )
                ],
            )

            with self.assertRaises(HTTPException) as ctx:
                service.create_samples_with_paths_for_assay(
                    user_id=admin.id,
                    assay_id=extra_assay.id,
                    rows=[
                        SampleBulkPathCreateItem(
                            existing_sample_id=samples[0].id,
                            raw_file=SamplePathReference(source_path=str(raw_path)),
                        )
                    ],
                )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("already been registered for this study", str(ctx.exception.detail))
        rows = service.list_for_study(admin.id, study.id)
        self.assertEqual(len(rows), 1)

    def test_list_files_exposes_dataset_and_parent_sample_context(self) -> None:
        admin = self._user("admin@metabolens.local")
        study = self._create_study("admin@metabolens.local")
        service = FileService(self.db)

        with TemporaryDirectory() as tmpdir:
            raw_path = Path(tmpdir) / "sample_a.raw"
            raw_path.write_bytes(b"RAW")

            samples = service.create_samples_with_paths(
                user_id=admin.id,
                study_id=study.id,
                rows=[
                    SampleBulkPathCreateItem(
                        sample=SampleBulkCreateItem(
                            name="S1",
                            description="Serum sample",
                            replicate_number=1,
                            dilution_factor=1.0,
                            organism="Homo sapiens",
                        ),
                        raw_file=SamplePathReference(source_path=str(raw_path)),
                    )
                ],
            )

        rows = service.list_for_study(admin.id, study.id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["dataset_kind"], "raw")
        self.assertEqual(rows[0]["dataset_type"], "thermo_raw_acquisition")
        self.assertEqual(rows[0]["file_sample_name"], samples[0].name)
        self.assertEqual(rows[0]["dataset_sample_ids"], [samples[0].id])
        self.assertEqual(rows[0]["dataset_sample_names"], [samples[0].name])

    def test_list_files_for_assay_only_returns_that_assays_files(self) -> None:
        admin = self._user("admin@metabolens.local")
        study = self._create_study("admin@metabolens.local")
        default_assay = self._default_assay(study.id)
        extra_assay = StudyService(self.db).create_assay(
            admin.id,
            study.id,
            AssayCreate(name="Extra Assay", assay_type="metabolomics_lcms"),
        )

        default_file = self._add_file(study.id, default_assay.id, created_by=admin.id, status="uploaded", name="default.raw")
        extra_file = self._add_file(study.id, extra_assay.id, created_by=admin.id, status="uploaded", name="extra.raw")

        rows = FileService(self.db).list_for_assay(admin.id, extra_assay.id)

        self.assertEqual([row["id"] for row in rows], [extra_file.id])
        self.assertNotIn(default_file.id, [row["id"] for row in rows])

    def test_assay_files_endpoint_filters_workflow_inputs_before_serializing(self) -> None:
        admin = self._user("admin@metabolens.local")
        study = self._create_study("admin@metabolens.local")
        assay = self._default_assay(study.id)
        eligible_file = self._add_file(
            study.id,
            assay.id,
            created_by=admin.id,
            status="uploaded",
            name="eligible.raw",
            with_sample=True,
        )
        failed_file = self._add_file(
            study.id,
            assay.id,
            created_by=admin.id,
            status="failed",
            name="failed.raw",
            with_sample=True,
        )
        unlinked_file = self._add_file(
            study.id,
            assay.id,
            created_by=admin.id,
            status="uploaded",
            name="unlinked.raw",
        )
        incomplete_metadata_file = self._add_file(
            study.id,
            assay.id,
            created_by=admin.id,
            status="uploaded",
            name="needs-metadata.raw",
            with_sample=True,
            sample_metadata_complete=False,
        )
        derived_dataset = Dataset(
            study_id=study.id,
            assay_id=assay.id,
            name="workflow-derived",
            dataset_kind="derived",
            dataset_type="workflow_outputs",
            status="validated",
            created_by=admin.id,
        )
        self.db.add(derived_dataset)
        self.db.flush()
        derived_file = DatasetFile(
            dataset_id=derived_dataset.id,
            sample_id=None,
            file_role="workflow_report",
            original_filename="report.json",
            storage_uri="file:///tmp/report.json",
            status="validated",
            created_by=admin.id,
        )
        self.db.add(derived_file)
        self.db.commit()

        try:
            rows = assay_routes.list_assay_files(
                assay.id,
                workflow_inputs_only=True,
                db=self.db,
                user=admin,
            )
        except TypeError as exc:
            self.fail(f"Assay file listing must accept workflow_inputs_only: {exc}")

        returned_ids = [row["id"] for row in rows]
        self.assertEqual(returned_ids, [incomplete_metadata_file.id, eligible_file.id])
        self.assertNotIn(failed_file.id, returned_ids)
        self.assertNotIn(unlinked_file.id, returned_ids)
        self.assertNotIn(derived_file.id, returned_ids)

    def test_study_structure_exposes_assays_datasets_and_buckets(self) -> None:
        admin = self._user("admin@metabolens.local")
        study = self._create_study("admin@metabolens.local")
        default_assay = self._default_assay(study.id)
        extra_assay = StudyService(self.db).create_assay(
            admin.id,
            study.id,
            AssayCreate(name="Secondary Assay", assay_type="metabolomics_lcms"),
        )

        file_service = FileService(self.db)
        with TemporaryDirectory() as tmpdir:
            raw_path = Path(tmpdir) / "sample_a.raw"
            raw_path.write_bytes(b"RAW")
            created_samples = file_service.create_samples_with_paths(
                user_id=admin.id,
                study_id=study.id,
                rows=[
                    SampleBulkPathCreateItem(
                        sample=SampleBulkCreateItem(
                            name="S1",
                            description="Dataset-linked sample",
                            replicate_number=1,
                            dilution_factor=1.0,
                            organism="Homo sapiens",
                        ),
                        raw_file=SamplePathReference(source_path=str(raw_path)),
                    )
                ],
            )

        assay_only_sample = Sample(
            study_id=study.id,
            name="S2",
            description="Assay linked, no dataset",
            organism="Homo sapiens",
            annotations_json={},
        )
        unlinked_sample = Sample(
            study_id=study.id,
            name="S3",
            description="Unlinked sample",
            organism="Homo sapiens",
            annotations_json={},
        )
        self.db.add_all([assay_only_sample, unlinked_sample])
        self.db.flush()
        self.db.add(AssaySampleLink(assay_id=extra_assay.id, sample_id=assay_only_sample.id, sample_role="input"))
        self.db.commit()

        structure = StudyService(self.db).get_structure(admin.id, study.id)
        nodes = structure["nodes"]
        node_ids = {node["id"] for node in nodes}
        labels = {node["label"] for node in nodes}

        self.assertEqual(structure["study_id"], study.id)
        self.assertIn(f"study:{study.id}", node_ids)
        self.assertIn(f"assay:{default_assay.id}", node_ids)
        self.assertIn(f"assay:{extra_assay.id}", node_ids)
        self.assertIn("No dataset yet", labels)
        self.assertIn("Unlinked samples", labels)
        self.assertIn("S1", labels)
        self.assertIn("S2", labels)
        self.assertIn("S3", labels)

        dataset_nodes = [node for node in nodes if node["node_type"] == "dataset" and node["assay_id"] == default_assay.id]
        self.assertTrue(dataset_nodes)
        self.assertEqual(dataset_nodes[0]["details"]["dataset_kind"], "raw")
        self.assertEqual(dataset_nodes[0]["details"]["file_count"], 1)
        study_node = next(node for node in nodes if node["id"] == f"study:{study.id}")
        default_assay_node = next(node for node in nodes if node["id"] == f"assay:{default_assay.id}")
        extra_assay_node = next(node for node in nodes if node["id"] == f"assay:{extra_assay.id}")
        self.assertGreater(study_node["value"], 0)
        self.assertGreater(default_assay_node["value"], 0)
        self.assertGreater(extra_assay_node["value"], 0)
        self.assertIn(created_samples[0].id, [node["sample_id"] for node in nodes if node["node_type"] == "sample"])

    def test_create_samples_with_paths_rejects_nonexistent_source_path(self) -> None:
        admin = self._user("admin@metabolens.local")
        study = self._create_study("admin@metabolens.local")
        service = FileService(self.db)

        with self.assertRaises(HTTPException) as ctx:
            service.create_samples_with_paths(
                user_id=admin.id,
                study_id=study.id,
                rows=[
                    SampleBulkPathCreateItem(
                        sample=SampleBulkCreateItem(name="S1", description="Serum sample"),
                        raw_file=SamplePathReference(source_path="/tmp/does-not-exist.raw"),
                    )
                ],
            )

        self.assertEqual(ctx.exception.status_code, 400)

    def test_create_samples_with_paths_accepts_mzml_source(self) -> None:
        admin = self._user("admin@metabolens.local")
        study = self._create_study("admin@metabolens.local")
        service = FileService(self.db)

        with TemporaryDirectory() as tmpdir:
            mzml_path = Path(tmpdir) / "sample_a.mzML"
            mzml_path.write_bytes(b"<mzML/>")

            samples = service.create_samples_with_paths(
                user_id=admin.id,
                study_id=study.id,
                rows=[
                    SampleBulkPathCreateItem(
                        sample=SampleBulkCreateItem(name="S1", description="Serum sample"),
                        raw_file=SamplePathReference(source_path=str(mzml_path)),
                    )
                ],
            )

        dataset_file = self.db.query(DatasetFile).filter(DatasetFile.sample_id == samples[0].id).first()
        assert dataset_file is not None
        self.assertEqual(dataset_file.original_filename, "sample_a.mzML")
        self.assertEqual(dataset_file.file_role, "thermo_raw")
        self.assertEqual(dataset_file.status, "registered")

    def test_create_samples_with_paths_rejects_unsupported_source_extension(self) -> None:
        admin = self._user("admin@metabolens.local")
        study = self._create_study("admin@metabolens.local")
        service = FileService(self.db)

        with TemporaryDirectory() as tmpdir:
            other_path = Path(tmpdir) / "sample_a.wiff"
            other_path.write_bytes(b"WIFF")

            with self.assertRaises(HTTPException) as ctx:
                service.create_samples_with_paths(
                    user_id=admin.id,
                    study_id=study.id,
                    rows=[
                        SampleBulkPathCreateItem(
                            sample=SampleBulkCreateItem(name="S1", description="Serum sample"),
                            raw_file=SamplePathReference(source_path=str(other_path)),
                        )
                    ],
                )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn(".raw or .mzML", str(ctx.exception.detail))

    def test_import_raw_folder_imports_mzml_and_prefers_raw_for_one_sample(self) -> None:
        admin = self._user("admin@metabolens.local")
        study = self._create_study("admin@metabolens.local")
        assay = self._default_assay(study.id)
        service = FileService(self.db)

        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            (tmp_path / "only_mzml.mzML").write_bytes(b"<mzML/>")
            (tmp_path / "both.raw").write_bytes(b"RAW")
            (tmp_path / "both.mzML").write_bytes(b"<mzML/>")
            (tmp_path / "ignore.txt").write_text("x", encoding="utf-8")

            summary = service.import_raw_folder_for_assay(admin.id, assay.id, str(tmp_path))

        self.assertEqual(summary["scanned_count"], 2)
        self.assertEqual(summary["imported_count"], 2)
        files = FileService(self.db).list_for_assay(admin.id, assay.id)
        self.assertEqual(
            sorted(row["original_filename"] for row in files),
            ["both.raw", "only_mzml.mzML"],
        )
        self.assertEqual({row["file_role"] for row in files}, {"thermo_raw"})

    def test_scan_raw_folder_lists_sorted_raw_files(self) -> None:
        admin = self._user("admin@metabolens.local")
        study = self._create_study("admin@metabolens.local")
        service = FileService(self.db)

        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            (tmp_path / "B_sample.raw").write_bytes(b"B")
            (tmp_path / "a_sample.raw").write_bytes(b"A")
            (tmp_path / "ignore.txt").write_text("x", encoding="utf-8")

            scanned = service.scan_raw_folder(admin.id, study.id, str(tmp_path))

        self.assertEqual([item["original_filename"] for item in scanned], ["a_sample.raw", "B_sample.raw"])
        self.assertEqual([item["sample_code"] for item in scanned], ["a_sample", "B_sample"])
        self.assertTrue(all(str(item["source_path"]).endswith(".raw") for item in scanned))

    def test_import_raw_folder_creates_samples_files_and_links(self) -> None:
        admin = self._user("admin@metabolens.local")
        study = self._create_study("admin@metabolens.local")
        assay = self._default_assay(study.id)
        service = FileService(self.db)

        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            (tmp_path / "B_sample.raw").write_bytes(b"B")
            (tmp_path / "a_sample.raw").write_bytes(b"A")
            (tmp_path / "ignore.txt").write_text("x", encoding="utf-8")

            summary = service.import_raw_folder_for_assay(admin.id, assay.id, str(tmp_path))

        self.assertEqual(summary["scanned_count"], 2)
        self.assertEqual(summary["imported_count"], 2)
        self.assertEqual(summary["skipped_existing_count"], 0)
        self.assertEqual(len(summary["imported_sample_ids"]), 2)
        self.assertEqual(len(summary["imported_file_ids"]), 2)
        self.assertEqual(self.db.query(Sample).filter(Sample.study_id == study.id).count(), 2)
        files = FileService(self.db).list_for_assay(admin.id, assay.id)
        self.assertEqual([row["original_filename"] for row in reversed(files)], ["a_sample.raw", "B_sample.raw"])
        self.assertTrue(all(row["sample_id"] in summary["imported_sample_ids"] for row in files))
        rows = (
            self.db.query(Sample.name, Acquisition.annotations_json)
            .join(Acquisition, Acquisition.sample_id == Sample.id)
            .filter(Sample.study_id == study.id, Acquisition.assay_id == assay.id)
            .all()
        )
        injection_order_by_name = {name: annotations.get("injection_order") for name, annotations in rows}
        self.assertEqual(injection_order_by_name, {"a_sample": 1, "B_sample": 2})
        self.assertEqual({annotations.get("batch_id") for _, annotations in rows}, {"1"})

    def test_import_raw_folder_skips_existing_full_source_paths(self) -> None:
        admin = self._user("admin@metabolens.local")
        study = self._create_study("admin@metabolens.local")
        assay = self._default_assay(study.id)
        service = FileService(self.db)

        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            existing_raw = tmp_path / "Existing.raw"
            new_raw = tmp_path / "New.raw"
            existing_raw.write_bytes(b"EXISTING")
            new_raw.write_bytes(b"NEW")

            first_summary = service.import_raw_folder_for_assay(admin.id, assay.id, str(tmp_path))
            second_summary = service.import_raw_folder_for_assay(admin.id, assay.id, str(tmp_path))

        self.assertEqual(first_summary["imported_count"], 2)
        self.assertEqual(second_summary["scanned_count"], 2)
        self.assertEqual(second_summary["imported_count"], 0)
        self.assertEqual(second_summary["skipped_existing_count"], 2)
        self.assertEqual(self.db.query(Sample).filter(Sample.study_id == study.id).count(), 2)
        self.assertEqual(len(FileService(self.db).list_for_assay(admin.id, assay.id)), 2)

    def test_import_raw_folder_allows_same_filename_from_different_folder(self) -> None:
        admin = self._user("admin@metabolens.local")
        study = self._create_study("admin@metabolens.local")
        assay = self._default_assay(study.id)
        service = FileService(self.db)

        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            (first / "Sample.raw").write_bytes(b"FIRST")
            (second / "Sample.raw").write_bytes(b"SECOND")

            first_summary = service.import_raw_folder_for_assay(admin.id, assay.id, str(first))
            second_summary = service.import_raw_folder_for_assay(admin.id, assay.id, str(second))

        self.assertEqual(first_summary["imported_count"], 1)
        self.assertEqual(second_summary["imported_count"], 1)
        self.assertEqual(self.db.query(Sample).filter(Sample.study_id == study.id, Sample.name == "Sample").count(), 1)
        files = FileService(self.db).list_for_assay(admin.id, assay.id)
        self.assertEqual(len(files), 2)
        self.assertEqual({row["original_filename"] for row in files}, {"Sample.raw"})

    def test_import_raw_folder_reuses_sample_when_name_exists(self) -> None:
        admin = self._user("admin@metabolens.local")
        study = self._create_study("admin@metabolens.local")
        assay = self._default_assay(study.id)
        StudyService(self.db).add_sample(admin.id, study.id, SampleCreate(name="Sample", description="Existing"))
        service = FileService(self.db)

        with TemporaryDirectory() as tmpdir:
            raw_path = Path(tmpdir) / "Sample.raw"
            raw_path.write_bytes(b"RAW")

            summary = service.import_raw_folder_for_assay(admin.id, assay.id, tmpdir)

        self.assertEqual(summary["imported_count"], 1)
        self.assertEqual(self.db.query(Sample).filter(Sample.study_id == study.id, Sample.name == "Sample").count(), 1)

    def test_delete_sample_removes_links_and_raw_files(self) -> None:
        admin = self._user("admin@metabolens.local")
        study = self._create_study("admin@metabolens.local")
        assay = self._default_assay(study.id)

        with TemporaryDirectory() as tmpdir:
            raw_path = Path(tmpdir) / "Sample.raw"
            raw_path.write_bytes(b"RAW")
            summary = FileService(self.db).import_raw_folder_for_assay(admin.id, assay.id, tmpdir)

        sample_id = summary["imported_sample_ids"][0]
        processed_dataset = Dataset(
            study_id=study.id,
            assay_id=assay.id,
            name="processed",
            dataset_kind="processed",
            dataset_type="msdial_output",
            status="registered",
            created_by=admin.id,
        )
        self.db.add(processed_dataset)
        self.db.flush()
        processed_file = DatasetFile(
            dataset_id=processed_dataset.id,
            sample_id=sample_id,
            file_role="processed_result",
            original_filename="result.tsv",
            storage_uri=f"file:///tmp/{study.id}/result.tsv",
            checksum_sha256="b" * 64,
            file_size_bytes=10,
            media_type="text/tab-separated-values",
            status="registered",
            created_by=admin.id,
        )
        self.db.add(processed_file)
        self.db.add(DatasetSampleLink(dataset_id=processed_dataset.id, sample_id=sample_id, sample_role="output"))
        self.db.commit()

        delete_summary = StudyService(self.db).delete_sample(admin.id, study.id, sample_id)

        self.assertEqual(delete_summary["deleted_sample_id"], sample_id)
        self.assertEqual(delete_summary["deleted_raw_file_count"], 1)
        self.assertEqual(delete_summary["cleared_file_sample_count"], 1)
        self.assertIsNone(self.db.query(Sample).filter(Sample.id == sample_id).first())
        self.assertEqual(self.db.query(AssaySampleLink).filter(AssaySampleLink.sample_id == sample_id).count(), 0)
        self.assertEqual(self.db.query(DatasetSampleLink).filter(DatasetSampleLink.sample_id == sample_id).count(), 0)
        self.assertEqual(self.db.query(DatasetFile).filter(DatasetFile.id.in_(summary["imported_file_ids"])).count(), 0)
        self.db.refresh(processed_file)
        self.assertIsNone(processed_file.sample_id)

    def test_browse_raw_folders_lists_subdirectories(self) -> None:
        admin = self._user("admin@metabolens.local")
        study = self._create_study("admin@metabolens.local")
        service = FileService(self.db)

        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            first = tmp_path / "first_batch"
            second = tmp_path / "second_batch"
            first.mkdir()
            second.mkdir()
            (first / "one.raw").write_bytes(b"1")
            (second / "two.raw").write_bytes(b"2")

            original_roots = service.settings.raw_path_browse_roots
            service.settings.raw_path_browse_roots = str(tmp_path)
            try:
                roots = service.browse_raw_folders(admin.id, study.id, None)
                self.assertEqual(len(roots["entries"]), 1)
                self.assertEqual(roots["entries"][0]["path"], str(tmp_path))

                nested = service.browse_raw_folders(admin.id, study.id, str(tmp_path))
            finally:
                service.settings.raw_path_browse_roots = original_roots

        self.assertEqual(nested["current_path"], str(tmp_path))
        self.assertEqual([item["name"] for item in nested["entries"]], ["first_batch", "second_batch"])
        self.assertEqual([item["raw_file_count"] for item in nested["entries"]], [1, 1])

    def test_workflow_output_registration_links_derived_dataset_to_input_samples(self) -> None:
        admin = self._user("admin@metabolens.local")
        workflow = self._workflow_definition()
        study = self._create_study("admin@metabolens.local")
        assay = self._default_assay(study.id)

        sample = StudyService(self.db).add_sample(
            admin.id,
            study.id,
            SampleCreate(name="S1", description="Sample 1"),
        )
        raw_file = self._add_file(study.id, assay.id, created_by=admin.id, status="registered", name="input.raw")
        raw_file.sample_id = sample.id
        self.db.commit()

        job = WorkflowJob(
            study_id=study.id,
            assay_id=assay.id,
            workflow_definition_id=workflow.id,
            requested_by=admin.id,
            status="running",
            input_manifest_json={
                "file_ids": [raw_file.id],
                "selected_files": [
                    {
                        "file_asset_id": raw_file.id,
                        "sample_id": sample.id,
                        "original_filename": raw_file.original_filename,
                        "storage_uri": raw_file.storage_uri,
                    }
                ],
            },
            parameter_values_json={},
            execution_backend="nextflow",
            execution_profile="local_docker",
            status_payload_json={},
        )
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)

        WorkflowJobStateService(self.db).mark_succeeded(
            job,
            output_records=[
                {
                    "artifact_role": "msdial_alignment",
                    "file_kind": "msdial_alignment",
                    "filename": "AlignResult-test.mdalign",
                    "storage_uri": "file:///tmp/AlignResult-test.mdalign",
                    "checksum_sha256": "c" * 64,
                    "file_size_bytes": 123,
                    "media_type": "application/octet-stream",
                },
                {
                    "artifact_role": "msdial_mztabm",
                    "file_kind": "msdial_mztabm",
                    "filename": "AlignResult-test.mzTabM",
                    "storage_uri": "file:///tmp/AlignResult-test.mzTabM",
                    "checksum_sha256": "d" * 64,
                    "file_size_bytes": 456,
                    "media_type": "text/plain",
                },
            ],
        )

        derived_datasets = self.db.query(Dataset).filter(Dataset.study_id == study.id, Dataset.assay_id == assay.id, Dataset.dataset_kind.in_(["derived", "maf"])).all()
        self.assertEqual(len(derived_datasets), 2)
        link_pairs = {(link.dataset_id, link.sample_id) for link in self.db.query(DatasetSampleLink).all()}
        for dataset in derived_datasets:
            self.assertIn((dataset.id, sample.id), link_pairs)

        output_rows = WorkflowJobReadService(self.db).list_output_files(admin.id, assay.id)
        self.assertEqual(len(output_rows), 2)
        self.assertTrue(all(row["dataset_kind"] in {"derived", "maf"} for row in output_rows))
        self.assertTrue(all(row["dataset_sample_ids"] == [sample.id] for row in output_rows))
        self.assertTrue(all(row["dataset_sample_names"] == [sample.name] for row in output_rows))
        self.assertTrue(all(row["file_sample_id"] is None for row in output_rows))

    def test_worker_lists_all_active_slurm_jobs(
        self,
    ) -> None:
        admin = self._user("admin@metabolens.local")
        workflow = self._workflow_definition()
        study = self._create_study()
        assay = self._default_assay(study.id)

        def make_job(
            status: str,
            profile: str,
            external_run_id: str | None,
        ) -> WorkflowJob:
            return WorkflowJob(
                study_id=study.id,
                assay_id=assay.id,
                workflow_definition_id=workflow.id,
                requested_by=admin.id,
                status=status,
                input_manifest_json={"file_ids": []},
                parameter_values_json={},
                execution_backend="nextflow",
                execution_profile=profile,
                status_payload_json={},
                external_run_id=external_run_id,
            )

        local_job = make_job(
            "running",
            "local_docker",
            "nf-local-12",
        )
        submitted_job = make_job(
            "submitted",
            "slurm_singularity_ssh",
            "482913",
        )
        running_job = make_job(
            "running",
            "slurm_singularity_ssh",
            "482914",
        )
        finished_job = make_job(
            "succeeded",
            "slurm_singularity_ssh",
            "482915",
        )
        self.db.add_all(
            [
                local_job,
                submitted_job,
                running_job,
                finished_job,
            ]
        )
        self.db.commit()

        service = WorkflowJobStateService(self.db)
        list_active = getattr(service, "list_active_slurm_jobs", None)

        self.assertIsNotNone(list_active)
        active_jobs = list_active()

        self.assertEqual(
            [job.id for job in active_jobs],
            [submitted_job.id, running_job.id],
        )

    def test_worker_can_claim_only_queued_ssh_slurm_jobs(self) -> None:
        admin = self._user("admin@metabolens.local")
        workflow = self._workflow_definition()
        study = self._create_study()
        assay = self._default_assay(study.id)

        def make_queued_job(profile: str) -> WorkflowJob:
            return WorkflowJob(
                study_id=study.id,
                assay_id=assay.id,
                workflow_definition_id=workflow.id,
                requested_by=admin.id,
                status="queued",
                input_manifest_json={"file_ids": []},
                parameter_values_json={},
                execution_backend="nextflow",
                execution_profile=profile,
                status_payload_json={},
            )

        local_job = make_queued_job("local_docker")
        ssh_job = make_queued_job("slurm_singularity_ssh")
        self.db.add_all([local_job, ssh_job])
        self.db.commit()

        try:
            claimed = WorkflowJobStateService(self.db).claim_next_job(
                execution_profile="slurm_singularity_ssh"
            )
        except TypeError as exc:
            self.fail(f"claim_next_job must accept an execution_profile filter: {exc}")

        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.id, ssh_job.id)
        self.assertEqual(claimed.status, "preparing")
        self.db.refresh(local_job)
        self.assertEqual(local_job.status, "queued")

    def test_workflow_output_listing_filters_by_selected_job(self) -> None:
        admin = self._user("admin@metabolens.local")
        workflow = self._workflow_definition()
        study = self._create_study("admin@metabolens.local")
        assay = self._default_assay(study.id)
        other_study = self._create_study("admin@metabolens.local")
        other_assay = self._default_assay(other_study.id)

        dataset = Dataset(
            study_id=study.id,
            assay_id=assay.id,
            name="workflow-derived",
            dataset_kind="derived",
            dataset_type="workflow_outputs",
            status="registered",
            created_by=admin.id,
        )
        self.db.add(dataset)
        self.db.flush()

        job_one = WorkflowJob(
            study_id=study.id,
            assay_id=assay.id,
            workflow_definition_id=workflow.id,
            requested_by=admin.id,
            status="succeeded",
            input_manifest_json={"file_ids": []},
            parameter_values_json={},
            execution_backend="nextflow",
            execution_profile="local_docker",
            status_payload_json={},
        )
        job_two = WorkflowJob(
            study_id=study.id,
            assay_id=assay.id,
            workflow_definition_id=workflow.id,
            requested_by=admin.id,
            status="succeeded",
            input_manifest_json={"file_ids": []},
            parameter_values_json={},
            execution_backend="nextflow",
            execution_profile="local_docker",
            status_payload_json={},
        )
        other_job = WorkflowJob(
            study_id=other_study.id,
            assay_id=other_assay.id,
            workflow_definition_id=workflow.id,
            requested_by=admin.id,
            status="succeeded",
            input_manifest_json={"file_ids": []},
            parameter_values_json={},
            execution_backend="nextflow",
            execution_profile="local_docker",
            status_payload_json={},
        )
        self.db.add_all([job_one, job_two, other_job])
        self.db.flush()

        file_one = DatasetFile(
            dataset_id=dataset.id,
            sample_id=None,
            file_role="msdial_alignment",
            original_filename="job-one.mdalign",
            storage_uri="file:///tmp/job-one.mdalign",
            checksum_sha256="e" * 64,
            file_size_bytes=1,
            media_type="text/plain",
            status="registered",
            created_by=admin.id,
        )
        file_two = DatasetFile(
            dataset_id=dataset.id,
            sample_id=None,
            file_role="msdial_alignment",
            original_filename="job-two.mdalign",
            storage_uri="file:///tmp/job-two.mdalign",
            checksum_sha256="f" * 64,
            file_size_bytes=2,
            media_type="text/plain",
            status="registered",
            created_by=admin.id,
        )
        self.db.add_all([file_one, file_two])
        self.db.flush()
        self.db.add_all(
            [
                WorkflowRunArtifact(workflow_job_id=job_one.id, dataset_file_id=file_one.id, artifact_role="msdial_alignment"),
                WorkflowRunArtifact(workflow_job_id=job_two.id, dataset_file_id=file_two.id, artifact_role="msdial_alignment"),
            ]
        )
        self.db.commit()

        service = WorkflowJobReadService(self.db)
        all_rows = service.list_output_files(admin.id, assay.id)
        job_one_rows = service.list_output_files(admin.id, assay.id, job_id=job_one.id)

        self.assertEqual({row["workflow_job_id"] for row in all_rows}, {job_one.id, job_two.id})
        self.assertEqual(len(job_one_rows), 1)
        self.assertEqual(job_one_rows[0]["workflow_job_id"], job_one.id)
        self.assertEqual(job_one_rows[0]["execution_profile"], "local_docker")
        self.assertEqual(job_one_rows[0]["original_filename"], "job-one.mdalign")
        self.assertEqual(job_one_rows[0]["checksum_sha256"], "e" * 64)
        self.assertEqual(WorkflowOutputFileOut.model_validate(job_one_rows[0]).model_dump()["checksum_sha256"], "e" * 64)
        with self.assertRaises(HTTPException) as ctx:
            service.list_output_files(admin.id, assay.id, job_id=other_job.id)
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
