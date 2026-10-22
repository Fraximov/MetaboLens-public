import csv
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from fastapi import HTTPException
from tests import test_workflow_feature_service as fixtures


class FeatureReviewTests(unittest.TestCase):
    setUp = fixtures.WorkflowFeatureServiceTests.setUp
    tearDown = fixtures.WorkflowFeatureServiceTests.tearDown
    _user = fixtures.WorkflowFeatureServiceTests._user
    _workflow_definition = fixtures.WorkflowFeatureServiceTests._workflow_definition
    _create_study = fixtures.WorkflowFeatureServiceTests._create_study
    _default_assay = fixtures.WorkflowFeatureServiceTests._default_assay
    _terminal_job = fixtures.WorkflowFeatureServiceTests._terminal_job
    register = fixtures.WorkflowFeatureServiceTests.register

    def fixture(self, root):
        fixtures.WorkflowFeatureServiceTests.fixture(self, root)
        from app.services.workflow_feature_review_service import WorkflowFeatureReviewService
        from unittest.mock import patch
        from types import SimpleNamespace
        settings = patch('app.services.workflow_feature_review_service.get_settings', create=True,
                         return_value=SimpleNamespace(upload_storage_dir=str(root / 'app-storage')))
        settings.start()
        self.addCleanup(settings.stop)
        self.reviews = WorkflowFeatureReviewService(self.db)
        self.job.run_root = str(root)
        self.job.parameter_values_json = {'workflow_stage': 'qc_preview'}
        self.job.input_manifest_json = {'engine': {'descriptor': {'review_transitions': {'qc_preview': 'full'}}}}
        rds = root / 'AlignResult-test/preview/qc_preview.rds'
        rds.write_bytes(b'opaque R preview')
        self.register(rds, 'metabolensr_preview')
        audit = Path(self.audit.storage_uri.removeprefix('file://'))
        text = audit.read_text().replace('effective_decision_statistics', 'automatic_decision_statistics\tautomatic_decision_annotation\teffective_decision_statistics')
        text = text.replace('\treview\n', '\tinclude\treview\tinclude\n')
        audit.write_text(text)
        import hashlib
        self.audit.checksum_sha256 = hashlib.sha256(audit.read_bytes()).hexdigest()
        self.db.commit()
        self.args = (self.user, self.job.assay_id, self.job.id, self.preview.id)

    def test_draft_revision_stable_queue_domains_and_confirmation(self):
        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            view = self.reviews.get(*self.args)
            self.assertEqual((view['revision'], view['total']), (0, 3))
            edit = {'feature_id': '0', 'domain': 'annotation', 'value': 'exclude'}
            self.reviews.patch(*self.args, expected_revision=0, changes=[edit])
            with self.assertRaises(HTTPException) as stale:
                self.reviews.patch(*self.args, expected_revision=0, changes=[edit])
            self.assertEqual(stale.exception.status_code, 409)
            copy = {'feature_id': '0', 'domain': 'statistics', 'value': 'include', 'copy_to_annotation': True}
            with self.assertRaises(HTTPException):
                self.reviews.patch(*self.args, expected_revision=1, changes=[copy])
            result = self.reviews.patch(*self.args, expected_revision=1, changes=[{**copy, 'confirm_annotation_replace': True}])
            self.assertEqual(result['revision'], 2)
            self.reviews.patch(*self.args, expected_revision=2, changes=[{'feature_id':'5','domain':'statistics','value':'exclude'}])
            view = self.reviews.get(*self.args)
            self.assertEqual(view['total'], 3)
            row = next(x for x in view['items'] if x['feature_id']=='5')
            self.assertEqual(row['automatic_statistics'], 'include')
            self.assertEqual(row['effective_statistics'], 'exclude')
            self.db.expire_all()
            self.assertEqual(self.reviews.get(*self.args)['revision'], 3)

    def test_freeze_continue_and_retry_keep_one_revision(self):
        from app.services.workflow_launch_service import WorkflowLaunchService
        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            self.reviews.patch(*self.args, expected_revision=0, changes=[{'feature_id':'0','domain':'statistics','value':'include'}])
            launch = WorkflowLaunchService(self.db)
            launch.continue_review_stage(*self.args[:3], preview_file_id=self.preview.id, expected_revision=1)
            frozen = dict(self.job.input_manifest_json['feature_review'])
            self.assertTrue(Path(frozen['path']).is_relative_to(Path(tmp) / 'app-storage'))
            self.assertFalse((Path(tmp) / 'contract' / 'curation').exists())
            data = Path(frozen['path']).read_text()
            rows = list(csv.DictReader(io.StringIO(data), delimiter='\t'))
            self.assertEqual(rows[0]['feature_id'], '0')
            self.assertEqual(rows[0]['operator_override_statistics'], 'include')
            self.assertEqual(rows[0]['operator_override_annotation'], 'automatic')
            self.assertEqual(self.job.status, 'queued')
            self.job.status='failed'; self.job.status_payload_json={'exit_code':1}; self.db.commit()
            self.assertTrue(self.reviews.get(*self.args)['can_retry'])
            self.reviews.patch(*self.args, expected_revision=1, changes=[{'feature_id':'5','domain':'annotation','value':'include'}])
            launch.continue_review_stage(*self.args[:3])
            self.assertEqual(self.job.input_manifest_json['feature_review'], frozen)
            self.assertEqual(Path(frozen['path']).read_text(), data)

    def test_fixture_engine_opaque_draft_freeze_and_retry_use_the_same_bundle(self):
        import json
        from app.core.engine_contract import sha256_file
        from app.models.models import WorkflowFeatureReview, WorkflowRunArtifact
        from app.services.workflow_launch_service import WorkflowLaunchService
        from tests.test_engine_contract import fixture_descriptor

        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            descriptor = {**fixture_descriptor(), 'review_transitions': {'qc_preview': 'full'}}
            self.job.input_manifest_json = {'engine': {'descriptor': descriptor}}
            rewrite = fixtures.WorkflowFeatureServiceTests.rewrite
            rewrite(self, self.preview, lambda _: json.dumps({
                'schema_version': '2.0', 'bundle_type': 'preview', 'source': {'tool': 'fixture_engine'}}))
            rewrite(self, self.audit, lambda text: text
                .replace('5\t5\tPackage 5', 'feature/z\tnative:five\tPackage 5')
                .replace('0\t0\tPackage 0', 'feature/a\tnative:zero\tPackage 0')
                .replace('2\t2\tPackage 2', 'feature/m\tnative:two\tPackage 2'))
            view = self.reviews.get(*self.args)
            self.assertEqual([row['feature_id'] for row in view['items']], ['feature/z', 'feature/a', 'feature/m'])
            self.reviews.patch(*self.args, expected_revision=0, changes=[
                {'feature_id': 'feature/a', 'domain': 'statistics', 'value': 'exclude'}])
            launch = WorkflowLaunchService(self.db)
            launch.continue_review_stage(*self.args[:3], preview_file_id=self.preview.id, expected_revision=1)
            frozen = dict(self.job.input_manifest_json['feature_review'])
            frozen_bytes = Path(frozen['path']).read_bytes()
            rows = list(csv.DictReader(io.StringIO(frozen_bytes.decode()), delimiter='\t'))
            self.assertEqual([(r['feature_id'], r['operator_override_statistics']) for r in rows],
                             [('feature/a', 'exclude')])
            self.assertEqual(sha256_file(Path(frozen['path'])), frozen['sha256'])
            self.assertEqual(self.job.parameter_values_json['workflow_stage'], 'full')
            self.assertTrue(self.job.parameter_values_json['nextflow_resume'])
            self.assertEqual(self.job.status, 'queued')
            self.assertEqual(frozen['preview_file_id'], self.preview.id)
            self.job.status = 'failed'
            self.job.status_payload_json = {'exit_code': 1}
            self.db.commit()
            self.assertTrue(self.reviews.get(*self.args)['can_retry'])
            self.reviews.patch(*self.args, expected_revision=1, changes=[
                {'feature_id': 'feature/z', 'domain': 'annotation', 'value': 'include'}])
            launch.continue_review_stage(*self.args[:3])
            self.assertEqual(self.job.status, 'queued')
            self.assertEqual(self.job.input_manifest_json['engine']['descriptor'], descriptor)
            self.assertEqual(self.job.input_manifest_json['feature_review'], frozen)
            self.assertEqual(Path(frozen['path']).read_bytes(), frozen_bytes)
            review = self.db.query(WorkflowFeatureReview).one()
            self.assertEqual((review.revision, review.frozen_revision, review.frozen_file_id), (2, 1, frozen['file_id']))
            self.assertEqual(self.db.query(WorkflowRunArtifact).filter_by(
                workflow_job_id=self.job.id, artifact_role='metabolensr_review_overrides').count(), 1)

    def test_invalid_drafts_and_stale_continue_do_not_requeue(self):
        from app.services.workflow_launch_service import WorkflowLaunchService
        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            for changes in [[{'feature_id':'nope','domain':'statistics','value':'include'}],
                            [{'feature_id':'0','domain':'statistics','value':'review'}],
                            [{'feature_id':'0','domain':'bad','value':'include'}],
                            [{'feature_id':'0','domain':'statistics','value':'include'}]*2]:
                with self.assertRaises(HTTPException):
                    self.reviews.patch(*self.args, expected_revision=0, changes=changes)
            with self.assertRaises(HTTPException):
                WorkflowLaunchService(self.db).continue_review_stage(*self.args[:3], preview_file_id=self.preview.id, expected_revision=8)
            self.assertEqual(self.job.status, 'succeeded')

    def test_wrong_scope_permission_and_preview_integrity(self):
        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            self.reviews.patch(*self.args, expected_revision=0, changes=[{'feature_id':'0','domain':'statistics','value':'exclude'}])
            for args in [(self.user,self.job.assay_id+100,self.job.id,self.preview.id),
                         (self.user,self.job.assay_id,self.job.id+100,self.preview.id)]:
                with self.assertRaises(HTTPException):
                    self.reviews.get(*args)
            reader = self._user('submitter@metabolens.local')
            with self.assertRaises(HTTPException):
                self.reviews.patch(reader.id,*self.args[1:],expected_revision=1,changes=[{'feature_id':'0','domain':'statistics','value':'include'}])
            path=Path(tmp)/'AlignResult-test/preview/qc_preview.rds'
            path.write_bytes(b'changed')
            with self.assertRaises(HTTPException):
                self.reviews.get(*self.args)

    def test_search_queues_page_and_domain_are_independent(self):
        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            self.assertEqual(self.reviews.get(*self.args,queue='review')['total'],0)
            self.assertEqual(self.reviews.get(*self.args,queue='review',domain='annotation')['total'],3)
            self.assertEqual(self.reviews.get(*self.args,page_size=1,page=2)['items'][0]['feature_id'],'0')
            self.assertEqual(self.reviews.get(*self.args,query='Package 2')['total'],1)
            self.reviews.patch(*self.args,expected_revision=0,changes=[{'feature_id':'0','domain':'statistics','value':'exclude'}])
            self.assertEqual(self.reviews.get(*self.args,queue='manual')['total'],1)
            self.assertEqual(self.reviews.get(*self.args,effective='exclude')['total'],1)

    def test_missing_native_evidence_does_not_block_review(self):
        from unittest.mock import patch
        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            with patch.object(self.reviews.features.eic,'package_evidence_features',side_effect=ValueError('bad native alignment')):
                self.assertEqual(self.reviews.get(*self.args)['total'],3)

    def test_completed_board_reads_final_decisions_not_unapplied_draft(self):
        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            self.reviews.patch(*self.args,expected_revision=0,changes=[{'feature_id':'0','domain':'statistics','value':'exclude'}])
            final=Path(tmp)/'AlignResult-test/final'
            final.mkdir()
            audit=final/'feature_audit_final.tsv'
            audit.write_text('feature_id\toperator_override_statistics\toperator_override_annotation\teffective_decision_statistics\teffective_decision_annotation\n0\tautomatic\tautomatic\tinclude\treview\n5\tautomatic\tautomatic\tinclude\treview\n2\tautomatic\tautomatic\tinclude\treview\n')
            self.register(audit,'metabolensr_feature_audit_final')
            self.job.parameter_values_json={'workflow_stage':'full'};self.db.commit()
            view=self.reviews.get(*self.args)
            row=next(r for r in view['items'] if r['feature_id']=='0')
            self.assertEqual(row['effective_statistics'],'include')
            self.assertEqual(view['decision_source'],'final')
            self.assertFalse(view['can_edit'])

    def test_freeze_transaction_failure_leaves_no_executable_reference(self):
        from unittest.mock import patch
        from app.models.models import WorkflowFeatureReview, WorkflowJob, WorkflowRunArtifact
        from app.services.workflow_launch_service import WorkflowLaunchService
        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            self.reviews.patch(*self.args,expected_revision=0,changes=[{'feature_id':'0','domain':'statistics','value':'include'}])
            job_id=self.job.id
            with patch.object(self.db,'commit',side_effect=RuntimeError('commit failed')):
                with self.assertRaisesRegex(RuntimeError,'commit failed'):
                    WorkflowLaunchService(self.db).continue_review_stage(*self.args[:3],preview_file_id=self.preview.id,expected_revision=1)
            self.db.rollback()
            job=self.db.get(WorkflowJob,job_id)
            self.assertEqual(job.status,'succeeded')
            self.assertNotIn('feature_review',job.input_manifest_json)
            self.assertIsNone(self.db.query(WorkflowFeatureReview).one().frozen_file_id)
            self.assertEqual(self.db.query(WorkflowRunArtifact).filter_by(artifact_role='metabolensr_review_overrides').count(),0)
            self.assertEqual(len(list((Path(tmp)/'app-storage').rglob('*.tsv'))),1)

    def test_read_checksum_reuse_preserves_fresh_write_and_freeze_verification(self):
        from unittest.mock import patch
        from app.services import workflow_feature_review_service as module
        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            with patch.object(module, 'sha256_file', wraps=module.sha256_file) as digest:
                self.assertEqual(self.reviews.get(*self.args)['total'], 3)
                first = digest.call_count
                self.assertEqual(self.reviews.get(*self.args)['total'], 3)
                self.assertEqual(digest.call_count, first)
                self.reviews.patch(*self.args, expected_revision=0,
                    changes=[{'feature_id':'0','domain':'statistics','value':'exclude'}])
                self.assertEqual(digest.call_count, first + 3)
                self.reviews.freeze(*self.args, expected_revision=1)
                # Three preview inputs plus the newly written override file.
                self.assertEqual(digest.call_count, first + 7)

    def test_unsearched_review_formats_only_selected_page(self):
        from unittest.mock import patch
        from app.services import workflow_feature_service as module
        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            with patch.object(module, '_number', wraps=module._number) as number:
                view = self.reviews.get(*self.args, page=2, page_size=1)
                self.assertEqual((view['total'], view['items'][0]['feature_id']), (3, '0'))
                self.assertEqual(number.call_count, 2)

    def test_warm_preview_digest_rejects_same_size_edit_with_restored_mtime(self):
        import os
        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            self.assertEqual(self.reviews.get(*self.args)['total'], 3)
            path = Path(tmp) / 'AlignResult-test/preview/qc_preview.rds'
            before = path.stat()
            path.write_bytes(b'x' * before.st_size)
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
            with self.assertRaises(HTTPException) as changed:
                self.reviews.get(*self.args)
            self.assertEqual(changed.exception.status_code, 400)

    def test_paged_search_preserves_comment_and_effective_filters(self):
        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            self.reviews.patch(*self.args, expected_revision=0, changes=[
                {'feature_id':'0', 'domain':'statistics', 'value':'exclude', 'comment':'operator note'}])
            view = self.reviews.get(*self.args, query='operator note exclude', effective='exclude',
                                    has_ms2=True, page=99, page_size=1)
            self.assertEqual((view['total'], view['page'], view['items'][0]['feature_id']), (1, 1, '0'))
            self.assertEqual(view['items'][0]['comment'], 'operator note')
            self.assertEqual(self.reviews.get(*self.args, has_ms2=False)['total'], 0)
