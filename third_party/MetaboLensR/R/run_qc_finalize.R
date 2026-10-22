#' Run the QC finalization workflow
#'
#' @param preview_path Path to a saved QC preview RDS.
#' @param overrides Path to an operator override TSV.
#' @param output_dir Directory for results and the HTML report.
#'
#' @return A named character vector of artifact paths.
#' @export
run_qc_finalize <- function(
  preview_path,
  overrides,
  output_dir
) {
  experiment <- readRDS(preview_path)

  operator_overrides <- utils::read.delim(
    overrides,
    colClasses = "character",
    check.names = FALSE,
    quote = "\"",
    comment.char = ""
  )

  result <- finalize_qc(
    experiment,
    operator_overrides
  )

  if (!dir.exists(output_dir)) {
    created <- dir.create(
      output_dir,
      recursive = TRUE
    )

    if (!created) {
      stop("Could not create output_dir", call. = FALSE)
    }
  }

  report_path <- file.path(output_dir, "qc_report.html")

  render_qc_report(
    result,
    output_file = report_path
  )

  paths <- write_qc_result(
    result,
    output_dir = output_dir
  )
  paths <- c(paths, report = report_path)

  manifest <- .bundle_metadata(result, "final", paths)
  manifest$inputs$preview <- .input_record(preview_path)
  manifest$inputs$overrides <- .input_record(overrides)
  manifest$normalization <- S4Vectors::metadata(result)$normalization_summary

  manifest_path <- file.path(
    output_dir,
    "metabolensr_manifest.json"
  )

  jsonlite::write_json(
    manifest,
    path = manifest_path,
    pretty = TRUE,
    auto_unbox = TRUE,
    na = "null",
    null = "null"
  )

  c(paths, manifest = manifest_path)
}
