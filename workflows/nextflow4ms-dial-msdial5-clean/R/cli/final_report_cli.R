#!/usr/bin/env Rscript

script_arg <- grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)[[1]]
workflow_root <- normalizePath(file.path(dirname(normalizePath(sub("^--file=", "", script_arg))), "..", ".."), mustWork = TRUE)
source(file.path(workflow_root, "R/functions/utils.R"))
source(file.path(workflow_root, "R/functions/report_helpers.R"))

args <- statpipeline_parse_args()
config <- statpipeline_merge_config(
  statpipeline_load_config(statpipeline_arg(args, "config", default = NULL)),
  statpipeline_config_from_args(args)
)

run_final_report(
  outdir = statpipeline_arg(args, "outdir", required = TRUE),
  config = config,
  parse_dir = statpipeline_arg(args, "parse-dir", default = NULL),
  preprocess_dir = statpipeline_arg(args, "preprocess-dir", default = NULL),
  normalization_dir = statpipeline_arg(args, "normalization-dir", default = NULL),
  qc_dir = statpipeline_arg(args, "qc-dir", default = NULL),
  statistics_dir = statpipeline_arg(args, "statistics-dir", default = NULL),
  lipid_dir = statpipeline_arg(args, "lipid-dir", default = NULL)
)
