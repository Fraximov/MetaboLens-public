# System overview

PostgreSQL stores studies, assays, samples, acquisitions, files, workflow jobs,
metadata revisions, audit events and provenance. FastAPI owns permissions and
business rules. Dash is an API client. One polling worker owns long-running work.

Large scientific files remain on disk; database records hold their locations,
size and checksums. Registration resolves permitted roots and rejects escapes.
New uploads and path imports record a checksum. Launch freezes the selected
acquisition identities and scientific configuration. The worker verifies staged
bytes against that frozen identity and verifies preserved inputs on continuation.
Older records without a launch checksum are explicitly marked unverified at that
boundary, rather than represented as verified historical data.

MS-DIAL and MassCube have separate versioned engine descriptors and native
processing/evidence adapters. They share MetaboLensR quality-control preview,
feature decisions, finalization and reporting. Operator review revisions are
frozen before continuation. Workflows remain assay-scoped; reinjections keep
independent acquisition identities.

SIRIUS is an optional, separate MS-DIAL annotation branch. User authentication
state is mounted only from the installation's ignored runtime home; it is never
a source resource or image-build input.

The source distribution supports local Docker with one worker. Cluster adapters
need site-specific configuration. MetaboLights study packaging is a prototype
with explicit validation findings. Unit tests do not establish scientific
acceptance for a new instrument, assay or reference library.
