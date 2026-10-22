import json
from datetime import datetime

import dash
from dash import html

from constants import SAMPLE_TYPE_LABELS, STATE_COLOR_MAP, STUDY_STATUS_LABELS


def short_timestamp(value: object) -> str:
    """`2026-09-03T14:41:40.713622Z` -> `03 Sep 14:41`.

    The year is added only when it is not the current one. The clock is the
    stored one (UTC for API timestamps), as in the job picker. Anything that
    does not parse is returned unchanged, so no value is ever hidden.
    """
    if value in (None, ""):
        return "-"
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    if moment.year != datetime.now(moment.tzinfo).year:
        return moment.strftime("%d %b %Y %H:%M")
    return moment.strftime("%d %b %H:%M")


def timestamp_cell(value: object) -> html.Span | str:
    """A short timestamp for tables, with the full stored value on hover."""
    short = short_timestamp(value)
    if short == "-" or short == str(value):
        return short
    return html.Span(short, title=str(value), className="ml-timestamp")


def pretty(obj) -> str:
    return json.dumps(obj, indent=2, sort_keys=True)


def status_label(value: str | None) -> str:
    raw = (value or "unknown").strip().lower()
    return STUDY_STATUS_LABELS.get(raw, raw.replace("_", " ").title())


def sample_type_label(value: str | None) -> str:
    raw = (value or "sample").strip().lower()
    return SAMPLE_TYPE_LABELS.get(raw, raw.replace("_", " ").title())


def metabolomics_assay_type_label(value: str | None) -> str:
    raw = (value or "").strip().upper().replace("-", "_")
    if raw == "LC_MS":
        return "LC-MS"
    if raw == "GC_MS":
        return "GC-MS"
    if raw == "NMR":
        return "NMR"
    return raw.replace("_", " ") if raw else "-"


def metabolomics_assay_type_value(assay: dict | None) -> str | None:
    metabolomics = (assay or {}).get("metabolomics_assay") or {}
    raw = str(metabolomics.get("assay_type") or "").strip().upper().replace("-", "_")
    return raw or None


def assay_kind_label(assay: dict | None) -> str:
    assay_data = assay or {}
    platform = str(assay_data.get("platform") or "").strip().lower()
    metabolomics_type = metabolomics_assay_type_label(metabolomics_assay_type_value(assay_data))

    if platform == "metabolomics":
        if metabolomics_type != "-":
            return f"Metabolomics {metabolomics_type}"
        return "Metabolomics"
    if platform:
        return platform.replace("_", " ").title()
    return "-"


def resolve_page_slug(pathname: str | None) -> tuple[str, str | None]:
    normalized = dash.strip_relative_path(pathname) if pathname else ""
    if not normalized:
        return "dashboard", "/dashboard"
    if normalized.isdigit():
        return "files", "/files"
    # Keep the old route as a deprecated bookmark redirect while the UI stays study-centric.
    if normalized == "experiments":
        return "studies", "/studies"
    return normalized, None


def state_badge(value: str | None) -> html.Span:
    raw = (value or "unknown").strip().lower()
    color = STATE_COLOR_MAP.get(raw, "#707372")
    label = status_label(raw)
    # The colour is passed as a variable, not a fill, so the stylesheet decides
    # how a state is drawn (dot, tint, text) while this map stays its source.
    return html.Span(label, className="state-pill", style={"--state-color": color}, **{"data-state": raw})


def file_sample_label(file_item: dict) -> str:
    sample_name = file_item.get("file_sample_name")
    sample_id = file_item.get("sample_id")
    if sample_name and sample_id is not None:
        return f"{sample_name} (#{sample_id})"
    if sample_name:
        return str(sample_name)
    if sample_id is not None:
        return f"#{sample_id}"
    return "-"


def dataset_sample_label(dataset_sample_names: list[str] | None, dataset_sample_ids: list[int] | None) -> str:
    names = [str(name) for name in (dataset_sample_names or []) if name]
    if names:
        return ", ".join(names)
    ids = [f"#{sample_id}" for sample_id in (dataset_sample_ids or [])]
    return ", ".join(ids) if ids else "-"


def format_bytes(value: int | None) -> str:
    if value is None:
        return "-"
    size = float(value)
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} GB"


def format_status_counts(counts: dict[str, int] | None) -> str:
    if not counts:
        return "-"
    parts = [f"{status_label(status)}: {count}" for status, count in sorted((counts or {}).items())]
    return ", ".join(parts)


def format_activity_payload(payload: dict | None) -> str:
    if not payload:
        return "-"
    compact = json.dumps(payload, sort_keys=True)
    if len(compact) > 180:
        return f"{compact[:177]}..."
    return compact
