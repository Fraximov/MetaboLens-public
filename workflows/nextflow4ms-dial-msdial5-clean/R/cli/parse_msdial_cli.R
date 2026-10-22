#!/usr/bin/env Rscript

script_arg <- grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)[[1]]
workflow_root <- normalizePath(file.path(dirname(normalizePath(sub("^--file=", "", script_arg))), "..", ".."), mustWork = TRUE)
source(file.path(workflow_root, "R/functions/utils.R"))
source(file.path(workflow_root, "R/functions/parse_msdial.R"))
statpipeline_source_v2(workflow_root)

args <- statpipeline_parse_args()
config <- statpipeline_merge_config(
  statpipeline_load_config(statpipeline_arg(args, "config", default = NULL)),
  statpipeline_config_from_args(args)
)

run_parse_msdial(
  input = statpipeline_arg(args, "input", required = TRUE),
  outdir = statpipeline_arg(args, "outdir", required = TRUE),
  output_prefix = config$output_prefix,
  sample_metadata = statpipeline_arg(args, "sample-metadata", default = NULL),
  config = config
)
