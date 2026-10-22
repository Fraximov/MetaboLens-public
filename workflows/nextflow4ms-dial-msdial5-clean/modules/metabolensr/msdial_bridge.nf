// MS-DIAL-specific conversion; QC only sees the package experiment contract.
process MSDIAL_TO_METABOLENSR {
    tag "${bundle_id}"
    container params.stats_adapter_container_image
    cpus 1
    memory '2 GB'
    publishDir { "${params.outdir}/metabolensr/${bundle_id}/input" }, mode: 'copy'

    input:
    tuple val(bundle_id), path(alignment, stageAs: 'alignment.mdalign')
    path samplesheet, stageAs: 'samplesheet.csv'
    val factor_column
    path adapter, stageAs: 'adapter.py'

    output:
    tuple val(bundle_id), path('adapted.txt'), path('acquisitions.csv'), emit: table

    script:
    """
    python3 adapter.py --mdalign alignment.mdalign --samplesheet samplesheet.csv \
      --factor-column '${factor_column}' --out adapted.txt --mapping acquisitions.csv
    """
}
