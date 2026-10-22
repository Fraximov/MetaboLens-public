# Contract: network_edges.tsv

## Purpose

Future molecular-network or feature-network edges.

## Format

Tab-separated UTF-8 text with a header row. This file is header-only in the current implementation.

## Columns

| Column | Required | Description |
| --- | --- | --- |
| `run_id` | yes | Canonical run id. |
| `annotation_job_id` | no | Future annotation or network job identifier. |
| `provider` | no | Network provider name. |
| `source_canonical_feature_id` | yes for data rows | Source feature id, foreign key to `features.tsv`. |
| `target_canonical_feature_id` | yes for data rows | Target feature id, foreign key to `features.tsv`. |
| `score` | no | Edge score. |
| `matched_peaks` | no | Matched peak count when available. |
| `delta_mz` | no | Delta m/z when available. |
| `component_id` | no | Network component identifier. |
| `evidence_json` | no | Compact JSON evidence object. |

## Rules

- Current exporter must write the header even when no network edges exist.
- Future data rows must reference `features.tsv` through both feature id columns.
