#' Apply a curation profile
#' @param experiment A canonical Summarized Experiment
#' @param profile A curation profile stored as a list
#' @param assay_name Assay used for processing-depend rules
#' @param raw_assay_name original raw assay used for raw decision evidence 
#' 
#' @return A curated SummarizedEpxeriment containing all feature 
#' @export 
apply_curation_profile <- function(
  experiment,
  profile,
  assay_name = "intensity",
  raw_assay_name = "intensity"
) {
  validate_experiment(experiment)
  if (!is.list(profile$statistics)) {
    stop("profile$statistics must be a list", call. = FALSE)
  }

  if (!is.list(profile$annotation)) {
    stop("profile$annotation must be a list", call. = FALSE)
  }

  metrics <- compute_feature_qc_metrics(
    experiment,
    assay_name = assay_name,
    raw_assay_name = raw_assay_name
  )

  feature_data <- SummarizedExperiment::rowData(experiment)

  metric_names <- setdiff(
    names(metrics),
    "feature_id"
  )

  for (metric_name in metric_names) {
    feature_data[[metric_name]] <- metrics[[metric_name]]
  }

  evidence <- as.data.frame(feature_data)

  decision_domains <- c("statistics", "annotation")

  for (domain in decision_domains) {
    rule_results <- list()

    for (rule in profile[[domain]]) {
      rule_id <- rule$rule_id

      rule_function <-
        .curation_rule_registry[[rule_id]]

      if (is.null(rule_function)) {
        stop(
          sprintf(
            'Unknown curation rule "%s"',
            rule_id
          ),
          call. = FALSE
        )
      }
      params <- rule$params

      if (is.null(params)) {
        params <- list()
      }

      states <- rule_function(
        evidence,
        params
      )

      rule_column <- paste0(
        "curation_rule_",
        domain,
        "_",
        rule_id
      )

      feature_data[[rule_column]] <- states
      rule_results[[rule_id]] <- states
    }

    automatic_decision <-
      .aggregate_curation_states(
        rule_results,
        nrow(feature_data)
      )

    automatic_column <- paste0(
      "automatic_decision_",
      domain
    )

    override_column <- paste0(
      "operator_override_",
      domain
    )

    effective_column <- paste0(
      "effective_decision_",
      domain
    )

    feature_data[[automatic_column]] <-
      automatic_decision

    feature_data[[override_column]] <-
      rep("automatic", nrow(feature_data))

    feature_data[[effective_column]] <-
      automatic_decision
  }

  SummarizedExperiment::rowData(experiment) <- feature_data

  experiment_metadata <- S4Vectors::metadata(experiment)

  experiment_metadata$curation <- list(
    profile = profile,
    assay_name = assay_name,
    raw_assay_name = raw_assay_name
  )

  S4Vectors::metadata(experiment) <- experiment_metadata

  experiment
}
