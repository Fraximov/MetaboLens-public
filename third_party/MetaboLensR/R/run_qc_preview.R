#' Run the QC preview workflow
#'
#' @param input Path to the aligned table accepted by the configured engine reader.
#' @param config Path to a JSON configuration file.
#' @param output_dir Directory for the preview bundle.
#' @param samplesheet Optional acquisition mapping data frame or CSV path.
#'
#' @return A named character vector of artifact paths.
#' @export
run_qc_preview <- function(
  input,
  config,
  output_dir,
  samplesheet = NULL
) {
  settings <- jsonlite::read_json(
    config,
    simplifyVector = FALSE
  )

  if (!identical(settings$schema_version, "1.0")) {
    stop("Unsupported config schema_version", call. = FALSE)
  }

  profile <- resolve_curation_profile(settings$profile)
  if ("enabled_rules" %in% names(settings)) {
    selection <- settings$enabled_rules
    # Keep JSON objects, arrays and null distinct, including empty arrays.
    if (!is.list(selection) || is.null(names(selection)) ||
        any(!vapply(selection, function(ids) {
          is.list(ids) && is.null(names(ids))
        }, logical(1)))) {
      stop("enabled_rules must be an object of rule ID arrays", call. = FALSE)
    }
    profile <- resolve_curation_profile(settings$profile, selection)
  }

  inputs <- list(source = .input_record(input), config = .input_record(config))
  if (is.character(samplesheet) && length(samplesheet) == 1L) {
    inputs$samplesheet <- .input_record(samplesheet)
    samplesheet <- utils::read.csv(samplesheet, colClasses = "character", check.names = FALSE,
                                   na.strings = "")
  }
  source_tool <- settings$source_tool
  if (is.null(source_tool)) source_tool <- "msdial"
  source_metadata <- settings$source_metadata
  if (is.null(source_metadata)) source_metadata <- list()
  experiment <- import_engine(source_tool, input, samplesheet, source_metadata)
  metadata <- S4Vectors::metadata(experiment)
  metadata$configuration <- settings
  metadata$inputs <- inputs
  S4Vectors::metadata(experiment) <- metadata

  preview <- apply_curation_profile(
    experiment,
    profile = profile
  )

  if (!dir.exists(output_dir)) {
    created <- dir.create(
      output_dir,
      recursive = TRUE
    )
    if (!created) {
      stop("Cound not create output_dir.", call. = FALSE)
    }
  }

  write_qc_preview(
    preview,
    output_dir = output_dir
  )
}
