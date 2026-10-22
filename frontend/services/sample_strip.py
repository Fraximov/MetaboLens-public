"""The run's samples, drawn as one strip in injection order.

Reads the frozen `samplesheet.csv` a run was launched with. That file is the
pipeline's own contract, so it reports sample types, groups, batches and
injection order as they were at launch, not as the sample records read today.

The strip answers the two questions asked when a run is opened:

* **Was the batch laid out properly** -- are QCs spread through the sequence or
  clumped at the front, do blanks bracket it, are study groups interleaved rather
  than acquired in blocks.
* **Did every acquisition complete** -- bar height is the RAW file size, so a
  truncated acquisition is a short bar among its neighbours.

Colours are the validated categorical slots for both light and dark surfaces; see
the palette note beside SAMPLE_TYPE_COLOURS.
"""
from __future__ import annotations

import csv
import io

# Categorical identity, in fixed order, never cycled. Validated together on both
# surfaces: lightness band, chroma floor, CVD separation, normal-vision floor and
# contrast all pass with no warning. Deliberately not the metro map's colours --
# those encode run status, and status colours are never reused for a series.
SAMPLE_TYPE_COLOURS = {
    "sample": {"light": "#2a78d6", "dark": "#3987e5"},
    "qc": {"light": "#eb6834", "dark": "#d95926"},
    "blank": {"light": "#4a3aa7", "dark": "#9085e9"},
    "other": {"light": "#eda100", "dark": "#c98500"},
}
TYPE_ORDER = ("sample", "qc", "blank", "other")


def sizes_from_manifest(manifest: dict | None) -> dict[str, int]:
    """RAW file sizes keyed by asset id, from the job's frozen input manifest.

    The samplesheet names the sample and its order but not how large the file is;
    the manifest has the size but not the type or the order. They share
    `file_asset_id`, and the manifest is already on the job record, so joining
    them costs nothing.
    """
    sizes = {}
    for entry in (manifest or {}).get("selected_files") or []:
        asset_id = entry.get("file_asset_id")
        size = entry.get("file_size_bytes")
        if asset_id is not None and isinstance(size, int):
            sizes[str(asset_id)] = size
    return sizes


def parse_samplesheet(text: str | None, sizes: dict[str, int] | None = None) -> list[dict]:
    """Rows in acquisition order.

    The file is not written in injection order, so sorting by that column is what
    makes the strip a sequence rather than a list. Rows without one keep their
    file order behind those that have one.
    """
    if not text or not text.strip():
        return []
    try:
        rows = list(csv.DictReader(io.StringIO(text)))
    except (csv.Error, ValueError):
        return []

    parsed = []
    for position, row in enumerate(rows):
        if not row:
            continue
        try:
            order = int(str(row.get("injection_order") or "").strip())
        except (TypeError, ValueError):
            order = None
        size = (sizes or {}).get(str(row.get("file_asset_id") or "").strip())
        raw_type = str(row.get("sample_type") or "").strip().lower()
        parsed.append({
            "name": str(row.get("sample_name") or row.get("input_filename") or "?").strip(),
            "type": raw_type if raw_type in SAMPLE_TYPE_COLOURS else "other",
            "raw_type": raw_type or "unknown",
            "group": str(row.get("group_label") or "").strip(),
            "batch": str(row.get("batch_id") or "").strip(),
            "is_reference": str(row.get("is_reference") or "").strip().lower() == "true",
            "injection_order": order,
            "size_bytes": size,
            "position": position,
        })
    parsed.sort(key=lambda r: (r["injection_order"] is None,
                               r["injection_order"] if r["injection_order"] is not None else 0,
                               r["position"]))
    return parsed


def type_tally(samples: list[dict] | None) -> list[tuple[str, int]]:
    """Counts per type in the fixed slot order, omitting types not present."""
    counts = {t: 0 for t in TYPE_ORDER}
    for sample in samples or []:
        counts[sample["type"]] += 1
    return [(t, counts[t]) for t in TYPE_ORDER if counts[t]]


def short_acquisitions(samples: list[dict] | None, fraction: float = 0.5) -> list[dict]:
    """Samples whose RAW file is far smaller than the run's median.

    A truncated acquisition is the usual cause and is otherwise only noticed much
    later. Compared against the median rather than the mean so one short file does
    not drag the threshold down and hide the next.
    """
    sizes = sorted(s["size_bytes"] for s in (samples or []) if s["size_bytes"])
    if len(sizes) < 3:
        return []
    median = sizes[len(sizes) // 2]
    if not median:
        return []
    return [s for s in samples if s["size_bytes"] and s["size_bytes"] < median * fraction]


# Mark geometry. Bars are capped well under the band so the leftover is air, and
# grow from a single baseline: rounded data-end, square foot, 2px surface gap.
BAR_W, GAP, BAR_MAX, BAR_MIN = 12, 2, 46, 6


def hover_text(sample: dict) -> str:
    return " · ".join(p for p in [
        sample["name"], sample["raw_type"],
        f"injection {sample['injection_order']}" if sample["injection_order"] is not None else "",
        f"group {sample['group']}" if sample["group"] else "",
        f"batch {sample['batch']}" if sample["batch"] else "",
        f"{round((sample['size_bytes'] or 0) / 1e6)} MB" if sample["size_bytes"] else "",
        "RT reference" if sample["is_reference"] else "",
    ] if p)


def bar_height(sample: dict, largest: int) -> int:
    if not sample["size_bytes"] or not largest:
        return BAR_MIN
    return max(BAR_MIN, round(BAR_MAX * sample["size_bytes"] / largest))


def summary_text(samples: list[dict]) -> str:
    parts = [f"{n} {t}" for t, n in type_tally(samples)]
    return (f"{len(samples)} acquisitions in injection order: " + ", ".join(parts)
            + ". Bar height is RAW file size.")
