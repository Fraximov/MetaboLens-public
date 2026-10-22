#' Apply operator curation overrides
#'
#' @param experiment A curated SummarizedExperiment.
#' @param overrides A data frame containing feature-level overrides.
#'
#' @return The experiment with operator and effective decisions updated.
#' @export
apply_curation_overrides <- function(
  experiment,
  overrides
) {
  validate_experiment(experiment)

  required <- c("feature_id", "operator_override_statistics", "operator_override_annotation")
  if (!is.data.frame(overrides) || !all(required %in% names(overrides)) ||
      anyDuplicated(names(overrides))) {
    stop("Overrides are missing required columns: ", paste(required, collapse = ", "), call. = FALSE)
  }
  ids <- as.character(overrides$feature_id)
  if (anyNA(ids) || any(!nzchar(trimws(ids))) || anyDuplicated(ids)) {
    stop("Override feature IDs must be present and unique", call. = FALSE)
  }
  for (column in required[-1L]) {
    values <- as.character(overrides[[column]])
    if (anyNA(values) || any(!values %in% c("automatic", "include", "exclude"))) {
      stop("Override values must be automatic, include, or exclude", call. = FALSE)
    }
  }

  feature_data <- SummarizedExperiment::rowData(experiment)

  feature_index <- match(
    as.character(overrides$feature_id),
    as.character(feature_data$feature_id)
  )
  unknown_feature <- is.na(feature_index)

  if (any(unknown_feature)) {
    unknown_ids <- unique(
      as.character(
        overrides$feature_id[unknown_feature]
      )
    )
    stop(
      "Unknown override feature ID(s): ",
      paste(unknown_ids, collapse = ", "),
      call. = FALSE
    )
  }

  decision_domains <- c(
    "statistics",
    "annotation"
  )

  for (domain in decision_domains) {
    override_column <- paste0(
      "operator_override_",
      domain
    )

    automatic_column <- paste0(
      "automatic_decision_",
      domain
    )

    effective_column <- paste0(
      "effective_decision_",
      domain
    )

    override_values <- as.character(
      overrides[[override_column]]
    )

    feature_data[[override_column]][
      feature_index
    ] <- override_values

    feature_data[[effective_column]][
      feature_index
    ] <- ifelse(
      override_values == "automatic",
      feature_data[[automatic_column]][
        feature_index
      ],
      override_values
    )

  }


  SummarizedExperiment::rowData(experiment) <- feature_data

  experiment
}
