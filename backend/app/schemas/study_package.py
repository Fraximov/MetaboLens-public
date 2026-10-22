"""The small, frozen contract between database mapping and ISA file writing.

Study metadata is frozen alongside workflow inputs. LabID integration can fill
these fields without the writer needing to know how LabID stores them.
"""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class PackageSourceSelection(BaseModel):
    assay_id: int = Field(gt=0)
    job_id: int = Field(gt=0)
    manifest_file_id: int = Field(gt=0)


class PackageRequest(BaseModel):
    request_key: UUID
    metabolights_id: str = Field(default="", pattern=r"^(?:REQ\d+|MTBLS\d+)?$")
    sources: list[PackageSourceSelection] = Field(min_length=1)
    omitted_assay_ids: list[int] = Field(default_factory=list)


class PackageCheck(BaseModel):
    """A proposed selection for the readiness check; nothing is frozen or built."""
    metabolights_id: str = Field(default="", pattern=r"^(?:REQ\d+|MTBLS\d+)?$")
    sources: list[PackageSourceSelection] = Field(default_factory=list)
    omitted_assay_ids: list[int] = Field(default_factory=list)


class PackageFile(BaseModel):
    file_id: int
    filename: str
    storage_uri: str
    # Registered input paths may not have been hashed at workflow launch.
    # Their first checksum is recorded in the package's file map during copying.
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    size_bytes: int | None = None
    role: str


class SampleSnapshot(BaseModel):
    sample_id: int
    name: str
    organism: str = ""
    organism_part: str = ""
    sample_type: str = ""
    ontology_terms: dict[str, dict[str, str]] = Field(default_factory=dict)
    factor_values: dict[str, str] = Field(default_factory=dict)


class AcquisitionSnapshot(BaseModel):
    acquisition_id: str
    sample_id: int
    file: PackageFile


class AssaySnapshot(BaseModel):
    assay_id: int
    assay_name: str
    job_id: int
    manifest_file_id: int
    polarity: str = ""
    method: dict[str, str] = Field(default_factory=dict)
    method_ontology_terms: dict[str, dict[str, str]] = Field(default_factory=dict)
    # Keys are ISA protocol names; values are the saved study metadata descriptions.
    protocols: dict[str, str] = Field(default_factory=dict)
    metadata_submission_id: int | None = None
    review_revision: int | None = None
    workflow: dict = Field(default_factory=dict)
    files: list[PackageFile]
    acquisitions: list[AcquisitionSnapshot]


class PackageSnapshot(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    study_id: int
    title: str
    description: str = ""
    metabolights_id: str = Field(default="", pattern=r"^(?:REQ\d+|MTBLS\d+)?$")
    submission_date: str = ""
    public_release_date: str = ""
    design_descriptors: list[dict[str, str]] = Field(default_factory=list)
    publications: list[dict[str, str]] = Field(default_factory=list)
    factors: list[dict] = Field(default_factory=list)
    collection_protocol: str = ""
    contacts: list[dict[str, str]] = Field(default_factory=list)
    samples: list[SampleSnapshot]
    assays: list[AssaySnapshot]
    omitted_assay_ids: list[int] = Field(default_factory=list)
    metadata_origin: str = "MetaboLens records at build request time"
