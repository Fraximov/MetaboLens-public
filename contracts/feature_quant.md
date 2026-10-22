# Contract: feature_quant.tsv

## Purpose

Long-form sample by feature abundance table.

## Format

Tab-separated UTF-8 text with a header row.

## Columns

| Column | Required | Description |
| --- | --- | --- |
| `run_id` | yes | Canonical run id. |
| `canonical_feature_id` | yes | Foreign key to `features.tsv`. |
| `sample_id` | yes | Sample identifier from StatPipeline matrix row metadata. |
| `raw_area` | no | Raw abundance value when available. |
| `normalized_area` | no | Selected normalized abundance value when available. |
| `log_area` | no | Reserved for future log-transformed abundance. |
| `qc_flag` | no | Reserved for future per-value QC flag. |

## Rules

- Every non-empty `canonical_feature_id` must exist in `features.tsv`.
- The exporter leaves unavailable values blank rather than inventing zeroes.
- Multiple rows for the same `canonical_feature_id` and `sample_id` should not be emitted by one exporter run.
