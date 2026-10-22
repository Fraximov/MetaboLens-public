run_parse_msdial <- function(input, outdir, output_prefix = "metabolens", sample_metadata = NULL, config = list()) {
  outdir <- statpipeline_ensure_dir(outdir)
  dat <- load_msdial(input)

  statpipeline_write_matrix(dat$intensity, file.path(outdir, "raw_feature_matrix.tsv"), dat$sample_info)
  saveRDS(dat$intensity, file.path(outdir, "raw_feature_matrix.rds"))
  statpipeline_write_tsv(dat$feature_info, file.path(outdir, "feature_metadata.tsv"))
  saveRDS(dat$feature_info, file.path(outdir, "feature_metadata.rds"))
  statpipeline_write_tsv(dat$sample_info, file.path(outdir, "sample_metadata.tsv"))
  saveRDS(dat$sample_info, file.path(outdir, "sample_metadata.rds"))

  if (!is.null(sample_metadata) && file.exists(sample_metadata)) {
    file.copy(sample_metadata, file.path(outdir, "input_sample_metadata.tsv"), overwrite = TRUE)
  }

  name_col <- grep("Metabolite|name", colnames(dat$feature_info), ignore.case = TRUE)[1]
  annotation_cols <- unique(na.omit(c(
    name_col,
    grep("Ontology|class|Adduct|Formula|SMILES|InChI|MS/MS|Score|RT|retention|m/z|mz", colnames(dat$feature_info), ignore.case = TRUE)
  )))
  annotation_table <- if (length(annotation_cols) > 0) dat$feature_info[, annotation_cols, drop = FALSE] else dat$feature_info
  statpipeline_write_tsv(annotation_table, file.path(outdir, "annotation_table.tsv"))
  saveRDS(annotation_table, file.path(outdir, "annotation_table.rds"))
  saveRDS(dat, file.path(outdir, "parsed_msdial.rds"))

  summary <- c(
    statpipeline_summary_base("parse_msdial", config),
    list(
      input_file = normalizePath(input, winslash = "/", mustWork = TRUE),
      output_prefix = output_prefix,
      sample_count = nrow(dat$sample_info),
      feature_count = ncol(dat$intensity),
      metadata_column_count = ncol(dat$feature_info),
      internal_standard_count = dat$is_number,
      filetype_counts = as.list(table(dat$sample_info$Filetype)),
      class_counts = as.list(table(dat$sample_info$Class))
    )
  )
  statpipeline_write_json(summary, file.path(outdir, "parse_summary.json"))
  invisible(list(data = dat, summary = summary))
}
