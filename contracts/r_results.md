# Contract: r_results.tsv

## Purpose

Long-form feature-level statistical results from modular StatPipeline v2.

## Format

Tab-separated UTF-8 text with a header row. Header-only output is valid when statistics did not run or no recognized result table was found.

## Columns

| Column | Required | Description |
| --- | --- | --- |
| `run_id` | yes | Canonical run id. |
| `canonical_feature_id` | yes for data rows | Foreign key to `features.tsv`. |
| `result_name` | yes for data rows | Source result table stem. |
| `normalization_method` | no | Normalization method inferred from the StatPipeline result path, such as `Raw`, `TIC`, or `BMIS`. |
| `contrast` | no | Statistical contrast, or source result directory name when not present in the table. |
| `statistic` | no | Test statistic. |
| `p_value` | no | Raw p-value. |
| `q_value` | no | Adjusted p-value or FDR value. |
| `log2_fc` | no | Log2 fold change when available. |
| `extra_json` | no | Compact JSON for result columns not mapped into canonical fields. |

## Rules

- Every data-row `canonical_feature_id` must exist in `features.tsv`.
- Recognized input tables are `statistical_results_*.txt`, `statistical_results_*.tsv`, and `statistical_results_*.xlsx` under StatPipeline output roots.
- XLSX inputs are read from the first worksheet only. This matches the single-sheet files written by the current R `writexl` output path.
- `normalization_method` strips numeric folder prefixes such as `01_Raw` to `Raw`.
- Unknown source columns are preserved in `extra_json`.
