# MetaboLens–LabID–MetaboLights field map

**Status:** reference

The field map lives in one file: [`metabolights-labid-field-map.csv`](resources/metabolights-labid-field-map.csv). It has 157 entries for the untargeted LC–MS packaging path. Each entry says where MetaboLens gets an ISA/MAF value, how complete that mapping is, where the value should come from, and which ontology terms MetaboLights accepts for it. This page only explains how to read and maintain it; it holds no entries, so there is nothing to keep in step.

MetaboLights packaging is a prototype in this alpha (see the [README](../README.md)). The [pinned contract and policy versions](../backend/app/resources/metabolights/lock.json) define the audited baseline.

## Columns

| Column | Meaning |
| --- | --- |
| Map ID | Stable `MLISA-###` identifier; never reused or renumbered. |
| Field, Scope | The value and the area it belongs to (Study, Samples and acquisitions, LC-MS method, MAF annotations, …). |
| Minimum mapping readiness | `Covered`: the current mapping meets the minimum requirement; the study still needs correct values. `Gap …`: an applicable required mapping is incomplete. `Not required`: not needed for the minimum in this scope. |
| Typical example (illustrative) | Fictional human-plasma HILIC–Orbitrap values. Bracketed identifiers are placeholders, not evidence about a real study. |
| Current MetaboLens storage, Database source / JSON path | `table.column` is SQL storage; a path after a JSON column names JSON keys, and `[]` marks array entries. Generated, file-backed or unmapped values have no scientific-value column. |
| ISA destination | The ISA or MAF file and column written. |
| Current support | `Mapped`, `Partly mapped`, `Generated`, `Provenance only`, `Stored, not exported`, `Missing` or `Withdrawn`. |
| Capture source | Where the value should originate in practice: facility sheet, instrument file, workflow output, … |
| MetaboLights submission requirement | Required, conditional, recommended, optional or internal-only, with the condition from the pinned policy and current submission guidance. |
| Ontology rule (general MetaboLights studies) | What the pinned policy accepts in the row's ISA column for LC-MS studies outside MetabolomicsHub (the policy's "MS (MTBLS)" category): a fixed list, or any term from named ontologies, below a parent term or from any ontology. Then whether the rule is required or recommended, and an example: the listed term the row's illustrative value names, otherwise the policy's first. Empty when the ISA column has no term rule. Generated; do not edit by hand. |
| Ontology rule (MetabolomicsHub studies) | The same for studies shared under MetabolomicsHub (the policy's `ms-mhd-enabled` category), whose rules are stricter and mostly PSI-MS. "Same as general MetaboLights studies." when the rules match. Generated. |
| Code evidence | `path` or `path::Qualified.symbol`, separated by semicolons. Symbols name a class, function, class attribute or module-level name, never a line number. |

## What LabID should populate

PI/contact, publication and design data are JSON entries in `studies.metadata_json`; protocols use `protocols` and `assay_protocol_links`. They are not inferred from login users. Samples and their stable assay-specific annotations remain separate from instrument measurements. Each injection has an `acquisitions` record linked to the study sample and assay; `dataset_files.acquisition_id` links its registered file. Batch, injection order, vial, acquired_at and injection volume belong to the acquisition. New jobs freeze effective samplesheet rows at launch; matrix identities remain `file-<DatasetFile.id>`.

Acquisition methods come from the metadata submission selected at job launch through `workflow_jobs.input_manifest_json.metadata_submission_id`. Study metadata, sample metadata and protocols are read when a package is requested, then frozen in `study_packages.snapshot_json`. Final annotations and quantitative matrices stay in registered workflow files; the database holds ownership, locations and checksums, not one row per metabolite.

Import through application services or the API, so permissions, validation, revisions and audit events stay intact. The map documents storage; it is not a direct-SQL write contract.

## Maintaining the map

Edit the CSV in the same change as the code it describes. Keep Map IDs, and add new entries at the next free number. [`test_field_map.py`](../backend/tests/test_field_map.py) runs with the backend suite. It checks the columns and unique IDs, and that every code reference names an existing file and symbol, so a rename or removal fails until the map is corrected. Mapping text is still checked by review, not by the test.

The two ontology-rule columns come from the pinned policy bundle. After editing the CSV or updating the bundle, run `python3 scripts/update_field_map_ontology_rules.py` from the repository root; `--check` reports whether they are current without changing anything. The test fails until they match the bundle.
