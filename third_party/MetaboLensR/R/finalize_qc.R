#' Finalize a QC preview
#'
#' @param preview A curated SummarizedExperiment.
#' @param overrides A data frame containing frozen operator overrides.
#'
#' @return The experiment with operator overrides applied.
#' @export
finalize_qc <- function(
  preview,
  overrides
) {
  finalized <- apply_curation_overrides(
    preview,
    overrides
  )

  tic <- normalize_tic(finalized)

  pqn_result <- tryCatch(
    list(
      experiment = normalize_pqn(tic),
      status = "available",
      reason = NA_character_
    ),
    error = function(error) {
      list(
        experiment = tic,
        status = "unavailable",
        reason = conditionMessage(error)
      )
    }
  )

  bmis_result <- tryCatch(
    list(
      experiment = normalize_bmis(pqn_result$experiment),
      status = "available",
      reason = NA_character_
    ),
    error = function(error) {
      list(
        experiment = pqn_result$experiment,
        status = "unavailable",
        reason = conditionMessage(error)
      )
    }
  )
  normalization_summary <- data.frame(
    assay = c(
      "intensity",
      "tic",
      "pqn",
      "bmis"
    ),
    status = c(
      "available",
      "available",
      pqn_result$status,
      bmis_result$status
    ),
    reason = c(
      NA_character_,
      NA_character_,
      pqn_result$reason,
      bmis_result$reason
    ),
    stringsAsFactors = FALSE
  )

  result <- bmis_result$experiment

  normalization_summary$median_qc_cv_pct <-
    rep(
      NA_real_,
      nrow(normalization_summary)
    )

  normalization_summary$features_at_or_below_20_pct <-
    rep(
      NA_integer_,
      nrow(normalization_summary)
    )

  available_rows <- which(
    normalization_summary$status ==
      "available"
  )

  for (summary_row in available_rows) {
    assay_name <-
      normalization_summary$assay[
        summary_row
      ]

    metrics <- compute_feature_qc_metrics(
      result,
      assay_name = assay_name,
      raw_assay_name = "intensity"
    )

    feature_data <- SummarizedExperiment::rowData(
      result
    )

    feature_data[[
      paste0(
        "qc_cv_pct::",
        assay_name
      )
    ]] <- metrics$qc_cv_pct

    SummarizedExperiment::rowData(result) <- feature_data

    observed_qc_cv <- metrics$qc_cv_pct[
      is.finite(metrics$qc_cv_pct)
    ]

    if (length(observed_qc_cv) == 0L) {
      normalization_summary$
        features_at_or_below_20_pct[
          summary_row
        ] <- 0L

      next
    }

    normalization_summary$median_qc_cv_pct[
      summary_row
    ] <- stats::median(observed_qc_cv)

    normalization_summary$
      features_at_or_below_20_pct[
        summary_row
      ] <- sum(
        observed_qc_cv <= 20
      )
  }

  experiment_metadata <-
    S4Vectors::metadata(result)

  experiment_metadata$normalization_summary <-
    normalization_summary

  S4Vectors::metadata(result) <-
    experiment_metadata

  result

}