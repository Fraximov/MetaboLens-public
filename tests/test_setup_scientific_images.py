import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/setup_scientific_images.py"


class ScientificImageSetupTests(unittest.TestCase):
    def load(self):
        self.assertTrue(SCRIPT.is_file(), "Scientific image setup is missing")
        spec = importlib.util.spec_from_file_location("setup_scientific_images", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_identity_uses_real_digest_or_exact_local_id(self):
        module = self.load()
        identity = "sha256:" + "a" * 64
        self.assertEqual(module.immutable_image({"Id": identity, "RepoDigests": []}), identity)
        reference = "registry.invalid/public@sha256:" + "b" * 64
        self.assertEqual(module.immutable_image({"Id": identity, "RepoDigests": [reference]}), reference)
        with self.assertRaises(ValueError):
            module.immutable_image({"Id": "latest", "RepoDigests": []})

    def test_descriptor_update_keeps_previous_files_if_validation_fails(self):
        module = self.load()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = []
            for name in ("msdial5", "masscube"):
                path = root / name / "engine.json"
                path.parent.mkdir()
                path.write_text(json.dumps({"container_images": {"engine": "old"},
                    "package": {"container_image": "old"}}))
                paths.append(path)
            originals = [path.read_bytes() for path in paths]
            images = {"engine": "sha256:" + "a" * 64, "metabolensr_container_image": "sha256:" + "b" * 64}
            with patch.object(module, "export_profiles", side_effect=[None, ValueError("bad package")]):
                with self.assertRaisesRegex(ValueError, "bad package"):
                    module.update_descriptors(paths, images)
            self.assertEqual([path.read_bytes() for path in paths], originals)
            with patch.object(module, "export_profiles") as validate:
                module.update_descriptors(paths, images)
            self.assertEqual(validate.call_count, 2)
            self.assertEqual(json.loads(paths[0].read_text())["container_images"]["engine"], images["engine"])

    def package_inputs(self, root):
        reference = "registry.invalid/metabolensr@sha256:" + "b" * 64
        source = {"commit": "c" * 40, "version": "0.0.0.9003"}
        (root / "SOURCE.json").write_text(json.dumps(source))
        catalog = {"strict": {"name": "strict", "statistics": [], "annotation": []}}
        descriptors = [{"package": {**source, "container_image": reference,
            "config": {"profile": "strict"}, "curation_profiles": catalog,
            "supports_rule_selection": True}} for _ in range(2)]
        inspected = {"Id": "sha256:" + "a" * 64, "RepoDigests": ["other.invalid/image@sha256:" + "d" * 64],
            "Os": "linux", "Architecture": "amd64", "Config": {"Labels": {
                "org.opencontainers.image.revision": source["commit"]}}}
        exported = {"version": source["version"], "profiles": catalog, "supports_rule_selection": True}
        return descriptors, inspected, exported

    def prepare(self, module, descriptors, source, **kwargs):
        return module.prepare_metabolensr(descriptors, platform="linux/amd64", package_source=source,
            tag="metabolens-public-test:source", **kwargs)

    def test_default_prebuilt_uses_exact_pin_without_building_or_pulling(self):
        module = self.load()
        with tempfile.TemporaryDirectory() as temp:
            descriptors, image, catalog = self.package_inputs(Path(temp))
            original = json.dumps(descriptors)
            def docker(command, **kwargs):
                return json.dumps(catalog if command[1] == "run" else [image])
            with patch.object(module.subprocess, "check_output", side_effect=docker), \
                    patch.object(module.subprocess, "run") as run, patch.object(module, "build_image") as build:
                record = self.prepare(module, descriptors, Path(temp))
            self.assertEqual(record["identity"], descriptors[0]["package"]["container_image"])
            self.assertEqual(record["mode"], "prebuilt")
            self.assertEqual(json.dumps(descriptors), original)
            run.assert_not_called()
            build.assert_not_called()

    def test_missing_registry_image_is_pulled_by_requested_digest(self):
        module = self.load()
        with tempfile.TemporaryDirectory() as temp:
            descriptors, image, catalog = self.package_inputs(Path(temp))
            requested = "another.invalid/metabolensr@sha256:" + "e" * 64
            replies = [subprocess.CalledProcessError(1, "inspect"), json.dumps([image]),
                       json.dumps([image]), json.dumps(catalog), json.dumps([image]), json.dumps(catalog)]
            with patch.object(module.subprocess, "check_output", side_effect=replies), \
                    patch.object(module.subprocess, "run") as run, patch.object(module, "build_image") as build:
                record = self.prepare(module, descriptors, Path(temp), image=requested)
            run.assert_called_once_with(["docker", "pull", "--platform", "linux/amd64", requested], check=True)
            self.assertEqual(record["identity"], requested)
            build.assert_not_called()

    def test_missing_prebuilt_fails_with_explicit_source_fallback_instruction(self):
        module = self.load()
        with tempfile.TemporaryDirectory() as temp:
            descriptors, _, _ = self.package_inputs(Path(temp))
            for reference in (descriptors[0]["package"]["container_image"], "sha256:" + "a" * 64):
                with self.subTest(reference=reference), \
                        patch.object(module.subprocess, "check_output", side_effect=subprocess.CalledProcessError(1, "inspect")), \
                        patch.object(module.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "pull")) as run, \
                        patch.object(module, "build_image") as build:
                    with self.assertRaisesRegex(ValueError, "--build-metabolensr"):
                        self.prepare(module, descriptors, Path(temp), image=reference)
                    if reference.startswith("sha256:"):
                        run.assert_not_called()
                    build.assert_not_called()

    def test_mutable_or_unsafe_image_references_are_rejected_before_docker(self):
        module = self.load()
        with tempfile.TemporaryDirectory() as temp:
            descriptors, _, _ = self.package_inputs(Path(temp))
            for reference in ("image:latest", "--help@sha256:" + "a" * 64, "user:secret@image@sha256:" + "a" * 64):
                with self.subTest(reference=reference), patch.object(module.subprocess, "check_output") as inspect:
                    with self.assertRaisesRegex(ValueError, "immutable"):
                        self.prepare(module, descriptors, Path(temp), image=reference)
                    inspect.assert_not_called()

    def test_wrong_platform_or_package_catalog_cannot_be_selected(self):
        module = self.load()
        with tempfile.TemporaryDirectory() as temp:
            for problem in ("platform", "revision", "version", "catalog"):
                descriptors, image, catalog = self.package_inputs(Path(temp))
                original = json.dumps(descriptors)
                if problem == "platform":
                    image["Architecture"] = "arm64"
                elif problem == "revision":
                    image["Config"]["Labels"]["org.opencontainers.image.revision"] = "f" * 40
                elif problem == "version":
                    catalog["version"] = "different"
                else:
                    catalog["profiles"] = {"strict": {"name": "different"}}
                def docker(command, **kwargs):
                    return json.dumps(catalog if command[1] == "run" else [image])
                with self.subTest(problem=problem), patch.object(module.subprocess, "check_output", side_effect=docker):
                    with self.assertRaisesRegex(ValueError, problem):
                        self.prepare(module, descriptors, Path(temp))
                self.assertEqual(json.dumps(descriptors), original)

    def test_disagreeing_default_pins_are_rejected_before_docker(self):
        module = self.load()
        with tempfile.TemporaryDirectory() as temp:
            descriptors, _, _ = self.package_inputs(Path(temp))
            descriptors[1]["package"]["container_image"] = "sha256:" + "d" * 64
            with patch.object(module.subprocess, "check_output") as inspect:
                with self.assertRaisesRegex(ValueError, "agree"):
                    self.prepare(module, descriptors, Path(temp))
            inspect.assert_not_called()

    def test_source_build_is_explicit_and_still_checks_the_installed_package(self):
        module = self.load()
        with tempfile.TemporaryDirectory() as temp:
            descriptors, image, catalog = self.package_inputs(Path(temp))
            image["RepoDigests"] = []
            def docker(command, **kwargs):
                return json.dumps(catalog if command[1] == "run" else [image])
            built = {"image_id": image["Id"], "repo_digests": [], "package_sha256": "d" * 64}
            with patch.object(module.subprocess, "check_output", side_effect=docker), \
                    patch.object(module, "build_image", return_value=built) as build:
                record = self.prepare(module, descriptors, Path(temp), build=True)
            build.assert_called_once_with(tag="metabolens-public-test:source", platform="linux/amd64", package_source=Path(temp))
            self.assertEqual(record["identity"], image["Id"])
            self.assertEqual(record["mode"], "source")
            self.assertEqual(record["package_sha256"], built["package_sha256"])

    def test_source_fallback_rejects_modified_source_before_docker(self):
        module = self.load()
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)
            descriptors, _, _ = self.package_inputs(source)
            metadata = json.loads((source / "SOURCE.json").read_text())
            metadata["files_sha256"] = {"DESCRIPTION": "a" * 64}
            (source / "SOURCE.json").write_text(json.dumps(metadata))
            (source / "DESCRIPTION").write_text("Version: 0.0.0.9003\n")
            with patch.object(module.subprocess, "run") as run, patch.object(module.subprocess, "check_output") as inspect:
                with self.assertRaisesRegex(ValueError, "checksum"):
                    self.prepare(module, descriptors, source, build=True)
            run.assert_not_called()
            inspect.assert_not_called()

    def test_setup_validates_prebuilt_before_other_builds_or_descriptor_writes(self):
        module = self.load()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            descriptors, image, catalog = self.package_inputs(root)
            converter = "registry.invalid/converter@sha256:" + "d" * 64
            paths = []
            for name, descriptor in zip(("msdial5", "masscube"), descriptors):
                descriptor["container_images"] = {"thermorawfileparser_image": converter}
                path = root / "engines" / name / "engine.json"
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(descriptor))
                paths.append(path)
            originals = [path.read_bytes() for path in paths]
            def docker(command, **kwargs):
                return json.dumps(catalog if command[1] == "run" else [image])
            with patch.object(module, "ROOT", root), patch.object(module, "WORKFLOW", root), \
                    patch.object(module, "BUILDS", {}), \
                    patch.object(sys, "argv", [str(SCRIPT), "--package-source", str(root)]), \
                    patch.object(module.subprocess, "check_output", side_effect=docker), \
                    patch.object(module.subprocess, "run") as run, patch.object(module, "build_image") as build:
                image["Config"]["Labels"]["org.opencontainers.image.revision"] = "f" * 40
                with self.assertRaisesRegex(ValueError, "revision"):
                    module.main()
                self.assertEqual([path.read_bytes() for path in paths], originals)
                self.assertFalse((root / "runtime").exists())
                run.assert_not_called()
                build.assert_not_called()
                image["Config"]["Labels"]["org.opencontainers.image.revision"] = "c" * 40
                module.main()
                run.assert_called_once_with(["docker", "pull", "--platform", "linux/amd64", converter], check=True)
                build.assert_not_called()
            for path in paths:
                self.assertEqual(json.loads(path.read_text())["package"]["container_image"], descriptors[0]["package"]["container_image"])
            records = list((root / "runtime/image-setup").glob("*.json"))
            self.assertEqual(len(records), 1)
            self.assertEqual(json.loads(records[0].read_text())["builds"]["metabolensr_container_image"]["mode"], "prebuilt")
