# Workflow agent guide

Read this folder's [README](README.md), the root [workflow architecture](../../docs/CURRENT_WORKFLOW_ARCHITECTURE.md) and [local runbook](../../docs/NEXTFLOW_LOCAL_RUNBOOK.md).

The application is the control plane. The worker freezes inputs, source, parameters, image identities and review decisions. Nextflow executes those contracts without querying the database or calling the API. Keep runs assay-scoped and preserve acquisition identities (`file-<DatasetFile.id>`).

Current application entrypoints are `main_metabolensr.nf` for MS-DIAL and `main_masscube.nf` for MassCube. Both use package-owned preview/finalization. Scientific configuration must exclude review-stage and override paths so continuation reuses cached processing. SIRIUS remains an optional, separately registered annotation branch.

RAW input preparation converts Thermo RAW to mzML. Copy staged inputs; do not introduce links to application uploads. Keep engine-specific rendering in worker adapters and scheduler handling generic. MassCube uses peak heights with native normalization, merging and RT correction disabled; preserve native evidence and acquisition IDs.

Keep library data, measurements, runtime files, private paths and credentials out of source. Build scientific images with `scripts/setup_scientific_images.py` from the repository root. Build contexts must contain only explicitly selected source files. Never bake SIRIUS login state into an image or export an authenticated image. Login uses the user's ignored `runtime/sirius-home` only.

Shell-quote every interpolated task value. Add a synthetic regression check for changed task arguments or staging; never use a user's study as a unit-test fixture. Validate configuration with `nextflow config main_metabolensr.nf -profile docker` and `nextflow config main_masscube.nf -profile docker`. Test app-to-workflow changes in the backend and worker suites. Keep the owning public documentation current.
