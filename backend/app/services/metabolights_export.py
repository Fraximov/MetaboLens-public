"""Write ISA files from a frozen snapshot, without a database or network.

Read this module in order: verify/copy workflow outputs, check bundle identities,
then write the shared sample sheet and each assay's MAF. Spectral files are linked,
not copied; export materializes them. ISA names are the recorded sample and file
names, qualified only on collision. Scientific quantities stay as text so decimal
precision, zero and missing values survive the export unchanged.
"""
from __future__ import annotations

from collections import Counter
import csv
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote, urlparse

from app.schemas.study_package import AssaySnapshot, PackageFile, PackageSnapshot
from app.services.package_readiness import (annotation_review_finding, export_quantity, metadata_findings,
                                            quantity_finding, quantity_label, resolve_quantity)

TEMPLATES = Path(__file__).parents[1] / "resources/metabolights"
PROTOCOL_NAMES = ("Sample collection", "Extraction", "Chromatography", "Mass spectrometry",
                  "Data transformation", "Metabolite identification")


@lru_cache(maxsize=1)
def controlled_terms() -> dict:
    """MetaboLens-owned term aliases; see resources/metabolights/metabolens-terms.json."""
    return json.loads((TEMPLATES / "metabolens-terms.json").read_text())


def controlled_term(group: str, value, field: str | None = None) -> dict | None:
    """The exact term for a recorded value, matched case-insensitively; None if unknown."""
    terms = controlled_terms()[group]
    if field is not None:
        terms = terms.get(field, {})
    return {key.casefold(): term for key, term in terms.items()}.get(" ".join(str(value or "").split()).casefold())


def local_file(uri: str) -> Path:
    """Accept only registered local files; remote storage needs its own reader."""
    parsed = urlparse(uri)
    if parsed.scheme not in ("", "file") or parsed.netloc:
        raise ValueError("Packaging currently requires local or mounted file storage")
    path = Path(unquote(parsed.path))
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError(f"Registered source is unavailable or is a symlink: {path}")
    return path


def copy_verified(record: PackageFile, destination: Path) -> str:
    """Return the copied bytes' hash; verify any previously recorded hash and size."""
    if not record.sha256 and record.role != "input":
        raise ValueError(f"Workflow output file {record.file_id} needs a registered checksum")
    source = local_file(record.storage_uri)
    before = source.stat()
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest, size = hashlib.sha256(), 0
    with source.open("rb") as incoming, destination.open("xb") as outgoing:
        for block in iter(lambda: incoming.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
            outgoing.write(block)
    after = source.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise ValueError(f"Source changed during copying: file {record.file_id}")
    checksum = digest.hexdigest()
    if ((record.sha256 is not None and checksum != record.sha256)
            or (record.size_bytes is not None and size != record.size_bytes)):
        raise ValueError(f"Source checksum or size changed: file {record.file_id}")
    return checksum


def read_table(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        header = reader.fieldnames or []
        rows = list(reader)
    if not header or any(None in row or None in row.values() for row in rows):
        raise ValueError(f"Malformed tabular file: {path.name}")
    return header, rows


def write_table(path: Path, header: list[str], rows) -> None:
    # csv.writer preserves repeated ISA headers such as Protocol REF.
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def clean(value) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in {"na", "nan", "null", "none"} else text


def link_verified(record: PackageFile, link: Path, verified: dict[int, dict]) -> dict:
    """Link one registered spectral file without copying it; return its frozen reference.

    A checksum is reused only from an earlier package that hashed the same file with
    unchanged size, modification time and inode; otherwise the file is read once.
    Export re-hashes every linked file before copying it.
    """
    source = local_file(record.storage_uri)
    before = source.stat()
    state = dict(size_bytes=before.st_size, mtime_ns=before.st_mtime_ns, inode=before.st_ino)
    if record.size_bytes is not None and before.st_size != record.size_bytes:
        raise ValueError(f"Source checksum or size changed: file {record.file_id}")
    previous = verified.get(record.file_id) or {}
    if previous.get("sha256") and all(previous.get(key) == value for key, value in state.items()):
        checksum, verification = previous["sha256"], "reused"
    else:
        checksum, verification = file_digest(source), "hashed"
        after = source.stat()
        if (after.st_size, after.st_mtime_ns, after.st_ino) != (before.st_size, before.st_mtime_ns, before.st_ino):
            raise ValueError(f"Source changed during hashing: file {record.file_id}")
    if record.sha256 is not None and checksum != record.sha256:
        raise ValueError(f"Source checksum or size changed: file {record.file_id}")
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(source)
    return dict(file_id=record.file_id, sha256=checksum, verification=verification, **state)


def isa_text(value) -> str:
    """ISA cells are single lines; collapse whitespace and keep every other character."""
    return " ".join(str(value or "").split())


def qualify_collisions(names: dict, suffix, key=lambda name: name) -> dict:
    """Keep recorded names; give every member of a colliding group its identifier."""
    counts = Counter(key(name) for name in names.values())
    result = {identity: suffix(identity, name) if counts[key(name)] > 1 else name
              for identity, name in names.items()}
    if len({key(name) for name in result.values()}) != len(result):
        raise ValueError("Recorded names still collide after adding their identifiers")
    return result


def export_names(snapshot: PackageSnapshot) -> tuple[dict, dict, dict, dict]:
    """Sample labels, assay keys, spectral file names and MS Assay Names.

    Spectral file names must be unique across the study, and each MS Assay Name is
    its file's stem. A clash inside one assay adds the file ID; a clash between
    assays adds the assay key. Names are compared case-insensitively because
    exported folders may land on case-insensitive file systems. Assay keys never
    contain the underscore that separates the official reader's filename parts.
    """
    samples = qualify_collisions(
        {s.sample_id: isa_text(s.name) or f"sample-{s.sample_id}" for s in snapshot.samples},
        lambda sample_id, name: f"{name}-s{sample_id}")
    assays = qualify_collisions(
        {a.assay_id: re.sub(r"[^A-Za-z0-9]+", "-", a.assay_name).strip("-") or f"assay-{a.assay_id}"
         for a in snapshot.assays},
        lambda assay_id, name: f"{name}-a{assay_id}", key=str.casefold)
    stem = lambda name: Path(name).stem.casefold()  # noqa: E731
    within_assays, owner = {}, {}
    for assay in snapshot.assays:
        original = {}
        for acquisition in assay.acquisitions:
            name = Path(acquisition.file.filename).name
            if not name or name in {".", ".."}:
                raise ValueError(f"Spectral file {acquisition.file.file_id} has no usable file name")
            original[acquisition.file.file_id] = name
            owner[acquisition.file.file_id] = assay.assay_id
        within_assays.update(qualify_collisions(
            original, lambda file_id, name: f"{Path(name).stem}-f{file_id}{Path(name).suffix}", key=stem))
    files = qualify_collisions(within_assays, lambda file_id, name: f"{assays[owner[file_id]]}-{name}", key=stem)
    ms_names = {(assay.assay_id, a.acquisition_id): Path(files[a.file.file_id]).stem
                for assay in snapshot.assays for a in assay.acquisitions}
    return samples, assays, files, ms_names


def file_digest(path: Path, algorithm="sha256") -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def unique_rows(rows, key: str, filename: str) -> dict[str, dict]:
    result = {}
    for row in rows:
        identity = row.get(key)
        if not identity or identity in result:
            raise ValueError(f"Missing or duplicate {key} in {filename}")
        result[identity] = row
    return result


def bundle_tables(assay: AssaySnapshot, copied: dict[int, Path]):
    """Require one complete final bundle and join features by ID, never position."""
    def member(role):
        matches = [f for f in assay.files if f.role == role]
        if len(matches) != 1:
            raise ValueError(f"Assay {assay.assay_id} needs exactly one {role}")
        return copied[matches[0].file_id]

    manifest_path = member("metabolensr_manifest")
    if copied.get(assay.manifest_file_id) != manifest_path:
        raise ValueError("Selected final manifest does not match the source bundle")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema_version") != "2.0" or manifest.get("bundle_type") != "final":
        raise ValueError("Only final schema-2 MetaboLensR bundles can be packaged")
    if manifest.get("source", {}).get("tool") != "msdial":
        raise ValueError("The first packaging mapping supports MS-DIAL bundles")
    outputs = manifest.get("outputs")
    if not isinstance(outputs, list) or not all(isinstance(output, dict) for output in outputs):
        raise ValueError("Final bundle has no valid output inventory")
    names = [output.get("file") for output in outputs]
    required = {"feature_audit_final.tsv", "sample_metadata.tsv", "normalized_raw.tsv"}
    if not required.issubset(names) or len(set(names)) != len(names):
        raise ValueError("Final bundle inventory is incomplete or contains duplicate files")
    for output in outputs:
        path = manifest_path.parent / output["file"]
        if path.parent != manifest_path.parent or not path.is_file():
            raise ValueError("Final bundle inventory contains an unavailable output")
        if file_digest(path, "md5") != output.get("md5"):
            raise ValueError(f"Final bundle inventory checksum mismatch: {path.name}")

    audit_header, audit_rows = read_table(member("metabolensr_feature_audit_final"))
    audit = unique_rows(audit_rows, "feature_id", "feature audit")
    if "effective_decision_annotation" not in audit_header or any(
            row["effective_decision_annotation"] not in {"include", "exclude", "review"} for row in audit_rows):
        raise ValueError("Final feature audit contains missing or invalid annotation decisions")
    header, matrix_rows = read_table(member("metabolensr_normalized_raw"))
    matrix = unique_rows(matrix_rows, "feature_id", "raw matrix")
    if set(matrix) != set(audit):
        raise ValueError("Raw matrix and final feature audit do not contain the same feature IDs")
    _, sample_rows = read_table(member("metabolensr_sample_metadata"))
    samples = unique_rows(sample_rows, "acquisition_id", "sample metadata")
    acquisitions = {a.acquisition_id: a for a in assay.acquisitions}
    if len(acquisitions) != len(assay.acquisitions) or set(header[1:]) != set(acquisitions) or len(header) != len(acquisitions) + 1:
        raise ValueError("Matrix acquisition columns do not match the frozen workflow inputs")
    if set(samples) != set(acquisitions):
        raise ValueError("Sample metadata acquisitions do not match the matrix")
    for identity, acquisition in acquisitions.items():
        row = samples[identity]
        if (identity != f"file-{acquisition.file.file_id}" or
                row.get("file_asset_id") != str(acquisition.file.file_id) or
                row.get("sample_id") != str(acquisition.sample_id)):
            raise ValueError(f"Acquisition-to-sample identity mismatch: {identity}")
    return manifest, audit, matrix, header[1:]


def write_investigation(snapshot, path, assay_files, protocol_descriptions):
    with (TEMPLATES / "i_Investigation.txt").open() as stream:
        template = list(csv.reader(stream, delimiter="\t"))
    definitions = json.loads((TEMPLATES / "protocols.json").read_text())["protocolDefinitions"]
    grouped = {}
    for name, _, description in protocol_descriptions:
        grouped.setdefault(name, [])
        if description:
            grouped[name].append(description)
    names = list(grouped)
    publication_terms = [controlled_term("publication_status", publication.get("status", ""))
                         for publication in snapshot.publications]
    values = {
        "Study Identifier": [snapshot.metabolights_id], "Study Title": [snapshot.title],
        "Study Submission Date": [snapshot.submission_date],
        "Study Public Release Date": [snapshot.public_release_date],
        "Study Design Type": [term.get("name", "") for term in snapshot.design_descriptors],
        "Study Design Type Term Source REF": [term.get("source", "") for term in snapshot.design_descriptors],
        "Study Design Type Term Accession Number": [term.get("accession", "") for term in snapshot.design_descriptors],
        "Study Factor Name": [factor["name"] for factor in snapshot.factors],
        "Study Factor Type": [factor.get("type", {}).get("name", "") for factor in snapshot.factors],
        "Study Factor Type Term Source REF": [factor.get("type", {}).get("source", "") for factor in snapshot.factors],
        "Study Factor Type Term Accession Number": [factor.get("type", {}).get("accession", "") for factor in snapshot.factors],
        "Study Publication Title": [publication.get("title", "") for publication in snapshot.publications],
        "Study Publication Author List": [publication.get("authors", "") for publication in snapshot.publications],
        "Study Publication Status": [term["name"] if term else publication.get("status", "")
                                     for publication, term in zip(snapshot.publications, publication_terms)],
        "Study Publication Status Term Source REF": [term["source"] if term else "" for term in publication_terms],
        "Study Publication Status Term Accession Number": [term["accession"] if term else "" for term in publication_terms],
        "Study Publication DOI": [publication.get("doi", "") for publication in snapshot.publications],
        "Study Description": [snapshot.description], "Study File Name": [f"s_{snapshot.metabolights_id or 'local-draft'}.txt"],
        "Study Assay File Name": assay_files,
        "Study Assay Measurement Type": ["metabolite profiling assay"] * len(assay_files),
        "Study Assay Measurement Type Term Source REF": ["OBI"] * len(assay_files),
        "Study Assay Measurement Type Term Accession Number": ["http://purl.obolibrary.org/obo/OBI_0000366"] * len(assay_files),
        "Study Assay Technology Platform": ["Liquid Chromatography MS"] * len(assay_files),
        "Study Assay Technology Type": ["mass spectrometry"] * len(assay_files),
        "Study Assay Technology Type Term Source REF": ["OBI"] * len(assay_files),
        "Study Assay Technology Type Term Accession Number": ["http://purl.obolibrary.org/obo/OBI_0000470"] * len(assay_files),
        "Study Protocol Name": names,
        "Study Protocol Type": [definitions.get(name, {}).get("type", {}).get("term", name.lower()) for name in names],
        "Study Protocol Type Term Source REF": [definitions.get(name, {}).get("type", {}).get("termSourceRef", "") for name in names],
        "Study Protocol Type Term Accession Number": [definitions.get(name, {}).get("type", {}).get("termAccessionNumber", "") for name in names],
        "Study Protocol Description": [" ".join(grouped[name]) for name in names],
        "Study Protocol Parameters Name": [";".join(definitions.get(name, {}).get("parameters", [])) for name in names],
    }
    for field in ("First Name", "Last Name", "Email", "Affiliation"):
        values[f"Study Person {field}"] = [contact.get(field, "") for contact in snapshot.contacts]
    # Exact role aliases only, never an inference about a contact's responsibility.
    roles = [contact.get("Roles", "") for contact in snapshot.contacts]
    role_terms = [controlled_term("contact_roles", role) for role in roles]
    values["Study Person Roles"] = [term["name"] if term else role for role, term in zip(roles, role_terms)]
    values["Study Person Roles Term Source REF"] = [term["source"] if term else "" for term in role_terms]
    values["Study Person Roles Term Accession Number"] = [term["accession"] if term else "" for term in role_terms]
    # Declare supplied ontology sources without inventing a version or URL for them.
    ontology_rows = {row[0]: row[1:] for row in template if row[0] in {
        "Term Source Name", "Term Source File", "Term Source Version", "Term Source Description"}}
    terms = [*snapshot.design_descriptors, *(factor.get("type", {}) for factor in snapshot.factors),
             *(term for sample in snapshot.samples for term in sample.ontology_terms.values()),
             *(term for assay in snapshot.assays for term in assay.method_ontology_terms.values())]
    extra_sources = sorted(({term.get("source", "") for term in terms}
                            | {term["source"] for term in publication_terms if term}
                            | set(values["Study Person Roles Term Source REF"]))
                           - set(ontology_rows["Term Source Name"]) - {""})
    for field, entries in ontology_rows.items():
        values[field] = entries + (extra_sources if field == "Term Source Name" else [""] * len(extra_sources))
    rows, section = [], ""
    for row in template:
        field = row[0]
        if len(row) == 1:
            section = field
            rows.append(row)
            continue
        # Keep ontology definitions and template version comments, not example text
        # or an implied licence/publication decision on behalf of the scientist.
        default = row[1:] if section == "ONTOLOGY SOURCE REFERENCE" or field in {
            "Comment[Sample Template]", "Comment[Study Template]", "Comment[Template Version]"} else [""]
        rows.append([field, *(" ".join(str(value).split()) for value in (values.get(field) or default))])
    with path.open("w", newline="") as stream:
        csv.writer(stream, delimiter="\t", lineterminator="\n").writerows(rows)


def write_maf(path, audit, matrix, columns, acquisition_names, assay_id, assay_key, source):
    header = read_table(TEMPLATES / "m_ms.tsv")[0]
    rows, crosswalk = [], []
    for feature_id, feature in audit.items():
        if feature["effective_decision_annotation"] != "include":
            continue
        name = clean(feature.get("feature_name"))
        if not name or name.lower() == "unknown":
            name = f"Unknown {assay_key} feature-{feature_id}"
        fields = dict(chemical_formula=clean(feature.get("Formula")), smiles=clean(feature.get("SMILES")),
                      metabolite_identification=name, mass_to_charge=clean(feature.get("mz")),
                      retention_time=clean(feature.get("rt_min")), modifications=clean(feature.get("adduct")),
                      search_engine=f"MS-DIAL {source.get('version', '')}".strip())
        score = clean(feature.get("Total score"))
        if score:
            fields["search_engine_score"] = f"Total score: {score}"
        # INCHIKEY is deliberately not mapped to the full-InChI column.
        abundances = []
        for column in columns:
            value = clean(matrix[feature_id][column])
            if value:
                try:
                    number = Decimal(value)
                    if not number.is_finite() or number < 0:
                        raise ValueError("non-finite or negative abundance")
                except (InvalidOperation, ValueError) as error:
                    raise ValueError(f"Invalid abundance at feature {feature_id}, {column}") from error
            abundances.append(value)
        rows.append([fields.get(field, "") for field in header] + abundances)
        crosswalk.append([assay_id, feature_id, name, len(rows) + 1])
    if not rows:
        raise ValueError(f"Assay {assay_id} has no annotation-included features")
    write_table(path, header + acquisition_names, rows)
    return crosswalk


def generated_protocols(source: dict, package_version: str, quantity: dict | None = None) -> dict[str, str]:
    """Text every build appends to these two protocols, from the selected run's bundle.

    ``quantity`` is the resolved one (see ``resolve_quantity``); without it, the run's declaration.
    """
    quantity = quantity or resolve_quantity(source.get("quantitation_type"), None)
    return {
        "Data transformation": (
            f"MS-DIAL {source.get('version', '')}; MetaboLensR {package_version}. "
            f"Unnormalized matrix normalized_raw.tsv; quantity: {quantity_label(quantity)}. "
            "Retention times are in minutes. Original processing and gap-filled values are preserved."),
        "Metabolite identification": (
            "Annotations are copied from the selected workflow final audit. Only effective_decision_annotation=include "
            "features are reported, including unknowns. MS-DIAL Total score is retained when present; "
            "no MSI confidence level or chemical structure is inferred."),
    }


def build_package(snapshot: PackageSnapshot, destination: Path, verified_files: dict[int, dict] | None = None) -> dict:
    """Build once in a new directory. An exception leaves an incomplete directory.

    The caller records completion only after this function and official validation
    finish. Existing packages are never overwritten, even after source changes.
    ``verified_files`` holds earlier packages' spectral references by file ID.
    """
    destination.mkdir(parents=True, exist_ok=False)
    submission = destination / "submission"
    provenance = destination / "provenance"
    submission.mkdir()
    provenance.mkdir()
    # The same metadata checks run in the readiness endpoint, so the two cannot disagree.
    issues, file_map, feature_map, spectral = metadata_findings(snapshot), [], [], {}
    sample_by_id = {s.sample_id: s for s in snapshot.samples}
    if len(sample_by_id) != len(snapshot.samples):
        raise ValueError("Duplicate biological sample IDs in the snapshot")
    assay_ids = [a.assay_id for a in snapshot.assays]
    if not assay_ids or len(set(assay_ids)) != len(assay_ids):
        raise ValueError("Select exactly one final bundle per included assay")
    included_sample_ids = {a.sample_id for assay in snapshot.assays for a in assay.acquisitions}
    if included_sample_ids != set(sample_by_id):
        raise ValueError("Sample snapshot must be the union of selected acquisitions")

    sample_header = read_table(TEMPLATES / "s_sample.txt")[0]
    for factor in snapshot.factors:
        # ISA requires the term columns even when the recorded value has no ontology reference.
        sample_header.extend([f"Factor Value[{factor['name']}]", "Term Source REF", "Term Accession Number"])
    labels, assay_keys, file_names, ms_names = export_names(snapshot)
    sample_rows = []
    for sample in sorted(snapshot.samples, key=lambda s: s.sample_id):
        # A biological sample appears once, even if measured repeatedly or in both modes.
        values = {"Source Name": labels[sample.sample_id], "Sample Name": labels[sample.sample_id],
                  "Characteristics[Organism]": sample.organism, "Characteristics[Organism part]": sample.organism_part,
                  "Characteristics[Sample type]": sample.sample_type, "Protocol REF": "Sample collection"}
        values.update({f"Factor Value[{name}]": value for name, value in sample.factor_values.items()})
        row = [values.get(field, "") for field in sample_header]
        # Repeated Term Source/Accession headers belong to the characteristic before them.
        for key, label in (("organism", "Organism"), ("organism_part", "Organism part"), ("sample_type", "Sample type")):
            term = sample.ontology_terms.get(key, {})
            column = sample_header.index(f"Characteristics[{label}]")
            row[column + 1:column + 3] = [term.get("source", ""), term.get("accession", "")]
        sample_rows.append(row)
    write_table(submission / f"s_{snapshot.metabolights_id or 'local-draft'}.txt", sample_header, sample_rows)
    write_table(provenance / "sample-map.tsv", ["sample_id", "original_name", "Sample Name"],
                [[s.sample_id, s.name, labels[s.sample_id]] for s in sorted(snapshot.samples, key=lambda s: s.sample_id)])

    assay_files, protocols = [], [("Sample collection", "sample collection", snapshot.collection_protocol)]
    for assay in sorted(snapshot.assays, key=lambda a: a.assay_id):
        support = provenance / f"assay-{assay.assay_id}"
        copied = {}
        for record in assay.files:
            if Path(record.filename).name != record.filename or record.file_id in copied:
                raise ValueError("Unsafe filename or duplicate source file ID")
            path = support / record.filename if record.role.startswith("metabolensr_") else support / "workflow-annotations" / f"file-{record.file_id}-{record.filename}"
            copy_verified(record, path)
            copied[record.file_id] = path
        manifest, audit, matrix, columns = bundle_tables(assay, copied)
        unresolved = [feature_id for feature_id, row in audit.items() if row["effective_decision_annotation"] == "review"]
        source = manifest["source"]
        export = next((copied[r.file_id] for r in assay.files if r.role == "msdial_mztabm"), None)
        quantity = resolve_quantity(source.get("quantitation_type"), export_quantity(export) if export else None)
        issues.extend(f for f in (annotation_review_finding(assay.assay_id, isa_text(assay.assay_name), unresolved),
                                  quantity_finding(assay.assay_id, isa_text(assay.assay_name), quantity)) if f)
        if source.get("retention_time_unit") != "minute":
            raise ValueError("MS-DIAL retention times must explicitly be in minutes")
        key = assay_keys[assay.assay_id]
        prefix = snapshot.metabolights_id or "local-draft"
        maf_name = f"m_{prefix}_{key}_metabolite_profiling_v2_maf.tsv"
        # The official reader identifies the technique from the third filename part.
        assay_name = f"a_{prefix}_LC-MS_{key}_metabolite_profiling.txt"
        assay_files.append(assay_name)
        acquisition_names = [ms_names[(assay.assay_id, column)] for column in columns]
        feature_map.extend(write_maf(submission / maf_name, audit, matrix, columns, acquisition_names,
                                     assay.assay_id, key, source))
        descriptions = dict(assay.protocols)
        for name, evidence in generated_protocols(source, manifest.get("package_version", ""), quantity).items():
            descriptions[name] = f"{descriptions.get(name, '')} {evidence}".strip()
        for name in PROTOCOL_NAMES[1:]:
            description = descriptions.get(name, "")
            labelled = f"{isa_text(assay.assay_name)} ({assay.polarity}): {description}" if description else ""
            protocols.append((name, name.lower(), labelled))
        assay_header = read_table(TEMPLATES / "a_lcms.txt")[0]
        rows, newly_hashed = [], []
        by_acquisition = {a.acquisition_id: a for a in assay.acquisitions}
        for column in columns:
            acquisition = by_acquisition[column]
            record = acquisition.file
            suffix = Path(record.filename).suffix.lower()
            if suffix not in {".raw", ".mzml"}:
                raise ValueError("The first packager supports individual RAW or mzML input files")
            folder = "RAW_FILES" if suffix == ".raw" else "DERIVED_FILES"
            relative = f"FILES/{folder}/{key}/{file_names[record.file_id]}"
            reference = link_verified(record, submission / relative, verified_files or {})
            spectral[f"submission/{relative}"] = reference
            origin = "registered" if record.sha256 else "packaging"
            acquisition_name = ms_names[(assay.assay_id, column)]
            file_map.append([assay.assay_id, assay.job_id, record.file_id, record.filename, record.storage_uri,
                             relative, acquisition_name, reference["sha256"], origin, reference["verification"]])
            if not record.sha256:
                newly_hashed.append(record.file_id)
            values = {"Sample Name": labels[acquisition.sample_id], "Extract Name": f"extract-{acquisition_name}",
                      "Labeled Extract Name": f"extract-{acquisition_name}", "MS Assay Name": acquisition_name,
                      "Parameter Value[Scan polarity]": assay.polarity,
                      "Raw Spectral Data File" if suffix == ".raw" else "Derived Spectral Data File": relative,
                      "Metabolite Assignment File": maf_name,
                      "Normalization Name": "unnormalized", "Data Transformation Name": f"workflow-job-{assay.job_id}"}
            values.update(assay.method)
            protocol_index = 0
            row = []
            for field in assay_header:
                if field == "Protocol REF":
                    protocol_index += 1
                    # The pinned policy requires these exact protocol names.
                    row.append(PROTOCOL_NAMES[protocol_index])
                else:
                    row.append(values.get(field, ""))
            for field, term in assay.method_ontology_terms.items():
                column = assay_header.index(field)
                if assay_header[column + 1:column + 3] != ["Term Source REF", "Term Accession Number"]:
                    raise ValueError(f"No ontology columns follow {field}")
                row[column + 1:column + 3] = [term.get("source", ""), term.get("accession", "")]
            rows.append(row)
        write_table(submission / assay_name, assay_header, rows)
        if newly_hashed:
            issues.append(dict(code="input_checksums_recorded", severity="warning", section="sources", assay_id=assay.assay_id,
                file_ids=newly_hashed, message=f"Assay {assay.assay_id}: {len(newly_hashed)} input files had no registered checksum. "
                "SHA-256 checksums were recorded during packaging; their contents at workflow launch cannot be verified retrospectively."))

    write_investigation(snapshot, submission / "i_Investigation.txt", assay_files, protocols)
    write_table(provenance / "file-map.tsv", ["assay_id", "job_id", "file_id", "original_name", "source_uri", "export_path",
                                              "ms_assay_name", "sha256", "checksum_origin", "verification"], file_map)
    write_table(provenance / "feature-map.tsv", ["assay_id", "feature_id", "export_name", "maf_row"], feature_map)
    (provenance / "snapshot.json").write_text(snapshot.model_dump_json(indent=2) + "\n")
    inventory = []
    for path in sorted(destination.rglob("*")):
        relative = path.relative_to(destination).as_posix()
        if path.is_symlink():
            # Linked spectra are not read again; their checksum was frozen above.
            reference = spectral[relative]
            inventory.append(dict(path=relative, sha256=reference["sha256"], size_bytes=reference["size_bytes"], linked=True))
        elif path.is_file():
            inventory.append(dict(path=relative, sha256=file_digest(path), size_bytes=path.stat().st_size))
    result = dict(schema_version="1.1", submission_ready=False, official_validation="not_run",
                  issues=issues, files=inventory, spectral_files=list(spectral.values()),
                  remote_validation="not_performed", tools=json.loads((TEMPLATES / "lock.json").read_text()))
    (provenance / "manifest.json").write_text(json.dumps(result, indent=2) + "\n")
    (destination / "README.txt").write_text(
        "MetaboLens Study package\n\nOnly submission/ contains ISA files and spectral data.\n"
        "Spectral files are links to their registered location, not copies. Export the\n"
        "package to obtain a self-contained folder; export re-verifies every checksum.\n"
        "provenance/ preserves workflow annotations and identity mappings.\n"
        "Read validation/report.json before submitting. No remote submission was performed.\n")
    return result
