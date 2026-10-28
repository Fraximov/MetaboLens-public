# Changelog

## 2026-10-28

- Corrected field map row MLISA-064 (batch identifier): batch is stored per injection, as its storage columns already said; the example still described per assay–sample storage.
- Added the field map's column guide, `docs/METABOLIGHTS_LABID_FIELD_MAP.md`, and indexed it from `docs/README.md`.
- Removed the field map's "LabID handoff action" column: guidance written before LabID's fields were known, mostly repeated stock sentences.
- Replaced the field map's "OLS / ontology lookup" prose with two columns generated from the pinned MetaboLights policy, "Ontology rule (general MetaboLights studies)" and "Ontology rule (MetabolomicsHub studies)". `scripts/update_field_map_ontology_rules.py` regenerates them and `test_field_map.py` checks them against the bundle.
- Corrected the field map guide: studies in the "MS (MTBLS)" category are shared with MetabolomicsHub under its legacy profile, not kept outside it.

## Initial public alpha

- Prepared standalone source with MIT licensing and retained third-party notices.
- Removed private development documents, real-data examples and site configuration.
- Required generated deployment credentials and removed the UI test-token login.
- Restricted local file access consistently and verified frozen input checksums.
- Quoted SIRIUS task inputs and applied configured MS1/MS2 tolerances.
- Added clean scientific image builds using bundled licensed MetaboLensR source.
- Defaulted MetaboLensR setup to an immutable prebuilt image with an explicit source-build fallback and package validation before descriptor updates.
- Published the tested MetaboLensR image on GitHub Container Registry and pinned both engines to its immutable digest, with anonymous pulls and the explicit source-build fallback.
- Added CI verification of anonymous MetaboLensR image pulls and both engine profile catalogs.
- Isolated SIRIUS login state in private runtime storage.
- Added source-release scanning and updated security-related dependencies.
- Fixed the root URL to open the dashboard, verified during isolated end-to-end testing.
