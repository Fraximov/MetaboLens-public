# Local scientific execution

Follow the [project setup](../README.md) before launching work. Prepare scientific
images with `python3 scripts/setup_scientific_images.py --build-metabolensr`
for a fresh clone, then recreate the application services when no job is running.
Without that flag, setup requires the unpublished MetaboLensR image pinned in
both engine descriptors. Setup also builds the other scientific images. It
writes an ignored setup record and pins new jobs to exact image identities.

## Prebuilt MetaboLensR

This source snapshot pins an unpublished local `linux/amd64` image. Its archive
is not included in this repository, and there is no public registry address yet.
If you have received the archive separately, follow these instructions.
Check the archive's SHA-256 against its accompanying `SHA256SUMS` before loading
it. From the archive directory:

```bash
shasum -a 256 -c SHA256SUMS
docker load --input metabolensr-0.0.0.9003-linux-amd64.tar
```

Then run setup from the source repository. It reuses the locally available image
without rebuilding MetaboLensR. The accompanying image manifest records the
archive checksum, package commit and image identity. Docker image-store formats
can expose different local IDs; an explicit immutable image override is available
with `--metabolensr-image`.

When a verified registry image is available, select its full immutable reference
with `--metabolensr-image REGISTRY/REPOSITORY@sha256:DIGEST`, replacing the example
with the actual reference. Setup pulls it only if unavailable locally, preserves
the requested digest and verifies platform, source revision, installed package
version and curation profiles before changing descriptors. Mutable tags are
rejected. Raw `sha256:` image IDs are local-only and are never pulled.

## Explicit source-build fallback

If the prebuilt image is unavailable, or you need to rebuild it:

```bash
python3 scripts/setup_scientific_images.py --build-metabolensr
```

This is an explicit choice, not an automatic response to a failed image download.
The source build may produce a different image identity; setup records that new
identity for subsequent jobs. `--package-source` selects an alternative package
source directory when building, and defaults to `third_party/MetaboLensR`.

The included MetaboLensR source is checked against its SOURCE.json inventory
before packaging. Scratch build contexts contain only explicitly selected files.
Scientific image builds do not require a private package
checkout, authenticated SIRIUS container or facility libraries. Upstream download
availability and license requirements still apply. Neither setup mode pushes an
image or changes an existing job's frozen image contract.

## Data and execution

Put your own permitted acquisitions in `runtime/raw_test/` and reference them
through `/raw_test`, or upload through the application. MS-DIAL also needs the
user-supplied libraries documented in the workflow libraries directory.

SIRIUS is disabled unless selected at launch. After image setup, run
`bash scripts/sirius-login-local.sh you@example.org`; enter your own password
interactively. The login helper requires a clean public-build image, uses the
installation's own `runtime/sirius-home/`, and sets directory permissions to 0700.
Never share that directory, commit an authenticated container, or include runtime
files in source archives.

Monitor runs in Jobs. Save feature-review decisions before finalization. Existing
jobs keep frozen source, images, parameters, input identities and review revisions;
changing release descriptors only affects new jobs.
