"""Exercise the real task renderer with a fake CLI and synthetic input only."""
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest


WORKFLOW = Path(__file__).resolve().parents[2] / "workflows/nextflow4ms-dial-msdial5-clean"


@unittest.skipUnless(shutil.which("nextflow"), "requires Nextflow for task rendering")
class SiriusExecutionTests(unittest.TestCase):
    def test_strings_remain_literal_and_tolerances_reach_formulas(self):
        with TemporaryDirectory(prefix="metabolens-public-sirius-") as temporary:
            root = Path(temporary)
            nextflow_home = root / "nextflow-home"
            nextflow_home.mkdir()
            framework = Path(os.environ.get("NXF_HOME", str(Path.home() / ".nextflow"))) / "framework"
            if framework.is_dir():
                (nextflow_home / "framework").symlink_to(framework)
            (root / "bin").mkdir()
            cli = root / "bin/sirius"
            cli.write_text('''#!/usr/bin/env python3
import json, pathlib, sys
out = pathlib.Path('sirius_output')
with (out / 'argv.jsonl').open('a') as handle:
    handle.write(json.dumps(sys.argv[1:]) + '\\n')
if 'summaries' in sys.argv:
    (out / 'summaries/result.tsv').write_text('synthetic\\n')
''')
            cli.chmod(0o755)
            (root / "input.msp").write_text("synthetic\n")
            values = {"database": "DB'\" $(touch injected-db) ; spaced",
                      "workspace": "own'\" $(touch injected-workspace) ; spaced"}
            (root / "params.json").write_text(json.dumps(values))
            (root / "nextflow.config").write_text("process.executor = 'local'\n")
            (root / "main.nf").write_text(f'''nextflow.enable.dsl = 2
include {{ run_sirius }} from '{WORKFLOW.as_posix()}/modules/sirius/main'
workflow {{
    run_sirius(channel.value(file('input.msp')), params.workspace, 'structures',
        params.database, 7.5, 3.25, 'OFF', true)
    run_sirius.out.artifacts.view {{ it -> 'RESULT_DIR=' + it }}
}}
''')
            completed = subprocess.run(
                ["nextflow", "-C", str(root / "nextflow.config"), "run", "main.nf",
                 "-ansi-log", "false", "-params-file", "params.json"],
                cwd=root, capture_output=True, text=True, timeout=90,
                env={**os.environ, "NXF_OFFLINE": "true", "NXF_DISABLE_CHECK_LATEST": "true",
                     "NXF_HOME": str(nextflow_home),
                     "NXF_PLUGINS_DIR": str(root / "plugins"), "NXF_PLUGINS_DEFAULT": "false",
                     "NXF_ENABLE_SECRETS": "false", "NXF_SYNTAX_PARSER": "v1",
                     "PATH": str(root / "bin") + os.pathsep + os.environ["PATH"]},
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            task = next((root / "work").glob("*/*/sirius_output"))
            calls = [json.loads(line) for line in (task / "argv.jsonl").read_text().splitlines()]
            formulas = next(call for call in calls if "formulas" in call)
            structures = next(call for call in calls if "structures" in call)
            self.assertEqual(formulas[formulas.index("--ppm-max") + 1], "3.25")
            self.assertEqual(formulas[formulas.index("--ppm-max-ms2") + 1], "7.5")
            self.assertEqual(structures[structures.index("-d") + 1], values["database"])
            self.assertEqual(structures[structures.index("--workspace") + 1], values["workspace"])
            self.assertFalse(list((root / "work").rglob("injected-*")))
