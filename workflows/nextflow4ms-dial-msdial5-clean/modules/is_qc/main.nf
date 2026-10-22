/*
 * Internal-standard QC review gate.
 *
 * This module inspects only the configured IS target list against prepared
 * mzML files. It does not run feature detection or alter downstream MS-DIAL
 * behavior.
 */

process extract_is_qc {

    tag "internal-standard-qc"

    input:
    path prepared_data
    path is_library
    path samplesheet
    path extractor_script
    val qc_config

    output:
    path "is-qc", emit: artifacts

    script:
    """
    mkdir -p is-qc &&
    python3 ${extractor_script} \
      --input-dir ${prepared_data} \
      --targets ${is_library} \
      --samplesheet ${samplesheet} \
      --outdir is-qc \
      --mz-tolerance-da "${qc_config.mz_tolerance_da}" \
      --rt-tolerance-min "${qc_config.rt_tolerance_min}" \
      --min-peak-height "${qc_config.min_peak_height}" \
      --min-sample-pass-fraction "${qc_config.min_sample_pass_fraction}" \
      --min-target-pass-fraction "${qc_config.min_target_pass_fraction}" \
      --max-qc-cv-percent "${qc_config.max_qc_cv_percent}"
    """
}

workflow RUN_IS_QC {
    take:
    prepared_data
    is_library
    samplesheet
    extractor_script
    qc_config

    main:
    extract_is_qc(
        prepared_data,
        is_library,
        samplesheet,
        extractor_script,
        qc_config
    )

    emit:
    artifacts = extract_is_qc.out.artifacts
}
