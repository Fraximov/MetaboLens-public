#' Import a native MassCube aligned peak-height table
#'
#' Supports MassCube 1.2.22 aligned TSV exports. Per-file detection tables and
#' other quantitation methods are rejected. Native annotation scores are kept
#' without interpreting them as MS-DIAL scores.
#'
#' @param path Path to the native aligned TSV file.
#' @param samplesheet Data frame with exact `source_column`, `acquisition_id`,
#'   `sample_name`, `sample_type` (Sample/QC/Blank), `group_label`, `batch_id`,
#'   and numeric `injection_order`. Additional acquisition metadata is retained.
#' @param source_metadata Named list with `version = "1.2.22"` and
#'   `quantitation_type = "height"`. Optional `acquisition_evidence_file` names
#'   a TSV relative to the aligned file (or absolute); see the package contract.
#'   Evidence requires `configuration$merge_features = FALSE`.
#' @return A validated [SummarizedExperiment::SummarizedExperiment]. Detection
#'   and gap-fill assays remain unknown unless explicit cell evidence is supplied.
#' @export
import_masscube <- function(path, samplesheet, source_metadata = list()) {
  if (!is.list(source_metadata) ||
      (length(source_metadata) && (is.null(names(source_metadata)) ||
       any(!nzchar(names(source_metadata))) || anyDuplicated(names(source_metadata))))) {
    stop("source_metadata must be a named list", call. = FALSE)
  }
  if (!identical(source_metadata$version, "1.2.22")) {
    stop("Supported MassCube version is 1.2.22; declare source_metadata$version", call. = FALSE)
  }
  if (!identical(source_metadata$quantitation_type, "height")) {
    stop("MassCube import currently supports verified peak height only", call. = FALSE)
  }
  unit <- source_metadata$retention_time_unit
  if (!is.null(unit) && !identical(unit, "minute")) {
    stop("MassCube 1.2.22 aligned RT must be in minute units", call. = FALSE)
  }
  method <- source_metadata$configuration$quant_method
  if (!is.null(method) && !identical(method, "peak_height")) {
    stop("MassCube quant_method must be peak_height", call. = FALSE)
  }
  tab <- .read_masscube_tsv(path)
  required <- c("feature_ID", "m/z", "RT", "alignment_reference_file",
                "detection_rate", "detection_rate_gap_filled")
  if (!all(required %in% names(tab))) {
    stop("Expected a MassCube aligned multi-file table, not per-file detections", call. = FALSE)
  }
  native <- c(required, "group_ID", "adduct", "isotope_state", "is_isotope",
              "is_in_source_fragment", "scan_scan_cor", "Gaussian_similarity",
              "noise_score", "asymmetry_factor", "peak_shape", "isotopes",
              "MS2_reference_file", "MS2_scan_id", "MS2", "precursor_ion_fraction",
              "matched_MS2", "search_mode", "annotation", "formula", "similarity",
              "matched_peak_number", "SMILES", "InChIKey", "database")
  source_columns <- setdiff(names(tab), native)
  samples <- .masscube_samples(samplesheet, source_columns)
  ids <- tab$feature_ID
  if (!length(ids) || anyNA(ids) || any(!nzchar(trimws(ids))) || anyDuplicated(ids)) {
    stop("MassCube feature IDs must be present and unique", call. = FALSE)
  }
  features <- tab[, intersect(names(tab), native), drop = FALSE]
  features$feature_id <- features$source_feature_id <- ids
  features$feature_name <- if ("annotation" %in% names(tab)) tab$annotation else rep(NA_character_, nrow(tab))
  # No automatic 'IS:' convention: that convention belongs to the MS-DIAL reader.
  features$feature_role <- rep("analyte", nrow(tab))
  features$mz <- .masscube_numeric(tab[["m/z"]], "m/z", missing = FALSE)
  features$rt_min <- .masscube_numeric(tab$RT, "RT", missing = FALSE)
  features$has_ms2 <- if ("MS2" %in% names(tab)) !is.na(tab$MS2) & nzchar(trimws(tab$MS2)) else rep(NA, nrow(tab))
  rownames(features) <- ids
  values <- vapply(tab[source_columns], .masscube_numeric, numeric(nrow(tab)), label = "intensity")
  values <- matrix(values, nrow = nrow(tab), dimnames = list(ids, samples$acquisition_id))
  unknown <- matrix(NA, nrow(values), ncol(values), dimnames = dimnames(values))
  assays <- list(intensity = values, detected = unknown, gap_filled = unknown)
  evidence_file <- source_metadata$acquisition_evidence_file
  if (!is.null(evidence_file)) {
    if (!is.character(evidence_file) || length(evidence_file) != 1L ||
        is.na(evidence_file) || !nzchar(evidence_file)) {
      stop("acquisition_evidence_file must be a single TSV path", call. = FALSE)
    }
    if (!identical(source_metadata$configuration$merge_features, FALSE)) {
      stop("Per-acquisition evidence requires configuration$merge_features = FALSE", call. = FALSE)
    }
    if (!grepl("^(/|[A-Za-z]:[/\\\\])", evidence_file)) evidence_file <- file.path(dirname(path), evidence_file)
    assays <- .masscube_evidence(assays, evidence_file)
    source_metadata$acquisition_evidence <- .input_record(evidence_file)
    source_metadata$acquisition_evidence_file <- NULL
  }
  source_metadata$tool <- "masscube"
  source_metadata$retention_time_unit <- "minute"
  experiment <- SummarizedExperiment::SummarizedExperiment(
    assays = assays, rowData = S4Vectors::DataFrame(features, check.names = FALSE),
    colData = S4Vectors::DataFrame(samples, check.names = FALSE),
    metadata = list(schema_version = "2.0", source = source_metadata))
  validate_experiment(experiment)
  experiment
}

.read_masscube_tsv <- function(path) {
  tab <- utils::read.delim(path, check.names = FALSE, colClasses = "character",
                          na.strings = "", quote = "\"",
                          comment.char = "", fileEncoding = "UTF-8")
  if (anyDuplicated(names(tab))) stop("MassCube TSV columns must be unique", call. = FALSE)
  tab
}

.masscube_numeric <- function(values, label, missing = TRUE) {
  # Missing-value markers belong to quantitative fields, never opaque IDs.
  values[values %in% c("NA", "None", "nan")] <- NA_character_
  numeric_values <- suppressWarnings(as.numeric(values))
  if (any(!is.na(values) & is.na(numeric_values)) || any(is.infinite(numeric_values)) ||
      (!missing && anyNA(numeric_values))) {
    stop(label, " must contain finite numeric values", if (missing) " or missing values", call. = FALSE)
  }
  numeric_values
}

.masscube_samples <- function(samplesheet, source_columns) {
  required <- c("source_column", "acquisition_id", "sample_name", "sample_type",
                "group_label", "batch_id", "injection_order")
  if (!is.data.frame(samplesheet) || !all(required %in% names(samplesheet)) ||
      anyDuplicated(names(samplesheet))) {
    stop("MassCube samplesheet must contain ", paste(required, collapse = ", "), call. = FALSE)
  }
  for (key in setdiff(required, "injection_order")) {
    samplesheet[[key]] <- as.character(samplesheet[[key]])
    if (anyNA(samplesheet[[key]]) || any(!nzchar(trimws(samplesheet[[key]])))) {
      stop("samplesheet ", key, " must be present", call. = FALSE)
    }
  }
  for (key in c("source_column", "acquisition_id")) {
    if (anyDuplicated(samplesheet[[key]])) stop("samplesheet ", key, " must be unique", call. = FALSE)
  }
  if (length(source_columns) < 2L || !setequal(samplesheet$source_column, source_columns)) {
    stop("samplesheet source_column must match every aligned acquisition column exactly", call. = FALSE)
  }
  samplesheet$injection_order <- .masscube_numeric(as.character(samplesheet$injection_order), "injection_order", missing = FALSE)
  types <- c(sample = "Sample", qc = "QC", blank = "Blank")
  samplesheet$sample_type <- unname(types[tolower(samplesheet$sample_type)])
  if (anyNA(samplesheet$sample_type)) stop("sample_type must be Sample, QC or Blank", call. = FALSE)
  samples <- samplesheet[match(source_columns, samplesheet$source_column), , drop = FALSE]
  rownames(samples) <- samples$acquisition_id
  samples
}

.masscube_evidence <- function(assays, path) {
  evidence <- .read_masscube_tsv(path)
  required <- c("source_feature_id", "acquisition_id", "native_feature_id", "detected", "gap_filled", "intensity")
  if (!all(required %in% names(evidence))) stop("Missing MassCube acquisition evidence columns", call. = FALSE)
  values <- assays$intensity
  position <- match(evidence$source_feature_id, rownames(values)) +
    (match(evidence$acquisition_id, colnames(values)) - 1L) * nrow(values)
  if (nrow(evidence) != length(values) || anyNA(position) || anyDuplicated(position)) {
    stop("Evidence must cover every feature/acquisition pair exactly once", call. = FALSE)
  }
  evidence <- evidence[order(position), , drop = FALSE]
  if (!identical(.masscube_numeric(evidence$intensity, "evidence intensity"), as.vector(values))) {
    stop("Evidence intensity differs from aligned intensity", call. = FALSE)
  }
  for (key in c("detected", "gap_filled")) {
    flags <- tolower(evidence[[key]])
    flags[flags %in% c("na", "none", "nan")] <- NA_character_
    if (any(!is.na(flags) & !flags %in% c("true", "false"))) stop(key, " flags must be true, false or missing", call. = FALSE)
    evidence[[key]] <- ifelse(is.na(flags), NA, flags == "true")
  }
  if (any(evidence$detected & evidence$gap_filled, na.rm = TRUE)) {
    stop("Evidence cannot be both detected and gap-filled", call. = FALSE)
  }
  native_id <- evidence$native_feature_id
  if (any(evidence$detected %in% TRUE & is.na(native_id)) ||
      any(!is.na(native_id) & !(evidence$detected %in% TRUE))) {
    stop("native_feature_id must identify detected cells only", call. = FALSE)
  }
  for (key in c("native_feature_id", "detected", "gap_filled")) {
    assays[[key]] <- matrix(evidence[[key]], nrow(values), dimnames = dimnames(values))
  }
  for (key in intersect(c("native_mz", "native_rt_min", "scan_idx"), names(evidence))) {
    assays[[key]] <- matrix(.masscube_numeric(evidence[[key]], key), nrow(values), dimnames = dimnames(values))
  }
  assays
}
