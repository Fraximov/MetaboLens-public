"""Resolve stable sample context and per-acquisition measurement metadata."""

from typing import Any


ASSAY_CONTEXT_ANNOTATION_KEYS = {"group_label"}
ACQUISITION_ANNOTATION_KEYS = {"batch_id", "injection_order", "vial", "acquired_at", "injection_volume_ul"}


def split_acquisition_annotations(value: Any) -> tuple[dict, dict]:
    annotations = annotations_dict(value)
    return ({key: val for key, val in annotations.items() if key not in ACQUISITION_ANNOTATION_KEYS},
            {key: val for key, val in annotations.items() if key in ACQUISITION_ANNOTATION_KEYS})


def normalize_acquisition_annotations(value: Any) -> dict:
    if not isinstance(value, dict) or set(value) - ACQUISITION_ANNOTATION_KEYS:
        raise ValueError("Acquisition annotations must contain only batch_id, injection_order, vial, acquired_at or injection_volume_ul")
    result = dict(value)
    if result.get("batch_id") is not None:
        if isinstance(result["batch_id"], bool) or not isinstance(result["batch_id"], (str, int)):
            raise ValueError("Batch ID must be text or an integer")
        result["batch_id"] = str(result["batch_id"]).strip()
    order = result.get("injection_order")
    if order not in (None, ""):
        try:
            normalized_order = int(order)
        except (ValueError, TypeError, OverflowError) as exc:
            raise ValueError("Injection order must be an integer >= 1") from exc
        if isinstance(order, bool) or (isinstance(order, float) and order != normalized_order) or normalized_order < 1:
            raise ValueError("Injection order must be an integer >= 1")
        result["injection_order"] = normalized_order
    if result.get("injection_volume_ul") not in (None, ""):
        import math
        if isinstance(result["injection_volume_ul"], bool):
            raise ValueError("Injection volume must be positive")
        try:
            volume = float(result["injection_volume_ul"])
        except (ValueError, TypeError) as exc:
            raise ValueError("Injection volume must be positive") from exc
        if not math.isfinite(volume) or volume <= 0:
            raise ValueError("Injection volume must be positive")
    for key in ("vial", "acquired_at"):
        if result.get(key) is not None and not isinstance(result[key], str):
            raise ValueError(f"{key} must be a string")
    return result


def annotations_dict(value: Any) -> dict:
    """Return a plain annotation dictionary, tolerating legacy null values."""

    return dict(value) if isinstance(value, dict) else {}


def sample_defaults_from_row_annotations(sample_annotations: dict | None, row_annotations: dict | None) -> dict:
    """Keep stable sample defaults while acquisition metadata is stored separately."""

    defaults = annotations_dict(sample_annotations)
    row_values = annotations_dict(row_annotations)
    if "group_label" in row_values and not str(defaults.get("group_label") or "").strip():
        defaults["group_label"] = row_values["group_label"]
    return defaults


def effective_sample_annotations(sample_annotations: dict | None, assay_annotations: dict | None,
                                 acquisition_annotations: dict | None = None) -> dict:
    """Merge sample defaults with assay-specific overrides."""

    values = {**annotations_dict(sample_annotations), **annotations_dict(assay_annotations)}
    if acquisition_annotations is not None:
        values, _ = split_acquisition_annotations(values)
        values.update(acquisition_annotations)
    return values
