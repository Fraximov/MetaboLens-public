statpipeline_script_path <- function() {
  args <- commandArgs(trailingOnly = FALSE)
  file_arg <- grep("^--file=", args, value = TRUE)
  if (length(file_arg) == 0) {
    stop("Could not determine current Rscript path")
  }
  normalizePath(sub("^--file=", "", file_arg[[1]]), mustWork = TRUE)
}

statpipeline_workflow_root <- function(script_path = statpipeline_script_path()) {
  normalizePath(file.path(dirname(script_path), "..", ".."), mustWork = TRUE)
}

statpipeline_parse_args <- function(args = commandArgs(trailingOnly = TRUE)) {
  parsed <- list()
  i <- 1
  while (i <= length(args)) {
    key <- args[[i]]
    if (!startsWith(key, "--")) {
      stop("Unexpected argument: ", key)
    }
    name <- substring(key, 3)
    if (i == length(args) || startsWith(args[[i + 1]], "--")) {
      parsed[[name]] <- TRUE
      i <- i + 1
    } else {
      parsed[[name]] <- args[[i + 1]]
      i <- i + 2
    }
  }
  parsed
}

statpipeline_arg <- function(args, name, default = NULL, required = FALSE) {
  value <- args[[name]]
  if (is.null(value)) {
    if (required) stop("Missing required argument: --", name)
    return(default)
  }
  value
}

statpipeline_bool <- function(value, default = FALSE) {
  if (is.null(value)) return(default)
  if (is.logical(value)) return(value)
  normalized <- tolower(trimws(as.character(value)))
  if (normalized %in% c("true", "t", "1", "yes", "y")) return(TRUE)
  if (normalized %in% c("false", "f", "0", "no", "n")) return(FALSE)
  stop("Expected boolean value, got: ", value)
}

statpipeline_csv <- function(value, default = character(0)) {
  if (is.null(value) || identical(value, "")) return(default)
  trimws(strsplit(as.character(value), ",", fixed = TRUE)[[1]])
}

statpipeline_ensure_dir <- function(path) {
  if (!dir.exists(path)) dir.create(path, recursive = TRUE, showWarnings = FALSE)
  normalizePath(path, winslash = "/", mustWork = TRUE)
}

statpipeline_require_jsonlite <- function() {
  if (!requireNamespace("jsonlite", quietly = TRUE)) {
    stop("Missing R package: jsonlite")
  }
}

statpipeline_write_json <- function(x, path) {
  statpipeline_require_jsonlite()
  jsonlite::write_json(x, path, pretty = TRUE, auto_unbox = TRUE, null = "null", na = "null")
}

statpipeline_write_tsv <- function(x, path) {
  utils::write.table(x, path, sep = "\t", quote = FALSE, row.names = FALSE, na = "")
}

statpipeline_write_matrix <- function(x, path, sample_metadata = NULL) {
  table <- as.data.frame(x, stringsAsFactors = FALSE)
  table <- cbind(Sample = rownames(table), table)
  if (!is.null(sample_metadata) && nrow(sample_metadata) == nrow(table)) {
    sample_cols <- sample_metadata
    sample_cols$Sample <- NULL
    table <- cbind(table["Sample"], sample_cols, table[setdiff(names(table), "Sample")])
  }
  statpipeline_write_tsv(table, path)
}

statpipeline_source_v2 <- function(workflow_root = statpipeline_workflow_root()) {
  script <- file.path(workflow_root, "R", "legacy", "StatPipeline_v2.R")
  if (!file.exists(script)) {
    stop("StatPipeline_v2.R not found at: ", script)
  }
  # The modular wrappers reuse the facility helper functions but must not run
  # the monolithic top-level analysis block. The facility script checks this
  # option before executing its legacy main section.
  previous <- getOption("statpipeline.v2.source_only", FALSE)
  options(statpipeline.v2.source_only = TRUE)
  on.exit(options(statpipeline.v2.source_only = previous), add = TRUE)
  source(script, local = .GlobalEnv)
}

statpipeline_apply_config <- function(config = list()) {
  if (is.null(config)) config <- list()
  assign_if_present <- function(name, value) {
    if (!is.null(value)) assign(name, value, envir = .GlobalEnv)
  }

  assign_if_present("data_type", config$data_type)
  assign_if_present("remove_unknowns", config$remove_unknowns)
  assign_if_present("remove_blank_features", config$remove_blank_features)
  assign_if_present("min_blank_fold", config$min_blank_fold)
  assign_if_present("filter_by_cv", config$filter_by_cv)
  assign_if_present("max_cv_percent", config$max_cv_percent)
  assign_if_present("imputation_method", config$imputation_method)
  assign_if_present("impute_missing", config$impute_missing)
  assign_if_present("fallback_impute_missing", config$fallback_impute_missing)
  assign_if_present("random_seed", config$random_seed)
  assign_if_present("do_log_transform", config$log_transform)
  assign_if_present("test_method", config$test_method)
  assign_if_present("p_adjust_method", config$p_adjust_method)
  assign_if_present("paired_design", config$paired_design)
  assign_if_present("do_enrichment", config$run_lipid_module)
  if (!is.null(config$report_format) && config$report_format %in% c("pdf", "tiff")) {
    assign("fig_format", config$report_format, envir = .GlobalEnv)
  }
  invisible(config)
}

statpipeline_config_from_args <- function(args) {
  keep_unknowns <- statpipeline_bool(args[["keep-unknowns"]], default = FALSE)
  imputation_method <- statpipeline_arg(args, "imputation-method", default = "missForest")
  if (!imputation_method %in% c("missForest", "half_min", "none")) {
    stop("Unsupported imputation_method: ", imputation_method)
  }
  selected_normalization_method <- statpipeline_arg(args, "selected-normalization-method", default = NULL)
  if (!is.null(selected_normalization_method) && identical(trimws(selected_normalization_method), "")) {
    selected_normalization_method <- NULL
  }
  # This config object is the stable R-side contract. Nextflow passes values as
  # CLI flags; each module records the merged config in its JSON summary so the
  # scientific behavior is traceable after the run.
  list(
    analysis_mode = statpipeline_arg(args, "analysis-mode", default = "full"),
    data_type = statpipeline_arg(args, "data-type", default = NULL),
    remove_unknowns = !keep_unknowns,
    keep_unknowns = keep_unknowns,
    remove_blank_features = statpipeline_bool(args[["remove-blank-features"]], default = TRUE),
    min_blank_fold = as.numeric(statpipeline_arg(args, "blank-filter-ratio", default = 5)),
    filter_by_cv = statpipeline_bool(args[["filter-by-cv"]], default = TRUE),
    max_cv_percent = as.numeric(statpipeline_arg(args, "qc-cv-threshold", default = 30)),
    imputation_method = imputation_method,
    impute_missing = imputation_method == "missForest",
    fallback_impute_missing = imputation_method != "none",
    normalization_methods = statpipeline_csv(statpipeline_arg(args, "normalization-methods", default = "Raw,TIC,BMIS"), default = c("Raw", "TIC", "BMIS")),
    selected_normalization_method = selected_normalization_method,
    run_stats = statpipeline_bool(args[["run-stats"]], default = TRUE),
    contrasts = statpipeline_arg(args, "contrasts", default = NULL),
    test_method = statpipeline_arg(args, "test-method", default = NULL),
    p_adjust_method = statpipeline_arg(args, "p-adjust-method", default = NULL),
    paired_design = statpipeline_bool(args[["paired-design"]], default = FALSE),
    run_lipid_module = statpipeline_bool(args[["run-lipid-module"]], default = TRUE),
    report_format = statpipeline_arg(args, "report-format", default = NULL),
    output_prefix = statpipeline_arg(args, "output-prefix", default = "metabolens")
  )
}

statpipeline_load_config <- function(path) {
  if (is.null(path) || identical(path, "") || !file.exists(path)) return(list())
  statpipeline_require_jsonlite()
  jsonlite::read_json(path, simplifyVector = TRUE)
}

statpipeline_merge_config <- function(...) {
  configs <- list(...)
  out <- list()
  for (config in configs) {
    if (is.null(config)) next
    for (name in names(config)) {
      if (!is.null(config[[name]])) out[[name]] <- config[[name]]
    }
  }
  out
}

statpipeline_summary_base <- function(module, config = list()) {
  list(
    module = module,
    generated_at = format(Sys.time(), "%Y-%m-%dT%H:%M:%S%z"),
    parameters = config
  )
}
