run_statistics_module <- function(normalization_rds, outdir, config = list()) {
  outdir <- statpipeline_ensure_dir(outdir)
  statpipeline_apply_config(config)
  if (isFALSE(config$run_stats)) {
    summary <- c(statpipeline_summary_base("statistics", config), list(status = "skipped", reason = "run_stats is false"))
    statpipeline_write_json(summary, file.path(outdir, "contrast_summary.json"))
    return(invisible(summary))
  }

  norm_result <- readRDS(normalization_rds)
  selected <- config$selected_normalization_method
  if (is.null(selected) || !selected %in% names(norm_result$norm_list)) {
    selected <- norm_result$selected_normalization_method
  }
  methods_to_run <- selected
  if (identical(config$analysis_mode, "full") && (is.null(config$selected_normalization_method) || identical(config$selected_normalization_method, ""))) {
    methods_to_run <- names(norm_result$norm_list)
  }

  analysis <- list()
  for (i in seq_along(methods_to_run)) {
    method_name <- methods_to_run[[i]]
    method_dir <- if (length(methods_to_run) > 1) {
      file.path(outdir, sprintf("%02d_%s", i, gsub("[^A-Za-z0-9._-]", "_", method_name)))
    } else {
      outdir
    }
    bmis_assignment <- if (identical(method_name, "BMIS")) norm_result$bmis_assignment else NULL
    analysis[[method_name]] <- run_analysis(norm_result$norm_list[[method_name]], norm_result$proc, method_name, method_dir, bmis_assignment)
  }

  stat_files <- list.files(outdir, pattern = "statistical_results_.*\\.(txt|xlsx)$", recursive = TRUE, full.names = FALSE)
  summary <- c(
    statpipeline_summary_base("statistics", config),
    list(
      status = "completed",
      selected_normalization_method = selected,
      methods_run = methods_to_run,
      result_files = stat_files,
      contrast_definition = config$contrasts
    )
  )
  statpipeline_write_json(summary, file.path(outdir, "contrast_summary.json"))
  invisible(list(analysis = analysis, summary = summary))
}
