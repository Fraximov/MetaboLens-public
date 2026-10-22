#!/usr/bin/env python3
"""Scan a source export, including nested archives, without printing secret values."""
import argparse
import gzip
import io
from pathlib import Path, PurePosixPath
import re
import tarfile
import zipfile


MAX_BYTES = 64 * 1024 * 1024
SKIP = {'.git', '__pycache__', '.pytest_cache', '.mypy_cache'}
PRIVATE_NAMES = {'.env', '.rtoken', '.credentials', '.credentials_rsaparams', 'id_rsa', 'id_ed25519'}
PRIVATE_DIRS = {'runtime', 'actions-runner', 'sirius-home', '.sirius-6.3', 'private-analysis'}
PATTERNS = {
    'private key': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----'),
    'GitHub access token': re.compile(rb'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b'),
    'JWT credential': re.compile(rb'\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{20,}\b'),
    'literal refresh credential': re.compile(rb'(?i)["\']refresh[_-]?token["\']\s*:\s*["\'][A-Za-z0-9_./+=-]{24,}["\']'),
}


def inspect_bytes(name: str, data: bytes, depth: int = 0) -> list[str]:
    problems = []
    parts = PurePosixPath(name.split('!')[-1]).parts
    if any(part in PRIVATE_NAMES or part in PRIVATE_DIRS or part.endswith('.env')
           or (part.startswith('.env.') and part != '.env.example') for part in parts):
        problems.append(f'{name}: private credential/runtime path')
    if name.lower().endswith(('.raw', '.mzml', '.mzml.gz', '.sirius')) and parts[-1] != 'Dockerfile.sirius':
        problems.append(f'{name}: experimental data requires explicit release review')
    for label, pattern in PATTERNS.items():
        if pattern.search(data):
            problems.append(f'{name}: {label}')
    archive = data.startswith((b'\x1f\x8b', b'PK\x03\x04')) or name.endswith('.tar')
    if archive and depth >= 3:
        return problems + [f'{name}: nested archive depth exceeds scan limit']
    try:
        if data.startswith(b'\x1f\x8b'):
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as compressed:
                expanded = compressed.read(MAX_BYTES + 1)
            if len(expanded) > MAX_BYTES:
                return problems + [f'{name}: decompressed content exceeds scan limit']
            child = name[:-3] if name.endswith('.gz') else name + '.tar'
            problems.extend(inspect_bytes(child, expanded, depth + 1))
        elif data.startswith(b'PK\x03\x04'):
            with zipfile.ZipFile(io.BytesIO(data)) as compressed:
                if sum(item.file_size for item in compressed.infolist()) > MAX_BYTES:
                    return problems + [f'{name}: archive content exceeds scan limit']
                for item in compressed.infolist():
                    if not item.is_dir():
                        problems.extend(inspect_bytes(f'{name}!{item.filename}', compressed.read(item), depth + 1))
        elif name.endswith('.tar'):
            with tarfile.open(fileobj=io.BytesIO(data), mode='r:') as compressed:
                total = 0
                for item in compressed:
                    total += item.size
                    if total > MAX_BYTES:
                        return problems + [f'{name}: archive content exceeds scan limit']
                    if item.issym() or item.islnk():
                        problems.append(f'{name}!{item.name}: archive link requires review')
                    elif item.isfile():
                        problems.extend(inspect_bytes(f'{name}!{item.name}', compressed.extractfile(item).read(), depth + 1))
    except (OSError, EOFError, tarfile.TarError, zipfile.BadZipFile, RuntimeError):
        problems.append(f'{name}: archive could not be inspected')
    return problems


def audit(root: Path) -> list[str]:
    problems = []
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if any(part in SKIP for part in relative.parts):
            continue
        if path.is_symlink():
            problems.append(f'{relative}: symlink requires review')
        elif path.is_file():
            if path.stat().st_size > MAX_BYTES:
                problems.append(f'{relative}: file exceeds scan limit')
            else:
                problems.extend(inspect_bytes(relative.as_posix(), path.read_bytes()))
    return sorted(set(problems))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', nargs='?', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    findings = audit(args.root)
    if findings:
        print('\n'.join(findings))
        return 1
    print('Source release scan passed. This check is not a substitute for reviewing data rights or all possible secret formats.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
