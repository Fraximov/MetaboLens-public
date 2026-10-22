"""Read one indexed MassCube record after verifying its registered provenance."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType

from app.core.errors import bad_request, not_found
from app.services.workflow_eic_service import WorkflowEicService


ADAPTER_ID = 'masscube-indexed-v1'


def _require(condition, message):
    if not condition:
        raise bad_request(f'Invalid MassCube evidence: {message}')


def _identity(value):
    return isinstance(value, str) and bool(value.strip())


def _number(value, *, nullable=False, positive=False):
    return ((value is None and nullable) or
            (type(value) in (int, float) and math.isfinite(value) and (value > 0 if positive else value >= 0)))


def _signature(file):
    path = WorkflowEicService._local_path_from_storage_uri(file.storage_uri)
    try:
        stat = path.stat()
    except OSError as exc:
        raise not_found('Registered MassCube evidence is unavailable on disk') from exc
    _require(isinstance(file.checksum_sha256, str) and
             re.fullmatch(r'[a-fA-F0-9]{64}', file.checksum_sha256), 'registered SHA256 is missing')
    return (str(path), file.checksum_sha256.lower(), stat.st_mtime_ns, stat.st_ctime_ns,
            stat.st_size, stat.st_ino, stat.st_dev)


def _unchanged(handle, signature):
    import os
    stat = os.fstat(handle.fileno())
    _require((stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino, stat.st_dev) == signature[2:],
             'registered artifact changed while reading')


@lru_cache(maxsize=16)
def _verify(signature):
    # Cache only verified stat/checksum identities, never decoded traces or file bytes.
    digest = hashlib.sha256()
    with open(signature[0], 'rb') as handle:
        _unchanged(handle, signature)
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
        _unchanged(handle, signature)
    _require(digest.hexdigest() == signature[1], 'registered artifact checksum mismatch')


@lru_cache(maxsize=16)
def _native_bounds(signature):
    """Keep only native IDs and integration limits, not the large peak-shape columns."""
    _verify(signature)
    bounds = {}
    with open(signature[0], newline='', encoding='utf-8-sig') as handle:
        _unchanged(handle, signature)
        try:
            rows = csv.DictReader(handle, delimiter='\t')
            _require({'feature_ID', 'RT', 'RT_start', 'RT_end'} <= set(rows.fieldnames or []),
                     'native integration columns are missing')
            for row in rows:
                identity = row['feature_ID']
                _require(_identity(identity) and identity not in bounds, 'ambiguous native feature ID')
                left, apex, right = (float(row[key]) for key in ('RT_start', 'RT', 'RT_end'))
                _require(all(_number(value) for value in (left, apex, right)) and left <= apex <= right,
                         'invalid native integration limits')
                bounds[identity] = (left, apex, right)
        except (ValueError, TypeError, csv.Error) as exc:
            raise bad_request('Invalid MassCube native integration table') from exc
        _unchanged(handle, signature)
    return MappingProxyType(bounds)


def _detected_bounds(bundle, data_signature, source_column, native_id, top):
    directory = Path(data_signature[0]).parent / 'single_files'
    matches = [file for artifact, file in bundle['files']
               if artifact.artifact_role == 'masscube_single_file'
               and (path := WorkflowEicService._local_path_from_storage_uri(file.storage_uri)).parent == directory
               and path.name == f'{source_column}.txt']
    if not matches:
        return None, None
    _require(len(matches) == 1, 'ambiguous native integration table')
    try:
        bounds = _native_bounds(_signature(matches[0])).get(native_id)
    except OSError as exc:
        raise not_found('Registered MassCube integration table is unavailable on disk') from exc
    _require(bounds is not None, 'detected feature is absent from native integration table')
    left, apex, right = bounds
    # MassCube exports RT and rt_seq endpoints rounded to 0.001 minute.
    _require(top is None or round(top, 3) == apex, 'native integration apex disagrees with evidence')
    return left, right


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, 'duplicate JSON object key')
        result[key] = value
    return result


def _json(data):
    try:
        result = json.loads(data, object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError) as exc:
        raise bad_request('Invalid MassCube evidence JSON') from exc
    _require(isinstance(result, dict), 'JSON object required')
    return result


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


@lru_cache(maxsize=8)
def _index(index_signature, data_signature):
    _verify(index_signature)
    # The catalogue needs only the verified index and its registered data
    # binding. Scan the large trace file only when a trace is requested.
    with open(index_signature[0], 'rb') as handle:
        _unchanged(handle, index_signature)
        value = _json(handle.read())
        _unchanged(handle, index_signature)
    _require(value.get('schema_version') == '1.0' and value.get('adapter_id') == ADAPTER_ID,
             'unsupported schema or adapter')
    _require(_identity(value.get('bundle_id')), 'bundle identity is required')
    _require(value.get('engine') == 'masscube' and value.get('retention_time_unit') == 'minute',
             'engine and retention time unit must be masscube/minute')
    _require(value.get('data_file') == 'evidence.jsonl' and
             value.get('data_sha256') == data_signature[1], 'index/data binding mismatch')
    for key in ('aligned_table_md5', 'acquisition_evidence_md5'):
        _require(isinstance(value.get(key), str) and re.fullmatch(r'[a-f0-9]{32}', value[key]),
                 f'{key} is required')
    acquisitions = value.get('acquisitions')
    _require(isinstance(acquisitions, list) and acquisitions, 'acquisitions are required')
    for key in ('acquisition_id', 'source_column'):
        ids = [row.get(key) for row in acquisitions if isinstance(row, dict)]
        _require(len(ids) == len(acquisitions) and all(_identity(identity) for identity in ids)
                 and len(set(ids)) == len(ids), f'{key} must be unique and nonempty')
    for row in acquisitions:
        _require(row.get('file_asset_id') is None or _identity(row['file_asset_id']), 'invalid file asset ID')
    features = value.get('features')
    _require(isinstance(features, dict) and all(_identity(key) for key in features), 'invalid source feature IDs')
    spans = []
    for entry in features.values():
        _require(isinstance(entry, dict), 'invalid feature index entry')
        offset, length = entry.get('offset'), entry.get('length')
        _require(type(offset) is int and type(length) is int and offset >= 0 and length > 0 and
                 offset + length <= data_signature[4], 'record offset/length is out of bounds')
        for key in ('has_ms2', 'has_reference'):
            _require(type(entry.get(key)) is bool, 'invalid evidence presence flag')
        for key in ('msms_matched', 'low_score'):
            _require(key in entry and (type(entry[key]) is bool or entry[key] is None), 'invalid evidence match flag')
        spans.append((offset, offset + length))
    cursor = 0
    for start, end in sorted(spans):
        _require(start == cursor, 'index records overlap or leave unindexed data')
        cursor = end
    _require(cursor == data_signature[4], 'index does not cover the data file')
    return _freeze(value)


def _samples(index, samples):
    mapping = {}
    columns = set()
    for sample in samples:
        identity = sample.get('acquisition_id')
        column = sample.get('engine_source_column') or sample.get('source_column')
        _require(_identity(identity) and identity not in mapping and _identity(column) and column not in columns,
                 'package acquisition mapping is missing or ambiguous')
        mapping[identity] = {**sample, 'source_column': column}
        columns.add(column)
    _require(set(mapping) == {row['acquisition_id'] for row in index['acquisitions']},
             'index must contain every package acquisition exactly once')
    for row in index['acquisitions']:
        sample = mapping[row['acquisition_id']]
        _require(row['source_column'] == sample['source_column'] and
                 (row.get('file_asset_id') or None) == (sample.get('file_asset_id') or None),
                 'native acquisition identity disagrees with package')
    return mapping


def _validate_spectrum(peaks):
    _require(isinstance(peaks, list), 'MS2 spectrum must be an array')
    for peak in peaks:
        _require(isinstance(peak, dict) and _number(peak.get('mz'), positive=True) and
                 _number(peak.get('intensity')) and type(peak.get('matched')) is bool,
                 'invalid MS2 peak')


def _validate_ms2(ms2):
    _require(isinstance(ms2, dict) and ms2.get('provider') == 'MassCube' and
             type(ms2.get('available')) is bool, 'invalid MS2 provider/availability')
    _require(_number(ms2.get('match_tolerance_da'), positive=True), 'invalid MS2 match tolerance')
    _validate_spectrum(ms2.get('experimental'))
    reference = ms2.get('reference')
    if reference is not None:
        _require(isinstance(reference, dict), 'invalid reference spectrum')
        _validate_spectrum(reference.get('peaks'))
        _require(_number(reference.get('precursor_mz'), nullable=True, positive=True), 'invalid reference mass')
    for key in ('reference_rt', 'reference_mz'):
        _require(_number(ms2.get(key), nullable=True), f'invalid {key}')
    scores = ms2.get('scores')
    _require(isinstance(scores, dict), 'invalid native scores')
    _require('similarity' in scores and _number(scores['similarity'], nullable=True), 'invalid native similarity score')
    _require('search_mode' in scores and (scores['search_mode'] is None or isinstance(scores['search_mode'], str)),
             'invalid native search mode')
    for key, score in scores.items():
        _require(score is None or isinstance(score, str) or
                 (type(score) in (int, float) and math.isfinite(score)), f'invalid native {key} score')


class MassCubeEvidence:
    def __init__(self, eic):
        pass

    def _context(self, bundle):
        def member(role):
            matches = [file for artifact, file in bundle['files'] if artifact.artifact_role == role]
            _require(len(matches) == 1, f'exactly one registered {role} is required')
            return matches[0]

        index_file, data_file = member('masscube_evidence_index'), member('masscube_evidence_data')
        index_signature, data_signature = _signature(index_file), _signature(data_file)
        try:
            index = _index(index_signature, data_signature)
        except OSError as exc:
            raise not_found('Registered MassCube evidence is unavailable on disk') from exc
        manifest = bundle['manifest']
        _require(index['bundle_id'] == bundle['bundle_key'], 'native index belongs to a different bundle')
        _require(manifest['source'].get('tool') == 'masscube', 'package source must be masscube')
        _require(index['aligned_table_md5'] == ((manifest.get('inputs') or {}).get('source') or {}).get('md5'),
                 'aligned input hash disagrees with package manifest')
        _require(index['acquisition_evidence_md5'] == (manifest['source'].get('acquisition_evidence') or {}).get('md5'),
                 'acquisition evidence hash disagrees with package manifest')
        identities = bundle['source_feature_ids']
        _require(all(_identity(identity) for identity in identities) and len(set(identities)) == len(identities)
                 and set(identities) == set(index['features']), 'source feature IDs disagree with package')
        samples = _samples(index, bundle['samples'])
        return index, data_signature, samples

    def features(self, bundle):
        index, _, _ = self._context(bundle)
        return index['features']

    def trace(self, bundle, source_feature_id, samples, *, evidence_features):
        index, data_signature, mapping = self._context(bundle)
        entry = index['features'].get(source_feature_id)
        _require(entry is not None, 'source feature ID is absent from index')
        try:
            _verify(data_signature)
            with open(data_signature[0], 'rb') as handle:
                _unchanged(handle, data_signature)
                handle.seek(entry['offset'])
                data = handle.read(entry['length'])
                _unchanged(handle, data_signature)
        except OSError as exc:
            raise not_found('Registered MassCube evidence is unavailable on disk') from exc
        _require(len(data) == entry['length'] and data.endswith(b'\n') and data.count(b'\n') == 1,
                 'indexed record must be one complete JSONL line')
        record = _json(data)
        _require(record.get('schema_version') == '1.0' and record.get('source_feature_id') == source_feature_id,
                 'record schema or source identity mismatch')
        _require(_number(record.get('mz'), positive=True) and _number(record.get('rt_min')), 'invalid feature mass/time')
        peaks = record.get('peaks')
        _require(isinstance(peaks, list), 'peaks must be an array')
        ids = [peak.get('acquisition_id') for peak in peaks if isinstance(peak, dict)]
        _require(len(ids) == len(peaks) and all(_identity(identity) for identity in ids) and
                 len(set(ids)) == len(ids) and set(ids) == set(mapping), 'record acquisitions are incomplete or ambiguous')
        result = []
        for file_id, peak in enumerate(peaks):
            _require(peak.get('native_feature_id') is None or _identity(peak['native_feature_id']), 'invalid native feature ID')
            _require(type(peak.get('detected')) is bool and type(peak.get('gap_filled')) is bool and
                     not (peak['detected'] and peak['gap_filled']), 'invalid native detection flags')
            _require((peak.get('native_feature_id') is not None) == peak['detected'],
                     'native feature IDs must identify detected acquisitions only')
            rt, intensity = peak.get('rt'), peak.get('intensity')
            _require(isinstance(rt, list) and isinstance(intensity, list) and len(rt) == len(intensity),
                     'trace arrays must have equal lengths')
            _require(all(_number(value) for value in rt + intensity) and
                     all(a <= b for a, b in zip(rt, rt[1:])), 'trace values must be finite with monotone retention time')
            for bound in ('top', 'left', 'right'):
                _require(bound in peak and _number(peak[bound], nullable=True), 'invalid peak bounds')
            left, top, right = peak['left'], peak['top'], peak['right']
            _require(left is None or right is None or left <= right, 'reversed peak bounds')
            _require(top is None or ((left is None or left <= top) and (right is None or top <= right)), 'apex outside peak bounds')
            _require(bool(rt) or (left is None and top is None and right is None), 'empty traces cannot declare peak bounds')
            selected = range(len(rt)) if len(rt) <= 900 else [i * (len(rt) - 1) // 899 for i in range(900)]
            points = [{'x': rt[i], 'intensity': intensity[i]} for i in selected]
            sample = mapping[peak['acquisition_id']]
            if rt and peak['detected'] and left is None and right is None:
                left, right = _detected_bounds(bundle, data_signature, sample['source_column'],
                                               peak['native_feature_id'], top)
            result.append(dict(file_id=file_id, acquisition_id=peak['acquisition_id'],
                               native_feature_id=peak.get('native_feature_id'), file_asset_id=sample.get('file_asset_id'),
                               sample_name=sample.get('sample_name') or sample['source_column'], source_column=sample['source_column'],
                               detected=peak['detected'], gap_filled=peak['gap_filled'], top=top, left=left, right=right,
                               point_count=len(rt), returned_point_count=len(points), points=points))
        ms2 = record.get('ms2')
        _validate_ms2(ms2)
        _require(entry['has_ms2'] == bool(ms2['experimental']) and entry['has_reference'] == bool(ms2['reference']),
                 'record spectrum disagrees with index flags')
        return {'reader': ADAPTER_ID, 'evidence_message': None, 'ms2': ms2,
                'spot': {'version': '1.0', 'feature_count': len(index['features']),
                         'center': {'rt': record['rt_min'], 'mz': record['mz']},
                         'trace_count': len(result), 'peaks': result}}
