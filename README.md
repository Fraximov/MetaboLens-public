# MetaboLens

MetaboLens is a research alpha for managing metabolomics studies and running
assay-scoped MS-DIAL and MassCube workflows with MetaboLensR quality control,
review and normalization. FastAPI/PostgreSQL store application records, Dash
provides the UI, and one worker runs Nextflow.

## Start locally

Install Docker with Compose and Python 3.10 or newer. Scientific task images use
Linux amd64; Docker Desktop on Apple Silicon needs amd64 emulation.
Run these commands from this directory:

```bash
python3 scripts/configure_local.py --email you@example.org
docker compose --env-file .env -f infra/compose/docker-compose.yml up --build
```

Open <http://127.0.0.1:18050>. Sign in with your chosen email and the generated
`BOOTSTRAP_ADMIN_PASSWORD` in the private `.env` file. The setup command does not
print secrets and refuses to overwrite an existing configuration. The API is at
<http://127.0.0.1:18000/docs>. PostgreSQL has no published host port. This Compose
project uses its own `metabolens-public` images and volumes.

The first startup creates the administrator. Later startups do not reset an
existing user's password. No shared demo password or test-login button is enabled.
Keep `.env` and `runtime/` private. A copy of this folder used for deployment must
not be redistributed as a source release.

## Prepare scientific execution

Prepare scientific images before launching workflows. Setup downloads the public
MetaboLensR 0.0.0.9003 image for Linux amd64 by its pinned digest; no registry login
or MetaboLensR build is required. Setup still builds the other scientific images
from selected source inputs. Those builds download substantial dependencies and
can take time:

```bash
python3 scripts/setup_scientific_images.py
```

To build MetaboLensR from the included checksummed source instead:

```bash
python3 scripts/setup_scientific_images.py --build-metabolensr
```

Setup verifies the MetaboLensR platform, source revision, version and profile
catalog before updating either engine descriptor. An unavailable prebuilt image
fails with instructions; it never silently builds a replacement. See the
[local runbook](docs/NEXTFLOW_LOCAL_RUNBOOK.md) for archive loading and selecting
a registry digest. Setup never pushes images. Recreate application containers
after changing descriptors:

```bash
docker compose --env-file .env -f infra/compose/docker-compose.yml up --build -d
```

Stop or finish active workflows before recreating a worker. Existing job contracts
retain their original images and source. Local image IDs are for local Docker;
cluster deployments need their own accessible immutable image references.

Provide your own permitted RAW/mzML data in `runtime/raw_test/` and browse
`/raw_test` from Files, or upload through the app. Create a study and assay,
register samples/acquisitions, provide metadata, approve the study and launch
from Workflows. Monitor and review runs in Jobs.

MS-DIAL requires assay-appropriate files described in the
[local reference-library guide](workflows/nextflow4ms-dial-msdial5-clean/libraries/README.md).
No facility libraries or real measurements are included. MassCube can run without
an annotation library. The supplied parameter templates are starting points;
review their scientific settings for your assay.

SIRIUS is optional. After building the clean images, authenticate with your own
account using its interactive password prompt:

```bash
bash scripts/sirius-login-local.sh you@example.org
```

Login state stays under ignored `runtime/sirius-home/`. Do not copy it into an
image or source archive. See [security and credential handling](SECURITY.md).

## Scope and development

This alpha supports one worker and local Docker execution. HPC/SLURM adapters are
retained as advanced code, without personal cluster settings or a supported
turnkey HPC deployment. MetaboLights packaging remains a prototype; review its
validation findings before any submission. This is not a validated clinical tool.

See the [documentation index](docs/README.md), [development checks](docs/DEVELOPMENT.md)
and [system overview](docs/architecture/system-overview.md).
The application uses the [MIT license](LICENSE); bundled components retain
[their own notices](THIRD_PARTY_NOTICES.md).
