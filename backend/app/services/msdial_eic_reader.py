"""Lazy reader for MS-DIAL CSS1 aligned EIC archives.

MS-DIAL writes `AlignResult*.EIC.aef` as a binary CSS1 archive. The archive has
an offset table, so one aligned feature can be read without loading the whole
file into memory.
"""

from __future__ import annotations

import math
import struct
from pathlib import Path
from typing import BinaryIO


class MsdialEicFormatError(ValueError):
    """Raised when an MS-DIAL EIC archive cannot be decoded as CSS1."""


class MsdialEicArchiveReader:
    VERSION_SIZE = 10
    SPOT_HEADER = struct.Struct("<ffffBi")
    PEAK_HEADER = struct.Struct("<iifff")
    POINT = struct.Struct("<ff")
    CHROM_X_TYPES = {
        0: "rt",
        1: "ri",
        2: "drift",
        3: "mz",
    }

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def read_summary(self) -> dict:
        with self.path.open("rb") as fh:
            version, pointers = self._read_header(fh)
        return {"version": version, "feature_count": len(pointers)}

    def read_spot(self, feature_index: int, *, max_points_per_trace: int = 900) -> dict:
        with self.path.open("rb") as fh:
            version, pointers = self._read_header(fh)
            if feature_index < 0 or feature_index >= len(pointers):
                raise IndexError(f"Feature index {feature_index} is outside the EIC archive")
            fh.seek(pointers[feature_index])
            spot = self._read_spot_at_current_position(fh, max_points_per_trace=max_points_per_trace)
        return {"version": version, "feature_count": len(pointers), **spot}

    def _read_header(self, fh: BinaryIO) -> tuple[str, list[int]]:
        version = self._read_exact(fh, self.VERSION_SIZE).decode("ascii", errors="replace").rstrip("\0")
        if version != "CSS1":
            raise MsdialEicFormatError(f"Unsupported EIC archive version: {version or '<empty>'}")

        feature_count = struct.unpack("<i", self._read_exact(fh, 4))[0]
        if feature_count < 0:
            raise MsdialEicFormatError(f"Invalid EIC feature count: {feature_count}")

        pointer_bytes = self._read_exact(fh, feature_count * 8)
        pointers = list(struct.unpack(f"<{feature_count}q", pointer_bytes)) if feature_count else []
        return version, pointers

    def _read_spot_at_current_position(self, fh: BinaryIO, *, max_points_per_trace: int) -> dict:
        rt, ri, mz, drift, main_type_id, sample_count = self.SPOT_HEADER.unpack(self._read_exact(fh, self.SPOT_HEADER.size))
        if sample_count < 0:
            raise MsdialEicFormatError(f"Invalid EIC sample count: {sample_count}")

        peaks = [self._read_peak(fh, max_points_per_trace=max_points_per_trace) for _ in range(sample_count)]
        return {
            "center": {
                "rt": rt,
                "ri": ri,
                "mz": mz,
                "drift": drift,
                "main_type": self.CHROM_X_TYPES.get(main_type_id, f"unknown_{main_type_id}"),
            },
            "trace_count": sample_count,
            "peaks": peaks,
        }

    def _read_peak(self, fh: BinaryIO, *, max_points_per_trace: int) -> dict:
        file_id, point_count, top, left, right = self.PEAK_HEADER.unpack(self._read_exact(fh, self.PEAK_HEADER.size))
        if point_count < 0:
            raise MsdialEicFormatError(f"Invalid EIC point count: {point_count}")

        points = [self.POINT.unpack(self._read_exact(fh, self.POINT.size)) for _ in range(point_count)]
        sampled_points = self._downsample_points(points, max_points_per_trace)
        return {
            "file_id": file_id,
            "point_count": point_count,
            "returned_point_count": len(sampled_points),
            "top": top,
            "left": left,
            "right": right,
            "points": [{"x": x, "intensity": intensity} for x, intensity in sampled_points],
        }

    @staticmethod
    def _downsample_points(points: list[tuple[float, float]], max_points: int) -> list[tuple[float, float]]:
        if max_points <= 0 or len(points) <= max_points:
            return points
        step = max(1, math.ceil(len(points) / max_points))
        sampled = points[::step]
        if sampled[-1] != points[-1]:
            sampled.append(points[-1])
        return sampled

    @staticmethod
    def _read_exact(fh: BinaryIO, size: int) -> bytes:
        data = fh.read(size)
        if len(data) != size:
            raise MsdialEicFormatError("Unexpected end of EIC archive")
        return data
