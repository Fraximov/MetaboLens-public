# Contract: feature_annotations.tsv

## Purpose

Future per-feature candidate annotations from providers such as SIRIUS or GNPS.

## Format

Tab-separated UTF-8 text with a header row. This file is header-only in the current implementation.

## Columns

| Column | Required | Description |
| --- | --- | --- |
| `run_id` | yes | Canonical run id. |
| `canonical_feature_id` | yes for data rows | Foreign key to `features.tsv`. |
| `annotation_job_id` | no | Future annotation job identifier. |
| `provider` | no | Annotation provider name. |
| `execution_backend` | no | Execution backend for the provider. |
| `annotation_type` | no | Annotation category. |
| `candidate_rank` | no | Candidate rank within provider output. |
| `name` | no | Candidate compound name. |
| `formula` | no | Candidate molecular formula. |
| `adduct` | no | Candidate adduct. |
| `smiles` | no | Candidate SMILES. |
| `inchi` | no | Candidate InChI. |
| `inchikey` | no | Candidate InChIKey. |
| `source_database` | no | Source database. |
| `score` | no | Provider score. |
| `confidence` | no | Provider confidence level. |
| `evidence_json` | no | Compact JSON evidence object. |
| `raw_record_json` | no | Compact JSON source record. |

## Rules

- Current exporter must write the header even when no annotations exist.
- Future provider rows must reference `features.tsv` through `canonical_feature_id`.
