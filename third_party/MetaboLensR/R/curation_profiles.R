#' Built-in curation profiles
#'
#' @return A named list of curation profiles.
#' @export
curation_profiles <- function() {
  shared_rules <- list(
    list(
      rule_id = "minimum_sample_detection_pct",
      params = list(
        review_below = 80,
        exclude_below = 50
      )
    ),
    list(
      rule_id = "blank_to_sample_median_ratio",
      params = list(
        review_above = 0.20,
        exclude_above = 0.20
      )
    ),
    list(
      rule_id = "maximum_qc_cv_pct",
      params = list(
        review_above = 20,
        exclude_above = 30
      )
    ),
    list(
      rule_id = "has_ms2",
      params = list(on_false = "exclude")
    )
  )

  list(
    strict = list(
      name = "strict",
      statistics = c(
        list(
          list(
            rule_id = "has_annotation",
            params = list(
              on_false = "exclude"
            )
          )
        ),
        shared_rules
      ),
      annotation = shared_rules
    )
  )
}
