# MetaboLens scientific workflows

The application launches `main_metabolensr.nf` (MS-DIAL 5) or `main_masscube.nf` (MassCube 1.2.22). The worker freezes the selected source, inputs, parameters and container identities into each run. Both engines produce MetaboLensR preview/final bundles; optional review pauses resume the same frozen run. Thermo RAW files are converted to mzML before processing.

Use Nextflow 25.10.4 and Docker for local execution. See the [application runbook](../../docs/NEXTFLOW_LOCAL_RUNBOOK.md) for startup and the [architecture](../../docs/CURRENT_WORKFLOW_ARCHITECTURE.md) for the worker boundary.

## Prepare scientific images

From the repository root:

```sh
python3 scripts/setup_scientific_images.py
```

The command pulls the public MetaboLensR 0.0.0.9003 `linux/amd64` image by the digest pinned in both engine descriptors, without a registry login or package build. It still builds the remaining clean images with unique `metabolens-public-*` tags and pulls the pinned public ThermoRawFileParser image. It verifies the package revision, version, platform and catalog before updating descriptors and writes an ignored setup record under `runtime/image-setup/`. Only explicit source files enter temporary build contexts. Nothing is pushed to a registry. Manual launches must supply descriptor image identities; default scientific image names use a separate public-only `:unconfigured` placeholder and never select an existing private image.

Use `python3 scripts/setup_scientific_images.py --build-metabolensr` to build the checksummed source in `third_party/MetaboLensR` explicitly instead. `--metabolensr-image` accepts an explicit immutable image reference; mutable tags and silent source-build fallback are prohibited. The exact public registry pin, archive verification and fallback instructions are in the [local runbook](../../docs/NEXTFLOW_LOCAL_RUNBOOK.md).

Requested prebuilt registry digests are retained exactly. For source builds, Docker's repository digest is recorded when available, otherwise its full local `sha256:<image ID>`. A local ID requires that image in the current Docker daemon; another host must load the archive or explicitly build from source. Existing queued/running jobs keep their original descriptors. Recreate application containers after setup, while no local job is running, so catalog seeding uses the updated descriptors. Later rebuilds can produce different identities because operating-system repositories are not frozen snapshots; setup records identify the exact result.

MS-DIAL requires your own permitted and scientifically appropriate `libraries/ms1.txt`, `libraries/ms2.msp` and `libraries/internal-standards.txt`. No facility reference data or private measurements are distributed. See [library setup](libraries/README.md). MassCube does not require an annotation library; optional MSP annotation uses a user-supplied library.

## Optional SIRIUS

The clean SIRIUS 6.3.4 image contains the CLI only. After image setup, authenticate with your own account:

```sh
scripts/sirius-login-local.sh scientist@example.org
```

Login selects the image pinned by the current MS-DIAL descriptor and requires its clean-build label. Credentials remain under ignored `runtime/sirius-home`, mounted only at runtime. Do not copy that folder into source, commit a running SIRIUS container, or distribute an authenticated image. SIRIUS service access may depend on your own account and license.

The workflow passes `sirius_ms1_tolerance` and `sirius_ms2_tolerance`, in ppm, to the formula step as `--ppm-max` and `--ppm-max-ms2`. Database and workspace strings remain literal shell arguments. The option names are documented in the [official SIRIUS CLI guide](https://v6.docs.sirius-ms.io/cli/).

## Validation without study data

From this workflow folder, configuration inspection executes no scientific task:

```sh
nextflow config main_metabolensr.nf -profile docker
nextflow config main_masscube.nf -profile docker
```

From the repository root, the synthetic SIRIUS regression uses the real Nextflow task renderer and a fake CLI, with no container, account or measurements:

```sh
python3 -m unittest discover -s worker/tests -p test_sirius_execution.py -v
```

The `docker` profile is the local application target. `embl_slurm` retains its API name but needs your own SLURM/SSH/shared-storage configuration; the generic `singularity` profile also needs site-specific setup. Removed private fixture profiles are not supported.
