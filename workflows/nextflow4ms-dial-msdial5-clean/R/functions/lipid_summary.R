run_lipid_summary <- function(preprocessed_rds, outdir, config = list()) {
  outdir <- statpipeline_ensure_dir(outdir)
  if (isFALSE(config$run_lipid_module)) {
    summary <- c(statpipeline_summary_base("lipid_summary", config), list(status = "skipped", reason = "run_lipid_module is false"))
    statpipeline_write_json(summary, file.path(outdir, "lipid_summary.json"))
    return(invisible(summary))
  }

  proc <- readRDS(preprocessed_rds)
  finfo <- proc$feature_info
  name_col <- grep("Metabolite|name", colnames(finfo), ignore.case = TRUE)[1]
  ont_col <- grep("Ontology|class", colnames(finfo), ignore.case = TRUE)[1]
  names_vec <- if (!is.na(name_col)) finfo[[name_col]] else rownames(finfo)
  lipid_features <- parse_lipid_features(names_vec)
  summary_table <- cbind(
    data.frame(
      Feature = names_vec,
      Ontology = if (!is.na(ont_col)) finfo[[ont_col]] else NA_character_,
      stringsAsFactors = FALSE
    ),
    lipid_features
  )
  statpipeline_write_tsv(summary_table, file.path(outdir, "lipid_summary.tsv"))

  class_counts <- if (!is.na(ont_col)) as.data.frame(table(finfo[[ont_col]]), stringsAsFactors = FALSE) else data.frame()
  if (nrow(class_counts) > 0) {
    names(class_counts) <- c("Ontology", "Feature_count")
    statpipeline_write_tsv(class_counts, file.path(outdir, "lipid_class_summary.tsv"))
  }

  lion_export <- data.frame(Name = names_vec, stringsAsFactors = FALSE)
  statpipeline_write_tsv(lion_export, file.path(outdir, "LION_export.tsv"))

  summary <- c(
    statpipeline_summary_base("lipid_summary", config),
    list(
      status = "completed",
      feature_count = nrow(summary_table),
      lipid_class_count = if (nrow(class_counts) > 0) nrow(class_counts) else 0
    )
  )
  statpipeline_write_json(summary, file.path(outdir, "lipid_summary.json"))
  invisible(summary)
}
