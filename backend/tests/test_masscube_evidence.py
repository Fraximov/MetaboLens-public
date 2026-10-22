"""Native evidence is indexed, immutable, and bound to the authorized package."""
import copy
import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import HTTPException

from tests import test_workflow_feature_service as fixtures


class MassCubeEvidenceTests(unittest.TestCase):
    setUp = fixtures.WorkflowFeatureServiceTests.setUp
    tearDown = fixtures.WorkflowFeatureServiceTests.tearDown
    _user = fixtures.WorkflowFeatureServiceTests._user
    _workflow_definition = fixtures.WorkflowFeatureServiceTests._workflow_definition
    _create_study = fixtures.WorkflowFeatureServiceTests._create_study
    _default_assay = fixtures.WorkflowFeatureServiceTests._default_assay
    _terminal_job = fixtures.WorkflowFeatureServiceTests._terminal_job
    fixture = fixtures.WorkflowFeatureServiceTests.fixture
    register = fixtures.WorkflowFeatureServiceTests.register
    listing = fixtures.WorkflowFeatureServiceTests.listing
    trace = fixtures.WorkflowFeatureServiceTests.trace
    rewrite = fixtures.WorkflowFeatureServiceTests.rewrite

    def masscube_fixture(self, root):
        self.fixture(root)
        self.job.input_manifest_json = {'engine': {'descriptor': {'evidence_adapter_id': 'masscube-indexed-v1'}}}
        self.manifest_path = Path(self.preview.storage_uri.removeprefix('file://'))
        self.manifest = {'schema_version': '2.0', 'bundle_type': 'preview',
                         'inputs': {'source': {'md5': 'a' * 32}},
                         'source': {'tool': 'masscube', 'evidence_adapter_id': 'masscube-indexed-v1',
                                    'acquisition_evidence': {'md5': 'b' * 32}}}
        self.rewrite(self.preview, lambda _: json.dumps(self.manifest))
        self.records = []
        for identity in ('2', '5', '0'):
            self.records.append({'schema_version': '1.0', 'source_feature_id': identity,
                                 'mz': 100 + int(identity), 'rt_min': 1 + int(identity),
                                 'peaks': [{'acquisition_id': acquisition, 'native_feature_id': 'native/' + identity if i == 0 else None,
                                            'detected': i == 0, 'gap_filled': i == 1, 'top': 3.0,
                                            'left': None, 'right': None,
                                            'rt': [j / 1000 for j in range(1801)],
                                            'intensity': [float(j) for j in range(1801)]}
                                           for i, acquisition in enumerate(('file-11', 'file-12'))],
                                 'ms2': {'provider': 'MassCube', 'available': True, 'match_tolerance_da': 0.015,
                                         'experimental': [{'mz': 42.0, 'intensity': 100.0, 'matched': False}],
                                         'reference': None, 'scores': {'similarity': None, 'search_mode': None},
                                         'reference_rt': None, 'reference_mz': None, 'library_filename': None}})
        native = root / 'masscube'
        native.mkdir()
        self.data_path = native / 'evidence.jsonl'
        self.index_path = native / 'evidence.index.json'
        self.index = {'schema_version': '1.0', 'adapter_id': 'masscube-indexed-v1', 'engine': 'masscube',
                      'retention_time_unit': 'minute', 'bundle_id': 'AlignResult-test',
                      'data_file': 'evidence.jsonl', 'aligned_table_md5': 'a' * 32,
                      'acquisition_evidence_md5': 'b' * 32,
                      'acquisitions': [{'acquisition_id': 'file-11', 'source_column': 'A', 'file_asset_id': '11', 'sample_name': 'Native'},
                                       {'acquisition_id': 'file-12', 'source_column': 'B', 'file_asset_id': '12', 'sample_name': 'Native'}]}
        self.write_records()
        self.data_file = self.register(self.data_path, 'masscube_evidence_data')
        self.index_file = self.register(self.index_path, 'masscube_evidence_index')
        return self

    def write_records(self):
        data = b''
        features = {}
        for record in self.records:
            line = (json.dumps(record) + '\n').encode()
            features[record['source_feature_id']] = {'offset': len(data), 'length': len(line),
                                                      'has_ms2': True, 'has_reference': False,
                                                      'msms_matched': None, 'low_score': None}
            data += line
        self.data_path.write_bytes(data)
        self.index.update(features=features, data_sha256=hashlib.sha256(data).hexdigest())
        self.index_path.write_text(json.dumps(self.index))
        if hasattr(self, 'data_file'):
            for file, path in ((self.data_file, self.data_path), (self.index_file, self.index_path)):
                file.checksum_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
            self.db.commit()

    def test_native_evidence_has_exact_identity_nullable_bounds_and_bounded_trace(self):
        from app.schemas.workflow import WorkflowFeatureTraceOut
        with TemporaryDirectory() as tmp:
            self.masscube_fixture(Path(tmp))
            self.rewrite(self.audit, lambda text: text.replace('2\t2\tPackage 2', 'feature/a:2\t2\tPackage 2'))
            result = self.trace('feature/a:2')
            self.assertEqual(result['reader'], 'masscube-indexed-v1')
            model = WorkflowFeatureTraceOut.model_validate(result)
            self.assertEqual(model.feature.metabolite_name, 'Package 2')
            self.assertEqual(model.ms2.provider, 'MassCube')
            self.assertEqual(model.ms2.scores, {'similarity': None, 'search_mode': None})
            self.assertIsNone(model.spot.peaks[0].left)
            self.assertTrue(model.spot.peaks[1].gap_filled)
            self.assertEqual(model.spot.peaks[0].sample_name, 'Sample')
            self.assertEqual(model.spot.peaks[0].acquisition_id, 'file-11')
            self.assertLessEqual(len(model.spot.peaks[0].points), 900)
            self.assertEqual(model.spot.peaks[0].points[-1].x, 1.8)
            self.assertEqual(model.spot.peaks[0].point_count, 1801)

    def test_index_binding_and_identity_corruption_are_rejected(self):
        mutations = [lambda x: x.update(retention_time_unit='second'),
                     lambda x: x.update(schema_version='9'),
                     lambda x: x.update(data_sha256='f' * 64),
                     lambda x: x.update(aligned_table_md5='f' * 32),
                     lambda x: x.update(acquisition_evidence_md5='f' * 32),
                     lambda x: x.update(bundle_id='other'),
                     lambda x: x.update(data_file='../evidence.jsonl'),
                     lambda x: x['features']['2'].update(offset=-1),
                     lambda x: x['features']['2'].update(length=10**12),
                     lambda x: x['features'].update(extra=x['features']['2']),
                     lambda x: x['acquisitions'][1].update(acquisition_id='file-11'),
                     lambda x: x['acquisitions'][0].update(source_column='unknown'),
                     lambda x: x['acquisitions'][0].update(file_asset_id='999')]
        with TemporaryDirectory() as tmp:
            self.masscube_fixture(Path(tmp))
            baseline = copy.deepcopy(self.index)
            for mutate in mutations:
                with self.subTest(mutate=mutate):
                    value = copy.deepcopy(baseline)
                    mutate(value)
                    self.rewrite(self.index_file, lambda _: json.dumps(value))
                    with self.assertRaises(HTTPException):
                        self.trace('2')

    def test_detected_bounds_use_registered_native_ids_and_leave_gap_fills_unshaded(self):
        with TemporaryDirectory() as tmp:
            self.masscube_fixture(Path(tmp))
            self.records[0]['peaks'][0]['top'] = 1.0004
            self.write_records()
            native = self.data_path.parent / 'single_files'
            native.mkdir()
            path = native / 'A.txt'
            header = 'feature_ID\tRT\tRT_start\tRT_end\n'
            path.write_text(header + 'native/5\t1\t0.5\t1.5\nnative/2\t1\t0.9\t1.1\n')
            # Unregistered files must never supply evidence.
            self.assertIsNone(self.trace('2')['spot']['peaks'][0]['left'])
            file = self.register(path, 'masscube_single_file')
            peaks = self.trace('2')['spot']['peaks']
            self.assertEqual((peaks[0]['left'], peaks[0]['top'], peaks[0]['right']), (0.9, 1.0004, 1.1))
            self.assertIsNone(peaks[1]['left'])
            self.assertIsNone(peaks[1]['right'])
            # Single-scan endpoints are rounded by MassCube to three decimals.
            self.rewrite(file, lambda _: header + 'native/2\t1\t1\t1\n')
            peak = self.trace('2')['spot']['peaks'][0]
            self.assertEqual((peak['left'], peak['right']), (1.0, 1.0))
            # A warm cache must not hide edits to the registered native table.
            path.write_text(header + 'native/2\t1\t0.8\t1.2\n')
            with self.assertRaisesRegex(HTTPException, 'checksum mismatch'):
                self.trace('2')

    def test_native_boundary_identity_and_invalid_limits_are_rejected(self):
        with TemporaryDirectory() as tmp:
            self.masscube_fixture(Path(tmp))
            native = self.data_path.parent / 'single_files'
            native.mkdir()
            path = native / 'A.txt'
            path.write_text('feature_ID\tRT\tRT_start\tRT_end\nnative/2\t3\t2\t4\n')
            file = self.register(path, 'masscube_single_file')
            for rows in ('native/5\t3\t2\t4\n',
                         'native/2\t3\t2\t4\nnative/2\t3\t2\t4\n',
                         'native/2\t3\t4\t2\n',
                         'native/2\t3\tnan\t4\n',
                         'native/2\t2\t1\t4\n'):
                with self.subTest(rows=rows):
                    self.rewrite(file, lambda _: 'feature_ID\tRT\tRT_start\tRT_end\n' + rows)
                    with self.assertRaises(HTTPException):
                        self.trace('2')

    def test_selected_record_corruption_is_rejected(self):
        mutations = [lambda x: x.update(source_feature_id='different'),
                     lambda x: x.update(mz=float('nan')),
                     lambda x: x['peaks'][0].update(intensity=[1]),
                     lambda x: x['peaks'][0].update(rt=[2, 1], intensity=[1, 2]),
                     lambda x: x['peaks'][0].update(rt=[1, float('inf')], intensity=[1, 2]),
                     lambda x: x['peaks'][1].update(acquisition_id='file-11'),
                     lambda x: x['peaks'].pop(),
                     lambda x: x['peaks'][0].update(detected='true'),
                     lambda x: x['ms2']['scores'].update(similarity=float('inf'))]
        with TemporaryDirectory() as tmp:
            self.masscube_fixture(Path(tmp))
            baseline = copy.deepcopy(self.records)
            for mutate in mutations:
                with self.subTest(mutate=mutate):
                    self.records = copy.deepcopy(baseline)
                    mutate(self.records[0])
                    self.write_records()
                    if self.records[0]['source_feature_id'] != '2':
                        self.index['features']['2'] = self.index['features'].pop('different')
                        self.rewrite(self.index_file, lambda _: json.dumps(self.index))
                    with self.assertRaises(HTTPException):
                        self.trace('2')

    def test_registered_data_and_index_edits_invalidate_verified_cache(self):
        with TemporaryDirectory() as tmp:
            self.masscube_fixture(Path(tmp))
            self.trace('2')
            self.data_path.write_bytes(self.data_path.read_bytes().replace(b'native/', b'changed'))
            with self.assertRaises(HTTPException):
                self.trace('2')
            self.write_records()
            self.trace('2')
            self.index_path.write_text(self.index_path.read_text() + ' ')
            with self.assertRaises(HTTPException):
                self.listing()

    def test_missing_declared_artifacts_and_unknown_adapter_fail_explicitly(self):
        with TemporaryDirectory() as tmp:
            self.masscube_fixture(Path(tmp))
            self.job.input_manifest_json = {'engine': {'descriptor': {'evidence_adapter_id': 'unknown-reader'}}}
            self.db.commit()
            with self.assertRaises(HTTPException):
                self.listing()
            self.job.input_manifest_json = {'engine': {'descriptor': {'evidence_adapter_id': 'masscube-indexed-v1'}}}
            self.db.commit()
            self.data_path.unlink()
            with self.assertRaises(HTTPException):
                self.trace('2')

    def test_metadata_listing_does_not_parse_or_read_selected_data_records(self):
        with TemporaryDirectory() as tmp:
            self.masscube_fixture(Path(tmp))
            self.records[0]['peaks'][0]['rt'] = [999]  # malformed record; not read during listing
            self.write_records()
            self.assertEqual(self.listing()['total'], 3)
            self.assertEqual(self.trace('0')['feature']['feature_id'], '0')
            with self.assertRaises(HTTPException):
                self.trace('2')

    def test_catalogue_reads_only_the_index_and_trace_still_checks_the_full_data(self):
        import builtins
        with TemporaryDirectory() as tmp:
            self.masscube_fixture(Path(tmp))
            opened = []
            original_open = builtins.open

            def track_open(path, *args, **kwargs):
                opened.append(str(path))
                return original_open(path, *args, **kwargs)

            with patch('builtins.open', side_effect=track_open):
                self.assertEqual(self.listing()['total'], 3)
            self.assertNotIn(str(self.data_path), opened)
            # Same-size corruption elsewhere in the file must still block an
            # unrelated requested trace, even after the catalogue cache warmed.
            self.data_path.write_bytes(self.data_path.read_bytes().replace(b'native/5', b'broken/5'))
            self.assertEqual(self.listing()['total'], 3)
            with self.assertRaisesRegex(HTTPException, 'checksum mismatch'):
                self.trace('2')

    def test_empty_trace_is_unavailable_without_inventing_bounds(self):
        with TemporaryDirectory() as tmp:
            self.masscube_fixture(Path(tmp))
            self.records[0]['peaks'][0].update(rt=[], intensity=[], top=None)
            self.write_records()
            peak = self.trace('2')['spot']['peaks'][0]
            self.assertEqual(peak['points'], [])
            self.assertIsNone(peak['top'])

    def test_authorization_is_checked_after_cache_warmup(self):
        with TemporaryDirectory() as tmp:
            self.masscube_fixture(Path(tmp))
            self.trace('2')
            with self.assertRaises(HTTPException) as error:
                self.service.get_feature_trace(self._user('submitter@metabolens.local').id,
                                               self.job.assay_id, self.job.id, self.preview.id, '2')
            self.assertEqual(error.exception.status_code, 403)

    def test_third_adapter_preserves_reordered_opaque_features_and_acquisitions(self):
        from app.schemas.workflow import WorkflowFeatureTraceOut
        from app.services.engine_evidence import registry

        # Native record order differs from audit order; trace acquisition order
        # differs by feature and from the package's B/A metadata order.
        native = {'native/a:2': [('file-11', 21), ('file-12', 22)],
                  'native/c:0': [],
                  'native/b:5': [('file-12', 52), ('file-11', 51)]}

        class FixtureAdapter:
            def __init__(self, eic):
                pass

            def features(self, bundle):
                return {identity: {'has_reference': identity == 'native/a:2'} for identity in native}

            def trace(self, bundle, source_feature_id, samples, *, evidence_features):
                measurements = native[source_feature_id]
                mapping = {sample['acquisition_id']: sample for sample in samples}
                peaks = []
                for position, (acquisition, intensity) in enumerate(measurements):
                    sample = mapping[acquisition]
                    peaks.append(dict(file_id=position, acquisition_id=acquisition,
                        file_asset_id=sample['file_asset_id'], sample_name=sample['sample_name'],
                        source_column=sample['engine_source_column'], top=None, left=None, right=None,
                        point_count=2, returned_point_count=2,
                        points=[{'x': 1.0, 'intensity': intensity}, {'x': 1.1, 'intensity': intensity + 1}]))
                return {'reader': 'third-fixture-v1', 'ms2': None,
                        'spot': {'version': '1.0', 'feature_count': 3, 'center': {'rt': 1.0, 'mz': 100.0},
                                 'trace_count': len(peaks), 'peaks': peaks} if peaks else None,
                        'evidence_message': None if peaks else 'No acquired trace for this feature.'}

        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            self.job.input_manifest_json = {'engine': {'descriptor': {
                'engine_id': 'fixture_engine', 'evidence_adapter_id': 'third-fixture-v1'}}}
            self.rewrite(self.preview, lambda _: json.dumps({
                'schema_version': '2.0', 'bundle_type': 'preview', 'source': {'tool': 'fixture_engine'}}))
            self.rewrite(self.audit, lambda text: text
                .replace('2\t2\tPackage 2', 'pkg/a:2\tnative/a:2\tPackage 2')
                .replace('5\t5\tPackage 5', 'pkg/b:5\tnative/b:5\tPackage 5')
                .replace('0\t0\tPackage 0', 'pkg/c:0\tnative/c:0\tPackage 0'))
            with patch.dict(registry.ADAPTERS, {'third-fixture-v1': FixtureAdapter}):
                features = self.listing()['items']
                self.assertEqual([row['feature_id'] for row in features], ['pkg/b:5', 'pkg/c:0', 'pkg/a:2'])
                self.assertEqual([row['has_reference'] for row in features], [False, False, True])
                for identity, expected in (
                    ('pkg/a:2', [('file-11', '11', 'A', 21), ('file-12', '12', 'B', 22)]),
                    ('pkg/b:5', [('file-12', '12', 'B', 52), ('file-11', '11', 'A', 51)]),
                ):
                    with self.subTest(feature=identity):
                        result = WorkflowFeatureTraceOut.model_validate(self.trace(identity))
                        self.assertEqual(result.reader, 'third-fixture-v1')
                        self.assertEqual(result.feature.feature_id, identity)
                        self.assertEqual(result.feature.metabolite_name, 'Package ' + identity[-1])
                        self.assertEqual([(p.acquisition_id, p.file_asset_id, p.source_column, p.points[0].intensity)
                                          for p in result.spot.peaks], expected)
                        self.assertEqual([p.sample_name for p in result.spot.peaks], ['Sample', 'Sample'])
                absent = self.trace('pkg/c:0')
                self.assertEqual(absent['reader'], 'third-fixture-v1')
                self.assertIsNone(absent['spot'])
                self.assertIn('No acquired trace', absent['evidence_message'])

    def test_declared_empty_adapter_is_rejected_and_no_adapter_returns_unavailable(self):
        with TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            self.rewrite(self.preview, lambda _: json.dumps({
                'schema_version': '2.0', 'bundle_type': 'preview', 'source': {'tool': 'other'}}))
            self.assertIsNone(self.trace('2')['reader'])
            self.assertIsNone(self.trace('2')['spot'])
            self.job.input_manifest_json = {'engine': {'descriptor': {'evidence_adapter_id': ''}}}
            self.db.commit()
            with self.assertRaises(HTTPException):
                self.listing()

    def test_source_declaration_resolves_adapter_without_frozen_descriptor(self):
        with TemporaryDirectory() as tmp:
            self.masscube_fixture(Path(tmp))
            self.job.input_manifest_json = {}
            self.db.commit()
            self.assertEqual(self.trace('2')['reader'], 'masscube-indexed-v1')

    def test_invalid_empty_bounds_native_scores_and_spectrum_are_rejected(self):
        mutations = [lambda x: x['peaks'][0].update(rt=[], intensity=[]),
                     lambda x: x['ms2']['scores'].update(similarity='unknown'),
                     lambda x: x['ms2']['scores'].update(search_mode=3),
                     lambda x: x['ms2']['experimental'][0].update(intensity=float('nan')),
                     lambda x: x['peaks'][0].update(left=5, right=4),
                     lambda x: x['peaks'][0].update(gap_filled=True)]
        with TemporaryDirectory() as tmp:
            self.masscube_fixture(Path(tmp))
            baseline = copy.deepcopy(self.records)
            for mutate in mutations:
                with self.subTest(mutate=mutate):
                    self.records = copy.deepcopy(baseline)
                    mutate(self.records[0])
                    self.write_records()
                    with self.assertRaises(HTTPException):
                        self.trace('2')

    def test_final_bundle_binds_all_source_ids_before_filtering_statistics(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.masscube_fixture(root)
            final = root / 'AlignResult-test' / 'final'
            final.mkdir()
            audit = final / 'feature_audit_final.tsv'
            audit.write_text('feature_id\tsource_feature_id\tfeature_name\tmz\trt_min\teffective_decision_statistics\n'
                             '5\t5\tRetained\t105\t6\tinclude\n'
                             '0\t0\tExcluded\t100\t1\texclude\n'
                             '2\t2\tReview\t102\t3\treview\n')
            self.register(audit, 'metabolensr_feature_audit_final')
            samples = final / 'sample_metadata.tsv'
            samples.write_text((final.parent / 'preview' / 'sample_metadata.tsv').read_text())
            self.register(samples, 'metabolensr_sample_metadata')
            manifest = final / 'metabolensr_manifest.json'
            manifest.write_text(json.dumps({**self.manifest, 'bundle_type': 'final'}))
            final_file = self.register(manifest, 'metabolensr_manifest')
            args = (self.user, self.job.assay_id, self.job.id, final_file.id)
            self.assertEqual([row['feature_id'] for row in self.service.feature_map(*args)], ['5'])
            self.assertEqual(self.service.get_feature_trace(*args, '5')['reader'], 'masscube-indexed-v1')
            with self.assertRaises(HTTPException):
                self.service.get_feature_trace(*args, '0')

    def test_reference_spectrum_and_native_similarity_survive_without_msdial_scores(self):
        from app.schemas.workflow import WorkflowFeatureTraceOut
        with TemporaryDirectory() as tmp:
            self.masscube_fixture(Path(tmp))
            self.records[0]['ms2'].update(
                scores={'similarity': 0.87, 'search_mode': 'identity_search'},
                reference={'name': 'Native reference', 'inchikey': None, 'precursor_mz': None,
                           'library_filename': 'library.msp',
                           'peaks': [{'mz': 42.0, 'intensity': 100.0, 'matched': True}]})
            self.records[0]['ms2']['experimental'][0]['matched'] = True
            self.write_records()
            self.index['features']['2']['has_reference'] = True
            self.rewrite(self.index_file, lambda _: json.dumps(self.index))
            model = WorkflowFeatureTraceOut.model_validate(self.trace('2'))
            self.assertEqual(model.ms2.reference.name, 'Native reference')
            self.assertTrue(model.ms2.reference.peaks[0].matched)
            self.assertEqual(model.ms2.scores, {'similarity': 0.87, 'search_mode': 'identity_search'})
            self.assertIsNone(model.feature.msms_matched)
            self.assertIsNone(model.feature.low_score)

    def test_presence_flags_cannot_be_null_in_a_declared_index(self):
        with TemporaryDirectory() as tmp:
            self.masscube_fixture(Path(tmp))
            self.index['features']['2']['has_reference'] = None
            self.rewrite(self.index_file, lambda _: json.dumps(self.index))
            with self.assertRaises(HTTPException):
                self.listing()

    def test_native_feature_ids_identify_detected_acquisitions_only(self):
        with TemporaryDirectory() as tmp:
            self.masscube_fixture(Path(tmp))
            self.records[0]['peaks'][0]['native_feature_id'] = None
            self.write_records()
            with self.assertRaises(HTTPException):
                self.trace('2')
