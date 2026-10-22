import base64
import csv
from io import StringIO

from constants import SAMPLE_TYPE_OPTIONS
from services.api_client import APIError, call_api
from services.study_service import ensure_draft_study, ensure_sample_details_editable

SAMPLE_IMPORT_TEMPLATE_COLUMNS = [
    "existing_sample_id",
    "sample_code",
    "description",
    "replicate_number",
    "dilution_factor",
    "sample_type",
    "organism",
    "group_label",
    "batch_id",
    "injection_order",
    "source_path",
]

_SAMPLE_TYPE_VALUE_MAP = {
    str(option["value"]).strip().lower(): str(option["value"]).strip().lower()
    for option in SAMPLE_TYPE_OPTIONS
}
_SAMPLE_TYPE_VALUE_MAP.update(
    {
        str(option["label"]).strip().lower(): str(option["value"]).strip().lower()
        for option in SAMPLE_TYPE_OPTIONS
    }
)
_SAMPLE_TYPE_VALUE_MAP["samples"] = "sample"


def blank_sample_row() -> dict:
    return {
        "existing_sample_id": "",
        "sample_code": "",
        "description": "",
        "replicate_number": 1,
        "dilution_factor": 1.0,
        "sample_type": "sample",
        "organism": "Homo sapiens",
        "group_label": "",
        "batch_id": "1",
        "injection_order": "",
        "source_path": "",
    }


def _build_row_annotations(row: dict | None, idx: int) -> dict:
    annotations: dict[str, object] = {}
    raw_group_label = str((row or {}).get("group_label") or "").strip()
    raw_batch_id = str((row or {}).get("batch_id") or "").strip()
    raw_injection_order = (row or {}).get("injection_order")

    if raw_group_label:
        annotations["group_label"] = raw_group_label
    if raw_batch_id:
        annotations["batch_id"] = raw_batch_id
    if raw_injection_order not in (None, ""):
        try:
            injection_order = int(raw_injection_order)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Row {idx + 1}: injection order must be an integer") from exc
        if injection_order < 1:
            raise ValueError(f"Row {idx + 1}: injection order must be >= 1")
        annotations["injection_order"] = injection_order
    return annotations


def build_sample_payload(rows: list[dict] | None) -> list[dict]:
    payload: list[dict] = []
    for idx, row in enumerate(rows or []):
        existing_sample_raw = (row or {}).get("existing_sample_id")
        sample_code_raw = str((row or {}).get("sample_code") or (row or {}).get("name") or "")
        description_raw = str((row or {}).get("description") or "")
        replicate_raw = (row or {}).get("replicate_number")
        dilution_raw = (row or {}).get("dilution_factor")
        sample_type_raw = str((row or {}).get("sample_type") or "sample")
        organism_raw = str((row or {}).get("organism") or "")
        group_label_raw = str((row or {}).get("group_label") or "")
        batch_id_raw = str((row or {}).get("batch_id") or "")
        injection_order_raw = (row or {}).get("injection_order")
        source_path_raw = str((row or {}).get("source_path") or "")
        if existing_sample_raw in (None, "") and not sample_code_raw.strip() and not description_raw.strip() and replicate_raw in (None, "") and dilution_raw in (None, "") and not organism_raw.strip() and not group_label_raw.strip() and not batch_id_raw.strip() and injection_order_raw in (None, ""):
            continue

        existing_sample_id = None
        if existing_sample_raw not in (None, ""):
            try:
                existing_sample_id = int(existing_sample_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Row {idx + 1}: existing sample id must be an integer") from exc

        sample_code = sample_code_raw.strip()
        description = description_raw.strip()
        sample_type = sample_type_raw.strip() or "sample"
        organism = organism_raw.strip() or "Homo sapiens"
        replicate_number = None
        dilution_factor = None
        if replicate_raw not in (None, ""):
            try:
                replicate_number = int(replicate_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Row {idx + 1}: replicate number must be an integer") from exc
            if replicate_number < 1:
                raise ValueError(f"Row {idx + 1}: replicate number must be >= 1")
        if dilution_raw not in (None, ""):
            try:
                dilution_factor = float(dilution_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Row {idx + 1}: dilution factor must be a number") from exc
            if dilution_factor <= 0:
                raise ValueError(f"Row {idx + 1}: dilution factor must be > 0")
        if existing_sample_id is None and not sample_code:
            raise ValueError(f"Row {idx + 1}: sample code is required")
        if existing_sample_id is None and not description:
            raise ValueError(f"Row {idx + 1}: description is required")
        source_path = source_path_raw.strip()
        if not source_path:
            raise ValueError(f"Row {idx + 1}: source path is required")
        annotations_json = _build_row_annotations(row, idx)

        item = {
            "raw_file": {
                "source_path": source_path,
            },
            "annotations_json": annotations_json,
        }
        if existing_sample_id is not None:
            item["existing_sample_id"] = existing_sample_id
        else:
            item["sample"] = {
                "name": sample_code,
                "description": description,
                "replicate_number": replicate_number,
                "dilution_factor": dilution_factor,
                "sample_type": sample_type,
                "organism": organism,
            }
        payload.append(item)

    if not payload:
        raise ValueError("Add at least one sample row with sample code or existing sample and a RAW source path")
    return payload


def sample_dropdown_options(samples: list[dict] | None) -> list[dict]:
    options = [{"label": "Create new sample", "value": ""}]
    for sample in samples or []:
        sample_id = sample.get("id")
        if sample_id is None:
            continue
        name = sample.get("name") or f"Sample #{sample_id}"
        options.append({"label": f"#{sample_id} {name}", "value": int(sample_id)})
    return options


def sample_import_template_csv() -> str:
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=SAMPLE_IMPORT_TEMPLATE_COLUMNS, lineterminator="\n")
    writer.writeheader()
    return buffer.getvalue()


def parse_sample_import_csv(contents: str | None) -> list[dict]:
    if not contents:
        raise ValueError("CSV upload is empty")
    if "," not in contents:
        raise ValueError("CSV upload content is malformed")

    _, encoded = contents.split(",", 1)
    try:
        decoded = base64.b64decode(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError("CSV upload could not be decoded") from exc

    text = decoded.decode("utf-8-sig")
    reader = csv.DictReader(StringIO(text))
    headers = [str(header or "").strip() for header in (reader.fieldnames or [])]
    if not headers:
        raise ValueError("CSV file has no header row")

    missing = [column for column in SAMPLE_IMPORT_TEMPLATE_COLUMNS if column not in headers]
    if missing:
        raise ValueError(f"CSV is missing required columns: {', '.join(missing)}")

    rows: list[dict] = []
    for index, raw_row in enumerate(reader, start=2):
        if raw_row is None:
            continue

        raw_values = {column: str((raw_row.get(column) or "")).strip() for column in SAMPLE_IMPORT_TEMPLATE_COLUMNS}
        if not any(raw_values.values()):
            continue

        row = blank_sample_row()
        row["existing_sample_id"] = raw_values["existing_sample_id"]
        row["sample_code"] = raw_values["sample_code"]
        row["description"] = raw_values["description"]
        row["organism"] = raw_values["organism"] or "Homo sapiens"
        row["group_label"] = raw_values["group_label"]
        row["batch_id"] = raw_values["batch_id"]
        row["injection_order"] = raw_values["injection_order"]
        row["source_path"] = raw_values["source_path"]

        sample_type_raw = raw_values["sample_type"].lower()
        if sample_type_raw:
            if sample_type_raw not in _SAMPLE_TYPE_VALUE_MAP:
                raise ValueError(f"CSV row {index}: unsupported sample_type {sample_type_raw!r}")
            row["sample_type"] = _SAMPLE_TYPE_VALUE_MAP[sample_type_raw]

        replicate_raw = raw_values["replicate_number"]
        if replicate_raw:
            try:
                row["replicate_number"] = int(replicate_raw)
            except ValueError as exc:
                raise ValueError(f"CSV row {index}: replicate_number must be an integer") from exc

        dilution_raw = raw_values["dilution_factor"]
        if dilution_raw:
            try:
                row["dilution_factor"] = float(dilution_raw)
            except ValueError as exc:
                raise ValueError(f"CSV row {index}: dilution_factor must be a number") from exc
        injection_order_raw = raw_values["injection_order"]
        if injection_order_raw:
            try:
                row["injection_order"] = int(injection_order_raw)
            except ValueError as exc:
                raise ValueError(f"CSV row {index}: injection_order must be an integer") from exc
        rows.append(row)

    if not rows:
        raise ValueError("CSV contains no sample rows")
    return rows


def list_study_samples(token: str, study_id: int) -> list[dict]:
    return call_api("GET", f"/studies/{study_id}/samples", token=token)


def update_study_sample(token: str, study_id: int, sample_id: int, payload: dict) -> dict:
    ensure_sample_details_editable(token, study_id)
    return call_api("PATCH", f"/studies/{study_id}/samples/{sample_id}", token=token, payload=payload)


def delete_study_sample(token: str, study_id: int, sample_id: int) -> dict:
    ensure_draft_study(token, study_id)
    return call_api("DELETE", f"/studies/{study_id}/samples/{sample_id}", token=token)


def update_sample_metadata_rows(token: str, study_id: int, rows: list[dict] | None) -> dict:
    ensure_sample_details_editable(token, study_id)
    current_samples = call_api("GET", f"/studies/{study_id}/samples", token=token)
    current_sample_ids = {int(sample["id"]) for sample in current_samples or [] if sample.get("id") is not None}

    updated_count = 0
    skipped_missing_count = 0
    for row in rows or []:
        sample_id = int(row["sample_id"])
        if sample_id not in current_sample_ids:
            skipped_missing_count += 1
            continue

        payload = build_sample_metadata_patch(
            row.get("sample_type"),
            row.get("matrix"),
            row.get("group_label"),
        )
        try:
            call_api("PATCH", f"/studies/{study_id}/samples/{sample_id}", token=token, payload=payload)
            updated_count += 1
        except APIError as exc:
            if str(exc).startswith("404:") and "Sample not found" in str(exc):
                skipped_missing_count += 1
                continue
            raise

    return {"updated_count": updated_count, "skipped_missing_count": skipped_missing_count}


def normalize_sample_type_value(sample_type) -> str:
    normalized = str(sample_type or "sample").strip().lower() or "sample"
    if normalized not in _SAMPLE_TYPE_VALUE_MAP:
        valid = ", ".join(option["value"] for option in SAMPLE_TYPE_OPTIONS)
        raise ValueError(f"Sample type must be one of: {valid}")
    return _SAMPLE_TYPE_VALUE_MAP[normalized]


def build_sample_metadata_patch(sample_type, matrix, group_label) -> dict:
    return {
        "sample_type": normalize_sample_type_value(sample_type),
        "matrix": str(matrix or "").strip(),
        "annotations_json": {"group_label": str(group_label or "").strip()},
    }


def build_folder_preview_rows(entries: list[dict], samples: list[dict]) -> list[dict]:
    by_name = {}
    for sample in samples:
        by_name.setdefault(str(sample.get("name") or "").lower(), []).append(sample["id"])
    rows = []
    for position, entry in enumerate(entries, 1):
        if entry.get("already_registered"):
            continue
        name = entry["sample_code"]
        matches = by_name.get(name.strip().lower(), [])
        rows.append({**blank_sample_row(), "sample_code": name, "description": name,
                     "source_path": entry["source_path"], "filename": entry["original_filename"],
                     "injection_order": position, "existing_sample_id": matches[0] if len(matches) == 1 else ""})
    return rows


def register_samples_with_paths(
    token: str,
    study_id: int,
    assay_id: int,
    sample_rows: list[dict] | None,
) -> list[dict]:
    ensure_draft_study(token, study_id)
    sample_payload = build_sample_payload(sample_rows)
    return call_api(
        "POST",
        f"/assays/{assay_id}/samples/with-paths",
        token=token,
        payload=sample_payload,
        timeout_seconds=120,
    )


def import_raw_folder(token: str, study_id: int, assay_id: int, folder_path: str | None) -> dict:
    normalized = str(folder_path or "").strip()
    if not normalized:
        raise ValueError("Folder path is required")
    ensure_draft_study(token, study_id)
    return call_api(
        "POST",
        f"/assays/{assay_id}/raw-paths/import",
        token=token,
        payload={"folder_path": normalized},
        timeout_seconds=120,
    )


def browse_raw_folders(token: str, study_id: int, folder_path: str | None) -> dict:
    ensure_draft_study(token, study_id)
    payload = {"folder_path": str(folder_path).strip()} if folder_path else {"folder_path": None}
    return call_api(
        "POST",
        f"/studies/{study_id}/raw-paths/browse",
        token=token,
        payload=payload,
        timeout_seconds=60,
    )


list_samples = list_study_samples
