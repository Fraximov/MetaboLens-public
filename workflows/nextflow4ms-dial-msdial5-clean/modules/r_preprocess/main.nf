process R_PREPROCESS {

    /*
     * Contract: parse/parsed_msdial.rds -> preprocess/preprocessed_data.rds.
     * Discovery mode is selected in the calling workflow by passing
     * keep_unknowns=true in analysis_config.
     */
    tag "${parse_dir.simpleName}"

    input:
    path parse_dir
    path r_dir
    val analysis_config

    output:
    path "preprocess", emit: preprocess_dir

    script:
    """
    Rscript ${r_dir}/cli/preprocess_cli.R \
      --parsed-rds ${parse_dir}/parsed_msdial.rds \
      --outdir preprocess \
      --analysis-mode "${analysis_config.analysis_mode}" \
      --output-prefix "${analysis_config.output_prefix}" \
      --keep-unknowns "${analysis_config.keep_unknowns}" \
      --remove-blank-features "${analysis_config.remove_blank_features}" \
      --blank-filter-ratio "${analysis_config.blank_filter_ratio}" \
      --filter-by-cv "${analysis_config.filter_by_cv}" \
      --qc-cv-threshold "${analysis_config.qc_cv_threshold}" \
      --imputation-method "${analysis_config.imputation_method}" \
      --normalization-methods "${analysis_config.normalization_methods}" \
      --selected-normalization-method "${analysis_config.selected_normalization_method ?: ''}" \
      --run-stats "${analysis_config.run_stats}" \
      --test-method "${analysis_config.test_method}" \
      --p-adjust-method "${analysis_config.p_adjust_method}" \
      --run-lipid-module "${analysis_config.run_lipid_module}" \
      --report-format "${analysis_config.report_format}"
    """
}
