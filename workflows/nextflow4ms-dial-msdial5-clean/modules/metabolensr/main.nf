/* Package-owned QC. Overrides only enter finalization, preserving preview cache. */
process METABOLENSR_PREVIEW {
    tag "${bundle_id}"
    container params.metabolensr_container_image
    cpus 1
    memory '4 GB'
    publishDir { "${params.outdir}/metabolensr/${bundle_id}" }, mode: 'copy'

    input:
    tuple val(bundle_id), path(table, stageAs: 'input.txt'), path(samplesheet, stageAs: 'samplesheet.csv'), path(companion_files)
    path config, stageAs: 'config.json'

    output:
    tuple val(bundle_id), path('preview'), emit: bundle

    script:
    def sheetArg = samplesheet ? '--samplesheet samplesheet.csv' : ''
    """
    export XDG_CACHE_HOME="\$PWD/.cache"
    mkdir -p "\$XDG_CACHE_HOME"
    metabolensr preview --input input.txt --config config.json ${sheetArg} --outdir preview
    """
}

process METABOLENSR_FINALIZE {
    tag "${bundle_id}"
    container params.metabolensr_container_image
    cpus 1
    memory '6 GB'
    publishDir { "${params.outdir}/metabolensr/${bundle_id}" }, mode: 'copy'

    input:
    tuple val(bundle_id), path(preview, stageAs: 'preview')
    path overrides, stageAs: 'overrides.tsv'

    output:
    tuple val(bundle_id), path('final'), emit: bundle

    script:
    """
    export XDG_CACHE_HOME="\$PWD/.cache"
    mkdir -p "\$XDG_CACHE_HOME"
    # SLURM's host TMPDIR may not be mounted inside the task container.
    export TMPDIR="\$PWD/.tmp"
    mkdir -p "\$TMPDIR"
    metabolensr finalize --preview preview/qc_preview.rds --overrides overrides.tsv --outdir final
    """
}

workflow METABOLENSR_QC {
    take:
    input_tables
    configuration
    overrides
    preview_only

    main:
    // Existing engines provide three fields; native import companions are optional.
    tables = input_tables.map { row -> row.size() == 3 ? tuple(row[0], row[1], row[2], []) : row }
    METABOLENSR_PREVIEW(tables, configuration)
    if (!preview_only) {
        METABOLENSR_FINALIZE(METABOLENSR_PREVIEW.out.bundle, overrides)
    }

    emit:
    previews = METABOLENSR_PREVIEW.out.bundle
}
