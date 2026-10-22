from __future__ import annotations

import re
from typing import Any


DEFAULT_SAMPLE_TYPE = "sample"
CANONICAL_SAMPLE_TYPES = ("sample", "qc", "blank", "standard", "other")

_SAMPLE_TYPE_ALIASES = {
    "sample": "sample",
    "study_sample": "sample",
    "study": "sample",
    "qc": "qc",
    "quality_control": "qc",
    "quality_control_sample": "qc",
    "blank": "blank",
    "blank_sample": "blank",
    "standard": "standard",
    "standard_sample": "standard",
    "std": "standard",
    "STD": "standard",
    "other": "other",
    "others": "other",
}


def _sample_type_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def normalize_sample_type(value: Any) -> str:
    """Return the canonical sample type code used internally by MetaboLens."""
    if value is None:
        return DEFAULT_SAMPLE_TYPE

    raw_value = str(value).strip()
    if not raw_value:
        return DEFAULT_SAMPLE_TYPE

    key = _sample_type_key(raw_value)
    if key in _SAMPLE_TYPE_ALIASES:
        return _SAMPLE_TYPE_ALIASES[key]

    valid = ", ".join(CANONICAL_SAMPLE_TYPES)
    raise ValueError(f"Invalid sample_type '{raw_value}'. Valid values: {valid}")


def normalize_sample_annotations(value: Any) -> dict:
    """Validate the LabID-style sample annotation envelope without over-modeling it."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("annotations_json must be a JSON object")

    normalized = dict(value)
    for section in ("characteristics", "factors"):
        if section not in normalized:
            continue
        if normalized[section] is None:
            normalized[section] = {}
            continue
        if not isinstance(normalized[section], dict):
            raise ValueError(f"annotations_json.{section} must be a JSON object")
    return normalized
