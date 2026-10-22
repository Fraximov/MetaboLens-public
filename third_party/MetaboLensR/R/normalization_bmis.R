#' Normalize an assay by chosing the internal standard
#' that best reduce the QC variance.
#'
#' @param experiment A validated SummarizedExperiment.
#' @param assay_name Source assay name.
#' @param output_assay_name Output assay name.
#'
#' @return The experiment with an additional BMIS-normalized assay.
#' @export
normalize_bmis <- function(
  experiment,
  assay_name = "intensity",
  output_assay_name = "bmis"
) {
  validate_experiment(experiment)

  intensity <- SummarizedExperiment::assay(
    experiment,
    assay_name
  )

  feature_data <- SummarizedExperiment::rowData(
    experiment
  )

  sample_data <- SummarizedExperiment::colData(
    experiment
  )

  selected_is_ids <- .select_bmis_IS(
    experiment,
    assay_name = assay_name
  )
  if (length(unique(sample_data$batch_id)) > 1) {
    stop("one batch at a time", call. = FALSE)
  }

  feature_data$bmis_internal_standard_id <-
    rep(
      NA_character_,
      nrow(feature_data)
    )
    
  feature_data$bmis_is_selected <-
    feature_data$feature_id %in% selected_is_ids
  feature_data$bmis_internal_standard_name <-
    rep(
      NA_character_,
      nrow(feature_data)
    )

  feature_data$bmis_applied <- FALSE

  sample_type <- tolower(
    trimws(sample_data$sample_type)
  )
  qc_selected <- !is.na(sample_type) &
    sample_type == "qc"

  qc_cv_cvt <- function(values) {
    values <- values[
      is.finite(values) &
        !is.na(values) &
        values > 0
    ]

    if (length(values) < 2L) {
      return(NA_real_)
    }

    stats::sd(values) /
      base::mean(values) * 100
  }
  raw_qc_cvt_threshold <- 10
  relative_improvement_threshold <- 0.40
  normalized <- intensity

  analyte_indices <- which(
    feature_data$feature_role == "analyte"
  )

  for (feature_index in analyte_indices) {
    analyte_values <- intensity[feature_index,]

    raw_qc_cvt <- qc_cv_cvt(
      analyte_values[qc_selected]
    )

    if (!is.finite(raw_qc_cvt) || raw_qc_cvt <= raw_qc_cvt_threshold) {
      next
    }

    candidates <- lapply(
      selected_is_ids,
      function(is_id) {
        is_values <- intensity[is_id, ]

        analyte_values * base::mean(is_values) /
          is_values
      }
    )

    candidate_cv_qc <- vapply(
      candidates,
      function(values) {
        qc_cv_cvt(values[qc_selected])
      },
      numeric(1)
    )

    candidate_cv_qc[
      !is.finite(candidate_cv_qc)
    ] <- Inf

    best_index <- which.min(candidate_cv_qc)

    selected_is_id <-
      selected_is_ids[best_index]

    selected_is_index <- match(
      selected_is_id,
      feature_data$feature_id
    )


    relative_improvement <-
      (raw_qc_cvt - candidate_cv_qc[best_index]) /
      raw_qc_cvt

    if (
      is.finite(relative_improvement) &&
        relative_improvement > relative_improvement_threshold
    ) {
      normalized[feature_index, ] <- candidates[[best_index]]
      feature_data$bmis_applied[feature_index] <- TRUE
      feature_data$bmis_internal_standard_id[feature_index] <-
        as.character(selected_is_id)
      feature_data$bmis_internal_standard_name[feature_index] <-
        as.character(feature_data$feature_name[selected_is_index])
    }

  }


  SummarizedExperiment::rowData(experiment) <- feature_data
  SummarizedExperiment::assay(
    experiment,
    output_assay_name
  ) <- normalized

  experiment_metadata <- S4Vectors::metadata(experiment)

  experiment_metadata$normalization[[output_assay_name]] <- list(
    method = "bmis",
    source_assay = assay_name,
    feature_scope = "analytes",
    minimum_raw_qc_cv_pct = raw_qc_cvt_threshold,
    minimum_relative_qc_cv_improvement = relative_improvement_threshold
  )


  S4Vectors::metadata(experiment) <- experiment_metadata

  experiment
}


.select_bmis_IS <- function(
  experiment,
  assay_name = "intensity"
) {
  validate_experiment(experiment)

  intensity <- SummarizedExperiment::assay(
    experiment, assay_name = assay_name
  )

  feature_data <- SummarizedExperiment::rowData(
    experiment
  )

  sample_data <- SummarizedExperiment::colData(
    experiment
  )

  sample_type <- tolower(
    trimws(
      as.character(sample_data$sample_type)
    )
  )

  qc_selected <-
    !is.na(sample_type) &
    sample_type == "qc"

  if (sum(qc_selected) <= 2L) {
    stop(
      "at least 3 QC samples.",
      call. = FALSE
    )
  }


  usable_as_denominator <- apply(
    intensity,
    1L,
    function(values) {
      all(
        is.finite(values) &
          values > 0
      )
    }
  )

  candidates_indices <- which(
    feature_data$feature_role ==
      "internal_standard" &
      feature_data$effective_decision_statistics ==
        "include" &
      feature_data$rt_matched %in% TRUE &
      feature_data$mz_matched %in% TRUE &
      usable_as_denominator
  )

  if (length(candidates_indices) == 0) {
    stop(
      "no eligible internal standards",
      call. = FALSE
    )
  }


  candidates_by_name <- split(
    candidates_indices,
    as.character(feature_data$feature_name[
      candidates_indices
    ]
    )
  )

  selected_indices <- vapply(
    candidates_by_name,
    function(indices) {
      qc_values <- apply(
        intensity[indices,qc_selected, drop = FALSE],
        1L,
        stats::median,
        na.rm = TRUE
      )

      indices[which.max(qc_values)]
    },
    integer(1)
  )

  unname(
    as.character(
      feature_data$feature_id[
        selected_indices
      ]
    )
  )
}
