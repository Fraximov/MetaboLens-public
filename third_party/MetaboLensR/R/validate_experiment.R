#' Validate a SummarizedExperiment object
#'
#' @param x A canonicalized SummarizedExperiment object.
#' @return Invisibly returns `TRUE` when validation succeeds.
#' @export
validate_experiment <- function(x) {
  if (!inherits(x, "SummarizedExperiment")) {
    stop("x must be a SummarizedExperiment")
  }

  if (!"intensity" %in% SummarizedExperiment::assayNames(x)) {
    stop("At least one intensity assay should be loaded ")
  }

  rd <- as.data.frame(SummarizedExperiment::rowData(x))
  cd <- as.data.frame(SummarizedExperiment::colData(x))
  schema <- S4Vectors::metadata(x)$schema_version
  if (!is.character(schema) || length(schema) != 1L || is.na(schema) ||
      !schema %in% c("1.0", "2.0")) {
    stop("Unsupported experiment schema_version", call. = FALSE)
  }

  required_features <- c(
    "feature_id",
    "feature_role",
    "feature_name",
    "mz",
    "rt_min",
    "has_ms2"
  )
  required_samples <- c(
    "sample_name",
    "group_label",
    "sample_type",
    "injection_order",
    "batch_id"
  )
  if (schema == "2.0") {
    required_features <- c(required_features, "source_feature_id")
    required_samples <- c(required_samples, "acquisition_id", "source_column")
  }
  missing_feature_columns <- setdiff(required_features, names(rd))
  missing_sample_columns <- setdiff(required_samples, names(cd))

  if (length(missing_feature_columns)) {
    stop(
      "Missing feature metadata",
      paste(missing_feature_columns, collapse = ",")
    )
  }

  if (length(missing_sample_columns)) {
    stop(
      "Missing sample metadata",
      paste(missing_sample_columns, collapse = ",")
    )
  }

  if (anyNA(rd$feature_id) || any(rd$feature_id == "")) {
    stop("feature id cannot be missing or empty")
  }

  if (anyDuplicated(rd$feature_id)) {
    stop("feature id must be unique")
  }

  if (schema == "1.0" && anyDuplicated(cd$sample_name)) {
    stop("Sample names must be unique")
  }
  sample_key <- if (schema == "2.0") cd$acquisition_id else cd$sample_name
  if (!is.character(sample_key) || anyNA(sample_key) ||
      any(!nzchar(trimws(sample_key))) || anyDuplicated(sample_key)) {
    stop("Sample acquisition identities must be present and unique", call. = FALSE)
  }

  if (!identical(
    rownames(x),
    as.character(rd$feature_id)
  )) {
    stop("feature id import from object must match the extracted row data")
  }

  if (!identical(
    colnames(x),
    as.character(sample_key)
  )) {
    stop("sample acquisition identities must match the matrix columns")
  }

  if (!is.character(rd$feature_id)) {
    stop("features_id must be character")
  }

  if (!is.numeric(rd$mz)) {
    stop("mz should be numeric")
  }

  if (!is.numeric(rd$rt_min)) {
    stop("rt should be numeric ")
  }
  if (!is.logical(rd$has_ms2)) stop("has_ms2 must be logical or missing")
  if (schema == "2.0") {
    if (!is.character(rd$source_feature_id) || anyNA(rd$source_feature_id) ||
        any(!nzchar(trimws(rd$source_feature_id))) || anyDuplicated(rd$source_feature_id)) {
      stop("source_feature_id must be present and unique")
    }
    source <- S4Vectors::metadata(x)$source
    if (!is.character(source$tool) || length(source$tool) != 1L ||
        is.na(source$tool) || !nzchar(source$tool) ||
        !is.character(source$quantitation_type) || length(source$quantitation_type) != 1L ||
        is.na(source$quantitation_type) ||
        !source$quantitation_type %in% c("height", "area", "unspecified")) {
      stop("Source tool and quantitation_type are required")
    }
  }
  injection_key <- paste(
    cd$batch_id,
    cd$injection_order,
    sep = "::"
  )

  if (anyDuplicated(injection_key)) {
    stop("injection order must be unique within each batch")
  }

  if (!is.numeric(SummarizedExperiment::assay(x, "intensity"))) {
    stop("The intensity assay must be numeric")
  }
  values <- SummarizedExperiment::assay(x, "intensity")
  if (any(is.infinite(values))) stop("Intensity values must be finite or missing")

  methods::validObject(x)
  invisible(TRUE)
}
