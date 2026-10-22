#!/usr/bin/env python3
"""Build a local MetaboLensR image from a committed package, never a live tree."""

import argparse
import gzip
import hashlib
import io
import json
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path


def export_package(repository: Path, revision: str, archive: Path) -> dict:
    commit = subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"],
        text=True,
    ).strip()
    with archive.open("wb") as output:
        subprocess.run(
            ["git", "-C", str(repository), "archive", "--format=tar.gz", "--prefix=MetaboLensR/", commit],
            stdout=output, check=True,
        )
    return {"package_commit": commit, "package_sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}


def export_vendored_package(source: Path, archive: Path) -> dict:
    """Archive only the release's listed source bytes, rejecting changes and links."""
    source = source.resolve()
    metadata = json.loads((source / "SOURCE.json").read_text())
    if not re.fullmatch(r"[0-9a-f]{40}", metadata["commit"]):
        raise ValueError("Invalid package source commit")
    files = metadata["files_sha256"]
    if not isinstance(files, dict) or "DESCRIPTION" not in files:
        raise ValueError("Package source inventory must include DESCRIPTION")
    with archive.open("wb") as output, gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode="w") as bundle:
            for relative, expected in sorted(files.items()):
                path = source / relative
                if Path(relative).is_absolute() or ".." in Path(relative).parts or path.resolve() != path.absolute():
                    raise ValueError(f"Unsafe package source path: {relative}")
                content = path.read_bytes()
                if hashlib.sha256(content).hexdigest() != expected:
                    raise ValueError(f"Package source checksum mismatch: {relative}")
                if relative == "DESCRIPTION" and f"Version: {metadata['version']}" not in content.decode().splitlines():
                    raise ValueError("Package source version mismatch")
                entry = tarfile.TarInfo("MetaboLensR/" + relative)
                entry.size, entry.mode = len(content), 0o644
                bundle.addfile(entry, io.BytesIO(content))
    return {"package_commit": metadata["commit"], "package_sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}


def build_image(*, tag: str, platform: str, package_source: Path = None,
                package_repo: Path = None, commit: str = None) -> dict:
    dockerfile = Path(__file__).resolve().parents[1] / "workflows/nextflow4ms-dial-msdial5-clean/Dockerfile.metabolensr"
    with tempfile.TemporaryDirectory(prefix="metabolensr-build-") as directory:
        context = Path(directory)
        archive = context / "MetaboLensR.tar.gz"
        record = (export_vendored_package(package_source.resolve(), archive) if package_source else
                  export_package(package_repo.resolve(), commit, archive))
        shutil.copyfile(dockerfile, context / "Dockerfile")
        shutil.copyfile(dockerfile.parent / "tests/check_plotly_runtime.R", context / "check_plotly_runtime.R")
        subprocess.run([
            "docker", "build", "--platform", platform, "--tag", tag,
            "--build-arg", f"PACKAGE_COMMIT={record['package_commit']}",
            "--build-arg", f"PACKAGE_SHA256={record['package_sha256']}", str(context),
        ], check=True)
    image = json.loads(subprocess.check_output(["docker", "image", "inspect", tag], text=True))[0]
    record.update(tag=tag, platform=platform, image_id=image["Id"], repo_digests=image.get("RepoDigests", []),
                  dockerfile_sha256=hashlib.sha256(dockerfile.read_bytes()).hexdigest())
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--package-repo", type=Path)
    source.add_argument("--package-source", type=Path)
    parser.add_argument("--commit", help="Required with --package-repo")
    parser.add_argument("--platform", default="linux/amd64")
    parser.add_argument("--tag", required=True)
    parser.add_argument("--record", type=Path, required=True, help="Write exact package and local image identity as JSON")
    args = parser.parse_args()
    if args.package_repo and not args.commit:
        parser.error("--package-repo requires --commit")
    record = build_image(tag=args.tag, platform=args.platform, package_source=args.package_source,
                         package_repo=args.package_repo, commit=args.commit)
    args.record.parent.mkdir(parents=True, exist_ok=True)
    args.record.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
