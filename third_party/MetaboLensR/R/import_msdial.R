#' Import an adapted MS-DIAL result
#'
#' Reads an adapted MS-DIAL result, canonicalizes its feature metadata, and
#' validates the resulting experiment.
#'
#' @param path Path to an adapted MS-DIAL result file.
#' @param samplesheet Optional acquisition mapping; see [import_engine()].
#' @param source_metadata Optional source version and quantitation provenance.
#' @param internal_standard_ids Explicit native feature IDs of standards,
#'   in addition to the MS-DIAL `IS:` name convention.
#'
#' @return A canonicalized and validated
#'   [SummarizedExperiment::SummarizedExperiment] object.
#' @export
# nolint start: object_usage_linter.
import_msdial <- function(path, samplesheet = NULL, source_metadata = list(),
                          internal_standard_ids = character()) {
  if (!is.list(source_metadata) ||
      (length(source_metadata) && (is.null(names(source_metadata)) ||
       any(!nzchar(names(source_metadata))) || anyDuplicated(names(source_metadata))))) {
    stop("source_metadata must be a named list", call. = FALSE)
  }
  quantity <- source_metadata$quantitation_type
  if (is.null(quantity)) quantity <- "unspecified"
  if (!is.character(quantity) || length(quantity) != 1L || is.na(quantity) ||
      !quantity %in% c("height", "area", "unspecified")) {
    stop("quantitation_type must be height, area, or unspecified", call. = FALSE)
  }
  experiment <- read_adapted_msdial(path)
  experiment <- canonicalize_msdial_metadata(experiment,
                                             internal_standard_ids = internal_standard_ids)
  experiment <- .map_acquisitions(experiment, samplesheet)
  metadata <- S4Vectors::metadata(experiment)
  metadata$schema_version <- "2.0"
  metadata$source <- source_metadata
  metadata$source$tool <- "msdial"
  metadata$source$quantitation_type <- quantity
  metadata$source$retention_time_unit <- "minute"
  S4Vectors::metadata(experiment) <- metadata

  validate_experiment(experiment)

  experiment
}
# nolint end
