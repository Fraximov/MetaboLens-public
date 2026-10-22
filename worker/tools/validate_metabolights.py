"""Offline official validation, run by the isolated MetaboLights Python runtime.

No credentials, uploads, rule overrides, or runtime downloads. The model includes
the actual FILES inventory. A successful process exit alone never means valid ISA.
"""
import argparse
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import subprocess

from metabolights_utils.provider.local_folder_metadata_collector import LocalFolderMetadataCollector
from metabolights_utils.provider.study_provider import MetabolightsStudyProvider
from metabolights_utils.provider.submission_model import ValidationResult


def validate(submission: Path, output: Path, resources: Path, opa: str, study_id: str):
    output.mkdir(parents=True, exist_ok=True)
    lock = json.loads((resources / "lock.json").read_text())
    if version("metabolights-utils") != lock["metabolights_utils"]:
        raise ValueError("Unexpected MetaboLights reader version")
    for name, expected in lock["files"].items():
        if hashlib.sha256((resources / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Changed official template or policy: {name}")
    opa_version = subprocess.run([opa, "version"], check=True, capture_output=True, text=True, timeout=10).stdout
    if f"Version: {lock['opa_version']}\n" not in opa_version:
        raise ValueError("Unexpected OPA runtime version")
    provider = MetabolightsStudyProvider(db_metadata_collector=None,
                                        folder_metadata_collector=LocalFolderMetadataCollector())
    model = provider.load_study(
        study_id or "local-draft", study_path=str(submission), connection=None,
        load_assay_files=True, load_sample_file=True, load_maf_files=True, load_folder_metadata=True,
        calculate_data_folder_size=True, calculate_metadata_size=True,
        data_files_path=str(submission / "FILES"), data_files_mapping_folder_name="FILES")
    model_path = output / "official-input.json"
    model_data = json.loads(model.model_dump_json(by_alias=True))
    # utils 1.4.35 adds this empty optional field; the published 2.2.0 policy
    # predates it. Omit only an empty value, never supplied scientific metadata.
    for study in model_data["investigation"]["studies"]:
        if study.get("linkedStudyAccession"):
            raise ValueError("Policy 2.2.0 does not support linkedStudyAccession")
        study.pop("linkedStudyAccession", None)
    model_path.write_text(json.dumps(model_data, indent=2))
    process = subprocess.run([opa, "eval", "--data", str(resources / "bundle.tar.gz"),
        "data.metabolights.validation.v2.report.complete_report", "-i", str(model_path)],
        check=True, capture_output=True, text=True, timeout=120)
    raw = json.loads(process.stdout)["result"][0]["expressions"][0]["value"]
    # Validate upstream shape before interpreting it. A missing report is an error.
    official = ValidationResult.model_validate(raw).model_dump(mode="json", by_alias=True)
    (output / "official-report.json").write_text(json.dumps(official, indent=2) + "\n")
    parser_messages = model_data.get("parserMessages", {})
    report = dict(completed=True, tools=lock, opa=opa_version, parser_messages=parser_messages,
                  violations=official["violations"], scope="Local ISA metadata and file inventory; no spectral-content or remote validation.")
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("submission", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("resources", type=Path)
    parser.add_argument("--opa", default="/opt/metabolights/bin/opa")
    parser.add_argument("--study-id", default="")
    args = parser.parse_args()
    validate(args.submission, args.output, args.resources, args.opa, args.study_id)
