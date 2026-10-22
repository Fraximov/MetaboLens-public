process R_LIPID_SUMMARY {

    /*
     * Contract: preprocess/preprocessed_data.rds -> lipid_summary/.
     * The shared StatPipeline v2 subworkflow normally skips this process in
     * qc_only mode. The run_lipid_module argument remains a defensive guard for
     * ad-hoc module execution.
     */
    tag "${preprocess_dir.simpleName}"

    input:
    path preprocess_dir
    path r_dir
    val analysis_config

    output:
    path "lipid_summary", emit: lipid_dir

    script:
    """
    Rscript ${r_dir}/cli/lipid_summary_cli.R \
      --preprocessed-rds ${preprocess_dir}/preprocessed_data.rds \
      --outdir lipid_summary \
      --analysis-mode "${analysis_config.analysis_mode}" \
      --output-prefix "${analysis_config.output_prefix}" \
      --run-lipid-module "${analysis_config.run_lipid_module}" \
      --report-format "${analysis_config.report_format}"
    """
}
