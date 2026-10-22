#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

params.masscube_config = null
params.masscube_container_image = null
params.metabolensr_config = null
params.metabolensr_container_image = null
params.metabolensr_overrides = "${projectDir}/assets/automatic_overrides.tsv"
params.masscube_ms2_library = null

include { prepare_acquisition_input } from './modules/acquisition/main'
include { MASSCUBE_PROCESS } from './modules/masscube/main'
include { METABOLENSR_QC } from './modules/metabolensr/main'

workflow {
    main:
    workflowStage = params.workflow_stage ?: 'full'
    if (!(workflowStage in ['qc_preview', 'full'])) {
        error 'MassCube workflow_stage must be qc_preview or full'
    }
    if (!params.masscube_config || !params.metabolensr_config || !params.samplesheet ||
        !params.masscube_container_image || !params.metabolensr_container_image) {
        error 'Required: --masscube_config, --metabolensr_config, --samplesheet and pinned engine/package images'
    }
    if (params.enable_sirius?.toString()?.toBoolean() || params.apply_rt_correction?.toString()?.toBoolean()) {
        error 'MassCube does not support SIRIUS or pre-engine RT correction'
    }
    prepare_acquisition_input(channel.fromPath("${params.input_dir}/*", checkIfExists: true).collect())
    library = params.masscube_ms2_library ? channel.value(file(params.masscube_ms2_library, checkIfExists: true)) : channel.value([])
    MASSCUBE_PROCESS(
        prepare_acquisition_input.out.prepared_data,
        channel.value(file(params.samplesheet, checkIfExists: true)),
        channel.value(file(params.masscube_config, checkIfExists: true)),
        channel.value(file(params.metabolensr_config, checkIfExists: true)),
        library,
        channel.value(file("${projectDir}/adapters/masscube_runner.py", checkIfExists: true)),
        channel.value(file("${projectDir}/adapters/masscube_evidence.py", checkIfExists: true))
    )
    METABOLENSR_QC(
        MASSCUBE_PROCESS.out.table.map { id, table, samples, evidence -> tuple(id, table, samples, [evidence]) },
        MASSCUBE_PROCESS.out.configuration,
        channel.value(file(params.metabolensr_overrides, checkIfExists: true)),
        workflowStage == 'qc_preview'
    )
    native_files = MASSCUBE_PROCESS.out.artifacts.flatMap { native_dir ->
        file("${native_dir}/**", type: 'file').collect { native_file ->
            tuple("masscube/${native_dir.relativize(native_file)}".toString(), native_file)
        }
    }
    publish:
    masscube_artifacts = native_files
}

output {
    masscube_artifacts {
        // Publish immutable files separately: replacing the directory on resume
        // can unlink registered evidence. Missing files can still be restored.
        path { relative_path, native_file -> native_file >> relative_path }
        overwrite false
    }
}
