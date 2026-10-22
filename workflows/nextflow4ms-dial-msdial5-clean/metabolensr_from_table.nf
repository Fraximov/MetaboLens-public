#!/usr/bin/env nextflow
// Acceptance entrypoint for the same package processes used after an engine.
nextflow.enable.dsl = 2
params.metabolensr_config = null
params.metabolensr_container_image = null
params.metabolensr_samplesheet = null
params.metabolensr_overrides = "${projectDir}/assets/automatic_overrides.tsv"
include { METABOLENSR_QC } from './modules/metabolensr/main'

workflow {
    if (!params.input || !params.metabolensr_config || !params.metabolensr_container_image) {
        error 'Required: --input, --metabolensr_config, --metabolensr_container_image'
    }
    previewOnly = (params.workflow_stage ?: 'full') in ['qc_preview', 'msdial_review']
    if (!(params.workflow_stage in ['full', 'qc_preview', 'msdial_review'])) {
        error 'workflow_stage must be full or qc_preview'
    }
    table = file(params.input, checkIfExists: true)
    samples = params.metabolensr_samplesheet ? file(params.metabolensr_samplesheet, checkIfExists: true) : []
    configuration = channel.value(file(params.metabolensr_config, checkIfExists: true))
    overrides = channel.value(file(params.metabolensr_overrides ?: "${projectDir}/assets/automatic_overrides.tsv", checkIfExists: true))
    METABOLENSR_QC(channel.of(tuple('aligned', table, samples)), configuration, overrides, previewOnly)
}
