process R_NORMALIZE {

    /*
     * Contract: preprocess/preprocessed_data.rds -> normalize/normalization_results.rds.
     * This is the stage that compares Raw/TIC/BMIS/etc. and records the
     * selected normalization method for QC and statistics.
     */
    tag "${preprocess_dir.simpleName}"

    input:
    path preprocess_dir
    path r_dir
    val analysis_config

    output:
    path "normalize", emit: normalization_dir

    script:
    """
    Rscript ${r_dir}/cli/normalize_cli.R \
      --preprocessed-rds ${preprocess_dir}/preprocessed_data.rds \
      --outdir normalize \
      --analysis-mode "${analysis_config.analysis_mode}" \
      --output-prefix "${analysis_config.output_prefix}" \
      --normalization-methods "${analysis_config.normalization_methods}" \
      --selected-normalization-method "${analysis_config.selected_normalization_method ?: ''}" \
      --qc-cv-threshold "${analysis_config.qc_cv_threshold}" \
      --run-stats "${analysis_config.run_stats}" \
      --test-method "${analysis_config.test_method}" \
      --p-adjust-method "${analysis_config.p_adjust_method}" \
      --run-lipid-module "${analysis_config.run_lipid_module}" \
      --report-format "${analysis_config.report_format}"
    """
}
