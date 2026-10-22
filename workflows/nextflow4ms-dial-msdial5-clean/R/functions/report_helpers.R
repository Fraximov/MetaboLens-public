run_final_report <- function(outdir, config = list(), parse_dir = NULL, preprocess_dir = NULL, normalization_dir = NULL, qc_dir = NULL, statistics_dir = NULL, lipid_dir = NULL) {
  outdir <- statpipeline_ensure_dir(outdir)
  inputs <- list(
    parse_dir = parse_dir,
    preprocess_dir = preprocess_dir,
    normalization_dir = normalization_dir,
    qc_dir = qc_dir,
    statistics_dir = statistics_dir,
    lipid_dir = lipid_dir
  )
  # The final-report module is intentionally lightweight for now: it records
  # which upstream module summaries are present. A richer HTML/PDF report can
  # build on this same summary-file scan without changing upstream contracts.
  summary_files <- unlist(lapply(inputs, function(path) {
    if (is.null(path) || !dir.exists(path)) return(character(0))
    list.files(path, pattern = "summary.*\\.json$|contrast_summary\\.json$|selected_normalization\\.json$", recursive = TRUE, full.names = TRUE)
  }), use.names = FALSE)

  provenance <- c(
    statpipeline_summary_base("final_report", config),
    list(
      input_directories = inputs,
      summary_files = normalizePath(summary_files, winslash = "/", mustWork = FALSE),
      analysis_mode = config$analysis_mode
    )
  )
  statpipeline_write_json(provenance, file.path(outdir, "provenance.json"))
  statpipeline_write_json(provenance, file.path(outdir, "pipeline_summary.json"))

  methods <- c(
    "MS-DIAL aligned feature tables were processed with the modular MetaboLens R analysis wrapper around StatPipeline_v2.R.",
    paste0("Analysis mode: ", config$analysis_mode %||% "unknown", "."),
    "Module summaries and parameters are recorded in provenance.json."
  )
  writeLines(methods, file.path(outdir, "methods_paragraph.txt"))
  invisible(provenance)
}

`%||%` <- function(x, y) {
  if (is.null(x)) y else x
}
