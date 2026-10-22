import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from services.artifact_collector import ArtifactCollector


class PackageArtifactTests(unittest.TestCase):
    def make_bundle(self, root, kind, *, nested=True):
        bundle = root / "metabolensr" / ("aligned" if nested else "") / kind
        bundle.mkdir(parents=True)
        names = ({"preview": "qc_preview.rds", "audit": "feature_audit.tsv", "samples": "sample_metadata.tsv"}
                 if kind == "preview" else {"result": "metabolensr_result.rds", "audit": "feature_audit_final.tsv", "samples": "sample_metadata.tsv", "raw": "normalized_raw.tsv", "tic": "normalized_tic.tsv", "normalization_summary": "normalization_summary.tsv", "report": "qc_report.html"})
        outputs = []
        for role, name in names.items():
            path = bundle / name
            path.write_text("fixture\n")
            outputs.append(dict(role=role, file=name, md5=hashlib.md5(path.read_bytes()).hexdigest()))
        manifest = dict(schema_version="2.0", bundle_type=kind, outputs=outputs)
        path = bundle / ("qc_preview_summary.json" if kind == "preview" else "metabolensr_manifest.json")
        path.write_text(json.dumps(manifest))
        return path, manifest

    def test_registers_package_roles_with_missing_optional_normalizations(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_bundle(root, "preview")
            self.make_bundle(root, "final")
            records = ArtifactCollector()._collect_metabolensr_artifacts(root)
            roles = {row["artifact_role"] for row in records}
            self.assertTrue({"metabolensr_preview", "metabolensr_preview_manifest", "metabolensr_manifest", "metabolensr_report", "metabolensr_normalized_raw", "metabolensr_sample_metadata"} <= roles)
            self.assertNotIn("metabolensr_normalized_bmis", roles)
            self.assertEqual(len(records), 12)

    def test_registers_flat_nextflow_publish_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_bundle(root, "preview", nested=False)
            self.make_bundle(root, "final", nested=False)
            records = ArtifactCollector()._collect_metabolensr_artifacts(root)
            self.assertEqual(len(records), 12)
            self.assertIn("metabolensr_result", {row["artifact_role"] for row in records})

    def test_rejects_missing_report_tampered_files_and_unsafe_paths(self):
        for damage in ("missing", "tampered", "traversal", "symlink", "schema", "duplicate", "required_role"):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                path, manifest = self.make_bundle(root, "final")
                report = path.parent / "qc_report.html"
                if damage == "missing": report.unlink()
                elif damage == "tampered": report.write_text("changed")
                elif damage == "traversal": manifest["outputs"][-1]["file"] = "../../../outside.html"
                elif damage == "symlink":
                    outside = root / "outside.html"; outside.write_text("fixture\n")
                    report.unlink(); report.symlink_to(outside)
                elif damage == "schema": manifest["schema_version"] = "999"
                elif damage == "duplicate": manifest["outputs"].append(manifest["outputs"][0])
                elif damage == "required_role": manifest["outputs"] = manifest["outputs"][:-1]
                path.write_text(json.dumps(manifest))
                with self.assertRaises(ValueError):
                    ArtifactCollector()._collect_metabolensr_artifacts(root)


if __name__ == "__main__":
    unittest.main()
