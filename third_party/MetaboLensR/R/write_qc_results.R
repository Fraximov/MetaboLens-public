#' Write finalized QC result artifacts
#'
#' @param experiment A finalized SummarizedExperiment.
#' @param output_dir Directory where result artifacts are written.
#'
#' @return A named character vector containing artifact paths.
#' @export
write_qc_result <- function(
  experiment,
  output_dir
) {
  validate_experiment(experiment)

  result_path <- file.path(
    output_dir,
    "metabolensr_result.rds"
  )

  audit_path <- file.path(
    output_dir,
    "feature_audit_final.tsv"
  )

  normalization_summary_path <- file.path(
    output_dir,
    "normalization_summary.tsv"
  )

  feature_audit_final <- as.data.frame(
    SummarizedExperiment::rowData(experiment),
    optional = TRUE
  )

  assay_mapping <- c(
    raw = "intensity",
    tic = "tic",
    pqn = "pqn",
    bmis = "bmis"
  )

  available_mapping <- assay_mapping[
    assay_mapping %in%
      SummarizedExperiment::assayNames(experiment)
  ]

  matrix_paths <- stats::setNames(
    file.path(
      output_dir,
      paste0(
        "normalized_",
        names(available_mapping),
        ".tsv"
      )
    ),
    names(available_mapping)
  )

  feature_ids <- as.character(
    SummarizedExperiment::rowData(
      experiment
    )$feature_id
  )

  for (output_name in names(available_mapping)) {
    values <- SummarizedExperiment::assay(
      experiment,
      available_mapping[[output_name]]
    )

    exported <- data.frame(
      feature_id = feature_ids,
      as.data.frame(
        values,
        optional = TRUE
      ),
      check.names = FALSE,
      stringsAsFactors = FALSE
    )

    utils::write.table(
      exported,
      file = matrix_paths[[output_name]],
      sep = "\t",
      row.names = FALSE,
      quote = TRUE,
    qmethod = "double",
      na = "",
      fileEncoding = "UTF-8"
    )
  }
  saveRDS(
    experiment,
    result_path
  )

  normalization_summary <-
    S4Vectors::metadata(
      experiment
    )$normalization_summary

  utils::write.table(
    normalization_summary,
    file = normalization_summary_path,
    sep = "\t",
    row.names = FALSE,
    quote = TRUE,
    qmethod = "double",
    na = "",
    fileEncoding = "UTF-8"
  )

  utils::write.table(
    feature_audit_final,
    file = audit_path,
    sep = "\t",
    row.names = FALSE,
    quote = TRUE,
    qmethod = "double",
    na = "",
    fileEncoding = "UTF-8"
)


  c(
    result = result_path,
    audit = audit_path,
    samples = .write_sample_metadata(experiment, output_dir),
    matrix_paths,
    normalization_summary = normalization_summary_path
  )
}
