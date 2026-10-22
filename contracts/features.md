# Contract: features.tsv

## Purpose

One row per source MS-DIAL alignment feature.

## Format

Tab-separated UTF-8 text with a header row.

## Columns

| Column | Required | Description |
| --- | --- | --- |
| `run_id` | yes | Canonical run id. |
| `canonical_feature_id` | yes | Deterministic feature id, currently `msdial5_<source_feature_id>`. |
| `source_tool` | yes | Current value: `msdial5`. |
| `source_feature_id` | yes | Source MS-DIAL `Alignment ID`. |
| `mz` | no | Feature m/z from source metadata. |
| `rt` | no | Retention time from source metadata. |
| `polarity` | no | Inferred from adduct when possible: `positive` or `negative`. |
| `adduct` | no | Source adduct. |
| `charge` | no | Source charge. |
| `has_ms2` | no | Normalized boolean text: `true`, `false`, or blank. |
| `spectrum_id` | no | Source spectrum reference when available. |
| `source_row_json` | yes | Compact JSON object containing the source feature row. |

## Rules

- `canonical_feature_id` must be unique within one file.
- Rows should sort by numeric `source_feature_id` when possible.
- Future canonical files must reference features through `canonical_feature_id`.
