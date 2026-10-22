#!/usr/bin/env python3
"""Use pinned MetaboLensR and build other scientific images for new launches."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.build_metabolensr_image import build_image
from scripts.export_metabolensr_profiles import export_profiles

WORKFLOW = ROOT / "workflows/nextflow4ms-dial-msdial5-clean"
IMAGE_PATTERN = r"(?:[a-z0-9][a-z0-9._:/-]*@)?sha256:[0-9a-f]{64}"
BUILDS = {
    "msdial5_container_image": ("msdial5", "Dockerfile", []),
    "stats_adapter_container_image": ("stats-adapter", "Dockerfile.stats_adapter", ["adapters/mdalign_to_stats_txt.py"]),
    "is_qc_container_image": ("is-qc", "Dockerfile.is_qc", []),
    "masscube_container_image": ("masscube", "Dockerfile.masscube", ["masscube-requirements.txt"]),
    "sirius_container_image": ("sirius", "Dockerfile.sirius", []),
}


def immutable_image(image: dict) -> str:
    """Classic Docker has no RepoDigest after a local build; its full ID is exact."""
    reference = next(iter(image.get("RepoDigests") or []), image.get("Id", ""))
    if not re.fullmatch(IMAGE_PATTERN, reference):
        raise ValueError("Docker did not return an immutable image identity")
    return reference


def prepare_metabolensr(descriptors: list, *, platform: str, package_source: Path,
                       tag: str, image: str = None, build: bool = False) -> dict:
    """Resolve and validate the package before changing launch descriptors."""
    if platform != "linux/amd64":
        raise ValueError("MetaboLensR supports only the tested linux/amd64 platform")
    if build and image:
        raise ValueError("Choose a prebuilt image or a source build, not both")
    source = json.loads((package_source / "SOURCE.json").read_text())
    if any((item["package"]["commit"], item["package"]["version"]) != (source["commit"], source["version"])
           for item in descriptors):
        raise ValueError("Vendored package identity does not match the engine descriptors")
    record = {}
    if build:
        record = build_image(tag=tag, platform=platform, package_source=package_source)
        reference = immutable_image({"Id": record["image_id"], "RepoDigests": record["repo_digests"]})
    else:
        if image is None:
            references = {item["package"]["container_image"] for item in descriptors}
            if len(references) != 1:
                raise ValueError("Engine descriptors must agree on the prebuilt MetaboLensR image")
            image = references.pop()
        reference = image
    if not re.fullmatch(IMAGE_PATTERN, reference):
        raise ValueError("MetaboLensR image must use an immutable repository digest or local sha256 image ID")
    try:
        inspected = json.loads(subprocess.check_output(
            ["docker", "image", "inspect", reference], text=True, stderr=subprocess.DEVNULL))[0]
    except subprocess.CalledProcessError:
        unavailable = "Pinned MetaboLensR image is unavailable. Load its image archive or use --build-metabolensr."
        if reference.startswith("sha256:"):
            raise ValueError(unavailable) from None
        try:
            subprocess.run(["docker", "pull", "--platform", platform, reference], check=True)
            inspected = json.loads(subprocess.check_output(["docker", "image", "inspect", reference], text=True))[0]
        except subprocess.CalledProcessError:
            raise ValueError(unavailable) from None
    if f"{inspected.get('Os')}/{inspected.get('Architecture')}" != platform:
        raise ValueError("MetaboLensR image platform does not match the requested platform")
    with TemporaryDirectory(prefix="metabolens-public-package-") as temporary:
        for index, descriptor in enumerate(descriptors):
            candidate = {**descriptor, "package": {**descriptor["package"], "container_image": reference}}
            path = Path(temporary) / f"{index}.json"
            path.write_text(json.dumps(candidate))
            export_profiles(path, check=True)
    record.update(mode="source" if build else "prebuilt", identity=reference, platform=platform,
                  image_id=inspected["Id"], repo_digests=inspected.get("RepoDigests", []),
                  package_commit=source["commit"], package_version=source["version"])
    return record


def update_descriptors(paths: list, images: dict) -> None:
    """Validate both package catalogs before replacing either launch descriptor."""
    with TemporaryDirectory(prefix="metabolens-public-descriptors-") as temporary:
        prepared = []
        for index, path in enumerate(paths):
            descriptor = json.loads(path.read_text())
            descriptor["container_images"] = {key: images[key] for key in descriptor["container_images"]}
            descriptor["package"]["container_image"] = images["metabolensr_container_image"]
            candidate = Path(temporary) / f"{index}.json"
            candidate.write_text(json.dumps(descriptor, indent=2) + "\n")
            export_profiles(candidate, check=True)
            prepared.append((path, candidate.read_text()))
        for path, content in prepared:
            candidate = path.with_suffix(".json.tmp")
            candidate.write_text(content)
            candidate.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-source", type=Path, default=ROOT / "third_party/MetaboLensR")
    package_mode = parser.add_mutually_exclusive_group()
    package_mode.add_argument("--metabolensr-image", help="Prebuilt immutable digest or local image ID; defaults to the engine descriptors")
    package_mode.add_argument("--build-metabolensr", action="store_true", help="Explicitly build MetaboLensR from the checksummed source instead")
    parser.add_argument("--tag-prefix", default="metabolens-public")
    parser.add_argument("--platform", choices=["linux/amd64"], default="linux/amd64")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._/-]*", args.tag_prefix):
        parser.error("--tag-prefix must be a lowercase Docker repository prefix")
    paths = [WORKFLOW / "engines" / engine / "engine.json" for engine in ("msdial5", "masscube")]
    descriptors = [json.loads(path.read_text()) for path in paths]
    suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    package = prepare_metabolensr(descriptors, platform=args.platform, package_source=args.package_source,
        tag=f"{args.tag_prefix}-metabolensr:{suffix}", image=args.metabolensr_image, build=args.build_metabolensr)
    images = {"metabolensr_container_image": package["identity"]}
    records = {"metabolensr_container_image": package}
    for parameter, (name, dockerfile, sources) in BUILDS.items():
        tag = f"{args.tag_prefix}-{name}:{suffix}"
        # An allowlisted scratch context cannot upload runtime files or login state.
        with TemporaryDirectory(prefix="metabolens-public-image-") as temporary:
            context = Path(temporary)
            shutil.copyfile(WORKFLOW / dockerfile, context / "Dockerfile")
            for relative in sources:
                destination = context / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(WORKFLOW / relative, destination)
            subprocess.run(["docker", "build", "--platform", args.platform, "--tag", tag, str(context)], check=True)
        inspected = json.loads(subprocess.check_output(["docker", "image", "inspect", tag], text=True))[0]
        images[parameter] = immutable_image(inspected)
        records[parameter] = {"tag": tag, "identity": images[parameter]}
    converter = descriptors[0]["container_images"]["thermorawfileparser_image"]
    if not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", converter):
        raise ValueError("ThermoRawFileParser must retain its public repository digest")
    subprocess.run(["docker", "pull", "--platform", args.platform, converter], check=True)
    images["thermorawfileparser_image"] = converter
    record_path = ROOT / "runtime/image-setup" / f"{suffix}.json"
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps({"platform": args.platform, "images": images, "builds": records}, indent=2) + "\n")
    update_descriptors(paths, images)
    print(f"Scientific image setup complete. Build record: {record_path}")
    print("Recreate application containers before new launches. Existing job contracts remain unchanged.")


if __name__ == "__main__":
    main()
