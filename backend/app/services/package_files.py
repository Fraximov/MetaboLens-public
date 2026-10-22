"""Read-only views of what a package revision sends to MetaboLights: its submission folder.

Listing never reads through a link. Linked spectra are listed with the registered
source and checksum recorded at build, and their target is only checked to exist.
Previews parse the ISA files the exporter wrote; other files have none. Paths are
confined to ``submission/``. Revisions never change, so neither do these views.
"""
import csv
from pathlib import Path, PurePosixPath
import re

from app.core.errors import bad_request, not_found

SUBMISSION = "submission"
MAF_PREVIEW_ROWS = 20
TABLE_ROW_LIMIT = 1000
TABLE_BYTE_LIMIT = 20 * 1024 * 1024  # sample and assay tables are read whole
ROLES = (("i_", ".txt", "investigation"), ("s_", ".txt", "samples"), ("a_", ".txt", "assay"), ("m_", ".tsv", "maf"))
REFERENCE_HEADERS = {"Unit", "Term Source REF", "Term Accession Number"}
# The validator names a reference column by its owner: "Term Source REF (of Characteristics[Organism part])".
# Its column_index for these points one column further, so findings are placed by name.
OWNED = re.compile(r"(Unit|Term Source REF|Term Accession Number) \(of (.+)\)")


def role(name: str) -> str | None:
    return next((kind for prefix, suffix, kind in ROLES if name.startswith(prefix) and name.endswith(suffix)), None)


def submission_folder(folder: Path) -> Path:
    submission = folder / SUBMISSION
    if submission.is_symlink() or not submission.is_dir():
        raise not_found("This revision has no submission folder")
    return submission


def spectra_records(folder: Path) -> dict:
    """The build's file-map.tsv rows, keyed by their path under submission/."""
    path = folder / "provenance" / "file-map.tsv"
    if path.is_symlink() or not path.is_file():
        return {}
    with path.open(newline="", encoding="utf-8") as stream:
        return {row["export_path"]: row for row in csv.DictReader(stream, delimiter="\t")}


def findings_for(issues: list[dict], relative: str) -> list[dict]:
    """Findings whose file is this path; the validator names top-level ISA files by their bare name."""
    return [issue for issue in issues if issue.get("file") in {relative, PurePosixPath(relative).name}]


def counts(found: list[dict]) -> dict:
    errors = sum(issue.get("severity") == "error" for issue in found)
    return dict(errors=errors, warnings=len(found) - errors)


def entry(path: Path, submission: Path, spectra: dict, issues: list[dict]) -> dict:
    relative = path.relative_to(submission).as_posix()
    kind = "link" if path.is_symlink() else "dir" if path.is_dir() else "file"
    item = dict(path=relative, name=path.name, kind=kind, role=role(path.name) if kind == "file" else None,
                size=path.lstat().st_size if kind == "file" else None, **counts(findings_for(issues, relative)))
    record = spectra.get(relative)
    if kind == "link" or record:
        try:
            size = path.stat().st_size  # metadata only; the content is never read here
        except OSError:
            size = None
        item.update(role="spectrum", size=size, available=size is not None,
                    source=(record or {}).get("source_uri") or str(path.readlink()),
                    sha256=(record or {}).get("sha256"), verification=(record or {}).get("verification"))
    return item


def listing(folder: Path, issues: list[dict]) -> list[dict]:
    """Every entry under submission/, depth first and sorted by name, without entering links."""
    submission = submission_folder(folder)
    spectra = spectra_records(folder)

    def walk(directory: Path):
        for path in sorted(directory.iterdir(), key=lambda p: p.name.lower()):
            yield entry(path, submission, spectra, issues)
            if path.is_dir() and not path.is_symlink():
                yield from walk(path)

    return list(walk(submission))


def resolve(folder: Path, relative: str) -> Path:
    """A regular file under submission/, reached without passing through a link."""
    parts = PurePosixPath(relative).parts
    if not parts or PurePosixPath(relative).is_absolute() or any(part in {".", ".."} for part in parts):
        raise bad_request("Not a file of this revision")
    path = submission_folder(folder)
    for part in parts:
        path = path / part
        if path.is_symlink():
            raise bad_request("Linked spectra have no preview")
    if not path.is_file():
        raise not_found("No such file in this revision")
    return path


def investigation_sections(path: Path) -> list[dict]:
    """i_Investigation.txt as its sections: an upper-case line, then one row per field."""
    sections = []
    with path.open(newline="", encoding="utf-8", errors="replace") as stream:
        for row in csv.reader(stream, delimiter="\t"):
            if not any(cell.strip() for cell in row):
                continue
            if len(row) == 1 and row[0].isupper():
                sections.append(dict(name=row[0], fields=[]))
                continue
            if not sections:
                sections.append(dict(name="", fields=[]))
            sections[-1]["fields"].append(dict(label=row[0], values=row[1:]))
    return sections


def read_table(path: Path, limit: int) -> tuple[list[str], list[list[str]], int]:
    """Header, the first ``limit`` rows and the total row count."""
    with path.open(newline="", encoding="utf-8", errors="replace") as stream:
        reader = csv.reader(stream, delimiter="\t")
        header = next(reader, [])
        rows, total = [], 0
        for row in reader:
            total += 1
            if len(rows) < limit:
                rows.append(row)
    return header, rows, total


def column_of(header: list[str], issue: dict) -> int | None:
    """The column a finding names, or None when the name does not identify one."""
    name = issue.get("column") or ""
    owned = OWNED.fullmatch(name)
    if owned:
        reference, owner = owned.groups()
        if header.count(owner) != 1:
            return None
        for index in range(header.index(owner) + 1, len(header)):
            if header[index] == reference:
                return index
            if header[index] not in REFERENCE_HEADERS:
                return None
        return None
    if name and header.count(name) == 1:
        return header.index(name)
    try:
        index = int(issue.get("column_index"))
    except (TypeError, ValueError):
        return None
    return index if 0 <= index < len(header) and header[index] == name else None


def compact(issue: dict, column: int | None = None) -> dict:
    return dict(code=issue.get("code", ""), severity=issue.get("severity", ""), message=issue.get("message", ""),
                column=issue.get("column") or "", column_index=column)


def preview(folder: Path, relative: str, issues: list[dict]) -> dict:
    """One ISA file parsed for reading, with its findings placed on their columns where possible."""
    path = resolve(folder, relative)
    kind = role(path.name)
    if kind is None or PurePosixPath(relative).parent != PurePosixPath("."):
        raise bad_request("Only the ISA files have a preview")
    found = findings_for(issues, relative)
    if kind == "investigation":
        return dict(path=relative, role=kind, sections=investigation_sections(path), findings=[compact(i) for i in found])
    if kind != "maf" and path.stat().st_size > TABLE_BYTE_LIMIT:
        raise bad_request("This table is too large to preview")
    header, rows, total = read_table(path, MAF_PREVIEW_ROWS if kind == "maf" else TABLE_ROW_LIMIT)
    return dict(path=relative, role=kind, columns=header, rows=rows, total_rows=total,
                findings=[compact(issue, column_of(header, issue)) for issue in found])
