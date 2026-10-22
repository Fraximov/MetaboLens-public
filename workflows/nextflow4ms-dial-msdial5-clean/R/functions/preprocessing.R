run_preprocessing <- function(parsed_rds, outdir, config = list()) {
  outdir <- statpipeline_ensure_dir(outdir)
  dat <- readRDS(parsed_rds)
  statpipeline_apply_config(config)

  before_features <- ncol(dat$intensity)
  before_samples <- nrow(dat$sample_info)
  proc <- preprocess_data(dat)

  statpipeline_write_matrix(proc$intensity, file.path(outdir, "cleaned_feature_matrix.tsv"), proc$sample_info)
  saveRDS(proc$intensity, file.path(outdir, "cleaned_feature_matrix.rds"))
  statpipeline_write_tsv(proc$feature_info, file.path(outdir, "cleaned_feature_metadata.tsv"))
  saveRDS(proc$feature_info, file.path(outdir, "cleaned_feature_metadata.rds"))
  statpipeline_write_tsv(proc$sample_info, file.path(outdir, "cleaned_sample_metadata.tsv"))
  saveRDS(proc$sample_info, file.path(outdir, "cleaned_sample_metadata.rds"))
  saveRDS(proc, file.path(outdir, "preprocessed_data.rds"))

  missing_values <- sum(is.na(proc$intensity))
  summary <- c(
    statpipeline_summary_base("preprocessing", config),
    list(
      input_features = before_features,
      output_features = ncol(proc$intensity),
      removed_features = before_features - ncol(proc$intensity),
      input_samples = before_samples,
      output_samples = nrow(proc$sample_info),
      removed_samples = before_samples - nrow(proc$sample_info),
      missing_values_after_preprocessing = missing_values,
      internal_standard_count = proc$is_number,
      note = "QC CV filtering remains in the legacy analysis stage by default to preserve StatPipeline_v2 behavior."
    )
  )
  statpipeline_write_json(summary, file.path(outdir, "preprocessing_summary.json"))
  invisible(list(data = proc, summary = summary))
}
