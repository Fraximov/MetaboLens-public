"""Sparse, revision-checked operator decisions bound to an immutable R preview."""
from copy import deepcopy
from functools import lru_cache
import csv
import io
import os
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.core.errors import bad_request
from app.core.engine_contract import sha256_file
from app.models.models import DatasetFile, WorkflowFeatureReview, WorkflowJob, WorkflowRunArtifact, utcnow
from app.services.audit_service import AuditService
from app.services.provenance_service import ProvenanceService
from app.services.workflow_feature_service import WorkflowFeatureService, parse_audit_rows, read_registered_text, parse_boolean
from app.services.study_service import StudyService
from app.core.permissions import require_allowed


@lru_cache(maxsize=8)
def _preview_digest(file_id, checksum, uri, mtime_ns, ctime_ns, size):
    # Cache only the digest, never the opaque RDS contents or permission state.
    return sha256_file(Path(uri.removeprefix('file://')))


def conflict(message='Review changed in another session. Reload before saving.'):
    return HTTPException(status_code=409, detail=message)


class WorkflowFeatureReviewService:
    def __init__(self, db):
        self.db = db
        self.features = WorkflowFeatureService(db)

    def _context(self, user_id, assay_id, job_id, preview_file_id, *, write=False):
        # Serialize edits and continuation on the same job; revision comparison
        # still rejects clients whose displayed state predates this transaction.
        job = self.db.query(WorkflowJob).filter_by(id=job_id, assay_id=assay_id).with_for_update().first() if write else None
        bundle = self.features.load_bundle(user_id, assay_id, job_id, preview_file_id)
        job = job or bundle['job']
        selected = next((f for a, f in bundle['files'] if f.id == preview_file_id and
                         a.artifact_role == 'metabolensr_preview_manifest'), None)
        if selected is None:
            raise bad_request('Review must reference a preview manifest from this job')
        parent = Path(selected.storage_uri.removeprefix('file://')).parent
        bound = {str(selected.id): selected.checksum_sha256}
        rds = None
        for role in ('metabolensr_preview', 'metabolensr_feature_audit', 'metabolensr_sample_metadata'):
            matches = [f for a, f in bundle['files'] if a.artifact_role == role and
                       Path(f.storage_uri.removeprefix('file://')).parent == parent]
            if len(matches) != 1:
                raise bad_request(f'Review requires one registered {role}')
            file = matches[0]
            path = Path(file.storage_uri.removeprefix('file://'))
            if write:
                digest = sha256_file(path)
            elif role == 'metabolensr_preview':
                stat = path.stat()
                digest = _preview_digest(file.id, file.checksum_sha256, file.storage_uri,
                                         stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
            else:
                # The text reader already verifies these registered artifacts.
                read_registered_text(file)
                digest = file.checksum_sha256
            if not file.checksum_sha256 or digest != file.checksum_sha256:
                raise bad_request('Review preview checksum mismatch')
            bound[str(file.id)] = file.checksum_sha256
            if role == 'metabolensr_preview':
                rds = file
        review = self.db.query(WorkflowFeatureReview).filter_by(workflow_job_id=job_id, preview_file_id=preview_file_id).first()
        if review and review.preview_checksums_json != bound:
            raise conflict('The bound preview changed. This draft cannot be applied to a different preview.')
        if write:
            perms = StudyService(self.db).perms
            require_allowed(perms.can_operate_workflows(user_id, job.study_id), 'Only operator/admin can edit feature reviews')
            if not self._editable(job):
                raise bad_request('Feature decisions can be edited only at a completed feature-review checkpoint or failed finalization')
        return bundle, review, bound, rds

    @staticmethod
    def _editable(job):
        stage = (job.parameter_values_json or {}).get('workflow_stage')
        return (job.status == 'succeeded' and stage == 'qc_preview') or (
            job.status == 'failed' and bool((job.input_manifest_json or {}).get('feature_review')))

    def get(self, user_id, assay_id, job_id, preview_file_id, *, queue='include', domain='statistics', query=None,
            effective=None, has_ms2=None, page=1, page_size=25):
        if queue not in {'include', 'review', 'exclude', 'manual', 'all'} or domain not in {'statistics', 'annotation'}:
            raise bad_request('Invalid review queue or domain')
        if effective not in {None, '', 'include', 'review', 'exclude'}:
            raise bad_request('Invalid effective decision')
        bundle, review, _, _ = self._context(user_id, assay_id, job_id, preview_file_id)
        overrides = review.overrides_json if review else {}
        rows = {r['feature_id']: r for r in bundle['rows']}
        final_rows = None
        job = bundle['job']
        if job.status == 'succeeded' and (job.parameter_values_json or {}).get('workflow_stage') == 'full':
            preview_file = next(f for a, f in bundle['files'] if f.id == preview_file_id)
            final_dir = Path(preview_file.storage_uri.removeprefix('file://')).parent.parent / 'final'
            finals = [f for a, f in bundle['files'] if a.artifact_role == 'metabolensr_feature_audit_final' and
                      Path(f.storage_uri.removeprefix('file://')).parent == final_dir]
            if len(finals) != 1:
                raise bad_request('Completed feature review requires its registered final audit')
            final_rows = {r['feature_id']: r for r in parse_audit_rows(read_registered_text(finals[0]))}
            if final_rows.keys() != rows.keys():
                raise bad_request('Final audit feature identities differ from the bound preview')
        items = []
        tokens = (query or '').lower().split()
        for identity, source in rows.items():
            manual = overrides.get(identity, {})
            if final_rows is not None:
                final = final_rows[identity]
                manual = {key: value for key, value in final.items() if key.startswith('operator_override_') and value != 'automatic'}
            item = {'comment': manual.get('comment', ''), 'annotation_override_source': manual.get('annotation_override_source'),
                    'manually_changed': bool(manual)}
            for d in ('statistics', 'annotation'):
                auto = source.get(f'automatic_decision_{d}')
                value = manual.get(f'operator_override_{d}', 'automatic')
                item[f'automatic_{d}'] = auto
                item[f'override_{d}'] = value
                item[f'effective_{d}'] = (final_rows[identity].get(f'effective_decision_{d}') if final_rows is not None
                                          else auto if value == 'automatic' else value)
            if queue in {'include', 'review', 'exclude'} and item[f'automatic_{domain}'] != queue:
                continue
            if queue == 'manual' and not manual:
                continue
            if effective and item[f'effective_{domain}'] != effective:
                continue
            if has_ms2 is not None and parse_boolean(source.get('has_ms2')) is not has_ms2:
                continue
            if tokens:
                feature = self.features.feature_record(source)
                haystack = ' '.join(str(v) for v in [*source.values(),
                    *(k for k in source if k.startswith('curation_rule_') or k in
                      {'sample_detection_pct', 'qc_cv_pct', 'blank_to_sample_median_ratio', 'qc_detected_count'}),
                    feature['label'], item['comment'], item['effective_statistics'], item['effective_annotation']]).lower()
                if any(token not in haystack for token in tokens):
                    continue
            else:
                feature = None
            items.append((source, item, feature))
        page, page_size = max(1, int(page)), max(1, min(200, int(page_size)))
        total = len(items)
        page = min(page, max(1, (total + page_size - 1) // page_size))
        page_items = []
        for source, item, feature in items[(page-1)*page_size:page*page_size]:
            page_items.append({**(feature or self.features.feature_record(source)), **item,
                'qc': {k: v for k, v in source.items() if k.startswith('curation_rule_') or k in
                       {'sample_detection_pct', 'qc_cv_pct', 'blank_to_sample_median_ratio', 'qc_detected_count'}}})
        can_edit = self._editable(bundle['job']) and StudyService(self.db).perms.can_operate_workflows(user_id, bundle['job'].study_id)
        return dict(decision_source='final' if final_rows is not None else 'draft', preview_file_id=preview_file_id, revision=review.revision if review else 0,
                    frozen_revision=review.frozen_revision if review else None,
                    can_edit=bool(can_edit), can_continue=bool(can_edit and bundle['job'].status == 'succeeded'),
                    can_retry=bool(can_edit and bundle['job'].status == 'failed' and (bundle['job'].input_manifest_json or {}).get('feature_review')),
                    items=page_items, total=total, page=page, page_size=page_size)

    def patch(self, user_id, assay_id, job_id, preview_file_id, *, expected_revision, changes):
        bundle, review, bound, _ = self._context(user_id, assay_id, job_id, preview_file_id, write=True)
        revision = review.revision if review else 0
        if type(expected_revision) is not int or expected_revision != revision:
            raise conflict()
        if not isinstance(changes, list) or not changes or len(changes) > 200:
            raise bad_request('Supply between one and 200 feature changes')
        rows = {r['feature_id']: r for r in bundle['rows']}
        overrides = deepcopy(review.overrides_json if review else {})
        seen = set()
        now = utcnow().isoformat()
        for change in changes:
            if not isinstance(change, dict):
                raise bad_request('Invalid feature change')
            identity, domain, value = change.get('feature_id'), change.get('domain'), change.get('value')
            if not isinstance(identity, str) or identity not in rows or identity in seen:
                raise bad_request('Unknown or duplicate feature ID')
            if domain not in {'statistics', 'annotation'} or value not in {'automatic', 'include', 'exclude'}:
                raise bad_request('Invalid override domain or value')
            if any(rows[identity].get(f'automatic_decision_{d}') not in {'include','review','exclude'} for d in ('statistics','annotation')):
                raise bad_request('Feature lacks valid automatic decisions and cannot be overridden')
            seen.add(identity)
            manual = overrides.setdefault(identity, {})
            if domain == 'statistics' and change.get('copy_to_annotation'):
                if manual.get('annotation_override_source') == 'annotation' and manual.get('operator_override_annotation', 'automatic') != 'automatic' and not change.get('confirm_annotation_replace'):
                    raise conflict('Confirm replacement of the existing annotation-specific override.')
                manual['operator_override_annotation'] = value
                manual['annotation_override_source'] = 'statistics'
            if domain == 'annotation':
                manual['annotation_override_source'] = 'annotation'
            manual[f'operator_override_{domain}'] = value
            if 'comment' in change:
                if not isinstance(change['comment'], str) or len(change['comment']) > 4000:
                    raise bad_request('Comment must be text of at most 4000 characters')
                manual['comment'] = change['comment']
            manual.update(operator_identifier=str(user_id), updated_at=now)
            if all(manual.get(f'operator_override_{d}', 'automatic') == 'automatic' for d in ('statistics', 'annotation')) and not manual.get('comment'):
                overrides.pop(identity)
        if review:
            result = self.db.execute(update(WorkflowFeatureReview).where(
                WorkflowFeatureReview.id == review.id, WorkflowFeatureReview.revision == expected_revision).values(
                revision=revision+1, overrides_json=overrides, updated_at=utcnow()))
            if result.rowcount != 1:
                raise conflict()
        else:
            self.db.add(WorkflowFeatureReview(workflow_job_id=job_id, preview_file_id=preview_file_id,
                        preview_checksums_json=bound, revision=1, overrides_json=overrides))
        AuditService(self.db).log(user_id, 'workflow.feature_review_saved', 'workflow_job', str(job_id),
                                  {'preview_file_id': preview_file_id, 'revision': revision+1, 'changes': changes})
        ProvenanceService(self.db).log(bundle['job'].study_id, 'workflow.feature_review_saved', assay_id=assay_id,
            workflow_job_id=job_id, source_ref={'preview_file_id': preview_file_id, 'checksums': bound},
            output_ref={'revision': revision+1})
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise conflict() from exc
        return {'preview_file_id': preview_file_id, 'revision': revision+1}

    def freeze(self, user_id, assay_id, job_id, preview_file_id, expected_revision):
        """Flush the frozen registration; the caller commits it with requeue."""
        bundle, review, bound, rds = self._context(user_id, assay_id, job_id, preview_file_id, write=True)
        if expected_revision != (review.revision if review else 0):
            raise conflict()
        previews = [f for a, f in bundle['files'] if a.artifact_role == 'metabolensr_preview_manifest']
        if len(previews) != 1:
            raise bad_request('Reviewed continuation currently requires one aligned feature bundle per job')
        if review is None:
            review = WorkflowFeatureReview(workflow_job_id=job_id, preview_file_id=preview_file_id,
                       preview_checksums_json=bound, revision=0, overrides_json={})
            self.db.add(review)
        columns = ['feature_id', 'operator_override_statistics', 'operator_override_annotation', 'schema_version',
                   'workflow_job_id', 'preview_checksum', 'annotation_override_source', 'comment', 'operator_identifier', 'updated_at']
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=columns, delimiter='\t', lineterminator='\n')
        writer.writeheader()
        for identity, manual in sorted(review.overrides_json.items()):
            writer.writerow({**{key: manual.get(key, '') for key in columns}, 'feature_id': identity,
                'operator_override_statistics': manual.get('operator_override_statistics', 'automatic'),
                'operator_override_annotation': manual.get('operator_override_annotation', 'automatic'),
                'schema_version': '1.0', 'workflow_job_id': job_id, 'preview_checksum': rds.checksum_sha256})
        # The API sees run outputs read-only. The worker stages this registered
        # artifact into its writable run contract before local or SSH execution.
        directory = Path(get_settings().upload_storage_dir).resolve() / 'feature-reviews' / f'job_{job_id}'
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f'preview-{preview_file_id}-revision-{expected_revision}-{uuid4().hex}.tsv'
        with path.open('x', encoding='utf-8', newline='') as handle:
            handle.write(output.getvalue()); handle.flush(); os.fsync(handle.fileno())
        file = DatasetFile(created_by=user_id, dataset_id=rds.dataset_id, file_role='metabolensr_overrides', original_filename=path.name,
                           storage_uri=path.as_uri(), status='registered', file_size_bytes=path.stat().st_size,
                           checksum_sha256=sha256_file(path), media_type='text/tab-separated-values')
        self.db.add(file); self.db.flush()
        self.db.add(WorkflowRunArtifact(workflow_job_id=job_id, dataset_file_id=file.id, artifact_role='metabolensr_review_overrides'))
        review.frozen_revision, review.frozen_file_id = expected_revision, file.id
        result = {'preview_file_id': preview_file_id, 'preview_checksum': rds.checksum_sha256,
                  'revision': expected_revision, 'file_id': file.id, 'path': str(path), 'sha256': file.checksum_sha256}
        AuditService(self.db).log(user_id, 'workflow.feature_review_frozen', 'workflow_job', str(job_id), result)
        ProvenanceService(self.db).log(bundle['job'].study_id, 'workflow.feature_review_frozen', assay_id=assay_id,
                                     workflow_job_id=job_id, source_ref={'checksums': bound}, output_ref=result)
        self.db.flush()
        return result
