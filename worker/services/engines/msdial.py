"""MS-DIAL-specific reference, template and native-artifact mapping."""
from pathlib import Path

from services.engines.base import EngineLaunch, checked_template_bytes
from services.msdial_config import MsdialConfigRenderer


def prepare_msdial(context):
    reference_id = context.manifest.get('reference_file_asset_id')
    matches = [row for row in context.staged_files if row['file_asset_id'] == reference_id]
    if len(matches) != 1:
        raise ValueError('MS-DIAL reference must be one selected acquisition')
    reference = matches[0]['staged_path']
    support = context.support_files
    template = context.manifest.get('msdial_template')
    if template is not None:
        support['msdial_config_path'].write_bytes(checked_template_bytes(template, 'MS-DIAL template snapshot'))
    config = context.workspace.staged_msdial_config_path
    MsdialConfigRenderer.render(template_path=support['msdial_config_path'], output_path=config,
                               ms1_library_path=support['ms1_library_path'], ms2_library_path=support['ms2_library_path'],
                               reference_path=Path(reference))
    parameters = {'ref': reference, 'msdial_config': str(config),
                  'sirius_container_options': context.settings.workflow_sirius_container_options if context.settings else '',
                  **{role: str(support[f'{role}_path']) for role in ('ms1_library', 'ms2_library', 'is_library')}}
    return EngineLaunch(
        parameters=parameters, artifacts=[(config, 'staged_msdial_config')],
        # These path selectors are worker inputs, not Nextflow scientific parameters.
        consumed_parameters=('reference_file_id', *support),
        resource_groups={
            'peak_detection_msdial5': 'peak',
            'prepare_msdial5_input': 'prepare',
            'extract_is_qc': 'prepare',
            'MSDIAL_TO_METABOLENSR': 'prepare',
            'METABOLENSR_PREVIEW': 'default',
            'METABOLENSR_FINALIZE': 'default',
        },
        native_outputs=[('ms-dial/**/AlignResult*.mdalign', 'msdial_alignment'),
                        ('ms-dial/**/AlignResult*.mdmsp', 'msdial_spectra'),
                        ('ms-dial/**/AlignResult*.mzTabM', 'msdial_mztabm'),
                        ('ms-dial/**/*EIC.aef', 'msdial_eic_archive')],
        legacy_fields={'reference_file_asset_id': reference_id, 'reference_path': reference,
                       'staged_msdial_config_path': str(config),
                       **{f'{role}_path': parameters[role] for role in ('ms1_library', 'ms2_library', 'is_library')}},
    )
