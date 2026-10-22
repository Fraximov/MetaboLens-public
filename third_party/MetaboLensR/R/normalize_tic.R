#' Normalize an assay by total ion current
#'
#' @param experiment A validated SummarizedExperiment.
#' @param assay_name Source assay name.
#' @param output_assay_name Output assay name.
#'
#' @return The experiment with an additional TIC-normalized assay.
#' @export
normalize_tic <- function(
  experiment,
  assay_name = "intensity",
  output_assay_name = "tic"
) {
  validate_experiment(experiment)

  intensity <- SummarizedExperiment::assay(
    experiment,
    assay_name
  )

  factors <- colSums(
    intensity,
    na.rm = TRUE
  )

  invalid_factors <-
    !is.finite(factors) |
    factors <= 0

  if (any(invalid_factors)) {
    invalid_samples <-
      colnames(intensity)[invalid_factors]
    stop(
      paste0(
        "TIC normalization requires a positive finite total for sample(s): ",
        paste(invalid_samples, collapse = ", ")
      ),
      call. = FALSE
    )
  }

  normalized <- sweep(
    intensity,
    2L,
    factors,
    "/"
  )

  SummarizedExperiment::assay(
    experiment,
    output_assay_name
  ) <- normalized

  factor_column <- paste0(
    output_assay_name,
    "_normalization_factor"
  )


  sample_data <-
    SummarizedExperiment::colData(experiment)

  sample_data[[factor_column]] <- factors

  SummarizedExperiment::colData(experiment) <-
    sample_data

  experiment_metadata <- S4Vectors::metadata(experiment)

  experiment_metadata$normalization[[output_assay_name]
  ] <- list(
    method = "tic",
    source_assay = assay_name,
    feature_scope = "all_features",
    factor_column = factor_column
  )

  S4Vectors::metadata(experiment) <- experiment_metadata

  experiment
}