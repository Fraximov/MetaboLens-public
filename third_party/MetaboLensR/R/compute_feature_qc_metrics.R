#' Compute per-feature QC metrics
#'
#' Treats zero and NA intensities as missing while preserving the original
#' SummarizedExperiment.
#' best_group_detection_pct currently means “percentage with a positive
#' post-gap-filling intensity,” not genuine MS-DIAL peak detection.
#'
#' @param experiment A canonical, validated SummarizedExperiment.
#' @param assay_name Name of the assay used for processing-dependent metrics,
#'   such as QC CV.
#' @param raw_assay_name Name of the raw assay used for detection, missingness,
#'   and blank metrics.
#'
#' @return A data frame containing one row per feature.
#' @export
compute_feature_qc_metrics <- function(
  experiment,
  assay_name = "intensity",
  raw_assay_name = "intensity") {
  validate_experiment(experiment)

  assay_names <- SummarizedExperiment::assayNames(experiment)

  if (!assay_name %in% assay_names) {
    stop(
      sprintf('assay_name "%s" was not found', assay_name),
      call. = FALSE
    )
  }

  if (!raw_assay_name %in% assay_names) {
    stop(
      sprintf('raw_assay_name "%s" was not found', raw_assay_name),
      call. = FALSE
    )
  }

  intensity <- SummarizedExperiment::assay(
    experiment,
    raw_assay_name
  )
  selected_intensity <- SummarizedExperiment::assay(
    experiment,
    assay_name
  )

  feature_id <- as.character(
    SummarizedExperiment::rowData(experiment)$feature_id
  )

  sample_type <- tolower(trimws(as.character(
    SummarizedExperiment::colData(experiment)$sample_type
  )))

  group_label <- trimws(as.character(
    SummarizedExperiment::colData(experiment)$group_label
  ))

  missing <- is.na(intensity) | intensity == 0

  biological_sample <-
    !is.na(sample_type) &
    sample_type == "sample" &
    !is.na(group_label) &
    nzchar(group_label)

  biological_groups <- unique(
    group_label[biological_sample]
  )

  group_detection <- lapply(
    biological_groups,
    function(group_pct) {
      selected <-
        biological_sample &
        group_label == group_pct

      rowMeans(
        !missing[, selected, drop = FALSE]
      ) * 100
    }
  )




  if (length(biological_groups) > 0L) {
    names(group_detection) <- paste0(
      "group_detection_pct::",
      biological_groups
    )

    group_detection_matrix <- do.call(
      cbind,
      group_detection
    )

    best_group_index <- max.col(
      group_detection_matrix,
      ties.method = "first"
    )

    best_group_detection_pct <-
      group_detection_matrix[
        cbind(
          seq_len(nrow(group_detection_matrix)),
          best_group_index
        )
      ]

    best_detected_group <-
      biological_groups[best_group_index]
  } else {
    best_group_detection_pct <-
      rep(NA_real_, nrow(intensity))

    best_detected_group <-
      rep(NA_character_, nrow(intensity))
  }


  detected_intensity <- intensity
  detected_intensity[missing] <- NA_real_

  detection_pct_for <- function(type) {
    selected <- !is.na(sample_type) &
      sample_type == tolower(type)

    if (!any(selected)) {
      return(rep(NA_real_, nrow(intensity)))
    }

    rowMeans(
      !missing[, selected, drop = FALSE]
    ) * 100
  }

  summarize_intensity_for <- function(type, summary_function) {
    selected <- !is.na(sample_type) &
      sample_type == tolower(type)

    if (!any(selected)) {
      return(rep(NA_real_, nrow(detected_intensity)))
    }

    values <- detected_intensity[
      ,
      selected,
      drop = FALSE
    ]

    apply(values, 1L, function(x) {
      if (all(is.na(x))) {
        return(NA_real_)
      }

      summary_function(x, na.rm = TRUE)
    })
  }

  qc_selected <- !is.na(sample_type) & sample_type == "qc"

  if (any(qc_selected)) {
    raw_qc_values <- detected_intensity[, qc_selected, drop = FALSE]
    qc_detected_count <- rowSums(!is.na(raw_qc_values))

    qc_values <- selected_intensity[, qc_selected, drop = FALSE]
    qc_values[is.na(raw_qc_values)] <- NA_real_

    qc_cv_pct <- apply(qc_values, 1L, function(x) {
      x <- x[!is.na(x)]

      if (length(x) < 2L) {
        return(NA_real_)
      }

      stats::sd(x) / base::mean(x) * 100
    })
  } else {
    qc_detected_count <- rep(0, nrow(intensity))
    qc_cv_pct <- rep(NA_real_, nrow(intensity))
  }


  metrics <- data.frame(
    feature_id = feature_id,
    n_total = rep(ncol(intensity), nrow(intensity)),
    zero_count = rowSums(
      !is.na(intensity) & intensity == 0
    ),
    na_count = rowSums((is.na(intensity))),
    missing_count = rowSums(missing),
    detected_count = rowSums(!missing),
    detection_pct = rowMeans(!missing) * 100,
    sample_detection_pct = detection_pct_for("Sample"),
    qc_detection_pct = detection_pct_for("QC"),
    blank_detection_pct = detection_pct_for("Blank"),
    sample_mean_intensity = summarize_intensity_for("Sample", base::mean),
    sample_median_intensity = summarize_intensity_for("Sample", stats::median),
    qc_mean_intensity = summarize_intensity_for("QC", base::mean),
    qc_median_intensity = summarize_intensity_for("QC", stats::median),
    blank_mean_intensity = summarize_intensity_for("Blank", base::mean),
    blank_median_intensity = summarize_intensity_for("Blank", stats::median),
    qc_detected_count = qc_detected_count,
    qc_cv_pct = qc_cv_pct,
    row.names = NULL,
    check.names = FALSE
  )

  for (metric_name in names(group_detection)) {
    metrics[[metric_name]] <-
      group_detection[[metric_name]]
  }

  metrics$best_group_detection_pct <-
    best_group_detection_pct

  metrics$best_detected_group <-
    best_detected_group

  metrics$blank_to_sample_mean_ratio <-
    metrics$blank_mean_intensity /
    metrics$sample_mean_intensity

  metrics$blank_to_sample_median_ratio <-
    metrics$blank_median_intensity /
    metrics$sample_median_intensity

  metrics$blank_to_qc_median_ratio <-
    metrics$blank_median_intensity /
    metrics$qc_median_intensity

  blank_undetected <-
    !is.na(metrics$blank_detection_pct) &
    metrics$blank_detection_pct == 0 &
    !is.na(metrics$sample_detection_pct) &
    metrics$sample_detection_pct > 0

  metrics$blank_to_sample_mean_ratio[
    blank_undetected
  ] <- 0

  metrics$blank_to_sample_median_ratio[
    blank_undetected
  ] <- 0

  metrics$blank_to_sample_mean_ratio[
    blank_undetected
  ] <- 0

  metrics

}
