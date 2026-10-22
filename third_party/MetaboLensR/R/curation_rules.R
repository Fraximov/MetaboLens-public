.aggregate_curation_states <- function(
  rule_results,
  n_features
) {
  if (length(rule_results) == 0) {
    return(rep("include", n_features))
  }

  state_matrix <- do.call(
    cbind,
    rule_results
  )

  decision <- rep("include",n_features)

  decision[
    rowSums(state_matrix == "review") > 0L
  ] <- "review"

  decision[
    rowSums(state_matrix == "exclude") > 0L
  ] <- "exclude"

  decision

}


.require_numeric_rule_parameter <- function(
  params,
  parameter_name,
  rule_id
) {
  value <- params[[parameter_name]]

  if (
    !is.numeric(value) ||
      length(value) != 1L ||
      is.na(value) ||
      !is.finite(value)
  ) {
    stop(
      sprintf(
        '%s requires numeric parameter "%s"',
        rule_id,
        parameter_name
      ),
      call. = FALSE
    )
  }

  as.numeric(value)
}


.evaluate_minimum_sample_detection_pct <- function(
  evidence,
  params
) {
  rule_id <- "minimum_sample_detection_pct"

  review_below <- .require_numeric_rule_parameter(
    params,
    "review_below",
    rule_id
  )

  exclude_below <- .require_numeric_rule_parameter(
    params,
    "exclude_below",
    rule_id
  )

  if (exclude_below > review_below) {
    stop(
      "exclude_below must not exceed review_below",
      call. = FALSE
    )
  }

  values <- evidence$sample_detection_pct
  result <- rep("include", length(values))

  result[
    is.na(values) |
      values < review_below
  ] <- "review"

  result[
    !is.na(values) &
      values < exclude_below
  ] <- "exclude"

  result
}



.evaluate_maximum_qc_cv_pct <- function(
  evidence,
  params
) {
  rule_id <- "maximum_qc_cv_pct"

  review_above <- .require_numeric_rule_parameter(
    params,
    "review_above",
    rule_id
  )

  exclude_above <- .require_numeric_rule_parameter(
    params,
    "exclude_above",
    rule_id
  )

  if (exclude_above < review_above) {
    stop(
      "exclude_above must not be below review_above",
      call. = FALSE
    )
  }

  values <- evidence$qc_cv_pct
  result <- rep("include", length(values))

  result[
    is.na(values) |
      values > review_above
  ] <- "review"

  result[
    !is.na(values) &
      values > exclude_above
  ] <- "exclude"

  result
}

.evaluate_has_annotation <- function(
  evidence,
  params
) {
  rule_id <- "has_annotation"
  on_false <- params$on_false

  valid_states <- c(
    "include",
    "review",
    "exclude"
  )

  if (
    !is.character(on_false) ||
      length(on_false) != 1L ||
      is.na(on_false) ||
      !on_false %in% valid_states
  ) {
    stop(
      sprintf(
        '%s parameter "on_false" invalid',
        rule_id
      ),
      call. = FALSE
    )
  }


  values <- trimws(
    as.character(evidence$feature_name)
  )

  is_annotated <-
    !is.na(values) &
    nzchar(values) &
    tolower(values) != "unknown"

  result <- rep(
    on_false,
    length(values)
  )

  result[is_annotated] <- "include"

  result

}

.evaluate_minimum_group_detection_pct <- function(
  evidence,
  params
) {
  rule_id <- "minimum_group_detection_pct"

  review_below<- .require_numeric_rule_parameter(
    params,
    "review_below",
    rule_id
  )

  exclude_below <- .require_numeric_rule_parameter(
    params,
    "exclude_below",
    rule_id
  )

  if (exclude_below > review_below) {
    stop(
      "exclude_below must not be above review_below",
      call. = FALSE
    )
  }

  values <- evidence$best_group_detection_pct
  result <- rep("include", length(values))

  result[
    is.na(values) |
      values < review_below
  ] <- "review"

  result[
    !is.na(values) &
      values < exclude_below
  ] <- "exclude"

  result
}



.evaluate_has_ms2 <- function(
  evidence,
  params
) {
  rule_id <- "has_ms2"
  on_false <- params$on_false


  valid_states <- c(
    "include",
    "exclude",
    "review"
  )

  if (
    !is.character(on_false) ||
      length(on_false) != 1L ||
      is.na(on_false) ||
      !on_false %in% valid_states
  ) {
    stop(
      paste0(
        rule_id,
        'parameter "on_false" invalid'
      ),
      call. = FALSE
    )
  }

  values <- evidence$has_ms2
  result <- rep("include", length(values))

  result[is.na(values)] <- "review"

  result[
    !is.na(values) &
      !values
  ] <- on_false

  result
}

.evaluate_maximum_blank_to_sample_median_ratio <- function(
  evidence,
  params
) {
  rule_id <- "blank_to_sample_median_ratio"

  review_above <- .require_numeric_rule_parameter(
    params,
    "review_above",
    rule_id
  )

  exclude_above<- .require_numeric_rule_parameter(
    params,
    "exclude_above",
    rule_id
  )

  if (exclude_above < review_above) {
    stop(
      "exclude_above must not be lowered than review_below",
      call. = FALSE
    )
  }

  values <- evidence$blank_to_sample_median_ratio
  result <- rep("include", length(values))

  result[
    is.na(values) |
      values > review_above
  ] <- "review"

  result[
    !is.na(values) &
      values > exclude_above
  ] <- "exclude"

  result
}





.curation_rule_registry <- list(
  minimum_sample_detection_pct =
    .evaluate_minimum_sample_detection_pct,
  maximum_qc_cv_pct =
    .evaluate_maximum_qc_cv_pct,
  has_ms2 =
    .evaluate_has_ms2,
  has_annotation =
    .evaluate_has_annotation,
  blank_to_sample_median_ratio =
    .evaluate_maximum_blank_to_sample_median_ratio,
  minimum_group_detection_pct =
    .evaluate_minimum_group_detection_pct

)
