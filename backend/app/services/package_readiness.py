"""Readiness findings for an ISA package, computed without copying or hashing data.

``metadata_findings`` runs in every build and in the readiness check, so the two
cannot disagree. ``preflight_findings`` predicts errors the official validator
reports later, so they appear before any build; builds leave them to the
validator. ``source_findings`` reads each selected bundle's audit and manifest and
checks that registered spectral files exist with their recorded size, without
reading their bytes.

Each finding names the workspace section that fixes it: study, protocols,
samples, sources, workflow or identifier.
"""
from collections import Counter
import json
from pathlib import Path

from app.schemas.study_package import PackageSnapshot

LAB_PROTOCOLS = ("Extraction", "Chromatography", "Mass spectrometry")
# Non-empty LC-MS assay columns required by the pinned policy 2.2.0 template.
# Scan polarity comes from the assay's polarity, the rest from the instrument record.
REQUIRED_METHOD_FIELDS = ("Parameter Value[Chromatography Instrument]", "Parameter Value[Column model]",
                          "Parameter Value[Column type]", "Parameter Value[Instrument]", "Parameter Value[Scan m/z range]")
SECTIONS = ("study", "protocols", "samples", "sources", "workflow", "identifier", "official")
QUANTITY_NAMES = {"height": "peak height", "area": "peak area"}
QUANTIFICATION_UNITS = {"small_molecule_feature-quantification_unit", "small_molecule-quantification_unit"}
# Reports written before findings carried a section.
CODE_SECTIONS = {
    "missing_request_id": "identifier", "missing_contacts": "study", "missing_description": "study",
    "example_metadata": "study", "missing_collection_protocol": "protocols", "missing_protocol": "protocols",
    "unresolved_annotation_decisions": "workflow", "unverified_area": "workflow",  # replaced by unknown_quantity
    "input_checksums_recorded": "sources", "validator_unavailable": "official",
    "invalid_validator_report": "official", "isa_parser": "official",
}


def section_of(issue: dict) -> str:
    code = issue.get("code", "")
    return issue.get("section") or CODE_SECTIONS.get(code) or ("official" if code.startswith("rule_") else "sources")


def finding(code: str, section: str, message: str, severity: str = "error", **extra) -> dict:
    return dict(code=code, severity=severity, section=section, message=message, **extra)


def metadata_findings(snapshot: PackageSnapshot) -> list[dict]:
    """Checks every build reports: missing identifier, people, description and protocols."""
    issues = []
    if not snapshot.metabolights_id:
        issues.append(finding("missing_request_id", "identifier", "A real MetaboLights request or accession is required."))
    if not snapshot.contacts:
        issues.append(finding("missing_contacts", "study", "PI/contact information is empty. Complete it in Study metadata."))
    if not snapshot.description:
        issues.append(finding("missing_description", "study", "The study description is empty."))
    if not snapshot.collection_protocol:
        issues.append(finding("missing_collection_protocol", "protocols",
                              "The sample collection protocol is empty. Complete it in Study metadata."))
    for assay in sorted(snapshot.assays, key=lambda a: a.assay_id):
        for name in LAB_PROTOCOLS:
            if not assay.protocols.get(name):
                issues.append(finding("missing_protocol", "protocols",
                                      f"The {name.lower()} protocol is empty. Complete it in Study metadata.",
                                      assay_id=assay.assay_id))
    return issues


def preflight_findings(snapshot: PackageSnapshot) -> list[dict]:
    """Errors and warnings the official validator would raise, shown before a build."""
    issues = []
    if not snapshot.publications:
        issues.append(finding("missing_publication", "study",
                              "At least one publication is required; record one as 'in preparation' if none exists yet."))
    roles = [" ".join(str(c.get("Roles", "")).split()).casefold() for c in snapshot.contacts]
    if snapshot.contacts and "principal investigator" not in roles:
        issues.append(finding("no_principal_investigator", "study", "No contact has the principal investigator role.",
                              severity="warning"))
    for field, label in (("organism", "Organism"), ("organism_part", "Organism part")):
        empty = [s.name for s in snapshot.samples if not getattr(s, field)]
        if empty:
            issues.append(finding(f"missing_{field}", "samples",
                                  f"{label} is empty for {len(empty)} samples; the official validator requires it.",
                                  items=empty))
    shared = sorted(name for name, count in Counter(" ".join(s.name.split()) for s in snapshot.samples).items() if count > 1)
    if shared:
        issues.append(finding("sample_names_qualified", "samples",
                              f"{len(shared)} sample names are shared by several samples; each gets its sample ID appended.",
                              severity="warning", items=shared))
    for assay in sorted(snapshot.assays, key=lambda a: a.assay_id):
        empty = [f for f in REQUIRED_METHOD_FIELDS if not assay.method.get(f)] + ([] if assay.polarity else ["Scan polarity"])
        if empty:
            names = ", ".join(f.removeprefix("Parameter Value[").rstrip("]") for f in empty)
            issues.append(finding("missing_assay_method", "sources",
                                  f"{assay.assay_name}: the instrument record lacks {names}.", assay_id=assay.assay_id))
    return issues


def annotation_review_finding(assay_id: int, assay_name: str, unresolved: list[str]) -> dict | None:
    if not unresolved:
        return None
    return finding("unresolved_annotation_decisions", "workflow",
                   f"{assay_name}: {len(unresolved)} features still await an annotation decision. The draft MAF contains "
                   "only explicitly included features; resolve the rest in the job's review before a final submission.",
                   assay_id=assay_id, feature_ids=unresolved)


def export_quantity(path) -> str | None:
    """The quantity an MS-DIAL mzTab-M export names in its header: "height", "area" or None.

    MS-DIAL writes `MTD small_molecule_feature-quantification_unit [,,precursor intensity (peak height), ]`.
    Its alignment table (.mdalign), which the workflow packages, holds the same values. Only the
    metadata lines at the top are read.
    """
    try:
        with Path(path).open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                if not line.startswith(("MTD", "COM")):
                    break
                parts = line.rstrip("\n").split("\t")
                if len(parts) > 2 and parts[1] in QUANTIFICATION_UNITS:
                    unit = parts[2].lower()
                    return "height" if "height" in unit else "area" if "area" in unit else None
    except (OSError, ValueError):
        return None
    return None


def resolve_quantity(declared: str | None, exported: str | None) -> dict:
    """What the MAF values are: the run's declaration, else what its MS-DIAL export names.

    ``value`` is "height", "area" or None when unknown or contradicted.
    """
    declared = declared if declared in QUANTITY_NAMES else None
    if declared and exported and declared != exported:
        return dict(value=None, origin="conflict", declared=declared, exported=exported)
    if declared:
        return dict(value=declared, origin="declared by the run")
    if exported:
        return dict(value=exported, origin="named by the MS-DIAL export")
    return dict(value=None, origin="unknown")


def quantity_label(quantity: dict) -> str:
    """"peak height (declared by the run)", or "unknown"."""
    return f"{QUANTITY_NAMES[quantity['value']]} ({quantity['origin']})" if quantity.get("value") else "unknown"


def quantity_finding(assay_id: int, assay_name: str, quantity: dict) -> dict | None:
    """Heights and areas are both fine; a package must only say which it holds."""
    if quantity.get("value"):
        return None
    if quantity.get("origin") == "conflict":
        return finding("quantity_conflict", "workflow",
                       f"{assay_name}: the run declares {QUANTITY_NAMES[quantity['declared']]}, but its MS-DIAL export "
                       f"names {QUANTITY_NAMES[quantity['exported']]}; the MAF values cannot be described.",
                       assay_id=assay_id)
    return finding("unknown_quantity", "workflow",
                   f"{assay_name}: neither the run nor its MS-DIAL export says whether the values are peak heights "
                   "or areas, so the MAF values cannot be described.", assay_id=assay_id)


def source_findings(snapshot: PackageSnapshot, verified_file_ids: set[int] = frozenset()) -> tuple[list[dict], dict]:
    """Bundle decisions, quantity and spectral availability; spectral bytes are never read.

    ``verified_file_ids`` are files an earlier build already hashed; a new build reuses
    their checksums while they are unchanged, so they are not counted as unhashed.
    """
    from app.services.metabolights_export import local_file, read_table
    issues, spectral = [], dict(files=0, bytes=0, unhashed=0)
    for assay in sorted(snapshot.assays, key=lambda a: a.assay_id):
        roles = {f.role: f for f in assay.files}
        try:
            audit = read_table(local_file(roles["metabolensr_feature_audit_final"].storage_uri))[1]
            source = json.loads(local_file(roles["metabolensr_manifest"].storage_uri).read_text()).get("source", {})
        except (KeyError, OSError, ValueError) as error:
            issues.append(finding("bundle_unavailable", "sources", f"{assay.assay_name}: the final bundle cannot be read ({error}).",
                                  assay_id=assay.assay_id))
            continue
        try:
            exported = export_quantity(local_file(roles["msdial_mztabm"].storage_uri)) if "msdial_mztabm" in roles else None
        except ValueError:
            exported = None
        quantity = resolve_quantity(source.get("quantitation_type"), exported)
        for extra in (annotation_review_finding(assay.assay_id, assay.assay_name,
                                                [r["feature_id"] for r in audit if r.get("effective_decision_annotation") == "review"]),
                      quantity_finding(assay.assay_id, assay.assay_name, quantity)):
            if extra:
                issues.append(extra)
        unavailable, changed = [], []
        for acquisition in assay.acquisitions:
            record = acquisition.file
            spectral["files"] += 1
            spectral["unhashed"] += record.sha256 is None and record.file_id not in verified_file_ids
            try:
                size = local_file(record.storage_uri).stat().st_size
            except (OSError, ValueError):
                unavailable.append(record.filename)
                continue
            spectral["bytes"] += size
            if record.size_bytes is not None and size != record.size_bytes:
                changed.append(record.filename)
        if unavailable:
            issues.append(finding("spectral_file_unavailable", "sources",
                                  f"{assay.assay_name}: {len(unavailable)} registered spectral files are missing.",
                                  assay_id=assay.assay_id, items=unavailable))
        if changed:
            issues.append(finding("spectral_file_changed", "sources",
                                  f"{assay.assay_name}: {len(changed)} spectral files no longer have their registered size.",
                                  assay_id=assay.assay_id, items=changed))
    if spectral["unhashed"]:
        issues.append(finding("input_checksums_pending", "sources",
                              f"{spectral['unhashed']} spectral files have no checksum yet; "
                              "the next build reads them once to record one.", severity="warning"))
    return issues, spectral


def summarize(issues: list[dict]) -> dict:
    """Error and warning counts overall and per workspace section."""
    sections = {name: dict(errors=0, warnings=0) for name in SECTIONS}
    for issue in issues:
        counts = sections.setdefault(section_of(issue), dict(errors=0, warnings=0))
        if issue.get("severity") in {"error", "warning"}:
            counts["errors" if issue["severity"] == "error" else "warnings"] += 1
    return dict(errors=sum(s["errors"] for s in sections.values()),
                warnings=sum(s["warnings"] for s in sections.values()), sections=sections)
