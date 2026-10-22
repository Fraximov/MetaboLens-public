from pathlib import Path
import tempfile
import unittest

from scripts.check_context_docs import audit_repository


class ContextDocsAuditTests(unittest.TestCase):
    def _write(self, root: Path, relative: str, text: str) -> None:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _clean_repository(self, root: Path) -> None:
        self._write(root, "README.md", "# Project\n")
        self._write(root, "AGENTS.md", "# Agents\n")
        self._write(root, "CLAUDE.md", "@AGENTS.md\n")
        self._write(root, "docs/DEVELOPMENT.md", "# Development\n")
        self._write(root, "docs/architecture/system-overview.md", "# System\n")
        self._write(
            root,
            "docs/README.md",
            "# Documentation\n\n"
            "- [Project](../README.md)\n"
            "- [Agents](../AGENTS.md)\n"
            "- [Claude](../CLAUDE.md)\n"
            "- [Development](DEVELOPMENT.md)\n"
            "- [System](architecture/system-overview.md)\n",
        )

    def test_clean_repository_has_no_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._clean_repository(root)
            self.assertEqual(audit_repository(root), [])

    def test_reports_broken_absolute_unindexed_and_oversized_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._clean_repository(root)
            self._write(root, "docs/orphan.md", "# Orphan\n")
            with (root / "docs/README.md").open("a", encoding="utf-8") as handle:
                handle.write("- [Missing](missing.md)\n- [Machine](/Users/example/note.md)\n")
            self._write(root, "CLAUDE.md", "\n".join(["@AGENTS.md"] * 11) + "\n")

            diagnostics = "\n".join(audit_repository(root))

            self.assertIn("docs/README.md:8: broken local link: missing.md", diagnostics)
            self.assertIn("docs/README.md:9: machine-specific link", diagnostics)
            self.assertIn("docs/orphan.md:1: not indexed by docs/README.md", diagnostics)
            self.assertIn("CLAUDE.md:1: line budget exceeded: 11 > 10", diagnostics)

    def test_reports_required_root_context_missing_from_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._clean_repository(root)
            index = root / "docs/README.md"
            index.write_text(
                index.read_text(encoding="utf-8").replace("- [Claude](../CLAUDE.md)\n", ""),
                encoding="utf-8",
            )

            diagnostics = "\n".join(audit_repository(root))

            self.assertIn("docs/README.md:1: required core document is not indexed: CLAUDE.md", diagnostics)

    def test_balanced_parentheses_do_not_truncate_inline_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._clean_repository(root)
            self._write(root, "docs/operator(quick).md", "# Operator quick guide\n")
            with (root / "docs/README.md").open("a", encoding="utf-8") as handle:
                handle.write("- [Operator quick guide](operator(quick).md)\n")

            self.assertEqual(audit_repository(root), [])

    def test_inline_link_title_is_not_treated_as_part_of_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._clean_repository(root)
            self._write(root, "docs/operator-guide.md", "# Operator guide\n")
            with (root / "docs/README.md").open("a", encoding="utf-8") as handle:
                handle.write('- [Operator guide](operator-guide.md "Local operations")\n')

            self.assertEqual(audit_repository(root), [])

    def test_full_reference_link_reports_missing_definition_target_at_use(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._clean_repository(root)
            with (root / "docs/README.md").open("a", encoding="utf-8") as handle:
                handle.write('- [Missing operator guide][g]\n\n[g]: missing.md "Local operations"\n')

            self.assertEqual(
                audit_repository(root),
                ["docs/README.md:8: broken local link: missing.md"],
            )

    def test_protocol_relative_url_is_not_a_local_repository_link(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._clean_repository(root)
            with (root / "docs/README.md").open("a", encoding="utf-8") as handle:
                handle.write("- [External guide](//example.org/operator-guide)\n")

            self.assertEqual(audit_repository(root), [])

    def test_file_uri_machine_paths_are_reported_after_url_decoding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._clean_repository(root)
            with (root / "docs/README.md").open("a", encoding="utf-8") as handle:
                handle.write(
                    "- [User guide](file:///Users/example/operator-guide.md)\n"
                    "- [Volume guide](file:///%56olumes/shared/operator-guide.md)\n"
                )

            self.assertEqual(
                audit_repository(root),
                [
                    "docs/README.md:8: machine-specific link: file:///Users/example/operator-guide.md",
                    "docs/README.md:9: machine-specific link: file:///%56olumes/shared/operator-guide.md",
                ],
            )

    def test_relative_link_outside_repository_reports_escape_and_audit_continues(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary_root = Path(directory)
            root = temporary_root / "repo"
            self._clean_repository(root)
            self._write(temporary_root, "outside.md", "# Outside\n")
            with (root / "docs/README.md").open("a", encoding="utf-8") as handle:
                handle.write("- [Outside](../../outside.md)\n- [Missing](missing.md)\n")

            self.assertEqual(
                audit_repository(root),
                [
                    "docs/README.md:8: link escapes repository: ../../outside.md",
                    "docs/README.md:9: broken local link: missing.md",
                ],
            )

    def test_symlink_to_document_outside_repository_reports_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary_root = Path(directory)
            root = temporary_root / "repo"
            self._clean_repository(root)
            self._write(temporary_root, "outside.md", "# Outside\n")
            (root / "docs/linked.md").symlink_to(temporary_root / "outside.md")
            with (root / "docs/README.md").open("a", encoding="utf-8") as handle:
                handle.write("- [Linked guide](linked.md)\n")

            self.assertEqual(
                audit_repository(root),
                ["docs/README.md:8: link escapes repository: linked.md"],
            )


if __name__ == "__main__":
    unittest.main()
