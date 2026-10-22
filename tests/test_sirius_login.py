import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class SiriusLoginTests(unittest.TestCase):
    def test_login_requires_fresh_public_image_and_uses_own_runtime(self):
        script = Path(__file__).resolve().parents[1] / "scripts/sirius-login-local.sh"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "scripts").mkdir()
            shutil.copyfile(script, root / "scripts/sirius-login-local.sh")
            descriptor = root / "workflows/nextflow4ms-dial-msdial5-clean/engines/msdial5/engine.json"
            descriptor.parent.mkdir(parents=True)
            image = "sha256:" + "a" * 64
            descriptor.write_text(json.dumps({"container_images": {"sirius_container_image": image}}))
            binary = root / "bin/docker"
            binary.parent.mkdir()
            binary.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
if sys.argv[1:3] == ['image', 'inspect']:
    print(json.dumps([{'Config': {'Labels': {'org.metabolens.public-build': os.environ['CLEAN_IMAGE']}}}]))
else:
    pathlib.Path(os.environ['CALL_RECORD']).write_text(json.dumps(sys.argv[1:]))
''')
            binary.chmod(0o755)
            record = root / "docker-call.json"
            env = {**os.environ, "PATH": str(binary.parent) + os.pathsep + os.environ["PATH"],
                   "CLEAN_IMAGE": "false", "CALL_RECORD": str(record)}
            for key in ("METABOLENS_SIRIUS_HOME", "METABOLENS_SIRIUS_IMAGE"):
                env.pop(key, None)
            command = ["bash", str(root / "scripts/sirius-login-local.sh"), "scientist@example.invalid"]
            failed = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertNotEqual(failed.returncode, 0, "Unverified old images must be refused")
            self.assertFalse(record.exists())
            env["CLEAN_IMAGE"] = "true"
            subprocess.run(command, env=env, capture_output=True, text=True, check=True)
            arguments = json.loads(record.read_text())
            self.assertIn(image, arguments)
            self.assertIn("--pull=never", arguments)
            self.assertIn(str(root.resolve() / "runtime/sirius-home") + ":/sirius-home", arguments)
            self.assertEqual(arguments[-5:], ["sirius", "login", "-u", "scientist@example.invalid", "-p"])
