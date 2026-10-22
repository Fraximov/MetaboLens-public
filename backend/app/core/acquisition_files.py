"""Acquisition file formats the MS-DIAL workflow can consume.

`prepare_msdial5_input` converts Thermo RAW to mzML and passes mzML through
unchanged, so both are valid inputs to upload, register, and import. Files keep
the `thermo_raw` role regardless of format; the role names the acquisition slot,
not the vendor container.
"""

from pathlib import Path

ACQUISITION_SUFFIXES = (".raw", ".mzml")
ACQUISITION_SUFFIX_LABEL = ".raw or .mzML"


def is_acquisition_file(filename: str) -> bool:
    """True when a file name carries an accepted acquisition extension."""

    return Path(filename).suffix.lower() in ACQUISITION_SUFFIXES
