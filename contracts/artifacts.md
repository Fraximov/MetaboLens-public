# Contract: artifacts.json

## Purpose

Machine-readable inventory of canonical files produced by one exporter run.

## Format

JSON object encoded as UTF-8.

## Required Fields

| Field | Type | Description |
| --- | --- | --- |
| `run_id` | string | Stable run id. |
| `assets` | array | Canonical file records. |

Each `assets` item contains:

| Field | Type | Description |
| --- | --- | --- |
| `asset_type` | string | File stem, such as `features` or `run_manifest`. |
| `uri` | string | `file://` URI for the canonical file. |
| `format` | string | `json` or `tsv`. |
| `checksum` | string | SHA-256 checksum of the file. |
| `created_by_step` | string | Current value: `canonicalize`. |

## Rules

- `artifacts.json` inventories the other canonical files and does not need to include itself.
- Every listed `uri` must point to a file produced by the same canonicalize step.
- `checksum` must be non-empty and calculated from file bytes.
