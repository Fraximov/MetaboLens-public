#!/usr/bin/env Rscript

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1) stop("Usage: test_load_msdial_alignment_ids.R <workflow-root>")

workflow_root <- normalizePath(args[[1]], mustWork = TRUE)
source(file.path(workflow_root, "R/functions/utils.R"))
statpipeline_source_v2(workflow_root)
source(file.path(workflow_root, "R/functions/parse_msdial.R"))

fixture <- tempfile(fileext = ".txt")
on.exit(unlink(fixture), add = TRUE)
writeLines(c(
  "\t\t\tClass\tCase\tControl",
  "\t\t\tFile type\tSample\tSample",
  "\t\t\tInjection order\t1\t2",
  "\t\t\tBatch ID\t1\t1",
  "Alignment ID\tMetabolite name\tAverage Mz\t\tSample A\tSample B",
  "0\tFeature A\t100.1\t\t10\t20",
  "2\tFeature B\t200.2\t\t30\t40",
  "5\tIS: Standard\t300.3\t\t50\t60"
), fixture)

parsed <- load_msdial(fixture)

stopifnot(identical(colnames(parsed$intensity), c("5", "0", "2")))
stopifnot(identical(as.character(parsed$feature_info[["Alignment ID"]]), c("5", "0", "2")))
stopifnot(identical(as.numeric(parsed$intensity[1, c("5", "0", "2")]), c(50, 10, 30)))

parse_outdir <- tempfile()
on.exit(unlink(parse_outdir, recursive = TRUE), add = TRUE)
run_parse_msdial(fixture, parse_outdir)
matrix_export <- read.delim(
  file.path(parse_outdir, "raw_feature_matrix.tsv"),
  check.names = FALSE,
  stringsAsFactors = FALSE
)
stopifnot(identical(tail(colnames(matrix_export), 3), c("5", "0", "2")))

expect_alignment_id_error <- function(lines) {
  invalid_fixture <- tempfile(fileext = ".txt")
  on.exit(unlink(invalid_fixture), add = TRUE)
  writeLines(lines, invalid_fixture)
  error <- tryCatch({
    load_msdial(invalid_fixture)
    NULL
  }, error = function(condition) condition)
  stopifnot(inherits(error, "error"))
  stopifnot(grepl("Alignment ID", conditionMessage(error), fixed = TRUE))
}

fixture_lines <- readLines(fixture)
duplicate_id_lines <- fixture_lines
duplicate_id_lines[[8]] <- "2\tIS: Standard\t300.3\t\t50\t60"
expect_alignment_id_error(duplicate_id_lines)

empty_id_lines <- fixture_lines
empty_id_lines[[7]] <- "\tFeature B\t200.2\t\t30\t40"
expect_alignment_id_error(empty_id_lines)

message("Alignment IDs remain attached to their feature intensities")
