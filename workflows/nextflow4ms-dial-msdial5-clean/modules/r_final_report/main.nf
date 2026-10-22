process R_FINAL_REPORT {

    /*
     * Contract: collected upstream result directories -> final_report/.
     * The collected input primarily acts as a Nextflow dependency barrier. The
     * R wrapper scans staged summaries and writes provenance/pipeline summaries.
     */
    tag "${analysis_config.analysis_mode}"

    input:
    path analysis_dirs
    path r_dir
    val analysis_config

    output:
    path "final_report", emit: final_report_dir

    script:
    """
    Rscript ${r_dir}/cli/final_report_cli.R \
      --outdir final_report \
      --analysis-mode "${analysis_config.analysis_mode}" \
      --output-prefix "${analysis_config.output_prefix}" \
      --report-format "${analysis_config.report_format}" \
      --parse-dir . \
      --preprocess-dir . \
      --normalization-dir . \
      --qc-dir . \
      --statistics-dir . \
      --lipid-dir .
    """
}
