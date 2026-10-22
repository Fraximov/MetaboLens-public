process R_QC_REPORT {

    /*
     * Contract: normalize/normalization_results.rds -> qc_report/.
     * QC reporting uses the selected or recommended normalization method and
     * should stay free of biological contrast assumptions.
     */
    tag "${normalization_dir.simpleName}"

    input:
    path normalization_dir
    path r_dir
    val analysis_config

    output:
    path "qc_report", emit: qc_dir

    script:
    """
    Rscript ${r_dir}/cli/qc_report_cli.R \
      --normalization-rds ${normalization_dir}/normalization_results.rds \
      --outdir qc_report \
      --analysis-mode "${analysis_config.analysis_mode}" \
      --output-prefix "${analysis_config.output_prefix}" \
      --selected-normalization-method "${analysis_config.selected_normalization_method ?: ''}" \
      --qc-cv-threshold "${analysis_config.qc_cv_threshold}" \
      --run-stats "${analysis_config.run_stats}" \
      --test-method "${analysis_config.test_method}" \
      --p-adjust-method "${analysis_config.p_adjust_method}" \
      --run-lipid-module "${analysis_config.run_lipid_module}" \
      --report-format "${analysis_config.report_format}"
    """
}
