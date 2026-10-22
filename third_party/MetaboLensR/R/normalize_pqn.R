#' Normalize an assay by calculating quotients against
#' a reference specrum (PQN)
#'
#' @param experiment A validated SummarizedExperiment.
#' @param assay_name Source assay name.
#' @param output_assay_name Output assay name.
#'
#' @return The experiment with an additional PQN-normalized assay.
#' @export
normalize_pqn <- function(
  experiment,
  assay_name = "intensity",
  output_assay_name = "pqn"
) {
  validate_experiment(experiment)

  intensity <- SummarizedExperiment::assay(experiment, assay_name)
  sample_data <- SummarizedExperiment::colData(experiment)

  sample_type <- tolower(
    trimws(
      as.character(sample_data$sample_type)
    )
  )

  qc_selected <-
    !is.na(sample_type) &
    sample_type == "qc"

  if (!any(qc_selected)) {
    stop(
      "PQN normalization requires at least one QC sample.",
      call. = FALSE
    )
  }

  qc_values <-
    intensity[,
      qc_selected,
      drop = FALSE
    ]

  qc_values[
    !is.finite(qc_values) |
      qc_values <= 0
  ] <- NA_real_

  references <- rowMeans(
    qc_values,
    na.rm = TRUE
  )

  quotients <- sweep(
    intensity,
    1L,
    references,
    "/"
  )


  quotients[
    !is.finite(quotients) |
      quotients <= 0
  ] <- NA_real_

  factors <- apply(
    quotients,
    2L,
    function(x) {
      observed <- x[!is.na(x)]

      if (length(observed) == 0L) {
        return(NA_real_)
      }
      stats::median(observed)
    }
  )

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

  sample_data[[factor_column]] <- factors

  SummarizedExperiment::colData(experiment) <- sample_data

  experiment

}