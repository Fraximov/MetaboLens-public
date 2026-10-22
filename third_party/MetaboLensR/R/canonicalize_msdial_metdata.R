#' Add canonical MetaboLensR feature metadata
#'
#' Preserves all original MS-DIAL columns while adding stable,
#' typed aliases used by downstream MetaboLensR functions.
#'
#' @param experiment A SummarizedExperiment constructed from an
#'   adapted MS-DIAL table.
#' @param mapping Named list mapping canonical names to source columns.
#' @param internal_standard_ids Optional feature IDs corresponding to
#'   internal standards.
#'
#' @return The experiment with canonical rowData fields.
#'
#' @keywords internal
canonicalize_msdial_metadata <- function(
  experiment,
  mapping = default_msdial_column_mapping(), # nolint: object_usage_linter.
  internal_standard_ids = character()
) {
  if (!inherits(experiment, "SummarizedExperiment")) {
    stop(
      "experiment must be a SummarizedExperiment.",
      call. = FALSE
    )
  }

  feature_data <- as.data.frame(
    SummarizedExperiment::rowData(experiment),
    optional = TRUE
  )

  required_keys <- c(
    "feature_id",
    "feature_name",
    "mz",
    "rt_min"
  )

  required_source_columns <- unlist(
    mapping[required_keys],
    use.names = FALSE
  )

  missing_required <- setdiff(
    required_source_columns,
    names(feature_data)
  )

  if (length(missing_required) > 0L) {
    stop(
      "Required MS-DIAL column(s) missing: ",
      paste(missing_required, collapse = ", "),
      call. = FALSE
    )
  }

  get_optional_column <- function(key, default = NA) {
    source_name <- mapping[[key]]

    if (
      is.null(source_name) ||
        !source_name %in% names(feature_data)
    ) {
      return(rep(default, nrow(feature_data)))
    }

    feature_data[[source_name]]
  }

  as_double <- function(x, field_name) {
    result <- suppressWarnings(
      as.numeric(as.character(x))
    )

    conversion_failed <-
      !is.na(x) &
      nzchar(trimws(as.character(x))) &
      is.na(result)

    if (any(conversion_failed)) {
      stop(
        "Could not convert canonical field '",
        field_name,
        "' to numeric.",
        call. = FALSE
      )
    }

    result
  }

  as_logical <- function(x, field_name) {
    result <- suppressWarnings(
      as.logical(trimws(as.character(x)))
    )

    conversion_failed <-
      !is.na(x) &
      nzchar(trimws(as.character(x))) &
      is.na(result)

    if (any(conversion_failed)) {
      stop(
        "Could not convert canonical field '",
        field_name,
        "' to logical.",
        call. = FALSE
      )
    }

    result
  }

  feature_data$feature_id <- as.character(
    feature_data[[mapping$feature_id]]
  )
  feature_data$source_feature_id <- feature_data$feature_id

  feature_data$feature_name <- as.character(
    feature_data[[mapping$feature_name]]
  )

  feature_data$mz <- as_double(
    feature_data[[mapping$mz]],
    "mz"
  )

  feature_data$rt_min <- as_double(
    feature_data[[mapping$rt_min]],
    "rt_min"
  )

  is_internal_standard <- grepl(
    "^IS:", feature_data$feature_name, ignore.case = TRUE
  )

  feature_data$adduct <- as.character(
    get_optional_column("adduct", NA_character_)
  )

  feature_data$ontology <- as.character(
    get_optional_column("ontology", NA_character_)
  )

  feature_data$fill_percent <- as_double(
    get_optional_column("fill_percent", NA_real_),
    "fill_percent"
  )

  feature_data$sn_average <- as_double(
    get_optional_column("sn_average", NA_real_),
    "sn_average"
  )

  feature_data$rt_matched <- as_logical(
    get_optional_column("rt_matched", NA),
    "rt_matched"
  )

  feature_data$mz_matched <- as_logical(
    get_optional_column("mz_matched", NA),
    "mz_matched"
  )

  msms_spectrum <- as.character(
    get_optional_column("msms_spectrum", NA_character_)
  )

  feature_data$has_ms2 <-
    !is.na(msms_spectrum) &
    nzchar(trimws(msms_spectrum)) &
    !msms_spectrum %in% c("NA", "null", "None")
  if (is.null(mapping$msms_spectrum) || !mapping$msms_spectrum %in% names(feature_data)) {
    feature_data$has_ms2 <- rep(NA, nrow(feature_data))
  }

  internal_standard_ids <- as.character(
    internal_standard_ids
  )
  if (anyNA(internal_standard_ids) ||
      any(!internal_standard_ids %in% feature_data$feature_id)) {
    stop("Unknown internal-standard feature ID", call. = FALSE)
  }
  is_internal_standard <- is_internal_standard | feature_data$feature_id %in% internal_standard_ids

  feature_data$feature_role <- ifelse(
    is_internal_standard,
    "internal_standard",
    "analyte"
  )

  rownames(feature_data) <- feature_data$feature_id

  SummarizedExperiment::rowData(experiment) <-
    S4Vectors::DataFrame(feature_data, check.names = FALSE)

  rownames(experiment) <- feature_data$feature_id

  experiment_metadata <-
    S4Vectors::metadata(experiment)

  experiment_metadata$source_column_mapping <- mapping

  S4Vectors::metadata(experiment) <-
    experiment_metadata

  experiment
}
