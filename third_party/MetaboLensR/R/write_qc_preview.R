#' Write QC preview artifacts
#'
#' @param experiment A curated SummarizedExperiment.
#' @param output_dir Directory where preview artifacts are written.
#'
#' @return A named character vector containing artifact paths.
#' @export
write_qc_preview <- function(
  experiment,
  output_dir
) {
  preview_path <- file.path(
    output_dir,
    "qc_preview.rds"
  )
  audit_path <- file.path(
    output_dir,
    "feature_audit.tsv"
  )
  summary_path <- file.path(
    output_dir,
    "qc_preview_summary.json"
  )

  validate_experiment(experiment)

  saveRDS(
    experiment,
    preview_path
  )

  feature_audit <- as.data.frame(
    SummarizedExperiment::rowData(experiment),
    optional = TRUE
  )

  decision_levels <- c(
    "include",
    "review",
    "exclude"
  )

  statistics_counts <- table(
    factor(
      feature_audit$automatic_decision_statistics,
      levels = decision_levels
    )
  )

  annotation_counts <- table(
    factor(
      feature_audit$automatic_decision_annotation,
      levels = decision_levels
    )
  )

  preview_summary <- list(
    profile_name =
      S4Vectors::metadata(experiment)$curation$profile$name,
    feature_count = nrow(experiment),
    sample_count = ncol(experiment),
    automatic_decision_counts = list(
      statistics = as.list(
        stats::setNames(
          as.integer(statistics_counts),
          decision_levels
        )
      ),
      annotation = as.list(
        stats::setNames(
          as.integer(annotation_counts),
          decision_levels
        )
      )
    )
  )

  utils::write.table(
    feature_audit,
    file = audit_path,
    sep = "\t",
    row.names = FALSE,
    quote = TRUE,
    qmethod = "double",
    na = "",
    fileEncoding = "UTF-8"
  )

  paths <- c(preview = preview_path, audit = audit_path,
             samples = .write_sample_metadata(experiment, output_dir))
  preview_summary <- c(.bundle_metadata(experiment, "preview", paths), preview_summary)

  jsonlite::write_json(
    preview_summary,
    path = summary_path,
    pretty = TRUE,
    auto_unbox = TRUE,
    na = "null",
    null = "null"
  )

  c(paths, summary = summary_path)
}
