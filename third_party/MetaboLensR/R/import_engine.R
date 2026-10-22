#' Import an aligned experiment through a registered engine reader
#'
#' @param source_tool Registered engine ID (`msdial` or `masscube`).
#' @param path Engine output path.
#' @param samplesheet Optional data frame mapping exact `source_column` values
#'   to unique `acquisition_id` values. Display sample names may repeat.
#' @param source_metadata Source provenance, including optional `version` and
#'   `quantitation_type` (`height`, `area`, or `unspecified`).
#' @return A validated SummarizedExperiment.
#' @export
import_engine <- function(source_tool, path, samplesheet = NULL,
                          source_metadata = list()) {
  readers <- .engine_importers()
  if (!is.character(source_tool) || length(source_tool) != 1L ||
      is.na(source_tool) || !source_tool %in% names(readers)) {
    stop("Unknown source engine; registered engines: ",
         paste(names(readers), collapse = ", "), call. = FALSE)
  }
  experiment <- readers[[source_tool]](path, samplesheet, source_metadata)
  validate_experiment(experiment)
  experiment
}

# Trusted, package-owned readers. Adding an engine does not change QC or CLI.
.engine_importers <- function() list(msdial = import_msdial, masscube = import_masscube)

.map_acquisitions <- function(experiment, samplesheet) {
  samples <- as.data.frame(SummarizedExperiment::colData(experiment))
  samples$source_column <- colnames(experiment)
  samples$acquisition_id <- samples$source_column
  if (!is.null(samplesheet)) {
    required <- c("source_column", "acquisition_id")
    if (!is.data.frame(samplesheet) || !all(required %in% names(samplesheet)) ||
        anyDuplicated(names(samplesheet))) {
      stop("samplesheet must contain source_column and acquisition_id columns", call. = FALSE)
    }
    for (key in required) {
      samplesheet[[key]] <- as.character(samplesheet[[key]])
      if (anyNA(samplesheet[[key]]) || any(!nzchar(trimws(samplesheet[[key]]))) ||
          anyDuplicated(samplesheet[[key]])) {
        stop("samplesheet ", key, " must be present and unique", call. = FALSE)
      }
    }
    if (!setequal(samplesheet$source_column, samples$source_column)) {
      stop("samplesheet source_column must match every input column exactly", call. = FALSE)
    }
    matched <- samplesheet[match(samples$source_column, samplesheet$source_column), , drop = FALSE]
    # Adapted-file scientific annotations are authoritative. A second metadata
    # source must agree instead of silently changing groups or injection order.
    scientific <- intersect(c("group_label", "sample_type", "batch_id", "injection_order"), names(matched))
    for (key in scientific) {
      if (!identical(as.character(matched[[key]]), as.character(samples[[key]]))) {
        stop("samplesheet conflicts with input ", key, call. = FALSE)
      }
    }
    for (key in setdiff(names(matched), scientific)) samples[[key]] <- matched[[key]]
  }
  rownames(samples) <- samples$acquisition_id
  colnames(experiment) <- samples$acquisition_id
  SummarizedExperiment::colData(experiment) <- S4Vectors::DataFrame(samples, check.names = FALSE)
  experiment
}
