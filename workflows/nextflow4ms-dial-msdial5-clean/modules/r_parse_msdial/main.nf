process R_PARSE_MSDIAL {

    /*
     * Contract: adapted MS-DIAL txt -> parse/parsed_msdial.rds.
     * The TSV/RDS side outputs are for inspection and MetaboLens artifacts;
     * the next module consumes parsed_msdial.rds.
     */
    tag "${msdial_txt.simpleName}"

    input:
    path msdial_txt
    path r_dir
    val analysis_config

    output:
    path "parse", emit: parse_dir

    script:
    """
    Rscript ${r_dir}/cli/parse_msdial_cli.R \
      --input ${msdial_txt} \
      --outdir parse \
      --analysis-mode "${analysis_config.analysis_mode}" \
      --output-prefix "${analysis_config.output_prefix}" \
      --keep-unknowns "${analysis_config.keep_unknowns}" \
      --blank-filter-ratio "${analysis_config.blank_filter_ratio}" \
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
