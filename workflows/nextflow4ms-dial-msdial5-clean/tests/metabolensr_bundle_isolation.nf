#!/usr/bin/env nextflow
// Real-package acceptance: two aligned experiments must never share a publish directory.
nextflow.enable.dsl = 2
include { METABOLENSR_QC } from '../modules/metabolensr/main'

workflow {
    table = file(params.input, checkIfExists: true)
    samples = file(params.samplesheet, checkIfExists: true)
    configuration = channel.value(file(params.metabolensr_config, checkIfExists: true))
    overrides = channel.value(file(params.metabolensr_overrides, checkIfExists: true))
    METABOLENSR_QC(channel.of(tuple('bundle-a', table, samples), tuple('bundle-b', table, samples)),
                  configuration, overrides, false)
}
