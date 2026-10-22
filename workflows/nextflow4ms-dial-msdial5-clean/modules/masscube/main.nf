/* Single aligned native engine task; package QC/finalization remain shared. */
process MASSCUBE_PROCESS {
    container params.masscube_container_image
    cpus 1
    memory '6 GB'
    time '24h'

    input:
    path prepared_data
    path samplesheet, stageAs: 'samplesheet.csv'
    path scientific_config, stageAs: 'masscube_config.json'
    path package_config, stageAs: 'package_config.json'
    path ms2_library
    path runner, stageAs: 'masscube_runner.py'
    path evidence_producer, stageAs: 'masscube_evidence.py'

    output:
    tuple val('aligned'), path('masscube/aligned_feature_table.tsv'), path('masscube/samplesheet.csv'), path('masscube/acquisition_evidence.tsv'), emit: table
    path 'masscube/metabolensr_config.json', emit: configuration
    path 'masscube', emit: artifacts

    script:
    def libraryArg = ms2_library ? "--library '${ms2_library.toString().replace("'", "'\\''")}'" : ''
    """
    export TMPDIR="\$PWD/.tmp"
    export NUMBA_CACHE_DIR="\$PWD/.numba"
    export MPLCONFIGDIR="\$PWD/.matplotlib"
    export OMP_NUM_THREADS=${task.cpus}
    export OPENBLAS_NUM_THREADS=${task.cpus}
    export MKL_NUM_THREADS=${task.cpus}
    mkdir -p "\$TMPDIR" "\$NUMBA_CACHE_DIR" "\$MPLCONFIGDIR"
    python3 masscube_runner.py \\
      --inputs '${prepared_data}' \\
      --samplesheet samplesheet.csv \\
      --config masscube_config.json \\
      --metabolensr-config package_config.json \\
      --container-image '${params.masscube_container_image}' \\
      --output masscube ${libraryArg}
    """
}
