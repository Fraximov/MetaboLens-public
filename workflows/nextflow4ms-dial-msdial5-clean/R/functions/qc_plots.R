run_qc_report <- function(normalization_rds, outdir, config = list()) {
  outdir <- statpipeline_ensure_dir(outdir)
  norm_result <- readRDS(normalization_rds)
  proc <- norm_result$proc
  selected <- config$selected_normalization_method
  if (is.null(selected) || !selected %in% names(norm_result$norm_list)) {
    selected <- norm_result$selected_normalization_method
  }
  data_mat <- norm_result$norm_list[[selected]]
  sinfo <- proc$sample_info
  filetype <- sinfo$Filetype
  class_vec <- sinfo$Class
  inj_order <- sinfo$InjOrder
  class_colors <- assign_class_colors(class_vec)

  plots_dir <- statpipeline_ensure_dir(file.path(outdir, "plots"))
  tables_dir <- statpipeline_ensure_dir(file.path(outdir, "tables"))

  missing_by_sample <- data.frame(
    Sample = rownames(data_mat),
    Filetype = filetype,
    Class = class_vec,
    Missing_count = rowSums(is.na(data_mat)),
    Missing_pct = round(rowMeans(is.na(data_mat)) * 100, 2),
    stringsAsFactors = FALSE
  )
  statpipeline_write_tsv(missing_by_sample, file.path(tables_dir, "missingness_by_sample.tsv"))

  feature_summary <- data.frame(
    Metric = c("samples", "features", "qc_samples", "blank_samples", "biological_samples"),
    Value = c(nrow(data_mat), ncol(data_mat), sum(filetype == "QC"), sum(filetype == "Blank"), sum(filetype == "Sample")),
    stringsAsFactors = FALSE
  )
  statpipeline_write_tsv(feature_summary, file.path(tables_dir, "feature_count_summary.tsv"))

  if (any(filetype == "QC")) {
    prec_qc <- apply(data_mat[filetype == "QC", , drop = FALSE], 2, cv_pct)
    statpipeline_write_tsv(
      data.frame(Feature = colnames(data_mat), QC_CV_pct = round(prec_qc, 4), stringsAsFactors = FALSE),
      file.path(tables_dir, "qc_cv_by_feature.tsv")
    )
    save_fig(plot_qc_precision(prec_qc, paste("QC Precision -", selected)), file.path(plots_dir, "QC_precision"), w_mm = 84, h_mm = 84)
    save_fig(plot_qc_histogram(prec_qc, paste("QC CV -", selected)), file.path(plots_dir, "QC_CV_histogram"), w_mm = 84, h_mm = 84)
  } else {
    prec_qc <- numeric(0)
  }

  if (any(!is.na(inj_order))) {
    save_fig(
      plot_injection_order_drift(data_mat, filetype, inj_order, class_vec, title_suffix = paste0(" - ", selected)),
      file.path(plots_dir, "injection_order_drift"),
      w_mm = 200,
      h_mm = 100
    )
  }

  data_for_pca <- data_mat
  if (do_log_transform && !already_log_transformed) {
    data_for_pca <- log(data_for_pca, base = log_base)
    data_for_pca[!is.finite(as.matrix(data_for_pca))] <- NA
  }
  save_fig(plot_rla(data_for_pca[order(class_vec), , drop = FALSE], class_vec[order(class_vec)], "ag", class_colors), file.path(plots_dir, "RLA_across_group"), w_mm = 220, h_mm = 100)
  save_fig(plot_rla(data_for_pca[order(class_vec), , drop = FALSE], class_vec[order(class_vec)], "wg", class_colors), file.path(plots_dir, "RLA_within_group"), w_mm = 220, h_mm = 100)
  save_fig(plot_pca(data_for_pca, class_vec, class_colors, "PCA - all samples"), file.path(plots_dir, "PCA_all"), w_mm = 160, h_mm = 130)
  save_fig(plot_pca(data_for_pca, class_vec, class_colors, "PCA scaled - all samples", scale_data = TRUE), file.path(plots_dir, "PCA_all_scaled"), w_mm = 160, h_mm = 130)

  if (!is.null(proc$is_data) && proc$is_number > 0) {
    plot_is_monitoring(proc$is_data, proc$is_names, filetype, inj_order, outdir = plots_dir)
  }

  qc_summary <- c(
    statpipeline_summary_base("qc_report", config),
    list(
      selected_normalization_method = selected,
      sample_count = nrow(data_mat),
      feature_count = ncol(data_mat),
      qc_sample_count = sum(filetype == "QC"),
      median_qc_cv = if (length(prec_qc) > 0) median(prec_qc, na.rm = TRUE) else NA_real_,
      missing_value_count = sum(is.na(data_mat)),
      release_question = "Review QC summaries and plots to decide whether this assay is trusted enough to release."
    )
  )
  statpipeline_write_json(qc_summary, file.path(outdir, "qc_summary.json"))
  invisible(qc_summary)
}
