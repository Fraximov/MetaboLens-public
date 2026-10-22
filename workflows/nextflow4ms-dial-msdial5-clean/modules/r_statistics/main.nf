process R_STATISTICS {

    /*
     * Contract: normalize/normalization_results.rds -> statistics/.
     * The shared StatPipeline v2 subworkflow normally skips this process in
     * qc_only mode. The run_stats argument remains a defensive guard for
     * ad-hoc module execution.
     */
    tag "${normalization_dir.simpleName}"

    input:
    path normalization_dir
    path r_dir
    val analysis_config

    output:
    path "statistics", emit: statistics_dir

    script:
    """
    Rscript ${r_dir}/cli/statistics_cli.R \
      --normalization-rds ${normalization_dir}/normalization_results.rds \
      --outdir statistics \
      --analysis-mode "${analysis_config.analysis_mode}" \
      --output-prefix "${analysis_config.output_prefix}" \
      --selected-normalization-method "${analysis_config.selected_normalization_method ?: ''}" \
      --run-stats "${analysis_config.run_stats}" \
      --contrasts "${analysis_config.contrasts ?: ''}" \
      --test-method "${analysis_config.test_method}" \
      --p-adjust-method "${analysis_config.p_adjust_method}" \
      --run-lipid-module "${analysis_config.run_lipid_module}" \
      --report-format "${analysis_config.report_format}"
    """
}
