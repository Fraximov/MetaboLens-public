from __future__ import annotations

import argparse
from collections.abc import Iterator, Sequence
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit


CORE_LINE_LIMITS = {
    "CLAUDE.md": 10,
    "AGENTS.md": 160,
    "docs/README.md": 250,
    "docs/architecture/system-overview.md": 250,
    "docs/DEVELOPMENT.md": 250,
}
INDEX_EXCLUDED_PREFIXES = (
    "docs/superpowers/plans/",
    "docs/superpowers/specs/",
)
REFERENCE_DEFINITION_PATTERN = re.compile(r"^\s{0,3}\[([^]]+)\]:\s*(.*)$")
MACHINE_PATH_PREFIXES = ("/Users/", "/Volumes/")


def _reference_name(value: str) -> str:
    return " ".join(value.split()).casefold()


def _reference_destination(value: str) -> str | None:
    value = value.lstrip()
    if not value:
        return None
    if value.startswith("<"):
        end = value.find(">", 1)
        return value[1:end] if end != -1 else None

    depth = 0
    for index, character in enumerate(value):
        if character == "(":
            depth += 1
        elif character == ")":
            if depth == 0:
                return None
            depth -= 1
        elif character.isspace() and depth == 0:
            return value[:index]
    return value if depth == 0 else None


def _closing_parenthesis(line: str, start: int) -> int | None:
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(start, len(line)):
        character = line[index]
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif quote:
            if character == quote:
                quote = None
        elif character in {'"', "'"}:
            quote = character
        elif character == "(":
            depth += 1
        elif character == ")":
            if depth == 0:
                return index
            depth -= 1
    return None


def _inline_destination(line: str, opening_parenthesis: int) -> tuple[str, int] | None:
    index = opening_parenthesis + 1
    while index < len(line) and line[index].isspace():
        index += 1
    if index == len(line):
        return None

    if line[index] == "<":
        end = line.find(">", index + 1)
        if end == -1:
            return None
        closing = _closing_parenthesis(line, end + 1)
        return (line[index + 1 : end], closing) if closing is not None else None

    start = index
    depth = 0
    escaped = False
    while index < len(line):
        character = line[index]
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == "(":
            depth += 1
        elif character == ")":
            if depth == 0:
                return line[start:index], index
            depth -= 1
        elif character.isspace() and depth == 0:
            closing = _closing_parenthesis(line, index)
            return (line[start:index], closing) if closing is not None else None
        index += 1
    return None


def markdown_links(path: Path) -> Iterator[tuple[int, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    references: dict[str, str] = {}
    for line in lines:
        match = REFERENCE_DEFINITION_PATTERN.match(line)
        if match and (target := _reference_destination(match.group(2))) is not None:
            references[_reference_name(match.group(1))] = target

    for line_number, line in enumerate(lines, 1):
        if REFERENCE_DEFINITION_PATTERN.match(line):
            continue
        index = 0
        while (opening := line.find("[", index)) != -1:
            closing = line.find("]", opening + 1)
            if closing == -1:
                break
            if opening > 0 and line[opening - 1] == "!":
                index = closing + 1
                continue
            if closing + 1 < len(line) and line[closing + 1] == "(":
                parsed = _inline_destination(line, closing + 1)
                if parsed is not None:
                    target, end = parsed
                    yield line_number, target
                    index = end + 1
                    continue
            elif closing + 1 < len(line) and line[closing + 1] == "[":
                reference_end = line.find("]", closing + 2)
                if reference_end != -1:
                    name = line[closing + 2 : reference_end] or line[opening + 1 : closing]
                    if target := references.get(_reference_name(name)):
                        yield line_number, target
                    index = reference_end + 1
                    continue
            index = closing + 1


def repository_markdown(root: Path) -> list[Path]:
    return sorted(path for path in (root / "docs").rglob("*.md") if path.is_file())


def _inside_repository(root: Path, path: Path) -> bool:
    return path == root or root in path.parents


def _local_link(root: Path, source: Path, target: str) -> tuple[Path | None, str | None]:
    split = urlsplit(target)
    if split.netloc or (split.scheme and split.scheme.casefold() != "file"):
        return None, None

    decoded_path = unquote(split.path)
    if not decoded_path:
        return None, None
    if decoded_path.startswith(MACHINE_PATH_PREFIXES):
        return None, "machine-specific link"

    candidate = Path(decoded_path)
    if split.scheme.casefold() == "file":
        resolved = candidate.resolve()
    else:
        resolved = (source.parent / candidate).resolve()
    if not _inside_repository(root, resolved):
        return None, "link escapes repository"
    return resolved, None


def audit_repository(root: Path) -> list[str]:
    root = root.resolve()
    diagnostics: list[str] = []
    indexed_paths: set[Path] = set()
    index_path = root / "docs/README.md"

    for relative, limit in CORE_LINE_LIMITS.items():
        path = root / relative
        if not path.exists():
            diagnostics.append(f"{relative}:1: required context file is missing")
            continue
        count = len(path.read_text(encoding="utf-8").splitlines())
        if count > limit:
            diagnostics.append(f"{relative}:1: line budget exceeded: {count} > {limit}")

    scan_paths = [root / relative for relative in CORE_LINE_LIMITS]
    if index_path.exists():
        for _, target in markdown_links(index_path):
            target_path, _ = _local_link(root, index_path, target)
            if target_path is not None:
                indexed_paths.add(target_path)
        scan_paths.extend(path for path in indexed_paths if path.suffix.lower() == ".md")
        for relative in CORE_LINE_LIMITS:
            if relative == "docs/README.md":
                continue
            if (root / relative).resolve() not in indexed_paths:
                diagnostics.append(
                    f"docs/README.md:1: required core document is not indexed: {relative}"
                )

    for path in sorted(set(scan_paths)):
        resolved_source = path.resolve()
        if not _inside_repository(root, resolved_source):
            continue
        if not resolved_source.exists() or not resolved_source.is_file():
            continue
        relative_source = resolved_source.relative_to(root).as_posix()
        for line_number, target in markdown_links(resolved_source):
            target_path, problem = _local_link(root, resolved_source, target)
            if problem is not None:
                diagnostics.append(f"{relative_source}:{line_number}: {problem}: {target}")
                continue
            if target_path is not None and not target_path.exists():
                diagnostics.append(f"{relative_source}:{line_number}: broken local link: {target}")

    for path in repository_markdown(root):
        relative = path.relative_to(root).as_posix()
        if relative == "docs/README.md" or relative.startswith(INDEX_EXCLUDED_PREFIXES):
            continue
        resolved = path.resolve()
        if not _inside_repository(root, resolved):
            continue
        if resolved not in indexed_paths:
            diagnostics.append(f"{relative}:1: not indexed by docs/README.md")

    return sorted(set(diagnostics))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the MetaboLens context documentation spine.")
    parser.add_argument("root", nargs="?", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    diagnostics = audit_repository(args.root)
    if diagnostics:
        print("\n".join(diagnostics))
        return 1
    print("Context documentation check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
