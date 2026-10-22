# Third-party components

The MetaboLens application is distributed under the root MIT license.

- The embedded Nextflow workflow derives from Nextflow4MS-DIAL by Xinsong Du
  and Dominick Lemas. Its [upstream MIT license](https://github.com/Nextflow4Metabolomics/nextflow4ms-dial/blob/56c7694c88a9a52bea4957bc7b9742c41526975c/LICENSE)
  is retained at `workflows/nextflow4ms-dial-msdial5-clean/LICENSE`.
  The inherited [`conf/base.config` header](https://github.com/Nextflow4Metabolomics/nextflow4ms-dial/blob/56c7694c88a9a52bea4957bc7b9742c41526975c/conf/base.config#L1-L24)
  also contains conflicting GPLv3-or-later wording; both original notices are
  preserved. This ambiguity has not been resolved with the upstream authors.
  The application's MIT license does not override third-party terms.
- The selected MetaboLensR source is MIT licensed; its license, copyright and
  source-file checksums are retained in `third_party/MetaboLensR/`.
  The published MetaboLensR container also includes Debian, R, R packages and
  Quarto under their respective licenses. Their included notices remain in the
  image; the application's MIT license does not relicense the whole container.
- MetaboLights resources retain their Apache-2.0 license and pinned bundle
  metadata in `backend/app/resources/metabolights/`.
- Python, R, Rust, JavaScript and container dependencies retain their own
  licenses. Build recipes download those dependencies from their upstream
  distributions; the application license does not replace their terms.
- MS-DIAL, SIRIUS, Quarto, MassCube and ThermoRawFileParser are separate tools.
  Consult their upstream terms before redistributing their binaries or images.
  SIRIUS service access requires each user's own account and applicable license.

This source distribution contains no facility spectral libraries, real mzML/RAW
measurements, SIRIUS credentials, or preauthenticated container images. Add your
own permitted inputs and reference libraries locally; do not commit them.
