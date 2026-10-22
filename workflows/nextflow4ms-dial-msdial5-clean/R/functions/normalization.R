build_normalization_list <- function(proc, methods, config = list(), outdir = NULL) {
  statpipeline_apply_config(config)
  methods <- unique(methods)
  int <- proc$intensity
  sinfo <- proc$sample_info
  filetype <- sinfo$Filetype
  inj_order <- sinfo$InjOrder
  norm_list <- list()
  bmis_assignment <- NULL

  if ("Raw" %in% methods) norm_list[["Raw"]] <- int
  if ("TIC" %in% methods) norm_list[["TIC"]] <- normalize_tic(int, proc$intensity_allfeatures)
  if ("mTIC" %in% methods) norm_list[["mTIC"]] <- normalize_mtic(int, proc$is_number)
  if ("BMIS" %in% methods) {
    bmis_res <- normalize_bmis(int, filetype, proc$is_data, proc$is_names)
    if (!is.null(bmis_res$assignment)) {
      norm_list[["BMIS"]] <- bmis_res$data
      bmis_assignment <- bmis_res$assignment
    }
  }
  if ("QC-RSC" %in% methods) {
    audit_dir <- if (is.null(outdir)) NULL else statpipeline_ensure_dir(file.path(outdir, "qc_rsc_audit"))
    norm_list[["QC-RSC"]] <- normalize_qcrsc(int, filetype, inj_order, outdir = audit_dir)
  }

  list(norm_list = norm_list, bmis_assignment = bmis_assignment)
}

run_normalization <- function(preprocessed_rds, outdir, config = list()) {
  outdir <- statpipeline_ensure_dir(outdir)
  proc <- readRDS(preprocessed_rds)
  methods <- config$normalization_methods
  if (is.null(methods) || length(methods) == 0) methods <- c("Raw", "TIC", "mTIC", "BMIS")

  built <- build_normalization_list(proc, methods, config, outdir)
  norm_list <- built$norm_list
  if (length(norm_list) == 0) stop("No normalization methods produced output")

  matrices_dir <- statpipeline_ensure_dir(file.path(outdir, "normalized_matrices"))
  for (method_name in names(norm_list)) {
    slug <- gsub("[^A-Za-z0-9._-]", "_", method_name)
    statpipeline_write_matrix(norm_list[[method_name]], file.path(matrices_dir, paste0("normalized_", slug, "_matrix.tsv")), proc$sample_info)
    saveRDS(norm_list[[method_name]], file.path(matrices_dir, paste0("normalized_", slug, "_matrix.rds")))
  }
  if (!is.null(built$bmis_assignment)) {
    statpipeline_write_tsv(built$bmis_assignment, file.path(outdir, "BMIS_assignment.tsv"))
  }

  comp <- NULL
  recommendation <- NULL
  selected <- config$selected_normalization_method
  if (length(norm_list) >= 2) {
    comp <- compare_normalizations(norm_list, proc$sample_info$Filetype, proc$sample_info$Class)
    if (!is.null(comp)) {
      plot_norm_comparison(comp, outdir)
      statpipeline_write_tsv(comp$summary, file.path(outdir, "normalization_qc_metrics.tsv"))
      ranking <- comp$summary[order(comp$summary$Composite_Rank), , drop = FALSE]
      statpipeline_write_tsv(ranking, file.path(outdir, "normalization_ranking.tsv"))
      recommendation <- comp$recommendation
      if (is.null(selected) || !selected %in% names(norm_list)) {
        selected <- ranking$Method[[1]]
      }
    }
  }
  if (is.null(selected) || !selected %in% names(norm_list)) selected <- names(norm_list)[[1]]

  result <- list(
    proc = proc,
    norm_list = norm_list,
    bmis_assignment = built$bmis_assignment,
    comparison = comp,
    selected_normalization_method = selected
  )
  saveRDS(result, file.path(outdir, "normalization_results.rds"))
  writeLines(selected, file.path(outdir, "selected_normalization.txt"))
  statpipeline_write_json(
    list(
      selected_normalization_method = selected,
      recommendation = recommendation,
      methods = names(norm_list)
    ),
    file.path(outdir, "selected_normalization.json")
  )

  summary <- c(
    statpipeline_summary_base("normalization", config),
    list(
      methods_run = names(norm_list),
      selected_normalization_method = selected,
      feature_count = ncol(proc$intensity),
      sample_count = nrow(proc$sample_info),
      recommendation = recommendation
    )
  )
  statpipeline_write_json(summary, file.path(outdir, "normalization_summary.json"))
  invisible(result)
}
