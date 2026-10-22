"""Package identity must survive sparse IDs and reordered evidence exports."""
import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import HTTPException
from app.models.models import Dataset, DatasetFile, WorkflowRunArtifact
from tests import test_advanced_backend_services as fixtures
from tests.test_msdial_eic_reader import _spot_bytes, _write_css1_archive


class WorkflowFeatureServiceTests(unittest.TestCase):
    setUp = fixtures.AdvancedBackendServiceTests.setUp
    tearDown = fixtures.AdvancedBackendServiceTests.tearDown
    _user = fixtures.AdvancedBackendServiceTests._user
    _workflow_definition = fixtures.AdvancedBackendServiceTests._workflow_definition
    _create_study = fixtures.AdvancedBackendServiceTests._create_study
    _default_assay = fixtures.AdvancedBackendServiceTests._default_assay
    _terminal_job = fixtures.AdvancedBackendServiceTests._terminal_job

    def test_map_response_preserves_features_without_sending_review_decisions(self):
        import asyncio
        from fastapi.routing import serialize_response
        from app.api.routes.assays import router
        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            route = next(route for route in router.routes if route.path.endswith('/features/map'))
            result = asyncio.run(serialize_response(
                field=route.response_field,
                response_content=self.service.feature_map(self.user, self.job.assay_id, self.job.id, self.preview.id),
                exclude=route.response_model_exclude,
            ))
            self.assertEqual([row['feature_id'] for row in result], ['5', '0', '2'])
            self.assertEqual(result[2]['label'], 'Source ID 2 | Package 2 | m/z 102.0 | RT 3.0')
            self.assertTrue(all('decisions' not in row for row in result))
            self.assertEqual(self.trace('2')['feature']['decisions'], {'effective_decision_statistics': 'review'})

    def test_map_search_has_no_dropdown_cap_and_labels_source_identity(self):
        from unittest.mock import patch
        from app.services.workflow_feature_service import WorkflowFeatureService
        service = WorkflowFeatureService(self.db)
        bundle = {'source_tool': 'other', 'rows': [
            {'feature_id': f'opaque/{i}', 'source_feature_id': str(i), 'feature_name': 'Example'}
            for i in range(81)]}
        with patch.object(service, 'load_bundle', return_value=bundle):
            features = service.feature_map(1, 1, 1, 1, query='example')
            self.assertEqual(len(features), 81)
            self.assertIn('Source ID 80', features[-1]['label'])
            self.assertIn('Package ID opaque/80', features[-1]['label'])
            self.assertEqual(features[-1]['feature_id'], 'opaque/80')
            self.assertEqual(service.feature_map(1, 1, 1, 1, query='reference'), [])

    def fixture(self, root):
        from app.services.workflow_feature_service import WorkflowFeatureService
        self.job = self._terminal_job()
        self.user = self.job.requested_by
        self.service = WorkflowFeatureService(self.db)
        dataset = Dataset(study_id=self.job.study_id, assay_id=self.job.assay_id,
                          name='outputs', dataset_kind='derived', dataset_type='workflow_outputs', created_by=self.user)
        self.db.add(dataset)
        self.db.flush()
        self.dataset = dataset
        bundle = root / 'AlignResult-test' / 'preview'
        bundle.mkdir(parents=True)
        audit = bundle / 'feature_audit.tsv'
        audit.write_text('feature_id\tsource_feature_id\tfeature_name\tmz\trt_min\tadduct\thas_ms2\teffective_decision_statistics\n' +
                         ''.join(f'{i}\t{i}\tPackage {i}\t{100+i}\t{1+i}\t[M+H]+\tTRUE\treview\n' for i in (5,0,2)))
        self.audit = self.register(audit, 'metabolensr_feature_audit')
        samples = bundle / 'sample_metadata.tsv'
        samples.write_text('engine_source_column\tacquisition_id\tfile_asset_id\tsample_name\nB\tfile-12\t12\tSample\nA\tfile-11\t11\tSample\n')
        self.register(samples, 'metabolensr_sample_metadata')
        manifest = bundle / 'qc_preview_summary.json'
        manifest.write_text(json.dumps({'schema_version':'2.0','source':{'tool':'msdial'},'bundle_type':'preview'}))
        self.preview = self.register(manifest, 'metabolensr_preview_manifest')
        alignment = root / 'AlignResult-test.mdalign'
        alignment.write_text('Alignment ID\tAverage Rt(min)\tAverage Mz\tMetabolite name\tMS/MS assigned\tMS/MS spectrum\tA\tB\n' +
                             ''.join(f'{i}\t{1+i}\t{100+i}\tNative {i}\tTrue\t{40+i}:100\t1\t2\n' for i in (2,5,0)))
        self.register(alignment, 'msdial_alignment')
        archive = root / 'AlignResult-test.EIC.aef'
        _write_css1_archive(archive, [_spot_bytes(1+i,100+i,[(f,1+i,.5+i,1.5+i,[(1+i,10+i+f)]) for f in (0,1)]) for i in range(6)])
        self.eic = self.register(archive, 'msdial_eic_archive')

    def register(self, path, role):
        file = DatasetFile(dataset_id=self.dataset.id, file_role=role, original_filename=path.name,
                           storage_uri=path.as_uri(), checksum_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),created_by=self.user)
        self.db.add(file); self.db.flush()
        self.db.add(WorkflowRunArtifact(workflow_job_id=self.job.id,dataset_file_id=file.id,artifact_role=role))
        self.db.commit()
        return file

    def listing(self, **kwargs):
        return self.service.list_features(self.user,self.job.assay_id,self.job.id,self.preview.id,**kwargs)

    def trace(self, feature_id):
        return self.service.get_feature_trace(self.user,self.job.assay_id,self.job.id,self.preview.id,feature_id)

    def test_final_bundle_uses_final_statistics_decisions_and_preserves_evidence(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            final = root / 'AlignResult-test' / 'final'
            final.mkdir()
            audit = final / 'feature_audit_final.tsv'
            audit.write_text('feature_id\tsource_feature_id\tfeature_name\tmz\trt_min\teffective_decision_statistics\n'
                             '5\t5\tFinal retained\t105\t6\tinclude\n'
                             '0\t0\tFinal excluded\t100\t1\texclude\n'
                             '2\t2\tFinal review\t102\t3\treview\n')
            self.register(audit, 'metabolensr_feature_audit_final')
            samples = final / 'sample_metadata.tsv'
            samples.write_text((final.parent / 'preview' / 'sample_metadata.tsv').read_text())
            self.register(samples, 'metabolensr_sample_metadata')
            manifest = final / 'metabolensr_manifest.json'
            manifest.write_text(json.dumps({'schema_version': '2.0', 'bundle_type': 'final', 'source': {'tool': 'msdial'}}))
            final_file = self.register(manifest, 'metabolensr_manifest')
            args = (self.user, self.job.assay_id, self.job.id, final_file.id)
            self.assertEqual([f['feature_id'] for f in self.service.feature_map(*args)], ['5'])
            self.assertEqual(self.service.list_features(*args)['total'], 1)
            trace = self.service.get_feature_trace(*args, '5')
            self.assertEqual(trace['feature']['metabolite_name'], 'Final retained')
            self.assertEqual(trace['spot']['peaks'][0]['points'][0]['intensity'], 15)
            with self.assertRaises(HTTPException):
                self.service.get_feature_trace(*args, '0')
            self.assertEqual(self.listing()['total'], 3)

    def test_sparse_ids_resolve_chromatogram_spectrum_and_acquisition_independently_of_row_order(self):
        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            for feature_id in ('0','2','5'):
                payload = self.trace(feature_id)
                self.assertEqual(payload['feature']['feature_id'], feature_id)
                self.assertEqual(payload['feature']['metabolite_name'], f'Package {feature_id}')
                self.assertEqual(payload['spot']['peaks'][0]['points'][0]['intensity'],10+int(feature_id))
                self.assertEqual(payload['ms2']['experimental'][0]['mz'],40+int(feature_id))
                self.assertEqual([p['acquisition_id'] for p in payload['spot']['peaks']],['file-11','file-12'])
                self.assertNotIn('feature_index',payload['feature'])

    def test_browsing_and_ms2_survive_missing_archive(self):
        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp)); Path(self.eic.storage_uri.removeprefix('file://')).unlink()
            result=self.listing(page_size=2)
            self.assertEqual((result['total'],result['page_size']), (3,2))
            self.assertEqual([x['feature_id'] for x in result['items']], ['5','0'])
            self.assertEqual(self.listing(query='Package 2')['items'][0]['feature_id'],'2')
            payload=self.trace('2')
            self.assertIsNone(payload['spot'])
            self.assertTrue(payload['ms2']['available'])
            self.assertIn('unavailable',payload['evidence_message'])

    def test_scope_unknown_identity_and_deleted_job_are_rejected(self):
        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            for assay_id,job_id,preview in [(999,self.job.id,self.preview.id),(self.job.assay_id,999,self.preview.id),(self.job.assay_id,self.job.id,self.eic.id)]:
                with self.assertRaises(HTTPException):
                    self.service.list_features(self.user,assay_id,job_id,preview)
            with self.assertRaises(HTTPException): self.trace('00')
            from datetime import datetime, timezone
            self.job.deleted_at=datetime.now(timezone.utc); self.db.commit()
            with self.assertRaises(HTTPException): self.listing()

    def test_cache_tracks_checksum_changes_and_rejects_unregistered_edits(self):
        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp)); self.listing()
            path=Path(self.audit.storage_uri.removeprefix('file://'))
            path.write_text(path.read_text().replace('Package 5','Changed 5'))
            with self.assertRaises(HTTPException): self.listing()
            self.audit.checksum_sha256=hashlib.sha256(path.read_bytes()).hexdigest(); self.db.commit()
            self.assertEqual(self.listing()['items'][0]['metabolite_name'],'Changed 5')

    def rewrite(self, file, transform):
        path = Path(file.storage_uri.removeprefix('file://'))
        path.write_text(transform(path.read_text()))
        file.checksum_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        self.db.commit()

    def test_package_id_is_opaque_and_distinct_from_engine_source_id(self):
        from app.schemas.workflow import WorkflowFeatureTraceOut
        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            self.rewrite(self.audit, lambda text: text.replace('2\t2\tPackage 2', 'feature/a:2\t2\tPackage 2'))
            payload = self.trace('feature/a:2')
            self.assertEqual(payload['spot']['peaks'][0]['points'][0]['intensity'], 12)
            self.assertEqual(WorkflowFeatureTraceOut.model_validate(payload).feature.feature_id, 'feature/a:2')

    def test_another_existing_job_and_unrelated_user_cannot_read_the_bundle(self):
        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            other = self._terminal_job()
            with self.assertRaises(HTTPException):
                self.service.list_features(self.user, other.assay_id, other.id, self.preview.id)
            with self.assertRaises(HTTPException) as error:
                self.service.list_features(self._user('submitter@metabolens.local').id,
                                           self.job.assay_id,self.job.id,self.preview.id)
            self.assertEqual(error.exception.status_code, 403)

    def test_duplicate_feature_ids_are_not_silently_overwritten(self):
        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            self.rewrite(self.audit, lambda text: text.replace('2\t2\tPackage 2', '0\t2\tPackage 2'))
            with self.assertRaises(HTTPException): self.listing()

    def test_wrong_alignment_basename_never_falls_back_to_first_archive(self):
        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            self.eic.original_filename = 'AlignResult-other.EIC.aef'; self.db.commit()
            self.assertIsNone(self.trace('0')['spot'])
            self.assertEqual(self.listing()['total'], 3)

    def test_binary_feature_mass_mismatch_is_rejected_even_at_the_same_rt(self):
        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            archive = Path(self.eic.storage_uri.removeprefix('file://'))
            _write_css1_archive(archive, [_spot_bytes(1, 999, [(0,1,.5,1.5,[(1,99)])])])
            with self.assertRaises(HTTPException): self.trace('0')

    def test_acquisition_mapping_skips_reference_filename_metadata(self):
        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            alignment = Path(tmp) / 'AlignResult-test.mdalign'
            text = alignment.read_text().replace('MS/MS spectrum\tA\tB', 'MS/MS spectrum\tSpectrum reference file name\tA\tB')
            alignment.write_text(text.replace(':100\t1\t2', ':100\tref.raw\t1\t2'))
            self.assertEqual([p['acquisition_id'] for p in self.trace('0')['spot']['peaks']], ['file-11','file-12'])

    def test_trace_formats_only_selected_feature_and_enriches_native_once(self):
        from unittest.mock import patch
        from app.services import workflow_feature_service as module
        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            with patch.object(module, '_number', wraps=module._number) as number, patch.object(
                    self.service.eic, 'package_evidence_features',
                    wraps=self.service.eic.package_evidence_features) as evidence:
                payload = self.trace('2')
                self.assertEqual(payload['feature']['feature_id'], '2')
                self.assertEqual(payload['ms2']['experimental'][0]['mz'], 42)
                self.assertEqual(number.call_count, 2)
                self.assertEqual(evidence.call_count, 1)
