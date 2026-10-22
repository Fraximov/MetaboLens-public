"""Create synthetic dual/single-assay packages and exercise the official policy.

Run in the worker image from the repository root. The output must be a new
directory. Incomplete and complete synthetic metadata are both exercised.
These fixtures check file plumbing, not a pretend accepted MetaboLights study.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests")]
from test_metabolights_export import example_snapshot, with_example_metadata
from app.services.metabolights_export import TEMPLATES, build_package


def complete_example(snapshot):
    """A fabricated local validation example, never an assigned repository study."""
    example = with_example_metadata(snapshot)
    example.title = "SYNTHETIC EXAMPLE — Human plasma HILIC Orbitrap LC-MS"
    # Twenty zeroes are only a syntax fixture, not a MetaboLights request number.
    example.metabolights_id = "REQ00000000000000000000"
    example.description += " The all-zero REQ identifier and specimen metadata are fabricated for local validation only."
    example.factors[0]["type"] = {"name": "treatment", "source": "EFO",
        "accession": "http://www.ebi.ac.uk/efo/EFO_0000727"}
    for sample in example.samples:
        blank = sample.sample_type == "blank"
        sample.organism = "blank" if blank else "Homo sapiens"
        sample.organism_part = "blank" if blank else "blood plasma"
        sample.sample_type = {1: "experimental sample", 2: "pooled quality control sample", 3: "solvent blank"}[sample.sample_id]
        sample.ontology_terms = {
            "organism": {"name": sample.organism, "source": "MTBLS" if blank else "NCBITaxon",
                "accession": "http://www.ebi.ac.uk/metabolights/ontology/MTBLS_002304" if blank else "http://purl.obolibrary.org/obo/NCBITaxon_9606"},
            "organism_part": {"name": sample.organism_part, "source": "MTBLS" if blank else "BTO",
                "accession": "http://www.ebi.ac.uk/metabolights/ontology/MTBLS_002304" if blank else "http://purl.obolibrary.org/obo/BTO_0000131"},
            "sample_type": {1: {"source": "CHMO", "accession": "http://purl.obolibrary.org/obo/CHMO_0002746"},
                            2: {},  # The pinned policy lists this exact QC label without an accession.
                            3: {"source": "MSIO", "accession": "http://purl.obolibrary.org/obo/MSIO_0000026"}}[sample.sample_id],
        }
    for assay in example.assays:
        assay.method = {
            "Parameter Value[Instrument]": "Thermo Scientific Orbitrap Exploris 240",
            "Parameter Value[Ion source]": "electrospray ionization",
            "Parameter Value[Chromatography Instrument]": "Thermo Scientific Vanquish UHPLC System",
            "Parameter Value[Column model]": "Waters ACQUITY UPLC BEH Amide",
            "Parameter Value[Column type]": "HILIC",
            "Parameter Value[Scan m/z range]": "70-1000",
        }
        assay.method_ontology_terms = {
            "Parameter Value[Instrument]": {"source": "MTBLS", "accession": "http://www.ebi.ac.uk/metabolights/ontology/MTBLS_002240"},
            "Parameter Value[Ion source]": {"source": "MS", "accession": "http://purl.obolibrary.org/obo/MS_1000073"},
            "Parameter Value[Chromatography Instrument]": {"source": "MTBLS", "accession": "http://www.ebi.ac.uk/metabolights/ontology/MTBLS_000858"},
            "Parameter Value[Column type]": {"source": "MTBLS", "accession": "http://www.ebi.ac.uk/metabolights/ontology/MTBLS_000856"},
        }
    return example


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--validator-python", default="/opt/metabolights/bin/python")
    parser.add_argument("--opa", default="/opt/metabolights/bin/opa")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    dual = example_snapshot(args.output)
    single = dual.model_copy(deep=True)
    single.assays = single.assays[:1]
    sample_ids = {a.sample_id for a in single.assays[0].acquisitions}
    single.samples = [s for s in single.samples if s.sample_id in sample_ids]
    examples = with_example_metadata(dual)
    # Keep missing biology, but exercise the fixture's recorded factor values too.
    examples.samples = [sample.model_copy(update={"factor_values": saved.factor_values})
                        for sample, saved in zip(dual.samples, examples.samples)]
    empty_metadata_errors = None
    for label, snapshot in (("dual", dual), ("single", single), ("examples", examples), ("complete-example", complete_example(dual))):
        destination = args.output / label
        local_report = build_package(snapshot, destination)
        command = [args.validator_python, str(ROOT / "worker/tools/validate_metabolights.py"),
                   str(destination / "submission"), str(destination / "validation"), str(TEMPLATES), "--opa", args.opa,
                   "--study-id", snapshot.metabolights_id]
        subprocess.run(command, check=True)
        report = json.loads((destination / "validation/report.json").read_text())
        assert report["completed"] is True
        assert not any(report["parser_messages"].values()), report["parser_messages"]
        assert not any(v["identifier"] in {"rule___100_100_001_02", "rule_a_100_100_001_13", "rule_s_100_100_001_12"}
                       for v in report["violations"])
        model = json.loads((destination / "validation/official-input.json").read_text())
        assert len(model["assays"]) == len(snapshot.assays)
        assert all(assay["assayTechnique"]["name"] == "LC-MS" for assay in model["assays"].values())
        errors = sum(v["type"] == "ERROR" for v in report["violations"])
        if label == "dual":
            empty_metadata_errors = errors
        if label == "examples":
            assert errors < empty_metadata_errors, "Saved metadata must reduce official missing-field errors"
            codes = [issue["code"] for issue in local_report["issues"]]
            assert not {"missing_contacts", "missing_collection_protocol", "missing_protocol"}.intersection(codes)

        if label == "complete-example":
            assert errors == 0, [v for v in report["violations"] if v["type"] == "ERROR"]
            assert local_report["issues"] == [] and local_report["submission_ready"] is False, local_report["issues"]
        else:
            assert errors > 0, "Incomplete metadata must fail official validation"
        initial = {json.dumps(v, sort_keys=True) for v in report["violations"]}

        def added_errors(label_suffix):
            broken_command = list(command)
            broken_command[3] = str(destination / f"{label_suffix}-validation")
            subprocess.run(broken_command, check=True)
            broken = json.loads((destination / f"{label_suffix}-validation/report.json").read_text())
            return [v for v in broken["violations"] if json.dumps(v, sort_keys=True) not in initial and v["type"] == "ERROR"]

        # Spectral files are links. Prove the validator checks both the listed
        # file and the bytes behind it, not only filenames in the ISA.
        spectral = next((destination / "submission/FILES").rglob("*.mzML"))
        assert spectral.is_symlink(), "Spectral files must be linked, not copied"
        spectral_name = spectral.relative_to(destination / "submission").as_posix()
        target = spectral.readlink()
        spectral.rename(spectral.with_suffix(".missing"))
        try:
            assert any(v["identifier"] == "rule_f_400_090_001_01" and any(spectral_name in value for value in v["values"])
                       for v in added_errors("missing-file")), "Removing a spectral file did not produce the missing-file error"
        finally:
            spectral.with_suffix(".missing").rename(spectral)
        spectral.unlink()
        spectral.symlink_to(target.with_name("absent-source.mzML"))
        try:
            assert any(v["identifier"] == "rule_f_400_090_001_04" for v in added_errors("dangling-link")), \
                "A link to a missing source did not produce an official error"
        finally:
            spectral.unlink()
            spectral.symlink_to(target)
        print(f"{label}: parsed; {len(report['violations'])} official findings ({errors} errors) retained; missing file and dangling link detected")
    print("Conformance checks passed. The complete example has no official errors; all fixtures remain synthetic and not submission-ready.")


if __name__ == "__main__":
    main()
