#!/usr/bin/env python3
"""Export preset definitions from an engine's pinned MetaboLensR image."""
import argparse
import json
from pathlib import Path
import re
import subprocess


def export_profiles(descriptor_path: Path, *, check: bool = False) -> None:
    descriptor = json.loads(descriptor_path.read_text(encoding='utf-8'))
    package = descriptor['package']
    image = package['container_image']
    if not re.fullmatch(r'(?:[^\s]+@)?sha256:[0-9a-f]{64}', image):
        raise ValueError('Package image must use an immutable digest')
    inspected = json.loads(subprocess.check_output(['docker', 'image', 'inspect', image], text=True))[0]
    labels = inspected['Config'].get('Labels') or {}
    if labels.get('org.opencontainers.image.revision') != package['commit']:
        raise ValueError('Package image revision does not match the descriptor')
    expression = ('cat(jsonlite::toJSON(list(version = as.character(utils::packageVersion("MetaboLensR")), '
                  'profiles = MetaboLensR::curation_profiles(), supports_rule_selection = '
                  '"resolve_curation_profile" %in% getNamespaceExports("MetaboLensR")), '
                  'auto_unbox = TRUE, pretty = TRUE))')
    exported = json.loads(subprocess.check_output([
        'docker', 'run', '--rm', '--pull=never', '--network=none', '--read-only',
        '--tmpfs', '/tmp', '--entrypoint', 'Rscript', image, '-e', expression,
    ], text=True))
    if exported['version'] != package['version']:
        raise ValueError('Package version does not match the descriptor')
    profiles = exported['profiles']
    if not isinstance(profiles, dict) or package['config']['profile'] not in profiles:
        raise ValueError('Package profiles do not contain the configured default profile')
    if check:
        if package.get('curation_profiles') != profiles:
            raise ValueError('Profile catalog differs from the pinned package; export it again')
        if package.get('supports_rule_selection', False) != exported['supports_rule_selection']:
            raise ValueError('Rule-selection capability differs from the pinned package; export it again')
    else:
        package['curation_profiles'] = profiles
        package['supports_rule_selection'] = exported['supports_rule_selection']
        descriptor_path.write_text(json.dumps(descriptor, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('descriptor', type=Path)
    parser.add_argument('--check', action='store_true', help='Compare without writing')
    args = parser.parse_args()
    export_profiles(args.descriptor, check=args.check)
    print(f'Profile catalog {"verified" if args.check else "exported"}: {args.descriptor}')
