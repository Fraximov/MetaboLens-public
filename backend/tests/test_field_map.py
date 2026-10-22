"""The MetaboLights–LabID field map CSV is the only copy; its code references must stay real.

Each reference is ``path`` or ``path::Qualified.symbol``. A renamed or removed
symbol fails here, so the map is corrected in the same change as the code.
"""
import ast
import csv
from pathlib import Path
import re
import unittest

ROOT = next((root for root in [*Path(__file__).resolve().parents, Path("/workspace")]
             if (root / "docs/resources/metabolights-labid-field-map.csv").is_file()), None)
COLUMNS = ["Map ID", "Field", "Minimum mapping readiness", "Typical example (illustrative)", "Scope",
           "Current MetaboLens storage", "Database source / JSON path", "ISA destination", "Current support",
           "Capture source", "LabID handoff action", "MetaboLights submission requirement", "OLS / ontology lookup",
           "Code evidence"]


def symbols(path: Path) -> set[str]:
    """Qualified names of classes, functions, class attributes and module-level assignments."""
    names = set()

    def visit(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                names.add(prefix + child.name)
                visit(child, prefix + child.name + ".")
            elif isinstance(child, (ast.Assign, ast.AnnAssign)) and (not prefix or isinstance(node, ast.ClassDef)):
                targets = [child.target] if isinstance(child, ast.AnnAssign) else child.targets
                names.update(prefix + target.id for target in targets if isinstance(target, ast.Name))

    visit(ast.parse(path.read_text()), "")
    return names


@unittest.skipIf(ROOT is None, "The repository docs folder is not mounted")
class FieldMapTests(unittest.TestCase):
    def setUp(self):
        with (ROOT / "docs/resources/metabolights-labid-field-map.csv").open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            self.columns, self.rows = reader.fieldnames, list(reader)

    def test_rows_have_stable_unique_ids_and_every_column(self):
        self.assertEqual(self.columns, COLUMNS)
        ids = [row["Map ID"] for row in self.rows]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(re.fullmatch(r"MLISA-\d{3}", map_id) for map_id in ids))
        self.assertTrue(all(row["Field"] and row["Current support"] for row in self.rows))

    def test_code_references_name_existing_files_and_symbols(self):
        cache, broken = {}, []
        for row in self.rows:
            for reference in filter(None, (part.strip() for part in row["Code evidence"].split(";"))):
                path, _, symbol = reference.partition("::")
                file = ROOT / path
                if not file.is_file():
                    broken.append(f"{row['Map ID']}: missing file {path}")
                elif symbol:
                    if path not in cache:
                        cache[path] = symbols(file)
                    if symbol not in cache[path]:
                        broken.append(f"{row['Map ID']}: {path} has no {symbol}")
        self.assertEqual(broken, [])


if __name__ == "__main__":
    unittest.main()
