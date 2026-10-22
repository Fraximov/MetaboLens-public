# Changelog

## Initial public alpha

- Prepared standalone source with MIT licensing and retained third-party notices.
- Removed private development documents, real-data examples and site configuration.
- Required generated deployment credentials and removed the UI test-token login.
- Restricted local file access consistently and verified frozen input checksums.
- Quoted SIRIUS task inputs and applied configured MS1/MS2 tolerances.
- Added clean scientific image builds using bundled licensed MetaboLensR source.
- Defaulted MetaboLensR setup to an immutable prebuilt image with an explicit source-build fallback and package validation before descriptor updates.
- Documented source builds for fresh public clones while the prebuilt image remains unpublished.
- Isolated SIRIUS login state in private runtime storage.
- Added source-release scanning and updated security-related dependencies.
- Fixed the root URL to open the dashboard, verified during isolated end-to-end testing.
