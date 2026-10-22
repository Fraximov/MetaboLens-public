#!/usr/bin/env python3
"""Create private credentials for a new local installation without printing them."""
import argparse
import os
from pathlib import Path
import re
import secrets


def configure(root: Path, email: str) -> Path:
    if not re.fullmatch(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', email):
        raise ValueError('Supply a valid administrator email address')
    root = root.resolve()
    if any(character in str(root) for character in "\r\n'$\\"):
        raise ValueError('Installation path cannot contain quotes, backslashes, dollar signs or line breaks')
    values = {
        'METABOLENS_HOST_ROOT': f"'{root}'",
        'APP_SECRET_KEY': secrets.token_urlsafe(48),
        'POSTGRES_PASSWORD': secrets.token_urlsafe(32),
        'BOOTSTRAP_ADMIN_EMAIL': email,
        'BOOTSTRAP_ADMIN_PASSWORD': secrets.token_urlsafe(24),
    }
    path = root / '.env'
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as output:
        output.write(''.join(f'{key}={value}\n' for key, value in values.items()))
    for name in ('uploads', 'raw_test', 'workflow-runs', 'sirius-home'):
        (root / 'runtime' / name).mkdir(parents=True, mode=0o700, exist_ok=True)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--email', required=True)
    args = parser.parse_args()
    try:
        path = configure(Path(__file__).resolve().parents[1], args.email)
    except (ValueError, OSError) as error:
        parser.exit(1, f'Configuration not written: {error}\n')
    print(f'Created {path.name} with private permissions. Read it locally for your initial administrator password; never share it.')


if __name__ == '__main__':
    main()
