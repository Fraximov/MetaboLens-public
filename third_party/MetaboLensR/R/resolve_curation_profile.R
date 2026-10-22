#' Select existing rules from a built-in curation profile
#'
#' @param profile Name returned by [curation_profiles()].
#' @param enabled_rules Optional named list with `statistics` and/or `annotation`.
#'   Each entry is an unnamed character vector of rule IDs (or a list of strings
#'   read from JSON). Omit the argument or a domain to retain its full preset.
#'   Use `character()` or `list()` for a domain with no automatic filtering.
#'   Explicit `NULL`, duplicate IDs and rules outside the preset are errors.
#'
#' @return A profile list with the selected rules in preset order, preserving
#'   their parameters. Suitable for [apply_curation_profile()].
#' @export
resolve_curation_profile <- function(profile, enabled_rules) {
  profiles <- curation_profiles()
  if (!is.character(profile) || length(profile) != 1L ||
      is.na(profile) || !profile %in% names(profiles)) {
    stop("Profile must contain a supported profile", call. = FALSE)
  }
  resolved <- profiles[[profile]]
  if (missing(enabled_rules)) return(resolved)
  if (!is.list(enabled_rules) ||
      (length(enabled_rules) && (is.null(names(enabled_rules)) ||
       anyDuplicated(names(enabled_rules)) ||
       any(!names(enabled_rules) %in% c("statistics", "annotation"))))) {
    stop("enabled_rules must name only statistics and/or annotation once", call. = FALSE)
  }
  for (domain in names(enabled_rules)) {
    selection <- enabled_rules[[domain]]
    valid_strings <- is.character(selection) ||
      (is.list(selection) && all(vapply(selection, function(id) {
        is.character(id) && length(id) == 1L && !is.na(id)
      }, logical(1))))
    if (!valid_strings || !is.null(names(selection)) || anyNA(selection)) {
      stop(paste0("enabled_rules.", domain, " must be an array of rule IDs"), call. = FALSE)
    }
    ids <- unlist(selection, use.names = FALSE)
    available <- vapply(resolved[[domain]], `[[`, character(1), "rule_id")
    if (anyDuplicated(ids) || any(!ids %in% available)) {
      stop(paste0("enabled_rules.", domain,
                  " must contain unique rule IDs from the selected profile"), call. = FALSE)
    }
    resolved[[domain]] <- resolved[[domain]][available %in% ids]
  }
  resolved
}
