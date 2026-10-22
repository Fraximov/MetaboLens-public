#' Read a MetaboLens adapted MS-DIAL file
#'
#' Parses an adapted MS-DIAL file without canonicalizing or validating its
#' feature metadata.
#'
#' @param path Path to the MS-DIAL adapted file
#' @return A raw SummarizedExperiment with features in rows and samples in
#'   columns.
#' @export
read_adapted_msdial <- function(path) {
  if (!file.exists(path)) {
    stop("File does not exist: ", path, call = FALSE)
  }

  tab <- utils::read.delim(
    path,
    header = FALSE,
    sep = "\t",
    check.names = FALSE,
    stringsAsFactors = FALSE,
    na.strings = c("", "NA"),
    fill = TRUE,
    comment.char = ""
  )

  class_col <- which(trimws(as.character(tab[1, ])) == "Class")

  if (length(class_col) == 0) {
    stop("No 'Class' column found in the MS-DIAL adapted file.", call. = FALSE)
  }

  header_row <- which(trimws(as.character(tab[[1L]])) == "Alignment ID")

  if (length(header_row) != 1L) {
    stop("No header row found in the MS-DIAL adapted file.", call. = FALSE)
  }

  if (length(header_row) > 1L) {
    stop("
    Multiple header rows found in the MS-DIAL adapted file.", call. = FALSE)
  }

  if (header_row >= nrow(tab)) {
    stop("
    Header row is the last row in the MS-DIAL adapted file.", call. = FALSE)
  }

  metadata_cols <- seq_len(class_col - 1L)
  sample_cols <- seq.int(class_col + 1L, ncol(tab))
  feature_rows <- seq.int(header_row + 1L, nrow(tab))

  feature_headers <- trimws(as.character(tab[header_row, metadata_cols]))
  sample_names <- trimws(as.character(tab[header_row, sample_cols]))

  if (
    any(is.na(sample_names) | !nzchar(sample_names)) ||
      any(duplicated(sample_names)) > 0L
  ) {
    stop("
  Sample names are missing or duplicated", call. = FALSE)
  }

  row_data <- tab[feature_rows, metadata_cols, drop = FALSE]
  names(row_data) <- feature_headers

  if (!"Alignment ID" %in% names(row_data)) {
    stop("
    No 'Alignment ID' column found in the MS-DIAL adapted file.", call. = FALSE)
  }

  feature_ids <- trimws(as.character(row_data[["Alignment ID"]]))

  if (
    any(is.na(feature_ids) | !nzchar(feature_ids)) ||
      any(duplicated(feature_ids)) > 0L
  ) {
    stop("Feature IDs are missing or duplicated", call. = FALSE)
  }

  raw_intensity <- as.matrix(tab[feature_rows, sample_cols, drop = FALSE])

  intensity <- suppressWarnings(
    matrix(
      as.numeric(raw_intensity),
      ncol = ncol(raw_intensity),
      nrow = nrow(raw_intensity)
    )
  )

  if (any(is.na(intensity) & !is.na(raw_intensity))) {
    stop("
    Some intensity values could not be converted to numeric and are set to NA.
    ", call. = FALSE)
  }

  dimnames(intensity) <- list(feature_ids, sample_names)
  rownames(row_data) <- feature_ids

  metadata_label <- trimws(as.character(tab[[class_col]]))

  read_metadata_row <- function(label) {
    row <- which(metadata_label == label)
    if (length(row) == 0L) {
      stop("No row found for metadata label: ", label, call. = FALSE)
    }
    trimws(as.character(tab[row, sample_cols]))
  }

  injection_order <- suppressWarnings(
    as.integer(read_metadata_row("Injection order"))
  )
  if (anyNA(injection_order)) {
    stop("Injection order contains non-integer values.", call. = FALSE)
  }

  col_data <- data.frame(
    sample_name = sample_names,
    group_label = read_metadata_row("Class"),
    sample_type = read_metadata_row("File type"),
    injection_order = injection_order,
    batch_id = read_metadata_row("Batch ID"),
    row.names = sample_names,
    check.names = FALSE,
    stringsAsFactors = FALSE
  )

  experiment <- SummarizedExperiment::SummarizedExperiment(
    assays = list(intensity = intensity),
    rowData = row_data,
    colData = col_data,
    metadata = list(
      schema_version = "1.0",
      source_file = normalizePath(path, winslash = "/")
    )
  )

  experiment

}
