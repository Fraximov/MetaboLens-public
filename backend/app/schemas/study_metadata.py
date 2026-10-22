"""Editable submission metadata; sample values stay in the existing sample sheet."""
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MetadataFields(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class OntologyTerm(MetadataFields):
    name: str = Field(default="", max_length=500)
    source: str = Field(default="", max_length=100)
    accession: str = Field(default="", max_length=1000)


class StudyContact(MetadataFields):
    first_name: str = Field(default="", max_length=255)
    last_name: str = Field(default="", max_length=255)
    email: str = Field(default="", max_length=255, pattern=r"^$|^[^@\s]+@[^@\s]+\.[^@\s]+$")
    affiliation: str = Field(default="", max_length=1000)
    role: str = Field(default="", max_length=255)


class StudyPublication(MetadataFields):
    title: str = Field(default="", max_length=2000)
    authors: str = Field(default="", max_length=10000)
    status: str = Field(default="", max_length=255)
    doi: str = Field(default="", max_length=1000)


class FactorDefinition(MetadataFields):
    name: str = Field(min_length=1, max_length=255)
    source_column: str = Field(min_length=1, max_length=500)
    type: OntologyTerm = Field(default_factory=OntologyTerm)

    @field_validator("name")
    @classmethod
    def valid_isa_factor_name(cls, value: str) -> str:
        if any(character in value for character in "[]\t\r\n"):
            raise ValueError("Factor names cannot contain square brackets, tabs or line breaks")
        # The investigation field and Factor Value header must use the same name.
        return " ".join(value.split())


ProtocolName = Literal["Extraction", "Chromatography", "Mass spectrometry"]


class AssaySubmissionMetadata(MetadataFields):
    assay_id: int
    assay_name: str = ""  # Display only; saving this form never renames an assay.
    protocols: dict[ProtocolName, str] = Field(default_factory=dict)


class StudySubmissionMetadata(MetadataFields):
    revision: int = Field(default=0, ge=0)
    title: str = Field(default="", max_length=255)
    description: str = Field(default="", max_length=50000)
    submission_date: date | None = None
    public_release_date: date | None = None
    contacts: list[StudyContact] = Field(default_factory=list, max_length=100)
    design_descriptors: list[OntologyTerm] = Field(default_factory=list, max_length=100)
    publications: list[StudyPublication] = Field(default_factory=list, max_length=100)
    factors: list[FactorDefinition] = Field(default_factory=list, max_length=100)
    collection_protocol: str = Field(default="", max_length=50000)
    assays: list[AssaySubmissionMetadata] = Field(default_factory=list)

    @model_validator(mode="after")
    def dates_follow_study_order(self):
        if self.submission_date and self.public_release_date and self.public_release_date < self.submission_date:
            raise ValueError("Public release date cannot be before the submission date")
        return self


class StudyMetadataView(BaseModel):
    metadata: StudySubmissionMetadata
    can_edit: bool
    samples: list[dict]
    # value, label, distinct values with sample counts, and samples whose assays disagree.
    factor_columns: list[dict]
    # assay_id -> protocol name -> other assays sharing that protocol record
    protocol_sharing: dict[int, dict[str, list[str]]] = Field(default_factory=dict)
