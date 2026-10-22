# Contract: run_manifest.json

## Purpose

Run-level manifest for one canonical export.

## Format

JSON object encoded as UTF-8.

## Required Fields

| Field | Type | Description |
| --- | --- | --- |
| `run_id` | string | Stable run id. Worker launches use `WorkflowJob.run_name`. |
| `pipeline_name` | string | Current value: `msdial5-nextflow`. |
| `pipeline_version` | string | Pipeline version or workflow contract version available to the exporter. |
| `preprocessing_provider` | string | Current value: `MS-DIAL 5`. |
| `params_hash` | string | SHA-256 checksum of `params.json`, or SHA-256 of empty bytes when unavailable. |
| `params_json` | object | `nextflow_params` from the worker contract. |
| `input_assets` | array | Selected input file records from the worker contract. |
| `output_assets` | array | Canonical output filenames produced by the exporter. |
| `created_at` | string | UTC ISO-8601 timestamp for the export. |

## Rules

- `run_id` must match the `run_id` column in all canonical TSV files.
- `params_hash` must be non-empty.
- `output_assets` must include the produced canonical TSV filenames.
