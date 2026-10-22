#!/usr/bin/env python3
"""Pure retention-time correction math matching MS-DIAL 5 defaults."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from statistics import mean
from typing import Any


MIN_RT_STEP = 0.00001


def _points(anchors: Sequence[Mapping[str, float]]) -> list[tuple[float, float]]:
    if not anchors:
        raise ValueError("RT correction requires at least one usable anchor")
    points = [(float(anchor["observed_rt_min"]), float(anchor["shift_min"])) for anchor in anchors]
    points.sort(key=lambda item: item[0])
    if points[0][0] <= 0:
        raise ValueError("RT correction anchor RTs must be positive")
    if any(right[0] <= left[0] for left, right in zip(points, points[1:])):
        raise ValueError("RT correction anchor RTs must be strictly increasing")
    return [(0.0, 0.0), *points]


def interpolate_shift(rt_min: float, anchors: Sequence[Mapping[str, float]]) -> float:
    rt = float(rt_min)
    points = _points(anchors)
    if rt >= points[-1][0]:
        return points[-1][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if rt <= x1:
            return y0 + (rt - x0) / (x1 - x0) * (y1 - y0)
    return points[-1][1]


def correct_rt(rt_min: float, anchors: Sequence[Mapping[str, float]]) -> float:
    rt = float(rt_min)
    return rt - interpolate_shift(rt, anchors)


def correct_rt_axis(
    original_rts: Sequence[float],
    anchors: Sequence[Mapping[str, float]],
    min_step: float = MIN_RT_STEP,
) -> list[float]:
    corrected: list[float] = []
    previous = 0.0
    for original_rt in original_rts:
        current = correct_rt(float(original_rt), anchors)
        if previous >= current:
            current = round(previous + min_step, 12)
        corrected.append(current)
        previous = current
    return corrected


def _truthy(value: Any) -> bool:
    return value is True or str(value or "").strip().lower() in {"1", "true", "yes", "pass"}


def _positive_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def build_correction_plan(rows: list[dict[str, Any]], trace_payload: dict[str, Any]) -> dict[str, Any]:
    accepted = [
        {**row, "observed_rt_min": _positive_float(row.get("observed_rt_min"))}
        for row in rows
        if _truthy(row.get("passed")) and _positive_float(row.get("observed_rt_min")) is not None
    ]
    by_target: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in accepted:
        by_target[str(row.get("target_id") or row.get("target_name") or "")].append(row)
    target_means = {
        key: {
            "target_id": key,
            "target_name": values[0].get("target_name"),
            "mean_rt_min": mean(float(value["observed_rt_min"]) for value in values),
            "sample_count": len(values),
        }
        for key, values in by_target.items()
        if key
    }

    declared_samples = list(trace_payload.get("samples") or [])
    if not declared_samples:
        sample_keys = {
            (str(row.get("sample_name") or ""), str(row.get("input_file") or ""))
            for row in rows
        }
        declared_samples = [
            {"sample_name": name, "input_file": filename}
            for name, filename in sorted(sample_keys)
        ]

    sample_models: list[dict[str, Any]] = []
    model_by_file: dict[str, dict[str, Any]] = {}
    for sample in declared_samples:
        filename = str(sample.get("input_file") or "")
        candidates = []
        for row in accepted:
            if str(row.get("input_file") or "") != filename:
                continue
            target_key = str(row.get("target_id") or row.get("target_name") or "")
            target_mean = target_means.get(target_key)
            if target_mean:
                observed = float(row["observed_rt_min"])
                candidates.append({
                    "target_id": target_key,
                    "target_name": row.get("target_name"),
                    "observed_rt_min": observed,
                    "cohort_mean_rt_min": float(target_mean["mean_rt_min"]),
                    "shift_min": observed - float(target_mean["mean_rt_min"]),
                })
        counts = Counter(anchor["observed_rt_min"] for anchor in candidates)
        duplicate_rts = {rt for rt, count in counts.items() if count > 1}
        anchors = sorted(
            (anchor for anchor in candidates if anchor["observed_rt_min"] not in duplicate_rts),
            key=lambda anchor: anchor["observed_rt_min"],
        )
        warnings = []
        if duplicate_rts:
            warnings.append(f"Excluded ambiguous duplicate anchor RT(s): {sorted(duplicate_rts)}")
        if len(anchors) == 1:
            warnings.append("Only one usable internal standard supports this sample correction curve.")
        model = {
            "sample_name": sample.get("sample_name"),
            "input_file": filename,
            "eligible": bool(anchors),
            "anchor_count": len(anchors),
            "anchors": anchors,
            "warnings": warnings,
        }
        sample_models.append(model)
        model_by_file[filename] = model

    features = []
    for row in rows:
        target_key = str(row.get("target_id") or row.get("target_name") or "")
        filename = str(row.get("input_file") or "")
        observed = _positive_float(row.get("observed_rt_min"))
        model = model_by_file.get(filename)
        corrected = correct_rt(observed, model["anchors"]) if observed is not None and model and model["eligible"] else None
        target_mean = target_means.get(target_key)
        features.append({
            "target_id": target_key,
            "target_name": row.get("target_name"),
            "sample_name": row.get("sample_name"),
            "input_file": filename,
            "observed_rt_min": observed,
            "cohort_mean_rt_min": target_mean.get("mean_rt_min") if target_mean else None,
            "corrected_rt_min": corrected,
            "shift_min": observed - corrected if observed is not None and corrected is not None else None,
            "accepted": _truthy(row.get("passed")),
            "used_for_rt_model": True,
        })

    blocking_reasons = [
        f"Sample {sample['input_file']} has no usable RT correction anchor."
        for sample in sample_models
        if not sample["eligible"]
    ]
    return {
        "version": "msdial_rt_correction_v1",
        "method": {
            "rt_difference": "sample_minus_sample_average",
            "interpolation": "linear",
            "beginning": "zero_at_rt_zero",
            "ending": "last_shift",
            "smoothing": False,
            "minimum_step_min": MIN_RT_STEP,
        },
        "can_apply": bool(sample_models) and not blocking_reasons,
        "blocking_reasons": blocking_reasons,
        "target_means": target_means,
        "samples": sample_models,
        "features": features,
    }


def select_correction_plan(
    plan: dict[str, Any],
    selected_target_ids: Sequence[str] | None,
) -> dict[str, Any]:
    """Return a correction plan using only the selected compound anchors."""

    known_ids = list(dict.fromkeys(
        str(feature.get("target_id") or "")
        for feature in plan.get("features", [])
        if str(feature.get("target_id") or "")
    ))
    selected_ids = known_ids if selected_target_ids is None else list(dict.fromkeys(
        str(target_id) for target_id in selected_target_ids if str(target_id)
    ))
    unknown = [target_id for target_id in selected_ids if target_id not in known_ids]
    if unknown:
        raise ValueError(f"Unknown RT correction target ID(s): {', '.join(unknown)}")
    selected = set(selected_ids)

    samples = []
    model_by_file = {}
    for source in plan.get("samples", []):
        anchors = [
            dict(anchor) for anchor in source.get("anchors", [])
            if str(anchor.get("target_id") or "") in selected
        ]
        warnings = [
            warning for warning in source.get("warnings", [])
            if "Only one usable internal standard" not in warning
        ]
        if len(anchors) == 1:
            warnings.append("Only one usable internal standard supports this sample correction curve.")
        sample = {
            **source,
            "eligible": bool(anchors),
            "anchor_count": len(anchors),
            "anchors": anchors,
            "warnings": warnings,
        }
        samples.append(sample)
        model_by_file[str(sample.get("input_file") or "")] = sample

    features = []
    for source in plan.get("features", []):
        feature = dict(source)
        target_id = str(feature.get("target_id") or "")
        observed = _positive_float(feature.get("observed_rt_min"))
        model = model_by_file.get(str(feature.get("input_file") or ""))
        corrected = (
            correct_rt(observed, model["anchors"])
            if observed is not None and model and model["eligible"] else None
        )
        feature.update({
            "corrected_rt_min": corrected,
            "shift_min": observed - corrected if observed is not None and corrected is not None else None,
            "used_for_rt_model": target_id in selected,
        })
        features.append(feature)

    blocking_reasons = []
    if not selected_ids:
        blocking_reasons.append("No internal standards are selected for RT correction.")
    blocking_reasons.extend(
        f"Sample {sample['input_file']} has no usable selected RT correction anchor."
        for sample in samples
        if not sample["eligible"]
    )
    return {
        **plan,
        "can_apply": bool(samples) and not blocking_reasons,
        "blocking_reasons": blocking_reasons,
        "selected_target_ids": selected_ids,
        "samples": samples,
        "features": features,
    }


def build_preview(plan: dict[str, Any], trace_payload: dict[str, Any]) -> dict[str, Any]:
    models = {sample["input_file"]: sample for sample in plan.get("samples", [])}
    features = {
        (str(feature.get("target_id") or ""), str(feature.get("input_file") or "")): feature
        for feature in plan.get("features", [])
    }
    traces = []
    for source in trace_payload.get("traces", []):
        trace = dict(source)
        key = (str(trace.get("target_id") or ""), str(trace.get("input_file") or ""))
        feature = features.get(key, {})
        model = models.get(str(trace.get("input_file") or ""))
        trace["cohort_mean_rt_min"] = feature.get("cohort_mean_rt_min")
        trace["corrected_observed_rt_min"] = feature.get("corrected_rt_min")
        trace["rt_shift_min"] = feature.get("shift_min")
        trace["points"] = [
            {
                **point,
                "corrected_rt_min": correct_rt(float(point["rt_min"]), model["anchors"])
                if model and model.get("eligible") else None,
            }
            for point in trace.get("points", [])
        ]
        traces.append(trace)
    return {**trace_payload, "version": "msdial_rt_correction_preview_v1", "traces": traces}
