#!/usr/bin/env python3
"""Crop an mzML file to a retention-time window and m/z range, for small test fixtures.

Keeps the MS1 scans inside the window, with only their peaks inside the m/z
range, and the MS2 scans inside the window whose precursor lies in the m/z
range. Retention times are unchanged, so library retention times still match.
The output is plain (non-indexed) mzML without chromatograms. Uses only the
Python standard library.

    python3 scripts/make_toy_mzml.py QC_03.mzML toy/QC_03.mzML --rt 4.4 7.9 --mz 70 400
"""

import argparse
import base64
import re
import sys
import xml.etree.ElementTree as ET
import zlib
from array import array
from pathlib import Path

SPECTRUM = re.compile(r"<spectrum\s.*?</spectrum>", re.DOTALL)
CHROMATOGRAMS = re.compile(r"\s*<chromatogramList\b.*?</chromatogramList>", re.DOTALL)


def cv_params(element):
    return {param.get("name"): param for param in element.iter("cvParam")}


def retention_minutes(spectrum) -> float:
    param = cv_params(spectrum)["scan start time"]
    value = float(param.get("value"))
    return value / 60 if param.get("unitName") == "second" else value


def decode(binary_array) -> array:
    params = cv_params(binary_array)
    data = base64.b64decode(binary_array.find("binary").text or "")
    if "zlib compression" in params:
        data = zlib.decompress(data)
    values = array("d" if "64-bit float" in params else "f")
    values.frombytes(data)
    if sys.byteorder == "big":
        values.byteswap()
    return values


def encode(binary_array, values: array) -> None:
    params = cv_params(binary_array)
    if sys.byteorder == "big":
        values = array(values.typecode, values)
        values.byteswap()
    data = values.tobytes()
    if "zlib compression" in params:
        data = zlib.compress(data)
    text = base64.b64encode(data).decode("ascii")
    binary_array.find("binary").text = text
    binary_array.set("encodedLength", str(len(text)))


def crop_ms1(spectrum, mz_low: float, mz_high: float) -> str:
    """Keep only the MS1 peaks inside the m/z range and refresh the summary values."""

    arrays = {}
    for binary_array in spectrum.iter("binaryDataArray"):
        params = cv_params(binary_array)
        kind = "mz" if "m/z array" in params else "intensity" if "intensity array" in params else None
        if kind:
            arrays[kind] = (binary_array, decode(binary_array))
    mz_array, mz = arrays["mz"]
    intensity_array, intensity = arrays["intensity"]
    keep = [i for i, value in enumerate(mz) if mz_low <= value <= mz_high]
    mz = array(mz.typecode, (mz[i] for i in keep))
    intensity = array(intensity.typecode, (intensity[i] for i in keep))
    encode(mz_array, mz)
    encode(intensity_array, intensity)
    spectrum.set("defaultArrayLength", str(len(keep)))

    summary = cv_params(spectrum)
    base_peak = max(range(len(intensity)), key=intensity.__getitem__) if keep else None
    updates = {
        "lowest observed m/z": min(mz) if keep else 0.0,
        "highest observed m/z": max(mz) if keep else 0.0,
        "base peak m/z": mz[base_peak] if keep else 0.0,
        "base peak intensity": intensity[base_peak] if keep else 0.0,
        "total ion current": sum(intensity),
    }
    for name, value in updates.items():
        if name in summary:
            summary[name].set("value", repr(float(value)))
    return ET.tostring(spectrum, encoding="unicode")


def crop(source: Path, target: Path, rt_low: float, rt_high: float, mz_low: float, mz_high: float) -> dict:
    text = source.read_text(encoding="utf-8")
    start, end = text.index("<mzML"), text.index("</mzML>") + len("</mzML>")
    body = CHROMATOGRAMS.sub("", text[start:end])
    list_start = body.index("<spectrumList")
    list_end = body.index("</spectrumList>")
    head, spectra, tail = body[:list_start], body[list_start:list_end], body[list_end:]

    kept, counts = [], {"1": 0, "2": 0}
    for match in SPECTRUM.finditer(spectra):
        block = match.group(0)
        spectrum = ET.fromstring(block)
        if not rt_low <= retention_minutes(spectrum) <= rt_high:
            continue
        level = cv_params(spectrum)["ms level"].get("value")
        if level == "1":
            block = crop_ms1(spectrum, mz_low, mz_high)
        else:
            precursor = cv_params(spectrum).get("selected ion m/z")
            if precursor is None or not mz_low <= float(precursor.get("value")) <= mz_high:
                continue
        block = re.sub(r'(<spectrum\b[^>]*?\bindex=")\d+(")', rf"\g<1>{len(kept)}\g<2>", block, count=1)
        kept.append(block)
        counts[level] = counts.get(level, 0) + 1

    opening = spectra[: spectra.index(">") + 1]
    opening = re.sub(r'\bcount="\d+"', f'count="{len(kept)}"', opening, count=1)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        + head + opening + "\n" + "\n".join(kept) + "\n" + tail + "\n",
        encoding="utf-8",
    )
    return {"ms1": counts.get("1", 0), "ms2": counts.get("2", 0), "bytes": target.stat().st_size}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--rt", nargs=2, type=float, required=True, metavar=("START", "END"), help="minutes")
    parser.add_argument("--mz", nargs=2, type=float, default=(0.0, float("inf")), metavar=("LOW", "HIGH"))
    args = parser.parse_args()
    result = crop(args.source, args.target, *args.rt, *args.mz)
    print(f"{args.target}: {result['ms1']} MS1 + {result['ms2']} MS2 scans, {result['bytes'] / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
