"""Materialize a Study package: copy linked spectra and re-verify every file.

Packages link registered spectral files instead of copying them into each
revision. Export writes one self-contained folder for transfer or upload. Every
file listed in the package inventory is re-hashed while copying; a missing,
moved or changed source stops the export, and the incomplete copy stays under a
``.partial`` name.

Run inside the worker, where registered source paths resolve:

    python -m services.study_package_export <package-folder> <new-destination>
"""
import argparse
import hashlib
import json
from pathlib import Path


def export_package(package: Path, destination: Path) -> dict:
    inventory = {entry["path"]: entry for entry in
                 json.loads((package / "provenance/manifest.json").read_text())["files"]}
    if destination.exists():
        raise FileExistsError(f"Export destination already exists: {destination}")
    partial = destination.with_name(destination.name + ".partial")
    partial.mkdir(parents=True)
    seen, linked = set(), 0
    for path in sorted(package.rglob("*")):
        if path.is_dir() and not path.is_symlink():
            continue
        relative = path.relative_to(package).as_posix()
        target = partial / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        digest, size = hashlib.sha256(), 0
        try:
            with path.open("rb") as incoming, target.open("xb") as outgoing:
                for block in iter(lambda: incoming.read(1024 * 1024), b""):
                    digest.update(block)
                    size += len(block)
                    outgoing.write(block)
        except FileNotFoundError as error:
            raise ValueError(f"Linked source is unavailable: {relative}") from error
        expected = inventory.get(relative)
        # Reports, the metadata download and the final manifest are written after
        # the inventory, so only inventory entries carry a checksum to verify.
        if expected and (digest.hexdigest() != expected["sha256"] or size != expected["size_bytes"]):
            raise ValueError(f"Checksum or size changed since the package was built: {relative}")
        seen.add(relative)
        linked += path.is_symlink()
    absent = sorted(set(inventory) - seen)
    if absent:
        raise ValueError(f"Package inventory files are missing: {', '.join(absent[:5])}")
    partial.rename(destination)
    return dict(destination=str(destination), files=len(seen), linked_files_copied=linked)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("package", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(json.dumps(export_package(args.package, args.destination), indent=2))
