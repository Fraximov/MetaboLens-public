#!/usr/bin/env bash
set -euo pipefail

email="${1:-${SIRIUS_EMAIL:-}}"
if [[ -z "${email}" ]]; then
    echo "Usage: $0 <sirius-email>" >&2
    exit 2
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
sirius_home="${repo_root}/runtime/sirius-home"
sirius_image="$(python3 - "${repo_root}" <<'PY'
import json
from pathlib import Path
import re
import subprocess
import sys

descriptor = Path(sys.argv[1]) / 'workflows/nextflow4ms-dial-msdial5-clean/engines/msdial5/engine.json'
image = json.loads(descriptor.read_text())['container_images']['sirius_container_image']
if not re.fullmatch(r'(?:[^\s]+@)?sha256:[0-9a-f]{64}', image):
    raise SystemExit('Run scripts/setup_scientific_images.py before SIRIUS login.')
inspected = json.loads(subprocess.check_output(['docker', 'image', 'inspect', image], text=True))[0]
if (inspected['Config'].get('Labels') or {}).get('org.metabolens.public-build') != 'true':
    raise SystemExit('SIRIUS image is not a clean public build. Run scripts/setup_scientific_images.py first.')
print(image)
PY
)"

mkdir -p "${sirius_home}"
chmod 700 "${sirius_home}"

exec docker run --rm --pull=never -it \
    -e HOME=/sirius-home \
    -e JAVA_TOOL_OPTIONS=-Duser.home=/sirius-home \
    -v "${sirius_home}:/sirius-home" \
    "${sirius_image}" \
    sirius login -u "${email}" -p
