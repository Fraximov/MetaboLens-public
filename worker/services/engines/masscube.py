"""MassCube configuration and native artifact mapping for the common runner."""
import json

from services.engines.base import EngineLaunch, checked_template_bytes


def prepare_masscube(context):
    artifacts = []
    snapshot = context.manifest.get('masscube_template')
    if snapshot is not None:
        source = context.workspace.contract_dir / 'masscube_template_source.csv'
        source.write_bytes(checked_template_bytes(snapshot, 'MassCube template'))
        artifacts.append((source, 'masscube_template_source'))
    config = context.workspace.contract_dir / 'masscube_config.json'
    # Only native scientific settings enter this file, so a review continuation
    # changes finalization without invalidating detection/alignment caches.
    config.write_text(json.dumps(context.parameters.get('masscube_parameters', {}),
                                 sort_keys=True, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    parameters = {'masscube_config': str(config)}
    if context.support_files.get('ms2_library_path'):
        parameters['masscube_ms2_library'] = str(context.support_files['ms2_library_path'])
    return EngineLaunch(
        parameters=parameters, artifacts=[(config, 'masscube_config'), *artifacts],
        consumed_parameters=('masscube_config_path', 'masscube_parameters', 'ms2_library_path'),
        resource_groups={'prepare_acquisition_input': 'prepare', 'MASSCUBE_PROCESS': 'peak',
                         'METABOLENSR_PREVIEW': 'default', 'METABOLENSR_FINALIZE': 'default'},
        native_outputs=[
            ('masscube/aligned_feature_table.tsv', 'masscube_alignment'),
            ('masscube/acquisition_evidence.tsv', 'masscube_acquisition_evidence'),
            ('masscube/source_metadata.json', 'masscube_source_metadata'),
            ('masscube/samplesheet.csv', 'masscube_samplesheet'),
            ('masscube/evidence.jsonl', 'masscube_evidence_data'),
            ('masscube/evidence.index.json', 'masscube_evidence_index'),
            ('masscube/single_files/*.txt', 'masscube_single_file'),
            ('masscube/cache/*.mzpkl', 'masscube_scan_cache'),
        ],
    )
