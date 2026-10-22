"""Read package-owned features; delegate source evidence to the engine reader."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from functools import lru_cache

from app.core.errors import bad_request, not_found
from app.models.models import Dataset, DatasetFile, WorkflowJob, WorkflowRunArtifact
from app.services.study_service import StudyService
from app.services.engine_evidence.registry import resolve
from app.services.workflow_eic_service import WorkflowEicService


@lru_cache(maxsize=8)
def _registered_text(file_id, checksum, uri, mtime_ns, ctime_ns, size):
    path = WorkflowEicService._local_path_from_storage_uri(uri)
    data = path.read_bytes()
    if not checksum or hashlib.sha256(data).hexdigest() != checksum:
        raise bad_request('Registered feature artifact checksum mismatch; register the updated output before reading it')
    return data.decode('utf-8-sig')


def read_registered_text(file):
    path = WorkflowEicService._local_path_from_storage_uri(file.storage_uri)
    try:
        stat = path.stat()
        return _registered_text(file.id, file.checksum_sha256, file.storage_uri,
                                stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
    except OSError as exc:
        raise not_found('Registered feature artifact is unavailable on disk') from exc


@lru_cache(maxsize=4)
def parse_audit_rows(text):
    reader = csv.DictReader(io.StringIO(text), delimiter='\t')
    fields = {'feature_id', 'source_feature_id', 'feature_name', 'mz', 'rt_min', 'adduct', 'has_ms2', 'sample_detection_pct', 'qc_cv_pct',
              'blank_to_sample_median_ratio', 'qc_detected_count'}
    rows = [{key: value for key, value in row.items()
             if key in fields or (key and key.startswith('curation_rule_')) or (key and ('decision_' in key or 'override_' in key))} for row in reader]
    seen = set()
    for row in rows:
        identity = row.get('feature_id')
        if not identity or identity in seen:
            raise bad_request('Package audit requires unique, nonempty feature IDs')
        seen.add(identity)
    return rows


def _number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def parse_boolean(value):
    return {'true': True, 'false': False}.get(str(value).lower())


class WorkflowFeatureService:
    def __init__(self, db):
        self.db = db
        self.eic = WorkflowEicService(db)

    def load_bundle(self, user_id, assay_id, job_id, preview_file_id):
        job = self.db.query(WorkflowJob).filter(
            WorkflowJob.id == job_id, WorkflowJob.assay_id == assay_id,
            WorkflowJob.deleted_at.is_(None)).first()
        if job is None:
            raise not_found('Workflow job not found for this assay')
        StudyService(self.db).get(user_id, job.study_id)
        files = self.db.query(WorkflowRunArtifact, DatasetFile).join(
            DatasetFile, DatasetFile.id == WorkflowRunArtifact.dataset_file_id).join(
            Dataset, Dataset.id == DatasetFile.dataset_id).filter(
            WorkflowRunArtifact.workflow_job_id == job_id,
            Dataset.assay_id == assay_id, Dataset.study_id == job.study_id).all()
        selected = next(((artifact, file) for artifact, file in files if file.id == preview_file_id and
                         artifact.artifact_role in {'metabolensr_preview', 'metabolensr_preview_manifest',
                                                    'metabolensr_manifest'}), None)
        if selected is None:
            raise not_found('Package feature bundle not found for this job')
        artifact, selected_file = selected
        kind = 'final' if artifact.artifact_role == 'metabolensr_manifest' else 'preview'
        directory = self.eic._local_path_from_storage_uri(selected_file.storage_uri).parent

        def member(role):
            matches = [file for artifact, file in files if artifact.artifact_role == role and
                       self.eic._local_path_from_storage_uri(file.storage_uri).parent == directory]
            if len(matches) != 1:
                raise bad_request(f'Package bundle requires exactly one registered {role}')
            return matches[0]

        try:
            manifest = json.loads(read_registered_text(member('metabolensr_manifest' if kind == 'final' else 'metabolensr_preview_manifest')))
        except ValueError as exc:
            raise bad_request('Invalid package feature manifest') from exc
        if manifest.get('schema_version') != '2.0' or manifest.get('bundle_type') != kind:
            raise bad_request('Unsupported package feature manifest')
        audit = member('metabolensr_feature_audit_final' if kind == 'final' else 'metabolensr_feature_audit')
        rows = parse_audit_rows(read_registered_text(audit))
        source_feature_ids = tuple(row.get('source_feature_id') for row in rows)
        if kind == 'final':
            if any(row.get('effective_decision_statistics') not in {'include', 'exclude', 'review'} for row in rows):
                raise bad_request('Final feature audit requires statistics decisions')
            rows = [row for row in rows if row['effective_decision_statistics'] == 'include']
        samples_file = member('metabolensr_sample_metadata')
        input_manifest = job.input_manifest_json or {}
        return dict(job=job, files=files, preview_file_id=preview_file_id,
                    manifest=manifest, input_manifest=input_manifest,
                    engine_descriptor=(input_manifest.get('engine') or {}).get('descriptor') or {},
                    source_feature_ids=source_feature_ids,
                    samples=list(csv.DictReader(io.StringIO(read_registered_text(samples_file)), delimiter='\t')),
                    source_tool=(manifest.get('source') or {}).get('tool'),
                    bundle_key=directory.parent.name, rows=rows,
                    samples_file=samples_file)

    def _features(self, bundle, *, enrich_evidence=True):
        # Evidence flags enrich the common package metadata; native names/mz/RT
        # never replace the package's display values.
        adapter = resolve(bundle, self.eic) if enrich_evidence else None
        evidence = adapter.features(bundle) if adapter else {}
        return [self.feature_record(row, evidence.get(row.get('source_feature_id'), {})) for row in bundle['rows']]

    def feature_record(self, row, native=None):
        native = native or {}
        source_id = row.get('source_feature_id')
        name = row.get('feature_name') or 'Unknown'
        feature = dict(feature_id=row['feature_id'], source_feature_id=source_id,
                       metabolite_name=name, average_mz=_number(row.get('mz')),
                       average_rt=_number(row.get('rt_min')), adduct_type=row.get('adduct'),
                       has_ms2=parse_boolean(row.get('has_ms2')),
                       annotated=self.eic._is_annotated_name(name),
                       msms_matched=native.get('msms_matched'),
                       has_reference=native.get('has_reference'),
                       low_score=native.get('low_score'),
                       decisions={key: value for key, value in row.items() if 'decision_' in key or 'override_' in key})
        identity = f"Source ID {source_id}" if source_id is not None else "Source ID unavailable"
        if source_id != row['feature_id']:
            identity += f" · Package ID {row['feature_id']}"
        feature['label'] = f"{identity} | {name} | m/z {feature['average_mz']} | RT {feature['average_rt']}"
        return feature

    def list_features(self, user_id, assay_id, job_id, preview_file_id, *, query=None, page=1, page_size=200):
        bundle = self.load_bundle(user_id, assay_id, job_id, preview_file_id)
        features = self._features(bundle)
        tokens = (query or '').lower().split()
        if tokens:
            features = [f for f in features if self.eic._feature_matches_search(f, tokens)]
        page, page_size = max(1, int(page)), min(200, max(1, int(page_size)))
        start = (page - 1) * page_size
        return dict(items=features[start:start + page_size], total=len(features), page=page, page_size=page_size)

    def feature_map(self, user_id, assay_id, job_id, preview_file_id, *, query=None):
        # One compact map fetch also supplies the slider's ordered opaque IDs.
        # Full audit rows remain paginated; no 40-request waterfall for an 8k run.
        features = self._features(self.load_bundle(user_id, assay_id, job_id, preview_file_id))
        tokens = (query or '').lower().split()
        return [f for f in features if self.eic._feature_matches_search(f, tokens)] if tokens else features

    def get_feature_trace(self, user_id, assay_id, job_id, preview_file_id, feature_id):
        bundle = self.load_bundle(user_id, assay_id, job_id, preview_file_id)
        row = next((row for row in bundle['rows'] if row['feature_id'] == feature_id), None)
        if row is None:
            raise not_found('Feature ID not found in this package bundle')
        adapter = resolve(bundle, self.eic)
        evidence = adapter.features(bundle) if adapter else {}
        feature = self.feature_record(row, evidence.get(row.get('source_feature_id')))
        payload = dict(preview_file_id=preview_file_id, feature=feature, spot=None, ms2=None,
                       reader=None, evidence_message='Chromatogram evidence is unavailable for this engine.')
        if adapter:
            payload.update(adapter.trace(bundle, feature['source_feature_id'], bundle['samples'], evidence_features=evidence))
        return payload
